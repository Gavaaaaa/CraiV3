"""
crai/api/app.py
FastAPI — unifica os pipelines da CRAI:
  /webhooks/pix-automatico → churn involuntário via Pix (assinatura obrigatória)
  /webhooks/stripe         → churn involuntário via cartão (assinatura obrigatória)
  /webhooks/segment        → churn voluntário  (assinatura obrigatória)
  /simulate/*              → endpoints de teste, restritos a ENV=development|demo

Os dois webhooks de churn involuntário alimentam o MESMO pipeline
(_run_involuntary_pipeline), mas cada um marca `payment_method` na entrada do
state. É esse campo que o grafo lê para escolher a política de retentativa
correta — ver crai/agent/main_agent.py.
"""

import os
import json
import math
import asyncio
import hashlib
import logging
import weakref
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..agent.main_agent import crai_agent
from ..agent.state import AgentState, PaymentMethod
from ..churn_voluntary.voluntary_agent import voluntary_churn_agent
from ..churn_voluntary.state import ChurnVoluntaryState
from ..integrations.payment_gateway import (
    DEGRADACOES_BLOQUEANTES,
    MOTIVO_SEM_IDENTIFICACAO,
    MOTIVO_VALOR_NAO_UTILIZAVEL,
    STATUS_COBRANCA_CONFIRMADA,
    STATUS_COBRANCA_FALHADA,
    STATUS_AUTORIZACAO_CONCEDIDA,
    STATUS_AUTORIZACAO_REVOGADA,
    PayloadPixInvalido,
    PixAutomaticoAdapter,
    _para_float,
)
from ..security.webhook_verification import (
    verify_stripe_signature,
    verify_segment_signature,
    verify_pix_automatico_signature,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="CRAI", version="2.0.0",
              description="Agente autônomo de recuperação de receita — churn involuntário + voluntário")


def _json_representavel(valor):
    """Troca por texto todo float que o JSON padrão não sabe escrever.

    `NaN`, `Infinity` e `-Infinity` são extensões do JSON do Python: existem no
    `json.loads` por default, mas não na especificação. O Starlette escreve as
    respostas com `allow_nan=False` — o certo — e por isso qualquer um deles
    dentro do corpo de uma resposta levanta `ValueError` na hora de serializar,
    já fora do `try` da rota. O cliente recebe 500.
    """
    if isinstance(valor, float) and not math.isfinite(valor):
        return repr(valor)
    if isinstance(valor, dict):
        return {chave: _json_representavel(v) for chave, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_json_representavel(v) for v in valor]
    return valor


@app.exception_handler(RequestValidationError)
async def _erro_de_validacao_nunca_vira_500(request: Request, exc: RequestValidationError):
    """422 com o motivo, em vez de 500 sem motivo, quando o Pydantic recusa.

    O projeto já fechava esta porta em dois pontos: o webhook de Pix recusa os
    literais no `json.loads` (`parse_constant`), e os `/simulate/*` de valor
    passam pelos portões `_valor_de_simulacao` / `_contador_de_simulacao`.
    Faltava o caso em que **o Pydantic recusa antes da rota existir**: um
    `NaN` chegando num campo declarado `int` (`days_since_last`) nunca alcança
    o corpo da função, e o handler padrão do FastAPI devolve o valor ofensor
    ECOADO dentro do erro. Aí o `NaN` está no corpo da RESPOSTA, e é a
    serialização dela que quebra — 500 num payload que o serviço tinha acabado
    de recusar corretamente.

    Medido antes da correção: `days_since_last: NaN | Infinity | -Infinity` em
    `/simulate/churn-risk` devolvia HTTP 500 "Internal Server Error" nos três.

    Sanear o eco resolve a classe inteira, não só estes três campos: vale para
    qualquer rota, atual ou futura, sem que cada uma precise lembrar do caso.
    """
    try:
        detalhe = _json_representavel(jsonable_encoder(exc.errors()))
    except RecursionError:
        # O eco carrega a ESTRUTURA recusada, não só o nome do campo. Um valor
        # profundamente aninhado num campo que o Pydantic rejeita faz o próprio
        # `jsonable_encoder` estourar a pilha — dentro do handler que existe
        # para impedir 500. Medido: ~2000 níveis em `days_since_last`, `valor`
        # ou `amount` devolviam 500 nos três `/simulate/*`.
        logger.warning("[VALIDACAO] Corpo recusado é aninhado demais para ser "
                       "ecoado — 422 sem o valor ofensor")
        detalhe = [{
            "type": "payload_aninhado_demais",
            "msg": ("o corpo foi recusado e é aninhado demais para ser "
                    "devolvido no erro"),
        }]

    return JSONResponse(status_code=422, content={"detail": detalhe})

# Ambientes onde os endpoints /simulate/* ficam expostos. O default é
# "production" (fail closed): esquecer de definir ENV nunca deve deixar um
# endpoint que dispara pipeline aberto sem autenticação.
SIMULATION_ENVS = {"development", "demo"}

# Header de assinatura do webhook de Pix Automático (mesmo formato do Stripe:
# 't=<timestamp>,v1=<hmac>'). O nome varia por PSP; ajustar aqui se necessário.
PIX_SIGNATURE_HEADER = "x-pix-signature"

# Os 4 eventos de Pix Automático cobertos. Só o último é falha de pagamento e
# aciona a recuperação; a revogação da autorização é sinal de churn voluntário
# e fica registrada para o pipeline voluntário consumir no futuro.
PIX_EVENTO_LABEL = {
    STATUS_AUTORIZACAO_CONCEDIDA: "autorização de recorrência concedida",
    STATUS_AUTORIZACAO_REVOGADA:  "autorização revogada pelo pagador",
    STATUS_COBRANCA_CONFIRMADA:   "cobrança recorrente confirmada",
    STATUS_COBRANCA_FALHADA:      "cobrança recorrente falhada",
}

_pix_adapter = PixAutomaticoAdapter()


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


def _reject_unsigned(request: Request, origem: str) -> HTTPException:
    """401 padrão para webhook sem assinatura válida, registrando a origem."""
    logger.warning(f"[SECURITY] Webhook {origem} rejeitado (401) — IP {_client_ip(request)}")
    return HTTPException(status_code=401, detail="Assinatura de webhook inválida")


def _rejeitar_constante_json(nome: str):
    """Recusa `Infinity` / `-Infinity` / `NaN` no corpo do webhook.

    O `json` do Python aceita esses três literais por extensão; o JSON padrão
    (RFC 8259) não os tem. Nenhum PSP legítimo manda um valor infinito, e
    deixá-los entrar é uma porta aberta para derrubar a API com 500.
    """
    raise ValueError(f"constante JSON não numérica não é aceita: {nome}")


def _thread_id(evento: dict) -> Optional[str]:
    """Identidade do checkpoint do LangGraph para um evento de Pix.

    O `thread_id` do `MemorySaver` é o que separa a memória de um cliente da do
    outro (ver `crai/agent/main_agent.py`). Usar o literal `"rec_desconhecida"`
    quando `id_recorrencia` vinha vazio fazia **todos** os pagadores anônimos
    caírem no mesmo checkpoint: o segundo evento retomava o estado do primeiro
    (P0-6).

    Returns:
        O id da recorrência, se houver; senão um id anônimo derivado do próprio
        evento; **`None`** quando o evento não carrega nada que identifique o
        pagador — e aí a borda recusa com 422.

    Por que `None` em vez de um id derivado só de ISPB e valor: num SaaS de
    preço único, cujo PSP não mande `e2e_id`, a base seria a mesma string para
    **todos os clientes**, e o P0-6 voltaria inteiro para essa fatia da base.
    Recusar é a única resposta honesta — a CRAI não pode isolar o estado de um
    cliente que o payload não identifica.

    A base é serializada como lista JSON, e não concatenada com `|`. Com o
    separador cru, `e2e="E123|999" + ispb="60701190"` produzia exatamente a
    mesma string que `e2e="E123" + ispb="999|60701190"` — dois clientes
    distintos colidindo no mesmo checkpoint pelo caminho que a correção do
    P0-6 existia para fechar.

    `sha256`, e não o `hash()` embutido: para strings, o hash do Python é
    randomizado por processo (PYTHONHASHSEED), o que daria um thread_id
    diferente a cada execução e quebraria o determinismo da demo.
    """
    rec = str(evento.get("id_recorrencia") or "").strip()
    if rec:
        return rec

    e2e = str(evento.get("e2e_id") or "").strip()
    if not e2e:
        logger.warning(
            "[PIX] Evento sem id de recorrência E sem e2e_id: não há como "
            "identificar o pagador. Recusando — derivar o checkpoint de ISPB + "
            "valor faria todos os clientes de mesmo preço dividirem estado.",
        )
        return None

    base = json.dumps(
        [e2e, str(evento.get("ispb_pagador") or ""), repr(evento.get("valor", 0))],
        ensure_ascii=False,
    )
    anonimo = "rec_anon_" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    logger.warning(
        "[PIX] Evento sem id de recorrência — checkpoint anônimo %s derivado do "
        "próprio evento (e2e/ISPB/valor).", anonimo,
    )
    return anonimo


def _valor_de_simulacao(bruto, campo: str = "valor") -> float:
    """Portão de valor dos endpoints `/simulate/*`, igual ao do webhook.

    Os endpoints de simulação sintetizam o evento em vez de recebê-lo do PSP,
    então não têm a lista `degradacoes` do parser — mas recebem número de fora
    igual ao webhook, e o Pydantic aceita `nan`, `inf` e `1e300` num campo
    `float`. Cada endpoint que fazia a própria checagem (ou nenhuma) reabriu o
    P0-5 por uma porta diferente. Este é o único lugar onde a regra mora.

    **Arredonda antes de validar**, na mesma ordem do `_extrair_valor` do
    gateway. A ordem inversa deixava passar `0 < valor < 0.005`: o valor era
    positivo na checagem, virava `0.0` no arredondamento, e a demo criava
    negócio de **R$ 0,00** no CRM enquanto o webhook recusava o mesmo número
    com 422.

    Raises:
        HTTPException: 422 quando o valor não serve como cobrança.
    """
    valor = _para_float(bruto)
    if valor is not None:
        valor = round(valor, 2)
    if valor is None or valor <= 0:
        logger.warning("[SIM] Campo %r recusado — %r não é uma cobrança "
                       "utilizável.", campo, bruto)
        raise HTTPException(status_code=422, detail={
            "motivo": MOTIVO_VALOR_NAO_UTILIZAVEL,
            "campo": campo,
            "detalhe": f"valor recebido: {bruto!r}",
        })
    return valor


def _contador_de_simulacao(bruto, campo: str, maximo: int = 100_000) -> int:
    """Portão dos campos de CONTAGEM dos `/simulate/*` (dias, usos, tentativas).

    Mesmo problema do valor, outro tipo: `days_since_last` é `int`, e um
    inteiro JSON de 401 dígitos atravessa o Pydantic intacto e estoura mais
    adiante, com o negócio já criado no CRM. Contadores de produto vivem em
    dezenas ou centenas; o teto é generoso de propósito e só existe para barrar
    o absurdo.
    """
    numero = _para_float(bruto)
    if numero is None or numero < 0 or numero > maximo:
        logger.warning("[SIM] Contador %r recusado — %r fora de [0, %d].",
                       campo, bruto, maximo)
        raise HTTPException(status_code=422, detail={
            "motivo": MOTIVO_VALOR_NAO_UTILIZAVEL,
            "campo": campo,
            "detalhe": f"valor recebido: {bruto!r}",
        })
    return int(numero)


def _require_simulation_env() -> None:
    """Endpoints /simulate/* só existem fora de produção."""
    env = os.getenv("ENV", "production").strip().lower()
    if env not in SIMULATION_ENVS:
        logger.warning(f"[SECURITY] /simulate/* bloqueado — ENV={env or 'não definido'}")
        raise HTTPException(
            status_code=403,
            detail="Endpoints /simulate/* exigem ENV=development ou ENV=demo",
        )


# ── Churn Involuntário — Pix Automático ──────────────────────────────────

@app.post("/webhooks/pix-automatico")
async def pix_automatico_webhook(request: Request) -> JSONResponse:
    """Recebe os eventos de Pix Automático do PSP.

    Só a cobrança recorrente FALHADA aciona o pipeline de recuperação. Os
    outros três eventos são registrados: autorização concedida/revogada e
    cobrança confirmada não são falha de pagamento.

    Formas de recusar, todas com log:
      400  corpo que não é JSON válido
      400  corpo que é JSON mas **não é um objeto** (lista, número, string,
           `null`) — a recusa é do envelope, antes de haver evento
      400  corpo aninhado além do que o parser suporta
      422  corpo que É um objeto e o adapter não sabe normalizar (lote de
           eventos, `data` com conteúdo não-objeto) — motivo
           `payload_nao_e_objeto` ou `lote_nao_suportado`
      422  cobrança falhada cujo VALOR não pôde ser lido
      422  evento sem `id_recorrencia` e sem `e2e_id` (P0-6)

    **Mudança de contrato declarada na A1-r9:** até `deb23be`, um corpo JSON
    não-objeto (`[]`, `5`, `"texto"`) devolvia **422** com motivo
    `payload_nao_e_objeto`, porque a rota tinha o próprio `json.loads` e
    entregava qualquer coisa ao adapter. Ao adotar o portão comum
    `_objeto_json_do_corpo` — que é o que fechou o 500 por aninhamento —
    esses casos passaram a **400**, alinhados com os webhooks de Stripe e
    Segment. É a resposta certa: um corpo que não é objeto é envelope
    malformado, não entidade semanticamente inválida. O 422 do adapter
    continua valendo para o corpo que É objeto e ainda assim não normaliza.
    """
    raw = await request.body()
    if not verify_pix_automatico_signature(
        raw,
        request.headers.get(PIX_SIGNATURE_HEADER, ""),
        os.getenv("PIX_WEBHOOK_SECRET", ""),
    ):
        raise _reject_unsigned(request, "pix_automatico")

    # Mesmo portão dos outros dois webhooks. Este era o `json.loads` próprio
    # desta rota, com o mesmo `parse_constant` mas SEM o `except RecursionError`
    # que a rodada 7 acrescentou ao portão comum — então a correção de lá não
    # alcançava a única rota do pipeline ativo, e um corpo assinado com ~5000
    # níveis de aninhamento devolvia 500 aqui. Duas cópias da mesma regra
    # divergiram no dia em que uma delas foi corrigida; agora há uma só.
    corpo = _objeto_json_do_corpo(raw, "PIX")

    try:
        evento = await _pix_adapter.parse_pix_event(corpo)
    except PayloadPixInvalido as e:
        logger.warning("[PIX] Payload recusado (%s) — 422. %s", e.motivo, e.detalhe)
        raise HTTPException(status_code=422,
                            detail={"motivo": e.motivo, "detalhe": e.detalhe})

    status = evento["status"]

    if status != STATUS_COBRANCA_FALHADA:
        rotulo = PIX_EVENTO_LABEL.get(status, status)
        logger.info("[PIX] %s — registrado sem acionar recuperação "
                    "(recorrencia=%s, degradacoes=%s)",
                    rotulo, evento["id_recorrencia"], evento["degradacoes"] or "nenhuma")
        return JSONResponse({"status": "ok", "evento": status, "pipeline": False})

    # A checagem vem DEPOIS do desvio acima de propósito: uma autorização
    # concedida legitimamente não carrega valor, e recusá-la transformaria a
    # correção do P0-5 num falso positivo. Só a cobrança falhada — a única que
    # entra no pipeline — precisa do valor.
    bloqueantes = sorted(set(evento["degradacoes"]) & DEGRADACOES_BLOQUEANTES)
    if bloqueantes:
        logger.warning(
            "[PIX] Cobrança falhada recusada (422): %s — recorrencia=%s. "
            "Diagnosticar R$ 0 levaria e-Profit a <= 0 e descartaria um churn "
            "involuntário legítimo sem rastro.",
            ", ".join(bloqueantes), evento["id_recorrencia"] or "desconhecida",
        )
        raise HTTPException(status_code=422, detail={
            "motivo": "evento_degradado", "degradacoes": bloqueantes,
        })

    # O identificador da autorização de recorrência é opaco: identifica o
    # contrato de cobrança, não a pessoa. A chave Pix nunca chega aqui.
    customer_id = _thread_id(evento)
    if customer_id is None:
        raise HTTPException(status_code=422, detail={
            "motivo": MOTIVO_SEM_IDENTIFICACAO,
            "detalhe": ("evento sem id_recorrencia e sem e2e_id — sem isso, "
                        "clientes distintos dividiriam o mesmo checkpoint"),
        })

    await _run_involuntary_pipeline(
        event=evento,
        payment_method="pix_automatico",
        customer_id=customer_id,
        amount=evento["valor"],
        invoice_id=evento["e2e_id"] or "e2e_desconhecido",
    )
    return JSONResponse({"status": "ok", "evento": status, "pipeline": True})


# ── Churn Involuntário — Cartão (recobrança automática desativada) ───────

@app.post("/webhooks/stripe")
async def stripe_webhook(request: Request) -> JSONResponse:
    """Recebe falhas de cobrança no cartão — registra, mas não recobra.

    O endpoint segue no ar com a validação de assinatura da Fase 1. Desde a
    Fase 3, porém, a recobrança automática de cartão está fora do pipeline
    ativo: o sistema de cobrança da CRAI é exclusivamente Pix Automático, e as
    duas políticas de retentativa são incompatíveis (ver
    crai/dunning/legacy_card/). O evento é registrado, nunca descartado em
    silêncio.
    """
    payload = await request.body()
    if not verify_stripe_signature(
        payload,
        request.headers.get("stripe-signature", ""),
        os.getenv("STRIPE_WEBHOOK_SECRET", ""),
    ):
        raise _reject_unsigned(request, "stripe")

    event = _objeto_json_do_corpo(payload, "STRIPE")

    # `data` e `data.object` são percorridos com `.get()` encadeado em
    # `_dados_stripe`, e `amount_due` entra numa divisão. Um `data` que não é
    # objeto, ou um `amount_due` que não é número, estourava ali.
    # `nulo_e_ausente=False` nos dois: `_dados_stripe` reencontra o payload CRU
    # e refaz `event.get("data", {}).get("object", {})`. Com `data: null` o
    # `.get` devolve `None` e o encadeamento estoura — um default calculado
    # aqui não alcançaria aquela leitura. Campo ausente segue valendo default.
    dados = _campo_com_forma(event, "data", (dict,), "STRIPE", default={},
                             nulo_e_ausente=False)
    fatura = _campo_com_forma(dados, "object", (dict,), "STRIPE", default={},
                              nulo_e_ausente=False)
    if "amount_due" in fatura:
        _campo_com_forma(fatura, "amount_due", (int, float), "STRIPE",
                         nulo_e_ausente=False)
    if "attempt_count" in fatura:
        _campo_com_forma(fatura, "attempt_count", (int,), "STRIPE",
                         nulo_e_ausente=False)
    # A guarda de inteiro vale para os DOIS webhooks. Ela nasceu no Segment,
    # onde o limite é o msgpack do checkpoint; aqui o limite aparece antes,
    # em `_dados_stripe`: `invoice.get("amount_due", 0) / 100` levanta
    # `OverflowError` — medido com `amount_due: 2**2000`, HTTP 500 — porque o
    # resultado não cabe num float. Mesma classe de defeito, mesma guarda:
    # fechá-la só num dos webhooks era metade da correção.
    _recusar_inteiro_grande_demais(dados, "STRIPE", "data")

    if event.get("type") == "invoice.payment_failed":
        _registrar_cartao_desativado(event)
        return JSONResponse({
            "status": "ok", "pipeline": False,
            "motivo": "recobranca_automatica_de_cartao_fora_do_pipeline_ativo",
        })
    return JSONResponse({"status": "ok", "pipeline": False})


class SimulatePayment(BaseModel):
    customer_id:  str   = "cus_demo_001"
    amount:       float = 299.90
    failure_code: str   = "insufficient_funds"


@app.post("/simulate/payment-failed")
async def simulate_payment_failed(payload: SimulatePayment) -> JSONResponse:
    """Falha de cartão simulada — mesmo tratamento do webhook real: só registra."""
    _require_simulation_env()
    # `_build_fake_stripe_event` faz `int(amount * 100)`: com `nan` ou `inf`
    # isso levanta ValueError e o FastAPI devolve 500.
    payload.amount = _valor_de_simulacao(payload.amount, "amount")
    _registrar_cartao_desativado(_build_fake_stripe_event(payload))
    return JSONResponse({
        "status": "registrado", "pipeline": False,
        "customer_id": payload.customer_id,
        "motivo": "recobranca_automatica_de_cartao_fora_do_pipeline_ativo",
    })


class SimulatePixFalha(BaseModel):
    id_recorrencia: str   = "RN_demo_001"
    valor:          float = 299.90
    ispb_pagador:   str   = "60701190"


@app.post("/simulate/pix-falhado")
async def simulate_pix_falhado(payload: SimulatePixFalha) -> JSONResponse:
    """Cobrança recorrente de Pix Automático que falhou, sem PSP real."""
    _require_simulation_env()

    # O valor passa pelos MESMOS portões do webhook assinado.
    #
    # Sintetizar o evento aqui não o torna confiável: `payload.valor` vem de
    # fora igual ao do webhook, e o Pydantic aceita `nan`, `inf` e `1e300` num
    # campo `float`. Sem esta checagem o endpoint da demo cria negócio de
    # `R$ -500,00` e de `R$ nan` no CRM, e devolve 500 com `1e300` — os defeitos
    # P0-5 e N-10 inteiros, pela porta que a apresentação de fato usa.
    valor = _valor_de_simulacao(payload.valor)

    evento = {
        "e2e_id": f"E{payload.ispb_pagador}{payload.id_recorrencia}",
        "valor": valor,
        "status": STATUS_COBRANCA_FALHADA,
        "ispb_pagador": payload.ispb_pagador,
        "id_recorrencia": payload.id_recorrencia,
        # Sintetizado após a validação acima: chega aqui sem degradação porque
        # o que degradaria já foi recusado, não porque ninguém olhou.
        "degradacoes": [],
    }
    # Mesma derivação de identidade do webhook real: se o simulador usasse o
    # `id_recorrencia` cru, um `id_recorrencia=""` na simulação produziria um
    # thread_id que o caminho real nunca produz — e o endpoint deixaria de
    # exercitar o código que a demo mostra.
    customer_id = _thread_id(evento)
    if customer_id is None:
        raise HTTPException(status_code=422, detail={"motivo": MOTIVO_SEM_IDENTIFICACAO})

    await _run_involuntary_pipeline(
        event=evento, payment_method="pix_automatico",
        customer_id=customer_id, amount=valor,
        invoice_id=evento["e2e_id"],
    )
    return JSONResponse({"status": "pipeline_executado", "id_recorrencia": payload.id_recorrencia})


# ── Churn Voluntário ─────────────────────────────────────────────────────

@app.post("/webhooks/segment")
async def segment_webhook(request: Request) -> JSONResponse:
    raw = await request.body()
    if not verify_segment_signature(
        raw,
        request.headers.get("x-signature", ""),
        os.getenv("SEGMENT_WEBHOOK_SECRET", ""),
    ):
        raise _reject_unsigned(request, "segment")

    payload = _objeto_json_do_corpo(raw, "SEGMENT")

    # As três formas que o pipeline de churn voluntário assume do evento.
    # `userId` vira chave de dicionário e `billing_profile` vira chave do
    # bandit: qualquer coisa não-hashable ali estourava com `TypeError`
    # dentro de `offer_bandit.py`, três camadas abaixo da borda.
    #
    # `userId` OU `anonymousId`: são as duas identidades do protocolo do
    # Segment, e um visitante que ainda não se identificou chega só com a
    # segunda. Exigir `userId` recusava com 422 um evento perfeitamente
    # legítimo — foi o que a auditoria A1-r7 mediu. O que a borda não pode
    # aceitar é evento SEM nenhuma identidade: o `thread_id` do checkpoint sai
    # daqui, e sem ele clientes distintos dividiriam o mesmo estado (é o mesmo
    # raciocínio do P0-6, no webhook de Pix).
    # As duas identidades vivem em ESPAÇOS DE NOME SEPARADOS. `userId` e
    # `anonymousId` são atribuídos por sistemas diferentes — o seu backend e o
    # SDK do navegador — e nada impede que coincidam. Sem separação, um
    # visitante anônimo cujo id calhasse de ser igual ao `userId` de um cliente
    # identificado herdava o checkpoint dele: medido, o segundo evento
    # SOBRESCREVEU o estado do primeiro (perfil CLT virou PJ). É o P0-6 por
    # outra porta, e a defesa é a mesma — o `thread_id` precisa ser único por
    # cliente, não só não-vazio.
    identificado = _campo_com_forma(payload, "userId", (str,), "SEGMENT")
    anonimo = _campo_com_forma(payload, "anonymousId", (str,), "SEGMENT")
    user_id = identificado or anonimo
    if not user_id:
        logger.warning("[SEGMENT] Evento sem userId e sem anonymousId — 422")
        raise HTTPException(status_code=422, detail={
            "motivo": MOTIVO_SEM_IDENTIFICACAO,
            "detalhe": ("evento sem `userId` e sem `anonymousId` — sem isso, "
                        "clientes distintos dividiriam o mesmo checkpoint"),
        })
    evento = _campo_com_forma(payload, "event", (str,), "SEGMENT", default="")
    props = _campo_com_forma(payload, "properties", (dict,), "SEGMENT", default={})
    if "billing_profile" in props:
        _campo_com_forma(props, "billing_profile", (str,), "SEGMENT")
    _recusar_inteiro_grande_demais(props, "SEGMENT")

    # A identidade que o negócio vê é o id cru; a chave do checkpoint é a
    # dupla (campo, valor). Ver `_thread_id_voluntario`.
    await _run_voluntary_pipeline(
        user_id=user_id, event=evento, props=props,
        thread_id=_thread_id_voluntario(
            "userId" if identificado else "anonymousId", user_id),
    )
    return JSONResponse({"status": "ok"})


class SimulateChurnRisk(BaseModel):
    user_id: str = "usr_demo_001"
    event:   str = "Cancellation Page Viewed"   # ou "Downgrade Clicked" | "Session Started"
    days_since_last:   int = 14
    features_used_30d: int = 2
    on_site_now: bool = True
    billing_profile: str = "CLT"


@app.post("/simulate/churn-risk")
async def simulate_churn_risk(payload: SimulateChurnRisk) -> JSONResponse:
    _require_simulation_env()
    props = {
        # Contadores validados antes de entrar no pipeline: sem isto, um
        # inteiro de 401 dígitos atravessa o Pydantic e estoura lá dentro —
        # com o negócio já criado no HubSpot antes do erro.
        "days_since_last":   _contador_de_simulacao(
            payload.days_since_last, "days_since_last"),
        "features_used_30d": _contador_de_simulacao(
            payload.features_used_30d, "features_used_30d"),
        "on_site_now":       payload.on_site_now,
        "billing_profile":   payload.billing_profile,
    }
    await _run_voluntary_pipeline(payload.user_id, payload.event, props)
    return JSONResponse({"status": "pipeline_executado", "user_id": payload.user_id})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "crai-agent-v2"}


# ── Helpers ────────────────────────────────────────────────────────────────

def _objeto_json_do_corpo(raw: bytes, origem: str) -> dict:
    """Corpo assinado -> objeto JSON, ou 400. Portão comum dos três webhooks.

    O webhook de Pix já tinha este portão; os de Stripe e Segment não. Assinar
    o corpo prova ORIGEM, não FORMA: um PSP legítimo com um bug de serialização
    manda um corpo assinado e torto, e a assinatura confere. O que vinha depois
    era `json.loads(raw)` cru, seguido de `.get()` — e `json.loads(b'[]')`
    devolve uma lista, que não tem `.get`.

    Medido antes deste portão, com assinatura VÁLIDA:
    `not json`, `[]`, `"texto"`, `5` e `null` davam HTTP 500 nos dois
    endpoints. Nenhum deles chegava a ser recusado: eles estouravam.

    `parse_constant` fecha `NaN`/`Infinity`/`-Infinity`, que o JSON do Python
    aceita por padrão e o JSON padrão não tem — a mesma porta que o P0-1 usou.
    """
    try:
        corpo = json.loads(raw, parse_constant=_rejeitar_constante_json)
    except json.JSONDecodeError as e:
        logger.warning("[%s] Corpo assinado não é JSON válido (%s) — 400", origem, e)
        raise HTTPException(status_code=400,
                            detail="Corpo do webhook não é JSON válido")
    except ValueError as e:
        logger.warning("[%s] Corpo assinado traz constante não numérica (%s) — 400",
                       origem, e)
        raise HTTPException(
            status_code=400,
            detail="Corpo do webhook contém Infinity/NaN, que não são JSON válido")
    except RecursionError:
        # O próprio `json.loads` estoura a pilha antes de qualquer validação,
        # com aninhamento suficiente. Recusar é a resposta; deixar subir é 500.
        logger.warning("[%s] Corpo assinado aninhado além do que o parser "
                       "suporta — 400", origem)
        raise HTTPException(status_code=400,
                            detail="Corpo do webhook está aninhado demais")

    if not isinstance(corpo, dict):
        logger.warning("[%s] Corpo assinado é %s, não um objeto JSON — 400",
                       origem, type(corpo).__name__)
        raise HTTPException(
            status_code=400,
            detail=f"Corpo do webhook deve ser um objeto JSON, e é {type(corpo).__name__}")
    return corpo


# O checkpoint do LangGraph é serializado em msgpack, que endereça inteiros de
# 64 bits. Um inteiro maior atravessa o JSON e o Pydantic intactos e só estoura
# na gravação do checkpoint, com `TypeError: Object of type int is not
# serializable` — 500, depois de o pipeline já ter rodado.
LIMITE_INTEIRO_SERIALIZAVEL = 2 ** 63 - 1


# Profundidade máxima de aninhamento aceita num objeto livre do payload.
# Existe porque a guarda não pode ser derrubável pelo que ela guarda: um
# `properties` com ~950 níveis fazia a varredura RECURSIVA estourar a pilha do
# Python (`RecursionError`) e devolver 500 — a guarda virava o próprio vetor.
# A varredura abaixo é ITERATIVA, então a pilha já não é o limite; este teto
# existe para o outro lado, o custo: um payload absurdamente aninhado é
# recusado em vez de percorrido. 100 níveis é ordens de grandeza acima de
# qualquer `properties` real do Segment.
PROFUNDIDADE_MAXIMA_DO_PAYLOAD = 100


def _recusar_inteiro_grande_demais(valor, origem: str, caminho: str = "properties"):
    """Varre o payload atrás de inteiro fora do alcance do checkpoint.

    Medido: `{"properties": {"days_since_last": 2**200}}` com assinatura
    válida devolvia HTTP 500. O mesmo com `features_used_30d` e `on_site_now`,
    e com `data.object.amount_due` no Stripe, onde o inteiro entra numa divisão
    e levanta `OverflowError`. O JSON é válido, o campo tem o nome certo, e
    mesmo assim derruba — porque o limite não é do JSON, é de quem grava o
    estado (msgpack, 64 bits) e de quem faz a conta.

    Percorre em qualquer profundidade porque `properties` e `data` são objetos
    livres: o número pode estar em qualquer nível, e barrar só o primeiro
    deixaria a porta aberta. **Iterativa**, com pilha explícita: a versão
    recursiva desta função era derrubável pelo próprio payload que ela
    inspeciona (`RecursionError` -> 500 a partir de ~950 níveis).
    """
    pilha = [(valor, caminho, 0)]
    while pilha:
        item, onde, nivel = pilha.pop()

        if nivel > PROFUNDIDADE_MAXIMA_DO_PAYLOAD:
            logger.warning("[%s] Payload aninhado além de %d níveis em %s — 422",
                           origem, PROFUNDIDADE_MAXIMA_DO_PAYLOAD, onde)
            raise HTTPException(status_code=422, detail={
                "motivo": "payload_aninhado_demais", "campo": onde,
                "detalhe": (f"mais de {PROFUNDIDADE_MAXIMA_DO_PAYLOAD} níveis "
                            "de aninhamento"),
            })

        if isinstance(item, bool):
            continue
        if isinstance(item, int) and abs(item) > LIMITE_INTEIRO_SERIALIZAVEL:
            logger.warning("[%s] Inteiro fora do alcance do checkpoint em %s — 422",
                           origem, onde)
            raise HTTPException(status_code=422, detail={
                "motivo": "inteiro_fora_do_alcance", "campo": onde,
                "detalhe": (f"inteiros acima de {LIMITE_INTEIRO_SERIALIZAVEL} não "
                            "cabem no checkpoint do agente nem na conta de valor"),
            })
        if isinstance(item, dict):
            pilha.extend((v, f"{onde}.{k}", nivel + 1) for k, v in item.items())
        elif isinstance(item, (list, tuple)):
            pilha.extend((v, f"{onde}[{i}]", nivel + 1) for i, v in enumerate(item))


def _campo_com_forma(corpo: dict, campo: str, tipos: tuple, origem: str,
                     obrigatorio: bool = False, default=None,
                     nulo_e_ausente: bool = True):
    """Lê um campo do payload exigindo a FORMA que o pipeline assume dele.

    Não é validação decorativa: cada tipo recusado aqui corresponde a um 500
    medido. `userId` virava chave de dicionário lá dentro, então uma lista ou
    um dict davam `TypeError: unhashable type`. `properties` era usado com
    `.get()`, então uma string dava `AttributeError`. O erro aparecia três
    camadas abaixo, com traceback de módulo de negócio, para um problema que é
    de contrato de entrada.

    Recusar na borda mantém a regra no lugar onde ela é verdadeira e evita
    espalhar `isinstance` por dentro do pipeline — que, no caso do
    `churn_voluntary/`, o plano do sprint declara fora de escopo.
    """
    if campo not in corpo:
        if obrigatorio:
            logger.warning("[%s] Campo %r ausente — 422", origem, campo)
            raise HTTPException(status_code=422, detail={
                "motivo": "campo_obrigatorio_ausente", "campo": campo,
            })
        return default

    valor = corpo[campo]
    if valor is None:
        # `null` explícito não é o mesmo que campo ausente, e nem sempre pode
        # ser tratado como tal: `{"amount_due": null}` entrava numa divisão e
        # dava `TypeError: unsupported operand`, HTTP 500. Onde o campo apenas
        # não se aplica (`properties: null`), o default segue valendo.
        if obrigatorio or not nulo_e_ausente:
            logger.warning("[%s] Campo %r veio nulo, esperado %s — 422", origem,
                           campo, " ou ".join(t.__name__ for t in tipos))
            raise HTTPException(status_code=422, detail={
                "motivo": "campo_com_forma_invalida", "campo": campo,
                "detalhe": "recebido null",
            })
        return default

    if not isinstance(valor, tipos):
        esperado = " ou ".join(t.__name__ for t in tipos)
        logger.warning("[%s] Campo %r veio como %s, esperado %s — 422",
                       origem, campo, type(valor).__name__, esperado)
        raise HTTPException(status_code=422, detail={
            "motivo": "campo_com_forma_invalida", "campo": campo,
            "detalhe": f"esperado {esperado}, recebido {type(valor).__name__}",
        })
    return valor


def _registrar_cartao_desativado(event: dict) -> dict:
    """Registra uma falha de cartão sem acionar recobrança automática.

    O prefixo [CARTAO-DESATIVADO] existe para deixar explícito, no log e na
    demo, que o evento chegou e foi reconhecido — o que não aconteceu foi a
    retentativa, que aguarda a reimplementação descrita no roadmap da Fase 3.
    """
    dados = _dados_stripe(event)
    aviso = (f"[CARTAO-DESATIVADO] {dados['customer_id']} | fatura "
             f"{dados['invoice_id']} | R$ {dados['amount']:.2f} — evento registrado, "
             f"recobrança automática de cartão fora do pipeline ativo "
             f"(aguardando reimplementação; ver crai/dunning/legacy_card/)")
    print(aviso)
    logger.warning(aviso)
    return dados


def _dados_stripe(event: dict) -> dict:
    """Extrai do payload do Stripe os campos de entrada do pipeline."""
    invoice = event.get("data", {}).get("object", {})
    return {
        "customer_id": invoice.get("customer", "cus_unknown"),
        "amount": invoice.get("amount_due", 0) / 100,
        "invoice_id": invoice.get("id", "inv_unknown"),
        # attempt_count do Stripe conta a cobranca original; retentativas ja feitas = count - 1
        "retries_done": max(0, invoice.get("attempt_count", 1) - 1),
    }


# Uma trava por `thread_id`, criada sob demanda. `WeakValueDictionary` porque
# o dicionário não pode crescer para sempre: enquanto alguém segura a trava há
# referência forte viva; quando o último a soltar sai de cena, a entrada some
# sozinha. Um `dict` comum viraria um vazamento indexado por id de cliente.
_travas_por_cliente: "weakref.WeakValueDictionary[str, asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)


def _trava_do_cliente(customer_id: str) -> asyncio.Lock:
    """Serializa as execuções do pipeline de um MESMO cliente.

    O `ainvoke` lê o checkpoint na entrada e o grava nó a nó. Entre a leitura e
    a gravação há `await` em quatro nós (diagnóstico, anomalia, payday e o
    próprio agendamento), e um PSP entrega eventos **at-least-once**: duas
    entregas do mesmo `id_recorrencia` chegando juntas intercalam de verdade.
    As duas leem `retry_count = 0`, as duas passam por `decide_recovery` com
    zero tentativas usadas, e as duas recebem 3 da política — que está certa,
    porque cada chamada isolada respeita o limite. O invariante quebrado é
    ENTRE chamadas.

    Medido antes desta trava, com N requisições concorrentes do mesmo
    `id_recorrencia` pelo webhook assinado, sem mock nenhum:

        N=1 -> 3 tentativas   numeros [1,2,3]
        N=2 -> 6 tentativas   numeros [1,2,3,1,2,3]
        N=3 -> 9 tentativas   numeros [1,2,3,1,2,3,1,2,3]

    contra o limite legal de 3 — e o checkpoint registrando `retry_count: 3`
    nos três casos, ou seja, **subnotificando** o que foi comprometido. Era o
    mesmo 3+3+3=9 que a rodada 4 fechou pela chegada sequencial e que a chegada
    concorrente mantinha aberto.

    A trava cobre o `ainvoke` inteiro, leitura e escrita. Clientes diferentes
    continuam correndo em paralelo: o que serializa é o `thread_id`, que é a
    unidade de estado.

    Isto fecha o caso de **um processo**. Entre processos não há nada — ver o
    comentário do `MemorySaver` em `crai/agent/main_agent.py` e a linha `P1-14`
    do README.
    """
    trava = _travas_por_cliente.get(customer_id)
    if trava is None:
        # Sem `await` entre a consulta e a escrita: num event loop só, este
        # trecho é atômico, e dois pedidos concorrentes pegam a MESMA trava.
        trava = asyncio.Lock()
        _travas_por_cliente[customer_id] = trava
    return trava


async def _run_involuntary_pipeline(
    event: dict,
    payment_method: PaymentMethod,
    customer_id: str,
    amount: float,
    invoice_id: str,
    retries_done: Optional[int] = None,
) -> None:
    """Monta o state inicial e roda o grafo de churn involuntário.

    `payment_method` é preenchido AQUI, na entrada, antes de qualquer nó de
    decisão — é o que permite ao grafo escolher a política de retentativa certa
    sem nunca precisar inferir a origem do evento depois.

    **`retry_count` é omitido de propósito quando `retries_done is None`**, e
    esse é o ponto inteiro desta função. O `ainvoke` aplica o dicionário de
    entrada como *atualização* sobre o checkpoint do `thread_id`: toda chave
    presente aqui SOBRESCREVE o que o checkpoint guardava. Enquanto esta função
    escrevia `"retry_count": 0` incondicionalmente, cada novo webhook do mesmo
    `id_recorrencia` zerava o contador que `schedule_retry_pix` tinha acabado
    de gravar — e três webhooks do mesmo cliente agendavam 3 + 3 + 3 = **nove**
    tentativas na mesma janela de 7 dias, todas numeradas 1, 2, 3. O limite do
    BACEN é 3. O checkpoint guardava o número certo; era a entrada que o
    apagava.

    Omitir a chave preserva o valor acumulado no checkpoint. Para um cliente
    sem checkpoint a chave simplesmente não existe, e os nós já leem com
    `state.get("retry_count", 0)`.

    `pix_janela_ate` é omitido pelo mesmo motivo, e é o par indispensável do
    contador: sozinho, `retry_count` acumula para sempre e o limite do BACEN
    vira "3 por contrato" em vez de "3 por janela de 7 dias". Quem decide se o
    contador ainda vale é `_janela_vigente` (crai/agent/workflow.py), que só
    consegue decidir porque encontra a marca da janela no checkpoint. Escrever
    qualquer um dos dois aqui apaga essa memória.

    `retries_done` continua aceito para a origem em que o contador é **externo
    e autoritativo** — o `attempt_count` do Stripe, quando a recobrança de
    cartão voltar ao pipeline ativo. Aí a verdade vem de fora e deve mesmo
    sobrescrever o checkpoint. No Pix o recebedor é quem conta, e quem conta é
    o checkpoint.
    """
    initial: AgentState = {
        "payment_event": event, "payment_method": payment_method,
        "customer_id": customer_id, "invoice_id": invoice_id, "amount": amount,
        "failure_cause": None, "recovery_score": None, "p_recovery": None,
        "eprofit": None, "recommend_action": None, "ltv_estimated": None,
        "shap_explanation": None, "feature_importance": None,
        "is_anomalous": None, "reconstruction_error": None, "anomaly_explanation": None,
        "optimal_retry_at": None,
        "estrategia": None, "raciocinio": None,
        "confidence": None, "profile_type": None, "next_retry_at": None,
        "retry_exhausted": False, "recovered": False, "pix_retry_schedule": None,
        "dunning_sent": False,
        "channel": None, "metodo_pagamento": None, "message_sent": None,
    }
    # Só entra na atualização quando a origem tem um contador autoritativo;
    # caso contrário o checkpoint mantém o que já acumulou (ver docstring).
    if retries_done is not None:
        initial["retry_count"] = retries_done

    # Uma execução por vez, por cliente. Ver `_trava_do_cliente`.
    config = {"configurable": {"thread_id": customer_id}}
    async with _trava_do_cliente(customer_id):
        await crai_agent.ainvoke(initial, config)


def _thread_id_voluntario(campo: str, valor: str) -> str:
    """Identidade do checkpoint de um evento de churn voluntário.

    O prefixo `anon:` — primeira tentativa desta separação — resolvia metade do
    problema. `anon:` + `anonymousId="vitima"` dá a mesma string que um
    `userId` literalmente igual a `"anon:vitima"`, e essa é a direção PIOR: o
    `anonymousId` é o campo que o visitante escolhe. Medido antes desta
    correção: o evento anônimo sobrescreveu o estado do cliente identificado.

    Serializar a dupla `(campo, valor)` como JSON torna o mapeamento injetivo:
    o JSON escapa o conteúdo, então nenhum valor de um campo consegue produzir
    a codificação de outro. É a mesma disciplina — e pelo mesmo motivo — que
    `_thread_id` já usa no webhook de Pix, onde concatenar com `|` deixava
    `e2e="E123|999"` colidir com `ispb="999|60701190"` (P0-6).
    """
    return json.dumps([campo, valor], ensure_ascii=False, sort_keys=True)


async def _run_voluntary_pipeline(user_id: str, event: str, props: dict,
                                  thread_id: Optional[str] = None):
    initial: ChurnVoluntaryState = {
        "user_id": user_id, "event": event, "props": props,
        "risk_score": 0.0, "profile": "CLT", "offer_type": None,
        "channel": None, "on_site_now": props.get("on_site_now", False),
        "prior_channel_success": None, "message": None,
        "offer_sent": False, "accepted": None, "retained": False, "escalated_to_human": False,
    }
    # `user_id` é a identidade do NEGÓCIO — vai para o state e para o HubSpot.
    # `thread_id` é a chave do CHECKPOINT, que precisa ser única por origem da
    # identidade. Separá-las evita que o prefixo de desambiguação vaze para o
    # nome do contato no CRM. Sem `thread_id` (origem `/simulate/*`), a
    # identidade é a própria chave, como sempre foi.
    config = {"configurable": {"thread_id": thread_id or user_id}}
    await voluntary_churn_agent.ainvoke(initial, config)


def _build_fake_stripe_event(p: SimulatePayment) -> dict:
    return {
        "id": f"evt_test_{p.customer_id}", "type": "invoice.payment_failed",
        "data": {"object": {
            "id": f"inv_test_{p.customer_id}", "customer": p.customer_id,
            "amount_due": int(p.amount * 100), "currency": "brl",
            "failure_code": p.failure_code, "failure_message": f"Teste: {p.failure_code}",
            "attempt_count": 1, "payment_method_details": {"brand": "visa", "last4": "4242"},
        }},
    }

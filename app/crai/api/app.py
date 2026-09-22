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
import re
import json
import math
import asyncio
import hashlib
import logging
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Carrega o .env ANTES de qualquer módulo ler os.getenv. Sem isto, `uvicorn
# crai.api.app:app` ignora o arquivo .env por completo: só test_pipeline.py
# chamava load_dotenv(), então ENV=development ficava invisível e os endpoints
# /simulate/* respondiam 403 mesmo com o .env preenchido do lado.
from dotenv import load_dotenv                      # noqa: E402
load_dotenv()

from ..agent.main_agent import crai_agent
from ..agent.pix_codes import CAUSA_LEGIVEL
from ..agent.state import AgentState, PaymentMethod
from ..agent.workflow import (
    success_fee,
    tentativas_ja_disparadas,
    update_roi_dashboard,
)
from ..dunning import recovery_log
from ..churn_voluntary.voluntary_agent import agente_do_modo, registrar_resultado_externo
from ..churn_voluntary.offer_bandit import OFFERS, PROFILES, TENANT_PADRAO
from ..churn_voluntary.state import ChurnVoluntaryState
from ..churn_voluntary import (clientes_importados, disparo_lote, importacao,
                               insights_unificados, retention_log)
from ..integrations import email_sender
from ..accounts import get_conta, get_tenant_id
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
from .idempotencia import (
    CICLOS_FECHADOS,
    EVENTOS_DE_FALHA,
    chave_do_evento,
)
from . import clientes as clientes_api
from . import titular as titular_api
from ..security.webhook_verification import (
    verify_stripe_signature,
    verify_segment_signature,
    verify_pix_automatico_signature,
    verify_retention_outcome_signature,
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

    # Qual empresa cliente da CRAI mandou este evento. Mesmo portão do
    # voluntário (`x-tenant-id` no header, senão `tenant_id` no corpo, senão
    # `default_tenant`): um tenant declarado e torto é 422, ausência é o balde
    # do MVP. Lido ANTES do parse porque vale para todo status, inclusive a
    # confirmação de pagamento, que não passa pelo pipeline.
    tenant_id = _tenant_da_requisicao(request, corpo, "PIX")

    try:
        evento = await _pix_adapter.parse_pix_event(corpo)
    except PayloadPixInvalido as e:
        logger.warning("[PIX] Payload recusado (%s) — 422. %s", e.motivo, e.detalhe)
        raise HTTPException(status_code=422,
                            detail={"motivo": e.motivo, "detalhe": e.detalhe})

    status = evento["status"]

    # A cobrança PAGA é o outro fim do ciclo, e é o evento que faltava.
    # Ela não roda o pipeline de diagnóstico — não há falha a diagnosticar —,
    # mas é a única origem que sabe que uma recuperação deu certo.
    if status == STATUS_COBRANCA_CONFIRMADA:
        return await _confirmar_cobranca_paga(evento, tenant_id)

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

    # Reenvio da MESMA falha não roda o pipeline de novo (Gap 3). A trava por
    # `thread_id` e o contador do checkpoint já impediam o 3+3 de tentativas;
    # o que continuava acontecendo era o custo: diagnóstico, LLM e — a partir
    # do Sprint 2 — chamada ao PSP repetidos por um evento já tratado.
    if not EVENTOS_DE_FALHA.registrar_se_novo(
        chave_do_evento(evento["id_recorrencia"], evento["e2e_id"])
    ):
        logger.info("[PIX] Cobrança falhada reenviada (recorrencia=%s, e2e=%s) — "
                    "pipeline não reexecutado.",
                    evento["id_recorrencia"] or "desconhecida", evento["e2e_id"][:16])
        # 200 e não 4xx: reenvio é comportamento correto do PSP, e um erro o
        # faria retentar para sempre o que já foi processado. Mesma escolha do
        # `/webhooks/retention-outcome`.
        return JSONResponse({"status": "ok", "evento": status, "pipeline": False,
                             "motivo": "evento_ja_processado"})

    await _run_involuntary_pipeline(
        event=evento,
        payment_method="pix_automatico",
        customer_id=customer_id,
        amount=evento["valor"],
        invoice_id=evento["e2e_id"] or "e2e_desconhecido",
        tenant_id=tenant_id,
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

    # Lido mesmo com a recobrança de cartão fora do pipeline ativo: o registro
    # `[CARTAO-DESATIVADO]` é evento de negócio de ALGUÉM, e quando o cartão
    # voltar (ver `dunning/legacy_card/`) o caminho já estará atribuído. Um
    # tenant torto é recusado aqui como nos outros webhooks — a validação não
    # depende do que o pipeline faz depois.
    tenant_id = _tenant_da_requisicao(request, event, "STRIPE")

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
        _registrar_cartao_desativado(event, tenant_id)
        return JSONResponse({
            "status": "ok", "pipeline": False,
            "motivo": "recobranca_automatica_de_cartao_fora_do_pipeline_ativo",
        })
    return JSONResponse({"status": "ok", "pipeline": False})


class SimulatePayment(BaseModel):
    customer_id:  str   = "cus_demo_001"
    amount:       float = 299.90
    failure_code: str   = "insufficient_funds"
    tenant_id: Optional[str] = None


@app.post("/simulate/payment-failed")
async def simulate_payment_failed(request: Request,
                                  payload: SimulatePayment) -> JSONResponse:
    """Falha de cartão simulada — mesmo tratamento do webhook real: só registra."""
    _require_simulation_env()
    # `_build_fake_stripe_event` faz `int(amount * 100)`: com `nan` ou `inf`
    # isso levanta ValueError e o FastAPI devolve 500.
    payload.amount = _valor_de_simulacao(payload.amount, "amount")
    tenant_id = _tenant_da_requisicao(
        request, {"tenant_id": payload.tenant_id} if payload.tenant_id else {},
        "SIMULATE")
    _registrar_cartao_desativado(_build_fake_stripe_event(payload), tenant_id)
    return JSONResponse({
        "status": "registrado", "pipeline": False,
        "customer_id": payload.customer_id,
        "motivo": "recobranca_automatica_de_cartao_fora_do_pipeline_ativo",
    })


class SimulatePixFalha(BaseModel):
    id_recorrencia: str   = "RN_demo_001"
    valor:          float = 299.90
    ispb_pagador:   str   = "60701190"
    codigo_falha:   str   = "AM04"      # ver crai/agent/pix_codes.py
    tenant_id: Optional[str] = None


@app.post("/simulate/pix-falhado")
async def simulate_pix_falhado(request: Request,
                               payload: SimulatePixFalha) -> JSONResponse:
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
        # O motivo cru da recusa, que o PIX_CODE_MAP traduz (Sprint 3). Sem
        # ele o endpoint da demo exercitaria só o default do mapa, e deixaria
        # de exercitar o código que a demo mostra — é o N-7 outra vez.
        "codigo_falha": payload.codigo_falha,
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

    # Mesmo portão de tenant do webhook assinado, pelo mesmo motivo (N-7): o
    # endpoint da demo tem que exercitar o código que a demo mostra.
    tenant_id = _tenant_da_requisicao(
        request, {"tenant_id": payload.tenant_id} if payload.tenant_id else {},
        "SIMULATE")

    await _run_involuntary_pipeline(
        event=evento, payment_method="pix_automatico",
        customer_id=customer_id, amount=valor,
        invoice_id=evento["e2e_id"], tenant_id=tenant_id,
    )
    return JSONResponse({"status": "pipeline_executado", "id_recorrencia": payload.id_recorrencia})


class SimulatePixPago(BaseModel):
    id_recorrencia: str = "RN_demo_001"
    tenant_id: Optional[str] = None
    # O e2e da cobrança PAGA é outro que não o da falhada — é outra transação.
    # Default derivado do id da recorrência para a demo não precisar inventá-lo.
    e2e_id: Optional[str] = None
    valor:  Optional[float] = None


@app.post("/simulate/pix-pago")
async def simulate_pix_pago(request: Request,
                            payload: SimulatePixPago) -> JSONResponse:
    """Confirmação de pagamento sem PSP real — fecha o ciclo de recuperação.

    É a outra metade de `/simulate/pix-falhado`, e existe pelo mesmo motivo que
    `/webhooks/retention-outcome` existe no voluntário: sem uma origem de
    desfecho, `recovered` nunca vira `True` e a demo mostra três clientes em
    retentativa e nenhuma recuperação. Com este endpoint a banca vê o ciclo
    inteiro — falha → agendamento → confirmação → fee.

    Só fecha ciclo que a CRAI abriu: se não houver checkpoint com diagnóstico
    para aquele `id_recorrencia`, responde `sem_ciclo_aberto` e não conta fee
    (a mesma regra do webhook real — ver `_fechar_ciclo_recuperado`).
    """
    _require_simulation_env()

    valor = _valor_de_simulacao(payload.valor) if payload.valor is not None else None
    e2e_id = payload.e2e_id or f"E{payload.id_recorrencia}_pago"

    tenant_id = _tenant_da_requisicao(
        request, {"tenant_id": payload.tenant_id} if payload.tenant_id else {},
        "SIMULATE")

    resultado = await _fechar_ciclo_recuperado(
        customer_id=payload.id_recorrencia, e2e_id=e2e_id, valor=valor,
        tenant_id=tenant_id,
    )
    return JSONResponse({"status": "confirmacao_processada",
                         "id_recorrencia": payload.id_recorrencia, **resultado})


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

    await _run_voluntary_pipeline(
        user_id=_identidade_voluntaria(
            "userId" if identificado else "anonymousId", user_id),
        event=evento, props=props,
        tenant_id=_tenant_da_requisicao(request, payload, "SEGMENT"),
    )
    return JSONResponse({"status": "ok"})


@app.post("/webhooks/retention-outcome")
async def retention_outcome_webhook(request: Request) -> JSONResponse:
    """O desfecho REAL de uma oferta de retenção, vindo do backend do cliente.

    É a metade que faltava do Sprint 4: em produção o grafo termina no envio, e
    é ESTE endpoint que ensina o bandit. Sem ele o aprendizado vinha de
    `random.random()`.

    SEGREDO PRÓPRIO (`RETENTION_OUTCOME_WEBHOOK_SECRET`), não o do Segment: quem
    envia aqui é o backend do cliente, e duas origens diferentes não dividem
    credencial. Sem o segredo no ambiente, 401 em tudo — fail closed, como os
    outros três webhooks. Um endpoint que move o posterior do bandit é
    exatamente o que não pode aceitar request forjado: com ele aberto, qualquer
    um faria a CRAI acreditar que `desconto_20` converte 100%.

    A FORMA DOS CAMPOS É VALIDADA CONTRA O VOCABULÁRIO, não só contra o tipo.
    `profile` e `offer_type` viram CHAVE de dicionário dentro do bandit, e
    `record_outcome` aceita qualquer string e persiste — foi assim que perfis
    como `""` e `"299,90"` entraram no `bandit_state.json` real (ver o
    saneamento no Sprint 1). O Sprint 1 limpou a leitura; recusar aqui fecha a
    ESCRITA, que é onde o lixo nasce.

    `accepted` exige `bool` de verdade. `"true"` e `1` são recusados de
    propósito: um cliente que mandar `accepted: "false"` — string não-vazia,
    logo `True` em Python — registraria aceite onde houve recusa, e o bandit
    aprenderia o contrário do que aconteceu.
    """
    raw = await request.body()
    if not verify_retention_outcome_signature(
        raw,
        request.headers.get("x-signature", ""),
        os.getenv("RETENTION_OUTCOME_WEBHOOK_SECRET", ""),
    ):
        raise _reject_unsigned(request, "retention-outcome")

    payload = _objeto_json_do_corpo(raw, "OUTCOME")

    user_id = _campo_com_forma(payload, "user_id", (str,), "OUTCOME", obrigatorio=True)
    if not user_id.strip():
        raise HTTPException(status_code=422, detail={
            "motivo": "campo_com_forma_invalida", "campo": "user_id",
            "detalhe": "identidade vazia — sem ela o desfecho não tem dono",
        })

    offer_type = _campo_com_forma(payload, "offer_type", (str,), "OUTCOME", obrigatorio=True)
    profile = _campo_com_forma(payload, "profile", (str,), "OUTCOME", obrigatorio=True)
    tenant_id = _tenant_da_requisicao(request, payload, "OUTCOME")

    for campo, valor, vocabulario in (("offer_type", offer_type, OFFERS),
                                      ("profile", profile, PROFILES)):
        if valor not in vocabulario:
            logger.warning("[OUTCOME] %s=%r fora do vocabulário — 422", campo, valor)
            raise HTTPException(status_code=422, detail={
                "motivo": "valor_fora_do_vocabulario", "campo": campo,
                "detalhe": f"esperado um de {vocabulario}",
            })

    accepted = payload.get("accepted")
    if not isinstance(accepted, bool):
        logger.warning("[OUTCOME] accepted=%r não é booleano — 422", accepted)
        raise HTTPException(status_code=422, detail={
            "motivo": "campo_com_forma_invalida", "campo": "accepted",
            "detalhe": ("esperado bool; \"true\"/1 são recusados porque uma "
                        "string não-vazia vira True e inverteria uma recusa"),
        })

    # A mesma qualificação do `/webhooks/segment` e do `/simulate/churn-risk`:
    # o ciclo foi gravado com a identidade qualificada, e é por ela que o
    # desfecho reencontra a linha. Desfecho vem do backend do cliente, que fala
    # de cliente identificado — visitante anônimo não gera retorno de servidor.
    identidade = _identidade_voluntaria("userId", user_id)

    resultado = await registrar_resultado_externo(
        identidade, offer_type, profile, accepted, tenant_id=tenant_id)

    if not resultado["contabilizado"]:
        # 200, não erro: reenvio é comportamento esperado de webhook, e um 4xx
        # faria o cliente retentar para sempre o que já foi processado.
        return JSONResponse({"status": "ignorado",
                             "motivo": "reenvio_ou_ciclo_inexistente"})

    return JSONResponse({"status": "contabilizado", "accepted": accepted})


class SimulateChurnRisk(BaseModel):
    user_id: str = "usr_demo_001"
    event:   str = "Cancellation Page Viewed"   # ou "Downgrade Clicked" | "Session Started"
    days_since_last:   int = 14
    features_used_30d: int = 2
    on_site_now: bool = True
    billing_profile: str = "CLT"
    tenant_id: Optional[str] = None
    # Sem telefone o canal WhatsApp fica inalcançável pela demo, e o endpoint
    # deixaria de exercitar o código que a demo mostra — é o N-7 outra vez.
    # `Optional[str]` já barra lista e dict no Pydantic; a forma do número quem
    # decide é `destino_utilizavel`, no sender.
    phone: Optional[str] = None


@app.post("/simulate/churn-risk")
async def simulate_churn_risk(request: Request,
                             payload: SimulateChurnRisk) -> JSONResponse:
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
    if payload.phone is not None:
        props["phone"] = payload.phone
    # Mesma qualificação do webhook. Sem isto, `/simulate/churn-risk` e o
    # webhook do Segment produziriam identidades diferentes para o mesmo
    # cliente, e o endpoint da demo deixaria de exercitar o código que a demo
    # mostra. É o N-7 — já cobrado e fechado no lado involuntário, onde
    # `/simulate/pix-falhado` passa pelo mesmo `_thread_id` do webhook.
    # Mesma validação do webhook, para o endpoint da demo exercitar o mesmo
    # código (N-7): aqui também o tenant pode vir por header `x-tenant-id`.
    tenant = _tenant_da_requisicao(
        request, {"tenant_id": payload.tenant_id} if payload.tenant_id else {},
        "SIMULATE")
    await _run_voluntary_pipeline(
        _identidade_voluntaria("userId", payload.user_id),
        payload.event, props, tenant_id=tenant,
    )
    return JSONResponse({"status": "pipeline_executado", "user_id": payload.user_id})


@app.get("/metrics/recovery")
async def metricas_de_recuperacao(tenant_id: Optional[str] = None,
                                  desde: Optional[str] = None) -> JSONResponse:
    """O agregado de negócio do churn involuntário (Gap 7).

    MRR recuperado, taxa de recuperação, custo total, custo médio POR
    RECUPERAÇÃO e margem (fee − custo). É o número que sustenta o modelo
    Outcome-as-a-Service: sem ele, "recuperamos R$ X" é afirmação sem
    denominador e sem custo.

    NÃO é o dashboard do cliente — é o dado consultável de onde um dashboard
    (ou a apresentação da banca) tira os números. A fonte é o log append-only
    de ciclos (`dunning/recovery_log.py`), e não o checkpoint do grafo: o
    checkpoint é memória de processo e some no restart.

    ⚠️ SEM AUTENTICAÇÃO nesta fase, e declarado em vez de escondido: o projeto
    não tem camada de auth além da assinatura dos webhooks, e este endpoint
    expõe números de negócio agregados. Em produção ele fica atrás do mesmo
    controle de acesso do dashboard; `tenant_id` aqui é FILTRO, não permissão.

    Args:
        tenant_id: restringe a uma empresa cliente. Ausente = todas.
        desde: ISO-8601 (`2026-09-01` ou `2026-09-01T00:00:00+00:00`).
    """
    return JSONResponse(recovery_log.metricas(tenant_id=tenant_id, desde=desde))


@app.get("/health")
async def health():
    return {"status": "ok", "service": "crai-agent-v2"}


# ── Self-service (empresa autenticada via Supabase) ───────────────────────
# Estas rotas são o caminho (2) do onboarding: a empresa anexa a base que já
# tem. Autenticação é o JWT do Supabase (`accounts.get_tenant_id`), NÃO a
# assinatura HMAC dos webhooks — são contratos diferentes para chamadores
# diferentes (um frontend logado vs. um PSP/Segment assinando payload).

# As rotas de sincronização um a um (`POST /clientes`, `/clientes/lote`,
# `PATCH`, `DELETE`) moram em `api/clientes.py`; este arquivo só as monta.
app.include_router(clientes_api.router)
# O direito à explicação (LGPD Art. 20) para a CONTROLADORA — `api/titular.py`.
# Autenticada por tenant; não existe rota pública para o titular.
app.include_router(titular_api.router)


@app.post("/clientes/importar")
async def importar_clientes(
    arquivo: UploadFile = File(..., description="CSV ou XLSX com a base de clientes"),
    mapeamento: Optional[str] = Form(
        default=None,
        description='JSON {"coluna no arquivo": "campo esperado"}, ex. '
                    '{"Última atividade": "days_since_last", "MRR (R$)": "mrr"}'),
    tenant_id: str = Depends(get_tenant_id),
) -> JSONResponse:
    """Upload em lote da base de clientes da empresa autenticada.

    Campos esperados no arquivo (nome exato, case-insensitive, ou via
    `mapeamento`): `customer_id_externo`, `mrr`, `billing_profile`
    (obrigatórios); `days_since_last`, `features_used_30d`, `email`
    (opcionais). Reimportar o mesmo `customer_id_externo` ATUALIZA a linha.

    Resposta: `{importados, rejeitados: [{linha, motivo}],
    colunas_nao_encontradas, linhas_sem_dado_comportamental}`. Uma linha
    inválida não derruba o lote. Ver `churn_voluntary/importacao.py`.
    """
    conteudo = await arquivo.read()
    try:
        relatorio = importacao.importar(
            tenant_id, arquivo.filename or "", conteudo, mapeamento=mapeamento)
    except importacao.ArquivoInvalido as e:
        logger.warning("[IMPORTACAO] tenant=%s recusado: %s", tenant_id, e.motivo)
        raise HTTPException(status_code=e.status,
                            detail={"motivo": e.motivo, "detalhe": e.mensagem})
    except clientes_importados.ConfiguracaoAusente as e:
        logger.error("[IMPORTACAO] %s", e)
        raise HTTPException(status_code=500, detail={
            "motivo": "base_nao_configurada", "detalhe": str(e)})
    return JSONResponse(relatorio)


CRITICIDADES_FILTRAVEIS = ("alto", "critico")


def _filtros_de_insights(limite: Optional[int], criticidade_minima: Optional[str]) -> tuple:
    """Valida os query params do /insights: 422 com motivo, nunca filtro silencioso."""
    if limite is not None and limite < 1:
        raise HTTPException(status_code=422, detail={
            "motivo": "limite_invalido", "campo": "limite",
            "detalhe": "esperado inteiro >= 1"})
    crit = criticidade_minima.strip().lower() if criticidade_minima else None
    if crit is not None and crit not in CRITICIDADES_FILTRAVEIS:
        raise HTTPException(status_code=422, detail={
            "motivo": "criticidade_invalida", "campo": "criticidade_minima",
            "detalhe": f"esperado um de {', '.join(CRITICIDADES_FILTRAVEIS)}"})
    return limite, crit


def _insights_do_tenant(tenant_id: str, limite, criticidade_minima,
                        idioma: str = "pt") -> dict:
    try:
        ranking = insights_unificados.clientes_em_risco(tenant_id, idioma)
    except clientes_importados.ConfiguracaoAusente as e:
        logger.error("[INSIGHTS] %s", e)
        raise HTTPException(status_code=500, detail={
            "motivo": "base_nao_configurada", "detalhe": str(e)})
    filtrado = insights_unificados.filtrar(ranking, limite, criticidade_minima)
    return {
        "total_clientes": len(ranking),
        "clientes_em_risco": filtrado,
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "filtros": {"limite": limite, "criticidade_minima": criticidade_minima},
    }


@app.get("/insights")
async def insights(
    limite: Optional[int] = None,
    criticidade_minima: Optional[str] = None,
    tenant_id: str = Depends(get_tenant_id),
) -> JSONResponse:
    """O ranking de clientes em risco da empresa autenticada — upload ∪ SDK.

    Cada linha traz `origem: "upload" | "sdk"` e `atualizado_em`; quando o
    mesmo cliente está nas duas origens, aparece UMA vez, com o dado mais
    recente. `?limite=N` corta a lista; `?criticidade_minima=alto|critico`
    filtra (`dado_insuficiente` nunca passa por esse filtro).
    `total_clientes` é o total ANTES dos filtros.
    """
    limite, crit = _filtros_de_insights(limite, criticidade_minima)
    return JSONResponse(_insights_do_tenant(tenant_id, limite, crit))


@app.post("/insights/enviar")
async def enviar_insights(
    limite: Optional[int] = None,
    criticidade_minima: Optional[str] = None,
    conta: dict = Depends(get_conta),
) -> JSONResponse:
    """Manda o resumo do ranking para o e-mail DA CONTA AUTENTICADA.

    O destinatário é o `email` do token do Supabase, e só ele: uma empresa
    logada não escolhe para quem a CRAI escreve. Sem SMTP configurado, o
    envio é simulado (logado) e a resposta diz `simulado: true` — mesmo
    padrão do WhatsApp e do HubSpot.
    """
    if not conta.get("email"):
        raise HTTPException(status_code=422, detail={
            "motivo": "conta_sem_email",
            "detalhe": "o token não traz `email`; o destinatário é sempre o "
                       "e-mail da conta autenticada"})
    limite, crit = _filtros_de_insights(limite, criticidade_minima)
    dados = _insights_do_tenant(conta["tenant_id"], limite, crit)
    resultado = email_sender.send_insights_email(
        conta["tenant_id"], dados["clientes_em_risco"], conta["email"],
        total_clientes=dados["total_clientes"])
    status = 200 if resultado["enviado"] else 502
    return JSONResponse({**resultado, "total_clientes": dados["total_clientes"],
                         "gerado_em": dados["gerado_em"]}, status_code=status)


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


# Um tenant é identificador de empresa cliente, não texto livre. O conjunto é
# restrito de propósito: o valor vira CHAVE em três lugares — o dicionário do
# bandit, o `_channel_history` (`f"{tenant}:{user}"`) e a coluna do dataset de
# treino. Um `:` no tenant tornaria a chave de canal ambígua; um valor gigante
# ou com caractere de controle vira chave de JSON persistido. Slug e UUID, que
# é o que um tenant realmente é, cabem folgados aqui.
_TENANT_VALIDO = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TENANT_HEADER = "x-tenant-id"


def _tenant_da_requisicao(request: Request, corpo: dict, origem: str) -> str:
    """O tenant do evento: header `x-tenant-id`, senão `tenant_id` no corpo.

    AUSENTE CAI EM `default_tenant`, e isso é MVP declarado: a CRAI ainda é
    operada para um cliente por instalação, e exigir o campo quebraria os
    webhooks já integrados. O dia em que houver dois clientes na mesma
    instalação, esta é a linha que vira obrigatória.

    DECLARADO EXPLICITAMENTE E TORTO É 422, não `default_tenant`. Cair no
    default silenciosamente misturaria o aprendizado de quem tentou se
    identificar com o de todo mundo que não se identificou — exatamente o
    vazamento que a camada de tenant existe para impedir. Errar alto é a única
    leitura correta aqui.

    O literal `default_tenant` também é recusado vindo de fora: é o balde do
    "não declarado", e ninguém deve poder entrar nele de propósito.
    """
    bruto = request.headers.get(TENANT_HEADER)
    if bruto is None:
        bruto = _campo_com_forma(corpo, "tenant_id", (str,), origem)
    if bruto is None:
        return TENANT_PADRAO

    tenant = bruto.strip()
    if tenant == TENANT_PADRAO:
        logger.warning("[%s] tenant_id=%r é reservado — 422", origem, tenant)
        raise HTTPException(status_code=422, detail={
            "motivo": "tenant_reservado", "campo": "tenant_id",
            "detalhe": (f"{TENANT_PADRAO!r} é o balde de quem não declara "
                        "tenant; use o identificador real da empresa"),
        })
    if not _TENANT_VALIDO.match(tenant):
        logger.warning("[%s] tenant_id=%r fora do formato aceito — 422", origem, tenant)
        raise HTTPException(status_code=422, detail={
            "motivo": "tenant_com_forma_invalida", "campo": "tenant_id",
            "detalhe": "esperado 1-64 caracteres em [A-Za-z0-9._-]",
        })
    return tenant


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


def _registrar_cartao_desativado(event: dict, tenant_id: str = TENANT_PADRAO) -> dict:
    """Registra uma falha de cartão sem acionar recobrança automática.

    O prefixo [CARTAO-DESATIVADO] existe para deixar explícito, no log e na
    demo, que o evento chegou e foi reconhecido — o que não aconteceu foi a
    retentativa, que aguarda a reimplementação descrita no roadmap da Fase 3.

    `tenant_id` entra no registro mesmo com o cartão fora do pipeline (Sprint
    4): a falha é evento de negócio de ALGUÉM, e um log sem dono é um log que
    não serve de evidência quando dois clientes dividem a instalação.
    """
    dados = _dados_stripe(event)
    dados["tenant_id"] = tenant_id
    aviso = (f"[CARTAO-DESATIVADO] {dados['customer_id']} | fatura "
             f"{dados['invoice_id']} | R$ {dados['amount']:.2f} | tenant "
             f"{tenant_id} — evento registrado, recobrança automática de cartão "
             f"fora do pipeline ativo (aguardando reimplementação; ver "
             f"crai/dunning/legacy_card/)")
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


async def _confirmar_cobranca_paga(evento: dict, tenant_id: str = TENANT_PADRAO) -> JSONResponse:
    """Trata o evento de cobrança PAGA vindo do PSP (Pagar.me).

    Nem toda cobrança paga é uma recuperação, e essa distinção é o coração
    deste handler. O Pix Automático cobra todo mês; a esmagadora maioria das
    confirmações é a mensalidade que deu certo de primeira, e contar success
    fee sobre elas transformaria a receita da CRAI em percentual do faturamento
    do cliente. Só é recuperação a confirmação que fecha um ciclo que a CRAI
    abriu — e quem sabe se há ciclo aberto é o checkpoint do `thread_id`.
    """
    customer_id = _thread_id(evento)
    if customer_id is None:
        # 200, não 422: uma confirmação é informativa. Recusá-la faria o PSP
        # retentar indefinidamente um evento sobre o qual não há nada a fazer.
        logger.info("[PIX] Cobrança confirmada sem identificação — registrada "
                    "sem fechar ciclo.")
        return JSONResponse({"status": "ok", "evento": evento["status"],
                             "pipeline": False, "ciclo": "sem_identificacao"})

    resultado = await _fechar_ciclo_recuperado(
        customer_id=customer_id,
        e2e_id=evento["e2e_id"] or "e2e_desconhecido",
        valor=evento.get("valor") or None,
        tenant_id=tenant_id,
    )
    return JSONResponse({"status": "ok", "evento": evento["status"],
                         "pipeline": False, **resultado})


async def _fechar_ciclo_recuperado(
    customer_id: str,
    e2e_id: str,
    valor: Optional[float] = None,
    tenant_id: str = TENANT_PADRAO,
) -> dict:
    """Marca `recovered=True` e fecha o ciclo: ROI com fee + estágio no CRM.

    POR QUE UMA FUNÇÃO DEDICADA, E NÃO UM `ainvoke` A MAIS. Reprocessar o grafo
    na confirmação seria ativamente errado, não apenas caro:

      - `diagnose_failure` rodaria o ensemble sobre um evento que não é falha;
      - `schedule_retry_pix` agendaria MAIS tentativas na janela do BACEN para
        uma cobrança que **acabou de ser paga** — gastando tentativas
        regulatórias contra o próprio cliente;
      - `trigger_dunning` mandaria mensagem de cobrança a quem já pagou.

    O caminho certo é o inverso: **ler** o checkpoint (que guarda o diagnóstico
    do ciclo aberto), gravar nele o desfecho e reaproveitar o nó de fechamento
    (`update_roi_dashboard`) fora do grafo. É o mesmo nó, o mesmo log `[ROI]` e
    o mesmo `register_recovery_cycle` que o caminho perdido usa — sem
    reexecutar nenhuma decisão.

    Returns:
        dict com `ciclo` ∈ {recuperado, reenvio, sem_ciclo_aberto,
        ja_recuperado} e o `fee` efetivamente contado (0.0 quando não houve).
    """
    # (a) Idempotência da confirmação: o mesmo e2e_id não conta fee duas vezes.
    if not CICLOS_FECHADOS.registrar_se_novo(chave_do_evento(customer_id, e2e_id)):
        logger.info("[PIX] Confirmação reenviada (%s / %s) — fee não recontado.",
                    customer_id, e2e_id[:16])
        return {"ciclo": "reenvio", "fee": 0.0}

    config = {"configurable": {"thread_id": customer_id}}

    # A trava do cliente cobre a leitura e a escrita do checkpoint pelo mesmo
    # motivo que cobre o `ainvoke`: uma confirmação chegando junto com uma
    # falha do mesmo `thread_id` intercalaria leitura e gravação.
    async with _trava_do_cliente(customer_id):
        snapshot = await crai_agent.aget_state(config)
        estado = dict(getattr(snapshot, "values", None) or {})

        # (b) Pagamento que nunca falhou: mensalidade normal, não recuperação.
        if not estado or estado.get("failure_cause") is None:
            logger.info("[PIX] Cobrança confirmada para %s sem ciclo de "
                        "recuperação aberto — mensalidade normal, sem fee.",
                        customer_id)
            return {"ciclo": "sem_ciclo_aberto", "fee": 0.0}

        # (c) Ciclo já fechado por outra entrega (e2e diferente, mesmo ciclo).
        if estado.get("recovered"):
            logger.info("[PIX] Ciclo de %s já constava como recuperado — "
                        "confirmação registrada sem novo fee.", customer_id)
            return {"ciclo": "ja_recuperado", "fee": 0.0}

        # O valor autoritativo é o da cobrança que FALHOU e abriu o ciclo: é
        # sobre ele que o fee é calculado. O valor do evento de confirmação
        # entra só como fallback, para o caso de um checkpoint sem `amount`.
        estado["amount"] = estado.get("amount") or valor or 0.0
        estado["recovered"] = True

        # O tenant AUTORITATIVO é o do ciclo, não o do evento de confirmação:
        # quem rodou a recuperação foi aquele, e é a ele que o resultado é
        # atribuído. Um evento que declare outro tenant não pode reatribuir uma
        # recuperação alheia — mas a divergência fica registrada, porque ou é
        # erro de integração do cliente ou é tentativa de atribuição indevida,
        # e as duas precisam ser vistas.
        tenant_do_ciclo = estado.get("tenant_id") or tenant_id
        if tenant_id != TENANT_PADRAO and tenant_id != tenant_do_ciclo:
            logger.warning(
                "[PIX] Confirmação de %s declarou tenant %r, mas o ciclo foi "
                "aberto por %r — atribuindo ao tenant do ciclo.",
                customer_id, tenant_id, tenant_do_ciclo,
            )
        estado["tenant_id"] = tenant_do_ciclo

        await crai_agent.aupdate_state(config, {"recovered": True})

        print(f"[PIX] Cobrança confirmada — ciclo de recuperação de "
              f"{customer_id} fechado com sucesso (e2e {e2e_id[:16]})")
        # ORDEM IMPORTA, e custou um teste para aparecer. `registrar_recuperacao`
        # fecha a linha ABERTA do ciclo — procura por `recovered = 0`. O
        # `update_roi_dashboard` grava a linha do dataset e, com o state já
        # marcado como recuperado, levaria `recovered` a 1 sem gravar fee nem
        # desfecho (esses três campos são preservados na regravação de
        # propósito, para que uma reentrega do webhook não desfaça um
        # desfecho). Rodando o dashboard primeiro, o UPDATE seguinte não
        # encontrava mais nenhuma linha aberta e a recuperação ficava
        # registrada com `success_fee = 0`.
        recovery_log.registrar_recuperacao(
            customer_id=customer_id,
            e2e_id=e2e_id,
            amount=estado["amount"],
            tenant_id=estado["tenant_id"],
            tentativas_usadas=tentativas_ja_disparadas(estado),
            dunning_enviado=bool(estado.get("dunning_sent")),
        )

        # Mesmo nó de fechamento do caminho perdido: [ROI] + HubSpot + a
        # regravação da linha com o diagnóstico atual.
        await update_roi_dashboard(estado)

    return {"ciclo": "recuperado", "fee": success_fee(estado["amount"], True),
            "tenant_id": estado["tenant_id"]}


async def _run_involuntary_pipeline(
    event: dict,
    payment_method: PaymentMethod,
    customer_id: str,
    amount: float,
    invoice_id: str,
    retries_done: Optional[int] = None,
    tenant_id: str = TENANT_PADRAO,
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
        "tenant_id": tenant_id,
        "customer_id": customer_id, "invoice_id": invoice_id, "amount": amount,
        "failure_cause": None, "recovery_score": None, "p_recovery": None,
        "eprofit": None, "recommend_action": None, "ltv_estimated": None,
        "shap_explanation": None, "feature_importance": None,
        "features": None,
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


# Prefixos das duas origens de identidade do churn voluntário. Têm o mesmo
# comprimento e diferem no primeiro byte, o que torna o mapeamento
# `(origem, valor) -> identidade` INJETIVO: `"user:" + A` nunca é igual a
# `"anon:" + B`, qualquer que seja o conteúdo. É a propriedade que um prefixo
# em um lado só não tem — `anon:` sozinho deixava `userId="anon:v"` colidir com
# `anonymousId="v"`, e essa é a direção pior, porque o `anonymousId` é o campo
# que o visitante escolhe.
PREFIXO_IDENTIFICADO = "user:"
PREFIXO_ANONIMO = "anon:"


def _identidade_voluntaria(campo: str, valor: str) -> str:
    """A identidade de um evento de churn voluntário. UMA, usada em todo lugar.

    A tentativa anterior tinha DOIS conceitos: `user_id` (identidade do
    negócio, crua) e `thread_id` (chave do checkpoint, desambiguada). A ideia
    era não sujar o nome do contato no CRM com um prefixo. O efeito medido foi
    o oposto do pretendido: a separação chegava ao `MemorySaver` e não chegava
    a `_channel_history` nem ao HubSpot, que leem `state["user_id"]`. A colisão
    não foi fechada — foi MOVIDA, para dois lugares onde antes não existia:

        visitante anônimo 'vitima' converte por popup
        cliente identificado 'vitima', FORA do site
        -> recebia popup, porque herdou o histórico de canal do anônimo
        -> e os dois viravam o mesmo contato e o mesmo deal no HubSpot

    `_channel_history` decide o canal de saída: um id que já converteu antes
    tem o canal anterior preferido sobre `on_site_now` e sobre o score de
    risco. Contaminar essa chave manda a oferta de retenção por um canal que
    não alcança o cliente — e a CRAI registra `retained` sobre uma mensagem
    que ninguém viu.

    Por isso volta a existir uma identidade só. O custo é o prefixo aparecer no
    contato do HubSpot, e ele é o preço certo: um visitante anônimo e um
    cliente identificado são duas entidades, e fundi-las no CRM é pior que um
    nome feio. Está declarado no README.
    """
    prefixo = PREFIXO_IDENTIFICADO if campo == "userId" else PREFIXO_ANONIMO
    return prefixo + valor


async def _run_voluntary_pipeline(user_id: str, event: str, props: dict,
                                  tenant_id: str = TENANT_PADRAO):
    initial: ChurnVoluntaryState = {
        "tenant_id": tenant_id, "user_id": user_id, "event": event, "props": props,
        "risk_score": 0.0, "profile": "CLT", "criticality": "padrao", "offer_type": None,
        "channel": None, "on_site_now": props.get("on_site_now", False),
        "prior_channel_success": None, "message": None,
        "offer_sent": False, "accepted": None, "retained": False, "is_critical": False,
    }
    # A identidade JÁ chega qualificada (ver `_identidade_voluntaria`), e é a
    # mesma coisa em todo lugar: no checkpoint, em `_channel_history` e no
    # HubSpot. Ter duas formas da identidade foi exatamente o defeito que a
    # A1-r10 mediu — a desambiguação chegava a um dos três consumidores.
    #
    # O TENANT entra no `thread_id` pelo mesmo motivo: dois clientes de empresas
    # diferentes podem ter o mesmo `user_id`, e sem o prefixo dividiriam o
    # checkpoint. É o P0-6 um nível acima.
    config = {"configurable": {"thread_id": f"{tenant_id}:{user_id}"}}
    # `agente_do_modo()` e não um agente fixo: o grafo de produção termina no
    # envio e o de simulação passa por `track_outcome`. Ver o docstring de
    # `voluntary_agent` — são topologias diferentes, escolhidas por
    # `CRAI_SIMULATE_OUTCOMES`.
    await agente_do_modo().ainvoke(initial, config)


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


# ---------------------------------------------------------------------------
# PAINEL DE AVALIACAO (`GET /painel`)
#
# Console que exercita esta mesma API a partir do navegador: cada botao chama
# uma das rotas abaixo, que rodam os grafos e os modulos REAIS e devolvem o
# estado final. Existe porque `/docs` responde JSON cru, e quem precisa avaliar
# o sistema -- uma banca, uma integracao sendo considerada -- nao deveria ter
# de ler JSON para ver o raciocinio do agente.
#
# Tudo aqui passa por `_require_simulation_env()`: so responde com
# ENV=development ou ENV=demo, igual ao resto de `/simulate/*`. Nenhuma destas
# rotas usa `get_tenant_id` -- o self-service autenticado continua sendo
# `/clientes/importar` e `/insights`, e a catraca em
# tests/test_supabase_auth.py garante que so aquelas duas exigem o JWT.
# ---------------------------------------------------------------------------


# CONVENIENCIA DE DESENVOLVIMENTO, e so dela: em ENV=development|demo, se
# NENHUM destino de base foi declarado, aponta para um SQLite local. Sem isto,
# quem clona o repositorio e sobe a API sem configurar nada recebe 500
# "base_nao_configurada" no painel -- e quem copia o `.env.example` sem editar
# recebe pior: o placeholder de `SUPABASE_DB_URL` aponta para um host que nao
# existe, o Postgres vence o SQLite e a importacao fica pendurada ate o
# timeout de conexao. Em producao nada disso acontece: `_destino()` continua
# falhando alto, que e o comportamento correto la.
def _destino_de_desenvolvimento() -> None:
    env = os.getenv("ENV", "production").strip().lower()
    if env not in SIMULATION_ENVS:
        return
    url = (os.getenv("SUPABASE_DB_URL") or "").strip()
    if url and "abcdefgh" not in url:          # placeholder do .env.example
        return
    if url:
        logger.warning("[PAINEL] SUPABASE_DB_URL ainda e o placeholder do "
                       ".env.example; ignorando e usando SQLite local.")
        os.environ.pop("SUPABASE_DB_URL", None)
    if not (os.getenv("CRAI_CLIENTES_DB") or "").strip():
        caminho = str(Path(__file__).resolve().parent.parent.parent / "clientes_dev.db")
        os.environ["CRAI_CLIENTES_DB"] = caminho
        logger.info("[PAINEL] base de clientes em SQLite local: %s", caminho)


_destino_de_desenvolvimento()

TENANT_PAINEL = "painel_avaliacao"

# Mesmo dicionario de crai/scripts/relatorio.py (ver o comentario acima de
# onde e usado): traduz o nome INTERNO da feature para o rotulo que aparece
# na decomposicao SHAP quando o texto `readable` do classificador nao cobre
# aquela feature.
ROTULO_FEATURE_PAINEL = {
    "tenure_months": "Tempo de casa (meses)", "payment_history_score": "Historico de pagamento",
    "gateway_error_code": "Codigo de erro", "invoice_amount": "Valor da fatura R$",
    "avg_ticket": "Ticket medio R$", "day_of_month": "Dia do mes",
    "hour_of_day": "Hora da cobranca", "day_of_week": "Dia da semana",
    "failure_count_90d": "Falhas (90 dias)", "attempt_count": "Tentativas anteriores",
    "card_brand": "Bandeira do cartao", "metodo_pagamento": "Metodo de pagamento",
    "ltv_estimated": "LTV estimado R$",
}

# CAUSA_LEGIVEL vem de agent/pix_codes.py -- e o mesmo rotulo que a trilha de
# raciocinio do agente ja usa, para o painel nao inventar um segundo
# vocabulario de traducao para a mesma causa.
ESTRATEGIA_LEGIVEL_PAINEL = {
    "retry_automatico":    "Nova tentativa automatica",
    "mensagem_pagamento":  "Mensagem de pagamento enviada ao cliente",
}

BASE_EXEMPLO_PAINEL = (
    "customer_id_externo,mrr,billing_profile,days_since_last,features_used_30d,email\n"
    "ACME-2291,890,PJ,47,1,financeiro@acme-exemplo.com.br\n"
    "Vertice-0834,1450,CLT,38,2,contato@vertice-exemplo.com.br\n"
    "NovaLog-7712,2680,PJ,12,6,ops@novalog-exemplo.com.br\n"
    "Ipe-4408,640,freelancer,29,2,ana@ipe-exemplo.com.br\n"
    "Solaris-1190,3200,CLT,4,11,admin@solaris-exemplo.com.br\n"
    "Kaeta-6621,410,freelancer,26,3,kaeta@exemplo.com.br\n"
    "Ribalta-3345,1180,PJ,18,4,ti@ribalta-exemplo.com.br\n"
    "Orbita-9080,750,CLT,9,8,suporte@orbita-exemplo.com.br\n"
    "Palma-5517,1620,PJ,2,14,diretoria@palma-exemplo.com.br\n"
    "Trilha-2204,520,freelancer,0,19,oi@trilha-exemplo.com.br\n"
    "Corvo-8863,980,CLT,6,9,financeiro@corvo-exemplo.com.br\n"
    "Marena-7351,1340,PJ,1,16,contato@marena-exemplo.com.br\n"
    "Aurora-1128,1750,CLT,,,fin@aurora-exemplo.com.br\n"
    "Bandeira-4490,860,PJ,,,contato@bandeira-exemplo.com.br\n"
)

OFERTA_LEGIVEL = {"desconto_10": "Oferta: desconto de 10%",
                  "desconto_20": "Oferta: desconto de 20%",
                  "pausa_1_mes": "Oferta: pausa de 1 mes",
                  "pix_boleto_flash": "Oferta: Pix / Boleto Flash"}




# O painel novo mora em `painel/` na raiz do repositorio (index.html,
# estilo.css, idioma.js, api.js, render.js, img/, e as fixtures e os CSVs de
# exemplo que a tela consome). E servido como estatico em /painel/v2 enquanto a
# pagina antiga em GET /painel continua no ar; quando ela for aposentada, este
# mount passa para /painel. Um mount nao passa pelas dependencies de rota, entao
# a trava de ambiente entra no proprio ASGI: fora de ENV=development|demo, 403
# com o mesmo detalhe de `_require_simulation_env()`.
from fastapi.staticfiles import StaticFiles                      # noqa: E402

PASTA_PAINEL = Path(__file__).resolve().parents[3] / "painel"


class _EstaticosDoPainel(StaticFiles):
    """StaticFiles atras de `_require_simulation_env()`."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            try:
                _require_simulation_env()
            except HTTPException as e:
                resposta = JSONResponse({"detail": e.detail},
                                        status_code=e.status_code)
                await resposta(scope, receive, send)
                return
        await super().__call__(scope, receive, send)


app.mount("/painel/v2",
          _EstaticosDoPainel(directory=PASTA_PAINEL, html=True),
          name="painel_v2")


@app.get("/simulate/painel/ambiente")
async def painel_ambiente():
    """O que esta ligado nesta execucao -- o painel mostra no cabecalho para
    nunca dar a entender que rodou com mais do que realmente tinha."""
    _require_simulation_env()
    from ..ml.failure_classifier import FailureClassifier
    return JSONResponse({
        "env": os.getenv("ENV", "production"),
        "modelos": bool(FailureClassifier().load()),
        "llm": bool(os.getenv("ANTHROPIC_API_KEY")),
    })


class PainelCobranca(BaseModel):
    valor: float = 299.90
    codigo_falha: str = "AM04"
    tentativas_usadas: int = 0
    # Identificador ESTAVEL do cliente de exemplo, opcional.
    #
    # Sem ele, `customer_id` era o id da recorrencia -- novo a cada chamada. E
    # `perfil_provider.get_perfil` deriva o perfil do pagador de
    # `seed_por_cliente(customer_id)`, um md5 estavel: cliente novo a cada
    # clique significava historico de pagamento, tempo de casa e falhas em 90
    # dias DIFERENTES para a mesma cobranca. Com este campo, o mesmo cliente
    # devolve sempre o mesmo perfil -- que e a reprodutibilidade que o provedor
    # sempre prometeu, e que a demonstracao precisa.
    #
    # Nao mexe na janela do BACEN: `id_recorrencia` e `thread_id` continuam
    # unicos por chamada, entao cada cobranca comeca com o contador limpo.
    cliente: str | None = None


@app.post("/simulate/painel/cobranca-falhada")
async def painel_cobranca_falhada(payload: PainelCobranca):
    """Roda o grafo do churn involuntario e devolve o estado final legivel.

    Difere de `/simulate/pix-falhado` num ponto so: aquele devolve o status do
    disparo (e o detalhe fica no log), este devolve a DECISAO -- causa, score,
    e-Profit, SHAP, raciocinio e plano de retentativa -- porque a pagina
    precisa exibir isso sem ninguem abrir o terminal.
    """
    _require_simulation_env()
    valor = _valor_de_simulacao(payload.valor, "valor")
    id_rec = f"RN_painel_{int(datetime.now(timezone.utc).timestamp())}"
    # So letras, digitos, hifen e sublinhado, no maximo 48 caracteres: o valor
    # vem do navegador e vira chave de perfil e de log.
    apelido = re.sub(r"[^A-Za-z0-9_-]", "", (payload.cliente or ""))[:48]
    id_cliente = f"CLI_painel_{apelido}" if apelido else id_rec
    evento = {"e2e_id": f"E60701190{id_rec}", "valor": valor,
              "status": "cobranca_falhada", "ispb_pagador": "60701190",
              "id_recorrencia": id_rec, "codigo_falha": payload.codigo_falha}
    inicial: AgentState = {
        "payment_event": evento, "payment_method": "pix_automatico",
        "tenant_id": TENANT_PAINEL, "customer_id": id_cliente,
        "invoice_id": evento["e2e_id"], "amount": valor, "features": None,
        "failure_cause": None, "recovery_score": None, "p_recovery": None,
        "eprofit": None, "recommend_action": None, "ltv_estimated": None,
        "shap_explanation": None, "feature_importance": None,
        "is_anomalous": None, "reconstruction_error": None,
        "anomaly_explanation": None, "optimal_retry_at": None,
        "estrategia": None, "raciocinio": None, "confidence": None,
        "profile_type": None, "retry_count": max(0, min(3, payload.tentativas_usadas)),
        "next_retry_at": None, "retry_exhausted": False, "recovered": False,
        "pix_retry_schedule": None, "dunning_sent": False, "channel": None,
        "canais_considerados": None, "metodo_pagamento": None,
        "message_sent": None,
    }
    final = await crai_agent.ainvoke(inicial, {"configurable": {"thread_id": id_rec}})

    shap_bruto = final.get("shap_explanation") or {}
    feats = shap_bruto.get("features") or []
    rotulos = [p.strip() for p in str(shap_bruto.get("readable", "")).split("|") if p.strip()]
    # `readable` (gerado pelo classificador) so cobre as features mais
    # relevantes; o resto caia no nome interno em ingles (day_of_week,
    # attempt_count...) sem traducao nenhuma. ROTULO_FEATURE_PAINEL e o
    # mesmo dicionario que crai/scripts/relatorio.py ja usa para o mesmo
    # problema -- um so vocabulario de traducao para os dois lugares que
    # mostram SHAP a um humano.
    shap = [{**f, "rotulo": (rotulos[i].rsplit("(", 1)[0].strip() if i < len(rotulos)
                             else f"{ROTULO_FEATURE_PAINEL.get(f.get('feature'), f.get('feature'))} "
                                  f"{f.get('value')}")}
            for i, f in enumerate(feats)]

    # O plano sai em DUAS formas, de proposito:
    #
    #   `plano`       -- as frases prontas, como sempre saiu. Quem ja consumia
    #                    continua funcionando.
    #   `plano_itens` -- os campos crus (numero, instante em ISO, valor). Data e
    #                    moeda sao FORMATACAO, nao explicacao: quem exibe e que
    #                    sabe o idioma e o formato do leitor. A frase pronta em
    #                    pt-BR deixava "17/09/2026 as 17:17 - R$ 489.0" no painel
    #                    em ingles, com o centavo comido.
    plano = []
    plano_itens = []
    for t in (final.get("pix_retry_schedule") or []):
        if isinstance(t, dict):
            quando = t.get("quando")
            iso = quando.isoformat() if hasattr(quando, "isoformat") else quando
            texto = quando.strftime("%d/%m/%Y as %H:%M") if hasattr(quando, "strftime") else quando
            try:
                valor_item = float(t.get("valor"))
            except (TypeError, ValueError):
                valor_item = None
            plano.append(f"tentativa {t.get('numero')} - {texto} - R$ {t.get('valor')}")
            plano_itens.append({"numero": t.get("numero"), "quando": iso,
                                "valor": valor_item})
        else:
            plano.append(str(t))
            plano_itens.append({"numero": None, "quando": None, "valor": None,
                                "texto": str(t)})

    racio = final.get("raciocinio")
    causa_bruta = final.get("failure_cause")
    estrategia_bruta = final.get("estrategia")
    return JSONResponse({
        # Valor cru mantido (uso interno/depuracao) + versao traduzida para
        # o painel exibir -- nenhum dos dois campos muda o que o agente
        # decidiu, so como isso aparece na tela.
        "failure_cause": causa_bruta,
        "failure_cause_legivel": CAUSA_LEGIVEL.get(causa_bruta, causa_bruta),
        "recovery_score": final.get("recovery_score"),
        "eprofit": final.get("eprofit"),
        "estrategia": estrategia_bruta,
        "estrategia_legivel": ESTRATEGIA_LEGIVEL_PAINEL.get(estrategia_bruta, estrategia_bruta),
        "shap": shap, "plano": plano, "plano_itens": plano_itens,
        "raciocinio": racio if isinstance(racio, list) else ([racio] if racio else []),
        "mensagem": final.get("message_sent"),
        # Como a mensagem foi escrita (modelo + parametros, ou gerada). O
        # painel usa para exibi-la no idioma do leitor quando veio de modelo.
        "mensagem_meta": final.get("mensagem_meta"),
        "channel": final.get("channel"),
        "canais_considerados": final.get("canais_considerados") or [],
    })


class PainelEvento(BaseModel):
    event: str = "Cancellation Page Viewed"
    days_since_last: float = 21
    features_used_30d: float = 2
    mrr: float = 1200
    billing_profile: str = "CLT"
    # Os dois dados que decidem o canal. Antes a rota fixava `on_site_now`
    # em True e não mandava telefone — e o canal saía sempre "popup", o que
    # fazia o painel parecer que a escolha não existia. O padrão continua
    # sendo "no site, sem telefone" para quem não enviar nada.
    phone: str | None = None
    on_site_now: bool = True


@app.post("/simulate/painel/evento-risco")
async def painel_evento_risco(payload: PainelEvento):
    """Roda o grafo do churn voluntario e devolve a decisao final.

    Mesma diferenca de `/simulate/churn-risk`: aquele confirma o disparo, este
    devolve risco, criticidade, oferta escolhida, canal e mensagem.
    """
    _require_simulation_env()
    user_id = f"painel_{int(datetime.now(timezone.utc).timestamp())}"
    props = {"days_since_last": _contador_de_simulacao(payload.days_since_last,
                                                      "days_since_last"),
             "features_used_30d": _contador_de_simulacao(payload.features_used_30d,
                                                         "features_used_30d"),
             "mrr": payload.mrr, "billing_profile": payload.billing_profile,
             "on_site_now": payload.on_site_now}
    if payload.phone:
        props["phone"] = payload.phone
    inicial: ChurnVoluntaryState = {
        "tenant_id": TENANT_PAINEL, "user_id": f"user:{user_id}",
        "event": payload.event, "props": props, "risk_score": 0.0,
        "profile": "CLT", "criticality": "padrao", "offer_type": None,
        "ofertas_consideradas": None, "channel": None,
        "on_site_now": payload.on_site_now, "prior_channel_success": None,
        "canais_considerados": None, "message": None, "candidatas": None,
        "offer_sent": False, "accepted": None, "retained": False,
        "is_critical": False,
    }
    final = await agente_do_modo().ainvoke(
        inicial, {"configurable": {"thread_id": f"{TENANT_PAINEL}:{user_id}"}})
    oferta = final.get("offer_type")
    return JSONResponse({
        "risk_score": final.get("risk_score"),
        "criticality": final.get("criticality"),
        "profile": final.get("profile"),
        "offer_type": oferta,
        "offer_label": OFERTA_LEGIVEL.get(oferta or "", "--"),
        "channel": final.get("channel"),
        "canais_considerados": final.get("canais_considerados") or [],
        "message": final.get("message"),
        "candidatas": final.get("candidatas") or [],
        "abordado": oferta is not None,
    })


class PainelDisparoLote(BaseModel):
    # Lista solta de propósito (`dict`, não modelo): uma linha torta vira um
    # item de `pulados` com motivo, e não um 422 que derruba o lote inteiro —
    # o mesmo contrato do `/clientes/importar`. Sem `clientes`, o lote é a
    # base importada do tenant do painel.
    clientes: list[dict] | None = None
    limite: int | None = None
    # Trata só estes `customer_id_externo`, com a régua e o risco da base
    # inteira. Ver `disparo_lote.disparar` para por que isto não é o mesmo que
    # mandar os clientes em `clientes`.
    somente: list[str] | None = None
    # Escreve o texto de cada oferta com a Claude API, nos dois idiomas, em vez
    # de usar o modelo pronto. Exige `somente` com poucos clientes: o custo e
    # (ofertas x idiomas) chamadas POR cliente.
    gerar: bool = False


@app.post("/simulate/painel/disparo-lote")
async def painel_disparo_lote(payload: PainelDisparoLote = None):
    """Trata todos os clientes que precisam: candidatas, escolha, canal e
    registro do envio (simulado), em lote.

    Mesmo padrão das outras rotas do painel: sem JWT, bloqueada fora de
    `ENV=development|demo`. O que entra, o que é pulado e por quê está em
    `churn_voluntary/disparo_lote.py`.
    """
    _require_simulation_env()
    payload = payload or PainelDisparoLote()
    if payload.limite is not None and payload.limite < 1:
        raise HTTPException(status_code=422, detail={
            "motivo": "limite_invalido", "detalhe": "limite precisa ser inteiro >= 1"})
    if payload.gerar and not payload.somente:
        raise HTTPException(status_code=422, detail={
            "motivo": "geracao_exige_somente", "campo": "gerar",
            "detalhe": ("gerar texto com a Claude API custa (ofertas x idiomas) "
                        "chamadas por cliente; peca os clientes em `somente`")})
    if payload.gerar and len(payload.somente) > disparo_lote.LIMITE_GERACAO:
        raise HTTPException(status_code=422, detail={
            "motivo": "geracao_grande_demais", "campo": "somente",
            "detalhe": f"no maximo {disparo_lote.LIMITE_GERACAO} clientes com `gerar`"})
    if payload.somente is not None and len(payload.somente) > disparo_lote.LIMITE_LOTE:
        raise HTTPException(status_code=422, detail={
            "motivo": "somente_grande_demais", "campo": "somente",
            "detalhe": f"no maximo {disparo_lote.LIMITE_LOTE} identificadores"})
    try:
        relatorio = await disparo_lote.disparar(payload.clientes, TENANT_PAINEL,
                                                limite=payload.limite,
                                                somente=payload.somente,
                                                gerar_texto=payload.gerar)
    except disparo_lote.LoteGrandeDemais as e:
        raise HTTPException(status_code=413, detail={
            "motivo": "lote_grande_demais", "detalhe": str(e)})
    except clientes_importados.ConfiguracaoAusente as e:
        raise HTTPException(status_code=500, detail={
            "motivo": "base_nao_configurada", "detalhe": str(e)})
    return JSONResponse(relatorio)


@app.post("/simulate/painel/importar")
async def painel_importar(arquivo: UploadFile = File(None)):
    """Importa a base pelo caminho real (`importacao.importar`), sem o JWT.

    A rota autenticada continua sendo `/clientes/importar`; esta existe para o
    painel exercitar o mesmo codigo quando o projeto Supabase ainda nao foi
    criado. Sem arquivo, usa a base de exemplo.

    SUBSTITUI, nao acumula: como tudo aqui vai para o mesmo `TENANT_PAINEL`,
    a base anterior e apagada antes de importar, e os ciclos de retencao do
    tenant tambem. Sem a primeira limpeza as bases de exemplo se somavam
    (diario + mensal + saudavel = 1.496 clientes) e a "base saudavel" saia
    com dezenas de criticos; sem a segunda, a segunda rodada do disparo em
    lote devolvia todo mundo como `ciclo_aberto` e nao tratava ninguem. So
    aqui: em `/clientes/importar` o upsert acumular e o ciclo aberto valer
    sao o comportamento correto.
    """
    _require_simulation_env()
    if arquivo is not None and arquivo.filename:
        nome, conteudo = arquivo.filename, await arquivo.read()
    else:
        nome, conteudo = "base_exemplo.csv", BASE_EXEMPLO_PAINEL.encode("utf-8")
    try:
        clientes_importados.apagar_tenant(TENANT_PAINEL)
        retention_log.apagar_tenant(TENANT_PAINEL)
        relatorio = importacao.importar(TENANT_PAINEL, nome, conteudo)
    except importacao.ArquivoInvalido as e:
        raise HTTPException(status_code=e.status,
                            detail={"motivo": e.motivo, "detalhe": e.mensagem})
    except clientes_importados.ConfiguracaoAusente as e:
        raise HTTPException(status_code=500, detail={
            "motivo": "base_nao_configurada", "detalhe": str(e)})
    return JSONResponse({"arquivo": nome, **relatorio})


@app.get("/simulate/painel/insights")
async def painel_insights(idioma: str = "pt"):
    """O ranking do tenant do painel -- mesmo `_insights_do_tenant` da rota
    autenticada `/insights`, so que sem o JWT."""
    _require_simulation_env()
    return JSONResponse(_insights_do_tenant(TENANT_PAINEL, None, None, idioma))

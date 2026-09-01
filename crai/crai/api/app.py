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
import hashlib
import logging
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
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

    Três formas de recusar, todas com log (Sprint 1):
      400  corpo que não é JSON
      422  payload que o adapter não sabe normalizar (lote, não-objeto)
      422  cobrança falhada cujo VALOR não pôde ser lido
    """
    raw = await request.body()
    if not verify_pix_automatico_signature(
        raw,
        request.headers.get(PIX_SIGNATURE_HEADER, ""),
        os.getenv("PIX_WEBHOOK_SECRET", ""),
    ):
        raise _reject_unsigned(request, "pix_automatico")

    try:
        # parse_constant fecha a porta para os literais `Infinity`, `-Infinity`
        # e `NaN`, que o JSON do Python aceita por padrão mas o JSON padrão não
        # tem. Um `inf` atravessando o parser reproduzia o P0-1 três nós adiante
        # — o sklearn levanta ValueError e o FastAPI devolve 500.
        corpo = json.loads(raw, parse_constant=_rejeitar_constante_json)
    except json.JSONDecodeError as e:
        logger.warning("[PIX] Corpo do webhook não é JSON válido (%s) — 400", e)
        raise HTTPException(status_code=400, detail="Corpo do webhook não é JSON válido")
    except ValueError as e:
        logger.warning("[PIX] Corpo do webhook contém constante não numérica (%s) — 400", e)
        raise HTTPException(status_code=400,
                            detail="Corpo do webhook contém Infinity/NaN, que não são JSON válido")

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

    event = json.loads(payload)
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
    valor = _para_float(payload.valor)
    if valor is None or valor <= 0:
        logger.warning("[PIX] /simulate/pix-falhado recusado — valor %r não é "
                       "uma cobrança utilizável.", payload.valor)
        raise HTTPException(status_code=422, detail={
            "motivo": MOTIVO_VALOR_NAO_UTILIZAVEL,
            "detalhe": f"valor recebido: {payload.valor!r}",
        })
    valor = round(valor, 2)

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

    payload = json.loads(raw)
    await _run_voluntary_pipeline(
        user_id=payload.get("userId", "usr_unknown"),
        event=payload.get("event", ""),
        props=payload.get("properties", {}),
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
        "days_since_last":   payload.days_since_last,
        "features_used_30d": payload.features_used_30d,
        "on_site_now":       payload.on_site_now,
        "billing_profile":   payload.billing_profile,
    }
    await _run_voluntary_pipeline(payload.user_id, payload.event, props)
    return JSONResponse({"status": "pipeline_executado", "user_id": payload.user_id})


@app.get("/health")
async def health():
    return {"status": "ok", "service": "crai-agent-v2"}


# ── Helpers ────────────────────────────────────────────────────────────────

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


async def _run_involuntary_pipeline(
    event: dict,
    payment_method: PaymentMethod,
    customer_id: str,
    amount: float,
    invoice_id: str,
    retries_done: int = 0,
) -> None:
    """Monta o state inicial e roda o grafo de churn involuntário.

    `payment_method` é preenchido AQUI, na entrada, antes de qualquer nó de
    decisão — é o que permite ao grafo escolher a política de retentativa certa
    sem nunca precisar inferir a origem do evento depois.
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
        "confidence": None, "profile_type": None, "retry_count": retries_done, "next_retry_at": None,
        "retry_exhausted": False, "recovered": False, "pix_retry_schedule": None,
        "dunning_sent": False,
        "channel": None, "metodo_pagamento": None, "message_sent": None,
    }
    config = {"configurable": {"thread_id": customer_id}}
    await crai_agent.ainvoke(initial, config)


async def _run_voluntary_pipeline(user_id: str, event: str, props: dict):
    initial: ChurnVoluntaryState = {
        "user_id": user_id, "event": event, "props": props,
        "risk_score": 0.0, "profile": "CLT", "offer_type": None,
        "channel": None, "on_site_now": props.get("on_site_now", False),
        "prior_channel_success": None, "message": None,
        "offer_sent": False, "accepted": None, "retained": False, "escalated_to_human": False,
    }
    config = {"configurable": {"thread_id": user_id}}
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

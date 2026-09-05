"""
crai/churn_voluntary/voluntary_agent.py
Agente de churn voluntário — LangGraph orquestra todo o fluxo, incluindo
a escolha de canal (decisão dinâmica com memória, não árvore fixa).

Fluxo:
    assess_risk → choose_offer → choose_channel
        → generate_message → send_offer → track_outcome → update_crm
"""

import os
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from anthropic import AsyncAnthropic

from .state import ChurnVoluntaryState
from .risk_scorer import calculate_risk, classify_profile
from .offer_bandit import OfferBandit, is_critical_risk
from ..integrations.hubspot_crm import HubSpotCRM

claude   = AsyncAnthropic()
_bandit  = OfferBandit()
_bandit.load()  # warm start dos posteriores simulados; senão, priors de benchmark
_hubspot = HubSpotCRM()

# Histórico simples de qual canal converteu por cliente (cold start em memória)
_channel_history: dict[str, str] = {}

OFFER_LABELS = {
    "desconto_10": "10% de desconto por 3 meses",
    "desconto_20": "20% de desconto por 3 meses",
    "pausa_1_mes": "pausar a assinatura por 1 mês sem custo",
    "pix_boleto_flash": "trocar para pagamento via Pix ou boleto em 1 clique",
}


# ── Nós do grafo ─────────────────────────────────────────────────────────

async def assess_risk(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    risk = calculate_risk(state["event"], state["props"])
    profile = classify_profile(state["props"])
    print(f"[CHURN-VOL] {state['user_id']} | evento: {state['event']} | risco: {risk:.2f} | perfil: {profile}")
    return {**state, "risk_score": risk, "profile": profile}


async def choose_offer(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    mrr = state["props"].get("mrr")  # quando o evento Segment traz o plano
    offer = _bandit.choose_offer(state["profile"], state["risk_score"], mrr=mrr)
    p_estimado = _bandit.conversion_rates(state["profile"]).get(offer, 0.0)
    print(f"[CHURN-VOL] Oferta escolhida (Thompson Sampling): {offer} "
          f"| P(aceite) posterior: {p_estimado:.1%}")
    return {**state, "offer_type": offer,
            "is_critical": is_critical_risk(state["risk_score"])}


async def choose_channel(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    """
    Roteamento de canal via LangGraph — usa memória de histórico do
    cliente em vez de regra fixa. Se o cliente já converteu em um canal
    antes, prioriza esse canal.
    """
    user_id = state["user_id"]
    on_site = state["props"].get("on_site_now", state.get("on_site_now", False))

    prior = _channel_history.get(user_id)

    if prior:
        channel = prior
        print(f"[CHURN-VOL] Canal por histórico: {channel} (converteu antes)")
    elif on_site:
        channel = "popup"
    elif state["risk_score"] >= 0.90:
        channel = "email"   # alto risco dispara mesmo fora do site
    else:
        channel = "email"

    return {**state, "channel": channel, "on_site_now": on_site}


async def generate_message(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    offer_label = OFFER_LABELS.get(state["offer_type"], "uma oferta especial")
    prompt = f"""Gere uma mensagem curta de retenção para um cliente que demonstrou risco de cancelar.
Evento: {state['event']} | Canal: {state['channel']} | Oferta: {offer_label}
Tom empático, sem culpar o cliente, no máximo 3 frases, português brasileiro natural.
Retorne APENAS a mensagem."""
    try:
        response = await claude.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        message = response.content[0].text.strip()
    except Exception as e:
        print(f"[CHURN-VOL] Claude API indisponível ({e}) — fallback")
        message = f"Antes de você ir, que tal {offer_label}? Estamos aqui para ajudar."
    return {**state, "message": message}


async def send_offer(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    print(f"[CHURN-VOL] Enviando via {state['channel'].upper()}: {state['message'][:90]}")
    return {**state, "offer_sent": True}


async def track_outcome(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    """
    MVP: simula aceite com probabilidade baseada na taxa histórica do bandit.
    Produção: aguarda webhook real de aceite/rejeição do cliente.
    """
    import random
    rates = _bandit.conversion_rates(state["profile"])
    prob  = rates.get(state["offer_type"], 0.3)
    accepted = random.random() < prob

    _bandit.record_outcome(state["profile"], state["offer_type"], accepted)
    if accepted:
        _channel_history[state["user_id"]] = state["channel"]

    print(f"[CHURN-VOL] Resultado: {'✅ ACEITOU' if accepted else '❌ recusou'}")
    return {**state, "accepted": accepted, "retained": accepted}


async def update_crm(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    crm_result = await _hubspot.register_retention_cycle(state)
    print(f"[CHURN-VOL] HubSpot: Contact {crm_result['hubspot_contact_id']} | Deal {crm_result['hubspot_deal_id']} | Stage: {crm_result['stage']}\n")
    return state


# ── Roteamento condicional ──────────────────────────────────────────────

def route_after_risk(state: ChurnVoluntaryState) -> str:
    if state["risk_score"] < 0.60:
        print(f"[CHURN-VOL] Risco baixo ({state['risk_score']:.2f}) — não intervém")
        return "update_crm"
    return "choose_offer"


# ── Construção do grafo ───────────────────────────────────────────────────

def build_voluntary_churn_graph() -> StateGraph:
    graph = StateGraph(ChurnVoluntaryState)

    graph.add_node("assess_risk",      assess_risk)
    graph.add_node("choose_offer",     choose_offer)
    graph.add_node("choose_channel",   choose_channel)
    graph.add_node("generate_message", generate_message)
    graph.add_node("send_offer",       send_offer)
    graph.add_node("track_outcome",    track_outcome)
    graph.add_node("update_crm",       update_crm)

    graph.set_entry_point("assess_risk")

    graph.add_conditional_edges("assess_risk", route_after_risk, {
        "choose_offer": "choose_offer",
        "update_crm":   "update_crm",
    })

    graph.add_edge("choose_offer",     "choose_channel")
    graph.add_edge("choose_channel",   "generate_message")
    graph.add_edge("generate_message", "send_offer")
    graph.add_edge("send_offer",       "track_outcome")
    graph.add_edge("track_outcome",    "update_crm")
    graph.add_edge("update_crm",       END)

    return graph.compile(checkpointer=MemorySaver())


voluntary_churn_agent = build_voluntary_churn_graph()

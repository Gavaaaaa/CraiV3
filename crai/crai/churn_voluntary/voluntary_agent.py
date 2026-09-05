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
from .risk_scorer import (
    calculate_risk,
    classify_criticality,
    classify_profile,
    mrr_utilizavel,
)
from .offer_bandit import OfferBandit, is_critical_risk
from ..integrations.hubspot_crm import HubSpotCRM
from ..integrations.whatsapp_sender import destino_utilizavel, send_whatsapp

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
    criticality = classify_criticality(risk, state["props"].get("mrr"))
    print(f"[CHURN-VOL] {state['user_id']} | evento: {state['event']} | risco: {risk:.2f} "
          f"| perfil: {profile} | criticidade: {criticality}")
    return {**state, "risk_score": risk, "profile": profile, "criticality": criticality}


async def choose_offer(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    # `props` é payload bruto do Segment e `mrr` entra em aritmética dentro do
    # bandit (`MESES_LTV_RETIDO * mrr`, `0.10 * 3 * mrr`). Cru, um `mrr:"2500"`
    # vindo de webhook ASSINADO devolvia HTTP 500 — medido nas três formas
    # ("2500", [2500], {"v":1}). É o P0-5/P0-1 por outra porta: a borda validava
    # `billing_profile` e deixava `mrr` passar. Sem número utilizável, o bandit
    # usa o MRR típico do perfil, que é o que ele já fazia quando o evento não
    # trazia plano nenhum.
    mrr = mrr_utilizavel(state["props"].get("mrr"))
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

    Ordem de decisão, e por que ela é esta:

      1. WHATSAPP, quando há número utilizável E a criticidade é "critico" ou
         "alto". Vem ANTES do histórico de propósito: o histórico diz por onde
         o cliente já converteu, mas quem está prestes a sair (ou paga muito)
         recebe o canal mais pessoal que temos, e não o canal que funcionou
         quando ele estava tranquilo. É uma escolha de produto, não uma
         otimização — e está travada por teste para ficar visível se mudar.
      2. HISTÓRICO, quando o canal lembrado ainda é entregável. Um histórico
         "whatsapp" sem telefone no evento atual é memória de um canal que não
         existe agora: cai para o resto da cadeia em vez de rotear para o vazio.
      3. POPUP, se o cliente está no site agora — a intervenção mais barata e
         imediata.
      4. E-MAIL, o fallback.

    O ramo `risk_score >= 0.90 → email` que existia aqui foi removido: ele e o
    `else` devolviam os dois `"email"`, então a condição nunca decidiu nada. O
    caso que ele queria cobrir — risco alto fora do site — agora é o item 1.
    """
    user_id = state["user_id"]
    on_site = state["props"].get("on_site_now", state.get("on_site_now", False))
    criticality = state.get("criticality", "padrao")
    destino = destino_utilizavel(state["props"].get("phone"))

    prior = _channel_history.get(user_id)

    if destino and criticality in ("critico", "alto"):
        channel = "whatsapp"
        print(f"[CHURN-VOL] Canal por criticidade ({criticality}): whatsapp")
    elif prior and (prior != "whatsapp" or destino):
        channel = prior
        print(f"[CHURN-VOL] Canal por histórico: {channel} (converteu antes)")
    elif on_site:
        channel = "popup"
    else:
        channel = "email"

    return {**state, "channel": channel, "on_site_now": on_site}


def _assinatura() -> str:
    """Nome que assina a mensagem crítica, ou vazio.

    Sem `CRAI_CS_SIGNATURE_NAME` no ambiente, a mensagem sai SEM assinatura. Um
    nome inventado é uma pessoa que não existe assinando uma promessa de
    cuidado — e a primeira resposta do cliente vai procurar por ela.
    """
    return os.getenv("CRAI_CS_SIGNATURE_NAME", "").strip()


def _instrucao_de_assinatura() -> str:
    nome = _assinatura()
    if not nome:
        return ("Não assine com nome de pessoa nenhuma: não há um nome real "
                "configurado e inventar um seria mentir sobre quem fala.")
    return f'Assine na última linha, exatamente assim: "— {nome}, time CRAI".'


def _prompt_de_retencao(state: ChurnVoluntaryState, offer_label: str) -> str:
    """O prompt muda com a criticidade; o resto do nó, não.

    A oferta já foi escolhida pelo bandit e não muda aqui — criticidade é tom,
    não decisão. As três variantes compartilham o mesmo cabeçalho de contexto
    para que a diferença fique no que se pede, não no que se informa.
    """
    contexto = (f"Evento: {state['event']} | Canal: {state['channel']} "
                f"| Oferta: {offer_label}")
    fecho = "Sem culpar o cliente, no máximo 3 frases, português brasileiro natural.\nRetorne APENAS a mensagem."

    criticality = state.get("criticality", "padrao")

    if criticality == "critico":
        return f"""Gere uma mensagem curta de retenção para um cliente em situação CRÍTICA — risco muito alto de cancelar, ou conta de alto valor.
{contexto}
Tom pessoal e de alto cuidado: reconheça o valor da relação, sem bajular e sem prometer o que não foi oferecido.
Apresente a oferta como uma solução pensada para este cliente, não como promoção genérica.
{_instrucao_de_assinatura()}
{fecho}"""

    if criticality == "alto":
        return f"""Gere uma mensagem curta de retenção para um cliente com risco ALTO de cancelar.
{contexto}
Tom atencioso e proativo: deixe claro que percebemos o movimento dele antes de ele precisar pedir.
Apresente a oferta como personalizada e diga que ela vale pelos próximos 7 dias.
{fecho}"""

    # O prompt "padrao" é o texto de antes do Sprint 2, palavra por palavra: o
    # caso comum não podia mudar de comportamento junto com os dois novos.
    return f"""Gere uma mensagem curta de retenção para um cliente que demonstrou risco de cancelar.
{contexto}
Tom empático, sem culpar o cliente, no máximo 3 frases, português brasileiro natural.
Retorne APENAS a mensagem."""


def _fallback_de_retencao(criticality: str, offer_label: str) -> str:
    """Texto de emergência quando a Claude API não responde.

    O cliente crítico é justamente quem não pode receber o texto genérico —
    então o fallback também tem as duas variantes. Ele só não tem como
    personalizar, e por isso não promete nada que dependa de contexto.
    """
    if criticality == "critico":
        base = ("Sua conta é importante para a gente e não queremos te perder. "
                f"Preparamos {offer_label} — responda aqui e a gente resolve junto.")
        nome = _assinatura()
        return f"{base}\n— {nome}, time CRAI" if nome else base

    return f"Antes de você ir, que tal {offer_label}? Estamos aqui para ajudar."


async def generate_message(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    offer_label = OFFER_LABELS.get(state["offer_type"], "uma oferta especial")
    criticality = state.get("criticality", "padrao")
    prompt = _prompt_de_retencao(state, offer_label)
    try:
        response = await claude.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        message = response.content[0].text.strip()
    except Exception as e:
        print(f"[CHURN-VOL] Claude API indisponível ({e}) — fallback ({criticality})")
        message = _fallback_de_retencao(criticality, offer_label)
    return {**state, "message": message}


async def send_offer(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    """WhatsApp sai pelo `whatsapp_sender`; popup e e-mail seguem simulados.

    `tenant_id` já é repassado, ainda que hoje seja sempre `None`: o Sprint 5
    coloca o campo no estado, e o sender é o consumidor final dele. Ligar o fio
    agora custa uma linha e evita que o Sprint 5 precise voltar aqui.
    """
    if state["channel"] == "whatsapp":
        resultado = await send_whatsapp(
            state["props"].get("phone"), state["message"],
            tenant_id=state.get("tenant_id"),
        )
        if not resultado["sent"]:
            # Não derruba o ciclo: a oferta foi decidida e o CRM precisa saber
            # que ela existiu. O que falhou foi a entrega, e isso é o que o log
            # diz — em vez de marcar `offer_sent` por um envio que não houve.
            print(f"[CHURN-VOL] Falha na entrega via WHATSAPP "
                  f"({resultado['motivo']}) — oferta não enviada")
        return {**state, "offer_sent": resultado["sent"]}

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

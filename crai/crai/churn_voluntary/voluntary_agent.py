"""
crai/churn_voluntary/voluntary_agent.py
Agente de churn voluntário — LangGraph orquestra todo o fluxo, incluindo
a escolha de canal (decisão dinâmica com memória, não árvore fixa).

DOIS MODOS, e eles têm TOPOLOGIAS DE GRAFO DIFERENTES. A variável de ambiente
`CRAI_SIMULATE_OUTCOMES` decide qual grafo é construído:

    MODO PRODUÇÃO (env ausente ou "0") — o padrão
        assess_risk → choose_offer → choose_channel
            → generate_message → send_offer → update_crm → END

        O grafo TERMINA no envio. O cliente aceita ou não aceita depois, no
        tempo dele, e isso chega por `POST /webhooks/retention-outcome`, que
        atualiza o bandit e fecha a linha no `retention_log`. Ao fim do grafo
        `accepted` é None e `retained` é False: é "aguardando retorno", não
        "recusou" — a diferença importa para o CRM e para o dataset.

    MODO SIMULAÇÃO (env == "1")
        ... send_offer → track_outcome → update_crm → END

        `track_outcome` sorteia o aceite com `random()` sobre a taxa histórica
        do bandit. Existe para a demo (`test_pipeline.py`) e para os testes
        rodarem ponta a ponta sem webhook externo. NUNCA deve rodar em
        produção: o bandit aprenderia com dado inventado, que é exatamente o
        problema que o Sprint 4 veio resolver.

Por que a topologia muda em vez de um `if` dentro do nó: um `track_outcome`
que às vezes não faz nada continua sendo um nó no caminho, e o desenho do
grafo — que é o que se lê para entender o sistema — mentiria sobre onde o
fluxo termina.
"""

import os
import random
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from anthropic import AsyncAnthropic

from .state import ChurnVoluntaryState
from .retention_log import (
    TENANT_PADRAO,
    ciclo_aberto,
    registrar_ciclo,
    registrar_desfecho,
)
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

# Histórico simples de qual canal converteu por cliente (cold start em memória).
# A chave é COMPOSTA — `f"{tenant_id}:{user_id}"` — desde o Sprint 5: dois
# clientes de empresas diferentes podem ter o mesmo `user_id`, e a memória de
# canal de um decidiria o envio do outro. É o mesmo raciocínio do P0-6 no
# checkpoint, um nível acima.
_channel_history: dict[str, str] = {}


def chave_de_canal(tenant_id: str, user_id: str) -> str:
    """A chave do `_channel_history`. Uma função só, para os três consumidores
    (nó de canal, desfecho simulado e desfecho real) não divergirem."""
    return f"{tenant_id or TENANT_PADRAO}:{user_id}"

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
    tenant = state.get("tenant_id") or TENANT_PADRAO
    offer = _bandit.choose_offer(tenant, state["profile"], state["risk_score"], mrr=mrr)
    p_estimado = _bandit.conversion_rates(tenant, state["profile"]).get(offer, 0.0)
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
    on_site = state["props"].get("on_site_now", state.get("on_site_now", False))
    criticality = state.get("criticality", "padrao")
    destino = destino_utilizavel(state["props"].get("phone"))

    prior = _channel_history.get(
        chave_de_canal(state.get("tenant_id"), state["user_id"]))

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
    """SÓ NO MODO SIMULAÇÃO. Sorteia o aceite pela taxa histórica do bandit.

    Este nó não existe no grafo de produção — ver `build_voluntary_churn_graph`.
    Em produção o desfecho é real e chega por webhook; aqui ele é inventado,
    para a demo e os testes rodarem ponta a ponta sem depender de um sistema
    externo. Aprender com sorteio é exatamente o defeito que o Sprint 4 veio
    corrigir, e é por isso que o nó ficou isolado num modo declarado em vez de
    num `if` dentro do caminho de produção.
    """
    tenant = state.get("tenant_id") or TENANT_PADRAO
    rates = _bandit.conversion_rates(tenant, state["profile"])
    prob  = rates.get(state["offer_type"], 0.3)
    accepted = random.random() < prob

    _bandit.record_outcome(tenant, state["profile"], state["offer_type"], accepted)
    if accepted:
        _channel_history[chave_de_canal(tenant, state["user_id"])] = state["channel"]

    print(f"[CHURN-VOL] Resultado: {'✅ ACEITOU' if accepted else '❌ recusou'}")
    return {**state, "accepted": accepted, "retained": accepted}


async def _crm_do_desfecho(user_id: str, ciclo: dict, offer_type: str,
                           accepted: bool) -> None:
    """Atualiza o CRM com o desfecho, reusando `register_retention_cycle`.

    O contexto (`risk_score`, `event`, `channel`) sai da linha do
    `retention_log` — é o primeiro uso prático do dataset: ele guarda o que a
    decisão sabia, e o webhook não carrega isso.

    RESSALVA REGISTRADA, não corrigida: `register_retention_cycle` deriva o
    estágio de `retained`/`offer_sent` e por isso NUNCA produz `churned`, ainda
    que o pipeline `crai_retention` do HubSpot declare esse estágio. Uma recusa
    entra como `offer_sent`. Mexer nisso é mudar o método do CRM, que o plano
    desta sessão declara fora de escopo.

    Falha do CRM NÃO derruba o desfecho: ele já foi contabilizado no bandit e
    no log, e o reenvio do cliente seria deduplicado — retentar não consertaria
    o CRM e ainda devolveria erro para um evento que foi processado.
    """
    estado = {
        "user_id":    user_id,
        "risk_score": ciclo.get("risk_score") or 0.0,
        "event":      ciclo.get("event") or "retention_outcome",
        "offer_type": offer_type,
        "channel":    ciclo.get("channel") or "",
        "offer_sent": True,
        "retained":   accepted,
    }
    try:
        crm = await _hubspot.register_retention_cycle(estado)
        print(f"[CHURN-VOL] HubSpot atualizado — Deal {crm['hubspot_deal_id']} "
              f"| Stage: {crm['stage']}")
    except Exception as e:                        # noqa: BLE001
        print(f"[CHURN-VOL] Falha ao atualizar HubSpot no desfecho "
              f"(já contabilizado): {e}")


async def registrar_resultado_externo(user_id: str, offer_type: str, profile: str,
                                      accepted: bool,
                                      tenant_id: str | None = None) -> dict:
    """Fecha o ciclo com o desfecho REAL, vindo de `/webhooks/retention-outcome`.

    É o que substitui o `track_outcome` no modo produção. Mora aqui, e não na
    API, porque o bandit e o `_channel_history` são estado deste módulo — a
    borda valida a forma do payload, este módulo decide o que fazer com ela.

    A ORDEM IMPORTA e a deduplicação está embutida nela:

      1. Lê o ciclo ABERTO no `retention_log` — é de onde saem `risk_score`,
         `event` e `channel`, que o webhook não carrega e o HubSpot precisa.
      2. Fecha a linha. Se `registrar_desfecho` devolver False, é reenvio ou
         desfecho órfão: NADA é contado. Contar duas vezes o mesmo aceite
         enviesaria o posterior do bandit para quem reenvia mais.
      3. Só então o bandit aprende.

    Devolve o contexto do ciclo para quem chama montar o estado do CRM.
    """
    tenant_id = tenant_id or TENANT_PADRAO
    ciclo = ciclo_aberto(tenant_id, user_id, offer_type)

    if not registrar_desfecho(tenant_id, user_id, offer_type, accepted):
        return {"contabilizado": False, "ciclo": ciclo}

    _bandit.record_outcome(tenant_id, profile, offer_type, accepted)

    canal = (ciclo or {}).get("channel")
    if accepted and canal:
        _channel_history[chave_de_canal(tenant_id, user_id)] = canal

    # Rótulo em ASCII, não os emoji do `track_outcome`: a catraca do N-12
    # (tests/test_encoding_saida.py) diz que o inventário de caracteres que o
    # cp1252 não codifica só pode DESCER. As 2 ocorrências do `track_outcome`
    # são dívida herdada; esta linha é nova e não entra na conta.
    print(f"[CHURN-VOL] Desfecho real de {user_id} | {offer_type}: "
          f"{'ACEITOU' if accepted else 'recusou'}")

    await _crm_do_desfecho(user_id, ciclo or {}, offer_type, accepted)
    return {"contabilizado": True, "ciclo": ciclo}


async def update_crm(state: ChurnVoluntaryState) -> ChurnVoluntaryState:
    crm_result = await _hubspot.register_retention_cycle(state)
    print(f"[CHURN-VOL] HubSpot: Contact {crm_result['hubspot_contact_id']} | Deal {crm_result['hubspot_deal_id']} | Stage: {crm_result['stage']}")

    # A linha do dataset de treino. Escrita AQUI porque `update_crm` é o último
    # nó nos DOIS modos e no caminho de risco baixo.
    #
    # O caminho de risco baixo também grava, com `offer_type` nulo, e isso é
    # deliberado: são os clientes que o sistema decidiu NÃO abordar. Sem eles o
    # dataset só teria quem passou pelo corte de 0.60, e um modelo treinado
    # nisso aprenderia sobre a população que o próprio modelo selecionou. O
    # viés não desaparece — não há desfecho para quem não recebeu oferta —, mas
    # com as linhas gravadas ele é MENSURÁVEL em vez de invisível. Está
    # documentado no README_treino.md.
    registrar_ciclo(state)
    if state.get("offer_type") and state.get("accepted") is None:
        print("[CHURN-VOL] Ciclo registrado — aguardando retorno do cliente")
    print()
    return state


# ── Roteamento condicional ──────────────────────────────────────────────

def route_after_risk(state: ChurnVoluntaryState) -> str:
    if state["risk_score"] < 0.60:
        print(f"[CHURN-VOL] Risco baixo ({state['risk_score']:.2f}) — não intervém")
        return "update_crm"
    return "choose_offer"


# ── Construção do grafo ───────────────────────────────────────────────────

def modo_simulacao() -> bool:
    """`CRAI_SIMULATE_OUTCOMES == "1"` liga o sorteio de desfecho.

    Lido a cada chamada, não congelado no import: `test_pipeline.py` seta a env
    no início do próprio script e os testes precisam construir os dois grafos
    na mesma sessão. Só `"1"` liga — qualquer outra coisa, inclusive `"true"`
    ou vazio, é produção. Um modo que aprende com dado inventado não pode ser
    ligado por engano de digitação.
    """
    return os.getenv("CRAI_SIMULATE_OUTCOMES", "").strip() == "1"


def build_voluntary_churn_graph(simular: bool | None = None) -> StateGraph:
    """Constrói o grafo do modo pedido (default: o que a env disser).

    As duas topologias diferem em UMA aresta e um nó — e essa diferença é o
    Sprint 4 inteiro: em produção o fluxo termina no envio e o desfecho chega
    depois, por webhook.
    """
    if simular is None:
        simular = modo_simulacao()

    graph = StateGraph(ChurnVoluntaryState)

    graph.add_node("assess_risk",      assess_risk)
    graph.add_node("choose_offer",     choose_offer)
    graph.add_node("choose_channel",   choose_channel)
    graph.add_node("generate_message", generate_message)
    graph.add_node("send_offer",       send_offer)
    graph.add_node("update_crm",       update_crm)

    graph.set_entry_point("assess_risk")

    graph.add_conditional_edges("assess_risk", route_after_risk, {
        "choose_offer": "choose_offer",
        "update_crm":   "update_crm",
    })

    graph.add_edge("choose_offer",     "choose_channel")
    graph.add_edge("choose_channel",   "generate_message")
    graph.add_edge("generate_message", "send_offer")

    if simular:
        graph.add_node("track_outcome", track_outcome)
        graph.add_edge("send_offer",    "track_outcome")
        graph.add_edge("track_outcome", "update_crm")
    else:
        # PRODUÇÃO: o grafo acaba aqui. `accepted` fica None e `retained`
        # False — "aguardando retorno", não "recusou". Quem fecha o ciclo é
        # `POST /webhooks/retention-outcome`.
        graph.add_edge("send_offer",    "update_crm")

    graph.add_edge("update_crm",       END)

    return graph.compile(checkpointer=MemorySaver())


# Os DOIS grafos são compilados no import, e o modo é escolhido a cada chamada.
#
# A alternativa — compilar só o grafo da env no import — congela o modo no
# momento em que o módulo é carregado. Isso funciona para um deploy, onde a
# configuração não muda, e quebra para tudo o mais: `test_pipeline.py` teria de
# setar a env antes do primeiro import de qualquer módulo `crai`, e a suíte não
# conseguiria exercitar os dois modos na mesma sessão (o módulo é importado uma
# vez por processo). Compilar os dois custa um objeto a mais em memória e paga
# testabilidade. É o mesmo padrão que `limiar_de_alto_valor()` e `_assinatura()`
# já usam: env lida na hora do uso, não no import.
#
# RESSALVA: cada grafo tem seu próprio `MemorySaver`, então trocar de modo com o
# processo de pé começa um checkpoint novo para o cliente. Irrelevante em
# produção (o modo é fixo por deploy) e declarado aqui para não ser descoberto.
_AGENTES = {
    True:  build_voluntary_churn_graph(simular=True),
    False: build_voluntary_churn_graph(simular=False),
}


def agente_do_modo():
    """O agente compilado do modo que o ambiente pede AGORA."""
    return _AGENTES[modo_simulacao()]


# Mantido para quem importa pelo nome antigo. Prefira `agente_do_modo()`: este
# aponta para o modo que valia no import.
voluntary_churn_agent = _AGENTES[modo_simulacao()]

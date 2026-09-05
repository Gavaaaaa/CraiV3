"""crai/agent/main_agent.py — Orquestrador do agente de churn involuntário.

O único caminho de retentativa automática do pipeline ativo é o de **Pix
Automático**, sob a janela regulada pelo BACEN. A recobrança automática de
cartão saiu do pipeline na Fase 3 e está preservada, isolada, em
`crai/dunning/legacy_card/` — ver o `__init__.py` de lá para o motivo e para o
caminho de reativação.

    decide_recovery ─┬─ payment_method == "pix_automatico" → schedule_retry_pix
                     └─ qualquer outro caso               → trigger_dunning

A aresta condicional continua lendo `payment_method`, e não um `if` dentro de
um nó genérico: é o que garante que só evento de Pix alcança a política de Pix,
e é o ponto onde um nó de cartão volta a ser plugado quando for reimplementado.
"""

import logging

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from .state import AgentState
from .workflow import (
    diagnose_failure, check_anomaly, infer_payday, decide_recovery,
    schedule_retry_pix, trigger_dunning, update_roi_dashboard,
)

logger = logging.getLogger(__name__)


def route_after_diagnosis(state: AgentState) -> str:
    """Decide se vale a pena continuar com base no e-Profit."""
    eprofit = state.get("eprofit", 0)
    recommend = state.get("recommend_action", True)
    score = state.get("recovery_score", 0)

    if not recommend or eprofit <= 0:
        print(f"[ROUTER] e-Profit R$ {eprofit:.2f} <= 0 -- abortando (nao vale intervir)")
        return "update_dashboard"
    if score < 5:
        print(f"[ROUTER] Score {score}/100 muito baixo -- abortando")
        return "update_dashboard"
    return "check_anomaly"


def route_after_decision(state: AgentState) -> str:
    """Decisão do Módulo 5 + isolamento por meio de pagamento (Fase 3).

    Duas perguntas, nesta ordem:
      1. A estratégia é retentar? Se não, vai para a mensagem personalizada.
      2. Retentar COMO? Só o Pix Automático tem política de retentativa ativa.
    """
    if state.get("estrategia") != "retry_automatico":
        return "trigger_dunning"   # mensagem_pagamento: contato personalizado via LLM

    # Mesmo default de `decide_recovery` (workflow.py): um state sem
    # payment_method é tratado como cartão nos dois lugares. Dois defaults para
    # o mesmo campo ausente faziam o mesmo state seguir dois caminhos (P2-12).
    metodo = state.get("payment_method", "card")
    if metodo == "pix_automatico":
        return "schedule_retry_pix"

    # Defesa em profundidade: `decide_recovery` já não produz retry_automatico
    # para cartão. Se chegar aqui, é bug de quem montou o state — registra e
    # manda para a mensagem personalizada, nunca para uma retentativa.
    logger.warning(
        "[CARTAO-DESATIVADO] Retentativa automática pedida para payment_method=%r, "
        "mas a recobrança de cartão está fora do pipeline ativo — seguindo para "
        "mensagem personalizada.", metodo,
    )
    return "trigger_dunning"


def route_after_retry(state: AgentState) -> str:
    # Retentativa esgotada sem sucesso -> cai para a mensagem personalizada.
    if state.get("retry_exhausted") and not state.get("recovered"):
        return "trigger_dunning"
    return "update_dashboard"


def build_crai_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("diagnose", diagnose_failure)
    graph.add_node("check_anomaly", check_anomaly)
    graph.add_node("infer_payday", infer_payday)
    graph.add_node("decide_recovery", decide_recovery)
    # Único nó de retentativa do pipeline ativo. O nó de cartão foi retirado na
    # Fase 3 e está preservado em crai/dunning/legacy_card/card_retry.py.
    graph.add_node("schedule_retry_pix", schedule_retry_pix)
    graph.add_node("trigger_dunning", trigger_dunning)
    graph.add_node("update_dashboard", update_roi_dashboard)

    graph.set_entry_point("diagnose")
    graph.add_conditional_edges("diagnose", route_after_diagnosis, {
        "check_anomaly": "check_anomaly", "update_dashboard": "update_dashboard",
    })
    graph.add_edge("check_anomaly", "infer_payday")
    graph.add_edge("infer_payday", "decide_recovery")
    graph.add_conditional_edges("decide_recovery", route_after_decision, {
        "schedule_retry_pix": "schedule_retry_pix",
        "trigger_dunning":    "trigger_dunning",
    })
    graph.add_conditional_edges("schedule_retry_pix", route_after_retry, {
        "trigger_dunning": "trigger_dunning", "update_dashboard": "update_dashboard",
    })
    graph.add_edge("trigger_dunning", "update_dashboard")
    graph.add_edge("update_dashboard", END)

    # ⚠️ O contador de tentativas do BACEN mora AQUI, e só aqui.
    #
    # `MemorySaver` guarda o checkpoint em RAM, no processo. Isso tem três
    # consequências que precisam estar escritas, e não descobertas depois:
    #
    #   1. Dois processos (dois workers do uvicorn, dois pods) têm memórias
    #      separadas. O mesmo `id_recorrencia` atendido por workers diferentes
    #      recebe 3 tentativas de cada um: 6 na mesma janela de 7 dias, contra
    #      o limite de 3. A CRAI hoje só é correta rodando em UM processo.
    #   2. Reiniciar o serviço zera o contador de todo mundo. Uma janela do
    #      BACEN dura 7 dias; nenhum deploy nesse intervalo pode acontecer sem
    #      reabrir o direito a 3 novas tentativas que já foram gastas.
    #   3. `pix_janela_ate` (ver crai/agent/state.py) sofre do mesmo mal: a
    #      marca de expiração da janela some junto com o contador.
    #
    # É dívida ASSUMIDA para a demo, não descuido: a POC roda em processo
    # único, e trocar o checkpointer por um persistente (`SqliteSaver` /
    # `PostgresSaver`, que o LangGraph já oferece com a mesma interface) é uma
    # linha aqui mais uma migração — trabalho de produção, não de banca.
    # Enquanto isso não acontecer, o limite regulatório é garantido pelo
    # processo, e o processo é a fronteira de correção do sistema.
    return graph.compile(checkpointer=MemorySaver())


crai_agent = build_crai_graph()

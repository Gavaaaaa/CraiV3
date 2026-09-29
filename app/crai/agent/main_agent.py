"""crai/agent/main_agent.py — Orquestrador do agente de churn involuntário.

O único caminho de retentativa automática do pipeline ativo é o de **Pix
Automático**, sob a janela regulada pelo BACEN. A recobrança automática de
cartão saiu do pipeline na Fase 3 e está preservada, isolada, em
`crai/dunning/legacy_card/` — ver o `__init__.py` de lá para o motivo e para o
caminho de reativação.

    open_cycle ─┬─ evento de cobrança NOVA ──────────→ diagnose ─┬─ vale agir → …
                │                                               └─ não vale → descartar_ciclo → update_dashboard
                └─ resultado de tentativa / falha tardia → registrar_resultado → END

    decide_recovery ─┬─ payment_method == "pix_automatico" → schedule_retry_pix
                     └─ qualquer outro caso               → trigger_dunning

O nó de entrada `open_cycle` (Etapa 1, Bloco 2) abre ou reencontra o CICLO DE
COBRANÇA em `crai/dunning/ciclo_cobranca.py` — a fonte da verdade — e decide o
que este evento é para ele. Diagnóstico, anomalia, liquidez e decisão rodam
UMA vez por ciclo, na cobrança nova; a falha de uma retentativa já disparada
só grava o resultado dela.

A aresta condicional depois de `decide_recovery` continua lendo
`payment_method`, e não um `if` dentro de um nó genérico: é o que garante que
só evento de Pix alcança a política de Pix, e é o ponto onde um nó de cartão
volta a ser plugado quando for reimplementado.
"""

import logging

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from .state import AgentState
from .pix_codes import CAUSA_REVOGADA
from .workflow import (
    diagnose_failure, check_anomaly, infer_payday, decide_recovery,
    descartar_ciclo, open_cycle, registrar_resultado_do_evento,
    schedule_retry_pix, trigger_dunning, update_roi_dashboard,
)
from ..dunning import ciclo_cobranca

logger = logging.getLogger(__name__)


def route_after_open_cycle(state: AgentState) -> str:
    """Cobrança nova roda o diagnóstico; o resto só registra o que aconteceu."""
    if state.get("ciclo_evento") in (ciclo_cobranca.RESULTADO_DE_TENTATIVA,
                                     ciclo_cobranca.FALHA_TARDIA):
        return "registrar_resultado"
    return "diagnose"


def route_after_diagnosis(state: AgentState) -> str:
    """Decide se vale a pena continuar com base no e-Profit.

    O aborto vai para `descartar_ciclo` (R6 / I-4b): a decisão de não agir é
    gravada na trilha e o ciclo vai a `descartado` com motivo — nunca em
    silêncio, e nunca confundível com um ciclo incompleto.

    D5: autorização REVOGADA não é abortada, mesmo com score abaixo do corte.
    A única ação que resta é a mensagem com o boleto, custa uma mensagem, e é
    a que o Crai decidiu que sai mesmo assim.
    """
    eprofit = state.get("eprofit", 0)
    recommend = state.get("recommend_action", True)
    score = state.get("recovery_score", 0)

    if state.get("failure_cause") == CAUSA_REVOGADA:
        print("[ROUTER] Autorização revogada -- a mensagem sai mesmo com score baixo (D5)")
        return "check_anomaly"
    if not recommend or eprofit <= 0:
        print(f"[ROUTER] e-Profit R$ {eprofit:.2f} <= 0 -- abortando (nao vale intervir)")
        return "descartar_ciclo"
    if score < 5:
        print(f"[ROUTER] Score {score}/100 muito baixo -- abortando")
        return "descartar_ciclo"
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
    # `retry_exhausted` significa "a política não devolveu tentativa": a
    # janela do BACEN encerrou, ou as 3 executadas já foram gastas.
    if state.get("retry_exhausted") and not state.get("recovered"):
        return "trigger_dunning"
    return "update_dashboard"


def build_crai_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("open_cycle", open_cycle)
    graph.add_node("registrar_resultado", registrar_resultado_do_evento)
    graph.add_node("descartar_ciclo", descartar_ciclo)
    graph.add_node("diagnose", diagnose_failure)
    graph.add_node("check_anomaly", check_anomaly)
    graph.add_node("infer_payday", infer_payday)
    graph.add_node("decide_recovery", decide_recovery)
    # Único nó de retentativa do pipeline ativo. O nó de cartão foi retirado na
    # Fase 3 e está preservado em crai/dunning/legacy_card/card_retry.py.
    graph.add_node("schedule_retry_pix", schedule_retry_pix)
    graph.add_node("trigger_dunning", trigger_dunning)
    graph.add_node("update_dashboard", update_roi_dashboard)

    graph.set_entry_point("open_cycle")
    graph.add_conditional_edges("open_cycle", route_after_open_cycle, {
        "diagnose": "diagnose", "registrar_resultado": "registrar_resultado",
    })
    graph.add_edge("registrar_resultado", END)
    graph.add_conditional_edges("diagnose", route_after_diagnosis, {
        "check_anomaly": "check_anomaly", "descartar_ciclo": "descartar_ciclo",
    })
    graph.add_edge("descartar_ciclo", "update_dashboard")
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

    # O CHECKPOINT NÃO CARREGA MAIS ESTADO DE NEGÓCIO ENTRE EVENTOS.
    #
    # Até a Etapa 1 (28/09/2026) o contador de tentativas do BACEN e o prazo
    # da janela moravam AQUI, num `MemorySaver` — RAM, por processo — e três
    # consequências ficaram documentadas neste comentário: dois processos com
    # memórias separadas (6 tentativas contra 3), reinício zerando o contador
    # de toda a base, e o prazo da janela sumindo junto. O diagnóstico de
    # 28/09 mediu as três (`docs/interno/DIAGNOSTICO_INTEGRACAO.md`, item 10).
    #
    # O que vale agora: contador, janela, plano, resultado de cada tentativa e
    # desfecho vivem nas tabelas de `crai/dunning/ciclo_cobranca.py`, sob
    # `BEGIN IMMEDIATE`. O nó `open_cycle` lê o ciclo e REESCREVE
    # `retry_count` (tentativas EXECUTADAS) e `pix_janela_ate` no state a cada
    # evento; nada que sobrou de um checkpoint anterior alcança um nó de
    # decisão. Trocar este `MemorySaver` por um checkpointer persistente
    # deixou de ser necessário para a regra regulatória: o checkpoint é o
    # rascunho de UMA execução, não a memória entre elas.
    #
    # O que continua valendo, e continua escrito: dentro de um processo, N
    # entregas simultâneas do mesmo `id_recorrencia` são serializadas pela
    # trava por `thread_id` em `_run_involuntary_pipeline` (crai/api/app.py) —
    # sem ela, duas execuções entrariam em `open_cycle` antes de a primeira
    # gravar o ciclo. Entre processos no MESMO arquivo, quem serializa é a
    # trava de escrita do SQLite (`BEGIN IMMEDIATE`) mais a `UNIQUE` por
    # cobrança. Entre máquinas diferentes, com arquivos diferentes, não há
    # nada — a mesma limitação do ledger de desfecho, declarada em
    # `docs/LIMITACOES.md`; a correção é Postgres.
    return graph.compile(checkpointer=MemorySaver())


crai_agent = build_crai_graph()

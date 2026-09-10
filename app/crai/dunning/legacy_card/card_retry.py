"""crai/dunning/legacy_card/card_retry.py — Nó de retentativa de cartão (inativo).

Este era o nó `schedule_retry_card` do grafo em `crai/agent/main_agent.py` até a
Fase 3. Está preservado aqui, íntegro, para a reimplementação futura descrita
no `__init__.py` deste pacote — **não é importado por nenhum código ativo**.

O que ele fazia: consultava o `SmartBackoff` (backoff exponencial com jitter,
sem teto regulatório) e devolvia o próximo horário de retentativa da cobrança
no cartão, ou marcava as tentativas como esgotadas.

Para reativar, reintroduza-o como nó do StateGraph com uma aresta condicional
em `payment_method == "card"` — nunca como um `if` dentro de um nó genérico
compartilhado com o Pix, porque as duas políticas são incompatíveis.
"""

from ...agent.state import AgentState
from .smart_backoff import SmartBackoff

_backoff = SmartBackoff()


async def schedule_retry_card(state: AgentState) -> AgentState:
    """Retentativa de CARTÃO — backoff exponencial + jitter, sem limite regulatório.

    Só pode ser alcançável quando payment_method == "card". Nunca deve receber
    um evento de Pix Automático: o backoff exponencial estouraria o limite de
    3 tentativas / 7 dias do BACEN.
    """
    attempt = state.get("retry_count", 0)
    result = _backoff.get_schedule(
        state["failure_cause"], attempt, state.get("optimal_retry_at"),
    )
    if result["exhausted"]:
        print("[AGENT] Retentativas esgotadas (cartão)")
    else:
        print(f"[AGENT] Retry de cartão agendado: "
              f"{result['schedule_at'].strftime('%d/%m %H:%M')} ({result.get('strategy')})")
    return {**state, "next_retry_at": result.get("schedule_at"),
            "retry_exhausted": result["exhausted"], "retry_count": attempt + 1}

"""crai/dunning/dunning_engine.py — Módulo 5: LangGraph + Claude API.

Motor de cobrança que gera a mensagem personalizada de recuperação e escolhe
o meio de pagamento. Grafo LangGraph de 4 nós:

    select_payment → define_tone → generate_msg → send_msg

Sem escalonamento humano: toda cobrança que chega aqui vira uma mensagem
personalizada via LLM. O meio de pagamento segue **Pix Automático como
primeira opção, boleto como fallback** — o Pix Automático (disponível no
Brasil desde jun/2025) recupera na hora, sem o cliente reabrir o app.
"""

from anthropic import AsyncAnthropic
from langgraph.graph import StateGraph, END
from typing import TypedDict

claude = AsyncAnthropic()

# Chaves = vocabulário de `AgentState.failure_cause`.
#
# O bloco de cima nasceu com os códigos do Stripe, quando o pipeline ativo era
# de cartão. O de baixo entrou no Sprint 3, com o PIX_CODE_MAP: são as duas
# causas de Pix que NÃO se resolvem por retentativa e que, por isso, chegam
# sempre aqui. Cada uma pede uma ação diferente do cliente — aumentar o limite
# do Pix Automático ou reautorizar a recorrência —, e uma mensagem genérica de
# "não conseguimos cobrar" faria o cliente tentar pagar de novo pelo caminho
# que está bloqueado. Os templates de cartão continuam para retrocompatibilidade
# do caminho legado.
FALLBACK_TEMPLATES = {
    "expired_card":       "Olá! Seu cartão expirou e não conseguimos cobrar R$ {amount:.2f}. Pague em segundos via {metodo}: {link}",
    "insufficient_funds": "Oi! Tivemos dificuldade ao cobrar R$ {amount:.2f}. Regularize via {metodo}: {link}",
    "do_not_honor":       "Olá! Seu banco não autorizou a cobrança de R$ {amount:.2f}. Pague por {metodo}: {link}",
    "card_declined":      "Olá! Seu banco recusou a cobrança de R$ {amount:.2f}. Pague por {metodo}: {link}",
    "generic_decline":    "Olá! Não foi possível concluir a cobrança de R$ {amount:.2f}. Pague por {metodo}: {link}",
    "processing_error":   "Olá! Houve uma falha técnica no pagamento de R$ {amount:.2f}. Conclua por {metodo}: {link}",
    # ── Pix Automático (Sprint 3) ────────────────────────────────────────
    "limit_exceeded":        "Olá! A cobrança de R$ {amount:.2f} passou do limite do seu Pix Automático. Aumente o limite no app do seu banco ou pague agora por {metodo}: {link}",
    "authorization_revoked": "Olá! A autorização do seu Pix Automático foi cancelada, então não conseguimos cobrar os R$ {amount:.2f}. Reative a recorrência ou pague por {metodo}: {link}",
}

# Rótulo do meio de pagamento para a mensagem.
METODO_LABEL = {"pix_automatico": "Pix Automático", "boleto": "boleto"}


class DunningState(TypedDict):
    customer_id: str
    failure_cause: str
    recovery_score: float
    amount: float
    channel: str
    tone: str
    message: str
    sent: bool
    portal_link: str
    payment_method: str


class DunningEngine:
    def __init__(self):
        self.graph = self._build_graph()

    def _build_graph(self):
        g = StateGraph(DunningState)
        g.add_node("select_payment", self._select_payment)
        g.add_node("define_tone", self._define_tone)
        g.add_node("generate_msg", self._generate_message)
        g.add_node("send_msg", self._send_message)
        g.set_entry_point("select_payment")
        g.add_edge("select_payment", "define_tone")
        g.add_edge("define_tone", "generate_msg")
        g.add_edge("generate_msg", "send_msg")
        g.add_edge("send_msg", END)
        return g.compile()

    async def _select_payment(self, state):
        """Escolhe o meio de pagamento: Pix Automático primeiro, boleto no fallback.

        Cartão expirado/recusado é problema do cartão — Pix Automático contorna
        o cartão e recupera na hora. Só cai para boleto quando o Pix não se
        aplica (ex.: erro técnico do próprio gateway de cobrança).
        """
        if state["failure_cause"] == "processing_error":
            metodo = "boleto"
            motivo = "falha técnica do gateway — boleto evita novo processamento no mesmo canal"
        elif state["failure_cause"] == "authorization_revoked":
            # Oferecer Pix Automático a quem acabou de revogar a autorização é
            # oferecer exatamente o caminho que o cliente fechou. O boleto é o
            # meio que funciona hoje; reautorizar é o que a MENSAGEM pede.
            metodo = "boleto"
            motivo = ("autorização de recorrência revogada — o Pix Automático "
                      "está indisponível até o cliente reautorizar")
        else:
            metodo = "pix_automatico"
            motivo = "Pix Automático recupera na hora, contornando o cartão"

        portal = f"https://pay.crai.ai/{metodo}/{state['customer_id'][:8]}"
        print(f"[DUNNING] Meio de pagamento: {METODO_LABEL[metodo]} ({motivo})")
        return {**state, "channel": "whatsapp", "payment_method": metodo, "portal_link": portal}

    async def _define_tone(self, state):
        score = state["recovery_score"]
        tone = "empático" if score >= 0.7 else "amigável" if score >= 0.4 else "direto"
        return {**state, "tone": tone}

    async def _generate_message(self, state):
        metodo_label = METODO_LABEL[state["payment_method"]]
        prompt = f"""Gere mensagem de {state['channel']} para recuperar um pagamento que falhou.
Causa: {state['failure_cause']} | Valor: R$ {state['amount']:.2f} | Tom: {state['tone']}
Meio de pagamento oferecido: {metodo_label}
Máximo 3 frases. Português brasileiro natural, sem culpar o cliente.
Incluir o link: {state['portal_link']}
Retorne APENAS a mensagem."""
        try:
            response = await claude.messages.create(
                model="claude-sonnet-5", max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )
            message = response.content[0].text.strip()
        except Exception as e:
            print(f"[DUNNING] Claude API indisponível ({e}) — usando fallback")
            template = FALLBACK_TEMPLATES.get(state["failure_cause"], FALLBACK_TEMPLATES["processing_error"])
            message = template.format(amount=state["amount"], link=state["portal_link"],
                                      metodo=metodo_label)
        return {**state, "message": message}

    async def _send_message(self, state):
        print(f"[DUNNING] {state['channel'].upper()} → {state['customer_id']}: {state['message'][:90]}")
        return {**state, "sent": True}

    async def run_campaign(self, customer_id, failure_cause, recovery_score, amount) -> dict:
        initial = DunningState(
            customer_id=customer_id, failure_cause=failure_cause, recovery_score=recovery_score,
            amount=amount, channel="whatsapp", tone="", message="", sent=False,
            portal_link="", payment_method="",
        )
        result = await self.graph.ainvoke(initial)
        return {"sent": result["sent"], "channel": result["channel"],
                "payment_method": result["payment_method"], "message": result["message"]}

"""crai/dunning/dunning_engine.py — Módulo 5: LangGraph + Claude API.

Motor de cobrança que gera a mensagem personalizada de recuperação e escolhe
o meio de pagamento. Grafo LangGraph de 4 nós:

    select_payment → define_tone → generate_msg → send_msg

Sem escalonamento humano: toda cobrança que chega aqui vira uma mensagem
personalizada via LLM. O meio de pagamento segue **Pix Automático como
primeira opção, boleto como fallback** — o Pix Automático (disponível no
Brasil desde jun/2025) recupera na hora, sem o cliente reabrir o app.
"""

import json
import logging
from typing import Optional, TypedDict

from anthropic import AsyncAnthropic

from ..agent.pix_codes import CAUSA_LEGIVEL
from ..churn_voluntary import retention_log as trilha
from .ciclo_cobranca import ABORDAGENS
from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)

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

# ── As três sugestões (Etapa 2, R7) ──────────────────────────────────────
ROTULO_DA_ABORDAGEM = {"lembrete_cordial": "Lembrete cordial", "facilitacao": "Facilitação",
                       "urgencia_respeitosa": "Urgência com respeito"}

# LGPD: toda mensagem, do LLM ou do template, termina dizendo que é automática.
# Acrescentada DEPOIS da geração (o LLM é instruído a não escrevê-la), para que
# nenhum texto saia sem ela. Há teste que reprova mensagem sem esta linha.
AVISO_AUTOMATICO = "Esta é uma mensagem automática."

# O link de pagamento NÃO vai ao LLM: o prompt pede este marcador e o link é
# posto no lugar depois. O link carrega os 8 primeiros caracteres do id da
# recorrência — que, num id curto, são o id inteiro.
MARCADOR_LINK = "{link}"
TEXTO_MAXIMO = 480

ROTULO_DO_CANAL = {"whatsapp": "WhatsApp", "email": "e-mail", "sem_canal": "texto"}

# Reserva por abordagem e por causa: 3 abordagens x as 4 causas do Pix. Causa
# fora destas (vocabulário de cartão, legado) usa a de `processing_error`.
# Nenhum termo de encaminhamento humano, nenhuma ameaça que a empresa não
# declarou (multa, corte, prazo).
TEMPLATES_POR_ABORDAGEM = {
    "lembrete_cordial": {
        "insufficient_funds": "{saudacao} Passando para lembrar da mensalidade de R$ {valor}, que ainda não foi compensada. Quando puder, regularize por {metodo}: {link}",
        "limit_exceeded": "{saudacao} Lembrete rápido: a cobrança de R$ {valor} passou do limite do seu Pix Automático. Quando puder, ajuste o limite no app do seu banco ou pague por {metodo}: {link}",
        "authorization_revoked": "{saudacao} Lembrete rápido: a autorização do Pix Automático foi cancelada e a mensalidade de R$ {valor} ficou em aberto. Você pode pagar por {metodo}: {link}",
        "processing_error": "{saudacao} Houve uma falha técnica na cobrança de R$ {valor}, sem nenhum problema do seu lado. Quando puder, conclua por {metodo}: {link}",
    },
    "facilitacao": {
        "insufficient_funds": "{saudacao} Sabemos que imprevistos acontecem. Para facilitar, você pode quitar os R$ {valor} por {metodo}, em poucos toques: {link}",
        "limit_exceeded": "{saudacao} Para facilitar: aumente o limite do Pix Automático no app do seu banco, ou pague os R$ {valor} agora por {metodo}: {link}",
        "authorization_revoked": "{saudacao} Para facilitar: reative a recorrência no app do seu banco, ou pague os R$ {valor} por {metodo} neste link: {link}",
        "processing_error": "{saudacao} Para facilitar, deixamos um caminho direto para concluir o pagamento de R$ {valor} por {metodo}: {link}",
    },
    "urgencia_respeitosa": {
        "insufficient_funds": "{saudacao} A mensalidade de R$ {valor} segue em aberto depois das tentativas de cobrança. Pedimos que regularize assim que possível por {metodo}: {link}",
        "limit_exceeded": "{saudacao} A cobrança de R$ {valor} continua em aberto porque passou do limite do Pix Automático. Pedimos que regularize assim que possível, ajustando o limite ou pagando por {metodo}: {link}",
        "authorization_revoked": "{saudacao} Com a autorização do Pix Automático cancelada, a mensalidade de R$ {valor} continua em aberto. Pedimos que regularize assim que possível por {metodo}: {link}",
        "processing_error": "{saudacao} O pagamento de R$ {valor} ainda não foi concluído por uma falha técnica. Pedimos que conclua assim que possível por {metodo}: {link}",
    },
}
CAUSA_DE_RESERVA = "processing_error"


def com_aviso(texto: str) -> str:
    """O texto com a linha de mensagem automática no fim, uma vez só."""
    texto = (texto or "").rstrip()
    if texto.endswith(AVISO_AUTOMATICO):
        return texto
    return f"{texto}\n{AVISO_AUTOMATICO}"


def abordagem_recomendada(causa: Optional[str], p_recovery: Optional[float]) -> str:
    """A recomendada entre as 3, pelo que já existe (Relatório 0, 0.3):

      1. autorização revogada ou limite excedido → Facilitação: a ação está do
         lado do cliente, e a mensagem tem de mostrar o caminho;
      2. falha técnica → Lembrete cordial: não há culpa do cliente;
      3. senão, pelos mesmos cortes de `_define_tone` sobre `p_recovery` (sem
         valor: o neutro 0,5): >= 0,7 lembrete; >= 0,4 facilitação; abaixo,
         urgência com respeito.
    """
    if causa in ("authorization_revoked", "limit_exceeded"):
        return "facilitacao"
    if causa == "processing_error":
        return "lembrete_cordial"
    p = 0.5 if p_recovery is None else float(p_recovery)
    if p >= 0.7:
        return "lembrete_cordial"
    if p >= 0.4:
        return "facilitacao"
    return "urgencia_respeitosa"


def montar_prompt(canal: str, causa: str, valor: float, metodo: str, tom: str,
                  primeiro_nome: Optional[str] = None,
                  tempo_de_casa: Optional[str] = None) -> str:
    """O prompt das 3 sugestões, com o MÍNIMO (LGPD, Etapa 2): canal, causa em
    português, valor, meio de pagamento, tom, e — só se a base tiver — o
    primeiro nome e a faixa de tempo de casa real. Nunca e-mail, telefone,
    CPF, chave Pix, id da recorrência nem e2e: esta função nem os recebe."""
    linhas = [
        f"Escreva 3 versões de uma mensagem de {ROTULO_DO_CANAL.get(canal, 'texto')} para um "
        "cliente cuja mensalidade não foi paga depois das tentativas automáticas de cobrança.",
        f"Causa: {CAUSA_LEGIVEL.get(causa, causa)}. Valor: R$ {valor:.2f}. "
        f"Meio de pagamento oferecido: {METODO_LABEL.get(metodo, metodo)}. Tom sugerido: {tom}.",
    ]
    if primeiro_nome:
        linhas.append(f"Primeiro nome do cliente: {primeiro_nome}.")
    if tempo_de_casa:
        linhas.append(f"Tempo como cliente: {tempo_de_casa}.")
    linhas += [
        "As três abordagens:",
        '- "lembrete_cordial": um lembrete gentil, de quem confia que o cliente vai pagar;',
        '- "facilitacao": mostra o caminho mais fácil para resolver agora;',
        '- "urgencia_respeitosa": deixa claro que a mensalidade continua em aberto, com respeito.',
        "Regras: no máximo 3 frases cada; português brasileiro natural; sem culpar o cliente; "
        "não ofereça contato com pessoas (atendimento, suporte, ligação); não invente prazos, "
        "multas ou consequências; inclua exatamente uma vez o marcador {link} onde vai o link "
        "de pagamento; não assine; não diga que a mensagem é automática.",
        'Responda APENAS com um objeto JSON com as chaves "lembrete_cordial", "facilitacao" e '
        '"urgencia_respeitosa", cada uma com o texto.',
    ]
    return "\n".join(linhas)


def _texto_do_llm_valido(texto) -> bool:
    if not isinstance(texto, str) or not texto.strip() or len(texto) > TEXTO_MAXIMO:
        return False
    if texto.count(MARCADOR_LINK) != 1:
        return False
    # Mesmo filtro de escalonamento humano do voluntário, reusado sem cópia.
    from ..churn_voluntary.voluntary_agent import encaminha_para_humano
    return encaminha_para_humano(texto) is None


def _json_das_sugestoes(bruto: str) -> dict:
    inicio, fim = bruto.find("{"), bruto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return {}
    try:
        dados = json.loads(bruto[inicio:fim + 1])
    except ValueError:
        return {}
    return dados if isinstance(dados, dict) else {}


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
    # De onde veio `message`: "gerado" (Claude API) ou "template" (fallback).
    # `codigo_template` e a chave de FALLBACK_TEMPLATES usada, ou "" quando o
    # texto foi gerado. Os dois existem para que quem EXIBE a mensagem possa
    # escreve-la no idioma do leitor: um template e um conjunto fechado, entao
    # e traduzivel; texto gerado nao e, e continua exibido como chegou.
    origem: str
    codigo_template: str
    # Por que `_select_payment` escolheu o meio de pagamento — vai para a
    # trilha do Art. 20 como `motivo_da_regra`.
    motivo_metodo: str
    # Etapa 2: a mensagem já escolhida entre as 3 sugestões. Com `texto_pronto`
    # o nó de geração não chama o LLM — só envia o que foi escolhido.
    texto_pronto: str
    abordagem: str
    motivo_canal: str


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
        # O canal vem de quem chama (Etapa 2: a cadeia de canal da base); sem
        # ele, o `whatsapp` de sempre.
        return {**state, "channel": state.get("channel") or "whatsapp", "payment_method": metodo,
                "portal_link": portal, "motivo_metodo": motivo}

    async def _define_tone(self, state):
        score = state["recovery_score"]
        tone = "empático" if score >= 0.7 else "amigável" if score >= 0.4 else "direto"
        return {**state, "tone": tone}

    async def _generate_message(self, state):
        if state.get("texto_pronto"):
            # A mensagem escolhida entre as 3 (Etapa 2): enviar, não redigir.
            return {**state, "message": com_aviso(state["texto_pronto"]),
                    "origem": state.get("origem") or "gerado",
                    "codigo_template": state.get("codigo_template") or ""}
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
            codigo = (state["failure_cause"] if state["failure_cause"] in FALLBACK_TEMPLATES
                      else "processing_error")
            message = FALLBACK_TEMPLATES[codigo].format(
                amount=state["amount"], link=state["portal_link"], metodo=metodo_label)
            return {**state, "message": com_aviso(message), "origem": "template",
                    "codigo_template": codigo}
        return {**state, "message": com_aviso(message), "origem": "gerado", "codigo_template": ""}

    async def _send_message(self, state):
        print(f"[DUNNING] {state['channel'].upper()} → {state['customer_id']}: {state['message'][:90]}")
        return {**state, "sent": True}

    def decisoes_da_campanha(self, tenant_id, customer_id, result: dict) -> list[dict]:
        """As duas decisões da campanha para a trilha do Art. 20: o que
        oferecer (meio de pagamento + tom) e por onde (canal).

        Entra `texto_codigo` e `origem` da mensagem, NUNCA o texto: o corpo
        é o que a pessoa leu, não o critério que o sistema usou.
        """
        entradas = {"failure_cause": result.get("failure_cause"),
                    "p_recovery": trilha._num(result.get("recovery_score")),
                    "amount": trilha._num(result.get("amount"))}
        saida_oferta = {"payment_method": result.get("payment_method"),
                        "tom": result.get("tone"),
                        "texto_codigo": result.get("codigo_template") or None,
                        "origem_texto": result.get("origem"),
                        "regra": "DunningEngine._select_payment",
                        "motivo_da_regra": result.get("motivo_metodo")}
        if result.get("abordagem"):
            saida_oferta["abordagem"] = result["abordagem"]
        oferta = trilha.decisao(
            tenant_id, customer_id, trilha.DOMINIO_INVOLUNTARIO, trilha.TIPO_OFERTA,
            trilha.MODELO_REGRA, entradas=entradas, saida=saida_oferta)
        if result.get("motivo_canal"):
            # Etapa 2: o canal saiu dos contatos da base, lidos na hora.
            saida_canal = {"channel": result.get("channel"),
                           "regra": "canal_involuntario.escolher_canal",
                           "motivo_da_regra": "primeiro canal permitido pela empresa com "
                                              f"contato na base ({result['motivo_canal']}); "
                                              "canais humanos são proibidos"}
        else:
            saida_canal = {"channel": result.get("channel"),
                           "regra": "DunningEngine.canal_padrao",
                           "motivo_da_regra": "único canal com integração de envio nesta fase "
                                              "(bot de WhatsApp); canais humanos são proibidos"}
        canal = trilha.decisao(
            tenant_id, customer_id, trilha.DOMINIO_INVOLUNTARIO, trilha.TIPO_CANAL,
            trilha.MODELO_REGRA, entradas={"failure_cause": result.get("failure_cause")},
            saida=saida_canal)
        return [oferta, canal]

    async def gerar_sugestoes(self, customer_id: str, failure_cause: str, recovery_score,
                              amount: float, canal: str = "whatsapp",
                              primeiro_nome: Optional[str] = None,
                              tempo_de_casa: Optional[str] = None) -> dict:
        """As 3 sugestões de mensagem (R7): UMA chamada ao LLM que devolve as
        três em JSON, validadas uma a uma; a que falhar na validação (ou todas,
        se o LLM não responder) sai do template da abordagem e da causa. O link
        entra depois da geração, e o aviso de mensagem automática no fim.

        Devolve `{sugestoes: [{abordagem, texto, recomendada, origem_texto,
        codigo_template}], recomendada, payment_method, tom, portal_link,
        motivo_metodo}`."""
        base = await self._select_payment({"customer_id": customer_id,
                                           "failure_cause": failure_cause, "channel": canal})
        base = await self._define_tone({**base, "recovery_score": recovery_score})
        metodo, link = base["payment_method"], base["portal_link"]
        prompt = montar_prompt(canal, failure_cause, amount, metodo, base["tone"],
                               primeiro_nome, tempo_de_casa)
        do_llm: dict = {}
        try:
            response = await claude.messages.create(
                model="claude-sonnet-5", max_tokens=900,
                messages=[{"role": "user", "content": prompt}])
            do_llm = _json_das_sugestoes(response.content[0].text)
        except Exception as e:                   # noqa: BLE001 — o template é a reserva
            print(f"[DUNNING] Claude API indisponível para as 3 sugestões ({e}) — templates")
        recomendada = abordagem_recomendada(failure_cause, recovery_score)
        causa_tpl = failure_cause if failure_cause in TEMPLATES_POR_ABORDAGEM["facilitacao"] \
            else CAUSA_DE_RESERVA
        saudacao = f"Olá, {primeiro_nome}!" if primeiro_nome else "Olá!"
        sugestoes = []
        for abordagem in ABORDAGENS:
            texto = do_llm.get(abordagem)
            if _texto_do_llm_valido(texto):
                final, origem, codigo = texto.replace(MARCADOR_LINK, link), "llm", None
            else:
                if abordagem in do_llm:
                    logger.warning("[DUNNING] sugestão %s do LLM recusada na validação — template",
                                   abordagem)
                final = TEMPLATES_POR_ABORDAGEM[abordagem][causa_tpl].format(
                    saudacao=saudacao, valor=f"{amount:.2f}",
                    metodo=METODO_LABEL.get(metodo, metodo), link=link)
                origem, codigo = "template", f"{abordagem}.{causa_tpl}"
            sugestoes.append({"abordagem": abordagem, "texto": com_aviso(final),
                              "recomendada": abordagem == recomendada,
                              "origem_texto": origem, "codigo_template": codigo})
        return {"sugestoes": sugestoes, "recomendada": recomendada, "payment_method": metodo,
                "tom": base["tone"], "portal_link": link, "motivo_metodo": base["motivo_metodo"]}

    async def run_campaign(self, customer_id, failure_cause, recovery_score, amount,
                           tenant_id=None, *, texto=None, abordagem=None, canal=None,
                           motivo_canal=None, origem=None, codigo_template=None) -> dict:
        """Envia UMA mensagem. Sem `texto`, redige (LLM ou template) como sempre
        fez. Com `texto` (Etapa 2), envia a mensagem já escolhida entre as 3,
        pelo `canal` escolhido na base — o LLM não é chamado."""
        initial = DunningState(
            customer_id=customer_id, failure_cause=failure_cause, recovery_score=recovery_score,
            amount=amount, channel=canal or "whatsapp", tone="", message="", sent=False,
            portal_link="", payment_method="", origem=origem or "",
            codigo_template=codigo_template or "", texto_pronto=texto or "",
            abordagem=abordagem or "", motivo_canal=motivo_canal or "",
        )
        result = await self.graph.ainvoke(initial)
        return {"sent": result["sent"], "channel": result["channel"],
                "payment_method": result["payment_method"], "message": result["message"],
                "origem": result["origem"], "codigo_template": result["codigo_template"],
                "portal_link": result["portal_link"], "abordagem": result.get("abordagem") or None,
                # Trilha do Art. 20: montadas aqui, gravadas no fim do grafo.
                "decisoes": self.decisoes_da_campanha(tenant_id, customer_id, result)}

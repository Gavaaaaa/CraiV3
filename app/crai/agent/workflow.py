"""crai/agent/workflow.py — Nós do pipeline de churn involuntário."""

from datetime import datetime
from typing import Optional
from .state import AgentState
from ..config import (
    CANAIS_HUMANOS,
    CANAL_PADRAO,
    custo_intervencao,
    custo_tentativa_pix,
    custos_por_canal,
    success_fee_pct,
)
from .pix_codes import CAUSA_LEGIVEL, CAUSAS_RETENTAVEIS_PIX, EXPLICACAO_DA_CAUSA, causa_do_codigo
from ..ml.failure_classifier import FailureClassifier
from ..ml.anomaly_detector import AnomalyDetector
from ..ml.payday_inference import PaydayInference
from .perfil_provider import provedor_padrao
from ..dunning.pix_automatico_retry import (
    MAX_TENTATIVAS as MAX_TENTATIVAS_PIX,
    PixAutomaticoRetryPolicy,
    fim_da_janela,
    inicio_da_janela,
)
from ..dunning.dunning_engine import DunningEngine
from ..dunning import recovery_log, retry_state
from ..dunning.retry_scheduler import disparar_tentativa
from ..integrations.hubspot_crm import HubSpotCRM

# Não há import de smart_backoff aqui: a retentativa de cartão saiu do pipeline
# ativo na Fase 3 e vive isolada em crai/dunning/legacy_card/.

_classifier = FailureClassifier()
_detector   = AnomalyDetector()
_payday     = PaydayInference()
_dunning    = DunningEngine()
_hubspot    = HubSpotCRM()

# Tentar carregar modelos treinados; se não existirem, usa heurística
_classifier.load()
_detector.load()
_payday.load()

# A política de Pix reaproveita o Payday Engine já carregado acima, em vez de
# instanciar e recarregar o modelo por conta própria.
_pix_retry = PixAutomaticoRetryPolicy(payday_inference=_payday)

# De onde vem tenure/histórico/LTV. Sintético enquanto não houver fonte real —
# ver `crai/agent/perfil_provider.py` e `crai/agent/README_treino.md`. É
# módulo-level para que um teste (ou a fase de treino) troque o provedor sem
# tocar em `_features_pix`.
_perfil_provider = provedor_padrao()


def _agora() -> datetime:
    """Ponto único de leitura do relógio nos nós do grafo.

    Existe para ser substituível: a expiração da janela do BACEN só é
    testável se o teste puder colocar o pipeline 60 dias à frente sem
    esperar 60 dias. Chamar `datetime.now()` direto dentro dos nós tornaria
    o invariante "3 por janela de 7 dias" indistinguível, em teste, do
    invariante errado "3 por contrato, para sempre".
    """
    return datetime.now()


def _janela_vigente(state: AgentState, agora: datetime) -> tuple[int, Optional[datetime]]:
    """Quantas tentativas já foram usadas NA JANELA ABERTA, e até quando ela vai.

    O contador do checkpoint (`retry_count`) é cumulativo e não sabe a que
    janela pertence. Quem sabe é `pix_janela_ate`. Passado o prazo, aquele
    contador descreve uma janela encerrada e não pode mais bloquear nada: a
    próxima cobrança que falhar abre uma janela nova, com as 3 tentativas
    próprias que o BACEN concede a ela.

    Um checkpoint com contador mas sem prazo (gravado antes desta marca
    existir, ou por uma origem externa que sobrescreveu `retry_count`) é
    tratado como janela ainda aberta — na dúvida, o limite regulatório
    aperta, nunca afrouxa.
    """
    usadas = state.get("retry_count") or 0
    prazo = state.get("pix_janela_ate")

    # `prazo is not None`, e não `isinstance(prazo, datetime)`: o segundo casa
    # contra o `datetime` global DESTE módulo, que é justamente o que um teste
    # substitui para congelar o relógio. Um `pix_janela_ate` legítimo deixaria
    # de ser reconhecido durante o teste, e a checagem defensiva silenciaria a
    # regra que ela deveria proteger. O campo só é escrito aqui e é tipado
    # `Optional[datetime]` no state; não há terceiro valor possível.
    if prazo is not None and agora > prazo:
        return 0, None
    return usadas, prazo


def canais_considerados_involuntario(optimal: dict | None) -> list[dict]:
    """O comparativo de canal do classificador, com o motivo de cada descarte.

    `_find_optimal_channel` compara os cinco canais da tabela de custos pelo
    e-Profit. Esse resultado NÃO decide o envio, e isto é limitação de
    integração declarada, não otimização: o único canal com integração nesta
    fase é o bot de WhatsApp (`CANAL_PADRAO`), e é por ele que a mensagem sai.
    Antes desta função o comparativo era calculado e descartado no caminho
    entre o classificador e o grafo; agora ele chega ao estado e ao painel,
    canal a canal, dizendo por que cada um não foi o escolhido.

    `ligacao_cs` é canal humano e nunca é elegível — nem quando o comparativo
    o apontar como o de maior e-Profit.
    """
    optimal = optimal or {}
    eprofits = optimal.get("all_channels") or {}
    melhor = optimal.get("channel")
    linhas = []
    for canal in custos_por_canal():
        if canal == CANAL_PADRAO:
            escolhido = True
            motivo = "único canal com integração de envio nesta fase (bot de WhatsApp)"
        elif canal in CANAIS_HUMANOS:
            escolhido = False
            motivo = "canal humano: proibido pela invariante de escalonamento zero"
        elif canal == melhor:
            escolhido = False
            motivo = "maior e-Profit do comparativo, mas sem integração de envio nesta fase"
        else:
            escolhido = False
            motivo = "sem integração de envio nesta fase"
        linhas.append({
            "canal": canal,
            "eprofit": eprofits.get(canal),
            "melhor_eprofit": canal == melhor,
            "escolhido": escolhido,
            "motivo": motivo,
        })
    return linhas


async def diagnose_failure(state: AgentState) -> AgentState:
    """Diagnostica causa da falha via ensemble XGBoost+RF com e-Profit e SHAP."""
    features = _extract_features(
        state["payment_event"], state["amount"], state.get("payment_method", "card"),
        customer_id=state["customer_id"],
    )
    # O campo `degradacoes` do evento normalizado só vale alguma coisa se algum
    # nó o LER. A borda já recusa as degradações bloqueantes (valor ilegível ou
    # ausente); as não-bloqueantes chegam até aqui e precisam ficar visíveis na
    # trilha de auditoria, senão o diagnóstico aparece na tela sem dizer que foi
    # feito sobre um evento remendado.
    degradacoes = state["payment_event"].get("degradacoes") or []
    if degradacoes:
        print(f"[QUALIDADE] Evento normalizado com degradação: {', '.join(degradacoes)} "
              f"— diagnóstico feito sobre campos preenchidos por default")

    result = _classifier.predict(features)

    shap_readable = result["shap_explanation"].get("readable", "")
    print(f"[AGENT] Diagnóstico ({result['method']}): "
          f"score {result['recovery_score']}/100 | "
          f"e-Profit R$ {result['eprofit']:.2f} | "
          f"ação: {'SIM' if result['recommend_action'] else 'NÃO'}")
    if shap_readable:
        print(f"[SHAP]  {shap_readable}")

    return {
        **state,
        # Preservado para o log de ciclo (Sprint 6): é o X do dataset de treino.
        "features": features,
        "failure_cause": features["gateway_error_code"],
        "recovery_score": result["recovery_score"],
        "p_recovery": result["p_recovery"],
        "eprofit": result["eprofit"],
        "recommend_action": result["recommend_action"],
        "ltv_estimated": result["ltv_estimated"],
        "shap_explanation": result["shap_explanation"],
        "feature_importance": {
            f["feature"]: f["contribution_pct"]
            for f in result["shap_explanation"].get("features", [])[:5]
        },
        # Era calculado pelo classificador e descartado aqui. Agora viaja.
        "canais_considerados": canais_considerados_involuntario(result.get("optimal_channel")),
    }


def _custo_previsto_do_ciclo(state: AgentState) -> float:
    """Quanto a CRAI espera gastar para tentar recuperar este ciclo.

    A mensagem personalizada é o custo certo (o canal ativo é o bot de
    WhatsApp). O custo por instrução reenviada ao PSP entra só para Pix
    Automático, multiplicado pelas tentativas que a janela do BACEN ainda
    permite — recuperar na 3ª tentativa custa 3× o PSP de recuperar na 1ª, e
    até o Sprint 5 o e-Profit ignorava isso inteiramente (Gap 6).

    É uma PREVISÃO, e o nome diz: este nó roda antes de `decide_recovery`, então
    ainda não se sabe se haverá retentativa nem quantas. Usa o teto da janela,
    que é o pior caso — subestimar o custo faria o agente agir onde não vale.
    O custo REALIZADO por ciclo (tentativas efetivamente disparadas) é outro
    número, e é o que o Sprint 6 grava no log.

    Com `CRAI_CUSTO_TENTATIVA_PIX` não configurada o segundo termo é zero e o
    resultado é idêntico ao de antes deste sprint — requisito, não coincidência:
    as métricas de e-Profit publicadas no README não podem mudar em silêncio.
    """
    custo = custo_intervencao()
    if state.get("payment_method") == "pix_automatico":
        usadas = state.get("retry_count") or 0
        restantes = max(0, MAX_TENTATIVAS_PIX - usadas)
        custo += custo_tentativa_pix() * restantes
    return round(custo, 4)


async def check_anomaly(state: AgentState) -> AgentState:
    result = await _detector.check(state["customer_id"], state["payment_event"])

    # Anomalia ajusta o score de recuperação para baixo
    if result["is_anomaly"]:
        adjusted_score = max(0, int(state["recovery_score"] * 0.7))
        adjusted_p = state.get("p_recovery", 0.5) * 0.7
    else:
        adjusted_score = state["recovery_score"]
        adjusted_p = state.get("p_recovery", 0.5)

    # Recalcular e-Profit com score ajustado
    ltv = state.get("ltv_estimated", state["amount"] * 6)
    cost = _custo_previsto_do_ciclo(state)
    new_eprofit = round(float(adjusted_p * ltv - cost), 2)

    print(f"[AGENT] Anomalia ({result['method']}): {result['is_anomaly']} | "
          f"erro: {result['error']:.4f} | threshold: {result['threshold']:.4f}")
    if result["is_anomaly"] and result.get("top_features"):
        top = ", ".join(f["feature"] for f in result["top_features"])
        print(f"[AGENT] Features anômalas: {top}")

    return {
        **state,
        "is_anomalous": result["is_anomaly"],
        "reconstruction_error": result["error"],
        "anomaly_explanation": result.get("top_features", []),
        "recovery_score": adjusted_score,
        "p_recovery": round(adjusted_p, 4),
        "eprofit": new_eprofit,
        "recommend_action": bool(new_eprofit > 0),
    }


async def infer_payday(state: AgentState) -> AgentState:
    if state["failure_cause"] != "insufficient_funds":
        return {**state, "optimal_retry_at": None}
    # TODO(pós-demo): infer_payday roda 2x por recuperação — este nó e, de novo,
    # dentro de PixAutomaticoRetryPolicy._consultar_payday. E os outputs
    # gravados aqui (optimal_retry_at, confidence, profile_type) não são lidos
    # por nenhum nó ativo: a política consulta o modelo por conta própria.
    # NÃO refatorar antes da demo — o ganho é performance, o risco é quebrar o
    # caminho feliz. Ver P2-10 no roadmap do README.
    window = await _payday.predict_next_window(state["customer_id"])
    print(f"[AGENT] Payday ({window.get('method', 'heuristic')}): "
          f"{window['timestamp'].strftime('%d/%m %H:%M')} | perfil: {window['profile']} | "
          f"confiança: {window['confidence']:.0%}")
    return {**state, "optimal_retry_at": window["timestamp"], "confidence": window["confidence"],
            "profile_type": window["profile"]}


# Causas em que retentar a cobrança pode, mecanicamente, resolver.
#
# O conjunto é o mesmo de antes do Sprint 3 — `insufficient_funds` e
# `processing_error` —, mas agora ele é ALCANÇÁVEL pelas outras duas pontas: até
# aqui toda falha de Pix chegava como `insufficient_funds`, então a linha de
# baixo nunca reprovava nada num evento de Pix. Com o PIX_CODE_MAP, os dois
# casos não retentáveis passam a existir de verdade:
#
#   limit_exceeded         o valor excede o teto que o pagador configurou.
#                          Nenhuma das 3 tentativas do BACEN passa enquanto o
#                          teto não subir, e só o cliente pode subi-lo.
#   authorization_revoked  não há mais mandato. Não existe cobrança a reenviar.
#
# Gastar tentativa regulada em qualquer um dos dois é queimar um direito do
# recebedor em algo que não pode dar certo.
#
# Cartão expirado / recusado / do_not_honor seguem fora por outro motivo (o
# cliente precisa agir), e continuam válidos enquanto o vocabulário do Stripe
# existir no caminho legado.
CAUSAS_RETENTAVEIS = set(CAUSAS_RETENTAVEIS_PIX)


async def decide_recovery(state: AgentState) -> AgentState:
    """Módulo 5 — nó de raciocínio (ReAct) que decide a estratégia de recuperação.

    Avalia o contexto acumulado pelos Módulos 1-3 (score + e-Profit + SHAP +
    anomalia + payday) e decide entre duas vias — nunca aciona um humano:

        retry_automatico   : a causa é retentável; reagenda a cobrança na
                             janela de liquidez prevista pelo Módulo 3.
        mensagem_pagamento : contatar o cliente com uma mensagem personalizada
                             (LLM) e um link de pagamento — Pix Automático como
                             primeira opção, boleto como fallback.

    Cada passo do raciocínio é logado em PT-BR para auditoria.
    """
    causa = state["failure_cause"]
    score = state.get("recovery_score", 0)
    eprofit = state.get("eprofit", 0.0)
    anomala = state.get("is_anomalous", False)
    metodo = state.get("payment_method", "card")
    # O contador cru do checkpoint pode pertencer a uma janela já encerrada.
    # `_janela_vigente` é quem decide se ele ainda vale — e o resultado é
    # gravado de volta no state, para que `schedule_retry_pix` leia o mesmo
    # número que esta decisão usou, em vez de recalcular a expiração.
    momento = _agora()
    usadas, prazo_vigente = _janela_vigente(state, momento)

    # Quantas retentativas ainda cabem depende do meio de pagamento:
    #   pix_automatico → o BACEN concede até 3 na janela de 7 dias, e desistir
    #                    na primeira jogaria fora tentativas a que o recebedor
    #                    tem direito. Quem valida o limite exato é a
    #                    PixAutomaticoRetryPolicy — aqui só evitamos entrar no
    #                    nó de retentativa quando a janela já acabou.
    #   cartão         → ZERO: a recobrança automática de cartão saiu do
    #                    pipeline ativo na Fase 3 (ver crai/dunning/legacy_card/).
    #                    Um evento de cartão que chegue aqui vai direto para a
    #                    mensagem personalizada, nunca para uma retentativa.
    limite = MAX_TENTATIVAS_PIX if metodo == "pix_automatico" else 0
    ainda_cabe = usadas < limite
    retentavel = causa in CAUSAS_RETENTAVEIS and ainda_cabe

    causa_legivel = CAUSA_LEGIVEL.get(causa, causa)
    raciocinio = [
        f"Observação: causa={causa_legivel}, score={score}/100, "
        f"e-Profit=R$ {eprofit:.2f}, anomalia={'sim' if anomala else 'não'}.",
    ]

    if retentavel:
        if metodo == "pix_automatico":
            quando = (f"na janela regulada do BACEN ({usadas}/{MAX_TENTATIVAS_PIX} "
                      f"tentativas usadas), mirando a liquidez prevista (Módulo 3)")
        else:
            quando = ("na janela de liquidez prevista (Módulo 3)"
                      if causa == "insufficient_funds" else "imediatamente")
        raciocinio.append(
            f"Pensamento: '{causa_legivel}' costuma ser resolvido por nova tentativa de "
            f"cobrança {quando} — insistir aqui tem retorno esperado positivo.")
        raciocinio.append("Decisão: nova tentativa automática.")
        estrategia = "retry_automatico"
    else:
        if causa not in CAUSAS_RETENTAVEIS:
            # A explicação por causa vem do vocabulário (pix_codes), e não de um
            # texto genérico: "o cliente precisa agir" não diz ao operador — nem
            # à banca — se o que falta é aumentar o limite do Pix ou reautorizar
            # a recorrência, que são ações diferentes.
            detalhe = EXPLICACAO_DA_CAUSA.get(
                causa, "o cliente precisa agir (atualizar cartão ou pagar por outro meio)")
            motivo = f"'{causa_legivel}' não se resolve por retentativa — {detalhe}"
        elif metodo == "pix_automatico":
            motivo = (f"as {MAX_TENTATIVAS_PIX} tentativas da janela regulada do "
                      f"BACEN já foram usadas")
        else:
            motivo = ("a recobrança automática de cartão está fora do pipeline "
                      "ativo [CARTAO-DESATIVADO] — só resta contatar o cliente")
        urgencia = "alta" if (anomala or score < 40) else "normal"
        raciocinio.append(
            f"Pensamento: {motivo}. Contatar com mensagem personalizada; "
            f"urgência {urgencia} pelo score/anomalia.")
        raciocinio.append("Decisão: mensagem de pagamento (Pix Automático → boleto).")
        estrategia = "mensagem_pagamento"

    print(f"[AGENT] Estratégia (Módulo 5): {estrategia}")
    for passo in raciocinio:
        print(f"[RACIOCÍNIO] {passo}")

    return {**state, "estrategia": estrategia, "raciocinio": raciocinio,
            "retry_count": usadas, "pix_janela_ate": prazo_vigente}


async def schedule_retry_pix(state: AgentState) -> AgentState:
    """Retentativa de PIX AUTOMÁTICO — janela regulada pelo BACEN.

    Só é alcançável quando payment_method == "pix_automatico". Nunca chama
    SmartBackoff: o backoff exponencial estouraria o limite de 3 tentativas
    dentro dos 7 dias corridos.

    A política devolve o plano completo das tentativas restantes de uma vez, e
    não uma por execução: são todas instruções de pagamento a reenviar dentro
    da mesma janela legal. Por isso `retry_count` passa a refletir todas as
    tentativas comprometidas — um novo evento do mesmo cliente na mesma janela
    encontra o limite já gasto e cai direto na mensagem personalizada.

    "Na mesma janela" é a parte que precisa estar escrita no state, e não só
    na cabeça de quem leu o BACEN. `pix_janela_ate` diz até quando o contador
    vale; enquanto ele valer, esta função **continua** a janela aberta em vez
    de abrir outra — reancorar o vencimento a cada webhook empurraria o prazo
    indefinidamente e transformaria "3 por janela" em "3 por webhook".
    """
    momento = _agora()
    usadas, prazo_vigente = _janela_vigente(state, momento)

    # Vencimento que ancora a janela: o da cobrança que a abriu, se ela ainda
    # está aberta; senão, agora — este evento é o começo de uma janela nova.
    vencimento = inicio_da_janela(prazo_vigente) if prazo_vigente else momento
    prazo_final = fim_da_janela(vencimento)

    tentativas = await _pix_retry.schedule(
        customer_id=state["customer_id"],
        valor_original=state["amount"],
        vencimento=vencimento,
        tentativas_usadas=usadas,
        agora=momento,
    )

    if not tentativas:
        print("[AGENT] Pix Automático: janela regulada esgotada — sem nova tentativa")
        return {**state, "next_retry_at": None, "retry_exhausted": True,
                "retry_count": usadas, "pix_janela_ate": prazo_vigente,
                "pix_retry_schedule": []}

    plano = [
        {"numero": t.numero, "quando": t.quando, "valor": t.valor, "origem": t.origem}
        for t in tentativas
    ]
    print(f"[AGENT] Pix Automático: {len(plano)} tentativa(s) na janela BACEN | "
          f"próxima: {tentativas[0].quando.strftime('%d/%m %H:%M')} ({tentativas[0].origem})")

    # O plano sai do grafo e vai para a camada de estado (Sprint 2). Sem isto,
    # as tentativas 2 e 3 morrem aqui: o `ainvoke` termina, o checkpoint guarda
    # o plano num formato privado do LangGraph, e nada volta na data certa para
    # reenviar a instrução de pagamento. Ver `crai/dunning/retry_scheduler.py`.
    retry_state.save_retry_state(
        customer_id=state["customer_id"],
        valor_original=state["amount"],
        tentativas=plano,
        pix_janela_ate=prazo_final,
        e2e_id=state.get("invoice_id"),
        tenant_id=state.get("tenant_id"),
    )

    # A primeira tentativa sai AGORA quando já é devida. "Devida" é a data que a
    # política calculou, não o momento em que o webhook chegou: a janela do
    # recebedor abre no dia seguinte ao vencimento (as duas janelas automáticas
    # do dia são do PSP do pagador). Numa cobrança que falhou há mais de um dia
    # a primeira tentativa está vencida e sai daqui; numa que acabou de falhar
    # ela é de amanhã, e quem a dispara é o agendador — o mesmo caminho das
    # tentativas 2 e 3, sem código paralelo.
    registro = retry_state.get_retry_state(state["customer_id"],
                                          tenant_id=state.get("tenant_id"))
    if registro:
        for devida in retry_state.tentativas_devidas(registro, momento):
            await disparar_tentativa(registro, devida, momento)

    return {**state, "next_retry_at": tentativas[0].quando, "retry_exhausted": False,
            "retry_count": usadas + len(tentativas), "pix_janela_ate": prazo_final,
            "pix_retry_schedule": plano}


async def trigger_dunning(state: AgentState) -> AgentState:
    """Executa a mensagem personalizada via LLM (LangGraph) — nunca aciona humano."""
    p_recovery = state.get("p_recovery", state.get("recovery_score", 50) / 100)
    result = await _dunning.run_campaign(state["customer_id"], state["failure_cause"],
                                          p_recovery, state["amount"])
    return {
        **state,
        "dunning_sent": result["sent"],
        "channel": result["channel"],
        "metodo_pagamento": result["payment_method"],
        "message_sent": result["message"],
    }


def success_fee(amount: float, recovered: bool) -> float:
    """O que a CRAI cobra por este ciclo. Zero quando não houve recuperação.

    O percentual vem de `crai/config.py` desde o Sprint 5 — é parâmetro de
    contrato, não constante de código. Esta função continua sendo o ÚNICO ponto
    que aplica o fee, porque o ciclo fecha em dois lugares (este nó, para o
    caso perdido/enviado, e `_fechar_ciclo_recuperado` na API, quando a
    confirmação do PSP chega) e duas contas separadas divergiriam como
    diferença de faturamento.
    """
    return round(amount * success_fee_pct(), 2) if recovered else 0.0


def tentativas_ja_disparadas(state: AgentState) -> int:
    """Quantas instruções de pagamento deste ciclo já saíram para o PSP.

    Vem do `retry_state`, que é quem sabe o que foi DISPARADO — o
    `retry_count` do checkpoint conta o que foi COMPROMETIDO pela política, que
    é outra coisa. A diferença é exatamente o custo que o Gap 6 pedia: um
    plano de 3 tentativas em que só a primeira saiu custou uma, não três.
    """
    registro = retry_state.get_retry_state(
        state.get("customer_id", ""), tenant_id=state.get("tenant_id"))
    if not registro:
        return 0
    return sum(1 for t in registro.get("tentativas") or [] if t.get("disparada_em"))


async def update_roi_dashboard(state: AgentState) -> AgentState:
    fee = success_fee(state["amount"], bool(state.get("recovered")))
    eprofit = state.get("eprofit", 0)
    recovered_icon = "[OK]" if state.get("recovered") else "[X]"
    print(f"[ROI] {recovered_icon} R$ {state['amount']:.2f} | taxa R$ {fee:.2f} | e-Profit R$ {eprofit:.2f}")
    crm_result = await _hubspot.register_recovery_cycle(state)
    print(f"[HUBSPOT] Contact {crm_result['hubspot_contact_id']} | Deal {crm_result['hubspot_deal_id']} | {crm_result['stage']}\n")

    # A linha do dataset (Sprint 6). Gravada aqui porque este é o último nó nos
    # DOIS caminhos do grafo — o que retentou e o que mandou mensagem —, e é o
    # ponto em que features, decisão e custo até agora já existem. O desfecho
    # entra depois, quando a confirmação do PSP chega; até lá `recovered` é 0,
    # que é a verdade: em produção ninguém sabe ainda.
    recovery_log.registrar_ciclo(state, tentativas_ja_disparadas(state))
    return state


# Causa DOMINANTE de uma cobrança recorrente de Pix Automático que falha: no
# fluxo do BACEN, as duas janelas automáticas do dia do vencimento já tentaram
# debitar a conta do pagador, e se ambas falharam a ausência de saldo é a
# hipótese mais provável — é também o caso em que o Payday Engine agrega.
#
# Até o Sprint 3 esta constante era atribuída a TODA falha de Pix, e aí ela
# deixava de ser hipótese para virar afirmação sobre um pagador de quem não se
# sabia nada. Hoje quem decide é o `PIX_CODE_MAP` sobre o motivo que o PSP
# enviou (ver `crai/agent/pix_codes.py`); a constante segue aqui porque é a
# causa mais frequente e continua sendo o rótulo do caminho simulado.
CAUSA_PIX_FALHA = "insufficient_funds"


def _extract_features(
    event: dict, amount: float, payment_method: str = "card",
    customer_id: str = "",
) -> dict:
    """Extrai as 11 features + LTV para o classificador, conforme a origem do evento.

    `customer_id` é a semente do perfil sintético. Ele vem de fora, e não de
    dentro do evento, porque é o mesmo id que identifica o checkpoint do
    LangGraph — ver `crai/api/app.py::_thread_id` (P0-6b).
    """
    if payment_method == "pix_automatico":
        return _features_pix(event, amount, customer_id)
    return _features_cartao(event, amount)


def _features_cartao(event: dict, amount: float) -> dict:
    """Features a partir do payload cru do Stripe (cartão)."""
    charge = event.get("data", {}).get("object", {})

    invoice_amount = charge.get("amount", 0) / 100 if charge.get("amount", 0) > 100 else amount
    perfil = _perfil_simulado(charge.get("customer", ""), invoice_amount)

    return {
        **perfil,
        "gateway_error_code": charge.get("failure_code") or "processing_error",
        "card_brand": (charge.get("payment_method_details") or {}).get("brand") or "visa",
        "attempt_count": charge.get("attempt_count", 1),
    }


def _features_pix(event: dict, amount: float, customer_id: str = "") -> dict:
    """Features a partir do evento JÁ normalizado de Pix Automático.

    O evento normalizado tem cinco campos de dado e nenhum deles identifica o
    pagador — a chave Pix nunca chega até aqui. O identificador usado é o da
    autorização de recorrência, que é opaco.

    A semente do perfil é o `customer_id`, e não `event["id_recorrencia"]`
    (P0-6b): para um pagador anônimo o id da recorrência é string vazia
    enquanto o resto do pipeline usa o id derivado do evento, e o mesmo webhook
    acabava gerando dois perfis sintéticos diferentes.
    """
    invoice_amount = event.get("valor") or amount
    perfil = _perfil_simulado(customer_id or event.get("id_recorrencia", ""), invoice_amount)

    return {
        **perfil,
        # Sprint 3: a causa vem do motivo que o PSP enviou, traduzido pelo
        # PIX_CODE_MAP. Um evento sem motivo (ou com motivo não reconhecido)
        # cai no default seguro do mapa, que é `processing_error` — e não mais
        # `insufficient_funds`, que afirmava falta de saldo sem saber.
        "gateway_error_code": causa_do_codigo(event.get("codigo_falha")),
        # Não existe bandeira em Pix; o encoder trata valor desconhecido.
        "card_brand": "n/a",
        # As duas janelas automáticas do dia são do PSP do pagador e não contam
        # como tentativa do recebedor.
        "attempt_count": 1,
    }


def _perfil_simulado(chave_cliente: str, invoice_amount: float) -> dict:
    """Tenure, histórico e LTV do cliente, pelo provedor configurado.

    O NOME CONTINUA `_perfil_simulado` porque é o que ele descreve hoje: sem
    fonte real configurada, o provedor devolve o perfil sintético, com os mesmos
    valores para a mesma semente que antes do Sprint 7. O que mudou é que a
    fonte virou configuração — `_perfil_provider` (topo deste módulo) pode ser
    trocado por um provedor de banco sem que esta função, `_features_pix` ou o
    nó de diagnóstico mudem de forma.

    Quatro das doze entradas do classificador não vêm do evento do PSP: tenure,
    histórico de pagamento, falhas em 90 dias e ticket médio. Elas vêm do
    negócio, e enquanto o negócio não estiver conectado, são fabricadas. Isso
    está documentado em `crai/agent/README_treino.md` como a limitação que é,
    não escondido atrás de um número plausível.
    """
    return _perfil_provider.get_perfil(chave_cliente, invoice_amount)

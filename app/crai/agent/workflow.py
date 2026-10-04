"""crai/agent/workflow.py — Nós do pipeline de churn involuntário."""

import logging
from datetime import datetime, timedelta, timezone
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
from .pix_codes import (CAUSA_LEGIVEL, CAUSA_REVOGADA, CAUSAS_RETENTAVEIS_PIX,
                        EXPLICACAO_DA_CAUSA, causa_do_codigo)
from ..churn_voluntary import retention_log as trilha
from ..ml.failure_classifier import MODELS_DIR as _MODELS_DIR, FailureClassifier
from ..ml.anomaly_detector import AnomalyDetector
from ..ml.payday_inference import PaydayInference
from .perfil_provider import provedor_padrao
from ..dunning.pix_automatico_retry import (
    MAX_TENTATIVAS as MAX_TENTATIVAS_PIX,
    ORIGEM_PAYDAY,
    PixAutomaticoRetryPolicy,
    fim_da_janela,
    inicio_da_janela,
)
from ..dunning.dunning_engine import DunningEngine
from ..dunning import canal_involuntario, ciclo_cobranca, configuracao, recovery_log, retry_state
from ..dunning.retry_scheduler import disparar_tentativa
from ..integrations.hubspot_crm import HubSpotCRM

# Não há import de smart_backoff aqui: a retentativa de cartão saiu do pipeline
# ativo na Fase 3 e vive isolada em crai/dunning/legacy_card/.

logger = logging.getLogger(__name__)

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

# Identidade dos artefatos para a trilha do Art. 20 (`modelo_versao`):
# `<treinado_em>#<hash>`, calculada uma vez por processo. None quando o
# modelo não carregou — a trilha grava NULL, não inventa versão.
_VERSAO_CLASSIFICADOR = trilha.versao_do_artefato(
    getattr(_classifier, "meta", None),
    _MODELS_DIR / "xgb_failure_classifier.joblib", _MODELS_DIR / "rf_failure_classifier.joblib",
) if getattr(_classifier, "is_fitted", False) else None
_VERSAO_PAYDAY = trilha.versao_do_artefato(
    getattr(_payday, "meta", None), _MODELS_DIR / "payday_lstm.pt",
) if getattr(_payday, "is_fitted", False) else None

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
    """Quantas tentativas deste ciclo já foram EXECUTADAS, e até quando vai a
    janela do BACEN dele — como o ciclo diz (R2, R3).

    Até a Etapa 1 esta função zerava o contador quando o prazo passava: o
    contador era por MANDATO, no checkpoint, e "prazo vencido" era a única
    forma de a cobrança do mês seguinte ganhar as 3 tentativas dela. Era
    também o que reabria a janela da MESMA cobrança depois de um reinício ou
    de uma falha tardia (diagnóstico de 28/09/2026, item 10).

    Agora o ciclo é por COBRANÇA: `open_cycle` copia para o state o contador
    executado e a `janela_fim` gravados na abertura do ciclo, e nada os
    reancora. Uma cobrança nova abre ciclo novo, com contador zero e janela
    própria — sem precisar zerar nada aqui. Uma falha da mesma cobrança
    depois do fim da janela chega com o contador que ela tem e o prazo que
    ela tem; quem responde "não cabe mais tentativa" é a
    `PixAutomaticoRetryPolicy`, pela aritmética da janela, e não um zero
    inventado aqui.

    Um state com contador e sem prazo (chamada direta, sem ciclo) é tratado
    como janela aberta — na dúvida, o limite regulatório aperta, nunca
    afrouxa. `agora` fica na assinatura por compatibilidade: a decisão de
    expiração saiu daqui.
    """
    return state.get("retry_count") or 0, state.get("pix_janela_ate")


def _ciclo_do_state(state: AgentState, momento: datetime) -> tuple[dict, dict]:
    """O ciclo de cobrança deste state, e o que o evento É para ele.

    1. `ciclo_id` já no state → é ele.
    2. Senão, a associação de `ciclo_cobranca.associar_falha` (regras D1/D2):
       o evento pode ser o resultado de uma tentativa disparada, uma falha
       tardia da mesma cobrança, ou cobrança nova.
    3. Cobrança nova → abre o ciclo. A janela do BACEN é gravada AQUI, uma vez
       (R3). Se o state inicial DECLARA `retry_count = N` ou `pix_janela_ate`
       sem ter ciclo (painel, `test_pipeline.py`, o `attempt_count` do
       Stripe, um teste que chama o nó direto), isso é o contador externo e
       autoritativo que `_run_involuntary_pipeline` sempre reconheceu (D10):
       o ciclo nasce com N tentativas `falhou` de origem `declarada` e com a
       janela declarada. Sem declaração, a janela ancora no instante da falha.

    A causa gravada na abertura vem do código do PSP (`codigo_falha` →
    `PIX_CODE_MAP`), e `diagnose_failure` a confirma com a feature que o
    classificador viu. Origem `webhook` (ou `declarada`) — nunca `plano`
    (B2-b): o caminho legado de `retry_state._ciclo_do_plano` não é deste
    fluxo.
    """
    if state.get("ciclo_id"):
        ciclo = ciclo_cobranca.ciclo_por_id(state["ciclo_id"])
        if ciclo is not None:
            return ciclo, {"tipo": ciclo_cobranca.NOVA, "ciclo": ciclo, "tentativa": None}

    evento = state.get("payment_event") or {}
    tenant = state.get("tenant_id")
    mandato = state["customer_id"]
    id_cobranca = evento.get("id_cobranca") or None
    e2e = state.get("invoice_id") or evento.get("e2e_id") or None

    assoc = ciclo_cobranca.associar_falha(tenant, mandato, id_cobranca, e2e, momento)
    if assoc["tipo"] != ciclo_cobranca.NOVA:
        return assoc["ciclo"], assoc
    if assoc["ciclo"] is not None:
        # I-4a: RETOMADA de um ciclo incompleto (aberto, sem decisão gravada e
        # sem tentativas): o diagnóstico roda de novo sobre o MESMO ciclo, com
        # a janela original — nenhum ciclo novo, nenhuma janela nova (R3).
        print(f"[CICLO] {mandato}: ciclo {assoc['ciclo']['id']} incompleto (sem decisão) — "
              f"retomando o diagnóstico, janela original mantida")
        return assoc["ciclo"], assoc

    declaradas = max(0, min(MAX_TENTATIVAS_PIX, int(state.get("retry_count") or 0)))
    janela_fim = ciclo_cobranca._data(state.get("pix_janela_ate"))
    janela_inicio = inicio_da_janela(janela_fim) if janela_fim else None
    origem = (ciclo_cobranca.ORIGEM_DECLARADA if (declaradas or janela_fim)
              else ciclo_cobranca.ORIGEM_WEBHOOK)
    if state.get("payment_method", "card") == "pix_automatico":
        causa = causa_do_codigo(evento.get("codigo_falha"))
    else:
        charge = (evento.get("data") or {}).get("object") or {}
        causa = charge.get("failure_code") or state.get("failure_cause") or "desconhecida"

    try:
        ciclo = ciclo_cobranca.abrir_ciclo(
            tenant, mandato, state.get("amount") or 0.0, causa, momento,
            id_cobranca=id_cobranca, e2e_falha_original=e2e,
            codigo_falha=evento.get("codigo_falha"),
            janela_inicio=janela_inicio, janela_fim=janela_fim, origem=origem,
        )
    except ciclo_cobranca.CicloJaExiste:
        # Outro processo abriu o mesmo ciclo entre a associação e o INSERT: a
        # `UNIQUE` falou. Reassociar encontra o ciclo dele.
        return _ciclo_do_state(state, momento)

    if declaradas:
        inicio = janela_inicio or momento
        ciclo_cobranca.agendar_tentativas(ciclo["id"], [
            {"numero": n, "quando": inicio, "valor": state.get("amount") or 0.0,
             "origem": ciclo_cobranca.ORIGEM_DECLARADA}
            for n in range(1, declaradas + 1)
        ], momento)
        for n in range(1, declaradas + 1):
            ciclo_cobranca.registrar_resultado(ciclo["id"], n, ciclo_cobranca.FALHOU, momento)
        print(f"[CICLO] {mandato}: ciclo {ciclo['id']} aberto com {declaradas} tentativa(s) "
              f"declarada(s) como executadas pelo chamador")
    return ciclo, {"tipo": ciclo_cobranca.NOVA, "ciclo": ciclo, "tentativa": None}


async def open_cycle(state: AgentState) -> AgentState:
    """Nó de entrada (Etapa 1, Bloco 2): abre ou reencontra o CICLO e copia
    para o state o que dele importa aos nós seguintes.

    `retry_count` passa a ser tentativas EXECUTADAS (R2) e `pix_janela_ate` a
    `janela_fim` gravada na primeira falha (R3) — reescritos a cada evento, e
    nunca acumulados no checkpoint. `ciclo_evento` diz ao roteador se este
    evento roda o diagnóstico (cobrança nova) ou só registra um resultado.
    """
    momento = _agora()
    ciclo, assoc = _ciclo_do_state(state, momento)
    executadas = ciclo_cobranca.tentativas_executadas(ciclo["id"])
    tentativa = assoc.get("tentativa") or {}
    print(f"[CICLO] {state['customer_id']}: ciclo {ciclo['id']} ({ciclo['estado']}) | "
          f"evento: {assoc['tipo']}"
          + (f" da tentativa {tentativa.get('numero')}" if tentativa else "")
          + f" | executadas {executadas}/{MAX_TENTATIVAS_PIX} | janela até "
          f"{str(ciclo['janela_fim'])[:16]}")
    return {
        **state,
        "ciclo_id": ciclo["id"],
        "ciclo_evento": assoc["tipo"],
        "ciclo_tentativa": tentativa.get("numero"),
        "retry_count": executadas,
        "pix_janela_ate": ciclo_cobranca._data(ciclo["janela_fim"]),
    }


async def registrar_resultado_do_evento(state: AgentState) -> AgentState:
    """O evento não é cobrança nova: grava o que ele diz sobre o ciclo e para.

    Diagnóstico, anomalia, liquidez e decisão NÃO rodam de novo (2.2). Dois
    casos:

      resultado_de_tentativa  a falha de uma retentativa disparada: a
                              tentativa recebe `falhou`, com o código do PSP
                              e o e2e do resultado (R2).
      falha_tardia            a mesma cobrança, de novo, sem tentativa em
                              aberto: NADA de tentativa nova (R3). Se a janela
                              do BACEN já encerrou e ainda há tentativa não
                              disparada, ela é cancelada (D7).

    Se depois disto o ciclo ficou sem tentativa pendente e sem pagamento, ele
    PRECISA de mensagem (R1) — a conclusão do ciclo entra no Bloco 3; aqui
    fica registrado no log. O state devolvido recarrega o diagnóstico a
    partir do ciclo, para o checkpoint desta execução não apagar o da
    abertura.
    """
    momento = _agora()
    ciclo_id = state["ciclo_id"]
    ciclo = ciclo_cobranca.ciclo_por_id(ciclo_id)
    evento = state.get("payment_event") or {}
    mandato = state["customer_id"]

    if (state.get("ciclo_evento") == ciclo_cobranca.RESULTADO_DE_TENTATIVA
            and state.get("ciclo_tentativa")):
        numero = state["ciclo_tentativa"]
        gravado = ciclo_cobranca.registrar_resultado(
            ciclo_id, numero, ciclo_cobranca.FALHOU, momento,
            codigo=evento.get("codigo_falha"), e2e=state.get("invoice_id"))
        print(f"[CICLO] {mandato}: tentativa {numero}/{MAX_TENTATIVAS_PIX} do ciclo {ciclo_id} "
              f"FALHOU ({evento.get('codigo_falha') or 'sem código'})"
              + ("" if gravado else " — resultado já estava registrado (reentrega)"))
    else:
        fim = ciclo_cobranca._data(ciclo["janela_fim"])
        if ciclo["estado"] == ciclo_cobranca.RECOBRANDO and fim is not None and fim < momento:
            canceladas = ciclo_cobranca.cancelar_pendentes(ciclo_id, "janela_encerrada", momento)
            print(f"[CICLO] {mandato}: falha da mesma cobrança DEPOIS da janela do BACEN — "
                  f"{canceladas} tentativa(s) não disparada(s) cancelada(s); nenhuma nova (R3)")
        else:
            print(f"[CICLO] {mandato}: falha da mesma cobrança registrada no ciclo {ciclo_id} — "
                  f"nenhuma tentativa nova (R3), diagnóstico não reexecutado")

    # R5: a falha com causa REVOGADA cancela o que ainda não saiu e manda a
    # mensagem na hora — não há mandato para tentar de novo.
    causa_evento = causa_do_codigo(evento.get("codigo_falha")) if evento.get("codigo_falha") else None
    concluido = None
    if causa_evento == CAUSA_REVOGADA and ciclo["estado"] == ciclo_cobranca.RECOBRANDO:
        canceladas = ciclo_cobranca.cancelar_pendentes(ciclo_id, "autorizacao_revogada", momento)
        print(f"[CICLO] {mandato}: autorização revogada — {canceladas} tentativa(s) "
              f"cancelada(s); mensagem na hora (R5)")
        concluido = await concluir_ciclo_por_resultado(ciclo_id, momento, motivo="autorizacao_revogada",
                                                       causa=CAUSA_REVOGADA)
    elif ciclo_cobranca.ciclo_precisa_de_mensagem(ciclo_id, momento):
        # R1: a mensagem nasce do RESULTADO da última tentativa (ou do fim da
        # janela), e não de um webhook novo de falha.
        concluido = await concluir_ciclo_por_resultado(ciclo_id, momento, motivo="tentativas_esgotadas")

    ciclo = ciclo_cobranca.ciclo_por_id(ciclo_id)
    score = ciclo.get("recovery_score")
    if concluido is not None:
        # O state desta execução carrega o que a mensagem produziu, como no grafo.
        return {**state, **{k: concluido.get(k) for k in (
            "dunning_sent", "channel", "metodo_pagamento", "message_sent", "mensagem_meta",
            "decisoes", "failure_cause", "recovery_score", "p_recovery", "eprofit",
            "estrategia", "amount")},
            "retry_count": ciclo_cobranca.tentativas_executadas(ciclo_id),
            "pix_janela_ate": ciclo_cobranca._data(ciclo["janela_fim"])}
    return {
        **state,
        "failure_cause": (ciclo["causa_original"] if ciclo["causa_original"] != "desconhecida"
                          else state.get("failure_cause")),
        "recovery_score": int(score) if score is not None else state.get("recovery_score"),
        "p_recovery": ciclo.get("p_recovery") if ciclo.get("p_recovery") is not None
        else state.get("p_recovery"),
        "eprofit": ciclo.get("eprofit") if ciclo.get("eprofit") is not None
        else state.get("eprofit"),
        "estrategia": ciclo.get("estrategia") or state.get("estrategia"),
        "amount": ciclo["valor"] or state.get("amount"),
        "retry_count": ciclo_cobranca.tentativas_executadas(ciclo_id),
        "pix_janela_ate": ciclo_cobranca._data(ciclo["janela_fim"]),
    }


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
            codigo = "unico_com_integracao"
            motivo = "único canal com integração de envio nesta fase (bot de WhatsApp)"
        elif canal in CANAIS_HUMANOS:
            escolhido = False
            codigo = "canal_humano"
            motivo = "canal humano: proibido pela invariante de escalonamento zero"
        elif canal == melhor:
            escolhido = False
            codigo = "melhor_sem_integracao"
            motivo = "maior e-Profit do comparativo, mas sem integração de envio nesta fase"
        else:
            escolhido = False
            codigo = "sem_integracao"
            motivo = "sem integração de envio nesta fase"
        linhas.append({
            "canal": canal,
            "eprofit": eprofits.get(canal),
            "melhor_eprofit": canal == melhor,
            "escolhido": escolhido,
            # `motivo` e a frase em pt-BR, como sempre foi — quem ja consumia
            # continua funcionando. `motivo_codigo` e a MESMA informacao como
            # identificador: sao quatro motivos fechados, decididos por
            # condicao, entao quem exibe pode escrever no idioma do leitor sem
            # que a tela invente explicacao nenhuma.
            "motivo": motivo,
            "motivo_codigo": codigo,
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

    # A causa que o classificador viu é a causa do ciclo (B2-b: o ciclo nasce
    # com a causa real, nunca `desconhecida`, quando o evento passa pelo grafo).
    if state.get("ciclo_id"):
        ciclo_cobranca.atualizar(state["ciclo_id"], causa_original=features["gateway_error_code"])

    shap_readable = result["shap_explanation"].get("readable", "")
    print(f"[AGENT] Diagnóstico ({result['method']}): "
          f"score {result['recovery_score']}/100 | "
          f"e-Profit R$ {result['eprofit']:.2f} | "
          f"ação: {'SIM' if result['recommend_action'] else 'NÃO'}")
    if shap_readable:
        print(f"[SHAP]  {shap_readable}")

    # Trilha do Art. 20: o diagnóstico é a decisão de risco do involuntário.
    # As contribuições são o SHAP JÁ calculado pelo classificador — k=5, no
    # momento da decisão, nunca recalculado depois. Sem modelo (heurística),
    # `modelo="regra"` e contribuições NULL: não se inventa contribuição.
    com_modelo = result.get("method") != "heuristic"
    contribuicoes = [
        {"feature": f.get("feature"), "contribution_pct": f.get("contribution_pct"),
         "direcao": f.get("direction")}
        for f in (result.get("shap_explanation") or {}).get("features", [])[:5]
    ] if com_modelo else None
    saida_risco = {"recovery_score": result["recovery_score"],
                   "p_recovery": result.get("p_recovery"),
                   "eprofit": result.get("eprofit"),
                   "recommend_action": result.get("recommend_action")}
    if not com_modelo:
        saida_risco["regra"] = "FailureClassifier.heuristica"
    decisao_risco = trilha.decisao(
        state.get("tenant_id"), state["customer_id"], trilha.DOMINIO_INVOLUNTARIO,
        trilha.TIPO_RISCO, "failure_classifier" if com_modelo else trilha.MODELO_REGRA,
        modelo_versao=_VERSAO_CLASSIFICADOR if com_modelo else None,
        entradas=features, saida=saida_risco, contribuicoes=contribuicoes or None)

    return {
        **trilha.anotar_decisao(state, decisao_risco),
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


# O desconto por anomalia. Quando o detector marca a cobrança como fora do
# padrão do cliente, a pontuação e a probabilidade do diagnóstico são
# multiplicadas por `FATOR_COM_ANOMALIA`, e o retorno esperado é recalculado. O
# percentual é o que a explicação do Art. 20 e a tela dizem ("reduzida em 30%").
FATOR_COM_ANOMALIA = 0.7
DESCONTO_POR_ANOMALIA_PCT = 30


async def check_anomaly(state: AgentState) -> AgentState:
    result = await _detector.check(state["customer_id"], state["payment_event"])

    # Anomalia ajusta o score de recuperação para baixo
    if result["is_anomaly"]:
        adjusted_score = max(0, int(state["recovery_score"] * FATOR_COM_ANOMALIA))
        adjusted_p = state.get("p_recovery", 0.5) * FATOR_COM_ANOMALIA
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
        # O que o diagnóstico tinha dito, guardado só quando houve desconto: é
        # o "de quanto" da explicação (`decide_recovery` grava na trilha).
        "recovery_score_sem_desconto": state["recovery_score"] if result["is_anomaly"] else None,
        "eprofit_sem_desconto": state.get("eprofit") if result["is_anomaly"] else None,
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
    # `usadas` são as tentativas EXECUTADAS do ciclo (R2), copiadas para o
    # state por `open_cycle`; `prazo_vigente` é a janela gravada na primeira
    # falha (R3). Uma janela já encerrada não zera nada aqui: a política
    # responde "não cabe" pela aritmética dela.
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
        # "->" em ASCII, e não a seta desenhada (U+2192): este texto vai para o
        # `print` logo abaixo, e com a saída redirecionada para arquivo no Windows
        # (cp1252) a seta derrubava a requisição com UnicodeEncodeError (N-12).
        raciocinio.append("Decisão: mensagem de pagamento (Pix Automático -> boleto).")
        estrategia = "mensagem_pagamento"

    print(f"[AGENT] Estratégia (Módulo 5): {estrategia}")
    for passo in raciocinio:
        print(f"[RACIOCÍNIO] {passo}")

    # O que o caminho de mensagem vai precisar DEPOIS da 3ª tentativa, sem
    # checkpoint: fica no ciclo.
    if state.get("ciclo_id"):
        ciclo_cobranca.atualizar(
            state["ciclo_id"], momento, estrategia=estrategia,
            recovery_score=trilha._num(score), p_recovery=trilha._num(state.get("p_recovery")),
            eprofit=trilha._num(eprofit),
            # I-4a: a DECISÃO está gravada — na mesma escrita. Sem esta marca o
            # ciclo é "incompleto" e o reenvio do PSP retoma o diagnóstico.
            decidido_em=momento.isoformat())

    # Trilha do Art. 20: é regra (ReAct sobre causa + janela), sem modelo.
    saida = {"estrategia": estrategia, "regra": "decide_recovery",
             "motivo_da_regra": raciocinio[1].removeprefix("Pensamento: ")}
    # Houve desconto por anomalia: a pontuação e o retorno que entram aqui já são
    # os reduzidos. A saída guarda o percentual e os valores de ANTES, para a
    # explicação dizer de quanto para quanto (as chaves a mais não mudam o
    # schema da trilha: `saida` é um JSON).
    antes = state.get("recovery_score_sem_desconto")
    if anomala and antes is not None:
        saida[trilha.CHAVE_DESCONTO_PCT] = DESCONTO_POR_ANOMALIA_PCT
        saida[trilha.CHAVE_PONTUACAO_ANTES] = trilha._num(antes)
        if state.get("eprofit_sem_desconto") is not None:
            saida[trilha.CHAVE_RETORNO_ANTES] = trilha._num(state["eprofit_sem_desconto"])
    dec = trilha.decisao(
        state.get("tenant_id"), state.get("customer_id"), trilha.DOMINIO_INVOLUNTARIO,
        trilha.TIPO_RETENTATIVA, trilha.MODELO_REGRA,
        entradas={"failure_cause": causa, "recovery_score": trilha._num(score),
                  "eprofit": trilha._num(eprofit), "is_anomalous": bool(anomala),
                  "payment_method": metodo, "tentativas_usadas": usadas,
                  "limite_tentativas": limite},
        saida=saida)
    return trilha.anotar_decisao(
        {**state, "estrategia": estrategia, "raciocinio": raciocinio,
         "retry_count": usadas, "pix_janela_ate": prazo_vigente}, dec)


async def schedule_retry_pix(state: AgentState) -> AgentState:
    """Retentativa de PIX AUTOMÁTICO — janela regulada pelo BACEN.

    Só é alcançável quando payment_method == "pix_automatico". Nunca chama
    SmartBackoff: o backoff exponencial estouraria o limite de 3 tentativas
    dentro dos 7 dias corridos.

    A política devolve o plano completo das tentativas restantes de uma vez, e
    não uma por execução: são todas instruções de pagamento a reenviar dentro
    da mesma janela legal. Elas vão para `tentativas_cobranca` como
    PENDENTES, e `retry_count` continua sendo o que foi EXECUTADO (R2): até a
    Etapa 1 este nó gravava `usadas + len(plano)` — tentativas comprometidas
    contadas como usadas —, e era isso que fazia a falha da tentativa 1
    encontrar "3 usadas" e disparar a mensagem com zero tentativa disparada
    (diagnóstico de 28/09/2026, item 1).

    A janela é a do CICLO, gravada na primeira falha (R3): `janela_inicio` é
    o vencimento que ancora as datas da política e `janela_fim` é o prazo.
    Nada aqui reancora — nem um segundo evento, nem um reinício.
    """
    momento = _agora()
    ciclo, _ = _ciclo_do_state(state, momento)
    ciclo_id = ciclo["id"]
    usadas = ciclo_cobranca.tentativas_executadas(ciclo_id)
    vencimento = ciclo_cobranca._data(ciclo["janela_inicio"]) or momento
    prazo_final = ciclo_cobranca._data(ciclo["janela_fim"]) or fim_da_janela(vencimento)
    prazo_vigente = prazo_final

    tentativas = await _pix_retry.schedule(
        customer_id=state["customer_id"],
        valor_original=state["amount"],
        vencimento=vencimento,
        tentativas_usadas=usadas,
        agora=momento,
    )

    if not tentativas:
        print("[AGENT] Pix Automático: janela regulada esgotada — sem nova tentativa")
        dec = trilha.decisao(
            state.get("tenant_id"), state["customer_id"], trilha.DOMINIO_INVOLUNTARIO,
            trilha.TIPO_RETENTATIVA, trilha.MODELO_REGRA,
            entradas={"tentativas_usadas": usadas, "limite_tentativas": MAX_TENTATIVAS_PIX,
                      "amount": trilha._num(state["amount"]), "payment_method": "pix_automatico"},
            saida={"tentativas": [], "regra": "PixAutomaticoRetryPolicy.janela_esgotada",
                   "motivo_da_regra": "a janela regulada do BACEN não comporta nova tentativa"})
        return trilha.anotar_decisao(
            {**state, "ciclo_id": ciclo_id, "next_retry_at": None, "retry_exhausted": True,
             "retry_count": usadas, "pix_janela_ate": prazo_vigente,
             "pix_retry_schedule": []}, dec)

    plano = [
        {"numero": t.numero, "quando": t.quando, "valor": t.valor, "origem": t.origem}
        for t in tentativas
    ]
    print(f"[AGENT] Pix Automático: {len(plano)} tentativa(s) na janela BACEN | "
          f"próxima: {tentativas[0].quando.strftime('%d/%m %H:%M')} ({tentativas[0].origem})")

    # O plano sai do grafo e vai para o ciclo (`tentativas_cobranca`, como
    # pendentes). Sem isto, as tentativas 2 e 3 morrem aqui: o `ainvoke`
    # termina e nada volta na data certa para reenviar a instrução de
    # pagamento. Ver `crai/dunning/retry_scheduler.py`. `ciclo_id` explícito:
    # o grafo nunca passa pelo caminho legado de `retry_state` (B2-b).
    retry_state.save_retry_state(
        customer_id=state["customer_id"],
        valor_original=state["amount"],
        tentativas=plano,
        pix_janela_ate=prazo_final,
        e2e_id=state.get("invoice_id"),
        tenant_id=state.get("tenant_id"),
        ciclo_id=ciclo_id,
    )

    # A primeira tentativa sai AGORA quando já é devida. "Devida" é a data que a
    # política calculou, não o momento em que o webhook chegou: a janela do
    # recebedor abre no dia seguinte ao vencimento (as duas janelas automáticas
    # do dia são do PSP do pagador). Numa cobrança que falhou há mais de um dia
    # a primeira tentativa está vencida e sai daqui; numa que acabou de falhar
    # ela é de amanhã, e quem a dispara é o agendador — o mesmo caminho das
    # tentativas 2 e 3, sem código paralelo.
    registro = retry_state.registro_do_ciclo(ciclo_id)
    if registro:
        for devida in retry_state.tentativas_devidas(registro, momento):
            await disparar_tentativa(registro, devida, momento)

    # Trilha do Art. 20: QUANDO tentar de novo. As datas vêm do modelo de
    # liquidez (payday) ou, no fallback, de uma distribuição uniforme — e a
    # linha diz qual dos dois foi.
    origem_datas = tentativas[0].origem
    pelo_modelo = origem_datas == ORIGEM_PAYDAY
    saida_plano = {"tentativas": [{"numero": t["numero"], "quando": t["quando"],
                                   "valor": t["valor"], "origem": t["origem"]} for t in plano],
                   "origem_das_datas": origem_datas, "janela_ate": prazo_final}
    if not pelo_modelo:
        saida_plano["regra"] = "PixAutomaticoRetryPolicy.fallback_uniforme"
    dec = trilha.decisao(
        state.get("tenant_id"), state["customer_id"], trilha.DOMINIO_INVOLUNTARIO,
        trilha.TIPO_RETENTATIVA, "payday_inference" if pelo_modelo else trilha.MODELO_REGRA,
        modelo_versao=_VERSAO_PAYDAY if pelo_modelo else None,
        entradas={"tentativas_usadas": usadas, "limite_tentativas": MAX_TENTATIVAS_PIX,
                  "amount": trilha._num(state["amount"]), "payment_method": "pix_automatico",
                  "vencimento": vencimento},
        saida=saida_plano)
    return trilha.anotar_decisao(
        {**state, "ciclo_id": ciclo_id, "next_retry_at": tentativas[0].quando,
         "retry_exhausted": False,
         # EXECUTADAS depois do disparo do que já era devido — nunca o plano.
         "retry_count": ciclo_cobranca.tentativas_executadas(ciclo_id),
         "pix_janela_ate": prazo_final,
         "pix_retry_schedule": plano}, dec)


async def trigger_dunning(state: AgentState) -> AgentState:
    """A mensagem de dentro do grafo (causa não retentável; janela esgotada) —
    nunca aciona humano.

    Com ciclo (sempre, no grafo: `open_cycle` o põe no state), a mensagem
    passa pelo MESMO caminho que a mensagem depois da 3ª tentativa (Etapa 2):
    `preparar_mensagem` gera as 3 sugestões e segue o modo da empresa. No modo
    escolha nada sai agora — o ciclo fica em `aguardando_escolha`. As decisões
    da campanha, quando a mensagem sai, entram no state e são gravadas por
    `update_roi_dashboard`, no fim do grafo, como sempre foram.

    Sem ciclo (chamada direta de teste) é o caminho de antes: uma mensagem só.
    """
    if state.get("ciclo_id"):
        ciclo = ciclo_cobranca.ciclo_por_id(state["ciclo_id"])
        enviado = None
        if ciclo is not None and ciclo["estado"] == ciclo_cobranca.RECOBRANDO:
            enviado = await preparar_mensagem(
                state["ciclo_id"], _agora(), motivo="grafo",
                causa=state.get("failure_cause"), persistir=False)
        if enviado is None:
            return {**state, "dunning_sent": False}
        return {**state, **{k: enviado.get(k) for k in (
            "dunning_sent", "channel", "metodo_pagamento", "message_sent", "mensagem_meta")},
            "decisoes": list(state.get("decisoes") or []) + list(enviado.get("decisoes") or [])}

    p_recovery = _p_recovery(state)
    result = await _dunning.run_campaign(state["customer_id"], state["failure_cause"],
                                          p_recovery, state["amount"],
                                          tenant_id=state.get("tenant_id"))
    return {
        **state,
        # As decisões da campanha (oferta + canal) entram na trilha do ciclo.
        "decisoes": list(state.get("decisoes") or []) + list(result.get("decisoes") or []),
        "dunning_sent": result["sent"],
        "channel": result["channel"],
        "metodo_pagamento": result["payment_method"],
        "message_sent": result["message"],
        "mensagem_meta": {"origem": result.get("origem") or "gerado",
                          "codigo": result.get("codigo_template") or "",
                          "valor": state["amount"],
                          "link": result.get("portal_link") or "",
                          "metodo": result["payment_method"]},
    }


def _p_recovery(state) -> float:
    """`p_recovery` do diagnóstico; sem ele, o score/100; sem os dois (ciclo
    legado, aberto por um plano sem diagnóstico), o neutro 0.5 — a mensagem
    sai com tom "amigável", nunca deixa de sair por falta de número."""
    p_recovery = state.get("p_recovery")
    if p_recovery is None:
        score = state.get("recovery_score")
        p_recovery = (score if score is not None else 50) / 100
    return p_recovery


def estado_do_ciclo(ciclo: dict, tentativas: Optional[list] = None) -> AgentState:
    """O `AgentState` de um ciclo, reconstruído das tabelas — sem checkpoint.

    É o que o caminho de mensagem DEPOIS da 3ª tentativa e o fechamento por
    confirmação usam: o diagnóstico que `decide_recovery` gravou no ciclo,
    a causa, o valor, o tenant, o e2e da falha original (que é a chave da
    linha do dataset) e o contador executado.
    """
    score = ciclo.get("recovery_score")
    return {
        "payment_event": {}, "payment_method": "pix_automatico",
        "tenant_id": ciclo["tenant_id"], "customer_id": ciclo["id_recorrencia"],
        "invoice_id": ciclo.get("e2e_falha_original") or ciclo["id_cobranca_original"],
        "amount": float(ciclo["valor"] or 0.0),
        "ciclo_id": ciclo["id"], "ciclo_evento": None, "ciclo_tentativa": None,
        "decisoes": [], "features": None,
        "failure_cause": ciclo.get("causa_original"),
        # Um ciclo sem diagnóstico (legado, aberto por um plano) tem score
        # nulo; os nós de fechamento e o CRM formatam número, então vai 0 —
        # que é o que se sabe: nada.
        "recovery_score": int(score) if score is not None else 0,
        "p_recovery": ciclo.get("p_recovery"), "eprofit": ciclo.get("eprofit"),
        "recommend_action": None, "ltv_estimated": None, "shap_explanation": None,
        "feature_importance": None, "is_anomalous": None, "reconstruction_error": None,
        "anomaly_explanation": None, "optimal_retry_at": None, "confidence": None,
        "profile_type": None,
        "estrategia": ciclo.get("estrategia"), "raciocinio": None,
        "retry_count": ciclo_cobranca.tentativas_executadas(ciclo["id"]),
        "next_retry_at": None,
        "retry_exhausted": ciclo["estado"] != ciclo_cobranca.RECOBRANDO,
        "recovered": ciclo["estado"] == ciclo_cobranca.RECUPERADO,
        "pix_janela_ate": ciclo_cobranca._data(ciclo["janela_fim"]),
        "pix_retry_schedule": [
            {"numero": t["numero"], "quando": ciclo_cobranca._data(t["agendada_para"]),
             "valor": t["valor"], "origem": t["origem_data"]}
            for t in (tentativas if tentativas is not None
                      else ciclo_cobranca.tentativas_do_ciclo(ciclo["id"]))],
        "dunning_sent": ciclo["estado"] in (ciclo_cobranca.MENSAGEM_ENVIADA,),
        "channel": None, "canais_considerados": None, "metodo_pagamento": None,
        "message_sent": None, "mensagem_meta": None,
    }


async def concluir_ciclo_por_resultado(ciclo_id: int, agora: Optional[datetime] = None,
                                       motivo: str = "tentativas_esgotadas",
                                       causa: Optional[str] = None) -> Optional[AgentState]:
    """A mensagem DEPOIS das tentativas (R1) — e na revogação (R5) —, fora do grafo.

    Desde a Etapa 2 (R7, R8) é `preparar_mensagem`: o ciclo vai de
    `recobrando` a `aguardando_escolha` sob a trava de escrita (quem perde a
    disputa — webhook e agendador ao mesmo tempo — não faz nada), as 3
    sugestões são geradas, e o modo da empresa decide: no automático a
    recomendada sai agora (se a janela de contato e um canal permitirem); no
    modo escolha, espera a escolha ou o prazo. Devolve o state final quando a
    mensagem SAIU, ou None.

    As decisões da campanha gravadas na trilha são as MESMAS de dentro do
    grafo (A2): o envio dos dois caminhos é `enviar_mensagem_escolhida`.
    """
    return await preparar_mensagem(ciclo_id, agora or _agora(), motivo=motivo, causa=causa)


# ── As três mensagens (Etapa 2, R7 a R9) ─────────────────────────────────
#
# O caminho de TODA mensagem do involuntário com ciclo:
#
#   preparar_mensagem         recobrando → aguardando_escolha (a trava) e a
#                             rodada 1; no modo automático, escolhe e envia
#   gerar_rodada              3 sugestões numa chamada ao LLM (reserva:
#                             templates), canal pela base, gravadas juntas
#   registrar_escolha         a escolha (papel, prazo ou automático) + a
#                             decisão na trilha (R9)
#   enviar_mensagem_escolhida janela de contato, canal relido na base,
#                             aguardando_escolha → mensagem_enviada (a reserva
#                             do envio), envio, confirmação
#
# R1 não muda: nada disto é alcançável sem os gatilhos que já existiam (o
# resultado da 3ª tentativa, a revogação, a causa não retentável, a janela
# esgotada), e nenhuma rota cria sugestão nem dispara sem ser a escolha de uma
# das 3 geradas.

async def gerar_rodada(ciclo_id: int, agora: datetime, causa: Optional[str] = None,
                       papel: Optional[str] = None) -> Optional[int]:
    """Gera e grava uma rodada de 3 sugestões. Devolve o número dela, ou None
    se o ciclo não aceita rodada (fora de `aguardando_escolha`, ou já tem
    escolhida — a escolha ou o prazo chegaram durante a geração: vencem eles).

    `causa`: a da rodada. Sem ela, a da última rodada (a regeração mantém a
    causa revogada, D-E2-7); sem rodada, a do ciclo. `papel`: quem pediu a
    regeração — vai para a trilha."""
    ciclo = ciclo_cobranca.ciclo_por_id(ciclo_id)
    if ciclo is None or ciclo["estado"] != ciclo_cobranca.AGUARDANDO_ESCOLHA:
        return None
    anteriores = ciclo_cobranca.mensagens_do_ciclo(ciclo_id)
    causa = causa or (anteriores[-1]["causa"] if anteriores else ciclo.get("causa_original"))
    config = configuracao.ler(ciclo["tenant_id"])
    canal = canal_involuntario.escolher_canal(ciclo["tenant_id"], ciclo["id_recorrencia"], config)
    estado = estado_do_ciclo(ciclo)
    p = _p_recovery(estado)
    try:
        geradas = await _dunning.gerar_sugestoes(
            ciclo["id_recorrencia"], causa, p, estado["amount"], canal=canal["canal"],
            primeiro_nome=canal["primeiro_nome"], tempo_de_casa=canal["tempo_de_casa"])
    except Exception as e:                       # noqa: BLE001 — a varredura regenera
        logger.error("[CICLO] ciclo %s: as sugestões não foram geradas (%r) — a varredura "
                     "tenta de novo.", ciclo_id, e)
        return None
    rodada = ciclo_cobranca.gravar_rodada(
        ciclo_id, geradas["sugestoes"], canal["canal"], canal["motivo_canal"], causa,
        geradas["payment_method"], geradas["tom"], agora)
    if rodada is None:
        return None
    print(f"[CICLO] {ciclo['id_recorrencia']}: ciclo {ciclo_id} — rodada {rodada} de 3 sugestões "
          f"(recomendada: {geradas['recomendada']}, canal: {canal['canal']})")
    if rodada > 1:
        # A regeração vale como decisão na trilha (R8, acréscimo): tipo
        # `oferta`, como a escolha. Nunca o texto.
        trilha.registrar_decisoes([trilha.decisao(
            ciclo["tenant_id"], ciclo["id_recorrencia"], trilha.DOMINIO_INVOLUNTARIO,
            trilha.TIPO_OFERTA, trilha.MODELO_REGRA,
            entradas={"failure_cause": causa, "rodada_anterior": rodada - 1},
            saida={"rodada": rodada, "abordagem_recomendada": geradas["recomendada"],
                   "pedida_por_papel": papel or "sistema",
                   "regra": "regeracao_de_sugestoes",
                   "motivo_da_regra": ("a empresa pediu outras 3 sugestões dentro do prazo "
                                       "de escolha, que não é reiniciado") if papel else
                                      "novas sugestões depois de um evento do ciclo"},
            decidido_em=_utc(agora))])
    return rodada


def _utc(agora: datetime) -> str:
    """O instante local do ciclo como a trilha o grava: UTC com offset."""
    return agora.astimezone().astimezone(timezone.utc).isoformat(timespec="seconds")


def registrar_escolha(ciclo_id: int, escolhida_por: str, agora: datetime,
                      rodada: Optional[int] = None, abordagem: Optional[str] = None
                      ) -> Optional[dict]:
    """A escolha de UMA das sugestões, e a decisão na trilha do Art. 20 (R9):
    tipo `oferta`, com o papel de quem escolheu (nunca o nome) e se foi por
    prazo. Devolve a mensagem escolhida, ou None se não havia o que escolher."""
    escolhida = ciclo_cobranca.registrar_escolha(ciclo_id, rodada, abordagem, escolhida_por, agora)
    if escolhida is None:
        return None
    ciclo = ciclo_cobranca.ciclo_por_id(ciclo_id)
    recomendada = next((m["abordagem"] for m in ciclo_cobranca.mensagens_do_ciclo(ciclo_id)
                        if m["rodada"] == escolhida["rodada"] and m["recomendada"]), None)
    motivos = {"prazo": "sem escolha da empresa no prazo, a recomendada da última rodada "
                        "saiu sozinha",
               "automatico": "modo automático da empresa: a recomendada sai sem escolha"}
    trilha.registrar_decisoes([trilha.decisao(
        ciclo["tenant_id"], ciclo["id_recorrencia"], trilha.DOMINIO_INVOLUNTARIO,
        trilha.TIPO_OFERTA, trilha.MODELO_REGRA,
        entradas={"failure_cause": escolhida["causa"], "rodada": escolhida["rodada"]},
        saida={"abordagem": escolhida["abordagem"], "abordagem_recomendada": recomendada,
               "rodada": escolhida["rodada"], "escolhida_por": escolhida_por,
               "por_prazo": escolhida_por == "prazo",
               "regra": "escolha_entre_sugestoes",
               "motivo_da_regra": motivos.get(escolhida_por,
                                              f"escolhida pela empresa (papel {escolhida_por}) "
                                              "entre as 3 sugestões geradas")},
        decidido_em=_utc(agora))])
    print(f"[CICLO] ciclo {ciclo_id}: escolhida {escolhida['abordagem']} (rodada "
          f"{escolhida['rodada']}) por {escolhida_por}")
    return escolhida


async def preparar_mensagem(ciclo_id: int, agora: datetime, motivo: str,
                            causa: Optional[str] = None,
                            persistir: bool = True) -> Optional[AgentState]:
    """`recobrando → aguardando_escolha`, a rodada 1, e o modo da empresa.
    Devolve o state quando a mensagem SAIU agora; None se ficou esperando
    (escolha, janela, canal) ou se não havia o que preparar."""
    ciclo = ciclo_cobranca.ciclo_por_id(ciclo_id)
    if ciclo is None or ciclo["estado"] != ciclo_cobranca.RECOBRANDO:
        return None
    try:
        ciclo_cobranca.transicionar(ciclo_id, ciclo_cobranca.AGUARDANDO_ESCOLHA, agora)
    except ciclo_cobranca.TransicaoInvalida:
        logger.info("[CICLO] ciclo %s: outro processo já preparou a mensagem", ciclo_id)
        return None
    print(f"[CICLO] {ciclo['id_recorrencia']}: ciclo {ciclo_id} — {motivo}; 3 sugestões de "
          f"mensagem ({ciclo_cobranca.tentativas_executadas(ciclo_id)}/{MAX_TENTATIVAS_PIX} "
          f"tentativa(s) executada(s))")
    rodada = await gerar_rodada(ciclo_id, agora, causa=causa)
    if rodada is None:
        return None
    return await _seguir_o_modo(ciclo_id, agora, persistir)


async def _seguir_o_modo(ciclo_id: int, agora: datetime, persistir: bool = True):
    ciclo = ciclo_cobranca.ciclo_por_id(ciclo_id)
    config = configuracao.ler(ciclo["tenant_id"])
    if config["modo_mensagem_involuntario"] != configuracao.MODO_AUTOMATICO:
        return None
    if registrar_escolha(ciclo_id, "automatico", agora) is None:
        return None
    return await enviar_mensagem_escolhida(ciclo_id, agora, persistir)


async def enviar_mensagem_escolhida(ciclo_id: int, agora: datetime,
                                    persistir: bool = True) -> Optional[AgentState]:
    """Envia a mensagem escolhida, se puder AGORA: dentro da janela de contato
    da empresa e com um canal entregável, relido na base neste instante (o
    contato nunca é copiado). Sem canal, a mensagem fica marcada como não
    entregável e o ciclo continua esperando — o relógio reconfere a cada
    passagem (D-E2-11). Devolve o state final quando saiu."""
    ciclo = ciclo_cobranca.ciclo_por_id(ciclo_id)
    if ciclo is None or ciclo["estado"] != ciclo_cobranca.AGUARDANDO_ESCOLHA:
        return None
    msg = ciclo_cobranca.mensagem_escolhida(ciclo_id)
    if msg is None or msg["enviada_em"]:
        return None
    config = configuracao.ler(ciclo["tenant_id"])
    if not configuracao.dentro_da_janela(config, agora):
        return None
    canal = canal_involuntario.escolher_canal(ciclo["tenant_id"], ciclo["id_recorrencia"], config)
    if canal["canal"] == canal_involuntario.SEM_CANAL:
        if not msg["nao_entregavel_em"]:
            print(f"[CICLO] ciclo {ciclo_id}: sem canal entregável ({canal['motivo_canal']}) — "
                  "mensagem não entregável; o relógio reconfere o contato a cada passagem")
        ciclo_cobranca.marcar_mensagem(msg["id"], agora, nao_entregavel=True,
                                       canal=canal["canal"], motivo_canal=canal["motivo_canal"])
        return None
    try:
        ciclo_cobranca.transicionar(ciclo_id, ciclo_cobranca.MENSAGEM_ENVIADA, agora)
    except ciclo_cobranca.TransicaoInvalida:
        logger.info("[CICLO] ciclo %s: outro processo já está enviando — nenhuma segunda "
                    "mensagem", ciclo_id)
        return None
    estado = estado_do_ciclo(ciclo_cobranca.ciclo_por_id(ciclo_id))
    estado["failure_cause"] = msg["causa"]
    origem = "gerado" if msg["origem_texto"] == "llm" else "template"
    try:
        result = await _dunning.run_campaign(
            estado["customer_id"], msg["causa"], _p_recovery(estado), estado["amount"],
            tenant_id=estado["tenant_id"], texto=msg["texto"], abordagem=msg["abordagem"],
            canal=canal["canal"], motivo_canal=canal["motivo_canal"], origem=origem,
            codigo_template=msg["codigo_template"])
    except Exception as e:                       # noqa: BLE001 — B3-a: a reserva não pode ficar órfã
        ciclo_cobranca.desfazer_reserva_de_mensagem(ciclo_id, f"envio levantou: {e!r}", agora)
        return None
    if not result.get("sent"):
        ciclo_cobranca.desfazer_reserva_de_mensagem(ciclo_id, "envio não confirmou (sent=False)",
                                                    agora)
        return None
    ciclo_cobranca.marcar_mensagem(msg["id"], agora, enviada=True, canal=canal["canal"],
                                   motivo_canal=canal["motivo_canal"])
    ciclo_cobranca.confirmar_mensagem(ciclo_id, agora)
    estado.update({
        "decisoes": list(result.get("decisoes") or []),
        "dunning_sent": True, "channel": result["channel"],
        "metodo_pagamento": result["payment_method"], "message_sent": result["message"],
        "mensagem_meta": {"origem": origem, "codigo": msg["codigo_template"] or "",
                          "valor": estado["amount"], "link": result.get("portal_link") or "",
                          "metodo": result["payment_method"], "abordagem": msg["abordagem"]},
    })
    if persistir:
        return await update_roi_dashboard(estado)
    return estado


async def refazer_por_revogacao(ciclo_id: int, agora: datetime) -> Optional[AgentState]:
    """D-E2-7: a revogação chega com o ciclo em `aguardando_escolha`. As
    sugestões oferecem o Pix Automático que o cliente acabou de fechar: uma
    escolha ainda não enviada é desfeita, e uma rodada nova sai com a causa
    revogada (boleto). O prazo de escolha NÃO é reiniciado. Depois, o modo da
    empresa, como em qualquer rodada."""
    ciclo_cobranca.desfazer_escolha_nao_enviada(ciclo_id, agora)
    rodada = await gerar_rodada(ciclo_id, agora, causa=CAUSA_REVOGADA)
    if rodada is None:
        return None
    return await _seguir_o_modo(ciclo_id, agora)


async def processar_pendencias_de_mensagem(resultado: dict, agora: datetime) -> None:
    """O que o relógio faz pelos ciclos em `aguardando_escolha` (as listas de
    `ciclo_cobranca.pendencias_de_mensagem`), com a configuração de cada
    empresa: sugestões que faltam são geradas; escolhas cujo prazo venceu
    (ou de empresa em modo automático) saem pela recomendada da última
    rodada; escolhidas não enviadas são enviadas se a janela e o canal
    deixarem."""
    for ciclo_id in resultado.get("sugestoes_faltando") or []:
        if await gerar_rodada(ciclo_id, agora) is not None:
            await _seguir_o_modo(ciclo_id, agora)
    for ciclo_id, tenant_id, desde in resultado.get("escolha_pendente") or []:
        config = configuracao.ler(tenant_id)
        inicio = ciclo_cobranca._data(desde)
        if config["modo_mensagem_involuntario"] == configuracao.MODO_AUTOMATICO:
            por = "automatico"
        elif inicio is not None and agora >= inicio + timedelta(hours=config["prazo_escolha_horas"]):
            por = "prazo"
        else:
            continue
        if registrar_escolha(ciclo_id, por, agora) is not None:
            await enviar_mensagem_escolhida(ciclo_id, agora)
    for ciclo_id in resultado.get("envio_pendente") or []:
        await enviar_mensagem_escolhida(ciclo_id, agora)


async def descartar_ciclo(state: AgentState) -> AgentState:
    """R6 / I-4b: o aborto de `route_after_diagnosis` deixa de ser silencioso.

    Grava a decisão na trilha do Art. 20 pelo caminho que já existe para as
    outras (`trilha.decisao` + `anotar_decisao`, persistida em
    `update_roi_dashboard`), e leva o ciclo a `descartado` com o motivo —
    gravando `decidido_em`, para o ciclo nunca se confundir com um incompleto.
    """
    momento = _agora()
    eprofit = state.get("eprofit", 0) or 0
    score = state.get("recovery_score", 0) or 0
    if not state.get("recommend_action", True) or eprofit <= 0:
        motivo = "eprofit_nao_positivo"
        frase = f"e-Profit R$ {eprofit:.2f} <= 0: intervir custaria mais do que recupera"
    else:
        motivo = "score_abaixo_do_corte"
        frase = f"score {score}/100 abaixo do corte de 5: recuperação improvável"
    print(f"[CICLO] {state.get('customer_id')}: ciclo descartado — {motivo}")
    dec = trilha.decisao(
        state.get("tenant_id"), state.get("customer_id"), trilha.DOMINIO_INVOLUNTARIO,
        trilha.TIPO_RETENTATIVA, trilha.MODELO_REGRA,
        entradas={"failure_cause": state.get("failure_cause"),
                  "recovery_score": trilha._num(score), "eprofit": trilha._num(eprofit),
                  "recommend_action": bool(state.get("recommend_action", True))},
        saida={"estrategia": "descartado", "regra": "route_after_diagnosis",
               "motivo_descarte": motivo, "motivo_da_regra": frase})
    if state.get("ciclo_id"):
        try:
            ciclo_cobranca.descartar(state["ciclo_id"], motivo, momento)
        except ciclo_cobranca.TransicaoInvalida as e:
            logger.warning("[CICLO] %s: descarte não aceito pelo ciclo (%s)",
                           state.get("customer_id"), e)
    return trilha.anotar_decisao({**state, "estrategia": "descartado"}, dec)


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
    """Quantas tentativas deste ciclo foram EXECUTADAS (saíram para o PSP, ou
    têm resultado de execução).

    Pelo ciclo, quando o state tem um. Sem ciclo (chamada direta), pelo plano
    do mandato em `retry_state`. Nunca pelo plano agendado: um plano de 3
    tentativas em que só a primeira saiu custou uma, não três (Gap 6).
    """
    if state.get("ciclo_id"):
        return ciclo_cobranca.tentativas_executadas(state["ciclo_id"])
    registro = retry_state.get_retry_state(
        state.get("customer_id", ""), tenant_id=state.get("tenant_id"))
    if not registro:
        return 0
    return sum(1 for t in registro.get("tentativas") or [] if t.get("disparada_em"))


async def update_roi_dashboard(state: AgentState) -> AgentState:
    fee = success_fee(state["amount"], bool(state.get("recovered")))
    # `or 0`: um state reconstruído de um ciclo sem diagnóstico (legado, ou a
    # linha de transição do dataset) tem e-Profit None, e o log não pode cair.
    eprofit = state.get("eprofit") or 0
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
    # A trilha do Art. 20 deste ciclo, numa transação só. Best effort.
    trilha.registrar_decisoes(state.get("decisoes") or [])
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

    A lista que o artefato em `models/` declara (Bloco H, 22/09/2026) é a da
    base v2: `card_brand` saiu e `metodo_pagamento` entrou. Quem monta o
    dicionário aqui tem que fornecer TODAS as features dessa lista — o
    `predict` substitui feature ausente por 0 em silêncio, e é exatamente o
    defeito que `tests/test_servir_v2.py` existe para pegar.
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
        # O classificador v2 não vê bandeira (`card_brand` saiu na base v2:
        # era "n/a" em 100% das linhas servidas). O que ele vê é o método de
        # pagamento do cliente, cujo vocabulário de treino é `pix_automatico`
        # / `boleto`. Cartão está FORA desse vocabulário de propósito — este
        # caminho não recebe evento desde a Fase 3 — e o encoder mapeia o
        # valor desconhecido para a categoria "outro", sem quebrar.
        "metodo_pagamento": "cartao",
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

    DE ONDE VEM `metodo_pagamento` (Bloco H, 22/09/2026). É atributo do
    cliente, não da cobrança — na base v2 cada cliente é `pix_automatico`
    (85%) ou `boleto` (15%). Nenhuma fonte de perfil tem esse campo: o
    `PerfilProvider` devolve tenure/histórico/LTV, e a base importada do
    self-service tem `billing_profile` (CLT/PJ/freelancer), que é outra coisa.
    A escolha declarada: o método vem do CANAL DO EVENTO. Este caminho só
    recebe webhooks de Pix Automático, e uma recorrência de Pix Automático que
    falhou é, por definição, de um cliente cobrado por Pix Automático — não é
    default, é dedução. `boleto` passa a aparecer no dia em que houver webhook
    de boleto falhado, e aí o valor vem do evento, não daqui.
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
        # Atributo do cliente, deduzido do canal do evento (ver docstring).
        # É um valor do vocabulário de treino, não a categoria "desconhecido".
        "metodo_pagamento": "pix_automatico",
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

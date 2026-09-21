"""crai/agent/state.py — Schema de estado do agente (churn involuntário).

O pipeline é alimentado por webhooks com regras de retentativa incompatíveis —
Pix Automático (regulado pelo BACEN) e cartão (backoff livre). `payment_method`
é o campo que mantém os dois isolados: ele é preenchido na entrada do pipeline,
antes de qualquer nó de decisão, e é o que a aresta condicional do grafo lê
para escolher a política.

Desde a Fase 3 só o caminho de Pix Automático tem política ativa; o de cartão
está preservado, fora do fluxo, em crai/dunning/legacy_card/. O campo continua
aqui porque é exatamente o ponto de extensão para reativá-lo.
"""

from typing import TypedDict, Optional, Literal
from datetime import datetime

# Meio de pagamento de origem do evento. Determina qual política de
# retentativa o grafo aplica — ver crai/agent/main_agent.py.
PaymentMethod = Literal["pix_automatico", "card"]


class AgentState(TypedDict):
    # Input
    payment_event:  dict            # evento de origem (Pix normalizado ou Stripe cru)
    payment_method: PaymentMethod   # preenchido na entrada, nunca inferido depois

    # Qual empresa cliente da CRAI gerou este ciclo. O churn voluntário já era
    # isolado por tenant; o involuntário não era, e a assimetria tinha efeito
    # prático: dois clientes da CRAI num mesmo HubSpot misturavam negócios no
    # mesmo pipeline sem nada que os separasse no relatório, e as tentativas
    # reenviadas ao PSP não eram atribuíveis a quem as pagou.
    #
    # Nesta fase o campo é PROPAGAÇÃO E ATRIBUIÇÃO, não regra: o comportamento
    # de recuperação é idêntico para todos os tenants. Decisão que dependa de
    # tenant é RBAC/produto, e entra por outra porta.
    #
    # Ausente vira `default_tenant` — MVP declarado, mesma escolha do
    # `_tenant_da_requisicao` do voluntário: a CRAI ainda é operada para um
    # cliente por instalação, e exigir o campo quebraria os webhooks já
    # integrados.
    tenant_id:      str

    customer_id:    str
    amount:         float
    invoice_id:     str

    # Diagnóstico (XGBoost + RF + e-Profit + SHAP)
    #
    # `features` é o X que o classificador consumiu — as 11 features + LTV.
    # Ele vive no state desde o Sprint 6 por um motivo só: sem isso, o par
    # (features, recovered) que o `dunning/recovery_log.py` grava não existe.
    # As features eram calculadas dentro de `diagnose_failure`, usadas na
    # predição e descartadas; rodar três meses em produção daria zero linha de
    # treino com dados reais. Não é dado novo — é o mesmo dado, preservado.
    features:             Optional[dict]
    failure_cause:        Optional[str]
    recovery_score:       Optional[int]           # 0-100
    p_recovery:           Optional[float]          # 0.0-1.0
    eprofit:              Optional[float]          # R$ — e-Profit da intervenção
    recommend_action:     Optional[bool]           # True se e-Profit > 0
    ltv_estimated:        Optional[float]          # LTV estimado do cliente
    shap_explanation:     Optional[dict]           # Explicação SHAP por feature
    feature_importance:   Optional[dict]

    # Anomalia (Autoencoder)
    is_anomalous:         Optional[bool]
    reconstruction_error: Optional[float]
    anomaly_explanation:  Optional[list]           # Top features por erro de reconstrução

    # Liquidez (LSTM + Prophet)
    optimal_retry_at: Optional[datetime]
    confidence:       Optional[float]
    profile_type:     Optional[str]

    # Decisão de recuperação (Módulo 5 — raciocínio ReAct sobre contexto ML)
    estrategia:   Optional[str]    # retry_automatico | mensagem_pagamento
    raciocinio:   Optional[list]   # trilha de raciocínio em PT-BR (auditoria)

    # Retentativa — a política aplicada depende de payment_method:
    #   pix_automatico → PixAutomaticoRetryPolicy (3 tentativas / 7 dias, BACEN)
    #   card           → nenhuma: a recobrança automática de cartão saiu do
    #                    pipeline ativo na Fase 3 (ver crai/dunning/legacy_card/)
    retry_count:     int
    next_retry_at:   Optional[datetime]
    retry_exhausted: bool
    recovered:       bool

    # Até quando `retry_count` vale. O limite do BACEN é "3 tentativas dentro
    # de 7 dias corridos contados do vencimento" — é um limite POR JANELA, não
    # por contrato. Sem esta marca, o contador só crescia, e uma cobrança que
    # falhasse no mês seguinte encontrava as 3 tentativas já gastas por uma
    # janela encerrada havia semanas: o cliente perdia por prescrição um
    # direito que a regulação lhe dá em cada ciclo.
    #
    # Ancorada no vencimento da cobrança que abriu a janela e NUNCA empurrada
    # por eventos posteriores da mesma janela (ver `inicio_da_janela` em
    # crai/dunning/pix_automatico_retry.py). `None` = nenhuma janela aberta.
    pix_janela_ate:  Optional[datetime]

    # Plano completo de tentativas dentro da janela regulada (só Pix Automático)
    pix_retry_schedule: Optional[list]

    # Dunning (LangGraph + Claude) — mensagem personalizada, sem escalonamento humano
    dunning_sent:     bool
    channel:          Optional[str]
    # O comparativo de canal do classificador, canal a canal, com o motivo de
    # cada descarte. O envio sai pelo bot de WhatsApp por limitação de
    # integração — a lista existe para o painel dizer isso, não esconder.
    canais_considerados: Optional[list]
    metodo_pagamento: Optional[str]   # pix_automatico | boleto (Pix Automático como fallback antes do boleto)
    message_sent:     Optional[str]
    # Como `message_sent` foi escrita: {"origem": "template"|"gerado",
    # "codigo": <chave de FALLBACK_TEMPLATES>, "valor", "link", "metodo"}.
    # Quem EXIBE a mensagem usa isto para escreve-la no idioma do leitor
    # quando ela veio de modelo; texto gerado pela Claude API nao tem codigo e
    # e exibido como chegou. Nao participa de nenhuma decisao.
    mensagem_meta:    Optional[dict]

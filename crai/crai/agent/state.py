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
    customer_id:    str
    amount:         float
    invoice_id:     str

    # Diagnóstico (XGBoost + RF + e-Profit + SHAP)
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
    metodo_pagamento: Optional[str]   # pix_automatico | boleto (Pix Automático como fallback antes do boleto)
    message_sent:     Optional[str]

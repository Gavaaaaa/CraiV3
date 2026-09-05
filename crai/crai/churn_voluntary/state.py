"""crai/churn_voluntary/state.py — Schema de estado do agente de churn voluntário."""

from typing import TypedDict, Optional


class ChurnVoluntaryState(TypedDict):
    # Input (do Segment SDK)
    user_id:    str
    event:      str           # Cancellation Page Viewed | Downgrade Clicked | Session Started
    props:      dict          # payload bruto do evento

    # Risco
    risk_score: float
    profile:    str           # CLT | PJ | freelancer
    # Tom da mensagem, não desvio de fluxo: risco crítico sai com tom de alto
    # cuidado. Nunca aciona humano.
    is_critical: bool
    criticality: str          # critico | alto | padrao

    # Oferta (Multi-Armed Bandit)
    offer_type: Optional[str]

    # Canal (LangGraph — roteamento com memória de histórico)
    channel:       Optional[str]   # popup | email | sms
    on_site_now:   bool
    prior_channel_success: Optional[str]

    # Mensagem (Claude API)
    message: Optional[str]

    # Resultado
    offer_sent: bool
    accepted:   Optional[bool]
    retained:   bool

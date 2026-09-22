"""crai/churn_voluntary/state.py — Schema de estado do agente de churn voluntário."""

from typing import TypedDict, Optional


class ChurnVoluntaryState(TypedDict):
    # Input (do Segment SDK)
    # Empresa cliente da CRAI. Separa o aprendizado do bandit e a memória de
    # canal entre clientes diferentes — sem isto, uma SaaS jurídica ensinaria a
    # CRAI sobre a base de uma SaaS de e-commerce.
    tenant_id:  str
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
    # A rodada do bandit que decidiu `offer_type`: os braços de maior e-Profit
    # amostrado, com a probabilidade aprendida de cada um. É o que vira as
    # candidatas de mensagem — a decisão continua sendo do bandit.
    ofertas_consideradas: Optional[list]

    # Canal (LangGraph — roteamento com memória de histórico)
    channel:       Optional[str]   # popup | email | whatsapp
    on_site_now:   bool
    prior_channel_success: Optional[str]

    # Mensagem (Claude API)
    message: Optional[str]
    # As candidatas que o painel mostra: uma por braço considerado, cada uma
    # com texto, oferta, probabilidade e a marca de qual venceu. `message` é
    # sempre o texto da vencedora.
    candidatas: Optional[list]
    # Cada canal que `choose_channel` olhou, com o motivo de ter sido escolhido
    # ou descartado.
    canais_considerados: Optional[list]

    # Resultado
    offer_sent: bool
    accepted:   Optional[bool]
    retained:   bool

    # A trilha do Art. 20 (LGPD): uma entrada por decisão automatizada deste
    # ciclo (risco, oferta, canal), montada NO NÓ que decide
    # (`retention_log.decisao`) e gravada de uma vez no `update_crm`
    # (`retention_log.registrar_decisoes`). Declarada aqui porque o LangGraph
    # só carrega entre nós as chaves do schema.
    decisoes: Optional[list]

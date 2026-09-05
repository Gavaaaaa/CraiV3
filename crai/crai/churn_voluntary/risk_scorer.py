"""
crai/churn_voluntary/risk_scorer.py
Calcula o risk_score a partir de eventos do Segment SDK.

Eventos suportados:
    Cancellation Page Viewed  → risco fixo alto (intenção explícita)
    Downgrade Clicked         → risco fixo médio-alto
    Session Started           → risco calculado por inatividade + uso

Além do risco, classifica a CRITICIDADE — o rótulo que define o tom da
mensagem. Criticidade não desvia fluxo nem muda oferta: a CRAI atende todo
mundo sozinha, e quem muda é a forma de falar.
"""

import math
import os

from .offer_bandit import is_critical_risk

HIGH_RISK_THRESHOLD = 0.75          # piso do "alto"; o teto é o CRITICAL do bandit
HIGH_VALUE_MRR_DEFAULT = 2000.0     # override: CRAI_HIGH_VALUE_MRR_THRESHOLD

FIXED_RISK = {
    "Cancellation Page Viewed": 0.90,
    "Downgrade Clicked":        0.75,
}


def calculate_risk(event: str, props: dict) -> float:
    if event in FIXED_RISK:
        return FIXED_RISK[event]

    if event == "Session Started":
        days = props.get("days_since_last", 0)
        features = props.get("features_used_30d", 10)
        # Risco sobe com inatividade, cai com uso de features
        risk = min(1.0, (days / 30) * 0.7 + max(0, (5 - features) / 5) * 0.3)
        return round(risk, 3)

    return 0.0   # evento desconhecido — não dispara nada


def classify_profile(props: dict) -> str:
    """Reaproveita o mesmo critério do churn involuntário (CV de pagamentos)."""
    return props.get("billing_profile", "CLT")


def limiar_de_alto_valor() -> float:
    """Lido a cada chamada, não no import.

    O ambiente muda depois que o módulo já está carregado — a demo seta env
    antes de rodar, o teste usa monkeypatch. Um valor congelado no import
    ignoraria os dois. Env ausente ou impossível de ler cai no default, sem
    derrubar o pipeline por causa de uma variável mal digitada.
    """
    bruto = os.getenv("CRAI_HIGH_VALUE_MRR_THRESHOLD")
    if bruto is None:
        return HIGH_VALUE_MRR_DEFAULT
    try:
        return float(bruto)
    except (TypeError, ValueError):
        print(f"[CHURN-VOL] CRAI_HIGH_VALUE_MRR_THRESHOLD={bruto!r} não é número "
              f"— usando o default R$ {HIGH_VALUE_MRR_DEFAULT:.2f}")
        return HIGH_VALUE_MRR_DEFAULT


def mrr_utilizavel(bruto) -> float | None:
    """O MRR do payload do Segment, ou None quando não dá para usar como número.

    `props` é payload bruto de terceiro: `mrr` chega como número, como texto,
    como lista, como `None`, ou não chega. Comparar qualquer uma dessas com um
    limiar levanta `TypeError` dentro do nó do grafo, três camadas abaixo da
    borda que validou o evento. Ausência de MRR não é erro — é só a ausência do
    sinal de alto valor, e aí o risco classifica sozinho.

    **Texto é recusado de propósito, não por preguiça.** `"10,000"` é dez mil em
    en-US e dez em pt-BR, e o payload do Segment não declara locale. Adivinhar
    rebaixaria o tom de um cliente de R$ 10.000 para o texto padrão em silêncio
    — o mesmo defeito que a A1 mediu no valor de cobrança (P0-1). O parser que
    resolve isso direito existe (`payment_gateway._para_float`), mas mora no
    lado involuntário; promovê-lo a helper compartilhado é uma frente própria,
    não um efeito colateral deste sprint. Até lá, texto = sinal ausente.

    Recusa também `inf`/`NaN` (que `json.loads` aceita como literal) e inteiro
    grande demais para virar float — os dois derrubariam a comparação adiante.
    """
    if bruto is None or isinstance(bruto, bool) or not isinstance(bruto, (int, float)):
        return None
    try:
        valor = float(bruto)
    except (OverflowError, ValueError):
        # Inteiro JSON de precisão arbitrária: 401 dígitos chegam como `int` e
        # derrubam `float()`. Ver `payment_gateway._para_float`, mesmo caso.
        return None
    if not math.isfinite(valor) or valor < 0:
        return None
    return valor


def classify_criticality(risk_score: float, mrr: float | None) -> str:
    """Rótulo de tom: "critico" | "alto" | "padrao".

    Duas portas levam a "critico", e elas são independentes:

      RISCO   — `is_critical_risk` (>= 0.90). O mesmo limiar que o bandit usa,
                importado em vez de recopiado: dois 0.90 em arquivos
                diferentes divergem no dia em que um deles mudar.
      VALOR   — MRR >= `limiar_de_alto_valor()` (default R$ 2.000). Um cliente
                grande com risco baixo ainda merece a mensagem cuidadosa; é
                caro demais para receber o texto padrão.

    "alto" é a faixa [0.75, 0.90): risco declarado, ainda não crítico.
    """
    if is_critical_risk(risk_score):
        return "critico"

    mrr = mrr_utilizavel(mrr)
    if mrr is not None and mrr >= limiar_de_alto_valor():
        return "critico"

    if risk_score >= HIGH_RISK_THRESHOLD:
        return "alto"

    return "padrao"

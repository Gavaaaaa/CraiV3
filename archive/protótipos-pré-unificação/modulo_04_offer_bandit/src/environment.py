"""Ambiente simulado de ofertas de retencao para o Modulo 4 do CRAI.

Define a "verdade" que o bandit NAO conhece: a probabilidade real de cada
segmento aceitar cada oferta, os custos de cada braco e o valor retido em
caso de aceite. O simulador sorteia clientes (perfil + MRR) e responde se
a oferta foi aceita.

As probabilidades foram calibradas para que o braco com MAIOR ACEITE nem
sempre seja o de maior e-Profit — e o caso do CLT, onde a consulta com CS
converte mais (50%) mas custa R$250 de tempo humano, perdendo em e-Profit
para o desconto de 10% na maioria dos MRRs. E exatamente essa diferenca
que justifica otimizar e-Profit em vez de taxa de conversao.
"""
from __future__ import annotations

import numpy as np

OFFERS = ["desconto_10", "desconto_20", "pausa_1_mes", "consulta_cs", "pix_boleto_flash"]
PROFILES = ["CLT", "PJ", "freelancer"]
PROFILE_WEIGHTS = [0.50, 0.30, 0.20]

# Probabilidade REAL de aceite por (perfil, oferta) — oculta do bandit
TRUE_ACCEPT = {
    "CLT":        {"desconto_10": 0.44, "desconto_20": 0.46, "pausa_1_mes": 0.30,
                   "consulta_cs": 0.50, "pix_boleto_flash": 0.18},
    "PJ":         {"desconto_10": 0.28, "desconto_20": 0.36, "pausa_1_mes": 0.58,
                   "consulta_cs": 0.42, "pix_boleto_flash": 0.30},
    "freelancer": {"desconto_10": 0.40, "desconto_20": 0.44, "pausa_1_mes": 0.35,
                   "consulta_cs": 0.30, "pix_boleto_flash": 0.50},
}

# MRR mensal tipico por perfil (R$): media e desvio do lognormal
MRR_PARAMS = {"CLT": (5.7, 0.35), "PJ": (6.3, 0.45), "freelancer": (5.4, 0.50)}

MESES_LTV_RETIDO = 6  # valor retido em caso de aceite (consistente com o Modulo 1)


def offer_cost(offer: str, mrr: float) -> float:
    """Custo da intervencao em R$ (descontos custam % do MRR por 3 meses)."""
    if offer == "desconto_10":
        return 0.10 * 3 * mrr
    if offer == "desconto_20":
        return 0.20 * 3 * mrr
    if offer == "pausa_1_mes":
        return 1.0 * mrr
    if offer == "consulta_cs":
        return 250.0          # hora de especialista de CS + overhead de agenda
    if offer == "pix_boleto_flash":
        return 2.0            # custo operacional do link de pagamento
    raise ValueError(f"oferta desconhecida: {offer}")


def eprofit(p_accept: float, offer: str, mrr: float) -> float:
    """e-Profit esperado = P(aceite) x LTV_retido - custo."""
    return p_accept * (MESES_LTV_RETIDO * mrr) - offer_cost(offer, mrr)


class RetentionEnvironment:
    """Sorteia clientes em risco e responde aceite/recusa das ofertas."""

    def __init__(self, seed: int = 42) -> None:
        self.rng = np.random.default_rng(seed)

    def sample_customer(self) -> dict:
        profile = str(self.rng.choice(PROFILES, p=PROFILE_WEIGHTS))
        mu, sigma = MRR_PARAMS[profile]
        mrr = float(np.clip(self.rng.lognormal(mu, sigma), 80, 20_000))
        return {"profile": profile, "mrr": round(mrr, 2)}

    def respond(self, customer: dict, offer: str) -> bool:
        return bool(self.rng.uniform() < TRUE_ACCEPT[customer["profile"]][offer])

    def oracle_offer(self, customer: dict) -> str:
        """Braco otimo em e-Profit se as probabilidades fossem conhecidas."""
        return max(
            OFFERS,
            key=lambda o: eprofit(TRUE_ACCEPT[customer["profile"]][o], o, customer["mrr"]),
        )

"""Thompson Sampling (Beta-Bernoulli) para selecao de ofertas de retencao.

Cada par (segmento, oferta) mantem um posterior Beta(alpha, beta) sobre a
taxa de aceite. A cada decisao o bandit AMOSTRA uma taxa de cada posterior
e escolhe a oferta que maximiza o e-Profit com a taxa amostrada:

    escolha = argmax_o  p_amostrado(o) * LTV_retido - custo(o)

A incerteza faz a exploracao sozinha: bracos pouco testados tem posteriores
largos e as vezes amostram alto (sao tentados); bracos ruins ja observados
amostram baixo e saem de cena. Nao ha epsilon para calibrar — a exploracao
decai naturalmente conforme os posteriores afinam.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from src.environment import OFFERS, PROFILES, eprofit

# Prior fraco e otimista: equivale a 2 exibicoes com 1 aceite
PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0


class ThompsonSamplingBandit:
    def __init__(self, seed: int = 42, optimize_eprofit: bool = True) -> None:
        self.rng = np.random.default_rng(seed)
        self.optimize_eprofit = optimize_eprofit
        self.state = {
            p: {o: {"alpha": PRIOR_ALPHA, "beta": PRIOR_BETA} for o in OFFERS}
            for p in PROFILES
        }

    def choose_offer(self, profile: str, mrr: float) -> str:
        amostras = {
            o: self.rng.beta(s["alpha"], s["beta"])
            for o, s in self.state[profile].items()
        }
        if self.optimize_eprofit:
            return max(OFFERS, key=lambda o: eprofit(amostras[o], o, mrr))
        return max(OFFERS, key=lambda o: amostras[o])

    def record_outcome(self, profile: str, offer: str, accepted: bool) -> None:
        s = self.state[profile][offer]
        if accepted:
            s["alpha"] += 1.0
        else:
            s["beta"] += 1.0

    def conversion_rates(self, profile: str) -> dict:
        """Media do posterior por oferta."""
        return {
            o: round(s["alpha"] / (s["alpha"] + s["beta"]), 3)
            for o, s in self.state[profile].items()
        }

    # ── Persistencia ─────────────────────────────────────────────────────
    def save(self, path: Path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def load(self, path: Path) -> None:
        with open(path, encoding="utf-8") as f:
            self.state = json.load(f)


class EpsilonGreedyBandit:
    """Baseline: a estrategia anterior do CRAI (epsilon fixo, otimiza aceite)."""

    def __init__(self, epsilon: float = 0.2, seed: int = 42) -> None:
        self.epsilon = epsilon
        self.rng = np.random.default_rng(seed)
        self.stats = {p: {o: {"shown": 0, "accepted": 0} for o in OFFERS} for p in PROFILES}

    def choose_offer(self, profile: str, mrr: float) -> str:
        if self.rng.uniform() < self.epsilon:
            return str(self.rng.choice(OFFERS))
        s = self.stats[profile]
        return max(OFFERS, key=lambda o: s[o]["accepted"] / max(s[o]["shown"], 1))

    def record_outcome(self, profile: str, offer: str, accepted: bool) -> None:
        self.stats[profile][offer]["shown"] += 1
        if accepted:
            self.stats[profile][offer]["accepted"] += 1


class RandomBandit:
    """Baseline inferior: oferta aleatoria."""

    def __init__(self, seed: int = 42) -> None:
        self.rng = np.random.default_rng(seed)

    def choose_offer(self, profile: str, mrr: float) -> str:
        return str(self.rng.choice(OFFERS))

    def record_outcome(self, profile: str, offer: str, accepted: bool) -> None:
        pass

"""Testes de sanidade do Modulo 4.

    python -m tests.test_bandit
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from src.bandit import ThompsonSamplingBandit
from src.environment import (OFFERS, PROFILES, RetentionEnvironment,
                             TRUE_ACCEPT, eprofit, offer_cost)


def test_environment():
    env = RetentionEnvironment(seed=1)
    clientes = [env.sample_customer() for _ in range(500)]
    assert all(c["profile"] in PROFILES and c["mrr"] > 0 for c in clientes)
    # Taxa empirica de aceite deve aproximar a verdadeira
    aceites = [env.respond({"profile": "CLT", "mrr": 300}, "desconto_10") for _ in range(4000)]
    assert abs(np.mean(aceites) - TRUE_ACCEPT["CLT"]["desconto_10"]) < 0.04
    print("[ok] test_environment")


def test_custos_e_eprofit():
    assert abs(offer_cost("desconto_10", 1000) - 300.0) < 1e-6
    assert offer_cost("pix_boleto_flash", 1000) == 2.0
    # e-Profit cresce com P(aceite)
    assert eprofit(0.6, "desconto_10", 500) > eprofit(0.2, "desconto_10", 500)
    print("[ok] test_custos_e_eprofit")


def test_posterior_update():
    bandit = ThompsonSamplingBandit(seed=0)
    antes = bandit.conversion_rates("CLT")["desconto_10"]
    for _ in range(50):
        bandit.record_outcome("CLT", "desconto_10", accepted=True)
    depois = bandit.conversion_rates("CLT")["desconto_10"]
    assert depois > antes and depois > 0.9
    print("[ok] test_posterior_update")


def test_convergencia_rapida():
    """Com um braco claramente superior, Thompson deve concentra-lo em ~500 rodadas."""
    bandit = ThompsonSamplingBandit(seed=3, optimize_eprofit=False)
    rng = np.random.default_rng(3)
    verdade = {o: 0.10 for o in OFFERS}
    verdade["pausa_1_mes"] = 0.60
    escolhas_finais = []
    for t in range(800):
        offer = bandit.choose_offer("PJ", mrr=500)
        bandit.record_outcome("PJ", offer, rng.uniform() < verdade[offer])
        if t >= 600:
            escolhas_finais.append(offer)
    frac = np.mean([o == "pausa_1_mes" for o in escolhas_finais])
    assert frac > 0.85, f"nao convergiu: {frac:.0%}"
    print("[ok] test_convergencia_rapida")


def test_persistencia(tmp_dir: Path = Path(__file__).parent / "_tmp_state.json"):
    bandit = ThompsonSamplingBandit(seed=0)
    for _ in range(10):
        bandit.record_outcome("PJ", "pausa_1_mes", True)
    bandit.save(tmp_dir)
    novo = ThompsonSamplingBandit(seed=0)
    novo.load(tmp_dir)
    assert novo.state["PJ"]["pausa_1_mes"]["alpha"] == bandit.state["PJ"]["pausa_1_mes"]["alpha"]
    tmp_dir.unlink()
    print("[ok] test_persistencia")


if __name__ == "__main__":
    test_environment()
    test_custos_e_eprofit()
    test_posterior_update()
    test_convergencia_rapida()
    test_persistencia()
    print("\nTodos os testes passaram.")

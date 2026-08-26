"""Simulacao do Modulo 4: Thompson Sampling vs baselines.

Roda as 4 estrategias no MESMO fluxo de clientes (mesma seed do ambiente):

- random          : oferta aleatoria (piso)
- epsilon_greedy  : estrategia anterior do CRAI (epsilon=0.2, otimiza aceite)
- thompson_aceite : Thompson Sampling otimizando taxa de aceite
- thompson_eprofit: Thompson Sampling otimizando e-Profit (modelo final)

Cada rodada: o ambiente sorteia um cliente em risco, a estrategia escolhe a
oferta, o ambiente responde aceite/recusa, a estrategia aprende. Registramos
o e-Profit REALIZADO (LTV retido se aceitou, menos o custo da oferta) e o
regret vs oraculo (que conhece as probabilidades verdadeiras).

Salva:
- models/bandit_state.json : posteriores do thompson_eprofit (warm start p/ crai/)
- models/meta.json         : parametros da simulacao
- reports/simulacao.csv    : trajetoria por rodada e estrategia
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.bandit import EpsilonGreedyBandit, RandomBandit, ThompsonSamplingBandit
from src.environment import (MESES_LTV_RETIDO, OFFERS, RetentionEnvironment,
                             TRUE_ACCEPT, eprofit, offer_cost)

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"


def _realized_eprofit(accepted: bool, offer: str, mrr: float) -> float:
    ganho = MESES_LTV_RETIDO * mrr if accepted else 0.0
    return ganho - offer_cost(offer, mrr)


def simular(n_rounds: int = 6000, seed: int = 42) -> dict:
    estrategias = {
        "random": RandomBandit(seed=seed),
        "epsilon_greedy": EpsilonGreedyBandit(epsilon=0.2, seed=seed),
        "thompson_aceite": ThompsonSamplingBandit(seed=seed, optimize_eprofit=False),
        "thompson_eprofit": ThompsonSamplingBandit(seed=seed, optimize_eprofit=True),
    }

    # Mesmo fluxo de clientes para todas as estrategias (comparacao pareada)
    env_clientes = RetentionEnvironment(seed=seed)
    clientes = [env_clientes.sample_customer() for _ in range(n_rounds)]

    linhas = []
    for nome, bandit in estrategias.items():
        env = RetentionEnvironment(seed=seed + 1)  # respostas independentes da escolha
        for t, cliente in enumerate(clientes):
            offer = bandit.choose_offer(cliente["profile"], cliente["mrr"])
            accepted = env.respond(cliente, offer)
            bandit.record_outcome(cliente["profile"], offer, accepted)

            otima = env_clientes.oracle_offer(cliente)
            ep_esperado = eprofit(TRUE_ACCEPT[cliente["profile"]][offer], offer, cliente["mrr"])
            ep_otimo = eprofit(TRUE_ACCEPT[cliente["profile"]][otima], otima, cliente["mrr"])

            linhas.append({
                "estrategia": nome, "round": t, "profile": cliente["profile"],
                "offer": offer, "accepted": int(accepted),
                "eprofit_realizado": round(_realized_eprofit(accepted, offer, cliente["mrr"]), 2),
                "regret_esperado": round(ep_otimo - ep_esperado, 2),
                "escolheu_otima": int(offer == otima),
            })

    df = pd.DataFrame(linhas)
    REPORTS_DIR.mkdir(exist_ok=True)
    df.to_csv(REPORTS_DIR / "simulacao.csv", index=False)

    # Persistir os posteriores do modelo final como warm start para o crai/
    MODELS_DIR.mkdir(exist_ok=True)
    estrategias["thompson_eprofit"].save(MODELS_DIR / "bandit_state.json")

    meta = {
        "n_rounds": n_rounds,
        "seed": seed,
        "offers": OFFERS,
        "meses_ltv_retido": MESES_LTV_RETIDO,
        "estrategias": list(estrategias.keys()),
    }
    with open(MODELS_DIR / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    resumo = (
        df.groupby("estrategia")
        .agg(eprofit_total=("eprofit_realizado", "sum"),
             regret_total=("regret_esperado", "sum"),
             pct_otima=("escolheu_otima", "mean"),
             taxa_aceite=("accepted", "mean"))
        .round(2)
    )
    print(f"[ok] {n_rounds} rodadas x {len(estrategias)} estrategias")
    print(resumo.to_string())
    print(f"\n[ok] posteriores salvos em {MODELS_DIR / 'bandit_state.json'}")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    simular(args.rounds, args.seed)


if __name__ == "__main__":
    main()

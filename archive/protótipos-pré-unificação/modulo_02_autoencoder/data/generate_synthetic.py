"""Gerador de dados sinteticos para o Modulo 2 do CRAI.

Modela duas populacoes de clientes SaaS B2B:
- Saudaveis (~91%): uso estavel, baixo atrito, alta adocao de features.
- Anomalos (~9%): queda de uso, aumento de fricção, sinais de churn voluntario.

A separacao das duas populacoes na fase de geração serve apenas como
ground-truth para validar o autoencoder. Em producao, o modelo recebe apenas
o vetor de features e nao tem acesso ao rotulo.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = DATA_DIR / "behavioral_data.csv"

FEATURE_COLS = [
    "tenure_days",
    "mrr_brl",
    "seats",
    "logins_7d",
    "logins_30d",
    "feature_adoption",
    "avg_session_min",
    "api_calls_7d",
    "days_since_last_login",
    "tickets_30d",
    "failed_pay_90d",
    "nps_last",
]


def _gerar_saudaveis(n: int, rng: np.random.Generator) -> pd.DataFrame:
    tenure = rng.gamma(shape=2.5, scale=180, size=n).clip(30, 2000).astype(int)
    mrr = rng.lognormal(mean=8.5, sigma=0.7, size=n).clip(500, 50_000).round(2)
    seats = rng.poisson(lam=15, size=n).clip(1, 200)

    logins_30d = rng.normal(loc=seats * 18, scale=seats * 3, size=n).clip(1).astype(int)
    logins_7d = (logins_30d * rng.uniform(0.22, 0.30, size=n)).astype(int)

    feature_adoption = rng.beta(a=5, b=2, size=n).round(3)
    avg_session_min = rng.normal(loc=22, scale=6, size=n).clip(2).round(1)
    api_calls_7d = rng.lognormal(mean=7.0, sigma=0.8, size=n).clip(10).astype(int)
    days_since_login = rng.exponential(scale=1.5, size=n).clip(0, 30).astype(int)

    tickets_30d = rng.poisson(lam=1.2, size=n)
    failed_pay_90d = rng.binomial(n=3, p=0.05, size=n)
    nps = rng.normal(loc=8.2, scale=1.3, size=n).clip(0, 10).round(1)

    return pd.DataFrame({
        "customer_id": [f"C{i:06d}" for i in range(1, n + 1)],
        "tenure_days": tenure,
        "mrr_brl": mrr,
        "seats": seats,
        "logins_7d": logins_7d,
        "logins_30d": logins_30d,
        "feature_adoption": feature_adoption,
        "avg_session_min": avg_session_min,
        "api_calls_7d": api_calls_7d,
        "days_since_last_login": days_since_login,
        "tickets_30d": tickets_30d,
        "failed_pay_90d": failed_pay_90d,
        "nps_last": nps,
        "is_anomalous": 0,
    })


def _gerar_anomalos(n: int, rng: np.random.Generator) -> pd.DataFrame:
    tenure = rng.gamma(shape=2.5, scale=180, size=n).clip(30, 2000).astype(int)
    mrr = rng.lognormal(mean=8.5, sigma=0.7, size=n).clip(500, 50_000).round(2)
    seats = rng.poisson(lam=15, size=n).clip(1, 200)

    logins_30d = rng.normal(loc=seats * 5, scale=seats * 2, size=n).clip(0).astype(int)
    logins_7d = (logins_30d * rng.uniform(0.05, 0.15, size=n)).astype(int)

    feature_adoption = rng.beta(a=2, b=5, size=n).round(3)
    avg_session_min = rng.normal(loc=6, scale=3, size=n).clip(0.5).round(1)
    api_calls_7d = rng.lognormal(mean=4.5, sigma=1.0, size=n).clip(0).astype(int)
    days_since_login = rng.exponential(scale=9, size=n).clip(0, 30).astype(int)

    tickets_30d = rng.poisson(lam=4.5, size=n)
    failed_pay_90d = rng.binomial(n=3, p=0.35, size=n)
    nps = rng.normal(loc=5.5, scale=2.0, size=n).clip(0, 10).round(1)

    return pd.DataFrame({
        "customer_id": [f"A{i:06d}" for i in range(1, n + 1)],
        "tenure_days": tenure,
        "mrr_brl": mrr,
        "seats": seats,
        "logins_7d": logins_7d,
        "logins_30d": logins_30d,
        "feature_adoption": feature_adoption,
        "avg_session_min": avg_session_min,
        "api_calls_7d": api_calls_7d,
        "days_since_last_login": days_since_login,
        "tickets_30d": tickets_30d,
        "failed_pay_90d": failed_pay_90d,
        "nps_last": nps,
        "is_anomalous": 1,
    })


def gerar_dataset(n_saudaveis: int = 5000, n_anomalos: int = 500, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = pd.concat(
        [_gerar_saudaveis(n_saudaveis, rng), _gerar_anomalos(n_anomalos, rng)],
        ignore_index=True,
    )
    return df.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gerador de dados sinteticos CRAI - Modulo 2")
    parser.add_argument("--saudaveis", type=int, default=5000)
    parser.add_argument("--anomalos", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    df = gerar_dataset(args.saudaveis, args.anomalos, args.seed)
    df.to_csv(args.out, index=False)
    print(f"[ok] {len(df)} linhas escritas em {args.out}")
    print(f"     saudaveis: {(df.is_anomalous == 0).sum()}")
    print(f"     anomalos:  {(df.is_anomalous == 1).sum()}")


if __name__ == "__main__":
    main()

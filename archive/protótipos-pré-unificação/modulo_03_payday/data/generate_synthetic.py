"""Gerador de series de liquidez sinteticas para o Modulo 3 do CRAI.

Modela o saldo diario de clientes SaaS B2B brasileiros em 3 perfis de
recebimento, com a sazonalidade tipica do mercado BR:

- CLT        (~50%): salario no 5o dia util + adiantamento ~dia 20.
                     Liquidez alta e previsivel logo apos esses marcos.
- PJ         (~30%): notas fiscais pagas em torno dos dias 10, 15 e 30,
                     com variabilidade moderada de valor e atraso.
- freelancer (~20%): entradas irregulares (projetos), alta variancia,
                     sem ancora mensal forte.

Cada cliente gera uma serie diaria de `balance_norm` (saldo / mensalidade)
e o rotulo `has_liquidity` (saldo cobre a mensalidade). Em producao essas
series viriam do historico de transacoes do gateway / open finance.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = DATA_DIR / "liquidity_series.csv"

PROFILES = ["CLT", "PJ", "freelancer"]
PROFILE_WEIGHTS = [0.50, 0.30, 0.20]


def _dias_uteis_do_mes(dates: pd.DatetimeIndex) -> pd.Series:
    """Indice do dia util dentro do mes (1 = primeiro dia util)."""
    df = pd.DataFrame({"date": dates})
    df["is_bday"] = df["date"].dt.dayofweek < 5
    df["bday_idx"] = df.groupby([df["date"].dt.year, df["date"].dt.month])["is_bday"].cumsum()
    return df["bday_idx"].where(df["is_bday"], 0).astype(int)


def _entradas_clt(dates: pd.DatetimeIndex, bday_idx: pd.Series, rng: np.random.Generator) -> np.ndarray:
    """Salario no 5o dia util + adiantamento (~40%) proximo do dia 20."""
    salario = rng.uniform(2.5, 5.0)  # em multiplos da mensalidade
    entradas = np.zeros(len(dates))
    entradas[bday_idx.to_numpy() == 5] = salario * 0.6
    dia_adiantamento = int(np.clip(rng.normal(20, 1), 18, 22))
    entradas[dates.day == dia_adiantamento] += salario * 0.4
    return entradas


def _entradas_pj(dates: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    """Notas pagas em torno dos dias 10, 15 e 30, com atraso de 0-3 dias."""
    receita = rng.uniform(2.0, 6.0)
    entradas = np.zeros(len(dates))
    for ancora, peso in [(10, 0.4), (15, 0.3), (30, 0.3)]:
        atraso = int(rng.integers(0, 4))
        dia = min(ancora + atraso, 28) if ancora == 30 else ancora + atraso
        pago = rng.uniform() > 0.15  # 15% de inadimplencia do cliente do cliente
        if pago:
            entradas[dates.day == dia] += receita * peso * rng.uniform(0.7, 1.3)
    return entradas


def _entradas_freelancer(dates: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    """2-5 pagamentos por mes em dias aleatorios, valores erraticos."""
    entradas = np.zeros(len(dates))
    meses = pd.unique(dates.to_period("M"))
    for mes in meses:
        n_pagamentos = int(rng.integers(2, 6))
        dias_do_mes = np.where(dates.to_period("M") == mes)[0]
        if len(dias_do_mes) == 0:
            continue
        idx_pagamentos = rng.choice(dias_do_mes, size=min(n_pagamentos, len(dias_do_mes)), replace=False)
        entradas[idx_pagamentos] = rng.exponential(scale=1.2, size=len(idx_pagamentos))
    return entradas


def _gerar_serie_cliente(
    customer_id: str, profile: str, dates: pd.DatetimeIndex,
    bday_idx: pd.Series, rng: np.random.Generator,
) -> pd.DataFrame:
    if profile == "CLT":
        entradas = _entradas_clt(dates, bday_idx, rng)
        gasto_diario = rng.uniform(0.08, 0.14)
    elif profile == "PJ":
        entradas = _entradas_pj(dates, rng)
        gasto_diario = rng.uniform(0.10, 0.18)
    else:
        entradas = _entradas_freelancer(dates, rng)
        gasto_diario = rng.uniform(0.10, 0.20)

    saldo = np.zeros(len(dates))
    atual = rng.uniform(0.2, 1.5)  # saldo inicial em multiplos da mensalidade
    for i in range(len(dates)):
        atual = max(0.0, atual + entradas[i] - gasto_diario * rng.uniform(0.5, 1.5))
        saldo[i] = atual

    return pd.DataFrame({
        "customer_id": customer_id,
        "profile": profile,
        "date": dates,
        "day_of_month": dates.day,
        "weekday": dates.dayofweek,
        "bday_idx": bday_idx.to_numpy(),
        "balance_norm": np.round(saldo, 4),
        "has_liquidity": (saldo >= 1.0).astype(int),
    })


def gerar_dataset(n_clientes: int = 600, n_dias: int = 180, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n_dias, freq="D")
    bday_idx = _dias_uteis_do_mes(dates)

    perfis = rng.choice(PROFILES, size=n_clientes, p=PROFILE_WEIGHTS)
    series = [
        _gerar_serie_cliente(f"C{i:05d}", perfis[i], dates, bday_idx, rng)
        for i in range(n_clientes)
    ]
    return pd.concat(series, ignore_index=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gerador de series de liquidez CRAI - Modulo 3")
    parser.add_argument("--clientes", type=int, default=600)
    parser.add_argument("--dias", type=int, default=180)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    df = gerar_dataset(args.clientes, args.dias, args.seed)
    df.to_csv(args.out, index=False)
    print(f"[ok] {len(df)} linhas escritas em {args.out}")
    for p in PROFILES:
        sub = df[df.profile == p]
        print(f"     {p:11s}: {sub.customer_id.nunique():4d} clientes | liquidez media {sub.has_liquidity.mean():.2%}")


if __name__ == "__main__":
    main()

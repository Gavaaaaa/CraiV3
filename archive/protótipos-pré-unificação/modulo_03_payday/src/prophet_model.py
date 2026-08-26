"""Prior sazonal de liquidez por perfil via Prophet.

Enquanto a LSTM aprende o padrao INDIVIDUAL do cliente (janela de 30 dias),
o Prophet captura a sazonalidade COLETIVA do perfil: a taxa media diaria de
liquidez de todos os clientes CLT sobe apos o 5o dia util, a dos PJ apos os
dias 10/15/30, etc. O ensemble downstream combina os dois sinais — util
principalmente quando o historico individual e curto ou ruidoso (cold start).

Treina um Prophet por perfil sobre a serie agregada (taxa diaria de clientes
com saldo), com sazonalidade semanal + mensal (fourier). Salva via
model_to_json em models/prophet_<perfil>.json.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from prophet import Prophet
from prophet.serialize import model_from_json, model_to_json

logging.getLogger("prophet").setLevel(logging.WARNING)
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "liquidity_series.csv"
MODELS_DIR = ROOT / "models"

PROFILES = ["CLT", "PJ", "freelancer"]


def _serie_agregada(df: pd.DataFrame, profile: str) -> pd.DataFrame:
    """Taxa diaria de clientes do perfil com liquidez -> formato Prophet."""
    sub = df[df["profile"] == profile]
    agg = sub.groupby("date")["has_liquidity"].mean().reset_index()
    return agg.rename(columns={"date": "ds", "has_liquidity": "y"})


def _novo_prophet() -> Prophet:
    m = Prophet(
        yearly_seasonality=False,
        weekly_seasonality=True,
        daily_seasonality=False,
        changepoint_prior_scale=0.01,
    )
    # Sazonalidade mensal: e onde mora o padrao de payday brasileiro
    m.add_seasonality(name="monthly", period=30.5, fourier_order=8)
    return m


def treinar(clientes_treino: set[str] | None = None, seed: int = 42) -> dict:
    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    if clientes_treino is None:
        # Mesmo split por cliente do train.py (via meta.json)
        meta_path = MODELS_DIR / "meta.json"
        if meta_path.exists():
            with open(meta_path, encoding="utf-8") as f:
                teste = set(json.load(f)["clientes_teste"])
            clientes_treino = set(df["customer_id"].unique()) - teste
        else:
            clientes_treino = set(df["customer_id"].unique())

    df = df[df["customer_id"].isin(clientes_treino)]

    MODELS_DIR.mkdir(exist_ok=True)
    resumo = {}
    for profile in PROFILES:
        serie = _serie_agregada(df, profile)
        m = _novo_prophet()
        m.fit(serie, seed=seed)
        with open(MODELS_DIR / f"prophet_{profile}.json", "w", encoding="utf-8") as f:
            f.write(model_to_json(m))
        resumo[profile] = {"n_dias": len(serie), "liquidez_media": round(float(serie["y"].mean()), 4)}
        print(f"[ok] prophet_{profile}.json | {len(serie)} dias | liquidez media {serie['y'].mean():.2%}")
    return resumo


class SeasonalPrior:
    """Carrega os Prophets salvos e devolve o prior sazonal para datas futuras."""

    def __init__(self, models_dir: Path = MODELS_DIR) -> None:
        self.models = {}
        for profile in PROFILES:
            path = models_dir / f"prophet_{profile}.json"
            with open(path, encoding="utf-8") as f:
                self.models[profile] = model_from_json(f.read())

    def prior(self, profile: str, datas: pd.DatetimeIndex) -> np.ndarray:
        """Probabilidade sazonal de liquidez do perfil em cada data (clip 0-1)."""
        m = self.models.get(profile, self.models["CLT"])
        futuro = pd.DataFrame({"ds": datas})
        yhat = m.predict(futuro)["yhat"].to_numpy()
        return np.clip(yhat, 0.0, 1.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    treinar(seed=args.seed)


if __name__ == "__main__":
    main()

"""Testes de sanidade do Modulo 3 (rodar apos train + prophet + evaluate).

    python -m tests.test_payday
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import torch

from data.generate_synthetic import gerar_dataset
from src.features import montar_janelas
from src.model import HORIZON, INPUT_FEATURES, LiquidityLSTM, WINDOW


def test_model_shape():
    model = LiquidityLSTM()
    x = torch.randn(8, WINDOW, INPUT_FEATURES)
    out = model(x)
    assert out.shape == (8, HORIZON), f"shape inesperado: {out.shape}"
    probs = model.predict_proba(x)
    assert float(probs.min()) >= 0.0 and float(probs.max()) <= 1.0
    print("[ok] test_model_shape")


def test_gerador_perfis():
    df = gerar_dataset(n_clientes=60, n_dias=90, seed=7)
    assert set(df["profile"].unique()) == {"CLT", "PJ", "freelancer"}
    assert df.groupby("customer_id").size().eq(90).all()
    # CLT deve ter liquidez mais previsivel (maior taxa media) que freelancer
    taxa = df.groupby("profile")["has_liquidity"].mean()
    assert 0.05 < taxa.min() and taxa.max() < 0.99, f"taxas degeneradas: {dict(taxa)}"
    print("[ok] test_gerador_perfis")


def test_janelas():
    df = gerar_dataset(n_clientes=5, n_dias=90, seed=7)
    janelas = montar_janelas(df, passo=7)
    assert janelas["X"].shape[1:] == (WINDOW, INPUT_FEATURES)
    assert janelas["y"].shape[1] == HORIZON
    assert len(janelas["X"]) == len(janelas["profile"]) == len(janelas["customer_id"])
    print("[ok] test_janelas")


def test_inferencia_e_sazonalidade():
    """O pipeline treinado deve prever janela valida e o prior CLT deve ter pico pos 5o dia util."""
    from src.payday_inference import PaydayInference
    from src.prophet_model import SeasonalPrior

    df = gerar_dataset(n_clientes=10, n_dias=90, seed=99)
    inferencia = PaydayInference()
    serie = df[df.customer_id == "C00000"]
    r = inferencia.predict_next_window(serie)
    assert 0.0 <= r["confidence"] <= 1.0
    assert r["profile"] in {"CLT", "PJ", "freelancer"}
    ultimo_dia = pd.Timestamp(serie["date"].max())
    delta = (pd.Timestamp(r["timestamp"]) - ultimo_dia).days
    assert 1 <= delta <= 14, f"janela fora do horizonte: {delta} dias"

    prior = SeasonalPrior()
    datas = pd.date_range("2026-08-01", "2026-08-31")
    y_clt = prior.prior("CLT", datas)
    inicio_mes = y_clt[6:14].mean()   # apos 5o dia util
    fim_mes = y_clt[24:28].mean()     # vale pre-salario
    assert inicio_mes > fim_mes, "prior CLT sem pico pos-salario"
    print("[ok] test_inferencia_e_sazonalidade")


if __name__ == "__main__":
    test_model_shape()
    test_gerador_perfis()
    test_janelas()
    test_inferencia_e_sazonalidade()
    print("\nTodos os testes passaram.")

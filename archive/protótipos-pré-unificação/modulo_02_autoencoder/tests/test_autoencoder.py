"""Testes de sanidade do Modulo 2.

Sao testes leves voltados a defender invariantes principais:
- o modelo produz output do mesmo shape da entrada;
- o detector separa um cliente notoriamente saudavel de um em risco;
- o gerador respeita as quantidades configuradas.
"""
from __future__ import annotations

import numpy as np
import torch

from data.generate_synthetic import FEATURE_COLS, gerar_dataset
from src.anomaly_detector import AnomalyDetector
from src.model import BehaviorAutoencoder


def test_model_shape() -> None:
    model = BehaviorAutoencoder(input_dim=12, bottleneck=4)
    x = torch.randn(8, 12)
    y = model(x)
    assert y.shape == x.shape


def test_gerador_quantidades() -> None:
    df = gerar_dataset(n_saudaveis=100, n_anomalos=20, seed=7)
    assert len(df) == 120
    assert (df.is_anomalous == 0).sum() == 100
    assert (df.is_anomalous == 1).sum() == 20
    for col in FEATURE_COLS:
        assert col in df.columns


def test_detector_separa_perfis() -> None:
    detector = AnomalyDetector()

    saudavel = {
        "tenure_days": 412, "mrr_brl": 4890.0, "seats": 14,
        "logins_7d": 68, "logins_30d": 251, "feature_adoption": 0.812,
        "avg_session_min": 24.3, "api_calls_7d": 2140, "days_since_last_login": 1,
        "tickets_30d": 1, "failed_pay_90d": 0, "nps_last": 9.1,
    }
    em_risco = {
        "tenure_days": 421, "mrr_brl": 5200.0, "seats": 15,
        "logins_7d": 3, "logins_30d": 41, "feature_adoption": 0.156,
        "avg_session_min": 4.1, "api_calls_7d": 89, "days_since_last_login": 18,
        "tickets_30d": 6, "failed_pay_90d": 1, "nps_last": 3.1,
    }

    score_saudavel = detector.score(saudavel)[0]
    score_em_risco = detector.score(em_risco)[0]

    assert score_em_risco > score_saudavel
    assert detector.is_anomaly(em_risco)[0]
    assert not detector.is_anomaly(saudavel)[0]


if __name__ == "__main__":
    test_model_shape()
    print("[ok] test_model_shape")
    test_gerador_quantidades()
    print("[ok] test_gerador_quantidades")
    test_detector_separa_perfis()
    print("[ok] test_detector_separa_perfis")
    print("\nTodos os testes passaram.")

"""Interface publica do Modulo 2 para os demais modulos do CRAI.

O agente LangGraph (Modulo 5) consome esta classe sem precisar conhecer
detalhes de PyTorch. Recebe um cliente (dict ou DataFrame), devolve score
de anomalia e flag binaria.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import pandas as pd
import torch

from src.model import BehaviorAutoencoder

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
DEFAULT_THRESHOLD_PERCENTIL = 95.0


class AnomalyDetector:
    def __init__(self, models_dir: Path = MODELS_DIR, threshold: float | None = None) -> None:
        with open(models_dir / "meta.json", encoding="utf-8") as f:
            self._meta = json.load(f)

        self.features = self._meta["features"]
        self.model = BehaviorAutoencoder(
            input_dim=self._meta["input_dim"],
            bottleneck=self._meta["bottleneck"],
        )
        self.model.load_state_dict(torch.load(models_dir / "autoencoder.pt"))
        self.model.eval()
        self.scaler = joblib.load(models_dir / "scaler.pkl")

        self.threshold = threshold if threshold is not None else self._calibrar_threshold()

    def _calibrar_threshold(self) -> float:
        metricas_path = ROOT / "reports" / "metricas.json"
        if metricas_path.exists():
            with open(metricas_path, encoding="utf-8") as f:
                return float(json.load(f)["threshold_valor"])
        return 1.0

    def _para_array(self, clientes: dict | list[dict] | pd.DataFrame) -> np.ndarray:
        if isinstance(clientes, dict):
            clientes = [clientes]
        if isinstance(clientes, list):
            clientes = pd.DataFrame(clientes)
        faltando = [c for c in self.features if c not in clientes.columns]
        if faltando:
            raise ValueError(f"colunas ausentes: {faltando}")
        return clientes[self.features].to_numpy(dtype=np.float32)

    def score(self, clientes: dict | list[dict] | pd.DataFrame) -> np.ndarray:
        """Erro de reconstrucao por cliente. Maior = mais anomalo."""
        X = self.scaler.transform(self._para_array(clientes)).astype(np.float32)
        with torch.no_grad():
            recon = self.model(torch.from_numpy(X))
            erros = ((recon - torch.from_numpy(X)) ** 2).mean(dim=1).numpy()
        return erros

    def is_anomaly(self, clientes: dict | list[dict] | pd.DataFrame) -> np.ndarray:
        return self.score(clientes) > self.threshold

    def explicar(self, cliente: dict) -> dict:
        """Quebra do erro por feature para o agente entender o "porque" da anomalia."""
        X = self.scaler.transform(self._para_array(cliente)).astype(np.float32)
        with torch.no_grad():
            tensor = torch.from_numpy(X)
            recon = self.model(tensor).numpy()
        x_arr = X[0]
        recon_arr = recon[0]
        erro_por_feature = (recon_arr - x_arr) ** 2

        ranking = sorted(
            zip(self.features, erro_por_feature.tolist(), x_arr.tolist()),
            key=lambda t: t[1],
            reverse=True,
        )
        return {
            "score": float(erro_por_feature.mean()),
            "is_anomaly": bool(erro_por_feature.mean() > self.threshold),
            "threshold": self.threshold,
            "top_features": [
                {"feature": nome, "contribuicao": contrib, "valor_normalizado": valor}
                for nome, contrib, valor in ranking[:5]
            ],
        }


def main() -> None:
    detector = AnomalyDetector()
    cliente_saudavel = {
        "tenure_days": 412, "mrr_brl": 4890.0, "seats": 14,
        "logins_7d": 68, "logins_30d": 251, "feature_adoption": 0.812,
        "avg_session_min": 24.3, "api_calls_7d": 2140, "days_since_last_login": 1,
        "tickets_30d": 1, "failed_pay_90d": 0, "nps_last": 9.1,
    }
    cliente_em_risco = {
        "tenure_days": 421, "mrr_brl": 5200.0, "seats": 15,
        "logins_7d": 7, "logins_30d": 68, "feature_adoption": 0.218,
        "avg_session_min": 5.4, "api_calls_7d": 142, "days_since_last_login": 11,
        "tickets_30d": 5, "failed_pay_90d": 2, "nps_last": 4.2,
    }

    print("=" * 60)
    print("DEMO - AnomalyDetector")
    print("=" * 60)
    for nome, cliente in [("saudavel", cliente_saudavel), ("em risco", cliente_em_risco)]:
        explicacao = detector.explicar(cliente)
        print(f"\nCliente {nome}:")
        print(f"  score      : {explicacao['score']:.4f}")
        print(f"  threshold  : {explicacao['threshold']:.4f}")
        print(f"  is_anomaly : {explicacao['is_anomaly']}")
        print(f"  top features que contribuem:")
        for item in explicacao["top_features"]:
            print(f"    - {item['feature']:25s} contrib={item['contribuicao']:.3f}")


if __name__ == "__main__":
    main()

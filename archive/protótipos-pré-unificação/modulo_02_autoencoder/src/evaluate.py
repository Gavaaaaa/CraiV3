"""Avaliacao do BehaviorAutoencoder em dataset rotulado.

O modelo nunca viu o rotulo durante o treino - so foi exposto a clientes
saudaveis. Aqui usamos os anomalos sinteticos como conjunto de teste para
medir se o erro de reconstrucao separa as duas populacoes.

Define o threshold de anomalia como o percentil 95 do erro de reconstrucao
dos saudaveis de treino. Em producao, esse threshold sera recalibrado
periodicamente conforme novos dados chegam.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
)

from src.model import BehaviorAutoencoder

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "behavioral_data.csv"
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"

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


def _carregar_modelo() -> tuple[BehaviorAutoencoder, object, dict]:
    with open(MODELS_DIR / "meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    model = BehaviorAutoencoder(input_dim=meta["input_dim"], bottleneck=meta["bottleneck"])
    model.load_state_dict(torch.load(MODELS_DIR / "autoencoder.pt"))
    model.eval()
    scaler = joblib.load(MODELS_DIR / "scaler.pkl")
    return model, scaler, meta


def _erro_reconstrucao(model: BehaviorAutoencoder, X: np.ndarray) -> np.ndarray:
    tensor = torch.from_numpy(X.astype(np.float32))
    return model.reconstruction_error(tensor).numpy()


def avaliar(percentil_threshold: float = 95.0) -> dict:
    REPORTS_DIR.mkdir(exist_ok=True)
    model, scaler, _ = _carregar_modelo()

    df = pd.read_csv(DATA_PATH)
    X = scaler.transform(df[FEATURE_COLS].to_numpy())
    y = df["is_anomalous"].to_numpy()

    erros = _erro_reconstrucao(model, X)

    erros_saudaveis = erros[y == 0]
    threshold = float(np.percentile(erros_saudaveis, percentil_threshold))

    preds = (erros > threshold).astype(int)

    roc_auc = float(roc_auc_score(y, erros))
    avg_precision = float(average_precision_score(y, erros))
    tn, fp, fn, tp = confusion_matrix(y, preds).ravel()
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    prec_curve, rec_curve, _ = precision_recall_curve(y, erros)
    f1_curve = 2 * prec_curve * rec_curve / np.where(prec_curve + rec_curve > 0, prec_curve + rec_curve, 1)
    f1_max = float(f1_curve.max())

    resultados = {
        "threshold_percentil": percentil_threshold,
        "threshold_valor": threshold,
        "roc_auc": roc_auc,
        "average_precision": avg_precision,
        "precision_at_threshold": float(precision),
        "recall_at_threshold": float(recall),
        "f1_at_threshold": float(f1),
        "f1_max_no_dataset": f1_max,
        "matriz_confusao": {
            "true_negatives": int(tn),
            "false_positives": int(fp),
            "false_negatives": int(fn),
            "true_positives": int(tp),
        },
        "erro_recon_saudaveis": {
            "media": float(erros_saudaveis.mean()),
            "mediana": float(np.median(erros_saudaveis)),
            "p95": float(np.percentile(erros_saudaveis, 95)),
            "p99": float(np.percentile(erros_saudaveis, 99)),
        },
        "erro_recon_anomalos": {
            "media": float(erros[y == 1].mean()),
            "mediana": float(np.median(erros[y == 1])),
            "p05": float(np.percentile(erros[y == 1], 5)),
        },
    }

    with open(REPORTS_DIR / "metricas.json", "w", encoding="utf-8") as f:
        json.dump(resultados, f, indent=2, ensure_ascii=False)

    df_scores = pd.DataFrame({
        "customer_id": df["customer_id"],
        "is_anomalous": y,
        "reconstruction_error": erros,
        "predicted_anomalous": preds,
    })
    df_scores.to_csv(REPORTS_DIR / "scores.csv", index=False)

    print("=" * 60)
    print("AVALIACAO - Modulo 2 (Autoencoder)")
    print("=" * 60)
    print(f"ROC-AUC                   : {roc_auc:.4f}")
    print(f"Average Precision (PR-AUC): {avg_precision:.4f}")
    print(f"F1 maximo no dataset      : {f1_max:.4f}")
    print()
    print(f"Threshold (p{int(percentil_threshold)} saudaveis): {threshold:.4f}")
    print(f"  precision               : {precision:.4f}")
    print(f"  recall                  : {recall:.4f}")
    print(f"  f1                      : {f1:.4f}")
    print()
    print("Matriz de confusao @ threshold:")
    print(f"  TN={tn:5d}  FP={fp:5d}")
    print(f"  FN={fn:5d}  TP={tp:5d}")
    print()
    print(f"Erro saudaveis (media)    : {erros_saudaveis.mean():.4f}")
    print(f"Erro anomalos  (media)    : {erros[y == 1].mean():.4f}")
    print(f"Separacao (ratio)         : {erros[y == 1].mean() / erros_saudaveis.mean():.2f}x")
    print()
    print(f"[ok] metricas salvas em {REPORTS_DIR / 'metricas.json'}")
    return resultados


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--percentil", type=float, default=95.0)
    args = parser.parse_args()
    avaliar(args.percentil)


if __name__ == "__main__":
    main()

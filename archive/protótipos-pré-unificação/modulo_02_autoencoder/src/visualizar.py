"""Gera figuras para o relatorio do Modulo 2."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve, roc_curve

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"
FIG_DIR = REPORTS_DIR / "figures"


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    scores = pd.read_csv(REPORTS_DIR / "scores.csv")
    y = scores["is_anomalous"].to_numpy()
    erros = scores["reconstruction_error"].to_numpy()

    erros_sau = erros[y == 0]
    erros_ano = erros[y == 1]

    fig, ax = plt.subplots(figsize=(9, 5))
    bins = np.logspace(np.log10(max(erros.min(), 1e-4)), np.log10(erros.max()), 60)
    ax.hist(erros_sau, bins=bins, alpha=0.65, label="Saudaveis", color="#4C9AFF")
    ax.hist(erros_ano, bins=bins, alpha=0.75, label="Anomalos", color="#FF5630")
    threshold = float(np.percentile(erros_sau, 95))
    ax.axvline(threshold, linestyle="--", color="black", label=f"Threshold (p95) = {threshold:.3f}")
    ax.set_xscale("log")
    ax.set_xlabel("Erro de reconstrucao (log scale)")
    ax.set_ylabel("Numero de clientes")
    ax.set_title("Distribuicao do erro de reconstrucao - separacao das populacoes")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "distribuicao_erros.png", dpi=140)
    plt.close(fig)

    fpr, tpr, _ = roc_curve(y, erros)
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot(fpr, tpr, label="Autoencoder", color="#4C9AFF", linewidth=2)
    ax.plot([0, 1], [0, 1], "--", color="gray", label="Random")
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title("Curva ROC")
    ax.legend()
    fig.tight_layout()
    fig.savefig(FIG_DIR / "roc.png", dpi=140)
    plt.close(fig)

    precision, recall, _ = precision_recall_curve(y, erros)
    fig, ax = plt.subplots(figsize=(6, 6))
    ax.plot(recall, precision, color="#36B37E", linewidth=2)
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title("Curva Precision-Recall")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "precision_recall.png", dpi=140)
    plt.close(fig)

    print(f"[ok] 3 figuras salvas em {FIG_DIR}")


if __name__ == "__main__":
    main()

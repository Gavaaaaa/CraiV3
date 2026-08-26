"""Treino da LiquidityLSTM.

Split por CLIENTE (nao por janela): 80% dos clientes treinam, 20% avaliam.
Janelas do mesmo cliente nunca aparecem nos dois lados, evitando vazamento
de padrao individual (a serie de um cliente e altamente autocorrelacionada).

Salva artefatos em models/:
- lstm.pt      pesos do modelo
- meta.json    hiperparametros + clientes de teste (para o evaluate)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from src.features import montar_janelas
from src.model import HORIZON, LiquidityLSTM, WINDOW

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "liquidity_series.csv"
MODELS_DIR = ROOT / "models"


def treinar(
    epochs: int = 40,
    batch_size: int = 256,
    lr: float = 1e-3,
    hidden: int = 64,
    patience: int = 6,
    seed: int = 42,
) -> dict:
    torch.manual_seed(seed)
    np.random.seed(seed)

    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    clientes = np.sort(df["customer_id"].unique())
    rng = np.random.default_rng(seed)
    rng.shuffle(clientes)
    corte = int(len(clientes) * 0.8)
    clientes_treino = set(clientes[:corte])
    clientes_teste = sorted(clientes[corte:])

    janelas = montar_janelas(df[df["customer_id"].isin(clientes_treino)])
    X, y = janelas["X"], janelas["y"]

    # validacao interna: ultimos 15% das janelas de treino (ja embaralhadas por cliente)
    perm = rng.permutation(len(X))
    X, y = X[perm], y[perm]
    n_val = int(len(X) * 0.15)
    X_val, y_val = torch.from_numpy(X[:n_val]), torch.from_numpy(y[:n_val])
    X_tr, y_tr = X[n_val:], y[n_val:]

    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_tr), torch.from_numpy(y_tr)),
        batch_size=batch_size,
        shuffle=True,
    )

    model = LiquidityLSTM(hidden_size=hidden)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.BCEWithLogitsLoss()

    historico = {"train_loss": [], "val_loss": []}
    melhor_val = float("inf")
    sem_melhora = 0
    melhor_state = None

    for epoch in range(1, epochs + 1):
        model.train()
        soma, n = 0.0, 0
        for xb, yb in train_loader:
            optimizer.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            optimizer.step()
            soma += loss.item() * xb.size(0)
            n += xb.size(0)
        train_loss = soma / n

        model.eval()
        with torch.no_grad():
            val_loss = loss_fn(model(X_val), y_val).item()

        historico["train_loss"].append(train_loss)
        historico["val_loss"].append(val_loss)
        print(f"epoch {epoch:3d} | train {train_loss:.5f} | val {val_loss:.5f}")

        if val_loss < melhor_val - 1e-5:
            melhor_val = val_loss
            sem_melhora = 0
            melhor_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            sem_melhora += 1
            if sem_melhora >= patience:
                print(f"[early stop] sem melhora ha {patience} epocas")
                break

    if melhor_state is not None:
        model.load_state_dict(melhor_state)

    MODELS_DIR.mkdir(exist_ok=True)
    torch.save(model.state_dict(), MODELS_DIR / "lstm.pt")

    meta = {
        "window": WINDOW,
        "horizon": HORIZON,
        "hidden_size": hidden,
        "epochs_treinadas": len(historico["train_loss"]),
        "melhor_val_loss": melhor_val,
        "n_janelas_treino": int(len(X_tr)),
        "n_clientes_treino": len(clientes_treino),
        "clientes_teste": clientes_teste,
        "seed": seed,
    }
    with open(MODELS_DIR / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"\n[ok] modelo salvo em {MODELS_DIR / 'lstm.pt'}")
    print(f"[ok] meta salvo em {MODELS_DIR / 'meta.json'} ({len(clientes_teste)} clientes de teste)")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--patience", type=int, default=6)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    treinar(args.epochs, args.batch_size, args.lr, args.hidden, args.patience, args.seed)


if __name__ == "__main__":
    main()

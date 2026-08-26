"""Loop de treino do BehaviorAutoencoder.

Premissa central: treinar somente com clientes saudaveis. Isso forca o
modelo a aprender a "geometria" do comportamento normal; qualquer cliente
fora dessa distribuicao produz reconstrucao ruim.

Salva artefatos em models/:
- autoencoder.pt   pesos do modelo
- scaler.pkl       StandardScaler ajustado nos saudaveis
- meta.json        hiperparametros e features (para reproducibilidade)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from src.model import BehaviorAutoencoder

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "behavioral_data.csv"
MODELS_DIR = ROOT / "models"

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


def carregar_dados(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(path)
    saudaveis = df[df.is_anomalous == 0].copy()
    anomalos = df[df.is_anomalous == 1].copy()
    return saudaveis, anomalos


def treinar(
    epochs: int = 100,
    batch_size: int = 128,
    lr: float = 1e-3,
    bottleneck: int = 4,
    patience: int = 10,
    seed: int = 42,
) -> dict:
    torch.manual_seed(seed)
    np.random.seed(seed)

    saudaveis, _ = carregar_dados(DATA_PATH)
    X = saudaveis[FEATURE_COLS].to_numpy(dtype=np.float32)

    X_train, X_val = train_test_split(X, test_size=0.15, random_state=seed)

    scaler = StandardScaler().fit(X_train)
    X_train_s = scaler.transform(X_train).astype(np.float32)
    X_val_s = scaler.transform(X_val).astype(np.float32)

    train_loader = DataLoader(
        TensorDataset(torch.from_numpy(X_train_s)),
        batch_size=batch_size,
        shuffle=True,
    )
    val_tensor = torch.from_numpy(X_val_s)

    model = BehaviorAutoencoder(input_dim=len(FEATURE_COLS), bottleneck=bottleneck)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()

    historico = {"train_loss": [], "val_loss": []}
    melhor_val = float("inf")
    epocas_sem_melhora = 0
    melhor_state = None

    for epoch in range(1, epochs + 1):
        model.train()
        soma = 0.0
        n = 0
        for (batch,) in train_loader:
            optimizer.zero_grad()
            recon = model(batch)
            loss = loss_fn(recon, batch)
            loss.backward()
            optimizer.step()
            soma += loss.item() * batch.size(0)
            n += batch.size(0)
        train_loss = soma / n

        model.eval()
        with torch.no_grad():
            val_loss = loss_fn(model(val_tensor), val_tensor).item()

        historico["train_loss"].append(train_loss)
        historico["val_loss"].append(val_loss)
        print(f"epoch {epoch:3d} | train {train_loss:.5f} | val {val_loss:.5f}")

        if val_loss < melhor_val - 1e-5:
            melhor_val = val_loss
            epocas_sem_melhora = 0
            melhor_state = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            epocas_sem_melhora += 1
            if epocas_sem_melhora >= patience:
                print(f"[early stop] sem melhora ha {patience} epocas")
                break

    if melhor_state is not None:
        model.load_state_dict(melhor_state)

    MODELS_DIR.mkdir(exist_ok=True)
    torch.save(model.state_dict(), MODELS_DIR / "autoencoder.pt")
    joblib.dump(scaler, MODELS_DIR / "scaler.pkl")

    meta = {
        "features": FEATURE_COLS,
        "input_dim": len(FEATURE_COLS),
        "bottleneck": bottleneck,
        "epochs_treinadas": len(historico["train_loss"]),
        "melhor_val_loss": melhor_val,
        "n_treino_saudaveis": int(X_train.shape[0]),
        "n_val_saudaveis": int(X_val.shape[0]),
        "seed": seed,
    }
    with open(MODELS_DIR / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"\n[ok] modelo salvo em {MODELS_DIR / 'autoencoder.pt'}")
    print(f"[ok] scaler salvo em {MODELS_DIR / 'scaler.pkl'}")
    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--bottleneck", type=int, default=4)
    parser.add_argument("--patience", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    treinar(args.epochs, args.batch_size, args.lr, args.bottleneck, args.patience, args.seed)


if __name__ == "__main__":
    main()

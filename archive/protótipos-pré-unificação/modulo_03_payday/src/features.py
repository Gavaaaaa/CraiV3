"""Construcao de janelas (30 dias -> 14 dias) a partir das series de liquidez.

Compartilhado por train.py, evaluate.py e pela interface publica, garantindo
que treino e inferencia usem exatamente a mesma featurizacao.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.model import HORIZON, INPUT_FEATURES, WINDOW


def featurizar_serie(df_cliente: pd.DataFrame) -> np.ndarray:
    """Serie diaria de um cliente -> matriz (n_dias, INPUT_FEATURES)."""
    dia = df_cliente["day_of_month"].to_numpy()
    return np.stack(
        [
            df_cliente["has_liquidity"].to_numpy(dtype=np.float32),
            np.clip(df_cliente["balance_norm"].to_numpy(dtype=np.float32), 0, 5),
            np.sin(2 * np.pi * dia / 31).astype(np.float32),
            np.cos(2 * np.pi * dia / 31).astype(np.float32),
            (df_cliente["weekday"].to_numpy() < 5).astype(np.float32),
        ],
        axis=1,
    )


def janelas_do_cliente(
    df_cliente: pd.DataFrame, passo: int = 7
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fatia a serie em janelas deslizantes.

    Retorna (X, y, idx_inicio_horizonte):
      X: (n, WINDOW, INPUT_FEATURES)
      y: (n, HORIZON) - has_liquidity dos 14 dias seguintes
    """
    feats = featurizar_serie(df_cliente)
    liquidez = df_cliente["has_liquidity"].to_numpy(dtype=np.float32)

    X, y, idx = [], [], []
    for inicio in range(0, len(feats) - WINDOW - HORIZON + 1, passo):
        X.append(feats[inicio : inicio + WINDOW])
        y.append(liquidez[inicio + WINDOW : inicio + WINDOW + HORIZON])
        idx.append(inicio + WINDOW)
    if not X:
        vazio = np.empty((0, WINDOW, INPUT_FEATURES), dtype=np.float32)
        return vazio, np.empty((0, HORIZON), dtype=np.float32), np.empty(0, dtype=int)
    return np.stack(X), np.stack(y), np.array(idx)


def montar_janelas(df: pd.DataFrame, passo: int = 7) -> dict:
    """Janelas de todos os clientes, preservando customer_id e perfil."""
    Xs, ys, perfis, clientes, idxs = [], [], [], [], []
    for cid, grupo in df.groupby("customer_id", sort=True):
        X, y, idx = janelas_do_cliente(grupo.sort_values("date"), passo)
        if len(X) == 0:
            continue
        Xs.append(X)
        ys.append(y)
        idxs.append(idx)
        perfis.extend([grupo["profile"].iloc[0]] * len(X))
        clientes.extend([cid] * len(X))
    return {
        "X": np.concatenate(Xs),
        "y": np.concatenate(ys),
        "idx": np.concatenate(idxs),
        "profile": np.array(perfis),
        "customer_id": np.array(clientes),
    }

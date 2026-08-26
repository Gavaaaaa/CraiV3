"""Avaliacao do Modulo 3: LSTM individual + prior sazonal Prophet.

Duas perguntas:
1. Qualidade diaria: o modelo acerta SE o cliente tera saldo em cada um dos
   14 dias seguintes? (ROC-AUC sobre todos os dias do horizonte)
2. Janela otima de retry: o primeiro dia previsto com liquidez esta perto do
   primeiro dia REAL com liquidez? (MAE em dias + hit-rate com tolerancia)

Compara 4 estrategias no MESMO conjunto de clientes de teste (nunca vistos
no treino):
- heuristica  : dias fixos por perfil (baseline atual do crai/)
- prophet     : so o prior sazonal do perfil
- lstm        : so o padrao individual
- ensemble    : 0.6*lstm + 0.4*prophet (modelo final do modulo)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import roc_auc_score

from src.features import janelas_do_cliente
from src.model import HORIZON, LiquidityLSTM
from src.prophet_model import SeasonalPrior

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data" / "liquidity_series.csv"
MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"

PESO_LSTM = 0.6
LIMIAR = 0.5

# Baseline: heuristica de dias fixos usada hoje em crai/ml/payday_inference.py
PAYDAY_HEURISTICS = {
    "CLT": [5, 6, 7, 20, 21],
    "freelancer": [10, 15, 20, 25],
    "PJ": [5, 10, 15, 20, 25],
}


def _primeiro_dia(probs: np.ndarray, limiar: float = LIMIAR) -> int:
    """Indice (0-13) do primeiro dia previsto com liquidez; argmax se nenhum."""
    acima = np.where(probs >= limiar)[0]
    return int(acima[0]) if len(acima) else int(np.argmax(probs))


def _primeiro_dia_heuristica(dias_do_mes: np.ndarray, profile: str) -> int:
    alvo = set(PAYDAY_HEURISTICS[profile])
    for i, dia in enumerate(dias_do_mes):
        if dia in alvo:
            return i
    return 0


def avaliar() -> dict:
    with open(MODELS_DIR / "meta.json", encoding="utf-8") as f:
        meta = json.load(f)

    df = pd.read_csv(DATA_PATH, parse_dates=["date"])
    df_teste = df[df["customer_id"].isin(set(meta["clientes_teste"]))]

    model = LiquidityLSTM(hidden_size=meta["hidden_size"])
    model.load_state_dict(torch.load(MODELS_DIR / "lstm.pt"))
    model.eval()
    prior_sazonal = SeasonalPrior()

    y_true, p_lstm, p_prophet, p_ens = [], [], [], []
    linhas = []

    for cid, grupo in df_teste.groupby("customer_id", sort=True):
        grupo = grupo.sort_values("date").reset_index(drop=True)
        profile = grupo["profile"].iloc[0]
        X, y, idx = janelas_do_cliente(grupo, passo=14)
        if len(X) == 0:
            continue

        probs_lstm = model.predict_proba(torch.from_numpy(X)).numpy()

        for j in range(len(X)):
            datas_horizonte = pd.DatetimeIndex(grupo["date"].iloc[idx[j] : idx[j] + HORIZON])
            dias_do_mes = grupo["day_of_month"].iloc[idx[j] : idx[j] + HORIZON].to_numpy()
            prior = prior_sazonal.prior(profile, datas_horizonte)
            ens = PESO_LSTM * probs_lstm[j] + (1 - PESO_LSTM) * prior

            y_true.append(y[j])
            p_lstm.append(probs_lstm[j])
            p_prophet.append(prior)
            p_ens.append(ens)

            # Tarefa "janela otima": so ha resposta se existe dia com liquidez
            com_liquidez = np.where(y[j] == 1)[0]
            if len(com_liquidez):
                real = int(com_liquidez[0])
                linhas.append({
                    "customer_id": cid,
                    "profile": profile,
                    "real": real,
                    "heuristica": _primeiro_dia_heuristica(dias_do_mes, profile),
                    "prophet": _primeiro_dia(prior),
                    "lstm": _primeiro_dia(probs_lstm[j]),
                    "ensemble": _primeiro_dia(ens),
                })

    y_flat = np.concatenate(y_true)
    aucs = {
        "lstm": round(float(roc_auc_score(y_flat, np.concatenate(p_lstm))), 4),
        "prophet": round(float(roc_auc_score(y_flat, np.concatenate(p_prophet))), 4),
        "ensemble": round(float(roc_auc_score(y_flat, np.concatenate(p_ens))), 4),
    }

    tarefas = pd.DataFrame(linhas)
    janela = {}
    for estrategia in ["heuristica", "prophet", "lstm", "ensemble"]:
        erro = (tarefas[estrategia] - tarefas["real"]).abs()
        janela[estrategia] = {
            "mae_dias": round(float(erro.mean()), 3),
            "hit_exato": round(float((erro == 0).mean()), 4),
            "hit_1d": round(float((erro <= 1).mean()), 4),
            "hit_2d": round(float((erro <= 2).mean()), 4),
        }

    por_perfil = {}
    for profile, sub in tarefas.groupby("profile"):
        erro_h = (sub["heuristica"] - sub["real"]).abs()
        erro_e = (sub["ensemble"] - sub["real"]).abs()
        por_perfil[profile] = {
            "n_janelas": int(len(sub)),
            "mae_heuristica": round(float(erro_h.mean()), 3),
            "mae_ensemble": round(float(erro_e.mean()), 3),
            "hit_1d_ensemble": round(float((erro_e <= 1).mean()), 4),
        }

    resultado = {
        "n_clientes_teste": int(df_teste["customer_id"].nunique()),
        "n_janelas": int(len(y_true)),
        "n_janelas_com_liquidez": int(len(tarefas)),
        "peso_lstm": PESO_LSTM,
        "limiar": LIMIAR,
        "roc_auc_diario": aucs,
        "janela_otima": janela,
        "por_perfil": por_perfil,
    }

    REPORTS_DIR.mkdir(exist_ok=True)
    with open(REPORTS_DIR / "metricas.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)
    tarefas.to_csv(REPORTS_DIR / "scores.csv", index=False)

    print("=" * 60)
    print("AVALIACAO - Modulo 3 (LSTM + Prophet)")
    print("=" * 60)
    print(f"clientes de teste: {resultado['n_clientes_teste']} | janelas: {resultado['n_janelas']}")
    print("\nROC-AUC diario (14 dias a frente):")
    for k, v in aucs.items():
        print(f"  {k:10s}: {v:.4f}")
    print("\nJanela otima de retry (dias ate a primeira liquidez):")
    print(f"  {'estrategia':12s} {'MAE':>6s} {'hit':>7s} {'hit±1':>7s} {'hit±2':>7s}")
    for k, v in janela.items():
        print(f"  {k:12s} {v['mae_dias']:6.2f} {v['hit_exato']:7.1%} {v['hit_1d']:7.1%} {v['hit_2d']:7.1%}")
    print("\nPor perfil (MAE heuristica -> ensemble):")
    for p, v in por_perfil.items():
        print(f"  {p:11s}: {v['mae_heuristica']:.2f} -> {v['mae_ensemble']:.2f} dias ({v['n_janelas']} janelas)")
    print(f"\n[ok] metricas salvas em {REPORTS_DIR / 'metricas.json'}")
    return resultado


if __name__ == "__main__":
    avaliar()

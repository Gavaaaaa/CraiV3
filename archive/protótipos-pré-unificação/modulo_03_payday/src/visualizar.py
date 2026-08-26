"""Figuras do Modulo 3 para a banca (reports/figures/).

1. serie_exemplo.png   - saldo de um cliente de teste + janela prevista
2. comparacao_mae.png  - MAE por estrategia (heuristica -> ensemble)
3. prior_sazonal.png   - sazonalidade de liquidez por perfil (Prophet)
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.payday_inference import PaydayInference
from src.prophet_model import SeasonalPrior

ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "reports" / "figures"

# Paleta categorica validada (CVD-safe): azul, aqua, amarelo; cinza p/ baseline
COR = {"CLT": "#2a78d6", "PJ": "#1baf7a", "freelancer": "#eda100",
       "baseline": "#8a8988", "destaque": "#2a78d6", "liquidez": "#1baf7a"}
GRID = dict(color="#d9d8d3", linewidth=0.6)


def _eixos_limpos(ax):
    for lado in ["top", "right"]:
        ax.spines[lado].set_visible(False)
    ax.grid(axis="y", **GRID)
    ax.set_axisbelow(True)


def fig_serie_exemplo(df: pd.DataFrame, meta: dict) -> None:
    cid = meta["clientes_teste"][0]
    serie = df[df.customer_id == cid].sort_values("date").tail(60)
    inferencia = PaydayInference()
    r = inferencia.predict_next_window(serie)

    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(serie["date"], serie["balance_norm"], color=COR["destaque"], linewidth=2,
            label="Saldo (x mensalidade)")
    ax.axhline(1.0, color=COR["baseline"], linewidth=1.2, linestyle="--")
    ax.text(serie["date"].iloc[2], 1.05, "mensalidade", fontsize=8, color="#5f5e5a")

    com_liq = serie[serie.has_liquidity == 1]
    ax.scatter(com_liq["date"], com_liq["balance_norm"], s=14, color=COR["liquidez"],
               zorder=3, label="Dias com liquidez")
    ax.axvline(r["timestamp"], color=COR["liquidez"], linewidth=2)
    ax.text(r["timestamp"], ax.get_ylim()[1] * 0.92,
            f"  retry previsto\n  ({r['confidence']:.0%} confianca)", fontsize=8,
            color="#1c7a55", va="top")

    _eixos_limpos(ax)
    ax.set_title(f"Cliente de teste {cid} ({serie['profile'].iloc[0]}) — saldo e janela prevista",
                 fontsize=11, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "serie_exemplo.png", dpi=150)
    plt.close(fig)


def fig_comparacao_mae(metricas: dict) -> None:
    janela = metricas["janela_otima"]
    estrategias = ["heuristica", "prophet", "lstm", "ensemble"]
    rotulos = ["Heurística\n(dias fixos)", "Prophet\n(sazonal)", "LSTM\n(individual)", "Ensemble\n(LSTM+Prophet)"]
    maes = [janela[e]["mae_dias"] for e in estrategias]
    cores = [COR["baseline"]] * 3 + [COR["destaque"]]

    fig, ax = plt.subplots(figsize=(7, 4))
    barras = ax.bar(rotulos, maes, color=cores, width=0.55)
    for barra, mae in zip(barras, maes):
        ax.text(barra.get_x() + barra.get_width() / 2, mae + 0.05, f"{mae:.2f}",
                ha="center", fontsize=9, color="#33322e")
    _eixos_limpos(ax)
    ax.set_ylabel("MAE (dias até a 1ª liquidez)", fontsize=9)
    ax.set_title("Erro na previsão da janela de retry — menor é melhor", fontsize=11, loc="left")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "comparacao_mae.png", dpi=150)
    plt.close(fig)


def fig_prior_sazonal() -> None:
    prior = SeasonalPrior()
    datas = pd.date_range("2026-08-01", "2026-08-31")

    fig, ax = plt.subplots(figsize=(9, 4))
    for perfil in ["CLT", "PJ", "freelancer"]:
        y = prior.prior(perfil, datas)
        ax.plot(datas.day, y, color=COR[perfil], linewidth=2, label=perfil)
        ax.text(datas.day[-1] + 0.3, y[-1], perfil, fontsize=8, color=COR[perfil], va="center")

    _eixos_limpos(ax)
    ax.set_xlabel("Dia do mês", fontsize=9)
    ax.set_ylabel("P(liquidez) sazonal", fontsize=9)
    ax.set_xlim(1, 34)
    ax.set_title("Prior sazonal Prophet por perfil — sazonalidade de payday brasileira",
                 fontsize=11, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "prior_sazonal.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(ROOT / "data" / "liquidity_series.csv", parse_dates=["date"])
    with open(ROOT / "models" / "meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    with open(ROOT / "reports" / "metricas.json", encoding="utf-8") as f:
        metricas = json.load(f)

    fig_serie_exemplo(df, meta)
    fig_comparacao_mae(metricas)
    fig_prior_sazonal()
    print(f"[ok] figuras salvas em {FIG_DIR}/")


if __name__ == "__main__":
    main()

"""Figuras do Modulo 4 para a banca (reports/figures/).

1. regret_acumulado.png  - regret esperado acumulado por estrategia
2. convergencia.png      - % de escolha do braco otimo (janela movel)
3. aceite_vs_eprofit.png - o caso CLT: converter mais != lucrar mais
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.environment import MRR_PARAMS, OFFERS, TRUE_ACCEPT, eprofit

ROOT = Path(__file__).resolve().parents[1]
FIG_DIR = ROOT / "reports" / "figures"

# Paleta categorica validada (CVD-safe); baseline em cinza, modelo final em azul
COR_ESTRATEGIA = {
    "random": "#8a8988",
    "epsilon_greedy": "#eda100",
    "thompson_aceite": "#1baf7a",
    "thompson_eprofit": "#2a78d6",
}
ROTULO = {
    "random": "Aleatória",
    "epsilon_greedy": "Epsilon-Greedy (baseline)",
    "thompson_aceite": "Thompson (aceite)",
    "thompson_eprofit": "Thompson (e-Profit)",
}
GRID = dict(color="#d9d8d3", linewidth=0.6)


def _eixos_limpos(ax):
    for lado in ["top", "right"]:
        ax.spines[lado].set_visible(False)
    ax.grid(axis="y", **GRID)
    ax.set_axisbelow(True)


def fig_regret(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for nome in ["random", "epsilon_greedy", "thompson_aceite", "thompson_eprofit"]:
        sub = df[df.estrategia == nome].sort_values("round")
        acum = sub["regret_esperado"].cumsum() / 1000
        ax.plot(sub["round"], acum, color=COR_ESTRATEGIA[nome], linewidth=2,
                label=ROTULO[nome])
        rotulo_curto = {"random": "Aleatória", "epsilon_greedy": "Epsilon-Greedy",
                        "thompson_aceite": "Thompson aceite",
                        "thompson_eprofit": "Thompson e-Profit"}[nome]
        ax.text(sub["round"].iloc[-1] + 60, acum.iloc[-1], rotulo_curto,
                fontsize=8, color=COR_ESTRATEGIA[nome], va="center")
    _eixos_limpos(ax)
    ax.set_xlabel("Rodada (cliente em risco atendido)", fontsize=9)
    ax.set_ylabel("Regret acumulado (R$ mil)", fontsize=9)
    ax.set_xlim(0, df["round"].max() * 1.22)
    ax.set_title("Regret esperado acumulado — menor é melhor", fontsize=11, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "regret_acumulado.png", dpi=150)
    plt.close(fig)


def fig_convergencia(df: pd.DataFrame, janela: int = 500) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    for nome in ["epsilon_greedy", "thompson_aceite", "thompson_eprofit"]:
        sub = df[df.estrategia == nome].sort_values("round")
        movel = sub["escolheu_otima"].rolling(janela, min_periods=100).mean() * 100
        ax.plot(sub["round"], movel, color=COR_ESTRATEGIA[nome], linewidth=2,
                label=ROTULO[nome])
    _eixos_limpos(ax)
    ax.set_xlabel("Rodada", fontsize=9)
    ax.set_ylabel(f"% escolhas do braço ótimo (janela de {janela})", fontsize=9)
    ax.set_ylim(0, 100)
    ax.set_title("Convergência para a oferta ótima em e-Profit", fontsize=11, loc="left")
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(FIG_DIR / "convergencia.png", dpi=150)
    plt.close(fig)


def fig_aceite_vs_eprofit() -> None:
    """CLT no MRR mediano: consulta_cs converte mais, desconto_10 lucra mais."""
    mrr = float(np.exp(MRR_PARAMS["CLT"][0]))
    aceites = [TRUE_ACCEPT["CLT"][o] * 100 for o in OFFERS]
    eprofits = [eprofit(TRUE_ACCEPT["CLT"][o], o, mrr) for o in OFFERS]
    nomes = [o.replace("_", "\n") for o in OFFERS]
    x = np.arange(len(OFFERS))

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
    melhor_aceite = int(np.argmax(aceites))
    melhor_ep = int(np.argmax(eprofits))

    cores1 = ["#8a8988"] * len(OFFERS)
    cores1[melhor_aceite] = "#1baf7a"
    ax1.bar(x, aceites, color=cores1, width=0.6)
    for i, v in enumerate(aceites):
        ax1.text(i, v + 1, f"{v:.0f}%", ha="center", fontsize=8, color="#33322e")
    ax1.set_xticks(x, nomes, fontsize=7)
    ax1.set_ylabel("Taxa de aceite real (%)", fontsize=9)
    ax1.set_title("Quem converte mais", fontsize=10, loc="left")
    _eixos_limpos(ax1)

    cores2 = ["#8a8988"] * len(OFFERS)
    cores2[melhor_ep] = "#2a78d6"
    ax2.bar(x, eprofits, color=cores2, width=0.6)
    for i, v in enumerate(eprofits):
        ax2.text(i, v + 12, f"R${v:.0f}", ha="center", fontsize=8, color="#33322e")
    ax2.set_xticks(x, nomes, fontsize=7)
    ax2.set_ylabel("e-Profit esperado (R$)", fontsize=9)
    ax2.set_title("Quem dá mais lucro", fontsize=10, loc="left")
    _eixos_limpos(ax2)

    fig.suptitle(f"Perfil CLT (MRR mediano R$ {mrr:.0f}) — converter mais ≠ lucrar mais",
                 fontsize=11, x=0.02, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG_DIR / "aceite_vs_eprofit.png", dpi=150)
    plt.close(fig)


def main() -> None:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(ROOT / "reports" / "simulacao.csv")
    fig_regret(df)
    fig_convergencia(df)
    fig_aceite_vs_eprofit()
    print(f"[ok] figuras salvas em {FIG_DIR}/")


if __name__ == "__main__":
    main()

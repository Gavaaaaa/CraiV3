"""Avaliacao do Modulo 4 a partir da trajetoria simulada (reports/simulacao.csv).

Metricas por estrategia:
- e-Profit total realizado (R$) e % capturado vs oraculo
- Regret esperado acumulado
- % de escolhas do braco otimo (global e nas ultimas 1000 rodadas)
- Convergencia por perfil: braco mais escolhido no ultimo quartil vs otimo
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from src.environment import OFFERS, PROFILES, TRUE_ACCEPT, eprofit

ROOT = Path(__file__).resolve().parents[1]
REPORTS_DIR = ROOT / "reports"


def _braco_otimo_tipico(profile: str) -> str:
    """Braco otimo em e-Profit no MRR mediano do perfil (referencia p/ relatorio)."""
    from src.environment import MRR_PARAMS
    mrr_mediano = float(np.exp(MRR_PARAMS[profile][0]))
    return max(OFFERS, key=lambda o: eprofit(TRUE_ACCEPT[profile][o], o, mrr_mediano))


def avaliar() -> dict:
    df = pd.read_csv(REPORTS_DIR / "simulacao.csv")
    n_rounds = df["round"].max() + 1

    por_estrategia = {}
    for nome, sub in df.groupby("estrategia"):
        ultimas = sub[sub["round"] >= n_rounds - 1000]
        por_estrategia[nome] = {
            "eprofit_total": round(float(sub["eprofit_realizado"].sum()), 2),
            "regret_acumulado": round(float(sub["regret_esperado"].sum()), 2),
            "pct_otima_global": round(float(sub["escolheu_otima"].mean()), 4),
            "pct_otima_ultimas_1000": round(float(ultimas["escolheu_otima"].mean()), 4),
            "taxa_aceite": round(float(sub["accepted"].mean()), 4),
        }

    melhor = max(por_estrategia, key=lambda k: por_estrategia[k]["eprofit_total"])
    baseline = por_estrategia["epsilon_greedy"]["eprofit_total"]
    ganho_vs_baseline = por_estrategia["thompson_eprofit"]["eprofit_total"] - baseline

    convergencia = {}
    final = df[(df["estrategia"] == "thompson_eprofit") & (df["round"] >= n_rounds * 0.75)]
    for profile in PROFILES:
        sub = final[final["profile"] == profile]
        mais_escolhida = sub["offer"].mode().iloc[0] if len(sub) else None
        convergencia[profile] = {
            "braco_mais_escolhido": mais_escolhida,
            "braco_otimo_tipico": _braco_otimo_tipico(profile),
            "convergiu": bool(mais_escolhida == _braco_otimo_tipico(profile)),
        }

    resultado = {
        "n_rounds": int(n_rounds),
        "por_estrategia": por_estrategia,
        "melhor_estrategia": melhor,
        "ganho_thompson_vs_epsilon_greedy_reais": round(float(ganho_vs_baseline), 2),
        "convergencia_thompson_eprofit": convergencia,
    }

    with open(REPORTS_DIR / "metricas.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, indent=2, ensure_ascii=False)

    print("=" * 64)
    print("AVALIACAO - Modulo 4 (Thompson Sampling)")
    print("=" * 64)
    print(f"{'estrategia':18s} {'e-Profit R$':>14s} {'regret R$':>12s} {'otima':>7s} {'otima@fim':>10s}")
    for nome, m in por_estrategia.items():
        print(f"{nome:18s} {m['eprofit_total']:14,.0f} {m['regret_acumulado']:12,.0f} "
              f"{m['pct_otima_global']:7.1%} {m['pct_otima_ultimas_1000']:10.1%}")
    print(f"\nGanho do thompson_eprofit vs epsilon_greedy: R$ {ganho_vs_baseline:,.2f}")
    print("\nConvergencia por perfil (ultimo quartil):")
    for p, c in convergencia.items():
        icone = "OK " if c["convergiu"] else "!= "
        print(f"  [{icone}] {p:11s}: escolhe {c['braco_mais_escolhido']} "
              f"(otimo tipico: {c['braco_otimo_tipico']})")
    print(f"\n[ok] metricas salvas em {REPORTS_DIR / 'metricas.json'}")
    return resultado


if __name__ == "__main__":
    avaliar()

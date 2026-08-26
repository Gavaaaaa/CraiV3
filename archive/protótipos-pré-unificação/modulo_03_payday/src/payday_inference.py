"""Interface publica do Modulo 3 para os demais modulos do CRAI.

O agente LangGraph (Modulo 5) consome esta classe sem conhecer detalhes de
PyTorch/Prophet. Recebe a serie recente do cliente, devolve a proxima janela
de liquidez: data prevista, confianca e perfil.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from src.features import featurizar_serie
from src.model import HORIZON, LiquidityLSTM, WINDOW
from src.prophet_model import SeasonalPrior

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"

PESO_LSTM = 0.6
LIMIAR = 0.5


class PaydayInference:
    def __init__(self, models_dir: Path = MODELS_DIR) -> None:
        with open(models_dir / "meta.json", encoding="utf-8") as f:
            self._meta = json.load(f)
        self.model = LiquidityLSTM(hidden_size=self._meta["hidden_size"])
        self.model.load_state_dict(torch.load(models_dir / "lstm.pt"))
        self.model.eval()
        self.prior = SeasonalPrior(models_dir)

    # Ancoras de dia do mes por perfil (sazonalidade de pagamento BR)
    _ANCORAS_CLT = set(range(4, 10)) | set(range(18, 23))       # 5o dia util + adiantamento
    _ANCORAS_PJ = set(range(10, 18)) | {28, 29, 30, 31, 1, 2, 3}  # notas 10/15/30

    @classmethod
    def classificar_perfil(cls, serie: pd.DataFrame) -> str:
        """Perfil pelo padrao das ENTRADAS (dias em que o saldo sobe).

        Vota nas ancoras de pagamento brasileiras: entradas concentradas no
        5o dia util / dia 20 indicam CLT; nos dias 10/15/30, PJ; entradas
        espalhadas sem ancora, freelancer.
        """
        serie = serie.sort_values("date")
        saldo = serie["balance_norm"].to_numpy()
        entradas = np.where(np.diff(saldo) > 0.5)[0]
        if len(entradas) < 2:
            return "CLT"
        dias = serie["day_of_month"].to_numpy()[entradas + 1]

        frac_clt = np.isin(dias, list(cls._ANCORAS_CLT)).mean()
        frac_pj = np.isin(dias, list(cls._ANCORAS_PJ)).mean()
        if max(frac_clt, frac_pj) < 0.55:
            return "freelancer"
        return "CLT" if frac_clt >= frac_pj else "PJ"

    def predict_next_window(self, serie: pd.DataFrame, profile: str | None = None) -> dict:
        """serie: DataFrame com >= WINDOW dias (date, day_of_month, weekday,
        balance_norm, has_liquidity). Devolve a proxima janela de liquidez."""
        serie = serie.sort_values("date")
        if profile is None:
            profile = self.classificar_perfil(serie)  # usa a serie completa
        serie = serie.tail(WINDOW)
        if len(serie) < WINDOW:
            raise ValueError(f"serie precisa de {WINDOW} dias, recebeu {len(serie)}")

        X = torch.from_numpy(featurizar_serie(serie)[None, ...])
        probs_lstm = self.model.predict_proba(X).numpy()[0]

        ultima_data = pd.Timestamp(serie["date"].iloc[-1])
        datas_futuras = pd.date_range(ultima_data + pd.Timedelta(days=1), periods=HORIZON)
        prior = self.prior.prior(profile, datas_futuras)

        probs = PESO_LSTM * probs_lstm + (1 - PESO_LSTM) * prior

        acima = np.where(probs >= LIMIAR)[0]
        idx = int(acima[0]) if len(acima) else int(np.argmax(probs))

        return {
            "timestamp": (datas_futuras[idx] + pd.Timedelta(hours=10)).to_pydatetime(),
            "confidence": round(float(probs[idx]), 4),
            "profile": profile,
            "method": "lstm_prophet",
            "probs_14d": [round(float(p), 4) for p in probs],
        }


def main() -> None:
    from data.generate_synthetic import gerar_dataset

    inferencia = PaydayInference()
    df = gerar_dataset(n_clientes=30, n_dias=90, seed=123)

    print("=" * 64)
    print("DEMO - PaydayInference (LSTM + Prophet)")
    print("=" * 64)
    for cid in ["C00000", "C00003", "C00007"]:
        serie = df[df.customer_id == cid]
        perfil_real = serie["profile"].iloc[0]
        r = inferencia.predict_next_window(serie)
        print(f"\nCliente {cid} (perfil real: {perfil_real}):")
        print(f"  proxima liquidez : {r['timestamp']:%d/%m %H:%M}")
        print(f"  confianca        : {r['confidence']:.2%}")
        print(f"  perfil inferido  : {r['profile']}")


if __name__ == "__main__":
    main()

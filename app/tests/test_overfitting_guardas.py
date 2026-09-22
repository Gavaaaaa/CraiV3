"""tests/test_overfitting_guardas.py — `risk_regra` não é feature do voluntário.

A coluna `risk_regra` está DENTRO do dataset do Módulo 4
(`data/v2/voluntario.parquet`) e é a regra que gerou o rótulo `churn`, com
ruído (correlação de Pearson 0,59 com o rótulo na base v2 — era 0,45 na v1,
auditoria de 15/09; a v2 é mais circular — e AUC 0,83 sozinha).
Se ela entrasse como feature, o modelo "aprenderia" a própria resposta e a
AUC declarada seria vazamento, não sinal.

Ler a lista `FEATURES_DE_RISCO` não basta — a lista pode ser lida certa e o
treino usar outra coisa. Estes testes provam por execução:

  1. a lista de features do scorer, a do gerador e a do artefato promovido
     não contêm `risk_regra`;
  2. um dataset em que `risk_regra` é uma CÓPIA PERFEITA do rótulo e as
     features são ruído puro treina para AUC ≈ 0,50 — se `risk_regra`
     vazasse por qualquer caminho, a AUC seria ≈ 1,00.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crai.churn_voluntary.risk_scorer import FEATURES_DE_RISCO
from crai.ml import voluntary_risk as vr
from crai.ml.synthetic_data import VOLUNTARY_FEATURES
from crai.ml.voluntary_risk import NOME_CANDIDATO, VoluntaryRiskModel

META = vr.MODELS_DIR / f"{NOME_CANDIDATO}_meta.json"


class TestRiskRegraNaoEFeature:

    def test_as_tres_listas_nao_contem_risk_regra(self):
        assert "risk_regra" not in FEATURES_DE_RISCO
        assert "risk_regra" not in VOLUNTARY_FEATURES
        assert "churn" not in FEATURES_DE_RISCO
        assert list(VOLUNTARY_FEATURES) == list(FEATURES_DE_RISCO)

    def test_o_artefato_promovido_nao_declara_risk_regra(self):
        if not META.exists():
            pytest.skip(f"{META} ausente — nenhum candidato treinado nesta máquina")
        meta = json.loads(META.read_text(encoding="utf-8"))
        assert "risk_regra" not in meta["features"]
        assert meta["features"] == list(FEATURES_DE_RISCO)

    def test_risk_regra_igual_ao_rotulo_nao_vaza_para_o_treino(self, tmp_path, monkeypatch):
        """Prova por execução, não por leitura da lista."""
        monkeypatch.setattr(vr, "MODELS_DIR", tmp_path)
        rng = np.random.default_rng(7)
        n = 3000
        churn = rng.integers(0, 2, size=n)
        df = pd.DataFrame({
            "customer_id": [f"c{i}" for i in range(n)],
            "days_since_last": rng.normal(size=n), "features_used_30d": rng.normal(size=n),
            "mrr": rng.normal(size=n), "evento_cancelamento": rng.normal(size=n),
            "evento_downgrade": rng.normal(size=n), "evento_sessao": rng.normal(size=n),
            "event": "x",
            "risk_regra": churn.astype(float),          # cópia PERFEITA do rótulo
            "churn": churn,
        })
        m = VoluntaryRiskModel().train(dados=df, groups=df["customer_id"], fonte="sintetico")
        assert m["auc_regra_vs_rotulo"] == 1.0, "a regra copiada do rótulo tem AUC 1 por construção"
        assert abs(m["auc_vs_rotulo"] - 0.5) < 0.06, (
            f"AUC {m['auc_vs_rotulo']} com features de ruído puro: `risk_regra` está "
            "entrando no treino por algum caminho")

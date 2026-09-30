"""tests/test_treino_voluntario_v3.py — o treino v3 do risco voluntário.

Treino v3, Bloco 2 (29/09/2026). O treino de verdade (`models/v3/`) é rodado
pelo operador no terminal. Aqui a base é gerada em `tmp_path` a partir da v2
(só lida) e o treino roda numa subamostra de clientes com poucas iterações:
o que se testa é o encanamento e as travas, não o número.
"""

import json
import subprocess
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest

from crai.churn_voluntary import risk_scorer as rs
from crai.ml import voluntario_v3 as V3
from crai.ml.split import split_por_cliente
from crai.scripts import gerar_base_v3_voluntario as G
from crai.scripts import treinar_voluntario_v3 as T

APP = Path(__file__).resolve().parents[1]
PASTA_V2 = APP / "data" / "v2"
HIPER_RAPIDO = {**T.HIPERPARAMETROS, "max_iter": 20}
AMOSTRA = 3000

pytestmark = pytest.mark.skipif(not (PASTA_V2 / "MANIFESTO.json").exists(),
                                reason="base v2 ausente")


@pytest.fixture(scope="module")
def base_tmp(tmp_path_factory):
    destino = tmp_path_factory.mktemp("base_v3")
    assert G.main(["--seed", "42", "--out", str(destino), "--v2", str(PASTA_V2)]) == 0
    return destino


@pytest.fixture(scope="module")
def treino(base_tmp, tmp_path_factory):
    out = tmp_path_factory.mktemp("models_v3")
    metricas = T.treinar(base_tmp, out, amostra_clientes=AMOSTRA, hiper=HIPER_RAPIDO,
                         com_ablacao=False, com_shap=False)
    return out, metricas


class TestContratoDeProducao:
    def test_artefato_v3_e_recusado_por_carregar_modelo(self, treino, monkeypatch):
        out, _ = treino
        # Os globais do cache voltam ao estado anterior no teardown.
        monkeypatch.setattr(rs, "_modelo", rs._modelo)
        monkeypatch.setattr(rs, "_modelo_consultado", rs._modelo_consultado)
        monkeypatch.setattr(rs, "MODELO_PATH", out / f"{T.NOME}.joblib")
        monkeypatch.setattr(rs, "MODELO_META_PATH", out / f"{T.NOME}_meta.json")
        assert rs.carregar_modelo(forcar=True) is False
        assert rs._modelo is None

    def test_meta_declara_features_v3_e_nao_as_de_producao(self, treino):
        out, _ = treino
        meta = json.loads((out / f"{T.NOME}_meta.json").read_text(encoding="utf-8"))
        assert meta["features"] == V3.FEATURES_DE_RISCO_V3
        assert meta["features"] != rs.FEATURES_DE_RISCO
        assert meta["contrato_de_producao"] is False

    def test_out_em_models_de_producao_e_recusado(self, base_tmp):
        with pytest.raises(SystemExit, match="producao"):
            T.main(["--base", str(base_tmp), "--out", str(rs.MODELS_DIR)])

    def test_script_nao_importa_train_all_nem_voluntary_risk(self):
        codigo = ("import sys, crai.scripts.treinar_voluntario_v3; "
                  "print('crai.scripts.train_all' in sys.modules, "
                  "'crai.ml.voluntary_risk' in sys.modules)")
        r = subprocess.run([sys.executable, "-c", codigo], cwd=APP, capture_output=True,
                           text=True, check=True)
        assert r.stdout.split() == ["False", "False"]


class TestOcultoAborta:
    def test_treino_com_coluna_oculto_nas_features_aborta(self, base_tmp, tmp_path):
        with pytest.raises(ValueError, match="proibidas"):
            T.treinar(base_tmp, tmp_path, features=[*V3.FEATURES_DE_RISCO_V3, "oculto_p_churn"],
                      amostra_clientes=500, hiper=HIPER_RAPIDO, com_ablacao=False,
                      com_shap=False)
        assert not (tmp_path / f"{T.NOME}.joblib").exists()

    def test_ajustar_recusa_matriz_com_oculto(self):
        X = pd.DataFrame({"mrr": [1.0, 2.0], "oculto_p_churn": [0.1, 0.9]})
        with pytest.raises(ValueError, match="proibidas"):
            T.ajustar(X, np.array([0, 1]), np.array(["a", "b"]), HIPER_RAPIDO)


class TestAumentoSoNoTreino:
    def test_tres_copias_com_peso_um_terco_e_mascaras(self):
        X = pd.DataFrame({f: [1.0, 2.0] for f in V3.FEATURES_DE_RISCO_V3})
        Xa, ya, ga, peso, cen = T.aumentar(X, np.array([0, 1]), np.array(["c1", "c2"]))
        assert len(Xa) == 6 and np.allclose(peso, 1 / 3)
        assert list(pd.Series(ga).value_counts()) == [3, 3]
        b = Xa[cen == "b"]
        assert b[T.CENARIOS["b"]].notna().all().all()
        assert b.drop(columns=T.CENARIOS["b"]).isna().all().all()

    def test_metricas_declaram_holdout_nao_aumentado_e_copias_na_mesma_dobra(self, treino):
        _, m = treino
        a = m["aumento_por_mascara"]
        assert a["so_no_treino"] is True and a["holdout_aumentado"] is False
        assert a["copias_do_cliente_na_mesma_dobra"] is True
        assert m["split"]["eventos_holdout"] == m["cenarios"]["a"]["linhas_holdout"]


class TestReguaComoAProducao:
    def test_cenario_c_nao_pontua_nenhuma_linha(self, treino):
        _, m = treino
        c = m["cenarios"]["c"]
        assert c["auc_regua_fixa"] is None and c["auc_regua_percentil"] is None
        assert c["linhas_regua_nao_pontuadas_dado_insuficiente"] == c["linhas_holdout"]

    def test_cenario_b_percentil_cai_na_global(self, treino):
        _, m = treino
        b = m["cenarios"]["b"]
        assert b["origem_regua_percentil"].startswith("global")
        assert b["auc_regua_percentil"] == b["auc_regua_fixa"]
        assert b["linhas_regua_nao_pontuadas_dado_insuficiente"] == 0


class TestMetricasBatemComORecomputado:
    def test_auc_por_cenario_recomputada_do_artefato(self, base_tmp, treino):
        out, m = treino
        df = T.amostrar_clientes(pd.read_parquet(base_tmp / "voluntario_v3.parquet"), AMOSTRA)
        _, idx_te = split_por_cliente(df["customer_id"].to_numpy(), test_size=T.TEST_SIZE,
                                      seed=T.SEED)
        modelo = joblib.load(out / f"{T.NOME}.joblib")
        X_te = df[V3.FEATURES_DE_RISCO_V3].iloc[idx_te]
        y_te = df["churn"].to_numpy()[idx_te]
        from sklearn.metrics import roc_auc_score
        for c in T.CENARIOS:
            s = T.prever(modelo, T.mascarar(X_te, c))
            assert round(float(roc_auc_score(y_te, s)), 4) == m["cenarios"][c]["auc_candidato"]
        teto = round(float(roc_auc_score(y_te, df["oculto_p_churn"].to_numpy()[idx_te])), 4)
        assert teto == m["teto_bayes_holdout"]

    def test_arquivo_de_metricas_e_o_devolvido(self, treino):
        out, m = treino
        gravado = json.loads((out / "train_metrics_v3.json").read_text(encoding="utf-8"))
        assert gravado["cenarios"] == json.loads(json.dumps(m["cenarios"], default=str))
        assert gravado["criterio_promocao"]["promovido"] is False

    def test_permutacao_tem_tres_sementes(self, treino):
        _, m = treino
        assert [p["semente"] for p in m["permutacao_rotulo"]] == list(T.SEMENTES_PERMUTACAO)

    def test_criterio_declarado_e_o_da_constante(self, treino):
        _, m = treino
        assert m["criterio_promocao"]["declarado"] == T.CRITERIO_PROMOCAO
        assert m["groupkfold"]["n_splits"] == 5


class TestAblacaoEImportancia:
    def test_ablacao_cobre_as_15_e_importancia_e_treeshap(self, base_tmp, tmp_path):
        m = T.treinar(base_tmp, tmp_path, amostra_clientes=800,
                      hiper={**T.HIPERPARAMETROS, "max_iter": 10})
        assert set(m["ablacao"]) == set(V3.FEATURES_DE_RISCO_V3)
        assert m["importancia_global"]["metodo"].startswith("TreeSHAP")
        assert set(m["importancia_global"]["valores"]) == set(V3.FEATURES_DE_RISCO_V3)
        assert m["dispositivo"].startswith("CPU") and m["duracao_total_s"] > 0

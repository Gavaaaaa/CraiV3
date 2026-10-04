"""tests/test_promocao_v3_bloco3.py — promoção, reversão e trava.

Promoção do voluntário v3, Bloco 3 (30/09/2026). Tudo em `tmp_path`: nenhum
teste escreve em `models/` (o `--promover` real é do operador).

  - `promover_voluntario_v3 --promover`: copia o v3, grava o meta de produção
    com contrato v3 e `referencia_score_quantis`, e o `risk_scorer` aceita;
    recusa se já houver modelo de produção, se o meta de origem não for v3, se
    a base não for a do treino;
  - `--reverter`: move para `historico/` e a régua volta a decidir;
  - D8: `VoluntaryRiskModel.ativar()` recusa com o v3 em produção, e volta a
    funcionar depois do `--reverter`;
  - as 9 colunas comportamentais chegam ao risco pelo lote, quando existem.
"""

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from crai.churn_voluntary import batch_scoring as bs
from crai.churn_voluntary import disparo_lote as dl
from crai.churn_voluntary import risk_scorer as rs
from crai.ml import voluntary_risk as vr
from crai.scripts import promover_voluntario_v3 as P

APP = Path(__file__).resolve().parents[1]
V3 = APP / "models" / "v3"
BASE_V3 = APP / "data" / "v3"

pytestmark = pytest.mark.skipif(
    not (V3 / "voluntary_risk_v3.joblib").exists()
    or not (BASE_V3 / "voluntario_v3.parquet").exists(),
    reason="artefato ou base v3 ausente")

COMPORTAMENTO = {"tenure_days": 400.0, "seats": 12.0, "logins_7d": 2.0, "logins_30d": 9.0,
                 "avg_session_min": 3.0, "api_calls_7d": 10.0, "tickets_30d": 7.0,
                 "failed_pay_90d": 2.0, "nps_last": 1.0}


@pytest.fixture(autouse=True)
def isolado(tmp_path, monkeypatch):
    producao = tmp_path / "models"
    producao.mkdir()
    monkeypatch.setattr(rs, "MODELS_DIR", producao)
    monkeypatch.setattr(rs, "MODELO_PATH", producao / "voluntary_risk.joblib")
    monkeypatch.setattr(rs, "MODELO_META_PATH", producao / "voluntary_risk_meta.json")
    monkeypatch.setattr(rs, "_modelo", None)
    monkeypatch.setattr(rs, "_modelo_consultado", False)
    monkeypatch.setattr(vr, "MODELS_DIR", producao)
    monkeypatch.setattr(vr, "MODELO_PATH", producao / "voluntary_risk.joblib")
    monkeypatch.setattr(vr, "MODELO_META_PATH", producao / "voluntary_risk_meta.json")
    monkeypatch.setattr(bs, "_referencia_do_score", {})
    monkeypatch.setattr(bs, "_referencia_do_mrr", {})
    return producao


def _promover(producao, origem=V3, base=BASE_V3):
    return P.main(["--promover", "--origem", str(origem), "--destino", str(producao),
                   "--base", str(base)])


class TestPromover:
    def test_promove_e_o_risk_scorer_aceita_como_v3(self, isolado, capsys):
        assert _promover(isolado) == 0
        assert "v3 PROMOVIDO" in capsys.readouterr().out
        meta = json.loads((isolado / "voluntary_risk_meta.json").read_text(encoding="utf-8"))
        assert meta["contrato"] == "v3" and meta["contrato_de_producao"] is True
        assert meta["origem"]["joblib_sha256"] == P._sha256(V3 / "voluntary_risk_v3.joblib")
        quantis = meta["referencia_score_quantis"]
        assert len(quantis) == P.N_QUANTIS and quantis == sorted(quantis)
        assert 0.0 <= quantis[0] and quantis[-1] <= 1.0
        assert rs.carregar_modelo(forcar=True) is True and rs.contrato_ativo() == rs.CONTRATO_V3
        assert bs.referencia_do_meta() == quantis
        assert (isolado / "voluntary_risk.joblib").read_bytes() == \
            (V3 / "voluntary_risk_v3.joblib").read_bytes()

    def test_nao_sobrescreve_modelo_de_producao(self, isolado, capsys):
        assert _promover(isolado) == 0
        antes = (isolado / "voluntary_risk_meta.json").read_text(encoding="utf-8")
        assert _promover(isolado) == 1
        assert "RECUSADO" in capsys.readouterr().out
        assert (isolado / "voluntary_risk_meta.json").read_text(encoding="utf-8") == antes

    def test_recusa_origem_que_nao_e_v3(self, isolado, tmp_path):
        origem = tmp_path / "origem"
        origem.mkdir()
        shutil.copyfile(V3 / "voluntary_risk_v3.joblib", origem / "voluntary_risk_v3.joblib")
        meta = json.loads((V3 / "voluntary_risk_v3_meta.json").read_text(encoding="utf-8"))
        meta["features"] = rs.FEATURES_DE_RISCO
        (origem / "voluntary_risk_v3_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        assert _promover(isolado, origem=origem) == 1
        assert not (isolado / "voluntary_risk.joblib").exists()

    def test_recusa_base_que_nao_e_a_do_treino(self, isolado, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        pd.DataFrame({"x": [1, 2]}).to_parquet(base / "voluntario_v3.parquet")
        assert _promover(isolado, base=base) == 1
        assert not (isolado / "voluntary_risk.joblib").exists()


class TestReverter:
    def test_reverte_para_o_historico_e_a_regua_volta(self, isolado, capsys):
        assert _promover(isolado) == 0
        assert rs.carregar_modelo(forcar=True) is True
        assert P.main(["--reverter", "--destino", str(isolado)]) == 0
        assert not (isolado / "voluntary_risk.joblib").exists()
        (pasta,) = (isolado / "historico").iterdir()
        assert {a.name for a in pasta.iterdir()} == {"voluntary_risk.joblib",
                                                     "voluntary_risk_meta.json"}
        assert rs.carregar_modelo(forcar=True) is False
        assert rs.calculate_risk("Downgrade Clicked", {}) == 0.75

    def test_sem_modelo_nada_a_reverter(self, isolado, capsys):
        assert P.main(["--reverter", "--destino", str(isolado)]) == 1
        assert "Nada a reverter" in capsys.readouterr().out


class TestTravaDoAtivar:
    @pytest.fixture
    def candidato(self, isolado):
        (isolado / "voluntary_risk_candidato.joblib").write_bytes(b"candidato-v2")
        (isolado / "voluntary_risk_candidato_meta.json").write_text(
            json.dumps({"features": rs.FEATURES_DE_RISCO}), encoding="utf-8")

    def test_ativar_recusa_com_v3_em_producao(self, isolado, candidato, capsys):
        assert _promover(isolado) == 0
        joblib_v3 = (isolado / "voluntary_risk.joblib").read_bytes()
        assert vr.VoluntaryRiskModel.ativar() is False
        assert "ATIVACAO RECUSADA" in capsys.readouterr().out
        assert (isolado / "voluntary_risk.joblib").read_bytes() == joblib_v3

    def test_depois_do_reverter_ativar_volta_a_funcionar(self, isolado, candidato):
        assert _promover(isolado) == 0
        assert P.main(["--reverter", "--destino", str(isolado)]) == 0
        assert vr.VoluntaryRiskModel.ativar() is True
        assert (isolado / "voluntary_risk.joblib").read_bytes() == b"candidato-v2"

    def test_sem_v3_ativar_continua_como_antes(self, isolado, candidato):
        assert vr.VoluntaryRiskModel.ativar() is True


@pytest.fixture
def v3_ativo(isolado):
    assert _promover(isolado) == 0
    assert rs.carregar_modelo(forcar=True) is True


def _linha(cid, **extra):
    return {"customer_id_externo": cid, "mrr": 900.0, "billing_profile": "CLT",
            "days_since_last": 10.0, "features_used_30d": 2.0, **extra}


class TestColunasComportamentaisNoLote:
    def test_pontuar_lista_repassa_as_colunas(self, v3_ativo):
        com = bs.pontuar_lista([_linha("com", **COMPORTAMENTO)])[0]
        sem = bs.pontuar_lista([_linha("sem")])[0]
        props = {"days_since_last": 10.0, "features_used_30d": 2.0, "mrr": 900.0, **COMPORTAMENTO}
        assert com["risk_score"] == rs.decidir_risco(rs.EVENTO_DADO_ESTATICO, props)[0]
        assert com["risk_score"] != sem["risk_score"]
        assert com["tickets_30d"] == 7.0 and "tickets_30d" not in sem

    def test_pontuar_cliente_repassa_as_colunas_com_modelo(self, v3_ativo):
        props = {"days_since_last": 10.0, "features_used_30d": 2.0, "mrr": 900.0, **COMPORTAMENTO}
        linha = bs.pontuar_cliente(_linha("c", **COMPORTAMENTO))
        assert linha["risk_score"] == rs.decidir_risco(rs.EVENTO_DADO_ESTATICO, props)[0]

    def test_sem_modelo_as_colunas_nao_mudam_nada(self):
        com = bs.pontuar_cliente(_linha("c", **COMPORTAMENTO))
        sem = bs.pontuar_cliente(_linha("c"))
        assert com["risk_score"] == sem["risk_score"]

    def test_disparo_mantem_as_colunas_numericas_do_corpo(self):
        cliente, motivo = dl._normalizar({**_linha("d"), "tickets_30d": 3, "nps_last": "alto",
                                          "seats": -1, "logins_7d": True})
        assert motivo is None
        assert cliente["tickets_30d"] == 3.0
        assert not {"nps_last", "seats", "logins_7d"} & set(cliente)

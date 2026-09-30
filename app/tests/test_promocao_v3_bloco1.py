"""tests/test_promocao_v3_bloco1.py — o modelo v3 decide quando há dado.

Promoção do voluntário v3, Bloco 1 (30/09/2026). O que se trava aqui:

  - contrato versionado: `carregar_modelo` aceita o legado (FEATURES_DE_RISCO,
    sem "contrato") e o v3 (contrato "v3", features_versao 1,
    FEATURES_DE_RISCO_V3); recusa qualquer outra combinação;
  - D1: com o v3 ativo, o modelo decide sempre que houver dias OU uso
    (N_MINIMO_COLUNAS_COMPORTAMENTAIS = 0); sem os dois, a régua decide e o
    motivo vai para a trilha;
  - o vetor v3 manda NaN para o ausente, não 0.0;
  - a trilha registra modelo ou régua, com TreeSHAP quando o modelo decide, e
    `verificar_cadeia` continua íntegra;
  - nenhum dado pessoal nas entradas, nas contribuições nem na explicação.

O artefato é o de `models/v3/` (só lido), copiado para `tmp_path` com um meta
de produção. `models/` nunca é tocado; o `retention_log` já é isolado pelo
`conftest`.
"""

import json
import math
import shutil
from pathlib import Path

import pytest

from crai.churn_voluntary import disparo_lote as dl
from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import risk_scorer as rs
from crai.churn_voluntary import voluntary_agent as va
from crai.ml.voluntario_v3 import FEATURES_DE_RISCO_V3, FEATURES_DE_RISCO_V3_VERSAO

APP = Path(__file__).resolve().parents[1]
V3 = APP / "models" / "v3"

pytestmark = pytest.mark.skipif(not (V3 / "voluntary_risk_v3.joblib").exists(),
                                reason="artefato v3 ausente")

EMAIL, TELEFONE, NOME = "fulana@exemplo.com.br", "11912345678", "Fulana de Tal"

COMPLETO = {"days_since_last": 12, "features_used_30d": 3, "mrr": 1800.0,
            "tenure_days": 400, "seats": 12, "logins_7d": 20, "logins_30d": 90,
            "avg_session_min": 14.5, "api_calls_7d": 900, "tickets_30d": 4,
            "failed_pay_90d": 1, "nps_last": 5.5}


@pytest.fixture(autouse=True)
def isolado(tmp_path, monkeypatch):
    """Nenhum teste vê o modelo de outro nem o de `models/`."""
    monkeypatch.setattr(rs, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(rs, "MODELO_PATH", tmp_path / "voluntary_risk.joblib")
    monkeypatch.setattr(rs, "MODELO_META_PATH", tmp_path / "voluntary_risk_meta.json")
    monkeypatch.setattr(rs, "_modelo", None)
    monkeypatch.setattr(rs, "_modelo_consultado", False)
    monkeypatch.setattr(rs, "_contrato", rs.CONTRATO_LEGADO)
    monkeypatch.setattr(ob, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "bandit_state.json")
    return tmp_path


def _meta_v3(**troca):
    meta = json.loads((V3 / "voluntary_risk_v3_meta.json").read_text(encoding="utf-8"))
    meta.update({"contrato": "v3", "contrato_de_producao": True})
    meta.update(troca)
    return meta


def _instalar(meta, joblib_de=V3 / "voluntary_risk_v3.joblib"):
    shutil.copyfile(joblib_de, rs.MODELO_PATH)
    rs.MODELO_META_PATH.write_text(json.dumps(meta), encoding="utf-8")
    return rs.carregar_modelo(forcar=True)


@pytest.fixture
def v3_ativo():
    assert _instalar(_meta_v3()) is True
    assert rs._contrato == rs.CONTRATO_V3


class TestContratoVersionado:
    def test_v3_com_contrato_e_versao_e_aceito(self, v3_ativo):
        assert rs.modelo_ativo() is True

    @pytest.mark.parametrize("troca", [
        {"features_versao": FEATURES_DE_RISCO_V3_VERSAO + 1},
        {"contrato": "v4"},
        {"features": list(reversed(FEATURES_DE_RISCO_V3))},
        {"features": FEATURES_DE_RISCO_V3 + ["coluna_desconhecida"]},
        {"features_versao": None},
    ])
    def test_lista_ou_versao_ou_contrato_desconhecido_e_recusado(self, troca, capsys):
        assert _instalar(_meta_v3(**troca)) is False
        assert rs.modelo_ativo() is False
        assert "IGNORADO" in capsys.readouterr().out

    def test_meta_de_models_v3_sem_contrato_e_recusado(self):
        meta = json.loads((V3 / "voluntary_risk_v3_meta.json").read_text(encoding="utf-8"))
        assert "contrato" not in meta
        assert _instalar(meta) is False

    def test_legado_continua_aceito_e_com_vetor_de_zeros(self):
        assert rs.contrato_do_meta({"features": rs.FEATURES_DE_RISCO}) == rs.CONTRATO_LEGADO
        assert rs._vetor_de_features("Session Started", {}) == [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    def test_legado_com_features_versao_e_recusado(self):
        assert rs.contrato_do_meta({"features": rs.FEATURES_DE_RISCO,
                                    "features_versao": 1}) is None


class TestVetorV3:
    def test_ausente_vira_nan_e_nao_zero(self):
        vetor = rs._vetor_v3("Session Started", {"days_since_last": 3})
        nomes = dict(zip(FEATURES_DE_RISCO_V3, vetor))
        assert nomes["days_since_last"] == 3.0
        assert math.isnan(nomes["tickets_30d"]) and math.isnan(nomes["features_used_30d"])
        assert nomes["evento_sessao"] == 1.0 and nomes["evento_cancelamento"] == 0.0

    def test_valor_torto_vira_nan(self):
        vetor = rs._vetor_v3("Session Started", {"nps_last": "alto", "seats": -3,
                                                 "logins_7d": True})
        nomes = dict(zip(FEATURES_DE_RISCO_V3, vetor))
        assert all(math.isnan(nomes[c]) for c in ("nps_last", "seats", "logins_7d"))


class TestQuemDecide:
    def test_dado_completo_e_decidido_pelo_modelo(self, v3_ativo):
        a = rs.avaliar_risco("Session Started", dict(COMPLETO))
        assert a["modelo"] == rs.NOME_DO_MODELO and a["modelo_versao"]
        assert a["motivo_da_regra"] is None
        esperado = round(float(rs._modelo.predict_proba(
            [rs._vetor_v3("Session Started", COMPLETO)])[0][1]), 3)
        assert a["risco"] == esperado == rs.calculate_risk("Session Started", dict(COMPLETO))

    def test_n_zero_so_com_dias_e_decidido_pelo_modelo(self, v3_ativo):
        assert rs.N_MINIMO_COLUNAS_COMPORTAMENTAIS == 0
        a = rs.avaliar_risco("Session Started", {"days_since_last": 9})
        assert a["modelo"] == rs.NOME_DO_MODELO

    def test_sem_dias_e_sem_uso_cai_na_regua_com_motivo(self, v3_ativo):
        props = {"tickets_30d": 4, "nps_last": 3.0, "mrr": 900.0}
        a = rs.avaliar_risco("Downgrade Clicked", props)
        assert a["modelo"] == "regra" and a["modelo_versao"] is None
        assert a["motivo_da_regra"] == rs.MOTIVO_SEM_DADO_PARA_O_MODELO
        assert a["contribuicoes"] is None
        assert a["risco"] == rs._risco_por_regras("Downgrade Clicked", props) == 0.75

    def test_sem_modelo_nada_muda(self):
        a = rs.avaliar_risco("Session Started", {"days_since_last": 15, "features_used_30d": 2})
        assert a == {"risco": rs._risco_por_regras("Session Started",
                                                   {"days_since_last": 15, "features_used_30d": 2}),
                     "modelo": "regra", "modelo_versao": None, "motivo_da_regra": None,
                     "contribuicoes": None, "contrato": None}


class TestExplicacao:
    def test_contribuicoes_tem_no_maximo_tres_com_direcao(self, v3_ativo):
        c = rs.avaliar_risco("Cancellation Page Viewed", dict(COMPLETO))["contribuicoes"]
        assert 1 <= len(c) <= 3
        assert all({"feature", "valor", "direcao", "peso"} <= set(i) for i in c)
        assert all(i["direcao"] in "+-" for i in c)
        pesos = [abs(i["peso"]) for i in c]
        assert pesos == sorted(pesos, reverse=True)
        assert not {i["feature"] for i in c} & {"evento_cancelamento", "evento_downgrade",
                                                "evento_sessao"}

    def test_coluna_ausente_nao_vira_motivo(self, v3_ativo):
        c = rs.avaliar_risco("Session Started", {"days_since_last": 20})["contribuicoes"]
        assert c and {i["feature"] for i in c} <= {"days_since_last", "event"}

    def test_toda_feature_v3_tem_rotulo_em_portugues(self):
        for f in FEATURES_DE_RISCO_V3:
            if f.startswith("evento_"):
                continue
            assert f in rl.ROTULOS_DE_FEATURE, f
            assert "=" not in rl._rotular(f, 3, rl.ROTULOS_DE_FEATURE)


def _estado(props, event="Session Started", tenant="tenant-b1"):
    return {"tenant_id": tenant, "user_id": "cli-1", "event": event, "props": props,
            "decisoes": []}


class TestTrilha:
    @pytest.mark.asyncio
    async def test_modelo_decide_e_a_trilha_registra_modelo_e_shap(self, v3_ativo):
        s = await va.assess_risk(_estado(dict(COMPLETO)))
        (d,) = s["decisoes"]
        assert d["modelo"] == rs.NOME_DO_MODELO and d["contribuicoes"]
        assert "regra" not in d["saida"]
        assert d["entradas"]["tickets_30d"] == 4.0
        assert "Pesaram, nesta ordem" in d["explicacao"]
        assert "modelo de risco de cancelamento" in d["explicacao"]

    @pytest.mark.asyncio
    async def test_regua_decide_e_o_motivo_aparece(self, v3_ativo):
        s = await va.assess_risk(_estado({"mrr": 500.0, "tickets_30d": 2}, "Downgrade Clicked"))
        (d,) = s["decisoes"]
        assert d["modelo"] == "regra" and d["contribuicoes"] is None
        assert d["saida"]["motivo_da_regra"] == rs.MOTIVO_SEM_DADO_PARA_O_MODELO
        assert "dado insuficiente para o modelo" in d["explicacao"]

    @pytest.mark.asyncio
    async def test_cadeia_continua_integra_com_as_duas(self, v3_ativo):
        tenant = "tenant-cadeia-b1"
        s1 = await va.assess_risk(_estado(dict(COMPLETO), tenant=tenant))
        s2 = await va.assess_risk(_estado({"mrr": 500.0}, "Downgrade Clicked", tenant=tenant))
        ids = rl.registrar_decisoes(s1["decisoes"] + s2["decisoes"])
        assert all(i is not None for i in ids)
        r = rl.verificar_cadeia(tenant)
        assert r["integra"] is True and r["linhas"] == 2
        modelos = [d["modelo"] for d in rl.decisoes_do_sujeito(tenant, "cli-1")]
        assert sorted(modelos) == sorted([rs.NOME_DO_MODELO, "regra"])

    @pytest.mark.asyncio
    async def test_nenhum_dado_pessoal_na_decisao(self, v3_ativo):
        props = {**COMPLETO, "email": EMAIL, "phone": TELEFONE, "nome": NOME}
        s = await va.assess_risk(_estado(props))
        texto = json.dumps(s["decisoes"][0], ensure_ascii=False, default=str)
        for pessoal in (EMAIL, TELEFONE, NOME):
            assert pessoal not in texto

    @pytest.mark.asyncio
    async def test_lote_registra_modelo_e_shap(self, v3_ativo, monkeypatch):
        monkeypatch.setattr(va, "_channel_history", {})
        linha = {"customer_id_externo": "lote-1", "risk_score": 0.4,
                 "criticality": "alto", "days_since_last": 12, "features_used_30d": 3,
                 "mrr": 800.0, "billing_profile": "PJ", "origem_da_regua": "regua_global"}
        _, estado = await dl.tratar(linha, "tenant-lote-b1")
        risco = estado["decisoes"][0]
        assert risco["tipo_decisao"] == "risco"
        assert risco["modelo"] == rs.NOME_DO_MODELO and risco["contribuicoes"]
        assert "regra" not in risco["saida"]

"""tests/test_promocao_v3_bloco2.py — o risco vira ação pela posição.

Promoção do voluntário v3, Bloco 2 (30/09/2026). Com o modelo ativo:

  - grave (critico) = os 10% de maior score da base da empresa, preocupante
    (alto) = os 20% seguintes, sem risco (padrao) = o resto — SEMPRE com o
    sinal absoluto de desengajamento;
  - a porta de valor (MRR >= R$ 2.000) continua separada;
  - empresas diferentes não misturam posição;
  - o /insights (pontuar_base), o disparo em lote com lista no corpo e o SDK
    usam o caminho novo; sem modelo, nada muda.

Os clientes vêm da base v3 (`data/v3/`, só lida), um por cliente (o último
evento). O modelo é o de `models/v3/`, copiado para `tmp_path` com meta de
produção. A base importada não é tocada: `listar` é trocado por monkeypatch.
"""

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from crai.churn_voluntary import batch_scoring as bs
from crai.churn_voluntary import disparo_lote as dl
from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary import risk_scorer as rs
from crai.churn_voluntary import voluntary_agent as va

APP = Path(__file__).resolve().parents[1]
V3 = APP / "models" / "v3"
BASE_V3 = APP / "data" / "v3" / "voluntario_v3.parquet"

pytestmark = pytest.mark.skipif(
    not (V3 / "voluntary_risk_v3.joblib").exists() or not BASE_V3.exists(),
    reason="artefato ou base v3 ausente")

MRR_ABAIXO_DA_PORTA = 1500.0


@pytest.fixture(scope="module")
def v3_por_cliente():
    df = pd.read_parquet(BASE_V3)
    return df.groupby("customer_id", sort=False).tail(1).reset_index(drop=True)


def _clientes(df, mrr_max=MRR_ABAIXO_DA_PORTA, **forcar):
    cols = ["days_since_last", "features_used_30d", "mrr", *rs.colunas_comportamentais_v3()]
    saida = []
    for r in df.itertuples():
        c = {"customer_id_externo": r.customer_id, "billing_profile": "CLT",
             **{k: float(getattr(r, k)) for k in cols}}
        c["mrr"] = min(c["mrr"], mrr_max)
        for k, f in forcar.items():
            c[k] = f(c[k])
        saida.append(c)
    return saida


def _meta(**extra):
    meta = json.loads((V3 / "voluntary_risk_v3_meta.json").read_text(encoding="utf-8"))
    meta.update({"contrato": "v3", "contrato_de_producao": True, **extra})
    return meta


@pytest.fixture(autouse=True)
def isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(rs, "MODELO_PATH", tmp_path / "voluntary_risk.joblib")
    monkeypatch.setattr(rs, "MODELO_META_PATH", tmp_path / "voluntary_risk_meta.json")
    monkeypatch.setattr(rs, "_modelo", None)
    monkeypatch.setattr(rs, "_modelo_consultado", False)
    monkeypatch.setattr(rs, "_contrato", rs.CONTRATO_LEGADO)
    monkeypatch.setattr(bs, "_referencia_do_score", {})
    monkeypatch.setattr(ob, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "bandit_state.json")
    monkeypatch.delenv("CRAI_HIGH_VALUE_MRR_THRESHOLD", raising=False)


def _instalar(meta=None):
    shutil.copyfile(V3 / "voluntary_risk_v3.joblib", rs.MODELO_PATH)
    rs.MODELO_META_PATH.write_text(json.dumps(meta or _meta()), encoding="utf-8")
    assert rs.carregar_modelo(forcar=True) is True


@pytest.fixture
def v3_ativo():
    _instalar()


def _proporcoes(linhas):
    s = pd.Series([l["criticality"] for l in linhas]).value_counts(normalize=True)
    return {k: float(s.get(k, 0.0)) for k in ("critico", "alto", "padrao")}


class TestDistribuicao:
    def test_risco_espalhado_com_sinal_fica_perto_de_10_20_70(self, v3_ativo, v3_por_cliente):
        clientes = _clientes(v3_por_cliente.sample(1000, random_state=7),
                             days_since_last=lambda d: max(d, 7.0))
        p = _proporcoes(bs.pontuar_lista(clientes))
        assert 0.08 <= p["critico"] <= 0.12, p
        assert 0.17 <= p["alto"] <= 0.23, p
        assert 0.66 <= p["padrao"] <= 0.74, p

    def test_base_saudavel_nao_tem_grave_nem_preocupante(self, v3_ativo, v3_por_cliente):
        clientes = _clientes(v3_por_cliente.sample(1000, random_state=8),
                             days_since_last=lambda d: min(d, 3.0),
                             features_used_30d=lambda u: max(u, 3.0))
        linhas = bs.pontuar_lista(clientes)
        assert _proporcoes(linhas)["critico"] == 0.0
        assert _proporcoes(linhas)["alto"] == 0.0
        topo = max(linhas, key=lambda l: l["risk_score"])
        assert topo["posicao_na_base"] >= 0.9           # continua ordenado...
        assert "sem sinal de abandono" in topo["explicacao"]   # ...e diz por quê

    def test_comportamento_real_nunca_passa_das_faixas(self, v3_ativo, v3_por_cliente):
        linhas = bs.pontuar_lista(_clientes(v3_por_cliente.sample(1000, random_state=9)))
        p = _proporcoes(linhas)
        assert p["critico"] <= 0.10 + 0.005 and p["alto"] <= 0.20 + 0.005, p
        for l in linhas:
            if l["criticality"] in ("critico", "alto"):
                assert rs.sinal_absoluto_de_desengajamento(l["days_since_last"],
                                                           l["features_used_30d"])


class TestRiscoReal:
    def test_graves_sao_majoritariamente_os_de_risco_real(self, v3_ativo, v3_por_cliente):
        pool = v3_por_cliente.sample(10000, random_state=11).sort_values("oculto_p_churn")
        em_risco = pool.tail(100)                                  # 10%: risco latente alto
        sem_risco = pool.head(len(pool) // 2).sample(900, random_state=12)
        clientes = _clientes(pd.concat([em_risco, sem_risco]))
        ids_risco = set(em_risco["customer_id"])
        graves = [l for l in bs.pontuar_lista(clientes) if l["criticality"] == "critico"]
        assert graves
        assert sum(l["customer_id_externo"] in ids_risco for l in graves) / len(graves) > 0.5


class TestPortaDeValor:
    """D-B2-3: no caminho do v3 a porta de valor só PROMOVE preocupante a grave,
    com o MRR no topo 20% da base; nunca marca grave sozinha. O caminho
    legado (régua e v2) continua com o limiar fixo por variável de ambiente."""

    def test_base_saudavel_com_mrr_alto_ninguem_vira_grave(self, v3_ativo, v3_por_cliente):
        clientes = _clientes(v3_por_cliente.sample(300, random_state=13), mrr_max=1e9,
                             days_since_last=lambda d: min(d, 2.0),
                             features_used_30d=lambda u: max(u, 3.0))
        clientes[0]["mrr"] = 50000.0
        assert sum(c["mrr"] >= 2000 for c in clientes) > 30     # MRR alto de verdade
        linhas = bs.pontuar_lista(clientes)
        assert {l["criticality"] for l in linhas} == {"padrao"}

    def test_preocupante_no_topo_de_mrr_vira_grave(self, v3_ativo, v3_por_cliente):
        clientes = _clientes(v3_por_cliente.sample(1000, random_state=21), mrr_max=1e9,
                             days_since_last=lambda d: max(d, 7.0))
        ref_mrr = bs.referencia_de_mrr(clientes)
        linhas = bs.pontuar_lista(clientes)
        promovidos = [l for l in linhas if l["criticality"] == "critico"
                      and l["posicao_na_base"] < 0.90]
        assert promovidos
        for l in promovidos:
            assert l["posicao_na_base"] >= 0.70
            assert bs.mrr_no_topo(bs._posicao_do_mrr(l["mrr"], ref_mrr))
            assert "grave pelo valor da conta" in l["explicacao"]
        for l in linhas:
            if l["criticality"] == "alto":
                assert not bs.mrr_no_topo(bs._posicao_do_mrr(l["mrr"], ref_mrr))

    def test_sem_risco_no_topo_de_mrr_continua_sem_risco(self, v3_ativo, v3_por_cliente):
        clientes = _clientes(v3_por_cliente.sample(1000, random_state=22), mrr_max=1e9,
                             days_since_last=lambda d: max(d, 7.0))
        ref_mrr = bs.referencia_de_mrr(clientes)
        linhas = bs.pontuar_lista(clientes)
        topo_sem_risco = [l for l in linhas if l["posicao_na_base"] < 0.70
                          and bs.mrr_no_topo(bs._posicao_do_mrr(l["mrr"], ref_mrr))]
        assert topo_sem_risco
        assert all(l["criticality"] == "padrao" for l in topo_sem_risco)

    def test_tabela_de_criticidade_por_posicao(self):
        topo = 0.95                                        # MRR no topo 20%
        assert bs.criticidade_por_posicao(0.95, 30, 0) == "critico"
        assert bs.criticidade_por_posicao(0.75, 30, 0) == "alto"
        assert bs.criticidade_por_posicao(0.75, 30, 0, posicao_mrr=topo) == "critico"
        assert bs.criticidade_por_posicao(0.75, 30, 0, posicao_mrr=0.50) == "alto"
        assert bs.criticidade_por_posicao(0.30, 30, 0, posicao_mrr=topo) == "padrao"
        assert bs.criticidade_por_posicao(0.95, 1, 5, posicao_mrr=topo) == "padrao"   # sem sinal
        assert bs.criticidade_por_posicao(None, 30, 0, posicao_mrr=topo) == "padrao"  # sem referência

    def test_caminho_legado_continua_com_o_limiar_fixo(self, monkeypatch):
        assert rs.modelo_ativo() is False
        cliente = {"customer_id_externo": "grande", "mrr": 2500.0, "billing_profile": "CLT",
                   "days_since_last": 1, "features_used_30d": 12}
        assert bs.pontuar_lista([cliente])[0]["criticality"] == "critico"
        monkeypatch.setenv("CRAI_HIGH_VALUE_MRR_THRESHOLD", "3000")
        assert bs.pontuar_lista([cliente])[0]["criticality"] == "padrao"


class TestEmpresasNaoMisturam:
    def test_referencia_e_por_tenant(self, v3_ativo, v3_por_cliente, monkeypatch):
        saudavel = _clientes(v3_por_cliente.sample(300, random_state=14),
                             days_since_last=lambda d: min(d, 2.0))
        arriscada = _clientes(v3_por_cliente.sample(300, random_state=15),
                              days_since_last=lambda d: max(d, 20.0),
                              features_used_30d=lambda u: min(u, 1.0))
        bases = {"tenant-A": saudavel, "tenant-B": arriscada}
        monkeypatch.setattr(bs.clientes_importados, "listar", lambda t: bases[t])
        bs.pontuar_base("tenant-A")
        bs.pontuar_base("tenant-B")
        ref_a, ref_b = bs._referencia_do_score["tenant-A"], bs._referencia_do_score["tenant-B"]
        assert ref_a != ref_b
        risco = float(pd.Series(ref_b).median())
        pos_a = bs.criticidade_do_evento("tenant-A", risco, 500.0, 30, 0, True)["posicao_na_base"]
        pos_b = bs.criticidade_do_evento("tenant-B", risco, 500.0, 30, 0, True)["posicao_na_base"]
        assert pos_a > pos_b
        assert bs.criticidade_do_evento("tenant-C", risco, 500.0, 30, 0, True)["posicao_na_base"] is None


class TestCaminhos:
    def test_insights_pontuar_base_usa_a_posicao(self, v3_ativo, v3_por_cliente, monkeypatch):
        base = _clientes(v3_por_cliente.sample(100, random_state=16))
        base.append({"customer_id_externo": "sem-dado", "mrr": 300.0})
        monkeypatch.setattr(bs.clientes_importados, "listar", lambda t: base)
        ranking = bs.pontuar_base("tenant-insights")
        assert ranking[-1]["criticality"] == bs.CRITICIDADE_SEM_DADO
        assert all(l["risco_decidido_por"] == "modelo" for l in ranking[:-1])
        assert all(l["origem_da_regua"] == bs.REGUA_BASE for l in ranking[:-1])
        assert all(0.0 <= l["posicao_na_base"] <= 1.0 for l in ranking[:-1])

    def test_disparo_com_lista_no_corpo_usa_a_posicao(self, v3_ativo, v3_por_cliente):
        corpo = [{k: c[k] for k in ("customer_id_externo", "mrr", "billing_profile",
                                    "days_since_last", "features_used_30d")}
                 for c in _clientes(v3_por_cliente.sample(60, random_state=17))]
        linhas, descartes, _ = dl.preparar(corpo, "tenant-disparo")
        assert not descartes and len(linhas) == 60
        assert all(l["risco_decidido_por"] == "modelo" for l in linhas)
        assert all(l["posicao_na_base"] is not None for l in linhas)

    def test_lista_pequena_usa_a_referencia_do_meta_ou_so_o_valor(self, v3_por_cliente):
        _instalar(_meta(referencia_score_quantis=[i / 100 for i in range(101)]))
        poucos = _clientes(v3_por_cliente.sample(5, random_state=18))
        linhas = bs.pontuar_lista(poucos)
        assert all(l["origem_da_regua"] == bs.REGUA_GLOBAL for l in linhas)
        assert all(l["posicao_na_base"] is not None for l in linhas)
        _instalar()                                     # meta sem quantis
        linhas = bs.pontuar_lista(poucos)
        assert all(l["posicao_na_base"] is None for l in linhas)
        assert all(l["criticality"] == "padrao" for l in linhas)
        assert all("sem base de comparação" in l["explicacao"] for l in linhas)

    @pytest.mark.asyncio
    async def test_sdk_posiciona_contra_a_base_do_tenant(self, v3_ativo, v3_por_cliente,
                                                         monkeypatch):
        base = _clientes(v3_por_cliente.sample(200, random_state=19))
        monkeypatch.setattr(bs.clientes_importados, "listar", lambda t: base)
        bs.pontuar_base("tenant-sdk")
        props = {"days_since_last": 30, "features_used_30d": 0, "mrr": 500.0,
                 "tickets_30d": 9, "nps_last": 1.0}
        s = await va.assess_risk({"tenant_id": "tenant-sdk", "user_id": "u1",
                                  "event": "Session Started", "props": props, "decisoes": []})
        d = s["decisoes"][0]
        assert d["modelo"] == rs.NOME_DO_MODELO
        assert d["saida"]["posicao"]["referencia"] == bs.REGUA_BASE
        esperado = bs.criticidade_do_evento("tenant-sdk", s["risk_score"], 500.0, 30, 0, True)
        assert s["criticality"] == esperado["criticality"]
        assert "posicao" not in d["explicacao"] and "=" not in d["explicacao"]


class TestSemModeloNadaMuda:
    def test_pontuar_lista_e_a_regua_de_sempre(self, v3_por_cliente):
        clientes = _clientes(v3_por_cliente.sample(80, random_state=20))
        regua = bs.regua_da_base(clientes)
        assert bs.pontuar_lista(clientes) == [bs.pontuar_cliente(c, regua) for c in clientes]

    @pytest.mark.asyncio
    async def test_sdk_sem_modelo_usa_classify_criticality(self):
        props = {"days_since_last": 30, "features_used_30d": 0, "mrr": 500.0}
        s = await va.assess_risk({"tenant_id": "t", "user_id": "u", "event": "Session Started",
                                  "props": props, "decisoes": []})
        assert s["criticality"] == rs.classify_criticality(s["risk_score"], 500.0)
        assert "posicao" not in s["decisoes"][0]["saida"]

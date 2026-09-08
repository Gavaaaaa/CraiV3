"""tests/test_batch_scoring.py — a base importada passa pelo mesmo motor, sem inventar risco.

O que o Sprint 3 do self-service promete, e o que aqui é verificado:

  (a) `risco_por_features` é o MESMO número de `calculate_risk` para o mesmo
      dado — regra ou modelo treinado, sem duplicar lógica;
  (b) `pontuar_base` devolve a base do tenant ordenada por risco decrescente;
  (c) cliente sem `days_since_last` NEM `features_used_30d` vira
      `dado_insuficiente` com `risk_score: None` — nunca 0.0 silencioso — e
      fica por ÚLTIMO, separado do 0.0 de verdade;
  (d) tenant A nunca vê a base de tenant B.

A catraca do Sprint 6 (`test_risk_pluggable`) continua valendo: extrair o
núcleo não mudou nenhum valor de `calculate_risk`.

ISOLAMENTO: `CRAI_CLIENTES_DB` em `tmp_path` (conftest) e o modelo do
risk_scorer resetado por teste, como em `test_risk_pluggable`.

Uso:
    pytest tests/test_batch_scoring.py -v
"""

import pytest

from crai.churn_voluntary import batch_scoring as bs
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import risk_scorer as rs
from crai.churn_voluntary.risk_scorer import (
    calculate_risk,
    classify_criticality,
    risco_por_features,
)

TENANT = "empresa-exemplo"


@pytest.fixture(autouse=True)
def modelo_isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(rs, "MODELO_PATH", tmp_path / "voluntary_risk.joblib")
    monkeypatch.setattr(rs, "MODELO_META_PATH", tmp_path / "voluntary_risk_meta.json")
    monkeypatch.setattr(rs, "_modelo", None)
    monkeypatch.setattr(rs, "_modelo_consultado", False)


def _cliente(cid, mrr=300.0, perfil="CLT", dias=None, uso=None, email=None):
    return {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil,
            "days_since_last": dias, "features_used_30d": uso, "email": email}


# ── (a) o mesmo motor ────────────────────────────────────────────────────

class TestMesmoMotor:
    @pytest.mark.parametrize("dias,uso,mrr", [
        (18, 1, 300.0), (30, 0, 2500.0), (0, 30, 100.0), (7, 3, None), (47, 1, 1500.0),
    ])
    def test_risco_por_features_igual_a_calculate_risk_session_started(self, dias, uso, mrr):
        props = {"days_since_last": dias, "features_used_30d": uso}
        if mrr is not None:
            props["mrr"] = mrr
        assert risco_por_features(dias, uso, mrr) == calculate_risk("Session Started", props)

    def test_ausente_vira_o_mesmo_default_das_regras(self):
        # Chave ausente no payload do SDK == None na planilha.
        assert risco_por_features(18, None, 300) == calculate_risk(
            "Session Started", {"days_since_last": 18})
        assert risco_por_features(None, 1, 300) == calculate_risk(
            "Session Started", {"features_used_30d": 1})

    def test_evento_explicito_passa_pelas_mesmas_regras(self):
        assert risco_por_features(99, 0, 100, event="Cancellation Page Viewed") == 0.90
        assert risco_por_features(0, 10, 100, event="Downgrade Clicked") == 0.75
        assert risco_por_features(0, 10, 100, event="Evento Desconhecido") == 0.0

    def test_modelo_treinado_decide_nos_dois_caminhos(self):
        class Modelo:
            def __init__(self):
                self.vistos = []

            def predict_proba(self, X):
                self.vistos.append(list(X[0]))
                return [[0.58, 0.42]]

        modelo = Modelo()
        rs._modelo, rs._modelo_consultado = modelo, True

        assert risco_por_features(47, 1, 1500.0) == 0.42
        assert calculate_risk("Session Started",
                              {"days_since_last": 47, "features_used_30d": 1,
                               "mrr": 1500.0}) == 0.42
        # O vetor é IDÊNTICO nos dois caminhos — inclusive `evento_sessao=1`.
        assert modelo.vistos[0] == modelo.vistos[1] == [47.0, 1.0, 1500.0, 0.0, 0.0, 1.0]

    def test_calculate_risk_nao_mudou_para_chave_presente_com_none(self):
        """`calculate_risk` recebe `props` cru; `None` NÃO vira default lá —
        é o comportamento de antes deste sprint, e a extração não o alterou."""
        with pytest.raises(TypeError):
            calculate_risk("Session Started", {"days_since_last": None})


# ── (b) e (c) o ranking ──────────────────────────────────────────────────

class TestPontuarBase:
    def test_ordenado_por_risco_decrescente_com_sem_dado_por_ultimo(self):
        ci.gravar(TENANT, [
            _cliente("ativo", dias=0, uso=30),                 # 0.0 de verdade
            _cliente("morno", dias=7, uso=3),                  # 0.283
            _cliente("frio", dias=30, uso=0),                  # 1.0
            _cliente("sem-dado", mrr=5000.0),                  # dado_insuficiente
            _cliente("quase", dias=18, uso=1),                 # 0.66
        ])
        ranking = bs.pontuar_base(TENANT)
        assert [l["customer_id_externo"] for l in ranking] == \
            ["frio", "quase", "morno", "ativo", "sem-dado"]

        riscos = [l["risk_score"] for l in ranking[:-1]]
        assert riscos == sorted(riscos, reverse=True)
        assert ranking[-1]["risk_score"] is None
        assert ranking[-1]["criticality"] == "dado_insuficiente"

    def test_sem_dado_nunca_e_zero_silencioso(self):
        ci.gravar(TENANT, [_cliente("sem-dado", mrr=100.0)])
        (linha,) = bs.pontuar_base(TENANT)
        assert linha["risk_score"] is None
        assert linha["criticality"] == "dado_insuficiente"
        assert "impossível avaliar" in linha["explicacao"]
        # E o mesmo dado, forçado pelo motor, daria 0.0 — é exatamente o que
        # o módulo existe para não fazer.
        assert risco_por_features(None, None, 100.0) == 0.0

    def test_sem_dado_e_mrr_alto_continua_sem_dado(self):
        """MRR alto sozinho vira 'critico' em `classify_criticality`. Sem dado de
        atividade, nem isso: MRR é cadastro, não sinal de churn."""
        ci.gravar(TENANT, [_cliente("grande-sem-dado", mrr=10_000.0)])
        (linha,) = bs.pontuar_base(TENANT)
        assert linha["criticality"] == "dado_insuficiente"
        assert linha["risk_score"] is None

    def test_sem_dado_fica_atras_do_zero_de_verdade(self):
        ci.gravar(TENANT, [_cliente("sem-dado", mrr=9000.0), _cliente("ativo", dias=0, uso=30)])
        ranking = bs.pontuar_base(TENANT)
        assert ranking[0]["customer_id_externo"] == "ativo"
        assert ranking[0]["risk_score"] == 0.0
        assert ranking[1]["customer_id_externo"] == "sem-dado"

    def test_uma_das_features_presente_pontua_e_avisa_na_explicacao(self):
        ci.gravar(TENANT, [_cliente("so-dias", dias=47), _cliente("so-uso", uso=1)])
        por_id = {l["customer_id_externo"]: l for l in bs.pontuar_base(TENANT)}
        assert por_id["so-dias"]["risk_score"] == risco_por_features(47, None, 300.0)
        assert "assumido 10" in por_id["so-dias"]["explicacao"]
        assert por_id["so-uso"]["risk_score"] == risco_por_features(None, 1, 300.0)
        assert "assumido 0" in por_id["so-uso"]["explicacao"]

    def test_criticidade_e_explicacao(self):
        ci.gravar(TENANT, [
            _cliente("critico-por-risco", dias=47, uso=1, mrr=1500.0),
            _cliente("critico-por-valor", dias=2, uso=12, mrr=2500.0),
            _cliente("alto", dias=30, uso=5, mrr=300.0),        # 0.7 → alto? 0.7 < 0.75 → padrao
            _cliente("padrao", dias=1, uso=8, mrr=300.0),
        ])
        por_id = {l["customer_id_externo"]: l for l in bs.pontuar_base(TENANT)}

        c = por_id["critico-por-risco"]
        assert c["risk_score"] == 1.0 and c["criticality"] == "critico"
        assert "sem login há 47 dias" in c["explicacao"]
        assert "usa 1 funcionalidade nos últimos 30 dias" in c["explicacao"]
        assert "R$ 1.500,00" in c["explicacao"]

        v = por_id["critico-por-valor"]
        assert v["criticality"] == "critico" and v["risk_score"] < 0.90
        assert "pelo valor da conta" in v["explicacao"]
        assert "R$ 2.500,00" in v["explicacao"]

        assert por_id["padrao"]["criticality"] == "padrao"
        for linha in por_id.values():
            assert linha["criticality"] == (
                classify_criticality(linha["risk_score"], linha["mrr"]))

    def test_linha_carrega_o_que_o_sprint_4_precisa(self):
        ci.gravar(TENANT, [_cliente("c1", dias=3, uso=4, email="a@b.co")])
        (linha,) = bs.pontuar_base(TENANT)
        assert set(linha) >= {"customer_id_externo", "risk_score", "criticality",
                              "explicacao", "mrr", "billing_profile", "days_since_last",
                              "features_used_30d", "email", "importado_em"}
        assert linha["importado_em"].endswith("+00:00")

    def test_base_vazia_e_lista_vazia(self):
        assert bs.pontuar_base("empresa-sem-base") == []


# ── (d) isolamento ───────────────────────────────────────────────────────

class TestIsolamento:
    def test_tenant_a_nunca_ve_tenant_b(self):
        ci.gravar("empresa-a", [_cliente("a1", dias=30, uso=0), _cliente("a2", dias=1, uso=9)])
        ci.gravar("empresa-b", [_cliente("b1", dias=30, uso=0)])
        assert {l["customer_id_externo"] for l in bs.pontuar_base("empresa-a")} == {"a1", "a2"}
        assert {l["customer_id_externo"] for l in bs.pontuar_base("empresa-b")} == {"b1"}
        assert bs.pontuar_base("empresa-c") == []

    def test_mesmo_customer_id_em_tenants_diferentes_pontua_separado(self):
        ci.gravar("empresa-a", [_cliente("c-001", dias=30, uso=0)])
        ci.gravar("empresa-b", [_cliente("c-001", dias=0, uso=30)])
        assert bs.pontuar_base("empresa-a")[0]["risk_score"] == 1.0
        assert bs.pontuar_base("empresa-b")[0]["risk_score"] == 0.0


# ── Formatação ───────────────────────────────────────────────────────────

class TestTexto:
    @pytest.mark.parametrize("valor,esperado", [
        (1500.5, "R$ 1.500,50"), (300, "R$ 300,00"), (1234567.891, "R$ 1.234.567,89"),
        (0, "R$ 0,00"), (None, "MRR desconhecido"),
    ])
    def test_reais_pt_br(self, valor, esperado):
        assert bs._reais(valor) == esperado

    def test_singular_e_plural(self):
        assert "há 1 dia," in bs.explicar(_cliente("x", dias=1, uso=1), 0.1, "padrao")
        assert "usa 1 funcionalidade nos" in bs.explicar(_cliente("x", dias=1, uso=1), 0.1, "padrao")
        assert "acessou hoje" in bs.explicar(_cliente("x", dias=0, uso=2), 0.1, "padrao")
        assert "usa 2 funcionalidades" in bs.explicar(_cliente("x", dias=0, uso=2), 0.1, "padrao")

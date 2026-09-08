"""tests/test_insights_unificados.py — upload e SDK viram UMA lista, sem cliente repetido.

O que o Sprint 4 do self-service promete, e o que aqui é verificado:

  (a) cliente só no upload aparece com `origem: "upload"`;
  (b) cliente só em eventos do SDK aparece com `origem: "sdk"`, com o risco
      que o ciclo GRAVOU e o `user:` tirado do id;
  (c) cliente presente nos dois aparece UMA vez, e vence o dado mais recente
      (`registrado_em` vs `importado_em`); empate exato → SDK;
  (d) `ultimo_ciclo_por_cliente` devolve só a linha mais recente de cada
      cliente, só do tenant pedido, e nunca levanta;
  (e) os filtros do endpoint: criticidade mínima e limite.

ISOLAMENTO: `CRAI_CLIENTES_DB` e `CRAI_RETENTION_DB` em `tmp_path` (conftest).
Os ciclos do SDK são gravados direto por `registrar_ciclo`, com o
`registrado_em` controlado pelo relógio do módulo.

Uso:
    pytest tests/test_insights_unificados.py -v
"""

import sqlite3

import pytest

from crai.churn_voluntary import batch_scoring as bs
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import insights_unificados as iu
from crai.churn_voluntary import retention_log as rl

TENANT = "empresa-exemplo"


def _cliente(cid, mrr=300.0, perfil="CLT", dias=None, uso=None, email=None):
    return {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil,
            "days_since_last": dias, "features_used_30d": uso, "email": email}


def _ciclo(user_id, risk, crit, event="Session Started", dias=None, uso=None,
           mrr=None, tenant=TENANT, perfil="PJ"):
    props = {}
    if dias is not None:
        props["days_since_last"] = dias
    if uso is not None:
        props["features_used_30d"] = uso
    if mrr is not None:
        props["mrr"] = mrr
    props["billing_profile"] = perfil
    return rl.registrar_ciclo({
        "tenant_id": tenant, "user_id": user_id, "event": event, "props": props,
        "risk_score": risk, "profile": perfil, "criticality": crit,
        "offer_type": "desconto_10", "channel": "whatsapp", "offer_sent": True,
    })


def _com_relogio(monkeypatch, iso: str):
    monkeypatch.setattr(rl, "_agora", lambda: iso)


def _com_relogio_upload(monkeypatch, iso: str):
    monkeypatch.setattr(ci, "_agora", lambda: iso)


# ── (a) e (b): cada origem sozinha ───────────────────────────────────────

class TestOrigens:
    def test_so_upload(self):
        ci.gravar(TENANT, [_cliente("c-001", dias=30, uso=0, email="a@b.co")])
        (linha,) = iu.clientes_em_risco(TENANT)
        assert linha["origem"] == "upload"
        assert linha["customer_id_externo"] == "c-001"
        assert linha["risk_score"] == 1.0
        assert linha["email"] == "a@b.co"
        assert linha["atualizado_em"] == linha["importado_em"]
        assert linha["evento"] is None

    def test_so_sdk_com_o_risco_que_o_ciclo_gravou(self):
        _ciclo("user:c-002", 0.90, "critico", event="Cancellation Page Viewed",
               dias=3, uso=8, mrr=450.0)
        (linha,) = iu.clientes_em_risco(TENANT)
        assert linha["origem"] == "sdk"
        assert linha["customer_id_externo"] == "c-002"          # `user:` removido
        assert linha["risk_score"] == 0.90                      # gravado, não recalculado
        assert linha["criticality"] == "critico"
        assert linha["evento"] == "Cancellation Page Viewed"
        assert linha["explicacao"].startswith("último evento: Cancellation Page Viewed;")
        assert "sem login há 3 dias" in linha["explicacao"]
        assert "R$ 450,00" in linha["explicacao"]
        assert linha["email"] is None
        assert linha["importado_em"] is None
        assert linha["atualizado_em"]

    def test_anonimo_do_sdk_fica_com_o_id_qualificado(self):
        _ciclo("anon:visitante-7", 0.75, "alto", event="Downgrade Clicked")
        (linha,) = iu.clientes_em_risco(TENANT)
        assert linha["customer_id_externo"] == "anon:visitante-7"
        assert linha["origem"] == "sdk"

    def test_prefixo_e_o_mesmo_do_app(self):
        from crai.api import app as app_module
        assert iu.PREFIXO_IDENTIFICADO == app_module.PREFIXO_IDENTIFICADO


# ── (c) fusão por customer_id ────────────────────────────────────────────

class TestFusao:
    def test_nos_dois_aparece_uma_vez_e_o_sdk_mais_recente_vence(self, monkeypatch):
        _com_relogio_upload(monkeypatch, "2026-09-01T10:00:00+00:00")
        ci.gravar(TENANT, [_cliente("c-001", dias=47, uso=1, mrr=1500.0, email="fin@c1.co")])
        _com_relogio(monkeypatch, "2026-09-07T10:00:00+00:00")
        _ciclo("user:c-001", 0.283, "padrao", dias=7, uso=3, mrr=1500.0)

        ranking = iu.clientes_em_risco(TENANT)
        assert len(ranking) == 1
        linha = ranking[0]
        assert linha["origem"] == "sdk"
        assert linha["risk_score"] == 0.283
        assert linha["atualizado_em"] == "2026-09-07T10:00:00+00:00"
        # O e-mail só existe no cadastro e continua valendo — é o mesmo cliente.
        assert linha["email"] == "fin@c1.co"

    def test_upload_mais_recente_vence_o_sdk_antigo(self, monkeypatch):
        _com_relogio(monkeypatch, "2026-08-01T10:00:00+00:00")
        _ciclo("user:c-001", 0.283, "padrao", dias=7, uso=3)
        _com_relogio_upload(monkeypatch, "2026-09-07T10:00:00+00:00")
        ci.gravar(TENANT, [_cliente("c-001", dias=47, uso=1, mrr=1500.0)])

        (linha,) = iu.clientes_em_risco(TENANT)
        assert linha["origem"] == "upload"
        assert linha["risk_score"] == 1.0
        assert linha["atualizado_em"] == "2026-09-07T10:00:00+00:00"

    def test_empate_exato_vai_para_o_sdk(self, monkeypatch):
        mesmo = "2026-09-07T10:00:00+00:00"
        _com_relogio_upload(monkeypatch, mesmo)
        _com_relogio(monkeypatch, mesmo)
        ci.gravar(TENANT, [_cliente("c-001", dias=47, uso=1)])
        _ciclo("user:c-001", 0.1, "padrao", dias=1, uso=9)
        (linha,) = iu.clientes_em_risco(TENANT)
        assert linha["origem"] == "sdk"

    def test_upload_sem_dado_e_sdk_com_dado_o_sdk_vence_e_tira_do_fim(self, monkeypatch):
        """A planilha não tinha atividade (dado_insuficiente), mas o SDK depois
        viu o cliente: agora há dado, e ele sobe para a posição do risco."""
        _com_relogio_upload(monkeypatch, "2026-09-01T10:00:00+00:00")
        ci.gravar(TENANT, [_cliente("c-001", mrr=5000.0), _cliente("c-002", dias=1, uso=9)])
        _com_relogio(monkeypatch, "2026-09-07T10:00:00+00:00")
        _ciclo("user:c-001", 1.0, "critico", dias=30, uso=0, mrr=5000.0)

        ranking = iu.clientes_em_risco(TENANT)
        assert [l["customer_id_externo"] for l in ranking] == ["c-001", "c-002"]
        assert ranking[0]["criticality"] == "critico"

    def test_lista_ordenada_e_sem_dado_por_ultimo_entre_origens(self, monkeypatch):
        ci.gravar(TENANT, [_cliente("u-sem-dado", mrr=9000.0), _cliente("u-morno", dias=7, uso=3)])
        _ciclo("user:s-frio", 1.0, "critico", dias=30, uso=0)
        _ciclo("user:s-ativo", 0.0, "padrao", dias=0, uso=30)
        ranking = iu.clientes_em_risco(TENANT)
        assert [l["customer_id_externo"] for l in ranking] == \
            ["s-frio", "u-morno", "s-ativo", "u-sem-dado"]

    def test_tenant_a_nao_ve_nada_de_tenant_b(self):
        ci.gravar("empresa-a", [_cliente("a-1", dias=1, uso=1)])
        ci.gravar("empresa-b", [_cliente("b-1", dias=1, uso=1)])
        _ciclo("user:a-2", 0.5, "padrao", tenant="empresa-a")
        _ciclo("user:b-2", 0.5, "padrao", tenant="empresa-b")
        assert {l["customer_id_externo"] for l in iu.clientes_em_risco("empresa-a")} == {"a-1", "a-2"}
        assert {l["customer_id_externo"] for l in iu.clientes_em_risco("empresa-b")} == {"b-1", "b-2"}
        assert iu.clientes_em_risco("empresa-c") == []


# ── (d) a leitura no retention_log ───────────────────────────────────────

class TestUltimoCicloPorCliente:
    def test_so_a_linha_mais_recente_de_cada_cliente(self, monkeypatch):
        _com_relogio(monkeypatch, "2026-09-01T10:00:00+00:00")
        _ciclo("user:c-1", 0.2, "padrao", dias=2, uso=9)
        _com_relogio(monkeypatch, "2026-09-05T10:00:00+00:00")
        _ciclo("user:c-1", 0.9, "critico", event="Cancellation Page Viewed")
        _ciclo("user:c-2", 0.1, "padrao")
        linhas = {l["user_id"]: l for l in rl.ultimo_ciclo_por_cliente(TENANT)}
        assert set(linhas) == {"user:c-1", "user:c-2"}
        assert linhas["user:c-1"]["risk_score"] == 0.9
        assert linhas["user:c-1"]["registrado_em"] == "2026-09-05T10:00:00+00:00"
        assert linhas["user:c-1"]["event"] == "Cancellation Page Viewed"

    def test_mesmo_segundo_vence_a_ultima_gravada(self, monkeypatch):
        _com_relogio(monkeypatch, "2026-09-05T10:00:00+00:00")
        _ciclo("user:c-1", 0.2, "padrao")
        _ciclo("user:c-1", 0.7, "padrao")
        (linha,) = rl.ultimo_ciclo_por_cliente(TENANT)
        assert linha["risk_score"] == 0.7

    def test_so_do_tenant_pedido(self):
        _ciclo("user:c-1", 0.2, "padrao", tenant="empresa-a")
        _ciclo("user:c-1", 0.9, "critico", tenant="empresa-b")
        (a,) = rl.ultimo_ciclo_por_cliente("empresa-a")
        assert a["risk_score"] == 0.2
        assert rl.ultimo_ciclo_por_cliente("empresa-c") == []

    def test_nunca_levanta(self, monkeypatch):
        def _quebrado():
            raise sqlite3.OperationalError("disco fora")
        monkeypatch.setattr(rl, "_conectar", _quebrado)
        assert rl.ultimo_ciclo_por_cliente(TENANT) == []

    def test_banco_do_sdk_fora_nao_derruba_o_insight_do_upload(self, monkeypatch):
        ci.gravar(TENANT, [_cliente("c-1", dias=30, uso=0)])
        monkeypatch.setattr(rl, "ultimo_ciclo_por_cliente", lambda t: [])
        (linha,) = iu.clientes_em_risco(TENANT)
        assert linha["origem"] == "upload"


# ── (e) filtros ──────────────────────────────────────────────────────────

class TestFiltros:
    @pytest.fixture
    def ranking(self):
        ci.gravar(TENANT, [
            _cliente("critico", dias=47, uso=1, mrr=100.0),
            _cliente("alto", dias=30, uso=5, mrr=100.0),          # 0.7 → padrao
            _cliente("critico-valor", dias=2, uso=12, mrr=2500.0),
            _cliente("padrao", dias=1, uso=9, mrr=100.0),
            _cliente("sem-dado", mrr=100.0),
        ])
        ci.gravar(TENANT, [_cliente("alto-real", dias=33, uso=5, mrr=100.0)])  # 0.77 → alto
        return iu.clientes_em_risco(TENANT)

    def test_criticidade_minima_alto(self, ranking):
        ids = {l["customer_id_externo"] for l in iu.filtrar(ranking, criticidade_minima="alto")}
        assert ids == {"critico", "critico-valor", "alto-real"}

    def test_criticidade_minima_critico(self, ranking):
        ids = {l["customer_id_externo"] for l in iu.filtrar(ranking, criticidade_minima="critico")}
        assert ids == {"critico", "critico-valor"}

    def test_sem_dado_nunca_passa_no_filtro_de_criticidade(self, ranking):
        for crit in ("alto", "critico"):
            assert all(l["criticality"] != "dado_insuficiente"
                       for l in iu.filtrar(ranking, criticidade_minima=crit))
        assert any(l["criticality"] == "dado_insuficiente" for l in iu.filtrar(ranking))

    def test_limite_corta_depois_do_filtro(self, ranking):
        top = iu.filtrar(ranking, limite=2)
        assert len(top) == 2
        assert top[0]["risk_score"] >= top[1]["risk_score"]
        so_um = iu.filtrar(ranking, limite=1, criticidade_minima="alto")
        assert len(so_um) == 1 and so_um[0]["criticality"] in ("critico", "alto")

    def test_ordem_e_a_do_batch_scoring(self, ranking):
        assert ranking == bs.ordenar(ranking)

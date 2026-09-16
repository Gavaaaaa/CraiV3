"""tests/test_painel_importar_substitui.py — a base de demonstração não acumula.

`POST /simulate/painel/importar` grava sempre em `TENANT_PAINEL`. Com o upsert
puro, cada base de exemplo enviada se somava à anterior (diário + mensal +
saudável = 1.496 clientes, e a base saudável saía com dezenas de críticos),
o que inviabilizava a demonstração. A rota agora apaga o tenant do painel
antes de importar (`clientes_importados.apagar_tenant`).

O que é verificado:

  (a) duas bases de exemplo em sequência pela rota de demonstração: a
      contagem final é a da segunda, não a soma; nenhum id só da primeira sobra;
  (b) a base saudável, importada depois das outras duas, sai sem nenhum
      crítico ou alto em `/simulate/painel/insights`;
  (c) `apagar_tenant` só apaga o tenant pedido;
  (d) a rota autenticada `/clientes/importar` continua ACUMULANDO: uma
      planilha nova atualiza, não substitui.
  (e) os ciclos de retenção do tenant do painel são apagados junto com a
      base: importar → lote → importar → lote trata os mesmos clientes na
      segunda rodada, em vez de devolver todos como `ciclo_aberto`. Só o
      tenant do painel, e só nessa rota.

ISOLAMENTO: `conftest` aponta `CRAI_CLIENTES_DB` para `tmp_path`.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.api.app import TENANT_PAINEL
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import retention_log as rl

EXEMPLOS = Path(__file__).resolve().parents[2] / "painel" / "exemplos"


@pytest.fixture(autouse=True)
def ambiente_de_demo(monkeypatch):
    monkeypatch.setenv("ENV", "development")


@pytest.fixture
def cliente():
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c


def _importar_exemplo(cliente, nome):
    with open(EXEMPLOS / nome, "rb") as f:
        r = cliente.post("/simulate/painel/importar",
                         files={"arquivo": (nome, f, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def _ids(tenant_id):
    return {l["customer_id_externo"] for l in ci.listar(tenant_id)}


class TestRotaDeDemonstracaoSubstitui:
    def test_segunda_base_substitui_a_primeira(self, cliente):
        r1 = _importar_exemplo(cliente, "base_uso_diario.csv")
        assert r1["importados"] == 500
        assert ci.contar(TENANT_PAINEL) == 500
        ids_da_primeira = _ids(TENANT_PAINEL)

        r2 = _importar_exemplo(cliente, "base_uso_mensal.csv")
        assert r2["importados"] == 500
        # a contagem é a da segunda base, não 500 + 500 (menos as 4 âncoras em comum)
        assert ci.contar(TENANT_PAINEL) == 500
        ids_da_segunda = _ids(TENANT_PAINEL)
        so_da_primeira = {i for i in ids_da_primeira if i.startswith("uso-diario-")}
        assert so_da_primeira and not (so_da_primeira & ids_da_segunda)
        assert any(i.startswith("uso-mensal-") for i in ids_da_segunda)

    def test_saudavel_depois_das_outras_sai_sem_ninguem_em_risco(self, cliente):
        _importar_exemplo(cliente, "base_uso_diario.csv")
        _importar_exemplo(cliente, "base_uso_mensal.csv")
        _importar_exemplo(cliente, "base_saudavel.csv")

        r = cliente.get("/simulate/painel/insights")
        assert r.status_code == 200
        dados = r.json()
        assert dados["total_clientes"] == 500
        criticidades = {l["criticality"] for l in dados["clientes_em_risco"]}
        assert "critico" not in criticidades and "alto" not in criticidades
        assert all(l["customer_id_externo"].startswith("saudavel-")
                   for l in dados["clientes_em_risco"])

    def test_insights_reflete_so_a_ultima_base(self, cliente):
        _importar_exemplo(cliente, "base_uso_mensal.csv")
        _importar_exemplo(cliente, "base_uso_diario.csv")
        r = cliente.get("/simulate/painel/insights").json()
        assert r["total_clientes"] == 500
        ancoras = {l["customer_id_externo"]: l["criticality"]
                   for l in r["clientes_em_risco"]
                   if l["customer_id_externo"].startswith("ANCORA")}
        # na base de uso diário as quatro âncoras são críticas
        assert ancoras == {"ANCORA-07-A": "critico", "ANCORA-07-B": "critico",
                           "ANCORA-20-A": "critico", "ANCORA-20-B": "critico"}


class TestApagarTenant:
    def test_apaga_so_o_tenant_pedido(self):
        linha = {"customer_id_externo": "c1", "mrr": 100.0, "billing_profile": "CLT"}
        ci.gravar("empresa_a", [linha, {**linha, "customer_id_externo": "c2"}])
        ci.gravar("empresa_b", [linha])
        assert ci.apagar_tenant("empresa_a") == 2
        assert ci.contar("empresa_a") == 0
        assert ci.contar("empresa_b") == 1

    def test_tenant_vazio_devolve_zero(self):
        assert ci.apagar_tenant("ninguem") == 0


class TestRotaAutenticadaContinuaAcumulando:
    def test_clientes_importar_faz_upsert_sem_apagar(self, cliente, supabase_falso):
        """Duas planilhas com ids diferentes pela rota autenticada: as duas
        ficam. A substituição é só da rota de demonstração."""
        headers = supabase_falso.bearer("empresa-x")
        csv1 = b"customer_id_externo;mrr;billing_profile\na1;100;CLT\na2;200;PJ\n"
        csv2 = b"customer_id_externo;mrr;billing_profile\nb1;300;CLT\n"
        for nome, csv in (("um.csv", csv1), ("dois.csv", csv2)):
            r = cliente.post("/clientes/importar", headers=headers,
                             files={"arquivo": (nome, csv, "text/csv")})
            assert r.status_code == 200, r.text
        assert ci.contar("empresa-x") == 3


class TestCiclosLimposJunto:
    def _lote(self, cliente):
        r = cliente.post("/simulate/painel/disparo-lote", json={})
        assert r.status_code == 200, r.text
        return r.json()

    def test_segunda_rodada_trata_os_mesmos_clientes(self, cliente):
        _importar_exemplo(cliente, "base_uso_diario.csv")
        primeira = self._lote(cliente)
        assert primeira["resumo"]["processados"] > 0
        tratados_1 = {c["customer_id_externo"] for c in primeira["clientes"]}

        # sem reimportar, a regra de não contatar duas vezes vale
        repetida = self._lote(cliente)
        assert repetida["resumo"]["processados"] == 0
        assert repetida["resumo"]["por_motivo"].get("ciclo_aberto") == len(tratados_1)

        # reimportar limpa a base E os ciclos do tenant do painel
        _importar_exemplo(cliente, "base_uso_diario.csv")
        segunda = self._lote(cliente)
        assert segunda["resumo"]["processados"] == len(tratados_1)
        assert {c["customer_id_externo"] for c in segunda["clientes"]} == tratados_1
        assert "ciclo_aberto" not in segunda["resumo"]["por_motivo"]

    def test_reimportar_tira_a_origem_sdk_do_insights(self, cliente):
        _importar_exemplo(cliente, "base_uso_diario.csv")
        self._lote(cliente)
        com_ciclos = cliente.get("/simulate/painel/insights").json()
        assert any(l["origem"] == "sdk" for l in com_ciclos["clientes_em_risco"])
        _importar_exemplo(cliente, "base_uso_diario.csv")
        limpo = cliente.get("/simulate/painel/insights").json()
        assert all(l["origem"] == "upload" for l in limpo["clientes_em_risco"])

    def test_apagar_tenant_do_log_so_apaga_o_tenant_pedido(self):
        def ciclo(tenant, uid):
            return {"tenant_id": tenant, "user_id": uid, "event": "Disparo em lote",
                    "props": {}, "risk_score": 0.9, "profile": "CLT", "criticality": "critico",
                    "offer_type": "desconto_20", "channel": "email", "offer_sent": True,
                    "accepted": None, "retained": False}
        assert rl.registrar_ciclo(ciclo("empresa_a", "user:a1")) is not None
        assert rl.registrar_ciclo(ciclo("empresa_a", "user:a2")) is not None
        assert rl.registrar_ciclo(ciclo("empresa_b", "user:b1")) is not None
        assert rl.apagar_tenant("empresa_a") == 2
        assert rl.ultimo_ciclo_por_cliente("empresa_a") == []
        assert len(rl.ultimo_ciclo_por_cliente("empresa_b")) == 1
        assert rl.apagar_tenant("ninguem") == 0

    def test_rota_autenticada_nao_apaga_ciclos(self, cliente, supabase_falso):
        headers = supabase_falso.bearer("empresa-x")
        assert rl.registrar_ciclo({"tenant_id": "empresa-x", "user_id": "user:x1",
                                   "event": "Disparo em lote", "props": {}, "risk_score": 0.9,
                                   "profile": "CLT", "criticality": "critico",
                                   "offer_type": "desconto_20", "channel": "email",
                                   "offer_sent": True, "accepted": None, "retained": False}) is not None
        csv = b"customer_id_externo;mrr;billing_profile\nx1;100;CLT\n"
        r = cliente.post("/clientes/importar", headers=headers,
                         files={"arquivo": ("um.csv", csv, "text/csv")})
        assert r.status_code == 200, r.text
        assert len(rl.ultimo_ciclo_por_cliente("empresa-x")) == 1

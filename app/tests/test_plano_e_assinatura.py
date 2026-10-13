"""O plano no voluntário e a assinatura no token (10/10/2026).

- O churn voluntário é só do Premium: as rotas do painel do voluntário e a
  simulação de retenção recusam o plano essencial (o Standard do site) com 403
  `plano_sem_voluntario`. Antes, só a tela bloqueava.
- `/insights` continua aberto aos dois planos (o ranking da base é o funil
  gratuito), e o involuntário não muda para o essencial.
- A claim `assinatura` do hook do Supabase: `pendente` recusa toda rota de login
  com 403 `assinatura_pendente`; `ativa`, `dispensada` ou sem a claim, nada muda.
"""

import pytest
from fastapi.testclient import TestClient

from crai import config
from crai.api import app as app_module
from crai.api import datas

A = "empresa-a"
ROTAS_DO_VOLUNTARIO = ("/clientes/recentes", "/clientes/base", "/metrics/voluntario/mes",
                       "/metrics/voluntario/serie", "/metrics/voluntario/regua-x-modelo")
ROTAS_DOS_DOIS_PLANOS = ("/ciclos", "/metrics/involuntario/mes", "/metrics/visao-geral",
                         "/configuracao", "/insights")
RETENCAO = {"nome": "Cliente de teste", "mrr": 300.0,
            "sinais": {"uso_caiu": True, "tickets": False, "atraso": False,
                       "abriu_cancelamento": False},
            "propensao": {}}


@pytest.fixture
def cliente(monkeypatch, supabase_falso):
    monkeypatch.setenv(datas.ENV_FUSO, "America/Sao_Paulo")
    monkeypatch.delenv(config.ENV_SUCCESS_FEE, raising=False)
    monkeypatch.delenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, raising=False)
    with TestClient(app_module.app) as c:
        c.projeto = supabase_falso
        yield c


def _h(c, plano=None, **claims):
    extras = dict(claims)
    if plano is not None:
        extras["plano"] = plano
    return c.projeto.bearer(A, papel="owner", **extras)


class TestVoluntarioSoNoPremium:
    @pytest.mark.parametrize("rota", ROTAS_DO_VOLUNTARIO)
    def test_essencial_recebe_403_plano_sem_voluntario(self, cliente, rota):
        r = cliente.get(rota, headers=_h(cliente, plano="essencial"))
        assert r.status_code == 403
        assert r.json()["detail"]["motivo"] == "plano_sem_voluntario"

    @pytest.mark.parametrize("rota", ROTAS_DO_VOLUNTARIO)
    def test_sem_a_claim_de_plano_vale_essencial(self, cliente, rota):
        r = cliente.get(rota, headers=_h(cliente))
        assert r.status_code == 403
        assert r.json()["detail"]["motivo"] == "plano_sem_voluntario"

    @pytest.mark.parametrize("rota", ROTAS_DO_VOLUNTARIO)
    def test_premium_entra(self, cliente, rota):
        assert cliente.get(rota, headers=_h(cliente, plano="premium")).status_code == 200

    def test_simular_retencao_no_essencial_e_403(self, cliente):
        r = cliente.post("/simulacao/retencao", json=RETENCAO, headers=_h(cliente, plano="essencial"))
        assert r.status_code == 403
        assert r.json()["detail"]["motivo"] == "plano_sem_voluntario"

    @pytest.mark.parametrize("rota", ROTAS_DOS_DOIS_PLANOS)
    def test_o_essencial_continua_com_o_involuntario_e_os_insights(self, cliente, rota):
        assert cliente.get(rota, headers=_h(cliente, plano="essencial")).status_code == 200


class TestAssinatura:
    @pytest.mark.parametrize("rota", ROTAS_DOS_DOIS_PLANOS + ROTAS_DO_VOLUNTARIO)
    def test_pendente_recusa_toda_rota_de_login(self, cliente, rota):
        r = cliente.get(rota, headers=_h(cliente, plano="premium", assinatura="pendente"))
        assert r.status_code == 403
        assert r.json()["detail"]["motivo"] == "assinatura_pendente"

    @pytest.mark.parametrize("assinatura", ["ativa", "dispensada"])
    def test_ativa_e_dispensada_entram(self, cliente, assinatura):
        h = _h(cliente, plano="premium", assinatura=assinatura)
        for rota in ROTAS_DOS_DOIS_PLANOS + ROTAS_DO_VOLUNTARIO:
            assert cliente.get(rota, headers=h).status_code == 200, rota

    def test_sem_a_claim_de_assinatura_nada_muda(self, cliente):
        assert cliente.get("/ciclos", headers=_h(cliente, plano="premium")).status_code == 200

    def test_pendente_tambem_no_post(self, cliente):
        r = cliente.post("/simulacao/retencao", json=RETENCAO,
                         headers=_h(cliente, plano="premium", assinatura="pendente"))
        assert r.status_code == 403
        assert r.json()["detail"]["motivo"] == "assinatura_pendente"

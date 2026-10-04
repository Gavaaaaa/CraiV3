"""tests/test_cors.py — Etapa 2, Bloco 4: CORS.

O QUE ESTE ARQUIVO MEDE:
  - a lista de origens é EXPLÍCITA: `CRAI_CORS_ORIGENS` (separada por vírgula),
    vazia por padrão; `*` é recusado; só em `ENV=development` entra
    `http://localhost:5173` (o dashboard em desenvolvimento);
  - sem origem nenhuma, o serviço não instala o middleware — nada muda para
    quem não configurou;
  - com origem permitida, o navegador recebe o que precisa para as rotas do
    dashboard (GET, POST, PUT, com `Authorization`); origem fora da lista não
    recebe `Access-Control-Allow-Origin`; credenciais (cookies) nunca;
  - o serviço de verdade (subprocesso) sobe com as origens da env.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crai.api import app as app_module

APP = Path(__file__).resolve().parents[1]
DASHBOARD = "http://localhost:5173"
SITE = "https://painel.exemplo.com.br"


@pytest.fixture(autouse=True)
def env_limpa(monkeypatch):
    monkeypatch.delenv("CRAI_CORS_ORIGENS", raising=False)
    monkeypatch.setenv("ENV", "production")


def _mini_app():
    aplicacao = FastAPI()

    @aplicacao.get("/configuracao")
    async def ler():
        return {"ok": True}

    @aplicacao.put("/configuracao")
    async def gravar():
        return {"ok": True}

    origens = app_module.instalar_cors(aplicacao)
    return TestClient(aplicacao), origens


def _preflight(cliente, origem, metodo="PUT"):
    return cliente.options("/configuracao", headers={
        "Origin": origem, "Access-Control-Request-Method": metodo,
        "Access-Control-Request-Headers": "authorization,content-type"})


# ── A lista de origens ───────────────────────────────────────────────────

class TestOrigens:

    def test_vazia_por_padrao(self):
        assert app_module.origens_cors() == []

    @pytest.mark.parametrize("env", ["production", "demo", "", "staging"])
    def test_fora_de_development_o_dashboard_local_nao_entra(self, monkeypatch, env):
        monkeypatch.setenv("ENV", env)
        assert app_module.origens_cors() == []

    def test_sem_env_definida_vale_producao(self, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        assert app_module.origens_cors() == []

    def test_em_development_entra_o_dashboard_local(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        assert app_module.origens_cors() == [DASHBOARD]
        monkeypatch.setenv("CRAI_CORS_ORIGENS", f"{SITE}, {DASHBOARD}")
        assert app_module.origens_cors() == [SITE, DASHBOARD]      # sem repetir

    def test_lista_separada_por_virgula_sem_espaco_barra_final_nem_repeticao(self, monkeypatch):
        monkeypatch.setenv("CRAI_CORS_ORIGENS",
                           f" {SITE}/ ,https://outro.exemplo.com.br,,{SITE}, ")
        assert app_module.origens_cors() == [SITE, "https://outro.exemplo.com.br"]

    @pytest.mark.parametrize("bruto", ["*", f"{SITE},*", "painel.exemplo.com.br",
                                       "ftp://painel.exemplo.com.br",
                                       "https://*.exemplo.com.br",
                                       f"{SITE}/caminho"])
    def test_curinga_e_origem_malformada_sao_recusados_com_log(self, monkeypatch, caplog, bruto):
        monkeypatch.setenv("CRAI_CORS_ORIGENS", bruto)
        with caplog.at_level(logging.ERROR, logger=app_module.__name__):
            origens = app_module.origens_cors()
        assert origens == ([SITE] if bruto.startswith(f"{SITE},") else [])
        assert any("[CORS]" in r.getMessage() for r in caplog.records)


# ── O middleware ─────────────────────────────────────────────────────────

class TestMiddleware:

    def test_sem_origem_o_middleware_nao_e_instalado(self):
        aplicacao = FastAPI()
        assert app_module.instalar_cors(aplicacao) == []
        assert aplicacao.user_middleware == []

    def test_origem_permitida_recebe_o_preflight_das_rotas_do_dashboard(self, monkeypatch):
        monkeypatch.setenv("CRAI_CORS_ORIGENS", SITE)
        cliente, origens = _mini_app()
        assert origens == [SITE]
        for metodo in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            r = _preflight(cliente, SITE, metodo)
            assert r.status_code == 200, (metodo, r.text)
            assert r.headers["access-control-allow-origin"] == SITE
            assert metodo in r.headers["access-control-allow-methods"]
        permitidos = r.headers["access-control-allow-headers"].lower()
        assert "authorization" in permitidos and "content-type" in permitidos
        assert "idempotency-key" in permitidos

    def test_resposta_comum_traz_a_origem_e_expoe_o_retry_after(self, monkeypatch):
        monkeypatch.setenv("CRAI_CORS_ORIGENS", SITE)
        cliente, _ = _mini_app()
        r = cliente.get("/configuracao", headers={"Origin": SITE})
        assert r.status_code == 200
        assert r.headers["access-control-allow-origin"] == SITE
        assert "retry-after" in r.headers["access-control-expose-headers"].lower()

    def test_origem_fora_da_lista_nao_recebe_permissao(self, monkeypatch):
        monkeypatch.setenv("CRAI_CORS_ORIGENS", SITE)
        cliente, _ = _mini_app()
        for origem in ("https://atacante.exemplo.com", DASHBOARD, SITE + ".atacante.com",
                       SITE.replace("https", "http")):
            assert "access-control-allow-origin" not in _preflight(cliente, origem).headers
            r = cliente.get("/configuracao", headers={"Origin": origem})
            assert "access-control-allow-origin" not in r.headers

    def test_nunca_com_credenciais_nem_curinga(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        cliente, origens = _mini_app()
        assert origens == [DASHBOARD]
        for r in (_preflight(cliente, DASHBOARD),
                  cliente.get("/configuracao", headers={"Origin": DASHBOARD})):
            assert r.headers["access-control-allow-origin"] == DASHBOARD
            assert "access-control-allow-credentials" not in r.headers

    def test_metodo_fora_da_lista_e_recusado_no_preflight(self, monkeypatch):
        monkeypatch.setenv("CRAI_CORS_ORIGENS", SITE)
        cliente, _ = _mini_app()
        assert _preflight(cliente, SITE, "TRACE").status_code == 400


# ── O serviço de verdade ─────────────────────────────────────────────────

def _origens_do_servico(env: str, origens: str) -> str:
    codigo = "from crai.api import app as m; print('CORS=' + repr(m.CORS_ORIGENS))"
    ambiente = {**os.environ, "ENV": env, "CRAI_CORS_ORIGENS": origens, "CRAI_RELOGIO": "0"}
    r = subprocess.run([sys.executable, "-c", codigo], cwd=APP, env=ambiente,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return [l for l in r.stdout.splitlines() if l.startswith("CORS=")][-1]


class TestOServicoDeVerdade:

    def test_sobe_com_as_origens_da_env(self):
        assert _origens_do_servico("development", SITE) == f"CORS={[SITE, DASHBOARD]!r}"
        assert _origens_do_servico("production", "") == "CORS=[]"

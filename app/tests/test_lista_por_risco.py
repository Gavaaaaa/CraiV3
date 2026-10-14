"""A lista de clientes por risco (14/10/2026).

Uma base que entra inteira por anexo tem a mesma data em todos os clientes:
"os 10 mais recentes" ali era um sorteio, e a tela dizia "Grave 0" com 38
graves na base. `GET /clientes/recentes?ordem=risco` devolve a ordem do
ranking, `?faixa=` filtra a base INTEIRA antes do corte, `?deslocamento=` pagina,
e a resposta traz `contagem_por_faixa` e `total_filtrado`. Sem os parâmetros
novos, a rota responde como antes (os testes de `test_voluntario_api` seguem).
"""

import pytest
from fastapi.testclient import TestClient

from crai import config
from crai.api import app as app_module
from crai.api import datas
from crai.churn_voluntary import clientes_importados as ci

A = "empresa-a"
FAIXAS = ("grave", "preocupante", "sem_risco", "sem_dado")
ORDEM = {f: i for i, f in enumerate(FAIXAS)}


def _c(cid, dias=None, uso=None, mrr=300.0):
    return {"customer_id_externo": cid, "mrr": mrr, "billing_profile": "PJ",
            "days_since_last": dias, "features_used_30d": uso}


@pytest.fixture
def cliente(monkeypatch, supabase_falso):
    monkeypatch.setenv(datas.ENV_FUSO, "America/Sao_Paulo")
    monkeypatch.delenv(config.ENV_SUCCESS_FEE, raising=False)
    monkeypatch.delenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, raising=False)
    with TestClient(app_module.app) as c:
        c.projeto = supabase_falso
        yield c


@pytest.fixture
def base(cliente):
    """40 clientes de uma vez (como um anexo): 30 com uso variado e 10 sem dado.
    Os sem dado vêm por último no arquivo, como na base de teste do dashboard."""
    linhas = [_c(f"c{i:02d}", dias=(i * 2) % 61, uso=max(0, 15 - i // 2)) for i in range(30)]
    linhas += [_c(f"z{i:02d}") for i in range(10)]
    ci.gravar(A, linhas)
    return cliente


def _get(c, **params):
    r = c.get("/clientes/recentes", params=params,
              headers=c.projeto.bearer(A, papel="owner", plano="premium"))
    assert r.status_code == 200, r.text
    return r.json()


class TestOrdemPorRisco:
    def test_sem_parametros_e_como_antes_e_a_contagem_vem_junto(self, base):
        r = _get(base)
        assert len(r["clientes"]) == 10 and r["total_na_base"] == 40
        assert sum(r["contagem_por_faixa"].values()) == 40
        assert r["contagem_por_faixa"]["sem_dado"] == 10
        assert r["total_filtrado"] == 40

    def test_por_risco_os_graves_vem_primeiro_e_o_sem_dado_por_ultimo(self, base):
        r = _get(base, ordem="risco", limite=100)
        faixas = [c["faixa"] for c in r["clientes"]]
        assert faixas == sorted(faixas, key=ORDEM.get)
        assert faixas[0] == "grave" and faixas[-1] == "sem_dado"
        # Dentro de cada faixa com risco, a posição no ranking cresce.
        for f in ("grave", "preocupante", "sem_risco"):
            pos = [c["posicao_no_ranking"] for c in r["clientes"] if c["faixa"] == f]
            assert pos == sorted(pos)

    def test_a_contagem_e_a_mesma_dos_cartoes_do_mes(self, base):
        r = _get(base, ordem="risco")
        mes = base.get("/metrics/voluntario/mes",
                       headers=base.projeto.bearer(A, papel="owner", plano="premium")).json()
        assert r["contagem_por_faixa"]["grave"] == mes["grave"]
        assert r["contagem_por_faixa"]["preocupante"] == mes["preocupante"]


class TestFiltroEPagina:
    @pytest.mark.parametrize("faixa", FAIXAS)
    def test_o_filtro_olha_a_base_inteira(self, base, faixa):
        r = _get(base, ordem="risco", faixa=faixa, limite=100)
        assert all(c["faixa"] == faixa for c in r["clientes"])
        assert r["total_filtrado"] == r["contagem_por_faixa"][faixa] == len(r["clientes"])

    def test_paginas_seguidas_formam_a_lista_inteira_sem_repetir(self, base):
        inteira = [c["id"] for c in _get(base, ordem="risco", limite=100)["clientes"]]
        paginas = []
        for d in range(0, 40, 15):
            paginas += [c["id"] for c in _get(base, ordem="risco", limite=15, deslocamento=d)["clientes"]]
        assert paginas == inteira and len(set(paginas)) == 40

    def test_deslocamento_alem_do_fim_devolve_vazio(self, base):
        r = _get(base, ordem="risco", deslocamento=500)
        assert r["clientes"] == [] and r["total_filtrado"] == 40


class TestValores:
    @pytest.mark.parametrize("params,campo", [
        ({"ordem": "alfabetica"}, "ordem"), ({"faixa": "alto"}, "faixa"),
        ({"deslocamento": -1}, "deslocamento")])
    def test_valor_fora_do_vocabulario_e_422(self, base, params, campo):
        r = base.get("/clientes/recentes", params=params,
                     headers=base.projeto.bearer(A, papel="owner", plano="premium"))
        assert r.status_code == 422
        assert r.json()["detail"]["campo"] == campo

    def test_continua_so_no_premium(self, base):
        r = base.get("/clientes/recentes", params={"ordem": "risco"},
                     headers=base.projeto.bearer(A, papel="owner", plano="essencial"))
        assert r.status_code == 403

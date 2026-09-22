"""tests/test_clientes_api.py — a API de sincronização de clientes, contra o app real.

O que o Bloco D promete, e o que aqui é verificado (os dez itens de D.6):

   1. cliente criado por `POST /clientes` aparece em `GET /insights` SEM
      upload de planilha;
   2. tenant A nunca enxerga, altera ou apaga cliente de B — nas quatro rotas;
   3. mesma `Idempotency-Key` duas vezes → um cliente só, sem reaplicar;
   4. `POST /clientes/lote` com uma linha torta → `rejeitados` tem um item,
      as outras entram;
   5. `DELETE` grava `cancelado_em`, a linha continua existindo, e o cliente
      sai do ranking de risco;
   6. cancelar duas vezes preserva a primeira data;
   7. `POST /clientes` com `reativar: true` sobre um cancelado limpa
      `cancelado_em`; sem o campo, não limpa;
   8. cliente de outro tenant devolve 404, não 403;
   9. `PATCH` de MRR muda o valor sem tocar nas colunas comportamentais;
  10. todas as respostas trazem `regua_calculada_em` (e `regua_versao`).

Mais: a validação é a MESMA da planilha (`importacao.validar_linha`), o
`motivo` do cancelamento não vai para o log, e sem JWT é 401.

A base vai para um SQLite em `tmp_path` (`conftest`), o Supabase é o
`supabase_falso`, e as janelas de idempotência são zeradas entre testes.

Uso:
    pytest tests/test_clientes_api.py -v
"""

import logging

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.churn_voluntary import batch_scoring
from crai.churn_voluntary import clientes_importados as ci

A, B = "empresa-a", "empresa-b"


def _corpo(cid, mrr=300.0, perfil="PJ", dias=None, uso=None, email=None, **extra):
    c = {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil,
         "days_since_last": dias, "features_used_30d": uso, "email": email}
    c.update(extra)
    return c


@pytest.fixture
def cliente():
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def api(cliente, supabase_falso):
    """Chamadas já autenticadas: `api.post(tenant, caminho, json=...)` etc."""
    class _Api:
        def _h(self, tenant, chave=None):
            h = supabase_falso.bearer(tenant)
            if chave:
                h["Idempotency-Key"] = chave
            return h

        def post(self, tenant, caminho, json, chave=None):
            return cliente.post(caminho, json=json, headers=self._h(tenant, chave))

        def patch(self, tenant, cid, json, chave=None):
            return cliente.patch(f"/clientes/{cid}", json=json, headers=self._h(tenant, chave))

        def delete(self, tenant, cid, json=None, chave=None):
            return cliente.request("DELETE", f"/clientes/{cid}", json=json,
                                   headers=self._h(tenant, chave))

        def insights(self, tenant):
            r = cliente.get("/insights", headers=self._h(tenant))
            assert r.status_code == 200, r.text
            return r.json()

        def ids_em_risco(self, tenant):
            return [l["customer_id_externo"] for l in self.insights(tenant)["clientes_em_risco"]]

    return _Api()


# ── 1. Criado pela API, aparece no /insights sem planilha ────────────────

class TestCriarEAparecer:
    def test_post_cria_e_insights_lista_sem_upload(self, api):
        r = api.post(A, "/clientes", _corpo("c-1", dias=40, uso=0))
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["cliente"]["customer_id_externo"] == "c-1"
        assert corpo["cliente"]["cancelado_em"] is None
        assert corpo["cliente"]["importado_em"].endswith("+00:00")
        assert "tenant_id" not in corpo["cliente"]
        assert corpo["reenvio"] is False
        assert "c-1" in api.ids_em_risco(A)

    def test_post_existente_atualiza_a_foto(self, api):
        api.post(A, "/clientes", _corpo("c-1", mrr=100.0, dias=1, uso=9))
        r = api.post(A, "/clientes", _corpo("c-1", mrr=250.0, dias=2, uso=8))
        assert r.status_code == 200
        assert r.json()["cliente"]["mrr"] == 250.0
        assert ci.contar(A) == 1

    def test_sem_jwt_e_401_nas_quatro(self, cliente, supabase_falso):
        assert cliente.post("/clientes", json=_corpo("c")).status_code == 401
        assert cliente.post("/clientes/lote", json={"clientes": []}).status_code == 401
        assert cliente.patch("/clientes/c", json={"mrr": 1}).status_code == 401
        assert cliente.request("DELETE", "/clientes/c").status_code == 401


# ── Validação: o mesmo crivo da planilha ─────────────────────────────────

class TestValidacaoReusada:
    @pytest.mark.parametrize("corpo, trecho", [
        (_corpo("", mrr=1), "customer_id_externo vazio"),
        (_corpo("com espaco", mrr=1), "espaços"),
        (_corpo("c", mrr="abc"), "mrr 'abc'"),
        (_corpo("c", mrr=-1), "mrr"),
        (_corpo("c", mrr=None), "mrr vazio"),
        (_corpo("c", perfil="MEI"), "billing_profile 'MEI'"),
        (_corpo("c", dias="ontem"), "days_since_last 'ontem'"),
        (_corpo("c", email="sem-arroba"), "email 'sem-arroba'"),
    ])
    def test_cliente_invalido_e_422_com_o_motivo_da_planilha(self, api, corpo, trecho):
        r = api.post(A, "/clientes", corpo)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["motivo"] == "cliente_invalido"
        assert trecho in r.json()["detail"]["detalhe"]
        assert ci.contar(A, incluir_cancelados=True) == 0

    def test_mrr_em_pt_br_e_aceito_como_na_planilha(self, api):
        r = api.post(A, "/clientes", _corpo("c", mrr="R$ 1.234,56", perfil="pj"))
        assert r.status_code == 200, r.text
        assert r.json()["cliente"]["mrr"] == 1234.56
        assert r.json()["cliente"]["billing_profile"] == "PJ"

    def test_campo_desconhecido_e_422(self, api):
        r = api.post(A, "/clientes", _corpo("c", nome="Fulano"))
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "campo_desconhecido"
        assert r.json()["detail"]["campo"] == "nome"

    def test_reativar_nao_booleano_e_422(self, api):
        r = api.post(A, "/clientes", _corpo("c", reativar="sim"))
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "reativar_invalido"

    def test_corpo_que_nao_e_objeto_e_422(self, api):
        r = api.post(A, "/clientes", [1, 2])
        assert r.status_code == 422


# ── 2 e 8. Isolamento por tenant: 404, nunca 403 ─────────────────────────

class TestIsolamentoPorTenant:
    @pytest.fixture(autouse=True)
    def _cliente_de_b(self, api):
        assert api.post(B, "/clientes", _corpo("de-b", mrr=500.0, dias=30, uso=0)).status_code == 200

    def test_a_nao_enxerga_cliente_de_b_no_insights(self, api):
        assert "de-b" not in api.ids_em_risco(A)
        assert "de-b" in api.ids_em_risco(B)

    def test_patch_de_a_em_cliente_de_b_e_404_e_nao_muda_nada(self, api):
        r = api.patch(A, "de-b", {"mrr": 1.0})
        assert r.status_code == 404
        assert r.json()["detail"]["motivo"] == "cliente_nao_encontrado"
        assert ci.obter(B, "de-b")["mrr"] == 500.0

    def test_delete_de_a_em_cliente_de_b_e_404_e_nao_cancela(self, api):
        r = api.delete(A, "de-b", {"motivo": "x"})
        assert r.status_code == 404
        assert ci.obter(B, "de-b")["cancelado_em"] is None

    def test_404_de_outro_tenant_e_identico_ao_de_inexistente(self, api):
        de_outro = api.delete(A, "de-b").json()
        inexistente = api.delete(A, "nunca-existiu").json()
        assert de_outro == inexistente
        assert api.patch(A, "de-b", {"mrr": 1}).json() == api.patch(A, "nunca", {"mrr": 1}).json()

    def test_post_de_a_com_o_mesmo_id_cria_outra_linha_nao_toca_a_de_b(self, api):
        r = api.post(A, "/clientes", _corpo("de-b", mrr=1.0))
        assert r.status_code == 200
        assert ci.obter(B, "de-b")["mrr"] == 500.0
        assert ci.obter(A, "de-b")["mrr"] == 1.0

    def test_lote_de_a_nao_toca_b(self, api):
        api.post(A, "/clientes/lote", {"clientes": [_corpo("de-b", mrr=1.0)]})
        assert ci.obter(B, "de-b")["mrr"] == 500.0

    def test_reativar_de_a_nao_reativa_o_de_b(self, api):
        api.delete(B, "de-b")
        api.post(A, "/clientes", _corpo("de-b", reativar=True))
        assert ci.obter(B, "de-b")["cancelado_em"] is not None


# ── 3. Idempotência ──────────────────────────────────────────────────────

class TestIdempotencia:
    def test_mesma_chave_duas_vezes_um_cliente_so_e_sem_reaplicar(self, api):
        r1 = api.post(A, "/clientes", _corpo("c-1", mrr=100.0), chave="k-1")
        r2 = api.post(A, "/clientes", _corpo("c-1", mrr=999.0), chave="k-1")
        assert r1.status_code == r2.status_code == 200
        assert r1.json()["reenvio"] is False
        assert r2.json()["reenvio"] is True
        assert r2.json()["cliente"]["mrr"] == 100.0          # a 2ª não aplicou
        assert ci.contar(A) == 1

    def test_chaves_diferentes_aplicam(self, api):
        api.post(A, "/clientes", _corpo("c-1", mrr=100.0), chave="k-1")
        r = api.post(A, "/clientes", _corpo("c-1", mrr=999.0), chave="k-2")
        assert r.json()["reenvio"] is False
        assert r.json()["cliente"]["mrr"] == 999.0

    def test_sem_header_cada_chamada_aplica(self, api):
        api.post(A, "/clientes", _corpo("c-1", mrr=100.0))
        r = api.post(A, "/clientes", _corpo("c-1", mrr=999.0))
        assert r.json()["cliente"]["mrr"] == 999.0 and ci.contar(A) == 1

    def test_a_mesma_chave_em_outro_tenant_e_outra_requisicao(self, api):
        api.post(A, "/clientes", _corpo("c-1"), chave="k")
        r = api.post(B, "/clientes", _corpo("c-1"), chave="k")
        assert r.json()["reenvio"] is False
        assert ci.contar(B) == 1

    def test_422_nao_consome_a_chave(self, api):
        assert api.post(A, "/clientes", _corpo("c-1", mrr="abc"), chave="k").status_code == 422
        r = api.post(A, "/clientes", _corpo("c-1", mrr=10.0), chave="k")
        assert r.status_code == 200 and r.json()["reenvio"] is False

    def test_patch_e_delete_e_lote_tambem_respeitam_a_chave(self, api):
        api.post(A, "/clientes", _corpo("c-1", mrr=100.0, dias=5, uso=5))
        api.patch(A, "c-1", {"mrr": 200.0}, chave="p")
        r = api.patch(A, "c-1", {"mrr": 300.0}, chave="p")
        assert r.json()["reenvio"] is True and ci.obter(A, "c-1")["mrr"] == 200.0

        r1 = api.delete(A, "c-1", {"motivo": "um"}, chave="d")
        r2 = api.delete(A, "c-1", {"motivo": "dois"}, chave="d")
        assert r2.json()["reenvio"] is True
        assert r2.json()["cliente"]["cancelado_em"] == r1.json()["cliente"]["cancelado_em"]

        api.post(A, "/clientes/lote", {"clientes": [_corpo("l-1")]}, chave="L")
        r = api.post(A, "/clientes/lote", {"clientes": [_corpo("l-1"), _corpo("l-2")]}, chave="L")
        assert r.json()["reenvio"] is True and r.json()["importados"] == 0
        assert ci.obter(A, "l-2") is None


# ── 4. Lote: linha torta vira `rejeitados` ───────────────────────────────

class TestLote:
    def test_uma_linha_torta_rejeitada_as_outras_entram(self, api):
        r = api.post(A, "/clientes/lote", {"clientes": [
            _corpo("ok-1", dias=1, uso=1),
            _corpo("torta", mrr="abc"),
            _corpo("ok-2"),
        ]})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["importados"] == 2
        assert corpo["rejeitados"] == [{"indice": 1, "motivo": "mrr 'abc' não é um número válido"}]
        assert corpo["linhas_sem_dado_comportamental"] == 1      # ok-2
        assert corpo["reenvio"] is False
        assert {c["customer_id_externo"] for c in ci.listar(A)} == {"ok-1", "ok-2"}

    def test_item_que_nao_e_objeto_e_item_com_campo_estranho_sao_rejeitados(self, api):
        r = api.post(A, "/clientes/lote", {"clientes": ["texto", _corpo("x", nome="n"), _corpo("ok")]})
        assert r.json()["importados"] == 1
        motivos = {e["indice"]: e["motivo"] for e in r.json()["rejeitados"]}
        assert "não é um objeto" in motivos[0]
        assert "nome" in motivos[1]

    def test_id_repetido_no_lote_a_ultima_vence(self, api):
        r = api.post(A, "/clientes/lote", {"clientes": [_corpo("c", mrr=1.0), _corpo("c", mrr=2.0)]})
        assert r.json()["importados"] == 1
        assert ci.obter(A, "c")["mrr"] == 2.0

    def test_lote_nao_reativa(self, api):
        api.post(A, "/clientes", _corpo("c-1"))
        api.delete(A, "c-1")
        api.post(A, "/clientes/lote", {"clientes": [_corpo("c-1", mrr=9.0)]})
        linha = ci.obter(A, "c-1")
        assert linha["mrr"] == 9.0 and linha["cancelado_em"] is not None

    def test_lote_vazio_e_422_e_forma_errada_e_422(self, api):
        assert api.post(A, "/clientes/lote", {"clientes": []}).json()["detail"]["motivo"] == "lote_vazio"
        assert api.post(A, "/clientes/lote", {"clientes": {}}).json()["detail"]["motivo"] == "lote_invalido"
        assert api.post(A, "/clientes/lote", {"itens": []}).json()["detail"]["motivo"] == "campo_desconhecido"

    def test_lote_acima_do_maximo_e_413(self, api, monkeypatch):
        from crai.churn_voluntary import importacao
        monkeypatch.setattr(importacao, "LINHAS_MAXIMAS", 2)
        r = api.post(A, "/clientes/lote", {"clientes": [_corpo(f"c{i}") for i in range(3)]})
        assert r.status_code == 413 and r.json()["detail"]["motivo"] == "linhas_demais"


# ── 5 e 6. DELETE é evento de churn ──────────────────────────────────────

class TestCancelamento:
    def test_delete_grava_cancelado_em_a_linha_fica_e_sai_do_ranking(self, api):
        api.post(A, "/clientes", _corpo("frio", dias=45, uso=0))
        api.post(A, "/clientes", _corpo("outro", dias=45, uso=0))
        assert "frio" in api.ids_em_risco(A)

        r = api.delete(A, "frio", {"motivo": "mudou de fornecedor"})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["cliente"]["cancelado_em"].endswith("+00:00")
        assert corpo["cliente"]["motivo_cancelamento"] == "mudou de fornecedor"
        assert corpo["ja_estava_cancelado"] is False

        assert ci.obter(A, "frio") is not None                    # a linha existe
        assert ci.contar(A, incluir_cancelados=True) == 2
        assert "frio" not in api.ids_em_risco(A)                  # saiu do ranking
        assert "outro" in api.ids_em_risco(A)

    def test_cancelar_duas_vezes_preserva_a_primeira_data(self, api, monkeypatch):
        api.post(A, "/clientes", _corpo("c-1"))
        monkeypatch.setattr(ci, "_agora", lambda: "2026-01-01T00:00:00+00:00")
        r1 = api.delete(A, "c-1", {"motivo": "primeiro"})
        monkeypatch.setattr(ci, "_agora", lambda: "2026-02-02T00:00:00+00:00")
        r2 = api.delete(A, "c-1", {"motivo": "segundo"})
        assert r1.status_code == r2.status_code == 200
        assert r2.json()["cliente"]["cancelado_em"] == "2026-01-01T00:00:00+00:00"
        assert r2.json()["cliente"]["motivo_cancelamento"] == "primeiro"
        assert r2.json()["ja_estava_cancelado"] is True

    def test_sem_corpo_cancela_sem_motivo(self, api):
        api.post(A, "/clientes", _corpo("c-1"))
        r = api.delete(A, "c-1")
        assert r.status_code == 200 and r.json()["cliente"]["motivo_cancelamento"] is None

    def test_motivo_longo_e_422_e_nao_cancela(self, api):
        api.post(A, "/clientes", _corpo("c-1"))
        r = api.delete(A, "c-1", {"motivo": "x" * 501})
        assert r.status_code == 422 and r.json()["detail"]["motivo"] == "motivo_longo"
        assert ci.obter(A, "c-1")["cancelado_em"] is None

    def test_motivo_nao_vai_para_o_log(self, api, caplog):
        api.post(A, "/clientes", _corpo("c-1"))
        with caplog.at_level(logging.INFO):
            api.delete(A, "c-1", {"motivo": "SEGREDO-PESSOAL-123"})
        assert "SEGREDO-PESSOAL-123" not in caplog.text

    def test_inexistente_e_404(self, api):
        r = api.delete(A, "nunca")
        assert r.status_code == 404 and r.json()["detail"]["motivo"] == "cliente_nao_encontrado"


# ── 7. Reativar é explícito ──────────────────────────────────────────────

class TestReativar:
    def test_post_com_reativar_true_limpa_e_volta_ao_ranking(self, api):
        api.post(A, "/clientes", _corpo("c-1", dias=45, uso=0))
        api.delete(A, "c-1", {"motivo": "m"})
        assert "c-1" not in api.ids_em_risco(A)
        r = api.post(A, "/clientes", _corpo("c-1", dias=45, uso=0, reativar=True))
        assert r.status_code == 200
        assert r.json()["cliente"]["cancelado_em"] is None
        assert r.json()["cliente"]["motivo_cancelamento"] is None
        assert "c-1" in api.ids_em_risco(A)

    def test_post_sem_reativar_nao_limpa(self, api):
        api.post(A, "/clientes", _corpo("c-1"))
        antes = api.delete(A, "c-1").json()["cliente"]["cancelado_em"]
        r = api.post(A, "/clientes", _corpo("c-1", mrr=5.0))
        assert r.json()["cliente"]["cancelado_em"] == antes
        assert r.json()["cliente"]["mrr"] == 5.0
        r = api.post(A, "/clientes", _corpo("c-1", reativar=False))
        assert r.json()["cliente"]["cancelado_em"] == antes


# ── 9. PATCH parcial ─────────────────────────────────────────────────────

class TestPatch:
    def test_mrr_muda_sem_tocar_nas_comportamentais(self, api):
        api.post(A, "/clientes", _corpo("c-1", mrr=100.0, dias=47, uso=1, email="a@b.co"))
        r = api.patch(A, "c-1", {"mrr": 250.0})
        assert r.status_code == 200, r.text
        c = r.json()["cliente"]
        assert c["mrr"] == 250.0
        assert c["days_since_last"] == 47.0
        assert c["features_used_30d"] == 1.0
        assert c["billing_profile"] == "PJ"
        assert c["email"] == "a@b.co"
        assert c["atualizado_em"].endswith("+00:00")

    def test_patch_valida_com_o_crivo_da_planilha(self, api):
        api.post(A, "/clientes", _corpo("c-1"))
        r = api.patch(A, "c-1", {"billing_profile": "MEI"})
        assert r.status_code == 422 and r.json()["detail"]["motivo"] == "cliente_invalido"
        assert "MEI" in r.json()["detail"]["detalhe"]
        r = api.patch(A, "c-1", {"mrr": -1})
        assert r.status_code == 422
        assert ci.obter(A, "c-1")["atualizado_em"] is None      # nada foi aplicado

    def test_null_limpa_um_opcional(self, api):
        api.post(A, "/clientes", _corpo("c-1", dias=47, uso=1))
        r = api.patch(A, "c-1", {"days_since_last": None})
        assert r.json()["cliente"]["days_since_last"] is None
        assert r.json()["cliente"]["features_used_30d"] == 1.0

    def test_corpo_vazio_e_422(self, api):
        api.post(A, "/clientes", _corpo("c-1"))
        r = api.patch(A, "c-1", {})
        assert r.status_code == 422 and r.json()["detail"]["motivo"] == "corpo_vazio"

    def test_id_no_corpo_e_campo_desconhecido(self, api):
        api.post(A, "/clientes", _corpo("c-1"))
        r = api.patch(A, "c-1", {"customer_id_externo": "c-2"})
        assert r.status_code == 422 and r.json()["detail"]["motivo"] == "campo_desconhecido"
        assert ci.obter(A, "c-1") is not None and ci.obter(A, "c-2") is None

    def test_patch_em_cancelado_atualiza_e_continua_cancelado(self, api):
        api.post(A, "/clientes", _corpo("c-1"))
        api.delete(A, "c-1")
        c = api.patch(A, "c-1", {"mrr": 7.0}).json()["cliente"]
        assert c["mrr"] == 7.0 and c["cancelado_em"] is not None

    def test_inexistente_e_404(self, api):
        assert api.patch(A, "nunca", {"mrr": 1}).status_code == 404


# ── 10. A régua em todas as respostas ────────────────────────────────────

class TestRegua:
    def test_todas_as_respostas_trazem_regua_calculada_em_e_versao(self, api):
        api.post(A, "/clientes", _corpo("seed"))
        respostas = [
            api.post(A, "/clientes", _corpo("c-1")).json(),
            api.post(A, "/clientes/lote", {"clientes": [_corpo("c-2")]}).json(),
            api.patch(A, "c-1", {"mrr": 1.0}).json(),
            api.delete(A, "c-2").json(),
            api.delete(A, "c-2").json(),                                # 2ª vez
            api.post(A, "/clientes", _corpo("c-1"), chave="k").json(),
            api.post(A, "/clientes", _corpo("c-1"), chave="k").json(),  # reenvio
        ]
        for corpo in respostas:
            assert "regua_calculada_em" in corpo, corpo
            assert corpo["regua_versao"] == batch_scoring.REGUA_VERSAO == "lote-v1"

    def test_regua_calculada_em_e_o_ultimo_calculo_em_lote_do_tenant(self, api):
        antes = api.post(A, "/clientes", _corpo("c-1")).json()
        assert antes["regua_calculada_em"] is None            # ninguém pediu o ranking ainda
        api.insights(A)                                       # o cálculo em lote
        depois = api.post(A, "/clientes", _corpo("c-1")).json()
        assert depois["regua_calculada_em"] is not None
        assert depois["regua_calculada_em"].endswith("+00:00")
        assert depois["regua_calculada_em"] == batch_scoring.ultimo_calculo_da_regua(A)
        # O de B continua sem cálculo: a marca é por tenant.
        assert api.post(B, "/clientes", _corpo("x")).json()["regua_calculada_em"] is None

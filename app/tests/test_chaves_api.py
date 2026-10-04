"""tests/test_chaves_api.py — Rodada 2, Fase 1: a chave de API da empresa.

O QUE ESTE ARQUIVO MEDE (o Portão 1, item por item):
  - gerar a chave e, com ela, criar um cliente por `POST /clientes`: o cliente
    aparece no `GET /insights` da mesma empresa;
  - a chave de A nunca enxerga, altera nem cancela cliente de B, nas quatro rotas;
  - revogar e usar de novo: 401 na requisição seguinte;
  - chave revogada, inexistente e malformada: respostas idênticas, byte a byte;
  - a chave não autentica nenhuma rota fora das quatro (o teste percorre TODAS
    as rotas do aplicativo);
  - membro: 403 ao gerar e ao revogar; plano essencial: 403 `plano_sem_api` ao
    GERAR, e só ao gerar (listar e revogar valem em qualquer plano);
  - o banco não tem a chave em texto puro (varredura do arquivo cru);
  - a chave não aparece em nenhum log nem na saída padrão;
  - a sexta chave ativa: 409; acima do limite por minuto: 429 com `Retry-After`;
  - o token de login continua valendo nas quatro rotas, ao lado da chave;
  - a migração (as duas tabelas novas) sobre a cópia de um banco que já existia.

NENHUMA CHAVE DE VERDADE ESTÁ ESCRITA AQUI. Toda chave usada é gerada na hora,
dentro do teste, num banco de `tmp_path`. As "inventadas" são montadas a partir
de letras repetidas, e não autenticam nada.
"""

import hmac
import logging
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crai.accounts import chaves_api, supabase_auth
from crai.api import app as app_module
from crai.api import dev_token, registro_acesso
from crai.churn_voluntary import clientes_importados as ci
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao

A, B = "empresa-a", "empresa-b"

# Visivelmente falsas: o prefixo certo e 43 letras iguais. Nunca foram geradas.
INVENTADA = chaves_api.PREFIXO + "A" * 43
MALFORMADAS = [
    chaves_api.PREFIXO,                           # só o prefixo
    chaves_api.PREFIXO + "curta",                 # curta demais
    chaves_api.PREFIXO + "A" * 44,                # um caractere a mais
    chaves_api.PREFIXO + "A" * 42 + "!",          # caractere fora do alfabeto
    chaves_api.PREFIXO + "A" * 20 + " " + "A" * 22,   # espaço no meio
]

AS_QUATRO = [
    ("POST", "/clientes"),
    ("POST", "/clientes/lote"),
    ("PATCH", "/clientes/{customer_id_externo}"),
    ("DELETE", "/clientes/{customer_id_externo}"),
]


@pytest.fixture(autouse=True)
def limites_zerados():
    """Nenhum teste herda a contagem do limite por minuto de outro."""
    chaves_api.limpar_limites()
    yield
    chaves_api.limpar_limites()


@pytest.fixture
def cliente(supabase_falso):
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        c.projeto = supabase_falso
        yield c


def _login(c, tenant=A, papel="owner", plano="premium") -> dict:
    """O header do token de login, com o papel e o plano dados (None: sem a claim)."""
    claims = {}
    if papel is not None:
        claims["papel"] = papel
    if plano is not None:
        claims["plano"] = plano
    return c.projeto.bearer(tenant, **claims)


def _bearer(chave: str) -> dict:
    return {"Authorization": f"Bearer {chave}"}


def _gerar(c, tenant=A, nome="Sistema de cobrança", papel="owner"):
    r = c.post("/integracao/chaves", json={"nome": nome}, headers=_login(c, tenant, papel))
    assert r.status_code == 201, r.text
    corpo = r.json()
    return corpo["chave_inteira"], corpo["chave"]


def _listar(c, tenant=A, papel="owner"):
    r = c.get("/integracao/chaves", headers=_login(c, tenant, papel))
    assert r.status_code == 200, r.text
    return r.json()


def _listar_como(c, tenant=A, papel="owner", plano="premium"):
    r = c.get("/integracao/chaves", headers=_login(c, tenant, papel, plano))
    assert r.status_code == 200, r.text
    return r.json()


def _revogar(c, chave_id, tenant=A, papel="owner"):
    return c.delete(f"/integracao/chaves/{chave_id}", headers=_login(c, tenant, papel))


def _corpo(cid, mrr=300.0, perfil="PJ", **extra):
    return {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil, **extra}


def _chamar(c, metodo, modelo, chave, cid="c-1", json=None):
    """Uma das quatro rotas, autenticada pela chave."""
    caminho = modelo.replace("{customer_id_externo}", cid)
    return c.request(metodo, caminho, json=json, headers=_bearer(chave))


# ══════════════════════════════════════════════════════════════════════════
# A geração e a lista (K1, K4)
# ══════════════════════════════════════════════════════════════════════════

class TestGeracao:

    def test_a_chave_tem_o_prefixo_e_32_bytes_aleatorios(self, cliente):
        chave, dados = _gerar(cliente)
        assert chave.startswith("crai_live_") and chaves_api._FORMA.match(chave)
        assert len(chave) - len("crai_live_") == 43       # 32 bytes em base64 de URL
        assert dados["prefixo"] == chave[:14] and dados["final"] == chave[-4:]
        assert dados["nome"] == "Sistema de cobrança" and dados["situacao"] == "ativa"
        assert dados["criada_por_papel"] == "owner"
        assert dados["ultimo_uso_em"] is None and dados["revogada_em"] is None
        assert dados["usos_hoje"] == 0 and dados["id"].startswith("chv_")

    def test_duas_chaves_nunca_sao_iguais(self, cliente):
        chaves = {_gerar(cliente, nome=f"n{i}")[0] for i in range(5)}
        assert len(chaves) == 5

    def test_a_resposta_da_criacao_nao_pode_ser_guardada_por_intermediario(self, cliente):
        r = cliente.post("/integracao/chaves", json={"nome": "ERP"}, headers=_login(cliente))
        assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
        assert r.json()["aviso"] == "Guarde esta chave agora. Ela não será mostrada de novo."

    def test_a_chave_inteira_aparece_uma_unica_vez(self, cliente):
        chave, dados = _gerar(cliente)
        segredo = chave[len("crai_live_"):]
        lista = cliente.get("/integracao/chaves", headers=_login(cliente))
        assert chave not in lista.text and segredo not in lista.text
        assert "hash" not in lista.text and chaves_api.hash_da_chave(chave) not in lista.text
        r = _revogar(cliente, dados["id"])
        assert chave not in r.text and chaves_api.hash_da_chave(chave) not in r.text

    def test_a_lista_traz_so_o_que_a_tela_mostra(self, cliente):
        _, dados = _gerar(cliente, papel="admin")
        corpo = _listar(cliente)
        assert corpo["ativas"] == 1 and corpo["limite_ativas"] == 5 and corpo["pode_gerar"] is True
        assert corpo["chaves"] == [dados]
        assert set(dados) == {"id", "nome", "prefixo", "final", "criada_em", "criada_por_papel",
                              "ultimo_uso_em", "revogada_em", "situacao", "usos_hoje"}
        assert set(corpo) == {"chaves", "ativas", "limite_ativas", "pode_revogar",
                              "plano_permite_gerar", "pode_gerar"}
        assert dados["criada_por_papel"] == "admin"
        assert datetime.fromisoformat(dados["criada_em"]).tzinfo is not None

    def test_a_lista_vem_da_mais_nova_a_mais_antiga_e_e_so_da_empresa(self, cliente):
        _gerar(cliente, nome="primeira")
        _gerar(cliente, nome="segunda")
        _gerar(cliente, tenant=B, nome="da outra")
        assert [c["nome"] for c in _listar(cliente)["chaves"]] == ["segunda", "primeira"]
        assert [c["nome"] for c in _listar(cliente, tenant=B)["chaves"]] == ["da outra"]

    @pytest.mark.parametrize("corpo, motivo", [
        ({}, "nome_invalido"),
        ({"nome": ""}, "nome_invalido"),
        ({"nome": "   "}, "nome_invalido"),
        ({"nome": "x" * 61}, "nome_invalido"),
        ({"nome": 7}, "nome_invalido"),
        ({"nome": None}, "nome_invalido"),
        ({"nome": "duas\nlinhas"}, "nome_invalido"),
        ({"nome": "ERP", "tenant_id": B}, "campo_desconhecido"),
        ({"nome": "ERP", "ambiente": "test"}, "campo_desconhecido"),
    ])
    def test_nome_invalido_e_campo_estranho_sao_422_e_nada_e_criado(self, cliente, corpo, motivo):
        r = cliente.post("/integracao/chaves", json=corpo, headers=_login(cliente))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["motivo"] == motivo
        assert "chave_inteira" not in r.text
        assert _listar(cliente)["chaves"] == []

    def test_o_nome_aceita_60_caracteres_e_perde_os_espacos_das_pontas(self, cliente):
        _, dados = _gerar(cliente, nome="  " + "n" * 60 + "  ")
        assert dados["nome"] == "n" * 60

    def test_sem_token_as_tres_rotas_sao_401(self, cliente):
        assert cliente.get("/integracao/chaves").status_code == 401
        assert cliente.post("/integracao/chaves", json={"nome": "x"}).status_code == 401
        assert cliente.delete("/integracao/chaves/chv_x").status_code == 401


# ══════════════════════════════════════════════════════════════════════════
# A chave funciona de verdade (K7, K9, K11)
# ══════════════════════════════════════════════════════════════════════════

class TestAChaveFunciona:

    def test_gera_cria_cliente_e_ele_aparece_no_insights_da_empresa(self, cliente):
        chave, _ = _gerar(cliente)
        r = cliente.post("/clientes", json=_corpo("c-1", days_since_last=40, features_used_30d=0),
                         headers=_bearer(chave))
        assert r.status_code == 200, r.text
        assert r.json()["cliente"]["customer_id_externo"] == "c-1"
        insights = cliente.get("/insights", headers=_login(cliente))
        assert insights.status_code == 200, insights.text
        assert "c-1" in [l["customer_id_externo"] for l in insights.json()["clientes_em_risco"]]
        # E não no da outra empresa.
        de_b = cliente.get("/insights", headers=_login(cliente, tenant=B)).json()
        assert "c-1" not in [l["customer_id_externo"] for l in de_b["clientes_em_risco"]]

    def test_as_quatro_rotas_aceitam_a_chave(self, cliente):
        chave, _ = _gerar(cliente)
        assert _chamar(cliente, "POST", "/clientes", chave, json=_corpo("c-1")).status_code == 200
        lote = _chamar(cliente, "POST", "/clientes/lote", chave,
                       json={"clientes": [_corpo("c-2"), _corpo("c-3")]})
        assert lote.status_code == 200 and lote.json()["importados"] == 2
        patch = _chamar(cliente, "PATCH", "/clientes/{customer_id_externo}", chave,
                        json={"mrr": 999.0})
        assert patch.status_code == 200 and patch.json()["cliente"]["mrr"] == 999.0
        cancelar = _chamar(cliente, "DELETE", "/clientes/{customer_id_externo}", chave)
        assert cancelar.status_code == 200 and cancelar.json()["cliente"]["cancelado_em"]
        assert ci.contar(A, incluir_cancelados=True) == 3 and ci.contar(B) == 0

    def test_o_token_de_login_continua_valendo_ao_lado_da_chave(self, cliente):
        chave, _ = _gerar(cliente)
        login = cliente.projeto.bearer(A)             # sem papel e sem plano, como sempre foi
        assert cliente.post("/clientes", json=_corpo("pelo-login"), headers=login).status_code == 200
        assert cliente.post("/clientes", json=_corpo("pela-chave"),
                            headers=_bearer(chave)).status_code == 200
        r = cliente.patch("/clientes/pela-chave", json={"mrr": 10.0}, headers=login)
        assert r.status_code == 200 and r.json()["cliente"]["mrr"] == 10.0
        assert ci.contar(A) == 2

    def test_a_validacao_das_rotas_e_a_mesma_com_a_chave(self, cliente):
        chave, _ = _gerar(cliente)
        r = cliente.post("/clientes", json=_corpo("c-1", perfil="MEI"), headers=_bearer(chave))
        assert r.status_code == 422 and r.json()["detail"]["motivo"] == "cliente_invalido"
        r = cliente.patch("/clientes/nunca", json={"mrr": 1}, headers=_bearer(chave))
        assert r.status_code == 404 and r.json()["detail"]["motivo"] == "cliente_nao_encontrado"

    def test_a_idempotencia_vale_com_a_chave(self, cliente):
        chave, _ = _gerar(cliente)
        h = {**_bearer(chave), "Idempotency-Key": "pedido-1"}
        assert cliente.post("/clientes", json=_corpo("c-1"), headers=h).json()["reenvio"] is False
        assert cliente.post("/clientes", json=_corpo("c-1"), headers=h).json()["reenvio"] is True

    def test_cada_uso_grava_o_ultimo_uso_e_soma_no_contador_do_dia(self, cliente):
        chave, dados = _gerar(cliente)
        outra, dados_outra = _gerar(cliente, nome="outra")
        assert _listar(cliente)["chaves"][1]["ultimo_uso_em"] is None
        for i in range(3):
            assert cliente.post("/clientes", json=_corpo(f"c-{i}"),
                                headers=_bearer(chave)).status_code == 200
        # Requisição autenticada e recusada pela validação também é uso da chave.
        assert cliente.post("/clientes", json={"x": 1}, headers=_bearer(chave)).status_code == 422
        por_id = {c["id"]: c for c in _listar(cliente)["chaves"]}
        assert por_id[dados["id"]]["usos_hoje"] == 4
        assert datetime.fromisoformat(por_id[dados["id"]]["ultimo_uso_em"]).tzinfo is not None
        assert por_id[dados_outra["id"]]["usos_hoje"] == 0
        assert por_id[dados_outra["id"]]["ultimo_uso_em"] is None

    def test_nada_do_corpo_da_requisicao_e_guardado_com_o_uso(self, cliente):
        chave, _ = _gerar(cliente)
        marca = "MARCA-DO-CORPO-7731"
        assert cliente.post("/clientes", json=_corpo(marca, nome="Fulana " + marca),
                            headers=_bearer(chave)).status_code == 200
        conn = sqlite3.connect(os.environ["CRAI_RECOVERY_DB"])
        try:
            for tabela in ("chaves_api", "chaves_api_uso"):
                linhas = conn.execute(f"SELECT * FROM {tabela}").fetchall()
                assert linhas and marca not in repr(linhas)
            assert [c[1] for c in conn.execute("PRAGMA table_info(chaves_api_uso)")] == [
                "chave_id", "dia", "total"]
        finally:
            conn.close()


# ══════════════════════════════════════════════════════════════════════════
# O tenant vem da chave (K7)
# ══════════════════════════════════════════════════════════════════════════

class TestIsolamento:

    @pytest.fixture
    def duas_empresas(self, cliente):
        chave_a, _ = _gerar(cliente, tenant=A)
        chave_b, _ = _gerar(cliente, tenant=B)
        assert cliente.post("/clientes", json=_corpo("so-de-b", mrr=500.0),
                            headers=_bearer(chave_b)).status_code == 200
        return chave_a, chave_b

    def test_post_com_a_chave_de_a_nao_altera_o_cliente_de_b(self, cliente, duas_empresas):
        chave_a, _ = duas_empresas
        r = cliente.post("/clientes", json=_corpo("so-de-b", mrr=1.0), headers=_bearer(chave_a))
        assert r.status_code == 200                      # criou um cliente em A, com o mesmo id
        assert ci.obter(B, "so-de-b")["mrr"] == 500.0
        assert ci.obter(A, "so-de-b")["mrr"] == 1.0

    def test_lote_com_a_chave_de_a_grava_so_em_a(self, cliente, duas_empresas):
        chave_a, _ = duas_empresas
        r = cliente.post("/clientes/lote", json={"clientes": [_corpo("so-de-b", mrr=2.0),
                                                              _corpo("novo")]},
                         headers=_bearer(chave_a))
        assert r.status_code == 200 and r.json()["importados"] == 2
        assert ci.obter(B, "so-de-b")["mrr"] == 500.0 and ci.contar(B) == 1

    @pytest.mark.parametrize("metodo, json", [("PATCH", {"mrr": 1.0}), ("DELETE", None)])
    def test_patch_e_delete_no_cliente_de_b_sao_o_404_do_inexistente(self, cliente, duas_empresas,
                                                                   metodo, json):
        chave_a, _ = duas_empresas
        de_b = cliente.request(metodo, "/clientes/so-de-b", json=json, headers=_bearer(chave_a))
        inexistente = cliente.request(metodo, "/clientes/nunca-existiu", json=json,
                                      headers=_bearer(chave_a))
        assert de_b.status_code == inexistente.status_code == 404
        assert de_b.content == inexistente.content
        linha = ci.obter(B, "so-de-b")
        assert linha["mrr"] == 500.0 and linha["cancelado_em"] is None

    @pytest.mark.parametrize("campo", ["tenant_id", "tenant", "empresa"])
    def test_o_tenant_nunca_vem_do_corpo(self, cliente, duas_empresas, campo):
        chave_a, _ = duas_empresas
        r = cliente.post("/clientes", json={**_corpo("x"), campo: B}, headers=_bearer(chave_a))
        assert r.status_code == 422 and r.json()["detail"]["motivo"] == "campo_desconhecido"
        assert ci.contar(B) == 1 and ci.contar(A) == 0

    def test_o_cabecalho_de_tenant_dos_webhooks_nao_muda_o_tenant_da_chave(self, cliente,
                                                                         duas_empresas):
        chave_a, _ = duas_empresas
        r = cliente.post("/clientes", json=_corpo("x"),
                         headers={**_bearer(chave_a), "x-tenant-id": B})
        assert r.status_code == 200
        assert ci.obter(A, "x") is not None and ci.obter(B, "x") is None


# ══════════════════════════════════════════════════════════════════════════
# Revogação (K5) e o 401 único (K6)
# ══════════════════════════════════════════════════════════════════════════

def _igual_byte_a_byte(a, b) -> bool:
    return (a.status_code == b.status_code and a.content == b.content
            and a.headers.get("www-authenticate") == b.headers.get("www-authenticate")
            and a.headers.get("content-type") == b.headers.get("content-type")
            and a.headers.get("content-length") == b.headers.get("content-length"))


class TestRevogacao:

    def test_revogar_e_usar_de_novo_e_401_na_requisicao_seguinte(self, cliente):
        chave, dados = _gerar(cliente)
        assert cliente.post("/clientes", json=_corpo("antes"), headers=_bearer(chave)).status_code == 200
        r = _revogar(cliente, dados["id"])
        assert r.status_code == 200 and r.json()["ja_estava_revogada"] is False
        assert r.json()["chave"]["situacao"] == "revogada" and r.json()["chave"]["revogada_em"]
        depois = cliente.post("/clientes", json=_corpo("depois"), headers=_bearer(chave))
        assert depois.status_code == 401
        assert depois.json()["detail"]["motivo"] == "chave_invalida"
        assert ci.obter(A, "depois") is None

    def test_a_revogada_e_recusada_nas_quatro_rotas(self, cliente):
        chave, dados = _gerar(cliente)
        _revogar(cliente, dados["id"])
        for metodo, modelo in AS_QUATRO:
            assert _chamar(cliente, metodo, modelo, chave, json={}).status_code == 401, modelo

    def test_revogar_duas_vezes_devolve_200_com_a_data_original(self, cliente, monkeypatch):
        from crai.api import datas
        _, dados = _gerar(cliente)
        monkeypatch.setattr(datas, "agora_local", lambda: datetime(2026, 10, 4, 9, 0, 0))
        primeira = _revogar(cliente, dados["id"]).json()
        monkeypatch.setattr(datas, "agora_local", lambda: datetime(2026, 10, 5, 18, 30, 0))
        segunda = _revogar(cliente, dados["id"])
        assert segunda.status_code == 200 and segunda.json()["ja_estava_revogada"] is True
        assert segunda.json()["chave"]["revogada_em"] == primeira["chave"]["revogada_em"]
        assert primeira["chave"]["revogada_em"].startswith("2026-10-04T09:00:00")

    def test_id_de_outra_empresa_e_o_404_do_inexistente(self, cliente):
        chave_b, dados_b = _gerar(cliente, tenant=B)
        de_b = _revogar(cliente, dados_b["id"], tenant=A)
        inexistente = _revogar(cliente, "chv_0000000000000000", tenant=A)
        assert de_b.status_code == inexistente.status_code == 404
        assert de_b.content == inexistente.content
        assert de_b.json()["detail"]["motivo"] == "chave_nao_encontrada"
        # E a chave de B continua valendo.
        assert cliente.post("/clientes", json=_corpo("c"), headers=_bearer(chave_b)).status_code == 200
        assert _listar(cliente, tenant=B)["chaves"][0]["situacao"] == "ativa"

    def test_a_revogada_continua_na_lista_como_revogada(self, cliente):
        _, dados = _gerar(cliente)
        _revogar(cliente, dados["id"])
        corpo = _listar(cliente)
        assert corpo["ativas"] == 0 and corpo["chaves"][0]["situacao"] == "revogada"


class TestO401Unico:

    @pytest.mark.parametrize("metodo, modelo", AS_QUATRO)
    def test_revogada_inexistente_e_malformada_sao_identicas_byte_a_byte(self, cliente,
                                                                       metodo, modelo):
        revogada, dados = _gerar(cliente)
        _revogar(cliente, dados["id"])
        referencia = _chamar(cliente, metodo, modelo, revogada, json=_corpo("c-1"))
        assert referencia.status_code == 401
        assert referencia.json() == {"detail": {"motivo": "chave_invalida",
                                                "detalhe": "chave de API inválida ou revogada"}}
        assert referencia.headers["www-authenticate"] == "Bearer"
        for outra in [INVENTADA, *MALFORMADAS]:
            r = _chamar(cliente, metodo, modelo, outra, json=_corpo("c-1"))
            assert _igual_byte_a_byte(r, referencia), outra
        assert ci.contar(A, incluir_cancelados=True) == 0

    def test_o_mesmo_segredo_com_o_final_trocado_nao_autentica(self, cliente):
        """Mesmo prefixo visível, segredo diferente: a busca acha a candidata e
        a comparação do hash recusa."""
        chave, _ = _gerar(cliente)
        trocada = chave[:-1] + ("A" if chave[-1] != "A" else "B")
        r = cliente.post("/clientes", json=_corpo("c"), headers=_bearer(trocada))
        ref = cliente.post("/clientes", json=_corpo("c"), headers=_bearer(INVENTADA))
        assert r.status_code == 401 and _igual_byte_a_byte(r, ref)

    def test_o_401_nao_devolve_a_chave_enviada(self, cliente):
        chave, dados = _gerar(cliente)
        _revogar(cliente, dados["id"])
        for enviada in (chave, INVENTADA, MALFORMADAS[1]):
            r = cliente.post("/clientes", json=_corpo("c"), headers=_bearer(enviada))
            assert enviada not in r.text and enviada[len("crai_live_"):] not in r.text

    def test_a_comparacao_do_hash_e_em_tempo_constante(self, cliente, monkeypatch):
        chave, _ = _gerar(cliente)
        vistos = []
        original = hmac.compare_digest

        def espia(a, b):
            vistos.append((a, b))
            return original(a, b)

        monkeypatch.setattr(chaves_api.hmac, "compare_digest", espia)
        assert chaves_api.autenticar(chave) == A
        assert vistos == [(chaves_api.hash_da_chave(chave), chaves_api.hash_da_chave(chave))]


# ══════════════════════════════════════════════════════════════════════════
# A chave não vale fora das quatro rotas (K8)
# ══════════════════════════════════════════════════════════════════════════

def _rotas_do_app():
    """[(método, modelo, usa_login)] de todas as rotas do aplicativo."""
    rotas = []
    for rota in app_module.app.routes:
        metodos = getattr(rota, "methods", None)
        if not metodos or not hasattr(rota, "dependant"):
            continue
        nomes = {getattr(d.call, "__name__", "") for d in rota.dependant.dependencies}
        usa_login = bool(nomes & {"get_tenant_id", "get_conta"})
        for metodo in sorted(metodos - {"HEAD", "OPTIONS"}):
            rotas.append((metodo, rota.path, usa_login))
    return rotas


def _caminho_concreto(modelo: str) -> str:
    import re
    return re.sub(r"\{[^}]+\}", "x1", modelo)


class TestSoAsQuatroRotas:

    def test_a_lista_fechada_sao_as_quatro_rotas_de_clientes_e_elas_existem(self):
        assert chaves_api.ROTAS_COM_CHAVE == frozenset(AS_QUATRO)
        existentes = {(m, p) for m, p, _ in _rotas_do_app()}
        assert chaves_api.ROTAS_COM_CHAVE <= existentes, (
            "uma rota da lista da chave não existe mais no aplicativo: a chave deixaria de "
            "funcionar nela em silêncio")

    def test_nenhuma_outra_rota_do_aplicativo_aceita_a_chave(self, cliente):
        """Percorre TODAS as rotas. Toda rota que autentica (token de login)
        recebe uma chave VÁLIDA: fora das quatro, tem que responder 401, e a
        mesma resposta que dá a uma chave inventada. Rota que não autentica não
        lê o `Authorization`: não há o que a chave abrir nela."""
        chave, _ = _gerar(cliente)
        rotas = _rotas_do_app()
        com_login = [(m, p) for m, p, usa in rotas if usa]
        # CATRACA QUE NÃO ENCONTRA O QUE VERIFICAR REPROVA.
        assert len(com_login) >= 15, f"poucas rotas autenticadas para verificar: {com_login}"
        assert ("GET", "/integracao/chaves") in com_login and ("GET", "/insights") in com_login

        aceitaram = set()
        for metodo, modelo in com_login:
            corpo = None if metodo == "GET" else {}
            r = cliente.request(metodo, _caminho_concreto(modelo), json=corpo, headers=_bearer(chave))
            if r.status_code != 401:
                aceitaram.add((metodo, modelo))
                continue
            assert (metodo, modelo) not in chaves_api.ROTAS_COM_CHAVE, (metodo, modelo)
            assert r.json()["detail"]["motivo"] == "chave_nao_vale_nesta_rota", (metodo, modelo)
            inventada = cliente.request(metodo, _caminho_concreto(modelo), json=corpo,
                                        headers=_bearer(INVENTADA))
            assert _igual_byte_a_byte(r, inventada), (
                f"{metodo} {modelo} responde diferente a chave válida e a chave inventada")
        assert aceitaram == set(AS_QUATRO), (
            f"rotas fora da lista aceitaram a chave: {sorted(aceitaram - set(AS_QUATRO))}")

    def test_so_as_rotas_que_usam_a_dependencia_de_tenant_leem_a_chave(self):
        """A chave só é consultada por `get_tenant_id`. As quatro rotas a usam;
        nenhuma rota fora de `SELF_SERVICE` a usa (a catraca de
        `test_supabase_auth.py` garante), então webhook e simulação nem olham o
        cabeçalho."""
        for rota in app_module.app.routes:
            if not hasattr(rota, "dependant"):
                continue
            for metodo in getattr(rota, "methods", set()) or set():
                if (metodo, rota.path) in chaves_api.ROTAS_COM_CHAVE:
                    nomes = {getattr(d.call, "__name__", "") for d in rota.dependant.dependencies}
                    assert "get_tenant_id" in nomes, rota.path

    def test_a_chave_nao_gera_nem_revoga_chaves(self, cliente):
        chave, dados = _gerar(cliente)
        assert cliente.get("/integracao/chaves", headers=_bearer(chave)).status_code == 401
        assert cliente.post("/integracao/chaves", json={"nome": "x"},
                            headers=_bearer(chave)).status_code == 401
        assert cliente.delete(f"/integracao/chaves/{dados['id']}",
                              headers=_bearer(chave)).status_code == 401
        corpo = _listar(cliente)
        assert corpo["ativas"] == 1 and len(corpo["chaves"]) == 1

    def test_a_chave_recusada_fora_das_quatro_nao_conta_como_uso(self, cliente):
        chave, _ = _gerar(cliente)
        assert cliente.get("/insights", headers=_bearer(chave)).status_code == 401
        assert cliente.get("/ciclos", headers=_bearer(chave)).status_code == 401
        unica = _listar(cliente)["chaves"][0]
        assert unica["usos_hoje"] == 0 and unica["ultimo_uso_em"] is None

    def test_sem_rota_resolvida_a_chave_nao_vale(self):
        from crai.accounts import auth
        assert auth._rota_aceita_chave(None) is False


# ══════════════════════════════════════════════════════════════════════════
# Quem pode (K2, K3)
# ══════════════════════════════════════════════════════════════════════════

class TestPapelEPlano:

    @pytest.mark.parametrize("papel", ["membro", None])
    def test_membro_e_token_sem_papel_veem_a_lista_e_nao_geram_nem_revogam(self, cliente, papel):
        _, dados = _gerar(cliente)
        lista = cliente.get("/integracao/chaves", headers=_login(cliente, papel=papel))
        assert lista.status_code == 200 and lista.json()["pode_gerar"] is False
        assert lista.json()["pode_revogar"] is False and lista.json()["plano_permite_gerar"] is True
        assert len(lista.json()["chaves"]) == 1
        gerar = cliente.post("/integracao/chaves", json={"nome": "x"},
                             headers=_login(cliente, papel=papel))
        revogar = cliente.delete(f"/integracao/chaves/{dados['id']}",
                                 headers=_login(cliente, papel=papel))
        for r in (gerar, revogar):
            assert r.status_code == 403 and r.json()["detail"]["motivo"] == "papel_insuficiente"
        assert "chave_inteira" not in gerar.text
        corpo = _listar(cliente)
        assert corpo["ativas"] == 1 and len(corpo["chaves"]) == 1

    @pytest.mark.parametrize("papel", ["owner", "admin"])
    def test_dono_e_administrador_geram_e_revogam(self, cliente, papel):
        _, dados = _gerar(cliente, papel=papel)
        assert _revogar(cliente, dados["id"], papel=papel).status_code == 200

    FORA_DO_PREMIUM = ["essencial", None, "enterprise", "PREMIUM"]

    @pytest.mark.parametrize("plano", FORA_DO_PREMIUM)
    @pytest.mark.parametrize("papel", ["owner", "admin", "membro", None])
    def test_fora_do_premium_so_gerar_e_403_plano_sem_api(self, cliente, plano, papel):
        _gerar(cliente)
        r = cliente.post("/integracao/chaves", json={"nome": "x"},
                         headers=_login(cliente, papel=papel, plano=plano))
        assert r.status_code == 403, r.text
        # O plano é conferido antes do papel: o motivo é o mesmo para todo papel.
        assert r.json()["detail"] == {"motivo": "plano_sem_api",
                                      "detalhe": "gerar chave de API faz parte do plano Premium"}
        assert "chave_inteira" not in r.text
        corpo = _listar(cliente)
        assert corpo["ativas"] == 1 and len(corpo["chaves"]) == 1

    @pytest.mark.parametrize("plano", FORA_DO_PREMIUM)
    @pytest.mark.parametrize("papel, revoga", [("owner", True), ("admin", True),
                                               ("membro", False), (None, False)])
    def test_fora_do_premium_qualquer_papel_lista_e_a_resposta_diz_o_que_pode(self, cliente, plano,
                                                                              papel, revoga):
        _, dados = _gerar(cliente)
        r = cliente.get("/integracao/chaves", headers=_login(cliente, papel=papel, plano=plano))
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert [c["id"] for c in corpo["chaves"]] == [dados["id"]]
        assert corpo["plano_permite_gerar"] is False and corpo["pode_gerar"] is False
        assert corpo["pode_revogar"] is revoga

    @pytest.mark.parametrize("plano", FORA_DO_PREMIUM)
    @pytest.mark.parametrize("papel", ["owner", "admin"])
    def test_fora_do_premium_dono_e_administrador_revogam_e_a_chave_para_de_valer(self, cliente,
                                                                                  plano, papel):
        chave, dados = _gerar(cliente)
        assert cliente.post("/clientes", json=_corpo("antes"), headers=_bearer(chave)).status_code == 200
        r = cliente.delete(f"/integracao/chaves/{dados['id']}",
                           headers=_login(cliente, papel=papel, plano=plano))
        assert r.status_code == 200, r.text
        assert r.json()["chave"]["situacao"] == "revogada" and r.json()["ja_estava_revogada"] is False
        assert cliente.post("/clientes", json=_corpo("depois"), headers=_bearer(chave)).status_code == 401

    @pytest.mark.parametrize("plano", FORA_DO_PREMIUM)
    @pytest.mark.parametrize("papel", ["membro", None])
    def test_fora_do_premium_membro_continua_sem_revogar(self, cliente, plano, papel):
        chave, dados = _gerar(cliente)
        r = cliente.delete(f"/integracao/chaves/{dados['id']}",
                           headers=_login(cliente, papel=papel, plano=plano))
        assert r.status_code == 403 and r.json()["detail"]["motivo"] == "papel_insuficiente"
        assert cliente.post("/clientes", json=_corpo("c"), headers=_bearer(chave)).status_code == 200

    def test_no_premium_a_lista_diz_que_o_plano_permite_gerar(self, cliente):
        for papel, gera in (("owner", True), ("admin", True), ("membro", False)):
            corpo = _listar(cliente, papel=papel)
            assert corpo["plano_permite_gerar"] is True
            assert corpo["pode_gerar"] is gera and corpo["pode_revogar"] is gera

    def test_a_chave_gerada_no_premium_continua_valendo_se_a_empresa_sai_do_plano(self, cliente):
        """A chave não carrega plano: o uso não confere. O que a empresa que saiu
        do premium ganha com esta regra é poder VER e DESLIGAR as chaves."""
        chave, _ = _gerar(cliente)
        assert _listar_como(cliente, plano="essencial")["ativas"] == 1
        assert cliente.post("/clientes", json=_corpo("c"), headers=_bearer(chave)).status_code == 200

    def test_token_sem_a_claim_plano_e_tratado_como_essencial(self, cliente):
        from crai.accounts import auth
        import asyncio
        sem = asyncio.run(auth.get_conta(cliente.projeto.bearer(A)["Authorization"]))
        com = asyncio.run(auth.get_conta(
            cliente.projeto.bearer(A, plano="premium")["Authorization"]))
        assert sem["plano"] == "essencial" and com["plano"] == "premium"

    def test_o_plano_nao_muda_as_outras_rotas(self, cliente):
        """O token sem `plano` (o do login real até a Etapa 5) continua lendo e
        gravando o resto como antes."""
        h = _login(cliente, papel="admin", plano=None)
        assert cliente.get("/configuracao", headers=h).status_code == 200
        assert cliente.post("/clientes", json=_corpo("c-1"), headers=h).status_code == 200


# ══════════════════════════════════════════════════════════════════════════
# Limites (K10, K12)
# ══════════════════════════════════════════════════════════════════════════

class TestLimiteDeChaves:

    def test_a_sexta_chave_ativa_e_409(self, cliente):
        ids = [_gerar(cliente, nome=f"chave {i}")[1]["id"] for i in range(5)]
        r = cliente.post("/integracao/chaves", json={"nome": "a sexta"}, headers=_login(cliente))
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["motivo"] == "limite_de_chaves"
        assert "5" in r.json()["detail"]["detalhe"] and "chave_inteira" not in r.text
        assert _listar(cliente)["ativas"] == 5
        # O limite é das ATIVAS: revogar uma abre a vaga.
        assert _revogar(cliente, ids[0]).status_code == 200
        _gerar(cliente, nome="a sexta, agora sim")
        corpo = _listar(cliente)
        assert corpo["ativas"] == 5 and len(corpo["chaves"]) == 6

    def test_o_limite_e_por_empresa(self, cliente):
        for i in range(5):
            _gerar(cliente, nome=f"a{i}")
        _gerar(cliente, tenant=B, nome="b0")


class _Relogio:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestLimitePorMinuto:

    @pytest.fixture
    def relogio(self, monkeypatch):
        r = _Relogio()
        monkeypatch.setattr(chaves_api, "_relogio", r)
        return r

    def test_o_padrao_e_120_e_a_env_muda(self, monkeypatch):
        monkeypatch.delenv("CRAI_API_LIMITE_POR_MINUTO", raising=False)
        assert chaves_api.limite_por_minuto() == 120
        monkeypatch.setenv("CRAI_API_LIMITE_POR_MINUTO", "7")
        assert chaves_api.limite_por_minuto() == 7
        for ruim in ("", "abc", "0", "-3", "1.5"):
            monkeypatch.setenv("CRAI_API_LIMITE_POR_MINUTO", ruim)
            assert chaves_api.limite_por_minuto() == 120

    def test_acima_do_limite_e_429_com_retry_after(self, cliente, monkeypatch, relogio):
        monkeypatch.setenv("CRAI_API_LIMITE_POR_MINUTO", "3")
        chave, _ = _gerar(cliente)
        for i in range(3):
            relogio.t += 1
            assert cliente.post("/clientes", json=_corpo(f"c-{i}"),
                                headers=_bearer(chave)).status_code == 200
        relogio.t += 1                                # 3 s depois do primeiro uso
        r = cliente.post("/clientes", json=_corpo("a-mais"), headers=_bearer(chave))
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["motivo"] == "limite_de_uso"
        assert r.headers["retry-after"] == "57"       # o primeiro uso sai da janela em 57 s
        assert chave not in r.text and ci.obter(A, "a-mais") is None
        # A recusada não conta como uso.
        assert _listar(cliente)["chaves"][0]["usos_hoje"] == 3

    def test_passada_a_janela_a_chave_volta_a_valer(self, cliente, monkeypatch, relogio):
        monkeypatch.setenv("CRAI_API_LIMITE_POR_MINUTO", "2")
        chave, _ = _gerar(cliente)
        for i in range(2):
            assert cliente.post("/clientes", json=_corpo(f"c-{i}"),
                                headers=_bearer(chave)).status_code == 200
        assert cliente.post("/clientes", json=_corpo("x"), headers=_bearer(chave)).status_code == 429
        relogio.t += 60
        assert cliente.post("/clientes", json=_corpo("x"), headers=_bearer(chave)).status_code == 200

    def test_o_limite_e_por_chave(self, cliente, monkeypatch, relogio):
        monkeypatch.setenv("CRAI_API_LIMITE_POR_MINUTO", "1")
        uma, _ = _gerar(cliente, nome="uma")
        outra, _ = _gerar(cliente, nome="outra")
        assert cliente.post("/clientes", json=_corpo("a"), headers=_bearer(uma)).status_code == 200
        assert cliente.post("/clientes", json=_corpo("b"), headers=_bearer(uma)).status_code == 429
        assert cliente.post("/clientes", json=_corpo("c"), headers=_bearer(outra)).status_code == 200

    def test_o_token_de_login_nao_tem_esse_limite(self, cliente, monkeypatch, relogio):
        monkeypatch.setenv("CRAI_API_LIMITE_POR_MINUTO", "1")
        login = _login(cliente)
        for i in range(4):
            assert cliente.post("/clientes", json=_corpo(f"c-{i}"), headers=login).status_code == 200

    def test_chave_invalida_acima_do_limite_continua_401(self, cliente, monkeypatch, relogio):
        """O limite é da chave que autentica: a revogada não vira 429."""
        monkeypatch.setenv("CRAI_API_LIMITE_POR_MINUTO", "1")
        chave, dados = _gerar(cliente)
        assert cliente.post("/clientes", json=_corpo("a"), headers=_bearer(chave)).status_code == 200
        assert cliente.post("/clientes", json=_corpo("b"), headers=_bearer(chave)).status_code == 429
        _revogar(cliente, dados["id"])
        assert cliente.post("/clientes", json=_corpo("c"), headers=_bearer(chave)).status_code == 401

    def test_o_cors_deixa_o_navegador_ler_o_retry_after(self):
        import inspect
        assert 'expose_headers=["Retry-After"]' in inspect.getsource(app_module.instalar_cors)


# ══════════════════════════════════════════════════════════════════════════
# A chave não fica em lugar nenhum (K4)
# ══════════════════════════════════════════════════════════════════════════

def _fluxo_completo(c):
    """Gera, lista, usa nas quatro rotas, erra de propósito, estoura o limite,
    revoga e usa depois de revogada. Devolve a chave."""
    chave, dados = _gerar(c)
    _listar(c)
    c.post("/clientes", json=_corpo("c-1"), headers=_bearer(chave))
    c.post("/clientes/lote", json={"clientes": [_corpo("c-2")]}, headers=_bearer(chave))
    c.patch("/clientes/c-1", json={"mrr": 1.0}, headers=_bearer(chave))
    c.request("DELETE", "/clientes/c-1", headers=_bearer(chave))
    c.post("/clientes", json={"torto": True}, headers=_bearer(chave))
    c.get("/insights", headers=_bearer(chave))
    c.get("/integracao/chaves", headers=_bearer(chave))
    c.post("/clientes", json=_corpo("c-3"), headers=_bearer(chave[:-1] + "!"))
    for _ in range(4):
        c.post("/clientes", json=_corpo("c-4"), headers=_bearer(chave))
    assert _revogar(c, dados["id"]).status_code == 200
    assert c.post("/clientes", json=_corpo("c-5"), headers=_bearer(chave)).status_code == 401
    return chave


class TestAChaveNaoFicaEmLugarNenhum:

    def test_o_banco_nao_tem_a_chave_em_texto_puro(self, cliente, tmp_path):
        chave, _ = _gerar(cliente)
        outra = _fluxo_completo(cliente)
        banco = Path(os.environ["CRAI_RECOVERY_DB"])
        # O -wal e o -journal entram: tudo o que o SQLite escreveu em `tmp_path`.
        arquivos = [a for a in tmp_path.rglob("*") if a.is_file()]
        assert banco in arquivos
        for arquivo in arquivos:
            cru = arquivo.read_bytes()
            for k in (chave, outra):
                assert k.encode() not in cru, f"a chave inteira está em {arquivo.name}"
                assert k[len("crai_live_"):].encode() not in cru, (
                    f"o segredo da chave está em {arquivo.name}")
        # A varredura olhou o arquivo certo: o hash e o prefixo visível estão lá.
        cru = banco.read_bytes()
        assert chaves_api.hash_da_chave(chave).encode() in cru
        assert chave[:14].encode() in cru

    def test_a_tabela_guarda_so_o_que_foi_decidido(self, cliente):
        chave, _ = _gerar(cliente, papel="admin")
        conn = sqlite3.connect(os.environ["CRAI_RECOVERY_DB"])
        conn.row_factory = sqlite3.Row
        try:
            colunas = [c[1] for c in conn.execute("PRAGMA table_info(chaves_api)")]
            linha = dict(conn.execute("SELECT * FROM chaves_api").fetchone())
        finally:
            conn.close()
        assert colunas == ["id", "tenant_id", "nome", "prefixo", "final", "hash", "criada_em",
                           "criada_por_papel", "ultimo_uso_em", "revogada_em"]
        assert linha["hash"] == chaves_api.hash_da_chave(chave) and len(linha["hash"]) == 64
        assert linha["prefixo"] == chave[:14] and linha["final"] == chave[-4:]
        assert linha["tenant_id"] == A and linha["criada_por_papel"] == "admin"
        # O papel de quem criou, nunca quem: nem e-mail, nem o `sub` do token.
        assert "@" not in repr(linha) and "0f1e2d3c" not in repr(linha)

    def test_a_chave_nao_aparece_em_nenhum_log_nem_na_saida(self, cliente, caplog, capsys,
                                                         monkeypatch):
        monkeypatch.setenv("CRAI_API_LIMITE_POR_MINUTO", "6")
        with caplog.at_level(logging.DEBUG):
            chave = _fluxo_completo(cliente)
        segredo = chave[len("crai_live_"):]
        assert len(caplog.records) >= 3, "nada foi logado: a busca não mediu nada"
        assert any("[CHAVE-API]" in r.getMessage() for r in caplog.records)
        for registro in caplog.records:
            texto = registro.getMessage() + repr(registro.args) + str(registro.exc_info or "")
            assert chave not in texto and segredo not in texto, registro.name
        assert chave not in caplog.text and segredo not in caplog.text
        saida = capsys.readouterr()
        assert segredo not in saida.out and segredo not in saida.err

    def test_o_erro_do_banco_no_registro_do_uso_nao_derruba_nem_vaza(self, cliente, caplog,
                                                                   monkeypatch):
        """K11 é best effort: se a escrita do uso falhar, a requisição segue, e
        o log do erro não traz a chave."""
        chave, _ = _gerar(cliente)
        conectar = chaves_api._conectar
        chamadas = {"n": 0}

        def falha_na_segunda():
            chamadas["n"] += 1
            if chamadas["n"] == 2:                    # a 1a é a leitura; a 2a, o uso
                raise sqlite3.OperationalError("database is locked")
            return conectar()

        monkeypatch.setattr(chaves_api, "_conectar", falha_na_segunda)
        with caplog.at_level(logging.DEBUG):
            r = cliente.post("/clientes", json=_corpo("c-1"), headers=_bearer(chave))
        assert r.status_code == 200, r.text
        assert "uso não registrado" in caplog.text
        assert chave not in caplog.text and chave[len("crai_live_"):] not in caplog.text


# ══════════════════════════════════════════════════════════════════════════
# O registro de acesso
# ══════════════════════════════════════════════════════════════════════════

class TestRegistroDeAcesso:

    def test_as_tres_rotas_entram_no_registro_com_tenant_rota_e_papel(self, cliente):
        _, dados = _gerar(cliente, papel="owner")
        _listar(cliente, papel="membro")
        _revogar(cliente, dados["id"], papel="admin")
        vistos = {(l["rota"], l["papel"]) for l in registro_acesso.acessos(A)}
        assert vistos == {("POST /integracao/chaves", "owner"),
                          ("GET /integracao/chaves", "membro"),
                          ("DELETE /integracao/chaves/{chave_id}", "admin")}
        assert registro_acesso.acessos(B) == []
        # O modelo da rota, nunca o id da chave; e só as quatro colunas de sempre.
        assert dados["id"] not in repr(registro_acesso.acessos(A))
        assert set(registro_acesso.acessos(A)[0]) == {"tenant_id", "rota", "papel", "quando"}

    def test_operacao_recusada_nao_entra_no_registro(self, cliente):
        cliente.post("/integracao/chaves", json={"nome": "x"}, headers=_login(cliente, papel="membro"))
        cliente.post("/integracao/chaves", json={"nome": "x"}, headers=_login(cliente, plano="essencial"))
        cliente.delete("/integracao/chaves/chv_nao_existe", headers=_login(cliente))
        cliente.delete("/integracao/chaves/chv_nao_existe", headers=_login(cliente, papel="membro"))
        cliente.post("/integracao/chaves", json={"nome": ""}, headers=_login(cliente))
        assert registro_acesso.acessos(A) == []

    def test_o_uso_da_chave_nas_rotas_de_clientes_nao_entra_no_registro(self, cliente):
        chave, _ = _gerar(cliente)
        cliente.post("/clientes", json=_corpo("c-1"), headers=_bearer(chave))
        assert [l["rota"] for l in registro_acesso.acessos(A)] == ["POST /integracao/chaves"]


# ══════════════════════════════════════════════════════════════════════════
# O plano no token de desenvolvimento
# ══════════════════════════════════════════════════════════════════════════

class TestPlanoNoTokenDeDesenvolvimento:

    @pytest.fixture(autouse=True)
    def desenvolvimento(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        monkeypatch.delenv("SUPABASE_PROJECT_URL", raising=False)
        supabase_auth.limpar_cache()
        dev_token.esquecer_chave()
        yield
        dev_token.esquecer_chave()

    def test_o_padrao_e_premium(self):
        emitido = dev_token.emitir("owner")
        assert emitido["plano"] == "premium"
        assert supabase_auth.validar_token(emitido["token"])["plano"] == "premium"

    @pytest.mark.parametrize("plano", ["premium", "essencial"])
    def test_a_rota_emite_o_plano_pedido(self, plano):
        from fastapi import FastAPI
        aplicacao = FastAPI()
        assert dev_token.montar(aplicacao) is True
        r = TestClient(aplicacao).post("/dev/token", json={"papel": "admin", "plano": plano})
        assert r.status_code == 200, r.text
        assert r.json()["plano"] == plano and r.json()["papel"] == "admin"
        assert supabase_auth.validar_token(r.json()["token"])["plano"] == plano

    @pytest.mark.parametrize("plano", ["gratis", "", None, 1, ["premium"]])
    def test_plano_fora_do_vocabulario_e_422(self, plano):
        from fastapi import FastAPI
        aplicacao = FastAPI()
        dev_token.montar(aplicacao)
        r = TestClient(aplicacao).post("/dev/token", json={"plano": plano})
        assert r.status_code == 422 and r.json()["detail"]["motivo"] == "plano_invalido"
        assert "token" not in r.json()
        with pytest.raises(ValueError):
            dev_token.emitir("owner", "gratis")

    def test_no_servico_o_premium_gera_e_o_essencial_recebe_403(self):
        with TestClient(app_module.app) as c:
            premium = {"Authorization": "Bearer " + dev_token.emitir("owner")["token"]}
            essencial = {"Authorization": "Bearer " + dev_token.emitir("owner", "essencial")["token"]}
            membro = {"Authorization": "Bearer " + dev_token.emitir("membro")["token"]}
            r = c.post("/integracao/chaves", json={"nome": "ERP"}, headers=premium)
            assert r.status_code == 201, r.text
            chave = r.json()["chave_inteira"]
            assert c.post("/clientes", json=_corpo("c-1"), headers=_bearer(chave)).status_code == 200
            assert ci.obter(dev_token.TENANT_DEMO, "c-1") is not None
            r = c.post("/integracao/chaves", json={"nome": "ERP"}, headers=essencial)
            assert r.status_code == 403 and r.json()["detail"]["motivo"] == "plano_sem_api"
            r = c.post("/integracao/chaves", json={"nome": "ERP"}, headers=membro)
            assert r.status_code == 403 and r.json()["detail"]["motivo"] == "papel_insuficiente"


# ══════════════════════════════════════════════════════════════════════════
# A migração: as tabelas novas num banco que já existia
# ══════════════════════════════════════════════════════════════════════════

def _tabelas(banco: Path) -> set:
    conn = sqlite3.connect(banco)
    try:
        return {l[0] for l in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


def _linhas(banco: Path, tabela: str) -> list:
    conn = sqlite3.connect(banco)
    try:
        return conn.execute(f"SELECT * FROM {tabela} ORDER BY 1").fetchall()
    finally:
        conn.close()


class TestMigracao:

    @pytest.fixture
    def banco_antigo(self, tmp_path, monkeypatch):
        """Um banco como o de antes desta fase: com ciclo, configuração e
        registro de acesso, e SEM as tabelas da chave."""
        antigo = tmp_path / "antigo.db"
        monkeypatch.setenv("CRAI_RECOVERY_DB", str(antigo))
        cc.esquecer_schema_garantido()
        configuracao.gravar(A, {"prazo_escolha_horas": 4}, papel="admin")
        registro_acesso.registrar(A, registro_acesso.ROTA_CICLOS, "owner")
        registro_acesso.registrar(B, registro_acesso.ROTA_CICLO, "membro")
        assert "chaves_api" not in _tabelas(antigo) and "chaves_api_uso" not in _tabelas(antigo)
        assert "configuracao_tenant" in _tabelas(antigo) and "ciclos_cobranca" in _tabelas(antigo)
        return antigo

    def _migrar(self, banco, monkeypatch):
        monkeypatch.setenv("CRAI_RECOVERY_DB", str(banco))
        cc.esquecer_schema_garantido()
        return chaves_api.listar(A)

    def test_sobre_a_copia_cria_as_tabelas_e_preserva_o_que_havia(self, banco_antigo, tmp_path,
                                                                monkeypatch):
        copia = tmp_path / "copia.db"
        shutil.copy2(banco_antigo, copia)
        original = banco_antigo.read_bytes()
        antes = {t: _linhas(copia, t) for t in ("configuracao_tenant", "acessos_titular")}
        assert len(antes["acessos_titular"]) == 2 and len(antes["configuracao_tenant"]) == 1

        assert self._migrar(copia, monkeypatch) == []

        assert {"chaves_api", "chaves_api_uso"} <= _tabelas(copia)
        assert {t: _linhas(copia, t) for t in antes} == antes
        assert configuracao.ler(A)["prazo_escolha_horas"] == 4
        # O original só foi lido.
        assert banco_antigo.read_bytes() == original

    def test_o_indice_do_hash_e_unico(self, banco_antigo, tmp_path, monkeypatch):
        copia = tmp_path / "copia.db"
        shutil.copy2(banco_antigo, copia)
        self._migrar(copia, monkeypatch)
        conn = sqlite3.connect(copia)
        try:
            indices = {l[1]: l[2] for l in conn.execute("PRAGMA index_list(chaves_api)")}
            assert indices["idx_chaves_api_hash"] == 1                 # 1 = UNIQUE
            assert [l[2] for l in conn.execute("PRAGMA index_info(idx_chaves_api_hash)")] == ["hash"]
            linha = ("chv_1", A, "n", "crai_live_aaaa", "zzzz", "h" * 64, "2026-10-04T10:00:00", "owner")
            sql = ("INSERT INTO chaves_api (id, tenant_id, nome, prefixo, final, hash, criada_em, "
                   "criada_por_papel) VALUES (?, ?, ?, ?, ?, ?, ?, ?)")
            conn.execute(sql, linha)
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(sql, ("chv_2", B, *linha[2:]))
        finally:
            conn.close()

    def test_a_segunda_execucao_nao_muda_nada_e_a_chave_funciona_no_banco_migrado(
            self, banco_antigo, tmp_path, monkeypatch):
        copia = tmp_path / "copia.db"
        shutil.copy2(banco_antigo, copia)
        self._migrar(copia, monkeypatch)
        chave, dados = chaves_api.criar(A, "ERP", "owner")
        esquema = _linhas(copia, "sqlite_master")
        assert [c["id"] for c in self._migrar(copia, monkeypatch)] == [dados["id"]]
        assert _linhas(copia, "sqlite_master") == esquema
        assert chaves_api.autenticar(chave) == A
        assert len(_linhas(copia, "acessos_titular")) == 2


# ══════════════════════════════════════════════════════════════════════════
# O módulo, sem a rota
# ══════════════════════════════════════════════════════════════════════════

class TestModulo:

    def test_criar_recusa_a_sexta_ativa_na_mesma_transacao(self):
        for i in range(5):
            chaves_api.criar(A, f"n{i}", "owner")
        with pytest.raises(chaves_api.LimiteDeChaves):
            chaves_api.criar(A, "n5", "owner")
        assert len(chaves_api.listar(A)) == 5

    @pytest.mark.parametrize("ruim", [None, 1, "", INVENTADA, *MALFORMADAS, "eyJhbGciOiJFUzI1NiJ9.x.y"])
    def test_autenticar_recusa_tudo_o_que_nao_e_chave_ativa_com_a_mesma_excecao(self, ruim):
        chaves_api.criar(A, "n", "owner")
        with pytest.raises(chaves_api.ChaveInvalida) as e:
            chaves_api.autenticar(ruim)
        assert str(e.value) == ""                       # a exceção não carrega a chave

    def test_parece_chave_olha_so_o_prefixo(self):
        assert chaves_api.parece_chave(INVENTADA) and chaves_api.parece_chave("crai_live_")
        for nao in ("eyJhbGciOiJFUzI1NiJ9.x.y", "CRAI_LIVE_" + "A" * 43, "", None, 7):
            assert not chaves_api.parece_chave(nao)

    def test_revogar_de_outra_empresa_devolve_none_e_nao_revoga(self):
        chave, dados = chaves_api.criar(A, "n", "owner")
        assert chaves_api.revogar(B, dados["id"]) is None
        assert chaves_api.autenticar(chave) == A


# ══════════════════════════════════════════════════════════════════════════
# A segunda empresa fictícia do token de desenvolvimento (só para os testes ao vivo)
# ══════════════════════════════════════════════════════════════════════════

class TestEmpresaDosTestesAoVivo:

    @pytest.fixture(autouse=True)
    def desenvolvimento(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        monkeypatch.delenv("SUPABASE_PROJECT_URL", raising=False)
        supabase_auth.limpar_cache()
        dev_token.esquecer_chave()
        yield
        dev_token.esquecer_chave()

    def _rota(self):
        from fastapi import FastAPI
        aplicacao = FastAPI()
        assert dev_token.montar(aplicacao) is True
        return TestClient(aplicacao)

    def test_o_padrao_continua_sendo_a_empresa_da_demonstracao(self):
        assert dev_token.EMPRESAS == ("demo_dashboard", "demo_testes")
        r = self._rota().post("/dev/token")
        assert r.status_code == 200 and r.json()["tenant_id"] == "demo_dashboard"
        assert dev_token.emitir("owner")["tenant_id"] == "demo_dashboard"

    @pytest.mark.parametrize("empresa", ["demo_dashboard", "demo_testes"])
    def test_a_rota_emite_o_token_da_empresa_pedida(self, empresa):
        r = self._rota().post("/dev/token", json={"papel": "admin", "empresa": empresa})
        assert r.status_code == 200, r.text
        assert r.json()["tenant_id"] == empresa and r.json()["papel"] == "admin"
        claims = supabase_auth.validar_token(r.json()["token"])
        assert claims["tenant_id"] == empresa
        assert set(claims) == {"iss", "sub", "aud", "role", "tenant_id", "papel", "plano", "iat", "exp"}

    @pytest.mark.parametrize("empresa", ["empresa-a", "default_tenant", "DEMO_TESTES", "", None, 1,
                                         ["demo_testes"]])
    def test_a_lista_de_empresas_e_fechada(self, empresa):
        """Não dá para pedir token de uma empresa de verdade, nem de uma inventada."""
        r = self._rota().post("/dev/token", json={"empresa": empresa})
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["motivo"] == "empresa_invalida"
        assert "token" not in r.json()

    def test_emitir_recusa_empresa_fora_da_lista(self):
        for empresa in ("empresa-a", "", None):
            with pytest.raises(ValueError):
                dev_token.emitir("owner", "premium", empresa)

    @pytest.mark.parametrize("env", ["production", "demo", ""])
    def test_fora_de_development_o_token_da_empresa_de_testes_tambem_e_recusado(self, monkeypatch, env):
        token = dev_token.emitir("owner", "premium", "demo_testes")["token"]
        monkeypatch.setenv("ENV", env)
        with TestClient(app_module.app) as c:
            r = c.get("/configuracao", headers={"Authorization": "Bearer " + token})
            assert r.status_code == 401
            assert r.json()["detail"]["motivo"] == "token_de_desenvolvimento_recusado"

    def test_o_que_os_testes_fazem_na_empresa_deles_nao_aparece_na_demonstracao(self):
        de = lambda empresa, papel="owner": {  # noqa: E731
            "Authorization": "Bearer " + dev_token.emitir(papel, "premium", empresa)["token"]}
        with TestClient(app_module.app) as c:
            r = c.post("/integracao/chaves", json={"nome": "Teste ao vivo"}, headers=de("demo_testes"))
            assert r.status_code == 201, r.text
            chave, chave_id = r.json()["chave_inteira"], r.json()["chave"]["id"]
            assert c.post("/clientes", json=_corpo("cliente-do-teste"),
                          headers=_bearer(chave)).status_code == 200
            assert c.put("/configuracao", json={"prazo_escolha_horas": 4},
                         headers=de("demo_testes")).status_code == 200

            # A empresa da demonstração não vê nada disso.
            demo = c.get("/integracao/chaves", headers=de("demo_dashboard")).json()
            assert demo["chaves"] == [] and demo["ativas"] == 0
            assert ci.obter("demo_dashboard", "cliente-do-teste") is None
            assert ci.obter("demo_testes", "cliente-do-teste") is not None
            assert c.get("/configuracao", headers=de("demo_dashboard")).json()[
                "configuracao"]["prazo_escolha_horas"] != 4
            # Nem revoga a chave da outra.
            r = c.delete(f"/integracao/chaves/{chave_id}", headers=de("demo_dashboard"))
            assert r.status_code == 404
            assert registro_acesso.acessos("demo_dashboard") != [] and all(
                l["rota"] != "POST /integracao/chaves" for l in registro_acesso.acessos("demo_dashboard"))

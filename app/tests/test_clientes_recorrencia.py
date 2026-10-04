"""tests/test_clientes_recorrencia.py — Etapa 2, Bloco 3: as 12 colunas da base.

`id_recorrencia` (liga a cobrança do Pix ao cadastro, única por empresa), as 9
de comportamento do v3 e os contatos `telefone` e `nome` — todas opcionais,
validadas, ausente = NULL (nunca 0). E a prova de que o pipeline voluntário não
muda: a mesma base, com e sem as colunas, dá a mesma importação (menos a
coluna) e o mesmo ranking.
"""

import pytest

from crai.churn_voluntary import batch_scoring, clientes_importados as ci, importacao

A, B = "empresa-a", "empresa-b"

CSV_SEM = ("customer_id_externo,mrr,billing_profile,days_since_last,features_used_30d,email\n"
           "c-1,100,CLT,40,2,a@x.com\nc-2,250,PJ,3,12,\nc-3,90,freelancer,,,\n")
CSV_COM = ("customer_id_externo;mrr;billing_profile;days_since_last;features_used_30d;email;"
           "id_recorrencia;logins_7d;logins_30d;avg_session_min;api_calls_7d;tickets_30d;"
           "failed_pay_90d;nps_last;seats;tenure_days;telefone;nome\n"
           "c-1;100;CLT;40;2;a@x.com;RN_1;0;3;4,5;0;2;1;6;3;400;(11) 98888-7777;Ana Souza\n"
           "c-2;250;PJ;3;12;;RN_2;9;40;22;120;0;0;9,5;10;1200;;\n"
           "c-3;90;freelancer;;;;;;;;;;;;;;;\n")


class TestContrato:

    def test_os_nomes_do_comportamento_sao_os_do_v3(self):
        from crai.ml.voluntario_v3 import FEATURES_DE_RISCO_V3
        assert set(ci.COMPORTAMENTO_V3) <= set(FEATURES_DE_RISCO_V3)
        assert len(ci.COMPORTAMENTO_V3) == 9
        assert ci.COLUNAS_ETAPA2 == ("id_recorrencia", *ci.COMPORTAMENTO_V3, "telefone", "nome")

    def test_importacao_sem_as_colunas_responde_como_antes(self):
        r = importacao.importar(A, "base.csv", CSV_SEM.encode())
        assert r == {"importados": 3, "rejeitados": [], "colunas_nao_encontradas": [],
                     "linhas_sem_dado_comportamental": 1}
        for c in ci.listar(A):
            assert all(c[col] is None for col in ci.COLUNAS_ETAPA2)

    def test_importacao_com_as_colunas_grava_tipado_e_ausente_e_nulo(self):
        r = importacao.importar(A, "base.csv", CSV_COM.encode())
        assert r["importados"] == 3 and r["rejeitados"] == []
        um = ci.obter(A, "c-1")
        assert (um["id_recorrencia"], um["logins_30d"], um["avg_session_min"], um["nps_last"],
                um["tenure_days"], um["telefone"], um["nome"]) == (
            "RN_1", 3, 4.5, 6.0, 400, "11988887777", "Ana Souza")
        assert um["logins_7d"] == 0, "zero informado é zero"
        tres = ci.obter(A, "c-3")
        assert all(tres[col] is None for col in ci.COLUNAS_ETAPA2), "ausente virou 0"
        assert ci.obter_por_recorrencia(A, "RN_2")["customer_id_externo"] == "c-2"
        assert ci.obter_por_recorrencia(B, "RN_2") is None

    def test_o_ranking_do_voluntario_e_o_mesmo_com_e_sem_as_colunas(self):
        importacao.importar(A, "base.csv", CSV_SEM.encode())
        importacao.importar(B, "base.csv", CSV_COM.encode())
        sem, com = batch_scoring.pontuar_base(A), batch_scoring.pontuar_base(B)
        ignorar = {"importado_em"}
        assert [{k: v for k, v in l.items() if k not in ignorar} for l in sem] == \
               [{k: v for k, v in l.items() if k not in ignorar} for l in com]


class TestValidacao:

    @pytest.mark.parametrize("campo, valor", [
        ("logins_7d", "3,5"), ("logins_7d", "-1"), ("seats", "muitos"), ("tenure_days", "1.5"),
        ("avg_session_min", "-2"), ("nps_last", "11"), ("nps_last", "-1"),
        ("telefone", "123"), ("telefone", "abc12345678"), ("nome", "x" * 121),
        ("id_recorrencia", "RN com espaco"),
    ])
    def test_valor_torto_e_rejeitado_com_motivo(self, campo, valor):
        cliente, motivo = importacao.validar_linha(
            {"customer_id_externo": "c", "mrr": "10", "billing_profile": "CLT", campo: valor})
        assert cliente is None and campo.split("_")[0] in motivo

    def test_valores_limite_aceitos(self):
        cliente, motivo = importacao.validar_linha(
            {"customer_id_externo": "c", "mrr": "10", "billing_profile": "CLT", "nps_last": "0",
             "seats": "0", "telefone": "+55 11 3333-4444", "nome": "  Zé   da Silva "})
        assert motivo is None
        assert (cliente["nps_last"], cliente["seats"], cliente["telefone"], cliente["nome"]) == (
            0.0, 0, "+551133334444", "Zé da Silva")


class TestRecorrenciaUnica:

    def test_repetida_no_lote_em_clientes_diferentes_rejeita_a_segunda(self):
        csv = ("customer_id_externo,mrr,billing_profile,id_recorrencia\n"
               "c-1,10,CLT,RN_x\nc-2,10,CLT,RN_x\n")
        r = importacao.importar(A, "b.csv", csv.encode())
        assert r["importados"] == 1
        assert r["rejeitados"][0]["linha"] == 3 and "RN_x" in r["rejeitados"][0]["motivo"]

    def test_ja_ligada_a_outro_cliente_gravado_rejeita(self):
        importacao.importar(A, "b.csv", b"customer_id_externo,mrr,billing_profile,id_recorrencia\n"
                                         b"c-1,10,CLT,RN_x\n")
        r = importacao.importar(A, "b.csv", b"customer_id_externo,mrr,billing_profile,id_recorrencia\n"
                                            b"c-2,10,CLT,RN_x\n")
        assert r["importados"] == 0 and "c-1" in r["rejeitados"][0]["motivo"]
        # O mesmo cliente reimportado com a mesma recorrência é a foto nova, não conflito.
        r = importacao.importar(A, "b.csv", b"customer_id_externo,mrr,billing_profile,id_recorrencia\n"
                                            b"c-1,20,CLT,RN_x\n")
        assert r["importados"] == 1 and ci.obter(A, "c-1")["mrr"] == 20.0

    def test_mesma_recorrencia_em_empresas_diferentes_pode(self):
        linha = b"customer_id_externo,mrr,billing_profile,id_recorrencia\nc-1,10,CLT,RN_x\n"
        assert importacao.importar(A, "b.csv", linha)["importados"] == 1
        assert importacao.importar(B, "b.csv", linha)["importados"] == 1

    def test_o_banco_recusa_mesmo_sem_a_validacao(self):
        ci.upsert_um(A, {**_cliente("c-1"), "id_recorrencia": "RN_z"})
        with pytest.raises(ci.ConflitoDeRecorrencia):
            ci.upsert_um(A, {**_cliente("c-2"), "id_recorrencia": "RN_z"})


def _cliente(cid):
    cliente, _ = importacao.validar_linha({"customer_id_externo": cid, "mrr": "10",
                                           "billing_profile": "CLT"})
    return cliente


class TestApiDeClientes:

    @pytest.fixture
    def api(self, supabase_falso):
        from fastapi.testclient import TestClient
        from crai.api import app as app_module
        with TestClient(app_module.app) as c:
            c.projeto = supabase_falso
            yield c

    def test_post_aceita_as_12_e_devolve_na_resposta(self, api):
        corpo = {"customer_id_externo": "c-1", "mrr": 10, "billing_profile": "CLT",
                 "id_recorrencia": "RN_api", "logins_7d": 2, "nps_last": 8.5,
                 "telefone": "11999998888", "nome": "Ana Souza"}
        r = api.post("/clientes", json=corpo, headers=api.projeto.bearer(A))
        assert r.status_code == 200, r.text
        cliente = r.json()["cliente"]
        assert (cliente["id_recorrencia"], cliente["logins_7d"], cliente["nps_last"],
                cliente["telefone"], cliente["nome"]) == ("RN_api", 2, 8.5, "11999998888", "Ana Souza")
        assert cliente["seats"] is None

    def test_conflito_de_recorrencia_e_409(self, api):
        h = api.projeto.bearer(A)
        base = {"mrr": 10, "billing_profile": "CLT", "id_recorrencia": "RN_api"}
        assert api.post("/clientes", json={"customer_id_externo": "c-1", **base}, headers=h).status_code == 200
        r = api.post("/clientes", json={"customer_id_externo": "c-2", **base}, headers=h)
        assert r.status_code == 409 and r.json()["detail"]["campo"] == "id_recorrencia"
        api.post("/clientes", json={"customer_id_externo": "c-2", "mrr": 10,
                                    "billing_profile": "CLT"}, headers=h)
        r = api.patch("/clientes/c-2", json={"id_recorrencia": "RN_api"}, headers=h)
        assert r.status_code == 409
        r = api.post("/clientes/lote", json={"clientes": [{"customer_id_externo": "c-3", **base}]},
                     headers=h)
        assert r.json()["importados"] == 0 and r.json()["rejeitados"][0]["indice"] == 0

    def test_patch_muda_so_a_coluna_nova(self, api):
        h = api.projeto.bearer(A)
        api.post("/clientes", json={"customer_id_externo": "c-1", "mrr": 10,
                                    "billing_profile": "CLT", "email": "a@x.com"}, headers=h)
        r = api.patch("/clientes/c-1", json={"telefone": "11988887777", "tenure_days": 30}, headers=h)
        assert r.status_code == 200
        cliente = r.json()["cliente"]
        assert cliente["telefone"] == "11988887777" and cliente["tenure_days"] == 30
        assert cliente["email"] == "a@x.com" and cliente["mrr"] == 10.0

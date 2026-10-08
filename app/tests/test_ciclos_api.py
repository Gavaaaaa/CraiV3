"""tests/test_ciclos_api.py — Etapa 2, Bloco 2: leitura e isolamento.

O QUE ESTE ARQUIVO MEDE (Portão 2):
  - uma sequência completa (falha → 3 tentativas → mensagem → pagamento no dia
    10) vista SÓ pelas rotas, com o status R10 certo em cada momento;
  - o valor líquido do mês bate com uma conta feita à mão sobre os mesmos
    ciclos, e a taxa não aparece (R11);
  - R12 em cada rota nova: token de A com id de B → 404 igual ao inexistente,
    e nada de B nas listas e métricas de A;
  - nenhum ciclo some: perdido e descartado aparecem como "encerrado sem
    recuperação", com o motivo;
  - a tradução de estado para status é uma só, em Python e em SQL;
  - toda data sai em ISO 8601 com fuso;
  - a deduplicação do Pix inclui o tenant, e a confirmação não procura ciclo
    em outra empresa (0.5).
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as workflow_module
from crai.churn_voluntary import retention_log as trilha
from crai.api import app as app_module
from crai.api import datas
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import dunning_engine
from crai.dunning import pix_automatico_retry as pix_retry_module
from crai.dunning import retry_scheduler as sched

A, B = "empresa-a", "empresa-b"
SECRET = b"s3cr3t_ciclos"
VALOR = 299.90
ABERTURA = datetime(2026, 9, 3, 9, 0)
FUSO = ZoneInfo("America/Sao_Paulo")


def _assinar(corpo: bytes) -> dict:
    ts = int(time.time())
    mac = hmac.new(SECRET, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json"}


def _evento(rec, e2e, evento="automatic_pix.charge_failed", id_cobranca=None):
    corpo = {"event": evento, "e2e_id": e2e, "valor": VALOR, "id_recorrencia": rec}
    if evento.endswith("charge_failed"):
        corpo["codigo_falha"] = "AM04"
    if id_cobranca:
        corpo["id_cobranca"] = id_cobranca
    return json.dumps(corpo).encode()


@pytest.fixture
def cliente(monkeypatch, supabase_falso):
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", SECRET.decode())
    monkeypatch.setenv(datas.ENV_FUSO, "America/Sao_Paulo")

    async def recusa(*a, **k):
        raise RuntimeError("sem LLM no teste")
    monkeypatch.setattr(dunning_engine.claude.messages, "create", recusa)
    with TestClient(app_module.app) as c:
        c.projeto = supabase_falso
        yield c


@pytest.fixture
def relogio(monkeypatch):
    estado = {"agora": ABERTURA}

    class Congelado(datetime):
        @classmethod
        def now(cls, tz=None):
            # O `agora` do teste é hora local de São Paulo; com `tz`, o mesmo
            # INSTANTE nesse fuso — é o que a trilha (UTC) precisa para a
            # junção com o ciclo (hora local) bater como em produção.
            if tz is None:
                return estado["agora"]
            return estado["agora"].replace(tzinfo=FUSO).astimezone(tz)

    monkeypatch.setattr(workflow_module, "datetime", Congelado)
    monkeypatch.setattr(trilha, "datetime", Congelado)
    monkeypatch.setattr(pix_retry_module, "datetime", Congelado)
    monkeypatch.setattr(app_module, "datetime", Congelado)
    monkeypatch.setattr(workflow_module._pix_retry, "confianca_minima", 2.0)
    monkeypatch.setattr(datas, "agora_local", lambda: estado["agora"])

    def mover(para):
        estado["agora"] = para
    return mover


def _post(c, corpo, tenant):
    r = c.post("/webhooks/pix-automatico", content=corpo,
               headers={**_assinar(corpo), "x-tenant-id": tenant})
    assert r.status_code == 200, r.text
    return r.json()


def _get(c, caminho, tenant=A, **params):
    return c.get(caminho, params=params, headers=c.projeto.bearer(tenant))


def _datas_com_fuso(valor, caminho="$"):
    """Toda chave terminada em `_em`, `_inicio`, `_fim`, `quando`, `inicio`,
    `fim` que tenha valor precisa ser ISO 8601 com fuso."""
    if isinstance(valor, dict):
        for k, v in valor.items():
            if isinstance(v, str) and (k.endswith("_em") or k in (
                    "quando", "inicio", "fim", "janela_inicio", "janela_fim")):
                assert datetime.fromisoformat(v).tzinfo is not None, f"{caminho}.{k}={v!r} sem fuso"
            _datas_com_fuso(v, f"{caminho}.{k}")
    elif isinstance(valor, list):
        for i, v in enumerate(valor):
            _datas_com_fuso(v, f"{caminho}[{i}]")


def _status(c, ciclo_id, tenant=A):
    r = _get(c, f"/ciclos/{ciclo_id}", tenant)
    assert r.status_code == 200, r.text
    return r.json()["ciclo"]["status"]


# ══════════════════════════════════════════════════════════════════════════
# A sequência inteira, só pelas rotas
# ══════════════════════════════════════════════════════════════════════════

class TestSequenciaPelasRotas:

    def test_falha_tres_tentativas_mensagem_e_pagamento_no_dia_10(self, cliente, relogio):
        rec = "RN_rotas_a"
        _post(cliente, _evento(rec, f"E_{rec}_0"), A)

        lista = _get(cliente, "/ciclos").json()["ciclos"]
        assert len(lista) == 1
        ciclo_id = lista[0]["id"]
        assert lista[0]["status"] == cc.STATUS_EM_ANALISE, "sem tentativa disparada é em análise"
        assert lista[0]["valor_cobranca"] == VALOR and lista[0]["valor_liquido"] is None

        for numero in (1, 2, 3):
            plano = cc.tentativas_do_ciclo(ciclo_id)
            quando = datetime.fromisoformat(plano[numero - 1]["agendada_para"]) + timedelta(hours=1)
            relogio(quando)
            disparos = __import__("asyncio").run(sched.processar_tentativas_devidas(quando))
            assert [d["numero"] for d in disparos] == [numero]
            assert _status(cliente, ciclo_id) == cc.STATUS_EM_PROCESSO
            _post(cliente, _evento(rec, f"E_{rec}_t{numero}",
                                   id_cobranca=disparos[0]["id_cobranca"]), A)

        detalhe = _get(cliente, f"/ciclos/{ciclo_id}").json()
        assert detalhe["ciclo"]["estado"] == cc.MENSAGEM_ENVIADA
        assert detalhe["ciclo"]["valor_cobranca"] == VALOR
        assert detalhe["ciclo"]["valor_liquido"] is None
        assert detalhe["ciclo"]["status"] == cc.STATUS_EM_PROCESSO
        assert detalhe["ciclo"]["mensagem"]["enviada_em"]

        relogio(ABERTURA + timedelta(days=10))
        pago = _post(cliente, _evento(rec, f"E_{rec}_pago", "automatic_pix.charge_paid"), A)
        assert pago["ciclo"] == "recuperado"

        detalhe = _get(cliente, f"/ciclos/{ciclo_id}").json()
        ciclo = detalhe["ciclo"]
        assert ciclo["status"] == cc.STATUS_RECUPERADO
        assert ciclo["tentativas_executadas"] == 3
        # R11: bruto e líquido em campos fixos; a taxa não aparece em lugar nenhum.
        assert ciclo["valor_cobranca"] == VALOR
        assert ciclo["valor_liquido"] == round(VALOR - pago["fee"], 2)
        assert "fee" not in json.dumps(detalhe)
        tipos = [e["tipo"] for e in detalhe["linha_do_tempo"]]
        for esperado in ("abertura", "diagnostico", "tentativa_disparada", "tentativa_resultado",
                         "mensagem_reservada", "mensagem_enviada", "recuperado"):
            assert esperado in tipos, (esperado, tipos)
        assert tipos.index("abertura") < tipos.index("mensagem_enviada") < tipos.index("recuperado")
        assert tipos.count("tentativa_disparada") == 3
        quandos = [datetime.fromisoformat(e["quando"]) for e in detalhe["linha_do_tempo"]]
        assert quandos == sorted(quandos), "a linha do tempo não está em ordem"
        # O diagnóstico em linguagem simples, com as contribuições do SHAP gravadas.
        diag = detalhe["diagnostico"]
        assert diag and diag["explicacao"]
        # Sem os modelos em `models/` (clone limpo, antes do download do pacote) o
        # diagnóstico sai da heurística, que não tem contribuição por fator: a lista
        # vem vazia, e isso é o comportamento declarado, não um defeito.
        if getattr(workflow_module._classifier, "is_fitted", False):
            assert diag["contribuicoes"], "com o modelo carregado, o SHAP tem de vir gravado"
        assert len(diag["contribuicoes"]) <= 5
        for item in diag["contribuicoes"]:
            assert " = " not in item["fator"], item
        assert detalhe["trilha_ambigua"] is False
        _datas_com_fuso(detalhe)
        # A trilha do período entra, e cada decisão uma vez só.
        decisoes = [e for e in detalhe["linha_do_tempo"] if e["tipo"] == "decisao"]
        assert {d["dados"]["tipo_decisao"] for d in decisoes} >= {"risco", "retentativa"}


# ══════════════════════════════════════════════════════════════════════════
# Ciclos montados direto no banco, com datas conhecidas
# ══════════════════════════════════════════════════════════════════════════

def _recuperado(tenant, rec, valor, fee, aberto, pago):
    c = cc.abrir_ciclo(tenant, rec, valor, "insufficient_funds", aberto, id_cobranca=f"inv_{rec}")
    cc.fechar_como_recuperado(c["id"], fee, pago)
    return c["id"]


def _perdido(tenant, rec, valor, aberto, perdido):
    c = cc.abrir_ciclo(tenant, rec, valor, "insufficient_funds", aberto, id_cobranca=f"inv_{rec}")
    cc.transicionar(c["id"], cc.MENSAGEM_ENVIADA, aberto + timedelta(days=7),
                    mensagem_confirmada_em=(aberto + timedelta(days=7)).isoformat())
    cc.transicionar(c["id"], cc.PERDIDO, perdido)
    return c["id"]


def _descartado(tenant, rec, valor, aberto, quando):
    c = cc.abrir_ciclo(tenant, rec, valor, "insufficient_funds", aberto, id_cobranca=f"inv_{rec}")
    cc.descartar(c["id"], "eprofit_nao_positivo", quando)
    return c["id"]


@pytest.fixture
def base_conhecida():
    """Setembro de 2026 da empresa A, e ruído em volta (agosto, empresa B)."""
    ids = {
        "a_rec_1": _recuperado(A, "RN_m1", 100.00, 15.00, datetime(2026, 9, 1, 10), datetime(2026, 9, 5, 10)),
        "a_rec_2": _recuperado(A, "RN_m2", 250.50, 37.58, datetime(2026, 9, 2, 10), datetime(2026, 9, 20, 23, 59)),
        "a_perd": _perdido(A, "RN_m3", 80.00, datetime(2026, 8, 1, 10), datetime(2026, 9, 9, 8)),
        "a_desc": _descartado(A, "RN_m4", 60.00, datetime(2026, 9, 10, 10), datetime(2026, 9, 10, 10)),
        "a_aberto": cc.abrir_ciclo(A, "RN_m5", 40.00, "insufficient_funds",
                                   datetime(2026, 9, 25, 10), id_cobranca="inv_RN_m5")["id"],
        # Fora do mês: recuperado em 1º de outubro à meia-noite (limite exclusivo).
        "a_out": _recuperado(A, "RN_m6", 999.00, 149.85, datetime(2026, 9, 28, 10), datetime(2026, 10, 1, 0, 0)),
        # Agosto.
        "a_ago": _recuperado(A, "RN_m7", 500.00, 75.00, datetime(2026, 8, 3, 10), datetime(2026, 8, 30, 10)),
        # Empresa B, no mesmo mês.
        "b_rec": _recuperado(B, "RN_b1", 7777.00, 1166.55, datetime(2026, 9, 2, 10), datetime(2026, 9, 6, 10)),
    }
    return ids


class TestMetricasDoMes:

    def test_valor_liquido_do_mes_bate_com_a_conta_a_mao(self, cliente, relogio, base_conhecida):
        corpo = _get(cliente, "/metrics/involuntario/mes", mes="2026-09").json()
        # À mão: recuperados de A com recuperado_em em setembro: 100,00 − 15,00 e
        # 250,50 − 37,58. Os de 1º/10, de agosto e de B não entram.
        assert corpo["valor_liquido_recuperado"] == round((100.00 - 15.00) + (250.50 - 37.58), 2) == 297.92
        assert corpo["recuperados"] == 2
        # Encerrados com desfecho em setembro: o perdido (9/9) e o descartado (10/9).
        assert corpo["encerrados_sem_recuperacao"] == 2
        assert corpo["taxa_recuperacao"] == 0.5
        # Abertos em setembro: rec_1, rec_2, desc, aberto e o de 1º/10 (aberto em 28/9).
        assert corpo["ciclos_abertos_no_mes"] == {
            cc.STATUS_EM_ANALISE: 1, cc.STATUS_EM_PROCESSO: 0,
            cc.STATUS_RECUPERADO: 3, cc.STATUS_ENCERRADO: 1}
        assert corpo["mes"] == "2026-09"
        assert "fee" not in json.dumps(corpo) and "7777" not in json.dumps(corpo)
        _datas_com_fuso(corpo)

    def test_mes_sem_desfecho_tem_taxa_nula_nao_zero(self, cliente, relogio, base_conhecida):
        corpo = _get(cliente, "/metrics/involuntario/mes", mes="2026-07").json()
        assert corpo["taxa_recuperacao"] is None and corpo["recuperados"] == 0

    def test_mes_padrao_e_o_corrente(self, cliente, relogio, base_conhecida):
        relogio(datetime(2026, 9, 15, 12))
        assert _get(cliente, "/metrics/involuntario/mes").json()["mes"] == "2026-09"

    @pytest.mark.parametrize("mes", ["2026-13", "2026-9", "setembro", "2026-09-01", "0000-01"])
    def test_mes_invalido_e_422(self, cliente, relogio, mes):
        assert _get(cliente, "/metrics/involuntario/mes", mes=mes).status_code == 422

    def test_serie_diaria_bate_com_os_mesmos_ciclos(self, cliente, relogio, base_conhecida):
        relogio(datetime(2026, 9, 30, 18))
        corpo = _get(cliente, "/metrics/involuntario/serie", dias=30).json()
        assert len(corpo["pontos"]) == 30
        por_dia = {p["dia"]: p for p in corpo["pontos"]}
        assert por_dia["2026-09-05"]["valor_liquido_recuperado"] == 85.00
        assert por_dia["2026-09-20"]["valor_liquido_recuperado"] == 212.92
        assert por_dia["2026-09-09"]["encerrados_sem_recuperacao"] == 1
        assert por_dia["2026-09-06"]["recuperados"] == 0, "o ciclo de B entrou na série de A"
        assert por_dia["2026-09-01"]["taxa_recuperacao"] is None
        total = round(sum(p["valor_liquido_recuperado"] for p in corpo["pontos"]), 2)
        assert total == 297.92

    @pytest.mark.parametrize("dias", [0, 366, -1])
    def test_serie_fora_da_faixa_e_422(self, cliente, relogio, dias):
        assert _get(cliente, "/metrics/involuntario/serie", dias=dias).status_code == 422


class TestCamposDeValor:

    def test_valor_cobranca_e_sempre_o_bruto_e_liquido_so_no_recuperado(
            self, cliente, relogio, base_conhecida):
        todos = {c["id"]: c for c in _get(cliente, "/ciclos", limite=200).json()["ciclos"]}
        esperado = {"a_rec_1": (100.00, 85.00), "a_rec_2": (250.50, 212.92),
                    "a_perd": (80.00, None), "a_desc": (60.00, None),
                    "a_aberto": (40.00, None), "a_out": (999.00, 849.15),
                    "a_ago": (500.00, 425.00)}
        for chave, (bruto, liquido) in esperado.items():
            linha = todos[base_conhecida[chave]]
            assert (linha["valor_cobranca"], linha["valor_liquido"]) == (bruto, liquido), chave
            assert "valor" not in linha and "fee" not in linha
        detalhe = _get(cliente, f"/ciclos/{base_conhecida['a_rec_2']}").json()
        assert detalhe["ciclo"]["valor_cobranca"] == 250.50
        assert detalhe["ciclo"]["valor_liquido"] == 212.92
        assert detalhe["linha_do_tempo"][-1]["dados"]["valor_liquido"] == 212.92
        assert "fee" not in json.dumps(detalhe)
        # As métricas somam o líquido — a mesma conta dos campos.
        mes = _get(cliente, "/metrics/involuntario/mes", mes="2026-09").json()
        assert mes["valor_liquido_recuperado"] == round(
            todos[base_conhecida["a_rec_1"]]["valor_liquido"]
            + todos[base_conhecida["a_rec_2"]]["valor_liquido"], 2)


class TestNenhumCicloSome:

    def test_perdido_e_descartado_aparecem_como_encerrados(self, cliente, relogio, base_conhecida):
        encerrados = _get(cliente, "/ciclos", status=cc.STATUS_ENCERRADO).json()["ciclos"]
        por_id = {c["id"]: c for c in encerrados}
        assert set(por_id) == {base_conhecida["a_perd"], base_conhecida["a_desc"]}
        assert por_id[base_conhecida["a_perd"]]["estado"] == cc.PERDIDO
        assert por_id[base_conhecida["a_desc"]]["motivo_descarte"] == "eprofit_nao_positivo"
        todos = _get(cliente, "/ciclos", limite=200).json()["ciclos"]
        assert {c["id"] for c in todos} == {v for k, v in base_conhecida.items() if k.startswith("a_")}
        assert {c["status"] for c in todos} == set(cc.STATUS_DA_TELA) - {cc.STATUS_EM_PROCESSO}
        _datas_com_fuso(todos)

    def test_descartado_na_linha_do_tempo_traz_o_motivo(self, cliente, relogio, base_conhecida):
        detalhe = _get(cliente, f"/ciclos/{base_conhecida['a_desc']}").json()
        fim = detalhe["linha_do_tempo"][-1]
        assert fim["tipo"] == "descartado" and fim["dados"]["motivo_descarte"] == "eprofit_nao_positivo"


# ══════════════════════════════════════════════════════════════════════════
# R12 em cada rota nova
# ══════════════════════════════════════════════════════════════════════════

class TestIsolamentoR12:

    def test_ciclo_de_outra_empresa_e_404_igual_ao_inexistente(self, cliente, relogio, base_conhecida):
        de_b = _get(cliente, f"/ciclos/{base_conhecida['b_rec']}", A)
        inexistente = _get(cliente, "/ciclos/999999", A)
        assert de_b.status_code == inexistente.status_code == 404
        assert de_b.json() == inexistente.json()
        assert _get(cliente, f"/ciclos/{base_conhecida['b_rec']}", B).status_code == 200

    def test_lista_de_a_nao_tem_ciclo_de_b(self, cliente, relogio, base_conhecida):
        ids_a = {c["id"] for c in _get(cliente, "/ciclos", A, limite=200).json()["ciclos"]}
        ids_b = {c["id"] for c in _get(cliente, "/ciclos", B, limite=200).json()["ciclos"]}
        assert base_conhecida["b_rec"] not in ids_a and ids_b == {base_conhecida["b_rec"]}
        # Nem pela busca por texto com o id da recorrência de B.
        assert _get(cliente, "/ciclos", A, q="RN_b1").json()["ciclos"] == []

    def test_metricas_de_a_nao_tem_valor_de_b(self, cliente, relogio, base_conhecida):
        a = _get(cliente, "/metrics/involuntario/mes", A, mes="2026-09").json()
        b = _get(cliente, "/metrics/involuntario/mes", B, mes="2026-09").json()
        assert a["valor_liquido_recuperado"] == 297.92
        assert b["valor_liquido_recuperado"] == round(7777.00 - 1166.55, 2)
        relogio(datetime(2026, 9, 30, 18))
        serie_b = _get(cliente, "/metrics/involuntario/serie", B, dias=30).json()
        assert round(sum(p["valor_liquido_recuperado"] for p in serie_b["pontos"]), 2) == 6610.45

    @pytest.mark.parametrize("caminho", ["/ciclos", "/ciclos/1", "/metrics/involuntario/mes",
                                         "/metrics/involuntario/serie"])
    def test_sem_token_e_401(self, cliente, caminho):
        assert cliente.get(caminho).status_code == 401

    @pytest.mark.parametrize("ciclo_id", ["0", "-1", "abc", str(2 ** 64)])
    def test_id_torto_e_422_nunca_500(self, cliente, relogio, ciclo_id):
        assert _get(cliente, f"/ciclos/{ciclo_id}").status_code == 422


# ══════════════════════════════════════════════════════════════════════════
# Listagem: paginação, filtros, validação
# ══════════════════════════════════════════════════════════════════════════

class TestListagem:

    def test_paginacao_por_cursor_sem_repetir_nem_pular(self, cliente, relogio):
        # Cinco ciclos com o MESMO atualizado_em: o desempate é o id.
        for i in range(5):
            cc.abrir_ciclo(A, f"RN_pag_{i}", 10.0 + i, "insufficient_funds", ABERTURA,
                           id_cobranca=f"inv_pag_{i}")
        vistos, cursor, paginas = [], None, 0
        while True:
            params = {"limite": 2, **({"cursor": cursor} if cursor else {})}
            corpo = _get(cliente, "/ciclos", **params).json()
            vistos += [c["id"] for c in corpo["ciclos"]]
            paginas += 1
            cursor = corpo["proximo_cursor"]
            if not corpo["tem_mais"]:
                assert cursor is None
                break
        assert paginas == 3 and len(vistos) == len(set(vistos)) == 5
        assert vistos == sorted(vistos, reverse=True)

    def test_filtros_de_periodo_e_texto(self, cliente, relogio, base_conhecida):
        setembro = _get(cliente, "/ciclos", desde="2026-09-01", ate="2026-10-01", limite=200).json()
        assert base_conhecida["a_perd"] not in {c["id"] for c in setembro["ciclos"]}
        assert base_conhecida["a_rec_1"] in {c["id"] for c in setembro["ciclos"]}
        por_prefixo = _get(cliente, "/ciclos", q="RN_m").json()["ciclos"]
        assert len(por_prefixo) == 7
        # `_` e `%` são literais na busca, não curingas.
        assert _get(cliente, "/ciclos", q="RN%").json()["ciclos"] == []
        assert _get(cliente, "/ciclos", q="RN_m1").json()["ciclos"][0]["id"] == base_conhecida["a_rec_1"]
        por_cobranca = _get(cliente, "/ciclos", q="inv_RN_m4").json()["ciclos"]
        assert [c["id"] for c in por_cobranca] == [base_conhecida["a_desc"]]

    def test_filtro_por_varios_status(self, cliente, relogio, base_conhecida):
        corpo = _get(cliente, "/ciclos", status="em_analise,recuperado", limite=200).json()
        assert {c["status"] for c in corpo["ciclos"]} == {cc.STATUS_EM_ANALISE, cc.STATUS_RECUPERADO}

    @pytest.mark.parametrize("params", [
        {"status": "perdido"}, {"limite": 0}, {"limite": 201}, {"desde": "ontem"},
        {"cursor": "nao-e-cursor"}, {"cursor": "eyJhIjoxfQ"}, {"q": "x" * 129},
    ])
    def test_parametro_torto_e_422(self, cliente, relogio, params):
        r = _get(cliente, "/ciclos", **params)
        assert r.status_code == 422, (params, r.text)

    def test_trilha_ambigua_com_dois_ciclos_do_mesmo_mandato_ao_mesmo_tempo(self, cliente, relogio):
        c1 = cc.abrir_ciclo(A, "RN_amb", 10.0, "insufficient_funds", ABERTURA, id_cobranca="inv_amb_1")
        cc.abrir_ciclo(A, "RN_amb", 20.0, "insufficient_funds", ABERTURA + timedelta(days=1),
                       id_cobranca="inv_amb_2")
        relogio(ABERTURA + timedelta(days=2))
        assert _get(cliente, f"/ciclos/{c1['id']}").json()["trilha_ambigua"] is True


# ══════════════════════════════════════════════════════════════════════════
# A tradução é uma só
# ══════════════════════════════════════════════════════════════════════════

class TestTraducaoDeStatus:

    def test_todo_estado_tem_status(self):
        for estado in cc.ESTADOS:
            for executada in (False, True):
                assert cc.status_da_tela(estado, executada) in cc.STATUS_DA_TELA

    def test_estado_desconhecido_levanta(self):
        with pytest.raises(ValueError):
            cc.status_da_tela("inventado", False)

    @pytest.mark.parametrize("estado", cc.ESTADOS)
    @pytest.mark.parametrize("executada", [False, True])
    def test_python_e_sql_dizem_o_mesmo(self, estado, executada):
        c = cc.abrir_ciclo(A, f"RN_tr_{estado}_{executada}", 10.0, "insufficient_funds",
                           ABERTURA, id_cobranca=f"inv_tr_{estado}_{executada}", estado=estado)
        if executada:
            cc.agendar_tentativas(c["id"], [{"numero": 1, "quando": ABERTURA, "valor": 10.0}], ABERTURA)
            cc.marcar_disparada(c["id"], 1, "ch_x", ABERTURA)
        pelo_banco = cc.ciclo_do_tenant(A, c["id"])["status"]
        assert pelo_banco == cc.status_da_tela(estado, executada)

    def test_tentativa_declarada_conta_como_executada(self):
        c = cc.abrir_ciclo(A, "RN_decl", 10.0, "insufficient_funds", ABERTURA, id_cobranca="inv_decl")
        cc.agendar_tentativas(c["id"], [{"numero": 1, "quando": ABERTURA, "valor": 10.0}], ABERTURA)
        cc.registrar_resultado(c["id"], 1, cc.FALHOU, ABERTURA)
        assert cc.ciclo_do_tenant(A, c["id"])["status"] == cc.STATUS_EM_PROCESSO

    def test_agendada_e_nao_disparada_e_em_analise(self):
        c = cc.abrir_ciclo(A, "RN_ag", 10.0, "insufficient_funds", ABERTURA, id_cobranca="inv_ag")
        cc.agendar_tentativas(c["id"], [{"numero": 1, "quando": ABERTURA, "valor": 10.0}], ABERTURA)
        assert cc.ciclo_do_tenant(A, c["id"])["status"] == cc.STATUS_EM_ANALISE


# ══════════════════════════════════════════════════════════════════════════
# 0.5: o que fecha antes da API key
# ══════════════════════════════════════════════════════════════════════════

class TestDeduplicacaoComTenant:

    def test_mesmo_par_de_ids_em_dois_tenants_sao_dois_eventos(self, cliente, relogio):
        corpo = _evento("RN_dup", "E_dup_0")
        assert _post(cliente, corpo, A)["pipeline"] is True
        assert _post(cliente, corpo, B)["pipeline"] is True, (
            "o evento do segundo tenant foi descartado como reenvio do primeiro")
        assert len(cc.ciclos_do_mandato(A, "RN_dup")) == len(cc.ciclos_do_mandato(B, "RN_dup")) == 1

    def test_reenvio_no_mesmo_tenant_continua_sendo_reenvio(self, cliente, relogio):
        corpo = _evento("RN_dup2", "E_dup2_0")
        assert _post(cliente, corpo, A)["pipeline"] is True
        segundo = _post(cliente, corpo, A)
        assert segundo["pipeline"] is False and segundo["motivo"] == "evento_ja_processado"

    def test_confirmacao_em_dois_tenants_conta_cada_uma_no_seu(self, cliente, relogio):
        for tenant in (A, B):
            _post(cliente, _evento("RN_dup3", "E_dup3_0"), tenant)
        pago = _evento("RN_dup3", "E_dup3_pago", "automatic_pix.charge_paid")
        assert _post(cliente, pago, A)["ciclo"] == "recuperado"
        assert _post(cliente, pago, B)["ciclo"] == "recuperado", (
            "a confirmação de B foi descartada como reenvio da de A")

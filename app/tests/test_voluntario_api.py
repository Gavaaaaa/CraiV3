"""tests/test_voluntario_api.py - Rodada 3, Fase 1: a pagina do voluntario.

O QUE ESTE ARQUIVO MEDE:

  - `GET /clientes/recentes`: a ordem (o atualizado por ultimo primeiro), a
    faixa, o motivo, quem decidiu e a abordagem; e que nenhum contato sai;
  - `GET /clientes/base`: os totais, a origem (API ou anexo) e a base vazia;
  - o cache da regua: uma leitura repetida nao recalcula, e QUALQUER escrita
    na base do tenant invalida;
  - V1 a V4 do valor mantido: a conta, o estorno no prazo, o cancelamento fora
    do prazo, e o mes fechado que nao e reescrito;
  - `GET /metrics/voluntario/mes` e `/serie`: o liquido, as contagens e a fee
    que nunca aparece (R11);
  - `GET /metrics/voluntario/regua-x-modelo`: vazio sem desfecho suficiente;
  - isolamento por tenant em cada rota nova, e 401 sem token.
"""

import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crai import config
from crai.api import app as app_module
from crai.api import datas, registro_acesso
from crai.api import voluntario as vol
from crai.churn_voluntary import batch_scoring as bs
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import insights_unificados as iu
from crai.churn_voluntary import mantido, origem_da_base
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import risk_scorer as rs
from crai.dunning import configuracao

A, B = "empresa-a", "empresa-b"
ROTAS = ("/clientes/recentes", "/clientes/base", "/metrics/voluntario/mes",
         "/metrics/voluntario/serie", "/metrics/voluntario/regua-x-modelo")

APP = Path(__file__).resolve().parents[1]
V3 = APP / "models" / "v3"


def _c(cid, mrr=300.0, dias=None, uso=None, **extra):
    return {"customer_id_externo": cid, "mrr": mrr, "billing_profile": "PJ",
            "days_since_last": dias, "features_used_30d": uso, **extra}


@pytest.fixture
def cliente(monkeypatch, supabase_falso):
    monkeypatch.setenv(datas.ENV_FUSO, "America/Sao_Paulo")
    monkeypatch.delenv(config.ENV_SUCCESS_FEE, raising=False)
    monkeypatch.delenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, raising=False)
    with TestClient(app_module.app) as c:
        c.projeto = supabase_falso
        yield c


def _get(cliente, tenant, caminho, **params):
    return cliente.get(caminho, params=params, headers=cliente.projeto.bearer(tenant, papel="owner"))


def _oferta(tenant, cid, oferta="desconto_20", mrr=500.0, canal="email", enviada=True,
            criticidade="critico"):
    """Um ciclo de retencao com oferta, como o grafo o grava no fim."""
    return rl.registrar_ciclo({
        "tenant_id": tenant, "user_id": f"user:{cid}", "event": "Cancellation Page Viewed",
        "props": {"mrr": mrr, "days_since_last": 20, "features_used_30d": 1},
        "risk_score": 0.93, "profile": "PJ", "criticality": criticidade,
        "offer_type": oferta, "channel": canal, "offer_sent": enviada, "accepted": None})


def _aceitar(tenant, cid, oferta="desconto_20", aceitou=True, origem="webhook"):
    assert rl.registrar_desfecho(tenant, f"user:{cid}", oferta, aceitou,
                                 origem=origem) is rl.ResultadoDesfecho.FECHADO


def _mover_aceite(ciclo_id, dias_atras):
    """Reescreve a data do desfecho no dataset (so o teste faz isto)."""
    quando = (datetime.now(timezone.utc) - timedelta(days=dias_atras)).isoformat(timespec="seconds")
    conn = sqlite3.connect(rl.caminho_do_banco())
    conn.execute("UPDATE ciclos_retencao SET desfecho_em = ? WHERE id = ?", (quando, ciclo_id))
    conn.commit()
    conn.close()
    return datas.para_local(quando)


def _mes_de(momento):
    return f"{momento.year:04d}-{momento.month:02d}"


def _chaves(valor):
    """Todas as chaves de um JSON, em qualquer profundidade."""
    if isinstance(valor, dict):
        for k, v in valor.items():
            yield k
            yield from _chaves(v)
    elif isinstance(valor, list):
        for v in valor:
            yield from _chaves(v)


# == GET /clientes/recentes ================================================

class TestRecentes:
    def test_o_atualizado_por_ultimo_vem_primeiro(self, cliente, monkeypatch):
        carimbo = iter(f"2026-09-0{i}T10:00:00+00:00" for i in range(1, 9))
        monkeypatch.setattr(ci, "_agora", lambda: next(carimbo))
        for cid in ("c1", "c2", "c3"):
            ci.upsert_um(A, _c(cid, dias=3, uso=5))
        # O PATCH carimba `atualizado_em`: c1 passa a ser o mais recente.
        ci.atualizar_parcial(A, "c1", {"mrr": 410.0})
        r = _get(cliente, A, "/clientes/recentes").json()
        assert [c["id"] for c in r["clientes"]] == ["c1", "c3", "c2"]
        assert r["total_na_base"] == 3
        assert r["clientes"][0]["mrr"] == 410.0
        # A base grava em UTC; a resposta sai no fuso da instalacao (o do ciclo).
        assert r["clientes"][0]["atualizado_em"] == "2026-09-04T07:00:00-03:00"

    def test_a_faixa_o_motivo_e_quem_decidiu(self, cliente):
        ci.gravar(A, [_c("ativo", dias=0, uso=9), _c("sumido", dias=60, uso=0),
                      _c("sem-dado")])
        por_id = {c["id"]: c for c in _get(cliente, A, "/clientes/recentes").json()["clientes"]}
        assert por_id["sumido"]["faixa"] == "grave"
        assert por_id["ativo"]["faixa"] == "sem_risco"
        assert por_id["sem-dado"]["faixa"] == "sem_dado"
        # Sem modelo ativo, quem decide e a regua; sem dado, ninguem decidiu.
        assert por_id["sumido"]["decidido_por"] == "regua"
        assert por_id["sem-dado"]["decidido_por"] is None
        assert por_id["sem-dado"]["posicao_no_ranking"] is None
        assert por_id["sumido"]["posicao_no_ranking"] == 1
        assert "60 dias" in por_id["sumido"]["motivo"]
        assert all(c["simulado"] is False for c in por_id.values())

    def test_a_abordagem_e_a_do_ultimo_ciclo_com_oferta(self, cliente):
        ci.gravar(A, [_c("c1", dias=30, uso=0), _c("c2", dias=30, uso=0),
                      _c("c3", dias=30, uso=0), _c("c4", dias=30, uso=0),
                      _c("c5", dias=1, uso=9)])
        _oferta(A, "c1", "desconto_10", canal="whatsapp")
        _oferta(A, "c2", "pausa_1_mes")
        _aceitar(A, "c2", "pausa_1_mes")
        _oferta(A, "c3", "desconto_20")
        _aceitar(A, "c3", "desconto_20", aceitou=False)
        _oferta(A, "c4", "pix_boleto_flash", enviada=False)
        ci.gravar(A, [_c(c, dias=30, uso=0) for c in ("c1", "c2", "c3", "c4")]
                  + [_c("c5", dias=1, uso=9)])
        por_id = {c["id"]: c for c in _get(cliente, A, "/clientes/recentes").json()["clientes"]}
        assert por_id["c1"]["abordagem"]["oferta"] == "desconto_10"
        assert por_id["c1"]["abordagem"]["oferta_legivel"] == "desconto de 10% por 3 meses"
        assert por_id["c1"]["abordagem"]["canal"] == "whatsapp"
        assert por_id["c1"]["abordagem"]["canal_legivel"] == "WhatsApp"
        assert por_id["c1"]["abordagem"]["situacao"] == "enviada"
        assert por_id["c2"]["abordagem"]["situacao"] == "aceita"
        assert por_id["c3"]["abordagem"]["situacao"] == "recusada"
        assert por_id["c4"]["abordagem"]["situacao"] == "aguardando"
        assert por_id["c5"]["abordagem"] is None

    def test_o_nome_sai_e_nenhum_contato_sai(self, cliente):
        ci.gravar(A, [_c("c1", dias=30, uso=0, email="pessoa@exemplo.com.br",
                         telefone="+5511988887777", nome="Maria Souza",
                         id_recorrencia="RN_abc123")])
        r = _get(cliente, A, "/clientes/recentes")
        assert r.json()["clientes"][0]["nome"] == "Maria Souza"
        texto = r.text
        for proibido in ("pessoa@exemplo.com.br", "5511988887777", "88887777"):
            assert proibido not in texto
        proibidas = {"email", "telefone", "phone", "cpf", "chave_pix", "fee"}
        assert not proibidas & set(_chaves(r.json()))

    def test_limite(self, cliente):
        ci.gravar(A, [_c(f"c{i:02d}", dias=i, uso=1) for i in range(15)])
        assert len(_get(cliente, A, "/clientes/recentes").json()["clientes"]) == 10
        assert len(_get(cliente, A, "/clientes/recentes", limite=3).json()["clientes"]) == 3
        for ruim in (0, 101, -1):
            r = _get(cliente, A, "/clientes/recentes", limite=ruim)
            assert r.status_code == 422
            assert r.json()["detail"]["motivo"] == "limite_invalido"

    def test_cancelado_sai_da_lista(self, cliente):
        ci.gravar(A, [_c("fica", dias=5, uso=2), _c("saiu", dias=50, uso=0)])
        ci.cancelar(A, "saiu")
        r = _get(cliente, A, "/clientes/recentes").json()
        assert [c["id"] for c in r["clientes"]] == ["fica"]

    def test_a_leitura_entra_no_registro_de_acesso(self, cliente):
        ci.gravar(A, [_c("c1", dias=5, uso=2)])
        cliente.get("/clientes/recentes", headers=cliente.projeto.bearer(A, papel="membro"))
        acessos = registro_acesso.acessos(A)
        assert [(a["rota"], a["papel"]) for a in acessos] == [("GET /clientes/recentes", "membro")]
        assert registro_acesso.acessos(B) == []


# == GET /clientes/base ====================================================

class TestBase:
    def test_sem_cliente_nenhum_a_base_e_nula(self, cliente):
        assert _get(cliente, A, "/clientes/base").json() == {"base": None}

    def test_os_totais(self, cliente):
        ci.gravar(A, [_c("a", dias=3, uso=4), _c("b", dias=9), _c("c"), _c("d", uso=2)])
        ci.cancelar(A, "d")
        base = _get(cliente, A, "/clientes/base").json()["base"]
        assert base["total"] == 3
        assert base["com_dados_comportamento"] == 2
        assert base["decididos_pelo_modelo"] == 0
        assert base["modelo_ativo"] is False
        # Gravada direto no modulo, sem passar por rota: a origem nao e conhecida.
        assert base["origem"] is None
        assert base["atualizada_em"] is not None

    def test_a_origem_e_api_depois_de_uma_rota_da_api(self, cliente):
        dono = cliente.projeto.bearer(A, papel="owner")
        assert cliente.post("/clientes", json=_c("c1", dias=1, uso=1), headers=dono).status_code == 200
        base = _get(cliente, A, "/clientes/base").json()["base"]
        assert base["origem"] == "api" and base["total"] == 1

    def test_a_origem_e_anexo_depois_da_planilha(self, cliente):
        dono = cliente.projeto.bearer(A, papel="owner")
        cliente.post("/clientes", json=_c("c1", dias=1, uso=1), headers=dono)
        csv = "customer_id_externo,mrr,billing_profile\nc2,100,PJ\nc3,200,CLT\n".encode()
        r = cliente.post("/clientes/importar", headers=dono,
                         files={"arquivo": ("base.csv", csv, "text/csv")})
        assert r.status_code == 200 and r.json()["importados"] == 2
        base = _get(cliente, A, "/clientes/base").json()["base"]
        assert base["origem"] == "anexo" and base["total"] == 3

    def test_requisicao_recusada_nao_muda_a_origem(self, cliente):
        dono = cliente.projeto.bearer(A, papel="owner")
        assert cliente.post("/clientes", json={"customer_id_externo": "x"},
                            headers=dono).status_code == 422
        assert cliente.patch("/clientes/nao-existe", json={"mrr": 1.0},
                             headers=dono).status_code == 404
        assert origem_da_base.ultima(A) is None

    def test_a_origem_de_uma_empresa_nao_e_a_da_outra(self, cliente):
        cliente.post("/clientes", json=_c("c1"), headers=cliente.projeto.bearer(A, papel="owner"))
        ci.gravar(B, [_c("c9")])
        assert _get(cliente, B, "/clientes/base").json()["base"]["origem"] is None


# == O cache da regua ======================================================

class TestCache:
    @pytest.fixture
    def contador(self, monkeypatch):
        chamadas = []
        original = iu.clientes_em_risco

        def contando(tenant_id, idioma="pt"):
            chamadas.append(tenant_id)
            return original(tenant_id, idioma)
        monkeypatch.setattr(iu, "clientes_em_risco", contando)
        return chamadas

    def test_leituras_repetidas_nao_recalculam(self, cliente, contador):
        ci.gravar(A, [_c("c1", dias=3, uso=4)])
        for _ in range(3):
            _get(cliente, A, "/clientes/recentes")
            _get(cliente, A, "/clientes/base")
            _get(cliente, A, "/metrics/voluntario/mes")
        assert contador == [A]

    @pytest.mark.parametrize("escrita", ["upsert", "lote", "patch", "cancelar", "apagar",
                                         "post", "delete", "importar"])
    def test_qualquer_escrita_na_base_invalida(self, cliente, contador, escrita):
        ci.gravar(A, [_c("c1", dias=3, uso=4), _c("c2", dias=3, uso=4)])
        antes = _get(cliente, A, "/clientes/recentes").json()
        assert contador == [A]
        dono = cliente.projeto.bearer(A, papel="owner")
        if escrita == "upsert":
            ci.upsert_um(A, _c("c1", mrr=999.0, dias=3, uso=4))
        elif escrita == "lote":
            ci.gravar(A, [_c("c1", mrr=999.0, dias=3, uso=4)])
        elif escrita == "patch":
            ci.atualizar_parcial(A, "c1", {"mrr": 999.0})
        elif escrita == "cancelar":
            ci.cancelar(A, "c1")
        elif escrita == "apagar":
            ci.apagar_tenant(A)
        elif escrita == "post":
            assert cliente.post("/clientes", json=_c("c1", mrr=999.0, dias=3, uso=4),
                                headers=dono).status_code == 200
        elif escrita == "delete":
            assert cliente.request("DELETE", "/clientes/c1", headers=dono).status_code == 200
        else:
            csv = "customer_id_externo,mrr,billing_profile\nc1,999,PJ\n".encode()
            assert cliente.post("/clientes/importar", headers=dono,
                                files={"arquivo": ("b.csv", csv, "text/csv")}).status_code == 200
        depois = _get(cliente, A, "/clientes/recentes").json()
        assert contador == [A, A], "a escrita nao invalidou o cache"
        assert depois != antes

    def test_escrita_de_outro_processo_invalida(self, cliente, contador):
        ci.gravar(A, [_c("c1", dias=3, uso=4)])
        _get(cliente, A, "/clientes/recentes")
        # Outro processo: grava direto no arquivo, sem passar pelo contador deste.
        conn = sqlite3.connect(ci._destino()[1])
        conn.execute(
            "INSERT INTO clientes_importados (tenant_id, customer_id_externo, mrr, "
            "billing_profile, importado_em) VALUES (?, 'de-fora', 10, 'PJ', ?)",
            (A, "2026-09-09T09:09:09+00:00"))
        conn.commit()
        conn.close()
        ids = {c["id"] for c in _get(cliente, A, "/clientes/recentes").json()["clientes"]}
        assert "de-fora" in ids and contador == [A, A]

    def test_evento_do_sdk_invalida(self, cliente, contador):
        ci.gravar(A, [_c("c1", dias=3, uso=4)])
        _get(cliente, A, "/clientes/recentes")
        _oferta(A, "c1")
        _get(cliente, A, "/clientes/recentes")
        _aceitar(A, "c1")
        _get(cliente, A, "/clientes/recentes")
        assert contador == [A, A, A]

    def test_a_escrita_de_uma_empresa_nao_recalcula_a_outra(self, cliente, contador):
        ci.gravar(A, [_c("c1", dias=3, uso=4)])
        ci.gravar(B, [_c("c9", dias=3, uso=4)])
        _get(cliente, A, "/clientes/recentes")
        _get(cliente, B, "/clientes/recentes")
        ci.upsert_um(B, _c("c9", mrr=1.0))
        _get(cliente, A, "/clientes/recentes")
        _get(cliente, B, "/clientes/recentes")
        assert contador == [A, B, B]

    def test_o_insights_antigo_continua_recalculando(self, cliente, contador):
        ci.gravar(A, [_c("c1", dias=3, uso=4)])
        dono = cliente.projeto.bearer(A, papel="owner")
        cliente.get("/insights", headers=dono)
        cliente.get("/insights", headers=dono)
        assert contador == [A, A]


# == V1 a V4: o valor mantido ==============================================

class TestAConta:
    def test_o_numero_de_meses_e_uma_constante_so(self):
        assert mantido.MESES_DE_MRR_MANTIDOS == 1
        fonte = (APP / "crai").rglob("*.py")
        donos = [p.name for p in fonte if "MESES_DE_MRR_MANTIDOS =" in p.read_text(encoding="utf-8")]
        assert donos == ["mantido.py"]

    @pytest.mark.parametrize("oferta,desconto,base", [
        ("desconto_10", 50.0, 450.0), ("desconto_20", 100.0, 400.0),
        ("pausa_1_mes", 500.0, 0.0), ("pix_boleto_flash", 0.0, 500.0)])
    def test_v1_um_mes_de_mrr_menos_o_desconto_liquido_da_fee(self, monkeypatch, oferta,
                                                             desconto, base):
        monkeypatch.delenv(config.ENV_SUCCESS_FEE, raising=False)
        monkeypatch.delenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, raising=False)
        v = mantido.valores_da_retencao(oferta, 500.0)
        assert v["desconto"] == desconto and v["valor_base"] == base
        assert v["fee"] == round(base * 0.15, 2)
        assert v["liquido"] == round(base - v["fee"], 2)

    def test_a_troca_dos_meses_e_de_uma_linha(self, monkeypatch):
        monkeypatch.delenv(config.ENV_SUCCESS_FEE, raising=False)
        monkeypatch.delenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, raising=False)
        monkeypatch.setattr(mantido, "MESES_DE_MRR_MANTIDOS", 6)
        v = mantido.valores_da_retencao("desconto_20", 500.0)
        # 6 meses de MRR; o desconto de 20% vale por 3 deles.
        assert v["meses"] == 6 and v["desconto"] == 300.0 and v["valor_base"] == 2700.0

    def test_a_fee_do_voluntario_tem_env_propria_e_cai_na_do_involuntario(self, monkeypatch):
        monkeypatch.setenv(config.ENV_SUCCESS_FEE, "0.25")
        monkeypatch.delenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, raising=False)
        assert config.success_fee_voluntario_pct() == 0.25
        monkeypatch.setenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, "0.20")
        assert config.success_fee_voluntario_pct() == 0.20
        assert config.success_fee_pct() == 0.25


class TestMantido:
    def test_v1_o_aceite_entra_no_mes_liquido_e_sem_fee_na_resposta(self, cliente):
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        _oferta(A, "c1", "desconto_20", mrr=500.0)
        antes = _get(cliente, A, "/metrics/voluntario/mes").json()
        assert antes["valor_liquido_mantido"] == 0 and antes["clientes_mantidos"] == 0
        assert antes["ofertas_enviadas"] == 1 and antes["ofertas_aceitas"] == 0
        _aceitar(A, "c1")
        r = _get(cliente, A, "/metrics/voluntario/mes")
        mes = r.json()
        # 500 - 20% = 400; menos a fee de 15% = 340.
        assert mes["valor_liquido_mantido"] == 340.0
        assert mes["clientes_mantidos"] == 1
        assert mes["ofertas_aceitas"] == 1
        assert mes["estornos"] == {"quantidade": 0, "valor_liquido_estornado": 0}
        assert mes["meses_de_mrr"] == 1 and mes["prazo_estorno_dias"] == 30
        assert mes["mes"] == _mes_de(datas.agora_local())
        assert "fee" not in set(_chaves(mes)) and "60.0" not in r.text

    def test_recusa_nao_e_valor(self, cliente):
        _oferta(A, "c1")
        _aceitar(A, "c1", aceitou=False)
        mes = _get(cliente, A, "/metrics/voluntario/mes").json()
        assert mes["valor_liquido_mantido"] == 0 and mes["clientes_mantidos"] == 0

    def test_v2_v3_cancelar_no_prazo_pelo_delete_estorna(self, cliente):
        dono = cliente.projeto.bearer(A, papel="owner")
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        _oferta(A, "c1")
        _aceitar(A, "c1")
        assert _get(cliente, A, "/metrics/voluntario/mes").json()["valor_liquido_mantido"] == 340.0
        assert cliente.request("DELETE", "/clientes/c1", headers=dono).status_code == 200
        # O DELETE ja sincroniza: a linha esta estornada antes de qualquer leitura.
        linha = _linhas(A)[0]
        assert linha["cancelamento_no_prazo"] == 1
        assert linha["valor_estornado"] == 400.0 and linha["fee_estornada"] == 60.0
        mes = _get(cliente, A, "/metrics/voluntario/mes").json()
        assert mes["valor_liquido_mantido"] == 0
        assert mes["clientes_mantidos"] == 1
        assert mes["estornos"] == {"quantidade": 1, "valor_liquido_estornado": 340.0}

    def test_e4_cancelar_fora_do_prazo_so_registra(self, cliente):
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        ciclo = _oferta(A, "c1")
        _aceitar(A, "c1")
        aceito = _mover_aceite(ciclo, 31)
        ci.cancelar(A, "c1")
        feito = mantido.sincronizar(A)
        assert feito["novas"] == 1 and feito["fora_do_prazo"] == 1 and feito["estornadas"] == 0
        linha = _linhas(A)[0]
        assert linha["cancelamento_no_prazo"] == 0 and linha["valor_estornado"] is None
        assert _get(cliente, A, "/metrics/voluntario/mes",
                    mes=_mes_de(aceito)).json()["valor_liquido_mantido"] == 340.0
        hoje = datas.agora_local()
        inicio = hoje - timedelta(days=1)
        assert len(mantido.cancelamentos_fora_do_prazo(A, inicio, hoje + timedelta(days=1))) == 1

    def test_o_dia_do_prazo_ainda_vale(self, cliente):
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        ciclo = _oferta(A, "c1")
        _aceitar(A, "c1")
        _mover_aceite(ciclo, 29)
        ci.cancelar(A, "c1")
        assert mantido.sincronizar(A)["estornadas"] == 1

    def test_e1_o_prazo_e_o_da_empresa(self, cliente):
        configuracao.gravar(A, {"prazo_estorno_dias": 5})
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        ciclo = _oferta(A, "c1")
        _aceitar(A, "c1")
        _mover_aceite(ciclo, 10)
        ci.cancelar(A, "c1")
        feito = mantido.sincronizar(A)
        assert feito["estornadas"] == 0 and feito["fora_do_prazo"] == 1

    def test_v4_o_mes_fechado_nao_e_reescrito(self, cliente):
        configuracao.gravar(A, {"prazo_estorno_dias": 60})
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        ciclo = _oferta(A, "c1")
        _aceitar(A, "c1")
        aceito = _mover_aceite(ciclo, 40)          # sempre num mes anterior ao de hoje
        mes_do_aceite, mes_de_hoje = _mes_de(aceito), _mes_de(datas.agora_local())
        assert mes_do_aceite != mes_de_hoje
        antes = _get(cliente, A, "/metrics/voluntario/mes", mes=mes_do_aceite).json()
        assert antes["valor_liquido_mantido"] == 340.0
        ci.cancelar(A, "c1")
        depois = _get(cliente, A, "/metrics/voluntario/mes", mes=mes_do_aceite).json()
        assert depois == antes, "o mes fechado mudou com um estorno de outro mes"
        hoje = _get(cliente, A, "/metrics/voluntario/mes").json()
        assert hoje["valor_liquido_mantido"] == -340.0
        assert hoje["clientes_mantidos"] == 0
        assert hoje["estornos"] == {"quantidade": 1, "valor_liquido_estornado": 340.0}

    def test_e7_sincronizar_de_novo_nao_conta_duas_vezes(self, cliente):
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        _oferta(A, "c1")
        _aceitar(A, "c1")
        ci.cancelar(A, "c1")
        assert mantido.sincronizar(A) == {"novas": 1, "sem_valor": 0, "estornadas": 1,
                                          "fora_do_prazo": 0}
        for _ in range(3):
            assert not any(mantido.sincronizar(A).values())
        assert len(_linhas(A)) == 1
        mes = _get(cliente, A, "/metrics/voluntario/mes").json()
        assert mes["estornos"]["quantidade"] == 1 and mes["valor_liquido_mantido"] == 0

    def test_reativar_e_cancelar_de_novo_nao_estorna_outra_vez(self, cliente):
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        _oferta(A, "c1")
        _aceitar(A, "c1")
        ci.cancelar(A, "c1")
        mantido.sincronizar(A)
        ci.upsert_um(A, _c("c1", mrr=500.0, dias=20, uso=1), reativar=True)
        ci.cancelar(A, "c1")
        assert not any(mantido.sincronizar(A).values())
        assert _get(cliente, A, "/metrics/voluntario/mes").json()["estornos"]["quantidade"] == 1

    def test_cancelamento_anterior_ao_aceite_nao_estorna(self, cliente):
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        ci.cancelar(A, "c1")
        conn = sqlite3.connect(ci._destino()[1])
        conn.execute("UPDATE clientes_importados SET cancelado_em = '2020-01-01T00:00:00+00:00'")
        conn.commit()
        conn.close()
        _oferta(A, "c1")
        _aceitar(A, "c1")
        feito = mantido.sincronizar(A)
        assert feito["novas"] == 1 and feito["estornadas"] == 0 and feito["fora_do_prazo"] == 0

    def test_a_linha_gravada_nao_muda_com_a_fee_nem_com_o_mrr(self, cliente, monkeypatch):
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        _oferta(A, "c1")
        _aceitar(A, "c1")
        assert _get(cliente, A, "/metrics/voluntario/mes").json()["valor_liquido_mantido"] == 340.0
        monkeypatch.setenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, "0.50")
        ci.atualizar_parcial(A, "c1", {"mrr": 9000.0})
        assert _get(cliente, A, "/metrics/voluntario/mes").json()["valor_liquido_mantido"] == 340.0

    def test_sem_mrr_no_ciclo_vale_o_da_base_e_sem_nenhum_nao_vira_valor(self, cliente):
        ci.gravar(A, [_c("na-base", mrr=200.0, dias=20, uso=1)])
        _oferta(A, "na-base", "pix_boleto_flash", mrr=None)
        _aceitar(A, "na-base", "pix_boleto_flash")
        _oferta(A, "fantasma", "pix_boleto_flash", mrr=None)
        _aceitar(A, "fantasma", "pix_boleto_flash")
        mes = _get(cliente, A, "/metrics/voluntario/mes").json()
        assert mes["clientes_mantidos"] == 1
        assert mes["valor_liquido_mantido"] == 170.0
        assert mes["aceites_sem_valor"] == 1
        assert mes["ofertas_aceitas"] == 2

    def test_aceite_sorteado_so_aparece_para_quem_pede_os_simulados(self, cliente):
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1)])
        _oferta(A, "c1")
        _aceitar(A, "c1", origem="simulacao")
        real = _get(cliente, A, "/metrics/voluntario/mes").json()
        assert real["valor_liquido_mantido"] == 0 and real["clientes_mantidos"] == 0
        assert real["ofertas_enviadas"] == 0 and real["ofertas_aceitas"] == 0
        com = _get(cliente, A, "/metrics/voluntario/mes", incluir_simulados="true").json()
        assert com["valor_liquido_mantido"] == 340.0 and com["ofertas_aceitas"] == 1
        serie = _get(cliente, A, "/metrics/voluntario/serie").json()["pontos"]
        assert all(p["valor_liquido_mantido"] == 0 for p in serie)

    def test_grave_e_preocupante_sao_os_da_base(self, cliente):
        ci.gravar(A, [_c("sumido", dias=60, uso=0), _c("ativo", dias=0, uso=9), _c("vazio")])
        mes = _get(cliente, A, "/metrics/voluntario/mes").json()
        assert mes["grave"] == 1 and mes["preocupante"] == 0

    def test_mes_invalido(self, cliente):
        for ruim in ("2026-13", "setembro", "2026-9", "2026-09-01"):
            r = _get(cliente, A, "/metrics/voluntario/mes", mes=ruim)
            assert r.status_code == 422 and r.json()["detail"]["motivo"] == "mes_invalido"


def _linhas(tenant):
    conn = sqlite3.connect(mantido.ciclo_cobranca.caminho_do_banco())
    conn.row_factory = sqlite3.Row
    try:
        return [dict(l) for l in conn.execute(
            "SELECT * FROM retencoes_mantidas WHERE tenant_id = ? ORDER BY id", (tenant,))]
    finally:
        conn.close()


class TestSerie:
    def test_o_aceite_no_dia_dele_e_o_estorno_no_dia_do_cancelamento(self, cliente):
        ci.gravar(A, [_c("c1", mrr=500.0, dias=20, uso=1), _c("c2", mrr=300.0, dias=20, uso=1)])
        ciclo = _oferta(A, "c1")
        _aceitar(A, "c1")
        aceito = _mover_aceite(ciclo, 3)
        _oferta(A, "c2", "pix_boleto_flash", mrr=300.0)
        _aceitar(A, "c2", "pix_boleto_flash")
        ci.cancelar(A, "c1")
        r = _get(cliente, A, "/metrics/voluntario/serie").json()
        assert r["dias"] == 30 and len(r["pontos"]) == 30
        por_dia = {p["dia"]: p for p in r["pontos"]}
        hoje = datas.agora_local().date().isoformat()
        assert por_dia[aceito.date().isoformat()]["valor_liquido_mantido"] == 340.0
        assert por_dia[aceito.date().isoformat()]["clientes_mantidos"] == 1
        # Hoje: +255 do c2 (300 menos 15%) e -340 do estorno do c1.
        assert por_dia[hoje]["valor_liquido_mantido"] == -85.0
        assert por_dia[hoje]["valor_liquido_estornado"] == 340.0
        assert round(sum(p["valor_liquido_mantido"] for p in r["pontos"]), 2) == 255.0
        assert "fee" not in set(_chaves(r))

    def test_dias(self, cliente):
        assert len(_get(cliente, A, "/metrics/voluntario/serie", dias=7).json()["pontos"]) == 7
        for ruim in (0, 366):
            r = _get(cliente, A, "/metrics/voluntario/serie", dias=ruim)
            assert r.status_code == 422 and r.json()["detail"]["motivo"] == "dias_invalido"


# == GET /metrics/voluntario/regua-x-modelo ================================

class TestReguaXModelo:
    def test_sem_modelo_ativo_devolve_vazio(self, cliente):
        ci.gravar(A, [_c(f"c{i}", dias=i, uso=i % 5) for i in range(40)])
        r = _get(cliente, A, "/metrics/voluntario/regua-x-modelo").json()
        assert r["comparacao"] is None and r["motivo_vazio"] == "modelo_inativo"
        assert r["dias"] == 30 and r["minimo_de_cancelamentos"] == vol.MINIMO_DE_CANCELAMENTOS

    @pytest.fixture
    def v3_ativo(self):
        if not (V3 / "voluntary_risk_v3.joblib").exists():
            pytest.skip("artefato do v3 ausente (app/models/v3)")
        meta = json.loads((V3 / "voluntary_risk_v3_meta.json").read_text(encoding="utf-8"))
        meta.update({"contrato": "v3", "contrato_de_producao": True})
        shutil.copyfile(V3 / "voluntary_risk_v3.joblib", rs.MODELO_PATH)
        rs.MODELO_META_PATH.write_text(json.dumps(meta), encoding="utf-8")
        assert rs.carregar_modelo(forcar=True) is True

    def _base(self, n=60):
        return [_c(f"c{i:03d}", mrr=200.0 + i, dias=float(i % 45), uso=float(i % 7),
                   logins_7d=i % 6, logins_30d=i % 20, tickets_30d=i % 4, tenure_days=100 + i)
                for i in range(n)]

    def test_base_pequena_devolve_vazio(self, cliente, v3_ativo):
        ci.gravar(A, self._base(10))
        r = _get(cliente, A, "/metrics/voluntario/regua-x-modelo").json()
        assert r["comparacao"] is None and r["motivo_vazio"] == "base_pequena"

    def test_poucos_cancelamentos_devolve_vazio(self, cliente, v3_ativo):
        ci.gravar(A, self._base())
        for cid in ("c040", "c041"):
            ci.cancelar(A, cid)
        r = _get(cliente, A, "/metrics/voluntario/regua-x-modelo").json()
        assert r["comparacao"] is None and r["motivo_vazio"] == "cancelamentos_insuficientes"

    def test_com_desfecho_suficiente_compara_sobre_a_mesma_base(self, cliente, v3_ativo):
        base = self._base()
        ci.gravar(A, base)
        cancelados = ["c040", "c041", "c042", "c043", "c044", "c010"]
        for cid in cancelados:
            ci.cancelar(A, cid)
        r = _get(cliente, A, "/metrics/voluntario/regua-x-modelo").json()
        comp = r["comparacao"]
        assert r["motivo_vazio"] is None
        assert comp["clientes_com_dados"] == 60 and comp["cancelamentos"] == 6
        # A mesma conta, feita por fora, sobre a base de antes dos cancelamentos.
        todos = ci.listar(A, incluir_cancelados=True)
        regua = bs.regua_da_base(todos)
        graves_regua = {c["customer_id_externo"] for c in todos
                        if bs.criticidade_pela_regua(c, regua) == "critico"}
        graves_modelo = {l["customer_id_externo"] for l in bs.pontuar_lista(todos)
                         if l["criticality"] == "critico"}
        assert comp["regua"] == {"marcou_grave": len(graves_regua),
                                 "avisou_antes": len(graves_regua & set(cancelados))}
        assert comp["modelo"] == {"marcou_grave": len(graves_modelo),
                                  "avisou_antes": len(graves_modelo & set(cancelados))}
        for lado in ("regua", "modelo"):
            assert 0 <= comp[lado]["avisou_antes"] <= comp["cancelamentos"]

    def test_cancelamento_antigo_fica_fora_do_periodo(self, cliente, v3_ativo):
        ci.gravar(A, self._base())
        for cid in ("c040", "c041", "c042", "c043", "c044", "c010"):
            ci.cancelar(A, cid)
        conn = sqlite3.connect(ci._destino()[1])
        conn.execute("UPDATE clientes_importados SET cancelado_em = '2020-01-01T00:00:00+00:00' "
                     "WHERE cancelado_em IS NOT NULL")
        conn.commit()
        conn.close()
        iu.esquecer_cache()
        r = _get(cliente, A, "/metrics/voluntario/regua-x-modelo").json()
        assert r["comparacao"] is None and r["motivo_vazio"] == "cancelamentos_insuficientes"

    def test_a_criticidade_pela_regua_e_a_do_ramo_da_regua(self):
        base = [_c(f"c{i}", dias=float(i), uso=float(i % 7)) for i in range(40)]
        regua = bs.regua_da_base(base)
        assert regua is not None
        for c in base:
            assert bs.criticidade_pela_regua(c, regua) == bs.pontuar_cliente(c, regua)["criticality"]
        assert bs.criticidade_pela_regua(_c("vazio"), regua) == bs.CRITICIDADE_SEM_DADO


# == Isolamento e autenticacao, rota por rota ==============================

class TestIsolamento:
    @pytest.fixture
    def duas_empresas(self, cliente):
        ci.gravar(A, [_c("a1", mrr=500.0, dias=40, uso=0, nome="Cliente de A")])
        ci.gravar(B, [_c("b1", mrr=900.0, dias=50, uso=0, nome="Cliente de B"),
                      _c("b2", mrr=700.0, dias=45, uso=0)])
        _oferta(B, "b1", mrr=900.0)
        _aceitar(B, "b1")
        cliente.post("/clientes", json=_c("b3", dias=1, uso=1),
                     headers=cliente.projeto.bearer(B, papel="owner"))
        return cliente

    @pytest.mark.parametrize("rota", ROTAS)
    def test_nada_de_b_aparece_para_a(self, duas_empresas, rota):
        r = _get(duas_empresas, A, rota)
        assert r.status_code == 200
        for de_b in ("b1", "b2", "b3", "Cliente de B", "900"):
            assert de_b not in r.text, (rota, de_b)

    def test_os_numeros_de_a_sao_so_os_de_a(self, duas_empresas):
        assert _get(duas_empresas, A, "/clientes/recentes").json()["total_na_base"] == 1
        assert _get(duas_empresas, A, "/clientes/base").json()["base"]["total"] == 1
        mes = _get(duas_empresas, A, "/metrics/voluntario/mes").json()
        assert mes["valor_liquido_mantido"] == 0 and mes["ofertas_enviadas"] == 0
        assert mes["grave"] == 1
        de_b = _get(duas_empresas, B, "/metrics/voluntario/mes").json()
        assert de_b["valor_liquido_mantido"] == 612.0 and de_b["clientes_mantidos"] == 1

    def test_o_cancelamento_de_a_nao_estorna_a_retencao_de_b(self, duas_empresas):
        ci.gravar(A, [_c("b1", mrr=1.0)])
        assert duas_empresas.request(
            "DELETE", "/clientes/b1",
            headers=duas_empresas.projeto.bearer(A, papel="owner")).status_code == 200
        de_b = _get(duas_empresas, B, "/metrics/voluntario/mes").json()
        assert de_b["valor_liquido_mantido"] == 612.0 and de_b["estornos"]["quantidade"] == 0

    @pytest.mark.parametrize("rota", ROTAS)
    def test_sem_token_e_401(self, cliente, rota):
        assert cliente.get(rota).status_code == 401

    @pytest.mark.parametrize("rota", ROTAS)
    def test_o_tenant_da_query_e_ignorado(self, duas_empresas, rota):
        r = _get(duas_empresas, A, rota, tenant_id=B)
        assert r.status_code == 200
        for de_b in ("b1", "b2", "Cliente de B"):
            assert de_b not in r.text

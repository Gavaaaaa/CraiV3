"""tests/test_voluntario_ajuste_fino.py - Rodada 4, Fase 1: o ajuste fino do conserto do voluntario.

As seis decisoes do Crai sobre o que o conserto de 05/10 deixou em aberto:

  1. a "oferta mais leve" nao pode ser a troca para Pix ou boleto: e a de menor
     custo entre as ofertas de retencao de verdade;
  2. evento de intencao explicita leva a oferta do bandit, qualquer que seja a
     faixa; a mais leve fica so para o preocupante que veio pela posicao na base;
  3. o disparo em lote usa a mesma regra de intensidade;
  4. o segundo evento do mesmo cliente nao regrava na trilha as decisoes do
     primeiro (no caminho da regua e no do modelo);
  5. na lista "Clientes em risco", o motivo de quem veio por evento diz quem
     decidiu de verdade;
  6. a referencia de posicao da empresa fica em disco (so quantis) e sobrevive
     ao reinicio.

Os auxiliares sao os de `test_intervencao_modelo_v3.py` (o modelo v3 copiado de
`models/v3/` para a pasta temporaria, a rota `POST /eventos` com a chave de API).
"""

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.api import relogio
from crai.churn_voluntary import batch_scoring as bs
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import disparo_lote as dl
from crai.churn_voluntary import insights_unificados as iu
from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import risk_scorer as rs
from crai.churn_voluntary import voluntary_agent as va
from tests.test_intervencao_modelo_v3 import (  # noqa: F401 - as fixtures entram pelo nome
    A, ABANDONO, CANCELAMENTO, REBAIXAR, REF_TODOS_ABAIXO, REF_TODOS_ACIMA, SAUDAVEL, SESSAO, V3,
    _adiantar, _ambiente, _chave, _decisoes, _evento, _instalar, _linhas, _login, _ofertas,
    _ref_que_deixa_preocupante, cliente, producao)

pytestmark = pytest.mark.skipif(not (V3 / "voluntary_risk_v3.joblib").exists(),
                                reason="artefato do v3 ausente (app/models/v3)")

B = "empresa-b"
PIX = "pix_boleto_flash"
SEM_USO = {"mrr": 500.0, "billing_profile": "PJ"}


def _rodada(mrr=500.0, perfil="PJ"):
    return va._bandit.classificar_ofertas(A, perfil, 0.3, mrr=mrr)


def _pipeline(user, evento, props, tenant=A):
    return asyncio.run(app_module._run_voluntary_pipeline(user, evento, dict(props), tenant_id=tenant))


def _tipos(tenant=A) -> list:
    return [d["tipo_decisao"] for d in _decisoes(tenant)]


def _base(n=60, **extra):
    return [{"customer_id_externo": f"b{i:03d}", "mrr": 300.0 + 10 * i, "billing_profile": "PJ",
             "days_since_last": float(i % 40), "features_used_30d": float(i % 9),
             "logins_30d": float(30 - i % 30), **extra} for i in range(n)]


# == 1. A oferta mais leve nao e a troca para Pix ou boleto =================

class TestOfertaMaisLeve:
    def test_pix_ou_boleto_fica_fora_da_comparacao(self):
        assert va.OFERTAS_FORA_DA_MAIS_LEVE == {PIX}
        rodada = [{"offer": PIX, "custo": 2.0}, {"offer": "pausa_1_mes", "custo": 500.0},
                  {"offer": "desconto_20", "custo": 300.0}, {"offer": "desconto_10", "custo": 150.0}]
        assert va.oferta_mais_leve(rodada)["offer"] == "desconto_10"
        assert [l["offer"] for l in va.ofertas_de_retencao(rodada)] == [
            "pausa_1_mes", "desconto_20", "desconto_10"]

    @pytest.mark.parametrize("perfil", ob.PROFILES)
    @pytest.mark.parametrize("mrr", [50.0, 300.0, 1200.0, 9000.0])
    def test_com_as_ofertas_de_hoje_e_sempre_o_desconto_de_10(self, perfil, mrr):
        for _ in range(20):                              # o sorteio do bandit muda a ordem
            rodada = _rodada(mrr, perfil)
            assert {l["offer"] for l in rodada} == set(ob.OFFERS)
            assert va.oferta_mais_leve(rodada)["offer"] == "desconto_10"

    def test_o_custo_e_o_do_bandit(self):
        rodada = _rodada(800.0)
        leve = va.oferta_mais_leve(rodada)
        assert leve["custo"] == round(ob.offer_cost(leve["offer"], 800.0), 2)
        assert leve["custo"] == min(l["custo"] for l in rodada if l["offer"] != PIX)

    def test_mais_leve_sai_das_ofertas_de_retencao_e_sao_elas_as_consideradas(self):
        rodada = _rodada()
        escolhida, consideradas = va.escolher_pela_intensidade(rodada, va.INTENSIDADE_MAIS_LEVE)
        assert escolhida["offer"] == "desconto_10"
        assert [c["offer"] for c in consideradas] == [l["offer"] for l in rodada if l["offer"] != PIX]
        assert len(consideradas) == va.N_CANDIDATAS and escolhida in consideradas

    @pytest.mark.parametrize("intensidade", [va.INTENSIDADE_DO_BANDIT, None])
    def test_quando_e_o_bandit_que_escolhe_nada_muda_e_ele_pode_escolher_pix(self, intensidade):
        rodada = [{"offer": PIX, "custo": 2.0}, {"offer": "pausa_1_mes", "custo": 500.0},
                  {"offer": "desconto_20", "custo": 300.0}, {"offer": "desconto_10", "custo": 150.0}]
        escolhida, consideradas = va.escolher_pela_intensidade(rodada, intensidade)
        assert escolhida is rodada[0] and consideradas == rodada[:va.N_CANDIDATAS]


# == 2. Intencao explicita leva a oferta do bandit ==========================

class TestIntensidade:
    @pytest.mark.parametrize("faixa", ["critico", "alto", "padrao"])
    def test_intencao_explicita_leva_a_do_bandit_em_qualquer_faixa(self, faixa):
        assert va.intensidade_da_intervencao(va.REGRA_INTENCAO_EXPLICITA, faixa) == va.INTENSIDADE_DO_BANDIT

    def test_pela_posicao_grave_leva_a_do_bandit_e_preocupante_a_mais_leve(self):
        regra = va.REGRA_POSICAO_NA_BASE
        assert va.intensidade_da_intervencao(regra, "critico") == va.INTENSIDADE_DO_BANDIT
        assert va.intensidade_da_intervencao(regra, "alto") == va.INTENSIDADE_MAIS_LEVE

    @pytest.mark.parametrize("evento", [CANCELAMENTO, REBAIXAR])
    def test_preocupante_pela_posicao_com_evento_de_intencao_leva_a_do_bandit(self, cliente, evento):
        _instalar()
        _instalar(_ref_que_deixa_preocupante(evento, ABANDONO))
        _evento(cliente, _chave(cliente), evento=evento, props=ABANDONO)
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]["saida"]
        assert risco["criticality"] == "alto"
        assert risco["regra_de_intervencao"] == va.REGRA_INTENCAO_EXPLICITA
        saida = _decisoes(tipo=rl.TIPO_OFERTA)[0]["saida"]
        assert saida["intensidade"] == va.INTENSIDADE_DO_BANDIT
        assert saida["offer_type"] == saida["ofertas_consideradas"][0]["offer"]
        assert "evento de intenção explícita" in _decisoes(tipo=rl.TIPO_OFERTA)[0]["explicacao"]

    def test_o_mesmo_cliente_preocupante_sem_o_evento_de_intencao_leva_a_mais_leve(self, cliente):
        _instalar()
        _instalar(_ref_que_deixa_preocupante(SESSAO, ABANDONO))
        _evento(cliente, _chave(cliente), evento=SESSAO, props=ABANDONO)
        saida = _decisoes(tipo=rl.TIPO_OFERTA)[0]["saida"]
        assert saida["intensidade"] == va.INTENSIDADE_MAIS_LEVE
        assert saida["offer_type"] == "desconto_10"
        assert PIX not in [c["offer"] for c in saida["ofertas_consideradas"]]
        assert _linhas()[0]["offer_type"] == "desconto_10"

    def test_na_regua_o_pipeline_de_eventos_continua_dando_a_primeira_do_bandit(self, cliente):
        # Sem modelo, a intensidade pela faixa NAO entra no pipeline de eventos: 0,75 (alto).
        _evento(cliente, _chave(cliente), evento=REBAIXAR, props=SEM_USO)
        saida = _decisoes(tipo=rl.TIPO_OFERTA)[0]["saida"]
        assert set(saida) == {"offer_type", "p_estimado", "ofertas_consideradas"}
        assert saida["offer_type"] == saida["ofertas_consideradas"][0]["offer"]


# == 3. O disparo em lote usa a mesma regra de intensidade ==================

def _c(cid, mrr=300.0, perfil="CLT", dias=None, uso=None, **extra):
    return {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil,
            "days_since_last": dias, "features_used_30d": uso, **extra}


# Regua global (lista pequena): 45 dias e 0 funcionalidades da 1,0 (critico);
# 30 dias e 2 funcionalidades da 0,88 (alto).
LOTE = [_c("grave-1", dias=45, uso=0), _c("grave-2", dias=45, uso=0, mrr=900.0, perfil="PJ"),
        _c("preocupante-1", dias=30, uso=2, mrr=450.0, perfil="PJ"),
        _c("preocupante-2", dias=30, uso=2, mrr=120.0, perfil="freelancer"),
        _c("sem-risco", dias=0, uso=30)]


class TestLote:
    @pytest.fixture
    def lote(self):
        with TestClient(app_module.app, raise_server_exceptions=False) as c:
            r = c.post("/simulate/painel/disparo-lote", json={"clientes": LOTE})
            assert r.status_code == 200, r.text
            return {x["customer_id_externo"]: x for x in r.json()["clientes"]}

    def test_grave_leva_a_do_bandit_e_preocupante_a_mais_leve(self, lote):
        assert set(lote) == {"grave-1", "grave-2", "preocupante-1", "preocupante-2"}
        for cid in ("grave-1", "grave-2"):
            assert lote[cid]["criticality"] == "critico"
            candidatas = lote[cid]["candidatas"]
            assert candidatas[0]["escolhida"] and candidatas[0]["oferta"] == lote[cid]["offer_type"]
            assert candidatas[0]["motivo_codigo"] == "maior_eprofit"
        for cid in ("preocupante-1", "preocupante-2"):
            assert lote[cid]["criticality"] == "alto"
            assert lote[cid]["offer_type"] == "desconto_10"
            candidatas = lote[cid]["candidatas"]
            assert PIX not in [c["oferta"] for c in candidatas]
            vencedora = [c for c in candidatas if c["escolhida"]]
            assert len(vencedora) == 1 and vencedora[0]["oferta"] == "desconto_10"
            assert vencedora[0]["motivo_codigo"] == "menor_custo"
            assert "desconto" in lote[cid]["mensagem"] or "10%" in lote[cid]["mensagem"]

    @pytest.mark.parametrize("faixa, intensidade", [("critico", va.INTENSIDADE_DO_BANDIT),
                                                   ("alto", va.INTENSIDADE_MAIS_LEVE)])
    def test_a_trilha_do_lote_grava_a_intensidade(self, faixa, intensidade):
        linha = {**_c("x", dias=30, uso=1, mrr=600.0, perfil="PJ"), "risk_score": 0.8,
                 "criticality": faixa}
        relatorio, estado = asyncio.run(dl.tratar(linha, A))
        oferta = next(d for d in estado["decisoes"] if d["tipo_decisao"] == rl.TIPO_OFERTA)
        assert oferta["saida"]["intensidade"] == intensidade
        assert oferta["saida"]["offer_type"] == relatorio["offer_type"] == estado["offer_type"]
        assert "regra_de_intervencao" not in oferta["saida"], "essa chave e do pipeline de eventos"
        consideradas = [c["offer"] for c in oferta["saida"]["ofertas_consideradas"]]
        if faixa == "alto":
            assert relatorio["offer_type"] == "desconto_10" and PIX not in consideradas
            assert "preocupante, e não grave" in oferta["explicacao"]
        else:
            assert relatorio["offer_type"] == consideradas[0]
        assert estado["intensidade_da_oferta"] == intensidade

    def test_o_criterio_de_quem_entra_no_lote_nao_mudou(self):
        assert dl.CRITERIO_INCLUSAO == ("critico", "alto")


# == 4. A trilha nao repete as decisoes do evento anterior ==================

UM_EVENTO_COM_OFERTA = [rl.TIPO_RISCO, rl.TIPO_OFERTA, rl.TIPO_CANAL]


class TestTrilhaSemDuplicata:
    def test_regua_dois_eventos_do_mesmo_cliente_com_o_limite_de_30_dias(self, cliente, producao):
        chave = _chave(cliente)
        _evento(cliente, chave, evento=CANCELAMENTO, props=SEM_USO, messageId="e1")
        assert _tipos() == UM_EVENTO_COM_OFERTA
        _evento(cliente, chave, evento=CANCELAMENTO, props=SEM_USO, messageId="e2")
        # O segundo evento: o risco, e a decisao de nao ofertar (o limite). Nada do primeiro de novo.
        assert _tipos() == UM_EVENTO_COM_OFERTA + [rl.TIPO_RISCO, rl.TIPO_OFERTA]
        decisoes = _decisoes()
        assert decisoes[4]["saida"]["regra"] == va.REGRA_DO_LIMITE_DE_CONTATO
        assert len({d["id"] for d in decisoes}) == 5
        assert len(_linhas()) == 2 and len(_ofertas()) == 1
        assert rl.verificar_cadeia(A) == {**rl.verificar_cadeia(A), "integra": True, "linhas": 5}

    def test_regua_tres_eventos_fora_do_limite_deixam_tres_vezes_as_tres_linhas(self, cliente,
                                                                             producao, monkeypatch):
        chave = _chave(cliente)
        for i in range(3):
            _adiantar(monkeypatch, days=31 * i)          # cada evento depois do intervalo de 30 dias
            _evento(cliente, chave, evento=CANCELAMENTO, props=SEM_USO, messageId=f"e{i}")
            assert _tipos() == UM_EVENTO_COM_OFERTA * (i + 1)
        assert rl.verificar_cadeia(A)["integra"] is True and len(_linhas()) == 3

    def test_modelo_dois_eventos_do_mesmo_cliente(self, cliente, producao, monkeypatch):
        _instalar(REF_TODOS_ABAIXO)
        chave = _chave(cliente)
        _evento(cliente, chave, evento=SESSAO, props=ABANDONO, messageId="e1")
        _evento(cliente, chave, evento=CANCELAMENTO, props=ABANDONO, messageId="e2")
        assert _tipos() == UM_EVENTO_COM_OFERTA + [rl.TIPO_RISCO, rl.TIPO_OFERTA]
        riscos = _decisoes(tipo=rl.TIPO_RISCO)
        assert [d["saida"]["regra_de_intervencao"] for d in riscos] == [
            va.REGRA_POSICAO_NA_BASE, va.REGRA_INTENCAO_EXPLICITA]
        assert [d["entradas"]["event"] for d in riscos] == [SESSAO, CANCELAMENTO]
        _adiantar(monkeypatch, days=30, minutes=1)
        _evento(cliente, chave, evento=SESSAO, props=ABANDONO, messageId="e3")
        assert _tipos() == UM_EVENTO_COM_OFERTA + [rl.TIPO_RISCO, rl.TIPO_OFERTA] + UM_EVENTO_COM_OFERTA
        cadeia = rl.verificar_cadeia(A)
        assert cadeia["integra"] is True and cadeia["linhas"] == 8

    def test_modelo_evento_sem_intervencao_depois_de_um_com_oferta(self, cliente):
        _instalar(REF_TODOS_ABAIXO)
        chave = _chave(cliente)
        _evento(cliente, chave, evento=CANCELAMENTO, props=SAUDAVEL, messageId="e1")
        _evento(cliente, chave, evento=SESSAO, props=SAUDAVEL, messageId="e2")   # sem sinal: nao intervem
        assert _tipos() == UM_EVENTO_COM_OFERTA + [rl.TIPO_RISCO]
        assert rl.verificar_cadeia(A)["integra"] is True

    def test_clientes_diferentes_nao_se_misturam(self, cliente):
        chave = _chave(cliente)
        _evento(cliente, chave, user="c-1", evento=CANCELAMENTO, props=SEM_USO)
        _evento(cliente, chave, user="c-2", evento=CANCELAMENTO, props=SEM_USO)
        assert [d["sujeito_id"] for d in _decisoes()] == ["user:c-1"] * 3 + ["user:c-2"] * 3

    def test_o_estado_de_um_evento_sem_oferta_nao_herda_o_do_anterior(self):
        primeiro = _pipeline("user:h-1", CANCELAMENTO, SEM_USO)
        assert primeiro["candidatas"] and primeiro["ofertas_consideradas"] and primeiro["canais_considerados"]
        assert len(primeiro["decisoes"]) == 3
        segundo = _pipeline("user:h-1", SESSAO, SEM_USO)             # risco 0: nao intervem
        assert segundo["offer_type"] is None and segundo["risk_score"] == 0.0
        assert [d["tipo_decisao"] for d in segundo["decisoes"]] == [rl.TIPO_RISCO]
        for chave in ("candidatas", "ofertas_consideradas", "canais_considerados", "sem_oferta_por"):
            assert segundo.get(chave) is None, chave

    def test_o_motivo_de_nao_ofertar_e_o_deste_evento(self, producao):
        _pipeline("user:h-2", CANCELAMENTO, SEM_USO)
        segundo = _pipeline("user:h-2", CANCELAMENTO, SEM_USO)
        assert segundo["sem_oferta_por"] == va.MOTIVO_LIMITE_DE_CONTATO
        terceiro = _pipeline("user:h-2", SESSAO, SEM_USO)
        assert terceiro["sem_oferta_por"] is None, "risco 0: nao foi o limite que segurou"


# == 5. O motivo de quem veio por evento diz quem decidiu ===================

def _ranking(tenant=A, idioma="pt") -> dict:
    return {l["customer_id_externo"]: l for l in iu.clientes_em_risco(tenant, idioma)}


class TestMotivoDeQuemVeioPorEvento:
    def test_grave_pelo_modelo_nao_diz_critico_pelo_valor_da_conta(self, cliente):
        _instalar(REF_TODOS_ABAIXO)
        _evento(cliente, _chave(cliente), user="m-1", evento=SESSAO, props=ABANDONO)
        linha = _ranking()["m-1"]
        assert linha["criticality"] == "critico" and linha["risk_score"] < 0.60
        assert linha["risco_decidido_por"] == bs.DECIDIDO_MODELO
        assert linha["posicao_na_base"] >= 0.90
        assert "entre os 10% de maior risco da sua base, pelo modelo" in linha["explicacao"]
        assert "pelo valor da conta" not in linha["explicacao"]
        assert linha["explicacao"].startswith(f"último evento: {SESSAO}; ")
        # Na rota da lista: quem decidiu, e o motivo.
        r = cliente.get("/clientes/recentes", headers=_login(cliente)).json()["clientes"]
        da_rota = next(c for c in r if c["id"] == "m-1")
        assert da_rota["faixa"] == "grave" and da_rota["decidido_por"] == "modelo"
        assert "pelo valor da conta" not in da_rota["motivo"] and "pelo modelo" in da_rota["motivo"]

    def test_intencao_explicita_diz_que_foi_o_evento_e_nao_a_pontuacao(self, cliente):
        _instalar(REF_TODOS_ACIMA)
        _evento(cliente, _chave(cliente), user="m-2", evento=CANCELAMENTO, props=SAUDAVEL)
        linha = _ranking()["m-2"]
        assert linha["criticality"] == "padrao" and linha["risco_decidido_por"] == bs.DECIDIDO_MODELO
        assert "fora dos 30% de maior risco da sua base, pelo modelo" in linha["explicacao"]
        assert linha["explicacao"].endswith(
            "o sistema agiu por intenção explícita do cliente (o evento), e não pela pontuação de risco")
        assert "pelo valor da conta" not in linha["explicacao"]
        assert "explicit intent" in _ranking(idioma="en")["m-2"]["explicacao"]

    def test_quando_a_regua_decide_a_frase_e_a_de_sempre(self, cliente):
        chave = _chave(cliente)
        _evento(cliente, chave, user="r-1", evento=CANCELAMENTO, props=SEM_USO)           # 0,90
        _evento(cliente, chave, user="r-2", evento=SESSAO,
                props={"mrr": 2500.0, "billing_profile": "PJ", "days_since_last": 1,
                       "features_used_30d": 9})                                          # critico pelo valor
        ranking = _ranking()
        assert ranking["r-1"]["risco_decidido_por"] == bs.DECIDIDO_REGRA
        assert "pelo valor da conta" not in ranking["r-1"]["explicacao"]
        assert "intenção explícita" not in ranking["r-1"]["explicacao"], "na regua nao ha essa regra"
        assert ranking["r-2"]["criticality"] == "critico"
        assert ranking["r-2"]["explicacao"].endswith("crítico pelo valor da conta, não pelo risco")
        assert ranking["r-2"]["posicao_na_base"] is None
        esperada = bs.explicar({"customer_id_externo": "r-2", "mrr": 2500.0, "billing_profile": "PJ",
                                "days_since_last": 1.0, "features_used_30d": 9.0},
                               ranking["r-2"]["risk_score"], "critico", None)
        assert ranking["r-2"]["explicacao"] == f"último evento: {SESSAO}; {esperada}"

    def test_sem_a_decisao_na_trilha_a_frase_nao_afirma_o_que_nao_sabe(self, cliente, monkeypatch):
        _instalar(REF_TODOS_ABAIXO)
        _evento(cliente, _chave(cliente), user="m-3", evento=SESSAO, props=ABANDONO)
        monkeypatch.setattr(rl, "ultimas_decisoes_de_risco", lambda tenant: {})
        linha = _ranking()["m-3"]
        assert linha["criticality"] == "critico" and linha["risco_decidido_por"] is None
        assert "pelo valor da conta" not in linha["explicacao"], "mensalidade de R$ 500 nao e alto valor"
        assert "pelo modelo" not in linha["explicacao"]
        assert "sem login há 30 dias" in linha["explicacao"]

    def test_a_decisao_de_outro_evento_nao_serve(self):
        ciclo = {"user_id": "user:x", "event": SESSAO, "risk_score": 0.3123}
        boa = {"modelo": rs.NOME_DO_MODELO, "entradas": {"event": SESSAO}, "saida": {"risk_score": 0.3123}}
        assert iu._decisao_deste_ciclo(ciclo, boa) is boa
        assert iu._decisao_deste_ciclo(ciclo, {**boa, "saida": {"risk_score": 0.31}}) is None
        assert iu._decisao_deste_ciclo(ciclo, {**boa, "entradas": {"event": CANCELAMENTO}}) is None
        assert iu._decisao_deste_ciclo(ciclo, None) is None
        assert iu._decisao_deste_ciclo({**ciclo, "risk_score": None}, boa) is None
        # O disparo em lote nao leva `event` nas entradas da decisao de risco.
        do_lote = {"modelo": rl.MODELO_REGRA, "entradas": {}, "saida": {"risk_score": 0.3123}}
        assert iu.EVENTO_DO_LOTE == dl.EVENTO_LOTE
        assert iu._decisao_deste_ciclo({**ciclo, "event": dl.EVENTO_LOTE}, do_lote) is do_lote
        assert iu._decisao_deste_ciclo(ciclo, do_lote) is None

    def test_linha_do_sdk_sem_decisao_continua_aceitando_so_o_ciclo(self):
        linha = iu._linha_do_sdk({"user_id": "user:c1", "risk_score": 0.5, "criticality": "padrao",
                                  "event": SESSAO, "days_since_last": 3, "features_used_30d": 4,
                                  "mrr": 300.0})
        assert linha["risco_decidido_por"] is None and linha["posicao_na_base"] is None
        assert linha["origem"] == iu.ORIGEM_SDK

    def test_a_leitura_da_trilha_e_a_ultima_decisao_de_risco_de_cada_cliente_da_empresa(self, cliente):
        chave_a, chave_b = _chave(cliente, A), _chave(cliente, B)
        _evento(cliente, chave_a, user="t-1", evento=SESSAO, props=SEM_USO, messageId="a1")
        _evento(cliente, chave_a, user="t-1", evento=CANCELAMENTO, props=SEM_USO, messageId="a2")
        _evento(cliente, chave_a, user="t-2", evento=REBAIXAR, props=SEM_USO, messageId="a3")
        _evento(cliente, chave_b, user="t-1", evento=REBAIXAR, props=SEM_USO, messageId="b1")
        de_a, de_b = rl.ultimas_decisoes_de_risco(A), rl.ultimas_decisoes_de_risco(B)
        assert set(de_a) == {"user:t-1", "user:t-2"} and set(de_b) == {"user:t-1"}
        assert de_a["user:t-1"]["entradas"]["event"] == CANCELAMENTO, "a mais recente"
        assert de_b["user:t-1"]["entradas"]["event"] == REBAIXAR, "so a da propria empresa"
        assert all(d["tipo_decisao"] == rl.TIPO_RISCO and d["tenant_id"] == A for d in de_a.values())
        assert rl.ultimas_decisoes_de_risco("empresa-que-nao-existe") == {}

    def test_a_leitura_nao_altera_a_trilha(self, cliente):
        _evento(cliente, _chave(cliente), evento=CANCELAMENTO, props=SEM_USO)
        antes = (rl.verificar_cadeia(A), _decisoes())
        rl.ultimas_decisoes_de_risco(A)
        _ranking()
        assert (rl.verificar_cadeia(A), _decisoes()) == antes


# == 6. A referencia de posicao em disco ====================================

class TestReferenciaEmDisco:
    @pytest.fixture
    def base(self, monkeypatch):
        """A base da empresa A (60 clientes) e a da B (40), com o modelo v3 ativo."""
        _instalar(REF_TODOS_ACIMA)
        bases = {A: _base(60), B: _base(40, days_since_last=1.0)}
        monkeypatch.setattr(bs.clientes_importados, "listar", lambda t, **k: bases.get(t, []))
        return bases

    def _reiniciar(self):
        """O que um reinicio do servico faz com a memoria deste modulo."""
        bs.esquecer_calculos_da_regua()
        assert bs._referencia_do_score == {} and bs._referencia_do_mrr == {}

    def test_o_arquivo_fica_ao_lado_do_banco_de_ciclos(self, monkeypatch, tmp_path):
        assert bs.caminho_das_referencias() == Path(rl.caminho_do_banco()).parent / "referencias_de_posicao.json"
        monkeypatch.setenv(bs.ENV_REFERENCIAS, str(tmp_path / "outro" / "ref.json"))
        assert bs.caminho_das_referencias() == tmp_path / "outro" / "ref.json"

    def test_pontuar_a_base_grava_so_os_quantis(self, base):
        bs.pontuar_base(A)
        arquivo = bs.caminho_das_referencias()
        gravado = json.loads(arquivo.read_text(encoding="utf-8"))
        assert set(gravado) == {A}
        assert set(gravado[A]) == {"score", "mrr", "clientes_com_score", "clientes_com_mrr",
                                   "atualizada_em"}
        for chave in ("score", "mrr"):
            quantis = gravado[A][chave]
            assert len(quantis) == bs.N_QUANTIS_DA_REFERENCIA == 101
            assert quantis == sorted(quantis) and all(isinstance(q, float) for q in quantis)
        assert gravado[A]["clientes_com_score"] == 60 and gravado[A]["clientes_com_mrr"] == 60
        assert gravado[A]["mrr"][0] == 300.0 and gravado[A]["mrr"][-1] == 890.0
        # Nenhum dado de cliente: nem identificador, nem coluna da base.
        texto = arquivo.read_text(encoding="utf-8")
        for proibido in ("b000", "b059", "customer", "days_since", "features", "email", "phone"):
            assert proibido not in texto, proibido

    def test_a_referencia_sobrevive_ao_reinicio(self, base):
        bs.pontuar_base(A)
        antes = bs.referencia_para_evento(A)
        assert antes[1] == bs.REGUA_BASE and len(antes[0]) == 101
        grade = [0.02, 0.08, 0.15, 0.25, 0.40, 0.70]
        posicoes = [bs.criticidade_do_evento(A, r, 800.0, 30, 0, True) for r in grade]
        self._reiniciar()
        assert bs.referencia_para_evento(A) == antes, "lida do disco na primeira consulta"
        assert [bs.criticidade_do_evento(A, r, 800.0, 30, 0, True) for r in grade] == posicoes
        assert all(p["origem_da_posicao"] == bs.REGUA_BASE for p in posicoes)
        assert any(p["mrr_no_topo"] is not None for p in posicoes), "a referencia do MRR voltou junto"

    def test_sem_o_arquivo_vale_a_referencia_de_treino_como_antes(self, base):
        assert not bs.caminho_das_referencias().exists()
        ref, origem = bs.referencia_para_evento(A)
        assert origem == bs.REGUA_GLOBAL and ref == sorted(REF_TODOS_ACIMA)

    def test_a_subida_do_servico_le_o_arquivo(self, base):
        bs.pontuar_base(A)
        bs.pontuar_base(B)
        self._reiniciar()
        assert relogio.carregar_referencias_de_posicao() == 2
        assert set(bs._referencia_do_score) == {A, B} and set(bs._referencia_do_mrr) == {A, B}

    def test_a_subida_chama_a_leitura_antes_de_ligar_o_relogio(self, monkeypatch):
        chamadas = []
        monkeypatch.setattr(relogio, "configurar_saida", lambda *a: chamadas.append("saida"))
        monkeypatch.setattr(relogio, "carregar_referencias_de_posicao",
                            lambda: chamadas.append("referencias"))
        monkeypatch.setattr(relogio, "ligar", lambda: chamadas.append("relogio"))

        async def nada():
            chamadas.append("desligar")
        monkeypatch.setattr(relogio, "desligar", nada)

        async def subir():
            async with relogio.ciclo_de_vida(None):
                pass
        asyncio.run(subir())
        assert chamadas == ["saida", "referencias", "relogio", "desligar"]

    def test_cada_empresa_tem_a_sua_e_uma_nao_apaga_a_outra(self, base):
        bs.pontuar_base(A)
        bs.pontuar_base(B)
        gravado = json.loads(bs.caminho_das_referencias().read_text(encoding="utf-8"))
        assert set(gravado) == {A, B} and gravado[A]["score"] != gravado[B]["score"]
        assert gravado[B]["clientes_com_score"] == 40
        self._reiniciar()
        assert bs.referencia_para_evento(A)[0] == gravado[A]["score"]
        assert bs.referencia_para_evento(B)[0] == gravado[B]["score"]
        assert bs.referencia_para_evento("empresa-c")[1] == bs.REGUA_GLOBAL

    def test_base_que_encolhe_perde_a_referencia(self, base):
        bs.pontuar_base(A)
        bs.pontuar_base(B)
        base[A] = _base(10)                              # abaixo do minimo de 30
        bs.pontuar_base(A)
        gravado = json.loads(bs.caminho_das_referencias().read_text(encoding="utf-8"))
        assert set(gravado) == {B}
        assert bs.referencia_para_evento(A)[1] == bs.REGUA_GLOBAL
        self._reiniciar()
        assert bs.referencia_para_evento(A)[1] == bs.REGUA_GLOBAL
        assert bs.referencia_para_evento(B)[1] == bs.REGUA_BASE

    def test_so_grava_quando_a_referencia_mudou(self, base, monkeypatch):
        import os
        gravacoes = []
        original = os.replace
        monkeypatch.setattr(os, "replace", lambda a, b: (gravacoes.append(b), original(a, b))[1])
        bs.pontuar_base(A)
        bs.pontuar_base(A)
        bs.pontuar_base(A)
        assert len(gravacoes) == 1
        base[A] = _base(45)
        bs.pontuar_base(A)
        assert len(gravacoes) == 2

    def test_lista_no_corpo_sem_empresa_nao_grava_nada(self, base):
        bs.pontuar_lista(_base(60))
        assert not bs.caminho_das_referencias().exists() and bs._referencia_do_score == {}

    def test_arquivo_torto_nao_derruba_e_e_ignorado(self, base):
        arquivo = bs.caminho_das_referencias()
        for conteudo in ("{isto nao e json", "[1, 2, 3]", json.dumps({A: "texto"}),
                         json.dumps({A: {"score": ["a", "b"], "mrr": [1]}})):
            arquivo.write_text(conteudo, encoding="utf-8")
            self._reiniciar()
            assert bs.carregar_referencias(forcar=True) == 0
            assert bs.referencia_para_evento(A)[1] == bs.REGUA_GLOBAL
        bs.pontuar_base(A)                               # e a proxima pontuacao conserta o arquivo
        assert set(json.loads(arquivo.read_text(encoding="utf-8"))) == {A}

    def test_falha_de_disco_nao_derruba_o_ranking(self, base, monkeypatch):
        import os

        def sem_disco(a, b):
            raise OSError("disco cheio")
        monkeypatch.setattr(os, "replace", sem_disco)
        ranking = bs.pontuar_base(A)
        assert len(ranking) == 60
        assert bs.referencia_para_evento(A)[1] == bs.REGUA_BASE, "a memoria fica certa"

    def test_o_que_esta_na_memoria_vence_o_disco(self, base):
        bs.pontuar_base(A)
        do_processo = bs._referencia_do_score[A]
        arquivo = bs.caminho_das_referencias()
        gravado = json.loads(arquivo.read_text(encoding="utf-8"))
        gravado[A]["score"] = [0.5 + i / 1000 for i in range(101)]
        arquivo.write_text(json.dumps(gravado), encoding="utf-8")
        assert bs.carregar_referencias(forcar=True) == 1
        assert bs._referencia_do_score[A] == do_processo

    def test_o_evento_usa_a_referencia_da_empresa_depois_do_reinicio(self, cliente, base):
        bs.pontuar_base(A)
        self._reiniciar()
        _evento(cliente, _chave(cliente), evento=SESSAO, props=ABANDONO)
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]["saida"]
        assert risco["posicao"]["referencia"] == bs.REGUA_BASE
        assert risco["posicao"]["na_base"] is not None

    def test_os_quantis_sao_101_do_menor_ao_maior(self):
        quantis = bs.quantis_da_referencia([0.9, 0.1, 0.5, 0.3, 0.7])
        assert len(quantis) == 101 and quantis[0] == 0.1 and quantis[-1] == 0.9 and quantis[50] == 0.5
        assert quantis == sorted(quantis)

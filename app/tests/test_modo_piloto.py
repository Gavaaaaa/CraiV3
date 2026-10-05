"""tests/test_modo_piloto.py - Rodada 4, Fase 3: o modo piloto (fee zerada).

AS SEIS REGRAS (decididas pelo Crai), e o que este arquivo mede de cada uma:

  M1  quem define o piloto e a CRAI, pela env `CRAI_TENANTS_EM_PILOTO`: nenhuma
      rota da empresa liga ou desliga, e a configuracao da empresa nao tem a chave;
  M2  em piloto a fee cobrada e zero: o liquido e o valor inteiro, na linha do
      ciclo, nas metricas do mes, na visao geral e no extrato;
  M3  a fee que seria cobrada continua calculada e guardada, e aparece SO no
      extrato (JSON e arquivo), na coluna propria;
  M4  o estorno funciona igual; em piloto nao ha fee a devolver;
  M5  sair do piloto vale dali para a frente: o que foi recuperado no piloto
      nao ganha fee depois, e o que foi recuperado fora dele nao perde a fee;
  M6  a visao geral e o extrato dizem que a empresa esta em piloto.

Vale para o involuntario (pelo webhook do Pix, o caminho real de fechamento do
ciclo) e para o voluntario (pelo aceite da oferta).
"""

import asyncio
import json
import sqlite3
from datetime import timedelta

import pytest

from crai import config
from crai.api import app as app_module
from crai.api import datas
from crai.api import visao_geral as vg
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import mantido, voluntary_agent
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao, recovery_log
from tests.test_mensagens_involuntario import (  # noqa: F401 - fixtures usadas pelo nome
    A, B, ABERTURA, VALOR, _assinar, _ciclo, _evento, _post, relogio, sem_llm)
from tests.test_mensagens_involuntario import cliente as pix  # noqa: F401
from tests.test_visao_geral_api import (  # noqa: F401
    _aceitar, _base, _chaves, _get, _oferta, cliente)

CHEIA = round(VALOR * 0.15, 2)                    # a fee de 15% de uma cobranca de 299,90
PAGO_EM = ABERTURA + timedelta(days=2, hours=1)   # 05/09/2026, 10h
SETEMBRO = "2026-09"
SEM_A_FEE = ("/metrics/visao-geral", "/metrics/serie", "/metrics/involuntario/funil",
             "/metrics/o-que-funciona", "/atividade", "/metrics/involuntario/mes", "/ciclos",
             "/clientes/recentes", "/metrics/voluntario/mes")


@pytest.fixture(autouse=True)
def _fee_padrao(monkeypatch):
    monkeypatch.delenv(config.ENV_SUCCESS_FEE, raising=False)
    monkeypatch.delenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, raising=False)
    monkeypatch.delenv(config.ENV_TENANTS_EM_PILOTO, raising=False)


def _piloto(monkeypatch, *tenants):
    if tenants:
        monkeypatch.setenv(config.ENV_TENANTS_EM_PILOTO, ",".join(tenants))
    else:
        monkeypatch.delenv(config.ENV_TENANTS_EM_PILOTO, raising=False)


def _dono(c, tenant, caminho, **params):
    return c.get(caminho, params=params,
                 headers=c.projeto.bearer(tenant, papel="owner", plano="premium"))


def _falhar(c, relogio, rec, tenant=A, quando=ABERTURA):
    relogio(quando)
    _post(c, _evento(rec, f"E_{rec}_0"), tenant)


def _pagar(c, relogio, rec, tenant=A, quando=PAGO_EM):
    relogio(quando)
    r = _post(c, _evento(rec, f"E_{rec}_pago", evento="automatic_pix.charge_succeeded"), tenant)
    assert r["ciclo"] == "recuperado", r
    return r


def _recuperar(c, relogio, rec, tenant=A, falhou_em=ABERTURA, quando=PAGO_EM):
    """Uma cobranca que falhou e foi paga, pelo webhook: o caminho real."""
    _falhar(c, relogio, rec, tenant, falhou_em)
    resposta = _pagar(c, relogio, rec, tenant, quando)
    return _ciclo(rec, tenant), resposta


def _devolver(c, relogio, rec, quando, valor=VALOR, id_devolucao="D_1", tenant=A):
    relogio(quando)
    corpo = json.dumps({"event": "automatic_pix.charge_refunded", "id_recorrencia": rec,
                        "e2e_id": f"E_{rec}_pago", "valor_devolvido": valor,
                        "id_devolucao": id_devolucao}).encode()
    r = c.post("/webhooks/pix-automatico", content=corpo,
               headers={**_assinar(corpo), "x-tenant-id": tenant})
    assert r.status_code == 200, r.text
    return r.json()


def _extrato(c, tenant=A, mes=SETEMBRO):
    r = _dono(c, tenant, "/extrato", mes=mes)
    assert r.status_code == 200, r.text
    return r.json()


def _csv(c, tenant=A, mes=SETEMBRO):
    r = _dono(c, tenant, "/extrato/csv", mes=mes)
    assert r.status_code == 200, r.text
    assert r.text.startswith(vg.MARCA_DE_UTF8)
    return [l.split(";") for l in r.text[1:].split("\r\n") if l]


# == M1: quem define o piloto e a CRAI ======================================

class TestQuemEstaEmPiloto:
    def test_sem_a_env_ninguem_esta_em_piloto(self):
        assert config.tenants_em_piloto() == frozenset()
        assert not config.tenant_em_piloto(A)
        assert config.taxa_cobrada_e_fora_do_piloto(30.0, A) == (30.0, None)
        assert config.success_fee_cobrada_pct(A) == config.success_fee_pct() == 0.15

    @pytest.mark.parametrize("bruto, esperado", [
        ("empresa-a", {"empresa-a"}),
        (" empresa-a , empresa-b ", {"empresa-a", "empresa-b"}),
        ("empresa-a;empresa-b,,", {"empresa-a", "empresa-b"}),
        ("", set()),
        ("  ,  ", set()),
    ])
    def test_a_lista_vem_da_env_separada_por_virgula(self, monkeypatch, bruto, esperado):
        monkeypatch.setenv(config.ENV_TENANTS_EM_PILOTO, bruto)
        assert config.tenants_em_piloto() == frozenset(esperado)

    def test_a_comparacao_e_exata_e_sem_id_nao_ha_piloto(self, monkeypatch):
        _piloto(monkeypatch, A)
        assert config.tenant_em_piloto(A)
        assert not config.tenant_em_piloto(A.upper())
        assert not config.tenant_em_piloto(A + "-2")
        assert not config.tenant_em_piloto(None) and not config.tenant_em_piloto("")
        assert not config.tenant_em_piloto(B)

    def test_em_piloto_a_conta_e_zero_e_a_cheia_fica_ao_lado(self, monkeypatch):
        _piloto(monkeypatch, A)
        assert config.taxa_cobrada_e_fora_do_piloto(44.985, A) == (0.0, 44.98) or \
            config.taxa_cobrada_e_fora_do_piloto(44.985, A) == (0.0, 44.99)
        assert config.taxa_cobrada_e_fora_do_piloto(30.0, A) == (0.0, 30.0)
        assert config.taxa_cobrada_e_fora_do_piloto(30.0, B) == (30.0, None)
        assert config.success_fee_cobrada_pct(A) == 0.0
        assert config.success_fee_cobrada_pct(B) == 0.15

    def test_nenhuma_rota_fala_de_piloto_e_a_configuracao_nao_tem_a_chave(self, cliente):
        assert [r.path for r in app_module.app.routes if "piloto" in r.path.lower()] == []
        assert not [k for k in configuracao.ler(A) if "piloto" in k.lower()]
        corpo = _get(cliente, A, "/configuracao").json()
        assert not [k for k in _chaves(corpo) if "piloto" in str(k).lower()]

    @pytest.mark.parametrize("chave", ["piloto", "em_piloto", "modo_piloto", "fee", "success_fee_pct"])
    def test_a_empresa_nao_liga_o_piloto_pela_configuracao(self, cliente, chave):
        r = cliente.put("/configuracao", json={chave: True},
                        headers=cliente.projeto.bearer(A, papel="owner", plano="premium"))
        assert r.status_code == 422, r.text
        assert not config.tenant_em_piloto(A)
        assert _get(cliente, A, "/metrics/visao-geral").json()["piloto"] is False


# == M2 e M3 no involuntario, pelo webhook ==================================

class TestInvoluntarioEmPiloto:
    def test_fora_do_piloto_nada_mudou(self, pix, relogio):
        ciclo, resposta = _recuperar(pix, relogio, "RN_normal")
        assert ciclo["fee"] == CHEIA and ciclo["fee_fora_do_piloto"] is None
        assert resposta["fee"] == CHEIA
        e = _extrato(pix)
        assert e["piloto"] == {"ativo": False, "fee_fora_do_piloto": None}
        assert [(l["fee"], l["liquido"], l["fee_fora_do_piloto"]) for l in e["linhas"]] == [
            (CHEIA, round(VALOR - CHEIA, 2), None)]
        assert _dono(pix, A, "/metrics/visao-geral").json()["piloto"] is False

    def test_em_piloto_a_fee_e_zero_e_a_que_seria_cobrada_fica_guardada(self, pix, relogio,
                                                                       monkeypatch):
        _piloto(monkeypatch, A)
        ciclo, resposta = _recuperar(pix, relogio, "RN_piloto")
        assert ciclo["estado"] == cc.RECUPERADO and ciclo["valor"] == VALOR
        assert ciclo["fee"] == 0.0
        assert ciclo["fee_fora_do_piloto"] == CHEIA
        assert resposta["fee"] == 0.0

    def test_o_liquido_e_o_valor_inteiro_em_toda_tela(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        ciclo, _ = _recuperar(pix, relogio, "RN_piloto")
        detalhe = _dono(pix, A, f"/ciclos/{ciclo['id']}").json()
        assert detalhe["ciclo"]["valor_liquido"] == VALOR
        recuperado = [e for e in detalhe["linha_do_tempo"] if e["tipo"] == "recuperado"]
        assert recuperado[0]["dados"] == {"valor_liquido": VALOR}
        mes = _dono(pix, A, "/metrics/involuntario/mes", mes=SETEMBRO).json()
        assert mes["valor_liquido_recuperado"] == VALOR and mes["recuperados"] == 1
        geral = _dono(pix, A, "/metrics/visao-geral").json()
        assert geral["recuperado_involuntario"] == VALOR and geral["mantido"] == VALOR
        assert geral["piloto"] is True
        e = _extrato(pix)
        assert e["totais"] == {"valor_base": VALOR, "fee": 0.0, "liquido": VALOR}

    def test_o_extrato_tem_a_coluna_propria(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        _recuperar(pix, relogio, "RN_piloto")
        e = _extrato(pix)
        assert e["piloto"] == {"ativo": True, "fee_fora_do_piloto": CHEIA}
        (linha,) = e["linhas"]
        assert (linha["valor_base"], linha["fee"], linha["liquido"]) == (VALOR, 0.0, VALOR)
        assert linha["fee_fora_do_piloto"] == CHEIA

    def test_o_arquivo_do_extrato_ganha_a_coluna_depois_da_taxa(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        _recuperar(pix, relogio, "RN_piloto")
        cabecalho, linha, total = _csv(pix)
        p = cabecalho.index("Taxa da CRAI (R$)")
        assert cabecalho[p + 1] == "Taxa fora do piloto (R$)" == vg.COLUNA_DO_PILOTO
        assert len(cabecalho) == len(vg.COLUNAS_DO_CSV) + 1 == len(linha) == len(total)
        assert [c for i, c in enumerate(cabecalho) if i != p + 1] == list(vg.COLUNAS_DO_CSV)
        reais = lambda v: f"{v:.2f}".replace(".", ",")
        assert linha[p - 1:p + 3] == [reais(VALOR), "0,00", reais(CHEIA), reais(VALOR)]
        assert total[p - 1:p + 3] == [reais(VALOR), "0,00", reais(CHEIA), reais(VALOR)]

    def test_fora_do_piloto_o_arquivo_nao_tem_a_coluna(self, pix, relogio):
        _recuperar(pix, relogio, "RN_normal")
        cabecalho, linha, _ = _csv(pix)
        assert cabecalho == list(vg.COLUNAS_DO_CSV) and len(linha) == len(vg.COLUNAS_DO_CSV)
        assert vg.COLUNA_DO_PILOTO not in cabecalho

    def test_a_fee_fora_do_piloto_so_aparece_no_extrato(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        ciclo, _ = _recuperar(pix, relogio, "RN_piloto")
        assert f"{CHEIA}" not in ("0.0", "0")
        for rota in SEM_A_FEE + (f"/ciclos/{ciclo['id']}",):
            r = _dono(pix, A, rota)
            assert r.status_code == 200, (rota, r.text)
            chaves = {str(k) for k in _chaves(r.json())}
            assert not [k for k in chaves if "fee" in k.lower() or "piloto_" in k.lower()], rota
            assert str(CHEIA) not in r.text, rota

    def test_o_piloto_de_uma_empresa_nao_vale_para_a_outra(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        da_a, _ = _recuperar(pix, relogio, "RN_a")
        da_b, _ = _recuperar(pix, relogio, "RN_b", tenant=B)
        assert (da_a["fee"], da_a["fee_fora_do_piloto"]) == (0.0, CHEIA)
        assert (da_b["fee"], da_b["fee_fora_do_piloto"]) == (CHEIA, None)
        assert _extrato(pix, B)["piloto"] == {"ativo": False, "fee_fora_do_piloto": None}
        assert _dono(pix, B, "/metrics/visao-geral").json()["piloto"] is False
        assert _dono(pix, B, "/metrics/visao-geral").json()["recuperado_involuntario"] == round(
            VALOR - CHEIA, 2)

    def test_o_dataset_e_a_receita_interna_contam_zero(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        _recuperar(pix, relogio, "RN_a")
        _recuperar(pix, relogio, "RN_b", tenant=B)
        da_a = recovery_log.metricas(tenant_id=A)
        da_b = recovery_log.metricas(tenant_id=B)
        assert da_a["recuperados"] == 1 and da_a["fee_total"] == 0.0
        assert da_b["recuperados"] == 1 and da_b["fee_total"] == CHEIA

    def test_membro_nao_ve_o_extrato_nem_em_piloto(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        _recuperar(pix, relogio, "RN_piloto")
        for rota in ("/extrato", "/extrato/csv"):
            r = pix.get(rota, params={"mes": SETEMBRO},
                        headers=pix.projeto.bearer(A, papel="membro", plano="premium"))
            assert r.status_code == 403, rota


# == M4: o estorno em piloto ================================================

class TestEstornoEmPiloto:
    def test_devolucao_total_nao_ha_fee_a_devolver(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        ciclo, _ = _recuperar(pix, relogio, "RN_volta")
        _devolver(pix, relogio, "RN_volta", PAGO_EM + timedelta(days=10))
        depois = _ciclo("RN_volta")
        assert depois["estado"] == cc.RECUPERADO, "o estorno nao muda o estado gravado"
        assert depois["valor_estornado"] == VALOR
        assert (depois["fee"], depois["fee_estornada"]) == (0.0, 0.0)
        assert depois["fee_fora_do_piloto"] == CHEIA, "o que foi gravado nao e reescrito"
        (aviso,) = cc.estornos_do_ciclo(ciclo["id"])
        assert aviso["fee_devolvida"] == 0.0 and aviso["valor_considerado"] == VALOR
        mes = _dono(pix, A, "/metrics/involuntario/mes", mes=SETEMBRO).json()
        assert mes["valor_liquido_recuperado"] == 0.0
        assert mes["estornos"]["valor_liquido_estornado"] == VALOR
        linha = _dono(pix, A, f"/ciclos/{ciclo['id']}").json()["ciclo"]
        assert linha["valor_liquido"] is None and linha["status"] == cc.STATUS_ENCERRADO

    def test_o_extrato_devolve_a_taxa_que_seria_cobrada(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        _recuperar(pix, relogio, "RN_volta")
        _devolver(pix, relogio, "RN_volta", PAGO_EM + timedelta(days=10))
        e = _extrato(pix)
        por_tipo = {l["tipo"]: l for l in e["linhas"]}
        assert por_tipo["recuperacao"]["fee_fora_do_piloto"] == CHEIA
        estorno = por_tipo["estorno"]
        assert (estorno["valor_base"], estorno["liquido"]) == (-VALOR, -VALOR)
        assert estorno["fee"] == 0.0
        assert estorno["fee_fora_do_piloto"] == -CHEIA
        assert e["totais"] == {"valor_base": 0.0, "fee": 0.0, "liquido": 0.0}
        assert e["piloto"] == {"ativo": True, "fee_fora_do_piloto": 0.0}

    def test_devolucao_parcial_leva_a_mesma_proporcao(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        _recuperar(pix, relogio, "RN_parcial")
        parcial = round(VALOR * 0.4, 2)
        _devolver(pix, relogio, "RN_parcial", PAGO_EM + timedelta(days=5), valor=parcial)
        e = _extrato(pix)
        estorno = next(l for l in e["linhas"] if l["tipo"] == "estorno")
        assert estorno["valor_base"] == -parcial and estorno["liquido"] == -parcial
        assert estorno["fee_fora_do_piloto"] == pytest.approx(-CHEIA * 0.4, abs=0.011)
        assert e["totais"]["liquido"] == round(VALOR - parcial, 2)
        assert e["piloto"]["fee_fora_do_piloto"] == pytest.approx(CHEIA * 0.6, abs=0.011)

    def test_dois_parciais_fecham_a_taxa_no_centavo(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        _recuperar(pix, relogio, "RN_dois")
        primeiro = 100.0
        _devolver(pix, relogio, "RN_dois", PAGO_EM + timedelta(days=3), valor=primeiro,
                  id_devolucao="D_1")
        _devolver(pix, relogio, "RN_dois", PAGO_EM + timedelta(days=6),
                  valor=round(VALOR - primeiro, 2), id_devolucao="D_2")
        e = _extrato(pix)
        estornos = [l for l in e["linhas"] if l["tipo"] == "estorno"]
        assert len(estornos) == 2
        assert round(sum(l["fee_fora_do_piloto"] for l in estornos), 2) == -CHEIA
        assert e["piloto"]["fee_fora_do_piloto"] == 0.0
        assert e["totais"]["liquido"] == 0.0

    def test_devolucao_fora_do_prazo_nao_mexe_em_nada(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        _recuperar(pix, relogio, "RN_tarde")
        r = _devolver(pix, relogio, "RN_tarde", PAGO_EM + timedelta(days=45))
        assert "fora_do_prazo" in json.dumps(r)
        assert [l["tipo"] for l in _extrato(pix, mes="2026-10")["linhas"]] == []
        e = _extrato(pix)
        assert e["piloto"]["fee_fora_do_piloto"] == CHEIA and e["totais"]["liquido"] == VALOR


# == M5: sair do piloto vale dali para a frente =============================

class TestSairDoPiloto:
    def test_o_que_foi_recuperado_no_piloto_nao_ganha_fee_depois(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        no_piloto, _ = _recuperar(pix, relogio, "RN_no_piloto")
        _piloto(monkeypatch)                                   # a CRAI tirou a empresa da lista
        depois, _ = _recuperar(pix, relogio, "RN_depois", falhou_em=PAGO_EM + timedelta(days=1),
                               quando=PAGO_EM + timedelta(days=3))
        assert (depois["fee"], depois["fee_fora_do_piloto"]) == (CHEIA, None)
        antigo = cc.ciclo_por_id(no_piloto["id"])
        assert (antigo["fee"], antigo["fee_fora_do_piloto"]) == (0.0, CHEIA)
        e = _extrato(pix)
        assert e["piloto"] == {"ativo": False, "fee_fora_do_piloto": CHEIA}, \
            "fora do piloto, o mes em que ele valeu continua mostrando a coluna"
        por_cliente = {l["id_cliente"]: l for l in e["linhas"]}
        assert por_cliente["RN_no_piloto"]["liquido"] == VALOR
        assert por_cliente["RN_no_piloto"]["fee_fora_do_piloto"] == CHEIA
        assert por_cliente["RN_depois"]["liquido"] == round(VALOR - CHEIA, 2)
        assert por_cliente["RN_depois"]["fee_fora_do_piloto"] is None
        assert e["totais"]["fee"] == CHEIA
        geral = _dono(pix, A, "/metrics/visao-geral").json()
        assert geral["piloto"] is False
        assert geral["recuperado_involuntario"] == round(VALOR + VALOR - CHEIA, 2)

    def test_o_arquivo_mostra_a_coluna_vazia_na_linha_que_nao_e_de_piloto(self, pix, relogio,
                                                                         monkeypatch):
        _piloto(monkeypatch, A)
        _recuperar(pix, relogio, "RN_no_piloto")
        _piloto(monkeypatch)
        _recuperar(pix, relogio, "RN_depois", falhou_em=PAGO_EM + timedelta(days=1),
                   quando=PAGO_EM + timedelta(days=3))
        cabecalho, *linhas, total = _csv(pix)
        p = cabecalho.index(vg.COLUNA_DO_PILOTO)
        ident = cabecalho.index("Identificador")
        por_cliente = {l[ident]: l for l in linhas}
        reais = lambda v: f"{v:.2f}".replace(".", ",")
        assert por_cliente["RN_no_piloto"][p] == reais(CHEIA)
        assert por_cliente["RN_depois"][p] == ""
        assert total[p] == reais(CHEIA) and total[p - 1] == reais(CHEIA)

    def test_ciclo_aberto_no_piloto_e_pago_depois_tem_fee(self, pix, relogio, monkeypatch):
        _piloto(monkeypatch, A)
        _falhar(pix, relogio, "RN_atravessa")
        _piloto(monkeypatch)
        _pagar(pix, relogio, "RN_atravessa")
        ciclo = _ciclo("RN_atravessa")
        assert (ciclo["fee"], ciclo["fee_fora_do_piloto"]) == (CHEIA, None)

    def test_entrar_no_piloto_nao_apaga_a_fee_do_que_ja_foi_recuperado(self, pix, relogio,
                                                                      monkeypatch):
        antes, _ = _recuperar(pix, relogio, "RN_antes")
        _piloto(monkeypatch, A)
        antigo = cc.ciclo_por_id(antes["id"])
        assert (antigo["fee"], antigo["fee_fora_do_piloto"]) == (CHEIA, None)
        e = _extrato(pix)
        assert e["piloto"] == {"ativo": True, "fee_fora_do_piloto": None}
        assert e["totais"]["fee"] == CHEIA
        # Em piloto agora, o arquivo tem a coluna, vazia nessa linha.
        cabecalho, linha, total = _csv(pix)
        p = cabecalho.index(vg.COLUNA_DO_PILOTO)
        assert linha[p] == "" and total[p] == "0,00"


# == O voluntario ===========================================================

MRR = 500.0
BASE = 400.0            # 1 mes de MRR menos 20% de desconto
FEE_VOL = 60.0          # 15% de 400


def _cancelar(tenant, cid):
    """O cancelamento que a empresa informa: `cancelado_em` na base importada."""
    assert ci.cancelar(tenant, cid) is not None


class TestVoluntarioEmPiloto:
    def test_a_conta_de_uma_retencao(self, monkeypatch):
        fora = mantido.valores_da_retencao("desconto_20", MRR, A)
        assert (fora["valor_base"], fora["fee"], fora["liquido"]) == (BASE, FEE_VOL, BASE - FEE_VOL)
        assert fora["fee_fora_do_piloto"] is None
        assert mantido.valores_da_retencao("desconto_20", MRR) == fora, "sem empresa, como sempre"
        _piloto(monkeypatch, A)
        dentro = mantido.valores_da_retencao("desconto_20", MRR, A)
        assert (dentro["valor_base"], dentro["fee"], dentro["liquido"]) == (BASE, 0.0, BASE)
        assert dentro["fee_fora_do_piloto"] == FEE_VOL
        assert mantido.valores_da_retencao("desconto_20", MRR, B) == fora

    def test_em_piloto_o_mantido_e_a_base_inteira(self, cliente, monkeypatch):
        _piloto(monkeypatch, A)
        _base(A, ("c-ana", MRR, 60, 0, {"nome": "Ana Prado"}))
        _oferta(A, "c-ana")
        _aceitar(A, "c-ana")
        geral = _get(cliente, A, "/metrics/visao-geral").json()
        assert geral["retido_voluntario"] == BASE and geral["mantido"] == BASE
        assert geral["piloto"] is True
        mes = _get(cliente, A, "/metrics/voluntario/mes").json()
        assert mes["valor_liquido_mantido"] == BASE
        e = _get(cliente, A, "/extrato").json()
        (linha,) = e["linhas"]
        assert (linha["origem"], linha["valor_base"], linha["fee"], linha["liquido"]) == (
            "voluntario", BASE, 0.0, BASE)
        assert linha["fee_fora_do_piloto"] == FEE_VOL
        assert e["piloto"] == {"ativo": True, "fee_fora_do_piloto": FEE_VOL}
        assert e["totais"] == {"valor_base": BASE, "fee": 0.0, "liquido": BASE}

    def test_fora_do_piloto_o_voluntario_nao_mudou(self, cliente):
        _base(A, ("c-ana", MRR, 60, 0, {"nome": "Ana Prado"}))
        _oferta(A, "c-ana")
        _aceitar(A, "c-ana")
        e = _get(cliente, A, "/extrato").json()
        (linha,) = e["linhas"]
        assert (linha["fee"], linha["liquido"], linha["fee_fora_do_piloto"]) == (
            FEE_VOL, BASE - FEE_VOL, None)
        assert e["piloto"] == {"ativo": False, "fee_fora_do_piloto": None}

    def test_o_aceite_grava_a_linha_na_hora_e_sair_do_piloto_nao_a_recalcula(self, cliente,
                                                                            monkeypatch):
        """M5 no voluntario: a linha do mantido nasce no aceite (e nao na
        proxima leitura da tela), com a fee do momento."""
        _piloto(monkeypatch, A)
        _base(A, ("c-ana", MRR, 60, 0, {"nome": "Ana Prado"}))
        _oferta(A, "c-ana")
        r = asyncio.run(voluntary_agent.registrar_resultado_externo(
            "user:c-ana", "desconto_20", "PJ", True, tenant_id=A))
        assert r["contabilizado"] is True
        (linha,) = mantido.do_cliente(A, "c-ana")
        assert (linha["fee"], linha["fee_fora_do_piloto"]) == (0.0, FEE_VOL)
        _piloto(monkeypatch)                                   # saiu do piloto
        assert mantido.sincronizar(A)["novas"] == 0
        (linha,) = mantido.do_cliente(A, "c-ana")
        assert (linha["fee"], linha["fee_fora_do_piloto"]) == (0.0, FEE_VOL)
        e = _get(cliente, A, "/extrato").json()
        assert e["piloto"] == {"ativo": False, "fee_fora_do_piloto": FEE_VOL}
        assert e["totais"]["liquido"] == BASE
        # Um aceite novo, ja fora do piloto, tem a fee normal.
        _base(A, ("c-bia", MRR, 55, 0, {"nome": "Bia Lemos"}))
        _oferta(A, "c-bia")
        asyncio.run(voluntary_agent.registrar_resultado_externo(
            "user:c-bia", "desconto_20", "PJ", True, tenant_id=A))
        (nova,) = mantido.do_cliente(A, "c-bia")
        assert (nova["fee"], nova["fee_fora_do_piloto"]) == (FEE_VOL, None)

    def test_recusa_nao_cria_linha(self, cliente, monkeypatch):
        _piloto(monkeypatch, A)
        _base(A, ("c-ana", MRR, 60, 0, {}))
        _oferta(A, "c-ana")
        asyncio.run(voluntary_agent.registrar_resultado_externo(
            "user:c-ana", "desconto_20", "PJ", False, tenant_id=A))
        assert mantido.do_cliente(A, "c-ana") == []

    def test_cancelamento_no_prazo_em_piloto(self, cliente, monkeypatch):
        _piloto(monkeypatch, A)
        _base(A, ("c-ana", MRR, 60, 0, {"nome": "Ana Prado"}))
        _oferta(A, "c-ana")
        _aceitar(A, "c-ana")
        assert mantido.sincronizar(A)["novas"] == 1
        _cancelar(A, "c-ana")
        assert mantido.sincronizar(A)["estornadas"] == 1
        (linha,) = mantido.do_cliente(A, "c-ana")
        assert linha["valor_estornado"] == BASE and linha["fee_estornada"] == 0.0
        e = _get(cliente, A, "/extrato").json()
        por_tipo = {l["tipo"]: l for l in e["linhas"]}
        assert por_tipo["mantido"]["fee_fora_do_piloto"] == FEE_VOL
        assert por_tipo["estorno"]["fee_fora_do_piloto"] == -FEE_VOL
        assert (por_tipo["estorno"]["valor_base"], por_tipo["estorno"]["liquido"]) == (-BASE, -BASE)
        assert e["totais"] == {"valor_base": 0.0, "fee": 0.0, "liquido": 0.0}
        assert e["piloto"] == {"ativo": True, "fee_fora_do_piloto": 0.0}
        assert _get(cliente, A, "/metrics/visao-geral").json()["retido_voluntario"] == 0.0

    def test_a_fee_fora_do_piloto_do_voluntario_so_aparece_no_extrato(self, cliente, monkeypatch):
        _piloto(monkeypatch, A)
        _base(A, ("c-ana", MRR, 60, 0, {"nome": "Ana Prado"}))
        _oferta(A, "c-ana")
        _aceitar(A, "c-ana")
        for rota in SEM_A_FEE:
            r = _get(cliente, A, rota)
            assert r.status_code == 200, (rota, r.text)
            assert not [k for k in _chaves(r.json()) if "fee" in str(k).lower()], rota
        # A exportacao do titular traz o valor mantido dele, sem taxa nenhuma.
        exportado = cliente.post("/titular/exportar", json={"customer_id_externo": "c-ana"},
                                 headers=cliente.projeto.bearer(A, papel="owner", plano="premium"))
        assert exportado.status_code == 200, exportado.text
        assert not [k for k in _chaves(exportado.json()) if "fee" in str(k).lower()]
        assert "piloto" not in exportado.text
        (mantida,) = exportado.json()["valores_mantidos"]
        assert set(mantida) == {"oferta", "aceito_em", "mrr", "desconto", "cancelamento_em"}


# == A coluna nova num banco que ja existia =================================

class TestMigracao:
    def test_a_tabela_do_mantido_ganha_a_coluna_sem_perder_linha(self, cliente, tmp_path,
                                                               monkeypatch):
        _base(A, ("c-ana", MRR, 60, 0, {}))
        _oferta(A, "c-ana")
        _aceitar(A, "c-ana")
        assert mantido.sincronizar(A)["novas"] == 1
        (antes,) = mantido.do_cliente(A, "c-ana")
        caminho = cc.caminho_do_banco()
        with sqlite3.connect(caminho) as conn:
            conn.execute("ALTER TABLE retencoes_mantidas DROP COLUMN fee_fora_do_piloto")
            colunas = [l[1] for l in conn.execute("PRAGMA table_info(retencoes_mantidas)")]
        assert "fee_fora_do_piloto" not in colunas, "o banco de antes da Rodada 4"
        (depois,) = mantido.do_cliente(A, "c-ana")
        assert depois == antes and depois["fee_fora_do_piloto"] is None
        with sqlite3.connect(caminho) as conn:
            assert "fee_fora_do_piloto" in [l[1] for l in conn.execute(
                "PRAGMA table_info(retencoes_mantidas)")]

    def test_ciclo_antigo_tem_a_coluna_nula_e_segue_valendo(self, pix, relogio):
        ciclo, _ = _recuperar(pix, relogio, "RN_antigo")
        assert "fee_fora_do_piloto" in ciclo and ciclo["fee_fora_do_piloto"] is None
        assert cc.extrato_do_periodo(A, ABERTURA, PAGO_EM + timedelta(days=1)) == [{
            "tipo": "recuperacao", "ciclo_id": ciclo["id"], "id_recorrencia": "RN_antigo",
            "quando": ciclo["recuperado_em"], "valor_base": VALOR, "fee": CHEIA,
            "liquido": round(VALOR - CHEIA, 2)}]

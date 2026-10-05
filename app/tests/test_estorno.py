"""tests/test_estorno.py — Etapa 2, Bloco 5: o estorno da fee.

A CRAI cobra fee do que recupera. Se o dinheiro recuperado VOLTA ao pagador
dentro do prazo da empresa, a recuperação não valeu e a fee é devolvida.

O QUE ESTE ARQUIVO MEDE (o Portão 5, item por item):
  - devolução TOTAL no dia 10, com prazo de 30: a fee vai a zero, o líquido sai
    do mês, e o status vira "encerrado sem recuperação" com o motivo;
  - devolução PARCIAL de 40% no prazo: fee e líquido caem 40%;
  - devolução no dia 31, com prazo de 30: nada muda nos valores; o evento
    aparece na linha do tempo;
  - empresa com `prazo_estorno_dias = 90`: devolução no dia 60 estorna;
  - recuperado em setembro e estornado em outubro: setembro não muda; outubro
    tem a parcela negativa;
  - o mesmo aviso reenviado conta uma vez; dois parciais somam e nunca passam
    do total;
  - aviso de devolução de outra empresa: 404, igual ao inexistente (R12), sem
    mexer no ciclo;
  - nenhuma rota permite declarar estorno à mão (E6);
  - nenhum dado pessoal novo gravado, e a chave Pix do payload descartada;
  - migração (as 3 colunas e a tabela nova) sobre um banco antigo e sobre uma
    cópia do banco real, com todas as linhas preservadas.

E o que a tarefa manda NÃO mudar: a trilha do Art. 20 não ganha linha, o rótulo
`recovered` do dataset de treino continua como estava, a fee não aparece em
resposta nenhuma, e os quatro eventos antigos do Pix saem do parser idênticos.
"""

import asyncio
import json
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from crai.api import app as app_module
from crai.churn_voluntary import retention_log as trilha
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao
from crai.integrations import payment_gateway as pg
from tests.test_mensagens_involuntario import (  # noqa: F401 — fixtures usadas por nome
    A, B, ABERTURA, VALOR, _assinar, _ciclo, _evento, _get, _post,
    cliente, envios, producao, relogio, sem_llm,
)

REAL = Path(__file__).resolve().parents[1] / "data" / "recovery_cycles.db"
RECUPERADO_EM = ABERTURA + timedelta(days=2, hours=1)          # 05/09/2026, 10h
CENTAVO = 0.011


# ── Montagem ──────────────────────────────────────────────────────────────

def _recuperar(c, relogio, rec, tenant=A, quando=RECUPERADO_EM, falhou_em=ABERTURA):
    """Uma cobrança que falhou e foi paga: um ciclo `recuperado`, com fee."""
    relogio(falhou_em)
    _post(c, _evento(rec, f"E_{rec}_0"), tenant)
    relogio(quando)
    r = _post(c, _evento(rec, f"E_{rec}_pago", evento="automatic_pix.charge_succeeded"), tenant)
    assert r["ciclo"] == "recuperado", r
    ciclo = _ciclo(rec, tenant)
    assert ciclo["estado"] == cc.RECUPERADO and ciclo["fee"] > 0 and ciclo["valor"] == VALOR
    return ciclo


def _aviso(rec, valor=VALOR, id_devolucao="D_1", **extra) -> bytes:
    """O aviso de devolução do PSP, sobre o pagamento que recuperou o ciclo."""
    corpo = {"event": "automatic_pix.charge_refunded", "id_recorrencia": rec,
             "e2e_id": f"E_{rec}_pago"}
    if valor is not None:
        corpo["valor_devolvido"] = valor
    if id_devolucao is not None:
        corpo["id_devolucao"] = id_devolucao
    corpo.update(extra)
    return json.dumps(corpo).encode()


def _avisar(c, corpo: bytes, tenant=A):
    return c.post("/webhooks/pix-automatico", content=corpo,
                  headers={**_assinar(corpo), "x-tenant-id": tenant})


def _estornar(c, relogio, rec, quando, valor=VALOR, id_devolucao="D_1", tenant=A, **extra):
    relogio(quando)
    r = _avisar(c, _aviso(rec, valor, id_devolucao, **extra), tenant)
    assert r.status_code == 200, r.text
    return r.json()


def _linha(c, ciclo_id, tenant=A) -> dict:
    return _get(c, f"/ciclos/{ciclo_id}", tenant).json()


def _mes(c, mes, tenant=A) -> dict:
    return _get(c, "/metrics/involuntario/mes", tenant, mes=mes).json()


def _tem_chave(valor, chave) -> bool:
    if isinstance(valor, list):
        return any(_tem_chave(v, chave) for v in valor)
    if isinstance(valor, dict):
        return any(k == chave or _tem_chave(v, chave) for k, v in valor.items())
    return False


# ══════════════════════════════════════════════════════════════════════════
# As regras E2, E3 e E4
# ══════════════════════════════════════════════════════════════════════════

class TestDevolucaoTotalNoPrazo:

    def test_dia_10_a_fee_vai_a_zero_o_liquido_sai_do_mes_e_o_status_vira_encerrado(
            self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_total")
        fee, liquido = ciclo["fee"], round(VALOR - ciclo["fee"], 2)
        antes = _mes(cliente, "2026-09")
        assert antes["valor_liquido_recuperado"] == liquido and antes["recuperados"] == 1

        r = _estornar(cliente, relogio, "RN_total", RECUPERADO_EM + timedelta(days=10))
        assert r["estorno"] == cc.ESTORNO_REGISTRADO and r["no_prazo"] is True and r["total"] is True

        # No banco: o estado continua `recuperado`; a fee que vale é zero.
        depois = _ciclo("RN_total")
        assert depois["estado"] == cc.RECUPERADO
        assert depois["fee"] == fee and depois["valor"] == VALOR        # o original não muda
        assert depois["fee_estornada"] == fee and depois["valor_estornado"] == VALOR
        assert round(depois["fee"] - depois["fee_estornada"], 2) == 0.0

        # Na tela: encerrado sem recuperação, com o motivo, e sem líquido.
        detalhe = _linha(cliente, ciclo["id"])
        assert detalhe["ciclo"]["status"] == cc.STATUS_ENCERRADO
        assert detalhe["ciclo"]["estado"] == cc.RECUPERADO
        assert detalhe["ciclo"]["motivo_encerramento"] == "estorno_no_prazo"
        assert detalhe["ciclo"]["valor_liquido"] is None
        assert detalhe["ciclo"]["valor_cobranca"] == VALOR
        assert detalhe["ciclo"]["estorno_parcial"] is False
        assert detalhe["ciclo"]["estorno"]["total"] is True
        assert detalhe["ciclo"]["estorno"]["valor_devolvido"] == VALOR

        # O líquido saiu do mês.
        mes = _mes(cliente, "2026-09")
        assert mes["valor_liquido_recuperado"] == 0.0
        assert mes["estornos"] == {"quantidade": 1, "ciclos_estornados_por_inteiro": 1,
                                   "valor_liquido_estornado": liquido}
        assert mes["ciclos_abertos_no_mes"][cc.STATUS_ENCERRADO] == 1
        assert mes["ciclos_abertos_no_mes"][cc.STATUS_RECUPERADO] == 0

    def test_o_filtro_da_lista_o_trata_como_encerrado(self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_filtro")
        outro = _recuperar(cliente, relogio, "RN_fica")
        _estornar(cliente, relogio, "RN_filtro", RECUPERADO_EM + timedelta(days=3))
        ids = lambda status: {c["id"] for c in _get(cliente, "/ciclos", status=status).json()["ciclos"]}
        assert ids(cc.STATUS_ENCERRADO) == {ciclo["id"]}
        assert ids(cc.STATUS_RECUPERADO) == {outro["id"]}

    def test_a_linha_do_tempo_ganha_o_evento_e_a_recuperacao_fica_como_aconteceu(
            self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_tempo")
        liquido = round(VALOR - ciclo["fee"], 2)
        _estornar(cliente, relogio, "RN_tempo", RECUPERADO_EM + timedelta(days=10))
        eventos = _linha(cliente, ciclo["id"])["linha_do_tempo"]
        recuperado = [e for e in eventos if e["tipo"] == "recuperado"]
        estorno = [e for e in eventos if e["tipo"] == "estorno"]
        assert len(recuperado) == 1 and recuperado[0]["dados"] == {"valor_liquido": liquido}
        assert len(estorno) == 1
        assert estorno[0]["dados"] == {"valor_devolvido": VALOR, "no_prazo": True, "total": True}
        assert estorno[0]["quando"].startswith("2026-09-15T10:00:00")
        assert eventos.index(estorno[0]) > eventos.index(recuperado[0])


class TestDevolucaoParcialNoPrazo:

    def test_40_por_cento_a_fee_e_o_liquido_caem_40_por_cento(self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_parcial")
        fee, liquido = ciclo["fee"], round(VALOR - ciclo["fee"], 2)
        devolvido = round(VALOR * 0.4, 2)

        r = _estornar(cliente, relogio, "RN_parcial", RECUPERADO_EM + timedelta(days=10),
                      valor=devolvido)
        assert r["estorno"] == cc.ESTORNO_REGISTRADO and r["total"] is False

        depois = _ciclo("RN_parcial")
        assert depois["valor_estornado"] == devolvido
        assert depois["fee_estornada"] == pytest.approx(fee * 0.4, abs=CENTAVO)
        assert depois["fee"] == fee

        linha = _linha(cliente, ciclo["id"])["ciclo"]
        assert linha["status"] == cc.STATUS_RECUPERADO
        assert linha["estorno_parcial"] is True and linha["motivo_encerramento"] is None
        assert linha["valor_liquido"] == pytest.approx(liquido * 0.6, abs=CENTAVO)
        assert linha["estorno"] == {"valor_devolvido": devolvido, "total": False,
                                    "ultimo_em": "2026-09-15T10:00:00-03:00"}

        mes = _mes(cliente, "2026-09")
        assert mes["valor_liquido_recuperado"] == pytest.approx(liquido * 0.6, abs=CENTAVO)
        assert mes["estornos"]["quantidade"] == 1
        assert mes["estornos"]["ciclos_estornados_por_inteiro"] == 0
        assert mes["estornos"]["valor_liquido_estornado"] == pytest.approx(liquido * 0.4, abs=CENTAVO)


class TestDevolucaoForaDoPrazo:

    def test_dia_31_com_prazo_de_30_nada_muda_e_o_evento_aparece(self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_tarde")
        antes_banco = _ciclo("RN_tarde")
        antes_linha = _linha(cliente, ciclo["id"])["ciclo"]
        antes_setembro = _mes(cliente, "2026-09")

        r = _estornar(cliente, relogio, "RN_tarde", RECUPERADO_EM + timedelta(days=31))
        assert r == {"status": "ok", "evento": "cobranca_devolvida", "pipeline": False,
                     "estorno": cc.ESTORNO_FORA_DO_PRAZO, "ciclo_id": ciclo["id"],
                     "no_prazo": False, "total": False, "valor_considerado": 0.0}

        depois_banco = _ciclo("RN_tarde")
        for campo in ("valor", "fee", "valor_estornado", "fee_estornada", "estornado_em", "estado"):
            assert depois_banco[campo] == antes_banco[campo], campo
        assert depois_banco["valor_estornado"] is None and depois_banco["fee_estornada"] is None

        detalhe = _linha(cliente, ciclo["id"])
        for campo in ("status", "valor_liquido", "valor_cobranca", "estorno", "estorno_parcial",
                      "motivo_encerramento"):
            assert detalhe["ciclo"][campo] == antes_linha[campo], campo
        assert detalhe["ciclo"]["estorno"] is None
        estorno = [e for e in detalhe["linha_do_tempo"] if e["tipo"] == "estorno"]
        assert [e["dados"] for e in estorno] == [
            {"valor_devolvido": VALOR, "no_prazo": False, "total": False}]

        assert _mes(cliente, "2026-09") == antes_setembro
        outubro = _mes(cliente, "2026-10")
        assert outubro["valor_liquido_recuperado"] == 0.0
        assert outubro["estornos"]["quantidade"] == 0

    def test_o_limite_o_dia_30_ainda_esta_no_prazo_e_um_minuto_depois_nao(self, cliente, relogio):
        _recuperar(cliente, relogio, "RN_limite_a")
        _recuperar(cliente, relogio, "RN_limite_b")
        no_dia_30 = _estornar(cliente, relogio, "RN_limite_a", RECUPERADO_EM + timedelta(days=30))
        depois = _estornar(cliente, relogio, "RN_limite_b",
                           RECUPERADO_EM + timedelta(days=30, minutes=1))
        assert no_dia_30["estorno"] == cc.ESTORNO_REGISTRADO and no_dia_30["no_prazo"] is True
        assert depois["estorno"] == cc.ESTORNO_FORA_DO_PRAZO and depois["no_prazo"] is False


# ══════════════════════════════════════════════════════════════════════════
# E1: o prazo é de cada empresa
# ══════════════════════════════════════════════════════════════════════════

class TestPrazoPorEmpresa:

    def test_o_padrao_e_30_e_esta_na_configuracao(self, cliente, producao):
        assert configuracao.PADROES_DE_PRODUCAO["prazo_estorno_dias"] == 30
        r = cliente.get("/configuracao", headers=cliente.projeto.bearer(A))
        assert r.json()["configuracao"]["prazo_estorno_dias"] == 30

    def test_com_90_dias_a_devolucao_do_dia_60_estorna_so_para_essa_empresa(self, cliente, relogio):
        r = cliente.put("/configuracao", json={"prazo_estorno_dias": 90},
                        headers=cliente.projeto.bearer(A, papel="admin"))
        assert r.status_code == 200 and r.json()["configuracao"]["prazo_estorno_dias"] == 90
        de_a = _recuperar(cliente, relogio, "RN_90", tenant=A)
        de_b = _recuperar(cliente, relogio, "RN_30", tenant=B)

        dia_60 = RECUPERADO_EM + timedelta(days=60)
        em_a = _estornar(cliente, relogio, "RN_90", dia_60, tenant=A)
        em_b = _estornar(cliente, relogio, "RN_30", dia_60, tenant=B)

        assert em_a["estorno"] == cc.ESTORNO_REGISTRADO and em_a["total"] is True
        assert em_b["estorno"] == cc.ESTORNO_FORA_DO_PRAZO
        assert _linha(cliente, de_a["id"], A)["ciclo"]["status"] == cc.STATUS_ENCERRADO
        assert _linha(cliente, de_b["id"], B)["ciclo"]["status"] == cc.STATUS_RECUPERADO
        assert _ciclo("RN_30", B)["fee_estornada"] is None

    @pytest.mark.parametrize("valor", [0, -1, 366, "30", 30.5, True, None])
    def test_prazo_invalido_e_422_e_nada_e_gravado(self, cliente, valor):
        r = cliente.put("/configuracao", json={"prazo_estorno_dias": valor},
                        headers=cliente.projeto.bearer(A, papel="owner"))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["campo"] == "prazo_estorno_dias"
        assert configuracao._gravada(A) == {}

    def test_membro_nao_muda_o_prazo(self, cliente):
        r = cliente.put("/configuracao", json={"prazo_estorno_dias": 365},
                        headers=cliente.projeto.bearer(A, papel="membro"))
        assert r.status_code == 403 and configuracao._gravada(A) == {}


# ══════════════════════════════════════════════════════════════════════════
# E5: o mês já fechado não é reescrito
# ══════════════════════════════════════════════════════════════════════════

class TestOMesFechadoNaoMuda:

    def test_recuperado_em_setembro_e_estornado_em_outubro(self, cliente, relogio):
        setembro_20 = datetime(2026, 9, 20, 10, 0)
        ciclo = _recuperar(cliente, relogio, "RN_e5", quando=setembro_20,
                           falhou_em=datetime(2026, 9, 18, 9, 0))
        liquido = round(VALOR - ciclo["fee"], 2)
        relogio(datetime(2026, 9, 30, 23, 0))
        setembro_antes = _mes(cliente, "2026-09")
        assert setembro_antes["valor_liquido_recuperado"] == liquido

        _estornar(cliente, relogio, "RN_e5", datetime(2026, 10, 5, 15, 0))

        # Setembro: o valor, as contagens, a taxa e os estornos ficam iguais.
        setembro = _mes(cliente, "2026-09")
        for campo in ("valor_liquido_recuperado", "recuperados", "encerrados_sem_recuperacao",
                      "taxa_recuperacao", "estornos"):
            assert setembro[campo] == setembro_antes[campo], campo
        assert setembro["estornos"]["quantidade"] == 0

        # Outubro: a parcela negativa.
        outubro = _mes(cliente, "2026-10")
        assert outubro["valor_liquido_recuperado"] == -liquido
        assert outubro["recuperados"] == 0
        assert outubro["estornos"] == {"quantidade": 1, "ciclos_estornados_por_inteiro": 1,
                                       "valor_liquido_estornado": liquido}

        # A série diária: +líquido em 20/09, −líquido em 05/10, e mais nada.
        relogio(datetime(2026, 10, 6, 9, 0))
        pontos = {p["dia"]: p for p in _get(cliente, "/metrics/involuntario/serie",
                                            dias=30).json()["pontos"]}
        assert pontos["2026-09-20"]["valor_liquido_recuperado"] == liquido
        assert pontos["2026-09-20"]["valor_liquido_estornado"] == 0.0
        assert pontos["2026-10-05"]["valor_liquido_recuperado"] == -liquido
        assert pontos["2026-10-05"]["valor_liquido_estornado"] == liquido
        assert round(sum(p["valor_liquido_recuperado"] for p in pontos.values()), 2) == 0.0

    def test_o_extrato_tem_uma_linha_positiva_e_uma_negativa_cada_uma_no_seu_mes(
            self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_extrato", quando=datetime(2026, 9, 20, 10, 0),
                           falhou_em=datetime(2026, 9, 18, 9, 0))
        fee, liquido = ciclo["fee"], round(VALOR - ciclo["fee"], 2)
        parcial = round(VALOR * 0.5, 2)
        _estornar(cliente, relogio, "RN_extrato", datetime(2026, 10, 5, 15, 0), valor=parcial)

        setembro = cc.extrato_do_periodo(A, datetime(2026, 9, 1), datetime(2026, 10, 1))
        outubro = cc.extrato_do_periodo(A, datetime(2026, 10, 1), datetime(2026, 11, 1))
        assert setembro == [{"tipo": "recuperacao", "ciclo_id": ciclo["id"],
                             "id_recorrencia": "RN_extrato", "quando": "2026-09-20T10:00:00",
                             "valor_base": VALOR, "fee": fee, "liquido": liquido}]
        assert len(outubro) == 1 and outubro[0]["tipo"] == "estorno"
        assert outubro[0]["valor_base"] == -parcial and outubro[0]["quando"] == "2026-10-05T15:00:00"
        assert outubro[0]["fee"] == pytest.approx(-fee * 0.5, abs=CENTAVO)
        assert outubro[0]["liquido"] == pytest.approx(-liquido * 0.5, abs=CENTAVO)
        # O extrato e a métrica do mês contam a mesma coisa.
        assert _mes(cliente, "2026-10")["valor_liquido_recuperado"] == outubro[0]["liquido"]
        assert cc.extrato_do_periodo(B, datetime(2026, 9, 1), datetime(2026, 11, 1)) == []


# ══════════════════════════════════════════════════════════════════════════
# E7: reenvio e parciais
# ══════════════════════════════════════════════════════════════════════════

class TestIdempotenciaEParciais:

    def test_o_mesmo_aviso_reenviado_conta_uma_vez(self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_reenvio")
        parcial = round(VALOR * 0.4, 2)
        dia = RECUPERADO_EM + timedelta(days=5)
        primeiro = _estornar(cliente, relogio, "RN_reenvio", dia, valor=parcial, id_devolucao="D_x")
        depois_do_primeiro = _ciclo("RN_reenvio")
        for quando in (dia, dia + timedelta(hours=3), dia + timedelta(days=40)):
            de_novo = _estornar(cliente, relogio, "RN_reenvio", quando, valor=parcial,
                                id_devolucao="D_x")
            assert de_novo["estorno"] == cc.ESTORNO_REENVIO
        assert primeiro["estorno"] == cc.ESTORNO_REGISTRADO
        agora = _ciclo("RN_reenvio")
        for campo in ("valor_estornado", "fee_estornada", "estornado_em"):
            assert agora[campo] == depois_do_primeiro[campo], campo
        assert len(cc.estornos_do_ciclo(ciclo["id"])) == 1
        assert len([e for e in _linha(cliente, ciclo["id"])["linha_do_tempo"]
                    if e["tipo"] == "estorno"]) == 1

    def test_dois_parciais_somam_e_nunca_passam_do_total(self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_soma")
        fee = ciclo["fee"]
        a, b = round(VALOR * 0.4, 2), round(VALOR * 0.3, 2)
        _estornar(cliente, relogio, "RN_soma", RECUPERADO_EM + timedelta(days=3), a, "D_a")
        _estornar(cliente, relogio, "RN_soma", RECUPERADO_EM + timedelta(days=6), b, "D_b")
        meio = _ciclo("RN_soma")
        assert meio["valor_estornado"] == pytest.approx(a + b, abs=CENTAVO)
        assert meio["fee_estornada"] == pytest.approx(fee * 0.7, abs=2 * CENTAVO)
        assert _linha(cliente, ciclo["id"])["ciclo"]["status"] == cc.STATUS_RECUPERADO

        # Um terceiro aviso de 50%: só os 30% que faltavam entram.
        terceiro = _estornar(cliente, relogio, "RN_soma", RECUPERADO_EM + timedelta(days=9),
                             round(VALOR * 0.5, 2), "D_c")
        assert terceiro["total"] is True
        assert terceiro["valor_considerado"] == pytest.approx(VALOR - a - b, abs=CENTAVO)
        fim = _ciclo("RN_soma")
        assert fim["valor_estornado"] == VALOR
        assert fim["fee_estornada"] == fee                       # fecha no centavo
        assert _linha(cliente, ciclo["id"])["ciclo"]["status"] == cc.STATUS_ENCERRADO

        # Depois do total, mais nada entra.
        quarto = _estornar(cliente, relogio, "RN_soma", RECUPERADO_EM + timedelta(days=12),
                           VALOR, "D_d")
        assert quarto["estorno"] == cc.ESTORNO_JA_TOTAL and quarto["valor_considerado"] == 0.0
        assert _ciclo("RN_soma")["valor_estornado"] == VALOR
        assert _ciclo("RN_soma")["fee_estornada"] == fee
        mes = _mes(cliente, "2026-09")
        assert mes["valor_liquido_recuperado"] == pytest.approx(0.0, abs=CENTAVO)
        assert mes["estornos"]["quantidade"] == 3
        assert mes["estornos"]["ciclos_estornados_por_inteiro"] == 1

    def test_aviso_maior_que_a_cobranca_estorna_so_a_cobranca(self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_maior")
        r = _estornar(cliente, relogio, "RN_maior", RECUPERADO_EM + timedelta(days=1), VALOR * 10)
        assert r["valor_considerado"] == VALOR and r["total"] is True
        depois = _ciclo("RN_maior")
        assert depois["valor_estornado"] == VALOR and depois["fee_estornada"] == ciclo["fee"]

    def test_sem_id_de_devolucao_o_reenvio_ainda_conta_uma_vez(self, cliente, relogio):
        _recuperar(cliente, relogio, "RN_sem_id")
        parcial = round(VALOR * 0.25, 2)
        dia = RECUPERADO_EM + timedelta(days=2)
        primeiro = _estornar(cliente, relogio, "RN_sem_id", dia, parcial, id_devolucao=None)
        segundo = _estornar(cliente, relogio, "RN_sem_id", dia, parcial, id_devolucao=None)
        assert [primeiro["estorno"], segundo["estorno"]] == [cc.ESTORNO_REGISTRADO, cc.ESTORNO_REENVIO]
        assert _ciclo("RN_sem_id")["valor_estornado"] == parcial

    def test_dois_avisos_ao_mesmo_tempo_contam_uma_vez(self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_corrida")
        relogio(RECUPERADO_EM + timedelta(days=4))

        async def dois():
            return await asyncio.gather(*[
                app_module._registrar_devolucao(A, "RN_corrida", None, "E_RN_corrida_pago",
                                                VALOR, "D_mesmo") for _ in range(2)])
        resultados = sorted(r["estorno"] for r in asyncio.run(dois()))
        assert resultados == [cc.ESTORNO_REENVIO, cc.ESTORNO_REGISTRADO]
        assert _ciclo("RN_corrida")["fee_estornada"] == ciclo["fee"]


# ══════════════════════════════════════════════════════════════════════════
# R12 e E6: quem pode estornar
# ══════════════════════════════════════════════════════════════════════════

class TestIsolamentoEOrigem:

    def test_aviso_de_outra_empresa_e_404_igual_ao_inexistente_e_nao_mexe_no_ciclo(
            self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_de_a", tenant=A)
        antes = _ciclo("RN_de_a", A)
        relogio(RECUPERADO_EM + timedelta(days=5))

        de_outra = _avisar(cliente, _aviso("RN_de_a"), tenant=B)
        inexistente = _avisar(cliente, _aviso("RN_que_nao_existe"), tenant=B)
        assert de_outra.status_code == inexistente.status_code == 404
        assert de_outra.json() == inexistente.json()
        assert "RN_de_a" not in de_outra.text and A not in de_outra.text

        assert _ciclo("RN_de_a", A) == antes
        assert cc.estornos_do_ciclo(ciclo["id"]) == []
        assert _linha(cliente, ciclo["id"], A)["ciclo"]["status"] == cc.STATUS_RECUPERADO

    def test_o_id_de_cobranca_de_outra_empresa_tambem_nao_atravessa(self, cliente, relogio):
        _recuperar(cliente, relogio, "RN_cob_a", tenant=A)
        original = _ciclo("RN_cob_a", A)["id_cobranca_original"]
        relogio(RECUPERADO_EM + timedelta(days=5))
        r = _avisar(cliente, _aviso("RN_outra_recorrencia", id_cobranca=original), tenant=B)
        assert r.status_code == 404
        assert _ciclo("RN_cob_a", A)["valor_estornado"] is None

    def test_aviso_sem_assinatura_do_psp_e_recusado(self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_sem_assinatura")
        relogio(RECUPERADO_EM + timedelta(days=5))
        corpo = _aviso("RN_sem_assinatura")
        r = cliente.post("/webhooks/pix-automatico", content=corpo,
                         headers={"content-type": "application/json", "x-tenant-id": A})
        assert r.status_code in (401, 403), r.text
        com_token = cliente.post("/webhooks/pix-automatico", content=corpo,
                                 headers={**cliente.projeto.bearer(A, papel="owner"),
                                          "content-type": "application/json", "x-tenant-id": A})
        assert com_token.status_code in (401, 403)
        assert cc.estornos_do_ciclo(ciclo["id"]) == []

    def test_nenhuma_rota_permite_declarar_estorno_a_mao(self, cliente, relogio, monkeypatch):
        """E6: só o aviso do PSP gera estorno. A única rota com "estorn" no nome é
        a de simulação, que não tem login, não existe para a empresa e responde
        403 fora de development/demo. Nenhuma rota autenticada escreve estorno."""
        # Produção, declarada: a suíte não pode depender do ENV do `.env` da máquina.
        monkeypatch.setenv("ENV", "production")
        rotas = [r for r in app_module.app.routes if hasattr(r, "methods")]
        com_nome = [r.path for r in rotas
                    if any(p in r.path.lower() for p in ("estorn", "devol", "refund", "chargeback"))]
        assert com_nome == ["/simulate/pix-estornado"]

        autenticadas_que_escrevem = set()
        for r in rotas:
            deps = {getattr(d.call, "__name__", "")
                    for d in getattr(getattr(r, "dependant", None), "dependencies", [])}
            if deps & {"get_tenant_id", "get_conta"} and r.methods & {"POST", "PUT", "PATCH", "DELETE"}:
                autenticadas_que_escrevem.add(r.path)
        assert autenticadas_que_escrevem == {
            "/clientes", "/clientes/lote", "/clientes/{customer_id_externo}", "/clientes/importar",
            "/insights/enviar", "/ciclos/{ciclo_id}/mensagens/escolher",
            "/ciclos/{ciclo_id}/mensagens/regerar", "/configuracao",
            "/integracao/chaves", "/integracao/chaves/{chave_id}",
            # Rodada 3, Fase 3: a simulação do gateway. Escreve só nos arquivos
            # de simulação da empresa, e nenhuma destas rotas gera estorno.
            "/simulacao", "/simulacao/cliente", "/simulacao/cobrar",
            "/simulacao/avancar", "/simulacao/retencao",
            # Rodada 3, Fase 4: o assistente. É POST, e não escreve nada.
            "/assistente",
            # Rodada 3, Fase 5: o evento de comportamento. Roda o pipeline
            # voluntário; não toca o ciclo de cobrança nem gera estorno.
            "/eventos",
            # Rodada 3, Fase 6: os direitos do titular e o descadastro. Apagam
            # texto e contato, e marcam "não contatar"; nenhuma gera estorno.
            "/titular/exportar", "/titular/anonimizar",
            "/clientes/{customer_id_externo}/nao-contatar"}

        ciclo = _recuperar(cliente, relogio, "RN_a_mao")
        dono = cliente.projeto.bearer(A, papel="owner")
        for metodo, caminho in (("POST", f"/ciclos/{ciclo['id']}/estorno"),
                                ("POST", f"/ciclos/{ciclo['id']}/estornar"),
                                ("PUT", f"/ciclos/{ciclo['id']}"),
                                ("PATCH", f"/ciclos/{ciclo['id']}"),
                                ("POST", "/estornos"),
                                ("POST", "/simulate/pix-estornado")):
            r = cliente.request(metodo, caminho, headers=dono,
                                json={"id_recorrencia": "RN_a_mao", "tenant_id": A, "valor": VALOR})
            assert r.status_code in (403, 404, 405), (metodo, caminho, r.status_code)
        # O PUT da configuração não aceita nada que estorne.
        r = cliente.put("/configuracao", headers=dono, json={"valor_estornado": VALOR})
        assert r.status_code == 422
        assert cc.estornos_do_ciclo(ciclo["id"]) == []
        assert _ciclo("RN_a_mao")["fee_estornada"] is None

    def test_a_simulacao_so_existe_em_development_ou_demo(self, cliente, relogio, monkeypatch):
        ciclo = _recuperar(cliente, relogio, "RN_sim")
        relogio(RECUPERADO_EM + timedelta(days=5))
        corpo = {"id_recorrencia": "RN_sim", "tenant_id": A}
        for env in ("production", "staging", ""):
            monkeypatch.setenv("ENV", env)
            assert cliente.post("/simulate/pix-estornado", json=corpo).status_code == 403
        monkeypatch.delenv("ENV", raising=False)
        assert cliente.post("/simulate/pix-estornado", json=corpo).status_code == 403
        assert cc.estornos_do_ciclo(ciclo["id"]) == []

        monkeypatch.setenv("ENV", "development")
        r = cliente.post("/simulate/pix-estornado", json={**corpo, "id_devolucao": "D_sim"})
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "devolucao_processada", "id_recorrencia": "RN_sim",
                            "estorno": cc.ESTORNO_REGISTRADO, "ciclo_id": ciclo["id"],
                            "no_prazo": True, "total": True, "valor_considerado": VALOR}
        de_novo = cliente.post("/simulate/pix-estornado", json={**corpo, "id_devolucao": "D_sim"})
        assert de_novo.json()["estorno"] == cc.ESTORNO_REENVIO
        assert cliente.post("/simulate/pix-estornado",
                            json={"id_recorrencia": "RN_nao_existe", "tenant_id": A}).status_code == 404
        assert cliente.post("/simulate/pix-estornado",
                            json={**corpo, "valor": -5}).status_code == 422


# ══════════════════════════════════════════════════════════════════════════
# O que o aviso precisa trazer, e o que acontece quando não traz
# ══════════════════════════════════════════════════════════════════════════

class TestOAviso:

    @pytest.mark.parametrize("extra", [
        {"valor_devolvido": "abc"}, {"valor_devolvido": 0}, {"valor_devolvido": -10},
        {"valor_devolvido": "NaN"}, {"valor_devolvido": 1e300}, {}])
    def test_sem_valor_legivel_e_422_e_nada_muda(self, cliente, relogio, extra):
        ciclo = _recuperar(cliente, relogio, "RN_valor")
        relogio(RECUPERADO_EM + timedelta(days=5))
        r = _avisar(cliente, _aviso("RN_valor", valor=None, **extra))
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["motivo"] == "valor_devolvido_nao_utilizavel"
        assert cc.estornos_do_ciclo(ciclo["id"]) == []
        assert _ciclo("RN_valor")["fee_estornada"] is None

    def test_ciclo_que_nao_esta_recuperado_nao_tem_o_que_estornar(self, cliente, relogio):
        relogio(ABERTURA)
        _post(cliente, _evento("RN_aberto", "E_RN_aberto_0"))
        ciclo = _ciclo("RN_aberto")
        assert ciclo["estado"] == cc.RECOBRANDO
        relogio(ABERTURA + timedelta(days=1))
        r = _avisar(cliente, _aviso("RN_aberto", id_cobranca=ciclo["id_cobranca_original"]))
        assert r.status_code == 200 and r.json()["estorno"] == cc.ESTORNO_CICLO_NAO_RECUPERADO
        assert cc.estornos_do_ciclo(ciclo["id"]) == []
        assert _ciclo("RN_aberto")["estado"] == cc.RECOBRANDO

    def test_falha_de_gravacao_e_503_e_o_reenvio_conta_uma_vez(self, cliente, relogio, monkeypatch):
        ciclo = _recuperar(cliente, relogio, "RN_503")
        relogio(RECUPERADO_EM + timedelta(days=5))
        original = cc.registrar_estorno

        def quebrado(*a, **k):
            raise sqlite3.OperationalError("disco cheio (teste)")
        monkeypatch.setattr(cc, "registrar_estorno", quebrado)
        r = _avisar(cliente, _aviso("RN_503"))
        assert r.status_code == 503 and r.headers["Retry-After"] == "30"
        assert _ciclo("RN_503")["fee_estornada"] is None

        monkeypatch.setattr(cc, "registrar_estorno", original)
        assert _avisar(cliente, _aviso("RN_503")).json()["estorno"] == cc.ESTORNO_REGISTRADO
        assert _avisar(cliente, _aviso("RN_503")).json()["estorno"] == cc.ESTORNO_REENVIO
        assert _ciclo("RN_503")["fee_estornada"] == ciclo["fee"]

    def test_o_aviso_acha_o_ciclo_pelo_e2e_do_pagamento_sem_o_id_da_recorrencia(
            self, cliente, relogio):
        """Com tentativa paga, o e2e do pagamento basta para achar o ciclo."""
        from tests.test_mensagens_involuntario import _agendador
        relogio(ABERTURA)
        _post(cliente, _evento("RN_e2e", "E_RN_e2e_0"))
        ciclo = _ciclo("RN_e2e")
        plano = cc.tentativas_do_ciclo(ciclo["id"])
        quando = datetime.fromisoformat(plano[0]["agendada_para"]) + timedelta(hours=1)
        relogio(quando)
        disparo = _agendador(quando)[0]
        _post(cliente, _evento("RN_e2e", "E_pagamento_unico", id_cobranca=disparo["id_cobranca"],
                               evento="automatic_pix.charge_succeeded"))
        assert _ciclo("RN_e2e")["estado"] == cc.RECUPERADO

        relogio(quando + timedelta(days=3))
        corpo = json.dumps({"event": "automatic_pix.charge_refunded", "e2e_id": "E_pagamento_unico",
                            "valor_devolvido": VALOR, "id_devolucao": "D_e2e"}).encode()
        r = _avisar(cliente, corpo)
        assert r.status_code == 200 and r.json()["ciclo_id"] == ciclo["id"]
        assert r.json()["estorno"] == cc.ESTORNO_REGISTRADO


# ══════════════════════════════════════════════════════════════════════════
# O adaptador do Pix: o quinto evento, aditivo
# ══════════════════════════════════════════════════════════════════════════

class TestAdaptador:

    def _parse(self, corpo):
        return asyncio.run(pg.PixAutomaticoAdapter().parse_pix_event(corpo))

    @pytest.mark.parametrize("corpo", [
        {"event": "automatic_pix.charge_refunded"}, {"event": "recurrence.charge_refunded"},
        {"event": "charge.refunded"}, {"status": "refunded"}, {"status": "DEVOLVIDO"},
        {"data": {"status": "refunded"}}])
    def test_reconhece_o_evento_e_o_status_cru(self, corpo):
        evento = self._parse({**corpo, "id_recorrencia": "RN_x", "valor": 50})
        assert evento["status"] == pg.STATUS_COBRANCA_DEVOLVIDA == "cobranca_devolvida"
        assert pg.STATUS_COBRANCA_DEVOLVIDA in pg.PIX_STATUSES

    @pytest.mark.parametrize("campos, esperado", [
        ({"valor_devolvido": 119.96}, 119.96),
        ({"valor_devolvido": "119,96"}, 119.96),
        ({"devolucao": {"valor": "50.00", "id": "D9"}}, 50.0),
        ({"refund": {"amount": 10}}, 10.0),
        ({"refunded_amount_cents": 11996}, 119.96),
        ({"valor": 299.9}, 299.9),                              # sem campo próprio: o valor do evento
        ({"valor_devolvido": 40, "valor": 299.9}, 40.0),        # o campo próprio vale mais
        ({}, None), ({"valor_devolvido": "abc"}, None), ({"valor_devolvido": -1}, None),
        ({"valor_devolvido": 0, "valor": 299.9}, None),         # zero declarado não vira o total
    ])
    def test_o_valor_devolvido(self, campos, esperado):
        evento = self._parse({"event": "automatic_pix.charge_refunded", "id_recorrencia": "RN_x",
                              **campos})
        assert evento["valor_devolvido"] == esperado

    @pytest.mark.parametrize("campos, esperado", [
        ({"id_devolucao": "D1"}, "D1"), ({"devolucao": {"id": "D2"}}, "D2"),
        ({"devolucao": {"rtrId": "D3"}}, "D3"), ({"rtrId": "D4"}, "D4"),
        ({"refund_id": "D5"}, "D5"), ({"refund": {"id": "D6"}}, "D6"), ({}, ""),
        ({"id_devolucao": {"a": 1}}, "")])
    def test_o_id_da_devolucao(self, campos, esperado):
        evento = self._parse({"event": "charge.refunded", "id_recorrencia": "RN_x", "valor": 5,
                              **campos})
        assert evento["id_devolucao"] == esperado

    def test_os_quatro_eventos_antigos_saem_identicos_sem_os_campos_novos(self):
        chaves = {"e2e_id", "valor", "status", "ispb_pagador", "id_recorrencia", "codigo_falha",
                  "id_cobranca", "degradacoes"}
        for nome in ("automatic_pix.charge_failed", "automatic_pix.charge_succeeded",
                     "automatic_pix.authorization_revoked", "automatic_pix.authorization_created"):
            evento = self._parse({"event": nome, "id_recorrencia": "RN_x", "e2e_id": "E1",
                                  "valor": 10, "valor_devolvido": 5, "id_devolucao": "D1"})
            assert set(evento) == chaves, nome
        assert set(self._parse({"event": "charge.refunded", "id_recorrencia": "RN_x",
                                "valor": 10})) == chaves | {"valor_devolvido", "id_devolucao"}

    def test_a_chave_pix_do_payload_e_descartada(self):
        corpo = {"event": "automatic_pix.charge_refunded", "id_recorrencia": "RN_x",
                 "valor_devolvido": 10, "id_devolucao": "D1",
                 "pix_key": "pagador@exemplo.com.br", "chave": "11999990000",
                 "chave_pix": "123.456.789-09", "payer_key": "abc", "key": "xyz",
                 "payer": {"name": "Mariana Albuquerque", "document": "12345678909"}}
        texto = json.dumps(self._parse(corpo))
        for proibido in ("pagador@exemplo.com.br", "11999990000", "123.456.789-09", "Mariana",
                         "12345678909", "payer_key", "pix_key"):
            assert proibido not in texto, proibido


# ══════════════════════════════════════════════════════════════════════════
# LGPD e o que a tarefa manda não mudar
# ══════════════════════════════════════════════════════════════════════════

class TestSemDadoPessoalNovo:

    PESSOAL = {"pix_key": "pagador.final@exemplo.com.br", "chave_pix": "123.456.789-09",
               "chave": "+5511988887777", "payer": {"name": "Mariana Albuquerque Tavares",
                                                    "document": "12345678909"},
               "nome": "Mariana Albuquerque Tavares", "email": "pagador.final@exemplo.com.br"}
    PROIBIDOS = ("pagador.final", "@exemplo", "123.456.789-09", "5511988887777", "Mariana",
                 "Albuquerque", "12345678909")

    def test_o_banco_cru_nao_ganha_nada_do_pagador(self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_lgpd")
        r = _estornar(cliente, relogio, "RN_lgpd", RECUPERADO_EM + timedelta(days=5),
                      **self.PESSOAL)
        assert r["estorno"] == cc.ESTORNO_REGISTRADO
        assert not any(p in json.dumps(r) for p in self.PROIBIDOS)

        with sqlite3.connect(cc.caminho_do_banco()) as conn:
            colunas = [l[1] for l in conn.execute("PRAGMA table_info(estornos_ciclo)")]
            estornos = [tuple(l) for l in conn.execute("SELECT * FROM estornos_ciclo")]
            ciclos = [tuple(l) for l in conn.execute("SELECT * FROM ciclos_cobranca")]
            tentativas = [tuple(l) for l in conn.execute("SELECT * FROM tentativas_cobranca")]
        assert colunas == ["id", "ciclo_id", "id_devolucao", "valor_devolvido", "valor_considerado",
                           "fee_devolvida", "no_prazo", "recebido_em"]
        assert len(estornos) == 1 and estornos[0][1] == ciclo["id"]
        cru = repr(estornos) + repr(ciclos) + repr(tentativas)
        for proibido in self.PROIBIDOS:
            assert proibido not in cru, proibido

    def test_a_resposta_da_rota_nao_tem_a_fee_nem_dado_do_pagador(self, cliente, relogio):
        ciclo = _recuperar(cliente, relogio, "RN_r11")
        _estornar(cliente, relogio, "RN_r11", RECUPERADO_EM + timedelta(days=5),
                  valor=round(VALOR * 0.4, 2), **self.PESSOAL)
        respostas = [_linha(cliente, ciclo["id"]), _get(cliente, "/ciclos").json(),
                     _mes(cliente, "2026-09"),
                     _get(cliente, "/metrics/involuntario/serie", dias=30).json()]
        for resposta in respostas:
            texto = json.dumps(resposta)
            assert not _tem_chave(resposta, "fee") and not _tem_chave(resposta, "fee_estornada")
            assert not _tem_chave(resposta, "fee_devolvida")
            assert not any(p in texto for p in self.PROIBIDOS)
        # O valor da fee (original e estornada) não aparece como número em lugar nenhum.
        depois = _ciclo("RN_r11")
        for numero in (ciclo["fee"], depois["fee_estornada"]):
            assert str(numero) not in json.dumps(respostas[0]), numero

    def test_a_trilha_do_art_20_nao_ganha_linha(self, cliente, relogio):
        _recuperar(cliente, relogio, "RN_trilha")
        antes = trilha.decisoes_do_sujeito(A, "RN_trilha", limite=200)
        cadeia_antes = trilha.verificar_cadeia(A)
        _estornar(cliente, relogio, "RN_trilha", RECUPERADO_EM + timedelta(days=5))
        assert trilha.decisoes_do_sujeito(A, "RN_trilha", limite=200) == antes
        cadeia = trilha.verificar_cadeia(A)
        assert cadeia["integra"] is True and cadeia["linhas"] == cadeia_antes["linhas"]

    def test_o_rotulo_recovered_do_dataset_de_treino_nao_muda(self, cliente, relogio):
        from crai.dunning import recovery_log
        _recuperar(cliente, relogio, "RN_rotulo")

        def linhas():
            with sqlite3.connect(recovery_log.caminho_do_banco()) as conn:
                conn.row_factory = sqlite3.Row
                return [dict(l) for l in conn.execute(
                    "SELECT * FROM ciclos_recuperacao WHERE customer_id = ?", ("RN_rotulo",))]
        antes = linhas()
        assert len(antes) == 1 and antes[0]["recovered"] == 1 and antes[0]["success_fee"] > 0
        _estornar(cliente, relogio, "RN_rotulo", RECUPERADO_EM + timedelta(days=5))
        assert linhas() == antes


# ══════════════════════════════════════════════════════════════════════════
# A tradução do status: Python = SQL, também com estorno
# ══════════════════════════════════════════════════════════════════════════

class TestStatusDaTela:

    def test_estorno_total_so_muda_o_status_do_recuperado(self):
        assert cc.status_da_tela(cc.RECUPERADO, False, estorno_total=True) == cc.STATUS_ENCERRADO
        assert cc.status_da_tela(cc.RECUPERADO, False) == cc.STATUS_RECUPERADO
        for estado in cc.ESTADOS:
            if estado != cc.RECUPERADO:
                assert cc.status_da_tela(estado, True, estorno_total=True) == \
                    cc.status_da_tela(estado, True)

    @pytest.mark.parametrize("estornado, esperado", [
        (None, cc.STATUS_RECUPERADO), (0.0, cc.STATUS_RECUPERADO), (99.99, cc.STATUS_RECUPERADO),
        (100.0, cc.STATUS_ENCERRADO), (99.996, cc.STATUS_ENCERRADO), (150.0, cc.STATUS_ENCERRADO)])
    def test_python_e_sql_dao_o_mesmo_status(self, estornado, esperado):
        ciclo = cc.abrir_ciclo(A, "RN_status", 100.0, "insufficient_funds", ABERTURA,
                               id_cobranca="cob_status")
        cc.fechar_como_recuperado(ciclo["id"], 20.0, RECUPERADO_EM)
        if estornado is not None:
            conn = cc._conectar()
            try:
                conn.execute("UPDATE ciclos_cobranca SET valor_estornado = ? WHERE id = ?",
                             (estornado, ciclo["id"]))
                conn.commit()
            finally:
                conn.close()
        do_sql = cc.ciclo_do_tenant(A, ciclo["id"])
        em_python = cc.status_da_tela(do_sql["estado"], False,
                                      estorno_total=cc.estornado_por_inteiro(do_sql))
        assert do_sql["status"] == em_python == esperado


# ══════════════════════════════════════════════════════════════════════════
# A regra, direto na função
# ══════════════════════════════════════════════════════════════════════════

class TestRegistrarEstorno:

    def _recuperado(self, valor=100.0, fee=20.0):
        ciclo = cc.abrir_ciclo(A, "RN_f", valor, "insufficient_funds", ABERTURA, id_cobranca="cob_f")
        cc.fechar_como_recuperado(ciclo["id"], fee, RECUPERADO_EM)
        return ciclo["id"]

    def test_tres_tercos_fecham_a_fee_no_centavo(self):
        ciclo_id = self._recuperado(valor=100.0, fee=20.0)
        dia = RECUPERADO_EM + timedelta(days=1)
        fees = [cc.registrar_estorno(ciclo_id, f"D{i}", v, 30, dia)["fee_devolvida"]
                for i, v in enumerate((33.33, 33.33, 33.34))]
        assert fees == [6.67, 6.67, 6.66] and round(sum(fees), 2) == 20.0
        ciclo = cc.ciclo_por_id(ciclo_id)
        assert ciclo["valor_estornado"] == 100.0 and ciclo["fee_estornada"] == 20.0
        assert cc.estornado_por_inteiro(ciclo) is True

    @pytest.mark.parametrize("argumentos", [
        ("", 10.0, 30), ("   ", 10.0, 30), ("D1", 0, 30), ("D1", -5, 30),
        ("D1", float("nan"), 30), ("D1", float("inf"), 30), ("D1", 10.0, 0), ("D1", 10.0, -1),
        ("D1", 10.0, 30.5), ("D1", 10.0, True), ("D1", 10.0, "30")])
    def test_argumento_torto_levanta_e_nada_e_gravado(self, argumentos):
        ciclo_id = self._recuperado()
        with pytest.raises(ValueError):
            cc.registrar_estorno(ciclo_id, *argumentos, RECUPERADO_EM + timedelta(days=1))
        assert cc.estornos_do_ciclo(ciclo_id) == []
        assert cc.ciclo_por_id(ciclo_id)["fee_estornada"] is None

    def test_o_banco_recusa_o_mesmo_aviso_duas_vezes(self):
        ciclo_id = self._recuperado()
        cc.registrar_estorno(ciclo_id, "D1", 10.0, 30, RECUPERADO_EM + timedelta(days=1))
        conn = cc._conectar()
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO estornos_ciclo (ciclo_id, id_devolucao, valor_devolvido,
                           valor_considerado, fee_devolvida, no_prazo, recebido_em)
                       VALUES (?, 'D1', 10, 10, 2, 1, '2026-09-06T10:00:00')""", (ciclo_id,))
        finally:
            conn.close()

    def test_ciclo_inexistente_ou_aberto_nao_grava(self):
        aberto = cc.abrir_ciclo(A, "RN_ab", 100.0, "insufficient_funds", ABERTURA, id_cobranca="c")
        for ciclo_id in (aberto["id"], 99999):
            r = cc.registrar_estorno(ciclo_id, "D1", 10.0, 30, ABERTURA + timedelta(days=1))
            assert r["resultado"] == cc.ESTORNO_CICLO_NAO_RECUPERADO
        with sqlite3.connect(cc.caminho_do_banco()) as conn:
            assert conn.execute("SELECT COUNT(*) FROM estornos_ciclo").fetchone()[0] == 0

    def test_limpar_tudo_limpa_os_estornos(self):
        ciclo_id = self._recuperado()
        cc.registrar_estorno(ciclo_id, "D1", 10.0, 30, RECUPERADO_EM + timedelta(days=1))
        cc.limpar_tudo()
        with sqlite3.connect(cc.caminho_do_banco()) as conn:
            assert conn.execute("SELECT COUNT(*) FROM estornos_ciclo").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM ciclos_cobranca").fetchone()[0] == 0


# ══════════════════════════════════════════════════════════════════════════
# A migração: três colunas e uma tabela, sem recriar nada
# ══════════════════════════════════════════════════════════════════════════

NOVAS = ("estornado_em", "valor_estornado", "fee_estornada")


def _linhas(banco, tabela) -> list:
    with sqlite3.connect(banco) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(l) for l in conn.execute(f"SELECT * FROM {tabela} ORDER BY id")]


def _colunas(banco, tabela) -> list:
    with sqlite3.connect(banco) as conn:
        return [l[1] for l in conn.execute(f"PRAGMA table_info({tabela})")]


def _abrir_com_o_codigo_de_hoje(banco, monkeypatch):
    monkeypatch.setenv("CRAI_RECOVERY_DB", str(banco))
    cc.esquecer_schema_garantido()
    cc.ciclo_por_id(1)                                   # a primeira conexão garante o schema


def _conferir_preservado(banco, antes: dict):
    """Toda linha de antes está igual, coluna a coluna; as colunas novas são NULL."""
    for tabela, linhas in antes.items():
        depois = _linhas(banco, tabela)
        assert len(depois) == len(linhas), tabela
        for a, d in zip(linhas, depois):
            assert {k: d[k] for k in a} == a, (tabela, a.get("id"))
    for linha in _linhas(banco, "ciclos_cobranca"):
        assert all(linha[c] is None for c in NOVAS)
    with sqlite3.connect(banco) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        tabelas = {l[0] for l in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "estornos_ciclo" in tabelas


class TestMigracao:

    def test_banco_do_bloco_4_ganha_as_colunas_sem_perder_linha(self, tmp_path, monkeypatch):
        """Um banco com o schema de ANTES do Bloco 5 (o de hoje, sem as 3 colunas
        e sem a tabela de estornos), com ciclos em todos os estados."""
        banco = tmp_path / "bloco4.db"
        ddl = "\n".join(l for l in cc._DDL_CICLOS.replace("{nome}", "ciclos_cobranca").split("\n")
                        if not any(c in l for c in NOVAS))
        with sqlite3.connect(banco) as conn:
            conn.executescript(ddl)
            for i, estado in enumerate(cc.ESTADOS, start=1):
                conn.execute(
                    """INSERT INTO ciclos_cobranca (tenant_id, id_recorrencia, id_cobranca_original,
                           valor, causa_original, janela_inicio, janela_fim, estado, fee,
                           aberto_em, atualizado_em, recuperado_em)
                       VALUES (?, ?, ?, ?, 'insufficient_funds', '2026-09-01T09:00:00',
                               '2026-09-08T09:00:00', ?, ?, '2026-09-01T09:00:00',
                               '2026-09-02T09:00:00', ?)""",
                    ("t", f"RN_{i}", f"inv_{i}", 100.0 + i, estado,
                     20.0 if estado == cc.RECUPERADO else 0.0,
                     "2026-09-02T09:00:00" if estado == cc.RECUPERADO else None))
        assert not set(NOVAS) & set(_colunas(banco, "ciclos_cobranca"))
        antes = {"ciclos_cobranca": _linhas(banco, "ciclos_cobranca")}

        _abrir_com_o_codigo_de_hoje(banco, monkeypatch)

        assert _colunas(banco, "ciclos_cobranca")[-3:] == list(NOVAS)
        _conferir_preservado(banco, antes)
        # O banco migrado e um banco novo têm as mesmas colunas, na mesma ordem.
        novo = tmp_path / "novo.db"
        _abrir_com_o_codigo_de_hoje(novo, monkeypatch)
        assert _colunas(novo, "ciclos_cobranca") == _colunas(banco, "ciclos_cobranca")
        # E o estorno funciona no banco migrado.
        monkeypatch.setenv("CRAI_RECOVERY_DB", str(banco))
        cc.esquecer_schema_garantido()
        recuperado = next(l for l in _linhas(banco, "ciclos_cobranca") if l["estado"] == cc.RECUPERADO)
        r = cc.registrar_estorno(recuperado["id"], "D1", recuperado["valor"], 30,
                                 datetime(2026, 9, 10, 9, 0))
        assert r["resultado"] == cc.ESTORNO_REGISTRADO and r["fee_devolvida"] == 20.0

    def test_a_segunda_abertura_nao_muda_nada(self, tmp_path, monkeypatch):
        banco = tmp_path / "duas_vezes.db"
        _abrir_com_o_codigo_de_hoje(banco, monkeypatch)
        ciclo = cc.abrir_ciclo("t", "RN_x", 10.0, "c", id_cobranca="inv_x")
        antes = (_colunas(banco, "ciclos_cobranca"), _linhas(banco, "ciclos_cobranca"))
        _abrir_com_o_codigo_de_hoje(banco, monkeypatch)
        assert (_colunas(banco, "ciclos_cobranca"), _linhas(banco, "ciclos_cobranca")) == antes
        assert cc.ciclo_por_id(ciclo["id"])["valor_estornado"] is None

    def test_copia_do_banco_real(self, tmp_path, monkeypatch):
        if not REAL.exists():
            pytest.skip("app/data/recovery_cycles.db não existe neste clone (app/data/ é ignorado "
                        "pelo git): a migração sobre o banco real não pôde ser medida aqui")
        original = (REAL.stat().st_size, REAL.stat().st_mtime_ns)
        copia = tmp_path / "copia_real.db"
        shutil.copy2(REAL, copia)
        antes = {"ciclos_cobranca": _linhas(copia, "ciclos_cobranca"),
                 "tentativas_cobranca": _linhas(copia, "tentativas_cobranca")}
        assert len(antes["ciclos_cobranca"]) >= 1, "o banco real não tinha ciclo: nada foi medido"
        monkeypatch.setenv("CRAI_RETRY_STATE", str(tmp_path / "sem_json.json"))

        _abrir_com_o_codigo_de_hoje(copia, monkeypatch)

        assert set(NOVAS) <= set(_colunas(copia, "ciclos_cobranca"))
        _conferir_preservado(copia, antes)
        # O original só foi lido.
        assert (REAL.stat().st_size, REAL.stat().st_mtime_ns) == original

"""tests/test_desconto_por_anomalia.py — os dois números do ciclo (Rodada 2, ajustes).

O QUE ERA. O classificador dá uma pontuação de recuperação (por exemplo 28/100);
se o detector de anomalia marca a cobrança, o nó seguinte a multiplica por 0,7
(19/100) e é com esse número que o sistema decide. A trilha do Art. 20 dizia os
dois, em frases diferentes, sem dizer que um vem do outro — e a tela mostrava os
dois no mesmo painel.

O QUE ESTE ARQUIVO MEDE:
  - a explicação da decisão com desconto diz que a pontuação foi reduzida em 30%
    por comportamento fora do padrão, de quanto para quanto, e o retorno esperado
    também;
  - a pontuação de antes é gravada como chave a mais na `saida` da decisão — e o
    schema, o encadeamento e o índice da trilha não mudaram;
  - sem anomalia, a decisão e a explicação são as de antes, sem chave nova;
  - o detalhe do ciclo (`GET /ciclos/{id}`) diz que houve desconto e de quanto,
    para a tela mostrar um número só; ciclo antigo, sem a chave nova, também.
"""

import sqlite3

import pytest

from crai.agent import workflow as wf
from crai.api import ciclos as ciclos_api
from crai.churn_voluntary import retention_log as trilha
from tests.test_mensagens_involuntario import (  # noqa: F401 — fixtures usadas por nome
    A, _decisoes, _evento, _get, _post, cliente, envios, relogio, sem_llm,
)

SENTENCA = ("A pontuação de recuperação foi reduzida em 30% por comportamento fora do padrão: "
            "de 28/100 para 19/100. O retorno esperado também foi reduzido: de R$ 225,84 para "
            "R$ 158,08.")


def _detector(monkeypatch, anomala: bool):
    """O detector de anomalia respondendo o que o teste manda."""
    async def check(customer_id, payment_event):
        return {"is_anomaly": anomala, "error": 0.9 if anomala else 0.01, "threshold": 0.14,
                "method": "autoencoder", "top_features": []}
    monkeypatch.setattr(wf._detector, "check", check)


def _estado(score=28, p=0.2827, eprofit=225.84):
    return {"tenant_id": A, "customer_id": "RN_desconto", "failure_cause": "authorization_revoked",
            "recovery_score": score, "p_recovery": p, "eprofit": eprofit, "ltv_estimated": 799.0,
            "amount": 799.0, "payment_method": "pix_automatico", "retry_count": 0,
            "payment_event": {}}


# ══════════════════════════════════════════════════════════════════════════
# O texto
# ══════════════════════════════════════════════════════════════════════════

class TestAFrase:

    ENTRADAS = {"failure_cause": "authorization_revoked", "recovery_score": 19.0, "eprofit": 158.08,
                "is_anomalous": True, "payment_method": "pix_automatico", "tentativas_usadas": 0,
                "limite_tentativas": 3}
    SAIDA = {"estrategia": "mensagem_pagamento", "regra": "decide_recovery",
             "motivo_da_regra": "sem mandato não existe cobrança a reenviar"}
    DESCONTO = {"desconto_por_anomalia_pct": 30, "recovery_score_antes_do_desconto": 28.0,
                "eprofit_antes_do_desconto": 225.84}

    def _frase(self, entradas, saida):
        return trilha.frase_da_decisao("2026-10-04T10:00:00+00:00", trilha.DOMINIO_INVOLUNTARIO,
                                       trilha.TIPO_RETENTATIVA, trilha.MODELO_REGRA, None,
                                       entradas, saida, None)

    def test_a_explicacao_diz_o_desconto_de_quanto_para_quanto_e_o_retorno_tambem(self):
        frase = self._frase(self.ENTRADAS, {**self.SAIDA, **self.DESCONTO})
        assert SENTENCA in frase
        # A sentença vem depois do que foi considerado e antes de quem decidiu.
        assert frase.index("Foram considerados") < frase.index(SENTENCA) < frase.index(
            "A decisão veio da regra 'decide_recovery'")
        # As chaves novas não viram item solto, nem aparecem com o nome cru.
        for cru in ("desconto_por_anomalia_pct", "recovery_score_antes", "eprofit_antes", " = "):
            assert cru not in frase

    def test_sem_as_chaves_a_frase_e_exatamente_a_de_antes(self):
        com = self._frase(self.ENTRADAS, {**self.SAIDA, **self.DESCONTO})
        sem = self._frase(self.ENTRADAS, self.SAIDA)
        assert "reduzida" not in sem and "fora do padrão" not in sem
        assert com.replace(" " + SENTENCA, "") == sem

    def test_sem_o_retorno_de_antes_a_frase_fala_so_da_pontuacao(self):
        saida = {**self.SAIDA, "desconto_por_anomalia_pct": 30, "recovery_score_antes_do_desconto": 28}
        frase = self._frase(self.ENTRADAS, saida)
        assert ("A pontuação de recuperação foi reduzida em 30% por comportamento fora do padrão: "
                "de 28/100 para 19/100.") in frase
        assert "O retorno esperado também" not in frase

    def test_a_frase_nao_tem_termo_tecnico_nem_dado_de_pessoa(self):
        frase = self._frase(self.ENTRADAS, {**self.SAIDA, **self.DESCONTO})
        for termo in ("autoencoder", "anomaly", "score", "SHAP", "0.7", "0,7", "@"):
            assert termo not in frase


# ══════════════════════════════════════════════════════════════════════════
# Os nós do agente
# ══════════════════════════════════════════════════════════════════════════

class TestOsNos:

    @pytest.mark.asyncio
    async def test_com_anomalia_o_no_guarda_o_que_o_diagnostico_tinha_dito(self, monkeypatch):
        _detector(monkeypatch, True)
        s = await wf.check_anomaly(_estado())
        assert s["is_anomalous"] is True
        assert (s["recovery_score"], s["p_recovery"]) == (19, 0.1979)        # 28 × 0,7 e 0,2827 × 0,7
        assert (s["recovery_score_sem_desconto"], s["eprofit_sem_desconto"]) == (28, 225.84)
        assert wf.FATOR_COM_ANOMALIA == 0.7 and wf.DESCONTO_POR_ANOMALIA_PCT == 30

    @pytest.mark.asyncio
    async def test_sem_anomalia_nada_muda_e_nada_e_guardado(self, monkeypatch):
        _detector(monkeypatch, False)
        s = await wf.check_anomaly(_estado())
        assert s["is_anomalous"] is False
        assert (s["recovery_score"], s["p_recovery"]) == (28, 0.2827)
        assert s["recovery_score_sem_desconto"] is None and s["eprofit_sem_desconto"] is None

    @pytest.mark.asyncio
    async def test_a_decisao_com_desconto_grava_as_chaves_e_a_explicacao(self, monkeypatch):
        _detector(monkeypatch, True)
        s = await wf.check_anomaly(_estado())
        s = await wf.decide_recovery({**s, "eprofit": 158.08})
        d = s["decisoes"][-1]
        assert d["tipo_decisao"] == trilha.TIPO_RETENTATIVA
        assert d["saida"]["desconto_por_anomalia_pct"] == 30
        assert d["saida"]["recovery_score_antes_do_desconto"] == 28
        assert d["saida"]["eprofit_antes_do_desconto"] == 225.84
        assert d["entradas"]["recovery_score"] == 19 and d["entradas"]["is_anomalous"] is True
        assert SENTENCA in d["explicacao"]
        # O que já estava na saída continua lá.
        assert d["saida"]["regra"] == "decide_recovery" and d["saida"]["estrategia"] == "mensagem_pagamento"

    @pytest.mark.asyncio
    async def test_a_decisao_sem_anomalia_tem_a_saida_de_antes(self, monkeypatch):
        _detector(monkeypatch, False)
        s = await wf.check_anomaly(_estado())
        s = await wf.decide_recovery(s)
        d = s["decisoes"][-1]
        assert set(d["saida"]) == {"estrategia", "regra", "motivo_da_regra"}
        assert "reduzida" not in d["explicacao"] and "fora do padrão" not in d["explicacao"]

    @pytest.mark.asyncio
    async def test_estado_anomalo_sem_o_valor_de_antes_nao_inventa_desconto(self):
        """Ciclo retomado, ou state montado por fora: sem o "de quanto", a
        decisão não afirma desconto nenhum."""
        s = await wf.decide_recovery({**_estado(score=19, p=0.1979, eprofit=158.08),
                                      "is_anomalous": True})
        d = s["decisoes"][-1]
        assert set(d["saida"]) == {"estrategia", "regra", "motivo_da_regra"}
        assert "reduzida" not in d["explicacao"]

    def test_o_grafo_carrega_os_dois_campos_de_um_no_para_o_outro(self):
        """O LangGraph descarta o que não está declarado no state."""
        from crai.agent.state import AgentState
        assert {"recovery_score_sem_desconto", "eprofit_sem_desconto"} <= set(AgentState.__annotations__)


# ══════════════════════════════════════════════════════════════════════════
# Ponta a ponta: o webhook, a trilha e o detalhe do ciclo
# ══════════════════════════════════════════════════════════════════════════

class TestPontaAPonta:

    def _abrir(self, c, monkeypatch, anomala, rec):
        _detector(monkeypatch, anomala)
        _post(c, _evento(rec, f"E_{rec}_0", codigo="MD01"))
        ciclo = ciclos_api.cc.ciclos_do_mandato(A, rec)[0]
        return ciclo, _decisoes(A, rec)

    def test_com_anomalia_a_trilha_e_o_detalhe_do_ciclo_contam_a_mesma_historia(self, cliente,
                                                                             monkeypatch):
        ciclo, decisoes = self._abrir(cliente, monkeypatch, True, "RN_com_anomalia")
        risco = next(d for d in decisoes if d["tipo_decisao"] == "risco")
        retentativa = next(d for d in decisoes if d["tipo_decisao"] == "retentativa")
        antes, depois = risco["saida"]["recovery_score"], retentativa["entradas"]["recovery_score"]
        assert depois == int(antes * 0.7) and depois < antes

        # A trilha: a chave a mais e a sentença, com os números deste ciclo.
        assert retentativa["saida"]["desconto_por_anomalia_pct"] == 30
        assert retentativa["saida"]["recovery_score_antes_do_desconto"] == antes
        assert (f"A pontuação de recuperação foi reduzida em 30% por comportamento fora do "
                f"padrão: de {int(antes)}/100 para {int(depois)}/100.") in retentativa["explicacao"]
        assert "O retorno esperado também foi reduzido: de R$ " in retentativa["explicacao"]
        # O ciclo guarda o número que o sistema usou.
        assert ciclo["recovery_score"] == depois

        # O detalhe do ciclo diz o desconto, para a tela mostrar um número só.
        r = _get(cliente, f"/ciclos/{ciclo['id']}")
        assert r.status_code == 200, r.text
        assert r.json()["diagnostico"]["desconto_por_anomalia"] == {
            "percentual": 30, "pontuacao_antes": antes, "pontuacao_usada": depois}
        diagnostico = next(e for e in r.json()["linha_do_tempo"] if e["tipo"] == "diagnostico")
        assert diagnostico["dados"]["recovery_score"] == depois

        # A explicação do titular traz a sentença.
        r = _get(cliente, "/titular/explicacao/RN_com_anomalia")
        assert r.status_code == 200, r.text
        assert "reduzida em 30% por comportamento fora do padrão" in r.text

    def test_sem_anomalia_o_detalhe_nao_tem_desconto(self, cliente, monkeypatch):
        ciclo, decisoes = self._abrir(cliente, monkeypatch, False, "RN_sem_anomalia")
        retentativa = next(d for d in decisoes if d["tipo_decisao"] == "retentativa")
        assert set(retentativa["saida"]) == {"estrategia", "regra", "motivo_da_regra"}
        r = _get(cliente, f"/ciclos/{ciclo['id']}")
        assert r.json()["diagnostico"]["desconto_por_anomalia"] is None

    def test_a_trilha_continua_integra_e_o_schema_e_o_mesmo(self, cliente, monkeypatch):
        self._abrir(cliente, monkeypatch, True, "RN_cadeia_1")
        self._abrir(cliente, monkeypatch, False, "RN_cadeia_2")
        self._abrir(cliente, monkeypatch, True, "RN_cadeia_3")
        cadeia = trilha.verificar_cadeia(A)
        assert cadeia["integra"] is True, cadeia
        assert cadeia["linhas"] >= 6
        conn = sqlite3.connect(trilha.caminho_do_banco())
        try:
            colunas = [c[1] for c in conn.execute("PRAGMA table_info(decisoes_automatizadas)")]
            indices = {l[1]: l[2] for l in conn.execute("PRAGMA index_list(decisoes_automatizadas)")}
        finally:
            conn.close()
        assert colunas == ["id", "tenant_id", "sujeito_id", "decidido_em", "dominio", "tipo_decisao",
                           "modelo", "modelo_versao", "entradas", "saida", "explicacao",
                           "contribuicoes", "hash_anterior", "hash_linha"]
        assert indices.get("uq_decisao_elo") == 1               # o índice único do encadeamento


class TestCicloAntigo:
    """Ciclos gravados antes desta rodada não têm a chave nova na trilha. O
    detalhe reconhece o desconto pela entrada `is_anomalous`."""

    RISCO = {"tipo_decisao": "risco", "saida": {"recovery_score": 24, "p_recovery": 0.2396}}

    def _retentativa(self, anomala, saida=None):
        return {"tipo_decisao": "retentativa",
                "entradas": {"recovery_score": 16.0 if anomala else 24.0, "is_anomalous": anomala},
                "saida": saida or {"estrategia": "mensagem_pagamento", "regra": "decide_recovery"}}

    def test_antigo_com_anomalia(self):
        d = ciclos_api._desconto_por_anomalia(self.RISCO, [self.RISCO, self._retentativa(True)])
        assert d == {"percentual": 30, "pontuacao_antes": 24, "pontuacao_usada": 16.0}

    def test_antigo_sem_anomalia(self):
        assert ciclos_api._desconto_por_anomalia(self.RISCO, [self.RISCO, self._retentativa(False)]) is None

    def test_sem_decisao_de_retentativa(self):
        assert ciclos_api._desconto_por_anomalia(self.RISCO, [self.RISCO]) is None

    def test_novo_usa_o_que_a_trilha_gravou(self):
        saida = {"estrategia": "mensagem_pagamento", "regra": "decide_recovery",
                 "desconto_por_anomalia_pct": 30, "recovery_score_antes_do_desconto": 28}
        d = ciclos_api._desconto_por_anomalia(self.RISCO, [self.RISCO, self._retentativa(True, saida)])
        assert d == {"percentual": 30, "pontuacao_antes": 28, "pontuacao_usada": 16.0}

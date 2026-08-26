"""
tests/test_pix_automatico_retry.py — Testes da janela regulada do BACEN (Fase 3).

Cobre os limites que o Pix Automático impõe ao Recebedor:
- nunca mais de 3 tentativas
- nunca uma tentativa além dos 7 dias corridos do vencimento
- com previsão confiável do Payday Engine, as tentativas ficam perto da
  janela de liquidez prevista (o Módulo 3 é mockado aqui)
- sem previsão confiável, cai no fallback uniforme
- tentar cobrar valor diferente do original levanta exceção

O Payday Engine é mockado em todos os casos: aqui interessa a POLÍTICA, não a
qualidade da previsão — que já é testada em tests/test_payday_inference.py.
"""

from datetime import datetime, timedelta

import pytest

from crai.dunning.pix_automatico_retry import (
    JANELA_DIAS,
    MAX_TENTATIVAS,
    ORIGEM_FALLBACK,
    ORIGEM_PAYDAY,
    PixAutomaticoRetryPolicy,
    PixRetryPolicyViolation,
)

VALOR = 299.90
# Momento fixo, para que os testes não dependam do dia em que rodam.
AGORA = datetime(2026, 8, 26, 9, 0)
VENCIMENTO = datetime(2026, 8, 25, 9, 0)   # venceu ontem: as 2 janelas automáticas já falharam


class PaydayMock:
    """Stand-in do Módulo 3 com previsão controlada pelo teste."""

    def __init__(self, quando=None, confidence=0.0, erro=False):
        self.quando = quando
        self.confidence = confidence
        self.erro = erro
        self.chamadas = []

    async def predict_next_window(self, customer_id):
        self.chamadas.append(customer_id)
        if self.erro:
            raise RuntimeError("modelo indisponível")
        return {"timestamp": self.quando, "confidence": self.confidence, "profile": "CLT"}


def policy(payday=None, confianca_minima=0.60) -> PixAutomaticoRetryPolicy:
    return PixAutomaticoRetryPolicy(payday_inference=payday, confianca_minima=confianca_minima)


# ══════════════════════════════════════════════════════════════════════════
# LIMITE DE TENTATIVAS
# ══════════════════════════════════════════════════════════════════════════

class TestLimiteDeTentativas:
    """O BACEN concede no máximo 3 novas tentativas ao recebedor."""

    @pytest.mark.asyncio
    async def test_nunca_agenda_mais_de_tres(self):
        p = policy(PaydayMock(quando=AGORA + timedelta(days=1), confidence=0.95))
        tentativas = await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)
        assert len(tentativas) <= MAX_TENTATIVAS

    @pytest.mark.asyncio
    @pytest.mark.parametrize("usadas, esperado", [(0, 3), (1, 2), (2, 1), (3, 0)])
    async def test_respeita_tentativas_ja_usadas(self, usadas, esperado):
        """O total (usadas + agendadas) nunca passa de 3."""
        p = policy(PaydayMock(quando=AGORA + timedelta(days=1), confidence=0.95))
        tentativas = await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO,
                                      tentativas_usadas=usadas, agora=AGORA)
        assert len(tentativas) == esperado
        assert usadas + len(tentativas) <= MAX_TENTATIVAS

    @pytest.mark.asyncio
    async def test_janela_esgotada_devolve_lista_vazia(self):
        """3 de 3 usadas: nada a agendar, sem exceção."""
        p = policy(PaydayMock(quando=AGORA + timedelta(days=1), confidence=0.95))
        assert await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO,
                                tentativas_usadas=MAX_TENTATIVAS, agora=AGORA) == []

    @pytest.mark.asyncio
    async def test_numeracao_continua_de_onde_parou(self):
        p = policy(PaydayMock(quando=AGORA + timedelta(days=1), confidence=0.95))
        tentativas = await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO,
                                      tentativas_usadas=1, agora=AGORA)
        assert [t.numero for t in tentativas] == [2, 3]

    @pytest.mark.asyncio
    async def test_tentativas_usadas_negativo_levanta(self):
        p = policy(PaydayMock(confidence=0.0))
        with pytest.raises(PixRetryPolicyViolation, match="negativo"):
            await p.schedule("RN_1", VALOR, tentativas_usadas=-1, agora=AGORA)


# ══════════════════════════════════════════════════════════════════════════
# PRAZO DE 7 DIAS CORRIDOS
# ══════════════════════════════════════════════════════════════════════════

class TestPrazoDeSeteDias:
    """Nenhuma tentativa pode cair depois de vencimento + 7 dias."""

    @pytest.mark.asyncio
    async def test_nenhuma_tentativa_alem_do_prazo(self):
        prazo = VENCIMENTO + timedelta(days=JANELA_DIAS)
        p = policy(PaydayMock(quando=AGORA + timedelta(days=1), confidence=0.95))
        tentativas = await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)

        assert tentativas
        for t in tentativas:
            assert t.quando <= prazo

    @pytest.mark.asyncio
    async def test_previsao_perto_do_fim_agenda_menos_tentativas(self):
        """Se a liquidez só chega no 6º dia, não cabem 3 tentativas — agenda as que couberem."""
        prazo = VENCIMENTO + timedelta(days=JANELA_DIAS)
        previsto = prazo - timedelta(days=1)
        p = policy(PaydayMock(quando=previsto, confidence=0.95))

        tentativas = await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)

        assert 0 < len(tentativas) < MAX_TENTATIVAS
        assert all(t.quando <= prazo for t in tentativas)

    @pytest.mark.asyncio
    async def test_janela_expirada_nao_agenda_nada(self):
        """Evento chegando depois dos 7 dias: nada a fazer."""
        p = policy(PaydayMock(confidence=0.95))
        muito_depois = VENCIMENTO + timedelta(days=JANELA_DIAS + 2)
        assert await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=muito_depois) == []

    @pytest.mark.asyncio
    async def test_nao_agenda_no_dia_do_vencimento(self):
        """As duas janelas automáticas do dia são do PSP do pagador, não da CRAI."""
        p = policy(PaydayMock(confidence=0.0))
        vencimento = AGORA
        tentativas = await p.schedule("RN_1", VALOR, vencimento=vencimento, agora=AGORA)
        assert all(t.quando >= vencimento + timedelta(days=1) for t in tentativas)


# ══════════════════════════════════════════════════════════════════════════
# PAYDAY ENGINE vs FALLBACK
# ══════════════════════════════════════════════════════════════════════════

class TestUsoDoPaydayEngine:
    """Previsão confiável ancora as tentativas; o resto cai no fallback."""

    @pytest.mark.asyncio
    async def test_previsao_confiavel_concentra_tentativas_na_liquidez(self):
        previsto = VENCIMENTO + timedelta(days=3)
        p = policy(PaydayMock(quando=previsto, confidence=0.92))

        tentativas = await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)

        assert all(t.origem == ORIGEM_PAYDAY for t in tentativas)
        assert tentativas[0].quando == previsto
        # Concentradas: da previsão em diante, uma por dia.
        assert all(t.quando >= previsto for t in tentativas)
        assert (tentativas[-1].quando - tentativas[0].quando) <= timedelta(days=MAX_TENTATIVAS)

    @pytest.mark.asyncio
    async def test_consulta_o_payday_com_o_cliente_certo(self):
        mock = PaydayMock(quando=VENCIMENTO + timedelta(days=2), confidence=0.9)
        await policy(mock).schedule("RN_cliente_x", VALOR, vencimento=VENCIMENTO, agora=AGORA)
        assert mock.chamadas == ["RN_cliente_x"]

    @pytest.mark.asyncio
    async def test_previsao_no_mesmo_dia_conta_como_dentro_da_janela(self):
        """Regressão: a janela é comparada por DIA, não por horário cheio.

        O Payday Engine prevê o dia da entrada de dinheiro e devolve uma hora
        convencional (10h). Se a janela do recebedor abre às 18h30 e a previsão
        é para as 10h do mesmo dia, ainda é o mesmo dia de liquidez — comparar
        timestamps cheios descartaria a previsão e cairia no fallback à toa.
        """
        agora = datetime(2026, 8, 26, 18, 30)
        vencimento = datetime(2026, 8, 25, 18, 30)
        previsto = datetime(2026, 8, 27, 10, 0)   # dia seguinte, hora anterior

        p = policy(PaydayMock(quando=previsto, confidence=0.90))
        tentativas = await p.schedule("RN_1", VALOR, vencimento=vencimento, agora=agora)

        assert all(t.origem == ORIGEM_PAYDAY for t in tentativas)
        assert tentativas[0].quando.date() == previsto.date()
        # E nunca antes da abertura da janela do recebedor (vencimento + 1 dia),
        # que é quando as duas janelas automáticas do PSP já se esgotaram.
        assert tentativas[0].quando >= vencimento + timedelta(days=1)

    @pytest.mark.asyncio
    async def test_confianca_baixa_cai_no_fallback(self):
        """Cliente novo / previsão fraca: distribuição uniforme."""
        p = policy(PaydayMock(quando=VENCIMENTO + timedelta(days=3), confidence=0.20))
        tentativas = await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)
        assert all(t.origem == ORIGEM_FALLBACK for t in tentativas)

    @pytest.mark.asyncio
    async def test_previsao_fora_da_janela_cai_no_fallback(self):
        """Liquidez prevista só depois do prazo: não adianta ancorar nela."""
        fora = VENCIMENTO + timedelta(days=JANELA_DIAS + 5)
        p = policy(PaydayMock(quando=fora, confidence=0.99))
        tentativas = await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)
        assert all(t.origem == ORIGEM_FALLBACK for t in tentativas)
        assert all(t.quando <= VENCIMENTO + timedelta(days=JANELA_DIAS) for t in tentativas)

    @pytest.mark.asyncio
    async def test_sem_payday_configurado_usa_fallback(self):
        tentativas = await policy(None).schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)
        assert all(t.origem == ORIGEM_FALLBACK for t in tentativas)

    @pytest.mark.asyncio
    async def test_payday_quebrado_nao_derruba_o_agendamento(self):
        """Modelo indisponível degrada para o fallback, não para exceção."""
        tentativas = await policy(PaydayMock(erro=True)).schedule(
            "RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)
        assert all(t.origem == ORIGEM_FALLBACK for t in tentativas)
        assert tentativas


class TestFallbackUniforme:
    """Sem previsão, espalha pelas datas restantes com ao menos 1 dia de intervalo."""

    @pytest.mark.asyncio
    async def test_intervalo_minimo_de_um_dia(self):
        tentativas = await policy(None).schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)
        for anterior, seguinte in zip(tentativas, tentativas[1:]):
            assert (seguinte.quando - anterior.quando) >= timedelta(days=1)

    @pytest.mark.asyncio
    async def test_distribui_ate_o_fim_da_janela(self):
        """A última tentativa aproveita o prazo, sem ultrapassá-lo."""
        prazo = VENCIMENTO + timedelta(days=JANELA_DIAS)
        tentativas = await policy(None).schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)
        assert tentativas[-1].quando <= prazo
        assert tentativas[-1].quando > tentativas[0].quando

    @pytest.mark.asyncio
    async def test_poucos_dias_restantes_agenda_menos(self):
        """Sobrando 1 dia, não force 3 tentativas no mesmo dia."""
        quase_no_fim = VENCIMENTO + timedelta(days=JANELA_DIAS - 1)
        tentativas = await policy(None).schedule(
            "RN_1", VALOR, vencimento=VENCIMENTO, agora=quase_no_fim)
        assert 0 < len(tentativas) <= 2


# ══════════════════════════════════════════════════════════════════════════
# VALOR ORIGINAL — COBRANÇA PARCIAL NÃO EXISTE
# ══════════════════════════════════════════════════════════════════════════

class TestValorOriginal:
    """Toda tentativa é pelo valor original."""

    @pytest.mark.asyncio
    async def test_valor_diferente_levanta_excecao(self):
        p = policy(PaydayMock(confidence=0.9, quando=VENCIMENTO + timedelta(days=2)))
        with pytest.raises(PixRetryPolicyViolation, match="parcial"):
            await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO, valor=150.00, agora=AGORA)

    @pytest.mark.asyncio
    async def test_valor_igual_ao_original_e_aceito(self):
        p = policy(PaydayMock(confidence=0.9, quando=VENCIMENTO + timedelta(days=2)))
        tentativas = await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO,
                                      valor=VALOR, agora=AGORA)
        assert all(t.valor == VALOR for t in tentativas)

    @pytest.mark.asyncio
    async def test_todas_as_tentativas_usam_o_valor_original(self):
        tentativas = await policy(None).schedule("RN_1", VALOR, vencimento=VENCIMENTO, agora=AGORA)
        assert {t.valor for t in tentativas} == {VALOR}

    @pytest.mark.asyncio
    async def test_erro_explica_a_regra(self):
        """A exceção precisa ser clara — vai virar log de auditoria."""
        p = policy(None)
        with pytest.raises(PixRetryPolicyViolation) as exc:
            await p.schedule("RN_1", VALOR, vencimento=VENCIMENTO, valor=1.00, agora=AGORA)
        assert "299.90" in str(exc.value) and "1.00" in str(exc.value)

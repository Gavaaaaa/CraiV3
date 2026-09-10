"""tests/test_pix_codes.py — Sprint 3: o diagnóstico de Pix deixa de ser cego.

O DEFEITO. `_features_pix` atribuía a TODA falha de Pix o mesmo
`gateway_error_code = "insufficient_funds"`. Havia razão escrita para o
default — no fluxo do BACEN, as duas janelas automáticas do dia já tentaram
debitar a conta, então falta de saldo é a causa dominante —, mas dominante não
é única, e as consequências eram concretas:

    - o classificador recebia uma feature CONSTANTE, que não separa nada;
    - `decide_recovery` mandava retentar sempre, inclusive nos dois casos em
      que retentar é garantidamente inútil: limite do pagador estourado e
      autorização revogada. Gastar as 3 tentativas do BACEN nesses casos é
      queimar um direito regulatório em algo que não pode dar certo;
    - o dunning escolhia a mensagem por um vocabulário de CARTÃO (`expired_card`,
      `do_not_honor`) num pipeline que só fala Pix;
    - e o dataset de treino do Sprint 6 nasceria com `failure_cause` constante.

O QUE OS TESTES ABAIXO SEPARAM: a tradução (código do PSP → causa interna), o
efeito da tradução na decisão do agente, e a garantia de que qualquer entrada —
`None`, número, dict, texto livre de 10 KB — sai como uma das quatro causas
conhecidas. Essa última não é preciosismo: o valor vira feature categórica e
coluna de dataset, e foi exatamente por aceitar string arbitrária de terceiro
que o `bandit_state.json` do churn voluntário acumulou perfis de fuzz.
"""

import pytest

from crai.agent import pix_codes
from crai.agent.pix_codes import (
    CAUSA_LIMITE,
    CAUSA_PADRAO,
    CAUSA_REVOGADA,
    CAUSA_SALDO,
    CAUSA_TECNICA,
    CAUSAS_PIX,
    causa_do_codigo,
    e_retentavel,
)
from crai.agent.workflow import CAUSAS_RETENTAVEIS, _features_pix, decide_recovery
from crai.dunning.dunning_engine import FALLBACK_TEMPLATES, DunningEngine
from crai.integrations.payment_gateway import PixAutomaticoAdapter


class TestTraducaoDoCodigo:

    @pytest.mark.parametrize("bruto,esperado", [
        ("insufficient_funds", CAUSA_SALDO),
        ("saldo_insuficiente", CAUSA_SALDO),
        ("Saldo Insuficiente", CAUSA_SALDO),
        ("SALDO-INSUFICIENTE", CAUSA_SALDO),
        ("AM04", CAUSA_SALDO),
        ("limit_exceeded", CAUSA_LIMITE),
        ("limite_do_pagador_excedido", CAUSA_LIMITE),
        ("AM02", CAUSA_LIMITE),
        ("authorization_revoked", CAUSA_REVOGADA),
        ("autorizacao_revogada", CAUSA_REVOGADA),
        ("mandate_revoked", CAUSA_REVOGADA),
        ("MD01", CAUSA_REVOGADA),
        ("processing_error", CAUSA_TECNICA),
        ("psp_error", CAUSA_TECNICA),
        ("AB03", CAUSA_TECNICA),
    ])
    def test_cada_codigo_vira_a_causa_certa(self, bruto, esperado):
        assert causa_do_codigo(bruto) == esperado

    def test_formatacao_nao_muda_o_diagnostico(self):
        """PSPs variam maiúscula, espaço e separador para o mesmo motivo."""
        variacoes = ["limit_exceeded", "LIMIT_EXCEEDED", "Limit Exceeded",
                     "limit-exceeded", "  limit_exceeded  "]
        assert {causa_do_codigo(v) for v in variacoes} == {CAUSA_LIMITE}


class TestDefaultSeguro:
    """Um código que ninguém mapeou não pode virar uma AFIRMAÇÃO sobre o pagador."""

    @pytest.mark.parametrize("bruto", [
        None, "", "  ", "codigo_que_o_psp_inventou", "XX99", 42, 3.14,
        {"code": "AM04"}, ["AM04"], True,
    ])
    def test_qualquer_entrada_sai_no_vocabulario(self, bruto):
        assert causa_do_codigo(bruto) in CAUSAS_PIX

    def test_o_default_nao_e_saldo_insuficiente(self):
        """A regressão que importa: manter o comportamento antigo seria mentir.

        `insufficient_funds` afirma que o pagador não tem saldo. Num código
        desconhecido, ninguém sabe disso — e a afirmação iria para a trilha de
        auditoria, para a mensagem ao cliente e, no Sprint 6, para o dataset de
        treino como rótulo.
        """
        assert CAUSA_PADRAO == CAUSA_TECNICA
        assert causa_do_codigo("motivo_novo_do_psp") != CAUSA_SALDO

    def test_texto_gigante_nao_vira_feature(self):
        assert causa_do_codigo("x" * 10_000) == CAUSA_PADRAO


class TestRetentabilidade:

    def test_saldo_e_tecnico_sao_retentaveis(self):
        assert e_retentavel(CAUSA_SALDO) and e_retentavel(CAUSA_TECNICA)

    def test_limite_e_revogacao_nao_sao(self):
        """Retentar aqui gasta tentativa regulada em algo que não pode passar."""
        assert not e_retentavel(CAUSA_LIMITE)
        assert not e_retentavel(CAUSA_REVOGADA)

    def test_o_workflow_usa_o_mesmo_conjunto(self):
        """Dois conjuntos divergiriam no dia em que um fosse corrigido."""
        assert CAUSAS_RETENTAVEIS == {CAUSA_SALDO, CAUSA_TECNICA}


class TestOEventoCarregaOCodigoAteAsFeatures:
    """O adapter precisa preservar o motivo, senão o mapa não tem o que traduzir."""

    @pytest.mark.asyncio
    async def test_o_parser_extrai_o_motivo_da_recusa(self):
        adapter = PixAutomaticoAdapter()
        evento = await adapter.parse_pix_event({
            "event": "automatic_pix.charge_failed",
            "data": {"e2e_id": "E1", "valor": 100.0, "id_recorrencia": "RN1",
                     "automatic_pix": {"failure_reason": "limit_exceeded"}},
        })
        assert evento["codigo_falha"] == "limit_exceeded"

    @pytest.mark.asyncio
    async def test_motivo_ausente_nao_e_degradacao(self):
        """Autorização concedida não traz motivo de recusa — e está certo assim."""
        adapter = PixAutomaticoAdapter()
        evento = await adapter.parse_pix_event({
            "event": "automatic_pix.authorization_approved",
            "data": {"e2e_id": "E2", "id_recorrencia": "RN2"},
        })
        assert evento["codigo_falha"] == ""
        assert "codigo_falha" not in " ".join(evento["degradacoes"])

    @pytest.mark.asyncio
    async def test_motivo_nao_escalar_e_descartado(self):
        """`str()` sobre um dict devolveria "{'code': 'AM04'}" como se fosse código."""
        adapter = PixAutomaticoAdapter()
        evento = await adapter.parse_pix_event({
            "event": "automatic_pix.charge_failed",
            "data": {"e2e_id": "E3", "valor": 10.0, "id_recorrencia": "RN3",
                     "error": {"code": {"aninhado": "AM04"}}},
        })
        assert evento["codigo_falha"] == ""

    @pytest.mark.parametrize("codigo,esperado", [
        ("insufficient_funds", CAUSA_SALDO),
        ("AM02", CAUSA_LIMITE),
        ("mandate_revoked", CAUSA_REVOGADA),
        ("", CAUSA_TECNICA),
    ])
    def test_features_pix_usa_o_mapa(self, codigo, esperado):
        features = _features_pix(
            {"valor": 299.90, "id_recorrencia": "RN_f", "codigo_falha": codigo},
            299.90, customer_id="RN_f",
        )
        assert features["gateway_error_code"] == esperado

    def test_features_pix_nao_e_mais_constante(self):
        """O defeito, medido: antes, estes quatro eventos davam a mesma feature."""
        causas = {
            _features_pix({"valor": 100.0, "id_recorrencia": "RN_x",
                           "codigo_falha": c}, 100.0, customer_id="RN_x")
            ["gateway_error_code"]
            for c in ("AM04", "AM02", "MD01", "AB03")
        }
        assert causas == {CAUSA_SALDO, CAUSA_LIMITE, CAUSA_REVOGADA, CAUSA_TECNICA}


class TestADecisaoMuda:
    """O que a tradução compra: as duas causas que não devem consumir tentativa."""

    def _state(self, causa):
        return {
            "failure_cause": causa, "recovery_score": 80, "eprofit": 50.0,
            "is_anomalous": False, "payment_method": "pix_automatico",
            "retry_count": 0, "pix_janela_ate": None,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("causa", [CAUSA_SALDO, CAUSA_TECNICA])
    async def test_causa_retentavel_vai_para_retry(self, causa):
        resultado = await decide_recovery(self._state(causa))
        assert resultado["estrategia"] == "retry_automatico"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("causa", [CAUSA_LIMITE, CAUSA_REVOGADA])
    async def test_causa_nao_retentavel_vai_para_mensagem(self, causa):
        resultado = await decide_recovery(self._state(causa))
        assert resultado["estrategia"] == "mensagem_pagamento", (
            f"'{causa}' foi para retentativa — as 3 tentativas do BACEN seriam "
            "gastas num caso em que nenhuma pode passar")

    @pytest.mark.asyncio
    async def test_o_raciocinio_diz_qual_acao_o_cliente_precisa_tomar(self):
        """"O cliente precisa agir" não diz SE é aumentar limite ou reautorizar."""
        limite = await decide_recovery(self._state(CAUSA_LIMITE))
        revogada = await decide_recovery(self._state(CAUSA_REVOGADA))

        texto_limite = " ".join(limite["raciocinio"]).lower()
        texto_revogada = " ".join(revogada["raciocinio"]).lower()
        assert "limite" in texto_limite
        assert "reautorizar" in texto_revogada or "mandato" in texto_revogada


class TestMensagemDoDunning:

    @pytest.mark.parametrize("causa", [CAUSA_LIMITE, CAUSA_REVOGADA])
    def test_ha_template_proprio_para_cada_causa_de_pix(self, causa):
        assert causa in FALLBACK_TEMPLATES, (
            f"'{causa}' cairia no template de erro técnico, que manda o cliente "
            "tentar de novo pelo caminho que está bloqueado")

    @pytest.mark.parametrize("causa,palavra", [
        (CAUSA_LIMITE, "limite"),
        (CAUSA_REVOGADA, "autoriz"),
    ])
    def test_o_template_pede_a_acao_certa(self, causa, palavra):
        assert palavra in FALLBACK_TEMPLATES[causa].lower()

    def test_templates_de_cartao_continuam(self):
        """Retrocompatibilidade do caminho legado — nada foi removido."""
        for antigo in ("expired_card", "do_not_honor", "card_declined",
                       "generic_decline", "insufficient_funds", "processing_error"):
            assert antigo in FALLBACK_TEMPLATES

    @pytest.mark.asyncio
    async def test_autorizacao_revogada_nao_oferece_pix_automatico(self):
        """Oferecer o canal que o cliente acabou de fechar não recupera nada."""
        engine = DunningEngine()
        resultado = await engine._select_payment({
            "failure_cause": CAUSA_REVOGADA, "customer_id": "RN_revogada",
        })
        assert resultado["payment_method"] == "boleto"

    @pytest.mark.asyncio
    async def test_limite_excedido_ainda_oferece_pix(self):
        """O canal funciona; o que não cabe é o VALOR naquele teto."""
        engine = DunningEngine()
        resultado = await engine._select_payment({
            "failure_cause": CAUSA_LIMITE, "customer_id": "RN_limite",
        })
        assert resultado["payment_method"] == "pix_automatico"


class TestOMapaEstaDeclarado:

    def test_toda_entrada_do_mapa_aponta_para_o_vocabulario(self):
        for codigo, causa in pix_codes.PIX_CODE_MAP.items():
            assert causa in CAUSAS_PIX, f"{codigo} aponta para {causa!r}, fora do vocabulário"

    def test_as_quatro_causas_estao_cobertas(self):
        assert set(pix_codes.PIX_CODE_MAP.values()) == CAUSAS_PIX

    def test_toda_causa_tem_explicacao_legivel(self):
        """O texto entra na trilha de auditoria; faltar uma é a trilha emudecer."""
        assert set(pix_codes.EXPLICACAO_DA_CAUSA) == CAUSAS_PIX

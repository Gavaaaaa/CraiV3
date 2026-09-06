"""tests/test_config_pricing.py — Sprint 5: custo e fee saem do hardcode.

DOIS NÚMEROS QUE DEFINEM O NEGÓCIO estavam escritos à mão dentro de nós do
grafo:

    update_roi_dashboard   `amount * 0.15` — o success fee, que é a RECEITA da
                           CRAI e varia por contrato;
    check_anomaly          `cost = 0.05` — o custo do bot de WhatsApp, que
                           entra no recálculo do e-Profit e portanto decide se
                           a CRAI age. O mesmo número já existia, duplicado, em
                           `ml/failure_classifier.py`.

Enquanto são literais, ajustar a comissão de um contrato é editar código e
publicar; e um número duplicado diverge no dia em que uma das cópias for
corrigida.

O REQUISITO QUE ESTE ARQUIVO GUARDA tem dois lados, e o segundo é o que
importa mais: com as envs configuradas, as contas mudam; **sem** elas, tudo sai
exatamente como antes. As métricas de e-Profit publicadas no README e os
números da demo não podem mudar em silêncio por causa de uma refatoração.
"""

import pytest

from crai.agent.workflow import _custo_previsto_do_ciclo, success_fee
from crai.config import (
    CUSTOS_PADRAO,
    CUSTO_TENTATIVA_PIX_PADRAO,
    ENV_CUSTO_TENTATIVA_PIX,
    ENV_CUSTO_WHATSAPP,
    ENV_SUCCESS_FEE,
    SUCCESS_FEE_PCT_PADRAO,
    custo_intervencao,
    custo_tentativa_pix,
    custos_por_canal,
    success_fee_pct,
)
from crai.dunning.pix_automatico_retry import MAX_TENTATIVAS
from crai.ml.failure_classifier import INTERVENTION_COSTS, FailureClassifier

VALOR = 299.90


@pytest.fixture(autouse=True)
def sem_envs_de_preco(monkeypatch):
    """Cada teste declara o que configura; nenhum herda do ambiente de quem roda."""
    for env in (ENV_SUCCESS_FEE, ENV_CUSTO_WHATSAPP, ENV_CUSTO_TENTATIVA_PIX):
        monkeypatch.delenv(env, raising=False)


class TestDefaultsSaoOsValoresAtuais:
    """A refatoração não pode mover número nenhum."""

    def test_o_fee_default_e_15_por_cento(self):
        assert success_fee_pct() == 0.15
        assert success_fee(VALOR, True) == round(VALOR * 0.15, 2)

    def test_o_custo_do_whatsapp_default_e_5_centavos(self):
        assert custo_intervencao() == 0.05
        assert custo_intervencao("bot_whatsapp") == 0.05

    def test_o_custo_por_tentativa_pix_default_e_zero(self):
        """Zero por escolha: o valor real é contratual e não é conhecido aqui.

        Com zero, o e-Profit sai idêntico ao de antes do Sprint 5 — nenhuma
        regressão silenciosa nas métricas já publicadas.
        """
        assert custo_tentativa_pix() == 0.0
        assert CUSTO_TENTATIVA_PIX_PADRAO == 0.0

    def test_a_tabela_de_canais_nao_mudou(self):
        assert custos_por_canal() == CUSTOS_PADRAO
        assert INTERVENTION_COSTS is CUSTOS_PADRAO, (
            "o nome que a suíte e o relatório de métricas importam deixou de "
            "apontar para a tabela de defaults")

    def test_o_eprofit_do_classificador_usa_o_mesmo_custo(self):
        c = FailureClassifier()
        resultado = c.predict({
            "gateway_error_code": "insufficient_funds", "card_brand": "visa",
            "tenure_months": 12, "day_of_month": 5, "invoice_amount": VALOR,
            "avg_ticket": VALOR, "payment_history_score": 0.8,
            "failure_count_90d": 1, "hour_of_day": 10, "day_of_week": 2,
            "attempt_count": 1, "ltv_estimated": 1000.0,
        })
        assert resultado["intervention_cost"] == 0.05


class TestEnvsMudamAsContas:

    @pytest.mark.parametrize("pct,esperado", [
        ("0.20", 59.98), ("0.10", 29.99), ("0", 0.0), ("1", 299.90),
    ])
    def test_o_fee_segue_a_env(self, monkeypatch, pct, esperado):
        monkeypatch.setenv(ENV_SUCCESS_FEE, pct)
        assert success_fee(VALOR, True) == pytest.approx(esperado)

    def test_o_custo_do_whatsapp_segue_a_env(self, monkeypatch):
        monkeypatch.setenv(ENV_CUSTO_WHATSAPP, "0.30")
        assert custo_intervencao() == 0.30
        assert custos_por_canal()["bot_whatsapp"] == 0.30
        # Os outros canais não se movem junto.
        assert custos_por_canal()["email_auto"] == CUSTOS_PADRAO["email_auto"]

    def test_a_env_e_lida_a_cada_chamada(self, monkeypatch):
        """Constante de módulo congelaria o valor no import do pacote."""
        assert success_fee_pct() == SUCCESS_FEE_PCT_PADRAO
        monkeypatch.setenv(ENV_SUCCESS_FEE, "0.25")
        assert success_fee_pct() == 0.25
        monkeypatch.delenv(ENV_SUCCESS_FEE)
        assert success_fee_pct() == SUCCESS_FEE_PCT_PADRAO

    def test_virgula_decimal_e_aceita(self, monkeypatch):
        """`0,20` é como um brasileiro escreve, inclusive num `.env`."""
        monkeypatch.setenv(ENV_SUCCESS_FEE, "0,20")
        assert success_fee_pct() == 0.20


class TestEnvTortaNaoDerrubaACobranca:
    """Um `.env` errado não pode parar a recuperação de todos os clientes."""

    @pytest.mark.parametrize("bruto", ["abc", "", "   ", "nan", "inf"])
    def test_valor_ilegivel_cai_no_default(self, monkeypatch, bruto):
        monkeypatch.setenv(ENV_SUCCESS_FEE, bruto)
        assert success_fee_pct() == SUCCESS_FEE_PCT_PADRAO

    @pytest.mark.parametrize("bruto", ["-0.5", "9", "1.01"])
    def test_fee_fora_da_faixa_cai_no_default(self, monkeypatch, bruto):
        """Cobrar mais de 100% do recuperado não é contrato, é erro de digitação."""
        monkeypatch.setenv(ENV_SUCCESS_FEE, bruto)
        assert success_fee_pct() == SUCCESS_FEE_PCT_PADRAO

    def test_custo_negativo_cai_no_default(self, monkeypatch):
        """Custo negativo viraria e-Profit inflado — a CRAI agiria onde não vale."""
        monkeypatch.setenv(ENV_CUSTO_WHATSAPP, "-1")
        assert custo_intervencao() == CUSTOS_PADRAO["bot_whatsapp"]


class TestCustoDaRetentativaNoEProfit:
    """Gap 6: recuperar na 3ª tentativa custa 3× o PSP de recuperar na 1ª."""

    def _state(self, metodo="pix_automatico", usadas=0):
        return {"payment_method": metodo, "retry_count": usadas, "amount": VALOR}

    def test_sem_env_o_custo_previsto_e_so_a_mensagem(self):
        """O requisito central: nada muda sem configuração."""
        assert _custo_previsto_do_ciclo(self._state()) == 0.05

    def test_com_env_o_custo_conta_as_tentativas_que_ainda_cabem(self, monkeypatch):
        monkeypatch.setenv(ENV_CUSTO_TENTATIVA_PIX, "0.10")
        esperado = 0.05 + 0.10 * MAX_TENTATIVAS
        assert _custo_previsto_do_ciclo(self._state()) == pytest.approx(esperado)

    def test_tentativas_ja_usadas_saem_da_conta(self, monkeypatch):
        monkeypatch.setenv(ENV_CUSTO_TENTATIVA_PIX, "0.10")
        assert _custo_previsto_do_ciclo(self._state(usadas=2)) == pytest.approx(0.15)

    def test_janela_esgotada_nao_soma_custo_de_tentativa(self, monkeypatch):
        monkeypatch.setenv(ENV_CUSTO_TENTATIVA_PIX, "0.10")
        assert _custo_previsto_do_ciclo(self._state(usadas=MAX_TENTATIVAS)) == 0.05

    def test_cartao_nao_soma_custo_de_tentativa_pix(self, monkeypatch):
        """Cartão está fora do pipeline ativo e não reenvia instrução de Pix."""
        monkeypatch.setenv(ENV_CUSTO_TENTATIVA_PIX, "0.10")
        assert _custo_previsto_do_ciclo(self._state(metodo="card")) == 0.05


class TestOEProfitDoNoDeAnomalia:
    """O custo previsto tem que chegar ao número que decide se a CRAI age."""

    @pytest.mark.asyncio
    async def test_o_no_usa_o_custo_configurado(self, monkeypatch):
        from crai.agent import workflow as workflow_module

        async def sem_anomalia(customer_id, evento):
            return {"is_anomaly": False, "error": 0.1, "threshold": 0.5,
                    "method": "teste", "top_features": []}

        monkeypatch.setattr(workflow_module._detector, "check", sem_anomalia)

        state = {
            "customer_id": "RN_cfg", "payment_event": {}, "amount": VALOR,
            "recovery_score": 50, "p_recovery": 0.5, "ltv_estimated": 100.0,
            "payment_method": "pix_automatico", "retry_count": 0,
        }

        padrao = await workflow_module.check_anomaly(dict(state))
        assert padrao["eprofit"] == pytest.approx(round(0.5 * 100.0 - 0.05, 2))

        monkeypatch.setenv(ENV_CUSTO_WHATSAPP, "10.00")
        caro = await workflow_module.check_anomaly(dict(state))
        assert caro["eprofit"] == pytest.approx(round(0.5 * 100.0 - 10.00, 2))
        assert caro["eprofit"] < padrao["eprofit"], (
            "o custo configurado não chegou ao e-Profit — é ele que decide se "
            "a CRAI intervém")


class TestONumeroNaoEstaDuplicado:

    def test_nao_ha_mais_literal_de_fee_nem_de_custo_nos_nos(self):
        """A regressão que este sprint existe para impedir: o literal voltar.

        Lê o fonte porque é a única forma de reprovar a REINTRODUÇÃO do número
        — um teste de comportamento passaria igual com o literal de volta, já
        que o default é o mesmo valor.
        """
        from pathlib import Path

        fonte = (Path(__file__).resolve().parent.parent / "crai" / "agent"
                 / "workflow.py").read_text(encoding="utf-8")
        linhas_de_codigo = [
            linha for linha in fonte.splitlines()
            if linha.strip() and not linha.strip().startswith("#")
        ]
        corpo = "\n".join(linhas_de_codigo)

        assert "* 0.15" not in corpo, "o success fee voltou a ser literal no nó"
        assert "cost = 0.05" not in corpo, "o custo do WhatsApp voltou a ser literal"

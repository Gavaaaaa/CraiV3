"""tests/test_pix_confirmacao.py — Sprint 1: o ciclo de recuperação fecha.

O QUE ESTE ARQUIVO EXISTE PARA PROVAR. Até o Sprint 1, `recovered` era
inicializado `False` em `_run_involuntary_pipeline` e nenhuma linha do projeto
o punha em `True`. As consequências não eram cosméticas:

    - o success fee (`amount * SUCCESS_FEE_PCT`) nunca disparava, e ele É a
      receita do modelo Outcome-as-a-Service;
    - o HubSpot nunca via o estágio `recovered`;
    - o par (features, recovered) — o dataset supervisionado que o Sprint 6
      grava e a fase de treino consome — teria `y` constante igual a zero.

É o equivalente involuntário do `random()` que o churn voluntário já teve: o
sistema decidia, agia e nunca descobria se tinha acertado.

O QUE NÃO PODE ACONTECER NA CORREÇÃO, e por isso tem teste próprio aqui:

    1. contar fee sobre mensalidade normal. O Pix Automático cobra todo mês; a
       maioria esmagadora das confirmações não fecha recuperação nenhuma.
       Contar fee sobre todas transformaria a receita da CRAI em percentual do
       faturamento do cliente;
    2. contar o mesmo fee duas vezes num reenvio de webhook — que é
       comportamento CORRETO de um PSP at-least-once, não anomalia;
    3. reprocessar o grafo na confirmação, agendando mais tentativas do BACEN
       (ou disparando dunning) contra quem acabou de pagar.
"""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from crai.agent.main_agent import crai_agent
from crai.agent.workflow import SUCCESS_FEE_PCT, success_fee
from crai.api import app as app_module
from crai.api.idempotencia import CICLOS_FECHADOS, EVENTOS_DE_FALHA

VALOR = 299.90


def _assinar(corpo: bytes, segredo: bytes = b"s3cr3t") -> dict:
    ts = int(time.time())
    mac = hmac.new(segredo, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json"}


def _corpo_falha(id_recorrencia: str, e2e: str, valor: float = VALOR) -> bytes:
    return json.dumps({
        "event": "automatic_pix.charge_failed",
        "e2e_id": e2e, "valor": valor, "id_recorrencia": id_recorrencia,
    }).encode()


def _corpo_pago(id_recorrencia: str, e2e: str, valor: float = VALOR) -> bytes:
    return json.dumps({
        "event": "automatic_pix.charge_paid",
        "e2e_id": e2e, "valor": valor, "id_recorrencia": id_recorrencia,
    }).encode()


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", "s3cr3t")
    with TestClient(app_module.app) as c:
        yield c


def _estado(id_recorrencia: str) -> dict:
    return dict(crai_agent.get_state(
        {"configurable": {"thread_id": id_recorrencia}}).values)


# ══════════════════════════════════════════════════════════════════════════
# O ciclo fecha
# ══════════════════════════════════════════════════════════════════════════

class TestConfirmacaoFechaOCiclo:

    def test_falha_seguida_de_confirmacao_marca_recovered(self, cliente):
        rec = "RN_s1_ciclo_completo"

        falha = _corpo_falha(rec, f"E_{rec}_falha")
        assert cliente.post("/webhooks/pix-automatico", content=falha,
                            headers=_assinar(falha)).status_code == 200
        assert _estado(rec)["recovered"] is False, (
            "o ciclo nasce aberto: sem confirmação não há recuperação")

        pago = _corpo_pago(rec, f"E_{rec}_pago")
        resposta = cliente.post("/webhooks/pix-automatico", content=pago,
                                headers=_assinar(pago))

        assert resposta.status_code == 200, resposta.text
        corpo = resposta.json()
        assert corpo["ciclo"] == "recuperado", corpo
        assert corpo["fee"] == pytest.approx(round(VALOR * SUCCESS_FEE_PCT, 2)), (
            f"fee de {corpo['fee']} sobre R$ {VALOR:.2f} — esperado "
            f"{SUCCESS_FEE_PCT:.0%} do valor recuperado")
        assert _estado(rec)["recovered"] is True, (
            "o checkpoint continua dizendo que o ciclo está aberto — o "
            "webhook de confirmação não gravou o desfecho")

    def test_a_confirmacao_nao_reprocessa_o_grafo(self, cliente, monkeypatch):
        """Reagendar tentativa do BACEN para quem pagou é o pior desfecho possível.

        Não é só desperdício: as 3 tentativas são um direito regulatório do
        recebedor DENTRO da janela, e gastá-las com uma cobrança já quitada
        significa cobrar de novo quem já pagou.
        """
        rec = "RN_s1_sem_reprocessar"

        falha = _corpo_falha(rec, f"E_{rec}_falha")
        cliente.post("/webhooks/pix-automatico", content=falha, headers=_assinar(falha))

        chamadas = []
        original = app_module.crai_agent.ainvoke

        async def espiao(state, config=None, *a, **kw):
            chamadas.append(config)
            return await original(state, config, *a, **kw)

        monkeypatch.setattr(app_module.crai_agent, "ainvoke", espiao)

        pago = _corpo_pago(rec, f"E_{rec}_pago")
        cliente.post("/webhooks/pix-automatico", content=pago, headers=_assinar(pago))

        assert chamadas == [], (
            "a confirmação de pagamento reexecutou o grafo — o nó "
            "schedule_retry_pix agendaria novas tentativas do BACEN, e o "
            "trigger_dunning mandaria cobrança, para um cliente que pagou")

    def test_o_estagio_do_crm_vira_recuperado(self, cliente, monkeypatch):
        rec = "RN_s1_crm"
        estagios = []

        original = app_module.update_roi_dashboard

        async def espiao(state):
            estagios.append(bool(state.get("recovered")))
            return await original(state)

        monkeypatch.setattr(app_module, "update_roi_dashboard", espiao)

        falha = _corpo_falha(rec, f"E_{rec}_falha")
        cliente.post("/webhooks/pix-automatico", content=falha, headers=_assinar(falha))
        pago = _corpo_pago(rec, f"E_{rec}_pago")
        cliente.post("/webhooks/pix-automatico", content=pago, headers=_assinar(pago))

        assert estagios == [True], (
            "o fechamento do ciclo deveria chamar o nó de ROI/CRM exatamente "
            f"uma vez, com recovered=True; chamadas: {estagios}")


# ══════════════════════════════════════════════════════════════════════════
# Idempotência — os dois lados
# ══════════════════════════════════════════════════════════════════════════

class TestIdempotenciaDaConfirmacao:

    def test_confirmacao_reenviada_nao_conta_fee_de_novo(self, cliente):
        """Gap 3, lado da receita: reenvio é normal; faturar em dobro não é."""
        rec = "RN_s1_reenvio_pago"

        falha = _corpo_falha(rec, f"E_{rec}_falha")
        cliente.post("/webhooks/pix-automatico", content=falha, headers=_assinar(falha))

        pago = _corpo_pago(rec, f"E_{rec}_pago")
        primeira = cliente.post("/webhooks/pix-automatico", content=pago,
                                headers=_assinar(pago)).json()
        segunda = cliente.post("/webhooks/pix-automatico", content=pago,
                               headers=_assinar(pago)).json()

        assert primeira["ciclo"] == "recuperado" and primeira["fee"] > 0
        assert segunda["ciclo"] == "reenvio", segunda
        assert segunda["fee"] == 0.0, (
            f"o reenvio contou fee de R$ {segunda['fee']:.2f} — o mesmo "
            "pagamento foi faturado duas vezes")

    def test_outra_confirmacao_do_mesmo_ciclo_tambem_nao_recobra(self, cliente):
        """e2e diferente, ciclo já fechado: a segunda barreira é o state.

        A janela de idempotência é por e2e_id. Um PSP que confirme o mesmo
        ciclo por duas transações distintas (estorno e recobrança, por
        exemplo) passaria por ela — quem barra aí é o `recovered` já gravado.
        """
        rec = "RN_s1_dois_e2e"

        falha = _corpo_falha(rec, f"E_{rec}_falha")
        cliente.post("/webhooks/pix-automatico", content=falha, headers=_assinar(falha))

        for sufixo in ("pago_a", "pago_b"):
            corpo = _corpo_pago(rec, f"E_{rec}_{sufixo}")
            resultado = cliente.post("/webhooks/pix-automatico", content=corpo,
                                     headers=_assinar(corpo)).json()
            esperado = "recuperado" if sufixo == "pago_a" else "ja_recuperado"
            assert resultado["ciclo"] == esperado, resultado
            if sufixo == "pago_b":
                assert resultado["fee"] == 0.0


class TestIdempotenciaDaFalha:
    """Gap 3, lado do custo: o mesmo evento de falha não roda o pipeline 2x."""

    def test_falha_reenviada_nao_reexecuta_o_pipeline(self, cliente, monkeypatch):
        rec = "RN_s1_reenvio_falha"
        corpo = _corpo_falha(rec, f"E_{rec}_falha")

        execucoes = []
        original = app_module.crai_agent.ainvoke

        async def espiao(state, config=None, *a, **kw):
            execucoes.append(config)
            return await original(state, config, *a, **kw)

        monkeypatch.setattr(app_module.crai_agent, "ainvoke", espiao)

        primeira = cliente.post("/webhooks/pix-automatico", content=corpo,
                                headers=_assinar(corpo)).json()
        segunda = cliente.post("/webhooks/pix-automatico", content=corpo,
                               headers=_assinar(corpo)).json()

        assert primeira["pipeline"] is True
        assert segunda["pipeline"] is False and segunda["motivo"] == "evento_ja_processado"
        assert len(execucoes) == 1, (
            f"o mesmo evento de falha rodou o pipeline {len(execucoes)} vezes — "
            "diagnóstico, LLM e (Sprint 2) chamada ao PSP pagos em duplicidade")

    def test_cobranca_distinta_do_mesmo_contrato_nao_e_confundida_com_reenvio(
            self, cliente, monkeypatch):
        """A janela não pode calar o mês seguinte.

        Duas cobranças do mesmo `id_recorrencia` têm e2e diferentes. Se a chave
        fosse só o contrato, a segunda falha real seria descartada como reenvio
        e o cliente ficaria sem recuperação nenhuma.
        """
        rec = "RN_s1_duas_cobrancas"
        execucoes = []
        original = app_module.crai_agent.ainvoke

        async def espiao(state, config=None, *a, **kw):
            execucoes.append(config)
            return await original(state, config, *a, **kw)

        monkeypatch.setattr(app_module.crai_agent, "ainvoke", espiao)

        for indice in range(2):
            corpo = _corpo_falha(rec, f"E_{rec}_{indice}")
            resposta = cliente.post("/webhooks/pix-automatico", content=corpo,
                                    headers=_assinar(corpo))
            assert resposta.status_code == 200, resposta.text

        assert len(execucoes) == 2, (
            "a segunda cobrança do mesmo contrato foi descartada como reenvio")

    def test_a_janela_nao_cresce_sem_limite(self):
        """Teto de entradas: um processo que roda meses não pode vazar memória."""
        janela = type(EVENTOS_DE_FALHA)("teste", max_entradas=3)
        for i in range(10):
            assert janela.registrar_se_novo(f"chave_{i}") is True
        assert len(janela) == 3
        # A mais antiga saiu; por isso volta a ser "nova".
        assert janela.registrar_se_novo("chave_0") is True
        assert janela.registrar_se_novo("chave_9") is False


# ══════════════════════════════════════════════════════════════════════════
# O fee só existe onde houve recuperação
# ══════════════════════════════════════════════════════════════════════════

class TestFeeSoOndeHouveRecuperacao:

    def test_mensalidade_normal_nao_gera_fee(self, cliente):
        """A confirmação de quem nunca falhou não é recuperação.

        Este é o defeito de negócio mais caro que a correção do Sprint 1 podia
        introduzir: cobrar 15% de TODA cobrança confirmada do cliente.
        """
        pago = _corpo_pago("RN_s1_nunca_falhou", "E_mensalidade_normal")
        resposta = cliente.post("/webhooks/pix-automatico", content=pago,
                                headers=_assinar(pago)).json()

        assert resposta["ciclo"] == "sem_ciclo_aberto", resposta
        assert resposta["fee"] == 0.0, (
            f"fee de R$ {resposta['fee']:.2f} sobre uma mensalidade que nunca "
            "falhou — a CRAI passaria a cobrar percentual do faturamento")

    def test_success_fee_e_zero_sem_recuperacao(self):
        assert success_fee(VALOR, False) == 0.0
        assert success_fee(VALOR, True) == round(VALOR * SUCCESS_FEE_PCT, 2)

    def test_confirmacao_sem_identificacao_nao_derruba_o_webhook(self, cliente):
        """Sem `id_recorrencia` nem `e2e_id` não há ciclo a fechar — mas é 200.

        Um 4xx faria o PSP retentar para sempre um evento sobre o qual não há
        nada a fazer. É a mesma escolha do `/webhooks/retention-outcome`.
        """
        corpo = json.dumps({"event": "automatic_pix.charge_paid",
                            "valor": VALOR}).encode()
        resposta = cliente.post("/webhooks/pix-automatico", content=corpo,
                                headers=_assinar(corpo))

        assert resposta.status_code == 200, resposta.text
        assert resposta.json()["ciclo"] == "sem_identificacao"


# ══════════════════════════════════════════════════════════════════════════
# O gatilho da demo
# ══════════════════════════════════════════════════════════════════════════

class TestSimulacaoDoCicloCompleto:
    """A banca precisa ver o loop inteiro sem PSP real (`/simulate/pix-pago`)."""

    def test_simulate_fecha_o_ciclo_aberto_por_simulate(self, cliente, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        rec = "RN_s1_demo"

        assert cliente.post("/simulate/pix-falhado", json={
            "id_recorrencia": rec, "valor": VALOR,
        }).status_code == 200

        resposta = cliente.post("/simulate/pix-pago", json={"id_recorrencia": rec})

        assert resposta.status_code == 200, resposta.text
        corpo = resposta.json()
        assert corpo["ciclo"] == "recuperado", corpo
        assert corpo["fee"] == pytest.approx(round(VALOR * SUCCESS_FEE_PCT, 2))

    def test_simulate_respeita_o_portao_de_ambiente(self, cliente, monkeypatch):
        """O endpoint move faturamento: em produção ele não existe."""
        monkeypatch.setenv("ENV", "production")
        resposta = cliente.post("/simulate/pix-pago", json={"id_recorrencia": "RN_x"})
        assert resposta.status_code == 403


def test_as_duas_janelas_sao_independentes():
    """Um e2e de falha não pode silenciar o e2e de uma confirmação."""
    chave = json.dumps(["RN_z", "E_z"])
    assert EVENTOS_DE_FALHA.registrar_se_novo(chave) is True
    assert CICLOS_FECHADOS.registrar_se_novo(chave) is True, (
        "as duas janelas compartilharam memória — uma falha registrada faria "
        "a confirmação do mesmo id ser descartada como reenvio")

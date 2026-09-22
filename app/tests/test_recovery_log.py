"""tests/test_recovery_log.py — Sprint 6: o loop de dados e o de dinheiro fecham.

TRÊS BURACOS, DE NATUREZAS DIFERENTES, que o log de ciclo fecha:

  TREINO (Gaps 4 e 5). O `FailureClassifier` é treinado em dados SINTÉTICOS. Para
      trocar por dados reais é preciso o par (features, recovered) de ciclos que
      aconteceram — e ele não existia. As features morriam dentro de
      `diagnose_failure`, o `MemorySaver` morre no restart, e rodar três meses em
      produção daria exatamente zero linha de treino.

  MARGEM (Gap 6). Recuperar na 3ª tentativa custa 3× o PSP de recuperar na 1ª.
      O `[ROI]` imprimia fee e e-Profit PREVISTO; o custo REALIZADO não existia
      em lugar nenhum, e sem ele não há margem por recuperação.

  AGREGADO (Gap 7). MRR recuperado, taxa de recuperação e custo médio não eram
      calculáveis, porque não havia de onde somar.

O QUE NÃO PODE ACONTECER, e por isso tem teste próprio:
  - o mesmo `e2e_id` gerar duas linhas — o fee entraria em dobro no agregado, e
    o agregado é o que vai para a banca;
  - um desfecho voltar atrás (linha fechada reabrir como perdida);
  - o custo médio ser calculado por CICLO em vez de por RECUPERAÇÃO, o que
    diluiria o número nos ciclos que não geraram fee e faria a operação parecer
    mais barata do que é.
"""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from crai.agent.workflow import tentativas_ja_disparadas
from crai.api import app as app_module
from crai.config import ENV_CUSTO_TENTATIVA_PIX, ENV_CUSTO_WHATSAPP, ENV_SUCCESS_FEE
from crai.dunning import recovery_log, retry_state
from crai.dunning.recovery_log import (
    FEATURES_DO_DATASET,
    custo_realizado,
    linhas,
    metricas,
    registrar_ciclo,
    registrar_recuperacao,
)
from crai.ml.failure_classifier import ALL_FEATURES, ALL_FEATURES_V2

VALOR = 299.90
TENANT = "empresa_log"


def _assinar(corpo: bytes, segredo: bytes = b"s3cr3t") -> dict:
    ts = int(time.time())
    mac = hmac.new(segredo, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json"}


def _corpo(rec: str, e2e: str, evento="automatic_pix.charge_failed",
           valor: float = VALOR) -> bytes:
    return json.dumps({"event": evento, "e2e_id": e2e, "valor": valor,
                       "id_recorrencia": rec}).encode()


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", "s3cr3t")
    with TestClient(app_module.app) as c:
        yield c


def _state(customer_id="RN_log", e2e="E_log", **extra) -> dict:
    """Um state como o que chega ao `update_roi_dashboard`."""
    base = {
        "customer_id": customer_id, "invoice_id": e2e, "tenant_id": TENANT,
        "amount": VALOR, "failure_cause": "insufficient_funds",
        "recovery_score": 72, "p_recovery": 0.72, "eprofit": 120.5,
        "estrategia": "retry_automatico", "recovered": False,
        "dunning_sent": False, "pix_retry_schedule": [
            {"numero": 1}, {"numero": 2}, {"numero": 3}],
        "features": {
            "tenure_months": 14, "day_of_month": 5, "invoice_amount": VALOR,
            "avg_ticket": 310.0, "payment_history_score": 0.83,
            "failure_count_90d": 1, "hour_of_day": 10, "day_of_week": 2,
            "attempt_count": 1, "gateway_error_code": "insufficient_funds",
            "metodo_pagamento": "pix_automatico", "ltv_estimated": 2100.0,
        },
    }
    base.update(extra)
    return base


class TestOContratoDoDataset:

    def test_as_features_do_log_cobrem_as_do_classificador(self):
        """Se o classificador ganhar uma feature e o log não, o treino recebe
        uma coluna a menos — e ninguém percebe até o `fit` reclamar de shape."""
        # As duas listas: a v2 é a do artefato em produção (Bloco H), a v1 é
        # a que `train_all --base v1` ainda consome.
        for lista in (ALL_FEATURES_V2, ALL_FEATURES):
            faltando = set(lista) - set(FEATURES_DO_DATASET)
            assert not faltando, (
                f"features consumidas pelo classificador e ausentes do dataset: "
                f"{sorted(faltando)}")

    def test_o_ltv_entra_mesmo_nao_sendo_feature_de_treino(self):
        """LTV não é X do modelo, mas é o que faz o e-Profit — sem ele a linha
        não permite recalcular a decisão depois."""
        assert "ltv_estimated" in FEATURES_DO_DATASET


class TestGravacaoDoCiclo:

    def test_um_ciclo_perdido_grava_recovered_zero(self):
        assert registrar_ciclo(_state()) is not None
        linha = linhas(TENANT)[0]

        assert linha["recovered"] == 0
        assert linha["desfecho_em"] is None, (
            "o ciclo nasce ABERTO: em produção ninguém sabe o desfecho ainda")
        assert linha["success_fee"] == 0.0

    def test_as_features_e_a_decisao_ficam_na_linha(self):
        registrar_ciclo(_state())
        linha = linhas(TENANT)[0]

        assert linha["tenure_months"] == 14
        assert linha["payment_history_score"] == pytest.approx(0.83)
        assert linha["gateway_error_code"] == "insufficient_funds"
        assert linha["ltv_estimated"] == 2100.0
        assert linha["estrategia"] == "retry_automatico"
        assert linha["tentativas_planejadas"] == 3

    def test_ciclo_sem_e2e_nao_grava(self):
        """Sem chave não há como fechar a linha nem deduplicá-la."""
        assert registrar_ciclo(_state(e2e="")) is None
        assert linhas(TENANT) == []

    def test_o_mesmo_e2e_nao_duplica_linha(self):
        """O fee entraria em dobro no agregado — e o agregado vai para a banca."""
        for _ in range(3):
            registrar_ciclo(_state())
        assert len(linhas(TENANT)) == 1

    def test_cobrancas_distintas_do_mesmo_cliente_sao_linhas_distintas(self):
        registrar_ciclo(_state(e2e="E_mes_1"))
        registrar_ciclo(_state(e2e="E_mes_2"))
        assert len(linhas(TENANT)) == 2

    def test_dois_tenants_com_o_mesmo_e2e_nao_colidem(self):
        registrar_ciclo(_state())
        registrar_ciclo(_state(tenant_id="outra_empresa"))
        assert len(linhas(TENANT)) == 1
        assert len(linhas("outra_empresa")) == 1


class TestFechamentoComDesfechoReal:

    def test_a_confirmacao_fecha_a_linha(self):
        registrar_ciclo(_state())
        assert registrar_recuperacao("RN_log", "E_pago", VALOR, TENANT,
                                     tentativas_usadas=2) is True

        linha = linhas(TENANT)[0]
        assert linha["recovered"] == 1
        assert linha["desfecho_em"] is not None
        assert linha["tentativas_usadas"] == 2
        assert linha["success_fee"] == pytest.approx(round(VALOR * 0.15, 2))

    def test_reenvio_da_confirmacao_nao_fecha_de_novo(self):
        registrar_ciclo(_state())
        assert registrar_recuperacao("RN_log", "E_pago", VALOR, TENANT) is True
        assert registrar_recuperacao("RN_log", "E_pago", VALOR, TENANT) is False, (
            "a segunda confirmação encontrou ciclo aberto — o fee seria somado "
            "duas vezes no agregado")

    def test_confirmacao_sem_ciclo_aberto_devolve_falso(self):
        assert registrar_recuperacao("RN_nunca_falhou", "E_x", VALOR, TENANT) is False

    def test_reexecucao_do_grafo_nao_reabre_um_ciclo_fechado(self):
        """O desfecho não pode voltar atrás.

        O grafo pode rodar de novo para o mesmo `e2e_id` — reentrega depois de
        um restart, que zerou a janela de idempotência da API. A regravação
        atualiza o diagnóstico, mas uma recuperação já registrada continua
        registrada.
        """
        registrar_ciclo(_state())
        registrar_recuperacao("RN_log", "E_pago", VALOR, TENANT)
        registrar_ciclo(_state())          # reentrega, com recovered=False

        assert linhas(TENANT)[0]["recovered"] == 1


class TestCustoRealizado:
    """Gap 6: o custo que aconteceu, não o previsto."""

    @pytest.fixture(autouse=True)
    def custo_por_tentativa(self, monkeypatch):
        monkeypatch.setenv(ENV_CUSTO_TENTATIVA_PIX, "0.40")
        monkeypatch.setenv(ENV_CUSTO_WHATSAPP, "0.05")

    def test_tres_tentativas_custam_o_triplo_de_uma(self):
        assert custo_realizado(1, False) == pytest.approx(0.40)
        assert custo_realizado(3, False) == pytest.approx(1.20)

    def test_a_mensagem_so_custa_se_foi_enviada(self):
        assert custo_realizado(0, False) == 0.0
        assert custo_realizado(0, True) == pytest.approx(0.05)

    def test_o_custo_entra_na_linha(self):
        registrar_ciclo(_state(dunning_sent=True), tentativas_usadas=3)
        assert linhas(TENANT)[0]["custo_total"] == pytest.approx(1.25)

    def test_sem_env_o_custo_de_tentativa_e_zero(self, monkeypatch):
        """Default zero: o valor real é contratual e não é conhecido aqui."""
        monkeypatch.delenv(ENV_CUSTO_TENTATIVA_PIX)
        assert custo_realizado(3, False) == 0.0


class TestMetricasDeNegocio:
    """Gap 7: o número que sustenta o Outcome-as-a-Service."""

    def _dataset(self, monkeypatch):
        monkeypatch.setenv(ENV_CUSTO_TENTATIVA_PIX, "0.50")
        monkeypatch.setenv(ENV_CUSTO_WHATSAPP, "0.05")
        # 4 ciclos: 2 recuperados (R$ 299,90 e R$ 100,00), 2 perdidos.
        registrar_ciclo(_state(e2e="E_1"), tentativas_usadas=1)
        registrar_recuperacao("RN_log", "E_1", VALOR, TENANT, tentativas_usadas=1)

        registrar_ciclo(_state(e2e="E_2", customer_id="RN_b", amount=100.0),
                        tentativas_usadas=3)
        registrar_recuperacao("RN_b", "E_2", 100.0, TENANT, tentativas_usadas=3)

        registrar_ciclo(_state(e2e="E_3", customer_id="RN_c", dunning_sent=True))
        registrar_ciclo(_state(e2e="E_4", customer_id="RN_d", dunning_sent=True))

    def test_taxa_de_recuperacao(self, monkeypatch):
        self._dataset(monkeypatch)
        m = metricas(TENANT)
        assert m["ciclos"] == 4 and m["recuperados"] == 2
        assert m["taxa_recuperacao"] == 0.5

    def test_mrr_recuperado_soma_so_o_que_foi_recuperado(self, monkeypatch):
        self._dataset(monkeypatch)
        m = metricas(TENANT)
        assert m["mrr_recuperado"] == pytest.approx(VALOR + 100.0)
        assert m["volume_total"] == pytest.approx(VALOR * 3 + 100.0)

    def test_custo_medio_e_por_recuperacao_nao_por_ciclo(self, monkeypatch):
        """Custo TOTAL sobre RECUPERAÇÕES — as duas escolhas apontam pro mesmo lado.

        O numerador inclui o gasto dos ciclos perdidos: aquele dinheiro foi
        gasto perseguindo recuperação. O denominador são as recuperações, não
        os ciclos: dividir por ciclo diluiria o custo nos que não geraram fee
        nenhum e faria a operação parecer mais barata do que é.
        """
        self._dataset(monkeypatch)
        m = metricas(TENANT)
        # recuperados: 1×0,50 + 3×0,50 = 2,00 ; perdidos: 2×0,05 = 0,10
        assert m["custo_total"] == pytest.approx(2.10)
        assert m["custo_medio_por_recuperacao"] == pytest.approx(2.10 / 2)

    def test_margem_e_fee_menos_custo(self, monkeypatch):
        self._dataset(monkeypatch)
        m = metricas(TENANT)
        esperado = round(VALOR * 0.15, 2) + round(100.0 * 0.15, 2)
        assert m["fee_total"] == pytest.approx(esperado)
        assert m["margem"] == pytest.approx(round(esperado - 2.10, 2))

    def test_o_filtro_por_tenant_separa_as_empresas(self, monkeypatch):
        self._dataset(monkeypatch)
        registrar_ciclo(_state(e2e="E_outra", tenant_id="outra_empresa"))

        assert metricas(TENANT)["ciclos"] == 4
        assert metricas("outra_empresa")["ciclos"] == 1
        assert metricas()["ciclos"] == 5

    def test_o_filtro_por_data_recorta_o_periodo(self, monkeypatch):
        self._dataset(monkeypatch)
        assert metricas(TENANT, desde="2000-01-01")["ciclos"] == 4
        assert metricas(TENANT, desde="2999-01-01")["ciclos"] == 0

    def test_dataset_vazio_nao_divide_por_zero(self):
        m = metricas("tenant_que_nao_existe")
        assert m["ciclos"] == 0
        assert m["taxa_recuperacao"] == 0.0
        assert m["custo_medio_por_recuperacao"] == 0.0

    def test_o_fee_segue_a_env(self, monkeypatch):
        monkeypatch.setenv(ENV_SUCCESS_FEE, "0.20")
        registrar_ciclo(_state())
        registrar_recuperacao("RN_log", "E_pago", VALOR, TENANT)
        assert metricas(TENANT)["fee_total"] == pytest.approx(round(VALOR * 0.20, 2))


class TestOPipelineGravaDeVerdade:
    """Ponta a ponta: o webhook alimenta o dataset sem ninguém chamar o log."""

    def test_uma_falha_pelo_webhook_vira_linha(self, cliente):
        rec = "RN_log_e2e"
        corpo = _corpo(rec, f"E_{rec}")
        cliente.post("/webhooks/pix-automatico", content=corpo,
                     headers={**_assinar(corpo), "x-tenant-id": TENANT})

        registros = linhas(TENANT)
        assert len(registros) == 1, "o pipeline não gravou o ciclo"
        assert registros[0]["customer_id"] == rec
        assert registros[0]["recovered"] == 0
        assert registros[0]["failure_cause"], "a linha saiu sem diagnóstico"
        assert registros[0]["tenure_months"] is not None, (
            "as features não chegaram ao dataset — o par (X, y) fica sem X e o "
            "log deixa de servir para treino")

    def test_o_ciclo_completo_fecha_a_linha(self, cliente):
        rec = "RN_log_completo"
        falha = _corpo(rec, f"E_{rec}_falha")
        cliente.post("/webhooks/pix-automatico", content=falha,
                     headers={**_assinar(falha), "x-tenant-id": TENANT})
        pago = _corpo(rec, f"E_{rec}_pago", evento="automatic_pix.charge_paid")
        cliente.post("/webhooks/pix-automatico", content=pago,
                     headers={**_assinar(pago), "x-tenant-id": TENANT})

        linha = linhas(TENANT)[0]
        assert linha["recovered"] == 1, (
            "a confirmação não fechou a linha — o `y` do dataset ficaria "
            "constante em zero, que é o defeito do Sprint 1 de volta")
        assert linha["success_fee"] > 0

    def test_o_endpoint_de_metricas_responde(self, cliente):
        rec = "RN_log_metrica"
        corpo = _corpo(rec, f"E_{rec}")
        cliente.post("/webhooks/pix-automatico", content=corpo,
                     headers={**_assinar(corpo), "x-tenant-id": TENANT})

        resposta = cliente.get(f"/metrics/recovery?tenant_id={TENANT}")
        assert resposta.status_code == 200, resposta.text
        corpo_json = resposta.json()
        assert corpo_json["ciclos"] == 1
        assert set(corpo_json) >= {"mrr_recuperado", "taxa_recuperacao",
                                   "custo_total", "custo_medio_por_recuperacao",
                                   "fee_total", "margem"}

    def test_as_tentativas_disparadas_vem_do_retry_state(self):
        """O custo conta o que SAIU, não o que foi planejado."""
        retry_state.save_retry_state(
            customer_id="RN_disparos", valor_original=VALOR, tenant_id=TENANT,
            tentativas=[{"numero": n, "quando": "2026-09-10T09:00:00",
                         "valor": VALOR, "origem": "teste"} for n in (1, 2, 3)],
        )
        estado = {"customer_id": "RN_disparos", "tenant_id": TENANT}
        assert tentativas_ja_disparadas(estado) == 0

        retry_state.marcar_disparada("RN_disparos", 1, "ch_1", tenant_id=TENANT)
        assert tentativas_ja_disparadas(estado) == 1


class TestNuncaLevanta:
    """Perder linha de dataset é ruim; derrubar a cobrança de um cliente é pior."""

    def test_banco_inacessivel_nao_propaga(self, monkeypatch, tmp_path):
        arquivo = tmp_path / "nao_e_um_banco.db"
        arquivo.write_bytes(b"isto nao e um sqlite")
        monkeypatch.setenv(recovery_log.ENV_CAMINHO, str(arquivo))

        assert registrar_ciclo(_state()) is None
        assert registrar_recuperacao("RN_x", "E_x", VALOR, TENANT) is False
        assert metricas(TENANT)["ciclos"] == 0
        assert linhas(TENANT) == []

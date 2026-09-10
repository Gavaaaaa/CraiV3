"""tests/test_tenant_involuntary.py — Sprint 4: o involuntário ganha tenant.

A ASSIMETRIA QUE ESTE ARQUIVO FECHA. O churn voluntário já era isolado por
tenant — `ChurnVoluntaryState.tenant_id`, chave do bandit, coluna do dataset de
treino, propriedade do deal. O involuntário não tinha o campo. Com dois
clientes da CRAI na mesma instalação, as consequências eram concretas:

    - o HubSpot misturava os negócios de recuperação de duas empresas no mesmo
      pipeline, sem nada que os separasse no relatório;
    - as instruções reenviadas ao Pagar.me não eram atribuíveis a quem as
      pagou — e cada tentativa custa;
    - dois planos de retentativa com o mesmo `id_recorrencia`, vindos de
      empresas diferentes, dividiam a mesma chave no estado.

O QUE ESTA FASE **NÃO** FAZ, e tem teste para provar: mudar comportamento por
tenant. Aqui `tenant_id` é propagação e atribuição. Decisão que dependa de
tenant é RBAC/produto e entra por outra porta — se um dia dois tenants
receberem recuperações diferentes por serem tenants diferentes, é aqui que
aparece.
"""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from crai.agent.main_agent import crai_agent
from crai.api import app as app_module
from crai.churn_voluntary.offer_bandit import TENANT_PADRAO as TENANT_PADRAO_VOLUNTARIO
from crai.dunning import retry_state

VALOR = 299.90
TENANT_PADRAO = "default_tenant"


def _assinar(corpo: bytes, segredo: bytes = b"s3cr3t") -> dict:
    ts = int(time.time())
    mac = hmac.new(segredo, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json"}


def _corpo(id_recorrencia: str, e2e: str, evento="automatic_pix.charge_failed",
           **extra) -> bytes:
    return json.dumps({
        "event": evento, "e2e_id": e2e, "valor": VALOR,
        "id_recorrencia": id_recorrencia, **extra,
    }).encode()


def _estado(id_recorrencia: str) -> dict:
    return dict(crai_agent.get_state(
        {"configurable": {"thread_id": id_recorrencia}}).values)


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", "s3cr3t")
    monkeypatch.setenv("ENV", "development")
    with TestClient(app_module.app) as c:
        yield c


class TestOTenantChegaAoState:

    def test_header_x_tenant_id_entra_no_pipeline(self, cliente):
        rec = "RN_s4_header"
        corpo = _corpo(rec, f"E_{rec}")
        resposta = cliente.post("/webhooks/pix-automatico", content=corpo,
                                headers={**_assinar(corpo), "x-tenant-id": "empresa_a"})

        assert resposta.status_code == 200, resposta.text
        assert _estado(rec)["tenant_id"] == "empresa_a"

    def test_tenant_id_no_corpo_tambem_vale(self, cliente):
        rec = "RN_s4_corpo"
        corpo = _corpo(rec, f"E_{rec}", tenant_id="empresa_b")
        cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))

        assert _estado(rec)["tenant_id"] == "empresa_b"

    def test_ausencia_vira_default_tenant(self, cliente):
        """MVP declarado: exigir o campo quebraria os webhooks já integrados."""
        rec = "RN_s4_sem_tenant"
        corpo = _corpo(rec, f"E_{rec}")
        cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))

        assert _estado(rec)["tenant_id"] == TENANT_PADRAO

    def test_tenant_torto_e_recusado_em_vez_de_cair_no_default(self, cliente):
        """Cair no default silenciosamente misturaria quem tentou se identificar.

        Mesma regra do voluntário: ausente é o balde do MVP; declarado e
        inválido é 422. É a diferença entre não saber e saber errado.
        """
        rec = "RN_s4_torto"
        corpo = _corpo(rec, f"E_{rec}")
        resposta = cliente.post("/webhooks/pix-automatico", content=corpo,
                                headers={**_assinar(corpo), "x-tenant-id": "tenant com espaco"})

        assert resposta.status_code == 422, resposta.text
        assert resposta.json()["detail"]["motivo"] == "tenant_com_forma_invalida"

    def test_default_tenant_declarado_de_fora_e_recusado(self, cliente):
        """O balde do 'não declarado' não pode ser escolhido de propósito."""
        rec = "RN_s4_reservado"
        corpo = _corpo(rec, f"E_{rec}")
        resposta = cliente.post("/webhooks/pix-automatico", content=corpo,
                                headers={**_assinar(corpo), "x-tenant-id": TENANT_PADRAO})

        assert resposta.status_code == 422
        assert resposta.json()["detail"]["motivo"] == "tenant_reservado"

    def test_o_simulate_usa_o_mesmo_portao(self, cliente):
        """N-7: o endpoint da demo tem que exercitar o código que a demo mostra."""
        rec = "RN_s4_simulate"
        resposta = cliente.post("/simulate/pix-falhado",
                                json={"id_recorrencia": rec, "valor": VALOR,
                                      "tenant_id": "empresa_sim"})

        assert resposta.status_code == 200, resposta.text
        assert _estado(rec)["tenant_id"] == "empresa_sim"


class TestOTenantChegaAosEfeitosExternos:

    def test_o_deal_de_recuperacao_carrega_o_tenant(self, cliente, monkeypatch):
        """Sem isto, um HubSpot compartilhado mistura duas empresas no relatório."""
        capturado = {}
        from crai.agent import workflow as workflow_module

        original = workflow_module._hubspot.create_deal

        async def espiao(name, pipeline, stage, props):
            if pipeline == "crai_recovery":
                capturado.update(props)
            return await original(name, pipeline, stage, props)

        monkeypatch.setattr(workflow_module._hubspot, "create_deal", espiao)

        rec = "RN_s4_crm"
        corpo = _corpo(rec, f"E_{rec}")
        cliente.post("/webhooks/pix-automatico", content=corpo,
                     headers={**_assinar(corpo), "x-tenant-id": "empresa_crm"})

        assert capturado.get("tenant_id") == "empresa_crm"

    def test_o_reenvio_ao_psp_e_atribuido(self, cliente, monkeypatch):
        """Cada instrução reenviada custa; sem tenant não se sabe de quem é."""
        chamadas = []
        import crai.dunning.retry_scheduler as sched

        original = sched.reenviar_cobranca_pix

        async def espiao(**kwargs):
            chamadas.append(kwargs)
            return await original(**kwargs)

        monkeypatch.setattr(sched, "reenviar_cobranca_pix", espiao)

        # Janela já aberta: a primeira tentativa é devida e o próprio nó a
        # dispara, sem precisar do agendador.
        from datetime import datetime, timedelta
        from crai.agent import workflow as workflow_module
        monkeypatch.setattr(workflow_module._pix_retry, "confianca_minima", 2.0)

        rec = "RN_s4_psp"
        crai_agent.update_state(
            {"configurable": {"thread_id": rec}},
            {"pix_janela_ate": datetime.now() + timedelta(days=5), "retry_count": 0},
        )
        corpo = _corpo(rec, f"E_{rec}")
        cliente.post("/webhooks/pix-automatico", content=corpo,
                     headers={**_assinar(corpo), "x-tenant-id": "empresa_psp"})

        assert chamadas, "nenhuma instrução foi reenviada — o teste não mediu nada"
        assert all(c.get("tenant_id") == "empresa_psp" for c in chamadas)

    def test_o_plano_de_retentativa_separa_tenants(self, cliente):
        """Dois clientes da CRAI podem ter o mesmo `id_recorrencia`."""
        rec = "RN_s4_compartilhado"
        for tenant in ("empresa_x", "empresa_y"):
            corpo = _corpo(f"{tenant}_{rec}", f"E_{tenant}_{rec}")
            cliente.post("/webhooks/pix-automatico", content=corpo,
                         headers={**_assinar(corpo), "x-tenant-id": tenant})

        for tenant in ("empresa_x", "empresa_y"):
            registro = retry_state.get_retry_state(f"{tenant}_{rec}", tenant_id=tenant)
            if registro is not None:      # só há plano quando a decisão foi retentar
                assert registro["tenant_id"] == tenant

    def test_a_chave_do_plano_isola_o_mesmo_id_de_recorrencia(self):
        """O caso direto, sem depender de qual estratégia a decisão escolheu."""
        for tenant in ("empresa_x", "empresa_y"):
            retry_state.save_retry_state(
                customer_id="RN_igual", valor_original=VALOR, tenant_id=tenant,
                tentativas=[{"numero": 1, "quando": "2026-09-10T09:00:00",
                             "valor": VALOR, "origem": "teste"}],
            )
        x = retry_state.get_retry_state("RN_igual", tenant_id="empresa_x")
        y = retry_state.get_retry_state("RN_igual", tenant_id="empresa_y")

        assert x["tenant_id"] == "empresa_x" and y["tenant_id"] == "empresa_y", (
            "dois tenants com o mesmo id de recorrência dividiram o mesmo plano")


class TestFechamentoDeCicloAtribuido:

    def test_a_recuperacao_e_atribuida_ao_tenant_do_ciclo(self, cliente):
        rec = "RN_s4_fechamento"
        falha = _corpo(rec, f"E_{rec}_falha")
        cliente.post("/webhooks/pix-automatico", content=falha,
                     headers={**_assinar(falha), "x-tenant-id": "empresa_dona"})

        pago = _corpo(rec, f"E_{rec}_pago", evento="automatic_pix.charge_paid")
        resultado = cliente.post("/webhooks/pix-automatico", content=pago,
                                 headers={**_assinar(pago), "x-tenant-id": "empresa_dona"}).json()

        assert resultado["ciclo"] == "recuperado"
        assert resultado["tenant_id"] == "empresa_dona"

    def test_confirmacao_de_outro_tenant_nao_reatribui_a_recuperacao(self, cliente):
        """Quem rodou a recuperação foi o tenant do ciclo — e o fee é dele.

        Uma confirmação declarando outro tenant ou é erro de integração do
        cliente ou é tentativa de atribuição indevida. Nos dois casos a
        atribuição segue o ciclo.
        """
        rec = "RN_s4_intruso"
        falha = _corpo(rec, f"E_{rec}_falha")
        cliente.post("/webhooks/pix-automatico", content=falha,
                     headers={**_assinar(falha), "x-tenant-id": "empresa_dona"})

        pago = _corpo(rec, f"E_{rec}_pago", evento="automatic_pix.charge_paid")
        resultado = cliente.post("/webhooks/pix-automatico", content=pago,
                                 headers={**_assinar(pago), "x-tenant-id": "empresa_intrusa"}).json()

        assert resultado["tenant_id"] == "empresa_dona", (
            "a recuperação foi reatribuída ao tenant que mandou a confirmação")


class TestOComportamentoNaoMudaPorTenant:
    """A fronteira desta fase: propagação e atribuição, não regra de negócio."""

    def test_dois_tenants_recebem_a_mesma_decisao(self, cliente):
        decisoes = {}
        for tenant in ("empresa_1", "empresa_2"):
            # Mesmo `id_recorrencia` base para os dois: o perfil sintético é
            # semeado pelo customer_id, então ids diferentes dariam features
            # diferentes e o teste mediria o gerador, não o tenant.
            rec = "RN_s4_mesmo_perfil"
            crai_agent.update_state({"configurable": {"thread_id": rec}},
                                    {"retry_count": 0, "pix_janela_ate": None})
            corpo = _corpo(rec, f"E_{tenant}_{rec}")
            cliente.post("/webhooks/pix-automatico", content=corpo,
                         headers={**_assinar(corpo), "x-tenant-id": tenant})
            estado = _estado(rec)
            decisoes[tenant] = (estado["estrategia"], estado["failure_cause"],
                                estado["recovery_score"])

        assert decisoes["empresa_1"] == decisoes["empresa_2"], (
            f"a recuperação mudou entre tenants: {decisoes}. Nesta fase o "
            "tenant é atribuição, não regra — decisão por tenant é RBAC/produto")


class TestCartaoDesativadoTambemEAtribuido:

    def test_o_registro_de_cartao_carrega_o_tenant(self, cliente, monkeypatch):
        """A falha de cartão é evento de negócio de alguém, mesmo sem recobrança."""
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "stripe_s3cr3t")
        corpo = json.dumps({
            "type": "invoice.payment_failed",
            "data": {"object": {"id": "inv_1", "customer": "cus_1",
                                "amount_due": 29990, "attempt_count": 1}},
        }).encode()
        ts = int(time.time())
        mac = hmac.new(b"stripe_s3cr3t", f"{ts}.".encode() + corpo,
                       hashlib.sha256).hexdigest()

        registrados = []
        original = app_module._registrar_cartao_desativado
        monkeypatch.setattr(app_module, "_registrar_cartao_desativado",
                            lambda e, t=TENANT_PADRAO: registrados.append(original(e, t)))

        resposta = cliente.post("/webhooks/stripe", content=corpo, headers={
            "stripe-signature": f"t={ts},v1={mac}", "x-tenant-id": "empresa_cartao",
        })

        assert resposta.status_code == 200, resposta.text
        assert registrados and registrados[0]["tenant_id"] == "empresa_cartao"


def test_o_balde_padrao_e_o_mesmo_dos_dois_churns():
    """Dois literais para o mesmo conceito divergem no dia em que um muda.

    `retry_state.TENANT_PADRAO` espelha o do churn voluntário sem importá-lo —
    um módulo de dunning não deve depender do pacote do outro churn por uma
    constante. O que impede a divergência é esta asserção.
    """
    assert retry_state.TENANT_PADRAO == TENANT_PADRAO_VOLUNTARIO == TENANT_PADRAO

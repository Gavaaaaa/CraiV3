"""Testes da Fase 1: verificação de assinatura dos webhooks.

    pytest tests/test_webhook_security.py -q

Cobre os três verificadores (Stripe, Segment, Pix Automático), os endpoints
que os consomem e a restrição de ambiente dos endpoints /simulate/*.
Os pipelines são stubados: aqui só interessa quem passa da porta de entrada.
"""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.security.webhook_verification import (
    REPLAY_TOLERANCE_SECONDS,
    verify_pix_automatico_signature,
    verify_segment_signature,
    verify_stripe_signature,
)

STRIPE_SECRET = "whsec_teste_fase1"
SEGMENT_SECRET = "segment_shared_secret_teste"
PIX_SECRET = "pix_secret_teste"

STRIPE_PAYLOAD = b'{"id":"evt_1","type":"invoice.payment_failed","data":{"object":{"id":"inv_1","customer":"cus_1","amount_due":29990}}}'
SEGMENT_PAYLOAD = b'{"userId":"usr_1","event":"Cancellation Page Viewed","properties":{}}'


def stripe_header(payload: bytes, secret: str = STRIPE_SECRET, timestamp: int = None) -> str:
    """Monta um header 'stripe-signature' válido para o payload."""
    timestamp = int(time.time()) if timestamp is None else timestamp
    assinatura = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload,
                          hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={assinatura}"


def segment_header(payload: bytes, secret: str = SEGMENT_SECRET) -> str:
    """Monta um header 'x-signature' válido para o payload."""
    return hmac.new(secret.encode(), payload, hashlib.sha1).hexdigest()


@pytest.fixture
def client(monkeypatch):
    """Client com os dois secrets configurados e pipelines stubados."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", STRIPE_SECRET)
    monkeypatch.setenv("SEGMENT_WEBHOOK_SECRET", SEGMENT_SECRET)

    chamadas = []

    async def fake_involuntary(event, payment_method="card", **kwargs):
        chamadas.append(("involuntary", payment_method))

    async def fake_voluntary(user_id, event, props):
        chamadas.append(("voluntary", user_id))

    monkeypatch.setattr(app_module, "_run_involuntary_pipeline", fake_involuntary)
    monkeypatch.setattr(app_module, "_run_voluntary_pipeline", fake_voluntary)

    with TestClient(app_module.app) as c:
        c.pipeline_calls = chamadas
        yield c


# ══════════════════════════════════════════════════════════════════════════
# STRIPE
# ══════════════════════════════════════════════════════════════════════════

class TestStripeWebhook:
    def test_assinatura_valida_aceita(self, client):
        r = client.post("/webhooks/stripe", content=STRIPE_PAYLOAD,
                        headers={"stripe-signature": stripe_header(STRIPE_PAYLOAD)})
        assert r.status_code == 200

    def test_assinatura_valida_registra_sem_recobrar(self, client):
        """Fase 3: cartão passa pela porta, mas não entra no pipeline.

        A recobrança automática de cartão saiu do fluxo ativo — o evento é
        registrado com [CARTAO-DESATIVADO]. Ver tests/test_payment_isolation.py.
        """
        r = client.post("/webhooks/stripe", content=STRIPE_PAYLOAD,
                        headers={"stripe-signature": stripe_header(STRIPE_PAYLOAD)})
        assert r.status_code == 200
        assert r.json()["pipeline"] is False
        assert client.pipeline_calls == []

    def test_assinatura_invalida_rejeita_401(self, client):
        r = client.post("/webhooks/stripe", content=STRIPE_PAYLOAD,
                        headers={"stripe-signature": f"t={int(time.time())},v1=deadbeef"})
        assert r.status_code == 401
        assert client.pipeline_calls == []

    def test_assinatura_ausente_rejeita_401(self, client):
        r = client.post("/webhooks/stripe", content=STRIPE_PAYLOAD)
        assert r.status_code == 401
        assert client.pipeline_calls == []

    def test_payload_adulterado_rejeita_401(self, client):
        """Assinatura legítima de outro payload não vale para este."""
        header = stripe_header(STRIPE_PAYLOAD)
        r = client.post("/webhooks/stripe", content=b'{"type":"invoice.payment_failed"}',
                        headers={"stripe-signature": header})
        assert r.status_code == 401
        assert client.pipeline_calls == []

    def test_replay_com_timestamp_expirado_rejeita(self, client):
        antigo = int(time.time()) - (REPLAY_TOLERANCE_SECONDS + 60)
        r = client.post("/webhooks/stripe", content=STRIPE_PAYLOAD,
                        headers={"stripe-signature": stripe_header(STRIPE_PAYLOAD, timestamp=antigo)})
        assert r.status_code == 401
        assert client.pipeline_calls == []

    def test_secret_ausente_rejeita(self, client, monkeypatch):
        """Fail closed: sem STRIPE_WEBHOOK_SECRET nenhum request passa."""
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        r = client.post("/webhooks/stripe", content=STRIPE_PAYLOAD,
                        headers={"stripe-signature": stripe_header(STRIPE_PAYLOAD)})
        assert r.status_code == 401


# ══════════════════════════════════════════════════════════════════════════
# SEGMENT
# ══════════════════════════════════════════════════════════════════════════

class TestSegmentWebhook:
    def test_assinatura_valida_aceita(self, client):
        r = client.post("/webhooks/segment", content=SEGMENT_PAYLOAD,
                        headers={"x-signature": segment_header(SEGMENT_PAYLOAD)})
        assert r.status_code == 200
        assert [c[0] for c in client.pipeline_calls] == ["voluntary"]

    def test_assinatura_invalida_rejeita_401(self, client):
        r = client.post("/webhooks/segment", content=SEGMENT_PAYLOAD,
                        headers={"x-signature": "a" * 40})
        assert r.status_code == 401
        assert client.pipeline_calls == []

    def test_assinatura_ausente_rejeita_401(self, client):
        r = client.post("/webhooks/segment", content=SEGMENT_PAYLOAD)
        assert r.status_code == 401
        assert client.pipeline_calls == []

    def test_payload_adulterado_rejeita_401(self, client):
        header = segment_header(SEGMENT_PAYLOAD)
        r = client.post("/webhooks/segment", content=b'{"userId":"usr_invasor","event":"x"}',
                        headers={"x-signature": header})
        assert r.status_code == 401
        assert client.pipeline_calls == []

    def test_secret_ausente_rejeita(self, client, monkeypatch):
        monkeypatch.delenv("SEGMENT_WEBHOOK_SECRET", raising=False)
        r = client.post("/webhooks/segment", content=SEGMENT_PAYLOAD,
                        headers={"x-signature": segment_header(SEGMENT_PAYLOAD)})
        assert r.status_code == 401


# ══════════════════════════════════════════════════════════════════════════
# VERIFICADORES (unitário, sem HTTP)
# ══════════════════════════════════════════════════════════════════════════

class TestVerificadores:
    def test_stripe_valido(self):
        assert verify_stripe_signature(
            STRIPE_PAYLOAD, stripe_header(STRIPE_PAYLOAD), STRIPE_SECRET) is True

    def test_stripe_segredo_errado(self):
        assert verify_stripe_signature(
            STRIPE_PAYLOAD, stripe_header(STRIPE_PAYLOAD, secret="outro"), STRIPE_SECRET) is False

    def test_stripe_header_malformado(self):
        for header in ["", "sem_igual", "t=abc,v1=x", f"t={int(time.time())}"]:
            assert verify_stripe_signature(STRIPE_PAYLOAD, header, STRIPE_SECRET) is False

    def test_stripe_aceita_multiplas_assinaturas(self):
        """Rotação de secret: basta uma das v1 conferir."""
        ts = int(time.time())
        valida = stripe_header(STRIPE_PAYLOAD, timestamp=ts).split("v1=")[1]
        assert verify_stripe_signature(
            STRIPE_PAYLOAD, f"t={ts},v1=deadbeef,v1={valida}", STRIPE_SECRET) is True

    def test_segment_valido(self):
        assert verify_segment_signature(
            SEGMENT_PAYLOAD, segment_header(SEGMENT_PAYLOAD), SEGMENT_SECRET) is True

    def test_segment_segredo_errado(self):
        assert verify_segment_signature(
            SEGMENT_PAYLOAD, segment_header(SEGMENT_PAYLOAD, secret="outro"), SEGMENT_SECRET) is False

    def test_pix_automatico_valido(self):
        """Verificador pronto para o webhook que a Fase 3 vai criar."""
        header = stripe_header(b'{"pix":"cobv"}', secret=PIX_SECRET)
        assert verify_pix_automatico_signature(b'{"pix":"cobv"}', header, PIX_SECRET) is True

    def test_pix_automatico_replay_rejeitado(self):
        antigo = int(time.time()) - (REPLAY_TOLERANCE_SECONDS + 1)
        header = stripe_header(b'{"pix":"cobv"}', secret=PIX_SECRET, timestamp=antigo)
        assert verify_pix_automatico_signature(b'{"pix":"cobv"}', header, PIX_SECRET) is False

    def test_pix_automatico_segredo_errado(self):
        header = stripe_header(b'{"pix":"cobv"}', secret="outro")
        assert verify_pix_automatico_signature(b'{"pix":"cobv"}', header, PIX_SECRET) is False

    def test_nenhum_verificador_passa_sem_segredo(self):
        assert verify_stripe_signature(STRIPE_PAYLOAD, stripe_header(STRIPE_PAYLOAD), "") is False
        assert verify_segment_signature(SEGMENT_PAYLOAD, segment_header(SEGMENT_PAYLOAD), "") is False
        assert verify_pix_automatico_signature(b"{}", stripe_header(b"{}"), "") is False


# ══════════════════════════════════════════════════════════════════════════
# ENDPOINTS /simulate/* — restritos por ambiente
# ══════════════════════════════════════════════════════════════════════════

class TestSimulateEnvGate:
    def test_liberado_em_development(self, client, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        r = client.post("/simulate/payment-failed", json={"customer_id": "cus_dev"})
        assert r.status_code == 200
        # Cartão só é registrado desde a Fase 3 — o gate de ambiente é o que
        # está sob teste aqui, não o pipeline.
        assert r.json()["status"] == "registrado"

    def test_liberado_em_demo(self, client, monkeypatch):
        monkeypatch.setenv("ENV", "demo")
        r = client.post("/simulate/churn-risk", json={"user_id": "usr_demo"})
        assert r.status_code == 200

    def test_bloqueado_em_producao(self, client, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        assert client.post("/simulate/payment-failed", json={}).status_code == 403
        assert client.post("/simulate/churn-risk", json={}).status_code == 403
        assert client.pipeline_calls == []

    def test_bloqueado_sem_env_definida(self, client, monkeypatch):
        """Fail closed: esquecer de definir ENV não expõe o endpoint."""
        monkeypatch.delenv("ENV", raising=False)
        assert client.post("/simulate/payment-failed", json={}).status_code == 403
        assert client.pipeline_calls == []


def test_health_continua_publico(client):
    assert client.get("/health").status_code == 200

class TestA1R5ValidacaoNuncaVira500:
    """Entrada hostil nos `/simulate/*` devolve 422, nunca 5xx.

    O projeto já fechava esta porta em dois pontos: o webhook de Pix recusa
    `NaN`/`Infinity` no `json.loads` (`parse_constant`), e os campos de valor
    e de contagem passam por `_valor_de_simulacao` / `_contador_de_simulacao`.

    Faltava o caso em que **o Pydantic recusa antes de a rota rodar**. Um
    `NaN` num campo declarado `int` (`days_since_last`) nunca alcança o corpo
    da função — e o handler padrão do FastAPI monta um 422 que ECOA o valor
    ofensor. Aí o `NaN` está no corpo da RESPOSTA, e o Starlette escreve as
    respostas com `allow_nan=False`: a serialização levanta `ValueError` já
    fora de qualquer `try`, e o cliente recebe 500. O serviço tinha recusado a
    entrada corretamente e ainda assim reportou erro interno.

    Medido em `317a0eb`: `days_since_last` com `NaN`, `Infinity` e
    `-Infinity` devolvia HTTP 500 "Internal Server Error" nos três.
    """

    LITERAIS = ("NaN", "Infinity", "-Infinity")

    @staticmethod
    def _corpo(literal: str) -> bytes:
        return (
            '{"user_id":"usr_r5","event":"Session Started",'
            f'"days_since_last":{literal},"features_used_30d":2,'
            '"on_site_now":true,"billing_profile":"CLT"}'
        ).encode()

    @staticmethod
    def _recusar_constante(nome):
        raise AssertionError(
            f"a resposta traz o literal `{nome}` cru, que não existe no JSON "
            "padrão. É exatamente esse valor no corpo da RESPOSTA que fazia o "
            "Starlette levantar ValueError e devolver 500"
        )

    @pytest.mark.parametrize("literal", LITERAIS)
    def test_nao_ha_5xx_em_literal_nao_json(self, client, monkeypatch, literal):
        monkeypatch.setenv("ENV", "demo")
        resposta = client.post(
            "/simulate/churn-risk", content=self._corpo(literal),
            headers={"content-type": "application/json"},
        )

        assert resposta.status_code < 500, (
            f"`days_since_last: {literal}` devolveu HTTP "
            f"{resposta.status_code}. Entrada recusada tem que sair como 4xx: "
            "um 5xx aqui diz ao cliente que o erro é do servidor, e some com o "
            "motivo real da recusa"
        )
        assert resposta.status_code == 422, (
            f"esperado 422 (entidade não processável), recebido "
            f"{resposta.status_code}: {resposta.text[:200]}"
        )

    @pytest.mark.parametrize("literal", LITERAIS)
    def test_a_recusa_sai_como_json_estrito(self, client, monkeypatch, literal):
        """O 500 nascia na serialização — a prova é o corpo ser JSON válido.

        `json.loads` aceita `NaN`/`Infinity` por default; com `parse_constant`
        ele passa a recusá-los, que é o comportamento do JSON padrão e o do
        Starlette ao escrever a resposta.
        """
        monkeypatch.setenv("ENV", "demo")
        resposta = client.post(
            "/simulate/churn-risk", content=self._corpo(literal),
            headers={"content-type": "application/json"},
        )

        corpo = json.loads(resposta.text, parse_constant=self._recusar_constante)
        assert "detail" in corpo, f"resposta sem motivo da recusa: {corpo!r}"

    def test_o_portao_de_contadores_continua_de_pe(self, client, monkeypatch):
        """Contrapeso: sanear o eco não pode ter afrouxado a validação real.

        Catraca, não regressão — este caso já devolvia 422 em `317a0eb`. Está
        aqui porque a correção mexe no caminho de erro de TODAS as rotas, e um
        handler amplo demais poderia ter passado a aceitar o que os portões do
        projeto recusam. O inteiro de 401 dígitos atravessa o Pydantic intacto
        (um `int` Python não tem teto) e só é barrado por
        `_contador_de_simulacao`.
        """
        monkeypatch.setenv("ENV", "demo")
        gigante = "9" * 401
        resposta = client.post(
            "/simulate/churn-risk",
            content=(
                '{"user_id":"usr_r5","event":"Session Started",'
                f'"days_since_last":{gigante},"features_used_30d":2,'
                '"on_site_now":true,"billing_profile":"CLT"}'
            ).encode(),
            headers={"content-type": "application/json"},
        )

        assert resposta.status_code == 422, resposta.text
        assert resposta.json()["detail"]["campo"] == "days_since_last"

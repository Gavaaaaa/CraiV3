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

class TestA1R6BordaAssinadaNaoDerrubaAApi:
    """Assinar prova ORIGEM, não FORMA — e a borda tratava as duas como a mesma.

    Os webhooks de Stripe e Segment faziam `json.loads(raw)` seguido de `.get()`
    e entregavam o resultado ao pipeline. `json.loads(b'[]')` devolve uma lista,
    que não tem `.get`; `{"userId": [1]}` é JSON perfeito e vira chave de
    dicionário três camadas adiante; `{"amount_due": null}` entra numa divisão.
    Um PSP legítimo com bug de serialização assina um corpo torto, e a
    assinatura confere.

    Medido em `8786d82`, com assinatura VÁLIDA e o pipeline REAL (sem stub):
    **11 payloads davam HTTP 500 em `/webhooks/segment` e 5 em
    `/webhooks/stripe`** — 16 no total. Cinco dos sete tracebacks distintos
    morriam dentro de `crai/api/app.py`, ou seja, na própria borda.

    O objetivo declarado do Sprint 1 é textual: *"nenhum payload de PSP, por
    mais torto que seja, derruba a API ou entra no pipeline em silêncio."*

    Este teste usa cliente PRÓPRIO, sem a fixture `client`: aquela fixture
    substitui os dois pipelines por stubs, e vários destes payloads só estouram
    dentro do pipeline de verdade. Com stub, o teste passaria em `8786d82` e
    não provaria nada.
    """

    SEGMENT_NAO_OBJETO = [b"not json", b"[]", b'"texto"', b"5", b"null"]
    SEGMENT_FORMA_ERRADA = [
        b'{"userId":null}',
        b'{"userId":[1],"event":"x"}',
        b'{"userId":{"a":1},"event":"Session Started"}',
        b'{"userId":"u","event":"x","properties":"nao-dict"}',
        b'{"userId":"u","event":"x","properties":[1,2]}',
        b'{"userId":"u","event":"Cancellation Page Viewed",'
        b'"properties":{"billing_profile":[1]}}',
    ]
    STRIPE_NAO_OBJETO = [b"not json", b"[]"]
    STRIPE_FORMA_ERRADA = [
        b'{"type":"invoice.payment_failed","data":"nao-dict"}',
        b'{"type":"invoice.payment_failed","data":null}',
        b'{"type":"invoice.payment_failed","data":{"object":{"amount_due":"abc"}}}',
        b'{"type":"invoice.payment_failed","data":{"object":{"amount_due":null}}}',
    ]

    @pytest.fixture
    def cliente_real(self, monkeypatch):
        """Cliente com os segredos configurados e os pipelines DE VERDADE."""
        monkeypatch.setenv("SEGMENT_WEBHOOK_SECRET", SEGMENT_SECRET)
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", STRIPE_SECRET)
        with TestClient(app_module.app, raise_server_exceptions=False) as c:
            yield c

    @staticmethod
    def _h_segment(corpo: bytes) -> dict:
        return {"x-signature": segment_header(corpo),
                "content-type": "application/json"}

    @staticmethod
    def _h_stripe(corpo: bytes) -> dict:
        return {"stripe-signature": stripe_header(corpo),
                "content-type": "application/json"}

    @pytest.mark.parametrize("corpo", SEGMENT_NAO_OBJETO)
    def test_segment_corpo_que_nao_e_objeto_json_da_400(self, cliente_real, corpo):
        r = cliente_real.post("/webhooks/segment", content=corpo,
                              headers=self._h_segment(corpo))
        assert r.status_code == 400, (
            f"{corpo!r} devolveu {r.status_code}. Corpo assinado que não é um "
            "objeto JSON é recusa de forma, não erro do servidor"
        )

    @pytest.mark.parametrize("corpo", SEGMENT_FORMA_ERRADA)
    def test_segment_campo_com_forma_errada_da_422(self, cliente_real, corpo):
        r = cliente_real.post("/webhooks/segment", content=corpo,
                              headers=self._h_segment(corpo))
        assert r.status_code == 422, (
            f"{corpo!r} devolveu {r.status_code}. É JSON bem-formado com um "
            "campo na forma errada — o pipeline assume str/dict e recebe outra "
            "coisa; recusar na borda é o que impede o 500 lá dentro"
        )

    @pytest.mark.parametrize("corpo", STRIPE_NAO_OBJETO)
    def test_stripe_corpo_que_nao_e_objeto_json_da_400(self, cliente_real, corpo):
        r = cliente_real.post("/webhooks/stripe", content=corpo,
                              headers=self._h_stripe(corpo))
        assert r.status_code == 400, f"{corpo!r} devolveu {r.status_code}"

    @pytest.mark.parametrize("corpo", STRIPE_FORMA_ERRADA)
    def test_stripe_campo_com_forma_errada_da_422(self, cliente_real, corpo):
        r = cliente_real.post("/webhooks/stripe", content=corpo,
                              headers=self._h_stripe(corpo))
        assert r.status_code == 422, f"{corpo!r} devolveu {r.status_code}"

    def test_inteiro_grande_demais_para_o_checkpoint_da_422(self, cliente_real):
        """2**200 é JSON válido e nome de campo certo — e derrubava a API.

        O limite não é do JSON nem do Pydantic: é do msgpack que serializa o
        checkpoint do LangGraph, que endereça 64 bits. O erro aparecia só na
        gravação do estado, depois de o pipeline ter rodado inteiro.
        """
        corpo = json.dumps({
            "userId": "usr_r6", "event": "Cancellation Page Viewed",
            "properties": {"days_since_last": 2 ** 200},
        }).encode()
        r = cliente_real.post("/webhooks/segment", content=corpo,
                              headers=self._h_segment(corpo))

        assert r.status_code == 422, (
            f"inteiro de 2**200 em properties devolveu {r.status_code}: "
            f"{r.text[:200]}"
        )
        assert r.json()["detail"]["motivo"] == "inteiro_fora_do_alcance"

    def test_nenhum_payload_torto_produz_5xx(self, cliente_real):
        """A asserção do Sprint 1, medida de uma vez sobre a classe inteira."""
        casos = [("/webhooks/segment", c, self._h_segment)
                 for c in self.SEGMENT_NAO_OBJETO + self.SEGMENT_FORMA_ERRADA]
        casos += [("/webhooks/stripe", c, self._h_stripe)
                  for c in self.STRIPE_NAO_OBJETO + self.STRIPE_FORMA_ERRADA]

        quintos = []
        for rota, corpo, assinar in casos:
            r = cliente_real.post(rota, content=corpo, headers=assinar(corpo))
            if r.status_code >= 500:
                quintos.append((rota, corpo, r.status_code))

        assert not quintos, (
            f"{len(quintos)} de {len(casos)} payloads ASSINADOS derrubaram a "
            f"API com 5xx: {quintos}"
        )

    def test_payload_bem_formado_continua_passando(self, cliente_real):
        """Contrapeso: a blindagem não pode ter fechado a porta legítima.

        Catraca, não regressão — este caso já passava em `8786d82`. Existe
        porque um portão de forma cedo demais é fácil de escrever apertado
        demais, e aí o webhook real para de funcionar sem ninguém notar.
        """
        corpo = json.dumps({
            "userId": "usr_r6_ok", "event": "Cancellation Page Viewed",
            "properties": {"days_since_last": 14, "features_used_30d": 2,
                           "on_site_now": True, "billing_profile": "CLT"},
        }).encode()
        r = cliente_real.post("/webhooks/segment", content=corpo,
                              headers=self._h_segment(corpo))
        assert r.status_code == 200, r.text

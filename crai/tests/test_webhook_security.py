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
from crai.integrations.payment_gateway import MOTIVO_SEM_IDENTIFICACAO
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
    **11 payloads davam HTTP 500 em `/webhooks/segment` e 6 em
    `/webhooks/stripe`** — 17 no total, que é o número que
    `test_nenhum_payload_torto_produz_5xx` imprime ao reprovar naquele commit.
    (A auditoria A1-r7 corrigiu esta contagem: a versão anterior desta
    docstring dizia 5 e 16, desmentida pela saída do teste ao lado.)

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

class TestA1R7AGuardaNaoPodeSerODefeito:
    """A blindagem da r6 tinha três buracos, dois deles nela mesma.

    A rodada 6 fechou 17 payloads assinados que davam 500. A rodada 7 mostrou
    que a própria guarda era atravessável:

    1. `_recusar_inteiro_grande_demais` só era chamada no Segment. No Stripe,
       `amount_due: 2**2000` passava pela checagem de TIPO (é `int`) e
       estourava em `invoice.get("amount_due", 0) / 100` com `OverflowError`
       — HTTP 500, num campo que a linha `N-15` do README declarava blindado.
       Mesma classe de defeito fechada num webhook e aberta no outro.
    2. A guarda era RECURSIVA sobre um objeto livre vindo do PSP. Um
       `properties` com ~950 níveis de aninhamento derrubava a própria guarda
       com `RecursionError` — 500. A guarda virava o vetor.
    3. `userId` virou obrigatório e passou a recusar com 422 um evento
       legítimo do Segment: o de visitante ainda não identificado, que chega
       só com `anonymousId`.

    Medido em `c7c6870`: 500, 500 e 422 respectivamente.
    """

    @pytest.fixture
    def cliente_real(self, monkeypatch):
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

    def test_inteiro_gigante_no_stripe_nao_derruba_a_conta(self, cliente_real):
        """`amount_due` entra numa divisão: o teto não é do JSON, é do float."""
        corpo = json.dumps({
            "type": "invoice.payment_failed",
            "data": {"object": {"amount_due": 2 ** 2000}},
        }).encode()
        r = cliente_real.post("/webhooks/stripe", content=corpo,
                              headers=self._h_stripe(corpo))

        assert r.status_code == 422, (
            f"`amount_due: 2**2000` devolveu {r.status_code}. O inteiro passa "
            "pela checagem de tipo (é int) e estoura na divisão por 100 com "
            "OverflowError — a guarda de magnitude precisa valer nos DOIS "
            "webhooks, não só no Segment"
        )
        assert r.json()["detail"]["motivo"] == "inteiro_fora_do_alcance"

    def test_inteiro_gigante_aninhado_no_stripe_tambem_e_recusado(self, cliente_real):
        """`data` é objeto livre: o número pode não estar em `amount_due`."""
        corpo = json.dumps({
            "type": "invoice.payment_failed",
            "data": {"object": {"metadata": {"seq": 2 ** 300}}},
        }).encode()
        r = cliente_real.post("/webhooks/stripe", content=corpo,
                              headers=self._h_stripe(corpo))
        assert r.status_code == 422, r.text

    @pytest.mark.parametrize("niveis", [1200, 5000])
    def test_payload_aninhado_demais_nao_derruba_a_propria_guarda(
            self, cliente_real, niveis):
        """A guarda não pode ser derrubável pelo que ela inspeciona.

        O corpo é montado como TEXTO, sem `json.dumps`, porque construir a
        estrutura em Python já estouraria a pilha do lado do teste — e o que
        se mede aqui é o lado do servidor.
        """
        corpo = ('{"userId":"u","event":"Session Started","properties":'
                 + '{"p":' * niveis + '{}' + '}' * niveis + '}').encode()
        r = cliente_real.post("/webhooks/segment", content=corpo,
                              headers=self._h_segment(corpo))

        assert r.status_code < 500, (
            f"{niveis} níveis de aninhamento devolveram {r.status_code}. Uma "
            "guarda recursiva sobre objeto livre do PSP é derrubável pelo "
            "próprio payload: RecursionError vira 500"
        )
        assert r.status_code in (400, 422), r.text

    def test_evento_do_segment_com_anonymous_id_e_aceito(self, cliente_real):
        """Visitante não identificado é evento legítimo, não payload torto.

        `userId` e `anonymousId` são as duas identidades do protocolo do
        Segment. Exigir a primeira recusava com 422 quem ainda não se
        identificou — que é justamente o candidato a churn voluntário.
        """
        corpo = json.dumps({
            "anonymousId": "anon_r7_001", "event": "Cancellation Page Viewed",
            "properties": {"on_site_now": True, "billing_profile": "CLT"},
        }).encode()
        r = cliente_real.post("/webhooks/segment", content=corpo,
                              headers=self._h_segment(corpo))

        assert r.status_code == 200, (
            f"evento com `anonymousId` e sem `userId` devolveu {r.status_code}: "
            f"{r.text[:200]}"
        )

    def test_evento_sem_identidade_nenhuma_continua_recusado(self, cliente_real):
        """Contrapeso: aceitar `anonymousId` não pode abrir a porta do P0-6.

        Existe porque afrouxar a exigência de identidade é o erro natural ao
        corrigir o teste acima, e sem identidade clientes distintos dividem o
        mesmo checkpoint.

        Precisão sobre o que ele prova: em `c7c6870` o STATUS já era 422 — o
        que reprova lá é a asserção do MOTIVO, que passou de
        `campo_obrigatorio_ausente` (um campo faltando) para
        `evento_sem_identificacao` (nenhuma das duas identidades veio). A
        mudança é real e é o ponto: o motivo agora descreve a regra, não o
        campo. Mas não é o mesmo tipo de prova dos testes acima, e vale contar
        como tal.
        """
        corpo = json.dumps({"event": "Session Started", "properties": {}}).encode()
        r = cliente_real.post("/webhooks/segment", content=corpo,
                              headers=self._h_segment(corpo))

        assert r.status_code == 422, r.text
        assert r.json()["detail"]["motivo"] == MOTIVO_SEM_IDENTIFICACAO

class TestA1R8ACorrecaoTemQueAlcancarTodasAsRotas:
    """A rodada 7 consertou o aninhamento em dois webhooks, e havia quatro portas.

    O `except RecursionError` foi posto no portão comum `_objeto_json_do_corpo`,
    que Stripe e Segment usam. `/webhooks/pix-automatico` — a única rota do
    pipeline ATIVO — tinha um `json.loads` próprio, cópia do mesmo código sem
    aquele `except`. E os três `/simulate/*` estouravam num terceiro lugar: o
    handler de validação, que ecoa a estrutura recusada e recursa dentro do
    `jsonable_encoder`.

    Medido em `deb23be`:

        /webhooks/pix-automatico, 5000 níveis .......... 500
        /simulate/churn-risk,    2000 níveis em campo
                                 recusado pelo Pydantic  500
        /simulate/pix-falhado,   idem ................... 500
        /simulate/payment-failed, idem .................. 500

    É a terceira rodada seguida em que a mesma classe é fechada num lugar e
    deixada aberta em outro (r6: Segment sim, Stripe não; r7: guarda de inteiro
    só no Segment; r8: aninhamento só em dois dos quatro caminhos de parse).
    Por isso o último teste desta classe varre TODAS as rotas de uma vez, em
    vez de uma por uma: o defeito não é o payload, é o inventário de portas.
    """

    NIVEIS = (900, 2000, 5000, 20000)

    @pytest.fixture
    def cliente_real(self, monkeypatch):
        monkeypatch.setenv("SEGMENT_WEBHOOK_SECRET", SEGMENT_SECRET)
        monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", STRIPE_SECRET)
        monkeypatch.setenv("PIX_WEBHOOK_SECRET", "pix_r8")
        monkeypatch.setenv("ENV", "demo")
        with TestClient(app_module.app, raise_server_exceptions=False) as c:
            yield c

    @staticmethod
    def _aninhado(niveis: int) -> str:
        """Montado como TEXTO: construir em Python já estouraria o teste."""
        return '{"p":' * niveis + "{}" + "}" * niveis

    @staticmethod
    def _h_pix(corpo: bytes) -> dict:
        ts = int(time.time())
        mac = hmac.new(b"pix_r8", f"{ts}.".encode() + corpo,
                       hashlib.sha256).hexdigest()
        return {"x-pix-signature": f"t={ts},v1={mac}",
                "content-type": "application/json"}

    @pytest.mark.parametrize("niveis", NIVEIS)
    def test_pix_com_corpo_aninhado_nao_da_5xx(self, cliente_real, niveis):
        """A rota do pipeline ativo tinha a cópia do parse SEM a correção."""
        corpo = ('{"event":"automatic_pix.charge_failed","e2e_id":"E_r8",'
                 '"valor":299.90,"id_recorrencia":"RN_r8","extra":'
                 + self._aninhado(niveis) + "}").encode()
        r = cliente_real.post("/webhooks/pix-automatico", content=corpo,
                              headers=self._h_pix(corpo))

        assert r.status_code < 500, (
            f"{niveis} níveis num corpo ASSINADO de Pix devolveram "
            f"{r.status_code}. Esta é a única rota do pipeline ativo, e tinha "
            "um `json.loads` próprio — a correção do portão comum não chegava "
            "aqui"
        )

    @pytest.mark.parametrize("rota,base,campo", [
        ("/simulate/churn-risk", '"user_id":"u","event":"Session Started"',
         "days_since_last"),
        ("/simulate/pix-falhado", '"id_recorrencia":"R1"', "valor"),
        ("/simulate/payment-failed", '"customer_id":"c"', "amount"),
    ])
    @pytest.mark.parametrize("niveis", [900, 2000])
    def test_simulate_com_campo_recusado_e_aninhado_nao_da_5xx(
            self, cliente_real, rota, base, campo, niveis):
        """O aninhamento precisa estar no campo que o Pydantic RECUSA.

        É isso que faz o valor entrar no eco do erro: o handler que existe para
        impedir 500 recursava sobre a estrutura recusada e produzia o 500.
        """
        corpo = ("{" + base + f',"{campo}":' + self._aninhado(niveis)
                 + "}").encode()
        r = cliente_real.post(rota, content=corpo,
                              headers={"content-type": "application/json"})

        assert r.status_code < 500, (
            f"{rota} com {niveis} níveis em `{campo}` devolveu "
            f"{r.status_code}. O corpo foi corretamente recusado pelo Pydantic "
            "e o 500 nasceu no handler que deveria transformar essa recusa em "
            "422"
        )

    def test_nenhuma_rota_da_5xx_com_corpo_aninhado(self, cliente_real):
        """O inventário de portas, varrido de uma vez.

        Os testes acima cobrem os caminhos conhecidos. Este existe para o
        caminho que ninguém lembrou: se amanhã nascer uma quinta rota com o
        seu próprio parse, ela reprova aqui sem que alguém precise se lembrar
        de acrescentar um teste.
        """
        def h_segment(c):
            return {"x-signature": segment_header(c),
                    "content-type": "application/json"}

        def h_stripe(c):
            return {"stripe-signature": stripe_header(c),
                    "content-type": "application/json"}

        simples = {"content-type": "application/json"}
        casos = []
        for n in self.NIVEIS:
            aninhado = self._aninhado(n)
            casos += [
                ("/webhooks/pix-automatico",
                 ('{"event":"automatic_pix.charge_failed","e2e_id":"E","valor":'
                  '299.90,"id_recorrencia":"RN","x":' + aninhado + "}").encode(),
                 self._h_pix),
                ("/webhooks/segment",
                 ('{"userId":"u","event":"Session Started","properties":'
                  + aninhado + "}").encode(), h_segment),
                ("/webhooks/stripe",
                 ('{"type":"invoice.payment_failed","data":'
                  + aninhado + "}").encode(), h_stripe),
                ("/simulate/churn-risk",
                 ('{"user_id":"u","event":"Session Started","days_since_last":'
                  + aninhado + "}").encode(), lambda _: simples),
                ("/simulate/pix-falhado",
                 ('{"id_recorrencia":"R1","valor":' + aninhado + "}").encode(),
                 lambda _: simples),
                ("/simulate/payment-failed",
                 ('{"customer_id":"c","amount":' + aninhado + "}").encode(),
                 lambda _: simples),
            ]

        quintos = []
        for rota, corpo, assinar in casos:
            r = cliente_real.post(rota, content=corpo, headers=assinar(corpo))
            if r.status_code >= 500:
                quintos.append((rota, len(corpo), r.status_code))

        assert not quintos, (
            f"{len(quintos)} de {len(casos)} corpos aninhados derrubaram a API: "
            f"{quintos}"
        )


class TestA1R8IdentidadeDoSegmentNaoColide:
    """`userId` e `anonymousId` são espaços de nome diferentes.

    Os dois ids são atribuídos por sistemas diferentes — o backend do cliente e
    o SDK do navegador — e nada garante que não coincidam. Ao aceitar
    `anonymousId` como identidade (correção da r7), o valor passou a entrar no
    `thread_id` sem prefixo: um visitante anônimo cujo id calhasse de ser igual
    ao `userId` de um cliente identificado herdava o checkpoint dele.

    Medido em `deb23be`, dois eventos com o mesmo id em campos diferentes:

        userId='colisao_r8'      -> perfil=CLT evento='Cancellation Page Viewed'
        anonymousId='colisao_r8' -> perfil=PJ  evento='Session Started'
        MESMO checkpoint: o segundo SOBRESCREVEU o estado do primeiro

    É o P0-6 por outra porta: o `thread_id` precisa ser único por cliente, não
    apenas não-vazio.
    """

    @pytest.fixture
    def cliente_real(self, monkeypatch):
        monkeypatch.setenv("SEGMENT_WEBHOOK_SECRET", SEGMENT_SECRET)
        with TestClient(app_module.app, raise_server_exceptions=False) as c:
            yield c

    @staticmethod
    def _enviar(cliente, campo, valor, evento, perfil):
        corpo = json.dumps({
            campo: valor, "event": evento,
            "properties": {"on_site_now": True, "billing_profile": perfil},
        }).encode()
        r = cliente.post("/webhooks/segment", content=corpo,
                         headers={"x-signature": segment_header(corpo),
                                  "content-type": "application/json"})
        assert r.status_code == 200, r.text

    def test_id_anonimo_nao_herda_o_checkpoint_do_identificado(self, cliente_real):
        from crai.churn_voluntary.voluntary_agent import voluntary_churn_agent

        colisao = "colisao_r8_teste"
        self._enviar(cliente_real, "userId", colisao,
                     "Cancellation Page Viewed", "CLT")
        self._enviar(cliente_real, "anonymousId", colisao,
                     "Session Started", "PJ")

        identificado = voluntary_churn_agent.get_state(
            {"configurable": {"thread_id": colisao}}).values

        assert identificado.get("event") == "Cancellation Page Viewed", (
            "o evento anônimo sobrescreveu o checkpoint do cliente "
            f"identificado: {identificado.get('event')!r}. Os dois ids vêm de "
            "sistemas diferentes e podem coincidir — sem prefixo, coincidir "
            "significa dividir o mesmo estado"
        )

    def test_o_evento_anonimo_tem_checkpoint_proprio(self, cliente_real):
        """A outra metade: isolar não pode virar descartar."""
        from crai.churn_voluntary.voluntary_agent import voluntary_churn_agent

        anonimo = "so_anonimo_r8"
        self._enviar(cliente_real, "anonymousId", anonimo,
                     "Cancellation Page Viewed", "PJ")

        estado = voluntary_churn_agent.get_state(
            {"configurable": {"thread_id": f"anon:{anonimo}"}}).values
        assert estado.get("event") == "Cancellation Page Viewed", (
            f"o evento anônimo não gravou estado em `anon:{anonimo}`: {estado}"
        )

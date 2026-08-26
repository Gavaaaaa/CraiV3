"""
tests/test_payment_gateway.py — Testes do adapter de PSP (Fase 3).

Cobre:
- Normalização dos 4 eventos de Pix Automático
- Garantia de que a chave Pix NUNCA aparece em texto puro no schema normalizado
- parse_card_event levanta NotImplementedError (cartão fora de escopo na fase)
- /webhooks/pix-automatico rejeita request sem assinatura válida
- Cifragem da chave Pix para conciliação (fora do caminho do pipeline)
"""

import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.integrations import payment_gateway as gateway_module
from crai.integrations.payment_gateway import (
    CARD_NOT_IMPLEMENTED,
    STATUS_AUTORIZACAO_CONCEDIDA,
    STATUS_AUTORIZACAO_REVOGADA,
    STATUS_COBRANCA_CONFIRMADA,
    STATUS_COBRANCA_FALHADA,
    PaymentGatewayAdapter,
    PixAutomaticoAdapter,
)
from crai.security.tokenization import (
    EncryptionKeyMissing,
    decrypt_sensitive_field,
    generate_key,
)

PIX_SECRET = "pix_secret_teste_fase3"

# Chave Pix do pagador usada nos payloads. Nenhum teste pode encontrá-la
# no dicionário normalizado.
CHAVE_PIX_PAGADOR = "12345678901"

# Os 5 campos — e apenas eles — que o schema normalizado pode conter.
CAMPOS_NORMALIZADOS = {"e2e_id", "valor", "status", "ispb_pagador", "id_recorrencia"}


def payload_pix(evento: str, **overrides) -> dict:
    """Payload no formato do PSP (referência: objeto `automatic_pix` da Iugu)."""
    base = {
        "event": evento,
        "data": {
            "id": "inv_pix_001",
            "total_cents": 29990,
            "automatic_pix": {
                "journey": "JORNADA_1",
                "recurrence_id": "RN2026082600001",
                "authorization_status": "approved",
            },
            "pix": {
                "end_to_end_id": "E60701190202608261200abcdef123",
                "payer": {
                    "pix_key": CHAVE_PIX_PAGADOR,
                    "ispb": "60701190",
                    "name": "Fulano de Tal",
                },
            },
        },
    }
    base["data"].update(overrides)
    return base


def pix_header(payload: bytes, secret: str = PIX_SECRET, timestamp: int = None) -> str:
    """Monta um header 'x-pix-signature' válido (mesmo formato do Stripe)."""
    timestamp = int(time.time()) if timestamp is None else timestamp
    assinatura = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload,
                          hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={assinatura}"


@pytest.fixture
def adapter():
    return PixAutomaticoAdapter()


# ══════════════════════════════════════════════════════════════════════════
# NORMALIZAÇÃO DOS 4 EVENTOS
# ══════════════════════════════════════════════════════════════════════════

class TestNormalizacaoDeEventos:
    """Cada um dos 4 eventos vira o mesmo schema, com o status certo."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("evento, status_esperado", [
        ("automatic_pix.authorization_created", STATUS_AUTORIZACAO_CONCEDIDA),
        ("automatic_pix.authorization_revoked", STATUS_AUTORIZACAO_REVOGADA),
        ("automatic_pix.charge_succeeded",      STATUS_COBRANCA_CONFIRMADA),
        ("automatic_pix.charge_failed",         STATUS_COBRANCA_FALHADA),
    ])
    async def test_status_normalizado(self, adapter, evento, status_esperado):
        """Os 4 tipos de evento mapeiam para o status normalizado correto."""
        resultado = await adapter.parse_pix_event(payload_pix(evento))
        assert resultado["status"] == status_esperado

    @pytest.mark.asyncio
    async def test_schema_tem_exatamente_os_cinco_campos(self, adapter):
        """Nem mais, nem menos: o contrato com o pipeline é fechado."""
        resultado = await adapter.parse_pix_event(payload_pix("automatic_pix.charge_failed"))
        assert set(resultado.keys()) == CAMPOS_NORMALIZADOS

    @pytest.mark.asyncio
    async def test_campos_extraidos_corretamente(self, adapter):
        """e2e_id, valor, ISPB e id da recorrência saem do payload do PSP."""
        resultado = await adapter.parse_pix_event(payload_pix("automatic_pix.charge_failed"))
        assert resultado["e2e_id"] == "E60701190202608261200abcdef123"
        assert resultado["valor"] == 299.90          # total_cents / 100
        assert resultado["ispb_pagador"] == "60701190"
        assert resultado["id_recorrencia"] == "RN2026082600001"

    @pytest.mark.asyncio
    async def test_evento_desconhecido_nao_quebra(self, adapter):
        """PSP mandando evento fora do catálogo não derruba o parsing."""
        resultado = await adapter.parse_pix_event(payload_pix("automatic_pix.something_new"))
        assert set(resultado.keys()) == CAMPOS_NORMALIZADOS
        assert resultado["status"] not in (STATUS_COBRANCA_FALHADA,)

    @pytest.mark.asyncio
    async def test_valor_em_reais_quando_psp_manda_reais(self, adapter):
        """PSP alternativo que envia o valor já em reais também é aceito."""
        payload = payload_pix("automatic_pix.charge_failed")
        del payload["data"]["total_cents"]
        payload["data"]["valor"] = 149.5
        resultado = await adapter.parse_pix_event(payload)
        assert resultado["valor"] == 149.50


# ══════════════════════════════════════════════════════════════════════════
# PRIVACIDADE — A CHAVE PIX NÃO PODE VAZAR
# ══════════════════════════════════════════════════════════════════════════

class TestChavePixNuncaVazada:
    """O requisito central de privacidade da Fase 3."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("evento", [
        "automatic_pix.authorization_created",
        "automatic_pix.authorization_revoked",
        "automatic_pix.charge_succeeded",
        "automatic_pix.charge_failed",
    ])
    async def test_chave_pix_ausente_do_normalizado(self, adapter, evento):
        """Em nenhum dos 4 eventos a chave Pix aparece no dict normalizado."""
        resultado = await adapter.parse_pix_event(payload_pix(evento))
        serializado = json.dumps(resultado, ensure_ascii=False)
        assert CHAVE_PIX_PAGADOR not in serializado
        assert "pix_key" not in serializado

    @pytest.mark.asyncio
    async def test_nome_do_pagador_tambem_nao_vaza(self, adapter):
        """Só os 5 campos passam — nome do pagador também fica para trás."""
        resultado = await adapter.parse_pix_event(payload_pix("automatic_pix.charge_failed"))
        assert "Fulano de Tal" not in json.dumps(resultado, ensure_ascii=False)

    @pytest.mark.asyncio
    async def test_chave_pix_em_formato_alternativo_tambem_e_descartada(self, adapter):
        """PSP que use outro nome de campo para a chave também não vaza."""
        payload = payload_pix("automatic_pix.charge_failed")
        payload["data"]["pix"]["payer"] = {"chave_pix": CHAVE_PIX_PAGADOR, "ispb": "60701190"}
        resultado = await adapter.parse_pix_event(payload)
        assert CHAVE_PIX_PAGADOR not in json.dumps(resultado, ensure_ascii=False)


class TestArquivamentoCifrado:
    """store_encrypted_pix_key(): conciliação sem texto puro."""

    def test_chave_cifrada_e_recuperavel(self, adapter, monkeypatch, tmp_path):
        monkeypatch.setenv("CRAI_ENCRYPTION_KEY", generate_key())
        monkeypatch.setattr(gateway_module, "VAULT_PATH", tmp_path / "pix_keys.json")

        token = adapter.store_encrypted_pix_key(
            "RN2026082600001", payload_pix("automatic_pix.charge_failed"),
        )

        assert CHAVE_PIX_PAGADOR not in token
        assert decrypt_sensitive_field(token) == CHAVE_PIX_PAGADOR

    def test_cofre_nao_guarda_texto_puro(self, adapter, monkeypatch, tmp_path):
        cofre = tmp_path / "pix_keys.json"
        monkeypatch.setenv("CRAI_ENCRYPTION_KEY", generate_key())
        monkeypatch.setattr(gateway_module, "VAULT_PATH", cofre)

        adapter.store_encrypted_pix_key(
            "RN2026082600001", payload_pix("automatic_pix.charge_failed"),
        )

        assert CHAVE_PIX_PAGADOR not in cofre.read_text(encoding="utf-8")

    def test_sem_chave_de_criptografia_falha_alto(self, adapter, monkeypatch, tmp_path):
        """Fail closed: sem CRAI_ENCRYPTION_KEY, não persiste em texto puro."""
        monkeypatch.delenv("CRAI_ENCRYPTION_KEY", raising=False)
        monkeypatch.setattr(gateway_module, "VAULT_PATH", tmp_path / "pix_keys.json")

        with pytest.raises(EncryptionKeyMissing):
            adapter.store_encrypted_pix_key(
                "RN2026082600001", payload_pix("automatic_pix.charge_failed"),
            )

    def test_payload_sem_chave_pix_levanta_erro(self, adapter, monkeypatch, tmp_path):
        monkeypatch.setenv("CRAI_ENCRYPTION_KEY", generate_key())
        monkeypatch.setattr(gateway_module, "VAULT_PATH", tmp_path / "pix_keys.json")

        payload = payload_pix("automatic_pix.charge_failed")
        payload["data"]["pix"]["payer"] = {"ispb": "60701190"}

        with pytest.raises(ValueError, match="chave Pix"):
            adapter.store_encrypted_pix_key("RN2026082600001", payload)


# ══════════════════════════════════════════════════════════════════════════
# CARTÃO FORA DE ESCOPO NESTA FASE
# ══════════════════════════════════════════════════════════════════════════

class TestCartaoForaDeEscopo:
    """A interface está pronta para cartão, mas nada a implementa nesta fase."""

    def test_parse_card_event_levanta_not_implemented(self, adapter):
        with pytest.raises(NotImplementedError) as exc:
            adapter.parse_card_event({"qualquer": "coisa"})
        assert str(exc.value) == CARD_NOT_IMPLEMENTED

    def test_mensagem_menciona_roadmap_futuro(self, adapter):
        with pytest.raises(NotImplementedError) as exc:
            adapter.parse_card_event({})
        mensagem = str(exc.value).lower()
        assert "cartão" in mensagem
        assert "roadmap futuro" in mensagem

    def test_interface_declara_o_metodo(self):
        """A assinatura existe na ABC, para o adapter de cartão do futuro."""
        assert hasattr(PaymentGatewayAdapter, "parse_card_event")
        assert hasattr(PaymentGatewayAdapter, "parse_pix_event")

    def test_nenhuma_subclasse_desta_fase_implementa_cartao(self):
        """PixAutomaticoAdapter herda o stub — não sobrescreve com implementação."""
        assert (PixAutomaticoAdapter.parse_card_event
                is PaymentGatewayAdapter.parse_card_event)


# ══════════════════════════════════════════════════════════════════════════
# ENDPOINT /webhooks/pix-automatico
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def client(monkeypatch):
    """Client com o secret de Pix configurado e o pipeline stubado."""
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", PIX_SECRET)

    chamadas = []

    async def fake_involuntary(event, payment_method="card", **kwargs):
        chamadas.append((payment_method, event))

    monkeypatch.setattr(app_module, "_run_involuntary_pipeline", fake_involuntary)

    with TestClient(app_module.app) as c:
        c.pipeline_calls = chamadas
        yield c


class TestEndpointPixAutomatico:
    """A porta de entrada do Pix: sem assinatura válida, nada passa."""

    def test_assinatura_valida_aciona_pipeline(self, client):
        corpo = json.dumps(payload_pix("automatic_pix.charge_failed")).encode()
        r = client.post("/webhooks/pix-automatico", content=corpo,
                        headers={"x-pix-signature": pix_header(corpo)})

        assert r.status_code == 200
        assert r.json()["pipeline"] is True
        assert [c[0] for c in client.pipeline_calls] == ["pix_automatico"]

    def test_assinatura_ausente_rejeita_401(self, client):
        corpo = json.dumps(payload_pix("automatic_pix.charge_failed")).encode()
        r = client.post("/webhooks/pix-automatico", content=corpo)

        assert r.status_code == 401
        assert client.pipeline_calls == []

    def test_assinatura_invalida_rejeita_401(self, client):
        corpo = json.dumps(payload_pix("automatic_pix.charge_failed")).encode()
        r = client.post("/webhooks/pix-automatico", content=corpo,
                        headers={"x-pix-signature": f"t={int(time.time())},v1=deadbeef"})

        assert r.status_code == 401
        assert client.pipeline_calls == []

    def test_payload_adulterado_rejeita_401(self, client):
        corpo = json.dumps(payload_pix("automatic_pix.charge_failed")).encode()
        header = pix_header(corpo)
        adulterado = corpo.replace(b"29990", b"1")

        r = client.post("/webhooks/pix-automatico", content=adulterado,
                        headers={"x-pix-signature": header})

        assert r.status_code == 401
        assert client.pipeline_calls == []

    def test_secret_ausente_rejeita_401(self, client, monkeypatch):
        """Fail closed: sem PIX_WEBHOOK_SECRET no ambiente, tudo é rejeitado."""
        monkeypatch.delenv("PIX_WEBHOOK_SECRET", raising=False)
        corpo = json.dumps(payload_pix("automatic_pix.charge_failed")).encode()

        r = client.post("/webhooks/pix-automatico", content=corpo,
                        headers={"x-pix-signature": pix_header(corpo)})

        assert r.status_code == 401
        assert client.pipeline_calls == []

    @pytest.mark.parametrize("evento", [
        "automatic_pix.authorization_created",
        "automatic_pix.authorization_revoked",
        "automatic_pix.charge_succeeded",
    ])
    def test_evento_que_nao_e_falha_nao_aciona_pipeline(self, client, evento):
        """Só cobrança falhada dispara recuperação; os outros são registrados."""
        corpo = json.dumps(payload_pix(evento)).encode()
        r = client.post("/webhooks/pix-automatico", content=corpo,
                        headers={"x-pix-signature": pix_header(corpo)})

        assert r.status_code == 200
        assert r.json()["pipeline"] is False
        assert client.pipeline_calls == []

    def test_pipeline_recebe_evento_sem_chave_pix(self, client):
        """O que chega ao pipeline é o normalizado — a chave Pix ficou para trás."""
        corpo = json.dumps(payload_pix("automatic_pix.charge_failed")).encode()
        client.post("/webhooks/pix-automatico", content=corpo,
                    headers={"x-pix-signature": pix_header(corpo)})

        _, evento = client.pipeline_calls[0]
        assert set(evento.keys()) == CAMPOS_NORMALIZADOS
        assert CHAVE_PIX_PAGADOR not in json.dumps(evento, ensure_ascii=False)

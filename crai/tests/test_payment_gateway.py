"""
tests/test_payment_gateway.py — Testes do adapter de PSP (Fase 3).

Cobre:
- Normalização dos 4 eventos de Pix Automático
- Garantia de que a chave Pix NUNCA aparece em texto puro no schema normalizado
- parse_card_event levanta NotImplementedError (cartão fora de escopo na fase)
- /webhooks/pix-automatico rejeita request sem assinatura válida
- Cifragem da chave Pix para conciliação (fora do caminho do pipeline)
- Sprint 1: suíte de payloads hostis (P0-1 … P0-5)
"""

import hashlib
import hmac
import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.integrations import payment_gateway as gateway_module
from crai.integrations.payment_gateway import (
    CARD_NOT_IMPLEMENTED,
    DEGRADACAO_ENVELOPE_AUSENTE,
    DEGRADACAO_LOTE_DE_1,
    DEGRADACAO_STATUS_DESCONHECIDO,
    DEGRADACAO_VALOR_AUSENTE,
    DEGRADACAO_VALOR_ILEGIVEL,
    DEGRADACOES_CONHECIDAS,
    MOTIVO_LOTE_NAO_SUPORTADO,
    MOTIVO_PAYLOAD_NAO_E_OBJETO,
    STATUS_AUTORIZACAO_CONCEDIDA,
    STATUS_AUTORIZACAO_REVOGADA,
    STATUS_COBRANCA_CONFIRMADA,
    STATUS_COBRANCA_FALHADA,
    PayloadPixInvalido,
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

# Os 5 campos de DADO — e apenas eles — que o schema normalizado pode conter.
# A partir do Sprint 1 existe um sexto campo, `degradacoes`, que é metadado de
# qualidade do parsing e não carrega nenhum dado do pagador (ver
# TestPayloadsHostis.test_degradacoes_nao_carrega_dado_do_pagador).
CAMPOS_NORMALIZADOS = {"e2e_id", "valor", "status", "ispb_pagador", "id_recorrencia"}
CAMPO_QUALIDADE = "degradacoes"


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
    async def test_schema_tem_os_cinco_campos_de_dado_mais_a_qualidade(self, adapter):
        """Nem mais, nem menos: o contrato com o pipeline é fechado.

        Cinco campos de dado + `degradacoes`. O sexto entrou no Sprint 1 e é
        metadado de qualidade — a promessa de privacidade continua sendo sobre
        os cinco.
        """
        resultado = await adapter.parse_pix_event(payload_pix("automatic_pix.charge_failed"))
        assert set(resultado.keys()) == CAMPOS_NORMALIZADOS | {CAMPO_QUALIDADE}

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
        assert set(resultado.keys()) == CAMPOS_NORMALIZADOS | {CAMPO_QUALIDADE}
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
        assert set(evento.keys()) == CAMPOS_NORMALIZADOS | {CAMPO_QUALIDADE}
        assert CHAVE_PIX_PAGADOR not in json.dumps(evento, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════════
# SPRINT 1 — PAYLOADS HOSTIS (P0-1 … P0-5)
#
# O módulo se declara "deliberadamente tolerante". Tolerante NÃO é silencioso:
# toda degradação aplicada tem que aparecer em `degradacoes` E num
# logger.warning, e o que a CRAI não sabe interpretar ela RECUSA (422) em vez
# de diagnosticar em cima de um default.
# ══════════════════════════════════════════════════════════════════════════

class TestPayloadsHostis:
    """Um teste por payload torto que hoje derruba a API ou passa em silêncio."""

    # ── P0-1: valor em formatos que não são float ────────────────────────

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bruto, esperado", [
        ("299,90",       299.90),   # decimal pt-BR — hoje: ValueError → HTTP 500
        ("1.299,90",     1299.90),  # milhar pt-BR
        ("R$ 299,90",    299.90),   # com símbolo de moeda
        ("R$ 1.299,90",  1299.90),  # milhar + moeda
        ("299.90",       299.90),   # decimal en-US (já funcionava)
        ("1,299.90",     1299.90),  # milhar en-US
        (149,            149.00),   # int puro
    ])
    async def test_valor_em_formato_alternativo_e_lido(self, adapter, bruto, esperado):
        payload = payload_pix("automatic_pix.charge_failed")
        del payload["data"]["total_cents"]
        payload["data"]["valor"] = bruto

        resultado = await adapter.parse_pix_event(payload)

        assert resultado["valor"] == esperado
        assert resultado["degradacoes"] == []

    @pytest.mark.asyncio
    async def test_valor_ilegivel_nao_levanta_e_marca_degradacao(self, adapter):
        """Texto que não é número: degrada com rastro, nunca ValueError."""
        payload = payload_pix("automatic_pix.charge_failed")
        del payload["data"]["total_cents"]
        payload["data"]["valor"] = "duzentos reais"

        resultado = await adapter.parse_pix_event(payload)

        assert resultado["valor"] == 0.0
        assert DEGRADACAO_VALOR_ILEGIVEL in resultado["degradacoes"]

    # ── P0-5: valor ausente é evento defeituoso, não R$ 0 ────────────────

    @pytest.mark.asyncio
    async def test_valor_ausente_marca_degradacao(self, adapter):
        """Sem nenhum campo de valor: 0.0 é um default DECLARADO, não um fato."""
        payload = payload_pix("automatic_pix.charge_failed")
        del payload["data"]["total_cents"]

        resultado = await adapter.parse_pix_event(payload)

        assert resultado["valor"] == 0.0
        assert DEGRADACAO_VALOR_AUSENTE in resultado["degradacoes"]

    @pytest.mark.asyncio
    async def test_valor_string_vazia_conta_como_ausente(self, adapter):
        payload = payload_pix("automatic_pix.charge_failed")
        del payload["data"]["total_cents"]
        payload["data"]["valor"] = ""

        resultado = await adapter.parse_pix_event(payload)

        assert DEGRADACAO_VALOR_AUSENTE in resultado["degradacoes"]

    @pytest.mark.asyncio
    async def test_toda_degradacao_tambem_vira_warning(self, adapter, caplog):
        """A regra do sprint: nada degrada sem log. Sem isto, `degradacoes`
        seria só um campo bonito que ninguém vê em produção."""
        payload = payload_pix("automatic_pix.charge_failed")
        del payload["data"]["total_cents"]

        with caplog.at_level(logging.WARNING, logger=gateway_module.__name__):
            resultado = await adapter.parse_pix_event(payload)

        assert resultado["degradacoes"]
        assert caplog.records, "degradou sem emitir logger.warning"

    # ── P0-2 / P0-3: o envelope tem que ser determinístico ───────────────

    @pytest.mark.asyncio
    async def test_lote_de_um_e_desempacotado(self, adapter):
        """`data: [{...}]` hoje devolve o normalizado INTEIRO em default."""
        payload = payload_pix("automatic_pix.charge_failed")
        payload["data"] = [payload["data"]]

        resultado = await adapter.parse_pix_event(payload)

        assert resultado["valor"] == 299.90
        assert resultado["e2e_id"] == "E60701190202608261200abcdef123"
        assert resultado["id_recorrencia"] == "RN2026082600001"
        assert DEGRADACAO_LOTE_DE_1 in resultado["degradacoes"]

    @pytest.mark.asyncio
    async def test_lote_de_dois_e_recusado(self, adapter):
        """Processar lote pela metade é pior que recusar o lote inteiro."""
        payload = payload_pix("automatic_pix.charge_failed")
        payload["data"] = [payload["data"], payload["data"]]

        with pytest.raises(PayloadPixInvalido) as exc:
            await adapter.parse_pix_event(payload)

        assert exc.value.motivo == MOTIVO_LOTE_NAO_SUPORTADO

    @pytest.mark.asyncio
    @pytest.mark.parametrize("envelope", [{}, None, [], "texto", 42])
    async def test_envelope_inutilizavel_cai_na_raiz_e_declara(self, adapter, envelope):
        """P0-3: hoje `data: {}` lê a raiz e `data: {...}` não — o comportamento
        dependia de o PSP mandar o envelope vazio ou ausente. Agora os dois
        caminhos são o mesmo, e ficam declarados."""
        payload = {
            "event": "automatic_pix.charge_failed",
            "data": envelope,
            "valor": 150.0,
            "e2e_id": "E607011902026083100raiz",
            "recurrence_id": "RN_raiz_001",
        }

        resultado = await adapter.parse_pix_event(payload)

        assert resultado["valor"] == 150.0
        assert resultado["id_recorrencia"] == "RN_raiz_001"
        assert DEGRADACAO_ENVELOPE_AUSENTE in resultado["degradacoes"]

    @pytest.mark.asyncio
    async def test_sem_chave_data_nao_e_degradacao_silenciosa(self, adapter):
        """PSP que manda tudo no nível raiz é legítimo — mas fica registrado."""
        payload = {"event": "automatic_pix.charge_failed", "valor": 99.0}

        resultado = await adapter.parse_pix_event(payload)

        assert resultado["valor"] == 99.0
        assert DEGRADACAO_ENVELOPE_AUSENTE in resultado["degradacoes"]

    # ── P0-4: o fallback de status era código morto ──────────────────────

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bruto", ["failed", "FAILED", "Declined", "rejected",
                                       "error", "unpaid"])
    async def test_status_cru_de_falha_e_reconhecido(self, adapter, bruto):
        """PSP que não manda nome de evento: hoje TODA falha vira 'desconhecido'
        e some com HTTP 200 {"pipeline": false}."""
        payload = {"data": {"valor": 299.90, "status": bruto,
                            "recurrence_id": "RN_cru_001"}}

        resultado = await adapter.parse_pix_event(payload)

        assert resultado["status"] == STATUS_COBRANCA_FALHADA

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bruto", ["paid", "succeeded", "settled", "CONFIRMED"])
    async def test_status_cru_de_confirmacao_e_reconhecido(self, adapter, bruto):
        payload = {"data": {"valor": 299.90, "status": bruto}}
        resultado = await adapter.parse_pix_event(payload)
        assert resultado["status"] == STATUS_COBRANCA_CONFIRMADA

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bruto, esperado", [
        ("approved",  STATUS_AUTORIZACAO_CONCEDIDA),
        ("active",    STATUS_AUTORIZACAO_CONCEDIDA),
        ("revoked",   STATUS_AUTORIZACAO_REVOGADA),
        ("cancelled", STATUS_AUTORIZACAO_REVOGADA),
    ])
    async def test_status_de_autorizacao_usa_semantica_de_autorizacao(
        self, adapter, bruto, esperado,
    ):
        """`authorization_status: "approved"` é autorização CONCEDIDA, não
        cobrança confirmada — o nome do campo carrega a semântica."""
        payload = {"data": {"valor": 299.90,
                            "automatic_pix": {"authorization_status": bruto}}}

        resultado = await adapter.parse_pix_event(payload)

        assert resultado["status"] == esperado

    @pytest.mark.asyncio
    async def test_token_interno_continua_aceito(self, adapter):
        """PSP que já manda o token normalizado da CRAI segue funcionando."""
        payload = {"data": {"valor": 10.0, "status": STATUS_COBRANCA_FALHADA}}
        resultado = await adapter.parse_pix_event(payload)
        assert resultado["status"] == STATUS_COBRANCA_FALHADA

    @pytest.mark.asyncio
    async def test_status_irreconhecivel_continua_desconhecido_e_declarado(self, adapter):
        payload = {"data": {"valor": 10.0, "status": "quantum_flux"}}
        resultado = await adapter.parse_pix_event(payload)
        assert resultado["status"] == "desconhecido"
        assert DEGRADACAO_STATUS_DESCONHECIDO in resultado["degradacoes"]

    # ── Degenerados ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_payload_vazio_nao_levanta(self, adapter):
        resultado = await adapter.parse_pix_event({})
        assert set(resultado.keys()) == CAMPOS_NORMALIZADOS | {"degradacoes"}
        assert resultado["status"] == "desconhecido"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [None, [], "texto", 42, [{"valor": 1}]])
    async def test_payload_que_nao_e_objeto_e_recusado(self, adapter, payload):
        with pytest.raises(PayloadPixInvalido) as exc:
            await adapter.parse_pix_event(payload)
        assert exc.value.motivo == MOTIVO_PAYLOAD_NAO_E_OBJETO

    @pytest.mark.asyncio
    async def test_evento_integro_tem_degradacoes_vazia(self, adapter):
        """O campo só acusa problema quando há problema."""
        resultado = await adapter.parse_pix_event(payload_pix("automatic_pix.charge_failed"))
        assert resultado["degradacoes"] == []

    @pytest.mark.asyncio
    async def test_degradacoes_nao_carrega_dado_do_pagador(self, adapter):
        """O sexto campo é metadado de qualidade: só rótulos fixos entram nele."""
        payload = payload_pix("automatic_pix.charge_failed")
        del payload["data"]["total_cents"]
        payload["data"]["valor"] = CHAVE_PIX_PAGADOR + "x"

        resultado = await adapter.parse_pix_event(payload)

        assert all(d in DEGRADACOES_CONHECIDAS for d in resultado["degradacoes"])
        assert CHAVE_PIX_PAGADOR not in json.dumps(resultado["degradacoes"])


class TestEndpointRecusaPayloadDegradado:
    """A borda da API: o que a CRAI não sabe interpretar, ela recusa."""

    def test_valor_ausente_em_cobranca_falhada_devolve_422(self, client):
        """Melhor recusar que diagnosticar uma cobrança de R$ 0: com
        invoice_amount=0 o LTV vai a ~0, o e-Profit fica <= 0 e o churn
        involuntário legítimo é descartado sem rastro (P0-5)."""
        payload = payload_pix("automatic_pix.charge_failed")
        del payload["data"]["total_cents"]
        corpo = json.dumps(payload).encode()

        r = client.post("/webhooks/pix-automatico", content=corpo,
                        headers={"x-pix-signature": pix_header(corpo)})

        assert r.status_code == 422
        assert client.pipeline_calls == []

    def test_lote_devolve_422(self, client):
        payload = payload_pix("automatic_pix.charge_failed")
        payload["data"] = [payload["data"], payload["data"]]
        corpo = json.dumps(payload).encode()

        r = client.post("/webhooks/pix-automatico", content=corpo,
                        headers={"x-pix-signature": pix_header(corpo)})

        assert r.status_code == 422
        assert client.pipeline_calls == []

    def test_corpo_que_nao_e_json_devolve_400(self, client):
        corpo = b"isto nao e json {{{"
        r = client.post("/webhooks/pix-automatico", content=corpo,
                        headers={"x-pix-signature": pix_header(corpo)})

        assert r.status_code == 400
        assert client.pipeline_calls == []

    def test_valor_ausente_em_evento_que_nao_e_cobranca_nao_bloqueia(self, client):
        """Autorização concedida legitimamente não carrega valor — recusar
        seria transformar a correção do P0-5 num falso positivo."""
        payload = payload_pix("automatic_pix.authorization_created")
        del payload["data"]["total_cents"]
        corpo = json.dumps(payload).encode()

        r = client.post("/webhooks/pix-automatico", content=corpo,
                        headers={"x-pix-signature": pix_header(corpo)})

        assert r.status_code == 200
        assert r.json()["pipeline"] is False

    def test_valor_em_pt_br_chega_ao_pipeline(self, client):
        """O caso do P0-1 ponta a ponta: 'R$ 1.299,90' vira 1299.90."""
        payload = payload_pix("automatic_pix.charge_failed")
        del payload["data"]["total_cents"]
        payload["data"]["valor"] = "R$ 1.299,90"
        corpo = json.dumps(payload).encode()

        r = client.post("/webhooks/pix-automatico", content=corpo,
                        headers={"x-pix-signature": pix_header(corpo)})

        assert r.status_code == 200
        _, evento = client.pipeline_calls[0]
        assert evento["valor"] == 1299.90

    def test_status_cru_failed_aciona_o_pipeline(self, client):
        """O P0-4 ponta a ponta: um PSP sem nome de evento não perde mais a
        cobrança falhada com HTTP 200 {"pipeline": false}."""
        corpo = json.dumps({"data": {
            "valor": 299.90, "status": "failed", "recurrence_id": "RN_cru_002",
            "pix": {"end_to_end_id": "E607011902026083100cru", "payer": {"ispb": "60701190"}},
        }}).encode()

        r = client.post("/webhooks/pix-automatico", content=corpo,
                        headers={"x-pix-signature": pix_header(corpo)})

        assert r.status_code == 200
        assert r.json()["pipeline"] is True
        assert [c[0] for c in client.pipeline_calls] == ["pix_automatico"]

"""crai/security/webhook_verification.py — Verificação de assinatura de webhooks.

Todo webhook que dispara um pipeline da CRAI precisa provar que veio do
provedor esperado: sem isso, um request forjado aciona recuperação de
receita, envio de dunning e escrita no CRM.

Três verificadores:
    verify_stripe_signature          HMAC-SHA256 sobre "{timestamp}.{payload}",
                                     header 'stripe-signature' (cartão — correção
                                     de dívida técnica existente, sem expansão)
    verify_segment_signature         HMAC-SHA1 sobre o payload cru,
                                     header 'x-signature'
    verify_pix_automatico_signature  HMAC-SHA256 genérico no mesmo padrão do
                                     Stripe, para o webhook de Pix Automático
                                     que a Fase 3 vai criar

Toda rejeição é logada com prefixo [SECURITY] e nunca inclui o segredo nem a
assinatura esperada — só o motivo e um prefixo da assinatura recebida.
"""

import hashlib
import hmac
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Janela de tolerância contra replay: assinaturas com timestamp fora dela
# são rejeitadas mesmo que o HMAC confira.
REPLAY_TOLERANCE_SECONDS = 300


def _reject(origem: str, motivo: str) -> bool:
    """Loga a rejeição sem vazar segredo ou assinatura esperada."""
    logger.warning(f"[SECURITY] {origem}: assinatura rejeitada — {motivo}")
    return False


def _preview(assinatura: str) -> str:
    """Prefixo da assinatura recebida, para correlacionar logs sem expor o valor."""
    return f"{assinatura[:8]}..." if assinatura else "vazia"


def _parse_timestamped_header(sig_header: str) -> tuple[Optional[int], list]:
    """Extrai (timestamp, [assinaturas v1]) do header no formato 't=...,v1=...'."""
    timestamp: Optional[int] = None
    signatures: list = []
    for part in sig_header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError:
                return None, []
        elif key == "v1":
            signatures.append(value)
    return timestamp, signatures


def _verify_timestamped_hmac(payload: bytes, sig_header: str, secret: str, origem: str) -> bool:
    """HMAC-SHA256 sobre "{timestamp}.{payload}" — padrão Stripe, reusado no Pix."""
    if not secret:
        return _reject(origem, "segredo não configurado no ambiente")
    if not sig_header:
        return _reject(origem, "header de assinatura ausente")

    timestamp, signatures = _parse_timestamped_header(sig_header)
    if timestamp is None:
        return _reject(origem, "header sem timestamp válido")
    if not signatures:
        return _reject(origem, "header sem assinatura v1")

    idade = abs(int(time.time()) - timestamp)
    if idade > REPLAY_TOLERANCE_SECONDS:
        return _reject(origem, f"timestamp fora da janela de {REPLAY_TOLERANCE_SECONDS}s "
                               f"(diferença de {idade}s) — possível replay")

    assinado = f"{timestamp}.".encode() + payload
    esperado = hmac.new(secret.encode(), assinado, hashlib.sha256).hexdigest()

    if not any(hmac.compare_digest(esperado, recebida) for recebida in signatures):
        return _reject(origem, f"HMAC não confere (assinatura recebida {_preview(signatures[0])})")
    return True


def verify_stripe_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Valida o header 'stripe-signature' (HMAC-SHA256 de timestamp + payload)."""
    return _verify_timestamped_hmac(payload, sig_header, secret, origem="stripe")


def verify_pix_automatico_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Valida assinatura do webhook de Pix Automático (consumida na Fase 3)."""
    return _verify_timestamped_hmac(payload, sig_header, secret, origem="pix_automatico")


def verify_segment_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Valida o header 'x-signature' do Segment (HMAC-SHA1 do payload cru)."""
    origem = "segment"
    if not secret:
        return _reject(origem, "segredo não configurado no ambiente")
    if not sig_header:
        return _reject(origem, "header de assinatura ausente")

    esperado = hmac.new(secret.encode(), payload, hashlib.sha1).hexdigest()
    if not hmac.compare_digest(esperado, sig_header.strip()):
        return _reject(origem, f"HMAC não confere (assinatura recebida {_preview(sig_header)})")
    return True

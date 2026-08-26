"""crai/security/tokenization.py — Criptografia simétrica de campos sensíveis.

Uso pontual, para o punhado de campos que precisam existir em repouso mas nunca
podem trafegar em texto puro: hoje, apenas a chave Pix do pagador
(CPF/telefone/e-mail), guardada só para conciliação financeira.

Fernet (AES-128-CBC + HMAC-SHA256, do pacote `cryptography`) com a chave vinda
da variável de ambiente CRAI_ENCRYPTION_KEY.

**Fail closed**: sem chave configurada, `encrypt_sensitive_field` levanta
`EncryptionKeyMissing` em vez de devolver o valor original. Um dado sensível
nunca deve ser persistido em texto puro porque o ambiente estava mal
configurado — é preferível a operação falhar de forma visível.

Gerar uma chave nova para o .env:

    python -c "from crai.security.tokenization import generate_key; print(generate_key())"
"""

import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

ENCRYPTION_KEY_ENV = "CRAI_ENCRYPTION_KEY"


class EncryptionKeyMissing(RuntimeError):
    """CRAI_ENCRYPTION_KEY ausente ou malformada no ambiente."""


class DecryptionFailed(RuntimeError):
    """Token corrompido ou cifrado com outra chave."""


def generate_key() -> str:
    """Gera uma chave Fernet nova (base64 urlsafe de 32 bytes)."""
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    """Instancia o Fernet a partir da chave do ambiente."""
    raw = os.getenv(ENCRYPTION_KEY_ENV, "").strip()
    if not raw:
        raise EncryptionKeyMissing(
            f"{ENCRYPTION_KEY_ENV} não definida — campo sensível não pode ser "
            f"cifrado nem persistido. Gere uma chave com "
            f"crai.security.tokenization.generate_key()."
        )
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError) as e:
        raise EncryptionKeyMissing(
            f"{ENCRYPTION_KEY_ENV} malformada ({e}) — precisa ser uma chave "
            f"Fernet válida (base64 urlsafe de 32 bytes)."
        ) from e


def encrypt_sensitive_field(valor: str) -> str:
    """Cifra um campo sensível. Retorna o token Fernet em texto (base64)."""
    if valor is None:
        raise ValueError("Campo sensível não pode ser None")
    token = _fernet().encrypt(str(valor).encode()).decode()
    # Nunca logar o valor — só o fato de que houve cifragem.
    logger.debug("[TOKENIZATION] Campo sensível cifrado (%d bytes de token)", len(token))
    return token


def decrypt_sensitive_field(token: str) -> str:
    """Decifra um token gerado por encrypt_sensitive_field()."""
    if not token:
        raise ValueError("Token vazio não pode ser decifrado")
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise DecryptionFailed(
            "Token inválido — corrompido ou cifrado com outra chave."
        ) from e

"""tests/supabase_falso.py — um projeto Supabase de mentira para a suíte.

Gera um par de chaves ES256 na hora, publica a pública num JWKS local e
assina tokens com a privada — o mesmo formato que o Supabase Auth serve e
emite. Nenhum teste bate na rede: `supabase_auth._baixar_jwks` é substituído
pela função que devolve o JWKS daqui (ver a fixture `supabase_falso` no
`conftest`).

COORDENADAS DE LARGURA FIXA. Não usar `ECAlgorithm.to_jwk` do PyJWT: ele
codifica com `to_base64url_uint`, que descarta bytes zero à esquerda, e
`from_jwk` exige exatamente 32 bytes — cerca de 1 em 128 chaves geradas
virava JWK inválido, e a suíte falhava de vez em quando com
`chave_desconhecida`. A RFC 7518 §6.2.1.2 pede largura fixa; o Supabase serve
assim; este helper também.
"""

import base64
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import ec

PROJECT_URL = "https://projeto-de-teste.supabase.co"
JWKS_URL = f"{PROJECT_URL}/auth/v1/.well-known/jwks.json"


def _b64url_32_bytes(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(32, "big")).rstrip(b"=").decode()


def par_de_chaves(kid: str):
    """(chave privada, JWK público) para um `kid`."""
    privada = ec.generate_private_key(ec.SECP256R1())
    pub = privada.public_key().public_numbers()
    jwk = {
        "kty": "EC", "crv": "P-256",
        "x": _b64url_32_bytes(pub.x), "y": _b64url_32_bytes(pub.y),
        "kid": kid, "alg": "ES256", "use": "sig",
    }
    return privada, jwk


def token(privada, kid: str, claims: dict, **sobrescreve) -> str:
    """Um access_token como o Supabase emite, com as claims dadas por cima."""
    agora = int(time.time())
    corpo = {
        "iss": f"{PROJECT_URL}/auth/v1",
        "sub": "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b",
        "aud": "authenticated",
        "role": "authenticated",
        "iat": agora,
        "exp": agora + 3600,
        **claims,
    }
    corpo.update(sobrescreve)
    return jwt.encode(corpo, privada, algorithm="ES256", headers={"kid": kid})


class ProjetoFalso:
    """Estado mutável do projeto: chaves publicadas e contador de buscas."""

    def __init__(self, kid: str = "chave-1"):
        self.privada, jwk = par_de_chaves(kid)
        self.kid = kid
        self.jwks = {"keys": [jwk]}
        self.buscas = 0

    def baixar_jwks(self, url: str) -> dict:
        assert url == JWKS_URL, url
        self.buscas += 1
        return self.jwks

    def token(self, tenant_id=None, **sobrescreve) -> str:
        claims = {} if tenant_id is None else {"tenant_id": tenant_id}
        return token(self.privada, self.kid, claims, **sobrescreve)

    def bearer(self, tenant_id=None, **sobrescreve) -> dict:
        return {"Authorization": f"Bearer {self.token(tenant_id, **sobrescreve)}"}

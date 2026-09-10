"""crai/accounts/supabase_auth.py — valida o JWT que o Supabase Auth emitiu.

O QUE ESTE MÓDULO FAZ, e só isso: dado o token que o frontend recebeu no login
(`Authorization: Bearer <token>`), confere que ele foi assinado por UMA das
chaves públicas do projeto Supabase (JWKS), que não expirou, e devolve as
claims. Login, senha, cadastro e a tabela `empresas` moram no Supabase, fora
deste repositório.

CHAVES PÚBLICAS (JWKS). O Supabase publica as chaves em
    https://<project>.supabase.co/auth/v1/.well-known/jwks.json
e pode rotacioná-las. O JWKS fica em cache por no máximo `JWKS_TTL_SEGUNDOS`
(10 min); e se um token chega com um `kid` que o cache não conhece, o cache é
renovado UMA vez antes de recusar — é o caso da rotação que aconteceu entre
duas leituras.

SÓ ALGORITMOS ASSIMÉTRICOS. `HS256` é recusado de propósito: com JWKS, a
chave que valida é pública; aceitar HMAC abriria a confusão de algoritmo
clássica (o atacante assina com a chave pública como segredo). Projetos
Supabase novos emitem ES256; os antigos, RS256. Os dois entram; nada mais.

SEM `SUPABASE_PROJECT_URL` NÃO HÁ VALIDAÇÃO — e isso é uma falha ALTA
(`ConfiguracaoAusente`), nunca um "deixa passar". Um endpoint autenticado que
aceita qualquer coisa porque a env não foi configurada é pior que um endpoint
fora do ar. Lido a cada chamada, como `modo_real()` e `caminho_do_banco()`:
a suíte liga e desliga env por teste.
"""

import json
import logging
import os
import threading
import time
import urllib.request

import jwt
from jwt import PyJWK
from jwt.exceptions import InvalidTokenError

logger = logging.getLogger(__name__)

ENV_PROJECT_URL = "SUPABASE_PROJECT_URL"
JWKS_TTL_SEGUNDOS = 600            # 10 min — o teto que o plano fixou
JWKS_TIMEOUT_SEGUNDOS = 5
ALGORITMOS_ACEITOS = ("ES256", "RS256")
AUDIENCIA = "authenticated"        # o `aud` dos tokens de usuário logado no Supabase
CLAIM_TENANT = "tenant_id"


class ConfiguracaoAusente(RuntimeError):
    """`SUPABASE_PROJECT_URL` não configurada. Erro de operação, não do cliente."""


class TokenInvalido(ValueError):
    """Token ausente, malformado, expirado, com assinatura errada ou sem tenant.

    `motivo` é curto e estável (vira o `detail` do 401); `mensagem` explica.
    """

    def __init__(self, motivo: str, mensagem: str):
        super().__init__(mensagem)
        self.motivo = motivo
        self.mensagem = mensagem


# ── Configuração ──────────────────────────────────────────────────────────

def project_url() -> str:
    bruto = os.getenv(ENV_PROJECT_URL)
    if bruto is None or not bruto.strip():
        raise ConfiguracaoAusente(
            f"{ENV_PROJECT_URL} não configurada — sem ela a CRAI não tem como "
            "validar o token do Supabase, e um endpoint autenticado sem validação "
            "não pode subir. Ex.: SUPABASE_PROJECT_URL=https://abcdefgh.supabase.co")
    return bruto.strip().rstrip("/")


def jwks_url() -> str:
    return f"{project_url()}/auth/v1/.well-known/jwks.json"


# ── JWKS: busca + cache ───────────────────────────────────────────────────

_cache_lock = threading.Lock()
_cache: dict = {"url": None, "chaves": {}, "expira_em": 0.0}


def _baixar_jwks(url: str) -> dict:
    """GET no JWKS. Separado para o teste substituir sem tocar na rede."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=JWKS_TIMEOUT_SEGUNDOS) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _indexar(jwks: dict) -> dict:
    """{kid: PyJWK} só das chaves com algoritmo aceito. As outras são ignoradas."""
    chaves = {}
    for entrada in jwks.get("keys", []):
        kid = entrada.get("kid")
        alg = entrada.get("alg")
        if not kid:
            continue
        if alg is not None and alg not in ALGORITMOS_ACEITOS:
            logger.warning("[SUPABASE-AUTH] chave %s com alg=%s ignorada", kid, alg)
            continue
        try:
            chaves[kid] = PyJWK(entrada, algorithm=alg)
        except Exception as e:                    # noqa: BLE001
            logger.warning("[SUPABASE-AUTH] chave %s ignorada: %s", kid, e)
    return chaves


def _chaves(forcar: bool = False) -> dict:
    """As chaves do projeto, do cache ou renovadas. Levanta se a URL falhar."""
    url = jwks_url()
    with _cache_lock:
        valido = (_cache["url"] == url and _cache["chaves"]
                  and time.monotonic() < _cache["expira_em"])
        if valido and not forcar:
            return _cache["chaves"]

        jwks = _baixar_jwks(url)
        chaves = _indexar(jwks)
        _cache.update(url=url, chaves=chaves,
                      expira_em=time.monotonic() + JWKS_TTL_SEGUNDOS)
        logger.info("[SUPABASE-AUTH] JWKS carregado de %s (%d chave(s))", url, len(chaves))
        return chaves


def limpar_cache() -> None:
    """Para os testes: nenhum teste herda as chaves que outro carregou."""
    with _cache_lock:
        _cache.update(url=None, chaves={}, expira_em=0.0)


def _chave_do_token(token: str) -> tuple:
    """(PyJWK, alg) para o `kid` do header. Renova o cache uma vez se não achar."""
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as e:
        raise TokenInvalido("token_malformado", f"token não é um JWT válido: {e}") from e

    alg = header.get("alg")
    if alg not in ALGORITMOS_ACEITOS:
        raise TokenInvalido(
            "algoritmo_recusado",
            f"alg={alg!r} não é aceito; só {', '.join(ALGORITMOS_ACEITOS)}")

    kid = header.get("kid")
    if not kid:
        raise TokenInvalido("sem_kid", "token sem `kid` no header — impossível "
                            "escolher a chave pública")

    chave = _chaves().get(kid)
    if chave is None:
        chave = _chaves(forcar=True).get(kid)      # rotação entre leituras
    if chave is None:
        raise TokenInvalido("chave_desconhecida",
                            f"kid={kid!r} não está no JWKS do projeto")
    return chave, alg


# ── Validação ─────────────────────────────────────────────────────────────

def validar_token(token: str) -> dict:
    """Confere assinatura (JWKS) + expiração + audiência e devolve as claims.

    Levanta `TokenInvalido` para tudo que é culpa do token, e
    `ConfiguracaoAusente`/erro de rede para o que é culpa da instalação —
    o chamador transforma o primeiro em 401 e o segundo em 500.

    Não exige `tenant_id` aqui: isso é responsabilidade de `tenant_das_claims`.
    A separação deixa cada função com um contrato só.
    """
    if not isinstance(token, str) or not token.strip():
        raise TokenInvalido("token_ausente", "token vazio")
    token = token.strip()

    chave, alg = _chave_do_token(token)
    try:
        return jwt.decode(
            token, chave.key, algorithms=[alg], audience=AUDIENCIA,
            options={"require": ["exp"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise TokenInvalido("token_expirado", "token expirado — faça login de novo") from e
    except jwt.InvalidAudienceError as e:
        raise TokenInvalido("audiencia_invalida",
                            f"`aud` não é {AUDIENCIA!r}") from e
    except jwt.InvalidSignatureError as e:
        raise TokenInvalido("assinatura_invalida",
                            "assinatura não confere com as chaves do projeto") from e
    except InvalidTokenError as e:
        raise TokenInvalido("token_invalido", str(e)) from e


def tenant_das_claims(claims: dict) -> str:
    """O `tenant_id` da claim customizada (ver README.md), ou `TokenInvalido`.

    Claim ausente é o caso do hook do Supabase não configurado ou da empresa
    sem vínculo em `empresas` — o 401 tem que dizer isso com todas as letras,
    porque é o erro que a pessoa vai ver no primeiro dia de integração.
    """
    tenant = claims.get(CLAIM_TENANT)
    if tenant is None:
        raise TokenInvalido(
            "sem_tenant",
            f"token válido, mas sem a claim `{CLAIM_TENANT}`. Configure o Custom "
            "Access Token Hook no Supabase (ver crai/accounts/README.md) e "
            "confira se a empresa está vinculada ao usuário na tabela `empresas`")
    if not isinstance(tenant, str) or not tenant.strip():
        raise TokenInvalido("tenant_invalido",
                            f"claim `{CLAIM_TENANT}` precisa ser texto não vazio")
    return tenant.strip()

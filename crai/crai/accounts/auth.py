"""crai/accounts/auth.py — a dependency do FastAPI que resolve o tenant.

    @app.get("/insights")
    async def insights(tenant_id: str = Depends(get_tenant_id)): ...

Lê `Authorization: Bearer <token>`, valida contra o Supabase (`supabase_auth`)
e devolve o `tenant_id` da claim — o MESMO identificador que o bandit e o
`retention_log` já usam para particionar. Os webhooks existentes (Segment,
Pix, Stripe) NÃO passam por aqui: eles continuam com `_tenant_da_requisicao`,
que é outro contrato (assinatura do provedor, não login de empresa).

401 para tudo que é culpa do token (ausente, expirado, assinatura errada, sem
`tenant_id`); 500 quando a instalação não tem `SUPABASE_PROJECT_URL` ou não
alcança o JWKS — o cliente não tem como consertar isso, e um 401 o mandaria
refazer login à toa.

`default_tenant` é recusado também aqui, pelo mesmo motivo que em
`_tenant_da_requisicao`: é o balde de quem não declarou tenant, e ninguém
entra nele de propósito.
"""

import logging
import re
from typing import Optional

from fastapi import Header, HTTPException

from ..churn_voluntary.offer_bandit import TENANT_PADRAO
from . import supabase_auth
from .supabase_auth import ConfiguracaoAusente, TokenInvalido

logger = logging.getLogger(__name__)

# O mesmo formato que `api/app.py::_TENANT_VALIDO` aceita. Copiado e não
# importado: `api/app.py` vai importar este pacote (Sprint 2), e um import no
# sentido contrário fecharia um ciclo.
_TENANT_VALIDO = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _401(motivo: str, mensagem: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"motivo": motivo, "detalhe": mensagem},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _token_do_header(authorization: Optional[str]) -> str:
    if authorization is None or not authorization.strip():
        raise _401("sem_authorization",
                   "header `Authorization: Bearer <token>` ausente")
    partes = authorization.strip().split(None, 1)
    if len(partes) != 2 or partes[0].lower() != "bearer" or not partes[1].strip():
        raise _401("authorization_malformado",
                   "esperado `Authorization: Bearer <token>`")
    return partes[1].strip()


async def get_conta(authorization: Optional[str] = Header(default=None)) -> dict:
    """A conta autenticada: `{tenant_id, email, sub}`.

    Para as rotas que precisam de mais que o tenant — o envio de insights por
    e-mail usa o `email` do token como destinatário, e SÓ ele: uma empresa
    logada não escolhe para quem a CRAI manda e-mail. `get_tenant_id` é o
    atalho para quem só precisa do tenant.
    """
    token = _token_do_header(authorization)
    try:
        claims = supabase_auth.validar_token(token)
        tenant = supabase_auth.tenant_das_claims(claims)
    except TokenInvalido as e:
        logger.warning("[SUPABASE-AUTH] token recusado: %s", e.motivo)
        raise _401(e.motivo, e.mensagem) from e
    except ConfiguracaoAusente as e:
        logger.error("[SUPABASE-AUTH] %s", e)
        raise HTTPException(status_code=500, detail={
            "motivo": "supabase_nao_configurado", "detalhe": str(e)}) from e
    except Exception as e:                        # noqa: BLE001 — JWKS fora do ar etc.
        logger.error("[SUPABASE-AUTH] falha ao obter as chaves do Supabase: %s", e)
        raise HTTPException(status_code=500, detail={
            "motivo": "jwks_indisponivel",
            "detalhe": f"não foi possível obter as chaves públicas do Supabase: {e}",
        }) from e

    if tenant == TENANT_PADRAO:
        raise _401("tenant_reservado",
                   f"{TENANT_PADRAO!r} é o balde de quem não declara tenant; "
                   "a claim precisa trazer o identificador real da empresa")
    if not _TENANT_VALIDO.match(tenant):
        raise _401("tenant_com_forma_invalida",
                   "claim `tenant_id` fora do formato: esperado 1-64 caracteres "
                   "em [A-Za-z0-9._-]")
    email = claims.get("email")
    return {
        "tenant_id": tenant,
        "email": email.strip() if isinstance(email, str) and email.strip() else None,
        "sub": claims.get("sub"),
    }


async def get_tenant_id(authorization: Optional[str] = Header(default=None)) -> str:
    return (await get_conta(authorization))["tenant_id"]

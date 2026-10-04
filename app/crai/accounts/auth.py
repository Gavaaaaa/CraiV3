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

O PAPEL (Etapa 2). A claim `papel` do token (`owner`, `admin` ou `membro`, o
que o hook do frontend grava) vem em `get_conta`. Ausente → `None`, e o
usuário só lê. Presente e fora do vocabulário → 401: um papel inventado não
pode virar "sem papel" em silêncio. `exigir_papel` é chamada DENTRO da rota
(e não como dependency): a catraca `test_supabase_auth` reconhece as rotas
autenticadas pelo nome da dependency (`get_conta`/`get_tenant_id`). O papel
vale até o token expirar; um membro rebaixado continua com o papel antigo até
o próximo login (o backend não consulta `membros_empresa`).

O PLANO (Rodada 2). A claim `plano` (`essencial` ou `premium`) vem em
`get_conta`. Ausente ou fora do vocabulário vale `essencial`: sem a claim, a
empresa fica com o plano que dá menos. `exigir_plano_premium` responde 403
`plano_sem_api`; hoje só GERAR chave de API o exige (listar e revogar valem em
qualquer plano). O login real só terá a claim na Etapa 5.

A CHAVE DE API (Rodada 2). `get_tenant_id` aceita, além do token de login, a
chave `crai_live_...` da empresa (`chaves_api.py`), e SÓ nas rotas de
`chaves_api.ROTAS_COM_CHAVE` — as quatro da API de clientes. Em qualquer outra
rota, um `Authorization` com cara de chave recebe 401 sem que a chave seja
consultada: a resposta é a mesma para chave válida, revogada ou inventada, e
nenhuma outra rota serve de teste para saber se uma chave existe. A chave
nunca chega ao validador de JWT nem ao log.
"""

import logging
import re
from typing import Optional

from fastapi import Header, HTTPException, Request

from ..churn_voluntary.offer_bandit import TENANT_PADRAO
from . import chaves_api, supabase_auth
from .supabase_auth import ConfiguracaoAusente, TokenInvalido

logger = logging.getLogger(__name__)

# O mesmo formato que `api/app.py::_TENANT_VALIDO` aceita. Copiado e não
# importado: `api/app.py` vai importar este pacote (Sprint 2), e um import no
# sentido contrário fecharia um ciclo.
_TENANT_VALIDO = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

PAPEIS = ("owner", "admin", "membro")
PLANO_ESSENCIAL = "essencial"
PLANO_PREMIUM = "premium"
PLANOS = (PLANO_ESSENCIAL, PLANO_PREMIUM)


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
    """A conta autenticada: `{tenant_id, email, sub, papel}`.

    Para as rotas que precisam de mais que o tenant — o envio de insights por
    e-mail usa o `email` do token como destinatário, e SÓ ele: uma empresa
    logada não escolhe para quem a CRAI manda e-mail. `get_tenant_id` é o
    atalho para quem só precisa do tenant.
    """
    token = _token_do_header(authorization)
    if chaves_api.parece_chave(token):
        # Antes de qualquer validação, e sem consultar a chave (K8): a resposta
        # não diz se ela existe.
        raise _401("chave_nao_vale_nesta_rota",
                   "a chave de API só autentica as rotas da API de clientes "
                   "(POST /clientes, POST /clientes/lote, PATCH /clientes/{id} e "
                   "DELETE /clientes/{id}); esta rota exige o token de login")
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
    papel = claims.get("papel")
    if papel is not None and (not isinstance(papel, str) or papel not in PAPEIS):
        raise _401("papel_invalido",
                   f"claim `papel` fora do vocabulário: esperado um de {', '.join(PAPEIS)}")
    plano = claims.get("plano")
    email = claims.get("email")
    return {
        "tenant_id": tenant,
        "email": email.strip() if isinstance(email, str) and email.strip() else None,
        "sub": claims.get("sub"),
        "papel": papel,
        "plano": plano if isinstance(plano, str) and plano in PLANOS else PLANO_ESSENCIAL,
    }


def exigir_papel(conta: dict, *aceitos: str) -> str:
    """403 se o papel da conta não está entre os `aceitos`; devolve o papel.
    Sem papel no token é 403 também: sem papel, só leitura."""
    papel = conta.get("papel")
    if papel not in aceitos:
        raise HTTPException(status_code=403, detail={
            "motivo": "papel_insuficiente",
            "detalhe": f"esta operação exige o papel {' ou '.join(aceitos)}"})
    return papel


def exigir_plano_premium(conta: dict) -> None:
    """403 `plano_sem_api` se a empresa da conta não é do plano premium (K3:
    gerar chave de API)."""
    if conta.get("plano") != PLANO_PREMIUM:
        raise HTTPException(status_code=403, detail={
            "motivo": "plano_sem_api",
            "detalhe": "gerar chave de API faz parte do plano Premium"})


def _rota_aceita_chave(request: Optional[Request]) -> bool:
    """A rota desta requisição está em `chaves_api.ROTAS_COM_CHAVE`? Sem rota
    resolvida, não: na dúvida a chave não vale."""
    if request is None:
        return False
    caminho = getattr(request.scope.get("route"), "path", None)
    return (request.method.upper(), caminho) in chaves_api.ROTAS_COM_CHAVE


def _tenant_da_chave(chave: str) -> str:
    try:
        return chaves_api.autenticar(chave)
    except chaves_api.ChaveInvalida:
        # K6: um corpo só para inexistente, malformada e revogada.
        raise _401("chave_invalida", "chave de API inválida ou revogada") from None
    except chaves_api.LimiteDeUso as e:
        raise HTTPException(
            status_code=429,
            detail={"motivo": "limite_de_uso",
                    "detalhe": f"limite de {chaves_api.limite_por_minuto()} requisições por "
                               f"minuto desta chave atingido; tente de novo em {e.espera} s"},
            headers={"Retry-After": str(e.espera)}) from None


async def get_tenant_id(request: Request = None,
                        authorization: Optional[str] = Header(default=None)) -> str:
    """O tenant de quem chama: do token de login, em toda rota; ou da chave de
    API da empresa, só nas rotas de `chaves_api.ROTAS_COM_CHAVE`."""
    if _rota_aceita_chave(request):
        token = _token_do_header(authorization)
        if chaves_api.parece_chave(token):
            return _tenant_da_chave(token)
    return (await get_conta(authorization))["tenant_id"]

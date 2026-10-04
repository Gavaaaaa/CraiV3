"""crai/api/dev_token.py — o token de DESENVOLVIMENTO do dashboard (Etapa 2, Bloco 4).

    POST /dev/token   {"papel": "owner" | "admin" | "membro"}   (padrão: owner)

POR QUE EXISTE. O dashboard novo (Etapa 4) fala com as rotas autenticadas do
self-service, e o login de verdade mora no Supabase do site — fora deste
repositório, e fora do alcance de quem só quer subir o backend na própria
máquina. Esta rota dá, em desenvolvimento, um token de uma empresa FICTÍCIA
(`demo_dashboard`) com o papel pedido, para a tela funcionar sem Supabase.

AS TRÊS TRAVAS, cada uma suficiente sozinha:

  1. A ROTA só é montada com `ENV=development` (`montar`, chamada na subida).
     Fora disso ela não existe: 404, como qualquer caminho desconhecido. `demo`
     NÃO conta: é o ambiente da demonstração pública.
  2. A CHAVE que assina é gerada na subida do serviço, em memória, e só em
     `development` (`preparar`). Nunca é gravada em disco nem lida de env: um
     token emitido por uma subida não vale na seguinte, nem em outra máquina.
  3. A VALIDAÇÃO (`accounts/supabase_auth.validar_token`) recusa, fora de
     `development`, qualquer token cujo `kid` comece por `crai-dev-` — antes de
     consultar chave alguma. Mesmo que a chave vazasse, produção não a aceita.

O QUE O TOKEN CARREGA: `tenant_id = demo_dashboard`, `papel`, `aud`, `iss`,
`sub` genérico, `exp` (1 h). Nenhum e-mail, nenhum nome: não há pessoa aqui.

Este módulo NÃO importa de `app.py` nem de `accounts` (é `supabase_auth` que o
consulta, por import tardio).
"""

import logging
import os
import secrets
import threading
import time
from typing import Optional

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import APIRouter, Body, HTTPException

logger = logging.getLogger(__name__)

ENV_DESENVOLVIMENTO = "development"
TENANT_DEMO = "demo_dashboard"
PREFIXO_KID = "crai-dev-"
EMISSOR = "crai-dev"
ALGORITMO = "ES256"
AUDIENCIA = "authenticated"          # a mesma dos tokens do Supabase
VALIDADE_SEGUNDOS = 3600
PAPEIS = ("owner", "admin", "membro")
PAPEL_PADRAO = "owner"

router = APIRouter(tags=["desenvolvimento"])

_trava = threading.Lock()
_par: Optional[tuple] = None         # (chave privada, kid) desta subida


def ambiente_de_desenvolvimento() -> bool:
    """Lido a cada chamada, como o resto das envs: a suíte liga e desliga."""
    return os.getenv("ENV", "production").strip().lower() == ENV_DESENVOLVIMENTO


def e_kid_de_desenvolvimento(kid) -> bool:
    return isinstance(kid, str) and kid.startswith(PREFIXO_KID)


def preparar() -> bool:
    """Gera a chave desta subida, se o ambiente for `development`. True se há
    chave. Fora de `development` não gera nada."""
    global _par
    if not ambiente_de_desenvolvimento():
        return False
    with _trava:
        if _par is None:
            _par = (ec.generate_private_key(ec.SECP256R1()),
                    PREFIXO_KID + secrets.token_hex(8))
            logger.warning("[DEV-TOKEN] ENV=development: chave de desenvolvimento gerada em "
                           "memória; POST /dev/token emite tokens da empresa fictícia %r. "
                           "Nunca suba um serviço exposto com ENV=development.", TENANT_DEMO)
    return True


def esquecer_chave() -> None:
    """Para os testes: a próxima `preparar` gera outra chave."""
    global _par
    with _trava:
        _par = None


def chave_publica(kid: str):
    """A chave pública desta subida, se o ambiente é `development` e o `kid` é
    o dela; None em qualquer outro caso."""
    if not ambiente_de_desenvolvimento():
        return None
    with _trava:
        par = _par
    if par is None or par[1] != kid:
        return None
    return par[0].public_key()


def emitir(papel: str = PAPEL_PADRAO) -> dict:
    """Um token da empresa fictícia com o papel dado. Fora de `development`
    levanta `RuntimeError`: não existe token de desenvolvimento em produção."""
    if papel not in PAPEIS:
        raise ValueError(f"papel desconhecido: {papel!r}")
    if not preparar():
        raise RuntimeError("token de desenvolvimento só existe com ENV=development")
    with _trava:
        privada, kid = _par
    agora = int(time.time())
    claims = {"iss": EMISSOR, "sub": f"dev-{papel}", "aud": AUDIENCIA, "role": "authenticated",
              "tenant_id": TENANT_DEMO, "papel": papel,
              "iat": agora, "exp": agora + VALIDADE_SEGUNDOS}
    token = jwt.encode(claims, privada, algorithm=ALGORITMO, headers={"kid": kid})
    return {"token": token, "tipo": "Bearer", "tenant_id": TENANT_DEMO, "papel": papel,
            "expira_em_segundos": VALIDADE_SEGUNDOS}


@router.post("/dev/token")
async def token_de_desenvolvimento(corpo: Optional[dict] = Body(default=None)) -> dict:
    """Token da empresa fictícia `demo_dashboard`, com o papel pedido. Só
    responde com `ENV=development`; fora disso, 404 — mesmo que a rota tenha
    sido montada (defesa em profundidade: a env pode mudar depois da subida)."""
    if not ambiente_de_desenvolvimento():
        raise HTTPException(status_code=404, detail="Not Found")
    if corpo is not None and not isinstance(corpo, dict):
        raise HTTPException(status_code=422, detail={
            "motivo": "corpo_invalido", "detalhe": "esperado um objeto, ou nada", "campo": "corpo"})
    corpo = corpo or {}
    extras = sorted(set(corpo) - {"papel"})
    if extras:
        raise HTTPException(status_code=422, detail={
            "motivo": "campo_desconhecido", "detalhe": "o corpo aceita só `papel`",
            "campo": extras[0]})
    papel = corpo.get("papel", PAPEL_PADRAO)
    if papel not in PAPEIS:
        raise HTTPException(status_code=422, detail={
            "motivo": "papel_invalido", "detalhe": f"esperado um de {', '.join(PAPEIS)}",
            "campo": "papel"})
    return emitir(papel)


def montar(aplicacao) -> bool:
    """Monta `POST /dev/token` em `aplicacao` SÓ com `ENV=development`, e gera
    a chave desta subida. Devolve se montou."""
    if not preparar():
        return False
    aplicacao.include_router(router)
    return True

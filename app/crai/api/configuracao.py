"""crai/api/configuracao.py — a configuração da empresa, lida e gravada por ela.

    GET /configuracao    qualquer papel (inclusive token sem papel): só leitura
    PUT /configuracao    `owner` ou `admin`; `membro` e token sem papel: 403

A regra de cada chave, os padrões e a validação moram em
`dunning/configuracao.py`; este módulo só expõe. O que ele garante:

  - R12: o tenant vem do token. Não existe configuração "de outra empresa" a
    pedir: cada token lê e grava a própria, e só ela.
  - O PUT é PARCIAL: manda só as chaves que mudam. Chave desconhecida ou
    interna (`canal_presumido`) é recusada com 422, e NADA é gravado — a
    validação acontece inteira antes da escrita, inclusive a do conjunto
    (janela com início antes do fim, faixa grave menor que a preocupante).
  - A resposta dos dois é a configuração EFETIVA completa (padrões ← gravada),
    sem as chaves internas: a tela não precisa conhecer os padrões.

Este módulo NÃO importa de `app.py` (é o `app.py` que o monta).
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException

from ..accounts import get_conta
from ..accounts.auth import exigir_papel
from ..dunning import configuracao
from . import datas

logger = logging.getLogger(__name__)

router = APIRouter(tags=["configuracao"])


def _resposta(tenant_id: str) -> dict:
    return {"configuracao": configuracao.publica(configuracao.ler(tenant_id))}


@router.get("/configuracao")
async def ler_configuracao(conta: dict = Depends(get_conta)) -> dict:
    """A configuração efetiva da empresa do token: modo da mensagem do
    involuntário, prazo de escolha, janela de contato, canais na ordem de
    preferência e os prazos de retenção. `pode_editar` diz se o papel do token
    grava (a tela usa para travar o formulário; quem decide é o PUT)."""
    return {**_resposta(conta["tenant_id"]),
            "pode_editar": conta.get("papel") in ("owner", "admin")}


@router.put("/configuracao")
async def gravar_configuracao(corpo: dict = Body(...),
                              conta: dict = Depends(get_conta)) -> dict:
    """Grava as chaves enviadas (parcial) e devolve a configuração completa.
    Exige `owner` ou `admin`. 422 com `campo` e `detalhe` para chave
    desconhecida, valor fora da faixa ou conjunto inválido; nada é gravado."""
    papel = exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    try:
        configuracao.gravar(tenant_id, corpo, papel=papel, agora=datas.agora_local())
    except configuracao.ConfiguracaoInvalida as e:
        raise HTTPException(status_code=422, detail={
            "motivo": "configuracao_invalida", "campo": e.campo, "detalhe": e.detalhe})
    logger.info("[CONFIG] tenant=%s configuração gravada por papel=%s (chaves: %s)",
                tenant_id, papel, ", ".join(sorted(corpo)))
    return {**_resposta(tenant_id), "pode_editar": True}

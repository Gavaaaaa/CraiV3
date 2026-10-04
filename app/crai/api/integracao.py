"""crai/api/integracao.py — as chaves de API da empresa, pelo dashboard (Rodada 2).

    GET    /integracao/chaves         a lista, sem hash e sem a chave
    POST   /integracao/chaves         {"nome": "..."} — cria e devolve a chave UMA vez
    DELETE /integracao/chaves/{id}    revoga

As três são do DASHBOARD: autenticam pelo token de login (`get_conta`), nunca
pela própria chave de API (K8) — uma chave vazada não gera nem revoga chaves.

QUEM PODE:
  - LISTAR: qualquer papel, em qualquer plano;
  - REVOGAR: `owner` e `admin`, em qualquer plano. Revogar é reduzir acesso: a
    empresa que saiu do premium tem que conseguir desligar as chaves que tem;
  - GERAR: `owner` e `admin`, e SÓ no plano premium (K3). Fora dele, 403
    `plano_sem_api`. O plano é conferido antes do papel: membro de empresa
    essencial recebe `plano_sem_api`.
  `membro` e token sem papel só veem a lista (403 `papel_insuficiente` ao
  tentar gerar ou revogar).

O tenant vem do token. Um id de chave de OUTRA empresa responde o mesmo 404 de
um id que não existe.

A regra da chave (geração, hash, limite de cinco ativas) mora em
`accounts/chaves_api.py`; este módulo só expõe. A chave inteira passa por aqui
uma única vez, na resposta do POST, e não é logada.

As três rotas entram no registro de acesso (`registro_acesso.py`): empresa,
rota, papel e instante, quando a operação é atendida.

Este módulo NÃO importa de `app.py` (é o `app.py` que o monta).
"""

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from ..accounts import chaves_api, get_conta
from ..accounts.auth import PLANO_PREMIUM, exigir_papel, exigir_plano_premium
from . import datas, registro_acesso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["integracao"])

PAPEIS_QUE_GERAM = ("owner", "admin")
AVISO_DA_CHAVE = "Guarde esta chave agora. Ela não será mostrada de novo."


def _publica(linha: dict) -> dict:
    """A chave como a tela a mostra: datas com fuso e a situação por extenso."""
    return {
        "id": linha["id"],
        "nome": linha["nome"],
        "prefixo": linha["prefixo"],
        "final": linha["final"],
        "criada_em": datas.iso_com_fuso(linha["criada_em"]),
        "criada_por_papel": linha["criada_por_papel"],
        "ultimo_uso_em": datas.iso_com_fuso(linha["ultimo_uso_em"]),
        "revogada_em": datas.iso_com_fuso(linha["revogada_em"]),
        "situacao": "revogada" if linha["revogada_em"] else "ativa",
        "usos_hoje": linha["usos_hoje"],
    }


def _422(motivo: str, detalhe: str, campo: str) -> HTTPException:
    return HTTPException(status_code=422, detail={
        "motivo": motivo, "detalhe": detalhe, "campo": campo})


def _404() -> HTTPException:
    # Chave inexistente e chave de OUTRA empresa: o mesmo 404, o mesmo texto.
    return HTTPException(status_code=404, detail={
        "motivo": "chave_nao_encontrada",
        "detalhe": "não há chave com este id nesta empresa"})


@router.get("/integracao/chaves")
async def listar_chaves(conta: dict = Depends(get_conta)) -> dict:
    """As chaves da empresa do token, ativas e revogadas, da mais nova à mais
    antiga. Qualquer papel lê, em qualquer plano. A tela usa três campos para
    mostrar ou esconder os botões (quem decide é o POST e o DELETE):
    `pode_revogar` (o papel revoga), `plano_permite_gerar` (a empresa é premium)
    e `pode_gerar` (o papel gera E a empresa é premium)."""
    tenant_id = conta["tenant_id"]
    agora = datas.agora_local()
    chaves = [_publica(linha) for linha in chaves_api.listar(tenant_id, agora)]
    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_CHAVES_LISTAR,
                              conta.get("papel"), agora)
    papel_gera = conta.get("papel") in PAPEIS_QUE_GERAM
    premium = conta.get("plano") == PLANO_PREMIUM
    return {
        "chaves": chaves,
        "ativas": sum(1 for c in chaves if c["situacao"] == "ativa"),
        "limite_ativas": chaves_api.MAX_ATIVAS,
        "pode_revogar": papel_gera,
        "plano_permite_gerar": premium,
        "pode_gerar": papel_gera and premium,
    }


@router.post("/integracao/chaves", status_code=201)
async def criar_chave(response: Response, corpo: dict = Body(...),
                      conta: dict = Depends(get_conta)) -> dict:
    """Cria uma chave com o nome dado e devolve a chave inteira em
    `chave_inteira` — a única vez em que ela aparece. Só no plano premium (403
    `plano_sem_api`). 409 `limite_de_chaves` se a empresa já tem cinco ativas."""
    exigir_plano_premium(conta)
    papel = exigir_papel(conta, *PAPEIS_QUE_GERAM)
    tenant_id = conta["tenant_id"]
    extras = sorted(set(corpo) - {"nome"})
    if extras:
        raise _422("campo_desconhecido", "o corpo aceita só `nome`", extras[0])
    if "nome" not in corpo:
        raise _422("nome_invalido", "`nome` é obrigatório", "nome")
    agora = datas.agora_local()
    try:
        chave, linha = chaves_api.criar(tenant_id, corpo["nome"], papel, agora)
    except chaves_api.NomeInvalido as e:
        raise _422("nome_invalido", str(e), "nome") from e
    except chaves_api.LimiteDeChaves:
        raise HTTPException(status_code=409, detail={
            "motivo": "limite_de_chaves",
            "detalhe": f"a empresa já tem {chaves_api.MAX_ATIVAS} chaves ativas, que é o "
                       "máximo; revogue uma para gerar outra"}) from None
    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_CHAVES_CRIAR, papel, agora)
    # A resposta carrega o segredo: nenhum intermediário deve guardá-la.
    response.headers["Cache-Control"] = "no-store"
    return {"chave": _publica(linha), "chave_inteira": chave, "aviso": AVISO_DA_CHAVE}


@router.delete("/integracao/chaves/{chave_id}")
async def revogar_chave(chave_id: str, conta: dict = Depends(get_conta)) -> dict:
    """Revoga a chave: a requisição seguinte com ela já recebe 401. Revogar
    duas vezes devolve 200 com a data ORIGINAL (`ja_estava_revogada: true`).
    Vale em qualquer plano: só o papel é conferido."""
    papel = exigir_papel(conta, *PAPEIS_QUE_GERAM)
    tenant_id = conta["tenant_id"]
    agora = datas.agora_local()
    resultado = chaves_api.revogar(tenant_id, chave_id, agora)
    if resultado is None:
        raise _404()
    linha, ja_estava = resultado
    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_CHAVES_REVOGAR, papel, agora)
    return {"chave": _publica(linha), "ja_estava_revogada": ja_estava}

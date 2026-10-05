"""crai/api/busca.py — a busca do topo do painel (Rodada 4, Fase 2).

`GET /busca?q=`: a empresa digita um nome ou um identificador e recebe os
CLIENTES da base dela e os CICLOS de cobrança que batem, para ir direto a eles.

O QUE É PROCURADO
    clientes  na base importada da empresa: o nome CONTÉM o texto, ou o
              identificador que a empresa usa (ou o id da recorrência) COMEÇA
              por ele;
    ciclos    o id da recorrência começa pelo texto (a mesma busca de
              `GET /ciclos?q=`), mais os ciclos dos clientes achados pelo nome.

O QUE NUNCA SAI. Telefone, e-mail, CPF, chave Pix, fee. De um cliente saem o
identificador, o nome, a mensalidade, se cancelou e se pediu para não ser
contatado; de um ciclo, o que a lista de ciclos já mostra.

DE QUEM. Só da empresa do token: não há identificador de empresa na rota, e as
duas leituras filtram pelo tenant. O texto que só existe na base de outra
empresa devolve as duas listas vazias, igual ao texto que não existe.

Fora do plano premium a lista de clientes vem vazia (a página de clientes é do
premium); os ciclos vêm, com o nome do cliente.

A busca lê o nome do cliente final: entra no registro de acesso.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..accounts import get_conta
from ..accounts.auth import PLANO_PREMIUM
from ..churn_voluntary import clientes_importados
from ..dunning import ciclo_cobranca as cc
from . import ciclos as ciclos_api
from . import datas, registro_acesso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["busca"])

MINIMO_DE_LETRAS = 2
MAXIMO_DE_LETRAS = 64
LIMITE = 8


def _422(motivo: str, detalhe: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"motivo": motivo, "detalhe": detalhe, "campo": "q"})


def _cliente_publico(linha: dict, marcados: dict) -> dict:
    return {"id": linha["customer_id_externo"], "nome": linha.get("nome"),
            "mrr": linha.get("mrr"), "cancelado": bool(linha.get("cancelado_em")),
            "nao_contatar": linha["customer_id_externo"] in marcados}


def _ciclo_publico(linha: dict) -> dict:
    return {k: linha[k] for k in ("id", "id_recorrencia", "cliente_nome", "status", "estado",
                                  "valor_cobranca", "causa_legivel", "atualizado_em")}


@router.get("/busca")
async def buscar(q: str = "", conta: dict = Depends(get_conta)) -> dict:
    """Procura `?q=` (de 2 a 64 caracteres) nos clientes e nos ciclos da
    empresa do token. Devolve `{q, clientes, ciclos, limite}`, com no máximo
    `limite` de cada. Nenhum contato na resposta."""
    tenant_id = conta["tenant_id"]
    texto = (q or "").strip()
    if len(texto) < MINIMO_DE_LETRAS:
        raise _422("busca_curta", f"digite pelo menos {MINIMO_DE_LETRAS} caracteres")
    if len(texto) > MAXIMO_DE_LETRAS:
        raise _422("busca_longa", f"no máximo {MAXIMO_DE_LETRAS} caracteres")

    try:
        da_base = clientes_importados.buscar(tenant_id, texto, LIMITE)
        marcados = clientes_importados.marcados_nao_contatar(tenant_id) if da_base else {}
    except clientes_importados.ConfiguracaoAusente:
        da_base, marcados = [], {}

    # Os ciclos: pelo id da recorrência, e os dos clientes achados pelo nome.
    achados = {c["id"]: c for c in cc.listar_ciclos(tenant_id, texto=texto, limite=LIMITE)}
    for cliente in da_base:
        if cliente.get("id_recorrencia"):
            for c in cc.listar_ciclos(tenant_id, texto=cliente["id_recorrencia"], limite=LIMITE):
                if c["id_recorrencia"] == cliente["id_recorrencia"]:
                    achados.setdefault(c["id"], c)
    ciclos = sorted(achados.values(), key=lambda c: (str(c["atualizado_em"]), c["id"]),
                    reverse=True)[:LIMITE]
    nomes = ciclos_api._nomes(tenant_id, ciclos)

    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_BUSCA, conta.get("papel"),
                              datas.agora_local())
    premium = conta.get("plano") == PLANO_PREMIUM
    return {"q": texto,
            "clientes": [_cliente_publico(l, marcados) for l in da_base] if premium else [],
            "ciclos": [_ciclo_publico(ciclos_api._linha_publica(c, nomes)) for c in ciclos],
            "limite": LIMITE}

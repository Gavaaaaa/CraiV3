"""crai/api/clientes.py — sincronização de clientes, um a um ou em lote.

O caminho (2) do self-service tinha só a planilha (`POST /clientes/importar`,
em `app.py`). Estas rotas são o mesmo caminho para quem tem um backend: o
sistema do cliente cria, atualiza e cancela clientes na CRAI conforme eles
mudam lá, sem subir arquivo.

    POST   /clientes           upsert de um cliente (existe → atualiza; não → cria)
    POST   /clientes/lote      sincronização em massa; linha torta vira `rejeitados`
    PATCH  /clientes/{id}      mudança parcial: plano, MRR, perfil, comportamento
    DELETE /clientes/{id}      CANCELOU — soft delete; a linha fica (ver a rota)

O QUE É REUSADO, e não reescrito:
  autenticação   `accounts.get_tenant_id` — o tenant vem do JWT, nunca do corpo
  idempotência   `api/idempotencia.CLIENTES_API` — header `Idempotency-Key`
  validação      `importacao.validar_linha` / `validar_linhas` — o MESMO crivo
                 da planilha. Dois vocabulários para a mesma entidade divergem
                 na primeira mudança; aqui só há um.
  persistência   `clientes_importados` — as quatro operações do Bloco C

Este módulo NÃO importa de `app.py` (é o `app.py` que o monta com
`include_router`); tudo de que precisa está nos módulos acima.

CONTRATO: `docs/CONTRATO_CLIENTES_API.md`. Quem muda uma resposta aqui muda
o contrato lá.

IDEMPOTÊNCIA. O header `Idempotency-Key` é opcional. Com ele, a mesma chave
(por tenant, método e caminho) dentro da janela de 7 dias devolve 200 com
`reenvio: true` e o estado ATUAL do cliente, sem aplicar a operação de novo.
A chave só é registrada depois que o corpo passou na validação: um 422 não
consome a chave, e a correção com a mesma chave entra normalmente. Sem o
header, cada requisição é aplicada — o upsert e o cancelamento são
naturalmente idempotentes de qualquer jeito; quem realmente precisa do header
é o `PATCH` com valor relativo do lado de lá e o `/lote`.

RÉGUA. Nesta etapa a régua de risco continua sendo recalculada em lote — a
cada `GET /insights`, inteira, a partir da base. Nenhuma destas rotas
recalcula nada. As respostas já trazem `regua_calculada_em` (último cálculo
em lote deste tenant neste processo; `null` se ainda não houve) e
`regua_versao` (`batch_scoring.REGUA_VERSAO`), para que o contrato não mude
quando a régua incremental entrar.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request

from ..accounts import get_tenant_id
from ..churn_voluntary import batch_scoring, clientes_importados, importacao
from .idempotencia import CLIENTES_API, chave_do_evento

logger = logging.getLogger(__name__)

router = APIRouter(tags=["clientes"])

HEADER_IDEMPOTENCIA = "Idempotency-Key"
CAMPO_REATIVAR = "reativar"
CAMPO_MOTIVO = "motivo"


# ── Peças comuns ──────────────────────────────────────────────────────────

def _422(motivo: str, detalhe: str, campo: Optional[str] = None) -> HTTPException:
    detail = {"motivo": motivo, "detalhe": detalhe}
    if campo is not None:
        detail["campo"] = campo
    return HTTPException(status_code=422, detail=detail)


def _404() -> HTTPException:
    # Cliente inexistente e cliente de OUTRO tenant recebem o mesmo 404, com o
    # mesmo texto: um 403 confirmaria a existência do cliente para quem não
    # deveria saber que ele existe.
    return HTTPException(status_code=404, detail={
        "motivo": "cliente_nao_encontrado",
        "detalhe": "não há cliente com este id nesta empresa"})


def _500_base(e: Exception) -> HTTPException:
    logger.error("[CLIENTES-API] %s", e)
    return HTTPException(status_code=500, detail={
        "motivo": "base_nao_configurada", "detalhe": str(e)})


def _regua(tenant_id: str) -> dict:
    return {
        "regua_calculada_em": batch_scoring.ultimo_calculo_da_regua(tenant_id),
        "regua_versao": batch_scoring.REGUA_VERSAO,
    }


def _publico(linha: Optional[dict]) -> Optional[dict]:
    """A linha sem `tenant_id`: o token já diz de quem é."""
    if linha is None:
        return None
    return {k: v for k, v in linha.items() if k != "tenant_id"}


def _resposta(tenant_id: str, linha: Optional[dict], **extra) -> dict:
    return {"cliente": _publico(linha), **_regua(tenant_id), **extra}


def _exigir_objeto(corpo, nome: str = "corpo") -> dict:
    if not isinstance(corpo, dict):
        raise _422("corpo_invalido", f"{nome} precisa ser um objeto JSON")
    return corpo


def _recusar_campos_desconhecidos(corpo: dict, aceitos: tuple, dica: str = "") -> None:
    desconhecidos = sorted(set(corpo) - set(aceitos))
    if desconhecidos:
        raise _422("campo_desconhecido",
                   f"campo(s) não reconhecido(s): {', '.join(desconhecidos)}. "
                   f"Aceitos: {', '.join(aceitos)}{dica}",
                   campo=desconhecidos[0])


def _validar_cliente(valores: dict) -> dict:
    """`importacao.validar_linha` como 422. Um cliente que chega pela API passa
    exatamente pelo mesmo crivo que uma linha de planilha."""
    cliente, motivo = importacao.validar_linha(valores)
    if motivo:
        raise _422("cliente_invalido", motivo)
    return cliente


def _e_reenvio(tenant_id: str, request: Request, chave: Optional[str]) -> bool:
    """`True` se esta (tenant, método, caminho, chave) já foi vista.

    Chamar SÓ depois da validação do corpo: registrar antes consumiria a
    chave num 422, e a correção com a mesma chave seria tratada como reenvio.
    """
    if chave is None or not chave.strip():
        return False
    return not CLIENTES_API.registrar_se_novo(chave_do_evento(
        tenant_id, request.method, request.url.path, chave.strip()))


def _com_base(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except clientes_importados.ConfiguracaoAusente as e:
        raise _500_base(e) from e


# ── POST /clientes ────────────────────────────────────────────────────────

@router.post("/clientes")
async def upsert_cliente(
    request: Request,
    corpo: dict = Body(...),
    tenant_id: str = Depends(get_tenant_id),
    idempotency_key: Optional[str] = Header(default=None, alias=HEADER_IDEMPOTENCIA),
) -> dict:
    """Upsert de UM cliente. Existe → atualiza a foto; não existe → cria.

    Corpo: os mesmos campos da planilha (`customer_id_externo`, `mrr`,
    `billing_profile` obrigatórios; `days_since_last`, `features_used_30d`,
    `email` opcionais) e, opcionalmente, `reativar: true`.

    NÃO ressuscita quem cancelou: o upsert atualiza MRR, perfil e
    comportamento, mas `cancelado_em` fica. `reativar: true` — e só isso —
    limpa `cancelado_em` e `motivo_cancelamento`. Um cliente que voltou é um
    cliente que voltou; o backend do cliente diz isso de propósito, não por
    acidente de sincronização.
    """
    corpo = _exigir_objeto(corpo)
    _recusar_campos_desconhecidos(corpo, importacao.ESPERADAS + (CAMPO_REATIVAR,))
    reativar = corpo.get(CAMPO_REATIVAR, False)
    if not isinstance(reativar, bool):
        raise _422("reativar_invalido", "`reativar` precisa ser true ou false",
                   campo=CAMPO_REATIVAR)
    cliente = _validar_cliente({k: v for k, v in corpo.items() if k != CAMPO_REATIVAR})

    if _e_reenvio(tenant_id, request, idempotency_key):
        linha = _com_base(clientes_importados.obter, tenant_id, cliente["customer_id_externo"])
        return _resposta(tenant_id, linha, reenvio=True)

    linha = _com_base(clientes_importados.upsert_um, tenant_id, cliente, reativar=reativar)
    logger.info("[CLIENTES-API] tenant=%s upsert id=%s reativar=%s",
                tenant_id, cliente["customer_id_externo"], reativar)
    return _resposta(tenant_id, linha, reenvio=False)


# ── POST /clientes/lote ───────────────────────────────────────────────────

@router.post("/clientes/lote")
async def sincronizar_lote(
    request: Request,
    corpo: dict = Body(...),
    tenant_id: str = Depends(get_tenant_id),
    idempotency_key: Optional[str] = Header(default=None, alias=HEADER_IDEMPOTENCIA),
) -> dict:
    """Sincronização em massa: `{"clientes": [{...}, ...]}`.

    Mesmo contrato de `/clientes/importar`: item torto vira entrada de
    `rejeitados` com `indice` (posição no array, a partir de 0) e `motivo`;
    os outros entram. Um cliente errado no meio de mil não impede os outros
    999. Dentro do lote, id repetido: o último vence. O lote NUNCA reativa —
    é uma foto do cadastro, não uma declaração de que todo mundo está ativo.
    """
    corpo = _exigir_objeto(corpo)
    _recusar_campos_desconhecidos(corpo, ("clientes",))
    itens = corpo.get("clientes")
    if not isinstance(itens, list):
        raise _422("lote_invalido", "`clientes` precisa ser uma lista de objetos",
                   campo="clientes")
    if not itens:
        raise _422("lote_vazio", "`clientes` está vazio", campo="clientes")
    if len(itens) > importacao.LINHAS_MAXIMAS:
        raise HTTPException(status_code=413, detail={
            "motivo": "linhas_demais",
            "detalhe": f"{len(itens)} clientes; o máximo por lote é "
                       f"{importacao.LINHAS_MAXIMAS}"})

    # Campo desconhecido num item é motivo de rejeição DO ITEM, não do lote:
    # o mesmo tratamento que uma linha com MRR torto.
    linhas, ruins_de_forma = [], {}
    for i, item in enumerate(itens):
        if isinstance(item, dict):
            extras = sorted(set(item) - set(importacao.ESPERADAS))
            if extras:
                ruins_de_forma[i] = f"campo(s) não reconhecido(s): {', '.join(extras)}"
                linhas.append(None)             # rejeitado por `validar_linhas`
                continue
        linhas.append(item)
    clientes, ruins = importacao.validar_linhas(linhas)
    rejeitados = [{"indice": i, "motivo": ruins_de_forma.get(i, motivo)}
                  for i, motivo in ruins]

    if _e_reenvio(tenant_id, request, idempotency_key):
        return {"importados": 0, "rejeitados": [], "linhas_sem_dado_comportamental": 0,
                "reenvio": True, **_regua(tenant_id)}

    importados = _com_base(clientes_importados.gravar, tenant_id, clientes)
    logger.info("[CLIENTES-API] tenant=%s lote importados=%d rejeitados=%d",
                tenant_id, importados, len(rejeitados))
    return {
        "importados": importados,
        "rejeitados": rejeitados,
        "linhas_sem_dado_comportamental": importacao.contar_sem_comportamento(clientes),
        "reenvio": False,
        **_regua(tenant_id),
    }


# ── PATCH /clientes/{id} ──────────────────────────────────────────────────

@router.patch("/clientes/{customer_id_externo}")
async def atualizar_cliente(
    customer_id_externo: str,
    request: Request,
    corpo: dict = Body(...),
    tenant_id: str = Depends(get_tenant_id),
    idempotency_key: Optional[str] = Header(default=None, alias=HEADER_IDEMPOTENCIA),
) -> dict:
    """Mudança parcial: só o que veio no corpo muda.

    Aceita `mrr`, `billing_profile`, `days_since_last`, `features_used_30d`,
    `email`. Campo ausente fica como está — mudar o MRR não toca nas colunas
    comportamentais. Campo presente com `null` limpa (só os opcionais). A
    validação é a da planilha, aplicada sobre a linha JÁ MESCLADA com o que
    veio: o resultado do PATCH é sempre um cliente que a planilha aceitaria.
    Um cliente cancelado pode ser atualizado; continua cancelado.
    """
    corpo = _exigir_objeto(corpo)
    if not corpo:
        raise _422("corpo_vazio", "o PATCH precisa de pelo menos um campo")
    _recusar_campos_desconhecidos(
        corpo, clientes_importados.COLUNAS_PARCIAIS,
        dica=". O id vai no caminho, não no corpo")

    atual = _com_base(clientes_importados.obter, tenant_id, customer_id_externo)
    if atual is None:
        raise _404()
    mesclado = {campo: atual.get(campo) for campo in importacao.ESPERADAS}
    mesclado.update(corpo)
    validado = _validar_cliente(mesclado)
    campos = {k: validado[k] for k in corpo}

    if _e_reenvio(tenant_id, request, idempotency_key):
        return _resposta(tenant_id, atual, reenvio=True)

    linha = _com_base(clientes_importados.atualizar_parcial, tenant_id,
                      customer_id_externo, campos)
    if linha is None:                       # sumiu entre a leitura e a escrita
        raise _404()
    logger.info("[CLIENTES-API] tenant=%s patch id=%s campos=%s",
                tenant_id, customer_id_externo, sorted(campos))
    return _resposta(tenant_id, linha, reenvio=False)


# ── DELETE /clientes/{id} ─────────────────────────────────────────────────

@router.delete("/clientes/{customer_id_externo}")
async def cancelar_cliente(
    customer_id_externo: str,
    request: Request,
    corpo: Optional[dict] = Body(default=None),
    tenant_id: str = Depends(get_tenant_id),
    idempotency_key: Optional[str] = Header(default=None, alias=HEADER_IDEMPOTENCIA),
) -> dict:
    """O cliente CANCELOU. É um evento de churn, não uma exclusão.

    `cancelado_em` é a semente do treino com desfecho observado: o único
    rótulo do sistema que não foi produzido pela própria regra de risco.

    Grava `cancelado_em` e, se vier `{"motivo": "..."}` no corpo,
    `motivo_cancelamento` (≤ 500 caracteres; pode conter dado pessoal — não
    vai para log nem para listagem agregada). A linha PERMANECE: apagá-la
    destruiria o rótulo. O cliente sai do ranking de risco pelo padrão de
    `clientes_importados.listar()` e continua no histórico.

    Cancelar duas vezes devolve 200 com a data ORIGINAL (`ja_estava_cancelado:
    true`), não 404 nem 409. Cliente inexistente → 404. Cliente de OUTRO
    tenant → 404 também, nunca 403.
    """
    motivo = None
    if corpo is not None:
        corpo = _exigir_objeto(corpo)
        _recusar_campos_desconhecidos(corpo, (CAMPO_MOTIVO,))
        motivo = corpo.get(CAMPO_MOTIVO)
        if motivo is not None:
            if not isinstance(motivo, str):
                raise _422("motivo_invalido", "`motivo` precisa ser texto", campo=CAMPO_MOTIVO)
            motivo = motivo.strip() or None
            if motivo and len(motivo) > clientes_importados.MOTIVO_CANCELAMENTO_MAX:
                raise _422("motivo_longo",
                           f"`motivo` tem {len(motivo)} caracteres; o máximo é "
                           f"{clientes_importados.MOTIVO_CANCELAMENTO_MAX}",
                           campo=CAMPO_MOTIVO)

    antes = _com_base(clientes_importados.obter, tenant_id, customer_id_externo)
    if antes is None:
        raise _404()
    ja_estava = antes["cancelado_em"] is not None

    if _e_reenvio(tenant_id, request, idempotency_key):
        return _resposta(tenant_id, antes, ja_estava_cancelado=ja_estava, reenvio=True)

    linha = _com_base(clientes_importados.cancelar, tenant_id, customer_id_externo, motivo)
    if linha is None:
        raise _404()
    # Sem o motivo no log, de propósito: é texto livre do backend do cliente.
    logger.info("[CLIENTES-API] tenant=%s cancelado id=%s ja_estava=%s",
                tenant_id, customer_id_externo, ja_estava)
    return _resposta(tenant_id, linha, ja_estava_cancelado=ja_estava, reenvio=False)

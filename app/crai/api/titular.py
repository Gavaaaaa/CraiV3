"""crai/api/titular.py — o direito à explicação (LGPD, Art. 20) para a CONTROLADORA.

    GET /titular/explicacao/{sujeito_id}     autenticada por tenant

QUEM É QUEM. O titular é o cliente final da empresa cliente. A empresa
cliente é a CONTROLADORA; a CRAI é OPERADORA. O pedido de revisão do titular
(Art. 20, caput) chega à controladora, e é ela quem responde. Esta rota
existe para que ela consiga: devolve, para um `sujeito_id` do SEU tenant, as
decisões automatizadas registradas na trilha (`retention_log.
decisoes_automatizadas`), cada uma com a explicação legível montada no
momento da decisão. **Não existe rota pública para o titular**, de propósito.

O QUE O §1º PEDE E O QUE ESTA ROTA ENTREGA. O Art. 20 §1º obriga o
controlador a fornecer "informações claras e adequadas a respeito dos
critérios e dos procedimentos utilizados para a decisão automatizada,
observados os segredos comercial e industrial". Por isso:

  ENTRA    quais features pesaram, em que direção e com que valores
           (`entradas`, `contribuicoes`); o que foi decidido (`saida`); qual
           modelo e qual versão de artefato decidiram; a frase em pt-BR
           (`explicacao`); e a integridade da trilha (`cadeia`).
  NÃO ENTRA — SEGREDO COMERCIAL E INDUSTRIAL (Art. 20 §1º, parte final) —
           os coeficientes e pesos internos dos modelos, os hiperparâmetros
           de treino, e os estados alpha/beta do bandit. A trilha já não os
           grava (`retention_log.CHAVES_FORA_DA_TRILHA`); a rota os tira de
           novo na saída, por defesa em profundidade. É a diferença entre
           uma escolha defensável e uma omissão: o que pesou é dito; como o
           modelo foi construído, não.

404 IDÊNTICO. Sujeito sem decisão registrada → 404. Sujeito de OUTRO tenant
→ 404 também, nunca 403, com o MESMO corpo: responder diferente confirmaria
a existência daquela pessoa para quem não deveria saber que ela existe. É o
mesmo padrão das rotas de cliente (`api/clientes.py`).

Este módulo NÃO importa de `app.py` (é o `app.py` que o monta).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..accounts import get_tenant_id
from ..churn_voluntary import retention_log as trilha

logger = logging.getLogger(__name__)

router = APIRouter(tags=["titular"])

LIMITE_PADRAO = 20
LIMITE_MAXIMO = 200

# O que a resposta declara sobre a própria base legal — texto fixo, para a
# controladora saber o que está (e o que não está) recebendo.
BASE_LEGAL = {
    "dispositivo": "LGPD, Lei 13.709/2018, Art. 20, caput e §1º",
    "papel_da_crai": "operadora; a controladora é a empresa cliente, e é ela quem responde ao titular",
    "o_que_entra": "critérios e procedimento: as features consideradas, com valores e direção; a "
                   "decisão; o modelo e a versão do artefato; a explicação em português",
    "o_que_nao_entra": "segredo comercial e industrial (Art. 20 §1º, parte final): coeficientes e "
                       "pesos internos dos modelos, hiperparâmetros de treino e os estados "
                       "alpha/beta do algoritmo de ofertas",
}


def _404() -> HTTPException:
    return HTTPException(status_code=404, detail={
        "motivo": "sujeito_sem_decisao",
        "detalhe": "não há decisão automatizada registrada para este identificador nesta empresa"})


def _publica(d: dict) -> dict:
    """A linha da trilha como a controladora a recebe.

    `_sem_dado_cru` de novo na saída: a trilha já grava limpa, mas uma linha
    antiga (ou uma chave nova na lista de segredos) não pode vazar por aqui.
    """
    return {
        "id": d["id"],
        "decidido_em": d["decidido_em"],
        "dominio": d["dominio"],
        "tipo_decisao": d["tipo_decisao"],
        "modelo": d["modelo"],
        "modelo_versao": d.get("modelo_versao"),
        "explicacao": d["explicacao"],
        "entradas": trilha._sem_dado_cru(d.get("entradas") or {}),
        "saida": trilha._sem_dado_cru(d.get("saida") or {}),
        "contribuicoes": trilha._sem_dado_cru(d["contribuicoes"]) if d.get("contribuicoes") else None,
        "hash_linha": d["hash_linha"],
        "hash_anterior": d.get("hash_anterior"),
    }


@router.get("/titular/explicacao/{sujeito_id}")
async def explicacao_do_titular(
    sujeito_id: str,
    limite: Optional[int] = None,
    antes_de: Optional[int] = None,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """As decisões automatizadas sobre um sujeito DESTE tenant, mais recentes
    primeiro, com paginação — para a controladora responder ao titular.

    Art. 20 §1º: entram os critérios e o procedimento (features com valores
    e direção, decisão, modelo e versão, explicação em pt-BR); NÃO entram
    coeficientes, hiperparâmetros nem os estados alpha/beta do bandit —
    segredo comercial e industrial, ressalvado pelo mesmo parágrafo.

    `?limite=N` (1..200, padrão 20) e `?antes_de=<id>` paginam; a resposta
    traz `proximo_antes_de` para a página seguinte. `cadeia` é o
    `verificar_cadeia` do tenant inteiro, para a controladora saber se a
    trilha que está lendo está íntegra. 404 para sujeito sem decisão e para
    sujeito de outro tenant, com corpo idêntico.
    """
    limite = LIMITE_PADRAO if limite is None else limite
    if limite < 1 or limite > LIMITE_MAXIMO:
        raise HTTPException(status_code=422, detail={
            "motivo": "limite_invalido", "campo": "limite",
            "detalhe": f"esperado inteiro entre 1 e {LIMITE_MAXIMO}"})
    if antes_de is not None and antes_de < 1:
        raise HTTPException(status_code=422, detail={
            "motivo": "antes_de_invalido", "campo": "antes_de",
            "detalhe": "esperado o id da última decisão recebida"})

    pagina = trilha.decisoes_do_sujeito(tenant_id, sujeito_id, limite=limite + 1, antes_de=antes_de)
    if not pagina and (antes_de is None
                       or not trilha.decisoes_do_sujeito(tenant_id, sujeito_id, limite=1)):
        raise _404()
    tem_mais = len(pagina) > limite
    pagina = pagina[:limite]

    logger.info("[ART20] tenant=%s explicacao sujeito=%s decisoes=%d", tenant_id, sujeito_id, len(pagina))
    return {
        "sujeito_id": sujeito_id,
        "decisoes": [_publica(d) for d in pagina],
        "paginacao": {"limite": limite, "antes_de": antes_de,
                      "proximo_antes_de": pagina[-1]["id"] if tem_mais and pagina else None,
                      "tem_mais": tem_mais},
        "cadeia": trilha.verificar_cadeia(tenant_id),
        "base_legal": BASE_LEGAL,
    }

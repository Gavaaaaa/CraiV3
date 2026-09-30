"""crai/api/ciclos.py — o involuntário visto de fora: a aba do dashboard.

    GET /ciclos                           lista paginada, com filtros
    GET /ciclos/{id}                      um ciclo e a linha do tempo dele
    GET /metrics/involuntario/mes         o mês: valor líquido, contagens, taxa
    GET /metrics/involuntario/serie       um ponto por dia

POR QUE ISTO EXISTE (F10–F12 do diagnóstico de 29/09/2026). Desde a Etapa 1 o
ciclo de cobrança guarda quase tudo que uma tela precisa, mas nenhuma rota o
lia para fora: só existia o agregado `/metrics/recovery`, do dataset de treino.

AS QUATRO REGRAS QUE ESTE MÓDULO GARANTE:

  R10  a tela vê quatro status — em análise, em processo, recuperado,
       encerrado sem recuperação — pela tradução única de
       `ciclo_cobranca.status_da_tela`, e nenhum ciclo some: perdido e
       descartado aparecem como encerrados, com o motivo.
  R11  dois campos de valor, cada um com um sentido só, que nunca muda:
       `valor_cobranca` é sempre o valor bruto da cobrança; `valor_liquido`
       é o que a empresa recebeu (`valor_cobranca − fee`), preenchido só em
       ciclo recuperado e `null` nos outros estados. A fee em si não sai em
       nenhuma resposta daqui, e as métricas somam o líquido.
  R12  toda leitura filtra pelo tenant do token. Token da empresa A com o id
       de um ciclo da empresa B responde 404 com o MESMO corpo de um id que
       não existe — um 403 confirmaria que o ciclo existe.
  F9   toda data sai em ISO 8601 COM fuso (`api/datas.py`); as datas do ciclo
       são gravadas em hora local sem fuso, as da trilha em UTC.

A LINHA DO TEMPO JUNTA DOIS BANCOS sem mudar o schema de nenhum. O ciclo mora em
`recovery_cycles.db`; as decisões do Art. 20 em `retention_cycles.db`, sem
`ciclo_id` (F3/F4). A junção é em Python: as decisões do MESMO sujeito
(`id_recorrencia`) cujo instante cai entre a abertura e o desfecho do ciclo.
Dois ciclos do mesmo mandato abertos ao mesmo tempo tornam essa junção ambígua
— a resposta diz isso (`trilha_ambigua`) em vez de adivinhar. Só um `ciclo_id`
na trilha resolveria, e a trilha não muda nesta etapa.

Este módulo NÃO importa de `app.py` (é o `app.py` que o monta).
"""

import base64
import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path

from ..accounts import get_tenant_id
from ..agent.pix_codes import CAUSA_LEGIVEL
from ..churn_voluntary import retention_log as trilha
from ..dunning import ciclo_cobranca as cc
from . import datas

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ciclos"])

LIMITE_PADRAO = 50
LIMITE_MAXIMO = 200
DIAS_PADRAO = 30
DIAS_MAXIMO = 365
# Folga na junção ciclo × trilha: a decisão de risco é carimbada no nó do
# grafo, instantes depois de `aberto_em`; o relógio da trilha é outro.
FOLGA_DA_JUNCAO = timedelta(minutes=5)
DECISOES_POR_SUJEITO = 500
ID_MAXIMO = 2 ** 63 - 1


# ── Peças comuns ──────────────────────────────────────────────────────────

def _404() -> HTTPException:
    # Ciclo inexistente e ciclo de OUTRO tenant: o mesmo 404, o mesmo corpo (R12).
    return HTTPException(status_code=404, detail={
        "motivo": "ciclo_nao_encontrado",
        "detalhe": "não há ciclo com este id nesta empresa"})


def _422(motivo: str, detalhe: str, campo: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"motivo": motivo, "detalhe": detalhe,
                                                  "campo": campo})


def _data_do_filtro(bruto: Optional[str], campo: str) -> Optional[datetime]:
    """`AAAA-MM-DD` (meia-noite local) ou ISO 8601 com ou sem fuso → hora local."""
    if bruto is None or not bruto.strip():
        return None
    local = datas.para_local(bruto.strip())
    if local is None:
        raise _422("data_invalida", "esperado AAAA-MM-DD ou ISO 8601", campo)
    return local


def _codificar_cursor(linha: dict) -> str:
    bruto = json.dumps({"a": linha["atualizado_em"], "i": linha["id"]}, separators=(",", ":"))
    return base64.urlsafe_b64encode(bruto.encode()).decode().rstrip("=")


def _decodificar_cursor(cursor: str) -> tuple:
    try:
        texto = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        dados = json.loads(texto)
        atualizado_em, ultimo_id = dados["a"], dados["i"]
        if not isinstance(atualizado_em, str) or not isinstance(ultimo_id, int) \
                or isinstance(ultimo_id, bool) or not 1 <= ultimo_id <= ID_MAXIMO:
            raise ValueError
        return atualizado_em, ultimo_id
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise _422("cursor_invalido", "use o `proximo_cursor` da página anterior", "cursor")


def _valor_cobranca(ciclo: dict) -> float:
    """O valor bruto da cobrança que abriu o ciclo. Não muda com o desfecho."""
    return round(float(ciclo["valor"] or 0.0), 2)


def _valor_liquido(ciclo: dict) -> Optional[float]:
    """R11: o que a empresa recebeu — só em ciclo recuperado; `None` nos
    outros estados (não há o que receber, e um número ali seria lido como
    recebido)."""
    if ciclo["estado"] != cc.RECUPERADO:
        return None
    return round(_valor_cobranca(ciclo) - float(ciclo.get("fee") or 0.0), 2)


def _desfecho_em(ciclo: dict) -> Optional[str]:
    return ciclo.get("recuperado_em") or ciclo.get("perdido_em") or ciclo.get("descartado_em")


def _mensagem(ciclo: dict) -> Optional[dict]:
    if not ciclo.get("mensagem_em"):
        return None
    return {"reservada_em": datas.iso_com_fuso(ciclo.get("mensagem_em")),
            "enviada_em": datas.iso_com_fuso(ciclo.get("mensagem_confirmada_em"))}


def _linha_publica(ciclo: dict) -> dict:
    """Um ciclo como a empresa o vê. Sem `fee`, sem `tenant_id` (o token já
    diz de quem é), sem identidade de cobrança do PSP."""
    causa = ciclo.get("causa_original")
    return {
        "id": ciclo["id"],
        "status": ciclo["status"],
        "estado": ciclo["estado"],
        "id_recorrencia": ciclo["id_recorrencia"],
        "valor_cobranca": _valor_cobranca(ciclo),
        "valor_liquido": _valor_liquido(ciclo),
        "causa": causa,
        "causa_legivel": CAUSA_LEGIVEL.get(causa, causa),
        "tentativas_executadas": ciclo["tentativas_executadas"],
        "aberto_em": datas.iso_com_fuso(ciclo["aberto_em"]),
        "atualizado_em": datas.iso_com_fuso(ciclo["atualizado_em"]),
        "desfecho_em": datas.iso_com_fuso(_desfecho_em(ciclo)),
        "motivo_descarte": ciclo.get("motivo_descarte"),
        "mensagem": _mensagem(ciclo),
    }


# ── GET /ciclos ───────────────────────────────────────────────────────────

@router.get("/ciclos")
async def listar_ciclos(
    status: Optional[str] = None,
    desde: Optional[str] = None,
    ate: Optional[str] = None,
    q: Optional[str] = None,
    cursor: Optional[str] = None,
    limite: Optional[int] = None,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """Os ciclos da empresa, do mais recentemente atualizado ao mais antigo.

    `?status=em_analise,em_processo` (os quatro códigos de R10, separados por
    vírgula); `?desde=` e `?ate=` sobre a abertura (`ate` exclusivo; data ou
    ISO 8601); `?q=` prefixo do id da recorrência ou o id exato da cobrança;
    `?limite=` 1..200 (padrão 50); `?cursor=` o `proximo_cursor` da página
    anterior.
    """
    limite = LIMITE_PADRAO if limite is None else limite
    if not 1 <= limite <= LIMITE_MAXIMO:
        raise _422("limite_invalido", f"esperado inteiro entre 1 e {LIMITE_MAXIMO}", "limite")
    lista_status = None
    if status is not None and status.strip():
        lista_status = [s.strip() for s in status.split(",") if s.strip()]
        desconhecidos = sorted(set(lista_status) - set(cc.STATUS_DA_TELA))
        if desconhecidos:
            raise _422("status_invalido", f"desconhecido(s): {', '.join(desconhecidos)}; "
                       f"aceitos: {', '.join(cc.STATUS_DA_TELA)}", "status")
    texto = q.strip() if q else None
    if texto and len(texto) > 128:
        raise _422("busca_longa", "no máximo 128 caracteres", "q")
    pagina = cc.listar_ciclos(
        tenant_id, status=lista_status, desde=_data_do_filtro(desde, "desde"),
        ate=_data_do_filtro(ate, "ate"), texto=texto or None,
        cursor=_decodificar_cursor(cursor) if cursor else None, limite=limite + 1)
    tem_mais = len(pagina) > limite
    pagina = pagina[:limite]
    return {"ciclos": [_linha_publica(c) for c in pagina],
            "proximo_cursor": _codificar_cursor(pagina[-1]) if tem_mais and pagina else None,
            "tem_mais": tem_mais}


# ── GET /ciclos/{id} ──────────────────────────────────────────────────────

def _evento(quando, tipo: str, ordem: int, **dados) -> Optional[dict]:
    instante = datas.com_fuso(quando)
    if instante is None:
        return None
    return {"_instante": instante, "_ordem": ordem, "quando": instante.isoformat(timespec="seconds"),
            "tipo": tipo, "dados": dados}


def _intervalo(ciclo: dict, agora: datetime) -> tuple:
    inicio = datas.com_fuso(ciclo["aberto_em"])
    fim = datas.com_fuso(_desfecho_em(ciclo)) or datas.com_fuso(agora)
    return inicio, fim


def _decisoes_do_ciclo(tenant_id: str, ciclo: dict, agora: datetime) -> tuple[list, bool]:
    """As decisões da trilha deste ciclo, e se a junção é ambígua.

    Mesmo sujeito, instante dentro de [abertura − folga, desfecho + folga].
    Linhas idênticas (mesmo tipo, instante, entradas e saída) aparecem uma vez
    só: são as regravações do defeito 7-D, que a trilha — só de INSERT —
    guarda para sempre."""
    inicio, fim = _intervalo(ciclo, agora)
    inicio, fim = inicio - FOLGA_DA_JUNCAO, fim + FOLGA_DA_JUNCAO
    vistas, decisoes = set(), []
    for d in trilha.decisoes_do_sujeito(tenant_id, ciclo["id_recorrencia"],
                                        limite=DECISOES_POR_SUJEITO):
        if d.get("dominio") != trilha.DOMINIO_INVOLUNTARIO:
            continue
        instante = datas.com_fuso(d.get("decidido_em"))
        if instante is None or not inicio <= instante <= fim:
            continue
        chave = (d["tipo_decisao"], d["decidido_em"],
                 json.dumps(d.get("entradas"), sort_keys=True, default=str),
                 json.dumps(d.get("saida"), sort_keys=True, default=str))
        if chave in vistas:
            continue
        vistas.add(chave)
        decisoes.append(d)
    decisoes.sort(key=lambda d: d["id"])

    ambigua = False
    for outro in cc.ciclos_do_mandato(tenant_id, ciclo["id_recorrencia"]):
        if outro["id"] == ciclo["id"]:
            continue
        o_inicio, o_fim = _intervalo(outro, agora)
        if o_inicio <= fim and inicio <= o_fim:
            ambigua = True
            break
    return decisoes, ambigua


def _diagnostico(decisoes: list) -> Optional[dict]:
    """A decisão de risco do ciclo: a explicação em linguagem simples e as
    (até) cinco contribuições do SHAP gravadas NO MOMENTO da decisão, com o
    rótulo da trilha — nunca o nome cru da feature, nunca recalculado."""
    risco = next((d for d in decisoes if d["tipo_decisao"] == trilha.TIPO_RISCO), None)
    if risco is None:
        return None
    entradas = risco.get("entradas") or {}
    itens = []
    for c in (risco.get("contribuicoes") or [])[:5]:
        nome = c.get("feature")
        valor = entradas.get(nome)
        # `_rotular` devolve "nome = valor" quando não há rótulo: isso é nome
        # cru de feature, e não sai para a tela.
        rotulo = (trilha._rotular(nome, valor, trilha.ROTULOS_DE_FEATURE)
                  if valor is not None else None)
        if rotulo is None or rotulo.startswith(f"{nome} = "):
            rotulo = "fator do diagnóstico sem rótulo legível"
        direcao = c.get("direcao") or c.get("direction")
        itens.append({"fator": rotulo,
                      "efeito": ("aumentou a chance de recuperar" if direcao == "+"
                                 else "reduziu a chance de recuperar" if direcao == "-" else None)})
    return {"decidido_em": datas.iso_com_fuso(risco["decidido_em"]),
            "explicacao": risco["explicacao"],
            "com_modelo": bool(risco.get("contribuicoes")),
            "contribuicoes": itens}


def _linha_do_tempo(ciclo: dict, tentativas: list, decisoes: list) -> list:
    eventos = [
        _evento(ciclo["aberto_em"], "abertura", 0, origem=ciclo.get("origem"),
                causa_legivel=CAUSA_LEGIVEL.get(ciclo.get("causa_original"),
                                                ciclo.get("causa_original")),
                janela_inicio=datas.iso_com_fuso(ciclo.get("janela_inicio")),
                janela_fim=datas.iso_com_fuso(ciclo.get("janela_fim"))),
        _evento(ciclo.get("decidido_em"), "diagnostico", 1, estrategia=ciclo.get("estrategia"),
                recovery_score=ciclo.get("recovery_score"), p_recovery=ciclo.get("p_recovery")),
    ]
    for t in tentativas:
        numero = t["numero"]
        if t.get("disparada_em"):
            eventos.append(_evento(t["disparada_em"], "tentativa_disparada", 2, numero=numero))
        elif t["resultado"] == cc.PENDENTE:
            eventos.append(_evento(t["agendada_para"], "tentativa_agendada", 2, numero=numero))
        if t["resultado"] == cc.CANCELADA:
            eventos.append(_evento(t.get("resultado_em"), "tentativa_cancelada", 3, numero=numero,
                                   motivo=t.get("motivo_cancelamento")))
        elif t["resultado"] != cc.PENDENTE:
            eventos.append(_evento(t.get("resultado_em"), "tentativa_resultado", 3, numero=numero,
                                   resultado=t["resultado"]))
    eventos.append(_evento(ciclo.get("mensagem_em"), "mensagem_reservada", 4))
    eventos.append(_evento(ciclo.get("mensagem_confirmada_em"), "mensagem_enviada", 5))
    if ciclo["estado"] == cc.RECUPERADO:
        eventos.append(_evento(ciclo.get("recuperado_em"), "recuperado", 6,
                               valor_liquido=_valor_liquido(ciclo)))
    elif ciclo["estado"] == cc.PERDIDO:
        eventos.append(_evento(ciclo.get("perdido_em"), "perdido", 6))
    elif ciclo["estado"] == cc.DESCARTADO:
        eventos.append(_evento(ciclo.get("descartado_em"), "descartado", 6,
                               motivo_descarte=ciclo.get("motivo_descarte")))
    for d in decisoes:
        eventos.append(_evento(d["decidido_em"], "decisao", 1, tipo_decisao=d["tipo_decisao"],
                               explicacao=d["explicacao"]))
    eventos = [e for e in eventos if e is not None]
    eventos.sort(key=lambda e: (e["_instante"], e["_ordem"]))
    return [{k: v for k, v in e.items() if not k.startswith("_")} for e in eventos]


@router.get("/ciclos/{ciclo_id}")
async def ler_ciclo(
    ciclo_id: int = Path(..., ge=1, le=ID_MAXIMO),
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    """Um ciclo da empresa e a linha do tempo dele: abertura, diagnóstico (com
    as cinco maiores contribuições em linguagem simples), cada tentativa,
    mensagem, desfecho, e as decisões da trilha do Art. 20 do período.
    404 idêntico para ciclo inexistente e de outra empresa (R12)."""
    ciclo = cc.ciclo_do_tenant(tenant_id, ciclo_id)
    if ciclo is None:
        raise _404()
    agora = datas.agora_local()
    decisoes, ambigua = _decisoes_do_ciclo(tenant_id, ciclo, agora)
    publica = _linha_publica(ciclo)
    publica.update(janela_inicio=datas.iso_com_fuso(ciclo["janela_inicio"]),
                   janela_fim=datas.iso_com_fuso(ciclo["janela_fim"]))
    return {"ciclo": publica,
            "diagnostico": _diagnostico(decisoes),
            "linha_do_tempo": _linha_do_tempo(ciclo, cc.tentativas_do_ciclo(ciclo["id"]),
                                              decisoes),
            "trilha_ambigua": ambigua}


# ── Métricas ──────────────────────────────────────────────────────────────

def _resumo(linhas: list) -> dict:
    """Valor líquido, recuperados, encerrados e taxa sobre os ciclos COM
    desfecho (D-E2-9). Sem desfecho nenhum, a taxa é `null` — não zero: zero
    diria que tudo foi perdido."""
    recuperados = [l for l in linhas if l["estado"] == cc.RECUPERADO]
    encerrados = len(linhas) - len(recuperados)
    liquido = round(sum(_valor_liquido(l) for l in recuperados), 2)
    return {"valor_liquido_recuperado": liquido,
            "recuperados": len(recuperados),
            "encerrados_sem_recuperacao": encerrados,
            "taxa_recuperacao": round(len(recuperados) / len(linhas), 4) if linhas else None}


@router.get("/metrics/involuntario/mes")
async def metricas_do_mes(mes: Optional[str] = None,
                          tenant_id: str = Depends(get_tenant_id)) -> dict:
    """O mês da empresa (`?mes=AAAA-MM`; padrão: o mês corrente no fuso da
    instalação). Fonte: `ciclos_cobranca`, a fonte da verdade do ciclo — não o
    dataset de treino. Valor líquido e taxa sobre os ciclos com desfecho no
    mês; as contagens por status, sobre os ciclos abertos no mês."""
    if mes is None or not mes.strip():
        hoje = datas.agora_local()
        ano, numero = hoje.year, hoje.month
    else:
        try:
            ano, numero = (int(p) for p in mes.strip().split("-"))
            date(ano, numero, 1)
            if len(mes.strip()) != 7:
                raise ValueError
        except ValueError:
            raise _422("mes_invalido", "esperado AAAA-MM", "mes")
    inicio = datetime(ano, numero, 1)
    fim = datetime(ano + (numero == 12), numero % 12 + 1, 1)
    return {"mes": f"{ano:04d}-{numero:02d}",
            "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
            **_resumo(cc.ciclos_com_desfecho(tenant_id, inicio, fim)),
            "ciclos_abertos_no_mes": cc.contagem_por_status(tenant_id, inicio, fim)}


@router.get("/metrics/involuntario/serie")
async def serie_diaria(dias: Optional[int] = None,
                       tenant_id: str = Depends(get_tenant_id)) -> dict:
    """Um ponto por dia, dos últimos `?dias=` (1..365, padrão 30) até hoje,
    pelo dia do DESFECHO. Dias sem desfecho entram com zero e taxa `null`."""
    dias = DIAS_PADRAO if dias is None else dias
    if not 1 <= dias <= DIAS_MAXIMO:
        raise _422("dias_invalido", f"esperado inteiro entre 1 e {DIAS_MAXIMO}", "dias")
    hoje = datas.agora_local().date()
    primeiro = hoje - timedelta(days=dias - 1)
    inicio = datetime.combine(primeiro, datetime.min.time())
    fim = datetime.combine(hoje + timedelta(days=1), datetime.min.time())
    por_dia: dict = {}
    for linha in cc.ciclos_com_desfecho(tenant_id, inicio, fim):
        por_dia.setdefault(str(linha["desfecho_em"])[:10], []).append(linha)
    pontos = []
    for i in range(dias):
        dia = (primeiro + timedelta(days=i)).isoformat()
        pontos.append({"dia": dia, **_resumo(por_dia.get(dia, []))})
    return {"dias": dias, "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
            "pontos": pontos}

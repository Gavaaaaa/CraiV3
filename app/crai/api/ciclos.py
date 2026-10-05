"""crai/api/ciclos.py — o involuntário visto de fora: a aba do dashboard.

    GET /ciclos                           lista paginada, com filtros
    GET /ciclos/{id}                      um ciclo e a linha do tempo dele
    GET /metrics/involuntario/mes         o mês: valor líquido, contagens, taxa
    GET /metrics/involuntario/serie       um ponto por dia
    POST /ciclos/{id}/mensagens/escolher  a empresa escolhe uma das sugestões
    POST /ciclos/{id}/mensagens/regerar   a empresa pede outras 3 sugestões

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
  E1–E7 (Bloco 5, estorno da fee). Se o dinheiro recuperado volta ao pagador
       dentro do prazo da empresa, a recuperação deixa de valer na proporção
       do que voltou: devolução TOTAL → o ciclo aparece como "encerrado sem
       recuperação", com `motivo_encerramento: "estorno_no_prazo"` e
       `valor_liquido: null`; devolução PARCIAL → continua "recuperado", com o
       `valor_liquido` reduzido e `estorno_parcial: true`; fora do prazo → nada
       muda, só o evento na linha do tempo. Nas métricas, o estorno é
       descontado NO DIA E NO MÊS EM QUE ACONTECEU: o mês já fechado não é
       reescrito. A fee continua fora de toda resposta.
  R12  toda leitura filtra pelo tenant do token. Token da empresa A com o id
       de um ciclo da empresa B responde 404 com o MESMO corpo de um id que
       não existe — um 403 confirmaria que o ciclo existe.
  F9   toda data sai em ISO 8601 COM fuso (`api/datas.py`); as datas do ciclo
       são gravadas em hora local sem fuso, as da trilha em UTC.

AS TRÊS MENSAGENS (Bloco 3, R7 a R9). As duas rotas POST exigem o papel
`owner` ou `admin` (`membro` e token sem papel: 403). Nenhuma rota recebe texto
de mensagem: `escolher` recebe a rodada e o código de uma das 3 abordagens
geradas; `regerar` não recebe nada. Não existe rota que dispare mensagem sem ser
a escolha de uma das sugestões, e nada é gerado antes do fim das tentativas
(R1): as sugestões nascem dos mesmos gatilhos da Etapa 1.

O NOME DO CLIENTE (`cliente_nome`) é lido da base importada pelo
`id_recorrencia` na hora da resposta — é o cliente da própria empresa. Nunca é
copiado para o ciclo nem para as mensagens, e vem `null` sem mapeamento.

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
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Path

from .. import ambiente, simulador
from ..accounts import get_conta, get_tenant_id
from ..accounts.auth import exigir_papel
from ..agent import workflow
from ..agent.pix_codes import CAUSA_LEGIVEL
from ..churn_voluntary import clientes_importados
from ..churn_voluntary import retention_log as trilha
from ..dunning import canal_involuntario, configuracao
from ..dunning import ciclo_cobranca as cc
from . import datas, registro_acesso

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


def _liquido_original(ciclo: dict) -> float:
    """O líquido da recuperação como ela aconteceu: valor menos a fee da época.
    Não muda com um estorno posterior — é o que mantém o mês fechado igual (E5)."""
    return round(_valor_cobranca(ciclo) - float(ciclo.get("fee") or 0.0), 2)


def _valor_liquido(ciclo: dict) -> Optional[float]:
    """R11: o que a empresa recebeu — só em ciclo recuperado; `None` nos
    outros estados (não há o que receber, e um número ali seria lido como
    recebido). Com estorno PARCIAL no prazo, é o líquido do que ficou (valor e
    fee caem na mesma proporção, E3); com estorno TOTAL, o ciclo deixa de
    contar como recuperado (E2) e o líquido é `None`."""
    if ciclo["estado"] != cc.RECUPERADO or cc.estornado_por_inteiro(ciclo):
        return None
    ficou = _valor_cobranca(ciclo) - float(ciclo.get("valor_estornado") or 0.0)
    fee = float(ciclo.get("fee") or 0.0) - float(ciclo.get("fee_estornada") or 0.0)
    return round(ficou - fee, 2)


def _estorno(ciclo: dict) -> Optional[dict]:
    """O estorno acumulado DENTRO DO PRAZO, ou `None`. Sem a fee: só quanto da
    cobrança voltou ao pagador, se foi tudo, e quando foi o último. Aviso fora
    do prazo não entra aqui (não muda valor): aparece só na linha do tempo."""
    devolvido = round(float(ciclo.get("valor_estornado") or 0.0), 2)
    if ciclo["estado"] != cc.RECUPERADO or devolvido <= 0:
        return None
    return {"valor_devolvido": devolvido, "total": cc.estornado_por_inteiro(ciclo),
            "ultimo_em": datas.iso_com_fuso(ciclo.get("estornado_em"))}


def _desfecho_em(ciclo: dict) -> Optional[str]:
    return ciclo.get("recuperado_em") or ciclo.get("perdido_em") or ciclo.get("descartado_em")


def _mensagem(ciclo: dict) -> Optional[dict]:
    if not ciclo.get("mensagem_em"):
        return None
    return {"reservada_em": datas.iso_com_fuso(ciclo.get("mensagem_em")),
            "enviada_em": datas.iso_com_fuso(ciclo.get("mensagem_confirmada_em"))}


def _nomes(tenant_id: str, ciclos: list) -> dict:
    """{id_recorrencia: nome} da base, lido agora, numa consulta só. Base não
    configurada: nenhum nome (a tela mostra o id)."""
    try:
        return clientes_importados.nomes_por_recorrencia(
            tenant_id, [c["id_recorrencia"] for c in ciclos])
    except clientes_importados.ConfiguracaoAusente:
        return {}


def _linha_publica(ciclo: dict, nomes: Optional[dict] = None) -> dict:
    """Um ciclo como a empresa o vê. Sem `fee`, sem `tenant_id` (o token já
    diz de quem é), sem identidade de cobrança do PSP."""
    causa = ciclo.get("causa_original")
    estorno = _estorno(ciclo)
    # Rodada 3: o ciclo da simulação do gateway sai com outro id (o 7 real e o 7
    # simulado são ciclos diferentes) e marcado, para a tela nunca os confundir.
    simulado = simulador.e_simulada(ciclo)
    return {
        "id": ciclo["id"] + simulador.ID_DA_SIMULACAO if simulado else ciclo["id"],
        "simulado": simulado,
        "status": ciclo["status"],
        "estado": ciclo["estado"],
        "id_recorrencia": ciclo["id_recorrencia"],
        "cliente_nome": (nomes or {}).get(ciclo["id_recorrencia"]),
        "valor_cobranca": _valor_cobranca(ciclo),
        "valor_liquido": _valor_liquido(ciclo),
        "causa": causa,
        "causa_legivel": CAUSA_LEGIVEL.get(causa, causa),
        "tentativas_executadas": ciclo["tentativas_executadas"],
        "aberto_em": datas.iso_com_fuso(ciclo["aberto_em"]),
        "atualizado_em": datas.iso_com_fuso(ciclo["atualizado_em"]),
        "desfecho_em": datas.iso_com_fuso(_desfecho_em(ciclo)),
        "motivo_descarte": ciclo.get("motivo_descarte"),
        "motivo_perdido": ciclo.get("motivo_perdido"),
        "mensagem": _mensagem(ciclo),
        # Bloco 5: o dinheiro recuperado voltou (todo ou parte) dentro do prazo.
        "estorno": estorno,
        "estorno_parcial": bool(estorno) and not estorno["total"],
        "motivo_encerramento": (cc.MOTIVO_ENCERRADO_POR_ESTORNO
                                if estorno and estorno["total"] else None),
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
    incluir_simulados: bool = False,
    aguardando_escolha: bool = False,
    conta: dict = Depends(get_conta),
) -> dict:
    """Os ciclos da empresa, do mais recentemente atualizado ao mais antigo.

    `?status=em_analise,em_processo` (os quatro códigos de R10, separados por
    vírgula); `?desde=` e `?ate=` sobre a abertura (`ate` exclusivo; data ou
    ISO 8601); `?q=` prefixo do id da recorrência ou o id exato da cobrança;
    `?limite=` 1..200 (padrão 50); `?cursor=` o `proximo_cursor` da página
    anterior.

    `?incluir_simulados=true` (Rodada 3) junta, na PRIMEIRA página, os ciclos da
    simulação do gateway desta empresa, com os mesmos filtros, cada um com
    `simulado: true` e o nome do cliente fictício. Sem o parâmetro, a lista é
    só de ciclos reais.

    `?aguardando_escolha=true` (Rodada 4) deixa só os ciclos que esperam a
    escolha da empresa agora: é a mesma conta do número `aguardando_escolha`
    das métricas (o sino do painel mostra o número e leva a esta lista).
    """
    tenant_id = conta["tenant_id"]
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
    abertos_desde, abertos_ate = _data_do_filtro(desde, "desde"), _data_do_filtro(ate, "ate")
    pagina = cc.listar_ciclos(
        tenant_id, status=lista_status, desde=abertos_desde, ate=abertos_ate,
        texto=texto or None,
        cursor=_decodificar_cursor(cursor) if cursor else None, limite=limite + 1,
        aguardando_escolha=aguardando_escolha)
    tem_mais = len(pagina) > limite
    pagina = pagina[:limite]
    nomes = _nomes(tenant_id, pagina)
    linhas = [_linha_publica(c, nomes) for c in pagina]
    if incluir_simulados and not cursor:
        simulados = simulador.na_simulacao(tenant_id, lambda: (
            cc.listar_ciclos(tenant_id, status=lista_status, desde=abertos_desde,
                             ate=abertos_ate, texto=texto or None, limite=LIMITE_MAXIMO,
                             aguardando_escolha=aguardando_escolha),
            simulador.nomes(tenant_id)))
        if simulados:
            do_simulador, nomes_ficticios = simulados
            linhas += [_linha_publica({**c, simulador.MARCA_DE_LINHA: True}, nomes_ficticios)
                       for c in do_simulador]
            linhas.sort(key=lambda l: datas.com_fuso(l["atualizado_em"]), reverse=True)
    # A lista lê o `cliente_nome` da base: é leitura de dado de titular (Bloco 4).
    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_CICLOS, conta.get("papel"),
                              datas.agora_local())
    return {"ciclos": linhas,
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
            "contribuicoes": itens,
            "desconto_por_anomalia": _desconto_por_anomalia(risco, decisoes)}


def _desconto_por_anomalia(risco: dict, decisoes: list) -> Optional[dict]:
    """O desconto que a decisão do ciclo aplicou sobre a pontuação do
    diagnóstico, ou None. A tela mostra UM número, o que o sistema usou, e usa
    isto para dizer que ele já tem o desconto.

    Lido da decisão de retentativa da trilha. Nos ciclos gravados depois da
    Rodada 2 a `saida` traz o percentual e a pontuação de antes; nos anteriores
    não, e o desconto é reconhecido pela entrada `is_anomalous`, com a pontuação
    de antes vinda da decisão de risco."""
    decisao = next((d for d in decisoes if d["tipo_decisao"] == trilha.TIPO_RETENTATIVA), None)
    if decisao is None:
        return None
    entradas, saida = decisao.get("entradas") or {}, decisao.get("saida") or {}
    pct = saida.get(trilha.CHAVE_DESCONTO_PCT)
    antes = saida.get(trilha.CHAVE_PONTUACAO_ANTES)
    if pct is None:
        if not entradas.get("is_anomalous"):
            return None
        pct = workflow.DESCONTO_POR_ANOMALIA_PCT
        antes = (risco.get("saida") or {}).get("recovery_score")
    return {"percentual": pct, "pontuacao_antes": antes,
            "pontuacao_usada": entradas.get("recovery_score")}


def _mensagem_publica(m: dict) -> dict:
    """Uma sugestão como a empresa a vê. Texto `null` depois do expurgo de 90
    dias (fica a abordagem). Nenhum contato: só o canal e o motivo."""
    return {
        "rodada": m["rodada"],
        "abordagem": m["abordagem"],
        "texto": None if m.get("texto_apagado_em") else m.get("texto"),
        "canal": m["canal"],
        "motivo_canal": m["motivo_canal"],
        "recomendada": bool(m["recomendada"]),
        "escolhida": bool(m["escolhida"]),
        "nao_entregavel": bool(m["nao_entregavel_em"]) and not m["enviada_em"],
        "gerada_em": datas.iso_com_fuso(m["gerada_em"]),
        "enviada_em": datas.iso_com_fuso(m["enviada_em"]),
    }


def _escolha_ate(ciclo: dict, config: dict) -> Optional[str]:
    """Até quando a empresa escolhe: `aguardando_escolha_em` + prazo da
    empresa, só enquanto o ciclo espera a escolha no modo escolha."""
    inicio = ciclo.get("aguardando_escolha_em")
    if (ciclo["estado"] != cc.AGUARDANDO_ESCOLHA or not inicio
            or config["modo_mensagem_involuntario"] != configuracao.MODO_ESCOLHA):
        return None
    return datas.iso_com_fuso(datas.para_local(inicio)
                              + timedelta(hours=config["prazo_escolha_horas"]))


def _linha_do_tempo(ciclo: dict, tentativas: list, decisoes: list,
                    mensagens: Optional[list] = None, estornos: Optional[list] = None) -> list:
    mensagens = mensagens or []
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
    for rodada in sorted({m["rodada"] for m in mensagens}):
        da_rodada = [m for m in mensagens if m["rodada"] == rodada]
        eventos.append(_evento(da_rodada[0]["gerada_em"], "sugestoes_geradas", 4, rodada=rodada,
                               recomendada=next((m["abordagem"] for m in da_rodada
                                                 if m["recomendada"]), None),
                               canal=da_rodada[0]["canal"]))
    for m in mensagens:
        if m["escolhida"]:
            eventos.append(_evento(m["escolhida_em"], "mensagem_escolhida", 4, rodada=m["rodada"],
                                   abordagem=m["abordagem"], escolhida_por=m["escolhida_por"]))
        if m["nao_entregavel_em"]:
            eventos.append(_evento(m["nao_entregavel_em"], "mensagem_nao_entregavel", 4,
                                   motivo_canal=m["motivo_canal"]))
    eventos.append(_evento(ciclo.get("mensagem_em"), "mensagem_reservada", 4))
    eventos.append(_evento(ciclo.get("mensagem_confirmada_em"), "mensagem_enviada", 5))
    if ciclo["estado"] == cc.RECUPERADO:
        # O líquido DA RECUPERAÇÃO, como ela aconteceu; o que voltou depois é
        # outro evento (`estorno`), na data dele.
        eventos.append(_evento(ciclo.get("recuperado_em"), "recuperado", 6,
                               valor_liquido=_liquido_original(ciclo)))
        acumulado, valor = 0.0, _valor_cobranca(ciclo)
        for e in estornos or []:
            acumulado += float(e["valor_considerado"] or 0.0)
            eventos.append(_evento(
                e["recebido_em"], "estorno", 7,
                valor_devolvido=round(float(e["valor_devolvido"]), 2),
                no_prazo=bool(e["no_prazo"]),
                total=bool(e["no_prazo"]) and valor > 0 and acumulado >= valor - 0.005))
    elif ciclo["estado"] == cc.PERDIDO:
        eventos.append(_evento(ciclo.get("perdido_em"), "perdido", 6,
                               motivo_perdido=ciclo.get("motivo_perdido")))
    elif ciclo["estado"] == cc.DESCARTADO:
        eventos.append(_evento(ciclo.get("descartado_em"), "descartado", 6,
                               motivo_descarte=ciclo.get("motivo_descarte")))
    for d in decisoes:
        eventos.append(_evento(d["decidido_em"], "decisao", 1, tipo_decisao=d["tipo_decisao"],
                               explicacao=d["explicacao"]))
    eventos = [e for e in eventos if e is not None]
    eventos.sort(key=lambda e: (e["_instante"], e["_ordem"]))
    return [{k: v for k, v in e.items() if not k.startswith("_")} for e in eventos]


@contextmanager
def _ambiente_do_ciclo(tenant_id: str, ciclo_id: int):
    """`(id interno, simulado, agora)` para um id de ciclo vindo da rota.

    Id acima de `simulador.ID_DA_SIMULACAO` é de um ciclo da SIMULAÇÃO do
    gateway (Rodada 3): o corpo da rota roda sobre os arquivos de simulação da
    empresa do token, com o relógio simulado dela. Empresa que nunca simulou
    responde o mesmo 404 de um ciclo que não existe: o id de um ciclo simulado
    de outra empresa não abre nada aqui."""
    if ciclo_id <= simulador.ID_DA_SIMULACAO:
        yield ciclo_id, False, datas.agora_local()
        return
    if not simulador.existe(tenant_id):
        raise _404()
    with ambiente.em_simulacao(tenant_id):
        relogio = simulador.relogio(tenant_id)
    agora = relogio["agora"] if relogio else datas.agora_local()
    with ambiente.em_simulacao(tenant_id, agora):
        yield ciclo_id - simulador.ID_DA_SIMULACAO, True, agora


def detalhe_do_ciclo(tenant_id: str, ciclo: dict, agora: datetime, nomes: Optional[dict],
                     simulado: bool = False) -> dict:
    """O corpo de `GET /ciclos/{id}` para um ciclo já encontrado. A simulação
    do gateway devolve o ciclo dela nesta mesma forma."""
    if simulado:
        ciclo = {**ciclo, simulador.MARCA_DE_LINHA: True}
    decisoes, ambigua = _decisoes_do_ciclo(tenant_id, ciclo, agora)
    publica = _linha_publica(ciclo, nomes)
    publica.update(janela_inicio=datas.iso_com_fuso(ciclo["janela_inicio"]),
                   janela_fim=datas.iso_com_fuso(ciclo["janela_fim"]))
    mensagens = cc.mensagens_do_ciclo(ciclo["id"])
    escolhida = next((m for m in mensagens if m["escolhida"]), None)
    config = configuracao.ler(tenant_id)
    return {"ciclo": publica,
            "diagnostico": _diagnostico(decisoes),
            "mensagens": [_mensagem_publica(m) for m in mensagens],
            "modo_mensagem": config["modo_mensagem_involuntario"],
            # Com a mensagem já escolhida não há mais prazo de escolha, mesmo que o
            # ciclo ainda espere a janela de contato abrir para a mensagem sair.
            "escolha_ate": None if escolhida else _escolha_ate(ciclo, config),
            "escolhida_por": escolhida["escolhida_por"] if escolhida else None,
            "linha_do_tempo": _linha_do_tempo(ciclo, cc.tentativas_do_ciclo(ciclo["id"]),
                                              decisoes, mensagens,
                                              cc.estornos_do_ciclo(ciclo["id"])),
            "trilha_ambigua": ambigua}


@router.get("/ciclos/{ciclo_id}")
async def ler_ciclo(
    ciclo_id: int = Path(..., ge=1, le=ID_MAXIMO),
    conta: dict = Depends(get_conta),
) -> dict:
    """Um ciclo da empresa e a linha do tempo dele: abertura, diagnóstico (com
    as cinco maiores contribuições em linguagem simples), cada tentativa,
    mensagem, desfecho, e as decisões da trilha do Art. 20 do período.
    404 idêntico para ciclo inexistente e de outra empresa (R12). O id de um
    ciclo da simulação do gateway (`simulado: true` na lista) abre o ciclo
    simulado da própria empresa."""
    tenant_id = conta["tenant_id"]
    with _ambiente_do_ciclo(tenant_id, ciclo_id) as (interno, simulado, agora):
        ciclo = cc.ciclo_do_tenant(tenant_id, interno)
        if ciclo is None:
            raise _404()
        nomes = simulador.nomes(tenant_id) if simulado else _nomes(tenant_id, [ciclo])
        detalhe = detalhe_do_ciclo(tenant_id, ciclo, agora, nomes, simulado)
    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_CICLO, conta.get("papel"),
                              datas.agora_local())
    return detalhe


# ── As três mensagens: escolher e regerar ─────────────────────────────────

def _409(motivo: str, detalhe: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"motivo": motivo, "detalhe": detalhe})


def _espera(ciclo_id: int, tenant_id: str, agora: datetime) -> Optional[str]:
    """Por que uma mensagem escolhida ainda não saiu: `fora_da_janela` ou
    `sem_canal`; None se saiu."""
    ciclo = cc.ciclo_por_id(ciclo_id)
    if ciclo["estado"] != cc.AGUARDANDO_ESCOLHA:
        return None
    if not configuracao.dentro_da_janela(configuracao.ler(tenant_id), agora):
        return "fora_da_janela"
    return "sem_canal"


@router.post("/ciclos/{ciclo_id}/mensagens/escolher")
async def escolher_mensagem(
    ciclo_id: int = Path(..., ge=1, le=ID_MAXIMO),
    corpo: dict = Body(...),
    conta: dict = Depends(get_conta),
) -> dict:
    """A empresa escolhe UMA das sugestões geradas: `{"rodada": n,
    "abordagem": "<lembrete_cordial|facilitacao|urgencia_respeitosa>"}`, de
    qualquer rodada (D-E2-12). Não existe campo de texto: a mensagem é sempre
    uma das geradas. Exige `owner` ou `admin` (403 para `membro`). 404 idêntico
    para ciclo inexistente e de outra empresa; 409 se o ciclo não espera
    escolha (já escolhida, enviada, fechada). A mensagem sai na hora se a
    janela de contato e um canal permitirem; senão, o relógio a envia depois.
    Vale também para o ciclo da simulação do gateway, com o relógio simulado."""
    papel = exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    with _ambiente_do_ciclo(tenant_id, ciclo_id) as (interno, _, agora):
        ciclo = cc.ciclo_do_tenant(tenant_id, interno)
        if ciclo is None:
            raise _404()
        if not isinstance(corpo, dict) or set(corpo) - {"rodada", "abordagem"}:
            extras = sorted(set(corpo) - {"rodada", "abordagem"}) if isinstance(corpo, dict) else []
            raise _422("campo_desconhecido", "o corpo aceita só `rodada` e `abordagem`: a "
                       "mensagem é sempre uma das sugestões geradas",
                       extras[0] if extras else "corpo")
        rodada, abordagem = corpo.get("rodada"), corpo.get("abordagem")
        if isinstance(rodada, bool) or not isinstance(rodada, int) or rodada < 1:
            raise _422("rodada_invalida", "esperado inteiro >= 1", "rodada")
        if abordagem not in cc.ABORDAGENS:
            raise _422("abordagem_invalida", f"esperado um de {', '.join(cc.ABORDAGENS)}",
                       "abordagem")
        if ciclo["estado"] != cc.AGUARDANDO_ESCOLHA or cc.mensagem_escolhida(interno):
            raise _409("ciclo_nao_aguarda_escolha", "este ciclo não está esperando uma escolha")
        existe = any(m["rodada"] == rodada and m["abordagem"] == abordagem
                     for m in cc.mensagens_do_ciclo(interno))
        if not existe:
            raise _422("sugestao_inexistente", "não há esta sugestão neste ciclo", "rodada")
        if workflow.registrar_escolha(interno, papel, agora, rodada, abordagem) is None:
            raise _409("ciclo_nao_aguarda_escolha",
                       "a escolha já foi feita (pela empresa ou pelo prazo)")
        enviado = await workflow.enviar_mensagem_escolhida(interno, agora)
        return {"ciclo_id": ciclo_id, "rodada": rodada, "abordagem": abordagem,
                "escolhida_por": papel, "enviada": enviado is not None,
                "espera": None if enviado is not None else _espera(interno, tenant_id, agora)}


@router.post("/ciclos/{ciclo_id}/mensagens/regerar")
async def regerar_mensagens(
    ciclo_id: int = Path(..., ge=1, le=ID_MAXIMO),
    conta: dict = Depends(get_conta),
) -> dict:
    """Outras 3 sugestões para o mesmo ciclo (acréscimo à R8), quantas vezes a
    empresa quiser DENTRO do prazo de escolha, que não é reiniciado. Só no
    modo escolha, com o ciclo esperando escolha. Cada rodada fica gravada; a
    recomendada da ÚLTIMA é a que sai por prazo. Vale como decisão na trilha."""
    papel = exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    with _ambiente_do_ciclo(tenant_id, ciclo_id) as (interno, _, agora):
        ciclo = cc.ciclo_do_tenant(tenant_id, interno)
        if ciclo is None:
            raise _404()
        config = configuracao.ler(tenant_id)
        if config["modo_mensagem_involuntario"] != configuracao.MODO_ESCOLHA:
            raise _409("modo_automatico",
                       "a empresa está no modo automático: não há escolha a fazer")
        if ciclo["estado"] != cc.AGUARDANDO_ESCOLHA or cc.mensagem_escolhida(interno):
            raise _409("ciclo_nao_aguarda_escolha", "este ciclo não está esperando uma escolha")
        limite = datas.para_local(ciclo["aguardando_escolha_em"]) + timedelta(
            hours=config["prazo_escolha_horas"])
        if agora >= limite:
            raise _409("prazo_de_escolha_vencido", "o prazo de escolha acabou")
        rodada = await workflow.gerar_rodada(interno, agora, papel=papel)
        if rodada is None:
            raise _409("ciclo_nao_aguarda_escolha", "a escolha foi feita durante a geração")
        return {"ciclo_id": ciclo_id, "rodada": rodada,
                "mensagens": [_mensagem_publica(m) for m in cc.mensagens_do_ciclo(interno)
                              if m["rodada"] == rodada],
                "escolha_ate": datas.iso_com_fuso(limite)}


# ── Métricas ──────────────────────────────────────────────────────────────

def _resumo(linhas: list, estornos: Optional[list] = None) -> dict:
    """Valor líquido, recuperados, encerrados e taxa sobre os ciclos COM
    desfecho (D-E2-9). Sem desfecho nenhum, a taxa é `null` — não zero: zero
    diria que tudo foi perdido.

    E5 (Bloco 5): `linhas` são os desfechos DO PERÍODO e `estornos` os estornos
    no prazo recebidos NO PERÍODO. A recuperação entra pelo líquido original, na
    data dela; o estorno sai na data dele. Um estorno de outubro não mexe em
    setembro: nem no valor, nem nas contagens, nem na taxa — as três contam o
    que aconteceu no período. O valor do período pode ficar negativo."""
    estornos = estornos or []
    recuperados = [l for l in linhas if l["estado"] == cc.RECUPERADO]
    encerrados = len(linhas) - len(recuperados)
    estornado = round(sum(e["liquido_devolvido"] for e in estornos), 2)
    liquido = round(sum(_liquido_original(l) for l in recuperados) - estornado, 2)
    return {"valor_liquido_recuperado": liquido,
            "recuperados": len(recuperados),
            "encerrados_sem_recuperacao": encerrados,
            "taxa_recuperacao": round(len(recuperados) / len(linhas), 4) if linhas else None,
            "estornos": {"quantidade": len(estornos),
                         "ciclos_estornados_por_inteiro": sum(1 for e in estornos if e["total"]),
                         "valor_liquido_estornado": estornado}}


def proxima_acao_do_sistema(tenant_id: str, agora: datetime) -> Optional[dict]:
    """O que o sistema vai fazer em seguida nos ciclos REAIS desta empresa, e
    quando (Rodada 4: o cartão "Próxima ação do sistema"). É a mais próxima entre:

      - a próxima tentativa de cobrança agendada (ciclos em recobrança);
      - o envio de uma mensagem que já foi escolhida e espera a janela de contato;
      - o envio automático da recomendada (no modo automático, na próxima janela;
        no modo "eu escolho", quando o prazo de escolha vencer).

    `{quando, tipo, descricao, ciclo_id}`, ou None se nada está agendado. Sem
    nome nem identificador do cliente: só o id do ciclo, para a tela abrir.
    O fim do prazo de recuperação de um ciclo não entra: não é uma ação sobre
    o cliente."""
    config = configuracao.ler(tenant_id)
    candidatas = []
    tentativa = cc.proxima_tentativa_pendente(tenant_id)
    if tentativa is not None:
        candidatas.append({"quando": cc._data(tentativa["agendada_para"]), "tipo": "tentativa",
                           "descricao": f"Tentativa {tentativa['numero']} de cobrança",
                           "ciclo_id": tentativa["ciclo_id"]})
    for ciclo in cc.ciclos_esperando_mensagem(tenant_id):
        if ciclo["tem_escolhida"]:
            quando = configuracao.proximo_inicio_da_janela(config, agora)
            descricao = "Envio da mensagem escolhida"
        elif config["modo_mensagem_involuntario"] == configuracao.MODO_AUTOMATICO:
            quando = configuracao.proximo_inicio_da_janela(config, agora)
            descricao = "Envio da mensagem recomendada"
        else:
            inicio = cc._data(ciclo.get("aguardando_escolha_em")) or agora
            limite = inicio + timedelta(hours=config["prazo_escolha_horas"])
            quando = configuracao.proximo_inicio_da_janela(config, max(limite, agora))
            descricao = "Envio automático da mensagem recomendada"
        candidatas.append({"quando": quando, "tipo": "mensagem", "descricao": descricao,
                           "ciclo_id": ciclo["id"]})
    candidatas = [c for c in candidatas if c["quando"] is not None]
    if not candidatas:
        return None
    proxima = min(candidatas, key=lambda c: (c["quando"], c["ciclo_id"]))
    return {**proxima, "quando": datas.iso_com_fuso(proxima["quando"])}


@router.get("/metrics/involuntario/mes")
async def metricas_do_mes(mes: Optional[str] = None, incluir_simulados: bool = False,
                          tenant_id: str = Depends(get_tenant_id)) -> dict:
    """O mês da empresa (`?mes=AAAA-MM`; padrão: o mês corrente no fuso da
    instalação). Fonte: `ciclos_cobranca`, a fonte da verdade do ciclo — não o
    dataset de treino. Valor líquido e taxa sobre os ciclos com desfecho no
    mês; as contagens por status, sobre os ciclos abertos no mês.
    `?incluir_simulados=true` soma os ciclos da simulação do gateway desta
    empresa; sem o parâmetro, os números são só dos ciclos reais."""
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
    # `d`: o deslocamento do relógio simulado (zero para os ciclos reais).
    ler = lambda fn: simulador.com_simulados(tenant_id, incluir_simulados, fn)   # noqa: E731
    return {"mes": f"{ano:04d}-{numero:02d}",
            "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
            **_resumo(ler(lambda d: cc.ciclos_com_desfecho(tenant_id, inicio + d, fim + d)),
                      ler(lambda d: cc.estornos_no_periodo(tenant_id, inicio + d, fim + d))),
            "ciclos_abertos_no_mes": ler(
                lambda d: cc.contagem_por_status(tenant_id, inicio + d, fim + d)),
            # Agora, não no mês: quantos ciclos esperam a escolha da empresa.
            "aguardando_escolha": ler(lambda d: cc.contar_aguardando_escolha(tenant_id)),
            # Rodada 4: o que o sistema faz em seguida nos ciclos reais, e quando
            # (None se nada está agendado). Os simulados têm relógio próprio e não entram.
            "proxima_acao": proxima_acao_do_sistema(tenant_id, datas.agora_local())}


@router.get("/metrics/involuntario/serie")
async def serie_diaria(dias: Optional[int] = None, incluir_simulados: bool = False,
                       tenant_id: str = Depends(get_tenant_id)) -> dict:
    """Um ponto por dia, dos últimos `?dias=` (1..365, padrão 30) até hoje,
    pelo dia do DESFECHO. Dias sem desfecho entram com zero e taxa `null`.
    `?incluir_simulados=true` soma os ciclos da simulação do gateway."""
    dias = DIAS_PADRAO if dias is None else dias
    if not 1 <= dias <= DIAS_MAXIMO:
        raise _422("dias_invalido", f"esperado inteiro entre 1 e {DIAS_MAXIMO}", "dias")
    hoje = datas.agora_local().date()
    primeiro = hoje - timedelta(days=dias - 1)
    inicio = datetime.combine(primeiro, datetime.min.time())
    fim = datetime.combine(hoje + timedelta(days=1), datetime.min.time())
    ler = lambda fn: simulador.com_simulados(tenant_id, incluir_simulados, fn)   # noqa: E731
    por_dia: dict = {}
    for linha in ler(lambda d: cc.ciclos_com_desfecho(tenant_id, inicio + d, fim + d)):
        por_dia.setdefault(str(linha["desfecho_em"])[:10], []).append(linha)
    estornos_por_dia: dict = {}
    for estorno in ler(lambda d: cc.estornos_no_periodo(tenant_id, inicio + d, fim + d)):
        estornos_por_dia.setdefault(str(estorno["recebido_em"])[:10], []).append(estorno)
    pontos = []
    for i in range(dias):
        dia = (primeiro + timedelta(days=i)).isoformat()
        resumo = _resumo(por_dia.get(dia, []), estornos_por_dia.get(dia, []))
        # No ponto do dia vai só o valor estornado; o detalhe fica no resumo do mês.
        estornado = resumo.pop("estornos")["valor_liquido_estornado"]
        pontos.append({"dia": dia, **resumo, "valor_liquido_estornado": estornado})
    return {"dias": dias, "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
            "pontos": pontos}

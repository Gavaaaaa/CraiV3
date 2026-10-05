"""crai/api/voluntario.py — o voluntário visto de fora: a página do dashboard (Rodada 3).

    GET /clientes/recentes                   os clientes atualizados por último, com a faixa
    GET /clientes/base                       a base em quatro números
    GET /metrics/voluntario/mes              o mês: valor mantido líquido, contagens
    GET /metrics/voluntario/serie            um ponto por dia
    GET /metrics/voluntario/regua-x-modelo   régua e modelo sobre a mesma base

O QUE ESTAS ROTAS GARANTEM:

  TENANT  toda leitura filtra pelo tenant do token. Não há id no caminho: uma
          empresa não tem como pedir nada de outra, e nada de outra aparece nas
          listas e nos totais dela.
  LGPD    nenhuma resposta traz telefone, e-mail, CPF ou chave Pix. Da base sai
          só o que a tela mostra: o id que a própria empresa usa, o nome (o
          mesmo que a lista de ciclos já mostra), a mensalidade e a avaliação.
          A linha é montada campo a campo, nunca copiada inteira.
  R11     a fee não aparece. O valor mantido sai sempre líquido.
  V1-V4   o que é "mantido" e quando é estornado: `churn_voluntary/mantido.py`.

A FAIXA é a tradução única da criticidade do backend para a tela:
`critico` grave, `alto` preocupante, `padrao` sem risco, `dado_insuficiente`
sem dado suficiente (que nunca aparece como "sem risco").

O CACHE DA RÉGUA. A lista e os totais saem de
`insights_unificados.painel_de_risco`, que só recalcula a base quando ela muda.

Este módulo NÃO importa de `app.py` (é o `app.py` que o monta).
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import simulador
from ..accounts import get_conta, get_tenant_id
from ..churn_voluntary import (batch_scoring, clientes_importados, insights_unificados,
                               mantido, origem_da_base, retention_log, risk_scorer)
from ..dunning import configuracao
from . import datas, registro_acesso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["voluntario"])

LIMITE_PADRAO = 10
LIMITE_MAXIMO = 100
DIAS_PADRAO = 30
DIAS_MAXIMO = 365

FAIXA_DA_CRITICIDADE = {"critico": "grave", "alto": "preocupante", "padrao": "sem_risco",
                        batch_scoring.CRITICIDADE_SEM_DADO: "sem_dado"}
DECIDIDO_POR = {batch_scoring.DECIDIDO_MODELO: "modelo", batch_scoring.DECIDIDO_REGRA: "regua"}

# A comparação régua x modelo só é mostrada com cancelamentos suficientes no
# período. Abaixo disto, um cancelamento a mais ou a menos mudaria a leitura
# inteira, e a rota devolve vazio.
MINIMO_DE_CANCELAMENTOS = 5

VAZIO_MODELO_INATIVO = "modelo_inativo"
VAZIO_BASE_PEQUENA = "base_pequena"
VAZIO_POUCOS_CANCELAMENTOS = "cancelamentos_insuficientes"


# ── Peças comuns ──────────────────────────────────────────────────────────

def _422(motivo: str, detalhe: str, campo: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"motivo": motivo, "detalhe": detalhe,
                                                  "campo": campo})


def _500_base(e: Exception) -> HTTPException:
    logger.error("[VOLUNTARIO] %s", e)
    return HTTPException(status_code=500, detail={
        "motivo": "base_nao_configurada", "detalhe": str(e)})


def periodo_do_mes(mes: Optional[str]) -> tuple:
    """`AAAA-MM` (ou o mês corrente) → (rótulo, início, fim), em hora local."""
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
    return f"{ano:04d}-{numero:02d}", inicio, fim


def periodo_dos_dias(dias: Optional[int]) -> tuple:
    """Os últimos `dias` (1..365, padrão 30) até hoje → (dias, primeiro dia, início, fim)."""
    dias = DIAS_PADRAO if dias is None else dias
    if not 1 <= dias <= DIAS_MAXIMO:
        raise _422("dias_invalido", f"esperado inteiro entre 1 e {DIAS_MAXIMO}", "dias")
    hoje = datas.agora_local().date()
    primeiro = hoje - timedelta(days=dias - 1)
    return (dias, primeiro, datetime.combine(primeiro, datetime.min.time()),
            datetime.combine(hoje + timedelta(days=1), datetime.min.time()))


def _ranking(tenant_id: str) -> list:
    try:
        return insights_unificados.painel_de_risco(tenant_id)
    except clientes_importados.ConfiguracaoAusente as e:
        raise _500_base(e) from e


def _ciclo_e_sorteio(ciclo: dict) -> bool:
    return ciclo.get("origem_desfecho") == mantido.ORIGEM_SORTEIO


def situacao_da_oferta(ciclo: dict) -> str:
    """aguardando (decidida, ainda não saiu), enviada, aceita ou recusada."""
    if ciclo.get("accepted") == 1:
        return "aceita"
    if ciclo.get("accepted") == 0:
        return "recusada"
    return "enviada" if ciclo.get("offer_sent") else "aguardando"


def ciclos_com_oferta(tenant_id: str, incluir_simulados: bool = False) -> list:
    """Os ciclos com oferta da empresa. Sem `incluir_simulados`: só os reais, e
    sem os de aceite sorteado. Com ele: mais os de aceite sorteado e os da
    simulação do gateway (marcados, com as datas no relógio de hoje)."""
    ciclos = simulador.com_simulados(tenant_id, incluir_simulados,
                                     lambda d: retention_log.ciclos_com_oferta(tenant_id))
    return [c for c in ciclos if incluir_simulados or not _ciclo_e_sorteio(c)]


def _marcadas(linhas: list) -> list:
    for l in linhas:
        if simulador.e_simulada(l):
            l["simulado"] = True
    return linhas


def mantidas_do_periodo(tenant_id: str, inicio: datetime, fim: datetime,
                        incluir_simulados: bool = False) -> list:
    """As retenções aceitas no período; com `incluir_simulados`, também as de
    aceite sorteado e as da simulação do gateway, todas com `simulado: True`."""
    return _marcadas(simulador.com_simulados(
        tenant_id, incluir_simulados,
        lambda d: mantido.mantidas_no_periodo(tenant_id, inicio + d, fim + d, incluir_simulados)))


def estornos_do_periodo(tenant_id: str, inicio: datetime, fim: datetime,
                        incluir_simulados: bool = False) -> list:
    return _marcadas(simulador.com_simulados(
        tenant_id, incluir_simulados,
        lambda d: mantido.estornos_no_periodo(tenant_id, inicio + d, fim + d, incluir_simulados)))


def abordagens_por_cliente(tenant_id: str) -> dict:
    """{id do cliente: o ÚLTIMO ciclo com oferta dele}. O id é o da base (sem o
    prefixo `user:`); o visitante anônimo fica com a identidade qualificada."""
    por_cliente: dict = {}
    for ciclo in retention_log.ciclos_com_oferta(tenant_id):
        por_cliente[insights_unificados._id_cru(ciclo["user_id"])] = ciclo
    return por_cliente


def _abordagem(ciclo: Optional[dict]) -> Optional[dict]:
    if ciclo is None:
        return None
    oferta, canal = ciclo["offer_type"], ciclo.get("channel")
    return {"oferta": oferta, "oferta_legivel": retention_log.ROTULOS_DE_OFERTA.get(oferta, oferta),
            "canal": canal, "canal_legivel": retention_log.ROTULOS_DE_CANAL.get(canal, canal),
            "situacao": situacao_da_oferta(ciclo),
            "decidida_em": datas.iso_com_fuso(ciclo.get("registrado_em")),
            "simulado": _ciclo_e_sorteio(ciclo)}


def decidido_por(linha: dict) -> Optional[str]:
    """Quem decidiu o risco desta linha do ranking: `modelo`, `regua`, ou None.

    None em dois casos: não há avaliação (sem dado), ou a linha veio de um
    evento do SDK, que não guarda no ciclo quem decidiu (isso fica na trilha).
    Na base importada, o caminho do modelo v3 marca cada linha; nos outros
    caminhos decide o modelo, se há um ativo, e a régua, se não há."""
    if linha.get("risk_score") is None:
        return None
    marcado = DECIDIDO_POR.get(linha.get("risco_decidido_por"))
    if marcado:
        return marcado
    if linha.get("origem") != insights_unificados.ORIGEM_UPLOAD:
        return None
    return "modelo" if risk_scorer.modelo_ativo() else "regua"


def _atualizado_em(linha: dict, carimbos: dict):
    """O instante mais recente em que este cliente mudou: a importação, a
    alteração parcial (`PATCH`) ou o último evento do SDK."""
    candidatos = [linha.get("atualizado_em")]
    if linha.get("origem") == insights_unificados.ORIGEM_UPLOAD:
        candidatos += list(carimbos.get(linha["customer_id_externo"], (None, None, None))[:2])
    instantes = [i for i in (datas.com_fuso(c) for c in candidatos) if i is not None]
    # No fuso da instalação, como as datas do ciclo (a base grava em UTC).
    return max(instantes).astimezone(datas.fuso_local()) if instantes else None


# ── GET /clientes/recentes ────────────────────────────────────────────────

def _clientes_ficticios_em_risco(tenant_id: str) -> list:
    """Os clientes fictícios em risco da simulação do gateway, na forma da
    lista de recentes, com `simulado: True`. Nunca com a propensão escondida. As
    datas saem no relógio de verdade (o agora da simulação aparece como agora)."""
    linhas = []
    d = simulador.deslocamento(tenant_id)
    for r in simulador.na_simulacao(tenant_id, lambda: simulador.retencoes(tenant_id), []):
        quando = datas.iso_com_fuso(simulador.recuar(r["criado_em"], d))
        abordagem = None
        if r.get("oferta"):
            abordagem = {
                "oferta": r["oferta"],
                "oferta_legivel": retention_log.ROTULOS_DE_OFERTA.get(r["oferta"], r["oferta"]),
                "canal": r.get("canal"),
                "canal_legivel": retention_log.ROTULOS_DE_CANAL.get(r.get("canal"), r.get("canal")),
                "situacao": ("enviada" if r["aceitou"] is None
                             else "aceita" if r["aceitou"] else "recusada"),
                "decidida_em": quando, "simulado": True}
        linhas.append({
            "id": insights_unificados._id_cru(r["user_id"]), "nome": r["nome"], "mrr": r["mrr"],
            "faixa": r.get("faixa") or "sem_dado", "motivo": r.get("motivo"),
            "decidido_por": r.get("decidido_por"), "posicao_no_ranking": None,
            "abordagem": abordagem, "atualizado_em": quando,
            "origem": "simulacao", "nao_contatar": False, "simulado": True})
    return linhas


@router.get("/clientes/recentes")
async def clientes_recentes(limite: Optional[int] = None, incluir_simulados: bool = False,
                            cliente: Optional[str] = None,
                            conta: dict = Depends(get_conta)) -> dict:
    """Os clientes da empresa atualizados mais recentemente (`?limite=` 1..100,
    padrão 10), cada um com a faixa de risco, o motivo em uma frase, quem
    decidiu (`modelo`, `regua`, ou `null` quando não há avaliação ou ela veio de
    um evento que não registra isso) e a abordagem: a oferta, o canal e a
    situação do último ciclo de retenção dele, ou `null` se nunca houve oferta.

    `posicao_no_ranking` é a posição pelo risco na base inteira (1 = o maior
    risco); `total_na_base` é o tamanho dessa base.

    Rodada 4: cada cliente diz se está marcado como "não contatar"
    (`nao_contatar`); e `?cliente=<id>` devolve só aquele cliente, esteja ou
    não entre os mais recentes (a busca do topo leva a ele). Id que não é da
    empresa devolve a lista vazia, igual ao que não existe."""
    tenant_id = conta["tenant_id"]
    limite = LIMITE_PADRAO if limite is None else limite
    if not 1 <= limite <= LIMITE_MAXIMO:
        raise _422("limite_invalido", f"esperado inteiro entre 1 e {LIMITE_MAXIMO}", "limite")
    ranking = _ranking(tenant_id)
    try:
        carimbos = insights_unificados.em_cache(
            tenant_id, "carimbos", lambda: clientes_importados.carimbos(tenant_id))
    except clientes_importados.ConfiguracaoAusente as e:
        raise _500_base(e) from e
    abordagens = abordagens_por_cliente(tenant_id)
    try:
        marcados = clientes_importados.marcados_nao_contatar(tenant_id)
    except clientes_importados.ConfiguracaoAusente:
        marcados = {}
    so_este = cliente.strip() if cliente and cliente.strip() else None

    linhas = []
    for posicao, l in enumerate(ranking, start=1):
        if so_este is not None and l["customer_id_externo"] != so_este:
            continue
        quando = _atualizado_em(l, carimbos)
        linhas.append((quando, posicao, l))
    minimo = datetime.min.replace(tzinfo=datas.fuso_local())
    linhas.sort(key=lambda t: (t[0] or minimo, t[2]["customer_id_externo"]), reverse=True)

    clientes = []
    for quando, posicao, l in linhas[:limite]:
        cid = l["customer_id_externo"]
        clientes.append({
            "id": cid,
            "nome": carimbos.get(cid, (None, None, None))[2],
            "mrr": l.get("mrr"),
            "faixa": FAIXA_DA_CRITICIDADE.get(l["criticality"], "sem_dado"),
            "motivo": l.get("explicacao"),
            "decidido_por": decidido_por(l),
            "posicao_no_ranking": posicao if l.get("risk_score") is not None else None,
            "abordagem": _abordagem(abordagens.get(cid)),
            "atualizado_em": quando.isoformat(timespec="seconds") if quando else None,
            "origem": l.get("origem"),
            # Rodada 4: quem pediu para não receber mensagens (ou foi marcado pela empresa).
            "nao_contatar": cid in marcados,
            "simulado": False,
        })
    if incluir_simulados:
        # Os clientes fictícios em risco da simulação do gateway entram na mesma
        # lista, marcados, e a lista continua do mais recente ao mais antigo.
        clientes += [c for c in _clientes_ficticios_em_risco(tenant_id)
                     if so_este is None or c["id"] == so_este]
        clientes.sort(key=lambda c: datas.com_fuso(c["atualizado_em"]) or minimo, reverse=True)
        clientes = clientes[:limite]
    # A lista lê o nome do cliente final na base: é leitura de dado de titular.
    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_CLIENTES_RECENTES,
                              conta.get("papel"), datas.agora_local())
    return {"clientes": clientes, "total_na_base": len(ranking)}


# ── GET /clientes/base ────────────────────────────────────────────────────

@router.get("/clientes/base")
async def base_de_clientes(tenant_id: str = Depends(get_tenant_id)) -> dict:
    """A base da empresa: quantos clientes ativos, quantos têm dado de
    comportamento, para quantos o modelo decide o risco, quando foi a última
    atualização e por onde ela chegou (`api`, `anexo`, ou `null` se a base é
    anterior a este registro). Sem nenhum cliente, `base` é `null`."""
    try:
        resumo = clientes_importados.resumo(tenant_id)
    except clientes_importados.ConfiguracaoAusente as e:
        raise _500_base(e) from e
    if resumo["atualizada_em"] is None:
        return {"base": None}
    ranking = _ranking(tenant_id)
    ultima = origem_da_base.ultima(tenant_id)
    return {"base": {
        "total": resumo["total"],
        "com_dados_comportamento": resumo["com_dados"],
        "decididos_pelo_modelo": sum(1 for l in ranking if decidido_por(l) == "modelo"),
        "modelo_ativo": risk_scorer.modelo_ativo(),
        "atualizada_em": datas.iso_com_fuso(
            ultima["atualizada_em"] if ultima else resumo["atualizada_em"]),
        "origem": ultima["origem"] if ultima else None,
    }}


# ── GET /metrics/voluntario/mes e /serie ──────────────────────────────────

def _no_periodo(texto, inicio: datetime, fim: datetime) -> bool:
    local = datas.para_local(texto)
    return local is not None and inicio <= local < fim


def ofertas_do_periodo(tenant_id: str, inicio: datetime, fim: datetime,
                       incluir_simulados: bool = False) -> dict:
    """Enviadas: os ciclos com oferta registrados no período em que a mensagem
    saiu. Aceitas: os aceites cujo desfecho caiu no período."""
    ciclos = ciclos_com_oferta(tenant_id, incluir_simulados)
    return {"ofertas_enviadas": sum(1 for c in ciclos if c.get("offer_sent")
                                    and _no_periodo(c.get("registrado_em"), inicio, fim)),
            "ofertas_aceitas": sum(1 for c in ciclos if c.get("accepted") == 1
                                   and _no_periodo(c.get("desfecho_em"), inicio, fim))}


def faixas_da_base(tenant_id: str) -> dict:
    """Quantos clientes da base estão, agora, em cada faixa."""
    contagem = {"grave": 0, "preocupante": 0, "sem_risco": 0, "sem_dado": 0}
    for l in _ranking(tenant_id):
        contagem[FAIXA_DA_CRITICIDADE.get(l["criticality"], "sem_dado")] += 1
    return contagem


@router.get("/metrics/voluntario/mes")
async def metricas_do_mes(mes: Optional[str] = None, incluir_simulados: bool = False,
                          tenant_id: str = Depends(get_tenant_id)) -> dict:
    """O mês do voluntário (`?mes=AAAA-MM`; padrão: o mês corrente). O valor
    mantido é líquido da fee: as retenções aceitas no mês menos os estornos do
    mês (V4: o estorno entra no mês do cancelamento, e o mês fechado não muda).
    `grave` e `preocupante` são de agora, na base inteira."""
    rotulo, inicio, fim = periodo_do_mes(mes)
    feito = mantido.sincronizar_sem_levantar(tenant_id) or {}
    faixas = faixas_da_base(tenant_id)
    return {"mes": rotulo, "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
            **mantido.resumo(mantidas_do_periodo(tenant_id, inicio, fim, incluir_simulados),
                             estornos_do_periodo(tenant_id, inicio, fim, incluir_simulados)),
            # Aceitou a oferta, mas não há mensalidade conhecida: não vira valor.
            "aceites_sem_valor": feito.get("sem_valor", 0),
            "grave": faixas["grave"], "preocupante": faixas["preocupante"],
            **ofertas_do_periodo(tenant_id, inicio, fim, incluir_simulados),
            "meses_de_mrr": mantido.MESES_DE_MRR_MANTIDOS,
            "prazo_estorno_dias": configuracao.ler(tenant_id)["prazo_estorno_dias"]}


def serie_do_voluntario(tenant_id: str, dias: int, primeiro: date, inicio: datetime,
                        fim: datetime, incluir_simulados: bool = False) -> list:
    """Um ponto por dia: o líquido das retenções aceitas no dia menos o líquido
    estornado no dia."""
    por_dia: dict = {}
    for m in mantidas_do_periodo(tenant_id, inicio, fim, incluir_simulados):
        por_dia.setdefault(m["aceito_em"][:10], []).append(m)
    estornos_por_dia: dict = {}
    for e in estornos_do_periodo(tenant_id, inicio, fim, incluir_simulados):
        estornos_por_dia.setdefault(e["cancelamento_em"][:10], []).append(e)
    pontos = []
    for i in range(dias):
        dia = (primeiro + timedelta(days=i)).isoformat()
        resumo = mantido.resumo(por_dia.get(dia, []), estornos_por_dia.get(dia, []))
        estornado = resumo.pop("estornos")["valor_liquido_estornado"]
        pontos.append({"dia": dia, **resumo, "valor_liquido_estornado": estornado})
    return pontos


@router.get("/metrics/voluntario/serie")
async def serie_diaria(dias: Optional[int] = None, incluir_simulados: bool = False,
                       tenant_id: str = Depends(get_tenant_id)) -> dict:
    """Um ponto por dia, dos últimos `?dias=` (1..365, padrão 30) até hoje,
    pelo dia do ACEITE. O estorno sai no dia do cancelamento."""
    dias, primeiro, inicio, fim = periodo_dos_dias(dias)
    mantido.sincronizar_sem_levantar(tenant_id)
    return {"dias": dias, "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
            "pontos": serie_do_voluntario(tenant_id, dias, primeiro, inicio, fim,
                                          incluir_simulados)}


# ── GET /metrics/voluntario/regua-x-modelo ────────────────────────────────

def _tem_dado(cliente: dict) -> bool:
    return (cliente.get("days_since_last") is not None
            or cliente.get("features_used_30d") is not None)


def _comparar(tenant_id: str, inicio: datetime, fim: datetime) -> dict:
    """Régua e modelo avaliando a MESMA base: a de hoje mais quem cancelou no
    período, com os dados que cada cliente tinha por último. Só com o que
    existe: sem modelo v3 ativo, sem base suficiente ou com poucos
    cancelamentos, não há comparação (`comparacao: None`, com o motivo)."""
    if not batch_scoring.posicao_pelo_modelo_ativa():
        return {"comparacao": None, "motivo_vazio": VAZIO_MODELO_INATIVO}
    todos = clientes_importados.listar(tenant_id, incluir_cancelados=True)
    ativos = [c for c in todos if c.get("cancelado_em") is None]
    cancelados = [c for c in todos if c.get("cancelado_em") is not None
                  and _no_periodo(c["cancelado_em"], inicio, fim)]
    base = ativos + cancelados
    com_dados = [c for c in base if _tem_dado(c)]
    cancelados_com_dados = {c["customer_id_externo"] for c in cancelados if _tem_dado(c)}
    regua = batch_scoring.regua_da_base(base)
    if regua is None or len(com_dados) < batch_scoring.MINIMO_LINHAS_POSICAO:
        return {"comparacao": None, "motivo_vazio": VAZIO_BASE_PEQUENA}
    if len(cancelados_com_dados) < MINIMO_DE_CANCELAMENTOS:
        return {"comparacao": None, "motivo_vazio": VAZIO_POUCOS_CANCELAMENTOS}

    graves_da_regua = {c["customer_id_externo"] for c in com_dados
                       if batch_scoring.criticidade_pela_regua(c, regua) == "critico"}
    # Sem `tenant_id`: a referência que o SDK usa não é trocada por esta conta.
    graves_do_modelo = {l["customer_id_externo"] for l in batch_scoring.pontuar_lista(base)
                        if l["criticality"] == "critico"
                        and l.get("risco_decidido_por") == batch_scoring.DECIDIDO_MODELO}
    return {"comparacao": {
        "clientes_com_dados": len(com_dados),
        "cancelamentos": len(cancelados_com_dados),
        "regua": {"marcou_grave": len(graves_da_regua),
                  "avisou_antes": len(graves_da_regua & cancelados_com_dados)},
        "modelo": {"marcou_grave": len(graves_do_modelo),
                   "avisou_antes": len(graves_do_modelo & cancelados_com_dados)},
    }, "motivo_vazio": None}


@router.get("/metrics/voluntario/regua-x-modelo")
async def regua_x_modelo(dias: Optional[int] = None,
                         tenant_id: str = Depends(get_tenant_id)) -> dict:
    """Entre os clientes com dado de comportamento, quantos cancelaram nos
    últimos `?dias=`, quantos cada avaliador marca como grave e quantos dos
    cancelamentos cada um tinha marcado, com os últimos dados do cliente antes
    de cancelar. Sem desfecho suficiente, `comparacao` é `null` e
    `motivo_vazio` diz por quê: a tela esconde a comparação."""
    dias, primeiro, inicio, fim = periodo_dos_dias(dias)
    try:
        resultado = insights_unificados.em_cache(
            tenant_id, f"regua_x_modelo:{dias}:{primeiro.isoformat()}",
            lambda: _comparar(tenant_id, inicio, fim))
    except clientes_importados.ConfiguracaoAusente as e:
        raise _500_base(e) from e
    return {"dias": dias, "minimo_de_cancelamentos": MINIMO_DE_CANCELAMENTOS, **resultado}

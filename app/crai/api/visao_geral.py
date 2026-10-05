"""crai/api/visao_geral.py — a página "Visão geral" do dashboard (Rodada 3, Fase 2).

    GET /metrics/visao-geral          os cartões do topo, nos últimos N dias
    GET /metrics/serie                os dois churns por dia
    GET /metrics/involuntario/funil   falhas, tentativas 1, 2 e 3, mensagem e desfecho
    GET /metrics/o-que-funciona       por causa, por oferta e por canal
    GET /atividade                    os eventos recentes, em frase pronta
    GET /extrato                      a memória de cálculo do mês (A ÚNICA COM A FEE)

O QUE ESTAS ROTAS GARANTEM:

  TENANT   toda leitura filtra pelo tenant do token. Não há id no caminho: uma
           empresa não tem como pedir nada de outra.
  LGPD     nenhuma resposta traz telefone, e-mail, CPF ou chave Pix. O único
           dado do cliente final é o nome que a lista de ciclos já mostra (lido
           da base na hora, nunca copiado), ou o id que a própria empresa usa.
           Nenhum texto de mensagem sai daqui.
  R11      a fee NÃO aparece em nenhuma destas rotas, só o líquido. A exceção é
           `GET /extrato`, que a seção 2.6 do plano define como a memória de
           cálculo: uma linha por cobrança recuperada ou cliente mantido, com o
           valor base, a fee e o líquido. Por isso ela exige `owner` ou `admin` e
           entra no registro de acesso.
  E5 / V4  o estorno entra no dia e no mês em que aconteceu, como linha
           negativa. O mês fechado não é reescrito.
  PLANO    o voluntário faz parte do plano premium: fora dele, os números do
           voluntário vêm `null` (não zero: zero diria que nada foi mantido).

"CICLOS ATIVOS", "AGUARDANDO ESCOLHA" E "RISCO GRAVE" SÃO DE AGORA, não do
período. Os outros números são do período pedido.

O CARTÃO "Comparação com grupo de controle" continua planejado: não tem rota.

Este módulo NÃO importa de `app.py` (é o `app.py` que o monta).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .. import config, simulador
from ..accounts import get_conta
from ..accounts.auth import PLANO_PREMIUM, exigir_papel
from ..agent.pix_codes import CAUSA_LEGIVEL
from ..churn_voluntary import clientes_importados, mantido
from ..churn_voluntary import retention_log
from ..dunning import ciclo_cobranca as cc
from . import ciclos as ciclos_api
from . import datas, registro_acesso
from . import voluntario as vol

logger = logging.getLogger(__name__)

router = APIRouter(tags=["visao-geral"])

LIMITE_PADRAO = 12
LIMITE_MAXIMO = 100
JANELA_DA_ATIVIDADE_DIAS = 30

ETAPAS_DO_FUNIL = (("falhas", "Cobranças que falharam"), ("tentativa_1", "Tentativa 1"),
                   ("tentativa_2", "Tentativa 2"), ("tentativa_3", "Tentativa 3"),
                   ("mensagem", "Mensagem"))

ROTULO_DO_CANAL = {"whatsapp": "WhatsApp", "email": "E-mail", "popup": "Aviso dentro do produto",
                   "sms": "SMS", "sem_canal": "Sem canal disponível"}
SEM_CADASTRO = "um cliente sem cadastro"


def _422(motivo: str, detalhe: str, campo: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"motivo": motivo, "detalhe": detalhe,
                                                  "campo": campo})


def _premium(conta: dict) -> bool:
    return conta.get("plano") == PLANO_PREMIUM


def _maiuscula(texto: str) -> str:
    return texto[:1].upper() + texto[1:] if texto else texto


def _ordinal(n: int) -> str:
    return f"{n}ª"


# ── Os nomes: lidos da base na hora, nunca copiados ───────────────────────

def _nomes_por_recorrencia(tenant_id: str, ids: list, incluir_simulados: bool = False) -> dict:
    """{id da recorrência: nome}. Os ciclos reais leem o nome na base; os da
    simulação do gateway, o nome do cliente fictício."""
    try:
        nomes = clientes_importados.nomes_por_recorrencia(tenant_id, ids)
    except clientes_importados.ConfiguracaoAusente:
        nomes = {}
    if incluir_simulados:
        nomes = {**simulador.na_simulacao(tenant_id, lambda: simulador.nomes(tenant_id), {}),
                 **nomes}
    return nomes


def _nomes_por_cliente(tenant_id: str, ids: list, incluir_simulados: bool = False) -> dict:
    try:
        nomes = {cid: l.get("nome") for cid, l in
                 clientes_importados.obter_varios(tenant_id, ids).items()}
    except clientes_importados.ConfiguracaoAusente:
        nomes = {}
    if incluir_simulados:
        ficticios = simulador.na_simulacao(tenant_id, lambda: simulador.retencoes(tenant_id), [])
        nomes = {**{vol.insights_unificados._id_cru(r["user_id"]): r["nome"] for r in ficticios},
                 **nomes}
    return nomes


def _leitor(tenant_id: str, incluir_simulados: bool):
    """`ler(fn)`: `fn(d)` sobre o que é real e, se pedido, sobre a simulação do
    gateway da empresa (`d` é o deslocamento do relógio simulado)."""
    return lambda fn: simulador.com_simulados(tenant_id, incluir_simulados, fn)


def _id(prefixo: str, linha: dict, chave: str = "id") -> str:
    """O id de uma linha de atividade ou de extrato: o da simulação não colide
    com o real."""
    return f"{'sim-' if simulador.e_simulada(linha) else ''}{prefixo}-{linha[chave]}"


# ── GET /metrics/visao-geral ──────────────────────────────────────────────

def numeros_do_involuntario(tenant_id: str, inicio: datetime, fim: datetime,
                            incluir_simulados: bool = False) -> dict:
    """Os números CRUS do involuntário no período (somáveis): o líquido, as
    contagens, e o que é de agora (ativos e aguardando escolha)."""
    ler = _leitor(tenant_id, incluir_simulados)
    resumo = ciclos_api._resumo(
        ler(lambda d: cc.ciclos_com_desfecho(tenant_id, inicio + d, fim + d)),
        ler(lambda d: cc.estornos_no_periodo(tenant_id, inicio + d, fim + d)))
    return {"recuperado_liquido": resumo["valor_liquido_recuperado"],
            "cobrancas_recuperadas": resumo["recuperados"],
            "ciclos_com_desfecho": resumo["recuperados"] + resumo["encerrados_sem_recuperacao"],
            "ciclos_ativos": ler(lambda d: cc.contar_ativos(tenant_id)),
            "aguardando_escolha": ler(lambda d: cc.contar_aguardando_escolha(tenant_id))}


def numeros_do_voluntario(tenant_id: str, inicio: datetime, fim: datetime,
                          incluir_simulados: bool = False) -> dict:
    """Os números CRUS do voluntário: o líquido mantido no período e, de agora,
    quantos clientes estão em risco grave e quantos deles já têm oferta."""
    resumo = mantido.resumo(
        vol.mantidas_do_periodo(tenant_id, inicio, fim, incluir_simulados),
        vol.estornos_do_periodo(tenant_id, inicio, fim, incluir_simulados))
    graves = [l["customer_id_externo"] for l in vol._ranking(tenant_id)
              if l["criticality"] == "critico"]
    com_oferta = vol.abordagens_por_cliente(tenant_id) if graves else {}
    return {"retido_liquido": resumo["valor_liquido_mantido"],
            "clientes_mantidos": resumo["clientes_mantidos"],
            "clientes_risco_grave": len(graves),
            "risco_grave_com_oferta": sum(1 for g in graves if g in com_oferta)}


@router.get("/metrics/visao-geral")
async def visao_geral(dias: Optional[int] = None, incluir_simulados: bool = False,
                      conta: dict = Depends(get_conta)) -> dict:
    """Os cartões da visão geral nos últimos `?dias=` (1..365, padrão 30): o
    mantido somando os dois churns, o recuperado do involuntário, o retido do
    voluntário, e, de agora, ciclos ativos, aguardando escolha e clientes em
    risco grave. Tudo líquido. A taxa de recuperação é `null` sem desfecho no
    período. Fora do plano premium, os campos do voluntário vêm `null`."""
    tenant_id = conta["tenant_id"]
    dias, primeiro, inicio, fim = vol.periodo_dos_dias(dias)
    inv = numeros_do_involuntario(tenant_id, inicio, fim, incluir_simulados)
    com_desfecho = inv["ciclos_com_desfecho"]
    resposta = {
        "dias": dias,
        "periodo": {"de": primeiro.isoformat(), "ate": (fim - timedelta(days=1)).date().isoformat()},
        "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
        "recuperado_involuntario": inv["recuperado_liquido"],
        "cobrancas_recuperadas": inv["cobrancas_recuperadas"],
        "ciclos_ativos": inv["ciclos_ativos"],
        "aguardando_escolha": inv["aguardando_escolha"],
        "taxa_recuperacao": (round(inv["cobrancas_recuperadas"] / com_desfecho, 4)
                             if com_desfecho else None),
        "ciclos_com_desfecho": com_desfecho,
        "retido_voluntario": None, "clientes_mantidos": None,
        "clientes_risco_grave": None, "risco_grave_com_oferta": None,
        # Rodada 4, Fase 3 (M6): a empresa está em período de piloto AGORA.
        # Só o aviso; o valor da taxa continua fora desta rota.
        "piloto": config.tenant_em_piloto(tenant_id),
    }
    mantido_total = inv["recuperado_liquido"]
    if _premium(conta):
        mantido.sincronizar_sem_levantar(tenant_id)
        v = numeros_do_voluntario(tenant_id, inicio, fim, incluir_simulados)
        resposta.update(retido_voluntario=v["retido_liquido"],
                        clientes_mantidos=v["clientes_mantidos"],
                        clientes_risco_grave=v["clientes_risco_grave"],
                        risco_grave_com_oferta=v["risco_grave_com_oferta"])
        mantido_total = round(mantido_total + v["retido_liquido"], 2)
    resposta["mantido"] = mantido_total
    return resposta


# ── GET /metrics/serie ────────────────────────────────────────────────────

def serie_do_involuntario(tenant_id: str, dias: int, primeiro, inicio: datetime,
                          fim: datetime, incluir_simulados: bool = False) -> dict:
    """{dia: líquido do dia}: as recuperações do dia menos os estornos do dia."""
    ler = _leitor(tenant_id, incluir_simulados)
    por_dia: dict = {}
    for linha in ler(lambda d: cc.ciclos_com_desfecho(tenant_id, inicio + d, fim + d)):
        por_dia.setdefault(str(linha["desfecho_em"])[:10], []).append(linha)
    estornos: dict = {}
    for e in ler(lambda d: cc.estornos_no_periodo(tenant_id, inicio + d, fim + d)):
        estornos.setdefault(str(e["recebido_em"])[:10], []).append(e)
    serie = {}
    for i in range(dias):
        dia = (primeiro + timedelta(days=i)).isoformat()
        serie[dia] = ciclos_api._resumo(por_dia.get(dia, []),
                                        estornos.get(dia, []))["valor_liquido_recuperado"]
    return serie


@router.get("/metrics/serie")
async def serie_dos_dois_churns(dias: Optional[int] = None, incluir_simulados: bool = False,
                                conta: dict = Depends(get_conta)) -> dict:
    """As duas séries por dia, líquidas, dos últimos `?dias=` até hoje: o
    involuntário pelo dia do desfecho, o voluntário pelo dia do aceite. Fora do
    plano premium, `voluntario` vem `null` em todo ponto."""
    tenant_id = conta["tenant_id"]
    dias, primeiro, inicio, fim = vol.periodo_dos_dias(dias)
    inv = serie_do_involuntario(tenant_id, dias, primeiro, inicio, fim, incluir_simulados)
    do_voluntario = None
    if _premium(conta):
        mantido.sincronizar_sem_levantar(tenant_id)
        do_voluntario = {p["dia"]: p["valor_liquido_mantido"] for p in vol.serie_do_voluntario(
            tenant_id, dias, primeiro, inicio, fim, incluir_simulados)}
    return {"dias": dias, "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
            "pontos": [{"dia": dia, "involuntario": valor,
                        "voluntario": do_voluntario[dia] if do_voluntario is not None else None}
                       for dia, valor in inv.items()]}


# ── GET /metrics/involuntario/funil ───────────────────────────────────────

def funil_do_periodo(tenant_id: str, inicio: datetime, fim: datetime,
                     incluir_simulados: bool = False) -> dict:
    """O funil da COORTE aberta em [inicio, fim): quantas cobranças chegaram a
    cada etapa, o valor delas, e quantas foram recuperadas ali. O valor é o da
    cobrança (o mesmo `valor_cobranca` da lista de ciclos), nunca a fee."""
    coorte = _leitor(tenant_id, incluir_simulados)(
        lambda d: cc.coorte_do_periodo(tenant_id, inicio + d, fim + d))
    etapas = {chave: {"chegaram": 0, "valor": 0.0, "recuperados_aqui": 0,
                      "valor_recuperado_aqui": 0.0} for chave, _ in ETAPAS_DO_FUNIL}
    desfecho = {"recuperados": 0, "encerrados": 0, "em_andamento": 0}
    for c in coorte:
        valor = float(c["valor"] or 0.0)
        recuperado = c["status"] == cc.STATUS_RECUPERADO
        desfecho["recuperados" if recuperado
                 else "encerrados" if c["status"] == cc.STATUS_ENCERRADO
                 else "em_andamento"] += 1
        chegou = ["falhas"]
        paga = None
        for t in c["tentativas"]:
            if 1 <= t["numero"] <= 3:
                chegou.append(f"tentativa_{t['numero']}")
                if t["resultado"] == cc.PAGA:
                    paga = f"tentativa_{t['numero']}"
        if c["chegou_a_mensagem"]:
            chegou.append("mensagem")
        for chave in chegou:
            etapas[chave]["chegaram"] += 1
            etapas[chave]["valor"] += valor
        onde = paga or ("mensagem" if c["chegou_a_mensagem"] else None)
        if recuperado and onde:
            etapas[onde]["recuperados_aqui"] += 1
            etapas[onde]["valor_recuperado_aqui"] += valor
    return {"etapas": [{"etapa": chave, "rotulo": rotulo,
                        "chegaram": etapas[chave]["chegaram"],
                        "valor": round(etapas[chave]["valor"], 2),
                        "recuperados_aqui": etapas[chave]["recuperados_aqui"],
                        "valor_recuperado_aqui": round(etapas[chave]["valor_recuperado_aqui"], 2)}
                       for chave, rotulo in ETAPAS_DO_FUNIL],
            "desfecho": desfecho}


@router.get("/metrics/involuntario/funil")
async def funil_do_involuntario(mes: Optional[str] = None, incluir_simulados: bool = False,
                                conta: dict = Depends(get_conta)) -> dict:
    """O funil das cobranças que falharam no mês (`?mes=AAAA-MM`; padrão: o
    corrente): falhas, tentativa 1, 2 e 3, mensagem, com quantas chegaram a
    cada etapa, o valor delas e quantas foram recuperadas ali; e o desfecho
    (recuperadas, encerradas sem recuperação, em andamento)."""
    rotulo, inicio, fim = vol.periodo_do_mes(mes)
    return {"mes": rotulo, "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
            **funil_do_periodo(conta["tenant_id"], inicio, fim, incluir_simulados)}


# ── GET /metrics/o-que-funciona ───────────────────────────────────────────

def _itens(contagem: dict, com_valor: bool = False) -> list:
    """{rotulo: {casos, sucessos, valor}} → a lista da tela, do mais comum ao menos."""
    itens = []
    for rotulo, c in contagem.items():
        item = {"rotulo": rotulo, "casos": c["casos"], "sucessos": c["sucessos"],
                "taxa": round(c["sucessos"] / c["casos"], 4) if c["casos"] else None}
        if com_valor:
            item["valor_liquido"] = round(c["valor"], 2)
        itens.append(item)
    return sorted(itens, key=lambda i: (-i["casos"], i["rotulo"]))


def _somar(contagem: dict, rotulo: str, sucesso: bool, valor: float = 0.0) -> None:
    c = contagem.setdefault(rotulo, {"casos": 0, "sucessos": 0, "valor": 0.0})
    c["casos"] += 1
    c["sucessos"] += 1 if sucesso else 0
    c["valor"] += valor


def o_que_funciona_no_periodo(tenant_id: str, inicio: datetime, fim: datetime,
                              com_voluntario: bool, incluir_simulados: bool = False) -> dict:
    """Só sobre o que TEVE desfecho no período. Causas: taxa de recuperação por
    causa da falha. Ofertas: taxa de aceite por oferta. Canais: quantas
    mensagens tiveram resposta (a cobrança recuperada depois da mensagem, ou a
    oferta aceita), por canal."""
    causas: dict = {}
    canais: dict = {}
    for c in _leitor(tenant_id, incluir_simulados)(
            lambda d: cc.desfechos_com_caminho(tenant_id, inicio + d, fim + d)):
        recuperado = c["status"] == cc.STATUS_RECUPERADO
        causa = CAUSA_LEGIVEL.get(c["causa_original"], c["causa_original"])
        liquido = (float(c["valor"] or 0) - float(c.get("valor_estornado") or 0)
                   - float(c["fee"] or 0) + float(c.get("fee_estornada") or 0)) if recuperado else 0.0
        _somar(causas, causa, recuperado, liquido)
        canal = c.get("canal_da_mensagem")
        if canal and canal != "sem_canal":
            # A resposta à mensagem: recuperado sem ter sido por uma tentativa.
            _somar(canais, ROTULO_DO_CANAL.get(canal, canal),
                   recuperado and c.get("tentativa_paga") is None)
    ofertas: dict = {}
    if com_voluntario:
        for ciclo in vol.ciclos_com_oferta(tenant_id, incluir_simulados):
            if ciclo.get("accepted") is None or not vol._no_periodo(ciclo.get("desfecho_em"),
                                                                     inicio, fim):
                continue
            aceitou = ciclo["accepted"] == 1
            oferta = ciclo["offer_type"]
            _somar(ofertas, _maiuscula(retention_log.ROTULOS_DE_OFERTA.get(oferta, oferta)), aceitou)
            if ciclo.get("offer_sent") and ciclo.get("channel"):
                _somar(canais, ROTULO_DO_CANAL.get(ciclo["channel"], ciclo["channel"]), aceitou)
    return {"causas": _itens(causas, com_valor=True), "ofertas": _itens(ofertas),
            "canais": _itens(canais)}


@router.get("/metrics/o-que-funciona")
async def o_que_funciona(dias: Optional[int] = None, incluir_simulados: bool = False,
                         conta: dict = Depends(get_conta)) -> dict:
    """Nos últimos `?dias=`, sobre o que já teve desfecho: por causa da falha
    (casos, recuperadas e o líquido), por oferta (casos e aceites) e por canal
    (mensagens e respostas). Sem caso nenhum, a lista vem vazia. Fora do plano
    premium, as ofertas vêm vazias e os canais contam só o involuntário."""
    dias, _, inicio, fim = vol.periodo_dos_dias(dias)
    return {"dias": dias, "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
            **o_que_funciona_no_periodo(conta["tenant_id"], inicio, fim, _premium(conta),
                                        incluir_simulados)}


# ── GET /atividade ────────────────────────────────────────────────────────

def _descricao_da_recuperacao(c: dict) -> str:
    if c.get("tentativa_paga"):
        return f"recuperada na {_ordinal(int(c['tentativa_paga']))} tentativa"
    if c.get("mensagem_confirmada_em"):
        return "recuperada depois da mensagem"
    return "recuperada"


def _evento(identidade: str, quando, tipo: str, texto: str, valor=None,
            simulado=False) -> Optional[dict]:
    instante = datas.com_fuso(quando)
    if instante is None:
        return None
    # Tudo no fuso da instalação: o ciclo grava hora local e o voluntário, UTC.
    instante = instante.astimezone(datas.fuso_local())
    return {"_instante": instante, "id": identidade,
            "em": instante.isoformat(timespec="seconds"), "tipo": tipo,
            "texto": _maiuscula(texto), "valor": valor, "simulado": bool(simulado)}


def eventos_do_involuntario(tenant_id: str, desde: datetime, ate: datetime, limite: int,
                            incluir_simulados: bool = False) -> list:
    """Os eventos recentes do involuntário, em frase pronta. O único dado do
    cliente final é o nome da base; sem nome, a frase diz "um cliente sem
    cadastro". Valor só em recuperação, e é o líquido."""
    ler = _leitor(tenant_id, incluir_simulados)
    recente = ler(lambda d: cc.atividade_recente(tenant_id, desde + d, limite))
    desfechos = sorted((c for c in ler(lambda d: cc.desfechos_com_caminho(
        tenant_id, desde + d, ate + d)) if c["estado"] == cc.RECUPERADO),
        key=lambda c: str(c["desfecho_em"]))[-limite:]
    estornos = sorted(ler(lambda d: cc.estornos_no_periodo(tenant_id, desde + d, ate + d)),
                      key=lambda e: str(e["recebido_em"]))[-limite:]
    nomes = _nomes_por_recorrencia(tenant_id, [
        l["id_recorrencia"] for grupo in (recente["tentativas_falhas"],
                                          recente["mensagens_enviadas"],
                                          recente["aguardando_escolha"], desfechos, estornos)
        for l in grupo], incluir_simulados)
    quem = lambda l: nomes.get(l["id_recorrencia"]) or SEM_CADASTRO   # noqa: E731
    sim = simulador.e_simulada
    eventos = []
    for c in desfechos:
        liquido = round(float(c["valor"] or 0) - float(c["fee"] or 0), 2)
        eventos.append(_evento(_id("rec", c), c["desfecho_em"], "recuperado",
                               f"Cobrança de {quem(c)} {_descricao_da_recuperacao(c)}", liquido,
                               sim(c)))
    for t in recente["tentativas_falhas"]:
        causa = CAUSA_LEGIVEL.get(t["causa_original"], "")
        texto = f"Tentativa {t['numero']} não passou para {quem(t)}"
        eventos.append(_evento(_id("ten", t), t["resultado_em"], "tentativa_falhou",
                               f"{texto}: {causa[:1].lower() + causa[1:]}" if causa else texto,
                               None, sim(t)))
    for c in recente["mensagens_enviadas"]:
        canal = ROTULO_DO_CANAL.get(c.get("canal_da_mensagem"))
        eventos.append(_evento(
            _id("msg", c), c["mensagem_confirmada_em"], "mensagem_enviada",
            f"Mensagem enviada {'por ' + canal + ' ' if canal else ''}para {quem(c)}",
            None, sim(c)))
    for c in recente["aguardando_escolha"]:
        eventos.append(_evento(_id("esc", c), c["aguardando_escolha_em"], "escolha",
                               f"Mensagens sugeridas para {quem(c)}, aguardando a sua escolha",
                               None, sim(c)))
    for e in estornos:
        parte = "O valor recuperado" if e["total"] else "Parte do valor recuperado"
        eventos.append(_evento(
            _id("est", e), e["recebido_em"], "estorno",
            f"{parte} de {quem(e)} voltou ao cliente dentro do prazo e saiu do recuperado",
            None, sim(e)))
    return [e for e in eventos if e is not None]


def eventos_do_voluntario(tenant_id: str, desde: datetime, ate: datetime, limite: int,
                          incluir_simulados: bool = False) -> list:
    """Os eventos recentes do voluntário: oferta aceita (com o líquido
    mantido), estorno por cancelamento no prazo, e cliente que entrou em risco
    grave. O cliente aparece pelo nome da base ou pelo id que a empresa usa."""
    mantidas = sorted(vol.mantidas_do_periodo(tenant_id, desde, ate, incluir_simulados),
                      key=lambda m: m["aceito_em"])[-limite:]
    estornos = sorted(vol.estornos_do_periodo(tenant_id, desde, ate, incluir_simulados),
                      key=lambda e: e["cancelamento_em"])[-limite:]
    graves = [c for c in vol.ciclos_com_oferta(tenant_id, incluir_simulados)
              if c.get("criticality") == "critico"
              and vol._no_periodo(c.get("registrado_em"), desde, ate)][-limite:]
    ids = ([m["cliente_id"] for m in mantidas] + [e["cliente_id"] for e in estornos]
           + [vol.insights_unificados._id_cru(c["user_id"]) for c in graves])
    nomes = _nomes_por_cliente(tenant_id, ids, incluir_simulados)
    quem = lambda cid: nomes.get(cid) or f"cliente {cid}"        # noqa: E731
    eventos = []
    for m in mantidas:
        oferta = retention_log.ROTULOS_DE_OFERTA.get(m["offer_type"], m["offer_type"])
        eventos.append(_evento(_id("ret", m), m["aceito_em"], "oferta_aceita",
                               f"{quem(m['cliente_id'])} aceitou {oferta}", m["liquido"],
                               m["simulado"]))
    for e in estornos:
        dias = (datetime.fromisoformat(e["cancelamento_em"])
                - datetime.fromisoformat(e["aceito_em"])).days
        eventos.append(_evento(
            _id("ret", e) + "-estorno", e["cancelamento_em"], "estorno",
            f"{quem(e['cliente_id'])} cancelou {dias} {'dia' if dias == 1 else 'dias'} depois "
            "do aceite; o valor saiu do mantido", None, e["simulado"]))
    for c in graves:
        eventos.append(_evento(
            _id("gra", c), c["registrado_em"], "risco_grave",
            f"{quem(vol.insights_unificados._id_cru(c['user_id']))} entrou em risco grave",
            None, simulador.e_simulada(c) or vol._ciclo_e_sorteio(c)))
    return [e for e in eventos if e is not None]


@router.get("/atividade")
async def atividade(limite: Optional[int] = None, incluir_simulados: bool = False,
                    conta: dict = Depends(get_conta)) -> dict:
    """Os eventos dos últimos 30 dias, do mais novo ao mais antigo (`?limite=`
    1..100, padrão 12), cada um em frase pronta: cobrança recuperada, tentativa
    que não passou, mensagem enviada, escolha pendente, estorno, oferta aceita e
    cliente em risco grave. `valor`, quando há, é líquido."""
    tenant_id = conta["tenant_id"]
    limite = LIMITE_PADRAO if limite is None else limite
    if not 1 <= limite <= LIMITE_MAXIMO:
        raise _422("limite_invalido", f"esperado inteiro entre 1 e {LIMITE_MAXIMO}", "limite")
    agora = datas.agora_local()
    desde, ate = agora - timedelta(days=JANELA_DA_ATIVIDADE_DIAS), agora + timedelta(seconds=1)
    eventos = eventos_do_involuntario(tenant_id, desde, ate, limite, incluir_simulados)
    if _premium(conta):
        mantido.sincronizar_sem_levantar(tenant_id)
        eventos += eventos_do_voluntario(tenant_id, desde, ate, limite, incluir_simulados)
    eventos.sort(key=lambda e: (e["_instante"], e["id"]), reverse=True)
    # As frases trazem o nome do cliente final, lido da base: leitura de dado de titular.
    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_ATIVIDADE, conta.get("papel"), agora)
    return {"dias": JANELA_DA_ATIVIDADE_DIAS,
            "atividades": [{k: v for k, v in e.items() if not k.startswith("_")}
                           for e in eventos[:limite]]}


# ── GET /extrato ──────────────────────────────────────────────────────────

def linhas_do_involuntario(tenant_id: str, inicio: datetime, fim: datetime,
                           incluir_simulados: bool = False) -> list:
    """Uma linha POSITIVA por cobrança recuperada no período (valor e fee da
    época) e uma NEGATIVA por estorno no prazo recebido no período."""
    ler = _leitor(tenant_id, incluir_simulados)
    sim = simulador.e_simulada
    caminho = {(sim(c), c["id"]): c for c in ler(
        lambda d: cc.desfechos_com_caminho(tenant_id, inicio + d, fim + d))}
    linhas = ler(lambda d: cc.extrato_do_periodo(tenant_id, inicio + d, fim + d))
    nomes = _nomes_por_recorrencia(tenant_id, [l["id_recorrencia"] for l in linhas],
                                   incluir_simulados)
    saida = []
    for posicao, l in enumerate(linhas):
        recuperacao = l["tipo"] == "recuperacao"
        c = caminho.get((sim(l), l["ciclo_id"]), {})
        saida.append({
            "id": (_id("rec", l, "ciclo_id") if recuperacao
                   else f"{_id('est', l, 'ciclo_id')}-{posicao}"),
            "data": datas.iso_com_fuso(l["quando"]),
            "cliente": nomes.get(l["id_recorrencia"]),
            "id_cliente": l["id_recorrencia"],
            "origem": "involuntario",
            "tipo": "recuperacao" if recuperacao else "estorno",
            "descricao": (_maiuscula(_descricao_da_recuperacao(c).replace("recuperada", "recuperado"))
                          if recuperacao else "Devolução ao cliente dentro do prazo: estorno"),
            "valor_base": l["valor_base"], "fee": l["fee"], "liquido": l["liquido"],
            # Só na linha de piloto: a fee que seria cobrada fora dele (M3).
            "fee_fora_do_piloto": l.get("fee_fora_do_piloto"),
            # Na linha da recuperação: se ela foi estornada por inteiro depois.
            "estornado": (not recuperacao) or c.get("status") == cc.STATUS_ENCERRADO,
            "simulado": sim(l)})
    return saida


def linhas_do_voluntario(tenant_id: str, inicio: datetime, fim: datetime,
                         incluir_simulados: bool = False) -> list:
    """Uma linha POSITIVA por cliente mantido no período e uma NEGATIVA por
    estorno (cancelamento no prazo) no período."""
    mantidas = vol.mantidas_do_periodo(tenant_id, inicio, fim, incluir_simulados)
    estornos = vol.estornos_do_periodo(tenant_id, inicio, fim, incluir_simulados)
    nomes = _nomes_por_cliente(tenant_id, [m["cliente_id"] for m in mantidas + estornos],
                               incluir_simulados)
    saida = []
    for m in mantidas:
        oferta = retention_log.ROTULOS_DE_OFERTA.get(m["offer_type"], m["offer_type"])
        saida.append({
            "id": _id("ret", m), "data": datas.iso_com_fuso(m["aceito_em"]),
            "cliente": nomes.get(m["cliente_id"]), "id_cliente": m["cliente_id"],
            "origem": "voluntario", "tipo": "mantido",
            "descricao": f"Aceitou {oferta}",
            "mrr": round(float(m["mrr"]), 2), "desconto": round(float(m["desconto"]), 2),
            "valor_base": round(float(m["valor_base"]), 2), "fee": round(float(m["fee"]), 2),
            "fee_fora_do_piloto": _fora_do_piloto(m),
            "liquido": m["liquido"], "estornado": m["estornada"], "simulado": m["simulado"]})
    for e in estornos:
        dias = (datetime.fromisoformat(e["cancelamento_em"])
                - datetime.fromisoformat(e["aceito_em"])).days
        saida.append({
            "id": _id("ret", e) + "-estorno", "data": datas.iso_com_fuso(e["cancelamento_em"]),
            "cliente": nomes.get(e["cliente_id"]), "id_cliente": e["cliente_id"],
            "origem": "voluntario", "tipo": "estorno",
            "descricao": f"Cancelou {dias} {'dia' if dias == 1 else 'dias'} depois do aceite: estorno",
            "valor_base": -round(float(e["valor_estornado"] or 0), 2),
            "fee": -round(float(e["fee_estornada"] or 0), 2),
            # O cancelamento no prazo devolve a retenção inteira (E2): a fee
            # que seria cobrada volta inteira também.
            "fee_fora_do_piloto": _fora_do_piloto(e, sinal=-1),
            "liquido": -e["liquido_devolvido"], "estornado": True, "simulado": e["simulado"]})
    return saida


def _fora_do_piloto(linha: dict, sinal: int = 1) -> Optional[float]:
    """A fee que seria cobrada fora do piloto, de uma linha do valor mantido;
    `None` se a linha não é de piloto."""
    valor = linha.get("fee_fora_do_piloto")
    return None if valor is None else sinal * round(float(valor), 2)


def resumo_do_piloto(tenant_id: str, linhas: list) -> dict:
    """M3 e M6 para o extrato: se a empresa está em piloto AGORA (`ativo`), e a
    soma da fee que seria cobrada nas linhas de piloto do período
    (`fee_fora_do_piloto`; `None` quando nenhuma linha é de piloto). Uma
    empresa que saiu do piloto continua vendo a coluna nos meses em que ele
    valeu."""
    de_piloto = [l["fee_fora_do_piloto"] for l in linhas
                 if l.get("fee_fora_do_piloto") is not None]
    return {"ativo": config.tenant_em_piloto(tenant_id),
            "fee_fora_do_piloto": round(sum(de_piloto), 2) if de_piloto else None}


def _extrato_do_mes(conta: dict, mes: Optional[str], incluir_simulados: bool, rota: str) -> dict:
    """O extrato do mês, para a rota em JSON e para a do arquivo: a mesma conta
    nas duas. Exige `owner` ou `admin` e grava o acesso com o nome da `rota`."""
    papel = exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    rotulo, inicio, fim = vol.periodo_do_mes(mes)
    linhas = linhas_do_involuntario(tenant_id, inicio, fim, incluir_simulados)
    if _premium(conta):
        mantido.sincronizar_sem_levantar(tenant_id)
        linhas += linhas_do_voluntario(tenant_id, inicio, fim, incluir_simulados)
    linhas.sort(key=lambda l: (l["data"] or "", l["id"]), reverse=True)
    registro_acesso.registrar(tenant_id, rota, papel, datas.agora_local())
    return {"mes": rotulo, "inicio": datas.iso_com_fuso(inicio), "fim": datas.iso_com_fuso(fim),
            "linhas": linhas,
            "totais": {"valor_base": round(sum(l["valor_base"] for l in linhas), 2),
                       "fee": round(sum(l["fee"] for l in linhas), 2),
                       "liquido": round(sum(l["liquido"] for l in linhas), 2)},
            "piloto": resumo_do_piloto(tenant_id, linhas),
            "meses_de_mrr": mantido.MESES_DE_MRR_MANTIDOS}


@router.get("/extrato")
async def extrato(mes: Optional[str] = None, incluir_simulados: bool = False,
                  conta: dict = Depends(get_conta)) -> dict:
    """A memória de cálculo do mês (`?mes=AAAA-MM`; padrão: o corrente): uma
    linha por cobrança recuperada ou cliente mantido, com o valor base, a fee
    da CRAI e o líquido; e uma linha negativa por estorno, no mês em que ele
    aconteceu. Com `GET /extrato/csv`, são as ÚNICAS rotas que devolvem a fee.
    Exige `owner` ou `admin` e entra no registro de acesso. Fora do plano
    premium, só o involuntário.

    Modo piloto (Rodada 4, Fase 3): em linha de piloto a `fee` é zero, o
    `liquido` é o valor inteiro e `fee_fora_do_piloto` traz a fee que seria
    cobrada (nas outras linhas, `null`). `piloto` diz se a empresa está em
    piloto agora e a soma dessa coluna no mês."""
    return _extrato_do_mes(conta, mes, incluir_simulados, registro_acesso.ROTA_EXTRATO)


# ── GET /extrato/csv (Rodada 4) ───────────────────────────────────────────

COLUNAS_DO_CSV = ("Data", "Cliente", "Identificador", "Origem", "O que aconteceu", "Valor (R$)",
                  "Taxa da CRAI (R$)", "Líquido para você (R$)", "Situação", "Demonstração")
ORIGEM_NO_CSV = {"involuntario": "Involuntário", "voluntario": "Voluntário"}
# A marca que diz ao Excel que o arquivo é UTF-8 (os acentos saem certos).
MARCA_DE_UTF8 = chr(0xFEFF)
# Rodada 4, Fase 3 (M3): a coluna própria do piloto. Só entra no arquivo quando
# a empresa está em piloto ou o mês tem linha de piloto; fica logo depois da taxa.
COLUNA_DO_PILOTO = "Taxa fora do piloto (R$)"
POSICAO_DA_COLUNA_DO_PILOTO = COLUNAS_DO_CSV.index("Taxa da CRAI (R$)") + 1


def _celula_do_csv(valor) -> str:
    """Uma célula do arquivo. Número: duas casas e vírgula decimal (o Excel em
    português lê como número). Texto: se começar por `=`, `+`, `-`, `@`, tab ou
    retorno de carro, ganha um apóstrofo na frente, para a planilha não o
    executar como fórmula (o nome do cliente vem da base da empresa); e vai
    entre aspas se tiver `;`, aspas ou quebra de linha."""
    if isinstance(valor, bool):
        valor = "Sim" if valor else "Não"
    if isinstance(valor, (int, float)):
        return f"{float(valor):.2f}".replace(".", ",")
    texto = "" if valor is None else str(valor)
    if texto[:1] in ("=", "+", "-", "@", "\t", "\r"):
        texto = "'" + texto
    if any(c in texto for c in (";", '"', "\n", "\r")):
        texto = '"' + texto.replace('"', '""') + '"'
    return texto


def _situacao(linha: dict) -> str:
    if linha.get("tipo") == "estorno":
        return "Estorno"
    return "Estornado" if linha.get("estornado") else "Confirmado"


def extrato_em_csv(linhas: list, com_piloto: bool = False) -> str:
    """As linhas do extrato num CSV para o Excel em português: `;` entre as
    colunas, vírgula decimal, uma linha de cabeçalho e uma de total. Com
    `com_piloto`, a coluna "Taxa fora do piloto" entra depois da taxa: o valor
    nas linhas de piloto, vazio nas outras, e a soma na linha de total."""
    def data(l):
        quando = datas.com_fuso(l.get("data"))
        return quando.strftime("%d/%m/%Y") if quando else ""
    saida = [COLUNAS_DO_CSV]
    for l in linhas:
        saida.append((data(l), l.get("cliente") or "Cliente sem cadastro", l.get("id_cliente"),
                      ORIGEM_NO_CSV.get(l.get("origem"), l.get("origem")), l.get("descricao"),
                      float(l["valor_base"]), float(l["fee"]), float(l["liquido"]),
                      _situacao(l), bool(l.get("simulado"))))
    saida.append(("", "Total", "", "", "",
                  round(sum(float(l["valor_base"]) for l in linhas), 2),
                  round(sum(float(l["fee"]) for l in linhas), 2),
                  round(sum(float(l["liquido"]) for l in linhas), 2), "", ""))
    if com_piloto:
        p = POSICAO_DA_COLUNA_DO_PILOTO
        fora = [l.get("fee_fora_do_piloto") for l in linhas]
        coluna = ([COLUNA_DO_PILOTO] + [None if f is None else float(f) for f in fora]
                  + [round(sum(float(f) for f in fora if f is not None), 2)])
        saida = [tuple(linha[:p]) + (celula,) + tuple(linha[p:])
                 for linha, celula in zip(saida, coluna)]
    return "\r\n".join(";".join(_celula_do_csv(c) for c in linha) for linha in saida) + "\r\n"


@router.get("/extrato/csv")
async def extrato_csv(mes: Optional[str] = None, incluir_simulados: bool = False,
                      conta: dict = Depends(get_conta)) -> Response:
    """O mesmo extrato de `GET /extrato`, num arquivo CSV para o financeiro
    (`?mes=AAAA-MM`; padrão: o corrente). Exige `owner` ou `admin` (membro
    recebe 403) e entra no registro de acesso, com o nome próprio desta rota.
    Traz a fee. Nenhum contato: do cliente, só o nome e o identificador que o
    extrato já mostra."""
    corpo = _extrato_do_mes(conta, mes, incluir_simulados, registro_acesso.ROTA_EXTRATO_CSV)
    piloto = corpo["piloto"]
    com_piloto = piloto["ativo"] or piloto["fee_fora_do_piloto"] is not None
    return Response(content=MARCA_DE_UTF8 + extrato_em_csv(corpo["linhas"], com_piloto),
                    media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="crai-extrato-{corpo["mes"]}.csv"'})

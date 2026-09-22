"""crai/churn_voluntary/disparo_lote.py — "trate todos os clientes que precisam".

Recebe um conjunto de clientes (o corpo da requisição, ou a base importada do
tenant) e, para cada um que PRECISA: monta as candidatas, escolhe uma (o
bandit), escolhe o canal (a mesma cadeia de `choose_channel`) e registra o
envio — simulado nesta fase — no `retention_log`.

QUEM PRECISA: criticidade `critico` ou `alto` (`CRITERIO_INCLUSAO`). Não é um
corte numérico de risco: a criticidade já carrega as duas portas do produto
(risco e valor da conta) e o sinal absoluto de desengajamento da régua da
base — ver `docs/CONTRATO_PAINEL.md`, seção do `/insights`.

QUEM É PULADO, e por quê (cada um vira uma linha em `pulados`):

    dado_insuficiente   sem `days_since_last` E sem `features_used_30d`.
                        Risco `None` não é risco zero; cliente sem sinal
                        suficiente é pulado, NUNCA contatado.
    abaixo_do_criterio  criticidade `padrao`.
    cadastro_invalido   identificador vazio, perfil fora de PROFILES ou MRR
                        que não é número. Uma linha torta não derruba o
                        lote — mesmo contrato do `/clientes/importar`.
    ciclo_aberto        já existe ciclo de retenção sem desfecho para este
                        cliente. Rodar o lote duas vezes não contata ninguém
                        duas vezes.

POR QUE NÃO RODA O GRAFO LANGGRAPH POR CLIENTE: o grafo chama a Claude API,
o HubSpot e o WhatsApp a cada nó — segundos por cliente, e um lote de
centenas levaria minutos. Aqui o texto da vencedora é o template da própria
oferta (marcado `origem_texto: "template"`), o bandit e a cadeia de canal
são os MESMOS do grafo (`classificar_ofertas`, `choose_channel`,
`montar_candidatas`), e o envio é registrado sem sair do processo. É uma
decisão declarada: o lote decide igual e escreve menos.
"""

from datetime import datetime, timezone

from . import batch_scoring, clientes_importados, insights_unificados, retention_log
from .batch_scoring import CRITICIDADE_SEM_DADO
from .offer_bandit import PROFILES
from .risk_scorer import REGRA_DE_RISCO, identidade_do_modelo, mrr_utilizavel
from . import voluntary_agent as va

CRITERIO_INCLUSAO = ("critico", "alto")

# Teto de clientes por chamada. Acima disso a resposta deixa de ser "alguns
# segundos" (ver o teste de tempo em tests/test_disparo_lote.py) e a
# requisição é recusada com 413 — quem tem mais, pagina com `limite`.
LIMITE_LOTE = 5000

# Quantos clientes podem ter o texto GERADO pela Claude API numa chamada. Cada
# um custa (ofertas x idiomas) chamadas de LLM -- hoje 3 x 2 = 6 --, entao isto
# e um caminho para a tela pedir a mensagem de um cliente escolhido a dedo, nao
# para o lote. O lote continua usando modelo, que e o que o mantem em segundos.
LIMITE_GERACAO = 3

EVENTO_LOTE = "Disparo em lote"

ORIGEM_CORPO = "corpo"
ORIGEM_BASE = "base_importada"


class LoteGrandeDemais(ValueError):
    def __init__(self, recebidos: int):
        self.recebidos = recebidos
        super().__init__(f"{recebidos} clientes no lote; o máximo por chamada é {LIMITE_LOTE}")


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _user_id(customer_id_externo: str) -> str:
    """A mesma convenção do SDK (`user:<id>`), para o `/insights` juntar o
    ciclo registrado aqui com a linha da base sem criar um cliente novo."""
    return f"user:{customer_id_externo}"


# ── Entrada ──────────────────────────────────────────────────────────────

def _normalizar(bruto: dict) -> tuple[dict | None, str | None]:
    """Uma linha do corpo -> (cliente, None) ou (None, motivo do descarte)."""
    if not isinstance(bruto, dict):
        return None, "linha não é um objeto"
    cid = bruto.get("customer_id_externo")
    if not isinstance(cid, str) or not cid.strip() or any(ch.isspace() for ch in cid.strip()):
        return None, "customer_id_externo vazio ou com espaços"
    perfil = bruto.get("billing_profile")
    perfil_ok = next((p for p in PROFILES if isinstance(perfil, str) and p.lower() == perfil.strip().lower()), None)
    if perfil_ok is None:
        return None, f"billing_profile {perfil!r} não é um dos perfis aceitos ({', '.join(PROFILES)})"
    mrr = mrr_utilizavel(bruto.get("mrr"))
    if mrr is None:
        return None, f"mrr {bruto.get('mrr')!r} não é um número válido"

    def _num_ou_none(v):
        if v is None or isinstance(v, bool):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f >= 0 and f == f else None

    return {
        "customer_id_externo": cid.strip(),
        "mrr": mrr,
        "billing_profile": perfil_ok,
        "days_since_last": _num_ou_none(bruto.get("days_since_last")),
        "features_used_30d": _num_ou_none(bruto.get("features_used_30d")),
        "email": bruto.get("email") if isinstance(bruto.get("email"), str) else None,
        "phone": bruto.get("phone"),
        "on_site_now": bool(bruto.get("on_site_now", False)),
    }, None


def preparar(clientes: list[dict] | None, tenant_id: str) -> tuple[list[dict], list[dict], str]:
    """Devolve (linhas pontuadas, descartes de cadastro, origem).

    Com `clientes` no corpo, a régua é a da própria lista (`regua_da_base`),
    exatamente como o `/insights` faz com a base importada. Sem corpo, a
    lista é o ranking unificado do tenant — a mesma que o painel mostra.
    """
    if clientes:
        if len(clientes) > LIMITE_LOTE:
            raise LoteGrandeDemais(len(clientes))
        validos, descartes = [], []
        for i, bruto in enumerate(clientes, start=1):
            cliente, motivo = _normalizar(bruto)
            if cliente is None:
                cid = bruto.get("customer_id_externo") if isinstance(bruto, dict) else None
                descartes.append({"customer_id_externo": cid if isinstance(cid, str) else f"linha {i}",
                                  "motivo": "cadastro_invalido", "detalhe": motivo})
            else:
                validos.append(cliente)
        regua = batch_scoring.regua_da_base(validos)
        linhas = []
        for c in validos:
            pontuada = batch_scoring.pontuar_cliente(c, regua)
            linhas.append({**pontuada, "phone": c["phone"], "on_site_now": c["on_site_now"]})
        return batch_scoring.ordenar(linhas), descartes, ORIGEM_CORPO

    ranking = insights_unificados.clientes_em_risco(tenant_id)
    if len(ranking) > LIMITE_LOTE:
        raise LoteGrandeDemais(len(ranking))
    return ranking, [], ORIGEM_BASE


# ── Um cliente ───────────────────────────────────────────────────────────

def motivo_para_pular(linha: dict, com_ciclo_aberto: set) -> tuple[str, str] | None:
    """(motivo, detalhe) se o cliente NÃO deve ser contatado; None se deve."""
    crit = linha.get("criticality")
    if crit == CRITICIDADE_SEM_DADO or linha.get("risk_score") is None:
        return ("dado_insuficiente",
                "sem dado de atividade (days_since_last e features_used_30d ausentes): "
                "impossível avaliar risco — cliente não contatado")
    if crit not in CRITERIO_INCLUSAO:
        return ("abaixo_do_criterio",
                f"criticidade '{crit}': fora do critério de inclusão ({' ou '.join(CRITERIO_INCLUSAO)})")
    if _user_id(linha["customer_id_externo"]) in com_ciclo_aberto:
        return ("ciclo_aberto",
                "já existe ciclo de retenção sem desfecho para este cliente: não contatar duas vezes")
    return None


async def tratar(linha: dict, tenant_id: str, gerar_texto: bool = False) -> tuple[dict, dict]:
    """Candidatas, escolha e canal para UM cliente que precisa.

    `gerar_texto` troca o modelo pronto por texto escrito pela Claude API para
    ESTE cliente, uma redação por oferta e por idioma (ver `gerar_textos`).
    Falha de API cai no modelo, e a candidata diz de onde o texto veio.

    Devolve (linha do relatório, estado a registrar). O registro no
    `retention_log` é feito pelo lote inteiro de uma vez, em `disparar` —
    ver `retention_log.registrar_ciclos` para o porquê.
    """
    cid = linha["customer_id_externo"]
    mrr = mrr_utilizavel(linha.get("mrr"))
    perfil = linha.get("billing_profile") if linha.get("billing_profile") in PROFILES else "CLT"
    risco = float(linha["risk_score"])
    criticidade = linha["criticality"]

    props = {"days_since_last": linha.get("days_since_last"),
             "features_used_30d": linha.get("features_used_30d"),
             "mrr": mrr, "billing_profile": perfil,
             "on_site_now": bool(linha.get("on_site_now", False))}
    if linha.get("phone"):
        props["phone"] = linha["phone"]

    rodada = va._bandit.classificar_ofertas(tenant_id, perfil, risco, mrr=mrr)
    oferta = rodada[0]["offer"]

    # Trilha do Art. 20: o risco desta linha veio de `batch_scoring` (régua
    # da base ou global, ou modelo treinado se ativo); a oferta, do bandit.
    # O canal entra sozinho, por `va.choose_channel`. Montadas aqui, gravadas
    # pelo lote inteiro em `disparar`.
    modelo, versao = identidade_do_modelo()
    saida_risco = {"risk_score": round(risco, 4), "criticality": criticidade}
    if modelo == retention_log.MODELO_REGRA:
        saida_risco["regra"] = (f"batch_scoring.{linha.get('origem_da_regua')}"
                                if linha.get("origem_da_regua") else REGRA_DE_RISCO)
    decisao_risco = retention_log.decisao(
        tenant_id, _user_id(cid), retention_log.DOMINIO_VOLUNTARIO, retention_log.TIPO_RISCO,
        modelo, modelo_versao=versao,
        entradas={"days_since_last": retention_log._num(props["days_since_last"]),
                  "features_used_30d": retention_log._num(props["features_used_30d"]),
                  "mrr": retention_log._num(mrr), "billing_profile": perfil},
        saida=saida_risco)
    decisao_oferta = va.decisao_de_oferta(tenant_id, _user_id(cid), perfil, risco, mrr, rodada)

    estado = {
        "tenant_id": tenant_id, "user_id": _user_id(cid), "event": EVENTO_LOTE,
        "props": props, "risk_score": risco, "profile": perfil,
        "criticality": criticidade, "is_critical": criticidade == "critico",
        "offer_type": oferta, "ofertas_consideradas": rodada[:va.N_CANDIDATAS],
        "channel": None, "on_site_now": props["on_site_now"],
        "prior_channel_success": None, "message": None,
        "offer_sent": False, "accepted": None, "retained": False,
        "decisoes": [decisao_risco, decisao_oferta],
    }
    estado = await va.choose_channel(estado)

    label = va.OFFER_LABELS.get(oferta, "uma oferta especial")
    if gerar_texto:
        textos = await va.gerar_textos(
            estado, [l["offer"] for l in estado["ofertas_consideradas"]], criticidade)
        em_pt = (textos.get(oferta) or {}).get("pt") or {}
        texto = em_pt.get("texto") or va._fallback_de_retencao(criticidade, label)
        candidatas = va.montar_candidatas(
            estado, texto, origem_texto_vencedora=em_pt.get("origem") or "template",
            textos=textos)
    else:
        texto = va._fallback_de_retencao(criticidade, label)
        candidatas = va.montar_candidatas(estado, texto, origem_texto_vencedora="template")
    estado = {**estado, "message": texto, "candidatas": candidatas, "offer_sent": True}

    return {
        "customer_id_externo": cid,
        "criticality": criticidade,
        "risk_score": risco,
        "mrr": mrr,
        "billing_profile": perfil,
        "offer_type": oferta,
        "offer_label": label,
        "channel": estado["channel"],
        "canais_considerados": estado["canais_considerados"],
        "mensagem": texto,
        "candidatas": candidatas,
        "envio": {"simulado": True, "registrado": False, "ciclo_id": None},
    }, estado


# ── O lote ───────────────────────────────────────────────────────────────

async def disparar(clientes: list[dict] | None, tenant_id: str, limite: int | None = None,
                   somente: list[str] | None = None, gerar_texto: bool = False) -> dict:
    """Processa o conjunto e devolve o relatório por cliente e o resumo.

    `limite` corta a lista DEPOIS da ordenação por risco: "trate os 50
    piores". Os cortados não são "pulados" — não foram avaliados — e entram
    só na contagem `nao_avaliados`.

    `somente` trata UM subconjunto sem mudar a régua. A diferença para mandar
    esses clientes em `clientes` é o que decide o risco de cada um: com a
    lista no corpo, `preparar` calcula a régua em cima da própria lista, e um
    cliente sozinho não sustenta percentis — a mesma linha que é "critico" na
    base inteira sai "padrao" avaliada isolada, e aí é descartada por estar
    fora do critério de inclusão. Com `somente`, a régua, o risco e a
    criticidade continuam vindo da base do tenant, e o filtro entra DEPOIS:
    só os clientes pedidos passam por `tratar`, então só eles recebem
    mensagem e só eles têm ciclo registrado. Ninguém mais é contatado.

    É o que o painel usa para mostrar a decisão de um cliente sem disparar a
    base inteira por baixo.

    `gerar_texto` só vale para até `LIMITE_GERACAO` clientes. Acima disso o
    pedido não falha: cai no modelo pronto, que é o comportamento do lote. O
    teto é do código e não de quem chama, porque o custo é em chamadas de LLM
    e um lote grande com geração ligada por engano sairia caro e lento sem
    ninguém perceber antes do fim.
    """
    linhas, descartes, origem = preparar(clientes, tenant_id)
    if somente is not None:
        pedidos = {str(c) for c in somente}
        linhas = [l for l in linhas if l.get("customer_id_externo") in pedidos]
        descartes = [d for d in descartes if d.get("customer_id_externo") in pedidos]
    total = len(linhas) + len(descartes)

    nao_avaliados = 0
    if limite is not None and len(linhas) > limite:
        nao_avaliados = len(linhas) - limite
        linhas = linhas[:limite]

    com_ciclo_aberto = {
        c["user_id"] for c in retention_log.ultimo_ciclo_por_cliente(tenant_id)
        if c.get("offer_type") and c.get("accepted") is None
    }

    gerar = gerar_texto and len(linhas) <= LIMITE_GERACAO

    tratados, estados, pulados = [], [], list(descartes)
    for linha in linhas:
        motivo = motivo_para_pular(linha, com_ciclo_aberto)
        if motivo:
            pulados.append({"customer_id_externo": linha["customer_id_externo"],
                            "criticality": linha.get("criticality"),
                            "motivo": motivo[0], "detalhe": motivo[1]})
            continue
        relatorio, estado = await tratar(linha, tenant_id, gerar_texto=gerar)
        tratados.append(relatorio)
        estados.append(estado)

    # Uma transação para o lote inteiro — é o que mantém a resposta em
    # segundos (ver `registrar_ciclos`).
    for relatorio, ciclo_id in zip(tratados, retention_log.registrar_ciclos(estados)):
        relatorio["envio"] = {"simulado": True, "registrado": ciclo_id is not None,
                              "ciclo_id": ciclo_id}
    # A trilha do Art. 20 do lote inteiro — risco, oferta e canal de cada
    # cliente tratado — na MESMA disciplina: uma transação, best effort.
    retention_log.registrar_decisoes(
        [d for e in estados for d in (e.get("decisoes") or [])])

    por_motivo: dict[str, int] = {}
    for p in pulados:
        por_motivo[p["motivo"]] = por_motivo.get(p["motivo"], 0) + 1

    return {
        "tenant_id": tenant_id,
        "origem": origem,
        "criterio": f"criticidade {' ou '.join(CRITERIO_INCLUSAO)}",
        "simulado": True,
        "aviso": ("Envio simulado: as mensagens foram decididas e registradas, "
                  "mas nenhum canal externo foi acionado nesta fase."),
        "resumo": {
            "recebidos": total,
            "processados": len(tratados),
            "pulados": len(pulados),
            "nao_avaliados": nao_avaliados,
            "por_motivo": por_motivo,
            "mrr_envolvido": round(sum(t["mrr"] or 0.0 for t in tratados), 2),
            "por_canal": _contar(tratados, "channel"),
            "por_oferta": _contar(tratados, "offer_type"),
        },
        "clientes": tratados,
        "pulados": pulados,
        "gerado_em": _agora(),
    }


def _contar(linhas: list[dict], campo: str) -> dict:
    contagem: dict[str, int] = {}
    for l in linhas:
        chave = l.get(campo) or "--"
        contagem[chave] = contagem.get(chave, 0) + 1
    return contagem

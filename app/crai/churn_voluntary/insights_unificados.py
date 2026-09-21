"""crai/churn_voluntary/insights_unificados.py — uma lista só, das duas origens.

Uma empresa pode ter clientes vindos do upload em lote (Sprints 2-3), do SDK
comportamental (eventos em `retention_cycles.db`), ou dos dois — subiu a base
para começar e depois integrou o SDK. O `/insights` não escolhe uma origem;
ele junta, e este módulo é a junção.

REGRA DE FUSÃO, por `customer_id`: quando o mesmo cliente aparece nas duas
origens, vence a linha mais recente — `registrado_em` do ciclo do SDK contra
`importado_em` da base. Na prática quase sempre é o SDK, porque ele se
atualiza a cada evento e o upload é uma foto parada no tempo do cadastro.
Empate exato (mesmo segundo) também vai para o SDK, pelo mesmo motivo. A
saída carrega `origem: "upload" | "sdk"` para o frontend mostrar de onde
veio, e `atualizado_em` para mostrar quando.

IDENTIDADE. O SDK grava o `user_id` QUALIFICADO (`user:<id>` / `anon:<id>`,
ver `_identidade_voluntaria` em `api/app.py`); a planilha traz o id cru. O
casamento tira o prefixo `user:`. Um visitante anônimo (`anon:`) não tem
como estar numa planilha de clientes e fica na lista com o id qualificado
mesmo — é um cliente em risco também, só que ainda sem nome.

O RISCO DO SDK É O QUE O CICLO GRAVOU, não recalculado: foi a decisão que o
sistema tomou naquele evento (com o modelo/regra vigente na hora), e
recalcular aqui produziria um número que nenhum ciclo produziu. A
explicação, essa é gerada agora, com as mesmas features e o evento.
"""

import logging

from . import batch_scoring, retention_log

logger = logging.getLogger(__name__)

ORIGEM_UPLOAD = "upload"
ORIGEM_SDK = "sdk"

# Mesmo literal de `api/app.py::PREFIXO_IDENTIFICADO`. Copiado e não importado:
# `api/app.py` importa este pacote, e o sentido contrário fecharia um ciclo.
# Há teste que confere a igualdade.
PREFIXO_IDENTIFICADO = "user:"


def _id_cru(user_id: str) -> str:
    if isinstance(user_id, str) and user_id.startswith(PREFIXO_IDENTIFICADO):
        return user_id[len(PREFIXO_IDENTIFICADO):]
    return user_id


def _linha_do_upload(l: dict) -> dict:
    return {**l, "origem": ORIGEM_UPLOAD, "atualizado_em": l.get("importado_em"),
            "evento": None}


def _linha_do_sdk(ciclo: dict, idioma: str = "pt") -> dict:
    """Um ciclo do `retention_log` no MESMO formato do ranking do upload."""
    risk = ciclo.get("risk_score")
    crit = ciclo.get("criticality") or "padrao"
    cliente = {
        "customer_id_externo": _id_cru(ciclo.get("user_id", "")),
        "mrr": ciclo.get("mrr"),
        "billing_profile": ciclo.get("billing_profile"),
        "days_since_last": ciclo.get("days_since_last"),
        "features_used_30d": ciclo.get("features_used_30d"),
    }
    explicacao = batch_scoring.explicar(cliente, risk, crit, None, idioma)
    evento = ciclo.get("event")
    if evento:
        explicacao = f"último evento: {evento}; {explicacao}"
    return {
        **cliente,
        "risk_score": risk,
        "criticality": crit,
        "explicacao": explicacao,
        "email": None,
        "importado_em": None,
        # O evento pontual não tem base para se comparar: é sempre a régua
        # global. Guardar a distribuição para o SDK é tarefa de outra sessão.
        "origem_da_regua": batch_scoring.REGUA_GLOBAL,
        "origem": ORIGEM_SDK,
        "atualizado_em": ciclo.get("registrado_em"),
        "evento": evento,
    }


def _mais_recente(a: dict, b: dict) -> dict:
    """Entre duas linhas do mesmo cliente, a mais recente; empate → SDK."""
    ta, tb = a.get("atualizado_em") or "", b.get("atualizado_em") or ""
    if ta != tb:
        return a if ta > tb else b
    return a if a["origem"] == ORIGEM_SDK else b


def clientes_em_risco(tenant_id: str, idioma: str = "pt") -> list[dict]:
    """Upload ∪ SDK deste tenant, um por cliente, ordenado por risco.

    Lê as duas origens SÓ para o tenant pedido; nenhuma das duas leituras tem
    forma sem filtro. Ordenação é a de `batch_scoring.ordenar` — risco
    decrescente, `dado_insuficiente` por último.
    """
    por_cliente: dict = {}

    for l in batch_scoring.pontuar_base(tenant_id, idioma):
        por_cliente[l["customer_id_externo"]] = _linha_do_upload(l)

    for ciclo in retention_log.ultimo_ciclo_por_cliente(tenant_id):
        linha = _linha_do_sdk(ciclo, idioma)
        cid = linha["customer_id_externo"]
        if cid in por_cliente:
            vencedora = _mais_recente(por_cliente[cid], linha)
            # O e-mail só existe no upload; se o SDK vence, o e-mail do
            # cadastro continua valendo — é o mesmo cliente.
            if vencedora is linha and por_cliente[cid].get("email"):
                vencedora = {**linha, "email": por_cliente[cid]["email"]}
            por_cliente[cid] = vencedora
        else:
            por_cliente[cid] = linha

    ranking = batch_scoring.ordenar(list(por_cliente.values()))
    logger.info("[INSIGHTS] tenant=%s clientes=%d upload=%d sdk=%d", tenant_id,
                len(ranking),
                sum(1 for l in ranking if l["origem"] == ORIGEM_UPLOAD),
                sum(1 for l in ranking if l["origem"] == ORIGEM_SDK))
    return ranking


def filtrar(ranking: list[dict], limite=None, criticidade_minima=None) -> list[dict]:
    """Os filtros do `/insights`: criticidade mínima, depois limite.

    `criticidade_minima="alto"` mantém alto e crítico; `"critico"` só crítico.
    `dado_insuficiente` nunca passa por um filtro de criticidade — não é uma
    criticidade, é a ausência dela.
    """
    linhas = ranking
    if criticidade_minima:
        piso = batch_scoring.ORDEM_CRITICIDADE[criticidade_minima]
        linhas = [l for l in linhas
                  if batch_scoring.ORDEM_CRITICIDADE.get(l["criticality"], 0) >= piso
                  and l["criticality"] != batch_scoring.CRITICIDADE_SEM_DADO]
    if limite is not None:
        linhas = linhas[:limite]
    return linhas

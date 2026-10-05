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
import threading

from . import batch_scoring, clientes_importados, retention_log
from . import risk_scorer as _rs

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


# O evento que o disparo em lote grava no ciclo (`disparo_lote.EVENTO_LOTE`; importar
# seria circular, e há teste que confere a igualdade).
EVENTO_DO_LOTE = "Disparo em lote"


def _decisao_deste_ciclo(ciclo: dict, decisao: dict | None) -> dict | None:
    """A decisão de risco da trilha, se ela é a DESTE ciclo: o mesmo evento e o
    mesmo risco. A trilha é gravada por melhor esforço e o ciclo não aponta
    para ela; se não bater, a linha fica sem a informação em vez de usar a de
    outro evento."""
    if not isinstance(decisao, dict):
        return None
    entradas, saida = decisao.get("entradas") or {}, decisao.get("saida") or {}
    try:
        mesmo_risco = round(float(ciclo.get("risk_score")), 4) == round(float(saida.get("risk_score")), 4)
    except (TypeError, ValueError):
        return None
    # A decisão de risco do disparo em lote não leva `event` nas entradas.
    mesmo_evento = entradas.get("event", EVENTO_DO_LOTE) == ciclo.get("event")
    return decisao if mesmo_risco and mesmo_evento else None


def _explicar_o_sdk(cliente: dict, risk, crit: str, decisao: dict | None, idioma: str) -> tuple:
    """(a frase, quem decidiu, a posição na base) da linha de quem veio por
    evento (Rodada 4). QUEM DECIDIU DE VERDADE sai da trilha:

      - o modelo decidiu: a frase é a da posição pelo modelo, a mesma do ranking;
      - a régua decidiu: a frase de sempre (nela, grave com risco abaixo de 0,90
        é mesmo pelo valor da conta);
      - sem a decisão na trilha: a frase da régua, mas SEM afirmar "crítico pelo
        valor da conta" quando a mensalidade não chega ao limiar de valor.

    Em qualquer caso, se a oferta saiu por intenção explícita, a frase diz."""
    saida = (decisao or {}).get("saida") or {}
    if decisao is not None and decisao.get("modelo") not in (None, retention_log.MODELO_REGRA):
        posicao = (saida.get("posicao") or {}).get("na_base")
        faixas = batch_scoring.faixas_de_posicao()
        promovido = (crit == "critico" and posicao is not None
                     and posicao < 1 - faixas[0] / 100)
        frase = batch_scoring._explicar_pelo_modelo(cliente, posicao, crit, faixas, idioma,
                                                    promovido=promovido)
        decidido = batch_scoring.DECIDIDO_MODELO
    else:
        posicao = None
        frase = batch_scoring.explicar(cliente, risk, crit, None, idioma)
        decidido = batch_scoring.DECIDIDO_REGRA if decisao is not None else None
        pelo_valor = batch_scoring._f("critico_valor", idioma)
        mrr = _rs.mrr_utilizavel(cliente.get("mrr"))
        if (decisao is None and frase.endswith(pelo_valor)
                and (mrr is None or mrr < _rs.limiar_de_alto_valor())):
            frase = frase[:-len(pelo_valor)]
    if saida.get("regra_de_intervencao") == "intencao_explicita":
        frase += batch_scoring._f("intencao_explicita", idioma)
    return frase, decidido, posicao


def _linha_do_sdk(ciclo: dict, idioma: str = "pt", decisao: dict | None = None) -> dict:
    """Um ciclo do `retention_log` no MESMO formato do ranking do upload.
    `decisao` é a última decisão de risco da trilha para este cliente (ou None)."""
    risk = ciclo.get("risk_score")
    crit = ciclo.get("criticality") or "padrao"
    cliente = {
        "customer_id_externo": _id_cru(ciclo.get("user_id", "")),
        "mrr": ciclo.get("mrr"),
        "billing_profile": ciclo.get("billing_profile"),
        "days_since_last": ciclo.get("days_since_last"),
        "features_used_30d": ciclo.get("features_used_30d"),
    }
    explicacao, decidido, posicao = _explicar_o_sdk(
        cliente, risk, crit, _decisao_deste_ciclo(ciclo, decisao), idioma)
    evento = ciclo.get("event")
    if evento:
        explicacao = f"último evento: {evento}; {explicacao}"
    return {
        **cliente,
        "risk_score": risk,
        "criticality": crit,
        "explicacao": explicacao,
        # Quem decidiu o risco deste evento, lido da trilha (None se ela não tem
        # a decisão deste ciclo), e a posição que o modelo deu.
        "risco_decidido_por": decidido,
        "posicao_na_base": posicao,
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

    decisoes = retention_log.ultimas_decisoes_de_risco(tenant_id)
    for ciclo in retention_log.ultimo_ciclo_por_cliente(tenant_id):
        linha = _linha_do_sdk(ciclo, idioma, decisoes.get(ciclo.get("user_id")))
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


# ── O cache da régua (Rodada 3, Fase 1) ───────────────────────────────────
#
# `clientes_em_risco` recalcula a base INTEIRA a cada chamada. Com o dashboard
# consultando de tempo em tempo, isso pesa: a página do voluntário pede a lista,
# os totais e a comparação a cada minuto. `painel_de_risco` guarda o resultado
# por tenant e só recalcula quando a MARCA muda. A marca junta:
#
#   - a base importada (`clientes_importados.marca_da_base`): toda escrita deste
#     processo, e a foto da tabela para a escrita de outro processo;
#   - os ciclos do SDK (`retention_log.marca_dos_ciclos`): ciclo novo ou desfecho;
#   - quem decide o risco agora (modelo ativo, contrato, arquivo do modelo e o
#     limiar de alto valor) e o idioma.
#
# Qualquer escrita na base do tenant muda a marca, e a leitura seguinte
# recalcula. Sem conseguir ler a marca (banco fora), não há cache: calcula e
# devolve. O `/insights` antigo NÃO passa por aqui: continua recalculando a cada
# chamada, como o contrato dele diz.
#
# O QUE É GUARDADO é o ranking completo do tenant (com o e-mail da base, como o
# `/insights` devolve). Fica só na memória do processo, um resultado por tenant,
# e some no reinício. Quem lê não altera as listas devolvidas.

_cache_lock = threading.Lock()
_cache: dict = {}


def esquecer_cache() -> None:
    """Zera o cache de todos os tenants (testes, e troca de modelo a quente)."""
    with _cache_lock:
        _cache.clear()


def marca_do_tenant(tenant_id: str, idioma: str = "pt"):
    """A marca que invalida o cache, ou None se alguma leitura falhou."""
    ciclos = retention_log.marca_dos_ciclos(tenant_id)
    if ciclos is None:
        return None
    return (clientes_importados.marca_da_base(tenant_id), ciclos,
            str(retention_log.caminho_do_banco()), idioma,
            batch_scoring.posicao_pelo_modelo_ativa(), _rs.modelo_ativo(),
            str(_rs.MODELO_PATH), _rs.limiar_de_alto_valor())


def em_cache(tenant_id: str, nome: str, calcular, idioma: str = "pt"):
    """`calcular()` guardado por (tenant, nome) enquanto a marca do tenant não
    mudar. É por aqui que as rotas do dashboard reaproveitam um cálculo que
    depende só da base."""
    marca = marca_do_tenant(tenant_id, idioma)
    if marca is None:
        return calcular()
    chave = (tenant_id, nome)
    with _cache_lock:
        guardado = _cache.get(chave)
    if guardado is not None and guardado[0] == marca:
        return guardado[1]
    valor = calcular()
    with _cache_lock:
        _cache[chave] = (marca, valor)
    return valor


def painel_de_risco(tenant_id: str, idioma: str = "pt") -> list[dict]:
    """O mesmo ranking de `clientes_em_risco`, do cache enquanto a base do
    tenant não mudar. NÃO altere a lista devolvida."""
    return em_cache(tenant_id, f"ranking:{idioma}",
                    lambda: clientes_em_risco(tenant_id, idioma), idioma)

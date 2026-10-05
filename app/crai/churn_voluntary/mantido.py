"""crai/churn_voluntary/mantido.py — o dinheiro MANTIDO do churn voluntário (Rodada 3).

O QUE É. Quando um cliente em risco aceita a oferta de retenção, a empresa
manteve uma receita que ia perder. Este módulo diz quanto, em reais, e quanto
disso é a taxa da CRAI. É o par, no voluntário, do que `ciclo_cobranca` faz com
a cobrança recuperada no involuntário.

AS QUATRO REGRAS (decididas pelo Crai):

    V1  valor mantido = `MESES_DE_MRR_MANTIDOS` mês(es) do MRR do cliente que
        aceitou a oferta, menos o desconto concedido, contado no DIA DO ACEITE,
        líquido da fee da CRAI;
    V2  se o cliente cancelar dentro de `prazo_estorno_dias` (a MESMA chave do
        estorno do involuntário, padrão 30), o mantido e a fee daquela retenção
        são estornados, com as regras E1 a E7 do Bloco 5;
    V3  o cancelamento que estorna é o que está na base importada
        (`cancelado_em`): chega pelo `DELETE /clientes/{id}`;
    V4  o mês fechado não é reescrito: o estorno aparece no mês em que o
        cancelamento aconteceu.

O NÚMERO DE MESES É UMA CONSTANTE SÓ, `MESES_DE_MRR_MANTIDOS`. O plano de
negócio fala em 6 meses com estorno em 90 dias, e isso ainda vai ser acertado:
a troca é esta linha (e o `prazo_estorno_dias` da configuração da empresa).

AS REGRAS E1 A E7, aqui:

    E1  o prazo é o da empresa, lido no momento em que o cancelamento é visto;
    E2  cancelamento no prazo: a fee inteira volta e o líquido sai das métricas;
    E3  não se aplica: cancelamento não é parcial;
    E4  cancelamento depois do prazo: fica registrado, sem mudar valor nenhum;
    E5  a retenção entra no dia do aceite, o estorno no dia do cancelamento;
    E6  nenhuma rota declara estorno: ele só nasce do cancelamento na base;
    E7  uma retenção é estornada uma vez só, e nunca além do que valeu.

DE ONDE VEM. O aceite mora em `ciclos_retencao` (`retention_log`), que é o
dataset de treino e não guarda dinheiro. O cancelamento mora na base importada.
`sincronizar` lê os dois e MATERIALIZA uma linha por retenção aceita na tabela
`retencoes_mantidas`, com o MRR, o desconto e a fee daquele momento: depois de
gravada, a linha não muda quando a fee da instalação ou o MRR do cliente mudam.
Só a marca do cancelamento é acrescentada, uma vez.

`sincronizar` é idempotente e é chamada pelas rotas que leem o mantido e depois
do cancelamento de um cliente. A data de cada coisa é a do fato (o aceite, o
cancelamento), não a da sincronização.

O ACEITE SORTEADO NÃO É DINHEIRO DE VERDADE. Com `CRAI_SIMULATE_OUTCOMES=1` o
desfecho é um sorteio (`origem_desfecho = "simulacao"`). Essas linhas entram
marcadas como `simulado` e só aparecem para quem pede os simulados.

SEM MRR, SEM VALOR. Se o ciclo não gravou o MRR e a base não tem o cliente, a
retenção não é materializada: o módulo não inventa uma mensalidade. Ela aparece
na contagem `sem_valor` da sincronização e é tentada de novo na próxima.

ONDE MORA. Tabela `retencoes_mantidas`, no arquivo do ciclo de cobrança
(`recovery_cycles.db`, env `CRAI_RECOVERY_DB`), ao lado do estorno do
involuntário. Datas em hora local sem fuso, como as outras desse arquivo. Não
guarda nome nem contato: só o identificador que a própria empresa usa.

Este módulo NÃO importa de `api/app.py`.
"""

import logging
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from .. import config
from ..api import datas
from ..dunning import ciclo_cobranca, configuracao
from . import clientes_importados, retention_log
from .insights_unificados import _id_cru

logger = logging.getLogger(__name__)

# V1: quantos meses de MRR contam como "mantido" por retenção aceita. É o único
# lugar em que este número aparece.
MESES_DE_MRR_MANTIDOS = 1

# Por quantos meses o desconto percentual vale (o mesmo de `offer_bandit.offer_cost`).
MESES_DO_DESCONTO = 3

ORIGEM_SORTEIO = "simulacao"

_DDL = """
CREATE TABLE IF NOT EXISTS retencoes_mantidas (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id             TEXT    NOT NULL,
    ciclo_retencao_id     INTEGER NOT NULL,
    cliente_id            TEXT    NOT NULL,
    offer_type            TEXT    NOT NULL,
    channel               TEXT,
    aceito_em             TEXT    NOT NULL,
    mrr                   REAL    NOT NULL,
    meses                 INTEGER NOT NULL,
    desconto              REAL    NOT NULL,
    valor_base            REAL    NOT NULL,
    fee                   REAL    NOT NULL,
    simulado              INTEGER NOT NULL DEFAULT 0,
    cancelamento_em       TEXT,
    cancelamento_no_prazo INTEGER,
    valor_estornado       REAL,
    fee_estornada         REAL,
    criado_em             TEXT    NOT NULL,
    fee_fora_do_piloto    REAL,
    UNIQUE (tenant_id, ciclo_retencao_id)
);
CREATE INDEX IF NOT EXISTS idx_mantidas_aceite ON retencoes_mantidas (tenant_id, aceito_em);
CREATE INDEX IF NOT EXISTS idx_mantidas_cancelamento
    ON retencoes_mantidas (tenant_id, cancelamento_em);
"""


def _conectar():
    conn = ciclo_cobranca._conectar()
    conn.executescript(_DDL)
    # Rodada 4, Fase 3 (modo piloto): a coluna nova num banco que já existia.
    # `NULL` em toda linha que não é de piloto.
    if "fee_fora_do_piloto" not in {l[1] for l in conn.execute(
            "PRAGMA table_info(retencoes_mantidas)")}:
        try:
            conn.execute("ALTER TABLE retencoes_mantidas ADD COLUMN fee_fora_do_piloto REAL")
        except sqlite3.OperationalError:
            # Outro processo acrescentou a coluna entre a leitura e o ALTER.
            pass
    return conn


def _texto(momento: datetime) -> str:
    return momento.isoformat(timespec="seconds")


# ── A conta (V1) ──────────────────────────────────────────────────────────

def desconto_concedido(offer_type: str, mrr: float, meses: Optional[int] = None) -> float:
    """Quanto a empresa abriu mão, em reais, dentro dos `meses` contados
    (`MESES_DE_MRR_MANTIDOS`, se não vier outro).

    Desconto de 10% ou 20%: o percentual sobre o MRR, pelos meses em que ele
    vale dentro da janela contada. Pausa de 1 mês: um mês inteiro sem cobrança.
    Pix ou boleto com facilidade: a mensalidade não muda."""
    meses = MESES_DE_MRR_MANTIDOS if meses is None else meses
    if offer_type == "desconto_10":
        return round(0.10 * mrr * min(meses, MESES_DO_DESCONTO), 2)
    if offer_type == "desconto_20":
        return round(0.20 * mrr * min(meses, MESES_DO_DESCONTO), 2)
    if offer_type == "pausa_1_mes":
        return round(mrr * min(meses, 1), 2)
    return 0.0


def valores_da_retencao(offer_type: str, mrr: float, tenant_id: Optional[str] = None) -> dict:
    """V1 para UMA retenção: base (meses de MRR menos o desconto), fee e líquido.

    Com o `tenant_id` de uma empresa em PILOTO (Rodada 4, Fase 3), a fee
    cobrada é zero, o líquido é a base inteira, e `fee_fora_do_piloto` traz a
    fee que seria cobrada. Fora do piloto, `fee_fora_do_piloto` é `None`."""
    desconto = desconto_concedido(offer_type, mrr)
    base = round(max(0.0, MESES_DE_MRR_MANTIDOS * mrr - desconto), 2)
    fee, fora = config.taxa_cobrada_e_fora_do_piloto(
        base * config.success_fee_voluntario_pct(), tenant_id)
    return {"meses": MESES_DE_MRR_MANTIDOS, "desconto": desconto, "valor_base": base,
            "fee": fee, "liquido": round(base - fee, 2), "fee_fora_do_piloto": fora}


# ── A sincronização ───────────────────────────────────────────────────────

def _clientes_da_base(tenant_id: str, ids: list) -> dict:
    try:
        return clientes_importados.obter_varios(tenant_id, ids)
    except clientes_importados.ConfiguracaoAusente:
        return {}


def sincronizar(tenant_id: str) -> dict:
    """Materializa as retenções aceitas que ainda não têm linha e aplica os
    cancelamentos ainda não vistos. Idempotente. Devolve o que fez:
    `{novas, sem_valor, estornadas, fora_do_prazo}`."""
    feito = {"novas": 0, "sem_valor": 0, "estornadas": 0, "fora_do_prazo": 0}
    aceitos = [c for c in retention_log.ciclos_com_oferta(tenant_id) if c.get("accepted") == 1]
    conn = _conectar()
    try:
        ja = {l["ciclo_retencao_id"] for l in conn.execute(
            "SELECT ciclo_retencao_id FROM retencoes_mantidas WHERE tenant_id = ?", (tenant_id,))}
        faltam = [c for c in aceitos if c["id"] not in ja]
        abertas = [dict(l) for l in conn.execute(
            "SELECT id, cliente_id, aceito_em, valor_base, fee FROM retencoes_mantidas "
            "WHERE tenant_id = ? AND cancelamento_em IS NULL", (tenant_id,))]
    finally:
        conn.close()
    if not faltam and not abertas:
        return feito

    base = _clientes_da_base(
        tenant_id, [_id_cru(c["user_id"]) for c in faltam] + [l["cliente_id"] for l in abertas])
    agora = _texto(datas.agora_local())

    novas = []
    for c in faltam:
        cliente_id = _id_cru(c["user_id"])
        mrr = c.get("mrr")
        if mrr is None and cliente_id in base:
            mrr = base[cliente_id].get("mrr")
        aceito_em = datas.para_local(c.get("desfecho_em"))
        if mrr is None or float(mrr) <= 0 or aceito_em is None:
            feito["sem_valor"] += 1
            continue
        # O piloto é o de AGORA, que é quando a linha nasce: por isso o aceite
        # chama esta sincronização na hora (ver `voluntary_agent`), e uma linha
        # já gravada nunca é recalculada (M5).
        v = valores_da_retencao(c["offer_type"], float(mrr), tenant_id)
        novas.append((tenant_id, c["id"], cliente_id, c["offer_type"], c.get("channel"),
                      _texto(aceito_em), round(float(mrr), 2), v["meses"], v["desconto"],
                      v["valor_base"], v["fee"],
                      1 if c.get("origem_desfecho") == ORIGEM_SORTEIO else 0, agora,
                      v["fee_fora_do_piloto"]))

    prazo = configuracao.ler(tenant_id)["prazo_estorno_dias"]
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        for linha in novas:
            cur = conn.execute(
                """INSERT OR IGNORE INTO retencoes_mantidas
                       (tenant_id, ciclo_retencao_id, cliente_id, offer_type, channel, aceito_em,
                        mrr, meses, desconto, valor_base, fee, simulado, criado_em,
                        fee_fora_do_piloto)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", linha)
            feito["novas"] += cur.rowcount
        candidatas = [dict(l) for l in conn.execute(
            "SELECT id, cliente_id, aceito_em, valor_base, fee FROM retencoes_mantidas "
            "WHERE tenant_id = ? AND cancelamento_em IS NULL", (tenant_id,))]
        for linha in candidatas:
            cliente = base.get(linha["cliente_id"])
            cancelado = datas.para_local((cliente or {}).get("cancelado_em"))
            aceito = datetime.fromisoformat(linha["aceito_em"])
            # Cancelamento anterior ao aceite não é desta retenção.
            if cancelado is None or cancelado < aceito:
                continue
            no_prazo = cancelado <= aceito + timedelta(days=prazo)
            atualizadas = conn.execute(
                """UPDATE retencoes_mantidas
                      SET cancelamento_em = ?, cancelamento_no_prazo = ?,
                          valor_estornado = ?, fee_estornada = ?
                    WHERE id = ? AND cancelamento_em IS NULL""",
                (_texto(cancelado), 1 if no_prazo else 0,
                 linha["valor_base"] if no_prazo else None,
                 linha["fee"] if no_prazo else None, linha["id"])).rowcount
            if atualizadas:
                feito["estornadas" if no_prazo else "fora_do_prazo"] += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if any(feito.values()):
        logger.info("[MANTIDO] tenant=%s novas=%d sem_valor=%d estornadas=%d fora_do_prazo=%d",
                    tenant_id, feito["novas"], feito["sem_valor"], feito["estornadas"],
                    feito["fora_do_prazo"])
    return feito


def sincronizar_sem_levantar(tenant_id: str) -> Optional[dict]:
    """`sincronizar` para quem não pode falhar por causa dela (a rota que
    cancela um cliente). A falha vai para o log em ERROR."""
    try:
        return sincronizar(tenant_id)
    except Exception as e:                        # noqa: BLE001 - best effort declarado
        logger.error("[MANTIDO] sincronização falhou (tenant=%s): %r", tenant_id, e)
        return None


# ── Leitura ───────────────────────────────────────────────────────────────

def _com_liquido(linha: dict) -> dict:
    linha["liquido"] = round(float(linha["valor_base"]) - float(linha["fee"]), 2)
    linha["simulado"] = bool(linha["simulado"])
    linha["estornada"] = bool(linha["cancelamento_no_prazo"])
    return linha


def mantidas_no_periodo(tenant_id: str, inicio: datetime, fim: datetime,
                        incluir_simulados: bool = False) -> list[dict]:
    """As retenções ACEITAS em [inicio, fim) deste tenant, pela data do aceite,
    com `liquido` (base menos a fee da época). INTERNO: as linhas trazem a fee."""
    filtro = "" if incluir_simulados else " AND simulado = 0"
    conn = _conectar()
    try:
        return [_com_liquido(dict(l)) for l in conn.execute(
            f"SELECT * FROM retencoes_mantidas WHERE tenant_id = ? AND aceito_em >= ? "
            f"AND aceito_em < ?{filtro} ORDER BY aceito_em, id",
            (tenant_id, _texto(inicio), _texto(fim)))]
    finally:
        conn.close()


def do_cliente(tenant_id: str, cliente_id: str) -> list[dict]:
    """As retenções aceitas DESTE cliente DESTE tenant, da mais antiga à mais
    nova. É a parte do valor mantido na exportação de um titular (Rodada 3,
    Fase 6). INTERNO: as linhas trazem a fee; quem exporta não a repassa."""
    conn = _conectar()
    try:
        return [dict(l) for l in conn.execute(
            "SELECT * FROM retencoes_mantidas WHERE tenant_id = ? AND cliente_id = ? "
            "ORDER BY aceito_em, id", (tenant_id, str(cliente_id)))]
    finally:
        conn.close()


def estornos_no_periodo(tenant_id: str, inicio: datetime, fim: datetime,
                        incluir_simulados: bool = False) -> list[dict]:
    """Os estornos NO PRAZO cujo cancelamento caiu em [inicio, fim). Cada um
    com `liquido_devolvido` (o que sai do mantido). É a parcela negativa de V4."""
    filtro = "" if incluir_simulados else " AND simulado = 0"
    conn = _conectar()
    try:
        linhas = [dict(l) for l in conn.execute(
            f"SELECT * FROM retencoes_mantidas WHERE tenant_id = ? AND cancelamento_no_prazo = 1 "
            f"AND cancelamento_em >= ? AND cancelamento_em < ?{filtro} "
            f"ORDER BY cancelamento_em, id", (tenant_id, _texto(inicio), _texto(fim)))]
    finally:
        conn.close()
    for l in linhas:
        l["simulado"] = bool(l["simulado"])
        l["liquido_devolvido"] = round(float(l["valor_estornado"] or 0.0)
                                       - float(l["fee_estornada"] or 0.0), 2)
    return linhas


def cancelamentos_fora_do_prazo(tenant_id: str, inicio: datetime, fim: datetime) -> list[dict]:
    """E4: os cancelamentos vistos DEPOIS do prazo, em [inicio, fim). Não mudam
    valor; existem para a atividade recente."""
    conn = _conectar()
    try:
        return [dict(l) for l in conn.execute(
            "SELECT * FROM retencoes_mantidas WHERE tenant_id = ? AND cancelamento_no_prazo = 0 "
            "AND cancelamento_em >= ? AND cancelamento_em < ? ORDER BY cancelamento_em, id",
            (tenant_id, _texto(inicio), _texto(fim)))]
    finally:
        conn.close()


def resumo(mantidas: list, estornos: list) -> dict:
    """Os números de um período: o líquido das retenções aceitas NELE menos o
    líquido estornado NELE (V4). Pode ficar negativo. As contagens contam o que
    aconteceu no período."""
    estornado = round(sum(e["liquido_devolvido"] for e in estornos), 2)
    return {"valor_liquido_mantido": round(sum(m["liquido"] for m in mantidas) - estornado, 2),
            "clientes_mantidos": len(mantidas),
            "estornos": {"quantidade": len(estornos), "valor_liquido_estornado": estornado}}

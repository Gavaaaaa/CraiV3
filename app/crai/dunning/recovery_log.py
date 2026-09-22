"""crai/dunning/recovery_log.py — o DATASET de treino do churn involuntário.

POR QUE ISTO EXISTE (Gaps 4, 5, 6 e 7 da auditoria). O involuntário decidia,
agia e não guardava nada do que tinha acontecido. As consequências eram três,
de naturezas diferentes:

  TREINO (Gap 4/5). O `FailureClassifier` é treinado em dados SINTÉTICOS
      (`ml/synthetic_data.py`). Para trocar por dados reais é preciso ter o par
      (features, recovered) de ciclos que aconteceram de verdade — e ele não
      existia em lugar nenhum. As features morriam no nó do grafo, o
      `MemorySaver` morre no restart, e rodar três meses em produção daria
      exatamente zero linha de treino. É o mesmo buraco que o
      `churn_voluntary/retention_log.py` fechou do outro lado, e este módulo é
      a paridade.

  MARGEM (Gap 6). Recuperar na 3ª tentativa custa 3× o PSP de recuperar na 1ª.
      O `[ROI]` imprimia o fee e o e-Profit previsto, nunca o custo REALIZADO.
      Sem ele não há margem por recuperação — e margem é o número que sustenta
      o modelo Outcome-as-a-Service.

  AGREGADO (Gap 7). MRR recuperado, taxa de recuperação, custo médio: nenhum
      deles era calculável, porque não havia de onde somar.

DUAS ESCRITAS POR CICLO, e a segunda pode demorar dias:

    registrar_ciclo(...)      no `update_roi_dashboard`, fim do grafo. Features
                              + decisão + custo até ali. `recovered` fica 0 e
                              `desfecho_em` NULL: o ciclo está aberto.
    registrar_recuperacao(...) quando a confirmação do PSP chega
                              (`_fechar_ciclo_recuperado`). Fecha a linha com
                              `recovered=1`, o custo final e o fee efetivo.

SQLITE, e não Parquet, pela mesma razão do log do voluntário: a segunda escrita
é um UPDATE numa linha gravada antes, possivelmente noutro processo e noutro
dia. Parquet é imutável por arquivo. `sqlite3` é da biblioteca padrão, aceita
`pandas.read_sql`, e exportar para o formato que o treino quiser é uma linha.

IDEMPOTENTE POR e2e_id, e isso é requisito de negócio, não de higiene: o mesmo
`e2e_id` gravado duas vezes contaria o mesmo fee duas vezes no agregado, e o
agregado é o que vai para a banca. A `UNIQUE (tenant_id, e2e_id)` faz a
deduplicação valer mesmo se a janela de idempotência da API (memória de
processo) tiver esquecido o evento por causa de um restart.

NUNCA LEVANTA. Toda escrita é best-effort com log, como o `_persist` do bandit
e o retention_log: perder uma linha de dataset é ruim; derrubar a recuperação
de um cliente por causa do disco é pior.

PII: nada aqui identifica o pagador. `customer_id` é o id da autorização de
recorrência, que é opaco, e a chave Pix nunca entrou no pipeline.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import custo_intervencao, custo_tentativa_pix, success_fee_pct

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "recovery_cycles.db"

ENV_CAMINHO = "CRAI_RECOVERY_DB"
TENANT_PADRAO = "default_tenant"

# As 11 features do classificador + o LTV. A lista mora aqui como CONTRATO do
# dataset: se o classificador ganhar uma feature e esta lista não ganhar, o
# treino com dados reais recebe uma coluna a menos e ninguém percebe até o
# `fit` reclamar de shape. `README_treino.md` (Sprint 7) referencia esta lista.
#
# Bloco H (22/09/2026): o artefato em produção passou a ser o da base v2, que
# trocou `card_brand` por `metodo_pagamento`. A coluna nova entra aqui e no
# schema (com migração para bancos já criados, ver `_conectar`). `card_brand`
# FICA na lista e no schema: as linhas gravadas antes a têm preenchida, e um
# retreino v1 (`train_all --base v1`) ainda a consome. Nas linhas novas ela
# vai NULL, porque o caminho de servir não a monta mais.
FEATURES_DO_DATASET = (
    "tenure_months", "day_of_month", "invoice_amount", "avg_ticket",
    "payment_history_score", "failure_count_90d", "hour_of_day",
    "day_of_week", "attempt_count", "gateway_error_code", "metodo_pagamento",
    "card_brand", "ltv_estimated",
)
FEATURES_TEXTO = ("gateway_error_code", "metodo_pagamento", "card_brand")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ciclos_recuperacao (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         TEXT    NOT NULL,
    customer_id       TEXT    NOT NULL,
    e2e_id            TEXT    NOT NULL,
    registrado_em     TEXT    NOT NULL,

    -- Features no momento do diagnóstico (o X do treino)
    tenure_months         REAL,
    day_of_month          REAL,
    invoice_amount        REAL,
    avg_ticket            REAL,
    payment_history_score REAL,
    failure_count_90d     REAL,
    hour_of_day           REAL,
    day_of_week           REAL,
    attempt_count         REAL,
    gateway_error_code    TEXT,
    metodo_pagamento      TEXT,
    card_brand            TEXT,
    ltv_estimated         REAL,

    -- O que o sistema diagnosticou e decidiu
    failure_cause         TEXT,
    recovery_score        REAL,
    p_recovery            REAL,
    eprofit               REAL,
    estrategia            TEXT,
    tentativas_planejadas INTEGER,

    -- O que efetivamente aconteceu (o y do treino, e o custo real)
    recovered             INTEGER NOT NULL DEFAULT 0,
    desfecho_em           TEXT,
    tentativas_usadas     INTEGER DEFAULT 0,
    custo_total           REAL    DEFAULT 0,
    success_fee           REAL    DEFAULT 0,
    amount                REAL,

    UNIQUE (tenant_id, e2e_id)
);
CREATE INDEX IF NOT EXISTS idx_ciclo_recuperacao_tenant
    ON ciclos_recuperacao (tenant_id, registrado_em);
"""


def caminho_do_banco() -> Path:
    """Lido a cada chamada para o teste poder redirecionar via env."""
    override = os.getenv(ENV_CAMINHO)
    return Path(override) if override else DB_PATH


# Colunas acrescentadas DEPOIS do schema original, com o tipo de cada uma.
# `CREATE TABLE IF NOT EXISTS` não altera uma tabela que já existe: um banco
# criado antes do Bloco H não teria `metodo_pagamento`, o INSERT falharia e —
# como toda escrita aqui é best-effort — o ciclo sumiria do dataset em
# silêncio. A migração é idempotente e barata (um PRAGMA por conexão).
_COLUNAS_ACRESCENTADAS = (("metodo_pagamento", "TEXT"),)


def _migrar(conn: sqlite3.Connection):
    existentes = {linha[1] for linha in conn.execute("PRAGMA table_info(ciclos_recuperacao)")}
    for coluna, tipo in _COLUNAS_ACRESCENTADAS:
        if coluna not in existentes:
            conn.execute(f"ALTER TABLE ciclos_recuperacao ADD COLUMN {coluna} {tipo}")
    conn.commit()


def _conectar() -> sqlite3.Connection:
    caminho = caminho_do_banco()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _migrar(conn)
    return conn


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(valor):
    """Só número vira número; o resto vira NULL.

    Mesma regra do retention_log: gravar `"muito"` numa coluna REAL entrega ao
    treino um dataset que o pandas lê como `object` e ninguém entende por quê.
    """
    if valor is None or isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)


def _texto(valor) -> Optional[str]:
    if valor is None:
        return None
    if isinstance(valor, (str, int, float)) and not isinstance(valor, bool):
        return str(valor)[:128]
    return None


def custo_realizado(tentativas_usadas: int, dunning_enviado: bool) -> float:
    """O custo REALIZADO deste ciclo — não o previsto (Gap 6).

    Duas parcelas, ambas com as constantes configuráveis do Sprint 5:
      - cada instrução reenviada ao PSP, contada pelas que de fato saíram;
      - a mensagem personalizada, contada só se ela foi enviada.

    A diferença entre isto e o `_custo_previsto_do_ciclo` do e-Profit é o ponto
    inteiro: o previsto usa o teto da janela para decidir SE vale agir; o
    realizado conta o que aconteceu, e é o que entra na margem.
    """
    custo = custo_tentativa_pix() * max(0, int(tentativas_usadas or 0))
    if dunning_enviado:
        custo += custo_intervencao()
    return round(custo, 4)


def registrar_ciclo(state: dict, tentativas_usadas: int = 0) -> Optional[int]:
    """Grava features + decisão. Devolve o id da linha, ou None.

    Chamado no `update_roi_dashboard`, que é o último nó do grafo nos dois
    caminhos. `recovered` entra como 0: em produção ninguém sabe ainda, e quem
    completa é a confirmação do PSP.

    Reexecução do grafo para o MESMO `e2e_id` (webhook reentregue depois de um
    restart, que zerou a janela de idempotência da API) não cria linha nova —
    atualiza a existente. Sem isso, o agregado contaria o mesmo ciclo duas
    vezes e a taxa de recuperação sairia diluída.
    """
    features = state.get("features") or {}
    plano = state.get("pix_retry_schedule") or []
    e2e = _texto(state.get("invoice_id")) or ""
    tenant = state.get("tenant_id") or TENANT_PADRAO

    if not e2e:
        logger.warning("[RECOVERY-LOG] Ciclo de %s sem e2e_id — não gravado. "
                       "Sem chave não há como fechá-lo nem deduplicá-lo.",
                       state.get("customer_id"))
        return None

    colunas = {
        "tenant_id": tenant,
        "customer_id": _texto(state.get("customer_id")) or "",
        "e2e_id": e2e,
        "registrado_em": _agora(),
        **{f: (_num(features.get(f)) if f not in FEATURES_TEXTO
               else _texto(features.get(f)))
           for f in FEATURES_DO_DATASET},
        "failure_cause": _texto(state.get("failure_cause")),
        "recovery_score": _num(state.get("recovery_score")),
        "p_recovery": _num(state.get("p_recovery")),
        "eprofit": _num(state.get("eprofit")),
        "estrategia": _texto(state.get("estrategia")),
        "tentativas_planejadas": len(plano),
        "recovered": 1 if state.get("recovered") else 0,
        "desfecho_em": _agora() if state.get("recovered") else None,
        "tentativas_usadas": int(tentativas_usadas or 0),
        "custo_total": custo_realizado(tentativas_usadas,
                                       bool(state.get("dunning_sent"))),
        "success_fee": (round(float(state.get("amount") or 0) * success_fee_pct(), 2)
                        if state.get("recovered") else 0.0),
        "amount": _num(state.get("amount")),
    }

    campos = ", ".join(colunas)
    marcas = ", ".join("?" * len(colunas))
    # `ON CONFLICT ... DO UPDATE` e não `INSERT OR IGNORE`: uma reentrega traz
    # o diagnóstico mais recente, e descartá-la deixaria a linha desatualizada.
    # O que NÃO pode voltar atrás é o desfecho — ver o `MAX` no `recovered`.
    atualizacoes = ", ".join(
        f"{c}=excluded.{c}" for c in colunas
        if c not in ("tenant_id", "e2e_id", "registrado_em", "recovered",
                     "desfecho_em", "success_fee")
    )
    try:
        with _conectar() as conn:
            cur = conn.execute(
                f"""INSERT INTO ciclos_recuperacao ({campos}) VALUES ({marcas})
                    ON CONFLICT (tenant_id, e2e_id) DO UPDATE SET
                        {atualizacoes},
                        recovered = MAX(ciclos_recuperacao.recovered, excluded.recovered)""",
                tuple(colunas.values()),
            )
            return cur.lastrowid
    except Exception as e:                       # noqa: BLE001 — best effort declarado
        logger.warning("[RECOVERY-LOG] Falha ao registrar ciclo: %s", e)
        return None


def registrar_recuperacao(
    customer_id: str, e2e_id: str, amount: float,
    tenant_id: str = TENANT_PADRAO, tentativas_usadas: int = 0,
    dunning_enviado: bool = False,
) -> bool:
    """Fecha o ciclo aberto com o desfecho REAL. False se não havia o que fechar.

    O `e2e_id` aqui é o da COBRANÇA QUE FALHOU e abriu o ciclo, não o da
    transação que a pagou: é aquele que identifica o ciclo na tabela. Quando a
    confirmação traz outro e2e (transação distinta, que é o caso normal), quem
    reencontra a linha é o ciclo aberto mais recente daquele cliente.

    Devolve `False` também no reenvio — a linha já está fechada —, e é isso que
    impede o fee de ser somado duas vezes no agregado.
    """
    try:
        with _conectar() as conn:
            linha = conn.execute(
                """SELECT id, amount FROM ciclos_recuperacao
                    WHERE tenant_id = ? AND customer_id = ? AND recovered = 0
                 ORDER BY id DESC LIMIT 1""",
                (tenant_id or TENANT_PADRAO, customer_id),
            ).fetchone()

            if linha is None:
                logger.info("[RECOVERY-LOG] Nenhum ciclo aberto para %s/%s — "
                            "confirmação não gravada (reenvio, ou pagamento que "
                            "nunca falhou).", tenant_id, customer_id)
                return False

            valor = float(amount or linha["amount"] or 0.0)
            conn.execute(
                """UPDATE ciclos_recuperacao
                      SET recovered = 1, desfecho_em = ?, tentativas_usadas = ?,
                          custo_total = ?, success_fee = ?, amount = ?
                    WHERE id = ?""",
                (_agora(), int(tentativas_usadas or 0),
                 custo_realizado(tentativas_usadas, dunning_enviado),
                 round(valor * success_fee_pct(), 2), valor, linha["id"]),
            )
            return True
    except Exception as e:                       # noqa: BLE001
        logger.warning("[RECOVERY-LOG] Falha ao registrar recuperação: %s", e)
        return False


def metricas(tenant_id: Optional[str] = None, desde: Optional[str] = None) -> dict:
    """O agregado de negócio (Gap 7) — o número que sustenta o Outcome-as-a-Service.

    Args:
        tenant_id: restringe a uma empresa cliente. `None` = todas.
        desde: ISO-8601; só ciclos registrados a partir dali.

    Returns:
        ciclos, recuperados, taxa_recuperacao, mrr_recuperado, volume_total,
        custo_total, custo_medio_por_recuperacao, fee_total e margem.

    `margem` é `fee_total - custo_total`, e é o número que diz se a operação se
    paga. Um custo médio por recuperação acima do fee médio significa que a
    CRAI está gastando mais para recuperar do que cobra por recuperar — e sem
    este agregado isso só apareceria na fatura do PSP.
    """
    filtros, params = [], []
    if tenant_id:
        filtros.append("tenant_id = ?")
        params.append(tenant_id)
    if desde:
        filtros.append("registrado_em >= ?")
        params.append(desde)
    onde = f"WHERE {' AND '.join(filtros)}" if filtros else ""

    vazio = {"ciclos": 0, "recuperados": 0, "taxa_recuperacao": 0.0,
             "mrr_recuperado": 0.0, "volume_total": 0.0, "custo_total": 0.0,
             "custo_medio_por_recuperacao": 0.0, "fee_total": 0.0, "margem": 0.0}
    try:
        with _conectar() as conn:
            linha = conn.execute(
                f"""SELECT COUNT(*) AS ciclos,
                           COALESCE(SUM(recovered), 0) AS recuperados,
                           COALESCE(SUM(CASE WHEN recovered = 1 THEN amount END), 0) AS mrr,
                           COALESCE(SUM(amount), 0) AS volume,
                           COALESCE(SUM(custo_total), 0) AS custo,
                           COALESCE(SUM(success_fee), 0) AS fee
                      FROM ciclos_recuperacao {onde}""",
                tuple(params),
            ).fetchone()
    except Exception as e:                       # noqa: BLE001
        logger.warning("[RECOVERY-LOG] Falha ao ler métricas: %s", e)
        return vazio

    ciclos = int(linha["ciclos"] or 0)
    recuperados = int(linha["recuperados"] or 0)
    custo = float(linha["custo"] or 0.0)
    fee = float(linha["fee"] or 0.0)

    return {
        "ciclos": ciclos,
        "recuperados": recuperados,
        "taxa_recuperacao": round(recuperados / ciclos, 4) if ciclos else 0.0,
        "mrr_recuperado": round(float(linha["mrr"] or 0.0), 2),
        "volume_total": round(float(linha["volume"] or 0.0), 2),
        "custo_total": round(custo, 4),
        # Custo TOTAL dividido pelas RECUPERAÇÕES, e não pelos ciclos. As duas
        # escolhas são deliberadas e apontam para o mesmo lado:
        #   - o numerador inclui o que se gastou nos ciclos perdidos, porque
        #     esse dinheiro foi gasto perseguindo recuperação e faz parte do
        #     custo de produzir a receita;
        #   - o denominador são as recuperações, não os ciclos, porque dividir
        #     por ciclo diluiria o custo nos que não geraram fee nenhum e faria
        #     a operação parecer mais barata do que é.
        # Comparar este número com o fee médio diz, sozinho, se a operação se
        # paga — e é a mesma base do `margem` logo abaixo.
        "custo_medio_por_recuperacao": round(custo / recuperados, 4) if recuperados else 0.0,
        "fee_total": round(fee, 2),
        "margem": round(fee - custo, 2),
    }


def linhas(tenant_id: Optional[str] = None, limite: int = 1000) -> list[dict]:
    """As linhas do dataset, para inspeção e para a fase de treino."""
    onde, params = ("WHERE tenant_id = ?", (tenant_id,)) if tenant_id else ("", ())
    try:
        with _conectar() as conn:
            return [dict(l) for l in conn.execute(
                f"SELECT * FROM ciclos_recuperacao {onde} ORDER BY id DESC LIMIT ?",
                (*params, int(limite)),
            )]
    except Exception as e:                       # noqa: BLE001
        logger.warning("[RECOVERY-LOG] Falha ao ler linhas: %s", e)
        return []


def resumo_legivel(metricas_dict: dict) -> str:
    """O agregado em uma linha por métrica, para a demo e para o log."""
    return json.dumps(metricas_dict, ensure_ascii=False, indent=2)

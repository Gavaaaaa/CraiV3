"""crai/churn_voluntary/origem_da_base.py — por onde a base foi atualizada por último.

O dashboard do voluntário mostra "Última atualização: pela API" ou "por anexo"
(Rodada 3, `GET /clientes/base`). A base importada não guarda por onde cada
linha chegou, e acrescentar uma coluna a ela mexeria na tabela que em produção
mora no Postgres da empresa. Aqui fica UMA linha por empresa, com a origem e o
instante da última escrita aceita:

    api     `POST /clientes`, `POST /clientes/lote`, `PATCH` e `DELETE /clientes/{id}`
    anexo   `POST /clientes/importar` (a planilha)

ONDE MORA. Tabela `base_atualizacoes`, no arquivo do ciclo de cobrança
(`recovery_cycles.db`, env `CRAI_RECOVERY_DB`), ao lado da configuração da
empresa. Não guarda nada do cliente final: só o tenant, a origem e a hora.

BEST EFFORT NA GRAVAÇÃO: uma falha aqui é logada e a escrita na base segue. Sem
linha (base anterior à Rodada 3, ou gravação que falhou), a origem é `None`, e
a tela diz que não sabe em vez de adivinhar.

Este módulo NÃO importa de `api/`.
"""

import logging
from datetime import datetime
from typing import Optional

from ..dunning import ciclo_cobranca

logger = logging.getLogger(__name__)

ORIGEM_API = "api"
ORIGEM_ANEXO = "anexo"
ORIGENS = (ORIGEM_API, ORIGEM_ANEXO)

_DDL = """
CREATE TABLE IF NOT EXISTS base_atualizacoes (
    tenant_id     TEXT PRIMARY KEY,
    origem        TEXT NOT NULL,
    atualizada_em TEXT NOT NULL
);
"""


def _conectar():
    conn = ciclo_cobranca._conectar()
    conn.executescript(_DDL)
    return conn


def registrar(tenant_id: str, origem: str, agora: Optional[datetime] = None) -> bool:
    """Grava a origem da última escrita aceita. Nunca levanta."""
    if origem not in ORIGENS:
        raise ValueError(f"origem desconhecida: {origem!r}")
    try:
        conn = _conectar()
        try:
            conn.execute(
                """INSERT INTO base_atualizacoes (tenant_id, origem, atualizada_em)
                   VALUES (?, ?, ?)
                   ON CONFLICT (tenant_id) DO UPDATE SET origem = excluded.origem,
                       atualizada_em = excluded.atualizada_em""",
                (tenant_id, origem, (agora or datetime.now()).isoformat(timespec="seconds")))
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:                        # noqa: BLE001 - best effort declarado
        logger.error("[BASE] origem da atualização não gravada (tenant=%s): %r", tenant_id, e)
        return False


def ultima(tenant_id: str) -> Optional[dict]:
    """`{origem, atualizada_em}` (hora local sem fuso) ou None."""
    conn = _conectar()
    try:
        linha = conn.execute(
            "SELECT origem, atualizada_em FROM base_atualizacoes WHERE tenant_id = ?",
            (tenant_id,)).fetchone()
    finally:
        conn.close()
    return dict(linha) if linha else None

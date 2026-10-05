"""crai/api/eventos_recebidos.py — a idempotência de `POST /eventos` (Rodada 3, S7).

O sistema da empresa reenvia o que não teve resposta: uma queda de rede no meio
da chamada, uma fila que reprocessa. O mesmo evento reenviado tem de contar UMA
vez: senão o pipeline rodaria duas vezes, e o dataset de treino ganharia duas
linhas para um fato só.

A CHAVE DE IDEMPOTÊNCIA de um evento, nesta ordem:

    messageId    o identificador que o próprio protocolo do Segment já tem. Se
                 o corpo o traz, é ele que identifica o evento.
    o corpo      sem `messageId`, o que identifica é o conteúdo: quem (`userId`
                 ou `anonymousId`), o que (`event`), as `properties` e o
                 `timestamp`. Reenviar o corpo idêntico conta uma vez; para
                 avisar o mesmo fato de novo, o `timestamp` muda.

O QUE É GUARDADO: o SHA-256 da chave (com o tenant dentro), o tenant e a hora.
Nada do evento: nem a identidade do cliente final, nem as propriedades. A
tabela `eventos_recebidos` mora no arquivo do ciclo de cobrança, ao lado das
chaves de API. Linhas com mais de `JANELA_DIAS` são apagadas a cada evento
novo: depois disso, um reenvio é tratado como evento novo.

A idempotência é por empresa: o mesmo `messageId` em duas empresas são dois
eventos.

Este módulo NÃO importa de `app.py`.
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta

from ..dunning import ciclo_cobranca

logger = logging.getLogger(__name__)

JANELA_DIAS = 30
MESSAGE_ID_MAX = 128
CAMPOS_DO_CONTEUDO = ("userId", "anonymousId", "event", "properties", "timestamp")

_DDL = """
CREATE TABLE IF NOT EXISTS eventos_recebidos (
    tenant_id   TEXT NOT NULL,
    chave       TEXT NOT NULL,
    recebido_em TEXT NOT NULL,
    PRIMARY KEY (tenant_id, chave)
);
CREATE INDEX IF NOT EXISTS idx_eventos_recebidos_em ON eventos_recebidos (recebido_em);
"""


def _conectar():
    conn = ciclo_cobranca._conectar()
    conn.executescript(_DDL)
    return conn


def chave_do_evento(tenant_id: str, payload: dict) -> str:
    """O SHA-256 que identifica este evento para esta empresa."""
    message_id = payload.get("messageId")
    if isinstance(message_id, str) and message_id.strip():
        base = "id:" + message_id.strip()
    else:
        base = "corpo:" + json.dumps({c: payload.get(c) for c in CAMPOS_DO_CONTEUDO},
                                     sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(f"{tenant_id}|{base}".encode("utf-8")).hexdigest()


def registrar(tenant_id: str, chave: str, agora: datetime) -> bool:
    """True se o evento é NOVO (e fica registrado); False se já tinha chegado.
    A inserção é a própria conferência: dois reenvios simultâneos não passam os
    dois."""
    quando = agora.isoformat(timespec="seconds")
    limite = (agora - timedelta(days=JANELA_DIAS)).isoformat(timespec="seconds")
    conn = _conectar()
    try:
        conn.execute("DELETE FROM eventos_recebidos WHERE recebido_em < ?", (limite,))
        cur = conn.execute(
            "INSERT OR IGNORE INTO eventos_recebidos (tenant_id, chave, recebido_em) "
            "VALUES (?, ?, ?)", (tenant_id, chave, quando))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def esquecer(tenant_id: str, chave: str) -> None:
    """Desfaz o registro de um evento cujo processamento FALHOU: o reenvio do
    sistema da empresa precisa poder processá-lo. Nunca levanta."""
    try:
        conn = _conectar()
        try:
            conn.execute("DELETE FROM eventos_recebidos WHERE tenant_id = ? AND chave = ?",
                         (tenant_id, chave))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:                        # noqa: BLE001
        logger.error("[EVENTOS] não foi possível desfazer o registro do evento: %r", e)


def contar(tenant_id: str) -> int:
    conn = _conectar()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM eventos_recebidos WHERE tenant_id = ?",
                                (tenant_id,)).fetchone()[0])
    finally:
        conn.close()

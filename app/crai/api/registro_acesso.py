"""crai/api/registro_acesso.py — quem leu dado de titular, e quando (Etapa 2, Bloco 4).

O QUE É. Uma linha por leitura das rotas que devolvem dado de cliente final
(titular): a explicação do Art. 20 (`GET /titular/explicacao/{id}`), o detalhe
de um ciclo (`GET /ciclos/{id}`) e a lista de ciclos (`GET /ciclos`, que desde
o Bloco 3 lê o `cliente_nome` da base). Serve para a controladora — e para a
CRAI, como operadora — conseguir responder "quem acessou os dados deste
período" (LGPD, Art. 37: registro das operações de tratamento).

O QUE A LINHA TEM, e só isso: `tenant_id`, `rota` (o MODELO da rota, com
`{ciclo_id}` e `{sujeito_id}` literais — nunca o id pedido), `papel` (owner,
admin, membro, ou NULL quando o token não traz papel) e `quando`. Não tem quem
é a pessoa logada (e-mail, `sub`), não tem o identificador do titular, não tem
IP: o registro de acesso não pode virar, ele mesmo, um banco de dado pessoal.

ONDE MORA. Tabela `acessos_titular`, no arquivo do ciclo de cobrança
(`recovery_cycles.db`, env `CRAI_RECOVERY_DB`) — o mesmo que a suíte já isola.
`quando` é hora local sem fuso, como as outras datas desse arquivo.

RETENÇÃO: 12 meses. `expurgar` é chamada pela passagem diária do relógio
(`api/relogio.py`).

BEST EFFORT NA GRAVAÇÃO. Uma falha ao registrar é logada em ERROR e a leitura
segue: a controladora não pode ficar sem responder ao titular porque o registro
de acesso não gravou. O expurgo, ao contrário, LEVANTA: retenção que falha em
silêncio é dado guardado além do prazo sem ninguém saber.

Este módulo NÃO importa de `app.py`.
"""

import logging
from datetime import datetime
from typing import Optional

from ..dunning import ciclo_cobranca

logger = logging.getLogger(__name__)

RETENCAO_MESES = 12

ROTA_EXPLICACAO = "GET /titular/explicacao/{sujeito_id}"
ROTA_CICLO = "GET /ciclos/{ciclo_id}"
ROTA_CICLOS = "GET /ciclos"

_DDL = """
CREATE TABLE IF NOT EXISTS acessos_titular (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    rota      TEXT NOT NULL,
    papel     TEXT,
    quando    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acessos_titular_quando ON acessos_titular (quando);
CREATE INDEX IF NOT EXISTS idx_acessos_titular_tenant ON acessos_titular (tenant_id, quando);
"""


def _conectar():
    conn = ciclo_cobranca._conectar()
    conn.executescript(_DDL)
    return conn


def registrar(tenant_id: str, rota: str, papel: Optional[str] = None,
              agora: Optional[datetime] = None) -> bool:
    """Grava um acesso. Nunca levanta: devolve False e loga em ERROR."""
    try:
        conn = _conectar()
        try:
            conn.execute(
                "INSERT INTO acessos_titular (tenant_id, rota, papel, quando) VALUES (?, ?, ?, ?)",
                (tenant_id, rota, papel, (agora or datetime.now()).isoformat(timespec="seconds")))
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as e:                        # noqa: BLE001 — best effort declarado
        logger.error("[ACESSO] registro de acesso não gravado (tenant=%s rota=%s): %r",
                     tenant_id, rota, e)
        return False


def acessos(tenant_id: str, limite: int = 200) -> list[dict]:
    """Os acessos de uma empresa, do mais recente ao mais antigo."""
    conn = _conectar()
    try:
        return [dict(l) for l in conn.execute(
            "SELECT tenant_id, rota, papel, quando FROM acessos_titular "
            "WHERE tenant_id = ? ORDER BY quando DESC, id DESC LIMIT ?", (tenant_id, limite))]
    finally:
        conn.close()


def limite_de_retencao(agora: datetime) -> datetime:
    """`agora` menos `RETENCAO_MESES`, no calendário (29/02 vira 28/02)."""
    total = agora.year * 12 + (agora.month - 1) - RETENCAO_MESES
    ano, mes = divmod(total, 12)
    mes += 1
    dia = agora.day
    while True:
        try:
            return agora.replace(year=ano, month=mes, day=dia)
        except ValueError:
            dia -= 1


def expurgar(agora: datetime) -> int:
    """Apaga os acessos com mais de 12 meses. Devolve quantas linhas saíram."""
    limite = limite_de_retencao(agora).isoformat(timespec="seconds")
    conn = _conectar()
    try:
        apagadas = conn.execute("DELETE FROM acessos_titular WHERE quando < ?", (limite,)).rowcount
        conn.commit()
        return int(apagadas)
    finally:
        conn.close()

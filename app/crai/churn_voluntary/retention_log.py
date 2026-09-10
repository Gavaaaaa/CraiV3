"""crai/churn_voluntary/retention_log.py — o DATASET de treino do churn voluntário.

POR QUE ISTO EXISTE. Sem este módulo o sistema aprende e esquece: o
`offer_bandit` persiste `alpha`/`beta`, que é um PLACAR agregado por
(perfil × oferta), não um conjunto de dados. As features que produziram a
decisão — inatividade, uso, MRR, criticidade — não sobrevivem ao nó do grafo,
e o `MemorySaver` morre no restart. Rodar três meses em produção e então
querer treinar um modelo de risco daria α/β e nenhuma linha.

O que este arquivo grava é a linha: **uma por ciclo de retenção**, com as
features no momento da decisão, a decisão tomada e — quando chega — o
desfecho. É o que o `README_treino.md` (Sprint 6) documenta e o que a fase de
treino consome.

DUAS ESCRITAS POR CICLO, e a segunda pode demorar dias:

    registrar_ciclo(...)   no `update_crm`, fim do grafo. Features + decisão.
                           `accepted` fica NULL: em produção ninguém sabe ainda.
    registrar_desfecho(...) quando o webhook `/webhooks/retention-outcome`
                           chega. Completa a linha aberta mais recente daquele
                           (tenant, user, oferta).

SQLITE, e não Parquet: a segunda escrita é um UPDATE numa linha gravada antes,
possivelmente em outro processo e outro dia. Parquet é imutável por arquivo —
completar uma linha significaria reescrever partição ou manter dois arquivos
para juntar depois. `sqlite3` é da biblioteca padrão, aceita
`pandas.read_sql`, e a exportação para o formato que o treino quiser é uma
linha. Para o volume de um TCC (e de um SaaS PME) isso sobra.

DEDUPLICAÇÃO SAI DE GRAÇA. O webhook pode reenviar o mesmo resultado; contar
duas vezes envenenaria o posterior do bandit. Como a linha do ciclo registra
se já tem desfecho, `registrar_desfecho` devolve `False` no reenvio e o
chamador não chama `record_outcome`. Isso é melhor que a memória curta em RAM
que o plano sugeria: sobrevive a restart, que é exatamente quando um reenvio
acontece.

NUNCA LEVANTA. Toda escrita é best-effort e engolida com log, como o
`_persist` do bandit: perder uma linha de dataset é ruim; derrubar o ciclo de
retenção de um cliente por causa do disco é pior.

PII: o telefone NÃO entra aqui. `user_id` entra já qualificado
(`user:` / `anon:`), que é a identidade que o resto do sistema usa.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "retention_cycles.db"

TENANT_PADRAO = "default_tenant"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ciclos_retencao (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         TEXT    NOT NULL,
    user_id           TEXT    NOT NULL,
    registrado_em     TEXT    NOT NULL,

    -- Features no momento da decisão (o X do treino)
    event             TEXT,
    days_since_last   REAL,
    features_used_30d REAL,
    mrr               REAL,
    billing_profile   TEXT,
    on_site_now       INTEGER,

    -- O que o sistema decidiu (contexto, e o braço do bandit)
    risk_score        REAL,
    profile           TEXT,
    criticality       TEXT,
    offer_type        TEXT,
    channel           TEXT,
    offer_sent        INTEGER,

    -- Desfecho (o y do treino). NULL = ciclo aberto, aguardando retorno.
    accepted          INTEGER,
    desfecho_em       TEXT,
    origem_desfecho   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ciclo_aberto
    ON ciclos_retencao (tenant_id, user_id, offer_type, accepted);
"""


def caminho_do_banco() -> Path:
    """Lido a cada chamada para o teste poder redirecionar via env.

    `CRAI_RETENTION_DB` existe pelo mesmo motivo que a fixture que isola
    `MODELS_DIR`: foi uma suíte escrevendo no arquivo real que encheu o
    `bandit_state.json` de perfis de fuzz.
    """
    override = os.getenv("CRAI_RETENTION_DB")
    return Path(override) if override else DB_PATH


def _conectar() -> sqlite3.Connection:
    caminho = caminho_do_banco()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(valor):
    """Só número vira número; o resto vira NULL.

    `props` é payload de terceiro e já derrubou o pipeline uma vez por causa
    disso (o 🔴 do `mrr` no Sprint 2). Aqui o custo de um valor torto é menor
    — uma coluna nula —, mas gravar `"muito"` numa coluna REAL entrega ao
    treino um dataset que o pandas lê como `object` e ninguém entende por quê.
    """
    if valor is None or isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)


def registrar_ciclo(state: dict) -> int | None:
    """Grava features + decisão. Devolve o id da linha, ou None se falhou.

    Chamado no `update_crm`, que é o último nó nos DOIS modos. Em simulação o
    `accepted` já é conhecido e entra junto; em produção fica NULL até o
    webhook.
    """
    props = state.get("props") or {}
    try:
        with _conectar() as conn:
            cur = conn.execute(
                """INSERT INTO ciclos_retencao (
                       tenant_id, user_id, registrado_em,
                       event, days_since_last, features_used_30d, mrr,
                       billing_profile, on_site_now,
                       risk_score, profile, criticality, offer_type, channel,
                       offer_sent, accepted, desfecho_em, origem_desfecho)
                   VALUES (?,?,?, ?,?,?,?, ?,?, ?,?,?,?,?, ?,?,?,?)""",
                (
                    state.get("tenant_id") or TENANT_PADRAO,
                    state.get("user_id", ""),
                    _agora(),
                    state.get("event"),
                    _num(props.get("days_since_last")),
                    _num(props.get("features_used_30d")),
                    _num(props.get("mrr")),
                    props.get("billing_profile") if isinstance(
                        props.get("billing_profile"), str) else None,
                    int(bool(state.get("on_site_now"))),
                    _num(state.get("risk_score")),
                    state.get("profile"),
                    state.get("criticality"),
                    state.get("offer_type"),
                    state.get("channel"),
                    int(bool(state.get("offer_sent"))),
                    None if state.get("accepted") is None else int(state["accepted"]),
                    _agora() if state.get("accepted") is not None else None,
                    "simulacao" if state.get("accepted") is not None else None,
                ),
            )
            return cur.lastrowid
    except Exception as e:                       # noqa: BLE001 — best effort declarado
        print(f"[RETENTION-LOG] Falha ao registrar ciclo: {e}")
        return None


def ciclo_aberto(tenant_id: str, user_id: str, offer_type: str) -> dict | None:
    """A linha mais recente daquele (tenant, user, oferta) SEM desfecho.

    É por aqui que o webhook reencontra o contexto que ele não carrega:
    `risk_score`, `event` e `channel` ficaram gravados na decisão e o HubSpot
    precisa deles.
    """
    try:
        with _conectar() as conn:
            linha = conn.execute(
                """SELECT * FROM ciclos_retencao
                    WHERE tenant_id = ? AND user_id = ? AND offer_type = ?
                      AND accepted IS NULL
                 ORDER BY id DESC LIMIT 1""",
                (tenant_id, user_id, offer_type),
            ).fetchone()
            return dict(linha) if linha else None
    except Exception as e:                       # noqa: BLE001
        print(f"[RETENTION-LOG] Falha ao consultar ciclo aberto: {e}")
        return None


def registrar_desfecho(tenant_id: str, user_id: str, offer_type: str,
                       accepted: bool, origem: str = "webhook") -> bool:
    """Fecha o ciclo aberto mais recente. Devolve False se não havia o que fechar.

    `False` significa uma de duas coisas, e as duas pedem que o chamador NÃO
    atualize o bandit:

      - REENVIO: o ciclo já tem desfecho. Contar de novo enviesaria o
        posterior para o lado que reenviou.
      - SEM CICLO: chegou desfecho de uma oferta que este sistema não fez.
        Aprender com isso seria aprender com dado de origem desconhecida.

    A distinção entre os dois casos sai no log, não no retorno — quem chama só
    precisa saber se deve ou não contar.
    """
    try:
        with _conectar() as conn:
            linha = conn.execute(
                """SELECT id FROM ciclos_retencao
                    WHERE tenant_id = ? AND user_id = ? AND offer_type = ?
                      AND accepted IS NULL
                 ORDER BY id DESC LIMIT 1""",
                (tenant_id, user_id, offer_type),
            ).fetchone()

            if linha is None:
                ja_fechado = conn.execute(
                    """SELECT 1 FROM ciclos_retencao
                        WHERE tenant_id = ? AND user_id = ? AND offer_type = ?
                     LIMIT 1""",
                    (tenant_id, user_id, offer_type),
                ).fetchone()
                motivo = ("desfecho já registrado (reenvio)" if ja_fechado
                          else "nenhum ciclo correspondente")
                print(f"[RETENTION-LOG] Ignorado — {motivo}: "
                      f"{tenant_id}/{user_id}/{offer_type}")
                return False

            conn.execute(
                """UPDATE ciclos_retencao
                      SET accepted = ?, desfecho_em = ?, origem_desfecho = ?
                    WHERE id = ?""",
                (int(bool(accepted)), _agora(), origem, linha["id"]),
            )
            return True
    except Exception as e:                       # noqa: BLE001
        print(f"[RETENTION-LOG] Falha ao registrar desfecho: {e}")
        return False


def ultimo_ciclo_por_cliente(tenant_id: str) -> list[dict]:
    """A linha MAIS RECENTE de cada cliente deste tenant — o "estado atual" do SDK.

    Leitura para o self-service (Sprint 4 do onboarding): o `/insights` junta
    esta lista com a base importada. Vive aqui, e não no módulo de insights,
    porque o schema é deste arquivo — quem grava é quem sabe ler.

    Uma linha por `user_id`, a de maior `id` (o autoincrement é a ordem de
    gravação, e `registrado_em` tem resolução de segundo). Como o resto do
    módulo, NUNCA levanta: falha de leitura devolve lista vazia com log — o
    insight do upload continua saindo mesmo se o banco do SDK estiver fora.
    """
    try:
        with _conectar() as conn:
            linhas = conn.execute(
                """SELECT c.tenant_id, c.user_id, c.registrado_em, c.event,
                          c.days_since_last, c.features_used_30d, c.mrr,
                          c.billing_profile, c.risk_score, c.criticality,
                          c.offer_type, c.channel, c.accepted
                     FROM ciclos_retencao c
                     JOIN (SELECT user_id, MAX(id) AS ultimo
                             FROM ciclos_retencao
                            WHERE tenant_id = ?
                         GROUP BY user_id) u
                       ON u.ultimo = c.id
                    WHERE c.tenant_id = ?""",
                (tenant_id, tenant_id),
            ).fetchall()
            return [dict(l) for l in linhas]
    except Exception as e:                       # noqa: BLE001
        print(f"[RETENTION-LOG] Falha ao ler último ciclo por cliente: {e}")
        return []


def estatisticas() -> dict:
    """Contagem para a demo e para saber se há dataset suficiente para treinar."""
    try:
        with _conectar() as conn:
            total = conn.execute("SELECT COUNT(*) FROM ciclos_retencao").fetchone()[0]
            fechados = conn.execute(
                "SELECT COUNT(*) FROM ciclos_retencao WHERE accepted IS NOT NULL"
            ).fetchone()[0]
            aceitos = conn.execute(
                "SELECT COUNT(*) FROM ciclos_retencao WHERE accepted = 1"
            ).fetchone()[0]
            return {"total": total, "com_desfecho": fechados,
                    "aguardando": total - fechados, "aceitos": aceitos}
    except Exception as e:                       # noqa: BLE001
        print(f"[RETENTION-LOG] Falha ao ler estatísticas: {e}")
        return {"total": 0, "com_desfecho": 0, "aguardando": 0, "aceitos": 0}

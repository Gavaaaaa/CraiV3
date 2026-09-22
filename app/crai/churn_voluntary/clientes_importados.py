"""crai/churn_voluntary/clientes_importados.py — a base de clientes que a empresa anexou.

O CAMINHO (2) DO SELF-SERVICE. O caminho (1) é o SDK: evento a evento, via
`/webhooks/segment`, gravado em `retention_cycles.db`. Este módulo é o outro
caminho — a empresa que NÃO tem tracking sobe a planilha que já tem em mãos
(MRR, perfil, última atividade) e recebe uma primeira análise sem
instrumentar nada. Os dois caminhos convivem; o Sprint 4 os junta.

ONDE MORA. No **Postgres do Supabase**, o mesmo banco que já guarda a tabela
`empresas` de quem faz login — decisão de arquitetura tomada antes do sprint
(ver `accounts/README.md`). A CRAI conecta com uma connection string de
serviço, `SUPABASE_DB_URL`, e usa `psycopg2` direto: o resto do pacote fala
SQL puro com `sqlite3`, e um ORM só para uma tabela seria a peça mais pesada
do pacote.

SQLITE SÓ PARA TESTE E DESENVOLVIMENTO. `CRAI_CLIENTES_DB=<caminho>` aponta
para um arquivo SQLite e é o que a suíte usa (o `conftest` a isola em
`tmp_path`). O SQL é o mesmo nos dois — `ON CONFLICT ... DO UPDATE`, tipos
que os dois aceitam — e a única diferença é o placeholder (`?` vs `%s`), que
`_Conexao` traduz. Sem NENHUMA das duas envs, falha ALTO
(`ConfiguracaoAusente`): um endpoint de importação que grava "em algum lugar"
por default é como o cliente descobre, semanas depois, que a base ficou num
arquivo local do container.

CHAVE (tenant_id, customer_id_externo). Reimportar a mesma base ATUALIZA a
linha em vez de duplicar — a planilha é uma foto, e a foto mais nova vale.
`importado_em` guarda quando a foto foi tirada, que é o que o Sprint 4 compara
com `registrado_em` do SDK para decidir qual dado é o mais recente.

PII: `email` é opcional e só existe para o "insight por e-mail" futuro. Nada
de telefone, CPF ou nome. `motivo_cancelamento` é texto livre vindo do backend
do cliente e PODE conter dado pessoal: não entra em log nem em resposta de
listagem agregada — só na resposta da própria operação de cancelar.

O CANCELAMENTO (`cancelado_em`). É o único sinal, em todo o sistema, de que
um cliente de fato saiu. `accepted` no retention_log é aceitação de oferta,
outra coisa. Sem desfecho observado o modelo de risco voluntário só consegue
aprender o rótulo que a própria regra produziu (base v2: AUC do modelo 0,8287
contra 0,8295 da regra). Esta coluna é a semente do treino com desfecho real —
por isso é um SOFT DELETE: a linha fica, o upsert em lote não a apaga, e só a
reativação explícita a limpa.
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_POSTGRES_URL = "SUPABASE_DB_URL"
ENV_SQLITE_PATH = "CRAI_CLIENTES_DB"
TABELA = "clientes_importados"

# Válido em Postgres E em SQLite (>= 3.24, pelo ON CONFLICT). `importado_em` é
# ISO-8601 UTC em texto, o mesmo formato de `registrado_em` no retention_log:
# o Sprint 4 compara os dois como texto, e ISO ordena lexicograficamente.
SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABELA} (
    tenant_id           TEXT NOT NULL,
    customer_id_externo TEXT NOT NULL,
    mrr                 DOUBLE PRECISION NOT NULL,
    billing_profile     TEXT NOT NULL,
    days_since_last     DOUBLE PRECISION,
    features_used_30d   DOUBLE PRECISION,
    email               TEXT,
    importado_em        TEXT NOT NULL,
    cancelado_em        TEXT,
    motivo_cancelamento TEXT,
    atualizado_em       TEXT,
    PRIMARY KEY (tenant_id, customer_id_externo)
);
"""

# Colunas que entraram DEPOIS da tabela existir em produção. `CREATE TABLE IF
# NOT EXISTS` não acrescenta coluna a tabela que já existe, então quem criou a
# tabela antes ficaria sem elas — e o erro só apareceria em produção. A
# migração (`_migrar_schema`) roda em toda garantia de schema e é idempotente:
# em SQLite lê `PRAGMA table_info` e só acrescenta o que falta (o `ALTER TABLE
# ... ADD COLUMN` do SQLite não aceita `IF NOT EXISTS`); em Postgres o próprio
# `ADD COLUMN IF NOT EXISTS` resolve. Toda coluna aqui tem que ser NULA por
# definição: um ALTER com NOT NULL sem default falha em tabela populada.
#
#   cancelado_em         ISO-8601 UTC, mesmo formato de `importado_em`
#   motivo_cancelamento  texto livre do backend do cliente (<= 500 chars, PII)
#   atualizado_em        última alteração parcial (`atualizar_parcial`)
COLUNAS_MIGRACAO = (
    ("cancelado_em", "TEXT"),
    ("motivo_cancelamento", "TEXT"),
    ("atualizado_em", "TEXT"),
)

MOTIVO_CANCELAMENTO_MAX = 500

COLUNAS = ("tenant_id", "customer_id_externo", "mrr", "billing_profile",
           "days_since_last", "features_used_30d", "email", "importado_em",
           "cancelado_em", "motivo_cancelamento", "atualizado_em")

# O que `atualizar_parcial` aceita mudar. Fora daqui é ValueError: identidade
# (tenant, id), carimbos e o cancelamento têm operação própria, de propósito.
COLUNAS_PARCIAIS = ("mrr", "billing_profile", "days_since_last",
                    "features_used_30d", "email")


class ConfiguracaoAusente(RuntimeError):
    """Nem `SUPABASE_DB_URL` nem `CRAI_CLIENTES_DB` configuradas."""


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Conexão: Postgres (produção) ou SQLite (teste/dev), mesmo SQL ─────────

class _Conexao:
    """Um cursor de vocabulário mínimo: `executar(sql, params)` e `fechar()`.

    Só existe para o módulo escrever o SQL uma vez. `?` é o placeholder de
    referência (o do `sqlite3`); no psycopg2 vira `%s`. Nenhum SQL deste
    módulo carrega `?` literal em string, então a troca é segura.
    """

    def __init__(self, raw, postgres: bool):
        self._raw = raw
        self._postgres = postgres

    def executar(self, sql: str, params: tuple = ()) -> list[dict]:
        if self._postgres:
            sql = sql.replace("?", "%s")
        cur = self._raw.cursor()
        cur.execute(sql, params)
        if cur.description is None:
            return []
        nomes = [d[0] for d in cur.description]
        return [dict(zip(nomes, linha)) for linha in cur.fetchall()]

    def commit(self):
        self._raw.commit()

    def fechar(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, tipo, valor, tb):
        try:
            if tipo is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self._raw.close()


_schema_garantido_lock = threading.Lock()
_schema_garantido: set = set()


def _destino() -> tuple:
    """("postgres", url) | ("sqlite", caminho). Lido a cada chamada (env por teste).

    Postgres vence se as duas estiverem configuradas: a env de SQLite é a
    de teste, e um teste que esqueça de limpar a de Postgres deve bater no
    Postgres e falhar alto, não gravar em silêncio num arquivo.
    """
    url = os.getenv(ENV_POSTGRES_URL)
    if url and url.strip():
        return "postgres", url.strip()
    caminho = os.getenv(ENV_SQLITE_PATH)
    if caminho and caminho.strip():
        return "sqlite", caminho.strip()
    raise ConfiguracaoAusente(
        f"nem {ENV_POSTGRES_URL} (Postgres do Supabase, produção) nem "
        f"{ENV_SQLITE_PATH} (SQLite, só teste/dev) estão configuradas — a base "
        "importada precisa de um destino declarado; ela não grava 'em algum lugar' "
        "por default")


def _conectar() -> _Conexao:
    tipo, alvo = _destino()
    if tipo == "postgres":
        import psycopg2                       # importado sob demanda, como httpx no pagarme

        raw = psycopg2.connect(alvo)
        conn = _Conexao(raw, postgres=True)
    else:
        Path(alvo).parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(alvo)
        conn = _Conexao(raw, postgres=False)

    _garantir_schema(conn, alvo)
    return conn


def _garantir_schema(conn: _Conexao, alvo: str) -> None:
    """DDL idempotente + migração, uma vez por processo por destino.

    No Supabase a tabela também pode ser criada pelo painel (o DDL é
    `SCHEMA_SQL`, ver README); aqui é a garantia de que a primeira importação
    não falha por falta dela — e de que uma tabela criada ANTES das colunas
    de `COLUNAS_MIGRACAO` chega ao mesmo schema de uma criada hoje.
    """
    with _schema_garantido_lock:
        if alvo in _schema_garantido:
            return
        conn.executar(SCHEMA_SQL)
        _migrar_schema(conn)
        conn.commit()
        _schema_garantido.add(alvo)


def _migrar_schema(conn: _Conexao) -> None:
    """Acrescenta as colunas de `COLUNAS_MIGRACAO` que faltam. Idempotente.

    Postgres: `ADD COLUMN IF NOT EXISTS`, direto. SQLite: não existe `IF NOT
    EXISTS` no `ADD COLUMN`, então lê `PRAGMA table_info` e acrescenta só o
    que falta. O resultado é o mesmo nos dois: `COLUNAS` inteira presente.
    """
    if conn._postgres:
        for coluna, tipo in COLUNAS_MIGRACAO:
            conn.executar(
                f"ALTER TABLE {TABELA} ADD COLUMN IF NOT EXISTS {coluna} {tipo}")
        return
    existentes = {l["name"] for l in conn.executar(f"PRAGMA table_info({TABELA})")}
    for coluna, tipo in COLUNAS_MIGRACAO:
        if coluna not in existentes:
            conn.executar(f"ALTER TABLE {TABELA} ADD COLUMN {coluna} {tipo}")


def esquecer_schema_garantido() -> None:
    """Para os testes: cada `tmp_path` é um banco novo.

    Zera o estado do schema E o da migração — são a mesma marca por destino
    (`_garantir_schema` faz os dois de uma vez), então na próxima conexão
    o `CREATE TABLE IF NOT EXISTS` e o `ALTER TABLE` rodam de novo.
    """
    with _schema_garantido_lock:
        _schema_garantido.clear()


# ── Escrita ───────────────────────────────────────────────────────────────

# O `DO UPDATE SET` lista as colunas da foto (planilha/API) e SÓ elas. Ele
# não pode tocar em `cancelado_em`/`motivo_cancelamento`: se tocasse, subir a
# planilha de novo apagaria em silêncio o único desfecho observado que o
# sistema tem. Reativar é explícito (`upsert_um(..., reativar=True)`).
# Também não toca em `atualizado_em`, que é o carimbo do PATCH.
_UPSERT = f"""
INSERT INTO {TABELA} (tenant_id, customer_id_externo, mrr, billing_profile,
                      days_since_last, features_used_30d, email, importado_em)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (tenant_id, customer_id_externo) DO UPDATE SET
    mrr               = excluded.mrr,
    billing_profile   = excluded.billing_profile,
    days_since_last   = excluded.days_since_last,
    features_used_30d = excluded.features_used_30d,
    email             = excluded.email,
    importado_em      = excluded.importado_em
"""


def _params_upsert(tenant_id: str, c: dict, agora: str) -> tuple:
    return (tenant_id, c["customer_id_externo"], float(c["mrr"]),
            c["billing_profile"], c.get("days_since_last"),
            c.get("features_used_30d"), c.get("email"), agora)


def _buscar(conn: _Conexao, tenant_id: str, customer_id_externo: str):
    linhas = conn.executar(
        f"SELECT {', '.join(COLUNAS)} FROM {TABELA} "
        "WHERE tenant_id = ? AND customer_id_externo = ?",
        (tenant_id, customer_id_externo))
    return linhas[0] if linhas else None


def gravar(tenant_id: str, clientes: list[dict]) -> int:
    """Upsert de linhas JÁ VALIDADAS (ver `importacao.py`). Devolve quantas.

    Uma transação para o lote inteiro: ou a base nova entra toda, ou nada
    muda. Meio lote gravado seria a pior das fotos — metade de ontem, metade
    de hoje, sem como saber qual metade.

    NUNCA reativa. Um cliente cancelado que apareça na planilha tem a foto
    atualizada (MRR, perfil, comportamento) mas continua cancelado: o lote é
    uma foto do cadastro, não uma declaração de que todo mundo ali está ativo.
    Reativar é `upsert_um(..., reativar=True)`, um cliente por vez.
    """
    if not clientes:
        return 0
    agora = _agora()
    with _conectar() as conn:
        for c in clientes:
            conn.executar(_UPSERT, _params_upsert(tenant_id, c, agora))
    return len(clientes)


def upsert_um(tenant_id: str, cliente: dict, reativar: bool = False) -> dict:
    """Upsert de UM cliente já validado. Devolve a linha como ficou.

    Mesmo `_UPSERT` do lote: existe → atualiza a foto; não existe → cria.
    Não ressuscita quem cancelou — `cancelado_em` fica como está. Com
    `reativar=True`, e só assim, limpa `cancelado_em` e `motivo_cancelamento`
    na mesma transação; é o `POST /clientes` com esse campo no corpo que pede
    isso. Se o cliente não estava cancelado, `reativar` não muda nada.
    """
    agora = _agora()
    with _conectar() as conn:
        conn.executar(_UPSERT, _params_upsert(tenant_id, cliente, agora))
        if reativar:
            conn.executar(
                f"UPDATE {TABELA} SET cancelado_em = NULL, motivo_cancelamento = NULL "
                "WHERE tenant_id = ? AND customer_id_externo = ?",
                (tenant_id, cliente["customer_id_externo"]))
        return _buscar(conn, tenant_id, cliente["customer_id_externo"])


def atualizar_parcial(tenant_id: str, customer_id_externo: str, campos: dict):
    """Muda SÓ o que veio em `campos`. Devolve a linha como ficou; `None` se
    o cliente não existe neste tenant.

    Campo ausente do dicionário fica como está — mudar o MRR não zera
    `days_since_last` nem `features_used_30d`. Campo presente com `None`
    limpa (é o jeito de dizer "não sei mais o comportamento"). Só aceita
    `COLUNAS_PARCIAIS`; qualquer outra chave é ValueError, porque identidade,
    carimbos e cancelamento têm operação própria. Os VALORES chegam já
    validados por `importacao.validar_linha` — este módulo não valida, de
    propósito, para não existir um segundo vocabulário de validação.

    Carimba `atualizado_em`. `campos` vazio não toca na linha (nem no
    carimbo) e só a devolve.
    """
    desconhecidos = set(campos) - set(COLUNAS_PARCIAIS)
    if desconhecidos:
        raise ValueError(
            f"atualizar_parcial não aceita {sorted(desconhecidos)}; "
            f"aceita {list(COLUNAS_PARCIAIS)}")
    with _conectar() as conn:
        if _buscar(conn, tenant_id, customer_id_externo) is None:
            return None
        if campos:
            colunas = [c for c in COLUNAS_PARCIAIS if c in campos]
            valores = [float(campos[c]) if c == "mrr" and campos[c] is not None
                       else campos[c] for c in colunas]
            conn.executar(
                f"UPDATE {TABELA} SET "
                + ", ".join(f"{c} = ?" for c in colunas)
                + ", atualizado_em = ? WHERE tenant_id = ? AND customer_id_externo = ?",
                (*valores, _agora(), tenant_id, customer_id_externo))
        return _buscar(conn, tenant_id, customer_id_externo)


def cancelar(tenant_id: str, customer_id_externo: str, motivo=None):
    """Registra que o cliente cancelou. SOFT DELETE: a linha fica.

    Grava `cancelado_em` (agora, UTC) e `motivo_cancelamento` (truncado em
    `MOTIVO_CANCELAMENTO_MAX`; pode conter dado pessoal — não logar). Devolve
    a linha como ficou; `None` se o cliente não existe neste tenant.

    Idempotente e preserva a PRIMEIRA data: cancelar quem já está cancelado
    não muda `cancelado_em` nem `motivo_cancelamento` — a primeira data é a
    verdadeira, e é ela que vira rótulo. O cliente sai do ranking de risco
    pelo padrão de `listar()`, sem ninguém precisar filtrar; o histórico
    continua em `listar(..., incluir_cancelados=True)`.
    """
    if motivo is not None:
        motivo = str(motivo)[:MOTIVO_CANCELAMENTO_MAX]
    with _conectar() as conn:
        linha = _buscar(conn, tenant_id, customer_id_externo)
        if linha is None:
            return None
        if linha["cancelado_em"] is None:
            conn.executar(
                f"UPDATE {TABELA} SET cancelado_em = ?, motivo_cancelamento = ? "
                "WHERE tenant_id = ? AND customer_id_externo = ?",
                (_agora(), motivo, tenant_id, customer_id_externo))
            linha = _buscar(conn, tenant_id, customer_id_externo)
        return linha


def apagar_tenant(tenant_id: str) -> int:
    """Apaga TODOS os clientes importados deste tenant. Devolve quantos saíram.

    Existe para a rota de demonstração `/simulate/painel/importar`, que grava
    sempre no mesmo tenant fixo: sem isto, cada base de exemplo enviada se
    somava à anterior (diário + mensal + saudável = 1.496 clientes, e a base
    saudável saía com dezenas de críticos). A rota autenticada
    `/clientes/importar` NÃO chama isto: lá o upsert acumular é o
    comportamento correto — a planilha nova atualiza, não substitui.

    Mesmo SQL em Postgres e SQLite, como o resto do módulo. O filtro por
    tenant é obrigatório: não há apagar sem ele, de propósito.
    """
    with _conectar() as conn:
        antes = conn.executar(
            f"SELECT COUNT(*) AS n FROM {TABELA} WHERE tenant_id = ?", (tenant_id,))
        conn.executar(f"DELETE FROM {TABELA} WHERE tenant_id = ?", (tenant_id,))
    return int(antes[0]["n"]) if antes else 0


# ── Leitura ───────────────────────────────────────────────────────────────

def listar(tenant_id: str, incluir_cancelados: bool = False) -> list[dict]:
    """Os clientes importados DESTE tenant. Nunca de outro.

    Por padrão EXCLUI quem cancelou (`cancelado_em` preenchido): é isso que
    tira o cliente cancelado do ranking de risco sem que `batch_scoring`,
    `insights_unificados` ou o painel precisem lembrar de filtrar — não faz
    sentido tentar reter quem já saiu. `incluir_cancelados=True` traz tudo,
    para histórico e para o treino com desfecho observado.

    O filtro por tenant é a única cerca entre as empresas dentro da tabela;
    não existe leitura sem ele, de propósito.
    """
    filtro = "" if incluir_cancelados else " AND cancelado_em IS NULL"
    with _conectar() as conn:
        return conn.executar(
            f"SELECT {', '.join(COLUNAS)} FROM {TABELA} WHERE tenant_id = ?"
            f"{filtro} ORDER BY customer_id_externo",
            (tenant_id,),
        )


def obter(tenant_id: str, customer_id_externo: str):
    """Um cliente DESTE tenant, cancelado ou não; `None` se não existe aqui.

    Cliente de outro tenant é `None` igual a cliente inexistente — quem chama
    não consegue distinguir os dois, e é assim que a rota devolve 404 nos
    dois casos em vez de um 403 que confirmaria a existência.
    """
    with _conectar() as conn:
        return _buscar(conn, tenant_id, customer_id_externo)


def contar(tenant_id: str, incluir_cancelados: bool = False) -> int:
    """Quantos clientes DESTE tenant. Mesmo padrão de `listar()`: sem os
    cancelados, a não ser com `incluir_cancelados=True`.

    Os dois descrevem a mesma coisa para quem usa o produto — um tenant que
    importa 1.000 e cancela 50 tem que ver 950 nos dois, não 950 num e 1.000
    no outro.
    """
    filtro = "" if incluir_cancelados else " AND cancelado_em IS NULL"
    with _conectar() as conn:
        linhas = conn.executar(
            f"SELECT COUNT(*) AS n FROM {TABELA} WHERE tenant_id = ?{filtro}",
            (tenant_id,))
        return int(linhas[0]["n"]) if linhas else 0

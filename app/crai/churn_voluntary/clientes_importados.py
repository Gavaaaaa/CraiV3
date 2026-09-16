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
de telefone, CPF ou nome.
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
    PRIMARY KEY (tenant_id, customer_id_externo)
);
"""

COLUNAS = ("tenant_id", "customer_id_externo", "mrr", "billing_profile",
           "days_since_last", "features_used_30d", "email", "importado_em")


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

    # DDL idempotente, uma vez por processo por destino. No Supabase a tabela
    # também pode ser criada pelo painel (o DDL é `SCHEMA_SQL`, ver README);
    # aqui é só a garantia de que a primeira importação não falha por falta dela.
    with _schema_garantido_lock:
        if alvo not in _schema_garantido:
            conn.executar(SCHEMA_SQL)
            conn.commit()
            _schema_garantido.add(alvo)
    return conn


def esquecer_schema_garantido() -> None:
    """Para os testes: cada `tmp_path` é um banco novo."""
    with _schema_garantido_lock:
        _schema_garantido.clear()


# ── Escrita ───────────────────────────────────────────────────────────────

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


def gravar(tenant_id: str, clientes: list[dict]) -> int:
    """Upsert de linhas JÁ VALIDADAS (ver `importacao.py`). Devolve quantas.

    Uma transação para o lote inteiro: ou a base nova entra toda, ou nada
    muda. Meio lote gravado seria a pior das fotos — metade de ontem, metade
    de hoje, sem como saber qual metade.
    """
    if not clientes:
        return 0
    agora = _agora()
    with _conectar() as conn:
        for c in clientes:
            conn.executar(_UPSERT, (
                tenant_id, c["customer_id_externo"], float(c["mrr"]),
                c["billing_profile"], c.get("days_since_last"),
                c.get("features_used_30d"), c.get("email"), agora,
            ))
    return len(clientes)


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

def listar(tenant_id: str) -> list[dict]:
    """Todos os clientes importados DESTE tenant. Nunca de outro.

    O filtro por tenant é a única cerca entre as empresas dentro da tabela;
    não existe leitura sem ele, de propósito.
    """
    with _conectar() as conn:
        return conn.executar(
            f"SELECT {', '.join(COLUNAS)} FROM {TABELA} WHERE tenant_id = ? "
            "ORDER BY customer_id_externo",
            (tenant_id,),
        )


def contar(tenant_id: str) -> int:
    with _conectar() as conn:
        linhas = conn.executar(
            f"SELECT COUNT(*) AS n FROM {TABELA} WHERE tenant_id = ?", (tenant_id,))
        return int(linhas[0]["n"]) if linhas else 0

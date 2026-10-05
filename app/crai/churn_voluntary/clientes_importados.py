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

PII: `email`, `telefone` e `nome` são opcionais. Desde a Etapa 2 os três são
os CONTATOS do cliente final para a mensagem do involuntário: lidos daqui na
hora do envio e nunca copiados para o ciclo, a tentativa ou a tabela de
mensagens (do nome, só o primeiro entra no texto). Nada de CPF.
`motivo_cancelamento` é texto livre vindo do backend do cliente e PODE conter
dado pessoal: não entra em log nem em resposta de listagem agregada — só na
resposta da própria operação de cancelar.

AS 12 COLUNAS DA ETAPA 2 (todas opcionais; ausente = NULL, nunca 0):
`id_recorrencia` (liga a cobrança do Pix Automático a este cadastro; única por
tenant), as 9 de comportamento que o modelo v3 do voluntário conhece
(`logins_7d`, `logins_30d`, `avg_session_min`, `api_calls_7d`, `tickets_30d`,
`failed_pay_90d`, `nps_last`, `seats`, `tenure_days`) e os contatos `telefone` e
`nome`. Este módulo guarda; a validação é de `importacao.validar_linha`.

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
    id_recorrencia      TEXT,
    logins_7d           BIGINT,
    logins_30d          BIGINT,
    avg_session_min     DOUBLE PRECISION,
    api_calls_7d        BIGINT,
    tickets_30d         BIGINT,
    failed_pay_90d      BIGINT,
    nps_last            DOUBLE PRECISION,
    seats               BIGINT,
    tenure_days         BIGINT,
    telefone            TEXT,
    nome                TEXT,
    PRIMARY KEY (tenant_id, customer_id_externo)
);
"""

# A MARCA "NÃO CONTATAR" (Rodada 3, Fase 6). Uma linha por cliente que pediu
# para não receber mensagens, na mesma base (o mesmo destino: Postgres em
# produção, SQLite em teste), com a mesma chave do cliente. Fica numa tabela
# própria, e não numa coluna de `clientes_importados`, por três motivos: a
# marca não entra na foto que a planilha e a API regravam; ela vale também
# para o cliente que o sistema só conhece por evento (sem linha na base); e a
# tabela que já está em produção não muda. `origem`: quem marcou.
TABELA_NAO_CONTATAR = "clientes_nao_contatar"
NAO_CONTATAR_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABELA_NAO_CONTATAR} (
    tenant_id           TEXT NOT NULL,
    customer_id_externo TEXT NOT NULL,
    marcado_em          TEXT NOT NULL,
    origem              TEXT NOT NULL,
    PRIMARY KEY (tenant_id, customer_id_externo)
)
"""
ORIGEM_EMPRESA = "empresa"                  # a empresa marcou, pelo painel
ORIGEM_RESPOSTA_SAIR = "resposta_sair"      # o cliente respondeu SAIR a uma mensagem
ORIGEM_ANONIMIZACAO = "anonimizacao"        # os contatos dele foram apagados (art. 18)
ORIGENS_DO_NAO_CONTATAR = (ORIGEM_EMPRESA, ORIGEM_RESPOSTA_SAIR, ORIGEM_ANONIMIZACAO)
# Os campos de contato e de identificação direta que a anonimização apaga.
CAMPOS_DE_CONTATO = ("nome", "email", "telefone", "motivo_cancelamento")

# `id_recorrencia` é única por tenant. NULL não colide (SQLite e Postgres tratam
# dois NULL como distintos num índice único): cliente sem mapeamento é o normal.
INDICE_RECORRENCIA_SQL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS uq_cliente_recorrencia
    ON {TABELA} (tenant_id, id_recorrencia)
"""

# As 12 colunas da Etapa 2, na ordem do contrato (`docs/CONTRATO_CLIENTES_API.md`).
COMPORTAMENTO_V3 = ("logins_7d", "logins_30d", "avg_session_min", "api_calls_7d",
                    "tickets_30d", "failed_pay_90d", "nps_last", "seats", "tenure_days")
COLUNAS_ETAPA2 = ("id_recorrencia", *COMPORTAMENTO_V3, "telefone", "nome")

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
    ("id_recorrencia", "TEXT"),
    ("logins_7d", "BIGINT"),
    ("logins_30d", "BIGINT"),
    ("avg_session_min", "DOUBLE PRECISION"),
    ("api_calls_7d", "BIGINT"),
    ("tickets_30d", "BIGINT"),
    ("failed_pay_90d", "BIGINT"),
    ("nps_last", "DOUBLE PRECISION"),
    ("seats", "BIGINT"),
    ("tenure_days", "BIGINT"),
    ("telefone", "TEXT"),
    ("nome", "TEXT"),
)

MOTIVO_CANCELAMENTO_MAX = 500

COLUNAS = ("tenant_id", "customer_id_externo", "mrr", "billing_profile",
           "days_since_last", "features_used_30d", "email", "importado_em",
           "cancelado_em", "motivo_cancelamento", "atualizado_em", *COLUNAS_ETAPA2)

# O que `atualizar_parcial` aceita mudar. Fora daqui é ValueError: identidade
# (tenant, id), carimbos e o cancelamento têm operação própria, de propósito.
COLUNAS_PARCIAIS = ("mrr", "billing_profile", "days_since_last",
                    "features_used_30d", "email", *COLUNAS_ETAPA2)


class ConfiguracaoAusente(RuntimeError):
    """Nem `SUPABASE_DB_URL` nem `CRAI_CLIENTES_DB` configuradas."""


class ConflitoDeRecorrencia(ValueError):
    """O `id_recorrencia` já está ligado a OUTRO cliente deste tenant."""

    def __init__(self, id_recorrencia: str):
        self.id_recorrencia = id_recorrencia
        super().__init__(f"id_recorrencia {id_recorrencia!r} já pertence a outro cliente")


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
        conn.executar(INDICE_RECORRENCIA_SQL)
        conn.executar(NAO_CONTATAR_SQL)
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

# Quantas escritas ESTE processo fez na base de cada tenant. É a parte exata da
# marca que invalida o cache da régua (`marca_da_base`): duas escritas no mesmo
# segundo, que os carimbos de data não distinguem, mudam este número.
_escritas: dict = {}


def _marcar_escrita(tenant_id: str) -> None:
    _escritas[tenant_id] = _escritas.get(tenant_id, 0) + 1


class _conflito_de_recorrencia:
    """Traduz a violação de `uq_cliente_recorrencia` (a única restrição de
    unicidade que o upsert não resolve) em `ConflitoDeRecorrencia`. É a
    segunda linha de defesa: a validação de lote já recusa o conflito antes
    (`importacao.recusar_conflitos_de_recorrencia`); aqui fica a corrida entre
    duas importações simultâneas."""

    def __enter__(self):
        return self

    def __exit__(self, tipo, valor, tb):
        if tipo is not None and "IntegrityError" in {k.__name__ for k in tipo.__mro__}:
            raise ConflitoDeRecorrencia("(gravação simultânea)") from valor
        return False


# O `DO UPDATE SET` lista as colunas da foto (planilha/API) e SÓ elas. Ele
# não pode tocar em `cancelado_em`/`motivo_cancelamento`: se tocasse, subir a
# planilha de novo apagaria em silêncio o único desfecho observado que o
# sistema tem. Reativar é explícito (`upsert_um(..., reativar=True)`).
# Também não toca em `atualizado_em`, que é o carimbo do PATCH.
_COLUNAS_DO_UPSERT = ("mrr", "billing_profile", "days_since_last", "features_used_30d",
                      "email", *COLUNAS_ETAPA2)
_UPSERT = (
    f"INSERT INTO {TABELA} (tenant_id, customer_id_externo, "
    f"{', '.join(_COLUNAS_DO_UPSERT)}, importado_em) "
    f"VALUES ({', '.join('?' * (len(_COLUNAS_DO_UPSERT) + 3))}) "
    "ON CONFLICT (tenant_id, customer_id_externo) DO UPDATE SET "
    + ", ".join(f"{c} = excluded.{c}" for c in (*_COLUNAS_DO_UPSERT, "importado_em")))


def _params_upsert(tenant_id: str, c: dict, agora: str) -> tuple:
    valores = [float(c["mrr"])] + [c.get(col) for col in _COLUNAS_DO_UPSERT[1:]]
    return (tenant_id, c["customer_id_externo"], *valores, agora)


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
    with _conflito_de_recorrencia():
        with _conectar() as conn:
            for c in clientes:
                conn.executar(_UPSERT, _params_upsert(tenant_id, c, agora))
    _marcar_escrita(tenant_id)
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
    _marcar_escrita(tenant_id)
    with _conflito_de_recorrencia(), _conectar() as conn:
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
    _marcar_escrita(tenant_id)
    with _conflito_de_recorrencia(), _conectar() as conn:
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
    _marcar_escrita(tenant_id)
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


# ── Não contatar (Rodada 3, Fase 6) ───────────────────────────────────────

def marcar_nao_contatar(tenant_id: str, customer_id_externo: str, origem: str) -> dict:
    """Marca o cliente como "não contatar". Devolve `{marcado_em, origem}` como
    ficou. Idempotente, e preserva a PRIMEIRA marca: quem já está marcado
    continua com a data e a origem de antes. Não exige que o cliente esteja na
    base: quem chama decide (a rota do painel exige; a resposta SAIR, não)."""
    if origem not in ORIGENS_DO_NAO_CONTATAR:
        raise ValueError(f"origem desconhecida: {origem!r}")
    with _conectar() as conn:
        conn.executar(
            f"INSERT INTO {TABELA_NAO_CONTATAR} (tenant_id, customer_id_externo, marcado_em, origem) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (tenant_id, customer_id_externo) DO NOTHING",
            (tenant_id, str(customer_id_externo), _agora(), origem))
        linha = conn.executar(
            f"SELECT marcado_em, origem FROM {TABELA_NAO_CONTATAR} "
            "WHERE tenant_id = ? AND customer_id_externo = ?",
            (tenant_id, str(customer_id_externo)))[0]
    return {"marcado_em": linha["marcado_em"], "origem": linha["origem"]}


def desmarcar_nao_contatar(tenant_id: str, customer_id_externo: str) -> bool:
    """Tira a marca. True se havia marca; False se não havia."""
    with _conectar() as conn:
        havia = bool(conn.executar(
            f"SELECT 1 AS um FROM {TABELA_NAO_CONTATAR} "
            "WHERE tenant_id = ? AND customer_id_externo = ?",
            (tenant_id, str(customer_id_externo))))
        conn.executar(
            f"DELETE FROM {TABELA_NAO_CONTATAR} WHERE tenant_id = ? AND customer_id_externo = ?",
            (tenant_id, str(customer_id_externo)))
    return havia


def nao_contatar(tenant_id: str, customer_id_externo) -> dict | None:
    """`{marcado_em, origem}` se ESTE cliente DESTE tenant pediu para não ser
    contatado; None se não. É a leitura que a cadeia de canal faz na hora de
    escolher o canal e, de novo, na hora de enviar."""
    if customer_id_externo is None or str(customer_id_externo) == "":
        return None
    with _conectar() as conn:
        linhas = conn.executar(
            f"SELECT marcado_em, origem FROM {TABELA_NAO_CONTATAR} "
            "WHERE tenant_id = ? AND customer_id_externo = ?",
            (tenant_id, str(customer_id_externo)))
    return dict(linhas[0]) if linhas else None


def marcados_nao_contatar(tenant_id: str) -> dict:
    """{customer_id_externo: {marcado_em, origem}} de todos os marcados do tenant."""
    with _conectar() as conn:
        linhas = conn.executar(
            f"SELECT customer_id_externo, marcado_em, origem FROM {TABELA_NAO_CONTATAR} "
            "WHERE tenant_id = ?", (tenant_id,))
    return {l["customer_id_externo"]: {"marcado_em": l["marcado_em"], "origem": l["origem"]}
            for l in linhas}


def apagar_contatos(tenant_id: str, customer_id_externo: str):
    """ANONIMIZAÇÃO (art. 18): apaga o nome, o e-mail, o telefone e o motivo de
    cancelamento (texto livre) deste cliente. O resto da linha fica: o MRR, o
    perfil, o comportamento e os carimbos são o que as métricas agregadas
    usam. Devolve a lista dos campos que TINHAM valor e foram apagados; `None`
    se o cliente não existe neste tenant. Idempotente."""
    _marcar_escrita(tenant_id)
    with _conectar() as conn:
        linha = _buscar(conn, tenant_id, customer_id_externo)
        if linha is None:
            return None
        apagados = [c for c in CAMPOS_DE_CONTATO if linha.get(c) not in (None, "")]
        conn.executar(
            f"UPDATE {TABELA} SET {', '.join(f'{c} = NULL' for c in CAMPOS_DE_CONTATO)} "
            "WHERE tenant_id = ? AND customer_id_externo = ?",
            (tenant_id, customer_id_externo))
    return apagados


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
    _marcar_escrita(tenant_id)
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


def obter_por_recorrencia(tenant_id: str, id_recorrencia: str):
    """O cliente DESTE tenant ligado a este `id_recorrencia`, ou `None`.

    É por aqui que o involuntário lê o contato do cliente NA HORA do envio
    (Etapa 2): o contato nunca é copiado para o ciclo nem para as mensagens."""
    if not id_recorrencia:
        return None
    with _conectar() as conn:
        linhas = conn.executar(
            f"SELECT {', '.join(COLUNAS)} FROM {TABELA} "
            "WHERE tenant_id = ? AND id_recorrencia = ?", (tenant_id, str(id_recorrencia)))
        return linhas[0] if linhas else None


def nomes_por_recorrencia(tenant_id: str, ids_recorrencia: list) -> dict:
    """{id_recorrencia: nome} dos clientes DESTE tenant, numa consulta só —
    para a listagem do dashboard. Sem mapeamento, a chave não aparece."""
    ids = sorted({str(i) for i in ids_recorrencia if i})
    if not ids:
        return {}
    with _conectar() as conn:
        linhas = conn.executar(
            f"SELECT id_recorrencia, nome FROM {TABELA} WHERE tenant_id = ? "
            f"AND id_recorrencia IN ({', '.join('?' * len(ids))})", (tenant_id, *ids))
    return {l["id_recorrencia"]: l["nome"] for l in linhas}


def donos_de_recorrencia(tenant_id: str, ids_recorrencia: list) -> dict:
    """{id_recorrencia: customer_id_externo} já gravados DESTE tenant."""
    ids = sorted({str(i) for i in ids_recorrencia if i})
    if not ids:
        return {}
    with _conectar() as conn:
        linhas = conn.executar(
            f"SELECT id_recorrencia, customer_id_externo FROM {TABELA} WHERE tenant_id = ? "
            f"AND id_recorrencia IN ({', '.join('?' * len(ids))})", (tenant_id, *ids))
    return {l["id_recorrencia"]: l["customer_id_externo"] for l in linhas}


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


# ── Leituras do dashboard do voluntário (Rodada 3) ────────────────────────

def marca_da_base(tenant_id: str) -> tuple:
    """Uma marca que MUDA a cada escrita na base deste tenant. É o que invalida
    o cache da régua (`insights_unificados.painel_de_risco`).

    Duas partes: o contador de escritas DESTE processo (exato) e uma foto barata
    da tabela (quantas linhas, quantos cancelados e os carimbos mais recentes),
    que pega a escrita feita por OUTRO processo. Os carimbos têm resolução de
    segundo: a escrita de outro processo que não mude nenhuma contagem e caia no
    mesmo segundo da anterior só é vista na escrita seguinte. Limite declarado
    em `docs/LIMITACOES.md`.
    """
    with _conectar() as conn:
        linha = conn.executar(
            f"SELECT COUNT(*) AS n, COUNT(cancelado_em) AS cancelados, "
            f"MAX(importado_em) AS importado, MAX(atualizado_em) AS atualizado, "
            f"MAX(cancelado_em) AS cancelado FROM {TABELA} WHERE tenant_id = ?",
            (tenant_id,))[0]
    return (_destino(), _escritas.get(tenant_id, 0), int(linha["n"] or 0),
            int(linha["cancelados"] or 0), linha["importado"], linha["atualizado"],
            linha["cancelado"])


def carimbos(tenant_id: str) -> dict:
    """{customer_id_externo: (importado_em, atualizado_em, nome)} dos clientes
    ATIVOS deste tenant, numa consulta só. Para a lista "atualizados mais
    recentemente" do dashboard: o nome é o que a lista de ciclos já mostra."""
    with _conectar() as conn:
        linhas = conn.executar(
            f"SELECT customer_id_externo, importado_em, atualizado_em, nome FROM {TABELA} "
            "WHERE tenant_id = ? AND cancelado_em IS NULL", (tenant_id,))
    return {l["customer_id_externo"]: (l["importado_em"], l["atualizado_em"], l["nome"])
            for l in linhas}


def obter_varios(tenant_id: str, ids: list) -> dict:
    """{customer_id_externo: linha} dos clientes DESTE tenant com estes ids,
    cancelados ou não, numa consulta por bloco. Id de outro tenant não aparece."""
    ids = sorted({str(i) for i in ids if i})
    achados: dict = {}
    if not ids:
        return achados
    with _conectar() as conn:
        for inicio in range(0, len(ids), 500):
            bloco = ids[inicio:inicio + 500]
            for l in conn.executar(
                    f"SELECT {', '.join(COLUNAS)} FROM {TABELA} WHERE tenant_id = ? "
                    f"AND customer_id_externo IN ({', '.join('?' * len(bloco))})",
                    (tenant_id, *bloco)):
                achados[l["customer_id_externo"]] = l
    return achados


def resumo(tenant_id: str) -> dict:
    """A base em três números, sem carregar as linhas: quantos clientes ativos,
    quantos deles têm dado de comportamento (`days_since_last` ou
    `features_used_30d`, o mínimo para o risco ser avaliado) e o carimbo da
    escrita mais recente (importação, alteração ou cancelamento)."""
    with _conectar() as conn:
        linha = conn.executar(
            f"SELECT COUNT(*) AS total, "
            f"SUM(CASE WHEN days_since_last IS NOT NULL OR features_used_30d IS NOT NULL "
            f"THEN 1 ELSE 0 END) AS com_dados FROM {TABELA} "
            "WHERE tenant_id = ? AND cancelado_em IS NULL", (tenant_id,))[0]
        marcas = conn.executar(
            f"SELECT MAX(importado_em) AS importado, MAX(atualizado_em) AS atualizado, "
            f"MAX(cancelado_em) AS cancelado FROM {TABELA} WHERE tenant_id = ?",
            (tenant_id,))[0]
    carimbos_da_base = [m for m in (marcas["importado"], marcas["atualizado"],
                                    marcas["cancelado"]) if m]
    return {"total": int(linha["total"] or 0), "com_dados": int(linha["com_dados"] or 0),
            "atualizada_em": max(carimbos_da_base) if carimbos_da_base else None}

"""crai/dunning/ciclo_cobranca.py — o ciclo de cobrança como FONTE DA VERDADE.

POR QUE ESTE MÓDULO EXISTE (Etapa 1, Bloco 1). O diagnóstico de 28/09/2026
(`docs/interno/DIAGNOSTICO_INTEGRACAO.md`) mediu três defeitos no fluxo de
cobrança, e os três nascem do mesmo lugar: o estado do ciclo estava dividido
entre o checkpoint do LangGraph (RAM, um processo) e um JSON de planos que não
sabia o resultado de nenhuma tentativa. Depois de um reinício, um pagamento
confirmado era descartado (`sem_ciclo_aberto`, fee 0), uma nova falha reabria a
janela do BACEN, e a deduplicação de webhook — também em RAM — deixava passar
reenvios do PSP.

Este módulo é o dono de três tabelas, no mesmo arquivo do dataset do
involuntário (`recovery_cycles.db`, env `CRAI_RECOVERY_DB`):

    ciclos_cobranca       um ciclo por COBRANÇA (não por mandato): tenant,
                          recorrência, identidade da cobrança, valor, causa,
                          janela do BACEN presa à primeira falha, estado, fee e
                          a data de cada transição.
    tentativas_cobranca   uma linha por tentativa (1 a 3): agendada para,
                          disparada em, id devolvido pelo PSP, RESULTADO.
    eventos_vistos        a deduplicação de webhook, fora da RAM.

O GRAFO LÊ E GRAVA AQUI. O checkpoint do LangGraph deixa de carregar estado de
negócio entre eventos: contador, janela e desfecho vivem nestas tabelas e
sobrevivem a reinício e a dois processos no mesmo host.

O QUE O BANCO GARANTE, e não só o código (mesmo princípio do
`CHECK (offer_type <> 'consulta_cs')` do churn voluntário):

  - no máximo 3 tentativas por ciclo: `CHECK (numero BETWEEN 1 AND 3)` mais
    `UNIQUE (ciclo_id, numero)`. O literal `3` é gerado de `MAX_TENTATIVAS`,
    e um teste confere que os dois não divergem;
  - um ciclo por cobrança, sempre: `UNIQUE (tenant_id, id_cobranca_original)`.
    Um ciclo fechado da mesma cobrança não é reaberto — só cobrança NOVA abre
    janela nova (regra R3 do produto);
  - estados e resultados fechados por `CHECK`.

IDENTIDADE DA COBRANÇA (Bloco 0, 0.1, com o ajuste A1). Quem identifica a
cobrança de um ciclo é o id que o PSP dá a ela (`data.id` na Iugu, `txid` no
BACEN), que se mantém entre a falha original e as retentativas enquanto o
`e2e_id` muda a cada transação. Quando o evento não traz esse id, o ciclo usa
o e2e da falha que o abriu — como a migração do JSON já faz. Nunca fica vazio:
sem id e sem e2e, a chave é derivada do mandato e do instante, marcada como
tal, e o log diz.

PRAZO DE RECUPERAÇÃO ≠ JANELA DO BACEN (ajuste A3). A janela de 7 dias governa
SÓ as retentativas automáticas. Depois da mensagem, o cliente pode pagar pelo
meio oferecido, e isso fecha o ciclo como recuperado, com fee, mesmo com a
janela do BACEN encerrada. `PRAZO_RECUPERACAO_DIAS` é o horizonte em que uma
confirmação ainda fala deste ciclo, contado de `mensagem_em`.

TRANSAÇÃO. Toda escrita que muda estado abre `BEGIN IMMEDIATE` com o
`busy_timeout` declarado, no padrão de `retention_log.registrar_decisoes`: a
trava de escrita é tomada ANTES da leitura, então dois workers no mesmo
arquivo não abrem dois ciclos para a mesma cobrança nem disparam a mesma
tentativa duas vezes. O que isto NÃO cobre é dois workers em máquinas
diferentes, cada um com o seu arquivo — a mesma limitação já declarada para o
ledger de desfecho em `docs/LIMITACOES.md`. A correção é Postgres.

SEM DADO PESSOAL. `id_recorrencia` (mandato, opaco), o id de cobrança do PSP e
o `e2e_id` são o limite. Nada de chave Pix, CPF, e-mail ou nome — e há um
teste que abre o banco cru e procura.

SCHEMA GARANTIDO uma vez por processo por destino (`_garantir_schema`, padrão
de `clientes_importados`), com migração explícita de colunas acrescentadas
depois (`_COLUNAS_ACRESCENTADAS`, padrão de `recovery_log._migrar`) e
`esquecer_schema_garantido()` para os testes. A migração dos planos do
`pix_retry_state.json` roda no mesmo ponto, uma vez, e é idempotente.

TEMPO. Datas em ISO-8601 naive, no relógio local, como o `retry_state` sempre
gravou: é o relógio que o agendador compara (`tentativas_devidas`) e o que os
testes congelam. O `recovery_log`, na mesma base, grava UTC — são colunas de
tabelas diferentes, e nenhuma consulta as compara entre si.
"""

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .pix_automatico_retry import JANELA_DIAS, MAX_TENTATIVAS, fim_da_janela, inicio_da_janela

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "recovery_cycles.db"

# O mesmo arquivo e a mesma env do dataset do involuntário (`recovery_log`),
# de propósito: fechar um ciclo como recuperado é marcar a tentativa paga,
# cancelar as pendentes, gravar a fee E atualizar a linha do dataset — num
# arquivo só, isso é UMA transação; em dois, a segunda pode não acontecer.
ENV_CAMINHO = "CRAI_RECOVERY_DB"
TENANT_PADRAO = "default_tenant"

# Espelha `retention_log.BUSY_TIMEOUT_MS_PADRAO` sem importá-lo (um módulo de
# dunning não depende do pacote do outro churn por uma constante). Que os
# dois não divirjam é asserção de teste.
BUSY_TIMEOUT_MS = 5000

# ── Estados do ciclo ─────────────────────────────────────────────────────
RECOBRANDO = "recobrando"              # tentativas automáticas em curso (ou por decidir)
MENSAGEM_ENVIADA = "mensagem_enviada"  # a mensagem saiu; aguarda pagamento pelo meio oferecido
RECUPERADO = "recuperado"              # pagamento confirmado; fee contada
PERDIDO = "perdido"                    # prazo de recuperação vencido sem pagamento
DESCARTADO = "descartado"              # o sistema decidiu não agir — com motivo (R6)

ESTADOS = (RECOBRANDO, MENSAGEM_ENVIADA, RECUPERADO, PERDIDO, DESCARTADO)
ESTADOS_ABERTOS = frozenset({RECOBRANDO, MENSAGEM_ENVIADA})

# Transições permitidas. `recobrando` é o estado de abertura: o ciclo nasce
# antes do diagnóstico, então "descartado" e "mensagem_enviada" saem dele.
# Os três últimos são terminais.
TRANSICOES = {
    RECOBRANDO: frozenset({MENSAGEM_ENVIADA, RECUPERADO, DESCARTADO}),
    MENSAGEM_ENVIADA: frozenset({RECUPERADO, PERDIDO}),
    RECUPERADO: frozenset(),
    PERDIDO: frozenset(),
    DESCARTADO: frozenset(),
}
_COLUNA_DA_TRANSICAO = {
    MENSAGEM_ENVIADA: "mensagem_em",
    RECUPERADO: "recuperado_em",
    PERDIDO: "perdido_em",
    DESCARTADO: "descartado_em",
}

# ── Resultados de uma tentativa ──────────────────────────────────────────
PENDENTE = "pendente"        # agendada, ou disparada e ainda sem resultado
PAGA = "paga"
FALHOU = "falhou"
SEM_RETORNO = "sem_retorno"  # disparada há mais de PRAZO_SEM_RETORNO sem resultado do PSP
CANCELADA = "cancelada"      # não vai sair: ciclo recuperado, revogação ou janela encerrada

RESULTADOS = (PENDENTE, PAGA, FALHOU, SEM_RETORNO, CANCELADA)
# Conta como executada: saiu para o PSP, ou tem um resultado que só existe
# depois de sair. Uma `falhou` declarada (origem `declarada`) conta também:
# é o contador externo e autoritativo que o chamador afirmou.
RESULTADOS_EXECUTADOS = frozenset({PAGA, FALHOU, SEM_RETORNO})

# ── Origens ──────────────────────────────────────────────────────────────
ORIGEM_WEBHOOK = "webhook"
ORIGEM_PLANO = "plano"              # aberto por `retry_state.save_retry_state` sem ciclo prévio
ORIGEM_DECLARADA = "declarada"      # N tentativas executadas afirmadas pelo chamador (painel, Stripe)
ORIGEM_MIGRACAO = "migracao_json"   # veio do `pix_retry_state.json`
ORIGEM_RECOVERY_LOG = "recovery_log"  # ciclo de transição fechado pela linha aberta do dataset (D9/A4)

# Ajuste A3: quanto tempo, depois da MENSAGEM, uma confirmação ainda fecha o
# ciclo como recuperado. É separado da janela do BACEN porque mede outra
# coisa: a janela é o direito regulatório de reenviar instrução; o prazo é o
# tempo que um cliente leva para pagar por boleto ou Pix depois de ser
# avisado. 30 dias é o ciclo de faturamento mensal, que é o do produto
# (assinatura recorrente): um pagamento além disso já é da mensalidade
# seguinte, não a recuperação desta. Passado o prazo, o ciclo vai a
# `perdido` pela varredura do agendador e uma confirmação tardia não o
# reabre — fica registrada como confirmação sem ciclo.
PRAZO_RECUPERACAO_DIAS = 30

# B3-a: quanto tempo uma reserva de mensagem (`mensagem_enviada` sem
# `mensagem_confirmada_em`) pode ficar sem confirmação antes de ser tratada
# como processo morto entre a reserva e o envio. O envio é uma chamada de
# LLM com fallback local: leva segundos. Quinze minutos é uma ordem de
# grandeza acima de qualquer envio legítimo e uma ordem abaixo do intervalo
# em que a demora vira prejuízo.
PRAZO_RESERVA_DE_MENSAGEM = timedelta(minutes=15)

# Decisão D6: tentativa disparada há mais de 24 h sem resultado do PSP vira
# `sem_retorno` — conta como não paga para a sequência e fica registrada
# diferente de `falhou`. As janelas de liquidação do Pix Automático são
# intradiárias; um dia inteiro sem retorno não é atraso, é ausência.
PRAZO_SEM_RETORNO = timedelta(hours=24)

# Horizonte de deduplicação de webhook: o mesmo TTL de `idempotencia.TTL_PADRAO`,
# a janela do BACEN. Recebido como parâmetro (`registrar_evento_se_novo`), não
# repetido aqui como constante.


class CicloJaExiste(ValueError):
    """A `UNIQUE (tenant_id, id_cobranca_original)` recusou um segundo ciclo."""

    def __init__(self, tenant_id: str, id_cobranca_original: str, ciclo_id: Optional[int]):
        self.tenant_id = tenant_id
        self.id_cobranca_original = id_cobranca_original
        self.ciclo_id = ciclo_id
        super().__init__(f"já existe ciclo {ciclo_id} para a cobrança "
                         f"{id_cobranca_original!r} do tenant {tenant_id!r}")


class LimiteDeTentativas(ValueError):
    """O banco recusou uma tentativa fora de 1..MAX_TENTATIVAS ou repetida."""


class TransicaoInvalida(ValueError):
    """Transição de estado que `TRANSICOES` não permite."""


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS ciclos_cobranca (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id             TEXT    NOT NULL,
    id_recorrencia        TEXT    NOT NULL,
    id_cobranca_original  TEXT    NOT NULL,
    e2e_falha_original    TEXT,
    valor                 REAL    NOT NULL,
    causa_original        TEXT    NOT NULL,
    codigo_falha_original TEXT,
    janela_inicio         TEXT    NOT NULL,
    janela_fim            TEXT    NOT NULL,
    estado                TEXT    NOT NULL
        CHECK (estado IN ({", ".join(repr(e) for e in ESTADOS)})),
    estrategia            TEXT,
    motivo_descarte       TEXT,
    recovery_score        REAL,
    p_recovery            REAL,
    eprofit               REAL,
    fee                   REAL    NOT NULL DEFAULT 0,
    origem                TEXT    NOT NULL DEFAULT '{ORIGEM_WEBHOOK}',
    aberto_em             TEXT    NOT NULL,
    mensagem_em           TEXT,
    recuperado_em         TEXT,
    perdido_em            TEXT,
    descartado_em         TEXT,
    atualizado_em         TEXT    NOT NULL,
    decidido_em           TEXT,
    mensagem_confirmada_em TEXT,
    UNIQUE (tenant_id, id_cobranca_original)
);
CREATE INDEX IF NOT EXISTS idx_ciclo_mandato
    ON ciclos_cobranca (tenant_id, id_recorrencia, estado);

CREATE TABLE IF NOT EXISTS tentativas_cobranca (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ciclo_id            INTEGER NOT NULL REFERENCES ciclos_cobranca(id),
    numero              INTEGER NOT NULL CHECK (numero BETWEEN 1 AND {MAX_TENTATIVAS}),
    agendada_para       TEXT    NOT NULL,
    origem_data         TEXT,
    valor               REAL    NOT NULL,
    disparada_em        TEXT,
    id_cobranca         TEXT,
    resultado           TEXT    NOT NULL DEFAULT '{PENDENTE}'
        CHECK (resultado IN ({", ".join(repr(r) for r in RESULTADOS)})),
    codigo_resultado    TEXT,
    e2e_resultado       TEXT,
    motivo_cancelamento TEXT,
    resultado_em        TEXT,
    UNIQUE (ciclo_id, numero)
);
CREATE INDEX IF NOT EXISTS idx_tentativa_id_cobranca
    ON tentativas_cobranca (id_cobranca);

CREATE TABLE IF NOT EXISTS eventos_vistos (
    escopo   TEXT NOT NULL,
    chave    TEXT NOT NULL,
    visto_em TEXT NOT NULL,
    PRIMARY KEY (escopo, chave)
);
CREATE INDEX IF NOT EXISTS idx_eventos_vistos_ttl
    ON eventos_vistos (escopo, visto_em);
"""

# Colunas acrescentadas DEPOIS do schema original, com o tipo: um banco criado
# antes de uma coluna chega ao mesmo schema de um criado depois —
# `CREATE TABLE IF NOT EXISTS` não altera tabela existente. Formato:
# (tabela, coluna, tipo). A coluna também está no `_SCHEMA`, no FIM da tabela,
# para que `table_info` de um banco novo e de um migrado sejam iguais.
#
#   decidido_em  (Bloco 3, I-4a) — o instante em que a DECISÃO do ciclo foi
#                gravada (estratégia, score, e-Profit, na mesma escrita). Um
#                ciclo `recobrando` sem `decidido_em` e sem tentativas é um
#                ciclo INCOMPLETO: o processo caiu entre abrir e decidir, e o
#                reenvio do PSP retoma o diagnóstico nele (ver `ciclo_incompleto`).
#   mensagem_confirmada_em (Bloco 3, B3-a) — o instante em que o ENVIO da
#                mensagem retornou. `mensagem_em` é a RESERVA (o ciclo vai a
#                `mensagem_enviada` antes de enviar, para dois processos não
#                mandarem duas); esta coluna é a prova de que a mensagem saiu.
#                Reserva sem confirmação há mais de PRAZO_RESERVA_DE_MENSAGEM é
#                um processo que morreu no meio: a varredura desfaz a reserva
#                e a passagem reenvia. O prazo de recuperação (A3) conta daqui.
_COLUNAS_ACRESCENTADAS: tuple = (("ciclos_cobranca", "decidido_em", "TEXT"),
                                 ("ciclos_cobranca", "mensagem_confirmada_em", "TEXT"))

# Campos de `ciclos_cobranca` que `atualizar`/`transicionar` aceitam por nome.
_CAMPOS_ATUALIZAVEIS = frozenset({
    "estrategia", "motivo_descarte", "recovery_score", "p_recovery", "eprofit",
    "fee", "causa_original", "codigo_falha_original", "e2e_falha_original",
    "decidido_em", "mensagem_confirmada_em",
})

_schema_garantido_lock = threading.Lock()
_schema_garantido: set = set()


# ── Infra ────────────────────────────────────────────────────────────────

def caminho_do_banco() -> Path:
    """Lido a cada chamada para o teste poder redirecionar via env."""
    override = os.getenv(ENV_CAMINHO)
    return Path(override) if override else DB_PATH


def _agora_texto(agora: Optional[datetime] = None) -> str:
    return (agora or datetime.now()).isoformat()


def _iso(valor) -> Optional[str]:
    if isinstance(valor, datetime):
        return valor.isoformat()
    return str(valor) if valor is not None else None


def _data(valor) -> Optional[datetime]:
    if isinstance(valor, datetime):
        return valor
    if not valor:
        return None
    try:
        return datetime.fromisoformat(str(valor))
    except ValueError:
        return None


def _conectar() -> sqlite3.Connection:
    caminho = caminho_do_banco()
    caminho.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    # O SQLite nasce com chaves estrangeiras DESLIGADAS. Sem isto,
    # `tentativas_cobranca.ciclo_id` aceitaria um ciclo que não existe.
    conn.execute("PRAGMA foreign_keys = ON")
    _garantir_schema(conn, str(caminho))
    return conn


def _garantir_schema(conn: sqlite3.Connection, alvo: str) -> None:
    """DDL idempotente + migração de colunas + migração do JSON, uma vez por
    processo por destino."""
    with _schema_garantido_lock:
        if alvo in _schema_garantido:
            return
        conn.executescript(_SCHEMA)
        _migrar_colunas(conn)
        conn.commit()
        _schema_garantido.add(alvo)
    # Fora da trava do schema: a migração abre a própria transação e pode
    # chamar de volta o módulo (via `retry_state`) sem deadlock.
    try:
        _migrar_json(conn)
    except Exception as e:                       # noqa: BLE001 — best effort declarado
        logger.warning("[CICLO] Migração do pix_retry_state.json falhou: %s — "
                       "os planos antigos ficam onde estavam; nenhum novo é afetado.", e)


def _migrar_colunas(conn: sqlite3.Connection) -> None:
    for tabela, coluna, tipo in _COLUNAS_ACRESCENTADAS:
        existentes = {linha[1] for linha in conn.execute(f"PRAGMA table_info({tabela})")}
        if coluna not in existentes:
            conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")


def esquecer_schema_garantido() -> None:
    """Para os testes: cada `tmp_path` é um banco novo."""
    with _schema_garantido_lock:
        _schema_garantido.clear()


def _linha(row) -> Optional[dict]:
    return dict(row) if row is not None else None


# ── Ciclos ───────────────────────────────────────────────────────────────

def identidade_da_cobranca(id_cobranca: Optional[str], e2e_falha_original: Optional[str],
                           id_recorrencia: str, agora: datetime) -> str:
    """A1: `id_cobranca_original` nunca vazio.

    Ordem: o id de cobrança do PSP; senão o e2e da falha que abriu o ciclo
    (o que a migração do JSON também usa); senão — evento sem nenhum dos
    dois — uma chave derivada do mandato e do instante, marcada `anon:` e
    logada. Ela não colide com nada que um PSP mande e existe só para a
    `UNIQUE` não recusar um ciclo legítimo de um evento degradado.
    """
    if id_cobranca and str(id_cobranca).strip():
        return str(id_cobranca).strip()
    if e2e_falha_original and str(e2e_falha_original).strip():
        return str(e2e_falha_original).strip()
    derivada = f"anon:{id_recorrencia}:{agora.isoformat(timespec='seconds')}"
    logger.warning("[CICLO] Cobrança sem id_cobranca e sem e2e — identidade "
                   "derivada do mandato e do instante: %s", derivada)
    return derivada


def abrir_ciclo(
    tenant_id: Optional[str], id_recorrencia: str, valor: float, causa_original: str,
    agora: Optional[datetime] = None, *,
    id_cobranca: Optional[str] = None, e2e_falha_original: Optional[str] = None,
    codigo_falha: Optional[str] = None,
    janela_inicio: Optional[datetime] = None, janela_fim: Optional[datetime] = None,
    estado: str = RECOBRANDO, estrategia: Optional[str] = None,
    origem: str = ORIGEM_WEBHOOK,
    recovery_score=None, p_recovery=None, eprofit=None,
) -> dict:
    """Abre o ciclo de UMA cobrança. A janela do BACEN é gravada aqui e nunca
    mais (R3): `janela_inicio` é o vencimento âncora (hoje, o instante da
    primeira falha, porque o evento não traz vencimento) e `janela_fim` é
    `fim_da_janela` dele.

    Raises:
        CicloJaExiste: já há ciclo para `(tenant, id_cobranca_original)`. É a
            `UNIQUE` do banco falando, não uma consulta prévia.
    """
    agora = agora or datetime.now()
    tenant = tenant_id or TENANT_PADRAO
    if estado not in ESTADOS:
        raise TransicaoInvalida(f"estado de abertura inválido: {estado!r}")
    identidade = identidade_da_cobranca(id_cobranca, e2e_falha_original, id_recorrencia, agora)
    inicio = janela_inicio or agora
    fim = janela_fim or fim_da_janela(inicio)

    colunas = {
        "tenant_id": tenant,
        "id_recorrencia": str(id_recorrencia),
        "id_cobranca_original": identidade,
        "e2e_falha_original": _texto(e2e_falha_original),
        "valor": round(float(valor), 2),
        "causa_original": str(causa_original or "desconhecida"),
        "codigo_falha_original": _texto(codigo_falha),
        "janela_inicio": _iso(inicio),
        "janela_fim": _iso(fim),
        "estado": estado,
        "estrategia": _texto(estrategia),
        "recovery_score": _num(recovery_score),
        "p_recovery": _num(p_recovery),
        "eprofit": _num(eprofit),
        "origem": origem,
        "aberto_em": _iso(agora),
        "atualizado_em": _iso(agora),
    }
    coluna_data = _COLUNA_DA_TRANSICAO.get(estado)
    if coluna_data:
        colunas[coluna_data] = _iso(agora)

    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                f"INSERT INTO ciclos_cobranca ({', '.join(colunas)}) "
                f"VALUES ({', '.join('?' * len(colunas))})",
                tuple(colunas.values()))
        except sqlite3.IntegrityError:
            conn.rollback()
            existente = conn.execute(
                "SELECT id FROM ciclos_cobranca WHERE tenant_id = ? AND id_cobranca_original = ?",
                (tenant, identidade)).fetchone()
            raise CicloJaExiste(tenant, identidade, existente["id"] if existente else None)
        conn.commit()
        ciclo_id = cur.lastrowid
        return _linha(conn.execute("SELECT * FROM ciclos_cobranca WHERE id = ?",
                                   (ciclo_id,)).fetchone())
    finally:
        conn.close()


def ciclo_por_id(ciclo_id: int) -> Optional[dict]:
    conn = _conectar()
    try:
        return _linha(conn.execute("SELECT * FROM ciclos_cobranca WHERE id = ?",
                                   (ciclo_id,)).fetchone())
    finally:
        conn.close()


def ciclo_da_cobranca(tenant_id: Optional[str], id_cobranca_original: str) -> Optional[dict]:
    """O ciclo desta cobrança, em qualquer estado (há no máximo um)."""
    conn = _conectar()
    try:
        return _linha(conn.execute(
            "SELECT * FROM ciclos_cobranca WHERE tenant_id = ? AND id_cobranca_original = ?",
            (tenant_id or TENANT_PADRAO, str(id_cobranca_original))).fetchone())
    finally:
        conn.close()


def ciclo_aberto_do_mandato(tenant_id: Optional[str], id_recorrencia: str,
                            estados=ESTADOS_ABERTOS) -> Optional[dict]:
    """O ciclo aberto mais recente deste mandato, ou None."""
    conn = _conectar()
    try:
        marcas = ", ".join("?" * len(estados))
        return _linha(conn.execute(
            f"""SELECT * FROM ciclos_cobranca
                 WHERE tenant_id = ? AND id_recorrencia = ? AND estado IN ({marcas})
              ORDER BY id DESC LIMIT 1""",
            (tenant_id or TENANT_PADRAO, str(id_recorrencia), *estados)).fetchone())
    finally:
        conn.close()


def ciclos_do_mandato(tenant_id: Optional[str], id_recorrencia: str) -> list[dict]:
    """Todos os ciclos do mandato, mais recentes primeiro."""
    conn = _conectar()
    try:
        return [dict(l) for l in conn.execute(
            """SELECT * FROM ciclos_cobranca
                WHERE tenant_id = ? AND id_recorrencia = ? ORDER BY id DESC""",
            (tenant_id or TENANT_PADRAO, str(id_recorrencia)))]
    finally:
        conn.close()


def atualizar(ciclo_id: int, agora: Optional[datetime] = None, **campos) -> Optional[dict]:
    """Atualiza campos de diagnóstico/decisão do ciclo (não o estado)."""
    invalidos = set(campos) - _CAMPOS_ATUALIZAVEIS
    if invalidos:
        raise ValueError(f"campos não atualizáveis: {sorted(invalidos)}")
    if not campos:
        return ciclo_por_id(ciclo_id)
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        sets = ", ".join(f"{c} = ?" for c in campos)
        conn.execute(f"UPDATE ciclos_cobranca SET {sets}, atualizado_em = ? WHERE id = ?",
                     (*campos.values(), _agora_texto(agora), ciclo_id))
        conn.commit()
        return _linha(conn.execute("SELECT * FROM ciclos_cobranca WHERE id = ?",
                                   (ciclo_id,)).fetchone())
    finally:
        conn.close()


def transicionar(ciclo_id: int, novo_estado: str, agora: Optional[datetime] = None,
                 **campos) -> dict:
    """Muda o estado do ciclo, gravando a data da transição.

    Lê o estado atual e grava o novo sob a MESMA trava de escrita: dois
    workers que tentem fechar o mesmo ciclo veem, o segundo, o estado que o
    primeiro deixou — e `TransicaoInvalida` diz que não há o que fazer.
    """
    invalidos = set(campos) - _CAMPOS_ATUALIZAVEIS
    if invalidos:
        raise ValueError(f"campos não atualizáveis: {sorted(invalidos)}")
    if novo_estado not in ESTADOS:
        raise TransicaoInvalida(f"estado desconhecido: {novo_estado!r}")
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        atual = conn.execute("SELECT * FROM ciclos_cobranca WHERE id = ?",
                             (ciclo_id,)).fetchone()
        if atual is None:
            conn.rollback()
            raise TransicaoInvalida(f"ciclo {ciclo_id} não existe")
        if novo_estado not in TRANSICOES[atual["estado"]]:
            conn.rollback()
            raise TransicaoInvalida(
                f"ciclo {ciclo_id}: {atual['estado']} -> {novo_estado} não é permitido")
        marca = _agora_texto(agora)
        sets = {"estado": novo_estado, "atualizado_em": marca, **campos}
        coluna_data = _COLUNA_DA_TRANSICAO.get(novo_estado)
        if coluna_data:
            sets[coluna_data] = marca
        conn.execute(
            f"UPDATE ciclos_cobranca SET {', '.join(f'{c} = ?' for c in sets)} WHERE id = ?",
            (*sets.values(), ciclo_id))
        conn.commit()
        return _linha(conn.execute("SELECT * FROM ciclos_cobranca WHERE id = ?",
                                   (ciclo_id,)).fetchone())
    finally:
        conn.close()


def descartar(ciclo_id: int, motivo: str, agora: Optional[datetime] = None) -> dict:
    """R6: nenhum descarte é silencioso — o motivo fica na linha. Grava
    `decidido_em` na mesma escrita (I-4b): um ciclo descartado NUNCA é um
    ciclo incompleto, e não é retomado."""
    return transicionar(ciclo_id, DESCARTADO, agora, motivo_descarte=str(motivo),
                        decidido_em=_agora_texto(agora))


def ciclo_incompleto(ciclo: dict) -> bool:
    """I-4a: `recobrando`, SEM decisão gravada (`decidido_em`) e sem
    tentativas — o processo caiu entre `open_cycle` e `decide_recovery`.
    Critério explícito, e não "estratégia vazia"."""
    if ciclo["estado"] != RECOBRANDO or ciclo.get("decidido_em"):
        return False
    conn = _conectar()
    try:
        n = conn.execute("SELECT COUNT(*) FROM tentativas_cobranca WHERE ciclo_id = ?",
                         (ciclo["id"],)).fetchone()[0]
    finally:
        conn.close()
    return n == 0


# ── Tentativas ───────────────────────────────────────────────────────────

def agendar_tentativas(ciclo_id: int, tentativas: list[dict],
                       agora: Optional[datetime] = None) -> list[dict]:
    """Grava o plano de tentativas de um ciclo como PENDENTES (R2).

    Regravar o plano do mesmo ciclo é operação normal. O que NÃO acontece é a
    regravação apagar uma tentativa que já saiu ou já tem resultado: essas
    são preservadas por número; só as ainda pendentes e não disparadas têm
    data e valor atualizados.

    Raises:
        LimiteDeTentativas: número fora de 1..MAX_TENTATIVAS. É o `CHECK` do
            banco, não uma consulta prévia.
    """
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for t in tentativas:
                numero = t.get("numero")
                if numero is None:
                    raise LimiteDeTentativas("tentativa sem número")
                existente = conn.execute(
                    "SELECT * FROM tentativas_cobranca WHERE ciclo_id = ? AND numero = ?",
                    (ciclo_id, numero)).fetchone()
                if existente is not None:
                    if existente["disparada_em"] or existente["resultado"] != PENDENTE:
                        continue
                    conn.execute(
                        """UPDATE tentativas_cobranca
                              SET agendada_para = ?, origem_data = ?, valor = ?
                            WHERE id = ?""",
                        (_iso(t.get("quando")), _texto(t.get("origem")),
                         round(float(t.get("valor")), 2), existente["id"]))
                    continue
                conn.execute(
                    """INSERT INTO tentativas_cobranca
                           (ciclo_id, numero, agendada_para, origem_data, valor, resultado)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (ciclo_id, int(numero), _iso(t.get("quando")), _texto(t.get("origem")),
                     round(float(t.get("valor")), 2), PENDENTE))
            conn.execute("UPDATE ciclos_cobranca SET atualizado_em = ? WHERE id = ?",
                         (_agora_texto(agora), ciclo_id))
            conn.commit()
        except sqlite3.IntegrityError as e:
            conn.rollback()
            raise LimiteDeTentativas(
                f"ciclo {ciclo_id}: o banco recusou a tentativa ({e}) — o limite do "
                f"BACEN é {MAX_TENTATIVAS} por ciclo, numeradas 1..{MAX_TENTATIVAS}") from e
        return [dict(l) for l in conn.execute(
            "SELECT * FROM tentativas_cobranca WHERE ciclo_id = ? ORDER BY numero",
            (ciclo_id,))]
    finally:
        conn.close()


def tentativas_do_ciclo(ciclo_id: int) -> list[dict]:
    conn = _conectar()
    try:
        return [dict(l) for l in conn.execute(
            "SELECT * FROM tentativas_cobranca WHERE ciclo_id = ? ORDER BY numero",
            (ciclo_id,))]
    finally:
        conn.close()


def tentativas_executadas(ciclo_id: int) -> int:
    """Quantas tentativas deste ciclo CONTAM para o limite do BACEN (R2):
    as que saíram para o PSP, mais as que têm resultado de execução
    (inclusive `falhou` declarada pelo chamador)."""
    conn = _conectar()
    try:
        marcas = ", ".join("?" * len(RESULTADOS_EXECUTADOS))
        return int(conn.execute(
            f"""SELECT COUNT(*) FROM tentativas_cobranca
                 WHERE ciclo_id = ? AND (disparada_em IS NOT NULL OR resultado IN ({marcas}))""",
            (ciclo_id, *RESULTADOS_EXECUTADOS)).fetchone()[0])
    finally:
        conn.close()


def marcar_disparada(ciclo_id: int, numero: int, id_cobranca: str,
                     quando: Optional[datetime] = None) -> bool:
    """Registra que a tentativa saiu para o PSP. False se não havia tentativa
    pendente e não disparada com esse número — é a marca que impede o
    disparo duplo, tomada sob a trava de escrita."""
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            """UPDATE tentativas_cobranca
                  SET disparada_em = ?, id_cobranca = ?
                WHERE ciclo_id = ? AND numero = ? AND resultado = ? AND disparada_em IS NULL""",
            (_agora_texto(quando), _texto(id_cobranca), ciclo_id, int(numero), PENDENTE))
        conn.execute("UPDATE ciclos_cobranca SET atualizado_em = ? WHERE id = ?",
                     (_agora_texto(quando), ciclo_id))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def registrar_resultado(ciclo_id: int, numero: int, resultado: str,
                        agora: Optional[datetime] = None, codigo: Optional[str] = None,
                        e2e: Optional[str] = None) -> bool:
    """Grava o resultado de uma tentativa PENDENTE. False se ela não estava
    pendente (resultado já registrado, ou cancelada)."""
    if resultado not in (PAGA, FALHOU, SEM_RETORNO):
        raise ValueError(f"resultado inválido para uma tentativa executada: {resultado!r}")
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        marca = _agora_texto(agora)
        cur = conn.execute(
            """UPDATE tentativas_cobranca
                  SET resultado = ?, resultado_em = ?, codigo_resultado = ?, e2e_resultado = ?
                WHERE ciclo_id = ? AND numero = ? AND resultado = ?""",
            (resultado, marca, _texto(codigo), _texto(e2e), ciclo_id, int(numero), PENDENTE))
        conn.execute("UPDATE ciclos_cobranca SET atualizado_em = ? WHERE id = ?",
                     (marca, ciclo_id))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def cancelar_pendentes(ciclo_id: int, motivo: str, agora: Optional[datetime] = None) -> int:
    """Cancela as tentativas que ainda não saíram. As disparadas e sem
    resultado ficam como estão: a instrução já está no PSP, e cancelá-la no
    banco não a traria de volta. Devolve quantas foram canceladas."""
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        marca = _agora_texto(agora)
        cur = conn.execute(
            """UPDATE tentativas_cobranca
                  SET resultado = ?, resultado_em = ?, motivo_cancelamento = ?
                WHERE ciclo_id = ? AND resultado = ? AND disparada_em IS NULL""",
            (CANCELADA, marca, str(motivo), ciclo_id, PENDENTE))
        conn.execute("UPDATE ciclos_cobranca SET atualizado_em = ? WHERE id = ?",
                     (marca, ciclo_id))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def tentativa_por_id_cobranca(tenant_id: Optional[str], id_cobranca: str) -> Optional[dict]:
    """A tentativa que o PSP identificou por este id, com o ciclo junto
    (chaves `ciclo_*`). Mais recente primeiro se houver mais de uma."""
    if not id_cobranca:
        return None
    conn = _conectar()
    try:
        row = conn.execute(
            """SELECT t.*, c.tenant_id AS ciclo_tenant_id, c.id_recorrencia AS ciclo_id_recorrencia,
                      c.estado AS ciclo_estado
                 FROM tentativas_cobranca t JOIN ciclos_cobranca c ON c.id = t.ciclo_id
                WHERE c.tenant_id = ? AND t.id_cobranca = ?
             ORDER BY t.id DESC LIMIT 1""",
            (tenant_id or TENANT_PADRAO, str(id_cobranca))).fetchone()
        return _linha(row)
    finally:
        conn.close()


def ciclos_recobrando_com_pendentes() -> list[dict]:
    """Ciclos em `recobrando` com ao menos uma tentativa pendente ainda não
    disparada — o que o agendador percorre."""
    conn = _conectar()
    try:
        return [dict(l) for l in conn.execute(
            """SELECT c.* FROM ciclos_cobranca c
                WHERE c.estado = ?
                  AND EXISTS (SELECT 1 FROM tentativas_cobranca t
                               WHERE t.ciclo_id = c.id AND t.resultado = ?
                                 AND t.disparada_em IS NULL)
             ORDER BY c.id""", (RECOBRANDO, PENDENTE))]
    finally:
        conn.close()


def ciclo_mais_recente_com_tentativas(tenant_id: Optional[str], id_recorrencia: str) -> Optional[dict]:
    """O ciclo mais recente do mandato que tem plano gravado (qualquer estado)."""
    conn = _conectar()
    try:
        return _linha(conn.execute(
            """SELECT c.* FROM ciclos_cobranca c
                WHERE c.tenant_id = ? AND c.id_recorrencia = ?
                  AND EXISTS (SELECT 1 FROM tentativas_cobranca t WHERE t.ciclo_id = c.id)
             ORDER BY c.id DESC LIMIT 1""",
            (tenant_id or TENANT_PADRAO, str(id_recorrencia))).fetchone())
    finally:
        conn.close()


# ── Associação de um webhook de falha a um ciclo (Bloco 2, regra do 0.1) ──

# O que um webhook de `cobranca_falhada` É, para o ciclo:
NOVA = "nova"                                      # cobrança nova: abre ciclo e roda o grafo
RESULTADO_DE_TENTATIVA = "resultado_de_tentativa"  # a falha de uma retentativa já disparada
FALHA_TARDIA = "falha_tardia"                      # a mesma cobrança, de novo, sem tentativa em aberto


def tentativa_disparada_pendente_mais_recente(ciclo_id: int) -> Optional[dict]:
    """A tentativa mais recente que saiu para o PSP e ainda não tem resultado."""
    conn = _conectar()
    try:
        return _linha(conn.execute(
            """SELECT * FROM tentativas_cobranca
                WHERE ciclo_id = ? AND resultado = ? AND disparada_em IS NOT NULL
             ORDER BY numero DESC LIMIT 1""", (ciclo_id, PENDENTE)).fetchone())
    finally:
        conn.close()


def _resultado_ou_tardia(ciclo: dict) -> dict:
    # I-4a: ciclo incompleto (caiu entre abrir e decidir) → o evento é tratado
    # como cobrança NOVA sobre o MESMO ciclo: o diagnóstico é retomado, com a
    # janela original, sem ciclo novo. Um ciclo `descartado` não passa aqui:
    # `descartar` grava `decidido_em`.
    if ciclo_incompleto(ciclo):
        return {"tipo": NOVA, "ciclo": ciclo, "tentativa": None}
    tentativa = tentativa_disparada_pendente_mais_recente(ciclo["id"])
    if tentativa is not None:
        return {"tipo": RESULTADO_DE_TENTATIVA, "ciclo": ciclo, "tentativa": tentativa}
    return {"tipo": FALHA_TARDIA, "ciclo": ciclo, "tentativa": None}


def associar_falha(tenant_id: Optional[str], id_recorrencia: str, id_cobranca: Optional[str],
                   e2e_id: Optional[str], agora: datetime) -> dict:
    """A que ciclo pertence esta falha — ou se é cobrança nova (D1 + D2).

    Na ordem:
      1. `id_cobranca` igual ao id de uma TENTATIVA disparada → é o resultado
         dela (modelo Pagar.me/Iugu: cada reenvio é uma cobrança com id
         próprio). Se a tentativa já tem resultado, é reentrega: falha tardia.
      2. `id_cobranca` igual ao id da COBRANÇA de um ciclo → a mesma cobrança
         (modelo BACEN: `txid` mantido entre retentativas). Com tentativa
         disparada em aberto, é o resultado da mais recente; sem, é falha
         tardia — e nunca abre tentativa nova (R3).
      3. `id_cobranca` presente e desconhecido → cobrança NOVA, mesmo com
         outro ciclo aberto do mandato: o PSP disse que é outra cobrança.
      4. Sem `id_cobranca` (fallback D2): o ciclo cuja identidade é o e2e; senão
         o ciclo mais recente do mandato com a janela do BACEN VIGENTE que
         esteja `recobrando`, `mensagem_enviada` ou `descartado` — dentro da
         janela é a mesma cobrança, e uma falha tardia de um ciclo descartado
         não reabre o diagnóstico (I-4b). Um ciclo `recuperado` NUNCA absorve
         uma falha (B3-b): ela é cobrança nova. Sem nenhum dos dois, cobrança
         nova — a identidade dela vai ser o e2e (A1).

    Devolve `{"tipo", "ciclo", "tentativa"}`. `tipo == NOVA` com `ciclo`
    preenchido é a RETOMADA de um ciclo incompleto (I-4a); com `ciclo` None é
    cobrança nova de verdade.
    """
    tenant = tenant_id or TENANT_PADRAO
    if id_cobranca:
        tentativa = tentativa_por_id_cobranca(tenant, id_cobranca)
        if tentativa is not None:
            ciclo = ciclo_por_id(tentativa["ciclo_id"])
            if tentativa["resultado"] == PENDENTE and tentativa["disparada_em"]:
                return {"tipo": RESULTADO_DE_TENTATIVA, "ciclo": ciclo, "tentativa": tentativa}
            return {"tipo": FALHA_TARDIA, "ciclo": ciclo, "tentativa": tentativa}
        ciclo = ciclo_da_cobranca(tenant, id_cobranca)
        if ciclo is not None:
            return _resultado_ou_tardia(ciclo)
        return {"tipo": NOVA, "ciclo": None, "tentativa": None}

    if e2e_id:
        ciclo = ciclo_da_cobranca(tenant, e2e_id)
        if ciclo is not None:
            return _resultado_ou_tardia(ciclo)
    absorventes = (RECOBRANDO, MENSAGEM_ENVIADA, DESCARTADO)
    recente = ciclo_aberto_do_mandato(tenant, id_recorrencia, estados=absorventes)
    if recente is not None:
        fim = _data(recente["janela_fim"])
        if fim is not None and fim >= agora:
            return _resultado_ou_tardia(recente)
    return {"tipo": NOVA, "ciclo": None, "tentativa": None}


def confirmar_mensagem(ciclo_id: int, agora: Optional[datetime] = None) -> bool:
    """B3-a: o envio retornou — grava `mensagem_confirmada_em`. False se o ciclo
    não está `mensagem_enviada` (a reserva foi desfeita no meio)."""
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        marca = _agora_texto(agora)
        cur = conn.execute(
            """UPDATE ciclos_cobranca SET mensagem_confirmada_em = ?, atualizado_em = ?
                WHERE id = ? AND estado = ? AND mensagem_confirmada_em IS NULL""",
            (marca, marca, ciclo_id, MENSAGEM_ENVIADA))
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def desfazer_reserva_de_mensagem(ciclo_id: int, motivo: str,
                                 agora: Optional[datetime] = None) -> bool:
    """B3-a: a mensagem foi RESERVADA e não saiu (exceção no envio, ou processo
    morto). O ciclo volta a `recobrando` para a próxima passagem tentar de
    novo. É a única transição `mensagem_enviada → recobrando` que existe, e
    por isso é uma função própria, fora de `TRANSICOES`: só vale sem
    `mensagem_confirmada_em`, sob a trava de escrita, e loga em WARNING."""
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        marca = _agora_texto(agora)
        cur = conn.execute(
            """UPDATE ciclos_cobranca SET estado = ?, mensagem_em = NULL, atualizado_em = ?
                WHERE id = ? AND estado = ? AND mensagem_confirmada_em IS NULL""",
            (RECOBRANDO, marca, ciclo_id, MENSAGEM_ENVIADA))
        conn.commit()
        desfeita = cur.rowcount == 1
    finally:
        conn.close()
    if desfeita:
        logger.warning("[CICLO] ciclo %s: mensagem reservada e NÃO enviada (%s) — reserva "
                       "desfeita, o ciclo volta a recobrando e a próxima passagem reenvia.",
                       ciclo_id, motivo)
    return desfeita


def varrer_reservas_orfas(agora: datetime) -> list[int]:
    """B3-a: reservas de mensagem sem confirmação há mais de
    `PRAZO_RESERVA_DE_MENSAGEM` — o processo morreu entre reservar e enviar.
    Desfaz cada uma (volta a `recobrando`); a mesma passagem as reenvia, com
    a mesma reserva contra duplicata."""
    limite = _iso(agora - PRAZO_RESERVA_DE_MENSAGEM)
    conn = _conectar()
    try:
        ids = [l["id"] for l in conn.execute(
            """SELECT id FROM ciclos_cobranca
                WHERE estado = ? AND mensagem_confirmada_em IS NULL
                  AND mensagem_em IS NOT NULL AND mensagem_em <= ?""",
            (MENSAGEM_ENVIADA, limite))]
    finally:
        conn.close()
    return [c for c in ids if desfazer_reserva_de_mensagem(
        c, f"reserva sem confirmação há mais de {PRAZO_RESERVA_DE_MENSAGEM}", agora)]


def ciclos_precisando_de_mensagem(agora: datetime) -> list[int]:
    """Todos os ciclos `recobrando` que precisam de mensagem (R1, D7), pela
    mesma regra de `ciclo_precisa_de_mensagem`, mais o caso da estratégia
    `mensagem_pagamento` decidida e não enviada (processo morto antes de
    `trigger_dunning`). Um ciclo incompleto (sem decisão, sem tentativas)
    NÃO entra: ele é retomado pelo diagnóstico, não concluído."""
    conn = _conectar()
    try:
        return [l["id"] for l in conn.execute(
            """SELECT c.id FROM ciclos_cobranca c
                WHERE c.estado = ?
                  AND NOT EXISTS (SELECT 1 FROM tentativas_cobranca t
                                   WHERE t.ciclo_id = c.id AND t.resultado = ?)
                  AND (
                       EXISTS (SELECT 1 FROM tentativas_cobranca t
                                WHERE t.ciclo_id = c.id AND t.resultado IN (?, ?))
                    OR (c.janela_fim < ? AND EXISTS (SELECT 1 FROM tentativas_cobranca t
                                                      WHERE t.ciclo_id = c.id))
                    OR (c.estrategia = 'mensagem_pagamento' AND c.decidido_em IS NOT NULL)
                  )
             ORDER BY c.id""",
            (RECOBRANDO, PENDENTE, FALHOU, SEM_RETORNO, _iso(agora)))]
    finally:
        conn.close()


def ciclo_precisa_de_mensagem(ciclo_id: int, agora: datetime) -> bool:
    """R1: a mensagem nasce do RESULTADO — o ciclo está `recobrando`, nenhuma
    tentativa continua pendente, e ou alguma falhou/ficou sem retorno, ou a
    janela do BACEN encerrou (D7). Quem age sobre isto é o Bloco 3."""
    ciclo = ciclo_por_id(ciclo_id)
    if ciclo is None or ciclo["estado"] != RECOBRANDO:
        return False
    tentativas = tentativas_do_ciclo(ciclo_id)
    if any(t["resultado"] == PENDENTE for t in tentativas):
        return False
    if any(t["resultado"] in (FALHOU, SEM_RETORNO) for t in tentativas):
        return True
    fim = _data(ciclo["janela_fim"])
    return bool(tentativas) and fim is not None and fim < agora


# ── Varreduras do agendador (D6, D7, A3) ─────────────────────────────────

def varrer_sem_retorno(agora: datetime) -> list[tuple[int, int]]:
    """D6: tentativa disparada há mais de `PRAZO_SEM_RETORNO` sem resultado
    vira `sem_retorno`. Só em ciclos `recobrando`: num ciclo já fechado a
    tentativa em aberto não muda nada."""
    limite = _iso(agora - PRAZO_SEM_RETORNO)
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        alvos = [(l["ciclo_id"], l["numero"]) for l in conn.execute(
            """SELECT t.ciclo_id, t.numero FROM tentativas_cobranca t
                 JOIN ciclos_cobranca c ON c.id = t.ciclo_id
                WHERE t.resultado = ? AND t.disparada_em IS NOT NULL AND t.disparada_em <= ?
                  AND c.estado = ?""", (PENDENTE, limite, RECOBRANDO))]
        marca = _iso(agora)
        for ciclo_id, numero in alvos:
            conn.execute(
                """UPDATE tentativas_cobranca SET resultado = ?, resultado_em = ?
                    WHERE ciclo_id = ? AND numero = ? AND resultado = ?""",
                (SEM_RETORNO, marca, ciclo_id, numero, PENDENTE))
            conn.execute("UPDATE ciclos_cobranca SET atualizado_em = ? WHERE id = ?",
                         (marca, ciclo_id))
        conn.commit()
        return alvos
    finally:
        conn.close()


def varrer_janelas_encerradas(agora: datetime) -> list[int]:
    """D7: ciclo `recobrando` cuja janela do BACEN encerrou com tentativas
    ainda não disparadas — elas não podem mais sair. Cancela-as com motivo
    `janela_encerrada` e devolve os ciclos afetados."""
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ciclos = [l["id"] for l in conn.execute(
            """SELECT c.id FROM ciclos_cobranca c
                WHERE c.estado = ? AND c.janela_fim < ?
                  AND EXISTS (SELECT 1 FROM tentativas_cobranca t
                               WHERE t.ciclo_id = c.id AND t.resultado = ?
                                 AND t.disparada_em IS NULL)""",
            (RECOBRANDO, _iso(agora), PENDENTE))]
        marca = _iso(agora)
        for ciclo_id in ciclos:
            conn.execute(
                """UPDATE tentativas_cobranca
                      SET resultado = ?, resultado_em = ?, motivo_cancelamento = 'janela_encerrada'
                    WHERE ciclo_id = ? AND resultado = ? AND disparada_em IS NULL""",
                (CANCELADA, marca, ciclo_id, PENDENTE))
            conn.execute("UPDATE ciclos_cobranca SET atualizado_em = ? WHERE id = ?",
                         (marca, ciclo_id))
        conn.commit()
        return ciclos
    finally:
        conn.close()


def varrer_prazo_de_recuperacao(agora: datetime) -> list[int]:
    """A3: ciclo `mensagem_enviada` há mais de `PRAZO_RECUPERACAO_DIAS` vai a
    `perdido`. A janela do BACEN NÃO entra aqui: o prazo conta da mensagem —
    da mensagem CONFIRMADA (`mensagem_confirmada_em`, B3-a), não da reserva."""
    limite = _iso(agora - timedelta(days=PRAZO_RECUPERACAO_DIAS))
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ciclos = [l["id"] for l in conn.execute(
            """SELECT id FROM ciclos_cobranca
                WHERE estado = ? AND mensagem_confirmada_em IS NOT NULL
                  AND mensagem_confirmada_em < ?""",
            (MENSAGEM_ENVIADA, limite))]
        marca = _iso(agora)
        for ciclo_id in ciclos:
            conn.execute(
                """UPDATE ciclos_cobranca SET estado = ?, perdido_em = ?, atualizado_em = ?
                    WHERE id = ? AND estado = ?""",
                (PERDIDO, marca, marca, ciclo_id, MENSAGEM_ENVIADA))
        conn.commit()
        return ciclos
    finally:
        conn.close()


def varrer(agora: datetime) -> dict:
    """As varreduras, na ordem, mais TODOS os ciclos que precisam de mensagem
    — inclusive os que ficaram devendo de uma passagem anterior (envio que
    levantou, reserva órfã desfeita agora). É o que o agendador chama ao fim
    de cada passagem."""
    sem_retorno = varrer_sem_retorno(agora)
    janelas = varrer_janelas_encerradas(agora)
    orfas = varrer_reservas_orfas(agora)
    perdidos = varrer_prazo_de_recuperacao(agora)
    devidas = ciclos_precisando_de_mensagem(agora)
    return {"sem_retorno": sem_retorno, "janelas_encerradas": janelas,
            "reservas_orfas": orfas, "perdidos": perdidos, "mensagem_devida": devidas}


# ── Confirmação de pagamento (R4, A3) ────────────────────────────────────

def ciclo_para_confirmacao(tenant_id: Optional[str], id_recorrencia: str,
                           id_cobranca: Optional[str]) -> Optional[dict]:
    """Que ciclo uma confirmação de pagamento fecha.

    Pelo `id_cobranca` da confirmação (a tentativa que pagou, ou a cobrança);
    senão o ciclo mais recente do mandato que esteja `recobrando` ou
    `mensagem_enviada` — A3: depois da mensagem o cliente pode pagar pelo
    meio oferecido, com a janela do BACEN já fechada, e isso ainda é
    recuperação. Sem ciclo aberto, devolve o ciclo FECHADO mais recente do
    mandato, se houver, para o chamador dizer o que aconteceu (`perdido`,
    `recuperado`, `descartado`) em vez de "sem ciclo".
    """
    tenant = tenant_id or TENANT_PADRAO
    if id_cobranca:
        tentativa = tentativa_por_id_cobranca(tenant, id_cobranca)
        if tentativa is not None:
            return ciclo_por_id(tentativa["ciclo_id"])
        ciclo = ciclo_da_cobranca(tenant, id_cobranca)
        if ciclo is not None:
            return ciclo
    aberto = ciclo_aberto_do_mandato(tenant, id_recorrencia)
    if aberto is not None:
        return aberto
    ciclos = ciclos_do_mandato(tenant, id_recorrencia)
    if ciclos:
        return ciclos[0]
    # A confirmação declarou um tenant que não tem ciclo deste mandato: ou é
    # erro de integração do cliente, ou tentativa de atribuição indevida. O
    # ciclo aberto do mandato em QUALQUER tenant é devolvido, e quem chama
    # atribui a recuperação ao tenant DO CICLO (e loga a divergência) — a
    # mesma regra de antes da Etapa 1, quando o checkpoint era por mandato.
    conn = _conectar()
    try:
        marcas = ", ".join("?" * len(ESTADOS_ABERTOS))
        return _linha(conn.execute(
            f"""SELECT * FROM ciclos_cobranca
                 WHERE id_recorrencia = ? AND estado IN ({marcas})
              ORDER BY id DESC LIMIT 1""",
            (str(id_recorrencia), *ESTADOS_ABERTOS)).fetchone())
    finally:
        conn.close()


def fechar_como_recuperado(ciclo_id: int, fee: float, agora: Optional[datetime] = None,
                           id_cobranca: Optional[str] = None,
                           e2e_confirmacao: Optional[str] = None) -> dict:
    """R4, numa transação só: marca a tentativa que pagou (pelo `id_cobranca`
    da confirmação; senão a disparada mais recente sem resultado), cancela as
    não disparadas (`recuperado`) e leva o ciclo a `recuperado` com a fee.

    Raises:
        TransicaoInvalida: o ciclo não está em estado que aceite `recuperado`
            (já fechado). Lido e decidido sob a trava de escrita.
    """
    marca = _agora_texto(agora)
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ciclo = conn.execute("SELECT * FROM ciclos_cobranca WHERE id = ?", (ciclo_id,)).fetchone()
        if ciclo is None or RECUPERADO not in TRANSICOES[ciclo["estado"]]:
            conn.rollback()
            raise TransicaoInvalida(
                f"ciclo {ciclo_id}: {ciclo['estado'] if ciclo else 'inexistente'} -> recuperado "
                "não é permitido")
        paga = None
        if id_cobranca:
            paga = conn.execute(
                """SELECT numero FROM tentativas_cobranca
                    WHERE ciclo_id = ? AND id_cobranca = ? AND resultado = ?""",
                (ciclo_id, str(id_cobranca), PENDENTE)).fetchone()
        if paga is None:
            paga = conn.execute(
                """SELECT numero FROM tentativas_cobranca
                    WHERE ciclo_id = ? AND resultado = ? AND disparada_em IS NOT NULL
                 ORDER BY numero DESC LIMIT 1""", (ciclo_id, PENDENTE)).fetchone()
        numero_paga = paga["numero"] if paga else None
        if numero_paga is not None:
            conn.execute(
                """UPDATE tentativas_cobranca
                      SET resultado = ?, resultado_em = ?, e2e_resultado = ?
                    WHERE ciclo_id = ? AND numero = ?""",
                (PAGA, marca, _texto(e2e_confirmacao), ciclo_id, numero_paga))
        canceladas = conn.execute(
            """UPDATE tentativas_cobranca
                  SET resultado = ?, resultado_em = ?, motivo_cancelamento = 'recuperado'
                WHERE ciclo_id = ? AND resultado = ? AND disparada_em IS NULL""",
            (CANCELADA, marca, ciclo_id, PENDENTE)).rowcount
        conn.execute(
            """UPDATE ciclos_cobranca
                  SET estado = ?, fee = ?, recuperado_em = ?, atualizado_em = ?
                WHERE id = ?""",
            (RECUPERADO, round(float(fee), 2), marca, marca, ciclo_id))
        conn.commit()
        return {"ciclo_id": ciclo_id, "estado_anterior": ciclo["estado"],
                "tentativa_paga": numero_paga, "canceladas": canceladas,
                "fee": round(float(fee), 2)}
    finally:
        conn.close()


def ciclos_recuperados_desde(limite: datetime) -> list[dict]:
    """Ciclos `recuperado` com `recuperado_em >= limite` — o que a
    reconciliação com o dataset (B4-a) revisita a cada passagem."""
    conn = _conectar()
    try:
        return [dict(l) for l in conn.execute(
            """SELECT * FROM ciclos_cobranca WHERE estado = ? AND recuperado_em >= ?
             ORDER BY id""", (RECUPERADO, _iso(limite)))]
    finally:
        conn.close()


# ── Deduplicação de webhook (1.2) ────────────────────────────────────────

def registrar_evento_se_novo(escopo: str, chave: str, agora: datetime, ttl: timedelta) -> bool:
    """`True` se a chave é nova no escopo; `False` se é reenvio dentro do TTL.

    Expira, consulta e grava sob a mesma trava de escrita — sem fresta entre
    a consulta e a escrita por onde duas entregas simultâneas passariam as
    duas. Sobrevive a reinício porque está no arquivo, e vale para dois
    processos no mesmo arquivo.
    """
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM eventos_vistos WHERE escopo = ? AND visto_em < ?",
                     (escopo, _iso(agora - ttl)))
        visto = conn.execute(
            "SELECT visto_em FROM eventos_vistos WHERE escopo = ? AND chave = ?",
            (escopo, chave)).fetchone()
        if visto is not None:
            conn.commit()
            logger.info("[IDEMPOTENCIA] %s: reenvio ignorado (visto em %s) — %s",
                        escopo, visto["visto_em"], chave)
            return False
        conn.execute("INSERT INTO eventos_vistos (escopo, chave, visto_em) VALUES (?, ?, ?)",
                     (escopo, chave, _iso(agora)))
        conn.commit()
        return True
    finally:
        conn.close()


def _banco_existe() -> bool:
    """B4-b: limpar ou contar num banco que ainda não existe é não fazer nada.

    Sem esta guarda, a fixture de isolamento da suíte — que limpa as janelas
    de deduplicação a cada teste — CRIAVA um banco novo (três tabelas, DDL
    inteiro) em cada `tmp_path`, para truncar tabelas vazias: ~1.400 bancos
    por execução, para ~150 testes que de fato tocam o ciclo. Medido antes e
    depois no relatório do Bloco 4."""
    return caminho_do_banco().exists()


def esquecer_evento(escopo: str, chave: str) -> None:
    """Desfaz o registro de um evento cujo processamento FALHOU (B2-a): o PSP
    vai reenviar, e o reenvio tem que ser processado, não descartado."""
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM eventos_vistos WHERE escopo = ? AND chave = ?", (escopo, chave))
        conn.commit()
    finally:
        conn.close()


def limpar_eventos(escopo: Optional[str] = None) -> None:
    if not _banco_existe():
        return
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if escopo is None:
            conn.execute("DELETE FROM eventos_vistos")
        else:
            conn.execute("DELETE FROM eventos_vistos WHERE escopo = ?", (escopo,))
        conn.commit()
    finally:
        conn.close()


def contar_eventos(escopo: str) -> int:
    if not _banco_existe():
        return 0
    conn = _conectar()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM eventos_vistos WHERE escopo = ?",
                                (escopo,)).fetchone()[0])
    finally:
        conn.close()


# ── Migração do pix_retry_state.json (1.3) ───────────────────────────────

def migrar_retry_state_json(agora: Optional[datetime] = None) -> int:
    """Os planos do `pix_retry_state.json` viram ciclos e tentativas. Devolve
    quantos ciclos foram criados NESTA chamada. Idempotente: um plano cujo
    ciclo já existe (`UNIQUE` por tenant + e2e do plano) é pulado."""
    conn = _conectar()
    try:
        return _migrar_json(conn, agora)
    finally:
        conn.close()


def _ler_json_de_planos() -> dict:
    """Lê o arquivo de planos pelo caminho que `retry_state` resolve. Import
    tardio para não fechar o ciclo `retry_state -> ciclo_cobranca`."""
    from . import retry_state
    caminho = retry_state.caminho_do_estado()
    if not caminho.exists():
        return {}
    try:
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else {}
    except Exception as e:                       # noqa: BLE001
        logger.warning("[CICLO] pix_retry_state.json ilegível em %s (%s) — nada migrado. "
                       "Nenhuma tentativa é CONCEDIDA por isso.", caminho, e)
        return {}


def _migrar_json(conn: sqlite3.Connection, agora: Optional[datetime] = None) -> int:
    dados = _ler_json_de_planos()
    if not dados:
        return 0
    agora = agora or datetime.now()
    tem_recovery_log = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ciclos_recuperacao'"
    ).fetchone() is not None

    criados = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for registro in dados.values():
            if not isinstance(registro, dict) or not registro.get("customer_id"):
                continue
            tenant = registro.get("tenant_id") or TENANT_PADRAO
            mandato = str(registro["customer_id"])
            e2e = _texto(registro.get("e2e_id"))
            atualizado = _data(registro.get("atualizado_em")) or agora
            identidade = identidade_da_cobranca(None, e2e, mandato, atualizado)
            if conn.execute(
                "SELECT 1 FROM ciclos_cobranca WHERE tenant_id = ? AND id_cobranca_original = ?",
                (tenant, identidade)).fetchone() is not None:
                continue

            linha_dataset = None
            if tem_recovery_log and e2e:
                linha_dataset = conn.execute(
                    """SELECT failure_cause, recovered, success_fee, amount
                         FROM ciclos_recuperacao WHERE tenant_id = ? AND e2e_id = ?""",
                    (tenant, e2e)).fetchone()

            janela_fim = _data(registro.get("pix_janela_ate")) or fim_da_janela(atualizado)
            janela_inicio = inicio_da_janela(janela_fim)
            valor = round(float(registro.get("valor_original") or 0.0), 2)
            causa = (linha_dataset["failure_cause"] if linha_dataset and linha_dataset["failure_cause"]
                     else "desconhecida")

            recuperado = bool(linha_dataset and linha_dataset["recovered"])
            if recuperado:
                estado, fee = RECUPERADO, float(linha_dataset["success_fee"] or 0.0)
            elif janela_fim < agora:
                estado, fee = PERDIDO, 0.0
            else:
                estado, fee = RECOBRANDO, 0.0

            marca = _iso(agora)
            colunas = {
                "tenant_id": tenant, "id_recorrencia": mandato,
                "id_cobranca_original": identidade, "e2e_falha_original": e2e,
                "valor": valor, "causa_original": causa,
                "janela_inicio": _iso(janela_inicio), "janela_fim": _iso(janela_fim),
                "estado": estado, "fee": fee, "origem": ORIGEM_MIGRACAO,
                "aberto_em": _iso(atualizado), "atualizado_em": marca,
            }
            coluna_data = _COLUNA_DA_TRANSICAO.get(estado)
            if coluna_data:
                colunas[coluna_data] = marca
            cur = conn.execute(
                f"INSERT INTO ciclos_cobranca ({', '.join(colunas)}) "
                f"VALUES ({', '.join('?' * len(colunas))})", tuple(colunas.values()))
            ciclo_id = cur.lastrowid
            criados += 1

            tentativas = [t for t in (registro.get("tentativas") or [])
                          if isinstance(t, dict) and isinstance(t.get("numero"), int)
                          and 1 <= t["numero"] <= MAX_TENTATIVAS]
            tentativas.sort(key=lambda t: t["numero"])
            disparadas = [t for t in tentativas if t.get("disparada_em")]
            mais_recente_disparada = disparadas[-1]["numero"] if disparadas else None

            # Números ausentes ABAIXO do menor presente: o plano nasceu com
            # `tentativas_usadas = N` e não gravou as N primeiras. Elas viram
            # `falhou` declaradas, para a contagem executada continuar sendo a
            # que o BACEN já viu — a migração não afrouxa o limite.
            menor = tentativas[0]["numero"] if tentativas else MAX_TENTATIVAS + 1
            for numero in range(1, menor):
                conn.execute(
                    """INSERT INTO tentativas_cobranca
                           (ciclo_id, numero, agendada_para, origem_data, valor, resultado)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (ciclo_id, numero, _iso(janela_inicio), ORIGEM_DECLARADA, valor, FALHOU))

            for t in tentativas:
                numero = t["numero"]
                disparada_em = _texto(t.get("disparada_em"))
                if estado == RECUPERADO:
                    resultado = PAGA if numero == mais_recente_disparada else (
                        SEM_RETORNO if disparada_em else CANCELADA)
                elif estado == PERDIDO:
                    resultado = SEM_RETORNO if disparada_em else CANCELADA
                else:
                    resultado = PENDENTE
                motivo = "migracao" if resultado == CANCELADA else None
                resultado_em = marca if resultado not in (PENDENTE,) else None
                conn.execute(
                    """INSERT INTO tentativas_cobranca
                           (ciclo_id, numero, agendada_para, origem_data, valor,
                            disparada_em, id_cobranca, resultado, motivo_cancelamento, resultado_em)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ciclo_id, numero, _iso(t.get("quando")) or _iso(janela_inicio),
                     _texto(t.get("origem")) or ORIGEM_MIGRACAO,
                     round(float(t.get("valor") or valor), 2),
                     disparada_em, _texto(t.get("id_cobranca")), resultado, motivo, resultado_em))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    if criados:
        logger.info("[CICLO] %d plano(s) do pix_retry_state.json migrado(s) para as tabelas.",
                    criados)
    return criados


# ── Limpeza (teste e demo) ───────────────────────────────────────────────

def limpar_tudo() -> None:
    """Esquece ciclos, tentativas e eventos vistos. Existe para o teste e para
    a demo — o JSON antigo não é tocado. Num banco que não existe, nada a
    esquecer (B4-b)."""
    if not _banco_existe():
        return
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM tentativas_cobranca")
        conn.execute("DELETE FROM ciclos_cobranca")
        conn.execute("DELETE FROM eventos_vistos")
        conn.commit()
    finally:
        conn.close()


# ── Helpers de tipo ──────────────────────────────────────────────────────

def _num(valor):
    if valor is None or isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)


def _texto(valor) -> Optional[str]:
    if valor is None:
        return None
    if isinstance(valor, (str, int, float)) and not isinstance(valor, bool):
        texto = str(valor)
        return texto[:128] if texto else None
    return None

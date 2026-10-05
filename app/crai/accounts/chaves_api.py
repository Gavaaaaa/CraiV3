"""crai/accounts/chaves_api.py — a chave de API da empresa (Rodada 2, Fase 1).

PARA QUE SERVE. A empresa gera uma chave no dashboard e a coloca no sistema
dela. Com a chave, esse sistema chama a API de clientes sozinho, sem ninguém
logado. O token de login continua valendo nas mesmas rotas: a chave é um
segundo caminho, não uma troca (K9).

AS REGRAS, e onde cada uma mora:

    K1  um tipo só: `crai_live_` + 32 bytes do `secrets` em texto seguro para
        URL (`gerar_chave`). Não existe chave de teste.
    K4  a chave inteira só existe na resposta da criação. O banco guarda o
        SHA-256 dela, o prefixo visível (`crai_live_` + 4 caracteres) e os 4
        últimos caracteres. Com 256 bits aleatórios, o hash sem sal basta: não
        há dicionário a tentar.
    K5  revogação imediata: `autenticar` lê o banco a cada requisição, sem
        cache. Vale também entre processos, porque o banco é o mesmo.
    K6  inexistente, malformada e revogada levantam a MESMA `ChaveInvalida`;
        quem responde (`accounts/auth.py`) devolve o mesmo 401 para as três.
    K7  o tenant é o gravado com a chave na criação, que veio do token de quem
        a criou. Nunca vem do corpo.
    K8  `ROTAS_COM_CHAVE` é a lista fechada das rotas que a chave autentica:
        as quatro da API de clientes e, desde a Rodada 3, `POST /eventos`.
        Fora dela a chave nem é consultada (`accounts/auth.py`).
    K10 no máximo `MAX_ATIVAS` chaves ativas por empresa, contadas e gravadas
        na mesma transação (`BEGIN IMMEDIATE`).
    K11 cada uso grava `ultimo_uso_em` e soma no contador do dia da chave
        (`chaves_api_uso`). Nada da requisição é guardado: nem corpo, nem
        caminho, nem IP.
    K12 limite de uso por chave e por minuto (`CRAI_API_LIMITE_POR_MINUTO`,
        padrão 120), em janela deslizante NA MEMÓRIA DO PROCESSO: reinício zera,
        e cada worker conta o seu. Limitação declarada em `docs/LIMITACOES.md`.

ONDE MORA. Tabelas `chaves_api` e `chaves_api_uso`, no arquivo do ciclo de
cobrança (`recovery_cycles.db`, env `CRAI_RECOVERY_DB`), ao lado de
`configuracao_tenant` e de `acessos_titular`: é dado de configuração da
empresa, a suíte já isola esse arquivo em todo teste, e ele não depende do
Supabase. Datas em hora local sem fuso, como as outras desse arquivo.

A COMPARAÇÃO. A busca é pelo prefixo visível (que não é segredo: fica em texto
no banco e aparece na tela), e o hash de cada candidata é comparado com
`hmac.compare_digest`. O tempo da resposta não depende de quantos caracteres do
segredo o chamador acertou.

A CHAVE NUNCA VAI PARA O LOG. Este módulo loga o `id` da chave e o tenant, e
nada mais. Nenhuma exceção daqui carrega a chave no texto.

Este módulo NÃO importa de `api/` nem de `accounts/auth.py`.
"""

import hashlib
import hmac
import logging
import math
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional

from ..dunning import ciclo_cobranca

logger = logging.getLogger(__name__)

PREFIXO = "crai_live_"
BYTES_ALEATORIOS = 32
VISIVEIS_NO_INICIO = 4
VISIVEIS_NO_FIM = 4
NOME_MAX = 60
MAX_ATIVAS = 5
ENV_LIMITE = "CRAI_API_LIMITE_POR_MINUTO"
LIMITE_PADRAO = 120
# S6 (Rodada 3): os eventos de comportamento têm limite próprio, contado à
# parte do das rotas de clientes. Um sistema que avisa cada visita não pode
# gastar o limite de quem atualiza a base, nem o contrário.
ENV_LIMITE_EVENTOS = "CRAI_EVENTOS_LIMITE_POR_MINUTO"
LIMITE_EVENTOS_PADRAO = 600
JANELA_SEGUNDOS = 60

# K8: (método, modelo da rota). É por esta lista, e só por ela, que
# `accounts/auth.get_tenant_id` decide se consulta a chave.
ROTA_DE_EVENTOS = ("POST", "/eventos")
ROTAS_COM_CHAVE = frozenset({
    ("POST", "/clientes"),
    ("POST", "/clientes/lote"),
    ("PATCH", "/clientes/{customer_id_externo}"),
    ("DELETE", "/clientes/{customer_id_externo}"),
    # Rodada 3 (S1): os eventos de comportamento do cliente final.
    ROTA_DE_EVENTOS,
})

# `token_urlsafe(32)` devolve sempre 43 caracteres de [A-Za-z0-9_-].
_FORMA = re.compile(r"^crai_live_[A-Za-z0-9_-]{43}$")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")

_DDL = """
CREATE TABLE IF NOT EXISTS chaves_api (
    id               TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL,
    nome             TEXT NOT NULL,
    prefixo          TEXT NOT NULL,
    final            TEXT NOT NULL,
    hash             TEXT NOT NULL,
    criada_em        TEXT NOT NULL,
    criada_por_papel TEXT NOT NULL,
    ultimo_uso_em    TEXT,
    revogada_em      TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chaves_api_hash ON chaves_api (hash);
CREATE INDEX IF NOT EXISTS idx_chaves_api_tenant ON chaves_api (tenant_id, criada_em);
CREATE INDEX IF NOT EXISTS idx_chaves_api_prefixo ON chaves_api (prefixo);
CREATE TABLE IF NOT EXISTS chaves_api_uso (
    chave_id TEXT    NOT NULL REFERENCES chaves_api(id),
    dia      TEXT    NOT NULL,
    total    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chave_id, dia)
);
"""

_COLUNAS_PUBLICAS = ("id, nome, prefixo, final, criada_em, criada_por_papel, "
                     "ultimo_uso_em, revogada_em")


class ChaveInvalida(Exception):
    """A chave não autentica: inexistente, malformada ou revogada. Sem texto
    que diga qual das três (K6)."""


class LimiteDeUso(Exception):
    """A chave passou do limite por minuto. `espera` é o `Retry-After`."""

    def __init__(self, espera: int):
        super().__init__(f"limite de uso por minuto atingido; tente em {espera}s")
        self.espera = espera


class NomeInvalido(ValueError):
    """O nome dado à chave não passou na validação."""


class LimiteDeChaves(Exception):
    """A empresa já tem `MAX_ATIVAS` chaves ativas (K10)."""


# ── Infra ────────────────────────────────────────────────────────────────

def _conectar() -> sqlite3.Connection:
    conn = ciclo_cobranca._conectar()
    conn.executescript(_DDL)
    return conn


def _texto(agora: Optional[datetime]) -> str:
    return (agora or datetime.now()).isoformat(timespec="seconds")


# ── A chave ──────────────────────────────────────────────────────────────

def gerar_chave() -> str:
    return PREFIXO + secrets.token_urlsafe(BYTES_ALEATORIOS)


def hash_da_chave(chave: str) -> str:
    return hashlib.sha256(chave.encode("utf-8")).hexdigest()


def parece_chave(token) -> bool:
    """O texto tem o prefixo de chave de API (K1). Não diz se ela é válida."""
    return isinstance(token, str) and token.startswith(PREFIXO)


def _prefixo_visivel(chave: str) -> str:
    return chave[:len(PREFIXO) + VISIVEIS_NO_INICIO]


def validar_nome(nome) -> str:
    """O nome que a empresa dá à chave: texto de 1 a 60 caracteres, sem
    caractere de controle; espaços das pontas são descartados."""
    if not isinstance(nome, str):
        raise NomeInvalido("`nome` precisa ser texto")
    nome = nome.strip()
    if not nome:
        raise NomeInvalido("`nome` não pode ficar vazio")
    if len(nome) > NOME_MAX:
        raise NomeInvalido(f"`nome` tem {len(nome)} caracteres; o máximo é {NOME_MAX}")
    if _CONTROLE.search(nome):
        raise NomeInvalido("`nome` não aceita quebra de linha nem caractere de controle")
    return nome


# ── Criar, listar, revogar ───────────────────────────────────────────────

def _publica(conn: sqlite3.Connection, linha, hoje: str) -> dict:
    dados = dict(linha)
    uso = conn.execute("SELECT total FROM chaves_api_uso WHERE chave_id = ? AND dia = ?",
                       (dados["id"], hoje)).fetchone()
    dados["usos_hoje"] = int(uso["total"]) if uso else 0
    return dados


def criar(tenant_id: str, nome: str, papel: str,
          agora: Optional[datetime] = None) -> tuple:
    """Cria uma chave da empresa. Devolve `(chave inteira, linha pública)`: é a
    ÚNICA vez em que a chave inteira existe fora de quem a recebe.

    Levanta `NomeInvalido` e `LimiteDeChaves`. A contagem das ativas e a
    gravação acontecem na mesma transação: duas criações simultâneas não
    passam do limite."""
    nome = validar_nome(nome)
    chave = gerar_chave()
    chave_id = "chv_" + secrets.token_hex(8)
    quando = _texto(agora)
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        ativas = conn.execute(
            "SELECT COUNT(*) FROM chaves_api WHERE tenant_id = ? AND revogada_em IS NULL",
            (tenant_id,)).fetchone()[0]
        if ativas >= MAX_ATIVAS:
            conn.rollback()
            raise LimiteDeChaves(f"a empresa já tem {MAX_ATIVAS} chaves ativas")
        conn.execute(
            "INSERT INTO chaves_api (id, tenant_id, nome, prefixo, final, hash, criada_em, "
            "criada_por_papel) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (chave_id, tenant_id, nome, _prefixo_visivel(chave), chave[-VISIVEIS_NO_FIM:],
             hash_da_chave(chave), quando, papel))
        conn.commit()
        linha = conn.execute(f"SELECT {_COLUNAS_PUBLICAS} FROM chaves_api WHERE id = ?",
                             (chave_id,)).fetchone()
        publica = _publica(conn, linha, quando[:10])
    finally:
        conn.close()
    logger.info("[CHAVE-API] tenant=%s chave criada id=%s por papel=%s", tenant_id, chave_id, papel)
    return chave, publica


def listar(tenant_id: str, agora: Optional[datetime] = None) -> list:
    """As chaves da empresa, da mais nova à mais antiga, ativas e revogadas.
    Sem o hash e sem a chave: só o que a tela mostra."""
    hoje = _texto(agora)[:10]
    conn = _conectar()
    try:
        linhas = conn.execute(
            f"SELECT {_COLUNAS_PUBLICAS} FROM chaves_api WHERE tenant_id = ? "
            "ORDER BY criada_em DESC, rowid DESC", (tenant_id,)).fetchall()
        return [_publica(conn, linha, hoje) for linha in linhas]
    finally:
        conn.close()


def revogar(tenant_id: str, chave_id: str, agora: Optional[datetime] = None):
    """Revoga a chave. Devolve `(linha pública, ja_estava_revogada)`, ou None se
    não há chave com este id NESTA empresa (a de outra empresa é o mesmo None).
    Revogar duas vezes mantém a data original."""
    quando = _texto(agora)
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        atual = conn.execute(
            "SELECT revogada_em FROM chaves_api WHERE id = ? AND tenant_id = ?",
            (chave_id, tenant_id)).fetchone()
        if atual is None:
            conn.rollback()
            return None
        ja_estava = atual["revogada_em"] is not None
        if not ja_estava:
            conn.execute("UPDATE chaves_api SET revogada_em = ? WHERE id = ? AND tenant_id = ?",
                         (quando, chave_id, tenant_id))
        conn.commit()
        linha = conn.execute(f"SELECT {_COLUNAS_PUBLICAS} FROM chaves_api WHERE id = ?",
                             (chave_id,)).fetchone()
        publica = _publica(conn, linha, quando[:10])
    finally:
        conn.close()
    with _limite_lock:
        _usos.pop(chave_id, None)
    if not ja_estava:
        logger.info("[CHAVE-API] tenant=%s chave revogada id=%s", tenant_id, chave_id)
    return publica, ja_estava


# ── Limite de uso (K12) ──────────────────────────────────────────────────

_limite_lock = threading.Lock()
_usos: dict = {}                     # id da chave → instantes dos usos na janela
_relogio = time.monotonic            # a suíte troca por um relógio que ela controla


def limite_por_minuto() -> int:
    """Lido a cada chamada. Valor ausente, ilegível ou menor que 1 vale o padrão."""
    try:
        valor = int((os.getenv(ENV_LIMITE) or "").strip())
    except ValueError:
        return LIMITE_PADRAO
    return valor if valor >= 1 else LIMITE_PADRAO


def limite_de_eventos_por_minuto() -> int:
    """S6: o limite de `POST /eventos` por chave e por minuto. Lido a cada
    chamada. Valor ausente, ilegível ou menor que 1 vale o padrão."""
    try:
        valor = int((os.getenv(ENV_LIMITE_EVENTOS) or "").strip())
    except ValueError:
        return LIMITE_EVENTOS_PADRAO
    return valor if valor >= 1 else LIMITE_EVENTOS_PADRAO


def _consumir(chave_id: str, eventos: bool = False) -> Optional[int]:
    """Conta um uso da chave na janela. None se coube; senão, quantos segundos
    faltam para o uso mais antigo sair da janela. `eventos` conta na janela dos
    eventos (S6), separada da das rotas de clientes."""
    agora = _relogio()
    limite = limite_de_eventos_por_minuto() if eventos else limite_por_minuto()
    with _limite_lock:
        fila = _usos.setdefault((chave_id, "eventos") if eventos else chave_id, deque())
        while fila and agora - fila[0] >= JANELA_SEGUNDOS:
            fila.popleft()
        if len(fila) >= limite:
            return max(1, math.ceil(JANELA_SEGUNDOS - (agora - fila[0])))
        fila.append(agora)
        return None


def limpar_limites() -> None:
    """Para os testes: nenhum teste herda a contagem de outro."""
    with _limite_lock:
        _usos.clear()


# ── Autenticar ───────────────────────────────────────────────────────────

def _registrar_uso(chave_id: str, agora: Optional[datetime]) -> None:
    """K11, best effort: uma falha ao contar o uso é logada e a requisição
    segue. A revogação não depende desta escrita."""
    quando = _texto(agora)
    try:
        conn = _conectar()
        try:
            conn.execute("UPDATE chaves_api SET ultimo_uso_em = ? WHERE id = ?", (quando, chave_id))
            conn.execute(
                "INSERT INTO chaves_api_uso (chave_id, dia, total) VALUES (?, ?, 1) "
                "ON CONFLICT (chave_id, dia) DO UPDATE SET total = total + 1",
                (chave_id, quando[:10]))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:                        # noqa: BLE001 — best effort declarado
        logger.error("[CHAVE-API] uso não registrado (id=%s): %r", chave_id, e)


def autenticar(chave: str, agora: Optional[datetime] = None, eventos: bool = False) -> str:
    """O tenant da chave, se ela vale. Levanta `ChaveInvalida` (inexistente,
    malformada ou revogada, sem distinção) ou `LimiteDeUso`. `eventos` conta o
    uso no limite próprio dos eventos (S6).

    A ordem: forma, banco, limite, registro do uso. Requisição recusada pelo
    limite não conta como uso."""
    if not isinstance(chave, str) or not _FORMA.match(chave):
        raise ChaveInvalida()
    calculado = hash_da_chave(chave)
    conn = _conectar()
    try:
        candidatas = conn.execute(
            "SELECT id, tenant_id, hash, revogada_em FROM chaves_api WHERE prefixo = ?",
            (_prefixo_visivel(chave),)).fetchall()
    finally:
        conn.close()
    achada = None
    for linha in candidatas:                      # sem parar na primeira: tempo constante
        if hmac.compare_digest(linha["hash"], calculado):
            achada = linha
    if achada is None or achada["revogada_em"] is not None:
        raise ChaveInvalida()
    espera = _consumir(achada["id"], eventos)
    if espera is not None:
        raise LimiteDeUso(espera)
    _registrar_uso(achada["id"], agora)
    return achada["tenant_id"]

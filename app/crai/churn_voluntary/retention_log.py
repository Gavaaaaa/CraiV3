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

──────────────────────────────────────────────────────────────────────────
A SEGUNDA TABELA: `decisoes_automatizadas` — a trilha do Art. 20 da LGPD.

Mesmo módulo, mesmo arquivo de banco, mesmos `_agora`, `_num`, `_conectar` e
a mesma disciplina de tenant. Não é a mesma tabela, e a razão é o que cada
uma É:

    ciclos_retencao          DATASET DE TREINO. O X e o y, e o y chega depois,
                             por UPDATE (`registrar_desfecho`). Está certo
                             para o que ela é.
    decisoes_automatizadas   REGISTRO IMUTÁVEL. O que foi decidido, quando,
                             por qual modelo, com quais entradas, e a frase
                             que explica. Só INSERT. Uma linha que pode ser
                             alterada não serve como evidência.

QUEM É QUEM. O titular é o cliente final da empresa cliente; a empresa
cliente é a CONTROLADORA; a CRAI é OPERADORA. O pedido de revisão (Art. 20)
chega à controladora, e é ela quem responde. Esta trilha existe para que ela
consiga: a leitura é por tenant, e não há rota pública para o titular.

APPEND-ONLY, VERIFICÁVEL. Cada linha carrega o sha256 da linha anterior do
mesmo tenant (`hash_anterior`; a primeira aponta para `HASH_GENESE`) e o seu
próprio (`hash_linha`). Alterar uma linha no meio quebra a cadeia da seguinte
em diante, e `verificar_cadeia` aponta onde. Não existe função de UPDATE nem
de DELETE sobre esta tabela — a única exceção é `apagar_trilha_expirada`, a
retenção, que ainda não é chamada por ninguém (ver `RETENCAO_TRILHA_DIAS`).

SEM BIFURCAÇÃO. Dois gravadores simultâneos do mesmo tenant (dois workers,
ou o lote junto com o grafo) leriam o mesmo "último hash" e gravariam duas
linhas apontando para o mesmo antecessor — dois ramos, cada um íntegro
consigo mesmo. O índice ÚNICO `(tenant_id, hash_anterior)` faz o segundo
FALHAR em vez de bifurcar, e falhar alto (`IntegrityError`, logado como
BIFURCAÇÃO EVITADA): é o comportamento certo para uma tabela que existe
para ser evidência. Em SQLite o `BEGIN IMMEDIATE` já serializa os
gravadores e o caso quase não aparece; em Postgres com vários workers,
aparece — e o índice é o que vale lá. O lote encadeia as próprias linhas
entre si dentro da transação, não todas contra o mesmo antecessor.

ONDE CHAMAR (e onde não). O registro pertence ao momento em que o SISTEMA
decide — os nós dos grafos e o disparo em lote —, não ao momento em que um
humano consulta. O ranking do `GET /insights` fica de fora por isso: se a
controladora olha o ranking e decide agir, aquela decisão deixa de ser
"unicamente com base em tratamento automatizado", que é o pressuposto do
caput do Art. 20; registrar cada leitura confundiria decisão automatizada
com consulta humana. (Raciocínio a confirmar com advogado.) As rotas de
CRUD da API de clientes também ficam de fora: são a controladora informando
um fato, não o sistema decidindo.

O QUE NUNCA ENTRA (e é testado lendo o conteúdo gravado, não só as colunas):
e-mail, telefone, o TEXTO da mensagem (entra `texto_codigo` e `origem`),
`motivo_cancelamento` (texto livre do backend do cliente) e qualquer nome de
pessoa. `_sem_dado_cru` tira essas chaves de `entradas`, `saida` e
`contribuicoes` em qualquer profundidade; os pontos de decisão, além disso,
montam dicionários explícitos em vez de despejar o `state`.

PSEUDONIMIZAÇÃO. `sujeito_id` é o identificador que a própria controladora
usa (`customer_id_externo` / `user_id`). A CRAI nunca recebe nome e o e-mail
fica fora da trilha, então a trilha sozinha não identifica ninguém sem a base
da controladora — isso já é pseudonimização. A TOKENIZAÇÃO CRIPTOGRÁFICA
(HMAC ou cifra do `sujeito_id`) é passo posterior e foi adiada de propósito:
gestão de chave é operação, e chave perdida significa trilha ilegível — o
oposto do que o Art. 20 pede.

BEST EFFORT, como o resto do módulo: falha ao gravar a trilha não derruba a
decisão. Loga e segue.
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
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

-- A trilha do Art. 20. Só INSERT (ver docstring do módulo).
CREATE TABLE IF NOT EXISTS decisoes_automatizadas (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id         TEXT    NOT NULL,
    sujeito_id        TEXT    NOT NULL,
    decidido_em       TEXT    NOT NULL,
    dominio           TEXT    NOT NULL,   -- involuntario | voluntario
    tipo_decisao      TEXT    NOT NULL,   -- risco | oferta | canal | retentativa
    modelo            TEXT    NOT NULL,   -- módulo que decidiu, ou 'regra'
    modelo_versao     TEXT,               -- versão/hash do artefato, ou NULL
    entradas          TEXT    NOT NULL,   -- JSON: as features que o modelo viu
    saida             TEXT    NOT NULL,   -- JSON: o que foi decidido
    explicacao        TEXT    NOT NULL,   -- frase em pt-BR
    contribuicoes     TEXT,               -- JSON: k features de maior peso, ou NULL
    hash_anterior     TEXT,               -- sha256 da linha anterior do tenant
    hash_linha        TEXT    NOT NULL    -- sha256 desta linha
);
CREATE INDEX IF NOT EXISTS idx_decisao_sujeito
    ON decisoes_automatizadas (tenant_id, sujeito_id, id);
CREATE INDEX IF NOT EXISTS idx_decisao_tenant
    ON decisoes_automatizadas (tenant_id, id);
"""

# O elo é único por tenant: duas linhas com o mesmo antecessor seriam uma
# bifurcação da cadeia. Índice (e não constraint na tabela) para valer também
# numa base criada antes desta linha existir. Criado FORA do `_SCHEMA`: numa
# base legada que já tenha bifurcação a criação falha, e essa falha não pode
# derrubar a conexão — o `ciclos_retencao` continua precisando gravar. Ela é
# logada alto, e `verificar_cadeia` aponta a bifurcação.
_INDICE_ELO_UNICO = """
CREATE UNIQUE INDEX IF NOT EXISTS uq_decisao_elo
    ON decisoes_automatizadas (tenant_id, hash_anterior);
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
    try:
        conn.executescript(_INDICE_ELO_UNICO)
    except sqlite3.IntegrityError as e:
        print(f"[ART20] ATENÇÃO: a trilha em {caminho} já tem bifurcação — o índice único "
              f"do elo não pôde ser criado ({e}). `verificar_cadeia` aponta onde.")
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


_INSERT_CICLO = """INSERT INTO ciclos_retencao (
                       tenant_id, user_id, registrado_em,
                       event, days_since_last, features_used_30d, mrr,
                       billing_profile, on_site_now,
                       risk_score, profile, criticality, offer_type, channel,
                       offer_sent, accepted, desfecho_em, origem_desfecho)
                   VALUES (?,?,?, ?,?,?,?, ?,?, ?,?,?,?,?, ?,?,?,?)"""


def _linha_do_ciclo(state: dict) -> tuple:
    """Os valores do INSERT, na ordem de `_INSERT_CICLO`. Uma função só para
    o registro unitário e o em lote gravarem exatamente a mesma linha."""
    props = state.get("props") or {}
    return (
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
    )


def registrar_ciclo(state: dict) -> int | None:
    """Grava features + decisão. Devolve o id da linha, ou None se falhou.

    Chamado no `update_crm`, que é o último nó nos DOIS modos. Em simulação o
    `accepted` já é conhecido e entra junto; em produção fica NULL até o
    webhook.
    """
    try:
        with _conectar() as conn:
            cur = conn.execute(_INSERT_CICLO, _linha_do_ciclo(state))
            return cur.lastrowid
    except Exception as e:                       # noqa: BLE001 — best effort declarado
        print(f"[RETENTION-LOG] Falha ao registrar ciclo: {e}")
        return None


def registrar_ciclos(states: list[dict]) -> list[int | None]:
    """O mesmo registro, para N ciclos numa conexão e numa transação só.

    Existe para o disparo em lote: medido, `registrar_ciclo` por cliente
    custava ~6 ms (abrir conexão, garantir schema, commit) e 2.000 clientes
    levavam 12 s só gravando. Aqui o custo é de um commit. A linha gravada é
    a mesma (`_linha_do_ciclo`), então o dataset de treino não distingue um
    ciclo do lote de um ciclo do grafo — exceto pelo `event`.

    Best effort como o unitário: falha na transação devolve `None` para
    todos, com log — o lote já decidiu; o que falhou foi o registro.
    """
    if not states:
        return []
    try:
        with _conectar() as conn:
            return [conn.execute(_INSERT_CICLO, _linha_do_ciclo(s)).lastrowid
                    for s in states]
    except Exception as e:                       # noqa: BLE001
        print(f"[RETENTION-LOG] Falha ao registrar {len(states)} ciclos em lote: {e}")
        return [None] * len(states)


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


def apagar_tenant(tenant_id: str) -> int:
    """Apaga TODOS os ciclos deste tenant. Devolve quantos saíram.

    Existe para a rota de demonstração `/simulate/painel/importar`: ela já
    apaga a base importada do tenant fixo do painel antes de importar, e sem
    apagar os ciclos junto a segunda rodada do disparo em lote devolvia todo
    mundo como `ciclo_aberto` e não tratava ninguém. A rota autenticada
    `/clientes/importar` NÃO chama isto: ciclo aberto lá é histórico real.

    Ao contrário do registro (best effort), aqui uma falha LEVANTA: uma
    limpeza que falha em silêncio deixaria a demonstração exatamente no
    estado que ela veio evitar. O filtro por tenant é obrigatório.
    """
    with _conectar() as conn:
        cur = conn.execute("DELETE FROM ciclos_retencao WHERE tenant_id = ?", (tenant_id,))
        return int(cur.rowcount)


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


# ══════════════════════════════════════════════════════════════════════════
# A trilha do Art. 20 — `decisoes_automatizadas`
# ══════════════════════════════════════════════════════════════════════════

DOMINIO_INVOLUNTARIO = "involuntario"
DOMINIO_VOLUNTARIO = "voluntario"
DOMINIOS = (DOMINIO_INVOLUNTARIO, DOMINIO_VOLUNTARIO)

TIPO_RISCO = "risco"
TIPO_OFERTA = "oferta"
TIPO_CANAL = "canal"
TIPO_RETENTATIVA = "retentativa"
TIPOS_DECISAO = (TIPO_RISCO, TIPO_OFERTA, TIPO_CANAL, TIPO_RETENTATIVA)

# `modelo` quando a decisão veio de regra fixa, sem modelo treinado. O NOME
# da regra vai em `saida["regra"]`, e a explicação o cita.
MODELO_REGRA = "regra"

# O `hash_anterior` da PRIMEIRA linha de cada tenant. Um valor, e não NULL:
# o índice único `(tenant_id, hash_anterior)` não vê dois NULL como iguais, e
# duas "primeiras linhas" seriam uma bifurcação na gênese.
HASH_GENESE = "0" * 64

# RETENÇÃO. A LGPD (Art. 15, I e Art. 16) manda guardar só pelo tempo
# necessário à finalidade; a finalidade aqui é a controladora conseguir
# responder a um pedido de revisão (Art. 20) sobre uma decisão passada. O
# prazo adequado depende de quanto tempo depois da decisão esse pedido ainda
# pode chegar — o que é decisão de negócio e jurídica do Crai, não deste
# código. 730 dias (dois anos) é um PADRÃO DECLARADO, escolhido por cobrir o
# ciclo de vida típico de uma assinatura mais uma margem; PRECISA SER
# CONFIRMADO antes de `apagar_trilha_expirada` passar a ser chamada.
RETENCAO_TRILHA_DIAS = 730

# Chaves que nunca entram em `entradas`, `saida` ou `contribuicoes`, em
# qualquer profundidade. É a rede de segurança; a primeira linha de defesa é
# cada ponto de decisão montar um dicionário explícito em vez de despejar o
# `state`. A comparação é por nome de chave, sem distinguir caixa.
CHAVES_FORA_DA_TRILHA = frozenset({
    "email", "e_mail", "phone", "telefone", "celular", "cpf", "cnpj",
    "message", "message_sent", "mensagem", "texto", "text", "body", "corpo",
    "candidatas", "raciocinio", "prompt",
    "motivo_cancelamento", "motivo",
    "nome", "name", "first_name", "last_name", "full_name", "razao_social",
    "link", "portal_link", "url",
    # Segredo comercial (Art. 20 §1º): estado do bandit, coeficientes.
    "alpha", "beta", "coef", "coeficientes", "hiperparametros", "hyperparameters",
})

# Sempre a mesma serialização, para o hash ser reproduzível: chaves
# ordenadas, sem escape de acento, e o que não é JSON (datetime) vira texto.
def _json(valor) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, default=str,
                      separators=(",", ":"))


def _sem_dado_cru(valor):
    """Remove `CHAVES_FORA_DA_TRILHA` de dicts e listas, recursivamente."""
    if isinstance(valor, dict):
        return {k: _sem_dado_cru(v) for k, v in valor.items()
                if str(k).lower() not in CHAVES_FORA_DA_TRILHA}
    if isinstance(valor, (list, tuple)):
        return [_sem_dado_cru(v) for v in valor]
    return valor


def _hash_da_linha(tenant_id, sujeito_id, decidido_em, dominio, tipo_decisao,
                   modelo, modelo_versao, entradas_json, saida_json, explicacao,
                   contribuicoes_json, hash_anterior) -> str:
    """sha256 dos campos GRAVADOS, na ordem do schema, a partir do texto que
    está no banco (os JSON já serializados). É o que `verificar_cadeia`
    recalcula linha a linha."""
    material = _json([tenant_id, sujeito_id, decidido_em, dominio, tipo_decisao,
                      modelo, modelo_versao, entradas_json, saida_json, explicacao,
                      contribuicoes_json, hash_anterior])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ── Identidade do modelo ─────────────────────────────────────────────────

_versoes_de_artefato: dict = {}


def versao_do_artefato(meta: dict | None, *arquivos: Path) -> str | None:
    """`<treinado_em>#<12 hex do sha256 dos artefatos>`, ou só uma das partes.

    É o `modelo_versao` da trilha: com ele dá para dizer QUAL artefato tomou
    a decisão, mesmo que o meta.json seja regravado depois. Cacheado por
    (caminho, mtime, tamanho) — hash de alguns MB uma vez por processo.
    Devolve None quando não há nem meta nem arquivo legível: a trilha grava
    NULL, não inventa versão.
    """
    partes = []
    treinado_em = (meta or {}).get("treinado_em")
    if treinado_em:
        partes.append(str(treinado_em))
    h = hashlib.sha256()
    algum = False
    for arq in arquivos:
        try:
            arq = Path(arq)
            st = arq.stat()
            chave = (str(arq), st.st_mtime_ns, st.st_size)
            if chave not in _versoes_de_artefato:
                d = hashlib.sha256()
                with open(arq, "rb") as f:
                    for bloco in iter(lambda: f.read(1 << 20), b""):
                        d.update(bloco)
                _versoes_de_artefato[chave] = d.hexdigest()
            h.update(_versoes_de_artefato[chave].encode())
            algum = True
        except OSError:
            continue
    if algum:
        partes.append(h.hexdigest()[:12])
    return "#".join(partes) if partes else None


# ── A frase legível (Art. 20 §1º) ────────────────────────────────────────
#
# Montada a partir das colunas, com dicionários de rótulos por feature e por
# tipo de decisão — não uma frase por tipo escrita à mão, que divergiria na
# primeira mudança. Quem acrescenta feature acrescenta rótulo aqui.

ROTULOS_DE_FEATURE = {
    "days_since_last":    lambda v: f"{_inteiro(v)} dias sem acesso ao produto",
    "features_used_30d":  lambda v: f"{_inteiro(v)} funcionalidades usadas nos últimos 30 dias",
    "mrr":                lambda v: f"mensalidade de R$ {_reais(v)}",
    "billing_profile":    lambda v: f"perfil de cobrança {v}",
    "profile":            lambda v: f"perfil de cobrança {v}",
    "event":              lambda v: f"evento '{v}'",
    "on_site_now":        lambda v: "cliente no produto neste momento" if v else "cliente fora do produto",
    "telefone_disponivel": lambda v: "telefone disponível para contato" if v else "sem telefone para contato",
    "canal_historico":    lambda v: f"canal em que já converteu antes: {v}" if v else "sem histórico de canal",
    "criticality":        lambda v: f"criticidade {v}",
    "risk_score":         lambda v: f"pontuação de risco {_decimal(v)}",
    "failure_cause":      lambda v: f"causa da falha de pagamento: {ROTULOS_DE_CAUSA.get(v, v)}",
    "gateway_error_code": lambda v: f"causa da falha de pagamento: {ROTULOS_DE_CAUSA.get(v, v)}",
    "amount":             lambda v: f"cobrança de R$ {_reais(v)}",
    "invoice_amount":     lambda v: f"cobrança de R$ {_reais(v)}",
    "recovery_score":     lambda v: f"pontuação de recuperação {_inteiro(v)}/100",
    "p_recovery":         lambda v: f"probabilidade de recuperação {_pct(v)}",
    "eprofit":            lambda v: f"retorno esperado da intervenção R$ {_reais(v)}",
    "is_anomalous":       lambda v: "comportamento anômalo detectado" if v else "sem anomalia de comportamento",
    "payment_method":     lambda v: f"meio de pagamento {ROTULOS_DE_METODO.get(v, v)}",
    "retry_count":        lambda v: f"{_inteiro(v)} tentativas de cobrança já usadas",
    "tentativas_usadas":  lambda v: f"{_inteiro(v)} tentativas de cobrança já usadas",
    "limite_tentativas":  lambda v: f"limite de {_inteiro(v)} tentativas na janela",
    "tenure_days":        lambda v: f"{_inteiro(v)} dias como cliente",
    "attempt_count":      lambda v: f"{_inteiro(v)}ª tentativa da cobrança",
    "card_brand":         lambda v: f"bandeira {v}",
    "metodo_pagamento":   lambda v: f"meio de pagamento {ROTULOS_DE_METODO.get(v, v)}",
    "ltv_estimated":      lambda v: f"valor estimado do cliente R$ {_reais(v)}",
    "customer_ltv":       lambda v: f"valor estimado do cliente R$ {_reais(v)}",
    "failed_payments_90d": lambda v: f"{_inteiro(v)} falhas de pagamento nos últimos 90 dias",
    "successful_payments": lambda v: f"{_inteiro(v)} pagamentos bem-sucedidos",
    "days_since_signup":  lambda v: f"{_inteiro(v)} dias desde o cadastro",
    "hour_of_day":        lambda v: f"cobrança às {_inteiro(v)}h",
    "day_of_month":       lambda v: f"cobrança no dia {_inteiro(v)} do mês",
    "day_of_week":        lambda v: f"cobrança num(a) {DIAS_DA_SEMANA.get(_inteiro(v), v)}",
    "tenure_months":      lambda v: f"{_inteiro(v)} meses como cliente",
    "failure_count_90d":  lambda v: f"{_inteiro(v)} falhas de pagamento nos últimos 90 dias",
    "payment_history_score": lambda v: f"histórico de pagamento {_pct(v)} positivo",
    "avg_ticket":         lambda v: f"gasto médio de R$ {_reais(v)}",
    "vencimento":         lambda v: f"vencimento em {_data_legivel(v)}",
    "criticidade":        lambda v: f"criticidade {v}",
    "canal":              lambda v: f"canal {ROTULOS_DE_CANAL.get(v, v)}",
}

DIAS_DA_SEMANA = {0: "segunda-feira", 1: "terça-feira", 2: "quarta-feira", 3: "quinta-feira",
                  4: "sexta-feira", 5: "sábado", 6: "domingo"}

ROTULOS_DE_SAIDA = {
    "risk_score":      lambda v: f"pontuação de risco {_decimal(v)}",
    "criticality":     lambda v: f"criticidade {v}",
    "profile":         lambda v: f"perfil {v}",
    "offer_type":      lambda v: ROTULOS_DE_OFERTA.get(v, v),
    "channel":         lambda v: f"canal {ROTULOS_DE_CANAL.get(v, v)}",
    "estrategia":      lambda v: ROTULOS_DE_ESTRATEGIA.get(v, v),
    "payment_method":  lambda v: f"meio de pagamento {ROTULOS_DE_METODO.get(v, v)}",
    "recovery_score":  lambda v: f"pontuação de recuperação {_inteiro(v)}/100",
    "recommend_action": lambda v: "intervenção recomendada" if v else "intervenção não recomendada",
    "tentativas":      lambda v: f"{len(v) if isinstance(v, list) else v} nova(s) tentativa(s) de cobrança agendada(s)",
    "origem_das_datas": lambda v: f"datas pela {ROTULOS_DE_ORIGEM_DATAS.get(v, v)}",
    "tom":             lambda v: f"tom {v}",
    "p_recovery":      lambda v: f"probabilidade de recuperação {_pct(v)}",
    "p_estimado":      lambda v: f"probabilidade estimada de aceite {_pct(v)}",
    "eprofit":         lambda v: f"retorno esperado R$ {_reais(v)}",
    "janela_ate":      lambda v: f"janela regulada até {_data_legivel(v)}",
    "texto_codigo":    lambda v: f"modelo de texto '{v}'",
    "origem_texto":    lambda v: ROTULOS_DE_ORIGEM_TEXTO.get(v, f"texto de origem {v}"),
    "criticidade":     lambda v: f"criticidade {v}",
    "canal":           lambda v: f"canal lembrado {ROTULOS_DE_CANAL.get(v, v)}",
}

ROTULOS_DE_ORIGEM_TEXTO = {"template": "texto de modelo pronto (não gerado)",
                           "gerado": "texto gerado para este caso"}

ROTULOS_DE_TIPO = {
    TIPO_RISCO:       "avaliou o risco deste cliente",
    TIPO_OFERTA:      "escolheu o que oferecer a este cliente",
    TIPO_CANAL:       "escolheu por onde contatar este cliente",
    TIPO_RETENTATIVA: "decidiu como tratar a cobrança que falhou",
}

ROTULOS_DE_DOMINIO = {
    DOMINIO_INVOLUNTARIO: "recuperação de pagamento",
    DOMINIO_VOLUNTARIO:   "retenção de assinatura",
}

# Copiados, e não importados, dos módulos que decidem: `retention_log` é
# importado por eles, e o sentido contrário fecharia um ciclo.
ROTULOS_DE_OFERTA = {
    "desconto_10": "desconto de 10% por 3 meses",
    "desconto_20": "desconto de 20% por 3 meses",
    "pausa_1_mes": "pausa de 1 mês na assinatura, sem custo",
    "pix_boleto_flash": "troca para Pix ou boleto em 1 clique",
    None: "nenhuma oferta",
}
ROTULOS_DE_CANAL = {
    "whatsapp": "WhatsApp", "email": "e-mail", "popup": "aviso dentro do produto",
    "bot_whatsapp": "WhatsApp", "sms": "SMS", "push": "notificação no aplicativo",
}
ROTULOS_DE_ESTRATEGIA = {
    "retry_automatico": "nova tentativa automática de cobrança",
    "mensagem_pagamento": "mensagem com link de pagamento",
}
ROTULOS_DE_METODO = {"pix_automatico": "Pix Automático", "boleto": "boleto", "card": "cartão"}
ROTULOS_DE_CAUSA = {
    "insufficient_funds": "saldo insuficiente", "limit_exceeded": "limite excedido",
    "authorization_revoked": "autorização de recorrência revogada",
    "processing_error": "erro de processamento", "expired_card": "cartão expirado",
    "card_declined": "cartão recusado", "do_not_honor": "recusado pelo emissor",
    "generic_decline": "recusa genérica",
}
ROTULOS_DE_ORIGEM_DATAS = {
    "payday_engine": "previsão de liquidez do cliente",
    "fallback_uniforme": "distribuição uniforme na janela",
}
ROTULOS_DE_MODELO = {
    "failure_classifier": "modelo de diagnóstico de falha de pagamento",
    "risk_scorer_voluntario": "modelo de risco de cancelamento",
    "offer_bandit": "algoritmo de escolha de oferta (Thompson Sampling)",
    "payday_inference": "modelo de previsão de liquidez",
    "anomaly_detector": "detector de anomalia de comportamento",
}


def _inteiro(v):
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return v


def _decimal(v):
    try:
        return f"{float(v):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return v


def _reais(v):
    try:
        return f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return v


def _pct(v):
    try:
        return f"{float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return v


def _data_legivel(iso) -> str:
    """dd/mm/aaaa a partir de ISO-8601 (texto) ou de um `datetime`."""
    try:
        quando = iso if isinstance(iso, datetime) else datetime.fromisoformat(str(iso))
        return quando.strftime("%d/%m/%Y")
    except (TypeError, ValueError):
        return str(iso)


def _rotular(chave, valor, rotulos: dict) -> str:
    f = rotulos.get(chave)
    if f is not None:
        try:
            return f(valor)
        except Exception:                        # noqa: BLE001 — rótulo nunca derruba
            pass
    return f"{chave} = {valor}"


def frase_da_decisao(decidido_em: str, dominio: str, tipo_decisao: str, modelo: str,
                     modelo_versao, entradas: dict, saida: dict,
                     contribuicoes: list | None) -> str:
    """A explicação legível (Art. 20 §1º): critério e procedimento, em pt-BR.

    Forma: "Em <data>, no fluxo de <domínio>, o sistema <o que o tipo faz>:
    <saída legível>. Pesaram, nesta ordem: <contribuições SHAP ou entradas>.
    A decisão foi tomada por <modelo>, versão <x> | pela regra '<regra>'."

    Com `contribuicoes` (modelo com SHAP), a ordem é a do peso e cada item
    diz a direção. Sem, a ordem é a das `entradas` — e a frase diz que veio
    de regra, nomeando-a (`saida["regra"]`), ou do módulo sem contribuições
    por feature (o bandit). Nunca inventa contribuição.
    """
    partes_saida = [_rotular(k, v, ROTULOS_DE_SAIDA) for k, v in saida.items()
                    if k not in ("regra", "motivo_da_regra") and v is not None
                    and not isinstance(v, (dict, list))]
    frase = (f"Em {_data_legivel(decidido_em)}, no fluxo de "
             f"{ROTULOS_DE_DOMINIO.get(dominio, dominio)}, o sistema "
             f"{ROTULOS_DE_TIPO.get(tipo_decisao, tipo_decisao)}: "
             f"{'; '.join(partes_saida) or 'sem saída registrada'}.")

    if contribuicoes:
        itens = []
        for c in contribuicoes:
            nome = c.get("feature")
            valor = entradas.get(nome, c.get("valor"))
            rot = _rotular(nome, valor, ROTULOS_DE_FEATURE) if valor is not None else str(nome)
            direcao = c.get("direcao") or c.get("direction")
            seta = (" (aumentou a chance)" if direcao == "+"
                    else " (reduziu a chance)" if direcao == "-" else "")
            itens.append(f"{rot}{seta}")
        frase += f" Pesaram, nesta ordem: {', '.join(itens)}."
    elif entradas:
        itens = [_rotular(k, v, ROTULOS_DE_FEATURE) for k, v in entradas.items()
                 if not isinstance(v, (dict, list))]
        frase += f" Foram considerados: {', '.join(itens)}."

    if modelo == MODELO_REGRA:
        regra = saida.get("regra") or "regra fixa"
        motivo = saida.get("motivo_da_regra")
        frase += f" A decisão veio da regra '{regra}', sem modelo treinado"
        frase += f": {str(motivo).rstrip('.')}." if motivo else "."
    else:
        nome = ROTULOS_DE_MODELO.get(modelo, modelo)
        frase += f" A decisão foi tomada pelo {nome} ({modelo})"
        frase += f", versão {modelo_versao}." if modelo_versao else ", sem versão de artefato declarada."
        if not contribuicoes and modelo in ("offer_bandit",):
            frase += " Este algoritmo não produz contribuições por feature."
    return frase


# ── Montagem e registro ──────────────────────────────────────────────────

def decisao(tenant_id, sujeito_id, dominio: str, tipo_decisao: str, modelo: str,
            entradas: dict, saida: dict, modelo_versao: str | None = None,
            contribuicoes: list | None = None, decidido_em: str | None = None) -> dict:
    """Monta a linha da trilha NO MOMENTO DA DECISÃO. Não grava.

    Os nós do grafo chamam isto onde decidem e acumulam o resultado em
    `state["decisoes"]`; a gravação acontece uma vez por ciclo
    (`registrar_decisoes`), ou uma vez por lote no disparo em massa — pelo
    mesmo motivo de `registrar_ciclos`. `decidido_em` é carimbado AQUI, não
    na gravação. `entradas`, `saida` e `contribuicoes` passam por
    `_sem_dado_cru`. Nunca levanta: se não conseguir montar, devolve um dict
    marcado `_falha` que `registrar_decisoes` ignora com log.
    """
    try:
        if dominio not in DOMINIOS or tipo_decisao not in TIPOS_DECISAO:
            raise ValueError(f"dominio/tipo inválidos: {dominio}/{tipo_decisao}")
        entradas = _sem_dado_cru(dict(entradas or {}))
        saida = _sem_dado_cru(dict(saida or {}))
        contribuicoes = _sem_dado_cru(list(contribuicoes)) if contribuicoes else None
        quando = decidido_em or _agora()
        return {
            "tenant_id": tenant_id or TENANT_PADRAO,
            "sujeito_id": str(sujeito_id or ""),
            "decidido_em": quando,
            "dominio": dominio,
            "tipo_decisao": tipo_decisao,
            "modelo": modelo or MODELO_REGRA,
            "modelo_versao": modelo_versao,
            "entradas": entradas,
            "saida": saida,
            "explicacao": frase_da_decisao(quando, dominio, tipo_decisao,
                                           modelo or MODELO_REGRA, modelo_versao,
                                           entradas, saida, contribuicoes),
            "contribuicoes": contribuicoes,
        }
    except Exception as e:                       # noqa: BLE001 — best effort declarado
        print(f"[ART20] Falha ao montar decisão {dominio}/{tipo_decisao}: {e}")
        return {"_falha": str(e), "tenant_id": tenant_id, "sujeito_id": sujeito_id,
                "dominio": dominio, "tipo_decisao": tipo_decisao}


def anotar_decisao(state: dict, d: dict) -> dict:
    """`{**state, "decisoes": [...anteriores, d]}` — o jeito de um nó do grafo
    acumular a decisão sem tocar no resto do state."""
    return {**state, "decisoes": list(state.get("decisoes") or []) + [d]}


_INSERT_DECISAO = """INSERT INTO decisoes_automatizadas (
                        tenant_id, sujeito_id, decidido_em, dominio, tipo_decisao,
                        modelo, modelo_versao, entradas, saida, explicacao,
                        contribuicoes, hash_anterior, hash_linha)
                     VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?,?)"""


def _ultimo_hash(conn, tenant_id: str) -> str:
    """O hash da ponta da cadeia deste tenant, ou `HASH_GENESE` se não há
    linha. Lido DENTRO da transação de escrita (`BEGIN IMMEDIATE`), e no lote
    é lido de novo a cada linha — assim a linha k aponta para a k-1 do mesmo
    lote, não todas para o antecessor de antes do lote."""
    linha = conn.execute(
        "SELECT hash_linha FROM decisoes_automatizadas WHERE tenant_id = ? "
        "ORDER BY id DESC LIMIT 1", (tenant_id,)).fetchone()
    return linha["hash_linha"] if linha else HASH_GENESE


def _inserir_decisao(conn, d: dict) -> int:
    entradas_json, saida_json = _json(d["entradas"]), _json(d["saida"])
    contrib_json = _json(d["contribuicoes"]) if d.get("contribuicoes") else None
    anterior = _ultimo_hash(conn, d["tenant_id"])
    h = _hash_da_linha(d["tenant_id"], d["sujeito_id"], d["decidido_em"], d["dominio"],
                       d["tipo_decisao"], d["modelo"], d.get("modelo_versao"),
                       entradas_json, saida_json, d["explicacao"], contrib_json, anterior)
    cur = conn.execute(_INSERT_DECISAO, (
        d["tenant_id"], d["sujeito_id"], d["decidido_em"], d["dominio"], d["tipo_decisao"],
        d["modelo"], d.get("modelo_versao"), entradas_json, saida_json, d["explicacao"],
        contrib_json, anterior, h))
    return cur.lastrowid


def registrar_decisoes(decisoes: list[dict]) -> list[int | None]:
    """Grava N decisões numa transação só. Devolve os ids (None para as que
    falharam ou vieram marcadas `_falha`).

    `BEGIN IMMEDIATE`: o `hash_anterior` é lido e a linha é gravada sob a
    mesma trava de escrita, para dois processos não encadearem no mesmo
    antecessor. Se mesmo assim dois gravadores chegarem com o mesmo
    antecessor (leitura fora da trava, outro banco), o índice único
    `uq_decisao_elo` recusa o segundo: nada é gravado desse lote, e o log
    diz BIFURCAÇÃO EVITADA — a cadeia continua linear. Best effort: falha na
    transação devolve None para todas, com log — as decisões já foram
    tomadas; o que falhou foi o registro.
    """
    validas = [d for d in decisoes or [] if d and not d.get("_falha")]
    if not validas:
        return [None] * len(decisoes or [])
    ids: dict = {}
    try:
        conn = _conectar()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for d in validas:
                ids[id(d)] = _inserir_decisao(conn, d)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except sqlite3.IntegrityError as e:
        print(f"[ART20] BIFURCAÇÃO EVITADA — {len(validas)} decisão(ões) NÃO gravada(s): "
              f"outro gravador encadeou no mesmo antecessor primeiro ({e})")
        return [None] * len(decisoes)
    except Exception as e:                       # noqa: BLE001
        print(f"[ART20] Falha ao registrar {len(validas)} decisão(ões): {e}")
        return [None] * len(decisoes)
    return [ids.get(id(d)) for d in decisoes]


def registrar_decisao(d: dict) -> int | None:
    """Uma decisão só. Mesma linha, mesma transação curta."""
    return registrar_decisoes([d])[0]


# ── Leitura (por tenant, sempre) ─────────────────────────────────────────

def _linha_da_trilha(l) -> dict:
    d = dict(l)
    for campo in ("entradas", "saida", "contribuicoes"):
        if d.get(campo) is not None:
            try:
                d[campo] = json.loads(d[campo])
            except (TypeError, ValueError):
                pass
    return d


def decisoes_do_sujeito(tenant_id: str, sujeito_id: str, limite: int = 50,
                        antes_de: int | None = None) -> list[dict]:
    """As decisões deste sujeito NESTE tenant, mais recentes primeiro.

    Paginação por `antes_de` (id): a página seguinte pede `antes_de = id da
    última linha recebida`. Sujeito de outro tenant devolve `[]`, igual a
    sujeito inexistente — quem chama não distingue os dois, e é assim que a
    rota responde 404 nos dois casos. Nunca levanta.
    """
    try:
        with _conectar() as conn:
            sql = ("SELECT * FROM decisoes_automatizadas "
                   "WHERE tenant_id = ? AND sujeito_id = ?")
            params: list = [tenant_id, sujeito_id]
            if antes_de is not None:
                sql += " AND id < ?"
                params.append(int(antes_de))
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(int(limite))
            return [_linha_da_trilha(l) for l in conn.execute(sql, params).fetchall()]
    except Exception as e:                       # noqa: BLE001
        print(f"[ART20] Falha ao ler decisões: {e}")
        return []


def verificar_cadeia(tenant_id: str) -> dict:
    """Percorre a trilha do tenant e diz se está íntegra e, se não, onde quebrou.

    Duas verificações por linha, na ordem do `id`:
      ELO      `hash_anterior` == `hash_linha` da linha anterior. Alterar uma
               linha no meio (e recalcular o hash dela, ou não) quebra o elo
               da linha SEGUINTE — é `quebra_em_id`.
      PRÓPRIO  `hash_linha` == sha256 recalculado do conteúdo gravado. Uma
               edição ingênua (sem recalcular o hash) aparece aqui, na
               própria linha — é `hash_proprio_invalido_em_id`.

      BIFURCAÇÃO duas linhas com o mesmo `hash_anterior` — dois ramos. O
               índice único impede isso numa base que já nasceu com ele; a
               verificação existe para a base que não nasceu, e aponta a
               segunda linha do par em `bifurcacao_em_id`.

    `inicio_truncado`: a primeira linha ainda existente não aponta para
    `HASH_GENESE` — o esperado depois de `apagar_trilha_expirada`; a cadeia
    continua verificável dali em diante. LIMITAÇÃO conhecida de cadeia sem
    âncora externa: a ÚLTIMA linha alterada com o hash recalculado não tem
    quem a denuncie; a âncora (publicar o hash da ponta) é passo posterior.
    Nunca levanta.
    """
    resultado = {"tenant_id": tenant_id, "integra": True, "linhas": 0,
                 "quebra_em_id": None, "hash_proprio_invalido_em_id": None,
                 "bifurcacao_em_id": None, "inicio_truncado": False, "ultimo_hash": None}
    try:
        with _conectar() as conn:
            linhas = conn.execute(
                "SELECT * FROM decisoes_automatizadas WHERE tenant_id = ? ORDER BY id",
                (tenant_id,)).fetchall()
    except Exception as e:                       # noqa: BLE001
        print(f"[ART20] Falha ao verificar cadeia: {e}")
        return {**resultado, "integra": False, "erro": str(e)}

    anterior = None
    antecessores_vistos: set = set()
    for i, l in enumerate(linhas):
        resultado["linhas"] += 1
        if l["hash_anterior"] in antecessores_vistos and resultado["bifurcacao_em_id"] is None:
            resultado["bifurcacao_em_id"] = l["id"]
            resultado["integra"] = False
        antecessores_vistos.add(l["hash_anterior"])
        if i == 0 and l["hash_anterior"] != HASH_GENESE:
            resultado["inicio_truncado"] = True
        elif i > 0 and l["hash_anterior"] != anterior and resultado["quebra_em_id"] is None:
            resultado["quebra_em_id"] = l["id"]
            resultado["integra"] = False
        recalculado = _hash_da_linha(
            l["tenant_id"], l["sujeito_id"], l["decidido_em"], l["dominio"],
            l["tipo_decisao"], l["modelo"], l["modelo_versao"], l["entradas"],
            l["saida"], l["explicacao"], l["contribuicoes"], l["hash_anterior"])
        if recalculado != l["hash_linha"] and resultado["hash_proprio_invalido_em_id"] is None:
            resultado["hash_proprio_invalido_em_id"] = l["id"]
            resultado["integra"] = False
        # O elo da próxima linha é conferido contra o hash RECALCULADO desta,
        # não contra o gravado: assim uma alteração na linha 3 aparece no elo
        # da 4 mesmo que o hash da 3 tenha sido regravado junto.
        anterior = recalculado
        resultado["ultimo_hash"] = l["hash_linha"]
    return resultado


# ── Decreto 11.034/2022: a trilha como evidência de que nada obstrui o cancelamento

# Saídas que, se aparecessem numa decisão gravada, significariam que o agente
# adiou, dificultou ou condicionou um cancelamento — o que o Decreto
# 11.034/2022 (SAC) proíbe e o produto se comprometeu a nunca fazer.
OFERTAS_QUE_OBSTRUEM = frozenset({"consulta_cs"})          # o braço que era "fale com o CS antes"
ESTRATEGIAS_PERMITIDAS = frozenset(ROTULOS_DE_ESTRATEGIA)   # retry automático | mensagem com link
CHAVES_QUE_OBSTRUEM = frozenset({
    "bloqueia_cancelamento", "condiciona_cancelamento", "adia_cancelamento",
    "retencao_obrigatoria", "exige_contato_humano", "prazo_para_cancelar",
})


def decisoes_que_obstruem_cancelamento(tenant_id: str) -> list[dict]:
    """As decisões gravadas deste tenant que violariam o Decreto 11.034/2022.

    Lista vazia é o esperado, e é o que o teste do decreto afirma. Uma
    decisão viola se: o `tipo_decisao` está fora dos quatro conhecidos; a
    oferta é `consulta_cs` (o banco já trava `offer_type <> 'consulta_cs'` no
    `ciclos_retencao`; aqui a mesma invariante vale para a trilha); o canal
    é humano (`config.CANAIS_HUMANOS`); a estratégia de cobrança não é uma
    das duas automáticas; ou a saída carrega alguma chave de
    `CHAVES_QUE_OBSTRUEM`. Nunca levanta.
    """
    from ..config import CANAIS_HUMANOS          # tardio: config não conhece este módulo

    violacoes = []
    try:
        with _conectar() as conn:
            linhas = conn.execute(
                "SELECT id, tipo_decisao, saida FROM decisoes_automatizadas "
                "WHERE tenant_id = ? ORDER BY id", (tenant_id,)).fetchall()
    except Exception as e:                       # noqa: BLE001
        print(f"[ART20] Falha ao varrer a trilha: {e}")
        return [{"id": None, "motivo": f"trilha ilegível: {e}"}]
    for l in linhas:
        try:
            saida = json.loads(l["saida"]) if l["saida"] else {}
        except (TypeError, ValueError):
            saida = {}
        motivo = None
        if l["tipo_decisao"] not in TIPOS_DECISAO:
            motivo = f"tipo_decisao desconhecido: {l['tipo_decisao']}"
        elif saida.get("offer_type") in OFERTAS_QUE_OBSTRUEM:
            motivo = f"oferta que condiciona o cancelamento: {saida.get('offer_type')}"
        elif saida.get("channel") in CANAIS_HUMANOS:
            motivo = f"canal humano: {saida.get('channel')}"
        elif "estrategia" in saida and saida["estrategia"] not in ESTRATEGIAS_PERMITIDAS:
            motivo = f"estratégia fora das automáticas: {saida.get('estrategia')}"
        else:
            chaves = CHAVES_QUE_OBSTRUEM & {str(k).lower() for k in saida}
            if chaves:
                motivo = f"saída com marca de obstrução: {', '.join(sorted(chaves))}"
        if motivo:
            violacoes.append({"id": l["id"], "tipo_decisao": l["tipo_decisao"], "motivo": motivo})
    return violacoes


def apagar_trilha_expirada(agora: datetime | None = None, tenant_id: str | None = None) -> int:
    """RETENÇÃO: apaga as decisões com mais de `RETENCAO_TRILHA_DIAS`.

    AINDA NÃO É CHAMADA POR NINGUÉM, de propósito: o prazo é decisão do Crai
    (ver a constante) e precisa ser confirmado antes de existir um agendador.
    É a ÚNICA operação que remove linha desta tabela, e remove só pela ponta
    antiga — o que sobra continua encadeado (`verificar_cadeia` marca
    `inicio_truncado`). `tenant_id=None` aplica a todos os tenants; com
    tenant, só a ele. Devolve quantas linhas saíram. Ao contrário do
    registro, uma falha aqui LEVANTA: retenção que falha em silêncio é
    dado guardado além do prazo sem ninguém saber.
    """
    momento = agora or datetime.now(timezone.utc)
    limite = (momento - timedelta(days=RETENCAO_TRILHA_DIAS)).isoformat(timespec="seconds")
    sql = "DELETE FROM decisoes_automatizadas WHERE decidido_em < ?"
    params: list = [limite]
    if tenant_id is not None:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    with _conectar() as conn:
        return int(conn.execute(sql, params).rowcount)

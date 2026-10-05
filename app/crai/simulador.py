"""crai/simulador.py — o pagador fictício, a verdade escondida e o PSP simulado (Rodada 3).

A página "Simulação do gateway" mostra o sistema agindo sobre um cliente que não
existe. Este módulo é o lado de FORA do sistema nessa demonstração: o cliente
fictício, o banco dele e o relógio. O lado de dentro é o pipeline de sempre
(`agent/workflow.py`, os modelos, a regra do BACEN), rodando sobre os arquivos
de simulação da empresa (`crai/ambiente.py`).

A VERDADE ESCONDIDA. Quem cria o cliente fictício define o que só o banco dele
sabe: em quantos dias o dinheiro entra, a chance de a cobrança passar quando há
saldo, e se ele vai revogar a autorização. Isso mora na tabela
`simulacao_verdade`, SEPARADA do que o sistema enxerga (`simulacao_pagadores`:
nome, mensalidade e perfil). Só duas funções deste arquivo leem a verdade:
`psp_responde` (o banco respondendo a uma cobrança) e `sem_crai` (o que teria
acontecido sem o sistema). Nenhum módulo do pipeline importa este arquivo nem
abre essa tabela, e há teste que prova: dois clientes iguais no que o sistema vê
e opostos na verdade recebem exatamente o mesmo diagnóstico.

O PSP SIMULADO responde a cada cobrança conforme a verdade:

    revogação   se o cliente vai revogar, a 1ª tentativa volta com `MD01`;
    saldo       antes do dia em que o dinheiro entra, volta `AM04`;
    sorteio     com saldo, a cobrança passa com a chance definida. O sorteio é
                determinístico por cliente e por tentativa: a mesma simulação,
                repetida, dá o mesmo resultado. Com chance 100% sempre passa;
                com 0%, nunca. Se não passa, volta `AB03` (falha técnica).

A RESPOSTA À MENSAGEM. `DIAS_PARA_RESPONDER` dias depois de a mensagem sair, o
cliente fictício paga se já tem saldo e o sorteio dele deixar (a chance de
pagar mais 20 pontos: a mensagem ajuda). Se não pagar, o ciclo segue como
qualquer ciclo real: fica `mensagem_enviada` até o prazo de recuperação e vira
`perdido` pela varredura.

"SEM A CRAI". A regra da comparação é a do Pix Automático, como está em
`dunning/pix_automatico_retry.py`: as duas janelas automáticas do dia do
vencimento rodam sob responsabilidade do banco do pagador, e a recobrança
dentro dos 7 dias só existe se o recebedor a pedir. Sem a CRAI ninguém pede: se
o dinheiro não está na conta no dia do vencimento, a cobrança se perde.

O FORMULÁRIO RECUSA DADO QUE PAREÇA REAL (`parece_dado_real`): e-mail, CPF,
CNPJ, telefone, chave Pix aleatória e sequência longa de números. O cliente da
simulação é inventado, e a simulação não é lugar de colar dado de gente de
verdade.

Tudo aqui pressupõe que quem chama está dentro de `ambiente.em_simulacao`: as
tabelas vivem no arquivo de simulação da empresa, ao lado do ciclo simulado.
"""

import gc
import hashlib
import json
import logging
import math
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from . import ambiente
from .churn_voluntary import offer_bandit, retention_log
from .dunning import ciclo_cobranca, recovery_log, retry_state

logger = logging.getLogger(__name__)

# Os ids de ciclo simulado saem nas rotas somados a este número: o ciclo real 7
# e o ciclo simulado 7 são coisas diferentes, e a tela usa o id para abrir o
# detalhe. Cabe com folga num inteiro seguro de JavaScript (2**53).
ID_DA_SIMULACAO = 9_000_000_000_000

PERFIS = ("clt", "pj", "freelancer")
NOME_MIN, NOME_MAX = 3, 40
MENSALIDADE_MIN, MENSALIDADE_MAX = 10.0, 50_000.0
MRR_MIN, MRR_MAX = 10.0, 100_000.0
DIAS_ATE_SALDO_MAX = 15
DIAS_PARA_RESPONDER = 2
BONUS_DA_MENSAGEM = 0.2

CODIGO_SALDO = "AM04"       # saldo insuficiente
CODIGO_REVOGADA = "MD01"    # autorização revogada
CODIGO_TECNICO = "AB03"     # falha técnica: a cobrança não passou mesmo com saldo

PAGA = "paga"
FALHOU = "falhou"
RESPOSTA_PAGOU = "pagou"
RESPOSTA_NAO_PAGOU = "nao_pagou"

# Os sinais do cliente fictício em risco. Cada um vira EXATAMENTE um dado que o
# sistema recebe de um cliente de verdade (`props_dos_sinais`), e mais nada.
SINAIS = ("uso_caiu", "tickets", "atraso", "abriu_cancelamento")
# O evento do SDK que o sinal "abriu a página de cancelamento" dispara.
EVENTO_DE_INTENCAO = "Cancellation Page Viewed"

_DDL = """
CREATE TABLE IF NOT EXISTS simulacao_relogio (
    tenant_id   TEXT PRIMARY KEY,
    agora       TEXT NOT NULL,
    iniciado_em TEXT NOT NULL
);

-- O que o SISTEMA vê do cliente fictício, e o que aconteceu com a cobrança dele.
CREATE TABLE IF NOT EXISTS simulacao_pagadores (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           TEXT    NOT NULL,
    id_recorrencia      TEXT    NOT NULL UNIQUE,
    nome                TEXT    NOT NULL,
    mensalidade         REAL    NOT NULL,
    perfil              TEXT    NOT NULL,
    criado_em           TEXT    NOT NULL,
    cobrado_em          TEXT,
    cobranca_inicial    TEXT,
    codigo_inicial      TEXT,
    ciclo_id            INTEGER,
    resposta_a_mensagem TEXT,
    resposta_em         TEXT,
    diagnostico         TEXT
);

-- A VERDADE ESCONDIDA. Só `psp_responde` e `sem_crai` leem esta tabela.
CREATE TABLE IF NOT EXISTS simulacao_verdade (
    pagador_id     INTEGER PRIMARY KEY REFERENCES simulacao_pagadores(id),
    dias_ate_saldo INTEGER NOT NULL,
    chance_pagar   REAL    NOT NULL,
    vai_revogar    INTEGER NOT NULL
);

-- O cliente fictício em risco (voluntário). `propensao` é a verdade escondida
-- dele: a chance de aceitar cada oferta. O aceite sai dela, nunca do bandit.
CREATE TABLE IF NOT EXISTS simulacao_retencoes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   TEXT    NOT NULL,
    user_id     TEXT    NOT NULL,
    nome        TEXT    NOT NULL,
    mrr         REAL    NOT NULL,
    sinais      TEXT    NOT NULL,
    propensao   TEXT    NOT NULL,
    faixa       TEXT,
    motivo      TEXT,
    decidido_por TEXT,
    oferta      TEXT,
    canal       TEXT,
    aceitou     INTEGER,
    criado_em   TEXT    NOT NULL
);
"""


class SimulacaoInvalida(ValueError):
    def __init__(self, campo: str, detalhe: str, motivo: str = "simulacao_invalida"):
        self.campo = campo
        self.detalhe = detalhe
        self.motivo = motivo
        super().__init__(f"{campo}: {detalhe}")


# ── O formulário: só dado inventado ───────────────────────────────────────

_PADROES_DE_DADO_REAL = (
    (re.compile(r"@"), "parece um e-mail"),
    (re.compile(r"\d{3}\.?\d{3}\.?\d{3}-?\d{2}"), "parece um CPF"),
    (re.compile(r"\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}"), "parece um CNPJ"),
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I),
     "parece uma chave Pix"),
    (re.compile(r"(\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}-?\d{4}"), "parece um telefone"),
    (re.compile(r"\d{6,}"), "tem um número longo, que pode ser um dado real"),
)


def parece_dado_real(texto: str) -> Optional[str]:
    """Por que este texto parece dado de gente de verdade, ou None."""
    for padrao, motivo in _PADROES_DE_DADO_REAL:
        if padrao.search(texto or ""):
            return motivo
    return None


def _nome(corpo: dict) -> str:
    nome = corpo.get("nome")
    if not isinstance(nome, str) or not NOME_MIN <= len(nome.strip()) <= NOME_MAX \
            or "\n" in nome or "\r" in nome:
        raise SimulacaoInvalida("nome", f"esperado um nome inventado de {NOME_MIN} a "
                                        f"{NOME_MAX} caracteres")
    suspeito = parece_dado_real(nome)
    if suspeito:
        raise SimulacaoInvalida("nome", f"o nome {suspeito}; aqui só entra um nome inventado",
                                motivo="dado_que_parece_real")
    return nome.strip()


def _numero(corpo: dict, campo: str, minimo: float, maximo: float) -> float:
    valor = corpo.get(campo)
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) \
            or valor != valor or not minimo <= valor <= maximo:
        raise SimulacaoInvalida(campo, f"esperado número entre {minimo:g} e {maximo:g}")
    return float(valor)


def _so_estes_campos(corpo, aceitos: tuple, onde: str = "corpo") -> dict:
    if not isinstance(corpo, dict):
        raise SimulacaoInvalida(onde, "esperado um objeto")
    extras = sorted(set(corpo) - set(aceitos))
    if extras:
        raise SimulacaoInvalida(extras[0], f"campo não reconhecido; aceitos: {', '.join(aceitos)}",
                                motivo="campo_desconhecido")
    return corpo


def validar_cliente(corpo) -> dict:
    """O cliente fictício da cobrança: `{nome, mensalidade, perfil, verdade:
    {dias_ate_saldo, chance_pagar, vai_revogar}}`. Nenhum outro campo é aceito:
    não há onde pôr CPF, conta, cartão, telefone, e-mail ou chave Pix."""
    corpo = _so_estes_campos(corpo, ("nome", "mensalidade", "perfil", "verdade"))
    perfil = corpo.get("perfil")
    if perfil not in PERFIS:
        raise SimulacaoInvalida("perfil", f"esperado um de {', '.join(PERFIS)}")
    verdade = _so_estes_campos(corpo.get("verdade"),
                               ("dias_ate_saldo", "chance_pagar", "vai_revogar"), "verdade")
    dias = verdade.get("dias_ate_saldo")
    if isinstance(dias, bool) or not isinstance(dias, int) or not 0 <= dias <= DIAS_ATE_SALDO_MAX:
        raise SimulacaoInvalida("verdade.dias_ate_saldo",
                                f"esperado inteiro entre 0 e {DIAS_ATE_SALDO_MAX}")
    revogar = verdade.get("vai_revogar")
    if not isinstance(revogar, bool):
        raise SimulacaoInvalida("verdade.vai_revogar", "esperado true ou false")
    try:
        chance = _numero(verdade, "chance_pagar", 0.0, 1.0)
    except SimulacaoInvalida as e:
        raise SimulacaoInvalida("verdade.chance_pagar", e.detalhe) from None
    return {"nome": _nome(corpo),
            "mensalidade": round(_numero(corpo, "mensalidade", MENSALIDADE_MIN, MENSALIDADE_MAX), 2),
            "perfil": perfil,
            "verdade": {"dias_ate_saldo": dias, "chance_pagar": chance, "vai_revogar": revogar}}


def validar_cliente_em_risco(corpo) -> dict:
    """O cliente fictício em risco: `{nome, mrr, sinais: {uso_caiu, tickets,
    atraso, abriu_cancelamento}, propensao: {<oferta>: 0..1}}`, com uma
    propensão para CADA oferta que o sistema faz (`offer_bandit.OFFERS`)."""
    corpo = _so_estes_campos(corpo, ("nome", "mrr", "sinais", "propensao"))
    sinais = _so_estes_campos(corpo.get("sinais"), SINAIS, "sinais")
    for s in SINAIS:
        if not isinstance(sinais.get(s), bool):
            raise SimulacaoInvalida(f"sinais.{s}", "esperado true ou false")
    propensao = _so_estes_campos(corpo.get("propensao"), tuple(offer_bandit.OFFERS), "propensao")
    limpa = {}
    for oferta in offer_bandit.OFFERS:
        try:
            limpa[oferta] = _numero(propensao, oferta, 0.0, 1.0)
        except SimulacaoInvalida as e:
            raise SimulacaoInvalida(f"propensao.{oferta}", e.detalhe) from None
    return {"nome": _nome(corpo), "mrr": round(_numero(corpo, "mrr", MRR_MIN, MRR_MAX), 2),
            "sinais": {s: sinais[s] for s in SINAIS}, "propensao": limpa}


# ── Armazenamento: os arquivos de simulação da empresa ────────────────────

def _conectar():
    if not ambiente.simulacao_ativa():
        raise RuntimeError("o simulador só grava dentro de `ambiente.em_simulacao`")
    conn = ciclo_cobranca._conectar()
    conn.executescript(_DDL)
    return conn


def _texto(momento: datetime) -> str:
    return momento.isoformat(timespec="seconds")


def arquivos(tenant_id: str) -> list:
    """Os arquivos de simulação desta empresa (existam ou não)."""
    with ambiente.fora_da_simulacao():
        reais = [ciclo_cobranca.caminho_do_banco(), recovery_log.caminho_do_banco(),
                 retention_log.caminho_do_banco(), retry_state.caminho_do_estado(),
                 Path(offer_bandit.STATE_PATH)]
    vistos, saida = set(), []
    for real in reais:
        simulado = ambiente.caminho_simulado(Path(real), tenant_id)
        if str(simulado) not in vistos:
            vistos.add(str(simulado))
            saida.append(simulado)
    return saida


def existe(tenant_id: str) -> bool:
    """A empresa tem alguma simulação gravada? (Sem isto, ler a simulação de uma
    empresa que nunca simulou criaria os arquivos dela só por ter olhado.)"""
    return any(p.exists() for p in arquivos(tenant_id))


def limpar(tenant_id: str) -> int:
    """Apaga TUDO o que é fictício desta empresa: os arquivos de simulação dela
    (ciclos, tentativas, mensagens, dataset, trilha, plano de tentativas e a
    cópia do bandit) e os auxiliares do SQLite. Devolve quantos arquivos saíram.
    Nenhum arquivo real é tocado: os nomes vêm de `ambiente.caminho_simulado`."""
    alvos = []
    for arquivo in arquivos(tenant_id):
        for alvo in (arquivo, *(arquivo.with_name(arquivo.name + sufixo)
                                for sufixo in ("-wal", "-shm", "-journal"))):
            if f".{ambiente.MARCA}.{ambiente.parte_do_nome(tenant_id)}." not in alvo.name:
                raise RuntimeError(f"recusado: {alvo.name} não é arquivo de simulação")
            alvos.append(alvo)
    # No Windows um arquivo aberto não pode ser apagado, e os módulos de
    # armazenamento deixam o fechamento da conexão do SQLite para o coletor de
    # lixo. A coleta aqui fecha as conexões que já não têm dono; se ainda assim
    # o arquivo estiver em uso (outra requisição no meio do caminho), a rota
    # responde 409 e a pessoa tenta de novo.
    gc.collect()
    apagados = 0
    for alvo in alvos:
        if alvo.exists():
            try:
                alvo.unlink()
            except PermissionError:
                gc.collect()
                alvo.unlink()
            apagados += 1
    # Cada módulo guarda a marca "schema já garantido" por arquivo: o arquivo
    # sumiu, a marca some junto.
    ciclo_cobranca.esquecer_schema_garantido()
    offer_bandit.esquecer_copia_da_simulacao(tenant_id)
    logger.info("[SIMULACAO] tenant=%s: simulação limpa (%d arquivo(s))", tenant_id, apagados)
    return apagados


# Uma simulação em que ninguém mexe há mais que isto é apagada pelo relógio
# diário (Rodada 4). "Parada" é pelo tempo de verdade (a última gravação nos
# arquivos dela), e não pelo relógio simulado.
DIAS_DE_SIMULACAO_PARADA = 30


def _simulacoes_em_disco() -> dict:
    """`{parte do nome: [arquivos]}` de toda simulação gravada, de qualquer
    empresa, achada pelo NOME dos arquivos ao lado dos reais."""
    with ambiente.fora_da_simulacao():
        reais = [ciclo_cobranca.caminho_do_banco(), recovery_log.caminho_do_banco(),
                 retention_log.caminho_do_banco(), retry_state.caminho_do_estado(),
                 Path(offer_bandit.STATE_PATH)]
    grupos: dict = {}
    for real in {str(Path(r)): Path(r) for r in reais}.values():
        prefixo, sufixo = f"{real.stem}.{ambiente.MARCA}.", real.suffix
        if not real.parent.is_dir():
            continue
        for arquivo in real.parent.iterdir():
            nome = arquivo.name
            for extra in ("", "-wal", "-shm", "-journal"):
                fim = sufixo + extra
                if nome.startswith(prefixo) and nome.endswith(fim) and len(nome) > len(prefixo) + len(fim):
                    parte = nome[len(prefixo):len(nome) - len(fim)]
                    grupos.setdefault(parte, []).append(arquivo)
                    break
    return grupos


def _tenant_dos_arquivos(arquivos_do_grupo: list) -> Optional[str]:
    """De qual empresa é esta simulação, lido do relógio gravado nela (o nome do
    arquivo pode ser o sha256 do tenant). None se não der para ler."""
    for arquivo in arquivos_do_grupo:
        if arquivo.suffix != ".db":
            continue
        try:
            conn = sqlite3.connect(f"file:{arquivo.as_posix()}?mode=ro", uri=True)
            try:
                linha = conn.execute("SELECT tenant_id FROM simulacao_relogio LIMIT 1").fetchone()
            finally:
                conn.close()
            if linha and linha[0]:
                return linha[0]
        except sqlite3.Error:
            continue
    return None


def apagar_paradas(agora: datetime, dias: int = DIAS_DE_SIMULACAO_PARADA) -> int:
    """RETENÇÃO: apaga as simulações em que nenhum arquivo é gravado há mais de
    `dias` dias. Devolve quantas simulações (empresas) saíram. Só toca arquivo
    com a marca de simulação no nome; os reais não entram na lista. Uma falha
    LEVANTA, como no resto do expurgo."""
    if isinstance(dias, bool) or not isinstance(dias, int) or dias < 1:
        raise ValueError(f"dias precisa ser inteiro >= 1; recebido {dias!r}")
    limite = agora.timestamp() - dias * 86400
    apagadas = 0
    for parte, do_grupo in sorted(_simulacoes_em_disco().items()):
        existentes = [a for a in do_grupo if a.exists()]
        if not existentes or max(a.stat().st_mtime for a in existentes) >= limite:
            continue
        tenant_id = _tenant_dos_arquivos(existentes)
        if tenant_id is not None and ambiente.parte_do_nome(tenant_id) == parte:
            limpar(tenant_id)                     # o caminho de sempre: arquivos e memória
        else:
            gc.collect()
            for alvo in existentes:
                if f".{ambiente.MARCA}.{parte}." not in alvo.name:
                    raise RuntimeError(f"recusado: {alvo.name} não é arquivo de simulação")
                alvo.unlink()
            ciclo_cobranca.esquecer_schema_garantido()
        apagadas += 1
        logger.info("[SIMULACAO] simulação parada há mais de %d dias apagada (%s)", dias, parte)
    return apagadas


# ── O relógio da empresa ──────────────────────────────────────────────────

def relogio(tenant_id: str) -> Optional[dict]:
    """`{agora, iniciado_em}` do relógio simulado desta empresa, ou None."""
    conn = _conectar()
    try:
        linha = conn.execute("SELECT agora, iniciado_em FROM simulacao_relogio WHERE tenant_id = ?",
                             (tenant_id,)).fetchone()
    finally:
        conn.close()
    if linha is None:
        return None
    return {"agora": datetime.fromisoformat(linha["agora"]),
            "iniciado_em": datetime.fromisoformat(linha["iniciado_em"])}


def acertar_relogio(tenant_id: str, agora: datetime) -> None:
    """Grava o instante do relógio simulado. Ele só anda para a frente."""
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        atual = conn.execute("SELECT agora FROM simulacao_relogio WHERE tenant_id = ?",
                             (tenant_id,)).fetchone()
        if atual is None:
            conn.execute("INSERT INTO simulacao_relogio (tenant_id, agora, iniciado_em) "
                         "VALUES (?, ?, ?)", (tenant_id, _texto(agora), _texto(agora)))
        elif _texto(agora) > atual["agora"]:
            conn.execute("UPDATE simulacao_relogio SET agora = ? WHERE tenant_id = ?",
                         (_texto(agora), tenant_id))
        conn.commit()
    finally:
        conn.close()


# ── O pagador fictício ────────────────────────────────────────────────────

def _id_recorrencia(conn, tenant_id: str, nome: str) -> str:
    """`RN_sim_<10 hex>_<n>`: o mesmo nome, na mesma empresa, dá a mesma base, e
    `n` conta quantos clientes com esse nome a simulação já teve. Depois de
    limpar, a mesma simulação repetida dá o mesmo id, e portanto o mesmo
    diagnóstico (o perfil sintético do sistema sai do id)."""
    base = hashlib.sha256(f"{tenant_id}|{nome.lower()}".encode("utf-8")).hexdigest()[:10]
    usados = conn.execute(
        "SELECT COUNT(*) FROM simulacao_pagadores WHERE tenant_id = ? AND id_recorrencia LIKE ?",
        (tenant_id, f"RN_sim_{base}_%")).fetchone()[0]
    return f"RN_sim_{base}_{int(usados) + 1}"


def criar_pagador(tenant_id: str, cliente: dict, agora: datetime) -> dict:
    """Grava o cliente fictício (já validado): o que o sistema vê numa tabela, a
    verdade escondida em outra."""
    verdade = cliente["verdade"]
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        rec = _id_recorrencia(conn, tenant_id, cliente["nome"])
        cur = conn.execute(
            "INSERT INTO simulacao_pagadores (tenant_id, id_recorrencia, nome, mensalidade, "
            "perfil, criado_em) VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, rec, cliente["nome"], cliente["mensalidade"], cliente["perfil"],
             _texto(agora)))
        conn.execute(
            "INSERT INTO simulacao_verdade (pagador_id, dias_ate_saldo, chance_pagar, vai_revogar) "
            "VALUES (?, ?, ?, ?)",
            (cur.lastrowid, verdade["dias_ate_saldo"], verdade["chance_pagar"],
             1 if verdade["vai_revogar"] else 0))
        conn.commit()
        return pagador_por_id(cur.lastrowid)
    finally:
        conn.close()


def _pagador(linha) -> Optional[dict]:
    if linha is None:
        return None
    p = dict(linha)
    p["diagnostico"] = json.loads(p["diagnostico"]) if p.get("diagnostico") else None
    return p


def pagador_por_id(pagador_id: int) -> Optional[dict]:
    conn = _conectar()
    try:
        return _pagador(conn.execute("SELECT * FROM simulacao_pagadores WHERE id = ?",
                                     (pagador_id,)).fetchone())
    finally:
        conn.close()


def pagador_atual(tenant_id: str) -> Optional[dict]:
    """O cliente fictício mais recente desta empresa: é o que a página mostra."""
    conn = _conectar()
    try:
        return _pagador(conn.execute(
            "SELECT * FROM simulacao_pagadores WHERE tenant_id = ? ORDER BY id DESC LIMIT 1",
            (tenant_id,)).fetchone())
    finally:
        conn.close()


def pagadores(tenant_id: str) -> list:
    conn = _conectar()
    try:
        return [_pagador(l) for l in conn.execute(
            "SELECT * FROM simulacao_pagadores WHERE tenant_id = ? ORDER BY id", (tenant_id,))]
    finally:
        conn.close()


def nomes(tenant_id: str) -> dict:
    """{id_recorrencia: nome fictício}: o nome que a lista de ciclos mostra para
    um ciclo simulado (ele não está na base da empresa)."""
    return {p["id_recorrencia"]: p["nome"] for p in pagadores(tenant_id)}


_CAMPOS_DO_PAGADOR = frozenset({"cobrado_em", "cobranca_inicial", "codigo_inicial", "ciclo_id",
                                "resposta_a_mensagem", "resposta_em", "diagnostico"})


def atualizar_pagador(pagador_id: int, **campos) -> dict:
    invalidos = set(campos) - _CAMPOS_DO_PAGADOR
    if invalidos:
        raise ValueError(f"campos não atualizáveis: {sorted(invalidos)}")
    if "diagnostico" in campos and campos["diagnostico"] is not None:
        campos["diagnostico"] = json.dumps(campos["diagnostico"], ensure_ascii=False, default=str)
    conn = _conectar()
    try:
        conn.execute(
            f"UPDATE simulacao_pagadores SET {', '.join(f'{c} = ?' for c in campos)} WHERE id = ?",
            (*campos.values(), pagador_id))
        conn.commit()
    finally:
        conn.close()
    return pagador_por_id(pagador_id)


# ── A verdade escondida: SÓ o PSP simulado e o "sem a CRAI" leem ──────────

def verdade_de(pagador_id: int) -> dict:
    """A verdade escondida de um cliente fictício. Chamada só por
    `psp_responde`, por `sem_crai` e pela rota que devolve o cliente a quem o
    criou (o verso do cartão). NUNCA por um nó do pipeline."""
    conn = _conectar()
    try:
        linha = conn.execute("SELECT dias_ate_saldo, chance_pagar, vai_revogar "
                             "FROM simulacao_verdade WHERE pagador_id = ?",
                             (pagador_id,)).fetchone()
    finally:
        conn.close()
    return {"dias_ate_saldo": int(linha["dias_ate_saldo"]),
            "chance_pagar": float(linha["chance_pagar"]),
            "vai_revogar": bool(linha["vai_revogar"])}


def _sorteio(id_recorrencia: str, numero) -> float:
    """Um número em [0, 1), sempre o mesmo para o mesmo cliente e a mesma
    cobrança. `sha256` e não `hash()`: o hash de string muda a cada processo."""
    bruto = hashlib.sha256(f"{id_recorrencia}|{numero}".encode("utf-8")).digest()
    return int.from_bytes(bruto[:8], "big") / 2 ** 64


def psp_responde(pagador: dict, dia_relativo: int, numero: int) -> tuple:
    """O banco do cliente fictício respondendo a UMA cobrança: `(pagou, código)`.

    `numero` 0 é a cobrança do dia do vencimento; 1 a 3, as tentativas do
    sistema. `dia_relativo` é quantos dias se passaram desde o vencimento."""
    verdade = verdade_de(pagador["id"])
    if verdade["vai_revogar"] and numero == 1:
        return False, CODIGO_REVOGADA
    if dia_relativo < verdade["dias_ate_saldo"]:
        return False, CODIGO_SALDO
    if _sorteio(pagador["id_recorrencia"], numero) < verdade["chance_pagar"]:
        return True, None
    return False, CODIGO_TECNICO


def responde_a_mensagem(pagador: dict, dia_relativo: int) -> bool:
    """O cliente fictício paga depois de receber a mensagem? Só se já tem saldo;
    a mensagem soma `BONUS_DA_MENSAGEM` à chance de pagar. Quem revogou a
    autorização paga pelo outro meio que a mensagem oferece, pela mesma conta."""
    verdade = verdade_de(pagador["id"])
    if dia_relativo < verdade["dias_ate_saldo"]:
        return False
    chance = min(1.0, verdade["chance_pagar"] + BONUS_DA_MENSAGEM)
    return _sorteio(pagador["id_recorrencia"], "mensagem") < chance


def sem_crai(pagador: dict) -> dict:
    """O que teria acontecido SEM o sistema, pela regra do Pix Automático: o
    banco do pagador só tenta nas duas janelas do dia do vencimento; a
    recobrança nos 7 dias seguintes só acontece se o recebedor pedir (ver
    `dunning/pix_automatico_retry.py`). Sem saldo no dia, a cobrança se perde."""
    verdade = verdade_de(pagador["id"])
    dias = verdade["dias_ate_saldo"]
    if verdade["vai_revogar"]:
        return {"resultado": "perdido",
                "explicacao": "O cliente revoga a autorização. Sem a CRAI, ninguém fala com ele "
                              "e a assinatura acaba."}
    if pagador.get("cobranca_inicial") == PAGA:
        return {"resultado": "recuperado",
                "explicacao": "A cobrança passou no dia do vencimento. Não havia o que recuperar."}
    if dias == 0 and psp_responde(pagador, 0, "segunda_janela")[0]:
        return {"resultado": "recuperado",
                "explicacao": "O dinheiro já estava na conta. O próprio banco resolveria na "
                              "segunda janela do dia do vencimento."}
    if dias == 0:
        return {"resultado": "perdido",
                "explicacao": "Havia saldo, mas a cobrança não passou nas duas janelas do dia do "
                              "vencimento. Sem a CRAI, ninguém pede uma nova tentativa e a "
                              "cobrança se perde."}
    return {"resultado": "perdido",
            "explicacao": "Sem a CRAI, o banco só tenta nas duas janelas do dia do vencimento. "
                          f"O dinheiro entra em {dias} {'dia' if dias == 1 else 'dias'}, então a "
                          "cobrança se perde e a empresa precisa correr atrás por conta própria."}


def dia_relativo(pagador: dict, agora: datetime) -> int:
    """Quantos dias (de calendário) se passaram desde a cobrança do vencimento."""
    cobrado = datetime.fromisoformat(pagador["cobrado_em"])
    return (agora.date() - cobrado.date()).days


# ── O cliente fictício em risco (voluntário) ──────────────────────────────

def props_dos_sinais(mrr: float, sinais: dict) -> dict:
    """O que o sistema recebe de um cliente em risco, a partir dos sinais
    marcados no formulário. Cada sinal marcado vira o dado correspondente, como
    chega da base ou do SDK; sinal NÃO marcado não vira dado nenhum (o sistema
    fica sem aquela informação, como fica de um cliente que só mandou um
    evento). A propensão escondida NÃO entra aqui.

        uso_caiu            24 dias sem entrar, 1 funcionalidade usada em 30 dias
        tickets             3 chamados de suporte em 30 dias
        atraso              2 pagamentos com falha em 90 dias
        abriu_cancelamento  o cliente está na página de cancelamento (o evento
                            é `EVENTO_DE_INTENCAO`; ver `evento_dos_sinais`)
    """
    props = {"mrr": mrr, "billing_profile": "PJ",
             "on_site_now": bool(sinais["abriu_cancelamento"])}
    if sinais["uso_caiu"]:
        props.update(days_since_last=24, features_used_30d=1)
    if sinais["tickets"]:
        props["tickets_30d"] = 3
    if sinais["atraso"]:
        props["failed_pay_90d"] = 2
    return props


def evento_dos_sinais(sinais: dict, evento_padrao: str) -> str:
    """O evento que leva o cliente fictício ao pipeline: o de quem abriu a
    página de cancelamento, ou `evento_padrao` (a foto de comportamento)."""
    return EVENTO_DE_INTENCAO if sinais["abriu_cancelamento"] else evento_padrao


def criar_cliente_em_risco(tenant_id: str, cliente: dict, agora: datetime) -> dict:
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        base = hashlib.sha256(f"{tenant_id}|{cliente['nome'].lower()}".encode("utf-8")).hexdigest()[:10]
        usados = conn.execute(
            "SELECT COUNT(*) FROM simulacao_retencoes WHERE tenant_id = ? AND user_id LIKE ?",
            (tenant_id, f"user:sim-{base}-%")).fetchone()[0]
        user_id = f"user:sim-{base}-{int(usados) + 1}"
        cur = conn.execute(
            "INSERT INTO simulacao_retencoes (tenant_id, user_id, nome, mrr, sinais, propensao, "
            "criado_em) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, user_id, cliente["nome"], cliente["mrr"],
             json.dumps(cliente["sinais"]), json.dumps(cliente["propensao"]), _texto(agora)))
        conn.commit()
        return {"id": cur.lastrowid, "user_id": user_id}
    finally:
        conn.close()


def aceita_a_oferta(user_id: str, oferta: str, propensao: dict) -> bool:
    """O cliente fictício aceita ESTA oferta? Sai da propensão escondida dele,
    com um sorteio determinístico. Nunca do bandit: é isso que impede o bandit
    de aprender consigo mesmo."""
    return _sorteio(user_id, f"oferta:{oferta}") < float(propensao.get(oferta, 0.0))


def registrar_resultado_da_retencao(retencao_id: int, **campos) -> None:
    aceitos = {"faixa", "motivo", "decidido_por", "oferta", "canal", "aceitou"}
    invalidos = set(campos) - aceitos
    if invalidos:
        raise ValueError(f"campos não atualizáveis: {sorted(invalidos)}")
    conn = _conectar()
    try:
        conn.execute(
            f"UPDATE simulacao_retencoes SET {', '.join(f'{c} = ?' for c in campos)} WHERE id = ?",
            (*campos.values(), retencao_id))
        conn.commit()
    finally:
        conn.close()


# ── Ler o real e o simulado juntos (a barra "Mostrar: Simulação") ─────────
#
# As rotas de leitura do dashboard mostram só o que é real, a não ser que a
# pessoa peça `incluir_simulados`. Aí cada leitura é feita DUAS vezes: uma nos
# arquivos reais, outra nos de simulação da empresa, e as linhas simuladas saem
# marcadas (`MARCA_DE_LINHA`), para a rota dizer `simulado: true` em cada uma.
#
# O RELÓGIO SIMULADO ANDA NA FRENTE. Um ciclo simulado recuperado "daqui a 3
# dias" não caberia nos "últimos 30 dias" de verdade. Por isso a leitura
# simulada usa o período deslocado (os últimos 30 dias DO RELÓGIO SIMULADO), e
# as datas das linhas voltam o mesmo tanto: o AGORA da simulação aparece como
# agora há pouco (`FOLGA_DO_DESLOCAMENTO` atrás), e o que aconteceu 3 dias
# simulados antes aparece 3 dias atrás. Nada simulado aparece no futuro.

MARCA_DE_LINHA = "_simulado"
# O deslocamento é o relógio simulado menos o de verdade, mais esta folga. A
# folga garante que o que acabou de acontecer na simulação caiba num período
# que termina "agora": a rota lê o relógio de verdade um instante ANTES de o
# deslocamento ser calculado, e sem folga o último evento ficaria de fora.
FOLGA_DO_DESLOCAMENTO = timedelta(seconds=60)
_CAMPOS_DE_DATA = frozenset({"quando", "agendada_para", "janela_inicio", "janela_fim"})


def na_simulacao(tenant_id: str, fn, padrao=None):
    """`fn()` sobre os arquivos de simulação da empresa, com o relógio simulado
    dela. Empresa que nunca simulou: `padrao`, sem criar arquivo nenhum."""
    if not existe(tenant_id):
        return padrao
    with ambiente.em_simulacao(tenant_id):
        rel = relogio(tenant_id)
    with ambiente.em_simulacao(tenant_id, rel["agora"] if rel else None):
        return fn()


def deslocamento(tenant_id: str) -> timedelta:
    """Quanto o relógio simulado da empresa está à frente do de verdade, em
    segundos inteiros, mais `FOLGA_DO_DESLOCAMENTO` (zero se ela nunca simulou).
    Pode ser negativo: uma simulação parada há dias aparece como de agora."""
    from .api import datas                       # só o relógio da instalação
    rel = na_simulacao(tenant_id, lambda: relogio(tenant_id))
    if not rel:
        return timedelta(0)
    agora = datas.agora_local().replace(tzinfo=None)
    segundos = math.ceil((rel["agora"] - agora).total_seconds())
    return timedelta(seconds=segundos) + FOLGA_DO_DESLOCAMENTO


def recuar(valor, d: timedelta):
    """Uma data da simulação (texto ISO) no relógio de verdade: `d` para trás."""
    return _recuar_data(valor, d)


def _recuar_data(valor, d: timedelta):
    if not isinstance(valor, str) or len(valor) < 10:
        return valor
    try:
        return (datetime.fromisoformat(valor) - d).isoformat()
    except ValueError:
        return valor


def _marcar(valor, d: timedelta):
    """Marca cada linha (dict dentro de lista) como simulada e recua as datas dela."""
    if isinstance(valor, list):
        return [({**{k: (_recuar_data(v, d) if (k.endswith("_em") or k in _CAMPOS_DE_DATA) else v)
                     for k, v in item.items()}, MARCA_DE_LINHA: True}
                 if isinstance(item, dict) else item) for item in valor]
    if isinstance(valor, dict):
        return {k: _marcar(v, d) for k, v in valor.items()}
    return valor


def _juntar(real, simulado):
    if isinstance(real, list):
        return real + list(simulado or [])
    if isinstance(real, dict):
        simulado = simulado or {}
        return {k: (_juntar(v, simulado[k]) if k in simulado else v) for k, v in real.items()}
    if isinstance(real, bool) or real is None:
        return real
    if isinstance(real, (int, float)):
        return real + (simulado or 0)
    return real


def com_simulados(tenant_id: str, incluir: bool, fn):
    """`fn(d)` lido do que é real (`d` zero) e, se `incluir`, também da simulação
    da empresa (`d` é o deslocamento do relógio simulado, para somar ao período).
    Listas são concatenadas (as linhas simuladas marcadas e com as datas
    recuadas `d`), números são somados, dicionários são juntados chave a chave."""
    real = fn(timedelta(0))
    if not incluir or not existe(tenant_id):
        return real
    d = deslocamento(tenant_id)
    return _juntar(real, _marcar(na_simulacao(tenant_id, lambda: fn(d)), d))


def e_simulada(linha: dict) -> bool:
    return bool(linha.get(MARCA_DE_LINHA))


def retencoes(tenant_id: str) -> list:
    """Os clientes fictícios em risco desta empresa, do mais novo ao mais antigo,
    SEM a propensão escondida."""
    conn = _conectar()
    try:
        linhas = [dict(l) for l in conn.execute(
            "SELECT id, user_id, nome, mrr, sinais, faixa, motivo, decidido_por, oferta, canal, "
            "aceitou, criado_em FROM simulacao_retencoes WHERE tenant_id = ? ORDER BY id DESC",
            (tenant_id,))]
    finally:
        conn.close()
    for l in linhas:
        l["sinais"] = json.loads(l["sinais"])
        l["aceitou"] = None if l["aceitou"] is None else bool(l["aceitou"])
    return linhas

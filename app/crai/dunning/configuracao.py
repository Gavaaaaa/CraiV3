"""crai/dunning/configuracao.py — a configuração de CADA empresa (Etapa 2).

Até aqui todo parâmetro de negócio era global da instalação, lido de env
(`crai/config.py`). A Etapa 2 traz as primeiras escolhas que são da empresa, não
da CRAI: se a mensagem do involuntário sai sozinha ou espera a escolha dela,
quanto tempo a escolha espera, em que horário o cliente dela pode ser contatado,
por quais canais, e os prazos de retenção que ela contrata.

ONDE MORA. Tabela `configuracao_tenant`, no arquivo do ciclo de cobrança
(`recovery_cycles.db`, env `CRAI_RECOVERY_DB`): uma linha por empresa, com os
valores em JSON validado. Sem linha, valem os PADROES. A escrita (`gravar`)
valida TUDO antes de gravar e grava a configuração completa já mesclada — nunca
uma chave solta que deixe o conjunto inválido (janela com início depois do fim,
faixa grave maior que a preocupante).

AS CHAVES, e quem as lê:

    modo_mensagem_involuntario  automatico | escolha — R8 (workflow)
    prazo_escolha_horas         1..72 — R8: sem escolha, a recomendada sai
    janela_contato_inicio/fim   HH:MM — nenhuma mensagem sai fora dela; o fim
                                aceita 24:00 (até o fim do dia)
    canais_permitidos           ordem de preferência entre whatsapp e email
    modo_mensagem_voluntario    gravado agora, lido na Etapa 3
    retencao_*                  prazos de retenção (Bloco 4 executa os dois
                                primeiros; os outros ficam documentados)
    posicao_grave_pct /         faixas de posição na base do voluntário — só
    posicao_preocupante_pct     leitura e validação; quem lê é o voluntário
    prazo_estorno_dias          1..365 — Bloco 5 (E1): por quantos dias depois da
                                recuperação uma devolução do dinheiro ainda
                                devolve a fee à empresa. Lido no momento do aviso

CHAVE INTERNA `canal_presumido`. Não é aceita por `gravar`: é o canal usado
quando o cliente não tem contato elegível na base. Em produção é `None` — sem
contato, o canal é `sem_canal` e a mensagem não é entregue (D-E2-8: nunca mostrar
um canal que o cliente não tem). Só o tenant fixo do painel de avaliação (clientes
fictícios, sem base) e a suíte da Etapa 1 a ligam, por `fixar` e pelo conftest.
"""

import json
import logging
import re
from copy import deepcopy
from types import MappingProxyType
from datetime import datetime, time, timedelta
from typing import Optional

from .. import ambiente
from ..config import CANAIS_HUMANOS
from . import ciclo_cobranca

logger = logging.getLogger(__name__)

MODO_AUTOMATICO = "automatico"
MODO_ESCOLHA = "escolha"
MODOS = (MODO_AUTOMATICO, MODO_ESCOLHA)
CANAIS_INVOLUNTARIO = ("whatsapp", "email")

PADROES = {
    "modo_mensagem_involuntario": MODO_ESCOLHA,
    "prazo_escolha_horas": 8,
    "janela_contato_inicio": "08:00",
    "janela_contato_fim": "20:00",
    "canais_permitidos": ["whatsapp", "email"],
    "modo_mensagem_voluntario": MODO_ESCOLHA,
    "retencao_mensagens_dias": 90,
    "retencao_ciclos_meses": 24,
    "retencao_base_meses_apos_contrato": 6,
    "retencao_trilha_anos": 5,
    "posicao_grave_pct": 10,
    "posicao_preocupante_pct": 20,
    "prazo_estorno_dias": 30,
    # Rodada 3 (S5): um cliente final recebe no máximo UMA oferta de retenção a
    # cada tantos dias, venha o evento de onde vier (Segment, `POST /eventos`,
    # disparo em lote). A empresa escolhe de 1 a 365.
    "intervalo_minimo_ofertas_dias": 30,
    "canal_presumido": None,
}
CHAVES_INTERNAS = frozenset({"canal_presumido"})

# Cópia imutável dos padrões como o código os declara: a suíte troca `PADROES`
# por padrões neutros (conftest), e um teste confere ESTES.
PADROES_DE_PRODUCAO = MappingProxyType(deepcopy(PADROES))

# Configurações fixas em código, por tenant (o painel de avaliação). Vencem a
# linha do banco e os padrões.
_FIXAS: dict = {}

_HORA = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

_DDL = """
CREATE TABLE IF NOT EXISTS configuracao_tenant (
    tenant_id            TEXT PRIMARY KEY,
    valores              TEXT NOT NULL,
    atualizada_em        TEXT NOT NULL,
    atualizada_por_papel TEXT
);
"""


class ConfiguracaoInvalida(ValueError):
    def __init__(self, campo: str, detalhe: str):
        self.campo = campo
        self.detalhe = detalhe
        super().__init__(f"{campo}: {detalhe}")


# ── Validação ────────────────────────────────────────────────────────────

def _inteiro(campo, valor, minimo, maximo) -> int:
    if isinstance(valor, bool) or not isinstance(valor, int) or not minimo <= valor <= maximo:
        raise ConfiguracaoInvalida(campo, f"esperado inteiro entre {minimo} e {maximo}")
    return valor


def _numero(campo, valor, minimo, maximo) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not minimo < valor < maximo:
        raise ConfiguracaoInvalida(campo, f"esperado número maior que {minimo} e menor que {maximo}")
    return valor


def _hora(campo, valor, aceita_24=False) -> str:
    if isinstance(valor, str) and (_HORA.match(valor) or (aceita_24 and valor == "24:00")):
        return valor
    raise ConfiguracaoInvalida(campo, "esperado HH:MM" + (" (ou 24:00)" if aceita_24 else ""))


def _validar_chave(campo: str, valor):
    if campo in ("modo_mensagem_involuntario", "modo_mensagem_voluntario"):
        if valor not in MODOS:
            raise ConfiguracaoInvalida(campo, f"esperado um de {', '.join(MODOS)}")
        return valor
    if campo == "prazo_escolha_horas":
        return _inteiro(campo, valor, 1, 72)
    if campo == "janela_contato_inicio":
        return _hora(campo, valor)
    if campo == "janela_contato_fim":
        valor = _hora(campo, valor, aceita_24=True)
        if valor == "00:00":
            raise ConfiguracaoInvalida(campo, "o fim da janela não pode ser 00:00")
        return valor
    if campo == "canais_permitidos":
        if (not isinstance(valor, list) or not valor or len(set(valor)) != len(valor)
                or any(c not in CANAIS_INVOLUNTARIO or c in CANAIS_HUMANOS for c in valor)):
            raise ConfiguracaoInvalida(campo, "lista não vazia, sem repetição, de "
                                              f"{', '.join(CANAIS_INVOLUNTARIO)}")
        return list(valor)
    if campo == "retencao_mensagens_dias":
        return _inteiro(campo, valor, 1, 3650)
    if campo == "retencao_ciclos_meses":
        return _inteiro(campo, valor, 1, 120)
    if campo == "retencao_base_meses_apos_contrato":
        return _inteiro(campo, valor, 0, 120)
    if campo == "retencao_trilha_anos":
        return _inteiro(campo, valor, 1, 20)
    if campo in ("posicao_grave_pct", "posicao_preocupante_pct"):
        return _numero(campo, valor, 0, 100)
    if campo == "prazo_estorno_dias":
        return _inteiro(campo, valor, 1, 365)
    if campo == "intervalo_minimo_ofertas_dias":
        return _inteiro(campo, valor, 1, 365)
    raise ConfiguracaoInvalida(campo, "chave desconhecida")


def _validar_conjunto(c: dict) -> None:
    if _minutos(c["janela_contato_inicio"]) >= _minutos(c["janela_contato_fim"]):
        raise ConfiguracaoInvalida("janela_contato_fim", "o fim precisa ser depois do início")
    if not c["posicao_grave_pct"] < c["posicao_preocupante_pct"]:
        raise ConfiguracaoInvalida("posicao_preocupante_pct",
                                   "precisa ser maior que posicao_grave_pct")


def validar(parcial: dict) -> dict:
    """Valida um conjunto PARCIAL de chaves (o corpo de um PUT). Chave interna
    ou desconhecida é recusada. Devolve os valores normalizados."""
    if not isinstance(parcial, dict):
        raise ConfiguracaoInvalida("corpo", "esperado um objeto")
    validos = {}
    for campo, valor in parcial.items():
        if campo in CHAVES_INTERNAS:
            raise ConfiguracaoInvalida(campo, "chave interna, não configurável")
        validos[campo] = _validar_chave(campo, valor)
    return validos


# ── Leitura e escrita ─────────────────────────────────────────────────────

def _conectar():
    conn = ciclo_cobranca._conectar()
    conn.executescript(_DDL)
    return conn


def _gravada(tenant_id: str) -> dict:
    conn = _conectar()
    try:
        linha = conn.execute("SELECT valores FROM configuracao_tenant WHERE tenant_id = ?",
                             (tenant_id,)).fetchone()
    finally:
        conn.close()
    if linha is None:
        return {}
    try:
        return {k: v for k, v in json.loads(linha["valores"]).items() if k in PADROES}
    except (TypeError, ValueError):
        logger.error("[CONFIG] configuração ilegível do tenant %s — usando os padrões", tenant_id)
        return {}


def ler(tenant_id: str) -> dict:
    """A configuração efetiva da empresa: padrões ← gravada ← fixa.

    Dentro da simulação do gateway (Rodada 3) vale a configuração REAL da
    empresa (modo, prazo, janela de contato), lida fora da simulação, mais um
    canal presumido: o cliente fictício não está na base, e sem isto a mensagem
    dele nunca teria por onde sair. O canal é o primeiro dos permitidos."""
    if ambiente.simulacao_ativa():
        with ambiente.fora_da_simulacao():
            efetiva = ler(tenant_id)
        if not efetiva.get("canal_presumido"):
            efetiva["canal_presumido"] = efetiva["canais_permitidos"][0]
        return efetiva
    efetiva = deepcopy(PADROES)
    efetiva.update(_gravada(tenant_id))
    efetiva.update(deepcopy(_FIXAS.get(tenant_id, {})))
    return efetiva


def publica(config: dict) -> dict:
    """A configuração como a empresa a vê: sem as chaves internas."""
    return {k: deepcopy(v) for k, v in config.items() if k not in CHAVES_INTERNAS}


def gravar(tenant_id: str, parcial: dict, papel: Optional[str] = None,
           agora: Optional[datetime] = None) -> dict:
    """Valida o parcial, mescla com o que está gravado, valida o conjunto e
    grava a configuração inteira. Devolve a configuração efetiva."""
    novos = validar(parcial)
    gravada = _gravada(tenant_id)
    candidata = deepcopy(PADROES)
    candidata.update(gravada)
    candidata.update(novos)
    _validar_conjunto(candidata)
    para_gravar = {k: v for k, v in {**gravada, **novos}.items() if k not in CHAVES_INTERNAS}
    conn = _conectar()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO configuracao_tenant (tenant_id, valores, atualizada_em, atualizada_por_papel)
               VALUES (?, ?, ?, ?)
               ON CONFLICT (tenant_id) DO UPDATE SET valores = excluded.valores,
                   atualizada_em = excluded.atualizada_em,
                   atualizada_por_papel = excluded.atualizada_por_papel""",
            (tenant_id, json.dumps(para_gravar, sort_keys=True),
             (agora or datetime.now()).isoformat(), papel))
        conn.commit()
    finally:
        conn.close()
    return ler(tenant_id)


def fixar(tenant_id: str, valores: dict) -> None:
    """Configuração fixa em código para um tenant (o painel de avaliação).
    Aceita as chaves internas; valida as outras."""
    externas = validar({k: v for k, v in valores.items() if k not in CHAVES_INTERNAS})
    _FIXAS[tenant_id] = {**externas, **{k: v for k, v in valores.items() if k in CHAVES_INTERNAS}}


# ── Janela de contato ─────────────────────────────────────────────────────

def _minutos(hora: str) -> int:
    h, m = hora.split(":")
    return int(h) * 60 + int(m)


def dentro_da_janela(config: dict, agora: datetime) -> bool:
    """`agora` (hora local) está em [início, fim) da janela de contato."""
    minuto = agora.hour * 60 + agora.minute
    return _minutos(config["janela_contato_inicio"]) <= minuto < _minutos(config["janela_contato_fim"])


def proximo_inicio_da_janela(config: dict, agora: datetime) -> datetime:
    """O próximo instante em que uma mensagem pode sair: `agora`, se já está
    dentro da janela; senão o início da próxima."""
    if dentro_da_janela(config, agora):
        return agora
    h, m = (int(p) for p in config["janela_contato_inicio"].split(":"))
    hoje = datetime.combine(agora.date(), time(h, m))
    if agora < hoje:
        return hoje
    return hoje + timedelta(days=1)

"""crai/dunning/canal_involuntario.py — por onde a mensagem do involuntário sai.

Até a Etapa 2 o canal era fixo (`whatsapp`, em `DunningEngine._select_payment`),
para qualquer cliente, tivesse ele telefone ou não. Agora ele sai dos contatos
que a EMPRESA mandou na base importada (`clientes_importados`), ligada à cobrança
pelo `id_recorrencia`:

    1. sem mapeamento (nenhum cliente da base com este `id_recorrencia`) →
       `sem_canal`, motivo `sem_mapeamento`;
    2. com mapeamento, o primeiro canal de `canais_permitidos` (configuração da
       empresa, na ordem) cujo contato existe: `whatsapp` exige `telefone`,
       `email` exige `email`;
    3. nenhum elegível → `sem_canal`, motivo `sem_contato`.

`sem_canal` é o canal de verdade quando o cliente não tem contato: a mensagem é
gerada e fica marcada como NÃO ENTREGÁVEL, visível na linha do tempo — nunca se
mostra um canal que o cliente não tem (D-E2-8). A única exceção é a chave
interna `canal_presumido` da configuração: o painel de avaliação (clientes
fictícios, sem base) e a suíte da Etapa 1 a ligam, e o motivo registrado diz
`presumido`.

O CONTATO É LIDO NA HORA E NUNCA COPIADO. Estas funções devolvem o canal e o
motivo, o primeiro nome e a faixa de tempo de casa — nunca o telefone nem o
e-mail. Quem chama grava canal e motivo; o contato fica só na base.
"""

import logging
from typing import Optional

from ..churn_voluntary import clientes_importados

logger = logging.getLogger(__name__)

SEM_CANAL = "sem_canal"
MOTIVO_CONTATO_DA_BASE = "contato_da_base"
MOTIVO_SEM_MAPEAMENTO = "sem_mapeamento"
MOTIVO_SEM_CONTATO = "sem_contato"
MOTIVO_PRESUMIDO = "presumido"

# Qual coluna da base torna cada canal elegível.
CONTATO_DO_CANAL = {"whatsapp": "telefone", "email": "email"}

# Faixas de tempo de casa para o prompt: nunca o número exato de dias.
FAIXAS_DE_TEMPO_DE_CASA = ((90, "menos de 3 meses"), (365, "de 3 a 12 meses"),
                           (3 * 365, "de 1 a 3 anos"))
FAIXA_MAIOR = "mais de 3 anos"


def cliente_da_recorrencia(tenant_id: str, id_recorrencia: str) -> Optional[dict]:
    """A linha da base ligada a esta recorrência, ou None. Base não
    configurada é tratada como sem mapeamento, com log — a mensagem não
    deixa de ser gerada por isso."""
    try:
        return clientes_importados.obter_por_recorrencia(tenant_id, id_recorrencia)
    except clientes_importados.ConfiguracaoAusente as e:
        logger.warning("[CANAL] base importada não configurada (%s) — sem mapeamento", e)
        return None


def escolher_canal(tenant_id: str, id_recorrencia: str, config: dict) -> dict:
    """`{"canal", "motivo_canal", "primeiro_nome", "tempo_de_casa"}` — sem contato."""
    cliente = cliente_da_recorrencia(tenant_id, id_recorrencia)
    base = {"primeiro_nome": primeiro_nome(cliente), "tempo_de_casa": faixa_de_tempo_de_casa(cliente)}
    if cliente is not None:
        for canal in config["canais_permitidos"]:
            if cliente.get(CONTATO_DO_CANAL.get(canal, "")):
                return {"canal": canal, "motivo_canal": MOTIVO_CONTATO_DA_BASE, **base}
    motivo = MOTIVO_SEM_MAPEAMENTO if cliente is None else MOTIVO_SEM_CONTATO
    presumido = config.get("canal_presumido")
    if presumido:
        return {"canal": presumido, "motivo_canal": f"{MOTIVO_PRESUMIDO}_{motivo}", **base}
    return {"canal": SEM_CANAL, "motivo_canal": motivo, **base}


def primeiro_nome(cliente: Optional[dict]) -> Optional[str]:
    """Só a primeira palavra do nome, e só letras (hífen e apóstrofo dentro do
    nome valem). É o máximo de nome que vai ao LLM e ao texto."""
    nome = (cliente or {}).get("nome")
    if not isinstance(nome, str) or not nome.strip():
        return None
    palavra = nome.split()[0].strip(".,;:!?")
    if not palavra or not all(ch.isalpha() or ch in "-'" for ch in palavra):
        return None
    return palavra[:40]


def faixa_de_tempo_de_casa(cliente: Optional[dict]) -> Optional[str]:
    """A faixa do `tenure_days` REAL mandado pela empresa, ou None. O
    `tenure_months` sintético do perfil do classificador nunca entra aqui."""
    dias = (cliente or {}).get("tenure_days")
    if dias is None or isinstance(dias, bool):
        return None
    try:
        dias = int(dias)
    except (TypeError, ValueError):
        return None
    if dias < 0:
        return None
    for limite, rotulo in FAIXAS_DE_TEMPO_DE_CASA:
        if dias < limite:
            return rotulo
    return FAIXA_MAIOR


def nome_do_cliente(tenant_id: str, id_recorrencia: str) -> Optional[str]:
    """O nome da base, para a tela da empresa (é o cliente dela). Nunca gravado
    em ciclo nem em mensagem."""
    cliente = cliente_da_recorrencia(tenant_id, id_recorrencia)
    return (cliente or {}).get("nome")

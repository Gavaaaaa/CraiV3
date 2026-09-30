"""crai/api/datas.py — toda data que sai pela API sai em ISO 8601 COM fuso.

POR QUE ISTO EXISTE (F9 / 7-H do diagnóstico de 29/09/2026). O mesmo banco
guarda dois relógios: o ciclo de cobrança, as tentativas (e, no Bloco 3, as
mensagens) gravam hora LOCAL sem fuso (`ciclo_cobranca`, TEMPO); o dataset, a
trilha do Art. 20 e a base importada gravam UTC com offset. Um navegador que
recebe `2026-09-03T09:00:00` não sabe de que fuso é, e ordenar ciclo e trilha
na mesma linha do tempo exige um instante comparável.

A REGRA. Texto com offset é devolvido como está (normalizado). Texto sem fuso
é interpretado no fuso da instalação: `CRAI_FUSO_LOCAL` (nome IANA, ex.
`America/Sao_Paulo`) quando definida; senão o fuso da máquina — que é o fuso em
que a hora local foi lida quando o dado foi gravado. Nenhuma data antiga é
migrada. Se a máquina que gravou estava em outro fuso, a conversão erra por
esse offset: limitação declarada.

O caminho inverso (`para_local`) serve aos filtros: um instante que o
navegador manda com fuso vira hora local sem fuso, comparável como texto com
as colunas do ciclo.
"""

import os
from datetime import datetime, timezone, tzinfo
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ENV_FUSO = "CRAI_FUSO_LOCAL"


def fuso_local() -> tzinfo:
    """O fuso da instalação. Lido a cada chamada (env por teste)."""
    nome = (os.getenv(ENV_FUSO) or "").strip()
    if nome:
        try:
            return ZoneInfo(nome)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def _como_datetime(valor) -> Optional[datetime]:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor
    try:
        return datetime.fromisoformat(str(valor))
    except ValueError:
        return None


def com_fuso(valor) -> Optional[datetime]:
    """`datetime` com fuso, ou None. Sem fuso → fuso da instalação."""
    d = _como_datetime(valor)
    if d is None:
        return None
    return d if d.tzinfo is not None else d.replace(tzinfo=fuso_local())


def iso_com_fuso(valor) -> Optional[str]:
    """ISO 8601 com offset, ou None. Texto que não é data volta None."""
    d = com_fuso(valor)
    return d.isoformat(timespec="seconds") if d is not None else None


def para_local(valor) -> Optional[datetime]:
    """Instante → hora local SEM fuso (o formato das colunas do ciclo).
    Sem fuso na entrada, já é hora local."""
    d = _como_datetime(valor)
    if d is None:
        return None
    if d.tzinfo is None:
        return d
    return d.astimezone(fuso_local()).replace(tzinfo=None)


def agora_local() -> datetime:
    """Hora local sem fuso, no fuso da instalação."""
    return datetime.now(fuso_local()).replace(tzinfo=None)

"""crai/ambiente.py — o AMBIENTE DE SIMULAÇÃO de cada empresa (Rodada 3, Fase 3).

O PROBLEMA. A "Simulação do gateway" do dashboard precisa rodar o pipeline DE
VERDADE (os mesmos nós, os mesmos modelos, a mesma regra do BACEN) sobre um
cliente fictício, com um relógio que a empresa avança. E nada disso pode
encostar no que é real: nem no ciclo de cobrança, nem no dataset de treino, nem
na trilha do Art. 20, nem no bandit, nem no PSP, nem no CRM.

A SAÍDA. Enquanto uma requisição da simulação está sendo atendida, este módulo
guarda (num `ContextVar`, que vale só para aquela tarefa) de qual empresa é a
simulação e que horas são no relógio dela. Os módulos que gravam em disco
perguntam a `caminho()` onde gravar, e os que leem o relógio perguntam a
`relogio_simulado()`:

    arquivos   cada empresa tem os SEUS arquivos de simulação, ao lado dos
               reais: `recovery_cycles.simulacao.<empresa>.db`,
               `retention_cycles.simulacao.<empresa>.db` e
               `pix_retry_state.simulacao.<empresa>.json`. Os reais não são
               abertos dentro da simulação (salvo `fora_da_simulacao`, usado
               para LER a configuração e a base da empresa).
    relógio    o relógio da simulação é um instante gravado por empresa; o
               relógio de verdade (`api/relogio.py`) nunca abre estes arquivos,
               então as tentativas simuladas só saem quando a empresa avança.
    de fora    PSP, CRM e WhatsApp perguntam `simulacao_ativa()` e, na
               simulação, ficam SEMPRE no modo simulado, mesmo com credencial
               de verdade configurada.

Por ser um arquivo por empresa, limpar a simulação de uma empresa é apagar os
arquivos dela, e uma empresa não tem como ler a simulação de outra: o tenant
entra no NOME do arquivo. Um tenant em minúsculas, números, `_` e `-` (o UUID
do Supabase é assim) entra como está; qualquer outro entra pelo seu sha256,
para que dois tenants diferentes nunca caiam no mesmo arquivo: nem por
maiúscula e minúscula (o Windows não as distingue em nome de arquivo), nem por
barra, ponto ou espaço.

Fora de uma simulação, tudo neste módulo é neutro: `caminho(p)` devolve `p`,
`relogio_simulado()` devolve None e `simulacao_ativa()` é False.

Este módulo não importa nada do pacote: todos podem importá-lo.
"""

import hashlib
import re
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Optional

MARCA = "simulacao"
_TENANT_SIMPLES = re.compile(r"[a-z0-9_-]{1,64}")


def parte_do_nome(tenant_id: str) -> str:
    """Como o tenant entra no nome do arquivo de simulação: ele mesmo, se for
    simples; senão, `h` + os 32 primeiros hex do sha256 dele."""
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError(f"tenant inválido para a simulação: {tenant_id!r}")
    if _TENANT_SIMPLES.fullmatch(tenant_id):
        return tenant_id
    return "h" + hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:32]

# (tenant, relógio) da simulação em curso NESTA tarefa, ou None.
_simulacao: ContextVar[Optional[tuple]] = ContextVar("crai_simulacao", default=None)


def simulacao_ativa() -> bool:
    """Esta tarefa está atendendo a simulação de uma empresa?"""
    return _simulacao.get() is not None


def tenant_da_simulacao() -> Optional[str]:
    atual = _simulacao.get()
    return atual[0] if atual else None


def relogio_simulado() -> Optional[datetime]:
    """Que horas são no relógio da simulação em curso (hora local sem fuso), ou
    None fora de uma simulação."""
    atual = _simulacao.get()
    return atual[1] if atual else None


def caminho_simulado(real: Path, tenant_id: str) -> Path:
    """O arquivo de simulação de `tenant_id` que corresponde ao arquivo `real`."""
    real = Path(real)
    return real.with_name(f"{real.stem}.{MARCA}.{parte_do_nome(tenant_id)}{real.suffix}")


def caminho(real: Path) -> Path:
    """Onde gravar: o arquivo `real`, ou o da simulação em curso."""
    atual = _simulacao.get()
    if atual is None:
        return real
    return caminho_simulado(real, atual[0])


@contextmanager
def em_simulacao(tenant_id: str, agora: Optional[datetime] = None):
    """Tudo o que rodar aqui dentro lê e grava nos arquivos de simulação de
    `tenant_id`, com o relógio em `agora`."""
    parte_do_nome(tenant_id)                      # ValueError se não for um tenant
    marca = _simulacao.set((tenant_id, agora))
    try:
        yield
    finally:
        _simulacao.reset(marca)


@contextmanager
def fora_da_simulacao():
    """Uma leitura do que é REAL de dentro de uma simulação: a configuração da
    empresa, por exemplo. Nada deve ser gravado aqui dentro."""
    marca = _simulacao.set(None)
    try:
        yield
    finally:
        _simulacao.reset(marca)

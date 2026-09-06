"""crai/dunning/retry_state.py — onde os planos de retentativa pendentes moram.

POR QUE ESTA CAMADA EXISTE (Gap 2 da auditoria). O contador de tentativas do
BACEN vive no checkpoint do LangGraph, que é um `MemorySaver` — RAM, um
processo. Já está documentado no `build_crai_graph` que reiniciar o serviço
zera o contador de todo mundo e que dois processos têm memórias separadas. O
Sprint 2 acrescenta um segundo consumidor desse estado: o agendador, que
precisa saber, DEPOIS que o grafo terminou, quais tentativas do plano ainda não
foram disparadas.

A tentação seria o agendador ler o checkpoint. Seria errado por dois motivos: o
checkpoint é privado do grafo (formato do LangGraph, não contrato nosso), e a
troca por PostgreSQL passaria a exigir mexer no agendador junto. Esta camada é
o ponto de troca: hoje é um JSON simples, amanhã é um `UPDATE ... WHERE` numa
tabela, e nem o nó do grafo nem o agendador mudam de forma.

QUAL É A FONTE DA VERDADE, para não haver duas. O contador do BACEN continua
sendo o do checkpoint — é ele que `decide_recovery` lê e é ele que decide se
ainda cabe tentativa. O que mora AQUI é o PLANO já concedido pela política:
quais instruções de pagamento foram comprometidas, quando, e quais já saíram.
O agendador **nunca concede** tentativa nova; ele só dispara o que a política
já autorizou. Por isso este arquivo não pode afrouxar o limite regulatório
mesmo se for corrompido — no máximo perde disparos, e perder disparo é
recuperável; conceder tentativa a mais não é.

LIMITAÇÕES ASSUMIDAS, escritas em vez de descobertas depois:

  1. Arquivo JSON único, reescrito inteiro a cada gravação. Serve ao volume de
     um TCC e de um SaaS PME em POC; não serve a dois processos escrevendo
     juntos. É a MESMA dependência de banco do `MemorySaver`, e some no mesmo
     dia que ela.
  2. Sem transação. Uma queda no meio da escrita pode deixar o arquivo
     truncado; a leitura trata isso devolvendo estado vazio e logando, em vez
     de derrubar o pipeline de cobrança de todo mundo.
  3. `CRAI_RETRY_STATE` redireciona o caminho — é o que a suíte usa para não
     escrever no arquivo real, pelo mesmo motivo que `CRAI_RETENTION_DB` existe
     no churn voluntário (ver `tests/conftest.py`).
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
CAMINHO_PADRAO = DATA_DIR / "pix_retry_state.json"

ENV_CAMINHO = "CRAI_RETRY_STATE"


def caminho_do_estado() -> Path:
    """Lido a cada chamada para o teste poder redirecionar via env."""
    override = os.getenv(ENV_CAMINHO)
    return Path(override) if override else CAMINHO_PADRAO


def _carregar() -> dict:
    caminho = caminho_do_estado()
    if not caminho.exists():
        return {}
    try:
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else {}
    except Exception as e:                       # noqa: BLE001 — ver limitação (2)
        logger.warning("[RETRY-STATE] Estado ilegível em %s (%s) — tratando como "
                       "vazio. Nenhuma tentativa é CONCEDIDA por isso; no pior "
                       "caso, disparos pendentes se perdem.", caminho, e)
        return {}


def _gravar(dados: dict) -> None:
    caminho = caminho_do_estado()
    try:
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:                       # noqa: BLE001
        # Best effort declarado, como o `_persist` do bandit e o retention_log:
        # perder o registro de um plano é ruim; derrubar a recuperação de um
        # cliente por causa do disco é pior.
        logger.warning("[RETRY-STATE] Falha ao gravar %s: %s", caminho, e)


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


def chave(customer_id: str, tenant_id: Optional[str] = None) -> str:
    """A identidade de um plano.

    O `tenant_id` já entra na chave, ainda que o involuntário só o ganhe no
    Sprint 4: quando ele chegar, os planos de duas empresas clientes não podem
    se sobrepor por compartilharem um `id_recorrencia`. Sem tenant a chave é só
    o cliente, e o que estava gravado continua legível.
    """
    return f"{tenant_id}:{customer_id}" if tenant_id else customer_id


def save_retry_state(
    customer_id: str,
    valor_original: float,
    tentativas: list[dict],
    pix_janela_ate=None,
    tenant_id: Optional[str] = None,
    e2e_id: Optional[str] = None,
) -> None:
    """Grava o plano de tentativas de um ciclo, preservando o que já disparou.

    Regravar o plano do mesmo cliente é operação normal — o grafo roda de novo
    a cada cobrança falhada. O que NÃO pode acontecer é a regravação apagar a
    marca de disparo de uma tentativa já enviada ao PSP: o agendador a
    reenviaria, e o cliente seria cobrado duas vezes pela mesma tentativa. Por
    isso o merge é por número de tentativa, e `disparada_em` é preservado.
    """
    dados = _carregar()
    k = chave(customer_id, tenant_id)
    anterior = (dados.get(k) or {}).get("tentativas") or []
    disparos = {t.get("numero"): t for t in anterior if t.get("disparada_em")}

    registro = {
        "customer_id": customer_id,
        "tenant_id": tenant_id,
        "e2e_id": e2e_id,
        "valor_original": round(float(valor_original), 2),
        "pix_janela_ate": _iso(pix_janela_ate),
        "atualizado_em": _iso(datetime.now()),
        "tentativas": [],
    }

    for t in tentativas:
        numero = t.get("numero")
        ja_disparada = disparos.get(numero)
        registro["tentativas"].append({
            "numero": numero,
            "quando": _iso(t.get("quando")),
            "valor": round(float(t.get("valor", valor_original)), 2),
            "origem": t.get("origem"),
            "disparada_em": (ja_disparada or {}).get("disparada_em"),
            "id_cobranca": (ja_disparada or {}).get("id_cobranca"),
        })

    dados[k] = registro
    _gravar(dados)


def get_retry_state(customer_id: str, tenant_id: Optional[str] = None) -> Optional[dict]:
    """O plano gravado para aquele cliente, ou None."""
    return _carregar().get(chave(customer_id, tenant_id))


def planos_pendentes() -> list[dict]:
    """Todo plano com ao menos uma tentativa ainda não disparada."""
    return [
        registro for registro in _carregar().values()
        if isinstance(registro, dict)
        and any(not t.get("disparada_em") for t in (registro.get("tentativas") or []))
    ]


def marcar_disparada(
    customer_id: str, numero: int, id_cobranca: str,
    quando: Optional[datetime] = None, tenant_id: Optional[str] = None,
) -> bool:
    """Registra que a tentativa `numero` saiu para o PSP. False se não achou.

    É esta marca que impede o disparo duplo: o agendador roda periodicamente e
    reencontra o mesmo plano toda vez.
    """
    dados = _carregar()
    k = chave(customer_id, tenant_id)
    registro = dados.get(k)
    if not registro:
        return False

    for t in registro.get("tentativas") or []:
        if t.get("numero") == numero:
            t["disparada_em"] = _iso(quando or datetime.now())
            t["id_cobranca"] = id_cobranca
            _gravar(dados)
            return True
    return False


def tentativas_devidas(registro: dict, agora: datetime) -> list[dict]:
    """As tentativas do plano cuja data já chegou e que ainda não saíram.

    Uma tentativa sem data legível é tratada como NÃO devida: na dúvida, o
    limite regulatório aperta. Disparar cedo demais é enviar instrução fora da
    janela combinada; não disparar é, no pior caso, um ciclo a menos.
    """
    devidas = []
    for t in registro.get("tentativas") or []:
        if t.get("disparada_em"):
            continue
        quando = _data(t.get("quando"))
        if quando is not None and quando <= agora:
            devidas.append(t)
    return sorted(devidas, key=lambda t: t.get("numero") or 0)


def limpar_tudo() -> None:
    """Esquece todos os planos. Existe para o teste e para a demo."""
    _gravar({})

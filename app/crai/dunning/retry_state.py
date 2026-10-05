"""crai/dunning/retry_state.py — o plano de tentativas, lido e gravado nas tabelas.

O QUE ESTE MÓDULO ERA. Até a Etapa 1 (Bloco 1, 28/09/2026) o plano de
retentativa morava num JSON reescrito inteiro a cada gravação, sem transação,
e o CONTADOR do BACEN morava no checkpoint do LangGraph, em RAM. Eram duas
fontes, com durabilidades diferentes, e nenhuma sabia o resultado de nenhuma
tentativa. O diagnóstico (`docs/interno/DIAGNOSTICO_INTEGRACAO.md`, item 10)
mediu o custo: depois de um reinício, confirmação descartada, janela reaberta,
reenvio do PSP passando como evento novo.

O QUE ELE É AGORA. Uma FACHADA sobre `crai/dunning/ciclo_cobranca.py`, que é
o dono das tabelas `ciclos_cobranca` e `tentativas_cobranca`. A API pública
continua a mesma — `save_retry_state`, `get_retry_state`, `planos_pendentes`,
`marcar_disparada`, `tentativas_devidas`, `limpar_tudo`, `chave` — e o
`retry_scheduler` e `tentativas_ja_disparadas` (workflow) não mudaram de
forma. O que mudou é onde a verdade mora: uma tabela, com `BEGIN IMMEDIATE`,
que sobrevive a reinício e a dois processos no mesmo arquivo.

O JSON (`pix_retry_state.json`, ou `CRAI_RETRY_STATE`) deixa de ser escrito.
Ele continua sendo LIDO uma vez por processo, pela migração em
`ciclo_cobranca._migrar_json`, para que os planos gravados antes desta etapa
virem linhas das tabelas — idempotente, e o arquivo fica no disco como
evidência. `caminho_do_estado` e `ENV_CAMINHO` existem para isso.

O AGENDADOR CONTINUA NÃO CONCEDENDO tentativa: ele dispara o que a política
gravou como pendente. E agora o BANCO também não concede: a 4ª tentativa de um
ciclo é recusada por `CHECK` + `UNIQUE`, não por convenção.

O REGISTRO devolvido por `get_retry_state` / `planos_pendentes` tem a mesma
forma de sempre (`customer_id`, `tenant_id`, `e2e_id`, `valor_original`,
`pix_janela_ate`, `atualizado_em`, `tentativas[{numero, quando, valor, origem,
disparada_em, id_cobranca}]`) e ganha duas chaves aditivas: `ciclo_id` e, em
cada tentativa, `resultado`. Uma tentativa `cancelada` nunca é devida.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from .. import ambiente
from . import ciclo_cobranca
from .pix_automatico_retry import inicio_da_janela

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
CAMINHO_PADRAO = DATA_DIR / "pix_retry_state.json"

ENV_CAMINHO = "CRAI_RETRY_STATE"

# O balde de quem não declara tenant. Espelha o literal do churn voluntário
# (`offer_bandit.TENANT_PADRAO`) sem importá-lo: um módulo de dunning não deve
# depender do pacote do outro churn só por uma constante. Que os dois não
# divirjam é asserção de teste, não esperança — ver `test_tenant_involuntary`.
TENANT_PADRAO = "default_tenant"


def caminho_do_estado() -> Path:
    """O JSON legado, lido só pela migração. Lido a cada chamada para o teste
    poder redirecionar via env."""
    override = os.getenv(ENV_CAMINHO)
    return ambiente.caminho(Path(override) if override else CAMINHO_PADRAO)


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
    """A identidade de um plano, como era no JSON. Mantida porque é a forma
    legível de "tenant + mandato" que o log e os testes usam; nas tabelas a
    separação é por coluna (`tenant_id`, `id_recorrencia`)."""
    if not tenant_id or tenant_id == TENANT_PADRAO:
        return customer_id
    return f"{tenant_id}:{customer_id}"


def _registro(ciclo: dict, tentativas: list[dict]) -> dict:
    return {
        "ciclo_id": ciclo["id"],
        "customer_id": ciclo["id_recorrencia"],
        "tenant_id": ciclo["tenant_id"],
        "e2e_id": ciclo.get("e2e_falha_original"),
        "valor_original": ciclo["valor"],
        "pix_janela_ate": ciclo["janela_fim"],
        "atualizado_em": ciclo["atualizado_em"],
        "tentativas": [
            {
                "numero": t["numero"],
                "quando": t["agendada_para"],
                "valor": t["valor"],
                "origem": t["origem_data"],
                "disparada_em": t["disparada_em"],
                "id_cobranca": t["id_cobranca"],
                "resultado": t["resultado"],
            }
            for t in tentativas
        ],
    }


def _ciclo_do_plano(tenant: str, customer_id: str, e2e_id: Optional[str],
                    pix_janela_ate: Optional[datetime], valor_original: float) -> dict:
    """A que ciclo um plano pertence.

    1. O ciclo da cobrança identificada pelo e2e do plano, se existe.
    2. Senão, o ciclo `recobrando` do mandato cuja janela é a do plano — é o
       "segundo evento da mesma janela", que continua a janela aberta em vez
       de abrir outra (o que o merge por número do JSON já fazia).
    3. Senão, um ciclo novo, com a janela que o plano declara. A causa fica
       `desconhecida`. É CAMINHO LEGADO (B2-b): desde o Bloco 2 o grafo abre o
       ciclo em `open_cycle`, antes do plano, e passa `ciclo_id` explícito a
       `save_retry_state`; o fluxo do grafo nunca chega aqui. Quem chega é um
       chamador antigo (teste ou script que grava plano sem ciclo), e o log
       diz, em WARNING.
    """
    if e2e_id:
        ciclo = ciclo_cobranca.ciclo_da_cobranca(tenant, e2e_id)
        if ciclo is not None:
            return ciclo
    aberto = ciclo_cobranca.ciclo_aberto_do_mandato(
        tenant, customer_id, estados=(ciclo_cobranca.RECOBRANDO,))
    if aberto is not None and (
        pix_janela_ate is None or _data(aberto["janela_fim"]) == pix_janela_ate
    ):
        return aberto
    agora = datetime.now()
    janela_inicio = inicio_da_janela(pix_janela_ate) if pix_janela_ate else agora
    logger.warning("[RETRY-STATE] Plano gravado SEM ciclo para %s/%s — abrindo ciclo de "
                   "origem 'plano' com causa desconhecida. Caminho legado (B2-b): o grafo "
                   "abre o ciclo em open_cycle e nunca passa por aqui.", tenant, customer_id)
    try:
        return ciclo_cobranca.abrir_ciclo(
            tenant, customer_id, valor_original, "desconhecida", agora,
            e2e_falha_original=e2e_id, janela_inicio=janela_inicio,
            janela_fim=pix_janela_ate, origem=ciclo_cobranca.ORIGEM_PLANO,
        )
    except ciclo_cobranca.CicloJaExiste as e:
        return ciclo_cobranca.ciclo_por_id(e.ciclo_id)


def save_retry_state(
    customer_id: str,
    valor_original: float,
    tentativas: list[dict],
    pix_janela_ate=None,
    tenant_id: Optional[str] = None,
    e2e_id: Optional[str] = None,
    ciclo_id: Optional[int] = None,
) -> None:
    """Grava o plano de tentativas de um ciclo como PENDENTES, preservando o
    que já disparou ou já tem resultado (merge por número, no banco).

    `ciclo_id` é a forma precisa, e é a que o grafo usa. Sem ele, o ciclo é
    resolvido por `_ciclo_do_plano` (caminho legado)."""
    tenant = tenant_id or TENANT_PADRAO
    if ciclo_id is not None:
        ciclo = ciclo_cobranca.ciclo_por_id(ciclo_id)
        if ciclo is None:
            raise ValueError(f"ciclo {ciclo_id} não existe")
    else:
        ciclo = _ciclo_do_plano(tenant, customer_id, e2e_id, _data(pix_janela_ate),
                                valor_original)
    ciclo_cobranca.agendar_tentativas(ciclo["id"], [
        {"numero": t.get("numero"), "quando": t.get("quando"),
         "valor": t.get("valor", valor_original), "origem": t.get("origem")}
        for t in tentativas
    ])


def get_retry_state(customer_id: str, tenant_id: Optional[str] = None) -> Optional[dict]:
    """O plano mais recente daquele mandato, ou None se nunca houve plano."""
    ciclo = ciclo_cobranca.ciclo_mais_recente_com_tentativas(tenant_id or TENANT_PADRAO,
                                                             customer_id)
    if ciclo is None:
        return None
    return _registro(ciclo, ciclo_cobranca.tentativas_do_ciclo(ciclo["id"]))


def registro_do_ciclo(ciclo_id: int) -> Optional[dict]:
    """O plano de UM ciclo, na forma de registro. None se o ciclo não existe."""
    ciclo = ciclo_cobranca.ciclo_por_id(ciclo_id)
    if ciclo is None:
        return None
    return _registro(ciclo, ciclo_cobranca.tentativas_do_ciclo(ciclo_id))


def planos_pendentes() -> list[dict]:
    """Todo plano com ao menos uma tentativa pendente ainda não disparada."""
    return [
        _registro(ciclo, ciclo_cobranca.tentativas_do_ciclo(ciclo["id"]))
        for ciclo in ciclo_cobranca.ciclos_recobrando_com_pendentes()
    ]


def marcar_disparada(
    customer_id: str, numero: int, id_cobranca: str,
    quando: Optional[datetime] = None, tenant_id: Optional[str] = None,
    ciclo_id: Optional[int] = None,
) -> bool:
    """Registra que a tentativa `numero` saiu para o PSP. False se não achou
    tentativa pendente e não disparada com esse número.

    `ciclo_id`, quando informado, é a forma precisa; sem ele, vale o ciclo
    mais recente com plano daquele mandato — que é o que o JSON guardava.
    """
    if ciclo_id is None:
        ciclo = ciclo_cobranca.ciclo_mais_recente_com_tentativas(tenant_id or TENANT_PADRAO,
                                                                 customer_id)
        if ciclo is None:
            return False
        ciclo_id = ciclo["id"]
    return ciclo_cobranca.marcar_disparada(ciclo_id, numero, id_cobranca, quando)


def tentativas_devidas(registro: dict, agora: datetime) -> list[dict]:
    """As tentativas do plano cuja data já chegou e que ainda não saíram.

    Uma tentativa sem data legível, ou cancelada, é tratada como NÃO devida:
    na dúvida, o limite regulatório aperta. Disparar cedo demais é enviar
    instrução fora da janela combinada; não disparar é, no pior caso, um
    ciclo a menos.
    """
    devidas = []
    for t in registro.get("tentativas") or []:
        if t.get("disparada_em"):
            continue
        if t.get("resultado") not in (None, ciclo_cobranca.PENDENTE):
            continue
        quando = _data(t.get("quando"))
        if quando is not None and quando <= agora:
            devidas.append(t)
    return sorted(devidas, key=lambda t: t.get("numero") or 0)


def limpar_tudo() -> None:
    """Esquece todos os ciclos, tentativas e eventos vistos. Existe para o
    teste e para a demo. O JSON legado não é tocado."""
    ciclo_cobranca.limpar_tudo()

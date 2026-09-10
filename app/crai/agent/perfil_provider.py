"""crai/agent/perfil_provider.py — de onde vem o perfil do cliente.

O PROBLEMA. Quatro das doze entradas do classificador — tenure, histórico de
pagamento, falhas em 90 dias e ticket médio, e por consequência o LTV — não vêm
do evento do PSP. Elas vêm do negócio: banco de dados, CRM, faturamento. Como
nada disso está conectado, `_perfil_simulado` as FABRICAVA com `np.random`,
semeado pelo id do cliente.

O perfil sintético é honesto enquanto está declarado, e para a banca ele é
suficiente: dá reprodutibilidade (mesmo cliente, mesmos números) e permite
demonstrar o pipeline inteiro sem integração nenhuma. O que ele não pode ser é
IRREMOVÍVEL. Enquanto a chamada estava embutida no meio de `_features_pix`,
plugar a fonte real significava reescrever o caminho de features — justamente
na véspera do treino, que é quando menos se quer mexer nisso.

O QUE ESTE MÓDULO FAZ: transforma "de onde vem o perfil" numa decisão de
configuração. `get_perfil(customer_id, invoice_amount)` é o contrato; o
provedor sintético é o default e continua devolvendo EXATAMENTE os mesmos
valores para a mesma semente; e o provedor de banco existe como stub, com o
ponto de conexão marcado, caindo no sintético enquanto não houver fonte.

O QUE ELE NÃO FAZ, de propósito: conectar banco nenhum. O escopo desta fase é
o ponto de extensão e o formato documentado (`README_treino.md`), para que a
fase de treino seja só "plugar" — não um refactor.

Mesmo padrão `load()` / fallback que os três módulos de ML já usam: sem fonte
configurada, o sistema funciona e diz no log que está no fallback, em vez de
falhar ou de fingir que tem dado real.
"""

import logging
import os
from datetime import datetime
from typing import Optional, Protocol, runtime_checkable

import numpy as np

from ..ml.ltv import ltv_estimado
from ..ml.synthetic_data import seed_por_cliente

logger = logging.getLogger(__name__)

ENV_FONTE_REAL = "CRAI_PERFIL_DB"

# As chaves que TODO provedor precisa devolver. É o contrato do dataset e do
# classificador ao mesmo tempo — um provedor que esqueça uma delas quebra a
# predição, e o teste `test_perfil_provider` reprova antes disso acontecer.
CAMPOS_DO_PERFIL = (
    "tenure_months", "day_of_month", "invoice_amount", "avg_ticket",
    "payment_history_score", "failure_count_90d", "hour_of_day",
    "day_of_week", "ltv_estimated",
)


@runtime_checkable
class PerfilProvider(Protocol):
    """Quem sabe dizer o perfil de um cliente.

    `Protocol` e não classe base abstrata: um provedor não precisa herdar nada
    para servir — um objeto de teste com o método certo já é aceito, e é o que
    torna o ponto de extensão barato de exercer.
    """

    def get_perfil(self, customer_id: str, invoice_amount: float) -> dict:
        """Tenure, histórico, ticket e LTV daquele cliente."""
        ...


class SyntheticPerfilProvider:
    """O perfil fabricado — o comportamento que existia antes deste módulo.

    Reprodutível por construção: a semente é derivada do `customer_id`
    (`seed_por_cliente`, um md5 estável), então o mesmo cliente recebe sempre o
    mesmo perfil, em qualquer máquina e em qualquer execução. É isso que faz a
    demo ser demonstrável duas vezes seguidas com o mesmo resultado.

    Os campos de calendário (`day_of_month`, `hour_of_day`, `day_of_week`) são
    do momento da falha, e não da semente: eles descrevem QUANDO a cobrança
    falhou, que é informação real do evento mesmo quando o resto é sintético.
    """

    def get_perfil(self, customer_id: str, invoice_amount: float) -> dict:
        agora = datetime.now()
        rng = np.random.default_rng(seed=seed_por_cliente(customer_id))

        tenure = int(rng.exponential(scale=12))
        payment_history = round(float(np.clip(rng.beta(5, 2), 0, 1)), 3)
        failure_count = int(rng.poisson(1.5))
        avg_ticket = round(invoice_amount * rng.uniform(0.9, 1.1), 2)

        return {
            "tenure_months": tenure,
            "day_of_month": agora.day,
            "invoice_amount": invoice_amount,
            "avg_ticket": avg_ticket,
            "payment_history_score": payment_history,
            "failure_count_90d": failure_count,
            "hour_of_day": agora.hour,
            "day_of_week": agora.weekday(),
            # A fórmula mora em `ml/ltv.py` desde o Sprint 7 — era a mesma conta
            # escrita também no gerador do dataset de treino, com outro fator
            # de retenção. Duas cópias divergiriam, e o LTV é o multiplicador do
            # e-Profit: inflado num lado e não no outro, o agente decidiria agir
            # com uma conta que o treino nunca viu.
            "ltv_estimated": ltv_estimado(tenure, avg_ticket, invoice_amount),
        }


class DBPerfilProvider:
    """O perfil REAL, vindo do banco/CRM do cliente. Stub declarado.

    ESTE É O PONTO DE CONEXÃO da fase de treino, e ele está vazio de propósito:
    conectar exige um banco que não existe neste escopo, e um cliente de banco
    inventado seria pior que um stub — passaria a "existir" no código e falharia
    na primeira consulta real, longe daqui.

    Sem `CRAI_PERFIL_DB` configurada, delega ao provedor sintético e diz isso no
    log uma vez. É o mesmo padrão `load()` / fallback dos módulos de ML: sem
    fonte, o sistema funciona e declara que está no fallback.

    Quando a fonte existir, o que muda é o corpo de `_consultar` — nem o nó do
    grafo, nem `_features_pix`, nem o dataset mudam de forma.
    """

    def __init__(self, fallback: Optional[PerfilProvider] = None):
        self._fallback = fallback or SyntheticPerfilProvider()
        self._avisou = False

    def disponivel(self) -> bool:
        """Lido a cada chamada, e não no import — mesma razão do `config.py`."""
        return bool(os.getenv(ENV_FONTE_REAL, "").strip())

    def get_perfil(self, customer_id: str, invoice_amount: float) -> dict:
        if not self.disponivel():
            if not self._avisou:
                logger.info(
                    "[PERFIL] Sem fonte real configurada (%s) — perfil SINTÉTICO. "
                    "Tenure, histórico de pagamento, falhas em 90d e LTV são "
                    "fabricados a partir do id do cliente. Ver "
                    "crai/agent/README_treino.md.", ENV_FONTE_REAL,
                )
                self._avisou = True
            return self._fallback.get_perfil(customer_id, invoice_amount)

        perfil = self._consultar(customer_id, invoice_amount)
        if perfil is None:
            # Cliente sem histórico é caso NORMAL, não erro: assinante novo, ou
            # migrado de outro sistema. Cair no sintético mantém o pipeline
            # rodando; derrubar a recuperação por falta de perfil seria trocar
            # um dado aproximado por nenhuma cobrança.
            logger.warning("[PERFIL] Sem perfil real para %s — caindo no "
                           "sintético para este cliente.", customer_id)
            return self._fallback.get_perfil(customer_id, invoice_amount)
        return perfil

    def _consultar(self, customer_id: str, invoice_amount: float) -> Optional[dict]:
        """TODO(treino): consultar a fonte real e devolver `CAMPOS_DO_PERFIL`.

        O formato esperado está em `crai/agent/README_treino.md`, com a origem
        de cada campo em produção. Devolver `None` quando não houver histórico
        para o cliente.
        """
        return None


def provedor_padrao() -> PerfilProvider:
    """O provedor que o pipeline usa: banco quando houver, sintético sempre."""
    return DBPerfilProvider(fallback=SyntheticPerfilProvider())

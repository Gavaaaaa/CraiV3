"""crai/dunning/retry_scheduler.py — quem dispara as tentativas 2 e 3 (Gap 1).

O PROBLEMA QUE ESTE ARQUIVO FECHA. A `PixAutomaticoRetryPolicy` devolve o plano
inteiro das tentativas restantes de uma vez — todas as instruções de pagamento
a reenviar dentro da janela de 7 dias. O nó `schedule_retry_pix` dispara a
primeira, quando ela já é devida. As outras duas caem em dias seguintes, e o
grafo já terminou: sem alguém que volte na data certa, elas simplesmente não
acontecem. O agente prometia usar as 3 tentativas que o BACEN concede ao
recebedor e usava uma.

O QUE ESTE MÓDULO É. A LÓGICA de disparo, testável e completa:
`processar_tentativas_devidas(agora)` percorre os planos pendentes
(`retry_state`), separa as tentativas cuja data chegou e ainda não saíram, e
reenvia cada uma pelo `pagarme_gateway`, marcando o disparo.

O QUE ELE NÃO É, e está fora do escopo declarado: o RELÓGIO. Em produção quem
chama esta função de tempos em tempos é um cron ou um worker — infraestrutura,
não agente. A separação é deliberada e é o que torna a regra testável: o teste
e a demo passam um `agora` avançado no tempo e observam as 3 tentativas saindo
em sequência, sem esperar 7 dias e sem depender de scheduler nenhum instalado.

O LIMITE DO BACEN NÃO PASSA POR AQUI, e isso é intencional. Este módulo não
concede tentativa: ele dispara o que a política já autorizou e gravou. A guarda
de `MAX_TENTATIVAS` abaixo é defesa em profundidade — se um estado corrompido
trouxer um plano com quatro entradas, o quarto não sai, e o log diz por quê.

Uma falha do PSP não consome a tentativa: `PagarmeIndisponivel` deixa a
tentativa SEM marca de disparo, e a próxima passagem do agendador tenta de
novo. Já uma violação de valor (`PixRetryPolicyViolation`) é defeito nosso e
para o plano — reexecutar não conserta um valor errado.
"""

import logging
from datetime import datetime
from typing import Optional

from ..integrations.pagarme_gateway import PagarmeIndisponivel, reenviar_cobranca_pix
from . import retry_state
from .pix_automatico_retry import MAX_TENTATIVAS, PixRetryPolicyViolation

logger = logging.getLogger(__name__)


async def disparar_tentativa(
    registro: dict, tentativa: dict, agora: Optional[datetime] = None,
) -> Optional[dict]:
    """Reenvia UMA tentativa ao PSP e marca o disparo. None se não saiu."""
    customer_id = registro["customer_id"]
    numero = tentativa.get("numero")

    if numero is None or numero > MAX_TENTATIVAS:
        logger.warning(
            "[RETRY-SCHED] %s: tentativa %r fora do limite do BACEN (%d) — "
            "não disparada. Plano inconsistente no estado gravado.",
            customer_id, numero, MAX_TENTATIVAS,
        )
        return None

    try:
        resultado = await reenviar_cobranca_pix(
            id_recorrencia=customer_id,
            valor=tentativa["valor"],
            e2e_ref=registro.get("e2e_id"),
            tenant_id=registro.get("tenant_id"),
            valor_original=registro.get("valor_original"),
            tentativa=numero,
        )
    except PixRetryPolicyViolation as e:
        # Defeito nosso: o plano gravado diverge do valor original. Marcar como
        # disparada esconderia o problema; deixar pendente faria o agendador
        # repetir o erro a cada passagem. Registra alto e não dispara.
        logger.error("[RETRY-SCHED] %s: tentativa %s recusada pela regra de "
                     "valor — %s", customer_id, numero, e)
        return None
    except PagarmeIndisponivel as e:
        # Condição externa: a tentativa continua devida e sai na próxima
        # passagem do cron. É por isso que a marca de disparo só é gravada
        # DEPOIS de o PSP aceitar.
        logger.warning("[RETRY-SCHED] %s: tentativa %s não saiu (%s) — segue "
                       "devida.", customer_id, numero, e)
        return None

    retry_state.marcar_disparada(
        customer_id=customer_id, numero=numero,
        id_cobranca=resultado.get("id_cobranca", ""),
        quando=agora, tenant_id=registro.get("tenant_id"),
    )
    print(f"[RETRY-SCHED] {customer_id}: tentativa {numero}/{MAX_TENTATIVAS} "
          f"disparada | R$ {tentativa['valor']:.2f} | {resultado.get('origem')}")
    return resultado


async def processar_tentativas_devidas(agora: Optional[datetime] = None) -> list[dict]:
    """Dispara todas as tentativas cuja data já chegou, em todos os planos.

    Args:
        agora: o instante de referência. Em produção, `datetime.now()` chamado
            pelo cron; em teste e na demo, um instante adiantado, que é o que
            permite ver as tentativas 2 e 3 sem esperar dias.

    Returns:
        Lista dos disparos efetivados (um dict por tentativa que saiu).
    """
    agora = agora or datetime.now()
    disparos: list[dict] = []

    for registro in retry_state.planos_pendentes():
        devidas = retry_state.tentativas_devidas(registro, agora)
        if not devidas:
            continue
        for tentativa in devidas:
            resultado = await disparar_tentativa(registro, tentativa, agora)
            if resultado is not None:
                disparos.append({
                    "customer_id": registro["customer_id"],
                    "tenant_id": registro.get("tenant_id"),
                    "numero": tentativa["numero"],
                    "valor": tentativa["valor"],
                    **resultado,
                })

    if disparos:
        logger.info("[RETRY-SCHED] %d tentativa(s) disparada(s) em %s",
                    len(disparos), agora.isoformat(timespec="seconds"))
    return disparos

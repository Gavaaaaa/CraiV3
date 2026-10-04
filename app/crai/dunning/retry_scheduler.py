"""crai/dunning/retry_scheduler.py — quem dispara as tentativas 2 e 3, e quem varre.

O PROBLEMA QUE ESTE ARQUIVO FECHA. A `PixAutomaticoRetryPolicy` devolve o plano
inteiro das tentativas restantes de uma vez — todas as instruções de pagamento
a reenviar dentro da janela de 7 dias. O nó `schedule_retry_pix` dispara a
primeira, quando ela já é devida. As outras duas caem em dias seguintes, e o
grafo já terminou: sem alguém que volte na data certa, elas simplesmente não
acontecem. O agente prometia usar as 3 tentativas que o BACEN concede ao
recebedor e usava uma.

O QUE ESTE MÓDULO É. A LÓGICA de disparo, testável e completa:
`processar_tentativas_devidas(agora)` percorre os ciclos `recobrando` com
tentativas pendentes (via `retry_state`, que desde a Etapa 1 lê e grava nas
tabelas de `crai/dunning/ciclo_cobranca.py`), separa as tentativas cuja data
chegou e ainda não saíram, reenvia cada uma pelo `pagarme_gateway`, marca o
disparo — e, ao fim da passagem, roda as três VARREDURAS do ciclo:

    sem_retorno          tentativa disparada há mais de 24 h sem resultado do
                         PSP vira `sem_retorno` (D6): conta como não paga para
                         a sequência, registrada diferente de `falhou`;
    reconciliados        ciclo `recuperado` cuja linha do `recovery_log`
                         continuava aberta (a falha caiu entre a transação do
                         ciclo e o fechamento best-effort da linha, B4-a): a
                         linha é fechada, com WARNING;
    reservas_orfas       mensagem reservada (`mensagem_enviada`) sem
                         confirmação de envio há mais de 15 min — o processo
                         morreu entre reservar e enviar (B3-a): a reserva é
                         desfeita e a mesma passagem reenvia, com a mesma
                         reserva contra duplicata;
    janelas_encerradas   ciclo `recobrando` cuja janela do BACEN encerrou com
                         tentativas ainda não disparadas: elas são canceladas
                         (`janela_encerrada`), porque não podem mais sair (D7);
    prazo_de_recuperacao ciclo `mensagem_enviada` há mais de
                         `PRAZO_RECUPERACAO_DIAS` vai a `perdido` (A3). A
                         janela do BACEN não entra nisto: o prazo conta da
                         mensagem, e um pagamento dentro dele fecha o ciclo
                         como recuperado.

Quando uma varredura deixa um ciclo sem tentativa pendente e com falha ou
sem retorno, o ciclo passa a PRECISAR de mensagem (R1). Este módulo chama
então `crai.agent.workflow.concluir_ciclo_por_resultado` para cada um — por
import tardio, o mesmo padrão de `httpx` no `pagarme_gateway`, porque
`workflow` importa este módulo e o ciclo de import não pode fechar. A
conclusão reserva o ciclo antes de enviar, então webhook e agendador
concorrendo não produzem duas mensagens.

O QUE ELE NÃO É, e está fora do escopo declarado: o RELÓGIO. Em produção quem
chama esta função de tempos em tempos é um cron ou um worker — infraestrutura,
não agente. A separação é deliberada e é o que torna a regra testável: o teste
e a demo passam um `agora` avançado no tempo e observam as 3 tentativas saindo
em sequência, sem esperar 7 dias e sem depender de scheduler nenhum instalado.

O LIMITE DO BACEN NÃO PASSA POR AQUI, e isso é intencional. Este módulo não
concede tentativa: ele dispara o que a política já autorizou e gravou. Desde a
Etapa 1 o próprio BANCO recusa a 4ª tentativa de um ciclo (`CHECK` + `UNIQUE`
em `tentativas_cobranca`); a guarda de `MAX_TENTATIVAS` abaixo é defesa em
profundidade para um registro montado à mão.

Uma falha do PSP não consome a tentativa: `PagarmeIndisponivel` deixa a
tentativa SEM marca de disparo, e a próxima passagem do agendador tenta de
novo. Já uma violação de valor (`PixRetryPolicyViolation`) é defeito nosso e
para o plano — reexecutar não conserta um valor errado.
"""

import logging
from datetime import datetime
from typing import Optional

from datetime import timedelta

from ..integrations.pagarme_gateway import PagarmeIndisponivel, reenviar_cobranca_pix
from . import ciclo_cobranca, recovery_log, retry_state
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

    # `ciclo_id` do registro: a forma precisa. Um registro montado à mão, sem
    # ciclo, cai na resolução por mandato de `retry_state`.
    retry_state.marcar_disparada(
        customer_id=customer_id, numero=numero,
        id_cobranca=resultado.get("id_cobranca", ""),
        quando=agora, tenant_id=registro.get("tenant_id"),
        ciclo_id=registro.get("ciclo_id"),
    )
    print(f"[RETRY-SCHED] {customer_id}: tentativa {numero}/{MAX_TENTATIVAS} "
          f"disparada | R$ {tentativa['valor']:.2f} | {resultado.get('origem')}")
    return resultado


async def processar_tentativas_devidas(agora: Optional[datetime] = None) -> list[dict]:
    """Dispara todas as tentativas cuja data já chegou, em todos os planos, e
    depois varre os ciclos (ver docstring do módulo).

    Args:
        agora: o instante de referência. Em produção, `datetime.now()` chamado
            pelo cron; em teste e na demo, um instante adiantado, que é o que
            permite ver as tentativas 2 e 3 sem esperar dias.

    Returns:
        Lista dos disparos efetivados (um dict por tentativa que saiu). O
        resultado das varreduras vai para o log e para `varrer_ciclos`, que
        pode ser chamada sozinha.
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
                    "ciclo_id": registro.get("ciclo_id"),
                    "numero": tentativa["numero"],
                    "valor": tentativa["valor"],
                    **resultado,
                })

    if disparos:
        logger.info("[RETRY-SCHED] %d tentativa(s) disparada(s) em %s",
                    len(disparos), agora.isoformat(timespec="seconds"))

    resultado = varrer_ciclos(agora)
    for ciclo_id in resultado["mensagem_devida"]:
        await concluir_ciclo_devido(ciclo_id, agora)
    # Etapa 2: os ciclos em `aguardando_escolha` — prazo de escolha (R8),
    # envio dentro da janela de contato, canal que apareceu, sugestões que
    # faltam. Quem decide, com a configuração de cada empresa, é o workflow.
    from ..agent.workflow import processar_pendencias_de_mensagem   # import tardio
    await processar_pendencias_de_mensagem(resultado, agora)
    return disparos


async def concluir_ciclo_devido(ciclo_id: int, agora: datetime) -> None:
    """A mensagem depois das tentativas, pelo agendador (R1)."""
    from ..agent.workflow import concluir_ciclo_por_resultado   # import tardio (ver docstring)
    await concluir_ciclo_por_resultado(ciclo_id, agora, motivo="tentativas_esgotadas")


def varrer_ciclos(agora: Optional[datetime] = None) -> dict:
    """As varreduras do ciclo (D6, D7, A3), com o resultado no log.

    Devolve o dict de `ciclo_cobranca.varrer`: `sem_retorno` (pares ciclo,
    número), `janelas_encerradas`, `perdidos` e `mensagem_devida` — os ciclos
    que ficaram sem tentativa pendente e com falha ou sem retorno, e que o
    Bloco 3 leva à mensagem.
    """
    agora = agora or datetime.now()
    resultado = ciclo_cobranca.varrer(agora)
    # B4-a: ciclo recuperado com a linha do dataset ainda aberta.
    horizonte = agora - timedelta(days=ciclo_cobranca.JANELA_DIAS
                                  + ciclo_cobranca.PRAZO_RECUPERACAO_DIAS)
    recuperados = ciclo_cobranca.ciclos_recuperados_desde(horizonte)
    for c in recuperados:
        c["_tentativas_executadas"] = ciclo_cobranca.tentativas_executadas(c["id"])
    resultado["reconciliados"] = recovery_log.reconciliar_recuperados(recuperados)
    for ciclo_id in resultado["reconciliados"]:
        print(f"[RETRY-SCHED] ciclo {ciclo_id}: linha do dataset fechada por reconciliação (B4-a)")
    for ciclo_id, numero in resultado["sem_retorno"]:
        print(f"[RETRY-SCHED] ciclo {ciclo_id}: tentativa {numero} sem retorno do PSP "
              f"há mais de {ciclo_cobranca.PRAZO_SEM_RETORNO} — registrada como sem_retorno")
    for ciclo_id in resultado["janelas_encerradas"]:
        print(f"[RETRY-SCHED] ciclo {ciclo_id}: janela do BACEN encerrada — tentativas "
              f"não disparadas canceladas")
    for ciclo_id in resultado["reservas_orfas"]:
        print(f"[RETRY-SCHED] ciclo {ciclo_id}: mensagem reservada há mais de "
              f"{ciclo_cobranca.PRAZO_RESERVA_DE_MENSAGEM} sem confirmação de envio — "
              f"reserva desfeita, reenvio nesta passagem")
    for ciclo_id in resultado["perdidos"]:
        print(f"[RETRY-SCHED] ciclo {ciclo_id}: {ciclo_cobranca.PRAZO_RECUPERACAO_DIAS} dias "
              f"depois da mensagem sem pagamento — perdido")
    for ciclo_id in resultado["perdidos_sem_canal"]:
        print(f"[RETRY-SCHED] ciclo {ciclo_id}: {ciclo_cobranca.PRAZO_SEM_CANAL_DIAS} dias sem "
              f"canal entregável para a mensagem — perdido (motivo: sem_canal)")
    for ciclo_id in resultado["mensagem_devida"]:
        logger.info("[RETRY-SCHED] ciclo %s: sem tentativa pendente e sem pagamento — "
                    "mensagem devida", ciclo_id)
    return resultado

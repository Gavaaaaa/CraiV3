"""crai/dunning/pix_automatico_retry.py — Retentativa regulada de Pix Automático.

O Pix Automático NÃO admite backoff livre. O BACEN regula o fluxo do Recebedor:

  1. No dia do vencimento existem **duas janelas automáticas** de tentativa
     (00h-08h e 18h-21h), geridas pelo PSP do pagador. A CRAI não age nelas e
     elas **não contam** no limite abaixo.
  2. Se as duas falharem, o recebedor tem direito a **no máximo 3 novas
     tentativas dentro de 7 dias corridos** contados do vencimento. Cada uma
     exige o reenvio de uma nova instrução de pagamento pelo recebedor.
  3. Toda tentativa é **pelo valor original** — cobrança parcial não existe
     neste fluxo.

Por isso este módulo é separado de `smart_backoff.py`: aquele é backoff
exponencial com jitter, desenhado para cartão, e aplicá-lo aqui violaria o
limite regulatório. O isolamento entre os dois caminhos é garantido por uma
aresta condicional no grafo (ver crai/agent/main_agent.py).

Dentro dessas restrições, sobra uma decisão de otimização: QUANDO usar as 3
tentativas. É aí que entra o Payday Engine (Módulo 3) — se a previsão de
liquidez for confiável e cair dentro da janela, as tentativas se concentram
nos dias em que o cliente provavelmente terá saldo. Caso contrário, o fallback
distribui uniformemente pelos dias restantes.

Toda decisão é logada com prefixo [PIX-RETRY], incluindo qual das duas
estratégias foi usada, para auditoria e demonstração.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ── Limites regulatórios (BACEN) — não são parâmetros de tuning ──────────
JANELA_DIAS = 7
MAX_TENTATIVAS = 3

# Confiança mínima da previsão do Payday Engine para ancorar as tentativas
# nela em vez de distribuir uniformemente.
CONFIANCA_MINIMA_PADRAO = 0.60

ORIGEM_PAYDAY = "payday_engine"
ORIGEM_FALLBACK = "fallback_uniforme"


class PixRetryPolicyViolation(Exception):
    """Agendamento violaria uma regra do BACEN para o fluxo do Recebedor."""


@dataclass(frozen=True)
class TentativaAgendada:
    """Uma nova instrução de pagamento a ser reenviada pelo recebedor."""

    numero: int          # 1..3, contando as tentativas já usadas
    quando: datetime
    valor: float         # sempre o valor original da cobrança
    origem: str          # ORIGEM_PAYDAY | ORIGEM_FALLBACK


class PixAutomaticoRetryPolicy:
    """Decide quando reenviar as instruções de pagamento dentro da janela legal."""

    def __init__(
        self,
        payday_inference=None,
        confianca_minima: float = CONFIANCA_MINIMA_PADRAO,
    ):
        """
        Args:
            payday_inference: instância de PaydayInference. O pipeline injeta a
                que já está carregada em workflow.py; os testes injetam um mock.
            confianca_minima: limiar de `confidence` do Módulo 3 abaixo do qual
                a previsão é ignorada e o fallback uniforme assume.
        """
        self._payday = payday_inference
        self.confianca_minima = confianca_minima

    # ══════════════════════════════════════════════════════════════════════
    # API PRINCIPAL
    # ══════════════════════════════════════════════════════════════════════

    async def schedule(
        self,
        customer_id: str,
        valor_original: float,
        vencimento: Optional[datetime] = None,
        tentativas_usadas: int = 0,
        valor: Optional[float] = None,
        agora: Optional[datetime] = None,
    ) -> list[TentativaAgendada]:
        """Agenda as tentativas restantes dentro da janela de 7 dias.

        Args:
            customer_id: identificador do cliente (id da recorrência, no Pix)
            valor_original: valor da cobrança que falhou
            vencimento: data de vencimento original (default: agora). O prazo
                de 7 dias corridos conta a partir dela.
            tentativas_usadas: quantas das 3 já foram gastas. As duas janelas
                automáticas do dia do vencimento NÃO contam aqui.
            valor: valor a cobrar. Se informado e diferente de `valor_original`,
                levanta PixRetryPolicyViolation — cobrança parcial não existe
                neste fluxo.
            agora: injeta o "momento presente" (testes). Default: datetime.now().

        Returns:
            Lista de TentativaAgendada, possivelmente vazia se a janela já
            expirou ou as 3 tentativas já foram usadas.

        Raises:
            PixRetryPolicyViolation: se o agendamento violaria limite de
                tentativas, prazo de 7 dias ou valor original.
        """
        agora = agora or datetime.now()
        vencimento = vencimento or agora

        if valor is not None and round(valor, 2) != round(valor_original, 2):
            raise PixRetryPolicyViolation(
                f"Pix Automático não admite cobrança parcial: tentativa de "
                f"agendar R$ {valor:.2f} sobre cobrança original de "
                f"R$ {valor_original:.2f}."
            )

        if tentativas_usadas < 0:
            raise PixRetryPolicyViolation(
                f"tentativas_usadas não pode ser negativo (recebido: {tentativas_usadas})"
            )

        prazo_final = vencimento + timedelta(days=JANELA_DIAS)
        restantes = MAX_TENTATIVAS - tentativas_usadas

        if restantes <= 0:
            logger.info(
                "[PIX-RETRY] %s: as %d tentativas permitidas já foram usadas — "
                "sem novo agendamento.", customer_id, MAX_TENTATIVAS,
            )
            return []

        # As duas janelas automáticas do dia do vencimento já rodaram sob
        # responsabilidade do PSP do pagador: a CRAI só age a partir do dia seguinte.
        inicio = max(agora, vencimento + timedelta(days=1))
        if inicio > prazo_final:
            logger.info(
                "[PIX-RETRY] %s: janela de %d dias corridos expirou em %s — "
                "sem novo agendamento.", customer_id, JANELA_DIAS,
                prazo_final.strftime("%d/%m/%Y"),
            )
            return []

        datas, origem = await self._escolher_datas(
            customer_id, inicio, prazo_final, restantes,
        )

        tentativas = [
            TentativaAgendada(
                numero=tentativas_usadas + i + 1,
                quando=data,
                valor=round(valor_original, 2),
                origem=origem,
            )
            for i, data in enumerate(datas)
        ]

        self._validar(tentativas, valor_original, inicio, prazo_final, tentativas_usadas)
        self._logar(customer_id, tentativas, origem, prazo_final)
        return tentativas

    # ══════════════════════════════════════════════════════════════════════
    # ESCOLHA DAS DATAS
    # ══════════════════════════════════════════════════════════════════════

    async def _escolher_datas(
        self, customer_id: str, inicio: datetime, prazo_final: datetime, restantes: int,
    ) -> tuple[list[datetime], str]:
        """Payday Engine se der; fallback uniforme caso contrário."""
        previsao = await self._consultar_payday(customer_id)

        if previsao is not None:
            confianca = float(previsao.get("confidence", 0.0))
            quando = previsao.get("timestamp")
            confiavel = confianca >= self.confianca_minima
            # Comparação por DIA, não por timestamp: o Payday Engine prevê o dia
            # em que entra dinheiro, e devolve uma hora convencional (10h). Comparar
            # o horário cheio descartaria uma previsão para hoje só porque a janela
            # do recebedor abriu à tarde — mesmo dia, mesma liquidez.
            na_janela = (
                isinstance(quando, datetime)
                and inicio.date() <= quando.date() <= prazo_final.date()
            )

            if confiavel and na_janela:
                datas = self._ancorar_na_liquidez(quando, inicio, prazo_final, restantes)
                if datas:
                    return datas, ORIGEM_PAYDAY
                # Rede de segurança do P1-7: com a janela aberta, zero tentativa
                # nunca é resposta válida. Antes do Sprint 2 este caminho
                # devolvia [] e o cliente ia direto para dunning.
                logger.warning("[PIX-RETRY] %s: ancoragem vazia — caindo para "
                               "fallback uniforme.", customer_id)
                return self._distribuir_uniforme(inicio, prazo_final, restantes), ORIGEM_FALLBACK

            motivo = (
                f"confiança {confianca:.0%} < {self.confianca_minima:.0%}"
                if not confiavel
                else "nenhum dia previsto cai na janela restante"
            )
            logger.info("[PIX-RETRY] %s: previsão de liquidez descartada (%s).",
                        customer_id, motivo)

        return self._distribuir_uniforme(inicio, prazo_final, restantes), ORIGEM_FALLBACK

    async def _consultar_payday(self, customer_id: str) -> Optional[dict]:
        """Previsão de liquidez do Módulo 3, ou None se indisponível."""
        if self._payday is None:
            return None
        try:
            return await self._payday.predict_next_window(customer_id)
        except Exception as e:  # o agendamento não pode cair por causa do modelo
            logger.warning("[PIX-RETRY] %s: Payday Engine indisponível (%s) — "
                           "usando fallback uniforme.", customer_id, e)
            return None

    @staticmethod
    def _dias_da_janela(inicio: datetime, prazo_final: datetime) -> int:
        """Quantos passos de 1 dia cabem em [inicio, prazo_final].

        Conta por `timedelta`, e **não** por data do calendário. Quando `agora`
        tem hora diferente da do vencimento, `inicio` e `prazo_final` deixam de
        compartilhar o horário: contar por data devolveria um dia a mais e a
        última tentativa cairia depois do prazo — violação do BACEN.
        """
        return (prazo_final - inicio).days

    @classmethod
    def _ancorar_na_liquidez(
        cls, previsto: datetime, inicio: datetime, prazo_final: datetime, restantes: int,
    ) -> list[datetime]:
        """Concentra as tentativas em torno do dia previsto de liquidez.

        A preferência continua sendo **para frente**: antes da entrada de
        dinheiro há menos saldo, então a tentativa vale mais depois do payday.
        Mas quando não cabem `restantes` dias para frente até o prazo, o bloco
        **recua** para trás, ocupando os dias livres entre `inicio` e o dia
        previsto (P1-8).

        O racional é assimétrico e é o ponto todo: uma tentativa antes do payday
        tem probabilidade menor de recuperar, mas **maior que zero**; descartá-la
        tem probabilidade exatamente zero. As 3 tentativas são um direito
        regulatório do recebedor, não um orçamento a economizar.

        As datas herdam o **horário de `inicio`**, não o de `previsto`. O Payday
        Engine prevê o DIA em que entra dinheiro e devolve uma hora convencional
        (10h) que não carrega informação — a mesma razão pela qual `na_janela` já
        comparava por data. Herdar o horário de `inicio` alinha o agendamento à
        janela do recebedor e é o que faz a contagem fechar exatamente.
        """
        dias_disponiveis = cls._dias_da_janela(inicio, prazo_final)
        if dias_disponiveis < 0:
            return []

        # Cabem no máximo (dias_disponiveis + 1) tentativas com 1 dia de intervalo.
        cabem = min(restantes, dias_disponiveis + 1)

        # Dia previsto, em offsets a partir de `inicio`, preso à janela.
        offset_previsto = (previsto.date() - inicio.date()).days
        offset_previsto = max(0, min(offset_previsto, dias_disponiveis))

        # O bloco começa no dia previsto; se ele não couber inteiro até o prazo,
        # recua o mínimo necessário — é aí que o preenchimento para trás entra.
        ultimo_inicio_possivel = dias_disponiveis + 1 - cabem
        inicio_bloco = min(offset_previsto, ultimo_inicio_possivel)

        return [inicio + timedelta(days=inicio_bloco + i) for i in range(cabem)]

    @classmethod
    def _distribuir_uniforme(
        cls, inicio: datetime, prazo_final: datetime, restantes: int,
    ) -> list[datetime]:
        """Espalha as tentativas pelos dias restantes, nunca a menos de 1 dia."""
        dias_disponiveis = cls._dias_da_janela(inicio, prazo_final)
        # Cabem no máximo (dias_disponiveis + 1) tentativas com 1 dia de intervalo.
        n = min(restantes, dias_disponiveis + 1)
        if n <= 1:
            return [inicio]

        passo = dias_disponiveis / (n - 1)
        return [inicio + timedelta(days=round(i * passo)) for i in range(n)]

    # ══════════════════════════════════════════════════════════════════════
    # VALIDAÇÃO — falha alto, não corrige em silêncio
    # ══════════════════════════════════════════════════════════════════════

    @staticmethod
    def _validar(
        tentativas: list[TentativaAgendada],
        valor_original: float,
        inicio: datetime,
        prazo_final: datetime,
        tentativas_usadas: int,
    ) -> None:
        """Confere as três invariantes do BACEN antes de devolver o agendamento."""
        total = tentativas_usadas + len(tentativas)
        if total > MAX_TENTATIVAS:
            raise PixRetryPolicyViolation(
                f"Agendamento resultaria em {total} tentativas — o limite do "
                f"BACEN é {MAX_TENTATIVAS} por janela de {JANELA_DIAS} dias."
            )

        anterior: Optional[datetime] = None
        for t in tentativas:
            if t.quando > prazo_final:
                raise PixRetryPolicyViolation(
                    f"Tentativa {t.numero} agendada para "
                    f"{t.quando.strftime('%d/%m/%Y %H:%M')}, além do prazo de "
                    f"{JANELA_DIAS} dias corridos que termina em "
                    f"{prazo_final.strftime('%d/%m/%Y %H:%M')}."
                )
            if t.quando < inicio:
                raise PixRetryPolicyViolation(
                    f"Tentativa {t.numero} agendada para "
                    f"{t.quando.strftime('%d/%m/%Y %H:%M')}, antes do início da "
                    f"janela do recebedor ({inicio.strftime('%d/%m/%Y %H:%M')})."
                )
            if round(t.valor, 2) != round(valor_original, 2):
                raise PixRetryPolicyViolation(
                    f"Tentativa {t.numero} com valor R$ {t.valor:.2f} != valor "
                    f"original R$ {valor_original:.2f} — Pix Automático não "
                    f"admite cobrança parcial."
                )
            if anterior is not None and (t.quando - anterior) < timedelta(days=1):
                raise PixRetryPolicyViolation(
                    f"Tentativas {t.numero - 1} e {t.numero} a menos de 1 dia "
                    f"de intervalo ({anterior.strftime('%d/%m %H:%M')} → "
                    f"{t.quando.strftime('%d/%m %H:%M')})."
                )
            anterior = t.quando

    @staticmethod
    def _logar(
        customer_id: str, tentativas: list[TentativaAgendada],
        origem: str, prazo_final: datetime,
    ) -> None:
        """Trilha de auditoria do agendamento, legível na demo da banca."""
        rotulo = ("previsão do Payday Engine" if origem == ORIGEM_PAYDAY
                  else "fallback uniforme")
        print(f"[PIX-RETRY] {customer_id}: {len(tentativas)} tentativa(s) agendada(s) "
              f"via {rotulo} | prazo BACEN: {prazo_final.strftime('%d/%m/%Y')}")
        for t in tentativas:
            print(f"[PIX-RETRY]   tentativa {t.numero}/{MAX_TENTATIVAS}: "
                  f"{t.quando.strftime('%d/%m %H:%M')} | R$ {t.valor:.2f}")
        logger.info("[PIX-RETRY] %s: %d tentativa(s) via %s, prazo %s",
                    customer_id, len(tentativas), origem, prazo_final.isoformat())

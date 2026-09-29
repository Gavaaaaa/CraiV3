"""crai/api/idempotencia.py — a mesma entrega, duas vezes, não conta duas vezes.

POR QUE ISTO EXISTE. Um PSP entrega webhook **at-least-once**: reenviar o
mesmo evento quando não recebe o 200 a tempo é o comportamento correto dele,
não uma anomalia. Do lado da CRAI, dois eventos idênticos custavam caro em
duas direções opostas:

    FALHA reenviada        → o pipeline rodava de novo e a política agendava
                             outro lote de tentativas na MESMA janela de 7
                             dias. A trava por `thread_id` (`_trava_do_cliente`)
                             serializa as execuções concorrentes, e o contador
                             do checkpoint impede o 3+3; o que nenhum dos dois
                             impede é gastar diagnóstico, LLM e chamada de PSP
                             de novo por um evento que já foi tratado.
    CONFIRMAÇÃO reenviada  → o ciclo fechava duas vezes e o success fee
                             (`amount * 0.15`) era contado em dobro. Como o fee
                             é a receita do modelo Outcome-as-a-Service, isso é
                             erro de faturamento, não de log.

O QUE ESTA CAMADA É. Duas formas, sob a MESMA interface (`registrar_se_novo`):

  persistente   as janelas do Pix (`EVENTOS_DE_FALHA`, `CICLOS_FECHADOS`)
                gravam em `eventos_vistos`, no arquivo do ciclo de cobrança
                (`crai/dunning/ciclo_cobranca.py`), sob `BEGIN IMMEDIATE`. Um
                reenvio do PSP depois de um REINÍCIO é reconhecido como
                reenvio; dois processos no mesmo arquivo veem a mesma janela.
                Era a limitação (1) e (2) da versão anterior deste módulo,
                fechada na Etapa 1 (Bloco 1, 28/09/2026). O que continua
                valendo é o limite do arquivo: dois workers em máquinas
                diferentes têm dois arquivos — a mesma dívida do ledger de
                desfecho, declarada em `docs/LIMITACOES.md`.
  em memória    a janela da API de clientes (`CLIENTES_API`) continua sendo um
                `OrderedDict` com TTL e teto de entradas — está fora do escopo
                da etapa, e a sua limitação (reinício zera) permanece.

Se o banco falhar numa janela persistente, a chamada NÃO levanta: loga alto e
cai para a janela em memória do processo, que ainda segura o reenvio dentro do
processo. Perder deduplicação entre reinícios é pior que derrubar a cobrança
de um cliente por causa do disco? Não — e é por isso que o fallback existe.

O TTL é a janela do BACEN (7 dias): é o horizonte em que um reenvio ainda
descreve o mesmo ciclo de cobrança. Passado isso, um evento com o mesmo par
de ids é um ciclo NOVO e deve mesmo ser processado.
"""

import json
import logging
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Optional

from ..dunning import ciclo_cobranca

logger = logging.getLogger(__name__)

# O horizonte em que um reenvio ainda fala do mesmo ciclo — ver docstring.
TTL_PADRAO = timedelta(days=7)

# Teto de entradas por janela. Passar disso descarta a mais antiga: perder a
# memória de um evento de 7 dias atrás é aceitável; crescer sem limite num
# processo que roda meses não é.
MAX_ENTRADAS = 10_000


def chave_do_evento(*partes) -> str:
    """Compõe a chave de um evento a partir de seus identificadores.

    Serializada como lista JSON, e **não** concatenada com um separador cru,
    pelo mesmo motivo já documentado em `_thread_id` (crai/api/app.py): com
    `|`, o par `("A|B", "C")` produz exatamente a mesma string que
    `("A", "B|C")` — dois eventos distintos colidindo numa chave só, o que aqui
    significaria descartar um evento legítimo como se fosse reenvio.
    """
    return json.dumps([str(p) for p in partes], ensure_ascii=False)


class JanelaDeIdempotencia:
    """Lembra as chaves vistas recentemente. `registrar_se_novo` é toda a API."""

    def __init__(self, escopo: str, ttl: timedelta = TTL_PADRAO,
                 max_entradas: int = MAX_ENTRADAS, persistente: bool = False):
        self.escopo = escopo
        self.ttl = ttl
        self.max_entradas = max_entradas
        # `persistente=True` grava em `eventos_vistos`; a memória do processo
        # vira só o fallback de quando o banco falha (ver docstring do módulo).
        self.persistente = persistente
        self._vistos: "OrderedDict[str, datetime]" = OrderedDict()
        # A janela é consultada de dentro de handlers async (um event loop) e,
        # nos testes, também de threads do TestClient. O lock é barato e torna
        # a leitura-e-escrita atômica em ambos os casos.
        self._trava = threading.Lock()

    def registrar_se_novo(self, chave: str, agora: Optional[datetime] = None) -> bool:
        """`True` se a chave é nova (siga em frente); `False` se é reenvio."""
        return self.registrar_se_novo_detalhado(chave, agora)[0]

    def registrar_se_novo_detalhado(self, chave: str,
                                    agora: Optional[datetime] = None) -> tuple[bool, bool]:
        """`(novo, persistido)`: se a chave é nova, e se a resposta veio do
        banco (`True`) ou da memória do processo (`False`, janela em memória
        ou fallback por banco indisponível — B2-a).

        Registra e responde no mesmo passo, sob trava: um `ja_visto()` seguido
        de um `marcar()` deixaria uma fresta entre a consulta e a escrita, que
        é exatamente por onde duas entregas simultâneas passariam as duas.
        """
        agora = agora or datetime.now()
        if self.persistente:
            try:
                return ciclo_cobranca.registrar_evento_se_novo(
                    self.escopo, chave, agora, self.ttl), True
            except Exception as e:               # noqa: BLE001 — fallback declarado
                # Marcador próprio, para o operador achar no log (B2-a).
                logger.warning("[IDEMPOTENCIA-FALLBACK] %s: banco indisponível (%s) — "
                               "usando a janela em memória deste processo para esta "
                               "chave. Um reenvio depois de reinício NÃO será "
                               "reconhecido enquanto o banco não voltar.",
                               self.escopo, e)
        with self._trava:
            self._expirar(agora)
            visto_em = self._vistos.get(chave)
            if visto_em is not None:
                logger.info("[IDEMPOTENCIA] %s: reenvio ignorado (visto em %s) — %s",
                            self.escopo, visto_em.isoformat(timespec="seconds"), chave)
                return False, False
            self._vistos[chave] = agora
            self._vistos.move_to_end(chave)
            while len(self._vistos) > self.max_entradas:
                self._vistos.popitem(last=False)
            return True, False

    def esquecer(self, chave: str) -> None:
        """Desfaz o registro de UMA chave cujo processamento falhou (B2-a).

        O PSP vai reenviar o evento, e o reenvio tem que ser processado como
        novo — na memória e no banco, os dois, porque não se sabe em qual dos
        dois a chave foi parar."""
        with self._trava:
            self._vistos.pop(chave, None)
        if self.persistente:
            try:
                ciclo_cobranca.esquecer_evento(self.escopo, chave)
            except Exception as e:               # noqa: BLE001
                logger.warning("[IDEMPOTENCIA] %s: não foi possível esquecer a chave no "
                               "banco (%s)", self.escopo, e)

    def _expirar(self, agora: datetime) -> None:
        """Descarta o que passou do TTL. As entradas estão em ordem de inserção."""
        limite = agora - self.ttl
        while self._vistos:
            chave, quando = next(iter(self._vistos.items()))
            if quando > limite:
                return
            self._vistos.popitem(last=False)

    def limpar(self) -> None:
        """Esquece tudo. Existe para o teste — ver `tests/conftest.py`."""
        with self._trava:
            self._vistos.clear()
        if self.persistente:
            try:
                ciclo_cobranca.limpar_eventos(self.escopo)
            except Exception as e:               # noqa: BLE001
                logger.warning("[IDEMPOTENCIA] %s: não foi possível limpar o banco (%s)",
                               self.escopo, e)

    def __len__(self) -> int:
        if self.persistente:
            try:
                return ciclo_cobranca.contar_eventos(self.escopo) + len(self._vistos)
            except Exception:                    # noqa: BLE001
                pass
        return len(self._vistos)


# As duas janelas do churn involuntário, PERSISTENTES (Etapa 1, 1.2). Separadas
# de propósito: um e2e_id de cobrança falhada e um de cobrança paga são
# transações diferentes, e misturá-los num balde só faria uma confirmação
# silenciar a falha seguinte.
EVENTOS_DE_FALHA = JanelaDeIdempotencia("pix_falha", persistente=True)
CICLOS_FECHADOS = JanelaDeIdempotencia("pix_recuperacao", persistente=True)

# A janela da API de sincronização de clientes (`api/clientes.py`). A chave é
# o header `Idempotency-Key` que o backend do cliente manda, composta com
# tenant, método e caminho (`chave_do_evento`): a mesma chave em tenants
# diferentes, ou em rotas diferentes, são requisições diferentes. Separada
# das janelas do Pix pelo mesmo motivo que elas são separadas entre si.
CLIENTES_API = JanelaDeIdempotencia("clientes_api")


def limpar_tudo() -> None:
    """Zera as três janelas. Usado pela fixture de isolamento dos testes."""
    EVENTOS_DE_FALHA.limpar()
    CICLOS_FECHADOS.limpar()
    CLIENTES_API.limpar()

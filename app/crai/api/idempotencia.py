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

O QUE ESTA CAMADA É — e o que ela não é. É uma janela de memória do processo:
um `OrderedDict` com TTL e teto de entradas. Consequências, escritas em vez de
descobertas depois:

  1. **Reinício zera a janela.** Um reenvio que chegue depois de um restart
     passa como novo. É a mesma limitação do `MemorySaver`
     (crai/agent/main_agent.py) e do contador do BACEN, e a mesma correção:
     persistência (PostgreSQL) — outra frente.
  2. **Dois processos têm janelas separadas.** A CRAI hoje só é correta em
     processo único, pelo mesmo motivo já documentado no grafo.
  3. O TTL é a janela do BACEN (7 dias): é o horizonte em que um reenvio ainda
     descreve o mesmo ciclo de cobrança. Passado isso, um evento com o mesmo
     par de ids é um ciclo NOVO e deve mesmo ser processado.

MVP DECLARADO: quando o DB entrar, a mesma interface (`registrar_se_novo`)
passa a consultar uma tabela com UNIQUE em (escopo, chave) e as três
limitações acima somem sem tocar em quem chama.
"""

import json
import logging
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Optional

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
                 max_entradas: int = MAX_ENTRADAS):
        self.escopo = escopo
        self.ttl = ttl
        self.max_entradas = max_entradas
        self._vistos: "OrderedDict[str, datetime]" = OrderedDict()
        # A janela é consultada de dentro de handlers async (um event loop) e,
        # nos testes, também de threads do TestClient. O lock é barato e torna
        # a leitura-e-escrita atômica em ambos os casos.
        self._trava = threading.Lock()

    def registrar_se_novo(self, chave: str, agora: Optional[datetime] = None) -> bool:
        """`True` se a chave é nova (siga em frente); `False` se é reenvio.

        Registra e responde no mesmo passo, sob trava: um `ja_visto()` seguido
        de um `marcar()` deixaria uma fresta entre a consulta e a escrita, que
        é exatamente por onde duas entregas simultâneas passariam as duas.
        """
        agora = agora or datetime.now()
        with self._trava:
            self._expirar(agora)
            visto_em = self._vistos.get(chave)
            if visto_em is not None:
                logger.info("[IDEMPOTENCIA] %s: reenvio ignorado (visto em %s) — %s",
                            self.escopo, visto_em.isoformat(timespec="seconds"), chave)
                return False
            self._vistos[chave] = agora
            self._vistos.move_to_end(chave)
            while len(self._vistos) > self.max_entradas:
                self._vistos.popitem(last=False)
            return True

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

    def __len__(self) -> int:
        return len(self._vistos)


# As duas janelas do churn involuntário. Separadas de propósito: um e2e_id de
# cobrança falhada e um de cobrança paga são transações diferentes, e misturá-
# los num balde só faria uma confirmação silenciar a falha seguinte.
EVENTOS_DE_FALHA = JanelaDeIdempotencia("pix_falha")
CICLOS_FECHADOS = JanelaDeIdempotencia("pix_recuperacao")

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

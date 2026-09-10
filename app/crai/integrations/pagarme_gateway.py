"""crai/integrations/pagarme_gateway.py — a SAÍDA para o Pagar.me (Pix Automático).

O `PixAutomaticoAdapter` (payment_gateway.py) é a ENTRADA: normaliza o que o PSP
manda. Este módulo é o outro sentido, e até o Sprint 2 ele não existia — com uma
consequência que a auditoria nomeou sem meias palavras: a
`PixAutomaticoRetryPolicy` **calcula** as datas das 3 tentativas e devolve uma
lista de `TentativaAgendada`, e nada, em lugar nenhum, reenviava a instrução de
pagamento ao PSP nessas datas. O agente decidia quando tentar e não tentava.

O QUE É UMA "TENTATIVA" NO PIX AUTOMÁTICO. Não é retentar uma transação
existente: é o recebedor **reenviar uma nova instrução de cobrança** sobre a
mesma autorização de recorrência, pelo valor original. Daí o nome da função —
`reenviar_cobranca_pix` — e daí o invariante de valor estar reforçado aqui, e
não só na política: quem fala com o PSP é este módulo, e é a última linha antes
do dinheiro.

DOIS MODOS, e o simulado é o default:

    simulado (default)   Nenhuma rede. Devolve um dict de sucesso com um id
                         fictício determinístico. É o que a banca roda, e é o
                         que a suíte roda: nenhum teste depende de credencial,
                         de internet ou do sandbox do PSP estar de pé.
    real                 `CRAI_PAGARME_LIVE=1` **e** `CRAI_PAGARME_API_KEY`
                         definidos. Sem a chave, ligar a env não basta: o
                         módulo recusa em vez de cair para simulado em
                         silêncio, porque "achei que estava cobrando" é pior
                         que "não cobrei".

O QUE ESTÁ DOCUMENTADO E O QUE ESTÁ MARCADO COMO TODO. A autenticação do
Pagar.me v5 é Basic com a secret key no usuário e senha vazia
(`Authorization: Basic base64("sk_...:")`), sobre `https://api.pagar.me/core/v5`
— isso é público e estável. O **endpoint exato e o corpo** da cobrança avulsa
sobre uma recorrência de Pix Automático dependem da conta e do contrato, e não
são conferíveis sem credencial: ficam em `CRAI_PAGARME_ENDPOINT`, com o default
marcado `TODO(integração)`. Inventar um caminho plausível seria pior que
declarar o que falta — a integração passaria a "existir" no código e falharia
na primeira chamada real, longe daqui.

NÃO TOCA EM CARTÃO. `reenviar_cobranca_pix` é exclusivo de Pix Automático; a
recobrança de cartão segue fora do pipeline ativo, em `dunning/legacy_card/`.
"""

import hashlib
import logging
import os
from typing import Optional

from ..dunning.pix_automatico_retry import PixRetryPolicyViolation

logger = logging.getLogger(__name__)

# ── Configuração ─────────────────────────────────────────────────────────
ENV_LIVE = "CRAI_PAGARME_LIVE"
ENV_API_KEY = "CRAI_PAGARME_API_KEY"
ENV_ENDPOINT = "CRAI_PAGARME_ENDPOINT"

BASE_URL = "https://api.pagar.me/core/v5"

# TODO(integração): confirmar contra a conta Pagar.me qual recurso cria uma
# cobrança avulsa sobre uma autorização de Pix Automático já existente. O valor
# abaixo é um PLACEHOLDER declarado, não uma afirmação sobre a API: quem ligar
# o modo real define `CRAI_PAGARME_ENDPOINT` com o caminho do contrato.
ENDPOINT_PADRAO = f"{BASE_URL}/recurrences/{{id_recorrencia}}/charges"

TIMEOUT_SEGUNDOS = 20.0

# Rótulos do resultado. `simulado` não é um terceiro estado de sucesso: é a
# mesma forma de resposta, com a origem declarada, para que quem lê o log da
# demo saiba exatamente o que aconteceu.
ORIGEM_SIMULADA = "simulado"
ORIGEM_REAL = "pagarme"


class PagarmeIndisponivel(Exception):
    """A chamada ao PSP não pôde ser feita ou não foi aceita.

    Separada de `PixRetryPolicyViolation` de propósito: violar o limite do
    BACEN é defeito NOSSO e não pode ser reexecutado; o PSP fora do ar é
    condição externa e a tentativa continua devida.
    """


def modo_real() -> bool:
    """Lido a cada chamada, e não no import.

    Uma constante de módulo congelaria o modo no momento em que o pacote foi
    importado — e a suíte, que liga e desliga a env por teste, mediria sempre o
    modo do primeiro import.
    """
    return os.getenv(ENV_LIVE, "").strip() in {"1", "true", "True"}


def _id_simulado(id_recorrencia: str, valor: float, e2e_ref: Optional[str],
                 tentativa: Optional[int]) -> str:
    """Id determinístico para o modo simulado.

    `sha256` e não `hash()`: o hash embutido de string é randomizado por
    processo (PYTHONHASHSEED), o que faria o id mudar a cada execução e a demo
    deixar de ser reprodutível — o mesmo motivo já documentado em `_thread_id`
    (crai/api/app.py) e no `create_deal` do HubSpot.
    """
    base = f"{id_recorrencia}|{valor:.2f}|{e2e_ref or ''}|{tentativa or 0}"
    return "ch_sim_" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


async def reenviar_cobranca_pix(
    id_recorrencia: str,
    valor: float,
    e2e_ref: Optional[str] = None,
    tenant_id: Optional[str] = None,
    valor_original: Optional[float] = None,
    tentativa: Optional[int] = None,
) -> dict:
    """Reenvia ao Pagar.me a instrução de cobrança de uma tentativa do BACEN.

    Args:
        id_recorrencia: a autorização de Pix Automático a cobrar. É o mesmo
            identificador opaco que o resto do pipeline usa — a chave Pix do
            pagador não passa por aqui, como não passa por lugar nenhum.
        valor: o valor a cobrar, que é sempre o da cobrança que falhou.
        e2e_ref: e2e da cobrança que originou o ciclo, para correlação no log.
        tenant_id: qual empresa cliente da CRAI gerou esta cobrança. Hoje só
            atribuição em log; o Sprint 4 o propaga por todo o involuntário.
        valor_original: quando informado, o valor da cobrança que abriu o ciclo.
            Divergir dele é violação do BACEN e levanta antes de qualquer rede.
        tentativa: qual das 3 tentativas da janela está sendo reenviada. Entra
            na composição do id simulado — sem ele, as 3 tentativas do mesmo
            ciclo recebiam o MESMO id fictício na demo, o que faria três
            cobranças distintas parecerem uma reenviada — e vai como metadado
            no modo real, onde é a chave natural de idempotência do PSP.

    Returns:
        dict com `sucesso`, `id_cobranca`, `origem` (simulado | pagarme) e o
        `valor` efetivamente enviado.

    Raises:
        PixRetryPolicyViolation: `valor` != `valor_original`. Cobrança parcial
            não existe no fluxo de Pix Automático, e esta é a última barreira
            antes de o pedido sair.
        PagarmeIndisponivel: modo real sem chave, ou a chamada falhou.
    """
    if valor_original is not None and round(valor, 2) != round(valor_original, 2):
        raise PixRetryPolicyViolation(
            f"Reenvio de R$ {valor:.2f} sobre cobrança original de "
            f"R$ {valor_original:.2f} — Pix Automático não admite cobrança "
            f"parcial nem majorada. Recorrência {id_recorrencia}."
        )
    if valor <= 0:
        raise PixRetryPolicyViolation(
            f"Reenvio de R$ {valor:.2f} para a recorrência {id_recorrencia}: "
            "uma instrução de pagamento precisa de valor positivo."
        )

    valor = round(valor, 2)
    etiqueta_tenant = f" | tenant {tenant_id}" if tenant_id else ""

    if not modo_real():
        id_cobranca = _id_simulado(id_recorrencia, valor, e2e_ref, tentativa)
        print(f"[PAGARME] (simulado) instrução reenviada: {id_recorrencia} | "
              f"R$ {valor:.2f} | cobrança {id_cobranca}{etiqueta_tenant}")
        return {"sucesso": True, "id_cobranca": id_cobranca,
                "origem": ORIGEM_SIMULADA, "valor": valor,
                "id_recorrencia": id_recorrencia}

    return await _reenviar_de_verdade(id_recorrencia, valor, e2e_ref,
                                      etiqueta_tenant, tentativa)


async def _reenviar_de_verdade(
    id_recorrencia: str, valor: float, e2e_ref: Optional[str],
    etiqueta_tenant: str, tentativa: Optional[int] = None,
) -> dict:
    """A chamada HTTP do modo real. Isolada para o simulado não pagar por ela."""
    chave = os.getenv(ENV_API_KEY, "").strip()
    if not chave:
        # Fail closed: com a env ligada e sem chave, a alternativa seria cair
        # para o simulado — e a operação acharia que cobrou.
        raise PagarmeIndisponivel(
            f"{ENV_LIVE}=1 sem {ENV_API_KEY} definida. O modo real não cai para "
            "simulado em silêncio: uma cobrança que não saiu precisa falhar alto."
        )

    # Importado aqui, e não no topo: o modo simulado — que é o default e o que a
    # suíte inteira exercita — não deve exigir a biblioteca de rede instalada.
    import httpx

    endpoint = (os.getenv(ENV_ENDPOINT, "").strip()
                or ENDPOINT_PADRAO.format(id_recorrencia=id_recorrencia))

    # TODO(integração): confirmar o nome dos campos do corpo contra a conta.
    # `amount` em centavos é a convenção da API v5 do Pagar.me; o restante
    # depende do recurso que a conta expõe para Pix Automático.
    corpo = {
        "amount": int(round(valor * 100)),
        "payment_method": "pix",
        "recurrence_id": id_recorrencia,
    }
    metadata = {}
    if e2e_ref:
        metadata["e2e_origem"] = e2e_ref
    if tentativa is not None:
        metadata["tentativa_bacen"] = str(tentativa)
    if metadata:
        corpo["metadata"] = metadata

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SEGUNDOS) as cliente:
            resposta = await cliente.post(endpoint, json=corpo, auth=(chave, ""))
    except Exception as e:                       # noqa: BLE001 — qualquer falha de rede
        raise PagarmeIndisponivel(
            f"falha ao falar com o Pagar.me ({endpoint}): {e}") from e

    if resposta.status_code >= 400:
        raise PagarmeIndisponivel(
            f"Pagar.me recusou o reenvio ({resposta.status_code}) para "
            f"{id_recorrencia}: {resposta.text[:200]}")

    try:
        dados = resposta.json()
    except Exception:                            # noqa: BLE001
        dados = {}

    id_cobranca = str(dados.get("id") or dados.get("charge_id") or "")
    print(f"[PAGARME] instrução reenviada: {id_recorrencia} | R$ {valor:.2f} | "
          f"cobrança {id_cobranca or 'sem id na resposta'}{etiqueta_tenant}")
    return {"sucesso": True, "id_cobranca": id_cobranca, "origem": ORIGEM_REAL,
            "valor": valor, "id_recorrencia": id_recorrencia}

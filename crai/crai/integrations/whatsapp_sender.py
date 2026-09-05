"""crai/integrations/whatsapp_sender.py — envio de WhatsApp do churn voluntário.

ESTADO ATUAL: SIMULADO, como o resto do MVP. O envio é um log; nenhuma
mensagem sai da máquina. `send_whatsapp` existe para que o ponto de
integração real seja UM só, conhecido e testado, em vez de nascer espalhado
no dia em que o BSP for contratado.

PONTO DE INTEGRAÇÃO REAL — o que trocar aqui e só aqui:
    O corpo de `_entregar` vira a chamada ao provedor (WhatsApp Cloud API da
    Meta, ou um BSP como Twilio/Zenvia/Gupshup). O que muda junto:
      1. Credencial por env (`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`),
         seguindo o padrão do `.env.example`.
      2. Mensagem ATIVA (fora da janela de 24h) exige TEMPLATE aprovado pela
         Meta — texto livre é recusado. As mensagens de retenção da CRAI são
         sempre ativas: o cliente não iniciou a conversa. Isso significa que o
         texto gerado pelo Claude precisa caber em variáveis de um template
         aprovado, não em texto solto. É a restrição que mais muda o desenho,
         e está declarada aqui para não ser descoberta em produção.
      3. E.164 obrigatório (`+5511912345678`). Ver `destino_utilizavel`.
      4. Retentativa e idempotência: o BSP pode aceitar e falhar depois, por
         callback. Hoje não há callback nenhum.

ISOLAMENTO (decisão do Sprint 3, declarada): este módulo é consumido APENAS
pelo churn voluntário. O `dunning_engine` do involuntário marca
`channel="whatsapp"` e imprime — ele nunca teve função de envio para extrair,
então não há duplicação a unificar, e não foi tocado. Quando a integração real
entrar, ela entra aqui e o dunning passa a chamar este módulo.
"""

import re

# Só dígitos, para medir o número sem depender de como ele foi formatado.
_SO_DIGITOS = re.compile(r"\D")

# 10 dígitos = DDD + número no Brasil sem código de país; 15 é o teto do E.164.
# Abaixo de 10 não é telefone; acima de 15 não é entregável em canal nenhum.
MIN_DIGITOS = 10
MAX_DIGITOS = 15


def destino_utilizavel(bruto) -> str | None:
    """O telefone do payload, em dígitos, ou None quando não dá para enviar.

    `props` é payload bruto de terceiro — o telefone chega como `None`, número,
    lista, string vazia ou `"(11) 91234-5678"`. Devolve só os dígitos, que é a
    forma que o provedor aceita depois de prefixada com o país.

    **Não inventa código de país.** `"11912345678"` (11 dígitos) é entregável no
    Brasil e ambíguo em qualquer lugar; prefixar `55` por conta própria assumiria
    o país do cliente a partir do país do desenvolvedor. Quando o tenant tiver
    país declarado (frente do Sprint 5), a normalização E.164 acontece com esse
    dado. Até lá, o número é repassado como veio e o provedor recusa o que não
    conseguir entregar — recusa visível é melhor que entrega no número errado.

    Número é PII: quem loga, loga `mascarar`.
    """
    if bruto is None or isinstance(bruto, bool):
        return None
    if isinstance(bruto, (int, float)):
        # `json` entrega `"phone": 5511912345678` como int. Float perde
        # precisão em número de telefone, então só o inteiro serve.
        if isinstance(bruto, float) and not bruto.is_integer():
            return None
        bruto = str(int(bruto))
    if not isinstance(bruto, str):
        return None

    digitos = _SO_DIGITOS.sub("", bruto)
    if not MIN_DIGITOS <= len(digitos) <= MAX_DIGITOS:
        return None
    return digitos


def mascarar(destino: str) -> str:
    """`5511912345678` → `*********5678`.

    O projeto cifra a chave Pix do pagador (`security/tokenization.py`) porque
    ela identifica uma pessoa. Um telefone identifica a mesma pessoa e sai em
    `print` para stdout, que vai para o log do container. Mascarar aqui é a
    mesma decisão aplicada ao canal novo, não uma nova política.
    """
    return "*" * max(0, len(destino) - 4) + destino[-4:]


async def _entregar(destino: str, message: str, tenant_id: str | None) -> bool:
    """O envio propriamente dito. HOJE: simulado.

    Trocar o corpo desta função pela chamada do provedor é a única mudança
    necessária para o WhatsApp sair de verdade. Ela é `async` desde já porque a
    chamada real é I/O de rede e o nó do grafo que a consome é `async`.
    """
    prefixo = f"[WHATSAPP] tenant={tenant_id} " if tenant_id else "[WHATSAPP] "
    # `->` em ASCII, não `→`: o console padrão do Windows abre em cp1252 e
    # um U+2192 no stdout MATA o processo (defeito N-12). O inventário daquela
    # catraca só pode descer, e módulo novo não entra nele.
    print(f"{prefixo}-> {mascarar(destino)}: {message[:90]}")
    return True


async def send_whatsapp(to, message: str, tenant_id: str | None = None) -> dict:
    """Envia a mensagem de retenção por WhatsApp.

    Devolve sempre um dict, nunca levanta: este é um nó de grafo e uma exceção
    aqui derrubaria o ciclo de retenção inteiro por causa de um número mal
    formatado. Destino inutilizável vira `{"sent": False}` com motivo, que o
    chamador loga e trata como canal indisponível.

    O `to` devolvido é MASCARADO de propósito — o número não precisa voltar
    para o estado do grafo nem para o CRM, e o que não circula não vaza.
    """
    destino = destino_utilizavel(to)
    if destino is None:
        print("[WHATSAPP] Destino inutilizável — nada enviado")
        return {"sent": False, "channel": "whatsapp", "to": None,
                "motivo": "destino_invalido", "simulado": True,
                "tenant_id": tenant_id}

    enviado = await _entregar(destino, message, tenant_id)
    return {"sent": enviado, "channel": "whatsapp", "to": mascarar(destino),
            "motivo": None, "simulado": True, "tenant_id": tenant_id}

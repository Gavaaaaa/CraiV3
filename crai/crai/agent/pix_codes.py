"""crai/agent/pix_codes.py — o vocabulário REAL de falha do Pix Automático.

O DEFEITO QUE ESTE MÓDULO FECHA. Até o Sprint 3, `_features_pix`
(crai/agent/workflow.py) atribuía a TODA falha de Pix o mesmo
`gateway_error_code = "insufficient_funds"`. Havia uma razão escrita para isso
— no fluxo do BACEN, as duas janelas automáticas do dia do vencimento já
tentaram debitar a conta, então ausência de saldo é a causa dominante — mas
"dominante" não é "única", e a consequência era um diagnóstico cego:

  - o classificador recebia uma feature constante, que não distingue nada;
  - `infer_payday` rodava sempre, inclusive quando o problema não tinha nada a
    ver com o dia em que entra dinheiro na conta;
  - `decide_recovery` mandava retentar sempre, inclusive nos dois casos em que
    retentar é garantidamente inútil — limite do pagador estourado e
    autorização revogada. Nesses dois, quem precisa agir é o cliente, e gastar
    as 3 tentativas do BACEN contra uma autorização revogada é gastar um
    direito regulatório em algo que não pode dar certo;
  - e — o que dói na fase de treino — o dataset de ciclos do Sprint 6 nasceria
    com `failure_cause` constante, ou seja, sem sinal nenhum.

O vocabulário do dunning, por sua vez, era o do Stripe (`expired_card`,
`do_not_honor`, ...): códigos de CARTÃO num pipeline que só fala Pix.

O QUE ESTE MÓDULO NÃO FAZ: adivinhar. O que se sabe com segurança é o
vocabulário INTERNO (as quatro causas abaixo, que o classificador e o dunning
consomem) e a semântica de cada uma. Os códigos externos que caem em cada
causa dependem do PSP e do contrato, e estão em dois grupos declarados:

    ALIASES_TEXTUAIS   as razões legíveis que os PSPs enviam no webhook. É o
                       grupo estável e o que o modo simulado exercita.
    CODIGOS_ISO_20022  os códigos de rejeição do arranjo Pix. Estão marcados
                       TODO(integração): a lista precisa ser conferida contra a
                       documentação da conta Pagar.me antes de produção. Estão
                       aqui porque um mapa vazio seria pior — o código
                       desconhecido cairia no default e ninguém saberia que
                       havia um mapeamento a fazer.

DEFAULT SEGURO: `processing_error`, e a escolha tem consequência. A tentação
seria manter `insufficient_funds`, que é o comportamento anterior — mas isso é
AFIRMAR que o pagador não tem saldo quando não se sabe, e essa afirmação vai
para a trilha de auditoria, para a mensagem do cliente e, no Sprint 6, para o
dataset de treino como rótulo. `processing_error` diz o que de fato se sabe:
algo falhou e a causa não foi reconhecida. Continua sendo retentável — um
código novo do PSP não deve custar ao cliente as tentativas a que ele tem
direito —, mas não dispara o Payday Engine nem promete um diagnóstico que
ninguém fez.
"""

import logging
import re

logger = logging.getLogger(__name__)

# ── Vocabulário interno ──────────────────────────────────────────────────
# É o que `AgentState.failure_cause` carrega, o que o classificador recebe como
# feature categórica e o que o dunning usa para escolher a mensagem.

CAUSA_SALDO = "insufficient_funds"        # retentável: pode haver saldo depois
CAUSA_LIMITE = "limit_exceeded"           # NÃO retentável: o teto é do pagador
CAUSA_REVOGADA = "authorization_revoked"  # NÃO retentável: não há mais mandato
CAUSA_TECNICA = "processing_error"        # retentável: falha do PSP/rede

CAUSA_PADRAO = CAUSA_TECNICA

CAUSAS_PIX = frozenset({CAUSA_SALDO, CAUSA_LIMITE, CAUSA_REVOGADA, CAUSA_TECNICA})

# Por que cada uma é ou não retentável — o texto entra na trilha de auditoria e
# na explicação que a banca lê, então mora junto do mapa, não num slide.
EXPLICACAO_DA_CAUSA = {
    CAUSA_SALDO: ("conta sem saldo no momento do débito — retentar na janela de "
                  "liquidez prevista pode resolver sem o cliente fazer nada"),
    CAUSA_LIMITE: ("o valor excede o limite que o pagador configurou para o Pix "
                   "Automático — nenhuma retentativa passa enquanto o teto não "
                   "subir, e só o cliente pode aumentá-lo"),
    CAUSA_REVOGADA: ("a autorização de recorrência foi revogada — sem mandato "
                     "não existe cobrança a reenviar; é preciso reautorizar"),
    CAUSA_TECNICA: ("falha técnica no processamento — nova instrução costuma "
                    "passar; é o default de código não reconhecido"),
}

# ── Razões legíveis enviadas pelos PSPs ──────────────────────────────────
ALIASES_TEXTUAIS = {
    # saldo
    "insufficient_funds": CAUSA_SALDO,
    "insufficient_balance": CAUSA_SALDO,
    "saldo_insuficiente": CAUSA_SALDO,
    "sem_saldo": CAUSA_SALDO,
    "not_enough_funds": CAUSA_SALDO,
    # limite do pagador
    "limit_exceeded": CAUSA_LIMITE,
    "amount_limit_exceeded": CAUSA_LIMITE,
    "limite_excedido": CAUSA_LIMITE,
    "limite_do_pagador_excedido": CAUSA_LIMITE,
    "value_above_limit": CAUSA_LIMITE,
    "exceeds_authorized_amount": CAUSA_LIMITE,
    # autorização
    "authorization_revoked": CAUSA_REVOGADA,
    "autorizacao_revogada": CAUSA_REVOGADA,
    "mandate_revoked": CAUSA_REVOGADA,
    "mandate_not_found": CAUSA_REVOGADA,
    "recurrence_cancelled": CAUSA_REVOGADA,
    "recurrence_canceled": CAUSA_REVOGADA,
    "authorization_expired": CAUSA_REVOGADA,
    # técnico
    "processing_error": CAUSA_TECNICA,
    "erro_processamento": CAUSA_TECNICA,
    "psp_error": CAUSA_TECNICA,
    "internal_error": CAUSA_TECNICA,
    "timeout": CAUSA_TECNICA,
    "service_unavailable": CAUSA_TECNICA,
}

# ── Códigos de rejeição do arranjo Pix (ISO 20022) ───────────────────────
# TODO(integração): conferir esta lista contra a documentação da conta Pagar.me
# antes de ligar `CRAI_PAGARME_LIVE=1`. Os códigos abaixo são os de uso corrente
# no padrão ISO 20022 que o arranjo Pix adota; o subconjunto exato que o PSP
# devolve numa recorrência de Pix Automático é contratual. Um código fora desta
# lista cai no default seguro e sai no log, que é como se descobre o que falta.
CODIGOS_ISO_20022 = {
    "AM04": CAUSA_SALDO,      # insufficient funds
    "AM02": CAUSA_LIMITE,     # not allowed amount
    "AM18": CAUSA_LIMITE,     # invalid number of transactions
    "MD01": CAUSA_REVOGADA,   # no mandate
    "MD07": CAUSA_REVOGADA,   # end customer deceased / mandato encerrado
    "AC06": CAUSA_REVOGADA,   # blocked account
    "AB03": CAUSA_TECNICA,    # settlement failed
    "AG03": CAUSA_TECNICA,    # transaction type not supported
    "MS03": CAUSA_TECNICA,    # reason not specified
}

PIX_CODE_MAP = {**ALIASES_TEXTUAIS, **CODIGOS_ISO_20022}

# Retentar só faz sentido onde uma nova instrução pode, mecanicamente, passar.
CAUSAS_RETENTAVEIS_PIX = frozenset({CAUSA_SALDO, CAUSA_TECNICA})

_NAO_ALFANUMERICO = re.compile(r"[^a-z0-9]+")


def _normalizar(bruto) -> str:
    """`"Saldo Insuficiente"`, `"SALDO-INSUFICIENTE"` e `"saldo insuficiente"` são o mesmo.

    PSPs variam maiúsculas, acento de separador e espaço; tratar cada variação
    como código distinto faria o mapa errar por formatação, que é o tipo de
    falha que só aparece em produção.
    """
    if bruto is None:
        return ""
    texto = str(bruto).strip().lower()
    return _NAO_ALFANUMERICO.sub("_", texto).strip("_")


def causa_do_codigo(bruto) -> str:
    """Traduz o código de falha do PSP para o vocabulário interno.

    Qualquer entrada é aceita — inclusive `None`, número ou lixo — e qualquer
    entrada sai como uma das quatro causas conhecidas. É requisito, não
    conveniência: o valor vira feature categórica do classificador e coluna do
    dataset de treino; deixar passar uma string arbitrária de terceiro
    envenenaria os dois, do mesmo jeito que o `bandit_state.json` do churn
    voluntário acumulou perfis de fuzz.
    """
    chave = _normalizar(bruto)
    if not chave:
        return CAUSA_PADRAO

    causa = PIX_CODE_MAP.get(chave)
    if causa is not None:
        return causa

    # ISO 20022 é case-sensitive na doc e vem normalizado em minúsculas aqui.
    causa = {k.lower(): v for k, v in CODIGOS_ISO_20022.items()}.get(chave)
    if causa is not None:
        return causa

    logger.info(
        "[PIX-CODE] Código de falha %r não reconhecido — diagnosticado como %r. "
        "Se aparecer com frequência, é entrada a acrescentar no PIX_CODE_MAP.",
        str(bruto)[:64], CAUSA_PADRAO,
    )
    return CAUSA_PADRAO


def e_retentavel(causa: str) -> bool:
    """Uma nova instrução de pagamento pode, mecanicamente, resolver esta causa?"""
    return causa in CAUSAS_RETENTAVEIS_PIX

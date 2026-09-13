"""crai/config.py — os parâmetros de NEGÓCIO, num lugar só.

O QUE ESTAVA ERRADO. Dois números que definem o modelo de negócio da CRAI
estavam escritos à mão dentro de nós do grafo:

    update_roi_dashboard   `amount * 0.15` — o success fee, que é a RECEITA da
                           CRAI. Varia por contrato e por cliente.
    check_anomaly          `cost = 0.05` — o custo do bot de WhatsApp, que
                           entra no recálculo do e-Profit e portanto decide se
                           a CRAI age ou não. Varia por canal e por operação.

Enquanto são literais, mudar a comissão de um contrato é editar código e
publicar; e o mesmo número aparecendo em dois arquivos diverge no dia em que um
for corrigido. O custo do WhatsApp já vivia duplicado — em
`ml/failure_classifier.py`, dentro de `INTERVENTION_COSTS`, e de novo no nó de
anomalia.

O QUE ESTE MÓDULO É: leitura de env com default igual ao valor atual. Nenhum
comportamento muda sem env configurada, e é requisito, não intenção — a demo,
as métricas do README e a suíte inteira precisam continuar dando exatamente os
mesmos números.

LIDO A CADA CHAMADA, e não no import. Uma constante de módulo congelaria o valor
no momento em que o pacote foi importado, e a suíte, que liga e desliga env por
teste, mediria sempre o primeiro import. É a mesma razão de `modo_real()` no
pagarme_gateway e de `caminho_do_banco()` no retention_log.

POR TENANT, DEPOIS. O fee real varia por contrato, e o contrato é do tenant
(Sprint 4). Estes valores são GLOBAIS por instalação: quando a configuração por
tenant existir, é a implementação destas funções que muda — quem chama já passa
pelo ponto certo.
"""

import logging
import os

logger = logging.getLogger(__name__)

# ── Envs ─────────────────────────────────────────────────────────────────
ENV_SUCCESS_FEE = "CRAI_SUCCESS_FEE_PCT"
ENV_CUSTO_WHATSAPP = "CRAI_CUSTO_INTERVENCAO_WHATSAPP"
ENV_CUSTO_TENTATIVA_PIX = "CRAI_CUSTO_TENTATIVA_PIX"

# ── Defaults: exatamente os números que estavam no código ────────────────
SUCCESS_FEE_PCT_PADRAO = 0.15
CANAL_PADRAO = "bot_whatsapp"

# Custos de intervenção por canal (R$). A tabela mora aqui desde o Sprint 5 —
# antes vivia em `ml/failure_classifier.py`, que é onde ela é consumida, mas
# custo de canal é parâmetro de negócio, não de modelo.
CUSTOS_PADRAO = {
    "bot_whatsapp": 0.05,
    "email_auto": 0.02,
    "sms": 0.08,
    "ligacao_cs": 15.00,
    "pix_boleto_link": 0.50,
}

# Canais que dependem de uma PESSOA do lado da CRAI. Escalonamento humano é
# zero neste produto: estes canais continuam na tabela de custos — o
# comparativo de e-Profit os mostra, com o motivo do descarte — mas nunca são
# elegíveis como canal de envio, em nenhum caminho.
CANAIS_HUMANOS = frozenset({"ligacao_cs"})

# Custo por instrução de pagamento reenviada ao PSP. Default ZERO, e isso é
# deliberado: o número real depende do contrato com o Pagar.me e não é conhecido
# aqui. Com zero, o e-Profit sai idêntico ao de antes deste sprint — nenhuma
# regressão silenciosa nas métricas já publicadas no README. Quem souber o custo
# do próprio contrato liga a env e o e-Profit passa a descontar as tentativas
# planejadas, que é o refinamento que o Gap 6 pedia.
CUSTO_TENTATIVA_PIX_PADRAO = 0.0


def _float_da_env(nome: str, padrao: float, minimo: float = 0.0,
                  maximo: float = 1e6) -> float:
    """Lê um float da env, com default e faixa. Valor torto cai no default.

    Cair no default, e não levantar, é escolha: um `CRAI_SUCCESS_FEE_PCT=abc`
    numa variável de ambiente de produção derrubaria o pipeline de cobrança de
    todos os clientes no deploy. O aviso alto no log é a resposta proporcional
    — e a faixa impede o oposto do erro barulhento, que é um fee de 900% ou
    negativo passando em silêncio.
    """
    bruto = os.getenv(nome)
    if bruto is None or not str(bruto).strip():
        return padrao
    try:
        valor = float(str(bruto).strip().replace(",", "."))
    except (TypeError, ValueError):
        logger.warning("[CONFIG] %s=%r não é número — usando o default %s.",
                       nome, bruto, padrao)
        return padrao
    if not (minimo <= valor <= maximo):
        logger.warning("[CONFIG] %s=%s fora da faixa [%s, %s] — usando o "
                       "default %s.", nome, valor, minimo, maximo, padrao)
        return padrao
    return valor


def success_fee_pct() -> float:
    """Percentual do valor recuperado que a CRAI cobra (Outcome-as-a-Service).

    Teto de 1.0: cobrar mais de 100% do valor recuperado não é um contrato, é
    um erro de digitação — e um que sairia caro no primeiro fechamento de ciclo.
    """
    return _float_da_env(ENV_SUCCESS_FEE, SUCCESS_FEE_PCT_PADRAO, 0.0, 1.0)


def custo_intervencao(canal: str = CANAL_PADRAO) -> float:
    """Custo de uma intervenção no canal. Só o WhatsApp é configurável hoje.

    É o canal do caminho ativo — os outros quatro entram no comparativo de
    canal ótimo do classificador e não são acionados pelo pipeline. Quando
    forem, cada um ganha a própria env pelo mesmo molde.
    """
    if canal == CANAL_PADRAO:
        return _float_da_env(ENV_CUSTO_WHATSAPP, CUSTOS_PADRAO[CANAL_PADRAO])
    return CUSTOS_PADRAO.get(canal, CUSTOS_PADRAO[CANAL_PADRAO])


def custos_por_canal() -> dict:
    """A tabela de custos com os overrides de env aplicados."""
    return {canal: custo_intervencao(canal) for canal in CUSTOS_PADRAO}


def custo_tentativa_pix() -> float:
    """Custo de UMA instrução de pagamento reenviada ao PSP. Default zero."""
    return _float_da_env(ENV_CUSTO_TENTATIVA_PIX, CUSTO_TENTATIVA_PIX_PADRAO)

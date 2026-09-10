"""crai/ml/ltv.py — a fórmula de LTV, uma vez só.

POR QUE ISTO EXISTE. O LTV estimado aparecia escrito duas vezes, com dois
valores diferentes para o fator de retenção:

    ml/synthetic_data.py   `tenure * avg_ticket * retention_factor / 12`, com
                           `retention_factor` variando de 0,80 a 0,98 conforme
                           o tenure — é o gerador do dataset de TREINO.
    agent/workflow.py      `tenure * avg_ticket * 0.9 / 12` no perfil sintético
                           do pipeline — é o que alimenta a PREDIÇÃO.

Duas cópias da mesma regra de negócio divergem no dia em que uma for corrigida,
e esta em particular divergiria em silêncio: o LTV não é feature de treino, é o
multiplicador do e-Profit. Um LTV inflado num lado e não no outro faria o agente
decidir agir com uma conta que o treino nunca viu.

O QUE ESTE MÓDULO **NÃO** MUDA: nenhum número. A função abaixo reproduz
exatamente as duas expressões — o fator de retenção continua sendo parâmetro, e
cada chamador passa o seu. O que deixa de existir é a fórmula duplicada.

DUAS TRILHAS DE ARREDONDAMENTO, e não é preciosismo. `round` do Python e
`np.round` não concordam em todo float: o do numpy multiplica por 100,
arredonda e divide, e o erro de ponto flutuante desse caminho move o resultado
em um centavo em torno de 0,5% dos casos (medido: 11 clientes em 2.000). Como
este sprint não pode mudar NENHUM valor — o perfil sintético precisa ser
reprodutível contra o que já existia, e o dataset de treino contra o que já foi
gerado —, cada trilha mantém o arredondamento que sempre teve: escalar pelo
`round` do Python, array pelo `.round()` do numpy.

A ordem entre `round` e `max`, essa sim, é indiferente: `round` é monótona não
decrescente, então `round(max(a, b)) == max(round(a), round(b))`. O
`synthetic_data` arredondava antes; o `workflow`, depois.

NÃO CONFUNDIR COM O FALLBACK do classificador (`invoice_amount * 6`, em
`failure_classifier.predict`). Aquilo não é a fórmula: é o que sobra quando o
chamador não informa LTV nenhum, e vale como piso grosseiro, não como estimativa.
"""

import numpy as np

# Fator de retenção do perfil sintético do pipeline. Fixo, e diferente do
# gerador de dataset — que o modula pelo tenure — porque o perfil do pipeline é
# um placeholder até a fonte real de dados entrar (ver
# `crai/agent/perfil_provider.py`). Está aqui, nomeado, em vez de solto como
# `0.9` no meio de uma expressão.
RETENCAO_PADRAO = 0.9

# Piso: o LTV nunca é menor que a própria cobrança. Um cliente que deve R$ 300
# vale ao menos R$ 300 — assumir menos faria o e-Profit rejeitar recuperações
# que se pagam sozinhas.
MESES_DO_ANO = 12


def ltv_estimado(tenure_months, avg_ticket, invoice_amount,
                 retention_factor=RETENCAO_PADRAO):
    """LTV estimado do cliente. Aceita escalares ou arrays do numpy.

    Args:
        tenure_months: meses de casa.
        avg_ticket: ticket médio mensal.
        invoice_amount: valor da cobrança que falhou — é o piso do LTV.
        retention_factor: fração do tenure que se espera manter. O gerador de
            dataset o modula pelo tenure (0,80–0,98); o perfil do pipeline usa
            `RETENCAO_PADRAO`.

    Returns:
        O mesmo tipo que entrou: `float` para escalares, `ndarray` para arrays.
    """
    entradas = (tenure_months, avg_ticket, invoice_amount, retention_factor)
    if all(np.ndim(x) == 0 for x in entradas):
        # Trilha escalar — o `round` do Python, como no perfil do pipeline.
        bruto = tenure_months * avg_ticket * retention_factor / MESES_DO_ANO
        return round(max(invoice_amount, bruto), 2)

    # Trilha vetorizada — o `.round()` do numpy, como no gerador do dataset.
    bruto = np.round(
        np.asarray(tenure_months) * np.asarray(avg_ticket)
        * np.asarray(retention_factor) / MESES_DO_ANO,
        2,
    )
    return np.round(np.maximum(bruto, np.asarray(invoice_amount)), 2)

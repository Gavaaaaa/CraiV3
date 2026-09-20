"""
crai/ml/populacao.py — A população compartilhada da base v2.

O problema que este módulo resolve
──────────────────────────────────
Até a base v1 (14/09/2026), os quatro modelos da CRAI eram treinados em quatro
datasets gerados de forma independente. A auditoria de 15/09 mediu duas
consequências:

1. **Interseção zero de `customer_id`.** Nenhum cliente aparecia em mais de uma
   base. Os Módulos 1 e 4 nem tinham a coluna.
2. **A mesma grandeza com três valores.** Mediana do valor mensal: R$ 97,44
   (Módulo 1), R$ 4.888,16 (Módulo 2), R$ 1.787,72 (Módulo 4). Razão de 50,2×.
   Causa: o Módulo 1 calibrava `invoice_amount` no ticket do Olist, que é
   varejo, não assinatura B2B.

O que **não** era o problema: ter quatro tabelas. Os quatro modelos preveem em
grãos diferentes — evento de cobrança, retrato de cliente, série diária, evento
de SDK. Grãos diferentes exigem tabelas diferentes. O que faltava era a **chave
que as liga** e a **população única** de onde todas derivam.

O desenho
─────────
Uma população de N clientes. Cada cliente tem os atributos que são dele e não
da observação: MRR, perfil de cobrança, tempo de casa, assentos. As quatro
tabelas de treino são **visões** dessa população, cada uma no seu grão:

    populacao (N clientes)
      ├── classificador   → várias cobranças por cliente        (grão: cobrança)
      ├── comportamental  → um retrato por cliente              (grão: cliente)
      ├── liquidez        → subamostra × 180 dias               (grão: cliente-dia)
      └── voluntario      → vários eventos por cliente          (grão: evento)

Um cliente que aparece em três tabelas tem **o mesmo MRR** nas três. É isso que
a interseção zero impedia e é isso que torna a junção possível.

Os latentes
───────────
`satisfacao`, `fit_produto`, `pressao_preco` e `saude_financeira` são gravados
na população e **não entram como feature de treino em nenhum modelo** nesta
etapa. Existem por dois motivos:

- São a semente do **rótulo latente** (etapa posterior, com portão próprio). O
  rótulo hoje ainda é calculado por fórmula a partir das mesmas features que o
  modelo vê — circular, e é a razão de o teto de Bayes medido ser 0,7095.
- Permitem calcular o teto de Bayes por construção, já que quem gerou o dado
  conhece a causa.

Determinismo
────────────
Tudo depende só de `seed`. Nenhum `date.today()`, nenhum `np.random` global.
Mesma semente → mesma população, byte a byte.
"""

from typing import Optional

import numpy as np
import pandas as pd

SEED = 42

# Perfis de recebimento — as mesmas proporções do Módulo 3 da base v1
# (`synthetic_data.LIQUIDITY_PROFILES` / `LIQUIDITY_PROFILE_PROBS`). O perfil
# deixa de ser sorteado dentro do gerador de liquidez e passa a ser atributo
# do cliente, porque é isso que ele é.
PERFIS_COBRANCA = ["CLT", "PJ", "freelancer"]
PERFIS_COBRANCA_PROBS = [0.50, 0.30, 0.20]

# MRR — lognormal calibrada para SaaS B2B brasileiro, não para varejo.
#   mediana = e^7.5   ≈ R$ 1.808
#   p90     = e^(7.5 + 1,2816·1,15) ≈ R$ 7.940
# A escala do Olist (mediana R$ 100) continua válida para `day_of_month`, que é
# o que ela realmente mediu; ela não serve para valor de assinatura.
MRR_LOGNORMAL = {"mean": 7.5, "sigma": 1.15, "clip": (200.0, 100_000.0)}

# Tempo de casa — a mesma mistura da base v1: ~65% exponencial (clientes novos,
# escala 6 meses) + ~35% uniforme em 12–72 meses (cauda longa).
TENURE_EXP_SCALE = 6.0
TENURE_FRACAO_NOVOS = 0.65
TENURE_ANTIGOS_RANGE = (12, 72)
TENURE_CLIP = (0, 72)

# Assentos — correlacionados com o MRR. R$ 120 por assento é o preço implícito;
# com a mediana de MRR acima isso dá ~15 assentos na mediana, que é exatamente
# o `lam` do Poisson da base v1. A correlação é o que muda: lá era independente.
PRECO_POR_ASSENTO = 120.0
ASSENTOS_CLIP = (1, 500)

LATENTES = ["satisfacao", "fit_produto", "pressao_preco", "saude_financeira"]

COLUNAS_POPULACAO = [
    "customer_id",
    "mrr",
    "billing_profile",
    "tenure_months",
    "seats",
    *LATENTES,
]


def gerar_populacao(n: int = 120_000, seed: Optional[int] = SEED) -> pd.DataFrame:
    """A população compartilhada: uma linha por cliente.

    Args:
        n: número de clientes.
        seed: semente. Mesma semente → mesmo DataFrame, byte a byte.

    Returns:
        DataFrame com `COLUNAS_POPULACAO`, indexado de 0 a n-1.
    """
    rng = np.random.default_rng(seed)

    customer_id = np.array([f"CLI_{i:06d}" for i in range(n)], dtype=object)

    # ── MRR ──────────────────────────────────────────────────────────
    mrr = np.clip(
        rng.lognormal(MRR_LOGNORMAL["mean"], MRR_LOGNORMAL["sigma"], size=n),
        MRR_LOGNORMAL["clip"][0],
        MRR_LOGNORMAL["clip"][1],
    ).round(2)

    # ── Perfil de cobrança ───────────────────────────────────────────
    billing_profile = rng.choice(PERFIS_COBRANCA, size=n, p=PERFIS_COBRANCA_PROBS)

    # ── Tempo de casa ────────────────────────────────────────────────
    n_novos = int(n * TENURE_FRACAO_NOVOS)
    tenure = np.concatenate([
        rng.exponential(scale=TENURE_EXP_SCALE, size=n_novos),
        rng.uniform(*TENURE_ANTIGOS_RANGE, size=n - n_novos),
    ])
    rng.shuffle(tenure)
    tenure_months = np.clip(tenure, *TENURE_CLIP).astype(int)

    # ── Assentos ─────────────────────────────────────────────────────
    # Ruído multiplicativo lognormal em torno do preço implícito: contratos
    # negociados, descontos por volume, planos com assento ilimitado.
    seats = np.clip(
        np.round(mrr / PRECO_POR_ASSENTO * rng.lognormal(0.0, 0.35, size=n)),
        *ASSENTOS_CLIP,
    ).astype(int)

    # ── Latentes ─────────────────────────────────────────────────────
    # Não são feature de treino nesta etapa. Estão aqui para a etapa do rótulo
    # latente e para o cálculo do teto de Bayes por construção.
    #
    # Não são independentes entre si nem da estrutura: quem tem pouco fit tende
    # a ter satisfação menor, e quem paga caro sente mais pressão de preço. É
    # essa estrutura que um rótulo latente vai poder explorar.
    fit_produto = rng.beta(5, 2, size=n)
    satisfacao = np.clip(
        0.55 * fit_produto + 0.45 * rng.beta(4, 2, size=n)
        + rng.normal(0, 0.06, size=n),
        0.0, 1.0,
    )
    mrr_percentil = pd.Series(mrr).rank(pct=True).to_numpy()
    pressao_preco = np.clip(
        0.40 * mrr_percentil + 0.60 * rng.beta(2, 4, size=n)
        + rng.normal(0, 0.06, size=n),
        0.0, 1.0,
    )
    # Saúde financeira depende do perfil de recebimento: CLT é o fluxo mais
    # previsível, freelancer o mais volátil. Isso é o que o Módulo 3 mede.
    piso = pd.Series(billing_profile).map(
        {"CLT": 0.55, "PJ": 0.45, "freelancer": 0.30}
    ).to_numpy()
    saude_financeira = np.clip(
        piso + rng.beta(2, 3, size=n) * 0.45 + rng.normal(0, 0.05, size=n),
        0.0, 1.0,
    )

    return pd.DataFrame({
        "customer_id": customer_id,
        "mrr": mrr,
        "billing_profile": billing_profile,
        "tenure_months": tenure_months,
        "seats": seats,
        "satisfacao": satisfacao.round(4),
        "fit_produto": fit_produto.round(4),
        "pressao_preco": pressao_preco.round(4),
        "saude_financeira": saude_financeira.round(4),
    })


def resumo(pop: pd.DataFrame) -> dict:
    """Números que o manifesto e o relatório citam."""
    return {
        "clientes": int(len(pop)),
        "mrr_mediana": float(pop["mrr"].median().round(2)),
        "mrr_p90": float(pop["mrr"].quantile(0.90).round(2)),
        "mrr_media": float(pop["mrr"].mean().round(2)),
        "tenure_mediana": float(pop["tenure_months"].median()),
        "seats_mediana": float(pop["seats"].median()),
        "perfis": {k: int(v) for k, v in pop["billing_profile"].value_counts().items()},
    }

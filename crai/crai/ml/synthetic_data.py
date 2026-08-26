"""
crai/ml/synthetic_data.py — Geradores de dataset sintético para treino dos módulos de ML.

Três geradores, um por modelo treinável do pacote:

1. generate_dataset()            → falhas de pagamento (Módulo 1, failure_classifier)
2. generate_behavioral_dataset() → uso do produto (Módulo 2, anomaly_detector)
3. generate_liquidity_series()   → saldo diário do cliente (Módulo 3, payday_inference)

Todos geram dados plausíveis para o mercado brasileiro de SaaS B2B:
- Tenure com distribuição realista (concentração em 0-12 meses, cauda longa até 60+)
- Sazonalidade de pagamento brasileira (5º dia útil, dias 10, 15, 20, 30)
- Códigos de falha de gateway com distribuição desbalanceada
- LTV estimado para cálculo do e-Profit
- Seed fixa (42) para reprodutibilidade
"""

from datetime import date
from typing import Optional, Union

import numpy as np
import pandas as pd

# Seed global para reprodutibilidade
SEED = 42

# Códigos de falha de gateway comuns no mercado brasileiro
GATEWAY_ERROR_CODES = [
    "insufficient_funds",
    "expired_card",
    "card_declined",
    "processing_error",
    "do_not_honor",
    "generic_decline",
]

# Probabilidades de cada código (distribuição desbalanceada — saldo insuficiente domina)
ERROR_CODE_PROBS = [0.35, 0.20, 0.15, 0.10, 0.12, 0.08]

# Bandeiras de cartão comuns no Brasil
CARD_BRANDS = ["visa", "mastercard", "elo", "amex", "hipercard"]
CARD_BRAND_PROBS = [0.40, 0.30, 0.15, 0.08, 0.07]

# ── Módulo 2 — features comportamentais do autoencoder ───────────────────
# Ordem canônica: é a ordem das colunas de entrada da rede.
BEHAVIORAL_FEATURES = [
    "tenure_days",
    "mrr_brl",
    "seats",
    "logins_7d",
    "logins_30d",
    "feature_adoption",
    "avg_session_min",
    "api_calls_7d",
    "days_since_last_login",
    "tickets_30d",
    "failed_pay_90d",
    "nps_last",
]

# ── Módulo 3 — perfis de recebimento do mercado brasileiro ───────────────
LIQUIDITY_PROFILES = ["CLT", "PJ", "freelancer"]
LIQUIDITY_PROFILE_PROBS = [0.50, 0.30, 0.20]


def generate_dataset(n_samples: int = 3000, seed: Optional[int] = SEED) -> pd.DataFrame:
    """
    Gera dataset sintético com distribuições plausíveis para SaaS B2B brasileiro.

    Args:
        n_samples: Número de amostras (default 3000)
        seed: Seed para reprodutibilidade (default 42)

    Returns:
        DataFrame com features e target 'recovered'
    """
    rng = np.random.default_rng(seed)

    # ── Tenure (meses de casa) ───────────────────────────────────────
    # Distribuição realista: ~60% em 0-12 meses, cauda longa até 60+
    # Mistura de exponencial (clientes novos) + uniforme (clientes antigos)
    tenure_new = rng.exponential(scale=6.0, size=int(n_samples * 0.65))
    tenure_old = rng.uniform(12, 72, size=int(n_samples * 0.35))
    tenure_all = np.concatenate([tenure_new, tenure_old])
    rng.shuffle(tenure_all)
    tenure_months = np.clip(tenure_all[:n_samples], 0, 72).astype(int)

    # ── Dia do mês da cobrança ───────────────────────────────────────
    # Sazonalidade brasileira: concentração nos dias 5, 10, 15, 30
    peak_days = [5, 10, 15, 20, 30]
    day_of_month = np.zeros(n_samples, dtype=int)
    for i in range(n_samples):
        if rng.random() < 0.6:  # 60% nos dias de pico
            day_of_month[i] = rng.choice(peak_days)
        else:
            day_of_month[i] = rng.integers(1, 29)

    # ── Valor da fatura (LogNormal) ──────────────────────────────────
    # SaaS B2B brasileiro: R$99 a R$5000, concentração em R$200-R$800
    invoice_amount = np.clip(
        rng.lognormal(mean=5.8, sigma=0.7, size=n_samples),
        49.90, 9999.90
    ).round(2)

    # ── Ticket médio (correlacionado com fatura, com ruído) ──────────
    avg_ticket = (invoice_amount * rng.uniform(0.85, 1.15, size=n_samples)).round(2)

    # ── Código de erro do gateway ────────────────────────────────────
    gateway_error_code = rng.choice(
        GATEWAY_ERROR_CODES, size=n_samples, p=ERROR_CODE_PROBS
    )

    # ── Bandeira do cartão ───────────────────────────────────────────
    card_brand = rng.choice(CARD_BRANDS, size=n_samples, p=CARD_BRAND_PROBS)

    # ── Histórico de pagamento (score 0-1) ───────────────────────────
    # Correlação positiva com tenure (clientes antigos tendem a ter histórico melhor)
    tenure_factor = np.clip(tenure_months / 60, 0, 1)
    payment_history_score = np.clip(
        rng.beta(5, 2, size=n_samples) * 0.7 + tenure_factor * 0.3,
        0, 1
    ).round(3)

    # ── Número de falhas nos últimos 90 dias ─────────────────────────
    failure_count_90d = rng.poisson(lam=1.5, size=n_samples)

    # ── Hora da tentativa de cobrança ────────────────────────────────
    hour_of_day = rng.choice(
        range(24), size=n_samples,
        p=_hour_distribution()
    )

    # ── Dia da semana (0=seg, 6=dom) ─────────────────────────────────
    day_of_week = rng.integers(0, 7, size=n_samples)

    # ── Número de tentativas anteriores ──────────────────────────────
    attempt_count = rng.choice([1, 2, 3, 4], size=n_samples, p=[0.45, 0.30, 0.15, 0.10])

    # ── LTV estimado ─────────────────────────────────────────────────
    # LTV = tenure * avg_ticket_mensal * fator_retenção
    retention_factor = np.clip(0.85 + tenure_factor * 0.10, 0.80, 0.98)
    ltv_estimated = (tenure_months * avg_ticket * retention_factor / 12).round(2)
    # Mínimo de LTV = valor da fatura (pelo menos 1 mês)
    ltv_estimated = np.maximum(ltv_estimated, invoice_amount).round(2)

    # ══ TARGET: recovered (0/1) ══════════════════════════════════════
    # Probabilidade de recuperação baseada em fatores realistas
    p_recovery = _calculate_recovery_probability(
        tenure_months=tenure_months,
        payment_history_score=payment_history_score,
        gateway_error_code=gateway_error_code,
        invoice_amount=invoice_amount,
        failure_count_90d=failure_count_90d,
        day_of_month=day_of_month,
        attempt_count=attempt_count,
        rng=rng,
    )
    recovered = (rng.random(n_samples) < p_recovery).astype(int)

    # ── Montar DataFrame ─────────────────────────────────────────────
    df = pd.DataFrame({
        "tenure_months": tenure_months,
        "day_of_month": day_of_month,
        "invoice_amount": invoice_amount,
        "avg_ticket": avg_ticket,
        "gateway_error_code": gateway_error_code,
        "card_brand": card_brand,
        "payment_history_score": payment_history_score,
        "failure_count_90d": failure_count_90d,
        "hour_of_day": hour_of_day,
        "day_of_week": day_of_week,
        "attempt_count": attempt_count,
        "ltv_estimated": ltv_estimated,
        "recovered": recovered,
    })

    return df


def _calculate_recovery_probability(
    tenure_months: np.ndarray,
    payment_history_score: np.ndarray,
    gateway_error_code: np.ndarray,
    invoice_amount: np.ndarray,
    failure_count_90d: np.ndarray,
    day_of_month: np.ndarray,
    attempt_count: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Calcula probabilidade de recuperação com base em fatores realistas.
    Modela as correlações que o XGBoost/RF devem aprender.
    """
    n = len(tenure_months)
    p = np.full(n, 0.5)

    # Tenure: correlação negativa forte com churn (clientes antigos recuperam mais)
    p += np.clip(tenure_months / 60, 0, 0.25)

    # Histórico de pagamento: bom histórico → maior recuperação
    p += (payment_history_score - 0.5) * 0.3

    # Código de erro: impacto diferente por tipo
    error_impact = {
        "insufficient_funds": -0.05,   # Recuperável com retry no payday
        "expired_card": -0.15,         # Precisa atualizar cartão
        "card_declined": -0.25,        # Bloqueio bancário — difícil
        "processing_error": 0.05,      # Erro técnico — retry geralmente resolve
        "do_not_honor": -0.30,         # Banco recusou — muito difícil
        "generic_decline": -0.20,      # Incerto
    }
    for code, impact in error_impact.items():
        mask = gateway_error_code == code
        p[mask] += impact

    # Valor da fatura: faturas muito altas → menor recuperação
    p -= np.clip((invoice_amount - 500) / 5000, 0, 0.15)

    # Falhas recentes: muitas falhas → menor recuperação
    p -= np.clip(failure_count_90d * 0.05, 0, 0.20)

    # Dia do mês: dias de pagamento (5, 10, 15) → melhor recuperação
    payday_mask = np.isin(day_of_month, [5, 6, 7, 10, 15, 20, 30])
    p[payday_mask] += 0.08

    # Tentativas anteriores: mais tentativas → menor chance
    p -= (attempt_count - 1) * 0.06

    # Ruído aleatório para evitar separação perfeita
    p += rng.normal(0, 0.05, size=n)

    return np.clip(p, 0.05, 0.95)


def _hour_distribution() -> list:
    """Distribuição de tentativas de cobrança por hora (concentração em horário comercial)."""
    probs = np.zeros(24)
    # Madrugada: baixo
    probs[0:6] = 0.5
    # Manhã: alto (processamento batch dos gateways)
    probs[6:12] = 3.0
    # Tarde: médio-alto
    probs[12:18] = 2.5
    # Noite: médio
    probs[18:24] = 1.5
    # Normalizar
    probs = probs / probs.sum()
    return probs.tolist()


# ══════════════════════════════════════════════════════════════════════════
# MÓDULO 2 — DATASET COMPORTAMENTAL (autoencoder de anomalias)
# ══════════════════════════════════════════════════════════════════════════

def generate_behavioral_dataset(
    n_samples: int = 5500,
    anomaly_rate: float = 0.09,
    seed: Optional[int] = SEED,
) -> pd.DataFrame:
    """
    Gera snapshots de uso do produto para duas populações de clientes SaaS B2B.

    - Saudáveis (~91%): uso estável, baixo atrito, alta adoção de features.
    - Anômalos (~9%): queda de uso, aumento de fricção, sinais de churn voluntário.

    A separação das populações só existe na geração, como ground-truth para
    calibrar o threshold e medir o autoencoder. O modelo treina apenas nos
    saudáveis e nunca vê o rótulo.

    Args:
        n_samples: Total de clientes gerados (saudáveis + anômalos)
        anomaly_rate: Fração de clientes anômalos
        seed: Seed para reprodutibilidade (default 42)

    Returns:
        DataFrame com BEHAVIORAL_FEATURES + 'customer_id' + 'is_anomalous'
    """
    rng = np.random.default_rng(seed)

    n_anomalous = int(round(n_samples * anomaly_rate))
    n_healthy = n_samples - n_anomalous

    df = pd.concat(
        [
            _behavioral_population(n_healthy, rng, anomalous=False),
            _behavioral_population(n_anomalous, rng, anomalous=True),
        ],
        ignore_index=True,
    )
    return df.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def _behavioral_population(n: int, rng: np.random.Generator, anomalous: bool) -> pd.DataFrame:
    """Uma das duas populações do dataset comportamental."""
    if n <= 0:
        return pd.DataFrame(columns=["customer_id", *BEHAVIORAL_FEATURES, "is_anomalous"])

    # Perfil de conta (tenure/MRR/seats) é idêntico nas duas populações:
    # o que distingue o cliente anômalo é o COMPORTAMENTO, não o tamanho.
    tenure_days = rng.gamma(shape=2.5, scale=180, size=n).clip(30, 2000).astype(int)
    mrr_brl = rng.lognormal(mean=8.5, sigma=0.7, size=n).clip(500, 50_000).round(2)
    seats = rng.poisson(lam=15, size=n).clip(1, 200)

    if anomalous:
        logins_30d = rng.normal(loc=seats * 5, scale=seats * 2, size=n).clip(0).astype(int)
        logins_7d = (logins_30d * rng.uniform(0.05, 0.15, size=n)).astype(int)
        feature_adoption = rng.beta(a=2, b=5, size=n).round(3)
        avg_session_min = rng.normal(loc=6, scale=3, size=n).clip(0.5).round(1)
        api_calls_7d = rng.lognormal(mean=4.5, sigma=1.0, size=n).clip(0).astype(int)
        days_since_last_login = rng.exponential(scale=9, size=n).clip(0, 30).astype(int)
        tickets_30d = rng.poisson(lam=4.5, size=n)
        failed_pay_90d = rng.binomial(n=3, p=0.35, size=n)
        nps_last = rng.normal(loc=5.5, scale=2.0, size=n).clip(0, 10).round(1)
        prefixo = "A"
    else:
        logins_30d = rng.normal(loc=seats * 18, scale=seats * 3, size=n).clip(1).astype(int)
        logins_7d = (logins_30d * rng.uniform(0.22, 0.30, size=n)).astype(int)
        feature_adoption = rng.beta(a=5, b=2, size=n).round(3)
        avg_session_min = rng.normal(loc=22, scale=6, size=n).clip(2).round(1)
        api_calls_7d = rng.lognormal(mean=7.0, sigma=0.8, size=n).clip(10).astype(int)
        days_since_last_login = rng.exponential(scale=1.5, size=n).clip(0, 30).astype(int)
        tickets_30d = rng.poisson(lam=1.2, size=n)
        failed_pay_90d = rng.binomial(n=3, p=0.05, size=n)
        nps_last = rng.normal(loc=8.2, scale=1.3, size=n).clip(0, 10).round(1)
        prefixo = "C"

    return pd.DataFrame({
        "customer_id": [f"{prefixo}{i:06d}" for i in range(1, n + 1)],
        "tenure_days": tenure_days,
        "mrr_brl": mrr_brl,
        "seats": seats,
        "logins_7d": logins_7d,
        "logins_30d": logins_30d,
        "feature_adoption": feature_adoption,
        "avg_session_min": avg_session_min,
        "api_calls_7d": api_calls_7d,
        "days_since_last_login": days_since_last_login,
        "tickets_30d": tickets_30d,
        "failed_pay_90d": failed_pay_90d,
        "nps_last": nps_last,
        "is_anomalous": int(anomalous),
    })


# ══════════════════════════════════════════════════════════════════════════
# MÓDULO 3 — SÉRIES DE LIQUIDEZ (LSTM + Prophet)
# ══════════════════════════════════════════════════════════════════════════

def generate_liquidity_series(
    n_customers: int = 600,
    n_days: int = 180,
    seed: Optional[int] = SEED,
    end_date: Optional[Union[str, pd.Timestamp]] = None,
) -> pd.DataFrame:
    """
    Gera o saldo diário de clientes SaaS B2B em 3 perfis de recebimento.

    - CLT        (~50%): salário no 5º dia útil + adiantamento ~dia 20
    - PJ         (~30%): notas pagas em torno dos dias 10, 15 e 30
    - freelancer (~20%): entradas irregulares (projetos), alta variância

    A série termina em `end_date` (default: hoje) para que o prior sazonal do
    Prophet fique alinhado ao presente — a inferência prevê os 14 dias
    seguintes ao dia de hoje. Em produção essas séries viriam do histórico de
    transações do gateway / open finance.

    Args:
        n_customers: Número de clientes simulados
        n_days: Dias de histórico por cliente
        seed: Seed para reprodutibilidade (default 42)
        end_date: Último dia da série (default: hoje)

    Returns:
        DataFrame longo com uma linha por (cliente, dia):
        customer_id, profile, date, day_of_month, weekday, bday_idx,
        balance_norm, has_liquidity
    """
    rng = np.random.default_rng(seed)

    fim = pd.Timestamp(end_date) if end_date is not None else pd.Timestamp(date.today())
    dates = pd.date_range(end=fim, periods=n_days, freq="D")
    bday_idx = _business_day_index(dates)

    profiles = rng.choice(LIQUIDITY_PROFILES, size=n_customers, p=LIQUIDITY_PROFILE_PROBS)
    series = [
        _liquidity_series_customer(f"C{i:05d}", profiles[i], dates, bday_idx, rng)
        for i in range(n_customers)
    ]
    return pd.concat(series, ignore_index=True)


def _business_day_index(dates: pd.DatetimeIndex) -> np.ndarray:
    """Índice do dia útil dentro do mês (1 = primeiro dia útil, 0 = fim de semana)."""
    is_bday = pd.Series(dates.dayofweek < 5, index=range(len(dates)))
    acumulado = is_bday.groupby([dates.year, dates.month]).cumsum()
    return acumulado.where(is_bday, 0).to_numpy(dtype=int)


def _liquidity_series_customer(
    customer_id: str,
    profile: str,
    dates: pd.DatetimeIndex,
    bday_idx: np.ndarray,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Série diária de saldo de um cliente, em múltiplos da mensalidade."""
    if profile == "CLT":
        entradas = _entradas_clt(dates, bday_idx, rng)
        gasto_diario = rng.uniform(0.08, 0.14)
    elif profile == "PJ":
        entradas = _entradas_pj(dates, rng)
        gasto_diario = rng.uniform(0.10, 0.18)
    else:
        entradas = _entradas_freelancer(dates, rng)
        gasto_diario = rng.uniform(0.10, 0.20)

    saldo = np.zeros(len(dates))
    atual = rng.uniform(0.2, 1.5)  # saldo inicial em múltiplos da mensalidade
    for i in range(len(dates)):
        atual = max(0.0, atual + entradas[i] - gasto_diario * rng.uniform(0.5, 1.5))
        saldo[i] = atual

    return pd.DataFrame({
        "customer_id": customer_id,
        "profile": profile,
        "date": dates,
        "day_of_month": dates.day,
        "weekday": dates.dayofweek,
        "bday_idx": bday_idx,
        "balance_norm": np.round(saldo, 4),
        "has_liquidity": (saldo >= 1.0).astype(int),
    })


def _entradas_clt(dates: pd.DatetimeIndex, bday_idx: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Salário no 5º dia útil + adiantamento (~40%) próximo do dia 20."""
    salario = rng.uniform(2.5, 5.0)  # em múltiplos da mensalidade
    entradas = np.zeros(len(dates))
    entradas[bday_idx == 5] = salario * 0.6
    dia_adiantamento = int(np.clip(rng.normal(20, 1), 18, 22))
    entradas[dates.day == dia_adiantamento] += salario * 0.4
    return entradas


def _entradas_pj(dates: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    """Notas pagas em torno dos dias 10, 15 e 30, com atraso de 0-3 dias."""
    receita = rng.uniform(2.0, 6.0)
    entradas = np.zeros(len(dates))
    for ancora, peso in [(10, 0.4), (15, 0.3), (30, 0.3)]:
        atraso = int(rng.integers(0, 4))
        dia = min(ancora + atraso, 28) if ancora == 30 else ancora + atraso
        if rng.uniform() > 0.15:  # 15% de inadimplência do cliente do cliente
            entradas[dates.day == dia] += receita * peso * rng.uniform(0.7, 1.3)
    return entradas


def _entradas_freelancer(dates: pd.DatetimeIndex, rng: np.random.Generator) -> np.ndarray:
    """2-5 pagamentos por mês em dias aleatórios, valores erráticos."""
    entradas = np.zeros(len(dates))
    periodos = dates.to_period("M")
    for mes in pd.unique(periodos):
        dias_do_mes = np.where(periodos == mes)[0]
        if len(dias_do_mes) == 0:
            continue
        n_pagamentos = int(rng.integers(2, 6))
        idx = rng.choice(dias_do_mes, size=min(n_pagamentos, len(dias_do_mes)), replace=False)
        entradas[idx] = rng.exponential(scale=1.2, size=len(idx))
    return entradas


if __name__ == "__main__":
    # Gerar e inspecionar dataset
    df = generate_dataset(3000)
    print(f"Dataset gerado: {df.shape}")
    print(f"\nDistribuição do target:")
    print(df["recovered"].value_counts(normalize=True).round(3))
    print(f"\nTenure (meses):")
    print(df["tenure_months"].describe().round(1))
    print(f"\nCódigos de erro:")
    print(df["gateway_error_code"].value_counts(normalize=True).round(3))
    print(f"\nLTV estimado:")
    print(df["ltv_estimated"].describe().round(1))
    print(f"\nTaxa de recuperação por código de erro:")
    print(df.groupby("gateway_error_code")["recovered"].mean().round(3))
    print(f"\nTaxa de recuperação por faixa de tenure:")
    bins = [0, 3, 6, 12, 24, 72]
    labels = ["0-3m", "3-6m", "6-12m", "12-24m", "24+m"]
    df["tenure_bin"] = pd.cut(df["tenure_months"], bins=bins, labels=labels)
    print(df.groupby("tenure_bin")["recovered"].mean().round(3))

    # ── Módulo 2: dataset comportamental ─────────────────────────────────
    beh = generate_behavioral_dataset(5500)
    print(f"\nDataset comportamental gerado: {beh.shape}")
    print(f"  saudaveis: {(beh.is_anomalous == 0).sum()} | anomalos: {(beh.is_anomalous == 1).sum()}")
    print("\nMedia por populacao (uso do produto):")
    print(beh.groupby("is_anomalous")[
        ["logins_7d", "feature_adoption", "days_since_last_login", "tickets_30d", "nps_last"]
    ].mean().round(2))

    # ── Módulo 3: séries de liquidez ─────────────────────────────────────
    liq = generate_liquidity_series(200, 180)
    print(f"\nSeries de liquidez geradas: {liq.shape}")
    for p in LIQUIDITY_PROFILES:
        sub = liq[liq.profile == p]
        print(f"  {p:11s}: {sub.customer_id.nunique():4d} clientes | "
              f"liquidez media {sub.has_liquidity.mean():.2%}")

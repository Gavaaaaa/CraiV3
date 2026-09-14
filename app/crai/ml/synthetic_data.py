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

Fonte dos parâmetros (`fonte=`, em todos os geradores):

- "sintetico" (default): os valores inventados de sempre, agora reunidos em
  `calibracao.PARAMETROS_DISTRIBUICAO`. Saída byte-idêntica à de antes —
  `tests/test_synthetic_data.py` trava o hash.
- "sintetico_calibrado": os mesmos geradores com os parâmetros MEDIDOS em
  doadores reais (`models/calibracao.json`), mais o ruído anti-circularidade
  descrito em cada gerador. Ver `calibracao.py` e `docs/DATA_CARD.md`.

Há um quarto gerador, `generate_voluntary_dataset()`, para o risk_scorer do
churn voluntário — o rótulo dele são as regras fixas do próprio scorer, com
ruído, e é só isso que ele pode ser enquanto não existe sinal real de
cancelamento (ver `churn_voluntary/README_treino.md`).
"""

import hashlib
from datetime import date
from typing import Optional, Union

import numpy as np

from .ltv import ltv_estimado
from . import calibracao as _calibracao
import pandas as pd

# Seed global para reprodutibilidade
SEED = 42


def _parametros(fonte: str, parametros: Optional[dict], bloco: str) -> dict:
    """O bloco de parâmetros do gerador (`classifier`, `behavioral`...).

    `parametros` explícito vence `fonte` — é o caminho dos testes, que
    injetam um dicionário sem depender do arquivo em disco.
    """
    if parametros is not None:
        return parametros[bloco] if bloco in parametros else parametros
    return _calibracao.parametros(fonte)[bloco]


def seed_por_cliente(customer_id: str) -> int:
    """Seed determinística por cliente, estável entre execuções.

    Vários pontos do sistema simulam o perfil de um cliente a partir do seu id
    (tenure, saldo, uso do produto). Isso precisa ser determinístico: o mesmo
    cliente tem que produzir sempre o mesmo perfil, senão a demo dá números
    diferentes a cada execução e a banca não consegue reproduzir o resultado.

    O `hash()` embutido do Python **não** serve: para strings ele é randomizado
    por processo (via PYTHONHASHSEED), então o mesmo customer_id gera seeds
    diferentes a cada execução do interpretador.

    O md5 aqui é usado apenas como digest estável — não tem função de segurança.
    """
    return int(hashlib.md5(str(customer_id).encode()).hexdigest(), 16) % (2**32)

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


def generate_dataset(
    n_samples: int = 3000,
    seed: Optional[int] = SEED,
    fonte: str = "sintetico",
    parametros: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Gera dataset sintético com distribuições plausíveis para SaaS B2B brasileiro.

    Args:
        n_samples: Número de amostras (default 3000)
        seed: Seed para reprodutibilidade (default 42)
        fonte: "sintetico" (default, inalterado) ou "sintetico_calibrado"
        parametros: bloco `classifier` de parâmetros, se quiser injetar um

    Returns:
        DataFrame com features e target 'recovered'
    """
    P = _parametros(fonte, parametros, "classifier")
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
    # Default: sazonalidade desenhada à mão (picos nos dias 5, 10, 15, 20, 30).
    # Calibrado: histograma empírico do dia do mês nas 300 transações reais
    # da Olist (Fonte A), lido de calibracao.json.
    if P["day_of_month_hist"] is None:
        peak_days = list(P["peak_days"])
        day_of_month = np.zeros(n_samples, dtype=int)
        for i in range(n_samples):
            if rng.random() < P["p_peak_day"]:  # 60% nos dias de pico
                day_of_month[i] = rng.choice(peak_days)
            else:
                day_of_month[i] = rng.integers(1, 29)
    else:
        hist = np.asarray(P["day_of_month_hist"], dtype=float)
        day_of_month = rng.choice(np.arange(1, len(hist) + 1), size=n_samples,
                                  p=hist / hist.sum())

    # ── Valor da fatura (LogNormal) ──────────────────────────────────
    # Default: mean=5.8, sigma=0.7 (palpite: R$200-R$800). Calibrado: MLE
    # sobre `payment_value` das 300 reais (mu=4.58, sigma=0.89 — mediana
    # R$ 100, ticket de e-commerce; ver DATA_CARD 2.3 sobre o custo disso).
    invoice_amount = np.clip(
        rng.lognormal(mean=P["invoice_lognormal"]["mean"],
                      sigma=P["invoice_lognormal"]["sigma"], size=n_samples),
        P["invoice_clip"][0], P["invoice_clip"][1]
    ).round(2)

    # ── Ticket médio (correlacionado com fatura, com ruído) ──────────
    avg_ticket = (invoice_amount * rng.uniform(0.85, 1.15, size=n_samples)).round(2)

    # ── Código de erro do gateway ────────────────────────────────────
    # Calibrado: a fatia de `insufficient_funds` é escalada pela razão entre a
    # inadimplência PF corrente e a média histórica (BACEN SGS 21084, Fonte B),
    # e os demais códigos renormalizados — hipótese declarada no DATA_CARD 3.
    gateway_error_code = rng.choice(
        GATEWAY_ERROR_CODES, size=n_samples, p=list(P["error_code_probs"])
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
    # Default: curva desenhada à mão. Calibrado: histograma empírico da hora
    # de `order_purchase_timestamp` nas 300 reais (proxy: hora de compra em
    # e-commerce, não hora de cobrança de assinatura — declarado).
    if P["hour_hist"] is None:
        p_hora = _hour_distribution()
    else:
        p_hora = (np.asarray(P["hour_hist"], dtype=float)
                  / np.sum(P["hour_hist"])).tolist()
    hour_of_day = rng.choice(range(24), size=n_samples, p=p_hora)

    # ── Dia da semana (0=seg, 6=dom) ─────────────────────────────────
    if P["day_of_week_hist"] is None:
        day_of_week = rng.integers(0, 7, size=n_samples)
    else:
        p_dow = np.asarray(P["day_of_week_hist"], dtype=float)
        day_of_week = rng.choice(7, size=n_samples, p=p_dow / p_dow.sum())

    # ── Número de tentativas anteriores ──────────────────────────────
    attempt_count = rng.choice([1, 2, 3, 4], size=n_samples, p=[0.45, 0.30, 0.15, 0.10])

    # ── LTV estimado ─────────────────────────────────────────────────
    # A fórmula mora em `ml/ltv.py` desde o Sprint 7 do churn involuntário. Ela
    # estava escrita aqui e, de novo, no perfil sintético do pipeline — com
    # outro fator de retenção. Duas cópias da mesma regra divergem no dia em que
    # uma for corrigida, e esta divergiria em silêncio: o LTV não é feature de
    # treino, é o multiplicador do e-Profit. O fator continua sendo parâmetro,
    # e aqui ele é modulado pelo tenure, como sempre foi.
    retention_factor = np.clip(0.85 + tenure_factor * 0.10, 0.80, 0.98)
    ltv_estimated = ltv_estimado(tenure_months, avg_ticket, invoice_amount,
                                 retention_factor)

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
        ruido_sd=P["ruido_rotulo_sd"],
    )
    recovered = (rng.random(n_samples) < p_recovery).astype(int)

    # ── Anti-circularidade: exceções à regra ─────────────────────────
    # O rótulo já não é função determinística das features: `p_recovery` leva
    # N(0, ruido_sd) e o rótulo é um sorteio Bernoulli(p) — com p entre 0,05
    # e 0,95, a mesma linha pode cair dos dois lados, e é isso que segura a
    # AUC do holdout em ~0,70 em vez de ~0,99. Em cima disso, no modo
    # calibrado, uma fração `p_excecao_rotulo` das linhas tem o rótulo
    # sorteado ao acaso, ignorando a regra: são os casos que nenhum modelo
    # causal escrito à mão prevê (cliente que paga apesar de tudo, cliente que
    # some sem motivo). A magnitude (2%) é deliberadamente pequena: o objetivo
    # é impedir que o modelo decore a regra, não afogar o sinal — cada ponto
    # de exceção custa AUC, e o gate G3 exige AUC >= 0,70.
    #
    # Esse 0,70 é o gate G3 de sprint (`docs/planos/sprints.md`). O gate
    # executável mudou em 14/09/2026: `tests/test_metricas_declaradas.py` usa
    # teto 0,92 como gate anti-vazamento, piso 0,60 como sanidade e o critério
    # operacional (zero recuperáveis perdidos, recall > 0,90 no limiar em uso)
    # como gate do produto — 0,70 estava dentro do erro padrão da medida. O
    # ruído acima não mudou e continua sendo a razão de o teto existir: é ele
    # que mantém a AUC longe de ~0,99. Decisão em `docs/LIMITACOES.md`.
    if P["p_excecao_rotulo"] > 0:
        excecao = rng.random(n_samples) < P["p_excecao_rotulo"]
        recovered[excecao] = rng.integers(0, 2, size=int(excecao.sum()))

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
    ruido_sd: float = 0.05,
) -> np.ndarray:
    """
    Calcula probabilidade de recuperação com base em fatores realistas.
    Modela as correlações que o XGBoost/RF devem aprender.

    Cada coeficiente e sua hipótese de negócio estão em `docs/DATA_CARD.md`,
    seção 7. `ruido_sd` é o desvio do ruído gaussiano somado ao final — o
    default 0,05 é o que sempre existiu; o modo calibrado passa o valor do
    `calibracao.json`.
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
    p += rng.normal(0, ruido_sd, size=n)

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
    fonte: str = "sintetico",
    parametros: Optional[dict] = None,
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
        fonte: "sintetico" (default, inalterado) ou "sintetico_calibrado"
        parametros: bloco `behavioral` de parâmetros, se quiser injetar um

    Returns:
        DataFrame com BEHAVIORAL_FEATURES + 'customer_id' + 'is_anomalous'
    """
    P = _parametros(fonte, parametros, "behavioral")
    rng = np.random.default_rng(seed)

    n_anomalous = int(round(n_samples * anomaly_rate))
    n_healthy = n_samples - n_anomalous

    saudaveis = _behavioral_population(n_healthy, rng, anomalous=False, parametros=P)
    anomalos = _behavioral_population(n_anomalous, rng, anomalous=True, parametros=P)

    # ── Anti-circularidade: exceções ao rótulo ───────────────────────
    # No default o rótulo `is_anomalous` É a população de origem, e as duas
    # populações quase não se sobrepõem — daí o ROC-AUC 0,995 que o README
    # marca como bandeira vermelha. No modo calibrado uma fração dos anômalos
    # (`p_excecao_anomalo`) esconde o sinal: metade das features
    # comportamentais vem da distribuição SAUDÁVEL (churn silencioso — o
    # cliente que cancela sem dar sinal). E uma fração dos saudáveis
    # (`p_excecao_saudavel`) mostra degradação passageira em 2 features
    # (férias, projeto parado), sem ser anômalo. Com isso o rótulo deixa de
    # ser dedutível das features; o autoencoder tem que aprender a geometria
    # do normal, não uma fronteira que o gerador desenhou. Magnitudes (15% e
    # 5%) no calibracao.json, com a justificativa lá.
    if n_anomalous > 0 and P["p_excecao_anomalo"] > 0:
        sombra = _behavioral_population(n_anomalous, rng, anomalous=False, parametros=P)
        anomalos = _aplicar_excecoes(anomalos, sombra, P["p_excecao_anomalo"], 3, rng)
    if n_healthy > 0 and P["p_excecao_saudavel"] > 0:
        sombra = _behavioral_population(n_healthy, rng, anomalous=True, parametros=P)
        saudaveis = _aplicar_excecoes(saudaveis, sombra, P["p_excecao_saudavel"], 2, rng)

    df = pd.concat([saudaveis, anomalos], ignore_index=True)
    return df.sample(frac=1.0, random_state=seed).reset_index(drop=True)


# As features que uma "exceção" pode trocar pela outra população. Perfil de
# conta (tenure/mrr/seats) fica de fora: ele já é igual nas duas populações.
_FEATURES_EXCECAO = [
    "logins_7d", "logins_30d", "feature_adoption", "avg_session_min",
    "api_calls_7d", "days_since_last_login", "tickets_30d", "nps_last",
]


def _aplicar_excecoes(df: pd.DataFrame, sombra: pd.DataFrame, p: float,
                      k: int, rng: np.random.Generator) -> pd.DataFrame:
    """Em uma fração `p` das linhas, troca `k` features pelas da `sombra`."""
    df = df.copy()
    n = len(df)
    alvo = np.where(rng.random(n) < p)[0]
    for i in alvo:
        cols = rng.choice(_FEATURES_EXCECAO, size=k, replace=False)
        for col in cols:
            df.at[i, col] = sombra.at[i, col]
    return df


def _behavioral_population(n: int, rng: np.random.Generator, anomalous: bool,
                           parametros: Optional[dict] = None) -> pd.DataFrame:
    """Uma das duas populações do dataset comportamental."""
    if n <= 0:
        return pd.DataFrame(columns=["customer_id", *BEHAVIORAL_FEATURES, "is_anomalous"])

    P = (_calibracao.PARAMETROS_DISTRIBUICAO["behavioral"] if parametros is None
         else parametros)
    Q = P["anomalo"] if anomalous else P["saudavel"]

    # Perfil de conta (tenure/MRR/seats) é idêntico nas duas populações:
    # o que distingue o cliente anômalo é o COMPORTAMENTO, não o tamanho.
    tenure_days = rng.gamma(shape=2.5, scale=180, size=n).clip(30, 2000).astype(int)
    mrr_brl = rng.lognormal(mean=8.5, sigma=0.7, size=n).clip(500, 50_000).round(2)
    seats = rng.poisson(lam=15, size=n).clip(1, 200)

    # As quatro features com doador real (Fonte E, calibrado) leem de Q:
    # days_since_last_login, avg_session_min, tickets_30d, nps_last.
    # As demais não têm doador e continuam com os literais de sempre.
    if anomalous:
        logins_30d = rng.normal(loc=seats * 5, scale=seats * 2, size=n).clip(0).astype(int)
        logins_7d = (logins_30d * rng.uniform(0.05, 0.15, size=n)).astype(int)
        feature_adoption = rng.beta(a=2, b=5, size=n).round(3)
        avg_session_min = rng.normal(loc=Q["avg_session"]["loc"], scale=Q["avg_session"]["scale"],
                                     size=n).clip(0.5).round(1)
        api_calls_7d = rng.lognormal(mean=4.5, sigma=1.0, size=n).clip(0).astype(int)
        days_since_last_login = rng.exponential(scale=Q["days_since_last_login_scale"],
                                                size=n).clip(0, 30).astype(int)
        tickets_30d = rng.poisson(lam=Q["tickets_lam"], size=n)
        failed_pay_90d = rng.binomial(n=3, p=0.35, size=n)
        nps_last = rng.normal(loc=Q["nps"]["loc"], scale=Q["nps"]["scale"],
                              size=n).clip(0, 10).round(1)
        prefixo = "A"
    else:
        logins_30d = rng.normal(loc=seats * 18, scale=seats * 3, size=n).clip(1).astype(int)
        logins_7d = (logins_30d * rng.uniform(0.22, 0.30, size=n)).astype(int)
        feature_adoption = rng.beta(a=5, b=2, size=n).round(3)
        avg_session_min = rng.normal(loc=Q["avg_session"]["loc"], scale=Q["avg_session"]["scale"],
                                     size=n).clip(2).round(1)
        api_calls_7d = rng.lognormal(mean=7.0, sigma=0.8, size=n).clip(10).astype(int)
        days_since_last_login = rng.exponential(scale=Q["days_since_last_login_scale"],
                                                size=n).clip(0, 30).astype(int)
        tickets_30d = rng.poisson(lam=Q["tickets_lam"], size=n)
        failed_pay_90d = rng.binomial(n=3, p=0.05, size=n)
        nps_last = rng.normal(loc=Q["nps"]["loc"], scale=Q["nps"]["scale"],
                              size=n).clip(0, 10).round(1)
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
    fonte: str = "sintetico",
    parametros: Optional[dict] = None,
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

    NÃO existe doador público para saldo diário de cliente — nenhuma das
    colunas deste gerador é calibrada em dado real, e o calibracao.json diz
    isso. O que "sintetico_calibrado" muda aqui é só o anti-circularidade:
    choques (salário atrasado, gasto imprevisto) que o default não tem.

    Args:
        n_customers: Número de clientes simulados
        n_days: Dias de histórico por cliente
        seed: Seed para reprodutibilidade (default 42)
        end_date: Último dia da série (default: hoje)
        fonte: "sintetico" (default, inalterado) ou "sintetico_calibrado"
        parametros: bloco `liquidity` de parâmetros, se quiser injetar um

    Returns:
        DataFrame longo com uma linha por (cliente, dia):
        customer_id, profile, date, day_of_month, weekday, bday_idx,
        balance_norm, has_liquidity
    """
    P = _parametros(fonte, parametros, "liquidity")
    rng = np.random.default_rng(seed)

    fim = pd.Timestamp(end_date) if end_date is not None else pd.Timestamp(date.today())
    dates = pd.date_range(end=fim, periods=n_days, freq="D")
    bday_idx = _business_day_index(dates)

    profiles = rng.choice(LIQUIDITY_PROFILES, size=n_customers, p=LIQUIDITY_PROFILE_PROBS)
    series = [
        _liquidity_series_customer(f"C{i:05d}", profiles[i], dates, bday_idx, rng, P)
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
    parametros: Optional[dict] = None,
) -> pd.DataFrame:
    """Série diária de saldo de um cliente, em múltiplos da mensalidade."""
    P = (_calibracao.PARAMETROS_DISTRIBUICAO["liquidity"] if parametros is None
         else parametros)
    if profile == "CLT":
        entradas = _entradas_clt(dates, bday_idx, rng)
        gasto_diario = rng.uniform(0.08, 0.14)
    elif profile == "PJ":
        entradas = _entradas_pj(dates, rng)
        gasto_diario = rng.uniform(0.10, 0.18)
    else:
        entradas = _entradas_freelancer(dates, rng)
        gasto_diario = rng.uniform(0.10, 0.20)

    # ── Anti-circularidade (só no modo calibrado) ────────────────────
    # `has_liquidity` é função determinística do saldo, e o saldo default é
    # função quase determinística do calendário: a LSTM poderia aprender o
    # calendário e nunca o cliente. Dois choques que o mundo real tem e a
    # série default não: salário que atrasa alguns dias (p por entrada) e
    # gasto imprevisto que zera a folga (p por dia). Magnitudes no
    # calibracao.json. No default os dois são zero e nenhum sorteio extra
    # acontece — a série é a mesma de sempre.
    if P["p_atraso_salario"] > 0:
        entradas = _atrasar_entradas(entradas, rng, P["p_atraso_salario"],
                                     P["atraso_salario_max_dias"])

    saldo = np.zeros(len(dates))
    atual = rng.uniform(0.2, 1.5)  # saldo inicial em múltiplos da mensalidade
    imprevisto = P["p_gasto_imprevisto"] > 0
    for i in range(len(dates)):
        atual = max(0.0, atual + entradas[i] - gasto_diario * rng.uniform(0.5, 1.5))
        if imprevisto and rng.uniform() < P["p_gasto_imprevisto"]:
            atual = max(0.0, atual - rng.uniform(P["gasto_imprevisto"]["min"],
                                                 P["gasto_imprevisto"]["max"]))
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


def _atrasar_entradas(entradas: np.ndarray, rng: np.random.Generator,
                      p: float, max_dias: int) -> np.ndarray:
    """Cada entrada positiva tem probabilidade `p` de chegar 1..max_dias depois."""
    saida = np.zeros_like(entradas)
    for i in np.where(entradas > 0)[0]:
        destino = i
        if rng.uniform() < p:
            destino = min(i + int(rng.integers(1, max_dias + 1)), len(entradas) - 1)
        saida[destino] += entradas[i]
    return saida


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


# ══════════════════════════════════════════════════════════════════════════
# RISK SCORER VOLUNTÁRIO — DATASET DE RISCO (novo)
# ══════════════════════════════════════════════════════════════════════════

# Mesma ordem de `churn_voluntary.risk_scorer.FEATURES_DE_RISCO`. Repetida
# aqui (e conferida por teste) para que este módulo não importe o scorer no
# import — o scorer é importado sob demanda, só para computar o rótulo.
VOLUNTARY_FEATURES = [
    "days_since_last",
    "features_used_30d",
    "mrr",
    "evento_cancelamento",
    "evento_downgrade",
    "evento_sessao",
]

_EVENTOS_VOLUNTARIOS = ["Session Started", "Downgrade Clicked", "Cancellation Page Viewed"]


def generate_voluntary_dataset(
    n_samples: int = 2000,
    seed: Optional[int] = SEED,
    fonte: str = "sintetico",
    parametros: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Gera eventos de risco de churn voluntário com o rótulo das REGRAS FIXAS.

    O que este gerador é, sem rodeios: não existe sinal real de cancelamento
    em lugar nenhum do sistema (ver `churn_voluntary/README_treino.md`), então
    o único rótulo defensável hoje é o que o próprio `risk_scorer` produz
    pelas regras — mais ruído, para que um modelo treinado aprenda a
    tendência e não decore a fórmula. Um modelo treinado aqui NÃO sabe mais
    que as regras; ele prova que o encaixe plugável funciona de ponta a ponta.

    - `days_since_last`: calibrado em `DaySinceLastOrder` (Fonte E)
    - `features_used_30d`: proxy fraco de `OrderCount` (Fonte E)
    - `mrr`: 100% sintético, sem doador
    - one-hots de evento: mix declarado, sem doador
    - `churn`: Bernoulli(regra + N(0, sd)), com `p_excecao_rotulo` de linhas
      sorteadas ao acaso

    Args:
        n_samples: Número de eventos
        seed: Seed para reprodutibilidade
        fonte: "sintetico" ou "sintetico_calibrado"
        parametros: bloco `voluntary` de parâmetros, se quiser injetar um

    Returns:
        DataFrame com VOLUNTARY_FEATURES + 'event' + 'risk_regra' + 'churn'
    """
    from ..churn_voluntary.risk_scorer import _risco_por_regras

    P = _parametros(fonte, parametros, "voluntary")
    rng = np.random.default_rng(seed)

    days_since_last = _amostrar_inteiro(P["days_since_last"], n_samples, rng)
    features_used_30d = _amostrar_inteiro(P["features_used_30d"], n_samples, rng)
    mrr = np.clip(
        rng.lognormal(mean=P["mrr_lognormal"]["mean"], sigma=P["mrr_lognormal"]["sigma"],
                      size=n_samples),
        P["mrr_lognormal"]["clip"][0], P["mrr_lognormal"]["clip"][1],
    ).round(2)
    mix = P["mix_eventos"]
    eventos = rng.choice(_EVENTOS_VOLUNTARIOS, size=n_samples,
                         p=[mix[e] for e in _EVENTOS_VOLUNTARIOS])

    # O rótulo vem das regras REAIS do scorer, linha a linha — não de uma
    # cópia. Se as regras mudarem, o dataset muda junto.
    risk_regra = np.array([
        _risco_por_regras(str(e), {"days_since_last": int(d), "features_used_30d": int(f)})
        for e, d, f in zip(eventos, days_since_last, features_used_30d)
    ], dtype=float)

    # ── Anti-circularidade ───────────────────────────────────────────
    # Regra + ruído gaussiano, corte em [0,02; 0,98] e sorteio Bernoulli: a
    # mesma combinação de features cai dos dois lados. Em cima disso,
    # `p_excecao_rotulo` das linhas recebe rótulo ao acaso. Magnitudes e
    # justificativa no calibracao.json.
    p = np.clip(risk_regra + rng.normal(0, P["ruido_rotulo_sd"], size=n_samples), 0.02, 0.98)
    churn = (rng.random(n_samples) < p).astype(int)
    if P["p_excecao_rotulo"] > 0:
        excecao = rng.random(n_samples) < P["p_excecao_rotulo"]
        churn[excecao] = rng.integers(0, 2, size=int(excecao.sum()))

    return pd.DataFrame({
        "days_since_last": days_since_last.astype(float),
        "features_used_30d": features_used_30d.astype(float),
        "mrr": mrr,
        "evento_cancelamento": (eventos == "Cancellation Page Viewed").astype(float),
        "evento_downgrade": (eventos == "Downgrade Clicked").astype(float),
        "evento_sessao": (eventos == "Session Started").astype(float),
        "event": eventos,
        "risk_regra": risk_regra,
        "churn": churn,
    })


def _amostrar_inteiro(spec: dict, n: int, rng: np.random.Generator) -> np.ndarray:
    """Inteiros de uma spec: histograma empírico (calibrado) ou regra procedural."""
    if spec.get("hist") is not None:
        valores = np.asarray(spec["hist"]["valores"], dtype=int)
        pesos = np.asarray(spec["hist"]["pesos"], dtype=float)
        return rng.choice(valores, size=n, p=pesos / pesos.sum())
    if spec["tipo"] == "uniforme_int":
        return rng.integers(spec["min"], spec["max"] + 1, size=n)
    if spec["tipo"] == "poisson":
        return rng.poisson(lam=spec["lam"], size=n)
    raise ValueError(f"spec de inteiro desconhecida: {spec}")


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

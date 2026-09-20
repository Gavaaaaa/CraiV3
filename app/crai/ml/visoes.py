"""
crai/ml/visoes.py — As quatro visões de treino derivadas da população.

Cada função aqui recebe a população de `populacao.gerar_populacao` e devolve a
tabela de treino de um módulo, **no grão daquele módulo**:

    visao_classificador(pop, ...)   → uma linha por COBRANÇA falhada
    visao_comportamental(pop, ...)  → uma linha por CLIENTE
    visao_liquidez(pop, ...)        → uma linha por CLIENTE-DIA
    visao_voluntario(pop, ...)      → uma linha por EVENTO de SDK

Todas carregam `customer_id`, e todo atributo que é do cliente (MRR, perfil de
cobrança, tempo de casa, assentos) é **lido da população**, nunca sorteado de
novo. Um cliente que aparece em três tabelas tem o mesmo MRR nas três.

Este módulo é ADITIVO: `synthetic_data.py` continua como está, com as mesmas
assinaturas e os mesmos hashes, e os testes que travam a saída v1 continuam
verdes. Quem quiser a base v1 chama `synthetic_data`; quem quiser a v2 chama
daqui. As funções auxiliares que descrevem o mundo (curva de hora do dia,
série de saldo por perfil, populações comportamentais) são **importadas** de
`synthetic_data`, não copiadas — o DNA estatístico continua sendo um só.

O que muda em relação à base v1, e por quê
───────────────────────────────────────────

1. **`invoice_amount` deriva do MRR** (antes: lognormal calibrada no ticket do
   Olist, que é varejo). Uma fatura é uma cobrança da assinatura:
   `invoice_amount = mrr × fator_do_ciclo`, com o fator cobrindo mensal, anual
   e proporcional. Isto é o que mata a razão de 50,2× medida na auditoria.
   A calibração do Olist continua valendo para `day_of_month` — que é o que ela
   realmente mediu.

2. **`failure_count_90d` é CONTADO, não sorteado.** Na v1 era um Poisson(1,5)
   independente de tudo. Aqui é o número de cobranças falhadas daquele cliente
   nos 90 dias anteriores àquela cobrança — o que só é possível porque agora
   existe histórico por cliente. Mesma coisa para `attempt_count`, que passa a
   ser a posição da cobrança dentro da escada de retentativa.

3. **`card_brand` sai; o vocabulário de erro vira o de produção.** A auditoria
   de 15/09 achou *train/serve skew*: `card_brand` tem 5 bandeiras no treino e
   é `"n/a"` em produção (a CRAI só opera Pix Automático e boleto), e 52% dos
   valores de `gateway_error_code` no treino eram códigos de cartão
   (`expired_card`, `card_declined`, `do_not_honor`). Uma feature constante no
   momento de servir não pode ajudar e pode atrapalhar. Os códigos passam a ser
   os que o sistema emite de verdade.

4. **Tudo tem data.** As cobranças acontecem numa janela de 180 dias que termina
   em `END_DATE`, fixa e derivada da configuração — não de `date.today()`.
"""

from typing import Optional

import numpy as np
import pandas as pd

from .ltv import ltv_estimado
from .populacao import gerar_populacao
from .synthetic_data import (
    BEHAVIORAL_FEATURES,
    VOLUNTARY_FEATURES,
    _aplicar_excecoes,
    _business_day_index,
    _hour_distribution,
    _liquidity_series_customer,
    seed_por_cliente,
)
from . import calibracao as _calibracao

SEED = 42

# Último dia de toda janela temporal da base. Fixo, não `date.today()`: a base
# de 14/09 foi gerada neste dia e a comparação de métricas só é honesta se a
# janela for a mesma.
END_DATE = "2026-09-14"
JANELA_DIAS = 180


# ══════════════════════════════════════════════════════════════════════════
# MÓDULO 1 — CLASSIFICADOR DE FALHA (grão: cobrança)
# ══════════════════════════════════════════════════════════════════════════

# Vocabulário de falha de Pix Automático e boleto. Os quatro primeiros são os
# códigos que o painel já traduz (`CAUSA_K` em `painel/render.js`); o quinto é
# o resíduo. Os códigos de cartão da v1 (`expired_card`, `card_declined`,
# `do_not_honor`) saíram: a CRAI não opera cartão em lugar nenhum.
CAUSAS_FALHA = [
    "insufficient_funds",
    "limit_exceeded",
    "authorization_revoked",
    "processing_error",
    "generic_decline",
]
CAUSAS_FALHA_PROBS = [0.46, 0.18, 0.14, 0.13, 0.09]

# Impacto de cada causa na probabilidade de recuperação. Os três códigos
# herdados mantêm o coeficiente de `synthetic_data._calculate_recovery_probability`,
# palavra por palavra. Os dois novos entram com a hipótese declarada:
#   limit_exceeded        — limite de transação do Pix estourado. Recuperável no
#                           ciclo seguinte, mas pior que saldo insuficiente, que
#                           o payday resolve.
#   authorization_revoked — o cliente revogou a autorização do Pix Automático.
#                           É o análogo do `do_not_honor` da v1 e herda o -0,30:
#                           é a causa mais difícil, porque exige ação do cliente.
IMPACTO_CAUSA = {
    "insufficient_funds": -0.05,
    "limit_exceeded": -0.12,
    "authorization_revoked": -0.30,
    "processing_error": 0.05,
    "generic_decline": -0.20,
}

# Métodos de pagamento. Atributo do cliente, não da cobrança: um cliente não
# alterna entre Pix Automático e boleto a cada fatura.
METODOS_PAGAMENTO = ["pix_automatico", "boleto"]
METODOS_PAGAMENTO_PROBS = [0.85, 0.15]

# Ciclos de cobrança e o fator que cada um aplica sobre o MRR.
#   mensal       — a fatura é a mensalidade
#   anual        — 12 meses com desconto de ~17%
#   proporcional — entrada no meio do ciclo, upgrade, crédito parcial
CICLOS = ["mensal", "anual", "proporcional"]
CICLOS_PROBS = [0.82, 0.06, 0.12]
FATOR_ANUAL = 10.0
FATOR_PROPORCIONAL = (0.15, 0.95)
# Faixa declarada de `invoice_amount / mrr`. Testada linha a linha.
FAIXA_FATOR_CICLO = (0.15, 10.0)


def visao_classificador(
    pop: pd.DataFrame,
    n_cobrancas: int = 120_000,
    seed: Optional[int] = SEED,
    fonte: str = "sintetico",
    end_date: str = END_DATE,
    janela_dias: int = JANELA_DIAS,
) -> pd.DataFrame:
    """Cobranças falhadas — **várias por cliente**.

    Na v1 cada linha era um cliente diferente, o que tornava
    `failure_count_90d` uma coluna sorteada sem lastro. Aqui um cliente tem
    histórico, e as duas features de contagem passam a ser contadas.
    """
    P = _calibracao.parametros(fonte)["classifier"]
    rng = np.random.default_rng(seed)
    n_pop = len(pop)

    # ── Quem falha ───────────────────────────────────────────────────
    # A propensão a falhar depende da saúde financeira latente do cliente. O
    # latente NÃO vira feature; ele só decide quem entra nesta tabela e com que
    # frequência. É a mesma ideia de "nem todo cliente teve cobrança recusada".
    #
    # Com os parâmetros abaixo, ~40% da população tem pelo menos uma cobrança
    # falhada na janela de 180 dias, com média de ~3,5 cobranças entre esses —
    # o que dá folga para `n_cobrancas` até cerca de 1,3 × `len(pop)`.
    propensao = np.clip(0.75 - 0.55 * pop["saude_financeira"].to_numpy(), 0.08, 0.75)
    ordem = rng.permutation(n_pop)

    linhas_por_cliente = []
    acumulado = 0
    for idx in ordem:
        if acumulado >= n_cobrancas:
            break
        if rng.random() > propensao[idx]:
            continue
        # Número de cobranças falhadas na janela. Poisson deslocado: quem
        # aparece aqui falhou pelo menos uma vez.
        k = 1 + int(rng.poisson(lam=1.2 + 3.5 * propensao[idx]))
        k = min(k, 12, n_cobrancas - acumulado)
        linhas_por_cliente.append((idx, k))
        acumulado += k

    if acumulado < n_cobrancas:
        raise RuntimeError(
            f"população de {n_pop} não produziu {n_cobrancas} cobranças "
            f"(chegou a {acumulado}); aumente n ou a propensão"
        )

    idx_cliente = np.repeat(
        np.array([i for i, _ in linhas_por_cliente]),
        np.array([k for _, k in linhas_por_cliente]),
    )
    n = len(idx_cliente)
    sub = pop.iloc[idx_cliente].reset_index(drop=True)

    # ── Datas das cobranças ──────────────────────────────────────────
    fim = pd.Timestamp(end_date)
    inicio = fim - pd.Timedelta(days=janela_dias - 1)
    # `day_of_month` guarda a sazonalidade medida no Olist (Fonte A) — é o que
    # aquela fonte realmente mede, e continua valendo.
    if P["day_of_month_hist"] is None:
        peak = list(P["peak_days"])
        dia = np.where(
            rng.random(n) < P["p_peak_day"],
            rng.choice(peak, size=n),
            rng.integers(1, 29, size=n),
        )
    else:
        hist = np.asarray(P["day_of_month_hist"], dtype=float)
        dia = rng.choice(np.arange(1, len(hist) + 1), size=n, p=hist / hist.sum())

    # Escolhe, dentro da janela, a ocorrência daquele dia do mês; o sorteio do
    # mês é uniforme entre os meses cobertos pela janela.
    meses = pd.date_range(inicio, fim, freq="MS")
    if len(meses) == 0:
        meses = pd.DatetimeIndex([inicio.normalize().replace(day=1)])
    mes_escolhido = meses[rng.integers(0, len(meses), size=n)]
    dia_seguro = np.minimum(dia, mes_escolhido.days_in_month.to_numpy())
    data = pd.to_datetime(
        {"year": mes_escolhido.year, "month": mes_escolhido.month, "day": dia_seguro}
    )
    data = data.clip(inicio, fim)

    df = pd.DataFrame({
        "customer_id": sub["customer_id"].to_numpy(),
        "data_cobranca": data.to_numpy(),
        "day_of_month": pd.DatetimeIndex(data).day.to_numpy(),
        "day_of_week": pd.DatetimeIndex(data).dayofweek.to_numpy(),
    })
    df = df.sort_values(["customer_id", "data_cobranca"], kind="mergesort").reset_index(drop=True)
    sub = pop.set_index("customer_id").loc[df["customer_id"]].reset_index()

    # ── Valor da fatura: DERIVA DO MRR ───────────────────────────────
    ciclo = rng.choice(CICLOS, size=n, p=CICLOS_PROBS)
    fator = np.where(
        ciclo == "mensal", 1.0,
        np.where(ciclo == "anual", FATOR_ANUAL,
                 rng.uniform(*FATOR_PROPORCIONAL, size=n)),
    )
    mrr = sub["mrr"].to_numpy()
    df["invoice_amount"] = (mrr * fator).round(2)
    df["ciclo"] = ciclo

    # `avg_ticket` é o ticket médio DAQUELE CLIENTE — média das faturas dele
    # nesta janela, não a fatura corrente com ruído. Feature derivada de
    # verdade, e a mesma para todas as linhas do cliente.
    df["avg_ticket"] = (
        df.groupby("customer_id")["invoice_amount"].transform("mean").round(2)
    )

    # ── Atributos que vêm da população ───────────────────────────────
    df["tenure_months"] = sub["tenure_months"].to_numpy()

    # ── Contagens: agora contadas, não sorteadas ─────────────────────
    df["failure_count_90d"] = _contar_janela(df, dias=90)
    df["attempt_count"] = np.clip(_contar_janela(df, dias=14) + 1, 1, 4)

    # ── Causa da falha e método de pagamento ─────────────────────────
    df["gateway_error_code"] = rng.choice(CAUSAS_FALHA, size=n, p=CAUSAS_FALHA_PROBS)
    metodo_por_cliente = pd.Series(
        rng.choice(METODOS_PAGAMENTO, size=len(pop), p=METODOS_PAGAMENTO_PROBS),
        index=pop["customer_id"],
    )
    df["metodo_pagamento"] = metodo_por_cliente.loc[df["customer_id"]].to_numpy()

    # ── Histórico de pagamento: atributo do cliente ──────────────────
    # Na v1 era beta(5,2)·0,7 + tenure·0,3, sorteado por linha — o mesmo
    # cliente teria históricos diferentes em cobranças diferentes. Aqui é um
    # atributo, ancorado na saúde financeira latente e no tempo de casa.
    tenure_factor = np.clip(pop["tenure_months"].to_numpy() / 60, 0, 1)
    hist_por_cliente = pd.Series(
        np.clip(
            0.60 * pop["saude_financeira"].to_numpy()
            + 0.25 * tenure_factor
            + 0.15 * rng.beta(5, 2, size=len(pop)),
            0, 1,
        ).round(3),
        index=pop["customer_id"],
    )
    df["payment_history_score"] = hist_por_cliente.loc[df["customer_id"]].to_numpy()

    # ── Hora da tentativa ────────────────────────────────────────────
    p_hora = (_hour_distribution() if P["hour_hist"] is None
              else (np.asarray(P["hour_hist"], float) / np.sum(P["hour_hist"])).tolist())
    df["hour_of_day"] = rng.choice(range(24), size=n, p=p_hora)

    # ── LTV ──────────────────────────────────────────────────────────
    retention_factor = np.clip(
        0.85 + np.clip(df["tenure_months"].to_numpy() / 60, 0, 1) * 0.10, 0.80, 0.98
    )
    df["ltv_estimated"] = ltv_estimado(
        df["tenure_months"].to_numpy(), df["avg_ticket"].to_numpy(),
        df["invoice_amount"].to_numpy(), retention_factor,
    )

    # ── Rótulo ───────────────────────────────────────────────────────
    df["recovered"] = _rotulo_recuperacao(df, rng, P)

    colunas = [
        "customer_id", "data_cobranca", "tenure_months", "day_of_month",
        "day_of_week", "hour_of_day", "invoice_amount", "avg_ticket", "ciclo",
        "gateway_error_code", "metodo_pagamento", "payment_history_score",
        "failure_count_90d", "attempt_count", "ltv_estimated", "recovered",
    ]
    return df[colunas]


def _contar_janela(df: pd.DataFrame, dias: int) -> np.ndarray:
    """Para cada cobrança, quantas cobranças ANTERIORES do mesmo cliente caem
    na janela de `dias` que termina nela. Contagem, não sorteio."""
    out = np.zeros(len(df), dtype=int)
    datas = df["data_cobranca"].to_numpy(dtype="datetime64[D]")
    limite = np.timedelta64(dias, "D")
    for _, idx in df.groupby("customer_id", sort=False).indices.items():
        d = datas[idx]
        # idx já vem ordenado por data (o DataFrame foi ordenado antes)
        for j in range(len(idx)):
            out[idx[j]] = int(np.sum((d[:j] > d[j] - limite) & (d[:j] <= d[j])))
    return out


def _rotulo_recuperacao(df: pd.DataFrame, rng: np.random.Generator, P: dict) -> np.ndarray:
    """`recovered` — a mesma função de `synthetic_data`, com o mapa de causas
    estendido para o vocabulário de Pix/boleto (ver `IMPACTO_CAUSA`).

    O rótulo continua sendo calculado por FÓRMULA a partir das mesmas features
    que o modelo vê. Isso é circular e é a razão de o teto de Bayes medido ser
    0,7095. A troca por rótulo latente é etapa posterior, com portão próprio —
    esta base não a antecipa, justamente para que a comparação de métricas com
    14/09 isole o efeito da população compartilhada.
    """
    n = len(df)
    p = np.full(n, 0.5)
    p += np.clip(df["tenure_months"].to_numpy() / 60, 0, 0.25)
    p += (df["payment_history_score"].to_numpy() - 0.5) * 0.3
    causa = df["gateway_error_code"].to_numpy()
    for code, impacto in IMPACTO_CAUSA.items():
        p[causa == code] += impacto
    p -= np.clip((df["invoice_amount"].to_numpy() - 500) / 5000, 0, 0.15)
    p -= np.clip(df["failure_count_90d"].to_numpy() * 0.05, 0, 0.20)
    payday = np.isin(df["day_of_month"].to_numpy(), [5, 6, 7, 10, 15, 20, 30])
    p[payday] += 0.08
    p -= (df["attempt_count"].to_numpy() - 1) * 0.06
    p += rng.normal(0, P["ruido_rotulo_sd"], size=n)
    p = np.clip(p, 0.05, 0.95)

    recovered = (rng.random(n) < p).astype(int)
    if P["p_excecao_rotulo"] > 0:
        excecao = rng.random(n) < P["p_excecao_rotulo"]
        recovered[excecao] = rng.integers(0, 2, size=int(excecao.sum()))
    return recovered


# ══════════════════════════════════════════════════════════════════════════
# MÓDULO 2 — DETECTOR DE ANOMALIA (grão: cliente)
# ══════════════════════════════════════════════════════════════════════════

def visao_comportamental(
    pop: pd.DataFrame,
    cobrancas: Optional[pd.DataFrame] = None,
    anomaly_rate: float = 0.09,
    seed: Optional[int] = SEED,
    fonte: str = "sintetico",
    end_date: str = END_DATE,
) -> pd.DataFrame:
    """Um retrato de uso por cliente — **toda a população**.

    `tenure_days`, `mrr_brl` e `seats` vêm da população. `failed_pay_90d` é
    contado da tabela de cobranças, quando ela é passada: na v1 era um
    binomial independente, e o cliente podia ter 3 falhas aqui e nenhuma lá.
    """
    P = _calibracao.parametros(fonte)["behavioral"]
    rng = np.random.default_rng(seed)
    n = len(pop)

    is_anomalous = (rng.random(n) < anomaly_rate).astype(int)

    tenure_days = np.clip(
        pop["tenure_months"].to_numpy() * 30.44 + rng.uniform(0, 30, size=n),
        30, 2000,
    ).astype(int)
    mrr_brl = pop["mrr"].to_numpy()
    seats = pop["seats"].to_numpy()

    # ── Inatividade: atributo do cliente, compartilhado com o Módulo 4 ──
    # `days_since_last_login` aqui e `days_since_last` no voluntário mediam a
    # mesma coisa em duas escalas diferentes, em bases sem interseção. Agora
    # os dois saem do mesmo número.
    Qa, Qs = P["anomalo"], P["saudavel"]
    escala = np.where(is_anomalous == 1,
                      Qa["days_since_last_login_scale"],
                      Qs["days_since_last_login_scale"])
    inatividade = rng.exponential(scale=escala).clip(0, 30)

    adocao = np.where(
        is_anomalous == 1,
        rng.beta(2, 5, size=n),
        rng.beta(5, 2, size=n),
    ).round(3)

    logins_30d = np.where(
        is_anomalous == 1,
        rng.normal(seats * 5, np.maximum(seats * 2, 1e-9)),
        rng.normal(seats * 18, np.maximum(seats * 3, 1e-9)),
    ).clip(0).astype(int)
    fator_7d = np.where(is_anomalous == 1,
                        rng.uniform(0.05, 0.15, size=n),
                        rng.uniform(0.22, 0.30, size=n))
    logins_7d = (logins_30d * fator_7d).astype(int)

    avg_session_min = np.where(
        is_anomalous == 1,
        rng.normal(Qa["avg_session"]["loc"], Qa["avg_session"]["scale"], size=n).clip(0.5),
        rng.normal(Qs["avg_session"]["loc"], Qs["avg_session"]["scale"], size=n).clip(2.0),
    ).round(1)

    api_calls_7d = np.where(
        is_anomalous == 1,
        rng.lognormal(4.5, 1.0, size=n).clip(0),
        rng.lognormal(7.0, 0.8, size=n).clip(10),
    ).astype(int)

    tickets_30d = np.where(
        is_anomalous == 1,
        rng.poisson(Qa["tickets_lam"], size=n),
        rng.poisson(Qs["tickets_lam"], size=n),
    )
    nps_last = np.where(
        is_anomalous == 1,
        rng.normal(Qa["nps"]["loc"], Qa["nps"]["scale"], size=n),
        rng.normal(Qs["nps"]["loc"], Qs["nps"]["scale"], size=n),
    ).clip(0, 10).round(1)

    # ── failed_pay_90d: CONTADO da tabela de cobranças ───────────────
    if cobrancas is not None and len(cobrancas):
        fim = pd.Timestamp(end_date)
        recorte = cobrancas[cobrancas["data_cobranca"] > fim - pd.Timedelta(days=90)]
        contagem = recorte.groupby("customer_id").size()
        failed_pay_90d = (
            pd.Series(0, index=pop["customer_id"])
            .add(contagem, fill_value=0)
            .loc[pop["customer_id"]]
            .to_numpy()
        )
        # A feature da v1 é binomial(n=3), então o alcance dela é 0..3.
        # Mantido para que a arquitetura do autoencoder não mude.
        failed_pay_90d = np.clip(failed_pay_90d, 0, 3).astype(int)
    else:
        failed_pay_90d = np.where(
            is_anomalous == 1,
            rng.binomial(3, 0.35, size=n),
            rng.binomial(3, 0.05, size=n),
        )

    df = pd.DataFrame({
        "customer_id": pop["customer_id"].to_numpy(),
        "tenure_days": tenure_days,
        "mrr_brl": mrr_brl,
        "seats": seats,
        "logins_7d": logins_7d,
        "logins_30d": logins_30d,
        "feature_adoption": adocao,
        "avg_session_min": avg_session_min,
        "api_calls_7d": api_calls_7d,
        "days_since_last_login": inatividade.astype(int),
        "tickets_30d": tickets_30d,
        "failed_pay_90d": failed_pay_90d,
        "nps_last": nps_last,
        "is_anomalous": is_anomalous,
    })

    # ── Anti-circularidade: as mesmas exceções da v1 ─────────────────
    # Sem elas o rótulo é dedutível das features e o ROC-AUC vai a 0,995 —
    # a bandeira vermelha que o README marca.
    if P["p_excecao_anomalo"] > 0 or P["p_excecao_saudavel"] > 0:
        df = _trocar_por_sombra(df, rng, P)

    return df[["customer_id", *BEHAVIORAL_FEATURES, "is_anomalous"]]


def _trocar_por_sombra(df: pd.DataFrame, rng: np.random.Generator, P: dict) -> pd.DataFrame:
    """Aplica as exceções da v1 sobre a tabela única, sem quebrá-la em duas.

    Na v1 as duas populações eram DataFrames separados e a sombra era uma
    terceira geração; aqui a tabela é uma só (um cliente, uma linha), então a
    sombra é montada por reamostragem dentro da própria tabela: a linha
    anômala que "esconde o sinal" recebe features de uma linha saudável
    sorteada, e vice-versa. O efeito estatístico é o mesmo.
    """
    df = df.copy()
    saudaveis = np.where(df["is_anomalous"].to_numpy() == 0)[0]
    anomalos = np.where(df["is_anomalous"].to_numpy() == 1)[0]
    if len(saudaveis) == 0 or len(anomalos) == 0:
        return df

    sombra_a = df.iloc[rng.choice(saudaveis, size=len(anomalos))].reset_index(drop=True)
    alvo_a = df.iloc[anomalos].reset_index(drop=True)
    alvo_a = _aplicar_excecoes(alvo_a, sombra_a, P["p_excecao_anomalo"], 3, rng)
    df.iloc[anomalos] = alvo_a.to_numpy()

    sombra_s = df.iloc[rng.choice(anomalos, size=len(saudaveis))].reset_index(drop=True)
    alvo_s = df.iloc[saudaveis].reset_index(drop=True)
    alvo_s = _aplicar_excecoes(alvo_s, sombra_s, P["p_excecao_saudavel"], 2, rng)
    df.iloc[saudaveis] = alvo_s.to_numpy()
    return df


# ══════════════════════════════════════════════════════════════════════════
# MÓDULO 3 — INFERÊNCIA DE LIQUIDEZ (grão: cliente-dia)
# ══════════════════════════════════════════════════════════════════════════

def visao_liquidez(
    pop: pd.DataFrame,
    n_customers: int = 10_000,
    n_days: int = JANELA_DIAS,
    seed: Optional[int] = SEED,
    fonte: str = "sintetico",
    end_date: str = END_DATE,
) -> pd.DataFrame:
    """Saldo diário — **subamostra declarada** da população.

    O grão aqui é cliente-dia. A população inteira × 180 dias daria 21,6
    milhões de linhas, ~830 MB em CSV, para treinar uma LSTM que converge com
    muito menos. A subamostra é estratificada por perfil de cobrança e o
    tamanho é um parâmetro declarado — não um acidente.
    """
    rng = np.random.default_rng(seed)

    # Estratificação por perfil: a subamostra preserva as proporções da
    # população, senão o prior sazonal do Prophet vem enviesado.
    # Sem `groupby.apply`: a assinatura dele mudou entre pandas 2 e 3 e este
    # script precisa rodar igual nas duas.
    partes = []
    for perfil, grupo in pop.groupby("billing_profile", sort=True):
        k = min(len(grupo), max(1, int(round(n_customers * len(grupo) / len(pop)))))
        partes.append(grupo.sample(n=k, random_state=seed))
    escolhidos = (
        pd.concat(partes).sort_values("customer_id").reset_index(drop=True)
    )

    P = _calibracao.parametros(fonte)["liquidity"]
    fim = pd.Timestamp(end_date)
    dates = pd.date_range(end=fim, periods=n_days, freq="D")
    bday_idx = _business_day_index(dates)

    series = [
        _liquidity_series_customer(
            row.customer_id, row.billing_profile, dates, bday_idx, rng, P
        )
        for row in escolhidos.itertuples()
    ]
    df = pd.concat(series, ignore_index=True)
    # `profile` é o nome que o Módulo 3 espera; é o `billing_profile` da população.
    return df


# ══════════════════════════════════════════════════════════════════════════
# MÓDULO 4 — RISCO VOLUNTÁRIO (grão: evento de SDK)
# ══════════════════════════════════════════════════════════════════════════

def visao_voluntario(
    pop: pd.DataFrame,
    comportamental: Optional[pd.DataFrame] = None,
    n_eventos: int = 120_000,
    seed: Optional[int] = SEED,
    fonte: str = "sintetico",
) -> pd.DataFrame:
    """Eventos de SDK — **vários por cliente**.

    `mrr` vem da população. `days_since_last` e `features_used_30d` derivam do
    retrato comportamental do MESMO cliente, quando ele é passado: na v1 essas
    duas colunas eram sorteadas de distribuições próprias, numa base sem
    interseção com a do Módulo 2 — o mesmo cliente podia estar ativo lá e
    inativo aqui.
    """
    from ..churn_voluntary.risk_scorer import _risco_por_regras
    from .synthetic_data import _EVENTOS_VOLUNTARIOS, _amostrar_inteiro

    P = _calibracao.parametros(fonte)["voluntary"]
    rng = np.random.default_rng(seed)
    n_pop = len(pop)

    # Quantos eventos cada cliente gera. Nem todo cliente tem SDK instrumentado;
    # quem tem gera uma sequência.
    ordem = rng.permutation(n_pop)
    escolha, acumulado = [], 0
    for idx in ordem:
        if acumulado >= n_eventos:
            break
        k = 1 + int(rng.poisson(lam=2.0))
        k = min(k, 15, n_eventos - acumulado)
        escolha.append((idx, k))
        acumulado += k
    if acumulado < n_eventos:
        raise RuntimeError(f"população não produziu {n_eventos} eventos (chegou a {acumulado})")

    idx_cliente = np.repeat(
        np.array([i for i, _ in escolha]), np.array([k for _, k in escolha])
    )
    n = len(idx_cliente)
    sub = pop.iloc[idx_cliente].reset_index(drop=True)

    # ── As duas features de engajamento ──────────────────────────────
    if comportamental is not None and len(comportamental):
        comp = comportamental.set_index("customer_id")
        base_inativo = comp.loc[sub["customer_id"], "days_since_last_login"].to_numpy()
        base_adocao = comp.loc[sub["customer_id"], "feature_adoption"].to_numpy()
        # Deriva por evento: o retrato é de um instante, o evento é de outro.
        days_since_last = np.clip(
            base_inativo + rng.integers(-2, 4, size=n), 0, 60
        ).astype(int)
        # `features_used_30d` é a contagem que corresponde à adoção do retrato.
        # 12 é o número de funcionalidades rastreadas pelo SDK.
        features_used_30d = np.clip(
            np.round(base_adocao * 12 + rng.normal(0, 1.2, size=n)), 0, 12
        ).astype(int)
    else:
        days_since_last = _amostrar_inteiro(P["days_since_last"], n, rng)
        features_used_30d = _amostrar_inteiro(P["features_used_30d"], n, rng)

    mrr = sub["mrr"].to_numpy()
    mix = P["mix_eventos"]
    eventos = rng.choice(_EVENTOS_VOLUNTARIOS, size=n,
                         p=[mix[e] for e in _EVENTOS_VOLUNTARIOS])

    # ── Rótulo: as regras REAIS do scorer, como na v1 ────────────────
    risk_regra = np.array([
        _risco_por_regras(str(e), {"days_since_last": int(d), "features_used_30d": int(f)})
        for e, d, f in zip(eventos, days_since_last, features_used_30d)
    ], dtype=float)
    p = np.clip(risk_regra + rng.normal(0, P["ruido_rotulo_sd"], size=n), 0.02, 0.98)
    churn = (rng.random(n) < p).astype(int)
    if P["p_excecao_rotulo"] > 0:
        excecao = rng.random(n) < P["p_excecao_rotulo"]
        churn[excecao] = rng.integers(0, 2, size=int(excecao.sum()))

    return pd.DataFrame({
        "customer_id": sub["customer_id"].to_numpy(),
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

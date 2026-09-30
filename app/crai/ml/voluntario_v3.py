"""
crai/ml/voluntario_v3.py — A base v3 do risco voluntário: rótulo latente.

Por que existe
──────────────
Na base v2 o rótulo do voluntário é `Bernoulli(regras fixas + ruído)`
(`visoes.visao_voluntario`): a régua fixa É o teto de Bayes, e nenhum modelo que
veja as mesmas features pode passar dela — o melhor que ele faz é empatar. O v3
troca a causa: o cancelamento é decidido pelas quatro latentes da população
(`satisfacao`, `fit_produto`, `pressao_preco`, `saude_financeira`), que o
modelo NUNCA vê, e o modelo aprende pelos rastros que elas deixam no
comportamento, que ele vê. Desenho aprovado no Bloco 0 do treino v3
(`docs/interno/RELATORIO_TREINO_V3_BLOCO0.md`, decisões D1-D9, 29/09/2026).

O rótulo (grão da decisão: cliente)
───────────────────────────────────
    z_x          = (x - MEDIAS_LATENTES[x]) / DESVIOS_LATENTES[x]
    eta          = B0 + soma(COEF_LATENTES[x] * z_x)
                   + INTERACAO_PRECO_SAUDE * z_preco * (-z_saude)
    p_verdadeira = 1 / (1 + exp(-eta))
    p            = clip(p_verdadeira + N(0; RUIDO_ROTULO_SD); CLIP_P)
    churn        = Bernoulli(p); P_SORTEIO dos clientes trocados por moeda honesta

O termo de interação preço x saúde foi posto DE PROPÓSITO: é o único termo não
aditivo, e existe para haver algo que uma árvore capte e uma régua linear não.
É escolha de desenho, não descoberta — e favorece modelos de árvore.

Os rastros
──────────
As colunas comportamentais do v3 são ANCORADAS no retrato v2 do mesmo cliente
(`comportamental.parquet`, que não muda): parte do valor v2 — com a estrutura de
`is_anomalous`, que vira ruído alheio ao churn — e soma um deslocamento que
depende das latentes, com ruído próprio. Nada no comportamento é causado pelo
rótulo: os dois são causados pelas latentes. Por isso o teto de Bayes é
exatamente a AUC de `p_verdadeira`, gravada em `oculto_p_churn`.

Sementes
────────
NUNCA `np.random.default_rng(seed)` cru: `gerar_populacao(seed)` abre o mesmo
fluxo, e os sorteios se alinham (Bloco 0, M2b: o 1º normal cru tem correlação
0,9965 com log(mrr)). Cada finalidade tem o seu fluxo derivado, ver `fluxo()`.
"""

from typing import Optional

import numpy as np
import pandas as pd

from .populacao import LATENTES

SEED = 42

# ── Contrato de features ─────────────────────────────────────────────────
# A ORDEM É O CONTRATO, como em `risk_scorer.FEATURES_DE_RISCO`. Os gêmeos
# substituem, não coexistem (D3): ficam as versões POR EVENTO que a régua lê e
# o SDK entrega (`days_since_last`, `features_used_30d`); saem os retratos
# `days_since_last_login` e `feature_adoption` (gravados como auxiliares),
# `mrr_brl` (idêntico a `mrr`) e `tenure_months` (fica `tenure_days`).
FEATURES_DE_RISCO_V3_VERSAO = 1
FEATURES_DE_RISCO_V3 = [
    "days_since_last",
    "features_used_30d",
    "mrr",
    "tenure_days",
    "seats",
    "logins_7d",
    "logins_30d",
    "avg_session_min",
    "api_calls_7d",
    "tickets_30d",
    "failed_pay_90d",
    "nps_last",
    "evento_cancelamento",
    "evento_downgrade",
    "evento_sessao",
]
COLUNAS_AUXILIARES = ["days_since_last_login", "feature_adoption"]
PREFIXO_OCULTO = "oculto_"
COLUNAS_OCULTAS = ["oculto_p_churn", "oculto_rotulo_sorteado"]
COLUNAS_V3 = ["customer_id", "event", *FEATURES_DE_RISCO_V3, *COLUNAS_AUXILIARES,
              "risk_regra", "churn", *COLUNAS_OCULTAS]

EVENTOS = ["Session Started", "Downgrade Clicked", "Cancellation Page Viewed"]

# ── O rótulo (D1, D2) ────────────────────────────────────────────────────
# Médias e desvios das latentes na população v2 (Bloco 0, M4). Constantes, e
# não recalculadas: o rótulo de um cliente não pode depender de quem mais está
# na tabela.
MEDIAS_LATENTES = {"satisfacao": 0.6933, "fit_produto": 0.7147,
                   "pressao_preco": 0.4004, "saude_financeira": 0.6500}
DESVIOS_LATENTES = {"satisfacao": 0.1329, "fit_produto": 0.1597,
                    "pressao_preco": 0.1682, "saude_financeira": 0.1403}
COEF_LATENTES = {"satisfacao": -0.80, "fit_produto": -0.50,
                 "pressao_preco": 0.60, "saude_financeira": -0.40}
INTERACAO_PRECO_SAUDE = 0.30
# Resolvido no piloto do Bloco 0 para média de p_verdadeira = 0,15.
B0 = -2.3279
TAXA_ALVO_P_VERDADEIRA = 0.15
# Mesma disciplina do classificador (`parametros.classifier` da calibração).
RUIDO_ROTULO_SD = 0.05
P_SORTEIO = 0.02
CLIP_P = (0.02, 0.98)
# Faixa aprovada da taxa de churn REALIZADA (D2), testada.
FAIXA_TAXA_CHURN = (0.13, 0.18)

# ── Os rastros (D4) ──────────────────────────────────────────────────────
# Faixa aprovada de |Spearman| entre um rastro criado pelo v3 e a sua latente
# principal, e o teto que nenhuma feature pode passar contra latente nenhuma.
FAIXA_SPEARMAN_RASTRO = (0.25, 0.60)
TETO_SPEARMAN = 0.70
# Rastro criado pelo v3 -> latente principal. É o que a faixa confere.
RASTROS_COM_FAIXA = {
    "nps_last": "satisfacao",
    "tickets_30d": "satisfacao",
    "feature_adoption": "fit_produto",
    "days_since_last_login": "satisfacao",
    "days_since_last": "satisfacao",
    "features_used_30d": "fit_produto",
}

# Mix de eventos da v2 (`calibracao.json`, `mix_eventos`), mantido na média.
MIX_EVENTOS = {"Session Started": 0.85, "Downgrade Clicked": 0.10,
               "Cancellation Page Viewed": 0.05}

# ── Fluxos aleatórios ────────────────────────────────────────────────────
# O do rótulo é `spawn_key=(3,)`, o mesmo do piloto do Bloco 0, na mesma ordem
# de sorteios: o rótulo de cada cliente é o do piloto.
FLUXO_ROTULO = (3,)
FLUXO_RASTROS = (3, 1)
FLUXO_EVENTOS = (3, 2)
FLUXO_DERIVA = (3, 3)


def fluxo(seed: int, spawn_key: tuple) -> np.random.Generator:
    """Gerador derivado de `seed`, independente de `default_rng(seed)`."""
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=spawn_key))


def _z(pop: pd.DataFrame) -> dict:
    return {l: ((pop[l].to_numpy(dtype=float) - MEDIAS_LATENTES[l]) / DESVIOS_LATENTES[l])
            for l in LATENTES}


def p_verdadeira(pop: pd.DataFrame) -> np.ndarray:
    """A probabilidade de churn por construção, uma por cliente da população."""
    z = _z(pop)
    eta = B0 + sum(COEF_LATENTES[l] * z[l] for l in LATENTES)
    eta = eta + INTERACAO_PRECO_SAUDE * z["pressao_preco"] * (-z["saude_financeira"])
    return 1.0 / (1.0 + np.exp(-eta))


def rotulo_por_cliente(pop: pd.DataFrame, seed: int = SEED) -> pd.DataFrame:
    """`churn`, `p_verdadeira` e `sorteado` para TODA a população, na ordem dela.

    Calculado na população inteira (e não só em quem tem SDK) para que o
    rótulo de um cliente não dependa de quem mais entrou na tabela.
    """
    pv = p_verdadeira(pop)
    rng = fluxo(seed, FLUXO_ROTULO)
    n = len(pop)
    p = np.clip(pv + rng.normal(0, RUIDO_ROTULO_SD, size=n), *CLIP_P)
    churn = (rng.random(n) < p).astype(int)
    sorteado = rng.random(n) < P_SORTEIO
    churn[sorteado] = rng.integers(0, 2, size=int(sorteado.sum()))
    return pd.DataFrame({"customer_id": pop["customer_id"].to_numpy(),
                         "churn": churn, "p_verdadeira": pv,
                         "sorteado": sorteado.astype(int)})


def rastros_por_cliente(pop: pd.DataFrame, comp: pd.DataFrame,
                        seed: int = SEED) -> pd.DataFrame:
    """As colunas comportamentais do v3, uma linha por cliente da população.

    Ancoradas no retrato v2 do mesmo cliente. `failed_pay_90d`, `mrr`, `seats`
    e `tenure_days` passam iguais: os rastros deles (saúde financeira, pressão
    de preço) já existem por construção na v2.
    """
    c = comp.set_index("customer_id").loc[pop["customer_id"]]
    z = _z(pop)
    zs, zf = z["satisfacao"], z["fit_produto"]
    rng = fluxo(seed, FLUXO_RASTROS)
    n = len(pop)
    e = {nome: rng.normal(0, 1, size=n)
         for nome in ("nps", "adocao", "dias", "logins", "sessao", "api")}
    lam_tickets = 0.6 * np.exp(-0.5 * zs - 0.3 * zf)
    tickets_extra = rng.poisson(lam_tickets)

    fator_logins = np.exp(0.20 * zf + 0.10 * zs + 0.15 * e["logins"])
    return pd.DataFrame({
        "customer_id": pop["customer_id"].to_numpy(),
        "mrr": pop["mrr"].to_numpy(dtype=float),
        "tenure_days": c["tenure_days"].to_numpy(dtype=float),
        "seats": c["seats"].to_numpy(dtype=float),
        "logins_7d": np.round(c["logins_7d"].to_numpy() * fator_logins),
        "logins_30d": np.round(c["logins_30d"].to_numpy() * fator_logins),
        "avg_session_min": np.round(
            c["avg_session_min"].to_numpy() * np.exp(0.15 * zf + 0.15 * e["sessao"]), 1),
        "api_calls_7d": np.round(
            c["api_calls_7d"].to_numpy() * np.exp(0.25 * zf + 0.20 * e["api"])),
        "tickets_30d": (c["tickets_30d"].to_numpy() + tickets_extra).astype(float),
        "failed_pay_90d": c["failed_pay_90d"].to_numpy(dtype=float),
        "nps_last": np.round(np.clip(
            c["nps_last"].to_numpy() + 1.2 * zs + 0.6 * e["nps"], 0, 10), 1),
        "days_since_last_login": np.clip(np.round(
            c["days_since_last_login"].to_numpy()
            * np.exp(-0.35 * zs - 0.20 * zf + 0.25 * e["dias"])), 0, 30),
        "feature_adoption": np.round(np.clip(
            c["feature_adoption"].to_numpy() + 0.12 * zf + 0.04 * e["adocao"], 0, 1), 3),
    })


def _tipos_de_evento(z_ev: dict, rng: np.random.Generator) -> np.ndarray:
    """Tipo de cada evento. Latente baixa de satisfação e alta de pressão de
    preço inclinam para cancelamento; pressão de preço e saúde financeira
    baixa, para downgrade. Renormalizado para o mix da v2 na média."""
    w_canc = np.exp(-0.6 * z_ev["satisfacao"] + 0.4 * z_ev["pressao_preco"])
    w_down = np.exp(0.6 * z_ev["pressao_preco"] - 0.3 * z_ev["saude_financeira"])
    p_canc = MIX_EVENTOS["Cancellation Page Viewed"] * w_canc / w_canc.mean()
    p_down = MIX_EVENTOS["Downgrade Clicked"] * w_down / w_down.mean()
    excesso = np.maximum(p_canc + p_down - 1.0, 0.0)
    p_down = p_down - excesso
    u = rng.random(len(p_canc))
    return np.where(u < p_canc, "Cancellation Page Viewed",
                    np.where(u < p_canc + p_down, "Downgrade Clicked", "Session Started"))


def gerar_voluntario_v3(pop: pd.DataFrame, comp: pd.DataFrame,
                        esqueleto: pd.Series, seed: Optional[int] = SEED) -> pd.DataFrame:
    """A tabela `voluntario_v3`: uma linha por EVENTO, com o esqueleto da v2.

    Args:
        pop: `populacao.parquet` da v2.
        comp: `comportamental.parquet` da v2.
        esqueleto: `customer_id` de `voluntario.parquet` da v2, na ordem — os
            mesmos clientes e o mesmo número de eventos por cliente.
        seed: semente; os fluxos são derivados dela (`fluxo`).

    Returns:
        DataFrame com `COLUNAS_V3`. As latentes não são colunas; o que o treino
        não pode ver tem prefixo `oculto_`.
    """
    from ..churn_voluntary.risk_scorer import _risco_por_regras

    rot = rotulo_por_cliente(pop, seed).set_index("customer_id")
    ras = rastros_por_cliente(pop, comp, seed).set_index("customer_id")
    ids = pd.Index(np.asarray(esqueleto))
    n = len(ids)

    ev = ras.loc[ids].reset_index()
    lat = pop.set_index("customer_id").loc[ids]
    z_ev = {l: ((lat[l].to_numpy(dtype=float) - MEDIAS_LATENTES[l]) / DESVIOS_LATENTES[l])
            for l in LATENTES}
    eventos = _tipos_de_evento(z_ev, fluxo(seed, FLUXO_EVENTOS))

    # Deriva por evento — a mesma de `visoes.visao_voluntario` (v2), sobre as
    # colunas auxiliares do v3: o retrato é de um instante, o evento de outro.
    rng = fluxo(seed, FLUXO_DERIVA)
    days_since_last = np.clip(
        ev["days_since_last_login"].to_numpy() + rng.integers(-2, 4, size=n), 0, 60)
    features_used_30d = np.clip(
        np.round(ev["feature_adoption"].to_numpy() * 12 + rng.normal(0, 1.2, size=n)), 0, 12)

    risk_regra = np.array([
        _risco_por_regras(str(e), {"days_since_last": int(d), "features_used_30d": int(f)})
        for e, d, f in zip(eventos, days_since_last, features_used_30d)
    ], dtype=float)

    df = pd.DataFrame({
        "customer_id": ids.to_numpy(dtype=object),
        "event": eventos,
        "days_since_last": days_since_last.astype(float),
        "features_used_30d": features_used_30d.astype(float),
    })
    for col in FEATURES_DE_RISCO_V3[2:12] + COLUNAS_AUXILIARES:
        df[col] = ev[col].to_numpy(dtype=float)
    df["evento_cancelamento"] = (eventos == "Cancellation Page Viewed").astype(float)
    df["evento_downgrade"] = (eventos == "Downgrade Clicked").astype(float)
    df["evento_sessao"] = (eventos == "Session Started").astype(float)
    df["risk_regra"] = risk_regra
    df["churn"] = rot.loc[ids, "churn"].to_numpy(dtype="int64")
    df["oculto_p_churn"] = rot.loc[ids, "p_verdadeira"].to_numpy(dtype=float)
    df["oculto_rotulo_sorteado"] = rot.loc[ids, "sorteado"].to_numpy(dtype="int64")
    return df[COLUNAS_V3]


def conferir_features(features) -> list:
    """Levanta `ValueError` se a lista traz latente ou coluna `oculto_`.

    É a guarda que o treino usa na matriz de features (D9): `oculto_p_churn`
    pode estar no parquet, para o teto de Bayes, mas nunca entre as features.
    """
    features = list(features)
    proibidas = [f for f in features
                 if f.startswith(PREFIXO_OCULTO) or f in LATENTES]
    if proibidas:
        raise ValueError(f"features proibidas na matriz de treino: {proibidas} "
                         "(latente ou prefixo oculto_)")
    return features


def verificacoes(df: pd.DataFrame, pop: pd.DataFrame) -> dict:
    """Os números que o MANIFESTO_v3.json grava e os testes conferem."""
    from scipy.stats import spearmanr
    from sklearn.metrics import roc_auc_score

    lat_ev = pop.set_index("customer_id").loc[df["customer_id"], LATENTES].reset_index(drop=True)
    por_cliente = df.drop_duplicates("customer_id").reset_index(drop=True)
    lat_cli = pop.set_index("customer_id").loc[por_cliente["customer_id"], LATENTES].reset_index(drop=True)

    grao_evento = {"days_since_last", "features_used_30d",
                   "evento_cancelamento", "evento_downgrade", "evento_sessao"}
    spearman = {}
    for col in FEATURES_DE_RISCO_V3 + COLUNAS_AUXILIARES:
        base, lat = (df, lat_ev) if col in grao_evento else (por_cliente, lat_cli)
        spearman[col] = {l: round(float(spearmanr(base[col], lat[l])[0]), 4) for l in LATENTES}

    rastros = {col: {"latente": lat, "abs_spearman": abs(spearman[col][lat])}
               for col, lat in RASTROS_COM_FAIXA.items()}
    maior = max(((abs(v), c, l) for c, d in spearman.items() for l, v in d.items()
                 if c in FEATURES_DE_RISCO_V3))
    return {
        "eventos": int(len(df)),
        "clientes": int(df["customer_id"].nunique()),
        "taxa_churn_evento": round(float(df["churn"].mean()), 4),
        "taxa_churn_cliente": round(float(por_cliente["churn"].mean()), 4),
        "faixa_taxa_churn": list(FAIXA_TAXA_CHURN),
        "clientes_com_rotulo_sorteado": int(por_cliente["oculto_rotulo_sorteado"].sum()),
        "mix_eventos": {k: round(float(v), 4)
                        for k, v in df["event"].value_counts(normalize=True).items()},
        "teto_bayes_auc_evento_base_inteira": round(
            float(roc_auc_score(df["churn"], df["oculto_p_churn"])), 4),
        "teto_bayes_auc_cliente_base_inteira": round(
            float(roc_auc_score(por_cliente["churn"], por_cliente["oculto_p_churn"])), 4),
        "spearman_feature_x_latente": spearman,
        "rastros_com_faixa": rastros,
        "faixa_spearman_rastro": list(FAIXA_SPEARMAN_RASTRO),
        "teto_spearman": TETO_SPEARMAN,
        "maior_abs_spearman_feature": {"valor": round(maior[0], 4), "feature": maior[1],
                                       "latente": maior[2]},
        "nenhuma_latente_nas_features": not (set(LATENTES) & set(FEATURES_DE_RISCO_V3)),
        "nenhuma_latente_nas_colunas": not (set(LATENTES) & set(df.columns)),
        "nulos": int(df.isna().sum().sum()),
    }

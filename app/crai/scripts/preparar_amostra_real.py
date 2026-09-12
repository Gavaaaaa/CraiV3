"""crai/scripts/preparar_amostra_real.py — Âncora real do dataset sintético.

Constrói, de forma **determinística e auditável**, os artefatos de dados
reais que calibram `crai/ml/synthetic_data.py`:

    data/real/amostra_300.csv        Fonte A — 300 transações reais (Olist)
    data/real/bacen_sgs_21084.json   Fonte B — inadimplência PF (BACEN SGS)
    data/real/bacen_sgs_21129.json   Fonte B — inadimplência PF cartão
    data/real/ecommerce_churn.csv    Fonte E — E-Commerce Customer Churn
                                     (só as colunas usadas, 5.630 linhas)
    data/real/PROVENIENCIA.json      hashes, contagens, parâmetros do sorteio
                                     e a proveniência feature a feature
    models/calibracao.json           os parâmetros MEDIDOS que o gerador lê
                                     com fonte="sintetico_calibrado" (é o
                                     único arquivo de models/ no git)

Rodar de `app/`:

    python -m crai.scripts.preparar_amostra_real
    python -m crai.scripts.preparar_amostra_real --sem-rede   # só cache local

O script é idempotente: mesma seed, mesmas 300 linhas, mesmo SHA-256. É isso
que permite ao DATA_CARD afirmar *qual* amostra foi usada, e não apenas que
"uma amostra foi usada".

FONTE A — Brazilian E-Commerce Public Dataset by Olist
    Licença CC BY-NC-SA 4.0 (uso acadêmico permitido, comercial NÃO).
    Origem canônica: kaggle.com/datasets/olistbr/brazilian-ecommerce
    Kaggle exige conta; para manter o pipeline reprodutível sem credencial, o
    download usa um espelho público dos mesmos arquivos (ver ESPELHO_OLIST) e
    o SHA-256 de cada arquivo fica registrado em PROVENIENCIA.json — quem
    quiser conferir baixa do Kaggle e compara o hash.

FONTE B — BACEN, Sistema Gerenciador de Séries Temporais (SGS)
    Licença Open Data Commons ODbL, API aberta, sem chave.
    Se a API estiver fora de alcance (proxy corporativo, sem rede), o script
    usa o JSON já em disco; se nem isso houver, usa os valores DECLARADOS no
    DATA_CARD (§3) e grava na proveniência que fez isso.

FONTE E — Ecommerce Customer Churn Analysis and Prediction (Kaggle,
    ankitverma2010): 5.630 clientes de um e-commerce, 20 colunas, com rótulo
    `Churn`. Licença não declarada na página do Kaggle: tratado como uso
    acadêmico apenas — o bruto fica FORA do git, como a Olist. Mesma
    convenção: espelho público + SHA-256 registrado. Mapeamento feature a
    feature (fixo, não inventar outro):

        DaySinceLastOrder  -> days_since_last (risk_scorer) e
                              days_since_last_login (AnomalyDetector)
        OrderCount         -> features_used_30d           (proxy fraco)
        HourSpendOnApp     -> avg_session_min             (proxy fraco)
        Complain           -> tickets_30d                 (proxy fraco)
        SatisfactionScore  -> nps_last                    (proxy fraco)

    O `Churn` real NÃO entra em nenhum parâmetro do gerador nem em nenhum
    treino — é usado só na checagem fora do domínio (`scripts/sanity_check_
    fora_do_dominio.py`), como rótulo que o modelo nunca viu.
"""

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from crai.ml import calibracao as _calib

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_REAL = BASE_DIR / "data" / "real"
CALIBRACAO_PATH = _calib.CALIBRACAO_PATH

SEED_AMOSTRA = 42
N_AMOSTRA = 300

ESPELHO_OLIST = "https://raw.githubusercontent.com/dujiaying/olist/master/data"
ARQUIVOS_OLIST = (
    "olist_order_payments_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_customers_dataset.csv",
)

SGS_URL = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados"
           "?formato=json&dataInicial=01/01/2020")
SERIES_SGS = (21084, 21129)
# Valores da série 21084 declarados no DATA_CARD §3 (79 obs., 01/2020-07/2026).
# Só entram quando a API E o cache local estão indisponíveis — e a proveniência
# registra que entraram.
SGS_21084_DECLARADO = {"media": 3.92, "ultimo": 5.81, "n_obs": 79}

# Fonte E — espelho público do arquivo do Kaggle (mesmo xlsx, 5.630 linhas).
ESPELHO_ECOMMERCE = ("https://raw.githubusercontent.com/JamshidSalimov/Ai-Fayls/"
                     "master/E-Commerce-Dataset.xlsx")
ARQUIVO_ECOMMERCE = "E-Commerce-Dataset.xlsx"
PLANILHA_ECOMMERCE = "E Comm"
COLUNAS_ECOMMERCE = ("Churn", "Tenure", "DaySinceLastOrder", "OrderCount",
                     "HourSpendOnApp", "Complain", "SatisfactionScore")
N_ECOMMERCE_ESPERADO = 5630

# `not_defined` são 3 linhas sem meio de pagamento declarado; valor zero é
# quitação integral por voucher. Nenhum dos dois informa a marginal de valor
# de uma cobrança de assinatura, então saem antes do sorteio — e o número de
# linhas descartadas fica registrado na proveniência.
TIPOS_EXCLUIDOS = ("not_defined",)


def _sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def baixar_olist(destino: Path) -> dict[str, str]:
    """Baixa os três CSVs da Fonte A (se ainda não estiverem em `destino`)."""
    destino.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for nome in ARQUIVOS_OLIST:
        alvo = destino / nome
        if not alvo.exists():
            print(f"[DADOS] baixando {nome} …")
            urllib.request.urlretrieve(f"{ESPELHO_OLIST}/{nome}", alvo)
        hashes[nome] = _sha256(alvo)
        print(f"[DADOS] {nome}: {alvo.stat().st_size} bytes | sha256 {hashes[nome][:16]}…")
    return hashes


def baixar_sgs() -> dict[int, int]:
    """Baixa as séries da Fonte B. Devolve {serie: nº de observações}.

    Uma série que não puder ser baixada (API fora de alcance) fica com o JSON
    que já estiver em disco, se houver, e devolve contagem None — quem chama
    decide o que fazer com a ausência, e a proveniência registra.
    """
    DATA_REAL.mkdir(parents=True, exist_ok=True)
    contagens = {}
    for serie in SERIES_SGS:
        alvo = DATA_REAL / f"bacen_sgs_{serie}.json"
        try:
            with urllib.request.urlopen(SGS_URL.format(serie=serie), timeout=60) as r:
                dados = json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"[DADOS] SGS {serie}: API inacessivel ({e}); "
                  f"{'usando cache em disco' if alvo.exists() else 'sem cache em disco'}")
            contagens[serie] = None
            continue
        alvo.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        contagens[serie] = len(dados)
        print(f"[DADOS] SGS {serie}: {len(dados)} observações -> {alvo.name}")
    return contagens


def baixar_ecommerce(destino: Path) -> str:
    """Baixa o xlsx da Fonte E (se ainda não estiver em `destino`). Devolve o SHA-256."""
    destino.mkdir(parents=True, exist_ok=True)
    alvo = destino / ARQUIVO_ECOMMERCE
    if not alvo.exists():
        print(f"[DADOS] baixando {ARQUIVO_ECOMMERCE} …")
        urllib.request.urlretrieve(ESPELHO_ECOMMERCE, alvo)
    sha = _sha256(alvo)
    print(f"[DADOS] {ARQUIVO_ECOMMERCE}: {alvo.stat().st_size} bytes | sha256 {sha[:16]}…")
    return sha


def montar_ecommerce(origem: Path) -> pd.DataFrame:
    """As 7 colunas usadas da Fonte E, sem `CustomerID`, na ordem original das linhas."""
    df = pd.read_excel(origem / ARQUIVO_ECOMMERCE, sheet_name=PLANILHA_ECOMMERCE)
    faltam = [c for c in COLUNAS_ECOMMERCE if c not in df.columns]
    if faltam:
        raise ValueError(f"Fonte E sem as colunas {faltam} — arquivo errado?")
    if len(df) != N_ECOMMERCE_ESPERADO:
        raise ValueError(f"Fonte E com {len(df)} linhas; esperado {N_ECOMMERCE_ESPERADO}")
    return df[list(COLUNAS_ECOMMERCE)].reset_index(drop=True)


def montar_populacao(origem: Path) -> pd.DataFrame:
    """Junta pagamentos + timestamp do pedido + UF do cliente."""
    pag = pd.read_csv(origem / "olist_order_payments_dataset.csv")
    ped = pd.read_csv(
        origem / "olist_orders_dataset.csv",
        usecols=["order_id", "customer_id", "order_purchase_timestamp"],
    )
    cli = pd.read_csv(
        origem / "olist_customers_dataset.csv",
        usecols=["customer_id", "customer_state"],
    )

    df = pag.merge(ped, on="order_id", how="inner").merge(cli, on="customer_id", how="inner")
    df["order_purchase_timestamp"] = pd.to_datetime(df["order_purchase_timestamp"])

    bruto = len(df)
    df = df[~df["payment_type"].isin(TIPOS_EXCLUIDOS)]
    df = df[df["payment_value"] > 0]
    print(f"[DADOS] população: {bruto} -> {len(df)} linhas após filtro "
          f"(tipo indefinido / valor zero)")

    # O identificador do cliente não entra na amostra: só a UF, que é agregada.
    return df.drop(columns=["customer_id"]).reset_index(drop=True)


def amostrar_estratificado(df: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """300 linhas estratificadas por payment_type × quartil de valor.

    Alocação **proporcional** ao tamanho de cada estrato na população, com
    arredondamento por maior resto. Proporcional é a escolha certa aqui porque
    a amostra tem que preservar o mix real de meio de pagamento — é uma das
    três marginais que ela existe para medir. Um desenho balanceado daria peso
    igual a `debit_card` (1,5% do real) e destruiria justamente o número que o
    TCC quer citar.
    """
    df = df.copy()
    df["faixa_valor"] = pd.qcut(df["payment_value"], q=4,
                                labels=["Q1", "Q2", "Q3", "Q4"])
    df["estrato"] = df["payment_type"].astype(str) + "|" + df["faixa_valor"].astype(str)

    tamanhos = df["estrato"].value_counts().sort_index()
    exato = tamanhos / tamanhos.sum() * n
    cotas = np.floor(exato).astype(int)

    # Maior resto, com desempate pelo nome do estrato — determinístico.
    faltam = n - int(cotas.sum())
    if faltam > 0:
        restos = (exato - cotas).sort_values(ascending=False, kind="mergesort")
        for estrato in restos.index[:faltam]:
            cotas[estrato] += 1

    partes = []
    for estrato in sorted(cotas.index):
        k = int(cotas[estrato])
        if k == 0:
            continue
        sub = df[df["estrato"] == estrato]
        # Seed derivada do nome do estrato: o sorteio de um estrato não muda
        # quando outro muda de tamanho.
        semente = seed + int(hashlib.md5(estrato.encode()).hexdigest(), 16) % 10_000
        partes.append(sub.sample(n=min(k, len(sub)), random_state=semente))

    amostra = pd.concat(partes, ignore_index=True)
    amostra = amostra.sort_values(["payment_type", "payment_value", "order_id"],
                                  kind="mergesort").reset_index(drop=True)

    ts = amostra["order_purchase_timestamp"]
    amostra["hour_of_day"] = ts.dt.hour
    amostra["day_of_week"] = ts.dt.dayofweek
    amostra["day_of_month"] = ts.dt.day
    return amostra


# ══════════════════════════════════════════════════════════════════════════
# CALIBRAÇÃO — dos doadores reais para os parâmetros do gerador
#
# Cada função abaixo devolve (parametros, notas): `parametros` é o bloco que
# `calibracao.parametros("sintetico_calibrado")` sobrepõe ao default, e
# `notas` explica de onde veio cada número — vai para o JSON, não para um
# comentário que ninguém lê.
# ══════════════════════════════════════════════════════════════════════════

def _histograma(valores: pd.Series, dominio: range, suavizacao: float = 0.5) -> list:
    """Frequência relativa em `dominio`, com suavização aditiva.

    Suavização de 0,5 por célula (Laplace/Jeffreys) para que um dia do mês
    ou uma hora ausentes nas 300 linhas não fiquem com probabilidade ZERO —
    300 linhas não sustentam a afirmação de que nunca se cobra às 4h.
    """
    contagem = valores.astype(int).value_counts()
    pesos = np.array([contagem.get(v, 0) + suavizacao for v in dominio], dtype=float)
    return (pesos / pesos.sum()).round(6).tolist()


def taxa_base_sgs_21084(contagem_baixada) -> dict:
    """Média histórica e último valor da série 21084 (Fonte B).

    Ordem de preferência: JSON em disco (baixado agora ou antes) > valores
    declarados no DATA_CARD. A origem usada fica no dicionário devolvido.
    """
    caminho = DATA_REAL / "bacen_sgs_21084.json"
    if caminho.exists():
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        serie = pd.to_numeric(pd.Series([d["valor"] for d in dados]), errors="coerce").dropna()
        if len(serie):
            return {
                "media": round(float(serie.mean()), 4),
                "ultimo": round(float(serie.iloc[-1]), 4),
                "n_obs": int(len(serie)),
                "origem": ("api_bcb (baixada nesta execucao)" if contagem_baixada
                           else "cache local (json de execucao anterior)"),
            }
    return {**SGS_21084_DECLARADO,
            "origem": "DECLARADO no DATA_CARD 3 — API e cache indisponiveis nesta execucao"}


def calibrar_classifier(amostra: pd.DataFrame, sgs: dict) -> tuple[dict, dict]:
    """Módulo 1: valor, sazonalidade (Fonte A) e peso de insufficient_funds (Fonte B)."""
    log_valor = np.log(amostra["payment_value"].to_numpy(dtype=float))
    mu, sigma = float(log_valor.mean()), float(log_valor.std(ddof=0))

    razao = sgs["ultimo"] / sgs["media"]
    base = list(_calib.PARAMETROS_DISTRIBUICAO["classifier"]["error_code_probs"])
    idx = _calib.CODIGOS_DE_ERRO.index("insufficient_funds")
    p_if = min(base[idx] * razao, 0.80)
    resto = 1.0 - p_if
    fator = resto / (1.0 - base[idx])
    probs = [round(p_if if i == idx else base[i] * fator, 4) for i in range(len(base))]
    probs[idx] = round(1.0 - sum(p for i, p in enumerate(probs) if i != idx), 4)

    parametros = {
        "invoice_lognormal": {"mean": round(mu, 4), "sigma": round(sigma, 4)},
        "invoice_clip": [float(amostra["payment_value"].min()),
                         float(amostra["payment_value"].max())],
        "day_of_month_hist": _histograma(amostra["day_of_month"], range(1, 32)),
        "hour_hist": _histograma(amostra["hour_of_day"], range(0, 24)),
        "day_of_week_hist": _histograma(amostra["day_of_week"], range(0, 7)),
        "error_code_probs": probs,
        "ruido_rotulo_sd": 0.05,
        "p_excecao_rotulo": 0.02,
    }
    notas = {
        "invoice_lognormal": "MLE de log(payment_value) nas 300 reais (Fonte A). "
                             "Mediana ~R$ 100: ticket de e-commerce, nao de SaaS B2B "
                             "(DATA_CARD 2.3).",
        "invoice_clip": "faixa observada nas 300 reais, no lugar de R$ 49,90-9.999,90",
        "day_of_month_hist": "histograma empirico do dia de order_purchase_timestamp, "
                             "suavizacao 0,5 por dia",
        "hour_hist": "histograma empirico da hora de compra (proxy: hora de compra em "
                     "e-commerce, nao hora de cobranca de assinatura)",
        "day_of_week_hist": "histograma empirico do dia da semana da compra (proxy)",
        "error_code_probs": f"fatia de insufficient_funds x {razao:.3f} "
                            f"(ultimo/media da SGS 21084: {sgs['ultimo']}/{sgs['media']}, "
                            f"origem: {sgs['origem']}); demais codigos renormalizados. "
                            f"Hipotese declarada no DATA_CARD 3.",
        "ruido_rotulo_sd": "N(0, 0,05) somado a p_recovery — o mesmo do default; nao e "
                           "calibravel (nao existe rotulo real de recuperacao)",
        "p_excecao_rotulo": "2% das linhas com rotulo sorteado ao acaso, ignorando a "
                            "regra. Pequeno de proposito: cada ponto custa AUC e o gate "
                            "G3 exige AUC >= 0,70; o objetivo e impedir que o modelo "
                            "decore a formula, nao apagar o sinal",
    }
    return parametros, notas


def calibrar_behavioral(ecom: pd.DataFrame) -> tuple[dict, dict]:
    """Módulo 2: as 4 features com doador na Fonte E; o resto fica como está."""
    P0 = _calib.PARAMETROS_DISTRIBUICAO["behavioral"]
    dias = ecom["DaySinceLastOrder"].dropna()
    horas = ecom["HourSpendOnApp"].dropna()
    sat = ecom["SatisfactionScore"].dropna()

    dias_saud = float(dias.mean())               # scale da exponencial = media
    deslocamento = (P0["anomalo"]["days_since_last_login_scale"]
                    - P0["saudavel"]["days_since_last_login_scale"])
    cv_horas = float(horas.std(ddof=0) / horas.mean())
    lam_saud = float(ecom.loc[ecom["Churn"] == 0, "Complain"].mean())
    lam_anom = float(ecom.loc[ecom["Churn"] == 1, "Complain"].mean())
    nps_loc_saud = float(sat.mean() * 2)          # 1-5 -> 0-10
    nps_scale = float(sat.std(ddof=0) * 2)
    razao_nps = P0["anomalo"]["nps"]["loc"] / P0["saudavel"]["nps"]["loc"]

    parametros = {
        "saudavel": {
            "days_since_last_login_scale": round(dias_saud, 4),
            "avg_session": {"loc": P0["saudavel"]["avg_session"]["loc"],
                            "scale": round(P0["saudavel"]["avg_session"]["loc"] * cv_horas, 4)},
            "tickets_lam": round(lam_saud, 4),
            "nps": {"loc": round(nps_loc_saud, 4), "scale": round(nps_scale, 4)},
        },
        "anomalo": {
            "days_since_last_login_scale": round(dias_saud + deslocamento, 4),
            "avg_session": {"loc": P0["anomalo"]["avg_session"]["loc"],
                            "scale": round(P0["anomalo"]["avg_session"]["loc"] * cv_horas, 4)},
            "tickets_lam": round(lam_anom, 4),
            "nps": {"loc": round(nps_loc_saud * razao_nps, 4), "scale": round(nps_scale, 4)},
        },
        "p_excecao_anomalo": 0.15,
        "p_excecao_saudavel": 0.05,
    }
    notas = {
        "days_since_last_login": f"saudavel: media de DaySinceLastOrder ({dias_saud:.3f} dias, "
                                 f"n={len(dias)}) como scale da exponencial. Anomalo: saudavel "
                                 f"+ {deslocamento:g} dias (o deslocamento que o gerador "
                                 f"default ja tinha). O corte por Churn do doador NAO foi "
                                 f"usado para separar as populacoes: nele quem cancela tem "
                                 f"MENOS dias desde o ultimo pedido, o inverso da hipotese "
                                 f"do gerador — declarado, nao escondido.",
        "avg_session_min": f"proxy fraco: HourSpendOnApp e horas/dia no app, nao minutos por "
                           f"sessao. Calibrado SO o coeficiente de variacao "
                           f"({cv_horas:.4f}); a escala (22 / 6 min) continua inventada.",
        "tickets_30d": f"proxy fraco: Complain e um flag 0/1, nao contagem de tickets. "
                       f"lambda da Poisson = P(Complain) por populacao do doador "
                       f"(Churn=0: {lam_saud:.4f}; Churn=1: {lam_anom:.4f}) — unica feature "
                       f"em que a direcao do doador coincide com a do gerador.",
        "nps_last": f"proxy fraco: SatisfactionScore 1-5 reescalado x2 para 0-10 "
                    f"(saudavel: media {nps_loc_saud:.3f}, sd {nps_scale:.3f}). Anomalo: "
                    f"media x {razao_nps:.4f} (razao do gerador default). No doador quem "
                    f"cancela e MAIS satisfeito em media — direcao inversa, nao usada.",
        "p_excecao_anomalo": "15% dos anomalos recebem 3 das 8 features comportamentais da "
                             "distribuicao saudavel (churn silencioso)",
        "p_excecao_saudavel": "5% dos saudaveis recebem 2 features da distribuicao anomala "
                              "(degradacao passageira)",
        "demais_features": "tenure_days, mrr_brl, seats, logins_7d, logins_30d, "
                           "feature_adoption, api_calls_7d, failed_pay_90d: sem doador, "
                           "literais de sempre",
    }
    return parametros, notas


def calibrar_voluntary(ecom: pd.DataFrame) -> tuple[dict, dict]:
    """risk_scorer voluntário: days_since_last e features_used_30d da Fonte E."""
    dias = ecom["DaySinceLastOrder"].dropna().astype(int).value_counts().sort_index()
    pedidos = ecom["OrderCount"].dropna().astype(int).value_counts().sort_index()
    parametros = {
        "days_since_last": {"tipo": "hist", "hist": {
            "valores": [int(v) for v in dias.index], "pesos": [int(c) for c in dias.values]}},
        "features_used_30d": {"tipo": "hist", "hist": {
            "valores": [int(v) for v in pedidos.index], "pesos": [int(c) for c in pedidos.values]}},
        "ruido_rotulo_sd": 0.08,
        "p_excecao_rotulo": 0.03,
    }
    notas = {
        "days_since_last": f"histograma empirico de DaySinceLastOrder (n={int(dias.sum())}, "
                           f"0-46 dias) — mesma unidade da feature",
        "features_used_30d": f"proxy fraco: OrderCount (pedidos acumulados, n={int(pedidos.sum())}) "
                             f"no lugar de features distintas usadas em 30 dias",
        "mrr": "sem doador: lognormal inventada (mean 7,5 / sigma 0,8, R$ 99-50.000)",
        "mix_eventos": "sem doador: 85% Session Started / 10% Downgrade / 5% Cancellation",
        "rotulo": "churn = Bernoulli(regras fixas do risk_scorer + N(0, 0,08)), com 3% de "
                  "excecoes ao acaso. Nao e churn observado: e o que as regras dizem, com "
                  "ruido suficiente para o modelo nao decorar a formula.",
    }
    return parametros, notas


def calibrar_liquidity() -> tuple[dict, dict]:
    """Módulo 3: sem doador. Só o anti-circularidade muda."""
    parametros = {
        "p_atraso_salario": 0.10,
        "atraso_salario_max_dias": 5,
        "p_gasto_imprevisto": 0.02,
        "gasto_imprevisto": {"min": 0.5, "max": 1.5},
    }
    notas = {
        "todas_as_features": "sem doador publico para saldo diario de cliente: perfis "
                             "CLT/PJ/freelancer, ancoras de payday e gasto diario continuam "
                             "inventados (DATA_CARD 6: 'nao calibravel')",
        "p_atraso_salario": "10% das entradas chegam 1-5 dias depois: a LSTM nao pode "
                            "aprender so o calendario",
        "p_gasto_imprevisto": "2% dos dias com gasto extra de 0,5-1,5 mensalidade",
    }
    return parametros, notas


def proveniencia_features(notas: dict) -> dict:
    """A lista feature a feature que `train()` devolve e o README_treino puxa.

    Status: "ancorada" (distribuição medida numa coluna real da mesma
    natureza), "proxy_fraco" (coluna real parecida, unidade/domínio diferem),
    "sintetica_sem_doador" (inventada — declarado, nunca omitido).
    """
    A, B, E = "A: Olist (300 transacoes)", "B: BACEN SGS 21084", "E: E-Commerce Customer Churn"

    def f(feature, status, fonte=None, coluna=None, nota=""):
        return {"feature": feature, "status": status, "fonte": fonte,
                "coluna": coluna, "nota": nota}

    n_cls = notas["classifier"]
    n_beh = notas["behavioral"]
    n_vol = notas["voluntary"]
    return {
        "FailureClassifier": [
            f("tenure_months", "sintetica_sem_doador", nota="mistura exponencial+uniforme a mao"),
            f("day_of_month", "ancorada", A, "order_purchase_timestamp.day", n_cls["day_of_month_hist"]),
            f("invoice_amount", "ancorada", A, "payment_value", n_cls["invoice_lognormal"]),
            f("avg_ticket", "proxy_fraco", A, "payment_value",
              "derivada de invoice_amount x U(0,85; 1,15); a relacao ticket/fatura e inventada"),
            f("gateway_error_code", "proxy_fraco", B, "serie 21084 (inadimplencia PF)",
              n_cls["error_code_probs"]),
            f("card_brand", "sintetica_sem_doador", nota="mix de bandeiras a mao"),
            f("payment_history_score", "sintetica_sem_doador", nota="beta(5,2) + tenure, a mao"),
            f("failure_count_90d", "sintetica_sem_doador", nota="poisson(1,5) a mao"),
            f("hour_of_day", "proxy_fraco", A, "order_purchase_timestamp.hour", n_cls["hour_hist"]),
            f("day_of_week", "proxy_fraco", A, "order_purchase_timestamp.dayofweek", n_cls["day_of_week_hist"]),
            f("attempt_count", "sintetica_sem_doador", nota="mix 45/30/15/10 a mao"),
            f("recovered (rotulo)", "sintetica_sem_doador",
              nota="modelo causal declarado (DATA_CARD 7) + N(0, 0,05) + Bernoulli + "
                   + n_cls["p_excecao_rotulo"]),
        ],
        "AnomalyDetector": [
            f("tenure_days", "sintetica_sem_doador", nota="gamma(2,5; 180) a mao"),
            f("mrr_brl", "sintetica_sem_doador", nota="lognormal(8,5; 0,7) a mao"),
            f("seats", "sintetica_sem_doador", nota="poisson(15) a mao"),
            f("logins_7d", "sintetica_sem_doador", nota="fracao de logins_30d a mao"),
            f("logins_30d", "sintetica_sem_doador", nota="normal(seats x 18 / x 5) a mao"),
            f("feature_adoption", "sintetica_sem_doador", nota="beta(5,2) / beta(2,5) a mao"),
            f("avg_session_min", "proxy_fraco", E, "HourSpendOnApp", n_beh["avg_session_min"]),
            f("api_calls_7d", "sintetica_sem_doador", nota="lognormal a mao"),
            f("days_since_last_login", "ancorada", E, "DaySinceLastOrder", n_beh["days_since_last_login"]),
            f("tickets_30d", "proxy_fraco", E, "Complain", n_beh["tickets_30d"]),
            f("failed_pay_90d", "sintetica_sem_doador", nota="binomial(3; 0,05 / 0,35) a mao"),
            f("nps_last", "proxy_fraco", E, "SatisfactionScore", n_beh["nps_last"]),
            f("is_anomalous (rotulo)", "sintetica_sem_doador",
              nota="populacao de origem; " + n_beh["p_excecao_anomalo"] + "; "
                   + n_beh["p_excecao_saudavel"]),
        ],
        "PaydayInference": [
            f("balance_norm", "sintetica_sem_doador", nota=notas["liquidity"]["todas_as_features"]),
            f("has_liquidity (rotulo)", "sintetica_sem_doador", nota="saldo >= 1 mensalidade; "
              + notas["liquidity"]["p_atraso_salario"] + "; " + notas["liquidity"]["p_gasto_imprevisto"]),
            f("day_of_month (sin/cos)", "sintetica_sem_doador", nota="calendario"),
            f("weekday", "sintetica_sem_doador", nota="calendario"),
            f("profile (CLT/PJ/freelancer)", "sintetica_sem_doador", nota="mix 50/30/20 a mao"),
        ],
        "risk_scorer_voluntario": [
            f("days_since_last", "ancorada", E, "DaySinceLastOrder", n_vol["days_since_last"]),
            f("features_used_30d", "proxy_fraco", E, "OrderCount", n_vol["features_used_30d"]),
            f("mrr", "sintetica_sem_doador", nota=n_vol["mrr"]),
            f("evento_cancelamento", "sintetica_sem_doador", nota=n_vol["mix_eventos"]),
            f("evento_downgrade", "sintetica_sem_doador", nota=n_vol["mix_eventos"]),
            f("evento_sessao", "sintetica_sem_doador", nota=n_vol["mix_eventos"]),
            f("churn (rotulo)", "sintetica_sem_doador", nota=n_vol["rotulo"]),
        ],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prepara a âncora real da CRAI")
    ap.add_argument("--origem", type=Path, default=DATA_REAL / "_olist_bruto",
                    help="diretório onde os CSVs da Fonte A ficam (baixa se faltar)")
    ap.add_argument("--origem-ecommerce", type=Path, default=DATA_REAL / "_ecommerce_bruto",
                    help="diretório onde o xlsx da Fonte E fica (baixa se faltar)")
    ap.add_argument("--sem-rede", action="store_true",
                    help="não baixa nada; usa o que já está em disco")
    args = ap.parse_args(argv)

    DATA_REAL.mkdir(parents=True, exist_ok=True)

    if args.sem_rede:
        hashes = {n: _sha256(args.origem / n) for n in ARQUIVOS_OLIST}
        contagens_sgs = {}
        sha_ecom = _sha256(args.origem_ecommerce / ARQUIVO_ECOMMERCE)
    else:
        hashes = baixar_olist(args.origem)
        contagens_sgs = baixar_sgs()
        sha_ecom = baixar_ecommerce(args.origem_ecommerce)

    populacao = montar_populacao(args.origem)
    amostra = amostrar_estratificado(populacao, N_AMOSTRA, SEED_AMOSTRA)

    destino = DATA_REAL / "amostra_300.csv"
    amostra.to_csv(destino, index=False, encoding="utf-8")
    sha_amostra = _sha256(destino)

    ecom = montar_ecommerce(args.origem_ecommerce)
    destino_ecom = DATA_REAL / "ecommerce_churn.csv"
    ecom.to_csv(destino_ecom, index=False, encoding="utf-8")
    sha_ecom_csv = _sha256(destino_ecom)

    # ── Calibração ──────────────────────────────────────────────────
    sgs = taxa_base_sgs_21084(contagens_sgs.get(21084))
    p_cls, n_cls = calibrar_classifier(amostra, sgs)
    p_beh, n_beh = calibrar_behavioral(ecom)
    p_vol, n_vol = calibrar_voluntary(ecom)
    p_liq, n_liq = calibrar_liquidity()
    notas = {"classifier": n_cls, "behavioral": n_beh, "voluntary": n_vol, "liquidity": n_liq}
    features = proveniencia_features(notas)

    fontes = {
        "fonte_a": {
            "nome": "Brazilian E-Commerce Public Dataset by Olist",
            "licenca": "CC BY-NC-SA 4.0 (nao comercial)",
            "origem_canonica": "kaggle.com/datasets/olistbr/brazilian-ecommerce",
            "espelho_usado": ESPELHO_OLIST,
            "sha256_arquivos": hashes,
            "linhas_populacao_filtrada": int(len(populacao)),
            "periodo": [str(populacao["order_purchase_timestamp"].min()),
                        str(populacao["order_purchase_timestamp"].max())],
        },
        "fonte_b": {
            "nome": "BACEN SGS",
            "licenca": "ODbL",
            "series": {str(s): contagens_sgs.get(s) for s in SERIES_SGS},
            "serie_21084_usada": sgs,
        },
        "fonte_e": {
            "nome": "Ecommerce Customer Churn Analysis and Prediction",
            "licenca": "nao declarada na pagina do Kaggle — uso academico apenas, "
                       "bruto fora do git",
            "origem_canonica": "kaggle.com/datasets/ankitverma2010/"
                               "ecommerce-customer-churn-analysis-and-prediction",
            "espelho_usado": ESPELHO_ECOMMERCE,
            "sha256_arquivo_bruto": sha_ecom,
            "n_linhas": int(len(ecom)),
            "colunas_usadas": list(COLUNAS_ECOMMERCE),
            "nulos_por_coluna": {c: int(ecom[c].isna().sum()) for c in COLUNAS_ECOMMERCE},
            "taxa_churn": round(float(ecom["Churn"].mean()), 4),
            "describe": {c: {k: round(float(v), 4) for k, v in ecom[c].describe().items()}
                         for c in COLUNAS_ECOMMERCE if c != "Churn"},
            "arquivo_derivado": {"nome": destino_ecom.name, "sha256": sha_ecom_csv},
            "papel_do_churn": "NAO entra em parametro nem em treino; so na checagem "
                              "fora do dominio (scripts/sanity_check_fora_do_dominio.py)",
        },
    }

    proveniencia = {
        "gerado_por": "crai/scripts/preparar_amostra_real.py",
        "gerado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seed": SEED_AMOSTRA,
        "n_amostra": int(len(amostra)),
        **fontes,
        "amostra": {
            "arquivo": destino.name,
            "sha256": sha_amostra,
            "mix_payment_type": amostra["payment_type"].value_counts().to_dict(),
            "valor": {
                "min": float(amostra["payment_value"].min()),
                "media": round(float(amostra["payment_value"].mean()), 2),
                "mediana": float(amostra["payment_value"].median()),
                "max": float(amostra["payment_value"].max()),
            },
        },
        "proveniencia_features": features,
    }
    (DATA_REAL / "PROVENIENCIA.json").write_text(
        json.dumps(proveniencia, ensure_ascii=False, indent=2), encoding="utf-8")

    calibracao = {
        "gerado_por": "crai/scripts/preparar_amostra_real.py",
        "gerado_em": proveniencia["gerado_em"],
        "calibrado": True,
        "fontes": {
            "A": {"nome": fontes["fonte_a"]["nome"], "amostra_sha256": sha_amostra,
                  "n": int(len(amostra))},
            "B": {"nome": "BACEN SGS 21084", **sgs},
            "E": {"nome": fontes["fonte_e"]["nome"], "sha256_bruto": sha_ecom,
                  "n": int(len(ecom))},
        },
        "parametros": {"classifier": p_cls, "behavioral": p_beh,
                       "liquidity": p_liq, "voluntary": p_vol},
        "notas": notas,
        "proveniencia_features": features,
    }
    CALIBRACAO_PATH.parent.mkdir(parents=True, exist_ok=True)
    CALIBRACAO_PATH.write_text(
        json.dumps(calibracao, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[DADOS] amostra: {len(amostra)} linhas -> {destino}")
    print(f"[DADOS] sha256: {sha_amostra}")
    print(f"[DADOS] mix: {proveniencia['amostra']['mix_payment_type']}")
    print(f"[DADOS] fonte E: {len(ecom)} linhas -> {destino_ecom} (churn {fontes['fonte_e']['taxa_churn']:.1%})")
    print(f"[DADOS] SGS 21084: media {sgs['media']} | ultimo {sgs['ultimo']} | {sgs['origem']}")
    print(f"[CALIBRACAO] parametros medidos -> {CALIBRACAO_PATH}")
    for modelo, linhas in features.items():
        por_status = {}
        for l in linhas:
            por_status[l["status"]] = por_status.get(l["status"], 0) + 1
        print(f"[CALIBRACAO]   {modelo:24s}: {por_status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

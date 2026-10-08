"""
crai/scripts/treinar_classificador_v4.py — Treino e medições do classificador na base v4.

    python -m crai.scripts.treinar_classificador_v4 --base data/v4/ --v2 data/v2/ --out models/v4/

Treina o ALGORITMO de produção (`FailureClassifier`: XGBoost 0,7 + Random Forest 0,3,
mesmos hiperparâmetros, mesma codificação) na base v4 (`crai.ml.classificador_v4`, rótulo
por mecanismo) e mede. NADA é promovido: os artefatos vão para `models/v4/`, com nomes
que o `FailureClassifier.load()` de produção não lê.

Três cenários de features:

    A  o contrato de hoje: as 11 features de `ALL_FEATURES_V2`
    B  as 11, mais o perfil do pagador e a razão fatura / ticket médio (o que o backend
       conseguiria fornecer com uma mudança pequena em quem monta a entrada)
    C  as de B, mais a previsão de liquidez do Módulo 3 para os dias das novas tentativas.
       EXPLORATÓRIO: entrou depois de ver A e B, e não faz parte do critério declarado.
       Supõe que o histórico de saldo do cliente chega ao sistema (gateway ou open
       finance), que é a premissa do próprio Módulo 3. Só roda com o modelo de liquidez
       de produção em `models/`.

Grava em `--out`:

    failure_classifier_v4_xgb.joblib, _rf.joblib, _encoders.joblib, _features.joblib
    failure_classifier_v4_meta.json      features na ordem, versões, semente, proveniência
    train_metrics_v4.json                todas as medições (ver `treinar`)

Desenho e critérios: `docs/evidencia_v4/DESENHO.md` (08/10/2026), escritos antes de
medir. O holdout é o mesmo da v2 (por cliente, 20%, semente 42): são as mesmas 24.089
cobranças, com o rótulo novo. O limiar sai das predições fora-da-dobra do treino, nunca
do teste.
"""

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, confusion_matrix, roc_auc_score
from sklearn.model_selection import GroupKFold

from ..ml import calibracao as _calibracao
from ..ml import classificador_v4 as V4
from ..ml import failure_classifier as fc
from ..ml.synthetic_data import _business_day_index
from ..ml.split import conferir_sem_vazamento, split_por_cliente
from .gerar_bases_v2 import _versoes

SEED = 42
TEST_SIZE = 0.2
N_FOLDS = 5
SEMENTES_PERMUTACAO = (0, 1, 2)
NOME = "failure_classifier_v4"
PESO_XGB, PESO_RF = 0.7, 0.3
RECALL_MINIMO = fc.RECALL_MINIMO
GRADE_LIMIARES = [round(x, 2) for x in np.arange(0.01, 1.0, 0.01)]
RECALLS_FIXOS = (0.938, 0.90, 0.80, 0.70, 0.50)
# Faixas do score: os 30% de cima, os 50% do meio e os 20% de baixo (as mesmas proporções
# usadas na leitura da v2), com os cortes tirados do treino.
FAIXAS = (("alta", 0.70, 1.00), ("media", 0.20, 0.70), ("baixa", 0.00, 0.20))

FEATURES_A = list(fc.ALL_FEATURES_V2)
EXTRAS_B = ["perfil_pagador_cod", "razao_fatura_ticket"]
FEATURES_B = FEATURES_A + EXTRAS_B
EXTRAS_C = ["liquidez_prevista_d2", "liquidez_prevista_d4", "liquidez_prevista_d6",
            "liquidez_prevista_max_na_janela"]
FEATURES_C = FEATURES_B + EXTRAS_C
PERFIS = {"CLT": 0, "PJ": 1, "freelancer": 2}

# A referência da v2 usada no CRITÉRIO (holdout por cliente, medições de 07/10/2026 no
# artefato de produção): é o número que estava escrito antes do treino. Quando o artefato
# de produção e a base v2 estão na máquina, `medir_v2` mede a v2 de novo com as mesmas
# funções deste script, e é essa medição que vai para as tabelas.
REFERENCIA_V2 = {
    "auc": 0.7096, "limiar": 0.25,
    "matriz_holdout": {"tn": 3676, "fp": 10657, "fn": 606, "tp": 9150},
    "precisao_em_recall_fixo": {"0.938": 0.4620, "0.9": 0.4768, "0.8": 0.5143,
                                "0.7": 0.5477, "0.5": 0.5999},
}
CRITERIO = ("A base nova resolve o problema declarado se, no holdout, com recall >= 0,90, a "
            "precisao do cenario A passar a da v2 (0,4768 com recall de 0,90) por mais que o "
            "desvio da precisao entre as dobras do GroupKFold. Escrito antes de medir. "
            "Satisfazer o criterio NAO promove nada.")


def _dizer(texto: str) -> None:
    print(f"[TREINO-V4] {texto}", flush=True)


def _r(valor, casas: int = 4):
    return None if valor is None else round(float(valor), casas)


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def ler_base(pasta: Path, pasta_v2: Path) -> tuple:
    """`(df com as colunas extras do cenário B, manifesto)`. O arquivo é conferido contra
    o `MANIFESTO_v4.json`: uma base editada à mão reprova aqui."""
    manifesto = json.loads((pasta / "MANIFESTO_v4.json").read_text(encoding="utf-8"))
    meta = manifesto["arquivos"]["classificador_v4"]
    caminho = pasta / meta["arquivo"]
    medido = _sha256(caminho)
    if medido != meta["sha256"]:
        raise SystemExit(f"[TREINO-V4] {caminho} nao confere com o MANIFESTO_v4.json "
                         f"(medido {medido[:16]}, declarado {meta['sha256'][:16]}).")
    df = pd.read_parquet(caminho)
    pop = pd.read_parquet(pasta_v2 / "populacao.parquet", columns=["customer_id", "billing_profile"])
    perfil = pop.set_index("customer_id")["billing_profile"].loc[df["customer_id"]].to_numpy()
    df["perfil_pagador_cod"] = pd.Series(perfil).map(PERFIS).to_numpy(dtype="int64")
    df["razao_fatura_ticket"] = (df["invoice_amount"] / df["avg_ticket"].clip(lower=0.01)).round(4)
    return df, manifesto


def janelas_antes_da_cobranca(feats: np.ndarray, dias: np.ndarray, janela: int) -> np.ndarray:
    """Para cada cobrança no dia `d`, as `janela` linhas de `feats` ANTERIORES a ela
    (`feats[d - janela:d]`): o que se sabia até a véspera. Onde a série ainda não tinha
    começado, a janela entra zerada, como um cliente com pouco histórico."""
    saida = np.zeros((len(dias), janela, feats.shape[1]), dtype=np.float32)
    for k, d in enumerate(dias):
        de = max(0, int(d) - janela)
        trecho = feats[de:int(d)]
        if len(trecho):
            saida[k, janela - len(trecho):] = trecho
    return saida


def previsao_de_liquidez(df: pd.DataFrame, pasta_v2: Path, seed: int, fonte: str):
    """As quatro colunas do cenário C, ou `(None, motivo)` sem o modelo de liquidez.

    Para cada cobrança, o Módulo 3 de PRODUÇÃO (LSTM 0,6 + Prophet 0,4) recebe os 30 dias
    de saldo do cliente até a véspera e prevê os 14 dias seguintes; ficam a previsão para
    D+2, D+4 e D+6 e a maior da janela de 7 dias. O saldo é o da simulação de caixa do
    v4, a mesma que decide o rótulo: a previsão vê o PASSADO do caixa, o rótulo depende do
    FUTURO dele."""
    from ..ml import payday_inference as pi
    if not (pi.TORCH_AVAILABLE and pi.PROPHET_AVAILABLE):
        return None, "torch ou prophet nao instalados"
    liquidez = pi.PaydayInference()
    if not liquidez.load():
        return None, "modelo de liquidez de producao ausente em models/"
    import torch

    P = _calibracao.parametros(fonte)["liquidity"]
    pop = pd.read_parquet(pasta_v2 / "populacao.parquet", columns=["customer_id", "billing_profile"])
    perfil_de = pop.set_index("customer_id")["billing_profile"]
    datas = V4.datas_do_caixa(df)
    bday_idx = _business_day_index(datas)
    dia = (pd.to_datetime(df["data_cobranca"]).dt.normalize() - datas[0]).dt.days.to_numpy()
    dia_do_mes, util = datas.day.to_numpy(), (datas.dayofweek.to_numpy() < 5).astype(np.float32)
    seno = np.sin(2 * np.pi * dia_do_mes / 31).astype(np.float32)
    cosseno = np.cos(2 * np.pi * dia_do_mes / 31).astype(np.float32)
    # O prior sazonal do perfil, para todas as datas de uma vez (com a folga do horizonte).
    todas = pd.date_range(datas[0], datas[-1] + pd.Timedelta(days=pi.HORIZON), freq="D")
    prior = {p: liquidez._prior_por_data(p, todas).to_numpy() for p in pi.PROFILES}

    n = len(df)
    previsao = np.zeros((n, pi.HORIZON), dtype=np.float32)
    ids = df["customer_id"].to_numpy()
    posicoes = pd.Series(np.arange(n)).groupby(ids, sort=False).indices
    lote_X, lote_linhas, lote_perfil = [], [], []

    def esvaziar():
        if not lote_X:
            return
        X = torch.from_numpy(np.concatenate(lote_X))
        with torch.no_grad():
            p_lstm = torch.sigmoid(liquidez.model(X)).numpy()
        linhas = np.concatenate(lote_linhas)
        perfis = np.concatenate(lote_perfil)
        for p in pi.PROFILES:
            m = perfis == p
            if m.any():
                horizonte = dia[linhas[m]][:, None] + np.arange(pi.HORIZON)[None, :]
                previsao[linhas[m]] = (pi.PESO_LSTM * p_lstm[m]
                                       + (1 - pi.PESO_LSTM) * prior[p][horizonte])
        lote_X.clear(), lote_linhas.clear(), lote_perfil.clear()

    for cid, linhas in posicoes.items():
        perfil = str(perfil_de.loc[cid])
        saldo = V4.caixa_do_cliente(cid, perfil, datas, bday_idx, seed, P)
        feats = np.stack([(saldo >= 1.0).astype(np.float32), np.clip(saldo, 0, 5).astype(np.float32),
                          seno, cosseno, util], axis=1)
        lote_X.append(janelas_antes_da_cobranca(feats, dia[linhas], pi.WINDOW))
        lote_linhas.append(np.asarray(linhas))
        lote_perfil.append(np.full(len(linhas), perfil))
        if sum(len(x) for x in lote_X) >= 8192:
            esvaziar()
    esvaziar()

    colunas = pd.DataFrame({
        "liquidez_prevista_d2": previsao[:, 2], "liquidez_prevista_d4": previsao[:, 4],
        "liquidez_prevista_d6": previsao[:, 6],
        "liquidez_prevista_max_na_janela": previsao[:, 1:1 + V4.JANELA_BACEN_DIAS].max(axis=1),
    }, index=df.index).round(4)
    return colunas, None


def matriz(df: pd.DataFrame, features: list) -> tuple:
    """`(codificador, X)` na codificação do modelo de produção, pelo código DELE. A guarda
    das ocultas roda antes: com `oculto_` ou latente na lista, o treino aborta."""
    V4.conferir_features(features)
    codificador = fc.FailureClassifier()
    X, _ = codificador._prepare_features(df, list(features))
    return codificador, X


def ajustar(X: np.ndarray, y: np.ndarray) -> "fc.FailureClassifier":
    """O algoritmo de produção (mesmas classes e hiperparâmetros), treinado nas linhas
    passadas. Nada é gravado."""
    modelo = fc.FailureClassifier()
    modelo.xgb.fit(X, y, verbose=False)
    modelo.rf.fit(X, y)
    return modelo


def prever(modelo: "fc.FailureClassifier", X: np.ndarray) -> np.ndarray:
    return PESO_XGB * modelo.xgb.predict_proba(X)[:, 1] + PESO_RF * modelo.rf.predict_proba(X)[:, 1]


def _auc(y, s):
    y = np.asarray(y)
    return _r(roc_auc_score(y, s)) if len(np.unique(y)) > 1 else None


def escolher_limiar(y, s, recall_minimo: float = RECALL_MINIMO):
    """O maior limiar da grade com recall >= `recall_minimo`, ou None."""
    y, s = np.asarray(y), np.asarray(s)
    positivos = max(int((y == 1).sum()), 1)
    aprovados = [t for t in GRADE_LIMIARES if ((s >= t) & (y == 1)).sum() / positivos >= recall_minimo]
    return max(aprovados) if aprovados else None


def no_limiar(y, s, limiar) -> dict:
    """A matriz de confusão e o que sai dela, num limiar."""
    if limiar is None:
        return {"limiar": None}
    y = np.asarray(y)
    pred = (np.asarray(s) >= limiar).astype(int)
    tn, fp, fn, tp = (int(v) for v in confusion_matrix(y, pred, labels=[0, 1]).ravel())
    return {
        "limiar": float(limiar),
        "matriz": {"tn": tn, "fp": fp, "fn": fn, "tp": tp},
        "recall": _r(tp / max(tp + fn, 1)),
        "precisao": _r(tp / max(tp + fp, 1)),
        "especificidade": _r(tn / max(tn + fp, 1)),
        "acuracia": _r((tp + tn) / max(len(y), 1)),
        "fracao_marcada": _r(pred.mean()),
        "recuperaveis_perdidos": fn,
    }


def precisao_em_recall(y, s, recall_alvo: float) -> dict:
    """O que acontece ao marcar, do maior score para o menor, até alcançar `recall_alvo`.

    A precisão depende da taxa de base (com menos recuperáveis ela cai com o mesmo
    modelo); a taxa de falsos positivos (dos que NÃO recuperam, quantos foram marcados)
    não depende, e é ela que compara duas bases. Scores empatados são cortados pela
    ordem das linhas."""
    y, s = np.asarray(y), np.asarray(s)
    ordem = np.argsort(-s, kind="stable")
    acertos = np.cumsum(y[ordem])
    k = int(np.searchsorted(acertos, recall_alvo * y.sum()))
    k = min(k, len(y) - 1)
    falsos = int((k + 1) - acertos[k])
    negativos = max(int(len(y) - y.sum()), 1)
    precisao = acertos[k] / (k + 1)
    return {"precisao": _r(precisao), "falsos_positivos": falsos,
            "taxa_de_falsos_positivos": _r(falsos / negativos),
            "ganho_sobre_a_taxa_de_base": _r(precisao / max(y.mean(), 1e-9)),
            "fracao_marcada": _r((k + 1) / len(y))}


def erro_de_calibracao(y, s, faixas: int = 10) -> float:
    y, s = np.asarray(y, dtype=float), np.asarray(s, dtype=float)
    cortes = np.linspace(0.0, 1.0, faixas + 1)
    faixa = np.clip(np.digitize(s, cortes[1:-1]), 0, faixas - 1)
    total = 0.0
    for i in range(faixas):
        dentro = faixa == i
        if dentro.any():
            total += dentro.mean() * abs(s[dentro].mean() - y[dentro].mean())
    return float(total)


def faixas_do_score(y_te, s_te, s_referencia) -> list:
    """A taxa real de recuperação em cada faixa do score. Os cortes são quantis do score
    FORA-DA-DOBRA do treino; o teste só é medido."""
    y_te, s_te = np.asarray(y_te), np.asarray(s_te)
    saida = []
    for nome, q_de, q_ate in FAIXAS:
        de = float(np.quantile(s_referencia, q_de)) if q_de > 0 else -np.inf
        ate = float(np.quantile(s_referencia, q_ate)) if q_ate < 1 else np.inf
        dentro = (s_te >= de) & (s_te < ate)
        saida.append({"faixa": nome, "score_de": None if q_de == 0 else _r(de),
                      "score_ate": None if q_ate == 1 else _r(ate),
                      "fracao_das_cobrancas": _r(dentro.mean()),
                      "taxa_de_recuperacao": _r(y_te[dentro].mean()) if dentro.any() else None})
    return saida


def validacao_cruzada(X: np.ndarray, y: np.ndarray, grupos: np.ndarray) -> dict:
    """GroupKFold por cliente sobre o TREINO. Devolve as predições fora-da-dobra, a AUC
    e a precisão com recall de 0,90 em cada dobra."""
    oof = np.full(len(y), np.nan)
    aucs, precisoes = [], []
    for tr, va in GroupKFold(n_splits=N_FOLDS).split(X, y, grupos):
        conferir_sem_vazamento(grupos, tr, va)
        s = prever(ajustar(X[tr], y[tr]), X[va])
        oof[va] = s
        aucs.append(float(roc_auc_score(y[va], s)))
        precisoes.append(precisao_em_recall(y[va], s, RECALL_MINIMO)["precisao"])
    desvio = lambda v: float(np.std(v, ddof=1)) if len(v) > 1 else 0.0       # noqa: E731
    return {"oof": oof,
            "auc": {"por_dobra": [_r(a) for a in aucs], "media": _r(np.mean(aucs)),
                    "desvio": _r(desvio(aucs))},
            "precisao_em_recall_0_90": {"por_dobra": precisoes, "media": _r(np.mean(precisoes)),
                                        "desvio": _r(desvio(precisoes))}}


def por_grupo(df_te: pd.DataFrame, y_te, s_te, limiar, coluna: str) -> dict:
    saida = {}
    for valor, linhas in df_te.groupby(coluna, sort=True).indices.items():
        m = no_limiar(np.asarray(y_te)[linhas], np.asarray(s_te)[linhas], limiar)
        saida[str(valor)] = {"cobrancas": int(len(linhas)),
                             "taxa_real": _r(np.asarray(y_te)[linhas].mean()),
                             "recall": m["recall"], "precisao": m["precisao"],
                             "fracao_marcada": m["fracao_marcada"]}
    return saida


def medir_cenario(df: pd.DataFrame, features: list, idx_tr, idx_te, completo: bool) -> tuple:
    """Todas as medições de um cenário de features. Com `completo`, entram a permutação
    do rótulo e a ablação (o cenário A); sem, só o que compara os cenários. Devolve
    `(medições, modelo, score no holdout)`."""
    y = df["recovered"].to_numpy(dtype=int)
    grupos = df["customer_id"].to_numpy()
    _, X = matriz(df, features)
    tempos = {}

    t = time.perf_counter()
    modelo = ajustar(X[idx_tr], y[idx_tr])
    tempos["treino_principal_s"] = round(time.perf_counter() - t, 2)
    s_te = prever(modelo, X[idx_te])
    y_te = y[idx_te]

    t = time.perf_counter()
    cv = validacao_cruzada(X[idx_tr], y[idx_tr], grupos[idx_tr])
    tempos["groupkfold_s"] = round(time.perf_counter() - t, 2)
    oof = cv.pop("oof")
    limiar = escolher_limiar(y[idx_tr], oof)

    saida = {
        "features": list(features),
        "n_features": len(features),
        "holdout": {
            "auc": _auc(y_te, s_te),
            "brier": _r(brier_score_loss(y_te, s_te)),
            "erro_de_calibracao": _r(erro_de_calibracao(y_te, s_te)),
            "auc_no_treino": _auc(y[idx_tr], prever(modelo, X[idx_tr])),
        },
        "groupkfold": cv,
        "limiar": {
            "regra": f"maior limiar da grade (0,01) com recall >= {RECALL_MINIMO} nas predicoes "
                     "fora-da-dobra do GroupKFold do treino",
            "escolhido": limiar,
            "no_limiar_escolhido": no_limiar(y_te, s_te, limiar),
            "no_limiar_em_uso_hoje": no_limiar(y_te, s_te, fc.LIMIAR_CLASSIFICACAO),
            "no_limiar_0_50": no_limiar(y_te, s_te, 0.50),
        },
        "precisao_em_recall_fixo": {str(r): precisao_em_recall(y_te, s_te, r) for r in RECALLS_FIXOS},
        "faixas_do_score": faixas_do_score(y_te, s_te, oof),
        "curva_de_limiares": [no_limiar(y_te, s_te, t) for t in
                              (0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60, 0.70)],
    }
    if limiar is not None:
        te = df.iloc[idx_te].reset_index(drop=True)
        saida["por_causa"] = por_grupo(te, y_te, s_te, limiar, "gateway_error_code")
        saida["por_metodo"] = por_grupo(te, y_te, s_te, limiar, "metodo_pagamento")

    if completo:
        t = time.perf_counter()
        permutacao = []
        for semente in SEMENTES_PERMUTACAO:
            embaralhado = np.random.default_rng(semente).permutation(y[idx_tr])
            permutacao.append(_auc(y_te, prever(ajustar(X[idx_tr], embaralhado), X[idx_te])))
        saida["permutacao_do_rotulo"] = {"sementes": list(SEMENTES_PERMUTACAO), "auc_holdout": permutacao}
        tempos["permutacao_s"] = round(time.perf_counter() - t, 2)

        t = time.perf_counter()
        ablacao = {}
        for f in features:
            sem = [c for c in features if c != f]
            _, Xs = matriz(df, sem)
            auc_sem = _auc(y_te, prever(ajustar(Xs[idx_tr], y[idx_tr]), Xs[idx_te]))
            ablacao[f] = {"auc_sem_ela": auc_sem, "queda": _r(saida["holdout"]["auc"] - auc_sem)}
        saida["ablacao"] = ablacao
        tempos["ablacao_s"] = round(time.perf_counter() - t, 2)

    saida["tempos_s"] = tempos
    return saida, modelo, s_te


def modelo_de_producao_na_v4(df: pd.DataFrame, idx_te) -> dict:
    """O artefato de produção de hoje (treinado na v2), aplicado ao holdout da v4 sem
    retreinar. Sem o artefato em `models/`, devolve o motivo."""
    atual = fc.FailureClassifier()
    if not atual.load():
        return {"medido": False, "motivo": "artefato de producao ausente em models/"}
    if list(atual.feature_names) != FEATURES_A:
        return {"medido": False, "motivo": f"o artefato declara outras features: {atual.feature_names}"}
    X, y = atual._prepare_features(df.iloc[idx_te], FEATURES_A)
    s = prever(atual, X)
    return {"medido": True, "auc": _auc(y, s),
            "no_limiar_em_uso_hoje": no_limiar(y, s, fc.LIMIAR_CLASSIFICACAO),
            "precisao_em_recall_fixo": {str(r): precisao_em_recall(y, s, r) for r in RECALLS_FIXOS}}


def medir_v2(pasta_v2: Path, clientes: set, completo: bool) -> dict:
    """A v2, medida AQUI com as mesmas funções: o artefato de produção no holdout da v2 (as
    mesmas cobranças do holdout da v4, com o rótulo antigo). Com `completo`, entra a
    validação cruzada do algoritmo de produção no treino da v2, que dá os cortes das
    faixas pelo mesmo método da v4. Sem o artefato, devolve o motivo."""
    atual = fc.FailureClassifier()
    if not atual.load():
        return {"medido": False, "motivo": "artefato de producao ausente em models/"}
    if list(atual.feature_names) != FEATURES_A:
        return {"medido": False, "motivo": f"o artefato declara outras features: {atual.feature_names}"}
    v2 = pd.read_parquet(pasta_v2 / "classificador.parquet")
    v2 = v2[v2["customer_id"].isin(clientes)].reset_index(drop=True)
    grupos = v2["customer_id"].to_numpy()
    idx_tr, idx_te = split_por_cliente(grupos, test_size=TEST_SIZE, seed=SEED)
    X, y = atual._prepare_features(v2, FEATURES_A)
    y = np.asarray(y, dtype=int)
    s_te, y_te = prever(atual, X[idx_te]), y[idx_te]
    saida = {
        "medido": True,
        "o_que_e": "o artefato de producao (treinado na v2) no holdout por cliente da v2",
        "linhas_teste": int(len(idx_te)),
        "taxa_recuperacao_teste": _r(y_te.mean()),
        "auc": _auc(y_te, s_te),
        "no_limiar_em_uso_hoje": no_limiar(y_te, s_te, fc.LIMIAR_CLASSIFICACAO),
        "precisao_em_recall_fixo": {str(r): precisao_em_recall(y_te, s_te, r) for r in RECALLS_FIXOS},
    }
    if completo:
        cv = validacao_cruzada(X[idx_tr], y[idx_tr], grupos[idx_tr])
        oof = cv.pop("oof")
        saida["groupkfold_do_algoritmo_no_treino_da_v2"] = cv
        saida["faixas_do_score"] = faixas_do_score(y_te, s_te, oof)
    return saida


def sem_a_taxa_de_base(v2: dict, cenario_a: dict) -> dict:
    """A comparação entre as duas bases que não depende de quantos recuperam em cada uma."""
    if not v2.get("medido"):
        return {"medido": False, "motivo": v2.get("motivo")}
    a, b = v2["precisao_em_recall_fixo"]["0.9"], cenario_a["precisao_em_recall_fixo"]["0.9"]
    taxa_v2 = v2["taxa_recuperacao_teste"]
    na_taxa_da_v2 = 0.9 * taxa_v2 / (0.9 * taxa_v2 + b["taxa_de_falsos_positivos"] * (1 - taxa_v2))
    return {
        "medido": True,
        "recall": 0.9,
        "taxa_de_falsos_positivos_v2": a["taxa_de_falsos_positivos"],
        "taxa_de_falsos_positivos_v4_a": b["taxa_de_falsos_positivos"],
        "ganho_sobre_a_taxa_de_base_v2": a["ganho_sobre_a_taxa_de_base"],
        "ganho_sobre_a_taxa_de_base_v4_a": b["ganho_sobre_a_taxa_de_base"],
        "precisao_do_v4_a_se_a_taxa_de_base_fosse_a_da_v2": _r(na_taxa_da_v2),
        "leitura": "o criterio declarado compara precisoes entre bases com taxas de recuperacao "
                   "diferentes; sem esse efeito, o cenario A marca menos nao-recuperaveis que a v2",
    }


def treinar(base: Path, pasta_v2: Path, out: Path, amostra_clientes: int = None,
            completo: bool = True, gravar: bool = True, com_liquidez: bool = True) -> dict:
    inicio = time.perf_counter()
    _dizer(f"Criterio, declarado antes do treino: {CRITERIO}")
    df, manifesto = ler_base(Path(base), Path(pasta_v2))
    if amostra_clientes:
        escolhidos = sorted(df["customer_id"].unique())[:int(amostra_clientes)]
        df = df[df["customer_id"].isin(escolhidos)].reset_index(drop=True)
    faltam = [f for f in FEATURES_B if f not in df.columns]
    if faltam:
        raise SystemExit(f"[TREINO-V4] features ausentes na base: {faltam}")

    y = df["recovered"].to_numpy(dtype=int)
    grupos = df["customer_id"].to_numpy()
    idx_tr, idx_te = split_por_cliente(grupos, test_size=TEST_SIZE, seed=SEED)
    conferir_sem_vazamento(grupos, idx_tr, idx_te)
    y_te = y[idx_te]

    cenario_a, modelo_a, s_a = medir_cenario(df, FEATURES_A, idx_tr, idx_te, completo=completo)
    cenario_b, _, _ = medir_cenario(df, FEATURES_B, idx_tr, idx_te, completo=False)

    # ── Cenário C, exploratório: a previsão de liquidez como feature ────
    cenario_c = {"medido": False, "motivo": "desligado (--sem-liquidez)"}
    if com_liquidez:
        t = time.perf_counter()
        colunas, motivo = previsao_de_liquidez(df, Path(pasta_v2), manifesto["semente"],
                                               manifesto.get("fonte_de_parametros") or "sintetico_calibrado")
        if colunas is None:
            cenario_c = {"medido": False, "motivo": motivo}
        else:
            df = pd.concat([df, colunas], axis=1)
            cenario_c, _, _ = medir_cenario(df, FEATURES_C, idx_tr, idx_te, completo=False)
            cenario_c.update({
                "medido": True, "exploratorio": True,
                "aviso": "entrou depois de ver A e B; fora do criterio declarado. Supoe que o "
                         "historico de saldo do cliente chega ao sistema (premissa do Modulo 3).",
                "auc_da_previsao_sozinha": _auc(y_te, df["liquidez_prevista_max_na_janela"].to_numpy()[idx_te]),
                "previsao_de_liquidez_s": round(time.perf_counter() - t, 1)})

    # ── O teto de quem soubesse o FUTURO do caixa ───────────────────────
    p_caixa = df["oculto_p_recuperacao"].to_numpy(dtype=float)[idx_te]
    teto = {
        "o_que_e": "a probabilidade de recuperar DADO o caixa simulado dos 7 dias SEGUINTES a "
                   "cobranca, o gasto do dia da falha e as latentes (oculto_p_recuperacao). E "
                   "previsao perfeita do futuro: nenhum previsor chega aqui, nem com o historico "
                   "de saldo. Serve de limite superior, nao de meta.",
        "auc": _auc(y_te, p_caixa),
        "precisao_em_recall_fixo": {str(r): precisao_em_recall(y_te, p_caixa, r) for r in RECALLS_FIXOS},
    }

    # ── O critério declarado ────────────────────────────────────────────
    em_090 = cenario_a["precisao_em_recall_fixo"]["0.9"]["precisao"]
    desvio = cenario_a["groupkfold"]["precisao_em_recall_0_90"]["desvio"] or 0.0
    referencia = REFERENCIA_V2["precisao_em_recall_fixo"]["0.9"]
    v2 = medir_v2(Path(pasta_v2), set(grupos), completo)
    criterio = {
        "declarado": CRITERIO,
        "precisao_v4_em_recall_0_90": em_090,
        "precisao_v2_em_recall_0_90": referencia,
        "diferenca": _r(em_090 - referencia),
        "desvio_exigido": _r(desvio),
        "satisfeito": bool(em_090 - referencia > desvio),
        "promovido": False,
        "sem_a_taxa_de_base": sem_a_taxa_de_base(v2, cenario_a),
    }

    metricas = {
        "modelo": f"{NOME} (experimento, NAO promovido)",
        "treinado_em": datetime.now().isoformat(timespec="seconds"),
        "semente": SEED,
        "algoritmo": "XGBClassifier (0,7) + RandomForestClassifier (0,3), hiperparametros de producao",
        "amostra_clientes": amostra_clientes,
        "base": {"pasta": str(Path(base).resolve()),
                 "sha256": manifesto["arquivos"]["classificador_v4"]["sha256"],
                 "hash_canonico": manifesto["arquivos"]["classificador_v4"].get("hash_canonico"),
                 "taxa_recuperacao": manifesto["verificacoes"]["taxa_recuperacao"],
                 "rotulo": "sintetico, por mecanismo desenhado pelo projeto; NAO e recuperacao observada"},
        "split": {"tipo": "por_cliente", "fracao_de_teste": TEST_SIZE, "semente": SEED,
                  "linhas_treino": int(len(idx_tr)), "linhas_teste": int(len(idx_te)),
                  "clientes_treino": int(len(set(grupos[idx_tr]))),
                  "clientes_teste": int(len(set(grupos[idx_te]))),
                  "taxa_recuperacao_teste": _r(y_te.mean()),
                  "mesmo_holdout_da_v2": "as mesmas cobrancas (mesma regra, mesma semente), com o rotulo novo"},
        "cenarios": {"A": cenario_a, "B": cenario_b, "C": cenario_c},
        "teto_com_o_caixa": teto,
        "modelo_de_producao_na_v4": modelo_de_producao_na_v4(df, idx_te),
        "referencia_v2": REFERENCIA_V2,
        "v2_medida": v2,
        "criterio": criterio,
        "versoes": _versoes(),
    }
    metricas["duracao_total_s"] = round(time.perf_counter() - inicio, 1)

    if gravar:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        codificador, _ = matriz(df, FEATURES_A)
        joblib.dump(modelo_a.xgb, out / f"{NOME}_xgb.joblib")
        joblib.dump(modelo_a.rf, out / f"{NOME}_rf.joblib")
        joblib.dump(codificador.label_encoders, out / f"{NOME}_encoders.joblib")
        joblib.dump(FEATURES_A, out / f"{NOME}_features.joblib")
        meta = {"modelo": NOME, "experimento": True, "contrato_de_producao": False,
                "algoritmo": metricas["algoritmo"], "features": FEATURES_A,
                "limiar_escolhido": cenario_a["limiar"]["escolhido"],
                "semente": SEED, "treinado_em": metricas["treinado_em"],
                "base_sha256": metricas["base"]["sha256"], "versoes": metricas["versoes"],
                "xgb_sha256": _sha256(out / f"{NOME}_xgb.joblib"),
                "rf_sha256": _sha256(out / f"{NOME}_rf.joblib")}
        (out / f"{NOME}_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                               encoding="utf-8")
        (out / "train_metrics_v4.json").write_text(
            json.dumps(metricas, indent=2, ensure_ascii=False), encoding="utf-8")
    _resumo(metricas, out if gravar else None)
    return metricas


def _resumo(m: dict, out) -> None:
    for nome in ("A", "B", "C"):
        c = m["cenarios"][nome]
        if c.get("medido") is False:
            _dizer(f"cenario {nome}: nao medido ({c['motivo']})")
            continue
        lim = c["limiar"]["no_limiar_escolhido"]
        _dizer(f"cenario {nome} ({c['n_features']} features): AUC {c['holdout']['auc']} | "
               f"GroupKFold {c['groupkfold']['auc']['media']} +- {c['groupkfold']['auc']['desvio']} | "
               f"limiar {lim.get('limiar')} -> recall {lim.get('recall')} precisao {lim.get('precisao')} "
               f"marcada {lim.get('fracao_marcada')}")
    a = m["cenarios"]["A"]
    if "permutacao_do_rotulo" in a:
        _dizer(f"permutacao do rotulo (A): {a['permutacao_do_rotulo']['auc_holdout']}")
    _dizer(f"teto com o futuro do caixa conhecido: AUC {m['teto_com_o_caixa']['auc']}")
    sem = m["criterio"]["sem_a_taxa_de_base"]
    if sem.get("medido"):
        _dizer(f"sem o efeito da taxa de base, recall 0,90: falsos positivos em "
               f"{sem['taxa_de_falsos_positivos_v2']} dos nao-recuperaveis na v2 e em "
               f"{sem['taxa_de_falsos_positivos_v4_a']} no cenario A")
    prod = m["modelo_de_producao_na_v4"]
    if prod.get("medido"):
        _dizer(f"modelo de producao (v2) na base v4: AUC {prod['auc']} | no limiar 0,25: "
               f"recall {prod['no_limiar_em_uso_hoje']['recall']} precisao {prod['no_limiar_em_uso_hoje']['precisao']}")
    c = m["criterio"]
    _dizer(f"criterio: precisao com recall 0,90 = {c['precisao_v4_em_recall_0_90']} (v2: "
           f"{c['precisao_v2_em_recall_0_90']}) | diferenca {c['diferenca']} > desvio {c['desvio_exigido']}: "
           f"{c['satisfeito']} (nada promovido)")
    _dizer(f"tempo total: {m['duracao_total_s']} s" + (f" | artefatos em {out}" if out else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Treina e mede o classificador de falha na base v4.")
    ap.add_argument("--base", default="data/v4/")
    ap.add_argument("--v2", default="data/v2/")
    ap.add_argument("--out", default="models/v4/")
    ap.add_argument("--amostra-clientes", type=int, default=None,
                    help="so os N primeiros clientes (para os testes)")
    ap.add_argument("--sem-medicoes-caras", action="store_true",
                    help="pula a permutacao do rotulo e a ablacao")
    ap.add_argument("--sem-liquidez", action="store_true",
                    help="pula o cenario C (a previsao de liquidez como feature)")
    args = ap.parse_args(argv)
    treinar(Path(args.base), Path(args.v2), Path(args.out),
            amostra_clientes=args.amostra_clientes, completo=not args.sem_medicoes_caras,
            com_liquidez=not args.sem_liquidez)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())

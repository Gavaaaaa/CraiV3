"""
crai/scripts/treinar_voluntario_v3.py — Treino v3 do risco voluntário (experimento).

    python -m crai.scripts.treinar_voluntario_v3 --base data/v3/ --out models/v3/

Treina um `HistGradientBoostingClassifier` sobre `FEATURES_DE_RISCO_V3` na base v3
(rótulo latente, `crai.ml.voluntario_v3`) e mede se ele supera a régua. NADA é
promovido: os artefatos vão para `models/v3/` com nome e lista de features que o
`risk_scorer.carregar_modelo()` de produção recusa.

Grava em `--out`:

    voluntary_risk_v3.joblib        o modelo
    voluntary_risk_v3_meta.json     features na ordem, versões, semente, proveniência
    train_metrics_v3.json           todas as medições (ver `treinar`)

Desenho aprovado nos Blocos 0 e 1 (`docs/interno/RELATORIO_TREINO_V3_BLOCO*.md`):

- Holdout por cliente, 20% (`split.split_por_cliente`, seed 42).
- Aumento por máscara SÓ NO TREINO (A1): cada linha de treino entra três vezes,
  com as máscaras dos cenários a, b e c, peso 1/3 cada. O holdout NUNCA é
  aumentado: cada cenário é o holdout original mascarado. No GroupKFold o grupo
  é o `customer_id`, o aumento é feito DEPOIS de separar as dobras, e as três
  cópias de um cliente caem sempre na mesma dobra.
- A régua no cenário faltante faz o que a produção faz (A2): ver `regua_no_cenario`.
- O critério de promoção é declarado ANTES do treino (`CRITERIO_PROMOCAO`) e
  impresso na primeira linha da saída.

Não importa `train_all` nem `voluntary_risk` (testado).
"""

import argparse
import hashlib
import io
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, confusion_matrix, roc_auc_score
from sklearn.model_selection import GroupKFold

from ..churn_voluntary.batch_scoring import regua_da_base
from ..churn_voluntary.risk_scorer import (EVENTO_DADO_ESTATICO, FIXED_RISK,
                                           HIGH_VALUE_MRR_DEFAULT, _risco_por_regras,
                                           risco_por_posicao)
from ..ml import voluntario_v3 as V3
from ..ml.split import conferir_sem_vazamento, split_por_cliente
from .gerar_bases_v2 import _versoes

SEED = 42
TEST_SIZE = 0.2
N_FOLDS = 5
SEMENTES_PERMUTACAO = (0, 1, 2)
NOME = "voluntary_risk_v3"

# Hiperparâmetros declarados. Sem early stopping: ele separaria uma validação
# interna por LINHA, que vazaria cliente entre os lados.
HIPERPARAMETROS = {
    "max_iter": 300,
    "learning_rate": 0.05,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 50,
    "l2_regularization": 1.0,
    "early_stopping": False,
    "random_state": SEED,
}

# Cenários de dado faltante (Bloco 0, 0.4). O que não está na lista vira NaN.
# "plano" = seats (D5).
CENARIOS = {
    "a": list(V3.FEATURES_DE_RISCO_V3),
    "b": ["days_since_last", "mrr", "tenure_days", "seats"],
    "c": ["mrr", "tenure_days", "seats", "failed_pay_90d"],
}
DESCRICAO_CENARIOS = {
    "a": "instrumentada: as 15 features",
    "b": "agregada: ultimo acesso, mrr, tenure e plano (seats)",
    "c": "so cobranca: sem sinal de uso",
}

# Regra do limiar (D7): a do classificador, "maior limiar da grade com recall
# >= 0,90", escolhido nas predições FORA-DA-DOBRA do treino (não no holdout).
RECALL_MINIMO = 0.90
GRADE_LIMIARES = [round(x, 2) for x in np.arange(0.01, 1.0, 0.01)]
# Grave = churner com MRR >= R$ 2.000 (porta de valor de `classify_criticality`),
# SÓ PARA RELATÓRIO (D7). Constante do código, não a env de produção.
MRR_GRAVE = HIGH_VALUE_MRR_DEFAULT

CRITERIO_PROMOCAO = (
    "No GroupKFold 5 sobre o treino, cenario (a): media_candidato - media_regua > "
    "max(desvio_candidato, desvio_regua), onde regua e a MELHOR das duas (fixa ou por "
    "percentil) pela media. Declarado antes do treino. Satisfazer o criterio NAO promove "
    "nada: promover e decisao de quem le o relatorio."
)
# Alarme de vazamento (Bloco 0, 0.2): uma feature sozinha acima desta fração do
# teto de Bayes, ou o candidato (a) acima dela, denuncia latente virando feature.
FRACAO_ALARME_FEATURE = 0.90
FRACAO_ALARME_CANDIDATO = 0.98


# ══════════════════════════════════════════════════════════════════════════
# DADOS
# ══════════════════════════════════════════════════════════════════════════

def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def ler_base(pasta: Path) -> tuple:
    """(df, manifesto, proveniência). Falha alto se o sha256 não confere."""
    manifesto_path = pasta / "MANIFESTO_v3.json"
    manifesto = json.loads(manifesto_path.read_text(encoding="utf-8"))
    meta = manifesto["arquivos"]["voluntario_v3"]
    caminho = pasta / meta["arquivo"]
    atual = _sha256(caminho)
    if atual != meta["sha256"]:
        raise SystemExit(f"[TREINO-V3] {caminho} nao confere com o MANIFESTO_v3.json "
                         f"(medido {atual[:16]}, declarado {meta['sha256'][:16]}).")
    if manifesto["features_de_risco_v3"]["lista"] != V3.FEATURES_DE_RISCO_V3:
        raise SystemExit("[TREINO-V3] a base declara outra FEATURES_DE_RISCO_V3; regenere.")
    prov = {
        "base_pasta": str(pasta.resolve()),
        "base_sha256": atual,
        "base_hash_canonico": meta.get("hash_canonico"),
        "manifesto_v3_sha256": _sha256(manifesto_path),
        "semente_da_base": manifesto.get("semente"),
        "features_versao": V3.FEATURES_DE_RISCO_V3_VERSAO,
        "rotulo": "Bernoulli(latentes da populacao) - sintetico, NAO e churn observado",
    }
    return pd.read_parquet(caminho), manifesto, prov


def amostrar_clientes(df: pd.DataFrame, n_clientes: int, seed: int = SEED) -> pd.DataFrame:
    """Subamostra de clientes inteiros (para testes). Declarada nas métricas."""
    ids = np.sort(df["customer_id"].unique())
    rng = np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(4,)))
    escolhidos = set(rng.choice(ids, size=min(n_clientes, len(ids)), replace=False))
    return df[df["customer_id"].isin(escolhidos)].reset_index(drop=True)


def mascarar(X: pd.DataFrame, cenario: str) -> pd.DataFrame:
    """O mesmo X com NaN em tudo que o cenário não vê."""
    out = X.copy()
    for col in out.columns:
        if col not in CENARIOS[cenario]:
            out[col] = np.nan
    return out


def aumentar(X: pd.DataFrame, y: np.ndarray, grupos: np.ndarray) -> tuple:
    """Três cópias (máscaras a, b, c), peso 1/3 cada. SÓ para linhas de treino."""
    partes = [mascarar(X, c) for c in CENARIOS]
    Xa = pd.concat(partes, ignore_index=True)
    k = len(CENARIOS)
    return (Xa, np.tile(y, k), np.tile(grupos, k),
            np.full(len(Xa), 1.0 / k), np.repeat(list(CENARIOS), len(X)))


# ══════════════════════════════════════════════════════════════════════════
# MODELO E RÉGUA
# ══════════════════════════════════════════════════════════════════════════

def ajustar(X: pd.DataFrame, y: np.ndarray, grupos: np.ndarray,
            hiper: dict = None) -> HistGradientBoostingClassifier:
    """Aumenta e ajusta. `X` são linhas de TREINO, nunca de avaliação."""
    V3.conferir_features(X.columns)
    Xa, ya, _, peso, _ = aumentar(X, y, grupos)
    m = HistGradientBoostingClassifier(**(hiper or HIPERPARAMETROS))
    m.fit(Xa.to_numpy(dtype=float), ya, sample_weight=peso)
    return m


def prever(m, X: pd.DataFrame) -> np.ndarray:
    return m.predict_proba(X.to_numpy(dtype=float))[:, 1]


def _auc(y, s):
    y = np.asarray(y)
    if len(np.unique(y)) < 2:
        return None
    return round(float(roc_auc_score(y, s)), 4)


def regua_no_cenario(df: pd.DataFrame, idx_treino, idx_aval, cenario: str) -> dict:
    """As duas réguas sobre as linhas `idx_aval`, como a produção faz (A2).

    (a) fixa = `_risco_por_regras(evento, dias, uso)`; por percentil = FIXED_RISK
        nos eventos de intenção e `risco_por_posicao` nas sessões, com a régua
        (`batch_scoring.regua_da_base`) calculada SÓ nas linhas de treino.
    (b) sem uso e sem evento. `regua_da_base` devolve None quando uma coluna tem
        menos de 30 valores (`batch_scoring.py:181-182`), então a de percentil é a
        global. A global é `risco_por_features(dias, None, mrr)` (Session Started,
        uso ausente = default 10 de `_risco_por_regras`); aqui chamada direto em
        `_risco_por_regras`, que é o que `risco_por_features` faz sem modelo ativo
        (o estado de produção). Linha sem dias e sem uso = `dado_insuficiente`,
        não pontuada (`batch_scoring.py:333-334`).
    (c) sem dias e sem uso: toda linha é `dado_insuficiente`. Nenhuma AUC.
    """
    vis = CENARIOS[cenario]
    tem_dias = "days_since_last" in vis
    tem_uso = "features_used_30d" in vis
    tem_evento = "evento_sessao" in vis
    aval = df.iloc[idx_aval]
    y = aval["churn"].to_numpy()

    def valor(linhas, col, visivel):
        return [None if not visivel else float(v) for v in linhas[col].to_numpy()]

    dias = valor(aval, "days_since_last", tem_dias)
    uso = valor(aval, "features_used_30d", tem_uso)
    eventos = aval["event"].tolist() if tem_evento else [EVENTO_DADO_ESTATICO] * len(aval)
    pontuavel = np.array([d is not None or u is not None for d, u in zip(dias, uso)])

    treino = df.iloc[idx_treino]
    regua = regua_da_base([
        {"days_since_last": d, "features_used_30d": u}
        for d, u in zip(valor(treino, "days_since_last", tem_dias),
                        valor(treino, "features_used_30d", tem_uso))])

    def props(d, u):
        p = {}
        if d is not None:
            p["days_since_last"] = d
        if u is not None:
            p["features_used_30d"] = u
        return p

    fixa = np.array([_risco_por_regras(e, props(d, u)) if ok else np.nan
                     for e, d, u, ok in zip(eventos, dias, uso, pontuavel)])
    if regua is None:
        perc, origem = fixa.copy(), "global (regua_da_base devolveu None)"
    else:
        perc = np.array([
            np.nan if not ok else (FIXED_RISK[e] if e in FIXED_RISK
                                   else risco_por_posicao(d, u, regua))
            for e, d, u, ok in zip(eventos, dias, uso, pontuavel)])
        origem = "da base (percentis do treino)"
    ok = pontuavel
    return {
        "fixa": fixa, "percentil": perc, "pontuavel": ok,
        "auc_fixa": _auc(y[ok], fixa[ok]) if ok.any() else None,
        "auc_percentil": _auc(y[ok], perc[ok]) if ok.any() else None,
        "linhas_nao_pontuadas_dado_insuficiente": int((~ok).sum()),
        "origem_regua_percentil": origem,
        "regua": regua,
    }


# ══════════════════════════════════════════════════════════════════════════
# TREINO E MEDIÇÕES
# ══════════════════════════════════════════════════════════════════════════

def escolher_limiar(y, s, recall_minimo: float = RECALL_MINIMO):
    """Maior limiar da grade com recall >= recall_minimo, ou None."""
    y = np.asarray(y)
    aprovados = [t for t in GRADE_LIMIARES
                 if ((s >= t) & (y == 1)).sum() / max((y == 1).sum(), 1) >= recall_minimo]
    return max(aprovados) if aprovados else None


def _media_desvio(v):
    v = [x for x in v if x is not None]
    return {"por_dobra": v, "media": round(float(np.mean(v)), 4),
            "desvio": round(float(np.std(v, ddof=1)), 4) if len(v) > 1 else None}


def treinar(base: Path, out: Path, features=None, amostra_clientes: int = None,
            hiper: dict = None, com_ablacao: bool = True, com_shap: bool = True) -> dict:
    features = V3.conferir_features(features or V3.FEATURES_DE_RISCO_V3)
    hiper = dict(hiper or HIPERPARAMETROS)
    t0 = time.perf_counter()
    tempos = {}
    print(f"[TREINO-V3] Criterio de promocao, declarado antes do treino: {CRITERIO_PROMOCAO}")

    df, manifesto, prov = ler_base(Path(base))
    if amostra_clientes:
        df = amostrar_clientes(df, amostra_clientes)
    faltam = [f for f in features if f not in df.columns]
    if faltam:
        raise SystemExit(f"[TREINO-V3] features ausentes na base: {faltam}")
    X = df[features]
    V3.conferir_features(X.columns)
    y = df["churn"].to_numpy(dtype=int)
    grupos = df["customer_id"].to_numpy()
    teto_p = df["oculto_p_churn"].to_numpy(dtype=float)    # só para o teto

    idx_tr, idx_te = split_por_cliente(grupos, test_size=TEST_SIZE, seed=SEED)
    conferir_sem_vazamento(grupos, idx_tr, idx_te)
    y_te = y[idx_te]

    # ── Modelo principal ────────────────────────────────────────────────
    t = time.perf_counter()
    modelo = ajustar(X.iloc[idx_tr], y[idx_tr], grupos[idx_tr], hiper)
    tempos["treino_principal_s"] = round(time.perf_counter() - t, 2)
    teto = _auc(y_te, teto_p[idx_te])

    t = time.perf_counter()
    cenarios = {}
    for c in CENARIOS:
        s = prever(modelo, mascarar(X.iloc[idx_te], c))
        r = regua_no_cenario(df, idx_tr, idx_te, c)
        auc_c = _auc(y_te, s)
        cenarios[c] = {
            "descricao": DESCRICAO_CENARIOS[c],
            "features_visiveis": CENARIOS[c],
            "auc_candidato": auc_c,
            "auc_regua_fixa": r["auc_fixa"],
            "auc_regua_percentil": r["auc_percentil"],
            "origem_regua_percentil": r["origem_regua_percentil"],
            "linhas_regua_nao_pontuadas_dado_insuficiente":
                r["linhas_nao_pontuadas_dado_insuficiente"],
            "linhas_holdout": int(len(idx_te)),
            "fracao_do_teto_direta": round(auc_c / teto, 4) if auc_c and teto else None,
            "fracao_do_teto_corrigida_acaso":
                round((auc_c - 0.5) / (teto - 0.5), 4) if auc_c and teto else None,
        }
        if c == "a":
            s_a, regua_a = s, r
    tempos["avaliacao_cenarios_s"] = round(time.perf_counter() - t, 2)

    # Secundária (D8): um evento por cliente, o último do holdout.
    te_df = df.iloc[idx_te].assign(_s=s_a, _fixa=regua_a["fixa"], _perc=regua_a["percentil"])
    ultimo = te_df.groupby("customer_id", sort=False).tail(1)
    por_cliente = {"clientes": int(len(ultimo)),
                   "auc_candidato": _auc(ultimo["churn"], ultimo["_s"]),
                   "auc_regua_fixa": _auc(ultimo["churn"], ultimo["_fixa"]),
                   "auc_regua_percentil": _auc(ultimo["churn"], ultimo["_perc"]),
                   "teto_bayes": _auc(ultimo["churn"], ultimo["oculto_p_churn"])}

    # ── GroupKFold 5 sobre o TREINO (A1) ────────────────────────────────
    t = time.perf_counter()
    gkf = GroupKFold(n_splits=N_FOLDS)
    auc_cand, auc_fixa, auc_perc = [], [], []
    oof = np.full(len(idx_tr), np.nan)
    copias_mesma_dobra = True
    for dobra_tr, dobra_va in gkf.split(idx_tr, groups=grupos[idx_tr]):
        itr, iva = idx_tr[dobra_tr], idx_tr[dobra_va]
        conferir_sem_vazamento(grupos, itr, iva)
        _, _, g_aum, _, _ = aumentar(X.iloc[itr], y[itr], grupos[itr])
        copias_mesma_dobra &= not (set(g_aum) & set(grupos[iva]))
        m = ajustar(X.iloc[itr], y[itr], grupos[itr], hiper)
        s = prever(m, X.iloc[iva])
        oof[dobra_va] = s
        r = regua_no_cenario(df, itr, iva, "a")
        auc_cand.append(_auc(y[iva], s))
        auc_fixa.append(r["auc_fixa"])
        auc_perc.append(r["auc_percentil"])
    tempos["groupkfold_s"] = round(time.perf_counter() - t, 2)
    kf_cand, kf_fixa, kf_perc = map(_media_desvio, (auc_cand, auc_fixa, auc_perc))
    melhor_nome, kf_regua = max((("fixa", kf_fixa), ("percentil", kf_perc)),
                                key=lambda kv: kv[1]["media"])
    folga = max(kf_cand["desvio"] or 0.0, kf_regua["desvio"] or 0.0)
    diferencas = [a - b for a, b in zip(auc_cand, auc_fixa if melhor_nome == "fixa" else auc_perc)]
    criterio = {
        "declarado": CRITERIO_PROMOCAO,
        "regua_comparada": melhor_nome,
        "media_candidato": kf_cand["media"],
        "media_regua": kf_regua["media"],
        "diferenca": round(kf_cand["media"] - kf_regua["media"], 4),
        "desvio_exigido": round(folga, 4),
        "desvio_das_diferencas_pareadas": round(float(np.std(diferencas, ddof=1)), 4)
        if len(diferencas) > 1 else None,
        "satisfeito": bool(kf_cand["media"] - kf_regua["media"] > folga),
        "promovido": False,
    }

    # ── Limiar (fora-da-dobra) e matriz de confusão no holdout ──────────
    limiar = escolher_limiar(y[idx_tr], oof)
    limiar_info = {"regra": f"maior limiar da grade com recall >= {RECALL_MINIMO}, escolhido "
                            "nas predicoes fora-da-dobra do GroupKFold do treino",
                   "limiar": limiar}
    if limiar is not None:
        pred = (s_a >= limiar).astype(int)
        tn, fp, fn, tp = confusion_matrix(y_te, pred, labels=[0, 1]).ravel()
        mrr_te = df["mrr"].to_numpy()[idx_te]
        graves = (y_te == 1) & (mrr_te >= MRR_GRAVE)
        limiar_info.update({
            "matriz_holdout": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
            "recall_holdout": round(tp / max(tp + fn, 1), 4),
            "precisao_holdout": round(tp / max(tp + fp, 1), 4),
            "fracao_marcada_holdout": round(float(pred.mean()), 4),
            "graves_definicao": f"churner com mrr >= {MRR_GRAVE} (so relatorio)",
            "graves_no_holdout": int(graves.sum()),
            "graves_perdidos": int((graves & (pred == 0)).sum()),
            "recall_graves": round(float(pred[graves].mean()), 4) if graves.any() else None,
        })

    # ── Permutação do rótulo (por cliente, só no treino) ────────────────
    t = time.perf_counter()
    permutacao = []
    rot_cli = df.iloc[idx_tr].groupby("customer_id", sort=True)["churn"].first()
    for sp in SEMENTES_PERMUTACAO:
        emb = pd.Series(np.random.default_rng(sp).permutation(rot_cli.to_numpy()),
                        index=rot_cli.index)
        y_perm = emb.loc[grupos[idx_tr]].to_numpy(dtype=int)
        m = ajustar(X.iloc[idx_tr], y_perm, grupos[idx_tr], hiper)
        permutacao.append({"semente": sp, "auc_holdout": _auc(y_te, prever(m, X.iloc[idx_te]))})
    tempos["permutacao_s"] = round(time.perf_counter() - t, 2)

    # ── Ablação: retreino sem cada feature ──────────────────────────────
    ablacao = None
    if com_ablacao:
        t = time.perf_counter()
        ablacao = {}
        base_auc = cenarios["a"]["auc_candidato"]
        for f in features:
            resto = [c for c in features if c != f]
            m = ajustar(X.iloc[idx_tr][resto], y[idx_tr], grupos[idx_tr], hiper)
            a = _auc(y_te, prever(m, X.iloc[idx_te][resto]))
            ablacao[f] = {"auc_sem": a, "queda": round(base_auc - a, 4)}
        tempos["ablacao_s"] = round(time.perf_counter() - t, 2)

    # ── Cada feature sozinha (alarme de vazamento) ──────────────────────
    sozinha = {}
    for f in features:
        a = _auc(y_te, X.iloc[idx_te][f].to_numpy(dtype=float))
        sozinha[f] = round(max(a, 1 - a), 4) if a is not None else None
    alarme = {
        "limiar_feature": round(FRACAO_ALARME_FEATURE * teto, 4) if teto else None,
        "features_acima": [f for f, a in sozinha.items()
                           if teto and a is not None and a > FRACAO_ALARME_FEATURE * teto],
        "limiar_candidato": round(FRACAO_ALARME_CANDIDATO * teto, 4) if teto else None,
        "candidato_acima": bool(teto and cenarios["a"]["auc_candidato"]
                                > FRACAO_ALARME_CANDIDATO * teto),
    }

    # ── Importância global ──────────────────────────────────────────────
    importancia = None
    if com_shap:
        t = time.perf_counter()
        amostra = X.iloc[idx_te].sample(n=min(2000, len(idx_te)), random_state=SEED)
        try:
            import shap
            valores = np.asarray(shap.TreeExplainer(modelo).shap_values(
                amostra.to_numpy(dtype=float)))
            if valores.ndim == 3:
                valores = valores[..., 1]
            importancia = {"metodo": f"TreeSHAP (shap {shap.__version__}), media de |SHAP| "
                                     f"em {len(amostra)} linhas do holdout, cenario (a)",
                           "valores": {f: round(float(v), 5) for f, v in
                                       zip(features, np.abs(valores).mean(axis=0))}}
        except Exception as e:  # noqa: BLE001
            from sklearn.inspection import permutation_importance
            pi = permutation_importance(modelo, amostra.to_numpy(dtype=float),
                                        df.loc[amostra.index, "churn"].to_numpy(),
                                        scoring="roc_auc", n_repeats=5, random_state=SEED)
            importancia = {"metodo": f"permutation importance (TreeSHAP falhou: {e})",
                           "valores": {f: round(float(v), 5)
                                       for f, v in zip(features, pi.importances_mean)}}
        tempos["importancia_s"] = round(time.perf_counter() - t, 2)

    duracao = round(time.perf_counter() - t0, 2)
    treinado_em = datetime.now().isoformat(timespec="seconds")
    metricas = {
        "modelo": "risk_scorer_voluntario_v3 (experimento, NAO promovido)",
        "treinado_em": treinado_em,
        "semente": SEED,
        "dispositivo": "CPU (HistGradientBoosting nao usa GPU)",
        "duracao_total_s": duracao,
        "tempos_s": tempos,
        "amostra_clientes": amostra_clientes,
        "hiperparametros": hiper,
        "features": features,
        "split": {"tipo": "por_cliente (GroupShuffleSplit)", "test_size": TEST_SIZE,
                  "seed": SEED, "eventos_treino": int(len(idx_tr)),
                  "eventos_holdout": int(len(idx_te)),
                  "clientes_treino": int(len(set(grupos[idx_tr]))),
                  "clientes_holdout": int(len(set(grupos[idx_te])))},
        "aumento_por_mascara": {"so_no_treino": True, "holdout_aumentado": False,
                                "copias": len(CENARIOS), "peso_por_copia": round(1 / len(CENARIOS), 6),
                                "cenarios": list(CENARIOS),
                                "grupo_do_groupkfold": "customer_id",
                                "copias_do_cliente_na_mesma_dobra": bool(copias_mesma_dobra)},
        "taxa_churn_holdout": round(float(y_te.mean()), 4),
        "teto_bayes_holdout": teto,
        "cenarios": cenarios,
        "por_cliente_ultimo_evento": por_cliente,
        "brier_holdout_cenario_a": round(float(brier_score_loss(y_te, s_a)), 4),
        "regua_percentil_holdout": regua_a["regua"],
        "groupkfold": {"n_splits": N_FOLDS, "dados": "treino (holdout intocado)",
                       "candidato": kf_cand, "regua_fixa": kf_fixa, "regua_percentil": kf_perc},
        "criterio_promocao": criterio,
        "limiar": limiar_info,
        "permutacao_rotulo": permutacao,
        "ablacao": ablacao,
        "auc_feature_sozinha_holdout": sozinha,
        "alarme_vazamento": alarme,
        "importancia_global": importancia,
        "proveniencia": prov,
        "versoes": {**_versoes(), "shap": _versao("shap")},
    }

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(modelo, out / f"{NOME}.joblib")
    meta = {
        "modelo": "risk_scorer_voluntario_v3",
        "contrato_de_producao": False,
        "features": features,
        "features_versao": V3.FEATURES_DE_RISCO_V3_VERSAO,
        "algoritmo": "HistGradientBoostingClassifier",
        "hiperparametros": hiper,
        "aumento_por_mascara": metricas["aumento_por_mascara"],
        "treinado_em": treinado_em,
        "seed": SEED,
        "versoes": metricas["versoes"],
        "proveniencia": prov,
        "joblib_sha256": _sha256(out / f"{NOME}.joblib"),
    }
    (out / f"{NOME}_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    (out / "train_metrics_v3.json").write_text(
        json.dumps(metricas, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    _resumo(metricas, out)
    return metricas


def _versao(modulo: str):
    try:
        return __import__(modulo).__version__
    except Exception:  # noqa: BLE001
        return None


def _resumo(m: dict, out: Path):
    print(f"\n[TREINO-V3] teto de Bayes (holdout): {m['teto_bayes_holdout']}")
    for c, v in m["cenarios"].items():
        print(f"  cenario {c}: candidato {v['auc_candidato']} | regua fixa {v['auc_regua_fixa']} "
              f"| percentil {v['auc_regua_percentil']} | nao pontuadas "
              f"{v['linhas_regua_nao_pontuadas_dado_insuficiente']}")
    k = m["groupkfold"]
    print(f"  GroupKFold: candidato {k['candidato']['media']} +- {k['candidato']['desvio']} | "
          f"fixa {k['regua_fixa']['media']} +- {k['regua_fixa']['desvio']} | "
          f"percentil {k['regua_percentil']['media']} +- {k['regua_percentil']['desvio']}")
    print(f"  criterio satisfeito: {m['criterio_promocao']['satisfeito']} (nada promovido)")
    print(f"  permutacao: {[p['auc_holdout'] for p in m['permutacao_rotulo']]}")
    print(f"  tempo total: {m['duracao_total_s']} s (CPU) | artefatos em {out}")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "buffer") and \
            (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Treino v3 do risco voluntario (experimento).")
    ap.add_argument("--base", default="data/v3/")
    ap.add_argument("--out", default="models/v3/")
    ap.add_argument("--amostra-clientes", type=int, default=None,
                    help="subamostra de clientes (so para teste; declarada nas metricas)")
    args = ap.parse_args(argv)

    from ..churn_voluntary.risk_scorer import MODELS_DIR
    if Path(args.out).resolve() == Path(MODELS_DIR).resolve():
        raise SystemExit("[TREINO-V3] --out aponta para models/ (producao). Use models/v3/.")
    treinar(Path(args.base), Path(args.out), amostra_clientes=args.amostra_clientes)
    return 0


if __name__ == "__main__":
    sys.exit(main())

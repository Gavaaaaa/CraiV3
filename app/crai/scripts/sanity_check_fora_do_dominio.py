"""crai/scripts/sanity_check_fora_do_dominio.py — Checagem honesta fora do domínio.

NÃO é treino e NÃO é teste automatizado: é um diagnóstico. Pega os modelos já
treinados em `models/` (fonte `sintetico_calibrado`, Etapa B) e roda SÓ
INFERÊNCIA contra dado real que nenhum deles viu como rótulo:

    AnomalyDetector       x  Fonte E (E-Commerce Churn, 5.630 clientes, rótulo
                             real `Churn`) — as 4 features com doador vêm da
                             linha real; as 8 sem doador ficam na mediana da
                             população saudável calibrada (constante, declarado)
    risk_scorer (regras)  x  Fonte E — `risco_por_features(DaySinceLastOrder,
      + candidato treinado    OrderCount)` contra `Churn`
    FailureClassifier     x  Fonte A (300 transações reais da Olist) — valor,
                             hora, dia da semana e dia do mês reais; NÃO há
                             rótulo de recuperação em lugar nenhum, então aqui
                             só se compara a DISTRIBUIÇÃO dos scores com a do
                             holdout sintético
    PaydayInference       -  não aplicável: não existe doador público de série
                             diária de saldo; fica só a métrica em domínio

A queda de performance fora do domínio é o RESULTADO ESPERADO — o oposto
seria a bandeira vermelha: um modelo que acerta um rótulo real de outro
domínio tão bem quanto acerta o próprio gerador estaria decorando algo, ou o
rótulo real seria trivial. O número é registrado como saiu; não se ajusta
nada para ele subir.

Extra — curva de volume: o mesmo `train()` em 4 volumes x 3 seeds do gerador
(classificador e voluntário, que são baratos). É a prova de que o mecanismo
de retreino responde a mais dado com variância medida, não com dois pontos.

Uso (de `app/`):
    python -m crai.scripts.sanity_check_fora_do_dominio --saida docs/evidencia/fora_do_dominio.json
    python -m crai.scripts.sanity_check_fora_do_dominio --sem-curva     # só a checagem
"""

import argparse
import io
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from crai.churn_voluntary.risk_scorer import risco_por_features
from crai.ml import anomaly_detector as anomaly_module
from crai.ml import calibracao
from crai.ml import failure_classifier as classifier_module
from crai.ml import voluntary_risk as voluntary_module
from crai.ml.anomaly_detector import AnomalyDetector
from crai.ml.failure_classifier import FailureClassifier
from crai.ml.synthetic_data import (
    BEHAVIORAL_FEATURES,
    _behavioral_population,
    generate_behavioral_dataset,
    generate_dataset,
)
from crai.ml.voluntary_risk import VoluntaryRiskModel

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_REAL = BASE_DIR / "data" / "real"
MODELS_DIR = BASE_DIR / "models"
LARGURA = 72


def _titulo(t: str):
    print(f"\n{'=' * LARGURA}\n  {t}\n{'=' * LARGURA}")


def _auc(y, score) -> float:
    return round(float(roc_auc_score(y, score)), 4) if len(np.unique(y)) > 1 else float("nan")


# ══════════════════════════════════════════════════════════════════════════
# 1. AnomalyDetector x Fonte E
# ══════════════════════════════════════════════════════════════════════════

def checar_anomaly(ecom: pd.DataFrame) -> dict:
    _titulo("1/3  AnomalyDetector (autoencoder)  x  E-Commerce Churn (rotulo real)")
    det = AnomalyDetector()
    if not det.load():
        return {"status": "sem modelo em models/ — rode train_all primeiro"}
    fonte = det.meta.get("fonte_usada", "sintetico")
    P = calibracao.parametros(fonte)["behavioral"]

    # Constantes para as 8 features sem doador: mediana da populacao saudavel
    # do proprio gerador (com a fonte do modelo). Declarado: o erro de
    # reconstrucao aqui so pode vir das 4 features reais.
    saud = _behavioral_population(4000, np.random.default_rng(0), anomalous=False, parametros=P)
    medianas = saud[BEHAVIORAL_FEATURES].median()

    linhas = ecom.dropna(subset=["DaySinceLastOrder", "HourSpendOnApp"]).copy()
    X = pd.DataFrame({f: medianas[f] for f in BEHAVIORAL_FEATURES}, index=linhas.index)
    X["days_since_last_login"] = linhas["DaySinceLastOrder"].clip(0, 30).astype(int)
    # avg_session_min: so o CV foi calibrado (unidade diferente) — reescala o
    # valor real para a escala do gerador mantendo a posicao relativa.
    X["avg_session_min"] = (P["saudavel"]["avg_session"]["loc"]
                            * linhas["HourSpendOnApp"] / linhas["HourSpendOnApp"].mean()).round(1)
    X["tickets_30d"] = linhas["Complain"].astype(int)
    X["nps_last"] = (linhas["SatisfactionScore"] * 2).round(1)
    y = linhas["Churn"].to_numpy(dtype=int)

    Xs = det.scaler.transform(X[det.features].to_numpy(dtype=np.float32)).astype(np.float32)
    erros = det._reconstruction_error(Xs)
    flags = erros > det.threshold

    # Em dominio: o mesmo modelo no dataset sintetico calibrado (holdout novo, seed 7)
    sint = generate_behavioral_dataset(3000, seed=7, fonte=fonte)
    Xs_sint = det.scaler.transform(sint[det.features].to_numpy(dtype=np.float32)).astype(np.float32)
    erros_sint = det._reconstruction_error(Xs_sint)
    y_sint = sint["is_anomalous"].to_numpy()

    res = {
        "modelo": "AnomalyDetector", "fonte_usada": fonte, "n_amostras_treino": det.meta.get("n_amostras"),
        "em_dominio": {
            "dataset": "sintetico_calibrado, 3000 clientes, seed 7 (nunca visto)",
            "roc_auc": _auc(y_sint, erros_sint),
            "average_precision": round(float(average_precision_score(y_sint, erros_sint)), 4),
            "taxa_flag": round(float((erros_sint > det.threshold).mean()), 4),
            "roc_auc_treino_declarada": det.meta.get("roc_auc"),
        },
        "fora_do_dominio": {
            "dataset": f"E-Commerce Churn, {len(linhas)} clientes com as 4 features presentes",
            "rotulo": "Churn real (16,8% de churn)",
            "roc_auc_erro_vs_churn": _auc(y, erros),
            "average_precision": round(float(average_precision_score(y, erros)), 4),
            "taxa_flag": round(float(flags.mean()), 4),
            "recall_churn_no_flag": round(float(flags[y == 1].mean()), 4),
            "precision_churn_no_flag": round(float(y[flags].mean()), 4) if flags.any() else None,
            "features_reais": ["days_since_last_login", "avg_session_min", "tickets_30d", "nps_last"],
            "features_constantes": [f for f in BEHAVIORAL_FEATURES
                                    if f not in ("days_since_last_login", "avg_session_min",
                                                 "tickets_30d", "nps_last")],
            "nota": "queda esperada: o doador tem direcao INVERTIDA em dias/satisfacao "
                    "(quem cancela pediu mais recentemente e esta mais satisfeito) e o "
                    "modelo so ve 4 das 12 features; um AUC ~0,5 aqui e o resultado "
                    "honesto, nao um bug",
        },
    }
    print(f"  em dominio   : ROC-AUC {res['em_dominio']['roc_auc']:.4f} | flag {res['em_dominio']['taxa_flag']:.1%}")
    print(f"  fora dominio : ROC-AUC {res['fora_do_dominio']['roc_auc_erro_vs_churn']:.4f} | "
          f"flag {res['fora_do_dominio']['taxa_flag']:.1%} | recall churn {res['fora_do_dominio']['recall_churn_no_flag']:.1%}")
    return res


# ══════════════════════════════════════════════════════════════════════════
# 2. risk_scorer (regras) + candidato x Fonte E
# ══════════════════════════════════════════════════════════════════════════

def checar_voluntario(ecom: pd.DataFrame) -> dict:
    _titulo("2/3  risk_scorer voluntario (regras e candidato)  x  E-Commerce Churn")
    linhas = ecom.dropna(subset=["DaySinceLastOrder", "OrderCount"]).copy()
    y = linhas["Churn"].to_numpy(dtype=int)
    dias = linhas["DaySinceLastOrder"].astype(int).to_numpy()
    pedidos = linhas["OrderCount"].astype(int).to_numpy()

    regras = np.array([risco_por_features(days_since_last=int(d), features_used_30d=int(f))
                       for d, f in zip(dias, pedidos)])
    res = {
        "modelo": "risk_scorer_voluntario",
        "regras_fixas": {
            "dataset": f"E-Commerce Churn, {len(linhas)} clientes",
            "roc_auc_vs_churn": _auc(y, regras),
            "risco_medio": round(float(regras.mean()), 4),
            "fracao_acima_de_0_60": round(float((regras >= 0.60).mean()), 4),
            "nota": "as regras nunca viram este dado; DaySinceLastOrder -> days_since_last, "
                    "OrderCount -> features_used_30d (proxy fraco), evento = Session Started",
        },
    }
    print(f"  regras fixas : ROC-AUC vs churn {res['regras_fixas']['roc_auc_vs_churn']:.4f} | "
          f"risco medio {res['regras_fixas']['risco_medio']:.3f}")

    vol = VoluntaryRiskModel()
    if vol.load():
        mrr_const = float(np.exp(calibracao.parametros(vol.meta.get("fonte_usada", "sintetico"))
                                 ["voluntary"]["mrr_lognormal"]["mean"]))
        X = np.column_stack([dias, pedidos, np.full(len(dias), mrr_const),
                             np.zeros(len(dias)), np.zeros(len(dias)), np.ones(len(dias))])
        proba = vol.model.predict_proba(X)[:, 1]
        res["candidato"] = {
            "fonte_usada": vol.meta.get("fonte_usada"),
            "n_amostras_treino": vol.meta.get("n_amostras"),
            "em_dominio_auc_vs_rotulo": vol.meta.get("auc_vs_rotulo"),
            "fora_do_dominio_auc_vs_churn": _auc(y, proba),
            "corr_com_regras_no_real": round(float(np.corrcoef(proba, regras)[0, 1]), 4),
            "mrr_constante_usado": round(mrr_const, 2),
            "nota": "o candidato aprendeu as REGRAS com ruido, nao churn observado — fora do "
                    "dominio ele so pode ser tao bom quanto as regras",
        }
        print(f"  candidato    : em dominio AUC {res['candidato']['em_dominio_auc_vs_rotulo']} | "
              f"fora dominio AUC vs churn {res['candidato']['fora_do_dominio_auc_vs_churn']:.4f}")
    else:
        res["candidato"] = {"status": "sem candidato em models/"}
    return res


# ══════════════════════════════════════════════════════════════════════════
# 3. FailureClassifier x Fonte A (sem rotulo: distribuicao)
# ══════════════════════════════════════════════════════════════════════════

def checar_classifier(amostra: pd.DataFrame) -> dict:
    _titulo("3/3  FailureClassifier  x  300 transacoes reais (Olist) — sem rotulo")
    clf = FailureClassifier()
    if not clf.load():
        return {"status": "sem modelo em models/ — rode train_all primeiro"}
    fonte = clf.meta.get("fonte_usada", "sintetico")

    # Features reais: valor, hora, dia da semana, dia do mes. As demais (sem
    # doador) ficam na mediana/moda do gerador com a mesma fonte — declarado.
    sint = generate_dataset(3000, seed=7, fonte=fonte)
    base = {
        "tenure_months": int(sint["tenure_months"].median()),
        "payment_history_score": float(sint["payment_history_score"].median()),
        "failure_count_90d": int(sint["failure_count_90d"].median()),
        "attempt_count": 1,
        "gateway_error_code": "insufficient_funds",
        "card_brand": "visa",
    }
    real = pd.DataFrame({
        **{k: [v] * len(amostra) for k, v in base.items()},
        "invoice_amount": amostra["payment_value"].to_numpy(dtype=float),
        "avg_ticket": amostra["payment_value"].to_numpy(dtype=float),
        "hour_of_day": amostra["hour_of_day"].to_numpy(dtype=int),
        "day_of_week": amostra["day_of_week"].to_numpy(dtype=int),
        "day_of_month": amostra["day_of_month"].to_numpy(dtype=int),
    })
    real["ltv_estimated"] = real["invoice_amount"] * 6

    # Sem log SHAP de auditoria a cada linha: e diagnostico, nao operacao.
    clf._save_audit_log = lambda *a, **k: None
    pred_real = clf.predict_batch(real)
    pred_sint = clf.predict_batch(sint.head(300).assign(
        ltv_estimated=lambda d: d["ltv_estimated"]))

    def _dist(p: pd.Series) -> dict:
        return {"media": round(float(p.mean()), 4), "p25": round(float(p.quantile(0.25)), 4),
                "mediana": round(float(p.median()), 4), "p75": round(float(p.quantile(0.75)), 4)}

    res = {
        "modelo": "FailureClassifier", "fonte_usada": fonte, "n_amostras_treino": clf.meta.get("n_amostras"),
        "em_dominio": {
            "dataset": "sintetico_calibrado, 300 linhas, seed 7 (nunca visto)",
            "p_recovery": _dist(pred_sint["p_recovery"]),
            "auc_holdout_treino_declarada": clf.meta.get("auc"),
        },
        "fora_do_dominio": {
            "dataset": "Olist, 300 transacoes reais (valor/hora/dia reais; demais features na mediana)",
            "rotulo": "NAO EXISTE — nenhuma base publica tem 'cobranca falhada foi recuperada'",
            "p_recovery": _dist(pred_real["p_recovery"]),
            "fracao_recommend_action": round(float(pred_real["recommend_action"].mean()), 4),
            "nota": "sem rotulo nao ha AUC; o que se mede e se a distribuicao de score no "
                    "dado real e plausivel e parecida com a do holdout — nao prova acerto",
        },
    }
    print(f"  p_recovery em dominio  : {res['em_dominio']['p_recovery']}")
    print(f"  p_recovery no real     : {res['fora_do_dominio']['p_recovery']}")
    return res


# ══════════════════════════════════════════════════════════════════════════
# EXTRA — curva de volume (mecanismo de retreino com variancia)
# ══════════════════════════════════════════════════════════════════════════

def curva_de_volume(fonte: str, volumes: list, seeds: list) -> dict:
    _titulo(f"EXTRA  curva de volume ({fonte}) — {len(volumes)} volumes x {len(seeds)} seeds")
    tmp = Path(tempfile.mkdtemp())
    classifier_module.MODELS_DIR = tmp / "clf"
    voluntary_module.MODELS_DIR = tmp / "vol"
    silencio = io.StringIO()

    curva = {"classifier": [], "voluntario": []}
    for n in volumes:
        aucs_c, aucs_v = [], []
        for seed in seeds:
            stdout = sys.stdout
            sys.stdout = silencio
            try:
                m = FailureClassifier().train(n_samples=n, fonte=fonte, seed=seed)
                aucs_c.append(m["auc"])
                v = VoluntaryRiskModel().train(n_samples=n, fonte=fonte, seed=seed)
                aucs_v.append(v["auc_vs_rotulo"])
            finally:
                sys.stdout = stdout
        curva["classifier"].append({"n_amostras": n, "auc_media": round(float(np.mean(aucs_c)), 4),
                                    "auc_dp": round(float(np.std(aucs_c)), 4), "aucs": aucs_c})
        curva["voluntario"].append({"n_amostras": n, "auc_media": round(float(np.mean(aucs_v)), 4),
                                    "auc_dp": round(float(np.std(aucs_v)), 4), "aucs": aucs_v})
        print(f"  n={n:6d}: classifier AUC {np.mean(aucs_c):.4f} +- {np.std(aucs_c):.4f} | "
              f"voluntario AUC {np.mean(aucs_v):.4f} +- {np.std(aucs_v):.4f}")
    return {"fonte": fonte, "seeds": seeds, **curva}


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Checagem fora do dominio dos modelos treinados")
    ap.add_argument("--saida", type=Path, default=None, help="grava o JSON de resultados aqui")
    ap.add_argument("--sem-curva", action="store_true", help="pula a curva de volume")
    ap.add_argument("--volumes", type=int, nargs="+", default=[1000, 3000, 6000, 12000])
    ap.add_argument("--seeds", type=int, nargs="+", default=[42, 7, 2024])
    args = ap.parse_args(argv)

    ecom = pd.read_csv(DATA_REAL / "ecommerce_churn.csv")
    amostra = pd.read_csv(DATA_REAL / "amostra_300.csv")

    resultados = {
        "anomaly": checar_anomaly(ecom),
        "voluntario": checar_voluntario(ecom),
        "classifier": checar_classifier(amostra),
        "payday": {"modelo": "PaydayInference",
                   "fora_do_dominio": "NAO APLICAVEL — nenhum doador publico de serie diaria de saldo; "
                                      "so a metrica em dominio existe (ver rodada_alta.json)"},
    }
    if not args.sem_curva:
        fonte = resultados["classifier"].get("fonte_usada", "sintetico_calibrado")
        resultados["curva_de_volume"] = curva_de_volume(fonte, args.volumes, args.seeds)

    if args.saida:
        args.saida.parent.mkdir(parents=True, exist_ok=True)
        args.saida.write_text(json.dumps(resultados, ensure_ascii=False, indent=2, default=str),
                              encoding="utf-8")
        print(f"\n  Resultados gravados em {args.saida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

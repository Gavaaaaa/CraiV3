"""
crai/ml/failure_classifier.py — XGBoost + Random Forest: classificador de falha de pagamento.

Módulo 1 do pipeline CRAI. Responsável por:
1. Treinar ensemble XGBoost (70%) + Random Forest (30%) em dataset sintético
2. Predizer probabilidade de recuperação (score 0-100)
3. Calcular e-Profit = (P_recuperação × LTV) - Custo_intervenção
4. Gerar explicações SHAP por predição (log JSON de auditoria)

Métrica de sucesso: e-Profit (NÃO apenas acurácia/AUC).
Recomenda intervenção SOMENTE se e-Profit > 0 para o cliente.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import joblib
import numpy as np
import pandas as pd
import shap
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    classification_report,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBClassifier

from .synthetic_data import generate_dataset

logger = logging.getLogger(__name__)

# ── Diretórios para persistência ─────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = BASE_DIR / "models"
LOGS_DIR = BASE_DIR / "logs" / "shap"

# ── Custos de intervenção por canal (R$) ─────────────────────────────────
INTERVENTION_COSTS = {
    "bot_whatsapp": 0.05,
    "email_auto": 0.02,
    "sms": 0.08,
    "ligacao_cs": 15.00,
    "pix_boleto_link": 0.50,
}

# ── Limiar de classificação — para o RELATÓRIO, não para a decisão ──────
#
# ATENÇÃO ao que este número é e ao que ele não é.
#
# Ele **não** é a regra de decisão do pipeline. Quem decide se a CRAI age é
# `route_after_diagnosis` (crai/agent/main_agent.py), pela regra de e-Profit:
# age quando `p_recovery * LTV > custo da intervenção`. Medida no holdout
# abaixo, essa regra abandona 3 clientes em 3.000 e perde **zero** recuperáveis
# — a recall OPERACIONAL do sistema é 1,000.
#
# Este limiar serve só para o relatório: acurácia, precisão, recall e matriz de
# confusão precisam de um corte binário, e o modelo devolve probabilidade.
#
# O 0,50 herdado era arbitrário — herança de `predict_proba` genérico, sem
# nenhuma justificativa de negócio. Ele fazia o relatório anunciar recall 0,548
# num sistema que na prática não abandona quase ninguém: o número descrevia o
# corte, não a CRAI.
#
# ── POR QUE 0,25, E POR QUE NÃO "PORQUE MAXIMIZA F2" ────────────────────
#
# Uma versão anterior deste comentário afirmava que 0,25 maximiza F2. **É
# falso, e a própria máquina desmente.** Medido em
# `generate_dataset(n_samples=15000, seed=42)`, split `random_state=42`,
# `test_size=0.2` → holdout de 3.000 linhas, 1.349 recuperáveis:
#
#     limiar  acurácia  precisão  recall      F2   recuperáveis perdidos
#       0,50     0,643     0,616   0,548  0,5603         610 / 1349
#       0,40     0,634     0,572   0,738  0,6972         354 / 1349
#       0,35     0,623     0,554   0,830  0,7548         229 / 1349
#       0,30     0,597     0,531   0,895  0,7874         141 / 1349
#     → 0,25     0,559     0,505   0,940  0,8020          81 / 1349
#       0,20     0,514     0,480   0,968  0,8045          43 / 1349
#       0,15     0,486     0,467   0,990  0,8083          14 / 1349
#
# O F2 **cresce monotonicamente** conforme o limiar cai. Não há máximo interior
# para escolher. E o motivo é estrutural, não ruído: com taxa base de 0,4497,
# o classificador TRIVIAL — prever "recupera" para todo mundo — tem F2 = 0,8034,
# a apenas 0,005 do melhor valor da tabela. **Maximizar F2 aqui seleciona o
# classificador que não classifica.** F2 é a métrica certa para comparar dois
# modelos; é a métrica errada para escolher um corte.
#
# Então o corte vem de uma REGRA DE NEGÓCIO declarada, não de um argmax:
#
#     o maior limiar da grade que ainda sustenta recall >= RECALL_MINIMO (0,90)
#
# Ler: "o relatório pode ser conservador, desde que não deixe de reconhecer
# mais de 10% dos clientes recuperáveis". 0,30 dá 0,895 e reprova; 0,25 dá
# 0,940 e passa. A regra está implementada em `escolher_limiar()` e um teste
# afirma que ela devolve exatamente esta constante — se alguém mudar o número
# sem refazer a análise, a suíte quebra.
#
# A acurácia CAI de 0,643 para 0,559, e isso é esperado e aceito: acurácia
# premia acertar a classe majoritária, que é exatamente a decisão sem valor
# comercial. O gate do plano é AUC, que não depende de limiar nenhum.
RECALL_MINIMO = 0.90
LIMIAR_CLASSIFICACAO = 0.25

# Limiares reportados na tabela de métricas, para a banca ver o trade-off
# inteiro em vez de um ponto escolhido a dedo.
LIMIARES_REPORTADOS = (0.50, 0.40, 0.35, 0.30, LIMIAR_CLASSIFICACAO, 0.20, 0.15)


def escolher_limiar(metricas_por_limiar: list[dict],
                    recall_minimo: float = RECALL_MINIMO) -> Optional[float]:
    """A regra de escolha do limiar do relatório, executável.

    Devolve o **maior** limiar que ainda sustenta `recall >= recall_minimo`.

    Existe para que a justificativa de `LIMIAR_CLASSIFICACAO` não seja um
    parágrafo de comentário que ninguém verifica. O teste
    `TestEscolhaDoLimiar` roda esta função sobre a varredura real e exige que
    ela devolva a constante — trocar o número sem refazer a medição quebra a
    suíte, que é o ponto.

    Devolve `None` quando nenhum limiar da grade alcança o recall mínimo: é o
    sinal de que a grade (ou o modelo) precisa mudar, não de que se deve
    arredondar o critério para baixo.
    """
    aprovados = [m["limiar"] for m in metricas_por_limiar
                 if m["recall"] >= recall_minimo]
    return max(aprovados) if aprovados else None

# ── Features categóricas e numéricas ─────────────────────────────────────
CATEGORICAL_FEATURES = ["gateway_error_code", "card_brand"]
NUMERICAL_FEATURES = [
    "tenure_months", "day_of_month", "invoice_amount", "avg_ticket",
    "payment_history_score", "failure_count_90d", "hour_of_day",
    "day_of_week", "attempt_count",
]
# LTV não entra como feature de treino (usada apenas no cálculo do e-Profit)
ALL_FEATURES = NUMERICAL_FEATURES + CATEGORICAL_FEATURES

# ── Mapa de códigos Stripe para códigos internos ────────────────────────
STRIPE_CODE_MAP = {
    "expired_card":       "expired_card",
    "insufficient_funds": "insufficient_funds",
    "card_declined":      "card_declined",
    "processing_error":   "processing_error",
    "do_not_honor":       "do_not_honor",
    "generic_decline":    "generic_decline",
}


class FailureClassifier:
    """
    Ensemble XGBoost + Random Forest para classificar falhas de pagamento.

    - train(): treina em dataset sintético e salva modelos + encoders
    - predict(): retorna score de recuperabilidade (0-100), e-Profit e explicação SHAP
    - load(): carrega modelos pré-treinados do disco
    """

    def __init__(self):
        self.xgb = XGBClassifier(
            n_estimators=200,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            verbosity=0,
            random_state=42,
        )
        self.rf = RandomForestClassifier(
            n_estimators=150,
            max_depth=8,
            min_samples_leaf=5,
            n_jobs=-1,
            class_weight="balanced",
            random_state=42,
        )
        self.label_encoders: dict[str, LabelEncoder] = {}
        self.is_fitted = False
        self.feature_names: list[str] = []
        self._xgb_explainer: Optional[shap.TreeExplainer] = None
        self._rf_explainer: Optional[shap.TreeExplainer] = None
        self._train_metrics: dict = {}

    # ══════════════════════════════════════════════════════════════════════
    # TREINO
    # ══════════════════════════════════════════════════════════════════════

    def train(self, n_samples: int = 3000, test_size: float = 0.2) -> dict:
        """
        Treina o ensemble em dataset sintético e retorna métricas.

        Args:
            n_samples: Tamanho do dataset sintético
            test_size: Fração para teste

        Returns:
            Dicionário com métricas de treino (AUC, report, e-Profit médio)
        """
        print("[CLASSIFIER] Gerando dataset sintético...")
        df = generate_dataset(n_samples=n_samples)

        # Separar LTV antes de preparar features (não entra no treino)
        ltv_series = df["ltv_estimated"].copy()

        # Preparar features
        X, y = self._prepare_features(df)
        self.feature_names = ALL_FEATURES.copy()

        X_train, X_test, y_train, y_test, ltv_train, ltv_test = train_test_split(
            X, y, ltv_series.values, test_size=test_size, random_state=42, stratify=y
        )

        # Treinar XGBoost
        print("[CLASSIFIER] Treinando XGBoost...")
        self.xgb.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            verbose=False,
        )

        # Treinar Random Forest
        print("[CLASSIFIER] Treinando Random Forest...")
        self.rf.fit(X_train, y_train)

        self.is_fitted = True

        # Inicializar SHAP explainers
        print("[CLASSIFIER] Inicializando SHAP explainers...")
        self._xgb_explainer = shap.TreeExplainer(self.xgb)
        self._rf_explainer = shap.TreeExplainer(self.rf)

        # Calcular métricas no conjunto de teste
        metrics = self._evaluate(X_test, y_test, ltv_test)
        self._train_metrics = metrics

        # Salvar modelos
        self._save_models()

        op = metrics["recall_operacional"]
        print(f"[CLASSIFIER] Treino concluído — AUC: {metrics['auc']:.3f} | "
              f"e-Profit médio (teste): R$ {metrics['avg_eprofit']:.2f}")
        print(f"[CLASSIFIER] Limiar do relatório {LIMIAR_CLASSIFICACAO:.2f} -> "
              f"recall {metrics['recall_recovered']:.3f}, "
              f"acurácia {metrics['accuracy']:.3f}")
        print(f"[CLASSIFIER] Recall OPERACIONAL (regra de e-Profit, que é quem "
              f"decide): {op['recall']:.3f} — "
              f"{op['recuperaveis_perdidos']} recuperável(is) perdido(s)")

        return metrics

    def _prepare_features(self, df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Codifica categóricas e retorna X, y."""
        df_encoded = df.copy()

        for col in CATEGORICAL_FEATURES:
            if col not in self.label_encoders:
                le = LabelEncoder()
                df_encoded[col] = le.fit_transform(df[col].astype(str))
                self.label_encoders[col] = le
            else:
                le = self.label_encoders[col]
                df_encoded[col] = df[col].astype(str).map(
                    lambda x, _le=le: (
                        _le.transform([x])[0] if x in _le.classes_
                        else len(_le.classes_)
                    )
                )

        X = df_encoded[ALL_FEATURES].values.astype(float)
        y = df["recovered"].values
        return X, y

    def _evaluate(self, X_test: np.ndarray, y_test: np.ndarray, ltv_test: np.ndarray) -> dict:
        """Calcula métricas de avaliação no conjunto de teste."""
        xgb_proba = self.xgb.predict_proba(X_test)[:, 1]
        rf_proba = self.rf.predict_proba(X_test)[:, 1]
        ensemble_proba = 0.7 * xgb_proba + 0.3 * rf_proba

        y_pred = (ensemble_proba >= LIMIAR_CLASSIFICACAO).astype(int)
        auc = roc_auc_score(y_test, ensemble_proba)

        # e-Profit médio (usando bot_whatsapp como canal padrão)
        cost = INTERVENTION_COSTS["bot_whatsapp"]
        eprofits = ensemble_proba * ltv_test - cost
        avg_eprofit = float(np.mean(eprofits[eprofits > 0])) if np.any(eprofits > 0) else 0.0

        report = classification_report(y_test, y_pred, output_dict=True)
        cm = confusion_matrix(y_test, y_pred).tolist()

        return {
            "auc": round(auc, 4),
            "limiar_classificacao": LIMIAR_CLASSIFICACAO,
            "accuracy": round(report["accuracy"], 4),
            "precision_recovered": round(report.get("1", {}).get("precision", 0), 4),
            "recall_recovered": round(report.get("1", {}).get("recall", 0), 4),
            "f1_recovered": round(report.get("1", {}).get("f1-score", 0), 4),
            "confusion_matrix": cm,
            # A tabela inteira, e não só o ponto escolhido: um limiar é uma
            # decisão de negócio, e a banca tem que poder ver o que se ganha e
            # o que se perde ao movê-lo.
            "metricas_por_limiar": self._varrer_limiares(y_test, ensemble_proba),
            # A recall que importa não é a do limiar: é a da regra que o
            # pipeline realmente usa para desistir de um cliente.
            "recall_operacional": self._recall_operacional(
                y_test, ensemble_proba, ltv_test, cost),
            "avg_eprofit": round(avg_eprofit, 2),
            "n_positive_eprofit": int(np.sum(eprofits > 0)),
            "n_total_test": len(y_test),
            "classification_report": report,
        }

    @staticmethod
    def _varrer_limiares(y_test: np.ndarray, proba: np.ndarray) -> list[dict]:
        """Acurácia / precisão / recall / F2 em cada limiar reportado."""
        linhas = []
        for limiar in LIMIARES_REPORTADOS:
            pred = (proba >= limiar).astype(int)
            precisao, recall, _, _ = precision_recall_fscore_support(
                y_test, pred, average="binary", zero_division=0)
            # F2 pesa recall 2x precisão — a assimetria do dunning.
            f2 = ((5 * precisao * recall) / (4 * precisao + recall)
                  if (precisao + recall) > 0 else 0.0)
            linhas.append({
                "limiar": limiar,
                "acuracia": round(float((pred == y_test).mean()), 4),
                "precisao": round(float(precisao), 4),
                "recall": round(float(recall), 4),
                "f2": round(float(f2), 4),
                "recuperaveis_perdidos": int(((y_test == 1) & (pred == 0)).sum()),
                "em_uso": limiar == LIMIAR_CLASSIFICACAO,
            })
        return linhas

    @staticmethod
    def _recall_operacional(y_test: np.ndarray, proba: np.ndarray,
                            ltv_test: np.ndarray, cost: float) -> dict:
        """A recall da regra que o pipeline REALMENTE usa para desistir.

        `route_after_diagnosis` abandona o cliente quando o e-Profit não é
        positivo (`p * LTV <= custo`) ou quando o score fica abaixo de 5/100.
        Como o custo do bot é R$ 0,05 e o LTV raramente é desprezível, essa
        regra quase nunca desiste — e é ela, não o limiar do relatório, que
        determina quantos clientes recuperáveis a CRAI deixa passar.
        """
        abandonados = ((proba * ltv_test - cost) <= 0) | ((proba * 100) < 5)
        recuperaveis = int((y_test == 1).sum())
        perdidos = int(((y_test == 1) & abandonados).sum())
        return {
            "regra": "e-Profit <= 0 ou recovery_score < 5 (route_after_diagnosis)",
            "clientes_abandonados": int(abandonados.sum()),
            "n_total": int(len(y_test)),
            "recuperaveis_perdidos": perdidos,
            "recall": round(1 - perdidos / recuperaveis, 4) if recuperaveis else None,
        }

    # ══════════════════════════════════════════════════════════════════════
    # PREDIÇÃO
    # ══════════════════════════════════════════════════════════════════════

    def predict(self, features: dict, channel: str = "bot_whatsapp") -> dict:
        """
        Prediz recuperabilidade e calcula e-Profit para um cliente.

        Args:
            features: Dicionário com features do cliente
            channel: Canal de intervenção para cálculo do custo

        Returns:
            Dicionário com score, e-Profit, recomendação e explicação SHAP
        """
        if not self.is_fitted:
            return self._heuristic_fallback(features, channel)

        ltv = features.get("ltv_estimated", features.get("invoice_amount", 100) * 6)
        cost = INTERVENTION_COSTS.get(channel, INTERVENTION_COSTS["bot_whatsapp"])

        # Preparar input
        X = self._preprocess_single(features)

        # Ensemble: 70% XGBoost + 30% Random Forest
        xgb_proba = self.xgb.predict_proba(X)[0, 1]
        rf_proba = self.rf.predict_proba(X)[0, 1]
        p_recovery = 0.7 * xgb_proba + 0.3 * rf_proba

        # Score de recuperabilidade (0-100)
        recovery_score = int(round(p_recovery * 100))

        # e-Profit
        eprofit = round(float(p_recovery * ltv - cost), 2)
        recommend_action = bool(eprofit > 0)

        # Explicação SHAP
        shap_explanation = self._explain_shap(X, features)

        # Canal ótimo (maior e-Profit positivo)
        optimal_channel = self._find_optimal_channel(p_recovery, ltv)

        result = {
            "recovery_score": recovery_score,
            "p_recovery": round(float(p_recovery), 4),
            "eprofit": eprofit,
            "recommend_action": recommend_action,
            "channel": channel,
            "intervention_cost": cost,
            "ltv_estimated": round(ltv, 2),
            "optimal_channel": optimal_channel,
            "shap_explanation": shap_explanation,
            "method": "ensemble_xgb_rf",
            "xgb_proba": round(float(xgb_proba), 4),
            "rf_proba": round(float(rf_proba), 4),
        }

        # Salvar log de auditoria SHAP
        self._save_audit_log(features, result)

        return result

    def predict_batch(self, df: pd.DataFrame, channel: str = "bot_whatsapp") -> pd.DataFrame:
        """Predição em batch para múltiplos clientes."""
        results = []
        for _, row in df.iterrows():
            result = self.predict(row.to_dict(), channel=channel)
            results.append(result)
        return pd.DataFrame(results)

    def _preprocess_single(self, features: dict) -> np.ndarray:
        """Converte dicionário de features para array numpy."""
        row = []
        for col in ALL_FEATURES:
            val = features.get(col, 0)
            if col in CATEGORICAL_FEATURES:
                le = self.label_encoders.get(col)
                if le is not None:
                    val_str = str(val)
                    if val_str in le.classes_:
                        val = le.transform([val_str])[0]
                    else:
                        val = len(le.classes_)
                else:
                    val = 0
            row.append(float(val))
        return np.array([row], dtype=float)

    def _find_optimal_channel(self, p_recovery: float, ltv: float) -> dict:
        """Encontra o canal com maior e-Profit positivo."""
        best_channel = None
        best_eprofit = 0.0
        channel_eprofits = {}

        for ch, cost in INTERVENTION_COSTS.items():
            ep = round(p_recovery * ltv - cost, 2)
            channel_eprofits[ch] = ep
            if ep > best_eprofit:
                best_eprofit = ep
                best_channel = ch

        return {
            "channel": best_channel or "nenhum",
            "eprofit": best_eprofit,
            "all_channels": channel_eprofits,
        }

    # ══════════════════════════════════════════════════════════════════════
    # SHAP — EXPLICABILIDADE
    # ══════════════════════════════════════════════════════════════════════

    def _explain_shap(self, X: np.ndarray, features: dict) -> dict:
        """
        Gera explicação SHAP para uma predição individual.

        Retorna contribuição de cada feature em formato legível:
        ex: "Tenure 24 meses (+32%), histórico limpo (+28%), dia 28 do mês (-12%)"
        """
        if self._xgb_explainer is None or self._rf_explainer is None:
            return {"error": "SHAP não inicializado — execute train() primeiro"}

        # SHAP values do ensemble ponderado
        xgb_shap = self._xgb_explainer.shap_values(X)
        rf_shap = self._rf_explainer.shap_values(X)

        # Extrair SHAP values para classe positiva (recovered=1)
        xgb_sv = self._extract_positive_class_shap(xgb_shap)
        rf_sv = self._extract_positive_class_shap(rf_shap)

        # Ensemble SHAP: mesma ponderação do modelo
        ensemble_shap = 0.7 * xgb_sv + 0.3 * rf_sv

        # Montar explicação ordenada por impacto absoluto
        explanations = []
        total_abs = np.sum(np.abs(ensemble_shap)) + 1e-9

        for i, fname in enumerate(self.feature_names):
            sv = float(ensemble_shap[i])
            pct = round((abs(sv) / total_abs) * 100, 1)
            raw_value = features.get(fname, "N/A")
            if isinstance(raw_value, (np.integer, np.floating)):
                raw_value = round(float(raw_value), 2)
            explanations.append({
                "feature": fname,
                "value": raw_value,
                "shap_value": round(sv, 4),
                "contribution_pct": pct,
                "direction": "+" if sv > 0 else "-",
            })

        # Ordenar por impacto absoluto (maior primeiro)
        explanations.sort(key=lambda x: abs(x["shap_value"]), reverse=True)

        # Gerar texto legível em PT-BR
        readable = self._format_readable_explanation(explanations)

        return {
            "features": explanations,
            "readable": readable,
        }

    @staticmethod
    def _extract_positive_class_shap(shap_values) -> np.ndarray:
        """Extrai SHAP values da classe positiva, independente do formato retornado."""
        if isinstance(shap_values, list):
            # RF retorna lista [class_0_shap, class_1_shap], cada um (n_samples, n_features)
            sv = shap_values[1] if len(shap_values) > 1 else shap_values[0]
        elif isinstance(shap_values, np.ndarray) and shap_values.ndim == 3:
            # Shape (n_samples, n_features, n_classes)
            sv = shap_values[:, :, 1] if shap_values.shape[2] > 1 else shap_values[:, :, 0]
        elif isinstance(shap_values, np.ndarray) and shap_values.ndim == 2:
            # Pode ser (n_samples, n_features) ou (n_features, n_classes)
            if shap_values.shape[0] == 1:
                # (1, n_features) — uma amostra, já é o que queremos
                sv = shap_values
            elif shap_values.shape[1] == 2:
                # (n_features, n_classes) — pegar classe positiva
                sv = shap_values[:, 1].reshape(1, -1)
            else:
                sv = shap_values
        else:
            sv = shap_values

        # Garantir shape (n_features,) para uma amostra
        sv = np.asarray(sv).flatten()
        return sv

    def _format_readable_explanation(self, explanations: list[dict]) -> str:
        """Formata explicação SHAP em texto legível PT-BR."""
        FRIENDLY_NAMES = {
            "tenure_months": "Tenure",
            "payment_history_score": "Histórico de pagamento",
            "gateway_error_code": "Código de erro",
            "invoice_amount": "Valor da fatura",
            "day_of_month": "Dia do mês",
            "avg_ticket": "Ticket médio",
            "failure_count_90d": "Falhas (90 dias)",
            "hour_of_day": "Hora da cobrança",
            "day_of_week": "Dia da semana",
            "attempt_count": "Tentativas anteriores",
            "card_brand": "Bandeira do cartão",
        }

        parts = []
        for exp in explanations[:5]:
            name = FRIENDLY_NAMES.get(exp["feature"], exp["feature"])
            val = exp["value"]
            sign = "+" if exp["direction"] == "+" else "-"
            pct = exp["contribution_pct"]

            if exp["feature"] == "tenure_months":
                parts.append(f"{name} {val} meses ({sign}{pct}%)")
            elif exp["feature"] == "invoice_amount":
                parts.append(f"{name} R$ {val:.2f} ({sign}{pct}%)" if isinstance(val, (int, float)) else f"{name} {val} ({sign}{pct}%)")
            elif exp["feature"] == "payment_history_score":
                if isinstance(val, (int, float)):
                    quality = "limpo" if val > 0.7 else "regular" if val > 0.4 else "problemático"
                    parts.append(f"{name} {quality} ({sign}{pct}%)")
                else:
                    parts.append(f"{name} {val} ({sign}{pct}%)")
            else:
                parts.append(f"{name} {val} ({sign}{pct}%)")

        return " | ".join(parts)

    # ══════════════════════════════════════════════════════════════════════
    # LOGS DE AUDITORIA
    # ══════════════════════════════════════════════════════════════════════

    def _save_audit_log(self, features: dict, result: dict):
        """Salva log de auditoria SHAP em JSON."""
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "input_features": {
                k: (float(v) if isinstance(v, (np.integer, np.floating)) else v)
                for k, v in features.items()
                if k in ALL_FEATURES + ["ltv_estimated"]
            },
            "output": {
                "recovery_score": result["recovery_score"],
                "p_recovery": result["p_recovery"],
                "eprofit": result["eprofit"],
                "recommend_action": result["recommend_action"],
                "channel": result["channel"],
                "optimal_channel": result["optimal_channel"]["channel"],
            },
            "shap_explanation": result["shap_explanation"],
        }

        filename = f"shap_audit_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.json"
        filepath = LOGS_DIR / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(log_entry, f, ensure_ascii=False, indent=2, default=str)

        logger.debug(f"[SHAP AUDIT] Salvo em {filepath}")

    # ══════════════════════════════════════════════════════════════════════
    # HEURÍSTICA (FALLBACK — sem modelo treinado)
    # ══════════════════════════════════════════════════════════════════════

    _HEURISTIC_RECOVERY = {
        "insufficient_funds": 0.68,
        "expired_card": 0.45,
        "card_declined": 0.25,
        "processing_error": 0.81,
        "do_not_honor": 0.20,
        "generic_decline": 0.30,
    }

    def _heuristic_fallback(self, features: dict, channel: str) -> dict:
        """Fallback heurístico quando o modelo não foi treinado."""
        code = str(features.get("gateway_error_code", features.get("error_code", ""))).lower()
        # Mapear código Stripe para interno se necessário
        code = STRIPE_CODE_MAP.get(code, code)
        p_recovery = self._HEURISTIC_RECOVERY.get(code, 0.50)

        # Ajustar por tenure
        tenure = features.get("tenure_months", 0)
        if tenure > 12:
            p_recovery = min(p_recovery + 0.10, 0.95)
        elif tenure < 3:
            p_recovery = max(p_recovery - 0.10, 0.05)

        ltv = features.get("ltv_estimated", features.get("invoice_amount", features.get("amount", 100)) * 6)
        cost = INTERVENTION_COSTS.get(channel, INTERVENTION_COSTS["bot_whatsapp"])
        eprofit = round(p_recovery * ltv - cost, 2)

        return {
            "recovery_score": int(round(p_recovery * 100)),
            "p_recovery": round(p_recovery, 4),
            "eprofit": eprofit,
            "recommend_action": eprofit > 0,
            "channel": channel,
            "intervention_cost": cost,
            "ltv_estimated": round(ltv, 2),
            "optimal_channel": {"channel": channel, "eprofit": eprofit, "all_channels": {}},
            "shap_explanation": {"features": [], "readable": "Modelo não treinado — usando heurística"},
            "method": "heuristic",
            "xgb_proba": 0.0,
            "rf_proba": 0.0,
        }

    # ══════════════════════════════════════════════════════════════════════
    # PERSISTÊNCIA
    # ══════════════════════════════════════════════════════════════════════

    def _save_models(self):
        """Salva modelos treinados e encoders no disco."""
        MODELS_DIR.mkdir(parents=True, exist_ok=True)

        joblib.dump(self.xgb, MODELS_DIR / "xgb_failure_classifier.joblib")
        joblib.dump(self.rf, MODELS_DIR / "rf_failure_classifier.joblib")
        joblib.dump(self.label_encoders, MODELS_DIR / "label_encoders.joblib")
        joblib.dump(self.feature_names, MODELS_DIR / "feature_names.joblib")

        # Salvar métricas de treino
        with open(MODELS_DIR / "train_metrics.json", "w", encoding="utf-8") as f:
            json.dump(self._train_metrics, f, ensure_ascii=False, indent=2, default=str)

        print(f"[CLASSIFIER] Modelos salvos em {MODELS_DIR}/")

    def load(self) -> bool:
        """Carrega modelos pré-treinados do disco."""
        try:
            self.xgb = joblib.load(MODELS_DIR / "xgb_failure_classifier.joblib")
            self.rf = joblib.load(MODELS_DIR / "rf_failure_classifier.joblib")
            self.label_encoders = joblib.load(MODELS_DIR / "label_encoders.joblib")
            self.feature_names = joblib.load(MODELS_DIR / "feature_names.joblib")

            self._xgb_explainer = shap.TreeExplainer(self.xgb)
            self._rf_explainer = shap.TreeExplainer(self.rf)

            self.is_fitted = True
            print(f"[CLASSIFIER] Modelos carregados de {MODELS_DIR}/")
            return True
        except FileNotFoundError:
            print("[CLASSIFIER] Modelos não encontrados — execute train() primeiro")
            return False
        except Exception as e:
            print(f"[CLASSIFIER] Erro ao carregar modelos: {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════
# SCRIPT DE DEMONSTRAÇÃO
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    print("=" * 70)
    print("  CRAI -- Modulo 1: Failure Classifier (XGBoost + RF + SHAP)")
    print("=" * 70)

    clf = FailureClassifier()

    # Treinar
    metrics = clf.train(n_samples=3000)
    print(f"\n{'-' * 70}")
    print(f"  METRICAS DE TREINO")
    print(f"{'-' * 70}")
    print(f"  AUC:                 {metrics['auc']}")
    print(f"  Acuracia:            {metrics['accuracy']}")
    print(f"  Precision (rec=1):   {metrics['precision_recovered']}")
    print(f"  Recall (rec=1):      {metrics['recall_recovered']}")
    print(f"  F1 (rec=1):          {metrics['f1_recovered']}")
    print(f"  e-Profit medio:      R$ {metrics['avg_eprofit']:.2f}")
    print(f"  Clientes e-Profit>0: {metrics['n_positive_eprofit']}/{metrics['n_total_test']}")

    # Predicoes de exemplo
    test_cases = [
        {
            "name": "Cliente fiel, saldo insuficiente (alta recuperabilidade)",
            "features": {
                "tenure_months": 24, "day_of_month": 5, "invoice_amount": 299.90,
                "avg_ticket": 280.00, "gateway_error_code": "insufficient_funds",
                "card_brand": "visa", "payment_history_score": 0.92,
                "failure_count_90d": 0, "hour_of_day": 10, "day_of_week": 2,
                "attempt_count": 1, "ltv_estimated": 7200.00,
            },
        },
        {
            "name": "Cliente novo, cartao bloqueado (baixa recuperabilidade)",
            "features": {
                "tenure_months": 2, "day_of_month": 22, "invoice_amount": 599.00,
                "avg_ticket": 599.00, "gateway_error_code": "do_not_honor",
                "card_brand": "mastercard", "payment_history_score": 0.35,
                "failure_count_90d": 3, "hour_of_day": 15, "day_of_week": 5,
                "attempt_count": 3, "ltv_estimated": 1200.00,
            },
        },
        {
            "name": "Cliente medio, erro tecnico (recuperacao provavel)",
            "features": {
                "tenure_months": 8, "day_of_month": 10, "invoice_amount": 149.90,
                "avg_ticket": 155.00, "gateway_error_code": "processing_error",
                "card_brand": "elo", "payment_history_score": 0.75,
                "failure_count_90d": 1, "hour_of_day": 8, "day_of_week": 1,
                "attempt_count": 1, "ltv_estimated": 3600.00,
            },
        },
    ]

    for tc in test_cases:
        print(f"\n{'-' * 70}")
        print(f"  CASO: {tc['name']}")
        print(f"{'-' * 70}")
        result = clf.predict(tc["features"])
        print(f"  Score de recuperabilidade: {result['recovery_score']}/100")
        print(f"  P(recuperacao):            {result['p_recovery']:.4f}")
        print(f"  e-Profit ({result['channel']}): R$ {result['eprofit']:.2f}")
        print(f"  Recomendar acao:           {'SIM' if result['recommend_action'] else 'NAO'}")
        print(f"  Canal otimo:               {result['optimal_channel']['channel']} "
              f"(e-Profit R$ {result['optimal_channel']['eprofit']:.2f})")
        print(f"  SHAP: {result['shap_explanation']['readable']}")

    print(f"\n{'=' * 70}")
    print(f"  Logs SHAP salvos em: {LOGS_DIR}/")
    print(f"  Modelos salvos em:   {MODELS_DIR}/")
    print(f"{'=' * 70}")

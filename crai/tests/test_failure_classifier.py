"""
tests/test_failure_classifier.py — Testes unitários do Módulo 1 (FailureClassifier).

Cobre:
- Geração de dataset sintético
- Treino do ensemble XGBoost + RF
- Predição com score, e-Profit e SHAP
- Fallback heurístico (modelo não treinado)
- Persistência (salvar/carregar modelos)
- Reprodutibilidade (seed fixa)

O treino é redirecionado para um diretório temporário (MODELS_DIR monkeypatched),
para que a suíte não sobrescreva os modelos de produção em crai/models/ — um
modelo treinado aqui usa amostra pequena e degradaria a demo.
"""

import json
import pytest
import numpy as np
import pandas as pd
from pathlib import Path

from crai.ml.synthetic_data import generate_dataset, GATEWAY_ERROR_CODES, CARD_BRANDS
from crai.ml import failure_classifier as classifier_module
from crai.ml.failure_classifier import (
    FailureClassifier,
    INTERVENTION_COSTS,
    ALL_FEATURES,
    LOGS_DIR,
)


@pytest.fixture(scope="module")
def classificador_treinado(tmp_path_factory):
    """Treina uma vez, em dataset pequeno, salvando num diretório temporário."""
    models_dir = tmp_path_factory.mktemp("models")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(classifier_module, "MODELS_DIR", models_dir)
        clf = FailureClassifier()
        metrics = clf.train(n_samples=1000, test_size=0.2)
        yield clf, metrics, models_dir


# ══════════════════════════════════════════════════════════════════════════
# DATASET SINTÉTICO
# ══════════════════════════════════════════════════════════════════════════

class TestSyntheticData:
    """Testes para o gerador de dataset sintético."""

    def test_generate_correct_shape(self):
        """Dataset tem o número correto de linhas e colunas."""
        df = generate_dataset(n_samples=500)
        assert len(df) == 500
        assert "recovered" in df.columns
        assert "tenure_months" in df.columns
        assert "ltv_estimated" in df.columns

    def test_reproducibility_with_seed(self):
        """Seed fixa gera datasets idênticos."""
        df1 = generate_dataset(n_samples=100, seed=42)
        df2 = generate_dataset(n_samples=100, seed=42)
        pd.testing.assert_frame_equal(df1, df2)

    def test_different_seeds_differ(self):
        """Seeds diferentes geram datasets diferentes."""
        df1 = generate_dataset(n_samples=100, seed=42)
        df2 = generate_dataset(n_samples=100, seed=99)
        assert not df1.equals(df2)

    def test_tenure_distribution(self):
        """Tenure tem concentração em 0-12 meses e cauda longa."""
        df = generate_dataset(n_samples=3000)
        pct_under_12 = (df["tenure_months"] <= 12).mean()
        # Pelo menos 40% dos clientes com tenure <= 12 meses
        assert pct_under_12 > 0.40
        # Cauda longa: existem clientes com tenure > 24 meses
        assert df["tenure_months"].max() > 24

    def test_gateway_error_codes_valid(self):
        """Todos os códigos de erro são válidos."""
        df = generate_dataset(n_samples=1000)
        assert set(df["gateway_error_code"].unique()).issubset(set(GATEWAY_ERROR_CODES))

    def test_card_brands_valid(self):
        """Todas as bandeiras são válidas."""
        df = generate_dataset(n_samples=1000)
        assert set(df["card_brand"].unique()).issubset(set(CARD_BRANDS))

    def test_target_not_degenerate(self):
        """Target (recovered) não é 100% uma classe."""
        df = generate_dataset(n_samples=1000)
        recovery_rate = df["recovered"].mean()
        assert 0.10 < recovery_rate < 0.90

    def test_ltv_minimum(self):
        """LTV estimado é pelo menos o valor da fatura."""
        df = generate_dataset(n_samples=500)
        assert (df["ltv_estimated"] >= df["invoice_amount"]).all()

    def test_payment_history_score_range(self):
        """Score de histórico está entre 0 e 1."""
        df = generate_dataset(n_samples=500)
        assert df["payment_history_score"].between(0, 1).all()


# ══════════════════════════════════════════════════════════════════════════
# FAILURE CLASSIFIER — HEURÍSTICA
# ══════════════════════════════════════════════════════════════════════════

class TestFailureClassifierHeuristic:
    """Testes do fallback heurístico (modelo não treinado)."""

    def setup_method(self):
        self.clf = FailureClassifier()
        assert not self.clf.is_fitted

    def test_heuristic_returns_all_fields(self):
        """Fallback retorna todos os campos esperados."""
        result = self.clf.predict({
            "gateway_error_code": "insufficient_funds",
            "invoice_amount": 299.90,
            "ltv_estimated": 3000.00,
        })
        expected_keys = [
            "recovery_score", "p_recovery", "eprofit", "recommend_action",
            "channel", "intervention_cost", "ltv_estimated", "optimal_channel",
            "shap_explanation", "method", "xgb_proba", "rf_proba",
        ]
        for key in expected_keys:
            assert key in result, f"Campo '{key}' ausente no resultado"

    def test_heuristic_method_tag(self):
        """Fallback marca method como 'heuristic'."""
        result = self.clf.predict({"gateway_error_code": "expired_card"})
        assert result["method"] == "heuristic"

    def test_heuristic_eprofit_calculation(self):
        """e-Profit = P_recovery * LTV - custo."""
        result = self.clf.predict({
            "gateway_error_code": "processing_error",
            "ltv_estimated": 1000.00,
        }, channel="bot_whatsapp")
        expected = round(result["p_recovery"] * 1000.00 - INTERVENTION_COSTS["bot_whatsapp"], 2)
        assert result["eprofit"] == expected

    def test_heuristic_tenure_adjustment(self):
        """Tenure alto aumenta score, tenure baixo diminui."""
        result_high = self.clf.predict({
            "gateway_error_code": "insufficient_funds", "tenure_months": 36,
        })
        result_low = self.clf.predict({
            "gateway_error_code": "insufficient_funds", "tenure_months": 1,
        })
        assert result_high["recovery_score"] > result_low["recovery_score"]


# ══════════════════════════════════════════════════════════════════════════
# FAILURE CLASSIFIER — TREINO E PREDIÇÃO
# ══════════════════════════════════════════════════════════════════════════

class TestFailureClassifierTrained:
    """Testes com modelo treinado."""

    def test_is_fitted_after_train(self, classificador_treinado):
        """Modelo está marcado como treinado."""
        clf, _, _ = classificador_treinado
        assert clf.is_fitted

    def test_auc_above_threshold(self, classificador_treinado):
        """AUC deve ser razoável (> 0.60) mesmo com dados sintéticos."""
        _, metrics, _ = classificador_treinado
        assert metrics["auc"] > 0.60

    def test_predict_returns_ensemble(self, classificador_treinado):
        """Predição usa ensemble, não heurística."""
        clf, _, _ = classificador_treinado
        result = clf.predict({
            "tenure_months": 12, "day_of_month": 10, "invoice_amount": 200.00,
            "avg_ticket": 200.00, "gateway_error_code": "insufficient_funds",
            "card_brand": "visa", "payment_history_score": 0.80,
            "failure_count_90d": 1, "hour_of_day": 10, "day_of_week": 2,
            "attempt_count": 1, "ltv_estimated": 4800.00,
        })
        assert result["method"] == "ensemble_xgb_rf"

    def test_recovery_score_range(self, classificador_treinado):
        """Score de recuperabilidade está entre 0 e 100."""
        clf, _, _ = classificador_treinado
        result = clf.predict({
            "tenure_months": 6, "day_of_month": 15, "invoice_amount": 300.00,
            "avg_ticket": 300.00, "gateway_error_code": "card_declined",
            "card_brand": "mastercard", "payment_history_score": 0.50,
            "failure_count_90d": 2, "hour_of_day": 14, "day_of_week": 3,
            "attempt_count": 2, "ltv_estimated": 2000.00,
        })
        assert 0 <= result["recovery_score"] <= 100

    def test_eprofit_formula(self, classificador_treinado):
        """e-Profit = P_recovery * LTV - custo do canal."""
        clf, _, _ = classificador_treinado
        features = {
            "tenure_months": 12, "day_of_month": 5, "invoice_amount": 150.00,
            "avg_ticket": 150.00, "gateway_error_code": "processing_error",
            "card_brand": "visa", "payment_history_score": 0.85,
            "failure_count_90d": 0, "hour_of_day": 9, "day_of_week": 1,
            "attempt_count": 1, "ltv_estimated": 5000.00,
        }
        result = clf.predict(features, channel="email_auto")
        expected = round(result["p_recovery"] * 5000.00 - INTERVENTION_COSTS["email_auto"], 2)
        assert abs(result["eprofit"] - expected) <= 0.02

    def test_shap_explanation_present(self, classificador_treinado):
        """Predição inclui explicação SHAP com features."""
        clf, _, _ = classificador_treinado
        result = clf.predict({
            "tenure_months": 18, "day_of_month": 10, "invoice_amount": 250.00,
            "avg_ticket": 250.00, "gateway_error_code": "insufficient_funds",
            "card_brand": "elo", "payment_history_score": 0.70,
            "failure_count_90d": 1, "hour_of_day": 11, "day_of_week": 2,
            "attempt_count": 1, "ltv_estimated": 4000.00,
        })
        shap_exp = result["shap_explanation"]
        assert "features" in shap_exp
        assert "readable" in shap_exp
        assert len(shap_exp["features"]) == len(ALL_FEATURES)
        # Cada feature tem os campos obrigatórios
        for feat in shap_exp["features"]:
            assert "feature" in feat
            assert "shap_value" in feat
            assert "contribution_pct" in feat
            assert "direction" in feat

    def test_shap_readable_not_empty(self, classificador_treinado):
        """Explicação SHAP em texto não é vazia."""
        clf, _, _ = classificador_treinado
        result = clf.predict({
            "tenure_months": 24, "day_of_month": 5, "invoice_amount": 299.90,
            "avg_ticket": 280.00, "gateway_error_code": "insufficient_funds",
            "card_brand": "visa", "payment_history_score": 0.92,
            "failure_count_90d": 0, "hour_of_day": 10, "day_of_week": 2,
            "attempt_count": 1, "ltv_estimated": 7200.00,
        })
        assert len(result["shap_explanation"]["readable"]) > 10

    def test_optimal_channel_selected(self, classificador_treinado):
        """Canal ótimo é calculado para todas as opções."""
        clf, _, _ = classificador_treinado
        result = clf.predict({
            "tenure_months": 20, "day_of_month": 10, "invoice_amount": 400.00,
            "avg_ticket": 400.00, "gateway_error_code": "insufficient_funds",
            "card_brand": "visa", "payment_history_score": 0.80,
            "failure_count_90d": 0, "hour_of_day": 10, "day_of_week": 1,
            "attempt_count": 1, "ltv_estimated": 6000.00,
        })
        opt = result["optimal_channel"]
        assert "channel" in opt
        assert "all_channels" in opt
        assert len(opt["all_channels"]) == len(INTERVENTION_COSTS)

    def test_high_recovery_client(self, classificador_treinado):
        """Cliente fiel com erro técnico deve ter score alto e e-Profit positivo."""
        clf, _, _ = classificador_treinado
        result = clf.predict({
            "tenure_months": 36, "day_of_month": 5, "invoice_amount": 199.90,
            "avg_ticket": 200.00, "gateway_error_code": "processing_error",
            "card_brand": "visa", "payment_history_score": 0.95,
            "failure_count_90d": 0, "hour_of_day": 10, "day_of_week": 2,
            "attempt_count": 1, "ltv_estimated": 8000.00,
        })
        assert result["recovery_score"] > 50
        assert result["eprofit"] > 0
        assert result["recommend_action"] is True

    def test_negative_eprofit_no_action(self, classificador_treinado):
        """e-Profit negativo com ligação CS (custo alto) → não recomendar."""
        clf, _, _ = classificador_treinado
        result = clf.predict({
            "tenure_months": 1, "day_of_month": 22, "invoice_amount": 49.90,
            "avg_ticket": 49.90, "gateway_error_code": "do_not_honor",
            "card_brand": "hipercard", "payment_history_score": 0.20,
            "failure_count_90d": 5, "hour_of_day": 22, "day_of_week": 6,
            "attempt_count": 4, "ltv_estimated": 50.00,
        }, channel="ligacao_cs")
        # Com LTV R$50 e custo R$15, e-Profit provavelmente negativo
        if result["eprofit"] <= 0:
            assert result["recommend_action"] is False


# ══════════════════════════════════════════════════════════════════════════
# PERSISTÊNCIA
# ══════════════════════════════════════════════════════════════════════════

class TestPersistence:
    """Testes de salvar e carregar modelos."""

    def test_models_saved_on_disk(self, classificador_treinado):
        """Modelos são salvos no disco após treino."""
        _, _, models_dir = classificador_treinado
        assert (models_dir / "xgb_failure_classifier.joblib").exists()
        assert (models_dir / "rf_failure_classifier.joblib").exists()
        assert (models_dir / "label_encoders.joblib").exists()
        assert (models_dir / "train_metrics.json").exists()

    def test_load_and_predict(self, classificador_treinado):
        """Modelo carregado do disco produz mesma predição."""
        clf, _, _ = classificador_treinado

        # Predição original
        features = {
            "tenure_months": 12, "day_of_month": 10, "invoice_amount": 200.00,
            "avg_ticket": 200.00, "gateway_error_code": "insufficient_funds",
            "card_brand": "visa", "payment_history_score": 0.80,
            "failure_count_90d": 1, "hour_of_day": 10, "day_of_week": 2,
            "attempt_count": 1, "ltv_estimated": 4800.00,
        }
        original = clf.predict(features)

        # Carregar em nova instância
        clf2 = FailureClassifier()
        assert clf2.load()
        loaded = clf2.predict(features)

        assert original["recovery_score"] == loaded["recovery_score"]
        assert original["p_recovery"] == loaded["p_recovery"]

    def test_audit_logs_created(self, classificador_treinado):
        """Logs de auditoria SHAP são criados após predição."""
        clf, _, _ = classificador_treinado
        clf.predict({
            "tenure_months": 6, "day_of_month": 15, "invoice_amount": 300.00,
            "avg_ticket": 300.00, "gateway_error_code": "card_declined",
            "card_brand": "mastercard", "payment_history_score": 0.50,
            "failure_count_90d": 2, "hour_of_day": 14, "day_of_week": 3,
            "attempt_count": 2, "ltv_estimated": 2000.00,
        })
        logs = list(LOGS_DIR.glob("shap_audit_*.json"))
        assert len(logs) > 0

        # Verificar estrutura do log
        with open(logs[-1], "r", encoding="utf-8") as f:
            log = json.load(f)
        assert "timestamp" in log
        assert "input_features" in log
        assert "output" in log
        assert "shap_explanation" in log

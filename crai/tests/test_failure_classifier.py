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

# Símbolos introduzidos pelo trabalho do limiar. Resolvidos em tempo de
# execução, e não no `import` acima, para que este arquivo CONTINUE COLETANDO
# no commit anterior — senão os 26 testes pré-existentes daqui morrem por
# ImportError num baseline e nenhuma comparação de regressão vale.
# Mesma disciplina já aplicada em tests/test_payment_gateway.py.
LIMIAR_CLASSIFICACAO = getattr(classifier_module, "LIMIAR_CLASSIFICACAO", 0.25)
RECALL_MINIMO = getattr(classifier_module, "RECALL_MINIMO", 0.90)
LIMIARES_REPORTADOS = getattr(
    classifier_module, "LIMIARES_REPORTADOS", (0.50, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15))


def escolher_limiar(*args, **kwargs):
    fn = getattr(classifier_module, "escolher_limiar", None)
    if fn is None:
        pytest.skip("escolher_limiar não existe neste commit")
    return fn(*args, **kwargs)


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


# ══════════════════════════════════════════════════════════════════════════
# LIMIAR DO RELATÓRIO — a justificativa tem que ser executável
#
# Nasceram com a re-auditoria A1-r2, que encontrou o commit 4109d84 afirmando
# no comentário que 0,25 "maximiza F2" quando a máquina mede F2 crescendo
# monotonicamente até o fim da grade. Um número documentado que o código
# desmente é pior que um número sem documentação: o primeiro convence a banca.
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def classificador_declarado(tmp_path_factory):
    """Treina na configuração que a documentação DECLARA: 15.000, seed 42.

    A fixture barata (`classificador_treinado`, 1.000 linhas) não serve para
    validar `LIMIAR_CLASSIFICACAO`: a curva de recall depende do tamanho do
    dataset, e com 1.000 linhas a regra escolhe 0,20. Validar a constante
    contra um dataset diferente do declarado seria repetir o defeito que a
    re-auditoria encontrou — um número justificado por uma medição que não é a
    que o comentário descreve.

    Custa ~30s. É o preço de a documentação ser verificável.
    """
    models_dir = tmp_path_factory.mktemp("models_declarado")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(classifier_module, "MODELS_DIR", models_dir)
        clf = FailureClassifier()
        yield clf.train(n_samples=15000, test_size=0.2)


class TestEscolhaDoLimiar:
    """O limiar do relatório sai de uma regra declarada, não de um argmax."""

    def test_a_regra_declarada_devolve_a_constante(self, classificador_declarado):
        """`escolher_limiar` sobre a varredura real tem que dar LIMIAR_CLASSIFICACAO.

        Este é o teste que impede a documentação de mentir: se alguém mudar
        LIMIAR_CLASSIFICACAO sem refazer a análise, ou mudar o modelo a ponto de
        deslocar a curva de recall, isto quebra.
        """
        metrics = classificador_declarado
        escolhido = escolher_limiar(metrics["metricas_por_limiar"])
        assert escolhido == LIMIAR_CLASSIFICACAO, (
            f"a regra declarada (maior limiar com recall >= {RECALL_MINIMO}) "
            f"escolhe {escolhido}, mas a constante é {LIMIAR_CLASSIFICACAO}")

    def test_o_limiar_em_uso_sustenta_o_recall_minimo(self, classificador_declarado):
        metrics = classificador_declarado
        linha = next(m for m in metrics["metricas_por_limiar"] if m["em_uso"])
        assert linha["limiar"] == LIMIAR_CLASSIFICACAO
        assert linha["recall"] >= RECALL_MINIMO

    def test_nenhum_limiar_maior_sustenta_o_recall_minimo(self, classificador_declarado):
        """É o *maior* que passa — senão o relatório seria conservador à toa."""
        metrics = classificador_declarado
        maiores = [m for m in metrics["metricas_por_limiar"]
                   if m["limiar"] > LIMIAR_CLASSIFICACAO]
        assert maiores, "grade sem nenhum limiar acima do escolhido"
        assert all(m["recall"] < RECALL_MINIMO for m in maiores)

    def test_a_varredura_cobre_a_grade_declarada(self, classificador_treinado):
        """A tabela reportada tem que ser a grade inteira, sem furos.

        (Substituiu um teste de monotonicidade do recall, que a auditoria A1-r3
        apontou como tautológico: baixar o limiar só pode aumentar o conjunto
        de positivos, então recall não-crescente é verdade por construção, não
        uma propriedade do código.)
        """
        _, metrics, _ = classificador_treinado
        limiares = sorted(m["limiar"] for m in metrics["metricas_por_limiar"])
        assert limiares == sorted(LIMIARES_REPORTADOS)
        assert sum(m["em_uso"] for m in metrics["metricas_por_limiar"]) == 1

    def test_regra_devolve_none_quando_nada_alcanca_o_minimo(self):
        """Sem limiar aceitável, devolve None — não arredonda o critério."""
        varredura = [{"limiar": 0.5, "recall": 0.10},
                     {"limiar": 0.2, "recall": 0.40}]
        assert escolher_limiar(varredura, recall_minimo=0.90) is None

    def test_f2_nao_e_usado_para_escolher_o_limiar(self, classificador_treinado):
        """O F2 máximo da grade NÃO é o limiar em uso — e isso é deliberado.

        Com taxa base ~0,45, o classificador trivial ("recupera" para todos)
        tem F2 ~0,80. Maximizar F2 selecionaria o corte que não classifica.
        Este teste documenta que a implementação sabe disso.
        """
        _, metrics, _ = classificador_treinado
        melhor_f2 = max(metrics["metricas_por_limiar"], key=lambda m: m["f2"])
        assert melhor_f2["limiar"] <= LIMIAR_CLASSIFICACAO


class TestLimiarNaoDecideNada:
    """O limiar é do relatório. Quem decide é o e-Profit."""

    def test_predict_nao_usa_o_limiar(self, classificador_treinado, monkeypatch):
        """Mudar LIMIAR_CLASSIFICACAO não muda nenhuma saída de `predict`."""
        clf, _, _ = classificador_treinado
        caso = {
            "tenure_months": 6, "day_of_month": 15, "invoice_amount": 300.00,
            "avg_ticket": 300.00, "gateway_error_code": "insufficient_funds",
            "card_brand": "visa", "payment_history_score": 0.60,
            "failure_count_90d": 1, "hour_of_day": 10, "day_of_week": 2,
            "attempt_count": 1, "ltv_estimated": 2000.00,
        }
        antes = clf.predict(caso)
        monkeypatch.setattr(classifier_module, "LIMIAR_CLASSIFICACAO", 0.99)
        depois = clf.predict(caso)
        assert antes["recovery_score"] == depois["recovery_score"]
        assert antes["eprofit"] == depois["eprofit"]

    def test_recall_operacional_e_reportado_junto(self, classificador_treinado):
        """A métrica que descreve o sistema de verdade não pode ficar de fora."""
        _, metrics, _ = classificador_treinado
        op = metrics["recall_operacional"]
        assert "route_after_diagnosis" in op["regra"]
        assert 0.0 <= op["recall"] <= 1.0
        assert op["recuperaveis_perdidos"] >= 0
        assert op["clientes_abandonados"] <= op["n_total"]

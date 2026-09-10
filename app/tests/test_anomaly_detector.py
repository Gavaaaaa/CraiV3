"""
tests/test_anomaly_detector.py — Testes unitários do Módulo 2 (AnomalyDetector).

Cobre:
- Geração do dataset comportamental sintético
- Fallback heurístico (modelo não treinado)
- Treino do autoencoder em dataset pequeno (200 clientes)
- Persistência: artefatos salvos são recarregáveis via load()
- Inferência com o modelo treinado (erro de reconstrução + top features)

O treino é redirecionado para um diretório temporário (MODELS_DIR monkeypatched),
para que a suíte não sobrescreva os modelos de produção em crai/models/.
"""

import json

import pytest

from crai.ml import anomaly_detector as anomaly_module
from crai.ml.anomaly_detector import TORCH_AVAILABLE, AnomalyDetector
from crai.ml.synthetic_data import BEHAVIORAL_FEATURES, generate_behavioral_dataset

requer_torch = pytest.mark.skipif(not TORCH_AVAILABLE, reason="PyTorch não instalado")


# ══════════════════════════════════════════════════════════════════════════
# DATASET COMPORTAMENTAL SINTÉTICO
# ══════════════════════════════════════════════════════════════════════════

class TestBehavioralDataset:
    """Testes do gerador de dataset comportamental."""

    def test_generate_correct_shape(self):
        """Dataset tem o número correto de linhas e todas as features da rede."""
        df = generate_behavioral_dataset(n_samples=500)
        assert len(df) == 500
        for feature in BEHAVIORAL_FEATURES:
            assert feature in df.columns, f"Feature '{feature}' ausente"
        assert "is_anomalous" in df.columns
        assert "customer_id" in df.columns

    def test_anomaly_rate_respected(self):
        """A fração de anômalos segue o parâmetro anomaly_rate."""
        df = generate_behavioral_dataset(n_samples=1000, anomaly_rate=0.09)
        assert df["is_anomalous"].sum() == 90

    def test_reproducibility_with_seed(self):
        """Seed fixa gera datasets idênticos."""
        df1 = generate_behavioral_dataset(n_samples=200, seed=42)
        df2 = generate_behavioral_dataset(n_samples=200, seed=42)
        assert df1.equals(df2)

    def test_different_seeds_differ(self):
        """Seeds diferentes geram datasets diferentes."""
        df1 = generate_behavioral_dataset(n_samples=200, seed=42)
        df2 = generate_behavioral_dataset(n_samples=200, seed=99)
        assert not df1.equals(df2)

    def test_anomalous_population_is_degraded(self):
        """Anômalos usam menos o produto e geram mais atrito que saudáveis."""
        df = generate_behavioral_dataset(n_samples=2000)
        saudaveis = df[df["is_anomalous"] == 0]
        anomalos = df[df["is_anomalous"] == 1]

        assert anomalos["logins_7d"].mean() < saudaveis["logins_7d"].mean()
        assert anomalos["feature_adoption"].mean() < saudaveis["feature_adoption"].mean()
        assert anomalos["nps_last"].mean() < saudaveis["nps_last"].mean()
        assert anomalos["tickets_30d"].mean() > saudaveis["tickets_30d"].mean()
        assert anomalos["days_since_last_login"].mean() > saudaveis["days_since_last_login"].mean()

    def test_no_missing_values(self):
        """Nenhuma feature vem com NaN (a rede não tolera)."""
        df = generate_behavioral_dataset(n_samples=500)
        assert not df[BEHAVIORAL_FEATURES].isna().any().any()


# ══════════════════════════════════════════════════════════════════════════
# FALLBACK HEURÍSTICO (COLD START)
# ══════════════════════════════════════════════════════════════════════════

class TestAnomalyDetectorHeuristic:
    """Testes do cold start — sem modelo treinado."""

    def setup_method(self):
        self.detector = AnomalyDetector()

    def test_not_fitted_on_init(self):
        """Detector nasce sem modelo."""
        assert not self.detector.is_fitted

    @pytest.mark.asyncio
    async def test_heuristic_returns_all_fields(self):
        """Fallback devolve o mesmo contrato do autoencoder."""
        event = {"data": {"object": {"amount": 29990, "failure_code": "insufficient_funds",
                                     "failure_message": "saldo", "attempt_count": 1}}}
        result = await self.detector.check("cus_teste", event)

        for key in ["is_anomaly", "error", "threshold", "method", "top_features"]:
            assert key in result, f"Campo '{key}' ausente no resultado"
        assert result["method"] == "heuristic"
        assert isinstance(result["is_anomaly"], bool)


# ══════════════════════════════════════════════════════════════════════════
# TREINO E PERSISTÊNCIA
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def detector_treinado(tmp_path_factory):
    """Treina uma vez, em dataset pequeno, salvando num diretório temporário."""
    models_dir = tmp_path_factory.mktemp("models")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(anomaly_module, "MODELS_DIR", models_dir)
        detector = AnomalyDetector()
        metrics = detector.train(n_samples=200, epochs=30, batch_size=32, patience=30)
        yield detector, metrics, models_dir


@requer_torch
class TestAnomalyDetectorTrain:
    """Testes do método train()."""

    def test_is_fitted_after_train(self, detector_treinado):
        """Modelo fica utilizável logo após o treino, sem precisar de load()."""
        detector, _, _ = detector_treinado
        assert detector.is_fitted
        assert detector.model is not None
        assert detector.scaler is not None
        assert detector.threshold is not None

    def test_metrics_keys_present(self, detector_treinado):
        """train() devolve dict com as métricas esperadas."""
        _, metrics, _ = detector_treinado
        for key in ["roc_auc", "average_precision", "precision", "recall", "f1",
                    "threshold", "separation_ratio", "epochs_trained", "best_val_loss"]:
            assert key in metrics, f"Métrica '{key}' ausente"

    def test_separates_healthy_from_anomalous(self, detector_treinado):
        """Mesmo com 200 clientes, o erro de reconstrução separa as populações."""
        _, metrics, _ = detector_treinado
        assert metrics["roc_auc"] > 0.70
        assert metrics["mean_error_anomalous"] > metrics["mean_error_healthy"]
        assert metrics["separation_ratio"] > 1.0

    def test_trained_only_on_healthy(self, detector_treinado):
        """Anômalos ficam fora do treino — só entram na avaliação."""
        _, metrics, _ = detector_treinado
        assert metrics["n_train_healthy"] + metrics["n_val_healthy"] == 200 - metrics["n_anomalous"]

    def test_artifacts_saved_on_disk(self, detector_treinado):
        """Os três artefatos que load() espera são gravados."""
        _, _, models_dir = detector_treinado
        assert (models_dir / "autoencoder.pt").exists()
        assert (models_dir / "autoencoder_scaler.pkl").exists()
        assert (models_dir / "autoencoder_meta.json").exists()

    def test_meta_json_has_load_contract(self, detector_treinado):
        """meta.json traz exatamente os campos lidos por load()."""
        _, _, models_dir = detector_treinado
        with open(models_dir / "autoencoder_meta.json", encoding="utf-8") as f:
            meta = json.load(f)
        for key in ["features", "input_dim", "bottleneck", "threshold"]:
            assert key in meta, f"Campo '{key}' ausente no meta.json"
        assert meta["features"] == BEHAVIORAL_FEATURES
        assert meta["input_dim"] == len(BEHAVIORAL_FEATURES)

    def test_load_recovers_model(self, detector_treinado):
        """Nova instância recarrega o modelo salvo com o mesmo threshold."""
        detector, _, _ = detector_treinado
        recarregado = AnomalyDetector()
        assert recarregado.load()
        assert recarregado.is_fitted
        assert recarregado.threshold == pytest.approx(detector.threshold)
        assert recarregado.features == detector.features

    @pytest.mark.asyncio
    async def test_check_uses_autoencoder(self, detector_treinado):
        """Com modelo treinado, check() usa o autoencoder e explica o erro."""
        detector, _, _ = detector_treinado
        event = {"data": {"object": {"amount": 29990, "failure_code": "insufficient_funds",
                                     "attempt_count": 1}}}
        result = await detector.check("cus_teste_001", event)

        assert result["method"] == "autoencoder"
        assert result["error"] >= 0
        assert result["threshold"] == pytest.approx(detector.threshold)
        assert len(result["top_features"]) == 3
        for item in result["top_features"]:
            assert item["feature"] in BEHAVIORAL_FEATURES
            assert "contribuicao" in item

    @pytest.mark.asyncio
    async def test_load_and_check_match(self, detector_treinado):
        """Modelo recarregado produz o mesmo erro de reconstrução do original."""
        detector, _, _ = detector_treinado
        recarregado = AnomalyDetector()
        assert recarregado.load()

        event = {"data": {"object": {"amount": 19990, "attempt_count": 1}}}
        original = await detector.check("cus_comparacao", event)
        depois = await recarregado.check("cus_comparacao", event)

        assert original["error"] == pytest.approx(depois["error"])
        assert original["is_anomaly"] == depois["is_anomaly"]

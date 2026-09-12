"""tests/test_train_fonte.py — `train(fonte=...)` nos três módulos + candidato voluntário.

O que se trava aqui (gate da Etapa B do trabalho de base de dados):

  (a) `fonte="sintetico"` (default) treina EXATAMENTE como hoje: chamar
      `train()` sem o parâmetro ou com `fonte="sintetico"` dá as mesmas
      métricas — o parâmetro novo não muda o caso comum.
  (b) `fonte="sintetico_calibrado"` roda sem erro nos três módulos e devolve
      `fonte_usada`, `n_amostras`, `proveniencia` (ancoradas em real vs.
      proxy fraco vs. sintéticas puras) e `versoes` (versão exata de cada
      biblioteca do treino).
  (c) Todo artefato salvo recarrega via `load()` e o `meta.json` gravado ao
      lado carrega os mesmos campos.
  (d) `requirements.txt` fixa versão EXATA (`==`) de scikit-learn, xgboost e
      torch, e o meta.json registra a versão de fato usada — uma divergência
      vira aviso nominal no `load()`, não erro genérico.
  (e) O candidato do risk_scorer voluntário é treinado e recarregável, mas
      NÃO é o arquivo que o scorer lê: sem `ativar()`, `calculate_risk`
      continua nas regras fixas. Com `ativar()`, o modelo passa a decidir.

Todo treino aqui vai para `tmp_path` (MODELS_DIR monkeypatched), como nos
demais testes de ML — nunca sobrescreve `models/` de verdade.

Uso:
    pytest tests/test_train_fonte.py -v
"""

import json
import re
from pathlib import Path

import pytest

from crai.ml import anomaly_detector as anomaly_module
from crai.ml import calibracao
from crai.ml import failure_classifier as classifier_module
from crai.ml import payday_inference as payday_module
from crai.ml import voluntary_risk as voluntary_module
from crai.churn_voluntary import risk_scorer as rs
from crai.ml.anomaly_detector import TORCH_AVAILABLE, AnomalyDetector
from crai.ml.failure_classifier import FailureClassifier
from crai.ml.payday_inference import PROPHET_AVAILABLE, PaydayInference
from crai.ml.voluntary_risk import VoluntaryRiskModel

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
REQUIREMENTS = RAIZ_PROJETO / "requirements.txt"

requer_calibracao = pytest.mark.skipif(
    not calibracao.CALIBRACAO_PATH.exists(),
    reason="models/calibracao.json ausente — rode preparar_amostra_real",
)
requer_torch = pytest.mark.skipif(not TORCH_AVAILABLE, reason="PyTorch não instalado")
requer_prophet = pytest.mark.skipif(not (TORCH_AVAILABLE and PROPHET_AVAILABLE),
                                    reason="torch/prophet não instalados")

CAMPOS_DE_RASTREIO = ("fonte_usada", "n_amostras", "proveniencia", "versoes", "treinado_em")


def _confere_rastreio(metrics: dict, modelo: str, fonte: str, n: int):
    for campo in CAMPOS_DE_RASTREIO:
        assert campo in metrics, f"train() sem `{campo}`"
    assert metrics["fonte_usada"] == fonte
    assert metrics["n_amostras"] == n
    prov = metrics["proveniencia"]
    assert prov["modelo"] == modelo and prov["fonte"] == fonte
    for chave in ("ancoradas_em_real", "proxy_fraco", "sinteticas_puras", "por_status"):
        assert chave in prov
    assert sum(prov["por_status"].values()) == prov["n_features"]
    for lib in ("scikit-learn", "xgboost", "torch", "python"):
        assert lib in metrics["versoes"]


# ══════════════════════════════════════════════════════════════════════════
# (a) DEFAULT IDÊNTICO AO DE HOJE
# ══════════════════════════════════════════════════════════════════════════

class TestDefaultIdentico:

    def test_classifier_sem_fonte_e_com_sintetico_dao_o_mesmo(self, tmp_path, monkeypatch):
        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path / "a")
        a = FailureClassifier().train(n_samples=600)
        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path / "b")
        b = FailureClassifier().train(n_samples=600, fonte="sintetico")
        assert a["auc"] == b["auc"]
        assert a["confusion_matrix"] == b["confusion_matrix"]
        assert a["fonte_usada"] == "sintetico"
        assert a["proveniencia"]["ancoradas_em_real"] == []

    def test_fonte_invalida_e_recusada_antes_de_treinar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path)
        with pytest.raises(ValueError):
            FailureClassifier().train(n_samples=100, fonte="dado_real")
        assert not (tmp_path / "xgb_failure_classifier.joblib").exists()


# ══════════════════════════════════════════════════════════════════════════
# (b)+(c) CALIBRADO — roda, devolve rastreio, salva meta, recarrega
# ══════════════════════════════════════════════════════════════════════════

@requer_calibracao
class TestClassifierCalibrado:

    @pytest.fixture(scope="class")
    def treinado(self, tmp_path_factory):
        models_dir = tmp_path_factory.mktemp("clf_calibrado")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(classifier_module, "MODELS_DIR", models_dir)
            clf = FailureClassifier()
            metrics = clf.train(n_samples=800, fonte="sintetico_calibrado")
            yield clf, metrics, models_dir

    def test_rastreio(self, treinado):
        _, metrics, _ = treinado
        _confere_rastreio(metrics, "FailureClassifier", "sintetico_calibrado", 800)
        assert "invoice_amount" in metrics["proveniencia"]["ancoradas_em_real"]
        assert "tenure_months" in metrics["proveniencia"]["sinteticas_puras"]

    def test_meta_json_gravado(self, treinado):
        _, metrics, models_dir = treinado
        meta = json.loads((models_dir / "failure_classifier_meta.json").read_text(encoding="utf-8"))
        assert meta["fonte_usada"] == "sintetico_calibrado"
        assert meta["n_amostras"] == 800
        assert meta["versoes"] == metrics["versoes"]
        assert meta["features"] == classifier_module.ALL_FEATURES

    def test_recarrega_e_prediz(self, treinado, monkeypatch):
        clf, _, models_dir = treinado
        monkeypatch.setattr(classifier_module, "MODELS_DIR", models_dir)
        clf2 = FailureClassifier()
        assert clf2.load()
        assert clf2.meta["fonte_usada"] == "sintetico_calibrado"
        caso = {"tenure_months": 12, "day_of_month": 10, "invoice_amount": 90.0,
                "avg_ticket": 90.0, "gateway_error_code": "insufficient_funds",
                "card_brand": "visa", "payment_history_score": 0.8, "failure_count_90d": 1,
                "hour_of_day": 10, "day_of_week": 2, "attempt_count": 1, "ltv_estimated": 900.0}
        assert clf.predict(caso)["p_recovery"] == clf2.predict(caso)["p_recovery"]


@requer_calibracao
@requer_torch
class TestAnomalyCalibrado:

    @pytest.fixture(scope="class")
    def treinado(self, tmp_path_factory):
        models_dir = tmp_path_factory.mktemp("ae_calibrado")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(anomaly_module, "MODELS_DIR", models_dir)
            det = AnomalyDetector()
            metrics = det.train(n_samples=300, epochs=20, batch_size=32, patience=20,
                                fonte="sintetico_calibrado")
            yield det, metrics, models_dir

    def test_rastreio(self, treinado):
        _, metrics, _ = treinado
        _confere_rastreio(metrics, "AnomalyDetector", "sintetico_calibrado", 300)
        assert "days_since_last_login" in metrics["proveniencia"]["ancoradas_em_real"]
        assert set(metrics["proveniencia"]["proxy_fraco"]) >= {"avg_session_min", "tickets_30d", "nps_last"}

    def test_meta_json_gravado(self, treinado):
        _, metrics, models_dir = treinado
        meta = json.loads((models_dir / "autoencoder_meta.json").read_text(encoding="utf-8"))
        assert meta["fonte_usada"] == "sintetico_calibrado"
        assert meta["n_amostras"] == 300
        assert meta["versoes"]["torch"] == metrics["versoes"]["torch"]

    @pytest.mark.asyncio
    async def test_recarrega_e_snapshot_segue_a_fonte(self, treinado, monkeypatch):
        det, _, models_dir = treinado
        monkeypatch.setattr(anomaly_module, "MODELS_DIR", models_dir)
        det2 = AnomalyDetector()
        assert det2.load()
        assert det2._fonte == "sintetico_calibrado"
        r1 = await det.check("cli_calibrado_001", {})
        r2 = await det2.check("cli_calibrado_001", {})
        assert r1["method"] == "autoencoder"
        assert r1["error"] == pytest.approx(r2["error"])
        # Com o modelo calibrado, o snapshot vem da distribuição calibrada:
        # a maioria dos clientes (85% saudáveis) NÃO pode virar anomalia.
        flags = [(await det2.check(f"cli_{i:04d}", {}))["is_anomaly"] for i in range(60)]
        assert sum(flags) < 30


@requer_calibracao
@requer_prophet
class TestPaydayCalibrado:

    @pytest.fixture(scope="class")
    def treinado(self, tmp_path_factory):
        models_dir = tmp_path_factory.mktemp("payday_calibrado")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(payday_module, "MODELS_DIR", models_dir)
            pay = PaydayInference()
            metrics = pay.train(n_samples=40, n_days=120, epochs=2, patience=2,
                                fonte="sintetico_calibrado")
            yield pay, metrics, models_dir

    def test_rastreio_e_100_por_cento_sintetico(self, treinado):
        _, metrics, _ = treinado
        _confere_rastreio(metrics, "PaydayInference", "sintetico_calibrado", 40)
        assert metrics["proveniencia"]["ancoradas_em_real"] == []
        assert metrics["proveniencia"]["por_status"]["sintetica_sem_doador"] > 0

    def test_meta_json_e_load(self, treinado, monkeypatch):
        _, metrics, models_dir = treinado
        meta = json.loads((models_dir / "payday_meta.json").read_text(encoding="utf-8"))
        assert meta["fonte_usada"] == "sintetico_calibrado" and meta["n_amostras"] == 40
        assert meta["versoes"]["prophet"] == metrics["versoes"]["prophet"]
        monkeypatch.setattr(payday_module, "MODELS_DIR", models_dir)
        pay2 = PaydayInference()
        assert pay2.load()
        assert pay2.meta["fonte_usada"] == "sintetico_calibrado"


# ══════════════════════════════════════════════════════════════════════════
# (d) VERSIONAMENTO
# ══════════════════════════════════════════════════════════════════════════

class TestVersionamento:

    @pytest.mark.parametrize("pacote", ["scikit-learn", "xgboost", "torch"])
    def test_requirements_fixa_versao_exata(self, pacote):
        texto = REQUIREMENTS.read_text(encoding="utf-8")
        linhas = [l.strip() for l in texto.splitlines()
                  if re.match(rf"^{re.escape(pacote)}\s*[=<>~!]", l.strip())]
        assert linhas, f"{pacote} nao esta em requirements.txt"
        for linha in linhas:
            assert re.match(rf"^{re.escape(pacote)}==\d", linha), (
                f"{pacote} sem versao exata (==): {linha!r}")

    def test_versoes_gravadas_sao_as_do_ambiente(self):
        import sklearn
        import xgboost
        v = calibracao.versoes_bibliotecas()
        assert v["scikit-learn"] == sklearn.__version__
        assert v["xgboost"] == xgboost.__version__
        if TORCH_AVAILABLE:
            import torch
            assert v["torch"] == torch.__version__

    def test_conferir_meta_avisa_divergencia_nominal(self, tmp_path, capsys):
        meta = {"fonte_usada": "sintetico_calibrado", "n_amostras": 10,
                "versoes": {"scikit-learn": "0.0.1", "xgboost": "0.0.1", "torch": "0.0.1"}}
        caminho = tmp_path / "x_meta.json"
        caminho.write_text(json.dumps(meta), encoding="utf-8")
        devolvido = calibracao.conferir_meta(caminho, "TESTE")
        saida = capsys.readouterr().out
        assert devolvido["n_amostras"] == 10
        assert "AVISO" in saida and "scikit-learn: treinado com 0.0.1" in saida

    def test_conferir_meta_sem_arquivo_nao_levanta(self, tmp_path, capsys):
        assert calibracao.conferir_meta(tmp_path / "nao_existe.json", "TESTE") == {}


# ══════════════════════════════════════════════════════════════════════════
# (e) CANDIDATO DO VOLUNTÁRIO — treina, recarrega, NÃO ativa sozinho
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def voluntario_isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(voluntary_module, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(voluntary_module, "MODELO_PATH", tmp_path / "voluntary_risk.joblib")
    monkeypatch.setattr(voluntary_module, "MODELO_META_PATH", tmp_path / "voluntary_risk_meta.json")
    monkeypatch.setattr(rs, "MODELO_PATH", tmp_path / "voluntary_risk.joblib")
    monkeypatch.setattr(rs, "MODELO_META_PATH", tmp_path / "voluntary_risk_meta.json")
    monkeypatch.setattr(rs, "_modelo", None)
    monkeypatch.setattr(rs, "_modelo_consultado", False)
    yield tmp_path
    rs._modelo = None
    rs._modelo_consultado = False


class TestVoluntarioCandidato:

    @pytest.mark.parametrize("fonte", ["sintetico",
                                       pytest.param("sintetico_calibrado", marks=requer_calibracao)])
    def test_treina_e_recarrega(self, voluntario_isolado, fonte):
        m = VoluntaryRiskModel().train(n_samples=500, fonte=fonte)
        _confere_rastreio(m, "risk_scorer_voluntario", fonte, 500)
        assert 0.5 < m["auc_vs_rotulo"] <= 1.0
        assert m["corr_vs_regra"] > 0.5
        assert (voluntario_isolado / "voluntary_risk_candidato.joblib").exists()
        meta = json.loads((voluntario_isolado / "voluntary_risk_candidato_meta.json")
                          .read_text(encoding="utf-8"))
        assert meta["features"] == rs.FEATURES_DE_RISCO
        v = VoluntaryRiskModel()
        assert v.load()
        assert 0.0 <= v.predict_proba_risco(20, 1, 500.0) <= 1.0

    def test_sem_ativar_o_scorer_continua_nas_regras(self, voluntario_isolado):
        VoluntaryRiskModel().train(n_samples=300)
        assert not (voluntario_isolado / "voluntary_risk.joblib").exists()
        assert rs.carregar_modelo(forcar=True) is False
        assert rs.calculate_risk("Session Started", {"days_since_last": 18,
                                                     "features_used_30d": 1}) == 0.66

    def test_ativar_promove_o_candidato(self, voluntario_isolado):
        VoluntaryRiskModel().train(n_samples=300)
        assert VoluntaryRiskModel.ativar() is True
        assert (voluntario_isolado / "voluntary_risk.joblib").exists()
        assert rs.carregar_modelo(forcar=True) is True
        assert rs.modelo_ativo()
        risco = rs.calculate_risk("Session Started", {"days_since_last": 18, "features_used_30d": 1})
        assert 0.0 <= risco <= 1.0
        # O modelo aprendeu a tendência das regras: inatividade sobe o risco
        baixo = rs.calculate_risk("Session Started", {"days_since_last": 0, "features_used_30d": 12})
        alto = rs.calculate_risk("Session Started", {"days_since_last": 30, "features_used_30d": 0})
        assert alto > baixo

    def test_ativar_sem_candidato_e_recusado(self, voluntario_isolado):
        assert VoluntaryRiskModel.ativar() is False

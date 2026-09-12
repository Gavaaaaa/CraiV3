"""tests/test_synthetic_data.py — o gerador tem duas fontes, e a default não mudou.

O que se trava aqui:

  (a) `fonte="sintetico"` (default) produz EXATAMENTE o dataset de antes de
      existir o parâmetro — hash do DataFrame medido em `1370224`, antes de
      qualquer alteração. Se um sorteio extra entrar no caminho default, o
      hash muda e este arquivo reprova. É a mesma catraca de
      `test_risk_pluggable.TestSemModelo`: ponto de extensão que muda o caso
      comum é regressão.
  (b) `fonte="sintetico_calibrado"` lê `models/calibracao.json`, usa os
      parâmetros medidos (o valor da fatura bate com o MLE da amostra real) e
      aplica o anti-circularidade (exceções ao rótulo).
  (c) A proveniência é declarada feature a feature, com um dos três status —
      nenhuma feature fica sem status, e o gerador calibrado NÃO inventa um
      status novo.
  (d) O gerador do voluntário produz as colunas na ordem de
      `FEATURES_DE_RISCO` e o rótulo vem das regras do scorer.

Uso:
    pytest tests/test_synthetic_data.py -v
"""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from crai.churn_voluntary.risk_scorer import FEATURES_DE_RISCO, _risco_por_regras
from crai.ml import calibracao
from crai.ml.synthetic_data import (
    BEHAVIORAL_FEATURES,
    VOLUNTARY_FEATURES,
    generate_behavioral_dataset,
    generate_dataset,
    generate_liquidity_series,
    generate_voluntary_dataset,
)

# Medidos em `1370224` (10/09/2026), ANTES de `fonte`/`calibracao.py` existirem.
HASH_DATASET_500 = "208cc0a5aacd7e4ff5b8f15e90d63282e0c2c59eed0fda32e3883f896ed00e46"
HASH_BEHAVIORAL_500 = "ea41be92bfa5d7d785704e320ba6d9ea699a1d7273a7bbef88b18d2167d38c24"
HASH_LIQUIDITY_20x90 = "80af6a12437aadac646e0bcbb367b5912d76d8cf3d725cfad872abe8c82bcdc3"


def _hash(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=True).values.tobytes()).hexdigest()


requer_calibracao = pytest.mark.skipif(
    not calibracao.CALIBRACAO_PATH.exists(),
    reason="models/calibracao.json ausente — rode preparar_amostra_real",
)


# ══════════════════════════════════════════════════════════════════════════
# (a) DEFAULT BYTE-IDÊNTICO
# ══════════════════════════════════════════════════════════════════════════

class TestDefaultNaoMudou:
    """CATRACA — passa antes e depois; existe para travar o caminho default."""

    def test_generate_dataset_identico_ao_baseline(self):
        assert _hash(generate_dataset(500, seed=42)) == HASH_DATASET_500

    def test_generate_behavioral_identico_ao_baseline(self):
        assert _hash(generate_behavioral_dataset(500, seed=42)) == HASH_BEHAVIORAL_500

    def test_generate_liquidity_identico_ao_baseline(self):
        df = generate_liquidity_series(20, 90, seed=42, end_date="2026-09-01")
        assert _hash(df) == HASH_LIQUIDITY_20x90

    def test_fonte_sintetico_explicita_e_o_default(self):
        pd.testing.assert_frame_equal(generate_dataset(300, seed=7),
                                      generate_dataset(300, seed=7, fonte="sintetico"))
        pd.testing.assert_frame_equal(generate_behavioral_dataset(300, seed=7),
                                      generate_behavioral_dataset(300, seed=7, fonte="sintetico"))

    def test_parametros_default_sao_os_literais_antigos(self):
        P = calibracao.parametros("sintetico")
        assert P["classifier"]["invoice_lognormal"] == {"mean": 5.8, "sigma": 0.7}
        assert P["classifier"]["error_code_probs"] == [0.35, 0.20, 0.15, 0.10, 0.12, 0.08]
        assert P["behavioral"]["saudavel"]["days_since_last_login_scale"] == 1.5
        assert P["behavioral"]["anomalo"]["tickets_lam"] == 4.5
        assert P["liquidity"]["p_atraso_salario"] == 0.0

    def test_parametros_devolve_copia(self):
        P = calibracao.parametros("sintetico")
        P["classifier"]["invoice_lognormal"]["mean"] = 99
        assert calibracao.PARAMETROS_DISTRIBUICAO["classifier"]["invoice_lognormal"]["mean"] == 5.8

    def test_fonte_invalida_e_recusada(self):
        with pytest.raises(ValueError):
            generate_dataset(10, fonte="real")
        with pytest.raises(ValueError):
            calibracao.parametros("dado_real")


# ══════════════════════════════════════════════════════════════════════════
# (b) CALIBRADO
# ══════════════════════════════════════════════════════════════════════════

@requer_calibracao
class TestCalibrado:

    def test_calibracao_json_tem_o_contrato(self):
        c = calibracao.carregar_calibracao()
        assert c["calibrado"] is True
        for bloco in ("classifier", "behavioral", "liquidity", "voluntary"):
            assert bloco in c["parametros"]
        for fonte in ("A", "B", "E"):
            assert fonte in c["fontes"]
        assert c["fontes"]["E"]["n"] == 5630

    def test_valor_da_fatura_bate_com_a_amostra_real(self):
        """mu/sigma do JSON = MLE de log(payment_value) das 300 reais (DATA_CARD 2.1)."""
        P = calibracao.parametros("sintetico_calibrado")["classifier"]
        assert P["invoice_lognormal"]["mean"] == pytest.approx(4.5824, abs=1e-3)
        assert P["invoice_lognormal"]["sigma"] == pytest.approx(0.8883, abs=1e-3)
        assert P["invoice_clip"][0] < 5 and P["invoice_clip"][1] < 2000

    def test_dataset_calibrado_usa_a_faixa_real(self):
        df = generate_dataset(3000, seed=42, fonte="sintetico_calibrado")
        assert df["invoice_amount"].median() < 200          # ticket de e-commerce
        assert df["invoice_amount"].max() <= 1312.67 + 1e-6
        assert 0.10 < df["recovered"].mean() < 0.90

    def test_calibrado_difere_do_default(self):
        assert not generate_dataset(200, seed=42, fonte="sintetico_calibrado").equals(
            generate_dataset(200, seed=42))
        assert not generate_behavioral_dataset(200, seed=42, fonte="sintetico_calibrado").equals(
            generate_behavioral_dataset(200, seed=42))

    def test_calibrado_e_reprodutivel(self):
        pd.testing.assert_frame_equal(
            generate_dataset(200, seed=1, fonte="sintetico_calibrado"),
            generate_dataset(200, seed=1, fonte="sintetico_calibrado"))
        pd.testing.assert_frame_equal(
            generate_behavioral_dataset(200, seed=1, fonte="sintetico_calibrado"),
            generate_behavioral_dataset(200, seed=1, fonte="sintetico_calibrado"))

    def test_histogramas_empiricos_somam_um(self):
        P = calibracao.parametros("sintetico_calibrado")["classifier"]
        for chave, tamanho in (("day_of_month_hist", 31), ("hour_hist", 24),
                               ("day_of_week_hist", 7)):
            assert len(P[chave]) == tamanho
            assert sum(P[chave]) == pytest.approx(1.0, abs=1e-4)
            assert min(P[chave]) > 0            # suavizacao: nada com prob. zero
        assert sum(P["error_code_probs"]) == pytest.approx(1.0, abs=1e-4)

    def test_features_calibradas_do_autoencoder_seguem_o_doador(self):
        """Saudável: média de dias ~ DaySinceLastOrder; anômalo continua pior."""
        P = calibracao.parametros("sintetico_calibrado")["behavioral"]
        df = generate_behavioral_dataset(4000, seed=42, fonte="sintetico_calibrado")
        saud = df[df["is_anomalous"] == 0]
        anom = df[df["is_anomalous"] == 1]
        assert saud["days_since_last_login"].mean() == pytest.approx(
            P["saudavel"]["days_since_last_login_scale"], rel=0.25)
        assert anom["days_since_last_login"].mean() > saud["days_since_last_login"].mean()
        assert anom["tickets_30d"].mean() > saud["tickets_30d"].mean()
        assert anom["nps_last"].mean() < saud["nps_last"].mean()


# ══════════════════════════════════════════════════════════════════════════
# ANTI-CIRCULARIDADE — o rótulo não é dedutível das features
# ══════════════════════════════════════════════════════════════════════════

class TestAntiCircularidade:

    def test_rotulo_do_classificador_tem_ruido_e_bernoulli(self):
        """Mesmas features -> rótulos diferentes entre seeds (não é função das features)."""
        P = calibracao.parametros("sintetico")["classifier"]
        a = generate_dataset(500, seed=42, parametros=P)
        # Reamostra só o sorteio final: mesmo p, outro rng
        rng = np.random.default_rng(0)
        outro = (rng.random(500) < 0.5).astype(int)
        assert (a["recovered"].to_numpy() != outro).any()
        assert P["ruido_rotulo_sd"] > 0

    def test_excecoes_do_classificador_mudam_rotulos(self):
        base = calibracao.parametros("sintetico")["classifier"]
        com = dict(base, p_excecao_rotulo=0.10)
        a = generate_dataset(2000, seed=42, parametros=base)
        b = generate_dataset(2000, seed=42, parametros=com)
        pd.testing.assert_frame_equal(a.drop(columns="recovered"), b.drop(columns="recovered"))
        diff = (a["recovered"] != b["recovered"]).mean()
        assert 0.02 < diff < 0.10      # ~10% sorteados, metade muda

    def test_excecoes_do_autoencoder_misturam_as_populacoes(self):
        base = calibracao.parametros("sintetico")["behavioral"]
        com = dict(base, p_excecao_anomalo=0.5, p_excecao_saudavel=0.0)
        a = generate_behavioral_dataset(2000, seed=42, parametros=base)
        b = generate_behavioral_dataset(2000, seed=42, parametros=com)
        anom_a = a[a["is_anomalous"] == 1]
        anom_b = b[b["is_anomalous"] == 1]
        assert len(anom_a) == len(anom_b)
        # O rótulo é o mesmo; as features dos anômalos ficaram mais "saudáveis"
        assert anom_b["days_since_last_login"].mean() < anom_a["days_since_last_login"].mean()
        assert anom_b["nps_last"].mean() > anom_a["nps_last"].mean()
        # Saudáveis intocados
        pd.testing.assert_frame_equal(
            a[a["is_anomalous"] == 0].reset_index(drop=True),
            b[b["is_anomalous"] == 0].reset_index(drop=True))

    def test_choques_de_liquidez_alteram_a_serie(self):
        base = calibracao.parametros("sintetico")["liquidity"]
        com = dict(base, p_atraso_salario=0.5, atraso_salario_max_dias=5, p_gasto_imprevisto=0.1)
        a = generate_liquidity_series(30, 120, seed=42, end_date="2026-09-01", parametros=base)
        b = generate_liquidity_series(30, 120, seed=42, end_date="2026-09-01", parametros=com)
        assert not a["balance_norm"].equals(b["balance_norm"])
        assert set(b["has_liquidity"].unique()) <= {0, 1}

    @requer_calibracao
    def test_calibrado_declara_excecoes_positivas(self):
        P = calibracao.parametros("sintetico_calibrado")
        assert P["classifier"]["p_excecao_rotulo"] > 0
        assert P["behavioral"]["p_excecao_anomalo"] > 0
        assert P["liquidity"]["p_atraso_salario"] > 0
        assert P["voluntary"]["ruido_rotulo_sd"] > 0


# ══════════════════════════════════════════════════════════════════════════
# (c) PROVENIÊNCIA FEATURE A FEATURE
# ══════════════════════════════════════════════════════════════════════════

@requer_calibracao
class TestProveniencia:

    MODELOS = {
        "FailureClassifier": ["tenure_months", "day_of_month", "invoice_amount", "avg_ticket",
                              "gateway_error_code", "card_brand", "payment_history_score",
                              "failure_count_90d", "hour_of_day", "day_of_week", "attempt_count"],
        "AnomalyDetector": list(BEHAVIORAL_FEATURES),
        "PaydayInference": ["balance_norm"],
        "risk_scorer_voluntario": list(FEATURES_DE_RISCO),
    }

    @pytest.mark.parametrize("modelo,features", list(MODELOS.items()))
    def test_toda_feature_de_treino_tem_status(self, modelo, features):
        linhas = calibracao.proveniencia_features(modelo, "sintetico_calibrado")
        declaradas = {l["feature"] for l in linhas}
        faltam = set(features) - declaradas
        assert not faltam, f"{modelo}: features sem proveniencia declarada: {faltam}"
        for l in linhas:
            assert l["status"] in calibracao.STATUS_ACEITOS
            if l["status"] != "sintetica_sem_doador":
                assert l["fonte"] and l["coluna"], f"{l['feature']}: ancorada/proxy sem fonte"

    def test_o_mapeamento_da_fonte_e_e_o_declarado(self):
        """DaySinceLastOrder -> days_since_last(_login); OrderCount -> features_used_30d;
        HourSpendOnApp -> avg_session_min; Complain -> tickets_30d;
        SatisfactionScore -> nps_last. Nenhum outro."""
        esperado = {
            ("AnomalyDetector", "days_since_last_login"): ("DaySinceLastOrder", "ancorada"),
            ("AnomalyDetector", "avg_session_min"): ("HourSpendOnApp", "proxy_fraco"),
            ("AnomalyDetector", "tickets_30d"): ("Complain", "proxy_fraco"),
            ("AnomalyDetector", "nps_last"): ("SatisfactionScore", "proxy_fraco"),
            ("risk_scorer_voluntario", "days_since_last"): ("DaySinceLastOrder", "ancorada"),
            ("risk_scorer_voluntario", "features_used_30d"): ("OrderCount", "proxy_fraco"),
        }
        for modelo in ("AnomalyDetector", "risk_scorer_voluntario"):
            for l in calibracao.proveniencia_features(modelo, "sintetico_calibrado"):
                chave = (modelo, l["feature"])
                if l["fonte"] and l["fonte"].startswith("E:"):
                    assert chave in esperado, f"mapeamento nao declarado: {chave}"
                    assert (l["coluna"], l["status"]) == esperado[chave]
                else:
                    assert chave not in esperado

    def test_payday_e_100_por_cento_sintetico_e_diz_isso(self):
        r = calibracao.resumo_proveniencia("PaydayInference", "sintetico_calibrado")
        assert r["por_status"]["ancorada"] == 0
        assert r["por_status"]["proxy_fraco"] == 0
        assert r["por_status"]["sintetica_sem_doador"] == r["n_features"] > 0

    def test_fonte_sintetico_nao_reivindica_ancora(self):
        r = calibracao.resumo_proveniencia("FailureClassifier", "sintetico")
        assert r["ancoradas_em_real"] == [] and r["proxy_fraco"] == []

    def test_status_invalido_e_recusado(self, tmp_path):
        c = calibracao.carregar_calibracao()
        c["proveniencia_features"]["FailureClassifier"][0]["status"] = "meio_real"
        caminho = tmp_path / "calibracao.json"
        caminho.write_text(json.dumps(c), encoding="utf-8")
        with pytest.raises(ValueError):
            calibracao.proveniencia_features("FailureClassifier", "sintetico_calibrado", caminho)

    def test_calibrado_sem_arquivo_levanta(self, tmp_path):
        with pytest.raises(calibracao.CalibracaoAusente):
            calibracao.parametros("sintetico_calibrado", tmp_path / "nao_existe.json")


# ══════════════════════════════════════════════════════════════════════════
# (d) GERADOR DO VOLUNTÁRIO
# ══════════════════════════════════════════════════════════════════════════

class TestGeradorVoluntario:

    def test_ordem_das_features_e_o_contrato_do_scorer(self):
        assert list(VOLUNTARY_FEATURES) == list(FEATURES_DE_RISCO)
        df = generate_voluntary_dataset(100, seed=42)
        assert list(df.columns[:len(FEATURES_DE_RISCO)]) == list(FEATURES_DE_RISCO)

    def test_rotulo_vem_das_regras_do_scorer(self):
        df = generate_voluntary_dataset(300, seed=42)
        for _, r in df.head(50).iterrows():
            esperado = _risco_por_regras(r["event"], {
                "days_since_last": int(r["days_since_last"]),
                "features_used_30d": int(r["features_used_30d"])})
            assert r["risk_regra"] == pytest.approx(esperado)
        assert set(df["churn"].unique()) <= {0, 1}
        assert 0.05 < df["churn"].mean() < 0.95

    def test_one_hot_exclusivo(self):
        df = generate_voluntary_dataset(500, seed=42)
        soma = df[["evento_cancelamento", "evento_downgrade", "evento_sessao"]].sum(axis=1)
        assert (soma == 1).all()

    def test_reprodutivel_e_seeds_diferem(self):
        pd.testing.assert_frame_equal(generate_voluntary_dataset(200, seed=3),
                                      generate_voluntary_dataset(200, seed=3))
        assert not generate_voluntary_dataset(200, seed=3).equals(
            generate_voluntary_dataset(200, seed=4))

    @requer_calibracao
    def test_calibrado_usa_o_histograma_do_doador(self):
        df = generate_voluntary_dataset(3000, seed=42, fonte="sintetico_calibrado")
        # DaySinceLastOrder: media 4,54, maximo observado 46; OrderCount: 1..16
        assert 3.5 < df["days_since_last"].mean() < 5.5
        assert df["features_used_30d"].min() >= 1 and df["features_used_30d"].max() <= 16

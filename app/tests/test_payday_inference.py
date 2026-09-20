"""
tests/test_payday_inference.py — Testes unitários do Módulo 3 (PaydayInference).

Cobre:
- Geração das séries de liquidez sintéticas (3 perfis de recebimento BR)
- Fallback heurístico (modelos não treinados)
- Treino de LSTM + Prophet em dataset pequeno
- Persistência: artefatos salvos são recarregáveis via load()
- Inferência com os modelos treinados (janela ótima de retentativa)

Aqui a unidade de amostra é o CLIENTE (uma série diária inteira), não a linha:
60 clientes × 120 dias = 7.200 observações diárias, o suficiente para validar
que o treino roda e o modelo salvo volta do disco.

O treino é redirecionado para um diretório temporário (MODELS_DIR monkeypatched),
para que a suíte não sobrescreva os modelos de produção em crai/models/.
"""

import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

from crai.ml import payday_inference as payday_module
from crai.ml.payday_inference import (
    HORIZON,
    PROFILES,
    PROPHET_AVAILABLE,
    TORCH_AVAILABLE,
    WINDOW,
    PaydayInference,
)
from crai.ml.synthetic_data import (
    LIQUIDITY_PROFILES,
    end_date_por_semente,
    generate_liquidity_series,
)

# Fim de série canônico da seed 42 (2026-09-14, a base do treino de 14/09/2026).
# `end_date` é obrigatório desde 19/09/2026: a série não pode depender do dia
# em que o teste roda.
END_DATE = end_date_por_semente(42)

requer_modelos = pytest.mark.skipif(
    not (TORCH_AVAILABLE and PROPHET_AVAILABLE),
    reason="torch e/ou prophet não instalados",
)


# ══════════════════════════════════════════════════════════════════════════
# SÉRIES DE LIQUIDEZ SINTÉTICAS
# ══════════════════════════════════════════════════════════════════════════

class TestLiquiditySeries:
    """Testes do gerador de séries de liquidez."""

    def test_generate_correct_shape(self):
        """Uma linha por (cliente, dia), com todas as colunas da featurização."""
        df = generate_liquidity_series(n_customers=20, n_days=90, end_date=END_DATE)
        assert len(df) == 20 * 90
        for col in ["customer_id", "profile", "date", "day_of_month",
                    "weekday", "balance_norm", "has_liquidity"]:
            assert col in df.columns, f"Coluna '{col}' ausente"

    def test_profiles_are_valid(self):
        """Todos os perfis pertencem aos 3 perfis de recebimento modelados."""
        df = generate_liquidity_series(n_customers=50, n_days=60, end_date=END_DATE)
        assert set(df["profile"].unique()).issubset(set(LIQUIDITY_PROFILES))

    def test_series_ends_on_end_date(self):
        """A série termina em `end_date`, nunca em `date.today()`.

        Até 19/09/2026 o default era hoje, e a base de treino mudava conforme o
        dia em que o treino rodou. Agora `end_date` é obrigatório e o fim
        canônico de uma semente vem de `end_date_por_semente`.
        """
        df = generate_liquidity_series(n_customers=5, n_days=60, end_date=END_DATE)
        assert df["date"].max() == END_DATE
        assert END_DATE == pd.Timestamp("2026-09-14")

    def test_end_date_e_obrigatorio(self):
        """Sem `end_date` o gerador não roda — nem com None."""
        with pytest.raises(TypeError):
            generate_liquidity_series(n_customers=5, n_days=60)
        with pytest.raises(ValueError, match="end_date"):
            generate_liquidity_series(n_customers=5, n_days=60, end_date=None)

    def test_has_liquidity_is_binary(self):
        """O rótulo de liquidez é 0/1 e coerente com o saldo."""
        df = generate_liquidity_series(n_customers=20, n_days=60, end_date=END_DATE)
        assert set(df["has_liquidity"].unique()).issubset({0, 1})
        assert (df.loc[df["has_liquidity"] == 1, "balance_norm"] >= 1.0).all()
        assert (df.loc[df["has_liquidity"] == 0, "balance_norm"] < 1.0).all()

    def test_reproducibility_with_seed(self):
        """Seed fixa gera séries idênticas."""
        df1 = generate_liquidity_series(n_customers=10, n_days=60, seed=42, end_date=END_DATE)
        df2 = generate_liquidity_series(n_customers=10, n_days=60, seed=42, end_date=END_DATE)
        assert df1.equals(df2)

    def test_clt_tem_ancora_mensal_mais_forte(self):
        """A liquidez do CLT é ancorada no dia do mês; a do freelancer, não.

        É essa ancoragem que o prior sazonal do Prophet aprende — medida aqui
        pela dispersão da taxa de liquidez ao longo dos dias do mês. Salário
        (CLT) e notas fiscais (PJ) criam picos; projetos avulsos, não.
        """
        df = generate_liquidity_series(n_customers=200, n_days=150, end_date=END_DATE)
        dispersao = {
            perfil: df[df["profile"] == perfil]
            .groupby("day_of_month")["has_liquidity"].mean().std()
            for perfil in LIQUIDITY_PROFILES
        }
        assert dispersao["CLT"] > dispersao["freelancer"]
        assert dispersao["PJ"] > dispersao["freelancer"]


# ══════════════════════════════════════════════════════════════════════════
# FALLBACK HEURÍSTICO (COLD START)
# ══════════════════════════════════════════════════════════════════════════

class TestPaydayHeuristic:
    """Testes do cold start — sem modelos treinados."""

    def setup_method(self):
        self.payday = PaydayInference()

    def test_not_fitted_on_init(self):
        """Instância nasce sem modelo."""
        assert not self.payday.is_fitted

    @pytest.mark.asyncio
    async def test_heuristic_returns_all_fields(self):
        """Fallback devolve o mesmo contrato do ensemble treinado."""
        result = await self.payday.predict_next_window("cus_teste")

        for key in ["timestamp", "confidence", "profile", "method"]:
            assert key in result, f"Campo '{key}' ausente no resultado"
        assert result["method"] == "heuristic"
        assert result["profile"] in PROFILES
        assert 0.0 <= result["confidence"] <= 1.0


# ══════════════════════════════════════════════════════════════════════════
# TREINO E PERSISTÊNCIA
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def payday_treinado(tmp_path_factory):
    """Treina uma vez, em dataset pequeno, salvando num diretório temporário."""
    models_dir = tmp_path_factory.mktemp("models")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(payday_module, "MODELS_DIR", models_dir)
        payday = PaydayInference()
        metrics = payday.train(n_samples=60, n_days=120, epochs=3, patience=3)
        yield payday, metrics, models_dir


@requer_modelos
class TestPaydayTrain:
    """Testes do método train()."""

    def test_is_fitted_after_train(self, payday_treinado):
        """Modelo fica utilizável logo após o treino, sem precisar de load()."""
        payday, _, _ = payday_treinado
        assert payday.is_fitted
        assert payday.model is not None
        assert set(payday.priors.keys()) == set(PROFILES)

    def test_metrics_keys_present(self, payday_treinado):
        """train() devolve dict com as métricas esperadas."""
        _, metrics, _ = payday_treinado
        for key in ["roc_auc_lstm", "roc_auc_prophet", "roc_auc_ensemble",
                    "mae_dias_ensemble", "mae_dias_heuristica", "hit_1d_ensemble",
                    "epochs_trained", "best_val_loss", "por_perfil"]:
            assert key in metrics, f"Métrica '{key}' ausente"

    def test_split_por_cliente(self, payday_treinado):
        """Treino e teste não compartilham clientes (test_size=0.2 de 60)."""
        _, metrics, _ = payday_treinado
        assert metrics["n_clientes_treino"] == 48
        assert metrics["n_clientes_teste"] == 12

    def test_beats_fixed_day_heuristic(self, payday_treinado):
        """O ensemble erra menos a janela de retry que a heurística de dias fixos."""
        _, metrics, _ = payday_treinado
        assert metrics["mae_dias_ensemble"] < metrics["mae_dias_heuristica"]

    def test_artifacts_saved_on_disk(self, payday_treinado):
        """Todos os artefatos que load() espera são gravados."""
        _, _, models_dir = payday_treinado
        assert (models_dir / "payday_lstm.pt").exists()
        assert (models_dir / "payday_meta.json").exists()
        for perfil in PROFILES:
            assert (models_dir / f"payday_prophet_{perfil}.json").exists()

    def test_meta_json_has_load_contract(self, payday_treinado):
        """meta.json traz o campo hidden_size lido por load() ao reconstruir a LSTM."""
        _, _, models_dir = payday_treinado
        with open(models_dir / "payday_meta.json", encoding="utf-8") as f:
            meta = json.load(f)
        assert "hidden_size" in meta
        assert meta["window"] == WINDOW
        assert meta["horizon"] == HORIZON

    def test_load_recovers_model(self, payday_treinado):
        """Nova instância recarrega LSTM + os 3 Prophets do disco."""
        recarregado = PaydayInference()
        assert recarregado.load()
        assert recarregado.is_fitted
        assert set(recarregado.priors.keys()) == set(PROFILES)

    @pytest.mark.asyncio
    async def test_predict_uses_ensemble(self, payday_treinado):
        """Com modelos treinados, a predição usa LSTM + Prophet, não a heurística."""
        payday, _, _ = payday_treinado
        result = await payday.predict_next_window("cus_teste_001")

        assert result["method"] == "lstm_prophet"
        assert result["profile"] in PROFILES
        assert 0.0 <= result["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_prediction_inside_horizon(self, payday_treinado):
        """A janela prevista cai dentro dos 14 dias seguintes."""
        payday, _, _ = payday_treinado
        result = await payday.predict_next_window("cus_teste_002")

        agora = datetime.now()
        assert result["timestamp"] > agora
        assert result["timestamp"] <= agora + timedelta(days=HORIZON + 1)

    @pytest.mark.asyncio
    async def test_load_and_predict_match(self, payday_treinado):
        """Modelo recarregado produz a mesma janela ótima do original."""
        payday, _, _ = payday_treinado
        recarregado = PaydayInference()
        assert recarregado.load()

        original = await payday.predict_next_window("cus_comparacao")
        depois = await recarregado.predict_next_window("cus_comparacao")

        assert original["timestamp"] == depois["timestamp"]
        assert original["confidence"] == pytest.approx(depois["confidence"])
        assert original["profile"] == depois["profile"]


@requer_modelos
class TestJanelasDeslizantes:
    """Testes da featurização/janelamento compartilhado entre treino e inferência."""

    def test_window_shape(self):
        """Cada janela tem 30 dias × 5 features e prevê 14 dias."""
        df = generate_liquidity_series(n_customers=1, n_days=120, end_date=END_DATE)
        X, y, idx = PaydayInference._janelas_do_cliente(df.sort_values("date"), passo=7)
        assert X.shape[1:] == (WINDOW, 5)
        assert y.shape[1] == HORIZON
        assert len(X) == len(y) == len(idx)

    def test_no_window_when_series_too_short(self):
        """Série menor que WINDOW+HORIZON não gera janela nenhuma."""
        df = generate_liquidity_series(n_customers=1, n_days=WINDOW + HORIZON - 1, end_date=END_DATE)
        X, y, _ = PaydayInference._janelas_do_cliente(df.sort_values("date"))
        assert len(X) == 0
        assert len(y) == 0

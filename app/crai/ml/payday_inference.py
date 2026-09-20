"""crai/ml/payday_inference.py — Módulo 3: LSTM + Prophet (Inferência de Liquidez).

Prevê QUANDO o cliente terá saldo para a retentativa: uma LSTM projeta 14 dias
de probabilidade de liquidez a partir dos últimos 30 dias de saldo do cliente,
e um Prophet por perfil (CLT/PJ/freelancer) contribui o prior sazonal do
payday brasileiro (5º dia útil, dias 10/15/20/30). Ensemble 0.6/0.4.

Cold start (modelo não treinado): heurística de dias fixos por perfil.
Produção: artefatos treinados por train(), salvos em crai/models/.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from . import calibracao
from .calibracao import conferir_meta
from .synthetic_data import end_date_por_semente, generate_liquidity_series, seed_por_cliente

try:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_AVAILABLE = True
except ImportError:  # pragma: no cover
    TORCH_AVAILABLE = False

try:
    from prophet import Prophet
    from prophet.serialize import model_from_json, model_to_json
    logging.getLogger("prophet").setLevel(logging.WARNING)
    logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
    PROPHET_AVAILABLE = True
except ImportError:  # pragma: no cover
    PROPHET_AVAILABLE = False

# ── Diretório de persistência (mesmo padrão dos módulos 1 e 2) ────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = BASE_DIR / "models"

WINDOW = 30
HORIZON = 14
PESO_LSTM = 0.6
LIMIAR = 0.5
PROFILES = ["CLT", "PJ", "freelancer"]

PAYDAY_HEURISTICS = {
    "CLT":        [5, 6, 7, 20, 21],
    "freelancer": [10, 15, 20, 25],
    "PJ":         [5, 10, 15, 20, 25],
    "default":    [5, 20],
}


if TORCH_AVAILABLE:
    class LiquidityLSTM(nn.Module):
        """Seq2vec: 30 dias de saldo (5 features/dia) → 14 logits de liquidez."""

        def __init__(self, input_size: int = 5, hidden_size: int = 64,
                     num_layers: int = 2, horizon: int = HORIZON, dropout: float = 0.2):
            super().__init__()
            self.lstm = nn.LSTM(input_size=input_size, hidden_size=hidden_size,
                                num_layers=num_layers, batch_first=True,
                                dropout=dropout if num_layers > 1 else 0.0)
            self.head = nn.Sequential(
                nn.Linear(hidden_size, 32), nn.ReLU(), nn.Dropout(dropout),
                nn.Linear(32, horizon),
            )

        def forward(self, x):
            _, (h_n, _) = self.lstm(x)
            return self.head(h_n[-1])


class PaydayInference:
    """Fase 1: heurística por perfil. Fase 2: LSTM + Prophet treinados."""

    def __init__(self):
        self.is_fitted = False
        self.model = None
        self.priors = {}
        self._train_metrics: dict = {}
        self.meta: dict = {}

    # ══════════════════════════════════════════════════════════════════════
    # TREINO
    # ══════════════════════════════════════════════════════════════════════

    def train(
        self,
        n_samples: int = 600,
        test_size: float = 0.2,
        n_days: int = 180,
        epochs: int = 40,
        batch_size: int = 256,
        lr: float = 1e-3,
        hidden_size: int = 64,
        patience: int = 6,
        seed: int = 42,
        fonte: str = "sintetico",
        end_date=None,
        dados: "pd.DataFrame | None" = None,
    ) -> dict:
        """
        Treina a LSTM de liquidez + os Prophets por perfil e retorna métricas.

        A unidade de amostra aqui é o CLIENTE (uma série diária), não a linha:
        o split é por cliente, nunca por janela. Janelas do mesmo cliente jamais
        aparecem nos dois lados — a série individual é altamente autocorrelacionada
        e o split por janela vazaria o padrão do cliente para o teste.

        Args:
            n_samples: Número de clientes simulados
            test_size: Fração de clientes reservada para avaliação
            n_days: Dias de histórico por cliente
            epochs: Máximo de épocas da LSTM (early stopping pode interromper antes)
            batch_size: Tamanho do batch
            lr: Learning rate do Adam
            hidden_size: Unidades ocultas da LSTM
            patience: Épocas sem melhora antes do early stopping
            seed: Seed para reprodutibilidade
            fonte: "sintetico" (default, inalterado) ou "sintetico_calibrado"
                   (mesmas séries + choques anti-circularidade; não há doador
                   real para saldo diário — ver calibracao.json)
            end_date: último dia das séries geradas. None (default) usa
                   `end_date_por_semente(seed)` — fixo, sem relógio; para a
                   seed 42 é 2026-09-14, o fim da base do treino de 14/09/2026.
                   Antes o gerador usava `date.today()` e a base mudava por dia.
            dados: séries JÁ GERADAS (colunas de `generate_liquidity_series`),
                   por exemplo `data/v2/liquidez.parquet` lido pelo `train_all`.
                   Quando vem, `n_samples`, `n_days` e `end_date` são lidos da
                   própria base, não dos argumentos.

        Returns:
            Dicionário com métricas (ROC-AUC diário, MAE da janela ótima vs heurística),
            mais `fonte_usada`, `n_amostras`, `proveniencia` e `versoes`.
        """
        calibracao.validar_fonte(fonte)
        if not TORCH_AVAILABLE or not PROPHET_AVAILABLE:
            raise RuntimeError(
                "torch e prophet são necessários para treinar o módulo de liquidez "
                "(pip install torch prophet)"
            )

        torch.manual_seed(seed)
        np.random.seed(seed)

        if dados is not None:
            df = dados.reset_index(drop=True)
            df["date"] = pd.to_datetime(df["date"])
            n_samples = int(df["customer_id"].nunique())
            n_days = int(df.groupby("customer_id")["date"].size().max())
            end_date = df["date"].max()
            print(f"[PAYDAY] Usando séries fornecidas ({n_samples} clientes x {n_days} dias, "
                  f"fim {end_date.date()}, fonte={fonte})...")
        else:
            end_date = pd.Timestamp(end_date) if end_date is not None else end_date_por_semente(seed)
            print(f"[PAYDAY] Gerando séries de liquidez sintéticas (fonte={fonte}, "
                  f"fim {end_date.date()})...")
            df = generate_liquidity_series(n_customers=n_samples, n_days=n_days, seed=seed,
                                           end_date=end_date, fonte=fonte)

        rng = np.random.default_rng(seed)
        clientes = np.sort(df["customer_id"].unique())
        rng.shuffle(clientes)
        corte = int(len(clientes) * (1 - test_size))
        clientes_treino = set(clientes[:corte])
        clientes_teste = set(clientes[corte:])

        df_treino = df[df["customer_id"].isin(clientes_treino)]
        df_teste = df[df["customer_id"].isin(clientes_teste)]

        # ── LSTM: padrão individual do cliente ───────────────────────────
        X, y = self._montar_janelas(df_treino, passo=7)
        if len(X) == 0:
            raise ValueError(
                f"Nenhuma janela gerada: n_days={n_days} é curto demais "
                f"(mínimo {WINDOW + HORIZON} dias)"
            )

        perm = rng.permutation(len(X))
        X, y = X[perm], y[perm]
        n_val = max(1, int(len(X) * 0.15))
        X_val, y_val = X[:n_val], y[:n_val]
        X_tr, y_tr = X[n_val:], y[n_val:]

        print(f"[PAYDAY] Treinando LSTM ({hidden_size} unid., 2 camadas) em "
              f"{len(X_tr)} janelas de {len(clientes_treino)} clientes...")
        self.model = LiquidityLSTM(hidden_size=hidden_size)
        historico = self._fit_lstm(X_tr, y_tr, X_val, y_val, epochs, batch_size, lr, patience)
        self.model.eval()

        # ── Prophet: sazonalidade coletiva do perfil ─────────────────────
        print("[PAYDAY] Ajustando priors sazonais (Prophet) por perfil...")
        self.priors = self._fit_priors(df_treino, seed)

        self.is_fitted = True

        metrics = self._evaluate(df_teste, historico)
        metrics["n_clientes_treino"] = len(clientes_treino)
        metrics["n_janelas_treino"] = int(len(X_tr))
        metrics["fonte_usada"] = fonte
        metrics["n_amostras"] = int(n_samples)
        metrics["n_days"] = int(n_days)
        metrics["end_date"] = str(pd.Timestamp(end_date).date())
        metrics["origem_dados"] = "dataframe_fornecido" if dados is not None else "gerador_em_memoria"
        metrics["proveniencia"] = calibracao.resumo_proveniencia("PaydayInference", fonte)
        metrics["versoes"] = calibracao.versoes_bibliotecas()
        metrics["treinado_em"] = datetime.now().isoformat(timespec="seconds")
        self._train_metrics = metrics

        self._save_models(hidden_size, seed, metrics)

        print(f"[PAYDAY] Treino concluído — ROC-AUC diário: {metrics['roc_auc_ensemble']:.3f} | "
              f"MAE da janela: {metrics['mae_dias_ensemble']:.2f} dia(s) "
              f"(heurística: {metrics['mae_dias_heuristica']:.2f}) | "
              f"acerto ±1d: {metrics['hit_1d_ensemble']:.1%}")

        return metrics

    # ── Janelas deslizantes (30 dias → 14 dias) ──────────────────────────
    @classmethod
    def _janelas_do_cliente(
        cls, serie: pd.DataFrame, passo: int = 7
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Fatia a série de um cliente. Retorna (X, y, idx_inicio_horizonte)."""
        feats = cls._featurize(serie)
        liquidez = serie["has_liquidity"].to_numpy(dtype=np.float32)

        X, y, idx = [], [], []
        for inicio in range(0, len(feats) - WINDOW - HORIZON + 1, passo):
            X.append(feats[inicio:inicio + WINDOW])
            y.append(liquidez[inicio + WINDOW:inicio + WINDOW + HORIZON])
            idx.append(inicio + WINDOW)
        if not X:
            return (
                np.empty((0, WINDOW, 5), dtype=np.float32),
                np.empty((0, HORIZON), dtype=np.float32),
                np.empty(0, dtype=int),
            )
        return np.stack(X), np.stack(y), np.array(idx)

    @classmethod
    def _montar_janelas(cls, df: pd.DataFrame, passo: int = 7) -> tuple[np.ndarray, np.ndarray]:
        """Janelas de todos os clientes do DataFrame, concatenadas."""
        Xs, ys = [], []
        for _, grupo in df.groupby("customer_id", sort=True):
            X, y, _ = cls._janelas_do_cliente(grupo.sort_values("date"), passo)
            if len(X):
                Xs.append(X)
                ys.append(y)
        if not Xs:
            return (
                np.empty((0, WINDOW, 5), dtype=np.float32),
                np.empty((0, HORIZON), dtype=np.float32),
            )
        return np.concatenate(Xs), np.concatenate(ys)

    def _fit_lstm(
        self,
        X_tr: np.ndarray, y_tr: np.ndarray,
        X_val: np.ndarray, y_val: np.ndarray,
        epochs: int, batch_size: int, lr: float, patience: int,
    ) -> dict:
        """Loop de treino da LSTM (BCE multi-rótulo) com early stopping."""
        train_loader = DataLoader(
            TensorDataset(torch.from_numpy(X_tr), torch.from_numpy(y_tr)),
            batch_size=batch_size,
            shuffle=True,
        )
        X_val_t, y_val_t = torch.from_numpy(X_val), torch.from_numpy(y_val)

        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        loss_fn = nn.BCEWithLogitsLoss()

        historico = {"train_loss": [], "val_loss": []}
        melhor_val = float("inf")
        sem_melhora = 0
        melhor_state = None

        for epoch in range(1, epochs + 1):
            self.model.train()
            soma, n = 0.0, 0
            for xb, yb in train_loader:
                optimizer.zero_grad()
                loss = loss_fn(self.model(xb), yb)
                loss.backward()
                optimizer.step()
                soma += loss.item() * xb.size(0)
                n += xb.size(0)
            train_loss = soma / n

            self.model.eval()
            with torch.no_grad():
                val_loss = loss_fn(self.model(X_val_t), y_val_t).item()

            historico["train_loss"].append(train_loss)
            historico["val_loss"].append(val_loss)
            if epoch % 5 == 0 or epoch == 1:
                print(f"[PAYDAY]   época {epoch:3d} | train {train_loss:.5f} | val {val_loss:.5f}")

            if val_loss < melhor_val - 1e-5:
                melhor_val = val_loss
                sem_melhora = 0
                melhor_state = {k: v.clone() for k, v in self.model.state_dict().items()}
            else:
                sem_melhora += 1
                if sem_melhora >= patience:
                    print(f"[PAYDAY]   early stop — sem melhora há {patience} épocas")
                    break

        if melhor_state is not None:
            self.model.load_state_dict(melhor_state)

        historico["melhor_val_loss"] = melhor_val
        return historico

    @staticmethod
    def _novo_prophet() -> "Prophet":
        m = Prophet(
            yearly_seasonality=False,
            weekly_seasonality=True,
            daily_seasonality=False,
            changepoint_prior_scale=0.01,
        )
        # Sazonalidade mensal: é onde mora o padrão de payday brasileiro
        m.add_seasonality(name="monthly", period=30.5, fourier_order=8)
        return m

    def _fit_priors(self, df_treino: pd.DataFrame, seed: int) -> dict:
        """Um Prophet por perfil sobre a taxa diária de clientes com saldo."""
        # cmdstanpy reconfigura seu logger no primeiro import (lazy, dentro do
        # fit) e volta a logar INFO por chain — silenciamos aqui, já carregado.
        logging.getLogger("cmdstanpy").setLevel(logging.WARNING)

        agregado_geral = (
            df_treino.groupby("date")["has_liquidity"].mean().reset_index()
            .rename(columns={"date": "ds", "has_liquidity": "y"})
        )

        priors = {}
        for perfil in PROFILES:
            sub = df_treino[df_treino["profile"] == perfil]
            serie = (
                sub.groupby("date")["has_liquidity"].mean().reset_index()
                .rename(columns={"date": "ds", "has_liquidity": "y"})
            )
            # Perfil ausente na amostra (datasets pequenos): cai no agregado geral
            if len(serie) < 2:
                serie = agregado_geral
            m = self._novo_prophet()
            m.fit(serie, seed=seed)
            priors[perfil] = m
            print(f"[PAYDAY]   prophet {perfil:11s} | {len(serie)} dias | "
                  f"liquidez média {serie['y'].mean():.1%}")
        return priors

    # ── Avaliação em clientes nunca vistos no treino ─────────────────────
    def _prior_por_data(self, perfil: str, datas: pd.DatetimeIndex) -> pd.Series:
        """Prior sazonal do perfil para um intervalo de datas (uma chamada só)."""
        yhat = self.priors[perfil].predict(pd.DataFrame({"ds": datas}))["yhat"].to_numpy()
        return pd.Series(np.clip(yhat, 0.0, 1.0), index=datas)

    @staticmethod
    def _primeiro_dia(probs: np.ndarray) -> int:
        """Índice do primeiro dia previsto com liquidez; argmax se nenhum passa."""
        acima = np.where(probs >= LIMIAR)[0]
        return int(acima[0]) if len(acima) else int(np.argmax(probs))

    @staticmethod
    def _primeiro_dia_heuristica(dias_do_mes: np.ndarray, perfil: str) -> int:
        alvo = set(PAYDAY_HEURISTICS.get(perfil, PAYDAY_HEURISTICS["default"]))
        for i, dia in enumerate(dias_do_mes):
            if dia in alvo:
                return i
        return 0

    def _evaluate(self, df_teste: pd.DataFrame, historico: dict) -> dict:
        """Compara heurística, Prophet, LSTM e ensemble nos clientes de teste."""
        datas_todas = pd.DatetimeIndex(sorted(df_teste["date"].unique()))
        priors_por_perfil = {p: self._prior_por_data(p, datas_todas) for p in PROFILES}

        y_true, p_lstm, p_prophet, p_ens = [], [], [], []
        linhas = []

        for cid, grupo in df_teste.groupby("customer_id", sort=True):
            grupo = grupo.sort_values("date").reset_index(drop=True)
            perfil = str(grupo["profile"].iloc[0])
            X, y, idx = self._janelas_do_cliente(grupo, passo=14)
            if len(X) == 0:
                continue

            with torch.no_grad():
                probs_lstm = torch.sigmoid(self.model(torch.from_numpy(X))).numpy()

            for j in range(len(X)):
                fatia = slice(idx[j], idx[j] + HORIZON)
                datas_horizonte = pd.DatetimeIndex(grupo["date"].iloc[fatia])
                dias_do_mes = grupo["day_of_month"].iloc[fatia].to_numpy()
                prior = priors_por_perfil[perfil].loc[datas_horizonte].to_numpy()
                ens = PESO_LSTM * probs_lstm[j] + (1 - PESO_LSTM) * prior

                y_true.append(y[j])
                p_lstm.append(probs_lstm[j])
                p_prophet.append(prior)
                p_ens.append(ens)

                # "Janela ótima" só tem resposta se existe algum dia com liquidez
                com_liquidez = np.where(y[j] == 1)[0]
                if len(com_liquidez):
                    linhas.append({
                        "customer_id": cid,
                        "profile": perfil,
                        "real": int(com_liquidez[0]),
                        "heuristica": self._primeiro_dia_heuristica(dias_do_mes, perfil),
                        "lstm": self._primeiro_dia(probs_lstm[j]),
                        "ensemble": self._primeiro_dia(ens),
                    })

        y_flat = np.concatenate(y_true)
        tem_duas_classes = len(np.unique(y_flat)) > 1

        def _auc(probs: list) -> float:
            if not tem_duas_classes:
                return 0.0
            return round(float(roc_auc_score(y_flat, np.concatenate(probs))), 4)

        tarefas = pd.DataFrame(
            linhas, columns=["customer_id", "profile", "real", "heuristica", "lstm", "ensemble"]
        )
        erro_ens = (tarefas["ensemble"] - tarefas["real"]).abs()
        erro_heu = (tarefas["heuristica"] - tarefas["real"]).abs()

        por_perfil = {}
        for perfil, sub in tarefas.groupby("profile"):
            e_ens = (sub["ensemble"] - sub["real"]).abs()
            e_heu = (sub["heuristica"] - sub["real"]).abs()
            por_perfil[str(perfil)] = {
                "n_janelas": int(len(sub)),
                "mae_heuristica": round(float(e_heu.mean()), 3),
                "mae_ensemble": round(float(e_ens.mean()), 3),
                "hit_1d_ensemble": round(float((e_ens <= 1).mean()), 4),
            }

        return {
            "roc_auc_lstm": _auc(p_lstm),
            "roc_auc_prophet": _auc(p_prophet),
            "roc_auc_ensemble": _auc(p_ens),
            "mae_dias_ensemble": round(float(erro_ens.mean()), 3),
            "mae_dias_heuristica": round(float(erro_heu.mean()), 3),
            "hit_exato_ensemble": round(float((erro_ens == 0).mean()), 4),
            "hit_1d_ensemble": round(float((erro_ens <= 1).mean()), 4),
            "hit_2d_ensemble": round(float((erro_ens <= 2).mean()), 4),
            "peso_lstm": PESO_LSTM,
            "limiar": LIMIAR,
            "epochs_trained": len(historico["train_loss"]),
            "best_val_loss": round(float(historico["melhor_val_loss"]), 6),
            "n_clientes_teste": int(df_teste["customer_id"].nunique()),
            "n_janelas_teste": int(len(y_true)),
            "por_perfil": por_perfil,
        }

    # ── Persistência dos modelos treinados ───────────────────────────────
    def _save_models(self, hidden_size: int, seed: int, metrics: dict):
        """Salva LSTM + Prophets no formato esperado por load()."""
        MODELS_DIR.mkdir(parents=True, exist_ok=True)

        torch.save(self.model.state_dict(), MODELS_DIR / "payday_lstm.pt")
        for perfil, modelo in self.priors.items():
            with open(MODELS_DIR / f"payday_prophet_{perfil}.json", "w", encoding="utf-8") as f:
                f.write(model_to_json(modelo))

        meta = {
            "window": WINDOW,
            "horizon": HORIZON,
            "hidden_size": hidden_size,
            "peso_lstm": PESO_LSTM,
            "limiar": LIMIAR,
            "epochs_treinadas": metrics["epochs_trained"],
            "melhor_val_loss": metrics["best_val_loss"],
            "n_clientes_treino": metrics["n_clientes_treino"],
            "n_janelas_treino": metrics["n_janelas_treino"],
            "roc_auc_ensemble": metrics["roc_auc_ensemble"],
            "mae_dias_ensemble": metrics["mae_dias_ensemble"],
            "seed": seed,
            "end_date": metrics["end_date"],
            "modelo": "PaydayInference",
            "algoritmo": "LSTM seq2vec (PyTorch) 0,6 + Prophet por perfil 0,4",
            "fonte_usada": metrics["fonte_usada"],
            "n_amostras": metrics["n_amostras"],
            "proveniencia": metrics["proveniencia"],
            "versoes": metrics["versoes"],
            "treinado_em": metrics["treinado_em"],
        }
        self.meta = meta
        with open(MODELS_DIR / "payday_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        print(f"[PAYDAY] Modelos salvos em {MODELS_DIR}/")

    # ── Carregamento dos modelos treinados ───────────────────────────────
    def load(self) -> bool:
        """Carrega LSTM + Prophets por perfil de crai/models/."""
        if not TORCH_AVAILABLE or not PROPHET_AVAILABLE:
            print("[PAYDAY] torch/prophet não instalados — usando heurística")
            return False
        try:
            with open(MODELS_DIR / "payday_meta.json", encoding="utf-8") as f:
                meta = json.load(f)
            self.meta = conferir_meta(MODELS_DIR / "payday_meta.json", "PAYDAY")
            self.model = LiquidityLSTM(hidden_size=meta["hidden_size"])
            self.model.load_state_dict(torch.load(MODELS_DIR / "payday_lstm.pt"))
            self.model.eval()
            for p in PROFILES:
                with open(MODELS_DIR / f"payday_prophet_{p}.json", encoding="utf-8") as f:
                    self.priors[p] = model_from_json(f.read())
            self.is_fitted = True
            print(f"[PAYDAY] LSTM + {len(self.priors)} Prophets carregados de {MODELS_DIR}/")
            return True
        except FileNotFoundError:
            print("[PAYDAY] Modelos não encontrados — usando heurística")
            return False
        except Exception as e:
            print(f"[PAYDAY] Erro ao carregar modelos: {e}")
            return False

    # ── Interface consumida pelo agente (Módulo 5) ───────────────────────
    async def predict_next_window(self, customer_id: str) -> dict:
        if not self.is_fitted:
            history = await self._fetch_history(customer_id)
            profile = self._classify_profile(history)
            return {**self._heuristic_predict(profile), "method": "heuristic"}

        serie = self._liquidity_series(customer_id)
        profile = serie.attrs["profile"]

        X = torch.from_numpy(self._featurize(serie)[None, ...])
        with torch.no_grad():
            probs_lstm = torch.sigmoid(self.model(X)).numpy()[0]

        hoje = pd.Timestamp(datetime.now().date())
        datas_futuras = pd.date_range(hoje + pd.Timedelta(days=1), periods=HORIZON)
        futuro = pd.DataFrame({"ds": datas_futuras})
        prior = np.clip(self.priors[profile].predict(futuro)["yhat"].to_numpy(), 0.0, 1.0)

        probs = PESO_LSTM * probs_lstm + (1 - PESO_LSTM) * prior
        acima = np.where(probs >= LIMIAR)[0]
        idx = int(acima[0]) if len(acima) else int(np.argmax(probs))

        return {
            "timestamp": (datas_futuras[idx] + pd.Timedelta(hours=10)).to_pydatetime(),
            "confidence": round(float(probs[idx]), 4),
            "profile": profile,
            "method": "lstm_prophet",
        }

    # ── Featurização (idêntica a modulo_03_payday/src/features.py) ───────
    @staticmethod
    def _featurize(serie: pd.DataFrame) -> np.ndarray:
        dia = serie["day_of_month"].to_numpy()
        return np.stack([
            serie["has_liquidity"].to_numpy(dtype=np.float32),
            np.clip(serie["balance_norm"].to_numpy(dtype=np.float32), 0, 5),
            np.sin(2 * np.pi * dia / 31).astype(np.float32),
            np.cos(2 * np.pi * dia / 31).astype(np.float32),
            (serie["weekday"].to_numpy() < 5).astype(np.float32),
        ], axis=1)

    def _liquidity_series(self, customer_id: str) -> pd.DataFrame:
        """Últimos 30 dias de saldo do cliente (em produção: gateway/open finance).

        Simulação determinística por customer_id, com as mesmas âncoras de
        pagamento BR usadas no treino (CLT 50%, PJ 30%, freelancer 20%).
        """
        rng = np.random.default_rng(seed=seed_por_cliente(customer_id))
        profile = rng.choice(PROFILES, p=[0.50, 0.30, 0.20])

        hoje = pd.Timestamp(datetime.now().date())
        datas = pd.date_range(hoje - pd.Timedelta(days=WINDOW - 1), periods=WINDOW)

        entradas = np.zeros(WINDOW)
        if profile == "CLT":
            salario = rng.uniform(2.5, 5.0)
            bday_idx = pd.Series((datas.dayofweek < 5)).groupby(
                [datas.year, datas.month]).cumsum().where(datas.dayofweek < 5, 0)
            entradas[np.asarray(bday_idx) == 5] = salario * 0.6
            entradas[datas.day == int(np.clip(rng.normal(20, 1), 18, 22))] += salario * 0.4
            gasto = rng.uniform(0.08, 0.14)
        elif profile == "PJ":
            receita = rng.uniform(2.0, 6.0)
            for ancora, peso in [(10, 0.4), (15, 0.3), (28, 0.3)]:
                if rng.uniform() > 0.15:
                    entradas[datas.day == ancora + int(rng.integers(0, 4))] += \
                        receita * peso * rng.uniform(0.7, 1.3)
            gasto = rng.uniform(0.10, 0.18)
        else:
            n_pag = int(rng.integers(2, 6))
            entradas[rng.choice(WINDOW, size=n_pag, replace=False)] = \
                rng.exponential(scale=1.2, size=n_pag)
            gasto = rng.uniform(0.10, 0.20)

        saldo = np.zeros(WINDOW)
        atual = rng.uniform(0.2, 1.5)
        for i in range(WINDOW):
            atual = max(0.0, atual + entradas[i] - gasto * rng.uniform(0.5, 1.5))
            saldo[i] = atual

        serie = pd.DataFrame({
            "date": datas, "day_of_month": datas.day, "weekday": datas.dayofweek,
            "balance_norm": np.round(saldo, 4),
            "has_liquidity": (saldo >= 1.0).astype(int),
        })
        serie.attrs["profile"] = str(profile)
        return serie

    # ── Fallback heurístico (cold start) ─────────────────────────────────
    def _classify_profile(self, history: list) -> str:
        amounts = [h["amount"] for h in history if h.get("status") == "succeeded"]
        if len(amounts) < 3:
            return "CLT"
        cv = np.std(amounts) / (np.mean(amounts) + 1e-9)
        if cv < 0.15: return "CLT"
        if cv < 0.40: return "PJ"
        return "freelancer"

    def _heuristic_predict(self, profile: str) -> dict:
        paydays = PAYDAY_HEURISTICS.get(profile, PAYDAY_HEURISTICS["default"])
        today = datetime.now()
        next_date = None
        for day in sorted(paydays):
            candidate = today.replace(day=min(day, 28))
            if candidate > today + timedelta(hours=2):
                next_date = candidate
                break
        if next_date is None:
            next_month = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
            next_date = next_month.replace(day=paydays[0])
        return {
            "timestamp":  next_date,
            "confidence": 0.65 if profile == "CLT" else 0.45,
            "profile":    profile,
        }

    async def _fetch_history(self, customer_id: str) -> list:
        rng = np.random.default_rng(seed=seed_por_cliente(customer_id))
        base = rng.uniform(500, 5000)
        noise = rng.normal(0, base * 0.05, 30)
        return [
            {"amount": round(float(base + noise[i]), 2), "date": datetime.now() - timedelta(days=i),
             "status": "succeeded" if rng.random() > 0.05 else "failed"}
            for i in range(30)
        ]

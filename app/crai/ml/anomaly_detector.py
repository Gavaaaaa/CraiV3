"""crai/ml/anomaly_detector.py — Módulo 2: Autoencoder de anomalias comportamentais.

Autoencoder denso (12→32→16→4→16→32→12) treinado apenas em clientes saudáveis.
O erro de reconstrução funciona como score de anomalia: clientes cujo
comportamento se desviou do padrão saudável são reconstruídos com erro alto.

Cold start (modelo não treinado): heurística de z-score sobre o evento Stripe.
Produção: autoencoder treinado por train(), com artefatos em crai/models/.
"""

import json
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from . import calibracao
from .calibracao import conferir_meta
from .synthetic_data import (
    BEHAVIORAL_FEATURES,
    _behavioral_population,
    generate_behavioral_dataset,
    seed_por_cliente,
)

try:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
    TORCH_AVAILABLE = True
except ImportError:  # pragma: no cover
    TORCH_AVAILABLE = False

# ── Diretório de persistência (mesmo padrão do failure_classifier) ───────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = BASE_DIR / "models"

# ── Limiar de anomalia ───────────────────────────────────────────────────
# O limiar é um percentil do erro de reconstrução dos SAUDÁVEIS de validação:
# tudo acima dele é anômalo. O percentil certo depende da base, porque depende
# de quanto as duas populações se separam no erro.
#
# Base v1 (14/09/2026): p95. As duas populações tinham perfis de conta
# diferentes e se separavam ~6x no erro; em p95 o detector media recall 0,97
# e precisão 0,93 — o percentil nem era uma escolha.
THRESHOLD_PERCENTIL_V1 = 95.0
# Base v2: o percentil NÃO é uma constante — é a SAÍDA de um critério
# aplicado à curva do artefato que está em produção, e é o TREINO quem aplica
# o critério (`train(recall_minimo=...)` chama `escolher_percentil` sobre a
# curva recém-calculada e grava o resultado em `threshold_percentil` no
# `autoencoder_meta.json`). Na v2 o perfil de conta vem da mesma população
# para os dois grupos, a anomalia está só no comportamento, a separação cai
# para ~3,3x e p95 deixa o recall em ~0,34 (precisão ~0,82) — dois terços dos
# anômalos passam.
#
# CRITÉRIO (`escolher_percentil`, recall_minimo=RECALL_MINIMO_LIMIAR_V2): o
# MAIOR percentil da curva precisão x recall x F1 (`curva_limiar`, p75..p97,
# gravada em models/curva_limiar_anomalia.json) cujo recall fica acima de
# 0,70. Maior percentil = limiar mais alto = menos falsos positivos; o piso de
# recall impede o detector de "acertar" ficando calado.
#
# O critério é o mesmo em qualquer máquina; o percentil que ele devolve NÃO
# é. A CAUSA é a ordem de acumulação em ponto flutuante, que depende do BLAS
# e do conjunto de instruções da CPU — NÃO o early stopping: a LSTM da
# liquidez não tem early stopping e varia mesmo assim (0,0004 de ROC-AUC).
# O early stopping (Adam + dropout + tolerância 1e-5) é AMPLIFICADOR: uma
# acumulação diferente muda a época em que o treino para, e aí muda o modelo
# inteiro (0,0112 de ROC-AUC) em vez de só o último dígito. Mesma base
# (sha256 conferidos), mesma semente e mesmas versões de torch/numpy/sklearn
# deram ROC-AUC 0,8684 e 0,8694 nas rodadas de 20/09/2026, 0,8582 na máquina
# de 22/09/2026 e 0,8694 na de 23/09/2026 (determinístico dentro de cada
# máquina: quatro rodadas idênticas, com 1, 6 e 12 threads). Classificador e
# voluntário reproduzem na quarta casa entre todas. Fixar versões no
# requirements.txt não basta para o autoencoder. Ver docs/LIMITACOES.md.
#
# Por isso NÃO existe uma constante `THRESHOLD_PERCENTIL_V2`. Existiu até
# 23/09/2026, congelada em 81,0: o treino copiava o número em vez de aplicar o
# critério, meta e constante concordavam por serem a mesma leitura, e a curva
# gravada pelo mesmo treino dizia p83. A fonte da verdade é o
# `threshold_percentil` do `autoencoder_meta.json` do artefato, gravado pelo
# treino a partir de `escolher_percentil`; quem precisa do percentil lê o meta.
#
# Valores de REFERÊNCIA, só documentação — nenhum caminho de decisão os lê:
#   máquina de 20/09/2026 -> p83  (docs/evidencia_base_v2/curva_limiar_anomalia_v2_varredura_20_09_p83.json)
#   máquina de 22/09/2026 -> p81  (docs/evidencia_base_v2/curva_limiar_anomalia_v2_varredura.json)
#     p75 rec 0,809 prec 0,680 | p81 rec 0,712 prec 0,711 | p82 rec 0,694 prec 0,716
#     p83 rec 0,670 prec 0,721 | p90 rec 0,497 prec 0,765 | p95 rec 0,341 prec 0,817
# `test_servir_v2.py::TestOLimiarDoAutoencoderPromovido` cobra que o meta do
# artefato promovido seja a saída do critério na curva dele — inclusive que o
# percentil seguinte já fique abaixo do piso (é o que prova "maior").
RECALL_MINIMO_LIMIAR_V2 = 0.70
PERCENTIS_CURVA_LIMIAR = tuple(range(75, 98))       # 75, 76, ..., 97


if TORCH_AVAILABLE:
    class BehaviorAutoencoder(nn.Module):
        """Encoder afunila 12 features até um gargalo de 4 dimensões."""

        def __init__(self, input_dim: int = 12, bottleneck: int = 4, dropout: float = 0.1):
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Linear(input_dim, 32), nn.ReLU(), nn.Dropout(dropout),
                nn.Linear(32, 16), nn.ReLU(),
                nn.Linear(16, bottleneck),
            )
            self.decoder = nn.Sequential(
                nn.Linear(bottleneck, 16), nn.ReLU(),
                nn.Linear(16, 32), nn.ReLU(), nn.Dropout(dropout),
                nn.Linear(32, input_dim),
            )

        def forward(self, x):
            return self.decoder(self.encoder(x))


class AnomalyDetector:
    """Cold start: heurística de z-score. Produção: autoencoder comportamental."""

    THRESHOLDS = {"CLT": 0.15, "PJ": 0.35, "freelancer": 0.55, "default": 0.30}

    def __init__(self):
        self.is_fitted = False
        self.model = None
        self.scaler = None
        self.threshold = None
        self.features = BEHAVIORAL_FEATURES
        self._train_metrics: dict = {}
        self.meta: dict = {}
        # Fonte com que o artefato carregado/treinado foi gerado. Decide como
        # `_behavioral_snapshot` simula o cliente na inferência: um modelo
        # treinado com parâmetros calibrados precisa de um snapshot na mesma
        # escala, senão TODO cliente vira anomalia. "sintetico" = caminho antigo.
        self._fonte = "sintetico"

    # ══════════════════════════════════════════════════════════════════════
    # TREINO
    # ══════════════════════════════════════════════════════════════════════

    def train(
        self,
        n_samples: int = 5500,
        test_size: float = 0.15,
        anomaly_rate: float = 0.09,
        epochs: int = 100,
        batch_size: int = 128,
        lr: float = 1e-3,
        bottleneck: int = 4,
        patience: int = 10,
        threshold_percentile: float = THRESHOLD_PERCENTIL_V1,
        seed: int = 42,
        fonte: str = "sintetico",
        dados: "pd.DataFrame | None" = None,
        recall_minimo: "float | None" = None,
    ) -> dict:
        """
        Treina o autoencoder em dataset comportamental sintético e retorna métricas.

        Premissa central: treinar SOMENTE com clientes saudáveis. Isso força a
        rede a aprender a geometria do comportamento normal; qualquer cliente
        fora dessa distribuição produz reconstrução ruim. Os clientes anômalos
        nunca entram no treino — só no conjunto de avaliação, junto com os
        saudáveis de validação (held-out).

        Args:
            n_samples: Tamanho total do dataset sintético (saudáveis + anômalos)
            test_size: Fração dos saudáveis reservada para validação
            anomaly_rate: Fração de clientes anômalos no dataset
            epochs: Máximo de épocas (early stopping pode interromper antes)
            batch_size: Tamanho do batch
            lr: Learning rate do Adam
            bottleneck: Dimensão do gargalo do autoencoder
            patience: Épocas sem melhora antes do early stopping
            threshold_percentile: Percentil do erro dos saudáveis que vira threshold
                   (default `THRESHOLD_PERCENTIL_V1`). IGNORADO quando
                   `recall_minimo` vem: aí o percentil é a saída do critério.
            recall_minimo: piso de recall do CRITÉRIO da base v2. Quando vem,
                   o treino calcula a curva precisão x recall por percentil e
                   usa `escolher_percentil(curva, recall_minimo)` — o MAIOR
                   percentil com recall acima do piso — para definir o limiar
                   e gravar `threshold_percentil` no meta. O treino APLICA o
                   critério; não copia um número. `None` = caminho v1.
            seed: Seed para reprodutibilidade
            fonte: "sintetico" (default, inalterado) ou "sintetico_calibrado"
                   (parâmetros medidos em doadores reais + exceções ao rótulo)
            dados: dataset JÁ GERADO (colunas de `generate_behavioral_dataset`),
                   por exemplo `data/v2/comportamental.parquet` lido pelo
                   `train_all`. Quando vem, `n_samples` e `anomaly_rate` são
                   ignorados (a base já tem o tamanho e a taxa que tem) e
                   `n_samples` passa a ser `len(dados)`.

        Returns:
            Dicionário com métricas de treino (ROC-AUC, precision/recall, threshold),
            mais `fonte_usada`, `n_amostras`, `proveniencia` e `versoes`.
        """
        calibracao.validar_fonte(fonte)
        if not TORCH_AVAILABLE:
            raise RuntimeError(
                "PyTorch não instalado — necessário para treinar o autoencoder "
                "(pip install torch)"
            )

        torch.manual_seed(seed)
        np.random.seed(seed)

        if dados is not None:
            print(f"[ANOMALY] Usando dataset fornecido ({len(dados)} clientes, fonte={fonte})...")
            df = dados.reset_index(drop=True)
            n_samples = len(df)
        else:
            print(f"[ANOMALY] Gerando dataset comportamental sintético (fonte={fonte})...")
            df = generate_behavioral_dataset(
                n_samples=n_samples, anomaly_rate=anomaly_rate, seed=seed, fonte=fonte
            )
        self.features = list(BEHAVIORAL_FEATURES)
        self._fonte = fonte

        saudaveis = df[df["is_anomalous"] == 0]
        anomalos = df[df["is_anomalous"] == 1]
        X_healthy = saudaveis[self.features].to_numpy(dtype=np.float32)
        X_anomalous = anomalos[self.features].to_numpy(dtype=np.float32)

        X_train, X_val = train_test_split(X_healthy, test_size=test_size, random_state=seed)

        # Scaler ajustado apenas nos saudáveis de treino
        self.scaler = StandardScaler().fit(X_train)
        X_train_s = self.scaler.transform(X_train).astype(np.float32)
        X_val_s = self.scaler.transform(X_val).astype(np.float32)

        print(f"[ANOMALY] Treinando autoencoder ({len(self.features)}→{bottleneck}→"
              f"{len(self.features)}) em {len(X_train)} clientes saudáveis...")

        self.model = BehaviorAutoencoder(
            input_dim=len(self.features), bottleneck=bottleneck
        )
        historico = self._fit_autoencoder(
            X_train_s, X_val_s, epochs, batch_size, lr, patience
        )
        self.model.eval()

        # ── Threshold calibrado nos saudáveis de validação (held-out) ────
        erros_val = self._reconstruction_error(X_val_s)
        # Curva precisão x recall x F1 por percentil, sempre calculada, e
        # ANTES do limiar: na base v2 é dela que o critério tira o percentil;
        # em qualquer base é o que permite auditar a escolha sem retreinar.
        erros_anom = self._reconstruction_error(
            self.scaler.transform(X_anomalous).astype(np.float32))
        curva = self.curva_limiar(erros_val, erros_anom)
        criterio = None
        if recall_minimo is not None:
            escolhido = self.escolher_percentil(curva, recall_minimo)
            threshold_percentile = escolhido["percentil"]
            criterio = {
                "regra": "maior percentil da curva com recall >= recall_minimo "
                         "(AnomalyDetector.escolher_percentil)",
                "recall_minimo": recall_minimo,
                "percentil": escolhido["percentil"],
                "recall": escolhido["recall"],
                "precision": escolhido["precision"],
            }
            if escolhido["recall"] < recall_minimo:
                print(f"[ANOMALY] AVISO: nenhum percentil da curva atinge recall "
                      f">= {recall_minimo}; usando o de maior recall "
                      f"(p{threshold_percentile:g}, recall {escolhido['recall']:.4f})")
            else:
                print(f"[ANOMALY] Limiar pelo critério: p{threshold_percentile:g} "
                      f"(recall {escolhido['recall']:.4f} >= {recall_minimo}, "
                      f"precisão {escolhido['precision']:.4f})")
        self.threshold = float(np.percentile(erros_val, threshold_percentile))

        metrics = self._evaluate(X_val_s, X_anomalous, threshold_percentile, historico)
        metrics["curva_limiar"] = curva
        metrics["criterio_limiar"] = criterio
        metrics["n_train_healthy"] = int(len(X_train))
        metrics["fonte_usada"] = fonte
        metrics["n_amostras"] = int(n_samples)
        metrics["origem_dados"] = "dataframe_fornecido" if dados is not None else "gerador_em_memoria"
        metrics["proveniencia"] = calibracao.resumo_proveniencia("AnomalyDetector", fonte)
        metrics["versoes"] = calibracao.versoes_bibliotecas()
        metrics["treinado_em"] = datetime.now().isoformat(timespec="seconds")
        self._train_metrics = metrics
        self.is_fitted = True

        self._save_models(bottleneck, seed, metrics)

        print(f"[ANOMALY] Treino concluído — ROC-AUC: {metrics['roc_auc']:.3f} | "
              f"recall: {metrics['recall']:.3f} | threshold: {self.threshold:.4f} | "
              f"separação: {metrics['separation_ratio']:.1f}x")

        return metrics

    def _fit_autoencoder(
        self,
        X_train_s: np.ndarray,
        X_val_s: np.ndarray,
        epochs: int,
        batch_size: int,
        lr: float,
        patience: int,
    ) -> dict:
        """Loop de treino com early stopping; restaura o melhor state_dict."""
        train_loader = DataLoader(
            TensorDataset(torch.from_numpy(X_train_s)),
            batch_size=batch_size,
            shuffle=True,
        )
        val_tensor = torch.from_numpy(X_val_s)

        optimizer = torch.optim.Adam(self.model.parameters(), lr=lr)
        loss_fn = nn.MSELoss()

        historico = {"train_loss": [], "val_loss": []}
        melhor_val = float("inf")
        sem_melhora = 0
        melhor_state = None

        for epoch in range(1, epochs + 1):
            self.model.train()
            soma, n = 0.0, 0
            for (batch,) in train_loader:
                optimizer.zero_grad()
                loss = loss_fn(self.model(batch), batch)
                loss.backward()
                optimizer.step()
                soma += loss.item() * batch.size(0)
                n += batch.size(0)
            train_loss = soma / n

            self.model.eval()
            with torch.no_grad():
                val_loss = loss_fn(self.model(val_tensor), val_tensor).item()

            historico["train_loss"].append(train_loss)
            historico["val_loss"].append(val_loss)
            if epoch % 10 == 0 or epoch == 1:
                print(f"[ANOMALY]   época {epoch:3d} | train {train_loss:.5f} | val {val_loss:.5f}")

            if val_loss < melhor_val - 1e-5:
                melhor_val = val_loss
                sem_melhora = 0
                melhor_state = {k: v.clone() for k, v in self.model.state_dict().items()}
            else:
                sem_melhora += 1
                if sem_melhora >= patience:
                    print(f"[ANOMALY]   early stop — sem melhora há {patience} épocas")
                    break

        if melhor_state is not None:
            self.model.load_state_dict(melhor_state)

        historico["melhor_val_loss"] = melhor_val
        return historico

    def _reconstruction_error(self, X_scaled: np.ndarray) -> np.ndarray:
        """Erro quadrático médio de reconstrução, por amostra."""
        with torch.no_grad():
            tensor = torch.from_numpy(X_scaled.astype(np.float32))
            recon = self.model(tensor).numpy()
        return ((recon - X_scaled) ** 2).mean(axis=1)

    def _evaluate(
        self,
        X_val_s: np.ndarray,
        X_anomalous: np.ndarray,
        threshold_percentile: float,
        historico: dict,
    ) -> dict:
        """Avalia em saudáveis held-out + anômalos (nunca vistos no treino)."""
        X_anom_s = self.scaler.transform(X_anomalous).astype(np.float32)

        erros = np.concatenate([
            self._reconstruction_error(X_val_s),
            self._reconstruction_error(X_anom_s),
        ])
        y = np.concatenate([np.zeros(len(X_val_s), dtype=int), np.ones(len(X_anom_s), dtype=int)])
        preds = (erros > self.threshold).astype(int)

        tn, fp, fn, tp = confusion_matrix(y, preds, labels=[0, 1]).ravel()
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

        erro_saudaveis = float(erros[y == 0].mean())
        erro_anomalos = float(erros[y == 1].mean()) if (y == 1).any() else 0.0

        return {
            "roc_auc": round(float(roc_auc_score(y, erros)), 4) if len(set(y)) > 1 else 0.0,
            "average_precision": (
                round(float(average_precision_score(y, erros)), 4) if len(set(y)) > 1 else 0.0
            ),
            "precision": round(float(precision), 4),
            "recall": round(float(recall), 4),
            "f1": round(float(f1), 4),
            "threshold": round(self.threshold, 6),
            "threshold_percentile": threshold_percentile,
            "confusion_matrix": [[int(tn), int(fp)], [int(fn), int(tp)]],
            "mean_error_healthy": round(erro_saudaveis, 4),
            "mean_error_anomalous": round(erro_anomalos, 4),
            "separation_ratio": round(erro_anomalos / (erro_saudaveis + 1e-9), 2),
            "epochs_trained": len(historico["train_loss"]),
            "best_val_loss": round(float(historico["melhor_val_loss"]), 6),
            "n_val_healthy": int(len(X_val_s)),
            "n_anomalous": int(len(X_anomalous)),
        }

    @staticmethod
    def curva_limiar(erros_saudaveis: np.ndarray, erros_anomalos: np.ndarray,
                     percentis=PERCENTIS_CURVA_LIMIAR) -> list:
        """Precisão, recall e F1 do detector para cada percentil candidato a limiar.

        Para cada `p`, o limiar é `percentile(erros_saudaveis, p)` — exatamente
        como o treino calcula o seu — e as previsões são `erro > limiar` sobre
        saudáveis de validação + anômalos. Devolve uma lista de dicionários
        (`percentil`, `threshold`, `precision`, `recall`, `f1`, `taxa_flag`),
        pronta para virar `curva_limiar_anomalia.json`.
        """
        erros = np.concatenate([erros_saudaveis, erros_anomalos])
        y = np.concatenate([np.zeros(len(erros_saudaveis), dtype=int),
                            np.ones(len(erros_anomalos), dtype=int)])
        curva = []
        for p in percentis:
            limiar = float(np.percentile(erros_saudaveis, p))
            preds = erros > limiar
            tp = int((preds & (y == 1)).sum())
            fp = int((preds & (y == 0)).sum())
            fn = int((~preds & (y == 1)).sum())
            precision = tp / (tp + fp) if (tp + fp) else 0.0
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
            curva.append({"percentil": float(p), "threshold": round(limiar, 6),
                          "precision": round(precision, 4), "recall": round(recall, 4),
                          "f1": round(f1, 4), "taxa_flag": round(float(preds.mean()), 4)})
        return curva

    @staticmethod
    def escolher_percentil(curva: list, recall_minimo: float = 0.70) -> dict:
        """O MAIOR percentil da curva cujo recall fica acima de `recall_minimo`.

        Maior percentil = limiar mais alto = menos falsos positivos; o piso de
        recall é o que impede o detector de "acertar" ficando calado. Se nenhum
        ponto atinge o piso, devolve o de maior recall (e o chamador decide).
        """
        acima = [c for c in curva if c["recall"] >= recall_minimo]
        if acima:
            return max(acima, key=lambda c: c["percentil"])
        return max(curva, key=lambda c: c["recall"])

    # ── Persistência do modelo treinado ──────────────────────────────────
    def _save_models(self, bottleneck: int, seed: int, metrics: dict):
        """Salva pesos, scaler e meta.json no formato esperado por load()."""
        MODELS_DIR.mkdir(parents=True, exist_ok=True)

        torch.save(self.model.state_dict(), MODELS_DIR / "autoencoder.pt")
        joblib.dump(self.scaler, MODELS_DIR / "autoencoder_scaler.pkl")

        meta = {
            "features": self.features,
            "input_dim": len(self.features),
            "bottleneck": bottleneck,
            "threshold": self.threshold,
            "threshold_percentil": metrics["threshold_percentile"],
            # None no caminho v1 (percentil fixo); na v2, o critério que o
            # treino aplicou e o ponto que ele escolheu — é o que o teste do
            # artefato promovido confere contra a curva gravada ao lado.
            "criterio_limiar": metrics.get("criterio_limiar"),
            "epochs_treinadas": metrics["epochs_trained"],
            "melhor_val_loss": metrics["best_val_loss"],
            "n_treino_saudaveis": metrics["n_train_healthy"],
            "n_val_saudaveis": metrics["n_val_healthy"],
            "n_anomalos_avaliacao": metrics["n_anomalous"],
            "roc_auc": metrics["roc_auc"],
            "seed": seed,
            "modelo": "AnomalyDetector",
            "algoritmo": "autoencoder denso 12-32-16-4-16-32-12 (PyTorch)",
            "fonte_usada": metrics["fonte_usada"],
            "n_amostras": metrics["n_amostras"],
            "proveniencia": metrics["proveniencia"],
            "versoes": metrics["versoes"],
            "treinado_em": metrics["treinado_em"],
        }
        self.meta = meta
        with open(MODELS_DIR / "autoencoder_meta.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)

        print(f"[ANOMALY] Modelos salvos em {MODELS_DIR}/")

    # ── Carregamento do modelo treinado ──────────────────────────────────
    def load(self) -> bool:
        """Carrega autoencoder, scaler e threshold de crai/models/."""
        if not TORCH_AVAILABLE:
            print("[ANOMALY] PyTorch não instalado — usando heurística")
            return False
        try:
            with open(MODELS_DIR / "autoencoder_meta.json", encoding="utf-8") as f:
                meta = json.load(f)

            self.features = meta["features"]
            self.threshold = float(meta["threshold"])
            self.model = BehaviorAutoencoder(
                input_dim=meta["input_dim"], bottleneck=meta["bottleneck"]
            )
            self.model.load_state_dict(torch.load(MODELS_DIR / "autoencoder.pt"))
            self.model.eval()
            self.scaler = joblib.load(MODELS_DIR / "autoencoder_scaler.pkl")
            self.meta = conferir_meta(MODELS_DIR / "autoencoder_meta.json", "ANOMALY")
            self._fonte = self.meta.get("fonte_usada", "sintetico")

            self.is_fitted = True
            print(f"[ANOMALY] Autoencoder carregado de {MODELS_DIR}/ (threshold={self.threshold:.4f})")
            return True
        except FileNotFoundError:
            print("[ANOMALY] Autoencoder não encontrado — usando heurística")
            return False
        except Exception as e:
            print(f"[ANOMALY] Erro ao carregar autoencoder: {e}")
            return False

    # ── Interface consumida pelo agente (Módulo 5) ───────────────────────
    async def check(self, customer_id: str, event: dict) -> dict:
        if not self.is_fitted:
            return self._heuristic_check(event)

        behavior = self._behavioral_snapshot(customer_id)
        X = self.scaler.transform(
            np.array([[behavior[f] for f in self.features]], dtype=np.float32)
        ).astype(np.float32)

        with torch.no_grad():
            tensor = torch.from_numpy(X)
            recon = self.model(tensor).numpy()

        erro_por_feature = (recon[0] - X[0]) ** 2
        error = float(erro_por_feature.mean())

        ranking = sorted(
            zip(self.features, erro_por_feature.tolist()),
            key=lambda t: t[1], reverse=True,
        )
        return {
            "is_anomaly": error > self.threshold,
            "error": round(error, 4),
            "threshold": self.threshold,
            "method": "autoencoder",
            "top_features": [
                {"feature": nome, "contribuicao": round(contrib, 4)}
                for nome, contrib in ranking[:3]
            ],
        }

    def _behavioral_snapshot(self, customer_id: str) -> dict:
        """Snapshot comportamental do cliente (em produção viria do Segment/DB).

        Determinístico por customer_id: ~15% dos clientes exibem o padrão
        degradado (queda de uso, fricção alta) usado no treino como anomalia.

        Com um artefato treinado em fonte="sintetico_calibrado", o snapshot é
        sorteado com os MESMOS parâmetros calibrados do treino (via
        `_behavioral_population`), senão a escala não bate e o autoencoder
        marca todo mundo como anômalo. O caminho default é o de sempre.
        """
        rng = np.random.default_rng(seed=seed_por_cliente(customer_id))
        degradado = rng.uniform() < 0.15

        if self._fonte != "sintetico":
            P = calibracao.parametros(self._fonte)["behavioral"]
            linha = _behavioral_population(1, rng, anomalous=bool(degradado), parametros=P)
            return {f: (float(linha.at[0, f]) if f in ("mrr_brl", "feature_adoption",
                                                      "avg_session_min", "nps_last")
                        else int(linha.at[0, f])) for f in BEHAVIORAL_FEATURES}

        seats = int(np.clip(rng.poisson(lam=15), 1, 200))
        if degradado:
            logins_30d = int(max(0, rng.normal(loc=seats * 5, scale=seats * 2)))
            logins_7d = int(logins_30d * rng.uniform(0.05, 0.15))
            adoption = round(float(rng.beta(2, 5)), 3)
            session = round(float(max(0.5, rng.normal(6, 3))), 1)
            api_calls = int(max(0, rng.lognormal(4.5, 1.0)))
            last_login = int(np.clip(rng.exponential(scale=9), 0, 30))
            tickets = int(rng.poisson(4.5))
            failed_pay = int(rng.binomial(3, 0.35))
            nps = round(float(np.clip(rng.normal(5.5, 2.0), 0, 10)), 1)
        else:
            logins_30d = int(max(1, rng.normal(loc=seats * 18, scale=seats * 3)))
            logins_7d = int(logins_30d * rng.uniform(0.22, 0.30))
            adoption = round(float(rng.beta(5, 2)), 3)
            session = round(float(max(2, rng.normal(22, 6))), 1)
            api_calls = int(max(10, rng.lognormal(7.0, 0.8)))
            last_login = int(np.clip(rng.exponential(scale=1.5), 0, 30))
            tickets = int(rng.poisson(1.2))
            failed_pay = int(rng.binomial(3, 0.05))
            nps = round(float(np.clip(rng.normal(8.2, 1.3), 0, 10)), 1)

        return {
            "tenure_days": int(np.clip(rng.gamma(2.5, 180), 30, 2000)),
            "mrr_brl": round(float(np.clip(rng.lognormal(8.5, 0.7), 500, 50_000)), 2),
            "seats": seats,
            "logins_7d": logins_7d,
            "logins_30d": logins_30d,
            "feature_adoption": adoption,
            "avg_session_min": session,
            "api_calls_7d": api_calls,
            "days_since_last_login": last_login,
            "tickets_30d": tickets,
            "failed_pay_90d": failed_pay,
            "nps_last": nps,
        }

    # ── Fallback heurístico (cold start) ─────────────────────────────────
    def _heuristic_check(self, event: dict) -> dict:
        features  = self._extract_features(event)
        profile   = event.get("profile_type", "default")
        threshold = self.THRESHOLDS.get(profile, self.THRESHOLDS["default"])
        error     = self._simple_outlier_score(features)
        return {
            "is_anomaly": error > threshold,
            "error":      round(error, 4),
            "threshold":  threshold,
            "method":     "heuristic",
            "top_features": [],
        }

    def _simple_outlier_score(self, features: np.ndarray) -> float:
        if len(features) == 0:
            return 0.1
        mean = np.mean(features)
        std  = np.std(features) + 1e-9
        z    = np.abs((features - mean) / std)
        return float(np.clip(np.mean(z) / 5.0, 0, 1))

    def _extract_features(self, event: dict) -> np.ndarray:
        charge = event.get("data", {}).get("object", {})
        return np.array([
            charge.get("amount", 0) / 100,
            len(charge.get("failure_code", "") or ""),
            int(bool(charge.get("failure_message"))),
            charge.get("attempt_count", 1),
        ], dtype=float)

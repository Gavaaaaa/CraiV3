"""crai/ml/voluntary_risk.py — Treino do modelo plugável do risk_scorer voluntário.

O `churn_voluntary/risk_scorer.py` tem, desde o Sprint 6, um encaixe para um
modelo treinado: `carregar_modelo()` procura `models/voluntary_risk.joblib` +
`voluntary_risk_meta.json`, confere que `meta["features"]` é EXATAMENTE
`FEATURES_DE_RISCO` e, havendo modelo, ele decide o risco no lugar das regras
fixas. Até este módulo, nada preenchia o encaixe.

O que este módulo treina — e o que ele NÃO é:

    O rótulo é `churn = Bernoulli(regras fixas + ruído)` do gerador
    `synthetic_data.generate_voluntary_dataset()`. Não existe sinal real de
    cancelamento em lugar nenhum do sistema (`churn_voluntary/README_treino.md`
    explica os quatro bloqueios). Logo, o modelo aprende a TENDÊNCIA das
    regras com ruído em cima — ele prova que o encaixe plugável, o treino, o
    retreino com mais volume e o `load()` funcionam de ponta a ponta. Ele não
    sabe mais sobre churn do que as regras sabem.

Por isso o treino grava, por padrão, um CANDIDATO:

    models/voluntary_risk_candidato.joblib
    models/voluntary_risk_candidato_meta.json

que o `risk_scorer` NÃO carrega. Promover o candidato ao nome que o scorer lê
(`voluntary_risk.joblib`) é um passo explícito — `ativar()` ou
`python -m crai.scripts.train_all --ativar-voluntario` — porque a partir daí
o comportamento observável do pipeline voluntário muda (o modelo decide, não
as regras), e isso é decisão de quem opera, não efeito colateral de um treino.

Uso:
    from crai.ml.voluntary_risk import VoluntaryRiskModel
    m = VoluntaryRiskModel()
    metrics = m.train(n_samples=2000, fonte="sintetico_calibrado")
    VoluntaryRiskModel().load()          # recarrega o candidato
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import train_test_split

from ..churn_voluntary.risk_scorer import (
    FEATURES_DE_RISCO,
    MODELO_META_PATH,
    MODELO_PATH,
)
from . import calibracao
from .calibracao import conferir_meta
from .synthetic_data import VOLUNTARY_FEATURES, generate_voluntary_dataset

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = BASE_DIR / "models"

NOME_CANDIDATO = "voluntary_risk_candidato"


class VoluntaryRiskModel:
    """Gradient boosting sobre FEATURES_DE_RISCO, no contrato de `carregar_modelo`."""

    def __init__(self):
        self.model = None
        self.is_fitted = False
        self.features = list(FEATURES_DE_RISCO)
        self._train_metrics: dict = {}
        self.meta: dict = {}

    # ══════════════════════════════════════════════════════════════════════
    # TREINO
    # ══════════════════════════════════════════════════════════════════════

    def train(
        self,
        n_samples: int = 2000,
        test_size: float = 0.2,
        seed: int = 42,
        fonte: str = "sintetico",
    ) -> dict:
        """
        Treina o candidato e devolve métricas.

        Args:
            n_samples: Número de eventos sintéticos
            test_size: Fração para teste
            seed: Seed para reprodutibilidade
            fonte: "sintetico" ou "sintetico_calibrado"

        Returns:
            AUC contra o rótulo ruidoso, Brier, e a fidelidade às regras
            (MAE e correlação entre a probabilidade prevista e `risk_regra` —
            o que o modelo de fato aprende), mais `fonte_usada`,
            `n_amostras`, `proveniencia` e `versoes`.
        """
        calibracao.validar_fonte(fonte)
        if list(VOLUNTARY_FEATURES) != list(FEATURES_DE_RISCO):
            raise RuntimeError(
                "VOLUNTARY_FEATURES (gerador) e FEATURES_DE_RISCO (scorer) divergem: "
                f"{VOLUNTARY_FEATURES} != {FEATURES_DE_RISCO}. A ordem é o contrato.")

        print(f"[RISK-VOL] Gerando dataset de risco voluntario (fonte={fonte})...")
        df = generate_voluntary_dataset(n_samples=n_samples, seed=seed, fonte=fonte)
        X = df[self.features].to_numpy(dtype=float)
        y = df["churn"].to_numpy(dtype=int)
        regra = df["risk_regra"].to_numpy(dtype=float)

        X_tr, X_te, y_tr, y_te, _, regra_te = train_test_split(
            X, y, regra, test_size=test_size, random_state=seed, stratify=y)

        print(f"[RISK-VOL] Treinando GradientBoosting em {len(X_tr)} eventos...")
        self.model = GradientBoostingClassifier(
            n_estimators=150, max_depth=3, learning_rate=0.05,
            subsample=0.8, random_state=seed)
        self.model.fit(X_tr, y_tr)
        self.is_fitted = True

        proba = self.model.predict_proba(X_te)[:, 1]
        metrics = {
            "auc_vs_rotulo": round(float(roc_auc_score(y_te, proba)), 4),
            "brier": round(float(brier_score_loss(y_te, proba)), 4),
            # Fidelidade às regras: o "teto" do que o modelo pode aprender.
            "mae_vs_regra": round(float(np.mean(np.abs(proba - regra_te))), 4),
            "corr_vs_regra": round(float(np.corrcoef(proba, regra_te)[0, 1]), 4),
            "auc_regra_vs_rotulo": round(float(roc_auc_score(y_te, regra_te)), 4),
            "taxa_churn_rotulo": round(float(y.mean()), 4),
            "n_treino": int(len(X_tr)),
            "n_teste": int(len(X_te)),
            "fonte_usada": fonte,
            "n_amostras": int(n_samples),
            "proveniencia": calibracao.resumo_proveniencia("risk_scorer_voluntario", fonte),
            "versoes": calibracao.versoes_bibliotecas(),
            "treinado_em": datetime.now().isoformat(timespec="seconds"),
        }
        self._train_metrics = metrics
        self._save_models(metrics, seed)

        print(f"[RISK-VOL] Treino concluido — AUC vs rotulo: {metrics['auc_vs_rotulo']:.3f} "
              f"(teto das regras: {metrics['auc_regra_vs_rotulo']:.3f}) | "
              f"corr com as regras: {metrics['corr_vs_regra']:.3f}")
        return metrics

    # ══════════════════════════════════════════════════════════════════════
    # PERSISTÊNCIA — candidato por padrão; ativar é explícito
    # ══════════════════════════════════════════════════════════════════════

    def _save_models(self, metrics: dict, seed: int):
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.model, MODELS_DIR / f"{NOME_CANDIDATO}.joblib")
        self.meta = {
            "modelo": "risk_scorer_voluntario",
            # Os três campos que `carregar_modelo()` lê: `features` (o
            # contrato de ordem), `algoritmo` e `treinado_em`.
            "features": list(self.features),
            "algoritmo": "GradientBoostingClassifier(n_estimators=150, max_depth=3)",
            "treinado_em": metrics["treinado_em"],
            "fonte_usada": metrics["fonte_usada"],
            "n_amostras": metrics["n_amostras"],
            "auc_vs_rotulo": metrics["auc_vs_rotulo"],
            "corr_vs_regra": metrics["corr_vs_regra"],
            "rotulo": "Bernoulli(regras fixas do risk_scorer + ruido) — NAO e churn observado",
            "proveniencia": metrics["proveniencia"],
            "versoes": metrics["versoes"],
            "seed": seed,
        }
        with open(MODELS_DIR / f"{NOME_CANDIDATO}_meta.json", "w", encoding="utf-8") as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=2, default=str)
        print(f"[RISK-VOL] Candidato salvo em {MODELS_DIR}/{NOME_CANDIDATO}.joblib "
              f"(NAO ativo: o scorer continua nas regras ate `ativar()`)")

    def load(self, candidato: bool = True) -> bool:
        """Recarrega o candidato (default) ou o modelo ativo, conferindo o contrato."""
        base = MODELS_DIR / NOME_CANDIDATO if candidato else MODELS_DIR / "voluntary_risk"
        try:
            with open(f"{base}_meta.json", encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("features") != list(FEATURES_DE_RISCO):
                print(f"[RISK-VOL] meta declara {meta.get('features')}, esperado "
                      f"{FEATURES_DE_RISCO} — artefato recusado")
                return False
            self.model = joblib.load(f"{base}.joblib")
            self.meta = conferir_meta(Path(f"{base}_meta.json"), "RISK-VOL")
            self.features = list(meta["features"])
            self.is_fitted = True
            print(f"[RISK-VOL] Modelo carregado de {base}.joblib")
            return True
        except FileNotFoundError:
            print(f"[RISK-VOL] {base.name} nao encontrado — execute train() primeiro")
            return False
        except Exception as e:  # noqa: BLE001
            print(f"[RISK-VOL] Erro ao carregar modelo: {e}")
            return False

    @staticmethod
    def ativar() -> bool:
        """Promove o candidato ao nome que o `risk_scorer` carrega.

        A partir daqui `calculate_risk` passa a usar o modelo (após reinício
        do processo — o scorer cacheia a ausência). Passo deliberadamente
        separado do treino.
        """
        origem = MODELS_DIR / f"{NOME_CANDIDATO}.joblib"
        origem_meta = MODELS_DIR / f"{NOME_CANDIDATO}_meta.json"
        if not origem.exists() or not origem_meta.exists():
            print("[RISK-VOL] Sem candidato para ativar — execute train() primeiro")
            return False
        shutil.copyfile(origem, MODELO_PATH)
        shutil.copyfile(origem_meta, MODELO_META_PATH)
        print(f"[RISK-VOL] Candidato ATIVADO como {MODELO_PATH.name}: o risk_scorer "
              f"passa a usar o modelo no proximo processo")
        return True

    def predict_proba_risco(self, days_since_last=0.0, features_used_30d=0.0, mrr=0.0,
                            event: str = "Session Started") -> float:
        """Risco [0,1] para um evento — o mesmo vetor que `risk_scorer._vetor_de_features`."""
        if not self.is_fitted:
            raise RuntimeError("modelo nao treinado/carregado")
        x = [[float(days_since_last), float(features_used_30d), float(mrr),
              1.0 if event == "Cancellation Page Viewed" else 0.0,
              1.0 if event == "Downgrade Clicked" else 0.0,
              1.0 if event == "Session Started" else 0.0]]
        return float(self.model.predict_proba(x)[0][1])

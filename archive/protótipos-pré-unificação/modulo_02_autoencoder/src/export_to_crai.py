"""Exporta os artefatos treinados do Modulo 2 para o pacote crai/.

Copia autoencoder.pt e scaler.pkl para crai/models/ e gera
autoencoder_meta.json (meta do treino + threshold calibrado), que e o
formato esperado por crai.ml.anomaly_detector.AnomalyDetector.load().

Uso (apos rodar src.train e src.evaluate):
    python -m src.export_to_crai
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

MODULO_ROOT = Path(__file__).resolve().parents[1]
CRAI_MODELS = MODULO_ROOT.parent / "crai" / "models"


def main() -> None:
    with open(MODULO_ROOT / "models" / "meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    with open(MODULO_ROOT / "reports" / "metricas.json", encoding="utf-8") as f:
        meta["threshold"] = json.load(f)["threshold_valor"]

    CRAI_MODELS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MODULO_ROOT / "models" / "autoencoder.pt", CRAI_MODELS / "autoencoder.pt")
    shutil.copy2(MODULO_ROOT / "models" / "scaler.pkl", CRAI_MODELS / "autoencoder_scaler.pkl")
    with open(CRAI_MODELS / "autoencoder_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"[ok] artefatos exportados para {CRAI_MODELS}/")
    print(f"     threshold: {meta['threshold']:.4f}")


if __name__ == "__main__":
    main()

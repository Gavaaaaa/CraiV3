"""Exporta os artefatos treinados do Modulo 3 para o pacote crai/.

Copia lstm.pt e os Prophets por perfil para crai/models/ e gera
payday_meta.json, formato esperado por crai.ml.payday_inference
.PaydayInference.load().

Uso (apos rodar src.train e src.prophet_model):
    python -m src.export_to_crai
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

MODULO_ROOT = Path(__file__).resolve().parents[1]
CRAI_MODELS = MODULO_ROOT.parent / "crai" / "models"

PROFILES = ["CLT", "PJ", "freelancer"]


def main() -> None:
    with open(MODULO_ROOT / "models" / "meta.json", encoding="utf-8") as f:
        meta = json.load(f)
    meta.pop("clientes_teste", None)  # so interessa ao evaluate

    CRAI_MODELS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MODULO_ROOT / "models" / "lstm.pt", CRAI_MODELS / "payday_lstm.pt")
    for p in PROFILES:
        shutil.copy2(
            MODULO_ROOT / "models" / f"prophet_{p}.json",
            CRAI_MODELS / f"payday_prophet_{p}.json",
        )
    with open(CRAI_MODELS / "payday_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)

    print(f"[ok] artefatos exportados para {CRAI_MODELS}/")
    print(f"     lstm.pt + {len(PROFILES)} prophets + payday_meta.json")


if __name__ == "__main__":
    main()

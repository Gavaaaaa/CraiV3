"""Exporta os posteriores aprendidos do Modulo 4 para o pacote crai/.

Copia bandit_state.json (posteriores Beta por perfil x oferta apos a
simulacao) para crai/models/, onde crai.churn_voluntary.offer_bandit
.OfferBandit.load() os usa como warm start do aprendizado online.

Uso (apos rodar src.simulate):
    python -m src.export_to_crai
"""
from __future__ import annotations

import shutil
from pathlib import Path

MODULO_ROOT = Path(__file__).resolve().parents[1]
CRAI_MODELS = MODULO_ROOT.parent / "crai" / "models"


def main() -> None:
    origem = MODULO_ROOT / "models" / "bandit_state.json"
    CRAI_MODELS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origem, CRAI_MODELS / "bandit_state.json")
    print(f"[ok] posteriores exportados para {CRAI_MODELS / 'bandit_state.json'}")


if __name__ == "__main__":
    main()

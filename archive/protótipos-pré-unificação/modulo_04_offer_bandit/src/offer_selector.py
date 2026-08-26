"""Interface publica do Modulo 4 para os demais modulos do CRAI.

O agente de churn voluntario (Modulo 5) consome esta classe: escolhe a
oferta de retencao por Thompson Sampling otimizando e-Profit e realimenta
o resultado (aceite/recusa) para o aprendizado continuo.
"""
from __future__ import annotations

from pathlib import Path

from src.bandit import ThompsonSamplingBandit
from src.environment import MESES_LTV_RETIDO, OFFERS, eprofit, offer_cost

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"


class OfferSelector:
    def __init__(self, models_dir: Path = MODELS_DIR, seed: int = 42) -> None:
        self.bandit = ThompsonSamplingBandit(seed=seed, optimize_eprofit=True)
        state_path = models_dir / "bandit_state.json"
        if state_path.exists():
            self.bandit.load(state_path)

    def choose_offer(self, profile: str, mrr: float, risk_score: float = 0.0) -> dict:
        """Risco critico (>= 0.90) escala direto para humano; senao, Thompson."""
        if risk_score >= 0.90:
            offer = "consulta_cs"
        else:
            offer = self.bandit.choose_offer(profile, mrr)
        p_estimado = self.bandit.conversion_rates(profile)[offer]
        return {
            "offer": offer,
            "p_accept_estimado": p_estimado,
            "custo": round(offer_cost(offer, mrr), 2),
            "eprofit_esperado": round(eprofit(p_estimado, offer, mrr), 2),
        }

    def record_outcome(self, profile: str, offer: str, accepted: bool) -> None:
        self.bandit.record_outcome(profile, offer, accepted)

    def conversion_rates(self, profile: str) -> dict:
        return self.bandit.conversion_rates(profile)


def main() -> None:
    selector = OfferSelector()
    print("=" * 64)
    print("DEMO - OfferSelector (Thompson Sampling, e-Profit)")
    print("=" * 64)
    for profile, mrr in [("CLT", 300.0), ("CLT", 2500.0), ("PJ", 550.0), ("freelancer", 220.0)]:
        r = selector.choose_offer(profile, mrr)
        print(f"\n{profile} com MRR R$ {mrr:,.0f}:")
        print(f"  oferta           : {r['offer']}")
        print(f"  P(aceite) apos {6000} rodadas: {r['p_accept_estimado']:.1%}")
        print(f"  custo            : R$ {r['custo']:,.2f}")
        print(f"  e-Profit esperado: R$ {r['eprofit_esperado']:,.2f}")


if __name__ == "__main__":
    main()

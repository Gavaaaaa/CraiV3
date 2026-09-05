"""crai/churn_voluntary/offer_bandit.py — Módulo 4: Thompson Sampling (MAB).

Cada par (perfil, oferta) mantém um posterior Beta(α, β) sobre a taxa de
aceite. A cada decisão o bandit AMOSTRA uma taxa de cada posterior e escolhe
a oferta que maximiza o e-Profit com a taxa amostrada:

    escolha = argmax_o  p_amostrado(o) × LTV_retido − custo(o)

A incerteza faz a exploração sozinha: braços pouco testados têm posteriores
largos e às vezes amostram alto; braços ruins saem de cena. Não há epsilon
para calibrar. O aprendizado é contínuo: cada aceite/recusa real atualiza o
posterior e é persistido em crai/models/bandit_state.json.

Warm start: posteriores da simulação de 6.000 rodadas do modulo_04_offer_bandit/.
Cold start (sem arquivo): priors de benchmarks de mercado.

AUTONOMIA TOTAL: não existe braço de escalação humana. A CRAI decide e age
sozinha — risco crítico muda o TOM da mensagem (ver `is_critical_risk`), nunca
o destinatário da decisão.
"""

import json
from pathlib import Path

import numpy as np

# ── Diretório de persistência (mesmo padrão dos módulos 1-3) ─────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = BASE_DIR / "models"
STATE_PATH = MODELS_DIR / "bandit_state.json"

OFFERS = ["desconto_10", "desconto_20", "pausa_1_mes", "pix_boleto_flash"]
PROFILES = ["CLT", "PJ", "freelancer"]

MESES_LTV_RETIDO = 6          # valor retido em caso de aceite (consistente com o Módulo 1)
MRR_TIPICO = {"CLT": 300.0, "PJ": 550.0, "freelancer": 220.0, "default": 350.0}

# Limiar de criticidade. NÃO é desvio de fluxo: só sinaliza que a mensagem deve
# sair com tom de alto cuidado. O braço `consulta_cs`, que era escolhido acima
# deste limiar, foi removido.
CRITICAL_RISK_THRESHOLD = 0.90

# Priors de cold start (benchmarks de mercado), em pseudo-observações (aceites, recusas)
SEED_PRIORS = {
    "CLT":        {"desconto_10": (9, 11),  "desconto_20": (14, 6), "pausa_1_mes": (8, 12),
                   "pix_boleto_flash": (2, 8)},
    "PJ":         {"desconto_10": (5, 10),  "desconto_20": (8, 7),  "pausa_1_mes": (13, 7),
                   "pix_boleto_flash": (3, 7)},
    "freelancer": {"desconto_10": (9, 11),  "desconto_20": (9, 11), "pausa_1_mes": (6, 9),
                   "pix_boleto_flash": (5, 5)},
}


def is_critical_risk(risk_score: float) -> bool:
    """Sinaliza criticidade só para ajustar o TOM da mensagem, nunca para
    desviar a oferta ou acionar humano."""
    return risk_score >= CRITICAL_RISK_THRESHOLD


def offer_cost(offer: str, mrr: float) -> float:
    """Custo da intervenção em R$ (descontos custam % do MRR por 3 meses)."""
    return {
        "desconto_10": 0.10 * 3 * mrr,
        "desconto_20": 0.20 * 3 * mrr,
        "pausa_1_mes": 1.0 * mrr,
        "pix_boleto_flash": 2.0,
    }[offer]


def _priors_de_benchmark() -> dict:
    """Estado completo de cold start: PROFILES × OFFERS a partir de SEED_PRIORS."""
    return {
        p: {o: {"alpha": 1.0 + a, "beta": 1.0 + b} for o, (a, b) in SEED_PRIORS[p].items()}
        for p in PROFILES
    }


def _posterior_valido(valor) -> bool:
    """Um posterior só serve se for Beta(α>0, β>0) com números reais."""
    if not isinstance(valor, dict):
        return False
    try:
        alpha, beta = float(valor["alpha"]), float(valor["beta"])
    except (KeyError, TypeError, ValueError):
        return False
    return alpha > 0 and beta > 0


def sanear_estado(bruto) -> tuple[dict, dict]:
    """Normaliza o JSON persistido contra o vocabulário atual (PROFILES × OFFERS).

    Duas coisas exigem isto, e as duas são reais neste repositório:

    1. `consulta_cs` foi um braço do bandit e tem observações acumuladas no
       arquivo persistido. Ele não existe mais — a CRAI não escala para humano.
       As observações desse braço são DESCARTADAS, não redistribuídas: elas
       mediam a aceitação de uma oferta que ninguém mais pode receber, e
       diluí-las nos outros braços inventaria evidência que nunca houve.

    2. `record_outcome` aceita qualquer string como perfil e persiste. Rodadas
       de teste de borda (fuzz dos webhooks) gravaram perfis como "", "abc" e
       "299,90" no arquivo real. Chave fora de PROFILES é ruído de teste, não
       aprendizado: descartada.

    O que sobra é sobreposto aos priors de benchmark, então o estado devolvido
    tem SEMPRE a forma completa — nenhum consumidor precisa lidar com perfil ou
    oferta faltando. Devolve (estado, descartes) para o chamador logar.
    """
    estado = _priors_de_benchmark()
    descartes = {"perfis": [], "ofertas": [], "posteriores": []}

    if not isinstance(bruto, dict):
        descartes["perfis"].append("<raiz do arquivo não é um objeto JSON>")
        return estado, descartes

    for perfil, ofertas in bruto.items():
        if perfil not in PROFILES or not isinstance(ofertas, dict):
            descartes["perfis"].append(perfil)
            continue
        for oferta, posterior in ofertas.items():
            if oferta not in OFFERS:
                descartes["ofertas"].append(f"{perfil}/{oferta}")
                continue
            if not _posterior_valido(posterior):
                descartes["posteriores"].append(f"{perfil}/{oferta}")
                continue
            estado[perfil][oferta] = {
                "alpha": float(posterior["alpha"]),
                "beta": float(posterior["beta"]),
            }

    return estado, descartes


class OfferBandit:
    """Thompson Sampling (Beta-Bernoulli) otimizando e-Profit por perfil."""

    def __init__(self, seed: int = 42):
        self.rng = np.random.default_rng(seed)
        self.is_fitted = False
        self.state = _priors_de_benchmark()

    # ── Carregamento do warm start ───────────────────────────────────────
    def load(self) -> bool:
        """Carrega posteriores aprendidos de crai/models/bandit_state.json.

        O arquivo é saneado na entrada (ver `sanear_estado`): braço extinto e
        perfil de ruído não voltam para a memória do bandit. Havendo descarte, o
        arquivo é reescrito já limpo — senão o mesmo lixo seria relido e
        relogado em toda inicialização.
        """
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                bruto = json.load(f)
        except FileNotFoundError:
            print("[BANDIT] Estado não encontrado — usando priors de benchmark (cold start)")
            return False
        except Exception as e:
            print(f"[BANDIT] Erro ao carregar estado: {e}")
            return False

        self.state, descartes = sanear_estado(bruto)
        self.is_fitted = True

        n_obs = sum(s["alpha"] + s["beta"] - 2 for p in self.state.values() for s in p.values())
        print(f"[BANDIT] Posteriores carregados de {STATE_PATH} ({n_obs:.0f} observações)")

        if any(descartes.values()):
            print(f"[BANDIT] Saneamento descartou: {len(descartes['perfis'])} perfil(is) "
                  f"fora de {PROFILES}, {len(descartes['ofertas'])} oferta(s) fora de "
                  f"{OFFERS}, {len(descartes['posteriores'])} posterior(es) malformado(s)")
            self._persist()

        return True

    # ── Interface consumida pelo agente (Módulo 5) ───────────────────────
    def choose_offer(self, profile: str, risk_score: float, mrr: float | None = None) -> str:
        """Thompson Sampling em TODA a faixa de risco: amostra um posterior por
        oferta e devolve a que maximiza o e-Profit com a taxa amostrada.

        `risk_score` não desvia mais a decisão em ponto nenhum da faixa — quem lê
        a criticidade é o tom da mensagem, via `is_critical_risk`.
        """
        if mrr is None:
            mrr = MRR_TIPICO.get(profile, MRR_TIPICO["default"])
        ltv_retido = MESES_LTV_RETIDO * mrr

        perfil = self.state.get(profile, self.state["CLT"])
        amostras = {o: self.rng.beta(s["alpha"], s["beta"]) for o, s in perfil.items()}
        return max(amostras, key=lambda o: amostras[o] * ltv_retido - offer_cost(o, mrr))

    def record_outcome(self, profile: str, offer: str, accepted: bool):
        """Atualiza o posterior após saber se o cliente aceitou e persiste."""
        s = self.state.setdefault(profile, {o: {"alpha": 1.0, "beta": 1.0} for o in OFFERS})
        s = s.setdefault(offer, {"alpha": 1.0, "beta": 1.0})
        if accepted:
            s["alpha"] += 1.0
        else:
            s["beta"] += 1.0
        self._persist()

    def conversion_rates(self, profile: str) -> dict:
        """Média do posterior por oferta para um perfil."""
        perfil = self.state.get(profile, self.state["CLT"])
        return {
            o: round(s["alpha"] / (s["alpha"] + s["beta"]), 3)
            for o, s in perfil.items()
        }

    def _persist(self):
        try:
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except OSError as e:
            print(f"[BANDIT] Falha ao persistir estado: {e}")

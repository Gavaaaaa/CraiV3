"""
crai/churn_voluntary/risk_scorer.py
Calcula o risk_score a partir de eventos do Segment SDK.

Eventos suportados:
    Cancellation Page Viewed  → risco fixo alto (intenção explícita)
    Downgrade Clicked         → risco fixo médio-alto
    Session Started           → risco calculado por inatividade + uso

Além do risco, classifica a CRITICIDADE — o rótulo que define o tom da
mensagem. Criticidade não desvia fluxo nem muda oferta: a CRAI atende todo
mundo sozinha, e quem muda é a forma de falar.

PONTO DE EXTENSÃO PARA O MODELO TREINADO (Sprint 6). `calculate_risk` consulta,
antes das regras, se existe um modelo em `crai/models/`. Havendo, ele decide;
não havendo — o caso de HOJE —, valem as regras fixas, com resultado IDÊNTICO
ao de antes deste sprint. É o mesmo `try/load/fallback` dos módulos de ML do
involuntário (`ml/anomaly_detector.py`, `ml/failure_classifier.py`), e não uma
hierarquia de classes: a diferença entre "tem modelo" e "não tem" cabe num `if`.

Como plugar o modelo quando ele existir está em `README_treino.md`, ao lado
deste arquivo.
"""

import json
import math
import os
from pathlib import Path

from .offer_bandit import is_critical_risk

HIGH_RISK_THRESHOLD = 0.75          # piso do "alto"; o teto é o CRITICAL do bandit
HIGH_VALUE_MRR_DEFAULT = 2000.0     # override: CRAI_HIGH_VALUE_MRR_THRESHOLD

FIXED_RISK = {
    "Cancellation Page Viewed": 0.90,
    "Downgrade Clicked":        0.75,
}

# ── Ponto de extensão: modelo treinado de risco voluntário ───────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = BASE_DIR / "models"
MODELO_PATH = MODELS_DIR / "voluntary_risk.joblib"
MODELO_META_PATH = MODELS_DIR / "voluntary_risk_meta.json"

# A ORDEM É O CONTRATO. Um modelo treinado com as colunas em outra ordem produz
# número plausível e errado — o pior tipo de defeito, porque não levanta nada. O
# `meta` declara a ordem com que o modelo foi treinado e `carregar_modelo`
# recusa carregar se ela não bater com esta lista.
FEATURES_DE_RISCO = [
    "days_since_last",
    "features_used_30d",
    "mrr",
    "evento_cancelamento",
    "evento_downgrade",
    "evento_sessao",
]

_modelo = None
_modelo_consultado = False


def _vetor_de_features(event: str, props: dict) -> list:
    """As features de `FEATURES_DE_RISCO`, nessa ordem, a partir do evento.

    Numérico ausente vira 0.0 e não `None`: o dataset de treino grava NULL para
    o que não veio (ver `retention_log`), e quem preenche o NULL é a fase de
    treino, com a estratégia que ela escolher. Aqui, na inferência, o vetor
    precisa ser completo.
    """
    return [
        float(_numero_utilizavel(props.get("days_since_last")) or 0.0),
        float(_numero_utilizavel(props.get("features_used_30d")) or 0.0),
        float(_numero_utilizavel(props.get("mrr")) or 0.0),
        1.0 if event == "Cancellation Page Viewed" else 0.0,
        1.0 if event == "Downgrade Clicked" else 0.0,
        1.0 if event == "Session Started" else 0.0,
    ]


def carregar_modelo(forcar: bool = False) -> bool:
    """Carrega o modelo de risco de `crai/models/`, se houver. Cacheia a resposta.

    Mesmo contrato dos módulos de ML do involuntário: devolve bool, loga o
    motivo, e NUNCA levanta — sem modelo o pipeline segue com as regras fixas.

    A resposta é cacheada (inclusive a negativa): um deploy não descobre um
    modelo novo sem reiniciar, igual ao `load()` do classificador e do
    autoencoder. `forcar=True` existe para os testes.
    """
    global _modelo, _modelo_consultado

    if _modelo_consultado and not forcar:
        return _modelo is not None

    _modelo_consultado = True
    _modelo = None

    if not MODELO_PATH.exists():
        # Silencioso: é o estado normal do projeto hoje, e um aviso a cada
        # importação vira ruído que ninguém lê.
        return False

    try:
        import joblib

        with open(MODELO_META_PATH, encoding="utf-8") as f:
            meta = json.load(f)

        if meta.get("features") != FEATURES_DE_RISCO:
            print(f"[RISK-VOL] Modelo IGNORADO: `features` do meta não bate com "
                  f"FEATURES_DE_RISCO. Esperado {FEATURES_DE_RISCO}, "
                  f"meta declara {meta.get('features')}. Um modelo com as "
                  f"colunas em outra ordem devolve número plausível e errado.")
            return False

        _modelo = joblib.load(MODELO_PATH)
        print(f"[RISK-VOL] Modelo de risco carregado de {MODELO_PATH} "
              f"({meta.get('algoritmo', 'algoritmo não declarado')}, "
              f"treinado em {meta.get('treinado_em', 'data não declarada')})")
        return True
    except FileNotFoundError:
        print(f"[RISK-VOL] {MODELO_META_PATH.name} não encontrado — o modelo "
              f"exige o meta com a ordem das features. Usando regras fixas.")
        return False
    except Exception as e:                        # noqa: BLE001
        print(f"[RISK-VOL] Erro ao carregar modelo ({e}) — usando regras fixas")
        return False


def modelo_ativo() -> bool:
    """Se a decisão de risco está vindo de modelo treinado ou das regras."""
    carregar_modelo()
    return _modelo is not None


def _risco_do_modelo(event: str, props: dict):
    """O risco segundo o modelo, ou None para cair nas regras.

    Qualquer falha — modelo ausente, `predict` que levanta, saída fora de
    [0, 1] — devolve None. Um modelo quebrado não pode derrubar o ciclo de
    retenção nem produzir risco absurdo: as regras fixas continuam sendo o
    chão do sistema.
    """
    if not carregar_modelo():
        return None

    try:
        bruto = _modelo.predict_proba([_vetor_de_features(event, props)])[0][1]
    except AttributeError:
        try:
            bruto = _modelo.predict([_vetor_de_features(event, props)])[0]
        except Exception as e:                    # noqa: BLE001
            print(f"[RISK-VOL] Modelo falhou ({e}) — caindo nas regras fixas")
            return None
    except Exception as e:                        # noqa: BLE001
        print(f"[RISK-VOL] Modelo falhou ({e}) — caindo nas regras fixas")
        return None

    try:
        risco = float(bruto)
    except (TypeError, ValueError):
        print(f"[RISK-VOL] Modelo devolveu {bruto!r}, que não é número — regras fixas")
        return None

    if not math.isfinite(risco) or not 0.0 <= risco <= 1.0:
        print(f"[RISK-VOL] Modelo devolveu {risco!r} fora de [0,1] — regras fixas")
        return None

    return round(risco, 3)


def _risco_por_regras(event: str, props: dict) -> float:
    """As regras fixas. Este corpo é o `calculate_risk` de antes do Sprint 6,
    palavra por palavra — sem modelo, o comportamento observável não mudou."""
    if event in FIXED_RISK:
        return FIXED_RISK[event]

    if event == "Session Started":
        days = props.get("days_since_last", 0)
        features = props.get("features_used_30d", 10)
        # Risco sobe com inatividade, cai com uso de features
        risk = min(1.0, (days / 30) * 0.7 + max(0, (5 - features) / 5) * 0.3)
        return round(risk, 3)

    return 0.0   # evento desconhecido — não dispara nada


def _risco(event: str, props: dict) -> float:
    """O núcleo: modelo treinado se houver, regras fixas se não.

    É UMA função para os dois caminhos de dado do self-service — o evento do
    SDK (`calculate_risk`) e a linha da base importada (`risco_por_features`).
    Regra ou modelo, os dois batem aqui; só muda de onde vêm os números.
    """
    risco = _risco_do_modelo(event, props)
    return risco if risco is not None else _risco_por_regras(event, props)


def calculate_risk(event: str, props: dict) -> float:
    """O risk_score do evento: modelo treinado se houver, regras fixas se não.

    `props` entra CRU, como sempre entrou — inclusive chave presente com valor
    `None`, que as regras tratam do jeito que sempre trataram. É por isso que
    esta função chama `_risco` direto em vez de passar por
    `risco_por_features`: lá, `None` significa "a planilha não tinha", e virar
    default aqui seria mudar o comportamento observável de um caminho que o
    Sprint 6 travou com teste de regressão.
    """
    return _risco(event, props)


# O evento que representa "dado estático de cadastro": é o único que hoje usa
# `days_since_last`/`features_used_30d` nas regras fixas, e o que o modelo
# treinado verá marcado em `evento_sessao`.
EVENTO_DADO_ESTATICO = "Session Started"


def risco_por_features(days_since_last=None, features_used_30d=None, mrr=None,
                       event: str | None = None) -> float:
    """O MESMO risco de `calculate_risk`, sem exigir um evento pontual.

    Para a base importada (Sprint 3): a linha da planilha não é um evento de
    comportamento, é uma foto. Trata-se como "Session Started" — o caso das
    regras que lê inatividade e uso — a não ser que `event` diga outra coisa.

    `None` aqui é AUSÊNCIA ("a planilha não tinha esta coluna"), e vira o
    mesmo default que as regras aplicam a chave ausente no payload do SDK
    (`days=0`, `features=10`). ATENÇÃO — os dois defaults juntos dão risco
    0.0, que é "sem risco nenhum", não "não sei avaliar". Quem chama com os
    dois ausentes tem que decidir ANTES se quer um número: `batch_scoring`
    não chama, e marca a linha como `dado_insuficiente`.
    """
    props = {}
    if days_since_last is not None:
        props["days_since_last"] = days_since_last
    if features_used_30d is not None:
        props["features_used_30d"] = features_used_30d
    if mrr is not None:
        props["mrr"] = mrr
    return _risco(EVENTO_DADO_ESTATICO if event is None else event, props)


def classify_profile(props: dict) -> str:
    """Reaproveita o mesmo critério do churn involuntário (CV de pagamentos)."""
    return props.get("billing_profile", "CLT")


def limiar_de_alto_valor() -> float:
    """Lido a cada chamada, não no import.

    O ambiente muda depois que o módulo já está carregado — a demo seta env
    antes de rodar, o teste usa monkeypatch. Um valor congelado no import
    ignoraria os dois. Env ausente ou impossível de ler cai no default, sem
    derrubar o pipeline por causa de uma variável mal digitada.
    """
    bruto = os.getenv("CRAI_HIGH_VALUE_MRR_THRESHOLD")
    if bruto is None:
        return HIGH_VALUE_MRR_DEFAULT
    try:
        return float(bruto)
    except (TypeError, ValueError):
        print(f"[CHURN-VOL] CRAI_HIGH_VALUE_MRR_THRESHOLD={bruto!r} não é número "
              f"— usando o default R$ {HIGH_VALUE_MRR_DEFAULT:.2f}")
        return HIGH_VALUE_MRR_DEFAULT


def mrr_utilizavel(bruto) -> float | None:
    """O MRR do payload do Segment, ou None quando não dá para usar como número.

    `props` é payload bruto de terceiro: `mrr` chega como número, como texto,
    como lista, como `None`, ou não chega. Comparar qualquer uma dessas com um
    limiar levanta `TypeError` dentro do nó do grafo, três camadas abaixo da
    borda que validou o evento. Ausência de MRR não é erro — é só a ausência do
    sinal de alto valor, e aí o risco classifica sozinho.

    **Texto é recusado de propósito, não por preguiça.** `"10,000"` é dez mil em
    en-US e dez em pt-BR, e o payload do Segment não declara locale. Adivinhar
    rebaixaria o tom de um cliente de R$ 10.000 para o texto padrão em silêncio
    — o mesmo defeito que a A1 mediu no valor de cobrança (P0-1). O parser que
    resolve isso direito existe (`payment_gateway._para_float`), mas mora no
    lado involuntário; promovê-lo a helper compartilhado é uma frente própria,
    não um efeito colateral deste sprint. Até lá, texto = sinal ausente.

    Recusa também `inf`/`NaN` (que `json.loads` aceita como literal) e inteiro
    grande demais para virar float — os dois derrubariam a comparação adiante.
    """
    return _numero_utilizavel(bruto)


def _numero_utilizavel(bruto) -> float | None:
    """Número real, finito e não negativo, ou None. Serve MRR e as features.

    Extraído de `mrr_utilizavel` sem mudar uma linha do que ele fazia: o vetor
    do modelo (Sprint 6) precisa da mesma coerção para `days_since_last` e
    `features_used_30d`, e chamar uma função com "mrr" no nome para converter
    dias seria mentira no lugar mais fácil de acreditar nela.
    """
    if bruto is None or isinstance(bruto, bool) or not isinstance(bruto, (int, float)):
        return None
    try:
        valor = float(bruto)
    except (OverflowError, ValueError):
        # Inteiro JSON de precisão arbitrária: 401 dígitos chegam como `int` e
        # derrubam `float()`. Ver `payment_gateway._para_float`, mesmo caso.
        return None
    if not math.isfinite(valor) or valor < 0:
        return None
    return valor


def classify_criticality(risk_score: float, mrr: float | None) -> str:
    """Rótulo de tom: "critico" | "alto" | "padrao".

    Duas portas levam a "critico", e elas são independentes:

      RISCO   — `is_critical_risk` (>= 0.90). O mesmo limiar que o bandit usa,
                importado em vez de recopiado: dois 0.90 em arquivos
                diferentes divergem no dia em que um deles mudar.
      VALOR   — MRR >= `limiar_de_alto_valor()` (default R$ 2.000). Um cliente
                grande com risco baixo ainda merece a mensagem cuidadosa; é
                caro demais para receber o texto padrão.

    "alto" é a faixa [0.75, 0.90): risco declarado, ainda não crítico.
    """
    if is_critical_risk(risk_score):
        return "critico"

    mrr = mrr_utilizavel(mrr)
    if mrr is not None and mrr >= limiar_de_alto_valor():
        return "critico"

    if risk_score >= HIGH_RISK_THRESHOLD:
        return "alto"

    return "padrao"

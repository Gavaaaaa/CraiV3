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

# ── Contratos de artefato aceitos (promoção v3, Bloco 1, 30/09/2026) ─────
# A tabela é FECHADA: qualquer outra combinação de `meta["contrato"]`,
# `meta["features_versao"]` e `meta["features"]` é recusada, como sempre foi.
#   legado — meta sem "contrato" e sem "features_versao", features ==
#            FEATURES_DE_RISCO. Vetor como sempre: ausente vira 0.0.
#   v3     — meta["contrato"] == "v3", features_versao == 1, features ==
#            FEATURES_DE_RISCO_V3 (`crai.ml.voluntario_v3`). Ausente vira NaN:
#            para o HistGradientBoosting "0 chamados" e "não sei" são coisas
#            diferentes, e o modelo aprendeu o NaN no treino (aumento por
#            máscara). O meta de `models/v3/` NÃO declara "contrato": só o meta
#            de produção, gravado pela promoção, declara.
CONTRATO_LEGADO = "legado"
CONTRATO_V3 = "v3"
_contrato = CONTRATO_LEGADO

# "Dado suficiente" para o modelo v3 decidir (D1): pelo menos uma das duas
# colunas que a régua lê E pelo menos este número das colunas comportamentais
# que só o v3 usa. 0 foi medido no Bloco 0 (holdout v3: 0,6377 contra 0,6156
# da melhor régua); o Bloco 3 confirma por GroupKFold, e se a vantagem ficar
# abaixo do desvio, passa a 1.
N_MINIMO_COLUNAS_COMPORTAMENTAIS = 0
MOTIVO_SEM_DADO_PARA_O_MODELO = (
    "modelo v3 ativo, mas sem days_since_last e sem features_used_30d: "
    "dado insuficiente para o modelo")


def _features_v3() -> tuple:
    """(FEATURES_DE_RISCO_V3, versão). Import preguiçoso: `voluntario_v3` traz o
    pandas, e este módulo é importado no caminho de toda requisição."""
    from ..ml.voluntario_v3 import FEATURES_DE_RISCO_V3, FEATURES_DE_RISCO_V3_VERSAO
    return list(FEATURES_DE_RISCO_V3), FEATURES_DE_RISCO_V3_VERSAO


def colunas_comportamentais_v3() -> list:
    """As colunas do contrato v3 que o contrato legado não tem (as 9 do Bloco 0)."""
    return [f for f in _features_v3()[0] if f not in FEATURES_DE_RISCO]


def contrato_do_meta(meta) -> str | None:
    """O contrato que este meta declara E cumpre, ou None (recusar)."""
    if not isinstance(meta, dict):
        return None
    contrato = meta.get("contrato")
    if contrato is None:
        if meta.get("features_versao") is None and meta.get("features") == FEATURES_DE_RISCO:
            return CONTRATO_LEGADO
        return None
    if contrato == CONTRATO_V3:
        lista, versao = _features_v3()
        if meta.get("features_versao") == versao and meta.get("features") == lista:
            return CONTRATO_V3
    return None


def _vetor_v3(event: str, props: dict) -> list:
    """As features de `FEATURES_DE_RISCO_V3`, nessa ordem. Ausente = NaN."""
    vetor = []
    for f in _features_v3()[0]:
        if f == "evento_cancelamento":
            vetor.append(1.0 if event == "Cancellation Page Viewed" else 0.0)
        elif f == "evento_downgrade":
            vetor.append(1.0 if event == "Downgrade Clicked" else 0.0)
        elif f == "evento_sessao":
            vetor.append(1.0 if event == "Session Started" else 0.0)
        else:
            v = _numero_utilizavel(props.get(f))
            vetor.append(float("nan") if v is None else float(v))
    return vetor


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
    global _modelo, _modelo_consultado, _contrato

    if _modelo_consultado and not forcar:
        return _modelo is not None

    _modelo_consultado = True
    _modelo = None
    _contrato = CONTRATO_LEGADO

    if not MODELO_PATH.exists():
        # Silencioso: é o estado normal do projeto hoje, e um aviso a cada
        # importação vira ruído que ninguém lê.
        return False

    try:
        import joblib

        with open(MODELO_META_PATH, encoding="utf-8") as f:
            meta = json.load(f)

        contrato = contrato_do_meta(meta)
        if contrato is None:
            print(f"[RISK-VOL] Modelo IGNORADO: o meta não cumpre nenhum contrato "
                  f"aceito (legado = FEATURES_DE_RISCO {FEATURES_DE_RISCO} sem "
                  f"'contrato'; ou contrato 'v3' com features_versao e "
                  f"FEATURES_DE_RISCO_V3). Meta declara contrato="
                  f"{meta.get('contrato')!r}, features_versao="
                  f"{meta.get('features_versao')!r}, features {meta.get('features')}. "
                  f"Um modelo com as colunas em outra ordem devolve número "
                  f"plausível e errado.")
            return False

        _modelo = joblib.load(MODELO_PATH)
        _contrato = contrato
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


NOME_DO_MODELO = "risk_scorer_voluntario"
REGRA_DE_RISCO = "risk_scorer.risco_por_features"


def identidade_do_modelo() -> tuple[str, str | None]:
    """(modelo, versão) de quem está decidindo o risco — para a trilha do Art. 20.

    Com modelo carregado: (`NOME_DO_MODELO`, `<treinado_em>#<hash do .joblib>`).
    Sem: (`"regra"`, None), e o nome da regra vai em `REGRA_DE_RISCO`. Nunca
    levanta: meta ilegível vira versão só pelo hash, ou None.
    """
    if not modelo_ativo():
        return "regra", None
    from .retention_log import versao_do_artefato

    meta = None
    try:
        with open(MODELO_META_PATH, encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:                             # noqa: BLE001
        meta = None
    return NOME_DO_MODELO, versao_do_artefato(meta, MODELO_PATH)


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
        bruto = _modelo.predict_proba([_vetor_do_contrato(event, props)])[0][1]
    except AttributeError:
        try:
            bruto = _modelo.predict([_vetor_do_contrato(event, props)])[0]
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
    return _decidir(event, props)[0]


def _dado_suficiente_v3(props: dict) -> bool:
    """D1: dias OU uso presente, e pelo menos N_MINIMO_COLUNAS_COMPORTAMENTAIS
    das colunas comportamentais do v3."""
    if not isinstance(props, dict):
        return False
    if (_numero_utilizavel(props.get("days_since_last")) is None
            and _numero_utilizavel(props.get("features_used_30d")) is None):
        return False
    presentes = sum(1 for c in colunas_comportamentais_v3()
                    if _numero_utilizavel(props.get(c)) is not None)
    return presentes >= N_MINIMO_COLUNAS_COMPORTAMENTAIS


def _vetor_do_contrato(event: str, props: dict) -> list:
    return _vetor_v3(event, props) if _contrato == CONTRATO_V3 else _vetor_de_features(event, props)


def _decidir(event: str, props: dict) -> tuple:
    """(risco, decidiu_o_modelo, motivo_da_regra).

    Contrato legado: exatamente o de antes (modelo se houver e funcionar, régua
    se não). Contrato v3: o modelo só decide com dado suficiente (D1); sem ele,
    a régua decide e o motivo vai para a trilha.
    """
    if carregar_modelo() and _contrato == CONTRATO_V3 and not _dado_suficiente_v3(props):
        return _risco_por_regras(event, props), False, MOTIVO_SEM_DADO_PARA_O_MODELO
    risco = _risco_do_modelo(event, props)
    if risco is not None:
        return risco, True, None
    motivo = ("modelo ativo, mas falhou ou devolveu valor fora de [0, 1]"
              if _modelo is not None else None)
    return _risco_por_regras(event, props), False, motivo


def decidir_risco(event: str, props: dict) -> tuple:
    """(risco, decidiu_o_modelo, motivo_da_regra), sem o TreeSHAP.

    Para quem pontua a base inteira (`batch_scoring`) e só precisa saber quem
    decidiu cada linha; a explicação por SHAP fica para o disparo, linha a
    linha (`avaliar_risco`)."""
    return _decidir(event, props)


def entradas_comportamentais(props: dict) -> dict:
    """As colunas comportamentais do v3 presentes em `props`, para as entradas
    da trilha. Vazio sem modelo v3 ativo: o registro legado não muda."""
    if _modelo is None or _contrato != CONTRATO_V3 or not isinstance(props, dict):
        return {}
    saida = {}
    for c in colunas_comportamentais_v3():
        v = _numero_utilizavel(props.get(c))
        if v is not None:
            saida[c] = v
    return saida


def avaliar_risco(event: str, props: dict) -> dict:
    """O risco E o registro de quem decidiu, para a trilha do Art. 20 (P1, P3).

    Devolve `risco` (o mesmo número de `calculate_risk`), `modelo` e
    `modelo_versao` DESTA decisão (a régua pode decidir com o modelo carregado,
    e então aqui sai "regra"), `motivo_da_regra` quando o modelo estava ativo e
    a régua decidiu, e `contribuicoes` do TreeSHAP quando o modelo v3 decidiu
    (None se não, ou se o SHAP falhar: nunca inventa contribuição). Nunca
    levanta por causa da explicação.
    """
    risco, por_modelo, motivo = _decidir(event, props)
    if por_modelo:
        modelo, versao = identidade_do_modelo()
    else:
        modelo, versao = "regra", None
    contribuicoes = None
    if por_modelo and _contrato == CONTRATO_V3:
        from .explicacao_v3 import contribuicoes_shap
        contribuicoes = contribuicoes_shap(_modelo, _features_v3()[0],
                                           _vetor_v3(event, props), event)
    return {"risco": risco, "modelo": modelo, "modelo_versao": versao,
            "motivo_da_regra": motivo, "contribuicoes": contribuicoes,
            "contrato": _contrato if por_modelo else None}


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


def classify_criticality(risk_score: float, mrr: float | None,
                         days_since_last=None, features_used_30d=None) -> str:
    """Rótulo de tom: "critico" | "alto" | "padrao".

    Duas portas levam a "critico", e elas são independentes:

      RISCO   — `is_critical_risk` (>= 0.90). O mesmo limiar que o bandit usa,
                importado em vez de recopiado: dois 0.90 em arquivos
                diferentes divergem no dia em que um deles mudar.
      VALOR   — MRR >= `limiar_de_alto_valor()` (default R$ 2.000). Um cliente
                grande com risco baixo ainda merece a mensagem cuidadosa; é
                caro demais para receber o texto padrão.

    "alto" é a faixa [0.75, 0.90): risco declarado, ainda não crítico.

    PISO ABSOLUTO. Quando `days_since_last` e/ou `features_used_30d` são
    passados — o caminho da régua da base, em que o risco é POSICIONAL —,
    "alto" e "critico" por risco exigem também `sinal_absoluto_de_desengajamento`.
    Sem sinal, o cliente pode estar no topo da lista e continua "padrao": o
    sistema não inventa vítima numa base saudável. Sem os dois argumentos (o
    SDK e a régua global), o comportamento é o de sempre: lá o risco já é
    absoluto por construção (0,75 exige >= 19,3 dias mesmo com uso zero). A
    porta do VALOR não passa pelo piso — é sobre dinheiro, não sobre risco.
    """
    exige_sinal = days_since_last is not None or features_used_30d is not None
    risco_conta = (not exige_sinal
                   or sinal_absoluto_de_desengajamento(days_since_last, features_used_30d))

    if risco_conta and is_critical_risk(risk_score):
        return "critico"

    mrr = mrr_utilizavel(mrr)
    if mrr is not None and mrr >= limiar_de_alto_valor():
        return "critico"

    if risco_conta and risk_score >= HIGH_RISK_THRESHOLD:
        return "alto"

    return "padrao"


# ── Régua da base: o risco pela POSIÇÃO do cliente na própria base ────────
#
# `_risco_por_regras` compara `days_since_last` com a constante 30 e
# `features_used_30d` com a constante 5 — a mesma régua para toda base do
# mundo. Um SaaS cujos clientes entram todo dia e outro cujos clientes entram
# uma vez por mês recebem o mesmo número para "7 dias sem login".
#
# Aqui a régua é a distribuição da própria base: quem calcula os percentis é
# `batch_scoring.regua_da_base` (em memória, a partir das linhas que ele já
# tem na mão); este módulo só converte posição em risco. Os PESOS são os
# mesmos das regras fixas (0,7 inatividade, 0,3 uso), de propósito: muda a
# régua, não a fórmula. `_risco_por_regras` continua intacta e continua sendo
# o chão do sistema — é para onde `batch_scoring` volta quando a base é
# pequena demais para ter distribuição.
#
# CADA COLUNA OLHA PARA A CAUDA CERTA. Em `days_since_last`, MAIS é pior, e a
# régua são os percentis ALTOS (p50/p75/p90): "acima de 90% da sua base". Em
# `features_used_30d`, MENOS é pior, e a régua são os percentis BAIXOS
# (p10/p25/p50): "menos que 90% da sua base". Medir p90 de uso descreveria
# justamente quem está bem.
#
# POSIÇÃO ORDENA; CRITICIDADE EXIGE SINAL ABSOLUTO. O risco posicional é
# relativo por construção: os 10% mais frios de QUALQUER base ficam "acima de
# 90% da sua base", inclusive numa base em que ninguém está em risco. Por isso
# `classify_criticality` só rotula "alto"/"critico" quando, além da posição,
# há um sinal absoluto de desengajamento (ver `sinal_absoluto_de_desengajamento`).
# Sem ele, o cliente fica no topo da lista — "por quem eu começo?" — com
# criticidade "padrao". O sistema não pode gritar numa base saudável.

PESO_INATIVIDADE = 0.7
PESO_USO = 0.3

# Piso absoluto de desengajamento. Escolhido a partir das bases de
# demonstração e da regra global, não de número redondo:
#   - 7 dias é um ciclo semanal inteiro sem entrar — o ritmo mais curto que um
#     SaaS B2B tem. Fica ACIMA do máximo da base de uso diário (5 dias) e
#     ABAIXO da mediana da base de uso mensal (25): o piso nunca decide a
#     demonstração, a posição decide. Na base de 500 clientes de exemplo,
#     6,8% cruzam esse piso; o resto não pode virar alarme.
#   - A própria regra global, o chão do sistema, dá 0,463 a 7 dias sem login
#     com uso zero: quase metade do risco máximo. É sinal também na escala
#     absoluta.
#   - Nenhuma funcionalidade em 30 dias é abandono em qualquer SaaS, qualquer
#     base; 1 ou mais já depende do produto, e aí quem julga é a posição.
PISO_ABSOLUTO_DIAS_SEM_LOGIN = 7.0
PISO_ABSOLUTO_FUNCIONALIDADES = 0.0


def sinal_absoluto_de_desengajamento(days_since_last, features_used_30d) -> bool:
    """Há sinal de abandono em escala absoluta, independente da base?

    Sim quando o cliente está há pelo menos `PISO_ABSOLUTO_DIAS_SEM_LOGIN`
    dias sem login, OU usou no máximo `PISO_ABSOLUTO_FUNCIONALIDADES`
    funcionalidades em 30 dias. Valor desconhecido não conta nem a favor nem
    contra: sem nenhum dos dois, não há sinal.
    """
    dias = _numero_utilizavel(days_since_last)
    uso = _numero_utilizavel(features_used_30d)
    if dias is not None and dias >= PISO_ABSOLUTO_DIAS_SEM_LOGIN:
        return True
    if uso is not None and uso <= PISO_ABSOLUTO_FUNCIONALIDADES:
        return True
    return False


def _interpolar(x: float, nos: list, alem: float) -> float:
    """Interpolação linear por nós (x crescente). Nós com o mesmo x colapsam
    ficando com a afirmação MAIS FRACA (o menor y): se p50 e p90 coincidem,
    40% da base está naquele valor, e quem está nele não está "acima de 90%"."""
    limpos: list = []
    for px, py in nos:
        if limpos and px <= limpos[-1][0]:
            limpos[-1] = (limpos[-1][0], min(limpos[-1][1], py))
        else:
            limpos.append((px, py))
    if x <= limpos[0][0]:
        return limpos[0][1]
    for (x0, y0), (x1, y1) in zip(limpos, limpos[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return alem


def posicao_na_base(valor, percentis: dict) -> float:
    """Posição estimada (0..1) de `valor` na cauda ALTA da distribuição.

    Para `days_since_last`. `percentis` traz p50/p75/p90. Interpolação linear
    por (0, 0) → (p50, 0,5) → (p75, 0,75) → (p90, 0,9) → (2·p90, 1,0); acima
    disso, 1,0. Zero é posição zero: acessou hoje.
    """
    x = float(valor)
    if x <= 0.0:
        return 0.0
    p90 = float(percentis["p90"])
    nos = [(0.0, 0.0), (float(percentis["p50"]), 0.5), (float(percentis["p75"]), 0.75),
           (p90, 0.9), (2.0 * p90, 1.0)]
    return _interpolar(x, nos, alem=1.0)


def desengajamento_de_uso(valor, percentis: dict) -> float:
    """Quanto `valor` está na cauda BAIXA da distribuição de uso (0..1).

    Para `features_used_30d`. `percentis` traz p10/p25/p50. 1,0 é usar nada;
    0,9 é estar no p10 ("menos que 90% da sua base"); 0,5 é a mediana; zero a
    partir do dobro da mediana. É o complemento da posição, medido onde o
    sinal mora.
    """
    x = float(valor)
    p50 = float(percentis["p50"])
    nos = [(0.0, 1.0), (float(percentis["p10"]), 0.9), (float(percentis["p25"]), 0.75),
           (p50, 0.5), (2.0 * p50, 0.0)]
    return _interpolar(max(x, 0.0), nos, alem=0.0)


def risco_por_posicao(days_since_last, features_used_30d, regua: dict) -> float:
    """O risco pela posição do cliente na própria base, em vez de pelas constantes.

    `regua` é o dicionário de `batch_scoring.regua_da_base`, com os percentis
    altos de `days_since_last` e os baixos de `features_used_30d`. Ausência
    tem o MESMO sentido das regras fixas: dias ausentes = acessou hoje
    (posição 0), uso ausente = como os mais ativos da base (desengajamento 0)
    — nenhum dos dois inventa risco.
    """
    dias = _numero_utilizavel(days_since_last)
    uso = _numero_utilizavel(features_used_30d)
    pos_inatividade = (posicao_na_base(dias, regua["days_since_last"])
                       if dias is not None else 0.0)
    desengajamento = (desengajamento_de_uso(uso, regua["features_used_30d"])
                      if uso is not None else 0.0)
    risco = min(1.0, PESO_INATIVIDADE * pos_inatividade + PESO_USO * desengajamento)
    return round(risco, 3)

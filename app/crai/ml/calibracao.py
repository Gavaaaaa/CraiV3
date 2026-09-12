"""crai/ml/calibracao.py — Parâmetros de distribuição do gerador sintético.

Duas fontes de parâmetros, escolhidas pelo argumento `fonte` dos geradores e
dos `train()`:

    "sintetico"            PARAMETROS_DISTRIBUICAO — os valores escritos à mão
                           que o gerador sempre teve. É o default e produz
                           exatamente o mesmo dataset de antes deste módulo
                           existir (há teste que trava isso por hash).
    "sintetico_calibrado"  os mesmos parâmetros, com os valores MEDIDOS em
                           doadores reais no lugar dos inventados, lidos de
                           `models/calibracao.json`. O arquivo é gerado por
                           `python -m crai.scripts.preparar_amostra_real` e é a
                           única coisa de `models/` que fica no git (ver
                           `.gitignore`): é um punhado de números medidos, não
                           um binário.

Doadores reais (detalhe em `docs/DATA_CARD.md` e no próprio JSON):

    A  Olist (300 transações reais)  → valor da fatura, hora/dia-da-semana/
                                        dia-do-mês da cobrança
    B  BACEN SGS 21084               → peso de `insufficient_funds` nos códigos
    E  E-Commerce Customer Churn     → days_since_last / days_since_last_login,
       (Kaggle, ankitverma2010)         features_used_30d, avg_session_min,
                                        tickets_30d, nps_last

O que NÃO tem doador continua inventado — e o JSON diz isso feature a feature
(`proveniencia_features`), com um dos três status:

    "ancorada"               distribuição medida numa coluna real
    "proxy_fraco"            medida numa coluna real que só se parece com a
                             feature (unidade, granularidade ou domínio diferem)
    "sintetica_sem_doador"   100% inventada; não existe fonte pública

Este módulo NÃO baixa nada e NÃO calcula estatística: só lê o JSON e devolve
dicionários. Quem mede é o script; quem consome é o gerador.
"""

import copy
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = BASE_DIR / "models"
CALIBRACAO_PATH = MODELS_DIR / "calibracao.json"

FONTES_ACEITAS = ("sintetico", "sintetico_calibrado")

STATUS_ACEITOS = ("ancorada", "proxy_fraco", "sintetica_sem_doador")

# ── Os parâmetros de HOJE, inventados, um por ponto do gerador ───────────
#
# Cada chave abaixo corresponde a um literal que estava embutido em
# `synthetic_data.py`. Os valores são os mesmos; só mudaram de lugar. Uma
# entrada `None` em `*_hist` significa "sem histograma empírico — usar a
# regra procedural de sempre" (picos de dia, curva de hora desenhada à mão).
PARAMETROS_DISTRIBUICAO = {
    # Módulo 1 — generate_dataset()
    "classifier": {
        "invoice_lognormal": {"mean": 5.8, "sigma": 0.7},
        "invoice_clip": [49.90, 9999.90],
        "peak_days": [5, 10, 15, 20, 30],
        "p_peak_day": 0.6,
        "day_of_month_hist": None,
        "hour_hist": None,
        "day_of_week_hist": None,
        "error_code_probs": [0.35, 0.20, 0.15, 0.10, 0.12, 0.08],
        # Ruído do rótulo. `ruido_rotulo_sd` é o N(0, 0.05) que sempre existiu
        # em `_calculate_recovery_probability`; `p_excecao_rotulo` é a fração
        # de linhas cujo rótulo é sorteado IGNORANDO a regra (exceção). Zero
        # no default porque o default não muda.
        "ruido_rotulo_sd": 0.05,
        "p_excecao_rotulo": 0.0,
    },
    # Módulo 2 — generate_behavioral_dataset()
    "behavioral": {
        "saudavel": {
            "days_since_last_login_scale": 1.5,
            "avg_session": {"loc": 22.0, "scale": 6.0},
            "tickets_lam": 1.2,
            "nps": {"loc": 8.2, "scale": 1.3},
        },
        "anomalo": {
            "days_since_last_login_scale": 9.0,
            "avg_session": {"loc": 6.0, "scale": 3.0},
            "tickets_lam": 4.5,
            "nps": {"loc": 5.5, "scale": 2.0},
        },
        # Fração de anômalos que "escondem" o sinal em parte das features
        # (churn silencioso) e de saudáveis com degradação passageira. Zero no
        # default: o rótulo default é 100% a população de origem.
        "p_excecao_anomalo": 0.0,
        "p_excecao_saudavel": 0.0,
    },
    # Módulo 3 — generate_liquidity_series()
    "liquidity": {
        # Choques que a série default não tem: salário atrasado e gasto
        # imprevisto. Zero no default.
        "p_atraso_salario": 0.0,
        "atraso_salario_max_dias": 0,
        "p_gasto_imprevisto": 0.0,
        "gasto_imprevisto": {"min": 0.5, "max": 1.5},
    },
    # risk_scorer voluntário — generate_voluntary_dataset() (novo)
    "voluntary": {
        "days_since_last": {"tipo": "uniforme_int", "min": 0, "max": 30, "hist": None},
        "features_used_30d": {"tipo": "poisson", "lam": 5.0, "hist": None},
        "mrr_lognormal": {"mean": 7.5, "sigma": 0.8, "clip": [99.0, 50_000.0]},
        "mix_eventos": {"Session Started": 0.85, "Downgrade Clicked": 0.10,
                        "Cancellation Page Viewed": 0.05},
        "ruido_rotulo_sd": 0.0,
        "p_excecao_rotulo": 0.0,
    },
}

# Ordem dos códigos de `error_code_probs` — a mesma de GATEWAY_ERROR_CODES em
# synthetic_data.py. Repetida aqui para o script de calibração não importar o
# gerador (que importa este módulo).
CODIGOS_DE_ERRO = [
    "insufficient_funds", "expired_card", "card_declined",
    "processing_error", "do_not_honor", "generic_decline",
]


class CalibracaoAusente(FileNotFoundError):
    """`fonte="sintetico_calibrado"` pedida sem `models/calibracao.json`."""


def validar_fonte(fonte: str) -> str:
    if fonte not in FONTES_ACEITAS:
        raise ValueError(
            f"fonte={fonte!r} nao e aceita; use uma de {FONTES_ACEITAS}")
    return fonte


def carregar_calibracao(caminho: Path = None) -> dict:
    """O JSON inteiro, ou CalibracaoAusente. Nunca inventa um default."""
    caminho = CALIBRACAO_PATH if caminho is None else Path(caminho)
    if not caminho.exists():
        raise CalibracaoAusente(
            f"{caminho} nao existe. Gere com "
            f"`python -m crai.scripts.preparar_amostra_real` (ou use "
            f"fonte='sintetico', que nao depende do arquivo).")
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def _mesclar(base: dict, novo: dict) -> dict:
    """Mescla recursiva: chaves de `novo` sobrepõem as de `base`."""
    saida = copy.deepcopy(base)
    for chave, valor in novo.items():
        if isinstance(valor, dict) and isinstance(saida.get(chave), dict):
            saida[chave] = _mesclar(saida[chave], valor)
        else:
            saida[chave] = copy.deepcopy(valor)
    return saida


def parametros(fonte: str = "sintetico", caminho: Path = None) -> dict:
    """Os parâmetros do gerador para a `fonte` pedida.

    "sintetico" devolve uma CÓPIA de PARAMETROS_DISTRIBUICAO (o chamador pode
    alterar sem contaminar o módulo). "sintetico_calibrado" devolve o default
    com os valores medidos por cima — só as chaves que o JSON traz mudam, o
    resto continua o de sempre. Se o JSON não existir, levanta: pedir
    calibração e receber palpite em silêncio é o erro que este projeto mais
    se esforça para não cometer.
    """
    validar_fonte(fonte)
    base = copy.deepcopy(PARAMETROS_DISTRIBUICAO)
    if fonte == "sintetico":
        return base
    calibracao = carregar_calibracao(caminho)
    return _mesclar(base, calibracao.get("parametros", {}))


def proveniencia_features(modelo: str, fonte: str = "sintetico",
                          caminho: Path = None) -> list:
    """Lista [{feature, status, fonte, coluna, nota}] do modelo, para a fonte.

    Para "sintetico" toda feature é "sintetica_sem_doador" por definição —
    nenhum parâmetro medido entra nesse caminho. Para "sintetico_calibrado" a
    lista vem do JSON, feature a feature, e é validada: status fora dos três
    aceitos ou feature sem status é erro, nunca omissão silenciosa.
    """
    validar_fonte(fonte)
    if fonte == "sintetico":
        calibracao = None
        try:
            calibracao = carregar_calibracao(caminho)
        except CalibracaoAusente:
            return []
        linhas = calibracao.get("proveniencia_features", {}).get(modelo, [])
        return [{**l, "status": "sintetica_sem_doador",
                 "fonte": None, "coluna": None,
                 "nota": "fonte='sintetico': nenhum parametro medido entra "
                         "neste caminho"} for l in linhas]

    calibracao = carregar_calibracao(caminho)
    linhas = calibracao.get("proveniencia_features", {}).get(modelo)
    if linhas is None:
        raise KeyError(f"calibracao.json sem proveniencia_features[{modelo!r}]")
    for linha in linhas:
        if linha.get("status") not in STATUS_ACEITOS:
            raise ValueError(
                f"feature {linha.get('feature')!r} de {modelo} com status "
                f"{linha.get('status')!r}; aceitos: {STATUS_ACEITOS}")
    return copy.deepcopy(linhas)


def resumo_proveniencia(modelo: str, fonte: str = "sintetico",
                        caminho: Path = None) -> dict:
    """Contagem por status + a lista — o que `train()` devolve e grava no meta."""
    linhas = proveniencia_features(modelo, fonte, caminho)
    contagem = {status: 0 for status in STATUS_ACEITOS}
    for linha in linhas:
        contagem[linha["status"]] += 1
    return {
        "modelo": modelo,
        "fonte": fonte,
        "n_features": len(linhas),
        "por_status": contagem,
        "ancoradas_em_real": [l["feature"] for l in linhas
                              if l["status"] == "ancorada"],
        "proxy_fraco": [l["feature"] for l in linhas
                        if l["status"] == "proxy_fraco"],
        "sinteticas_puras": [l["feature"] for l in linhas
                             if l["status"] == "sintetica_sem_doador"],
        "features": linhas,
    }


BIBLIOTECAS_CRITICAS = ("scikit-learn", "xgboost", "torch")


def conferir_meta(caminho: Path, tag: str) -> dict:
    """Lê o meta.json de um artefato e compara as versões gravadas com as atuais.

    Nunca levanta: um artefato antigo sem meta.json carrega como sempre
    carregou (devolve `{}`), e uma divergência de versão vira um aviso
    NOMINAL — "xgboost: treinado com 2.1.1, ambiente tem 2.2.0" — em vez do
    erro genérico (ou do carregamento silenciosamente errado) que um `.joblib`
    de outra versão produz. Quem decide se a divergência importa é quem lê o
    aviso; o que este código garante é que ela não passa despercebida.
    """
    caminho = Path(caminho)
    if not caminho.exists():
        return {}
    try:
        with open(caminho, encoding="utf-8") as f:
            meta = json.load(f)
    except (OSError, ValueError) as e:
        print(f"[{tag}] meta.json ilegivel ({e}) — versoes do treino desconhecidas")
        return {}

    gravadas = meta.get("versoes") or {}
    atuais = versoes_bibliotecas()

    def _base(v):
        # "2.13.0+cpu" e "2.13.0+cu130" sao o mesmo torch para fins de
        # serializacao: o sufixo local e o build, nao a versao.
        return str(v).split("+")[0] if v is not None else None

    divergentes = [
        f"{nome}: treinado com {gravadas.get(nome)}, ambiente tem {atuais.get(nome)}"
        for nome in BIBLIOTECAS_CRITICAS
        if nome in gravadas and _base(gravadas.get(nome)) != _base(atuais.get(nome))
    ]
    if divergentes:
        print(f"[{tag}] AVISO versao de biblioteca diferente do treino — "
              + "; ".join(divergentes)
              + ". O artefato carregou, mas confira o resultado ou retreine "
                "(requirements.txt fixa as versoes exatas).")
    if meta.get("fonte_usada"):
        print(f"[{tag}] artefato treinado com fonte={meta['fonte_usada']}, "
              f"n_amostras={meta.get('n_amostras')}")
    return meta


def versoes_bibliotecas() -> dict:
    """Versão exata de cada biblioteca que entra num artefato treinado.

    Vai para o `meta.json` de cada modelo. Um `.joblib` de sklearn 1.5 aberto
    com sklearn 1.7 carrega com warning ou carrega errado — e a mensagem
    genérica não diz o que mudou. Com a versão gravada ao lado do binário, a
    diferença é um `diff`, não uma investigação.
    """
    import platform

    versoes = {"python": platform.python_version()}
    for nome, modulo in (("scikit-learn", "sklearn"), ("xgboost", "xgboost"),
                         ("torch", "torch"), ("numpy", "numpy"),
                         ("pandas", "pandas"), ("joblib", "joblib"),
                         ("shap", "shap"), ("prophet", "prophet")):
        try:
            mod = __import__(modulo)
            versoes[nome] = str(getattr(mod, "__version__", "sem __version__"))
        except ImportError:
            versoes[nome] = None
    return versoes

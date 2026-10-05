"""crai/scripts/experimento_redes_neurais.py — Rodada 4, Fase 5: redes neurais como DESAFIANTES.

POR QUE EXISTE. O professor recomendou testar uma rede neural de classificação
no lugar do XGBoost + Random Forest (Módulo 1) e uma rede de regressão no lugar
do LSTM + Prophet (Módulo 3), com AS MESMAS features, para ver se as métricas
sobem. Este script mede isso e escreve o resultado. NADA É PROMOVIDO: ele só lê
`models/` (os artefatos de produção) e grava as redes em `models/experimentos_rn/`.

COMO RODAR (de `app/`):

    python -m crai.scripts.experimento_redes_neurais            # o experimento inteiro
    python -m crai.scripts.experimento_redes_neurais --rapido   # amostra pequena, para conferir

O treino completo NÃO roda dentro da suíte. Os testes (`tests/
test_experimento_redes_neurais.py`) conferem as garantias abaixo e a forma do
resultado, com uma amostra pequena.

AS GARANTIAS DE QUE A COMPARAÇÃO É JUSTA (cada uma tem teste):

  1. A lista de features é IMPORTADA do módulo do modelo atual, nunca copiada
     (`failure_classifier.ALL_FEATURES_V2`; na liquidez, a própria função
     `PaydayInference._featurize` e as janelas de `_janelas_do_cliente`).
  2. A mesma base (`data/v2`, com os sha256 conferidos contra o manifesto), a
     mesma função de separação por cliente e a mesma semente: os clientes de
     teste são exatamente os do treino de produção. No classificador é
     `split.split_por_cliente(grupos, 0.2, 42)`. Na liquidez o sorteio de
     produção é feito dentro de `PaydayInference.train`; aqui ele é refeito
     passo a passo, e o experimento PROVA que é o mesmo: o artefato de
     produção, avaliado nesses clientes, tem de devolver os números que o
     `payday_meta.json` gravou no treino.
  3. A preparação que a rede exige (escala dos números; categorias em colunas
     de 0 e 1) mora dentro de um `Pipeline` e é ajustada SÓ no treino.
  4. O limiar é escolhido numa partição de validação, pela mesma regra do
     modelo atual (`failure_classifier.escolher_limiar`), e reportado no teste.

A REGRA DE DECISÃO foi escrita antes de medir e está em `REGRA_DE_DECISAO`,
palavra por palavra como o Crai a deu. `recomendar_classificador` e
`recomendar_prophet` são essa regra em código, e têm teste.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.metrics import brier_score_loss, precision_recall_fscore_support, roc_auc_score
from sklearn.model_selection import GroupKFold
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from crai.ml import calibracao, visoes
from crai.ml import failure_classifier as fc
from crai.ml import payday_inference as pi
from crai.ml.split import conferir_sem_vazamento, split_por_cliente
from crai.scripts.gerar_bases import ARQUIVOS_V2, DADOS_V2_DIR, fonte_do_manifesto, ler_bases

# ── Onde fica cada coisa ──────────────────────────────────────────────────
APP_DIR = Path(__file__).resolve().parent.parent.parent
MODELOS_DE_PRODUCAO = APP_DIR / "models"
MODELOS_DO_EXPERIMENTO = MODELOS_DE_PRODUCAO / "experimentos_rn"
EVIDENCIA = APP_DIR.parent / "docs" / "evidencia_redes_neurais"
ARQUIVO_DE_RESULTADOS = "resultados.json"
ARQUIVO_LEIA = "LEIA.md"

# ── O que é igual ao treino de produção ───────────────────────────────────
SEMENTE = 42
FRACAO_DE_TESTE = 0.2
# A validação sai de DENTRO do treino (20% dos clientes de treino), com a mesma
# função e a mesma semente. O teste de produção não é tocado.
FRACAO_DE_VALIDACAO = 0.2

# Garantia 1: as MESMAS listas, por referência. Nenhum nome de coluna é escrito aqui.
FEATURES_DO_CLASSIFICADOR = fc.ALL_FEATURES_V2
NUMERICAS_DO_CLASSIFICADOR = fc.NUMERICAL_FEATURES
CATEGORICAS_DO_CLASSIFICADOR = fc.CATEGORICAL_FEATURES_V2
JANELA = pi.WINDOW
HORIZONTE = pi.HORIZON
PESO_LSTM_DE_PRODUCAO = pi.PESO_LSTM

# Os pesos do conjunto de produção. `FailureClassifier._evaluate` os escreve
# como literais; o teste `test_o_conjunto_reavaliado_e_o_de_producao` confere
# que a conta daqui devolve a mesma AUC que a de lá.
PESO_XGB, PESO_RF = 0.7, 0.3

N_DOBRAS = 5
SORTEIOS_DO_TETO_DE_BAYES = 400
# "Pouco histórico": quantos dias REAIS a janela de 30 dias tem. Os dias que
# faltam entram zerados (sem informação), para a LSTM e para a rede igualmente.
CORTES_DE_HISTORICO = (JANELA, 14, 7, 0)
CORTES_DE_POUCO_HISTORICO = (14, 7)
PESOS_DO_CONJUNTO_TESTADOS = (("conjunto_60_40", PESO_LSTM_DE_PRODUCAO), ("conjunto_80_20", 0.8),
                              ("conjunto_90_10", 0.9))

REGRA_DE_DECISAO = (
    "A rede só é recomendada para substituir o modelo atual se vencer por mais que o desvio "
    "da validação cruzada, sem piorar o recall no limiar nem a calibração.",
    "O Prophet só é recomendado para sair se a LSTM sozinha, ou a rede nova, for igual ou "
    "melhor no erro em dias e no acerto de ±1 dia, inclusive para o cliente com pouco "
    "histórico.",
    "Empate ou vantagem dentro do desvio: fica o modelo atual. Ele se explica pelo TreeSHAP, "
    "que é exato, e a rede precisaria de um método aproximado (pesa no Art. 20).",
)


def _dizer(texto: str) -> None:
    print(f"[EXPERIMENTO-RN] {texto}", flush=True)


def _r(valor, casas: int = 4):
    return None if valor is None else round(float(valor), casas)


# ══════════════════════════════════════════════════════════════════════════
# 5.1  O CLASSIFICADOR DE FALHA: REDE NEURAL DE CLASSIFICAÇÃO
# ══════════════════════════════════════════════════════════════════════════

def particoes_do_classificador(grupos) -> dict:
    """Os índices de cada partição, por cliente.

    `teste` e `treino_cheio` são exatamente o holdout do treino de produção
    (`split_por_cliente(grupos, 0.2, 42)`). `treino` e `validacao` dividem o
    `treino_cheio`, de novo por cliente, com a mesma função e a mesma semente:
    a validação serve só para escolher o limiar."""
    grupos = np.asarray(grupos)
    cheio, teste = split_por_cliente(grupos, test_size=FRACAO_DE_TESTE, seed=SEMENTE)
    conferir_sem_vazamento(grupos, cheio, teste)
    dentro_tr, dentro_va = split_por_cliente(grupos[cheio], test_size=FRACAO_DE_VALIDACAO,
                                             seed=SEMENTE)
    treino, validacao = cheio[dentro_tr], cheio[dentro_va]
    conferir_sem_vazamento(grupos, treino, validacao)
    return {"treino": treino, "validacao": validacao, "treino_cheio": cheio, "teste": teste}


def nova_rede_de_classificacao(semente: int = SEMENTE, rapido: bool = False) -> Pipeline:
    """A rede desafiante: uma MLP do scikit-learn dentro de um `Pipeline`.

    A preparação (escala dos números, categorias em colunas de 0 e 1) é um
    passo do pipeline: `fit` a ajusta só nas linhas que recebe, e quem chama
    só passa o treino (garantia 3). Categoria que não apareceu no treino vira
    uma linha de zeros, sem erro."""
    preparo = ColumnTransformer([
        ("numeros", StandardScaler(), list(NUMERICAS_DO_CLASSIFICADOR)),
        ("categorias", OneHotEncoder(handle_unknown="ignore", sparse_output=False),
         list(CATEGORICAS_DO_CLASSIFICADOR)),
    ])
    rede = MLPClassifier(hidden_layer_sizes=(64, 32), activation="relu", alpha=1e-4,
                         batch_size=256, learning_rate_init=1e-3,
                         max_iter=6 if rapido else 200, early_stopping=True,
                         validation_fraction=0.1, n_iter_no_change=10, random_state=semente)
    return Pipeline([("preparo", preparo), ("rede", rede)])


def matriz_do_modelo_atual(df: pd.DataFrame, modelo: "fc.FailureClassifier | None" = None):
    """`(modelo, X)` na codificação do modelo atual, pelo código DELE.

    Sem `modelo`, cria um `FailureClassifier` novo (os hiperparâmetros de
    produção vêm do construtor) e ajusta os codificadores na base passada,
    como o treino de produção faz. Com um modelo carregado, usa os
    codificadores do artefato."""
    modelo = modelo or fc.FailureClassifier()
    X, _ = modelo._prepare_features(df, list(FEATURES_DO_CLASSIFICADOR))
    return modelo, X


def proba_do_conjunto(modelo: "fc.FailureClassifier", X: np.ndarray) -> np.ndarray:
    """A probabilidade do conjunto de produção: 0,7 XGBoost + 0,3 Random Forest."""
    return (PESO_XGB * modelo.xgb.predict_proba(X)[:, 1]
            + PESO_RF * modelo.rf.predict_proba(X)[:, 1])


def treinar_modelo_atual(X: np.ndarray, y: np.ndarray) -> "fc.FailureClassifier":
    """O ALGORITMO atual (mesmas classes, mesmos hiperparâmetros), treinado de
    novo nas linhas passadas. Nada é gravado em disco."""
    modelo = fc.FailureClassifier()
    modelo.xgb.fit(X, y, verbose=False)
    modelo.rf.fit(X, y)
    return modelo


def carregar_modelo_de_producao() -> "fc.FailureClassifier":
    modelo = fc.FailureClassifier()
    if not modelo.load():
        raise SystemExit("[EXPERIMENTO-RN] O classificador de produção não está em models/. "
                         "Sem ele não há com o que comparar.")
    if list(modelo.feature_names) != list(FEATURES_DO_CLASSIFICADOR):
        raise SystemExit("[EXPERIMENTO-RN] O artefato de produção declara outra lista de "
                         f"features: {modelo.feature_names}")
    return modelo


def escolher_limiar_na_validacao(y_validacao, proba_validacao):
    """Garantia 4: a MESMA regra do modelo atual (o maior limiar da grade com
    recall >= 0,90), aplicada à partição de validação. `None` se nenhum limiar
    da grade alcança o recall mínimo."""
    varredura = fc.FailureClassifier._varrer_limiares(np.asarray(y_validacao),
                                                      np.asarray(proba_validacao))
    return fc.escolher_limiar(varredura)


def erro_de_calibracao(y, proba, faixas: int = 10) -> float:
    """ECE: a distância média entre a probabilidade prevista e a frequência
    observada, em faixas de igual largura, ponderada pelo tamanho da faixa."""
    y, proba = np.asarray(y, dtype=float), np.asarray(proba, dtype=float)
    cortes = np.linspace(0.0, 1.0, faixas + 1)
    faixa = np.clip(np.digitize(proba, cortes[1:-1]), 0, faixas - 1)
    total = 0.0
    for i in range(faixas):
        dentro = faixa == i
        if dentro.any():
            total += dentro.mean() * abs(proba[dentro].mean() - y[dentro].mean())
    return float(total)


def no_limiar(y, proba, limiar) -> dict:
    """Recall e precisão dos recuperáveis num limiar."""
    if limiar is None:
        return {"limiar": None, "recall": None, "precisao": None, "recuperaveis_perdidos": None}
    y = np.asarray(y)
    previsto = (np.asarray(proba) >= limiar).astype(int)
    precisao, recall, _, _ = precision_recall_fscore_support(y, previsto, average="binary",
                                                             zero_division=0)
    return {"limiar": float(limiar), "recall": _r(recall), "precisao": _r(precisao),
            "recuperaveis_perdidos": int(((y == 1) & (previsto == 0)).sum())}


def medir_classificador(y_teste, proba_teste, y_validacao, proba_validacao) -> dict:
    """As métricas de um modelo no holdout. O limiar vem da VALIDAÇÃO e é
    reportado no TESTE; o limiar em uso em produção (0,25) vai ao lado."""
    limiar = escolher_limiar_na_validacao(y_validacao, proba_validacao)
    return {
        "auc": _r(roc_auc_score(y_teste, proba_teste)),
        "brier": _r(brier_score_loss(y_teste, proba_teste)),
        "erro_de_calibracao": _r(erro_de_calibracao(y_teste, proba_teste)),
        "limiar_escolhido_na_validacao": limiar,
        "no_limiar_escolhido": no_limiar(y_teste, proba_teste, limiar),
        "no_limiar_em_uso": no_limiar(y_teste, proba_teste, fc.LIMIAR_CLASSIFICACAO),
    }


def teto_de_bayes(df_teste: pd.DataFrame, fonte: str,
                  sorteios: int = SORTEIOS_DO_TETO_DE_BAYES) -> float:
    """A AUC do melhor classificador possível no holdout, por construção.

    O rótulo da base é sorteado por `visoes._rotulo_recuperacao` a partir das
    features. Chamando a PRÓPRIA função do gerador muitas vezes sobre as linhas
    de teste, a média dos sorteios é a probabilidade verdadeira de cada linha
    (dadas as features), e a AUC dela contra o rótulo é o teto: nenhum modelo
    que só veja as features passa disso, a não ser por sorte."""
    P = calibracao.parametros(fonte)["classifier"]
    rng = np.random.default_rng(SEMENTE)
    soma = np.zeros(len(df_teste))
    for _ in range(sorteios):
        soma += visoes._rotulo_recuperacao(df_teste, rng, P)
    return float(roc_auc_score(df_teste["recovered"].to_numpy(), soma / sorteios))


def validacao_cruzada(df: pd.DataFrame, dobras: int = N_DOBRAS, rapido: bool = False) -> dict:
    """GroupKFold por cliente: o algoritmo atual e a rede, treinados e medidos
    nas MESMAS dobras. O desvio é o desvio-padrão amostral entre as dobras."""
    grupos = df["customer_id"].to_numpy()
    y = df["recovered"].to_numpy()
    _, X_atual = matriz_do_modelo_atual(df)
    entradas_da_rede = df[list(FEATURES_DO_CLASSIFICADOR)]
    auc_atual, auc_rede = [], []
    for n, (tr, te) in enumerate(GroupKFold(n_splits=dobras).split(X_atual, y, grupos), start=1):
        conferir_sem_vazamento(grupos, tr, te)
        atual = treinar_modelo_atual(X_atual[tr], y[tr])
        auc_atual.append(float(roc_auc_score(y[te], proba_do_conjunto(atual, X_atual[te]))))
        rede = nova_rede_de_classificacao(rapido=rapido).fit(entradas_da_rede.iloc[tr], y[tr])
        auc_rede.append(float(roc_auc_score(
            y[te], rede.predict_proba(entradas_da_rede.iloc[te])[:, 1])))
        _dizer(f"  dobra {n}/{dobras}: atual {auc_atual[-1]:.4f} | rede {auc_rede[-1]:.4f}")
    diferenca = np.array(auc_rede) - np.array(auc_atual)
    desvio = lambda v: float(np.std(v, ddof=1)) if len(v) > 1 else 0.0  # noqa: E731
    return {
        "dobras": dobras,
        "auc_atual_por_dobra": [_r(a) for a in auc_atual],
        "auc_rede_por_dobra": [_r(a) for a in auc_rede],
        "auc_atual_media": _r(np.mean(auc_atual)), "auc_atual_desvio": _r(desvio(auc_atual)),
        "auc_rede_media": _r(np.mean(auc_rede)), "auc_rede_desvio": _r(desvio(auc_rede)),
        "diferenca_media_rede_menos_atual": _r(diferenca.mean()),
        "diferenca_desvio": _r(desvio(diferenca)),
        # O "desvio da validação cruzada" da regra: o maior entre o desvio da
        # AUC de cada modelo e o da diferença. É a leitura mais exigente.
        "desvio_de_referencia": _r(max(desvio(auc_atual), desvio(auc_rede), desvio(diferenca))),
    }


def teste_de_permutacao(df: pd.DataFrame, partes: dict, rapido: bool = False) -> dict:
    """Com o rótulo do TREINO embaralhado, nenhum dos dois pode acertar o
    teste: a AUC tem de cair para perto de 0,5. É a prova de que o número
    medido vem do sinal, e não de vazamento."""
    rng = np.random.default_rng(SEMENTE)
    y = df["recovered"].to_numpy()
    tr, te = partes["treino_cheio"], partes["teste"]
    y_embaralhado = rng.permutation(y[tr])
    _, X_atual = matriz_do_modelo_atual(df)
    atual = treinar_modelo_atual(X_atual[tr], y_embaralhado)
    entradas = df[list(FEATURES_DO_CLASSIFICADOR)]
    rede = nova_rede_de_classificacao(rapido=rapido).fit(entradas.iloc[tr], y_embaralhado)
    return {"auc_atual_com_rotulo_embaralhado": _r(roc_auc_score(
                y[te], proba_do_conjunto(atual, X_atual[te]))),
            "auc_rede_com_rotulo_embaralhado": _r(roc_auc_score(
                y[te], rede.predict_proba(entradas.iloc[te])[:, 1]))}


def experimento_do_classificador(df: pd.DataFrame, fonte: str, rapido: bool = False,
                                 usar_producao: bool = True, gravar: bool = True) -> dict:
    """5.1 inteiro: o holdout (duas comparações simétricas), o GroupKFold, o
    teste de permutação e o teto de Bayes."""
    df = df.reset_index(drop=True)
    grupos = df["customer_id"].to_numpy()
    y = df["recovered"].to_numpy()
    partes = particoes_do_classificador(grupos)
    tr, va, cheio, te = (partes[k] for k in ("treino", "validacao", "treino_cheio", "teste"))
    entradas = df[list(FEATURES_DO_CLASSIFICADOR)]
    _dizer(f"classificador: {len(df)} cobranças | treino {len(tr)} | validação {len(va)} | "
           f"teste {len(te)} ({len(set(grupos[te]))} clientes)")

    # ── Comparação A: como em produção (os dois treinados no treino cheio) ──
    comparacao_a = {}
    if usar_producao:
        producao = carregar_modelo_de_producao()
        _, X_producao = matriz_do_modelo_atual(df.iloc[np.concatenate([va, te])], producao)
        p_va, p_te = (proba_do_conjunto(producao, X_producao[:len(va)]),
                      proba_do_conjunto(producao, X_producao[len(va):]))
        comparacao_a["modelo_de_producao"] = medir_classificador(y[te], p_te, y[va], p_va)
        comparacao_a["modelo_de_producao"]["auc_declarada_no_treino"] = producao.meta.get("auc")
        _dizer(f"  produção (artefato): AUC {comparacao_a['modelo_de_producao']['auc']}")
    rede_cheia = nova_rede_de_classificacao(rapido=rapido).fit(entradas.iloc[cheio], y[cheio])
    comparacao_a["rede"] = medir_classificador(
        y[te], rede_cheia.predict_proba(entradas.iloc[te])[:, 1],
        y[va], rede_cheia.predict_proba(entradas.iloc[va])[:, 1])
    _dizer(f"  rede (treino cheio): AUC {comparacao_a['rede']['auc']}")

    # ── Comparação B: limiar numa validação que NENHUM dos dois viu ─────────
    _, X_atual = matriz_do_modelo_atual(df)
    atual = treinar_modelo_atual(X_atual[tr], y[tr])
    rede = nova_rede_de_classificacao(rapido=rapido).fit(entradas.iloc[tr], y[tr])
    comparacao_b = {
        "modelo_atual_retreinado": medir_classificador(
            y[te], proba_do_conjunto(atual, X_atual[te]),
            y[va], proba_do_conjunto(atual, X_atual[va])),
        "rede": medir_classificador(
            y[te], rede.predict_proba(entradas.iloc[te])[:, 1],
            y[va], rede.predict_proba(entradas.iloc[va])[:, 1]),
    }
    _dizer(f"  atual retreinado: AUC {comparacao_b['modelo_atual_retreinado']['auc']} | "
           f"rede: AUC {comparacao_b['rede']['auc']}")

    _dizer("GroupKFold por cliente...")
    cruzada = validacao_cruzada(df, dobras=2 if rapido else N_DOBRAS, rapido=rapido)
    _dizer("teste de permutação do rótulo...")
    permutacao = teste_de_permutacao(df, partes, rapido=rapido)
    teto = teto_de_bayes(df.iloc[te], fonte, sorteios=20 if rapido else SORTEIOS_DO_TETO_DE_BAYES)

    if gravar:
        MODELOS_DO_EXPERIMENTO.mkdir(parents=True, exist_ok=True)
        joblib.dump(rede_cheia, MODELOS_DO_EXPERIMENTO / "rede_classificacao.joblib")
    arquitetura = rede_cheia.named_steps["rede"]
    resultado = {
        "features": list(FEATURES_DO_CLASSIFICADOR),
        "n_features": len(FEATURES_DO_CLASSIFICADOR),
        "particoes": {"n_linhas": int(len(df)), "n_treino_cheio": int(len(cheio)),
                      "n_treino": int(len(tr)), "n_validacao": int(len(va)),
                      "n_teste": int(len(te)), "n_clientes_teste": int(len(set(grupos[te]))),
                      "semente": SEMENTE, "fracao_de_teste": FRACAO_DE_TESTE,
                      "fracao_de_validacao_dentro_do_treino": FRACAO_DE_VALIDACAO},
        "rede": {"biblioteca": "scikit-learn MLPClassifier",
                 "camadas_ocultas": list(arquitetura.hidden_layer_sizes),
                 "epocas_treinadas": int(arquitetura.n_iter_),
                 "preparacao": "StandardScaler nos números e OneHotEncoder nas categorias, "
                               "dentro do Pipeline, ajustados só no treino",
                 "versao_em_pytorch": "não feita (a instrução pedia só se sobrasse tempo)"},
        "holdout_como_em_producao": comparacao_a,
        "holdout_com_validacao_nao_vista": comparacao_b,
        "validacao_cruzada": cruzada,
        "permutacao_do_rotulo": permutacao,
        "teto_de_bayes": {"no_holdout": _r(teto),
                          "sorteios": 20 if rapido else SORTEIOS_DO_TETO_DE_BAYES,
                          "da_base_inteira_declarado": 0.7249,
                          "fonte_do_declarado": "docs/RELATORIO_BASE_V2.md"},
    }
    resultado["recomendacao"] = recomendar_classificador(resultado)
    return resultado


def recomendar_classificador(resultado: dict) -> dict:
    """A regra de decisão do classificador, em código.

    A rede só é recomendada se (1) vence a validação cruzada por mais que o
    desvio, (2) não piora o recall no limiar e (3) não piora a calibração. As
    condições 2 e 3 têm de valer nas duas comparações do holdout."""
    cv = resultado["validacao_cruzada"]
    vantagem, desvio = cv["diferenca_media_rede_menos_atual"], cv["desvio_de_referencia"]
    vence = vantagem > desvio
    pares = []
    for chave, atual in (("holdout_como_em_producao", "modelo_de_producao"),
                         ("holdout_com_validacao_nao_vista", "modelo_atual_retreinado")):
        bloco = resultado[chave]
        if atual in bloco:
            pares.append((chave, bloco[atual], bloco["rede"]))

    def _recall(m):
        return m["no_limiar_escolhido"]["recall"]

    recall_ok = all(_recall(r) is not None and _recall(a) is not None and _recall(r) >= _recall(a)
                    for _, a, r in pares)
    calibracao_ok = all(r["brier"] <= a["brier"] for _, a, r in pares)
    recomenda = bool(vence and recall_ok and calibracao_ok)
    if recomenda:
        frase = ("A rede venceu por mais que o desvio da validação cruzada, sem piorar o recall "
                 "no limiar nem a calibração: pela regra, é recomendada para substituir.")
    elif not vence:
        frase = (f"A diferença de AUC na validação cruzada ({vantagem:+.4f}) não passa do desvio "
                 f"({desvio:.4f}): pela regra, fica o modelo atual.")
    else:
        faltou = [nome for nome, ok in (("o recall no limiar", recall_ok),
                                        ("a calibração", calibracao_ok)) if not ok]
        frase = ("A rede venceu a validação cruzada por mais que o desvio, mas piorou "
                 + " e ".join(faltou) + ": pela regra, fica o modelo atual.")
    return {"recomenda_trocar": recomenda, "vence_por_mais_que_o_desvio": bool(vence),
            "vantagem_de_auc": vantagem, "desvio": desvio,
            "sem_piorar_o_recall_no_limiar": bool(recall_ok),
            "sem_piorar_a_calibracao": bool(calibracao_ok), "frase": frase}


# ══════════════════════════════════════════════════════════════════════════
# 5.2  A LIQUIDEZ: REDE NEURAL DE REGRESSÃO
# ══════════════════════════════════════════════════════════════════════════

def clientes_de_treino_e_teste_da_liquidez(ids, fracao_de_teste: float = FRACAO_DE_TESTE,
                                           semente: int = SEMENTE) -> tuple:
    """O sorteio de clientes do treino de produção da liquidez, refeito.

    `PaydayInference.train` faz este sorteio por dentro (não é uma função que
    dê para chamar): ordena os ids, embaralha com `default_rng(seed)` e corta
    em `1 - test_size`. Os mesmos passos, na mesma ordem. A prova de que deu o
    mesmo resultado não é este comentário: é `reproducao_do_meta` no resultado
    do experimento (o artefato de produção, medido nestes clientes de teste,
    devolve os números gravados no treino)."""
    rng = np.random.default_rng(semente)
    clientes = np.sort(np.unique(np.asarray(ids)))
    rng.shuffle(clientes)
    corte = int(len(clientes) * (1 - fracao_de_teste))
    return clientes[:corte], clientes[corte:]


def alvo_em_dias(y: np.ndarray) -> np.ndarray:
    """Quantos dias faltam até o primeiro dia com saldo, por janela (0 = o
    primeiro dia do horizonte). `-1` quando o horizonte não tem dia com saldo:
    essas janelas não têm resposta e ficam fora, como na métrica de produção."""
    y = np.asarray(y)
    tem = y.max(axis=1) >= 1
    return np.where(tem, y.argmax(axis=1), -1).astype(int)


def mascarar_historico(X: np.ndarray, dias_conhecidos: int) -> np.ndarray:
    """A janela de um cliente com POUCO histórico: só os últimos
    `dias_conhecidos` dias são reais; os anteriores entram zerados."""
    if dias_conhecidos >= X.shape[1]:
        return X
    cortada = X.copy()
    cortada[:, :X.shape[1] - dias_conhecidos, :] = 0.0
    return cortada


def metricas_de_dias(real, previsto) -> dict:
    """Erro em dias, acerto exato e acerto de ±1 dia."""
    erro = np.abs(np.asarray(previsto, dtype=float) - np.asarray(real, dtype=float))
    if len(erro) == 0:
        return {"n_janelas": 0, "erro_em_dias": None, "acerto_exato": None, "acerto_1_dia": None}
    return {"n_janelas": int(len(erro)), "erro_em_dias": _r(erro.mean(), 3),
            "acerto_exato": _r((erro == 0).mean()), "acerto_1_dia": _r((erro <= 1).mean())}


def janelas_de_avaliacao(df_teste: pd.DataFrame, modelo: "pi.PaydayInference") -> dict:
    """As janelas de teste, montadas pelo código de produção (`_janelas_do_cliente`,
    passo 14, como em `_evaluate`), com o prior do Prophet de cada dia do horizonte."""
    datas = pd.DatetimeIndex(sorted(df_teste["date"].unique()))
    priors = {p: modelo._prior_por_data(p, datas) for p in pi.PROFILES}
    Xs, ys, perfis, clientes, prior, heuristica = [], [], [], [], [], []
    for cid, grupo in df_teste.groupby("customer_id", sort=True):
        grupo = grupo.sort_values("date").reset_index(drop=True)
        perfil = str(grupo["profile"].iloc[0])
        X, y, idx = modelo._janelas_do_cliente(grupo, passo=14)
        for j in range(len(X)):
            fatia = slice(idx[j], idx[j] + HORIZONTE)
            Xs.append(X[j])
            ys.append(y[j])
            perfis.append(perfil)
            clientes.append(cid)
            datas_do_horizonte = pd.DatetimeIndex(grupo["date"].iloc[fatia])
            prior.append(priors[perfil].loc[datas_do_horizonte].to_numpy())
            # O dia do mês sai da própria data: nenhum nome de coluna de feature é escrito aqui.
            heuristica.append(modelo._primeiro_dia_heuristica(
                datas_do_horizonte.day.to_numpy(), perfil))
    return {"X": np.stack(Xs), "y": np.stack(ys), "perfil": np.array(perfis),
            "cliente": np.array(clientes), "prior": np.stack(prior),
            "heuristica": np.array(heuristica)}


def _probabilidades_da_lstm(modelo: "pi.PaydayInference", X: np.ndarray) -> np.ndarray:
    torch = pi.torch
    saidas = []
    with torch.no_grad():
        for i in range(0, len(X), 4096):
            saidas.append(torch.sigmoid(modelo.model(torch.from_numpy(X[i:i + 4096]))).numpy())
    return np.concatenate(saidas)


def _primeiros_dias(probabilidades: np.ndarray) -> np.ndarray:
    """O dia escolhido em cada janela, pela regra de produção (`_primeiro_dia`)."""
    return np.array([pi.PaydayInference._primeiro_dia(p) for p in probabilidades])


def nova_rede_de_regressao(semente: int = SEMENTE, rapido: bool = False) -> Pipeline:
    """A rede desafiante da liquidez: recebe a MESMA janela (30 dias x 5
    features, achatada em 150 números) e devolve um número: quantos dias
    faltam até o cliente ter saldo. A escala é ajustada só no treino."""
    rede = MLPRegressor(hidden_layer_sizes=(128, 64), activation="relu", alpha=1e-4,
                        batch_size=256, learning_rate_init=1e-3, max_iter=5 if rapido else 80,
                        early_stopping=True, validation_fraction=0.1, n_iter_no_change=8,
                        random_state=semente)
    return Pipeline([("escala", StandardScaler()), ("rede", rede)])


def _prever_dias(rede: Pipeline, X: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(rede.predict(X.reshape(len(X), -1))), 0, HORIZONTE - 1).astype(int)


def experimento_da_liquidez(df: pd.DataFrame, rapido: bool = False, gravar: bool = True,
                            max_clientes: "int | None" = None) -> dict:
    """5.2 inteiro: o conjunto atual, a LSTM sozinha, os pesos 80/20 e 90/10, o
    Prophet só para cliente sem histórico e a rede de regressão, nos mesmos
    clientes de teste, por perfil e por tamanho de histórico."""
    df = df.reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    ids_treino, ids_teste = clientes_de_treino_e_teste_da_liquidez(df["customer_id"].to_numpy())
    todos_de_teste = len(ids_teste)
    if max_clientes:
        ids_treino, ids_teste = ids_treino[:max_clientes * 4], ids_teste[:max_clientes]
    assert not set(ids_treino) & set(ids_teste)

    producao = pi.PaydayInference()
    if not producao.load():
        raise SystemExit("[EXPERIMENTO-RN] O modelo de liquidez de produção não está em models/.")

    _dizer(f"liquidez: {len(ids_treino)} clientes de treino | {len(ids_teste)} de teste")
    teste = janelas_de_avaliacao(df[df["customer_id"].isin(set(ids_teste))], producao)
    real = alvo_em_dias(teste["y"])
    com_resposta = real >= 0

    # ── A rede de regressão: as mesmas janelas de treino da LSTM (passo 7) ──
    _dizer("montando as janelas de treino (as mesmas da LSTM)...")
    X_tr, y_tr = producao._montar_janelas(df[df["customer_id"].isin(set(ids_treino))], passo=7)
    alvo_tr = alvo_em_dias(y_tr)
    usar = alvo_tr >= 0
    _dizer(f"treinando a rede de regressão em {int(usar.sum())} janelas...")
    rede = nova_rede_de_regressao(rapido=rapido).fit(X_tr[usar].reshape(int(usar.sum()), -1),
                                                    alvo_tr[usar])
    if gravar:
        MODELOS_DO_EXPERIMENTO.mkdir(parents=True, exist_ok=True)
        joblib.dump(rede, MODELOS_DO_EXPERIMENTO / "rede_regressao_liquidez.joblib")

    # ── Cada modelo, em cada tamanho de histórico ───────────────────────────
    por_corte, auc_do_conjunto = {}, None
    for dias in CORTES_DE_HISTORICO:
        X = mascarar_historico(teste["X"], dias)
        p_lstm = _probabilidades_da_lstm(producao, X)
        previsoes = {"lstm_sozinha": _primeiros_dias(p_lstm),
                     "prophet_sozinho": _primeiros_dias(teste["prior"]),
                     "rede_de_regressao": _prever_dias(rede, X),
                     "heuristica_por_perfil": teste["heuristica"]}
        for nome, peso in PESOS_DO_CONJUNTO_TESTADOS:
            previsoes[nome] = _primeiros_dias(peso * p_lstm + (1 - peso) * teste["prior"])
        # A política "Prophet só para cliente sem histórico": com algum dia de
        # histórico, a LSTM sozinha; sem nenhum, o Prophet do perfil.
        previsoes["prophet_so_sem_historico"] = (previsoes["lstm_sozinha"] if dias > 0
                                                 else previsoes["prophet_sozinho"])
        if dias == JANELA:
            auc_do_conjunto = float(roc_auc_score(
                teste["y"].ravel(),
                (PESO_LSTM_DE_PRODUCAO * p_lstm + (1 - PESO_LSTM_DE_PRODUCAO) * teste["prior"]).ravel()))
        bloco = {}
        for nome, previsto in previsoes.items():
            bloco[nome] = {"todos": metricas_de_dias(real[com_resposta], previsto[com_resposta])}
            for perfil in pi.PROFILES:
                mascara = com_resposta & (teste["perfil"] == perfil)
                bloco[nome][perfil] = metricas_de_dias(real[mascara], previsto[mascara])
        por_corte["historico_completo" if dias == JANELA else f"historico_de_{dias}_dias"] = bloco

    completo = por_corte["historico_completo"]["conjunto_60_40"]["todos"]
    meta = producao.meta or {}
    reproducao = {
        "erro_em_dias_medido": completo["erro_em_dias"],
        "erro_em_dias_no_meta": meta.get("mae_dias_ensemble"),
        "auc_do_conjunto_medida": _r(auc_do_conjunto),
        "auc_do_conjunto_no_meta": meta.get("roc_auc_ensemble"),
        "n_clientes_de_treino": int(len(ids_treino)),
        "n_clientes_de_treino_no_meta": meta.get("n_clientes_treino"),
        "n_clientes_de_teste": int(len(ids_teste)),
        "n_clientes_de_teste_na_base": int(todos_de_teste),
    }
    reproducao["confere"] = bool(
        not max_clientes
        and reproducao["erro_em_dias_medido"] == reproducao["erro_em_dias_no_meta"]
        and reproducao["auc_do_conjunto_medida"] == reproducao["auc_do_conjunto_no_meta"]
        and reproducao["n_clientes_de_treino"] == reproducao["n_clientes_de_treino_no_meta"])
    arquitetura = rede.named_steps["rede"]
    resultado = {
        "janela_em_dias": JANELA, "horizonte_em_dias": HORIZONTE,
        "features_por_dia": int(teste["X"].shape[2]),
        "n_janelas_de_teste": int(len(real)), "n_janelas_com_resposta": int(com_resposta.sum()),
        "n_janelas_de_treino_da_rede": int(usar.sum()),
        "rede": {"biblioteca": "scikit-learn MLPRegressor",
                 "camadas_ocultas": list(arquitetura.hidden_layer_sizes),
                 "epocas_treinadas": int(arquitetura.n_iter_),
                 "entrada": f"a janela de {JANELA} dias x {int(teste['X'].shape[2])} features, "
                            "achatada; StandardScaler ajustado só no treino",
                 "saida": "dias até o primeiro dia com saldo (arredondado, de 0 a 13)"},
        "pouco_historico": {"definicao": "a janela só tem os últimos N dias reais; os outros "
                                         "entram zerados, para a LSTM e para a rede",
                            "cortes_em_dias": list(CORTES_DE_HISTORICO)},
        "reproducao_do_meta": reproducao,
        "por_historico": por_corte,
    }
    resultado["recomendacao"] = recomendar_prophet(resultado)
    return resultado


def recomendar_prophet(resultado: dict) -> dict:
    """A regra de decisão do Prophet, em código.

    O Prophet só é recomendado para sair se a LSTM sozinha, OU a rede nova, for
    igual ou melhor que o conjunto atual no erro em dias E no acerto de ±1 dia,
    no histórico completo E em cada corte de pouco histórico."""
    cortes = ["historico_completo"] + [f"historico_de_{d}_dias" for d in CORTES_DE_POUCO_HISTORICO]
    por = resultado["por_historico"]
    veredito = {}
    for candidato in ("lstm_sozinha", "rede_de_regressao"):
        linhas = []
        for corte in cortes:
            atual, novo = por[corte]["conjunto_60_40"]["todos"], por[corte][candidato]["todos"]
            linhas.append({"corte": corte,
                           "erro_igual_ou_melhor": novo["erro_em_dias"] <= atual["erro_em_dias"],
                           "acerto_1_dia_igual_ou_melhor":
                               novo["acerto_1_dia"] >= atual["acerto_1_dia"]})
        veredito[candidato] = {
            "igual_ou_melhor_em_tudo": all(l["erro_igual_ou_melhor"]
                                           and l["acerto_1_dia_igual_ou_melhor"] for l in linhas),
            "por_corte": linhas}
    sai = any(v["igual_ou_melhor_em_tudo"] for v in veredito.values())
    if sai:
        quem = [("a LSTM sozinha" if c == "lstm_sozinha" else "a rede de regressão")
                for c, v in veredito.items() if v["igual_ou_melhor_em_tudo"]]
        frase = ("Pela regra, o Prophet é recomendado para sair: " + " e ".join(quem)
                 + " ficou igual ou melhor no erro em dias e no acerto de ±1 dia, também com "
                   "pouco histórico.")
    else:
        frase = ("Pela regra, o Prophet fica: nem a LSTM sozinha nem a rede de regressão ficou "
                 "igual ou melhor que o conjunto atual em todos os cortes (histórico completo "
                 "e pouco histórico).")
    return {"recomenda_tirar_o_prophet": bool(sai), "candidatos": veredito, "frase": frase}


# ══════════════════════════════════════════════════════════════════════════
# O RESULTADO: O JSON E O LEIA.md
# ══════════════════════════════════════════════════════════════════════════

def _pct(valor) -> str:
    return "n/d" if valor is None else f"{valor * 100:.1f}%".replace(".", ",")


def _num(valor, casas: int = 4) -> str:
    return "n/d" if valor is None else f"{valor:.{casas}f}".replace(".", ",")


NOMES_DA_LIQUIDEZ = (
    ("conjunto_60_40", "Conjunto atual (60% LSTM, 40% Prophet)"),
    ("lstm_sozinha", "LSTM sozinha"),
    ("conjunto_80_20", "Conjunto 80% LSTM, 20% Prophet"),
    ("conjunto_90_10", "Conjunto 90% LSTM, 10% Prophet"),
    ("prophet_so_sem_historico", "Prophet só para cliente sem histórico"),
    ("rede_de_regressao", "Rede de regressão (nova)"),
    ("prophet_sozinho", "Prophet sozinho (referência)"),
    ("heuristica_por_perfil", "Regra fixa por perfil (referência)"),
)
NOMES_DOS_CORTES = {"historico_completo": "Histórico completo (30 dias)",
                    "historico_de_14_dias": "Pouco histórico: 14 dias",
                    "historico_de_7_dias": "Pouco histórico: 7 dias",
                    "historico_de_0_dias": "Sem histórico nenhum"}


def _linha_do_classificador(nome: str, m: dict) -> str:
    escolhido = m["no_limiar_escolhido"]
    limiar = "nenhum" if escolhido["limiar"] is None else _num(escolhido["limiar"], 2)
    return (f"| {nome} | {_num(m['auc'])} | {_num(m['brier'])} | {_num(m['erro_de_calibracao'])} | "
            f"{limiar} | {_pct(escolhido['recall'])} | {_pct(escolhido['precisao'])} | "
            f"{_pct(m['no_limiar_em_uso']['recall'])} | {_pct(m['no_limiar_em_uso']['precisao'])} |")


def gerar_leia(r: dict) -> str:
    """O `LEIA.md` da evidência, escrito a partir do `resultados.json`. O teste
    confere que o arquivo em disco é exatamente o que esta função produz."""
    c, l = r["classificador"], r["liquidez"]
    a, b, cv = c["holdout_como_em_producao"], c["holdout_com_validacao_nao_vista"], c["validacao_cruzada"]
    cabecalho = ("| Modelo | AUC | Brier | Erro de calibração | Limiar (validação) | Recall no "
                 "limiar | Precisão no limiar | Recall em 0,25 | Precisão em 0,25 |\n"
                 "|---|---|---|---|---|---|---|---|---|")
    linhas = [
        "# Experimento das redes neurais (Rodada 4, Fase 5)",
        "",
        f"Gerado por `python -m crai.scripts.experimento_redes_neurais` em {r['gerado_em']}"
        + (" (**execução rápida, com amostra pequena: os números não valem como resultado**)"
           if r["rapido"] else "") + ".",
        f"Base: `{r['base']['pasta']}` (sha256 conferidos contra o manifesto), semente "
        f"{r['base']['semente']}. Tempo total: {r['tempo_total_segundos']} segundos.",
        "",
        "**Nada foi promovido.** Os modelos de produção em `app/models/` não mudaram. As redes "
        "treinadas ficam em `app/models/experimentos_rn/`.",
        "",
        "## A regra de decisão, escrita antes de medir",
        "",
    ] + [f"- {regra}" for regra in REGRA_DE_DECISAO] + [
        "",
        "## 1. Classificador de falha: o modelo atual x a rede",
        "",
        f"As mesmas {c['n_features']} features (importadas do módulo do modelo atual), a mesma "
        f"separação por cliente e a mesma semente: {c['particoes']['n_teste']} cobranças de teste, "
        f"de {c['particoes']['n_clientes_teste']} clientes. A rede é uma MLP do scikit-learn com "
        f"camadas {c['rede']['camadas_ocultas']}.",
        "",
        "**Comparação A, como em produção** (os dois treinados em todo o treino; o limiar é "
        "escolhido na validação e medido no teste):",
        "",
        cabecalho,
    ]
    if "modelo_de_producao" in a:
        linhas.append(_linha_do_classificador("Modelo atual (artefato de produção)",
                                              a["modelo_de_producao"]))
    linhas += [
        _linha_do_classificador("Rede neural", a["rede"]),
        "",
        "**Comparação B, com o limiar numa validação que nenhum dos dois viu no treino:**",
        "",
        cabecalho,
        _linha_do_classificador("Modelo atual (mesmo algoritmo, retreinado)",
                                b["modelo_atual_retreinado"]),
        _linha_do_classificador("Rede neural", b["rede"]),
        "",
        "Brier e erro de calibração: quanto menor, melhor. O limiar é o maior da grade que "
        "mantém o recall em 90% ou mais na validação (a regra do modelo atual).",
        "",
        f"**Validação cruzada (GroupKFold de {cv['dobras']}, por cliente):**",
        "",
        "| Modelo | AUC média | Desvio | AUC em cada dobra |",
        "|---|---|---|---|",
        f"| Modelo atual | {_num(cv['auc_atual_media'])} | {_num(cv['auc_atual_desvio'])} | "
        f"{', '.join(_num(x) for x in cv['auc_atual_por_dobra'])} |",
        f"| Rede neural | {_num(cv['auc_rede_media'])} | {_num(cv['auc_rede_desvio'])} | "
        f"{', '.join(_num(x) for x in cv['auc_rede_por_dobra'])} |",
        "",
        f"Diferença média (rede menos atual): **{_num(cv['diferenca_media_rede_menos_atual'])}**. "
        f"Desvio de referência: **{_num(cv['desvio_de_referencia'])}** (o maior entre o desvio de "
        "cada modelo e o da diferença).",
        "",
        f"**Teto de Bayes no holdout:** {_num(c['teto_de_bayes']['no_holdout'])} (o melhor que "
        "qualquer modelo pode fazer com estas features, calculado com o próprio gerador da base). "
        f"O declarado para a base inteira é {_num(c['teto_de_bayes']['da_base_inteira_declarado'])}.",
        "",
        "**Teste de permutação do rótulo** (treino com o rótulo embaralhado; tem de dar perto de "
        f"0,5): modelo atual {_num(c['permutacao_do_rotulo']['auc_atual_com_rotulo_embaralhado'])}, "
        f"rede {_num(c['permutacao_do_rotulo']['auc_rede_com_rotulo_embaralhado'])}.",
        "",
        f"**Recomendação pela regra:** {c['recomendacao']['frase']}",
        "",
        "## 2. Liquidez: o conjunto atual x as alternativas",
        "",
        f"Os mesmos clientes de teste do treino de produção ({l['reproducao_do_meta']['n_clientes_de_teste']}"
        f" clientes, {l['n_janelas_com_resposta']} janelas com resposta), a mesma janela de "
        f"{l['janela_em_dias']} dias com {l['features_por_dia']} features por dia e o mesmo "
        f"horizonte de {l['horizonte_em_dias']} dias. A LSTM e o Prophet são os de produção, "
        "carregados de `app/models/`; a rede de regressão é nova e prevê quantos dias faltam até "
        "o cliente ter saldo.",
        "",
        "Prova de que os clientes de teste são os mesmos: o conjunto de produção, medido aqui, "
        f"deu erro de {_num(l['reproducao_do_meta']['erro_em_dias_medido'], 3)} dia e AUC "
        f"{_num(l['reproducao_do_meta']['auc_do_conjunto_medida'])}; o treino de produção gravou "
        f"{_num(l['reproducao_do_meta']['erro_em_dias_no_meta'], 3)} e "
        f"{_num(l['reproducao_do_meta']['auc_do_conjunto_no_meta'])}. "
        + ("**Confere.**" if l["reproducao_do_meta"]["confere"] else "**Não confere.**"),
        "",
        f"Pouco histórico: {l['pouco_historico']['definicao']}.",
    ]
    for corte, titulo in NOMES_DOS_CORTES.items():
        if corte not in l["por_historico"]:
            continue
        linhas += ["", f"**{titulo}**", "",
                   "| Modelo | Erro em dias | Acerto exato | Acerto de ±1 dia | CLT (±1 dia) | "
                   "PJ (±1 dia) | Freelancer (±1 dia) |",
                   "|---|---|---|---|---|---|---|"]
        for chave, nome in NOMES_DA_LIQUIDEZ:
            m = l["por_historico"][corte][chave]
            linhas.append(
                f"| {nome} | {_num(m['todos']['erro_em_dias'], 3)} | {_pct(m['todos']['acerto_exato'])} | "
                f"{_pct(m['todos']['acerto_1_dia'])} | {_pct(m['CLT']['acerto_1_dia'])} | "
                f"{_pct(m['PJ']['acerto_1_dia'])} | {_pct(m['freelancer']['acerto_1_dia'])} |")
    linhas += [
        "",
        f"**Recomendação pela regra:** {l['recomendacao']['frase']}",
        "",
        "## O que este experimento não é",
        "",
        "- A base é sintética: o que se mede é quem aprende melhor a regra que o próprio projeto "
        "escreveu, não o comportamento de clientes reais.",
        "- A rede de classificação foi treinada com uma configuração só, sem busca de "
        "hiperparâmetros. Uma busca poderia mover o número; a regra de decisão pede vantagem "
        "acima do desvio justamente para não trocar de modelo por ruído.",
        "- A rede de regressão recebe a janela achatada. Uma arquitetura recorrente nova, para "
        "regressão, não foi testada.",
        "- A versão em PyTorch da rede de classificação não foi feita.",
        "- O corte de pouco histórico zera os dias que faltam. Nenhum dos modelos foi treinado "
        "com janelas assim: o corte mede o que acontece hoje com um cliente novo.",
        "",
    ]
    return "\n".join(linhas)


def gravar_resultado(resultado: dict, pasta: Path = EVIDENCIA) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / ARQUIVO_DE_RESULTADOS).write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (pasta / ARQUIVO_LEIA).write_text(gerar_leia(resultado), encoding="utf-8")


def rodar(rapido: bool = False, gravar: bool = True, pasta_da_base: Path = DADOS_V2_DIR,
          pasta_da_evidencia: Path = EVIDENCIA) -> dict:
    """O experimento inteiro. Com `rapido`, uma amostra pequena e redes de
    poucas épocas: serve para conferir que tudo roda, não como resultado."""
    inicio = time.time()
    bases, manifesto = ler_bases(pasta_da_base, conferir_sha256=True, arquivos=ARQUIVOS_V2)
    fonte = fonte_do_manifesto(manifesto)
    classificador, liquidez = bases["classificador"], bases["liquidez"]
    if rapido:
        clientes = np.sort(classificador["customer_id"].unique())[:1500]
        classificador = classificador[classificador["customer_id"].isin(set(clientes))]
    resultado = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "rapido": bool(rapido),
        "nada_foi_promovido": True,
        "regra_de_decisao": list(REGRA_DE_DECISAO),
        "base": {"pasta": "app/data/v2", "semente": manifesto.get("semente"), "fonte": fonte,
                 "sha256_conferidos": True},
        "versoes": calibracao.versoes_bibliotecas(),
    }
    t = time.time()
    resultado["classificador"] = experimento_do_classificador(classificador, fonte, rapido=rapido,
                                                              gravar=gravar)
    resultado["classificador"]["tempo_segundos"] = round(time.time() - t, 1)
    t = time.time()
    resultado["liquidez"] = experimento_da_liquidez(liquidez, rapido=rapido, gravar=gravar,
                                                    max_clientes=60 if rapido else None)
    resultado["liquidez"]["tempo_segundos"] = round(time.time() - t, 1)
    resultado["tempo_total_segundos"] = round(time.time() - inicio, 1)
    if gravar:
        (MODELOS_DO_EXPERIMENTO / "meta.json").write_text(json.dumps({
            "o_que_e": "redes neurais desafiantes da Rodada 4, Fase 5; NÃO são modelos de produção",
            "gerado_em": resultado["gerado_em"], "rapido": bool(rapido),
            "arquivos": ["rede_classificacao.joblib", "rede_regressao_liquidez.joblib"],
            "resultados": "docs/evidencia_redes_neurais/resultados.json",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        gravar_resultado(resultado, pasta_da_evidencia)
    return resultado


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Experimento das redes neurais (não promove nada).")
    parser.add_argument("--rapido", action="store_true",
                        help="amostra pequena e poucas épocas; não vale como resultado")
    parser.add_argument("--sem-gravar", action="store_true",
                        help="não grava as redes nem a evidência")
    args = parser.parse_args(argv)
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(errors="replace")
    resultado = rodar(rapido=args.rapido, gravar=not args.sem_gravar)
    _dizer(f"classificador: {resultado['classificador']['recomendacao']['frase']}")
    _dizer(f"liquidez: {resultado['liquidez']['recomendacao']['frase']}")
    _dizer(f"tempo total: {resultado['tempo_total_segundos']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

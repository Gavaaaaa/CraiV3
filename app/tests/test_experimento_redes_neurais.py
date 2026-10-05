"""tests/test_experimento_redes_neurais.py - Rodada 4, Fase 5: o experimento das redes neurais.

O TREINO COMPLETO NAO RODA AQUI. Estes testes sao rapidos e conferem:

  as quatro garantias de que a comparacao e justa
    1. a lista de features e IMPORTADA do modulo do modelo atual, nunca copiada;
    2. a mesma base, a mesma funcao de separacao por cliente e a mesma semente:
       os clientes de teste sao os do treino de producao;
    3. a preparacao que a rede exige (escala; categorias em colunas de 0 e 1) e
       ajustada SO no treino;
    4. o limiar e escolhido na validacao e reportado no teste;

  a regra de decisao, em codigo, com resultados montados a mao;

  a forma do resultado: uma execucao rapida devolve as mesmas chaves que o
  `resultados.json` da evidencia, e o `LEIA.md` e exatamente o que o gerador
  produz a partir dele;

  e que nada foi promovido: o experimento so grava na pasta dele.
"""

import inspect
import json

import numpy as np
import pandas as pd
import pytest

from crai.ml import failure_classifier as fc
from crai.ml import payday_inference as pi
from crai.ml.split import split_por_cliente
from crai.scripts import experimento_redes_neurais as exp
from crai.scripts.gerar_bases import DADOS_V2_DIR

FONTE = inspect.getsource(exp)
RESULTADOS = exp.EVIDENCIA / exp.ARQUIVO_DE_RESULTADOS
LEIA = exp.EVIDENCIA / exp.ARQUIVO_LEIA
TEM_BASE = (DADOS_V2_DIR / "MANIFESTO.json").exists()
TEM_MODELOS = ((exp.MODELOS_DE_PRODUCAO / "train_metrics.json").exists()
               and (exp.MODELOS_DE_PRODUCAO / "payday_meta.json").exists())
precisa_da_base = pytest.mark.skipif(not TEM_BASE, reason="a base v2 nao esta em app/data/v2")
precisa_dos_modelos = pytest.mark.skipif(not (TEM_BASE and TEM_MODELOS),
                                         reason="sem a base v2 ou sem os modelos de producao")


def _base_pequena(n_clientes=120, por_cliente=5, semente=7) -> pd.DataFrame:
    """Uma base com as colunas do classificador v2, pequena e com sinal."""
    rng = np.random.default_rng(semente)
    n = n_clientes * por_cliente
    df = pd.DataFrame({
        "customer_id": np.repeat([f"C{i:04d}" for i in range(n_clientes)], por_cliente),
        "tenure_months": rng.integers(1, 60, n), "day_of_month": rng.integers(1, 29, n),
        "invoice_amount": rng.uniform(50, 5000, n).round(2),
        "avg_ticket": rng.uniform(50, 5000, n).round(2),
        "payment_history_score": rng.uniform(0, 1, n).round(3),
        "failure_count_90d": rng.integers(0, 4, n), "hour_of_day": rng.integers(0, 24, n),
        "day_of_week": rng.integers(0, 7, n), "attempt_count": rng.integers(1, 4, n),
        "gateway_error_code": rng.choice(["insufficient_funds", "limit_exceeded"], n),
        "metodo_pagamento": rng.choice(["pix_automatico", "boleto"], n),
    })
    p = 0.2 + 0.6 * df["payment_history_score"].to_numpy()
    df["recovered"] = (rng.random(n) < p).astype(int)
    df["ltv_estimated"] = df["invoice_amount"]
    return df


# == Garantia 1: as features sao importadas =================================

class TestAsFeaturesSaoImportadas:
    def test_a_lista_do_classificador_e_o_mesmo_objeto_do_modelo_atual(self):
        assert exp.FEATURES_DO_CLASSIFICADOR is fc.ALL_FEATURES_V2
        assert exp.NUMERICAS_DO_CLASSIFICADOR is fc.NUMERICAL_FEATURES
        assert exp.CATEGORICAS_DO_CLASSIFICADOR is fc.CATEGORICAL_FEATURES_V2
        assert len(exp.FEATURES_DO_CLASSIFICADOR) == 11

    def test_nenhum_nome_de_feature_esta_escrito_no_script(self):
        for nome in list(fc.ALL_FEATURES_V2) + ["balance_norm", "has_liquidity", "weekday"]:
            assert f'"{nome}"' not in FONTE and f"'{nome}'" not in FONTE, nome

    def test_a_rede_recebe_exatamente_essas_colunas(self):
        preparo = exp.nova_rede_de_classificacao().named_steps["preparo"]
        colunas = [c for _, _, cols in preparo.transformers for c in cols]
        assert sorted(colunas) == sorted(fc.ALL_FEATURES_V2)

    def test_a_liquidez_usa_a_janela_e_as_funcoes_do_modelo_atual(self):
        assert exp.JANELA is pi.WINDOW and exp.HORIZONTE is pi.HORIZON
        assert exp.PESO_LSTM_DE_PRODUCAO is pi.PESO_LSTM
        assert dict(exp.PESOS_DO_CONJUNTO_TESTADOS)["conjunto_60_40"] == pi.PESO_LSTM
        for usado in ("_janelas_do_cliente", "_montar_janelas", "_primeiro_dia",
                      "_prior_por_data"):
            assert f"{usado}(" in FONTE, usado
        assert "def _featurize" not in FONTE, "a featurizacao nao pode ser reescrita aqui"

    @precisa_dos_modelos
    def test_o_conjunto_reavaliado_e_o_de_producao(self):
        """A conta do conjunto feita aqui (0,7 e 0,3) da a mesma AUC que
        `FailureClassifier._evaluate` da para as mesmas linhas."""
        df = pd.read_parquet(DADOS_V2_DIR / "classificador.parquet").head(3000)
        modelo = exp.carregar_modelo_de_producao()
        _, X = exp.matriz_do_modelo_atual(df, modelo)
        y = df["recovered"].to_numpy()
        do_modelo = modelo._evaluate(X, y, df["ltv_estimated"].to_numpy())["auc"]
        from sklearn.metrics import roc_auc_score
        assert round(float(roc_auc_score(y, exp.proba_do_conjunto(modelo, X))), 4) == do_modelo


# == Garantia 2: a mesma separacao, a mesma semente =========================

class TestAMesmaSeparacao:
    def test_o_teste_e_o_holdout_de_producao(self):
        grupos = _base_pequena()["customer_id"].to_numpy()
        partes = exp.particoes_do_classificador(grupos)
        cheio, teste = split_por_cliente(grupos, test_size=0.2, seed=42)
        assert np.array_equal(partes["teste"], teste)
        assert np.array_equal(partes["treino_cheio"], cheio)
        assert (exp.SEMENTE, exp.FRACAO_DE_TESTE) == (42, 0.2)

    def test_nenhum_cliente_em_dois_lados_e_nada_fica_de_fora(self):
        grupos = _base_pequena()["customer_id"].to_numpy()
        p = exp.particoes_do_classificador(grupos)
        de = lambda nome: set(grupos[p[nome]])  # noqa: E731
        assert not de("treino") & de("validacao")
        assert not de("treino") & de("teste") and not de("validacao") & de("teste")
        assert sorted(np.concatenate([p["treino"], p["validacao"]])) == sorted(p["treino_cheio"])
        assert sorted(np.concatenate([p["treino_cheio"], p["teste"]])) == list(range(len(grupos)))

    @precisa_dos_modelos
    def test_na_base_de_verdade_o_teste_tem_o_tamanho_que_o_treino_gravou(self):
        grupos = pd.read_parquet(DADOS_V2_DIR / "classificador.parquet",
                                 columns=["customer_id"])["customer_id"].to_numpy()
        partes = exp.particoes_do_classificador(grupos)
        gravado = json.loads((exp.MODELOS_DE_PRODUCAO / "train_metrics.json").read_text("utf-8"))
        assert len(partes["teste"]) == gravado["n_teste"]
        assert len(partes["treino_cheio"]) == gravado["n_treino"]
        assert len(set(grupos[partes["teste"]])) == gravado["n_clientes_teste"]

    def test_o_sorteio_da_liquidez_e_o_de_producao_passo_a_passo(self):
        ids = np.array([f"CLI_{i:03d}" for i in range(50)] * 3)
        treino, teste = exp.clientes_de_treino_e_teste_da_liquidez(ids)
        # Os passos de PaydayInference.train: ordenar, embaralhar com a semente, cortar em 80%.
        rng = np.random.default_rng(42)
        esperado = np.sort(np.unique(ids))
        rng.shuffle(esperado)
        assert list(treino) == list(esperado[:40]) and list(teste) == list(esperado[40:])
        assert not set(treino) & set(teste) and len(set(treino) | set(teste)) == 50
        de_novo = exp.clientes_de_treino_e_teste_da_liquidez(ids[::-1])
        assert list(de_novo[1]) == list(teste), "nao depende da ordem das linhas"

    @precisa_dos_modelos
    def test_na_base_de_verdade_o_treino_da_liquidez_tem_o_tamanho_do_meta(self):
        ids = pd.read_parquet(DADOS_V2_DIR / "liquidez.parquet",
                              columns=["customer_id"])["customer_id"].to_numpy()
        treino, teste = exp.clientes_de_treino_e_teste_da_liquidez(ids)
        meta = json.loads((exp.MODELOS_DE_PRODUCAO / "payday_meta.json").read_text("utf-8"))
        assert len(treino) == meta["n_clientes_treino"]
        assert len(treino) + len(teste) == meta["n_amostras"]


# == Garantia 3: a preparacao e ajustada so no treino =======================

class TestAPreparacaoSoNoTreino:
    def test_a_escala_e_a_media_do_treino_e_nao_a_da_base_inteira(self):
        df = _base_pequena()
        p = exp.particoes_do_classificador(df["customer_id"].to_numpy())
        entradas = df[list(exp.FEATURES_DO_CLASSIFICADOR)]
        rede = exp.nova_rede_de_classificacao(rapido=True).fit(
            entradas.iloc[p["treino"]], df["recovered"].to_numpy()[p["treino"]])
        escala = rede.named_steps["preparo"].named_transformers_["numeros"]
        numeros = list(exp.NUMERICAS_DO_CLASSIFICADOR)
        do_treino = entradas.iloc[p["treino"]][numeros].to_numpy(dtype=float)
        assert np.allclose(escala.mean_, do_treino.mean(axis=0))
        assert np.allclose(escala.scale_, do_treino.std(axis=0))
        assert not np.allclose(escala.mean_, entradas[numeros].to_numpy(dtype=float).mean(axis=0))
        assert escala.n_samples_seen_ == len(p["treino"])

    def test_categoria_que_so_existe_no_teste_nao_entra_no_codificador(self):
        df = _base_pequena()
        p = exp.particoes_do_classificador(df["customer_id"].to_numpy())
        categoria = exp.CATEGORICAS_DO_CLASSIFICADOR[0]
        df.loc[p["teste"], categoria] = "so_no_teste"
        entradas = df[list(exp.FEATURES_DO_CLASSIFICADOR)]
        rede = exp.nova_rede_de_classificacao(rapido=True).fit(
            entradas.iloc[p["treino"]], df["recovered"].to_numpy()[p["treino"]])
        codificador = rede.named_steps["preparo"].named_transformers_["categorias"]
        assert "so_no_teste" not in codificador.categories_[0]
        # E prever o teste nao levanta: a categoria nova vira zeros.
        proba = rede.predict_proba(entradas.iloc[p["teste"]])[:, 1]
        assert len(proba) == len(p["teste"]) and np.all((proba >= 0) & (proba <= 1))

    def test_a_escala_da_rede_de_regressao_tambem_e_so_do_treino(self):
        rng = np.random.default_rng(1)
        X_treino, X_teste = rng.normal(0, 1, (300, 150)), rng.normal(50, 1, (100, 150))
        rede = exp.nova_rede_de_regressao(rapido=True).fit(X_treino, rng.integers(0, 14, 300))
        escala = rede.named_steps["escala"]
        assert np.allclose(escala.mean_, X_treino.mean(axis=0))
        assert escala.n_samples_seen_ == 300
        assert abs(escala.mean_.mean() - np.vstack([X_treino, X_teste]).mean()) > 5

    def test_o_script_nunca_ajusta_no_teste(self):
        """Nenhum `fit` do script recebe os indices de teste."""
        for linha in FONTE.splitlines():
            if ".fit(" in linha:
                assert "[te]" not in linha and "iloc[te]" not in linha, linha


# == Garantia 4: o limiar vem da validacao ==================================

class TestOLimiarVemDaValidacao:
    def test_e_a_regra_do_modelo_atual_aplicada_a_validacao(self):
        rng = np.random.default_rng(3)
        y = rng.integers(0, 2, 2000)
        proba = np.clip(0.35 + 0.2 * y + rng.normal(0, 0.15, 2000), 0, 1)
        esperado = fc.escolher_limiar(fc.FailureClassifier._varrer_limiares(y, proba))
        assert exp.escolher_limiar_na_validacao(y, proba) == esperado
        assert esperado in fc.LIMIARES_REPORTADOS

    def test_o_limiar_reportado_e_o_da_validacao_mesmo_que_o_teste_escolhesse_outro(self):
        # Validacao: todo recuperavel tem probabilidade alta -> o maior limiar da grade passa.
        y_va = np.array([1] * 100 + [0] * 100)
        p_va = np.array([0.9] * 100 + [0.1] * 100)
        # Teste: os recuperaveis tem probabilidade 0,22 -> so um limiar baixo manteria o recall.
        y_te = np.array([1] * 100 + [0] * 100)
        p_te = np.array([0.22] * 100 + [0.1] * 100)
        m = exp.medir_classificador(y_te, p_te, y_va, p_va)
        assert m["limiar_escolhido_na_validacao"] == max(fc.LIMIARES_REPORTADOS) == 0.5
        assert exp.escolher_limiar_na_validacao(y_te, p_te) == 0.2, "o teste escolheria outro"
        assert m["no_limiar_escolhido"] == {"limiar": 0.5, "recall": 0.0, "precisao": 0.0,
                                            "recuperaveis_perdidos": 100}
        assert m["no_limiar_em_uso"]["limiar"] == fc.LIMIAR_CLASSIFICACAO
        assert m["no_limiar_em_uso"]["recall"] == 0.0

    def test_sem_limiar_que_sustente_o_recall_o_resultado_diz_nenhum(self):
        y = np.array([1] * 50 + [0] * 50)
        p = np.array([0.05] * 50 + [0.04] * 50)
        m = exp.medir_classificador(y, p, y, p)
        assert m["limiar_escolhido_na_validacao"] is None
        assert m["no_limiar_escolhido"]["recall"] is None

    def test_as_medidas_de_calibracao(self):
        y = np.array([1, 0, 1, 0] * 250)
        perfeito = np.array([0.5] * 1000)
        assert exp.erro_de_calibracao(y, perfeito) == pytest.approx(0.0)
        assert exp.erro_de_calibracao(y, np.array([0.9] * 1000)) == pytest.approx(0.4)


# == A liquidez: o alvo, o pouco historico e as metricas ====================

class TestAsPecasDaLiquidez:
    def test_o_alvo_e_o_primeiro_dia_com_saldo(self):
        y = np.zeros((3, exp.HORIZONTE))
        y[0, 4] = y[0, 9] = 1
        y[1, 0] = 1
        assert list(exp.alvo_em_dias(y)) == [4, 0, -1]

    def test_pouco_historico_zera_os_dias_que_faltam_e_so_eles(self):
        X = np.ones((2, exp.JANELA, 5), dtype=np.float32)
        com_7 = exp.mascarar_historico(X, 7)
        assert com_7[:, :exp.JANELA - 7].sum() == 0 and com_7[:, exp.JANELA - 7:].min() == 1
        assert exp.mascarar_historico(X, 0).sum() == 0
        assert exp.mascarar_historico(X, exp.JANELA) is X
        assert X.min() == 1, "a janela original nao e alterada"

    def test_as_metricas_em_dias(self):
        m = exp.metricas_de_dias([0, 3, 5, 10], [0, 4, 8, 10])
        assert m == {"n_janelas": 4, "erro_em_dias": 1.0, "acerto_exato": 0.5, "acerto_1_dia": 0.75}
        assert exp.metricas_de_dias([], [])["erro_em_dias"] is None

    def test_os_cortes_e_os_modelos_pedidos_estao_todos_la(self):
        assert exp.CORTES_DE_HISTORICO[0] == exp.JANELA and 0 in exp.CORTES_DE_HISTORICO
        assert set(exp.CORTES_DE_POUCO_HISTORICO) < set(exp.CORTES_DE_HISTORICO)
        assert [p for _, p in exp.PESOS_DO_CONJUNTO_TESTADOS] == [0.6, 0.8, 0.9]
        nomes = {chave for chave, _ in exp.NOMES_DA_LIQUIDEZ}
        assert {"conjunto_60_40", "lstm_sozinha", "conjunto_80_20", "conjunto_90_10",
                "prophet_so_sem_historico", "rede_de_regressao"} <= nomes


# == A regra de decisao, em codigo ==========================================

def _medida(auc=0.70, brier=0.22, recall=0.93):
    return {"auc": auc, "brier": brier, "erro_de_calibracao": 0.02,
            "limiar_escolhido_na_validacao": 0.25,
            "no_limiar_escolhido": {"limiar": 0.25, "recall": recall, "precisao": 0.46,
                                    "recuperaveis_perdidos": 0},
            "no_limiar_em_uso": {"limiar": 0.25, "recall": recall, "precisao": 0.46,
                                 "recuperaveis_perdidos": 0}}


def _classificador(vantagem, desvio, rede=None, atual=None):
    rede, atual = rede or _medida(), atual or _medida()
    return {"validacao_cruzada": {"diferenca_media_rede_menos_atual": vantagem,
                                  "desvio_de_referencia": desvio},
            "holdout_como_em_producao": {"modelo_de_producao": atual, "rede": rede},
            "holdout_com_validacao_nao_vista": {"modelo_atual_retreinado": atual, "rede": rede}}


class TestARegraDoClassificador:
    def test_a_regra_esta_escrita_palavra_por_palavra(self):
        assert exp.REGRA_DE_DECISAO[0].startswith("A rede só é recomendada para substituir")
        assert "por mais que o desvio da validação cruzada" in exp.REGRA_DE_DECISAO[0]
        assert "inclusive para o cliente com pouco histórico" in exp.REGRA_DE_DECISAO[1]
        assert exp.REGRA_DE_DECISAO[2].startswith("Empate ou vantagem dentro do desvio: fica o")

    def test_vantagem_dentro_do_desvio_fica_o_atual(self):
        r = exp.recomendar_classificador(_classificador(0.004, 0.006))
        assert r["recomenda_trocar"] is False and r["vence_por_mais_que_o_desvio"] is False
        assert "fica o modelo atual" in r["frase"]

    def test_empate_e_derrota_ficam_com_o_atual(self):
        assert exp.recomendar_classificador(_classificador(0.0, 0.0))["recomenda_trocar"] is False
        assert exp.recomendar_classificador(_classificador(0.006, 0.006))["recomenda_trocar"] is False
        assert exp.recomendar_classificador(_classificador(-0.02, 0.005))["recomenda_trocar"] is False

    def test_vence_por_mais_que_o_desvio_sem_piorar_nada_e_recomendada(self):
        r = exp.recomendar_classificador(_classificador(
            0.02, 0.005, rede=_medida(brier=0.21, recall=0.94)))
        assert r["recomenda_trocar"] is True
        assert "é recomendada para substituir" in r["frase"]

    def test_vence_mas_piora_o_recall_no_limiar_nao_e_recomendada(self):
        r = exp.recomendar_classificador(_classificador(0.02, 0.005, rede=_medida(recall=0.91)))
        assert r["recomenda_trocar"] is False and r["sem_piorar_o_recall_no_limiar"] is False
        assert "piorou o recall no limiar" in r["frase"]

    def test_vence_mas_piora_a_calibracao_nao_e_recomendada(self):
        r = exp.recomendar_classificador(_classificador(0.02, 0.005, rede=_medida(brier=0.23)))
        assert r["recomenda_trocar"] is False and r["sem_piorar_a_calibracao"] is False
        assert "piorou a calibração" in r["frase"]

    def test_rede_sem_limiar_que_sustente_o_recall_nao_e_recomendada(self):
        rede = _medida()
        rede["no_limiar_escolhido"] = {"limiar": None, "recall": None, "precisao": None,
                                       "recuperaveis_perdidos": None}
        assert exp.recomendar_classificador(
            _classificador(0.02, 0.005, rede=rede))["recomenda_trocar"] is False


def _liquidez(**candidatos):
    """`candidatos[nome] = {corte: (erro, acerto_1_dia)}`; o conjunto e (0.60, 0.90) em tudo."""
    cortes = ["historico_completo", "historico_de_14_dias", "historico_de_7_dias"]
    por = {}
    for corte in cortes:
        por[corte] = {"conjunto_60_40": {"todos": {"erro_em_dias": 0.60, "acerto_1_dia": 0.90}}}
        for nome in ("lstm_sozinha", "rede_de_regressao"):
            erro, acerto = candidatos.get(nome, {}).get(corte, (0.70, 0.85))
            por[corte][nome] = {"todos": {"erro_em_dias": erro, "acerto_1_dia": acerto}}
    return {"por_historico": por}


class TestARegraDoProphet:
    def test_ninguem_igual_ou_melhor_o_prophet_fica(self):
        r = exp.recomendar_prophet(_liquidez())
        assert r["recomenda_tirar_o_prophet"] is False and "o Prophet fica" in r["frase"]

    def test_lstm_igual_em_tudo_o_prophet_pode_sair(self):
        iguais = {c: (0.60, 0.90) for c in ("historico_completo", "historico_de_14_dias",
                                            "historico_de_7_dias")}
        r = exp.recomendar_prophet(_liquidez(lstm_sozinha=iguais))
        assert r["recomenda_tirar_o_prophet"] is True and "a LSTM sozinha" in r["frase"]

    def test_melhor_no_completo_e_pior_com_pouco_historico_o_prophet_fica(self):
        r = exp.recomendar_prophet(_liquidez(lstm_sozinha={
            "historico_completo": (0.50, 0.93), "historico_de_14_dias": (0.55, 0.91),
            "historico_de_7_dias": (0.65, 0.91)}))
        assert r["recomenda_tirar_o_prophet"] is False
        pior = [l for l in r["candidatos"]["lstm_sozinha"]["por_corte"]
                if not l["erro_igual_ou_melhor"]]
        assert [l["corte"] for l in pior] == ["historico_de_7_dias"]

    def test_melhor_no_erro_e_pior_no_acerto_nao_basta(self):
        r = exp.recomendar_prophet(_liquidez(rede_de_regressao={
            c: (0.50, 0.89) for c in ("historico_completo", "historico_de_14_dias",
                                      "historico_de_7_dias")}))
        assert r["recomenda_tirar_o_prophet"] is False

    def test_a_rede_nova_tambem_pode_tirar_o_prophet(self):
        r = exp.recomendar_prophet(_liquidez(rede_de_regressao={
            c: (0.55, 0.92) for c in ("historico_completo", "historico_de_14_dias",
                                      "historico_de_7_dias")}))
        assert r["recomenda_tirar_o_prophet"] is True and "a rede de regressão" in r["frase"]


# == A forma do resultado ===================================================

def _forma(valor, caminho=""):
    """As chaves de um resultado, sem os valores: o esqueleto que tem de ser igual."""
    if isinstance(valor, dict):
        return {k: _forma(v, f"{caminho}.{k}") for k, v in sorted(valor.items())}
    if isinstance(valor, list):
        return "lista"
    return "valor"


@pytest.fixture(scope="module")
def rapido(tmp_path_factory):
    """Uma execucao rapida de verdade, sem gravar nada no repositorio."""
    if not (TEM_BASE and TEM_MODELOS):
        pytest.skip("sem a base v2 ou sem os modelos de producao")
    return exp.rodar(rapido=True, gravar=False)


class TestAFormaDoResultado:
    def test_a_execucao_rapida_traz_os_dois_experimentos_e_a_regra(self, rapido):
        assert rapido["rapido"] is True and rapido["nada_foi_promovido"] is True
        assert rapido["regra_de_decisao"] == list(exp.REGRA_DE_DECISAO)
        assert rapido["classificador"]["features"] == list(fc.ALL_FEATURES_V2)
        c = rapido["classificador"]
        for bloco, atual in (("holdout_como_em_producao", "modelo_de_producao"),
                             ("holdout_com_validacao_nao_vista", "modelo_atual_retreinado")):
            for modelo in (atual, "rede"):
                m = c[bloco][modelo]
                assert 0.0 <= m["auc"] <= 1.0 and 0.0 <= m["brier"] <= 1.0
                assert set(m["no_limiar_escolhido"]) == {"limiar", "recall", "precisao",
                                                         "recuperaveis_perdidos"}
        assert len(c["validacao_cruzada"]["auc_rede_por_dobra"]) == c["validacao_cruzada"]["dobras"]
        assert isinstance(c["recomendacao"]["recomenda_trocar"], bool)

    def test_a_liquidez_traz_cada_modelo_em_cada_corte_e_por_perfil(self, rapido):
        l = rapido["liquidez"]
        assert set(l["por_historico"]) == set(exp.NOMES_DOS_CORTES)
        for corte, bloco in l["por_historico"].items():
            assert set(bloco) == {chave for chave, _ in exp.NOMES_DA_LIQUIDEZ}, corte
            for nome, m in bloco.items():
                assert set(m) == {"todos", *pi.PROFILES}, (corte, nome)
                assert m["todos"]["n_janelas"] == l["n_janelas_com_resposta"]
        completo, sem = l["por_historico"]["historico_completo"], l["por_historico"]["historico_de_0_dias"]
        # A politica "Prophet so para cliente sem historico": LSTM com historico, Prophet sem.
        assert completo["prophet_so_sem_historico"] == completo["lstm_sozinha"]
        assert sem["prophet_so_sem_historico"] == sem["prophet_sozinho"]
        # O Prophet nao usa o historico: da o mesmo em todos os cortes.
        assert sem["prophet_sozinho"] == completo["prophet_sozinho"]
        assert isinstance(l["recomendacao"]["recomenda_tirar_o_prophet"], bool)

    def test_o_leia_sai_do_resultado_e_traz_a_regra_e_as_duas_recomendacoes(self, rapido):
        texto = exp.gerar_leia(rapido)
        for regra in exp.REGRA_DE_DECISAO:
            assert regra in texto
        assert rapido["classificador"]["recomendacao"]["frase"] in texto
        assert rapido["liquidez"]["recomendacao"]["frase"] in texto
        assert "Nada foi promovido" in texto and "execução rápida" in texto
        for _, nome in exp.NOMES_DA_LIQUIDEZ:
            assert nome in texto


class TestAEvidenciaGravada:
    """O `resultados.json` e o `LEIA.md` do treino completo, em `docs/evidencia_redes_neurais/`."""

    def test_o_resultado_gravado_e_do_treino_completo(self):
        r = json.loads(RESULTADOS.read_text(encoding="utf-8"))
        assert r["rapido"] is False and r["nada_foi_promovido"] is True
        assert r["regra_de_decisao"] == list(exp.REGRA_DE_DECISAO)
        assert r["classificador"]["features"] == list(fc.ALL_FEATURES_V2)
        assert r["classificador"]["validacao_cruzada"]["dobras"] == exp.N_DOBRAS == 5
        assert r["classificador"]["teto_de_bayes"]["sorteios"] == exp.SORTEIOS_DO_TETO_DE_BAYES

    def test_o_gravado_tem_a_mesma_forma_da_execucao_rapida(self, rapido):
        gravado = json.loads(RESULTADOS.read_text(encoding="utf-8"))
        assert _forma(gravado) == _forma(rapido)

    def test_os_clientes_de_teste_sao_os_de_producao(self):
        """A prova da garantia 2, medida no treino completo."""
        r = json.loads(RESULTADOS.read_text(encoding="utf-8"))
        prova = r["liquidez"]["reproducao_do_meta"]
        assert prova["confere"] is True
        assert prova["erro_em_dias_medido"] == prova["erro_em_dias_no_meta"]
        assert prova["auc_do_conjunto_medida"] == prova["auc_do_conjunto_no_meta"]
        producao = r["classificador"]["holdout_como_em_producao"]["modelo_de_producao"]
        assert producao["auc"] == producao["auc_declarada_no_treino"]

    def test_a_permutacao_do_rotulo_derruba_os_dois_modelos(self):
        r = json.loads(RESULTADOS.read_text(encoding="utf-8"))
        for chave, auc in r["classificador"]["permutacao_do_rotulo"].items():
            assert 0.45 <= auc <= 0.55, (chave, auc)

    def test_ninguem_passa_do_teto_de_bayes_por_mais_que_o_ruido(self):
        r = json.loads(RESULTADOS.read_text(encoding="utf-8"))
        c = r["classificador"]
        teto = c["teto_de_bayes"]["no_holdout"]
        for bloco in ("holdout_como_em_producao", "holdout_com_validacao_nao_vista"):
            for modelo, m in c[bloco].items():
                assert m["auc"] <= teto + 0.01, (bloco, modelo, m["auc"], teto)

    def test_a_recomendacao_gravada_e_a_que_a_regra_da_para_os_numeros_gravados(self):
        r = json.loads(RESULTADOS.read_text(encoding="utf-8"))
        assert exp.recomendar_classificador(r["classificador"]) == r["classificador"]["recomendacao"]
        assert exp.recomendar_prophet(r["liquidez"]) == r["liquidez"]["recomendacao"]

    def test_o_leia_e_exatamente_o_que_o_gerador_produz(self):
        r = json.loads(RESULTADOS.read_text(encoding="utf-8"))
        assert LEIA.read_text(encoding="utf-8") == exp.gerar_leia(r)


# == Nada e promovido =======================================================

class TestNadaEPromovido:
    def test_o_experimento_so_grava_na_pasta_dele(self):
        assert exp.MODELOS_DO_EXPERIMENTO == exp.MODELOS_DE_PRODUCAO / "experimentos_rn"
        assert exp.MODELOS_DO_EXPERIMENTO.parent.name == "models"
        gravacoes = [l.strip() for l in FONTE.splitlines()
                     if "joblib.dump(" in l or ".write_text(" in l]
        assert len(gravacoes) == 5
        for linha in gravacoes:
            assert ("MODELOS_DO_EXPERIMENTO" in linha or "pasta /" in linha), linha

    def test_o_script_nao_chama_o_treino_nem_a_gravacao_dos_modelos_de_producao(self):
        for proibido in ("_save_models", ".train(", "apontar_models_dir", "promover",
                         "shutil", "os.replace", "os.rename"):
            assert proibido not in FONTE, proibido

    @precisa_dos_modelos
    def test_rodar_o_experimento_nao_muda_nenhum_artefato_de_producao(self):
        """O tamanho e a data de cada arquivo de `models/` sao os mesmos antes e
        depois de rodar o experimento do classificador e o da liquidez."""
        def retrato():
            return {a.name: (a.stat().st_size, a.stat().st_mtime_ns)
                    for a in exp.MODELOS_DE_PRODUCAO.iterdir()
                    if a.is_file() and a.name != "bandit_state.json"}
        antes = retrato()
        assert "xgb_failure_classifier.joblib" in antes and "payday_lstm.pt" in antes
        base = pd.read_parquet(DADOS_V2_DIR / "classificador.parquet").head(2500)
        exp.experimento_do_classificador(base, "sintetico_calibrado", rapido=True, gravar=False)
        liquidez = pd.read_parquet(DADOS_V2_DIR / "liquidez.parquet")
        exp.experimento_da_liquidez(liquidez, rapido=True, gravar=False, max_clientes=20)
        assert retrato() == antes

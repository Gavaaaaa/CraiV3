"""tests/test_classificador_v4.py — a base v4 do classificador de falha e o treino dela.

Experimento de 08/10/2026 (desenho em docs/evidencia_v4/DESENHO.md). A base v4
de verdade (`app/data/v4/`) é gerada só pelo comando que o operador roda; aqui toda
escrita vai para `tmp_path`, com uma AMOSTRA de clientes, para a suíte não ficar lenta. A
v2 em `app/data/v2/` é só LIDA, e a catraca da raiz (`conftest.py`) falha a sessão se
alguém a tocar.
"""

import hashlib
import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crai.ml import classificador_v4 as V4
from crai.ml import failure_classifier as fc
from crai.ml.populacao import LATENTES
from crai.scripts import gerar_base_v4_classificador as G
from crai.scripts import treinar_classificador_v4 as T
from crai.scripts.gerar_bases_v2 import _fixar_dtypes

PASTA_V2 = Path(__file__).resolve().parents[1] / "data" / "v2"
CINCO_V2 = ["populacao", "classificador", "comportamental", "liquidez", "voluntario"]
AMOSTRA = 600

requer_v2 = pytest.mark.skipif(not (PASTA_V2 / "MANIFESTO.json").exists(), reason="base v2 ausente")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def geracoes(tmp_path_factory):
    """Duas gerações da mesma amostra, com a mesma semente, em pastas diferentes."""
    saidas = []
    for nome in ("a", "b"):
        destino = tmp_path_factory.mktemp(f"v4_{nome}")
        assert G.main(["--seed", "42", "--out", str(destino), "--v2", str(PASTA_V2),
                       "--amostra-clientes", str(AMOSTRA)]) == 0
        saidas.append(destino)
    return saidas


@pytest.fixture(scope="module")
def base(geracoes):
    return pd.read_parquet(geracoes[0] / "classificador_v4.parquet")


@pytest.fixture(scope="module")
def manifesto(geracoes):
    return json.loads((geracoes[0] / "MANIFESTO_v4.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def v2_da_amostra():
    cob = pd.read_parquet(PASTA_V2 / "classificador.parquet")
    escolhidos = sorted(cob["customer_id"].unique())[:AMOSTRA]
    return cob[cob["customer_id"].isin(escolhidos)].reset_index(drop=True)


# == O que o treino não pode ver ==========================================

class TestOcultasForaDasFeatures:
    def test_nenhuma_feature_e_latente_nem_oculta(self):
        for lista in (T.FEATURES_A, T.FEATURES_B):
            assert not set(LATENTES) & set(lista)
            assert not [f for f in lista if f.startswith(V4.PREFIXO_OCULTO)]

    def test_o_cenario_a_e_o_contrato_de_producao(self):
        assert T.FEATURES_A == list(fc.ALL_FEATURES_V2)

    @pytest.mark.parametrize("intrusa", ["oculto_p_recuperacao", "oculto_saldo_na_janela",
                                         "satisfacao", "saude_financeira", "recovered"])
    def test_a_guarda_recusa_oculta_latente_e_o_rotulo(self, intrusa):
        with pytest.raises(ValueError, match="proibidas"):
            V4.conferir_features([*T.FEATURES_A, intrusa])

    @requer_v2
    def test_o_treino_aborta_com_uma_oculta_na_matriz(self, base):
        with pytest.raises(ValueError, match="proibidas"):
            T.matriz(base, [*T.FEATURES_A, "oculto_p_recuperacao"])

    @requer_v2
    def test_nenhuma_latente_no_parquet_e_o_que_e_novo_tem_prefixo_oculto(self, base, v2_da_amostra):
        assert not set(LATENTES) & set(base.columns)
        novas = [c for c in base.columns if c not in v2_da_amostra.columns]
        assert novas == V4.COLUNAS_OCULTAS
        assert all(c.startswith(V4.PREFIXO_OCULTO) for c in novas)


# == A base ================================================================

@requer_v2
class TestDeterminismo:
    def test_duas_geracoes_com_a_mesma_semente_dao_o_mesmo_arquivo(self, geracoes):
        a, b = (g / "classificador_v4.parquet" for g in geracoes)
        assert _sha(a) == _sha(b)

    def test_o_manifesto_bate_com_o_arquivo(self, geracoes, base, manifesto):
        meta = manifesto["arquivos"]["classificador_v4"]
        assert meta["sha256"] == _sha(geracoes[0] / "classificador_v4.parquet")
        assert meta["hash_canonico"] == G.hash_canonico(base)
        assert meta["linhas"] == len(base) and manifesto["experimento"] is True

    def test_o_rotulo_de_um_cliente_nao_depende_de_quem_mais_esta_na_tabela(self, base, tmp_path):
        """A amostra menor tem exatamente as linhas que esses clientes têm na maior."""
        assert G.main(["--seed", "42", "--out", str(tmp_path), "--v2", str(PASTA_V2),
                       "--amostra-clientes", "150"]) == 0
        menor = pd.read_parquet(tmp_path / "classificador_v4.parquet")
        da_maior = base[base["customer_id"].isin(set(menor["customer_id"]))].reset_index(drop=True)
        pd.testing.assert_frame_equal(menor, da_maior)

    def test_nem_das_datas_das_cobrancas_dos_outros(self, base, v2_da_amostra):
        """O caixa usa um calendário fixo. Antes ele ia da primeira à última cobrança da
        tabela recebida, e um recorte com outras datas mudava o rótulo do mesmo cliente."""
        pop = pd.read_parquet(PASTA_V2 / "populacao.parquet")
        datas = pd.to_datetime(v2_da_amostra["data_cobranca"])
        fora = v2_da_amostra.loc[(datas < "2026-06-10") | (datas > "2026-08-05"), "customer_id"]
        recorte = v2_da_amostra[~v2_da_amostra["customer_id"].isin(set(fora))]
        assert 20 < recorte["customer_id"].nunique() < AMOSTRA, "o recorte precisa ser mais estreito"
        novo = V4.rotular(recorte, pop, seed=42)
        esperado = base.loc[recorte.index]
        assert np.array_equal(novo["recovered"].to_numpy(), esperado["recovered"].to_numpy())
        assert np.allclose(novo["oculto_p_recuperacao"], esperado["oculto_p_recuperacao"])

    def test_cobranca_fora_do_calendario_do_caixa_e_recusada(self, v2_da_amostra):
        pop = pd.read_parquet(PASTA_V2 / "populacao.parquet")
        tarde = v2_da_amostra.head(5).assign(data_cobranca=pd.Timestamp("2026-09-20"))
        with pytest.raises(ValueError, match="fora do calendario"):
            V4.rotular(tarde, pop, seed=42)

    def test_outra_semente_da_outro_rotulo(self, base, v2_da_amostra):
        pop = pd.read_parquet(PASTA_V2 / "populacao.parquet")
        outro = V4.rotular(v2_da_amostra, pop, seed=7)
        assert (outro["recovered"].to_numpy() != base["recovered"].to_numpy()).mean() > 0.05


@requer_v2
class TestV2Intacta:
    def test_os_cinco_parquets_da_v2_continuam_iguais_ao_manifesto(self, geracoes):
        declarado = json.loads((PASTA_V2 / "MANIFESTO.json").read_text(encoding="utf-8"))["arquivos"]
        for nome in CINCO_V2:
            assert _sha(PASTA_V2 / f"{nome}.parquet") == declarado[nome]["sha256"], nome

    def test_o_manifesto_registra_a_v2_igual_antes_e_depois(self, manifesto):
        assert manifesto["entradas_v2"]["v2_igual_antes_e_depois"] is True

    def test_out_na_pasta_da_v2_e_recusado(self):
        with pytest.raises(SystemExit, match="v2 nao e regravada"):
            G.main(["--out", str(PASTA_V2), "--v2", str(PASTA_V2)])

    def test_as_cobrancas_e_as_features_sao_as_da_v2(self, base, v2_da_amostra, manifesto):
        # A base v4 sai com os inteiros em int64 (`_fixar_dtypes`, igual em qualquer sistema);
        # a v2 pode ter sido gravada com int32 (Windows). Compara-se o valor, no mesmo dtype.
        iguais = [c for c in v2_da_amostra.columns if c != "recovered"]
        pd.testing.assert_frame_equal(base[iguais], _fixar_dtypes(v2_da_amostra[iguais]))
        assert manifesto["verificacoes"]["features_iguais_as_da_v2"] is True


@requer_v2
class TestFaixaEFormas:
    def test_a_taxa_de_recuperacao_fica_na_faixa_declarada(self, base):
        de, ate = V4.FAIXA_TAXA_RECUPERACAO
        assert de <= base["recovered"].mean() <= ate

    def test_sem_nulos_e_rotulo_binario(self, base):
        assert not base.isna().any().any()
        assert set(base["recovered"].unique()) <= {0, 1}

    def test_a_probabilidade_oculta_e_probabilidade(self, base):
        p = base["oculto_p_recuperacao"]
        assert p.between(V4.P_EXCECAO * 0.5 - 1e-9, 1 - V4.P_EXCECAO * 0.5 + 1e-9).all()

    def test_fatura_anual_quase_nunca_recupera(self, base):
        """Dez mensalidades de saldo é raro: o tamanho da fatura pesa, como no mundo."""
        anual = base[base["ciclo"] == "anual"]
        if len(anual) < 30:
            pytest.skip("amostra com menos de 30 faturas anuais")
        assert anual["recovered"].mean() < 0.15


# == O mecanismo, em casos montados à mão ==================================

def _caso(causa="insufficient_funds", metodo="pix_automatico", degrau=1, fator=1.0,
          satisfacao=0.7, saude=0.65, n=40):
    """`n` clientes iguais, uma cobrança cada, em dias espalhados por dois meses."""
    ids = [f"CLI_teste_{i:03d}" for i in range(n)]
    pop = pd.DataFrame({"customer_id": ids, "mrr": 1000.0, "billing_profile": "CLT",
                        "tenure_months": 12, "seats": 5, "satisfacao": satisfacao,
                        "fit_produto": 0.7, "pressao_preco": 0.4, "saude_financeira": saude})
    datas = pd.date_range("2026-06-01", periods=n, freq="D")
    cob = pd.DataFrame({"customer_id": ids, "data_cobranca": datas,
                        "invoice_amount": 1000.0 * fator, "attempt_count": degrau,
                        "gateway_error_code": causa, "metodo_pagamento": metodo})
    return V4.rotular(cob, pop, seed=42)


class TestMecanismo:
    MINIMO, MAXIMO = V4.P_EXCECAO * 0.5, 1 - V4.P_EXCECAO * 0.5

    def test_saldo_insuficiente_e_tudo_ou_nada_dado_o_caixa(self):
        p = _caso("insufficient_funds")["oculto_p_recuperacao"]
        assert set(np.round(p, 6)) <= {round(self.MINIMO, 6), round(self.MAXIMO, 6)}
        assert 0 < (p > 0.5).mean() < 1, "com 40 dias espalhados, há casos dos dois lados"

    def test_no_ultimo_degrau_nao_ha_nova_tentativa(self):
        r = _caso("insufficient_funds", degrau=4)
        assert (r["oculto_retentativas"] == 0).all()
        assert np.allclose(r["oculto_p_recuperacao"], self.MINIMO)

    def test_cada_degrau_tem_uma_tentativa_a_menos(self):
        assert [int(_caso(degrau=d)["oculto_retentativas"].iloc[0]) for d in (1, 2, 3, 4)] == [3, 2, 1, 0]

    def test_autorizacao_revogada_depende_do_cliente_e_e_rara(self):
        teto = (V4.P_REAUTORIZA[0] + V4.P_REAUTORIZA[1]) * (1 - V4.P_EXCECAO) + self.MINIMO
        assert (_caso("authorization_revoked")["oculto_p_recuperacao"] <= teto + 1e-9).all()

    def test_cliente_mais_satisfeito_reautoriza_mais(self):
        baixo = _caso("authorization_revoked", satisfacao=0.1)["oculto_p_recuperacao"].max()
        alto = _caso("authorization_revoked", satisfacao=0.9)["oculto_p_recuperacao"].max()
        assert alto > baixo

    def test_boleto_depende_de_o_cliente_pagar(self):
        r = _caso("insufficient_funds", metodo="boleto", saude=0.5)
        esperado = (V4.P_PAGA_BOLETO[0] + V4.P_PAGA_BOLETO[1] * 0.5) * (1 - V4.P_EXCECAO) + self.MINIMO
        com_saldo = r[r["oculto_saldo_na_janela"] == 1]["oculto_p_recuperacao"]
        assert len(com_saldo) and np.allclose(com_saldo, esperado)
        assert np.allclose(r[r["oculto_saldo_na_janela"] == 0]["oculto_p_recuperacao"], self.MINIMO)

    def test_fatura_de_dez_mensalidades_nao_encontra_saldo(self):
        assert _caso(fator=10.0)["oculto_saldo_na_janela"].mean() < 0.2

    def test_erro_tecnico_recupera_mais_que_recusa_generica_com_o_mesmo_caixa(self):
        tecnico = _caso("processing_error")
        generico = _caso("generic_decline")
        assert (tecnico["oculto_tentativas_com_saldo"] == generico["oculto_tentativas_com_saldo"]).all()
        assert (tecnico["oculto_p_recuperacao"] >= generico["oculto_p_recuperacao"] - 1e-9).all()

    def test_o_caixa_e_reproduzivel_e_proprio_de_cada_cliente(self):
        datas = pd.date_range("2026-06-01", periods=30, freq="D")
        from crai.ml.calibracao import parametros
        from crai.ml.synthetic_data import _business_day_index
        P, dias_uteis = parametros("sintetico_calibrado")["liquidity"], _business_day_index(datas)
        a = V4.caixa_do_cliente("CLI_x", "PJ", datas, dias_uteis, 42, P)
        b = V4.caixa_do_cliente("CLI_x", "PJ", datas, dias_uteis, 42, P)
        c = V4.caixa_do_cliente("CLI_y", "PJ", datas, dias_uteis, 42, P)
        assert np.array_equal(a, b) and not np.array_equal(a, c)


# == As contas do treino ===================================================

class TestContas:
    def test_o_limiar_e_o_maior_da_grade_com_o_recall_minimo(self):
        y = np.array([1] * 10 + [0] * 10)
        s = np.array([0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.30] + [0.2] * 10)
        assert T.escolher_limiar(y, s, 0.90) == 0.55      # 9 de 10 com score >= 0,55
        assert T.escolher_limiar(y, s, 1.00) == 0.30
        assert T.escolher_limiar(y, np.zeros(20), 0.90) is None

    def test_a_matriz_de_confusao_no_limiar(self):
        m = T.no_limiar([1, 1, 0, 0], [0.9, 0.2, 0.8, 0.1], 0.5)
        assert m["matriz"] == {"tn": 1, "fp": 1, "fn": 1, "tp": 1}
        assert m["recall"] == 0.5 and m["precisao"] == 0.5 and m["fracao_marcada"] == 0.5

    def test_precisao_em_recall_fixo(self):
        y = np.array([1, 0, 1, 0, 1, 0])
        s = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4])
        assert T.precisao_em_recall(y, s, 1.0) == {
            "precisao": 0.6, "falsos_positivos": 2, "taxa_de_falsos_positivos": 0.6667,
            "ganho_sobre_a_taxa_de_base": 1.2, "fracao_marcada": 0.8333}


# == O treino, numa amostra ================================================

@requer_v2
class TestTreino:
    @pytest.fixture(scope="class")
    def rodada(self, geracoes, tmp_path_factory):
        saida = tmp_path_factory.mktemp("modelos_v4")
        metricas = T.treinar(geracoes[0], PASTA_V2, saida, completo=False, com_liquidez=False)
        return saida, metricas

    def test_grava_os_artefatos_com_nomes_proprios(self, rodada):
        saida, _ = rodada
        nomes = sorted(p.name for p in saida.iterdir())
        assert nomes == sorted(["failure_classifier_v4_xgb.joblib", "failure_classifier_v4_rf.joblib",
                                "failure_classifier_v4_encoders.joblib",
                                "failure_classifier_v4_features.joblib",
                                "failure_classifier_v4_meta.json", "train_metrics_v4.json"])

    def test_o_carregador_de_producao_nao_le_nenhum_arquivo_do_v4(self, rodada):
        saida, _ = rodada
        fonte = inspect.getsource(fc.FailureClassifier.load)
        assert not any(p.name in fonte for p in saida.iterdir())
        meta = json.loads((saida / "failure_classifier_v4_meta.json").read_text(encoding="utf-8"))
        assert meta["experimento"] is True and meta["contrato_de_producao"] is False

    def test_as_metricas_trazem_os_cenarios_e_nada_promovido(self, rodada):
        _, m = rodada
        assert set(m["cenarios"]) == {"A", "B", "C"}
        assert m["v2_medida"]["medido"] in (True, False) and "sem_a_taxa_de_base" in m["criterio"]
        assert m["cenarios"]["C"] == {"medido": False, "motivo": "desligado (--sem-liquidez)"}
        assert m["cenarios"]["A"]["features"] == T.FEATURES_A
        assert m["cenarios"]["B"]["features"] == T.FEATURES_B
        assert m["criterio"]["promovido"] is False
        assert "NAO promovido" in m["modelo"]

    def test_o_holdout_nao_divide_cliente_com_o_treino(self, rodada):
        _, m = rodada
        assert m["split"]["clientes_treino"] + m["split"]["clientes_teste"] == AMOSTRA

    def test_a_matriz_gravada_bate_com_a_recomputada(self, rodada, geracoes):
        saida, m = rodada
        df, _ = T.ler_base(geracoes[0], PASTA_V2)
        _, idx_te = T.split_por_cliente(df["customer_id"].to_numpy(), T.TEST_SIZE, T.SEED)
        modelo = fc.FailureClassifier()
        import joblib
        modelo.xgb = joblib.load(saida / "failure_classifier_v4_xgb.joblib")
        modelo.rf = joblib.load(saida / "failure_classifier_v4_rf.joblib")
        _, X = T.matriz(df, T.FEATURES_A)
        y = df["recovered"].to_numpy(dtype=int)[idx_te]
        gravado = m["cenarios"]["A"]["limiar"]["no_limiar_em_uso_hoje"]
        assert T.no_limiar(y, T.prever(modelo, X[idx_te]), fc.LIMIAR_CLASSIFICACAO)["matriz"] == gravado["matriz"]

    def test_base_que_nao_confere_com_o_manifesto_e_recusada(self, geracoes, tmp_path):
        for nome in ("classificador_v4.parquet", "MANIFESTO_v4.json"):
            (tmp_path / nome).write_bytes((geracoes[0] / nome).read_bytes())
        df = pd.read_parquet(tmp_path / "classificador_v4.parquet")
        df.loc[0, "recovered"] = 1 - df.loc[0, "recovered"]
        df.to_parquet(tmp_path / "classificador_v4.parquet", index=False)
        with pytest.raises(SystemExit, match="nao confere"):
            T.ler_base(tmp_path, PASTA_V2)


# == O cenário C: a previsão de liquidez como feature =====================

class TestPrevisaoDeLiquidez:
    def test_a_janela_so_tem_dias_anteriores_a_cobranca(self):
        feats = np.arange(40 * 2, dtype=np.float32).reshape(40, 2) + 1
        j = T.janelas_antes_da_cobranca(feats, np.array([35, 3, 0]), janela=30)
        assert j.shape == (3, 30, 2)
        assert np.array_equal(j[0], feats[5:35]), "os 30 dias até a véspera, sem o dia da cobrança"
        assert np.array_equal(j[1, -3:], feats[0:3]) and not j[1, :-3].any(), "pouco histórico: zeros antes"
        assert not j[2].any(), "sem histórico nenhum: janela zerada"

    def test_as_features_do_cenario_c_nao_sao_ocultas(self):
        assert T.FEATURES_C == T.FEATURES_B + T.EXTRAS_C
        V4.conferir_features(T.FEATURES_C)

    @requer_v2
    def test_a_previsao_e_probabilidade_e_reproduz(self, base):
        amostra = base[base["customer_id"].isin(sorted(base["customer_id"].unique())[:60])].reset_index(drop=True)
        a, motivo = T.previsao_de_liquidez(amostra, PASTA_V2, 42, "sintetico_calibrado")
        if a is None:
            pytest.skip(motivo)
        assert list(a.columns) == T.EXTRAS_C and len(a) == len(amostra)
        assert a.to_numpy().min() >= 0.0 and a.to_numpy().max() <= 1.0
        assert (a["liquidez_prevista_max_na_janela"] >= a[["liquidez_prevista_d2", "liquidez_prevista_d4",
                                                           "liquidez_prevista_d6"]].max(axis=1) - 1e-6).all()
        b, _ = T.previsao_de_liquidez(amostra, PASTA_V2, 42, "sintetico_calibrado")
        pd.testing.assert_frame_equal(a, b)


    @requer_v2
    def test_a_previsao_nao_ve_o_dia_da_cobranca_nem_o_futuro(self, base, monkeypatch):
        """A prova de que o cenário C não vaza o rótulo: troca-se por lixo o saldo do dia
        da cobrança em diante, e as quatro features não mudam. Com lixo desde a véspera,
        mudam (o teste tem como falhar)."""
        uma_por_cliente = base.drop_duplicates("customer_id").head(80).reset_index(drop=True)
        original, motivo = T.previsao_de_liquidez(uma_por_cliente, PASTA_V2, 42, "sintetico_calibrado")
        if original is None:
            pytest.skip(motivo)
        datas = V4.datas_do_caixa()
        dia_de = dict(zip(uma_por_cliente["customer_id"],
                          (pd.to_datetime(uma_por_cliente["data_cobranca"]) - datas[0]).dt.days))
        caixa = V4.caixa_do_cliente

        def com_lixo(recuo):
            def falso(cid, *args, **kwargs):
                saldo = caixa(cid, *args, **kwargs).copy()
                saldo[max(dia_de[cid] - recuo, 0):] = 37.0
                return saldo
            monkeypatch.setattr(V4, "caixa_do_cliente", falso)
            return T.previsao_de_liquidez(uma_por_cliente, PASTA_V2, 42, "sintetico_calibrado")[0]

        pd.testing.assert_frame_equal(com_lixo(0), original)
        assert not com_lixo(1).equals(original), "lixo na véspera tem de mudar a previsão"


class TestIsolamento:
    def test_os_scripts_do_v4_nao_importam_o_treino_de_producao(self):
        for modulo in (T, G, V4):
            fonte = inspect.getsource(modulo)
            assert "train_all" not in fonte, modulo.__name__

    def test_o_treino_nao_grava_em_models_de_producao(self):
        fonte = inspect.getsource(T)
        assert "MODELS_DIR" not in fonte and "_save_models" not in fonte

"""tests/test_populacao_compartilhada.py — base v2: população compartilhada e split por cliente.

Bloco B (20/09/2026). O que este arquivo trava, por seção:

  SPLIT POR CLIENTE (B.3 — o item que não pode ser pulado). A base v2 tem
  várias cobranças por cliente e vários eventos por cliente; um split por
  linha põe o mesmo cliente nos dois lados do holdout e as features que são
  atributos dele viram identificador. `failure_classifier.train(groups=)` e
  `voluntary_risk.train(groups=)` usam `GroupShuffleSplit`, e aqui se confere
  que NENHUM `customer_id` aparece nos dois lados — no helper e nos dois
  módulos treinados de verdade.

  MECANISMO v2 DO train_all (B.2 / B.5). Com uma pasta v2 completa (cinco
  parquet + manifesto), `resolver_bases` escolhe v2 por default, confere o
  sha256 e recusa arquivo alterado; os artefatos vão para `models/v2/`.

  POPULAÇÃO E VISÕES (B.1 / B.6). `gerar_populacao` é determinística; as
  quatro visões carregam `customer_id` e todo id existe na população; a
  interseção das quatro não é vazia; o mesmo cliente tem o mesmo `mrr` em
  todas; `invoice_amount / mrr` cai em `visoes.FAIXA_FATOR_CICLO` em toda
  linha; `card_brand` não existe e nenhum código de cartão aparece;
  `failure_count_90d` é conferido num caso pequeno montado à mão.

  A BASE DO PACOTE (`app/data/v2/`): junção por `customer_id`, interseção
  1.330, razão invoice/MRR ≈ 1. Pula num clone limpo (a pasta é ignorada).

  card_brand × metodo_pagamento (adendo §3): o artefato v1 e o v2 reportam
  cada um a sua lista de features e `predict` funciona com os dois sem o
  encoder de um contaminar o outro.

  LIMIAR DO AUTOENCODER (B.4): `THRESHOLD_PERCENTIL_V2` é o que o critério
  declarado escolhe na curva medida (docs/evidencia_base_v2/), não um número
  solto.

Todo treino aqui vai para `tmp_path` (MODELS_DIR monkeypatched): os binários
reais de `models/` são o gate de honestidade do README (`tests/conftest.py`).
"""

from argparse import Namespace

import numpy as np
import pandas as pd
import pytest

from crai.ml import failure_classifier as classifier_module
from crai.ml import voluntary_risk as voluntary_module
from crai.ml.failure_classifier import FailureClassifier
from crai.ml.split import conferir_sem_vazamento, split_por_cliente
from crai.ml.synthetic_data import generate_dataset, generate_voluntary_dataset
from crai.ml.voluntary_risk import VoluntaryRiskModel
from crai.scripts import gerar_bases as gb
from crai.scripts import train_all


def _com_clientes(df: pd.DataFrame, n_clientes: int, seed: int) -> pd.DataFrame:
    """Anexa um `customer_id` a uma base v1, com várias linhas por cliente —
    o formato da v2 no que interessa ao split."""
    rng = np.random.default_rng(seed)
    ids = rng.choice([f"CLI_{i:06d}" for i in range(n_clientes)], size=len(df))
    out = df.copy()
    out.insert(0, "customer_id", ids)
    return out


# ══════════════════════════════════════════════════════════════════════════
# SPLIT POR CLIENTE
# ══════════════════════════════════════════════════════════════════════════

class TestSplitPorCliente:

    def test_helper_nenhum_cliente_nos_dois_lados(self):
        groups = np.repeat([f"CLI_{i:06d}" for i in range(200)], 4)
        idx_tr, idx_te = split_por_cliente(groups, test_size=0.2, seed=42)
        assert len(idx_tr) + len(idx_te) == len(groups)
        assert not (set(groups[idx_tr]) & set(groups[idx_te]))
        conferir_sem_vazamento(groups, idx_tr, idx_te)
        # Fração de teste é de CLIENTES: 20% de 200 = 40 clientes = 160 linhas.
        assert len(set(groups[idx_te])) == 40
        assert len(idx_te) == 160

    def test_helper_e_deterministico_e_varia_com_a_semente(self):
        groups = np.repeat(np.arange(100), 3)
        a = split_por_cliente(groups, 0.2, 42)
        b = split_por_cliente(groups, 0.2, 42)
        c = split_por_cliente(groups, 0.2, 43)
        assert np.array_equal(a[1], b[1])
        assert not np.array_equal(a[1], c[1])

    def test_conferir_sem_vazamento_pega_vazamento(self):
        groups = np.array(["A", "A", "B", "B"])
        with pytest.raises(AssertionError, match="dois lados"):
            conferir_sem_vazamento(groups, np.array([0, 2]), np.array([1, 3]))

    def test_classifier_split_por_cliente(self, tmp_path, monkeypatch):
        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path / "clf")
        df = _com_clientes(generate_dataset(n_samples=800, seed=42), n_clientes=220, seed=1)
        clf = FailureClassifier()
        m = clf.train(dados=df, groups=df["customer_id"])
        assert m["split"] == "por_cliente"
        assert m["n_clientes_treino"] + m["n_clientes_teste"] == df["customer_id"].nunique()
        assert not (clf._grupos_split["treino"] & clf._grupos_split["teste"])
        assert clf._grupos_split["treino"] | clf._grupos_split["teste"] == set(df["customer_id"])
        assert 0.5 < m["auc"] <= 1.0

    def test_classifier_sem_groups_continua_por_linha(self, tmp_path, monkeypatch):
        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path / "clf")
        m = FailureClassifier().train(n_samples=600)
        assert m["split"] == "por_linha"
        assert "n_clientes_teste" not in m

    def test_classifier_groups_com_tamanho_errado_e_erro(self, tmp_path, monkeypatch):
        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path / "clf")
        df = generate_dataset(n_samples=300, seed=42)
        with pytest.raises(ValueError, match="groups"):
            FailureClassifier().train(dados=df, groups=np.arange(10))

    def test_voluntario_split_por_cliente(self, tmp_path, monkeypatch):
        monkeypatch.setattr(voluntary_module, "MODELS_DIR", tmp_path / "vol")
        df = _com_clientes(generate_voluntary_dataset(n_samples=900, seed=42),
                           n_clientes=300, seed=2)
        vol = VoluntaryRiskModel()
        m = vol.train(dados=df, groups=df["customer_id"])
        assert m["split"] == "por_cliente"
        assert not (vol._grupos_split["treino"] & vol._grupos_split["teste"])
        assert m["n_clientes_treino"] + m["n_clientes_teste"] == df["customer_id"].nunique()

    def test_voluntario_sem_groups_continua_por_linha(self, tmp_path, monkeypatch):
        monkeypatch.setattr(voluntary_module, "MODELS_DIR", tmp_path / "vol")
        m = VoluntaryRiskModel().train(n_samples=400)
        assert m["split"] == "por_linha"


# ══════════════════════════════════════════════════════════════════════════
# MECANISMO v2 DO train_all
# ══════════════════════════════════════════════════════════════════════════

def _pasta_v2_falsa(pasta, seed: int = 42) -> dict:
    """Uma pasta com a FORMA da base v2 (cinco parquet com `customer_id` +
    manifesto no formato de `gerar_bases`), montada sobre as visões v1. Serve
    para testar o mecanismo do train_all, não a população em si."""
    bases = gb.gerar_bases(seed=seed, classifier_samples=300, anomaly_samples=200,
                           payday_customers=6, payday_days=60, voluntario_samples=150)
    pop = pd.DataFrame({"customer_id": [f"CLI_{i:06d}" for i in range(80)],
                        "mrr": np.linspace(300, 8000, 80)})
    v2 = {"populacao": (pop, {"gerador": "gerar_populacao", "n": 80, "seed": seed})}
    for nome, (df, params) in bases.items():
        if "customer_id" in df.columns:          # liquidez já tem, com outro formato de id
            df = df.copy()
        else:
            df = _com_clientes(df, n_clientes=80, seed=seed)
        v2[nome] = (df, params)
    return gb.escrever_bases(v2, pasta, seed, "sintetico", arquivos=gb.ARQUIVOS_V2)


def _args(**kw) -> Namespace:
    base = dict(base=None, dados=None, sem_dados=False, quick=False, fonte="sintetico",
                classifier_samples=None, anomaly_samples=None,
                payday_customers=None, voluntario_samples=None)
    return Namespace(**{**base, **kw})


class TestTrainAllV2:

    def test_pasta_v2_completa_e_escolhida_por_default(self, tmp_path):
        _pasta_v2_falsa(tmp_path)
        args = _args(dados=str(tmp_path))
        bases, desc = train_all.resolver_bases(args)
        assert args.base == "v2"
        assert set(bases) == set(gb.ARQUIVOS_V2)
        assert desc.startswith("v2")
        assert args.bases_info["base"] == "v2"
        assert args.classifier_samples == 300
        assert args.voluntario_samples == 150

    def test_sem_populacao_a_pasta_e_v1(self, tmp_path):
        _pasta_v2_falsa(tmp_path)
        (tmp_path / gb.ARQUIVOS_V2["populacao"]).unlink()
        args = _args(dados=str(tmp_path))
        # Cai para v1: a mesma pasta é uma base v1 válida? Não — o manifesto
        # lista cinco arquivos e o leitor v1 exige só quatro, que existem.
        bases, _ = train_all.resolver_bases(args)
        assert args.base == "v1"
        assert set(bases) == set(gb.ARQUIVOS)

    def test_base_v2_explicita_sem_pasta_e_erro(self, tmp_path):
        with pytest.raises(SystemExit, match="Base v2 nao encontrada"):
            train_all.resolver_bases(_args(base="v2", dados=str(tmp_path / "nada")))

    def test_quick_e_sem_dados_nao_valem_para_v2(self, tmp_path):
        _pasta_v2_falsa(tmp_path)
        with pytest.raises(SystemExit, match="--base v1"):
            train_all.resolver_bases(_args(base="v2", dados=str(tmp_path), quick=True))
        with pytest.raises(SystemExit, match="--base v1"):
            train_all.resolver_bases(_args(base="v2", dados=str(tmp_path), sem_dados=True))

    def test_sha256_alterado_falha_alto_com_o_nome_do_arquivo(self, tmp_path):
        _pasta_v2_falsa(tmp_path)
        alvo = tmp_path / gb.ARQUIVOS_V2["classificador"]
        with open(alvo, "r+b") as f:
            f.seek(200)
            byte = f.read(1)
            f.seek(200)
            f.write(bytes([byte[0] ^ 0xFF]))
        with pytest.raises(ValueError, match="classificador.parquet"):
            train_all.resolver_bases(_args(base="v2", dados=str(tmp_path)))

    def test_apontar_models_dir_v2_e_volta(self):
        raiz = train_all.MODELS_RAIZ
        try:
            destino = train_all.apontar_models_dir("v2")
            assert destino == raiz / "v2"
            for modulo in (classifier_module, voluntary_module,
                           train_all.anomaly_module, train_all.payday_module):
                assert modulo.MODELS_DIR == raiz / "v2"
        finally:
            assert train_all.apontar_models_dir(None) == raiz
        assert classifier_module.MODELS_DIR == raiz


# ══════════════════════════════════════════════════════════════════════════
# POPULAÇÃO E VISÕES (B.1 / B.6) — geradas pequenas aqui, com o código do pacote
# ══════════════════════════════════════════════════════════════════════════

import json  # noqa: E402
from pathlib import Path  # noqa: E402

from pandas.testing import assert_frame_equal  # noqa: E402

from crai.ml import anomaly_detector as anomaly_module  # noqa: E402
from crai.ml import populacao as P  # noqa: E402
from crai.ml import visoes as V  # noqa: E402
from crai.scripts.gerar_bases import DADOS_V2_DIR  # noqa: E402

CODIGOS_CARTAO = {"expired_card", "card_declined", "do_not_honor"}
RAIZ_REPO = Path(__file__).resolve().parent.parent.parent
CURVA_MEDIDA = RAIZ_REPO / "docs" / "evidencia_base_v2" / "curva_limiar_anomalia_v2_varredura.json"


@pytest.fixture(scope="module")
def base_pequena():
    """População de 3.000 clientes e as quatro visões, com a mesma cadeia de
    chamadas de `gerar_bases_v2.gerar` (comportamental recebe as cobranças;
    voluntário recebe o retrato)."""
    pop = P.gerar_populacao(3000, seed=42)
    cob = V.visao_classificador(pop, n_cobrancas=3000, seed=42)
    comp = V.visao_comportamental(pop, cob, seed=42)
    liq = V.visao_liquidez(pop, n_customers=200, n_days=60, seed=42)
    vol = V.visao_voluntario(pop, comp, n_eventos=3000, seed=42)
    return pop, {"classificador": cob, "comportamental": comp, "liquidez": liq, "voluntario": vol}


class TestPopulacao:

    def test_gerar_populacao_duas_vezes_identica(self):
        a = P.gerar_populacao(1000, seed=42)
        b = P.gerar_populacao(1000, seed=42)
        assert_frame_equal(a, b)
        assert list(a.columns) == P.COLUNAS_POPULACAO
        assert a["customer_id"].is_unique
        assert a["customer_id"].iloc[0] == "CLI_000000"

    def test_latentes_existem_e_ficam_em_0_1(self):
        pop = P.gerar_populacao(1000, seed=42)
        for col in P.LATENTES:
            assert pop[col].between(0, 1).all(), col

    def test_escala_de_mrr_e_saas_b2b(self):
        pop = P.gerar_populacao(20000, seed=42)
        assert 1500 < pop["mrr"].median() < 2100          # ~R$ 1.800
        assert 6500 < pop["mrr"].quantile(0.90) < 9500    # ~R$ 8.000


class TestVisoes:

    def test_as_quatro_visoes_tem_customer_id_e_todo_id_existe_na_populacao(self, base_pequena):
        pop, visoes = base_pequena
        ids_pop = set(pop["customer_id"])
        for nome, df in visoes.items():
            assert "customer_id" in df.columns, nome
            assert set(df["customer_id"]) <= ids_pop, nome

    def test_intersecao_das_quatro_nao_e_vazia(self, base_pequena):
        _, visoes = base_pequena
        comum = set.intersection(*(set(df["customer_id"]) for df in visoes.values()))
        assert len(comum) > 0
        # Os pares que o relatório cita: cobrança ∩ retrato e retrato ∩ liquidez.
        assert set(visoes["classificador"]["customer_id"]) <= set(visoes["comportamental"]["customer_id"])
        assert set(visoes["liquidez"]["customer_id"]) <= set(visoes["comportamental"]["customer_id"])

    def test_mesmo_cliente_mesmo_mrr_em_todas_as_visoes(self, base_pequena):
        pop, visoes = base_pequena
        mrr = pop.set_index("customer_id")["mrr"]
        comp = visoes["comportamental"]
        assert (comp["mrr_brl"].to_numpy() == mrr.loc[comp["customer_id"]].to_numpy()).all()
        vol = visoes["voluntario"]
        assert (vol["mrr"].to_numpy() == mrr.loc[vol["customer_id"]].to_numpy()).all()
        # O classificador não carrega `mrr`; a fatura deriva dele (teste seguinte).

    def test_invoice_amount_sobre_mrr_na_faixa_em_toda_linha(self, base_pequena):
        pop, visoes = base_pequena
        cob = visoes["classificador"]
        mrr = pop.set_index("customer_id")["mrr"].loc[cob["customer_id"]].to_numpy()
        fator = cob["invoice_amount"].to_numpy() / mrr
        lo, hi = V.FAIXA_FATOR_CICLO
        assert (fator >= lo - 1e-6).all() and (fator <= hi + 1e-6).all()
        # Mediana da fatura ~ mediana do MRR: o 50,2x sumiu.
        assert 0.85 < cob["invoice_amount"].median() / pop["mrr"].median() < 1.2

    def test_classificador_sem_card_brand_e_sem_codigos_de_cartao(self, base_pequena):
        _, visoes = base_pequena
        cob = visoes["classificador"]
        assert "card_brand" not in cob.columns
        assert "metodo_pagamento" in cob.columns
        assert not (set(cob["gateway_error_code"]) & CODIGOS_CARTAO)
        assert set(cob["gateway_error_code"]) <= set(V.CAUSAS_FALHA)
        assert set(cob["metodo_pagamento"]) <= set(V.METODOS_PAGAMENTO)

    def test_failure_count_90d_caso_pequeno_conferido_a_mao(self):
        """Cliente A: 01/01, 15/02, 28/02, 01/06. Janela de 90 dias que termina
        na cobrança (aberta à esquerda: `> d - 90`): 15/02 ve 01/01 (45 d) -> 1;
        28/02 ve 01/01 (58 d) e 15/02 (13 d) -> 2; 01/06 nao ve 28/02 (93 d) -> 0.
        Cliente B, uma cobrança -> 0."""
        df = pd.DataFrame({
            "customer_id": ["A", "A", "A", "A", "B"],
            "data_cobranca": pd.to_datetime(
                ["2026-01-01", "2026-02-15", "2026-02-28", "2026-06-01", "2026-04-10"]),
        })
        assert V._contar_janela(df, dias=90).tolist() == [0, 1, 2, 0, 0]
        # attempt_count = cobranças nos 14 dias anteriores + 1: 28/02 ve 15/02 (13 d) -> 2;
        # 15/02 nao ve 01/01 (45 d) -> 1.
        assert np.clip(V._contar_janela(df, dias=14) + 1, 1, 4).tolist() == [1, 1, 2, 1, 1]
        # A borda e estrita: exatamente 14 dias antes NAO conta.
        borda = pd.DataFrame({"customer_id": ["C", "C"],
                              "data_cobranca": pd.to_datetime(["2026-02-15", "2026-03-01"])})
        assert V._contar_janela(borda, dias=14).tolist() == [0, 0]

    def test_failure_count_90d_da_visao_bate_com_recontagem_independente(self, base_pequena):
        _, visoes = base_pequena
        cob = visoes["classificador"]
        esperado = np.zeros(len(cob), dtype=int)
        for _, g in cob.groupby("customer_id"):
            datas = g["data_cobranca"].to_numpy()
            for pos, (i, d) in enumerate(zip(g.index, datas)):
                anteriores = datas[:pos]
                esperado[i] = int(((anteriores > d - np.timedelta64(90, "D")) & (anteriores <= d)).sum())
        assert (cob["failure_count_90d"].to_numpy() == esperado).all()


# ══════════════════════════════════════════════════════════════════════════
# A BASE DO PACOTE (data/v2) — pula num clone limpo
# ══════════════════════════════════════════════════════════════════════════

requer_base_v2 = pytest.mark.skipif(
    not (DADOS_V2_DIR / "MANIFESTO.json").exists(),
    reason="app/data/v2 ausente — base v2 não está nesta máquina")


@requer_base_v2
class TestBaseV2DoPacote:

    @pytest.fixture(scope="class")
    def v2(self):
        return gb.ler_bases(DADOS_V2_DIR, conferir_sha256=True, arquivos=gb.ARQUIVOS_V2)

    def test_cinco_arquivos_conferem_com_o_manifesto(self, v2):
        bases, manifesto = v2
        assert set(bases) == set(gb.ARQUIVOS_V2)
        assert manifesto["semente"] == 42

    def test_juncao_por_customer_id_funciona(self, v2):
        bases, manifesto = v2
        pop = bases["populacao"]
        ids_pop = set(pop["customer_id"])
        for nome in gb.ARQUIVOS:
            assert set(bases[nome]["customer_id"]) <= ids_pop, nome
        comum = set.intersection(*(set(bases[n]["customer_id"]) for n in gb.ARQUIVOS))
        assert len(comum) == manifesto["verificacoes"]["intersecao_das_quatro_tabelas"] == 1330
        juncao = bases["classificador"].merge(pop, on="customer_id", how="left")
        assert juncao["mrr"].notna().all()

    def test_o_50x_sumiu(self, v2):
        bases, _ = v2
        razao = bases["classificador"]["invoice_amount"].median() / bases["populacao"]["mrr"].median()
        assert 0.9 < razao < 1.1
        assert "card_brand" not in bases["classificador"].columns
        assert not (set(bases["classificador"]["gateway_error_code"]) & CODIGOS_CARTAO)


# ══════════════════════════════════════════════════════════════════════════
# card_brand × metodo_pagamento — o artefato sabe com que features foi treinado
# ══════════════════════════════════════════════════════════════════════════

class TestFeaturesDoArtefato:

    def test_v1_e_v2_carregam_a_propria_lista_e_predizem_sem_contaminacao(
            self, tmp_path, monkeypatch, base_pequena):
        _, visoes = base_pequena
        cob_v2 = visoes["classificador"]

        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path / "v1")
        m1 = FailureClassifier().train(n_samples=800)
        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path / "v2")
        m2 = FailureClassifier().train(dados=cob_v2, groups=cob_v2["customer_id"])

        assert m1["features_usadas"] == classifier_module.ALL_FEATURES
        assert m2["features_usadas"] == classifier_module.ALL_FEATURES_V2
        assert "card_brand" not in m2["features_usadas"]
        assert "metodo_pagamento" in m2["features_usadas"]

        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path / "v1")
        clf1 = FailureClassifier()
        assert clf1.load()
        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path / "v2")
        clf2 = FailureClassifier()
        assert clf2.load()

        assert clf1.feature_names == classifier_module.ALL_FEATURES
        assert clf2.feature_names == classifier_module.ALL_FEATURES_V2
        assert clf1.meta["features"] == classifier_module.ALL_FEATURES
        assert clf2.meta["features"] == classifier_module.ALL_FEATURES_V2
        assert set(clf1.label_encoders) == {"gateway_error_code", "card_brand"}
        assert set(clf2.label_encoders) == {"gateway_error_code", "metodo_pagamento"}
        assert "expired_card" in clf1.label_encoders["gateway_error_code"].classes_
        assert "expired_card" not in clf2.label_encoders["gateway_error_code"].classes_

        comum = dict(tenure_months=12, day_of_month=10, invoice_amount=1800.0, avg_ticket=1750.0,
                     payment_history_score=0.7, failure_count_90d=1, hour_of_day=10,
                     day_of_week=2, attempt_count=1, ltv_estimated=20000.0)
        r1 = clf1.predict({**comum, "gateway_error_code": "expired_card", "card_brand": "visa"})
        r2 = clf2.predict({**comum, "gateway_error_code": "limit_exceeded",
                           "metodo_pagamento": "pix_automatico"})
        for r in (r1, r2):
            assert 0.0 <= r["p_recovery"] <= 1.0
        assert {e["feature"] for e in r1["shap_explanation"]["features"]} == set(classifier_module.ALL_FEATURES)
        assert {e["feature"] for e in r2["shap_explanation"]["features"]} == set(classifier_module.ALL_FEATURES_V2)

    def test_base_sem_as_colunas_da_lista_e_erro_claro(self, tmp_path, monkeypatch):
        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path)
        df = generate_dataset(n_samples=200, seed=42)
        with pytest.raises(ValueError, match="metodo_pagamento"):
            FailureClassifier().train(dados=df, features=classifier_module.ALL_FEATURES_V2)


# ══════════════════════════════════════════════════════════════════════════
# LIMIAR DO AUTOENCODER (B.4) — a constante é o que o critério escolhe na curva
# ══════════════════════════════════════════════════════════════════════════

class TestLimiarAnomaliaV2:

    def test_constante_v2_e_o_maior_percentil_com_recall_acima_do_piso(self):
        if not CURVA_MEDIDA.exists():
            pytest.skip(f"{CURVA_MEDIDA} ausente")
        curva = json.loads(CURVA_MEDIDA.read_text(encoding="utf-8"))["curva_limiar"]
        escolhido = anomaly_module.AnomalyDetector.escolher_percentil(
            curva, anomaly_module.RECALL_MINIMO_LIMIAR_V2)
        assert escolhido["percentil"] == anomaly_module.THRESHOLD_PERCENTIL_V2 == 83.0
        assert escolhido["recall"] >= anomaly_module.RECALL_MINIMO_LIMIAR_V2
        # p95 (a constante da v1) deixa dois terços dos anômalos passarem na v2.
        p95 = next(c for c in curva if c["percentil"] == anomaly_module.THRESHOLD_PERCENTIL_V1)
        assert p95["recall"] < 0.40

    def test_escolher_percentil_sem_ponto_acima_do_piso_devolve_o_de_maior_recall(self):
        curva = [{"percentil": 80.0, "recall": 0.5, "precision": 0.6, "f1": 0.55},
                 {"percentil": 90.0, "recall": 0.3, "precision": 0.8, "f1": 0.44}]
        assert anomaly_module.AnomalyDetector.escolher_percentil(curva, 0.70)["percentil"] == 80.0
        assert anomaly_module.AnomalyDetector.escolher_percentil(curva, 0.30)["percentil"] == 90.0

"""tests/test_experimentos_voluntario_v3.py — Bloco 4 do treino v3.

As bases variantes e o treino com o vetor de produção são gerados em
`tmp_path`; as de verdade são geradas pelo operador. `data/v2`, `data/v3` e
`models/v3` são só lidos, e o teste confere que não mudaram.
"""

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crai.churn_voluntary import risk_scorer as rs
from crai.ml import voluntario_v3 as V3
from crai.scripts import experimentos_voluntario_v3 as E
from crai.scripts import gerar_base_v3_voluntario as G
from crai.scripts import treinar_voluntario_v3 as T

APP = Path(__file__).resolve().parents[1]
PASTA_V2 = APP / "data" / "v2"
HIPER_RAPIDO = {**T.HIPERPARAMETROS, "max_iter": 20}

pytestmark = pytest.mark.skipif(not (PASTA_V2 / "MANIFESTO.json").exists(),
                                reason="base v2 ausente")

ORIGINAIS = {"INTERACAO_PRECO_SAUDE": V3.INTERACAO_PRECO_SAUDE, "B0": V3.B0,
             "_tipos_de_evento": V3._tipos_de_evento}


def _sha_pasta(pasta: Path) -> dict:
    if not pasta.exists():
        return {}
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(pasta.iterdir()) if p.is_file()}


@pytest.fixture(scope="module")
def protegidas_antes():
    return {p: _sha_pasta(APP / p) for p in ("data/v2", "data/v3", "models/v3")}


@pytest.fixture(scope="module")
def bases(tmp_path_factory, protegidas_antes):
    saidas = {}
    destino = tmp_path_factory.mktemp("v3")
    assert G.main(["--seed", "42", "--out", str(destino), "--v2", str(PASTA_V2)]) == 0
    saidas["v3"] = destino
    for v in E.VARIANTES:
        destino = tmp_path_factory.mktemp(v)
        assert E.main(["gerar", "--variante", v, "--seed", "42", "--out", str(destino),
                       "--v2", str(PASTA_V2)]) == 0
        saidas[v] = destino
    return saidas


def _ler(pasta):
    return pd.read_parquet(pasta / "voluntario_v3.parquet")


def _man(pasta, nome="MANIFESTO_v3.json"):
    return json.loads((pasta / nome).read_text(encoding="utf-8"))


COMPORTAMENTO = [*V3.FEATURES_DE_RISCO_V3, *V3.COLUNAS_AUXILIARES, "event", "risk_regra"]
ROTULO = ["churn", "oculto_p_churn", "oculto_rotulo_sorteado"]


class TestSemInteracao:
    def test_muda_so_o_rotulo(self, bases):
        v3, var = _ler(bases["v3"]), _ler(bases["sem_interacao"])
        pd.testing.assert_frame_equal(v3[COMPORTAMENTO], var[COMPORTAMENTO])
        assert not np.allclose(v3["oculto_p_churn"], var["oculto_p_churn"])

    def test_manifesto_e_variante_declaram_interacao_zero_e_b0_novo(self, bases):
        f = _man(bases["sem_interacao"])["formula"]
        d = _man(bases["sem_interacao"], "VARIANTE.json")
        assert f["interacao_preco_saude"] == 0.0
        assert f["B0"] == d["B0"]["variante"] != V3.B0

    def test_b0_resolvido_da_a_media_alvo(self):
        pop = pd.read_parquet(PASTA_V2 / "populacao.parquet")
        with E._variante(INTERACAO_PRECO_SAUDE=0.0, B0=E.b0_para_taxa(pop, 0.0)):
            assert abs(V3.p_verdadeira(pop).mean() - V3.TAXA_ALVO_P_VERDADEIRA) < 1e-3

    def test_taxa_na_faixa(self, bases):
        lo, hi = V3.FAIXA_TAXA_CHURN
        assert lo <= _ler(bases["sem_interacao"])["churn"].mean() <= hi


class TestIntencaoForte:
    def test_rotulo_identico_ao_da_v3(self, bases):
        v3, var = _ler(bases["v3"]), _ler(bases["intencao_forte"])
        pd.testing.assert_frame_equal(v3[ROTULO], var[ROTULO])

    def test_so_eventos_mudam_nas_features(self, bases):
        v3, var = _ler(bases["v3"]), _ler(bases["intencao_forte"])
        iguais = [c for c in COMPORTAMENTO if c not in
                  ("event", "risk_regra", "evento_cancelamento", "evento_downgrade",
                   "evento_sessao")]
        pd.testing.assert_frame_equal(v3[iguais], var[iguais])
        assert (v3["event"] != var["event"]).any()

    def test_cancelamento_carrega_mais_churn_e_mantem_o_mix(self, bases):
        v3, var = _ler(bases["v3"]), _ler(bases["intencao_forte"])
        taxa = lambda d: d.loc[d["evento_cancelamento"] == 1, "churn"].mean()  # noqa: E731
        assert taxa(var) > taxa(v3)
        assert 0.045 <= var["evento_cancelamento"].mean() <= 0.055

    def test_teto_de_spearman_respeitado(self, bases):
        v = _man(bases["intencao_forte"])["verificacoes"]
        assert v["maior_abs_spearman_feature"]["valor"] <= V3.TETO_SPEARMAN


class TestNadaVazaNemMuda:
    def test_modulo_restaurado_depois_das_variantes(self, bases):
        for nome, valor in ORIGINAIS.items():
            assert getattr(V3, nome) is valor or getattr(V3, nome) == valor, nome

    def test_restaura_mesmo_com_erro(self):
        with pytest.raises(RuntimeError):
            with E._variante(B0=0.0):
                raise RuntimeError("falha no meio")
        assert V3.B0 == ORIGINAIS["B0"]

    @pytest.mark.parametrize("argv", [
        ["gerar", "--variante", "sem_interacao", "--out", "data/v3/"],
        ["gerar", "--variante", "intencao_forte", "--out", "data/v2/"],
        ["treinar-vetor-producao", "--out", "models/v3/"],
        ["treinar-vetor-producao", "--out", "models/"],
    ])
    def test_pastas_protegidas_recusadas(self, argv, monkeypatch):
        monkeypatch.chdir(APP)
        with pytest.raises(SystemExit, match="protegida"):
            E.main(argv)

    def test_v2_v3_e_models_v3_nao_mudaram(self, bases, vetor_producao, protegidas_antes):
        for p, antes in protegidas_antes.items():
            assert _sha_pasta(APP / p) == antes, p


@pytest.fixture(scope="module")
def vetor_producao(bases, tmp_path_factory):
    out = tmp_path_factory.mktemp("vetor_producao")
    m = E.treinar_vetor_producao(bases["v3"], out, amostra_clientes=3000, hiper=HIPER_RAPIDO,
                                 com_ablacao=False, com_shap=False)
    return out, m


class TestVetorDeProducao:
    def test_treina_so_com_features_de_risco(self, vetor_producao):
        out, m = vetor_producao
        meta = json.loads((out / f"{T.NOME}_meta.json").read_text(encoding="utf-8"))
        assert m["features"] == meta["features"] == rs.FEATURES_DE_RISCO

    def test_nao_grava_com_o_nome_que_a_producao_carrega(self, vetor_producao):
        out, _ = vetor_producao
        assert not (out / rs.MODELO_PATH.name).exists()
        assert (out / f"{T.NOME}.joblib").exists()

    def test_cenarios_mascaram_so_o_que_existe(self, vetor_producao):
        _, m = vetor_producao
        assert m["cenarios"]["c"]["linhas_regua_nao_pontuadas_dado_insuficiente"] == \
            m["cenarios"]["c"]["linhas_holdout"]
        assert m["cenarios"]["a"]["auc_candidato"] is not None

"""tests/test_base_v3_voluntario.py — o gerador da base v3 do risco voluntário.

Treino v3, Bloco 1 (29/09/2026). A base v3 real (`app/data/v3/`) é gerada só
pelo comando que o operador roda no terminal; aqui toda escrita vai para
`tmp_path`. A v2 em `app/data/v2/` é só LIDA — e a catraca da raiz
(`conftest.py`) falha a sessão se alguém a tocar.
"""

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from crai.ml import voluntario_v3 as V3
from crai.ml.populacao import LATENTES
from crai.scripts import gerar_base_v3_voluntario as G

PASTA_V2 = Path(__file__).resolve().parents[1] / "data" / "v2"
QUATRO_V2 = ["populacao", "classificador", "comportamental", "liquidez"]

pytestmark = pytest.mark.skipif(not (PASTA_V2 / "MANIFESTO.json").exists(),
                                reason="base v2 ausente")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def geracoes(tmp_path_factory):
    """Duas gerações com a mesma semente, em pastas diferentes."""
    saidas = []
    for nome in ("a", "b"):
        destino = tmp_path_factory.mktemp(f"v3_{nome}")
        assert G.main(["--seed", "42", "--out", str(destino), "--v2", str(PASTA_V2)]) == 0
        saidas.append(destino)
    return saidas


@pytest.fixture(scope="module")
def base(geracoes):
    return pd.read_parquet(geracoes[0] / "voluntario_v3.parquet")


@pytest.fixture(scope="module")
def manifesto(geracoes):
    return json.loads((geracoes[0] / "MANIFESTO_v3.json").read_text(encoding="utf-8"))


class TestLatentesForaDasFeatures:
    def test_nenhuma_latente_entre_as_colunas_do_parquet(self, base):
        assert not set(LATENTES) & set(base.columns)

    def test_nenhuma_latente_nem_oculto_na_lista_de_features(self):
        assert not set(LATENTES) & set(V3.FEATURES_DE_RISCO_V3)
        assert not [f for f in V3.FEATURES_DE_RISCO_V3 if f.startswith(V3.PREFIXO_OCULTO)]

    def test_o_que_o_treino_nao_ve_tem_prefixo_oculto(self, base):
        fora = set(base.columns) - set(V3.FEATURES_DE_RISCO_V3) - set(V3.COLUNAS_AUXILIARES) \
            - {"customer_id", "event", "risk_regra", "churn"}
        assert fora == set(V3.COLUNAS_OCULTAS)
        assert all(c.startswith(V3.PREFIXO_OCULTO) for c in fora)

    @pytest.mark.parametrize("intrusa", ["oculto_p_churn", "satisfacao"])
    def test_guarda_da_matriz_recusa_oculto_e_latente(self, intrusa):
        with pytest.raises(ValueError, match="proibidas"):
            V3.conferir_features([*V3.FEATURES_DE_RISCO_V3, intrusa])

    def test_colunas_e_ordem_do_contrato(self, base):
        assert list(base.columns) == V3.COLUNAS_V3
        assert len(V3.FEATURES_DE_RISCO_V3) == 15


class TestDeterminismo:
    def test_duas_geracoes_mesma_semente_mesmo_sha256(self, geracoes):
        a, b = (_sha(p / "voluntario_v3.parquet") for p in geracoes)
        assert a == b

    def test_sha256_e_hash_canonico_do_manifesto_batem_com_o_arquivo(self, geracoes, base,
                                                                     manifesto):
        from crai.scripts.gerar_bases_v2 import hash_canonico
        meta = manifesto["arquivos"]["voluntario_v3"]
        assert meta["sha256"] == _sha(geracoes[0] / "voluntario_v3.parquet")
        assert meta["hash_canonico"] == hash_canonico(base)
        assert meta["linhas"] == len(base)

    def test_rotulo_nao_usa_o_fluxo_cru_da_populacao(self):
        # Bloco 0, M2b: default_rng(seed) cru repete os sorteios da população.
        import numpy as np
        cru = np.random.default_rng(42).normal(size=5)
        derivado = V3.fluxo(42, V3.FLUXO_ROTULO).normal(size=5)
        assert not np.allclose(cru, derivado)


class TestV2Intacta:
    def test_os_quatro_parquets_v2_iguais_ao_manifesto(self, geracoes):
        declarado = json.loads((PASTA_V2 / "MANIFESTO.json").read_text(encoding="utf-8"))
        for nome in QUATRO_V2:
            assert _sha(PASTA_V2 / f"{nome}.parquet") == declarado["arquivos"][nome]["sha256"], nome

    def test_manifesto_v3_registra_v2_igual_antes_e_depois(self, manifesto):
        assert manifesto["entradas_v2"]["igual_ao_manifesto_v2"] is True

    def test_out_na_pasta_da_v2_e_recusado(self):
        with pytest.raises(SystemExit, match="v2 nao e regravada"):
            G.main(["--out", str(PASTA_V2), "--v2", str(PASTA_V2)])


class TestFaixasAprovadas:
    def test_taxa_de_churn_na_faixa(self, base):
        lo, hi = V3.FAIXA_TAXA_CHURN
        assert lo <= base["churn"].mean() <= hi

    def test_rastros_na_faixa_de_spearman(self, manifesto):
        lo, hi = V3.FAIXA_SPEARMAN_RASTRO
        for col, r in manifesto["verificacoes"]["rastros_com_faixa"].items():
            assert lo <= r["abs_spearman"] <= hi, (col, r)

    def test_nenhuma_feature_passa_do_teto_de_spearman(self, manifesto):
        assert manifesto["verificacoes"]["maior_abs_spearman_feature"]["valor"] <= V3.TETO_SPEARMAN

    def test_esqueleto_de_eventos_e_o_da_v2(self, base):
        esq = pd.read_parquet(PASTA_V2 / "voluntario.parquet", columns=["customer_id"])
        assert base["customer_id"].tolist() == esq["customer_id"].tolist()

    def test_rotulo_e_do_cliente(self, base):
        assert (base.groupby("customer_id")["churn"].nunique() == 1).all()

    def test_sem_nulos(self, base):
        assert int(base.isna().sum().sum()) == 0

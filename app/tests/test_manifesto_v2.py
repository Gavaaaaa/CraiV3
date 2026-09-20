"""tests/test_manifesto_v2.py — a base v2 é reproduzível por semente e o manifesto a descreve.

Bloco B (20/09/2026). `crai.scripts.gerar_bases_v2` gera a população
compartilhada e as quatro visões, e grava MANIFESTO.json. Aqui:

  - gerar duas vezes com a mesma semente → os mesmos sha256 nos cinco
    arquivos (e semente diferente → sha256 diferentes);
  - o manifesto tem, por arquivo, semente, linhas, colunas, lista de colunas
    e sha256, e no topo as versões de biblioteca;
  - o leitor compartilhado (`gerar_bases.ler_bases`, o mesmo do v1) lê a
    pasta v2 com a conferência de sha256 e entende o formato do manifesto v2.

Tamanhos pequenos: a base do pacote (120.000 clientes) é a de `data/v2/`; o
que se testa aqui é o mecanismo, com a mesma função e os mesmos parâmetros.
"""

import json

import pytest

from crai.scripts import gerar_bases as gb
from crai.scripts import gerar_bases_v2

PEQUENO = ["--clientes", "2000", "--cobrancas", "2000", "--eventos", "2000",
           "--clientes-liquidez", "12", "--dias", "60"]
CINCO = ["populacao", "classificador", "comportamental", "liquidez", "voluntario"]


def _gerar(pasta, seed: int = 42) -> dict:
    assert gerar_bases_v2.main(["--seed", str(seed), "--out", str(pasta), *PEQUENO]) == 0
    return json.loads((pasta / "MANIFESTO.json").read_text(encoding="utf-8"))


class TestManifestoV2:

    def test_mesma_semente_mesmos_sha256_nos_cinco_arquivos(self, tmp_path):
        m1 = _gerar(tmp_path / "um")
        m2 = _gerar(tmp_path / "dois")
        for nome in CINCO:
            assert m1["arquivos"][nome]["sha256"] == m2["arquivos"][nome]["sha256"], nome
            assert gb.sha256_arquivo(tmp_path / "um" / f"{nome}.parquet") == \
                m1["arquivos"][nome]["sha256"]

    def test_semente_diferente_sha256_diferentes(self, tmp_path):
        m1 = _gerar(tmp_path / "um", seed=42)
        m2 = _gerar(tmp_path / "dois", seed=7)
        for nome in CINCO:
            assert m1["arquivos"][nome]["sha256"] != m2["arquivos"][nome]["sha256"], nome

    def test_manifesto_descreve_cada_arquivo_e_as_versoes(self, tmp_path):
        m = _gerar(tmp_path)
        assert m["semente"] == 42
        assert m["end_date"] == "2026-09-14"
        assert m["fonte_de_parametros"] == "sintetico_calibrado"
        for lib in ("python", "numpy", "pandas", "scikit-learn", "xgboost", "torch", "pyarrow"):
            assert lib in m["versoes"], lib
        assert set(m["arquivos"]) == set(CINCO)
        for nome, info in m["arquivos"].items():
            for chave in ("arquivo", "linhas", "colunas", "lista_de_colunas", "sha256", "bytes"):
                assert chave in info, f"{nome} sem {chave}"
            assert info["arquivo"] == f"{nome}.parquet"
            assert info["colunas"] == len(info["lista_de_colunas"])
            assert len(info["sha256"]) == 64
            assert (tmp_path / info["arquivo"]).stat().st_size == info["bytes"]
        # A semente é do manifesto inteiro (uma só para as cinco tabelas).
        assert m["arquivos"]["liquidez"]["linhas"] == 12 * 60
        assert m["verificacoes"]["todos_os_ids_existem_na_populacao"] is True
        assert m["verificacoes"]["card_brand_presente"] is False

    def test_leitor_compartilhado_le_a_pasta_v2_com_sha256(self, tmp_path):
        m = _gerar(tmp_path)
        assert gb.ler_manifesto(tmp_path, gb.ARQUIVOS_V2) is not None
        assert gb.fonte_do_manifesto(m) == "sintetico_calibrado"
        bases, manifesto = gb.ler_bases(tmp_path, conferir_sha256=True, arquivos=gb.ARQUIVOS_V2)
        assert set(bases) == set(CINCO)
        for nome in CINCO:
            entrada = gb.entrada_do_manifesto(manifesto, nome, f"{nome}.parquet")
            assert len(bases[nome]) == entrada["linhas"]
            assert list(bases[nome].columns) == entrada["lista_colunas"]

    def test_arquivo_alterado_reprova_no_leitor(self, tmp_path):
        _gerar(tmp_path)
        alvo = tmp_path / "populacao.parquet"
        with open(alvo, "r+b") as f:
            f.seek(300)
            byte = f.read(1)
            f.seek(300)
            f.write(bytes([byte[0] ^ 0xFF]))
        with pytest.raises(ValueError, match="populacao.parquet"):
            gb.ler_bases(tmp_path, arquivos=gb.ARQUIVOS_V2)

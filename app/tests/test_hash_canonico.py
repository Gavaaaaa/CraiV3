"""tests/test_hash_canonico.py — o hash canônico responde "essa base é aquela base?" em qualquer pandas.

O PROBLEMA (Bloco I, 22/09/2026). O `MANIFESTO.json` declara o sha256 de cada
parquet. Regenerar a base v2 com a mesma semente em pandas 2.2.3 produz as
mesmas tabelas linha a linha, mas outros bytes de parquet — largura default de
inteiro, unidade do datetime, metadados do pandas dentro do arquivo — e
portanto outros sha256. O sha256 do arquivo responde "é o mesmo arquivo?"; a
pergunta que uma banca faz é "é a mesma base?".

`hash_canonico` é o sha256 do CONTEÚDO (CSV canônico, float em `%.6f`). Estes
testes provam, simulando a diferença de dtype em vez de esperar dois pandas
instalados, que:

  - a mesma tabela com `int32`/`datetime64[ns]` e com `int64`/`datetime64[us]`
    dá sha256 de ARQUIVO diferentes e hash canônico IGUAL;
  - um valor diferente (na sexta casa de um float, ou um inteiro) muda o hash;
  - `_fixar_dtypes` normaliza os dtypes, e o gerador grava o campo no manifesto.
"""

import json

import numpy as np
import pandas as pd
import pytest

from crai.scripts import gerar_bases as gb
from crai.scripts import gerar_bases_v2 as g2

PEQUENO = ["--clientes", "1500", "--cobrancas", "1500", "--eventos", "1500",
           "--clientes-liquidez", "8", "--dias", "45"]
CINCO = ["populacao", "classificador", "comportamental", "liquidez", "voluntario"]


def _tabela() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    n = 500
    return pd.DataFrame({
        "customer_id": [f"c{i:05d}" for i in range(n)],
        "inteiro": rng.integers(0, 1000, size=n).astype("int64"),
        "valor": np.round(rng.normal(1800, 300, size=n), 2),
        # como o gerador grava desde 22/09 (`_fixar_dtypes`): datetime64[us]
        "data": (pd.to_datetime("2026-09-14")
                 - pd.to_timedelta(rng.integers(0, 180, size=n), unit="D")).astype("datetime64[us]"),
        "flag": rng.integers(0, 2, size=n).astype(bool),
        "texto": rng.choice(["pix_automatico", "boleto"], size=n),
    })


def _outro_ambiente(df: pd.DataFrame) -> pd.DataFrame:
    """A mesma tabela como outro numpy/pandas a gravaria: int32 e datetime64[ns]."""
    out = df.copy()
    out["inteiro"] = out["inteiro"].astype("int32")
    out["data"] = out["data"].astype("datetime64[ns]")
    return out


class TestHashCanonico:

    def test_dtypes_diferentes_sha256_de_arquivo_diferente_hash_canonico_igual(self, tmp_path):
        a = _tabela()
        b = _outro_ambiente(a)
        assert str(a["inteiro"].dtype) != str(b["inteiro"].dtype)
        assert str(a["data"].dtype) != str(b["data"].dtype)

        a.to_parquet(tmp_path / "a.parquet", index=False)
        b.to_parquet(tmp_path / "b.parquet", index=False)
        assert gb.sha256_arquivo(tmp_path / "a.parquet") != gb.sha256_arquivo(tmp_path / "b.parquet"), (
            "a simulação não reproduziu o problema: os parquets deveriam ter bytes diferentes")
        assert g2.hash_canonico(a) == g2.hash_canonico(b)

    def test_fixar_dtypes_torna_os_parquets_identicos_de_novo(self, tmp_path):
        a, b = _tabela(), _outro_ambiente(_tabela())
        fa, fb = g2._fixar_dtypes(a), g2._fixar_dtypes(b)
        assert str(fa["inteiro"].dtype) == str(fb["inteiro"].dtype) == "int64"
        assert str(fa["data"].dtype) == str(fb["data"].dtype) == "datetime64[us]"
        assert str(fa["flag"].dtype) == "bool" and str(fa["valor"].dtype) == "float64"
        fa.to_parquet(tmp_path / "a.parquet", index=False)
        fb.to_parquet(tmp_path / "b.parquet", index=False)
        assert gb.sha256_arquivo(tmp_path / "a.parquet") == gb.sha256_arquivo(tmp_path / "b.parquet")

    def test_conteudo_diferente_muda_o_hash(self):
        a = _tabela()
        b = a.copy(); b.loc[7, "inteiro"] += 1
        assert g2.hash_canonico(a) != g2.hash_canonico(b)
        c = a.copy(); c.loc[7, "valor"] += 1e-6          # sexta casa: muda
        assert g2.hash_canonico(a) != g2.hash_canonico(c)
        d = a.copy(); d.loc[7, "valor"] += 1e-9          # abaixo do %.6f: não muda
        assert g2.hash_canonico(a) == g2.hash_canonico(d)
        e = a[[*reversed(a.columns)]]                     # ordem de colunas faz parte do conteúdo
        assert g2.hash_canonico(a) != g2.hash_canonico(e)

    def test_o_hash_e_o_mesmo_em_blocos_e_de_uma_vez(self, monkeypatch):
        a = _tabela()
        inteiro = g2.hash_canonico(a)
        monkeypatch.setattr(g2, "_BLOCO_CANONICO", 64)
        assert g2.hash_canonico(a) == inteiro

    def test_o_gerador_grava_o_hash_canonico_e_os_dtypes_fixos(self, tmp_path):
        assert g2.main(["--seed", "42", "--out", str(tmp_path), *PEQUENO]) == 0
        m = json.loads((tmp_path / "MANIFESTO.json").read_text(encoding="utf-8"))
        assert "hash_canonico_metodo" in m
        for nome in CINCO:
            info = m["arquivos"][nome]
            assert len(info["hash_canonico"]) == 64, nome
            assert info["hash_canonico"] != info["sha256"]
            df = pd.read_parquet(tmp_path / info["arquivo"])
            assert g2.hash_canonico(df) == info["hash_canonico"], nome
            for col, dt in info["dtypes"].items():
                assert not dt.startswith("int") or dt == "int64", (nome, col, dt)
                assert not dt.startswith("datetime") or dt == "datetime64[us]", (nome, col, dt)
        # E a mesma base, regravada como outro ambiente gravaria, tem o mesmo hash canônico.
        for nome in CINCO:
            df = pd.read_parquet(tmp_path / f"{nome}.parquet")
            outro = df.copy()
            for col in outro.columns:
                if pd.api.types.is_integer_dtype(outro[col].dtype):
                    outro[col] = outro[col].astype("int32")
                elif pd.api.types.is_datetime64_any_dtype(outro[col].dtype):
                    outro[col] = outro[col].astype("datetime64[ns]")
            assert g2.hash_canonico(outro) == m["arquivos"][nome]["hash_canonico"], nome

    def test_bases_anteriores_nao_tem_o_campo_e_o_leitor_nao_o_exige(self, tmp_path):
        """A base de produção (20/09/2026) não tem `hash_canonico`; `train_all`
        continua lendo-a pelo sha256, e `hash_canonico_de_pasta` calcula o
        campo para ela."""
        assert g2.main(["--seed", "42", "--out", str(tmp_path), *PEQUENO]) == 0
        caminho = tmp_path / "MANIFESTO.json"
        m = json.loads(caminho.read_text(encoding="utf-8"))
        for nome in CINCO:
            m["arquivos"][nome].pop("hash_canonico")
        m.pop("hash_canonico_metodo")
        caminho.write_text(json.dumps(m), encoding="utf-8")
        bases, manifesto = gb.ler_bases(tmp_path, conferir_sha256=True, arquivos=gb.ARQUIVOS_V2)
        assert set(bases) == set(CINCO)
        calculado = g2.hash_canonico_de_pasta(tmp_path)
        assert set(calculado) == set(CINCO)
        assert all(len(h) == 64 for h in calculado.values())

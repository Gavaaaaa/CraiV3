"""tests/test_gerar_bases.py — as bases de treino persistidas são reproduzíveis por semente.

Até 19/09/2026 o `train_all` gerava os quatro datasets em memória e os
descartava: não havia o que auditar, e a base do Módulo 3 ainda dependia do
dia em que rodou (`end_date=date.today()`). Este arquivo trava o que mudou:

  (a) `generate_liquidity_series` exige `end_date`; o fim canônico de uma
      semente é `end_date_por_semente(seed)` (42 → 2026-09-14, o fim da base
      do treino de 14/09/2026). Mesma semente + mesmo end_date → série idêntica.
  (b) `crai.scripts.gerar_bases` grava os quatro parquet + MANIFESTO.json, e
      duas gerações com a mesma semente dão sha256 idênticos, arquivo a
      arquivo. O manifesto traz o que a auditoria precisa (semente, linhas,
      colunas, lista de colunas, sha256, versões das bibliotecas).
  (c) O parquet lido de volta é o MESMO DataFrame que o gerador devolve em
      memória — persistir não muda nenhum número.
  (d) `ler_bases` reprova uma base alterada depois de gerada (sha256).
  (e) `train_all.resolver_bases` lê a base se ela existe (e só se a fonte
      bate e os tamanhos explícitos batem), e cai para memória se não existe.
  (f) Treinar com `dados=` (o parquet) dá as mesmas métricas que treinar com o
      gerador em memória — o caminho novo não altera o resultado.
"""

import json
from argparse import Namespace
from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from crai.ml import failure_classifier as classifier_module
from crai.ml import voluntary_risk as voluntary_module
from crai.ml.failure_classifier import FailureClassifier
from crai.ml.synthetic_data import (
    ANCORA_END_DATE,
    end_date_por_semente,
    generate_dataset,
    generate_liquidity_series,
    generate_voluntary_dataset,
)
from crai.ml.voluntary_risk import VoluntaryRiskModel
from crai.scripts import gerar_bases as gb
from crai.scripts import train_all

PEQUENO = dict(classifier_samples=400, anomaly_samples=300, payday_customers=8,
               payday_days=60, voluntario_samples=200)


def _gerar_em(pasta: Path, seed: int = 42, fonte: str = "sintetico", **tamanhos) -> dict:
    bases = gb.gerar_bases(seed=seed, fonte=fonte, **{**PEQUENO, **tamanhos})
    return gb.escrever_bases(bases, pasta, seed, fonte)


# ══════════════════════════════════════════════════════════════════════════
# (a) end_date obrigatório e derivado da semente
# ══════════════════════════════════════════════════════════════════════════

class TestEndDate:

    def test_end_date_por_semente_42_e_a_base_de_14_09(self):
        assert end_date_por_semente(42) == pd.Timestamp("2026-09-14")
        assert ANCORA_END_DATE == pd.Timestamp("2026-08-03")

    def test_end_date_por_semente_e_deterministico_e_varia_com_a_semente(self):
        assert end_date_por_semente(7) == end_date_por_semente(7)
        assert end_date_por_semente(7) != end_date_por_semente(8)
        # Nunca depende do relógio: é âncora + (seed mod 366) dias.
        assert end_date_por_semente(0) == ANCORA_END_DATE
        assert end_date_por_semente(366) == ANCORA_END_DATE

    def test_generate_liquidity_exige_end_date(self):
        with pytest.raises(TypeError):
            generate_liquidity_series(5, 40, seed=42)
        with pytest.raises(ValueError, match="end_date"):
            generate_liquidity_series(5, 40, seed=42, end_date=None)

    def test_mesma_semente_mesmo_end_date_serie_identica(self):
        a = generate_liquidity_series(10, 60, seed=42, end_date="2026-09-14")
        b = generate_liquidity_series(10, 60, seed=42, end_date=end_date_por_semente(42))
        assert_frame_equal(a, b)
        assert a["date"].max() == pd.Timestamp("2026-09-14")

    def test_end_date_diferente_serie_diferente(self):
        """É por isso que `date.today()` era um problema: os dias úteis mudam."""
        a = generate_liquidity_series(10, 60, seed=42, end_date="2026-09-14")
        b = generate_liquidity_series(10, 60, seed=42, end_date="2026-09-15")
        assert not a.drop(columns="date").equals(b.drop(columns="date"))


# ══════════════════════════════════════════════════════════════════════════
# (b) gerar_bases: sha256 idênticos e manifesto completo
# ══════════════════════════════════════════════════════════════════════════

class TestGerarBases:

    def test_duas_geracoes_mesma_semente_sha256_identicos(self, tmp_path):
        m1 = _gerar_em(tmp_path / "um")
        m2 = _gerar_em(tmp_path / "dois")
        for arquivo in gb.ARQUIVOS.values():
            assert m1["arquivos"][arquivo]["sha256"] == m2["arquivos"][arquivo]["sha256"], arquivo
            assert gb.sha256_arquivo(tmp_path / "um" / arquivo) == m1["arquivos"][arquivo]["sha256"]
        # O manifesto também: nada de relógio dentro dele.
        assert (tmp_path / "um" / gb.MANIFESTO).read_bytes() == \
            (tmp_path / "dois" / gb.MANIFESTO).read_bytes()

    def test_semente_diferente_sha256_diferente(self, tmp_path):
        m1 = _gerar_em(tmp_path / "um", seed=42)
        m2 = _gerar_em(tmp_path / "dois", seed=43)
        for arquivo in gb.ARQUIVOS.values():
            assert m1["arquivos"][arquivo]["sha256"] != m2["arquivos"][arquivo]["sha256"], arquivo

    def test_manifesto_traz_o_que_a_auditoria_precisa(self, tmp_path):
        m = _gerar_em(tmp_path, seed=42)
        assert m["semente"] == 42
        assert m["fonte"] == "sintetico"
        assert m["end_date_liquidez"] == "2026-09-14"
        for lib in ("numpy", "pandas", "scikit-learn", "xgboost", "torch"):
            assert lib in m["versoes"], lib
        assert set(m["arquivos"]) == set(gb.ARQUIVOS.values())
        for arquivo, info in m["arquivos"].items():
            for chave in ("semente", "linhas", "colunas", "lista_colunas", "sha256", "gerador"):
                assert chave in info, f"{arquivo} sem {chave}"
            assert info["colunas"] == len(info["lista_colunas"])
            assert len(info["sha256"]) == 64
        assert m["arquivos"]["classificador.parquet"]["linhas"] == PEQUENO["classifier_samples"]
        assert m["arquivos"]["liquidez.parquet"]["linhas"] == \
            PEQUENO["payday_customers"] * PEQUENO["payday_days"]
        # O que está em disco é o que o manifesto diz.
        em_disco = json.loads((tmp_path / gb.MANIFESTO).read_text(encoding="utf-8"))
        assert em_disco == m

    def test_cli_escreve_os_cinco_arquivos(self, tmp_path):
        rc = gb.main(["--seed", "42", "--out", str(tmp_path),
                      "--classifier-samples", "100", "--anomaly-samples", "100",
                      "--payday-customers", "3", "--payday-days", "50",
                      "--voluntario-samples", "50"])
        assert rc == 0
        for arquivo in list(gb.ARQUIVOS.values()) + [gb.MANIFESTO]:
            assert (tmp_path / arquivo).exists(), arquivo


# ══════════════════════════════════════════════════════════════════════════
# (c) persistir não muda nenhum número; (d) alteração é detectada
# ══════════════════════════════════════════════════════════════════════════

class TestLerBases:

    def test_parquet_lido_e_identico_ao_gerador_em_memoria(self, tmp_path):
        _gerar_em(tmp_path, seed=42)
        bases, manifesto = gb.ler_bases(tmp_path)
        memoria = gb.gerar_bases(seed=42, **PEQUENO)
        for nome in gb.ARQUIVOS:
            assert_frame_equal(bases[nome], memoria[nome][0])
        assert manifesto["semente"] == 42

    def test_base_alterada_depois_de_gerada_reprova(self, tmp_path):
        _gerar_em(tmp_path, seed=42)
        alvo = tmp_path / gb.ARQUIVOS["voluntario"]
        with open(alvo, "r+b") as f:
            f.seek(100)
            byte = f.read(1)
            f.seek(100)
            f.write(bytes([byte[0] ^ 0xFF]))
        with pytest.raises(ValueError, match="sha256"):
            gb.ler_bases(tmp_path)
        # Sem conferência, lê (é o que o caminho de emergência permite).
        assert gb.ler_manifesto(tmp_path) is not None

    def test_pasta_incompleta_nao_e_base(self, tmp_path):
        assert gb.ler_manifesto(tmp_path) is None
        _gerar_em(tmp_path, seed=42)
        (tmp_path / gb.ARQUIVOS["liquidez"]).unlink()
        assert gb.ler_manifesto(tmp_path) is None
        with pytest.raises(FileNotFoundError):
            gb.ler_bases(tmp_path)


# ══════════════════════════════════════════════════════════════════════════
# (e) train_all decide de onde vem o dado
# ══════════════════════════════════════════════════════════════════════════

def _args(**kw) -> Namespace:
    base = dict(base=None, dados=None, sem_dados=False, quick=False, fonte="sintetico",
                classifier_samples=None, anomaly_samples=None,
                payday_customers=None, voluntario_samples=None)
    return Namespace(**{**base, **kw})


class TestResolverBases:

    def test_sem_base_em_disco_gera_em_memoria_com_os_defaults(self, tmp_path):
        args = _args(dados=str(tmp_path / "nao_existe"))
        bases, desc = train_all.resolver_bases(args)
        assert bases is None
        assert "memoria" in desc
        assert args.base == "v1"
        assert (args.classifier_samples, args.anomaly_samples,
                args.payday_customers, args.voluntario_samples) == (3000, 5500, 600, 2000)
        assert args.bases_info["origem"] == "gerador_em_memoria"

    def test_com_base_em_disco_le_e_usa_os_tamanhos_do_manifesto(self, tmp_path):
        _gerar_em(tmp_path, seed=42)
        args = _args(dados=str(tmp_path))
        bases, desc = train_all.resolver_bases(args)
        assert set(bases) == set(gb.ARQUIVOS)
        assert "lidas de" in desc
        assert args.classifier_samples == PEQUENO["classifier_samples"]
        assert args.anomaly_samples == PEQUENO["anomaly_samples"]
        assert args.payday_customers == PEQUENO["payday_customers"]
        assert args.voluntario_samples == PEQUENO["voluntario_samples"]
        assert args.bases_info["origem"] == "arquivo"
        assert set(args.bases_info["sha256"]) == set(gb.ARQUIVOS.values())

    def test_sem_dados_e_quick_ignoram_a_base_em_disco(self, tmp_path):
        _gerar_em(tmp_path, seed=42)
        assert train_all.resolver_bases(_args(dados=str(tmp_path), sem_dados=True))[0] is None
        assert train_all.resolver_bases(_args(dados=str(tmp_path), quick=True))[0] is None

    def test_fonte_diferente_da_base_e_erro(self, tmp_path):
        _gerar_em(tmp_path, seed=42, fonte="sintetico")
        with pytest.raises(SystemExit, match="fonte"):
            train_all.resolver_bases(_args(dados=str(tmp_path), fonte="sintetico_calibrado"))

    def test_tamanho_explicito_diferente_da_base_e_erro(self, tmp_path):
        _gerar_em(tmp_path, seed=42)
        with pytest.raises(SystemExit, match="classifier-samples"):
            train_all.resolver_bases(_args(dados=str(tmp_path), classifier_samples=999))
        # Tamanho explícito IGUAL ao da base passa; 0 no voluntário (desliga) também.
        args = _args(dados=str(tmp_path), classifier_samples=PEQUENO["classifier_samples"],
                     voluntario_samples=0)
        assert train_all.resolver_bases(args)[0] is not None
        assert args.voluntario_samples == 0


# ══════════════════════════════════════════════════════════════════════════
# (f) treinar do parquet dá o mesmo número que treinar do gerador
# ══════════════════════════════════════════════════════════════════════════

class TestTreinarComDados:
    """Todo treino aqui vai para `tmp_path` (MODELS_DIR monkeypatched): os
    binários reais de `models/` são o gate de honestidade do README e nenhum
    teste pode sobrescrevê-los (ver `tests/conftest.py`)."""

    def test_classifier_do_parquet_igual_ao_gerador(self, tmp_path, monkeypatch):
        monkeypatch.setattr(classifier_module, "MODELS_DIR", tmp_path / "modelos")
        df = generate_dataset(n_samples=600, seed=42)
        df.to_parquet(tmp_path / "c.parquet", index=False)
        a = FailureClassifier().train(n_samples=600)
        b = FailureClassifier().train(dados=pd.read_parquet(tmp_path / "c.parquet"))
        assert a["auc"] == b["auc"]
        assert a["accuracy"] == b["accuracy"]
        assert a["n_amostras"] == b["n_amostras"] == 600
        assert a["origem_dados"] == "gerador_em_memoria"
        assert b["origem_dados"] == "dataframe_fornecido"

    def test_voluntario_do_parquet_igual_ao_gerador(self, tmp_path, monkeypatch):
        monkeypatch.setattr(voluntary_module, "MODELS_DIR", tmp_path / "modelos")
        df = generate_voluntary_dataset(n_samples=500, seed=42)
        df.to_parquet(tmp_path / "v.parquet", index=False)
        a = VoluntaryRiskModel().train(n_samples=500)
        b = VoluntaryRiskModel().train(dados=pd.read_parquet(tmp_path / "v.parquet"))
        assert a["auc_vs_rotulo"] == b["auc_vs_rotulo"]
        assert a["brier"] == b["brier"]
        assert b["n_amostras"] == 500

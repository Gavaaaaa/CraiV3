"""tests/test_pacote_modelos.py — o pacote de modelos para download e a conferência dele.

`app/models/` não é versionada; o README manda baixar um zip. Estes testes travam:

  - `empacotar_modelos`: a lista do pacote é FECHADA (só o que os carregadores leem, mais a
    evidência de treino); o estado do bandit, os `.bak`, o candidato v2, as pastas `v2/`,
    `v3/` e de experimento NUNCA entram; os arquivos ficam na raiz do zip; o zip é
    reprodutível; recusa quando falta arquivo, quando o voluntário não é o v3 promovido e
    quando um arquivo de texto do pacote tem cara de dado pessoal;
  - o manifesto: nome, tamanho e sha256 de cada arquivo, o sha256 do zip, a data e o treino
    de cada modelo;
  - `verificar_modelos`: código 0 só se tudo bater; diz o que falta e o que está diferente;
    confere o zip baixado antes de extrair; não carrega modelo nenhum;
  - o manifesto versionado em `docs/modelos/` tem a forma certa e a lista do pacote.

Tudo em `tmp_path`, com arquivos de mentira: nenhum teste depende dos modelos da máquina
(a lição do D-B3-2), nem escreve em `app/models/` ou em `docs/`.
"""

import ast
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from crai.scripts import empacotar_modelos as E
from crai.scripts import verificar_modelos as V

RAIZ = Path(__file__).resolve().parents[2]
MANIFESTO_VERSIONADO = RAIZ / "docs" / "modelos" / "MANIFESTO_MODELOS.json"

METAS = {
    "failure_classifier_meta.json": {"algoritmo": "XGB + RF", "treinado_em": "2026-09-27T19:57:57",
                                     "fonte_usada": "sintetico_calibrado", "n_amostras": 120000},
    "autoencoder_meta.json": {"algoritmo": "autoencoder", "treinado_em": "2026-09-27T19:59:55",
                              "fonte_usada": "sintetico_calibrado", "n_amostras": 120000, "seed": 42},
    "payday_meta.json": {"algoritmo": "LSTM + Prophet", "treinado_em": "2026-09-27T20:09:54",
                         "fonte_usada": "sintetico_calibrado", "n_amostras": 10000, "seed": 42},
    "voluntary_risk_meta.json": {"algoritmo": "HistGradientBoostingClassifier", "contrato": "v3",
                                 "treinado_em": "2026-09-29T21:34:09", "promovido_em": "2026-10-03T15:59:05",
                                 "seed": 42, "proveniencia": {"base_sha256": "d33b", "base_hash_canonico": "2ef7"},
                                 "origem": {"joblib_sha256": "b4ee"}},
}
FORA = ["bandit_state.json", "bandit_state.json.bak-2026-09-27", "calibracao.json",
        "voluntary_risk_candidato.joblib", "voluntary_risk_candidato_meta.json"]
PASTAS_FORA = ["v2", "v3", "v3_exp_sem_interacao", "historico"]


@pytest.fixture
def modelos(tmp_path):
    """Uma pasta `models/` de mentira: os arquivos do pacote e tudo o que fica de fora."""
    pasta = tmp_path / "models"
    pasta.mkdir()
    for i, nome in enumerate(E.nomes_do_pacote()):
        if nome in METAS:
            (pasta / nome).write_text(json.dumps(METAS[nome]), encoding="utf-8")
        elif nome.endswith(".json"):
            (pasta / nome).write_text(json.dumps({"arquivo": nome, "n": i}), encoding="utf-8")
        else:
            (pasta / nome).write_bytes(f"binario de mentira {i} {nome}".encode() * (i + 3))
    for nome in FORA:
        (pasta / nome).write_text('{"segredo": "estado operacional"}', encoding="utf-8")
    for sub in PASTAS_FORA:
        (pasta / sub).mkdir()
        (pasta / sub / "voluntary_risk_v3.joblib").write_bytes(b"experimento")
    return pasta


@pytest.fixture
def saidas(tmp_path):
    return tmp_path / "release" / "crai-modelos.zip", tmp_path / "docs" / "MANIFESTO_MODELOS.json"


def _empacotar(modelos, saidas):
    zip_, manifesto = saidas
    return E.main(["--modelos", str(modelos), "--saida", str(zip_), "--manifesto", str(manifesto)])


def _verificar(modelos, manifesto, *extra):
    return V.main(["--modelos", str(modelos), "--manifesto", str(manifesto), *extra])


def _sha(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


# ── A lista do pacote ─────────────────────────────────────────────────────

class TestALista:

    def test_e_fechada_e_cobre_os_quatro_modelos(self):
        nomes = E.nomes_do_pacote()
        assert len(nomes) == len(set(nomes)) == 18
        assert {m for _, m, _, _ in E.ARQUIVOS_DO_PACOTE} == set(E.META_DO_MODELO) == {
            "classificador_de_falha", "detector_de_anomalia", "inferencia_de_liquidez",
            "risco_voluntario_v3"}
        for meta in E.META_DO_MODELO.values():
            assert meta in nomes

    def test_o_que_os_carregadores_leem_esta_na_lista(self):
        """A lista saiu da leitura dos carregadores. Este teste relê o código deles: todo
        arquivo que um `load` abre em `MODELS_DIR` tem que estar no pacote."""
        import inspect
        import re

        from crai.churn_voluntary import risk_scorer
        from crai.ml import anomaly_detector, failure_classifier, payday_inference

        lidos = set()
        for classe in (failure_classifier.FailureClassifier, anomaly_detector.AnomalyDetector,
                       payday_inference.PaydayInference):
            fonte = inspect.getsource(classe.load)
            lidos |= set(re.findall(r'MODELS_DIR / "([^"{]+)"', fonte))
            if 'payday_prophet_{' in fonte:
                lidos |= {f"payday_prophet_{p}.json" for p in payday_inference.PROFILES}
        lidos |= {risk_scorer.MODELO_PATH.name, risk_scorer.MODELO_META_PATH.name}
        assert len(lidos) >= 14, lidos
        carregados = {n for n, _, papel, _ in E.ARQUIVOS_DO_PACOTE if papel == E.CARREGADO}
        assert lidos == carregados

    def test_nada_do_que_fica_de_fora_esta_na_lista(self):
        for nome in E.nomes_do_pacote():
            assert "bandit" not in nome and "candidato" not in nome and ".bak" not in nome
            assert "/" not in nome and "\\" not in nome
            assert nome != "calibracao.json"

    @pytest.mark.parametrize("intruso", [
        "bandit_state.json", "v3/voluntary_risk_v3.joblib", "voluntary_risk_candidato.joblib",
        "bandit_state.json.bak-2026-09-27", "calibracao.json", "xgb_failure_classifier.joblib"])
    def test_lista_adulterada_e_recusada(self, monkeypatch, intruso):
        monkeypatch.setattr(E, "ARQUIVOS_DO_PACOTE", E.ARQUIVOS_DO_PACOTE + (
            (intruso, "classificador_de_falha", E.CARREGADO, "x"),))
        with pytest.raises(SystemExit):
            E.conferir_lista()


# ── O zip ─────────────────────────────────────────────────────────────────

class TestOZip:

    def test_tem_exatamente_a_lista_na_raiz_e_nada_do_que_fica_de_fora(self, modelos, saidas, capsys):
        assert _empacotar(modelos, saidas) == 0
        zip_, _ = saidas
        with zipfile.ZipFile(zip_) as z:
            nomes = z.namelist()
            assert nomes == E.nomes_do_pacote()
            for nome in nomes:
                assert z.read(nome) == (modelos / nome).read_bytes()
        texto = " ".join(nomes)
        for proibido in ("bandit", "candidato", ".bak", "calibracao", "v2", "v3/", "v3_exp", "historico"):
            assert proibido not in texto
        saida = capsys.readouterr().out
        assert f"sha256 do zip: {_sha(zip_)}" in saida

    def test_extrair_deixa_os_arquivos_direto_na_pasta_de_modelos(self, modelos, saidas, tmp_path):
        assert _empacotar(modelos, saidas) == 0
        destino = tmp_path / "clone_limpo" / "app" / "models"
        destino.mkdir(parents=True)
        with zipfile.ZipFile(saidas[0]) as z:
            z.extractall(destino)
        assert sorted(p.name for p in destino.iterdir()) == sorted(E.nomes_do_pacote())
        assert _verificar(destino, saidas[1]) == 0

    def test_e_reprodutivel(self, modelos, saidas, tmp_path):
        assert _empacotar(modelos, saidas) == 0
        primeiro = _sha(saidas[0])
        outro = (tmp_path / "outro" / "crai-modelos.zip", tmp_path / "outro" / "manifesto.json")
        assert _empacotar(modelos, outro) == 0
        assert _sha(outro[0]) == primeiro

    def test_mudar_um_byte_de_um_modelo_muda_o_sha256_do_zip(self, modelos, saidas, tmp_path):
        assert _empacotar(modelos, saidas) == 0
        primeiro = _sha(saidas[0])
        alvo = modelos / "payday_lstm.pt"
        alvo.write_bytes(alvo.read_bytes()[:-1] + b"!")
        assert _empacotar(modelos, saidas) == 0
        assert _sha(saidas[0]) != primeiro


# ── As recusas ────────────────────────────────────────────────────────────

class TestRecusas:

    def test_falta_arquivo(self, modelos, saidas, capsys):
        (modelos / "autoencoder.pt").unlink()
        (modelos / "payday_prophet_PJ.json").unlink()
        assert _empacotar(modelos, saidas) == 2
        saida = capsys.readouterr().out
        assert "RECUSADO" in saida and "autoencoder.pt" in saida and "payday_prophet_PJ.json" in saida
        assert not saidas[0].exists() and not saidas[1].exists()

    @pytest.mark.parametrize("contrato", [None, "legado", "v2"])
    def test_voluntario_que_nao_e_o_v3_promovido(self, modelos, saidas, capsys, contrato):
        meta = dict(METAS["voluntary_risk_meta.json"])
        if contrato is None:
            meta.pop("contrato")
        else:
            meta["contrato"] = contrato
        (modelos / "voluntary_risk_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        assert _empacotar(modelos, saidas) == 2
        assert "não é o v3 promovido" in capsys.readouterr().out
        assert not saidas[0].exists()

    @pytest.mark.parametrize("texto, tipo", [
        ('{"contato": "cliente.final@exemplo.com.br"}', "e-mail"),
        ('{"doc": "123.456.789-09"}', "CPF"),
        ('{"fone": "+55 11 98888-7777"}', "telefone"),
        ('{"base": "C:\\\\Users\\\\fulano\\\\dados"}', "pasta de usuário"),
    ])
    def test_dado_pessoal_evidente_em_arquivo_de_texto(self, modelos, saidas, capsys, texto, tipo):
        (modelos / "train_metrics.json").write_text(texto, encoding="utf-8")
        assert _empacotar(modelos, saidas) == 2
        saida = capsys.readouterr().out
        assert "RECUSADO" in saida and "train_metrics.json" in saida and tipo in saida
        assert not saidas[0].exists() and not saidas[1].exists()


# ── O manifesto ───────────────────────────────────────────────────────────

class TestOManifesto:

    def test_traz_nome_tamanho_e_sha256_de_cada_arquivo_e_o_sha256_do_zip(self, modelos, saidas):
        assert _empacotar(modelos, saidas) == 0
        zip_, caminho = saidas
        m = json.loads(caminho.read_text(encoding="utf-8"))
        assert m["pacote"] == {"nome": "crai-modelos.zip", "bytes": zip_.stat().st_size,
                               "sha256": _sha(zip_), "instalar_em": "app/models/"}
        assert [a["nome"] for a in m["arquivos"]] == E.nomes_do_pacote()
        for a in m["arquivos"]:
            arquivo = modelos / a["nome"]
            assert a["bytes"] == arquivo.stat().st_size and a["sha256"] == _sha(arquivo)
            assert a["papel"] in (E.CARREGADO, E.EVIDENCIA) and a["modelo"] in E.META_DO_MODELO
        assert m["gerado_em"] and m["versao_do_manifesto"] == 1

    def test_diz_de_qual_treino_veio_cada_modelo(self, modelos, saidas):
        assert _empacotar(modelos, saidas) == 0
        treinos = json.loads(saidas[1].read_text(encoding="utf-8"))["modelos"]
        assert set(treinos) == set(E.META_DO_MODELO)
        assert treinos["classificador_de_falha"] == {
            "meta": "failure_classifier_meta.json", "algoritmo": "XGB + RF",
            "treinado_em": "2026-09-27T19:57:57", "fonte": "sintetico_calibrado",
            "n_amostras": 120000}
        vol = treinos["risco_voluntario_v3"]
        assert vol["contrato"] == "v3" and vol["promovido_em"] == "2026-10-03T15:59:05"
        assert vol["base_sha256"] == "d33b" and vol["joblib_de_origem_sha256"] == "b4ee"
        assert "sintética" in vol["fonte"]

    def test_nao_carrega_caminho_da_maquina_nem_o_que_fica_de_fora(self, modelos, saidas, tmp_path):
        assert _empacotar(modelos, saidas) == 0
        texto = saidas[1].read_text(encoding="utf-8")
        assert str(tmp_path) not in texto and str(tmp_path).replace("\\", "\\\\") not in texto
        assert "estado operacional" in texto            # a lista do que fica de fora, declarada
        m = json.loads(texto)
        assert not any("bandit" in a["nome"] or "candidato" in a["nome"] for a in m["arquivos"])


# ── A conferência ─────────────────────────────────────────────────────────

class TestVerificar:

    def test_tudo_certo_sai_com_zero(self, modelos, saidas, capsys):
        assert _empacotar(modelos, saidas) == 0
        capsys.readouterr()
        assert _verificar(modelos, saidas[1]) == 0
        assert "Tudo certo: os 18 arquivos batem com o manifesto." in capsys.readouterr().out

    def test_o_que_fica_de_fora_do_pacote_nao_atrapalha(self, modelos, saidas):
        assert _empacotar(modelos, saidas) == 0
        (modelos / "bandit_state.json").write_text('{"mudou": true}', encoding="utf-8")
        assert _verificar(modelos, saidas[1]) == 0

    def test_diz_o_que_falta(self, modelos, saidas, capsys):
        assert _empacotar(modelos, saidas) == 0
        (modelos / "voluntary_risk.joblib").unlink()
        (modelos / "autoencoder_scaler.pkl").unlink()
        capsys.readouterr()
        assert _verificar(modelos, saidas[1]) == 1
        saida = capsys.readouterr().out
        assert "FALTA      voluntary_risk.joblib" in saida
        assert "FALTA      autoencoder_scaler.pkl" in saida
        assert "16 certo(s), 2 faltando, 0 diferente(s)." in saida
        assert "heurística" in saida

    def test_diz_o_que_esta_diferente_por_tamanho_e_por_conteudo(self, modelos, saidas, capsys):
        assert _empacotar(modelos, saidas) == 0
        maior = modelos / "rf_failure_classifier.joblib"
        maior.write_bytes(maior.read_bytes() + b"x")
        trocado = modelos / "payday_lstm.pt"
        trocado.write_bytes(bytes(reversed(trocado.read_bytes())))      # mesmo tamanho
        capsys.readouterr()
        assert _verificar(modelos, saidas[1]) == 1
        saida = capsys.readouterr().out
        assert "DIFERENTE  rf_failure_classifier.joblib: tem" in saida and "bytes; o manifesto diz" in saida
        assert "DIFERENTE  payday_lstm.pt: sha256" in saida
        assert "16 certo(s), 0 faltando, 2 diferente(s)." in saida

    def test_pasta_vazia_como_num_clone_limpo(self, saidas, modelos, tmp_path, capsys):
        assert _empacotar(modelos, saidas) == 0
        vazia = tmp_path / "vazia"
        vazia.mkdir()
        capsys.readouterr()
        assert _verificar(vazia, saidas[1]) == 1
        assert "0 certo(s), 18 faltando" in capsys.readouterr().out

    def test_confere_o_zip_baixado_antes_de_extrair(self, modelos, saidas, tmp_path, capsys):
        assert _empacotar(modelos, saidas) == 0
        capsys.readouterr()
        assert _verificar(modelos, saidas[1], "--zip", str(saidas[0])) == 0
        assert "O zip confere com o manifesto" in capsys.readouterr().out

        adulterado = tmp_path / "baixado.zip"
        adulterado.write_bytes(saidas[0].read_bytes() + b"\0")
        assert _verificar(modelos, saidas[1], "--zip", str(adulterado)) == 1
        saida = capsys.readouterr().out
        assert "O ZIP NÃO CONFERE" in saida and "NÃO extraia" in saida
        assert _verificar(modelos, saidas[1], "--zip", str(tmp_path / "nao_existe.zip")) == 2

    def test_manifesto_ausente_ou_torto_nunca_da_zero(self, modelos, tmp_path, capsys):
        assert _verificar(modelos, tmp_path / "nao_existe.json") == 2
        torto = tmp_path / "torto.json"
        for conteudo in ("isto não é json", "{}", '{"arquivos": []}',
                         '{"arquivos": [{"nome": "../fora.joblib", "bytes": 1, "sha256": "x"}]}',
                         '{"arquivos": [{"nome": "a.joblib"}]}'):
            torto.write_text(conteudo, encoding="utf-8")
            assert _verificar(modelos, torto) == 2, conteudo
        assert "Manifesto" in capsys.readouterr().out

    def test_conferir_nao_carrega_modelo(self):
        """Conferir um arquivo suspeito não pode executá-lo: o verificador não importa
        `joblib`, `pickle` nem `torch`, em nenhum nível."""
        arvore = ast.parse(Path(V.__file__).read_text(encoding="utf-8"))
        importados = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados |= {a.name.split(".")[0] for a in no.names}
            elif isinstance(no, ast.ImportFrom):
                importados.add((no.module or "").split(".")[0])
        assert importados == {"argparse", "hashlib", "json", "sys", "pathlib"}


# ── O manifesto versionado ────────────────────────────────────────────────

class TestManifestoVersionado:

    def test_existe_tem_a_forma_certa_e_a_lista_do_pacote(self):
        m = V.ler_manifesto(MANIFESTO_VERSIONADO)
        assert [a["nome"] for a in m["arquivos"]] == E.nomes_do_pacote()
        assert m["pacote"]["nome"] == "crai-modelos.zip" and len(m["pacote"]["sha256"]) == 64
        assert all(len(a["sha256"]) == 64 and a["bytes"] > 0 for a in m["arquivos"])
        assert set(m["modelos"]) == set(E.META_DO_MODELO)
        assert m["modelos"]["risco_voluntario_v3"]["contrato"] == "v3"

    def test_nao_tem_estado_do_bandit_nem_caminho_de_maquina(self):
        texto = MANIFESTO_VERSIONADO.read_text(encoding="utf-8")
        m = json.loads(texto)
        assert not any("bandit" in a["nome"] or "candidato" in a["nome"] for a in m["arquivos"])
        for proibido in ("Users", ":\\\\", "@"):
            assert proibido not in texto, proibido

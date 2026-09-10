"""tests/test_risk_pluggable.py — o risco aceita um modelo treinado sem reescrever nada.

O Sprint 6 não treina modelo nenhum. Ele garante duas coisas, e as duas são
testáveis hoje:

  (a) SEM modelo — o estado de agora — `calculate_risk` devolve EXATAMENTE os
      mesmos valores de antes do sprint. Um ponto de extensão que muda o
      comportamento no caso comum não é ponto de extensão, é regressão.

  (b) COM modelo, ele decide; e se estiver defeituoso, o sistema DEGRADA para
      as regras em vez de quebrar. É o mesmo contrato do `load()` dos módulos
      de ML do involuntário: `is_fitted` falso → heurística.

O contrato de ordem das features é o ponto mais perigoso e por isso tem teste
próprio: um modelo treinado com as colunas trocadas devolve número plausível e
ERRADO, sem levantar nada. `carregar_modelo` recusa esse modelo em vez de
confiar nele.

**O que aqui é prova de regressão e o que é catraca.** `TestSemModelo` é
CATRACA — passa antes e depois do sprint, e existe para travar o "idêntico ao
de hoje". O resto reprova em `31a2450` (fim do Sprint 5) por ImportError.

ISOLAMENTO: `MODELO_PATH`/`MODELO_META_PATH` apontam para `tmp_path` e o cache
do módulo é resetado a cada teste. Sem isso, um teste que carrega um modelo
falso deixa o próximo decidindo risco com ele.

Uso:
    pytest tests/test_risk_pluggable.py -v
"""

import json

import pytest

from crai.churn_voluntary import risk_scorer as rs
from crai.churn_voluntary.risk_scorer import (
    FEATURES_DE_RISCO,
    calculate_risk,
    carregar_modelo,
    modelo_ativo,
)

# Os mesmos casos do `risk_scorer` de hoje, com o valor que ele devolve.
# Medido em `31a2450`, antes de existir qualquer ponto de extensão.
CASOS_DE_HOJE = [
    ("Cancellation Page Viewed", {}, 0.90),
    ("Cancellation Page Viewed", {"days_since_last": 99}, 0.90),
    ("Downgrade Clicked", {}, 0.75),
    ("Session Started", {"days_since_last": 18, "features_used_30d": 1}, 0.66),
    ("Session Started", {"days_since_last": 30, "features_used_30d": 0}, 1.0),
    ("Session Started", {"days_since_last": 0, "features_used_30d": 30}, 0.0),
    ("Session Started", {}, 0.0),
    ("Session Started", {"days_since_last": 7, "features_used_30d": 3}, 0.283),
    ("Evento Desconhecido", {}, 0.0),
    ("", {}, 0.0),
]


class ModeloFalso:
    """Um classificador do tamanho do contrato: `predict_proba` e nada mais."""

    def __init__(self, valor=0.42, explode=False):
        self.valor = valor
        self.explode = explode
        self.vistos = []

    def predict_proba(self, X):
        if self.explode:
            raise RuntimeError("modelo quebrado")
        self.vistos.append(list(X[0]))
        return [[1 - self.valor, self.valor]]


class ModeloSoPredict:
    """Regressor: só `predict`. O sistema tem que aceitar os dois formatos."""

    def __init__(self, valor=0.31):
        self.valor = valor

    def predict(self, X):
        return [self.valor]


@pytest.fixture(autouse=True)
def modelo_isolado(tmp_path, monkeypatch):
    """Nenhum teste vê o modelo de outro, nem o de `crai/models/`."""
    monkeypatch.setattr(rs, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(rs, "MODELO_PATH", tmp_path / "voluntary_risk.joblib")
    monkeypatch.setattr(rs, "MODELO_META_PATH", tmp_path / "voluntary_risk_meta.json")
    monkeypatch.setattr(rs, "_modelo", None)
    monkeypatch.setattr(rs, "_modelo_consultado", False)
    return tmp_path


def _injetar(modelo):
    """Põe ESTE objeto como o modelo ativo, sem passar pelo disco.

    `joblib.dump`/`load` devolve uma CÓPIA: o espião salvo em disco não é o
    espião que o teste segura, e `modelo.vistos` fica vazio para sempre. Para
    inspecionar o que o modelo RECEBEU, a instância tem que ser a mesma. O
    caminho do disco é exercido pelos testes de `_salvar`.
    """
    rs._modelo = modelo
    rs._modelo_consultado = True
    return modelo


def _salvar(modelo, features=None, **meta_extra):
    """Salva modelo + meta como o README_treino.md manda, e força o reload."""
    import joblib

    joblib.dump(modelo, rs.MODELO_PATH)
    meta = {"features": features if features is not None else FEATURES_DE_RISCO,
            "algoritmo": "ModeloFalso", "treinado_em": "2026-09-05"}
    meta.update(meta_extra)
    rs.MODELO_META_PATH.write_text(json.dumps(meta), encoding="utf-8")
    carregar_modelo(forcar=True)


# ── Sem modelo: nada mudou ───────────────────────────────────────────────

class TestSemModelo:
    """CATRACA. Estes valores são os de antes do Sprint 6, e não podem mudar
    porque um ponto de extensão foi adicionado."""

    def test_modelo_nao_esta_ativo(self):
        assert modelo_ativo() is False

    @pytest.mark.parametrize("event,props,esperado", CASOS_DE_HOJE)
    def test_valores_identicos_aos_de_hoje(self, event, props, esperado):
        assert calculate_risk(event, props) == esperado

    @pytest.mark.parametrize("event,props,esperado", CASOS_DE_HOJE)
    def test_calculate_risk_e_as_regras_concordam(self, event, props, esperado):
        """Sem modelo, `calculate_risk` É `_risco_por_regras` — nada no meio."""
        assert calculate_risk(event, props) == rs._risco_por_regras(event, props)

    def test_meta_sem_modelo_nao_ativa_nada(self, modelo_isolado):
        """Meta solto no diretório não é modelo."""
        rs.MODELO_META_PATH.write_text(
            json.dumps({"features": FEATURES_DE_RISCO}), encoding="utf-8")
        assert carregar_modelo(forcar=True) is False


# ── Com modelo: ele decide ───────────────────────────────────────────────

class TestComModelo:

    def test_modelo_decide_o_risco(self):
        _salvar(ModeloFalso(valor=0.42))

        assert modelo_ativo() is True
        # 0.42 não é nenhum dos valores que as regras produzem para este evento.
        assert calculate_risk("Cancellation Page Viewed", {}) == 0.42
        assert calculate_risk("Session Started", {"days_since_last": 18}) == 0.42

    def test_aceita_modelo_com_so_predict(self):
        """Regressor também serve: nem todo modelo tem `predict_proba`."""
        _salvar(ModeloSoPredict(valor=0.31))
        assert calculate_risk("Downgrade Clicked", {}) == 0.31

    def test_features_chegam_na_ordem_declarada(self):
        """A ordem é o contrato. Este teste é o que prova que o vetor montado
        aqui é o mesmo que o README_treino.md manda treinar."""
        modelo = _injetar(ModeloFalso())

        calculate_risk("Downgrade Clicked", {
            "days_since_last": 12, "features_used_30d": 3, "mrr": 550.0})

        assert modelo.vistos[-1] == [12.0, 3.0, 550.0, 0.0, 1.0, 0.0]
        assert len(modelo.vistos[-1]) == len(FEATURES_DE_RISCO)

    @pytest.mark.parametrize("event,esperado", [
        ("Cancellation Page Viewed", [1.0, 0.0, 0.0]),
        ("Downgrade Clicked",        [0.0, 1.0, 0.0]),
        ("Session Started",          [0.0, 0.0, 1.0]),
        ("Evento Desconhecido",      [0.0, 0.0, 0.0]),
    ])
    def test_one_hot_do_evento(self, event, esperado):
        modelo = _injetar(ModeloFalso())
        calculate_risk(event, {})
        assert modelo.vistos[-1][3:] == esperado

    def test_props_torto_nao_quebra_o_vetor(self):
        """`props` é payload de terceiro, e o vetor precisa ser completo."""
        modelo = _injetar(ModeloFalso())

        calculate_risk("Session Started", {
            "days_since_last": "muito", "features_used_30d": [3], "mrr": None})

        assert modelo.vistos[-1] == [0.0, 0.0, 0.0, 0.0, 0.0, 1.0]

    def test_pipeline_continua_de_pe_com_modelo(self):
        """O `assess_risk` do grafo consome `calculate_risk` — o modelo entra
        sem que o nó saiba da diferença."""
        _salvar(ModeloFalso(valor=0.95))
        from crai.churn_voluntary.risk_scorer import classify_criticality

        risco = calculate_risk("Session Started", {"days_since_last": 1})
        assert risco == 0.95
        assert classify_criticality(risco, None) == "critico"


# ── Modelo defeituoso: degrada, não quebra ───────────────────────────────

class TestModeloDefeituosoDegrada:

    def test_ordem_de_features_divergente_e_recusada(self, capsys):
        """O DEFEITO MAIS PERIGOSO desta frente: colunas trocadas produzem
        número plausível e ERRADO, sem levantar nada. Recusar é a única
        leitura correta."""
        _salvar(ModeloFalso(valor=0.42),
                features=list(reversed(FEATURES_DE_RISCO)))

        assert modelo_ativo() is False
        assert "IGNORADO" in capsys.readouterr().out
        assert calculate_risk("Cancellation Page Viewed", {}) == 0.90

    def test_meta_sem_features_e_recusado(self):
        _salvar(ModeloFalso(), features=None)
        rs.MODELO_META_PATH.write_text(json.dumps({"algoritmo": "X"}),
                                       encoding="utf-8")
        assert carregar_modelo(forcar=True) is False

    def test_modelo_sem_meta_e_recusado(self):
        import joblib

        joblib.dump(ModeloFalso(), rs.MODELO_PATH)
        assert carregar_modelo(forcar=True) is False
        assert calculate_risk("Downgrade Clicked", {}) == 0.75

    def test_arquivo_corrompido_cai_nas_regras(self):
        rs.MODELO_PATH.write_bytes(b"isto nao e um joblib")
        rs.MODELO_META_PATH.write_text(
            json.dumps({"features": FEATURES_DE_RISCO}), encoding="utf-8")

        assert carregar_modelo(forcar=True) is False
        assert calculate_risk("Cancellation Page Viewed", {}) == 0.90

    def test_predict_que_levanta_cai_nas_regras(self):
        """Um modelo quebrado não derruba o ciclo de retenção de ninguém."""
        _salvar(ModeloFalso(explode=True))

        assert modelo_ativo() is True         # carregou...
        assert calculate_risk("Downgrade Clicked", {}) == 0.75   # ...mas degradou

    @pytest.mark.parametrize("valor", [1.5, -0.2, float("nan"), float("inf")])
    def test_saida_fora_de_zero_um_cai_nas_regras(self, valor):
        """Risco é probabilidade. Um 1.5 atravessaria o corte de 0.90 e mandaria
        tom crítico para todo mundo."""
        _salvar(ModeloFalso(valor=valor))
        assert calculate_risk("Downgrade Clicked", {}) == 0.75

    def test_saida_nao_numerica_cai_nas_regras(self):
        _salvar(ModeloSoPredict(valor="alto"))
        assert calculate_risk("Downgrade Clicked", {}) == 0.75


# ── O contrato documentado ───────────────────────────────────────────────

class TestContratoDoReadme:
    """O README_treino.md é o guia da fase de treino. Se ele mentir sobre um
    nome, o passo a passo não roda — e ninguém descobre até a hora do treino."""

    @pytest.fixture
    def readme(self):
        from pathlib import Path
        caminho = (Path(__file__).resolve().parent.parent
                   / "crai" / "churn_voluntary" / "README_treino.md")
        return caminho.read_text(encoding="utf-8")

    def test_readme_existe_ao_lado_do_modulo(self, readme):
        assert len(readme) > 2000

    @pytest.mark.parametrize("simbolo", [
        "FEATURES_DE_RISCO", "MODELO_PATH", "MODELO_META_PATH",
        "carregar_modelo", "modelo_ativo", "ciclos_retencao",
        "origem_desfecho", "retention_cycles.db",
    ])
    def test_readme_so_cita_simbolo_que_existe(self, readme, simbolo):
        assert simbolo in readme, f"{simbolo} sumiu do guia"

    def test_simbolos_citados_existem_de_verdade(self):
        for nome in ("FEATURES_DE_RISCO", "MODELO_PATH", "MODELO_META_PATH",
                     "carregar_modelo", "modelo_ativo"):
            assert hasattr(rs, nome), f"o README cita {nome}, que não existe"

    def test_readme_declara_as_duas_limitacoes(self, readme):
        """As duas limitações CONHECIDAS têm que estar escritas: o label não é
        churn, e o corte de 0.60 enviesa a seleção. Documentadas, elas são
        decisão; omitidas, viram armadilha para quem treinar."""
        assert "ACEITAÇÃO DE OFERTA" in readme
        assert "Viés de seleção" in readme
        assert "0.60" in readme

    def test_ordem_de_features_do_readme_bate_com_o_codigo(self, readme):
        """O guia manda treinar com `X = treino[FEATURES_DE_RISCO]`, então a
        lista impressa nele precisa ser a lista real."""
        for feature in FEATURES_DE_RISCO:
            assert f'"{feature}"' in readme

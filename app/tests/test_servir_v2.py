"""tests/test_servir_v2.py — o artefato v2 REAL é servido sem feature ausente.

POR QUE ISTO EXISTE (Bloco H, 22/09/2026). O classificador treinado na base v2
trocou de features: `card_brand` saiu, `metodo_pagamento` entrou. O artefato
grava a própria lista e `predict` lê dela — mas quem monta o dicionário de
entrada na hora de servir (`agent/workflow.py`) continuava fornecendo
`card_brand` e nada de `metodo_pagamento`. O `predict` não quebra com feature
ausente: ele a substitui por 0 em silêncio (`features.get(col, 0)`), e para
uma categórica isso vira a categoria "desconhecido" do encoder. O grafo
rodaria, o painel mostraria um score, e o modelo estaria sendo servido com
uma entrada que ele nunca viu no treino — sem log, sem erro, sem teste.

O QUE ESTE ARQUIVO FAZ, e o que ele NÃO faz de propósito: carrega o artefato
que está em `models/` — o que produção carrega — e roda o grafo inteiro por
cima dele, espiando o dicionário que chega ao `predict`. **Não usa mock do
modelo**: o mock é justamente o que esconderia o defeito, porque aceita
qualquer dicionário.

Se `models/` não tem artefato, ou tem um artefato v1 (sem
`metodo_pagamento`), os testes PULAM: um checkout limpo não pode reprovar por
falta de um binário que o `.gitignore` garante não estar lá, e um `train_all
--base v1` deliberado não é defeito — é outra escolha de artefato.
"""

import hashlib
import hmac
import json
import sqlite3
import time

import joblib
import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as workflow_module
from crai.agent.main_agent import crai_agent
from crai.agent.workflow import _features_pix
from crai.api import app as app_module
from crai.dunning import recovery_log
from crai.ml.failure_classifier import ALL_FEATURES_V2, FEATURES_CATEGORICAS, MODELS_DIR

VALOR = 1818.29          # a mediana de `invoice_amount` da base v2
TENANT = "empresa_v2"
FEATURE_NAMES = MODELS_DIR / "feature_names.joblib"


def _artefato_em_producao_e_v2() -> bool:
    if not FEATURE_NAMES.exists():
        return False
    try:
        return "metodo_pagamento" in list(joblib.load(FEATURE_NAMES))
    except Exception:
        return False


requer_artefato_v2 = pytest.mark.skipif(
    not _artefato_em_producao_e_v2(),
    reason=f"{MODELS_DIR} sem artefato v2 (feature_names.joblib com metodo_pagamento)",
)


def _assinar(corpo: bytes, segredo: bytes = b"s3cr3t") -> dict:
    ts = int(time.time())
    mac = hmac.new(segredo, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json"}


def _corpo(id_recorrencia: str, e2e: str, codigo_falha: str = "AM04") -> bytes:
    return json.dumps({
        "event": "automatic_pix.charge_failed", "e2e_id": e2e, "valor": VALOR,
        "id_recorrencia": id_recorrencia, "codigo_falha": codigo_falha,
    }).encode()


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", "s3cr3t")
    monkeypatch.setenv("ENV", "development")
    with TestClient(app_module.app) as c:
        yield c


def _conferir_entrada_contra_o_artefato(features: dict, clf) -> None:
    """Toda feature que o artefato declara chega, não é None, e cada categórica
    é um valor que o encoder VIU no treino (não o índice 'desconhecido')."""
    ausentes = [c for c in clf.feature_names if c not in features]
    assert not ausentes, f"features do artefato ausentes na entrada: {ausentes}"
    nulas = [c for c in clf.feature_names if features[c] is None]
    assert not nulas, f"features do artefato chegando como None: {nulas}"
    for col in clf.feature_names:
        if col in FEATURES_CATEGORICAS:
            classes = list(clf.label_encoders[col].classes_)
            assert str(features[col]) in classes, (
                f"{col}={features[col]!r} não está no vocabulário de treino "
                f"{classes}: cairia no índice 'desconhecido' do encoder")
    # E a linha que o modelo recebe de fato não usa o índice 'desconhecido'.
    X = clf._preprocess_single(features)
    for i, col in enumerate(clf.feature_names):
        if col in FEATURES_CATEGORICAS:
            assert X[0, i] < len(clf.label_encoders[col].classes_), col


@requer_artefato_v2
class TestOArtefatoQueOGrafoCarrega:

    def test_o_classificador_do_grafo_e_o_artefato_v2_de_models(self):
        """`workflow._classifier` é carregado de `models/` no import. Tem que ser
        o v2: lista da base v2, sem `card_brand`, com `metodo_pagamento`."""
        clf = workflow_module._classifier
        assert clf.is_fitted, "o grafo está na heurística — nenhum artefato carregou"
        assert clf.feature_names == ALL_FEATURES_V2
        assert "card_brand" not in clf.feature_names
        assert clf.meta["features"] == ALL_FEATURES_V2
        assert set(clf.label_encoders) == {"gateway_error_code", "metodo_pagamento"}
        assert set(clf.label_encoders["metodo_pagamento"].classes_) == {"pix_automatico", "boleto"}

    def test_as_features_de_pix_cobrem_a_lista_do_artefato(self):
        """O ponto de montagem do caminho ativo, sozinho, contra o artefato real."""
        features = _features_pix(
            {"valor": VALOR, "id_recorrencia": "RN_v2_unit", "codigo_falha": "AM04"},
            VALOR, customer_id="RN_v2_unit",
        )
        _conferir_entrada_contra_o_artefato(features, workflow_module._classifier)
        assert features["metodo_pagamento"] == "pix_automatico"
        assert "card_brand" not in features

    @pytest.mark.parametrize("codigo,causa", [
        ("AM04", "insufficient_funds"), ("AM02", "limit_exceeded"),
        ("MD01", "authorization_revoked"), ("AB03", "processing_error"),
    ])
    def test_toda_causa_de_pix_esta_no_vocabulario_do_artefato_v2(self, codigo, causa):
        """A base v2 treinou com o vocabulário de Pix; nenhuma causa que o
        pipeline emite pode cair no índice 'desconhecido'."""
        features = _features_pix(
            {"valor": VALOR, "id_recorrencia": "RN_v2_causa", "codigo_falha": codigo},
            VALOR, customer_id="RN_v2_causa",
        )
        assert features["gateway_error_code"] == causa
        _conferir_entrada_contra_o_artefato(features, workflow_module._classifier)


@requer_artefato_v2
class TestOGrafoServeOArtefatoV2:

    def test_ponta_a_ponta_sem_mock_nada_chega_como_ausente(self, cliente, monkeypatch):
        """Webhook assinado → grafo inteiro → predict do artefato v2 REAL.

        O espião embrulha o `predict` verdadeiro: registra o dicionário que
        chegou e chama o modelo de verdade. Não é mock — o resultado que
        volta para o grafo é o do XGBoost + RF carregados de `models/`.
        """
        clf = workflow_module._classifier
        recebidos = []
        predict_real = clf.predict

        def espiao(features, channel="bot_whatsapp"):
            recebidos.append(dict(features))
            return predict_real(features, channel=channel)

        monkeypatch.setattr(clf, "predict", espiao)

        rec = "RN_v2_ponta_a_ponta"
        corpo = _corpo(rec, f"E_{rec}")
        resposta = cliente.post("/webhooks/pix-automatico", content=corpo,
                                headers={**_assinar(corpo), "x-tenant-id": TENANT})
        assert resposta.status_code == 200, resposta.text
        assert resposta.json().get("pipeline") is not False

        assert len(recebidos) == 1, "o diagnóstico chamou o predict uma vez por ciclo"
        _conferir_entrada_contra_o_artefato(recebidos[0], clf)
        assert recebidos[0]["metodo_pagamento"] == "pix_automatico"

        # O que ficou no checkpoint veio do modelo, não da heurística.
        estado = dict(crai_agent.get_state({"configurable": {"thread_id": rec}}).values)
        assert estado["features"]["metodo_pagamento"] == "pix_automatico"
        assert "card_brand" not in estado["features"]
        shap = estado["shap_explanation"]
        assert shap.get("features"), "sem SHAP: o predict caiu na heurística"
        assert {e["feature"] for e in shap["features"]} == set(ALL_FEATURES_V2)
        assert "heurística" not in shap.get("readable", "")
        assert 0 <= estado["recovery_score"] <= 100
        assert 0.0 <= estado["p_recovery"] <= 1.0

        # A trilha do Art. 20 gravou o diagnóstico como decisão do MODELO.
        decisoes = [d for d in (estado.get("decisoes") or []) if d.get("tipo_decisao") == "risco"]
        assert decisoes and decisoes[0]["modelo"] == "failure_classifier"
        assert decisoes[0]["modelo_versao"], "a versão do artefato não foi gravada"

    def test_o_ciclo_gravado_carrega_o_metodo_de_pagamento(self, cliente):
        """O dataset de treino (recovery_log) recebe a feature nova — senão o
        retreino com dado real nasceria com a coluna que o modelo mais precisa
        vazia."""
        rec = "RN_v2_dataset"
        corpo = _corpo(rec, f"E_{rec}")
        resposta = cliente.post("/webhooks/pix-automatico", content=corpo,
                                headers={**_assinar(corpo), "x-tenant-id": TENANT})
        assert resposta.status_code == 200, resposta.text

        linhas = [l for l in recovery_log.linhas(TENANT) if l["e2e_id"] == f"E_{rec}"]
        assert len(linhas) == 1
        assert linhas[0]["metodo_pagamento"] == "pix_automatico"
        assert linhas[0]["card_brand"] is None       # coluna legada, NULL nas linhas novas
        assert linhas[0]["gateway_error_code"] == "insufficient_funds"


@requer_artefato_v2
class TestOLimiarDoAutoencoderPromovido:
    """O percentil do autoencoder em `models/` é o que o critério devolve na
    curva DESSE artefato — não uma constante.

    O autoencoder não reproduz entre máquinas (`docs/LIMITACOES.md`): o p83 de
    20/09 virou p81 em 22/09 e voltou a p83 em 23/09, pelo mesmo critério. O
    TREINO é quem aplica o critério (`AnomalyDetector.train(recall_minimo=...)`)
    e grava a saída no meta; aqui se confere o critério INTEIRO sobre o artefato
    promovido:

      (a) `meta["threshold_percentil"]` == `escolher_percentil(curva, piso)`;
      (b) o recall no percentil escolhido é >= piso;
      (c) o recall no percentil SEGUINTE da curva é < piso — é o que prova que
          é o MAIOR percentil que passa, e não apenas um que passa.

    Um percentil vizinho gravado no meta — que "também passa", ou que não
    passa — reprova (teste de adulteração abaixo).
    """

    @staticmethod
    def _artefato() -> tuple:
        curva_path = MODELS_DIR / "curva_limiar_anomalia.json"
        meta_path = MODELS_DIR / "autoencoder_meta.json"
        if not (curva_path.exists() and meta_path.exists()):
            pytest.skip("models/ sem curva_limiar_anomalia.json ou autoencoder_meta.json")
        return (json.loads(curva_path.read_text(encoding="utf-8"))["curva"],
                json.loads(meta_path.read_text(encoding="utf-8")))

    @staticmethod
    def _conferir_criterio(meta: dict, curva: list, piso: float) -> dict:
        """As três afirmações (a), (b), (c). Devolve o ponto escolhido e o seguinte."""
        from crai.ml import anomaly_detector as am

        escolhido = am.AnomalyDetector.escolher_percentil(curva, piso)
        # (a) o meta gravou a saída do critério, não outro número
        assert meta["threshold_percentil"] == escolhido["percentil"], (
            f"artefato gravado com p{meta['threshold_percentil']}; o critério na curva "
            f"deste artefato dá p{escolhido['percentil']}. O treino tem que APLICAR o "
            "critério, não copiar um número")
        # (b) o recall no percentil escolhido atinge o piso
        assert escolhido["recall"] >= piso, escolhido
        # (c) é o MAIOR: o percentil seguinte da curva já fica abaixo do piso
        seguintes = [c for c in curva if c["percentil"] > escolhido["percentil"]]
        assert seguintes, (
            f"p{escolhido['percentil']} é o último ponto da curva — a varredura é "
            "curta demais para provar que ele é o maior que passa")
        seguinte = min(seguintes, key=lambda c: c["percentil"])
        assert seguinte["recall"] < piso, (
            f"p{seguinte['percentil']} ainda tem recall {seguinte['recall']} >= {piso}: "
            f"p{escolhido['percentil']} passa, mas não é o MAIOR que passa")
        # e o limiar gravado é o da curva nesse percentil (a curva arredonda a 6 casas)
        assert abs(meta["threshold"] - escolhido["threshold"]) < 1e-6, (
            f"threshold do meta {meta['threshold']} não é o da curva em "
            f"p{escolhido['percentil']} ({escolhido['threshold']})")
        return {"escolhido": escolhido, "seguinte": seguinte}

    def test_o_percentil_gravado_e_a_saida_do_criterio_na_curva_do_artefato(self):
        from crai.ml import anomaly_detector as am

        curva, meta = self._artefato()
        self._conferir_criterio(meta, curva, am.RECALL_MINIMO_LIMIAR_V2)

    def test_o_meta_declara_o_criterio_que_o_treino_aplicou(self):
        """`criterio_limiar` no meta é a prova de que o treino aplicou o critério
        (e não recebeu um `--percentil-limiar` à mão)."""
        from crai.ml import anomaly_detector as am

        _, meta = self._artefato()
        criterio = meta.get("criterio_limiar")
        assert criterio, ("meta sem `criterio_limiar`: o artefato foi treinado com "
                          "percentil fixo, não pelo critério")
        assert criterio["recall_minimo"] == am.RECALL_MINIMO_LIMIAR_V2
        assert criterio["percentil"] == meta["threshold_percentil"]
        assert criterio["recall"] >= am.RECALL_MINIMO_LIMIAR_V2

    @pytest.mark.parametrize("delta", [-1.0, +1.0], ids=["um_abaixo", "um_acima"])
    def test_um_percentil_adulterado_no_meta_reprova(self, delta):
        """Adultera o meta em memória: o vizinho de baixo "também passa" no
        piso mas não é o maior; o de cima não passa. Os dois têm que reprovar."""
        from crai.ml import anomaly_detector as am

        curva, meta = self._artefato()
        adulterado = {**meta, "threshold_percentil": meta["threshold_percentil"] + delta}
        with pytest.raises(AssertionError):
            self._conferir_criterio(adulterado, curva, am.RECALL_MINIMO_LIMIAR_V2)


class TestMigracaoDoLogDeCiclos:
    """Independe do artefato: um banco criado ANTES do Bloco H não tem a coluna
    `metodo_pagamento`, e `CREATE TABLE IF NOT EXISTS` não a acrescentaria."""

    def _state(self, e2e: str) -> dict:
        return {
            "customer_id": "RN_legado", "invoice_id": e2e, "tenant_id": TENANT,
            "amount": VALOR, "failure_cause": "insufficient_funds",
            "recovery_score": 70, "p_recovery": 0.70, "eprofit": 99.0,
            "estrategia": "retry_automatico", "recovered": False,
            "dunning_sent": False, "pix_retry_schedule": [{"numero": 1}],
            "features": {
                "tenure_months": 14, "day_of_month": 5, "invoice_amount": VALOR,
                "avg_ticket": VALOR, "payment_history_score": 0.83,
                "failure_count_90d": 1, "hour_of_day": 10, "day_of_week": 2,
                "attempt_count": 1, "gateway_error_code": "insufficient_funds",
                "metodo_pagamento": "pix_automatico", "ltv_estimated": 2100.0,
            },
        }

    def test_banco_anterior_ao_bloco_h_ganha_a_coluna_e_grava_a_linha(self, tmp_path, monkeypatch):
        caminho = tmp_path / "legado.db"
        monkeypatch.setenv(recovery_log.ENV_CAMINHO, str(caminho))

        schema_antigo = recovery_log._SCHEMA.replace("    metodo_pagamento      TEXT,\n", "")
        assert "metodo_pagamento" not in schema_antigo
        with sqlite3.connect(caminho) as conn:
            conn.executescript(schema_antigo)
        colunas = {l[1] for l in sqlite3.connect(caminho).execute("PRAGMA table_info(ciclos_recuperacao)")}
        assert "metodo_pagamento" not in colunas

        assert recovery_log.registrar_ciclo(self._state("E_legado"), 0) is not None
        linha = [l for l in recovery_log.linhas(TENANT) if l["e2e_id"] == "E_legado"][0]
        assert linha["metodo_pagamento"] == "pix_automatico"
        assert linha["card_brand"] is None

    def test_a_migracao_e_idempotente(self, tmp_path, monkeypatch):
        monkeypatch.setenv(recovery_log.ENV_CAMINHO, str(tmp_path / "novo.db"))
        for _ in range(3):
            recovery_log._conectar().close()
        colunas = [l[1] for l in sqlite3.connect(tmp_path / "novo.db")
                   .execute("PRAGMA table_info(ciclos_recuperacao)")]
        assert colunas.count("metodo_pagamento") == 1

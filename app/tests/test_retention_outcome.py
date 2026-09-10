"""tests/test_retention_outcome.py — o bandit para de aprender com dado inventado.

Até o Sprint 3, `track_outcome` decidia o aceite com `random.random()` e
alimentava o posterior do bandit com isso. O sistema aprendia com uma moeda.

O Sprint 4 separa os dois mundos em TOPOLOGIAS DE GRAFO diferentes:

    PRODUÇÃO   (default)  o grafo termina em `update_crm` logo após o envio.
                          `accepted` fica None — "aguardando retorno", não
                          "recusou". Quem ensina o bandit é
                          `POST /webhooks/retention-outcome`.
    SIMULAÇÃO  (env "1")  `track_outcome` volta ao caminho, para a demo e para
                          os testes rodarem sem webhook externo.

E acrescenta o que faltava para a fase de treino: o `retention_log` grava UMA
LINHA POR CICLO com as features da decisão, completada pelo desfecho quando ele
chega. Sem isso o sistema aprende e esquece — o `bandit_state.json` guarda um
placar agregado, não um conjunto de dados.

**O que aqui é prova de regressão e o que é catraca.** Tudo reprova em
`c02beb0` (fim do Sprint 3): o endpoint, o módulo de log e os dois modos não
existiam. Os que merecem nome próprio:

    test_producao_nao_sorteia_desfecho     — o defeito central do sprint
    test_reenvio_nao_conta_duas_vezes      — webhook reenvia; posterior não pode mover
    test_sem_assinatura_nao_ensina_o_bandit — endpoint que move aprendizado é
                                             superfície de ataque
    test_identidades_distintas_nao_se_misturam — o invariante da A1-r10 pelo
                                             caminho NOVO, que substituiu o
                                             caminho medido em test_webhook_security

ISOLAMENTO: `CRAI_RETENTION_DB` aponta para `tmp_path` e `MODELS_DIR` do bandit
também. Nenhum teste toca o banco nem o estado reais.

Uso:
    pytest tests/test_retention_outcome.py -v
"""

import hashlib
import hmac
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import voluntary_agent as va

SEGREDO_SEGMENT = "segredo_segment_de_teste"
SEGREDO_OUTCOME = "segredo_outcome_de_teste"


def _assinar(corpo: bytes, segredo: str) -> str:
    return hmac.new(segredo.encode(), corpo, hashlib.sha1).hexdigest()


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    """Banco, estado do bandit e envs isolados por teste."""
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SEGMENT_WEBHOOK_SECRET", SEGREDO_SEGMENT)
    monkeypatch.setenv("RETENTION_OUTCOME_WEBHOOK_SECRET", SEGREDO_OUTCOME)
    monkeypatch.setenv("CRAI_RETENTION_DB", str(tmp_path / "ciclos.db"))
    monkeypatch.delenv("CRAI_SIMULATE_OUTCOMES", raising=False)   # produção
    monkeypatch.setattr(ob, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "bandit_state.json")
    va._channel_history.clear()
    yield
    va._channel_history.clear()


@pytest.fixture
def cliente():
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c


def _disparar_ciclo(cliente, user_id="cliente_teste", perfil="PJ", on_site=True):
    """Um ciclo completo pelo webhook do Segment. Devolve a linha gravada."""
    corpo = json.dumps({
        "userId": user_id, "event": "Cancellation Page Viewed",
        "properties": {"billing_profile": perfil, "on_site_now": on_site,
                       "days_since_last": 12, "features_used_30d": 2,
                       "mrr": 550.0},
    }).encode()
    r = cliente.post("/webhooks/segment", content=corpo, headers={
        "x-signature": _assinar(corpo, SEGREDO_SEGMENT),
        "content-type": "application/json"})
    assert r.status_code == 200, r.text
    return _ultima_linha()


def _ultima_linha() -> dict:
    conn = sqlite3.connect(rl.caminho_do_banco())
    conn.row_factory = sqlite3.Row
    linha = conn.execute(
        "SELECT * FROM ciclos_retencao ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return dict(linha) if linha else {}


def _enviar_desfecho(cliente, payload: dict, segredo: str = SEGREDO_OUTCOME):
    corpo = json.dumps(payload).encode()
    return cliente.post("/webhooks/retention-outcome", content=corpo, headers={
        "x-signature": _assinar(corpo, segredo),
        "content-type": "application/json"})


# ── Os dois modos ────────────────────────────────────────────────────────

class TestModosDoGrafo:

    def test_producao_nao_sorteia_desfecho(self, cliente):
        """O defeito central do Sprint 4.

        Em produção nada decide por conta própria se o cliente aceitou. Ao fim
        do grafo o ciclo está ABERTO: `accepted` é NULL no log, e o posterior
        do bandit não se moveu.
        """
        antes = va._bandit.conversion_rates(rl.TENANT_PADRAO, "PJ")
        linha = _disparar_ciclo(cliente)

        assert linha["accepted"] is None, "o grafo de produção decidiu o desfecho sozinho"
        assert linha["offer_sent"] == 1
        assert va._bandit.conversion_rates(rl.TENANT_PADRAO, "PJ") == antes, (
            "o posterior mudou sem desfecho real")

    def test_simulacao_fecha_o_ciclo_na_hora(self, cliente, monkeypatch):
        """CATRACA da demo: com a env ligada, o comportamento antigo volta."""
        monkeypatch.setenv("CRAI_SIMULATE_OUTCOMES", "1")

        linha = _disparar_ciclo(cliente)

        assert linha["accepted"] in (0, 1), "o modo simulação não fechou o ciclo"
        assert linha["origem_desfecho"] == "simulacao"

    def test_so_o_literal_1_liga_a_simulacao(self, monkeypatch):
        """Um modo que aprende com dado inventado não pode ser ligado por
        erro de digitação."""
        for valor, esperado in [("1", True), ("0", False), ("true", False),
                                ("sim", False), ("", False), (" 1 ", True)]:
            monkeypatch.setenv("CRAI_SIMULATE_OUTCOMES", valor)
            assert va.modo_simulacao() is esperado, f"env={valor!r}"

    def test_topologias_sao_de_fato_diferentes(self):
        producao = va.build_voluntary_churn_graph(simular=False).get_graph()
        simulacao = va.build_voluntary_churn_graph(simular=True).get_graph()

        nos_producao = {n for n in producao.nodes}
        nos_simulacao = {n for n in simulacao.nodes}

        assert "track_outcome" not in nos_producao
        assert "track_outcome" in nos_simulacao


# ── Segurança da borda ───────────────────────────────────────────────────

class TestBordaDoWebhook:

    def test_sem_assinatura_nao_ensina_o_bandit(self, cliente):
        """Um endpoint que move o posterior é superfície de ataque: aberto,
        qualquer um faz a CRAI acreditar que uma oferta converte 100%."""
        linha = _disparar_ciclo(cliente)
        antes = va._bandit.conversion_rates(rl.TENANT_PADRAO, "PJ")

        r = cliente.post("/webhooks/retention-outcome", json={
            "user_id": "cliente_teste", "offer_type": linha["offer_type"],
            "profile": "PJ", "accepted": True})

        assert r.status_code == 401
        assert va._bandit.conversion_rates(rl.TENANT_PADRAO, "PJ") == antes

    def test_assinatura_errada_e_401(self, cliente):
        linha = _disparar_ciclo(cliente)
        r = _enviar_desfecho(cliente, {
            "user_id": "cliente_teste", "offer_type": linha["offer_type"],
            "profile": "PJ", "accepted": True}, segredo="outro_segredo")
        assert r.status_code == 401

    def test_segredo_ausente_fecha_o_endpoint(self, cliente, monkeypatch):
        """Fail closed, como os outros três webhooks: esquecer de configurar
        nunca deve deixar aberto o que ensina o modelo."""
        monkeypatch.delenv("RETENTION_OUTCOME_WEBHOOK_SECRET", raising=False)
        r = _enviar_desfecho(cliente, {"user_id": "x", "offer_type": "desconto_20",
                                       "profile": "PJ", "accepted": True})
        assert r.status_code == 401

    @pytest.mark.parametrize("campo,valor", [
        ("profile", "abc"), ("profile", ""), ("profile", "299,90"),
        ("offer_type", "consulta_cs"), ("offer_type", "qualquer_coisa"),
    ])
    def test_vocabulario_e_recusado_na_borda(self, cliente, campo, valor):
        """Fecha a ESCRITA do lixo que o Sprint 1 teve de limpar na LEITURA.

        `profile` e `offer_type` viram CHAVE de dicionário no bandit, e
        `record_outcome` aceita qualquer string e persiste — foi assim que
        perfis como `""` e `"299,90"` entraram no `bandit_state.json` real.
        `consulta_cs` está aqui porque é o braço extinto: aceitar um desfecho
        dele ressuscitaria a oferta que o Sprint 1 removeu.
        """
        base = {"user_id": "x", "offer_type": "desconto_20",
                "profile": "PJ", "accepted": True}
        r = _enviar_desfecho(cliente, {**base, campo: valor})
        assert r.status_code == 422
        assert r.json()["detail"]["campo"] == campo

    @pytest.mark.parametrize("accepted", ["true", "false", 1, 0, None, [], {}])
    def test_accepted_exige_booleano_de_verdade(self, cliente, accepted):
        """`"false"` é string não-vazia, logo `True` em Python: aceito, ele
        registraria aceite onde houve recusa e o bandit aprenderia o oposto."""
        r = _enviar_desfecho(cliente, {
            "user_id": "x", "offer_type": "desconto_20", "profile": "PJ",
            "accepted": accepted})
        assert r.status_code == 422

    @pytest.mark.parametrize("payload", [
        {"offer_type": "desconto_20", "profile": "PJ", "accepted": True},
        {"user_id": "", "offer_type": "desconto_20", "profile": "PJ", "accepted": True},
        {"user_id": ["x"], "offer_type": "desconto_20", "profile": "PJ", "accepted": True},
    ])
    def test_identidade_ausente_ou_torta_e_422(self, cliente, payload):
        assert _enviar_desfecho(cliente, payload).status_code == 422


# ── O desfecho real ──────────────────────────────────────────────────────

class TestDesfechoReal:

    def test_webhook_valido_move_o_posterior(self, cliente):
        linha = _disparar_ciclo(cliente)
        oferta = linha["offer_type"]
        alpha_antes = va._bandit.state[rl.TENANT_PADRAO]["PJ"][oferta]["alpha"]

        r = _enviar_desfecho(cliente, {
            "user_id": "cliente_teste", "offer_type": oferta,
            "profile": "PJ", "accepted": True})

        assert r.status_code == 200
        assert r.json()["status"] == "contabilizado"
        assert va._bandit.state[rl.TENANT_PADRAO]["PJ"][oferta]["alpha"] == alpha_antes + 1.0

    def test_recusa_move_o_beta(self, cliente):
        linha = _disparar_ciclo(cliente)
        oferta = linha["offer_type"]
        beta_antes = va._bandit.state[rl.TENANT_PADRAO]["PJ"][oferta]["beta"]

        _enviar_desfecho(cliente, {
            "user_id": "cliente_teste", "offer_type": oferta,
            "profile": "PJ", "accepted": False})

        assert va._bandit.state[rl.TENANT_PADRAO]["PJ"][oferta]["beta"] == beta_antes + 1.0

    def test_reenvio_nao_conta_duas_vezes(self, cliente):
        """Webhook reenvia — é o normal, não a exceção. Contar duas vezes
        enviesaria o posterior para quem reenvia mais."""
        linha = _disparar_ciclo(cliente)
        oferta = linha["offer_type"]
        payload = {"user_id": "cliente_teste", "offer_type": oferta,
                   "profile": "PJ", "accepted": True}

        primeira = _enviar_desfecho(cliente, payload)
        alpha_depois = va._bandit.state[rl.TENANT_PADRAO]["PJ"][oferta]["alpha"]
        segunda = _enviar_desfecho(cliente, payload)
        terceira = _enviar_desfecho(cliente, payload)

        assert primeira.json()["status"] == "contabilizado"
        assert segunda.json()["status"] == "ignorado"
        assert terceira.json()["status"] == "ignorado"
        assert segunda.status_code == 200, "reenvio não é erro do cliente"
        assert va._bandit.state[rl.TENANT_PADRAO]["PJ"][oferta]["alpha"] == alpha_depois

    def test_desfecho_orfao_nao_ensina(self, cliente):
        """Desfecho de uma oferta que este sistema nunca fez: aprender com
        isso seria aprender com dado de origem desconhecida."""
        antes = va._bandit.conversion_rates(rl.TENANT_PADRAO, "CLT")

        r = _enviar_desfecho(cliente, {
            "user_id": "nunca_visto", "offer_type": "desconto_10",
            "profile": "CLT", "accepted": True})

        assert r.json()["status"] == "ignorado"
        assert va._bandit.conversion_rates(rl.TENANT_PADRAO, "CLT") == antes

    def test_aceite_alimenta_o_historico_de_canal(self, cliente):
        """O que `track_outcome` fazia no grafo, agora pelo caminho real — e o
        canal vem da linha do log, que o webhook não carrega."""
        linha = _disparar_ciclo(cliente, on_site=True)

        _enviar_desfecho(cliente, {
            "user_id": "cliente_teste", "offer_type": linha["offer_type"],
            "profile": "PJ", "accepted": True})

        assert va._channel_history[
            va.chave_de_canal(rl.TENANT_PADRAO, "user:cliente_teste")] == linha["channel"]

    def test_recusa_nao_alimenta_o_historico(self, cliente):
        linha = _disparar_ciclo(cliente)
        _enviar_desfecho(cliente, {
            "user_id": "cliente_teste", "offer_type": linha["offer_type"],
            "profile": "PJ", "accepted": False})
        assert va._channel_history == {}

    def test_identidades_distintas_nao_se_misturam(self, cliente):
        """O invariante da A1-r10 pelo caminho NOVO.

        `test_webhook_security` media isso no `track_outcome`, que saiu do
        caminho de produção. A regra não mudou: o histórico de canal de um
        cliente não pode ser decidido por outro, e a qualificação da identidade
        (`user:` / `anon:`) é o que separa os dois.
        """
        linha = _disparar_ciclo(cliente, user_id="colisao", on_site=True)

        _enviar_desfecho(cliente, {
            "user_id": "colisao", "offer_type": linha["offer_type"],
            "profile": "PJ", "accepted": True})

        assert list(va._channel_history) == [
            va.chave_de_canal(rl.TENANT_PADRAO, "user:colisao")], (
            "o desfecho gravou histórico sob identidade não qualificada")


# ── O dataset ────────────────────────────────────────────────────────────

class TestLogDeCiclos:

    def test_linha_guarda_as_features_da_decisao(self, cliente):
        """É o X do treino. Sem isso o sistema aprende e esquece: o
        `bandit_state.json` é placar agregado, não conjunto de dados."""
        linha = _disparar_ciclo(cliente)

        assert linha["days_since_last"] == 12.0
        assert linha["features_used_30d"] == 2.0
        assert linha["mrr"] == 550.0
        assert linha["billing_profile"] == "PJ"
        assert linha["event"] == "Cancellation Page Viewed"
        assert linha["risk_score"] == 0.90
        assert linha["criticality"] == "critico"
        assert linha["offer_type"] in ob.OFFERS
        assert linha["channel"] in ("popup", "email", "whatsapp")
        assert linha["tenant_id"] == rl.TENANT_PADRAO

    def test_desfecho_completa_a_mesma_linha(self, cliente):
        linha = _disparar_ciclo(cliente)

        _enviar_desfecho(cliente, {
            "user_id": "cliente_teste", "offer_type": linha["offer_type"],
            "profile": "PJ", "accepted": True})

        assert rl.estatisticas() == {"total": 1, "com_desfecho": 1,
                                     "aguardando": 0, "aceitos": 1}
        fechada = _ultima_linha()
        assert fechada["accepted"] == 1
        assert fechada["origem_desfecho"] == "webhook"
        assert fechada["desfecho_em"] is not None

    def test_risco_baixo_tambem_vira_linha(self, cliente):
        """Quem o sistema decidiu NÃO abordar entra no dataset com
        `offer_type` nulo. Sem essas linhas o dataset só teria quem passou pelo
        corte de 0.60, e o viés de seleção ficaria invisível em vez de
        mensurável. Ver README_treino.md."""
        corpo = json.dumps({
            "userId": "tranquilo", "event": "Session Started",
            "properties": {"billing_profile": "CLT", "days_since_last": 1,
                           "features_used_30d": 30},
        }).encode()
        r = cliente.post("/webhooks/segment", content=corpo, headers={
            "x-signature": _assinar(corpo, SEGREDO_SEGMENT),
            "content-type": "application/json"})

        assert r.status_code == 200
        linha = _ultima_linha()
        assert linha["offer_type"] is None
        assert linha["risk_score"] < 0.60
        assert linha["days_since_last"] == 1.0

    def test_mrr_torto_vira_nulo_e_nao_texto_na_coluna_real(self, cliente):
        """Gravar `"muito"` numa coluna REAL entrega ao treino um dataset que o
        pandas lê como `object` e ninguém entende por quê."""
        corpo = json.dumps({
            "userId": "mrr_torto", "event": "Cancellation Page Viewed",
            "properties": {"billing_profile": "PJ", "mrr": "muito"},
        }).encode()
        cliente.post("/webhooks/segment", content=corpo, headers={
            "x-signature": _assinar(corpo, SEGREDO_SEGMENT),
            "content-type": "application/json"})

        assert _ultima_linha()["mrr"] is None

    def test_falha_de_escrita_nao_derruba_o_ciclo(self, cliente, monkeypatch):
        """Perder uma linha de dataset é ruim; derrubar o ciclo de retenção de
        um cliente por causa do disco é pior."""
        def explode(*a, **k):
            raise OSError("disco cheio")

        monkeypatch.setattr(rl, "_conectar", explode)

        corpo = json.dumps({
            "userId": "sem_disco", "event": "Cancellation Page Viewed",
            "properties": {"billing_profile": "PJ"},
        }).encode()
        r = cliente.post("/webhooks/segment", content=corpo, headers={
            "x-signature": _assinar(corpo, SEGREDO_SEGMENT),
            "content-type": "application/json"})

        assert r.status_code == 200

    def test_ciclo_aberto_encontra_o_mais_recente(self, cliente):
        """Dois ciclos do mesmo cliente com a mesma oferta: o desfecho fecha o
        mais novo, não o antigo."""
        rl.registrar_ciclo({"tenant_id": "t", "user_id": "u", "profile": "PJ",
                            "offer_type": "desconto_20", "channel": "email",
                            "risk_score": 0.7, "offer_sent": True})
        rl.registrar_ciclo({"tenant_id": "t", "user_id": "u", "profile": "PJ",
                            "offer_type": "desconto_20", "channel": "whatsapp",
                            "risk_score": 0.9, "offer_sent": True})

        aberto = rl.ciclo_aberto("t", "u", "desconto_20")
        assert aberto["channel"] == "whatsapp"

        assert rl.registrar_desfecho("t", "u", "desconto_20", True) is True
        assert rl.ciclo_aberto("t", "u", "desconto_20")["channel"] == "email", (
            "fechou o ciclo errado — o antigo deveria continuar aberto")

    def test_tenant_padrao_ate_o_sprint_5(self, cliente):
        """O campo já existe e já é gravado; quem o preenche de verdade é o
        Sprint 5. Ligar o fio agora evita migrar o dataset depois."""
        assert _disparar_ciclo(cliente)["tenant_id"] == "default_tenant"

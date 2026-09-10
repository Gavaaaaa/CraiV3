"""tests/test_tenant_isolation_voluntary.py — uma empresa cliente não ensina a outra.

Até o Sprint 5, `self.state` do bandit era `{profile: {offer: {α,β}}}` —
GLOBAL. Todas as empresas clientes da CRAI dividiam o mesmo posterior: uma SaaS
jurídica ensinava a CRAI sobre a base de uma SaaS de e-commerce, e o que saía
era uma média que não servia a nenhuma das duas. O mesmo valia para o
`_channel_history` (chaveado só por `user_id`, que colide entre empresas) e
para o `thread_id` do checkpoint.

Agora o estado é `{tenant: {profile: {offer: {α,β}}}}` e o `tenant_id`
atravessa: borda da API → estado do grafo → bandit → histórico de canal →
dataset de treino → negócio do HubSpot.

**O que aqui é prova de regressão e o que é catraca.** Tudo reprova em
`6fa4ee3` (fim do Sprint 4) — `choose_offer` tinha outra assinatura e não havia
camada de tenant. Os que merecem nome próprio:

    test_dois_tenants_aprendem_separado    — o defeito central do sprint
    test_tenant_novo_comeca_do_benchmark   — herdar posterior alheio é o mesmo
                                             vazamento disfarçado de warm start
    test_formato_antigo_migra_sem_perder   — retrocompatibilidade: é o caminho
                                             que todo deploy existente percorre
    test_formato_antigo_corrompido_ainda_migra — o furo que a primeira versão do
                                             detector tinha, e que os testes de
                                             posterior malformado acharam
    test_tenant_declarado_e_torto_e_422    — cair no default em silêncio funde
                                             quem tentou se identificar com quem
                                             não se identificou

ISOLAMENTO: `MODELS_DIR` e `CRAI_RETENTION_DB` em `tmp_path`.

Uso:
    pytest tests/test_tenant_isolation_voluntary.py -v
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
from crai.churn_voluntary.offer_bandit import (
    OFFERS,
    PROFILES,
    SEED_PRIORS,
    TENANT_PADRAO,
    OfferBandit,
    sanear_estado,
)

SEGREDO = "segredo_de_teste"


def _assinar(corpo: bytes) -> str:
    return hmac.new(SEGREDO.encode(), corpo, hashlib.sha1).hexdigest()


@pytest.fixture(autouse=True)
def ambiente(tmp_path, monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SEGMENT_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setenv("RETENTION_OUTCOME_WEBHOOK_SECRET", SEGREDO)
    monkeypatch.setattr(ob, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "bandit_state.json")
    va._channel_history.clear()
    # `va._bandit` é global do MÓDULO: sem reset, um teste herda os posteriores
    # que outro moveu, e uma asserção sobre isolamento vira uma asserção sobre
    # ordem de execução. Foi o que aconteceu na primeira rodada deste arquivo.
    original = va._bandit.state
    va._bandit.state = {TENANT_PADRAO: ob._priors_de_benchmark()}
    yield
    va._bandit.state = original
    va._channel_history.clear()


@pytest.fixture
def estado_persistido(tmp_path):
    return tmp_path / "bandit_state.json"


@pytest.fixture
def cliente():
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c


def _evento(cliente, user_id="cliente", tenant=None, no_header=True, perfil="PJ"):
    corpo = {"userId": user_id, "event": "Cancellation Page Viewed",
             "properties": {"billing_profile": perfil, "on_site_now": True}}
    headers = {"content-type": "application/json"}
    if tenant and not no_header:
        corpo["tenant_id"] = tenant
    bruto = json.dumps(corpo).encode()
    headers["x-signature"] = _assinar(bruto)
    if tenant and no_header:
        headers["x-tenant-id"] = tenant
    return cliente.post("/webhooks/segment", content=bruto, headers=headers)


def _linhas() -> list:
    conn = sqlite3.connect(rl.caminho_do_banco())
    conn.row_factory = sqlite3.Row
    linhas = [dict(r) for r in
              conn.execute("SELECT * FROM ciclos_retencao ORDER BY id")]
    conn.close()
    return linhas


# ── O aprendizado ────────────────────────────────────────────────────────

class TestAprendizadoPorTenant:

    def test_dois_tenants_aprendem_separado(self):
        """O DEFEITO CENTRAL. Antes, este `record_outcome` movia o posterior que
        a outra empresa cliente também lia."""
        bandit = OfferBandit()
        antes_b = bandit.conversion_rates("tenant_b", "PJ")

        for _ in range(20):
            bandit.record_outcome("tenant_a", "PJ", "desconto_20", True)

        assert bandit.conversion_rates("tenant_b", "PJ") == antes_b, (
            "o aprendizado do tenant A vazou para o B")
        assert (bandit.conversion_rates("tenant_a", "PJ")["desconto_20"]
                > antes_b["desconto_20"]), "o tenant A não aprendeu nada"

    def test_tenant_novo_comeca_do_benchmark(self):
        """Herdar posterior alheio é o mesmo vazamento, disfarçado de warm start."""
        bandit = OfferBandit()
        for _ in range(50):
            bandit.record_outcome("veterano", "CLT", "pausa_1_mes", True)

        novo = bandit.conversion_rates("recem_chegado", "CLT")
        a, b = SEED_PRIORS["CLT"]["pausa_1_mes"]
        esperado = round((1.0 + a) / ((1.0 + a) + (1.0 + b)), 3)

        assert novo["pausa_1_mes"] == esperado
        assert novo["pausa_1_mes"] != bandit.conversion_rates("veterano", "CLT")["pausa_1_mes"]

    def test_escolha_de_oferta_usa_o_posterior_do_proprio_tenant(self):
        """Isolamento no caminho de LEITURA, não só no de escrita."""
        bandit = OfferBandit(seed=1)
        for _ in range(300):
            bandit.record_outcome("so_pausa", "CLT", "pausa_1_mes", True)
            bandit.record_outcome("so_pausa", "CLT", "desconto_20", False)

        escolhas_do_tenant = {bandit.choose_offer("so_pausa", "CLT", 0.8)
                              for _ in range(30)}
        assert escolhas_do_tenant == {"pausa_1_mes"}, (
            f"o tenant treinado não convergiu: {escolhas_do_tenant}")

        # O tenant virgem não herda essa convicção.
        virgens = {bandit.choose_offer("virgem", "CLT", 0.8) for _ in range(30)}
        assert len(virgens) > 1, "o tenant virgem herdou a convicção do treinado"

    def test_tenant_vazio_ou_none_cai_no_padrao(self):
        """Defesa em profundidade: se uma chamada esquecer o tenant, ela escreve
        no balde do 'não declarado' — nunca no de outra empresa."""
        bandit = OfferBandit()
        bandit.record_outcome("", "PJ", "desconto_10", True)
        bandit.record_outcome(None, "PJ", "desconto_10", True)

        assert set(bandit.state) == {TENANT_PADRAO}
        assert bandit.state[TENANT_PADRAO]["PJ"]["desconto_10"]["alpha"] == 6.0 + 2


# ── Retrocompatibilidade ─────────────────────────────────────────────────

class TestMigracaoDoFormatoAntigo:

    def test_formato_antigo_migra_sem_perder(self, estado_persistido):
        """O caminho que todo deploy existente percorre uma vez."""
        estado_persistido.write_text(json.dumps({
            "CLT": {"desconto_10": {"alpha": 30.0, "beta": 5.0}},
            "PJ":  {"pausa_1_mes": {"alpha": 12.0, "beta": 9.0}},
        }), encoding="utf-8")

        bandit = OfferBandit()
        assert bandit.load() is True

        assert set(bandit.state) == {TENANT_PADRAO}
        assert bandit.state[TENANT_PADRAO]["CLT"]["desconto_10"] == {"alpha": 30.0, "beta": 5.0}
        assert bandit.state[TENANT_PADRAO]["PJ"]["pausa_1_mes"] == {"alpha": 12.0, "beta": 9.0}

    @pytest.mark.parametrize("antigo", [
        {"CLT": {"desconto_10": {"alpha": 1.0}}},          # posterior sem beta
        {"CLT": {"desconto_10": [10, 12]}},                # formato de tupla
        {"CLT": {"desconto_10": {"alpha": "x", "beta": 1}}},
        {"CLT": {}},                                       # perfil vazio
        {"CLT": "não é um dicionário"},
    ])
    def test_formato_antigo_corrompido_ainda_migra(self, estado_persistido, antigo):
        """REGRESSÃO do detector. A primeira versão olhava se o TERCEIRO nível
        tinha `alpha`/`beta`, e lia estes cinco casos como formato NOVO —
        criando um tenant chamado `CLT` e partindo o aprendizado do cliente em
        dois baldes em silêncio. O discriminador certo é o SEGUNDO nível, porque
        OFFERS e PROFILES são vocabulários fechados e disjuntos.
        """
        estado_persistido.write_text(json.dumps(antigo), encoding="utf-8")

        bandit = OfferBandit()
        bandit.load()

        assert set(bandit.state) == {TENANT_PADRAO}, (
            f"{antigo} foi lido como formato novo — tenant fantasma criado")

    def test_formato_novo_nao_e_remigrado(self, estado_persistido):
        """CATRACA: rodar duas vezes não pode aninhar `default_tenant` dentro
        de `default_tenant`."""
        estado_persistido.write_text(json.dumps({
            "acme": {"CLT": {"desconto_10": {"alpha": 3.0, "beta": 4.0}}},
            TENANT_PADRAO: {"PJ": {"desconto_20": {"alpha": 5.0, "beta": 5.0}}},
        }), encoding="utf-8")

        bandit = OfferBandit()
        bandit.load()

        assert set(bandit.state) == {"acme", TENANT_PADRAO}
        assert bandit.state["acme"]["CLT"]["desconto_10"] == {"alpha": 3.0, "beta": 4.0}

    def test_saneamento_continua_valendo_dentro_do_tenant(self, estado_persistido):
        """O que o Sprint 1 limpava por perfil agora é limpo por tenant."""
        estado_persistido.write_text(json.dumps({
            "acme": {"CLT": {"consulta_cs": {"alpha": 99.0, "beta": 1.0},
                             "desconto_10": {"alpha": 3.0, "beta": 4.0}},
                     "299,90": {"desconto_10": {"alpha": 2.0, "beta": 1.0}}},
        }), encoding="utf-8")

        bandit = OfferBandit()
        bandit.load()

        assert "consulta_cs" not in bandit.state["acme"]["CLT"]
        assert set(bandit.state["acme"]) == set(PROFILES)
        assert bandit.state["acme"]["CLT"]["desconto_10"] == {"alpha": 3.0, "beta": 4.0}

    def test_tenant_torto_no_arquivo_e_descartado(self):
        """Tenant não tem vocabulário fechado, então a validação aqui é de
        FORMA. Quem restringe o conteúdo é a borda da API."""
        _, descartes = sanear_estado({
            "": {"CLT": {"desconto_10": {"alpha": 2.0, "beta": 2.0}}},
            "  ": {"CLT": {}},
            "ok": {"CLT": {"desconto_10": {"alpha": 2.0, "beta": 2.0}}},
        })
        assert len(descartes["tenants"]) == 2


# ── A borda da API ───────────────────────────────────────────────────────

class TestTenantNaBorda:

    def test_ausente_cai_no_padrao(self, cliente):
        assert _evento(cliente).status_code == 200
        assert _linhas()[-1]["tenant_id"] == TENANT_PADRAO

    def test_header_x_tenant_id(self, cliente):
        assert _evento(cliente, tenant="acme").status_code == 200
        assert _linhas()[-1]["tenant_id"] == "acme"

    def test_tenant_id_no_corpo(self, cliente):
        assert _evento(cliente, tenant="acme", no_header=False).status_code == 200
        assert _linhas()[-1]["tenant_id"] == "acme"

    def test_header_vence_o_corpo(self, cliente):
        corpo = {"userId": "u", "event": "Cancellation Page Viewed",
                 "tenant_id": "do_corpo",
                 "properties": {"billing_profile": "PJ", "on_site_now": True}}
        bruto = json.dumps(corpo).encode()
        r = cliente.post("/webhooks/segment", content=bruto, headers={
            "x-signature": _assinar(bruto), "x-tenant-id": "do_header",
            "content-type": "application/json"})

        assert r.status_code == 200
        assert _linhas()[-1]["tenant_id"] == "do_header"

    @pytest.mark.parametrize("tenant", [
        "com espaço", "com:dois_pontos", "com/barra", "acentuação",
        # Sem emoji na lista: a catraca do N-12 varre os `.py` do repositório
        # inteiro, testes incluídos, e o inventário declarado só pode DESCER.
        # "acentuação" já cobre não-ASCII (e é representável em cp1252); "#"
        # cobre o caractere fora do conjunto permitido sem custo de encoding.
        "x" * 65, "tenant#hash", "tab\tinterno", "aspas\"",
    ])
    def test_tenant_declarado_e_torto_e_422(self, cliente, tenant):
        """Cair no default em silêncio fundiria quem TENTOU se identificar com
        quem não se identificou — o vazamento que esta camada existe para
        impedir. Errar alto é a única leitura correta.

        `:` está na lista porque a chave do `_channel_history` é
        `f"{tenant}:{user}"`: um `:` no tenant tornaria a chave ambígua.
        """
        # Pelo CORPO, e não pelo header: header HTTP é latin-1 por especificação,
        # e um cliente real nem consegue ENVIAR `x-tenant-id: acentuação` — a
        # codificação falha antes de sair da máquina dele. O caminho alcançável
        # para esses valores é o JSON, e é ele que precisa recusar.
        r = _evento(cliente, tenant=tenant, no_header=False)
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "tenant_com_forma_invalida"

    def test_default_tenant_vindo_de_fora_e_recusado(self, cliente):
        """É o balde do 'não declarado'. Ninguém deve entrar nele de propósito."""
        r = _evento(cliente, tenant=TENANT_PADRAO)
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "tenant_reservado"

    @pytest.mark.parametrize("tenant", ["acme", "acme-corp", "acme_corp",
                                        "acme.com.br", "a", "x" * 64,
                                        "550e8400-e29b-41d4-a716-446655440000"])
    def test_formas_legitimas_de_tenant(self, cliente, tenant):
        """Slug e UUID, que é o que um tenant realmente é, cabem folgados."""
        assert _evento(cliente, tenant=tenant).status_code == 200

    def test_simulate_aceita_tenant(self, cliente):
        r = cliente.post("/simulate/churn-risk",
                         json={"user_id": "demo", "tenant_id": "acme"})
        assert r.status_code == 200
        assert _linhas()[-1]["tenant_id"] == "acme"


# ── A propagação ponta a ponta ───────────────────────────────────────────

class TestPropagacao:

    def test_mesmo_user_id_em_tenants_diferentes_nao_colide(self, cliente,
                                                            monkeypatch):
        """Dois clientes de empresas diferentes podem ter o mesmo `user_id`.

        Sem o tenant na chave, a memória de canal de um decidiria o envio do
        outro, e os dois dividiriam o checkpoint. É o P0-6 um nível acima.
        """
        monkeypatch.setenv("CRAI_SIMULATE_OUTCOMES", "1")
        import random
        monkeypatch.setattr(random, "random", lambda: 0.0)   # força o aceite

        _evento(cliente, user_id="joao", tenant="empresa_a")
        _evento(cliente, user_id="joao", tenant="empresa_b")

        assert len(va._channel_history) == 2, (
            f"os dois tenants dividiram a memória de canal: {va._channel_history}")
        assert set(va._channel_history) == {
            va.chave_de_canal("empresa_a", "user:joao"),
            va.chave_de_canal("empresa_b", "user:joao"),
        }

    def test_bandit_aprende_no_tenant_do_evento(self, cliente, monkeypatch):
        monkeypatch.setenv("CRAI_SIMULATE_OUTCOMES", "1")

        _evento(cliente, tenant="acme")

        assert "acme" in va._bandit.state, "o evento não criou o tenant no bandit"

    def test_dataset_grava_o_tenant_real(self, cliente):
        """Era `default_tenant` para todo mundo até este sprint."""
        _evento(cliente, user_id="a", tenant="empresa_a")
        _evento(cliente, user_id="b", tenant="empresa_b")

        assert [l["tenant_id"] for l in _linhas()] == ["empresa_a", "empresa_b"]

    def test_deal_do_hubspot_carrega_o_tenant(self, cliente, monkeypatch):
        """Um HubSpot compartilhado sem isto mistura os negócios de dois
        clientes no mesmo pipeline, sem nada que os separe no relatório."""
        capturado = {}

        async def espiao(name, pipeline, stage, props):
            capturado.update(props)
            return "deal_falso"

        monkeypatch.setattr(va._hubspot, "create_deal", espiao)
        _evento(cliente, tenant="acme")

        assert capturado["tenant_id"] == "acme"

    def test_desfecho_de_um_tenant_nao_fecha_o_ciclo_do_outro(self, cliente):
        """O ciclo aberto é procurado por (tenant, user, oferta). Sem o tenant
        na busca, o desfecho da empresa A fecharia o ciclo da empresa B."""
        _evento(cliente, user_id="joao", tenant="empresa_a")
        _evento(cliente, user_id="joao", tenant="empresa_b")
        linhas = _linhas()
        oferta_a = linhas[0]["offer_type"]
        antes_b = va._bandit.conversion_rates("empresa_b", "PJ")

        corpo = json.dumps({"user_id": "joao", "offer_type": oferta_a,
                            "profile": "PJ", "accepted": True,
                            "tenant_id": "empresa_a"}).encode()
        r = cliente.post("/webhooks/retention-outcome", content=corpo, headers={
            "x-signature": _assinar(corpo), "content-type": "application/json"})
        assert r.status_code == 200
        assert r.json()["status"] == "contabilizado"

        depois = _linhas()
        assert depois[0]["accepted"] == 1, "o ciclo do tenant A não fechou"
        assert depois[1]["accepted"] is None, (
            "o desfecho do tenant A fechou o ciclo do tenant B")

        assert va._bandit.conversion_rates("empresa_b", "PJ") == antes_b, (
            "o desfecho do tenant A moveu o posterior do tenant B")

    def test_state_declara_tenant_id(self):
        from crai.churn_voluntary.state import ChurnVoluntaryState

        assert ChurnVoluntaryState.__annotations__["tenant_id"] is str


# ── As assinaturas ───────────────────────────────────────────────────────

class TestAssinaturasMudaram:
    """`tenant_id` é POSICIONAL nos três métodos, e isso é escolha.

    Um default silencioso deixaria uma chamada esquecida escrevendo no tenant
    errado sem erro nenhum — o pior desfecho possível numa camada de
    isolamento. Com posicional, esquecer é `TypeError` na hora.
    """

    @pytest.mark.parametrize("chamada", [
        lambda b: b.choose_offer("PJ", 0.8),
        lambda b: b.record_outcome("PJ", "desconto_20", True),
        lambda b: b.conversion_rates("PJ"),
    ])
    def test_chamada_sem_tenant_falha_alto(self, chamada):
        with pytest.raises(TypeError):
            chamada(OfferBandit())

    def test_ordem_dos_parametros(self):
        import inspect

        for metodo, esperado in [
            (OfferBandit.choose_offer, ["self", "tenant_id", "profile", "risk_score", "mrr"]),
            (OfferBandit.record_outcome, ["self", "tenant_id", "profile", "offer", "accepted"]),
            (OfferBandit.conversion_rates, ["self", "tenant_id", "profile"]),
        ]:
            assert list(inspect.signature(metodo).parameters) == esperado

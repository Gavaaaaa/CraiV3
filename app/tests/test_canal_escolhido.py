"""tests/test_canal_escolhido.py — o canal escolhido de verdade, e por quê.

O que a etapa do canal promete, e o que aqui é verificado:

  (a) VOLUNTÁRIO — `choose_channel` devolve `canais_considerados`: os três
      canais do fluxo, exatamente um escolhido, cada um com motivo. Dois
      clientes com dados diferentes (telefone, no site ou não) recebem canais
      diferentes.
  (b) A rota `/simulate/painel/evento-risco` para de mentir: aceita `phone`
      e `on_site_now`, e com dados diferentes o canal muda. Sem nada, o
      padrão continua sendo o de antes (no site, sem telefone: popup). A
      resposta traz os canais considerados e as candidatas de mensagem.
  (c) INVOLUNTÁRIO — o resultado de `_find_optimal_channel`, que morria
      entre o classificador e o grafo, chega ao estado como
      `canais_considerados`. O envio continua pelo bot de WhatsApp por
      limitação de integração, e a lista DIZ isso, canal a canal.
  (d) INVARIANTE: `ligacao_cs` é canal humano e nunca é o canal escolhido —
      em nenhum caminho, nem quando o custo dele for o menor da tabela.

ISOLAMENTO: nenhum teste chama a Claude API; o bandit persistido não é
tocado; a rota do painel roda com `ENV=development` via monkeypatch.

Uso:
    pytest tests/test_canal_escolhido.py -v
"""

import itertools

import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow
from crai.agent.workflow import canais_considerados_involuntario
from crai.api import app as app_module
from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary import voluntary_agent as va
from crai.config import CANAIS_HUMANOS, CANAL_PADRAO, CUSTOS_PADRAO
from crai.dunning import dunning_engine
from crai.ml import failure_classifier as fc
from crai.ml.failure_classifier import FailureClassifier

CANAIS_VOLUNTARIO = {"whatsapp", "popup", "email"}
TELEFONE = "11912345678"


@pytest.fixture(autouse=True)
def isolamento(monkeypatch, tmp_path):
    """Bandit em `tmp_path`, Claude API fora do ar nos dois fluxos, memória de
    canal limpa — nenhum teste herda histórico de outro."""
    monkeypatch.setattr(ob, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "bandit_state.json")

    async def api_fora(**kwargs):
        raise RuntimeError("Claude fora do ar (teste)")

    monkeypatch.setattr(va.claude.messages, "create", api_fora)
    monkeypatch.setattr(dunning_engine.claude.messages, "create", api_fora)
    monkeypatch.setattr(va, "_channel_history", {})


def _estado(phone=None, on_site=False, criticality="critico", user_id="usr_a"):
    props = {"billing_profile": "PJ", "on_site_now": on_site}
    if phone:
        props["phone"] = phone
    return {
        "tenant_id": "t_teste", "user_id": user_id, "event": "Cancellation Page Viewed",
        "props": props, "risk_score": 0.9, "profile": "PJ", "is_critical": True,
        "criticality": criticality, "offer_type": "desconto_20", "channel": None,
        "on_site_now": on_site, "prior_channel_success": None, "message": None,
        "offer_sent": False, "accepted": None, "retained": False,
    }


def _um_so_escolhido(canais, esperado):
    escolhidos = [c["canal"] for c in canais if c["escolhido"]]
    assert escolhidos == [esperado], canais
    for c in canais:
        assert c["motivo"].strip(), f"canal {c['canal']} sem motivo"


# ── (a) voluntário: o nó ─────────────────────────────────────────────────

class TestCanalVoluntario:

    @pytest.mark.asyncio
    async def test_dois_clientes_dados_diferentes_canais_diferentes(self):
        com_telefone = await va.choose_channel(_estado(phone=TELEFONE, on_site=True))
        no_site = await va.choose_channel(_estado(on_site=True, user_id="usr_b"))
        fora = await va.choose_channel(_estado(on_site=False, user_id="usr_c"))

        assert com_telefone["channel"] == "whatsapp"
        assert no_site["channel"] == "popup"
        assert fora["channel"] == "email"

    @pytest.mark.asyncio
    async def test_canais_considerados_um_escolhido_todos_com_motivo(self):
        resultado = await va.choose_channel(_estado(phone=TELEFONE, on_site=True))
        canais = resultado["canais_considerados"]
        assert [c["canal"] for c in canais] == ["whatsapp", "popup", "email"]
        _um_so_escolhido(canais, "whatsapp")

    @pytest.mark.asyncio
    async def test_motivo_do_descarte_diz_o_que_faltou(self):
        resultado = await va.choose_channel(_estado(on_site=False, criticality="critico"))
        motivos = {c["canal"]: c["motivo"] for c in resultado["canais_considerados"]}
        assert "telefone" in motivos["whatsapp"]
        assert "não está no produto" in motivos["popup"]
        assert "reserva" in motivos["email"]

    @pytest.mark.asyncio
    async def test_telefone_sem_criticidade_nao_vira_whatsapp_e_diz_por_que(self):
        resultado = await va.choose_channel(_estado(phone=TELEFONE, on_site=True,
                                                    criticality="padrao"))
        assert resultado["channel"] == "popup"
        whatsapp = next(c for c in resultado["canais_considerados"] if c["canal"] == "whatsapp")
        assert not whatsapp["escolhido"]
        assert "criticidade 'padrao'" in whatsapp["motivo"]

    @pytest.mark.asyncio
    async def test_historico_aparece_no_motivo(self):
        va._channel_history[va.chave_de_canal("t_teste", "usr_h")] = "email"
        resultado = await va.choose_channel(_estado(on_site=True, criticality="padrao",
                                                    user_id="usr_h"))
        assert resultado["channel"] == "email"
        email = next(c for c in resultado["canais_considerados"] if c["canal"] == "email")
        assert "histórico" in email["motivo"]

    @pytest.mark.asyncio
    async def test_varredura_nunca_devolve_canal_humano(self):
        """INVARIANTE (d) no voluntário: toda combinação de telefone, presença,
        criticidade e histórico devolve um dos três canais — nunca humano."""
        combinacoes = itertools.product(
            [None, TELEFONE], [True, False], ["padrao", "alto", "critico"],
            [None, "popup", "email", "whatsapp"])
        for i, (phone, on_site, crit, prior) in enumerate(combinacoes):
            uid = f"usr_{i}"
            if prior:
                va._channel_history[va.chave_de_canal("t_teste", uid)] = prior
            resultado = await va.choose_channel(_estado(phone, on_site, crit, uid))
            assert resultado["channel"] in CANAIS_VOLUNTARIO, (phone, on_site, crit, prior)
            assert resultado["channel"] not in CANAIS_HUMANOS
            _um_so_escolhido(resultado["canais_considerados"], resultado["channel"])


# ── (b) a rota do painel ─────────────────────────────────────────────────

class TestRotaDoPainel:

    @pytest.fixture
    def cliente(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        with TestClient(app_module.app, raise_server_exceptions=False) as c:
            yield c

    def test_sem_nada_o_padrao_continua_popup(self, cliente):
        r = cliente.post("/simulate/painel/evento-risco", json={})
        assert r.status_code == 200, r.text
        assert r.json()["channel"] == "popup"

    def test_dados_diferentes_canais_diferentes(self, cliente):
        com_telefone = cliente.post("/simulate/painel/evento-risco",
                                    json={"phone": TELEFONE}).json()
        fora_do_site = cliente.post("/simulate/painel/evento-risco",
                                    json={"on_site_now": False}).json()
        no_site = cliente.post("/simulate/painel/evento-risco", json={}).json()

        assert com_telefone["channel"] == "whatsapp"
        assert fora_do_site["channel"] == "email"
        assert no_site["channel"] == "popup"

    def test_resposta_traz_canais_considerados_e_candidatas(self, cliente):
        r = cliente.post("/simulate/painel/evento-risco", json={"phone": TELEFONE}).json()
        _um_so_escolhido(r["canais_considerados"], "whatsapp")
        assert len(r["candidatas"]) == 3
        escolhidas = [c for c in r["candidatas"] if c["escolhida"]]
        assert len(escolhidas) == 1
        assert escolhidas[0]["oferta"] == r["offer_type"]
        assert escolhidas[0]["texto"] == r["message"]
        for c in r["candidatas"]:
            assert va.encaminha_para_humano(c["texto"]) is None

    def test_canal_da_rota_nunca_e_humano(self, cliente):
        for corpo in ({}, {"phone": TELEFONE}, {"on_site_now": False},
                      {"phone": TELEFONE, "on_site_now": False}):
            r = cliente.post("/simulate/painel/evento-risco", json=corpo).json()
            assert r["channel"] in CANAIS_VOLUNTARIO
            assert r["channel"] not in CANAIS_HUMANOS

    def test_fora_de_development_continua_bloqueada(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        with TestClient(app_module.app, raise_server_exceptions=False) as c:
            assert c.post("/simulate/painel/evento-risco",
                          json={"phone": TELEFONE}).status_code == 403


# ── (c) involuntário: o comparativo deixa de morrer ──────────────────────

def _optimal(melhor="email_auto", p=0.6, ltv=1800.0):
    return {"channel": melhor, "eprofit": round(p * ltv - CUSTOS_PADRAO[melhor], 2),
            "all_channels": {c: round(p * ltv - custo, 2) for c, custo in CUSTOS_PADRAO.items()}}


class TestComparativoInvoluntario:

    def test_todos_os_canais_um_escolhido_e_e_o_integrado(self):
        canais = canais_considerados_involuntario(_optimal())
        assert {c["canal"] for c in canais} == set(CUSTOS_PADRAO)
        _um_so_escolhido(canais, CANAL_PADRAO)

    def test_o_escolhido_diz_que_e_por_integracao_nao_por_eprofit(self):
        canais = {c["canal"]: c for c in canais_considerados_involuntario(_optimal())}
        assert "integração" in canais[CANAL_PADRAO]["motivo"]
        assert canais["email_auto"]["melhor_eprofit"] is True
        assert "maior e-Profit" in canais["email_auto"]["motivo"]
        assert "sem integração" in canais["email_auto"]["motivo"]

    def test_eprofit_de_cada_canal_viaja(self):
        opt = _optimal()
        for c in canais_considerados_involuntario(opt):
            assert c["eprofit"] == opt["all_channels"][c["canal"]]

    def test_sem_comparativo_a_lista_existe_mesmo_assim(self):
        """Fallback heurístico do classificador: `all_channels` vazio. A
        lista sai completa, com e-Profit `None`, nunca some."""
        canais = canais_considerados_involuntario(None)
        assert len(canais) == len(CUSTOS_PADRAO)
        _um_so_escolhido(canais, CANAL_PADRAO)
        assert all(c["eprofit"] is None for c in canais)

    @pytest.mark.asyncio
    async def test_diagnose_failure_carrega_o_comparativo_no_estado(self, monkeypatch):
        """A REGRESSÃO que esta etapa corrige: `predict` devolvia
        `optimal_channel` e o nó não copiava para o estado."""
        opt = _optimal()

        def predict_falso(features, channel="bot_whatsapp"):
            return {"recovery_score": 60, "p_recovery": 0.6, "eprofit": 100.0,
                    "recommend_action": True, "ltv_estimated": 1800.0,
                    "shap_explanation": {"features": [], "readable": ""},
                    "optimal_channel": opt, "method": "falso", "channel": channel}

        monkeypatch.setattr(workflow._classifier, "predict", predict_falso)
        estado = {"payment_event": {"gateway_error_code": "insufficient_funds",
                                    "codigo_falha": "AM04", "degradacoes": []},
                  "amount": 299.9, "payment_method": "pix_automatico",
                  "customer_id": "cli_teste", "tenant_id": "t_teste"}
        resultado = await workflow.diagnose_failure(estado)
        assert "canais_considerados" in resultado
        _um_so_escolhido(resultado["canais_considerados"], CANAL_PADRAO)


# ── (d) INVARIANTE: canal humano nunca é escolhido ───────────────────────

class TestCanalHumanoNuncaEscolhido:

    def test_ligacao_cs_e_declarado_humano(self):
        assert "ligacao_cs" in CANAIS_HUMANOS
        assert CANAL_PADRAO not in CANAIS_HUMANOS

    def test_comparativo_nunca_escolhe_ligacao_cs(self):
        for melhor in CUSTOS_PADRAO:
            canais = canais_considerados_involuntario(_optimal(melhor))
            ligacao = next(c for c in canais if c["canal"] == "ligacao_cs")
            assert ligacao["escolhido"] is False
            assert "humano" in ligacao["motivo"]

    def test_find_optimal_channel_nunca_devolve_ligacao_cs(self):
        clf = FailureClassifier()
        for p in (0.05, 0.3, 0.6, 0.95):
            for ltv in (50.0, 500.0, 5000.0):
                assert clf._find_optimal_channel(p, ltv)["channel"] != "ligacao_cs"

    def test_mesmo_com_custo_rigado_ligacao_cs_nao_vence(self, monkeypatch):
        """O caso que a tabela de custos hoje não produz, mas que uma env
        futura poderia: ligação mais barata que tudo. Continua não elegível."""
        rigado = {**CUSTOS_PADRAO, "ligacao_cs": 0.0}
        monkeypatch.setattr(fc, "custos_por_canal", lambda: rigado)
        opt = FailureClassifier()._find_optimal_channel(0.8, 2000.0)
        assert opt["channel"] != "ligacao_cs"
        assert opt["channel"] in CUSTOS_PADRAO
        assert "ligacao_cs" in opt["all_channels"], "o comparativo mostra; só não escolhe"

    def test_rota_de_cobranca_do_painel_nunca_escolhe_humano(self, monkeypatch):
        """Ponta a ponta no grafo involuntário real, com a Claude API fora do
        ar: o canal de envio e a lista do comparativo saem sem humano."""
        monkeypatch.setenv("ENV", "development")
        with TestClient(app_module.app, raise_server_exceptions=False) as c:
            r = c.post("/simulate/painel/cobranca-falhada",
                       json={"valor": 299.9, "codigo_falha": "AM04"})
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["channel"] not in CANAIS_HUMANOS
        assert corpo["canais_considerados"], "o comparativo tem que chegar ao painel"
        _um_so_escolhido(corpo["canais_considerados"], CANAL_PADRAO)
        ligacao = next(c for c in corpo["canais_considerados"] if c["canal"] == "ligacao_cs")
        assert ligacao["escolhido"] is False

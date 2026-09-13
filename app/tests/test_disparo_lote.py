"""tests/test_disparo_lote.py — "trate todos os clientes que precisam".

O que a rota `POST /simulate/painel/disparo-lote` promete, e o que aqui é
verificado:

  (a) INCLUSÃO POR CRITICIDADE: `critico` e `alto` são tratados; `padrao` é
      pulado com motivo. Não é corte numérico de risco.
  (b) SEM SINAL, SEM CONTATO: `dado_insuficiente` é pulado, nunca tratado, e
      o motivo diz o que faltou.
  (c) por cliente tratado: identificador, mensagem escolhida, canal, valor
      mensal, candidatas e canais considerados; o resumo fecha a conta
      (processados + pulados = recebidos).
  (d) uma linha torta vira `pulados` com motivo, não 422.
  (e) o envio é REGISTRADO no `retention_log` e rodar duas vezes não contata
      ninguém duas vezes (`ciclo_aberto`).
  (f) invariantes: nenhum canal humano, nenhum texto que encaminhe para
      humano — em todo o lote.
  (g) mesmos portões das outras rotas do painel (403 fora de dev/demo);
      lote acima do teto é 413; `limite` corta os piores primeiro.
  (h) sem corpo, o lote é a base importada do tenant do painel.
  (i) tempo: 2.000 clientes ficam em segundos, sem LLM.

ISOLAMENTO: `conftest` já redireciona o `retention_log` e a base importada
para `tmp_path`; aqui o bandit também vai para lá e a Claude API fica fora.

Uso:
    pytest tests/test_disparo_lote.py -v
"""

import time

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.api.app import TENANT_PAINEL
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import disparo_lote as dl
from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import voluntary_agent as va
from crai.config import CANAIS_HUMANOS

TELEFONE = "11912345678"


@pytest.fixture(autouse=True)
def isolamento(monkeypatch, tmp_path):
    monkeypatch.setattr(ob, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "bandit_state.json")
    monkeypatch.setattr(va, "_channel_history", {})
    monkeypatch.setenv("ENV", "development")

    async def api_fora(**kwargs):
        raise RuntimeError("Claude fora do ar (teste)")

    monkeypatch.setattr(va.claude.messages, "create", api_fora)


@pytest.fixture
def cliente():
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c


def _c(cid, mrr=300.0, perfil="CLT", dias=None, uso=None, **extra):
    return {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil,
            "days_since_last": dias, "features_used_30d": uso, **extra}


# Régua global (lista pequena, `risco_por_features`): 45 dias e 0 features
# dá 1,0 (crítico); 30 dias e 2 features dá 0,88 (alto); 0 dias e 30
# features dá padrão. Medido, não suposto.
LOTE_BASICO = [
    _c("critico-com-fone", dias=45, uso=0, phone=TELEFONE),
    _c("critico-sem-fone", dias=45, uso=0),
    _c("alto", dias=30, uso=2, mrr=450.0, perfil="PJ"),
    _c("padrao", dias=0, uso=30),
    _c("sem-dado"),
    _c("valor-alto", dias=1, uso=25, mrr=2500.0),   # crítico pela porta do MRR
]


def _post(cliente, corpo):
    r = cliente.post("/simulate/painel/disparo-lote", json=corpo)
    assert r.status_code == 200, r.text
    return r.json()


# ── (a) e (b): quem entra, quem é pulado ─────────────────────────────────

class TestCriterioDeInclusao:

    def test_critico_e_alto_entram_padrao_e_sem_dado_nao(self, cliente):
        corpo = _post(cliente, {"clientes": LOTE_BASICO})
        tratados = {c["customer_id_externo"] for c in corpo["clientes"]}
        pulados = {p["customer_id_externo"]: p for p in corpo["pulados"]}

        assert tratados == {"critico-com-fone", "critico-sem-fone", "alto", "valor-alto"}
        assert pulados["padrao"]["motivo"] == "abaixo_do_criterio"
        assert "padrao" in pulados["padrao"]["detalhe"]
        assert pulados["sem-dado"]["motivo"] == "dado_insuficiente"

    def test_sem_sinal_nunca_e_tratado_e_o_motivo_diz_o_que_faltou(self, cliente):
        corpo = _post(cliente, {"clientes": [_c("a"), _c("b", mrr=9000.0)]})
        assert corpo["clientes"] == []
        assert corpo["resumo"]["processados"] == 0
        assert corpo["resumo"]["por_motivo"] == {"dado_insuficiente": 2}
        for p in corpo["pulados"]:
            assert "days_since_last" in p["detalhe"] and "não contatado" in p["detalhe"]

    def test_o_criterio_e_a_criticidade_nao_o_risco(self, cliente):
        """Crítico pela porta do VALOR (risco baixo, MRR alto) é tratado. Um
        corte por risco numérico o deixaria de fora."""
        corpo = _post(cliente, {"clientes": [_c("valor-alto", dias=1, uso=25, mrr=2500.0)]})
        assert [c["customer_id_externo"] for c in corpo["clientes"]] == ["valor-alto"]
        assert corpo["clientes"][0]["criticality"] == "critico"
        assert corpo["criterio"] == "criticidade critico ou alto"


# ── (c): o que vem por cliente, e o resumo ───────────────────────────────

class TestRespostaPorCliente:

    def test_cada_tratado_tem_id_mensagem_canal_e_valor(self, cliente):
        corpo = _post(cliente, {"clientes": LOTE_BASICO})
        for c in corpo["clientes"]:
            assert c["customer_id_externo"]
            assert c["mensagem"].strip()
            assert c["channel"] in {"whatsapp", "popup", "email"}
            assert isinstance(c["mrr"], float) and c["mrr"] > 0
            assert c["offer_type"] in ob.OFFERS
            assert len(c["candidatas"]) == 3
            assert sum(1 for k in c["candidatas"] if k["escolhida"]) == 1
            escolhida = next(k for k in c["candidatas"] if k["escolhida"])
            assert escolhida["texto"] == c["mensagem"]
            assert escolhida["origem_texto"] == "template"
            assert sum(1 for k in c["canais_considerados"] if k["escolhido"]) == 1
            assert c["envio"] == {"simulado": True, "registrado": True,
                                  "ciclo_id": c["envio"]["ciclo_id"]}
            assert isinstance(c["envio"]["ciclo_id"], int)

    def test_telefone_muda_o_canal_dentro_do_lote(self, cliente):
        corpo = _post(cliente, {"clientes": LOTE_BASICO})
        canal = {c["customer_id_externo"]: c["channel"] for c in corpo["clientes"]}
        assert canal["critico-com-fone"] == "whatsapp"
        assert canal["critico-sem-fone"] == "email"

    def test_resumo_fecha_a_conta(self, cliente):
        corpo = _post(cliente, {"clientes": LOTE_BASICO})
        r = corpo["resumo"]
        assert r["recebidos"] == len(LOTE_BASICO)
        assert r["processados"] + r["pulados"] == r["recebidos"]
        assert r["processados"] == len(corpo["clientes"])
        assert r["pulados"] == len(corpo["pulados"])
        assert sum(r["por_motivo"].values()) == r["pulados"]
        assert r["mrr_envolvido"] == round(sum(c["mrr"] for c in corpo["clientes"]), 2)
        assert sum(r["por_canal"].values()) == r["processados"]
        assert corpo["simulado"] is True and corpo["aviso"]
        assert corpo["origem"] == "corpo"


# ── (d): linha torta não derruba o lote ──────────────────────────────────

class TestLinhaTorta:

    def test_cadastro_invalido_vira_pulado_com_motivo(self, cliente):
        corpo = _post(cliente, {"clientes": [
            _c("ok", dias=45, uso=0),
            _c("perfil-ruim", dias=45, uso=0, perfil="MEI"),
            _c("mrr-ruim", dias=45, uso=0, mrr="abc"),
            _c("", dias=45, uso=0),
        ]})
        assert [c["customer_id_externo"] for c in corpo["clientes"]] == ["ok"]
        motivos = {p["customer_id_externo"]: p for p in corpo["pulados"]}
        assert motivos["perfil-ruim"]["motivo"] == "cadastro_invalido"
        assert "MEI" in motivos["perfil-ruim"]["detalhe"]
        assert motivos["mrr-ruim"]["motivo"] == "cadastro_invalido"
        assert corpo["resumo"]["por_motivo"]["cadastro_invalido"] == 3
        assert corpo["resumo"]["recebidos"] == 4

    def test_item_que_nao_e_objeto_e_422_do_contrato(self, cliente):
        """`clientes` é lista de objetos: um item que não é objeto JSON é
        requisição malformada (422 do pydantic), não uma linha torta."""
        r = cliente.post("/simulate/painel/disparo-lote",
                         json={"clientes": [_c("ok", dias=45, uso=0), "texto solto"]})
        assert r.status_code == 422


# ── (e): registro e idempotência ─────────────────────────────────────────

class TestRegistroDoEnvio:

    def test_envio_fica_no_retention_log(self, cliente):
        corpo = _post(cliente, {"clientes": LOTE_BASICO})
        registrados = {c["user_id"]: c for c in rl.ultimo_ciclo_por_cliente(TENANT_PAINEL)}
        for c in corpo["clientes"]:
            ciclo = registrados[f"user:{c['customer_id_externo']}"]
            assert ciclo["offer_type"] == c["offer_type"]
            assert ciclo["channel"] == c["channel"]
            assert ciclo["accepted"] is None, "aguardando retorno, não 'recusou'"
            assert ciclo["event"] == dl.EVENTO_LOTE

    def test_rodar_duas_vezes_nao_contata_duas_vezes(self, cliente):
        primeiro = _post(cliente, {"clientes": LOTE_BASICO})
        segundo = _post(cliente, {"clientes": LOTE_BASICO})
        assert primeiro["resumo"]["processados"] == 4
        assert segundo["resumo"]["processados"] == 0
        assert segundo["resumo"]["por_motivo"]["ciclo_aberto"] == 4
        assert len(rl.ultimo_ciclo_por_cliente(TENANT_PAINEL)) == 4

    def test_ciclo_fechado_libera_novo_contato(self, cliente):
        primeiro = _post(cliente, {"clientes": [_c("x", dias=45, uso=0)]})
        oferta = primeiro["clientes"][0]["offer_type"]
        assert rl.registrar_desfecho(TENANT_PAINEL, "user:x", oferta, False)
        segundo = _post(cliente, {"clientes": [_c("x", dias=45, uso=0)]})
        assert segundo["resumo"]["processados"] == 1


# ── (f): invariantes em todo o lote ──────────────────────────────────────

class TestInvariantesNoLote:

    def test_nenhum_canal_humano_nenhum_texto_que_encaminhe(self, cliente):
        lote = [_c(f"c{i}", dias=40 + i, uso=0, mrr=200.0 + i,
                   perfil=ob.PROFILES[i % 3], phone=TELEFONE if i % 2 else None)
                for i in range(60)]
        corpo = _post(cliente, {"clientes": lote})
        assert corpo["resumo"]["processados"] == 60
        for c in corpo["clientes"]:
            assert c["channel"] not in CANAIS_HUMANOS
            assert c["offer_type"] != "consulta_cs"
            for k in c["candidatas"]:
                assert va.encaminha_para_humano(k["texto"]) is None, k["texto"]
                assert k["oferta"] != "consulta_cs"
            for k in c["canais_considerados"]:
                assert not (k["escolhido"] and k["canal"] in CANAIS_HUMANOS)


# ── (g): portões e limites ───────────────────────────────────────────────

class TestPortoes:

    def test_fora_de_dev_e_demo_e_403(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        with TestClient(app_module.app, raise_server_exceptions=False) as c:
            r = c.post("/simulate/painel/disparo-lote", json={"clientes": LOTE_BASICO})
        assert r.status_code == 403

    def test_lote_acima_do_teto_e_413(self, cliente):
        lote = [_c(f"c{i}", dias=45, uso=0) for i in range(dl.LIMITE_LOTE + 1)]
        r = cliente.post("/simulate/painel/disparo-lote", json={"clientes": lote})
        assert r.status_code == 413
        assert r.json()["detail"]["motivo"] == "lote_grande_demais"

    def test_limite_corta_os_piores_primeiro(self, cliente):
        corpo = _post(cliente, {"clientes": LOTE_BASICO, "limite": 2})
        assert corpo["resumo"]["processados"] == 2
        assert corpo["resumo"]["nao_avaliados"] == len(LOTE_BASICO) - 2
        riscos = [c["risk_score"] for c in corpo["clientes"]]
        assert riscos == sorted(riscos, reverse=True)

    def test_limite_invalido_e_422(self, cliente):
        r = cliente.post("/simulate/painel/disparo-lote", json={"clientes": LOTE_BASICO, "limite": 0})
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "limite_invalido"


# ── (h): sem corpo, a base importada ─────────────────────────────────────

class TestBaseImportada:

    def test_sem_corpo_trata_a_base_do_tenant_do_painel(self, cliente):
        ci.gravar(TENANT_PAINEL, [
            _c("frio", dias=45, uso=0), _c("ativo", dias=0, uso=30), _c("sem-dado"),
        ])
        ci.gravar("outro-tenant", [_c("frio-de-outro", dias=45, uso=0)])

        r = cliente.post("/simulate/painel/disparo-lote")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["origem"] == "base_importada"
        assert [c["customer_id_externo"] for c in corpo["clientes"]] == ["frio"]
        assert {p["customer_id_externo"] for p in corpo["pulados"]} == {"ativo", "sem-dado"}
        assert "frio-de-outro" not in {c["customer_id_externo"] for c in corpo["clientes"]}

    def test_corpo_vazio_equivale_a_sem_corpo(self, cliente):
        ci.gravar(TENANT_PAINEL, [_c("frio", dias=45, uso=0)])
        corpo = _post(cliente, {})
        assert corpo["origem"] == "base_importada"
        assert corpo["resumo"]["processados"] == 1


# ── (i): tempo ───────────────────────────────────────────────────────────

class TestTempo:

    def test_dois_mil_clientes_em_segundos(self, cliente):
        """A decisão de não rodar o grafo por cliente, medida: 2.000 clientes
        tratados (todos críticos, todos registrados) em menos de 15 s."""
        lote = [_c(f"c{i}", dias=30 + (i % 40), uso=0, mrr=100.0 + i,
                   perfil=ob.PROFILES[i % 3], phone=TELEFONE if i % 3 == 0 else None)
                for i in range(2000)]
        inicio = time.perf_counter()
        corpo = _post(cliente, {"clientes": lote})
        duracao = time.perf_counter() - inicio
        assert corpo["resumo"]["processados"] == 2000
        assert duracao < 15, f"lote de 2.000 levou {duracao:.1f}s"
        print(f"[LOTE] 2.000 clientes em {duracao:.2f}s")

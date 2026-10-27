"""tests/test_mensagens_voluntario.py — as 3 mensagens do voluntário (14/10/2026).

O QUE ESTE ARQUIVO MEDE:

  M1  no modo "eu escolho", a oferta por e-mail ou WhatsApp NÃO sai na hora:
      espera a escolha, com 3 mensagens para a MESMA oferta e uma recomendada;
  M2  o pop-up não espera (o cliente está no site agora), e o modo automático
      envia como sempre;
  M3  escolher só aceita rodada e abordagem (nunca texto), só owner ou admin,
      e envia na hora; a oferta passa a contar como enviada;
  M4  "Quero outro tipo de mensagem" escreve outra rodada sem reiniciar o prazo;
  M5  o relógio envia a recomendada quando o prazo vence, e quando a empresa
      passa para o modo automático;
  M6  a oferta que espera vale como a oferta do período (limite de contato);
  M7  isolamento por empresa, nada de telefone na resposta, "não contatar"
      relido na hora do envio;
  M8  os textos nunca encaminham para uma pessoa nem dificultam o cancelamento;
      o texto gerado que falhar no filtro é trocado pelo modelo pronto;
  M9  LGPD: o texto sai no prazo de guarda e na anonimização do titular.
"""

import json
import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from crai.accounts import chaves_api
from crai.api import app as app_module
from crai.api import datas, relogio
from crai.churn_voluntary import mensagens_voluntario as mv
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import voluntary_agent as va
from crai.churn_voluntary import clientes_importados
from crai.dunning import configuracao

A, B = "empresa-a", "empresa-b"
CANCELAMENTO = "Cancellation Page Viewed"


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.delenv("CRAI_SIMULATE_OUTCOMES", raising=False)      # o grafo de produção
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)           # redator desligado
    monkeypatch.delenv(chaves_api.ENV_LIMITE, raising=False)
    monkeypatch.delenv(chaves_api.ENV_LIMITE_EVENTOS, raising=False)
    chaves_api.limpar_limites()
    va._channel_history.clear()
    for chave, valor in configuracao.PADROES_DE_PRODUCAO.items():
        monkeypatch.setitem(configuracao.PADROES, chave, valor)
    yield
    chaves_api.limpar_limites()
    va._channel_history.clear()


@pytest.fixture
def cliente(supabase_falso):
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        c.projeto = supabase_falso
        yield c


def _login(c, tenant=A, papel="owner"):
    return c.projeto.bearer(tenant, papel=papel, plano="premium")


def _chave(c, tenant=A) -> str:
    r = c.post("/integracao/chaves", json={"nome": "Servidor"}, headers=_login(c, tenant))
    assert r.status_code == 201, r.text
    return r.json()["chave_inteira"]


def _evento(c, chave, user="c-1", evento=CANCELAMENTO, **props):
    corpo = {"userId": user, "event": evento,
             "properties": {"mrr": 500.0, "billing_profile": "PJ", **props}}
    r = c.post("/eventos", json=corpo, headers={"Authorization": f"Bearer {chave}"})
    assert r.status_code == 200, r.text
    return r


def _ciclos(tenant=A) -> list:
    conn = sqlite3.connect(rl.caminho_do_banco())
    conn.row_factory = sqlite3.Row
    try:
        return [dict(l) for l in conn.execute(
            "SELECT * FROM ciclos_retencao WHERE tenant_id = ? AND offer_type IS NOT NULL "
            "ORDER BY id", (tenant,))]
    finally:
        conn.close()


def _lista(c, tenant=A) -> dict:
    r = c.get("/voluntario/mensagens", headers=_login(c, tenant))
    assert r.status_code == 200, r.text
    return r.json()


def _pendente(c, tenant=A) -> dict:
    """Uma oferta esperando a escolha, e o detalhe dela."""
    chave = _chave(c, tenant)
    _evento(c, chave)
    [m] = _lista(c, tenant)["mensagens"]
    r = c.get(f"/voluntario/mensagens/{m['id']}", headers=_login(c, tenant))
    assert r.status_code == 200, r.text
    return r.json()


def _escolher(c, mid, corpo, papel="owner", tenant=A):
    return c.post(f"/voluntario/mensagens/{mid}/escolher", json=corpo,
                  headers=_login(c, tenant, papel))


# ── M1 ───────────────────────────────────────────────────────────────────

class TestM1EsperaAEscolha:
    def test_o_padrao_de_producao_e_eu_escolho(self):
        assert configuracao.PADROES_DE_PRODUCAO["modo_mensagem_voluntario"] == "escolha"

    def test_a_oferta_por_email_espera_e_nao_conta_como_enviada(self, cliente):
        d = _pendente(cliente)
        [ciclo] = _ciclos()
        assert ciclo["channel"] == "email" and ciclo["offer_sent"] == 0
        assert d["estado"] == "aguardando" and d["canal"] == "email"
        assert d["oferta"] == ciclo["offer_type"]

    def test_tres_mensagens_da_mesma_oferta_com_uma_recomendada(self, cliente):
        d = _pendente(cliente)
        assert [s["abordagem"] for s in d["sugestoes"]] == list(mv.ABORDAGENS)
        assert sum(s["recomendada"] for s in d["sugestoes"]) == 1
        # Abriu a página de cancelamento: a recomendada é a direta, que diz que
        # cancelar continua igual.
        assert d["recomendada"]["abordagem"] == "direto_ao_ponto"
        rotulo = mv.OFERTA_NO_TEXTO["pt"][d["oferta"]]
        for s in d["sugestoes"]:
            assert rotulo in s["textos"]["pt"] and s["textos"]["en"]
            assert s["origem"] == "template" and not s["escolhida"]

    def test_o_prazo_e_o_da_configuracao(self, cliente):
        r = cliente.put("/configuracao", json={"prazo_escolha_horas": 4}, headers=_login(cliente))
        assert r.status_code == 200, r.text
        d = _pendente(cliente)
        prazo = datas.com_fuso(d["escolha_ate"]) - datas.com_fuso(d["criado_em"])
        assert prazo == timedelta(hours=4)

    def test_a_lista_de_clientes_mostra_a_espera_e_leva_as_mensagens(self, cliente):
        d = _pendente(cliente)
        r = cliente.get("/clientes/recentes?cliente=c-1", headers=_login(cliente))
        assert r.status_code == 200, r.text
        [linha] = r.json()["clientes"]
        assert linha["abordagem"]["situacao"] == "aguardando_escolha"
        assert linha["abordagem"]["mensagem_id"] == d["id"]

    def test_o_resumo_do_mes_conta_as_que_esperam(self, cliente):
        _pendente(cliente)
        r = cliente.get("/metrics/voluntario/mes", headers=_login(cliente))
        assert r.json()["aguardando_escolha"] == 1 and r.json()["ofertas_enviadas"] == 0

    def test_o_detalhe_explica_o_risco_e_conta_a_historia(self, cliente):
        d = _pendente(cliente)
        assert 0 < d["risco"]["pontuacao"] <= 1
        assert d["risco"]["decidido_por"] in ("regua", "modelo")
        tipos = [e["tipo"] for e in d["linha_do_tempo"]]
        assert tipos[:2] == ["sinal_de_risco", "mensagens_sugeridas"]


# ── M2 ───────────────────────────────────────────────────────────────────

class TestM2OQueNaoEspera:
    def test_o_popup_sai_na_hora(self, cliente):
        _evento(cliente, _chave(cliente), on_site_now=True)
        [ciclo] = _ciclos()
        assert ciclo["channel"] == "popup" and ciclo["offer_sent"] == 1
        assert _lista(cliente)["mensagens"] == []

    def test_no_modo_automatico_sai_na_hora(self, cliente):
        r = cliente.put("/configuracao", json={"modo_mensagem_voluntario": "automatico"},
                        headers=_login(cliente))
        assert r.status_code == 200, r.text
        _evento(cliente, _chave(cliente))
        [ciclo] = _ciclos()
        assert ciclo["offer_sent"] == 1
        assert _lista(cliente) ["mensagens"] == [] and _lista(cliente)["modo"] == "automatico"


# ── M3 ───────────────────────────────────────────────────────────────────

class TestM3Escolher:
    def test_escolher_envia_na_hora_e_conta_como_enviada(self, cliente):
        d = _pendente(cliente)
        r = _escolher(cliente, d["id"], {"rodada": 1, "abordagem": "facilitacao"})
        assert r.status_code == 200, r.text
        assert r.json()["enviada"] is True and r.json()["escolhida_por"] == "owner"
        assert _ciclos()[0]["offer_sent"] == 1
        assert _lista(cliente)["mensagens"] == []
        depois = cliente.get(f"/voluntario/mensagens/{d['id']}", headers=_login(cliente)).json()
        assert depois["estado"] == "enviada" and depois["escolha_ate"] is None
        assert [s["abordagem"] for s in depois["sugestoes"] if s["escolhida"]] == ["facilitacao"]
        assert [e["tipo"] for e in depois["linha_do_tempo"]][-2:] == ["mensagem_escolhida",
                                                                      "mensagem_enviada"]

    def test_escolher_de_novo_e_409(self, cliente):
        d = _pendente(cliente)
        assert _escolher(cliente, d["id"], {"rodada": 1, "abordagem": "facilitacao"}).status_code == 200
        r = _escolher(cliente, d["id"], {"rodada": 1, "abordagem": "direto_ao_ponto"})
        assert r.status_code == 409

    @pytest.mark.parametrize("corpo, campo", [
        ({"rodada": 1, "abordagem": "facilitacao", "texto": "Fique, por favor"}, "texto"),
        ({"rodada": 1, "abordagem": "lembrete_cordial"}, "abordagem"),
        ({"rodada": 0, "abordagem": "facilitacao"}, "rodada"),
        ({"rodada": 2, "abordagem": "facilitacao"}, "rodada"),
    ])
    def test_corpo_torto_e_422_e_nada_sai(self, cliente, corpo, campo):
        d = _pendente(cliente)
        r = _escolher(cliente, d["id"], corpo)
        assert r.status_code == 422 and r.json()["detail"]["campo"] == campo
        assert _ciclos()[0]["offer_sent"] == 0

    def test_membro_nao_escolhe(self, cliente):
        d = _pendente(cliente)
        r = _escolher(cliente, d["id"], {"rodada": 1, "abordagem": "facilitacao"}, papel="membro")
        assert r.status_code == 403
        assert cliente.post(f"/voluntario/mensagens/{d['id']}/regerar",
                            headers=_login(cliente, papel="membro")).status_code == 403

    def test_whatsapp_sai_pelo_sender_com_a_linha_de_saida(self, cliente, monkeypatch):
        enviados = []

        async def falso(destino, texto, tenant_id=None):
            enviados.append((destino, texto))
            return {"sent": True, "motivo": None}

        monkeypatch.setattr(va, "send_whatsapp", falso)
        _evento(cliente, _chave(cliente), phone="+5511988887777")
        [m] = _lista(cliente)["mensagens"]
        assert m["canal"] == "whatsapp" and enviados == []
        d = cliente.get(f"/voluntario/mensagens/{m['id']}", headers=_login(cliente)).json()
        assert "988887777" not in json.dumps(d)
        assert _escolher(cliente, m["id"], {"rodada": 1, "abordagem": "cuidado_proximo"}).json()["enviada"]
        [(destino, texto)] = enviados
        assert destino == "+5511988887777" and texto.endswith(va.AVISO_SAIR)
        assert texto.startswith(d["sugestoes"][0]["textos"]["pt"])
        # O telefone não fica guardado depois do envio.
        assert mv.ler(A, m["id"])["destino"] is None


# ── M4 ───────────────────────────────────────────────────────────────────

class TestM4Regerar:
    def test_outra_rodada_sem_reiniciar_o_prazo(self, cliente):
        d = _pendente(cliente)
        r = cliente.post(f"/voluntario/mensagens/{d['id']}/regerar", headers=_login(cliente))
        assert r.status_code == 200, r.text
        assert r.json()["rodada"] == 2 and r.json()["escolha_ate"] == d["escolha_ate"]
        novas = r.json()["sugestoes"]
        assert [s["abordagem"] for s in novas] == list(mv.ABORDAGENS)
        antigas = {s["abordagem"]: s["textos"]["pt"] for s in d["sugestoes"]}
        assert all(s["textos"]["pt"] != antigas[s["abordagem"]] for s in novas)
        depois = cliente.get(f"/voluntario/mensagens/{d['id']}", headers=_login(cliente)).json()
        assert len(depois["sugestoes"]) == 6 and depois["recomendada"]["rodada"] == 2
        # Escolher uma da 1ª rodada continua valendo.
        assert _escolher(cliente, d["id"], {"rodada": 1, "abordagem": "facilitacao"}).status_code == 200

    def test_depois_da_escolha_e_409(self, cliente):
        d = _pendente(cliente)
        _escolher(cliente, d["id"], {"rodada": 1, "abordagem": "facilitacao"})
        r = cliente.post(f"/voluntario/mensagens/{d['id']}/regerar", headers=_login(cliente))
        assert r.status_code == 409


# ── M5 ───────────────────────────────────────────────────────────────────

class TestM5Relogio:
    @pytest.mark.asyncio
    async def test_antes_do_prazo_nada_sai(self, cliente):
        d = _pendente(cliente)
        r = await relogio.passagem(datas.agora_local() + timedelta(hours=7))
        assert r["mensagens_voluntario"]["enviadas"] == 0
        assert mv.ler(A, d["id"])["estado"] == "aguardando"

    @pytest.mark.asyncio
    async def test_no_prazo_sai_a_recomendada_da_ultima_rodada(self, cliente):
        d = _pendente(cliente)
        cliente.post(f"/voluntario/mensagens/{d['id']}/regerar", headers=_login(cliente))
        r = await relogio.passagem(datas.agora_local() + timedelta(hours=8, minutes=1))
        assert r["mensagens_voluntario"]["enviadas"] == 1
        m = mv.ler(A, d["id"])
        assert m["estado"] == "enviada" and m["escolhida_por"] == "prazo"
        assert (m["escolhida_rodada"], m["escolhida_abordagem"]) == (2, d["recomendada"]["abordagem"])
        assert _ciclos()[0]["offer_sent"] == 1

    @pytest.mark.asyncio
    async def test_passar_para_o_automatico_envia_as_que_esperavam(self, cliente):
        d = _pendente(cliente)
        cliente.put("/configuracao", json={"modo_mensagem_voluntario": "automatico"},
                    headers=_login(cliente))
        await relogio.passagem(datas.agora_local())
        m = mv.ler(A, d["id"])
        assert m["estado"] == "enviada" and m["escolhida_por"] == "automatico"


# ── M6 ───────────────────────────────────────────────────────────────────

class TestM6LimiteDeContato:
    def test_um_segundo_evento_nao_abre_outra_espera(self, cliente):
        chave = _chave(cliente)
        _evento(cliente, chave)
        _evento(cliente, chave, evento="Downgrade Clicked")
        assert len(_lista(cliente)["mensagens"]) == 1 and len(_ciclos()) == 1

    def test_outro_cliente_tem_a_sua(self, cliente):
        chave = _chave(cliente)
        _evento(cliente, chave, user="c-1")
        _evento(cliente, chave, user="c-2")
        assert len(_lista(cliente)["mensagens"]) == 2


# ── M7 ───────────────────────────────────────────────────────────────────

class TestM7Isolamento:
    def test_outra_empresa_nao_ve_nem_escolhe(self, cliente):
        d = _pendente(cliente, A)
        assert _lista(cliente, B)["mensagens"] == []
        assert cliente.get(f"/voluntario/mensagens/{d['id']}",
                           headers=_login(cliente, B)).status_code == 404
        r = _escolher(cliente, d["id"], {"rodada": 1, "abordagem": "facilitacao"}, tenant=B)
        assert r.status_code == 404 and mv.ler(A, d["id"])["estado"] == "aguardando"

    def test_so_no_premium(self, cliente):
        r = cliente.get("/voluntario/mensagens",
                        headers=cliente.projeto.bearer(A, papel="owner", plano="basico"))
        assert r.status_code == 403

    def test_nao_contatar_e_relido_na_hora_do_envio(self, cliente):
        d = _pendente(cliente)
        clientes_importados.marcar_nao_contatar(A, "c-1", clientes_importados.ORIGEM_ANONIMIZACAO)
        r = _escolher(cliente, d["id"], {"rodada": 1, "abordagem": "facilitacao"})
        assert r.status_code == 200 and r.json()["enviada"] is False
        assert r.json()["motivo_nao_enviada"] == "nao_contatar"
        assert _ciclos()[0]["offer_sent"] == 0


# ── M8 ───────────────────────────────────────────────────────────────────

class TestM8Textos:
    @pytest.mark.parametrize("oferta", sorted(mv.OFERTA_NO_TEXTO["pt"]))
    @pytest.mark.parametrize("rodada", [1, 2])
    @pytest.mark.parametrize("abordagem", mv.ABORDAGENS)
    def test_nenhum_modelo_encaminha_para_uma_pessoa(self, oferta, rodada, abordagem):
        for idioma, texto in mv.textos_do_modelo(abordagem, oferta, rodada).items():
            assert va.encaminha_para_humano(texto) is None, (idioma, texto)
            assert len(texto) <= mv.TEXTO_MAXIMO and "{" not in texto

    def test_a_direta_diz_que_cancelar_continua_igual(self):
        for rodada in (1, 2):
            t = mv.textos_do_modelo("direto_ao_ponto", "desconto_20", rodada)
            assert "Cancelar" in t["pt"] or "cancelar" in t["pt"]
            assert "ancel" in t["en"]

    @pytest.mark.parametrize("evento, criticidade, esperada", [
        (CANCELAMENTO, "padrao", "direto_ao_ponto"), ("Downgrade Clicked", "critico", "direto_ao_ponto"),
        ("Session Started", "critico", "cuidado_proximo"), ("Session Started", "alto", "facilitacao"),
    ])
    def test_a_recomendada(self, evento, criticidade, esperada):
        assert mv.abordagem_recomendada(evento, criticidade) == esperada

    @pytest.mark.asyncio
    async def test_o_gerado_que_falha_no_filtro_vira_o_modelo(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-teste")

        class Resposta:
            content = [type("T", (), {"text": json.dumps({
                "cuidado_proximo": "Sentimos sua falta! Volte com 20% de desconto por 3 meses.",
                "facilitacao": "Fale com nosso suporte para ativar o desconto.",
                "direto_ao_ponto": "x" * (mv.TEXTO_MAXIMO + 1)})})()]

        async def criar(**_):
            return Resposta()

        monkeypatch.setattr(va.claude.messages, "create", criar)
        rodada = {s["abordagem"]: s for s in await mv.gerar_rodada(
            "desconto_20", "email", "critico", "Session Started", 1)}
        assert rodada["cuidado_proximo"]["origem"] == "gerado"
        assert rodada["cuidado_proximo"]["textos"] == {
            "pt": "Sentimos sua falta! Volte com 20% de desconto por 3 meses."}
        assert rodada["facilitacao"]["origem"] == "template"
        assert rodada["direto_ao_ponto"]["origem"] == "template"

    @pytest.mark.asyncio
    async def test_sem_chave_nao_chama_a_api(self, monkeypatch):
        async def nao_pode(**_):
            raise AssertionError("a API foi chamada sem chave")

        monkeypatch.setattr(va.claude.messages, "create", nao_pode)
        rodada = await mv.gerar_rodada("pausa_1_mes", "email", "alto", "Session Started", 1)
        assert {s["origem"] for s in rodada} == {"template"}


# ── M9 ───────────────────────────────────────────────────────────────────

class TestM9Lgpd:
    def test_o_texto_sai_no_prazo_de_guarda_e_fica_a_abordagem(self, cliente):
        d = _pendente(cliente)
        _escolher(cliente, d["id"], {"rodada": 1, "abordagem": "facilitacao"})
        assert mv.apagar_texto_expirado(datas.agora_local() + timedelta(days=89), A, 90) == 0
        assert mv.apagar_texto_expirado(datas.agora_local() + timedelta(days=91), A, 90) == 1
        depois = cliente.get(f"/voluntario/mensagens/{d['id']}", headers=_login(cliente)).json()
        assert depois["texto_apagado"] is True
        assert all(s["textos"] is None for s in depois["sugestoes"])
        assert [s["abordagem"] for s in depois["sugestoes"] if s["escolhida"]] == ["facilitacao"]

    def test_a_que_espera_nao_expira(self, cliente):
        _pendente(cliente)
        assert mv.apagar_texto_expirado(datas.agora_local() + timedelta(days=400), A, 90) == 0

    def test_anonimizar_apaga_o_texto_e_a_espera(self, cliente):
        d = _pendente(cliente)
        assert mv.apagar_do_cliente(A, ["user:c-1"], datas.agora_local()) == 1
        m = mv.ler(A, d["id"])
        assert m["estado"] == "nao_enviada" and m["motivo_nao_enviada"] == "nao_contatar"
        assert all(s["textos"] is None for s in m["sugestoes"]) and m["destino"] is None
        assert mv.apagar_do_cliente(A, ["user:c-1"], datas.agora_local()) == 0

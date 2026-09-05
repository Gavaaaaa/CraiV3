"""tests/test_whatsapp_channel.py — WhatsApp entra no voluntário sem tocar o dunning.

O WhatsApp já era o canal declarado do projeto para os DOIS churns
(`README.md:442` — "Bot WhatsApp | 0,05 | Dunning + ofertas"), mas não existia
em lugar nenhum: o `dunning_engine` do involuntário marca `channel="whatsapp"`
e imprime, e o voluntário só tinha popup e e-mail.

DECISÃO DE ISOLAMENTO DO SPRINT 3, declarada e travada aqui: como o
involuntário nunca teve função de envio, não havia o que extrair — então
`whatsapp_sender.py` nasce novo e é consumido SÓ pelo voluntário. Não há
duplicação temporária a unificar depois; quando a integração real entrar, ela
entra no sender e o dunning passa a chamá-lo. `TestIsolamentoDoInvoluntario`
existe para provar que essa fronteira continua de pé.

**O que aqui é prova de regressão e o que é catraca.** Tudo reprova em
`1b397c2` (fim do Sprint 2) por ImportError — o módulo não existia. Dentro
disso:

    test_criticidade_vence_o_historico       — decisão de PRODUTO, fixada de
                                               propósito para ficar visível se
                                               alguém quiser invertê-la
    test_numero_nunca_aparece_inteiro_no_log — PII, mesma política da chave Pix
    TestIsolamentoDoInvoluntario             — catraca da fronteira

Uso:
    pytest tests/test_whatsapp_channel.py -v
"""

import inspect

import pytest

from crai.churn_voluntary import voluntary_agent as va
from crai.integrations import whatsapp_sender as ws
from crai.integrations.whatsapp_sender import (
    destino_utilizavel,
    mascarar,
    send_whatsapp,
)

TELEFONE = "11912345678"


@pytest.fixture(autouse=True)
def historico_limpo():
    """`_channel_history` é global do módulo: um teste não pode herdar o do outro."""
    va._channel_history.clear()
    yield
    va._channel_history.clear()


def _estado(criticality="critico", phone=TELEFONE, on_site=False, **extra):
    props = {"billing_profile": "PJ", "on_site_now": on_site}
    if phone is not None:
        props["phone"] = phone
    return {
        "user_id": "usr_teste", "event": "Cancellation Page Viewed",
        "props": props, "risk_score": 0.90, "profile": "PJ",
        "is_critical": True, "criticality": criticality,
        "offer_type": "desconto_20", "channel": None, "on_site_now": on_site,
        "prior_channel_success": None, "message": "mensagem de teste",
        "offer_sent": False, "accepted": None, "retained": False, **extra,
    }


# ── Destino ──────────────────────────────────────────────────────────────

class TestDestinoUtilizavel:

    @pytest.mark.parametrize("bruto,esperado", [
        ("11912345678", "11912345678"),
        ("5511912345678", "5511912345678"),
        ("+55 11 91234-5678", "5511912345678"),
        ("(11) 91234-5678", "11912345678"),
        (11912345678, "11912345678"),          # `json` entrega número como int
    ])
    def test_formas_aceitas(self, bruto, esperado):
        assert destino_utilizavel(bruto) == esperado

    @pytest.mark.parametrize("bruto", [
        None, "", "   ", "123", "abc", "não tenho", [TELEFONE], {"n": TELEFONE},
        True, False, 1234.56, "9" * 16,
    ])
    def test_formas_recusadas(self, bruto):
        """`props` é payload de terceiro: nada ali pode virar destino de envio."""
        assert destino_utilizavel(bruto) is None

    def test_nao_inventa_codigo_de_pais(self):
        """Prefixar `55` assumiria o país do cliente a partir do país de quem
        programou. O número volta como veio; quem recusa o inentregável é o
        provedor, com erro visível."""
        assert destino_utilizavel("11912345678") == "11912345678"

    def test_mascara_preserva_so_os_quatro_ultimos(self):
        assert mascarar("5511912345678") == "*********5678"


# ── Envio ────────────────────────────────────────────────────────────────

class TestSendWhatsapp:

    @pytest.mark.asyncio
    async def test_envio_bem_sucedido(self):
        r = await send_whatsapp(TELEFONE, "Olá!")
        assert r["sent"] is True
        assert r["channel"] == "whatsapp"
        assert r["motivo"] is None

    @pytest.mark.asyncio
    async def test_destino_invalido_nao_levanta(self):
        """É um nó de grafo: uma exceção aqui derrubaria o ciclo inteiro por
        causa de um número mal formatado."""
        r = await send_whatsapp("não tenho", "Olá!")
        assert r["sent"] is False
        assert r["motivo"] == "destino_invalido"

    @pytest.mark.asyncio
    async def test_numero_nunca_aparece_inteiro_no_log(self, capsys):
        """PII. O projeto cifra a chave Pix do pagador porque ela identifica uma
        pessoa; o telefone identifica a mesma pessoa e sai em `print` para o log
        do container. Mascarar é a mesma política aplicada ao canal novo."""
        await send_whatsapp("+55 11 91234-5678", "Olá!")
        saida = capsys.readouterr().out
        assert "5511912345678" not in saida
        assert "91234" not in saida
        assert "5678" in saida            # o sufixo fica, para dar rastro

    @pytest.mark.asyncio
    async def test_numero_nao_volta_inteiro_no_retorno(self):
        """O que não circula não vaza: o estado do grafo e o CRM não precisam
        do número, só da confirmação."""
        r = await send_whatsapp(TELEFONE, "Olá!")
        assert r["to"] == mascarar(TELEFONE)

    @pytest.mark.asyncio
    async def test_tenant_id_atravessa(self):
        """Fio já ligado para o Sprint 5, que coloca `tenant_id` no estado."""
        r = await send_whatsapp(TELEFONE, "Olá!", tenant_id="acme")
        assert r["tenant_id"] == "acme"


# ── Roteamento de canal ──────────────────────────────────────────────────

class TestChooseChannel:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("criticality", ["critico", "alto"])
    async def test_whatsapp_quando_ha_numero_e_criticidade(self, criticality):
        r = await va.choose_channel(_estado(criticality))
        assert r["channel"] == "whatsapp"

    @pytest.mark.asyncio
    async def test_padrao_nao_usa_whatsapp_mesmo_com_numero(self):
        """O canal mais pessoal é para quem está prestes a sair, não para todo
        mundo que tem telefone cadastrado."""
        r = await va.choose_channel(_estado("padrao"))
        assert r["channel"] != "whatsapp"

    @pytest.mark.asyncio
    async def test_sem_numero_cai_na_cadeia_antiga(self):
        assert (await va.choose_channel(
            _estado("critico", phone=None, on_site=True)))["channel"] == "popup"
        assert (await va.choose_channel(
            _estado("critico", phone=None)))["channel"] == "email"

    @pytest.mark.asyncio
    async def test_numero_invalido_conta_como_ausente(self):
        r = await va.choose_channel(_estado("critico", phone="123"))
        assert r["channel"] == "email"

    @pytest.mark.asyncio
    async def test_criticidade_vence_o_historico(self):
        """DECISÃO DE PRODUTO, fixada aqui de propósito.

        O histórico diz por onde o cliente já converteu; a criticidade diz que
        ele está prestes a sair AGORA. Escolhemos o canal mais pessoal em vez do
        que funcionou quando ele estava tranquilo. Se um dia essa troca se
        provar errada, é este teste que reprova primeiro — que é exatamente o
        ponto de tê-lo.
        """
        va._channel_history["usr_teste"] = "popup"
        r = await va.choose_channel(_estado("critico"))
        assert r["channel"] == "whatsapp"

    @pytest.mark.asyncio
    async def test_historico_preservado_fora_da_criticidade(self):
        """CATRACA: a memória de canal do Sprint anterior continua valendo."""
        va._channel_history["usr_teste"] = "popup"
        r = await va.choose_channel(_estado("padrao"))
        assert r["channel"] == "popup"

    @pytest.mark.asyncio
    async def test_historico_whatsapp_sem_numero_nao_roteia_para_o_vazio(self):
        """Memória de um canal que não existe agora é memória, não destino."""
        va._channel_history["usr_teste"] = "whatsapp"
        r = await va.choose_channel(_estado("padrao", phone=None, on_site=True))
        assert r["channel"] == "popup"

    @pytest.mark.asyncio
    async def test_ramo_morto_removido_sem_mudar_o_resultado(self):
        """CATRACA do que NÃO podia mudar.

        O `elif risk_score >= 0.90: channel = "email"` e o `else: channel =
        "email"` devolviam os dois a mesma coisa — a condição nunca decidiu
        nada. Removê-la não pode ter mexido no resultado de quem não tem
        telefone: risco alto, fora do site, sem número → e-mail, como antes.
        """
        r = await va.choose_channel(
            _estado("critico", phone=None, on_site=False, risk_score=0.95))
        assert r["channel"] == "email"


# ── Envio a partir do nó ─────────────────────────────────────────────────

class TestSendOffer:

    @pytest.mark.asyncio
    async def test_canal_whatsapp_chama_o_sender(self, monkeypatch):
        chamadas = []

        async def espiao(to, message, tenant_id=None):
            chamadas.append({"to": to, "message": message, "tenant_id": tenant_id})
            return {"sent": True, "channel": "whatsapp", "to": "***",
                    "motivo": None, "simulado": True, "tenant_id": tenant_id}

        monkeypatch.setattr(va, "send_whatsapp", espiao)

        r = await va.send_offer(_estado("critico", channel="whatsapp"))

        assert len(chamadas) == 1
        assert chamadas[0]["to"] == TELEFONE
        assert chamadas[0]["message"] == "mensagem de teste"
        assert r["offer_sent"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("canal", ["popup", "email"])
    async def test_demais_canais_nao_chamam_o_sender(self, monkeypatch, canal):
        async def nao_deveria(*a, **k):
            raise AssertionError("send_whatsapp chamado fora do canal whatsapp")

        monkeypatch.setattr(va, "send_whatsapp", nao_deveria)

        r = await va.send_offer(_estado("padrao", channel=canal))
        assert r["offer_sent"] is True

    @pytest.mark.asyncio
    async def test_falha_de_entrega_nao_marca_oferta_como_enviada(self):
        """`offer_sent` é o que o CRM lê. Marcar True num envio que não houve
        registraria no HubSpot uma oferta que o cliente nunca viu."""
        estado = _estado("critico", phone=None, channel="whatsapp")
        r = await va.send_offer(estado)
        assert r["offer_sent"] is False


# ── A fronteira com o involuntário ───────────────────────────────────────

class TestIsolamentoDoInvoluntario:

    def test_dunning_nao_importa_o_sender(self):
        """A opção conservadora, verificada e não só declarada."""
        from crai.dunning import dunning_engine

        assert "whatsapp_sender" not in inspect.getsource(dunning_engine)

    @pytest.mark.asyncio
    async def test_dunning_continua_com_o_comportamento_de_antes(self):
        """CATRACA: o involuntário não pode ter mudado de comportamento
        observável por causa de um canal que nasceu do outro lado."""
        from crai.dunning.dunning_engine import DunningEngine

        r = await DunningEngine().run_campaign(
            customer_id="cus_teste", failure_cause="expired_card",
            recovery_score=0.8, amount=299.90)

        assert r["channel"] == "whatsapp"      # continua sendo o rótulo dele
        assert r["sent"] is True
        assert r["payment_method"] == "pix_automatico"

    def test_sender_nao_conhece_o_involuntario(self):
        """A dependência é de mão única: se um dia o dunning chamar o sender,
        quem muda é o dunning, não este módulo."""
        fonte = inspect.getsource(ws)
        for modulo in ("dunning_engine", "payment_gateway", "failure_classifier"):
            assert f"import {modulo}" not in fonte

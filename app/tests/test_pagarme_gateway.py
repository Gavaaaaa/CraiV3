"""tests/test_pagarme_gateway.py — Sprint 2: o agente passa a EXECUTAR a tentativa.

Até aqui a `PixAutomaticoRetryPolicy` calculava as datas das 3 tentativas do
BACEN e devolvia uma lista de `TentativaAgendada` — e nada, em lugar nenhum,
reenviava a instrução de pagamento ao PSP nessas datas. O agente decidia quando
tentar e não tentava. Este arquivo cobre a ponte: `pagarme_gateway`.

As três propriedades que precisam valer, e que os testes abaixo separam:

    1. o modo simulado é o DEFAULT e não toca a rede — nenhum teste, e nenhuma
       demo de banca, pode depender de credencial ou de internet;
    2. o valor da tentativa nunca diverge do valor original (BACEN: cobrança
       parcial não existe no fluxo de Pix Automático), e a barreira está no
       último ponto antes do pedido sair;
    3. o modo real falha ALTO sem chave, em vez de cair para simulado — a
       operação não pode achar que cobrou.
"""

import pytest

from crai.dunning.pix_automatico_retry import PixRetryPolicyViolation
from crai.integrations import pagarme_gateway
from crai.integrations.pagarme_gateway import (
    ORIGEM_SIMULADA,
    PagarmeIndisponivel,
    reenviar_cobranca_pix,
)

VALOR = 299.90


class TestModoSimulado:
    """O default. É o que a banca roda e o que a suíte inteira exercita."""

    @pytest.mark.asyncio
    async def test_reenvia_sem_rede_e_devolve_sucesso(self, monkeypatch):
        monkeypatch.delenv(pagarme_gateway.ENV_LIVE, raising=False)

        # Se qualquer caminho tentar abrir cliente HTTP, o teste falha aqui em
        # vez de silenciosamente atravessar a rede na máquina de quem roda.
        import httpx

        def proibido(*a, **kw):
            raise AssertionError("o modo simulado abriu um cliente HTTP")

        monkeypatch.setattr(httpx, "AsyncClient", proibido)

        resultado = await reenviar_cobranca_pix("RN_sim", VALOR, e2e_ref="E_sim")

        assert resultado["sucesso"] is True
        assert resultado["origem"] == ORIGEM_SIMULADA
        assert resultado["valor"] == VALOR
        assert resultado["id_cobranca"].startswith("ch_sim_")

    @pytest.mark.asyncio
    async def test_id_simulado_e_reproduzivel(self, monkeypatch):
        """A demo tem que dar o mesmo id em duas execuções.

        Mesmo motivo já documentado no `create_deal` do HubSpot e no
        `_thread_id`: o `hash()` de string é randomizado por processo, e um id
        que muda a cada execução torna a apresentação irreprodutível.
        """
        monkeypatch.delenv(pagarme_gateway.ENV_LIVE, raising=False)
        a = await reenviar_cobranca_pix("RN_rep", VALOR, e2e_ref="E_rep")
        b = await reenviar_cobranca_pix("RN_rep", VALOR, e2e_ref="E_rep")
        c = await reenviar_cobranca_pix("RN_outro", VALOR, e2e_ref="E_rep")

        assert a["id_cobranca"] == b["id_cobranca"]
        assert a["id_cobranca"] != c["id_cobranca"]


class TestInvarianteDeValor:
    """Cobrança parcial não existe no Pix Automático — nem majorada."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("valor_enviado", [150.00, 299.89, 300.00, 599.80])
    async def test_valor_diferente_do_original_levanta(self, monkeypatch, valor_enviado):
        monkeypatch.delenv(pagarme_gateway.ENV_LIVE, raising=False)
        with pytest.raises(PixRetryPolicyViolation):
            await reenviar_cobranca_pix("RN_valor", valor_enviado, valor_original=VALOR)

    @pytest.mark.asyncio
    async def test_valor_igual_ao_original_passa(self, monkeypatch):
        monkeypatch.delenv(pagarme_gateway.ENV_LIVE, raising=False)
        resultado = await reenviar_cobranca_pix("RN_valor_ok", VALOR, valor_original=VALOR)
        assert resultado["sucesso"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("valor", [0.0, -1.0])
    async def test_valor_nao_positivo_levanta(self, monkeypatch, valor):
        monkeypatch.delenv(pagarme_gateway.ENV_LIVE, raising=False)
        with pytest.raises(PixRetryPolicyViolation):
            await reenviar_cobranca_pix("RN_zero", valor)


class TestModoReal:
    """Ligar a env não pode ser meio caminho."""

    @pytest.mark.asyncio
    async def test_sem_chave_falha_alto_em_vez_de_simular(self, monkeypatch):
        monkeypatch.setenv(pagarme_gateway.ENV_LIVE, "1")
        monkeypatch.delenv(pagarme_gateway.ENV_API_KEY, raising=False)

        with pytest.raises(PagarmeIndisponivel) as erro:
            await reenviar_cobranca_pix("RN_sem_chave", VALOR)

        assert pagarme_gateway.ENV_API_KEY in str(erro.value), (
            "a mensagem precisa dizer qual env falta — quem opera vai ler isto "
            "com a cobrança já atrasada")

    @pytest.mark.asyncio
    async def test_erro_de_rede_vira_pagarme_indisponivel(self, monkeypatch):
        """Uma exceção de rede crua atravessaria o agendador como erro fatal.

        A distinção importa: `PagarmeIndisponivel` deixa a tentativa devida e o
        cron tenta de novo; uma exceção qualquer subiria pelo grafo.
        """
        monkeypatch.setenv(pagarme_gateway.ENV_LIVE, "1")
        monkeypatch.setenv(pagarme_gateway.ENV_API_KEY, "sk_teste")

        import httpx

        class ClienteQueCai:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                raise httpx.ConnectError("sem rota para o host")

        monkeypatch.setattr(httpx, "AsyncClient", ClienteQueCai)

        with pytest.raises(PagarmeIndisponivel):
            await reenviar_cobranca_pix("RN_rede", VALOR)

    @pytest.mark.asyncio
    async def test_recusa_do_psp_nao_vira_sucesso(self, monkeypatch):
        monkeypatch.setenv(pagarme_gateway.ENV_LIVE, "1")
        monkeypatch.setenv(pagarme_gateway.ENV_API_KEY, "sk_teste")

        import httpx

        class Resposta:
            status_code = 422
            text = '{"message":"recorrencia inativa"}'

        class ClienteQueRecusa:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return Resposta()

        monkeypatch.setattr(httpx, "AsyncClient", ClienteQueRecusa)

        with pytest.raises(PagarmeIndisponivel) as erro:
            await reenviar_cobranca_pix("RN_recusa", VALOR)
        assert "422" in str(erro.value)

    @pytest.mark.asyncio
    async def test_valor_vai_em_centavos_e_a_auth_e_basic(self, monkeypatch):
        """O que efetivamente sai no pedido, medido em vez de afirmado."""
        monkeypatch.setenv(pagarme_gateway.ENV_LIVE, "1")
        monkeypatch.setenv(pagarme_gateway.ENV_API_KEY, "sk_teste")
        monkeypatch.setenv(pagarme_gateway.ENV_ENDPOINT, "https://psp.teste/cobrar")

        capturado = {}

        import httpx

        class Resposta:
            status_code = 200
            text = "{}"

            @staticmethod
            def json():
                return {"id": "ch_real_1"}

        class ClienteEspiao:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, json=None, auth=None, **kw):
                capturado.update(url=url, corpo=json, auth=auth)
                return Resposta()

        monkeypatch.setattr(httpx, "AsyncClient", ClienteEspiao)

        resultado = await reenviar_cobranca_pix("RN_real", VALOR, e2e_ref="E_real")

        assert capturado["url"] == "https://psp.teste/cobrar"
        assert capturado["corpo"]["amount"] == 29990, (
            "o Pagar.me v5 recebe valor em centavos; enviar reais cobraria "
            "R$ 299,90 como R$ 2,99")
        assert capturado["corpo"]["payment_method"] == "pix"
        assert capturado["auth"] == ("sk_teste", "")
        assert resultado["id_cobranca"] == "ch_real_1"

    @pytest.mark.asyncio
    async def test_a_chave_nunca_aparece_no_log(self, monkeypatch, capsys):
        monkeypatch.setenv(pagarme_gateway.ENV_LIVE, "1")
        monkeypatch.setenv(pagarme_gateway.ENV_API_KEY, "sk_segredo_do_cliente")

        import httpx

        class Resposta:
            status_code = 200
            text = "{}"

            @staticmethod
            def json():
                return {"id": "ch_x"}

        class Cliente:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return Resposta()

        monkeypatch.setattr(httpx, "AsyncClient", Cliente)
        await reenviar_cobranca_pix("RN_log", VALOR)

        assert "sk_segredo_do_cliente" not in capsys.readouterr().out


class TestOModoEhLidoACadaChamada:
    def test_ligar_a_env_depois_do_import_tem_efeito(self, monkeypatch):
        """Uma constante de módulo congelaria o modo no import do pacote."""
        monkeypatch.delenv(pagarme_gateway.ENV_LIVE, raising=False)
        assert pagarme_gateway.modo_real() is False
        monkeypatch.setenv(pagarme_gateway.ENV_LIVE, "1")
        assert pagarme_gateway.modo_real() is True

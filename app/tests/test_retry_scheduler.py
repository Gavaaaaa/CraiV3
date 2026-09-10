"""tests/test_retry_scheduler.py — Sprint 2: as tentativas 2 e 3 existem (Gap 1).

O DEFEITO QUE ESTE ARQUIVO MEDE. A política do BACEN devolve o plano inteiro
das tentativas restantes — todas as instruções de pagamento a reenviar dentro
dos 7 dias. O nó do grafo dispara a primeira, quando ela é devida, e termina.
As tentativas 2 e 3 caem em dias seguintes, com o `ainvoke` já encerrado: sem
alguém que volte na data certa, elas não acontecem. O agente prometia usar as 3
tentativas que a regulação concede ao recebedor e usava uma.

O relógio é injetado (`processar_tentativas_devidas(agora=...)`) exatamente
para que isto seja testável: sem mover o tempo, "dispara na data certa" e
"dispara tudo de uma vez" são indistinguíveis.

O QUE NÃO PODE ACONTECER, e por isso tem teste próprio:
  - disparar antes da data (instrução fora da janela combinada);
  - disparar a mesma tentativa duas vezes (o cron passa de novo e reencontra o
    mesmo plano — o cliente seria cobrado duas vezes pela mesma tentativa);
  - ultrapassar 3 tentativas na janela, mesmo com estado corrompido;
  - consumir a tentativa quando o PSP está fora do ar (ela continua devida).
"""

from datetime import datetime, timedelta

import pytest

from crai.dunning import retry_state
from crai.dunning.pix_automatico_retry import MAX_TENTATIVAS
from crai.dunning.retry_scheduler import processar_tentativas_devidas
from crai.integrations import pagarme_gateway
from crai.integrations.pagarme_gateway import PagarmeIndisponivel

VALOR = 299.90
ABERTURA = datetime(2026, 9, 3, 9, 0)


@pytest.fixture(autouse=True)
def sem_modo_real(monkeypatch):
    monkeypatch.delenv(pagarme_gateway.ENV_LIVE, raising=False)


def _plano(customer_id: str = "RN_sched", quantas: int = MAX_TENTATIVAS,
           tenant_id=None) -> None:
    """Grava um plano com `quantas` tentativas, uma por dia a partir da abertura."""
    retry_state.save_retry_state(
        customer_id=customer_id,
        valor_original=VALOR,
        tenant_id=tenant_id,
        e2e_id=f"E_{customer_id}",
        pix_janela_ate=ABERTURA + timedelta(days=7),
        tentativas=[
            {"numero": i + 1, "quando": ABERTURA + timedelta(days=i + 1),
             "valor": VALOR, "origem": "fallback_uniforme"}
            for i in range(quantas)
        ],
    )


def _disparadas(customer_id: str = "RN_sched") -> list[int]:
    registro = retry_state.get_retry_state(customer_id) or {}
    return [t["numero"] for t in registro.get("tentativas", []) if t.get("disparada_em")]


class TestDisparoNaDataCerta:

    @pytest.mark.asyncio
    async def test_nada_sai_antes_da_data(self):
        _plano()
        disparos = await processar_tentativas_devidas(ABERTURA)
        assert disparos == [], (
            "tentativa disparada antes da data planejada — a instrução sairia "
            "fora da janela que a política calculou")
        assert _disparadas() == []

    @pytest.mark.asyncio
    async def test_as_tres_saem_em_sequencia_conforme_o_tempo_avanca(self):
        """O teste que o Gap 1 pedia: as 3 tentativas do BACEN acontecem."""
        _plano()

        vistos = []
        for dia in range(1, MAX_TENTATIVAS + 1):
            disparos = await processar_tentativas_devidas(
                ABERTURA + timedelta(days=dia, hours=1))
            vistos.append([d["numero"] for d in disparos])

        assert vistos == [[1], [2], [3]], (
            f"sequência de disparos {vistos} — cada tentativa deve sair no seu "
            "dia, uma por passagem do agendador")
        assert _disparadas() == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_uma_passagem_atrasada_recupera_as_pendentes(self):
        """Se o cron ficou parado, o que venceu enquanto isso ainda sai.

        Vale enquanto a janela do BACEN não expirou: a política já validou que
        todas as datas do plano cabem nos 7 dias.
        """
        _plano()
        disparos = await processar_tentativas_devidas(ABERTURA + timedelta(days=5))
        assert [d["numero"] for d in disparos] == [1, 2, 3]


class TestNaoDisparaDuasVezes:

    @pytest.mark.asyncio
    async def test_a_segunda_passagem_nao_reenvia_o_que_ja_saiu(self):
        _plano()
        primeira = await processar_tentativas_devidas(ABERTURA + timedelta(days=1, hours=1))
        segunda = await processar_tentativas_devidas(ABERTURA + timedelta(days=1, hours=2))

        assert [d["numero"] for d in primeira] == [1]
        assert segunda == [], (
            "a tentativa 1 foi reenviada na segunda passagem do agendador — o "
            "cliente seria cobrado duas vezes pela mesma tentativa")

    @pytest.mark.asyncio
    async def test_regravar_o_plano_preserva_a_marca_de_disparo(self):
        """O grafo roda de novo a cada cobrança falhada e regrava o plano.

        Se a regravação apagasse `disparada_em`, a próxima passagem do cron
        reenviaria tudo o que já tinha saído.
        """
        _plano()
        await processar_tentativas_devidas(ABERTURA + timedelta(days=1, hours=1))
        _plano()   # regravação, como o nó do grafo faz

        assert _disparadas() == [1]
        restantes = await processar_tentativas_devidas(ABERTURA + timedelta(days=1, hours=2))
        assert restantes == []


class TestLimiteDoBacen:

    @pytest.mark.asyncio
    async def test_nunca_ultrapassa_tres_tentativas(self):
        _plano()
        total = 0
        for dia in range(1, 10):
            total += len(await processar_tentativas_devidas(
                ABERTURA + timedelta(days=dia)))
        assert total == MAX_TENTATIVAS, (
            f"{total} tentativas disparadas na janela — o limite do BACEN é "
            f"{MAX_TENTATIVAS} por janela de 7 dias")

    @pytest.mark.asyncio
    async def test_plano_corrompido_com_quarta_tentativa_nao_dispara_a_quarta(self):
        """Defesa em profundidade: o agendador não concede, só executa.

        Um estado gravado com 4 entradas é inconsistente por construção — a
        política nunca produz isso. Se acontecer (arquivo editado à mão, versão
        antiga do formato), a quarta não pode sair.
        """
        _plano(quantas=MAX_TENTATIVAS + 1)
        disparos = await processar_tentativas_devidas(ABERTURA + timedelta(days=9))

        assert [d["numero"] for d in disparos] == [1, 2, 3]
        assert MAX_TENTATIVAS + 1 not in _disparadas()


class TestFalhaDoPSP:

    @pytest.mark.asyncio
    async def test_psp_fora_do_ar_nao_consome_a_tentativa(self, monkeypatch):
        _plano()

        async def indisponivel(**kwargs):
            raise PagarmeIndisponivel("timeout")

        import crai.dunning.retry_scheduler as sched
        monkeypatch.setattr(sched, "reenviar_cobranca_pix", indisponivel)

        disparos = await processar_tentativas_devidas(ABERTURA + timedelta(days=1, hours=1))

        assert disparos == []
        assert _disparadas() == [], (
            "a tentativa foi marcada como disparada mesmo sem o PSP aceitar — "
            "o recebedor perderia uma das 3 tentativas sem ter cobrado nada")

    @pytest.mark.asyncio
    async def test_a_tentativa_sai_na_passagem_seguinte(self, monkeypatch):
        _plano()
        estado = {"cai": True}
        original = None

        import crai.dunning.retry_scheduler as sched
        original = sched.reenviar_cobranca_pix

        async def instavel(**kwargs):
            if estado["cai"]:
                raise PagarmeIndisponivel("timeout")
            return await original(**kwargs)

        monkeypatch.setattr(sched, "reenviar_cobranca_pix", instavel)

        await processar_tentativas_devidas(ABERTURA + timedelta(days=1, hours=1))
        estado["cai"] = False
        disparos = await processar_tentativas_devidas(ABERTURA + timedelta(days=1, hours=2))

        assert [d["numero"] for d in disparos] == [1]


class TestValorEnviado:

    @pytest.mark.asyncio
    async def test_o_valor_de_toda_tentativa_e_o_original(self):
        _plano()
        disparos = await processar_tentativas_devidas(ABERTURA + timedelta(days=9))
        assert {d["valor"] for d in disparos} == {VALOR}

    @pytest.mark.asyncio
    async def test_plano_com_valor_divergente_nao_dispara(self):
        """A barreira de valor vale mesmo com o estado adulterado."""
        retry_state.save_retry_state(
            customer_id="RN_divergente", valor_original=VALOR,
            pix_janela_ate=ABERTURA + timedelta(days=7),
            tentativas=[{"numero": 1, "quando": ABERTURA + timedelta(days=1),
                         "valor": VALOR / 2, "origem": "adulterado"}],
        )
        disparos = await processar_tentativas_devidas(ABERTURA + timedelta(days=2))

        assert disparos == [], (
            "uma cobrança parcial foi enviada ao PSP — o fluxo de Pix "
            "Automático não admite valor diferente do original")


class TestIntegracaoComONoDoGrafo:
    """O nó grava o plano; o agendador conclui. Sem código paralelo entre os dois."""

    @pytest.mark.asyncio
    async def test_o_no_grava_o_plano_e_dispara_a_primeira_devida(self, monkeypatch):
        from crai.agent import workflow as workflow_module
        from crai.agent.workflow import schedule_retry_pix

        # Confiança mínima inalcançável: a previsão do Payday Engine é
        # descartada e a política cai no fallback uniforme, que ancora a
        # primeira tentativa na abertura da janela do recebedor. Sem isto o
        # teste dependeria de qual dia o modelo prevê para este cliente — se a
        # previsão cair adiante, a primeira tentativa não é devida agora e o
        # teste falha por motivo que não é o defeito que ele mede.
        monkeypatch.setattr(workflow_module._pix_retry, "confianca_minima", 2.0)

        agora = datetime.now()
        # Janela já aberta há dois dias: a primeira tentativa do plano é
        # imediatamente devida (a janela do recebedor abre no dia seguinte ao
        # vencimento), e é o caso em que o próprio nó dispara.
        state = {
            "customer_id": "RN_no_do_grafo", "amount": VALOR,
            "invoice_id": "E_no_do_grafo", "retry_count": 0,
            "pix_janela_ate": agora + timedelta(days=5),
        }

        resultado = await schedule_retry_pix(state)

        registro = retry_state.get_retry_state("RN_no_do_grafo")
        assert registro is not None, (
            "o nó não gravou o plano na camada de estado — as tentativas 2 e 3 "
            "morreriam no fim do `ainvoke`")
        assert len(registro["tentativas"]) == len(resultado["pix_retry_schedule"])
        assert _disparadas("RN_no_do_grafo") == [1], (
            "a primeira tentativa já era devida e não foi disparada pelo nó")

    @pytest.mark.asyncio
    async def test_tentativa_futura_fica_para_o_agendador(self):
        from crai.agent.workflow import schedule_retry_pix

        state = {
            "customer_id": "RN_futura", "amount": VALOR,
            "invoice_id": "E_futura", "retry_count": 0,
        }
        await schedule_retry_pix(state)

        assert _disparadas("RN_futura") == [], (
            "o nó disparou uma tentativa cuja data ainda não chegou — a janela "
            "do recebedor só abre no dia seguinte ao vencimento")


class TestCamadaDeEstado:
    """Gap 2: o ponto de troca para o DB, e o que ele promete hoje."""

    def test_o_caminho_do_estado_e_redirecionavel(self, monkeypatch, tmp_path):
        destino = tmp_path / "outro.json"
        monkeypatch.setenv(retry_state.ENV_CAMINHO, str(destino))
        _plano("RN_redirecionado")

        assert destino.exists(), (
            "sem redirecionamento por env a suíte escreveria no estado real, e "
            "a próxima passagem do agendador cobraria clientes de teste")
        assert retry_state.get_retry_state("RN_redirecionado") is not None

    def test_estado_ilegivel_nao_derruba_e_nao_concede_tentativa(
            self, monkeypatch, tmp_path):
        arquivo = tmp_path / "corrompido.json"
        arquivo.write_text("{ isto não é json", encoding="utf-8")
        monkeypatch.setenv(retry_state.ENV_CAMINHO, str(arquivo))

        assert retry_state.planos_pendentes() == []
        assert retry_state.get_retry_state("qualquer") is None

    def test_a_chave_separa_tenants(self):
        """Preparado para o Sprint 4: dois tenants não se sobrepõem."""
        _plano("RN_compartilhado", tenant_id="empresa_a")
        _plano("RN_compartilhado", tenant_id="empresa_b")

        a = retry_state.get_retry_state("RN_compartilhado", tenant_id="empresa_a")
        b = retry_state.get_retry_state("RN_compartilhado", tenant_id="empresa_b")
        assert a is not None and b is not None
        assert a["tenant_id"] == "empresa_a" and b["tenant_id"] == "empresa_b"

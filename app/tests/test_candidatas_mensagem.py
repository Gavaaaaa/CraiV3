"""tests/test_candidatas_mensagem.py — três candidatas por evento, e nenhuma
encaminha para humano.

O que a etapa das três mensagens promete, e o que aqui é verificado:

  (a) o bandit expõe a RODADA inteira (`classificar_ofertas`): todos os
      braços, ordenados pelo e-Profit amostrado, com a probabilidade aprendida
      de cada um — e o [0] é exatamente o que `choose_offer` devolve com a
      mesma semente. Expor não mudou a decisão.
  (b) o nó `choose_offer` carrega as três primeiras linhas da rodada no
      estado, e a escolhida é a primeira delas.
  (c) `generate_message` devolve `candidatas` com três ofertas DIFERENTES,
      exatamente uma marcada `escolhida`, e `message` continua sendo o texto
      dela — o caminho antigo (uma string) segue valendo.
  (d) INVARIANTE DE PRODUTO: nenhuma candidata encaminha para atendente,
      suporte ou qualquer pessoa. Vale para o template em todas as
      criticidades e ofertas, com e sem assinatura, e vale para o texto que a
      Claude API devolver — se ela sugerir "fale com o suporte", o texto é
      descartado e o template entra no lugar.
  (e) estado sem a rodada (chamador antigo) produz UMA candidata e não quebra.

ISOLAMENTO: nenhum teste chama a Claude API nem toca o bandit persistido.

Uso:
    pytest tests/test_candidatas_mensagem.py -v
"""

import types

import pytest

from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary import voluntary_agent as va
from crai.churn_voluntary.offer_bandit import OFFERS, PROFILES, TENANT_PADRAO, OfferBandit


@pytest.fixture(autouse=True)
def bandit_isolado(monkeypatch, tmp_path):
    """Nenhum teste deste arquivo toca `crai/models/bandit_state.json`."""
    monkeypatch.setattr(ob, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "bandit_state.json")
    monkeypatch.delenv("CRAI_CS_SIGNATURE_NAME", raising=False)


class EspiaoClaude:
    def __init__(self, texto="mensagem gerada", erro=None):
        self.texto, self.erro, self.chamadas = texto, erro, []

    async def create(self, **kwargs):
        self.chamadas.append(kwargs)
        if self.erro:
            raise self.erro
        return types.SimpleNamespace(content=[types.SimpleNamespace(text=self.texto)])


def _claude(monkeypatch, texto="mensagem gerada", erro=None):
    e = EspiaoClaude(texto, erro)
    monkeypatch.setattr(va.claude.messages, "create", e.create)
    return e


def _estado(criticality="padrao", **extra):
    return {
        "tenant_id": TENANT_PADRAO, "user_id": "usr_teste",
        "event": "Cancellation Page Viewed", "props": {"billing_profile": "PJ"},
        "risk_score": 0.90, "profile": "PJ", "is_critical": True,
        "criticality": criticality, "offer_type": None, "channel": "email",
        "on_site_now": False, "prior_channel_success": None, "message": None,
        "offer_sent": False, "accepted": None, "retained": False, **extra,
    }


# ── (a) a rodada do bandit ───────────────────────────────────────────────

class TestRodadaDoBandit:

    def test_rodada_tem_todos_os_bracos_e_so_eles(self):
        rodada = OfferBandit(seed=1).classificar_ofertas(TENANT_PADRAO, "CLT", 0.8)
        assert [l["offer"] for l in sorted(rodada, key=lambda l: l["offer"])] == sorted(OFFERS)

    def test_rodada_vem_ordenada_pelo_eprofit_amostrado(self):
        rodada = OfferBandit(seed=1).classificar_ofertas(TENANT_PADRAO, "PJ", 0.8, mrr=550.0)
        valores = [l["eprofit_amostrado"] for l in rodada]
        assert valores == sorted(valores, reverse=True)

    @pytest.mark.parametrize("profile", PROFILES)
    @pytest.mark.parametrize("seed", [1, 7, 42, 2026])
    def test_primeira_da_rodada_e_a_escolha_de_choose_offer(self, profile, seed):
        """Expor a rodada NÃO pode ter mudado a decisão: com a mesma semente,
        `choose_offer` e `classificar_ofertas()[0]` são a mesma oferta."""
        rodada = OfferBandit(seed=seed).classificar_ofertas(TENANT_PADRAO, profile, 0.8, mrr=400.0)
        escolha = OfferBandit(seed=seed).choose_offer(TENANT_PADRAO, profile, 0.8, mrr=400.0)
        assert rodada[0]["offer"] == escolha

    def test_probabilidade_e_a_media_do_posterior(self):
        """`p_estimado` é a probabilidade APRENDIDA (média Beta), a mesma que
        `conversion_rates` devolve — não um número inventado para a tela."""
        bandit = OfferBandit(seed=3)
        taxas = bandit.conversion_rates(TENANT_PADRAO, "freelancer")
        for linha in bandit.classificar_ofertas(TENANT_PADRAO, "freelancer", 0.7):
            assert linha["p_estimado"] == taxas[linha["offer"]]
            assert 0.0 <= linha["p_amostrado"] <= 1.0

    def test_nao_existe_braco_sem_oferta_nem_humano(self):
        rodada = OfferBandit(seed=5).classificar_ofertas(TENANT_PADRAO, "CLT", 0.95)
        for linha in rodada:
            assert linha["offer"] in OFFERS
            assert linha["offer"] != "consulta_cs"


# ── (b) o nó carrega a rodada ────────────────────────────────────────────

class TestNoChooseOffer:

    @pytest.mark.asyncio
    async def test_no_carrega_tres_consideradas_e_a_escolhida_e_a_primeira(self):
        resultado = await va.choose_offer(_estado())
        consideradas = resultado["ofertas_consideradas"]
        assert len(consideradas) == va.N_CANDIDATAS == 3
        assert consideradas[0]["offer"] == resultado["offer_type"]
        assert len({l["offer"] for l in consideradas}) == 3


# ── (c) três candidatas, uma escolhida ───────────────────────────────────

class TestTresCandidatas:

    @pytest.mark.asyncio
    async def test_tres_ofertas_diferentes_uma_escolhida(self, monkeypatch):
        _claude(monkeypatch, "Texto gerado para a vencedora.")
        estado = await va.choose_offer(_estado())
        resultado = await va.generate_message(estado)

        candidatas = resultado["candidatas"]
        assert len(candidatas) == 3
        assert len({c["oferta"] for c in candidatas}) == 3, "três variações da mesma oferta"
        escolhidas = [c for c in candidatas if c["escolhida"]]
        assert len(escolhidas) == 1
        assert escolhidas[0]["oferta"] == resultado["offer_type"]

    @pytest.mark.asyncio
    async def test_message_continua_sendo_o_texto_da_escolhida(self, monkeypatch):
        """O caminho antigo: `message` é UMA string, e é a da vencedora."""
        _claude(monkeypatch, "Texto gerado para a vencedora.")
        resultado = await va.generate_message(await va.choose_offer(_estado()))
        escolhida = next(c for c in resultado["candidatas"] if c["escolhida"])
        assert resultado["message"] == "Texto gerado para a vencedora."
        assert escolhida["texto"] == resultado["message"]
        assert escolhida["origem_texto"] == "gerado"

    @pytest.mark.asyncio
    async def test_cada_candidata_tem_texto_oferta_e_probabilidade(self, monkeypatch):
        _claude(monkeypatch)
        resultado = await va.generate_message(await va.choose_offer(_estado("alto")))
        for c in resultado["candidatas"]:
            assert c["texto"].strip()
            assert c["oferta"] in OFFERS
            assert isinstance(c["p_sucesso"], float) and 0.0 <= c["p_sucesso"] <= 1.0
            assert c["motivo"]

    @pytest.mark.asyncio
    async def test_a_escolha_e_do_bandit_nao_da_montagem(self, monkeypatch):
        """`montar_candidatas` não refaz a decisão: a escolhida é `offer_type`,
        mesmo que outra linha da rodada tenha e-Profit maior (um estado
        montado à mão, para provar que a função só expõe, não decide)."""
        _claude(monkeypatch)
        estado = _estado(offer_type="pausa_1_mes", ofertas_consideradas=[
            {"offer": "desconto_20", "p_estimado": 0.9, "p_amostrado": 0.9,
             "eprofit_amostrado": 999.0, "custo": 1.0},
            {"offer": "pausa_1_mes", "p_estimado": 0.5, "p_amostrado": 0.5,
             "eprofit_amostrado": 10.0, "custo": 1.0},
            {"offer": "desconto_10", "p_estimado": 0.4, "p_amostrado": 0.4,
             "eprofit_amostrado": 5.0, "custo": 1.0},
        ])
        resultado = await va.generate_message(estado)
        escolhida = next(c for c in resultado["candidatas"] if c["escolhida"])
        assert escolhida["oferta"] == "pausa_1_mes"

    @pytest.mark.asyncio
    async def test_uma_chamada_a_api_por_evento(self, monkeypatch):
        """Três candidatas não são três chamadas: só a vencedora é gerada;
        as outras usam o template da própria oferta."""
        espiao = _claude(monkeypatch)
        resultado = await va.generate_message(await va.choose_offer(_estado()))
        assert len(espiao.chamadas) == 1
        templates = [c for c in resultado["candidatas"] if not c["escolhida"]]
        assert all(c["origem_texto"] == "template" for c in templates)


# ── (d) INVARIANTE: ninguém encaminha para humano ────────────────────────

class TestNenhumaCandidataEncaminhaParaHumano:

    def test_lista_de_termos_cobre_o_basico(self):
        for termo in ("atendente", "suporte", "humano", "fale com", "consultor"):
            assert termo in va.TERMOS_DE_ENCAMINHAMENTO_HUMANO

    @pytest.mark.parametrize("assinatura", [None, "Marina"])
    @pytest.mark.parametrize("criticality", ["padrao", "alto", "critico"])
    @pytest.mark.parametrize("oferta", OFFERS)
    def test_template_nunca_encaminha(self, monkeypatch, assinatura, criticality, oferta):
        """O texto de reserva, em TODAS as combinações, passa no filtro."""
        if assinatura:
            monkeypatch.setenv("CRAI_CS_SIGNATURE_NAME", assinatura)
        texto = va._fallback_de_retencao(criticality, va.OFFER_LABELS[oferta])
        assert va.encaminha_para_humano(texto) is None, texto

    @pytest.mark.asyncio
    @pytest.mark.parametrize("criticality", ["padrao", "alto", "critico"])
    async def test_as_tres_candidatas_passam_no_filtro(self, monkeypatch, criticality):
        _claude(monkeypatch, "Preparamos algo para você continuar com a gente.")
        resultado = await va.generate_message(await va.choose_offer(_estado(criticality)))
        for c in resultado["candidatas"]:
            assert va.encaminha_para_humano(c["texto"]) is None, c["texto"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("texto_da_api", [
        "Fale com nosso atendente e resolvemos juntos.",
        "Nosso suporte vai entrar em contato com você.",
        "Um consultor vai te ligar ainda hoje.",
        "Agende uma conversa com nossa equipe de sucesso do cliente.",
    ])
    async def test_texto_da_api_que_encaminha_e_descartado(self, monkeypatch, texto_da_api):
        """Se a Claude API sugerir humano, o texto NÃO chega ao cliente: entra
        o template. Quem garante a invariante é o código, não o prompt."""
        _claude(monkeypatch, texto_da_api)
        resultado = await va.generate_message(await va.choose_offer(_estado("critico")))
        assert resultado["message"] != texto_da_api
        for c in resultado["candidatas"]:
            assert va.encaminha_para_humano(c["texto"]) is None, c["texto"]
            assert c["texto"] != texto_da_api

    @pytest.mark.asyncio
    async def test_api_fora_do_ar_tambem_nao_encaminha(self, monkeypatch):
        _claude(monkeypatch, erro=RuntimeError("Claude fora do ar"))
        resultado = await va.generate_message(await va.choose_offer(_estado("critico")))
        assert len(resultado["candidatas"]) == 3
        for c in resultado["candidatas"]:
            assert va.encaminha_para_humano(c["texto"]) is None

    @pytest.mark.asyncio
    async def test_texto_de_fallback_e_declarado_template_nao_gerado(self, monkeypatch):
        """O painel não pode dizer "gerado" onde houve template: com a API
        fora do ar, ou com o texto descartado pelo filtro, a vencedora sai
        marcada `origem_texto: "template"`."""
        _claude(monkeypatch, erro=RuntimeError("Claude fora do ar"))
        resultado = await va.generate_message(await va.choose_offer(_estado()))
        assert next(c for c in resultado["candidatas"] if c["escolhida"])["origem_texto"] == "template"

        _claude(monkeypatch, "Fale com nosso atendente.")
        resultado = await va.generate_message(await va.choose_offer(_estado()))
        assert next(c for c in resultado["candidatas"] if c["escolhida"])["origem_texto"] == "template"


# ── (e) chamador antigo ──────────────────────────────────────────────────

class TestCaminhoAntigo:

    @pytest.mark.asyncio
    async def test_estado_sem_rodada_produz_uma_candidata(self, monkeypatch):
        """Quem monta o estado sem passar por `choose_offer` (testes de nó
        isolado, chamadores antigos) continua recebendo `message` — e uma
        lista de uma candidata, a escolhida, com a probabilidade do bandit."""
        _claude(monkeypatch, "texto")
        resultado = await va.generate_message(_estado(offer_type="desconto_20"))
        assert resultado["message"] == "texto"
        assert len(resultado["candidatas"]) == 1
        assert resultado["candidatas"][0]["escolhida"] is True
        assert resultado["candidatas"][0]["oferta"] == "desconto_20"
        assert isinstance(resultado["candidatas"][0]["p_sucesso"], float)

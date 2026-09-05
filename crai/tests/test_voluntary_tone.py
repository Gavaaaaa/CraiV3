"""tests/test_voluntary_tone.py — o tom acompanha a criticidade, a oferta não.

O Sprint 1 tirou a escalação humana do churn voluntário. O que a escalação
resolvia mal — dar tratamento diferente a quem está prestes a sair, ou a quem
paga muito — passa a ser resolvido no ÚNICO lugar onde isso não custa
autonomia: o texto da mensagem.

A distinção que este arquivo protege é essa: `criticality` muda o PROMPT e o
FALLBACK, e nada mais. Não muda a oferta (quem escolhe é o bandit, por
e-Profit), não muda o canal, não desvia o grafo, não chama ninguém. Um teste
que deixasse a criticidade vazar para a decisão estaria medindo o retorno do
`consulta_cs` por outra porta.

**O que aqui é prova de regressão e o que é catraca.** Tudo neste arquivo
reprova em `730205b` (fim do Sprint 1) por ImportError — `classify_criticality`
não existia. Dentro disso, dois testes são catracas do que NÃO pode mudar:

    test_prompt_padrao_e_o_texto_de_antes  — o caso comum tem que sair igual
    test_modelo_e_max_tokens_intactos      — o sprint proíbe mexer na chamada

ISOLAMENTO: nenhum teste aqui chama a Claude API. `claude.messages.create` é
substituído por um espião que captura o prompt; o caminho de fallback é
exercitado levantando a exceção de dentro do espião.

Uso:
    pytest tests/test_voluntary_tone.py -v
"""

import types

import pytest

from crai.churn_voluntary import risk_scorer as rs
from crai.churn_voluntary import voluntary_agent as va
from crai.churn_voluntary.offer_bandit import OFFERS
from crai.churn_voluntary.risk_scorer import (
    HIGH_VALUE_MRR_DEFAULT,
    classify_criticality,
    limiar_de_alto_valor,
    mrr_utilizavel,
)


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    """Nenhum teste herda env de assinatura ou de limiar de outro."""
    monkeypatch.delenv("CRAI_CS_SIGNATURE_NAME", raising=False)
    monkeypatch.delenv("CRAI_HIGH_VALUE_MRR_THRESHOLD", raising=False)


class EspiaoClaude:
    """Captura o prompt em vez de chamar a API. Opcionalmente explode."""

    def __init__(self, texto="mensagem gerada", erro=None):
        self.texto = texto
        self.erro = erro
        self.chamadas = []

    async def create(self, **kwargs):
        self.chamadas.append(kwargs)
        if self.erro:
            raise self.erro
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(text=self.texto)])

    @property
    def prompt(self) -> str:
        return self.chamadas[-1]["messages"][0]["content"]


@pytest.fixture
def espiao(monkeypatch):
    e = EspiaoClaude()
    monkeypatch.setattr(va.claude.messages, "create", e.create)
    return e


def _estado(criticality="padrao", **extra):
    return {
        "user_id": "usr_teste", "event": "Cancellation Page Viewed",
        "props": {}, "risk_score": 0.90, "profile": "PJ",
        "is_critical": True, "criticality": criticality,
        "offer_type": "desconto_20", "channel": "email", "on_site_now": False,
        "prior_channel_success": None, "message": None,
        "offer_sent": False, "accepted": None, "retained": False, **extra,
    }


# ── Classificação ────────────────────────────────────────────────────────

class TestClassifyCriticality:

    @pytest.mark.parametrize("risco", [0.90, 0.95, 1.0])
    def test_risco_critico(self, risco):
        assert classify_criticality(risco, None) == "critico"

    @pytest.mark.parametrize("risco", [0.75, 0.80, 0.89])
    def test_risco_alto(self, risco):
        assert classify_criticality(risco, None) == "alto"

    @pytest.mark.parametrize("risco", [0.0, 0.5, 0.74])
    def test_risco_padrao(self, risco):
        assert classify_criticality(risco, None) == "padrao"

    def test_mrr_alto_com_risco_baixo_e_critico(self):
        """A porta do VALOR é independente da porta do RISCO.

        Um cliente de R$ 2.500/mês que só abriu a página de cancelamento uma
        vez não pode receber o texto genérico: o custo de errar com ele é
        outro. Este é o caso que o plano do sprint pediu explicitamente.
        """
        assert classify_criticality(0.10, 2500.0) == "critico"
        assert classify_criticality(0.10, None) == "padrao"

    def test_limiar_de_valor_e_fechado(self):
        assert classify_criticality(0.0, HIGH_VALUE_MRR_DEFAULT) == "critico"
        assert classify_criticality(0.0, HIGH_VALUE_MRR_DEFAULT - 0.01) == "padrao"

    def test_env_muda_o_limiar_de_valor(self, monkeypatch):
        monkeypatch.setenv("CRAI_HIGH_VALUE_MRR_THRESHOLD", "500")
        assert limiar_de_alto_valor() == 500.0
        assert classify_criticality(0.0, 600.0) == "critico"
        assert classify_criticality(0.0, 400.0) == "padrao"

    def test_env_ilegivel_cai_no_default_sem_quebrar(self, monkeypatch):
        """Uma variável mal digitada não pode derrubar o pipeline."""
        monkeypatch.setenv("CRAI_HIGH_VALUE_MRR_THRESHOLD", "dois mil")
        assert limiar_de_alto_valor() == HIGH_VALUE_MRR_DEFAULT
        assert classify_criticality(0.0, 2500.0) == "critico"

    @pytest.mark.parametrize("bruto", [
        "2500", "2.500,00", "R$ 2500", [2500], {"v": 2500}, True, False,
        float("inf"), float("nan"), 10 ** 400, -100,
    ])
    def test_mrr_inutilizavel_nao_quebra_e_nao_promove(self, bruto):
        """`props` é payload de terceiro: nada ali pode virar `TypeError` no nó.

        Texto é recusado de propósito — `"10,000"` é dez mil em en-US e dez em
        pt-BR, e o evento do Segment não declara locale. Sem MRR utilizável, o
        risco classifica sozinho.
        """
        assert mrr_utilizavel(bruto) is None
        assert classify_criticality(0.10, bruto) == "padrao"
        assert classify_criticality(0.95, bruto) == "critico"   # o risco ainda manda

    def test_mrr_numerico_em_texto_nao_promove_por_engano(self):
        """A recusa de texto tem um custo declarado, e este teste o fixa:
        R$ 10.000 escrito como "10,000" NÃO vira crítico — mas também não vira
        R$ 10,00 em silêncio, que era o desfecho pior."""
        assert classify_criticality(0.10, "10,000") == "padrao"


# ── Prompt por criticidade ───────────────────────────────────────────────

class TestPromptPorCriticidade:

    @pytest.mark.asyncio
    async def test_tres_niveis_tres_prompts_distintos(self, espiao):
        prompts = {}
        for nivel in ("padrao", "alto", "critico"):
            await va.generate_message(_estado(nivel))
            prompts[nivel] = espiao.prompt

        assert len(set(prompts.values())) == 3, "níveis diferentes geraram o mesmo prompt"

    @pytest.mark.asyncio
    async def test_prompt_padrao_e_o_texto_de_antes(self, espiao):
        """CATRACA. O caso comum não podia mudar junto com os dois novos.

        Texto de `730205b`, palavra por palavra.
        """
        await va.generate_message(_estado("padrao"))
        assert espiao.prompt == (
            "Gere uma mensagem curta de retenção para um cliente que demonstrou risco de cancelar.\n"
            "Evento: Cancellation Page Viewed | Canal: email | Oferta: 20% de desconto por 3 meses\n"
            "Tom empático, sem culpar o cliente, no máximo 3 frases, português brasileiro natural.\n"
            "Retorne APENAS a mensagem.")

    @pytest.mark.asyncio
    async def test_prompt_critico_pede_tom_pessoal(self, espiao):
        await va.generate_message(_estado("critico"))
        prompt = espiao.prompt.lower()
        assert "crítica" in prompt
        assert "pessoal e de alto cuidado" in prompt
        assert "valor da relação" in prompt

    @pytest.mark.asyncio
    async def test_prompt_alto_pede_proatividade_e_prazo(self, espiao):
        await va.generate_message(_estado("alto"))
        prompt = espiao.prompt.lower()
        assert "risco alto" in prompt
        assert "proativo" in prompt
        assert "7 dias" in prompt

    @pytest.mark.asyncio
    async def test_oferta_e_canal_atravessam_os_tres_niveis(self, espiao):
        """Criticidade é tom. A oferta escolhida pelo bandit e o canal
        continuam chegando ao prompt inalterados nos três níveis."""
        for nivel in ("padrao", "alto", "critico"):
            await va.generate_message(_estado(nivel))
            assert "20% de desconto por 3 meses" in espiao.prompt
            assert "Canal: email" in espiao.prompt

    @pytest.mark.asyncio
    async def test_modelo_e_max_tokens_intactos(self, espiao):
        """CATRACA. O sprint proíbe mexer na chamada existente."""
        await va.generate_message(_estado("critico"))
        assert espiao.chamadas[-1]["model"] == "claude-sonnet-4-20250514"
        assert espiao.chamadas[-1]["max_tokens"] == 200

    @pytest.mark.asyncio
    async def test_estado_sem_criticality_cai_no_padrao(self, espiao):
        """Robustez: um estado montado por outro caminho não pode derrubar o nó."""
        estado = _estado()
        del estado["criticality"]
        await va.generate_message(estado)
        assert "situação CRÍTICA" not in espiao.prompt
        assert "risco ALTO" not in espiao.prompt


# ── Assinatura ───────────────────────────────────────────────────────────

class TestAssinatura:

    @pytest.mark.asyncio
    async def test_sem_env_proibe_inventar_nome(self, espiao):
        await va.generate_message(_estado("critico"))
        assert "Não assine com nome de pessoa nenhuma" in espiao.prompt

    @pytest.mark.asyncio
    async def test_com_env_assina_com_o_nome_configurado(self, espiao, monkeypatch):
        monkeypatch.setenv("CRAI_CS_SIGNATURE_NAME", "Marina")
        await va.generate_message(_estado("critico"))
        assert '"— Marina, time CRAI"' in espiao.prompt

    @pytest.mark.asyncio
    async def test_env_so_com_espacos_conta_como_ausente(self, espiao, monkeypatch):
        monkeypatch.setenv("CRAI_CS_SIGNATURE_NAME", "   ")
        await va.generate_message(_estado("critico"))
        assert "Não assine com nome de pessoa nenhuma" in espiao.prompt

    @pytest.mark.asyncio
    async def test_assinatura_nao_aparece_nos_outros_niveis(self, espiao, monkeypatch):
        monkeypatch.setenv("CRAI_CS_SIGNATURE_NAME", "Marina")
        for nivel in ("padrao", "alto"):
            await va.generate_message(_estado(nivel))
            assert "Marina" not in espiao.prompt


# ── Fallback ─────────────────────────────────────────────────────────────

class TestFallbackSemClaude:

    @pytest.fixture
    def api_fora(self, monkeypatch):
        e = EspiaoClaude(erro=RuntimeError("Claude fora do ar"))
        monkeypatch.setattr(va.claude.messages, "create", e.create)
        return e

    @pytest.mark.asyncio
    async def test_critico_tem_fallback_proprio(self, api_fora):
        """Quem mais precisa do texto cuidadoso é justamente quem não pode
        receber o genérico quando a API cai."""
        resultado = await va.generate_message(_estado("critico"))
        assert "não queremos te perder" in resultado["message"]
        assert "Antes de você ir" not in resultado["message"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("nivel", ["padrao", "alto"])
    async def test_demais_niveis_mantem_o_fallback_de_antes(self, api_fora, nivel):
        resultado = await va.generate_message(_estado(nivel))
        assert resultado["message"] == (
            "Antes de você ir, que tal 20% de desconto por 3 meses? "
            "Estamos aqui para ajudar.")

    @pytest.mark.asyncio
    async def test_fallback_critico_assina_quando_ha_nome(self, api_fora, monkeypatch):
        monkeypatch.setenv("CRAI_CS_SIGNATURE_NAME", "Marina")
        resultado = await va.generate_message(_estado("critico"))
        assert resultado["message"].endswith("— Marina, time CRAI")

    @pytest.mark.asyncio
    async def test_fallback_critico_sem_nome_nao_assina(self, api_fora):
        resultado = await va.generate_message(_estado("critico"))
        assert "time CRAI" not in resultado["message"]


# ── Integração no grafo ──────────────────────────────────────────────────

class TestCriticidadeNoGrafo:

    @pytest.mark.asyncio
    async def test_assess_risk_publica_a_criticidade(self):
        estado = _estado(event="Cancellation Page Viewed")
        estado["props"] = {"billing_profile": "PJ"}
        resultado = await va.assess_risk(estado)
        assert resultado["criticality"] == "critico"   # risco fixo 0.90

    @pytest.mark.asyncio
    async def test_assess_risk_le_o_mrr_do_evento(self):
        """MRR alto com evento de risco baixo → crítico pela porta do valor."""
        estado = _estado(event="Session Started")
        estado["props"] = {"billing_profile": "PJ", "days_since_last": 1,
                           "features_used_30d": 20, "mrr": 5000.0}
        resultado = await va.assess_risk(estado)
        assert resultado["risk_score"] < rs.HIGH_RISK_THRESHOLD
        assert resultado["criticality"] == "critico"

    @pytest.mark.asyncio
    async def test_mrr_ilegivel_nao_derruba_a_escolha_de_oferta(self):
        """REGRESSÃO 🔴, medida em `730205b`.

        `mrr` entra em aritmética dentro do bandit (`MESES_LTV_RETIDO * mrr`,
        `0.10 * 3 * mrr`) e chegava cru do `props`. Um evento do Segment
        ASSINADO com `mrr: "2500"` devolvia HTTP 500 — nas três formas medidas
        ("2500", [2500], {"v": 1}). A borda validava `billing_profile` e
        deixava `mrr` passar; o estouro acontecia dois nós adiante.

        Sem número utilizável o bandit cai no MRR típico do perfil, que é o
        que ele já fazia quando o evento não trazia plano nenhum.
        """
        for bruto in ("2500", [2500], {"v": 1}, True, float("inf")):
            estado = _estado("critico")
            estado["props"] = {"billing_profile": "PJ", "mrr": bruto}
            resultado = await va.choose_offer(estado)
            assert resultado["offer_type"] in OFFERS

    @pytest.mark.asyncio
    async def test_mrr_numerico_continua_chegando_ao_bandit(self, monkeypatch):
        """CATRACA: a defesa acima não pode ter cortado o caminho válido."""
        recebidos = []
        real = va._bandit.choose_offer

        def espiao(profile, risk_score, mrr=None):
            recebidos.append(mrr)
            return real(profile, risk_score, mrr=mrr)

        monkeypatch.setattr(va._bandit, "choose_offer", espiao)

        estado = _estado("critico")
        estado["props"] = {"billing_profile": "PJ", "mrr": 4200.0}
        await va.choose_offer(estado)
        assert recebidos == [4200.0]

    @pytest.mark.asyncio
    async def test_criticidade_nao_mexe_na_oferta(self):
        """O contrato inteiro deste sprint em um teste: mesma semente, mesmo
        perfil, mesma oferta — a criticidade não toca a decisão do bandit."""
        from crai.churn_voluntary.offer_bandit import OfferBandit

        escolhas = []
        for risco in (0.10, 0.80, 0.95):
            bandit = OfferBandit(seed=7)
            escolhas.append(bandit.choose_offer("PJ", risco, mrr=550.0))

        assert len(set(escolhas)) == 1, (
            f"a oferta variou com o risco: {escolhas}")

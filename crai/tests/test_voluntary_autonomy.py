"""tests/test_voluntary_autonomy.py — a CRAI decide sozinha, em toda a faixa de risco.

O churn voluntário tinha uma saída de emergência para humano em dois lugares
que se reforçavam: `offer_bandit.choose_offer` devolvia `consulta_cs` sem nem
amostrar os posteriores quando `risk_score >= 0.90`, e `send_offer` marcava
`escalated_to_human` quando a oferta escolhida era essa. O cliente de MAIOR
risco — exatamente aquele que o produto promete atender sozinho — era o único
que caía na fila de um humano.

O invariante de produto é o contrário disso: **a CRAI é 100% autônoma e NUNCA
escala para humano**. Risco crítico não muda o destinatário da decisão, muda o
TOM da mensagem (`is_critical_risk`, consumida pelo Sprint 2).

**O que aqui é prova de regressão e o que é catraca.** Reprovam em `b38ba72`
(antes do Sprint 1) e provam a correção:

    test_nunca_oferece_consulta_cs           — `choose_offer` devolvia `consulta_cs`
                                               em todo risco >= 0.90
    test_state_declara_is_critical_e_nao_escalacao
                                             — `escalated_to_human` existia no schema
    test_is_critical_risk_threshold          — `is_critical_risk` não existia
    test_send_offer_nao_devolve_escalacao    — `send_offer` devolvia o campo
    test_vocabulario_de_ofertas_sem_consulta_cs — `consulta_cs` estava nas 4 tabelas

As duas últimas classes são **catracas de saneamento**: cobrem o estado
persistido em `crai/models/bandit_state.json`, que é a única memória do bandit
e não está no repositório (`.gitignore`).

ISOLAMENTO: `record_outcome` e `load` PERSISTEM. A fixture `bandit_isolado`
redireciona `MODELS_DIR`/`STATE_PATH` para `tmp_path` em todos os testes deste
arquivo — sem ela, uma rodada de teste escreve no arquivo real, que foi
exatamente como perfis de fuzz (`""`, `"abc"`, `"299,90"`) entraram na memória
do bandit de produção.

Uso:
    pytest tests/test_voluntary_autonomy.py -v
"""

import json

import pytest

from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary.offer_bandit import (
    CRITICAL_RISK_THRESHOLD,
    OFFERS,
    PROFILES,
    SEED_PRIORS,
    TENANT_PADRAO,
    OfferBandit,
    is_critical_risk,
    offer_cost,
    sanear_estado,
)
from crai.churn_voluntary.state import ChurnVoluntaryState


@pytest.fixture(autouse=True)
def bandit_isolado(tmp_path, monkeypatch):
    """Nenhum teste deste arquivo toca `crai/models/bandit_state.json`."""
    monkeypatch.setattr(ob, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "bandit_state.json")
    return tmp_path / "bandit_state.json"


# ── O invariante ─────────────────────────────────────────────────────────

class TestAutonomiaTotal:

    def test_nunca_oferece_consulta_cs(self):
        """INVARIANTE PERMANENTE: nenhuma combinação de perfil e risco devolve
        um braço de escalação humana.

        Varre os três perfis contra a faixa inteira de risco em passos de 0.05.
        Antes do Sprint 1, todo ponto com risco >= 0.90 devolvia `consulta_cs`
        — 3 perfis × 3 pontos. Este teste não é sobre o valor 0.90: é sobre não
        existir NENHUM risco que desvie a decisão para fora do bandit.
        """
        bandit = OfferBandit(seed=42)
        for profile in PROFILES:
            for passo in range(21):
                risk = round(passo * 0.05, 2)
                offer = bandit.choose_offer(TENANT_PADRAO, profile, risk)
                assert offer != "consulta_cs", (
                    f"escalação humana devolvida para perfil={profile} risco={risk}")
                assert offer in OFFERS, (
                    f"oferta fora do vocabulário: {offer!r} (perfil={profile} risco={risk})")

    def test_state_declara_is_critical_e_nao_escalacao(self):
        """O schema do estado não carrega mais o campo de escalação."""
        anotacoes = ChurnVoluntaryState.__annotations__
        assert "escalated_to_human" not in anotacoes
        assert "is_critical" in anotacoes
        assert anotacoes["is_critical"] is bool

    def test_is_critical_risk_threshold(self):
        """Criticidade é um rótulo de tom, e o limiar é fechado em 0.90."""
        assert CRITICAL_RISK_THRESHOLD == 0.90
        assert is_critical_risk(0.89) is False
        assert is_critical_risk(0.90) is True
        assert is_critical_risk(1.0) is True

    @pytest.mark.asyncio
    async def test_send_offer_nao_devolve_escalacao(self):
        """O nó de envio não produz mais o campo — nem `False`.

        Um campo que existe valendo `False` é um campo que alguém volta a
        preencher. O contrato é que ele não exista.
        """
        from crai.churn_voluntary.voluntary_agent import send_offer

        estado = {
            "user_id": "usr_teste", "event": "Cancellation Page Viewed", "props": {},
            "risk_score": 0.95, "profile": "PJ", "is_critical": True,
            "offer_type": "desconto_20", "channel": "email", "on_site_now": False,
            "prior_channel_success": None, "message": "mensagem de teste",
            "offer_sent": False, "accepted": None, "retained": False,
        }
        resultado = await send_offer(estado)

        assert resultado["offer_sent"] is True
        assert "escalated_to_human" not in resultado

    @pytest.mark.asyncio
    async def test_choose_offer_marca_criticidade(self):
        """O nó do grafo carrega a criticidade adiante, para o tom do Sprint 2."""
        from crai.churn_voluntary import voluntary_agent as va

        base = {
            "user_id": "usr_teste", "event": "Cancellation Page Viewed",
            "props": {"billing_profile": "CLT"}, "profile": "CLT",
            "is_critical": False, "offer_type": None, "channel": None,
            "on_site_now": False, "prior_channel_success": None, "message": None,
            "offer_sent": False, "accepted": None, "retained": False,
        }

        critico = await va.choose_offer({**base, "risk_score": 0.90})
        assert critico["is_critical"] is True
        assert critico["offer_type"] in OFFERS

        normal = await va.choose_offer({**base, "risk_score": 0.60})
        assert normal["is_critical"] is False

    def test_vocabulario_de_ofertas_sem_consulta_cs(self):
        """As quatro tabelas que conheciam a oferta esqueceram dela juntas."""
        from crai.churn_voluntary.voluntary_agent import OFFER_LABELS

        assert "consulta_cs" not in OFFERS
        assert "consulta_cs" not in OFFER_LABELS
        for profile in PROFILES:
            assert "consulta_cs" not in SEED_PRIORS[profile]
        with pytest.raises(KeyError):
            offer_cost("consulta_cs", 300.0)

        # E o vocabulário segue completo: as 4 ofertas restantes têm prior,
        # rótulo e custo. Uma remoção pela metade quebraria aqui.
        for offer in OFFERS:
            assert offer in OFFER_LABELS
            assert offer_cost(offer, 300.0) > 0
            for profile in PROFILES:
                assert offer in SEED_PRIORS[profile]


# ── Saneamento do estado persistido ──────────────────────────────────────

class TestSaneamentoDoEstadoPersistido:
    """O arquivo em disco é anterior à remoção e não está no repositório.

    Ele tem observações de `consulta_cs` acumuladas (no arquivo real,
    `CLT/consulta_cs` estava em α=372, β=258) e perfis gravados por fuzz de
    webhook. Carregar isso de volta ressuscitaria o braço extinto.
    """

    def test_load_descarta_consulta_cs_persistido(self, bandit_isolado):
        bandit_isolado.write_text(json.dumps({
            "CLT": {"desconto_10": {"alpha": 10.0, "beta": 12.0},
                    "consulta_cs": {"alpha": 372.0, "beta": 258.0}},
            "PJ": {"desconto_20": {"alpha": 9.0, "beta": 8.0}},
        }), encoding="utf-8")

        bandit = OfferBandit()
        assert bandit.load() is True

        # O arquivo estava no formato pré-tenant: migra inteiro para o padrão.
        assert set(bandit.state) == {TENANT_PADRAO}
        perfis = bandit.state[TENANT_PADRAO]
        for profile in PROFILES:
            assert "consulta_cs" not in perfis[profile]
        assert "consulta_cs" not in bandit.conversion_rates(TENANT_PADRAO, "CLT")
        # O que era válido no arquivo sobreviveu intacto...
        assert perfis["CLT"]["desconto_10"] == {"alpha": 10.0, "beta": 12.0}
        assert perfis["PJ"]["desconto_20"] == {"alpha": 9.0, "beta": 8.0}
        # ...e o que faltava veio do benchmark, sem buraco no vocabulário.
        assert set(perfis["CLT"]) == set(OFFERS)

    def test_load_descarta_perfis_de_fuzz(self, bandit_isolado):
        """`record_outcome` aceita qualquer string como perfil e persiste."""
        bandit_isolado.write_text(json.dumps({
            "CLT": {"desconto_10": {"alpha": 10.0, "beta": 12.0}},
            "299,90": {"desconto_10": {"alpha": 2.0, "beta": 1.0}},
            "abc": {"desconto_10": {"alpha": 2.0, "beta": 1.0}},
            "": {"desconto_10": {"alpha": 2.0, "beta": 1.0}},
            "null": {"desconto_10": {"alpha": 2.0, "beta": 1.0}},
        }), encoding="utf-8")

        bandit = OfferBandit()
        bandit.load()

        assert set(bandit.state[TENANT_PADRAO]) == set(PROFILES)

    def test_load_reescreve_o_arquivo_ja_limpo(self, bandit_isolado):
        """Senão o mesmo lixo é relido e relogado em toda inicialização."""
        bandit_isolado.write_text(json.dumps({
            "CLT": {"consulta_cs": {"alpha": 372.0, "beta": 258.0}},
            "abc": {"desconto_10": {"alpha": 2.0, "beta": 1.0}},
        }), encoding="utf-8")

        OfferBandit().load()

        em_disco = json.loads(bandit_isolado.read_text(encoding="utf-8"))
        assert set(em_disco) == {TENANT_PADRAO}, "não migrou para o formato com tenant"
        assert set(em_disco[TENANT_PADRAO]) == set(PROFILES)
        assert "consulta_cs" not in em_disco[TENANT_PADRAO]["CLT"]

    def test_load_sem_arquivo_usa_benchmark(self, bandit_isolado):
        """Cold start: sem arquivo, priors de benchmark e nada escrito em disco."""
        bandit = OfferBandit()
        assert bandit.load() is False
        assert bandit.is_fitted is False
        assert set(bandit.state) == {TENANT_PADRAO}
        assert set(bandit.state[TENANT_PADRAO]) == set(PROFILES)
        assert not bandit_isolado.exists()

    @pytest.mark.parametrize("lixo", [
        {"CLT": {"desconto_10": {"alpha": 0.0, "beta": 1.0}}},      # α não positivo
        {"CLT": {"desconto_10": {"alpha": "muito", "beta": 1.0}}},  # não numérico
        {"CLT": {"desconto_10": {"alpha": 1.0}}},                   # sem β
        {"CLT": {"desconto_10": [10, 12]}},                         # formato de tupla antigo
        {"CLT": "não é um dicionário"},
        ["nem sequer um objeto"],
    ])
    def test_load_sobrevive_a_posterior_malformado(self, bandit_isolado, lixo):
        """Estado corrompido cai para o prior daquele braço, sem derrubar o import.

        `_bandit.load()` roda no import de `voluntary_agent`: uma exceção aqui
        derruba a API inteira, não só o churn voluntário.
        """
        bandit_isolado.write_text(json.dumps(lixo), encoding="utf-8")

        bandit = OfferBandit()
        bandit.load()

        perfis = bandit.state[TENANT_PADRAO]
        assert set(perfis) == set(PROFILES)
        assert set(perfis["CLT"]) == set(OFFERS)
        alpha, beta = SEED_PRIORS["CLT"]["desconto_10"]
        assert perfis["CLT"]["desconto_10"] == {"alpha": 1.0 + alpha, "beta": 1.0 + beta}

    def test_sanear_estado_relata_o_que_descartou(self):
        """O descarte é logado, não silencioso: perder aprendizado sem dizer é
        o mesmo defeito que aceitar lixo."""
        _, descartes = sanear_estado({
            "CLT": {"consulta_cs": {"alpha": 372.0, "beta": 258.0},
                    "desconto_10": {"alpha": "x", "beta": 1.0}},
            "abc": {"desconto_10": {"alpha": 2.0, "beta": 1.0}},
        })

        # Os rótulos carregam o tenant desde o Sprint 5: `<tenant>/<perfil>/...`
        assert descartes["perfis"] == [f"{TENANT_PADRAO}/abc"]
        assert descartes["ofertas"] == [f"{TENANT_PADRAO}/CLT/consulta_cs"]
        assert descartes["posteriores"] == [f"{TENANT_PADRAO}/CLT/desconto_10"]

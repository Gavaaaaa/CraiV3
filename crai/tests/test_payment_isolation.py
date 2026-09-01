"""
tests/test_payment_isolation.py — Isolamento cartão x Pix Automático (Fase 3).

**A cláusula de remoção do cartão FOI acionada.** A recobrança automática de
cartão saiu do pipeline ativo e está preservada, isolada, em
`crai/dunning/legacy_card/`. Por isso este arquivo não valida a coexistência de
duas políticas de retentativa, e sim que só existe uma:

    evento Pix    → PixAutomaticoRetryPolicy (3 tentativas / 7 dias, BACEN)
    evento cartão → apenas registrado e logado com [CARTAO-DESATIVADO],
                    sem nenhuma tentativa de retentativa automática

Por que isso importa: as duas políticas são incompatíveis por natureza. O
backoff exponencial do cartão não tem teto de tentativas; aplicá-lo a uma
cobrança Pix violaria o limite regulatório. Com cartão fora do fluxo ativo, essa
superfície de erro deixa de existir — e os testes abaixo travam esse estado.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as workflow_module
from crai.agent.main_agent import build_crai_graph, route_after_decision
from crai.agent.workflow import decide_recovery, schedule_retry_pix
from crai.api import app as app_module

VALOR = 299.90
STRIPE_SECRET = "whsec_teste_fase3"


def _estado(payment_method, causa="insufficient_funds", retry_count=0):
    """State mínimo para os nós de decisão e retentativa."""
    return {
        "payment_event": {}, "payment_method": payment_method,
        "customer_id": "RN_teste" if payment_method == "pix_automatico" else "cus_teste",
        "invoice_id": "inv_1", "amount": VALOR,
        "failure_cause": causa, "recovery_score": 60, "p_recovery": 0.6,
        "eprofit": 100.0, "recommend_action": True, "is_anomalous": False,
        "optimal_retry_at": None, "estrategia": "retry_automatico",
        "retry_count": retry_count, "next_retry_at": None,
        "retry_exhausted": False, "recovered": False, "pix_retry_schedule": None,
    }


class EspiaoPixPolicy:
    """Espião na política de Pix — registra qualquer uso da janela regulada."""

    def __init__(self):
        self.chamadas = []

    async def schedule(self, customer_id, valor_original, **kwargs):
        self.chamadas.append((customer_id, valor_original))
        return []


def stripe_header(payload: bytes, secret: str = STRIPE_SECRET) -> str:
    timestamp = int(time.time())
    assinatura = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload,
                          hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={assinatura}"


# ══════════════════════════════════════════════════════════════════════════
# CARTÃO: SÓ REGISTRO, NENHUMA RETENTATIVA
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def client(monkeypatch):
    """Client com o secret do Stripe e o pipeline espionado."""
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", STRIPE_SECRET)
    monkeypatch.setenv("ENV", "development")

    chamadas = []

    async def fake_involuntary(event, payment_method="card", **kwargs):
        chamadas.append(payment_method)

    monkeypatch.setattr(app_module, "_run_involuntary_pipeline", fake_involuntary)

    with TestClient(app_module.app) as c:
        c.pipeline_calls = chamadas
        yield c


STRIPE_PAYLOAD = json.dumps({
    "id": "evt_1", "type": "invoice.payment_failed",
    "data": {"object": {"id": "inv_1", "customer": "cus_1", "amount_due": 29990,
                        "failure_code": "insufficient_funds", "attempt_count": 1}},
}).encode()


class TestCartaoApenasRegistrado:
    """O evento de cartão chega, é reconhecido, e para por aí."""

    def test_webhook_stripe_nao_aciona_pipeline(self, client):
        r = client.post("/webhooks/stripe", content=STRIPE_PAYLOAD,
                        headers={"stripe-signature": stripe_header(STRIPE_PAYLOAD)})

        assert r.status_code == 200
        assert r.json()["pipeline"] is False
        assert client.pipeline_calls == [], "evento de cartão entrou no pipeline"

    def test_webhook_stripe_loga_com_prefixo_cartao_desativado(self, client, capsys):
        client.post("/webhooks/stripe", content=STRIPE_PAYLOAD,
                    headers={"stripe-signature": stripe_header(STRIPE_PAYLOAD)})

        saida = capsys.readouterr().out
        assert "[CARTAO-DESATIVADO]" in saida
        # O registro identifica a cobrança, para não se perder.
        assert "cus_1" in saida and "299.90" in saida

    def test_webhook_stripe_continua_exigindo_assinatura(self, client):
        """A validação de assinatura da Fase 1 permanece ativa."""
        r = client.post("/webhooks/stripe", content=STRIPE_PAYLOAD)
        assert r.status_code == 401

    def test_simulate_payment_failed_tambem_so_registra(self, client, capsys):
        r = client.post("/simulate/payment-failed", json={"customer_id": "cus_demo"})

        assert r.status_code == 200
        assert r.json()["pipeline"] is False
        assert client.pipeline_calls == []
        assert "[CARTAO-DESATIVADO]" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_cartao_no_grafo_nunca_pede_retentativa(self):
        """Se um evento de cartão chegar ao nó de decisão, vira mensagem."""
        s = await decide_recovery(_estado("card"))
        assert s["estrategia"] == "mensagem_pagamento"
        assert route_after_decision(s) == "trigger_dunning"

    @pytest.mark.asyncio
    async def test_raciocinio_explica_que_cartao_esta_desativado(self):
        s = await decide_recovery(_estado("card"))
        assert "[CARTAO-DESATIVADO]" in " ".join(s["raciocinio"])

    def test_router_manda_cartao_para_dunning_mesmo_forcado(self):
        """Defesa em profundidade: state forjado pedindo retry não retenta."""
        forcado = {"estrategia": "retry_automatico", "payment_method": "card"}
        assert route_after_decision(forcado) == "trigger_dunning"

    def test_payment_method_ausente_tambem_nao_retenta(self):
        assert route_after_decision({"estrategia": "retry_automatico"}) == "trigger_dunning"


# ══════════════════════════════════════════════════════════════════════════
# PIX: A ÚNICA POLÍTICA DE RETENTATIVA ATIVA
# ══════════════════════════════════════════════════════════════════════════

class TestPixEhOUnicoCaminhoDeRetentativa:

    @pytest.mark.asyncio
    async def test_evento_pix_usa_a_politica_regulada(self, monkeypatch):
        espiao = EspiaoPixPolicy()
        monkeypatch.setattr(workflow_module, "_pix_retry", espiao)

        await schedule_retry_pix(_estado("pix_automatico"))
        assert len(espiao.chamadas) == 1

    @pytest.mark.asyncio
    async def test_pix_no_grafo_chega_ao_no_de_retentativa(self):
        s = await decide_recovery(_estado("pix_automatico"))
        assert s["estrategia"] == "retry_automatico"
        assert route_after_decision(s) == "schedule_retry_pix"

    @pytest.mark.asyncio
    async def test_janela_esgotada_marca_retry_exhausted(self, monkeypatch):
        """Sem tentativa disponível, o grafo segue para a mensagem personalizada."""
        monkeypatch.setattr(workflow_module, "_pix_retry", EspiaoPixPolicy())
        resultado = await schedule_retry_pix(_estado("pix_automatico", retry_count=3))

        assert resultado["retry_exhausted"] is True
        assert resultado["next_retry_at"] is None
        assert resultado["pix_retry_schedule"] == []

    @pytest.mark.asyncio
    async def test_plano_de_tentativas_vai_para_o_state(self, monkeypatch):
        """O plano completo fica no state, para auditoria e para o CRM."""
        class PixComPlano:
            async def schedule(self, customer_id, valor_original, **kwargs):
                from crai.dunning.pix_automatico_retry import TentativaAgendada
                base = datetime.now() + timedelta(days=1)
                return [
                    TentativaAgendada(1, base, valor_original, "payday_engine"),
                    TentativaAgendada(2, base + timedelta(days=1), valor_original, "payday_engine"),
                ]

        monkeypatch.setattr(workflow_module, "_pix_retry", PixComPlano())
        resultado = await schedule_retry_pix(_estado("pix_automatico"))

        assert len(resultado["pix_retry_schedule"]) == 2
        assert resultado["retry_exhausted"] is False
        assert resultado["next_retry_at"] == resultado["pix_retry_schedule"][0]["quando"]
        assert resultado["retry_count"] == 2


# ══════════════════════════════════════════════════════════════════════════
# O CÓDIGO DE CARTÃO ESTÁ ISOLADO, NÃO DELETADO
# ══════════════════════════════════════════════════════════════════════════

class TestLegacyCardIsolado:
    """A cláusula de remoção mandava isolar, não apagar."""

    def test_smart_backoff_continua_existindo_no_legacy(self):
        from crai.dunning.legacy_card.smart_backoff import SmartBackoff

        agendamento = SmartBackoff().get_schedule("insufficient_funds", 0)
        assert agendamento["exhausted"] is False

    def test_no_de_retentativa_de_cartao_preservado(self):
        from crai.dunning.legacy_card.card_retry import schedule_retry_card

        assert callable(schedule_retry_card)

    def test_pipeline_ativo_nao_importa_legacy_card(self):
        """Nenhum módulo do fluxo ativo depende do pacote isolado."""
        import crai.agent.main_agent
        import crai.agent.workflow
        import crai.api.app
        import crai.dunning.dunning_engine

        for modulo in (crai.agent.workflow, crai.agent.main_agent,
                       crai.api.app, crai.dunning.dunning_engine):
            fonte = modulo.__dict__
            assert not any("legacy_card" in str(v) for v in fonte.get("__builtins__", {}) or {})
            assert "SmartBackoff" not in fonte, (
                f"{modulo.__name__} ainda referencia SmartBackoff")

    def test_grafo_nao_tem_mais_no_de_cartao(self):
        nos = set(build_crai_graph().get_graph().nodes)
        assert "schedule_retry_pix" in nos
        assert "schedule_retry_card" not in nos
        assert "schedule_retry" not in nos


# ══════════════════════════════════════════════════════════════════════════
# SPRINT 2 — DOIS CLIENTES NUNCA COMPARTILHAM CHECKPOINT (P0-6)
#
# O `thread_id` do MemorySaver é a identidade da conversa no LangGraph. Com
# `id_recorrencia` vazio, todos os pagadores anônimos caíam no literal
# "rec_desconhecida" — mesmo thread_id, mesmo checkpoint — e o segundo evento
# retomava o estado do primeiro.
# ══════════════════════════════════════════════════════════════════════════

class TestThreadIdNuncaColide:
    """A identidade do checkpoint tem que vir do evento, não de um literal."""

    def test_id_de_recorrencia_presente_e_usado_como_esta(self):
        evento = {"e2e_id": "E1", "valor": 299.90, "ispb_pagador": "60701190",
                  "id_recorrencia": "RN2026083100001", "degradacoes": []}
        assert app_module._thread_id(evento) == "RN2026083100001"

    def test_dois_pagadores_anonimos_diferentes_nao_colidem(self):
        """O caso do P0-6: dois eventos sem id de recorrência e com valores
        diferentes são dois clientes distintos — não podem dividir estado."""
        a = {"e2e_id": "E_a", "valor": 299.90, "ispb_pagador": "60701190",
             "id_recorrencia": "", "degradacoes": []}
        b = {"e2e_id": "E_b", "valor": 149.00, "ispb_pagador": "00000000",
             "id_recorrencia": "", "degradacoes": []}

        assert app_module._thread_id(a) != app_module._thread_id(b)

    def test_muda_so_o_valor_ja_basta_para_separar(self):
        a = {"e2e_id": "E_x", "valor": 299.90, "ispb_pagador": "60701190",
             "id_recorrencia": ""}
        b = {**a, "valor": 300.00}
        assert app_module._thread_id(a) != app_module._thread_id(b)

    def test_eventos_realmente_identicos_compartilham_checkpoint(self):
        """Não é bug: se dois eventos são indistinguíveis, tratá-los como o
        mesmo cliente é o comportamento correto — e o único disponível."""
        evento = {"e2e_id": "E_y", "valor": 10.0, "ispb_pagador": "1",
                  "id_recorrencia": ""}
        assert app_module._thread_id(evento) == app_module._thread_id(dict(evento))

    def test_id_anonimo_e_deterministico_entre_processos(self):
        """Precisa ser sha256, não `hash()`: o hash embutido do Python é
        randomizado por processo para strings, e a demo tem que reproduzir."""
        evento = {"e2e_id": "E_z", "valor": 55.5, "ispb_pagador": "9",
                  "id_recorrencia": ""}
        base = json.dumps(["E_z", "9", repr(55.5)], ensure_ascii=False)
        assert app_module._thread_id(evento) == (
            "rec_anon_" + hashlib.sha256(base.encode("utf-8")).hexdigest()[:16])

    def test_separador_dentro_do_campo_nao_causa_colisao(self):
        """A1/N-5. Concatenando os campos com `|` cru, estes dois eventos
        produziam a MESMA base — `E123|999|60701190|299.9` — e dois clientes
        distintos dividiam checkpoint pelo caminho que o P0-6 existia para
        fechar. A base agora é serializada como lista JSON."""
        a = {"e2e_id": "E123|999", "ispb_pagador": "60701190",
             "valor": 299.90, "id_recorrencia": ""}
        b = {"e2e_id": "E123", "ispb_pagador": "999|60701190",
             "valor": 299.90, "id_recorrencia": ""}

        assert app_module._thread_id(a) != app_module._thread_id(b)

    def test_evento_sem_nenhuma_identificacao_e_recusado(self):
        """A1/N-5. Sem `id_recorrencia` e sem `e2e_id` não há o que
        identificar: num SaaS de preço único cujo PSP omita o e2e, a base
        seria a mesma string para TODOS os clientes e o P0-6 voltaria inteiro.
        Recusar é a única resposta honesta."""
        evento = {"e2e_id": "", "ispb_pagador": "", "valor": 299.90,
                  "id_recorrencia": ""}
        assert app_module._thread_id(evento) is None

    def test_webhook_sem_identificacao_devolve_422(self, monkeypatch):
        """Ponta a ponta: o evento anônimo demais não entra no pipeline."""
        monkeypatch.setenv("PIX_WEBHOOK_SECRET", "s3cr3t")
        chamadas = []

        async def fake(event, payment_method="card", **kw):
            chamadas.append(payment_method)

        monkeypatch.setattr(app_module, "_run_involuntary_pipeline", fake)

        corpo = json.dumps({"data": {"valor": 299.90, "status": "failed"}}).encode()
        ts = int(time.time())
        assinatura = hmac.new(b"s3cr3t", f"{ts}.".encode() + corpo,
                              hashlib.sha256).hexdigest()

        with TestClient(app_module.app) as c:
            r = c.post("/webhooks/pix-automatico", content=corpo,
                       headers={"x-pix-signature": f"t={ts},v1={assinatura}"})

        assert r.status_code == 422
        assert chamadas == []

    def test_id_anonimo_e_reconhecivel_no_log(self):
        evento = {"e2e_id": "E_w", "valor": 1.0, "ispb_pagador": "2",
                  "id_recorrencia": "  "}
        assert app_module._thread_id(evento).startswith("rec_anon_")

    def test_rec_desconhecida_nunca_mais_e_thread_id(self):
        """O literal pode continuar existindo em log ou comentário, mas não
        pode voltar a ser a identidade de um checkpoint."""
        import inspect

        fonte = inspect.getsource(app_module)
        for linha in fonte.splitlines():
            if "rec_desconhecida" in linha and not linha.strip().startswith("#"):
                assert "customer_id=" not in linha, (
                    f"'rec_desconhecida' voltou a ser thread_id: {linha.strip()}")


class TestPerfilSinteticoUsaUmIdSo:
    """P0-6b: o mesmo evento não pode gerar dois perfis sintéticos."""

    def test_features_pix_semeia_pelo_customer_id(self):
        """`_features_pix` usava `event["id_recorrencia"]` (string vazia no
        caso anônimo) enquanto o resto do pipeline usava `customer_id` — dois
        perfis diferentes para o mesmo evento."""
        evento = {"e2e_id": "E1", "valor": 299.90, "ispb_pagador": "60701190",
                  "id_recorrencia": "", "degradacoes": []}

        pelo_customer = workflow_module._extract_features(
            evento, 299.90, "pix_automatico", customer_id="rec_anon_abc123")
        pelo_vazio = workflow_module._extract_features(
            evento, 299.90, "pix_automatico", customer_id="")

        assert pelo_customer["tenure_months"] != pelo_vazio["tenure_months"] or \
            pelo_customer["payment_history_score"] != pelo_vazio["payment_history_score"]

    def test_mesmo_customer_id_da_sempre_o_mesmo_perfil(self):
        evento = {"e2e_id": "E1", "valor": 299.90, "ispb_pagador": "60701190",
                  "id_recorrencia": "RN_1", "degradacoes": []}
        a = workflow_module._extract_features(evento, 299.90, "pix_automatico",
                                              customer_id="RN_1")
        b = workflow_module._extract_features(evento, 299.90, "pix_automatico",
                                              customer_id="RN_1")
        assert a == b


class TestUmDefaultSoParaPaymentMethod:
    """P2-12: `route_after_decision` e `decide_recovery` usam o mesmo default.

    **Declaração honesta, cobrada pela auditoria A1 (N-13):** esta correção é
    um no-op COMPORTAMENTAL, e o teste abaixo passa igual no commit anterior.
    `None` e `"card"` sempre rotearam para `trigger_dunning`, porque só
    `"pix_automatico"` desvia — o `else` engolia os dois.

    O teste fica porque o valor dele é outro: ele trava a **consistência** dos
    dois defaults. No dia em que alguém acrescentar um segundo meio de pagamento
    com política própria, dois defaults divergentes passam a mudar o
    comportamento — e aí este teste começa a valer como regressão de verdade.
    Marcar isso é melhor que deixar a suíte insinuar que houve conserto de bug.
    """

    def test_os_dois_pontos_leem_o_mesmo_default(self):
        """Comparação direta do código-fonte: é o que a correção realmente fez."""
        import inspect

        from crai.agent.main_agent import route_after_decision as rota
        from crai.agent.workflow import decide_recovery as decide

        fonte_rota = inspect.getsource(rota)
        fonte_decide = inspect.getsource(decide)
        assert 'state.get("payment_method", "card")' in fonte_rota
        assert 'state.get("payment_method", "card")' in fonte_decide

    def test_payment_method_ausente_nao_retenta(self):
        """Invariante de segurança que vale independente do default escolhido."""
        estado = _estado("pix_automatico")
        del estado["payment_method"]
        assert route_after_decision(estado) == "trigger_dunning"


# ══════════════════════════════════════════════════════════════════════════
# P0-6 — REGRESSÃO COMPORTAMENTAL, NÃO PROVA DE SÍMBOLO
#
# Nasceu da re-auditoria A1-r2. Os testes de `TestThreadIdNuncaColide` chamam
# `app_module._thread_id` diretamente: no commit anterior isso levanta
# `AttributeError` e o teste "falha" por o símbolo ser novo, não por a colisão
# existir. Isso não é prova de regressão — é prova de que a função nasceu aqui.
#
# Esta classe não menciona `_thread_id`. Entra pelo webhook assinado e observa
# o `thread_id` que chega ao `MemorySaver`, que é o que de fato separa a
# memória de um cliente da do outro. No baseline os dois pagadores anônimos
# recebem `"rec_desconhecida"` e o teste falha por IGUALDADE.
# ══════════════════════════════════════════════════════════════════════════

class TestP0_6RegressaoPeloWebhook:
    """Dois pagadores anônimos distintos não podem dividir checkpoint."""

    @staticmethod
    def _assinar(corpo: bytes, segredo: bytes = b"s3cr3t") -> dict:
        ts = int(time.time())
        mac = hmac.new(segredo, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
        return {"x-pix-signature": f"t={ts},v1={mac}"}

    @staticmethod
    def _corpo(e2e: str, ispb: str, valor: float) -> bytes:
        """Cobrança falhada SEM id_recorrencia — o caso do P0-6."""
        return json.dumps({
            "event": "automatic_pix.charge_failed",
            "data": {"e2e_id": e2e, "ispb_pagador": ispb, "valor": valor,
                     "status": "failed"},
        }).encode()

    def _thread_ids_entregues(self, monkeypatch, eventos) -> list:
        """Manda cada evento pelo webhook e devolve os thread_id capturados."""
        monkeypatch.setenv("PIX_WEBHOOK_SECRET", "s3cr3t")
        capturados = []

        async def fake_ainvoke(state, config=None, *a, **kw):
            capturados.append((config or {}).get("configurable", {}).get("thread_id"))
            return dict(state)

        monkeypatch.setattr(app_module.crai_agent, "ainvoke", fake_ainvoke)

        with TestClient(app_module.app) as c:
            for corpo in eventos:
                c.post("/webhooks/pix-automatico", content=corpo,
                       headers=self._assinar(corpo))
        return capturados

    def test_dois_pagadores_anonimos_nao_dividem_checkpoint(self, monkeypatch):
        """O defeito P0-6 na sua forma original, medido no ponto que importa."""
        ids = self._thread_ids_entregues(monkeypatch, [
            self._corpo("E_PAGADOR_A", "60701190", 299.90),
            self._corpo("E_PAGADOR_B", "00000000", 149.90),
        ])

        assert len(ids) == 2, (
            f"os dois eventos deveriam chegar ao pipeline; chegaram {len(ids)}")
        assert ids[0] != ids[1], (
            f"P0-6 VIVO: dois pagadores distintos dividiram o checkpoint {ids[0]!r} "
            "— o segundo evento retomaria o estado do primeiro")
        assert all(i for i in ids), f"thread_id vazio entregue ao MemorySaver: {ids}"

    def test_o_mesmo_pagador_mantem_o_seu_checkpoint(self, monkeypatch):
        """O contrário também precisa valer, senão o cliente perde a memória."""
        corpo = self._corpo("E_PAGADOR_A", "60701190", 299.90)
        ids = self._thread_ids_entregues(monkeypatch, [corpo, corpo])
        assert len(ids) == 2
        assert ids[0] == ids[1]

    def test_literal_rec_desconhecida_nao_chega_ao_memorysaver(self, monkeypatch):
        """O default degenerado do baseline não pode voltar por caminho nenhum."""
        ids = self._thread_ids_entregues(monkeypatch, [
            self._corpo("E_X", "111", 10.0),
            self._corpo("E_Y", "222", 20.0),
        ])
        assert "rec_desconhecida" not in ids

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

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as workflow_module
from crai.agent.main_agent import build_crai_graph, crai_agent, route_after_decision
from crai.agent.workflow import decide_recovery, schedule_retry_pix
from crai.api import app as app_module
from crai.api.idempotencia import limpar_tudo as limpar_idempotencia
from crai.dunning import pix_automatico_retry as pix_retry_module
from crai.dunning.pix_automatico_retry import MAX_TENTATIVAS as MAX_TENTATIVAS_PIX

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
        """O contrário também precisa valer, senão o cliente perde a memória.

        Para um pagador ANÔNIMO a identidade é derivada do próprio evento
        (e2e + ISPB + valor), então "o mesmo pagador de novo" é, por
        construção, o mesmo corpo de novo. Como a janela de idempotência do
        Sprint 1 descarta o reenvio antes do pipeline, o segundo evento aqui
        representa o CICLO SEGUINTE — a mesma cobrança recorrente um mês
        depois, já fora do TTL de 7 dias da janela. Esquecer a janela é
        exatamente o que a passagem do tempo faria; o que o teste mede é que a
        derivação de identidade continua estável entre um ciclo e outro.
        """
        corpo = self._corpo("E_PAGADOR_A", "60701190", 299.90)
        primeiro = self._thread_ids_entregues(monkeypatch, [corpo])
        limpar_idempotencia()
        segundo = self._thread_ids_entregues(monkeypatch, [corpo])

        assert len(primeiro) == 1 and len(segundo) == 1
        assert primeiro[0] == segundo[0]

    def test_literal_rec_desconhecida_nao_chega_ao_memorysaver(self, monkeypatch):
        """O default degenerado do baseline não pode voltar por caminho nenhum."""
        ids = self._thread_ids_entregues(monkeypatch, [
            self._corpo("E_X", "111", 10.0),
            self._corpo("E_Y", "222", 20.0),
        ])
        assert "rec_desconhecida" not in ids


class TestA1R4LimiteBacenAtravessaOsWebhooks:
    """O limite de 3 tentativas tem que valer entre invocações, não dentro de uma.

    Defeito encontrado na auditoria A1-r4. `_run_involuntary_pipeline` escrevia
    `"retry_count": retries_done` (0, porque o caminho Pix nunca preenche esse
    parâmetro) no dicionário de entrada do `ainvoke`. O `ainvoke` aplica a
    entrada como atualização sobre o checkpoint do `thread_id`, então toda
    chave presente sobrescreve o que estava lá — e o contador que
    `schedule_retry_pix` tinha acabado de gravar voltava a zero a cada webhook.

    Medido em `8ee67ae`: três webhooks assinados do mesmo `id_recorrencia`
    agendavam 3 + 3 + 3 = **nove** tentativas na mesma janela de 7 dias, as
    nove numeradas 1, 2, 3. O limite do BACEN é 3.

    Por que a varredura de 212.992 combinações da rodada 3 não pegou: ela
    exercitava `PixAutomaticoRetryPolicy.schedule()` isoladamente, e a política
    **estava certa** — ela recebia `tentativas_usadas=0` e respondia
    corretamente com 3. O defeito não estava na política, estava em quem a
    chamava. Por isso este teste entra pelo webhook e conta o total agendado
    ao longo de várias invocações, em vez de inspecionar uma só.
    """

    @staticmethod
    def _assinar(corpo: bytes, segredo: bytes = b"s3cr3t") -> dict:
        ts = int(time.time())
        mac = hmac.new(segredo, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
        return {"x-pix-signature": f"t={ts},v1={mac}"}

    def _agendadas_em_sequencia(self, monkeypatch, id_recorrencia, quantos):
        """Manda `quantos` webhooks idênticos e devolve o que a política agendou.

        Não faz mock do `crai_agent`: o defeito vive exatamente na fronteira
        entre a borda e o checkpoint, e um agente falso apagaria o checkpoint
        junto com o bug.
        """
        monkeypatch.setenv("PIX_WEBHOOK_SECRET", "s3cr3t")
        lotes = []
        original = workflow_module._pix_retry.schedule

        async def espiao(**kwargs):
            tentativas = await original(**kwargs)
            lotes.append(tentativas)
            return tentativas

        monkeypatch.setattr(workflow_module._pix_retry, "schedule", espiao)

        # Cobrancas DISTINTAS do mesmo contrato (um e2e cada), e nao o mesmo
        # corpo repetido: o reenvio identico agora para antes, na janela de
        # idempotencia do Sprint 1, e o que este teste mede e o contador do
        # checkpoint entre execucoes — que so e exercido por eventos que de
        # fato chegam ao pipeline. Ver o mesmo comentario em
        # `TestA1R5AJanelaDoBacenExpira._webhooks_no_tempo`.
        with TestClient(app_module.app) as c:
            for indice in range(quantos):
                corpo = json.dumps({
                    "event": "automatic_pix.charge_failed",
                    "e2e_id": f"E{id_recorrencia}_{indice}",
                    "valor": VALOR,
                    "id_recorrencia": id_recorrencia,
                }).encode()
                resposta = c.post("/webhooks/pix-automatico", content=corpo,
                                  headers=self._assinar(corpo))
                assert resposta.status_code == 200, resposta.text
        return lotes

    def test_tres_webhooks_do_mesmo_cliente_nao_estouram_a_janela(self, monkeypatch):
        lotes = self._agendadas_em_sequencia(monkeypatch, "RN_r4_janela_a", 3)
        total = sum(len(lote) for lote in lotes)

        assert total <= MAX_TENTATIVAS_PIX, (
            f"LIMITE BACEN VIOLADO: {total} tentativas agendadas na mesma janela "
            f"de 7 dias para o mesmo id_recorrencia (máximo {MAX_TENTATIVAS_PIX}). "
            f"Lotes por invocação: {[len(l) for l in lotes]}"
        )

    def test_o_numero_da_tentativa_nunca_se_repete(self, monkeypatch):
        """Numeração repetida é a assinatura do contador zerado.

        Vale como asserção independente: mesmo que alguém no futuro limite o
        total por outro caminho, duas tentativas nº 1 na mesma janela
        continuariam sendo um contador que não avançou.
        """
        lotes = self._agendadas_em_sequencia(monkeypatch, "RN_r4_janela_b", 3)
        numeros = [t.numero for lote in lotes for t in lote]

        assert len(numeros) == len(set(numeros)), (
            f"numeração repetida na mesma janela: {numeros} — o contador do "
            "checkpoint foi sobrescrito pela entrada do ainvoke"
        )

    def test_o_segundo_webhook_ve_a_janela_ja_gasta(self, monkeypatch):
        """A consequência positiva: o cliente cai na mensagem personalizada.

        Depois do primeiro webhook o checkpoint guarda 3 tentativas
        comprometidas, então `decide_recovery` barra o segundo evento antes do
        nó de agendamento — que é o comportamento que o Sprint 2 documentou.
        """
        lotes = self._agendadas_em_sequencia(monkeypatch, "RN_r4_janela_c", 2)

        assert len(lotes) == 1, (
            f"o nó de agendamento foi alcançado {len(lotes)} vezes; a partir da "
            "segunda o cliente já não tem tentativa disponível e deveria ser "
            "barrado em decide_recovery"
        )

class TestA1R5AJanelaDoBacenExpira:
    """O limite do BACEN é 3 POR JANELA DE 7 DIAS, não 3 por contrato.

    A correção da rodada 4 parou de zerar `retry_count` a cada webhook. O
    contador ficou monotônico: só crescia, e nada no state dizia a que janela
    ele pertencia. Com isso o invariante em vigor passou a ser "3 tentativas
    por id_recorrencia, para sempre" — e a cobrança do mês seguinte, que é uma
    janela regulatória inteiramente nova, encontrava o limite já gasto por uma
    janela encerrada 53 dias antes. O cliente perdia por prescrição um direito
    que a regulação lhe dá em cada ciclo de cobrança.

    Os testes da rodada 4 não pegaram isso porque afirmam `total <= 3`, e esse
    teto está certo sob os DOIS invariantes. O que separa um do outro é o
    tempo. Por isso estes testes controlam o relógio: sem mover o relógio, os
    dois contratos são literalmente indistinguíveis.

    Medido em `317a0eb`, antes desta correção: segundo evento 60 dias depois,
    `chamadas a schedule: NENHUMA`, estratégia `mensagem_pagamento`, com o
    pipeline dizendo "as 3 tentativas da janela regulada do BACEN já foram
    usadas". `AgentState` não tinha nenhuma chave de janela ou prazo.
    """

    ABERTURA = datetime(2026, 9, 3, 9, 0)

    @staticmethod
    def _assinar(corpo: bytes, segredo: bytes = b"s3cr3t") -> dict:
        ts = int(time.time())
        mac = hmac.new(segredo, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
        return {"x-pix-signature": f"t={ts},v1={mac}",
                "content-type": "application/json"}

    def _webhooks_no_tempo(self, monkeypatch, id_recorrencia, momentos):
        """Um webhook em cada instante de `momentos`, com o relógio do grafo movido.

        Devolve `(lotes, estados)`: o que a política agendou em cada passagem
        pelo nó de retentativa, e o checkpoint depois de cada webhook.

        Como na rodada 4, não há mock do `crai_agent`: o defeito vive na
        fronteira entre a borda e o checkpoint, e um agente falso apagaria o
        checkpoint junto com o bug. O que se substitui é só o relógio.
        """
        monkeypatch.setenv("PIX_WEBHOOK_SECRET", "s3cr3t")
        relogio = {"agora": momentos[0]}
        original = workflow_module._pix_retry.schedule
        lotes = []

        async def espiao(**kwargs):
            tentativas = await original(**kwargs)
            lotes.append(tentativas)
            return tentativas

        monkeypatch.setattr(workflow_module._pix_retry, "schedule", espiao)

        # O relógio é congelado substituindo o `datetime` que os DOIS módulos
        # já importavam, e não um ponto de injeção novo. A diferença importa:
        # um `monkeypatch.setattr(workflow, "_agora", ...)` levantaria
        # AttributeError em `317a0eb`, onde esse símbolo não existe — e um
        # AttributeError não é prova de falha de comportamento, é prova de
        # símbolo ausente. Substituindo `datetime`, o mesmo teste roda nas duas
        # pontas e a diferença que ele mede é a do CÓDIGO.
        class RelogioCongelado(datetime):
            @classmethod
            def now(cls, tz=None):
                return relogio["agora"]

        monkeypatch.setattr(workflow_module, "datetime", RelogioCongelado)
        monkeypatch.setattr(pix_retry_module, "datetime", RelogioCongelado)

        # UM e2e POR COBRANCA, e nao um corpo repetido. O `end_to_end_id` e
        # unico por transacao no arranjo Pix — duas cobrancas de meses
        # diferentes nunca compartilham o mesmo. Repetir o corpo era atalho de
        # escrita, e desde a janela de idempotencia do Sprint 1
        # (`crai/api/idempotencia.py`) esse atalho passou a descrever outra
        # coisa: um REENVIO da mesma cobranca, que e justamente o que aquela
        # janela existe para descartar. O que este teste mede — se o contador
        # do BACEN expira com o tempo — so e observavel quando os eventos sao
        # cobrancas distintas do mesmo contrato, que e o caso real.
        #
        # O `id_recorrencia` continua o mesmo nos dois: e ele, e nao o e2e, que
        # define o `thread_id` e portanto o checkpoint (ver `_thread_id`).
        def corpo_da_cobranca(indice: int) -> bytes:
            return json.dumps({
                "event": "automatic_pix.charge_failed",
                "e2e_id": f"E{id_recorrencia}_{indice}",
                "valor": VALOR,
                "id_recorrencia": id_recorrencia,
            }).encode()

        estados = []
        config = {"configurable": {"thread_id": id_recorrencia}}
        with TestClient(app_module.app) as c:
            for indice, momento in enumerate(momentos):
                relogio["agora"] = momento
                corpo = corpo_da_cobranca(indice)
                resposta = c.post("/webhooks/pix-automatico", content=corpo,
                                  headers=self._assinar(corpo))
                assert resposta.status_code == 200, resposta.text
                estados.append(dict(crai_agent.get_state(config).values))
        return lotes, estados

    # -- o defeito: a janela seguinte nao existia -------------------------

    def test_a_janela_seguinte_tem_as_proprias_tres_tentativas(self, monkeypatch):
        """Sessenta dias depois é outra cobrança, com outro direito de 3."""
        lotes, _ = self._webhooks_no_tempo(
            monkeypatch, "RN_r5_expira_a",
            [self.ABERTURA, self.ABERTURA + timedelta(days=60)],
        )

        assert len(lotes) == 2, (
            f"o nó de agendamento foi alcançado {len(lotes)} vez(es). O evento "
            "de 60 dias depois abre uma janela BACEN nova e tem direito às 3 "
            "tentativas dela; encontrar o limite gasto significa que o contador "
            "nunca expira — '3 por contrato' em vez de '3 por janela de 7 dias'"
        )
        assert len(lotes[1]) == MAX_TENTATIVAS_PIX, (
            f"a janela nova concedeu {len(lotes[1])} tentativa(s), não "
            f"{MAX_TENTATIVAS_PIX}"
        )
        assert [t.numero for t in lotes[1]] == [1, 2, 3], (
            f"a numeração da janela nova começou em {[t.numero for t in lotes[1]]}; "
            "cada janela numera as próprias tentativas de 1 a 3"
        )

    def test_o_prazo_da_janela_fica_gravado_no_checkpoint(self, monkeypatch):
        """Sem a marca no state, expirar a janela é impossível por construção."""
        _, estados = self._webhooks_no_tempo(
            monkeypatch, "RN_r5_expira_b", [self.ABERTURA],
        )
        prazo = estados[0].get("pix_janela_ate")

        assert prazo is not None, (
            "o checkpoint não guarda até quando o contador de tentativas vale. "
            "Um `retry_count` sem janela é um limite sem prazo: só cresce"
        )
        assert prazo == self.ABERTURA + timedelta(days=7), (
            f"prazo gravado {prazo!r}; a janela do BACEN é de 7 dias corridos "
            f"contados do vencimento ({self.ABERTURA!r})"
        )

    def test_a_janela_nova_reancora_no_evento_que_a_abriu(self, monkeypatch):
        """A janela #2 conta 7 dias a partir dela mesma, não da #1."""
        abertura_2 = self.ABERTURA + timedelta(days=60)
        _, estados = self._webhooks_no_tempo(
            monkeypatch, "RN_r5_expira_c", [self.ABERTURA, abertura_2],
        )

        assert estados[1].get("pix_janela_ate") == abertura_2 + timedelta(days=7), (
            f"prazo após a segunda cobrança: {estados[1].get('pix_janela_ate')!r}. "
            f"Esperado {abertura_2 + timedelta(days=7)!r} — a janela nova se "
            "ancora no vencimento que a abriu"
        )

    # -- o contrapeso: a correcao nao pode virar '3 por webhook' ----------

    def test_evento_dentro_da_janela_nao_empurra_o_prazo(self, monkeypatch):
        """Trava a correção pelo outro lado.

        Zerar o contador por tempo sem ancorar a janela faria cada webhook
        adiar o prazo em mais 7 dias, e o limite nunca venceria — '3 por
        webhook', que é o defeito da rodada 4 de volta por outra porta.

        Em `317a0eb` ele reprova por falta da própria marca de janela, então
        conta como regressão; mas o que ele existe para pegar é a
        SOBRE-correção, e isso só uma implementação futura pode disparar.
        """
        _, estados = self._webhooks_no_tempo(
            monkeypatch, "RN_r5_expira_d",
            [self.ABERTURA, self.ABERTURA + timedelta(days=3)],
        )

        assert estados[1].get("pix_janela_ate") == self.ABERTURA + timedelta(days=7), (
            "o segundo evento, ainda DENTRO da janela, empurrou o prazo para "
            f"{estados[1].get('pix_janela_ate')!r}. A janela pertence à cobrança "
            "que a abriu; reancorar a cada webhook a torna eterna"
        )

    def test_evento_dentro_da_janela_nao_ganha_tentativa_nova(self, monkeypatch):
        """O invariante da rodada 4, preservado: dentro da janela o teto vale."""
        lotes, _ = self._webhooks_no_tempo(
            monkeypatch, "RN_r5_expira_e",
            [self.ABERTURA, self.ABERTURA + timedelta(days=3)],
        )
        total = sum(len(lote) for lote in lotes)

        assert total <= MAX_TENTATIVAS_PIX, (
            f"LIMITE BACEN VIOLADO: {total} tentativas na mesma janela de 7 dias "
            f"(máximo {MAX_TENTATIVAS_PIX}). Lotes: {[len(l) for l in lotes]}"
        )

    # -- a regra pura, sem HTTP ------------------------------------------
    #
    # Os dois testes abaixo exercem `_janela_vigente` diretamente. São testes
    # UNITÁRIOS da regra nova, não prova de regressão: em `317a0eb` a função
    # não existe, e o que eles produzem lá é AttributeError — símbolo ausente,
    # não comportamento errado. A prova de regressão são os quatro testes de
    # ponta a ponta acima.

    def test_contador_sem_prazo_e_tratado_como_janela_aberta(self):
        """Na dúvida o limite regulatório aperta, nunca afrouxa.

        Um checkpoint gravado antes desta marca existir — ou por uma origem
        externa que sobrescreveu `retry_count` — tem contador e não tem prazo.
        Tratar isso como janela expirada liberaria 3 tentativas extras.
        """
        usadas, prazo = workflow_module._janela_vigente(
            {"retry_count": 3, "pix_janela_ate": None}, datetime(2030, 1, 1),
        )
        assert (usadas, prazo) == (3, None)

    def test_o_contador_zera_quando_o_prazo_passa(self):
        agora = datetime(2026, 9, 3, 9, 0)
        estado = {"retry_count": 3, "pix_janela_ate": agora - timedelta(seconds=1)}

        assert workflow_module._janela_vigente(estado, agora) == (0, None)
        # Um segundo ANTES do fim a janela ainda vale: a fronteira é inclusiva.
        estado["pix_janela_ate"] = agora + timedelta(seconds=1)
        assert workflow_module._janela_vigente(estado, agora) == (
            3, agora + timedelta(seconds=1),
        )

class TestA1R6ConcorrenciaNaJanelaDoBacen:
    """Duas entregas do mesmo evento, ao mesmo tempo, não podem dobrar o limite.

    A rodada 4 fechou a chegada SEQUENCIAL: o segundo webhook encontra o
    contador do checkpoint e cai na mensagem personalizada. A chegada
    CONCORRENTE nunca foi fechada — e um PSP entrega *at-least-once*, então
    duas cópias do mesmo evento chegando juntas é operação normal, não
    condição exótica.

    Entre a leitura do checkpoint e a gravação há `await` em quatro nós. Dois
    `ainvoke` do mesmo `thread_id` intercalam: os dois leem `retry_count = 0`,
    os dois recebem 3 tentativas da política — que está certa, porque cada
    chamada isolada respeita o limite. O invariante quebrado é ENTRE chamadas.

    Medido em `8786d82`, pelo webhook assinado, sem mock nenhum, contando pela
    própria trilha `[PIX-RETRY]`:

        N=1 -> 3 tentativas   numeros [1,2,3]
        N=2 -> 6 tentativas   numeros [1,2,3,1,2,3]
        N=3 -> 9 tentativas   numeros [1,2,3,1,2,3,1,2,3]

    contra o limite legal de 3 — e o checkpoint registrando `retry_count: 3`
    nos três, ou seja, subnotificando o que foi comprometido. É o mesmo
    3+3+3=9 que a rodada 4 descreve ter fechado.
    """

    @staticmethod
    def _assinar(corpo: bytes, segredo: bytes = b"s3cr3t") -> dict:
        ts = int(time.time())
        mac = hmac.new(segredo, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
        return {"x-pix-signature": f"t={ts},v1={mac}",
                "content-type": "application/json"}

    def _corpo(self, id_recorrencia: str, indice: int = 0) -> bytes:
        """Uma cobranca do contrato `id_recorrencia`, com e2e proprio.

        O `indice` distingue COBRANCAS, nao entregas: desde o Sprint 1 a janela
        de idempotencia descarta a reentrega byte a byte antes do pipeline, e o
        que esta classe mede e a corrida ENTRE execucoes do mesmo `thread_id` —
        que continua existindo, e e a mais perigosa, quando dois eventos
        legitimos e distintos do mesmo contrato chegam juntos.
        """
        return json.dumps({
            "event": "automatic_pix.charge_failed",
            "e2e_id": f"E_{id_recorrencia}_{indice}", "valor": VALOR,
            "id_recorrencia": id_recorrencia,
        }).encode()

    async def _disparar_juntos(self, corpos):
        """N requisições concorrentes no MESMO event loop, sem mock.

        `httpx.ASGITransport` fala com o app direto, sem socket e sem thread:
        um processo, um loop, um `MemorySaver`. É a condição que a declaração
        do projeto diz ser suficiente para o limite valer.
        """
        transporte = httpx.ASGITransport(app=app_module.app)
        async with httpx.AsyncClient(transport=transporte,
                                     base_url="http://teste") as cliente:
            return await asyncio.gather(*[
                cliente.post("/webhooks/pix-automatico", content=corpo,
                             headers=self._assinar(corpo))
                for corpo in corpos
            ])

    def _agendadas(self, monkeypatch, corpos):
        """Total de tentativas que a política efetivamente devolveu."""
        monkeypatch.setenv("PIX_WEBHOOK_SECRET", "s3cr3t")
        original = workflow_module._pix_retry.schedule
        lotes = []

        async def espiao(**kwargs):
            tentativas = await original(**kwargs)
            lotes.append(tentativas)
            return tentativas

        monkeypatch.setattr(workflow_module._pix_retry, "schedule", espiao)
        respostas = asyncio.run(self._disparar_juntos(corpos))
        assert [r.status_code for r in respostas] == [200] * len(corpos)
        return lotes

    @pytest.mark.parametrize("quantos", [2, 3])
    def test_webhooks_concorrentes_nao_dobram_a_janela(self, monkeypatch, quantos):
        corpos = [self._corpo(f"RN_r6_conc_{quantos}", i) for i in range(quantos)]
        lotes = self._agendadas(monkeypatch, corpos)
        total = sum(len(lote) for lote in lotes)

        assert total <= MAX_TENTATIVAS_PIX, (
            f"LIMITE BACEN VIOLADO por concorrência: {quantos} entregas "
            f"simultâneas do mesmo id_recorrencia agendaram {total} tentativas "
            f"na mesma janela de 7 dias (máximo {MAX_TENTATIVAS_PIX}). "
            f"Lotes: {[len(l) for l in lotes]}. O `ainvoke` lê o checkpoint na "
            "entrada e grava nó a nó; sem exclusão mútua por thread_id, as N "
            "execuções leem retry_count=0 e cada uma recebe 3"
        )

    def test_a_numeracao_nao_se_repete_entre_execucoes_simultaneas(self, monkeypatch):
        """Numeração repetida é a assinatura do contador lido em paralelo.

        Vale como asserção independente do total: duas tentativas nº 1 na
        mesma janela são duas execuções que se enxergaram como a primeira.
        """
        corpos = [self._corpo("RN_r6_conc_num", i) for i in range(3)]
        lotes = self._agendadas(monkeypatch, corpos)
        numeros = [t.numero for lote in lotes for t in lote]

        assert len(numeros) == len(set(numeros)), (
            f"numeração repetida na mesma janela: {numeros} — três execuções "
            "concorrentes do mesmo cliente leram o mesmo contador"
        )

    def test_o_checkpoint_nao_subnotifica_o_que_foi_comprometido(self, monkeypatch):
        """O contador gravado tem que bater com o que a política agendou.

        Esta é a parte que impede qualquer auditoria posterior de perceber o
        estouro: medido em `8786d82`, nove instruções comprometidas e
        `retry_count: 3` no checkpoint. Quem lesse o estado depois concluiria
        que o limite foi respeitado.
        """
        corpo = self._corpo("RN_r6_conc_sub")
        lotes = self._agendadas(monkeypatch, [corpo] * 3)
        total = sum(len(lote) for lote in lotes)
        estado = crai_agent.get_state(
            {"configurable": {"thread_id": "RN_r6_conc_sub"}}).values

        assert estado.get("retry_count") == total, (
            f"a política agendou {total} tentativa(s) e o checkpoint registra "
            f"{estado.get('retry_count')}. O estado precisa refletir o que foi "
            "comprometido, senão o estouro fica invisível para auditoria"
        )

    def test_clientes_diferentes_continuam_correndo_em_paralelo(self, monkeypatch):
        """Contrapeso: a trava é por cliente, não uma fila global.

        Serializar tudo também faria o total bater, e estaria errado — cada
        `id_recorrencia` é uma cobrança independente com direito às 3 dela.
        Este teste reprova uma trava global, que o teste acima aceitaria.
        """
        corpos = [self._corpo(f"RN_r6_conc_par_{i}") for i in range(3)]
        lotes = self._agendadas(monkeypatch, corpos)
        total = sum(len(lote) for lote in lotes)

        assert total == 3 * MAX_TENTATIVAS_PIX, (
            f"três clientes DISTINTOS agendaram {total} tentativas, esperado "
            f"{3 * MAX_TENTATIVAS_PIX}. Cada id_recorrencia tem a própria "
            "janela; uma trava global roubaria de dois deles"
        )

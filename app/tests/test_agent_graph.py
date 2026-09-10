"""Testes do Módulo 5 — nó de decisão (ReAct) e motor de dunning.

Cobrem o roteamento do grafo e a política sem escalonamento humano:
    retentável        -> retry_automatico
    não retentável    -> mensagem_pagamento (Pix Automático → boleto)
    retry já tentado  -> mensagem_pagamento
    nenhuma decisão aciona um humano

Uso:
    pytest tests/test_agent_graph.py -v
"""
import pytest

from crai.agent.workflow import decide_recovery
from crai.agent.main_agent import route_after_decision
from crai.dunning.dunning_engine import DunningEngine


def _estado(causa, score=60, retry_count=0, anomala=False, payment_method="card"):
    return {
        "failure_cause": causa, "recovery_score": score, "eprofit": 100.0,
        "is_anomalous": anomala, "retry_count": retry_count, "amount": 200.0,
        "customer_id": "cus_test", "p_recovery": score / 100,
        "payment_method": payment_method,
    }


@pytest.mark.asyncio
async def test_causa_retentavel_vira_retry():
    """Retentativa automática só existe para Pix Automático (Fase 3)."""
    for causa in ("insufficient_funds", "processing_error"):
        s = await decide_recovery(_estado(causa, payment_method="pix_automatico"))
        assert s["estrategia"] == "retry_automatico", causa
        assert route_after_decision(s) == "schedule_retry_pix"


@pytest.mark.asyncio
async def test_cartao_nunca_vira_retry_automatico():
    """A recobrança automática de cartão saiu do pipeline ativo na Fase 3."""
    for causa in ("insufficient_funds", "processing_error"):
        s = await decide_recovery(_estado(causa, payment_method="card"))
        assert s["estrategia"] == "mensagem_pagamento", causa
        assert route_after_decision(s) == "trigger_dunning"


@pytest.mark.asyncio
async def test_causa_nao_retentavel_vira_mensagem():
    for causa in ("expired_card", "card_declined", "do_not_honor", "generic_decline"):
        s = await decide_recovery(_estado(causa))
        assert s["estrategia"] == "mensagem_pagamento", causa
        assert route_after_decision(s) == "trigger_dunning"


@pytest.mark.asyncio
async def test_retry_ja_tentado_nao_reinsiste():
    # insufficient_funds e retentável, mas esgotada a janela do BACEN
    # (3 tentativas), contata o cliente.
    s = await decide_recovery(_estado("insufficient_funds", retry_count=3,
                                      payment_method="pix_automatico"))
    assert s["estrategia"] == "mensagem_pagamento"


@pytest.mark.asyncio
async def test_nenhuma_decisao_aciona_humano():
    # Regra de produto: o involuntário nunca escala para humano.
    for causa in ("insufficient_funds", "expired_card", "card_declined",
                  "do_not_honor", "processing_error", "generic_decline"):
        s = await decide_recovery(_estado(causa, retry_count=2))
        assert route_after_decision(s) in ("schedule_retry_pix", "trigger_dunning")
        assert "humano" not in " ".join(s["raciocinio"]).lower()


@pytest.mark.asyncio
async def test_raciocinio_em_portugues():
    s = await decide_recovery(_estado("expired_card"))
    texto = " ".join(s["raciocinio"])
    assert "Observação" in texto and "Pensamento" in texto and "Decisão" in texto


@pytest.mark.asyncio
async def test_pix_automatico_como_fallback_antes_do_boleto():
    eng = DunningEngine()
    # Cartão expirado: Pix Automático contorna o cartão.
    r = await eng.run_campaign("cus_x", "expired_card", 0.8, 149.0)
    assert r["payment_method"] == "pix_automatico"
    # Erro técnico do gateway: cai para boleto.
    r = await eng.run_campaign("cus_y", "processing_error", 0.9, 99.0)
    assert r["payment_method"] == "boleto"


@pytest.mark.asyncio
async def test_dunning_gera_mensagem_com_link():
    eng = DunningEngine()
    r = await eng.run_campaign("cus_z", "card_declined", 0.5, 300.0)
    assert r["sent"] is True
    assert "http" in r["message"]      # link de pagamento presente
    assert r["message"]                # mensagem não vazia (LLM ou fallback)

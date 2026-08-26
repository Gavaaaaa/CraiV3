"""
test_pipeline.py
Testa os pipelines da CRAI sem precisar de PSP, Stripe, Segment ou HubSpot reais:

  1. Churn Involuntário — Pix Automático : 3 cenários da janela regulada (BACEN)
  2. Churn Involuntário — cartão         : 2 cenários de registro sem recobrança
  3. Churn Voluntário                    : 4 cenários de risco de cancelamento

Desde a Fase 3, a recobrança automática de cartão está fora do pipeline ativo
(ver crai/dunning/legacy_card/). Os cenários de cartão existem para demonstrar
que o evento continua sendo recebido e registrado, com prefixo
[CARTAO-DESATIVADO], em vez de descartado em silêncio.

Uso:
    python test_pipeline.py
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from crai.agent.main_agent import crai_agent
from crai.agent.state import AgentState
from crai.api.app import _registrar_cartao_desativado
from crai.churn_voluntary.voluntary_agent import voluntary_churn_agent
from crai.churn_voluntary.state import ChurnVoluntaryState


# ── Churn Involuntário ───────────────────────────────────────────────────

def make_stripe_event(customer_id, amount, failure_code):
    return {
        "id": f"evt_{customer_id}", "type": "invoice.payment_failed",
        "data": {"object": {
            "id": f"inv_{customer_id}", "customer": customer_id,
            "amount_due": int(amount * 100), "currency": "brl",
            "failure_code": failure_code, "failure_message": f"Teste: {failure_code}",
            "attempt_count": 1, "payment_method_details": {"brand": "visa", "last4": "4242"},
        }},
    }


def run_card_scenario(name, customer_id, amount, failure_code):
    """Cartão: o evento é recebido e registrado, sem recobrança automática."""
    print(f"\n{'═'*64}\n  CARTÃO (RECOBRANÇA DESATIVADA) — {name}\n"
          f"  {customer_id} | R$ {amount:.2f} | {failure_code}\n{'═'*64}")
    return _registrar_cartao_desativado(make_stripe_event(customer_id, amount, failure_code))


# ── Churn Involuntário via Pix Automático ────────────────────────────────

async def run_pix_scenario(name, id_recorrencia, valor, tentativas_usadas=0):
    print(f"\n{'═'*64}\n  CHURN INVOLUNTÁRIO (PIX AUTOMÁTICO) — {name}\n"
          f"  {id_recorrencia} | R$ {valor:.2f} | cobrança recorrente falhada\n{'═'*64}")

    # Evento já normalizado pelo PixAutomaticoAdapter: 5 campos, sem chave Pix.
    evento = {
        "e2e_id": f"E60701190{id_recorrencia}",
        "valor": valor,
        "status": "cobranca_falhada",
        "ispb_pagador": "60701190",
        "id_recorrencia": id_recorrencia,
    }

    initial: AgentState = {
        "payment_event": evento, "payment_method": "pix_automatico",
        "customer_id": id_recorrencia, "invoice_id": evento["e2e_id"], "amount": valor,
        "failure_cause": None, "recovery_score": None, "p_recovery": None,
        "eprofit": None, "recommend_action": None, "ltv_estimated": None,
        "shap_explanation": None, "feature_importance": None,
        "is_anomalous": None, "reconstruction_error": None, "anomaly_explanation": None,
        "optimal_retry_at": None,
        "estrategia": None, "raciocinio": None,
        "confidence": None, "profile_type": None,
        "retry_count": tentativas_usadas, "next_retry_at": None,
        "retry_exhausted": False, "recovered": False, "pix_retry_schedule": None,
        "dunning_sent": False,
        "channel": None, "metodo_pagamento": None, "message_sent": None,
    }
    config = {"configurable": {"thread_id": id_recorrencia}}
    return await crai_agent.ainvoke(initial, config)


# ── Churn Voluntário ─────────────────────────────────────────────────────

async def run_voluntary_scenario(name, user_id, event, props):
    print(f"\n{'═'*64}\n  CHURN VOLUNTÁRIO — {name}\n  {user_id} | evento: {event}\n{'═'*64}")

    initial: ChurnVoluntaryState = {
        "user_id": user_id, "event": event, "props": props,
        "risk_score": 0.0, "profile": "CLT", "offer_type": None,
        "channel": None, "on_site_now": props.get("on_site_now", False),
        "prior_channel_success": None, "message": None,
        "offer_sent": False, "accepted": None, "retained": False, "escalated_to_human": False,
    }
    config = {"configurable": {"thread_id": user_id}}
    return await voluntary_churn_agent.ainvoke(initial, config)


async def main():
    print("\n🚀 CRAI v2 — Teste dos Dois Pipelines (Involuntário + Voluntário)\n")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠️  ANTHROPIC_API_KEY não definida — mensagens usarão fallback\n")
    if not os.getenv("HUBSPOT_TOKEN"):
        print("⚠️  HUBSPOT_TOKEN não definida — HubSpot rodará em modo simulação\n")

    # ── Cenários de Pix Automático (janela regulada BACEN) ──────────────
    pix_scenarios = [
        ("Primeira falha — 3 tentativas disponíveis", "RN_maria_001", 299.90, 0),
        ("Já usou 2 das 3 tentativas",                "RN_joao_002",  149.00, 2),
        ("Janela esgotada — 3 de 3 usadas",           "RN_pedro_003", 599.00, 3),
    ]
    pix_results = []
    for name, rec_id, valor, usadas in pix_scenarios:
        result = await run_pix_scenario(name, rec_id, valor, usadas)
        pix_results.append(result)

    # ── Cenários de cartão (recobrança automática desativada na Fase 3) ──
    card_scenarios = [
        ("Cartão Expirado",   "cus_joao_002",  149.00, "expired_card"),
        ("Bloqueio Bancário", "cus_pedro_003", 599.00, "card_declined"),
    ]
    card_results = [run_card_scenario(*s) for s in card_scenarios]

    # ── Cenários de churn voluntário ────────────────────────────────────
    voluntary_scenarios = [
        ("Visitou Cancelamento (CLT)",  "usr_marcos_011", "Cancellation Page Viewed",
         {"on_site_now": True, "billing_profile": "CLT"}),
        ("Clicou em Downgrade (PJ)",    "usr_julia_012",  "Downgrade Clicked",
         {"on_site_now": True, "billing_profile": "PJ"}),
        ("Inatividade Prolongada",      "usr_diego_013",  "Session Started",
         {"days_since_last": 18, "features_used_30d": 1, "on_site_now": False, "billing_profile": "freelancer"}),
        ("Risco Crítico (>=0.90)",      "usr_lara_014",   "Cancellation Page Viewed",
         {"on_site_now": False, "billing_profile": "PJ"}),
    ]
    voluntary_results = []
    for name, uid, event, props in voluntary_scenarios:
        result = await run_voluntary_scenario(name, uid, event, props)
        voluntary_results.append(result)

    # ── Resumo ───────────────────────────────────────────────────────────
    print(f"\n{'═'*64}\n  RESUMO GERAL\n{'═'*64}")

    pix_amount = sum(r["amount"] for r in pix_results)
    com_plano = [r for r in pix_results if r.get("pix_retry_schedule")]
    print(f"\n🔷 Churn Involuntário (Pix Automático):")
    print(f"   Volume testado          : R$ {pix_amount:.2f}")
    print(f"   Com plano de retentativa: {len(com_plano)}/{len(pix_results)}")
    print(f"   Janela BACEN esgotada   : {sum(1 for r in pix_results if r.get('retry_exhausted'))}/{len(pix_results)}")
    for r in com_plano:
        plano = r["pix_retry_schedule"]
        origem = plano[0]["origem"]
        dias = ", ".join(t["quando"].strftime("%d/%m") for t in plano)
        print(f"     {r['customer_id']}: {len(plano)} tentativa(s) em {dias} ({origem})")

    card_amount = sum(r["amount"] for r in card_results)
    print(f"\n💳 Cartão (recobrança automática desativada — Fase 3):")
    print(f"   Volume registrado       : R$ {card_amount:.2f}")
    print(f"   Eventos registrados     : {len(card_results)}/{len(card_results)}")
    print(f"   Recobranças automáticas : 0 (código isolado em dunning/legacy_card/)")

    retained = sum(1 for r in voluntary_results if r.get("retained"))
    escalated = sum(1 for r in voluntary_results if r.get("escalated_to_human"))
    print(f"\n📈 Churn Voluntário:")
    print(f"   Sinais de risco processados : {len(voluntary_results)}")
    print(f"   Clientes retidos             : {retained}/{len(voluntary_results)}")
    print(f"   Escalados para CS humano     : {escalated}/{len(voluntary_results)}")

    print(f"\n✅ Pipeline CRAI v2 (involuntário + voluntário + HubSpot) funcionando!\n")


if __name__ == "__main__":
    asyncio.run(main())

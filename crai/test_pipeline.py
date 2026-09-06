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
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

# A DEMO RODA EM MODO SIMULAÇÃO, e isso é uma escolha, não um descuido.
#
# Em produção o grafo termina no envio e o desfecho chega depois, por
# `POST /webhooks/retention-outcome` (Sprint 4). Uma demo assim mostraria quatro
# clientes "aguardando retorno" e nenhum resultado — não dá para demonstrar
# retenção sem o desfecho. Com a env ligada, `track_outcome` sorteia o aceite
# pela taxa histórica do bandit e a demo fecha o ciclo na hora.
#
# `setdefault` e não atribuição: quem quiser ver o comportamento de produção
# roda `CRAI_SIMULATE_OUTCOMES=0 python test_pipeline.py` e o script respeita.
os.environ.setdefault("CRAI_SIMULATE_OUTCOMES", "1")

from crai.agent.main_agent import crai_agent
from crai.agent.state import AgentState
from crai.api.app import _fechar_ciclo_recuperado, _registrar_cartao_desativado
from crai.dunning import retry_state
from crai.dunning.retry_scheduler import processar_tentativas_devidas
from crai.churn_voluntary.voluntary_agent import agente_do_modo
from crai.churn_voluntary.state import ChurnVoluntaryState


# ── Cabeçalho dos cenários ───────────────────────────────────────────────
#
# UM lugar com a régua, e não um literal por cenário. A régua usa U+2550, que o
# console cp1252 do Windows não codifica (linha N-12 do README, catraca em
# `tests/test_encoding_saida.py`): cada cópia do literal era uma ocorrência a
# mais do mesmo defeito, e a quinta cópia — o cabeçalho da confirmação de
# pagamento do Sprint 1 — foi a que estourou a catraca. Consolidar aqui reduz o
# inventário em vez de aumentá-lo, e deixa um ponto único para trocar a régua
# por ASCII no dia em que a dívida for paga de vez.

REGUA = "═" * 64


def cabecalho(titulo: str, detalhe: str = "") -> None:
    linha_detalhe = f"\n  {detalhe}" if detalhe else ""
    print(f"\n{REGUA}\n  {titulo}{linha_detalhe}\n{REGUA}")


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
    cabecalho(f"CARTÃO (RECOBRANÇA DESATIVADA) — {name}",
              f"{customer_id} | R$ {amount:.2f} | {failure_code}")
    return _registrar_cartao_desativado(make_stripe_event(customer_id, amount, failure_code))


# ── Churn Involuntário via Pix Automático ────────────────────────────────

async def run_pix_scenario(name, id_recorrencia, valor, tentativas_usadas=0,
                           codigo_falha="AM04"):
    cabecalho(f"CHURN INVOLUNTÁRIO (PIX AUTOMÁTICO) — {name}",
              f"{id_recorrencia} | R$ {valor:.2f} | recusa do PSP: {codigo_falha}")

    # Evento já normalizado pelo PixAutomaticoAdapter: campos de dado, sem
    # chave Pix. `codigo_falha` é o motivo CRU da recusa; quem o traduz para o
    # vocabulário interno é o PIX_CODE_MAP (crai/agent/pix_codes.py).
    evento = {
        "e2e_id": f"E60701190{id_recorrencia}",
        "valor": valor,
        "status": "cobranca_falhada",
        "ispb_pagador": "60701190",
        "id_recorrencia": id_recorrencia,
        "codigo_falha": codigo_falha,
    }

    initial: AgentState = {
        "payment_event": evento, "payment_method": "pix_automatico",
        # Mesma empresa cliente dos cenários de churn voluntário: a demo mostra
        # os dois pipelines de UM tenant, que é como a CRAI é operada hoje.
        "tenant_id": "demo_tenant",
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


async def run_pix_confirmacao(id_recorrencia, valor):
    """A confirmação de pagamento que fecha o ciclo — o outro fim do loop.

    Em produção este evento chega por `POST /webhooks/pix-automatico` com
    status de cobrança confirmada, enviado pelo Pagar.me quando uma das
    tentativas reenviadas é paga. A demo chama a mesma função que o webhook
    chama (`_fechar_ciclo_recuperado`), e não uma versão paralela: o que a
    banca vê é o código de produção, sem PSP real.

    Sem esta metade, `recovered` nunca vira True, o success fee nunca aparece
    e a demo mostra três clientes em retentativa e nenhuma recuperação — o
    mesmo buraco que o `/webhooks/retention-outcome` fechou no voluntário.
    """
    cabecalho("CONFIRMAÇÃO DE PAGAMENTO (PIX AUTOMÁTICO)",
              f"{id_recorrencia} | R$ {valor:.2f} | cobrança recorrente paga")
    return await _fechar_ciclo_recuperado(
        customer_id=id_recorrencia,
        e2e_id=f"E60701190{id_recorrencia}_pago",
        valor=valor,
        tenant_id="demo_tenant",
    )


async def run_agendador(dias_a_frente):
    """O cron de produção, com o relógio adiantado — as tentativas 2 e 3.

    A política do BACEN concede 3 tentativas na janela de 7 dias e devolve o
    plano inteiro; o nó do grafo dispara a que já é devida e termina. As outras
    caem em dias seguintes, com o `ainvoke` encerrado há muito. Em produção
    quem volta nessas datas é um cron chamando
    `processar_tentativas_devidas()` — infraestrutura, não agente.

    Aqui o relógio é adiantado dia a dia para a banca ver as 3 tentativas
    saindo em sequência sem esperar uma semana. O que roda é o MESMO código de
    produção; o que muda é só o instante informado.
    """
    cabecalho("AGENDADOR DE RETENTATIVAS (as tentativas 2 e 3 do BACEN)",
              "o cron de produção, com o relógio adiantado dia a dia")
    disparos = []
    for dia in range(1, dias_a_frente + 1):
        momento = datetime.now() + timedelta(days=dia, hours=1)
        do_dia = await processar_tentativas_devidas(momento)
        if do_dia:
            print(f"  D+{dia}: {len(do_dia)} tentativa(s) reenviada(s) ao PSP")
        disparos.extend(do_dia)
    if not disparos:
        print("  nenhuma tentativa devida no período")
    return disparos


# ── Churn Voluntário ─────────────────────────────────────────────────────

async def run_voluntary_scenario(name, user_id, event, props):
    cabecalho(f"CHURN VOLUNTÁRIO — {name}", f"{user_id} | evento: {event}")

    initial: ChurnVoluntaryState = {
        "tenant_id": "demo_tenant", "user_id": user_id, "event": event, "props": props,
        "risk_score": 0.0, "profile": "CLT", "criticality": "padrao", "offer_type": None,
        "channel": None, "on_site_now": props.get("on_site_now", False),
        "prior_channel_success": None, "message": None,
        "offer_sent": False, "accepted": None, "retained": False, "is_critical": False,
    }
    config = {"configurable": {"thread_id": f"demo_tenant:{user_id}"}}
    return await agente_do_modo().ainvoke(initial, config)


async def main():
    print("\n🚀 CRAI v2 — Teste dos Dois Pipelines (Involuntário + Voluntário)\n")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠️  ANTHROPIC_API_KEY não definida — mensagens usarão fallback\n")
    if not os.getenv("HUBSPOT_TOKEN"):
        print("⚠️  HUBSPOT_TOKEN não definida — HubSpot rodará em modo simulação\n")

    # Estado de retentativa zerado a cada execução da demo: sem isto, o plano
    # da rodada anterior segue pendente em `data/` e o agendador o dispararia
    # junto com o desta, embaralhando a contagem que a banca vê.
    retry_state.limpar_tudo()

    # ── Cenários de Pix Automático (janela regulada BACEN) ──────────────
    #
    # O quinto campo é o motivo CRU da recusa, e é o que o Sprint 3 trouxe: até
    # então toda falha de Pix era diagnosticada como falta de saldo, e os dois
    # cenários de baixo — limite estourado e autorização revogada — teriam ido
    # para retentativa, gastando tentativas do BACEN em algo que não pode
    # passar. Agora eles vão direto para a mensagem personalizada, com o pedido
    # certo: aumentar o limite, ou reautorizar a recorrência.
    pix_scenarios = [
        ("Saldo insuficiente — 3 tentativas disponíveis", "RN_maria_001", 299.90, 0, "AM04"),
        ("Saldo insuficiente — já usou 2 das 3",          "RN_joao_002",  149.00, 2, "AM04"),
        ("Janela esgotada — 3 de 3 usadas",               "RN_pedro_003", 599.00, 3, "AM04"),
        ("Limite do Pix Automático excedido",             "RN_ana_004",   899.00, 0, "AM02"),
        ("Autorização de recorrência revogada",           "RN_luis_005",  199.00, 0, "MD01"),
    ]
    pix_results = []
    for name, rec_id, valor, usadas, codigo in pix_scenarios:
        result = await run_pix_scenario(name, rec_id, valor, usadas, codigo)
        pix_results.append(result)

    # ── As tentativas 2 e 3: o agendador percorre a janela do BACEN ─────
    disparos = await run_agendador(dias_a_frente=8)

    # ── O ciclo se fecha: o pagamento chega e a recuperação é contabilizada ──
    #
    # Só o primeiro cenário paga. Os outros dois seguem em aberto de
    # propósito: uma demo em que 100% recupera não mede nada, e a taxa de
    # recuperação (Sprint 6) precisa de denominador.
    rec_pago, valor_pago = pix_scenarios[0][1], pix_scenarios[0][2]  # noqa: E501
    confirmacao = await run_pix_confirmacao(rec_pago, valor_pago)
    # O reenvio do mesmo webhook — comportamento normal de PSP at-least-once —
    # não pode faturar de novo. Provado na própria demo, não só no pytest.
    reenvio = await run_pix_confirmacao(rec_pago, valor_pago)

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
        # Único cenário com telefone: é o que exercita o canal WhatsApp do
        # Sprint 3. Os outros três cobrem popup e e-mail.
        ("Risco Crítico (>=0.90) — WhatsApp", "usr_lara_014", "Cancellation Page Viewed",
         {"on_site_now": False, "billing_profile": "PJ", "phone": "+55 11 91234-5678"}),
    ]
    voluntary_results = []
    for name, uid, event, props in voluntary_scenarios:
        result = await run_voluntary_scenario(name, uid, event, props)
        voluntary_results.append(result)

    # ── Resumo ───────────────────────────────────────────────────────────
    cabecalho("RESUMO GERAL")

    pix_amount = sum(r["amount"] for r in pix_results)
    com_plano = [r for r in pix_results if r.get("pix_retry_schedule")]
    print(f"\n🔷 Churn Involuntário (Pix Automático):")
    print(f"   Volume testado          : R$ {pix_amount:.2f}")
    print(f"   Com plano de retentativa: {len(com_plano)}/{len(pix_results)}")
    print(f"   Janela BACEN esgotada   : {sum(1 for r in pix_results if r.get('retry_exhausted'))}/{len(pix_results)}")
    causas = {}
    for r in pix_results:
        causa = r.get("failure_cause", "?")
        causas[causa] = causas.get(causa, 0) + 1
    print(f"   Diagnóstico por causa   : "
          f"{', '.join(f'{c}: {n}' for c, n in sorted(causas.items()))}")
    assert len(causas) > 1, (
        "INVARIANTE VIOLADO: todas as falhas de Pix receberam a mesma causa — o "
        "PIX_CODE_MAP não está sendo aplicado e o diagnóstico voltou a ser cego")
    nao_retentaveis = [r for r in pix_results
                       if r.get("failure_cause") in ("limit_exceeded", "authorization_revoked")]
    assert all(r.get("estrategia") == "mensagem_pagamento" for r in nao_retentaveis), (
        "INVARIANTE VIOLADO: causa não retentável foi para retentativa — "
        "tentativa do BACEN gasta em algo que não pode passar")
    print(f"   Sem retentativa (por causa): {len(nao_retentaveis)} "
          f"(limite excedido / autorização revogada → mensagem personalizada)")
    for r in com_plano:
        plano = r["pix_retry_schedule"]
        origem = plano[0]["origem"]
        dias = ", ".join(t["quando"].strftime("%d/%m") for t in plano)
        print(f"     {r['customer_id']}: {len(plano)} tentativa(s) em {dias} ({origem})")

    por_cliente = {}
    for d in disparos:
        por_cliente[d["customer_id"]] = por_cliente.get(d["customer_id"], 0) + 1
    print(f"   Tentativas reenviadas ao PSP : {len(disparos)} "
          f"({', '.join(f'{c}: {n}' for c, n in sorted(por_cliente.items())) or 'nenhuma'})")
    assert all(n <= 3 for n in por_cliente.values()), (
        f"INVARIANTE VIOLADO: mais de 3 tentativas na janela do BACEN: {por_cliente}")
    print(f"   Ciclos fechados por pagamento: 1/{len(pix_results)} "
          f"({confirmacao['ciclo']}, fee R$ {confirmacao['fee']:.2f}, "
          f"tenant {confirmacao.get('tenant_id', '?')})")
    print(f"   Reenvio da mesma confirmação  : {reenvio['ciclo']} "
          f"(fee R$ {reenvio['fee']:.2f} — não recontado)")
    assert confirmacao["fee"] > 0, (
        "INVARIANTE VIOLADO: o ciclo fechou sem success fee — `recovered` não "
        "chegou ao update_roi_dashboard")
    assert reenvio["fee"] == 0, (
        f"INVARIANTE VIOLADO: o reenvio da confirmação faturou "
        f"R$ {reenvio['fee']:.2f} pela segunda vez")

    card_amount = sum(r["amount"] for r in card_results)
    print(f"\n💳 Cartão (recobrança automática desativada — Fase 3):")
    print(f"   Volume registrado       : R$ {card_amount:.2f}")
    print(f"   Eventos registrados     : {len(card_results)}/{len(card_results)}")
    print(f"   Recobranças automáticas : 0 (código isolado em dunning/legacy_card/)")

    retained = sum(1 for r in voluntary_results if r.get("retained"))
    criticos = sum(1 for r in voluntary_results if r.get("is_critical"))
    # O invariante de produto, medido em vez de afirmado: nenhum resultado pode
    # sair do grafo carregando escalação humana. `consulta_cs` e
    # `escalated_to_human` deixaram de existir no Sprint 1 — esta linha reprova
    # a demo se algum dia voltarem.
    escalados = sum(1 for r in voluntary_results
                    if r.get("escalated_to_human") or r.get("offer_type") == "consulta_cs")
    assert escalados == 0, f"INVARIANTE VIOLADO: {escalados} escalação(ões) humana(s)"
    print(f"\n📈 Churn Voluntário:")
    print(f"   Sinais de risco processados : {len(voluntary_results)}")
    print(f"   Clientes retidos             : {retained}/{len(voluntary_results)}")
    canais = {}
    for r in voluntary_results:
        if r.get("channel"):
            canais[r["channel"]] = canais.get(r["channel"], 0) + 1
    distribuicao = ", ".join(f"{c}: {n}" for c, n in sorted(canais.items()))
    print(f"   Sinalizados como críticos    : {criticos}/{len(voluntary_results)} (tom ajustado)")
    print(f"   Canais escolhidos            : {distribuicao}")
    print(f"   Escalações para humano        : 0/{len(voluntary_results)} (invariante verificado)")

    # O dataset de treino, medido em vez de prometido. Em produção estes ciclos
    # ficariam "aguardando" até o webhook de desfecho; aqui a demo os fecha.
    from crai.churn_voluntary.retention_log import estatisticas
    st = estatisticas()
    print(f"   Ciclos no dataset de treino  : {st['total']} "
          f"({st['com_desfecho']} com desfecho, {st['aguardando']} aguardando)")

    print(f"\n✅ Pipeline CRAI v2 (involuntário + voluntário + HubSpot) funcionando!\n")


if __name__ == "__main__":
    asyncio.run(main())

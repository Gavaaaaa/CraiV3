"""
crai/integrations/hubspot_crm.py
Integração HubSpot CRM — espelha clientes e ciclos de recuperação/retenção.

Funciona em modo simulação se HUBSPOT_TOKEN não estiver definido —
não quebra o pipeline durante desenvolvimento.
"""

import hashlib
import os
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from hubspot import HubSpot
    from hubspot.crm.contacts import SimplePublicObjectInputForCreate
    from hubspot.crm.deals import SimplePublicObjectInputForCreate as DealInput
    HUBSPOT_AVAILABLE = True
except ImportError:
    HUBSPOT_AVAILABLE = False

DEAL_STAGES = {
    "diagnosing": "diagnosing", "retrying": "retrying", "dunning_sent": "dunning_sent",
    "recovered": "recovered", "lost": "lost",
    # Pipeline de churn voluntário
    "risk_detected": "risk_detected", "offer_sent": "offer_sent",
    "retained": "retained", "churned": "churned",
}


class HubSpotCRM:
    def __init__(self):
        token = os.getenv("HUBSPOT_TOKEN", "")
        self.dry = not token or not HUBSPOT_AVAILABLE
        if self.dry:
            print("[HUBSPOT] Modo simulação (sem token ou SDK não instalado)")
        else:
            self.client = HubSpot(access_token=token)

    async def upsert_contact(self, customer_id: str, **extra) -> Optional[str]:
        props = {"stripe_customer_id": customer_id, "lifecyclestage": "customer", **extra}
        if self.dry:
            print(f"[HUBSPOT-SIM] Contact upsert: {customer_id}")
            return f"sim_contact_{customer_id}"
        try:
            obj = SimplePublicObjectInputForCreate(properties=props)
            result = self.client.crm.contacts.basic_api.create(simple_public_object_input_for_create=obj)
            return result.id
        except Exception as e:
            logger.error(f"[HUBSPOT] Erro contact: {e}")
            return None

    async def create_deal(self, name: str, pipeline: str, stage: str, props: dict) -> Optional[str]:
        full_props = {"dealname": name, "pipeline": pipeline, "dealstage": DEAL_STAGES.get(stage, stage), **props}
        if self.dry:
            print(f"[HUBSPOT-SIM] Deal criado: {name} | pipeline={pipeline} | stage={stage}")
            # md5 e nao hash(): o hash() de string e randomizado por processo,
            # o que faria o id do deal mudar a cada execucao da demo.
            digest = hashlib.md5(name.encode()).hexdigest()
            return f"sim_deal_{int(digest, 16) % 100000}"
        try:
            obj = DealInput(properties=full_props)
            result = self.client.crm.deals.basic_api.create(simple_public_object_input_for_create=obj)
            return result.id
        except Exception as e:
            logger.error(f"[HUBSPOT] Erro deal: {e}")
            return None

    # ── Churn involuntário ──────────────────────────────────────────────

    async def register_recovery_cycle(self, state: dict) -> dict:
        contact_id = await self.upsert_contact(state["customer_id"])

        if state.get("recovered"):
            stage = "recovered"
        elif state.get("dunning_sent"):
            stage = "dunning_sent"
        elif state.get("retry_exhausted"):
            stage = "lost"
        else:
            stage = "retrying"

        deal_name = f"Recuperação {state['customer_id'][:12]} — R$ {state['amount']:.2f}"
        deal_id = await self.create_deal(
            name=deal_name, pipeline="crai_recovery", stage=stage,
            props={
                "amount": str(round(state["amount"], 2)),
                "failure_cause": state.get("failure_cause", "unknown"),
                "recovery_score": str(round(state.get("recovery_score", 0), 3)),
            },
        )
        return {"hubspot_contact_id": contact_id, "hubspot_deal_id": deal_id, "stage": stage}

    # ── Churn voluntário ────────────────────────────────────────────────

    async def register_retention_cycle(self, state: dict) -> dict:
        contact_id = await self.upsert_contact(state["user_id"])

        if state.get("retained"):
            stage = "retained"
        elif state.get("offer_sent"):
            stage = "offer_sent"
        else:
            stage = "risk_detected"

        deal_name = f"Retenção {state['user_id'][:12]} — risco {state['risk_score']:.0%}"
        deal_id = await self.create_deal(
            name=deal_name, pipeline="crai_retention", stage=stage,
            props={
                "risk_score": str(round(state["risk_score"], 3)),
                "trigger_event": state.get("event", "unknown"),
                "offer_type": state.get("offer_type", ""),
                "channel": state.get("channel", ""),
            },
        )
        return {"hubspot_contact_id": contact_id, "hubspot_deal_id": deal_id, "stage": stage}

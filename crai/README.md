# CRAI v2 — POC Completa

Agente autônomo de recuperação de receita: churn involuntário + churn voluntário + CRM automático.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edite .env — só ANTHROPIC_API_KEY é necessária para mensagens reais via Claude

python test_pipeline.py
```

O `test_pipeline.py` roda **8 cenários** (4 de cada tipo de churn) sem precisar de Stripe, Segment ou HubSpot reais.

## Subir a API

```bash
uvicorn crai.api.app:app --reload
# http://localhost:8000/docs
```

### Testar churn involuntário

```bash
curl -X POST http://localhost:8000/simulate/payment-failed \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"cus_teste","amount":299.90,"failure_code":"insufficient_funds"}'
```

### Testar churn voluntário

```bash
curl -X POST http://localhost:8000/simulate/churn-risk \
  -H "Content-Type: application/json" \
  -d '{"user_id":"usr_teste","event":"Cancellation Page Viewed","on_site_now":true,"billing_profile":"CLT"}'
```

## Estrutura

```
crai/
├── agent/                      # Churn involuntário
│   ├── main_agent.py            # Grafo LangGraph principal
│   ├── workflow.py              # Nós: diagnose, anomaly, payday, retry, dunning, CRM
│   └── state.py
├── churn_voluntary/             # Churn voluntário (NOVO)
│   ├── voluntary_agent.py       # Grafo LangGraph: risk → offer → channel → message → CRM
│   ├── offer_bandit.py          # Multi-Armed Bandit — aprende a melhor oferta por perfil
│   ├── risk_scorer.py           # Calcula risk_score a partir de eventos Segment
│   └── state.py
├── ml/
│   ├── failure_classifier.py    # XGBoost + Random Forest — train() + predict()
│   ├── anomaly_detector.py      # Autoencoder PyTorch — train() + check()
│   ├── payday_inference.py      # LSTM + Prophet — train() + predict_next_window()
│   └── synthetic_data.py        # Geradores de dataset dos 3 modelos (seed 42)
├── scripts/
│   └── train_all.py             # Treina os 3 modelos: python -m crai.scripts.train_all
├── dunning/
│   ├── dunning_engine.py        # LangGraph + Claude API (dunning involuntário)
│   └── smart_backoff.py         # Backoff Exponencial + Jitter
├── integrations/
│   └── hubspot_crm.py           # CRM — 2 pipelines: recovery + retention
├── api/
│   └── app.py                   # FastAPI: webhooks Stripe + Segment + endpoints de simulação
├── test_pipeline.py
└── requirements.txt
```

## Os dois pipelines

### 1. Churn Involuntário (pagamento falhou)

```
Stripe webhook
    ↓
XGBoost (causa raiz) → Autoencoder (anomalia) → LSTM+Prophet (payday)
    ↓
Backoff Exponencial → [esgotado?] → Claude API (dunning) → HubSpot
```

### 2. Churn Voluntário (cliente em risco) — NOVO

```
Segment SDK (evento de comportamento)
    ↓
risk_scorer (calcula score) → [risco >= 0.60?]
    ↓
Multi-Armed Bandit (escolhe oferta) → LangGraph (escolhe canal com memória)
    ↓
Claude API (mensagem) → envio → track_outcome → HubSpot
```

## Sobre o Multi-Armed Bandit

Em vez de regras fixas ("sempre oferecer 20% de desconto"), o bandit testa diferentes ofertas
(`desconto_10`, `desconto_20`, `pausa_1_mes`, `consulta_cs`) por perfil de cliente (CLT/PJ/freelancer)
e aprende qual converte mais — com prioris realistas pré-carregadas para não começar do zero.

Score de risco >= 0.90 sempre pula direto para `consulta_cs` (intervenção humana).

## HubSpot — dois pipelines

```
crai_recovery (churn involuntário):
  diagnosing → retrying → dunning_sent → recovered / lost

crai_retention (churn voluntário):
  risk_detected → offer_sent → retained / churned
```

Sem `HUBSPOT_TOKEN`, tudo roda em modo simulação — os logs mostram exatamente o que seria
criado no HubSpot, sem quebrar o pipeline.

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

## Dívida técnica conhecida (roadmap pós-demo)

Itens levantados nas auditorias adversariais A1 e A1-r2 dos sprints de 31/08–03/09 e **deliberadamente
não corrigidos antes da demo**, por serem risco maior que ganho nessa janela.
Estão aqui para não virarem dívida esquecida.

| ID | Onde | O quê | Por que ficou |
|---|---|---|---|
| **P2-10** | `agent/workflow.py::infer_payday` | O Payday Engine roda **2× por recuperação**: uma no nó `infer_payday` e outra dentro de `PixAutomaticoRetryPolicy._consultar_payday`. Além disso, os campos que o nó grava (`optimal_retry_at`, `confidence`, `profile_type`) **não são lidos por nenhum nó ativo** — a política consulta o modelo por conta própria. | O ganho é performance; o risco é quebrar o caminho feliz da demo. Unificar depois: ou a política recebe a previsão pelo state, ou o nó deixa de existir. |
| **P2-11** | `integrations/hubspot_crm.py` | Duplica inline o digest md5 estável de `seed_por_cliente` (`ml/synthetic_data.py`) em vez de importá-lo. Mesma decisão de projeto escrita em dois lugares. | Baixo risco, mas toca o id que aparece na demo. |
| **P2-13** | `integrations/payment_gateway.py::store_encrypted_pix_key` | Read-modify-write no cofre de chaves Pix sem lock. | Não é chamado no pipeline ativo hoje. Vira P0 no dia em que a conciliação entrar em produção. |
| — | `ml/synthetic_data.py` | `tenure_months` e a probabilidade-base de recuperação (`p = 0.5`) continuam **não calibrados** — não existe fonte pública para nenhum dos dois. | Declarado em `docs/DATA_CARD.md`, seções 5 e 6. É limitação assumida, não descuido. |
| **N-8** | `agent/workflow.py` | `float(confidence)` fica **fora** do `try` que protege a chamada do Módulo 3: se o modelo devolver algo não numérico em `confidence`, a exceção escapa. | Não dispara com os modelos atuais. **Vira P0 no Sprint 4**, que é o próximo a mexer no retorno do Módulo 3. |
| **N-9** | `integrations/payment_gateway.py::_resolver_envelope` | `{"data": [<não-dict>]}` — lista de 1 elemento que não é objeto — cai no ramo do envelope em vez de ser recusada como lote. | Recusado adiante pelo portão de valor (422), então não produz decisão errada. Fecha junto com o próximo toque no envelope. |
| **N-11** | `integrations/payment_gateway.py` | `str()` sobre estrutura (dict/list) num campo de identificação produz um identificador em vez de recusar. | Só afeta payload malformado que já é recusado pelo valor. |
| **N-12** | `test_pipeline.py` | `UnicodeEncodeError` no console padrão do Windows; só roda com `PYTHONIOENCODING=utf-8`. | Pré-existente. **Tem que ser resolvido dentro do `demo_runner.py` no Sprint 5** — o teste de ambiente limpo do gate GA2 depende disso. |
| **N-6 (resíduo)** | `integrations/payment_gateway.py::store_encrypted_pix_key` | Descarta a lista `degradacoes` que recebe, em vez de registrá-la. | Fora do caminho do pipeline ativo, como o P2-13. |
| **§4.6** | `crai/models/` | Os binários em disco são de um treino anterior: **AUC 0,6797, abaixo do piso [0,70; 0,92]** dos gates G3/G4, e `train_metrics.json` com schema anterior ao commit `4109d84`. | **Fecha no Sprint 4**, que retreina os três módulos com os 15.000 do Sprint 3. Até lá, a demo carrega modelo que não passa no próprio gate. |

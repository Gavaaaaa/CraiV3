# CRAI - Churn Recovery Artificial Intelligence

**Retention OS para PMEs brasileiras de SaaS** (MRR R$500k - R$5M)

Sistema autônomo de recuperação de receita que combina Machine Learning, IA generativa e automação inteligente para combater churn involuntário e voluntário em empresas SaaS B2B no mercado brasileiro.

> Projeto de Conclusão de Curso (TCC) — Engenharia de Software

---

## O Problema

PMEs de SaaS no Brasil perdem entre **5% a 12% do MRR** mensalmente com churn involuntário (falhas de pagamento) e voluntário (cancelamentos). A maioria usa regras fixas de retentativa que ignoram o contexto do cliente, desperdiçando dinheiro em intervenções de baixo retorno e perdendo clientes que poderiam ser recuperados.

## A Solução

O CRAI é um **agente autônomo** que:

1. **Diagnostica** a causa raiz de cada falha de pagamento (XGBoost + Random Forest)
2. **Detecta anomalias** no comportamento do cliente (Autoencoder PyTorch)
3. **Prediz liquidez** — quando o cliente terá saldo (LSTM + Prophet)
4. **Seleciona a melhor oferta** de retenção por perfil (Thompson Sampling)
5. **Raciocina e executa** ações personalizadas (LangGraph + LLM)
6. **Aprende continuamente** com os resultados

### Diferencial: Métrica e-Profit

Enquanto sistemas tradicionais otimizam acurácia ou AUC, o CRAI otimiza **e-Profit**:

```
e-Profit = (P_recuperação × LTV) - Custo_intervenção
```

Recomenda intervenção **somente se e-Profit > 0**, evitando gastar R$15 numa ligação CS para recuperar um cliente de R$49,90/mês com baixa probabilidade de sucesso.

---

## Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                        CRAI - Pipeline                          │
├─────────────────────┬───────────────────────────────────────────┤
│  CHURN INVOLUNTÁRIO │  CHURN VOLUNTÁRIO                        │
│  (pagamento falhou) │  (cliente em risco)                      │
├─────────────────────┼───────────────────────────────────────────┤
│                     │                                           │
│  Stripe Webhook     │  Segment SDK (evento comportamental)     │
│       │             │       │                                   │
│       ▼             │       ▼                                   │
│  ┌──────────┐       │  ┌──────────┐                            │
│  │ XGBoost  │       │  │  Risk    │                            │
│  │ + RF     │       │  │  Scorer  │                            │
│  │ (causa)  │       │  │          │                            │
│  └────┬─────┘       │  └────┬─────┘                            │
│       ▼             │       ▼                                   │
│  ┌──────────┐       │  ┌──────────┐                            │
│  │Autoenc.  │       │  │ Thompson │                            │
│  │(anomalia)│       │  │ Sampling │                            │
│  └────┬─────┘       │  │ (oferta) │                            │
│       ▼             │  └────┬─────┘                            │
│  ┌──────────┐       │       ▼                                   │
│  │LSTM +    │       │  ┌──────────┐                            │
│  │Prophet   │       │  │ LangGraph│                            │
│  │(payday)  │       │  │ + LLM    │                            │
│  └────┬─────┘       │  │(execução)│                            │
│       ▼             │  └────┬─────┘                            │
│  ┌──────────┐       │       ▼                                   │
│  │ Dunning  │       │  ┌──────────┐                            │
│  │ Engine   │       │  │ Track    │                            │
│  │(LangGraph│       │  │ Outcome  │                            │
│  │ + LLM)   │       │  └────┬─────┘                            │
│  └────┬─────┘       │       │                                   │
│       │             │       │                                   │
├───────┴─────────────┴───────┴───────────────────────────────────┤
│                    HubSpot CRM (2 pipelines)                    │
│            crai_recovery │ crai_retention                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## Stack Tecnológico

| Camada | Tecnologia | Função |
|--------|-----------|--------|
| **ML - Classificação** | XGBoost + Random Forest | Diagnóstico de falha + score de recuperabilidade |
| **ML - Anomalia** | PyTorch (Autoencoder) | Detecção de comportamento anômalo não supervisionada |
| **ML - Séries Temporais** | LSTM + Prophet | Inferência de liquidez (quando o cliente terá saldo) |
| **ML - Otimização** | Thompson Sampling (MAB) | Seleção da melhor oferta de retenção por segmento |
| **XAI** | SHAP (TreeExplainer) | Explicabilidade por predição com log de auditoria JSON |
| **Agente** | LangGraph | Orquestração multi-etapa com raciocínio em loop |
| **LLM** | Claude API (Anthropic) | Geração de mensagens personalizadas por canal |
| **API** | FastAPI | Webhooks Stripe/Segment + endpoints de simulação |
| **CRM** | HubSpot API | Espelhamento automático de ciclos de recuperação/retenção |
| **Dados** | NumPy + Pandas | Dataset sintético com distribuições do mercado BR |

---

## Módulos de IA

### 1. Failure Classifier (`ml/failure_classifier.py`)

Ensemble **XGBoost (70%) + Random Forest (30%)** treinado em dataset sintético de 3.000 transações com distribuições plausíveis para SaaS B2B brasileiro.

**Features:**
- Tenure (meses de casa) — preditor mais forte, correlação negativa com churn
- Histórico de pagamento (score 0-1)
- Código de erro do gateway (6 tipos)
- Valor da fatura, ticket médio
- Dia do mês, hora, dia da semana
- Quantidade de falhas nos últimos 90 dias
- LTV estimado (para cálculo do e-Profit)

**Outputs:**
- Score de recuperabilidade (0-100)
- e-Profit por canal de intervenção
- Explicação SHAP por feature (texto legível em PT-BR)
- Recomendação de canal ótimo

**Exemplo de saída SHAP:**
```
Score 54/100 — Histórico de pagamento limpo (+23.8%) | Código de erro
insufficient_funds (-20.3%) | Falhas (90 dias) 0 (+14.0%)
```

### 2. Anomaly Detector (`ml/anomaly_detector.py`)

Autoencoder denso em **PyTorch** (12→32→16→4→16→32→12) treinado **apenas em clientes saudáveis** — o erro de reconstrução funciona como score de anomalia não supervisionado.

**Features (12):** tenure, MRR, seats, logins 7d/30d, adoção de features, duração média de sessão, chamadas de API, dias desde o último login, tickets, falhas de pagamento 90d, NPS.

**Outputs:**
- Erro de reconstrução (score de anomalia) + flag binária via threshold no percentil 95 dos saudáveis
- Top features que mais contribuem para o erro (explicabilidade)
- Anomalia reduz `p_recovery` em 30% e recalcula o e-Profit downstream

Pipeline de treino, avaliação e figuras para a banca em [`modulo_02_autoencoder/`](modulo_02_autoencoder/). Métricas: ROC-AUC **0.996**, recall **97,4%** @ p95, separação saudáveis vs anômalos de **11,6x**.

### 3. Payday Inference (`ml/payday_inference.py`)

**LSTM + Prophet** para inferência de liquidez — prediz **quando** o cliente terá saldo para a retentativa.

- **LSTM** (64 unid., 2 camadas): janela de 30 dias de saldo do cliente → probabilidade de liquidez nos 14 dias seguintes (seq2vec)
- **Prophet por perfil** (CLT/PJ/freelancer): prior da sazonalidade brasileira — 5º dia útil, dias 10, 15, 20 e 30
- **Ensemble 0.6/0.4**: janela ótima = primeiro dia com P(liquidez) ≥ 0.5

**Outputs:** data ótima de retry + confiança + perfil inferido (84% de acurácia via âncoras de payday).

Pipeline de treino e relatório em [`modulo_03_payday/`](modulo_03_payday/). Métricas: MAE de **0,62 dia** vs 5,01 da heurística de dias fixos; acerto da janela com ±1 dia em **89,3%** dos casos (ROC-AUC diário 0.97).

### 4. Offer Selector (`churn_voluntary/offer_bandit.py`)

**Thompson Sampling** (Multi-Armed Bandit Beta-Bernoulli) para seleção de ofertas de retenção.

- 5 braços: desconto 10%, desconto 20%, pausa 1 mês, consulta CS, Pix/Boleto Flash
- Um posterior Beta(α,β) por par (perfil, oferta) — a exploração decai sozinha, sem epsilon
- **Otimiza e-Profit, não conversão**: `argmax p_amostrado × LTV_retido − custo(oferta)` — no CLT, a consulta CS converte mais (50%) mas perde para o desconto de 10% por causa do custo humano
- Aprendizado contínuo: cada aceite/recusa real atualiza o posterior e persiste em disco

Simulação, relatório e figuras em [`modulo_04_offer_bandit/`](modulo_04_offer_bandit/). Em 6.000 rodadas: **+R$199 mil** vs o epsilon-greedy anterior, regret 40% menor, 87% de escolhas ótimas ao final.

### 5. Agente LangGraph (`agent/` + `dunning/`)

Orquestração multi-etapa com **raciocínio (ReAct)** sobre o contexto acumulado:
- Avalia a situação com contexto ML + SHAP + anomaly score + payday
- Decide entre **retentativa automática** e **mensagem de pagamento** — sem
  escalonamento humano: quando a retentativa não resolve (ou já falhou), o
  sistema gera uma **mensagem personalizada via LLM**
- Meio de pagamento com **Pix Automático como primeira opção, boleto no fallback**
- Cada nó loga seu raciocínio em PT-BR (trilha de auditoria)

---

## Estrutura do Projeto

```
crai/
├── crai/
│   ├── agent/                          # Agente de churn involuntário
│   │   ├── main_agent.py               # Grafo LangGraph principal
│   │   ├── workflow.py                 # Nós: diagnose → anomaly → payday → decide → retry/dunning
│   │   └── state.py                    # Schema de estado (TypedDict)
│   │
│   ├── churn_voluntary/                # Pipeline de churn voluntário
│   │   ├── voluntary_agent.py          # Grafo: risk → offer → channel → message → track
│   │   ├── offer_bandit.py             # Thompson Sampling (Multi-Armed Bandit)
│   │   ├── risk_scorer.py              # Score de risco via eventos Segment
│   │   └── state.py                    # Schema de estado voluntário
│   │
│   ├── ml/                             # Algoritmos de Machine Learning
│   │   ├── failure_classifier.py       # XGBoost + RF + e-Profit + SHAP
│   │   ├── anomaly_detector.py         # Autoencoder PyTorch
│   │   ├── payday_inference.py         # LSTM + Prophet
│   │   └── synthetic_data.py           # Gerador de dataset sintético (seed 42)
│   │
│   ├── dunning/                        # Motor de cobrança inteligente
│   │   ├── dunning_engine.py           # LangGraph + Claude API (multicanal)
│   │   └── smart_backoff.py            # Backoff exponencial + jitter
│   │
│   ├── integrations/                   # Integrações externas
│   │   └── hubspot_crm.py             # CRM com 2 pipelines (recovery + retention)
│   │
│   └── api/                            # API REST
│       └── app.py                      # FastAPI: webhooks + simulação
│
├── tests/
│   └── test_failure_classifier.py      # 26 testes unitários (pytest)
│
├── test_pipeline.py                    # Demo: 8 cenários (4 involuntário + 4 voluntário)
├── requirements.txt                    # Dependências com versões fixadas
└── .env.example                        # Template de variáveis de ambiente
```

---

## Setup

### Pré-requisitos

- Python 3.11+
- pip

### Instalação

```bash
cd crai
pip install -r requirements.txt
cp .env.example .env
```

### Variáveis de Ambiente

| Variável | Obrigatória | Descrição |
|----------|:-----------:|-----------|
| `ANTHROPIC_API_KEY` | Para LLM | Mensagens personalizadas via Claude API |
| `STRIPE_SECRET_KEY` | Não | Modo simulação funciona sem |
| `HUBSPOT_TOKEN` | Não | CRM roda em modo simulação sem token |
| `SEGMENT_WRITE_KEY` | Não | Simulação via `/simulate/churn-risk` |

> Todos os módulos funcionam **sem nenhuma API key** — fallbacks heurísticos e templates estáticos substituem as chamadas externas.

### Proteção contra Commit de Segredos (opcional)

O repositório inclui um hook de pre-commit que bloqueia commits contendo um
`.env` real ou padrões de chave conhecidos (`sk-ant-`, `sk_live_`, `whsec_`,
`pat-na1-` seguidos de caracteres reais). Ele não roda automaticamente —
para ativar localmente:

```bash
cp scripts/pre-commit-secrets.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

---

## Como Executar

### Treinar o modelo de classificação

```bash
cd crai
python -m crai.ml.failure_classifier
```

Gera dataset sintético (3.000 linhas), treina XGBoost+RF, calcula métricas e demonstra 3 predições com SHAP.

### Rodar testes

```bash
cd crai
pytest tests/ -v
```

26 testes cobrindo: dataset sintético, heurística, treino, predição, SHAP, e-Profit, persistência.

### Pipeline completo (8 cenários)

```bash
cd crai
python test_pipeline.py
```

Roda 4 cenários de churn involuntário + 4 de churn voluntário com output visual no terminal.

### API (FastAPI)

```bash
cd crai
uvicorn crai.api.app:app --reload
# Swagger UI: http://localhost:8000/docs
```

**Endpoints de simulação:**

```bash
# Churn involuntário
curl -X POST http://localhost:8000/simulate/payment-failed \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"cus_teste","amount":299.90,"failure_code":"insufficient_funds"}'

# Churn voluntário
curl -X POST http://localhost:8000/simulate/churn-risk \
  -H "Content-Type: application/json" \
  -d '{"user_id":"usr_teste","event":"Cancellation Page Viewed","on_site_now":true}'
```

---

## Custos de Intervenção (e-Profit)

| Canal | Custo (R$) | Uso |
|-------|:----------:|-----|
| E-mail automático | 0,02 | Dunning padrão |
| Bot WhatsApp | 0,05 | Dunning + ofertas |
| SMS | 0,08 | Alertas urgentes |
| Link Pix/Boleto | 0,50 | Fallback de pagamento |
| Ligação CS humano | 15,00 | Clientes de alto LTV |

O sistema só recomenda intervenção quando `e-Profit > 0`, ou seja, quando o retorno esperado (probabilidade de recuperação x LTV) supera o custo do canal.

---

## Métricas do Módulo 1 (Failure Classifier)

| Métrica | Valor |
|---------|-------|
| AUC-ROC | 0.68 |
| Acurácia | 0.64 |
| e-Profit médio | R$ 404,93 |
| Clientes com e-Profit > 0 | 100% (teste) |

*Treinado em dataset sintético com seed fixa (42) para reprodutibilidade.*

---

## Roadmap de Implementação

- [x] **Módulo 1** — XGBoost + Random Forest (failure_classifier.py)
  - [x] Dataset sintético com distribuições brasileiras
  - [x] Ensemble XGBoost (70%) + RF (30%)
  - [x] Métrica e-Profit como critério de decisão
  - [x] SHAP TreeExplainer com logs JSON de auditoria
  - [x] 26 testes unitários (pytest)
- [x] **Módulo 2** — Autoencoder PyTorch (anomaly_detector.py)
  - [x] Dataset comportamental sintético (5000 saudáveis + 500 anômalos)
  - [x] Autoencoder denso 12→4→12 com early stopping (treino só em saudáveis)
  - [x] Threshold calibrado no percentil 95 (ROC-AUC 0.996, recall 97,4%)
  - [x] Explicabilidade: quebra do erro de reconstrução por feature
  - [x] Integração ao pipeline LangGraph com fallback heurístico
- [x] **Módulo 3** — LSTM + Prophet (payday_inference.py)
  - [x] Séries de liquidez sintéticas (600 clientes × 180 dias, 3 perfis BR)
  - [x] LiquidityLSTM seq2vec (30 dias → 14 dias) com split por cliente
  - [x] Prophet por perfil com sazonalidade mensal (payday brasileiro)
  - [x] Ensemble 0.6/0.4 — MAE 0,62 dia vs 5,01 da heurística (hit ±1d: 89%)
  - [x] Integração ao pipeline LangGraph com fallback heurístico
- [x] **Módulo 4** — Thompson Sampling (offer_selector.py)
  - [x] Ambiente simulado com verdade oculta (aceites por perfil × oferta, MRR lognormal)
  - [x] Beta-Bernoulli otimizando e-Profit (não conversão) com custo por braço
  - [x] Comparação pareada: +R$199k vs epsilon-greedy, 87% de escolhas ótimas
  - [x] Persistência dos posteriores (aprendizado contínuo sobrevive a restarts)
  - [x] Integração ao agente voluntário com warm start e 5º braço (Pix/Boleto)
- [x] **Módulo 5** — LangGraph + LLM (agent/ + dunning/)
  - [x] Nó de decisão (ReAct) sobre contexto ML + SHAP + anomalia + payday
  - [x] Decisão de 2 vias: retentativa automática | mensagem de pagamento
  - [x] Sem escalonamento humano — mensagem personalizada via LLM (Claude)
  - [x] Pix Automático como primeira opção, boleto no fallback
  - [x] Trilha de raciocínio em PT-BR por decisão (auditoria)
  - [x] Testes do grafo (7 casos de roteamento + dunning)
- [ ] **Módulo 6** — Demo para banca (demo_reasoning.py)

---

## Contexto do Mercado

O CRAI atende **PMEs brasileiras de SaaS** com MRR entre R$500k e R$5M:

- **Churn involuntário** (falhas de pagamento) representa 20-40% do churn total em SaaS
- **Pix Automático** (disponível desde jun/2025) é usado como primeiro fallback antes do boleto
- Sazonalidade brasileira: concentração de pagamentos no 5º dia útil, dia 10, 15 e 30
- Perfis de cliente: CLT (salário fixo), PJ (receita variável), Freelancer (alta variabilidade)

---

## Licença

Projeto acadêmico (TCC) — uso educacional.

---

*Desenvolvido como Trabalho de Conclusão de Curso em Engenharia de Software.*

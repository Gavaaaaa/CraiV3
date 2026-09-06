# CRAI - Churn Recovery Artificial Intelligence

**Retention OS para PMEs brasileiras de SaaS** (MRR R$500k - R$5M)

Sistema autônomo de recuperação de receita que combina Machine Learning, IA generativa e automação inteligente para combater churn involuntário e voluntário em empresas SaaS B2B no mercado brasileiro.

> Projeto de Conclusão de Curso (TCC) — Engenharia de Software

---

## O Problema

PMEs de SaaS no Brasil perdem entre **5% a 12% do MRR** mensalmente com churn involuntário (falhas de pagamento) e voluntário (cancelamentos). A maioria usa regras fixas de retentativa que ignoram o contexto do cliente, desperdiçando dinheiro em intervenções de baixo retorno e perdendo clientes que poderiam ser recuperados.

## A Solução

O CRAI é um **agente autônomo** que cobra via **Pix Automático** e:

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
│  Webhook Pix Autom. │  Segment SDK (evento comportamental)     │
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
│  │Retry BACEN│      │  ┌──────────┐                            │
│  │3x / 7 dias│      │  │ Track    │                            │
│  └────┬─────┘       │  │ Outcome  │                            │
│       ▼             │  └────┬─────┘                            │
│  ┌──────────┐       │       │                                   │
│  │ Dunning  │       │       │                                   │
│  │ Engine   │       │       │                                   │
│  │(LangGraph│       │       │                                   │
│  │ + LLM)   │       │       │                                   │
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
| **Pagamentos** | Pix Automático (PSP) | Cobrança recorrente com retentativa regulada pelo BACEN |
| **Segurança** | HMAC + Fernet | Assinatura dos webhooks + cifragem da chave Pix |
| **API** | FastAPI | Webhooks Pix/Stripe/Segment + endpoints de simulação |
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

Treino via `AnomalyDetector.train()` — gera o dataset comportamental sintético, treina só nos saudáveis, calibra o threshold no percentil 95 dos saudáveis de validação (held-out) e salva os artefatos em `crai/models/`. Métricas do último treino: ROC-AUC **0.995**, recall **98,4%** @ p95, separação saudáveis vs anômalos de **7,7x**.

### 3. Payday Inference (`ml/payday_inference.py`)

**LSTM + Prophet** para inferência de liquidez — prediz **quando** o cliente terá saldo para a retentativa.

- **LSTM** (64 unid., 2 camadas): janela de 30 dias de saldo do cliente → probabilidade de liquidez nos 14 dias seguintes (seq2vec)
- **Prophet por perfil** (CLT/PJ/freelancer): prior da sazonalidade brasileira — 5º dia útil, dias 10, 15, 20 e 30
- **Ensemble 0.6/0.4**: janela ótima = primeiro dia com P(liquidez) ≥ 0.5

**Outputs:** data ótima de retry + confiança + perfil inferido (84% de acurácia via âncoras de payday).

Treino via `PaydayInference.train()` — gera as séries de liquidez sintéticas, faz o split **por cliente** (nunca por janela, para não vazar o padrão individual), treina LSTM + os 3 Prophets e salva tudo em `crai/models/`. Métricas do último treino: MAE de **0,60 dia** vs 5,32 da heurística de dias fixos; acerto da janela com ±1 dia em **90,6%** dos casos (ROC-AUC diário 0.979).

### 4. Offer Selector (`churn_voluntary/offer_bandit.py`)

**Thompson Sampling** (Multi-Armed Bandit Beta-Bernoulli) para seleção de ofertas de retenção.

- 5 braços: desconto 10%, desconto 20%, pausa 1 mês, consulta CS, Pix/Boleto Flash
- Um posterior Beta(α,β) por par (perfil, oferta) — a exploração decai sozinha, sem epsilon
- **Otimiza e-Profit, não conversão**: `argmax p_amostrado × LTV_retido − custo(oferta)` — no CLT, a consulta CS converte mais (50%) mas perde para o desconto de 10% por causa do custo humano
- Aprendizado contínuo: cada aceite/recusa real atualiza o posterior e persiste em disco

Os posteriores nascem do warm start da simulação de 6.000 rodadas e evoluem em produção a cada aceite/recusa, persistidos em `crai/models/bandit_state.json`. Na simulação: **+R$199 mil** vs o epsilon-greedy anterior, regret 40% menor, 87% de escolhas ótimas ao final.

### 5. Pix Automático — cobrança e retentativa regulada (`integrations/` + `dunning/`)

O sistema de cobrança novo é **exclusivamente Pix Automático**. Cartão continua entrando pelo webhook do Stripe, mas os dois caminhos são isolados no grafo (ver abaixo).

**Camada de adaptação de PSP** (`integrations/payment_gateway.py`): a interface `PaymentGatewayAdapter` normaliza o evento de qualquer PSP para um schema fechado, o que permite trocar Iugu por Pagar.me sem tocar em `agent/` nem em `ml/`. O `parse_card_event` existe na interface mas levanta `NotImplementedError` — cartão é roadmap futuro.

O schema normalizado tem **exatamente cinco campos**:

```
e2e_id | valor | status | ispb_pagador | id_recorrencia
```

A **chave Pix do pagador** (CPF/telefone/e-mail) nunca entra nele. Quando precisa ser guardada para conciliação, vai cifrada com Fernet (`security/tokenization.py`) para um arquivo segregado, sem nenhum caminho de código até `ml/` ou `agent/`. Sem `CRAI_ENCRYPTION_KEY` no ambiente, o arquivamento **falha** em vez de gravar em texto puro.

**Retentativa regulada** (`dunning/pix_automatico_retry.py`): o Pix Automático não admite backoff livre. O BACEN define que, no dia do vencimento, há duas janelas automáticas geridas pelo PSP do pagador (00h-08h e 18h-21h, sem ação da CRAI); se ambas falharem, o recebedor tem direito a **no máximo 3 novas tentativas em 7 dias corridos**, cada uma pelo **valor original**.

Dentro dessas restrições sobra uma decisão de otimização — *quando* usar as 3 tentativas — e é aí que o Módulo 3 entra:

| Situação | Estratégia |
|----------|-----------|
| Previsão de liquidez com confiança ≥ 0.60 e dentro da janela | Tentativas concentradas a partir do dia previsto |
| Previsão fraca, cliente novo, ou liquidez só depois do prazo | Fallback uniforme pelos dias restantes (mínimo 1 dia de intervalo) |

Violar qualquer um dos três limites levanta `PixRetryPolicyViolation` — a política falha alto em vez de corrigir em silêncio. Cada agendamento é logado com prefixo `[PIX-RETRY]`, indicando qual das duas estratégias foi usada.

#### Decisão de arquitetura: recobrança de cartão fora do pipeline ativo

As duas políticas de retentativa são **incompatíveis por natureza**:

| | Cartão | Pix Automático |
|---|--------|----------------|
| Teto de tentativas | nenhum | 3, por lei |
| Espaçamento | backoff exponencial + jitter | dentro de 7 dias corridos |
| Valor | livre | sempre o original |

Manter as duas vivas no mesmo grafo significaria sustentar indefinidamente uma superfície onde um erro de roteamento aplicaria a política errada — e o erro na direção cartão → Pix é uma **violação regulatória**, não um bug de conveniência.

Como o sistema de cobrança desta fase é exclusivamente Pix Automático e o cartão **não está em uso real**, a recobrança automática de cartão foi retirada do pipeline ativo. O grafo tem hoje um único caminho de retentativa:

```
decide_recovery ─┬─ payment_method == "pix_automatico" → schedule_retry_pix  (3 tentativas / 7 dias)
                 └─ qualquer outro caso               → trigger_dunning
```

**O que isso não é:** não é remoção de código. `crai/dunning/legacy_card/` preserva `smart_backoff.py` e o nó `schedule_retry_card` íntegros, com o caminho de reativação documentado no `__init__.py` do pacote. O campo `payment_method` continua no `AgentState` e a aresta condicional continua lendo ele — é exatamente onde um nó de cartão volta a ser plugado.

**O que continua funcionando:** `/webhooks/stripe` segue no ar com a validação de assinatura HMAC da Fase 1. Toda falha de cartão é recebida e **registrada** com o prefixo `[CARTAO-DESATIVADO]`, nunca descartada em silêncio:

```
[CARTAO-DESATIVADO] cus_joao_002 | fatura inv_cus_joao_002 | R$ 149.00 — evento
registrado, recobrança automática de cartão fora do pipeline ativo
(aguardando reimplementação; ver crai/dunning/legacy_card/)
```

`tests/test_payment_isolation.py` trava esse estado: valida que o evento de cartão é registrado e logado sem retentativa, que o grafo não tem mais nó de cartão, que nenhum módulo ativo importa `SmartBackoff`, e que o código isolado continua funcional.

### 6. Agente LangGraph (`agent/` + `dunning/`)

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
│   │   └── synthetic_data.py           # 3 geradores de dataset sintético (seed 42)
│   │
│   ├── scripts/                        # Scripts executáveis do pacote
│   │   └── train_all.py                # Treina classifier + anomaly + payday
│   │
│   ├── dunning/                        # Motor de cobrança inteligente
│   │   ├── dunning_engine.py           # LangGraph + Claude API (multicanal)
│   │   ├── pix_automatico_retry.py     # Janela regulada BACEN (3 tentativas / 7 dias)
│   │   └── legacy_card/                # Cartão: preservado, fora do fluxo ativo
│   │       ├── smart_backoff.py        # Backoff exponencial + jitter
│   │       └── card_retry.py           # Nó de retentativa de cartão (inativo)
│   │
│   ├── integrations/                   # Integrações externas
│   │   ├── payment_gateway.py          # Adapter de PSP → schema normalizado de Pix
│   │   └── hubspot_crm.py             # CRM com 2 pipelines (recovery + retention)
│   │
│   ├── security/                       # Fronteira de confiança
│   │   ├── webhook_verification.py     # HMAC dos 3 webhooks
│   │   └── tokenization.py             # Fernet para campos sensíveis
│   │
│   └── api/                            # API REST
│       └── app.py                      # FastAPI: webhooks + simulação
│
├── models/                             # Artefatos treinados (gerados por train_all)
│
├── tests/
│   ├── test_failure_classifier.py      # Módulo 1: dataset, treino, SHAP, e-Profit
│   ├── test_anomaly_detector.py        # Módulo 2: dataset, treino, persistência
│   ├── test_payday_inference.py        # Módulo 3: séries, treino, persistência
│   ├── test_agent_graph.py             # Módulo 5: roteamento do grafo + dunning
│   ├── test_webhook_security.py        # HMAC dos webhooks + restrição de ENV
│   ├── test_payment_gateway.py         # Adapter de Pix + privacidade da chave
│   ├── test_pix_automatico_retry.py    # Limites do BACEN na retentativa
│   └── test_payment_isolation.py       # Cartão nunca usa política de Pix (e vice-versa)
│
├── test_pipeline.py                    # Demo: 8 cenários (4 involuntário + 4 voluntário)
├── requirements.txt                    # Dependências com versões fixadas
└── .env.example                        # Template de variáveis de ambiente
```

> A pasta [`archive/protótipos-pré-unificação/`](archive/prot%C3%B3tipos-pr%C3%A9-unifica%C3%A7%C3%A3o/) guarda os laboratórios originais dos módulos 2, 3 e 4 (relatórios, figuras e métricas usados no TCC). A lógica de treino deles já vive no pacote `crai/` — as pastas ficam só como histórico.

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
| `ENV` | Para `/simulate/*` | `development` ou `demo` habilitam os endpoints de teste; qualquer outro valor (ou ausente) faz eles responderem 403 |
| `ANTHROPIC_API_KEY` | Para LLM | Mensagens personalizadas via Claude API |
| `STRIPE_SECRET_KEY` | Não | Modo simulação funciona sem |
| `STRIPE_WEBHOOK_SECRET` | Para `/webhooks/stripe` | Valida o header `stripe-signature` (HMAC-SHA256). Sem ele o endpoint rejeita tudo com 401 |
| `PIX_WEBHOOK_SECRET` | Para `/webhooks/pix-automatico` | Valida o header `x-pix-signature` (HMAC-SHA256). Sem ele o endpoint rejeita tudo com 401 |
| `CRAI_ENCRYPTION_KEY` | Para arquivar chave Pix | Chave Fernet para cifrar a chave Pix do pagador. Sem ela o arquivamento falha em vez de gravar em texto puro |
| `CRAI_PAGARME_LIVE` | Não | `1` liga o envio REAL de instrução de cobrança ao Pagar.me. Sem ela, `reenviar_cobranca_pix` roda em modo simulado — nenhuma rede, nenhuma credencial |
| `CRAI_PAGARME_API_KEY` | Com `CRAI_PAGARME_LIVE=1` | Secret key do Pagar.me (Basic auth, senha vazia). Ligar o modo real sem ela **falha alto**, em vez de cair para simulado em silêncio |
| `CRAI_PAGARME_ENDPOINT` | Não | Caminho da cobrança avulsa sobre a recorrência de Pix Automático. O default é um placeholder marcado `TODO(integração)` — o valor real depende da conta |
| `CRAI_RETRY_STATE` | Não | Redireciona o arquivo de planos de retentativa pendentes (default `crai/data/pix_retry_state.json`). A suíte usa isto para não escrever no estado real |
| `HUBSPOT_TOKEN` | Não | CRM roda em modo simulação sem token |
| `SEGMENT_WRITE_KEY` | Não | Simulação via `/simulate/churn-risk` |
| `SEGMENT_WEBHOOK_SECRET` | Para `/webhooks/segment` | Valida o header `x-signature` (HMAC-SHA1). Sem ele o endpoint rejeita tudo com 401 |

> Os módulos de ML e o pipeline funcionam **sem nenhuma API key** — fallbacks heurísticos e templates estáticos substituem as chamadas externas. Os *secrets de webhook* são a exceção: sem eles os endpoints `/webhooks/*` rejeitam qualquer request (fail closed), por design. Para rodar local sem webhook real, use `/simulate/*` com `ENV=development`.

### Higiene de segredos (hook de pre-commit)

O `.env` real **nunca** deve ser commitado. Além do `.gitignore`, o repositório traz um hook opcional em `scripts/pre-commit` que bloqueia o commit quando detecta:

- qualquer arquivo `.env` / `.env.*` estagiado (exceto `.env.example`);
- valores que se pareçam com chaves reais (`sk-ant-`, `sk_live_`, `whsec_`, `pat-na1-`, `AKIA…`, `ghp_…`) no conteúdo estagiado.

Ativação local (uma vez, na raiz do repositório):

```bash
git config core.hooksPath scripts
```

Verificar se ficou ativo:

```bash
git config --get core.hooksPath   # deve imprimir: scripts
```

O hook vale para todo o repositório, incluindo `archive/`. Ele não roda automaticamente para quem clona o repositório — cada pessoa precisa executar o comando acima. Em caso de falso positivo, o bypass pontual é `git commit --no-verify` (usar com parcimônia).

---

## Como Executar

### Treinar os três modelos de ML

```bash
cd crai
python -m crai.scripts.train_all
```

Comando único que treina, em sequência, os três modelos treináveis do pacote:

| # | Modelo | Dataset sintético | Artefatos em `crai/models/` |
|---|--------|-------------------|------------------------------|
| 1 | Failure Classifier (XGBoost + RF) | 3.000 transações | `xgb_*.joblib`, `rf_*.joblib`, `label_encoders.joblib` |
| 2 | Anomaly Detector (Autoencoder) | 5.500 clientes (91% saudáveis) | `autoencoder.pt`, `autoencoder_scaler.pkl`, `autoencoder_meta.json` |
| 3 | Payday Inference (LSTM + Prophet) | 600 clientes × 180 dias | `payday_lstm.pt`, `payday_prophet_*.json`, `payday_meta.json` |

Ao final imprime um resumo das métricas dos três e confirma que cada modelo salvo é recarregável via `load()` — é essa verificação que garante que o agente LangGraph vai encontrar os artefatos no formato esperado. Leva cerca de 1 minuto em CPU.

Para um smoke test rápido (amostras reduzidas):

```bash
python -m crai.scripts.train_all --quick
```

Cada modelo também pode ser treinado isoladamente:

```python
from crai.ml.anomaly_detector import AnomalyDetector
metrics = AnomalyDetector().train(n_samples=5500)
```

Sem os artefatos treinados, **todos os módulos continuam funcionando** com os fallbacks heurísticos — o treino melhora a qualidade da decisão, não é pré-requisito para rodar.

### Rodar testes

```bash
cd crai
pytest tests/ -v
```

Cobrem: os três datasets sintéticos, os fallbacks heurísticos, o treino de cada modelo (em amostras pequenas), a persistência (`train()` → `load()`), SHAP, e-Profit e o roteamento do grafo do agente.

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
# Churn involuntário — cartão
curl -X POST http://localhost:8000/simulate/payment-failed \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"cus_teste","amount":299.90,"failure_code":"insufficient_funds"}'

# Churn involuntário — Pix Automático (cobrança recorrente falhada)
curl -X POST http://localhost:8000/simulate/pix-falhado \
  -H "Content-Type: application/json" \
  -d '{"id_recorrencia":"RN_teste","valor":299.90,"ispb_pagador":"60701190"}'

# Churn involuntário — confirmação de pagamento (fecha o ciclo e conta o fee)
curl -X POST http://localhost:8000/simulate/pix-pago \
  -H "Content-Type: application/json" \
  -d '{"id_recorrencia":"RN_teste"}'

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

## Métricas dos Modelos

Resultado de `python -m crai.scripts.train_all` com os tamanhos de dataset padrão.

**Módulo 1 — Failure Classifier** (XGBoost 70% + Random Forest 30%)

| Métrica | Valor |
|---------|-------|
| AUC-ROC | 0.680 |
| Acurácia | 0.640 |
| Recall (recuperado) | 0.629 |
| e-Profit médio | R$ 404,93 |
| Clientes com e-Profit > 0 | 100% (teste) |

**Módulo 2 — Anomaly Detector** (Autoencoder, treino só em clientes saudáveis)

| Métrica | Valor |
|---------|-------|
| ROC-AUC | 0.995 |
| Average Precision | 0.991 |
| Precision @ threshold p95 | 0.928 |
| Recall @ threshold p95 | 0.984 |
| Separação saudáveis vs anômalos | 7,7x |

**Módulo 3 — Payday Inference** (LSTM + Prophet, split por cliente)

| Métrica | Ensemble | Heurística de dias fixos |
|---------|----------|--------------------------|
| MAE da janela de retry | **0,60 dia** | 5,32 dias |
| Acerto exato | 84,3% | — |
| Acerto ±1 dia | 90,6% | — |
| ROC-AUC diário | 0.979 | — |

Por perfil (MAE heurística → ensemble): CLT 6,24 → **0,20** | PJ 3,47 → **1,10** | freelancer 4,68 → **1,21**

*Todos treinados em dataset sintético com seed fixa (42) para reprodutibilidade.*

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
  - [x] Threshold calibrado no percentil 95 dos saudáveis held-out (ROC-AUC 0.995, recall 98,4%)
  - [x] Explicabilidade: quebra do erro de reconstrução por feature
  - [x] Integração ao pipeline LangGraph com fallback heurístico
- [x] **Módulo 3** — LSTM + Prophet (payday_inference.py)
  - [x] Séries de liquidez sintéticas (600 clientes × 180 dias, 3 perfis BR)
  - [x] LiquidityLSTM seq2vec (30 dias → 14 dias) com split por cliente
  - [x] Prophet por perfil com sazonalidade mensal (payday brasileiro)
  - [x] Ensemble 0.6/0.4 — MAE 0,60 dia vs 5,32 da heurística (hit ±1d: 90,6%)
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
- [x] **Pix Automático** — cobrança regulada e isolamento de meios de pagamento
  - [x] `PaymentGatewayAdapter` (ABC) + `PixAutomaticoAdapter` — troca de PSP sem tocar no pipeline
  - [x] Schema normalizado de 5 campos, sem a chave Pix do pagador
  - [x] Chave Pix cifrada com Fernet, em arquivo segregado de `ml/` e `agent/`
  - [x] `PixAutomaticoRetryPolicy` — 3 tentativas / 7 dias, valor original, com o Payday Engine escolhendo os dias
  - [x] Endpoint `/webhooks/pix-automatico` com HMAC obrigatório
  - [x] Recobrança automática de cartão retirada do pipeline ativo e isolada em `dunning/legacy_card/`
  - [x] `/webhooks/stripe` mantido, registrando as falhas com `[CARTAO-DESATIVADO]`
  - [x] `parse_card_event` reservado para o roadmap — cartão não implementado nesta fase
  - [x] Demo reprodutível: `hashlib.md5` no lugar do `hash()` randomizado por processo
- [x] **Ciclo de recuperação fechado** — confirmação real de pagamento (Sprint 1)
  - [x] Status de cobrança confirmada do PSP fecha o ciclo: `recovered=True`, success fee no `[ROI]`, estágio `recovered` no CRM
  - [x] Fechamento **fora do grafo** (`_fechar_ciclo_recuperado`) — reprocessar o pipeline agendaria tentativas do BACEN contra quem acabou de pagar
  - [x] Fee só onde houve recuperação: confirmação sem ciclo aberto é mensalidade normal, não recuperação
  - [x] Idempotência dos **dois** lados (janela de 7 dias, `api/idempotencia.py`): reenvio da confirmação não fatura duas vezes; reenvio da falha não reexecuta o pipeline
  - [x] `/simulate/pix-pago` — a banca vê o loop inteiro (falha → agendamento → confirmação → fee) sem PSP real
  - ⚠️ A janela de idempotência é memória de processo: reinício a esquece e dois processos têm janelas separadas — mesma dependência de DB do `MemorySaver` (P1-14)
- [x] **Execução da retentativa** — o agente deixa de só agendar (Sprint 2)
  - [x] `integrations/pagarme_gateway.py` — a SAÍDA para o PSP (`reenviar_cobranca_pix`), espelhando o adapter de entrada
  - [x] Modo simulado por default (sem rede, sem credencial); modo real com `CRAI_PAGARME_LIVE=1` + chave, que **falha alto** se a chave faltar
  - [x] Invariante de valor na última linha antes do dinheiro: valor != original levanta `PixRetryPolicyViolation`
  - [x] `dunning/retry_scheduler.py` — as tentativas 2 e 3 do BACEN (Gap 1). Em produção um cron chama `processar_tentativas_devidas()`; o relógio é injetado, e é o que torna a regra testável
  - [x] `dunning/retry_state.py` — o plano pendente atrás de `get_retry_state`/`save_retry_state` (Gap 2): ponto de troca pronto para o PostgreSQL
  - [x] Falha do PSP não consome tentativa: a marca de disparo só é gravada depois do aceite
  - ⚠️ **Limitação assumida (Gap 2):** o contador do BACEN segue no `MemorySaver` e o plano num JSON reescrito inteiro. Um processo, sem transação — reinício e segundo worker continuam sendo a dependência de banco já descrita em P1-14. O que muda com o DB é a implementação por trás dessas duas funções, não o nó do grafo nem o agendador
  - ⚠️ **Fora do escopo declarado:** o scheduler temporal de produção (cron/worker) é infraestrutura. O que existe aqui é a LÓGICA de disparo, completa e testável, mais o ponto onde o cron chama
- [x] **Diagnóstico real de falha de Pix** — o PIX_CODE_MAP (Sprint 3)
  - [x] `agent/pix_codes.py` — quatro causas internas (`insufficient_funds`, `limit_exceeded`, `authorization_revoked`, `processing_error`) com aliases textuais dos PSPs e códigos ISO 20022 do arranjo Pix
  - [x] Schema normalizado ganhou `codigo_falha` de forma **aditiva** — os cinco campos anteriores intactos, e a chave Pix continua fora
  - [x] `_features_pix` deixou de atribuir `insufficient_funds` a toda falha: a feature do classificador era constante, e o dataset de treino nasceria sem sinal
  - [x] `limit_exceeded` e `authorization_revoked` **não** consomem tentativa do BACEN — retentar não pode dar certo nos dois, e a tentativa é um direito do recebedor
  - [x] Templates de dunning próprios para as duas causas (aumentar o limite / reautorizar), e autorização revogada não oferece Pix Automático — é o canal que o cliente acabou de fechar
  - [x] Default seguro `processing_error` para código não reconhecido: não afirma falta de saldo sobre um pagador de quem não se sabe nada
  - ⚠️ **TODO(integração):** a lista de códigos ISO 20022 precisa ser conferida contra a documentação da conta Pagar.me antes de `CRAI_PAGARME_LIVE=1`. Código fora do mapa cai no default e sai no log — é assim que se descobre o que falta
- [ ] **Cartão** — reimplementar a recobrança automática (ver `dunning/legacy_card/`)
- [x] **Consolidação** — treino real dentro do pacote principal
  - [x] `train()` em `anomaly_detector.py` e `payday_inference.py` (antes só tinham `load()`)
  - [x] Geradores de dataset dos módulos 2 e 3 portados para `ml/synthetic_data.py`
  - [x] Comando único de treino: `python -m crai.scripts.train_all`
  - [x] Protótipos movidos para `archive/protótipos-pré-unificação/` (histórico)
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

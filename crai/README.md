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

Itens levantados nas auditorias adversariais A1 e suas re-rodadas (r2 a r5) dos sprints de 31/08–04/09 e **deliberadamente
não corrigidos antes da demo**, por serem risco maior que ganho nessa janela.
Estão aqui para não virarem dívida esquecida.

| ID | Onde | O quê | Por que ficou |
|---|---|---|---|
| **P2-10** | `agent/workflow.py::infer_payday` | O Payday Engine roda **2× por recuperação**: uma no nó `infer_payday` e outra dentro de `PixAutomaticoRetryPolicy._consultar_payday`. Além disso, os campos que o nó grava (`optimal_retry_at`, `confidence`, `profile_type`) **não são lidos por nenhum nó ativo** — a política consulta o modelo por conta própria. | O ganho é performance; o risco é quebrar o caminho feliz da demo. Unificar depois: ou a política recebe a previsão pelo state, ou o nó deixa de existir. |
| **P2-11** | `integrations/hubspot_crm.py` | Duplica inline o digest md5 estável de `seed_por_cliente` (`ml/synthetic_data.py`) em vez de importá-lo. Mesma decisão de projeto escrita em dois lugares. | Baixo risco, mas toca o id que aparece na demo. |
| **P2-13** | `integrations/payment_gateway.py::store_encrypted_pix_key` | Read-modify-write no cofre de chaves Pix sem lock. | Não é chamado no pipeline ativo hoje. Vira P0 no dia em que a conciliação entrar em produção. |
| — | `ml/synthetic_data.py` | `tenure_months` e a probabilidade-base de recuperação (`p = 0.5`) continuam **não calibrados** — não existe fonte pública para nenhum dos dois. | Declarado em `docs/DATA_CARD.md`, seções 5 e 6. É limitação assumida, não descuido. |
| **N-8** | `agent/workflow.py` | `float(confidence)` fica **fora** do `try` que protege a chamada do Módulo 3: se o modelo devolver algo não numérico em `confidence`, a exceção escapa. | Não dispara com os modelos atuais. **Vira P0 no Sprint 4**, que é o próximo a mexer no retorno do Módulo 3. |
| **N-12** | `test_pipeline.py` (15×), `crai/agent/workflow.py:243`, `crai/churn_voluntary/voluntary_agent.py:118` (2×), `crai/dunning/dunning_engine.py:109`, `crai/dunning/pix_automatico_retry.py:355`, `crai/ml/anomaly_detector.py:145` (2×) | `UnicodeEncodeError` no console padrão do Windows (cp1252). **22 ocorrências restantes** em strings que chegam ao stdout — `→` (U+2192) e os emoji `✅`/`❌` (U+2705/U+274C). Medido com `tokenize`, contando só literais fora de docstring, a partir da **raiz do repositório**. | As 22 **são** pré-existentes: `baseline-pre-sprint` tem exatamente as mesmas 22, arquivo por arquivo — a contribuição líquida deste diff é **zero**. Outras 5 que este diff havia introduzido (`ml/failure_classifier.py:250` por `4109d84`, `integrations/payment_gateway.py:562` e 3 em `scripts/preparar_amostra_real.py`) já foram corrigidas neste sprint. **Correção da auditoria A1-r5:** a linha anterior desta tabela dizia "7 ocorrências" — era a contagem só do pacote `crai/`, e `tests/test_encoding_saida.py` prometia cobrir "qualquer módulo" varrendo só ele. As 15 de `test_pipeline.py`, fora do pacote, ficavam invisíveis. A catraca agora varre a raiz e declara as 22. ⚠️ Dois pontos de morte conhecidos: **`anomaly_detector.py:145` derruba `python -m crai.scripts.train_all`** (gate do Sprint 4) e **`test_pipeline.py:103` derruba `python test_pipeline.py`** (primeiro comando deste README). Até o Sprint 5, rode ambos com `PYTHONIOENCODING=utf-8`. A solução sistêmica (`sys.stdout.reconfigure`) é do `demo_runner.py` no Sprint 5, de que o gate GA2 depende. |
| **P1-14** | `agent/main_agent.py::build_crai_graph` | O contador de tentativas do BACEN vive num `MemorySaver` — **RAM, por processo**. Dois workers do uvicorn são duas memórias: o mesmo `id_recorrencia` atendido por workers diferentes ganha 3 tentativas de cada um, **6 na mesma janela** contra o limite legal de 3. Reiniciar o serviço zera o contador de toda a base, e a janela do BACEN dura 7 dias. | Dívida **assumida**, não descuido: a POC roda em processo único, e é isso que garante o limite hoje. O conserto é trocar o checkpointer por `SqliteSaver`/`PostgresSaver` (mesma interface do LangGraph) mais a migração — trabalho de produção. A consequência está escrita no ponto do código onde o `MemorySaver` é criado, para que ninguém suba um segundo worker sem ler. |
| **N-13** | `agent/workflow.py::schedule_retry_pix` | `retry_exhausted = True` é **inalcançável** no caminho de Pix: `decide_recovery` só roteia para o nó de retentativa quando ainda cabem tentativas, e nesse caso a política nunca devolve lista vazia. O cenário "Janela esgotada" imprime `0/3` em vez do esgotamento real. | Pré-existente — verificado idêntico em `baseline-pre-sprint`. É um ramo defensivo morto, não um erro de cálculo: o limite continua correto, só o rótulo da demo fica errado. **Fecha no Sprint 5**, que monta os cenários da banca e precisa deste exato caso na tela. |
| **N-14** | `api/app.py::_thread_id` | Um pagador anônimo (`id_recorrencia` vazio) recebe um `thread_id` derivado do próprio evento, que muda a cada evento. Três eventos anônimos do mesmo cliente real viram três checkpoints e **9 tentativas** na mesma janela. | O 422 do P0-6 já recusa o evento que não identifica ninguém; o que sobra é o caso em que o PSP manda `e2e_id` mas não `id_recorrencia` — a CRAI não tem como saber que os três são a mesma pessoa. Não é resolvível dentro do payload: exige conciliação por chave Pix, que é justamente o que o `P2-13` destrava. |
| **N-6 (resíduo)** | `integrations/payment_gateway.py::store_encrypted_pix_key` | Descarta a lista `degradacoes` que recebe, em vez de registrá-la. | Fora do caminho do pipeline ativo, como o P2-13. |
| **§4.6** | `crai/models/` | Os binários em disco são de um treino anterior: **AUC 0,6797, abaixo do piso [0,70; 0,92]** dos gates G3/G4, e `train_metrics.json` com schema anterior ao commit `4109d84`. | **Fecha no Sprint 4**, que retreina os três módulos com os 15.000 do Sprint 3. Até lá, a demo carrega modelo que não passa no próprio gate. |

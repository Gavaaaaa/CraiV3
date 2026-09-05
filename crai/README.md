# CRAI v2 — POC Completa

Agente autônomo de recuperação de receita: churn involuntário + churn voluntário + CRM automático.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edite .env — só ANTHROPIC_API_KEY é necessária para mensagens reais via Claude

python test_pipeline.py
```

O `test_pipeline.py` roda **9 cenários** sem precisar de Stripe, Segment ou HubSpot reais: **3** de Pix Automático (janela do BACEN em 0, 2 e 3 tentativas usadas), **2** de cartão — que hoje só são registrados, ver a Fase 3 — e **4** de churn voluntário.

> ⚠️ Num console do Windows (cp1252) este comando morre na linha 103 com `UnicodeEncodeError`. Rode `PYTHONIOENCODING=utf-8 python test_pipeline.py` até o Sprint 5 fechar o `N-12`.

## Subir a API

```bash
uvicorn crai.api.app:app --reload
# http://localhost:8000/docs
```

### Testar churn involuntário (Pix Automático)

```bash
curl -X POST http://localhost:8000/simulate/pix-falhado \
  -H "Content-Type: application/json" \
  -d '{"id_recorrencia":"RN_teste_001","valor":299.90,"ispb_pagador":"60701190"}'
```

Devolve `{"status":"pipeline_executado", ...}` — é este que aciona o agente.

O endpoint de cartão continua no ar, mas **não roda pipeline**: desde a Fase 3
a recobrança automática de cartão está fora do fluxo ativo, e ele responde
`{"status":"registrado","pipeline":false}`.

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
├── agent/                          # Churn involuntário — grafo principal
│   ├── main_agent.py                # Grafo LangGraph + MemorySaver (checkpoint por cliente)
│   ├── workflow.py                  # Nós: diagnose, anomaly, payday, decide, retry, dunning, CRM
│   └── state.py                     # AgentState — inclui retry_count e pix_janela_ate
├── churn_voluntary/                 # Churn voluntário
│   ├── voluntary_agent.py           # Grafo LangGraph: risk -> offer -> channel -> message -> CRM
│   ├── offer_bandit.py              # Multi-Armed Bandit — melhor oferta por perfil
│   ├── risk_scorer.py               # risk_score a partir de eventos Segment
│   └── state.py
├── ml/
│   ├── failure_classifier.py        # XGBoost + Random Forest — train() + predict() + SHAP
│   ├── anomaly_detector.py          # Autoencoder PyTorch — train() + check()
│   ├── payday_inference.py          # LSTM + Prophet — train() + predict_next_window()
│   └── synthetic_data.py            # Geradores dos 3 datasets (seed 42)
├── dunning/
│   ├── pix_automatico_retry.py      # Política do BACEN: 3 tentativas / janela de 7 dias
│   ├── dunning_engine.py            # LangGraph + Claude API (mensagem personalizada)
│   └── legacy_card/                 # Recobrança de cartão — FORA do pipeline ativo (Fase 3)
│       ├── card_retry.py            #   preservada para reativação, não importada
│       └── smart_backoff.py         #   backoff exponencial + jitter (política de cartão)
├── integrations/
│   ├── payment_gateway.py           # Adapter de Pix Automático: normaliza e recusa payload torto
│   └── hubspot_crm.py               # CRM — 2 pipelines: recovery + retention
├── security/
│   ├── webhook_verification.py      # HMAC dos 3 webhooks + janela anti-replay
│   └── tokenization.py              # Tokenização de dados sensíveis
├── api/
│   └── app.py                       # FastAPI: webhooks Pix + Stripe + Segment,
│                                    #   blindagem de forma da borda, trava por cliente,
│                                    #   endpoints /simulate/*
├── scripts/
│   ├── train_all.py                 # Treina os 3 modelos: python -m crai.scripts.train_all
│   └── preparar_amostra_real.py     # Amostra real -> parâmetros de calibração
├── tests/                           # a suíte (rode `pytest tests/ -q` para a contagem)
├── test_pipeline.py                 # Demo dos 9 cenários (rode com PYTHONIOENCODING=utf-8)
└── requirements.txt
```

> Conferido com `find crai -name "*.py"` na correção da auditoria A1-r8. A
> versão anterior deste bloco listava `dunning/smart_backoff.py`, que não
> existe nesse caminho desde a Fase 3, e omitia seis módulos — entre eles
> `pix_automatico_retry.py` e `payment_gateway.py`, que são o coração da
> janela do BACEN e da blindagem da borda.

## Os dois pipelines

### 1. Churn Involuntário (pagamento falhou)

```
Webhook de Pix Automático (assinado)
    ↓
XGBoost + RF (causa raiz, e-Profit, SHAP) → Autoencoder (anomalia) → LSTM+Prophet (payday)
    ↓
Módulo 5 (ReAct: retentar ou contatar?)
    ↓
PixAutomaticoRetryPolicy — 3 tentativas por janela de 7 dias (BACEN)
    ↓
[janela esgotada?] → Claude API (mensagem personalizada) → HubSpot
```

O webhook do Stripe **não entra neste grafo**. Desde a Fase 3 ele apenas
registra o evento com o prefixo `[CARTAO-DESATIVADO]` e responde
`pipeline: false` — nenhum nó roda. `smart_backoff.py`, a política de cartão,
está preservada em `dunning/legacy_card/` e não é importada por nada no
caminho ativo: aplicar backoff exponencial a uma cobrança Pix violaria o
limite do BACEN.

O campo `payment_method` do state e a aresta condicional em `main_agent.py`
continuam existindo, e é por eles que o cartão volta ao fluxo quando for
reimplementado — mas hoje nenhum evento de cartão chega até lá.

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

Itens levantados nas auditorias adversariais A1 e suas re-rodadas (r2 a r9) dos sprints de 31/08–05/09 e **deliberadamente
não corrigidos antes da demo**, por serem risco maior que ganho nessa janela.
Estão aqui para não virarem dívida esquecida.

| ID | Onde | O quê | Por que ficou |
|---|---|---|---|
| **P2-10** | `agent/workflow.py::infer_payday` | O Payday Engine roda **2× por recuperação**: uma no nó `infer_payday` e outra dentro de `PixAutomaticoRetryPolicy._consultar_payday`. Além disso, os campos que o nó grava (`optimal_retry_at`, `confidence`, `profile_type`) **não são lidos por nenhum nó ativo** — a política consulta o modelo por conta própria. | O ganho é performance; o risco é quebrar o caminho feliz da demo. Unificar depois: ou a política recebe a previsão pelo state, ou o nó deixa de existir. |
| **P2-11** | `integrations/hubspot_crm.py` | Duplica inline o digest md5 estável de `seed_por_cliente` (`ml/synthetic_data.py`) em vez de importá-lo. Mesma decisão de projeto escrita em dois lugares. | Baixo risco, mas toca o id que aparece na demo. |
| **P2-13** | `integrations/payment_gateway.py::store_encrypted_pix_key` | Read-modify-write no cofre de chaves Pix sem lock. | Não é chamado no pipeline ativo hoje. Vira P0 no dia em que a conciliação entrar em produção. |
| — | `ml/synthetic_data.py` | `tenure_months` e a probabilidade-base de recuperação (`p = 0.5`) continuam **não calibrados** — não existe fonte pública para nenhum dos dois. | Declarado em `docs/DATA_CARD.md`, seções 5 e 6. É limitação assumida, não descuido. |
| **N-8** | `agent/workflow.py` | `float(confidence)` fica **fora** do `try` que protege a chamada do Módulo 3: se o modelo devolver algo não numérico em `confidence`, a exceção escapa. | Não dispara com os modelos atuais. **Vira P0 no Sprint 4**, que é o próximo a mexer no retorno do Módulo 3. |
| **N-12** | `crai/test_pipeline.py` (15×), `crai/crai/agent/workflow.py:249`, `crai/crai/churn_voluntary/voluntary_agent.py:118` (2×), `crai/crai/dunning/dunning_engine.py:109`, `crai/crai/dunning/pix_automatico_retry.py:355`, `crai/crai/ml/anomaly_detector.py:145` (2×), `archive/.../modulo_04_offer_bandit/src/visualizar.py:119` | `UnicodeEncodeError` no console padrão do Windows (cp1252). **23 ocorrências restantes** em strings que chegam ao stdout — `→` (U+2192) e os emoji `✅`/`❌` (U+2705/U+274C). Medido com `tokenize`, contando só literais fora de docstring, a partir da raiz do repositório (`D:/PTI`, a pasta que contém `.git`). | As 23 **são** pré-existentes: `baseline-pre-sprint` tem exatamente as mesmas 23, arquivo por arquivo — a contribuição líquida destes sprints é **zero**. Outras 5 que o sprint havia introduzido (`ml/failure_classifier.py:250` por `4109d84`, `integrations/payment_gateway.py:562` e 3 em `scripts/preparar_amostra_real.py`) já foram corrigidas. **Correção da auditoria A1-r6:** esta linha dizia **22** e dizia medir "a partir da raiz do repositório" — mas a varredura começava em `D:/PTI/crai`, um nível abaixo, deixando `archive/` de fora. É a terceira rodada em que a catraca afirma um escopo maior do que mede (r4: só o pacote; r5: só `crai/`), então agora existe uma asserção em vez de um comentário: `test_a_raiz_e_mesmo_a_raiz_do_repositorio` exige que a raiz contenha `.git`. O ponteiro de `workflow.py` também estava errado — 243, medido 249. ⚠️ Dois pontos de morte conhecidos: **`anomaly_detector.py:145` derruba `python -m crai.scripts.train_all`** (gate do Sprint 4) e **`test_pipeline.py:103` derruba `python test_pipeline.py`** (primeiro comando deste README). Até o Sprint 5, rode ambos com `PYTHONIOENCODING=utf-8`. A solução sistêmica (`sys.stdout.reconfigure`) é do `demo_runner.py` no Sprint 5, de que o gate GA2 depende. |
| **P1-14** | `agent/main_agent.py::build_crai_graph` | O contador de tentativas do BACEN vive num `MemorySaver` — **RAM, por processo**. Dois workers do uvicorn são duas memórias: o mesmo `id_recorrencia` atendido por workers diferentes ganha 3 tentativas de cada um, **6 na mesma janela** contra o limite legal de 3. Reiniciar o serviço zera o contador de toda a base, e a janela do BACEN dura 7 dias. | Dívida **assumida**: o conserto é trocar o checkpointer por `SqliteSaver`/`PostgresSaver` (mesma interface do LangGraph) mais a migração — trabalho de produção. **Correção da auditoria A1-r6:** esta linha dizia que "a POC roda em processo único, e é isso que garante o limite hoje". Era falso. Dentro de **um** processo, N entregas simultâneas do mesmo `id_recorrencia` agendavam 3×N tentativas — medido 6 e 9, com o checkpoint registrando 3 e portanto escondendo o estouro. O que garante o limite hoje é a **trava por `thread_id`** (`P1-15`), não o processo. O que continua valendo desta linha é só o caso **entre** processos. |
| **P1-15** | `api/app.py::_trava_do_cliente` | A trava que serializa o pipeline por `id_recorrencia` é um `asyncio.Lock` em memória, válido dentro de **um event loop**. Ela fecha a corrida que a A1-r6 mediu (3×N tentativas por entregas simultâneas), e **só** essa. Dois processos continuam sem exclusão mútua entre si. | Está aqui declarada junto do `P1-14` de propósito: são o mesmo limite regulatório protegido em duas camadas diferentes, e a de fora não existe. Quem subir um segundo worker precisa das duas — checkpointer transacional **e** trava distribuída —, não de uma. |
| **N-15** | `api/app.py::_campo_com_forma` | A blindagem de forma dos webhooks valida os campos que o pipeline **consome hoje** (`userId`, `event`, `properties`, `billing_profile`, `data.object.amount_due`, `attempt_count`), não o grafo inteiro do payload. Um campo novo que o pipeline passe a ler sem entrar nessa lista volta a atravessar a borda sem checagem. | É contrato explícito, não descuido: validar tudo na borda exigiria um schema completo do payload de cada PSP, que é trabalho de integração real — declarado fora de escopo em `sprints.md`. Medido depois da correção: 396 requisições hostis (3 webhooks assinados + 3 `/simulate/*`, 18 valores por campo) devolveram `{200: 169, 400: 64, 422: 163}` e **zero 5xx**. A A1-r7 mostrou que a lista de campos blindados tinha de fato um buraco — `amount_due: 2**2000` estourava `OverflowError` na divisão por 100, porque a guarda de inteiro só era chamada no Segment. Fechado, e a guarda passou a ser iterativa: a versão recursiva era derrubável pelo próprio payload que inspeciona (`RecursionError` a partir de ~950 níveis). |
| **N-16** | `api/app.py::_identidade_voluntaria` | A identidade do churn voluntário é **qualificada pela origem**: `user:<userId>` ou `anon:<anonymousId>`. O prefixo aparece no nome do contato e do deal no HubSpot. | É o preço declarado de uma propriedade que o projeto precisa: os dois ids vêm de sistemas diferentes (o backend do cliente e o SDK do navegador) e **nada impede que coincidam**. Prefixar só o lado anônimo separava numa direção só — `userId="anon:v"` colidia com `anonymousId="v"`, e essa é a direção pior, porque o `anonymousId` é o campo que o visitante escolhe. Prefixar os dois é injetivo por construção. A tentativa de manter o CRM limpo guardando a desambiguação só no `thread_id` foi **pior**: a separação chegava ao checkpoint e não chegava a `_channel_history` nem ao HubSpot — medido na A1-r10, um cliente fora do site recebia popup porque herdou o histórico de um visitante anônimo homônimo, e os dois viravam o mesmo deal. Fundir duas pessoas no CRM é pior que um nome feio. |
| **N-13** | `agent/workflow.py::schedule_retry_pix` | `retry_exhausted = True` é **inalcançável** no caminho de Pix: `decide_recovery` só roteia para o nó de retentativa quando ainda cabem tentativas, e nesse caso a política nunca devolve lista vazia. O cenário "Janela esgotada" imprime `0/3` em vez do esgotamento real. | Pré-existente — verificado idêntico em `baseline-pre-sprint`. É um ramo defensivo morto, não um erro de cálculo: o limite continua correto, só o rótulo da demo fica errado. **Fecha no Sprint 5**, que monta os cenários da banca e precisa deste exato caso na tela. |
| **N-14** | `api/app.py::_thread_id` | Um pagador anônimo (`id_recorrencia` vazio) recebe um `thread_id` derivado do próprio evento, que muda a cada evento. Três eventos anônimos do mesmo cliente real viram três checkpoints e **9 tentativas** na mesma janela. | O 422 do P0-6 já recusa o evento que não identifica ninguém; o que sobra é o caso em que o PSP manda `e2e_id` mas não `id_recorrencia` — a CRAI não tem como saber que os três são a mesma pessoa. Não é resolvível dentro do payload: exige conciliação por chave Pix, que é justamente o que o `P2-13` destrava. |
| **N-6 (resíduo)** | `integrations/payment_gateway.py::store_encrypted_pix_key` | Descarta a lista `degradacoes` que recebe, em vez de registrá-la. | Fora do caminho do pipeline ativo, como o P2-13. |
| **§4.6** | `crai/models/` | **Só o Módulo 1 foi retreinado** (01/09, `n=15000`): **AUC 0,7029**, dentro da faixa [0,70; 0,92] dos gates G3/G4 — mas com folga de **+0,0029** sobre o piso, e **abaixo da meta de 0,78–0,85** fixada na aprovação do Sprint 3 (`docs/APROVACAO_SPRINT3.md`, no repositório desde a A1-r8 — antes o número não tinha fonte auditável). Os Módulos 2 e 3 são de **26/08**, anteriores a todo o trabalho destes sprints, e trazem ROC-AUC **0,995** (autoencoder) e **0,9792** (payday). | **Fecha no Sprint 4**, que retreina os três com os 15.000 do Sprint 3 — que ainda não rodou: `data/synthetic/treino_15000.parquet` não existe e o gerador segue não-calibrado, então o 0,7029 é do gerador ANTIGO. **Correção da auditoria A1-r8:** esta linha dizia "AUC 0,6797, abaixo do piso" e "`train_metrics.json` com schema anterior ao commit `4109d84`". As duas afirmações eram falsas: o arquivo em disco tem 0,7029 e o schema novo (com `metricas_por_limiar` e `recall_operacional`, que só existem depois daquele commit). Números reproduzidos carregando o modelo de disco e re-scorando o holdout: AUC 0,7029, recall 0,9400, precisão 0,5054, matriz `[[410,1241],[81,1268]]` — idênticos aos gravados. O erro sobreviveu a sete rodadas de auditoria porque `crai/models/` é ignorado pelo git: os auditores liam a declaração, nunca o arquivo. ⚠️ O **0,995** do autoencoder e o **0,9792** do payday não estão sob o gate anti-vazamento (que o plano aplica ao Módulo 1), mas são a faixa que o próprio `sprints.md` chama de *"bandeira vermelha que qualquer orientador reconhece"*. O Sprint 4 precisa ou justificar os dois, ou medi-los sob o mesmo critério. |

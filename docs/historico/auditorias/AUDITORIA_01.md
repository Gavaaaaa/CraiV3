# AUDITORIA ADVERSARIAL #1 — Sprints 1 e 2

**Escopo:** `baseline-pre-sprint..aa6c487` (16 arquivos, +2302/−67)
**Data:** 31/08/2026 · **Contexto:** sessão limpa, sem acesso às justificativas de quem implementou
**Ambiente:** Python 3.12.10, Windows 10, `D:\PTI\crai`, modelos treinados presentes em `crai/models/`

Suíte no HEAD, antes de qualquer ataque:

```
$ cd D:\PTI\crai; python -m pytest tests/ -q
272 passed, 3 warnings in 16.01s
```

Verde não é o mesmo que correto. O que segue são os payloads que a suíte não tem.

---

## 1. Método

Cada linha da tabela abaixo tem:

1. um payload/estado construído para **ainda disparar** o defeito alegado como corrigido;
2. a saída real do comando que o dispara (ou a prova de que a correção resiste);
3. o resultado de reverter **só o código** para `baseline-pre-sprint`, mantendo os testes do HEAD:

```bash
git checkout baseline-pre-sprint -- crai/crai/integrations/payment_gateway.py \
    crai/crai/api/app.py crai/crai/agent/workflow.py crai/crai/agent/main_agent.py \
    crai/crai/dunning/pix_automatico_retry.py
cd crai; python -m pytest <teste> -q
cd D:\PTI; git checkout HEAD -- <os mesmos arquivos>
```

Os scripts de ataque rodaram de `D:\PTI\crai` com `PYTHONPATH=D:\PTI\crai` e foram apagados ao fim.

---

## 2. Veredito por defeito alegado

| ID | Alegação | Veredito | Evidência executada | Gravidade |
|---|---|---|---|---|
| **P0-1** | `_extrair_valor` fazia `float()` no valor cru → `ValueError` com `"299,90"` | **CONFIRMADO** | §3.1 — `"299,90"`→299.9, `"R$ 1.299,90"`→1299.9, nenhuma exceção em nenhum formato testado. **Mas** a função nova introduz dois defeitos novos (N-1, N-2) na mesma linha de código | — (ver N-1/N-2) |
| **P0-2** | `data: [{...}]` (lote) → normalizado inteiro em default | **CONFIRMADO** | §3.2 — lote de 1 desempacotado (`valor=299.9`, `degradacoes=['lote_de_1']`); lote de 2 → `PayloadPixInvalido` → HTTP 422. Resíduo menor em N-9 | — |
| **P0-3** | `data: {}` vs `data: {...}` → fonte de leitura mudava conforme o PSP | **CONFIRMADO** | §3.3 — `{}`, `None`, `[]`, `"texto"`, `42` produzem todos o mesmo caminho (raiz) e todos marcam `envelope_ausente`. `grep -rn "or raw_payload" crai/` → zero | — |
| **P0-4** | Fallback de status comparava status cru contra tokens internos → toda falha virava `desconhecido` | **PARCIAL** | §3.4 — o branch morto morreu (`status:"failed"` → `cobranca_falhada`, HTTP 200 `pipeline:true`). **Mas** com a *fixture de referência do próprio repositório* e sem nome de evento, `status:"failed"` vira `autorizacao_concedida`, `pipeline:false` — o mesmo sumiço silencioso, por uma porta nova (N-3). E `status:"revoked"/"cancelled"/"authorized"` → `desconhecido` (N-4) | **ALTA** |
| **P0-5** | Valor ausente → `0.0` sem log → e-Profit ≤ 0 → churn legítimo descartado | **PARCIAL** | §3.5 — valor ausente/ilegível → `degradacoes` + warning + HTTP 422, confirmado. **Mas** `valor: 0`, `"0,00"`, `"0,004"`, `total_cents: 0` e `"1,299"` entregam 0.0/1.3 com `degradacoes=[]`, passam o portão e reproduzem o sintoma completo (e-Profit ≤ 0, descartado sem rastro). E **nenhum nó do grafo lê `degradacoes`** (N-6), contra o que a docstring afirma | **ALTA** |
| **P0-6** | `id_recorrencia` vazio → `thread_id` `"rec_desconhecida"` compartilhado | **PARCIAL** | §3.6 — o literal saiu (`grep -rn "rec_desconhecida" crai/` → só docstring). **Mas** dois clientes distintos ainda colidem: `\|` não é escapado, e um SaaS de preço único cujo PSP omita `e2e_id`/ISPB coloca **todos** os anônimos no mesmo checkpoint. Reproduzido ponta a ponta pelo webhook assinado (N-5) | **ALTA** |
| **P1-7** | `_ancorar_na_liquidez` devolvia `[]` com payday no último dia em hora tardia | **CONFIRMADO** | §3.7 — 54.600 combinações de (hora do vencimento × hora de `agora` × dia de `agora` × dia previsto × usadas × confiança × minutos): **0 listas vazias com janela aberta**. Repro do plano (venc 01/09 09h, payday 08/09 21h) → 3 tentativas | — |
| **P1-8** | Ancoragem só para frente → jogava fora tentativas a que o recebedor tem direito | **CONFIRMADO** | §3.8 — nas mesmas 54.600 combinações: **0 sub-agendamentos**, **0 violações** (prazo, intervalo ≥ 1 dia, ≤ 3 somadas, ordem crescente). `test_pipeline.py`: `RN_maria_001` → **3/3** | — |

**Gate GA1, critério "todo defeito P0/P1 tem veredito CONFIRMADO": REPROVADO** (P0-4, P0-5 e P0-6 são PARCIAL).

---

## 3. Evidência executada

### 3.1 P0-1 — a conversão tolerante resiste

Comando (`ataque1.py`, seção A/B):

```
$ PYTHONPATH=D:\PTI\crai python ataque1.py
  _para_float('299,90')       = 299.9
  _para_float('1.299,90')     = 1299.9
  _para_float('R$ 1.299,90')  = 1299.9
  _para_float('299.90')       = 299.9
  _para_float('1,299.90')     = 1299.9
  _para_float('0x10')         = None     # recusa, como prometido
  _para_float('_1_0_0')       = None
```

Prova de que o defeito existia no commit anterior — a suíte `test_payment_gateway.py` **não coleta** lá (§4), então exercitei o módulo baseline direto:

```
$ git checkout baseline-pre-sprint -- crai/crai/integrations/payment_gateway.py ...
$ PYTHONPATH=D:\PTI\crai python baseline_repro.py
P0-1  valor '299,90' (pt-BR):
      -> ValueError: could not convert string to float: '299,90'   <<< EXCECAO = HTTP 500
```

Não consegui derrubar a função com nenhuma string: `int`, `float`, `bool`, `None`, dict, list, hex, underscore, string vazia, só símbolos. **A correção resiste ao defeito alegado.** O que ela introduziu está em N-1 e N-2.

### 3.2 / 3.3 P0-2 e P0-3 — envelope determinístico

```
$ PYTHONPATH=D:\PTI\crai python ataque1.py   # seções E/F
  data=[{...}]        -> valor=299.9  degr=['lote_de_1']
  data=[{...},{...}]  -> PayloadPixInvalido: lote_nao_suportado → HTTP 422
  data={}     + valor na raiz  -> valor=150.0 degr=['envelope_ausente']
  data=None   + valor na raiz  -> valor=150.0 degr=['envelope_ausente']
  data=[]     + valor na raiz  -> valor=150.0 degr=['envelope_ausente']
  data="texto"+ valor na raiz  -> valor=150.0 degr=['envelope_ausente']
  data=42     + valor na raiz  -> valor=150.0 degr=['envelope_ausente']

$ grep -rn "or raw_payload" crai/
  (zero ocorrencias)
```

Baseline, para comparação — as duas fontes de leitura que o P0-3 denunciava:

```
P0-2  data:[{...}] (lote de 1):
      -> {'e2e_id': '', 'valor': 0.0, 'status': 'cobranca_falhada', 'ispb_pagador': '', 'id_recorrencia': ''}
P0-3  data={}      -> {... 'valor': 150.0 ...}      # leu a RAIZ
      data={'x':1} -> {... 'valor': 0.0 ...}        # leu o DATA
```

Ambos confirmados.

### 3.4 P0-4 — o branch morto morreu, mas a falha continua sumindo

O que funciona:

```
$ PYTHONPATH=D:\PTI\crai python ataque2.py
  [200] {"status":"ok","evento":"cobranca_falhada","pipeline":true}   # data.status="failed"
```

O que **ainda** perde a cobrança falhada — usando `payload_pix()`, a fixture de referência **do próprio repositório** (`tests/test_payment_gateway.py:63`), que já carrega `authorization_status: "approved"` em todo payload, com `status: "failed"` acrescentado e sem nome de evento (o cenário exato que o P0-4 descreve):

```
$ PYTHONPATH=D:\PTI\crai python -c "... payload_pix('automatic_pix.charge_failed'); p['data']['status']='failed'; del p['event'] ..."
payload (fixture DO PROPRIO REPO, sem event):
{ "data": { "id": "inv_pix_001", "total_cents": 29990,
   "automatic_pix": { "journey": "JORNADA_1", "recurrence_id": "RN2026082600001",
                      "authorization_status": "approved" },
   "pix": {...}, "status": "failed" } }
-> normalizado: {'e2e_id': 'E60701190202608261200abcdef123', 'valor': 299.9,
                 'status': 'autorizacao_concedida', ..., 'degradacoes': []}
```

E pelo webhook assinado:

```
  [200] status=failed + authorization_status=approved (perda silenciosa)
        {"status":"ok","evento":"autorizacao_concedida","pipeline":false}
```

`degradacoes` vazio, HTTP 200, `pipeline:false`, **nenhum warning**. É o sintoma do P0-4 na íntegra — "toda cobrança falhada de um PSP que não manda nome de evento some silenciosamente" — só que agora sem sequer o log de `desconhecido` que existia antes. Detalhado em N-3.

### 3.5 P0-5 — o portão 422 existe, mas fecha na porta errada

Funciona:

```
  [422] valor ausente em cobranca falhada
        {"detail":{"motivo":"evento_degradado","degradacoes":["valor_ausente"]}}
```

Não funciona — `ataque5.py`, seção I:

```
   valor=0            -> normalizado=0.0   degradacoes=[]  recusado_422=False
   valor='0,00'       -> normalizado=0.0   degradacoes=[]  recusado_422=False
   valor='R$ 0,00'    -> normalizado=0.0   degradacoes=[]  recusado_422=False
   valor='0,004'      -> normalizado=0.0   degradacoes=[]  recusado_422=False
   valor='1,299'      -> normalizado=1.3   degradacoes=[]  recusado_422=False
   valor='10,000'     -> normalizado=10.0  degradacoes=[]  recusado_422=False
   total_cents=0      -> normalizado=0.0   degradacoes=[]  recusado_422=False
```

O portão do P0-5 é acionado pelo **rótulo de degradação**, não pelo **valor**. Um valor que chega a 0.0 por qualquer outro caminho — o PSP mandar 0, o arredondamento comer os centavos, a heurística de milhar do N-1 dividir por 1000 — passa direto e reproduz a cadeia inteira do P0-5: `invoice_amount≈0` → LTV≈0 → e-Profit ≤ 0 → `route_after_diagnosis` → descartado. Com `"valor": NaN` isso é literal na saída do pipeline real:

```
[AGENT] Diagnostico (ensemble_xgb_rf): score 39/100 | e-Profit R$ nan | acao: NAO
[ROUTER] e-Profit R$ nan <= 0 -- abortando (nao vale intervir)
  [200] {"status":"ok","evento":"cobranca_falhada","pipeline":true}
```

### 3.6 P0-6 — o literal saiu, a colisão não

```
$ grep -rn "rec_desconhecida" crai/
crai/api/app.py:83:    outro (ver `crai/agent/main_agent.py`). Usar o literal `"rec_desconhecida"`
```

Só docstring — o critério do gate G2 está atendido na letra. Mas o `thread_id` novo colide. Ponta a ponta, quatro webhooks **assinados** e distintos:

```
$ PYTHONPATH=D:\PTI\crai python ataque6.py
  A         HTTP 200 {'status': 'ok', 'evento': 'cobranca_falhada', 'pipeline': True}
  B         HTTP 200 {'status': 'ok', 'evento': 'cobranca_falhada', 'pipeline': True}
  C(maria)  HTTP 200 {'status': 'ok', 'evento': 'cobranca_falhada', 'pipeline': True}
  D(joao)   HTTP 200 {'status': 'ok', 'evento': 'cobranca_falhada', 'pipeline': True}

  thread_id efetivamente entregue ao MemorySaver:
    rec_anon_33e7db5c52e3efcf   (valor=299.9 e2e='E123|999' ispb='60701190')
    rec_anon_33e7db5c52e3efcf   (valor=299.9 e2e='E123' ispb='999|60701190')
    rec_anon_806c74f4c681f239   (valor=299.9 e2e='' ispb='')
    rec_anon_806c74f4c681f239   (valor=299.9 e2e='' ispb='')

  A e B colidem? True
  C e D colidem? True
```

Detalhado em N-5.

### 3.7 / 3.8 P1-7 e P1-8 — a janela BACEN resiste

Força bruta sobre `schedule()` (`ataque_janela.py`), variando **horário de `agora` e de `vencimento` independentemente**, como pedido:

| eixo | valores |
|---|---|
| hora do vencimento | 0, 9, 13, 21, 23 |
| hora de `agora` | 0, 9, 13, 21, 23 |
| minuto | 0, 30, 59 |
| dia de `agora` (rel. venc.) | −2, 0, +1, +3, +6, +7, +8 |
| dia previsto (rel. venc.) | −2 … +10 |
| `tentativas_usadas` | 0, 1, 2, 3 |
| `confidence` | 0.95, 0.30 |

```
combinacoes testadas: 54600
  chamadas sem excecao : 54600
  VIOLACOES de invariante (prazo / 1 dia / >3 / ordem / excecao): 0
  LISTA VAZIA com janela aberta (P1-7): 0
  SUB-AGENDAMENTO (P1-8, agendou menos do que cabia): 0
```

O recuo do bloco é aritmeticamente sólido: `inicio_bloco = min(offset_previsto, dias_disponiveis + 1 - cabem)` garante `inicio_bloco ≥ 0` e último offset `≤ dias_disponiveis`, e `_dias_da_janela` conta por `timedelta` (não por calendário), que é justamente o que impede a última tentativa de furar o prazo quando `agora` e `vencimento` têm horas diferentes. **Não consegui produzir violação.**

O racional assimétrico também se mantém — o bloco só recua o mínimo:

```
  payday dia+5: 3 tentativas ['06/09 09:00','07/09 09:00','08/09 09:00'] | 0 ANTES do payday
  payday dia+6: 3 tentativas ['06/09 09:00','07/09 09:00','08/09 09:00'] | 1 ANTES do payday
  payday dia+7: 3 tentativas ['06/09 09:00','07/09 09:00','08/09 09:00'] | 2 ANTES do payday
```

E o gate G2 na demo:

```
$ PYTHONIOENCODING=utf-8 python test_pipeline.py
[PIX-RETRY] RN_maria_001: 3 tentativa(s) agendada(s) via previsão do Payday Engine | prazo BACEN: 07/09/2026
[PIX-RETRY]   tentativa 1/3: 01/09 23:42 | R$ 299.90
[PIX-RETRY]   tentativa 2/3: 02/09 23:42 | R$ 299.90
[PIX-RETRY]   tentativa 3/3: 03/09 23:42 | R$ 299.90
```

**Observação (não é defeito):** a rede de segurança do P1-7 (`pix_automatico_retry.py:199-201`, "ancoragem vazia — caindo para fallback uniforme") é **código morto**. Varri todas as combinações em que `na_janela` é verdadeiro e `_ancorar_na_liquidez` nunca devolve `[]`:

```
  Nenhuma. `_ancorar_na_liquidez` nunca devolve [] com a janela aberta
  => o fallback do P1-7 é CODIGO MORTO em producao; so e alcancavel
     monkeypatchando o metodo, que e o que o teste faz.
```

Consequência prática: o critério que o plano escreveu para o teste do P1-7 — *"3 tentativas, origem `fallback_uniforme`"* — **não é o que acontece**; o caso do plano é resolvido pelo próprio ancoramento, com `origem=payday_engine`. O teste implementado não afirma a origem, então não acusa a divergência. O resultado é melhor que o especificado; o registro fica porque o plano e o código discordam.

---

## 4. Testes de regressão que NÃO falham no commit anterior

Rodei a suíte do HEAD contra o código do baseline. Resultados:

### 4.1 `tests/test_payment_gateway.py` — não falha: **nem coleta**

```
$ git checkout baseline-pre-sprint -- crai/crai/integrations/payment_gateway.py ...
$ cd crai; python -m pytest tests/test_payment_gateway.py -q
ERROR tests/test_payment_gateway.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
3 warnings, 1 error in 8.99s
```

Causa: o `import` no topo do arquivo puxa oito símbolos que não existem no baseline (`DEGRADACAO_*`, `DEGRADACOES_CONHECIDAS`, `MOTIVO_*`, `PayloadPixInvalido`).

**Isso reprova o critério do gate GA1** — "todo teste de regressão comprovadamente falha no commit anterior". Um `ImportError` de coleta não demonstra defeito nenhum: ele apenas prova que o arquivo de teste foi escrito depois da correção, contra a regra explícita do plano ("escrever ANTES da correção, ver falhar", §4 Sprint 1). Nenhum dos ~40 testes novos de `TestPayloadsHostis` / `TestEndpointRecusaPayloadDegradado` tem prova comportamental de falha-antes.

Supri a lacuna com `baseline_repro.py` (§3.1–3.3), que exercita o módulo baseline direto e reproduz P0-1 a P0-5. **Os defeitos eram reais** — o que falta é o teste provar isso, não o defeito existir.

### 4.2 Testes que PASSAM antes e depois — não testam nada

```
$ python -m pytest "tests/test_payment_isolation.py::TestUmDefaultSoParaPaymentMethod" -q
1 passed, 3 warnings in 8.66s          # ← com o CÓDIGO DO BASELINE
```

| Teste | Baseline | HEAD | Diagnóstico |
|---|---|---|---|
| `TestUmDefaultSoParaPaymentMethod::test_payment_method_ausente_cai_no_mesmo_default` | **PASSED** | PASSED | Único teste do **P2-12**. Não pode discriminar — ver N-13: `None` e `"card"` roteiam para o mesmo destino |
| `TestNenhumaTentativaDesperdicada::test_previsao_no_primeiro_dia_respeita_todas_as_invariantes` | **PASSED** | PASSED | Payday no dia 1 já cabia para frente no baseline |
| `test_hora_de_agora_nao_empurra_tentativa_alem_do_prazo[0]` | **PASSED** | PASSED | — |
| `test_hora_de_agora_nao_empurra_tentativa_alem_do_prazo[7]` | **PASSED** | PASSED | — |
| `test_invariante_parametrico` — 25 dos 32 casos | **PASSED** | PASSED | Aceitável: é varredura de invariante, e 7 casos (`[0-6] [0-7] [1-7]` + os 3 de hora + os targeted) falham no baseline |

### 4.3 Testes que falham antes, mas pelo motivo errado

Os 7 de `TestThreadIdNuncaColide` "falham" no baseline por `AttributeError: module 'crai.api.app' has no attribute '_thread_id'` — a função não existia. Falham porque o símbolo sumiu, não porque a colisão do P0-6 foi demonstrada. É evidência mais fraca do que o gate supõe.

### 4.4 O que falha corretamente

```
$ python -m pytest tests/test_pix_automatico_retry.py -q      # código do baseline
11 failed, 57 passed in 0.42s
   test_previsao_perto_do_fim_nao_desperdica_tentativa                FAILED
   test_previsao_no_ultimo_dia_em_hora_tardia_nao_zera                FAILED
   test_previsao_no_dia_6_de_7_agenda_as_tres                         FAILED
   test_datas_saem_em_ordem_crescente                                 FAILED
   test_invariante_parametrico[0-6] [0-7] [1-7]                       FAILED
   test_hora_de_agora_nao_empurra_tentativa_alem_do_prazo[12][18][23] FAILED
   test_ancoragem_vazia_cai_no_fallback_em_vez_de_zerar               FAILED
```

P1-7 e P1-8 têm regressão honesta. São os únicos dos oito que têm.

Repositório restaurado e revalidado depois do experimento:

```
$ git checkout HEAD -- crai/crai/integrations/payment_gateway.py crai/crai/api/app.py \
      crai/crai/agent/workflow.py crai/crai/agent/main_agent.py \
      crai/crai/dunning/pix_automatico_retry.py
$ cd crai; python -m pytest tests/ -q
272 passed, 3 warnings in 15.88s
```

---

## 5. Defeitos novos encontrados

### N-1 — `_para_float` converte silenciosamente para o número ERRADO · **GRAVE**

A heurística "o separador decimal é o que aparece por último" quebra em milhar en-US **sem casas decimais**. O resultado não é `None` (que viraria degradação e 422): é um número plausível, mil vezes menor.

```
$ PYTHONPATH=D:\PTI\crai python ataque1.py
  _para_float('1,299')  = 1.299    # en-US: mil duzentos e noventa e nove
  _para_float('2,500')  = 2.5      # en-US: dois mil e quinhentos
  _para_float('10,000') = 10.0     # en-US: dez mil
```

Pelo parser completo, sem nenhum rastro:

```
   valor='1,299'   -> normalizado=1.3    degradacoes=[]  recusado_422=False
   valor='10,000'  -> normalizado=10.0   degradacoes=[]  recusado_422=False
```

A docstring declara como limite conhecido a ambiguidade de `"1.299"`. Não declara a de `"1,299"` — que é o **espelho exato** dela e igualmente ambígua. Pior: a própria função aceita `"1,299.90"` como milhar en-US, ou seja, ela **já assume** que o PSP pode falar en-US; aí, quando o mesmo PSP manda um valor redondo sem centavos, ela troca de idioma no meio da frase.

Impacto: uma cobrança de R$ 10.000 vira R$ 10,00 → LTV ≈ 0 → e-Profit ≤ 0 → churn involuntário de maior valor da base é o **primeiro** a ser descartado. É a cadeia do P0-5 inteira, disparada pela correção do P0-1.

### N-2 — `NaN` e `Infinity` atravessam o parser · **GRAVE**

`float()` aceita `"nan"`, `"inf"`, `"Infinity"`, `"-inf"`. E `json.loads` aceita os literais `NaN`/`Infinity` no corpo por padrão. Nada disso é filtrado.

```
   data.valor='nan'      -> valor=nan   status=cobranca_falhada  degr=[]
   data.valor='Infinity' -> valor=inf   status=cobranca_falhada  degr=[]
   JSON literal NaN      -> valor=nan   degr=[]
      isnan(valor)=True  bloqueante? False
```

Duas consequências, ambas reproduzindo defeitos que as sprints diziam ter fechado:

**(a) `NaN` → sintoma do P0-5, com HTTP 200.** O pipeline roda inteiro e descarta o cliente:

```
[AGENT] Diagnostico (ensemble_xgb_rf): score 39/100 | e-Profit R$ nan | acao: NAO
[ROUTER] e-Profit R$ nan <= 0 -- abortando (nao vale intervir)
[ROI] [X] R$ nan | taxa R$ 0.00 | e-Profit R$ nan
[HUBSPOT-SIM] Deal criado: Recuperacao RN_nan - R$ nan | stage=retrying
  [200] {"status":"ok","evento":"cobranca_falhada","pipeline":true}
```

**(b) `Infinity` → sintoma do P0-1, com HTTP 500.** A `ValueError` só mudou de andar: saiu do parser e foi para o `sklearn`, e escapa até o handler do FastAPI.

```
  [500] valor Infinity literal no JSON     Internal Server Error
  [500] valor "inf" como string            Internal Server Error

  File "sklearn\utils\validation.py", line 172, in _assert_all_finite_element_wise
    raise ValueError(msg_err)
  ValueError: Input X contains infinity or a value too large for dtype('float32').
```

É exatamente a falha que o P0-1 descreve — `ValueError` → HTTP 500 → tempestade de retry do PSP. O Sprint 1 blindou a borda contra `"299,90"` e deixou `"inf"` passar para o modelo.

### N-3 — `authorization_status` tem precedência sobre `status: "failed"` · **GRAVE**

`_extrair_status` consulta `STATUS_CRU_AUTORIZACAO` **antes** de `STATUS_CRU_COBRANCA` e retorna no primeiro acerto. Um payload que carregue os dois campos — o objeto `automatic_pix` com a autorização ativa e o objeto de cobrança com a falha — é classificado pela autorização.

Isso não é payload exótico: é a **forma da fixture de referência do próprio repositório** (`tests/test_payment_gateway.py:63-80`), que traz `authorization_status: "approved"` em todos os payloads. Basta o PSP não mandar nome de evento — que é a premissa inteira do P0-4:

```
-> normalizado: {..., 'status': 'autorizacao_concedida', ..., 'degradacoes': []}
  [200] {"status":"ok","evento":"autorizacao_concedida","pipeline":false}
```

Resultado: cobrança falhada perdida, `pipeline:false`, `degradacoes` vazio, **zero warning** — pior que o baseline, que ao menos logava `Evento não reconhecido` e marcava `desconhecido`.

A justificativa escrita no código para os dois mapas ("`authorization_status: approved` é a AUTORIZAÇÃO concedida, não uma cobrança confirmada") está correta como semântica de campo, mas foi implementada como **ordem de precedência entre eventos**, que é outra coisa. Uma falha de cobrança explícita não pode perder para o estado (estático) da autorização.

### N-4 — a divisão em dois mapas perde status que o mapa único do plano cobria · **MÉDIA**

O plano (§4, Sprint 1, tarefa 4) especificou **um** `STATUS_CRU_MAP` contendo `authorized`, `revoked` e `cancelled`. A implementação separou em dois e, com isso, esses tokens deixaram de ser reconhecidos quando chegam no campo genérico `status` — que é onde muitos PSPs os colocam:

```
  data.status='authorized' -> desconhecido   (mapa unico do plano diria: autorizacao_concedida)
  data.status='revoked'    -> desconhecido   (mapa unico do plano diria: autorizacao_revogada)
  data.status='cancelled'  -> desconhecido   (mapa unico do plano diria: autorizacao_revogada)
  data.status='canceled'   -> desconhecido
  data.status='active'     -> desconhecido
  data.status='created'    -> desconhecido
  data.status='expired'    -> desconhecido
```

Pelo webhook: `{"status":"ok","evento":"desconhecido","pipeline":false}`.

`autorizacao_revogada` é o sinal de churn **voluntário** que `app.py:61` diz registrar para o pipeline voluntário consumir. Um PSP que mande `status: "revoked"` perde esse sinal.

**Julgamento sobre a pergunta do plano:** a intuição dos dois mapas é boa (o nome do campo carrega semântica) e a implementação é uma melhoria *para o campo `authorization_status`*. Mas ela foi entregue sem preservar a cobertura do mapa único, e com a ordem de precedência invertida. Líquido: **piora**, por causa do N-3.

### N-5 — `thread_id` anônimo colide entre clientes distintos · **GRAVE**

`base = f"{e2e_id}|{ispb_pagador}|{valor}"`. Dois problemas:

**(a) O delimitador não é escapado.** Ambos os campos vêm crus do payload:

```
    A e2e='E123|999' ispb='60701190' -> rec_anon_33e7db5c52e3efcf
    B e2e='E123' ispb='999|60701190' -> rec_anon_33e7db5c52e3efcf
    COLIDEM? True
```

**(b) Muito pior — a base pode não ter discriminante nenhum.** Um SaaS de preço único, com PSP que não popula `e2e_id`/ISPB no evento de falha, produz base constante `"||299.9"` para **todos** os pagadores anônimos:

```
    cliente 1 -> rec_anon_806c74f4c681f239
    cliente 2 -> rec_anon_806c74f4c681f239
    COLIDEM? True  <- P0-6 identico, restrito a mesmo preco/ISPB
```

Isso é o P0-6 sem alteração de substância: em vez de um checkpoint único `"rec_desconhecida"` para todos, há um checkpoint único por (preço, ISPB) — e num SaaS de plano único isso é a mesma coisa. O docstring afirma "Colisão passa a acontecer só quando dois eventos são realmente indistinguíveis"; a afirmação é verdadeira sobre a **base**, e falsa sobre os **clientes**, porque a base perdeu campos.

Também: `degradacoes` não entra na base, e a chave ausente é indistinguível da string vazia:

```
  integro -> rec_anon_999c2abd043fafaa | degradado -> rec_anon_999c2abd043fafaa | COLIDEM? True
  sem e2e/ispb -> rec_anon_806c74f4c681f239 | strings vazias -> rec_anon_806c74f4c681f239 | iguais? True
```

O teste `test_dois_pagadores_anonimos_diferentes_nao_colidem` só passa porque escolhe eventos que diferem em **e2e_id e valor ao mesmo tempo**. Ele não cobre o caso em que a base é degenerada.

### N-6 — `degradacoes` não é lido por nenhum nó do pipeline · **MÉDIA**

A docstring do módulo (linha 24) afirma: *"O pipeline nunca recebe um default sem saber que é um default."* Não é o que o código faz.

```
$ grep -rn "degradacoes" crai/ --include=*.py | grep -v "integrations/payment_gateway.py"
crai/api/app.py:161:  "(recorrencia=%s, degradacoes=%s)",              # log
crai/api/app.py:162:  rotulo, evento["id_recorrencia"], evento["degradacoes"] or "nenhuma"
crai/api/app.py:169:  bloqueantes = sorted(set(evento["degradacoes"]) & DEGRADACOES_BLOQUEANTES)
crai/api/app.py:178:  "motivo": "evento_degradado", "degradacoes": bloqueantes,
crai/api/app.py:260:  "degradacoes": [],                                # /simulate, sintetico
```

Nenhuma ocorrência em `crai/agent/`, `crai/ml/`, `crai/dunning/` ou `crai/integrations/hubspot_crm.py`. O campo é lido **só na borda**, e só para os dois rótulos bloqueantes. Um evento com `lote_de_1` ou `envelope_ausente` roda a recuperação inteira e vira deal no HubSpot sem que nenhum nó saiba que foi montado a partir de um default:

```
   lote_de_1 -> {'e2e_id': '', 'valor': 299.9, 'status': 'cobranca_falhada',
                 'ispb_pagador': '', 'id_recorrencia': 'RN_lote', 'degradacoes': ['lote_de_1']}
```

Note o `e2e_id` e o `ispb_pagador` vazios nesse mesmo evento: um `lote_de_1` alimenta `_thread_id` com base degenerada (N-5) e ninguém marca nada. Além disso `store_encrypted_pix_key` (`payment_gateway.py:464`) chama `_resolver_envelope(raw_payload, [])` com uma lista descartável: o cofre resolve o envelope e joga a informação de degradação no lixo.

O campo é um bom mecanismo. Ele só não foi ligado em ponta nenhuma além do portão 422.

### N-7 — `/simulate/pix-falhado` não passa por `_thread_id` · **MÉDIA**

```python
# crai/api/app.py:264
customer_id=payload.id_recorrencia, amount=payload.valor,
```

O endpoint que a demo usa monta `customer_id` direto do corpo, sem a função nova. Com `{"id_recorrencia": ""}` o `thread_id` vira string vazia — todos os cenários compartilham checkpoint. A correção do P0-6 cobriu um dos dois caminhos que chegam a `_run_involuntary_pipeline`.

### N-8 — `float(previsao["confidence"])` não está protegido · **BAIXA**

`_consultar_payday` tem `try/except Exception` em volta de `predict_next_window` ("o agendamento não pode cair por causa do modelo"), mas a conversão acontece **depois**, em `_escolher_datas:180`, fora da proteção:

```
   {'timestamp': ..., 'confidence': None}   -> TypeError: float() argument must be a string
                                               or a real number, not 'NoneType'   <<< ESCAPA
   {'timestamp': ..., 'confidence': 'alta'} -> ValueError: could not convert string to float
                                               <<< ESCAPA de schedule()
   {'timestamp': ...}  (sem a chave)        -> ok, 3 tentativas
```

A `PaydayInference` atual devolve float, então não dispara hoje. Vira P0 no dia em que o Sprint 4 trocar o Módulo 3 — que é o próximo sprint a mexer nesse retorno.

### N-9 — `data: [<não-dict>]` cai na raiz em vez de ser recusado · **BAIXA**

```
  data=[ 'texto' ] -> valor=77.0 (lido da RAIZ)  degr=['envelope_ausente']
  data=[ null ]    -> valor=77.0 (lido da RAIZ)  degr=['envelope_ausente']
```

O plano especificou: lista com 1 item dict → desempacota; com mais → 422. Lista de 1 item **não-dict** ficou indefinida e caiu no ramo da raiz. É o resíduo do P0-3 ("a fonte de leitura depende do que o PSP mandou"), agora ao menos declarado.

### N-10 — valor negativo é aceito sem degradação · **MÉDIA**

```
   valor='-500,00' -> normalizado=-500.0 degradacoes=[] recusado_422=False
```

Pipeline real:

```
[AGENT] Diagnostico: score 66/100 | e-Profit R$ -330.30 | acao: NAO
[SHAP]  ... Valor da fatura R$ -500.00 (+18.4%) | Ticket medio -543.74 (+14.3%)
[HUBSPOT-SIM] Deal criado: Recuperacao RN_neg - R$ -500.00 | stage=retrying
```

Cobrança de valor negativo não existe no fluxo do Recebedor. Ela alimenta o SHAP e cria deal no CRM. É o tipo de linha que aparece na tela da banca.

### N-11 — `str()` sobre estruturas não-escalares vaza para o CRM · **BAIXA**

```
  [200] id_recorrencia e uma lista
[PIX-RETRY] ['a', 'b']: 3 tentativa(s) agendada(s) ...
[HUBSPOT-SIM] Contact upsert: ['a', 'b']
[HUBSPOT] Contact sim_contact_['a', 'b'] | Deal sim_deal_10805 | retrying
```

`str(_primeiro_preenchido(...))` aceita dict e list e produz a repr do Python como identificador de cliente. Sem exceção, sem degradação.

### N-13 — a correção do P2-12 é um no-op de comportamento · **BAIXA**

O P2-12 não está na lista dos oito, mas foi entregue no mesmo diff e ilustra o problema de processo do §4.2. `route_after_decision` retorna `schedule_retry_pix` **apenas** para `"pix_automatico"`; todo o resto — inclusive `None` e `"card"` — cai no mesmo `trigger_dunning`:

```
$ PYTHONPATH=D:\PTI\crai python -c "from crai.agent.main_agent import route_after_decision; ..."
  payment_method=None             -> trigger_dunning
  payment_method='card'           -> trigger_dunning
  payment_method='pix_automatico' -> schedule_retry_pix
  payment_method='boleto'         -> trigger_dunning
```

Trocar `state.get("payment_method")` por `state.get("payment_method", "card")` não altera nenhum roteamento — muda só o `%r` da mensagem de `logger.warning` (de `None` para `'card'`). A premissa do P2-12 ("o mesmo state ausente podia seguir dois caminhos") **não se sustenta neste roteador**: os dois defaults sempre convergiram.

A correção é inofensiva e alinha a leitura do código. Mas o teste que a acompanha não podia falhar antes — não porque foi mal escrito, e sim porque **não há comportamento a testar**. Vale reescrever a justificativa ou remover o teste; um teste que existe para preencher a regra "todo bug nasce com regressão" corrói a regra.

### N-12 — `python test_pipeline.py` quebra no console padrão do Windows · **MÉDIA (pré-existente, fora do diff)**

```
$ cd D:\PTI\crai; python test_pipeline.py
UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f680' in position 2
```

Só roda com `PYTHONIOENCODING=utf-8`. `test_pipeline.py` não foi tocado pelo diff, então **não é regressão das sprints** — mas significa que os comandos de gate `python test_pipeline.py` (G1) e `python test_pipeline.py 2>&1 | grep "PIX-RETRY"` (G2) **não podem ter sido executados verdes nesta máquina como escritos**. Relevante para o G5 (determinismo) e o GA2 (ambiente limpo).

---

## 6. O que resistiu

Registrado porque auditoria que só acusa não é auditoria:

- `_ancorar_na_liquidez` / `_escolher_datas`: 54.600 combinações, **zero** violações de invariante. A contagem por `timedelta` em `_dias_da_janela` é a decisão certa e está corretamente justificada no comentário.
- `_resolver_envelope`: determinístico para todos os tipos que testei. `grep -rn "or raw_payload" crai/` → zero. O P0-2 e o P0-3 estão fechados.
- `_para_float` nunca levanta — testei `int`, `float`, `bool`, `None`, `dict`, `list`, hex, underscore, string vazia, só-símbolos.
- Cobertura de `payment_gateway.py`: **95%** (gate G1 pedia ≥ 90%).
  ```
  crai\integrations\payment_gateway.py  156  8  95%  219, 223, 227, 375-378, 457, 479
  ```
- Privacidade: a chave Pix continua fora do schema normalizado e fora de `degradacoes`, incluindo nos payloads hostis.
- Suíte: **272 passed**, zero regressão, `test_pipeline.py` roda ponta a ponta (com `PYTHONIOENCODING=utf-8`).

---

## 7. Veredito

**BLOQUEADO para o Sprint 3.**

Três dos oito defeitos alegados são PARCIAL (P0-4, P0-5, P0-6), o que já reprova o gate GA1. Além disso, dois dos defeitos novos **reproduzem exatamente os sintomas que as sprints diziam ter eliminado**: N-2(b) devolve a `ValueError` → HTTP 500 do P0-1 (agora vinda do sklearn), e N-3 devolve o sumiço silencioso de cobrança falhada do P0-4 (agora sem nem o log de `desconhecido`). E a suíte de regressão do Sprint 1 não demonstra falha no commit anterior — ela nem coleta lá.

### Mínimo necessário para liberar

Bloqueantes (P0):

1. **N-2 — recusar valores não-finitos.** `_para_float` devolve `None` para `nan`/`inf`/`-inf` (`math.isfinite`), e `json.loads(raw, parse_constant=...)` recusa os literais `NaN`/`Infinity` no corpo. Sem isso há um HTTP 500 alcançável por webhook assinado.
2. **N-3 — inverter a precedência de status.** Um status de cobrança explícito (`status`/`charge_status`/`payment_status` em `STATUS_CRU_COBRANCA`) tem que vencer `authorization_status`. Consultar o mapa de autorização só quando não houver status de cobrança reconhecível. Teste obrigatório: `payload_pix('...charge_failed')` sem `event` e com `status:"failed"` → `cobranca_falhada`.
3. **N-1 — não adivinhar milhar ambíguo.** Uma string com um único separador e exatamente 3 dígitos à direita (`"1,299"`, `"10,000"`) é ambígua: marcar `valor_ilegivel` (bloqueante → 422) em vez de escolher um dos dois sentidos em silêncio. Recusar é a política que o próprio módulo declara para o que não sabe interpretar.
4. **N-5 — `thread_id` sem colisão.** Escapar os campos (hash de um JSON canônico, ou de comprimentos+campos) **e** recusar (422 + log) derivar id anônimo de evento sem discriminante algum (`e2e_id` e `ispb_pagador` ambos vazios). Um checkpoint compartilhado por preço não é "eventos indistinguíveis", é perda de isolamento entre clientes.

Bloqueantes de processo (gate GA1):

5. **Testes de regressão que falhem no commit anterior por comportamento.** Mover as constantes novas para `pytest.importorskip`/`getattr` — ou, mais simples, um módulo `tests/test_regressao_p0.py` que não dependa dos símbolos novos — para que os casos de P0-1…P0-5 possam ser executados contra o baseline e vistos falhar. Hoje só P1-7 e P1-8 têm regressão honesta.
6. **Resolver o teste do P2-12.** Ele passa antes e depois porque não há comportamento a discriminar (N-13): `None` e `"card"` sempre rotearam igual. Remover o teste e reescrever a justificativa da mudança, ou apontá-lo para o que de fato mudou (a mensagem de log). Manter como está é registrar uma regressão que não existe.

Aceitáveis como dívida documentada (P1/P2), se listados no `AUDITORIA_02.md`:

7. N-4 (recuperar `authorized`/`revoked`/`cancelled`/`expired` no campo `status`), N-6 (ligar `degradacoes` a pelo menos um nó, ou corrigir a docstring que afirma o contrário), N-7 (`/simulate/pix-falhado` via `_thread_id`), N-8 (`float(confidence)` dentro do `try`), N-9, N-10, N-11, N-12.

**Reexecutar esta auditoria em contexto novo depois das correções 1–6.** Não negociar os itens 1–4: cada um deles é, na prática, um defeito da lista original ainda vivo.

---

*Auditoria executada sem acesso às justificativas da implementação. Scripts de ataque em scratchpad temporário, apagados ao fim. Único arquivo alterado neste sprint: `docs/AUDITORIA_01.md`.*

# RE-AUDITORIA ADVERSARIAL #1 (R5) — Sprints 1, 2 e A1

**Escopo:** `baseline-pre-sprint (2956a69) .. HEAD (317a0eb)` — branch `sprint/a1-auditoria`.
**Foco:** (A) os 4 bloqueios de `AUDITORIA_01_R4.md` §7, que o commit `317a0eb` alega fechar;
(B) defeitos NOVOS introduzidos por `317a0eb` e o que as rodadas 1-4 não cobriram.
**Contexto:** sessão limpa. Recebi apenas `sprints.md`, `docs/DATA_CARD.md` e as quatro
auditorias anteriores. Mensagens de commit, docstrings e README tratados como **alegação a
testar**, nunca como prova.
**Data:** 2026-09-04.

> Documento escrito incrementalmente: cada achado foi acrescentado assim que comprovado.

---

## 0. Método

- Ataques rodados de `D:\PTI\crai` contra o **pipeline real, não stubado** (`TestClient`,
  `MemorySaver` de processo), entrando pelos endpoints com **assinatura HMAC válida**.
- Prova de regressão em **worktree descartável** em
  `C:\Users\peide\AppData\Local\Temp\claude\a1r5\prev` (= `8ee67ae`), com os arquivos de teste
  do HEAD copiados para lá. Nunca `git checkout` de caminho na árvore principal.
- Suíte no HEAD, antes de qualquer ataque:

```
$ cd D:\PTI\crai; PYTHONIOENCODING=utf-8 python -m pytest tests/ -q
367 passed, 3 warnings in 17.90s
```

---

## 1. Tabela de vereditos

| ID | Alegação | Veredito | Evidência executada | Gravidade |
|---|---|---|---|---|
| **R4-1** | Limite BACEN deixa de atravessar webhooks: 3 webhooks do mesmo `id_recorrencia` agendam 3, não 9 | **CONFIRMADO** | §2.1 | — |
| **R4-2** | Teto de magnitude igual nas duas unidades (`valor` vs `total_cents`) | **CONFIRMADO** | §2.2 | — |
| **R4-3** | `TestA1R4TetoIgualNasDuasUnidades` compara os dois caminhos de verdade e falha em `8ee67ae` | **CONFIRMADO** | §2.3 | — |
| **R4-4** | Linha N-12 do README passa a descrever o inventário medido (12 no HEAD, 7 no baseline); 5 ocorrências corrigidas | **PARCIAL** | §4.1 | BAIXA |
| **N-R5-1** | *(novo, introduzido por `317a0eb`)* "O checkpoint mantém o acumulado" — mas o acumulado **nunca expira**: o `retry_count` é monótono e não há marca de tempo da janela. Um cliente que gastou as 3 tentativas fica **permanentemente** sem retentativa, mesmo numa janela BACEN nova | **🔴 DEFEITO NOVO** | §3.1 | **ALTA** |
| **N-R5-2** | *(novo, agravado por `317a0eb`)* O limite regulatório passa a ser guardado **exclusivamente** no `MemorySaver` em RAM: reiniciar o processo zera o contador de todos os clientes | **🔴 DEFEITO NOVO** | §3.2 | MÉDIA |
| **N-R5-3** | O limite continua atravessável por eventos sem `id_recorrencia` (identidade anônima por `e2e_id`): 3 eventos → 9 tentativas | **RESÍDUO** | §3.3 | MÉDIA |
| **N-R5-4** | `retry_exhausted` continua sendo sobrescrito com `False` na entrada do `ainvoke` — a assimetria que a docstring diz ter fechado só foi fechada para `retry_count` | **RESÍDUO** | §3.4 | BAIXA |
| **Catraca N-12** | `tests/test_encoding_saida.py` mede o que diz medir | **PARCIAL** | §4.2 | BAIXA |
| **R3-2 (revisto)** | "`/simulate/payment-failed` e `/simulate/churn-risk` deixam de dar 500" — marcado CONFIRMADO pela R4 | **PARCIAL** | §5.2 — `days_since_last`/`features_used_30d` = `NaN`/`Infinity`/`-Infinity` → **HTTP 500** nos seis casos; `_contador_de_simulacao` é ramo morto para essa entrada | MÉDIA |
| **Fuzz 5xx (Pix)** | O código novo de `317a0eb` não abre exceção até o handler do FastAPI | **CONFIRMADO** | §5.1 — 1.600 payloads assinados, `{200: 1212, 400: 80, 422: 308}`, zero 5xx | — |
| **P1-8** | Preenchimento para trás não agenda < 1 dia, não passa do prazo, não excede 3 | **CONFIRMADO** | §5.5 — 9.984 combinações, 0 violações | — |
| **`degradacoes`** | Nenhum caminho novo ignora a lista e segue com default | **CONFIRMADO** | §5.3 | — |
| **`thread_id`** | Dois clientes distintos colidem em algum payload | **NÃO ENCONTRADO** | §5.4 (com uma nota de dívida sobre `id_recorrencia = 0`) | BAIXA |

---

## 2. Parte A — o que fechou de verdade

### 2.1 R4-1 — o limite BACEN não atravessa mais os webhooks ✔

Três webhooks assinados do mesmo `id_recorrencia`, pipeline real (`MemorySaver` de processo),
com espião em `PixAutomaticoRetryPolicy.schedule` registrando `(customer_id, tentativas_usadas,
quantas_agendou, números)`:

```
=== A1: 3 webhooks, MESMO id_recorrencia (o caso da R4) ===
  webhook#1 -> (200, '{"status":"ok","evento":"cobranca_falhada","pipeline":true}')
  webhook#2 -> (200, '{"status":"ok","evento":"cobranca_falhada","pipeline":true}')
  webhook#3 -> (200, '{"status":"ok","evento":"cobranca_falhada","pipeline":true}')
  lotes: [('RN_A1', 0, 3, [1, 2, 3])]
  TOTAL AGENDADO: 3
```

Antes eram três lotes de 3. Agora o nó de agendamento é alcançado **uma vez**; do segundo
evento em diante `decide_recovery` barra em `mensagem_pagamento`. A premissa de merge do
LangGraph (omitir a chave preserva o valor do checkpoint) foi testada **diretamente** e é
verdadeira: o `retry_count` gravado por `schedule_retry_pix` sobrevive à invocação seguinte —
ver o dump do checkpoint em §3.1. **Correção resiste** para o caso que a R4 cobrou.

### 2.2 R4-2 — o teto passou a ser o mesmo nas duas unidades ✔

Busca binária no maior valor aceito por campo, contra `PixAutomaticoAdapter._extrair_valor`:

```
VALOR_MAXIMO_PLAUSIVEL = 1000000000000.0

--- fronteira declarada (teto em REAIS, igual nos dois campos) ---
  valor        bruto=1000000000000.0      -> R$ 1000000000000.0        degr=[]
  valor        bruto=1000000000001.0      -> R$ 0.0                    degr=['valor_ilegivel']
  total_cents  bruto=100000000000000.0    -> R$ 1000000000000.0        degr=[]
  total_cents  bruto=100000000000100.0    -> R$ 0.0                    degr=['valor_ilegivel']
  total_cents  bruto=1000000000000.0      -> R$ 10000000000.0          degr=[]

--- busca binaria do MAIOR valor em REAIS aceito por campo ---
  campo 'valor'      : maior R$ aceito = 999,498,877,761.07
  campo 'total_cents': maior R$ aceito = 999,498,877,761.07
  RAZAO = 1.0000x   (declarado: 1.0x)

--- o mesmo, por string (pt-BR e en-US) ---
  valor        bruto='1000000000000,00'     -> R$ 1000000000000.0        degr=[]
  total_cents  bruto='100000000000000'      -> R$ 1000000000000.0        degr=[]
  valor        bruto='1000000000001'        -> R$ 0.0                    degr=['valor_ilegivel']
  total_cents  bruto='100000000000100'      -> R$ 0.0                    degr=['valor_ilegivel']
```

A discrepância de 100× que sobreviveu a três rodadas **fechou**. O ramo que a R4 mostrou ser
morto (`_valor_utilizavel(round(centavos/100, 2))`) agora é alcançável — é ele quem recusa
`total_cents = 1e14 + 100`. Vale por número **e** por string, nos dois locales.

**Chamador em unidade errada: não encontrei.** Os únicos outros chamadores de `_para_float` são
`_valor_de_simulacao` (`api/app.py:167`) e `_contador_de_simulacao` (`api/app.py:190`), ambos
com o teto default — que é a unidade certa (reais e contagem, não centavos):

```
$ grep -rn "_para_float\|_valor_utilizavel" crai/ --include=*.py
crai/api/app.py:167:    valor = _para_float(bruto)
crai/api/app.py:190:    numero = _para_float(bruto)
crai/integrations/payment_gateway.py:334:        return _valor_utilizavel(valor, teto)
crai/integrations/payment_gateway.py:361:    return _valor_utilizavel(valor, teto)
crai/integrations/payment_gateway.py:518:            centavos = _para_float(bruto_centavos, teto=VALOR_MAXIMO_PLAUSIVEL * 100)
crai/integrations/payment_gateway.py:528:            reais_de_centavos = _valor_utilizavel(round(centavos / 100, 2))
```

### 2.3 R4-3 — os testes de regressão falham por comportamento em `8ee67ae` ✔

Worktree descartável em `C:\...\a1r5\prev` (= `8ee67ae`), com os três arquivos de teste do HEAD
copiados para lá:

```
$ cd <worktree 8ee67ae>/crai
$ python -m pytest tests/test_payment_gateway.py -k "A1R4" tests/test_payment_isolation.py -v --tb=line

tests/test_payment_gateway.py::TestA1R4TetoIgualNasDuasUnidades::test_cem_bilhoes_passa_pelos_dois_campos FAILED
tests/test_payment_gateway.py::TestA1R4TetoIgualNasDuasUnidades::test_a_fronteira_e_a_mesma_nas_duas_unidades FAILED
tests/test_payment_gateway.py::TestA1R4TetoIgualNasDuasUnidades::test_o_maior_valor_aceito_e_o_mesmo_nos_dois_campos FAILED
tests/test_payment_isolation.py::TestA1R4LimiteBacenAtravessaOsWebhooks::test_tres_webhooks_do_mesmo_cliente_nao_estouram_a_janela FAILED
tests/test_payment_isolation.py::TestA1R4LimiteBacenAtravessaOsWebhooks::test_o_numero_da_tentativa_nunca_se_repete FAILED
tests/test_payment_isolation.py::TestA1R4LimiteBacenAtravessaOsWebhooks::test_o_segundo_webhook_ve_a_janela_ja_gasta FAILED

tests\test_payment_gateway.py:1232: AssertionError: tetos diferentes: R$ 1,000,000,000,000 via 'valor'
    contra R$ 10,000,000,000 via 'total_cents' (100.0x de diferença)
tests\test_payment_isolation.py:557: AssertionError: LIMITE BACEN VIOLADO: 9 tentativas agendadas
    na mesma janela de 7 dias para o mesmo id_recorrencia (máximo 3). Lotes por invocação: [3, 3, 3]
tests\test_payment_isolation.py:573: AssertionError: numeração repetida na mesma janela:
    [1, 2, 3, 1, 2, 3, 1, 2, 3] — o contador do checkpoint foi sobrescrito pela entrada do ainvoke
====================== 6 failed, 187 deselected in 7.49s ======================
```

Falha por **igualdade e por contagem**, não por `ImportError`/`AttributeError` — a exigência do
Gate GA1 está cumprida para estes dois itens. Os 5 do `test_encoding_saida.py` também falham em
`8ee67ae` por conteúdo (§4.2); o 6º (`test_a_divida_herdada_nao_cresceu`) passa antes e depois,
e o commit o declara catraca, não regressão — declaração correta.

---

## 3. Parte B — o que `317a0eb` quebrou ao consertar

### 3.1 N-R5-1 (BLOQUEIO) — o contador nunca expira: o cliente fica preso para sempre

A docstring nova (`api/app.py:498-513`) diz *"Omitir a chave preserva o valor acumulado no
checkpoint"*. Preserva — e é exatamente esse o problema. `MAX_TENTATIVAS` é **3 por janela de 7
dias corridos** (`dunning/pix_automatico_retry.py:8-9`), não 3 na vida do contrato. O
`retry_count` gravado no checkpoint é **monótono**: nada no repositório o decrementa, o zera,
ou o associa a uma janela.

```
$ grep -rn "retry_count" crai/ --include=*.py
crai/agent/state.py:58:    retry_count:     int
crai/agent/workflow.py:154:    usadas = state.get("retry_count", 0)
crai/agent/workflow.py:224:    usadas = state.get("retry_count", 0)
crai/agent/workflow.py:244:            "retry_count": usadas + len(tentativas), ...
crai/api/app.py:536:        initial["retry_count"] = retries_done
crai/dunning/legacy_card/card_retry.py:29:    attempt = state.get("retry_count", 0)
crai/dunning/legacy_card/card_retry.py:39:            ... "retry_count": attempt + 1}
```

Ataque: **mesmo `id_recorrencia`, duas faturas de ciclos diferentes**, com o relógio da política
adiantado em 60 dias entre uma e outra — isto é, uma janela BACEN inteiramente nova.

```
### CICLO 1 (fatura de setembro) ###
[PIX-RETRY] RN_CICLO_MENSAL: 3 tentativa(s) agendada(s) via fallback uniforme | prazo BACEN: 11/09/2026
[PIX-RETRY]   tentativa 1/3: 05/09 16:51 | R$ 299.90
[PIX-RETRY]   tentativa 2/3: 08/09 16:51 | R$ 299.90
[PIX-RETRY]   tentativa 3/3: 11/09 16:51 | R$ 299.90
  status: 200
  chamadas a schedule: [('RN_CICLO_MENSAL', 0, 3)]
  CHECKPOINT retry_count      = 3
  CHECKPOINT retry_exhausted  = False
  CHECKPOINT tem marca de tempo da janela? NENHUMA CHAVE
  CHECKPOINT next_retry_at    = 2026-09-05 16:51:16.190034

### CICLO 2 (fatura de novembro, 60 dias depois; janela nova) ###
  relogio da politica agora: 2026-11-04 16:00:00
  status: 200
  chamadas a schedule no ciclo 2: NENHUMA
  estrategia do ciclo 2 : mensagem_pagamento
  retry_count            : 3
  raciocinio             :
      Observacao: causa=insufficient_funds, score=63/100, e-Profit=R$ 188.05, anomalia=nao.
      Pensamento: as 3 tentativas da janela regulada do BACEN ja foram usadas. Contatar com
                  mensagem personalizada; urgencia normal pelo score/anomalia.
      Decisao: mensagem_pagamento (Pix Automatico -> boleto).
```

O pipeline afirma *"as 3 tentativas da janela regulada do BACEN já foram usadas"* sobre uma
janela que **começou dois meses depois** daquela em que elas foram usadas. A linha
`CHECKPOINT tem marca de tempo da janela? NENHUMA CHAVE` é a prova mecânica: não existe no
state nenhum campo que permita distinguir "mesma janela" de "janela nova", logo o invariante
implementado não é o do BACEN — é *"3 tentativas por `id_recorrencia`, para sempre"*.

Consequência de negócio: **todo cliente recorrente que falhar uma vez perde o direito a
retentativa automática em todos os ciclos seguintes**, e vai direto para dunning. Não é um
detalhe de parser: é o produto do Sprint 2 desligando-se sozinho depois do primeiro mês. A
rodada 4 mediu o erro na direção permissiva (9 onde cabiam 3); `317a0eb` o trocou por um erro
na direção restritiva. Nenhuma das duas implementa a janela.

Nenhum teste da suíte cobre um segundo ciclo: `TestA1R4LimiteBacenAtravessaOsWebhooks` manda
`quantos` webhooks em sequência imediata e afirma `total <= 3`, o que é satisfeito tanto por
"3 por janela" quanto por "3 para sempre". **Gravidade ALTA — introduzido por `317a0eb`.**

### 3.2 N-R5-2 (BLOQUEIO) — o limite regulatório agora mora só na RAM

Como consequência direta de (1), a única guarda do limite BACEN passou a ser o `retry_count`
do `MemorySaver`, que é **em memória de processo** (`agent/main_agent.py:20,108`:
`from langgraph.checkpoint.memory import MemorySaver` … `graph.compile(checkpointer=MemorySaver())`).
Antes de `317a0eb` o contador não valia nada em lugar nenhum; agora ele é a única defesa, e ela
não sobrevive a um restart:

```
=== dois PROCESSOS distintos, MESMO id_recorrencia ===
PROCESSO 1 -> status 200 | schedule: [('RN_PERSISTENTE', 0, 3)]
PROCESSO 2 -> status 200 | schedule: [('RN_PERSISTENTE', 0, 3)]
```

Reiniciar a API (deploy, crash, autoscaling) devolve 3 tentativas novas a **todos** os clientes
dentro da mesma janela de 7 dias. O defeito que a R4 cobrou volta inteiro por essa porta.
O `MemorySaver` é pré-existente; o que `317a0eb` mudou é que ele passou a ser **load-bearing
para um limite regulatório**, o que antes não era. Gravidade MÉDIA na demo (monoprocesso),
ALTA em produção.

### 3.3 N-R5-3 — o limite continua atravessável pela identidade anônima

Três eventos de cobrança falhada **sem `id_recorrencia`** (o caminho em que `_thread_id` deriva
um id anônimo de `[e2e_id, ispb_pagador, repr(valor)]`), mesmo ISPB e mesmo valor:

```
=== A2: 3 eventos do MESMO cliente SEM id_recorrencia (identidade por e2e) ===
  webhook#1 -> 200   webhook#2 -> 200   webhook#3 -> 200
  lotes: [('rec_anon_ffbb03fcb67ef89b', 0, 3, [1, 2, 3]),
          ('rec_anon_69f117de386d5bef', 0, 3, [1, 2, 3]),
          ('rec_anon_056189b6a6085173', 0, 3, [1, 2, 3])]
  TOTAL AGENDADO: 9
```

Como o `e2e_id` é por transação, cada reenvio da mesma cobrança produz um checkpoint novo e o
contador reinicia — 9 tentativas na mesma janela, que é literalmente o número da R4. É o preço
declarado do P0-6b (sem `id_recorrencia` a CRAI não sabe que é o mesmo pagador), e por isso
registro como **resíduo, não bloqueio**; mas as docstrings de `_run_involuntary_pipeline` e de
`schedule_retry_pix` (`workflow.py:220-222`) afirmam o invariante sem essa ressalva.

### 3.4 `retry_exhausted` — a assimetria que ficou

`_run_involuntary_pipeline` deixou de escrever `retry_count`, mas continua escrevendo
`"retry_exhausted": False, "recovered": False, "pix_retry_schedule": None,
"next_retry_at": None, "dunning_sent": False` incondicionalmente (`api/app.py:529-531`) — todos
sobrescrevendo o checkpoint pelo mesmo mecanismo que a docstring descreve como o defeito.

Hoje não abre caminho novo, porque `decide_recovery` roteia por `retry_count`, não por
`retry_exhausted` (`workflow.py:154-166`), e `route_after_retry` (`main_agent.py:75`) só é
alcançado depois do nó de agendamento. Mas o efeito colateral é que, com a correção de (1),
**`retry_exhausted=True` deixou de ser alcançável pelo pipeline de Pix**: para
`schedule_retry_pix` devolver lista vazia seria preciso `restantes <= 0` (barrado antes por
`decide_recovery`) ou `inicio > prazo_final` (impossível, porque `schedule()` usa
`vencimento = agora`). A consequência é visível na saída da própria demo, no cenário que se
chama "Janela esgotada":

```
$ PYTHONIOENCODING=utf-8 python test_pipeline.py
  CHURN INVOLUNTARIO (PIX AUTOMATICO) - Janela esgotada - 3 de 3 usadas
  RN_pedro_003 | R$ 599.00 | cobranca recorrente falhada
[RACIOCINIO] Decisao: mensagem_pagamento (Pix Automatico -> boleto).
   Com plano de retentativa: 2/3
   Janela BACEN esgotada   : 0/3
```

`Janela BACEN esgotada: 0/3` com um cenário chamado "Janela esgotada — 3 de 3 usadas".
Verifiquei em worktree que `decide_recovery` já tinha o portão `usadas < limite` em
`baseline-pre-sprint` (`crai/agent/workflow.py:149-151`), logo é **pré-existente**, não
introduzido por `317a0eb` — resíduo de BAIXA gravidade, com a ressalva de que é uma linha que a
banca vê. O gate do Sprint 2 (`RN_maria_001` mostra 3/3) está verde:

```
[PIX-RETRY] RN_maria_001: 3 tentativa(s) agendada(s) via previsao do Payday Engine | prazo BACEN: 11/09/2026
[PIX-RETRY]   tentativa 1/3: 05/09 16:54 | R$ 299.90
[PIX-RETRY]   tentativa 2/3: 06/09 16:54 | R$ 299.90
[PIX-RETRY]   tentativa 3/3: 07/09 16:54 | R$ 299.90
```

### 3.5 A correção foi aplicada em um dos dois lugares que montam o `initial`

`test_pipeline.py:67-81` — a demo da banca — monta o mesmo dicionário à mão e continua
escrevendo `"retry_count": tentativas_usadas` incondicionalmente, chamando `crai_agent.ainvoke`
direto, sem passar por `_run_involuntary_pipeline`. Hoje é inofensivo (os três cenários usam
`id_recorrencia` distintos e cada execução é um processo novo), mas é a cópia não corrigida do
código que causou o bloqueio, dentro do arquivo que a apresentação executa. **BAIXA.**

---

## 4. O item N-12 e a catraca de encoding

### 4.1 R4-4 — PARCIAL: a contagem está certa, o escopo declarado não

Rodei o **mesmo algoritmo da catraca** (tokenize, `STRING` + `FSTRING_MIDDLE`, excluindo
docstrings) nas três árvores, cada uma em worktree própria:

```
### 8ee67ae  (o commit anterior)
   agent/workflow.py                              1  [(201, 'U+2192')]
   churn_voluntary/voluntary_agent.py             2  [(118, 'U+2705'), (118, 'U+274C')]
   dunning/dunning_engine.py                      1  [(109, 'U+2192')]
   dunning/pix_automatico_retry.py                1  [(334, 'U+2192')]
   integrations/payment_gateway.py                1  [(546, 'U+2192')]
   ml/anomaly_detector.py                         2  [(145, 'U+2192'), (145, 'U+2192')]
   ml/failure_classifier.py                       1  [(250, 'U+2192')]
   scripts/preparar_amostra_real.py               3  [(93,'U+2192'),(115,'U+2192'),(228,'U+2192')]
   TOTAL NO PACOTE: 12

### BASELINE (2956a69)          TOTAL NO PACOTE: 7
### HEAD (317a0eb)             TOTAL NO PACOTE: 7
```

**Os números 12 e 7 batem exatamente**, e as 5 ocorrências introduzidas pelo diff foram de
fato removidas. Até aqui a alegação é verdadeira e é a primeira vez em cinco rodadas que um
número documentado sobrevive à medição sem ressalva.

O que torna o veredito **PARCIAL** é a fronteira que o inventário declara. A mesma varredura,
no resto do projeto:

```
-- fora do escopo da catraca (resto de crai/, exceto o pacote) --
   test_pipeline.py                              15
   TOTAL FORA DO PACOTE: 15      (idêntico no baseline, no 8ee67ae e no HEAD)
```

A linha N-12 nova do README abre com *"**7 ocorrências restantes**"*. O projeto tem **22**. O
arquivo com as outras 15 é `test_pipeline.py` — que é o que a linha N-12 **antiga** nomeava, que
a R4 declarou atribuição falsa, e que a linha nova **removeu da tabela**. Ele continua morrendo
no console padrão do Windows, antes de executar qualquer coisa:

```
$ PYTHONIOENCODING=cp1252 python test_pipeline.py
  File "D:\PTI\crai\test_pipeline.py", line 103, in main
    print("\n\U0001f680 CRAI v2 - Teste dos Dois Pipelines (Involuntario + Voluntario)\n")
UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f680' in position 2
```

Ou seja: a tabela trocou uma atribuição incompleta ("é do `test_pipeline.py`") por outra
("são estas 7 do pacote"), e o arquivo que a demo executa saiu da tabela justamente na rodada
que se propôs a corrigir a tabela. **Gravidade BAIXA** (é dívida documental, e a dívida real
já estava declarada como resolvida no Sprint 5), mas é literalmente a mesma classe de defeito
que reprovou as rodadas 3 e 4.

### 4.2 A catraca mede o que diz medir? — PARCIAL

Testado o algoritmo, não a prosa:

- **O que ela mede bem.** A exclusão de docstrings por `lineno` funciona (docstring
  multi-linha é um único token cujo `start[0]` é a linha de abertura). A inclusão de
  `FSTRING_MIDDLE` é necessária e correta no 3.12 — sem ela a contagem cai de 12 para 4,
  exatamente como a docstring do arquivo declara. As 5 asserções falham por **conteúdo** em
  `8ee67ae`, com as linhas e os codepoints certos:

```
$ cd <worktree 8ee67ae>/crai; python -m pytest tests/test_encoding_saida.py -v --tb=short
FAILED ...[ml/failure_classifier.py]      AssertionError: N-12 voltou em ml/failure_classifier.py: linha 250 (U+2192)
FAILED ...[integrations/payment_gateway.py] AssertionError: N-12 voltou em integrations/payment_gateway.py: linha 546 (U+2192)
FAILED ...[scripts/preparar_amostra_real.py] AssertionError: linha 93 (U+2192), linha 115 (U+2192), linha 228 (U+2192)
FAILED ...test_nenhum_modulo_novo_entrou_na_lista
FAILED ...test_o_total_bate_com_o_medido_na_auditoria  assert 12 <= 7
PASSED ...test_a_divida_herdada_nao_cresceu
========================= 5 failed, 1 passed in 0.84s =========================
```

- **Onde a prosa excede a medição.** A docstring afirma: *"qualquer ocorrência NOVA, **em
  qualquer módulo**, falha aqui"*. Falso por construção: `PACOTE = Path(__file__).parent.parent
  / "crai"`, isto é, só `crai/crai/**`. A prova está na suíte verde de hoje — existem **15**
  ocorrências vivas em `crai/test_pipeline.py` e os 6 testes passam:

```
$ cd D:\PTI\crai; PYTHONIOENCODING=utf-8 python -m pytest tests/test_encoding_saida.py -q
6 passed
```

  O `demo_runner.py` do Sprint 5, que a própria tabela aponta como a solução sistêmica, também
  nascerá fora do escopo se ficar na raiz de `crai/`.

- **Segunda imprecisão, menor.** O README diz que as 7 estão *"todas em strings que chegam ao
  stdout"*. `dunning/pix_automatico_retry.py:334` é a mensagem de uma
  `PixRetryPolicyViolation` — chega ao stderr por traceback, não ao stdout por `print`. Não
  muda o desfecho (stderr também é cp1252), mas a frase não descreve o que foi medido.

- **Evasão possível, não explorada hoje:** a varredura roda sobre `token.string`, que é o texto
  **fonte**. Um `"\u2192"` escrito como escape passa despercebido e imprime o mesmo caractere.
  Registro como limitação conhecida da catraca, não como defeito.

**Gravidade BAIXA.** A catraca é útil e faz o que promete dentro de `crai/crai/`; o que não faz
é o "qualquer módulo" da própria docstring.

---

## 5. Parte B — o resto do checklist

### 5.1 Exceção nova escapando ao handler do FastAPI — nenhuma pelo Pix

Fuzz dirigido ao código que `317a0eb` mudou (parâmetro `teto`, caminho de centavos, entrada do
`ainvoke` sem `retry_count`): 20 formas de valor × 5 campos monetários × 4 formas de identidade
× 4 envelopes, **todos assinados**, pipeline real:

```
=== B1: fuzz de 5xx dirigido ao codigo novo (teto + retry_count omitido) ===
  PAYLOADS: 1600 | CODIGOS: {200: 1212, 400: 80, 422: 308}
  5xx: NENHUM
```

Conferido também que nenhum 200 entra no pipeline sem identidade (o cenário do P0-6):

```
  (status, pipeline_true) -> {(422, False): 8, (200, False): 24}
     [payloads sem id_recorrencia e sem e2e_id: ou 422, ou 200 com pipeline:false]
```

### 5.2 `/simulate/churn-risk` ainda devolve HTTP 500 — o bloqueio R3-2 não fechou inteiro

Com `ENV=development`, sem assinatura nenhuma, corpo JSON válido para o parser do Python:

```
  churn-risk days_since_last=NaN        -> 500 Internal Server Error
  churn-risk days_since_last=Infinity   -> 500 Internal Server Error
  churn-risk days_since_last=-Infinity  -> 500 Internal Server Error
  churn-risk features_used_30d=NaN      -> 500 Internal Server Error
  churn-risk features_used_30d=Infinity -> 500 Internal Server Error
  churn-risk features_used_30d=-Infinity-> 500 Internal Server Error

  payment-failed amount=NaN        -> 422 {"motivo":"valor_nao_utilizavel","campo":"amount",...}
  pix-falhado    valor=NaN         -> 422 {"motivo":"valor_nao_utilizavel","campo":"valor",...}
  payment-failed amount=Infinity   -> 422 ...
  pix-falhado    valor=Infinity    -> 422 ...
```

Causa, pelo traceback real:

```
--- traceback real (raise_server_exceptions=True) ---
    File ".../json/encoder.py", line 258, in iterencode
      return _iterencode(o, 0)
  ValueError: Out of range float values are not JSON compliant: nan
```

`SimulateChurnRisk` (`api/app.py:421-425`) declara `days_since_last: int` e
`features_used_30d: int`. O Pydantic recusa `nan`/`inf` **antes** do corpo da rota, o FastAPI
monta um 422 cujo campo `input` contém o próprio `nan`, e o `json.dumps` desse 422 estoura —
o 500 sai da serialização da mensagem de erro. `_contador_de_simulacao` (`api/app.py:181-199`),
que existe exatamente para barrar isso, **nunca é alcançado** nesta classe de entrada: é ramo
morto para `NaN`/`Infinity`, do mesmo tipo que a R4 encontrou no teto de magnitude.

Atribuição, medida em worktree do baseline:

```
raiz: C:/Users/peide/AppData/Local/Temp/claude/a1r5/base/crai   (baseline-pre-sprint)
  churn-risk days_since_last=NaN   -> 500 Internal Server Error
  payment-failed amount=NaN        -> 500 Internal Server Error
  pix-falhado    valor=NaN         -> 200 {"status":"pipeline_executado","id_recorrencia":"RN"}
```

Não é defeito **novo**: existia no baseline. Mas o bloqueio **R3-2** dizia literalmente
*"`/simulate/payment-failed` e `/simulate/churn-risk` deixam de dar 500"*, a R4 o marcou
**CONFIRMADO** com uma sonda que não incluiu os literais `NaN`/`Infinity`, e os outros dois
endpoints do mesmo grupo tratam esses literais corretamente. O `/simulate/churn-risk` é o único
que sobrou, e é um 5xx alcançável na configuração da demo. **Gravidade MÉDIA.**

### 5.3 `degradacoes` — nenhum caminho novo ignora a lista

Sete formas de evento degradado contra o adaptador real:

```
  valor ilegivel         -> degr=['envelope_ausente', 'valor_ilegivel']         valor=0.0
  valor ausente          -> degr=['envelope_ausente', 'valor_ausente']          valor=0.0
  valor negativo         -> degr=['envelope_ausente', 'valor_nao_positivo']     valor=-5.0
  centavos ilegiveis     -> degr=['envelope_ausente', 'valor_ilegivel']         valor=0.0
  envelope ausente       -> degr=['envelope_ausente']                           valor=10.0
  status desconhecido    -> degr=['envelope_ausente', 'status_desconhecido']    status='desconhecido'
  identidade estrutura   -> degr=['envelope_ausente', 'identificacao_ilegivel'] valor=10.0
  DEGRADACOES_CONHECIDAS = ['envelope_ausente', 'identificacao_ilegivel', 'lote_de_1',
                            'status_desconhecido', 'valor_ausente', 'valor_ilegivel',
                            'valor_nao_positivo']
```

Toda degradação produzida está no conjunto declarado, e o caminho de centavos com o `teto` novo
registra `valor_ilegivel` como o caminho em reais. O único descarte da lista continua sendo o
resíduo N-6 já declarado no README (`store_encrypted_pix_key`, fora do pipeline ativo).

### 5.4 `thread_id` — não achei dois clientes distintos colidindo

Dez pares construídos para forçar colisão, incluindo o ataque de separador que motivou o P0-6b:

```
  e2e com | vs ispb com |    A=rec_anon_76fc6d6d0e8e1750  B=rec_anon_c2ead5b58270676c  COLIDEM? False
  valor 1 vs 1.0             A=rec_anon_b46f9b6870f6f3a5  B=rec_anon_7a8f722f9cf1f4e7  COLIDEM? False
  valor True vs 1            A=rec_anon_a893f3e98745531d  B=rec_anon_b46f9b6870f6f3a5  COLIDEM? False
  ispb None vs ''            A=rec_anon_a9169859dbce17ac  B=rec_anon_a9169859dbce17ac  COLIDEM? True   (mesmo cliente)
  valor ausente vs 0         A=rec_anon_168a2f2a2d2c5b22  B=rec_anon_168a2f2a2d2c5b22  COLIDEM? True   (mesmo cliente)
  id_rec 'RN1' vs 'RN1 '     A=RN1                        B=RN1                        COLIDEM? True   (strip deliberado)
  id_rec 0 vs ''             A=rec_anon_65e0bc2f5ed553db  B=rec_anon_65e0bc2f5ed553db  COLIDEM? True   (ver nota)
  id_rec False vs ''         A=rec_anon_65e0bc2f5ed553db  B=rec_anon_65e0bc2f5ed553db  COLIDEM? True   (ver nota)
  id_rec '0' vs 0            A=0                          B=rec_anon_5c6d4249b33d744e  COLIDEM? False  (ver nota)
  e2e '[a,b]' vs lista       A=rec_anon_4e35871ecaeb3afa  B=rec_anon_4454402f4396c1d3  COLIDEM? False
```

As colisões observadas são normalização (`strip`) ou o mesmo pagador. **Nota (BAIXA, dívida
nova):** `_thread_id` faz `str(evento.get("id_recorrencia") or "")`, e `0`/`False` são falsos em
Python — um `id_recorrencia` numérico igual a `0` é tratado como **ausente** e cai no caminho
anônimo, enquanto a string `"0"` vira o `thread_id` `"0"`. O mesmo identificador em dois
checkpoints diferentes, dependendo do tipo JSON. Pré-existente, não introduzido por `317a0eb`.

### 5.5 Preenchimento para trás (P1-8) — 9.984 combinações, zero violação

Varredura com hipótese explícita nas três invariantes que o plano nomeia (intervalo < 1 dia,
passar do `prazo_final`, exceder 3 somadas às usadas), mais numeração e valor original.
Eixos: `tentativas_usadas` 0-3 × vencimento de −192h a +24h (incluindo as bordas −168h/−167h e
−144h/−145h, onde a janela fecha) × dia previsto de −4 a +11 × confiança
{0,0 / 0,59 / 0,60 / 1,0} × deslocamento de minutos {0, 7, 59} para dessincronizar `agora` do
vencimento:

```
=== B5: preenchimento para tras — varredura de invariantes ===
  combinacoes checadas: 9984 | agendamentos vazios: 3456
  VIOLACOES: 0
```

Nenhuma `PixRetryPolicyViolation`, nenhuma exceção não prevista, nenhum intervalo abaixo de
1 dia, nenhuma data além do prazo, nenhum total acima de 3, numeração sempre contígua a partir
de `tentativas_usadas + 1`. **A política em si continua correta** — como já era na rodada 3. O
defeito de §3.1 não está nela: está em quem decide o `tentativas_usadas` que ela recebe.

---

## 6. O que resistiu

Esta rodada tem mais coisa fechada do que qualquer uma das anteriores, e vale dizer sem ressalva:

- **O teto de magnitude fechou de verdade**, depois de três rodadas. A razão entre os dois
  caminhos é **1,0000×** medida por busca binária, vale por número e por string nos dois
  locales, e o ramo que a R4 provou ser morto agora é quem decide a fronteira.
- **O limite BACEN parou de atravessar os webhooks pelo caminho que a R4 cobrou**: três
  webhooks do mesmo `id_recorrencia` agendam 3, não 9. Testei a premissa de merge do LangGraph
  **diretamente** (dump do checkpoint entre invocações), em vez de aceitá-la: omitir a chave de
  fato preserva o valor acumulado.
- **Os testes de regressão falham por comportamento em `8ee67ae`** — 6 no `payment_gateway` e
  no `payment_isolation`, 5 no `test_encoding_saida`, com as linhas e codepoints corretos. Zero
  `ImportError`. O 12º é declarado catraca, e a declaração está certa. O Gate GA1 está cumprido
  para os itens 1, 2 e 3.
- **A contagem do N-12 é reproduzível ao número**: 12 em `8ee67ae`, 7 no baseline, 7 no HEAD,
  arquivo por arquivo e linha por linha. Primeira vez em cinco rodadas.
- **A política de retentativa continua correta**: 9.984 combinações, zero violação de invariante.
- **A borda de Pix continua sem 5xx**: 1.600 payloads assinados dirigidos ao código novo.
- **Não achei colisão de `thread_id` entre clientes distintos.**
- **Higiene:** `367 passed`, `models/` intacto, nenhum efeito colateral da suíte.

---

## 7. Veredito

# BLOQUEADO para o Sprint 3.

Os quatro bloqueios da rodada 4 foram atacados de frente, e três deles fecharam com prova
executável. Não é o caso de fingir que a rodada foi ruim: foi a melhor até aqui.

O que reprova é uma coisa só, e ela é grande: **a correção do limite BACEN trocou um erro
permissivo por um erro restritivo, e ninguém mediu o outro lado.** O `retry_count` passou a ser
preservado, mas não existe nada no state que o prenda a uma janela — nem uma data, nem um
identificador de ciclo. O invariante que roda hoje não é "3 tentativas por janela de 7 dias";
é "3 tentativas por `id_recorrencia`, para sempre". Reproduzido: o mesmo contrato, dois meses
depois, com o relógio da política adiantado e uma janela BACEN inteiramente nova, recebe
`chamadas a schedule: NENHUMA` e a frase *"as 3 tentativas da janela regulada do BACEN já foram
usadas"*. Num produto de retenção, isso desliga a recuperação de todo cliente reincidente a
partir do segundo ciclo — que é a população inteira que o produto existe para atender.

O agravante é o de sempre nesta série: a docstring que descreve a correção
(`api/app.py:498-513`, 16 linhas) explica com precisão o mecanismo do merge e **não menciona
que o contador nunca expira**. A alegação está certa sobre o que faz e silenciosa sobre o que
custa.

### Mínimo necessário para liberar, em ordem de gravidade

1. **Prender o `retry_count` a uma janela** (§3.1). O state precisa carregar a fronteira da
   janela em que as tentativas foram gastas (o `prazo_final` já calculado pela política serve),
   e `decide_recovery` — ou o nó de agendamento — precisa tratar um evento posterior a essa
   fronteira como janela nova, zerando o contador. **Testes obrigatórios, pela borda, os dois:**
   (a) dois webhooks do mesmo `id_recorrencia` **dentro** da janela → soma ≤ 3 (já existe,
   `TestA1R4LimiteBacenAtravessaOsWebhooks`); (b) dois webhooks do mesmo `id_recorrencia`
   separados por mais de `JANELA_DIAS` → o segundo recebe **3 tentativas novas**. Hoje (b) dá 0.
   Enquanto só (a) existir, os dois invariantes contraditórios passam no mesmo teste.

2. **O limite regulatório não pode ter como única guarda um `MemorySaver` em RAM** (§3.2).
   Duas saídas aceitáveis, e a segunda é barata: ou o checkpoint passa a ser persistente, ou o
   README declara a linha de dívida com a consequência explícita — *"reiniciar a API devolve 3
   tentativas a todos os clientes dentro da mesma janela"* — e o Sprint que for para produção a
   assume. O que não é aceitável é a docstring de `_run_involuntary_pipeline` afirmar o
   invariante sem essa ressalva, que é a mesma falha das rodadas 3 e 4.

3. **`/simulate/churn-risk` ainda devolve HTTP 500** (§5.2), em 6 entradas, com `ENV=development`
   e sem assinatura. É o bloqueio R3-2, que a R4 marcou CONFIRMADO com uma sonda incompleta. Os
   outros dois `/simulate/*` já tratam `NaN`/`Infinity` corretamente; falta este. Correção
   mínima: tipar os dois campos como `float` (ou `Any`) no `SimulateChurnRisk` e deixar
   `_contador_de_simulacao` decidir, que é o portão que já existe e hoje é ramo morto para essa
   entrada. Teste: os 6 casos acima → 422.

4. **A linha N-12 do README e a docstring da catraca** (§4.1, §4.2). A linha abre com
   "**7 ocorrências restantes**" e o projeto tem 22; as outras 15 estão em `test_pipeline.py`,
   que a linha antiga nomeava e a nova removeu da tabela, e que morre no console cp1252 antes
   de imprimir a primeira linha. E a docstring de `tests/test_encoding_saida.py` afirma
   "qualquer ocorrência NOVA, **em qualquer módulo**, falha aqui", o que é falso: o escopo é
   `crai/crai/**`. Ou a catraca passa a varrer `crai/` inteiro (e o número declarado vira 22),
   ou as duas frases passam a dizer "no pacote `crai/crai/`" e `test_pipeline.py` volta para a
   tabela com suas 15.

### Não bloqueiam — acrescentar à tabela de dívida

- O limite continua atravessável por eventos **sem `id_recorrencia`**: 3 eventos → 9 tentativas
  (§3.3). É o preço declarado do P0-6b, mas as duas docstrings que afirmam o invariante
  (`api/app.py` e `workflow.py:220-222`) não trazem a ressalva.
- `retry_exhausted=True` ficou **inalcançável** pelo pipeline de Pix (§3.4), e a demo imprime
  `Janela BACEN esgotada: 0/3` no cenário chamado "Janela esgotada — 3 de 3 usadas". Verificado
  em worktree que é **pré-existente** (o portão `usadas < limite` já estava no baseline), mas é
  uma linha que a banca lê.
- `test_pipeline.py:67-81` mantém a **cópia não corrigida** do `initial` com
  `"retry_count": tentativas_usadas` incondicional (§3.5), sem passar por
  `_run_involuntary_pipeline`.
- `_thread_id`: `id_recorrencia = 0` (inteiro) é tratado como ausente pelo `or ""` e cai no
  checkpoint anônimo, enquanto `"0"` (string) vira `thread_id` `"0"` (§5.4). Pré-existente.
- A catraca de encoding não detecta `"\u2192"` escrito como escape (§4.2).

### Já declarado, não repetir

P2-10, P2-11, P2-13, N-6, N-8, `synthetic_data.py` não calibrado, §4.6 (modelos em disco com
AUC 0,6797), `/webhooks/stripe` e `/webhooks/segment` com 500 em corpo não-JSON (R4 §3.7),
`data: "texto"` caindo na raiz em silêncio (R4 §2.3).

**Observação para o Sprint 3, não bloqueante** (repetida da R4 porque continua válida):
`TestEscolhaDoLimiar` se apoia numa margem de 0,0045 (`recall(0,30) = 0,8955` contra
`RECALL_MINIMO = 0,90`), e o Sprint 3 recalibra exatamente a curva que a sustenta.

---

## 8. Estado da árvore

```
$ cd D:\PTI\crai; PYTHONIOENCODING=utf-8 python -m pytest tests/ -q
367 passed, 3 warnings in 17.85s

$ git worktree list
D:/PTI                                             317a0eb [sprint/a1-auditoria]

$ git status --short
?? .claude/
?? crai/docs/AUDITORIA_01_R5.md

$ git log -1 --oneline
317a0eb sprint(a1): corrige os 4 bloqueios da re-auditoria A1-r4
```

As duas worktrees descartáveis (`C:\...\a1r5\prev` = `8ee67ae` e `C:\...\a1r5\base` =
`baseline-pre-sprint`) foram criadas fora de `D:\PTI` e removidas com
`git worktree remove --force`. **Nenhum `git checkout <ref> -- <caminho>` foi executado na
árvore principal.** Nenhum commit foi feito. Nenhum arquivo de produção foi alterado — o único
arquivo que esta auditoria escreve é `crai/docs/AUDITORIA_01_R5.md`. Os scripts de ataque
ficaram em `C:\Users\peide\AppData\Local\Temp\claude\a1r5\scripts`, fora do repositório. Todos
os testes rodaram com `PYTHONIOENCODING=utf-8`.

---

*Re-auditoria executada sem acesso às justificativas da implementação; mensagens de commit,
docstrings e README tratados como alegações a testar. Ataques rodados contra o pipeline real,
não stubado, com assinatura HMAC válida. Prova de regressão em duas worktrees descartáveis,
removidas ao fim.*

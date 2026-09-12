# AUDITORIA ADVERSARIAL A1 — RODADA 6

**Diff sob auditoria:** `317a0eb..8786d82` (correção da rodada 5)
**Contexto do sprint:** `baseline-pre-sprint (2956a69)..8786d82`
**Branch:** `sprint/a1-auditoria` · **HEAD:** `8786d824136aae790f36330683323a9dcc75319f`
**Data da medição:** 04/09/2026 · Windows 10, Python 3.12, console cp1252

---

## VEREDITO: 🔴 **BLOQUEADO** (GA1)

Um bloqueio 🔴, um 🟠 e dois 🟡.

O bloqueio é o mesmo defeito que a rodada 4 alegou fechar — **3 + 3 + 3 = 9
tentativas na mesma janela de 7 dias, todas numeradas 1, 2, 3** — ainda vivo,
alcançável pelo webhook assinado, **num único processo**. A rodada 4 fechou a
chegada *sequencial*; a chegada *concorrente* nunca foi fechada. E este diff
(`8786d82`) escreveu, em dois lugares, a afirmação de que o processo único
garante o limite. Ele não garante. A afirmação é nova e é falsa.

| ID | Severidade | Assunto | Introduzido por |
|---|---|---|---|
| **R6-1** | 🔴 bloqueio | Corrida no contador do BACEN: N webhooks concorrentes do mesmo `id_recorrencia` agendam 3×N tentativas numa janela, em processo único. A declaração `P1-14` afirma o contrário. | comportamento **pré-existente**; a **declaração falsa** é deste diff |
| **R6-2** | 🟠 bloqueio menor | 11 payloads assinados produzem **HTTP 500** em `/webhooks/segment` e 5 em `/webhooks/stripe`. Levantado na A1-r4 (`N-R4-2`), deliberadamente não corrigido, e **ausente da tabela de dívida do README** — que se declara completa. | pré-existente; a omissão na tabela é deste diff |
| **R6-3** | 🟡 dívida | A catraca de encoding e o README dizem varrer "a raiz do repositório"; a raiz medida é `crai/`. Na raiz real do repositório o inventário é **23**, não 22. | deste diff |
| **R6-4** | 🟡 dívida | README aponta a ocorrência N-12 em `crai/agent/workflow.py:243`; a medição diz **249**. | deste diff |

---

# 🔴 R6-1 — O limite do BACEN cai por concorrência, em processo único

## O que a rodada 5 afirma

`crai/crai/agent/main_agent.py:113-128` (linhas escritas por `8786d82`):

```
    #   1. Dois processos (dois workers do uvicorn, dois pods) têm memórias
    #      separadas. O mesmo `id_recorrencia` atendido por workers diferentes
    #      recebe 3 tentativas de cada um: 6 na mesma janela de 7 dias, contra
    #      o limite de 3. A CRAI hoje só é correta rodando em UM processo.
    ...
    # É dívida ASSUMIDA para a demo, não descuido: a POC roda em processo
    # único, e trocar o checkpointer por um persistente ...
    # Enquanto isso não acontecer, o limite regulatório é garantido pelo
    # processo, e o processo é a fronteira de correção do sistema.
```

E `crai/README.md`, linha `P1-14`:

> *"Dívida **assumida**, não descuido: a POC roda em processo único, **e é isso
> que garante o limite hoje**."*

Duas afirmações, a mesma: **um processo ⇒ no máximo 3 por janela.**

## A medição

Script: `conc_sem_mock.py` — N requisições `POST /webhooks/pix-automatico`
assinadas, mesmo corpo, mesmo `id_recorrencia`, disparadas com
`asyncio.gather` sobre `httpx.ASGITransport` (um único processo, um único
event loop, um único `MemorySaver`). **Nenhum monkeypatch, nenhum mock, nenhum
espião**: a contagem sai da própria trilha de auditoria que
`pix_automatico_retry.py` imprime (`[PIX-RETRY] tentativa N/3: ...`).

```
$ cd /d/PTI/crai && PYTHONIOENCODING=utf-8 PYTHONPATH=/d/PTI/crai \
    python .../conc_sem_mock.py <N> RN_sem_mock_00<N>

----- N=1 -----
requisicoes concorrentes: 1  status: [200]
tentativas efetivamente agendadas (linhas [PIX-RETRY]): 3
numeros: ['1', '2', '3']
datas  : ['05/09 21:48', '08/09 21:48', '11/09 21:48']
retry_count no checkpoint: 3 | pix_janela_ate: 2026-09-11 21:48:44.424542

----- N=2 -----
requisicoes concorrentes: 2  status: [200, 200]
tentativas efetivamente agendadas (linhas [PIX-RETRY]): 6
numeros: ['1', '2', '3', '1', '2', '3']
datas  : ['05/09 21:48', '08/09 21:48', '11/09 21:48', '05/09 21:48', '08/09 21:48', '11/09 21:48']
retry_count no checkpoint: 3 | pix_janela_ate: 2026-09-11 21:48:53.998020

----- N=3 -----
requisicoes concorrentes: 3  status: [200, 200, 200]
tentativas efetivamente agendadas (linhas [PIX-RETRY]): 9
numeros: ['1', '2', '3', '1', '2', '3', '1', '2', '3']
datas  : ['05/09 21:49', '08/09 21:49', '11/09 21:49', '05/09 21:49', '08/09 21:49', '11/09 21:49', '05/09 21:49', '08/09 21:49', '11/09 21:49']
retry_count no checkpoint: 3 | pix_janela_ate: 2026-09-11 21:49:04.680126
```

**Nove instruções de pagamento comprometidas na mesma janela de 7 dias, para o
mesmo `id_recorrencia`, nas mesmas três datas, todas numeradas 1, 2, 3** — e o
checkpoint diz `retry_count: 3`. O sistema não sabe que fez o que fez.

Compare com a descrição que o próprio `_run_involuntary_pipeline` dá do defeito
que a rodada 4 corrigiu (`crai/crai/api/app.py:545-551`):

> *"três webhooks do mesmo cliente agendavam 3 + 3 + 3 = **nove** tentativas na
> mesma janela de 7 dias, todas numeradas 1, 2, 3."*

É a mesma frase. É o mesmo número. É o mesmo defeito.

## O controle — não é o payload, é a concorrência

Os mesmos dois webhooks, mesmo corpo, mesmo `id_recorrencia`, **em sequência**:

```
$ python .../conc_seq.py
STATUS: [200, 200]
LOTES agendados (numero das tentativas): [[1, 2, 3]]
TOTAL de tentativas agendadas na MESMA janela: 3
retry_count no checkpoint: 3
pix_janela_ate: 2026-09-11 21:43:19.212901
```

Sequencial: 3 (correto). Concorrente: 6 e 9. A variável isolada é a
concorrência.

## Por que acontece

`crai_agent.ainvoke(initial, config)` lê o checkpoint do `thread_id` na
entrada e o grava nó a nó. Não há nenhuma exclusão mútua por `thread_id` em
lugar nenhum do caminho — nem em `_run_involuntary_pipeline`, nem no grafo. O
pipeline tem `await` em `diagnose_failure`, `check_anomaly`, `infer_payday` e
na própria `_pix_retry.schedule`, então dois `ainvoke` do mesmo `thread_id`
intercalam de verdade: os dois leem `retry_count = 0`, os dois passam por
`decide_recovery` com `usadas = 0`, os dois chamam
`PixAutomaticoRetryPolicy.schedule(tentativas_usadas=0)`, e a política — que
está correta — devolve 3 para cada um. A validação `_validar` também está
correta: ela confere `tentativas_usadas + len(tentativas) <= 3` dentro de cada
chamada, e cada chamada isolada respeita o limite. **O invariante quebrado é
entre chamadas, e não existe nada que o proteja.**

## Por que está DENTRO do escopo

1. `sprints.md`, linha 340 — o prompt do auditor que o próprio plano manda
   colar, textualmente: *"existe combinação que agenda < 1 dia de intervalo,
   passa do `prazo_final`, **ou excede 3 tentativas somadas às usadas**?"*
   Existe. É esta.
2. `sprints.md` §3 lista o que está fora de escopo. Concorrência, corrida,
   lock por `thread_id` — nada disso aparece. O único item de lock excluído é
   o `P2-13`, que é sobre o cofre de chaves Pix (`store_encrypted_pix_key`),
   outro arquivo e outro assunto.
3. O webhook assinado é a porta de produção, e chegada concorrente/duplicada é
   o modo normal de operação de um PSP (entrega *at-least-once*). Não é
   condição exótica: é o default de qualquer app ASGI.
4. **A afirmação falsa é deste diff.** Não estou cobrando a corrida como
   defeito novo — ela existe desde antes (medido em `317a0eb`, abaixo). Estou
   cobrando que `8786d82` escreveu, em dois entregáveis, que o processo único
   garante o limite. Pelo critério do próprio projeto — *"declarar errado é
   pior que não declarar"* — isso é o defeito.

Medição em `317a0eb`, em worktree descartável, confirmando que o
**comportamento** é pré-existente e só a **declaração** é nova:

```
$ cd <worktree 317a0eb>/crai && python .../conc_sem_mock.py 3 RN_wt_317
requisicoes concorrentes: 3  status: [200, 200, 200]
tentativas efetivamente agendadas (linhas [PIX-RETRY]): 9
numeros: ['1', '2', '3', '1', '2', '3', '1', '2', '3']
retry_count no checkpoint: 3 | pix_janela_ate: None
```

## Por que isto não é o `P1-14`

`P1-14` descreve **memórias separadas** (dois processos, dois `MemorySaver`),
e o conserto que ele aponta é *"trocar o checkpointer por `SqliteSaver` /
`PostgresSaver`"* — trabalho de produção, corretamente adiado. O defeito aqui
é **uma memória só, duas leituras concorrentes**, e trocar o checkpointer
**não conserta**: um `SqliteSaver` sem transação serializada tem exatamente a
mesma corrida. São defeitos diferentes com consertos diferentes, e o segundo
não está declarado em lugar nenhum.

## Mínimo para liberar

Uma das duas:

- **Fechar:** serializar por `thread_id` no ponto onde o `ainvoke` acontece —
  um `asyncio.Lock` por `id_recorrencia` em `_run_involuntary_pipeline` fecha o
  caso de processo único por inteiro. É uma dezena de linhas, não uma migração,
  e devolve a verdade às duas frases já escritas. Com teste de regressão que
  reprove antes (fácil: o script acima, que hoje mede 9).
- **Ou declarar honestamente:** corrigir `P1-14` e o comentário de
  `main_agent.py` para dizer o que é verdade — que o limite **não** é garantido
  nem em processo único, que a fronteira de correção é a chegada sequencial, e
  qual é a consequência (9 instruções contra o limite legal de 3). Nesse caso
  a declaração precisa dizer também que o `retry_count` do checkpoint
  **subnotifica** o que foi comprometido, porque essa é a parte que impede
  qualquer auditoria posterior de perceber o estouro.

---

# 🟠 R6-2 — 500 em webhook assinado, e a tabela de dívida que se diz completa

## A afirmação

`crai/README.md`, abertura da seção *Dívida técnica conhecida*, editada por
este diff:

> *"Itens levantados nas auditorias adversariais A1 e suas re-rodadas (r2 a r5)
> dos sprints de 31/08–04/09 e **deliberadamente não corrigidos antes da
> demo** ... **Estão aqui para não virarem dívida esquecida.**"*

É uma afirmação de completude. Este diff acrescentou três linhas novas à
tabela (`P1-14`, `N-13`, `N-14`) exatamente por essa razão. Falta uma.

`docs/AUDITORIA_01_R4.md`, linha 42:

> `| **N-R4-2** | /webhooks/stripe e /webhooks/segment fazem parte da "borda de
> entrada blindada" | N/A — pré-existente | §3.7 — corpo assinado que não é JSON
> → HTTP 500 nos dois ... | MÉDIA (fora do escopo do plano) |`

Item levantado numa re-rodada A1, deliberadamente não corrigido, **fora da
tabela**. É a definição de dívida esquecida.

## A medição — e a classe é maior do que a A1-r4 registrou

A A1-r4 registrou um caso: *corpo assinado que não é JSON*. Medindo com
assinatura **válida** (HMAC-SHA1 do corpo cru para o Segment, HMAC-SHA256
`t=<ts>,v1=<hmac>` para o Stripe) e `TestClient(raise_server_exceptions=False)`:

```
$ python .../fuzz_segment.py
SEG 200 b'{}'
SEG 500 b'not json'
SEG 500 b'[]'
SEG 500 b'"texto"'
SEG 500 b'5'
SEG 500 b'null'
SEG 500 b'{"userId":null}'
SEG 500 b'{"userId":[1],"event":"x"}'
SEG 200 b'{"event":123}'
SEG 500 b'{"userId":"u","event":"x","properties":"nao-dict"}'
SEG 500 b'{"userId":"u","event":"x","properties":[1,2]}'
SEG 200 b'{"userId":"u","event":"Cancellation Page Viewed","properties":{"days_since_...
SEG 200 b'{"userId":"u","event":"Cancellation Page Viewed","properties":{"days_since_...
SEG 200 b'{"userId":"u","event":"Cancellation Page Viewed","properties":{"days_since_...
SEG 200 b'{"userId":"u","event":"Cancellation Page Viewed","properties":{"days_since_...
SEG 500 b'{"userId":"u","event":"Cancellation Page Viewed","properties":{"billing_pro...
SEG 500 b'{"userId":{"a":1},"event":"Session Started"}'

5xx no /webhooks/segment ASSINADO: 11
```

```
$ python .../fuzz.py     (trecho: /webhooks/stripe assinado)
STR 200 b'{}'
STR 500 b'not json'
STR 500 b'[]'
STR 200 b'{"type":"invoice.payment_failed"}'
STR 500 b'{"type":"invoice.payment_failed","data":{"object":{"amount_due":"abc"}}}'
STR 500 b'{"type":"invoice.payment_failed","data":{"object":{"amount_due":null}}}'
STR 500 b'{"type":"invoice.payment_failed","data":"nao-dict"}'
STR 200 b'{"type":"invoice.payment_failed","data":{"object":{"amount_due":1e999}}}'

===== 5xx ENCONTRADOS: 5 =====
('stripe', "b'not json'", 500, 'Internal Server Error')
('stripe', "b'[]'", 500, 'Internal Server Error')
('stripe', 'b\'{"type":"invoice.payment_failed","data":{"object":{"amount_due":"abc"}\'', 500, 'Internal Server Error')
('stripe', 'b\'{"type":"invoice.payment_failed","data":{"object":{"amount_due":null}}\'', 500, 'Internal Server Error')
('stripe', 'b\'{"type":"invoice.payment_failed","data":"nao-dict"}\'', 500, 'Internal Server Error')
```

Onde cada um morre (traceback real, `raise_server_exceptions=True`, último
quadro dentro de `crai/`):

```
--- b'not json'
    ULTIMO QUADRO CRAI: File "D:\PTI\crai\crai\api\app.py", line 459, in segment_webhook
    ERRO: json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
--- b'[]'
    ULTIMO QUADRO CRAI: File "D:\PTI\crai\crai\api\app.py", line 461, in segment_webhook
    ERRO: AttributeError: 'list' object has no attribute 'get'
--- b'{"userId":null}'
    ULTIMO QUADRO CRAI: File "D:\PTI\crai\crai\integrations\hubspot_crm.py", line 109, in register_retention_cycle
    ERRO: TypeError: 'NoneType' object is not subscriptable
--- b'{"userId":[1],"event":"x"}'
    ULTIMO QUADRO CRAI: File "D:\PTI\crai\crai\api\app.py", line 605, in _run_voluntary_pipeline
    ERRO: TypeError: unhashable type: 'list'
--- b'{"userId":"u","event":"x","properties":"nao-dict"}'
    ULTIMO QUADRO CRAI: File "D:\PTI\crai\crai\api\app.py", line 600, in _run_voluntary_pipeline
    ERRO: AttributeError: 'str' object has no attribute 'get'
--- b'{"userId":"u",...,"properties":{"billing_profile":[1]}}'
    ULTIMO QUADRO CRAI: File "D:\PTI\crai\crai\churn_voluntary\offer_bandit.py", line 110, in conversion_rates
    ERRO: TypeError: unhashable type: 'list'
--- b'{"userId":{"a":1},"event":"Session Started"}'
    ULTIMO QUADRO CRAI: File "D:\PTI\crai\crai\api\app.py", line 605, in _run_voluntary_pipeline
    ERRO: TypeError: unhashable type: 'dict'
```

Dois pontos que mudam o enquadramento da A1-r4:

- **Não é só "corpo não-JSON".** `{"userId": null}`, `{"userId": [1]}`,
  `{"properties": "nao-dict"}` são JSON perfeitamente bem-formado. São formas
  que um provedor real erra.
- **Cinco dos sete morrem dentro de `crai/api/app.py`**, não dentro de
  `churn_voluntary/`. A exclusão de escopo do `sprints.md` (*"❌ Refatorar
  `churn_voluntary/` — está funcionando, não se mexe"*) **não cobre** a borda
  de entrada. E o objetivo declarado do Sprint 1 é, textualmente: *"nenhum
  payload de PSP, por mais torto que seja, derruba a API ou entra no pipeline
  em silêncio."*

## Por que 🟠 e não 🔴

Porque a A1-r4 já classificou o item como pré-existente e fora do escopo de
tarefas do Sprint 1 (*"arquivo único: `payment_gateway.py`"*), e não vou
reabrir uma decisão de escopo já tomada. O que cobro é a **omissão na tabela**
— que é deste diff — e o fato de a classe medida ser mais larga do que o
registro da A1-r4 sugere.

## Mínimo para liberar

Acrescentar a linha à tabela de dívida do README, com a consequência escrita e
o tamanho real da classe (11 payloads no Segment, 5 no Stripe; cinco deles
estourando na própria `api/app.py`, não em `churn_voluntary/`). Corrigir é
melhor — é o mesmo `try/except` que o webhook de Pix já tem — mas declarar
honestamente basta para o gate.

---

# 🟡 R6-3 — "raiz do repositório" não é a raiz do repositório

`tests/test_encoding_saida.py:47-49`, escrito por este diff:

```python
# Raiz do repositório, não o pacote: a varredura precisa alcançar
# `test_pipeline.py`, que vive fora de `crai/` e é o primeiro comando do README.
RAIZ = Path(__file__).resolve().parent.parent
```

`__file__` é `D:\PTI\crai\tests\test_encoding_saida.py`. `.parent.parent` é
`D:\PTI\crai`. A raiz do repositório git é `D:\PTI`.

A docstring do arquivo promete mais: *"qualquer ocorrência NOVA, **em qualquer
arquivo `.py` do repositório**, falha aqui"*. O README repete: *"Medido com
`tokenize`, contando só literais fora de docstring, **a partir da raiz do
repositório**"*, e declara **22**.

Medindo com a mesma varredura (mesmo `tokenize`, mesmo filtro de docstring,
mesma tabela cp1252, mesma lista `IGNORADOS`) nas duas raízes:

```
$ python .../scan_enc.py /d/PTI/crai
crai/agent/workflow.py: 1  linhas=[249]
crai/churn_voluntary/voluntary_agent.py: 2  linhas=[118, 118]
crai/dunning/dunning_engine.py: 1  linhas=[109]
crai/dunning/pix_automatico_retry.py: 1  linhas=[355]
crai/ml/anomaly_detector.py: 2  linhas=[145, 145]
test_pipeline.py: 15  linhas=[47, 48, 55, 56, 89, 89, 103, 105, 107, 144, 144, 148, 159, 166, 171]
TOTAL 22

$ python .../scan_enc.py /d/PTI          # raiz REAL do repositório
archive/protótipos-pré-unificação/modulo_04_offer_bandit/src/visualizar.py: 1  linhas=[119]
crai/crai/agent/workflow.py: 1  linhas=[249]
crai/crai/churn_voluntary/voluntary_agent.py: 2  linhas=[118, 118]
crai/crai/dunning/dunning_engine.py: 1  linhas=[109]
crai/crai/dunning/pix_automatico_retry.py: 1  linhas=[355]
crai/crai/ml/anomaly_detector.py: 2  linhas=[145, 145]
crai/test_pipeline.py: 15  linhas=[...]
TOTAL 23
```

O inventário do repositório é **23**, não 22. A afirmação "qualquer arquivo
`.py` do repositório" é falsa: `archive/` inteiro fica de fora, e a catraca
não trava nada lá.

**Por que 🟡 e não 🟠:** o arquivo que falta é um protótipo arquivado, fora de
qualquer comando documentado, e a alegação de fundo — *"a contribuição líquida
deste diff para o N-12 é zero"* — **está correta e eu confirmei**. É a
afirmação que está larga demais, não o número que importa. Ainda assim é a
terceira rodada seguida em que a catraca afirma um escopo maior do que mede, e
por isso registro.

**Conserto:** ou `RAIZ = Path(__file__).resolve().parents[2]` e declarar 23,
ou trocar as duas frases para "a partir de `crai/`" e dizer que `archive/`
está deliberadamente fora.

---

# 🟡 R6-4 — Número de linha errado no README

`crai/README.md`, linha N-12, escrita por este diff, lista
`crai/agent/workflow.py:243`. Os outros cinco ponteiros conferem
(`voluntary_agent.py:118`, `dunning_engine.py:109`,
`pix_automatico_retry.py:355`, `anomaly_detector.py:145` ×2). Este não:

```
$ sed -n 241,251p crai/crai/agent/workflow.py | cat -n
     1                            f"BACEN já foram usadas")
     2          else:
     ...
     9              raciocinio.append("Decisão: mensagem_pagamento (Pix Automático → boleto).")
```

Linha 249, não 243. A árvore de trabalho está limpa em `8786d82`
(`git status --short` só mostra `.claude/`), então não é deriva de edição
posterior — o número saiu errado do commit. Deriva de seis linhas, sem
consequência funcional, mas o README é entregável e o ponteiro é o que alguém
vai seguir.

---

# O que eu verifiquei e estava certo

Esta seção existe para não dar a impressão de que só olhei o que quebrou. Tudo
abaixo foi medido, não lido.

### 1. A janela do BACEN expira, reancora e não é empurrável (chegada sequencial)

25 eventos em instantes aleatórios (seed 7) ao longo de 40 dias, mesmo
`id_recorrencia`, relógio congelado substituindo o `datetime` que os módulos já
importavam, checkpoint inspecionado após cada webhook:

```
evento                | retry_count | pix_janela_ate
05/09 01:57           | 3           | 2026-09-12 01:57:00
05/09 13:44           | 3           | 2026-09-12 01:57:00
06/09 00:21           | 3           | 2026-09-12 01:57:00
...  (10 eventos dentro da janela 1, prazo NUNCA se move)
13/09 03:30           | 3           | 2026-09-20 03:30:00   <- janela 2 abre
14/09 07:52           | 3           | 2026-09-20 03:30:00
...
21/09 08:15           | 3           | 2026-09-28 08:15:00   <- janela 3
28/09 10:53           | 3           | 2026-10-05 10:53:00   <- janela 4
10/10 18:03           | 3           | 2026-10-17 18:03:00   <- janela 5

lotes agendados:
  evento 05/09 01:57 -> numeros [1, 2, 3] datas ['06/09 01:57', '09/09 01:57', '12/09 01:57']
  evento 13/09 03:30 -> numeros [1, 2, 3] datas ['14/09 03:30', '17/09 03:30', '20/09 03:30']
  evento 21/09 08:15 -> numeros [1, 2, 3] datas ['22/09 08:15', '25/09 08:15', '28/09 08:15']
  evento 28/09 10:53 -> numeros [1, 2, 3] datas ['29/09 10:53', '02/10 10:53', '05/10 10:53']
  evento 10/10 18:03 -> numeros [1, 2, 3] datas ['11/10 18:03', '14/10 18:03', '17/10 18:03']

TOTAL de tentativas agendadas em 40 dias: 15
MAIOR numero de tentativas em qualquer janela DESLIZANTE de 7 dias: 3
```

Cinco janelas, três tentativas cada, numeradas 1..3 em cada uma. Nunca 4 numa
janela deslizante de 7 dias. Nenhum dos 20 eventos que caíram dentro de uma
janela aberta empurrou o prazo. A ancoragem por
`inicio_da_janela`/`fim_da_janela` faz o que promete: o prazo pertence à
cobrança que abriu a janela, e um evento posterior não o reancora. **Esta parte
da correção B1 é sólida.**

### 2. Os testes novos reprovam em `317a0eb` — e a distinção que o commit faz é honesta

Worktree descartável em `317a0eb`, com os três arquivos de teste desta correção
copiados por cima:

```
12 failed, 65 passed, 3 warnings in 10.70s
```

E, por tipo de erro:

```
test_payment_isolation.py:689: AssertionError: o nó de agendamento foi alcançado 1 vez(es). ...
test_payment_isolation.py:711: AssertionError: o checkpoint não guarda até quando o contador de tentativas vale. ...
test_payment_isolation.py:727: AssertionError: prazo após a segunda cobrança: None. Esperado datetime.datetime(2026, 11, 9, 9, 0) ...
test_payment_isolation.py:751: AssertionError: o segundo evento, ainda DENTRO da janela, empurrou o prazo para None. ...
test_payment_isolation.py:785: AttributeError: module 'crai.agent.workflow' has no attribute '_janela_vigente'
test_payment_isolation.py:794: AttributeError: module 'crai.agent.workflow' has no attribute '_janela_vigente'
json/encoder.py:258: ValueError: Out of range float values are not JSON compliant: nan
json/encoder.py:258: ValueError: Out of range float values are not JSON compliant: inf
json/encoder.py:258: ValueError: Out of range float values are not JSON compliant: -inf
json/encoder.py:258: ValueError: Out of range float values are not JSON compliant: nan
json/encoder.py:258: ValueError: Out of range float values are not JSON compliant: inf
json/encoder.py:258: ValueError: Out of range float values are not JSON compliant: -inf
```

Dez falhas de comportamento, duas por símbolo ausente. O commit declara
exatamente isto — *"Das 12, DEZ são falhas de comportamento
(AssertionError/ValueError). As outras 2 são AttributeError por símbolo ausente
e estão declaradas no próprio arquivo como testes unitários da regra nova, NÃO
como prova"* — e a declaração confere linha por linha. É a primeira rodada em
que a prova de regressão não precisou ser recontada pela auditoria. O truque de
congelar o relógio substituindo `datetime` (símbolo que existe nas duas pontas)
em vez de `_agora` (que só existe depois) é a razão de isso funcionar, e é o
motivo certo.

Os dois testes marcados como catraca — `test_o_portao_de_contadores_continua_de_pe`
e `test_a_varredura_alcanca_arquivo_fora_do_pacote` — de fato **passam** em
`317a0eb`, e ambos estão declarados como catraca na própria docstring. Isso é
o uso correto da distinção.

### 3. A catraca de encoding realmente distingue

Injetei um `U+2192` num módulo declarado limpo (`ml/failure_classifier.py`), na
worktree descartável:

```
E       AssertionError: total de 23 ocorrências contra 22 declaradas nas auditorias A1-r4 e A1-r5
E       assert 23 <= 22
FAILED tests/test_encoding_saida.py::TestN12Catraca::test_modulos_limpos_por_este_sprint_continuam_limpos[crai/ml/failure_classifier.py]
FAILED tests/test_encoding_saida.py::TestN12Catraca::test_nenhum_modulo_novo_entrou_na_lista
FAILED tests/test_encoding_saida.py::TestN12Catraca::test_o_total_bate_com_o_medido_na_auditoria
3 failed, 4 passed
```

Uma ocorrência nova derruba três testes. Não é catraca decorativa.

### 4. As 22 são idênticas nas duas pontas — a alegação B4 confere

Worktree em `baseline-pre-sprint` (`2956a69`), mesma varredura:

```
crai/agent/workflow.py: 1  linhas=[184]
crai/churn_voluntary/voluntary_agent.py: 2  linhas=[118, 118]
crai/dunning/dunning_engine.py: 1  linhas=[109]
crai/dunning/pix_automatico_retry.py: 1  linhas=[291]
crai/ml/anomaly_detector.py: 2  linhas=[145, 145]
test_pipeline.py: 15  linhas=[47, 48, 55, 56, 89, 89, 103, 105, 107, 144, 144, 148, 159, 166, 171]
TOTAL 22
```

Mesmos arquivos, mesmas contagens, e as 15 de `test_pipeline.py` nas **mesmas
15 linhas** que no `HEAD`. A contribuição líquida do diff para o N-12 é
**zero**, como o commit afirma. (Só as linhas dos módulos do pacote andaram,
porque o código cresceu.)

### 5. `test_pipeline.py` morre onde o README diz que morre

```
$ PYTHONIOENCODING=cp1252 python test_pipeline.py
  File "D:\PTI\crai\test_pipeline.py", line 103, in main
    print("\n\U0001f680 CRAI v2 ? Teste dos Dois Pipelines (Involunt?rio + Volunt?rio)\n")
UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f680' in position 2: character maps to <undefined>
```

Linha 103, exatamente. O aviso novo do README está certo, e o primeiro comando
do README de fato precisa de `PYTHONIOENCODING=utf-8` hoje.

### 6. O caminho de erro dos `/simulate/*` está fechado — B3 confere, e mais largo do que os três campos

Fuzz de 33 payloads no webhook de Pix assinado (valor string / null / lista /
dict / bool / `1e308` / negativo / zero / `0.001` / inteiro de 400 dígitos;
`id_recorrencia` lista / dict / vazio / 100 000 chars / unicode astral; envelope
lista / string / número / null / bool / vazio; `Infinity`, `-Infinity`, `NaN`
crus; `1e999`; corpo vazio; bytes nulos; 200 000 chars) + 19 payloads nos três
`/simulate/*` + 13 literais não-JSON + 15 casos de fronteira do handler de
validação:

```
===== 5xx ENCONTRADOS: 5 =====     (todos os 5 em /webhooks/stripe — R6-2)
```

Zero 5xx no `/webhooks/pix-automatico` e zero nos `/simulate/*`. E o corpo de
toda recusa é JSON **estrito** (verificado com `json.loads(..., parse_constant=)`
que levanta em qualquer literal cru):

```
LIT 422 /simulate/churn-risk b'{"user_id":"u","event":"Session Started","days_since_last":NaN}'
LIT 422 /simulate/churn-risk b'{"user_id":"u","event":"Session Started","days_since_last":Infinity}'
LIT 422 /simulate/churn-risk b'{"user_id":"u","event":"Session Started","features_used_30d":-Infinity}'
LIT 422 /simulate/churn-risk b'{"user_id":NaN}'
LIT 422 /simulate/churn-risk b'{"user_id":"u","event":"Session Started","days_since_last":1e999}'
LIT 422 /simulate/pix-falhado b'{"id_recorrencia":"RN","valor":NaN,"ispb_pagador":"1"}'
LIT 422 /simulate/pix-falhado b'{"id_recorrencia":"RN","valor":Infinity,"ispb_pagador":"1"}'
LIT 422 /simulate/pix-falhado b'{"id_recorrencia":NaN,"valor":1,"ispb_pagador":"1"}'
LIT 422 /simulate/pix-falhado b'{"id_recorrencia":"RN","valor":1e999,"ispb_pagador":"1"}'
LIT 422 /simulate/payment-failed b'{"customer_id":"c","amount":NaN}'
LIT 422 /simulate/payment-failed b'{"customer_id":"c","amount":Infinity}'
LIT 422 /simulate/payment-failed b'{"customer_id":NaN,"amount":1.0}'
LIT 422 /simulate/payment-failed b'{"customer_id":"c","amount":1e999}'
```

```
$ python .../fuzz2.py       (fronteiras do handler de validação)
400  utf8 invalido                  /simulate/churn-risk
422  body vazio                     /simulate/churn-risk
422  body nao-json                  /simulate/churn-risk
422  array no lugar do objeto       /simulate/churn-risk
422  aninhado 200                   /simulate/churn-risk
422  chave duplicada NaN            /simulate/churn-risk
422  float enorme em campo int      /simulate/churn-risk
422  negativo -1e999 em valor       /simulate/pix-falhado
422  valor lista                    /simulate/pix-falhado
422  valor dict                     /simulate/pix-falhado
422  id_rec dict com NaN            /simulate/pix-falhado
422  amount lista com Inf           /simulate/payment-failed
422  failure_code dict NaN          /simulate/payment-failed
400  utf8 invalido pix              /simulate/pix-falhado
200  query string                   /simulate/churn-risk?x=NaN

===== problemas: 0
```

A alegação do commit — *"Sanear o eco resolve a classe inteira, não só estes
três campos"* — se sustenta para a classe que ela nomeia
(`RequestValidationError` com eco não serializável), em rotas e campos que o
teste não toca. E o contrapeso confere: `_contador_de_simulacao` continua
barrando o inteiro de 401 dígitos com 422 e `detail.campo == "days_since_last"`.

### 7. `pix_janela_ate` não é escrivível de fora

```
$ grep -rn "pix_janela_ate" /d/PTI/crai --include=*.py
```

Fora de docstrings, comentários e testes, os únicos pontos de **escrita** são
`crai/agent/workflow.py:257`, `:298` e `:309` — os três dentro de
`decide_recovery`/`schedule_retry_pix`. `_run_involuntary_pipeline` de fato
omite a chave (e omite `retry_count`, e `retries_done` só é preenchido pelo
caminho de cartão, que hoje nem chama o pipeline). A justificativa defensiva de
`_janela_vigente` — *"O campo só é escrito aqui ... não há terceiro valor
possível"* — é verdadeira: não achei entrada externa capaz de plantar um
`pix_janela_ate` corrompido.

### 8. O `N-13` (`retry_exhausted` inalcançável) está declarado corretamente

Confirmado por análise do caminho: com a janela aberta,
`inicio = max(agora, vencimento+1d) <= prazo_final` sempre, e `decide_recovery`
já barra `usadas >= 3` antes do nó. `schedule` nunca devolve `[]` no caminho de
Pix, logo `retry_exhausted = True` é inalcançável. A tabela do README descreve
isso com o efeito certo (o rótulo `0/3` da demo) e aponta o Sprint 5. Declaração
honesta.

### 9. Suíte e proveniência

```
$ cd /d/PTI/crai && PYTHONIOENCODING=utf-8 python -m pytest -q
382 passed, 3 warnings in 23.62s
```

382, como o commit diz (367 → 382).

```
$ python -c "...sha256(open('data/real/amostra_300.csv','rb').read())..."
linhas: 301
sha256: d800c60f765b245bfff29e5c258eb34c6cb1a2f54c8dd4c3a69b7bb794de2f0f
```

Bate com o `DATA_CARD.md` (linha 6) e com as 301 linhas prometidas.

---

# O que eu NÃO consegui verificar

Limites explícitos desta medição. Nada aqui é insinuação de defeito — é o
contorno do que a auditoria alcança.

1. **Concorrência real com múltiplos processos.** Medi a corrida dentro de um
   event loop (`asyncio.gather` sobre `ASGITransport`). Não subi `uvicorn
   --workers 2`. O `P1-14` afirma que dois workers dão 6 tentativas; é plausível
   e consistente com o que medi, mas **não medi**. A parte que medi é a que o
   `P1-14` diz estar segura.

2. **A janela real do BACEN não é o que o código chama de vencimento.** Quando
   uma janela expira, `schedule_retry_pix` ancora a janela nova em `agora` — o
   instante do webhook — e não no vencimento real da cobrança, que o payload do
   PSP não carrega. Se o PSP entregar o evento com atraso, a janela nova nasce
   deslocada para a frente e as tentativas podem cair fora dos 7 dias corridos
   contados do vencimento verdadeiro. Não consigo medir isso sem um campo de
   vencimento no payload, que não existe. Registro como limite de medição, não
   como achado.

3. **A regra do BACEN em si.** Auditei o código contra a regra como o projeto a
   descreve (3 tentativas em 7 dias corridos do vencimento, duas janelas
   automáticas do dia do vencimento não contam). Não fui à norma. Se a leitura
   regulatória estiver errada, todo o resto está construído sobre ela.

4. **Se a janela deve ser por cobrança ou deslizante.** O código implementa
   "por cobrança": janelas consecutivas podem colocar tentativas a 1 dia de
   distância na virada (última da janela N no dia 7, primeira da N+1 no dia 8).
   Numa janela **deslizante** de 7 dias isso passaria de 3. Adotei a leitura do
   projeto (por cobrança) porque é a que a norma citada sustenta, e sob ela o
   estresse de 40 dias fecha em 3. Registro a ambiguidade.

5. **Os modelos e os gates G3/G4.** Não retreinei nada e não avaliei AUC. A
   `§4.6` do README declara que os binários em disco estão em AUC 0,6797,
   abaixo do piso — aceito a declaração, não a verifiquei, e ela é
   explicitamente do Sprint 4.

6. **`churn_voluntary/` por dentro.** Fora de escopo por `sprints.md`. Só toquei
   nele pela borda, e só o que estourou em `api/app.py` entrou no R6-2.

7. **Fuzz é amostragem.** Rodei ~80 payloads escolhidos à mão nos cinco
   endpoints. Não é fuzzing por cobertura nem por propriedade. Ausência de 5xx
   nos `/simulate/*` e no webhook de Pix é evidência forte, não prova.

8. **`archive/`.** Não auditei o conteúdo — só contei ocorrências de encoding
   para medir o escopo da catraca (R6-3).

---

## Higiene da auditoria

Toda comparação com commits anteriores foi feita em worktree descartável fora
de `D:\PTI`
(`.../auditoria-r6/wt-317a0eb` e `.../auditoria-r6/wt-baseline`), removidas ao
final. Nenhum `git checkout <ref> -- <caminho>` e nenhum `git restore --source`
foi executado na árvore principal. Todos os scripts de medição vivem em
`C:/Users/peide/AppData/Local/Temp/claude/D--PTI/auditoria-r6/`.

```
$ git worktree list
D:/PTI  8786d82 [sprint/a1-auditoria]

$ git status --short
?? .claude/
?? crai/docs/AUDITORIA_01_R6.md
```

(`.claude/` já estava sem rastrear antes desta auditoria começar.)

---

## Mínimo para o GA1 virar LIBERADO

1. **R6-1** — fechar a corrida por `thread_id` (`asyncio.Lock` em
   `_run_involuntary_pipeline` resolve o processo único), **ou** corrigir o
   `P1-14` e o comentário de `main_agent.py` para dizer a verdade: que o limite
   não é garantido nem em processo único, com a consequência (9 instruções
   contra 3) e a subnotificação do `retry_count` escritas. Se corrigir, teste de
   regressão que reprove antes.
2. **R6-2** — acrescentar o `N-R4-2` à tabela de dívida do README, com o tamanho
   real da classe (11 payloads no Segment, 5 no Stripe, cinco estourando em
   `api/app.py`), ou fechar com o mesmo `try/except` que o webhook de Pix já
   tem.

R6-3 e R6-4 são 🟡: não seguram o gate, mas são correções de uma linha cada.

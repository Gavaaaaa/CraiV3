# RE-AUDITORIA ADVERSARIAL #1 (R4) — Sprints 1, 2 e A1

**Escopo:** `baseline-pre-sprint (2956a69) .. HEAD (8ee67ae)` — branch `sprint/a1-auditoria`.
**Foco:** (A) os bloqueios de `AUDITORIA_01_R2.md` §7 e `AUDITORIA_01_R3.md` §6, que os commits
`b9388d5` e `8ee67ae` alegam fechar; (B) defeitos NOVOS introduzidos por esses dois commits.
**Contexto:** sessão limpa. Recebi apenas `sprints.md`, `docs/DATA_CARD.md` e as três auditorias
anteriores. Mensagens de commit e README tratados como **alegação a testar**, nunca como prova.
**Data:** 2026-09-04.

> Documento escrito incrementalmente: cada achado foi acrescentado assim que comprovado.

---

## 0. Método

- Ataques rodados de `D:\PTI\crai` contra o **pipeline real, não stubado** (`TestClient` com
  `raise_server_exceptions=False`, que é o que o servidor real faz), entrando pelos endpoints
  com **assinatura HMAC válida**.
- Prova de regressão em **worktree descartável** em
  `C:\Users\peide\AppData\Local\Temp\claude\a1r4\prev` (= `b9388d5`), com os arquivos de teste
  do HEAD copiados para lá. Nunca `git checkout` na árvore principal.
- Suíte no HEAD, antes de qualquer ataque:

```
$ cd D:\PTI\crai; PYTHONIOENCODING=utf-8 python -m pytest tests/ -q
355 passed, 3 warnings in 45.71s
```

---

## 1. Tabela de vereditos

| ID | Alegação | Veredito | Evidência executada | Gravidade |
|---|---|---|---|---|
| **R3-1** | `/simulate/pix-falhado` arredonda antes de validar: `0 < valor < 0.005` → 422 | **CONFIRMADO** | §2.1 | — |
| **R3-2** | `/simulate/payment-failed` e `/simulate/churn-risk` deixam de dar 500 | **CONFIRMADO** | §2.2 | — |
| **R3-3a** | N-9 corrigido: `data:[<não-objeto>]` → 422, não mais 200 com `pipeline:true` | **CONFIRMADO** (com assimetria residual) | §2.3 | BAIXA (resíduo) |
| **R3-3b** | N-11 corrigido: identidade só aceita escalar; estrutura → vazio + degradação | **CONFIRMADO** | §2.4 | — |
| **R3-4** | "teto de magnitude agora vale em REAIS nos dois caminhos" (`valor` e `total_cents`) | **🔴 FALSO** | §3.1 — teto efetivo medido: **R$ 1 trilhão em `valor`, R$ 10 bilhões em `total_cents`**. A discrepância de 100× que a alegação diz ter fechado **permanece intacta**; o código novo é **ramo morto** | **ALTA** (alegação falsa) |
| **R3-4t** | O teste de regressão do item acima (`TestA1R3TetoIgualNosDoisCaminhos`) prova a correção | **🔴 FALSO** | §3.2 — as **duas** funções da classe **passam sem alteração em `b9388d5`**. Zero testes falhando antes ⇒ viola o Gate GA1 ("todo teste de regressão comprovadamente falha no commit anterior") | **ALTA** (processo) |
| **P1-8/BACEN** | `schedule_retry_pix`: "um novo evento do mesmo cliente na mesma janela encontra o limite já gasto e cai direto na mensagem personalizada" (docstring, `workflow.py:220-222`) | **🔴 FALSO** | §3.3 — três webhooks assinados do mesmo `id_recorrencia` agendam **3 + 3 + 3 = 9 tentativas** na mesma janela de 7 dias. `retry_count` entra em **0** nas três vezes | **ALTA** |
| **N-R4-2** | `/webhooks/stripe` e `/webhooks/segment` fazem parte da "borda de entrada blindada" | **N/A — pré-existente** | §3.7 — corpo assinado que não é JSON → **HTTP 500** nos dois. Não introduzido pelo diff (`json.loads` cru intacto desde o baseline); Sprint 1 declarou escopo "arquivo único: `payment_gateway.py`" | MÉDIA (fora do escopo do plano) |
| **N-R4-1** | README, dívida N-12: o `UnicodeEncodeError` é de `test_pipeline.py` e é "pré-existente" | **🔴 FALSO** | §3.4 — o `→` que quebra está em `crai/ml/failure_classifier.py:250`, código de biblioteca, **introduzido por `4109d84`** (dentro deste diff). `train()` levanta em console Windows padrão | MÉDIA |
| **Limiar** | A regra documentada ("maior limiar da grade com recall ≥ 0,90") é a executada, no dataset declarado | **CONFIRMADO** | §3.5 | — |
| **`degradacoes`** | Nenhum caminho ignora a lista e segue com default | **PARCIAL** | §3.6 | BAIXA |

---

## 2. Parte A — os bloqueios da rodada 3 que foram de fato fechados

### 2.1 R3-1 — o simulador agora arredonda antes de validar ✔

A faixa `0 < valor < 0,005`, que na R3 produzia `Deal criado: Recuperação RN_s — R$ 0.00`:

```
=== R3-1: /simulate/pix-falhado, faixa que arredondava para zero ===
  valor=0.001      -> 422 {"detail":{"motivo":"valor_nao_utilizavel","campo":"valor","detalhe":"valor recebido: 0.001"}}
  valor=0.004      -> 422 {"detail":{"motivo":"valor_nao_utilizavel","campo":"valor","detalhe":"valor recebido: 0.004"}}
  valor=0.0049     -> 422 {"detail":{"motivo":"valor_nao_utilizavel","campo":"valor","detalhe":"valor recebido: 0.0049"}}
  valor=1e-9       -> 422 ...
  valor=1e-320     -> 422 ...
  valor=0.005      -> 200 {"status":"pipeline_executado","id_recorrencia":"RN_s"}
  valor=0.01       -> 200 {"status":"pipeline_executado","id_recorrencia":"RN_s"}
  valor=-500       -> 422 ...   valor=0 -> 422 ...   valor=1e300 -> 422 ...   valor=1e13 -> 422 ...
  valor=1e12       -> 200 {"status":"pipeline_executado","id_recorrencia":"RN_s"}
```

Nenhum `R$ 0.00` no CRM. Fronteira exata em 0,005, que é onde `round(v, 2)` deixa de zerar —
o mesmo ponto do webhook. **Correção resiste.**

### 2.2 R3-2 — os outros dois `/simulate/*` deixaram de dar 500 ✔

```
=== /simulate/payment-failed, amount hostil ===
  amount=NaN         -> 422 {"motivo":"valor_nao_utilizavel","campo":"amount","detalhe":"valor recebido: nan"}
  amount=Infinity    -> 422 ...    amount=-Infinity -> 422 ...    amount=-500 -> 422 ...
  amount=0           -> 422 ...    amount=1e300     -> 422 ...    amount=1e13 -> 422 ...
  amount=0.001       -> 422 ...
  amount=299.90      -> 200 {"status":"registrado","pipeline":false,...}

=== /simulate/churn-risk, inteiros hostis (401 dígitos) ===
  days_since_last=999...9 (401d) -> 422 {"motivo":"valor_nao_utilizavel","campo":"days_since_last"}
  days_since_last=-1             -> 422 ...    days_since_last=100001 -> 422 ...
  days_since_last=100000         -> 200 {"status":"pipeline_executado","user_id":"u1"}
  features_used_30d=999...9      -> 422 ...    features_used_30d=12   -> 200 ...
```

Zero 5xx nos três `/simulate/*`. Nenhum efeito colateral no HubSpot antes da recusa (o portão
roda antes de montar `props`). **Correção resiste.**

### 2.3 R3-3a — N-9 fechado, com uma assimetria residual

```
  data=["texto"] + valor raiz -> 422 {"motivo":"payload_nao_e_objeto","detalhe":"'data' é uma lista cujo conteúdo não é objeto (str) — não há evento para normalizar"}
  data=[42]      + valor raiz -> 422 (int)
  data=[null]    + valor raiz -> 422 (NoneType)
  data=[[]]      + valor raiz -> 422 (list)
  data=[]        + valor raiz -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}   ← declarado
```

O caso que a R3 mediu em 200 agora é 422. **Resíduo (BAIXA, não bloqueia):** a recusa só cobre
a lista. O envelope não-objeto que *não* é lista continua caindo na raiz em silêncio, que é
exatamente o "trocar de fonte de dados em silêncio" que a justificativa da correção condena:

```
  data="txt" + valor raiz -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
  data=42    + valor raiz -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
```

`data: ["texto"]` é recusado e `data: "texto"` é aceito. Duas regras para a mesma classe de
payload. Como o caminho da raiz é comportamento declarado do Sprint 1 (P0-3), registro como
resíduo, não como bloqueio.

### 2.4 R3-3b — N-11 fechado ✔

```
  id_rec dict,  sem e2e -> 422 {"motivo":"evento_sem_identificacao",...}
  id_rec lista, sem e2e -> 422 {"motivo":"evento_sem_identificacao",...}
  id_rec bool true      -> 422 {"motivo":"evento_sem_identificacao",...}
  id_rec dict, com e2e  -> 200 (checkpoint anônimo derivado do e2e)
```

Nenhuma estrutura vira identidade. Sondagem de colisão de `thread_id` em 10 pares:

```
  id_rec int 123 vs str '123'                  A=123                       B=123                       COLIDEM? True   (mesmo identificador, tipo JSON diferente — pré-existente)
  id_rec 'RN1' vs ' RN1 '                      A=RN1                       B=RN1                       COLIDEM? True   (normalização por strip, deliberada)
  dict{a:1} vs dict{b:2} (e2e distinto)        A=rec_anon_f98b4e5fac3eb899 B=rec_anon_742310e04c3412ab COLIDEM? False
  e2e igual, ispb distinto                     A=rec_anon_7eb6cc1c96fa8145 B=rec_anon_8e2c826283fae21b COLIDEM? False
  dict{a:1} vs list ['x'], MESMO e2e           A=rec_anon_1e1a10624a018ed3 B=rec_anon_1e1a10624a018ed3 COLIDEM? True   (mesmo e2e = mesmo cliente; correto)
```

**Não encontrei payload em que dois clientes distintos colidam.** As colisões observadas são
normalização (`strip`, `str(123)`) ou o mesmo `e2e_id`, que por definição é o mesmo pagador.

### 2.5 Prova de regressão dos testes de `8ee67ae` contra `b9388d5`

Worktree descartável em `.../a1r4/prev` (= `b9388d5`), com os arquivos de teste do HEAD
copiados para lá:

```
$ python -m pytest tests/test_payment_gateway.py -q -k "A1R3" --tb=line
17 failed, 7 passed, 124 deselected, 3 warnings in 8.13s

FAILED TestA1R3ArredondaAntesDeValidar::test_valor_que_arredonda_para_zero_e_recusado[0.001|0.004|0.0049]
FAILED TestA1R3ArredondaAntesDeValidar::test_nenhum_amount_zerado_chega_ao_pipeline
FAILED TestA1R3OutrosSimulateNaoDevolvem500::test_payment_failed_recusa_sem_500[nan|inf|1e+300|0.001]
FAILED TestA1R3OutrosSimulateNaoDevolvem500::test_churn_risk_recusa_contador_absurdo_sem_500
FAILED TestA1R3N9ListaQueNaoEObjeto::test_lista_sem_objeto_e_recusada[data0..data3]
FAILED TestA1R3N11IdentidadeNaoEEstrutura::test_estrutura_nao_vira_id_recorrencia[bruto0..bruto2]
FAILED TestA1R3N11IdentidadeNaoEEstrutura::test_sem_nenhuma_identificacao_escalar_e_recusado
```

Falha por **comportamento** (200 ≠ 422, `ValueError`, `TypeError`), não por `ImportError`.
Os 7 que passam são invariantes complementares ("continua passando") — **exceto dois**, ver §3.2.

---

## 3. Parte B — o que NÃO fechou

### 3.1 🔴 R4-1 — o teto de magnitude **continua** valendo R$ 1 tri em `valor` e R$ 10 bi em `total_cents`

**Alegação** (`8ee67ae`, mensagem de commit e comentário em `payment_gateway.py:234-238`):

> "teto de magnitude agora vale em REAIS nos dois caminhos: checar so o numero cru dava a
> total_cents um teto 100x menor que o de valor"
>
> "O teto é em REAIS, e vale igual nos dois caminhos […] Checar só o número cru daria a
> `total_cents` um teto de R$ 10 bilhões e a `valor` um de R$ 1 trilhão — dois limites para a
> mesma regra."

**Medido no HEAD:**

```
$ python b_teto.py
VALOR_MAXIMO_PLAUSIVEL = 1000000000000.0 (declarado: teto em REAIS, igual nos dois caminhos)

campo         valor bruto            -> valor normalizado (R$)      degradacoes
  valor        1000000000000.0        -> R$ 1000000000000.0        []
  valor        1000000000000.01       -> R$ 0.0                    ['valor_ilegivel']
  total_cents  1000000000000.0        -> R$ 10000000000.0          []
  total_cents  1100000000000.0        -> R$ 0.0                    ['valor_ilegivel']
  total_cents  10000000000000.0       -> R$ 0.0                    ['valor_ilegivel']

TETO EFETIVO EM REAIS, por campo (busca binária no maior aceito):
  campo 'valor'      : maior R$ aceito = 1,000,000,000,000.00
  campo 'total_cents': maior R$ aceito = 10,000,000,000.00
```

Confirmado também pela borda, com webhook assinado:

```
  total_cents=1000000000000  (R$ 10 bi)             -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
  total_cents=1000000000001  (R$ 10 bi + 1 centavo) -> 422 {"motivo":"evento_degradado","degradacoes":["valor_ilegivel"]}
  total_cents=1e14           (R$ 1 tri)             -> 422 {"motivo":"evento_degradado","degradacoes":["valor_ilegivel"]}
```

**A discrepância de 100× que a alegação diz ter fechado está intacta.** `total_cents` recusa
R$ 10 bilhões + 1 centavo; `valor` aceita até R$ 1 trilhão.

**Causa mecânica:** `_extrair_valor` chama `_para_float(bruto_centavos)` **primeiro**
(`payment_gateway.py:504`), e `_para_float` já aplica `_valor_utilizavel` ao número **cru**.
Qualquer coisa acima de 1e12 centavos morre ali. A checagem nova
(`_valor_utilizavel(round(centavos/100, 2))`, linhas 512-518) só recebe valores que já
passaram por `<= 1e12`, e `1e12/100 = 1e10 < 1e12` — **ela nunca pode falhar**:

```
A LINHA NOVA DE 8ee67ae E ALCANCAVEL?
  Para chegar la, `_para_float(bruto)` ja devolveu <= 1e12 (senao seria None).
  Logo centavos/100 <= 1e10 < 1e12 = teto  =>  a checagem nova NUNCA falha.
  contra-exemplo procurado: NENHUM — ramo morto
```

**Gravidade ALTA** — não pelo impacto operacional (um teto de R$ 10 bi não recusa cobrança
legítima), mas porque é a terceira rodada seguida em que um número documentado é desmentido
pela máquina, e **este é o defeito exato que a rodada 3 registrou como resíduo e que o commit
alega ter corrigido.** Foi adicionado código morto e escrita a correção no comentário.

### 3.2 🔴 R4-2 — o teste de regressão desse item passa igual em `b9388d5`

`TestA1R3TetoIgualNosDoisCaminhos` é a classe que o commit apresenta como prova da correção
acima. Ela tem duas funções, e **as duas passam sem alteração no commit anterior**:

```
$ cd <worktree b9388d5>/crai
$ python -m pytest tests/test_payment_gateway.py -k "A1R3" -v --tb=no -p no:warnings
...
tests/test_payment_gateway.py::TestA1R3TetoIgualNosDoisCaminhos::test_centavos_usa_o_mesmo_teto_em_reais PASSED [ 75%]
tests/test_payment_gateway.py::TestA1R3TetoIgualNosDoisCaminhos::test_centavos_acima_do_teto_em_reais_e_recusado PASSED [ 79%]
================ 17 failed, 7 passed, 124 deselected in 7.28s =================
```

Os dois casos escolhidos (`total_cents = 1e12` → 200 e `total_cents = 1e15` → 422) têm o mesmo
desfecho antes e depois, porque ambos são decididos pelo `_para_float` que já existia. O único
caso que distinguiria os dois commits — `total_cents` entre `1e12` e `1e14`, isto é, entre
R$ 10 bi e R$ 1 tri — **não está na classe**, e é justamente o que continua sendo recusado.

Gate GA1 do plano: *"Todo teste de regressão comprovadamente falha no commit anterior"*. Esta
classe não falha. A mensagem de commit reporta "18 failed, 7 passed" e conta estes dois entre
os 7 — apresentando como prova de correção um par de testes que não testa a correção.
**Gravidade ALTA (processo).**

### 3.3 🔴 R4-3 — 9 tentativas de Pix na mesma janela BACEN de 7 dias

`workflow.py:220-222`, docstring de `schedule_retry_pix`:

> "Por isso `retry_count` passa a refletir todas as tentativas comprometidas — **um novo evento
> do mesmo cliente na mesma janela encontra o limite já gasto** e cai direto na mensagem
> personalizada."

Três cobranças falhadas do **mesmo `id_recorrencia`**, por webhook assinado, pipeline real:

```
=== TRES cobrancas falhadas do MESMO id_recorrencia, na MESMA janela BACEN ===
  webhook #1 -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
  webhook #2 -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
  webhook #3 -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}

  {'customer_id':'RN_DUPLO','retry_count_ENTRADA':0,'agendadas':3,'retry_count_SAIDA':3,'quando':['05/09 16:20','08/09 16:20','11/09 16:20']}
  {'customer_id':'RN_DUPLO','retry_count_ENTRADA':0,'agendadas':3,'retry_count_SAIDA':3,'quando':['05/09 16:20','08/09 16:20','11/09 16:20']}
  {'customer_id':'RN_DUPLO','retry_count_ENTRADA':0,'agendadas':3,'retry_count_SAIDA':3,'quando':['05/09 16:20','08/09 16:20','11/09 16:20']}

  TENTATIVAS AGENDADAS SOMADAS PARA O MESMO CLIENTE NA MESMA JANELA: 9  (limite BACEN: 3)
```

O checkpoint **guarda** o valor correto; ele é sobrescrito na entrada:

```
  apos webhook #1: checkpoint RN_CK -> retry_count=3 retry_exhausted=False agendadas_no_plano=3
  apos webhook #2: checkpoint RN_CK -> retry_count=3 retry_exhausted=False agendadas_no_plano=3
```

**Causa exata:** `_run_involuntary_pipeline` (`api/app.py:507`) monta o `initial` com
`"retry_count": retries_done`, e `retries_done` é um parâmetro com default `0` que **nenhum
chamador preenche**. O `ainvoke` aplica esse `0` como update de canal sobre o checkpoint
retomado, zerando o contador antes de `schedule_retry_pix` lê-lo — por isso
`retry_count_ENTRADA` é `0` nas três invocações. O `retry_exhausted` do checkpoint também
nunca chega ao grafo pela mesma razão (`"retry_exhausted": False` no `initial`).

O `_validar` de `pix_automatico_retry.py` não pode ver isso: cada chamada de `schedule()`
recebe `tentativas_usadas=0` e está, isoladamente, correta. A varredura de 212.992 combinações
da rodada 3 exercitou `schedule()` isolado e por isso não podia detectar este caminho.

`MAX_TENTATIVAS = 3` é limite regulatório, não parâmetro de tuning (docstring do módulo,
linhas 6-10). Um PSP que reenvie o webhook da mesma cobrança falhada — comportamento normal de
retentativa de entrega de webhook — basta para disparar. **Gravidade ALTA.**

### 3.4 🟠 R4-4 — `4109d84` derruba `FailureClassifier.train()` no console padrão do Windows

O `print` acrescentado por `4109d84` a `failure_classifier.py:250` contém `→` (U+2192), que o
`cp1252` não codifica. Sem `PYTHONIOENCODING=utf-8`, `train()` levanta no meio, **depois de
treinar e de salvar os modelos**:

```
$ python -c "import crai.ml.failure_classifier as M; M.FailureClassifier().train(n_samples=300)"
[CLASSIFIER] Treinando XGBoost...
[CLASSIFIER] Treino conclu?do ? AUC: 0.665 | e-Profit m?dio (teste): R$ 464.47
UnicodeEncodeError: 'charmap' codec can't encode character '\u2192' in position 38
  File "D:\PTI\crai\crai\ml\failure_classifier.py", line 250, in train
```

Origem, confirmada por `git log -L`:

```
$ git log -1 --format="%h %s" -L 250,251:crai/crai/ml/failure_classifier.py
4109d84 feat(classificador): limiar do relatorio passa de 0.50 arbitrario para 0.25 por F2
+        print(f"[CLASSIFIER] Limiar do relatório {LIMIAR_CLASSIFICACAO:.2f} → "
```

Antes/depois, nas linhas de `print` do módulo:

```
--- baseline-pre-sprint: linhas de print com caractere fora do cp1252 ---  NENHUMA
--- HEAD:                linhas de print com caractere fora do cp1252 ---  U+2192 (linha 250)
```

A tabela de dívida do README atribui o `UnicodeEncodeError` a `test_pipeline.py` e o declara
**"Pré-existente"** (linha N-12). Medido: está em código de biblioteca, e **foi introduzido por
este diff**. É o mesmo tipo de linha falsa numa tabela de dívida que reprovou a rodada 3.
Consequência prática: o gate do Sprint 4 (`python -m crai.scripts.train_all`) não roda num
console Windows limpo. **Gravidade MÉDIA.**

### 3.5 O limiar e `escolher_limiar` — regra documentada = regra executada ✔

Reproduzido na configuração declarada (`generate_dataset(n_samples=15000, seed=42)`,
`test_size=0.2`), com `MODELS_DIR` redirecionado para temporário e `_save_models` neutralizado:

```
Tabela do comentario vs medido (3 casas nas 3 primeiras, 4 no F2, exato nos perdidos):
  0.50: acur 0.6433~0.643 prec 0.6163~0.616 recall 0.5478~0.548 f2 0.5603~0.5603 perdidos 610~610 -> OK
  0.40: acur 0.6337~0.634 prec 0.5718~0.572 recall 0.7376~0.738 f2 0.6972~0.6972 perdidos 354~354 -> OK
  0.35: acur 0.6227~0.623 prec 0.5536~0.554 recall 0.8302~0.830 f2 0.7548~0.7548 perdidos 229~229 -> OK
  0.30: acur 0.5973~0.597 prec 0.5310~0.531 recall 0.8955~0.895 f2 0.7874~0.7874 perdidos 141~141 -> OK
  0.25: acur 0.5593~0.559 prec 0.5054~0.505 recall 0.9400~0.940 f2 0.8020~0.8020 perdidos  81~ 81 -> OK
  0.20: acur 0.5140~0.514 prec 0.4800~0.480 recall 0.9681~0.968 f2 0.8045~0.8045 perdidos  43~ 43 -> OK
  0.15: acur 0.4863~0.486 prec 0.4665~0.467 recall 0.9896~0.990 f2 0.8083~0.8083 perdidos  14~ 14 -> OK
TODAS AS 35 CELULAS BATEM: True
n_total_test: 3000 | AUC: 0.7029
regra documentada: maior limiar com recall>=0.9 -> aprovados [0.15, 0.2, 0.25] -> max 0.25
escolher_limiar: 0.25 | LIMIAR_CLASSIFICACAO: 0.25 | IGUAIS? True
margem: recall(0.30) = 0.8955 vs RECALL_MINIMO 0.9
```

A regra do comentário ("o maior limiar da grade que ainda sustenta recall >= 0,90") é
literalmente a implementada (`max([m.limiar for m in grade if m.recall >= recall_minimo])`), e
o resultado é a constante. **CONFIRMADO.** O limiar continua sem decidir nada no pipeline
(`predict()` devolve `p_recovery` cru; a decisão é por e-Profit).

Observação não bloqueante, já levantada pela R3 e ainda válida: a margem que sustenta 0,25 é
`0,8955` contra `0,90` — 0,0045. O Sprint 3 recalibra `synthetic_data.py`, que é exatamente o
que move essa curva; a quebra de `TestEscolhaDoLimiar` será legítima.

Nota menor: `test_a_varredura_cobre_a_grade_declarada`, que substituiu o teste tautológico,
também **passa em `b9388d5`** (`34 passed`) — é guarda de documentação, não regressão. Não é
defeito (não foi apresentado como prova de correção de defeito), mas não é trava.

### 3.6 `degradacoes` — nenhum caminho novo ignora a lista; um resíduo antigo permanece

- `identificacao_ilegivel` (degradação nova) é registrada, entra em `DEGRADACOES_CONHECIDAS`,
  é lida por `workflow.py:47` e **não** é bloqueante — coerente com o comentário, e o evento
  que sobra sem nenhuma identidade escalar é recusado com 422 (§2.4).
- `/simulate/pix-falhado` sintetiza `"degradacoes": []`, e o comentário afirma que "chega aqui
  sem degradação porque o que degradaria já foi recusado". Verificado: `valor` é o único campo
  degradável do evento sintetizado, e ele passa por `_valor_de_simulacao`. A afirmação é
  verdadeira **neste** endpoint.
- Resíduo inalterado e já declarado no README (N-6): `store_encrypted_pix_key` chama
  `_resolver_envelope(raw_payload, [])` e descarta a lista. Fora do pipeline ativo. **BAIXA,
  não bloqueia.** Efeito colateral novo de `8ee67ae`: esse mesmo método agora pode levantar
  `PayloadPixInvalido` por `data:[<não-objeto>]`; como não tem chamador em produção
  (`grep -rn "store_encrypted_pix_key" crai/ --include=*.py` devolve só a definição), não abre
  caminho de 5xx.

### 3.7 Exceções escapando ao handler do FastAPI

**Pelo webhook de Pix: nenhuma.** Fuzz dirigido às três peças que `8ee67ae` acrescentou
(coerção de identidade, recusa de lista não-objeto, teto de centavos) — 13 formas de
identidade × 3 de `e2e_id` × 10 de valor × 4 envelopes, todos assinados, pipeline real:

```
PAYLOADS: 1560
CODIGOS: {200: 336, 422: 1224}
5xx/EXCECAO: 0
```

**Pelos `/simulate/*`: nenhuma** (§2.2) — os dois 500 da rodada 3 estão fechados.

**Pré-existente, fora do escopo declarado do plano** (Sprint 1 é "arquivo único:
`payment_gateway.py`"): `/webhooks/stripe` e `/webhooks/segment` usam `json.loads` cru, sem
`try/except` e sem checagem de tipo, e devolvem **HTTP 500** com corpo assinado:

```
=== /webhooks/stripe : corpo assinado mas nao-JSON ===
  nao-JSON  -> 500 Internal Server Error
  vazio     -> 500 Internal Server Error
  lista     -> 500 Internal Server Error
  string    -> 500 Internal Server Error

=== /webhooks/segment : corpo assinado mas nao-JSON ===
  nao-JSON  -> 500 Internal Server Error
  lista     -> 500 Internal Server Error
  string    -> 500 Internal Server Error
```

Que é pré-existente, prova o diff — a única linha de `json.loads` tocada em toda a sprint é a
do Pix:

```
$ git diff baseline-pre-sprint..HEAD -- crai/crai/api/app.py | grep "json.loads"
-    evento = await _pix_adapter.parse_pix_event(json.loads(raw))
+        corpo = json.loads(raw, parse_constant=_rejeitar_constante_json)
```

**Não é bloqueio desta auditoria** (não introduzido pelo diff, fora do escopo escrito do
Sprint 1), mas fica registrado: a frase "borda de entrada blindada" do merge `193e8d9` vale
para um dos três webhooks.

---
## 4. Os bloqueios da rodada 2 — reconferidos

Os quatro itens do §7 da `AUDITORIA_01_R2.md` continuam fechados:

| ID (R2) | Estado no HEAD | Evidência desta rodada |
|---|---|---|
| R2-1 magnitude / 5xx por webhook assinado | **fechado** | 1.560 payloads dirigidos + 19 estruturais, todos assinados, pipeline real → `{200: 336, 422: 1224}`, **zero 5xx** (§3.7) |
| R2-2 `/simulate/*` sem os portões do webhook | **fechado** | §2.1 e §2.2 — os três endpoints devolvem 422 em valor não utilizável e em contador absurdo |
| R2-3 tabela do limiar não reproduzível | **fechado** | §3.5 — as 35 células batem na configuração declarada; `escolher_limiar() == LIMIAR_CLASSIFICACAO` |
| R2-4 (processo) regressão do P0-6 provava símbolo | **fechado** | abaixo |

```
$ git worktree add .../a1r4/base baseline-pre-sprint
Preparing worktree (detached HEAD 2956a69)
$ cd .../a1r4/base/crai   # com o test_payment_isolation.py do HEAD
$ python -m pytest tests/test_payment_isolation.py::TestP0_6RegressaoPeloWebhook -q --tb=short
F.F
tests\test_payment_isolation.py:471: in test_dois_pagadores_anonimos_nao_dividem_checkpoint
E   AssertionError: P0-6 VIVO: dois pagadores distintos dividiram o checkpoint
    'rec_desconhecida' — o segundo evento retomaria o estado do primeiro
E   assert 'rec_desconhecida' != 'rec_desconhecida'
tests\test_payment_isolation.py:489: in test_literal_rec_desconhecida_nao_chega_ao_memorysaver
E   AssertionError: assert 'rec_desconhecida' not in ['rec_desconhecida', 'rec_desconhecida']
2 failed, 1 passed in 7.21s
```

Falha por igualdade. **Zero `ImportError`, zero `AttributeError`.**

---

## 5. Reprodução limpa do R4-3, sem nenhum monkeypatch

Para eliminar a hipótese de o achado do §3.3 ser artefato da instrumentação, dois webhooks
assinados do mesmo `id_recorrencia`, com a saída de `stdout` do próprio pipeline:

```
########## WEBHOOK #1 (mesmo RN_LIMPO, sem nenhum monkeypatch) ##########
[RACIOCÍNIO] Pensamento: 'insufficient_funds' costuma ser resolvido por nova tentativa de cobrança
             na janela regulada do BACEN (0/3 tentativas usadas), ...
[PIX-RETRY] RN_LIMPO: 3 tentativa(s) agendada(s) via previsão do Payday Engine | prazo BACEN: 11/09/2026
[PIX-RETRY]   tentativa 1/3: 05/09 16:29 | R$ 299.90
[PIX-RETRY]   tentativa 2/3: 06/09 16:29 | R$ 299.90
[PIX-RETRY]   tentativa 3/3: 07/09 16:29 | R$ 299.90
RESP: 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}

########## WEBHOOK #2 (mesmo RN_LIMPO, sem nenhum monkeypatch) ##########
[RACIOCÍNIO] Pensamento: ... na janela regulada do BACEN (0/3 tentativas usadas), ...
[PIX-RETRY] RN_LIMPO: 3 tentativa(s) agendada(s) via previsão do Payday Engine | prazo BACEN: 11/09/2026
[PIX-RETRY]   tentativa 1/3: 05/09 16:29 | R$ 299.90
[PIX-RETRY]   tentativa 2/3: 06/09 16:29 | R$ 299.90
[PIX-RETRY]   tentativa 3/3: 07/09 16:29 | R$ 299.90
RESP: 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
```

O segundo evento imprime **"0/3 tentativas usadas"** e reagenda as mesmas três datas.
Seis instruções de pagamento comprometidas para a mesma cobrança, dentro do mesmo prazo de
7 dias corridos, com o mesmo prazo BACEN impresso nas duas.

Por que a suíte não pega: `tests/test_payment_isolation.py:176` exercita
`schedule_retry_pix(_estado("pix_automatico", retry_count=3))` — chama o **nó** já com o
contador certo, que é exatamente o valor que o caminho real nunca entrega. Nenhum teste
manda dois eventos do mesmo cliente pela borda.

---

## 6. O que resistiu

- **Os três bloqueios da rodada 3 estão fechados de verdade**, e reproduzi cada um: a faixa
  `0 < valor < 0,005` do simulador vira 422 com fronteira exata em 0,005; os dois `/simulate/*`
  que davam 500 devolvem 422 sem efeito colateral no CRM; e `data:[<não-objeto>]` deixou de
  entrar no pipeline com `pipeline: true`.
- **N-11 fechado de forma mais forte do que o pedido:** a coerção de identidade não só evita o
  `str()` sobre estrutura, como registra degradação e recusa com 422 quando não sobra
  identidade escalar nenhuma. Não achei payload em que dois clientes distintos colidam.
- **A borda de Pix não abre 5xx:** 1.560 payloads assinados dirigidos ao código novo, zero.
- **O limiar saiu do território da alegação:** 35 células reproduzidas, regra documentada
  idêntica à executada, `escolher_limiar()` devolvendo a constante.
- **P0-6 tem regressão comportamental real no baseline**, sem erro de coleta.
- **Higiene:** `355 passed`; `models/` não é tocado pela suíte; árvore principal limpa.

---

## 7. Veredito

# BLOQUEADO para o Sprint 3.

Os três bloqueios da rodada 3 foram fechados, e fechados com testes que de fato falham no
commit anterior (17 falhas comportamentais em `b9388d5`). Isso é progresso real e merece ser
dito sem ressalva.

O que reprova esta rodada é de outra ordem, e é a mesma coisa duas vezes: **uma correção que
não corrige, acompanhada de um teste que não testa** (§3.1/§3.2), e **uma docstring que afirma
um invariante regulatório que a máquina desmente em dois webhooks** (§3.3). O item do teto de
magnitude é a terceira rodada seguida em que um número documentado não sobrevive à medição — e
desta vez o número já tinha sido apontado pela rodada anterior. O item do BACEN é pior: é um
limite regulatório, não um detalhe de parser, e a demo da banca é sobre Pix Automático.

### Mínimo necessário para liberar, em ordem de gravidade

1. **`retry_count` não pode ser zerado a cada evento** (§3.3). `_run_involuntary_pipeline`
   (`api/app.py:507`) escreve `"retry_count": retries_done` (=0) por cima do checkpoint em toda
   invocação. Ou o `initial` deixa de carregar `retry_count`/`retry_exhausted` quando já existe
   checkpoint para aquele `thread_id`, ou o nó passa a ler o contador do checkpoint em vez do
   state de entrada. **Teste obrigatório, pela borda:** dois webhooks assinados do mesmo
   `id_recorrencia` → soma das tentativas agendadas ≤ 3, e o segundo evento cai em
   `retry_exhausted`. Hoje: 3 + 3, com `"0/3 tentativas usadas"` impresso no segundo.
2. **O teto de magnitude tem que ser um só, ou a documentação tem que descrever os dois**
   (§3.1). Ou `_extrair_valor` passa a converter os centavos sem o teto e aplica
   `_valor_utilizavel` só depois da divisão (o que torna o teto realmente igual nos dois
   campos), ou o comentário e a mensagem de commit deixam de afirmar que já é assim. As duas
   saídas são aceitáveis; a atual — código morto mais comentário afirmativo — não é.
3. **`TestA1R3TetoIgualNosDoisCaminhos` precisa de um caso que distinga os dois commits**
   (§3.2). Qualquer `total_cents` entre `1e12` e `1e14` serve: hoje devolve 422, e sob a regra
   documentada deveria devolver 200. Enquanto os dois testes da classe passarem em `b9388d5`,
   o Gate GA1 não está cumprido para esse item.
4. **Corrigir a linha N-12 da tabela de dívida do README** (§3.4). Ela diz `test_pipeline.py` e
   diz "Pré-existente"; medido, o `→` está em `crai/ml/failure_classifier.py:250`, em código de
   biblioteca, e foi introduzido por `4109d84`, dentro deste diff. Ou se troca o caractere por
   `->` (uma linha) e a dívida some, ou a tabela passa a descrever o que existe.

**Aceitáveis como dívida (já no README, não repetir):** P2-10, P2-11, P2-13, N-6 (resíduo),
N-8, o `synthetic_data.py` não calibrado e o §4.6 (modelos em disco com AUC 0,6797).

**Novas, para acrescentar à lista de dívida — não bloqueiam:**
- `data: "texto"` / `data: 42` continuam caindo na raiz em silêncio, enquanto
  `data: ["texto"]` é recusado (§2.3) — duas regras para a mesma classe de envelope ilegível.
- `/webhooks/stripe` e `/webhooks/segment` devolvem **HTTP 500** com corpo assinado que não é
  JSON (§3.7). Pré-existente e fora do escopo escrito do Sprint 1 ("arquivo único:
  `payment_gateway.py`"), mas é o mesmo defeito que a rodada 2 tratou como bloqueio no Pix.
- `test_a_varredura_cobre_a_grade_declarada` e `test_o_limiar_em_uso_sustenta_o_recall_minimo`
  passam em `b9388d5`: são guardas de documentação, não regressões, e convém declará-los como
  tal (a disciplina que a R2 exigiu para o P2-12).

**Observação para o Sprint 3, não bloqueante:** `TestEscolhaDoLimiar` se apoia numa margem de
0,0045 (`recall(0,30) = 0,8955` vs `RECALL_MINIMO = 0,90`). O Sprint 3 recalibra
`synthetic_data.py`, que move exatamente essa curva. A quebra será o teste funcionando; a
resposta certa é refazer a análise, não afrouxar o `RECALL_MINIMO`.

---

## 8. Estado da árvore

```
$ git worktree list
D:/PTI 8ee67ae [sprint/a1-auditoria]

$ git status --short
?? .claude/
?? crai/docs/AUDITORIA_01_R4.md

$ git log -1 --oneline
8ee67ae sprint(a1): corrige os 3 bloqueios da re-auditoria A1-r3

$ cd crai; python -m pytest tests/ -q
355 passed in 15.82s
```

As duas worktrees descartáveis (`.../a1r4/prev` = `b9388d5` e `.../a1r4/base` =
`baseline-pre-sprint`) foram criadas fora de `D:/PTI` e removidas com
`git worktree remove --force`. Nenhum `git checkout` de caminho foi executado na árvore
principal. Nenhum commit foi feito. Nenhum arquivo de produção foi alterado — o único arquivo
que esta auditoria escreve é `crai/docs/AUDITORIA_01_R4.md`. Os scripts de ataque ficaram em
`C:\Users\peide\AppData\Local\Temp\claude\a1r4\scripts`, fora do repositório.

---

*Re-auditoria executada sem acesso às justificativas da implementação; mensagens de commit,
comentários e README tratados como alegações a testar. Ataques rodados contra o pipeline real,
não stubado, com assinatura HMAC válida. Prova de regressão em duas worktrees descartáveis,
removidas ao fim.*

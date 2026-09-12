# RE-AUDITORIA ADVERSARIAL #1 (R3) — Sprints 1, 2 e A1

**Escopo:** `baseline-pre-sprint (2956a69) .. HEAD (b9388d5)` — 19 arquivos, +4062/−73
**Foco:** os 4 bloqueios da `AUDITORIA_01_R2.md` §7 + defeitos novos introduzidos por `b9388d5`.
**Contexto:** sessão limpa. Recebi `sprints.md`, `docs/DATA_CARD.md`, `docs/AUDITORIA_01.md`,
`docs/AUDITORIA_01_R2.md`, o diff completo e o diff de `b9388d5`. **Nenhuma justificativa do
implementador além da mensagem de commit, tratada como alegação a verificar.**
**Ambiente:** Python 3.12.10, Windows 10, `D:\PTI\crai`, modelos treinados presentes.
**Data:** 01/09/2026

Suíte no HEAD, antes de qualquer ataque:

```
$ cd D:\PTI\crai; PYTHONIOENCODING=utf-8 python -m pytest tests/ -q
331 passed, 3 warnings in 15.51s
```

---

## 0. Método

- Todo ataque rodou de `D:\PTI\crai` com o **pipeline real, não stubado** — a suíte do
  repositório monkeypatcha `_run_involuntary_pipeline`, então um 500 vindo do sklearn três
  nós adiante não apareceria nela. Os fuzzes desta auditoria entram pelo
  `/webhooks/pix-automatico` **com assinatura HMAC válida** e deixam o modelo rodar.
- Prova de regressão em **duas árvores de trabalho descartáveis** (`baseline-pre-sprint` e
  `4109d84`), nunca `git checkout` na árvore principal. Removidas ao fim (§5).

---

## 1. Parte 1 — os 4 bloqueios da rodada 2

| ID | Alegação | Veredito | Evidência executada | Gravidade |
|---|---|---|---|---|
| **R2-1** | `_para_float` com teto (`VALOR_MAXIMO_PLAUSIVEL`) e `try/except OverflowError` fecham o P0-1 por magnitude: "6 → 422, zero 5xx" | **CONFIRMADO** | §2.1 — **1.292 webhooks assinados com pipeline real, zero 5xx** (R2: 70 em 1.088). `_para_float` não levanta em 37 sondas, incluindo `int` de 401 dígitos e `10**309`. Sem falso positivo: pt-BR, `R$`, centavos e até R$ 1e11 continuam passando | — |
| **R2-2** | `/simulate/pix-falhado` "passa pelos mesmos portões: 422 em valor não utilizável" | **PARCIAL** | §2.2 — `-500`, `0`, `NaN`, `inf`, `1e300`, `1e13` viram 422, como alegado. **Mas `valor=0.001` devolve HTTP 200, roda o pipeline e imprime `Deal criado: Recuperação RN_s — R$ 0.00`** — o mesmo valor pelo webhook dá 422. Os portões **não** são os mesmos. E o furo **se repete nos outros dois `/simulate/*`, que o commit não tocou: `payment-failed` com `amount: NaN` → HTTP 500; `churn-risk` com inteiro de 401 dígitos → HTTP 500** | **ALTA** |
| **R2-3** | A tabela do limiar reproduz e `escolher_limiar()` devolve `LIMIAR_CLASSIFICACAO` na configuração declarada | **CONFIRMADO** | §2.3 — **as 35 células batem, célula a célula** (`TODAS AS CELULAS BATEM: True`). Holdout 3.000 / 1.349 recuperáveis, AUC 0,7029, F2 trivial 0,8034, recall operacional 1,000 com 3 abandonados. `escolher_limiar(...) == 0.25 == LIMIAR_CLASSIFICACAO`. Determinístico em 3 processos. O limiar não entra em nenhuma decisão do pipeline | — |
| **R2-4** | `TestP0_6RegressaoPeloWebhook` falha no baseline por comportamento, não por símbolo | **CONFIRMADO** | §2.4 — `AssertionError: assert 'rec_desconhecida' != 'rec_desconhecida'`. Zero `ImportError`, zero `AttributeError`. `test_failure_classifier.py` e `test_payment_gateway.py` **coletam** no baseline (158 testes) | — |

---

## 2. Evidência executada

### 2.1 R2-1 — a magnitude foi fechada, e não quebrou caminho legítimo

**Fuzz dirigido:** 28 magnitudes × 5 campos (`valor`, `total_cents`, `amount`, `total`,
`pix.valor`), corpo JSON cru para poder mandar inteiro de precisão arbitrária, webhook
assinado, pipeline real:

```
$ python r3_fuzz_magnitude.py
  ...
  pix.valor    1e300              -> 422 {"detail":{"motivo":"evento_degradado","degradacoes":["valor_ilegivel"]}}
  pix.valor    -1e300             -> 422 {"detail":{"motivo":"evento_degradado","degradacoes":["valor_ilegivel"]}}
  pix.valor    1e13               -> 422 {"detail":{"motivo":"evento_degradado","degradacoes":["valor_ilegivel"]}}
  pix.valor    1e12               -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
  pix.valor    1e12_menos_1       -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
  pix.valor    1e12_mais_1        -> 422 {"detail":{"motivo":"evento_degradado","degradacoes":["valor_ilegivel"]}}
  pix.valor    str_401_digitos    -> 422 ...
  pix.valor    str_5000_digitos   -> 422 ...
  pix.valor    299.90             -> 200 ...
  pix.valor    str_299,90         -> 200 ...
  pix.valor    str_1.299,90       -> 200 ...

TOTAL: 140 | 5xx/EXCECAO: 0 | 200: 30
```

**Fuzz amplo**, replicando o grid que a R2 usou para achar os 70 (18 valores × 8 status ×
8 formas de envelope):

```
$ python r3_fuzz_amplo.py
PAYLOADS: 1152
CODIGOS: {422: 784, 200: 368}
5xx/EXCECAO: 0
```

**Fuzz estrutural** (25 payloads: aninhamento de 200 níveis, tipos errados em cada campo de
identidade, `event` como lista/`null`, `status` como int/lista, top-level não-objeto, chave
duplicada, lote de 2, `data:[<não-dict>]`, unicode com `\u0000`, `id_recorrencia` de 10 kB):

```
  5xx/EXCECAO: 0
```

**Total: 1.317 payloads assinados, zero 5xx.** Na R2 eram 70 em 1.088.

`_para_float` diretamente, 37 sondas — nenhuma levanta:

```
  _para_float(1000000000000.0                 ) = 1000000000000.0
  _para_float(1000000000001.0                 ) = None
  _para_float(999999999999.99                 ) = 999999999999.99
  _para_float(1000000000000.01                ) = None
  _para_float(1e+300                          ) = None
  _para_float(999999999999999999999999999...  ) = None      # int de 401 dígitos
  _para_float(100000000000000000000000000...  ) = None      # 10**309
  _para_float(inf                             ) = None
  _para_float(nan                             ) = None
```

**Falso positivo — o teto não recusa cobrança legítima:**

```
  _para_float('299,90'                        ) = 299.9
  _para_float('1.299,90'                       ) = 1299.9
  _para_float('R$ 1.299,90'                   ) = 1299.9
  _para_float('1,299.90'                      ) = 1299.9
  _para_float('29990'                         ) = 29990.0
  _para_float('0,01'                          ) = 0.01
```

E pelo webhook, `299.90`, `"299,90"`, `"1.299,90"`, `1e11` e `1e12` passam nos cinco campos
(30 respostas 200 na tabela acima). **A blindagem de magnitude não custou nenhum caminho
legítimo.**

Cobertura do módulo, para o gate G1:

```
$ python -m pytest tests/test_payment_gateway.py -q --cov=crai.integrations.payment_gateway
crai\integrations\payment_gateway.py   188   7   96%   278, 291, 295, 543-544, 572, 594
124 passed
```

Resíduo **BAIXA** (não bloqueia): o teto é aplicado ao número cru, antes da divisão por 100,
então o limite efetivo depende do campo — R$ 1 trilhão em `valor`, R$ 10 bilhões em
`total_cents`. O comentário declara "R$ 1 trilhão" sem essa ressalva.

```
  valor       =1000000000000.0   -> valor=1000000000000.0  degr=[]
  total_cents =1000000000000.0   -> valor=10000000000.0     degr=[]
  total_cents =100000000000000.0 -> valor=0.0               degr=['valor_ilegivel']
```

### 2.2 🔴 R2-2 — o simulador **não** passa pelos mesmos portões, e o furo se repete nos irmãos

**(a) O que a alegação cumpre.** Os cinco casos que a R2 nomeou estão fechados:

```
$ python r3_simulate.py
=== A) /simulate/pix-falhado : valores hostis (corpo JSON cru) ===
  -500           -> 422 {"detail":{"motivo":"valor_nao_utilizavel","detalhe":"valor recebido: -500.0"}}
  0              -> 422 {"detail":{"motivo":"valor_nao_utilizavel","detalhe":"valor recebido: 0.0"}}
  NaN            -> 422 {"detail":{"motivo":"valor_nao_utilizavel","detalhe":"valor recebido: nan"}}
  Infinity       -> 422 {"detail":{"motivo":"valor_nao_utilizavel","detalhe":"valor recebido: inf"}}
  -Infinity      -> 422 {"detail":{"motivo":"valor_nao_utilizavel","detalhe":"valor recebido: -inf"}}
  1e300          -> 422 {"detail":{"motivo":"valor_nao_utilizavel","detalhe":"valor recebido: 1e+300"}}
  1e13           -> 422 {"detail":{"motivo":"valor_nao_utilizavel","detalhe":"valor recebido: 10000000000000.0"}}
```

**(b) O que ela não cumpre.** O mesmo run, três linhas abaixo:

```
[ROI] [X] R$ 0.00 | taxa R$ 0.00 | e-Profit R$ -0.05
[HUBSPOT-SIM] Contact upsert: RN_s
[HUBSPOT-SIM] Deal criado: Recuperação RN_s — R$ 0.00 | pipeline=crai_recovery | stage=retrying
[HUBSPOT] Contact sim_contact_RN_s | Deal sim_deal_62212 | retrying
  0.001          -> 200 {"status":"pipeline_executado","id_recorrencia":"RN_s"}
```

O portão comparado lado a lado com o do webhook, para o mesmo valor:

```
$ python r3_simulate2.py
=== 1) ASSIMETRIA: valor que ARREDONDA para zero ===
  valor=0.001     /simulate -> 200 {"status":"pipeline_executado"...}  | webhook -> 422 {"motivo":"evento_degradado","degradacoes":["valor_nao_positivo"]}
  valor=0.004     /simulate -> 200 ...                                 | webhook -> 422 ...
  valor=0.0049    /simulate -> 200 ...                                 | webhook -> 422 ...
  valor=1e-9      /simulate -> 200 ...                                 | webhook -> 422 ...
  valor=1e-320    /simulate -> 200 ...                                 | webhook -> 422 ...
  valor=0.005     /simulate -> 200 ...                                 | webhook -> 200 ...
  valor=0.01      /simulate -> 200 ...                                 | webhook -> 200 ...
```

E o log do webhook, que mostra a regra correta:

```
[PIX] Valor não-positivo (0.001 → 0.00) — uma cobrança de R$ 0,00 ou negativa não é uma cobrança.
```

**Causa, exata:** o gateway **arredonda e depois valida**
(`_validar_positivo(round(reais, 2), ...)`, `payment_gateway.py:465`); o endpoint
**valida e depois arredonda** (`app.py:316-323`):

```python
valor = _para_float(payload.valor)
if valor is None or valor <= 0:      # 0.001 > 0 → passa
    raise HTTPException(422, ...)
valor = round(valor, 2)              # → 0.0, e o pipeline roda com 0.0
```

Consequência: para todo `0 < valor < 0.005`, o endpoint da demo executa o pipeline com
`amount=0.0` e cria negócio de **R$ 0,00** no CRM. É o sintoma do P0-5/N-10 — o mesmo que o
commit diz ter fechado — pela mesma porta, com outro número na entrada. O comentário novo no
código afirma que o evento "chega aqui sem degradação porque o que degradaria já foi
recusado, não porque ninguém olhou". Nesta faixa, é falso.

**(c) O padrão se repete — e nos irmãos produz 5xx.** Os outros dois `/simulate/*` não foram
tocados por `b9388d5` e não têm portão nenhum:

```
=== B) /simulate/payment-failed : amount hostil ===
  -500       -> 200 {"status":"registrado","pipeline":false,...}
  1e300      -> 200 {"status":"registrado","pipeline":false,...}
  0          -> 200 {"status":"registrado","pipeline":false,...}
  NaN        -> EXCECAO ValueError: cannot convert float NaN to integer

=== C) /simulate/churn-risk : inteiros hostis ===
[HUBSPOT-SIM] Deal criado: Retenção u1 — risco 90% | pipeline=crai_retention | stage=offer_sent
  dias_int401  -> EXCECAO TypeError: Object of type int is not serializable
  feat_int401  -> EXCECAO TypeError: Object of type int is not serializable
```

Com `raise_server_exceptions=False`, que é o que o servidor real faz:

```
=== 2) /simulate/payment-failed com amount=NaN -> 500? ===
  HTTP 500 Internal Server Error
=== 3) /simulate/churn-risk com int gigante -> 500? ===
  HTTP 500 Internal Server Error
```

Os dois quadros da pilha, dentro do código da CRAI:

```
### /simulate/payment-failed
  File "D:\PTI\crai\crai\api\app.py", line 290, in simulate_payment_failed
  File "D:\PTI\crai\crai\api\app.py", line 480, in _build_fake_stripe_event
ValueError: cannot convert float NaN to integer

### /simulate/churn-risk
  File "D:\PTI\crai\crai\api\app.py", line 391, in simulate_churn_risk
  File "D:\PTI\crai\crai\api\app.py", line 472, in _run_voluntary_pipeline
TypeError: Object of type int is not serializable
```

Agrava no caso do `churn-risk`: o `Deal criado: Retenção u1` é impresso **antes** do 500 — o
efeito colateral acontece e a resposta é erro.

Nota de contrato, não defeito: como o Pydantic coage antes do portão, `/simulate` recusa
`"299,90"` com `float_parsing` enquanto o webhook aceita. Os dois caminhos declaram-se
equivalentes e não são, nas duas pontas.

### 2.3 R2-3 — a tabela do limiar reproduz, célula a célula

Reproduzido na configuração declarada no comentário — `generate_dataset(n_samples=15000,
seed=42)`, `test_size=0.2`, `random_state=42` —, com `MODELS_DIR` redirecionado e
`_save_models` neutralizado para não tocar nos binários da demo:

```
$ python r3_limiar.py
MODELS_DIR redirecionado para C:\...\Temp\r3_models_ebrxx89o
LIMIAR_CLASSIFICACAO = 0.25   RECALL_MINIMO = 0.9
taxa base recovered (15000, seed 42) = 0.4495
n_total_test : 3000     AUC : 0.7029     recuperaveis no holdout : 1349

limiar |  acur decl/med | prec decl/med | recall decl/med |  F2 decl/med  | perdidos decl/med | OK?
  0.50 | 0.643 / 0.643 | 0.616 / 0.616 |  0.548 / 0.548  | 0.5603/0.5603 |   610 /   610 | OK
  0.40 | 0.634 / 0.634 | 0.572 / 0.572 |  0.738 / 0.738  | 0.6972/0.6972 |   354 /   354 | OK
  0.35 | 0.623 / 0.623 | 0.554 / 0.554 |  0.830 / 0.830  | 0.7548/0.7548 |   229 /   229 | OK
  0.30 | 0.597 / 0.597 | 0.531 / 0.531 |  0.895 / 0.895  | 0.7874/0.7874 |   141 /   141 | OK
  0.25 | 0.559 / 0.559 | 0.505 / 0.505 |  0.940 / 0.940  | 0.8020/0.8020 |    81 /    81 | OK
  0.20 | 0.514 / 0.514 | 0.480 / 0.480 |  0.968 / 0.968  | 0.8045/0.8045 |    43 /    43 | OK
  0.15 | 0.486 / 0.486 | 0.467 / 0.467 |  0.990 / 0.990  | 0.8083/0.8083 |    14 /    14 | OK

TODAS AS CELULAS BATEM: True

escolher_limiar(varredura) = 0.25
LIMIAR_CLASSIFICACAO       = 0.25
IGUAIS? True

recall_operacional: {'regra': 'e-Profit <= 0 ou recovery_score < 5 (route_after_diagnosis)',
                     'clientes_abandonados': 3, 'n_total': 3000,
                     'recuperaveis_perdidos': 0, 'recall': 1.0}
```

As três afirmações extra-tabela do comentário também batem:

- "holdout de 3.000 linhas, 1.349 recuperáveis" → `n_total_test: 3000`, `1349`. ✔
- "com taxa base de 0,4497, o classificador TRIVIAL tem F2 = 0,8034" → 1349/3000 = 0,44967;
  F2 trivial = 5·0,4497/(4·0,4497+1) = **0,8034**. ✔
- "abandona 3 clientes em 3.000 e perde zero recuperáveis — recall OPERACIONAL 1,000" →
  `clientes_abandonados: 3, recuperaveis_perdidos: 0, recall: 1.0`. ✔

Determinismo entre processos (3 execuções independentes, `PYTHONHASHSEED` livre):

```
auc 0.7029 | escolhido 0.25 | f2 [0.5603, 0.6972, 0.7548, 0.7874, 0.802, 0.8045, 0.8083]
auc 0.7029 | escolhido 0.25 | f2 [0.5603, 0.6972, 0.7548, 0.7874, 0.802, 0.8045, 0.8083]
auc 0.7029 | escolhido 0.25 | f2 [0.5603, 0.6972, 0.7548, 0.7874, 0.802, 0.8045, 0.8083]
```

**O limiar continua sem decidir nada.** Todas as ocorrências estão em caminho de relatório —
`predict()` (linhas 324-380) devolve `p_recovery` cru e decide por `eprofit > 0`:

```
$ grep -rn "LIMIAR_CLASSIFICACAO" crai/ml/failure_classifier.py
107/108: RECALL_MINIMO / LIMIAR_CLASSIFICACAO (constantes)
112: LIMIARES_REPORTADOS
250: print do relatório
287: y_pred, dentro de _evaluate
300: chave do dict de métricas
338: flag "em_uso" da varredura
```

Fora do módulo, zero ocorrências em código de produção (só em `tests/`).

### 2.4 R2-4 — a regressão do P0-6 falha por igualdade no baseline

```
$ git -C D:\PTI worktree add C:\...\audit_r3 baseline-pre-sprint
Preparing worktree (detached HEAD 2956a69)

$ cd C:\...\audit_r3\crai
$ python -m pytest tests/test_payment_isolation.py::TestP0_6RegressaoPeloWebhook -q
F.F

E       AssertionError: P0-6 VIVO: dois pagadores distintos dividiram o checkpoint
        'rec_desconhecida' — o segundo evento retomaria o estado do primeiro
E       assert 'rec_desconhecida' != 'rec_desconhecida'
tests\test_payment_isolation.py:471: AssertionError

E       AssertionError: assert 'rec_desconhecida' not in ['rec_desconhecida', 'rec_desconhecida']
tests\test_payment_isolation.py:489: AssertionError

2 failed, 1 passed, 3 warnings in 7.32s
```

**Zero `ImportError`, zero `AttributeError`.** A classe não menciona `_thread_id`: entra pelo
webhook assinado e captura o `thread_id` entregue ao `MemorySaver`. O teste que passa no
baseline (`test_o_mesmo_pagador_mantem_o_seu_checkpoint`) é o invariante complementar, que
tem que valer nos dois commits — não é tautologia, é a trava contra a supercorreção.

Coleção no baseline, o defeito de processo que reprovou o GA1 na rodada 1:

```
$ python -m pytest tests/test_failure_classifier.py tests/test_payment_gateway.py --collect-only -q
158 tests collected in 7.45s

$ python -m pytest tests/test_failure_classifier.py -q --tb=line
7 failed, 26 passed, 1 skipped
  tests/test_failure_classifier.py:404: KeyError: 'metricas_por_limiar'
  ...
  tests/test_failure_classifier.py:462: AttributeError: ...has no attribute 'LIMIAR_CLASSIFICACAO'
  tests/test_failure_classifier.py:470: KeyError: 'recall_operacional'
```

Os 26 testes pré-existentes continuam passando no baseline — a resolução em runtime cumpriu o
que prometia. (`test_predict_nao_usa_o_limiar` falha por `AttributeError` do
`monkeypatch.setattr`; é prova de símbolo, mas o teste documenta um invariante, não corrige
bug, então não pesa no gate.)

Contra `4109d84`, os testes novos de `b9388d5` falham por comportamento:

```
$ cd C:\...\audit_r3_prev\crai
$ python -m pytest tests/test_payment_gateway.py::TestA1R2MagnitudeImplausivel \
    tests/test_payment_gateway.py::TestA1R2SimuladorTemOsMesmosPortoes \
    tests/test_payment_isolation.py::TestP0_6RegressaoPeloWebhook \
    tests/test_failure_classifier.py::TestEscolhaDoLimiar -q --tb=line

  test_payment_gateway.py:927: assert 1e+300 is None
  test_payment_gateway.py:927: assert 10000000000000.0 is None
  test_payment_gateway.py:951: assert 200 == 422
  test_payment_gateway.py:994: AssertionError: valor=-500.0 entrou no pipeline da demo (HTTP 200)
  test_payment_gateway.py:994: AssertionError: valor=nan entrou no pipeline da demo (HTTP 200)
  test_payment_gateway.py:994: AssertionError: valor=1e+300 entrou no pipeline da demo (HTTP 200)
13 failed, 10 passed, 2 skipped
```

---

## 3. Parte 2 — defeitos NOVOS ou residuais de `b9388d5`

### 3.1 🔴 N-R3-1 — o portão do simulador valida antes de arredondar (§2.2)

Já detalhado. **ALTA**, porque é o endpoint da demo e o sintoma na tela é literalmente
`Deal criado: Recuperação RN_s — R$ 0.00`.

### 3.2 🔴 N-R3-2 — `/simulate/payment-failed` e `/simulate/churn-risk` devolvem 500 (§2.2c)

O commit corrigiu um dos três `/simulate/*`. Os outros dois têm o mesmo furo e produzem 5xx
alcançável. **ALTA** pelo critério declarado no próprio escopo desta auditoria ("qualquer 5xx
é bloqueio"), **MÉDIA** pelo impacto real (`/simulate/*` exige `ENV=development|demo`, e o
`payment-failed` não roda pipeline).

### 3.3 🟠 N-R3-3 — a tabela de dívida do README descreve o N-9 errado

O README, adicionado por `b9388d5` a pedido da R2, afirma sobre o N-9:

> `{"data": [<não-dict>]}` … **Recusado adiante pelo portão de valor (422), então não produz
> decisão errada.**

Medido:

```
  N-9 com valor na raiz          -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
  N-9 data=[42] c/ valor raiz    -> 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
  N-9 sem valor na raiz          -> 422 {"detail":{"motivo":"evento_sem_identificacao",...}}
```

Com um `valor` no envelope raiz, `{"event":...,"data":["texto"],"valor":77,...}` entra no
pipeline com HTTP 200. **A justificativa é falsa** — e ela é da mesma natureza do defeito que
reprovou a rodada 2 (número/afirmação documentado que a máquina desmente). O defeito técnico
em si continua sendo BAIXA; a linha do README é que precisa ser corrigida. **MÉDIA.**

### 3.4 O acoplamento de `_para_float` — o que ele de fato quebra

`app.py` importa `_para_float` como símbolo privado de `payment_gateway`. O import em si
funciona (mesmo pacote, sem ciclo) e não quebra caminho nenhum. O problema é **o que ele não
importa**: o portão do gateway são *duas* peças — `_para_float` **e**
`_validar_positivo(round(v, 2), ...)`. O endpoint levou só a primeira. É a causa mecânica do
§3.1. Corrigir arrastando `round` para antes da comparação (ou reusando `_validar_positivo`)
fecha o furo e elimina o acoplamento parcial.

### 3.5 A fixture `classificador_declarado` — não deixa resíduo e é determinística

- **Resíduo / vazamento de `MODELS_DIR`:** MD5 dos 14 arquivos de `models/` antes e depois da
  suíte completa:
  ```
  $ md5sum models/* > antes.txt ; python -m pytest tests/ -q ; md5sum models/* > depois.txt
  331 passed, 3 warnings in 15.57s
  $ diff antes.txt depois.txt
  MODELS INTACTOS (diff vazio)
  $ git -C D:\PTI status --short
  ?? .claude/
  ```
  O `pytest.MonkeyPatch.context()` envolve o `yield`, então o patch só é desfeito na
  destruição da fixture. Correto.
- **Determinismo:** 3 execuções do arquivo → `34 passed` nas três; 3 processos independentes
  → mesma AUC e mesmo vetor de F2 (§2.3).
- **Custo:** a docstring diz "Custa ~30s". Medido: `DURACAO train(15000): 1.18 s`. Comentário
  desatualizado, sem consequência.

### 3.6 Testes que passam por acaso ou são tautológicos

- `test_recall_nao_cresce_quando_o_limiar_cresce` — **tautológico**. Recall é monotonicamente
  não-crescente no limiar por construção (`pred = proba >= limiar`); o teste não pode falhar
  para modelo nenhum. Inofensivo, mas não é trava.
- `test_f2_nao_e_usado_para_escolher_o_limiar` — quase tautológico pelo mesmo motivo (F2
  cresce até o fim da grade, logo o argmax é sempre 0,15 ≤ 0,25). Documenta a intenção; não
  detecta regressão.
- `test_o_limiar_em_uso_sustenta_o_recall_minimo` passa também em `4109d84` — é guarda de
  documentação, não regressão. Não está declarado como tal (a R2 exigiu essa declaração no
  caso do P2-12; a mesma disciplina não foi aplicada aqui).
- **Fragilidade por desenho:** a margem que sustenta 0,25 é `recall(0,30) = 0,895` contra
  `RECALL_MINIMO = 0,90` — 0,005. Qualquer mudança em `synthetic_data.py` (que é exatamente
  o objeto do Sprint 3) move a escolha para 0,30 e quebra `TestEscolhaDoLimiar`. Isso é o
  comportamento pretendido ("se alguém mudar sem refazer a análise, a suíte quebra"), mas
  **o Sprint 3 vai encostar nisso já no primeiro commit** — vale saber antes.

`escolher_limiar()` devolvendo `None`: **não há chamador em produção**. A função só é
invocada pelos testes; `LIMIAR_CLASSIFICACAO` continua sendo constante literal. Não existe
caminho em que o `None` chegue a código que não o trate.

---

## 4. Parte 3 — varredura livre

### 4.1 P1-7 / P1-8 — 212.992 combinações, zero violações

Varredura paramétrica de `schedule()`, com hora do vencimento e hora de `agora` variando
independentemente:

| eixo | valores |
|---|---|
| hora do vencimento | 0, 1, 7, 9, 13, 18, 21, 23 |
| hora de `agora` | 0, 1, 7, 9, 13, 18, 21, 23 |
| minuto | 0, 1, 30, 59 |
| dia de `agora` (rel. venc.) | −2, 0, +1, +3, +5, +6, +7, +8 |
| dia previsto (rel. venc.) | −2 … +10 |
| `tentativas_usadas` | 0, 1, 2, 3 |
| `confidence` | 0.95, 0.30 |

```
$ python r3_sweep2.py
combinacoes testadas  : 212992
  violacoes de politica : 0
  excecoes inesperadas  : 0
  LISTA VAZIA c/ janela : 0
  fora do prazo_final   : 0
  intervalo < 1 dia     : 0
  usadas+agendadas > 3  : 0
  fora de ordem         : 0
  SUB-AGENDAMENTO       : 0
```

Invariante de sub-agendamento: `len(ts) == min(3 − usadas, (prazo_final − inicio).days + 1)`,
contando por `timedelta` — que é a decisão correta e é a que o código toma. (Contando por
data do calendário aparecem 8.736 "faltas" que, verificadas uma a uma, colocariam a terceira
tentativa **depois** do `prazo_final`: são falso positivo da métrica, não do código.)

Ponta a ponta:

```
$ PYTHONIOENCODING=utf-8 python test_pipeline.py
[PIX-RETRY] RN_maria_001: 3 tentativa(s) agendada(s) via previsão do Payday Engine | prazo BACEN: 08/09/2026
[PIX-RETRY]   tentativa 1/3: 02/09 10:53 | R$ 299.90
[PIX-RETRY]   tentativa 2/3: 03/09 10:53 | R$ 299.90
[PIX-RETRY]   tentativa 3/3: 04/09 10:53 | R$ 299.90
```

Gate G2 verde.

### 4.2 `degradacoes` — leitor funciona, resíduo conhecido permanece

```
[QUALIDADE] Evento normalizado com degradação: lote_de_1 — diagnóstico feito sobre campos preenchidos por default
RESP 200 {"status":"ok","evento":"cobranca_falhada","pipeline":true}
```

Resíduo inalterado e já listado no README: `store_encrypted_pix_key` chama
`_resolver_envelope(raw_payload, [])` e descarta a lista. Fora do pipeline ativo.

### 4.3 `thread_id` — nenhuma colisão nova

```
  espaco vs sem espaco                 A=RN1  B=RN1  COLIDEM? True   (normalização, correta)
  rec_anon forjado vs anonimo real     A=rec_anon_forjado  B=rec_anon_7a8f722f9cf1f4e7  COLIDEM? False
  valor int vs float                   COLIDEM? False
  valor 1.0 vs 1.00                    COLIDEM? True   (mesmo float, correto)
  id_rec lista vs str                  A=['R1']  B=['R1']  COLIDEM? True   ← N-11, dívida declarada
  id_rec int vs str                    A=123     B=123     COLIDEM? True   ← N-11, dívida declarada
```

As duas colisões são a classe N-11 (`str()` sobre estrutura), já registrada no README como
dívida aceita. Nenhuma colisão nova.

### 4.4 Exceções escapando ao handler do FastAPI

Pelo webhook: **nenhuma**, em 1.317 payloads assinados. Pelos `/simulate/*`: **duas**, §2.2c.

### 4.5 P2-9 — melhorou, não fechou

```
   Janela BACEN esgotada   : 0/3          ← ainda mente: RN_pedro_003 foi desenhado como esgotado
   Clientes retidos             : 1/4
   Escalados para CS humano     : 3/4     ← 1+3 = 4; a contradição da R2 (3/4 e 3/4) sumiu
```

Metade do P2-9 se resolveu sozinha. A linha `0/3` continua reproduzível. É escopo declarado
do Sprint 5; registro, não bloqueio.

### 4.6 Achado colateral da R2 que permanece

`models/train_metrics.json` continua com o schema anterior a `4109d84` e AUC 0,6797, abaixo
do piso [0,70; 0,92] dos gates G3/G4 — agora **listado no README como dívida com prazo
(Sprint 4)**, que era o que a R2 pediu. Aceito como dívida declarada.

---

## 5. O que resistiu

- **P0-1 por magnitude:** 1.317 payloads assinados com pipeline real, **zero 5xx** (R2: 70 em
  1.088). `_para_float` cumpre a docstring: não levanta em nenhuma das 37 sondas, incluindo
  `int` de 401 dígitos e `10**309`. E não custou nenhum caminho legítimo.
- **O limiar:** as 35 células reproduzem exatamente; as três afirmações extra-tabela também.
  `escolher_limiar()` devolve a constante. Determinístico entre processos. Não decide nada no
  pipeline. Este item saiu de "número que a máquina desmente" para "número que a máquina
  confirma e um teste tranca".
- **P0-6:** regressão comportamental de verdade no baseline (`'rec_desconhecida' !=
  'rec_desconhecida'`), sem `ImportError`. O bloqueio de processo do gate GA1 está fechado.
- **P1-7 / P1-8:** 212.992 combinações, zero violações de qualquer invariante.
- **P0-2 / P0-3 / P0-4:** não reconstruí nenhum dos ataques; 25 payloads estruturalmente
  hostis, incluindo aninhamento de 200 níveis e tipos errados em cada campo — zero 5xx.
- **Higiene da suíte:** 331 passed; `models/` bit a bit idêntico antes e depois; árvore
  principal limpa.
- **Cobertura:** `payment_gateway.py` em **96%** (gate G1 pede ≥ 90%).

Limpeza, como exigido:

```
$ git -C D:\PTI worktree remove C:\...\audit_r3 --force
$ git -C D:\PTI worktree remove C:\...\audit_r3_prev --force
$ git -C D:\PTI worktree list
D:/PTI  b9388d5 [sprint/a1-auditoria]
$ git -C D:\PTI status --short
?? .claude/
```

---

## 6. Veredito

# BLOQUEADO para o Sprint 3.

Três dos quatro bloqueios da rodada 2 estão **fechados de verdade**, e não retoricamente: a
magnitude sumiu de 70 respostas 5xx para **zero em 1.317 payloads assinados com o pipeline
real**; a tabela do limiar reproduz **célula a célula**, com as três afirmações fora da tabela
também batendo; e a regressão do P0-6 falha no baseline por igualdade, não por símbolo. Esse
é um progresso real e merece ser dito sem qualificação.

O bloqueio é o quarto item, e ele não foi fechado — foi fechado pela metade. O endpoint que a
demo usa **não** passa pelos mesmos portões do webhook: `valor=0.001` produz
`Deal criado: Recuperação RN_s — R$ 0.00` no CRM enquanto o webhook recusa o mesmo valor com
422. E o padrão que a auditoria mandou procurar **de fato se repete**: os outros dois
`/simulate/*` não foram tocados e devolvem **HTTP 500** — `amount: NaN` e um inteiro JSON
gigante. Pelo critério declarado ("qualquer 5xx é bloqueio"), isso basta.

### Mínimo necessário para liberar, em ordem de gravidade

1. **`/simulate/pix-falhado`: arredondar antes de validar.** Trocar as quatro linhas de
   `app.py:316-323` por `round` primeiro (ou, melhor, reusar
   `PixAutomaticoAdapter._validar_positivo`, que é a peça que faltou importar junto com
   `_para_float`). Teste obrigatório: `valor=0.001` → **422**, e `sim_client.pipeline_calls
   == []`. Hoje: `200` + `Deal criado: … R$ 0.00`.
2. **`/simulate/payment-failed`: portão em `amount`.** `NaN` → `ValueError: cannot convert
   float NaN to integer` em `app.py:480` → **HTTP 500**. Mesmo portão do item 1, 422 em valor
   não utilizável.
3. **`/simulate/churn-risk`: limitar os inteiros.** `days_since_last` / `features_used_30d`
   de 401 dígitos → `TypeError: Object of type int is not serializable` em `app.py:472` →
   **HTTP 500**, e o negócio no HubSpot é criado antes do erro. Faixa plausível
   (`0 ≤ n ≤ 3650`, p.ex.) via `Field(ge=…, le=…)` resolve os dois campos.
4. **Corrigir a linha do N-9 na tabela de dívida do README.** Ela afirma que o payload é
   "recusado adiante pelo portão de valor (422)"; medido, `{"data":["texto"],"valor":77,…}`
   devolve **200 com `pipeline: true`**. Ou se descreve o comportamento real, ou se fecha o
   defeito. Uma justificativa falsa numa tabela de dívida é o mesmo tipo de erro que reprovou
   a rodada 2.

**Aceitáveis como dívida (já listadas no README, não repetir aqui):** N-8, N-11, N-12, o
resíduo do N-6, P2-10/11/13 e o §4.6. **Novas, para acrescentar à lista:** o teto de
magnitude é aplicado ao número cru, então vale R$ 1 trilhão em `valor` e R$ 10 bilhões em
`total_cents`, enquanto o comentário declara só o primeiro (BAIXA); e o P2-9 residual
(`Janela BACEN esgotada: 0/3`), que o Sprint 5 já tem no escopo.

**Observação para o Sprint 3, não bloqueante:** `TestEscolhaDoLimiar` sustenta-se numa margem
de 0,005 (`recall(0,30) = 0,895` vs `RECALL_MINIMO = 0,90`). O Sprint 3 recalibra
`synthetic_data.py`, que é exatamente o que move essa curva. A quebra será legítima — é o
teste fazendo o trabalho dele —, mas convém saber que ela vai acontecer e que a resposta
certa é refazer a análise, não afrouxar o `RECALL_MINIMO`.

---

*Re-auditoria executada sem acesso às justificativas da implementação. Ataques rodados contra
o pipeline real, não stubado. Prova de regressão em duas worktrees descartáveis
(`baseline-pre-sprint` e `4109d84`), removidas ao fim; `git worktree list` com uma linha só e
`git status --short` na árvore principal: `?? .claude/`. Scripts de ataque em scratchpad
temporário. Único arquivo alterado por esta auditoria: `docs/AUDITORIA_01_R3.md`.*

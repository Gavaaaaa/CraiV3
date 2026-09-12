# RE-AUDITORIA ADVERSARIAL #1 (R2) — Sprints 1, 2 e A1

**Escopo:** `baseline-pre-sprint (2956a69) .. HEAD (4109d84)` — 17 arquivos, +2864/−72
**Contexto:** sessão limpa. Recebi apenas `sprints.md`, `docs/DATA_CARD.md`, o diff e a
`docs/AUDITORIA_01.md` anterior. **Nenhuma justificativa do implementador.**
**Ambiente:** Python 3.12.10, Windows 10, `D:\PTI\crai`, modelos treinados presentes.
**Data:** 01/09/2026

Suíte no HEAD, antes de qualquer ataque:

```
$ cd D:\PTI\crai; PYTHONIOENCODING=utf-8 python -m pytest tests/ -q
304 passed, 3 warnings in 14.71s
```

---

## 0. Método

- Ataques rodaram de `D:\PTI\crai` com `PYTHONPATH=.`, contra o adapter direto e contra
  `/webhooks/pix-automatico` **com assinatura HMAC válida** (`TestClient`), para não
  auditar um caminho que a borda real não expõe.
- A prova de regressão usou **árvore de trabalho separada e descartável**, nunca
  `git checkout` na árvore principal:

```
$ git -C D:\PTI worktree add C:\Users\peide\AppData\Local\Temp\claude\baseline_audit baseline-pre-sprint
Preparing worktree (detached HEAD 2956a69)
HEAD is now at 2956a69 chore: amplia o ignore de relatorios para relatorio_*.txt
```

Os três arquivos de teste do HEAD foram copiados para dentro dessa árvore e rodados
contra o código do baseline. Ao fim:

```
$ git -C D:\PTI worktree remove ...\baseline_audit --force && git -C D:\PTI status --short
?? .claude/
```

Árvore principal limpa, como exigido.

---

## 1. Parte 1 — os 8 defeitos das Sprints 1 e 2

| ID | Alegação | Veredito | Evidência executada | Gravidade |
|---|---|---|---|---|
| **P0-1** | `_extrair_valor` fazia `float()` cru → `ValueError` → HTTP 500 | **PARCIAL** | §2.1 — pt-BR/en-US/`R$` resolvidos e ambíguos recusados. **Mas dois payloads assinados ainda devolvem HTTP 500**: inteiro JSON de 401 dígitos (`OverflowError` dentro de `_para_float`, que a docstring jura "**Nunca levanta**") e `total_cents: 1e300` (valor finito acima do teto de `float32` → `ValueError` do sklearn). É o sintoma literal do P0-1: exceção não tratada → 500 → tempestade de retry do PSP | **ALTA** |
| **P0-2** | `data: [{...}]` (lote) → normalizado inteiro em default | **CONFIRMADO** | §2.3 — lote de 1 desempacotado com `degradacoes=['lote_de_1']`; lote de 2 → `PayloadPixInvalido` → HTTP 422 | — |
| **P0-3** | `data: {}` vs `data: {...}` → fonte de leitura mudava | **CONFIRMADO** | §2.3 — `{}`, `None`, `[]`, `"x"`, `42` produzem todos o mesmo caminho (raiz) + `envelope_ausente`. `grep -rn "or raw_payload" crai/` → zero. Resíduo menor em N-9 | — |
| **P0-4** | Fallback comparava status cru contra tokens internos → toda falha virava `desconhecido` | **CONFIRMADO** | §2.4 — `status:"failed"` → `cobranca_falhada` mesmo com `authorization_status:"approved"`; `revoked`/`cancelled`/`authorized` reconhecidos nos **dois** campos. A porta que a A1 abriu (N-3) está fechada | — |
| **P0-5** | Valor ausente → `0.0` sem log → e-Profit ≤ 0 → churn legítimo descartado | **PARCIAL** | §2.2 — pelo webhook, `0`, `-500`, `nan`, `inf`, ilegível e ausente viram degradação bloqueante → HTTP 422. **Mas `/simulate/pix-falhado` não tem portão nenhum**: `valor=-500`, `valor=0` e `valor=NaN` rodam o pipeline inteiro e criam deal no HubSpot (§4.3). E `test_pipeline.py` — a face da demo hoje — chama `crai_agent.ainvoke` direto, sem passar por borda alguma | **MÉDIA** |
| **P0-6** | `id_recorrencia` vazio → `thread_id` `"rec_desconhecida"` compartilhado | **CONFIRMADO** | §2.5 — não consegui produzir colisão entre clientes distintos: separador cru, aspas, contrabarra, `int` vs `float`, `float` vs `str` — todos separam. Evento sem `id_recorrencia` **e** sem `e2e_id` é recusado com 422 em vez de receber id degenerado | — |
| **P1-7** | `_ancorar_na_liquidez` devolvia `[]` no último dia em hora tardia | **CONFIRMADO** | §2.6 — **212.992** combinações, zero listas vazias com janela aberta | — |
| **P1-8** | Ancoragem só para frente → tentativas jogadas fora | **CONFIRMADO** | §2.6 — nas mesmas 212.992: zero sub-agendamentos, zero violações. `test_pipeline.py`: `RN_maria_001` → **3/3** | — |

**Testes de regressão contra o baseline:** ver §3. O defeito de processo que reprovou o
GA1 anterior (a suíte de gateway **nem coletava** no baseline) foi corrigido de verdade.

---

## 2. Evidência executada

### 2.1 P0-1 — a conversão resiste ao que foi especificado; a borda não

O que funciona (`a_parser.py`, seção A):

```
  _para_float('299,90'          ) = 299.9
  _para_float('1.299,90'        ) = 1299.9
  _para_float('R$ 1.299,90'     ) = 1299.9
  _para_float('299.90'          ) = 299.9
  _para_float('1,299.90'        ) = 1299.9
  _para_float('1,299'           ) = None      # ambíguo: RECUSA (era 1.299)
  _para_float('10,000'          ) = None      # ambíguo: RECUSA (era 10.0)
  _para_float('2,500'           ) = None      # ambíguo: RECUSA (era 2.5)
  _para_float('1.299'           ) = None
  _para_float('nan'             ) = None
  _para_float('inf'             ) = None
  _para_float('Infinity'        ) = None
  _para_float('-inf'            ) = None
  _para_float('1e400'           ) = None
  _para_float(float('inf'))  = None
  _para_float(float('nan'))  = None
  _para_float(True)          = None
```

O que **não** funciona:

```
  _para_float(10**400)       = EXCECAO OverflowError: int too large to convert to float
```

Traceback completo, pelo parser público:

```
  File "D:\PTI\crai\crai\integrations\payment_gateway.py", line 327, in parse_pix_event
    "valor": self._extrair_valor(dados, degradacoes),
  File "D:\PTI\crai\crai\integrations\payment_gateway.py", line 416, in _extrair_valor
    reais = _para_float(bruto_reais)
  File "D:\PTI\crai\crai\integrations\payment_gateway.py", line 240, in _para_float
    valor = float(bruto)
OverflowError: int too large to convert to float
```

A linha 235 do mesmo arquivo diz, em negrito: `**Nunca levanta.** Quem chama decide se o
`None` vira degradação ou recusa.` A linha 240 desmente. E a linha 240 está **fora da
cobertura de teste** (relatório de cobertura: `238, 243, 247, 403-406, 495-496, 524, 546`).

Pelo webhook assinado, os dois caminhos que chegam a 500 (`a_big.py`):

```
=== O) qual magnitude de valor derruba a API (500)? ===
  total_cents=1e38   (R$ 1e36) -> HTTP 200 {'status': 'ok', 'evento': 'cobranca_falhada', 'pipeline': True}
  total_cents=1e40   (R$ 1e38) -> HTTP 200 {'status': 'ok', 'evento': 'cobranca_falhada', 'pipeline': True}
  total_cents=1e100  (R$ 1e98) -> HTTP 500 Internal Server Error
  total_cents=1e300  (R$ 1e298) -> HTTP 500 Internal Server Error

=== P) inteiro JSON grande demais para float ===
  valor com  10 digitos -> HTTP 200 {'status': 'ok', 'evento': 'cobranca_falhada', 'pipeline': True}
  valor com 100 digitos -> HTTP 500 Internal Server Error
  valor com 308 digitos -> HTTP 500 Internal Server Error
  valor com 401 digitos -> HTTP 500 Internal Server Error
```

Fuzz mais amplo (16 valores × 8 formas de envelope × 8 status = **1.088 webhooks
assinados**):

```
=== N) fuzz de payloads hostis: procurando 5xx ===
  payloads enviados: 1088 | respostas 5xx: 70
   5xx: (1e+300, 'dict', 'failed', 500)
   5xx: (1e+300, 'list1', 'failed', 500)
   5xx: (1e+300, 'empty', 'failed', 500)
   5xx: (1e+300, 'null', 'failed', 500)
   5xx: (1e+300, 'str', 'failed', 500)
   5xx: (1e+300, 'int', 'failed', 500)
```

**Todos os 70 são de magnitude numérica.** Nenhum outro payload dos 1.088 produziu 5xx —
a blindagem contra formato está boa; contra magnitude, não existe.

Traceback do segundo caminho (o valor finito que estoura o `float32` do sklearn):

```
  File "D:\PTI\crai\crai\ml\failure_classifier.py", line 347, in predict
    rf_proba = self.rf.predict_proba(X)[0, 1]
  ...
  File "sklearn\utils\validation.py", line 172, in _assert_all_finite_element_wise
    raise ValueError(msg_err)
ValueError: Input X contains infinity or a value too large for dtype('float32').
```

É exatamente a `ValueError` que o N-2(b) apontou. A correção fechou `Infinity`/`NaN`
(literais e strings) e **não fechou a magnitude finita**, que produz o mesmo erro.

Isto **não é regressão** — o baseline também quebrava:

```
 BASELINE int 401 dig -> EXCECAO OverflowError int too large to convert to float
 BASELINE 1e300 -> {'e2e_id': '', 'valor': 1.0000000000000001e+298, ...}
```

É um P0-1 **não fechado**, apresentado como fechado.

### 2.2 P0-5 — o portão de valor, pelo webhook, resiste

```
=== B) parser completo: valor problematico ===
  valor=0              -> valor=0.0          degr=['valor_nao_positivo']
  valor='0,00'         -> valor=0.0          degr=['valor_nao_positivo']
  valor='0,004'        -> valor=0.0          degr=['valor_ilegivel']
  valor='1,299'        -> valor=0.0          degr=['valor_ilegivel']
  valor='10,000'       -> valor=0.0          degr=['valor_ilegivel']
  valor='2,500'        -> valor=0.0          degr=['valor_ilegivel']
  valor='-500,00'      -> valor=-500.0       degr=['valor_nao_positivo']
  valor=-500           -> valor=-500.0       degr=['valor_nao_positivo']
  total_cents=0        -> valor=0.0          degr=['valor_nao_positivo']
  total_cents=-100     -> valor=-1.0         degr=['valor_nao_positivo']
  valor=nan(py)        -> valor=0.0          degr=['valor_ilegivel']
  valor=inf(py)        -> valor=0.0          degr=['valor_ilegivel']
  valor='inf'          -> valor=0.0          degr=['valor_ilegivel']
  valor='Infinity'     -> valor=0.0          degr=['valor_ilegivel']
  sem valor            -> valor=0.0          degr=['valor_ausente']
  valor=[1]            -> valor=0.0          degr=['valor_ilegivel']
  valor={'a':1}        -> valor=0.0          degr=['valor_ilegivel']
  total_cents=1e20     -> valor=1e+18        degr=[]        <-- sem teto superior
```

E os três literais do JSON são recusados na desserialização, antes do adapter:

```
  JSON literal NaN           -> (400, {'detail': 'Corpo do webhook contém Infinity/NaN, que não são JSON válido'})
  JSON literal Infinity      -> (400, {'detail': 'Corpo do webhook contém Infinity/NaN, que não são JSON válido'})
  JSON literal -Infinity     -> (400, {'detail': 'Corpo do webhook contém Infinity/NaN, que não são JSON válido'})
  string 'inf'               -> (422, {'detail': {'motivo': 'evento_degradado', 'degradacoes': ['valor_ilegivel']}})
  1e400 (overflow float)     -> (422, {'detail': {'motivo': 'evento_degradado', 'degradacoes': ['valor_ilegivel']}})
```

O que continua aberto é o **outro** caminho de entrada — ver §4.3.

### 2.3 P0-2 / P0-3 — envelope determinístico

```
=== I) N-9: data como lista de 1 item nao-dict ===
  data=['texto']    -> valor=77.0 degr=['envelope_ausente']
  data=[None]       -> valor=77.0 degr=['envelope_ausente']
  data=[42]         -> valor=77.0 degr=['envelope_ausente']
  data=[[1, 2]]     -> valor=77.0 degr=['envelope_ausente']
  data=[]           -> valor=77.0 degr=['envelope_ausente']
  data=[{}]         -> valor=0.0 degr=['lote_de_1', 'valor_ausente']
  data=[{}, {}]     -> PayloadPixInvalido: lote_nao_suportado: 2 eventos num único payload

$ grep -rn "or raw_payload" crai/
  (zero ocorrencias)
```

### 2.4 P0-4 / N-3 / N-4 — a precedência foi invertida e os dois mapas viraram um só, na prática

```
=== D) N-3: precedencia status x authorization_status (SEM nome de evento) ===
  status=failed      authorization_status=approved    -> cobranca_falhada         degr=[]
  status=failed      authorization_status=active      -> cobranca_falhada         degr=[]
  status=failed      authorization_status=authorized  -> cobranca_falhada         degr=[]
  status=declined    authorization_status=approved    -> cobranca_falhada         degr=[]
  status=rejected    authorization_status=approved    -> cobranca_falhada         degr=[]
  status=paid        authorization_status=revoked     -> cobranca_confirmada      degr=[]
  status=revoked     authorization_status=approved    -> autorizacao_revogada     degr=[]
  status=cancelled   authorization_status=approved    -> autorizacao_revogada     degr=[]
  status=authorized  authorization_status=approved    -> autorizacao_concedida    degr=[]

=== E) N-4: tokens no campo generico `status`, sozinhos ===
  status=authorized   -> autorizacao_concedida    degr=[]
  status=revoked      -> autorizacao_revogada     degr=[]
  status=cancelled    -> autorizacao_revogada     degr=[]
  status=canceled     -> autorizacao_revogada     degr=[]
  status=expired      -> autorizacao_revogada     degr=[]
  status=failed       -> cobranca_falhada         degr=[]
  status=pending      -> desconhecido             degr=['status_desconhecido']
  status=chargeback   -> desconhecido             degr=['status_desconhecido']

=== F) tokens no campo authorization_status, sozinhos ===
  authorization_status=failed       -> cobranca_falhada         degr=[]
  authorization_status=approved     -> autorizacao_concedida    degr=[]
  authorization_status=revoked      -> autorizacao_revogada     degr=[]

=== G) fixture de referencia do repo + status failed, sem `event` ===
  -> {'e2e_id': 'E60701190202608261200abcdef123', 'valor': 299.9,
      'status': 'cobranca_falhada', 'ispb_pagador': '60701190',
      'id_recorrencia': 'RN2026082600001', 'degradacoes': []}
```

O ataque que a A1 usou para derrubar o P0-4 — a fixture do próprio repositório, com
`authorization_status: "approved"` e `status: "failed"`, sem `event` — agora produz
`cobranca_falhada`. **Não consegui reconstruí-lo.** N-3 e N-4 estão fechados.

Resíduo de julgamento (não é defeito reproduzido): `status: "expired"` e
`status: "cancelled"` num campo de **cobrança** viram `autorizacao_revogada`. Para um PSP
que use `status` para o ciclo da cobrança (Pix expirado, cobrança cancelada), a leitura
está trocada. Não tenho payload de PSP real para provar qual das duas é a correta, então
registro como ambiguidade, não como achado.

### 2.5 P0-6 / N-5 — não consegui produzir colisão

```
=== H) N-5: separador dentro dos campos ===
  A e2e='E123|999' ispb='60701190' -> rec_anon_7fd8106d9dbd9a76
  B e2e='E123' ispb='999|60701190' -> rec_anon_7a9ceae56a1262ca
  COLIDEM? False

=== H2) aspas/virgula/colchete dentro dos campos (a base agora e JSON) ===
  {'e2e_id': 'A", "B', 'ispb_pagador': 'C', 'valor': 1.0} -> rec_anon_795285780863c3b1
  {'e2e_id': 'A', 'ispb_pagador': 'B", "C', 'valor': 1.0} -> rec_anon_d65b5a0f81a8e768
  COLIDEM? False

  {'e2e_id': 'X\\', 'ispb_pagador': 'Y', 'valor': 1.0} -> rec_anon_5ea1cc62fa92eece
  {'e2e_id': 'X', 'ispb_pagador': '\\Y', 'valor': 1.0} -> rec_anon_04b900b237c5add9
  COLIDEM? False

  {'e2e_id': 'A', 'ispb_pagador': 'B', 'valor': 1.0} -> rec_anon_c37641fe3a25f8ca
  {'e2e_id': 'A', 'ispb_pagador': 'B', 'valor': 1}   -> rec_anon_c0797587c6f50e0f
  COLIDEM? False

=== H3) sem discriminante nenhum ===
  tudo vazio                 -> None      # -> HTTP 422 na borda
  so ispb+valor (sem e2e)    -> None      # -> HTTP 422 na borda
  so id_recorrencia='  '     -> None

=== H4) dois clientes de mesmo preco, PSP que so manda e2e ===
  cliente1 e2e=E1 -> rec_anon_3155ecb4712bfb92
  cliente2 e2e=E2 -> rec_anon_15648d77242ffa8b
  COLIDEM? False
```

O que tentei e por que resiste: a base virou `json.dumps([e2e, ispb, repr(valor)])`, então
todo separador injetado é escapado pelo próprio JSON; `repr` separa `1` de `1.0`; e o caso
degenerado (base sem discriminante) deixou de gerar id e passou a **recusar com 422** em
vez de inventar identidade. O único par que colide é o par de eventos genuinamente
idênticos — que é o comportamento correto:

```
=== H5) mesmo cliente, degradacoes diferentes ===
  integro=rec_anon_3155ecb4712bfb92  degradado=rec_anon_3155ecb4712bfb92  COLIDEM? True
```

(Dois eventos do mesmo pagador, um degradado, compartilham checkpoint. Correto: é o mesmo
cliente.)

### 2.6 P1-7 / P1-8 — 212.992 combinações, zero violações

Varredura de `schedule()` variando **hora do vencimento** e **hora de `agora`
independentemente**, como pedido:

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
combinacoes testadas : 212992
  violacoes de politica : 0
  excecoes inesperadas  : 0
  LISTA VAZIA c/ janela : 0
  SUB-AGENDAMENTO       : 0
```

Invariantes checadas em cada chamada: `quando <= prazo_final`, `quando >= inicio`,
intervalo ≥ 1 dia entre consecutivas, `usadas + len(ts) <= 3`, ordem crescente, e
`len(ts) == min(3 - usadas, dias_disponiveis + 1)`.

Gate G2 na demo:

```
$ PYTHONIOENCODING=utf-8 python test_pipeline.py
[PIX-RETRY] RN_maria_001: 3 tentativa(s) agendada(s) via previsão do Payday Engine | prazo BACEN: 08/09/2026
[PIX-RETRY]   tentativa 1/3: 02/09 06:10 | R$ 299.90
[PIX-RETRY]   tentativa 2/3: 03/09 06:10 | R$ 299.90
[PIX-RETRY]   tentativa 3/3: 04/09 06:10 | R$ 299.90
[PIX-RETRY] RN_joao_002: 1 tentativa(s) agendada(s) via fallback uniforme | prazo BACEN: 08/09/2026
```

---

## 3. Testes de regressão — falham no commit anterior?

Método: árvore `baseline_audit` (código de `2956a69`), com os três arquivos de teste do
HEAD copiados para dentro dela.

### 3.1 `tests/test_payment_gateway.py` — **corrigido de verdade**

O bloqueio de processo da A1 (o arquivo **nem coletava** no baseline, por importar
símbolos novos) foi resolvido movendo as constantes para literais e usando
`getattr(gateway_module, "PayloadPixInvalido", _NuncaLevantada)`.

```
$ cd ...\baseline_audit\crai; python -m pytest tests/test_payment_gateway.py -q
78 failed, 30 passed, 3 warnings in 9.58s
```

Distribuição dos motivos de falha — **comportamentais, não de importação**:

```
     24 AssertionError: asse...
     18 ValueError: could ...          <- o float() cru do P0-1
     15 KeyError: 'deg...              <- o schema sem `degradacoes`
      2 AttributeError: 'list' object has ...
      1 Failed: DID NOT RAISE <class 'tests.test_payme...
      3 AttributeError: (str / NoneType / int)
```

Zero `ImportError`, zero erro de coleta. Amostra dos testes que falham no baseline e
passam no HEAD:

```
FAILED ...::TestA1ValorAmbiguoNaoEChutado::test_um_separador_com_tres_digitos_e_recusado[10,000]
FAILED ...::TestA1InfinityENaN::test_literal_no_corpo_do_webhook_devolve_400[Infinity]
FAILED ...::TestA1PrecedenciaDeStatus::test_status_da_cobranca_ganha_da_autorizacao
FAILED ...::TestA1PrecedenciaDeStatus::test_cobranca_falhada_com_autorizacao_aprovada_aciona_o_pipeline
FAILED ...::TestA1ValorNaoPositivo::test_valor_negativo_devolve_422
FAILED ...::TestA1DegradacaoTemLeitor::test_pipeline_anuncia_evento_degradado
FAILED ...::TestEndpointRecusaPayloadDegradado::test_valor_em_pt_br_chega_ao_pipeline
FAILED ...::TestEndpointRecusaPayloadDegradado::test_status_cru_failed_aciona_o_pipeline
```

**Item 5 do "mínimo para liberar" da A1: cumprido.**

### 3.2 `tests/test_pix_automatico_retry.py` — regressão honesta

```
$ cd ...\baseline_audit\crai; python -m pytest tests/test_pix_automatico_retry.py -q --tb=line
FAILED ...::TestPrazoDeSeteDias::test_previsao_perto_do_fim_nao_desperdica_tentativa
FAILED ...::TestNenhumaTentativaDesperdicada::test_previsao_no_ultimo_dia_em_hora_tardia_nao_zera
FAILED ...::TestNenhumaTentativaDesperdicada::test_previsao_no_dia_6_de_7_agenda_as_tres
FAILED ...::TestNenhumaTentativaDesperdicada::test_datas_saem_em_ordem_crescente
FAILED ...::TestNenhumaTentativaDesperdicada::test_invariante_parametrico[0-6] [0-7] [1-7]
FAILED ...::TestNenhumaTentativaDesperdicada::test_hora_de_agora_nao_empurra_tentativa_alem_do_prazo[12][18][23]
FAILED ...::TestNenhumaTentativaDesperdicada::test_ancoragem_vazia_cai_no_fallback_em_vez_de_zerar
```

com falhas do tipo certo:

```
...\tests\test_pix_automatico_retry.py:395: AssertionError: assert 1 == 2
...\crai\dunning\pix_automatico_retry.py:270: PixRetryPolicyViolation: Tentativa 3
    agendada para 08/09/2026 12:00, além do prazo de 7 dias corridos que termina em
    08/09/2026 09:00.
...\tests\test_pix_automatico_retry.py:431: assert 0 == 3
```

### 3.3 `tests/test_payment_isolation.py` — **ainda majoritariamente prova de símbolo, não de comportamento**

```
$ cd ...\baseline_audit\crai; python -m pytest tests/test_payment_isolation.py -q --tb=line
...test_payment_isolation.py:257: AttributeError: module 'crai.api.app' has no attribute '_thread_id'
...test_payment_isolation.py:267: AttributeError: module 'crai.api.app' has no attribute '_thread_id'
...test_payment_isolation.py:273: AttributeError: module 'crai.api.app' has no attribute '_thread_id'
...test_payment_isolation.py:280: AttributeError: module 'crai.api.app' has no attribute '_thread_id'
...test_payment_isolation.py:289: AttributeError: module 'crai.api.app' has no attribute '_thread_id'
...test_payment_isolation.py:301: AttributeError: module 'crai.api.app' has no attribute '_thread_id'
...test_payment_isolation.py:310: AttributeError: module 'crai.api.app' has no attribute '_thread_id'
...test_payment_isolation.py:331: assert 200 == 422                    <- COMPORTAMENTAL
...test_payment_isolation.py:337: AttributeError: module 'crai.api.app' has no attribute '_thread_id'
...test_payment_isolation.py:347: AssertionError: 'rec_desconhecida' voltou a ser thread_id:
    customer_id=evento["id_recorrencia"] or "rec_desconhecida",       <- fonte, semi-comportamental
...test_payment_isolation.py:361: TypeError: _extract_features() got an unexpected keyword argument 'customer_id'
...test_payment_isolation.py:403: assert 'state.get("payment_method", "card")' in '...' <- fonte
```

**Dos 13 testes de isolamento, exatamente 1 falha por comportamento observável**
(`test_webhook_sem_identificacao_devolve_422`, ponta a ponta pelo webhook: `200 != 422`),
1 falha por inspeção de código-fonte que ao menos exibe a linha defeituosa do baseline, e
os outros 11 falham porque um símbolo novo não existia. Isso é a mesma fraqueza que a A1
apontou em §4.3, **não corrigida** — e é fraqueza real: um teste que só prova que
`_thread_id` foi criada não prova que a colisão do P0-6 acabou. A prova de que ela acabou
é minha, em §2.5, não da suíte.

### 3.4 P2-12 / N-13 — resolvido pela via honesta

O teste passou a falhar no baseline, mas por assertiva sobre o código-fonte
(`assert 'state.get("payment_method", "card")' in fonte`), não por comportamento. E o
comportamento continua idêntico, como a A1 disse:

```
$ python -c "from crai.agent.main_agent import route_after_decision as r; ..."
  payment_method=None             -> trigger_dunning
  payment_method='card'           -> trigger_dunning
  payment_method='pix_automatico' -> schedule_retry_pix
  payment_method='boleto'         -> trigger_dunning
  payment_method=''               -> trigger_dunning
```

A diferença é que a docstring do teste agora **declara** isso literalmente
("*esta correção é um no-op COMPORTAMENTAL, e o teste abaixo passa igual no commit
anterior*"). Era o que a A1 pediu (item 6: "remover o teste ou reescrever a
justificativa"). **Aceito.**

---

## 4. Parte 2 — status dos 13 achados N-1..N-13

| ID | Ataque original | Ainda passa? | Evidência |
|---|---|---|---|
| **N-1** | `"10,000"`, `"1,299"`, `"2,500"` viram número errado | **NÃO** | §2.1 — os três devolvem `None` + warning + `valor_ilegivel` → 422 |
| **N-2** | `Infinity`/`NaN` chegam ao modelo | **PARCIAL** | §2.2 — literais → 400, strings/floats → 422. **Mas magnitude finita grande continua chegando e produz o mesmo 500** (§2.1) |
| **N-3** | `authorization_status` vence `status: "failed"` | **NÃO** | §2.4 — 11 combinações, cobrança sempre vence |
| **N-4** | `revoked`/`cancelled`/`authorized` no campo `status` viram `desconhecido` | **NÃO** | §2.4 seção E |
| **N-5** | Dois clientes colidem no `thread_id` | **NÃO** | §2.5 — separador, aspas, contrabarra, tipo do valor, base degenerada |
| **N-6** | `degradacoes` não é lido por nenhum nó | **PARCIAL** | `workflow.py:47` agora lê e imprime `[QUALIDADE]`. Ponta a ponta: §4.1. Resíduo: `store_encrypted_pix_key` ainda chama `_resolver_envelope(raw_payload, [])` com lista descartável |
| **N-7** | `/simulate/pix-falhado` não passa por `_thread_id` | **NÃO (para o thread_id)** | `app.py:320` chama `_thread_id`. **Mas o endpoint continua sem os portões de valor** — ver §4.3 |
| **N-8** | `float(previsao["confidence"])` fora do `try` | **SIM — inalterado** | §4.4 |
| **N-9** | `data: [<não-dict>]` cai na raiz | **SIM — inalterado** | §2.3 |
| **N-10** | Valor negativo → LTV/negócio negativo | **NÃO pelo webhook; SIM pelo `/simulate`** | §2.2 e §4.3 |
| **N-11** | `str()` sobre estruturas vaza para o CRM | **SIM — inalterado** | §4.2 |
| **N-12** | `test_pipeline.py` quebra sem `PYTHONIOENCODING=utf-8` | **SIM — inalterado** (pré-existente, fora do diff) | — |
| **N-13** | Correção do P2-12 é no-op | **SIM, e agora declarado** | §3.4 — aceito |

---

## 5. Parte 3 — defeitos NOVOS ou residuais

### 4.1 `degradacoes` — agora tem leitor, e ele funciona

Pergunta do escopo: *"algum caminho o ignora e segue com default?"*

```
$ grep -rn "degradacoes" crai/ --include=*.py | grep -v payment_gateway
crai/agent/workflow.py:47:    degradacoes = state["payment_event"].get("degradacoes") or []
crai/agent/workflow.py:48:    if degradacoes:
crai/agent/workflow.py:49:        print(f"[QUALIDADE] Evento normalizado com degradação: ...")
crai/api/app.py:207-208   (log)
crai/api/app.py:215       bloqueantes = sorted(set(evento["degradacoes"]) & DEGRADACOES_BLOQUEANTES)
crai/api/app.py:314       "degradacoes": [],   # /simulate, sintetico
```

Ponta a ponta, com um `lote_de_1` (degradação **não**-bloqueante):

```
=== M) QUALIDADE: degradacao nao-bloqueante e anunciada no pipeline? ===
[QUALIDADE] Evento normalizado com degradação: lote_de_1 — diagnóstico feito sobre campos preenchidos por default
  (200, {'status': 'ok', 'evento': 'cobranca_falhada', 'pipeline': True})
```

Resíduos, **não** bloqueantes:
- `store_encrypted_pix_key` (`payment_gateway.py:463`) chama `_resolver_envelope(raw_payload, [])`
  e joga a degradação fora.
- `crai/dunning/`, `crai/ml/` e `crai/integrations/hubspot_crm.py` continuam sem
  conhecimento do campo. O único leitor é `diagnose_failure`, e o efeito é imprimir.

### 4.2 `str()` sobre estruturas continua produzindo identificador (N-11)

```
=== J) N-11: estruturas nos campos de identidade ===
  -> {'e2e_id': "{'x': 1}", 'valor': 299.9, 'status': 'cobranca_falhada',
      'ispb_pagador': '[1, 2]', 'id_recorrencia': "['a', 'b']", 'degradacoes': []}
```

Um PSP que mande `recurrence_id` como lista produz a repr do Python como id de cliente —
sem exceção, sem degradação, e esse id vai para `thread_id` e para o HubSpot. **BAIXA**,
inalterado.

### 4.3 🔴 NOVO — `/simulate/pix-falhado` não tem nenhum dos portões do Sprint 1

O endpoint sintetiza `"degradacoes": []` por construção (`app.py:314`) e usa `payload.valor`
direto, sem `_extrair_valor`, sem `_validar_positivo`, sem checagem de finitude.
Consequência (com `ENV=development`, que é o modo da demo):

```
=== Q) /simulate/pix-falhado (usado pela demo) ===
[ROI] [X] R$ -500.00 | taxa R$ 0.00 | e-Profit R$ -230.36
[HUBSPOT-SIM] Deal criado: Recuperação RN_x — R$ -500.00 | pipeline=crai_recovery | stage=retrying
  {'id_recorrencia': 'RN_x', 'valor': -500.0}    -> HTTP 200 {'status': 'pipeline_executado', ...}

[ROI] [X] R$ 0.00 | taxa R$ 0.00 | e-Profit R$ -0.05
[HUBSPOT-SIM] Deal criado: Recuperação RN_y — R$ 0.00 | ... | stage=retrying
  {'id_recorrencia': 'RN_y', 'valor': 0.0}       -> HTTP 200 {'status': 'pipeline_executado', ...}

  {'id_recorrencia': 'RN_z', 'valor': 1e+300}    -> HTTP 500 Internal Server Error

[ROI] [X] R$ nan | taxa R$ 0.00 | e-Profit R$ nan
[HUBSPOT-SIM] Deal criado: Recuperação RN_w — R$ nan | ... | stage=retrying
  {'id_recorrencia': 'RN_w', 'valor': nan}       -> HTTP 200 {'status': 'pipeline_executado', ...}
```

Isto é o **P0-5 e o N-10 inteiros, vivos**, na porta que a demo usa. O `Deal criado:
Recuperação RN_w — R$ nan` é exatamente o tipo de linha que aparece na tela da banca.

Agrava: `test_pipeline.py` — a face da demo hoje — nem passa por aqui. Ele importa
`crai_agent` e chama `ainvoke` direto (`test_pipeline.py:24,83`), **contornando toda a
borda do Sprint 1**. Os "+2 cenários de borda" previstos para o `demo_runner.py` do
Sprint 5 só provam alguma coisa se entrarem pelo webhook (ou ao menos pelo adapter + o
portão de `degradacoes`); pelo caminho atual, provariam o oposto.

### 4.4 N-8 confirmado inalterado — `float(confidence)` fora da proteção

```
  {'timestamp': ..., 'confidence': None}    -> ESCAPA TypeError: float() argument must be a string or a real number, not 'NoneType'
  {'timestamp': ..., 'confidence': 'alta'}  -> ESCAPA ValueError: could not convert string to float: 'alta'
  {'timestamp': ..., 'confidence': nan}     -> 3 tentativa(s)
  {'timestamp': ...} (sem a chave)          -> 3 tentativa(s)
  None                                      -> 3 tentativa(s)
  'isso nao e dict'                         -> ESCAPA AttributeError: 'str' object has no attribute 'get'
```

`_consultar_payday` protege `predict_next_window` com `try/except Exception`, mas a
conversão acontece em `_escolher_datas:180`, **fora** dela. A `PaydayInference` atual
devolve float, então não dispara hoje. O **Sprint 4 é o próximo sprint a mexer nesse
retorno.** Continua P1 latente, não corrigido, e **não está listado na tabela de dívida
do README** (que registra só P2-10, P2-11, P2-13 e a calibração).

### 4.5 🔴 NOVO — o limiar 0.25 (`4109d84`): a mudança é inócua, a justificativa não é reproduzível

**A pergunta do escopo, respondida primeiro:** o limiar **não muda decisão nenhuma do
pipeline.** `LIMIAR_CLASSIFICACAO` aparece em quatro lugares, todos de relatório:

```
$ grep -n "LIMIAR_CLASSIFICACAO" crai/crai/ml/failure_classifier.py
86:LIMIAR_CLASSIFICACAO = 0.25
90:LIMIARES_REPORTADOS = (0.50, 0.40, 0.35, 0.30, LIMIAR_CLASSIFICACAO, 0.20, 0.15)
207:        print(f"[CLASSIFIER] Limiar do relatório {LIMIAR_CLASSIFICACAO:.2f} → ...")
244:        y_pred = (ensemble_proba >= LIMIAR_CLASSIFICACAO).astype(int)   # dentro de _evaluate
257:            "limiar_classificacao": LIMIAR_CLASSIFICACAO,
295:                "em_uso": limiar == LIMIAR_CLASSIFICACAO,
```

`predict()` (linha 324-380) não o usa: devolve `p_recovery` cru e decide por
`eprofit > 0`. Nenhum documento afirma o contrário — `README.md` e `DATA_CARD.md` não
mencionam limiar. **Neste ponto o commit é honesto.**

O problema é outro: **os números que justificam a escolha não se reproduzem.** O
comentário de 40 linhas apresenta uma tabela medida "num holdout de 3.000 linhas" e afirma
que 0,25 é o ponto que **maximiza F2**. Rodei a mesma varredura no mesmo código, duas
vezes, com `_save_models` neutralizado para não sobrescrever os modelos da demo:

```
$ python -c "fc.FailureClassifier._save_models = lambda self: None; clf.train(n_samples=15000)"
holdout: 3000 | AUC: 0.7029 | limiar em uso: 0.25
 limiar    acur    prec  recall      F2  perdidos
   0.50   0.643   0.616   0.548   0.560       610
   0.40   0.634   0.572   0.738   0.697       354
   0.35   0.623   0.554   0.830   0.755       229
   0.30   0.597   0.531   0.895   0.787       141
   0.25   0.559   0.505   0.940   0.802        81
   0.20   0.514   0.480   0.968   0.804        43
   0.15   0.486   0.467   0.990   0.808        14
recall_operacional: {"clientes_abandonados": 3, "n_total": 3000,
                     "recuperaveis_perdidos": 0, "recall": 1.0}
```

Comparando linha a linha com o comentário do código:

| limiar | acurácia (comentário → medido) | recall (com. → med.) | F2 (com. → med.) | perdidos (com. → med.) |
|---|---|---|---|---|
| 0,50 | 0,732 → **0,643** | 0,607 → **0,548** | 0,614 → **0,560** | 436/1109 → **610** |
| 0,35 | 0,696 → **0,623** | 0,810 → **0,830** | 0,744 → **0,755** | 211/1109 → **229** |
| 0,25 | 0,636 → **0,559** | 0,907 → **0,940** | 0,782 → **0,802** | 103/1109 → **81** |
| 0,15 | 0,527 → **0,486** | 0,964 → **0,990** | 0,777 → **0,808** | 40/1109 → **14** |

Três consequências:

1. **O argumento central é falso no código de hoje.** O comentário diz "*0,25 é o ponto
   que **maximiza F2** no holdout (F2 = 0,782)*". Na varredura real, F2 **cresce
   monotonicamente** até o menor limiar testado: 0,802 (0,25) < 0,804 (0,20) < 0,808
   (0,15). 0,25 não é o argmax de nada. Na tabela do comentário ele era, porque lá F2 caía
   de 0,782 para 0,777 — comportamento que este código não produz.
2. **O dataset não bate.** O comentário diz 1109 recuperáveis no holdout de 3.000
   (37,0%). O gerador atual produz:
   ```
   $ python -c "from crai.ml.synthetic_data import generate_dataset; ..."
   n=3000: recovered.mean()=0.4583  -> em holdout 20%: 275 recuperaveis
   n=15000: recovered.mean()=0.4495 -> em holdout 20%: 1349 recuperaveis
   determinista? True
   ```
   1349, não 1109. `synthetic_data.py` **não foi tocado pelo diff** e o gerador é
   determinístico (`SEED=42`), então não há como o número do comentário ter saído deste
   código.
3. **A recall operacional citada também não bate:** o comentário diz "abandona 71 clientes
   (2,37%) e perde **1** único recuperável — recall OPERACIONAL 99,9%". Medido: **3
   abandonados, 0 perdidos, recall 100,0%.**

Além disso o commit **não traz teste nenhum**:

```
$ git diff --stat baseline-pre-sprint..HEAD -- crai/tests/test_failure_classifier.py
(vazio)
$ grep -n "LIMIAR\|limiar\|0.25" crai/tests/test_failure_classifier.py
(nenhuma ocorrência)
```

Isso viola a regra do plano ("*Todo bug corrigido nasce com teste de regressão que falha
antes e passa depois. Sem exceção*") e, pior, deixa sem trava justamente o número que a
banca vai perguntar de onde veio. **Gravidade ALTA — não pelo risco técnico (o limiar é
inócuo), mas porque é um número apresentado como medido que a máquina do TCC não
reproduz.** É exatamente o tipo de coisa que a §2.1 do `DATA_CARD` promete não fazer.

### 4.6 Achado colateral — o artefato de métricas em disco está obsoleto e abaixo do gate

```
$ python -c "import json; print(json.load(open('crai/models/train_metrics.json')))"
{"auc": 0.6797, "accuracy": 0.64, "recall_recovered": 0.6291, ..., "n_total_test": 600}
```

Três fatos: (a) o arquivo tem o **schema antigo** — sem `limiar_classificacao`, sem
`metricas_por_limiar`, sem `recall_operacional` —, ou seja, os modelos que estão em
`crai/models/` e que a demo carrega hoje foram treinados **antes** do commit `4109d84`;
(b) `n_total_test: 600` ⇒ treinados com `n_samples=3000`, não 15.000; (c) **AUC 0,6797
está abaixo do piso [0,70 ; 0,92]** que os gates G3 e G4 exigem. Não é falha das Sprints 1
e 2 — é do Sprint 3/4 —, mas quem for para o G4 com estes binários reprova.

---

## 6. O que resistiu

Registrado porque auditoria que só acusa não é auditoria.

- **P0-4/N-3/N-4:** a precedência entre `status` e `authorization_status` está correta e
  cada campo é consultado nos dois mapas. Não consegui reconstruir o sumiço silencioso da
  cobrança falhada em nenhuma das 11 combinações nem com a fixture do próprio repositório.
- **N-1:** a regra "um separador seguido de exatamente três dígitos é ambíguo → recusa" é
  a política certa e está implementada simetricamente para `,` e `.`.
- **P0-6/N-5:** base serializada como JSON + `repr(valor)` + recusa 422 quando não há
  discriminante. Ataquei com separador cru, aspas, contrabarra, tipo numérico e base
  degenerada; nenhum colidiu.
- **P1-7/P1-8:** 212.992 combinações, zero violações de invariante, zero sub-agendamento.
  A contagem por `timedelta` em `_dias_da_janela` continua sendo a decisão certa.
- **Processo:** a suíte `test_payment_gateway.py` agora **coleta e falha por
  comportamento** no baseline (78 failed). Era o principal bloqueio de processo do GA1.
- **Cobertura:** `payment_gateway.py` em **94%** (gate G1 pede ≥ 90%).
  ```
  crai\integrations\payment_gateway.py  175  10  94%  238, 243, 247, 403-406, 495-496, 524, 546
  ```
- **Fuzz:** 1.088 webhooks assinados hostis; **os únicos 5xx são de magnitude numérica**.
  Nenhuma outra classe de payload derruba a API.
- **Privacidade:** a chave Pix continua fora do schema normalizado e fora de `degradacoes`
  em todos os payloads que testei.
- **Suíte:** 304 passed, zero regressão.

---

## 7. Veredito

# BLOQUEADO para o Sprint 3.

Os oito defeitos originais estão em muito melhor estado que na A1 — seis **CONFIRMADOS**,
dois **PARCIAIS** —, e os quatro itens que a A1 declarou inegociáveis (N-1, N-2, N-3, N-5)
foram fechados, três deles completamente. O bloqueio agora tem outra natureza: **um
caminho de 500 alcançável por webhook assinado**, **um endpoint que contorna toda a borda
que o Sprint 1 construiu**, e **um número apresentado como medido que a própria máquina
desmente.**

### Mínimo necessário para liberar, em ordem de gravidade

**Bloqueantes (P0):**

1. **`_para_float` tem que cumprir a própria docstring e ter teto.** Envolver
   `float(bruto)` em `try/except (OverflowError, ValueError)` → `None`, e **recusar
   magnitude implausível** (um teto explícito — p.ex. `abs(valor) > 1e12` → `valor_ilegivel`),
   porque `math.isfinite` deixa passar `1e298`, que estoura o `float32` do sklearn três nós
   adiante. Teste obrigatório: `{"data":{"valor": 9…9 (401 dígitos)}}` e
   `{"data":{"total_cents": 1e300}}` → **422, nunca 500**. Hoje: `70 respostas 5xx em 1088`.
2. **`/simulate/pix-falhado` tem que passar pelos mesmos portões do webhook.** Chamar
   `_extrair_valor`/`_validar_positivo` (ou replicar a checagem de finitude e positividade)
   e devolver 422 em `valor <= 0`, `NaN` e magnitude implausível. Hoje o endpoint da demo
   cria deal de `R$ -500,00` e `R$ nan` no CRM, e devolve 500 com `1e300`.
3. **Corrigir ou remover a tabela de justificativa do limiar 0,25.** Ou se reproduzem os
   números (declarando o dataset, o `n_samples` e a seed que os geraram), ou se substitui
   pela varredura que este código realmente produz — onde **0,25 não maximiza F2** e o
   argumento escrito precisa ser outro (p.ex.: "0,25 é o joelho da curva; abaixo dele a
   precisão cai mais rápido que o F2 sobe"). E o commit precisa nascer com o teste que o
   plano exige — hoje `test_failure_classifier.py` não tem uma linha sobre o assunto.

**Bloqueante de processo (gate GA1):**

4. **`test_payment_isolation.py` precisa de regressão comportamental para o P0-6.** 11 dos
   13 testes falham no baseline por `AttributeError: module 'crai.api.app' has no attribute
   '_thread_id'` — prova de que o símbolo é novo, não de que a colisão acabou. Basta
   exercitar o caminho ponta a ponta pelo webhook (como
   `test_webhook_sem_identificacao_devolve_422` já faz corretamente): dois webhooks
   assinados de pagadores anônimos distintos, capturando o `thread_id` entregue ao
   `MemorySaver` — no baseline os dois são `"rec_desconhecida"` e o teste falha por
   igualdade, não por importação.

**Aceitáveis como dívida, desde que listados no README (hoje NÃO estão):**

5. N-8 (`float(confidence)` fora do `try` — vira P0 no Sprint 4, que é o próximo a mexer
   no retorno do Módulo 3), N-9 (`data:[<não-dict>]`), N-11 (`str()` sobre estruturas),
   N-12 (`PYTHONIOENCODING`), o resíduo do N-6 (`store_encrypted_pix_key` descartando
   degradação), e §4.6 (os modelos em `crai/models/` estão com AUC 0,6797, abaixo do piso
   dos gates G3/G4, e `train_metrics.json` com schema anterior ao commit `4109d84`).

**Fora do escopo desta auditoria, mas com prazo:** `test_pipeline.py` contorna a borda
inteira (`crai_agent.ainvoke` direto). Os "+2 cenários de borda" do Sprint 5 só provam o
Sprint 1 se entrarem pelo webhook assinado. E o P2-9 (o resumo que mente:
`Janela BACEN esgotada: 0/3`, `Clientes retidos 3/4` **e** `Escalados 3/4`
simultaneamente) continua reproduzível na saída de hoje.

---

*Re-auditoria executada sem acesso às justificativas da implementação. Prova de regressão
feita em worktree descartável (`baseline-pre-sprint`), removida ao fim; `git status --short`
na árvore principal: `?? .claude/`. Scripts de ataque em scratchpad temporário. Único
arquivo alterado por esta auditoria: `docs/AUDITORIA_01_R2.md`.*

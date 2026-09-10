# AUDITORIA ADVERSARIAL A1 — RODADA 7

**Diff sob auditoria:** `8786d82..c7c6870` (correção da rodada 6)
**Contexto do sprint:** `baseline-pre-sprint (2956a69)..c7c6870`
**Branch:** `sprint/a1-auditoria` · **HEAD:** `c7c687050390ff09071892dfdeb0d04ed98331cb`
**Ambiente da medição:** Windows 10, Python 3.12.10, FastAPI 0.115.0, httpx 0.27.2, console cp1252 (`PYTHONIOENCODING=utf-8`)

---

## VEREDITO: 🔴 **BLOQUEADO** (GA1)

Um bloqueio 🔴, um 🟠 e três 🟡.

O bloqueio principal da rodada 6 — **3×N tentativas na mesma janela de 7 dias por
entregas concorrentes** — está **fechado**, e fechado de verdade: eu ataquei a
trava por sete caminhos diferentes (10 concorrentes, pressão de GC no meio,
webhook misturado com `/simulate/*`, chegada sequencial, ids com espaços,
50 requisições de 25 clientes, e a janela do BACEN atravessando 7 dias) e o
limite de 3 se manteve em todos. Os testes novos reprovam em `8786d82` **por
asserção**, não por símbolo ausente. A catraca de encoding agora mede a raiz de
verdade e o inventário bate nas duas pontas.

O que segura o gate é a outra metade do diff: a **blindagem de forma dos
webhooks**. Ela continua deixando passar **HTTP 500 em endpoint assinado**, num
campo que o próprio diff declara ter blindado, com o remédio já escrito no
mesmo arquivo e a uma linha de distância. E a linha `N-15` do README, que existe
justamente para declarar o que ficou de fora, declara a fronteira errada.

| ID | Severidade | Assunto | Introduzido por |
|---|---|---|---|
| **R7-1** | 🔴 bloqueio | `/webhooks/stripe` com assinatura válida e `amount_due` acima do alcance do `float` → **HTTP 500** (`OverflowError`). `amount_due` está na lista de campos que a `N-15` declara blindados, e a `N-15` afirma "zero 5xx". | comportamento pré-existente; a **declaração falsa** é deste diff |
| **R7-2** | 🟠 bloqueio menor | `_recusar_inteiro_grande_demais` é recursão sem limite sobre input do PSP: `properties` com ≥ ~950 níveis → `RecursionError` → **HTTP 500**. A docstring da função afirma cobrir "qualquer profundidade". | função **é deste diff**; o 500 nessa entrada é pré-existente |
| **R7-3** | 🟡 dívida | A docstring de `TestA1R6BordaAssinadaNaoDerrubaAApi` diz "5 em `/webhooks/stripe` — 16 no total". Medido: **6 e 17** — contradito pela saída do teste-irmão do mesmo arquivo. | deste diff |
| **R7-4** | 🟡 dívida | O corpo do README (fora da tabela de dívida) contém 4 afirmações falsas sobre o sistema, uma delas na instrução "Testar churn involuntário", que não testa churn involuntário. | pré-existente; não declarado |
| **R7-5** | 🟡 dívida | `userId` virou obrigatório em `/webhooks/segment`: evento legítimo do Segment com `anonymousId` passa de **200 para 422**. Mudança de superfície não declarada em lugar nenhum. | deste diff |

---

# 🔴 R7-1 — `/webhooks/stripe` assinado ainda devolve 500

## O que o diff declara

`crai/README.md`, linha `N-15`, escrita por `c7c6870`:

> A blindagem de forma dos webhooks valida os campos que o pipeline **consome
> hoje** (`userId`, `event`, `properties`, `billing_profile`,
> `data.object.amount_due`, `attempt_count`), não o grafo inteiro do payload. Um
> campo novo que o pipeline passe a ler sem entrar nessa lista volta a
> atravessar a borda sem checagem. […] Medido depois da correção: 396
> requisições hostis (3 webhooks assinados + 3 `/simulate/*`, 18 valores por
> campo) devolveram `{200: 174, 400: 64, 422: 158}` e **zero 5xx**.

E `crai/crai/api/app.py:385-390`, também deste diff:

```python
    if "amount_due" in fatura:
        _campo_com_forma(fatura, "amount_due", (int, float), "STRIPE",
                         nulo_e_ausente=False)
```

A fronteira declarada da dívida é: *campo fora da lista volta a atravessar sem
checagem*. `amount_due` está **dentro** da lista, é checado, e mesmo assim
derruba a API.

## A medição

`fuzz.py` — 43 payloads hostis, os 3 webhooks assinados com HMAC **válido** e os
3 `/simulate/*` com `ENV=development`:

```
cd /d/PTI/crai && PYTHONIOENCODING=utf-8 PYTHONPATH=/d/PTI/crai python fuzz.py
```

```
   422 | /webhooks/segment            | segment nested 1500
   422 | /webhooks/stripe             | stripe nested 1500
   422 | /webhooks/pix-automatico     | pix nested 1500
   200 | /webhooks/segment            | segment nested dict 600
   500 | /webhooks/stripe             | stripe amount_due 2**2000
   200 | /webhooks/stripe             | stripe attempt_count 2**2000
   200 | /webhooks/stripe             | stripe amount_due 1e400
   200 | /webhooks/stripe             | stripe amount_due -1e400
   200 | /webhooks/stripe             | stripe customer dict
   200 | /webhooks/stripe             | stripe customer list
   200 | /webhooks/stripe             | stripe invoice id list
   422 | /webhooks/stripe             | stripe object list
   200 | /webhooks/stripe             | stripe type list
   ... (30 linhas restantes: 200 / 400 / 422, nenhuma 5xx)

=== 5xx/EXC: 1
    ('/webhooks/stripe', 'stripe amount_due 2**2000', 500)
```

Reprodução isolada, com o traceback:

```
cd /d/PTI/crai && PYTHONIOENCODING=utf-8 PYTHONPATH=/d/PTI/crai python stripe_500.py
```

```
  File "C:\...\fastapi\routing.py", line 212, in run_endpoint_function
    return await dependant.call(**values)
  File "D:\PTI\crai\crai\api\app.py", line 393, in stripe_webhook
    _registrar_cartao_desativado(event)
  File "D:\PTI\crai\crai\api\app.py", line 663, in _registrar_cartao_desativado
    dados = _dados_stripe(event)
  File "D:\PTI\crai\crai\api\app.py", line 678, in _dados_stripe
    "amount": invoice.get("amount_due", 0) / 100,
              ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^~~~~
OverflowError: integer division result too large for a float
STATUS SEM RAISE: 500 Internal Server Error
```

O corpo tem assinatura HMAC-SHA256 válida com o `STRIPE_WEBHOOK_SECRET`
configurado. Ele passa pelo `_objeto_json_do_corpo` (é objeto), passa pelo
`_campo_com_forma` (`2**2000` **é** um `int`), e morre três linhas depois.

## Por que é defeito

1. **A blindagem falha exatamente no campo que ela diz blindar.** `_campo_com_forma`
   valida o *tipo* de `amount_due` e nada mais. `2**2000` é `int`, portanto passa.
   A operação seguinte é `/ 100`, que só existe para `float`, e acima de ~1.8e308
   o `int` não cabe num `float`.

2. **O remédio já está escrito, no mesmo arquivo, e não foi chamado.** Este mesmo
   diff criou `_recusar_inteiro_grande_demais` (`app.py:583`) com
   `LIMITE_INTEIRO_SERIALIZAVEL = 2**63 - 1`, e o chamou **só no
   `/webhooks/segment`** (`app.py:492`). O mesmo `2**2000` que derruba o Stripe
   é recusado com 422 no Segment:

   ```
   2**200 a     3 niveis de profundidade -> 422    (segment)
   ```

   Ou seja: a mesma classe de defeito, no mesmo commit, fechada num webhook e
   aberta no outro.

3. **A declaração da `N-15` está errada na direção que importa.** Ela diz que o
   risco residual é "campo novo, fora da lista". O risco residual medido é
   "campo da lista, valor dentro do tipo, fora do alcance da operação". Quem
   ler a `N-15` para decidir se pode subir isso não vai olhar para `amount_due`,
   porque a `N-15` diz que `amount_due` está coberto. Pela regra da própria
   auditoria: declaração incompleta é pior que ausência de declaração.

4. **O teste do diff afirma que a classe está fechada.**
   `tests/test_webhook_security.py::test_nenhum_payload_torto_produz_5xx` tem
   por docstring *"A asserção do Sprint 1, medida de uma vez sobre a classe
   inteira."* Ele mede 17 payloads, não a classe. Com um 18º a classe reabre.

## Por que está DENTRO do escopo

`sprints.md:157` põe fora de escopo *"Reativar recobrança de cartão
(`dunning/legacy_card/`) — fica isolado como está"*. O defeito não está em
`dunning/legacy_card/` e não pede reativar nada: está em `crai/api/app.py`, na
borda de entrada, e o próprio `c7c6870` reescreveu essas linhas — adicionou
`_objeto_json_do_corpo` e `_campo_com_forma` ao `/webhooks/stripe` e escreveu
quatro testes que exigem 400/422 daquele endpoint. O implementador colocou o
endpoint em escopo; eu estou medindo o que ele colocou lá.

O objetivo textual do Sprint 1 (`sprints.md:215`), citado pelo próprio teste do
diff: *"nenhum payload de PSP, por mais torto que seja, derruba a API ou entra
no pipeline em silêncio."* Um payload de PSP derruba a API.

## Atenuante honesto

O endpoint exige assinatura válida, então o gatilho é o PSP (ou quem tenha o
segredo), não um anônimo. E `/webhooks/stripe` não roda pipeline: registra e
responde. O impacto é 500 + tempestade de retry do PSP, não corrupção de estado.
É por isso que a correção é de uma linha — chamar
`_recusar_inteiro_grande_demais(fatura, "STRIPE", "data.object")` — e não de um
sprint.

---

# 🟠 R7-2 — A guarda contra input hostil é recursão sem limite sobre input hostil

## O que o diff declara

`crai/crai/api/app.py:583-601`, escrito por `c7c6870`:

```python
def _recusar_inteiro_grande_demais(valor, origem: str, caminho: str = "properties"):
    """Varre o payload atrás de inteiro fora do alcance do checkpoint.
    ...
    É recursivo porque `properties` é um objeto livre: o número pode estar em
    qualquer profundidade, e barrar só o primeiro nível deixaria a porta aberta.
    """
```

## A medição

```
cd /d/PTI/crai && PYTHONIOENCODING=utf-8 PYTHONPATH=/d/PTI/crai python profundidade.py
```

```
sys.getrecursionlimit() = 1000
segment  properties aninhado    100 niveis -> 200
segment  properties aninhado    300 niveis -> 200
segment  properties aninhado    500 niveis -> 200
segment  properties aninhado    700 niveis -> 200
segment  properties aninhado    900 niveis -> 200
segment  properties aninhado   1200 niveis -> 500
segment  properties aninhado   2000 niveis -> 500
segment  properties aninhado   5000 niveis -> 500
segment  properties aninhado  20000 niveis -> 500
pix      data aninhado          500 niveis -> 200
pix      data aninhado         2000 niveis -> 200
pix      data aninhado        20000 niveis -> 500
```

O traceback diz que agora quem morre é a própria guarda:

```
  File "D:\PTI\crai\crai\api\app.py", line 492, in segment_webhook
    _recusar_inteiro_grande_demais(props, "SEGMENT")
  File "D:\PTI\crai\crai\api\app.py", line 600, in _recusar_inteiro_grande_demais
    _recusar_inteiro_grande_demais(item, origem, f"{caminho}.{chave}")
  [Previous line repeated 969 more times]
RecursionError: maximum recursion depth exceeded
```

E a promessa de "qualquer profundidade" quebra exatamente onde a recursão
quebra — o mesmo `2**200` que é recusado com 422 a 900 níveis vira 500 a 1200:

```
2**200 a     3 niveis de profundidade -> 422
2**200 a   900 niveis de profundidade -> 422
2**200 a  1200 niveis de profundidade -> 500
```

## Por que é 🟠 e não 🔴

**Não é regressão.** Rodei a mesma varredura na worktree de `8786d82` e o
resultado é idêntico linha por linha — as mesmas profundidades já davam 500
antes, de outro ponto do código:

```
cd .../auditoria-r7/r6/crai && python profundidade.py
segment  properties aninhado    900 niveis -> 200
segment  properties aninhado   1200 niveis -> 500
...
pix      data aninhado        20000 niveis -> 500
```

O diff moveu o local da morte para dentro da sua própria guarda; não piorou o
código de resposta. O que é deste diff é a **afirmação** de cobrir "qualquer
profundidade", que não se sustenta acima de ~950 níveis, e o fato de a única
função nova cuja razão de existir é resistir a payload hostil ser ela mesma
derrubável por payload hostil. Um `if` de profundidade máxima (ou uma pilha
explícita) resolve.

## Por que está DENTRO do escopo

A função é nova neste diff, mora em `crai/api/app.py`, e é o objeto direto da
blindagem de forma que o diff entrega.

---

# 🟡 R7-3 — Um número errado na docstring que o teste-irmão desmente

`crai/tests/test_webhook_security.py:355-357`, escrito por `c7c6870`:

> Medido em `8786d82`, com assinatura VÁLIDA e o pipeline REAL (sem stub):
> **11 payloads davam HTTP 500 em `/webhooks/segment` e 5 em `/webhooks/stripe`**
> — 16 no total.

Copiei os três arquivos de teste de `c7c6870` para a worktree de `8786d82` e
rodei a classe inteira contra o código antigo:

```
cd .../auditoria-r7/r6/crai && python -m pytest tests/test_webhook_security.py -k A1R6 -q
```

```
E  AssertionError: 17 de 17 payloads ASSINADOS derrubaram a API com 5xx:
   [('/webhooks/segment', b'not json', 500), ('/webhooks/segment', b'[]', 500),
    ('/webhooks/segment', b'"texto"', 500), ('/webhooks/segment', b'5', 500),
    ('/webhooks/segment', b'null', 500), ('/webhooks/segment', b'{"userId":null}', 500),
    ('/webhooks/segment', b'{"userId":[1],"event":"x"}', 500),
    ('/webhooks/segment', b'{"userId":{"a":1},"event":"Session Started"}', 500),
    ('/webhooks/segment', b'{"userId":"u","event":"x","properties":"nao-dict"}', 500),
    ('/webhooks/segment', b'{"userId":"u","event":"x","properties":[1,2]}', 500),
    ('/webhooks/segment', b'{"userId":"u",...,"properties":{"billing_profile":[1]}}', 500),
    ('/webhooks/stripe', b'not json', 500), ('/webhooks/stripe', b'[]', 500),
    ('/webhooks/stripe', b'{"type":"invoice.payment_failed","data":"nao-dict"}', 500),
    ('/webhooks/stripe', b'{"type":"invoice.payment_failed","data":null}', 500),
    ('/webhooks/stripe', b'{...,"data":{"object":{"amount_due":"abc"}}}', 500),
    ('/webhooks/stripe', b'{...,"data":{"object":{"amount_due":null}}}', 500)]
19 failed, 1 passed, 34 deselected
```

Segment: 11 ✅. Stripe: **6**, não 5. Total: **17**, não 16. As listas
`STRIPE_NAO_OBJETO` (2 itens) + `STRIPE_FORMA_ERRADA` (4 itens) do próprio
arquivo já somam 6 — o número da docstring é desmentido pelo código dez linhas
abaixo dele e pela saída do teste-irmão. Não muda nenhuma conclusão; é
o tipo de número que a rodada seguinte cita como verdade medida.

---

# 🟡 R7-4 — O corpo do README descreve um sistema que não existe mais

A tabela de dívida do README foi auditada em cinco rodadas. O corpo do README,
acima dela, não. Ele contém quatro afirmações que não sobrevivem à medição:

**(a) "O `test_pipeline.py` roda **8 cenários** (4 de cada tipo de churn)"** —
`crai/test_pipeline.py:110-136` tem 3 cenários de Pix + 2 de cartão + 4 de churn
voluntário = **9**, e a partição é 5/4, não 4/4.

**(b) O fluxograma do churn involuntário** (README, "Os dois pipelines") diz
`Stripe webhook → … → Backoff Exponencial → …`. Nenhuma das duas pontas é
verdade desde a Fase 3: a entrada é `/webhooks/pix-automatico` (o Stripe
responde `pipeline: false`) e a retentativa é `PixAutomaticoRetryPolicy` na
janela do BACEN — o `SmartBackoff` é explicitamente proibido nesse caminho
(`workflow.py:263`: *"Nunca chama SmartBackoff: o backoff exponencial estouraria
o limite de 3 tentativas"*).

**(c) A seção "Estrutura"** descreve `api/app.py` como *"FastAPI: webhooks Stripe
+ Segment + endpoints de simulação"* — omite `/webhooks/pix-automatico`, que é
o único webhook que alimenta o pipeline ativo.

**(d) A instrução "Testar churn involuntário" não testa churn involuntário.**
Rodei o `curl` do README:

```
README 'Testar churn involuntário' -> 200 {"status":"registrado","pipeline":false,
  "customer_id":"cus_teste","motivo":"recobranca_automatica_de_cartao_fora_do_pipeline_ativo"}
```

`pipeline: false`. Quem seguir o README para conferir o pipeline involuntário vê
um no-op e conclui que funciona. O endpoint que exercita o pipeline
(`/simulate/pix-falhado`) não aparece no README.

**(e)** O cabeçalho da tabela de dívida ainda diz *"auditorias adversariais A1 e
suas re-rodadas (r2 a r5)"*, enquanto a tabela abaixo já cita correções da r6 e
carrega duas linhas novas (`P1-15`, `N-15`) nascidas na r6.

Nada disso é bloqueio: não muda comportamento e não é dívida escondida por
conveniência — é README que envelheceu. Fica como 🟡 porque o README é
entregável, porque (d) tem consequência prática na hora de conferir a demo, e
porque a tabela de dívida logo abaixo se apresenta como o inventário completo
do que está errado.

---

# 🟡 R7-5 — `userId` virou obrigatório e ninguém declarou

`app.py:487-488`, deste diff:

```python
    user_id = _campo_com_forma(payload, "userId", (str,), "SEGMENT",
                               obrigatorio=True)
```

Antes: `payload.get("userId", "usr_unknown")`. Medido nas duas pontas com o mesmo
payload — um evento `track` do Segment que traz `anonymousId` em vez de `userId`,
que é a forma normal de um visitante não logado:

```
### HEAD c7c6870
segment so com anonymousId -> 422 {"detail":{"motivo":"campo_obrigatorio_ausente","campo":"userId"}}
### r6 8786d82
segment so com anonymousId -> 200 {"status":"ok"}
```

**Não estou dizendo que 422 é a escolha errada** — é a mesma escolha que o P0-6
fez do lado do Pix, e pelo mesmo motivo: `usr_unknown` fazia todos os anônimos
dividirem o mesmo `thread_id` do `MemorySaver`, que é o defeito original. A
escolha é defensável e provavelmente certa.

O defeito é que ela é **invisível**. A `N-15` descreve a blindagem como validação
de forma dos campos que o pipeline consome; não diz que um deles passou a ser
obrigatório nem que isso muda quais eventos legítimos o serviço aceita. É a
única mudança do diff que rejeita tráfego que antes era processado, e é a única
que não está escrita em lugar nenhum. Uma linha na tabela resolve.

---

# O que eu verifiquei e estava certo

## 1. A trava por `thread_id` fecha o R6-1, e fecha de verdade

Ataquei por sete caminhos, todos com o webhook assinado ou `/simulate/*` reais,
sem mock do pipeline, `httpx.ASGITransport` num único event loop:

```
cd /d/PTI/crai && PYTHONIOENCODING=utf-8 PYTHONPATH=/d/PTI/crai python conc.py
```

```
### A) 10 entregas concorrentes, mesmo id_recorrencia
  status      : [200, 200, 200, 200, 200, 200, 200, 200, 200, 200]
  TOTAL agend.: 3   numeros=[1, 2, 3]
  checkpoint  : retry_count=3 janela_ate=2026-09-11 22:20:14
  VEREDITO    : OK (<=3)
### B) 6 concorrentes + gc.collect() em loop no meio
  TOTAL agend.: 3   numeros=[1, 2, 3]        VEREDITO: OK (<=3)
### C) 3 webhooks + 3 /simulate/pix-falhado concorrentes, mesmo id
  TOTAL agend.: 3   numeros=[1, 2, 3]        VEREDITO: OK (<=3)
### D) 5 entregas SEQUENCIAIS, mesmo id_recorrencia
  TOTAL agend.: 3   numeros=[1, 2, 3]        VEREDITO: OK (<=3)
### E) 4 concorrentes com id_recorrencia com/sem espacos
  TOTAL agend.: 3   numeros=[1, 2, 3]        VEREDITO: OK (<=3)
```

O caso (B) é o ataque específico à `WeakValueDictionary`: uma corrotina rodando
`gc.collect()` 200 vezes intercalada com as requisições, para tentar coletar a
trava no meio do uso. Não coletou — a `__aexit__` do `async with` mantém
referência forte durante o bloco inteiro, e quem espera na fila também.

Carga maior, para provar que a trava é **por cliente** e não uma fila global:

```
cd /d/PTI/crai && python muitos.py     # 50 requisicoes simultaneas, 25 clientes
status distintos : [200]
clientes         : 25
total agendado   : 75  esperado 75
clientes >3      : {}
clientes <3      : {}
dict de travas apos tudo: {}
duracao          : 6.5s
VEREDITO: OK
```

`dict de travas apos tudo: {}` — a `WeakValueDictionary` esvazia sozinha; não há
vazamento indexado por id de cliente.

## 2. A trava não vaza entre event loops

`asyncio.Lock` (3.10+) se amarra ao loop no primeiro uso e levanta `RuntimeError`
se for reusada em outro. `_travas_por_cliente` é global de módulo, então uma
trava que sobrevivesse ao seu loop envenenaria o próximo. Testei inclusive o
caso em que um handler de erro **guarda a exceção** (e portanto o traceback, e
portanto os frames, e portanto a trava), com o GC cíclico desligado:

```
cd /d/PTI/crai && python loops.py
1) apos loop 1, dict = {}
   loop 2 com o mesmo id: OK (trava foi coletada e recriada)
2) apos excecao guardada, dict = {}
   loop 2 com o mesmo id: OK
```

O `async with` solta a trava mesmo quando o bloco levanta, e a contagem de
referências libera a entrada antes do loop seguinte. Não consegui produzir
`RuntimeError` por nenhum caminho realista.

## 3. A janela do BACEN: 3 por janela, cada janela nova com direito próprio

Relógio congelado via `workflow._agora`, entregas **concorrentes** em cada ponto:

```
cd /d/PTI/crai && python janela.py
relogio congelado em 2026-03-02 10:00:00
D+0    primeiro estouro (3 concorrentes)      n=3 agendou= 3 retry_count=3 janela_ate=2026-03-09 10:00
D+1    mesma janela, 5 concorrentes           n=5 agendou= 0 retry_count=3 janela_ate=2026-03-09 10:00
D+6h23 ainda na mesma janela, 3 concorrentes  n=3 agendou= 0 retry_count=3 janela_ate=2026-03-09 10:00
D+7h1  JANELA NOVA, 5 concorrentes            n=5 agendou= 3 retry_count=3 janela_ate=2026-03-16 11:00
D+7h2  mesma janela nova, 5 concorrentes      n=5 agendou= 0 retry_count=3 janela_ate=2026-03-16 11:00
D+20   terceira janela, 4 concorrentes        n=4 agendou= 3 retry_count=3 janela_ate=2026-03-29 10:00

janela 1 (D+0..D+6h23): 3  (limite 3)
janela 2 (D+7h1..D+7h2): 3 (limite 3)
janela 3 (D+20):        3  (limite 3)
VEREDITO: OK
```

13 requisições concorrentes dentro da janela 1 renderam 3 instruções. A janela
não é reancorada a cada webhook (o `janela_ate` de D+1 e D+6h23 continua
`2026-03-09`), e uma janela vencida devolve as 3 tentativas que o BACEN concede
a ela. Não achei combinação que estoure.

## 4. Os testes novos reprovam em `8786d82` por asserção, não por símbolo ausente

Worktree descartável em `8786d82`, arquivos de teste de `c7c6870` copiados por
cima, código antigo intacto:

```
tests/test_payment_isolation.py -k A1R6  →  4 failed, 1 passed
E  AssertionError: LIMITE BACEN VIOLADO por concorrência: 2 entregas simultâneas
   do mesmo id_recorrencia agendaram 6 tentativas na mesma janela de 7 dias
   (máximo 3). Lotes: [3, 3].                                    assert 6 <= 3
E  AssertionError: LIMITE BACEN VIOLADO por concorrência: 3 entregas ...
   agendaram 9 tentativas ... Lotes: [3, 3, 3].                  assert 9 <= 3
E  AssertionError: numeração repetida na mesma janela:
   [1, 2, 3, 1, 2, 3, 1, 2, 3]                                   assert 9 == 3
E  AssertionError: a política agendou 9 tentativa(s) e o checkpoint registra 3.  assert 3 == 9

tests/test_webhook_security.py -k A1R6  →  19 failed, 1 passed
PASSED ...::test_payload_bem_formado_continua_passando
```

Todas as falhas são `AssertionError` com número medido, nenhuma é `ImportError`
ou `AttributeError`. E os dois testes que **passam dos dois lados** — o
contrapeso `test_clientes_diferentes_continuam_correndo_em_paralelo` e o
`test_payload_bem_formado_continua_passando` — estão **declarados como catraca**
na própria docstring (*"Contrapeso: a trava é por cliente, não uma fila global"*
e *"Catraca, não regressão — este caso já passava em `8786d82`"*). Isso é
exatamente o que a regra 4 exige.

O contrapeso não é decorativo: `test_clientes_diferentes...` exige
`total == 3 * MAX_TENTATIVAS_PIX`, ou seja, reprova uma trava global que o teste
de limite aceitaria. Ele distingue a correção certa da errada.

## 5. A catraca de encoding mede a raiz de verdade, e o 23 bate nas duas pontas

Medido com script próprio (não o do projeto), mesma metodologia (`tokenize`,
literais fora de docstring), a partir de `D:/PTI`:

```
python enc.py /d/PTI
archive/protótipos-pré-unificação/modulo_04_offer_bandit/src/visualizar.py: 1  [119]
crai/crai/agent/workflow.py: 1  [249]
crai/crai/churn_voluntary/voluntary_agent.py: 2  [118, 118]
crai/crai/dunning/dunning_engine.py: 1  [109]
crai/crai/dunning/pix_automatico_retry.py: 1  [355]
crai/crai/ml/anomaly_detector.py: 2  [145, 145]
crai/test_pipeline.py: 15  [47,48,55,56,89,89,103,105,107,144,144,148,159,166,171]
TOTAL 23
```

E na worktree de `baseline-pre-sprint`:

```
python enc.py .../auditoria-r7/base
archive/.../visualizar.py: 1  [119]
crai/crai/agent/workflow.py: 1  [184]
crai/crai/churn_voluntary/voluntary_agent.py: 2  [118, 118]
crai/crai/dunning/dunning_engine.py: 1  [109]
crai/crai/dunning/pix_automatico_retry.py: 1  [291]
crai/crai/ml/anomaly_detector.py: 2  [145, 145]
crai/test_pipeline.py: 15  [47,48,55,56,89,89,103,105,107,144,144,148,159,166,171]
TOTAL 23
```

Mesmos arquivos, mesmas contagens, e as 15 de `test_pipeline.py` linha por
linha idênticas. A afirmação do README — *"as 23 são pré-existentes […] a
contribuição líquida destes sprints é zero"* — é **verdadeira**. O ponteiro
`workflow.py:249` também está certo (era 243 e foi corrigido).

E a raiz é mesmo a raiz:

```
RAIZ = D:\PTI
.git existe = True
TOTAL_DECLARADO = 23
medido = 23
```

A adição de `.claude` a `IGNORADOS` não esconde nada: não há `.py` sob
`D:/PTI/.claude` (só `settings.json` e `settings.local.json`), e a contagem é
23 com ou sem o filtro.

## 6. A afirmação corrigida em `main_agent.py` é verdadeira

O comentário do `MemorySaver` agora diz: *"o limite é garantido DENTRO de um
processo, pela trava; ENTRE processos não há nada"*. Medi as duas metades. A
primeira: seções 1 e 3 acima. A segunda é estrutural — `MemorySaver` é RAM por
processo e `asyncio.Lock` é por event loop; a `P1-15` do README declara isso
explicitamente (*"Dois processos continuam sem exclusão mútua entre si. […] Quem
subir um segundo worker precisa das duas — checkpointer transacional **e** trava
distribuída —, não de uma"*). A declaração está correta e é a mais precisa das
sete rodadas.

## 7. Suíte inteira verde, sem regressão

```
cd /d/PTI/crai && PYTHONIOENCODING=utf-8 python -m pytest tests -q
408 passed, 3 warnings in 26.05s
```

## 8. A blindagem não ficou apertada demais

Dos 43 payloads do fuzz, os legítimos continuam passando: evento de Pix bem
formado (200), Segment bem formado (200), `/simulate/churn-risk` padrão (200),
`/simulate/pix-falhado` padrão (200), Stripe com `customer` inesperado (200,
segue a rota de registro), autorização de Pix sem valor (200 — o desvio antes da
checagem de valor continua correto). Nenhum falso positivo. A única recusa nova
sobre tráfego plausível é a do R7-5.

---

# O que eu NÃO consegui verificar

1. **Multiprocesso de verdade.** Todas as medições de concorrência rodam num
   processo e num event loop, com `httpx.ASGITransport`. Não subi dois workers
   de uvicorn contra a mesma base. A afirmação "entre processos não há exclusão
   mútua" eu aceito por leitura de código (`MemorySaver` em RAM, `asyncio.Lock`
   por loop), não por medição — mas é uma afirmação que **admite** a falha, não
   que a nega, então o risco de estar errada é do lado seguro.

2. **Concorrência real com paralelismo de SO.** `asyncio.gather` num loop dá
   intercalação cooperativa, que é o que reproduz o defeito da r6 e é o que a
   trava fecha. Não testei threads nem `ProcessPoolExecutor` chamando a API.

3. **"Cinco dos sete tracebacks distintos morriam dentro de `crai/api/app.py`"**
   (docstring de `TestA1R6BordaAssinadaNaoDerrubaAApi`). Contei os **status**
   (17 payloads → 500) mas não agrupei os tracebacks por local de morte, então
   não confirmo nem desminto o "cinco de sete".

4. **As 396 requisições da `N-15`.** Não reproduzi o fuzz de 18 valores por
   campo que o README cita; rodei o meu, de 43 casos com valores diferentes.
   O `{200: 174, 400: 64, 422: 158}` fecha em 396 e é internamente consistente;
   o que eu contesto é a conclusão "zero 5xx" como descrição do estado do
   sistema, não a aritmética daquela linha.

5. **`python test_pipeline.py` de ponta a ponta.** Não rodei a demo completa
   (precisa de `ANTHROPIC_API_KEY` para as mensagens e o N-12 mata o processo na
   linha 103 num console cp1252 — dívida declarada). Contei os cenários
   estaticamente no fonte (3 + 2 + 4 = 9), o que basta para o R7-4(a).

6. **`DATA_CARD.md`.** Li, mas o diff `8786d82..c7c6870` não o toca e as rodadas
   3 a 5 já o auditaram. Não refiz a verificação de proveniência.

7. **Deadlock por trava não solta.** Construí artificialmente o caso "trava
   adquirida e nunca solta, referência forte deliberada" e ele trava o processo
   — mas não existe caminho no código que faça isso (`_trava_do_cliente` só é
   usada dentro de `async with`), então não conta como defeito. Anoto só para
   registrar que o cenário foi tentado.

---

# Mínimo para o GA1 virar LIBERADO

1. **R7-1** — chamar `_recusar_inteiro_grande_demais` no caminho do Stripe
   (uma linha, a função já existe e já é usada no Segment), **ou** corrigir a
   `N-15` para dizer a verdade: que `amount_due` está checado só no tipo, que um
   inteiro acima do alcance do `float` produz 500 no `/webhooks/stripe`, e que a
   medição "zero 5xx" vale para os 396 casos daquele fuzz e não para a classe.
   Se corrigir o código, teste de regressão que reprove antes — o payload é
   `{"type":"invoice.payment_failed","data":{"object":{"amount_due": 2**2000}}}`
   com assinatura válida.

2. **R7-2** — limitar a profundidade de `_recusar_inteiro_grande_demais`
   (parâmetro `profundidade` com teto, devolvendo 422 ao estourar), ou corrigir
   a docstring que promete "qualquer profundidade". Como não é regressão, aceito
   também que vire linha de dívida com a consequência escrita (≥ ~950 níveis →
   500).

R7-3, R7-4 e R7-5 são 🟡: não seguram o gate. R7-3 e R7-5 são uma linha cada.

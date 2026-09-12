# AUDITORIA A1-r8 — auditoria adversarial independente

> **Diff sob auditoria:** `c7c6870..deb23be` (correções das rodadas 7 e 8)
> **HEAD auditado:** `deb23be` · branch `sprint/a1-auditoria`
> **Contexto:** `baseline-pre-sprint..deb23be`
> **Data:** 05/09/2026
> **Ambiente:** Windows 10, Python 3.12, `PYTHONIOENCODING=utf-8`, comandos a partir de `D:/PTI/crai`
>
> Nenhuma justificativa do implementador foi consultada. Comentários de código,
> docstrings e mensagens de commit foram tratados como **artefatos sob auditoria**.
> Toda comparação com commits anteriores foi feita em **worktrees descartáveis fora
> de `D:\PTI`**; nenhum `git checkout <ref> -- <caminho>` foi executado na árvore principal.

---

## Veredito

# 🔴 BLOQUEADO (GA1)

**Motivo mínimo do bloqueio:** o diff declara ter fechado a classe
"payload assinado derruba a API com 5xx" — inclusive escrevendo, na linha `N-15`
do README e no `except RecursionError` novo, que a guarda deixou de ser
derrubável. Um fuzz de **442 requisições** sobre as **seis** rotas mostra
**42 respostas 5xx**, todas concentradas nas rotas em que a correção não foi
aplicada: `/webhooks/pix-automatico` (a **única** rota do pipeline ativo) e os
três `/simulate/*`. É o mesmo erro que a A1-r7 já tinha cobrado uma vez
("fechá-la só num dos webhooks era metade da correção"), repetido uma rodada
depois em outro par de rotas.

**Mínimo necessário para liberar:**

1. Fechar `A1r8-01` — `/webhooks/pix-automatico` não pode devolver 5xx.
2. Fechar `A1r8-02` — os `/simulate/*` não podem devolver 5xx (ou a linha `N-15`
   precisa parar de afirmar que as seis rotas foram medidas com zero 5xx).
3. Corrigir `A1r8-03` — a frase do fluxograma do README que descreve um caminho
   de execução que não existe, e que contradiz o próprio README 60 linhas acima.
4. Estender o corpus de `test_nenhum_payload_torto_produz_5xx` a
   `/webhooks/pix-automatico` e aos `/simulate/*` — hoje ele cobre só Stripe e
   Segment, e é por isso que os 42 passaram despercebidos.

---

## Índice dos achados

| ID | Sev | O quê | Onde |
|---|---|---|---|
| `A1r8-01` | 🔴 | `/webhooks/pix-automatico` devolve **HTTP 500** com corpo assinado aninhado (`RecursionError` não capturado) | `crai/api/app.py:290` |
| `A1r8-02` | 🟠 | Os três `/simulate/*` devolvem **HTTP 500** com corpo aninhado — no handler que declara resolver "a classe inteira" | `crai/api/app.py:76-99` |
| `A1r8-03` | 🟠 | README: *"O webhook do Stripe alimenta o mesmo grafo"* — falso, e contradiz o próprio README | `crai/README.md:105-110` |
| `A1r8-04` | 🟠 | README, bloco de estrutura: 1 arquivo que não existe + 5 módulos centrais ausentes | `crai/README.md:60-83` |
| `A1r8-05` | 🟠 | Segment: `anonymousId` sem namespace colide com o `userId` de outro cliente no mesmo `thread_id` | `crai/api/app.py:500-503` |
| `A1r8-06` | 🟠 | `DATA_CARD.md` afirma como existente um gate, um arquivo e dois scripts que não existem; e marca ✅ CALIBRADO um gerador que não foi calibrado | `crai/docs/DATA_CARD.md` §1, §5, §6, §8, §9 |
| `A1r8-07` | 🟡 | `tests/test_metricas_declaradas.py`: 4 dos 5 testes passam nos dois lados sem estarem declarados como catraca | `crai/tests/test_metricas_declaradas.py` |
| `A1r8-08` | 🟡 | README §4.6 cita "meta de 0,78–0,85 fixada na aprovação do Sprint 3" — sem fonte no repositório | `crai/README.md:162` |

---

## 🔴 A1r8-01 — `/webhooks/pix-automatico` devolve 500 com corpo assinado aninhado

### A medição

Fuzz sobre as **seis** rotas: 6 rotas × campos do payload × 20 valores hostis,
com **assinatura HMAC válida** nos três webhooks e `ENV=development` nos três
`/simulate/*`.

```
cd /d/PTI/crai && PYTHONIOENCODING=utf-8 PYTHONPATH=/d/PTI/crai \
  python <scratch>/probes/p11_fuzz.py
```

```
TOTAL: 442
DIST: {200: 108, 400: 102, 422: 190, 500: 42}
5xx/EXC: 42
    ('/webhooks/pix-automatico', 'id_recorrencia', 'nest_3000', 500)
    ('/webhooks/pix-automatico', 'id_recorrencia', 'nest_6000', 500)
    ('/webhooks/pix-automatico', 'id_recorrencia', 'arr_3000',  500)
    ('/webhooks/pix-automatico', 'valor',          'nest_3000', 500)
    ('/webhooks/pix-automatico', 'valor',          'nest_6000', 500)
    ('/webhooks/pix-automatico', 'valor',          'arr_3000',  500)
    ('/webhooks/pix-automatico', 'status',         'nest_3000', 500)
    ('/webhooks/pix-automatico', 'status',         'nest_6000', 500)
    ('/webhooks/pix-automatico', 'status',         'arr_3000',  500)
    ('/webhooks/pix-automatico', 'ispb_pagador',   'nest_3000', 500)
    ('/webhooks/pix-automatico', 'ispb_pagador',   'nest_6000', 500)
    ('/webhooks/pix-automatico', 'ispb_pagador',   'arr_3000',  500)
    ('/webhooks/pix-automatico', 'e2e_id',         'nest_3000', 500)
    ('/webhooks/pix-automatico', 'e2e_id',         'nest_6000', 500)
    ('/webhooks/pix-automatico', 'e2e_id',         'arr_3000',  500)
    ('/webhooks/pix-automatico', 'data',           'nest_3000', 500)
    ('/webhooks/pix-automatico', 'data',           'nest_6000', 500)
    ('/webhooks/pix-automatico', 'data',           'arr_3000',  500)
    ... (as 24 restantes são o A1r8-02)
```

Zero 5xx em `/webhooks/stripe` e `/webhooks/segment` — as duas rotas que o diff
de fato corrigiu.

### O limiar exato

```
rota                       niveis  status
/webhooks/pix-automatico    1300   200
/webhooks/pix-automatico    1500   200
/webhooks/pix-automatico    2000   200
/webhooks/pix-automatico    2500   200
/webhooks/pix-automatico    3000   500
/webhooks/pix-automatico    4000   500
```

### O traceback

```
### 1) /webhooks/pix-automatico, 5000 niveis
  RecursionError: maximum recursion depth exceeded while decoding a JSON object from a unicode string
  ... ultimas 8 linhas do traceback:
      File "...\Lib\json\decoder.py", line 338, in decode
        obj, end = self.raw_decode(s, idx=_w(s, 0).end())
      File "...\Lib\json\decoder.py", line 354, in raw_decode
        obj, end = self.scan_once(s, idx)
    RecursionError: maximum recursion depth exceeded while decoding a JSON object from a unicode string
```

### Por que é defeito

O commit `dd9b16a` adicionou exatamente esta guarda:

```python
    except RecursionError:
        # O próprio `json.loads` estoura a pilha antes de qualquer validação,
        # com aninhamento suficiente. Recusar é a resposta; deixar subir é 500.
        raise HTTPException(status_code=400,
                            detail="Corpo do webhook está aninhado demais")
```

Ela está dentro de `_objeto_json_do_corpo` (`crai/api/app.py:554`), o portão
comum **de Stripe e Segment**. `/webhooks/pix-automatico` **não usa esse
portão**: tem o próprio `json.loads` em `crai/api/app.py:290`, com
`except json.JSONDecodeError` e `except ValueError`. `RecursionError` herda de
`RuntimeError`, não de `ValueError` — passa reto e vira 500.

```
$ grep -n "_objeto_json_do_corpo\|json.loads" crai/crai/api/app.py
290:        corpo = json.loads(raw, parse_constant=_rejeitar_constante_json)   # PIX — sem guarda
372:    event = _objeto_json_do_corpo(payload, "STRIPE")                        # com guarda
487:    payload = _objeto_json_do_corpo(raw, "SEGMENT")                         # com guarda
```

A mesma rota também não recebe `_recusar_inteiro_grande_demais` nem, por
consequência, o teto `PROFUNDIDADE_MAXIMA_DO_PAYLOAD = 100`. Nas outras duas
rotas há duas linhas de defesa (422 por profundidade entre 101 e ~4000; 400 por
`RecursionError` acima disso). Na de Pix não há nenhuma.

### Por que está DENTRO do escopo

- O objetivo textual do Sprint 1, citado na docstring de
  `TestA1R6BordaAssinadaNaoDerrubaAApi`: *"nenhum payload de PSP, por mais torto
  que seja, derruba a API ou entra no pipeline em silêncio."*
- `/webhooks/pix-automatico` é a **única** rota que aciona o pipeline ativo — o
  próprio README, reescrito neste diff, diz *"é este que aciona o agente"* e
  *"O endpoint de cartão continua no ar, mas não roda pipeline"*.
- `sprints.md` §3 (fora de escopo) não menciona nada disso; a única dívida
  declarada na vizinhança, `N-15`, tem fronteira explícita e diferente
  (*"valida os campos que o pipeline consome hoje"*) — profundidade de
  aninhamento não é cobertura de campo.
- Não é regressão deste diff: medido idêntico em `c7c6870` (mesma tabela de
  limiares, 3000 → 500). É uma **correção pela metade** de um defeito que o
  diff afirma ter fechado.

### Por que nenhum teste pegou

`test_nenhum_payload_torto_produz_5xx` monta o corpus só com
`SEGMENT_* + STRIPE_*`:

```python
        casos = [("/webhooks/segment", c, self._h_segment) ...]
        casos += [("/webhooks/stripe", c, self._h_stripe) ...]
```

`/webhooks/pix-automatico` não aparece em nenhuma linha desse teste.

---

## 🟠 A1r8-02 — os três `/simulate/*` devolvem 500 com corpo aninhado

### A medição

Do mesmo fuzz (442 requisições), as 24 restantes:

```
    ('/simulate/pix-falhado',    'id_recorrencia',    'nest_1200', 500)
    ('/simulate/pix-falhado',    'id_recorrencia',    'arr_1200',  500)
    ('/simulate/pix-falhado',    'valor',             'nest_1200', 500)
    ('/simulate/pix-falhado',    'valor',             'arr_1200',  500)
    ('/simulate/pix-falhado',    'ispb_pagador',      'nest_1200', 500)
    ('/simulate/pix-falhado',    'ispb_pagador',      'arr_1200',  500)
    ('/simulate/payment-failed', 'customer_id',       'nest_1200', 500)
    ('/simulate/payment-failed', 'customer_id',       'arr_1200',  500)
    ('/simulate/payment-failed', 'amount',            'nest_1200', 500)
    ('/simulate/payment-failed', 'amount',            'arr_1200',  500)
    ('/simulate/payment-failed', 'failure_code',      'nest_1200', 500)
    ('/simulate/payment-failed', 'failure_code',      'arr_1200',  500)
    ('/simulate/churn-risk',     'user_id',           'nest_1200', 500)
    ('/simulate/churn-risk',     'user_id',           'arr_1200',  500)
    ('/simulate/churn-risk',     'event',             'nest_1200', 500)
    ('/simulate/churn-risk',     'event',             'arr_1200',  500)
    ('/simulate/churn-risk',     'days_since_last',   'nest_1200', 500)
    ('/simulate/churn-risk',     'days_since_last',   'arr_1200',  500)
    ('/simulate/churn-risk',     'features_used_30d', 'nest_1200', 500)
    ('/simulate/churn-risk',     'features_used_30d', 'arr_1200',  500)
    ('/simulate/churn-risk',     'on_site_now',       'nest_1200', 500)
    ('/simulate/churn-risk',     'on_site_now',       'arr_1200',  500)
    ('/simulate/churn-risk',     'billing_profile',   'nest_1200', 500)
    ('/simulate/churn-risk',     'billing_profile',   'arr_1200',  500)
```

Limiar exato:

```
/simulate/pix-falhado        950   422
/simulate/pix-falhado       1000   500
/simulate/pix-falhado       1050   500
/simulate/pix-falhado       1100   500
```

### O traceback

```
### 2) /simulate/pix-falhado, 1200 niveis
  RecursionError: maximum recursion depth exceeded
  ... ultimas 10 linhas do traceback:
        encoded_value = jsonable_encoder(
      [Previous line repeated 969 more times]
      File "...\fastapi\encoders.py", line 281, in jsonable_encoder
        encoded_key = jsonable_encoder(
      File "...\fastapi\encoders.py", line 216, in jsonable_encoder
        if isinstance(obj, BaseModel):
    RecursionError: maximum recursion depth exceeded
```

### Por que é defeito

O Pydantic recusa o campo, monta `exc.errors()` **com o valor ofensor ecoado
dentro**, e o handler `_erro_de_validacao_nunca_vira_500` chama
`jsonable_encoder(exc.errors())` — que é recursivo, sobre o objeto que o
cliente controla. É **exatamente** o mesmo padrão que a A1-r7 cobrou e que este
diff corrigiu em `_recusar_inteiro_grande_demais`, tornando-a iterativa: *"a
guarda não pode ser derrubável pelo que ela guarda"*. Aqui a guarda continua
recursiva, e `_json_representavel` (a função de saneamento escrita para este
handler) também é recursiva.

A docstring do próprio handler afirma o contrário, e é entregável:

```
    Sanear o eco resolve a classe inteira, não só estes três campos: vale para
    qualquer rota, atual ou futura, sem que cada uma precise lembrar do caso.
```

Medido: não resolve a classe inteira. A afirmação é falsa em 24 das 24
combinações testadas com aninhamento ≥ 1000.

### Por que está DENTRO do escopo

- A linha `N-15` do README, reescrita neste diff, declara o corpus medido como
  *"3 webhooks assinados + 3 `/simulate/*`"* e conclui **"zero 5xx"**. A
  declaração é **incompleta**: com o corpus estendido em uma dimensão
  (profundidade), a mesma matriz de rotas dá 42 5xx.
- `/simulate/pix-falhado` é o primeiro `curl` que o README manda a banca rodar.

### Nota de calibragem

Marcado 🟠 e não 🔴 porque as rotas `/simulate/*` são fechadas por
`_require_simulation_env()` (`ENV=production` por default, *fail closed*) e não
recebem tráfego de PSP. Se o leitor aceitar a fronteira que o **próprio README
`N-15` desenhou** (as seis rotas), a severidade é a mesma do `A1r8-01`.

---

## 🟠 A1r8-03 — o fluxograma do README descreve um caminho de execução que não existe

### O que o README diz (adicionado NESTE diff, `crai/README.md:105-110`)

> O webhook do Stripe alimenta o mesmo grafo, mas um evento de cartão nunca
> alcança a política de retentativa: a aresta condicional lê `payment_method` e
> manda direto para a mensagem personalizada.

### A medição

```
$ grep -n "_run_involuntary_pipeline\|crai_agent.ainvoke" crai/crai/api/app.py
10:(_run_involuntary_pipeline), mas cada um marca `payment_method` na entrada do
341:    await _run_involuntary_pipeline(      <- pix_automatico_webhook
467:    await _run_involuntary_pipeline(      <- simulate_pix_falhado
791:async def _run_involuntary_pipeline(
855:        await crai_agent.ainvoke(initial, config)
```

Corpo inteiro de `stripe_webhook` depois da validação (`app.py:399-405`):

```python
    if event.get("type") == "invoice.payment_failed":
        _registrar_cartao_desativado(event)
        return JSONResponse({"status": "ok", "pipeline": False, ...})
    return JSONResponse({"status": "ok", "pipeline": False})
```

Resposta real do endpoint de cartão:

```
2) {"status":"registrado","pipeline":false,"customer_id":"cus_teste",
    "motivo":"recobranca_automatica_de_cartao_fora_do_pipeline_ativo"}
```

### Por que é defeito

Um evento de cartão **não alimenta grafo nenhum**: a rota retorna antes de
`_run_involuntary_pipeline`. A aresta condicional existe no grafo
(`main_agent.py:98`), mas nenhuma rota HTTP viva a alcança com
`payment_method="card"`. A frase descreve como *comportamento em execução* algo
que é código morto.

Pior: **o mesmo README, 60 linhas acima**, diz o oposto e está certo —
*"O endpoint de cartão continua no ar, mas **não roda pipeline**"*. Um documento
que se contradiz sobre o caminho central do sistema é o defeito que a rodada 8
existia para fechar, reintroduzido em outro parágrafo do mesmo arquivo.

A docstring do módulo `crai/api/app.py:9-12` carrega a mesma afirmação falsa
(*"Os dois webhooks de churn involuntário alimentam o MESMO pipeline"*), e não
foi tocada neste diff.

### Por que está DENTRO do escopo

O README é entregável, o parágrafo foi **escrito neste diff**, e a comparação
com o código é uma medição, não opinião. Comparar com a linha `N-13`, que
declara honestamente um ramo inalcançável (`retry_exhausted = True`): aqui a
inalcançabilidade não é declarada — é negada.

---

## 🟠 A1r8-04 — o bloco de estrutura do README não descreve a árvore que existe

### A medição

```
$ for f in <cada arquivo listado no bloco>; do [ -e "$f" ] && echo OK || echo FALTA; done
OK    crai/agent/main_agent.py
OK    crai/agent/workflow.py
...
OK    crai/dunning/dunning_engine.py
FALTA crai/dunning/smart_backoff.py
OK    crai/integrations/hubspot_crm.py
OK    crai/api/app.py
```

```
$ find crai -name "*.py" -not -path "*__pycache__*" | sort
crai/dunning/dunning_engine.py
crai/dunning/legacy_card/__init__.py
crai/dunning/legacy_card/card_retry.py
crai/dunning/legacy_card/smart_backoff.py
crai/dunning/pix_automatico_retry.py
crai/integrations/hubspot_crm.py
crai/integrations/payment_gateway.py
crai/scripts/preparar_amostra_real.py
crai/scripts/train_all.py
crai/security/__init__.py
crai/security/tokenization.py
crai/security/webhook_verification.py
```

### Divergências, uma a uma

| O bloco diz | A árvore tem |
|---|---|
| `dunning/smart_backoff.py` | **não existe** — está em `dunning/legacy_card/smart_backoff.py` |
| — | `dunning/pix_automatico_retry.py` **ausente do bloco**: é a `PixAutomaticoRetryPolicy` citada no fluxograma logo abaixo, e o núcleo da política do BACEN |
| — | `dunning/legacy_card/card_retry.py` ausente |
| `integrations/hubspot_crm.py` (só ele) | `integrations/payment_gateway.py` **ausente** — o adapter de Pix, objeto integral do Sprint 1 |
| — | `security/webhook_verification.py` e `security/tokenization.py` **ausentes** — o pacote inteiro, apesar de o fluxograma dizer "Webhook de Pix Automático (**assinado**)" |
| — | `scripts/preparar_amostra_real.py` ausente — é o comando que o `DATA_CARD` manda rodar para regenerar a amostra |

### Por que é defeito

O bloco foi **editado neste diff** (a linha de `api/app.py` foi reescrita para
mencionar a blindagem de borda e a trava por cliente), e o restante continua
descrevendo uma árvore que não é esta. O caso de `smart_backoff.py` é o mais
agudo: o parágrafo imediatamente abaixo do fluxograma explica que
*"`smart_backoff.py` ... é a política de cartão"* — o que é verdade **porque**
ele foi movido para `legacy_card/`, e o bloco de cima nega esse movimento
colocando-o na raiz de `dunning/`.

### Por que está DENTRO do escopo

Entregável, editado neste diff, verificável por `find`. Não é dívida declarada:
nenhuma linha da tabela de dívida menciona o bloco de estrutura.

---

## 🟠 A1r8-05 — dois clientes distintos colidem no mesmo `thread_id` do Segment

### O que o diff fez (`app.py:500-503`)

```python
    user_id = (_campo_com_forma(payload, "userId", (str,), "SEGMENT")
               or _campo_com_forma(payload, "anonymousId", (str,), "SEGMENT"))
```

com o comentário: *"sem ele clientes distintos dividiriam o mesmo estado (é o
mesmo raciocínio do P0-6, no webhook de Pix)"*.

### A medição

```
=== Cliente A: userId='cli_7' ===
  -> 200
  checkpoint thread 'cli_7': {'user_id': 'cli_7', 'profile': 'PJ',
                              'risk_score': 0.9, 'offer_type': 'consulta_cs',
                              'channel': 'popup'}
=== Cliente B (OUTRA pessoa): anonymousId='cli_7', sem userId ===
  -> 200
  checkpoint thread 'cli_7': {'user_id': 'cli_7', 'profile': 'freelancer',
                              'risk_score': 0.023, 'offer_type': None,
                              'channel': None}
  user_id recebidos pelo pipeline: ['cli_7', 'cli_7']
  MESMO thread_id para dois clientes distintos? True
```

O estado do cliente A (risco 0,90, oferta `consulta_cs`, canal `popup`) foi
**sobrescrito** pelo evento anônimo do cliente B, no mesmo checkpoint.

Comportamento adjacente, medido no mesmo probe (tudo correto):

```
=== ambos com userId e anonymousId: qual vence? ===  -> U        (userId, correto)
=== userId vazio + anonymousId ===                   -> 200 A2   (correto)
=== userId nao-str (int) ===                         -> 422 {"motivo":"campo_com_forma_invalida"}
```

### Por que é defeito

`_run_voluntary_pipeline` usa o valor cru como `thread_id`
(`config = {"configurable": {"thread_id": user_id}}`, `app.py:864`). `userId` e
`anonymousId` são **dois espaços de nome distintos** do protocolo do Segment, e
a correção os funde num único keyspace sem prefixo.

O projeto **já sabe** como fazer isso: a função irmã `_thread_id`, para Pix,
prefixa e resume o id anônimo (`"rec_anon_" + sha256(...)[:16]`), e a docstring
dela explica exatamente por quê — inclusive o cuidado de serializar a base como
lista JSON para evitar colisão por separador. Nenhum desses cuidados foi
aplicado no lado do Segment. Uma linha (`"anon_" + valor`) fecharia.

### Por que está DENTRO do escopo

O prompt do gate GA1 em `sprints.md` lista textualmente:
*"o novo `thread_id`: existe payload em que dois clientes distintos colidem?"*.
Existe, e foi introduzido por este diff.

### Nota de calibragem

🟠 e não 🔴: o pipeline voluntário não está sob o limite regulatório do BACEN
(a consequência é estado/oferta/canal errados e um deal no HubSpot no contato
errado, não estouro de tentativas), e na prática o `anonymousId` do SDK do
Segment é um UUID, o que torna a colisão oportunista em vez de sistemática — ao
contrário do P0-6 original, em que **todos** os anônimos caíam num único
checkpoint.

---

## 🟠 A1r8-06 — o `DATA_CARD.md` afirma como existente o que não existe

### A medição

```
$ for f in crai/models/calibracao.json crai/crai/scripts/calibrar.py \
           crai/crai/scripts/gerar_treino.py crai/tests/test_synthetic_fidelity.py \
           data/synthetic/treino_15000.parquet crai/models_demo.tar.gz; do ...
FALTA crai/models/calibracao.json
FALTA crai/crai/scripts/calibrar.py
FALTA crai/crai/scripts/gerar_treino.py
FALTA crai/tests/test_synthetic_fidelity.py
FALTA data/synthetic/treino_15000.parquet
FALTA crai/models_demo.tar.gz

$ grep -rn "NÃO-CALIBRADO\|SYNTH" --include="*.py" crai/
(nada)

$ grep -n "lognormal" crai/crai/ml/synthetic_data.py
119:        rng.lognormal(mean=5.8, sigma=0.7, size=n_samples),
```

### As afirmações, uma a uma

| Onde | O que afirma | Medido |
|---|---|---|
| §1 | `FEATURES → calibradas em dados REAIS (marginais e sazonalidade medidas)` — e diz explicitamente **"é assim que deve ser apresentada à banca"** | `synthetic_data.py:119` ainda usa `mean=5.8, sigma=0.7`, o palpite. **Falso.** |
| §2.2 | *"Valor → **substitui** a lognormal hardcoded ... por μ = 4,5824 · σ = 0,8883"* | Não substituiu. **Falso** (tempo verbal). |
| §5 | *"Está marcado como `false` em `calibracao.json → calibrado.tenure`"* | `calibracao.json` não existe. **Falso.** |
| §6 [2] | `valor → lognormal por MLE ✅ CALIBRADO` (e mais três ✅) | Nenhum foi aplicado ao gerador. **Falso.** |
| §8 | *"O gate anti-cópia (`tests/test_synthetic_fidelity.py`) **exige** zero linha sintética idêntica a uma real"* | O arquivo não existe. Não há gate. **Falso.** |
| §9 | `calibracao.json` \| ✅ exceção no .gitignore \| `python -m crai.scripts.calibrar` | A exceção no `.gitignore` **é verdadeira** (linha 16). O arquivo e o script **não existem**. |
| §9 | `treino_15000.parquet` \| `python -m crai.scripts.gerar_treino` | O script não existe. |
| §9 (rodapé) | *"sem ele no clone limpo a demo cairia em `[SYNTH] MODO NÃO-CALIBRADO`"* | Essa string não existe em nenhum `.py`. |

### Por que é defeito, e por que ainda assim não é o bloqueio

Todos esses artefatos são **entregas do Sprint 3** (`sprints.md`, "SPRINT 3",
tarefas 1–3 e a tabela `test_synthetic_fidelity.py`), e o Sprint 3 tem como
pré-requisito bloqueante justamente `GA1 LIBERADO` — ou seja, ainda não rodou.
O problema não é falta de funcionalidade; é **tempo verbal**: um documento
escrito no Sprint 0 descreve o estado final do Sprint 3 como se fosse o estado
atual, com ✅ e presente do indicativo.

Isso é grave porque §1 é literalmente a frase que o documento manda o aluno
dizer à banca, e ela é falsa hoje. E porque contradiz o README §4.6 **reescrito
neste mesmo diff**, que diz certo: *"o gerador segue não-calibrado, então o
0,7029 é do gerador ANTIGO"*.

Não é o bloqueio do GA1 porque a checklist do gate GA1 é sobre o relatório de
auditoria e os testes de regressão, e o `DATA_CARD` é entrega do G0, já
aprovado. **Recomendação:** corrigir antes do Sprint 3, não depois — o custo é
marcar as linhas como PENDENTE.

---

## 🟡 A1r8-07 — 4 dos 5 testes novos de métricas passam nos dois lados sem estarem declarados como catraca

### A medição (worktree em `dd9b16a`, com `crai/models/` copiado)

```
$ cd <worktree dd9b16a>/crai && pytest tests/test_metricas_declaradas.py -q
E  AssertionError: o README §4.6 não cita a AUC real do modelo em disco (0.7029).
   Números que a linha declara: [0.6797, 0.7, 0.92]. Foi exatamente assim que o
   '0,6797' sobreviveu a sete auditorias — ninguém comparou a frase com o arquivo
E  assert 0.7029 in {0.6797, 0.7, 0.92}
FAILED tests/test_metricas_declaradas.py::...::test_a_auc_declarada_e_a_auc_medida
1 failed, 4 passed
```

Skip correto quando não há modelo (mesma worktree com `models/` removido):

```
SKIPPED [5] tests\test_metricas_declaradas.py:48: ...\models\train_metrics.json
             não existe — nenhum modelo treinado nesta máquina
5 skipped
```

### Por que é defeito (menor)

`test_a_auc_declarada_e_a_auc_medida` é regressão de verdade: reprova por
**asserção** no commit anterior. Os outros quatro passam dos dois lados — são
catracas legítimas sobre o arquivo de treino, mas **não estão declaradas como
tal**, e o próprio projeto adota esse padrão em outros lugares
(`test_payload_bem_formado_continua_passando`: *"Catraca, não regressão — este
caso já passava em `8786d82`"*; `test_evento_sem_identidade_nenhuma_continua_recusado`:
*"não é o mesmo tipo de prova dos testes acima, e vale contar como tal"*).

O caso mais desalinhado é `test_o_schema_do_treino_e_o_atual`, cuja docstring diz:

> O README afirmava que o arquivo em disco era anterior a esse commit.
> **Este teste é o que torna a afirmação verificável em vez de opinável.**

Medido: ele passa em `dd9b16a`, ou seja, **no commit em que a afirmação do README
era falsa**. Ele verifica o arquivo, não a afirmação do README sobre o arquivo —
que é a única coisa que estava errada. A docstring promete mais do que o teste
entrega.

Observação adicional sobre derrubabilidade: `_numeros_do_texto` coleta **todos**
os números em notação brasileira da linha §4.6 e só exige que `0.7029` esteja no
conjunto. A linha atual declara uma dúzia de números; o teste passaria mesmo se
a frase principal voltasse a dizer "AUC 0,6797", desde que "0,7029" aparecesse
em qualquer outro ponto da linha. É acoplamento frouxo, não furo — mas é o tipo
de frouxidão que deixou o "0,6797" sobreviver sete rodadas.

---

## 🟡 A1r8-08 — a "meta de 0,78–0,85" do §4.6 não tem fonte no repositório

### A medição

```
$ grep -n "0,78\|0.78\|0,85\|0.85" /d/PTI/sprints.md
(nenhuma linha)

$ grep -rn "0,78\|0\.78" --include="*.md" .
./crai/docs/AUDITORIA_01_R2.md:606:  ... 0.787 ...    (F2, não AUC)
./crai/README.md:162: ... abaixo da meta de 0,78–0,85 fixada na aprovação do Sprint 3 ...
```

A única faixa fixada em `sprints.md` é `[0.70, 0.92]` (linhas 390, 401, 432, 516).
A "meta de 0,78–0,85" aparece **só** na frase que a invoca.

### Por que é 🟡 e não maior

A frase atribui a meta a *"a aprovação do Sprint 3"*, que pode ter sido uma
decisão humana fora do repositório — e não tenho como verificar aprovações
verbais. Registro como **afirmação não verificável em documento entregável**:
ou existe um registro escrito, e ele deveria ser citado, ou a frase deveria
dizer que a meta veio de fora do plano.

---

## O que eu verifiquei e estava CERTO

Coloco aqui, com a saída, tudo que auditei tentando derrubar e não consegui.

### 1. A §4.6 do README bate com o arquivo em disco — e é reprodutível

Carreguei os binários de `crai/models/` (que o git ignora) e re-scorei o holdout
por conta própria, sem usar os números gravados:

```
load: True
is_fitted: True
n=15000: n_test=3000 AUC=0.7029 recall=0.94 prec=0.5054 cm=[[410, 1241], [81, 1268]]
n=3000:  n_test=600  AUC=0.7104 recall=0.9455 prec=0.5049 cm=[[70, 255], [15, 260]]
gravado: 0.7029
```

Os quatro números que o README declara — AUC 0,7029, recall 0,9400, precisão
0,5054, matriz `[[410,1241],[81,1268]]` — reproduzem **exatamente** com
`n_samples=15000`. O `n=15000` também é consistente
(`test_size=0.2` × 15000 = 3000 = `n_total_test`).

Os demais números da §4.6 conferem contra os arquivos:

```
$ grep roc_auc models/autoencoder_meta.json  -> "roc_auc": 0.995
$ grep roc_auc models/payday_meta.json       -> "roc_auc_ensemble": 0.9792
$ ls -la models/
-rw-r--r--  Aug 26 17:46 autoencoder.pt                 (README: "26/08" ✓)
-rw-r--r--  Aug 26 17:47 payday_lstm.pt                 (README: "26/08" ✓)
-rw-r--r--  Sep  1 06:23 xgb_failure_classifier.joblib  (README: "01/09" ✓)
-rw-r--r--  Sep  1 06:23 train_metrics.json
$ ls data/synthetic/  -> No such file or directory      (README: "não existe" ✓)
```

O schema de `train_metrics.json` tem mesmo `metricas_por_limiar`,
`recall_operacional` e `limiar_classificacao`, e `limiar_classificacao: 0.25`
bate com `LIMIAR_CLASSIFICACAO` do código. A autocrítica da §4.6 ("esta linha
dizia 0,6797 ... era falsa") é verdadeira, e verifiquei o "antes" por worktree.

### 2. Os testes novos de webhook reprovam no commit anterior POR ASSERÇÃO

Worktree em `c7c6870`, com `tests/test_webhook_security.py` do HEAD copiado:

```
E  AssertionError: `amount_due: 2**2000` devolveu 500. ... assert 500 == 422
E  AssertionError: {"status":"ok","pipeline":false,...}  assert 200 == 422
E  AssertionError: 1200 níveis de aninhamento devolveram 500. ... assert 500 < 500
E  AssertionError: 5000 níveis de aninhamento devolveram 500. ... assert 500 < 500
E  AssertionError: evento com `anonymousId` e sem `userId` devolveu 422:
     {"detail":{"motivo":"campo_obrigatorio_ausente","campo":"userId"}}  assert 422 == 200
E  AssertionError: assert 'campo_obrigatorio_ausente' == 'evento_sem_identificacao'
```

Seis reprovações, todas por asserção — nenhuma por `ImportError` ou símbolo
ausente. São regressões de verdade.

### 3. A contagem "11 + 6 = 17" da docstring está exata

Worktree em `8786d82`:

```
E  AssertionError: 17 de 17 payloads ASSINADOS derrubaram a API com 5xx:
   [('/webhooks/segment', b'not json', 500), ... 11 do segment ...
    ('/webhooks/stripe', b'not json', 500), ... 6 do stripe ...]
```

11 no Segment, 6 no Stripe, 17 no total. A correção que a A1-r7 fez nessa
docstring (de "5 e 16" para "6 e 17") está certa.

### 4. A guarda iterativa fechou o que se propôs a fechar, em Stripe e Segment

```
=== SEGMENT webhook, aninhamento em properties ===
  niveis=   50 -> HTTP 200
  niveis=  150 -> HTTP 422  payload_aninhado_demais
  niveis=  500 -> HTTP 422  payload_aninhado_demais
  niveis=  900 -> HTTP 422  payload_aninhado_demais
  niveis= 1200 -> HTTP 422  payload_aninhado_demais
  niveis= 5000 -> HTTP 400  "Corpo do webhook está aninhado demais"
=== STRIPE webhook, aninhamento em data ===
  niveis=   50 -> HTTP 200
  niveis=  150 -> HTTP 422 ... niveis=1200 -> HTTP 422 ... niveis=5000 -> HTTP 400
```

**Não há faixa de escape** entre o teto de 100 e o limite do parser nessas duas
rotas: 101..~4000 dá 422, acima dá 400. Testei também aninhamento fora das
subárvores blindadas (`stripe.lixo`, `segment.context`, `segment.traits`) — 200,
sem 5xx, porque nada as percorre. E `amount_due: 2**2000` agora dá
`422 inteiro_fora_do_alcance` no campo `data.object.amount_due`.

### 5. A guarda NÃO recusa payload legítimo, e o custo de payload LARGO é aceitável

```
=== Custo de payload LARGO (nao fundo) ===
  chaves=   1000 bytes=    10835 -> 200 em 0.01s
  chaves=  50000 bytes=   727835 -> 200 em 0.08s
  chaves= 200000 bytes=  3177835 -> 200 em 0.35s
```

Linear, sem patologia. O teto de 100 níveis é ordens de grandeza acima de
qualquer `properties` do Segment ou `data` do Stripe reais, e
`test_payload_bem_formado_continua_passando` segue verde.

### 6. O limite de 3 tentativas por janela de 7 dias do BACEN resiste

Probe com a política instrumentada (conta **toda** tentativa efetivamente
agendada, não o que o checkpoint grava), rodando o pipeline real num **único
event loop** — que é o cenário do uvicorn:

```
=== A) 5 entregas SEQUENCIAIS, mesmo id_recorrencia ===
  RN_seq -> total agendado = 3 [1, 2, 3]
=== B) 4 entregas CONCORRENTES, mesmo id_recorrencia (mesmo loop) ===
  status: [200, 200, 200, 200] | total agendado = 3 [1, 2, 3]
=== D) webhook + /simulate/pix-falhado, mesmo id_recorrencia ===
  RN_dupla -> total agendado = 3 [1, 2, 3]
=== E) id_recorrencia com espacos / caixa ===
  por thread_id: {'RN_esp': 3, 'RN_ESP': 3}       (" RN_esp " normaliza p/ RN_esp)
```

Tentei estourar por quatro caminhos (sequencial, concorrente, mistura
webhook + `/simulate`, e variação de forma do id) e não consegui. A única forma
que achei de somar 6 numa mesma janela é a **já declarada** na linha `N-14`:

```
=== C) mesmo contrato: 1x com id_recorrencia, 2x so com e2e ===
  por thread_id: {'RN_mix': 3, 'rec_anon_b2dd1d82f5dd3a88': 3} | TOTAL = 6
```

Isso é exatamente o que `N-14` descreve, com a consequência escrita e o motivo
(*"exige conciliação por chave Pix"*). Dívida declarada, não defeito. Registro
uma **imprecisão menor** de `N-14`: ela diz que o `thread_id` anônimo *"muda a
cada evento"* — medido, ele é estável para o mesmo `e2e_id`; o que muda a cada
evento é o `e2e_id`, que é por transação. O efeito descrito continua correto.

### 7. Inteiro gigante nas rotas de Pix não derruba nada

```
=== PIX: inteiro gigante em varios campos (cobranca_falhada) ===
  valor_int_gigante    -> 422 {"motivo":"evento_degradado","degradacoes":["valor_ilegivel"]}
  valor_str_gigante    -> 422 {"motivo":"evento_degradado","degradacoes":["valor_ilegivel"]}
  meta_int_gigante     -> 200 (campo não consumido; não chega ao checkpoint)
  aninhado_150         -> 200
  ok                   -> 200 pipeline:true
```

`_extrair_valor` já barra. A ausência de `_recusar_inteiro_grande_demais` nessa
rota **não** produz 5xx, porque o evento normalizado carrega só cinco escalares
já saneados. (O 5xx do `A1r8-01` é no parser, antes disso.)

### 8. Os "9 cenários", o aviso do cp1252 e os três `curl` do README

```
$ PYTHONIOENCODING=utf-8 python test_pipeline.py
🔷 Churn Involuntário (Pix Automático):
   Com plano de retentativa: 2/3          <- 3 cenários Pix
     RN_maria_001: 3 tentativa(s) ...     (0 usadas)
     RN_joao_002:  1 tentativa(s) ...     (2 usadas)
💳 Cartão (recobrança automática desativada — Fase 3):
   Eventos registrados     : 2/2          <- 2 cenários de cartão
📈 Churn Voluntário:
   Sinais de risco processados : 4        <- 4 cenários voluntários
✅ Pipeline CRAI v2 ... funcionando!
```

3 + 2 + 4 = **9**. Confere, inclusive a decomposição declarada ("janela do BACEN
em 0, 2 e 3 tentativas usadas").

O aviso do cp1252 confere, na linha exata declarada:

```
$ PYTHONIOENCODING=cp1252 python test_pipeline.py
  File "D:\PTI\crai\test_pipeline.py", line 103, in main
UnicodeEncodeError: 'charmap' codec can't encode character '\U0001f680'
```

Os três `curl` do README, rodados com os corpos exatos que ele publica:

```
1) {"status":"pipeline_executado","id_recorrencia":"RN_teste_001"}
2) {"status":"registrado","pipeline":false,"customer_id":"cus_teste",
    "motivo":"recobranca_automatica_de_cartao_fora_do_pipeline_ativo"}
3) {"status":"pipeline_executado","user_id":"usr_teste"}
```

Os três respondem o que o README diz que respondem.

### 9. Os números do `DATA_CARD` sobre a amostra real estão exatos

```
sha256: d800c60f765b245bfff29e5c258eb34c6cb1a2f54c8dd4c3a69b7bb794de2f0f   ✓ (cabeçalho)
linhas totais (com header): 301                                            ✓ (§2.1)
mix: credit_card 222 (74.0%) | boleto 57 (19.0%) | voucher 17 (5.7%) | debit_card 4 (1.3%)  ✓
valor min/p25/med/mean/p75/max: [0.99, 57.03, 100.57, 142.17, 171.81, 1312.67]              ✓
MLE lognormal mu/sigma: 4.5824 0.8883                                      ✓
periodo: 2016-10-10 16:00:30 -> 2018-08-26 07:38:38                        ✓
```

Seis a seis, nenhum arredondado a favor. O contraste com o `A1r8-06` é
justamente esse: o que o `DATA_CARD` **mediu** está certo; o que ele **projeta
como já feito** está errado.

### 10. A suíte inteira no HEAD

```
$ PYTHONIOENCODING=utf-8 python -m pytest tests/ -q
419 passed, 3 warnings in 23.63s
```

Zero skip — os 21 skips do estado inicial descrito em `sprints.md` §0 realmente
sumiram.

---

## O que eu NÃO consegui verificar

Sou explícito sobre os limites desta rodada.

1. **A medição "396 requisições hostis → `{200: 169, 400: 64, 422: 163}`" da
   linha `N-15`.** A aritmética fecha (169+64+163 = 396) e a matriz de rotas
   está descrita, mas o corpus exato ("18 valores por campo", quais campos) não
   está no repositório e não consegui reconstruí-lo para reproduzir a
   distribuição linha a linha. O que fiz foi montar **meu** corpus (442
   requisições, 20 valores por campo, as mesmas 6 rotas) e medir. A conclusão
   "zero 5xx" não se sustenta no meu corpus — ver `A1r8-01` e `A1r8-02`. Não
   afirmo que o corpus deles produzisse 5xx: afirmo que a conclusão que a linha
   tira dele é mais larga do que o corpus permite.

2. **A "meta de 0,78–0,85 fixada na aprovação do Sprint 3"** (`A1r8-08`). Se
   houve uma aprovação humana registrada fora do repositório, não tenho acesso a
   ela. Registrei como não verificável, não como falsa.

3. **A contagem de 23 ocorrências do `N-12`.** Aceitei o número porque existe
   uma asserção executável (`test_a_raiz_e_mesmo_a_raiz_do_repositorio` mais a
   contagem por `tokenize`) e ela está verde entre os 419. Não refiz a varredura
   por conta própria, e não verifiquei a afirmação *"`baseline-pre-sprint` tem
   exatamente as mesmas 23, arquivo por arquivo"* — verifiquei apenas o ponto de
   morte mais consequente (`test_pipeline.py:103`), que confere.

4. **`P1-14` (dois processos / dois `MemorySaver`).** Não subi dois workers de
   uvicorn. Toda a medição de concorrência foi **dentro de um processo e um
   event loop**, que é onde a trava `_trava_do_cliente` vale. A dívida entre
   processos está declarada com a consequência escrita ("6 na mesma janela") e
   não a contesto — mas também não a confirmei experimentalmente.

5. **A afirmação de que `crai/models/` foi treinado com o gerador ANTIGO.** O
   que medi é que `generate_dataset(15000)` **hoje** reproduz exatamente as
   métricas gravadas — consistente com o gerador não ter mudado desde o treino,
   mas não prova a data do treino nem qual versão do código gerou os dados.

6. **`autoencoder` (ROC-AUC 0,995) e `payday` (0,9792).** Li os valores dos
   `*_meta.json` e conferi que o README os cita corretamente. **Não** os re-medi,
   e não avaliei se há vazamento de rótulo nesses dois módulos — que é o alerta
   que a própria §4.6 levanta e joga para o Sprint 4. Concordo com o alerta; ele
   continua aberto.

7. **Comportamento sob uvicorn real.** Todas as medições de HTTP usaram
   `TestClient` (Starlette) ou `httpx.ASGITransport` sobre o mesmo app ASGI.
   Limites que só existem no servidor (tamanho máximo de corpo, timeouts do
   worker) não foram exercitados. Se o uvicorn recusasse corpos grandes antes da
   rota, o `A1r8-01` mudaria de forma — mas não de existência, já que o corpo de
   3000 níveis tem ~24 KB, muito abaixo de qualquer limite default.

8. **Ordem de chegada patológica no `weakref.WeakValueDictionary` das travas.**
   Li o raciocínio ("sem `await` entre a consulta e a escrita") e o achei
   correto, mas não construí um cenário de coleta de lixo adversarial entre a
   liberação de uma trava e a criação da próxima.

---

## Higiene desta auditoria

- Worktrees usadas e removidas ao final: `wt-c7c6870`, `wt-dd9b16a`,
  `wt-8786d82`, todas em
  `C:/Users/peide/AppData/Local/Temp/claude/D--PTI/auditoria-r8/`.
- Nenhum `git checkout <ref> -- <caminho>` nem `git restore --source` foi
  executado em `D:\PTI`.
- Nenhum arquivo foi escrito dentro de `D:\PTI` além deste relatório.

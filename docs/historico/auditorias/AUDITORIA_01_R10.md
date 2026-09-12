# AUDITORIA A1 — RODADA 10 (adversarial, independente)

**Diff sob auditoria:** `94cd8f6..2634272` ("corrige os 5 achados da re-auditoria A1-r9")
**Contexto do sprint:** `baseline-pre-sprint..2634272`
**Branch:** `sprint/a1-auditoria` · **Data:** 05/09/2026
**Ambiente:** Windows 10, Python 3.12, `PYTHONIOENCODING=utf-8`, suíte rodada de `D:\PTI\crai`

---

## VEREDITO: 🔴 **BLOQUEADO** (GA1)

Um achado 🔴, um 🟠 e dois 🟡.

O bloqueio não é sobre a chave `json.dumps([campo, valor])` — **ela é injetiva, testei e
resiste**. O bloqueio é sobre o que a mesma commit fez do outro lado da correção: para tirar o
prefixo `anon:` da identidade de negócio, ela passou o **id cru** para o pipeline. O id cru é
exatamente o valor que o comentário três linhas acima declara que "**nada impede que
coincidam**". Resultado medido: a colisão saiu do checkpoint do LangGraph e entrou em
`_channel_history` e na identidade do HubSpot — dois lugares onde, em `94cd8f6`, ela **não
existia**. É regressão líquida, não dívida herdada.

| ID | Severidade | O quê |
|---|---|---|
| **R10-1** | 🔴 | `userId` × `anonymousId` foram separados no checkpoint e **unidos** em `_channel_history` e no contato/deal do HubSpot. Regressão medida contra `94cd8f6` |
| **R10-2** | 🟠 | `/simulate/churn-risk` não passa por `_thread_id_voluntario` — dois espaços de chave para o mesmo pipeline. É o **N-7** (já cobrado e fechado no lado involuntário) reaberto no lado voluntário |
| **R10-3** | 🟡 | DATA_CARD §6: o bloco de código ainda aponta `(seção 4)` para números que estão na §2.1/§2.2 — a **mesma classe de ponteiro errado** que o banner da r9 diz ter corrigido. E o banner afirma "Cada seção repete este aviso no ponto exato", o que é falso para §3 e §4 |
| **R10-4** | 🟡 | O comentário de `COLISOES` chama os três pares de quebradores do "esquema de prefixo simples"; só um dos três reprova em `94cd8f6`. Os outros dois são catracas não declaradas |

---

## 🔴 R10-1 — A colisão de identidade foi movida, não fechada

### Alegação sob auditoria

`crai/api/app.py:527-531` (texto da própria commit):

> `userId` e `anonymousId` são atribuídos por sistemas diferentes — o seu backend e o SDK do
> navegador — e **nada impede que coincidam**.

E `crai/api/app.py:920-927`:

> `user_id` é a identidade do NEGÓCIO — vai para o state e para o HubSpot. `thread_id` é a chave
> do CHECKPOINT [...] Separá-las evita que o prefixo de desambiguação vaze para o nome do contato
> no CRM.

A commit fez `user_id = identificado or anonimo` (antes: `identificado or f"anon:{anonimo}"`),
e o teste novo `test_a_desambiguacao_nao_vaza_para_o_negocio` **trava** esse comportamento:
`assert vistos == ["visitante_r9"]`.

### O defeito

`state["user_id"]` não é só um rótulo. Dois consumidores o usam como **chave de estado**, e
nenhum dos dois recebeu a separação:

- `crai/churn_voluntary/voluntary_agent.py:62-65` — `prior = _channel_history.get(user_id)`
- `crai/churn_voluntary/voluntary_agent.py:116` — `_channel_history[state["user_id"]] = state["channel"]`
- `crai/integrations/hubspot_crm.py:100` — `upsert_contact(state["user_id"])`

`_channel_history` **decide o canal de saída**: se o id já converteu antes, o canal anterior
vence `on_site_now` e vence o score de risco (`choose_channel`, linhas 66-73).

### Comando rodado

Script em `.../auditoria-r10/t_channel.py`. Dois webhooks Segment **assinados**, ids coincidentes
em campos diferentes, `random.random` fixado em `0.0` para forçar o aceite (o `track_outcome` é
estocástico; sem isso o teste é um sorteio, não uma medição):

```
1) anonymousId='vitima', on_site_now=True   -> converte
2) userId='vitima',      on_site_now=False  -> deveria receber EMAIL
```

### Saída literal — HEAD (`2634272`)

```
>>> 1) VISITANTE ANONIMO anonymousId='vitima', ON SITE -> converte por POPUP
[CHURN-VOL] vitima | evento: Cancellation Page Viewed | risco: 0.90 | perfil: PJ
[CHURN-VOL] Enviando via POPUP: Antes de você ir, que tal uma conversa com nosso time...
[CHURN-VOL] Resultado: ACEITOU
[HUBSPOT-SIM] Contact upsert: vitima
[HUBSPOT-SIM] Deal criado: Retenção vitima — risco 90% | pipeline=crai_retention | stage=retained
[CHURN-VOL] HubSpot: Contact sim_contact_vitima | Deal sim_deal_97165 | Stage: retained
    _channel_history = {'vitima': 'popup'}
>>> 2) CLIENTE IDENTIFICADO userId='vitima', NAO esta no site
[CHURN-VOL] vitima | evento: Cancellation Page Viewed | risco: 0.90 | perfil: CLT
[CHURN-VOL] Canal por histórico: popup (converteu antes)
[CHURN-VOL] Enviando via POPUP: Antes de você ir, que tal uma conversa com nosso time...
[HUBSPOT-SIM] Contact upsert: vitima
[HUBSPOT-SIM] Deal criado: Retenção vitima — risco 90% | pipeline=crai_retention | stage=retained
[CHURN-VOL] HubSpot: Contact sim_contact_vitima | Deal sim_deal_97165 | Stage: retained
    _channel_history = {'vitima': 'popup'}
```

### Saída literal — mesmo script em `94cd8f6` (worktree descartável)

```
>>> 1) VISITANTE ANONIMO anonymousId='vitima', ON SITE -> converte por POPUP
[CHURN-VOL] Enviando via POPUP: Antes de você ir, que tal uma conversa com nosso time...
[HUBSPOT-SIM] Deal criado: Retenção anon:vitima — risco 90% | pipeline=crai_retention | stage=retained
    _channel_history = {'anon:vitima': 'popup'}
>>> 2) CLIENTE IDENTIFICADO userId='vitima', NAO esta no site
[CHURN-VOL] Enviando via EMAIL: Antes de você ir, que tal uma conversa com nosso time...
[HUBSPOT-SIM] Deal criado: Retenção vitima — risco 90% | pipeline=crai_retention | stage=retained
    _channel_history = {'anon:vitima': 'popup', 'vitima': 'email'}
```

### Por que é defeito

Lado a lado, o **mesmo** cenário:

| | `94cd8f6` | `2634272` (HEAD) |
|---|---|---|
| canal do cliente identificado, **fora do site** | **EMAIL** (correto) | **POPUP** (não alcança ninguém) |
| `_channel_history` | duas chaves, dois clientes | **uma chave, dois clientes** |
| deal do HubSpot | ids distintos | **`sim_deal_97165` idêntico** |
| contato do HubSpot | `anon:vitima` / `vitima` | **`vitima` / `vitima`** |

Três consequências, todas medidas:

1. **A oferta de retenção vai por um canal que não entrega.** O cliente identificado não está no
   site; o popup não chega a ele. A CRAI registra `retained` sobre uma mensagem que ninguém viu.
2. **O histórico de conversão de um estranho decide a política de canal de outro.** É estado por
   cliente contaminado — literalmente o P0-6, com outro dicionário no lugar do `MemorySaver`.
3. **O CRM funde as duas pessoas num contato só**, e os dois ciclos de retenção viram o **mesmo
   deal** (`sim_deal_97165` nas duas linhas), porque o digest do deal é derivado do `user_id`.

O ponto que decide a severidade: **em `94cd8f6` esses três estavam separados.** O prefixo `anon:`
era feio no nome do contato, e era o que segurava a separação nos três. A commit removeu o
prefixo, colocou a separação no `thread_id` — e o `thread_id` não chega a nenhum dos três. A
superfície de colisão ficou **mais larga**, não mais estreita: em `94cd8f6` era preciso um
`userId` que literalmente começasse com `anon:`; em `2634272` basta um `anonymousId` igual a
qualquer `userId`, que é a coincidência banal que o próprio comentário do código diz esperar.

O comentário `# A identidade que o negócio vê é o id cru; a chave do checkpoint é a dupla
(campo, valor)` descreve um sistema em que o id de negócio é só apresentação. Medido, ele é chave
de dois stores. A afirmação "As duas identidades vivem em ESPAÇOS DE NOME SEPARADOS" é falsa para
`_channel_history` e para o HubSpot.

### Por que está DENTRO do escopo

- É **regressão introduzida pelo diff sob auditoria**, não dívida herdada. Não está no README, não
  está em nenhum `AUDITORIA_01*`, não está declarada em lugar nenhum.
- `sprints.md` §3 exclui *"Refatorar `churn_voluntary/` — está funcionando, não se mexe"*. Não
  invoco refactor: a premissa "está funcionando" deixou de valer nesta commit, e o conserto é uma
  chave de dicionário, não uma reestruturação. Também há caminho que não toca o pacote (manter a
  identidade desambiguada no `user_id` e mandar o id cru só para o CRM).
- `sprints.md` §3 exclui *"HubSpot real"*; o item 3 acima vale mesmo em modo simulação, porque o
  id do deal é derivado do `user_id` e já colide na saída da demo.

### Mínimo para liberar

Fazer `_channel_history` (e o que mais leia `state["user_id"]` como chave) enxergar a mesma
identidade desambiguada que o checkpoint enxerga — ou reverter `user_id` para a forma
desambiguada e passar o id cru apenas para o CRM. Com **teste de regressão que reprove em
`2634272`**: o par (`anonymousId='v'` convertendo por popup, `userId='v'` fora do site) precisa
terminar em `EMAIL`.

---

## 🟠 R10-2 — `/simulate/churn-risk` fora de `_thread_id_voluntario` (N-7 reaberto no lado voluntário)

### O defeito

`crai/api/app.py:583` — `await _run_voluntary_pipeline(payload.user_id, payload.event, props)`.
Sem `thread_id`, e `_run_voluntary_pipeline` cai em `thread_id or user_id`. A rota do Segment
grava em `["userId", "x"]`; a rota de simulação grava em `x`. **Dois espaços de chave para o mesmo
grafo**, e um `user_id` forjado na simulação alcança o checkpoint de um cliente real.

### Comando rodado e saída literal

Script `.../auditoria-r10/t_colisao_simulate.py` — webhook Segment assinado com
`userId='cliente_x'`, depois `/simulate/churn-risk` com `user_id='["userId", "cliente_x"]'`:

```
A) cliente real via Segment: userId='cliente_x'
   [CHURN-VOL] Resultado: recusou
   segment userId -> 200
B) /simulate/churn-risk com user_id = '["userId", "cliente_x"]'
   [CHURN-VOL] ["userId", "cliente_x"] | evento: Session Started | risco: 0.26 | perfil: PJ
   simulate -> 200 {"status":"pipeline_executado","user_id":"[\"userId\", \"cliente_x\"]"}

thread_ids usados: ['["userId", "cliente_x"]', '["userId", "cliente_x"]']
COLIDIRAM? True
estado no checkpoint do cliente real: {'user_id': '["userId", "cliente_x"]',
                                       'event': 'Session Started', 'profile': 'PJ'}
```

O checkpoint do cliente real do Segment foi **sobrescrito** — `event` passou de
`Cancellation Page Viewed`/CLT para `Session Started`/PJ.

### Por que é defeito, e por que 🟠 e não 🔴

Este projeto **já classificou exatamente isto como defeito e o corrigiu**, do lado involuntário:

> `docs/AUDITORIA_01.md:469` — **N-7 — `/simulate/pix-falhado` não passa por `_thread_id`** ·
> **MÉDIA**. "O endpoint que a demo usa monta `customer_id` direto do corpo, sem a função nova.
> [...] A correção do P0-6 cobriu um dos dois caminhos que chegam a `_run_involuntary_pipeline`."

A correção do N-7 está viva em `app.py:486` e o comentário ao lado dela diz o motivo com todas as
letras: *"se o simulador usasse o `id_recorrencia` cru [...] o endpoint deixaria de exercitar o
código que a demo mostra"*. É o mesmo argumento, palavra por palavra, contra o que a commit acabou
de fazer no lado voluntário — e desta vez com a agravante de que a divergência **não existia**
antes: em `94cd8f6` as duas portas produziam a mesma chave.

Não é 🔴 porque `/simulate/*` é fechado por `_require_simulation_env()` (403 fora de
`development`/`demo`) e porque `sprints.md` tira a API ao vivo da demo — o alcance real é o
ambiente de desenvolvimento, e a colisão exige um `user_id` forjado.

O comentário `# Sem thread_id (origem /simulate/*), a identidade é a própria chave, como sempre
foi` declara o *comportamento*, mas **não a consequência**, e "como sempre foi" é impreciso: até
`94cd8f6` a rota do Segment também usava a identidade como chave, então as duas coincidiam. Não há
nenhuma linha no README ou em `sprints.md` sobre isto. Declaração incompleta.

---

## 🟡 R10-3 — O banner novo do DATA_CARD erra o mesmo ponteiro que veio corrigir

### Alegação sob auditoria

Banner do topo do `docs/DATA_CARD.md`, escrito nesta commit:

> **Correção da auditoria A1-r9.** A rodada anterior corrigiu esta mesma classe de afirmação — mas
> só na §6, porque a nota de correção apontava para as seções erradas (dizia "seção 5" para o que é
> §2.2, e "seção 4" para o que é §2.1).
>
> Leia todo verbo no presente das seções 2, 3, 4 e 5 como "passa a", não como "é". **Cada seção
> repete este aviso no ponto exato.**

### O defeito, em duas partes

**(a) O ponteiro `(seção 4)` sobreviveu, dentro do mesmo bloco que a commit editou.**
`docs/DATA_CARD.md`, bloco de código da §6, passo `[2] CALIBRAÇÃO`:

```
                  ⬜ = a fonte foi levantada e o número existe (seção 4), mas
                       o gerador ainda NÃO o usa.
```

Os quatro itens marcados ⬜ são valor (lognormal por MLE), hora/dow/dom, mix de meio de pagamento e
taxa-base de falha. Os três primeiros estão na **§2.1/§2.2**; o quarto, na **§3**. Nenhum está na
§4. Medido:

```
$ grep -n "^#\{1,3\} " crai/docs/DATA_CARD.md
 91:### 2.1 A amostra de 300 linhas
114:### 2.2 Papel na CRAI — três marginais medidas, não chutadas
153:## 3. Fonte B — taxa-base de inadimplência (BACEN SGS)
170:## 4. Fonte C — contexto de mercado do Pix (BACEN dados abertos)
```

E a própria §4 diz de si mesma: *"**Papel:** sustentar o argumento de mercado do TCC. **Não entra
no treino.**"* — é a única seção do documento que declaradamente não fornece nenhum parâmetro. A r9
corrigiu duas ocorrências em prosa do "seção 4"/"seção 5" e deixou a terceira, oito linhas abaixo,
no bloco de código.

**(b) "Cada seção repete este aviso no ponto exato" é falso.** A commit inseriu o aviso ⬜ na §2.2,
na §2.3 e na §5. **§3 e §4 não têm nenhum aviso** — e a §3 é justamente onde o presente descreve
algo que o gerador não faz:

> §3 — **Papel:** ancorar `ERROR_CODE_PROBS`. [...] Com 5,81% contra média de 3,92%, o excesso é de
> **+48%**.

```
$ grep -rn "ERROR_CODE_PROBS" crai/crai/ml/synthetic_data.py
56:ERROR_CODE_PROBS = [0.35, 0.20, 0.15, 0.10, 0.12, 0.08]
128:        GATEWAY_ERROR_CODES, size=n_samples, p=ERROR_CODE_PROBS

$ grep -rn "21084|sgs" crai/crai/ml/synthetic_data.py
(vazio — nenhuma referência à série do BACEN no gerador)
```

O vetor é constante escrita à mão; os +48% não estão aplicados a nada. A §6 marca esse item como
`⬜ PLANEJADO` corretamente; a §3 não marca.

### Por que é defeito e está no escopo

Regra do próprio contrato: afirmação falsa em documentação é defeito. As duas afirmações são sobre
o **próprio documento**, foram escritas nesta commit, e são a razão declarada de a commit existir.
🟡 porque não muda comportamento: o banner acerta o essencial (nenhum parâmetro está no gerador) e
a §6 já marca a taxa-base como planejada — o leitor cuidadoso não é enganado sobre o estado da
calibração, só é mandado para a seção errada.

---

## 🟡 R10-4 — Duas das três `COLISOES` são catracas, e o comentário diz o contrário

### Alegação sob auditoria

`crai/tests/test_webhook_security.py`, comentário escrito nesta commit:

```python
    # Os pares abaixo quebram um esquema de prefixo simples. O terceiro é o
    # mais duro: o `userId` é literalmente a codificação que a chave usa para
    # o campo anônimo.
    COLISOES = [
        ("colisao_r8_teste", "colisao_r8_teste"),
        ("anon:vitima", "vitima"),
        ('["anonymousId", "x"]', "x"),
    ]
```

### Comando rodado

Worktree descartável em `94cd8f6`, com o arquivo de teste da HEAD copiado por cima:

```
$ pytest tests/test_webhook_security.py -q -k "A1R8" -v
FAILED ...::test_identidades_distintas_nao_dividem_checkpoint[anon:vitima-vitima]
FAILED ...::test_cada_identidade_guarda_o_proprio_evento[anon:vitima-vitima]
FAILED ...::test_a_chave_do_checkpoint_e_injetiva_em_muitos_pares
FAILED ...::test_a_desambiguacao_nao_vaza_para_o_negocio
4 failed, 15 passed, 60 deselected
```

### Por que é defeito

Dos três pares, só `("anon:vitima", "vitima")` reprova em `94cd8f6`. Os outros dois passam nas duas
pontas — são catracas. E o comentário afirma o oposto de cada um:

- `("colisao_r8_teste", "colisao_r8_teste")` **não** quebra "um esquema de prefixo simples": é
  precisamente o caso que o prefixo `anon:` de `94cd8f6` resolvia. Ele quebra o esquema *sem
  prefixo nenhum* (`deb23be`), que é outro commit.
- `('["anonymousId", "x"]', "x")` — chamado de "o mais duro" — não quebra o prefixo nem nada mais:
  com `anon:`, as chaves são `["anonymousId", "x"]` e `anon:x`, distintas. Ele só teria força
  contra o esquema JSON, e o esquema JSON é injetivo; ele é a catraca mais inerte das três.

Manter os três é legítimo (uma matriz de casos), e a classe declara no docstring que passou a
exercer o invariante. O que falta é o rótulo: `sprints.md` exige regressão que reprove antes, e o
comentário faz dois casos parecerem regressão quando são catraca. 🟡 — o teste que de fato prova a
correção (`[anon:vitima-vitima]` mais `test_a_chave_do_checkpoint_e_injetiva_em_muitos_pares`)
existe e reprova; o defeito é só de rótulo.

Registro que o autor aplicou o critério corretamente no teste vizinho —
`test_a_desambiguacao_nao_vaza_para_o_negocio` diz de si "Escrito como catraca, mas é PROVA DE
REGRESSÃO", e a medição confirma: ele **reprova** em `94cd8f6`. O rótulo certo está a 40 linhas do
errado.

---

## O que eu verifiquei e estava certo

### 1. A chave `json.dumps([campo, valor], ensure_ascii=False, sort_keys=True)` é injetiva

Tentei quebrá-la com 36 valores adversariais em cada campo (72 chaves): valores que já são JSON
(`'["anonymousId", "x"]'`, `'["userId", "x"]'`, `'"x"'`), separadores (`a:b`, `anon:x`), aspas e
contrabarras cruas e escapadas, caracteres de controle (`\x00`, `\n`, `\t`), Unicode composto vs.
decomposto (`ç` vs `c\u0327`, `Å` vs `A\u030a` vs `\u212b`), ligaduras (`ﬁ` vs `fi`), **surrogates
isolados** (`\ud800`, `\udc00`), emoji fora do BMP, e ids de 5000/5001 caracteres.

```
pares testados: 72
colisoes: NENHUMA
chave e reversivel (json.loads(k) == [campo, valor]): True
```

A propriedade que a sustenta: o primeiro elemento da lista é o nome do campo, então `userId` e
`anonymousId` nunca podem produzir o mesmo prefixo; e dentro de um campo o escape de string do JSON
é injetivo. `ensure_ascii=False` não abre buraco (emite o caractere cru, e caracteres crus
distintos continuam distintos); `sort_keys=True` é inerte numa lista. **A alegação central da
commit, isolada, resiste.** O que falha é o alcance dela (R10-1, R10-2).

### 2. A janela do BACEN — não consegui estourar 3 por 7 dias por caminho novo

- `_run_involuntary_pipeline` omite `retry_count` e `pix_janela_ate` quando `retries_done is None`,
  então o webhook não zera o contador do checkpoint (`app.py:889-891`).
- `_janela_vigente` trata contador-sem-prazo como janela aberta — aperta, não afrouxa
  (`workflow.py:65-75`).
- `schedule_retry_pix` reancora em `inicio_da_janela(prazo_vigente)` quando a janela está aberta,
  em vez de empurrar o vencimento (`workflow.py:280-283`).
- `/simulate/pix-falhado` passa por `_thread_id` (`app.py:486`) — o N-7 continua fechado do lado
  involuntário.
- O único caminho que ainda multiplica a janela é o **anônimo com `e2e_id` variável**, e ele está
  declarado no README com a consequência escrita e o número certo: *"Três eventos anônimos do mesmo
  cliente real viram três checkpoints e **9 tentativas** na mesma janela"* (item **N-14**), junto
  com o motivo de não ser resolvível dentro do payload. Declaração completa e verdadeira — não
  cobro.
- `P1-14`/`P1-15` (MemorySaver em RAM, lock por processo) também estão declarados com a
  consequência numérica. Não cobro.

### 3. O contrato da rota Pix — a docstring nova bate com a medição, item por item

Webhook assinado, 16 corpos:

```
nao-JSON                 -> 400  {"detail":"Corpo do webhook não é JSON válido"}
lista []                 -> 400  {"detail":"Corpo do webhook deve ser um objeto JSON, e é list"}
numero 5                 -> 400  {"detail":"... e é int"}
string                   -> 400  {"detail":"... e é str"}
null                     -> 400  {"detail":"... e é NoneType"}
NaN                      -> 400  {"detail":"Corpo do webhook contém Infinity/NaN, ..."}
aninhado 5000            -> 400  {"detail":"Corpo do webhook está aninhado demais"}
aninhado 20000           -> 400  {"detail":"Corpo do webhook está aninhado demais"}
aninhado 100000          -> 400  {"detail":"Corpo do webhook está aninhado demais"}
data lista de texto      -> 422  {"motivo":"payload_nao_e_objeto", ...}
lote (2 eventos em data) -> 422  {"motivo":"lote_nao_suportado", ...}
falhada sem valor        -> 422  {"motivo":"evento_degradado","degradacoes":["valor_ausente"]}
sem id nem e2e           -> 422  {"motivo":"evento_sem_identificacao", ...}
```

Zero 5xx. A mudança de contrato 422→400 para corpo não-objeto está declarada na docstring, com o
motivo, e é a resposta certa. Testei especificamente a afirmação mais fácil de ser falsa — que o
motivo `payload_nao_e_objeto` **continua alcançável** por um corpo que É objeto: alcançável, via
`{"data": ["texto"]}` (`payment_gateway.py:487`). A afirmação é verdadeira.

### 4. Os números do DATA_CARD §2.1 — todos exatos, conferidos contra `data/real/amostra_300.csv`

```
linhas 300
credit_card    222 74.0%   boleto  57 19.0%   voucher  17 5.7%   debit_card  4 1.3%
min 0.99  p25 57.03  med 100.57  media 142.17  p75 171.81  max 1312.67
MLE mu=4.5824 sigma=0.8883
```

Bate com a tabela linha por linha, incluindo o μ/σ por MLE e a faixa `0,99 – 1.312,67` que a §2.3
diz que o `clip` "passará a" usar.

### 5. O banner novo do DATA_CARD — as quatro linhas da tabela são verdadeiras

```
$ grep -n "lognormal|clip|peak_days|_hour_distribution" crai/crai/ml/synthetic_data.py
108:    peak_days = [5, 10, 15, 20, 30]
118:    invoice_amount = np.clip(
119:        rng.lognormal(mean=5.8, sigma=0.7, size=n_samples),
120:        49.90, 9999.90
148:        p=_hour_distribution()
$ ls crai/models/ | grep calibracao   (vazio — o arquivo não existe)
$ ls crai/data/                        (só `real/`; não há `synthetic/`)
```

A mudança de verbo de "é" para "passa a" nas §2.2/§2.3/§5 está correta e no lugar certo. A §5
declara honestamente que a limitação de `tenure` vive naquele parágrafo e em mais nada, já que
`calibracao.json` não existe. O README aponta para "seções 5 e 6" e ambas de fato cobrem `tenure` e
`p = 0.5`.

### 6. A suíte inteira

```
$ PYTHONIOENCODING=utf-8 python -m pytest tests/ -q
438 passed, 3 warnings in 25.69s
```

Zero falha, zero erro de coleta. O `README.md` deixou de afirmar "419 testes" e passou a mandar
rodar `pytest` para a contagem — a afirmação numérica que envelhecia sozinha saiu, o que fecha o
achado da rodada anterior sem criar outro.

### 7. Onde procurei a quinta instância do padrão e **não** achei

- **Outros pontos que decodificam corpo:** `json.loads` no pacote só existe em
  `crai/api/app.py::_objeto_json_do_corpo` (portão comum dos três webhooks) e em
  `crai/scripts/preparar_amostra_real.py` (script offline, entrada é arquivo baixado). Os três
  webhooks passam pelo mesmo portão — conferi por leitura e pela medição do item 3.
- **Outros pontos que constroem identidade:** `_texto_de_identificacao` (`payment_gateway.py:242`)
  recusa dict/lista e registra degradação, então nenhuma estrutura vira `thread_id` por `str()`.
  `_thread_id` (Pix) usa lista JSON mais sha256.
- **Espaço de nomes involuntário × voluntário:** são grafos e checkpointers distintos (`crai_agent`
  vs `voluntary_churn_agent`); um `id_recorrencia` não alcança um `userId`.

O que achei foi a instância *dentro* do lado voluntário (R10-1) e a instância *na outra porta* do
mesmo pipeline (R10-2).

---

## O que eu NÃO consegui verificar

1. **Comportamento com HubSpot real.** `HubSpotCRM` está em modo simulação (sem token/SDK). O item
   3 do R10-1 — a fusão dos dois contatos — foi medido no id simulado (`sim_contact_vitima` para os
   dois) e no digest do deal (`sim_deal_97165` idêntico), não contra a API da HubSpot.
   `sprints.md` tira a integração real do escopo, então não persegui.
2. **A colisão `id_recorrencia` cru × `rec_anon_<sha16>`.** Um `id_recorrencia` literalmente igual a
   `rec_anon_<16 hex>` cairia no checkpoint de um pagador anônimo. Não montei o ataque porque ele
   exige conhecer `e2e_id` mais ISPB mais valor da vítima para calcular o sha, e o efeito é
   **compartilhar** o contador do BACEN (aperta o limite, não afrouxa). Registro como não
   investigado, não como liberado.
3. **`_channel_history` sob concorrência.** É `dict` global sem lock, como o `MemorySaver`. Não medi
   corrida; a trava `_trava_do_cliente` protege só o pipeline involuntário. Fora do meu orçamento
   nesta rodada.
4. **Os números de modelo da §4.6 do README** (AUC 0,7029, autoencoder 0,995, payday 0,9792). Não
   recarreguei os modelos de disco — a rodada r8 registra ter feito exatamente isso e o diff sob
   auditoria não os tocou. Aceito como verificado por terceiro, não por mim.
5. **A profundidade em que `json.loads` passa a estourar.** Medi 2000 (200, aceito e processado),
   5000/20000/100000 (400). Não busquei o limiar exato entre 2000 e 5000; o que importava era que
   nenhum valor produz 5xx, e nenhum produziu.
6. **`sprints.md` além da §3 (escopo) e do gate GA1.** Li o contrato do gate e a lista de exclusões;
   não auditei os gates G2..G4 nem os critérios do Sprint 5.

---

## Higiene

Worktree usada e removida: `.../auditoria-r10/base94` (`94cd8f6 --detach`). Nenhum
`git checkout <ref> -- <caminho>` e nenhum `git restore --source` foi executado em `D:\PTI`. Todos
os scripts de medição ficaram em
`C:/Users/peide/AppData/Local/Temp/claude/D--PTI/auditoria-r10/`.

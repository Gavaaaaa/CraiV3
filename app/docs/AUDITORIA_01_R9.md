# Auditoria adversarial A1 — rodada 9

**Diff sob auditoria:** `deb23be..94cd8f6` ("corrige os 8 achados da re-auditoria A1-r8")
**Contexto do sprint:** `baseline-pre-sprint..94cd8f6`
**Branch:** `sprint/a1-auditoria` · **HEAD:** `94cd8f6ee24ad60f68eb35ff6a25ab7346c26d10`
**Máquina:** Windows 10, Python 3.12, `PYTHONIOENCODING=utf-8`, comandos rodados de `D:\PTI\crai`
**Comparações com o commit anterior:** worktree descartável em
`C:/Users/peide/AppData/Local/Temp/claude/D--PTI/auditoria-r9/base` (`deb23be`, `--detach`),
removida ao fim. Nenhum `git checkout <ref> -- <caminho>` foi executado em `D:\PTI`.

---

# 🔴 BLOQUEADO (GA1)

O diff **fecha de fato** o que alegou fechar no código: o 500 por aninhamento
nas quatro portas de parse está medido e fechado, a colisão
`userId` × `anonymousId` que a r8 mediu está fechada, e a `§4.6` do README
passou a bater com o `train_metrics.json` em disco, número por número.

O que bloqueia é a **quarta instância do mesmo padrão que bloqueou as três
rodadas anteriores** — a classe fechada num lugar e deixada aberta em outro —
agora nas duas dimensões que esta rodada escolheu como escopo:

- **na documentação:** o `DATA_CARD.md` foi reescrito para deixar de afirmar
  que o gerador está calibrado — mas só a `§6`. As seções `2.2`, `2.3` e `5`
  continuam afirmando a calibração como fato consumado, e a `§5` descreve o
  conteúdo de um arquivo que **não existe**, no mesmo documento cuja `§6` diz,
  seis linhas de tabela acima, que ele não existe;
- **no código:** o prefixo `anon:` separa `anonymousId` de `userId` numa
  direção só. Um `userId` que já comece com `anon:` volta a colidir com um
  `anonymousId`, e o comentário que a correção escreveu afirma literalmente
  que os dois vivem em "ESPAÇOS DE NOME SEPARADOS" — a implementação funde os
  dois num só.

| ID | Sev | O quê | Onde |
|---|---|---|---|
| `A1r9-01` | 🟠 | `DATA_CARD` §2.2/§2.3/§5 ainda afirmam a calibração como feita; a §5 descreve o conteúdo de `calibracao.json`, que não existe | `crai/docs/DATA_CARD.md:89,94,109,153,271` |
| `A1r9-02` | 🟠 | Colisão residual de `thread_id`: `userId="anon:X"` e `anonymousId="X"` caem no mesmo checkpoint | `crai/crai/api/app.py:516-518` |
| `A1r9-03` | 🟡 | README diz `tests/ # 419 testes`; medido 432, e o bloco foi anotado como "conferido" nesta mesma correção | `crai/README.md:91` |
| `A1r9-04` | 🟡 | Referências cruzadas falsas na própria nota de correção da r8 ("seção 5", "seção 4") e no cabeçalho da tabela de dívidas ("r2 a r5") | `crai/docs/DATA_CARD.md:167,176`; `crai/README.md:164` |
| `A1r9-05` | 🟡 | Mudança de contrato não declarada na rota Pix: corpo JSON não-objeto passou de `422 payload_nao_e_objeto` para `400`, e a docstring da rota continua prometendo 422 | `crai/crai/api/app.py:287,304` |

**Mínimo para liberar:** `A1r9-01` e `A1r9-02`. Os três 🟡 são baratos e
deveriam ir junto, mas não seguram o gate sozinhos.

---

## 🟠 A1r9-01 — o `DATA_CARD` deixou de mentir na §6 e continua mentindo na §2.2, §2.3 e §5

### O que o documento afirma, hoje, no HEAD

```
$ cd /d/PTI/crai && grep -n "calibrado.tenure\|substitui a lognormal\|passam a ser a faixa\|Substitui\b" docs/DATA_CARD.md
89:1. **Valor** → substitui a lognormal hardcoded de `synthetic_data.py`
94:   `order_purchase_timestamp`, como histograma empírico. Substitui
109:- **Os limites de corte (`clip`) do valor sintético passam a ser a faixa
153:`false` em `calibracao.json → calibrado.tenure`.
```

Texto integral dos trechos:

- **§2.2, item 1** — *"**Valor** → substitui a lognormal hardcoded de
  `synthetic_data.py` (`mean=5.8, sigma=0.7`), que **era** palpite, por
  **μ = 4,5824 · σ = 0,8883**."*
- **§2.2, item 3** — *"**Sazonalidade** → ... como histograma empírico.
  **Substitui** `peak_days = [5,10,15,20,30]` e `_hour_distribution()`, ambos
  inventados."*
- **§2.3** — *"Os limites de corte (`clip`) do valor sintético **passam a ser**
  a faixa observada na âncora (R$ 0,99 – R$ 1.312,67), e não os antigos
  R$ 49,90 – R$ 9.999,90."*
- **§5** — *"`tenure_months` permanece NÃO CALIBRADO ... **Está marcado como
  `false` em `calibracao.json → calibrado.tenure`**."*

### O que existe

```
$ cd /d/PTI/crai && sed -n '104,121p' crai/ml/synthetic_data.py
    tenure_months = np.clip(tenure_all[:n_samples], 0, 72).astype(int)

    # ── Dia do mês da cobrança ───────────────────────────────────────
    # Sazonalidade brasileira: concentração nos dias 5, 10, 15, 30
    peak_days = [5, 10, 15, 20, 30]
    day_of_month = np.zeros(n_samples, dtype=int)
    for i in range(n_samples):
        if rng.random() < 0.6:  # 60% nos dias de pico
            day_of_month[i] = rng.choice(peak_days)
        else:
            day_of_month[i] = rng.integers(1, 29)

    # ── Valor da fatura (LogNormal) ──────────────────────────────────
    # SaaS B2B brasileiro: R$99 a R$5000, concentração em R$200-R$800
    invoice_amount = np.clip(
        rng.lognormal(mean=5.8, sigma=0.7, size=n_samples),
        49.90, 9999.90
    ).round(2)
```

```
$ cd /d/PTI/crai && ls crai/models/calibracao.json
ls: cannot access 'crai/models/calibracao.json': No such file or directory

$ ls crai/models/
autoencoder.pt          feature_names.joblib            payday_prophet_PJ.json
autoencoder_meta.json   label_encoders.joblib           rf_failure_classifier.joblib
autoencoder_scaler.pkl  payday_lstm.pt                  train_metrics.json
bandit_state.json       payday_meta.json                xgb_failure_classifier.joblib
                        payday_prophet_CLT.json
                        payday_prophet_freelancer.json

$ grep -rn "calibracao" crai/crai/ | grep -v __pycache__
(sem saída — nenhum módulo do pacote lê ou escreve o arquivo)
```

`_hour_distribution()` também segue lá, intacto (`crai/ml/synthetic_data.py:148,253`).

### Por que é defeito

Quatro afirmações de estado, no presente ou no pretérito perfeito, sobre um
trabalho que não aconteceu:

1. `mean=5.8, sigma=0.7` **não** foi substituído por `μ = 4,5824 · σ = 0,8883`;
2. `peak_days` e `_hour_distribution()` **não** foram substituídos por
   histograma empírico;
3. o `clip` **não** passou a ser `0,99 – 1.312,67`: continua `49.90, 9999.90`;
4. e a `§5` cita o valor de uma chave (`calibrado.tenure`) dentro de um arquivo
   que não existe — não é ambiguidade de tempo verbal, é a descrição do
   conteúdo de um arquivo ausente. Nenhuma leitura caridosa sobrevive a essa.

O documento **contradiz a si mesmo** dentro de dez linhas. A tabela nova da §6,
introduzida por este mesmo commit, diz:

```
> | gerador calibrado por MLE | `synthetic_data.py` ainda usa `rng.lognormal(mean=5.8, sigma=0.7)` — o palpite que a seção 5 diz ter sido substituído |
> | `crai/models/calibracao.json` | não existe |
```

Ou seja: **quem escreveu a correção sabia que a afirmação continuava viva em
outra seção, apontou para ela (errando o número da seção, ver `A1r9-04`), e
corrigiu apenas a seção em que estava olhando.** É exatamente o padrão que a
r6, a r7 e a r8 cobraram no código, reproduzido na documentação.

### Por que está dentro do escopo

O `sprints.md` põe o `DATA_CARD.md` como entregável do Sprint 0 (gate G0, item
*"docs/DATA_CARD.md existe, com licença de cada fonte e SHA-256 da amostra"*) e
manda documentar a calibração nele no Sprint 3. O critério de julgamento desta
auditoria é explícito: *"dívida declarada no README com a consequência escrita
não é defeito — mas declaração INCOMPLETA ou FALSA é, e é pior que não
declarar"*. A `§5` não é declaração incompleta: é falsa. E o `DATA_CARD` é o
documento que vai para a banca junto com o TCC — o próprio `sprints.md` chama a
proveniência de *"o que separa um TCC sério de um que superajustou a própria
regra e chamou de IA"*.

### O mínimo para fechar

Reescrever §2.2 (itens 1 e 3), o terceiro bullet da §2.3 e a última frase da §5
no mesmo registro da §6 (plano, não estado), e remover da §5 a referência ao
conteúdo de `calibracao.json`. A linha 271 (*"`calibracao.json` é a única
exceção porque **é** um punhado de números medidos"*) merece o mesmo tratamento.

---

## 🟠 A1r9-02 — o prefixo `anon:` separa os espaços de nome numa direção só

### O que a correção escreveu

`crai/crai/api/app.py:507-518`:

```python
    # As duas identidades vivem em ESPAÇOS DE NOME SEPARADOS. `userId` e
    # `anonymousId` são atribuídos por sistemas diferentes — o seu backend e o
    # SDK do navegador — e nada impede que coincidam.
    ...
    identificado = _campo_com_forma(payload, "userId", (str,), "SEGMENT")
    anonimo = _campo_com_forma(payload, "anonymousId", (str,), "SEGMENT")
    user_id = identificado or (f"anon:{anonimo}" if anonimo else None)
```

A função de identidade é `userId ↦ userId` e
`anonymousId ↦ "anon:" + anonymousId`. Ela **não é injetiva**: a imagem do
primeiro espaço não é disjunta da imagem do segundo. Todo `userId` da forma
`anon:<X>` colide com o `anonymousId` `<X>`.

### Medição

Script: `.../auditoria-r9/p1_colisao.py` — dois eventos assinados, um com
`userId`, outro com `anonymousId`, e leitura direta do checkpoint do
`voluntary_churn_agent`.

```
$ cd /d/PTI/crai && PYTHONIOENCODING=utf-8 PYTHONPATH=/d/PTI/crai python .../p1_colisao.py

=== PROBE 1: userId que ja comeca com 'anon:' vs anonymousId ===
  POST userId='anon:vitima' evento='Cancellation Page Viewed' perfil=CLT -> 200
  POST anonymousId='vitima' evento='Session Started' perfil=PJ -> 200
  checkpoint thread_id='anon:vitima' -> event='Session Started' billing_profile=None
  COLIDIU
```

O segundo evento — de **outra identidade, atribuída por outro sistema** —
sobrescreveu o estado do primeiro. É literalmente a saída que a classe de teste
`TestA1R8IdentidadeDoSegmentNaoColide` documenta como o defeito que ela fecha,
com os papéis dos dois campos trocados.

O mesmo vale para o `/simulate/churn-risk`, que passa `payload.user_id` cru
como `thread_id`:

```
=== PROBE 1b: /simulate/churn-risk user_id vs anonymousId ===
  simulate -> 200                      (user_id='anon:sim')
  POST anonymousId='sim' evento='Session Started' perfil=PJ -> 200
  checkpoint 'anon:sim' -> event='Session Started'
```

### Por que é defeito

1. **A afirmação do comentário é falsa.** "As duas identidades vivem em espaços
   de nome separados" descreve a intenção, não o código: o código as funde num
   espaço só, com um mapeamento que colide. Comentário é artefato sob auditoria.
2. **A defesa está incompleta justamente na direção que importa mais.** O
   `anonymousId` é o campo *sob controle do visitante*
   (`ajs_anonymous_id` no `localStorage` do navegador). O `userId` é atribuído
   pelo backend do cliente SaaS. Um SaaS que use `anon:<id>` como convenção
   para contas de convidado — que é exatamente a convenção que este commit
   acabou de introduzir do outro lado — entrega ao visitante a capacidade de
   escolher em qual checkpoint entrar. Antes da correção a colisão era
   acidental nos dois sentidos; depois, ficou acidental num sentido e
   **escolhível** no outro.
3. **Não está declarada.** Não há uma linha na tabela de dívidas do README, nem
   no `DATA_CARD`, nem no `AUDITORIA_01_R8.md`, dizendo que a separação vale só
   para `userId` que não comece com `anon:`.

A correção completa é simétrica e cabe numa linha: prefixar os **dois**
(`f"user:{identificado}"` / `f"anon:{anonimo}"`), ou codificar a identidade
como o próprio `_thread_id` do Pix já faz — `json.dumps([campo, valor])`, cuja
docstring (`app.py:176-181`) explica que o separador cru foi abandonado
justamente porque produzia colisão. A regra existe no arquivo, aplicada à rota
de Pix, e não foi aplicada à rota do Segment. Quarta instância do padrão.

### Por que está dentro do escopo

É o P0-6 (`sprints.md`, tabela dos P0: *"o `thread_id` precisa ser único por
cliente"*), que é item de sprint, não de backlog. O `churn_voluntary/` está
fora de escopo para **refatoração** (`sprints.md` §3: *"Refatorar
`churn_voluntary/` — está funcionando, não se mexe"*), mas a linha em questão
está em `api/app.py`, na borda, que é o objeto do Sprint 1 — e foi escrita
*por este commit*.

---

## 🟡 A1r9-03 — `419 testes` num bloco anotado como "conferido" nesta correção

```
$ cd /d/PTI/crai && PYTHONIOENCODING=utf-8 python -m pytest tests/ -q --collect-only 2>&1 | tail -2
432 tests collected in 7.98s

$ grep -n "testes" README.md
91:├── tests/                           # 419 testes
```

E logo abaixo do bloco, `README.md:96`:

> *"Conferido com `find crai -name "*.py"` na correção da auditoria A1-r8."*

O 419 era correto **no commit pai**:

```
$ cd <worktree deb23be>/crai && PYTHONIOENCODING=utf-8 python -m pytest tests/ -q --collect-only 2>&1 | tail -2
419 tests collected in 8.01s
```

O próprio commit sob auditoria acrescentou 13 testes (4 + 6 + 1 na
`TestA1R8ACorrecaoTemQueAlcancarTodasAsRotas`, 2 na
`TestA1R8IdentidadeDoSegmentNaoColide`) e não atualizou o número — num commit
cujo propósito declarado era corrigir quatro afirmações numéricas falsas em
README/DATA_CARD. Esta é a quinta.

O resto do bloco de árvore **está correto**: conferi `find crai -name "*.py"`
módulo a módulo, e os "seis módulos omitidos" da nota batem exatamente
(`pix_automatico_retry`, `legacy_card/card_retry`, `payment_gateway`,
`preparar_amostra_real`, `tokenization`, `webhook_verification` — com
`legacy_card/smart_backoff` contando como "listado no caminho errado", não como
omitido).

---

## 🟡 A1r9-04 — a nota de correção da r8 aponta para as seções erradas

```
$ cd /d/PTI/crai && grep -n "a seção 5 diz\|seção 4, conferidos" docs/DATA_CARD.md
167:> | gerador calibrado por MLE | ... — o palpite que a seção 5 diz ter sido substituído |
176:> amostra real (seção 4, conferidos), e este documento.

$ grep -n "^## " docs/DATA_CARD.md
36:## 2. Fonte A — âncora primária das 300 linhas reais
121:## 3. Fonte B — taxa-base de inadimplência (BACEN SGS)
138:## 4. Fonte C — contexto de mercado do Pix (BACEN dados abertos)
147:## 5. Fonte D — âncora de tenure
```

- *"o palpite que a **seção 5** diz ter sido substituído"* — a §5 é "Fonte D —
  âncora de tenure". Quem diz que o palpite foi substituído é a **§2.2**
  (linha 89). Um leitor que siga o ponteiro não encontra a afirmação falsa —
  que é precisamente o `A1r9-01`.
- *"os números da amostra real (**seção 4**, conferidos)"* — a §4 é "Fonte C —
  contexto de mercado do Pix", que diz de si mesma *"Não entra no treino"* e não
  tem nenhum número da amostra. Os números da amostra estão na **§2.1**
  (linha 64).

E no README, `README.md:164`:

> *"Itens levantados nas auditorias adversariais A1 e suas re-rodadas (**r2 a
> r5**)..."*

A tabela logo abaixo contém itens explicitamente marcados como correções da
**r6** (`N-12`, `P1-14`), da **r7** (`N-15`) e da **r8** (`§4.6`). O intervalo
declarado está três rodadas atrasado.

Severidade 🟡 e não 🟠 porque nenhuma dessas frases afirma um comportamento
errado do sistema — mas o custo é real: são ponteiros de auditoria, e foi o
ponteiro errado que deixou o `A1r9-01` passar.

---

## 🟡 A1r9-05 — a rota de Pix mudou de contrato e a docstring não acompanhou

Trocar o `json.loads` próprio da rota pelo portão comum `_objeto_json_do_corpo`
fechou o 500 (ver "o que verifiquei e estava certo"), mas trouxe junto o
`if not isinstance(corpo, dict)` do portão comum, que responde **400**. Antes,
o corpo não-objeto chegava ao `parse_pix_event` e voltava **422** com motivo
estruturado.

Mesmo script (`.../auditoria-r9/p2_pix.py`), mesmos payloads assinados, nos dois
commits:

| payload | `deb23be` | `94cd8f6` |
|---|---|---|
| `[]` | `422 {"motivo":"payload_nao_e_objeto","detalhe":"corpo do webhook é list, esperado objeto JSON"}` | `400 "Corpo do webhook deve ser um objeto JSON, e é list"` |
| `[{...um evento...}]` | idem 422 | idem 400 |
| `"texto"` | `422 payload_nao_e_objeto` | `400` |
| `5` | `422 payload_nao_e_objeto` | `400` |
| `null` | `422 payload_nao_e_objeto` | `400` |
| `true` | `422 payload_nao_e_objeto` | `400` |
| `not json` | `400` | `400` (igual) |
| `Infinity` / `NaN` em `valor` | `400` | `400` (igual) |
| objeto válido | `200 pipeline:true` | `200 pipeline:true` (igual) |
| **5000 níveis** | **`500 Internal Server Error`** | **`400 "está aninhado demais"`** ✅ |

A docstring da própria rota, que o commit não tocou (`app.py:285-288`):

```
    Três formas de recusar, todas com log (Sprint 1):
      400  corpo que não é JSON
      422  payload que o adapter não sabe normalizar (lote, não-objeto)
```

"não-objeto" agora é 400, não 422. Não achei teste que dependesse do 422 (a
suíte inteira passa: 432 passed), e `payload_nao_e_objeto` continua alcançável
pelo caminho `data: "texto"` dentro de um objeto — então não virou código morto.
É 🟡: uma mudança de contrato de borda não declarada, com a docstring da rota
descrevendo o comportamento anterior.

---

## O que eu verifiquei e estava certo

### 1. O 500 por aninhamento está fechado nas quatro portas, e a correção alcança a rota do pipeline ativo

`deb23be`, corpo **assinado** de Pix com 5000 níveis → **500**. `94cd8f6` →
**400**. Medido na tabela do `A1r9-05`.

Varredura de 18 payloads hostis em 6 rotas (`.../auditoria-r9/p3_sweep.py`),
incluindo os caminhos que **nenhum teste do commit cobre** — aninhamento e
inteiro gigante *fora* dos campos que a guarda varre:

```
/webhooks/stripe          deep 900 FORA de data        -> 200
/webhooks/stripe          deep 900 em data.object.x    -> 422 payload_aninhado_demais
/webhooks/stripe          bigint FORA de data          -> 200
/webhooks/stripe          amount_due string            -> 422 campo_com_forma_invalida
/webhooks/stripe          data.object lista            -> 422 campo_com_forma_invalida
/webhooks/segment         deep 900 FORA de properties  -> 200
/webhooks/segment         bigint FORA de properties    -> 200
/webhooks/segment         userId + anonymousId vazios  -> 422 evento_sem_identificacao
/webhooks/segment         anonymousId numero           -> 422 campo_com_forma_invalida
/webhooks/pix-automatico  deep 900 em id_recorrencia   -> 200
/webhooks/pix-automatico  valor lista                  -> 422 evento_degradado
/webhooks/pix-automatico  data lista de 2              -> 422 lote_nao_suportado
/simulate/churn-risk      deep 900 em campo extra      -> 200
/simulate/churn-risk      days_since_last bigint       -> 422 valor_nao_utilizavel
/simulate/churn-risk      user_id 200k chars           -> 200
/simulate/pix-falhado     valor bigint                 -> 422
/simulate/payment-failed  amount bigint                -> 422
```

**Zero 5xx.** Os 200 dos payloads "fora do campo guardado" são corretos: o campo
não é lido por ninguém, e o `N-15` do README declara exatamente esse contrato
(*"valida os campos que o pipeline consome hoje, não o grafo inteiro"*).

Inventário de portas de parse conferido:
`grep -rn "json.loads" crai/` → no caminho de webhook, só
`_objeto_json_do_corpo` (`app.py:587`); os três `/simulate/*` passam pelo
Pydantic e caem no handler `_erro_de_validacao_nunca_vira_500`. **Não achei uma
quinta porta.**

### 2. O `except RecursionError` do handler não é derrubável por payload largo, nem custa caro

`.../auditoria-r9/p4_custo.py` — `/simulate/churn-risk`, campo recusado pelo
Pydantic:

```
deep n=  100 req=     662B -> 422 resp=      716B (0.00s) type=int_type               amplif=1.1x
deep n=  400 req=    2462B -> 422 resp=     2516B (0.00s) type=int_type               amplif=1.0x
deep n=  800 req=    4862B -> 422 resp=     4916B (0.00s) type=int_type               amplif=1.0x
deep n=  900 req=    5462B -> 422 resp=     5516B (0.00s) type=int_type               amplif=1.0x
deep n= 1500 req=    9062B -> 422 resp=      124B (0.00s) type=payload_aninhado_demais
largo m=  10000 req=   20061B -> 422 resp=    20115B (0.01s) amplif=1.0x
largo m= 100000 req=  200061B -> 422 resp=   200115B (0.11s) amplif=1.0x
largo m= 500000 req= 1000061B -> 422 resp=  1000115B (0.52s) amplif=1.0x
```

A transição para o ramo `payload_aninhado_demais` acontece entre 900 e 1500
níveis e **encolhe** a resposta (5,5 KB → 124 B). O eco do payload largo é
1,0× — não há amplificação. Nenhum caso passou de 0,52 s. Procurei um payload
que produzisse 5xx por largura, por profundidade abaixo do limiar, ou pela
combinação: não achei.

### 3. Os testes novos reprovam em `deb23be` por asserção, não por símbolo ausente

```
$ cd <worktree deb23be>/crai && pytest tests/test_webhook_security.py -q -k A1R8
8 failed, 5 passed, 60 deselected

E  AssertionError: 5000 níveis num corpo ASSINADO de Pix devolveram 500. ...
E  assert 500 < 500
E   +  where 500 = <Response [500 Internal Server Error]>.status_code

E  AssertionError: o evento anônimo sobrescreveu o checkpoint do cliente identificado:
   'Session Started'. ...
E  assert 'Session Started' == 'Cancellation Page Viewed'
E    - Cancellation Page Viewed
E    + Session Started
```

Falha por asserção de comportamento, não por `ImportError` nem por símbolo
faltando. Os 5 que passam dos dois lados são as parametrizações de 900 e 2000
níveis — a própria docstring da classe declara a tabela medida (500 a partir de
5000 níveis no Pix), então a catraca está declarada.

As catracas de `test_metricas_declaradas.py` também estão declaradas, e a
declaração é verdadeira: `test_a_auc_declarada_e_a_auc_medida` compara a linha
`§4.6` do README com o disco, e em `dd9b16a` o README dizia `0,6797`:

```
$ git show dd9b16a:crai/README.md | grep "§4.6"
| **§4.6** | ... **AUC 0,6797, abaixo do piso [0,70; 0,92]** ... `train_metrics.json`
  com schema anterior ao commit `4109d84`. |
```

### 4. A `§4.6` do README agora bate com o arquivo em disco, número por número

`crai/models/train_metrics.json` (ignorado pelo git, lido do disco):

| README §4.6 afirma | `train_metrics.json` / disco |
|---|---|
| AUC **0,7029** | `"auc": 0.7029` ✅ |
| recall **0,9400** | `"recall_recovered": 0.94` ✅ |
| precisão **0,5054** | `"precision_recovered": 0.5054` ✅ |
| matriz `[[410,1241],[81,1268]]` | `[[410,1241],[81,1268]]` ✅ |
| schema com `metricas_por_limiar` e `recall_operacional` | ambos presentes ✅ |
| limiar em uso 0,25 | `{"limiar": 0.25, ..., "em_uso": true}` e `LIMIAR_CLASSIFICACAO = 0.25` (`failure_classifier.py:108`) ✅ |
| autoencoder ROC-AUC **0,995** | `autoencoder_meta.json: "roc_auc": 0.995` ✅ |
| payday **0,9792** | `payday_meta.json: "roc_auc_ensemble": 0.9792` ✅ |
| Módulo 1 de 01/09; Módulos 2 e 3 de 26/08 | mtimes: `xgb`/`rf`/`train_metrics` **Sep 1**; `autoencoder*`/`payday*` **Aug 26** ✅ |
| `data/synthetic/treino_15000.parquet` não existe | `crai/data/` só contém `real/` ✅ |
| meta 0,78–0,85 com fonte auditável | `docs/APROVACAO_SPRINT3.md`, adicionado neste commit, contém *"Mire o AUC em 0,78–0,85"* ✅ |

### 5. A tabela "o que o texto afirmava × o que existe" da §6 do `DATA_CARD` está correta

Sete linhas, sete verificações:

```
crai/models/calibracao.json                   ausente   ✅ como declarado
data/synthetic/treino_15000.parquet           ausente   ✅
tests/test_synthetic_fidelity.py              ausente   ✅
crai/scripts/gerar_treino.py                  ausente   ✅
crai/scripts/calibrar_parametros.py           ausente   ✅
crai/scripts/preparar_amostra_real.py         EXISTE    ✅ como declarado
grep -n "PARAMS" crai/ml/synthetic_data.py    -> sem saída            ✅ símbolo não existe
grep -n "lognormal" crai/ml/synthetic_data.py -> :119 mean=5.8, sigma=0.7  ✅
```

E os números da amostra real (que a nota manda conferir na seção errada — ver
`A1r9-04` — mas que estão na §2.1) conferem:

```
$ python -c "import hashlib,pathlib; p=pathlib.Path('data/real/amostra_300.csv'); print(hashlib.sha256(p.read_bytes()).hexdigest()); print(sum(1 for _ in p.open(encoding='utf-8')))"
d800c60f765b245bfff29e5c258eb34c6cb1a2f54c8dd4c3a69b7bb794de2f0f
301
```

Idêntico ao SHA-256 declarado na linha 6 do `DATA_CARD`, e 301 linhas =
300 + header, como a §2.1 afirma.

### 6. A janela do BACEN continua fechada por todo caminho que encontrei

`.../auditoria-r9/p5_bacen.py` — 6 cobranças falhadas assinadas do mesmo
`id_recorrencia`, lendo o checkpoint entre uma e outra:

```
evento 1: 200 retry_count=3 exhausted=False agendadas=3 janela_ate=2026-09-12 10:27:29
evento 2: 200 retry_count=3 exhausted=False agendadas=0 janela_ate=2026-09-12 10:27:29
evento 3: 200 retry_count=3 exhausted=False agendadas=0 janela_ate=2026-09-12 10:27:29
evento 4: 200 retry_count=3 exhausted=False agendadas=0 janela_ate=2026-09-12 10:27:29
evento 5: 200 retry_count=3 exhausted=False agendadas=0 janela_ate=2026-09-12 10:27:29
evento 6: 200 retry_count=3 exhausted=False agendadas=0 janela_ate=2026-09-12 10:27:29
```

Três, e só três. O único estouro que consegui produzir é o **já declarado** no
`N-14` — e a declaração é exata, incluindo o número:

```
=== B: 3 eventos SEM id_recorrencia, e2e diferentes ===
  thread rec_anon_20ef273d01b3e592 -> retry_count=3 agendadas=3
  thread rec_anon_f09999c1fbaf5cf4 -> retry_count=3 agendadas=3
  thread rec_anon_59ff9adc76b0032e -> retry_count=3 agendadas=3
  TOTAL agendado na mesma janela para o mesmo pagador: 9
```

O README diz: *"Três eventos anônimos do mesmo cliente real viram três
checkpoints e **9 tentativas** na mesma janela"*, com a consequência e o motivo
de não ser resolvível dentro do payload. Declaração completa e verdadeira —
**não é defeito**, pela regra 3.

### 7. Suíte e demo verdes

```
$ cd /d/PTI/crai && PYTHONIOENCODING=utf-8 python -m pytest tests/ -q
432 passed, 3 warnings in 24.91s

$ PYTHONIOENCODING=utf-8 python test_pipeline.py
🔷 Churn Involuntário (Pix Automático):
   Volume testado          : R$ 1047.90
   Com plano de retentativa: 2/3
   Janela BACEN esgotada   : 0/3
     RN_maria_001: 3 tentativa(s) em 06/09, 07/09, 08/09 (payday_engine)
     RN_joao_002: 1 tentativa(s) em 06/09 (fallback_uniforme)
💳 Cartão: 2/2 eventos registrados · 0 recobranças automáticas
📈 Churn Voluntário: 4 sinais processados
✅ Pipeline CRAI v2 (involuntário + voluntário + HubSpot) funcionando!
```

9 cenários, como o README afirma (3 Pix + 2 cartão + 4 voluntário).
`RN_maria_001` mostra **3 tentativas** — o P1-8 do Sprint 2 segue correto. O
`Janela BACEN esgotada: 0/3` da tela é o `N-13`, declarado.

---

## O que eu NÃO consegui verificar

1. **A reprodução da `§4.6`.** O README afirma *"Números reproduzidos carregando
   o modelo de disco e re-scorando o holdout ... idênticos aos gravados"*.
   Verifiquei que os números **declarados** batem com o `train_metrics.json`,
   não que o modelo re-scorado produza esses números — isso exige regenerar o
   mesmo holdout (`seed 42`, gerador antigo) e não reservei orçamento para
   auditar o pipeline de treino. Se o `train_metrics.json` tiver sido editado à
   mão, meu método não detecta.
2. **O `{200: 169, 400: 64, 422: 163}` do `N-15`.** As 396 requisições hostis
   citadas no README não vêm com o script que as gerou; minha varredura própria
   (18 casos) confirma o "zero 5xx", mas não a distribuição declarada.
3. **A contagem de 23 `UnicodeEncodeError` do `N-12`.** Não reexecutei a
   varredura com `tokenize` a partir de `D:/PTI`. Confirmei só a consequência
   prática: `python test_pipeline.py` precisa de `PYTHONIOENCODING=utf-8`.
4. **Concorrência real (`P1-14` / `P1-15`).** Testei sequencialmente, num único
   event loop e num único processo. Não montei o cenário multi-worker que as
   duas dívidas descrevem; ambas estão declaradas com a consequência escrita.
5. **`AUDITORIA_01_R8.md` (880 linhas).** Li o sumário de achados, o veredito e
   a seção da colisão de identidade. Não conferi uma a uma as saídas de comando
   que ele cola — só as que se cruzam com os meus achados.
6. **`P2-9` do `sprints.md`** (o resumo da demo imprimir *"Clientes retidos 3/4"*
   **e** *"Escalados para CS humano 3/4"* ao mesmo tempo) segue na tela e não
   achei declaração dele na tabela de dívidas — só a metade coberta pelo `N-13`.
   Não classifiquei como achado porque as duas contagens **podem** ser
   coerentes (o `consulta_cs` é simultaneamente retenção e escalonamento, ver
   `README.md:147`), e não medi o suficiente para afirmar que não são.
7. **A explorabilidade real da colisão `anon:`.** Medi que a colisão existe e
   que o `anonymousId` é escolhível pelo visitante. Não medi a convenção de
   `userId` de nenhum cliente real — o risco depende de o SaaS emitir `userId`
   com esse prefixo, o que é plausível mas não demonstrado. É por isso que o
   `A1r9-02` é 🟠 e não 🔴.

---

## Higiene

Worktree criada em
`C:/Users/peide/AppData/Local/Temp/claude/D--PTI/auditoria-r9/base` (`deb23be`,
`--detach`) e removida com `git worktree remove --force`. Nenhum
`git checkout <ref> -- <caminho>` nem `git restore --source` foi executado em
`D:\PTI`. Scripts de prova em
`C:/Users/peide/AppData/Local/Temp/claude/D--PTI/auditoria-r9/`
(`p1_colisao.py`, `p2_pix.py`, `p3_sweep.py`, `p4_custo.py`, `p5_bacen.py`).

```
$ git worktree list
D:/PTI  94cd8f6 [sprint/a1-auditoria]

$ git status --short
?? .claude/
?? crai/docs/AUDITORIA_01_R9.md
```

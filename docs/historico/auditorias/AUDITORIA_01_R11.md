# AUDITORIA A1 — RODADA 11 (verificação dirigida)

**Diff sob verificação:** `2634272..93161c8` ("corrige os 4 achados da re-auditoria A1-r10")
**Contexto:** `baseline-pre-sprint..93161c8`
**Branch:** `sprint/a1-auditoria` · **Data:** 05/09/2026
**Ambiente:** Windows 10, Python 3.12, `PYTHONIOENCODING=utf-8`, suíte rodada de `D:\PTI\crai`

**Mandato desta rodada:** verificação estreita da correção de identidade de cliente
(`93161c8`), mais os quatro invariantes de consequência (regressão, BACEN, 5xx, valor
probatório dos testes novos). Não é auditoria completa. Documentação, rótulos, ponteiros
de seção e redação estão **fora de escopo** por instrução.

---

## VEREDITO: 🟢 **LIBERADO** (GA1)

**Nenhum 🔴.** A correção de `93161c8` fecha o R10-1 nos três consumidores — checkpoint,
`_channel_history` e HubSpot —, e nada que toque dinheiro, estado de cliente ou o limite
regulatório do BACEN regrediu. Dois 🟠 e dois 🟡 vão para a tabela de dívida, sem correção
neste gate.

| ID | Sev | O quê |
|---|---|---|
| **R11-1** | 🟠 | `hubspot_crm.py:109` trunca `user_id[:12]`. O prefixo novo come 5 dos 12 caracteres, e sobram 7 para discriminar: `usr_demo_001` e `usr_demo_002` viram o **mesmo nome de deal** e, em modo simulação, o **mesmo `sim_deal_`**. Regressão medida contra `2634272` — mas só no rótulo/id simulado; contato, canal e checkpoint continuam separados |
| **R11-2** | 🟠 | Um *lone surrogate* (`"\ud800"`) em qualquer campo string derruba as **seis** rotas com HTTP 500 (`UnicodeEncodeError` num `print`). **Pré-existente** — idêntico em `2634272` |
| **R11-3** | 🟡 | `HubSpotCRM.upsert_contact` é upsert só no nome: chama `contacts.basic_api.create(...)` sempre. Em modo real, cada evento cria um contato novo. Pré-existente, fora do diff |
| **R11-4** | 🟡 | Um visitante que converte anônimo e depois faz login vira duas entidades (`anon:x` → `user:y`) e perde o histórico de canal. É o trade-off deliberado do N-16, mas o README não diz que o vínculo anônimo→identificado se perde |

---

## O QUE VERIFIQUEI E ESTAVA CERTO

### 1. O R10-1 está fechado — reproduzido e medido

Cenário do R10-1, com `random.random` fixado em `0.0` (sem isso o aceite é sorteio, não
medição), dois webhooks Segment assinados:

```
python .../auditoria-r11/t_ident.py "D:/PTI/crai"
```

Saída literal em `93161c8`:

```
>>> 1) anonymousId='vitima', NO SITE -> converte por popup
[CHURN-VOL] anon:vitima | evento: Cancellation Page Viewed | risco: 0.90 | perfil: PJ
[CHURN-VOL] Enviando via POPUP: Antes de você ir, que tal uma conversa com nosso time...
[CHURN-VOL] Resultado: ✅ ACEITOU
[HUBSPOT-SIM] Contact upsert: anon:vitima
[HUBSPOT-SIM] Deal criado: Retenção anon:vitima — risco 90% | pipeline=crai_retention | stage=retained
    _channel_history = {'anon:vitima': 'popup'}
>>> 2) userId='vitima', FORA do site -> deveria receber EMAIL
[CHURN-VOL] user:vitima | evento: Cancellation Page Viewed | risco: 0.90 | perfil: CLT
[CHURN-VOL] Enviando via EMAIL: Antes de você ir, que tal uma conversa com nosso time...
[HUBSPOT-SIM] Contact upsert: user:vitima
[HUBSPOT-SIM] Deal criado: Retenção user:vitima — risco 90% | pipeline=crai_retention | stage=retained
    _channel_history = {'anon:vitima': 'popup', 'user:vitima': 'email'}
    contatos HubSpot = ['anon:vitima', 'user:vitima']
    thread_ids       = ['anon:vitima', 'user:vitima']
```

Lado a lado com o que a A1-r10 mediu em `2634272`:

| | `2634272` (defeituoso) | `93161c8` (HEAD) |
|---|---|---|
| canal do cliente identificado, fora do site | **POPUP** (não alcança) | **EMAIL** ✅ |
| `_channel_history` | 1 chave para 2 pessoas | **2 chaves** ✅ |
| contato do HubSpot | `vitima` / `vitima` | **`anon:vitima` / `user:vitima`** ✅ |
| `thread_id` do checkpoint | separado | **separado** ✅ |

Os três consumidores que a A1-r10 nomeou recebem agora a mesma identidade qualificada.

### 2. A identidade é injetiva, e chega a todos os consumidores

`PREFIXO_IDENTIFICADO = "user:"` e `PREFIXO_ANONIMO = "anon:"` têm o **mesmo comprimento**
e diferem no primeiro byte: `"user:" + A == "anon:" + B` é impossível para quaisquer `A`,
`B`. Dentro do mesmo prefixo, igualdade de identidade exige igualdade de `(campo, valor)` —
que é a mesma entidade. O par que quebrava o esquema antigo (`userId="anon:vitima"` ×
`anonymousId="vitima"`) produz `user:anon:vitima` × `anon:vitima`: distintos.

Os **dois** call sites de `_run_voluntary_pipeline` passam por `_identidade_voluntaria`:

```
$ grep -n "_run_voluntary_pipeline\|_identidade_voluntaria" crai/api/app.py
548:    await _run_voluntary_pipeline(
549:        user_id=_identidade_voluntaria(
584:    await _run_voluntary_pipeline(
585:        _identidade_voluntaria("userId", payload.user_id),
```

E `_run_voluntary_pipeline` não tem mais o parâmetro `thread_id`: `config = {"configurable":
{"thread_id": user_id}}`. Não sobra caminho por onde uma identidade crua entre no pipeline.
Os únicos leitores de `state["user_id"]` no pacote são `voluntary_agent.py:43,62,65,116` e
`hubspot_crm.py:100,109` — todos a jusante da qualificação.

### 3. Nenhuma regressão na direção oposta

- **Suíte completa:** `python -m pytest -q` → **`440 passed, 3 warnings in 25.09s`**.
- **Demo ponta a ponta:** `python test_pipeline.py` roda até o resumo:
  `Janela BACEN esgotada: 0/3`, `Clientes retidos: 3/4`,
  `✅ Pipeline CRAI v2 (involuntário + voluntário + HubSpot) funcionando!`
- **N-7 do lado voluntário:** webhook e `/simulate/churn-risk` produzem agora o **mesmo**
  `thread_id` para o mesmo cliente (medido: `['user:mesma_porta_r10', 'user:mesma_porta_r10']`).

### 4. A janela do BACEN continua inviolável

Fuzz de horizonte longo, relógio congelado e avançado deterministicamente (`wf._agora`
substituído), **135 webhooks Pix assinados + 45 chamadas a `/simulate/pix-falhado`** do
**mesmo** `id_recorrencia` ao longo de 45 dias simulados:

```
python .../auditoria-r11/t_bacen3.py
```

```
status: [200] pipeline: ['True'] chamadas: 7
  agora=01/03 09:00 venc=01/03 09:00 prazo=08/03 09:00 usadas=0 -> 02/03 09:00, 05/03 09:00, 08/03 09:00
  agora=08/03 13:00 venc=08/03 13:00 prazo=15/03 13:00 usadas=0 -> 09/03 13:00, 12/03 13:00, 15/03 13:00
  agora=15/03 17:00 venc=15/03 17:00 prazo=22/03 17:00 usadas=0 -> 16/03 17:00, 19/03 17:00, 22/03 17:00
  agora=22/03 21:00 venc=22/03 21:00 prazo=29/03 21:00 usadas=0 -> 23/03 21:00, 26/03 21:00, 29/03 21:00
  agora=30/03 09:00 venc=30/03 09:00 prazo=06/04 09:00 usadas=0 -> 31/03 09:00, 03/04 09:00, 06/04 09:00
  agora=06/04 13:00 venc=06/04 13:00 prazo=13/04 13:00 usadas=0 -> 07/04 13:00, 10/04 13:00, 13/04 13:00
  agora=13/04 17:00 venc=13/04 17:00 prazo=20/04 17:00 usadas=0 -> 14/04 17:00, 17/04 17:00, 20/04 17:00
total agendadas: 21
MAX em janela deslizante de 7 dias: 3 desde 2026-03-02 09:00:00
VEREDITO BACEN: OK (<=3)
```

Três propriedades medidas, não só a contagem: (a) **nenhuma** tentativa cai fora do
`[vencimento, vencimento+7d]` da sua janela; (b) uma janela nova só abre **depois** de a
anterior expirar (a segunda abre 08/03 13:00, contra prazo 08/03 09:00); (c) o máximo em
**qualquer** janela deslizante de 7 dias — inclusive as que cruzam a fronteira entre duas
janelas ancoradas — é 3. Um teste separado misturando a porta de simulação e tentando
injetar `retries_done`/`retry_count`/`attempt_count` pelo corpo em 53 eventos consecutivos
também parou em 3 (`MAX ... : 3 / VEREDITO BACEN: OK (<=3)`).

### 5. Os testes novos reprovam em `2634272` por asserção

Arquivo de testes do HEAD copiado para uma worktree descartável em `2634272`:

```
E  AssertionError: o visitante anônimo e o cliente identificado dividiram a mesma entrada
   de histórico de canal: {'vitima_r10': 'popup'}. ...
E  assert 1 == 2

E  AssertionError: o CRM recebeu ['fusao_r10', 'fusao_r10'] — o visitante anônimo e o
   cliente identificado viraram o mesmo contato
E  assert 1 == 2

E  AssertionError: webhook e simulador gravaram em checkpoints diferentes:
   ['["userId", "mesma_porta_r10"]', 'mesma_porta_r10']. ...

FAILED ...::test_o_historico_de_canal_nao_e_compartilhado
FAILED ...::test_o_crm_nao_funde_o_anonimo_com_o_identificado
FAILED ...::test_o_simulador_usa_a_mesma_identidade_do_webhook
3 failed, 78 deselected
```

Os três reprovam por **asserção sobre o comportamento**, não por `TypeError` de assinatura
nem por símbolo ausente. São prova de regressão legítima.

---

## 🟠 R11-1 — O prefixo come 5 dos 12 caracteres do nome do deal

`crai/integrations/hubspot_crm.py:109`:

```python
deal_name = f"Retenção {state['user_id'][:12]} — risco {state['risk_score']:.0%}"
```

Com a identidade crua, `[:12]` deixava 12 caracteres para discriminar. Com o prefixo, sobram
**7**. Dois clientes identificados distintos, com ids no padrão que o próprio projeto usa
como default (`SimulateChurnRisk.user_id = "usr_demo_001"`):

```
python .../auditoria-r11/t_deal.py "D:/PTI/crai"      # HEAD 93161c8
  name='Retenção user:usr_dem — risco 90%'  id=sim_deal_8519
  name='Retenção user:usr_dem — risco 90%'  id=sim_deal_8519
nomes distintos: 1  ids distintos: 1
```

```
# mesma medição na worktree em 2634272
  name='Retenção usr_demo_001 — risco 90%'  id=sim_deal_40872
  name='Retenção usr_demo_002 — risco 90%'  id=sim_deal_10741
nomes distintos: 2  ids distintos: 2
```

**Por que é 🟠 e não 🔴.** Nenhum estado de cliente é fundido: `upsert_contact` recebe
`user:usr_demo_001` e `user:usr_demo_002` (medido, distintos), `_channel_history` tem duas
chaves, o checkpoint tem dois `thread_id`. Em modo real (`HUBSPOT_TOKEN` definido) os deals
são objetos distintos criados por `deals.basic_api.create(...)` — só o `dealname` colide, e
o deal continua associável pelo contato. O `sim_deal_` idêntico existe apenas no modo
simulação, onde é uma string impressa e devolvida, nunca lida por nenhuma decisão. Não toca
dinheiro nem o BACEN. **Mas é visível na demo**, e a linha N-16 do README declara o custo do
prefixo ("aparece no nome do contato e do deal") sem dizer que ele encurta o discriminante.
Conserto de uma linha: tirar o `[:12]` ou ampliá-lo.

## 🟠 R11-2 — *Lone surrogate* derruba as seis rotas com 500

```
python .../auditoria-r11/t_surrogate.py "D:/PTI/crai"
/simulate/churn-risk         b'{"user_id": "a\\ud800b"}'                          -> 500
/simulate/pix-falhado        b'{"id_recorrencia": "a\\ud800b", "valor": 100.0}'   -> 500
/simulate/payment-failed     b'{"customer_id": "a\\ud800b", "amount": 100.0}'     -> 500
/simulate/churn-risk         b'{"user_id": "ok", "billing_profile": "a\\ud800b"}' -> 500
/simulate/churn-risk         b'{"user_id": "ok"}'                                 -> 200
```

Os três webhooks assinados também: `/webhooks/segment -> 500`,
`/webhooks/pix-automatico -> 500`, `/webhooks/stripe -> 500`. Traceback:

```
File "D:\PTI/crai\crai\churn_voluntary\voluntary_agent.py", line 43, in assess_risk
UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800' in position 18:
surrogates not allowed
```

O `print` do primeiro nó do grafo é quem estoura. **Pré-existente:** o mesmo script na
worktree em `2634272` devolve exatamente os mesmos 500. Não é regressão do diff. Consequência
contida: o crash é no primeiro nó, antes de qualquer escrita no CRM ou no contador do BACEN
— nada é comprometido pela metade. As três rotas `/simulate/*` estão atrás de
`_require_simulation_env`; os três webhooks exigem assinatura válida.

Contexto da medição: fuzz de **3500 requisições** nas seis rotas (350 iterações × 10 formas,
24 valores hostis por campo — inteiros de 400 dígitos, `NaN`/`Infinity`, `2**63`, tipos
trocados, corpos malformados). **Todos** os 71 5xx observados foram este caso; nenhum outro
valor hostil produziu 5xx. `/health -> 200`.

---

## O QUE **NÃO** VERIFIQUEI

- **Auditoria completa do sprint** (`baseline-pre-sprint..93161c8`). O mandato era a
  verificação dirigida de `93161c8`; só o diff `2634272..93161c8` foi lido linha a linha.
- **Documentação, rótulos de teste, ponteiros de seção, contagens e redação** — excluídos por
  instrução. Li o diff de `README.md` e `DATA_CARD.md` só para confirmar que N-16 existe e
  declara o trade-off do prefixo; não conferi números, ponteiros nem completude.
- **Modo real do HubSpot.** Todas as medições de CRM foram em modo simulação
  (`HUBSPOT_TOKEN` ausente). O comportamento de `create`/`upsert` contra a API real é
  inferido do código, não medido.
- **Concorrência entre processos e entre entregas simultâneas** (P1-14 / P1-15). O fuzz do
  BACEN é sequencial, num processo, num event loop. A dívida declarada de multi-worker
  continua declarada e continua não testada aqui.
- **Módulos de ML** (classifier, anomaly, payday, bandit) e o conteúdo do DATA_CARD.
- **O `customer_id[:12]` do lado involuntário** (`hubspot_crm.py:86`). Verifiquei apenas que
  o prefixo do churn voluntário não chega lá; não medi colisão de nome de deal na
  recuperação.
- **`/webhooks/stripe` além do fuzz.** O caminho de cartão está fora do pipeline ativo
  (Fase 3) e não foi exercitado funcionalmente.

---

## Higiene

Worktree descartável criada em
`C:/Users/peide/AppData/Local/Temp/claude/D--PTI/auditoria-r11/prev` (`2634272 --detach`) e
**removida** ao fim. Nenhum `git checkout <ref> -- <caminho>` nem `git restore --source` foi
executado em `D:\PTI`. `git worktree list` mostra só `D:/PTI`; `git status --short` limpo
exceto este relatório e o diretório `.claude/` já presente no início da sessão.

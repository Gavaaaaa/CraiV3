# Treino do churn involuntário com dados reais

Guia da fase de treino: o que o classificador consome, de onde cada dado vem em
produção, onde plugar a fonte real e como usar o log de ciclos como dataset
supervisionado.

Este documento descreve o estado do código **após os 7 sprints de prontidão para
o mercado**. Ele não promete integrações que não existem — o que falta está
marcado como limitação, com o ponto exato onde entra.

---

## 1. O que o classificador consome

`FailureClassifier.predict()` recebe 11 features + o LTV. O LTV **não é feature
de treino**: ele entra só no cálculo do e-Profit (`p_recovery × LTV − custo`),
que é o critério de decisão do agente.

| Feature | Tipo | De onde vem em produção | Hoje |
|---|---|---|---|
| `invoice_amount` | num | evento Pagar.me (`valor` da cobrança) | **real** |
| `attempt_count` | num | evento Pagar.me | fixo em `1` para Pix¹ |
| `gateway_error_code` | cat | evento Pagar.me → `PIX_CODE_MAP` | **real** |
| `card_brand` | cat | não existe em Pix — `"n/a"` | constante |
| `day_of_month` | num | relógio no momento da falha | **real** |
| `hour_of_day` | num | relógio no momento da falha | **real** |
| `day_of_week` | num | relógio no momento da falha | **real** |
| `tenure_months` | num | banco/CRM: meses de assinatura | **sintético** |
| `avg_ticket` | num | faturamento: ticket médio mensal | **sintético** |
| `payment_history_score` | num | faturamento: % de cobranças pagas em dia | **sintético** |
| `failure_count_90d` | num | faturamento: falhas nos últimos 90 dias | **sintético** |
| `ltv_estimated` | num | derivado (ver §4) | **sintético** |

¹ As duas janelas automáticas do dia do vencimento são do PSP do pagador e não
contam como tentativa do recebedor — por isso `attempt_count = 1` na entrada.
As retentativas da CRAI são contadas pelo checkpoint, não por este campo.

**Quatro features são fabricadas.** É a limitação central desta fase, e ela está
declarada aqui e no log (`[PERFIL]`), não escondida. As quatro vêm do negócio —
banco, CRM, faturamento — e nada disso está conectado.

---

## 2. Onde plugar a fonte real

`crai/agent/perfil_provider.py`.

```python
class PerfilProvider(Protocol):
    def get_perfil(self, customer_id: str, invoice_amount: float) -> dict: ...
```

- `SyntheticPerfilProvider` — o default. Fabrica o perfil com `np.random`
  semeado pelo `customer_id` (`seed_por_cliente`, md5 estável). O mesmo cliente
  recebe **sempre** o mesmo perfil, em qualquer máquina — é o que torna a demo
  reprodutível.
- `DBPerfilProvider` — o stub. Sem `CRAI_PERFIL_DB` configurada, delega ao
  sintético e avisa uma vez no log.

**Passo a passo para plugar:**

1. Implementar `DBPerfilProvider._consultar(customer_id, invoice_amount)`,
   devolvendo um dict com as chaves de `CAMPOS_DO_PERFIL`, ou `None` quando não
   houver histórico daquele cliente (assinante novo é caso normal, não erro —
   o provedor cai no sintético para ele e o pipeline segue).
2. Definir `CRAI_PERFIL_DB` no ambiente.
3. Nada mais muda: nem `_features_pix`, nem o nó de diagnóstico, nem o schema do
   dataset. O ponto de troca é `_perfil_provider`, no topo de
   `crai/agent/workflow.py`.

Para testar um provedor alternativo sem env nenhuma, basta trocar o atributo de
módulo — `PerfilProvider` é `Protocol`, então qualquer objeto com o método certo
serve (ver `tests/test_perfil_provider.py`).

---

## 3. Vocabulário de `failure_cause` (Pix)

Desde o Sprint 3 o diagnóstico de Pix tem vocabulário próprio, em
`crai/agent/pix_codes.py`. Antes disso **toda** falha de Pix virava
`insufficient_funds`, o que deixava a feature constante e o dataset sem sinal.

| Código do PSP (exemplos) | ISO 20022 | `failure_cause` | Retentável? |
|---|---|---|---|
| `insufficient_funds`, `saldo_insuficiente` | `AM04` | `insufficient_funds` | sim |
| `limit_exceeded`, `limite_excedido` | `AM02`, `AM18` | `limit_exceeded` | **não** |
| `authorization_revoked`, `mandate_revoked` | `MD01`, `MD07`, `AC06` | `authorization_revoked` | **não** |
| `processing_error`, `psp_error`, `timeout` | `AB03`, `AG03`, `MS03` | `processing_error` | sim |
| qualquer código não reconhecido | — | `processing_error` (default seguro) | sim |

O default **não** é `insufficient_funds` de propósito: afirmar falta de saldo
sobre um pagador de quem não se sabe nada envenenaria o rótulo do dataset.

> ⚠️ **TODO(integração):** a coluna ISO 20022 precisa ser conferida contra a
> documentação da conta Pagar.me antes de `CRAI_PAGARME_LIVE=1`. Código fora do
> mapa cai no default e sai no log com prefixo `[PIX-CODE]` — é assim que se
> descobre o que falta acrescentar.

---

## 4. A fórmula do LTV

Mora em `crai/ml/ltv.py`, e **só** lá:

```
LTV = max(invoice_amount, tenure_months × avg_ticket × fator_retenção ÷ 12)
```

O fator de retenção é parâmetro: o gerador de dataset o modula pelo tenure
(0,80–0,98); o perfil do pipeline usa `RETENCAO_PADRAO = 0.9`. Antes do Sprint 7
a fórmula estava escrita duas vezes, com fatores diferentes — e divergiria em
silêncio, porque o LTV é o multiplicador do e-Profit, não uma feature que o
treino veria.

Não confundir com o **fallback** `invoice_amount × 6` de
`failure_classifier.predict`: aquilo é o que sobra quando o chamador não informa
LTV nenhum, e vale como piso grosseiro.

---

## 5. A FONTE DE DADOS DE TREINO

`crai/dunning/recovery_log.py` → `crai/data/recovery_cycles.db` (SQLite).

Uma linha por ciclo de recuperação, gravada em duas etapas:

| Quando | O quê |
|---|---|
| fim do grafo (`update_roi_dashboard`) | features + diagnóstico + decisão. `recovered = 0` |
| confirmação do PSP (`_fechar_ciclo_recuperado`) | `recovered = 1`, custo realizado, fee efetivo |

**O par `(features, recovered)` desta tabela é exatamente o dataset
supervisionado** que substitui `ml/synthetic_data.py` no retreino.

### Lendo o dataset

```python
import pandas as pd, sqlite3
from crai.dunning.recovery_log import caminho_do_banco, FEATURES_DO_DATASET
from crai.ml.failure_classifier import ALL_FEATURES

with sqlite3.connect(caminho_do_banco()) as conn:
    df = pd.read_sql("SELECT * FROM ciclos_recuperacao", conn)

# Só ciclos com desfecho conhecido: `recovered = 0` numa linha sem
# `desfecho_em` significa "ainda não sabemos", não "não recuperou".
df = df[df["desfecho_em"].notna()]
```

> **A distinção acima é a que mais custa se for ignorada.** Treinar com os
> ciclos ainda abertos como negativos ensina o modelo a prever "não recupera"
> para tudo que é recente — e recentes são a maioria numa base em crescimento.

### Alimentando o `train()`

```python
X = df[list(ALL_FEATURES)]     # as 11; o LTV fica de fora do X
y = df["recovered"]
ltv = df["ltv_estimated"]      # entra só no e-Profit da avaliação
```

`FailureClassifier.train()` hoje gera o dataset sintético internamente. Para
treinar com dados reais, passe este `df` no lugar da chamada a
`generate_dataset()` — as colunas têm os mesmos nomes de propósito, e
`tests/test_recovery_log.py` reprova se o classificador ganhar uma feature que o
log não grava.

### Quantas linhas são suficientes

O dataset sintético usa 5.000 amostras com ~30% de positivos. Com volume real
menor, comece por: (a) verificar se `metricas()["ciclos"]` passa de algumas
centenas com desfecho; (b) conferir o balanço de `recovered` — se a taxa de
recuperação real for muito baixa, `class_weight`/`scale_pos_weight` importam
mais que o volume.

---

## 6. Métricas de negócio

`GET /metrics/recovery?tenant_id=...&desde=...`, ou
`recovery_log.metricas(...)`:

- `taxa_recuperacao` — recuperados ÷ ciclos
- `mrr_recuperado` — soma de `amount` dos recuperados
- `custo_total` e `custo_medio_por_recuperacao` — custo **realizado**
  (tentativas que de fato saíram × custo por tentativa + mensagens enviadas)
- `fee_total` e `margem` — a receita e o que sobra dela

O custo médio é o custo **total** dividido pelas **recuperações**: o numerador
inclui o gasto dos ciclos perdidos, porque aquele dinheiro foi gasto perseguindo
recuperação; o denominador são as recuperações, porque dividir por ciclo
diluiria o custo nos que não geraram fee.

---

## 7. Limitações conhecidas (para a banca)

| # | Limitação | Onde | Sai com |
|---|---|---|---|
| 1 | Perfil **sintético**: tenure, histórico, falhas 90d e LTV são fabricados | `perfil_provider.py` | implementar `DBPerfilProvider._consultar` |
| 2 | `recovered` depende do webhook de confirmação — não é inferido nem sorteado | `app.py::_fechar_ciclo_recuperado` | já resolvido (Sprint 1); exige o webhook do PSP ativo |
| 3 | Contador do BACEN e planos de retentativa em **memória/arquivo** | `main_agent.py` (`MemorySaver`), `retry_state.py` | PostgreSQL — outra frente |
| 4 | **Um processo só.** Dois workers têm memórias separadas e o limite de 3 tentativas vale por processo | idem | idem |
| 5 | Scheduler temporal é **infra externa**; existe a lógica de disparo, não o cron | `retry_scheduler.py` | cron/worker chamando `processar_tentativas_devidas()` |
| 6 | Endpoint do Pagar.me marcado `TODO(integração)` — não inventado | `pagarme_gateway.py` | confirmar contra a conta e definir `CRAI_PAGARME_ENDPOINT` |
| 7 | Códigos ISO 20022 a conferir com o PSP | `pix_codes.py` | idem |
| 8 | `custo por tentativa` default **zero** — o valor real é contratual | `config.py` | definir `CRAI_CUSTO_TENTATIVA_PIX` |
| 9 | `/metrics/recovery` **sem autenticação** | `app.py` | atrás do controle de acesso do dashboard |
| 10 | e-Profit usa custo configurável, não medido no PSP | `config.py` | conciliar com a fatura do PSP |

Nenhuma delas é descoberta tardia: cada uma tem o ponto de troca pronto no
código e um comentário dizendo o que muda quando a dependência chegar.

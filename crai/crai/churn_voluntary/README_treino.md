# Guia de treino — risco do churn voluntário

Este arquivo é o passo a passo da fase de treino. Ele descreve **o formato que
o sistema já grava** (não um formato a construir), as limitações conhecidas do
dado, e como plugar o modelo treinado sem reescrever pipeline nenhum.

Escrito no Sprint 6 (`sprint(cv6)`), com o dataset produzido desde o Sprint 4.

---

## 1. O que o risco consome hoje

`risk_scorer.calculate_risk(event, props)` decide o `risk_score` a partir de
cinco sinais que chegam no evento do Segment:

| Campo | Origem | Tipo | Uso hoje (regras fixas) |
|---|---|---|---|
| `event` | evento do Segment | `str` | `Cancellation Page Viewed` → 0.90; `Downgrade Clicked` → 0.75; `Session Started` → cálculo; outro → 0.0 |
| `days_since_last` | `properties` | número | `(days/30) × 0.7` |
| `features_used_30d` | `properties` | número | `max(0, (5-features)/5) × 0.3` |
| `billing_profile` | `properties` | `str` | não entra no risco; define o **perfil** do bandit (`CLT`/`PJ`/`freelancer`) |
| `mrr` | `properties` | número | não entra no risco; define a **criticidade** (tom) e o LTV do e-Profit |

O vetor que o modelo treinado recebe está declarado em
`risk_scorer.FEATURES_DE_RISCO`, **nesta ordem**:

```python
["days_since_last", "features_used_30d", "mrr",
 "evento_cancelamento", "evento_downgrade", "evento_sessao"]
```

Os três últimos são a codificação one-hot de `event`. A ordem é contrato: um
modelo treinado com as colunas em outra ordem devolve número plausível e
errado, que é o pior tipo de defeito porque não levanta exceção. Por isso o
`carregar_modelo()` **recusa** um modelo cujo meta declare outra ordem.

---

## 2. Onde o dado está

`crai/data/retention_cycles.db` — SQLite, tabela `ciclos_retencao`, uma linha
por ciclo de retenção. Fora do git (`.gitignore`), porque carrega dado de
cliente e se regenera sozinho.

Escrito em duas etapas, e a segunda pode demorar dias:

| Quando | O quê |
|---|---|
| Fim do grafo (`update_crm`) | features + decisão. `accepted` fica `NULL` |
| Chegada do desfecho (`POST /webhooks/retention-outcome`) | `accepted`, `desfecho_em`, `origem_desfecho` |

### Colunas

```
id                INTEGER   chave
tenant_id         TEXT      empresa cliente (Sprint 5)
user_id           TEXT      identidade qualificada: "user:..." ou "anon:..."
registrado_em     TEXT      ISO-8601 UTC

-- X (features no momento da decisão)
event             TEXT
days_since_last   REAL
features_used_30d REAL
mrr               REAL
billing_profile   TEXT
on_site_now       INTEGER

-- contexto (o que o sistema decidiu)
risk_score        REAL      o risco que ESTE código calculou
profile           TEXT
criticality       TEXT      critico | alto | padrao
offer_type        TEXT      NULL quando não houve oferta (ver §4)
channel           TEXT
offer_sent        INTEGER

-- y
accepted          INTEGER   NULL = ciclo aberto, aguardando retorno
desfecho_em       TEXT
origem_desfecho   TEXT      "webhook" (real) | "simulacao" (demo)
```

### Carregar num DataFrame

```python
import sqlite3, pandas as pd

conn = sqlite3.connect("crai/data/retention_cycles.db")
df = pd.read_sql("SELECT * FROM ciclos_retencao", conn)

# Só desfecho REAL: linhas de "simulacao" vêm de `random()` na demo e
# envenenariam o treino tanto quanto envenenavam o bandit antes do Sprint 4.
treino = df[(df.origem_desfecho == "webhook") & df.accepted.notna()]
```

> **Filtre `origem_desfecho == "webhook"`.** É a primeira coisa a fazer e a
> mais fácil de esquecer. A demo (`test_pipeline.py`) roda em modo simulação e
> grava linhas com desfecho sorteado; treinar com elas é aprender com uma moeda.

---

## 3. ⚠️ O label é ACEITAÇÃO DE OFERTA, não churn

**Decisão consciente, não descuido.** `accepted` responde *"o cliente aceitou a
oferta que mandamos?"*, e não *"o cliente cancelou a assinatura?"*.

As duas coisas são diferentes e a diferença importa:

- Um cliente aceita 20% de desconto e cancela dois meses depois. No dataset ele
  é um `accepted = 1`.
- Um cliente ignora a oferta e continua pagando por anos. Ele é um
  `accepted = 0`.

Portanto, um modelo treinado neste dataset como está é um **modelo de
propensão a aceitar oferta de retenção**, não um modelo de risco de churn.
Isso é útil — inclusive é o que o bandit já otimiza —, mas não é o que o nome
`risk_score` sugere.

**O que faltaria para ter churn de verdade:** um sinal de fim de assinatura,
observado num horizonte fixo (30/60/90 dias) depois do evento. Nada no sistema
observa isso hoje: nem o Segment (que manda comportamento, não faturamento),
nem o webhook de desfecho (que fala da oferta), nem o HubSpot (cujo estágio
`churned` sequer é produzido — ver a ressalva em `_crm_do_desfecho`).

Se a fase de treino quiser um modelo de churn, o passo anterior ao treino é
**criar o produtor desse label** — um webhook de cancelamento, ou um job que
leia a base de assinaturas do cliente e marque a coluna. A infraestrutura de
gravação já existe: seria uma coluna a mais em `ciclos_retencao`.

---

## 4. ⚠️ Viés de seleção — e por que agora ele é mensurável

`route_after_risk` corta em **0.60**: quem tem `risk_score < 0.60` não recebe
oferta nenhuma. Logo, esses clientes **não têm desfecho** — não há oferta para
aceitar ou recusar.

Isso significa que o modelo de risco decide quem entra no próprio dataset dele.
É viés de seleção clássico, e ele não desaparece por ser declarado.

**O que este projeto fez a respeito:** desde o Sprint 4, o caminho de risco
baixo **também grava linha**, com `offer_type` e `accepted` nulos. Sem essas
linhas, o dataset só teria a população que passou pelo corte, e o viés seria
invisível. Com elas, dá para medir:

```python
# A população que o sistema decidiu NÃO abordar
nao_abordados = df[df.offer_type.isna()]

# Proporção, e como as features se distribuem nos dois grupos
print(len(nao_abordados) / len(df))
print(df.groupby(df.offer_type.isna())[
    ["days_since_last", "features_used_30d", "mrr"]].describe())
```

**Como tratar na fase de treino** (em ordem de custo):

1. **Declarar.** No mínimo, o relatório do TCC diz que o modelo foi treinado na
   população `risk_score >= 0.60` e não generaliza abaixo disso.
2. **Restringir o escopo.** Assumir explicitamente que o modelo prediz
   aceitação *dado que houve abordagem*, e mantê-lo só nessa faixa.
3. **Quebrar o ciclo com exploração.** Abordar uma fração aleatória dos de
   risco baixo (um ε pequeno) gera desfecho para a população hoje ausente. É a
   mesma ideia que o Thompson Sampling já aplica às OFERTAS, um nível acima —
   aplicada a QUEM abordar, não a O QUE oferecer. Custa dinheiro real, então é
   decisão de produto, não de engenharia.

---

## 5. Como plugar o modelo treinado

O ponto de extensão já existe em `risk_scorer.py` e não exige mudar nada no
pipeline. São dois arquivos em `crai/models/`:

### 5.1 Salvar

```python
import json, joblib
from datetime import date
from crai.churn_voluntary.risk_scorer import (
    FEATURES_DE_RISCO, MODELO_PATH, MODELO_META_PATH,
)

# `modelo` treinado com as colunas EXATAMENTE em FEATURES_DE_RISCO
X = treino[FEATURES_DE_RISCO]          # a ordem sai daqui, não do seu DataFrame
y = treino["accepted"].astype(int)
# modelo.fit(X, y)

joblib.dump(modelo, MODELO_PATH)
MODELO_META_PATH.write_text(json.dumps({
    "features": FEATURES_DE_RISCO,     # OBRIGATÓRIO e conferido no load
    "algoritmo": "LogisticRegression",
    "treinado_em": str(date.today()),
    "n_amostras": len(X),
    "metricas": {"auc": 0.0, "brier": 0.0},
}, indent=2, ensure_ascii=False), encoding="utf-8")
```

O arquivo de features não é opcional: `carregar_modelo()` compara
`meta["features"]` com `FEATURES_DE_RISCO` e **recusa** o modelo se divergirem,
justamente para que uma reordenação de colunas falhe alto em vez de produzir
risco errado em silêncio.

### 5.2 Como o sistema encontra

Na próxima inicialização, `calculate_risk` passa a consultar o modelo:

```
[RISK-VOL] Modelo de risco carregado de .../voluntary_risk.joblib
           (LogisticRegression, treinado em 2026-10-01)
```

Sem os arquivos, nada é logado e as regras fixas continuam valendo — é o estado
de hoje.

### 5.3 O que o sistema exige do modelo

- `predict_proba(X)[0][1]` (preferido) ou `predict(X)[0]`;
- saída numérica finita em **[0, 1]**;
- qualquer falha — arquivo corrompido, `predict` que levanta, saída fora da
  faixa — cai nas **regras fixas**, com log. Um modelo quebrado não derruba o
  ciclo de retenção de ninguém.

### 5.4 Verificar antes de confiar

```bash
cd crai
python -c "from crai.churn_voluntary.risk_scorer import modelo_ativo; print(modelo_ativo())"
pytest tests/test_risk_pluggable.py -v
python test_pipeline.py
```

`tests/test_risk_pluggable.py` cobre os dois lados: sem modelo, os valores são
idênticos aos das regras; com modelo, é ele que decide — e um modelo defeituoso
degrada em vez de quebrar.

### 5.5 Lembrar de mexer no corte

`route_after_risk` corta em 0.60 e `is_critical_risk` em 0.90. Esses números
foram calibrados para a ESCALA das regras fixas. Um modelo probabilístico bem
calibrado tem outra distribuição — trocar o cálculo sem revisitar os cortes
muda quantos clientes são abordados sem ninguém ter decidido isso.

---

## 6. Sobre o gerador sintético (`crai/ml/synthetic_data.py`)

**Não foi estendido, de propósito.** O Sprint 6 permitia estendê-lo se ele não
produzisse as features do voluntário; ele de fato não produz, mas estender
deixou de ser necessário quando o Sprint 4 criou o produtor de dado REAL.
`crai/ml/` é território do churn involuntário, e mexer nele para gerar dado
sintético que ninguém vai usar é risco sem contrapartida.

O mapeamento, para quem quiser bootstrap sintético:

| Feature do voluntário | `generate_behavioral_dataset` |
|---|---|
| `days_since_last` | `days_since_last_login` |
| `features_used_30d` | `feature_adoption` (escala diferente: proporção, não contagem) |
| `mrr` | `mrr_brl` |
| `event` | **não existe** — o gerador é de comportamento, não de eventos |
| `billing_profile` | **não existe** |
| `accepted` | **não existe** — o label de lá é `is_anomalous` |

Ou seja: dá para aproximar três das seis features, e nenhuma delas é o label.
Um bootstrap sintético aqui treinaria um modelo sobre um alvo inventado. Para o
horizonte do TCC, o caminho honesto é rodar o sistema, acumular ciclos reais e
treinar com eles — que é exatamente o que o §2 descreve.

---

## 7. Checklist da fase de treino

- [ ] Rodar o sistema em produção (`CRAI_SIMULATE_OUTCOMES` **ausente**) tempo
      suficiente para acumular ciclos com desfecho
- [ ] `SELECT COUNT(*) FROM ciclos_retencao WHERE origem_desfecho='webhook'` —
      quantos ciclos fechados de verdade existem?
- [ ] Filtrar `origem_desfecho == "webhook"` (§2)
- [ ] Decidir e **declarar** o alvo: aceitação de oferta ou churn real (§3)
- [ ] Medir e **declarar** o viés de seleção (§4)
- [ ] Treinar com `X = treino[FEATURES_DE_RISCO]`, na ordem do módulo (§5.1)
- [ ] Salvar `.joblib` + `_meta.json` em `crai/models/` (§5.1)
- [ ] Revisitar os cortes 0.60 e 0.90 para a nova escala (§5.5)
- [ ] `pytest tests/ -q` e `python test_pipeline.py` verdes

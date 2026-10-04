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

---

## 8. O modelo v3 (promoção de 30/09/2026)

Os §§1-7 descrevem o encaixe original e o treino com **desfecho real**, que
continua sendo o caminho de longo prazo. Esta seção registra o que existe desde a
promoção do voluntário v3: um modelo treinado em **dado sintético com rótulo
latente** (`docs/interno/RELATORIO_TREINO_V3_BLOCO*.md`), que o `risk_scorer`
carrega por um contrato próprio.

### 8.1 O contrato v3

`carregar_modelo()` aceita dois contratos, e só esses (`risk_scorer.contrato_do_meta`):

| contrato | o meta declara | vetor |
|---|---|---|
| legado | `features` == `FEATURES_DE_RISCO`, sem `contrato` e sem `features_versao` | o de sempre; ausente vira 0.0 |
| v3 | `contrato: "v3"`, `features_versao: 1`, `features` == `FEATURES_DE_RISCO_V3` (`crai/ml/voluntario_v3.py`, 15 colunas) | ausente vira **NaN** |

As 15 colunas do v3 são as 6 de `FEATURES_DE_RISCO` mais 9 comportamentais:
`tenure_days`, `seats`, `logins_7d`, `logins_30d`, `avg_session_min`,
`api_calls_7d`, `tickets_30d`, `failed_pay_90d`, `nps_last`. Com o v3 ativo, o
modelo decide sempre que houver `days_since_last` ou `features_used_30d`
(`N_MINIMO_COLUNAS_COMPORTAMENTAIS = 0`); sem os dois, a régua decide e a trilha
do Art. 20 registra o motivo (`motivo_da_regra`). Quando o modelo decide, a
trilha recebe as 3 maiores contribuições do TreeSHAP (`explicacao_v3.py`).

### 8.2 Promover e reverter

De dentro de `app/`:

```
python -m crai.scripts.promover_voluntario_v3 --promover
python -m crai.scripts.promover_voluntario_v3 --reverter
```

O `--promover` copia `models/v3/voluntary_risk_v3.joblib` para `MODELO_PATH` e
grava `MODELO_META_PATH` com `contrato: "v3"` e `referencia_score_quantis` (os
quantis do score no holdout, que o `batch_scoring` usa para posicionar listas
pequenas). Ele recusa se já houver modelo de produção. O `--reverter` move os
dois arquivos para `models/historico/` e a régua volta. Os dois valem a partir do
próximo reinício do processo (o carregamento é cacheado).

**Trava:** com o v3 em produção, `VoluntaryRiskModel.ativar()` e
`train_all --ativar-voluntario` **recusam**. Copiar o candidato v2 por cima
trocaria o modelo de produção sem aviso. Para voltar ao candidato, rode antes o
`--reverter`.

### 8.3 O que o v3 mede, e a correção de um número

| comparação (holdout por cliente da base v3) | modelo v3 | melhor régua | vantagem |
|---|---|---|---|
| todas as 15 colunas | 0,6929 | 0,6156 | 0,0773 |
| só o vetor de produção de hoje (as 6 colunas), evento real | 0,6377 | 0,6156 | **0,0221** |
| só as 6 colunas, evento fixo "Session Started" (o do lote) | 0,6329 | 0,6063 | 0,0266 |

**Correção:** um relatório anterior citou 0,0164 como a vantagem "só com o vetor
que a produção recebe hoje". 0,0164 é a vantagem do cenário (b) do treino v3, que
também esconde `features_used_30d` e as flags de evento. O vetor de produção (as
6 colunas de `FEATURES_DE_RISCO`) dá **0,6377, vantagem de 0,0221**. Números
completos em `docs/evidencia_v3/metricas_v3.json`.

**O rótulo é sintético e foi desenhado pelo projeto.** A vantagem do v3 é
medida contra o próprio gerador, e não diz nada sobre churn observado. O caminho
dos §§2-7, treinar com desfecho real de `ciclos_retencao`, continua sendo o
único que mede isso. Limites declarados em `docs/LIMITACOES.md`.

### 8.4 Regenerar o artefato (ele não vai para o git)

O `.joblib` promovido **não entra no repositório**: o repositório é público, e um
`.joblib` executa código ao ser carregado (pickle). `app/models/` e `app/data/` são
ignorados pelo git. Num clone limpo, a sequência abaixo, de dentro de `app/`,
regenera tudo. Ela foi conferida de ponta a ponta numa pasta temporária em 30/09/2026,
com numpy 1.26.4, pandas 2.2.3 e scikit-learn 1.5.2 (`requirements.txt`).

```
python -m crai.scripts.gerar_bases_v2 --seed 42 --out data/v2/
python -m crai.scripts.gerar_base_v3_voluntario --seed 42 --out data/v3/
python -m crai.scripts.treinar_voluntario_v3 --base data/v3/ --out models/v3/
python -m crai.scripts.promover_voluntario_v3 --promover
```

O que conferir:

| etapa | o que deve bater |
|---|---|
| base v2 | o `hash_canonico` de cada tabela no `data/v2/MANIFESTO.json` igual ao de `docs/base-v2/HASH_CANONICO.json` (populacao `1cd4a2d65ef8ba28...`, voluntario `d60d3f9ac9da87de...`, e as outras três). O **sha256 do arquivo** muda de ambiente para ambiente e não serve para conferir a v2 |
| base v3 | `data/v3/voluntario_v3.parquet` com sha256 `d33be11ee7fd93f6b3c278bc2ebd4d025b1f037a9015b1be523a28661ff7c397` e `hash_canonico` `2ef77ec8b25f7ee079b0aa1d1b181d9b0217a3ea045041a5927eaf436593a7aa` |
| modelo | `models/v3/voluntary_risk_v3.joblib` com sha256 `b4ee00fd823f1e11b15b585c1d0525ffaffe31bb4f9cd1333c7f026cc11cfe7d`; o `--promover` imprime o mesmo início (`b4ee00fd823f1e11`) para `models/voluntary_risk.joblib` |
| métricas | `models/v3/train_metrics_v3.json`: AUC do cenário (a) 0,6929, teto 0,7832 |

Com outras versões de biblioteca, os sha256 da v3 e do joblib podem mudar. Nesse caso
vale o `hash_canonico` da v3 e as métricas do treino. O `--promover` confere sozinho
que a base é a do treino que gerou o modelo.

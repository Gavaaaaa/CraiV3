# Contrato da API para o painel da CRAI

Congelado em 12/09/2026. É o documento que permite construir o painel em qualquer
stack sem esperar o backend: os dois primeiros endpoints existem hoje e estão
documentados **lendo o código** (`app/crai/api/app.py`,
`app/crai/churn_voluntary/importacao.py`, `insights_unificados.py`,
`batch_scoring.py`); os três últimos **não existem** e estão especificados aqui
para implementação na semana seguinte.

Para cada endpoint há uma resposta de exemplo em `painel/fixtures/` (ver
`painel/README.md`). O consumidor troca entre fixtures e API real por uma única
variável de configuração.

| Endpoint | Estado | Fixture |
|---|---|---|
| `POST /clientes/importar` | existe | `painel/fixtures/importar.json` |
| `GET /insights` | existe | `painel/fixtures/insights.json` |
| `GET /modelos/status` | **a implementar** | `painel/fixtures/modelos_status.json` |
| `POST /modelos/retreinar` | **a implementar** | `painel/fixtures/modelos_retreinar.json` |
| `GET /resultado` | **a implementar** | `painel/fixtures/resultado.json` |

Convenções: JSON em UTF-8; datas em ISO-8601 com fuso (`2026-09-12T14:30:00+00:00`);
dinheiro em `number` (reais, duas casas), nunca em texto; `null` é um valor com
significado próprio e **nunca deve ser tratado como zero** (ver `/insights`).

---

## 0. Autenticação (vale para os cinco endpoints)

Todos os endpoints do painel são de **empresa autenticada**. O token é o JWT
emitido pelo **Supabase Auth** no login da empresa e entra no header:

```
Authorization: Bearer <access_token do Supabase>
```

O que o backend confere (`app/crai/accounts/supabase_auth.py`): assinatura
contra o JWKS do projeto (só `ES256` ou `RS256`), expiração, `aud ==
"authenticated"`, e a claim customizada **`tenant_id`** (o identificador da
empresa, 1 a 64 caracteres em `[A-Za-z0-9._-]`). O `tenant_id` nunca é enviado
pelo painel: ele vem de dentro do token. O painel não escolhe empresa.

Erros de autenticação, sempre no formato `{"detail": {"motivo", "detalhe"}}`:

| HTTP | `motivo` | Quando |
|---|---|---|
| 401 | `sem_authorization` | header ausente |
| 401 | `authorization_malformado` | não é `Bearer <token>` |
| 401 | `token_malformado`, `sem_kid`, `chave_desconhecida`, `assinatura_invalida`, `token_expirado`, `audiencia_invalida`, `token_invalido` | token inválido; `detalhe` explica qual parte |
| 401 | `sem_tenant` | token válido, mas sem a claim `tenant_id` (hook do Supabase não configurado) |
| 401 | `tenant_invalido`, `tenant_reservado`, `tenant_com_forma_invalida` | claim com valor recusado |
| 500 | `supabase_nao_configurado` / `jwks_indisponivel` | problema da instalação, não do usuário; o painel não deve mandar refazer login |

Toda resposta 401 traz `WWW-Authenticate: Bearer`. **O Supabase ainda não está
configurado neste ambiente**; o contrato do header já vale, e é por isso que a
troca fixtures ↔ API real precisa ser de uma variável só.

---

## 1. `POST /clientes/importar` — existe hoje

Upload em lote da base de clientes da empresa autenticada. O arquivo é lido,
cada linha é validada e o que sobreviveu é gravado; reimportar o mesmo
`customer_id_externo` **atualiza** a linha (a planilha é uma foto; a mais nova
vale).

**Requisição:** `multipart/form-data`.

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `arquivo` | file | sim | `.csv` ou `.xlsx`, até 10 MB e 50.000 linhas. CSV em UTF-8 (com ou sem BOM) ou Latin-1; separador detectado (`;` é o comum). |
| `mapeamento` | string (JSON) | não | `{"coluna no arquivo": "campo esperado"}`. Ex.: `{"dias_sem_uso": "days_since_last"}`. Sem ele, o casamento é por nome exato, sem distinguir caixa. |

Campos esperados no arquivo:

| Campo | Obrigatório | Regra de validação |
|---|---|---|
| `customer_id_externo` | sim | texto sem espaços, 1 a 128 caracteres |
| `mrr` | sim | número ≥ 0; aceita pt-BR (`"1.234,56"`, `"R$ 120,00"`) |
| `billing_profile` | sim | um de `CLT`, `PJ`, `freelancer` (sem distinguir caixa) |
| `days_since_last` | não | número ≥ 0 ou vazio |
| `features_used_30d` | não | número ≥ 0 ou vazio |
| `email` | não | se preenchido, precisa parecer e-mail |

A base de exemplo `painel/exemplos/base_exemplo_clientes.csv` usa nomes
próprios de coluna e **precisa** deste mapeamento:

```json
{"id_cliente": "customer_id_externo", "perfil_pagador": "billing_profile",
 "dias_sem_uso": "days_since_last", "features_usadas_30d": "features_used_30d"}
```

**Resposta 200** (`importacao.importar`):

```json
{
  "importados": 497,
  "rejeitados": [
    {"linha": 17,  "motivo": "mrr 'abc' não é um número válido"},
    {"linha": 203, "motivo": "billing_profile 'MEI' não é um dos perfis aceitos (CLT, PJ, freelancer)"},
    {"linha": 444, "motivo": "customer_id_externo vazio"}
  ],
  "colunas_nao_encontradas": ["email"],
  "linhas_sem_dado_comportamental": 12,
  "mensagem": "…"
}
```

| Campo | Tipo | Nulo? | Significado |
|---|---|---|---|
| `importados` | integer | não | linhas gravadas (upsert). Duas linhas com o mesmo id contam uma: a última vence. |
| `rejeitados` | array | não (pode ser `[]`) | uma entrada por linha inválida; **não** derruba o lote |
| `rejeitados[].linha` | integer | não | número da linha **na planilha**, cabeçalho = 1, primeira linha de dado = 2. É o número que a pessoa procura no Excel. |
| `rejeitados[].motivo` | string | não | texto em pt-BR, citando o valor recusado |
| `colunas_nao_encontradas` | array de string | não (pode ser `[]`) | campos esperados sem coluna no arquivo. Obrigatória ausente → nada é importado. |
| `linhas_sem_dado_comportamental` | integer | não | importadas **sem** `days_since_last` **e sem** `features_used_30d`. Entram no cadastro, mas o `/insights` vai devolvê-las como `dado_insuficiente`. É aqui que a empresa fica sabendo disso pela primeira vez; a tela deve mostrar. |
| `mensagem` | string | **só existe quando `importados == 0`** | o porquê de nada ter entrado (coluna obrigatória faltando, ou todas as linhas rejeitadas) |

Não há campo "colunas reconhecidas" na resposta de hoje; o reconhecido é o
complemento de `colunas_nao_encontradas` sobre a lista dos seis campos.

**Erros** (`{"detail": {"motivo", "detalhe"}}`):

| HTTP | `motivo` |
|---|---|
| 415 | `extensao_nao_suportada` |
| 413 | `arquivo_grande_demais`, `linhas_demais` |
| 422 | `arquivo_vazio`, `arquivo_ilegivel`, `sem_linhas`, `mapeamento_invalido` |
| 500 | `base_nao_configurada` (nem `SUPABASE_DB_URL` nem `CRAI_CLIENTES_DB` definidas) |

---

## 2. `GET /insights` — existe hoje

O ranking de clientes em risco da empresa autenticada, juntando as duas
origens: a base importada (`upload`) e os eventos do SDK comportamental
(`sdk`). Quando o mesmo cliente está nas duas, aparece **uma** vez, com o dado
mais recente.

**Query params:**

| Param | Tipo | Regra |
|---|---|---|
| `limite` | integer ≥ 1 | corta a lista depois da ordenação. Torto → 422 `limite_invalido`. |
| `criticidade_minima` | `alto` \| `critico` | `alto` mantém alto e crítico; `critico` só crítico. `dado_insuficiente` **nunca** passa por este filtro. Outro valor → 422 `criticidade_invalida`. |

**Resposta 200:**

```json
{
  "total_clientes": 500,
  "clientes_em_risco": [ { …linha… } ],
  "gerado_em": "2026-09-12T14:30:00+00:00",
  "filtros": {"limite": null, "criticidade_minima": null}
}
```

| Campo | Tipo | Nulo? | Significado |
|---|---|---|---|
| `total_clientes` | integer | não | total **antes** dos filtros (upload ∪ sdk) |
| `clientes_em_risco` | array | não | linhas já ordenadas (ver ordenação) |
| `gerado_em` | string ISO | não | quando a resposta foi calculada |
| `filtros.limite` | integer | **sim** (`null` = sem limite) | eco do filtro aplicado |
| `filtros.criticidade_minima` | string | **sim** | eco do filtro aplicado |

**Cada linha de `clientes_em_risco`:**

| Campo | Tipo | Nulo? | Significado |
|---|---|---|---|
| `customer_id_externo` | string | não | o id da empresa (o da planilha, ou o do SDK sem o prefixo `user:`; visitante anônimo vem como `anon:<id>`) |
| `risk_score` | number 0–1 (3 casas) | **SIM — ver abaixo** | risco de churn voluntário |
| `criticality` | string | não | `critico` \| `alto` \| `padrao` \| `dado_insuficiente` |
| `explicacao` | string | não | uma frase em pt-BR com os números que produziram o risco |
| `mrr` | number | sim (SDK pode não trazer) | receita mensal do cliente, em reais |
| `billing_profile` | string | sim (SDK) | `CLT` \| `PJ` \| `freelancer` |
| `days_since_last` | number | sim | dias sem login; `null` = a planilha não tinha |
| `features_used_30d` | number | sim | funcionalidades usadas nos últimos 30 dias; `null` = a planilha não tinha |
| `email` | string | sim | só existe no upload; se o SDK vence a fusão, o e-mail do cadastro é preservado |
| `importado_em` | string ISO | sim | quando a planilha foi importada; `null` na origem `sdk` |
| `origem` | string | não | `upload` \| `sdk` |
| `atualizado_em` | string ISO | não | `importado_em` (upload) ou `registrado_em` do último ciclo (sdk); é o que decide a fusão |
| `evento` | string | sim | só na origem `sdk`: o último evento (`Cancellation Page Viewed`, `Downgrade Clicked`, `Session Started`) |
| `origem_da_regua` | string | não | `base_do_tenant` \| `padrao_global` — qual régua mediu o risco (ver abaixo) |

**Criticidade** (`risk_scorer.classify_criticality`): duas portas independentes
levam a `critico`. Por **risco**: `risk_score ≥ 0,90`. Por **valor**:
`mrr ≥ R$ 2.000` (env `CRAI_HIGH_VALUE_MRR_THRESHOLD`), mesmo com risco baixo.
Nesse segundo caso a `explicacao` termina com "— crítico pelo valor da conta,
não pelo risco". `alto` é `[0,75; 0,90)`. O resto é `padrao`.

**Ordenação** (`batch_scoring.ordenar`): risco decrescente; empate por
criticidade, depois MRR decrescente, depois id. As linhas `dado_insuficiente`
vão **sempre por último**, separadas do risco 0,0 de verdade.

**A régua** (`origem_da_regua`): o risco de uma linha vinda do upload é medido
pela **posição do cliente na própria base da empresa** quando a base tem pelo
menos 30 linhas com `days_since_last` e 30 com `features_used_30d`
(`base_do_tenant`). Em dias sem login, mais é pior, e a régua são os
percentis altos da base (p50/p75/p90); em funcionalidades usadas, menos é
pior, e a régua são os percentis baixos (p10/p25/p50). Um SaaS de uso diário
e um de uso mensal recebem réguas diferentes: 7 dias sem login é alarme num e
rotina no outro. Com base menor, ou sem distribuição, vale a régua fixa de
sempre (`padrao_global`), com o mesmo número de antes. Linhas de origem `sdk`
são sempre `padrao_global`: um evento pontual não tem base para se comparar.
A `explicacao` diz a régua em português, e é a frase que a tela deve mostrar:

```
"sem login há 47 dias — acima de 90% da sua base, usa 2 funcionalidades nos últimos 30 dias — menos que 75% da sua base, MRR R$ 298,74"   (base_do_tenant)
"sem login há 47 dias, usa 2 funcionalidades nos últimos 30 dias, MRR R$ 298,74"                                                          (padrao_global)
```

**Posição ordena; criticidade exige sinal absoluto.** O risco posicional é
relativo: os 10 % mais frios de qualquer base ficam "acima de 90% da sua
base", inclusive numa base em que ninguém está em risco. Por isso, na régua
da base, `alto` e `critico` por risco exigem também um **sinal absoluto de
desengajamento**: pelo menos **7 dias sem login** ou **nenhuma funcionalidade
usada em 30 dias**. Sem esse sinal o cliente pode ser o primeiro da lista
(`risk_score` alto) e continuar `padrao`, e a `explicacao` termina dizendo o
porquê:

```
"sem login há 3 dias — acima de 75% da sua base, usa 9 funcionalidades nos últimos 30 dias — menos que 90% da sua base, MRR R$ 300,00 — primeiro da fila da sua base, mas sem sinal de abandono: entrou há menos de 7 dias e usa o produto"
```

Para a tela: **ordene pelo `risk_score`, colora pela `criticality`**. Um
`risk_score` de 0,89 com `criticality: "padrao"` numa base saudável é o
comportamento correto, não um bug: é "por quem eu começo?", não "quem está
indo embora?". A porta do MRR alto não passa pelo piso: ela é sobre valor.
Quando `origem_da_regua` for `padrao_global` numa base grande, vale um aviso
do tipo "régua padrão: importe as colunas de atividade para a CRAI usar a
régua da sua base".

### ⚠️ `dado_insuficiente` tem `risk_score: null` — e NUNCA `0.0`

Uma linha sem `days_since_last` **e** sem `features_used_30d` não passa pelo
motor de risco. Ela volta com `risk_score: null`, `criticality:
"dado_insuficiente"` e a explicação "sem dado de atividade — impossível avaliar
risco de churn para este cliente".

Por quê: nas regras, dia ausente vale 0 e uso ausente vale 10, e os dois juntos
dão risco **0,0**, que significa "cliente ativo e engajado". Tratar ausência de
dado como "sem risco" é o erro mais caro possível num produto de retenção,
porque silencia exatamente o cliente que já parou de usar. É **invariante do
produto**, travada por teste (`tests/test_batch_scoring.py::test_sem_dado_nunca_e_zero_silencioso`).

O que a tela precisa fazer com isso:

- **nunca** converter `null` em `0`, nem para ordenar, nem para gráfico, nem para média;
- **não** desenhar barra de risco: mostrar um estado próprio ("sem dado de atividade");
- **não** somar essas linhas às faixas `padrao`/`alto`/`critico`;
- mostrar a contagem e dizer o que resolve: importar as duas colunas comportamentais.

Com **uma** das duas presente, o motor roda com o default da outra e a
`explicacao` avisa ("uso de funcionalidades desconhecido (assumido 10)"); o
número existe, mas é parcial e a tela deve mostrar a frase.

**Erros:** 422 `limite_invalido` / `criticidade_invalida`; 500 `base_nao_configurada`.

---

## 3. `GET /modelos/status` — a implementar

Estado, por tenant, de cada modelo de IA: se está ativo, o que está decidindo
de verdade, e com que evidência. É a tela que responde "isso é fórmula ou
modelo?" sem ninguém precisar abrir o código.

**Resposta 200:**

```json
{
  "tenant_id": "empresa-exemplo",
  "consultado_em": "2026-09-12T14:30:00+00:00",
  "modelos": [
    {
      "nome": "voluntary_risk",
      "descricao": "risco de churn voluntário a partir de inatividade, uso e MRR",
      "ativo": false,
      "algoritmo": "GradientBoostingClassifier(n_estimators=150, max_depth=3)",
      "treinado_em": "2026-09-12T15:45:10",
      "n_amostras": 2000,
      "metrica_principal": "auc_vs_rotulo",
      "valor": 0.7133,
      "fonte_dos_dados": "sintetico_calibrado",
      "origem_da_decisao": "regras",
      "observacao": "candidato treinado e NÃO promovido: …"
    }
  ],
  "versoes_bibliotecas": {"scikit-learn": "1.5.2", "torch": "2.13.0+cpu", "…": "…"}
}
```

| Campo | Tipo | Nulo? | Significado |
|---|---|---|---|
| `tenant_id` | string | não | do token |
| `consultado_em` | string ISO | não | |
| `modelos[]` | array | não | sempre os quatro: `failure_classifier`, `anomaly_detector`, `payday_predictor`, `voluntary_risk` |
| `modelos[].nome` | string | não | identificador estável, o mesmo do arquivo em `app/models/` |
| `modelos[].descricao` | string | não | uma frase para leigo |
| `modelos[].ativo` | boolean | não | o artefato existe, carregou e está sendo consultado |
| `modelos[].algoritmo` | string | sim (nunca treinado) | o texto que o `meta.json` do artefato declara |
| `modelos[].treinado_em` | string ISO | sim (nunca treinado) | |
| `modelos[].n_amostras` | integer | sim (nunca treinado) | |
| `modelos[].metrica_principal` | string | sim | nome da métrica (`auc`, `roc_auc`, `roc_auc_ensemble`, `auc_vs_rotulo`) |
| `modelos[].valor` | number | sim | valor da métrica no conjunto de teste do último treino |
| `modelos[].fonte_dos_dados` | string | sim | `sintetico` \| `sintetico_calibrado` \| (futuro) `real` |
| `modelos[].origem_da_decisao` | string | não | **`modelo`** \| **`regras`** — o que está decidindo AGORA. Pode ser `regras` com `ativo: false` (sem artefato) ou com artefato que falhou ao carregar. |
| `modelos[].observacao` | string | sim | por que não está ativo, quando não está |
| `versoes_bibliotecas` | object | não | as versões com que o ambiente está rodando |

Regra para a tela: `origem_da_decisao` é o campo que importa; `ativo` é
detalhe. Hoje o `voluntary_risk` devolve `regras`, e isso é o estado esperado.

---

## 4. `POST /modelos/retreinar` — a implementar

Dispara o retreino do **candidato** de risco voluntário daquele tenant e
decide, com número, se ele passa a valer. O candidato só é promovido se bater
o campeão em uso (hoje, as regras). Sem corpo de requisição.

**Resposta 200:**

```json
{
  "tenant_id": "empresa-exemplo",
  "modelo": "voluntary_risk",
  "promovido": false,
  "auc_candidato": 0.7133,
  "auc_campeao": 0.7405,
  "motivo": "candidato (AUC 0,7133) não supera o campeão em uso (regras fixas, AUC 0,7405 contra o mesmo rótulo); o scorer continua nas regras",
  "n_amostras": 2000,
  "fonte_dos_dados": "sintetico_calibrado",
  "duracao_s": 4.8,
  "iniciado_em": "2026-09-12T14:29:55+00:00",
  "concluido_em": "2026-09-12T14:30:00+00:00",
  "artefatos": {"candidato": "models/voluntary_risk_candidato.joblib",
                "meta": "models/voluntary_risk_candidato_meta.json"}
}
```

| Campo | Tipo | Nulo? | Significado |
|---|---|---|---|
| `promovido` | boolean | não | se o candidato virou o modelo que o scorer carrega |
| `auc_candidato` | number | sim (treino falhou) | AUC do candidato no conjunto de teste |
| `auc_campeao` | number | não | AUC de quem está decidindo hoje, medido contra o **mesmo** rótulo e conjunto |
| `motivo` | string | não | a decisão em uma frase, com os dois números |
| `n_amostras`, `fonte_dos_dados`, `duracao_s` | | sim | do treino |
| `artefatos` | object | sim | caminhos gravados |

Erros previstos: 409 `retreino_em_andamento`; 422 `base_insuficiente`
(`detalhe` diz quantas linhas há e o mínimo); 500 `treino_falhou` com o erro.

Nota de honestidade a manter na tela: enquanto o rótulo de treino for derivado
das próprias regras (estado de hoje), `promovido` vai sair `false` por
construção, e o motivo diz isso.

---

## 5. `GET /resultado` — a implementar

O fechamento do mês: quanto a CRAI recuperou (cobrança que falhou e voltou),
quanto salvou (cliente que ia cancelar e ficou), o ganho incremental e a fatura
correspondente, com a memória de cálculo aberta.

**Query params:** `mes` (`AAAA-MM`, obrigatório).

**Resposta 200:** ver `painel/fixtures/resultado.json`.

| Campo | Tipo | Nulo? | Significado |
|---|---|---|---|
| `tenant_id`, `mes`, `moeda` | string | não | `moeda` é sempre `BRL` |
| **`simulado`** | boolean | não | **`true` nesta fase** (ver aviso abaixo) |
| **`aviso`** | string | sim (só quando `simulado: false`) | texto pronto para a tela exibir |
| `mrr_recuperado` | number | não | soma do valor das cobranças recuperadas no mês (churn involuntário) |
| `mrr_salvo` | number | não | soma do MRR dos clientes cuja oferta de retenção foi aceita no mês (churn voluntário) |
| `ganho_incremental` | number | não | `mrr_recuperado + mrr_salvo` |
| `fatura.success_fee_pct_recuperacao` | number 0–1 | não | percentual sobre recuperação |
| `fatura.success_fee_pct_retencao` | number 0–1 | não | percentual sobre retenção |
| `fatura.fee_recuperacao`, `fatura.fee_retencao`, `fatura.total` | number | não | |
| `fatura.vencimento` | string data | sim | |
| `memoria_de_calculo.involuntario` | object | não | os agregados de `recovery_log.metricas` (ciclos, recuperados, taxa, volume, custo) |
| `memoria_de_calculo.voluntario` | object | não | ciclos, ofertas, aceites, critério de "salvo" |
| `memoria_de_calculo.formulas` | object | não | as fórmulas em texto, para a tela poder mostrar "como chegamos nesse número" |
| `memoria_de_calculo.parametros` | object | não | o percentual em vigor e de onde vem |
| `gerado_em` | string ISO | não | |

### ⚠️ Nesta fase, a recuperação vem de gateway SIMULADO

O gateway de pagamento da CRAI é uma simulação, de propósito: ele exemplifica
como o produto vai funcionar quando a plataforma contratante cobrar pelo
gateway da CRAI. Logo, `mrr_recuperado`, e tudo que deriva dele, é número de
simulação. **A interface deve dizer isso ao usuário**, com o texto de `aviso`,
enquanto `simulado` for `true`. Simulação declarada na tela é honesta;
descoberta numa pergunta, não.

Sobre o percentual: o código usa hoje `CRAI_SUCCESS_FEE_PCT` com default
**0,15** (`app/crai/config.py`), enquanto os documentos de negócio falam em 25 %
sobre recuperação e 20 % sobre retenção. O contrato separa os dois percentuais
para não precisar mudar quando isso for decidido (ver `DECISOES.md`); a fixture
usa o valor do código.

Erros previstos: 422 `mes_invalido`; 500 `base_nao_configurada`.

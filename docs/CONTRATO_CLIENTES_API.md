# Contrato da API de sincronização de clientes

Congelado em 21/09/2026, lendo o código (`app/crai/api/clientes.py`,
`app/crai/churn_voluntary/clientes_importados.py`, `importacao.py`,
`batch_scoring.py`). É o documento contra o qual o painel é construído: quem
mudar uma resposta muda este arquivo no mesmo commit.

As quatro rotas existem hoje:

| Rota | O que faz |
|---|---|
| `POST /clientes` | upsert de um cliente: existe → atualiza; não existe → cria |
| `POST /clientes/lote` | sincronização em massa; item torto vira `rejeitados` |
| `PATCH /clientes/{id}` | mudança parcial: plano, MRR, perfil, comportamento |
| `DELETE /clientes/{id}` | o cliente **cancelou** — soft delete; a linha fica |

Elas convivem com `POST /clientes/importar` (planilha) e `GET /insights`,
documentados em `CONTRATO_PAINEL.md`. Um cliente criado por qualquer das
rotas abaixo aparece no `GET /insights` sem upload de planilha.

Convenções (as mesmas do `CONTRATO_PAINEL.md`): JSON em UTF-8; datas em
ISO-8601 UTC com fuso (`2026-09-21T14:30:00+00:00`); dinheiro em `number`;
`null` é um valor com significado e nunca vale zero. Erros sempre em
`{"detail": {"motivo", "detalhe"[, "campo"]}}` — `motivo` é curto e estável
(é nele que o painel decide o que mostrar), `detalhe` é texto em pt-BR.

---

## 0. O que vale para as quatro rotas

### 0.1 Autenticação

JWT do Supabase no header `Authorization: Bearer <access_token>`. O
`tenant_id` vem da claim do token; nunca vai no corpo nem no caminho. Os
erros 401 e 500 de autenticação são exatamente os do `CONTRATO_PAINEL.md`
seção 0 (`sem_authorization`, `authorization_malformado`, `token_expirado`,
`sem_tenant`, ... e `supabase_nao_configurado`, `jwks_indisponivel`).

### 0.2 Isolamento por tenant

Toda leitura e escrita é filtrada pelo tenant do token. Um cliente que existe
em **outro** tenant responde **404 `cliente_nao_encontrado`**, com o mesmo
corpo de um cliente que não existe em lugar nenhum. Nunca 403: um 403
confirmaria a existência do cliente para quem não deveria saber dele.

### 0.3 Idempotência (header opcional `Idempotency-Key`)

```
Idempotency-Key: <qualquer texto que identifique esta requisição no seu lado>
```

Com o header, a mesma chave (por tenant, método e caminho) repetida dentro de
7 dias devolve **200** com `"reenvio": true` e o **estado atual** do cliente,
**sem aplicar a operação de novo**. Regras:

- A chave só é registrada depois que o corpo passou na validação. Um 422 não
  consome a chave: corrija o corpo e reenvie com a mesma chave.
- A janela vive na memória do processo (reinício zera; ver
  `api/idempotencia.py`). Para o painel isso é transparente.
- Sem o header, cada requisição é aplicada. O upsert e o cancelamento são
  naturalmente idempotentes; o header importa mais para o `PATCH` e o `/lote`.
- No `/lote`, o reenvio devolve `importados: 0`, `rejeitados: []` e
  `reenvio: true` — o servidor não guarda o relatório da primeira execução.

### 0.4 Os dois campos de régua, em toda resposta 200

| Campo | Tipo | Significado |
|---|---|---|
| `regua_calculada_em` | string ISO-8601 ou `null` | quando a régua de risco deste tenant foi calculada pela última vez **em lote** (a cada `GET /insights`). **Vive na memória do processo**, e isso tem três consequências que o consumidor precisa tratar como estado normal, não como erro: (1) volta a `null` sempre que o processo sobe, sem que nada esteja errado; (2) zera a cada deploy; (3) com mais de um worker do uvicorn, dois pedidos seguidos podem receber valores diferentes, porque cada worker tem a própria memória. `null` significa "este processo ainda não calculou o ranking deste tenant" — nunca "a base está vazia" nem "houve falha". Não use este campo para decidir se o cliente já entrou no ranking; para isso, leia o `/insights`. |
| `regua_versao` | string | versão do esquema de cálculo. Hoje `"lote-v1"`: a régua é recalculada inteira a cada leitura do ranking, e nenhuma destas rotas recalcula nada. Quando a régua incremental entrar, este valor muda e o contrato das rotas não. |

O que isso diz ao painel: um cliente criado ou alterado por estas rotas entra
no ranking na **próxima** leitura do `/insights`, com a régua recalculada
naquele momento. Não há defasagem visível para quem lê o `/insights` logo em
seguida; os dois campos existem para que a defasagem, quando passar a existir,
já tenha onde ser mostrada. Enquanto o valor for de memória de processo (ver
a tabela), o painel deve exibi-lo como informação ("régua calculada às …" ou
"ainda não calculada nesta sessão do serviço") e nunca condicionar uma ação a
ele.

### 0.5 O objeto `cliente`

Devolvido por `POST /clientes`, `PATCH` e `DELETE`:

```json
{
  "customer_id_externo": "c-001",
  "mrr": 1500.0,
  "billing_profile": "PJ",
  "days_since_last": 47.0,
  "features_used_30d": 1.0,
  "email": "fin@c001.com",
  "importado_em": "2026-09-21T14:30:00+00:00",
  "cancelado_em": null,
  "motivo_cancelamento": null,
  "atualizado_em": null
}
```

| Campo | Tipo | Nulo? | Significado |
|---|---|---|---|
| `customer_id_externo` | string | não | o id do cliente **no sistema da empresa** |
| `mrr` | number | não | receita mensal, reais |
| `billing_profile` | string | não | `CLT`, `PJ` ou `freelancer` (devolvido normalizado) |
| `days_since_last` | number | sim | dias desde a última atividade |
| `features_used_30d` | number | sim | funcionalidades usadas nos últimos 30 dias |
| `email` | string | sim | só para o insight por e-mail |
| `importado_em` | string | não | última vez que a foto (POST, lote ou planilha) entrou |
| `cancelado_em` | string | sim | **preenchido = cancelou**. Sai do ranking de risco; fica no histórico |
| `motivo_cancelamento` | string | sim | o que o backend da empresa informou no DELETE |
| `atualizado_em` | string | sim | último `PATCH` |

### 0.6 Validação: a mesma da planilha

Um cliente que chega pela API passa **exatamente** pelo crivo de
`POST /clientes/importar` (`importacao.validar_linha`):

| Campo | Obrigatório | Regra |
|---|---|---|
| `customer_id_externo` | sim | texto sem espaços, 1 a 128 caracteres |
| `mrr` | sim | número ≥ 0; aceita número JSON ou texto pt-BR (`"1.234,56"`, `"R$ 120,00"`) |
| `billing_profile` | sim | `CLT`, `PJ` ou `freelancer`, sem distinguir caixa |
| `days_since_last` | não | número ≥ 0 ou `null` |
| `features_used_30d` | não | número ≥ 0 ou `null` |
| `email` | não | se preenchido, precisa parecer e-mail |

Campo fora dessa lista é **422 `campo_desconhecido`** (no `/lote`, é
rejeição do item). A mensagem de `cliente_invalido` é a mesma frase que a
planilha devolve em `rejeitados[].motivo`.

---

## 1. `POST /clientes` — upsert de um cliente

**Requisição** (`application/json`): os seis campos de 0.6 e, opcionalmente,
`reativar`.

```json
{
  "customer_id_externo": "c-001",
  "mrr": 1500.0,
  "billing_profile": "PJ",
  "days_since_last": 47,
  "features_used_30d": 1,
  "email": "fin@c001.com"
}
```

| Campo extra | Tipo | Padrão | Significado |
|---|---|---|---|
| `reativar` | boolean | `false` | `true` limpa `cancelado_em` e `motivo_cancelamento` de um cliente cancelado. É a **única** forma de reativar. |

**Regra que não é óbvia:** o upsert **não ressuscita** quem cancelou. Sem
`reativar: true`, um POST sobre um cliente cancelado atualiza MRR, perfil e
comportamento, mas `cancelado_em` fica como está. Reimportar a base (ou
sincronizar todo mundo) nunca apaga um cancelamento por acidente.

**Resposta 200:**

```json
{
  "cliente": { "...objeto de 0.5..." },
  "reenvio": false,
  "regua_calculada_em": "2026-09-21T14:29:10+00:00",
  "regua_versao": "lote-v1"
}
```

`reenvio: true` só com `Idempotency-Key` repetida (0.3); nesse caso `cliente`
é o estado atual, não o do corpo enviado.

**Erros:**

| HTTP | `motivo` | Quando |
|---|---|---|
| 422 | `cliente_invalido` | algum campo reprovou em 0.6; `detalhe` diz qual e por quê |
| 422 | `campo_desconhecido` | chave fora dos seis campos + `reativar`; `campo` diz qual |
| 422 | `reativar_invalido` | `reativar` não é boolean |
| 422 | `corpo_invalido` | o corpo não é um objeto JSON |
| 500 | `base_nao_configurada` | nem `SUPABASE_DB_URL` nem `CRAI_CLIENTES_DB` definidas |

Exemplo de erro:

```json
{"detail": {"motivo": "cliente_invalido",
            "detalhe": "billing_profile 'MEI' não é um dos perfis aceitos (CLT, PJ, freelancer)"}}
```

---

## 2. `POST /clientes/lote` — sincronização em massa

**Requisição:**

```json
{
  "clientes": [
    {"customer_id_externo": "c-001", "mrr": 1500.0, "billing_profile": "PJ",
     "days_since_last": 47, "features_used_30d": 1, "email": "fin@c001.com"},
    {"customer_id_externo": "c-002", "mrr": "abc", "billing_profile": "CLT"},
    {"customer_id_externo": "c-003", "mrr": 220.5, "billing_profile": "freelancer"}
  ]
}
```

Até 50.000 itens. Sem `reativar` aqui: **o lote nunca reativa** — é uma foto
do cadastro, não uma declaração de que todo mundo está ativo. Dentro do
lote, `customer_id_externo` repetido: o último vence.

**Resposta 200** (mesmo contrato de `/clientes/importar`):

```json
{
  "importados": 2,
  "rejeitados": [
    {"indice": 1, "motivo": "mrr 'abc' não é um número válido"}
  ],
  "linhas_sem_dado_comportamental": 1,
  "reenvio": false,
  "regua_calculada_em": null,
  "regua_versao": "lote-v1"
}
```

| Campo | Tipo | Significado |
|---|---|---|
| `importados` | integer | itens gravados (upsert). Ids repetidos contam uma vez. |
| `rejeitados` | array | um por item inválido; **não derruba o lote**. Um cliente errado no meio de mil não impede os outros 999. |
| `rejeitados[].indice` | integer | posição no array `clientes`, **a partir de 0** |
| `rejeitados[].motivo` | string | a mesma frase da planilha; ou `campo(s) não reconhecido(s): ...`; ou `não é um objeto com os campos do cliente` |
| `linhas_sem_dado_comportamental` | integer | gravados sem `days_since_last` **e** sem `features_used_30d` — o `/insights` os devolve como `dado_insuficiente` |

**Erros** (derrubam o lote inteiro, porque o problema é do envelope, não de um item):

| HTTP | `motivo` | Quando |
|---|---|---|
| 422 | `lote_vazio` | `clientes` é `[]` |
| 422 | `lote_invalido` | `clientes` não é uma lista |
| 422 | `campo_desconhecido` | chave fora de `clientes` no envelope |
| 422 | `corpo_invalido` | o corpo não é um objeto JSON |
| 413 | `linhas_demais` | mais de 50.000 itens |
| 500 | `base_nao_configurada` | idem |

---

## 3. `PATCH /clientes/{id}` — mudança parcial

`{id}` é o `customer_id_externo`. **Só o que vier no corpo muda**; campo
ausente fica como está. Mudar o MRR não toca em `days_since_last` nem em
`features_used_30d`.

**Requisição:** qualquer subconjunto não vazio de `mrr`, `billing_profile`,
`days_since_last`, `features_used_30d`, `email`.

```json
{"mrr": 2500.0, "billing_profile": "PJ"}
```

- `null` num campo opcional limpa o campo (`{"days_since_last": null}`).
- `mrr` e `billing_profile` não aceitam `null` (são obrigatórios: 422
  `cliente_invalido`).
- A validação roda sobre o cliente **já mesclado**: o resultado do PATCH é
  sempre um cliente que a planilha aceitaria.
- Um cliente cancelado pode ser atualizado; continua cancelado.

**Resposta 200:** o mesmo envelope de `POST /clientes`, com `atualizado_em`
preenchido.

```json
{
  "cliente": {"customer_id_externo": "c-001", "mrr": 2500.0, "billing_profile": "PJ",
              "days_since_last": 47.0, "features_used_30d": 1.0, "email": "fin@c001.com",
              "importado_em": "2026-09-21T14:30:00+00:00", "cancelado_em": null,
              "motivo_cancelamento": null, "atualizado_em": "2026-09-21T15:02:41+00:00"},
  "reenvio": false,
  "regua_calculada_em": "2026-09-21T14:29:10+00:00",
  "regua_versao": "lote-v1"
}
```

**Erros:**

| HTTP | `motivo` | Quando |
|---|---|---|
| 404 | `cliente_nao_encontrado` | não existe neste tenant (inclusive se existe em outro) |
| 422 | `corpo_vazio` | `{}` |
| 422 | `campo_desconhecido` | chave fora das cinco; inclui `customer_id_externo` (o id vai no caminho) |
| 422 | `cliente_invalido` | o cliente mesclado reprovou em 0.6 |
| 422 | `corpo_invalido` | o corpo não é um objeto JSON |
| 500 | `base_nao_configurada` | idem |

Ordem: 404 é verificado antes de `cliente_invalido` (precisa da linha atual
para mesclar); `corpo_vazio` e `campo_desconhecido` vêm antes do 404.

---

## 4. `DELETE /clientes/{id}` — o cliente cancelou

**É um evento de churn, não uma exclusão.** A linha permanece, com
`cancelado_em` preenchido. Esse campo é a semente do treino com desfecho
observado — o único rótulo do sistema que não foi produzido pela própria regra
de risco. O cliente sai do ranking do `/insights` e fica no histórico.

**Requisição:** corpo opcional.

```json
{"motivo": "mudou de fornecedor"}
```

| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `motivo` | string | não | até 500 caracteres. Texto livre do backend da empresa; pode conter dado pessoal — a CRAI não o loga nem o devolve em listagem agregada. |

**Resposta 200:**

```json
{
  "cliente": {"customer_id_externo": "c-001", "mrr": 1500.0, "billing_profile": "PJ",
              "days_since_last": 47.0, "features_used_30d": 1.0, "email": "fin@c001.com",
              "importado_em": "2026-09-21T14:30:00+00:00",
              "cancelado_em": "2026-09-21T16:10:05+00:00",
              "motivo_cancelamento": "mudou de fornecedor",
              "atualizado_em": null},
  "ja_estava_cancelado": false,
  "reenvio": false,
  "regua_calculada_em": "2026-09-21T14:29:10+00:00",
  "regua_versao": "lote-v1"
}
```

| Campo | Significado |
|---|---|
| `ja_estava_cancelado` | `true` quando o cliente já estava cancelado. Nesse caso `cancelado_em` e `motivo_cancelamento` são os **originais** — a primeira data é a verdadeira e um segundo DELETE não a move. É 200, não 404 nem 409. |

**Erros:**

| HTTP | `motivo` | Quando |
|---|---|---|
| 404 | `cliente_nao_encontrado` | não existe neste tenant (inclusive se existe em outro; nunca 403) |
| 422 | `motivo_invalido` | `motivo` não é texto |
| 422 | `motivo_longo` | mais de 500 caracteres; nada é cancelado |
| 422 | `campo_desconhecido` | chave fora de `motivo` |
| 422 | `corpo_invalido` | o corpo não é um objeto JSON |
| 500 | `base_nao_configurada` | idem |

Para trazer o cliente de volta: `POST /clientes` com `"reativar": true`
(seção 1). Não existe "des-cancelar" no DELETE.

---

## 5. Sequência típica para o painel

1. `POST /clientes/lote` com a base inicial → `importados`, `rejeitados`.
2. `GET /insights` → ranking; a régua é calculada aqui
   (`regua_calculada_em` das rotas passa a refletir esse momento).
3. Mudanças do dia a dia: `POST /clientes` (novo ou foto inteira),
   `PATCH /clientes/{id}` (só o que mudou), `DELETE /clientes/{id}` (cancelou).
4. `GET /insights` de novo: quem cancelou não aparece; quem mudou aparece com
   o dado novo.

Para conferir se um cliente específico está cancelado sem ler o ranking:
`DELETE /clientes/{id}` com `Idempotency-Key` não serve para isso — use o
`ja_estava_cancelado` de um DELETE real só se a intenção for cancelar. Uma
rota `GET /clientes/{id}` não faz parte deste contrato.

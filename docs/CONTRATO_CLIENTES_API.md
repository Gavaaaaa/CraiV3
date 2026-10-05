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

Desde 04/10/2026 as quatro rotas aceitam também a **chave de API** da empresa
(0.1b). O token de login continua valendo como antes: a chave é um segundo
caminho, não uma troca.

### 0.1b Autenticação por chave de API

Para o **sistema da empresa** chamar as quatro rotas sozinho, sem ninguém
logado. A chave vai no mesmo header:

```
Authorization: Bearer crai_live_EXEMPLO_NAO_E_UMA_CHAVE_DE_VERDADE_000
```

**Como a empresa consegue a chave.** No dashboard, na aba API, um dono ou
administrador de uma empresa do plano premium gera a chave com um clique (o
dashboard dá a ela um nome padrão, "Chave de API" mais a data).
A chave inteira aparece **uma única vez**, na hora em que é gerada: a CRAI
guarda só o hash, o começo e os 4 últimos caracteres. Perdeu a chave, gere
outra e revogue a antiga.

**O que a chave faz e o que não faz.**

- Autentica **só** estas quatro rotas: `POST /clientes`, `POST /clientes/lote`,
  `PATCH /clientes/{id}` e `DELETE /clientes/{id}`; e, desde a Rodada 3, uma
  quinta: `POST /eventos` (seção 6). Em qualquer outra rota
  (dashboard, configuração, ciclos, titular, `/insights`, `/clientes/importar`
  e as próprias rotas de chave) ela não vale: 401.
- O `tenant_id` vem **da chave**, nunca do corpo nem de header. Tudo o que 0.2
  diz sobre isolamento vale igual: cliente de outra empresa é 404, o mesmo do
  inexistente.
- Validação, idempotência (0.3) e respostas são exatamente as do token de
  login. Só a origem do tenant muda.
- A revogação é imediata: a requisição seguinte com a chave revogada já
  recebe 401.
- Cada empresa tem no máximo 5 chaves ativas.
- Cada chave tem um limite de requisições por minuto (120 por padrão; o
  operador da instalação muda em `CRAI_API_LIMITE_POR_MINUTO`). Um lote conta
  como uma requisição: para carga grande, use `POST /clientes/lote`.

**Exemplo em `curl`** (a chave fica numa variável de ambiente, nunca no
código nem no histórico do terminal):

```bash
curl -X POST "https://api.exemplo-crai.com.br/clientes" \
  -H "Authorization: Bearer $CRAI_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: cadastro-c-001-2026-10-04" \
  -d '{"customer_id_externo": "c-001", "mrr": 1500.0, "billing_profile": "PJ"}'
```

**Exemplo em Python** (só a biblioteca padrão):

```python
import json
import os
import urllib.request

API = "https://api.exemplo-crai.com.br"
CHAVE = os.environ["CRAI_API_KEY"]          # crai_live_EXEMPLO...: nunca no código

def chamar(metodo, caminho, corpo=None):
    dados = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    req = urllib.request.Request(API + caminho, data=dados, method=metodo, headers={
        "Authorization": f"Bearer {CHAVE}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=30) as resposta:
        return json.loads(resposta.read().decode("utf-8"))

# Cadastrar ou atualizar um cliente
chamar("POST", "/clientes", {"customer_id_externo": "c-001", "mrr": 1500.0,
                             "billing_profile": "PJ"})
# O cliente mudou de valor
chamar("PATCH", "/clientes/c-001", {"mrr": 1800.0})
# O cliente cancelou
chamar("DELETE", "/clientes/c-001", {"motivo": "mudou de fornecedor"})
```

**Erros de autenticação pela chave** (o corpo é sempre
`{"detail": {"motivo", "detalhe"}}`):

| HTTP | `motivo` | Quando |
|---|---|---|
| 401 | `chave_invalida` | a chave não existe, está malformada ou foi revogada. **A resposta é a mesma nos três casos**, byte a byte: nada revela se a chave existe |
| 401 | `chave_nao_vale_nesta_rota` | a chave foi enviada a uma rota fora das cinco (as quatro de clientes e `POST /eventos`). A resposta não depende de a chave ser válida |
| 429 | `limite_de_uso` | a chave passou do limite por minuto. O header `Retry-After` traz em quantos segundos tentar de novo; a requisição recusada não foi aplicada |

Um `Authorization: Bearer` que não começa por `crai_live_` é tratado como
token de login, e recebe os erros de 0.1.

**Erros ao gerar e revogar a chave** (rotas do dashboard, com o token de
login; não são chamadas pelo sistema da empresa):

| Rota | HTTP | `motivo` | Quando |
|---|---|---|---|
| `POST /integracao/chaves` | 403 | `plano_sem_api` | a empresa não é do plano premium. **Só gerar** é do premium: listar e revogar valem em qualquer plano, para a empresa que saiu do premium conseguir ver e desligar as chaves que tem |
| `POST` e `DELETE /integracao/chaves...` | 403 | `papel_insuficiente` | quem pede não é dono nem administrador (membro só vê a lista) |
| `POST /integracao/chaves` | 409 | `limite_de_chaves` | a empresa já tem 5 chaves ativas; revogue uma para gerar outra |
| `POST /integracao/chaves` | 422 | `nome_invalido`, `campo_desconhecido` | o corpo é `{"nome": "..."}`, com 1 a 60 caracteres |
| `DELETE /integracao/chaves/{id}` | 404 | `chave_nao_encontrada` | não há chave com este id nesta empresa (inclusive se existe em outra) |

As três rotas do dashboard:

| Rota | Resposta |
|---|---|
| `GET /integracao/chaves` | 200 `{"chaves": [...], "ativas", "limite_ativas", "pode_revogar", "plano_permite_gerar", "pode_gerar"}`. `pode_revogar`: o papel revoga (dono ou administrador); `plano_permite_gerar`: a empresa é premium; `pode_gerar`: as duas coisas. Cada chave: `id`, `nome`, `prefixo` (`crai_live_` + 4 caracteres), `final` (4 caracteres), `criada_em`, `criada_por_papel`, `ultimo_uso_em`, `revogada_em`, `situacao` (`ativa` ou `revogada`) e `usos_hoje`. Sem o hash e sem a chave |
| `POST /integracao/chaves` | 201 `{"chave": {...}, "chave_inteira": "crai_live_...", "aviso"}`, com `Cache-Control: no-store`. É a única resposta que traz a chave inteira |
| `DELETE /integracao/chaves/{id}` | 200 `{"chave": {...}, "ja_estava_revogada"}`. Revogar duas vezes devolve 200 com a data original |

O que a chave ainda não faz (um tipo só, sem escopos, sem chave de teste,
limite por processo) está em `docs/LIMITACOES.md`.

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
  "atualizado_em": null,
  "id_recorrencia": "RN_8841a2",
  "logins_7d": 0, "logins_30d": 3, "avg_session_min": 4.5, "api_calls_7d": null,
  "tickets_30d": 2, "failed_pay_90d": 1, "nps_last": 6.0, "seats": 3, "tenure_days": 400,
  "telefone": "+5511988887777",
  "nome": "Ana Souza"
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
| `id_recorrencia` | string | sim | o id da autorização do **Pix Automático** deste cliente no PSP. Liga a cobrança que falhou (churn involuntário) a este cadastro. Único por empresa |
| `logins_7d`, `logins_30d`, `api_calls_7d`, `tickets_30d`, `failed_pay_90d`, `seats`, `tenure_days` | inteiro | sim | comportamento do cliente (os nomes são os do modelo v3 do voluntário) |
| `avg_session_min` | number | sim | duração média da sessão, em minutos |
| `nps_last` | number | sim | última nota de satisfação, 0 a 10 |
| `telefone` | string | sim | contato do cliente final para a mensagem do involuntário (WhatsApp); devolvido só com dígitos e `+` |
| `nome` | string | sim | nome do cliente final; a tela o mostra, e **só o primeiro nome** entra no texto da mensagem |

**As 12 colunas da Etapa 2** (de `id_recorrencia` a `nome`) são todas
opcionais: ausente ou vazio é `null`, **nunca 0**. Quem não as manda recebe a
mesma resposta de antes (elas não aparecem em `colunas_nao_encontradas`).

**Telefone, e-mail e nome ficam só aqui.** A mensagem do involuntário lê o
contato desta base **na hora do envio**, pelo `id_recorrencia`, e nunca o copia
para o ciclo, as tentativas ou a tabela de mensagens. Sem contato elegível, a
mensagem é gerada e fica marcada como não entregável — o canal mostrado é
`sem_canal`, nunca um canal que o cliente não tem.

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
| `id_recorrencia` | não | texto sem espaços, 1 a 128 caracteres; **único por empresa**: já ligado a outro cliente é **409 `id_recorrencia_em_uso`** (no `/lote` e na planilha, rejeição da linha); repetido em dois clientes do mesmo lote, a segunda linha é rejeitada |
| `logins_7d`, `logins_30d`, `api_calls_7d`, `tickets_30d`, `failed_pay_90d`, `seats`, `tenure_days` | não | inteiro ≥ 0 (`"3"` e `"3,0"` valem; `"3,5"` não) ou `null` |
| `avg_session_min` | não | número ≥ 0 ou `null` |
| `nps_last` | não | número de 0 a 10 ou `null` |
| `telefone` | não | 10 a 15 dígitos, `+` opcional; espaços, pontos, hífens e parênteses são ignorados |
| `nome` | não | até 120 caracteres, sem caractere de controle; espaços repetidos são reduzidos |

Campo fora dessa lista é **422 `campo_desconhecido`** (no `/lote`, é
rejeição do item). A mensagem de `cliente_invalido` é a mesma frase que a
planilha devolve em `rejeitados[].motivo`.

---

## 1. `POST /clientes` — upsert de um cliente

**Requisição** (`application/json`): os campos de 0.6 e, opcionalmente,
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
`days_since_last`, `features_used_30d`, `email` e as 12 colunas da Etapa 2
(um `id_recorrencia` já ligado a outro cliente é 409).

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

---

## 6. `POST /eventos` — avisar o que o cliente final fez (Rodada 3)

O sistema da empresa avisa a CRAI de um evento de comportamento ("abriu a página de
cancelamento"), do **servidor dela**, com a mesma chave de API. Antes disto, o evento só
chegava pelo Segment.

**Autenticação:** a chave de API (`Authorization: Bearer crai_live_...`) ou o token de login.
O tenant vem da chave: um `tenant_id` no corpo ou em header é ignorado.

**A chave é secreta.** Esta chamada sai do servidor da empresa. Não coloque a chave em
página web nem em aplicativo: quem a tiver altera a base de clientes. O SDK de navegador,
com chave pública, é de outra etapa.

**Corpo:** o mesmo do webhook do Segment, com o mesmo vocabulário e a mesma validação.

```json
{"userId": "c-001",
 "event": "Cancellation Page Viewed",
 "properties": {"mrr": 1500.0, "billing_profile": "PJ"},
 "messageId": "evento-0001",
 "timestamp": "2026-10-04T12:00:00Z"}
```

| Campo | Regra |
|---|---|
| `userId` ou `anonymousId` | texto; pelo menos um. Use em `userId` o mesmo identificador do cadastro (`customer_id_externo`) |
| `event` | texto. Os que o sistema reconhece: `Cancellation Page Viewed`, `Downgrade Clicked`, `Session Started`. Outro nome é aceito e não gera ação |
| `properties` | objeto, opcional. Os campos de comportamento do cadastro (`mrr`, `billing_profile`, `days_since_last`, `features_used_30d`, ...) |
| `messageId` | texto de 1 a 128 caracteres, opcional. É a chave de idempotência |
| `timestamp` | opcional. Sem `messageId`, entra na identificação do evento |

**Resposta 200:** `{"status": "ok", "duplicado": false}`. A resposta não diz o risco nem se
houve oferta: quem decide se e quando falar com o cliente é a CRAI, pelas regras da
configuração da empresa.

**Idempotência.** O mesmo evento reenviado conta uma vez, e a resposta do reenvio traz
`duplicado: true`. Com `messageId`, é ele que identifica o evento. Sem ele, o que identifica
é o conteúdo (`userId` ou `anonymousId`, `event`, `properties` e `timestamp`): para avisar o
mesmo fato de novo, mude o `timestamp`. A memória de eventos recebidos dura 30 dias.

**Limite de contato.** O evento segue pelo mesmo caminho do Segment. Um cliente final recebe
no máximo uma oferta de retenção a cada `intervalo_minimo_ofertas_dias` (configuração da
empresa, de 1 a 365 dias, padrão 30), venha o evento de onde vier.

| HTTP | `motivo` | Quando |
|---|---|---|
| 400 | (texto) | o corpo não é JSON, ou não é um objeto |
| 401 | `chave_invalida` | chave inexistente, malformada ou revogada (a mesma resposta nas três) |
| 422 | `evento_sem_identificacao` ou `campo_com_forma_invalida` | sem `userId` e sem `anonymousId`; campo com tipo errado; `messageId` vazio ou longo demais |
| 429 | `limite_de_eventos` | a chave passou de `CRAI_EVENTOS_LIMITE_POR_MINUTO` (padrão 600) eventos por minuto. É um limite próprio, separado do das rotas de clientes. `Retry-After` diz quando tentar de novo; o evento recusado não foi processado |

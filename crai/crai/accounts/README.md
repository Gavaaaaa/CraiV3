# `crai/accounts` — quem é a empresa que está chamando a API

A CRAI **não** cadastra empresa, não guarda senha e não emite API key. Tudo
isso mora no **Supabase** (Auth + Postgres): o frontend faz login direto no
Supabase, recebe um JWT, e manda esse JWT em toda chamada à CRAI:

```
Authorization: Bearer <access_token do Supabase>
```

Este pacote só **valida** o token e extrai o `tenant_id` de dentro dele. É
esse `tenant_id` que o resto do pipeline (bandit, `retention_log`, e daqui
em diante a base importada e os insights) já usa para particionar dado.

## O contrato: a claim `tenant_id`

O token padrão do Supabase identifica o **usuário** (`sub`), não a
**empresa**. Uma empresa pode ter vários usuários, e o que a CRAI particiona
é a empresa. Por isso o token precisa carregar uma claim a mais:

| claim       | origem                   | uso na CRAI                                   |
|-------------|--------------------------|-----------------------------------------------|
| `sub`       | Supabase Auth            | id do usuário (não usado para particionar)     |
| `role`      | Supabase Auth            | `authenticated` para usuário logado            |
| `aud`       | Supabase Auth            | precisa ser `authenticated` (verificado)       |
| `exp`       | Supabase Auth            | expiração (verificada; obrigatória)            |
| `tenant_id` | **Custom Access Token Hook** | **a empresa** — obrigatória, 401 se faltar |

Formato aceito para `tenant_id`: 1 a 64 caracteres em `[A-Za-z0-9._-]`,
o mesmo que os webhooks já aceitam em `x-tenant-id`. O literal
`default_tenant` é recusado (é o balde de "não declarou tenant").

### Exemplo de payload decodificado

```json
{
  "iss": "https://abcdefgh.supabase.co/auth/v1",
  "sub": "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b",
  "aud": "authenticated",
  "role": "authenticated",
  "email": "financeiro@empresa-exemplo.com.br",
  "exp": 1757347200,
  "iat": 1757343600,
  "tenant_id": "empresa-exemplo"
}
```

Header esperado: `{"alg": "ES256", "kid": "<id da chave>", "typ": "JWT"}`.
Projetos antigos emitem `RS256`; os dois são aceitos. `HS256` é recusado
(ver "Segurança" abaixo).

## Como o `tenant_id` entra no token (configuração no Supabase, fora deste repo)

1. Tabela `empresas` no Postgres do Supabase, com pelo menos:
   `id text primary key` (o `tenant_id`), `nome text`, `criada_em timestamptz`.
   E um vínculo usuário → empresa, por exemplo `usuarios_empresas(user_id uuid
   references auth.users, empresa_id text references empresas)`.
2. Uma função Postgres registrada como **Custom Access Token Hook**
   (Dashboard → Authentication → Hooks). Ela roda a cada login e devolve as
   claims com `tenant_id` adicionado. Esboço:

   ```sql
   create or replace function public.custom_access_token_hook(event jsonb)
   returns jsonb language plpgsql stable as $$
   declare
     claims jsonb := event->'claims';
     empresa text;
   begin
     select empresa_id into empresa
       from public.usuarios_empresas
      where user_id = (event->>'user_id')::uuid
      limit 1;
     if empresa is not null then
       claims := jsonb_set(claims, '{tenant_id}', to_jsonb(empresa));
     end if;
     return jsonb_set(event, '{claims}', claims);
   end;
   $$;
   grant execute on function public.custom_access_token_hook to supabase_auth_admin;
   ```

   Referência: docs.supabase.com/guides/auth/auth-hooks/custom-access-token-hook.
3. Nada muda no código Python quando a empresa é criada ou o plano do
   Supabase muda (Free → Pro): a validação é a mesma.

Usuário logado **sem** vínculo em `empresas` recebe um token válido sem
`tenant_id`, e a CRAI responde `401 {"motivo": "sem_tenant", ...}` com a
mensagem apontando para este README. É o erro esperado no primeiro dia de
integração, e por isso ele é verboso.

## A base de clientes importada (Sprint 2) — tabela `clientes_importados`

Mora no **mesmo Postgres do Supabase**, ao lado de `empresas`. A CRAI
conecta com a connection string de serviço (`SUPABASE_DB_URL`) e faz upsert
por `(tenant_id, customer_id_externo)`: reimportar atualiza, não duplica.

O DDL é `clientes_importados.SCHEMA_SQL` (em
`crai/churn_voluntary/clientes_importados.py`) e é executado com
`IF NOT EXISTS` na primeira conexão — mas pode ser criado antes pelo painel
(SQL Editor), que é o caminho recomendado para deixar RLS e permissões
explícitas:

```sql
create table if not exists public.clientes_importados (
  tenant_id           text not null,
  customer_id_externo text not null,
  mrr                 double precision not null,
  billing_profile     text not null,           -- CLT | PJ | freelancer
  days_since_last     double precision,        -- NULL = a planilha não tinha
  features_used_30d   double precision,        -- NULL = a planilha não tinha
  email               text,
  importado_em        text not null,           -- ISO-8601 UTC
  primary key (tenant_id, customer_id_externo)
);
-- A CRAI acessa com a role de serviço; o frontend NÃO lê esta tabela direto.
alter table public.clientes_importados enable row level security;
```

`importado_em` é texto ISO-8601 de propósito: é comparado com o
`registrado_em` do log de ciclos do SDK (SQLite) no Sprint 4, e ISO ordena
como texto.

## Variáveis de ambiente

| env                    | obrigatória          | exemplo                                                        |
|------------------------|----------------------|----------------------------------------------------------------|
| `SUPABASE_PROJECT_URL` | sim                  | `https://abcdefgh.supabase.co`                                 |
| `SUPABASE_DB_URL`      | sim (produção)       | `postgresql://postgres.abcdefgh:SENHA@...pooler.supabase.com:6543/postgres?sslmode=require` |
| `CRAI_CLIENTES_DB`     | só teste/dev         | caminho de um SQLite; a suíte usa `tmp_path`                   |

Sem `SUPABASE_DB_URL` nem `CRAI_CLIENTES_DB`, `POST /clientes/importar`
responde 500 `base_nao_configurada`. Se as duas existirem, o Postgres vence.

As chaves públicas são lidas de
`<SUPABASE_PROJECT_URL>/auth/v1/.well-known/jwks.json` e ficam em cache por
**até 10 minutos**. Um token com `kid` desconhecido força uma renovação do
cache antes de ser recusado (cobre rotação de chave entre leituras).

**Sem a env configurada, os endpoints autenticados respondem 500 com motivo
`supabase_nao_configurado`** — nunca deixam passar. Os webhooks (Segment,
Pix, Stripe) não dependem dela e continuam funcionando como antes.

## Segurança

- Só `ES256` e `RS256`. `HS256` é recusado no header antes de qualquer
  verificação: com JWKS a chave é pública, e aceitar HMAC permitiria assinar
  um token com a própria chave pública como segredo.
- `exp` é obrigatória e verificada. `aud` precisa ser `authenticated`.
- O `kid` do header escolhe a chave; token sem `kid` é recusado.

## Uso nos endpoints

```python
from fastapi import Depends
from ..accounts import get_tenant_id, get_conta

@app.get("/insights")
async def insights(tenant_id: str = Depends(get_tenant_id)):
    ...

@app.post("/insights/enviar")
async def enviar(conta: dict = Depends(get_conta)):   # {tenant_id, email, sub}
    ...
```

## As rotas do self-service (contrato estável a partir do Sprint 4)

Todas exigem `Authorization: Bearer <token>`. Os webhooks (`/webhooks/*`)
e os `/simulate/*` NÃO passam por aqui e continuam como sempre.

| rota | o que faz | resposta |
|------|-----------|----------|
| `POST /clientes/importar` | multipart: `arquivo` (.csv/.xlsx) + `mapeamento` (JSON opcional). Upsert por `customer_id_externo`. | `{importados, rejeitados:[{linha,motivo}], colunas_nao_encontradas, linhas_sem_dado_comportamental}` |
| `GET /insights` | ranking upload ∪ SDK, um por cliente, risco decrescente. `?limite=N`, `?criticidade_minima=alto\|critico`. | `{total_clientes, clientes_em_risco:[{customer_id_externo, risk_score, criticality, explicacao, origem, atualizado_em, ...}], gerado_em, filtros}` |
| `POST /insights/enviar` | manda o resumo para o `email` do token. Mesmos filtros. Sem SMTP, simula. | `{enviado, simulado, destinatario, linhas, total_clientes, gerado_em}` (502 se o SMTP falhar) |

Regras que valem nas três: o tenant vem do JWT e só dele (`x-tenant-id`
e campos no corpo são ignorados); `risk_score: null` +
`criticality: "dado_insuficiente"` significa "sem dado de atividade", não
"sem risco"; quando o mesmo cliente está no upload e no SDK, vence o dado
mais recente e `origem` diz qual foi.

## Testando sem frontend

Gere um JWT no painel do Supabase (ou faça login via `supabase-js` no
console do navegador e copie `session.access_token`) e chame:

```
curl -H "Authorization: Bearer <token>" http://localhost:8000/insights
```

A suíte (`tests/test_supabase_auth.py`) não toca no Supabase: ela gera um par
de chaves ES256 na hora e substitui a busca do JWKS por um dicionário local.

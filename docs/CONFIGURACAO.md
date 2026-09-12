# Configuração — variáveis de ambiente

Referência para você montar o seu `.env` local do zero. **Este documento não é
um arquivo de configuração e não deve ser copiado para `.env`.**

O `.env` fica em `app/` e é carregado pelo `crai/api/app.py` (`load_dotenv()`)
quando a API sobe. Nenhuma variável é necessária para rodar a suíte de testes.
Para abrir o painel de avaliação localmente, basta `ENV=development`.

As 22 variáveis abaixo são as que o antigo `app/.env.example` listava, na mesma
ordem, agrupadas por assunto.

**Sobre o `.env.example` que existia.** O arquivo existiu até a migração para o
CraiV3, em 12/09/2026, e continha apenas placeholders: `sk-ant-...`,
`whsec_...`, o host `abcdefgh` e a palavra `SENHA`. Antes de publicar, o
histórico inteiro (todos os commits de todas as refs, `git log -p --all`) foi
varrido com os padrões de chave do hook e com o padrão de string de conexão com
senha. Nenhuma credencial real foi encontrada: os únicos casamentos foram o
placeholder `postgres.abcdefgh:SENHA@`. O arquivo foi removido e o hook
`scripts/pre-commit` passou a bloquear qualquer `.env*` estagiado, sem exceção.

## Ambiente

**`ENV`**
- **O que faz:** com `development` ou `demo`, expõe os endpoints `/simulate/*`,
  dos quais o painel `/painel` depende. Nesses dois modos, se nenhuma base foi
  configurada, a API usa um SQLite local (`app/clientes_dev.db`).
- **Obrigatória:** não.
- **Sem ela:** vale `production`. Os `/simulate/*` respondem 403 ("Endpoints
  /simulate/* exigem ENV=development ou ENV=demo") e o painel abre sem conseguir
  chamar nada.

## IA generativa

**`ANTHROPIC_API_KEY`**
- **O que faz:** chave da API da Anthropic, que começa com `sk-ant-`. É usada para
  gerar as mensagens de cobrança (`dunning_engine.py`) e de retenção
  (`voluntary_agent.py`).
- **Obrigatória:** não, para rodar. Sim, para mensagens geradas por IA.
- **Sem ela:** a chamada falha e as mensagens saem dos templates de fallback do
  próprio código.

## Pagamentos e webhooks de cobrança

**`STRIPE_SECRET_KEY`** — **LEGADO**
- **O que faz:** nada hoje. É herança do desenho antigo baseado em cartão e
  nenhum módulo Python lê esta variável.
- **Obrigatória:** não.
- **Sem ela:** nada muda.

**`STRIPE_WEBHOOK_SECRET`** — **LEGADO**
- **O que faz:** segredo do HMAC-SHA256 do header `stripe-signature` no
  `/webhooks/stripe`. O webhook existe por herança do desenho antigo baseado em
  cartão e não faz parte do caminho ativo, que é Pix Automático via Pagar.me.
- **Obrigatória:** só para esse webhook.
- **Sem ela:** todo request ao `/webhooks/stripe` é rejeitado com 401.

**`PIX_WEBHOOK_SECRET`**
- **O que faz:** segredo do HMAC-SHA256 do header `x-pix-signature` no
  `/webhooks/pix-automatico`, por onde o PSP avisa a falha de cobrança.
- **Obrigatória:** sim, para receber eventos do PSP.
- **Sem ela:** todo request ao webhook é rejeitado com 401.

**`CRAI_ENCRYPTION_KEY`**
- **O que faz:** chave Fernet usada para cifrar a chave Pix do pagador antes de
  arquivá-la para conciliação. Gerar com
  `python -c "from crai.security.tokenization import generate_key; print(generate_key())"`.
- **Obrigatória:** sim, para arquivar a chave Pix.
- **Sem ela:** o arquivamento falha, em vez de gravar em texto puro.

## CRM e eventos comportamentais

**`HUBSPOT_TOKEN`**
- **O que faz:** token de Private App do HubSpot, que começa com `pat-na1-`. Precisa
  das permissões `crm.objects.contacts.write` e `crm.objects.deals.write`, e dos
  pipelines `crai_recovery` e `crai_retention` criados no HubSpot.
- **Obrigatória:** não.
- **Sem ela:** o CRM roda em modo simulação.

**`SEGMENT_WRITE_KEY`**
- **O que faz:** nada hoje. Estava no `.env.example`, mas nenhum módulo Python a lê.
- **Obrigatória:** não.
- **Sem ela:** nada muda.

**`SEGMENT_WEBHOOK_SECRET`**
- **O que faz:** segredo do HMAC-SHA1 do header `x-signature` no `/webhooks/segment`.
- **Obrigatória:** sim, para receber eventos do Segment.
- **Sem ela:** todo request ao webhook é rejeitado com 401. Sem Segment, o
  `/simulate/churn-risk` faz o papel dele em desenvolvimento.

## Churn voluntário

**`CRAI_HIGH_VALUE_MRR_THRESHOLD`**
- **O que faz:** limiar de MRR, em R$ por mês, acima do qual o cliente é tratado
  como CRÍTICO mesmo com risco baixo, e recebe a mensagem de alto cuidado.
- **Obrigatória:** não.
- **Sem ela:** vale R$ 2.000,00. O mesmo vale se o valor for ilegível.

**`CRAI_CS_SIGNATURE_NAME`**
- **O que faz:** nome que assina as mensagens críticas.
- **Obrigatória:** não.
- **Sem ela:** a mensagem sai sem assinatura. O prompt proíbe inventar um nome,
  porque um nome que não existe é uma pessoa que o cliente vai procurar.

**`RETENTION_OUTCOME_WEBHOOK_SECRET`**
- **O que faz:** segredo próprio do HMAC-SHA1 do header `x-signature` no
  `/webhooks/retention-outcome`. Quem envia é o backend do cliente, não o
  Segment. Esse webhook move o aprendizado do bandit de ofertas.
- **Obrigatória:** sim, para receber desfechos reais.
- **Sem ela:** todo request ao webhook é rejeitado com 401.

**`CRAI_SIMULATE_OUTCOMES`**
- **O que faz:** `1` liga o modo simulação, em que o grafo sorteia o aceite da
  oferta (`track_outcome`). Serve para a demo e para os testes.
- **Obrigatória:** não. **Nunca ligar em produção:** o bandit aprenderia com
  dado inventado.
- **Sem ela:** é o modo produção. O grafo termina no envio e o desfecho chega
  pelo webhook acima.

**`CRAI_RETENTION_DB`**
- **O que faz:** caminho alternativo do log de ciclos de retenção, que é o
  dataset de treino do churn voluntário.
- **Obrigatória:** não.
- **Sem ela:** o log fica em `app/data/retention_cycles.db`, que é ignorado
  pelo git.

O tenant **não** é variável de ambiente: chega por requisição, no header
`x-tenant-id` ou no campo `tenant_id` do corpo.

## Self-service: login e base de clientes (Supabase)

**`SUPABASE_PROJECT_URL`**
- **O que faz:** URL do projeto no Supabase. Dela a CRAI baixa as chaves
  públicas que validam o JWT (`<URL>/auth/v1/.well-known/jwks.json`, com cache
  de até 10 minutos).
- **Obrigatória:** sim, para os endpoints autenticados. Os webhooks não
  dependem dela.
- **Sem ela:** os endpoints autenticados respondem 500 `supabase_nao_configurado`.
  Nunca deixam o request passar.

**`SUPABASE_DB_URL`**
- **O que faz:** string de conexão com o Postgres do mesmo projeto Supabase,
  onde mora a tabela `clientes_importados`. No painel do Supabase fica em
  Project Settings → Database → Connection string, modo "session" ou
  "transaction", terminando com `sslmode=require`.
- **Obrigatória:** sim, em produção.
- **Sem ela:** o `POST /clientes/importar` responde 500 `base_nao_configurada`.
  Em `development` ou `demo` a API usa o SQLite local. Se ela e a
  `CRAI_CLIENTES_DB` estiverem definidas, o Postgres vence.

**`CRAI_CLIENTES_DB`**
- **O que faz:** só para teste e desenvolvimento. Caminho de um arquivo SQLite
  usado no lugar do Postgres, com o mesmo SQL. A suíte de testes usa esta
  variável.
- **Obrigatória:** não.
- **Sem ela:** em `development` ou `demo` a API usa `app/clientes_dev.db`; em
  produção, vale o que foi dito para `SUPABASE_DB_URL`.

## Insights por e-mail

O `POST /insights/enviar` manda o resumo do ranking para o e-mail da conta
autenticada. Qualquer provedor com SMTP serve.

**`CRAI_SMTP_HOST`**
- **O que faz:** endereço do servidor SMTP.
- **Obrigatória:** não.
- **Sem ela:** o envio é simulado (vai para o log) e a resposta traz
  `simulado: true`.

**`CRAI_SMTP_PORT`**
- **O que faz:** porta do servidor SMTP.
- **Obrigatória:** não.
- **Sem ela:** usa 587.

**`CRAI_SMTP_USER`**
- **O que faz:** usuário do SMTP.
- **Obrigatória:** depende do provedor.
- **Sem ela:** a conexão é feita sem usuário.

**`CRAI_SMTP_PASSWORD`**
- **O que faz:** senha do SMTP, ou senha de app do provedor.
- **Obrigatória:** depende do provedor.
- **Sem ela:** a conexão é feita sem senha.

**`CRAI_EMAIL_FROM`**
- **O que faz:** endereço de remetente dos e-mails de insights.
- **Obrigatória:** não.
- **Sem ela:** usa `insights@crai.local`.

## Lidas pelo código, mas ausentes do antigo `.env.example`

Estas variáveis não estavam nas 22 acima. O código lê todas; o comportamento
detalhado de cada uma não foi documentado nesta migração, e a referência é o
módulo indicado.

| Variável | Módulo |
|---|---|
| `CRAI_PAGARME_LIVE`, `CRAI_PAGARME_API_KEY`, `CRAI_PAGARME_ENDPOINT` | `crai/integrations/pagarme_gateway.py` (sem `CRAI_PAGARME_LIVE`, o gateway fica simulado) |
| `CRAI_PERFIL_DB` | `crai/agent/perfil_provider.py` (fonte real do perfil do cliente) |
| `CRAI_RECOVERY_DB` | `crai/dunning/recovery_log.py` |
| `CRAI_RETRY_STATE` | `crai/dunning/retry_state.py` |
| `CRAI_SUCCESS_FEE_PCT`, `CRAI_CUSTO_INTERVENCAO_WHATSAPP`, `CRAI_CUSTO_TENTATIVA_PIX` | `crai/config.py` |

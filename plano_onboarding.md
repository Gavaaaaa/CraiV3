# Plano — Onboarding self-service + Insights de churn voluntário

> Objetivo: uma empresa cliente se cadastra no site da CRAI e recebe insights
> (ranking + explicação) de quais clientes dela têm mais chance de churn
> voluntário. Existem DOIS caminhos de dado, complementares, não excludentes:
> (1) integrar o SDK comportamental (o caminho já existente, evento a evento,
> pensado para acumular dado real com o tempo) e (2) anexar uma base que a
> empresa já tem em mãos, para uma primeira análise imediata sem precisar
> instrumentar nada. Este plano cobre o caminho (2) como ADIÇÃO — não toca no
> caminho (1). CRM (HubSpot) fica de lado por enquanto. O frontend já existe
> fora deste repositório — este plano cobre só o backend/dados; a integração
> com a tela vem depois.
>
> Escrito a partir de uma leitura do repositório real (github.com/Gavaaaaa/Crai)
> em 2026-09-08. Segue a mesma convenção do `churn_voluntario_completo.md`:
> um CONTEXTO colado uma vez, depois um sprint por vez com gate.

---

## Diagnóstico — o que já está pronto vs. o que falta

### Já integrado (reaproveitável quase sem mudança)

- **Motor de risco** (`crai/churn_voluntary/risk_scorer.py`): calcula
  `risk_score` a partir de `days_since_last`, `features_used_30d`, `mrr` e
  tipo de evento. Já tem ponto de extensão para modelo treinado
  (`carregar_modelo()` / `voluntary_risk.joblib`) com fallback automático para
  regras fixas — o mesmo padrão `train()`/`load()` dos módulos de ML do
  involuntário.
- **Classificação de criticidade** (`classify_criticality`): já separa
  "crítico" / "alto" / "padrão" cruzando risco com MRR — é praticamente o
  ranking de prioridade que um dashboard de insights precisa mostrar primeiro.
- **Isolamento por tenant** (Sprint 5 do `churn_voluntario_completo.md`, já
  aplicado): o bandit (`offer_bandit.py`) e o histórico de canal já são
  particionados por `tenant_id`. Ou seja, a base técnica de multi-tenant **já
  existe** — falta só a camada de identidade (login/API key) por cima dela.
- **Persistência dos ciclos** (`crai/churn_voluntary/retention_log.py` →
  SQLite `crai/data/retention_cycles.db`): grava uma linha por cliente
  avaliado, com todas as features, o risco calculado, a criticidade e (quando
  chega) o desfecho. Essa tabela é, na prática, a matéria-prima de qualquer
  relatório de insights — só falta uma camada de leitura/API em cima dela.
- **Explicabilidade**: o classificador de falha involuntária já expõe SHAP em
  texto PT-BR; o voluntário tem o equivalente mais simples (`classify_
  criticality` + as próprias features). Dá para gerar frases do tipo "risco
  alto por 47 dias sem login e baixo uso de funcionalidades" sem trabalho
  novo de modelagem.

### Falta construir

1. **Cadastro/autenticação de empresa.** Hoje `tenant_id` é só um campo de
   texto solto no payload do webhook (default `"default_tenant"` se ausente).
   Não existe tabela de empresas, não existe login, não existe API key. É o
   maior buraco entre "o que existe" e "empresas se cadastram no site".
   Decisão já tomada (ver seção "Onde mora a conta da empresa" abaixo):
   quem resolve isso é o Supabase (Auth + Postgres), não um sistema novo
   dentro do CRAI — o CRAI só passa a validar o token que o Supabase emite.
2. **Ingestão de base de clientes em lote.** O sistema hoje só recebe dado
   **um evento comportamental por vez**, via `/webhooks/segment` (Session
   Started, Cancellation Page Viewed, etc.) — pensado para tracking em tempo
   real, não para "eu subo uma planilha com 4.000 clientes". Não existe
   endpoint de upload (CSV/XLSX/JSON) nem tabela de "cadastro de clientes" da
   empresa — só o log de ciclos de retenção, que é outra coisa.
3. **Scoring em lote.** `calculate_risk(event, props)` espera um `event` de
   comportamento pontual. Para pontuar uma base inteira de uma vez (sem
   evento em tempo real) precisa de uma função irmã que rode as mesmas
   features sem exigir `event` — pequeno, mas não existe hoje.
4. **Endpoint/relatório de insights.** Não existe nenhuma rota que devolva
   "ranking de clientes em risco" — hoje dá pra fazer isso só com SQL direto
   no SQLite. Falta a API (e, se quiser, o envio programado por e-mail).
5. **Envio de e-mail/notificação.** Não há nenhuma integração de e-mail no
   projeto (nem SMTP, nem SendGrid/Resend). Necessário se o "manda insights"
   for além do dashboard (ex.: resumo semanal por e-mail).
6. **Ressalva de modelagem, importante:** o `README_treino.md` já documenta
   que o único label disponível hoje é *"aceitou a oferta"*, não *"cancelou
   de verdade"*. Para prever churn voluntário real (não propensão a aceitar
   desconto), value a pena, num sprint futuro, criar um sinal de cancelamento
   de assinatura de verdade — sem isso, o "insight" que a IA manda é uma boa
   aproximação (a mesma lógica de risco por inatividade), mas não é ainda um
   modelo treinado em churn observado.

### Os dois caminhos de dado — SDK e anexo de base, lado a lado

Vale deixar explícito, porque muda o desenho do Sprint 4: nenhum dos dois
caminhos substitui o outro, e o sistema precisa aceitar os dois desde já.

- **Caminho SDK (já existe, não mexe).** A empresa integra o SDK
  comportamental (hoje modelado em cima de Segment) no produto dela. Os
  eventos chegam em tempo real via `/webhooks/segment`, o risco é calculado
  evento a evento, e cada ciclo fica gravado em `retention_cycles.db`. É o
  caminho que acumula dado real com o tempo e que, mais pra frente, alimenta
  o treino de um modelo de churn de verdade (ver a ressalva do
  `README_treino.md` mais abaixo). Nenhum sprint deste plano toca nesse
  fluxo.
- **Caminho anexo de base (o que este plano adiciona).** Nem toda empresa
  tem esse comportamento instrumentado à parte — muitas têm só uma
  exportação/planilha com o que já sabem de cada cliente (MRR, perfil,
  última atividade conhecida). Para essas, o cadastro + upload (Sprints 1-3)
  dá uma primeira análise imediata, sem exigir integração nenhuma. É mais
  rápido de entregar valor, mas é uma fotografia estática — não aprende
  sozinho com o tempo do jeito que o caminho SDK aprende (bandit, dataset de
  ciclos).
- **O motor de risco é o mesmo para os dois.** `risco_por_features` (Sprint
  3) é extraído de `calculate_risk` sem duplicar lógica — os dois caminhos
  batem na mesma regra/modelo, só muda de onde vêm os números
  (`clientes_importados` num caso, o payload do evento no outro).
- **Consequência para o Sprint 4 (insights):** uma empresa pode ter clientes
  vindos só do upload, só do SDK, ou dos dois ao mesmo tempo (ex.: subiu a
  base para começar e depois integrou o SDK). O endpoint `/insights`
  precisa combinar as duas fontes numa lista só, por `customer_id`: quando o
  mesmo cliente aparece nas duas (upload E eventos do SDK), prevalece o dado
  mais recente — na prática, quase sempre o do SDK, porque ele se atualiza a
  cada evento e o upload é uma foto parada no tempo do cadastro.

### Onde mora a conta da empresa — Supabase, custo zero agora

Decisão confirmada em conversa: quem guarda empresa/login/base de clientes é
o **Supabase** (Auth + Postgres), não uma tabela nova dentro do CRAI. O CRAI
vira um serviço de IA por trás, chamado depois que a empresa já está
autenticada — ele não emite senha, não guarda conta, só confere um token.

Dois pontos práticos que motivaram deixar isso escrito aqui:

- **Custo: zero agora, plano pago só se/quando vier investimento.** O plano
  Free do Supabase já inclui Auth completo + 500 MB de banco + 1 GB de
  arquivo — mais do que suficiente para o volume de uma apresentação de TCC
  ou de um piloto com poucas empresas. Não é preciso decidir ou pagar plano
  pago agora. A parte boa: o código que valida o token e lê do Postgres é
  **exatamente o mesmo** no Free e no Pro — trocar de plano depois é um
  clique no painel do Supabase, não uma reescrita. É isso que "deixar pronto
  para a troca futura" significa na prática: nada a fazer hoje além de criar
  o projeto Free.
- **Cuidado operacional para o dia da banca:** o plano Free pausa o projeto
  depois de **7 dias sem atividade** (poucas consultas por dia já bastam
  pra evitar isso). Não perde dado — fica salvo por até 1 ano — mas o
  projeto para de responder até alguém entrar no painel e clicar em
  "Resume project". Se o sistema ficar parado por mais de uma semana antes
  da apresentação, é só lembrar de reativar antes de apresentar (leva
  segundos); plano pago não pausa nunca, mas isso só importa quando vocês
  migrarem.

### Veredito de viabilidade

É viável, e o caminho é mais curto do que parece: a parte difícil (motor de
risco, isolamento por tenant, explicabilidade, padrão de modelo plugável) já
está pronta. O que falta é, essencialmente, engenharia de produto direto —
cadastro de empresa, upload de planilha, um scoring em lote e uma rota de
leitura — não pesquisa de ML nova. Dá para fazer em sprints pequenos, do
jeito que os sprints do churn voluntário já foram feitos, sem tocar no
involuntário nem no que já funciona.

---

## Como usar este plano com o Claude Code

Mesmo fluxo que você já usa: abra o Claude Code CLI na raiz do repo, cole o
bloco CONTEXTO uma vez, depois mande um sprint por vez, rodando o gate antes
de avançar para o próximo. Se algo do involuntário ou do voluntário existente
quebrar, pare e revise antes de continuar.

### CONTEXTO (colar uma vez)

```
Você está no repositório da CRAI (github.com/Gavaaaaa/crai). Esta sessão
trata de um novo fluxo self-service: empresas clientes se cadastram e
recebem insights de risco de churn voluntário. Existem DOIS caminhos de
dado, e os dois continuam válidos ao final deste plano — não é para
escolher um: (1) o SDK comportamental já existente (evento a evento, via
/webhooks/segment) para quem tem esse tracking separado, e (2) o anexo de
uma base que a empresa já tem em mãos (upload em lote), para quem não tem.
Esta sessão ADICIONA o caminho (2); NÃO reduz, substitui nem desativa o
caminho (1). O CRM (HubSpot) fica de fora por enquanto — não maximizar isso
agora. O agente de churn involuntário (crai/crai/agent/, crai/crai/dunning/)
e o pipeline voluntário orientado a evento (crai/crai/churn_voluntary/) já
existem e NÃO podem mudar de comportamento observável.

DECISÃO DE ARQUITETURA JÁ TOMADA (não reabrir): quem guarda empresa, login e
a base de clientes importada é o **Supabase** (Auth + Postgres, plano Free
por enquanto, sem custo — upgrade para pago é só troca de plano, não de
código, quando/se vier investimento). O CRAI NÃO tem tabela de empresa nem
sistema de senha/API key próprio — ele só VALIDA o token (JWT) que o
Supabase já emitiu no login, extrai o `tenant_id` de dentro dele, e usa
esse `tenant_id` exatamente como o resto do pipeline (bandit, risk_scorer)
já espera.

ESTRUTURA REAL (confirmada):
- Pacote: crai/crai/
- Risco voluntário: crai/crai/churn_voluntary/risk_scorer.py
  (calculate_risk(event, props) — regras fixas + modelo plugável opcional)
- Bandit/tenant: crai/crai/churn_voluntary/offer_bandit.py
  (já particionado por tenant_id desde o Sprint 5)
- Log de ciclos: crai/crai/churn_voluntary/retention_log.py →
  SQLite crai/data/retention_cycles.db (fora do git) — fica como está, é do
  caminho SDK e este plano não mexe nele
- API: crai/crai/api/app.py (FastAPI) — hoje só webhooks orientados a evento
  e endpoints /simulate/*
- Testes: crai/crai/tests/

INVARIANTE: nada que já existe pode mudar de comportamento. Este plano
ADICIONA módulos novos (validação de JWT, ingestão em lote, insights) e só
toca em código existente onde for estritamente necessário (ex.: reaproveitar
calculate_risk).

REGRA DE OURO: ao fim de cada sprint, rodar o gate + a suíte inteira
(`pytest crai/tests/ -q`) e mostrar o resultado. Se algo antigo quebrar,
parar e avisar.

Confirme que entendeu o estado real do código antes de eu mandar o Sprint 1.
```

---

### SPRINT 1 — Validar o JWT do Supabase e resolver o tenant_id

```
SPRINT 1 — Autenticação via token do Supabase (sem tabela de conta no CRAI)

CONTEXTO: o cadastro/login da empresa acontece INTEIRAMENTE no Supabase Auth,
do lado do frontend — o CRAI nunca vê e-mail nem senha. O frontend manda o
token (JWT) que o Supabase emitiu no login em toda chamada ao CRAI, no header
`Authorization: Bearer <token>`. Este sprint cria só a validação desse token.

IMPLEMENTAR:

1. Criar crai/crai/accounts/ (pacote novo, só auth — sem tabela de empresa):
   - supabase_auth.py:
     * carregar as chaves públicas do Supabase uma vez (JWKS):
       GET https://<project-id>.supabase.co/auth/v1/.well-known/jwks.json
       — cachear por no máximo 10 minutos (o Supabase pode rotacionar as
       chaves; ver docs.supabase.com/guides/auth/jwts).
     * validar_token(token: str) -> dict: usa PyJWT (adicionar ao
       requirements.txt) para conferir assinatura contra o JWKS + expiração
       (`exp`). Levanta exceção clara se inválido/expirado. Devolve os
       claims decodificados (contém `sub`, `role`, `exp`, e o `tenant_id`
       — ver item 2).
   - auth.py: dependency do FastAPI (`get_tenant_id`) que lê o header
     `Authorization`, chama validar_token, extrai `tenant_id` dos claims, e
     levanta 401 se o header faltar, o token for inválido/expirado, ou o
     claim `tenant_id` não existir.

2. Como o `tenant_id` chega no token: configurar no painel do Supabase um
   "Custom Access Token Hook" (função Postgres que roda no login e adiciona
   `tenant_id` como claim customizada do JWT, a partir de uma tabela
   `empresas` que mora NO PRÓPRIO Supabase, fora deste repositório). Este
   sprint só documenta esse contrato em accounts/README.md (qual claim
   esperar, exemplo de payload decodificado) — a função Postgres em si é
   configurada direto no painel/projeto Supabase, não é código Python.

3. Variável de ambiente nova: `SUPABASE_PROJECT_URL` (de onde monta a URL do
   JWKS). Sem ela configurada, os endpoints que dependem de
   `get_tenant_id` devem falhar alto (500 claro na inicialização ou no
   primeiro uso), nunca aceitar requisição sem validar de verdade.

4. Não mexer em nenhum endpoint de webhook existente neste sprint — eles
   continuam aceitando tenant_id do jeito que já aceitam (Segment não passa
   por este novo dependency).

GATE:
    cd crai && pytest tests/test_supabase_auth.py -v   (criar, com JWKS
    mockado — não bater no Supabase de verdade no teste)
    cd crai && pytest tests/ -q
test_supabase_auth: token assinado com a chave certa e `tenant_id` no claim
-> get_tenant_id resolve certo; token sem `Authorization` -> 401; token
expirado -> 401; token assinado com chave errada -> 401; token válido mas
sem claim `tenant_id` -> 401 com mensagem clara. Mostre os resultados.
```

---

### SPRINT 2 — Ingestão de base de clientes em lote

```
SPRINT 2 — Upload de clientes por empresa

IMPLEMENTAR:

1. Tabela `clientes_importados` **no Postgres do Supabase** (não em SQLite
   novo — é o mesmo banco que já guarda `empresas`, criado via painel/
   migration do Supabase, fora deste repositório): tenant_id,
   customer_id_externo, mrr, billing_profile, days_since_last,
   features_used_30d, email (opcional, só se for usar para o insight por
   e-mail depois), importado_em. Chave (tenant_id, customer_id_externo) —
   reimportar atualiza a linha, não duplica. O CRAI conecta nesse Postgres
   com uma connection string de serviço (`SUPABASE_DB_URL`, nova variável de
   ambiente) — via SQLAlchemy ou `psycopg2` direto, o que já estiver mais
   perto do padrão do resto do pacote.

2. app.py: POST /clientes/importar (autenticado via o `get_tenant_id` do
   Sprint 1 — header `Authorization: Bearer <token>`, não mais uma api_key
   própria do CRAI), recebendo o ARQUIVO de verdade como upload
   (multipart/form-data, `UploadFile` do FastAPI) — é isso que faz a aba
   "anexar arquivo" do frontend funcionar sem o front ter que converter nada
   antes. Aceitar `.csv` e `.xlsx` (usar `pandas.read_csv`/`read_excel` a
   partir dos bytes recebidos; XLSX exige `openpyxl` — adicionar ao
   requirements.txt se não estiver lá).

3. Colunas esperadas no arquivo (nome exato ou mapeadas — ver item 4):
   `customer_id_externo` (obrigatório), `mrr` (obrigatório),
   `billing_profile` (obrigatório), `days_since_last`, `features_used_30d`,
   `email` (opcional). Os dois últimos são as features de COMPORTAMENTO
   que o motor de risco usa (ver Sprint 3) — se a planilha do cliente não
   tiver isso, dizer isso já na resposta da importação (ver item 5), não só
   silenciar depois na hora de pontuar.

4. Mapeamento de colunas: nem toda empresa vai chamar a coluna exatamente
   de `days_since_last`. Aceitar um parâmetro opcional `mapeamento` (JSON
   como string, junto do multipart) do tipo
   `{"Última atividade": "days_since_last", "MRR (R$)": "mrr"}` — se
   ausente, tentar casar por nome exato (case-insensitive) e reportar quais
   colunas esperadas não foram encontradas.

5. Validar campos obrigatórios por linha e devolver, na resposta:
   `{ importados, rejeitados: [ {linha, motivo} ], colunas_nao_encontradas,
   linhas_sem_dado_comportamental }` — `linhas_sem_dado_comportamental`
   conta quantas linhas foram importadas SEM `days_since_last` e/ou
   `features_used_30d` (isso não impede a importação da linha, mas precisa
   aparecer aqui porque afeta o Sprint 3 — ver o aviso lá). Uma linha
   inválida não derruba o resto do lote.

GATE:
    cd crai && pytest tests/test_importacao_clientes.py -v   (criar)
test_importacao_clientes: sobe um CSV e um XLSX de teste, os dois importam
igual; reimportação do mesmo customer_id_externo atualiza em vez de
duplicar; linha inválida é reportada e não derruba o resto do lote;
mapeamento de coluna com nome diferente funciona; arquivo sem uma coluna
obrigatória aparece em `colunas_nao_encontradas`. Mostre os resultados.
```

---

### SPRINT 3 — Scoring em lote (reaproveitando o motor de risco)

```
SPRINT 3 — Pontuar toda a base importada de uma empresa

ATENÇÃO — ARMADILHA DE DADO FALTANTE: hoje, em `_risco_por_regras`, os
defaults quando o evento é "Session Started" são
`props.get("days_since_last", 0)` e `props.get("features_used_30d", 10)`.
Isso é seguro no caminho SDK (o Segment sempre manda esses dois números
juntos, o default nunca é exercido de verdade). No upload em lote NÃO é
seguro: se a planilha do cliente não tiver essas colunas, os dois defaults
juntos (`days=0`, `features=10`) produzem risco **0.0** — ou seja, todo
cliente sem dado comportamental aparece como "sem risco nenhum", que é
exatamente o oposto de "não sei avaliar esse cliente". Este sprint tem que
evitar isso explicitamente (ver item 2).

IMPLEMENTAR:

1. risk_scorer.py: extrair de calculate_risk uma função
       def risco_por_features(days_since_last, features_used_30d, mrr,
                               event=None) -> float
   que roda a MESMA lógica (modelo plugável se existir, senão regras fixas)
   sem exigir um `event` de comportamento pontual — para o caso de dado
   estático de cadastro, tratar como o caso "Session Started" (o único que
   hoje usa days_since_last/features_used_30d nas regras fixas).
   calculate_risk(event, props) passa a chamar essa função por dentro —
   NÃO duplicar a lógica, e comportamento observável de calculate_risk
   continua idêntico ao de hoje (mesmo teste de regressão do Sprint 6
   precisa continuar verde).

2. Criar crai/crai/churn_voluntary/batch_scoring.py:
       def pontuar_base(tenant_id) -> list[dict]
   lê `clientes_importados` do tenant. Para cada linha, ANTES de chamar
   risco_por_features: se `days_since_last` E `features_used_30d` estiverem
   ambos ausentes (NULL) — ou seja, a linha veio de uma planilha que não
   tinha essas colunas —, NÃO calcular risco nenhum; marcar a linha com
   `criticality: "dado_insuficiente"` e `risk_score: None`, com o texto
   "sem dado de atividade — impossível avaliar risco de churn para este
   cliente" (usar MRR/perfil só para o cadastro aparecer na lista, não para
   inventar um risco). Só quando pelo menos um dos dois vier preenchido,
   chamar risco_por_features + classify_criticality normalmente. Devolver
   lista ordenada por risco decrescente (dado_insuficiente sempre por
   último, não misturado com risco 0 de verdade) com: customer_id_externo,
   risk_score, criticality, e um texto curto de explicação (ex.: "sem login
   há {N} dias, usa {M} funcionalidades, MRR R$X").

GATE:
    cd crai && pytest tests/test_risk_pluggable.py -v   # não quebrou
    cd crai && pytest tests/test_batch_scoring.py -v   (criar)
test_batch_scoring: pontua uma base de teste e devolve ordenado por risco;
cliente sem days_since_last NEM features_used_30d vira "dado_insuficiente"
com risk_score None, nunca 0.0 silencioso; mesmo tenant nunca vê dado de
outro tenant. Mostre os resultados.
```

---

### SPRINT 4 — Endpoint de insights (unificando upload + SDK)

```
SPRINT 4 — Rota que devolve os insights para o frontend consumir

CONTEXTO ADICIONAL DESTE SPRINT: uma empresa pode ter clientes vindos do
upload em lote (Sprint 2/3), do SDK comportamental já existente (eventos em
retention_cycles.db), ou dos dois. O endpoint precisa combinar as duas
fontes numa lista só — não é para escolher uma.

IMPLEMENTAR:

1. Criar crai/crai/churn_voluntary/insights_unificados.py:
       def clientes_em_risco(tenant_id) -> list[dict]
   que junta:
   (a) batch_scoring.pontuar_base(tenant_id) — origem "upload";
   (b) por customer_id, o risk_score/criticality mais recente em
       retention_cycles.db para aquele tenant_id (origem "sdk") — reaproveitar
       a leitura já usada pelo README_treino.md (`sqlite3`/`pandas`), sem
       duplicar o schema.
   Ao juntar por customer_id: se o cliente aparece nas duas origens, vence a
   linha mais recente por registrado_em/importado_em (normalmente a do SDK,
   por se atualizar a cada evento); manter um campo `origem: "upload"|"sdk"`
   na saída para o frontend poder mostrar isso se quiser.

2. app.py: GET /insights (autenticado via o `get_tenant_id` do Sprint 1)
   chamando
   insights_unificados.clientes_em_risco(tenant_id) e devolvendo JSON:
   { total_clientes, clientes_em_risco: [...], gerado_em }.
   Parâmetro opcional ?limite=N e ?criticidade_minima=alto|critico para o
   frontend filtrar.

3. (Opcional, só se quiser o "manda insight" além do dashboard) criar
   crai/crai/integrations/email_sender.py com um envio simples (SMTP ou
   provedor à sua escolha — decidir depois, este sprint só cria a interface
   send_insights_email(empresa, clientes_em_risco)) e um endpoint
   POST /insights/enviar que dispara o e-mail para o e-mail cadastrado da
   empresa. Sem credenciais de e-mail configuradas, deve simular (logar) em
   vez de falhar — mesmo padrão do resto do projeto (funciona sem API key,
   com fallback).

GATE:
    cd crai && pytest tests/test_insights_unificados.py -v   (criar)
    cd crai && pytest tests/test_insights_endpoint.py -v   (criar)
    cd crai && pytest tests/ -q      # suíte inteira, nada quebrou
test_insights_unificados: cliente só no upload aparece com origem "upload";
cliente só em eventos do SDK aparece com origem "sdk"; cliente presente nos
dois aparece UMA vez, com o dado mais recente vencendo. test_insights_endpoint:
sem auth -> 401; com auth -> lista ordenada e filtrável; tenant A nunca
recebe insight de tenant B. Mostre os resultados e confirme que involuntário
e voluntário (evento a evento) seguem intactos.
```

---

### Sprint futuro (não fazer agora) — churn real em vez de aceitação de oferta

Quando quiser um modelo de fato treinado (e não só as regras fixas de
inatividade), o `README_treino.md` já deixa claro o que falta: um sinal real
de cancelamento de assinatura, observado numa janela fixa (30/60/90 dias).
Hoje ninguém no sistema produz esse sinal — nem o Segment, nem o webhook de
desfecho, nem o HubSpot. Isso é trabalho de produto (decidir de onde vem o
"cancelou de verdade"), não só de engenharia, e vale tratar como uma frente
separada depois que o self-service estiver rodando e a base real de clientes
começar a se acumular na tabela `clientes_importados`.

---

## Integração com o frontend (depois)

O plano acima cobre só a API. Quando for plugar no frontend que já existe:
o cadastro/login em si acontece direto contra o Supabase (SDK
`@supabase/supabase-js` no frontend, sem passar pelo CRAI). O contrato dos
endpoints do CRAI (`POST /clientes/importar`, `GET /insights`) fica estável
a partir do Sprint 4 — o frontend consome via `fetch`/`axios`, mandando o
token de sessão que o próprio Supabase já devolve no login, no header
`Authorization: Bearer <token>`. Não precisa esperar o repositório do
frontend estar no GitHub para começar; a API pode ser testada via Swagger
(`/docs`) ou `curl` (com um JWT de teste gerado no painel do Supabase)
enquanto o
frontend não está integrado.

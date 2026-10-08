# Mapeamento de dados pessoais — CRAI

Levantado em 12/09/2026 **lendo o código**: DDLs (`recovery_log.py`,
`retention_log.py`, `clientes_importados.py`), modelos Pydantic e estado dos
grafos (`api/app.py`, `agent/workflow.py`, `churn_voluntary/state.py`), as
escritas em arquivo (`retry_state.py`, `offer_bandit.py`,
`payment_gateway.py::store_encrypted_pix_key`, `failure_classifier.py::_save_audit_log`)
e o cache de JWKS (`accounts/supabase_auth.py`). Onde o código não permite
determinar, está escrito **NÃO VERIFICADO**. Nada abaixo foi preenchido por
suposição.

**Papéis.** A CRAI trata dados de clientes finais **em nome da empresa
contratante (tenant)**: a empresa é a controladora, a CRAI é operadora
(LGPD art. 5º, VI e VII). A base legal listada é a que a **controladora**
pode invocar; a CRAI não a escolhe. Os dados do **usuário da empresa** que
faz login (e-mail do token do Supabase) são tratados pela CRAI como
controladora, para prestar o serviço.

**Legenda.** Pessoal: identifica ou pode identificar uma pessoa natural
(`customer_id_externo`, `user_id` e `customer_id` são pseudônimos: o tenant
sabe quem é, logo continuam pessoais, art. 12 §2º; para cliente pessoa
jurídica, não são dado pessoal — **NÃO VERIFICADO** qual é o caso em cada
tenant). Sensível: categorias do art. 5º, II (nenhum campo abaixo cai nelas).
Base legal proposta: **contrato** = execução de contrato (art. 7º, V);
**legítimo interesse** = art. 7º, IX; **obrigação legal** = art. 7º, II.
Prazo: proposta; **não existe política de retenção nem rotina de expurgo no
código hoje** (nenhum `DELETE` por idade em nenhum módulo).

Terceiros considerados: Pagar.me, HubSpot, Segment, BSP do WhatsApp,
Anthropic (Claude API), Supabase. Coluna "vai para": só o que o código envia
de fato; "—" = não sai da CRAI.

---

## 1. `clientes_importados` — base que a empresa sobe

Onde: Postgres do Supabase (`SUPABASE_DB_URL`) em produção; SQLite
(`CRAI_CLIENTES_DB`) em teste. Origem: `POST /clientes/importar`.

| tabela · campo | pessoal? | sensível? | base legal (controladora) | prazo proposto | vai para |
|---|---|---|---|---|---|
| `clientes_importados.tenant_id` | não (empresa) | não | — | vida do contrato | Supabase |
| `clientes_importados.customer_id_externo` | sim (pseudônimo) | não | contrato | vida do contrato + 30 dias | Supabase; SMTP (e-mail da própria empresa, `/insights/enviar`) |
| `clientes_importados.mrr` | sim (dado financeiro do cliente, se PF) | não | contrato | idem | Supabase |
| `clientes_importados.billing_profile` (CLT/PJ/freelancer) | sim (vínculo de trabalho, se PF) | não (art. 5º, II não inclui) | legítimo interesse | idem | Supabase |
| `clientes_importados.days_since_last` | sim (comportamento) | não | legítimo interesse | idem | Supabase |
| `clientes_importados.features_used_30d` | sim (comportamento) | não | legítimo interesse | idem | Supabase |
| `clientes_importados.email` | **sim (direto)** | não | contrato | idem | Supabase; **não é enviado a ninguém hoje** (reservado ao "insight por e-mail" futuro) |
| `clientes_importados.importado_em` | não | não | — | idem | Supabase |
| `clientes_importados.id_recorrencia` (Etapa 2) | sim (pseudônimo: o id da autorização do Pix Automático) | não | contrato (a empresa informa, como controladora, para ligar a cobrança ao cadastro — atualização da OP-01, sem campo novo sobre a pessoa além do vínculo) | idem | Supabase |
| `clientes_importados.logins_7d`, `logins_30d`, `avg_session_min`, `api_calls_7d`, `tickets_30d`, `failed_pay_90d`, `nps_last`, `seats`, `tenure_days` (Etapa 2) | sim (comportamento e satisfação) | não | legítimo interesse | idem | Supabase; `tenure_days` vai à Anthropic **só em faixa** ("de 1 a 3 anos") e só se a empresa o mandou |
| `clientes_importados.telefone` (Etapa 2) | **sim (direto)** | não | contrato (contato de cobrança — OP-01) | idem | Supabase; **não é copiado** para ciclo, tentativa ou mensagem: lido na hora do envio para escolher o canal; o envio é simulado nesta etapa (nenhum BSP recebe) |
| `clientes_importados.nome` (Etapa 2) | **sim (direto)** | não | contrato (OP-01) | idem | Supabase; a tela da empresa mostra o nome (lido na hora, `cliente_nome`); **só o primeiro nome** vai à Anthropic e entra no texto da mensagem |

## 1b. O ciclo de cobrança do involuntário e as mensagens (Etapas 1 e 2)

Onde: `app/data/recovery_cycles.db` (SQLite local, fora do git; env
`CRAI_RECOVERY_DB`). Origem: `/webhooks/pix-automatico` e o relógio do serviço.
A Etapa 1 criou `ciclos_cobranca`, `tentativas_cobranca` e `eventos_vistos` sem
dado pessoal além de pseudônimos (há teste que abre o banco cru e procura); a
Etapa 2 acrescenta `mensagens_ciclo` e `configuracao_tenant`.

| tabela · campo | pessoal? | sensível? | base legal | prazo proposto | vai para |
|---|---|---|---|---|---|
| `ciclos_cobranca.id_recorrencia`, `id_cobranca_original`, `e2e_falha_original` | sim (pseudônimos da transação) | não | contrato | 24 meses, depois anonimizar (configurado; execução na Etapa 3) | — |
| `ciclos_cobranca.valor`, `fee`, estado e datas | sim (financeiro) | não | contrato | idem | — (a fee nunca sai nas rotas) |
| `tentativas_cobranca.*` | sim (financeiro, pseudônimo) | não | contrato | idem | Pagar.me (`amount`, simulado) |
| `mensagens_ciclo.texto` (Etapa 2) | **sim (texto dirigido à pessoa; no máximo o primeiro nome)** | não | contrato (OP-01: cobrança) | **90 dias depois do desfecho do ciclo**, depois o texto é apagado e fica só a abordagem (expurgo diário, Etapa 2 Bloco 4) | BSP do canal quando o envio for real (hoje simulado); **nunca** entra no dataset de treino nem na trilha do Art. 20 |
| `mensagens_ciclo.abordagem`, `rodada`, `recomendada`, `escolhida`, `escolhida_por` (papel: owner/admin/prazo/automático, nunca nome), `por_prazo`, datas | fraco (decisão sobre a mensagem) | não | contrato | mesma do ciclo | trilha do Art. 20 (abordagem e papel, nunca o texto) |
| `mensagens_ciclo.canal`, `motivo_canal` (Etapa 2) | fraco (qual canal, nunca o contato) | não | contrato | mesma do ciclo | — |
| `configuracao_tenant.*` | não (configuração da empresa) | não | contrato | vida do contrato | — |

**Canal por cliente (Etapa 2).** O canal da mensagem sai dos contatos da base
(`telefone` → WhatsApp, `email` → e-mail, na ordem que a empresa configurou),
lidos na hora do envio. Nenhum contato é copiado para as tabelas acima. Sem
contato elegível, o canal é `sem_canal` e a mensagem não é entregue.

**Janela de contato.** Nenhuma mensagem sai fora da janela configurada pela
empresa (padrão 8 h às 20 h).

**Registro de acesso (Etapa 2, Bloco 4).** Tabela `acessos_titular`, no mesmo arquivo
(`recovery_cycles.db`). Uma linha por leitura das rotas que devolvem dado de titular:
`GET /titular/explicacao/{sujeito_id}`, `GET /ciclos/{ciclo_id}` e `GET /ciclos` (a lista
lê o `cliente_nome` da base). É o que permite responder quem acessou dados de titular num
período (art. 37).

| tabela · campo | pessoal? | sensível? | base legal | prazo | vai para |
|---|---|---|---|---|---|
| `acessos_titular.tenant_id` | não (a empresa) | não | obrigação legal (art. 37) e legítimo interesse (segurança) | **12 meses**, apagado pelo expurgo diário do relógio | — |
| `acessos_titular.rota` | não: é o **modelo** da rota (`GET /ciclos/{ciclo_id}`), nunca o id pedido nem o identificador do titular | não | idem | idem | — |
| `acessos_titular.papel` | fraco (owner, admin, membro ou vazio; **nunca** o e-mail nem o `sub` de quem leu) | não | idem | idem | — |
| `acessos_titular.quando` | não | não | idem | idem | — |

O registro não guarda quem é a pessoa logada nem qual titular foi lido, de propósito: ele
não pode virar um banco de dado pessoal. Há teste que abre a tabela crua e procura
identificador, e-mail e nome.

Desde a Rodada 2 a mesma tabela registra também a **gestão das chaves de API**
(`GET /integracao/chaves`, `POST /integracao/chaves` e
`DELETE /integracao/chaves/{chave_id}`), com as mesmas quatro colunas e o mesmo prazo: é a
operação que abre a base de clientes da empresa a um sistema de fora. Só a operação
atendida é registrada; a `rota` é o modelo, nunca o id da chave. O **uso** da chave nas
rotas de clientes não entra aqui (ver a seção 1c).

**Expurgo (Etapa 2, Bloco 4).** Uma vez por dia o relógio do serviço apaga o `texto` de
`mensagens_ciclo` dos ciclos com desfecho há mais de `retencao_mensagens_dias` (90 por
padrão; configurável por empresa) e os `acessos_titular` com mais de 12 meses. A quantidade
de linhas tocadas vai para o log (`[EXPURGO]`). A retenção da trilha do Art. 20 e a dos
ciclos ainda não são executadas (pendência 1, na seção 12).

**Token de desenvolvimento (Etapa 2, Bloco 4).** Com `ENV=development`, `POST /dev/token`
emite um token de uma empresa fictícia (`demo_dashboard`, ou `demo_testes` para os testes ao
vivo do dashboard). Ele não carrega dado de pessoa
(sem e-mail, `sub` genérico) e não é gravado. Fora de `development` não existe.

## 1c. As chaves de API da empresa (Rodada 2)

Tabelas `chaves_api` e `chaves_api_uso`, no arquivo do ciclo de cobrança
(`recovery_cycles.db`, env `CRAI_RECOVERY_DB`). A empresa gera a chave no dashboard e a
coloca no sistema dela, que passa a chamar as quatro rotas da API de clientes sem ninguém
logado (`docs/CONTRATO_CLIENTES_API.md`, seção 0.1b). Código: `accounts/chaves_api.py` e
`api/integracao.py`.

| tabela · campo | pessoal? | sensível? | base legal | prazo | vai para |
|---|---|---|---|---|---|
| `chaves_api.id` | não (identificador aleatório da chave, `chv_...`) | não | execução de contrato | vida da conta (sem expurgo: pendência 7) | — |
| `chaves_api.tenant_id` | não (a empresa) | não | idem | idem | — |
| `chaves_api.nome` | **pode ser**: é texto livre de até 60 caracteres, escolhido por quem gera a chave (o esperado é o nome de um sistema, mas nada impede o nome de uma pessoa) | não | idem | idem | — |
| `chaves_api.prefixo`, `chaves_api.final` | não: `crai_live_` mais 4 caracteres, e os 4 últimos. Servem para a empresa reconhecer a chave na lista | não | idem | idem | — |
| `chaves_api.hash` | não: SHA-256 da chave. **A chave em si não é gravada em lugar nenhum**; ela só existe na resposta da criação | não | idem | idem | — |
| `chaves_api.criada_em`, `ultimo_uso_em`, `revogada_em` | não | não | idem | idem | — |
| `chaves_api.criada_por_papel` | fraco (owner ou admin; **nunca** o nome, o e-mail nem o `sub` de quem criou) | não | idem | idem | — |
| `chaves_api_uso.chave_id`, `dia`, `total` | não: quantas requisições a chave autenticou em cada dia | não | legítimo interesse (segurança: perceber uso anormal) | idem | — |

O que o uso da chave **não** grava: o corpo da requisição, o caminho chamado, o
identificador do cliente, o IP e o cabeçalho. Há teste que abre as duas tabelas cruas depois
de um uso e procura o conteúdo do corpo. A chave nunca vai para o log: o módulo loga o `id`
da chave e a empresa (há teste que captura o log e a saída padrão e procura a chave).

Quem tem a chave altera a base de clientes da empresa (seção 1) pelas quatro rotas, e
recebe na resposta o cliente gravado, inclusive os campos de contato (`email`, `telefone`,
`nome`). A guarda da chave é da empresa; a revogação vale na requisição seguinte.

O contador de limite por minuto (id da chave e instantes dos usos no último minuto) vive
só na memória do processo.

## 1d. O valor mantido do voluntário e a origem da base (Rodada 3)

Tabelas `retencoes_mantidas` e `base_atualizacoes`, no arquivo do ciclo de cobrança
(`recovery_cycles.db`, env `CRAI_RECOVERY_DB`). Código: `churn_voluntary/mantido.py` e
`churn_voluntary/origem_da_base.py`.

| tabela · campo | pessoal? | sensível? | base legal | prazo | vai para |
|---|---|---|---|---|---|
| `retencoes_mantidas.cliente_id` | **pseudônimo** (o `customer_id_externo` que a própria empresa usa) | não | execução de contrato (é a base da fatura) | proposta: o dos ciclos, 24 meses (sem expurgo: pendência 8) | a tela da empresa (extrato) |
| `retencoes_mantidas.offer_type`, `channel` | fraco (o que foi oferecido a esse cliente, e por onde) | não | idem | idem | idem |
| `retencoes_mantidas.mrr`, `desconto`, `valor_base`, `fee` | fraco (valor do contrato do cliente final com a empresa) | não | idem | idem | a fee só sai em `GET /extrato` (dono e administrador) |
| `retencoes_mantidas.aceito_em`, `cancelamento_em`, `cancelamento_no_prazo`, `valor_estornado`, `fee_estornada` | fraco (quando aceitou, quando cancelou) | não | idem | idem | idem |
| `base_atualizacoes.tenant_id`, `origem`, `atualizada_em` | não (a empresa; `api` ou `anexo`; a hora) | não | execução de contrato | vida da conta | a tela da empresa |

`retencoes_mantidas` **não** guarda nome, e-mail nem telefone. O nome que a tela mostra ao
lado da linha é lido da base importada na hora da resposta, como na lista de ciclos.

**As rotas de leitura do dashboard (Rodada 3)** não criam dado novo: leem as tabelas acima e
as das seções 1, 1b, 2 e 3, sempre filtradas pelo tenant do token. Nenhuma devolve telefone,
e-mail, CPF ou chave Pix; do cliente final sai só o nome (o mesmo da lista de ciclos) ou o
id que a própria empresa usa. Há teste em cada rota, com um cliente que tem e-mail e
telefone na base. `GET /clientes/recentes`, `GET /atividade` e `GET /extrato` entram no
registro de acesso (seção 1b), porque mostram o nome do cliente final; o extrato, além
disso, é a única rota que devolve a fee, e exige dono ou administrador.

## 1e. A simulação do gateway (Rodada 3): dado fictício, em arquivos à parte

Arquivos `*.simulacao.<empresa>.*`, ao lado dos reais (um conjunto por empresa). Código:
`crai/ambiente.py`, `crai/simulador.py`, `api/simulacao.py`.

| tabela · campo | pessoal? | sensível? | base legal | prazo | vai para |
|---|---|---|---|---|---|
| `simulacao_pagadores.nome`, `mensalidade`, `perfil` | **não** (nome inventado; o formulário recusa o que pareça CPF, CNPJ, e-mail, telefone ou chave Pix, e a rota recusa de novo) | não | não é tratamento de dado pessoal | até a empresa apagar (`DELETE /simulacao`); sem expurgo automático (pendência 9) | a tela da empresa |
| `simulacao_verdade.*` (dias até o saldo, chance de pagar, revogação) | não (parâmetros inventados) | não | idem | idem | só a empresa que criou; nunca os modelos |
| `simulacao_retencoes.nome`, `mrr`, `sinais`, `propensao` | não (inventados) | não | idem | idem | a tela da empresa (a propensão não volta em leitura nenhuma) |
| as cópias de simulação de `ciclos_cobranca`, `ciclos_recuperacao`, `ciclos_retencao`, `decisoes_automatizadas`, do plano de retentativas e do bandit | não (tudo sobre o cliente fictício) | não | idem | idem | a tela da empresa, só com "Mostrar: Simulação" ou na página da simulação |

**O que a simulação não faz.** Não lê nem grava a base de clientes da empresa; não grava
nada nos arquivos reais (há teste byte a byte); não chama o PSP, o CRM nem envia mensagem,
mesmo com credencial configurada (há teste com a credencial ligada); e não manda nada à
Anthropic além do que o redator de mensagens já recebe de um ciclo real, com o nome
inventado no lugar do nome do cliente.

**O risco que sobra** é a pessoa digitar um dado real no campo de nome, de um jeito que
os filtros não peguem (um nome e sobrenome de verdade, por exemplo). O formulário avisa que
só entra nome inventado, e não existe campo para nenhum outro dado.

## 1g. O descadastro e os direitos do titular (Rodada 3)

Tabela `clientes_nao_contatar`, no mesmo destino da base importada (Postgres em produção).
Código: `churn_voluntary/clientes_importados.py`, `api/clientes.py`, `api/titular.py`.

| tabela · campo | pessoal? | sensível? | base legal | prazo | vai para |
|---|---|---|---|---|---|
| `clientes_nao_contatar.customer_id_externo` | **pseudônimo** (a chave que a empresa usa para o cliente) | não | cumprimento de obrigação legal (atender à oposição do titular, art. 18) | enquanto a marca existir; sem expurgo (a marca precisa durar para valer) | a cadeia de canal (leitura interna) e a exportação do titular |
| `clientes_nao_contatar.marcado_em`, `origem` | fraco (quando e por onde o titular pediu) | não | idem | idem | idem |

**O que as rotas novas fazem com dado de titular:**

| rota | o que lê | o que devolve | o que apaga | registro de acesso |
|---|---|---|---|---|
| `POST /titular/exportar` | cadastro, ciclos, mensagens, retenção, trilha | tudo isso, **sem** o valor do e-mail e do telefone, e sem a fee | nada | sim |
| `POST /titular/anonimizar` | idem | contagens | nome, e-mail, telefone, motivo de cancelamento e o texto das mensagens | sim |
| `POST` e `DELETE /clientes/{id}/nao-contatar` | a linha do cliente | a marca | a marca (no `DELETE`) | sim |
| `GET /titular/texto-para-politica` | um arquivo do repositório e os prazos da configuração | o texto | nada | não (não há dado de titular) |
| `POST /simulate/resposta-sair` (só desenvolvimento) | a linha do cliente | a marca | nada | não |

## 1f. Os eventos recebidos pela API (Rodada 3): só o hash

Tabela `eventos_recebidos`, no arquivo do ciclo de cobrança. Código:
`api/eventos_recebidos.py`. Serve para o mesmo evento reenviado contar uma vez.

| tabela · campo | pessoal? | sensível? | base legal | prazo | vai para |
|---|---|---|---|---|---|
| `eventos_recebidos.chave` | **não** (SHA-256 do tenant com o `messageId`, ou com o conteúdo do evento; não dá para voltar ao evento a partir dele) | não | execução de contrato | 30 dias (apagado a cada evento novo) | ninguém |
| `eventos_recebidos.tenant_id`, `recebido_em` | não | não | idem | idem | ninguém |

O evento em si (`userId`, `event`, `properties`) segue o caminho do webhook do Segment: vira
uma linha de `ciclos_retencao` (seção 2) e as decisões na trilha (com as mesmas entradas
explícitas, nunca o `properties` inteiro).

## 2. `ciclos_retencao` — log do churn voluntário (dataset de treino)

Onde: `app/data/retention_cycles.db` (SQLite local, fora do git). Origem:
`/webhooks/segment` e `/simulate/churn-risk`.

| tabela · campo | pessoal? | sensível? | base legal | prazo proposto | vai para |
|---|---|---|---|---|---|
| `ciclos_retencao.user_id` (`user:<id>` / `anon:<id>`) | sim (pseudônimo) | não | legítimo interesse | 24 meses (treino), depois anonimizar | HubSpot (nome do deal e contato), se token configurado |
| `ciclos_retencao.tenant_id` | não | não | — | — | HubSpot |
| `ciclos_retencao.registrado_em` | não | não | — | — | — |
| `ciclos_retencao.event` | sim (comportamento) | não | legítimo interesse | 24 meses | HubSpot (`trigger_event`); Anthropic (no prompt) |
| `ciclos_retencao.days_since_last` | sim | não | legítimo interesse | 24 meses | — |
| `ciclos_retencao.features_used_30d` | sim | não | legítimo interesse | 24 meses | — |
| `ciclos_retencao.mrr` | sim (financeiro) | não | contrato | 24 meses | — |
| `ciclos_retencao.billing_profile` | sim | não | legítimo interesse | 24 meses | — |
| `ciclos_retencao.on_site_now` | sim (comportamento) | não | legítimo interesse | 24 meses | — |
| `ciclos_retencao.risk_score`, `profile`, `criticality` | sim (perfilamento, art. 20) | não | legítimo interesse | 24 meses | HubSpot (`risk_score`) |
| `ciclos_retencao.offer_type`, `channel`, `offer_sent` | sim (decisão sobre a pessoa) | não | legítimo interesse | 24 meses | HubSpot; Anthropic (oferta e canal, no prompt) |
| `ciclos_retencao.accepted`, `desfecho_em`, `origem_desfecho` | sim | não | legítimo interesse | 24 meses | HubSpot (estágio do deal) |

## 3. `ciclos_recuperacao` — log do churn involuntário (dataset de treino)

Onde: `app/data/recovery_cycles.db` (SQLite local, fora do git). Origem:
`/webhooks/pix-automatico`, `/webhooks/stripe`, `/simulate/*`.

| tabela · campo | pessoal? | sensível? | base legal | prazo proposto | vai para |
|---|---|---|---|---|---|
| `ciclos_recuperacao.customer_id` | sim (pseudônimo) | não | contrato | 5 anos (prazo prescricional de cobrança, CC art. 206 §5º, I) — **NÃO VERIFICADO** com jurídico | HubSpot (`stripe_customer_id`, nome do deal); Anthropic (8 primeiros caracteres, no link do portal dentro do prompt) |
| `ciclos_recuperacao.e2e_id` (id fim-a-fim do Pix) | sim (identifica a transação de uma pessoa) | não | obrigação legal (conciliação) | 5 anos | — |
| `ciclos_recuperacao.tenant_id`, `registrado_em` | não | não | — | — | HubSpot |
| `ciclos_recuperacao.tenure_months`, `avg_ticket`, `payment_history_score`, `failure_count_90d`, `ltv_estimated` | sim (financeiro/comportamento) — **hoje vêm de `_perfil_simulado`, sintéticos** | não | contrato | 5 anos | — (só o SHAP local) |
| `ciclos_recuperacao.invoice_amount`, `amount` | sim (financeiro) | não | contrato | 5 anos | HubSpot (`amount`); Anthropic (valor, no prompt); Pagar.me (`amount`, no reenvio) |
| `ciclos_recuperacao.day_of_month`, `hour_of_day`, `day_of_week`, `attempt_count` | fraco (deriváveis da transação) | não | contrato | 5 anos | — |
| `ciclos_recuperacao.gateway_error_code`, `metodo_pagamento`, `card_brand` (legada, NULL desde 22/09/2026), `failure_cause` | sim (situação financeira: "sem saldo") | não | contrato | 5 anos | HubSpot (`failure_cause`); Anthropic (`failure_cause`, no prompt) |
| `ciclos_recuperacao.recovery_score`, `p_recovery`, `eprofit`, `estrategia`, `tentativas_planejadas` | sim (perfilamento, art. 20) | não | legítimo interesse | 5 anos | HubSpot (`recovery_score`) |
| `ciclos_recuperacao.recovered`, `desfecho_em`, `tentativas_usadas`, `custo_total`, `success_fee` | sim (desfecho financeiro) | não | contrato | 5 anos | HubSpot (estágio) |

## 4. `app/vault/pix_keys.json` — cofre da chave Pix

Escrito por `PixAutomaticoAdapter.store_encrypted_pix_key`, cifrado com
Fernet (`CRAI_ENCRYPTION_KEY`), fail-closed. Fora do git.

| arquivo · campo | pessoal? | sensível? | base legal | prazo proposto | vai para |
|---|---|---|---|---|---|
| `pix_keys.json[<id_recorrencia>]` = chave Pix do pagador (**CPF, telefone ou e-mail**), cifrada | **sim (direto; CPF)** | não (CPF não é art. 5º, II, mas é dado de alto risco) | obrigação legal (conciliação financeira) | enquanto a recorrência existir + prazo de conciliação — **NÃO VERIFICADO** | — (nenhum módulo de `ml/` ou `agent/` lê; só `decrypt_sensitive_field`) |

## 5. `app/data/pix_retry_state.json` — plano de retentativas

| arquivo · campo | pessoal? | sensível? | base legal | prazo proposto | vai para |
|---|---|---|---|---|---|
| chave `tenant:customer_id`, `customer_id`, `tenant_id` | sim (pseudônimo) | não | contrato | até o fim da janela + 30 dias | Pagar.me (`recurrence_id`, no reenvio) |
| `valor_original`, `pix_janela_ate`, `atualizado_em`, `tentativas[]` | sim (financeiro) | não | contrato | idem | Pagar.me (`amount`) |

## 6. `app/models/bandit_state.json` — posteriores do bandit

| arquivo · campo | pessoal? | sensível? | base legal | prazo | vai para |
|---|---|---|---|---|---|
| `<tenant>.<perfil>.<oferta>.{alpha, beta}` | **não** (agregado por tenant e perfil, sem identificador) | não | — | indefinido | — |

## 7. `app/logs/shap/shap_audit_*.json` — auditoria SHAP

| arquivo · campo | pessoal? | sensível? | base legal | prazo | vai para |
|---|---|---|---|---|---|
| `timestamp`, `input_features` (as 12 do classificador + `ltv_estimated`), `output`, `shap_explanation` | **não identificado**: o log **não grava `customer_id` nem `e2e_id`** | não | — | indefinido | — |

Consequência (ver `decisao-automatizada.md`): a explicação existe, mas não
está ligada ao ciclo. Para atender um pedido de revisão (art. 20 §1º) seria
preciso casar por `timestamp`.

## 8. Memória de processo (não persistida)

| onde · campo | pessoal? | sensível? | base legal | prazo | vai para |
|---|---|---|---|---|---|
| `MemorySaver` (checkpoint LangGraph, `thread_id = tenant:user_id`): o estado inteiro do ciclo, **inclusive `props` brutos do Segment** — o que o SDK da empresa mandar, hoje `phone` e o que mais vier | **sim (telefone, direto)** | depende do que o SDK mandar — **NÃO VERIFICADO** | legítimo interesse | morre no restart do processo; sem TTL | BSP do WhatsApp (`phone` como destino + texto da mensagem) |
| `state.message` (texto gerado pelo Claude) | sim (dirigido à pessoa) | não | legítimo interesse | idem | BSP do WhatsApp; **não é gravado em `ciclos_retencao`** |
| `_channel_history[tenant:user_id]` = canal que converteu | sim | não | legítimo interesse | idem | — |
| idempotência (`api/idempotencia.py`): chaves de evento (`e2e_id`, `user_id`+evento) | sim (pseudônimo) | não | legítimo interesse | TTL do registro | — |
| cache de JWKS (`accounts/supabase_auth.py::_cache`): `url`, chaves **públicas** do projeto, `expira_em` | **não** | não | — | 10 min | — |

## 9. Token do Supabase (lido, não gravado)

| origem · campo | pessoal? | sensível? | base legal (CRAI como controladora) | prazo | vai para |
|---|---|---|---|---|---|
| claim `sub` (uuid do usuário da empresa) | sim | não | contrato | não gravado | — |
| claim `email` (usuário da empresa) | **sim (direto)** | não | contrato | não gravado; usado só como destinatário em `/insights/enviar` | SMTP configurado pela CRAI |
| claim `tenant_id` | não | não | — | vai para todo log e tabela como partição | Supabase, HubSpot |
| claim `plano` (`essencial` ou `premium`; Rodada 2) | não (é da empresa) | não | contrato | não gravado; decide se as rotas de chave de API respondem. **O login real só terá a claim na Etapa 5**; sem ela vale `essencial` | — |
| Tabelas `empresas` (`id`, `nome`, `criada_em`) e `usuarios_empresas` (`user_id`, `empresa_id`) | `user_id`: sim | não | contrato | vida da conta | Supabase (moram lá; DDL só esboçado em `accounts/README.md`, **NÃO VERIFICADO** se existe) |

## 10. O que entra nos pipelines de ML (checagem da invariante)

| pipeline | vetor de entrada | contém CPF / telefone / e-mail / chave Pix? |
|---|---|---|
| `failure_classifier` (XGB + RF) | 9 numéricas + `gateway_error_code`, `metodo_pagamento` (`ALL_FEATURES_V2`; artefato da base v2 desde 22/09/2026) | **não** |
| `anomaly_detector` (autoencoder) | `BEHAVIORAL_FEATURES` (12: tenure, mrr, seats, logins, adoção, sessão, api calls, dias sem login, tickets, falhas de pagamento, nps) | **não** |
| `payday_inference` (LSTM + Prophet) | série de liquidez sintética por cliente (`generate_liquidity_series`) | **não** |
| `voluntary_risk` (candidato) e `risk_scorer` (regras / régua da base) | `FEATURES_DE_RISCO`: dias sem login, funcionalidades, mrr, 3 one-hots de evento | **não** |
| dataset de treino voluntário (`ciclos_retencao`) | as colunas de features; `user_id` está na tabela mas **não** entra no X | **não** |

**Resultado da checagem: nenhuma linha mostra CPF, telefone, e-mail ou chave
Pix entrando em pipeline de ML.** O telefone só existe em memória e sai para
o BSP; a chave Pix só existe cifrada no cofre; o e-mail só existe em
`clientes_importados` e não alimenta nada.

## 11. O que sai para cada terceiro (resumo)

| terceiro | estado hoje | campos que saem | módulo |
|---|---|---|---|
| Pagar.me | **simulado** por default (`CRAI_PAGARME_LIVE=1` + chave para o real) | `amount`, `recurrence_id`, `payment_method` | `integrations/pagarme_gateway.py` |
| HubSpot | **simulado** sem `HUBSPOT_TOKEN` | contato: `customer_id`/`user_id` como `stripe_customer_id`; deal: `amount`, `failure_cause`, `recovery_score`, `risk_score`, `trigger_event`, `offer_type`, `channel`, `tenant_id`, estágio | `integrations/hubspot_crm.py` |
| Segment | só **entrada** (webhook); a CRAI não envia nada ao Segment | — | `api/app.py::segment_webhook` |
| BSP do WhatsApp | **simulado** (log com telefone mascarado) | `phone` (E.164) + texto da mensagem | `integrations/whatsapp_sender.py` |
| Anthropic (Claude API) | **real** se `ANTHROPIC_API_KEY` estiver no ambiente; fallback sem rede | voluntário: `event`, `channel`, rótulo da oferta, criticidade — **sem identificador**. Involuntário, desde a Etapa 2 (as 3 sugestões, `montar_prompt`): causa em português, valor, tom, meio de pagamento, canal e, **só se a base tiver**, o primeiro nome e a faixa de tempo de casa real — **sem identificador nenhum** (o link vai como marcador `{link}` e é posto depois; há teste que reprova e-mail, telefone, CPF, chave Pix, id da recorrência, e2e ou sobrenome no prompt). O caminho antigo de uma mensagem só (chamada direta, sem ciclo) ainda leva o link com 8 caracteres do identificador | `churn_voluntary/voluntary_agent.py`, `dunning/dunning_engine.py` |
| Anthropic (Claude API), pelo **assistente** do dashboard (Rodada 3) | **real** se `ANTHROPIC_API_KEY` estiver no ambiente; sem ela, texto fixo, sem rede | a documentação de produto; os **totais** da empresa do token (valores, contagens, taxas, rótulos de causa, oferta e canal, a configuração de mensagens e prazos), depois de um filtro que só deixa passar número, data e rótulo de lista fechada; e a pergunta do usuário, com e-mail, CPF, CNPJ, telefone e número longo mascarados. **Nunca:** nome, contato, id de cliente, id de recorrência, texto de mensagem, a fee. Um nome de pessoa digitado na pergunta vai como digitado. A pergunta e a resposta não são guardadas | `api/assistente.py` |
| Supabase | conta ainda não criada | `clientes_importados` inteira; auth (`sub`, `email`, `tenant_id`) | `churn_voluntary/clientes_importados.py`, `accounts/` |
| SMTP (provedor da CRAI) | simulado sem SMTP | `customer_id_externo`, risco, criticidade, `explicacao` dos clientes em risco → e-mail da conta autenticada | `integrations/email_sender.py` |

## 12. Pendências que este mapeamento expõe

1. **O expurgo cobre cinco coisas** (texto das mensagens, registro de acesso, trilha, ciclos e simulação). Desde a Etapa 2 (Bloco 4) o relógio apaga, uma vez por dia, o texto das mensagens do involuntário (90 dias depois do desfecho) e o registro de acesso (12 meses). Desde a Rodada 3 (Fase 7) o mesmo expurgo diário apaga as decisões da trilha do Art. 20 com mais de 5 anos (ou do prazo da empresa, se for maior). Desde a Rodada 4 ele também **anonimiza os ciclos de cobrança** com desfecho há mais que o prazo da empresa (24 meses por padrão; saem os identificadores, ficam os valores e as datas) e **apaga a simulação do gateway** parada há mais de 30 dias. **Continuam sem execução:** a base depois do contrato (6 meses, depende do site) e o dataset de retenção do voluntário. Os prazos dessas linhas são propostas ou configuração ainda não executada.
2. **`props` brutos do Segment entram inteiros no estado** (`_run_voluntary_pipeline`): o que a empresa mandar no SDK fica em memória. Recomenda-se filtrar para a lista de campos usados antes de entrar no grafo.
3. **A explicação SHAP não carrega identificador** (seção 7): auditável em agregado, não por pessoa.
4. **`customer_id[:8]` vai para a Anthropic** dentro do link do portal; é um fragmento de pseudônimo, mas é envio a terceiro fora do país — cabe DPA/cláusula de transferência internacional (art. 33). Desde a Etapa 2 o prompt das 3 sugestões do involuntário não leva mais o link; o primeiro nome e a faixa de tempo de casa passam a ir quando a base os tem — continua cabendo DPA.
5. Os CSV de `exemplos/` são sintéticos (`gerar_bases_demo.py`, e-mails `@exemplo.com.br`); a `base_exemplo_clientes.csv` vem do mvp-crai com nomes de empresas fictícias — **NÃO VERIFICADO** se algum registro é real.
6. **O registro de operações de tratamento (art. 37) não está em `docs/lgpd/`.** A OP-01 (recuperação) é citada no plano das etapas e num `relatorio-conformidade-lgpd.md` que não está no repositório; as atualizações da Etapa 2 (texto das mensagens, `id_recorrencia`, canal por cliente, `telefone` e `nome`) estão registradas neste mapeamento até o registro existir aqui.
7. **As chaves de API não têm expurgo** (seção 1c). A chave revogada continua na tabela (sem o segredo: só o hash, o nome e as datas) e o contador diário de uso (`chaves_api_uso`) cresce uma linha por chave por dia de uso, sem prazo. Nenhuma das duas guarda dado de titular; o `nome` da chave é texto livre da empresa. Prazo a definir.
8. **O valor mantido do voluntário não tem expurgo** (seção 1d). `retencoes_mantidas` guarda o identificador que a empresa usa para o cliente, a oferta e os valores. O prazo proposto é o dos ciclos (24 meses), ainda não executado.
9. **A simulação do gateway não tem expurgo automático** (seção 1e). Os arquivos de simulação de uma empresa ficam até ela apagar. Não guardam dado de titular (o nome é inventado e filtrado), mas um nome real digitado à mão ficaria ali. Prazo a definir; a limpeza de um clique já existe.
10. **O limite de contato usa o dataset de treino como memória** (seção 2): a última oferta enviada a cada cliente é lida de `ciclos_retencao`. Quando o expurgo desse dataset for executado, o prazo dele precisa ser maior que o maior intervalo permitido (365 dias), senão o limite deixa de segurar.
11. **A anonimização deixa o pseudônimo** (seção 1g). Depois de `POST /titular/anonimizar`, o `customer_id_externo` continua nos ciclos, no dataset e na trilha: é o que mantém as métricas agregadas. A eliminação completa (art. 18, VI), inclusive do identificador, não está construída: ela conflita com a guarda da trilha do Art. 20, e é decisão jurídica.
12. **O expurgo dos ciclos (24 meses) e o da base (contrato + 6 meses) continuam sem execução.** A tela e o texto para a política deixaram de prometê-los.

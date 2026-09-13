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
| `ciclos_recuperacao.gateway_error_code`, `card_brand`, `failure_cause` | sim (situação financeira: "sem saldo") | não | contrato | 5 anos | HubSpot (`failure_cause`); Anthropic (`failure_cause`, no prompt) |
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
| Tabelas `empresas` (`id`, `nome`, `criada_em`) e `usuarios_empresas` (`user_id`, `empresa_id`) | `user_id`: sim | não | contrato | vida da conta | Supabase (moram lá; DDL só esboçado em `accounts/README.md`, **NÃO VERIFICADO** se existe) |

## 10. O que entra nos pipelines de ML (checagem da invariante)

| pipeline | vetor de entrada | contém CPF / telefone / e-mail / chave Pix? |
|---|---|---|
| `failure_classifier` (XGB + RF) | 9 numéricas + `gateway_error_code`, `card_brand` (`ALL_FEATURES`) | **não** |
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
| Anthropic (Claude API) | **real** se `ANTHROPIC_API_KEY` estiver no ambiente; fallback sem rede | voluntário: `event`, `channel`, rótulo da oferta, criticidade — **sem identificador**. Involuntário: `failure_cause`, `amount`, tom, meio de pagamento e o link `pay.crai.ai/<metodo>/<customer_id[:8]>` — **8 caracteres do identificador** | `churn_voluntary/voluntary_agent.py`, `dunning/dunning_engine.py` |
| Supabase | conta ainda não criada | `clientes_importados` inteira; auth (`sub`, `email`, `tenant_id`) | `churn_voluntary/clientes_importados.py`, `accounts/` |
| SMTP (provedor da CRAI) | simulado sem SMTP | `customer_id_externo`, risco, criticidade, `explicacao` dos clientes em risco → e-mail da conta autenticada | `integrations/email_sender.py` |

## 12. Pendências que este mapeamento expõe

1. **Não há política de retenção nem expurgo** em nenhuma tabela ou arquivo. Os prazos acima são propostas.
2. **`props` brutos do Segment entram inteiros no estado** (`_run_voluntary_pipeline`): o que a empresa mandar no SDK fica em memória. Recomenda-se filtrar para a lista de campos usados antes de entrar no grafo.
3. **A explicação SHAP não carrega identificador** (seção 7): auditável em agregado, não por pessoa.
4. **`customer_id[:8]` vai para a Anthropic** dentro do link do portal; é um fragmento de pseudônimo, mas é envio a terceiro fora do país — cabe DPA/cláusula de transferência internacional (art. 33).
5. Os CSV de `painel/exemplos/` são sintéticos (`gerar_bases_demo.py`, e-mails `@exemplo.com.br`); a `base_exemplo_clientes.csv` vem do mvp-crai com nomes de empresas fictícias — **NÃO VERIFICADO** se algum registro é real.

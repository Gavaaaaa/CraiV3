# Decisões automatizadas — CRAI

Levantado em 12/09/2026 lendo os dois grafos LangGraph
(`app/crai/agent/main_agent.py` + `workflow.py`, churn involuntário;
`app/crai/churn_voluntary/voluntary_agent.py`, churn voluntário) e os módulos
que cada nó chama. Invariante do produto: **nenhuma decisão passa por humano**,
em nenhum caminho. É exatamente o caso do art. 20 da LGPD: o titular tem
direito a pedir revisão de decisão tomada unicamente com base em tratamento
automatizado que afete seus interesses, e a controladora tem que fornecer
informações claras sobre os critérios. O Marco Legal da IA (PL 2338/2023)
acrescenta o dever de explicabilidade. Esta é a lista do que a CRAI decide
sozinha, com o que, e onde a explicação fica (ou não fica) registrada.

Coluna "onde fica a explicação": o que o código grava HOJE. **Lacuna** =
a decisão é gravada, mas o porquê não; ou o porquê é gravado sem ligação com
a pessoa.

---

## A. Churn voluntário (grafo `build_voluntary_churn_graph`)

| # | decisão | quem produz | o que entra | onde fica registrada | onde fica a explicação |
|---|---|---|---|---|---|
| V1 | **Risco de churn** (`risk_score`, 0–1) | regras fixas `risk_scorer._risco_por_regras` (evento fixo → 0,90/0,75; sessão → dias/30 e (5−uso)/5); na base importada com ≥ 30 linhas, **régua da base** `risco_por_posicao` (posição nos percentis da própria base); modelo treinado `voluntary_risk.joblib` se existir e for válido (hoje **não existe**) | `event`, `days_since_last`, `features_used_30d`, `mrr` (e os 3 one-hots do evento, no modelo) | `ciclos_retencao.risk_score` (SDK); resposta do `/insights` (upload, não persistida) | **SDK: lacuna** — só o número. **Upload:** a frase `explicacao` ("sem login há 47 dias — acima de 90% da sua base, …") é gerada na leitura e devolvida ao painel; não é persistida. `origem_da_regua` diz qual régua. |
| V2 | **Seguir ou não com a retenção** (`route_after_risk`) | regra: `risk_score` < 0,60 → não intervém e vai direto a `update_crm`; ≥ 0,60 → escolhe oferta | `risk_score` | `update_crm` chama `registrar_ciclo` nos dois caminhos: o ciclo "não contatado" **é gravado** em `ciclos_retencao` (`offer_sent = 0`, `offer_type` nulo) | o limiar 0,60 é constante no código; não há texto por ciclo |
| V3 | **Criticidade** (`critico` / `alto` / `padrao`) — o TOM da mensagem, nunca desvio de fluxo | regra `classify_criticality`: risco ≥ 0,90 → crítico; MRR ≥ R$ 2.000 → crítico por valor; ≥ 0,75 → alto. Na régua da base, alto/crítico por risco exigem sinal absoluto (≥ 7 dias sem login ou 0 funcionalidades) | `risk_score`, `mrr`, `days_since_last`, `features_used_30d` | `ciclos_retencao.criticality` | upload: a `explicacao` diz "crítico pelo valor da conta, não pelo risco" ou "primeiro da fila, mas sem sinal de abandono". SDK: **lacuna** (só o rótulo) |
| V4 | **Perfil de cobrança** (`CLT` / `PJ` / `freelancer`) | regra `classify_profile`: copia `billing_profile` do payload, default `CLT` | `billing_profile` | `ciclos_retencao.profile` | — (é cópia, não inferência) |
| V5 | **Qual oferta** (`desconto_10`, `desconto_20`, `pausa_1_mes`, `pix_boleto_flash`) | **bandit Thompson Sampling** `OfferBandit.choose_offer`: sorteia da posterior Beta(α, β) por tenant × perfil; priors de cold start de benchmark, warm start de simulação de 6.000 rodadas | `tenant_id`, `profile`, `risk_score` | `ciclos_retencao.offer_type`; posteriores em `models/bandit_state.json` | **lacuna**: a escolha é aleatória por construção (sorteio da posterior); o que dá para explicar é "a oferta X tem a maior taxa de aceite estimada para o perfil Y" (`conversion_rates`), e isso **não é gravado** no ciclo |
| V6 | **Canal** (`popup` / `email` / `whatsapp`) | regra `choose_channel`: canal que já converteu antes para este usuário (`_channel_history`) > WhatsApp se houver telefone utilizável > popup se está no site > e-mail | `on_site_now`, `props.phone`, histórico em memória | `ciclos_retencao.channel` | lacuna: o motivo (histórico / telefone / no site) não é gravado |
| V7 | **Texto da mensagem** | **Claude API** (`generate_message`, prompt por criticidade); fallback de texto fixo se a API falhar | `event`, `channel`, rótulo da oferta, criticidade; nenhum identificador | **não é persistido** (só no `MemorySaver`) e sai para o BSP | **lacuna**: a mensagem enviada à pessoa não fica em lugar nenhum |
| V8 | **Desfecho e aprendizado** (`track_outcome`, `registrar_resultado_externo`) | em simulação, aceite **sorteado** (`CRAI_SIMULATE_OUTCOMES=1`); em produção, o webhook `/webhooks/retention-outcome`; o aceite atualiza a posterior do bandit | `accepted` | `ciclos_retencao.accepted`, `origem_desfecho` (`simulacao` ou externo) | — |

## B. Churn involuntário (grafo `build_agent`)

| # | decisão | quem produz | o que entra | onde fica registrada | onde fica a explicação |
|---|---|---|---|---|---|
| I1 | **Causa da falha** (`failure_cause`) | regra `diagnose_failure`: mapa fixo de código do PSP → causa (`PIX_CODE_MAP` em `agent/pix_codes.py`; códigos de cartão no legado) | código de falha do webhook | `ciclos_recuperacao.failure_cause`, `gateway_error_code` | o mapa é público no código; não há texto por ciclo |
| I2 | **Abandonar ou perseguir** (`route_after_diagnosis`, `route_after_decision`) — **a decisão mais grave do sistema: quem NÃO recebe tentativa de recuperação** | regra: e-Profit ≤ 0 ou `recovery_score` < 5 → não persegue | `recovery_score`, `p_recovery`, `eprofit` (I3), custo previsto do ciclo | o caminho de abandono vai de `diagnose` direto a `update_dashboard`, que chama `registrar_ciclo`: o ciclo abandonado **é gravado** em `ciclos_recuperacao` (`estrategia`, `tentativas_planejadas = 0`, `recovered = 0`) | lacuna: o porquê (e-Profit ≤ 0 ou score < 5) não é gravado como campo; só dedutível de `eprofit` e `recovery_score` |
| I3 | **Probabilidade de recuperação e e-Profit** (`recovery_score`, `p_recovery`, `eprofit`) | **modelo** `FailureClassifier` (XGBoost 0,7 + Random Forest 0,3) se houver artefato; **heurística** por causa se não houver (`is_fitted` falso). e-Profit = p × valor − custo | as 12 features de `ALL_FEATURES` (`tenure_months`, `day_of_month`, `invoice_amount`, `avg_ticket`, `payment_history_score`, `failure_count_90d`, `hour_of_day`, `day_of_week`, `attempt_count`, `gateway_error_code`, `card_brand`) + `ltv_estimated` no e-Profit. **Hoje 4 delas vêm de `_perfil_simulado` (sintéticas)** | `ciclos_recuperacao.recovery_score`, `p_recovery`, `eprofit`, e as 12 features | **SHAP por predição** em `app/logs/shap/shap_audit_<timestamp>.json` (`shap_explanation` com as features que mais pesaram, e `readable`) — **mas sem `customer_id` nem `e2e_id`**: a explicação existe e não está ligada ao ciclo. Sem modelo: `"Modelo não treinado — usando heurística"` |
| I4 | **Anomalia comportamental** (`is_anomalous`) | **modelo** `AnomalyDetector` (autoencoder) se houver artefato; heurística por perfil se não | `BEHAVIORAL_FEATURES` (12) — **hoje sintéticas** para o cliente | `AgentState.is_anomalous`, `anomaly_explanation` (top features) — **não gravado** em `ciclos_recuperacao` | lacuna: `top_features` existe no estado e morre com o processo |
| I5 | **Janela de pagamento** (`payday_window`) | **modelo** `PaydayInference` (LSTM + Prophet por perfil) se houver artefato; heurística por perfil (CLT dia 5, etc.) se não | 30 dias de saldo — **hoje série sintética** (`generate_liquidity_series`) | `AgentState.payday_window` — não gravado | lacuna |
| I6 | **Datas das retentativas Pix** (`schedule_retry_pix`) | regra `PixAutomaticoRetryPolicy` (limites do BACEN para Pix Automático: até 3 tentativas dentro da janela) + a janela de I5 | I5, `pix_janela_ate`, tentativas usadas | `app/data/pix_retry_state.json` (`tentativas[]` com datas); `ciclos_recuperacao.tentativas_planejadas` | as datas ficam; o porquê de cada data (janela do payday) não |
| I7 | **Meio de pagamento oferecido e tom** (`dunning_engine`) | regra: causa → meio (`boleto` se falha técnica ou autorização revogada, senão Pix Automático); tom por `recovery_score` (empático / amigável / direto) | `failure_cause`, `recovery_score` | **não gravado** (`payment_method`, `tone`, `portal_link` só no estado) | lacuna |
| I8 | **Texto da cobrança** | **Claude API** (`_generate_message`), fallback por template | `failure_cause`, `amount`, tom, meio, link com `customer_id[:8]` | **não gravado**; hoje só impresso (WhatsApp do involuntário é `print`) | lacuna |
| I9 | **Fechamento do ciclo e success fee** | regra: webhook de pagamento confirmado casa pelo `e2e_id` (idempotente) → `recovered=1`, fee = `amount × success_fee_pct()` | evento do PSP | `ciclos_recuperacao.recovered`, `desfecho_em`, `success_fee`, `custo_total` | — (determinístico) |

## C. Decisões da camada de importação e painel

| # | decisão | quem produz | o que entra | registrada | explicação |
|---|---|---|---|---|---|
| P1 | **Rejeitar uma linha da planilha** | regra `importacao.validar_linha` | os 6 campos | resposta do `POST /clientes/importar` (`rejeitados[].linha`, `motivo`) — não persistida | o `motivo` cita o valor recusado; fica só na resposta |
| P2 | **`dado_insuficiente`** (não avaliar) | regra `batch_scoring.pontuar_cliente`: sem `days_since_last` e sem `features_used_30d` → `risk_score: null` | as duas colunas | resposta do `/insights` | texto fixo "sem dado de atividade — impossível avaliar risco de churn" |
| P3 | **Régua da base vs global** (`origem_da_regua`) | regra `regua_da_base`: ≥ 30 linhas utilizáveis em cada coluna e distribuição não degenerada | a base inteira do tenant | resposta do `/insights` | o campo `origem_da_regua`; os percentis usados só no log (`[BATCH-SCORING] … regua=…`) |

## D. O que falta para atender o art. 20 (revisão) e a explicabilidade

1. **Ligar a explicação à pessoa.** O SHAP (I3) é gravado sem identificador; o `top_features` da anomalia (I4) e a janela do payday (I5) não são gravados. Proposta: gravar `explicacao_json` em `ciclos_recuperacao` (SHAP + anomalia + janela) e `explicacao` em `ciclos_retencao` (a mesma frase do painel, mais o motivo do canal e a taxa estimada da oferta escolhida).
2. **Explicar a decisão negativa.** Quem não foi perseguido (I2) e quem não foi contatado (V2) têm o ciclo gravado, mas sem o motivo em campo próprio; são as decisões que mais afetam a pessoa e as que um pedido de revisão vai perguntar primeiro.
3. **Guardar a mensagem enviada** (V7, I8). Sem ela, um pedido de revisão não tem como mostrar o que a pessoa recebeu.
4. **Declarar o aleatório.** O bandit (V5) sorteia por construção; a explicação honesta é "escolha probabilística com taxa estimada X", e ela precisa estar escrita para o titular, não só no código.
5. **Declarar o sintético.** Enquanto 4 features do classificador e todas as do autoencoder e do payday vierem de `_perfil_simulado` e de séries sintéticas, a "decisão sobre a pessoa" é, em parte, decisão sobre um perfil inventado. Isso já está em `docs/LIMITACOES.md` e precisa constar em qualquer resposta a titular.
6. **Canal de revisão.** Não existe endpoint nem processo para o titular pedir revisão; o produto promete zero humano no fluxo, e o art. 20 não exige humano na revisão, mas exige o canal e a informação.

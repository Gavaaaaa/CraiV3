# Prompt em Sprints — Churn Involuntário pronto para o mercado (CRAI · Pagar.me)

> Verificado contra o código real do repositório (branch main atual). Caminhos,
> assinaturas e estruturas conferidos. O churn involuntário já passou pelas
> Fases 1-3 (isolamento cartão/Pix, retentativa BACEN, segurança de webhook);
> estes sprints fecham o que falta para operar no mercado com Pagar.me como PSP.
>
> Como usar: cole "CONTEXTO" uma vez. Depois um sprint por vez, na ordem,
> validando o gate de cada um antes do próximo. Não cole tudo de uma vez.

---

## CONTEXTO (colar uma vez)

```
Você está no repositório da CRAI (github.com/Gavaaaaa/crai). Esta sessão
trata EXCLUSIVAMENTE do agente de churn involuntário (crai/crai/agent/,
crai/crai/dunning/), deixando-o pronto para operar no mercado com Pagar.me
como PSP de Pix Automático. O churn voluntário (crai/crai/churn_voluntary/)
NÃO deve ser alterado em comportamento por nenhum sprint aqui.

ESTRUTURA REAL DE PASTAS (confirmada — atenção aos caminhos):
- Raiz do pacote: crai/crai/
- Agente involuntário: crai/crai/agent/ (main_agent.py, state.py, workflow.py)
- Dunning/retentativa: crai/crai/dunning/ (dunning_engine.py,
    pix_automatico_retry.py, legacy_card/)
- ML: crai/crai/ml/ (failure_classifier.py, anomaly_detector.py,
    payday_inference.py, synthetic_data.py)
- Integrações: crai/crai/integrations/ (payment_gateway.py com
    PixAutomaticoAdapter, hubspot_crm.py)
- API: crai/crai/api/app.py
- Testes: crai/tests/ (NÃO crai/crai/tests/). Já existem:
    test_agent_graph.py, test_payment_isolation.py,
    test_pix_automatico_retry.py, test_payment_gateway.py,
    test_webhook_security.py, test_failure_classifier.py, etc.
- Pipeline de demo: crai/test_pipeline.py

FATOS DO CÓDIGO REAL (não assuma outra coisa):
- AgentState (crai/crai/agent/state.py): tem payment_method:
    Literal["pix_automatico","card"], recovered: bool, retry_count: int,
    pix_retry_schedule, next_retry_at, retry_exhausted. NÃO tem tenant_id.
- O grafo (main_agent.py): diagnose → (router e-Profit) → check_anomaly →
    infer_payday → decide_recovery → (router) → schedule_retry_pix OU
    trigger_dunning → update_dashboard → END. Aresta condicional por
    payment_method já isola Pix de cartão. Cartão está em
    crai/crai/dunning/legacy_card/ (fora do fluxo ativo).
- recovered É INICIALIZADO False em app.py (_run_involuntary_pipeline) e
    NUNCA é setado para True por nenhum código. Não há webhook de
    confirmação de pagamento. O success fee em update_roi_dashboard
    (amount*0.15) portanto nunca dispara.
- PixAutomaticoRetryPolicy.schedule(customer_id, valor_original,
    tentativas_usadas) só CALCULA as datas das tentativas (retorna lista de
    TentativaAgendada). NÃO envia nada ao PSP. Respeita BACEN: MAX_TENTATIVAS=3,
    JANELA_DIAS=7, valor fixo; levanta PixRetryPolicyViolation se violado.
- payment_gateway.py já tem PixAutomaticoAdapter (parse_pix_event) agnóstico
    de PSP, com referência à API da Iugu. parse_card_event levanta
    NotImplementedError de propósito.
- failure_classifier + _features_pix (workflow.py) hoje mapeiam TODA falha de
    Pix para um único gateway_error_code = "insufficient_funds"
    (CAUSA_PIX_FALHA). O vocabulário de FALLBACK_TEMPLATES no dunning_engine é
    de códigos do Stripe (expired_card, do_not_honor, etc.).
- _perfil_simulado (workflow.py) gera tenure/histórico/LTV com np.random a
    partir de seed_por_cliente — perfil 100% sintético (em produção viria do
    banco/CRM). É o ponto de integração de dados reais.
- infer_payday roda o Payday Engine e grava optimal_retry_at/confidence/
    profile_type, mas esses outputs NÃO são lidos por nenhum nó — a
    PixAutomaticoRetryPolicy consulta o Payday por conta própria (dupla
    chamada, documentada como P2-10). NÃO mexer nisso antes da demo.

DECISÃO DE PSP: o PSP oficial é o Pagar.me. Onde estes sprints falarem de
"enviar instrução de pagamento" ou "confirmar pagamento", a referência é a
API do Pagar.me. Toda integração externa deve ter modo simulado (default,
para banca/testes) e modo real (via env), no mesmo padrão adapter já usado
no projeto — nunca exigir credencial real para rodar test_pipeline.

INVARIANTES DE PRODUTO (não-negociáveis):
- A CRAI é 100% autônoma, NUNCA escala para humano.
- Pix e cartão NUNCA compartilham política de retentativa (isolamento por
    payment_method + aresta condicional — já implementado, não regredir).
- Retentativa de Pix SEMPRE dentro dos limites do BACEN (3/7dias/valor fixo).
- A chave Pix do pagador NUNCA entra no pipeline de ML nem em log.

CONVENÇÕES: logs em PT-BR com prefixos já em uso ([AGENT], [ROUTER],
[DUNNING], [PIX-RETRY], [ROI], [HUBSPOT], [SHAP]); métrica de decisão é
e-Profit; LangGraph (StateGraph+END); TypedDict no state; funções async nos
nós; teste correspondente em crai/tests/ para todo código novo.

REGRA DE OURO: ao fim de cada sprint rodar o gate + `python
crai/test_pipeline.py` e me mostrar o resultado. Se QUALQUER teste do churn
voluntário quebrar, PARE e me avise — não conserte o voluntário por conta
própria.

CS (CRM depois): NÃO finalizar o HubSpot nesta sessão. Onde os sprints
tocarem register_recovery_cycle, apenas manter o comportamento atual
funcionando; o CRM será tratado numa frente separada. Nunca alterar
register_retention_cycle (é do voluntário).

Confirme que entendeu os fatos do código real e o isolamento entre os dois
churns antes de eu enviar o Sprint 1.
```

---

## SPRINT 1 — Confirmação real de pagamento (fechar o ciclo)

```
SPRINT 1 DE 7 — Webhook de confirmação: marcar recovered de verdade

PROBLEMA (o mais grave): recovered nunca vira True. É inicializado False em
_run_involuntary_pipeline (app.py) e nenhum código o atualiza. Logo, o
sistema nunca registra uma recuperação bem-sucedida, o success fee
(amount*0.15 em update_roi_dashboard) nunca dispara, e o HubSpot nunca fecha
um deal como recuperado. É o equivalente involuntário ao problema do
random() que o churn voluntário já teve.

CONTEXTO PAGAR.ME: quando uma cobrança de Pix Automático é paga (seja numa
das janelas automáticas, seja numa retentativa reenviada pela CRAI), o
Pagar.me envia um webhook de status "pago/confirmado". É esse evento que
fecha o ciclo.

IMPLEMENTAR:

1. Em crai/crai/api/app.py, no webhook /webhooks/pix-automatico já existente:
   hoje ele só aciona o pipeline quando status == STATUS_COBRANCA_FALHADA e
   apenas registra os demais status. Adicionar tratamento explícito do status
   de cobrança CONFIRMADA/PAGA: quando chegar, NÃO rodar o pipeline de
   diagnóstico de novo — em vez disso, fechar o ciclo de recuperação:
     - Localizar o ciclo em aberto para aquele id_recorrencia / e2e_id /
       customer_id (usar _thread_id como já é feito).
     - Marcar a recuperação como bem-sucedida e registrar no HubSpot via
       register_recovery_cycle com o estágio de recuperado (mantendo o
       comportamento atual do método; se ele já aceita um state com
       recovered=True e produz o estágio certo, reutilizar).
     - Registrar o success fee no [ROI] com recovered=True.
   Como o grafo usa MemorySaver com thread_id = customer_id, use o mesmo
   thread_id para recuperar o checkpoint do ciclo e atualizar recovered, OU
   (se for mais simples e robusto) crie uma função dedicada
   _fechar_ciclo_recuperado(customer_id, e2e_id, amount) que faz o registro
   de ROI + HubSpot sem reprocessar o grafo. Documente a escolha.

2. Idempotência (DOIS lados, não só a confirmação):
   a) Confirmação: o Pagar.me pode reenviar o mesmo webhook de confirmação.
      Garantir que fechar o mesmo ciclo duas vezes não conta o success fee em
      dobro (dedupe simples por e2e_id recente, documentado como MVP).
   b) Falha: o Pagar.me também pode reenviar o mesmo evento de FALHA. Hoje,
      dois eventos de falha idênticos rodariam o pipeline duas vezes e
      agendariam tentativas duplicadas (violando o limite BACEN). Adicionar
      dedupe do webhook de falha por (id_recorrencia + e2e_id) recente, para
      que um reenvio do mesmo evento não dispare um segundo pipeline.
      Documentar como MVP (a versão robusta virá com o DB persistente — ver a
      nota do Sprint 2 sobre retry_count).

3. Modo simulação: test_pipeline e a demo devem conseguir exercitar um ciclo
   COMPLETO (falha → agendamento → confirmação → recovered=True → fee) sem
   depender de webhook externo real. Adicionar ao test_pipeline (ou a um
   endpoint /simulate/pix-pago) um gatilho que simula a chegada da
   confirmação de pagamento e fecha o ciclo, para a banca ver o loop inteiro.

GATE:
    cd crai && pytest tests/test_pix_confirmacao.py -v   (criar)
    cd crai && pytest tests/ -q       # nada do voluntário/involuntário quebra
    python crai/test_pipeline.py
test_pix_confirmacao: um evento de confirmação fecha o ciclo com
recovered=True e dispara o fee; um segundo evento idêntico NÃO conta o fee de
novo. A demo deve imprimir pelo menos um [ROI] [OK] com fee > 0. Mostre os
resultados.
```

---

## SPRINT 2 — Executor de retentativa no Pagar.me (enviar, não só agendar)

```
SPRINT 2 DE 7 — Ponte entre o plano agendado e o envio real ao Pagar.me

PROBLEMA: PixAutomaticoRetryPolicy.schedule() só CALCULA as datas das
tentativas (retorna TentativaAgendada[]). Nada reenvia a instrução de
pagamento ao PSP nas datas planejadas. Em produção, o agente decide quando
tentar mas não executa a tentativa.

IMPLEMENTAR:

1. Criar crai/crai/integrations/pagarme_gateway.py com uma classe/função de
   SAÍDA para Pix Automático no Pagar.me:
       async def reenviar_cobranca_pix(id_recorrencia: str, valor: float,
                                       e2e_ref: str | None = None,
                                       tenant_id: str | None = None) -> dict
   que reenvia uma nova instrução de cobrança (nova charge da assinatura de
   Pix Automático) conforme a API do Pagar.me. Requisitos:
     - Modo simulado (default, sem env): não chama rede, retorna um dict de
       sucesso simulado com um id fictício — para banca/testes.
     - Modo real (env CRAI_PAGARME_LIVE=1 + CRAI_PAGARME_API_KEY): chama a API
       real do Pagar.me. Documentar no docstring o endpoint/estrutura usados
       (baseado na doc pública do Pagar.me para Pix/assinaturas recorrentes);
       se algum campo exato não for conhecível sem credencial, deixar
       claramente marcado como TODO de integração, sem inventar.
     - NUNCA enviar valor diferente do original (reforço do invariante BACEN).
     - Log [PAGARME] por tentativa reenviada.
   Espelhar o mesmo padrão de adapter já usado no projeto (entrada:
   PixAutomaticoAdapter; agora a saída: pagarme_gateway).

2. Em crai/crai/agent/workflow.py, no nó schedule_retry_pix: hoje ele só
   grava o plano (pix_retry_schedule) e a próxima data. Passar a, além de
   agendar, DISPARAR a primeira tentativa devida via
   pagarme_gateway.reenviar_cobranca_pix quando a data da primeira tentativa
   for "agora/vencida" (nas execuções de teste/simulação, considerar a
   primeira tentativa como imediatamente disparável).

3. AGENDADOR das tentativas 2 e 3 (Gap 1 da auditoria — não deixar solto):
   as tentativas futuras do plano BACEN (dias seguintes) precisam de algo que
   as dispare na data certa. O worker/cron de produção é infra externa, MAS o
   agente não pode simplesmente "esquecer" as tentativas 2 e 3 — isso quebra
   a promessa de usar as 3 tentativas do BACEN. Implementar um STUB de
   agendador dentro do escopo desta sessão:
     - crai/crai/dunning/retry_scheduler.py com uma função
       processar_tentativas_devidas(agora: datetime | None = None) que
       percorre os planos de retentativa pendentes e dispara, via
       pagarme_gateway, as tentativas cuja data já chegou (e ainda não
       disparadas). Em modo simulação, o test_pipeline chama essa função com
       um "agora" avançado no tempo para demonstrar as 3 tentativas sendo
       disparadas em sequência.
     - Documentar claramente: em produção, um cron/worker chamaria
       processar_tentativas_devidas periodicamente; o stub prova que a lógica
       de disparo das tentativas 2 e 3 existe e é testável, sem depender de
       infra de agendamento real.
   Onde os planos pendentes ficam guardados: ver a nota de persistência
   abaixo (por ora, memória/arquivo simples; em produção, DB).

4. NOTA DE DEPENDÊNCIA DE INFRA (Gap 2 — sinalizar, não resolver aqui):
   o grafo usa MemorySaver() (checkpoint EM MEMÓRIA) e o retry_count deriva
   do attempt_count do evento. Isso significa que, se o processo reiniciar, a
   contagem de tentativas BACEN já usadas pode se perder — e um novo evento
   poderia reiniciar a contagem, arriscando ultrapassar o limite de 3/7 dias.
   Resolver de verdade exige persistência (PostgreSQL — outra frente, fora
   deste escopo). Nesta sessão: (a) documentar explicitamente essa limitação
   no código e no README, e (b) isolar a leitura/gravação de retry_count e do
   plano pendente atrás de uma pequena camada (ex: funções get_retry_state /
   save_retry_state) que hoje usa memória/arquivo simples e amanhã troca para
   DB sem tocar no resto. Isso deixa o ponto de troca pronto e a limitação
   visível para a banca, em vez de escondida.

5. NÃO tocar em legacy_card/ nem reativar cartão. reenviar_cobranca_pix é
   exclusivo de Pix Automático.

GATE:
    cd crai && pytest tests/test_pagarme_gateway.py -v   (criar)
    cd crai && pytest tests/test_retry_scheduler.py -v   (criar)
    cd crai && pytest tests/test_pix_automatico_retry.py -v  # não regrediu
    cd crai && pytest tests/ -q
    python crai/test_pipeline.py
test_pagarme_gateway: em modo simulado, reenviar_cobranca_pix retorna sucesso
sem rede; valor != original levanta erro; schedule_retry_pix chama o executor
para a primeira tentativa (mock/spy).
test_retry_scheduler: com o tempo avançado, processar_tentativas_devidas
dispara as tentativas 2 e 3 nas datas certas e nunca ultrapassa 3 tentativas.
A demo (test_pipeline) deve mostrar as 3 tentativas de um ciclo sendo
disparadas. Mostre os resultados.
```

---

## SPRINT 3 — PIX_CODE_MAP (diagnóstico real de falhas de Pix)

```
SPRINT 3 DE 7 — Vocabulário real de erros de Pix no classificador

PROBLEMA: _features_pix (workflow.py) mapeia TODA falha de Pix para um único
gateway_error_code = "insufficient_funds" (CAUSA_PIX_FALHA). O classificador
e o dunning usam vocabulário do Stripe (expired_card, do_not_honor, ...). Com
isso o diagnóstico de Pix é cego aos tipos reais de falha (limite excedido do
pagador, autorização revogada, erro do PSP, saldo insuficiente).

IMPLEMENTAR:

1. Criar um PIX_CODE_MAP (em workflow.py ou num módulo dedicado
   crai/crai/agent/pix_codes.py) que traduz os códigos/estados de falha de
   Pix Automático do Pagar.me para o vocabulário interno de failure_cause.
   Cobrir ao menos:
     - saldo insuficiente          → "insufficient_funds"  (retentável)
     - limite do pagador excedido  → "limit_exceeded"      (NÃO retentável;
                                      o cliente precisa aumentar o teto)
     - autorização revogada        → "authorization_revoked" (NÃO retentável;
                                      contato para reautorizar)
     - erro técnico do PSP         → "processing_error"    (retentável)
   Mapear qualquer código desconhecido para um default seguro documentado.

2. Em _features_pix: usar o PIX_CODE_MAP para derivar gateway_error_code a
   partir do evento normalizado (o adapter de entrada precisa carregar o
   código/estado de falha do Pagar.me até aqui — se o parse_pix_event atual
   não o preserva, estender o schema normalizado de forma ADITIVA para incluir
   um campo de código de falha, sem remover os cinco campos atuais e sem
   deixar a chave Pix entrar).

3. Atualizar CAUSAS_RETENTAVEIS em workflow.py para refletir o novo
   vocabulário: "insufficient_funds" e "processing_error" continuam
   retentáveis; "limit_exceeded" e "authorization_revoked" NÃO são (vão para
   mensagem personalizada). Confirmar que a lógica de decide_recovery
   continua correta com os novos códigos.

4. Em dunning_engine.py: adicionar entradas em FALLBACK_TEMPLATES para os
   novos códigos de Pix (limit_exceeded, authorization_revoked) com mensagem
   adequada (ex.: pedir para aumentar o limite do Pix Automático, ou
   reautorizar a cobrança recorrente). Manter os templates de cartão
   existentes para retrocompatibilidade.

GATE:
    cd crai && pytest tests/test_pix_codes.py -v   (criar)
    cd crai && pytest tests/test_agent_graph.py -v # decisão ainda correta
    cd crai && pytest tests/ -q
    python crai/test_pipeline.py
test_pix_codes: cada código do Pagar.me mapeia para o failure_cause certo;
limit_exceeded e authorization_revoked NÃO são retentáveis e caem em
mensagem_pagamento; insufficient_funds continua indo para retry. Mostre os
resultados.
```

---

## SPRINT 4 — Isolamento por tenant (paridade com o voluntário)

```
SPRINT 4 DE 7 — tenant_id no pipeline involuntário

PROBLEMA: o AgentState não tem tenant_id. O churn voluntário já foi isolado
por tenant; o involuntário não. Para a CRAI atender várias empresas com
segurança e para os dados de recuperação serem atribuíveis por cliente da
CRAI, o involuntário precisa da mesma dimensão.

IMPLEMENTAR:

1. crai/crai/agent/state.py: adicionar tenant_id: str ao AgentState.

2. crai/crai/api/app.py, _run_involuntary_pipeline: aceitar tenant_id e
   colocá-lo no state inicial. Extrair do header/payload do webhook
   /webhooks/pix-automatico (e do /webhooks/stripe, mesmo com cartão
   desativado, para consistência); se ausente, "default_tenant" (documentado
   como MVP). Propagar também nos endpoints /simulate/* do involuntário.

3. Propagar tenant_id ATÉ os pontos que já têm ou terão efeito externo:
   - update_roi_dashboard / register_recovery_cycle: incluir tenant_id como
     propriedade do deal (NÃO alterar register_retention_cycle do voluntário).
   - reenviar_cobranca_pix (Sprint 2) e o fechamento de ciclo (Sprint 1):
     repassar tenant_id para log/atribuição.
   - O dataset/log de ciclo, se existir, grava tenant_id (paridade com o que
     o voluntário faz).

4. NÃO introduzir lógica de decisão que dependa de tenant ainda (isso é
   RBAC/produto, fora do escopo do agente). Aqui tenant_id é só propagação e
   atribuição — o comportamento de recuperação é idêntico para todos os
   tenants nesta fase.

GATE:
    cd crai && pytest tests/test_tenant_involuntary.py -v  (criar)
    cd crai && pytest tests/ -q
    python crai/test_pipeline.py
test_tenant_involuntary: um evento com tenant_id chega ao registro de
recuperação com o tenant certo; ausência de tenant vira "default_tenant". O
comportamento de recuperação não muda entre tenants. Mostre os resultados e
confirme o voluntário intacto.
```

---

## SPRINT 5 — Configuráveis: custo de intervenção e success fee

```
SPRINT 5 DE 7 — Tirar custo e fee do hardcode

PROBLEMA: dois valores de negócio estão fixos no código:
  - check_anomaly (workflow.py): cost = 0.05 (custo do bot WhatsApp) fixo,
    usado no recálculo de e-Profit.
  - update_roi_dashboard (workflow.py): success fee = amount * 0.15 fixo.
Em produção, o fee varia por contrato/tenant e o custo de intervenção varia
por canal (WhatsApp vs boleto vs custo de PSP por tentativa).

IMPLEMENTAR:

1. Centralizar esses parâmetros num único lugar (ex:
   crai/crai/config.py ou constantes no topo do módulo, lidas de env com
   default):
     - CRAI_SUCCESS_FEE_PCT (default 0.15)
     - CRAI_CUSTO_INTERVENCAO_WHATSAPP (default 0.05)
     - (opcional) CRAI_CUSTO_TENTATIVA_PIX — custo por instrução reenviada ao
       Pagar.me, para refinar o e-Profit quando houver retentativa.
   Documentar que, no futuro, esses valores virão de configuração por tenant;
   por ora são globais via env.

2. Substituir os literais 0.05 e 0.15 pelas constantes. Não mudar o
   comportamento default (mesmos números), só torná-los configuráveis — a
   demo e os testes existentes devem continuar com os mesmos resultados.

3. Se o e-Profit passar a considerar o custo real da retentativa Pix (custo
   por tentativa × nº de tentativas planejadas), fazê-lo de forma que, sem a
   env configurada, o resultado seja idêntico ao atual (custo extra 0 por
   default) — nenhuma regressão silenciosa nos testes de e-Profit.

GATE:
    cd crai && pytest tests/test_config_pricing.py -v   (criar)
    cd crai && pytest tests/test_failure_classifier.py -v  # e-Profit não regrediu
    cd crai && pytest tests/ -q
    python crai/test_pipeline.py
test_config_pricing: com envs default, fee e custo batem os valores atuais;
com envs customizadas, os cálculos mudam de acordo. Mostre os resultados.
```

---

## SPRINT 6 — Captura de dados de ciclo + métricas de negócio

```
SPRINT 6 DE 7 — Log de recuperação para treino e agregação de resultados

PROBLEMA (Gaps 4, 5, 6, 7 da auditoria): o involuntário não grava em lugar
nenhum o histórico "falhou → features → decisão → resultado → custo". Sem
isso: (a) não há dado real para treinar o classificador depois (Gap 4/5);
(b) não se sabe a margem real por recuperação, pois recuperar na 3ª tentativa
custa 3× o PSP de recuperar na 1ª (Gap 6); (c) não há métrica agregada de
negócio (MRR recuperado, taxa de recuperação, custo médio) para a banca
(Gap 7). O churn voluntário já tem um log append-only de ciclo; este sprint
traz a paridade para o involuntário, com o ângulo financeiro embutido.

IMPLEMENTAR:

1. Log append-only de ciclo de recuperação (mesma ideia do voluntário):
   uma linha por ciclo, gravada quando o ciclo se resolve (recuperado ou
   perdido). Local: data/ (SQLite ou Parquet), com tenant_id já previsto
   (Sprint 4). Cada linha deve conter, no mínimo:
     - tenant_id, customer_id (ou o id opaco), e2e_id
     - features usadas no diagnóstico (as 11 + LTV) e o failure_cause
       (já no vocabulário PIX_CODE_MAP do Sprint 3)
     - decisão do agente (estrategia) e nº de tentativas planejadas/usadas
     - resultado real (recovered True/False — vindo do webhook do Sprint 1)
     - custo acumulado real do ciclo (nº de tentativas Pix × custo por
       tentativa + custo de mensagem, usando as constantes configuráveis do
       Sprint 5) e o success fee efetivo
   Escrever esse registro no ponto onde o ciclo se fecha: o
   update_roi_dashboard (para o caso perdido/enviado) e o fechamento de ciclo
   do Sprint 1 (para o caso recuperado). Idempotente por e2e_id.

2. Este log é a FONTE DE TREINO do classificador (Gap 4/5): documentar que o
   par (features, recovered) é exatamente o dataset supervisionado para
   retreinar o FailureClassifier com dados reais depois. O README do Sprint 7
   referencia este arquivo como origem dos dados de treino de produção.

3. Métricas agregadas de negócio (Gap 7): criar uma função/endpoint de
   resumo (ex: GET /metrics/recovery?tenant_id=...&desde=...) que agrega do
   log: MRR recuperado no período, taxa de recuperação (recuperados /
   ciclos), custo total de operação, custo médio por recuperação e margem
   (fee − custo). Em modo simulação, o test_pipeline deve conseguir imprimir
   esse resumo ao final da demo — é o número que sustenta o modelo de
   Outcome-as-a-Service na banca.
   (Este endpoint NÃO é o dashboard do cliente — é agregação de dados; o
   dashboard/CRM é a sua frente separada. Aqui é só o dado consultável.)

GATE:
    cd crai && pytest tests/test_recovery_log.py -v   (criar)
    cd crai && pytest tests/ -q
    python crai/test_pipeline.py
test_recovery_log: um ciclo recuperado grava uma linha com recovered=True,
custo acumulado e fee corretos; um ciclo perdido grava recovered=False; o
mesmo e2e_id não duplica linha; a função de métricas agrega corretamente
(taxa de recuperação e custo médio batem os valores do dataset de teste).
A demo deve imprimir o resumo de negócio ao final. Mostre os resultados.
```

---

## SPRINT 7 — Prontidão para treino com dados reais

```
SPRINT 7 DE 7 — Perfil real plugável + documentação de treino

PROBLEMA: _perfil_simulado (workflow.py) gera tenure/payment_history/
failure_count/LTV com np.random. Isso alimenta o classificador (Módulo 1) com
features FABRICADAS. Para o agente estar pronto para treino/produção, o perfil
precisa poder vir de uma fonte real (banco/CRM) sem reescrever o pipeline —
mantendo o sintético como fallback enquanto não há dados reais.

Objetivo enxuto (horizonte TCC): NÃO treinar nem conectar banco agora. Só
criar o ponto de extensão e documentar o formato, para a fase de treino ser
só "plugar".

IMPLEMENTAR:

1. Refatorar a obtenção do perfil em workflow.py para passar por um provedor
   plugável:
     - PerfilProvider com um método get_perfil(customer_id, invoice_amount)
       -> dict (as mesmas chaves que _perfil_simulado retorna hoje).
     - Implementação default: SyntheticPerfilProvider (o _perfil_simulado
       atual, comportamento idêntico — mesmos valores para mesma seed).
     - Deixar preparado um DBPerfilProvider (stub) que, no futuro, buscaria o
       perfil real; se não configurado, cai no sintético. Mesmo padrão
       load()/fallback já usado nos módulos de ML.
   Sem fonte real configurada, o resultado é IDÊNTICO ao de hoje (não mudar
   comportamento observável nem quebrar reprodutibilidade por seed).

2. Garantir que a fórmula de LTV é única (hoje aparece em _perfil_simulado e
   no classificador). Centralizar num único ponto para não divergirem, sem
   mudar o valor calculado. Documentar.

3. Criar crai/crai/agent/README_treino.md documentando:
   - As 11 features + LTV que o classificador consome, e de onde cada uma
     viria em produção (evento Pagar.me vs banco/CRM).
   - Que o perfil hoje é sintético e como plugar o provedor real.
   - Que o vocabulário de failure_cause de Pix agora segue o PIX_CODE_MAP
     (Sprint 3), e o mapeamento código Pagar.me → failure_cause.
   - QUAL É A FONTE DE DADOS DE TREINO: o log append-only de ciclo criado no
     Sprint 6 (data/), cujo par (features, recovered) é o dataset
     supervisionado para retreinar o FailureClassifier. Passo a passo de como
     ler esse log e alimentar o train() do classificador.
   - Limitações conhecidas para a banca: perfil sintético; e-Profit usa custo
     configurável; label de "recuperado" depende do webhook de confirmação
     do Sprint 1 (não é inferido); persistência de retry_count ainda é
     memória/arquivo (Gap 2) até o DB entrar.
   Este README é o guia que EU seguirei na fase de treino.

GATE:
    cd crai && pytest tests/test_perfil_provider.py -v   (criar)
    cd crai && pytest tests/ -q
    python crai/test_pipeline.py
test_perfil_provider: SyntheticPerfilProvider reproduz exatamente os valores
atuais para a mesma seed; a interface aceita um provider alternativo (mock)
sem quebrar o pipeline. Mostre os resultados.
```

---

## Observação final

Ordem de dependência: 1 → 2 → 3 → 4 → 5 → 6 → 7.
- Sprints 1 e 2 fecham os furos estruturais (confirmação de pagamento +
  execução da retentativa, com stub de agendador para as tentativas 2 e 3) —
  é o que faz o agente "fechar o ciclo" de verdade, não só em simulação.
- Sprint 3 dá diagnóstico real de Pix (sai do vocabulário Stripe).
- Sprint 4 traz paridade multi-tenant com o voluntário.
- Sprint 5 tira custo e fee do hardcode.
- Sprint 6 grava o log de ciclo (fonte de treino) + métricas de negócio
  (MRR recuperado, taxa, custo médio, margem) — fecha o loop de dados e o
  ângulo financeiro para a banca.
- Sprint 7 deixa o perfil plugável e documenta tudo para a fase de treino.

Esta versão incorpora os 7 gaps da auditoria (eng/IA/negócio):
  Gap 1 (agendador das tentativas 2 e 3) → stub no Sprint 2.
  Gap 2 (retry_count em memória / MemorySaver) → isolado atrás de camada
        trocável + documentado como dependência de DB, no Sprint 2.
  Gap 3 (idempotência do webhook de falha) → Sprint 1.
  Gap 4 e 5 (captura de dados + label para treino) → Sprint 6.
  Gap 6 (custo real acumulado por ciclo) → Sprint 5 (constantes) + Sprint 6
        (registro do custo acumulado por ciclo).
  Gap 7 (métricas agregadas de negócio) → Sprint 6.

Nada aqui reativa cartão (segue em legacy_card/), altera o churn voluntário,
ou finaliza o HubSpot (sua frente de CRM). O executor de retentativa liga no
Pagar.me com modo simulado por default — nenhuma credencial é necessária para
a banca; basta setar CRAI_PAGARME_LIVE=1 + chave quando for para produção.

Duas limitações ficam CONSCIENTEMENTE fora do escopo (documentadas para a
banca, não escondidas): a persistência real de retry_count/planos (depende do
PostgreSQL — outra frente) e o scheduler temporal de produção (cron/worker —
infra). Os dois têm ponto de troca pronto no código; o que muda depois é a
implementação por trás, não o resto do pipeline.

Após os 7 sprints, o involuntário estará: fechando ciclo com confirmação real
de pagamento, disparando as 3 tentativas do BACEN via Pagar.me, diagnosticando
falhas reais de Pix, isolado por tenant, com custo/fee configuráveis, gravando
o histórico para treino, expondo métricas de negócio, e com o perfil pronto
para receber dados reais.
```

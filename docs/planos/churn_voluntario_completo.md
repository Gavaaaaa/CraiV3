# Prompt em Sprints (v2 — revisado) — Churn Voluntário pronto para o mercado

> Revisão crítica sobre a v1: caminhos de arquivo corrigidos, estrutura real
> do bandit_state.json refletida, assinaturas de métodos que mudam sinalizadas,
> mudança de topologia do grafo explicitada, e Sprint 6 simplificado para o
> horizonte do TCC. Verificado contra o código real do repositório.
>
> Como usar: cole "CONTEXTO" uma vez. Depois um sprint por vez, na ordem,
> validando o gate de cada um antes do próximo.

---

## CONTEXTO (colar uma vez)

```
Você está no repositório da CRAI (github.com/Gavaaaaa/crai). Esta sessão
trata EXCLUSIVAMENTE do agente de churn voluntário, deixando-o pronto para
o mercado e para treino posterior com dados reais. O churn involuntário
(crai/agent/, crai/dunning/) já está pronto e NÃO pode mudar de
comportamento.

ESTRUTURA REAL DE PASTAS (confirmada — atenção aos caminhos):
- Raiz do pacote: crai/crai/
- Agente voluntário: crai/crai/churn_voluntary/
    voluntary_agent.py, risk_scorer.py, offer_bandit.py, state.py
- API: crai/crai/api/app.py
- Integrações: crai/crai/integrations/ (hubspot_crm.py etc.)
- Testes: crai/crai/tests/ (pytest; já há test_agent_graph.py,
    test_payment_isolation.py, etc. — todos do involuntário)
- Pipeline de demo: crai/test_pipeline.py (NA RAIZ crai/, não em crai/crai/)
- Persistência de modelos/bandit: crai/models/ (no .gitignore)

FATOS DO CÓDIGO REAL (não assuma outra coisa):
- offer_bandit.py: self.state tem a forma
      { profile: { offer: {"alpha": float, "beta": float} } }
  (dicionário com chaves "alpha"/"beta" — NÃO tuplas). Já existem os
  métodos: load(), choose_offer(profile, risk_score, mrr=None),
  record_outcome(profile, offer, accepted), conversion_rates(profile),
  _persist(). OFFERS inclui hoje "consulta_cs". SEED_PRIORS usa tuplas
  (aceites, recusas) que o load() converte para o formato alpha/beta.
- choose_offer() hoje começa com: if risk_score >= 0.90: return
  "consulta_cs"  ← atalho a remover.
- voluntary_agent.py: grafo LangGraph assess_risk → choose_offer →
  choose_channel → generate_message → send_offer → track_outcome →
  update_crm. send_offer marca escalated_to_human. track_outcome usa
  random.random() para simular aceite. Canais atuais: popup, email.
- state.py: ChurnVoluntaryState tem escalated_to_human: bool.
- app.py: _run_voluntary_pipeline monta o estado inicial (contém
  "escalated_to_human": False) e chama voluntary_churn_agent.ainvoke.
  Webhook em /webhooks/segment (assinatura obrigatória, já validada).
- NÃO existe teste do grafo voluntário hoje (test_agent_graph.py é do
  involuntário). Ou seja, os testes do voluntário serão criados do zero.

INVARIANTE DE PRODUTO (não-negociável): a CRAI é 100% autônoma, NUNCA
escala para humano.

CONVENÇÕES: logs em português com prefixo [CHURN-VOL] (ou [BANDIT] no
bandit); métrica de decisão é e-Profit; LangGraph (StateGraph+END);
TypedDict no state; funções async nos nós; teste correspondente em
crai/crai/tests/ para todo código novo.

REGRA DE OURO: ao fim de cada sprint rodar o gate + `python
crai/test_pipeline.py` (a partir da raiz crai/) e me mostrar o
resultado. Se QUALQUER teste do churn involuntário quebrar, PARE e me
avise — não conserte o involuntário por conta própria.

CS (importante p/ CRM depois): NÃO se preocupe em finalizar o HubSpot
nesta sessão. Onde os sprints tocarem register_retention_cycle, apenas
mantenha o comportamento atual funcionando (registro do deal); o CRM
será tratado por mim numa frente separada. Nunca altere
register_recovery_cycle (é do involuntário).

Confirme que entendeu os fatos do código real e o isolamento entre os
dois churns antes de eu enviar o Sprint 1.
```

---

## SPRINT 1 — Autonomia total (remover escalação humana)

```
SPRINT 1 DE 6 — Remover consulta_cs e escalated_to_human

Referência de arquivos reais: crai/crai/churn_voluntary/offer_bandit.py,
voluntary_agent.py, state.py; crai/crai/api/app.py; crai/test_pipeline.py.

IMPLEMENTAR:

1. offer_bandit.py:
   - Remover "consulta_cs" de OFFERS.
   - Remover a chave "consulta_cs" de cada perfil de SEED_PRIORS.
   - Remover "consulta_cs": 250.0 de offer_cost().
   - Em choose_offer(), remover as duas primeiras linhas:
         if risk_score >= 0.90:
             return "consulta_cs"
     Depois disso choose_offer roda Thompson Sampling para qualquer
     risk_score. NÃO alterar a assinatura do método neste sprint.
   - Robustez do load(): se o bandit_state.json persistido já contiver
     "consulta_cs" em algum profile (formato real:
     profile -> "consulta_cs" -> {"alpha":..,"beta":..}), o load() deve
     descartar essa chave ao carregar, sem quebrar. As observações de
     consulta_cs são simplesmente ignoradas (documentar como decisão);
     não precisa redistribuir. Os demais offers permanecem intactos.
   - Adicionar, fora da lógica de escolha:
         CRITICAL_RISK_THRESHOLD = 0.90
         def is_critical_risk(risk_score: float) -> bool:
             """Sinaliza criticidade só para ajustar o TOM da mensagem,
             nunca para desviar a oferta ou acionar humano."""
             return risk_score >= CRITICAL_RISK_THRESHOLD
     Atualizar a docstring de choose_offer removendo a menção a
     "escala direto para humano".

2. state.py: remover escalated_to_human; adicionar is_critical: bool.

3. voluntary_agent.py:
   - Import: from .offer_bandit import OfferBandit, is_critical_risk
   - Remover a entrada "consulta_cs" de OFFER_LABELS.
   - choose_offer(): incluir is_critical =
     is_critical_risk(state["risk_score"]) no dict retornado.
   - send_offer(): remover a linha escalate = ... e o campo
     "escalated_to_human" do dict retornado (retornar só offer_sent).

4. app.py, dentro de _run_voluntary_pipeline: no dict do estado inicial,
   trocar "escalated_to_human": False por "is_critical": False.

5. crai/test_pipeline.py: se houver qualquer contagem/print de
   "escalados para humano" no bloco do voluntário, trocar por contagem de
   is_critical (rotular como "sinalizados como críticos (tom ajustado)").
   Se o pipeline afirmar/validar escalação humana em algum ponto, essa
   verificação deve passar a exigir ZERO escalação.

6. Criar crai/crai/tests/test_voluntary_autonomy.py:
   - test_nunca_oferece_consulta_cs: instanciar OfferBandit e rodar
     choose_offer() para os três perfis ("CLT","PJ","freelancer") com
     risk_score de 0.0 a 1.0 em passos de 0.05; falhar se "consulta_cs"
     for retornado. Documentar como INVARIANTE permanente.
   - test_state_sem_escalated_to_human: via
     ChurnVoluntaryState.__annotations__, garantir que a chave não existe
     mais e que "is_critical" existe.
   - test_is_critical_risk_threshold: is_critical_risk(0.89) False,
     is_critical_risk(0.90) True.

GATE:
    cd crai && pytest crai/tests/test_voluntary_autonomy.py -v
    cd crai && pytest crai/tests/ -q          # nada do involuntário pode quebrar
    python crai/test_pipeline.py
Nenhum log pode mencionar consulta_cs ou escalated_to_human. Mostre os
três resultados.
```

---

## SPRINT 2 — Tom adaptativo por criticidade

```
SPRINT 2 DE 6 — Mensagem com tom ajustado ao risco/valor do cliente

IMPLEMENTAR:

1. risk_scorer.py: adicionar
       def classify_criticality(risk_score: float, mrr: float | None) -> str
   retornando "critico" (risk_score >= 0.90 OU mrr is not None e mrr >=
   limiar de env CRAI_HIGH_VALUE_MRR_THRESHOLD, default 2000.0),
   "alto" (0.75 <= risk_score < 0.90), senão "padrao".

2. state.py: adicionar criticality: str.

3. voluntary_agent.py:
   - No nó assess_risk, após calcular risk e profile, calcular
     criticality = classify_criticality(risk, state["props"].get("mrr"))
     e incluí-lo no dict retornado. (assess_risk já tem acesso a props.)
   - Em generate_message(), ramificar o prompt enviado ao Claude conforme
     state["criticality"]:
       * "critico": tom pessoal e de alto cuidado, reconhecendo o valor
         da relação; oferta como solução pensada para aquele cliente. Se
         env CRAI_CS_SIGNATURE_NAME estiver setada, assinar com esse nome
         ("— {nome}, time CRAI"); se não, não inventar nome.
       * "alto": tom atencioso e proativo, oferta personalizada com prazo.
       * "padrao": manter o prompt atual.
   - O fallback (except da Claude API) também deve ter uma variante mais
     cuidadosa quando criticality == "critico".
   - Não mudar o modelo nem max_tokens da chamada existente.

GATE:
    cd crai && pytest crai/tests/test_voluntary_tone.py -v   (criar)
    python crai/test_pipeline.py
O teste cobre: classify_criticality nos três níveis (incluindo o caso de
mrr alto com risco baixo → "critico"); generate_message monta prompts
distintos por nível (mockar claude.messages.create e inspecionar o prompt
enviado). Mostre os resultados.
```

---

## SPRINT 3 — Canal WhatsApp (sem alterar o comportamento do involuntário)

```
SPRINT 3 DE 6 — Adicionar WhatsApp como canal do churn voluntário

Contexto: o WhatsApp de envio hoje vive no dunning involuntário
(crai/crai/dunning/dunning_engine.py). O voluntário só tem popup/email.

REGRA DE ISOLAMENTO: escolha a opção de MENOR risco para o involuntário e
me diga qual escolheu e por quê:
   (Opção limpa) extrair o envio de WhatsApp para
     crai/crai/integrations/whatsapp_sender.py e fazer o dunning
     involuntário passar a chamá-lo, sem mudar seu comportamento
     observável (todos os testes do involuntário continuam verdes); OU
   (Opção conservadora) criar whatsapp_sender.py novo e usá-lo APENAS no
     voluntário, deixando o involuntário intacto, com uma nota de
     duplicação temporária a unificar depois.

IMPLEMENTAR:

1. Criar crai/crai/integrations/whatsapp_sender.py com
       async def send_whatsapp(to: str, message: str,
                               tenant_id: str | None = None) -> dict
   encapsulando o envio. No MVP pode simular (como o resto do sistema faz
   hoje), mas com docstring marcando o ponto de integração real
   (BSP / WhatsApp Cloud API).

2. voluntary_agent.py, choose_channel(): incluir "whatsapp" como canal.
   Política sugerida: se props tiver telefone/whatsapp disponível E
   state["criticality"] in ("critico","alto") → "whatsapp"; senão manter
   a lógica atual (histórico → popup se on_site → email). Preservar a
   memória _channel_history existente.

3. send_offer(): quando channel == "whatsapp", chamar
   send_whatsapp(...); para os demais canais, manter o comportamento atual
   (print simulado).

GATE:
    cd crai && pytest crai/tests/ -v      # suíte inteira: prova que o
                                          # involuntário não quebrou
    cd crai && pytest crai/tests/test_whatsapp_channel.py -v   (criar)
    python crai/test_pipeline.py
test_whatsapp_channel: choose_channel escolhe whatsapp nas condições
certas; send_offer chama send_whatsapp quando channel=="whatsapp"
(mock/spy). Confirme explicitamente que os testes do involuntário seguem
verdes e diga qual opção de isolamento escolheu.
```

---

## SPRINT 4 — Resultado real (substituir o random e ajustar o grafo)

```
SPRINT 4 DE 6 — Fechar o loop de aprendizado com resultado real

PROBLEMA: track_outcome() usa random.random(), então o bandit aprende com
dado inventado. Para produção e para treino real, o resultado precisa vir
de um sinal real. ATENÇÃO: isto MUDA A TOPOLOGIA DO GRAFO — em produção o
fluxo termina após o envio e o resultado chega depois, de forma assíncrona.

IMPLEMENTAR:

1. app.py: criar POST /webhooks/retention-outcome recebendo user_id,
   offer_type, profile, accepted (bool) e, se houver, tenant_id. Validar
   assinatura reaproveitando o mesmo padrão HMAC já usado nos outros
   webhooks (verify_segment_signature ou o verificador genérico
   existente). Ao receber: chamar _bandit.record_outcome(profile,
   offer_type, accepted) e atualizar o HubSpot para retained/churned via
   register_retention_cycle (mantendo o comportamento atual do método).

2. voluntary_agent.py — dois modos, controlados por env
   CRAI_SIMULATE_OUTCOMES:
   - MODO PRODUÇÃO (env ausente/0): o grafo NÃO passa por um
     track_outcome que sorteia. O fluxo do grafo deve terminar em
     update_crm logo após send_offer, com o estado marcado como
     "aguardando retorno" (accepted=None, retained=False). O aprendizado
     do bandit acontece depois, via webhook do item 1. Ajustar as arestas
     do StateGraph de acordo (remover a passagem obrigatória por um nó de
     sorteio no caminho de produção).
   - MODO SIMULAÇÃO (env==1): manter o comportamento atual de
     track_outcome com random(), para test_pipeline e demos continuarem
     reprodutíveis sem depender de webhook externo.
   Documentar os dois modos no docstring do módulo.

3. Deduplicação simples: record_outcome não deve contar o mesmo resultado
   duas vezes se o webhook reenviar (ex: memória curta de (user_id,
   offer_type) processados recentemente). Documentar como MVP.

4. crai/test_pipeline.py: garantir que roda em MODO SIMULAÇÃO (setar a env
   internamente no início do script), mantendo a demo reprodutível.

GATE:
    cd crai && pytest crai/tests/test_retention_outcome.py -v   (criar)
    python crai/test_pipeline.py
test_retention_outcome: webhook rejeita sem assinatura; webhook válido
chama record_outcome com os valores certos; em MODO PRODUÇÃO o grafo NÃO
sorteia resultado (accepted permanece None ao fim do grafo). Mostre os
resultados.
```

---

## SPRINT 5 — Isolamento por tenant no aprendizado

```
SPRINT 5 DE 6 — Separar bandit e histórico de canal por tenant

PROBLEMA: hoje self.state do bandit é { profile: { offer: {alpha,beta} } }
— global, mistura empresas clientes diferentes. Precisa virar
{ tenant: { profile: { offer: {alpha,beta} } } }.

ATENÇÃO — MUDAM ASSINATURAS: choose_offer, record_outcome e
conversion_rates passam a receber tenant_id. TODAS as chamadas a esses
métodos em voluntary_agent.py precisam ser atualizadas junto, no mesmo
sprint, senão o pipeline quebra.

IMPLEMENTAR:

1. state.py: adicionar tenant_id: str.

2. app.py, _run_voluntary_pipeline: aceitar tenant_id (do header/payload
   do webhook /webhooks/segment; se ausente, "default_tenant"
   documentado como MVP) e colocá-lo no estado inicial. Atualizar também
   /simulate/churn-risk para passar um tenant (pode ser "default_tenant").

3. offer_bandit.py:
   - self.state passa a ter a camada de tenant no topo:
     { tenant: { profile: { offer: {"alpha","beta"} } } }.
   - choose_offer(tenant_id, profile, risk_score, mrr=None),
     record_outcome(tenant_id, profile, offer, accepted),
     conversion_rates(tenant_id, profile) — todas recebem tenant_id e
     operam dentro do sub-dicionário daquele tenant (criando-o a partir
     de SEED_PRIORS/priors neutros na primeira vez que o tenant aparece).
   - RETROCOMPATIBILIDADE no load(): se o arquivo persistido estiver no
     formato antigo (sem camada de tenant — detectável porque os valores
     do topo têm chaves de profile como "CLT"/"PJ"/"freelancer"), migrar
     todo o conteúdo para dentro de "default_tenant" ao carregar, sem
     erro.

4. voluntary_agent.py: propagar state["tenant_id"] em TODAS as chamadas ao
   bandit (choose_offer, record_outcome via webhook/simulação,
   conversion_rates). O _channel_history passa a usar chave composta
   f"{tenant_id}:{user_id}".

5. register_retention_cycle (hubspot): incluir tenant_id como propriedade
   do deal. NÃO alterar register_recovery_cycle.

GATE:
    cd crai && pytest crai/tests/test_tenant_isolation_voluntary.py -v (criar)
    cd crai && pytest crai/tests/ -q
    python crai/test_pipeline.py
test_tenant_isolation_voluntary: dois tenants com o mesmo profile aprendem
posteriores independentes (um record_outcome em tenant A não muda
conversion_rates de tenant B); um bandit_state.json no formato antigo
migra para default_tenant sem erro. Mostre os resultados e confirme o
involuntário intacto.
```

---

## SPRINT 6 — Prontidão mínima para treino (simplificado para o TCC)

```
SPRINT 6 DE 6 — Deixar risco e dados prontos para treino real

Objetivo enxuto (horizonte TCC): NÃO treinar nada agora; só garantir que
(a) o cálculo de risco pode receber um modelo treinado depois sem
reescrever o pipeline, e (b) o formato de dados que o voluntário consome
está documentado, para a fase de treino ser só "plugar".

IMPLEMENTAR (mínimo viável, sem over-engineering):

1. risk_scorer.py: introduzir um ponto de extensão simples. Manter
   calculate_risk como está (regras fixas) mas fazê-lo consultar, no
   início, se existe um modelo treinado salvo em crai/models/ para risco
   voluntário; se existir, usá-lo; se não (caso atual), usar as regras
   fixas. Seguir EXATAMENTE o mesmo padrão load()/fallback já usado nos
   módulos de ML do involuntário (crai/crai/ml/). Sem modelo treinado, o
   resultado tem de ser idêntico ao de hoje (não mudar comportamento
   observável). Não criar uma hierarquia de classes elaborada se um
   try/load/fallback resolver.

2. Documentar em crai/crai/churn_voluntary/README_treino.md:
   - Features que o risco voluntário consome hoje (event,
     days_since_last, features_used_30d, billing_profile, mrr).
   - Formato em que o dataset real precisa chegar para treinar.
   - Passo a passo de como plugar o modelo treinado (onde salvar em
     crai/models/, como o load do item 1 o encontra).
   Este README é o guia que EU seguirei na fase de treino.

3. (Somente se necessário) se o gerador sintético em crai/crai/ml/
   synthetic_data.py não produzir as features acima no formato que o
   voluntário espera, estender de forma ADITIVA (sem remover/alterar as
   features usadas pelo involuntário). Se já produzir, apenas documentar o
   mapeamento no README. Esta é a única parte que toca crai/crai/ml/.

GATE:
    cd crai && pytest crai/tests/test_risk_pluggable.py -v   (criar)
    cd crai && pytest crai/tests/ -q
    python crai/test_pipeline.py
test_risk_pluggable: sem modelo salvo, calculate_risk reproduz
exatamente os valores atuais (mesmos casos do risk_scorer de hoje); com um
modelo mock salvo/injetado, calculate_risk usa o modelo sem quebrar o
pipeline. Mostre os resultados.
```

---

## Resumo da revisão (o que mudou da v1 para a v2)

- Caminhos corrigidos: pacote em crai/crai/, testes em crai/crai/tests/,
  test_pipeline.py na raiz crai/. (v1 usava crai/tests/ e crai/ soltos.)
- Estrutura real do bandit refletida: self.state é
  { profile: { offer: {"alpha","beta"} } } com dicts, não tuplas; Sprint 5
  agora descreve a migração para { tenant: { profile: {...} } } e a
  detecção do formato antigo.
- Assinaturas que mudam (Sprint 5: choose_offer/record_outcome/
  conversion_rates ganham tenant_id) agora estão sinalizadas com o aviso
  de atualizar todas as chamadas no mesmo sprint.
- Sprint 4 agora explicita que a mudança MEXE NA TOPOLOGIA do grafo (não é
  só editar uma função) e define os dois modos por env.
- Sprint 1 agora trata o load() encontrando "consulta_cs" no JSON
  persistido (descartar sem quebrar) e não mistura formato tupla/dict.
- Sprint 6 foi simplificado para try/load/fallback (sem hierarquia de
  classes), adequado ao horizonte do TCC.
- Confirmado que test_agent_graph.py é do involuntário — o Sprint 1 não o
  quebra, mas cada sprint roda a suíte inteira como rede de segurança.
```

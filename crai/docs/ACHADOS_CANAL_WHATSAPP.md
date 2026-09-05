# ACHADOS — Canal WhatsApp (registro, sem correção)

**Data:** 05/09/2026 · **Branch:** `sprint/a1-auditoria` · **HEAD:** `b38ba72`
**Origem:** verificação pedida antes do Sprint 1 do plano de churn voluntário
(`churn_voluntario_completo.md`), para responder se o WhatsApp já está integrado ao
churn involuntário.

**Mandato:** apenas REGISTRAR. Nenhum destes achados foi corrigido. Todos são
pré-existentes e vivem no lado **involuntário** (`dunning/`, `agent/`, `ml/`) ou na
documentação — fora do escopo dos 6 sprints do voluntário, que não podem mudar o
comportamento do involuntário.

---

## RESPOSTA À PERGUNTA: o WhatsApp NÃO está integrado

O que existe hoje é o **rótulo** do canal, não o envio:

- `dunning/dunning_engine.py:80` — `return {**state, "channel": "whatsapp", ...}`
- `dunning/dunning_engine.py:115` — `channel="whatsapp"` no estado inicial de `run_campaign`
- `dunning/dunning_engine.py:110` — `_send_message` é um `print(...)`; retorna `sent: True`

Não há cliente de BSP, não há WhatsApp Cloud API, não há número de destino, não há
variável de ambiente, não há dependência em `requirements.txt`. O envio é simulado, como
o resto do MVP.

**Consequência para o Sprint 3:** não existe envio a extrair do involuntário, logo a
"Opção limpa" do plano (extrair para `integrations/whatsapp_sender.py` e fazer o dunning
chamá-lo) não tem o que extrair. Vale a **Opção conservadora**: `whatsapp_sender.py` novo,
consumido só pelo voluntário. Não há duplicação real a unificar depois — quando a
integração de verdade entrar, ela entra no `whatsapp_sender.py` e o dunning passa a
chamá-lo.

**A descrição do projeto confirma o WhatsApp como canal dos dois churns:**
`README.md:442` — `| Bot WhatsApp | 0,05 | Dunning + ofertas |`. "Ofertas" é o bandit do
churn voluntário.

---

## ACHADOS REGISTRADOS

| ID | Sev | O quê |
|---|---|---|
| **W-1** | 🟠 | O canal nunca é escolhido — é constante `"whatsapp"`, e o e-Profit é calculado para ele por hardcode |
| **W-2** | 🟠 | Não existe destinatário em lugar nenhum: nenhum estado carrega telefone |
| **W-3** | 🟡 | `ligacao_cs` (R$ 15,00) sobrevive na tabela de custos e no README — o canal humano, contra o invariante de autonomia. Nunca é selecionado |
| **W-4** | 🟡 | Os dois churns chamam modelos Claude diferentes |

---

### W-1 🟠 — O canal é constante, mas o sistema promete escolhê-lo

`dunning_engine.py:80` e `:115` fixam `"whatsapp"`. Nenhum caminho de código decide entre
canais. Em paralelo:

- `README.md:126` promete "Recomendação de canal ótimo" entre as saídas do sistema;
- `README.md:439-445` publica a tabela de 5 canais com custos;
- `ml/failure_classifier.py:45-51` implementa `INTERVENTION_COSTS` com os 5 canais;
- `ml/failure_classifier.py:368` — `predict(self, features, channel="bot_whatsapp")` aceita
  o canal e desconta o custo certo no e-Profit;
- **mas** `agent/workflow.py:134` — `cost = 0.05  # bot_whatsapp padrão` — recalcula o
  e-Profit com o custo do WhatsApp cravado, em vez de usar o canal do estado.

Ou seja: a matemática por canal existe e é testada (`tests/test_failure_classifier.py:160`,
`:301`), mas nenhum chamador de produção passa `channel`. O e-Profit sempre assume WhatsApp
e a "recomendação de canal ótimo" não é decidida em lugar nenhum.

Nota: o churn **voluntário** decide canal de verdade (`voluntary_agent.py:56-77`,
`choose_channel` com memória de histórico) — ainda que o ramo `risk_score >= 0.90` e o
`else` retornem os dois `"email"` (`:72-75`), o que torna a condição inócua. O Sprint 3
mexe nessa função e é a hora de resolver esse ramo morto — **no voluntário**.

### W-2 🟠 — Não existe destinatário

`DunningState` (`dunning_engine.py:34-44`) tem `customer_id`, `channel`, `message`,
`portal_link` — e nenhum campo de telefone ou endereço de destino. `ChurnVoluntaryState`
também não tem. Uma busca por `telefone|phone|msisdn|celular` em `crai/crai/` só acha
menções à **chave Pix** do pagador (`integrations/payment_gateway.py:39`,
`security/tokenization.py:5`), que é outra coisa e é dado cifrado e segregado por decisão
de privacidade.

Quando a integração real do WhatsApp entrar, falta a canalização ponta a ponta do
destinatário: webhook → estado → sender. Vale para os dois churns.

**Consequência para o Sprint 3:** a política proposta no plano — *"se `props` tiver
telefone/whatsapp disponível E `criticality` in ("critico","alto") → whatsapp"* — hoje lê
um campo que nunca chega. Precisa de decisão sobre a chave que o evento do Segment
carregaria (`props["phone"]`? `props["whatsapp"]`?) e sobre o comportamento na ausência
dela (proposta: degradar para o canal atual, sem erro).

### W-3 🟡 — `ligacao_cs` ainda existe na tabela de custos e no README

`ml/failure_classifier.py:49` — `"ligacao_cs": 15.00` · `README.md:445` — "Ligação CS
humano | 15,00 | Clientes de alto LTV". É o canal de escalação humana, contra o invariante
de produto "a CRAI é 100% autônoma, NUNCA escala para humano".

**Verificado: não é escalação viva.** Nenhum caminho de código o seleciona. As duas únicas
referências fora da tabela são testes que exercitam a matemática do e-Profit com um custo
alto (`tests/test_failure_classifier.py:293,301`) — o teste inclusive afirma que o canal
caro NÃO deve ser recomendado. É resíduo de tabela + documentação.

É o irmão involuntário do `consulta_cs` que o Sprint 1 remove do voluntário. Removê-lo
mexeria em `ml/` e num teste do involuntário — por isso fica registrado, não corrigido.

### W-4 🟡 — Modelos Claude divergentes entre os dois churns

- `dunning/dunning_engine.py:97` — `model="claude-sonnet-5"`
- `churn_voluntary/voluntary_agent.py:88` — `model="claude-sonnet-4-20250514"`

O voluntário está preso a um modelo antigo por id datado. O Sprint 2 proíbe explicitamente
mudar o modelo ("Não mudar o modelo nem max_tokens da chamada existente"), então fica
registrado para uma frente separada.

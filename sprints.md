# CRAI — Plano de Sprints até a Demo (03/09/2026)

> **Documento operacional para o Claude Code.** Leia inteiro antes de tocar em código.
> Cada sprint tem um **gate**: comando executável + critério objetivo. **Gate reprovado = sprint não terminou.** Não avance.
>
> **Repositório:** `github.com/Gavaaaaa/Crai` · **HEAD auditado:** `2956a69`
> **Autor do plano:** engenharia · **Data:** 31/08/2026 · **Demo:** quinta, 03/09/2026
> **Audiência da demo:** orientadores do curso técnico
> **Formato do output da demo:** terminal + relatório `JSON` + `Markdown` (sem dashboard, sem API ao vivo)

---

## 0. Estado real do projeto (medido, não presumido)

Tudo abaixo foi **executado** no HEAD `2956a69`, não inferido da leitura.

```
pytest tests/ -q     →  150 passed, 21 skipped, 7.70s     ✅
python test_pipeline.py →  roda de ponta a ponta, sem exceção  ✅
```

Os 21 skips são dos módulos que dependem de `torch`/`prophet` — não instalados. Consequência: **os três modelos de ML estão em fallback heurístico**, e a demo hoje mostraria isto:

```
[CLASSIFIER] Modelos não encontrados — execute train() primeiro
[ANOMALY]    PyTorch não instalado — usando heurística
[PAYDAY]     torch/prophet não instalados — usando heurística
[SHAP]       Modelo não treinado — usando heurística
```

**Leitura honesta:** o *esqueleto* (LangGraph, roteamento, política BACEN, isolamento de meio de pagamento, HubSpot) está funcionando e bem escrito. O que **não** está demonstrável é a camada de IA — ela nunca rodou com modelo treinado. É esse o buraco central deste plano, junto com a borda de entrada (parsing de webhook), que é onde a auditoria concentrou os P0.

---

## 1. Achados confirmados por reprodução

A auditoria que você trouxe está correta. Reproduzi cada item e **encontrei mais dois**. Evidência anexa em cada linha.

### 🔴 P0 — quebram a demo ou perdem dinheiro em silêncio

| ID | Local | Sintoma **reproduzido** |
|---|---|---|
| **P0-1** | `integrations/payment_gateway.py:166-177` | `_extrair_valor` faz `float()` no valor cru. Payload com `"valor": "299,90"` (pt-BR) → `ValueError: could not convert string to float: '299,90'` → HTTP 500 → tempestade de retry do PSP. Contradiz o contrato "parsing deliberadamente tolerante" declarado na docstring do módulo (linha 16). |
| **P0-2** | `payment_gateway.py:137` | `dados = raw_payload.get("data") or raw_payload`. Com `data: [{...}]` (lote) o parser não quebra — devolve `{'e2e_id': '', 'valor': 0.0, 'status': 'cobranca_falhada', 'ispb_pagador': '', 'id_recorrencia': ''}`. **Evento normalizado inteiro em default, e o pipeline roda como se fosse válido.** |
| **P0-3** | `payment_gateway.py:137` | Com `data: {}` o `or` cai no envelope: o parser lê `valor` do nível raiz e devolve `150.0`. Ora usa o envelope, ora não — comportamento depende do PSP mandar `data` vazio ou ausente. |
| **P0-4** | `payment_gateway.py:190-194` | O fallback de `_extrair_status` compara o status **cru do PSP** contra `PIX_STATUSES`, que só contém tokens internos em português. `status: "failed"` → nunca casa → `desconhecido` → `HTTP 200 {"pipeline": false}`. **Toda cobrança falhada de um PSP que não manda nome de evento some silenciosamente.** O branch é código morto. |
| **P0-5** | `payment_gateway.py:174-177` | Sem campo de valor conhecido → `valor = 0.0`, **sem log**. `invoice_amount=0` → LTV ≈ 0 → `eprofit ≤ 0` → `route_after_diagnosis` (`main_agent.py:36`) manda para `update_dashboard`. Churn involuntário legítimo é diagnosticado como R$ 0 e descartado sem rastro. |
| **P0-6** | `api/app.py:119` + `app.py:307` | `customer_id=evento["id_recorrencia"] or "rec_desconhecida"` vira `thread_id` do `MemorySaver` (compartilhado, `main_agent.py:105`). Dois pagadores com `id_recorrencia` vazio **colidem no mesmo checkpoint** — o segundo retoma o estado do primeiro. Além disso `_features_pix` (`workflow.py:291`) usa a string vazia como seed enquanto os demais usam `"rec_desconhecida"` → perfis sintéticos inconsistentes para o mesmo evento. |

### 🟠 P1 — erros de política (o sistema decide errado, sem falhar)

| ID | Local | Sintoma **reproduzido** |
|---|---|---|
| **P1-7** | `dunning/pix_automatico_retry.py:216-231` | `_ancorar_na_liquidez` devolve `[]` quando o payday previsto cai no último dia da janela mas em horário posterior ao `prazo_final`. `_escolher_datas` não tem fallback → `schedule()` devolve `[]`. **Cliente com previsão confiável e 6 dias livres recebe ZERO retentativas** e vai direto para dunning. Repro: vencimento 01/09 09h, payday previsto 08/09 21h → `tentativas: 0`. |
| **P1-8** | `pix_automatico_retry.py:226-231` | **NOVO — não estava na auditoria.** O ancoramento anda 1 dia por vez para frente e para no `prazo_final`, **jogando fora tentativas a que o recebedor tem direito**. Payday no dia 6 da janela de 7 → agenda 2 das 3. Isso não é hipótese: no `test_pipeline.py` de hoje, `RN_maria_001` com **0 tentativas usadas** recebe **2/3**, e o resumo da demo mostra isso na tela. É um erro visível e caro: cada tentativa descartada é receita não recuperada. |

### 🟡 P2 — sujeira que a banca enxerga

| ID | Local | Problema |
|---|---|---|
| **P2-9** | `test_pipeline.py:143-171` | **NOVO.** O resumo da demo *mente*. Imprime `Janela BACEN esgotada: 0/3` num run em que o cenário `RN_pedro_003` foi **desenhado** como esgotado (ele nunca chega em `schedule_retry_pix`, então `retry_exhausted` nunca vira `True`). E imprime `Clientes retidos 3/4` **e** `Escalados para CS humano 3/4` — as duas coisas ao mesmo tempo. Um orientador que ler a tela com atenção acha isso em 30 segundos. |
| **P2-10** | `agent/workflow.py:104` + `pix_automatico_retry.py:210` | Payday roda inferência **2× por recuperação**. E os outputs de `infer_payday` (`optimal_retry_at`, `confidence`, `profile_type`) não são lidos por nenhum nó ativo — a política consulta o modelo por conta própria. |
| **P2-11** | `integrations/hubspot_crm.py:62` | Duplica inline o padrão de digest md5 estável de `seed_por_cliente` (`ml/synthetic_data.py:29`) em vez de importá-lo. Mesma decisão de projeto escrita em dois lugares — se um mudar, a demo passa a dar ids diferentes só num deles. |
| **P2-12** | `main_agent.py:55` vs `workflow.py:136` | `route_after_decision` usa `state.get("payment_method")` (default `None`); `decide_recovery` usa default `"card"`. Dois defaults para o mesmo campo ausente. |
| **P2-13** | `payment_gateway.py:228-237` | `store_encrypted_pix_key` faz read-modify-write sem lock. Não é chamado em produção hoje — **P2, não P0**. |

---

## 2. Proveniência dos dados — LEIA E APROVE ANTES DE TREINAR

> Você pediu para saber **de onde vem a base real** antes de qualquer treino. Esta seção é o gate. Nada de `train_all.py` antes de o CEO aprovar esta seção.

### 2.1 A verdade desconfortável, dita primeiro

**Não existe base pública, brasileira ou estrangeira, com o rótulo que a CRAI precisa:** "esta cobrança falhada foi recuperada, sim ou não". Nenhum PSP publica isso. Quem tem esse dado são as operadoras de dunning, e é justamente o ativo comercial delas.

Portanto a arquitetura de dados é esta, e **é assim que deve ser apresentada aos orientadores**:

```
FEATURES  → calibradas em dados REAIS (marginais e sazonalidade medidas)
RÓTULO    → gerado por um MODELO CAUSAL DECLARADO, não aprendido
```

O que a demo prova: **o pipeline aprende, explica (SHAP) e decide sobre um sinal.** O que a demo **não** prova: que a previsão de recuperação vale em produção. Dizer isso na apresentação é força, não fraqueza — é o que separa um TCC sério de um que superajustou a própria regra e chamou de IA.

O código já assume essa postura hoje: `ml/synthetic_data.py:198` (`_calculate_recovery_probability`) é o modelo causal, escrito à mão, com ruído gaussiano deliberado para evitar separação perfeita. **O trabalho do Sprint 3 não é inventar isso — é calibrar os parâmetros que hoje são chutados, contra dados reais.**

### 2.2 Fonte A — âncora primária das 300 linhas reais

**Brazilian E-Commerce Public Dataset by Olist** — `kaggle.com/datasets/olistbr/brazilian-ecommerce`

- **O que é:** ~99.441 pedidos **reais** de um marketplace brasileiro, set/2016 a out/2018. É a maior base pública de transações de pagamento brasileiras com valor, meio de pagamento e timestamp por transação.
- **Arquivos usados:** `olist_order_payments_dataset.csv` (`order_id`, `payment_sequential`, `payment_type`, `payment_installments`, `payment_value`) + `olist_orders_dataset.csv` (`order_purchase_timestamp`) + `olist_customers_dataset.csv` (`customer_state`).
- **Papel na CRAI — três marginais, medidas, não chutadas:**
  1. **Valor** → substitui a lognormal hardcoded de `synthetic_data.py:118` (`mean=5.8, sigma=0.7`), que hoje é palpite.
  2. **Mix de meio de pagamento** → `boleto` / `credit_card` / `debit_card` / `voucher`. Sustenta com número real o argumento de que o boleto ainda pesa no Brasil.
  3. **Sazonalidade** → `hour_of_day`, `day_of_week` e `day_of_month` extraídos de `order_purchase_timestamp`. Substitui `peak_days = [5,10,15,20,30]` (`synthetic_data.py:110`) e `_hour_distribution()` (`:253`), ambos hoje inventados.
- **Licença:** CC BY-NC-SA 4.0 → **uso acadêmico OK; uso comercial NÃO.** Registrar no TCC e no `DATA_CARD.md`. Quando a CRAI for para produção, essa âncora precisa ser trocada por dados do cliente-piloto.
- **Acesso:** exige conta Kaggle. `kaggle datasets download -d olistbr/brazilian-ecommerce`
- **Limitação a declarar:** é e-commerce, não SaaS por assinatura. Não tem `tenure`, não tem recorrência, não tem rótulo de recuperação. **Serve como âncora de distribuição, jamais como ground truth.**

### 2.3 Fonte B — taxa-base de falha (BACEN SGS, aberto, ODbL)

- **Série 21084** — Inadimplência da carteira de crédito, pessoas físicas, total (% mensal)
- **Série 21129** — Inadimplência PF, cartão de crédito
- **Papel:** hoje `ERROR_CODE_PROBS` e a taxa de recuperação base (`p = 0.5`, `synthetic_data.py:212`) são números escolhidos. A série 21084 dá a **taxa-base real de inadimplência PF e sua sazonalidade mensal** para ancorar isso.
- **Acesso — aberto, sem chave, sem conta:**
  ```
  https://api.bcb.gov.br/dados/serie/bcdata.sgs.21084/dados?formato=json&dataInicial=01/01/2020
  ```
- **Licença:** Open Data Commons ODbL. Uso comercial permitido com atribuição.

### 2.4 Fonte C — contexto de mercado do Pix (BACEN dados abertos, ODbL)

- **Estatísticas do Pix** — `dadosabertos.bcb.gov.br/dataset/pix`
- **Papel:** mix PF/PJ, volume por natureza e finalidade, transações por município. Sustenta o **argumento de mercado** do TCC (adoção do Pix), não entra no treino.
- ⚠️ **Correção necessária:** essa base **não tem granularidade por hora do dia**. Se algum material da CRAI afirma sazonalidade horária do Pix citando o BACEN, está errado. A sazonalidade horária vem da **Fonte A** (Olist), e é de e-commerce — declarar como proxy.

### 2.5 Fonte D — âncora de tenure (opcional, se sobrar tempo)

- **IBM Telco Customer Churn** — 7.043 assinantes reais, com `tenure`, `MonthlyCharges`, `PaymentMethod` e rótulo de `Churn`.
- **Papel:** a Olist não tem assinatura, logo não tem `tenure`. Esta fonte ancora a distribuição de `tenure_months` (`synthetic_data.py:99-104`) e a relação `tenure ↔ churn`.
- **Status:** confirmar licença no Sprint 0. **Se a confirmação demorar mais de 20 min, corta.** É o item mais dispensável do plano.

### 2.6 Protocolo 300 reais → 15.000 sintéticos

```
[1] AMOSTRA        300 linhas da Fonte A, estratificadas por payment_type × faixa de valor
                   (quartis). Seed fixa. → data/real/amostra_300.csv + SHA-256 no DATA_CARD

[2] CALIBRAÇÃO     ajustar sobre as 300:
                     valor           → lognormal por MLE (substitui mean=5.8, sigma=0.7)
                     hora/dow/dom    → histograma empírico (substitui peak_days e _hour_distribution)
                     mix de meio pgto→ frequência observada
                     tenure          → Fonte D, ou mantém o atual e DECLARA como não-calibrado
                     taxa-base falha → Fonte B (série 21084)

[3] DEPENDÊNCIAS   preservar a matriz de Spearman das 300 via amostragem condicional
                   por estrato. NÃO usar cópula gaussiana — não cabe em 2 dias e não
                   é necessário para 12 features.

[4] EXPANSÃO       generate_dataset(n_samples=15000, seed=42)

[5] RÓTULO         _calculate_recovery_probability(), com os coeficientes de impacto
                   DOCUMENTADOS um a um em DATA_CARD.md como hipótese de negócio,
                   com a justificativa de cada sinal. Mantém o ruído N(0, 0.05) da linha 248.
```

**Regra inviolável do passo [5]:** nenhuma feature nova pode ser adicionada ao rótulo sem também ser adicionada ao gerador de features. Se o rótulo souber algo que as features não sabem, o modelo não aprende nada; se souber *exatamente* o que as features sabem sem ruído, o modelo decora a regra. O gate G3 mede as duas falhas.

---

## 3. Escopo — o que NÃO vamos fazer até quinta

Sprint de 3 dias morre por escopo, não por dificuldade. **Nada abaixo entra, mesmo que seja rápido:**

- ❌ Dashboard HTML, frontend, Next.js
- ❌ API ao vivo na demo (o runner determinístico é o output)
- ❌ Reativar recobrança de cartão (`dunning/legacy_card/`) — fica isolado como está
- ❌ Integração real com PSP, HubSpot real, Segment real
- ❌ Refatorar `churn_voluntary/` — está funcionando, não se mexe
- ❌ Cópula gaussiana, SMOTE, tuning de hiperparâmetro
- ❌ P2-13 (lock no vault) — não é chamado em produção
- ❌ Resolver o débito de `infer_payday` rodar 2× **como refactor**; só marcar (ver S2)

---

## 4. Os sprints

### Convenções

- Branch por sprint: `sprint/<n>-<slug>`, merge em `main` **só com o gate verde**.
- Todo comando roda de `crai/` (a raiz do pacote).
- **Se um gate reprovar:** pare, corrija dentro do mesmo sprint. Não abra o próximo. Se estourar o tempo-caixa, aplique o rollback declarado no sprint e siga — a demo é inegociável, o sprint não.
- Todo bug corrigido nasce com **teste de regressão que falha antes e passa depois**. Sem exceção.

---

### 🔵 SPRINT 0 — Congelar a linha de base e aprovar os dados
**Seg 31/08, noite · 1h30 · branch `sprint/0-baseline`**

**Objetivo:** ter um ponto de retorno confiável e a proveniência aprovada. Nenhuma correção de bug aqui.

**Tarefas**

1. `git tag baseline-pre-sprint 2956a69` — ponto de rollback absoluto.
2. Instalar o ambiente completo, **incluindo `torch` e `prophet`** (é isso que desliga os 21 skips):
   ```bash
   pip install -r requirements.txt
   pip install torch --index-url https://download.pytorch.org/whl/cpu
   ```
3. `pytest tests/ -q | tee docs/baseline_pytest.txt` — registrar o número de skips **depois** do torch. Espera-se que caia de 21 para perto de 0.
4. Baixar as Fontes A e B. Verificar acesso, tamanho, colunas.
5. Escrever `docs/DATA_CARD.md` com as seções 2.2–2.6 deste plano, preenchendo os números reais que você mediu (nº de linhas, período, hashes).
6. `pip freeze > requirements-lock.txt` — a demo tem que rodar amanhã igual a hoje.

**Gate G0 — todas verdadeiras:**
```bash
pytest tests/ -q                          # 150 passed, skips < 5
python -c "import torch, prophet; print('ok')"
test -f data/real/amostra_300.csv         # existe e tem 301 linhas com header
python -c "import pandas as pd; d=pd.read_csv('data/real/amostra_300.csv'); print(len(d), d.payment_type.value_counts())"
curl -s "https://api.bcb.gov.br/dados/serie/bcdata.sgs.21084/dados?formato=json" | head -c 200
```
- [ ] `docs/DATA_CARD.md` existe, com licença de cada fonte e SHA-256 da amostra
- [ ] **Aprovação humana do CEO na seção de proveniência** ← gate bloqueante, não pule

**Rollback:** se Kaggle não liberar em 30 min, usar **só** as Fontes B e C, e declarar no `DATA_CARD` que as marginais de valor permanecem não-calibradas. A demo sobrevive; a honestidade é obrigatória.

---

### 🔴 SPRINT 1 — Blindar a borda de entrada (P0-1 a P0-5)
**Ter 01/09, manhã · 3h30 · branch `sprint/1-borda`**

**Objetivo:** nenhum payload de PSP, por mais torto que seja, derruba a API ou entra no pipeline em silêncio. Arquivo único: `crai/integrations/payment_gateway.py`.

**Princípio de projeto:** o módulo se declara "deliberadamente tolerante" (docstring, linha 16). Tolerante **não** é o mesmo que silencioso. A regra nova é:

> Toda degradação é registrada com `logger.warning` **e** marcada no evento normalizado. O pipeline nunca recebe um default sem saber que é default.

**Tarefas**

1. **P0-1 — `_extrair_valor` tolerante.** Nova `_para_float(valor)` que trata: `None`; `float`/`int`; `"299,90"` (vírgula decimal); `"1.299,90"` (milhar pt-BR); `"R$ 299,90"`; `"299.90"`; string vazia. Nunca levanta — devolve `None` e loga.
2. **P0-5 — valor ausente é evento defeituoso, não R$ 0.** Adicionar ao schema normalizado um **sexto campo**:
   ```python
   "degradacoes": []   # lista de strings; vazia = evento íntegro
   ```
   Sem campo de valor → `valor = 0.0` **e** `degradacoes.append("valor_ausente")` **e** `logger.warning`. Em `api/app.py`, um evento com `"valor_ausente"` em `degradacoes` **não** entra no pipeline: retorna `HTTP 422` e loga. Melhor recusar que diagnosticar R$ 0.
   > ⚠️ O `DATA_CARD`/README dizem "o schema normalizado contém APENAS cinco campos". Atualize essa frase — o sexto campo é metadado de qualidade e **não carrega dado do pagador**, então a promessa de privacidade continua intacta. Diga isso na docstring.
3. **P0-2 / P0-3 — envelope determinístico.** Substituir `raw_payload.get("data") or raw_payload` por uma função explícita:
   - `data` é `dict` não-vazio → usa `data`
   - `data` é `list` → se tiver exatamente 1 item `dict`, usa esse item e loga `lote_de_1`; se tiver mais, **`HTTP 422` com `"lote_nao_suportado"`** (processar lote pela metade é pior que recusar)
   - `data` ausente, `{}`, `None`, ou outro tipo → usa `raw_payload` e registra `degradacoes.append("envelope_ausente")`
4. **P0-4 — matar o código morto do fallback de status.** Trocar `if bruto in PIX_STATUSES` por um mapa **de status crus de PSP** → status interno:
   ```python
   STATUS_CRU_MAP = {
       "failed": STATUS_COBRANCA_FALHADA, "declined": STATUS_COBRANCA_FALHADA,
       "rejected": STATUS_COBRANCA_FALHADA, "error": STATUS_COBRANCA_FALHADA,
       "paid": STATUS_COBRANCA_CONFIRMADA, "succeeded": STATUS_COBRANCA_CONFIRMADA,
       "approved": STATUS_COBRANCA_CONFIRMADA, "settled": STATUS_COBRANCA_CONFIRMADA,
       "authorized": STATUS_AUTORIZACAO_CONCEDIDA,
       "revoked": STATUS_AUTORIZACAO_REVOGADA, "cancelled": STATUS_AUTORIZACAO_REVOGADA,
   }
   ```
   Manter a checagem contra `PIX_STATUSES` **depois** (para o caso de o PSP já mandar token interno), mas o mapa cru vem primeiro. `desconhecido` continua existindo e continua logando — só deixa de ser o destino de todo evento de falha.

**Testes de regressão (escrever ANTES da correção, ver falhar):** ampliar `tests/test_payment_gateway.py` com uma classe `TestPayloadsHostis`, um teste por caso: `"299,90"`, `"R$ 1.299,90"`, `data:[{...}]`, `data:[{},{}]`, `data:{}`, `data:null`, `status:"failed"`, `status:"FAILED"`, valor ausente, payload `{}` vazio.

**Gate G1 — todas verdadeiras:**
```bash
pytest tests/test_payment_gateway.py -q       # 100% verde, ≥10 testes novos
pytest tests/ -q                              # nenhuma regressão: ≥150 passed
python test_pipeline.py                       # ainda roda ponta a ponta
```
- [ ] Nenhum payload da suíte hostil levanta exceção não tratada
- [ ] Todo default aplicado aparece em `degradacoes` **e** em `logger.warning`
- [ ] `grep -rn "or raw_payload" crai/` → **zero ocorrências**
- [ ] Cobertura de `payment_gateway.py` ≥ 90%: `pytest --cov=crai.integrations.payment_gateway`

**Rollback:** se o campo `degradacoes` estourar o tempo (ele toca `app.py` e os testes de contrato), entregue só as correções 1, 3 e 4 e faça o P0-5 virar `logger.error` sem mudar o schema. Perde-se o `HTTP 422`, não se perde a rastreabilidade.

---

### 🔴 SPRINT 2 — Isolamento de estado e janela BACEN (P0-6, P1-7, P1-8)
**Ter 01/09, tarde · 3h30 · branch `sprint/2-estado-janela`**

**Objetivo:** dois clientes nunca se misturam, e o recebedor nunca perde uma tentativa a que tem direito.

**Tarefas**

1. **P0-6 — `thread_id` nunca colide.** Em `api/app.py`, quando `id_recorrencia` vier vazio, derivar um id determinístico do próprio evento em vez do literal `"rec_desconhecida"`:
   ```python
   def _thread_id(evento: dict) -> str:
       rec = (evento.get("id_recorrencia") or "").strip()
       if rec:
           return rec
       base = f"{evento.get('e2e_id','')}|{evento.get('ispb_pagador','')}|{evento.get('valor',0)}"
       return "rec_anon_" + hashlib.sha256(base.encode()).hexdigest()[:16]
   ```
   Colisão só acontece se dois eventos forem realmente idênticos — aí compartilhar checkpoint é o comportamento correto. Logar `WARNING` sempre que cair no ramo anônimo.
2. **P0-6b — um único id.** `_features_pix` (`workflow.py:291`) usa `event.get("id_recorrencia", "")` como semente enquanto o resto usa `customer_id`. Passar a usar `state["customer_id"]`, para que o perfil sintético do mesmo evento seja um só.
3. **P1-7 + P1-8 — `_ancorar_na_liquidez` correta.** Duas mudanças em `pix_automatico_retry.py`:
   - **Nunca devolver lista vazia.** Ao final de `_escolher_datas`, se `datas` estiver vazio e ainda houver janela (`inicio <= prazo_final`), cair no `_distribuir_uniforme` e logar `[PIX-RETRY] ancoragem vazia — caindo para fallback uniforme`.
   - **Nunca desperdiçar tentativa.** Se o ancoramento para frente couber em menos que `restantes` dias, **preencher para trás** — os dias entre `inicio` e o dia previsto —, respeitando o intervalo mínimo de 1 dia e o `prazo_final`. Racional: uma tentativa antes do payday tem probabilidade menor, mas maior que zero; descartá-la tem probabilidade exatamente zero. As 3 são um direito regulatório, não um orçamento a economizar.
   - Ordenar as datas antes de devolver, para que `_validar` (`:288`) continue vendo a sequência crescente.
4. **P2-12 — um default só.** `main_agent.py:55` passa a usar `state.get("payment_method", "card")`, igual a `workflow.py:136`.
5. **P2-10 — marcar, não refatorar.** Adicionar um `# TODO(pós-demo): infer_payday roda 2× por recuperação` em `workflow.py:104` e um item no roadmap do README. **Não refatore agora** — o ganho é performance, o risco é quebrar o caminho feliz da demo.

**Testes de regressão:** em `tests/test_pix_automatico_retry.py`:
- payday no último dia da janela, hora posterior ao `prazo_final` → **3 tentativas**, origem `fallback_uniforme` (hoje: 0)
- payday no dia 6 de 7, `tentativas_usadas=0` → **3 tentativas** (hoje: 2)
- payday no dia 1, `tentativas_usadas=0` → 3 tentativas, todas ≥ `inicio`, todas ≤ `prazo_final`, intervalo ≥ 1 dia
- **invariante paramétrico:** para todo payday previsto de `inicio` até `prazo_final` e todo `tentativas_usadas ∈ {0,1,2,3}`, o total agendado é `min(3 - usadas, dias_disponíveis + 1)` e `_validar` nunca levanta

Em `tests/test_payment_isolation.py`: dois eventos com `id_recorrencia` vazio e valores diferentes → `thread_id` diferente.

**Gate G2 — todas verdadeiras:**
```bash
pytest tests/test_pix_automatico_retry.py tests/test_payment_isolation.py -q
pytest tests/ -q                              # ≥150 passed, zero regressão
python test_pipeline.py 2>&1 | grep "PIX-RETRY"
```
- [ ] No `test_pipeline.py`, `RN_maria_001` (0 usadas) mostra **3/3 tentativas**, não 2
- [ ] Nenhum `PixRetryPolicyViolation` em nenhum cenário
- [ ] Invariante paramétrico verde em todas as combinações
- [ ] `grep -n "rec_desconhecida" crai/` → só em comentário/log, nunca como `thread_id`

**Rollback:** se o preenchimento para trás (P1-8) gerar violação de invariante que não feche em 1h, entregue só o fallback do P1-7 e **declare o P1-8 no relatório de auditoria como dívida conhecida**. Melhor uma dívida documentada que um agendamento que viola o BACEN na frente da banca.

---

### 🟣 SPRINT A1 — AUDITORIA ADVERSARIAL #1
**Ter 01/09, fim do dia · 1h30 · branch `sprint/a1-auditoria`**

**Objetivo:** encontrar o que os Sprints 1 e 2 quebraram ou fingiram consertar.

**Protocolo — o ponto inteiro está aqui:**

> A auditoria **não pode rodar no mesmo contexto que escreveu o código.** Quem escreveu já se convenceu. Abra uma **sessão nova** do Claude Code (ou um subagente com contexto limpo) e entregue a ele **apenas**: o diff `baseline-pre-sprint..HEAD`, este documento, e o `DATA_CARD.md`. **Não** entregue as justificativas de quem implementou.

**Prompt do auditor (cole literalmente):**

```
Você é auditor de sistemas de pagamento. Não escreveu este código e não confia nele.
Recebe o diff de duas sprints que alegam corrigir 8 defeitos.

Sua tarefa NÃO é confirmar as correções. É encontrar onde elas falham.

Para CADA defeito alegado como corrigido:
  1. Construa um payload/estado que ainda o dispara. Rode. Mostre a saída.
  2. Se não conseguir, diga o que tentou e por que a correção resiste.
  3. Verifique se o teste de regressão realmente FALHA no commit anterior:
     git stash && pytest <teste> && git stash pop
     Um teste que passa antes e depois não testa nada.

Depois procure defeitos NOVOS introduzidos pelo diff, com atenção a:
  - o campo `degradacoes`: algum caminho o ignora e segue com default?
  - o novo `thread_id`: existe payload em que dois clientes distintos colidem?
  - o preenchimento para trás: existe combinação que agenda < 1 dia de intervalo,
    passa do prazo_final, ou excede 3 tentativas somadas às usadas?
  - alguma exceção nova que escape até o handler do FastAPI?

Entregue docs/AUDITORIA_01.md:
  | ID | Alegação | Veredito (CONFIRMADO/PARCIAL/FALSO) | Evidência executada | Gravidade |
Termine com um veredito único: LIBERADO ou BLOQUEADO para o Sprint 3.
Se BLOQUEADO, liste o mínimo necessário para liberar.
```

**Gate GA1:**
- [ ] `docs/AUDITORIA_01.md` existe, com **saída de comando real** em cada linha — não prosa
- [ ] Todo defeito P0/P1 tem veredito `CONFIRMADO`
- [ ] Todo teste de regressão comprovadamente falha no commit anterior
- [ ] Veredito final `LIBERADO`

**Se `BLOQUEADO`:** corrija dentro deste sprint e rode a auditoria **de novo, em contexto novo**. Não negocie com o auditor.

---

### 🟢 SPRINT 3 — Dados: 300 reais → 15.000 sintéticos calibrados
**Qua 02/09, manhã · 4h · branch `sprint/3-dados`**

**Pré-requisito bloqueante:** G0 aprovado pelo CEO **e** GA1 `LIBERADO`.

**Objetivo:** trocar os parâmetros chutados de `synthetic_data.py` por parâmetros **medidos**, e provar estatisticamente que o sintético se parece com o real.

**Tarefas**

1. `crai/ml/calibracao.py` — módulo novo:
   - `carregar_amostra_real(path) -> pd.DataFrame` (valida schema e as 300 linhas)
   - `ajustar_lognormal(valores) -> (mu, sigma)` por MLE
   - `histograma_empirico(serie) -> np.ndarray` para hora, dia-da-semana, dia-do-mês
   - `taxa_base_inadimplencia(serie_sgs) -> float`
   - `salvar_parametros(dict) -> crai/models/calibracao.json`
2. `crai/ml/synthetic_data.py` — passa a **ler** `calibracao.json`:
   - `mean=5.8, sigma=0.7` (`:118`) → parâmetros ajustados
   - `peak_days = [5,10,15,20,30]` (`:110`) → histograma empírico de `day_of_month`
   - `_hour_distribution()` (`:253`) → histograma empírico de hora
   - `ERROR_CODE_PROBS` → ancorado na taxa-base da série 21084
   - **Se `calibracao.json` não existir, cair nos valores atuais e imprimir `[SYNTH] MODO NÃO-CALIBRADO`.** O código nunca quebra por falta do arquivo, mas nunca finge estar calibrado.
3. `generate_dataset(n_samples=15000, seed=42)` — gerar, salvar em `data/synthetic/treino_15000.parquet`, registrar SHA-256.
4. Documentar em `DATA_CARD.md` **cada coeficiente** de `_calculate_recovery_probability` (`:198-250`) com a hipótese de negócio que o justifica. Exemplo: `"card_declined": -0.25` → "bloqueio bancário exige ação do emissor; a literatura de dunning trata como a causa menos recuperável depois de `do_not_honor`". Isto é o que a banca vai perguntar.

**`tests/test_synthetic_fidelity.py` — os gates viram teste:**

| Teste | Critério |
|---|---|
| KS por feature contínua (300 real × 15k sint.) | `p > 0.05` para valor, hora, dia-do-mês |
| χ² para categóricas | `p > 0.05` para meio de pagamento |
| Delta de correlação | `‖ρ_real − ρ_sint‖_F < 0.15` |
| **Anti-vazamento** | AUC do XGBoost em holdout sintético **∈ [0.70, 0.92]** |
| Anti-cópia | zero linha sintética idêntica a uma real (privacidade) |
| Reprodutibilidade | dois `generate_dataset(15000, seed=42)` → DataFrames idênticos |
| Balanceamento | taxa de `recovered` ∈ [0.30, 0.70] |

**Gate G3:**
```bash
pytest tests/test_synthetic_fidelity.py -q     # 100% verde
python -c "import pandas as pd; d=pd.read_parquet('data/synthetic/treino_15000.parquet'); print(len(d), d.recovered.mean())"
```
- [ ] 15.000 linhas exatas, `recovered.mean()` ∈ [0.30, 0.70]
- [ ] **AUC ≤ 0.92.** Se der 0.97, o gerador está vazando o rótulo → aumente o ruído em `synthetic_data.py:248` e reduza a força dos coeficientes. **Este é o gate mais importante do plano.** Um AUC de 0.99 numa demo de TCC é a bandeira vermelha que qualquer orientador reconhece.
- [ ] `DATA_CARD.md` justifica cada coeficiente do modelo causal
- [ ] `[SYNTH] MODO NÃO-CALIBRADO` **não** aparece na saída

**Rollback:** se a calibração não fechar, gere os 15.000 com os parâmetros atuais, marque `"calibrado": false` no `DATA_CARD` e **diga isso na apresentação**. Um dataset sintético não-calibrado e declarado é defensável; um apresentado como calibrado sem ser, não é.

---

### 🟢 SPRINT 4 — Treino real dos três modelos
**Qua 02/09, tarde · 3h · branch `sprint/4-treino`**

**Objetivo:** apagar as quatro linhas de fallback heurístico da saída da demo.

**Tarefas**

1. `python -m crai.scripts.train_all` com `n_samples=15000`, lendo o parquet do Sprint 3 (não regerando).
2. Registrar métricas em `docs/METRICAS_MODELOS.md`, uma tabela por módulo:
   - **Módulo 1 (XGBoost + RF):** AUC-ROC, precision/recall por classe, matriz de confusão, top-10 SHAP
   - **Módulo 2 (Autoencoder):** distribuição do erro de reconstrução, threshold escolhido **e o critério** (percentil? qual?), precision/recall de anomalia
   - **Módulo 3 (LSTM + Prophet):** MAE em dias na previsão de payday, por perfil (CLT / PJ / freelancer), comparado a um **baseline ingênuo** ("todo dia 5") — se o LSTM não vencer o baseline ingênuo, **diga isso**; é um resultado, não um fracasso
3. `train_all.py` já verifica que cada modelo salvo é recarregável (`recarregavel`). Confirmar que os três dão `True`.
4. Rodar `test_pipeline.py` e conferir que a saída **não** contém nenhuma das quatro linhas de fallback.

**Gate G4:**
```bash
python -m crai.scripts.train_all
python test_pipeline.py 2>&1 | grep -E "heurística|não treinado|não instalado"   # deve retornar VAZIO
pytest tests/ -q                                                                 # skips ≈ 0
ls -la crai/models/
```
- [ ] Os três modelos recarregáveis (`recarregavel: True`)
- [ ] AUC do Módulo 1 ∈ [0.70, 0.92] — **mesma faixa do G3**; fora dela, volte ao Sprint 3
- [ ] SHAP produz explicação legível em português na saída do pipeline
- [ ] `docs/METRICAS_MODELOS.md` tem o baseline ingênuo do Módulo 3
- [ ] `crai/models/` versionado **fora do git** (está no `.gitignore`) → **empacote em `models_demo.tar.gz` e guarde**, senão a demo depende de retreinar na hora

**Rollback:** se `prophet` ou o LSTM não convergirem em 1h, treine só os Módulos 1 e 2, mantenha o Módulo 3 em heurística e **rotule `[MODO HEURÍSTICO]` explicitamente na saída**. O Módulo 1 com SHAP real já sustenta a narrativa de IA.

---

### 🟢 SPRINT 5 — Runner determinístico e relatório da demo
**Qua 02/09, noite · 2h30 · branch `sprint/5-demo`**

**Objetivo:** a demo é **um comando** que sempre produz o mesmo resultado e deixa um artefato auditável.

**Tarefas**

1. `demo_runner.py` na raiz de `crai/`, substituindo o `test_pipeline.py` como face da demo:
   - `PYTHONHASHSEED=0`, seeds fixas, **relógio injetado** (`DEMO_NOW=2026-09-03T09:00:00`) — sem isso as datas do plano BACEN mudam a cada execução e a apresentação fica irreprodutível
   - 8 cenários: 4 de Pix (0, 1, 2 e 3 tentativas usadas) + 2 de cartão desativado + 2 de churn voluntário
   - **+2 cenários de borda**, que são a prova do Sprint 1: payload com `"valor": "299,90"` e payload com `data: [{...}]` → mostram o sistema **recusando com 422 e log claro**, em vez de quebrar. Mostrar o sistema recusando bem é mais forte que mostrar só o caminho feliz.
2. Saída em três camadas:
   - **terminal:** a trilha `[RACIOCÍNIO]` já existente, que é o melhor ativo narrativo do projeto
   - **`relatorio_demo.json`:** um objeto por cenário — entrada, cada decisão, cada score, o plano BACEN, o e-Profit
   - **`relatorio_demo.md`:** tabela-resumo + as métricas dos modelos + a proveniência dos dados
3. **Corrigir o P2-9** (o resumo que mente):
   - `Janela BACEN esgotada` só conta cenários que **chegaram** a `schedule_retry_pix`; cenários barrados em `decide_recovery` viram uma linha própria: `Barrados por janela esgotada (antes do agendamento)`
   - `retidos` e `escalados` passam a ser **mutuamente exclusivos**; se hoje um cenário é os dois, a lógica de `voluntary_agent.py` está ambígua — resolva ou reporte
4. `docs/ROTEIRO_DEMO.md`: roteiro de 8 minutos, com o comando exato, o que apontar em cada tela, e — em uma seção própria — **as três perguntas mais prováveis dos orientadores** com a resposta pronta:
   - *"Esses dados são reais?"* → seção 2 deste plano, sem rodeios
   - *"Como sei que o modelo não decorou sua própria regra?"* → gate anti-vazamento, AUC 0.70–0.92, com o número medido
   - *"Por que 3 tentativas e não 5?"* → limite regulatório do BACEN, e o teste que falha se o código violar

**Gate G5:**
```bash
DEMO_NOW=2026-09-03T09:00:00 python demo_runner.py > run1.txt
DEMO_NOW=2026-09-03T09:00:00 python demo_runner.py > run2.txt
diff run1.txt run2.txt            # DEVE ser vazio
```
- [ ] `diff` vazio — **determinismo é o gate**, sem isso a demo é uma aposta
- [ ] Roda em **menos de 60s** sem rede (sem `ANTHROPIC_API_KEY`, sem HubSpot)
- [ ] Também roda **com** `ANTHROPIC_API_KEY` (mensagens reais do Claude) sem quebrar — testar as duas
- [ ] `relatorio_demo.json` válido, com os 10 cenários
- [ ] Nenhum número do resumo se contradiz (P2-9 fechado)
- [ ] Zero `Traceback` na saída

---

### 🟣 SPRINT A2 — AUDITORIA FINAL E CONGELAMENTO
**Qui 03/09, 07h–09h · 2h · branch `sprint/a2-final`**

**Objetivo:** ensaio geral e congelamento. **Depois deste sprint, nenhuma linha de código muda antes da demo.** Essa regra existe para ser cumprida: quase toda demo que quebra, quebra por causa de uma "melhoria rápida" feita na última hora.

**Tarefas**

1. **Teste do ambiente limpo** — o único teste que importa de verdade:
   ```bash
   git clone <repo> /tmp/crai-limpo && cd /tmp/crai-limpo/crai
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements-lock.txt
   tar xzf models_demo.tar.gz
   DEMO_NOW=2026-09-03T09:00:00 python demo_runner.py
   ```
   Se falhar aqui, falha na frente dos orientadores.
2. **Auditoria adversarial #2**, contexto novo, mesmo protocolo do A1, com foco em: os gates G3/G4/G5 medem o que dizem medir? Algum número do `relatorio_demo.md` contradiz `METRICAS_MODELOS.md`? O `DATA_CARD` descreve o dataset que foi de fato usado no treino? → `docs/AUDITORIA_02.md`
3. **Ensaio cronometrado** do `ROTEIRO_DEMO.md`, com alguém segurando o relógio.
4. `git tag demo-2026-09-03` e **congelar**.

**Gate GA2 — todas obrigatórias:**
- [ ] Ambiente limpo roda de primeira
- [ ] `docs/AUDITORIA_02.md` com veredito `LIBERADO`
- [ ] Ensaio ≤ 8 min
- [ ] Zero item P0/P1 aberto (P2 aberto é aceitável **se listado** no `AUDITORIA_02.md`)
- [ ] Tag criada · **código congelado**

---

## 5. Mapa de gates

| Gate | Quando | Critério que reprova |
|---|---|---|
| **G0** | Seg 23h | Sem aprovação humana da proveniência; `torch`/`prophet` não instalam |
| **G1** | Ter 12h | Qualquer payload hostil levanta exceção; default sem log |
| **G2** | Ter 17h | `RN_maria_001` com 0 usadas agenda < 3; `PixRetryPolicyViolation` |
| **GA1** | Ter 19h | Auditor devolve `BLOQUEADO`; teste de regressão que passa no commit anterior |
| **G3** | Qua 12h | **AUC > 0.92 (vazamento)** ou < 0.70; KS reprova |
| **G4** | Qua 17h | Alguma linha de fallback heurístico na saída; modelo não recarregável |
| **G5** | Qua 22h | `diff run1 run2` não-vazio; números do resumo se contradizem |
| **GA2** | Qui 09h | Ambiente limpo falha; `AUDITORIA_02` `BLOQUEADO` |

---

## 6. Caminho crítico e o que sacrificar

```
G0 ──► S1 ──► S2 ──► A1 ──► S3 ──► S4 ──► S5 ──► A2 ──► DEMO
       (bugs)        (audit) (dados)(treino)(demo) (freeze)
```

**Ordem de sacrifício, se o tempo apertar** — corte de baixo para cima:

1. Fonte D (tenure do Telco) — corta primeiro, sem dor
2. Módulo 3 (LSTM/Prophet) em heurística rotulada
3. Calibração completa → só a marginal de valor
4. P1-8 (preenchimento para trás) → vira dívida documentada
5. **Nunca corte:** S1 (a borda), G3 anti-vazamento, G5 determinismo, A2 ambiente limpo

Esses quatro últimos são o que separa "funciona na minha máquina" de "demonstrável".

---

## 7. Registro por sprint

Ao fechar cada sprint, anexe aqui:

```markdown
### Sprint N — <nome>
- Branch/commit:
- Gate: ✅ / ❌  (se ❌: o que reprovou e o que foi feito)
- Testes: X passed, Y skipped
- Dívida aberta:
- Tempo real vs. estimado:
```

---

## Apêndice A — Comandos de referência

```bash
# ambiente
pip install -r requirements.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu

# testes
pytest tests/ -q
pytest tests/ -q --cov=crai --cov-report=term-missing
pytest tests/test_pix_automatico_retry.py -q -k "ancoragem"

# um teste de regressão realmente falha antes?
git stash && pytest tests/test_payment_gateway.py::TestPayloadsHostis -q; git stash pop

# treino
python -m crai.scripts.train_all
python -m crai.scripts.train_all --quick        # smoke test

# demo
DEMO_NOW=2026-09-03T09:00:00 python demo_runner.py

# dados abertos BACEN (sem chave)
curl -s "https://api.bcb.gov.br/dados/serie/bcdata.sgs.21084/dados?formato=json&dataInicial=01/01/2024"
```

## Apêndice B — Arquivos novos criados por este plano

```
docs/DATA_CARD.md              proveniência, licenças, hashes, modelo causal do rótulo
docs/METRICAS_MODELOS.md       métricas dos 3 módulos + baseline ingênuo
docs/AUDITORIA_01.md           auditoria adversarial pós-correções
docs/AUDITORIA_02.md           auditoria final pré-congelamento
docs/ROTEIRO_DEMO.md           roteiro de 8 min + perguntas prováveis
crai/ml/calibracao.py          ajuste de parâmetros sobre as 300 reais
tests/test_synthetic_fidelity.py   os gates estatísticos como teste
demo_runner.py                 a demo determinística
data/real/amostra_300.csv      âncora real (fora do git se a licença exigir)
data/synthetic/treino_15000.parquet
requirements-lock.txt
models_demo.tar.gz             modelos treinados (models/ está no .gitignore)
```
# DATA CARD — CRAI

> Proveniência, licenças e limites do que a CRAI usa para treinar.
> Gerado no Sprint 0. Números medidos, não estimados.
>
> **Amostra real:** `data/real/amostra_300.csv` · SHA-256 `d800c60f765b245bfff29e5c258eb34c6cb1a2f54c8dd4c3a69b7bb794de2f0f`
> **Reprodução:** `python -m crai.scripts.preparar_amostra_real`
> **Registro-máquina:** `data/real/PROVENIENCIA.json`

---

> # ⬜ ESTADO DA CALIBRAÇÃO — LEIA ANTES DE QUALQUER SEÇÃO
>
> **Nenhum parâmetro medido neste documento está no gerador ainda.** As fontes
> foram levantadas, os números foram calculados e estão corretos — mas
> `crai/ml/synthetic_data.py` continua com os valores inventados. A calibração
> é a tarefa do **Sprint 3**, que está parado pelo gate GA1 (ver `sprints.md`).
>
> Medido em `94cd8f6`:
>
> | O documento afirma | O gerador tem |
> |---|---|
> | valor `μ = 4,5824 · σ = 0,8883` (§2.2) | `rng.lognormal(mean=5.8, sigma=0.7)` |
> | clip `R$ 0,99 – R$ 1.312,67` (§2.3) | `clip(49.90, 9999.90)` |
> | sazonalidade por histograma empírico (§2.2) | `peak_days = [5,10,15,20,30]` e `_hour_distribution()` |
> | `calibracao.json → calibrado.tenure = false` (§5) | **o arquivo não existe** |
>
> Leia todo verbo no presente das seções 2, 3, 4 e 5 como **"passa a"**, não
> como "é". Cada seção repete este aviso no ponto exato.
>
> **Correção da auditoria A1-r9.** A rodada anterior corrigiu esta mesma classe
> de afirmação — mas só na §6, porque a nota de correção apontava para as
> seções erradas (dizia "seção 5" para o que é §2.2, e "seção 4" para o que é
> §2.1). O ponteiro errado deixou §2.2, §2.3 e §5 continuarem afirmando a
> calibração como fato consumado.

---

## 1. A verdade desconfortável, dita primeiro

**Não existe base pública — brasileira ou estrangeira — com o rótulo que a CRAI
precisa:** *"esta cobrança falhada foi recuperada, sim ou não"*. Nenhum PSP
publica isso. Quem tem esse dado são as operadoras de dunning, e é exatamente o
ativo comercial delas.

Logo, a arquitetura de dados da CRAI é esta, e **é assim que deve ser
apresentada à banca**:

```
FEATURES  → calibradas em dados REAIS (marginais e sazonalidade medidas)
RÓTULO    → gerado por um MODELO CAUSAL DECLARADO, não aprendido
```

O que a demo prova: **o pipeline aprende, explica (SHAP) e decide sobre um
sinal.**
O que a demo **não** prova: que a previsão de recuperação vale em produção.

Dizer isso na apresentação é força, não fraqueza — é o que separa um TCC sério
de um que superajustou a própria regra e chamou de IA.

---

## 2. Fonte A — âncora primária das 300 linhas reais

**Brazilian E-Commerce Public Dataset by Olist**

| Campo | Valor medido |
|---|---|
| Origem canônica | `kaggle.com/datasets/olistbr/brazilian-ecommerce` |
| Espelho usado no download | `raw.githubusercontent.com/dujiaying/olist/master/data` |
| Licença | **CC BY-NC-SA 4.0** — acadêmico OK, **comercial NÃO** |
| Transações de pagamento (bruto) | 103.886 |
| Pedidos | 99.441 |
| Clientes | 99.441 |
| População após filtro | **103.877** (removidos `payment_type = not_defined` e `payment_value = 0`) |
| Período dos pedidos | 2016-09-04 → 2018-10-17 |

**SHA-256 dos arquivos de origem** (confira contra o Kaggle se quiser auditar):

```
olist_order_payments_dataset.csv  4f713964f2815dbbaa40b9488268c55aac3627bfce5aa96cf58d1f3616de3cc0
olist_orders_dataset.csv          8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf
olist_customers_dataset.csv       983a422239e1712ded753b3bf9ecf47dc73f144d306029dcfa99e70a226883d2
```

> **Por que um espelho e não o Kaggle direto:** o Kaggle exige conta e chave de
> API. Para o pipeline ser reproduzível por qualquer avaliador sem credencial, o
> download vem de um espelho público — e o SHA-256 acima é a prova de que os
> bytes são os mesmos. Quem tiver conta no Kaggle baixa de lá e compara o hash.

### 2.1 A amostra de 300 linhas

Sorteio **estratificado por `payment_type` × quartil de `payment_value`**, com
**alocação proporcional** (arredondamento por maior resto) e seed fixa (42).
Proporcional, e não balanceado, de propósito: a amostra existe para **medir** o
mix real de meio de pagamento. Um desenho balanceado daria a `debit_card` (1,5%
do real) o mesmo peso de `credit_card` (74%) e destruiria justamente o número
que o TCC quer citar.

| Métrica | Valor medido nas 300 |
|---|---|
| Linhas | 300 (+ header = 301) |
| Período coberto | 2016-10-10 → 2018-08-26 |
| `credit_card` | 222 (74,0%) |
| `boleto` | 57 (19,0%) |
| `voucher` | 17 (5,7%) |
| `debit_card` | 4 (1,3%) |
| Valor: mín / p25 / mediana / média / p75 / máx | R$ 0,99 / 57,03 / **100,57** / 142,17 / 171,81 / 1.312,67 |
| Ajuste lognormal por MLE | **μ = 4,5824 · σ = 0,8883** |

O mix da amostra reproduz o da população (73,9 / 19,0 / 5,6 / 1,5%) dentro de
0,3 p.p. — a estratificação funcionou.

### 2.2 Papel na CRAI — três marginais medidas, não chutadas

> ⬜ **Medidas, e ainda não aplicadas.** Os três números abaixo estão
> corretos e vêm da amostra real; o gerador ainda não os usa. Verbos no
> presente descrevem o Sprint 3, não o código de hoje.

1. **Valor** → substitui a lognormal hardcoded de `synthetic_data.py`
   (`mean=5.8, sigma=0.7`), que era palpite, por **μ = 4,5824 · σ = 0,8883**.
2. **Mix de meio de pagamento** → sustenta com número real o argumento de que o
   boleto ainda pesa no Brasil (19% das transações).
3. **Sazonalidade** → `hour_of_day`, `day_of_week` e `day_of_month` extraídos de
   `order_purchase_timestamp`, como histograma empírico. Substitui
   `peak_days = [5,10,15,20,30]` e `_hour_distribution()`, ambos inventados.

### 2.3 ⚠️ Limitações — leia antes de citar qualquer número

- **É e-commerce B2C, não SaaS B2B por assinatura.** Não tem `tenure`, não tem
  recorrência, e **não tem rótulo de recuperação**. Serve como **âncora de
  distribuição, jamais como ground truth**.
- ⬜ **Consequência direta e incômoda da calibração do valor** — quando ela
  acontecer, no Sprint 3: a mediana real da
  Olist é **R$ 100,57**, não os ~R$ 330 que a lognormal chutada produzia. Ao
  calibrar, o dataset sintético passa a ter **ticket de e-commerce brasileiro**,
  e deixa de ter ticket de SaaS B2B. Isso é uma perda de aderência ao domínio —
  e ainda assim é a escolha certa: um parâmetro medido e declarado vale mais que
  um parâmetro inventado que *parecia* certo. **Quando houver cliente-piloto,
  esta é a primeira coisa a trocar.**
- ⬜ **Os limites de corte (`clip`) do valor sintético passarão a ser a faixa
  observada na âncora** (R$ 0,99 – R$ 1.312,67), no lugar dos R$ 49,90 –
  R$ 9.999,90 **que ainda estão no código**. Sem isso, com μ = 4,58 uma fatia grande da distribuição bateria
  no piso de R$ 49,90 e criaria um pico artificial exatamente ali.
- **A sazonalidade horária é de e-commerce.** Se algum material da CRAI afirma
  sazonalidade horária **do Pix** citando o BACEN, está errado — a base de Pix do
  BACEN não tem granularidade por hora. A hora vem da Fonte A, e é **proxy
  declarado**.
- **Uso comercial proibido** pela CC BY-NC-SA 4.0. Registrar no TCC.

---

## 3. Fonte B — taxa-base de inadimplência (BACEN SGS)

| Série | O que é | Obs. | Período | Média | Último |
|---|---|---|---|---|---|
| **21084** | Inadimplência da carteira de crédito — PF, total (% mensal) | 79 | 01/2020 → 07/2026 | **3,92%** | **5,81%** |
| **21129** | Inadimplência PF — cartão de crédito (% mensal) | 79 | 01/2020 → 07/2026 | **7,16%** | **9,51%** |

- **Acesso:** aberto, sem chave, sem conta —
  `https://api.bcb.gov.br/dados/serie/bcdata.sgs.21084/dados?formato=json&dataInicial=01/01/2020`
- **Licença:** Open Data Commons **ODbL** — uso comercial permitido com atribuição.
- **Papel:** ancorar `ERROR_CODE_PROBS`. A hipótese declarada é: *quando a
  inadimplência PF corrente está acima da sua média histórica, a fatia de falhas
  por `insufficient_funds` sobe proporcionalmente, e os demais códigos são
  renormalizados.* Com 5,81% contra média de 3,92%, o excesso é de **+48%**.

---

## 4. Fonte C — contexto de mercado do Pix (BACEN dados abertos)

- `dadosabertos.bcb.gov.br/dataset/pix` · licença ODbL.
- **Papel:** sustentar o **argumento de mercado** do TCC (adoção do Pix). **Não
  entra no treino.**
- ⚠️ **Não tem granularidade por hora do dia.** Ver a limitação em 2.3.

---

## 5. Fonte D — âncora de tenure

**Status: NÃO USADA.** O IBM Telco Customer Churn era o item explicitamente
mais dispensável do plano (primeiro da ordem de sacrifício). Consequência
declarada: **`tenure_months` permanece NÃO CALIBRADO** — segue com a mistura
exponencial + uniforme escrita à mão em `synthetic_data.py`.

⬜ O plano é registrar isso como `false` em `calibracao.json → calibrado.tenure`
— **arquivo que ainda não existe**, porque o script que o gera é do Sprint 3.
Enquanto isso, a declaração desta limitação é este parágrafo, e só ele.

---

## 6. Protocolo 300 reais → 15.000 sintéticos

> ⚠️ **ESTE PROTOCOLO É O PLANO DO SPRINT 3, E O SPRINT 3 AINDA NÃO RODOU.**
>
> Correção da auditoria A1-r8: os passos [2] e [4] abaixo estavam escritos no
> presente, com selos `✅ CALIBRADO`, como se descrevessem o estado atual do
> código. Não descrevem. Medido em `deb23be`:
>
> | O que o texto afirmava | O que existe |
> |---|---|
> | gerador calibrado por MLE | `synthetic_data.py` ainda usa `rng.lognormal(mean=5.8, sigma=0.7)` — o palpite que a **§2.2** diz ter sido substituído |
> | `PARAMS` alimentando o gerador | o símbolo não existe no módulo |
> | `crai/models/calibracao.json` | não existe |
> | `data/synthetic/treino_15000.parquet` | não existe |
> | `tests/test_synthetic_fidelity.py` (gate G3) | não existe |
> | `python -m crai.scripts.gerar_treino` | o script não existe |
> | `python -m crai.scripts.calibrar_parametros` | o script não existe |
>
> O que EXISTE hoje: `crai/scripts/preparar_amostra_real.py`, os números da
> amostra real (**§2.1**, conferidos), e este documento. A calibração em si é a
> tarefa do Sprint 3, que está **parado pelo gate GA1** — ver `sprints.md`.
>
> Consequência prática: os modelos em `crai/models/` foram treinados pelo
> gerador NÃO calibrado, e é daí que vem a AUC 0,7029 registrada na `§4.6` do
> README. Nenhum número desta seção 6 pode ser apresentado à banca como
> medido — eles são a meta, não o resultado.


```
[1] AMOSTRA     300 linhas da Fonte A, estratificadas por payment_type × quartil
                de valor, alocação proporcional, seed 42.
                → data/real/amostra_300.csv  (SHA-256 acima)

[2] CALIBRAÇÃO  a ajustar sobre as 300 → crai/models/calibracao.json
                  valor            → lognormal por MLE          ⬜ PLANEJADO
                  hora/dow/dom     → histograma empírico        ⬜ PLANEJADO
                  mix meio pgto    → frequência observada       ⬜ PLANEJADO
                  taxa-base falha  → série SGS 21084            ⬜ PLANEJADO
                  tenure           → sem fonte                  ❌ NÃO CALIBRÁVEL
                  p base = 0.5     → sem fonte pública          ❌ NÃO CALIBRÁVEL

                  ⬜ = a fonte foi levantada e o número existe (seção 4), mas
                       o gerador ainda NÃO o usa.
                  ❌ = não há fonte pública; fica declarado como limitação.

[3] DEPENDÊNCIAS preservar a matriz de Spearman das 300 por amostragem
                 condicional por estrato. NÃO usar cópula gaussiana.

[4] EXPANSÃO    generate_dataset(n_samples=15000, seed=42)
                → data/synthetic/treino_15000.parquet   ⬜ NÃO GERADO

[5] RÓTULO      _calculate_recovery_probability(), coeficientes documentados um
                a um na seção 7, com ruído N(0, 0.05) preservado.
```

**Regra inviolável do passo [5]:** nenhuma feature nova entra no rótulo sem
entrar também no gerador de features. Se o rótulo souber algo que as features
não sabem, o modelo não aprende nada. Se souber *exatamente* o que as features
sabem, sem ruído, o modelo decora a regra. O gate **G3 mede as duas falhas**
(AUC obrigatoriamente dentro de **[0,70 ; 0,92]**).

---

## 7. O modelo causal do rótulo — cada coeficiente e sua hipótese

`crai/ml/synthetic_data.py :: _calculate_recovery_probability`. Ponto de partida
**p = 0,50**.

> **Declaração de honestidade:** os coeficientes abaixo são **hipóteses de
> negócio escritas à mão**, não estimativas ajustadas a dados. É isso que a
> seção 1 quer dizer com *"rótulo gerado por um modelo causal declarado"*. Eles
> são a parte do sistema que **não** é aprendida.

| Termo | Coeficiente | Hipótese de negócio que o justifica |
|---|---|---|
| `tenure_months` | `+ clip(tenure/60, 0, 0.25)` | Tempo de casa é o proxy mais forte de intenção de permanecer. Cliente antigo que falha um pagamento quase sempre teve um problema pontual, não uma decisão de sair. Teto em +0,25 para não dominar os demais sinais. |
| `payment_history_score` | `+ (score − 0.5) × 0.30` | Histórico de adimplência é o melhor preditor individual de adimplência futura — é a premissa de qualquer bureau de crédito. Centrado em 0,5 para que um histórico mediano não empurre nada. |
| `insufficient_funds` | `− 0,05` | **A falha mais recuperável.** Não há recusa do banco: falta saldo *agora*. É exatamente o caso que o Módulo 3 (Payday) resolve, reagendando para o dia da entrada de dinheiro. |
| `processing_error` | `+ 0,05` | Falha técnica do gateway, não do pagador. Retentar costuma resolver sozinho — o único código com impacto positivo. |
| `expired_card` | `− 0,15` | Exige ação do cliente (atualizar o cartão), mas é uma ação trivial e o cliente normalmente quer continuar. Recuperável por mensagem, não por insistência. |
| `generic_decline` | `− 0,20` | Recusa sem motivo declarado pelo emissor. Incerto por definição: engloba desde antifraude até limite. |
| `card_declined` | `− 0,25` | Bloqueio bancário. Exige ação do **emissor**, não do cliente — a literatura de dunning trata como a causa menos recuperável depois de `do_not_honor`. |
| `do_not_honor` | `− 0,30` | **A pior.** O emissor recusou e não diz por quê; frequentemente é sinal de conta comprometida ou restrição de crédito. Insistir queima tentativa sem retorno. |
| `invoice_amount` | `− clip((valor − 500)/5000, 0, 0.15)` | Fatura alta exige decisão consciente de gasto; ticket baixo é recuperado no automático. Teto em −0,15. **Nota:** com o valor calibrado na Olist (mediana R$ 100), este termo passa a ser ~0 na maior parte das linhas — o sinal enfraquece, e isso está declarado. |
| `failure_count_90d` | `− clip(falhas × 0.05, 0, 0.20)` | Reincidência é sinal de fragilidade financeira estrutural, não de acidente. Teto em −0,20 para não zerar o cliente sozinho. |
| dia de pagamento | `+ 0,08` se dia ∈ {5,6,7,10,15,20,30} | Sazonalidade de recebimento brasileira (5º dia útil, quinzena, dia 30). Cobrar quando entrou dinheiro é o mecanismo central da tese. |
| `attempt_count` | `− (tentativas − 1) × 0.06` | Cada tentativa fracassada é evidência acumulada contra a recuperação. Linear, sem teto — é o termo que impede o modelo de recomendar insistência infinita. |
| **ruído** | `+ N(0 , 0.05)` | **Deliberado e não negociável.** Sem ele o rótulo é função determinística das features e o modelo decora a regra: AUC vai a ~0,99 e o G3 reprova, corretamente. |
| corte final | `clip(p, 0.05, 0.95)` | Nenhuma cobrança é impossível nem garantida. |

---

## 8. Anti-privacidade e anti-cópia

- A amostra real **não contém `customer_id`** — a coluna é usada só para o join
  e descartada antes de gravar. Sobram `order_id` (opaco), UF e valores.
- O gate **anti-cópia** (`tests/test_synthetic_fidelity.py`) exige **zero linha
  sintética idêntica a uma linha real**.
- O schema normalizado de Pix nunca carrega chave Pix do pagador — ver
  `crai/integrations/payment_gateway.py`.

---

## 9. Onde cada arquivo vive

| Arquivo | No git? | Como regenerar |
|---|---|---|
| `data/real/amostra_300.csv` | ❌ (CC BY-NC-SA) | `python -m crai.scripts.preparar_amostra_real` |
| `data/real/bacen_sgs_*.json` | ❌ | idem |
| `data/real/PROVENIENCIA.json` | ❌ | idem |
| `crai/models/calibracao.json` | ✅ **exceção no .gitignore** | `python -m crai.scripts.calibrar` ⬜ *script do Sprint 3, ainda não escrito* |
| `data/synthetic/treino_15000.parquet` | ❌ | `python -m crai.scripts.gerar_treino` ⬜ *script do Sprint 3, ainda não escrito* |
| `crai/models/*` (modelos) | ❌ | `python -m crai.scripts.train_all` + `models_demo.tar.gz` |

`calibracao.json` é a única exceção porque é um punhado de números medidos, não
um binário — e sem ele no clone limpo a demo cairia em
`[SYNTH] MODO NÃO-CALIBRADO` na frente da banca.

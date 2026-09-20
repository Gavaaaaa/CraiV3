# Base v2 — população compartilhada de 120.000 clientes

Gerada e treinada em 20/09/2026. Semente 42, `fonte=sintetico_calibrado`,
`END_DATE=2026-09-14` (a mesma janela da base de 14/09, para que a comparação
de métricas meça a base e não o calendário).

---

## 1. O que foi feito

Uma população de **120.000 clientes**. As quatro tabelas de treino deixaram de
ser bases independentes e passaram a ser **visões** dessa população, cada uma no
seu grão:

```
populacao (120.000 clientes)
  ├── classificador   120.000 cobranças falhadas   (grão: cobrança)  — 33.177 clientes
  ├── comportamental  120.000 retratos             (grão: cliente)   — 120.000 clientes
  ├── liquidez      1.800.000 linhas               (grão: cliente-dia) — 10.000 clientes × 180 dias
  └── voluntario      120.000 eventos de SDK       (grão: evento)    — 40.029 clientes
```

Ter quatro tabelas nunca foi o problema: os quatro modelos preveem em unidades
diferentes, e unidades diferentes exigem tabelas diferentes. O que faltava era a
**chave que as liga** e a **população única** de onde todas derivam. Um cliente
que aparece em três tabelas agora tem o mesmo MRR nas três.

### Tamanho em disco

| Tabela | Linhas | Colunas | Parquet | Clientes distintos |
|---|---:|---:|---:|---:|
| `populacao` | 120.000 | 9 | 2,78 MB | 120.000 |
| `classificador` | 120.000 | 16 | 2,37 MB | 33.177 |
| `comportamental` | 120.000 | 14 | 3,04 MB | 120.000 |
| `liquidez` | 1.800.000 | 8 | 5,28 MB | 10.000 |
| `voluntario` | 120.000 | 10 | 1,19 MB | 40.029 |
| **Total** | **2.280.000** | | **14,66 MB** | |

Sobre a pergunta dos "gigas": a base pesa **14,66 MB em parquet**, não gigabytes.
Duas razões. Primeira, o parquet é colunar e comprimido — as mesmas tabelas em
CSV dariam ~180 MB. Segunda, e mais importante: a tabela de liquidez é uma
**subamostra declarada** de 10.000 clientes, não a população inteira. 120.000
clientes × 180 dias seriam 21,6 milhões de linhas e ~830 MB em CSV, para treinar
uma LSTM que converge com muito menos — 136.000 janelas já é bastante. O tamanho
da subamostra é um parâmetro (`--clientes-liquidez`), está no manifesto, e é
defensável na banca justamente por ser uma escolha declarada.

Base pesada não é sinal de base boa. As bases de gigabytes que você já usou eram
pesadas porque tinham texto, imagem ou milhares de colunas esparsas. Aqui são 57
colunas de número e categoria curta.

---

## 2. As três correções que entraram

### 2.1 A escala de 50,2× acabou

`invoice_amount` deixou de ser sorteado de uma lognormal calibrada no ticket do
Olist — que é varejo, mediana R$ 100 — e passou a **derivar do MRR do cliente**:

```
invoice_amount = mrr × fator_do_ciclo      fator ∈ [0,15 ; 10,0]
```

com o fator cobrindo mensal (82%), anual (6%, 12 meses com desconto) e
proporcional (12%, entrada no meio do ciclo).

| | Base v1 (14/09) | Base v2 |
|---|---:|---:|
| Mediana do MRR (população) | — | R$ 1.789,30 |
| Mediana de `invoice_amount` | R$ 97,44 | R$ 1.818,29 |
| Mediana do MRR (Módulo 2) | R$ 4.888,16 | R$ 1.789,30 |
| Mediana do MRR (Módulo 4) | R$ 1.787,72 | R$ 1.789,30 |
| **Razão entre a maior e a menor** | **50,2×** | **1,016×** |

A calibração do Olist **continua valendo para `day_of_month`** — sazonalidade de
dia do mês é o que aquela fonte realmente mediu, e isso não mudou.

### 2.2 `failure_count_90d` e `attempt_count` passaram a ser contados

Na v1 cada linha era um cliente diferente, então `failure_count_90d` era um
Poisson(1,5) sorteado sem lastro nenhum: o número dizia "três falhas nos últimos
90 dias" numa tabela onde aquele cliente aparecia uma vez só.

Agora um cliente tem histórico — em média **3,62 cobranças** — e as duas colunas
são **contagens de verdade**:

- `failure_count_90d` = cobranças anteriores do mesmo cliente nos 90 dias que
  terminam naquela cobrança.
- `attempt_count` = posição da cobrança dentro da escada de retentativa
  (cobranças do mesmo cliente nos 14 dias anteriores, + 1, teto em 4).

Distribuição medida de `failure_count_90d`: 0 → 39.187, 1 → 37.573, 2 → 24.902,
3 → 12.064, 4 → 4.422, 5 ou mais → 1.852.

Isso só é possível com várias cobranças por cliente, que foi exatamente o
desenho que você escolheu.

### 2.3 O *train/serve skew* de cartão foi fechado

A auditoria de 15/09 achou o seguinte: `card_brand` tinha 5 bandeiras no treino
e é `"n/a"` em produção, e 52% dos valores de `gateway_error_code` no treino
eram códigos de cartão (`expired_card`, `card_declined`, `do_not_honor`). A CRAI
só opera Pix Automático e boleto — o modelo estava aprendendo com um vocabulário
que nunca vê ao servir.

- **`card_brand` saiu.** Uma feature constante no momento de servir não pode
  ajudar e pode atrapalhar. O classificador passa de 12 para 11 features.
- **Os três códigos de cartão saíram.** O vocabulário agora é o que o sistema
  emite de verdade — os mesmos quatro que o painel já traduz, mais o resíduo:
  `insufficient_funds` (46%), `limit_exceeded` (18%),
  `authorization_revoked` (14%), `processing_error` (13%),
  `generic_decline` (9%).
- **`metodo_pagamento` entrou** no lugar do `card_brand`: `pix_automatico` (85%)
  ou `boleto` (15%), atributo do cliente e não da cobrança.

Os coeficientes dos três códigos herdados ficaram palavra por palavra como
estavam. Os dois novos entram com a hipótese declarada no código:
`limit_exceeded` = −0,12 (limite do Pix estourado, recuperável no ciclo
seguinte) e `authorization_revoked` = −0,30 (o cliente revogou a autorização;
herda o coeficiente do antigo `do_not_honor` porque é a causa mais difícil,
exige ação do cliente).

---

## 3. Verificações — tudo o que a auditoria de 15/09 reprovou

| Verificação | v1 (14/09) | v2 |
|---|---|---|
| Interseção de `customer_id` entre as quatro tabelas | **0** | **1.330** |
| Interseção classificador ∩ comportamental | 0 (nem tinham a coluna) | **33.177** |
| Interseção comportamental ∩ liquidez | **0** | **10.000** |
| Todos os ids existem na população | n/a | **sim** |
| Mesmo cliente, mesmo MRR em todas as tabelas | n/a | **sim** |
| `invoice_amount / mrr` dentro da faixa declarada, linha a linha | não | **sim**, [0,15 ; 10,0] |
| `card_brand` presente | sim | **não** |
| Códigos de cartão em `gateway_error_code` | 52% das linhas | **nenhum** |
| Nulos | 0 | **0** |
| Duplicatas | 14 no Módulo 4 | **0** |
| Gerar duas vezes com a mesma semente → mesmos sha256 | não testado | **sim, nos 5 arquivos** |

O determinismo foi testado de verdade: duas gerações completas com `--seed 42`
produziram sha256 idênticos nos cinco arquivos. Não há `date.today()` em lugar
nenhum deste caminho — a janela termina em `END_DATE`, que é constante.

---

## 4. Os modelos treinados na base nova

Hiperparâmetros, pesos do ensemble, limiar, split e early stopping: **os mesmos
do código atual, sem um ajuste sequer**. Se eu tivesse mexido neles, a
comparação mediria o meu ajuste, não a base.

### 4.1 Classificador de falha — XGBoost (0,7) + Random Forest (0,3)

| Rodada | Linhas | Features | Split | Taxa de recuperação | **AUC** | Brier | Recall @ 0,25 |
|---|---:|---:|---|---:|---:|---:|---:|
| v1, 40k *(o treino de 14/09)* | 40.000 | 11 | linha | 0,4491 | **0,6940** | 0,2193 | 0,9374 |
| v1, 120k | 120.000 | 11 | linha | 0,4471 | **0,7054** | 0,2158 | 0,9489 |
| **v2, 120k** | 120.000 | 11 | linha | 0,3959 | **0,7094** | 0,2085 | 0,9127 |
| **v2, 120k** | 120.000 | 11 | **cliente** | 0,3959 | **0,7095** | 0,2100 | 0,9109 |

A primeira linha reproduz o 0,6951 declarado no `app/README.md` dentro do erro
de medida (0,6940). Isso valida o banco de provas: o que vem depois é
comparável.

**Duas leituras, e a segunda é a importante.**

A primeira: a AUC subiu de 0,6940 para 0,7095. É melhora real, mas pequena — e
**a maior parte dela é volume, não população**. Só sair de 40k para 120k na base
v1, sem mexer em mais nada, já dá 0,7054. A população compartilhada, a escala
corrigida, as contagens de verdade e o vocabulário de Pix acrescentam 0,0041 em
cima disso.

A segunda, que responde à pergunta que você fez antes de tudo isso:

> **Teto de Bayes da base v2, calculado por construção: 0,7249.
> O modelo entrega 0,7095 — 97,9% de tudo que existe para ser aprendido.**

Na base v1 o teto era 0,7095 e o modelo entregava 0,6951 — 98,0%. **O número não
se moveu.** Triplicar o volume, unificar a população, consertar a escala e as
contagens mudou o teto em 0,015 e manteve o modelo colado nele.

Isso encerra a dúvida sobre volume, e não por argumento: por medida. O que
limita o Módulo 1 **não é a quantidade de dados nem a estrutura da base**. É o
**rótulo**. `recovered` continua sendo calculado por uma fórmula que usa as
mesmas features que o modelo vê; um modelo não pode superar a fórmula que gerou
a resposta que ele tenta prever, e a fórmula tem ruído de propósito — é esse
ruído que define o teto. Enquanto o rótulo for calculado, nenhum aumento de base
tira o Módulo 1 de ~0,71.

**Verificação de vazamento, feita de propósito:** com várias cobranças por
cliente, um split por linha coloca o mesmo cliente nos dois lados, e features
que são atributos dele (`payment_history_score`, `avg_ticket`, `tenure_months`)
poderiam virar identificador. Medi os dois: split por linha 0,7094, split por
cliente 0,7095. **Não há vazamento.** Mas o split por cliente deve entrar no
`train_all` mesmo assim — hoje a igualdade é sorte, e para de ser no dia em que
uma feature por cliente ficar mais informativa.

### 4.2 Risco voluntário — GradientBoosting

| Rodada | Linhas | Split | Taxa de churn | AUC do modelo | AUC da regra | Brier |
|---|---:|---|---:|---:|---:|---:|
| v1, 20k *(o treino de 14/09)* | 20.000 | linha | 0,3483 | 0,7503 | 0,7535 | 0,1812 |
| v1, 120k | 120.000 | linha | 0,3480 | 0,7469 | 0,7477 | 0,1817 |
| **v2, 120k** | 120.000 | linha | 0,2560 | **0,8279** | 0,8281 | 0,1249 |
| **v2, 120k** | 120.000 | **cliente** | 0,2560 | **0,8287** | 0,8295 | 0,1248 |

A AUC saltou de 0,75 para 0,83 — e **isso não é mérito do modelo**. Olhe a
coluna ao lado: a regra sozinha dá 0,8295. O modelo continua empatando com a
regra na terceira casa decimal, exatamente como na v1. O que subiu foi a
**separabilidade da própria regra**, porque `days_since_last` e
`features_used_30d` agora derivam do retrato comportamental do mesmo cliente em
vez de serem sorteados de distribuições próprias.

É o mesmo diagnóstico do Módulo 1, mais nu ainda: o rótulo do voluntário **é a
regra**, com ruído. Treinar contra ele só pode reproduzir a regra. A saída é o
`DELETE /clientes/{id}` do plano — é ele que dá, pela primeira vez, um
cancelamento observado para usar como rótulo.

### 4.3 Detector de anomalia — autoencoder 12→4→12

| Rodada | Treino (saudáveis) | ROC-AUC | Avg. Precision | Precisão | Recall | F1 | Separação |
|---|---:|---:|---:|---:|---:|---:|---:|
| v1, 120k | 92.820 | **0,9723** | 0,9507 | 0,916 | 0,827 | 0,869 | 6,01× |
| **v2, 120k** | 92.872 | **0,8684** | 0,7566 | 0,810 | **0,326** | 0,465 | 3,25× |

**A AUC caiu, e a queda é a notícia boa.** O 0,995 que o `app/README.md` marca
como bandeira vermelha (e o 0,9723 a que ele desce com 120k) existia porque as
duas populações eram desenhadas separadas: o cliente anômalo tinha outro perfil
de conta, não só outro comportamento. Na v2 o perfil de conta (`tenure_days`,
`mrr_brl`, `seats`) vem da mesma população para os dois grupos, e `seats` é
correlacionado com `mrr` — a anomalia passou a estar **só no comportamento**,
que é o que ela é no mundo real. 0,8684 é o número honesto.

**Mas há um problema operacional que precisa de conserto, e não é da base:** o
recall despencou para 0,326. O limiar é o percentil 95 do erro dos saudáveis, e
esse percentil foi calibrado numa distribuição que não existe mais. Com a base
v2, o percentil 95 deixa passar dois terços dos anômalos. **O
`threshold_percentile` tem que ser recalibrado** — provavelmente para algo entre
85 e 90 — com a curva precisão × recall medida e o ponto escolhido declarado.
Isso é tarefa do Bloco B, está na especificação.

### 4.4 Inferência de liquidez — LSTM + Prophet

| Rodada | Clientes | Janelas de treino | AUC da LSTM | AUC do Prophet | **AUC do ensemble** | MAE da janela | Acerto exato | Acerto ±1 dia |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| v1, 600 clientes *(o treino de 14/09)* | 600 | 8.160 | 0,9668 | 0,7403 | **0,9591** | 0,594 dia | 82,2% | 89,2% |
| **v2, 10.000 clientes** | 10.000 | 136.000 | 0,9744 | 0,7213 | **0,9639** | 0,616 dia | **84,4%** | **90,9%** |

Este foi o módulo que mais ganhou com o volume, e é o único em que isso faz
sentido: o rótulo dele (`has_liquidity`, "o saldo cobre a mensalidade") é o
único dos quatro que **não é calculado a partir das features que o modelo vê** —
ele vem da simulação do fluxo de caixa, e as features são calendário. Sem
circularidade, mais dado ajuda de verdade. Acerto exato subiu de 82,2% para
84,4% e o ±1 dia de 89,2% para 90,9%, contra a heurística que erra 4,9 dias.

O MAE subiu 0,022 dia — dois centésimos de dia, e medido sobre 20.000 janelas de
teste em vez de 1.200. O número da v1 tinha barra de erro grande demais para que
essa diferença signifique alguma coisa.

O split deste módulo **já é por cliente** no código dele, e está certo: a série
diária é fortemente autocorrelacionada e um split por janela vazaria o padrão do
cliente. Foi o único dos quatro que já estava com o split certo — vale citar
isso na banca se perguntarem sobre metodologia de validação.

---

## 5. O que isto quer dizer, em uma página

1. **A base agora é uma só, com quatro visões.** É a arquitetura que você
   descreveu, e ela está medida: interseção deixou de ser zero, MRR é o mesmo em
   todas as tabelas, a escala de 50,2× virou 1,016×.

2. **O volume não era o problema — nos módulos cujo rótulo é calculado.**
   Triplicar para 120.000 moveu a AUC do Módulo 1 em 0,0155, e 0,0114 disso é só
   volume na base antiga. O modelo continua entregando 98% do teto teórico, como
   já entregava.

   O **Módulo 3 é o contraexemplo que fecha o argumento**: lá o volume ajudou de
   verdade (acerto exato 82,2% → 84,4%, ±1 dia 89,2% → 90,9%). E ele é
   justamente o único dos quatro cujo rótulo **não** é calculado a partir das
   features que o modelo vê — `has_liquidity` vem da simulação do fluxo de
   caixa, as features são calendário. Onde não há circularidade, mais dado
   melhora. Onde há, não melhora. Os quatro módulos, na mesma base, no mesmo
   dia, separam as duas coisas.

3. **O rótulo é o problema, e agora isso é uma medida, não uma opinião.** Teto
   de Bayes 0,7249, modelo 0,7095. O voluntário empata com a regra na terceira
   casa. Enquanto a resposta for calculada por fórmula a partir das features que
   o modelo vê, é isso que dá.

4. **A saída já está no plano de 19–29/09 e não é modelo nenhum:** é o
   `DELETE /clientes/{id}`, que grava `cancelado_em` e dá ao sistema, pela
   primeira vez, um desfecho observado. É a única coisa que transforma "o modelo
   reproduz a regra" em "o modelo aprendeu algo que a regra não sabia".

5. **Para a banca, isto é material melhor do que uma AUC alta.** Uma AUC de 0,97
   numa base sintética com rótulo calculado é uma bandeira vermelha, e o
   professor vai enxergar. "Medimos o teto de Bayes da nossa própria base,
   ficamos a 2% dele, identificamos que o limite é a circularidade do rótulo e
   especificamos a correção" é uma resposta que nenhum TCC de graduação
   costuma ter.

---

## 6. O que ainda falta

- **Recalibrar o `threshold_percentile` do autoencoder** na base v2. Está na
  especificação do Bloco B.
- **Split por cliente no `train_all`** para os Módulos 1 e 4. Hoje não há
  vazamento medido, mas a garantia não pode depender de sorte.
- **Rótulo latente.** Os quatro latentes (`satisfacao`, `fit_produto`,
  `pressao_preco`, `saude_financeira`) já estão gravados na população e **não
  entram como feature em nenhum modelo** — estão lá exatamente para esta etapa,
  que tem portão próprio e não foi antecipada aqui de propósito: se eu tivesse
  trocado o rótulo junto, a comparação de métricas acima não isolaria nada.

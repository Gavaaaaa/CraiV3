# Classificador de falha v4: o que foi medido

Gerado a partir de `metricas_v4.json` (treino de 2026-10-08, semente 42). **Nada foi promovido.** O classificador de produção continua sendo o treinado na base v2.

A base v4 tem as mesmas 120.000 cobranças e as mesmas features da v2. Só o rótulo muda: a cobrança recupera por um mecanismo (saldo do cliente na data da nova tentativa, causa da falha, método de pagamento), e não pelo sorteio de uma fórmula. O desenho, escrito antes de medir, está em `DESENHO.md`. A base é sintética e o mecanismo foi desenhado pelo projeto: **isto não é recuperação observada.**

## O critério, escrito antes de medir

> A base nova resolve o problema declarado se, no holdout, com recall >= 0,90, a precisao do cenario A passar a da v2 (0,4768 com recall de 0,90) por mais que o desvio da precisao entre as dobras do GroupKFold. Escrito antes de medir. Satisfazer o criterio NAO promove nada.

**Resultado: não satisfeito.** Com recall de 90%, a precisão do cenário A é 45,6%; a da v2 é 47,7%. A diferença é -0,0207, e o desvio exigido era 0,0085.

O critério compara precisões entre duas bases com taxas de recuperação diferentes (40,5% no teste da v2, 36,7% no da v4), e com menos recuperáveis a precisão cai com o mesmo modelo. A medida que não depende disso é a taxa de falsos positivos (dos que **não** recuperam, quantos foram marcados): com recall de 90% ela é 67,2% na v2 e 62,1% no cenário A. Ou seja, o rótulo novo **reduz pouco** os falsos positivos; não resolve o problema. O critério declarado não foi reescrito depois de ver o resultado: continua não satisfeito.

## Os cenários

- **A:** as 11 features de hoje (o contrato de produção).
- **B:** as de A, mais o perfil do pagador e a razão entre a fatura e o ticket médio.
- **C (exploratório):** as de B, mais a previsão de liquidez do Módulo 3 para os dias das novas tentativas. Entrou depois de ver A e B, então está fora do critério. As ressalvas dele estão numa seção própria, abaixo.
- **Teto com o futuro do caixa conhecido:** a probabilidade de recuperar dado o caixa simulado dos 7 dias seguintes à cobrança. É previsão perfeita do futuro: nenhum previsor chega lá, nem com o histórico de saldo. Serve de limite superior, não de meta.

O holdout é o mesmo da v2: as mesmas 24.089 cobranças de 6.636 clientes, com o rótulo novo. A coluna da v2 foi medida pelo mesmo script e pelas mesmas funções: é o artefato de produção no holdout da v2.

## Os números

| | v2 (hoje) | v4, cenário A | v4, cenário B | v4, cenário C | Teto (futuro do caixa) |
|---|---:|---:|---:|---:|---:|
| Taxa real de recuperação no teste | 40,5% | 36,7% | 36,7% | 36,7% | 36,7% |
| AUC | 0,7096 | 0,7363 | 0,7803 | 0,8438 | 0,9544 |
| AUC na validação cruzada (5 partes) | 0,7100 ± 0,0030 | 0,7305 ± 0,0036 | 0,7751 ± 0,0018 | 0,8401 ± 0,0034 |  |
| Precisão com recall de 93,8% | 46,2% | 43,0% | 45,9% | 49,5% | 76,0% |
| Precisão com recall de 90% | 47,7% | 45,6% | 47,6% | 53,3% | 78,7% |
| Precisão com recall de 80% | 51,4% | 49,2% | 52,3% | 62,3% | 85,1% |
| Precisão com recall de 70% | 54,8% | 53,3% | 58,5% | 69,3% | 91,0% |
| Precisão com recall de 50% | 60,0% | 60,2% | 67,8% | 78,8% | 98,4% |
| Falsos positivos com recall de 90% | 9.634 | 9.479 | 8.763 | 6.957 | 2.152 |
| Falsos positivos com recall de 70% | 5.641 | 5.406 | 4.381 | 2.733 | 614 |
| Taxa de falsos positivos com recall de 90% | 67,2% | 62,1% | 57,4% | 45,6% | 14,1% |
| Taxa de falsos positivos com recall de 70% | 39,4% | 35,4% | 28,7% | 17,9% | 4,0% |
| Ganho sobre a taxa de base com recall de 90% | 1,18× | 1,24× | 1,30× | 1,45× | 2,15× |
| Faixa alta do score: recuperam | 60,9% | 60,1% | 65,8% | 73,4% |  |
| Faixa média: recuperam | 37,6% | 32,5% | 30,9% | 26,9% |  |
| Faixa baixa: recuperam | 15,5% | 11,3% | 7,5% | 4,1% |  |

Como ler: a **taxa de falsos positivos** e o **ganho sobre a taxa de base** (precisão dividida pela taxa real de recuperação) comparam a v2 com a v4. A precisão e a contagem de falsos positivos comparam as colunas da v4 entre si. As faixas do score são cortes fixos nas predições fora-da-dobra do treino (30% de cima, 50% do meio, 20% de baixo).

## A matriz de confusão no holdout, no limiar que a regra escolhe

A regra é a de hoje: o maior limiar com recall de pelo menos 90%, escolhido nas predições fora-da-dobra do treino. Na v2 é o limiar em uso em produção.

| | v2 (hoje) | v4, cenário A | v4, cenário B | v4, cenário C |
|---|---:|---:|---:|---:|
| Limiar | 0,25 | 0,28 | 0,29 | 0,28 |
| Verdadeiros positivos | 9.150 | 7.964 | 8.111 | 8.035 |
| Falsos negativos (recuperáveis perdidos) | 606 | 867 | 720 | 796 |
| Falsos positivos | 10.657 | 9.544 | 9.280 | 7.408 |
| Verdadeiros negativos | 3.676 | 5.714 | 5.978 | 7.850 |
| Recall | 93,8% | 90,2% | 91,8% | 91,0% |
| Precisão | 46,2% | 45,5% | 46,6% | 52,0% |
| Especificidade | 25,6% | 37,4% | 39,2% | 51,4% |
| Taxa de falsos positivos | 74,4% | 62,6% | 60,8% | 48,6% |
| Fração marcada como recuperável | 82,2% | 72,7% | 72,2% | 64,1% |

## As provas de que o experimento é válido

- **Permutação do rótulo** (treino com o rótulo embaralhado, 3 sementes): AUC de 0,4895, 0,5284, 0,5054. Cai para perto de 0,50, como deve.
- **Treino contra holdout** (cenário A): AUC de 0,7578 no treino e 0,7363 no holdout.
- **Colunas ocultas:** o treino aborta se uma coluna `oculto_`, uma variável latente ou o próprio rótulo entra na matriz (testado).
- **Sem vazamento do futuro no cenário C:** trocar por lixo todo o saldo a partir do dia da cobrança não muda a previsão de liquidez (testado).
- **Features:** idênticas às da v2, linha a linha (conferido na geração e testado).
- **Rótulo por cliente:** o rótulo de um cliente não depende de quem mais está na tabela nem do intervalo de datas dela (testado).
- **Taxa de recuperação da base:** 36,6%, dentro da faixa declarada (30% a 50%). Nenhum parâmetro do gerador foi alterado depois de ver uma métrica de modelo.
- **Revisão independente:** não achou vazamento. As correções que ela pediu estão em `DESENHO.md`, na seção "O que mudou depois do desenho".

## O que cada feature pesa no cenário A (queda da AUC ao tirá-la)

| Feature | Queda da AUC |
|---|---:|
| `gateway_error_code` | 0,0799 |
| `day_of_month` | 0,0336 |
| `invoice_amount` | 0,0256 |
| `metodo_pagamento` | 0,0195 |
| `avg_ticket` | 0,0167 |
| `payment_history_score` | 0,0122 |
| `attempt_count` | 0,0049 |
| `tenure_months` | 0,0030 |
| `day_of_week` | 0,0000 |
| `failure_count_90d` | -0,0004 |
| `hour_of_day` | -0,0005 |

## O modelo de produção de hoje, aplicado à base v4 sem retreinar

AUC de 0,5648; no limiar em uso (0,25), recall de 86,0% e precisão de 38,4%. Ele aprendeu a fórmula da v2, que não é o mecanismo da v4.

## Ressalvas do cenário C

1. **Foi decidido depois de ver A e B.** Está fora do critério declarado.
2. **O previsor de liquidez foi treinado no mesmo gerador que produz o caixa da v4.** Ele conhece a forma do mundo que está prevendo. Com saldo de verdade, o ganho tende a ser menor.
3. **Supõe que o histórico de saldo do cliente chega ao sistema.** Hoje, em produção, a série de 30 dias do Módulo 3 é simulada a partir do identificador do cliente.
4. **Parte das cobranças tem janela incompleta.** Em 16,8% delas faltam dias antes da cobrança para completar os 30 da janela, e o que falta entra como zero.
5. **Os parâmetros do Prophet foram ajustados na série inteira de liquidez da v2,** que inclui datas posteriores a parte das cobranças. A LSTM e a janela de entrada só veem o passado.
6. **A previsão sozinha vale pouco** (AUC de 0,6692); o ganho vem dela combinada com as outras features.

## Leitura

1. **Trocar o rótulo, sozinho, reduz pouco os falsos positivos.** Com recall de 90%, a taxa de falsos positivos vai de 67,2% (v2) para 62,1% (v4, cenário A). No mundo da v4 o desfecho é quase todo decidido pelo saldo, e as 11 features de hoje enxergam pouco dele.
2. **O que reduz, dentro deste mundo simulado, é informação sobre o saldo.** Com a previsão de liquidez do Módulo 3 como feature (cenário C), os falsos positivos com recall de 90% caem de 9.479 para 6.957, e a faixa baixa do score passa a recuperar só 4,1% das vezes.
3. **O teto não é alcançável.** Os 2.152 falsos positivos dele supõem conhecer o caixa dos 7 dias seguintes. Ele só mostra que o resto do erro é futuro que ninguém sabe.
4. **Nada disto foi medido em cobrança real.** É o comportamento do modelo num mecanismo que o projeto desenhou.

## Como regerar

A partir de `app/`, com a base v2 em `app/data/v2/` e os modelos de produção em `app/models/`:

```
python -m crai.scripts.gerar_base_v4_classificador --seed 42 --out data/v4/ --v2 data/v2/
python -m crai.scripts.treinar_classificador_v4 --base data/v4/ --v2 data/v2/ --out models/v4/
```

O primeiro leva cerca de 1 minuto; o segundo levou 5 minutos na máquina deste treino (2 núcleos). As pastas `app/data/` e `app/models/` não são versionadas. `saida_do_treino.txt` é a saída do segundo comando; só o caminho da máquina foi trocado por `<repo>`.

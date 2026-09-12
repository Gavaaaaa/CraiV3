# README_treino — base de dados e treino/retreino dos modelos da CRAI

> **Gerado por** `python -m crai.scripts.gerar_readme_treino` a partir de `models/calibracao.json` e `docs/evidencia/treino/*.json`. Nao edite as tabelas a mao: regere. O teste `tests/test_readme_treino.py` confere que este arquivo bate com os JSONs.
>
> Fonte dos parametros: **`sintetico_calibrado`** · calibracao gerada em `2026-09-11T21:31:34+00:00` · versoes gravadas nos `meta.json`: scikit-learn 1.5.2, xgboost 2.1.1, torch 2.13.0+cu130, prophet 1.4.0.

## 0. Declaracao, antes de qualquer numero

**Isto NAO e treino com dado real de churn observado.** Nenhum dos quatro modelos abaixo viu um rotulo real de "esta cobranca foi recuperada" ou "este cliente cancelou" — esse dado nao existe publicamente, e dentro da CRAI ainda nao foi acumulado. O que este trabalho entrega, e so isso:

1. **Parte das features calibrada em dado real** de outras bases (Olist, BACEN, E-Commerce Customer Churn), feature a feature, com o status de cada uma declarado (secao 2) — e o resto declarado como inventado.
2. **O mecanismo de treino/retreino funcionando de ponta a ponta**: `train(fonte=...)` nos tres modulos + o candidato do voluntario, duas rodadas de volume por modelo, artefatos salvos e recarregados via `load()` (secao 3).
3. **Mitigacao explicita da circularidade** — o rotulo nao e funcao deterministica das features (secao 4.1).
4. **Uma checagem honesta fora do dominio**, com a queda de performance registrada como saiu (secao 4.2).

Os quatro bloqueios para treinar com dado real **continuam de pe e nao sao resolvidos aqui** (secao 7).

## 1. Fonte e volume por modelo

| Modelo | Codigo | `train()` | Fonte usada | Volume (rodada 1 -> rodada 2) | Artefatos em `models/` |
|---|---|---|---|---|---|
| FailureClassifier | `crai/ml/failure_classifier.py` | `train(n_samples, fonte)` | `sintetico_calibrado` | 3000 -> 6000 transacoes | `xgb_failure_classifier.joblib`, `rf_failure_classifier.joblib`, `label_encoders.joblib`, `feature_names.joblib`, `train_metrics.json`, `failure_classifier_meta.json` |
| AnomalyDetector | `crai/ml/anomaly_detector.py` | `train(n_samples, fonte)` | `sintetico_calibrado` | 5500 -> 11000 clientes | `autoencoder.pt`, `autoencoder_scaler.pkl`, `autoencoder_meta.json` |
| PaydayInference | `crai/ml/payday_inference.py` | `train(n_samples, fonte)` | `sintetico_calibrado` | 600 -> 1200 clientes x 180 dias | `payday_lstm.pt`, `payday_prophet_{CLT,PJ,freelancer}.json`, `payday_meta.json` |
| risk_scorer voluntario (candidato) | `crai/ml/voluntary_risk.py` -> encaixe em `churn_voluntary/risk_scorer.py` | `train(n_samples, fonte)` | `sintetico_calibrado` | 2000 -> 4000 eventos | `voluntary_risk_candidato.joblib` + `_meta.json` (**nao ativo** — ver 3.4) |

`fonte="sintetico"` (default) continua sendo o gerador de sempre, byte-identico (`tests/test_synthetic_data.py::TestDefaultNaoMudou` trava o hash). `fonte="sintetico_calibrado"` le `models/calibracao.json`, gerado por `python -m crai.scripts.preparar_amostra_real` — e o unico arquivo de `models/` versionado no git, porque e um punhado de numeros medidos, nao um binario.

### 1.1 Doadores reais

| Fonte | O que e | Licenca | Prova de identidade | Papel |
|---|---|---|---|---|
| **A** Olist | 300 transacoes reais estratificadas (de 103.877) | CC BY-NC-SA 4.0 (academico) | SHA-256 da amostra `39485c22968bd733...` | valor da fatura (MLE lognormal), hora / dia da semana / dia do mes |
| **B** BACEN SGS 21084 | inadimplencia PF mensal | ODbL | media 3,92 / ultimo 5,81 — origem: DECLARADO no DATA_CARD 3 — API e cache indisponiveis nesta execucao | peso de `insufficient_funds` nos codigos de erro |
| **E** E-Commerce Customer Churn (Kaggle, ankitverma2010) | 5630 clientes, 20 colunas, rotulo `Churn` | nao declarada no Kaggle — academico apenas, bruto fora do git | SHA-256 do xlsx `db70f1e34bc53268...` | `days_since_last(_login)`, `features_used_30d`, `avg_session_min`, `tickets_30d`, `nps_last` |

Mapeamento fixo da Fonte E (nenhum outro foi inventado): `DaySinceLastOrder` -> `days_since_last` (risk_scorer) e `days_since_last_login` (AnomalyDetector); `OrderCount` -> `features_used_30d`; `HourSpendOnApp` -> `avg_session_min`; `Complain` -> `tickets_30d`; `SatisfactionScore` -> `nps_last`. O `Churn` real **nao entra em parametro nem em treino** — so na checagem fora do dominio.

## 2. Tabela de features — ancorada / proxy fraco / sintetica sem doador

Puxada de `models/calibracao.json -> proveniencia_features` (a mesma lista que `train()` devolve em `proveniencia` e grava no `meta.json`).

### 2.1 FailureClassifier

| Feature | Status | Fonte / coluna real | Nota |
|---|---|---|---|
| `tenure_months` | 100% sintetica, SEM doador | - | mistura exponencial+uniforme a mao |
| `day_of_month` | ancorada em dado real | A: Olist (300 transacoes) / `order_purchase_timestamp.day` | histograma empirico do dia de order_purchase_timestamp, suavizacao 0,5 por dia |
| `invoice_amount` | ancorada em dado real | A: Olist (300 transacoes) / `payment_value` | MLE de log(payment_value) nas 300 reais (Fonte A). Mediana ~R$ 100: ticket de e-commerce, nao de SaaS B2B (DATA_CARD 2.3). |
| `avg_ticket` | proxy FRACO de coluna real | A: Olist (300 transacoes) / `payment_value` | derivada de invoice_amount x U(0,85; 1,15); a relacao ticket/fatura e inventada |
| `gateway_error_code` | proxy FRACO de coluna real | B: BACEN SGS 21084 / `serie 21084 (inadimplencia PF)` | fatia de insufficient_funds x 1.482 (ultimo/media da SGS 21084: 5.81/3.92, origem: DECLARADO no DATA_CARD 3 — API e cache indisponiveis nesta execucao); demais codigos renormalizados. Hipotese declarada no DATA_CARD 3. |
| `card_brand` | 100% sintetica, SEM doador | - | mix de bandeiras a mao |
| `payment_history_score` | 100% sintetica, SEM doador | - | beta(5,2) + tenure, a mao |
| `failure_count_90d` | 100% sintetica, SEM doador | - | poisson(1,5) a mao |
| `hour_of_day` | proxy FRACO de coluna real | A: Olist (300 transacoes) / `order_purchase_timestamp.hour` | histograma empirico da hora de compra (proxy: hora de compra em e-commerce, nao hora de cobranca de assinatura) |
| `day_of_week` | proxy FRACO de coluna real | A: Olist (300 transacoes) / `order_purchase_timestamp.dayofweek` | histograma empirico do dia da semana da compra (proxy) |
| `attempt_count` | 100% sintetica, SEM doador | - | mix 45/30/15/10 a mao |
| `recovered (rotulo)` | 100% sintetica, SEM doador | - | modelo causal declarado (DATA_CARD 7) + N(0, 0,05) + Bernoulli + 2% das linhas com rotulo sorteado ao acaso, ignorando a regra. Pequeno de proposito: cada ponto custa AUC e o gate G3 exige AUC >= 0,70; o objetivo e impedir que o modelo decore a formula, nao apagar o sinal |

Resumo: 2 ancorada em dado real, 4 proxy FRACO de coluna real, 6 100% sintetica, SEM doador.

### 2.2 AnomalyDetector

| Feature | Status | Fonte / coluna real | Nota |
|---|---|---|---|
| `tenure_days` | 100% sintetica, SEM doador | - | gamma(2,5; 180) a mao |
| `mrr_brl` | 100% sintetica, SEM doador | - | lognormal(8,5; 0,7) a mao |
| `seats` | 100% sintetica, SEM doador | - | poisson(15) a mao |
| `logins_7d` | 100% sintetica, SEM doador | - | fracao de logins_30d a mao |
| `logins_30d` | 100% sintetica, SEM doador | - | normal(seats x 18 / x 5) a mao |
| `feature_adoption` | 100% sintetica, SEM doador | - | beta(5,2) / beta(2,5) a mao |
| `avg_session_min` | proxy FRACO de coluna real | E: E-Commerce Customer Churn / `HourSpendOnApp` | proxy fraco: HourSpendOnApp e horas/dia no app, nao minutos por sessao. Calibrado SO o coeficiente de variacao (0.2462); a escala (22 / 6 min) continua inventada. |
| `api_calls_7d` | 100% sintetica, SEM doador | - | lognormal a mao |
| `days_since_last_login` | ancorada em dado real | E: E-Commerce Customer Churn / `DaySinceLastOrder` | saudavel: media de DaySinceLastOrder (4.543 dias, n=5323) como scale da exponencial. Anomalo: saudavel + 7.5 dias (o deslocamento que o gerador default ja tinha). O corte por Churn do doador NAO foi usado para separar as populacoes: nele quem cancela tem MENOS dias desde o ultimo pedido, o inverso da hipotese do gerador — declarado, nao escondido. |
| `tickets_30d` | proxy FRACO de coluna real | E: E-Commerce Customer Churn / `Complain` | proxy fraco: Complain e um flag 0/1, nao contagem de tickets. lambda da Poisson = P(Complain) por populacao do doador (Churn=0: 0.2341; Churn=1: 0.5359) — unica feature em que a direcao do doador coincide com a do gerador. |
| `failed_pay_90d` | 100% sintetica, SEM doador | - | binomial(3; 0,05 / 0,35) a mao |
| `nps_last` | proxy FRACO de coluna real | E: E-Commerce Customer Churn / `SatisfactionScore` | proxy fraco: SatisfactionScore 1-5 reescalado x2 para 0-10 (saudavel: media 6.134, sd 2.760). Anomalo: media x 0.6707 (razao do gerador default). No doador quem cancela e MAIS satisfeito em media — direcao inversa, nao usada. |
| `is_anomalous (rotulo)` | 100% sintetica, SEM doador | - | populacao de origem; 15% dos anomalos recebem 3 das 8 features comportamentais da distribuicao saudavel (churn silencioso); 5% dos saudaveis recebem 2 features da distribuicao anomala (degradacao passageira) |

Resumo: 1 ancorada em dado real, 3 proxy FRACO de coluna real, 9 100% sintetica, SEM doador.

### 2.3 PaydayInference

| Feature | Status | Fonte / coluna real | Nota |
|---|---|---|---|
| `balance_norm` | 100% sintetica, SEM doador | - | sem doador publico para saldo diario de cliente: perfis CLT/PJ/freelancer, ancoras de payday e gasto diario continuam inventados (DATA_CARD 6: 'nao calibravel') |
| `has_liquidity (rotulo)` | 100% sintetica, SEM doador | - | saldo >= 1 mensalidade; 10% das entradas chegam 1-5 dias depois: a LSTM nao pode aprender so o calendario; 2% dos dias com gasto extra de 0,5-1,5 mensalidade |
| `day_of_month (sin/cos)` | 100% sintetica, SEM doador | - | calendario |
| `weekday` | 100% sintetica, SEM doador | - | calendario |
| `profile (CLT/PJ/freelancer)` | 100% sintetica, SEM doador | - | mix 50/30/20 a mao |

Resumo: 0 ancorada em dado real, 0 proxy FRACO de coluna real, 5 100% sintetica, SEM doador.

### 2.4 risk_scorer_voluntario

| Feature | Status | Fonte / coluna real | Nota |
|---|---|---|---|
| `days_since_last` | ancorada em dado real | E: E-Commerce Customer Churn / `DaySinceLastOrder` | histograma empirico de DaySinceLastOrder (n=5323, 0-46 dias) — mesma unidade da feature |
| `features_used_30d` | proxy FRACO de coluna real | E: E-Commerce Customer Churn / `OrderCount` | proxy fraco: OrderCount (pedidos acumulados, n=5372) no lugar de features distintas usadas em 30 dias |
| `mrr` | 100% sintetica, SEM doador | - | sem doador: lognormal inventada (mean 7,5 / sigma 0,8, R$ 99-50.000) |
| `evento_cancelamento` | 100% sintetica, SEM doador | - | sem doador: 85% Session Started / 10% Downgrade / 5% Cancellation |
| `evento_downgrade` | 100% sintetica, SEM doador | - | sem doador: 85% Session Started / 10% Downgrade / 5% Cancellation |
| `evento_sessao` | 100% sintetica, SEM doador | - | sem doador: 85% Session Started / 10% Downgrade / 5% Cancellation |
| `churn (rotulo)` | 100% sintetica, SEM doador | - | churn = Bernoulli(regras fixas do risk_scorer + N(0, 0,08)), com 3% de excecoes ao acaso. Nao e churn observado: e o que as regras dizem, com ruido suficiente para o modelo nao decorar a formula. |

Resumo: 1 ancorada em dado real, 1 proxy FRACO de coluna real, 5 100% sintetica, SEM doador.

## 3. Mecanismo de retreino — duas rodadas por modelo, lado a lado

Comandos exatos (de `app/`), com os artefatos sobrescritos em `models/` a cada rodada e a verificacao `load()` apos cada `train()`:

```
python -m crai.scripts.train_all --fonte sintetico_calibrado \
    --classifier-samples 3000 --anomaly-samples 5500 --payday-customers 600 \
    --voluntario-samples 2000 --saida-json docs/evidencia/treino/rodada_baixa.json
python -m crai.scripts.train_all --fonte sintetico_calibrado \
    --classifier-samples 6000 --anomaly-samples 11000 --payday-customers 1200 \
    --voluntario-samples 4000 --saida-json docs/evidencia/treino/rodada_alta.json
```

Rodada 1: 69.4 s · rodada 2: 131.6 s (CPU). Logs completos em `docs/evidencia/treino/log_rodada_*.txt`.

### 3.1 FailureClassifier (XGBoost 0,7 + RandomForest 0,3)

| Metrica | n = 3000 | n = 6000 | delta |
|---|---|---|---|
| AUC-ROC (holdout 20%) | 0,6695 | 0,6669 | -0,0026 |
| Acuracia @ limiar 0,25 | 0,5250 | 0,5258 | +0,0008 |
| Precisao (recuperado) | 0,4862 | 0,4878 | +0,0016 |
| Recall (recuperado) @ 0,25 | 0,9114 | 0,9191 | +0,0077 |
| F1 (recuperado) | 0,6341 | 0,6373 | +0,0032 |
| e-Profit medio (R$) | 143,7600 | 135,3700 | -8,3900 |
| Linhas de treino | 2400 | 4800 | +2400 |
| Linhas de teste | 600 | 1200 | +600 |
| `fonte_usada` | `sintetico_calibrado` | `sintetico_calibrado` | |
| recarregou via `load()` | sim | sim | |

Recall OPERACIONAL (regra de e-Profit, que e quem decide): 1,0000 -> 1,0000.

**Leia com cuidado.** Com o dobro de volume a AUC do holdout ficou estatisticamente no mesmo lugar (a diferenca esta dentro do erro-padrao de um holdout de 600-1.200 linhas, ~0,02). Dois pontos nao provam tendencia; a curva de volume com 3 seeds (3.5) prova. E a AUC esta **abaixo do piso 0,70** do gate G3 do `sprints.md` — ver 4.3, porque isso e esperado e esta declarado, nao escondido.

### 3.2 AnomalyDetector (autoencoder 12-4-12, treinado so em saudaveis)

| Metrica | n = 5500 | n = 11000 | delta |
|---|---|---|---|
| ROC-AUC (saudaveis held-out + anomalos) | 0,9789 | 0,9810 | +0,0021 |
| Average precision | 0,9503 | 0,9541 | +0,0038 |
| Precisao @ p95 | 0,9213 | 0,9240 | +0,0027 |
| Recall @ p95 | 0,8990 | 0,9333 | +0,0343 |
| F1 | 0,9100 | 0,9286 | +0,0186 |
| Threshold (p95 do erro saudavel) | 0,8717 | 0,7482 | -0,1235 |
| Separacao anomalo/saudavel | 4,4600 | 5,4600 | +1,0000 |
| Epocas | 100 | 100 | +0 |
| Saudaveis no treino | 4254 | 8508 | +4254 |
| `fonte_usada` | `sintetico_calibrado` | `sintetico_calibrado` | |
| recarregou via `load()` | sim | sim | |

### 3.3 PaydayInference (LSTM 0,6 + Prophet 0,4)

| Metrica | n = 600 | n = 1200 | delta |
|---|---|---|---|
| ROC-AUC diario — LSTM | 0,9546 | 0,9616 | +0,0070 |
| ROC-AUC diario — Prophet | 0,6957 | 0,7370 | +0,0413 |
| ROC-AUC diario — ensemble | 0,9418 | 0,9519 | +0,0101 |
| MAE da janela otima (dias) — ensemble | 0,6760 | 0,6820 | +0,0060 |
| MAE da janela otima (dias) — heuristica de dia fixo | 4,4310 | 4,5500 | +0,1190 |
| Acerto exato | 0,8106 | 0,7908 | -0,0198 |
| Acerto +-1 dia | 0,8974 | 0,8707 | -0,0267 |
| Janelas de treino | 8160 | 16320 | +8160 |
| Clientes de teste | 120 | 240 | +120 |
| `fonte_usada` | `sintetico_calibrado` | `sintetico_calibrado` | |
| recarregou via `load()` | sim | sim | |

### 3.4 risk_scorer voluntario — candidato (GradientBoosting sobre `FEATURES_DE_RISCO`)

| Metrica | n = 2000 | n = 4000 | delta |
|---|---|---|---|
| AUC vs rotulo ruidoso | 0,7133 | 0,7517 | +0,0384 |
| AUC das PROPRIAS REGRAS vs rotulo (teto) | 0,7405 | 0,7578 | +0,0173 |
| Brier | 0,1887 | 0,1822 | -0,0065 |
| MAE entre p(modelo) e regra | 0,0561 | 0,0413 | -0,0148 |
| Correlacao p(modelo) x regra | 0,9415 | 0,9705 | +0,0290 |
| Eventos de treino | 1600 | 3200 | +1600 |
| `fonte_usada` | `sintetico_calibrado` | `sintetico_calibrado` | |
| recarregou via `load()` | sim | sim | |

O candidato **nao esta ativo**: o treino grava `voluntary_risk_candidato.joblib`, que `carregar_modelo()` nao le. Promover ao nome que o scorer carrega (`voluntary_risk.joblib`) e um passo explicito — `VoluntaryRiskModel.ativar()` ou `train_all --ativar-voluntario` — porque a partir dai o pipeline voluntario passa a decidir pelo modelo e nao pelas regras. Como o rotulo do candidato SAO as regras com ruido, ele nao sabe nada que as regras nao saibam (`corr_vs_regra` ~0,97): prova o encaixe, nao melhora a decisao. `tests/test_train_fonte.py::TestVoluntarioCandidato` trava os dois lados (sem ativar = regras identicas; ativado = modelo decide).

### 3.5 Curva de volume — o mecanismo responde a mais dado (3 seeds do gerador)

Mesmo `train(fonte="sintetico_calibrado")`, em 4 volumes, com 3 seeds diferentes do gerador em cada volume (`python -m crai.scripts.sanity_check_fora_do_dominio`). E a prova que dois pontos nao dao: a media sobe e a variancia entre seeds cai.

FailureClassifier:

| n_amostras | AUC media | desvio-padrao (3 seeds) |
|---|---|---|
| 1000 | 0,6264 | 0,0372 |
| 3000 | 0,6810 | 0,0086 |
| 6000 | 0,6861 | 0,0205 |
| 12000 | 0,6961 | 0,0052 |

risk_scorer voluntario (candidato):

| n_amostras | AUC media | desvio-padrao (3 seeds) |
|---|---|---|
| 1000 | 0,7016 | 0,0507 |
| 3000 | 0,7507 | 0,0184 |
| 6000 | 0,7463 | 0,0109 |
| 12000 | 0,7404 | 0,0029 |

O voluntario satura em torno de 0,75 porque o proprio rotulo tem um teto: a AUC das regras contra o rotulo ruidoso e ~0,76 (tabela 3.4). Mais dado reduz a variancia, nao ultrapassa o teto — comportamento correto para um rotulo com ruido irredutivel.

## 4. Riscos conhecidos e mitigacao

### 4.1 Circularidade — o modelo decorar a formula que gerou o rotulo

O risco: se o rotulo for funcao deterministica das features, o modelo aprende a formula, a AUC vai a ~0,99 e o numero nao significa nada. A mitigacao esta em `crai/ml/synthetic_data.py`, ponto a ponto onde o rotulo e calculado, e os valores vem de `calibracao.json -> parametros`:

| Gerador | Rotulo | O que ja existia | O que entrou no modo calibrado | Magnitude | Por que essa magnitude |
|---|---|---|---|---|---|
| `generate_dataset` | `recovered` | `p_recovery` leva N(0, 0,05) e o rotulo e um sorteio Bernoulli(p), p em [0,05; 0,95] — a mesma linha pode cair dos dois lados | `p_excecao_rotulo`: fracao de linhas com rotulo sorteado ao acaso, ignorando a regra | 2% | 2% das linhas com rotulo sorteado ao acaso, ignorando a regra. Pequeno de proposito: cada ponto custa AUC e o gate G3 exige AUC >= 0,70; o objetivo e impedir que o modelo decore a formula, nao apagar o sinal |
| `generate_behavioral_dataset` | `is_anomalous` | nada — o rotulo ERA a populacao de origem, e as populacoes quase nao se sobrepoem (ROC-AUC 0,995 no README, marcado como bandeira vermelha) | `p_excecao_anomalo`: anomalos que recebem 3 das 8 features comportamentais da distribuicao saudavel (churn silencioso); `p_excecao_saudavel`: saudaveis com 2 features degradadas (passageiro) | 15% / 5% | o rotulo deixa de ser dedutivel das features; a AUC em dominio caiu de 0,995 para ~0,98 e nao mais porque 8 das 12 features nao tem doador e continuam separadas a mao — o numero honesto e o da secao 4.2 |
| `generate_liquidity_series` | `has_liquidity` | saldo quase deterministico do calendario (a LSTM podia aprender o calendario, nunca o cliente) | `p_atraso_salario` (entrada chega 1-5 dias depois) e `p_gasto_imprevisto` (gasto extra de 0,5-1,5 mensalidade) | 10% por entrada / 2% por dia | choques que o mundo real tem e a serie nao tinha; a liquidez media caiu de ~66% para ~54% dos dias |
| `generate_voluntary_dataset` | `churn` | (gerador novo) | Bernoulli(regras + N(0, 0,08)) + `p_excecao_rotulo` | sd 0,08 / 3% | o teto de AUC das regras contra o rotulo fica em ~0,76: o modelo pode aprender a tendencia, nao decorar a formula |

Cada decisao esta comentada no codigo, no ponto exato (`synthetic_data.py`, blocos "Anti-circularidade"), e `tests/test_synthetic_data.py::TestAntiCircularidade` verifica que os mecanismos de fato alteram o rotulo sem alterar as features.

### 4.2 Checagem fora do dominio (Etapa C) — a queda esperada, registrada como saiu

`python -m crai.scripts.sanity_check_fora_do_dominio` — so inferencia, nada e treinado com dado real. Resultado completo em `docs/evidencia/treino/fora_do_dominio.json`.

| Modelo | Em dominio (sintetico calibrado, holdout novo) | Fora do dominio (dado real) | Rotulo real | Leitura |
|---|---|---|---|---|
| AnomalyDetector | ROC-AUC 0,9860 (flag 12,9%) | ROC-AUC 0,5058 (flag 0,1%, recall de churn 0,0%) | `Churn` da Fonte E, 5068 clientes com as 4 features presentes | queda esperada: o doador tem direcao INVERTIDA em dias/satisfacao (quem cancela pediu mais recentemente e esta mais satisfeito) e o modelo so ve 4 das 12 features; um AUC ~0,5 aqui e o resultado honesto, nao um bug |
| risk_scorer — regras fixas | (as regras nao tem holdout) | ROC-AUC 0,4053 vs churn; risco medio 0,263 | `Churn` da Fonte E | abaixo de 0,5: no doador quem cancela pediu MAIS recentemente, o inverso da regra de inatividade — a regra e de SaaS por assinatura, o dado e de e-commerce |
| risk_scorer — candidato | AUC 0,7517 vs rotulo ruidoso | ROC-AUC 0,4295 vs churn | `Churn` da Fonte E | o candidato aprendeu as REGRAS com ruido, nao churn observado — fora do dominio ele so pode ser tao bom quanto as regras |
| FailureClassifier | p_recovery mediana 0,4473 (p25-p75 0,3095-0,5912) | p_recovery mediana 0,6271 (p25-p75 0,5878-0,6664) nas 300 transacoes reais da Olist | **nao existe** | sem rotulo nao ha AUC; o que se mede e se a distribuicao de score no dado real e plausivel e parecida com a do holdout — nao prova acerto |
| PaydayInference | ROC-AUC diario 0,9519 | nao aplicavel | nao existe doador de serie de saldo | fica so a metrica em dominio, declarada como tal |

**A queda e o teste funcionando.** Um autoencoder que caisse de 0,98 para 0,90 num rotulo real de outro dominio seria suspeito; cair para ~0,5 e o que se espera de um modelo que aprendeu a geometria de um gerador — ele nao esta artificialmente perfeito, esta honestamente limitado ao dominio em que foi treinado. Nada foi ajustado para os numeros subirem.

### 4.3 A AUC do classificador ficou abaixo do piso 0,70 do gate G3

Medido: 0,6695 (n=3000) e 0,6669 (n=6000) com fonte calibrada; com a fonte default nos MESMOS volumes, 0,680 e 0,676. O 0,7029 citado no `README.md` (secao 4.6) foi medido com n=15.000 na fonte default. Duas causas, as duas declaradas:

- **Volume.** O piso 0,70 so aparece perto de 15.000 linhas (curva 3.5: 0,696 em 12.000). Os volumes 3.000 -> 6.000 sao os pedidos para a prova de retreino, nao os do gate.
- **Calibracao do valor.** O `DATA_CARD.md` (2.3 e 7) previa: com a fatura calibrada na Olist (mediana ~R$ 100), o termo `- clip((valor - 500)/5000, 0, 0,15)` do modelo causal fica ~0 em quase todas as linhas — o sinal do valor desaparece e a AUC cai. Um parametro medido e declarado vale mais que um inventado que parecia certo.

Consequencia pratica que precisa ficar escrita: `tests/test_metricas_declaradas.py` exige que a AUC do `train_metrics.json` presente em `models/` esteja em [0,70; 0,92] **e** seja citada na linha 4.6 do `README.md`. Com os artefatos desta rodada em `models/`, esses dois testes reprovam — e e o comportamento correto do gate de honestidade, nao um defeito deste trabalho. Num clone limpo (`models/` esta no `.gitignore`) eles pulam e a suite fica verde. A linha 4.6 do `README.md` nao foi alterada aqui de proposito; atualiza-la e decisao de quem mantem o README.

## 5. O que JA aprende com dado real hoje: `offer_bandit.py`

`crai/churn_voluntary/offer_bandit.py` (Modulo 4, Thompson Sampling) nao depende de nada sintetico: cada par (perfil, oferta) mantem um posterior Beta(alfa, beta) sobre a taxa de aceite, e **cada aceite/recusa REAL** que chega pelo webhook `/webhooks/retention-outcome` atualiza o posterior (`record_outcome`, chamado em `voluntary_agent.py` depois de `registrar_desfecho`), persistido em `models/bandit_state.json`, isolado por `tenant_id`. E aprendizado online com o desfecho observado — a prova positiva, separada, de que a arquitetura ja fecha o ciclo com dado real onde o dado real existe. O rotulo ali e **aceitacao de oferta**, nao churn; isso esta declarado em `churn_voluntary/README_treino.md`.

## 6. Versionamento — o artefato recarrega de forma previsivel

- `requirements.txt` fixa versao **exata** (`==`) de `scikit-learn`, `xgboost`, `torch` e `prophet` (antes: `torch>=2.3`, `prophet>=1.1`). `tests/test_train_fonte.py::TestVersionamento` reprova se voltar a faixa.
- Cada `meta.json` grava as versoes usadas NAQUELE treino (`calibracao.versoes_bibliotecas()`): python 3.11.15, scikit-learn 1.5.2, xgboost 2.1.1, torch 2.13.0+cu130, numpy 1.26.4, pandas 2.2.3, joblib 1.4.2, shap 0.46.0, prophet 1.4.0.
- `load()` de cada modulo chama `calibracao.conferir_meta()`: se a versao do ambiente diverge da gravada, imprime um aviso NOMINAL ("xgboost: treinado com 2.1.1, ambiente tem 2.2.0") em vez do erro generico ou do carregamento silenciosamente errado. O sufixo de build do torch (`+cpu` / `+cu130`) e ignorado na comparacao — e o mesmo formato de serializacao.

## 7. O que continua bloqueando o treino com dado real (nao resolvido aqui)

Os quatro bloqueios do `claude/crai-status-treino` e dos dois `README_treino.md` de pacote continuam exatamente onde estavam:

1. **Perfil real do cliente** — `DBPerfilProvider._consultar()` (`crai/agent/perfil_provider.py`) segue stub; 4 das 12 features do classificador de falha (`tenure_months`, `avg_ticket`, `payment_history_score`, `failure_count_90d`) continuam fabricadas por `SyntheticPerfilProvider`.
2. **Sinal real de cancelamento** — nao existe em lugar nenhum do sistema (nem Segment, nem webhook, nem HubSpot). O que `retention_log` grava e aceitacao de oferta. Sem esse sinal, o candidato do voluntario aprende as regras, nao churn.
3. **Vies de selecao do corte 0,60** — quem tem `risk_score < 0,60` nunca recebe oferta e nunca gera desfecho; o proprio modelo decide quem entra no dataset dele.
4. **Volume de ciclos com desfecho por webhook** — `recovery_cycles.db` e `retention_cycles.db` so viram dataset quando acumularem ciclos fechados de verdade (nao simulados), em producao, por tempo suficiente.

Este trabalho torna o pipeline **pronto para receber** esse dado — `train(fonte=...)` e o ponto onde uma fonte `"real"` entraria — mas nao o substitui.

## 8. Como reproduzir

```
cd app
pip install -r requirements.txt
python -m crai.scripts.preparar_amostra_real        # doadores + models/calibracao.json
pytest tests/test_synthetic_data.py tests/test_train_fonte.py -v
python -m crai.scripts.train_all --fonte sintetico_calibrado --saida-json rodada.json
python -m crai.scripts.sanity_check_fora_do_dominio --saida fora.json
python -m crai.scripts.gerar_readme_treino            # regera este arquivo
pytest tests/ -q
```

Dado bruto (Olist, E-Commerce) fica em `data/real/` fora do git e e baixado de espelhos publicos com SHA-256 conferido; `data/real/PROVENIENCIA.json` (copia em `docs/evidencia/treino/`) registra hashes, contagens, nulos e o `describe()` de cada coluna usada. Se a API do BACEN estiver fora de alcance, o script usa o cache local ou, na falta dele, os valores declarados no `DATA_CARD.md` — e grava qual dos tres usou.

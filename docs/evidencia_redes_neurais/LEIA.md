# Experimento das redes neurais (Rodada 4, Fase 5)

Gerado por `python -m crai.scripts.experimento_redes_neurais` em 2026-10-05T11:04:43.
Base: `app/data/v2` (sha256 conferidos contra o manifesto), semente 42. Tempo total: 161.9 segundos.

**Nada foi promovido.** Os modelos de produção em `app/models/` não mudaram. As redes treinadas ficam em `app/models/experimentos_rn/`.

## A regra de decisão, escrita antes de medir

- A rede só é recomendada para substituir o modelo atual se vencer por mais que o desvio da validação cruzada, sem piorar o recall no limiar nem a calibração.
- O Prophet só é recomendado para sair se a LSTM sozinha, ou a rede nova, for igual ou melhor no erro em dias e no acerto de ±1 dia, inclusive para o cliente com pouco histórico.
- Empate ou vantagem dentro do desvio: fica o modelo atual. Ele se explica pelo TreeSHAP, que é exato, e a rede precisaria de um método aproximado (pesa no Art. 20).

## 1. Classificador de falha: o modelo atual x a rede

As mesmas 11 features (importadas do módulo do modelo atual), a mesma separação por cliente e a mesma semente: 24089 cobranças de teste, de 6636 clientes. A rede é uma MLP do scikit-learn com camadas [64, 32].

**Comparação A, como em produção** (os dois treinados em todo o treino; o limiar é escolhido na validação e medido no teste):

| Modelo | AUC | Brier | Erro de calibração | Limiar (validação) | Recall no limiar | Precisão no limiar | Recall em 0,25 | Precisão em 0,25 |
|---|---|---|---|---|---|---|---|---|
| Modelo atual (artefato de produção) | 0,7096 | 0,2102 | 0,0194 | 0,30 | 88,6% | 48,3% | 93,8% | 46,2% |
| Rede neural | 0,7043 | 0,2114 | 0,0078 | 0,25 | 91,6% | 46,9% | 91,6% | 46,9% |

**Comparação B, com o limiar numa validação que nenhum dos dois viu no treino:**

| Modelo | AUC | Brier | Erro de calibração | Limiar (validação) | Recall no limiar | Precisão no limiar | Recall em 0,25 | Precisão em 0,25 |
|---|---|---|---|---|---|---|---|---|
| Modelo atual (mesmo algoritmo, retreinado) | 0,7093 | 0,2103 | 0,0190 | 0,25 | 93,6% | 46,2% | 93,6% | 46,2% |
| Rede neural | 0,7025 | 0,2126 | 0,0236 | 0,20 | 93,4% | 46,0% | 88,1% | 48,1% |

Brier e erro de calibração: quanto menor, melhor. O limiar é o maior da grade que mantém o recall em 90% ou mais na validação (a regra do modelo atual).

**Validação cruzada (GroupKFold de 5, por cliente):**

| Modelo | AUC média | Desvio | AUC em cada dobra |
|---|---|---|---|
| Modelo atual | 0,7104 | 0,0030 | 0,7088, 0,7066, 0,7104, 0,7146, 0,7113 |
| Rede neural | 0,7044 | 0,0039 | 0,7010, 0,6998, 0,7053, 0,7084, 0,7076 |

Diferença média (rede menos atual): **-0,0059**. Desvio de referência: **0,0039** (o maior entre o desvio de cada modelo e o da diferença).

**Teto de Bayes no holdout:** 0,7106 (o melhor que qualquer modelo pode fazer com estas features, calculado com o próprio gerador da base). O declarado para a base inteira é 0,7249.

**Teste de permutação do rótulo** (treino com o rótulo embaralhado; tem de dar perto de 0,5): modelo atual 0,5260, rede 0,5046.

**Recomendação pela regra:** A diferença de AUC na validação cruzada (-0.0059) não passa do desvio (0.0039): pela regra, fica o modelo atual.

## 2. Liquidez: o conjunto atual x as alternativas

Os mesmos clientes de teste do treino de produção (2000 clientes, 16924 janelas com resposta), a mesma janela de 30 dias com 5 features por dia e o mesmo horizonte de 14 dias. A LSTM e o Prophet são os de produção, carregados de `app/models/`; a rede de regressão é nova e prevê quantos dias faltam até o cliente ter saldo.

Prova de que os clientes de teste são os mesmos: o conjunto de produção, medido aqui, deu erro de 0,609 dia e AUC 0,9639; o treino de produção gravou 0,609 e 0,9639. **Confere.**

Pouco histórico: a janela só tem os últimos N dias reais; os outros entram zerados, para a LSTM e para a rede.

**Histórico completo (30 dias)**

| Modelo | Erro em dias | Acerto exato | Acerto de ±1 dia | CLT (±1 dia) | PJ (±1 dia) | Freelancer (±1 dia) |
|---|---|---|---|---|---|---|
| Conjunto atual (60% LSTM, 40% Prophet) | 0,609 | 84,4% | 90,6% | 97,2% | 85,5% | 78,0% |
| LSTM sozinha | 0,617 | 84,4% | 90,6% | 97,1% | 86,0% | 77,8% |
| Conjunto 80% LSTM, 20% Prophet | 0,610 | 84,5% | 90,7% | 97,2% | 86,0% | 77,9% |
| Conjunto 90% LSTM, 10% Prophet | 0,609 | 84,4% | 90,7% | 97,2% | 86,1% | 77,7% |
| Prophet só para cliente sem histórico | 0,617 | 84,4% | 90,6% | 97,1% | 86,0% | 77,8% |
| Rede de regressão (nova) | 0,579 | 77,9% | 87,9% | 94,2% | 80,7% | 79,2% |
| Prophet sozinho (referência) | 1,969 | 63,7% | 70,9% | 82,5% | 39,9% | 78,8% |
| Regra fixa por perfil (referência) | 4,857 | 10,9% | 13,9% | 15,5% | 11,5% | 12,4% |

**Pouco histórico: 14 dias**

| Modelo | Erro em dias | Acerto exato | Acerto de ±1 dia | CLT (±1 dia) | PJ (±1 dia) | Freelancer (±1 dia) |
|---|---|---|---|---|---|---|
| Conjunto atual (60% LSTM, 40% Prophet) | 1,074 | 76,7% | 83,1% | 92,2% | 67,0% | 77,8% |
| LSTM sozinha | 1,455 | 75,6% | 79,1% | 88,0% | 60,4% | 78,1% |
| Conjunto 80% LSTM, 20% Prophet | 1,296 | 75,9% | 79,9% | 88,2% | 63,3% | 78,0% |
| Conjunto 90% LSTM, 10% Prophet | 1,411 | 75,8% | 79,2% | 87,6% | 61,7% | 78,1% |
| Prophet só para cliente sem histórico | 1,455 | 75,6% | 79,1% | 88,0% | 60,4% | 78,1% |
| Rede de regressão (nova) | 1,067 | 67,0% | 75,3% | 81,8% | 60,2% | 76,7% |
| Prophet sozinho (referência) | 1,969 | 63,7% | 70,9% | 82,5% | 39,9% | 78,8% |
| Regra fixa por perfil (referência) | 4,857 | 10,9% | 13,9% | 15,5% | 11,5% | 12,4% |

**Pouco histórico: 7 dias**

| Modelo | Erro em dias | Acerto exato | Acerto de ±1 dia | CLT (±1 dia) | PJ (±1 dia) | Freelancer (±1 dia) |
|---|---|---|---|---|---|---|
| Conjunto atual (60% LSTM, 40% Prophet) | 1,002 | 75,7% | 82,5% | 90,6% | 67,4% | 79,0% |
| LSTM sozinha | 1,286 | 74,5% | 78,2% | 86,6% | 59,2% | 79,0% |
| Conjunto 80% LSTM, 20% Prophet | 1,119 | 75,2% | 80,0% | 88,1% | 63,2% | 79,0% |
| Conjunto 90% LSTM, 10% Prophet | 1,214 | 74,7% | 78,5% | 86,4% | 60,7% | 79,0% |
| Prophet só para cliente sem histórico | 1,286 | 74,5% | 78,2% | 86,6% | 59,2% | 79,0% |
| Rede de regressão (nova) | 1,100 | 64,9% | 74,1% | 80,3% | 59,1% | 76,3% |
| Prophet sozinho (referência) | 1,969 | 63,7% | 70,9% | 82,5% | 39,9% | 78,8% |
| Regra fixa por perfil (referência) | 4,857 | 10,9% | 13,9% | 15,5% | 11,5% | 12,4% |

**Sem histórico nenhum**

| Modelo | Erro em dias | Acerto exato | Acerto de ±1 dia | CLT (±1 dia) | PJ (±1 dia) | Freelancer (±1 dia) |
|---|---|---|---|---|---|---|
| Conjunto atual (60% LSTM, 40% Prophet) | 5,900 | 16,3% | 23,1% | 27,8% | 27,2% | 3,6% |
| LSTM sozinha | 11,413 | 2,3% | 3,2% | 2,9% | 4,0% | 2,9% |
| Conjunto 80% LSTM, 20% Prophet | 8,203 | 3,6% | 7,8% | 6,9% | 13,3% | 3,2% |
| Conjunto 90% LSTM, 10% Prophet | 8,553 | 3,6% | 5,6% | 4,4% | 10,4% | 2,9% |
| Prophet só para cliente sem histórico | 1,969 | 63,7% | 70,9% | 82,5% | 39,9% | 78,8% |
| Rede de regressão (nova) | 4,555 | 2,9% | 7,6% | 7,6% | 8,8% | 5,7% |
| Prophet sozinho (referência) | 1,969 | 63,7% | 70,9% | 82,5% | 39,9% | 78,8% |
| Regra fixa por perfil (referência) | 4,857 | 10,9% | 13,9% | 15,5% | 11,5% | 12,4% |

**Recomendação pela regra:** Pela regra, o Prophet fica: nem a LSTM sozinha nem a rede de regressão ficou igual ou melhor que o conjunto atual em todos os cortes (histórico completo e pouco histórico).

## O que este experimento não é

- A base é sintética: o que se mede é quem aprende melhor a regra que o próprio projeto escreveu, não o comportamento de clientes reais.
- A rede de classificação foi treinada com uma configuração só, sem busca de hiperparâmetros. Uma busca poderia mover o número; a regra de decisão pede vantagem acima do desvio justamente para não trocar de modelo por ruído.
- A rede de regressão recebe a janela achatada. Uma arquitetura recorrente nova, para regressão, não foi testada.
- A versão em PyTorch da rede de classificação não foi feita.
- O corte de pouco histórico zera os dias que faltam. Nenhum dos modelos foi treinado com janelas assim: o corte mede o que acontece hoje com um cliente novo.

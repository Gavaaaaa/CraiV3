# Relatorio - Modulo 3: Inferencia de Liquidez (LSTM + Prophet)

**Projeto:** CRAI - Churn Recovery Artificial Intelligence (TCC)
**Modulo:** 3 de 6 - Payday Inference
**Data:** julho/2026
**Seed:** 42 (reproduzivel)

---

## 1. Objetivo

Responder **quando** retentar a cobranca de um cliente cujo pagamento falhou
por saldo insuficiente. Retentativas em dias sem liquidez desperdicam taxas
de gateway e queimam tentativas do cartao; o modulo preve, para os proximos
14 dias, a probabilidade diaria de o cliente ter saldo, e devolve a janela
otima de retry para o Dunning Engine.

## 2. Por que LSTM + Prophet

O problema tem dois sinais complementares:

- **Padrao individual** — a serie de saldo de cada cliente tem seu ritmo
  proprio (dia do salario, gasto diario, folga de caixa). Uma **LSTM**
  processa a janela dos ultimos 30 dias e projeta os 14 seguintes de uma
  vez (seq2vec multi-rotulo, BCE).
- **Sazonalidade coletiva** — o mercado brasileiro concentra pagamentos no
  5o dia util, dia 10, 15, 20 e 30. Um **Prophet por perfil** (CLT, PJ,
  freelancer) ajustado na taxa agregada de liquidez captura esse calendario
  mesmo quando o historico individual e curto ou ruidoso (cold start).

O ensemble pondera 0.6 LSTM + 0.4 Prophet. A codificacao ciclica do dia do
mes (sin/cos) evita a descontinuidade dia 31 -> dia 1.

## 3. Arquitetura

```
serie 30 dias (5 feats/dia) -> LSTM(64, 2 camadas) -> Linear(32) -> 14 logits
                                                                       |
prior sazonal Prophet(perfil) ----------------------- 0.4 ----------> ensemble
                                                                       |
                                                    primeiro dia >= 0.5 = retry
```

Features por dia: has_liquidity, balance_norm (clip 0-5), sin/cos do dia do
mes, is_business_day.

## 4. Dados sinteticos

600 clientes x 180 dias (108.000 observacoes), tres perfis com as ancoras
de pagamento do mercado BR:

| Perfil | % | Entradas | Liquidez media |
|--------|---|----------|----------------|
| CLT | 50% | salario no 5o dia util + adiantamento ~dia 20 | 75.8% |
| PJ | 30% | notas nos dias 10/15/30 com atraso 0-3d e 15% inadimplencia | 41.5% |
| freelancer | 20% | 2-5 pagamentos/mes em dias aleatorios | 56.1% |

Split **por cliente** (80/20): janelas do mesmo cliente nunca aparecem em
treino e teste ao mesmo tempo, evitando vazamento de padrao individual.

## 5. Resultados (120 clientes de teste)

### 5.1 Previsao diaria (ROC-AUC, 14 dias a frente)

| Estrategia | ROC-AUC |
|------------|---------|
| Prophet (so sazonal) | 0.6816 |
| LSTM (so individual) | 0.9755 |
| Ensemble | 0.9695 |

### 5.2 Janela otima de retry (1.044 janelas com liquidez no horizonte)

| Estrategia | MAE (dias) | Acerto exato | ±1 dia | ±2 dias |
|------------|-----------|--------------|--------|---------|
| Heuristica de dias fixos (baseline do crai/) | 5.01 | 13.6% | 28.3% | 38.8% |
| Prophet | 2.45 | 59.8% | 63.1% | 68.8% |
| LSTM | 0.64 | 82.5% | 89.0% | 92.0% |
| **Ensemble** | **0.62** | 81.7% | **89.3%** | **92.6%** |

### 5.3 Por perfil (MAE heuristica -> ensemble)

| Perfil | Baseline | Ensemble | Reducao |
|--------|----------|----------|---------|
| CLT | 6.03 | 0.21 | 29x |
| PJ | 3.25 | 0.91 | 3.6x |
| freelancer | 4.36 | 1.47 | 3.0x |

### 5.4 Classificacao de perfil

Votacao das entradas de saldo nas ancoras de payday (5o dia util/dia 20 =
CLT; 10/15/30 = PJ; sem ancora = freelancer): **84%** de acuracia em
clientes nunca vistos.

## 6. Interpretacao para a banca

- A heuristica de dias fixos erra em media **5 dias** — num ciclo de dunning
  de 21 dias, isso queima 1-2 retentativas inuteis por cliente.
- O ensemble erra **0.62 dia** e acerta a janela com tolerancia de 1 dia em
  **89%** dos casos: a retentativa passa a cair no dia em que o dinheiro esta
  na conta.
- LSTM sozinha ja e forte quando ha 30 dias de historico; o Prophet
  contribui o prior sazonal interpretavel (figura `prior_sazonal.png`
  mostra o pico CLT apos o 5o dia util) e sustenta o cold start.
- Conexao com o e-Profit: retry no dia certo aumenta P(recuperacao) sem
  custo adicional — e a alavanca mais barata do pipeline.

## 7. Limitacoes conhecidas

- Dados sinteticos: as distribuicoes sao plausiveis, mas nao substituem
  series reais de open finance / gateway.
- O peso do ensemble (0.6/0.4) foi fixado a priori; poderia ser aprendido
  por validacao.
- Horizonte fixo de 14 dias: falhas proximas da virada do mes podem ter a
  liquidez logo apos o horizonte.
- O Prophet e ajustado por perfil agregado; nao ha um Prophet por cliente
  (custo de treino proibitivo em producao).

## 8. Proximos passos

- Aprender o peso LSTM/Prophet por perfil (stacking com regressao logistica).
- Retreino continuo com o resultado real das retentativas (Modulo 6 do
  roadmap de aprendizado).
- Integrar Pix Automatico como fallback quando a janela prevista passa de
  N dias.

## 9. Artefatos

| Arquivo | Conteudo |
|---------|----------|
| `models/lstm.pt` | pesos da LiquidityLSTM |
| `models/prophet_<perfil>.json` | Prophet serializado por perfil |
| `models/meta.json` | hiperparametros + clientes de teste |
| `reports/metricas.json` | todas as metricas desta pagina |
| `reports/scores.csv` | previsao vs real por janela de teste |
| `reports/figures/*.png` | figuras para a banca |

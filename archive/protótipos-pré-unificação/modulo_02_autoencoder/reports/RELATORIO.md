# Relatorio - Modulo 2: Autoencoder de Anomalias Comportamentais

**Projeto:** CRAI - Churn Recovery Artificial Intelligence
**Modulo:** 2 / 6
**Data:** 2026-06-25
**Status:** concluido localmente, pronto para integracao no agente (Modulo 5)

---

## 1. Objetivo

Identificar, sem rotulos, clientes SaaS B2B cujo padrao de uso recente
divergiu da norma da base. O modulo produz um sinal numerico (erro de
reconstrucao) que o agente combina com a metrica e-Profit para decidir se
vale a pena uma intervencao de retencao.

## 2. Por que Autoencoder

Comparado a alternativas mais simples:

| Abordagem            | Pro                                          | Contra                                                |
|----------------------|----------------------------------------------|-------------------------------------------------------|
| Isolation Forest     | Sem treino caro, leve                        | Sem embeddings reutilizaveis pelos Modulos 3-5        |
| One-Class SVM        | Boa teoria                                   | Escala mal acima de ~10k amostras                     |
| **Autoencoder denso**| Embeddings densos reutilizaveis, sem rotulo  | Treino mais caro (compensado em CPU comum em ~1 min)  |

A escolha foi guiada pela necessidade de integracao com o LangGraph
(Modulo 5), que vai consumir tanto o score quanto o embedding do bottleneck.

## 3. Arquitetura

- **Entrada:** 12 features comportamentais (logins, sessao, adocao, tickets,
  NPS, falhas de pagamento, etc).
- **Encoder:** 12 -> 32 -> 16 -> 4 (bottleneck), com ReLU e dropout 0.1.
- **Decoder:** simetrico ao encoder.
- **Loss:** MSE entre entrada e reconstrucao.
- **Treino:** apenas com clientes saudaveis - o modelo aprende a "geometria"
  do normal e reconstroi mal qualquer cliente fora dela.

## 4. Dados sinteticos

Como nao existe dataset publico de comportamento SaaS B2B brasileiro com
qualidade suficiente para o TCC, foi construido um gerador sintetico que
modela duas populacoes:

- **5000 saudaveis (~91%):** distribuicoes consistentes com SaaS maduro.
- **500 anomalos (~9%):** queda de uso, aumento de fricção e NPS reduzido.

O rotulo `is_anomalous` existe apenas como ground-truth para validacao - o
treino do autoencoder nao tem acesso a ele.

## 5. Resultados

### 5.1 Metricas globais

| Metrica            | Valor   |
|--------------------|---------|
| ROC-AUC            | 0.9960  |
| Average Precision  | 0.9700  |
| F1 maximo possivel | 0.9190  |

### 5.2 No threshold operacional (percentil 95 dos saudaveis)

| Metrica   | Valor  |
|-----------|--------|
| Threshold | 0.6701 |
| Precision | 66.08% |
| Recall    | 97.40% |
| F1        | 78.74% |

### 5.3 Matriz de confusao

|                  | Pred negativo | Pred positivo |
|------------------|--------------:|--------------:|
| **Real negativo**| 4750          | 250           |
| **Real positivo**| 13            | 487           |

### 5.4 Separacao das populacoes

- Erro medio - saudaveis: **0.29**
- Erro medio - anomalos: **3.38**
- Razao: **11.63x**

## 6. Interpretacao para a banca

O threshold p95 prioriza **recall sobre precision** propositalmente: e mais
caro perder um cliente em risco real (churn = receita perdida cumulativa)
do que enviar uma intervencao desnecessaria a um cliente saudavel (custo
operacional baixo). O **e-Profit do Modulo 4** vai filtrar os 250 falsos
positivos calculando se a intervencao economicamente compensa - clientes
saudaveis ali tendem a ter custo de retencao baixo e ROI negativo, sendo
descartados.

A explicabilidade vem do metodo `explicar()` do `AnomalyDetector`: ele
quebra o erro por feature, dando ao agente uma justificativa textual ("usuario
caiu em logins, NPS desabou, abriu 5 tickets") que pode ser usada na
geracao da mensagem de retencao via LLM.

## 7. Limitacoes conhecidas

1. **Dados sinteticos:** o modelo precisa ser revalidado quando dados reais
   de cliente piloto estiverem disponiveis.
2. **Sem temporalidade:** o autoencoder denso ignora a ordem dos eventos.
   Uma evolucao v2 seria um LSTM-autoencoder, mas isso esbarra no Modulo 3
   (LSTM + Prophet de liquidez) - vale evitar duplicidade de complexidade.
3. **Threshold fixo:** em producao precisara ser recalibrado periodicamente
   conforme a populacao evolui (concept drift).

## 8. Proximos passos

- [ ] Integrar `AnomalyDetector.score()` como ferramenta exposta ao agente
      LangGraph no Modulo 5.
- [ ] Modulo 3: LSTM + Prophet para predicao de liquidez.
- [ ] Modulo 4: Thompson Sampling para selecao de ofertas, consumindo o
      score deste modulo como feature contextual.

## 9. Artefatos

- `models/autoencoder.pt` - pesos treinados (PyTorch)
- `models/scaler.pkl` - StandardScaler ajustado
- `models/meta.json` - hiperparametros e features
- `reports/metricas.json` - metricas serializadas
- `reports/scores.csv` - score por cliente
- `reports/figures/` - distribuicao de erros, ROC, PR

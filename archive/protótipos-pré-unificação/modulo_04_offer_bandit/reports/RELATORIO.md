# Relatorio - Modulo 4: Selecao de Ofertas via Thompson Sampling

**Projeto:** CRAI - Churn Recovery Artificial Intelligence (TCC)
**Modulo:** 4 de 6 - Offer Selector (Multi-Armed Bandit)
**Data:** julho/2026
**Seed:** 42 (reproduzivel)

---

## 1. Objetivo

Quando um cliente demonstra risco de cancelar (visitou a pagina de
cancelamento, clicou em downgrade, ficou inativo), o CRAI precisa decidir
**qual oferta de retencao** apresentar. Ofertas diferentes convertem
diferente por perfil — e tem custos muito diferentes. O Modulo 4 aprende
essa relacao **online**, sem regras programadas, e escolhe a oferta que
maximiza o e-Profit esperado.

## 2. Por que Thompson Sampling

A alternativa anterior do CRAI era um epsilon-greedy com epsilon fixo de
20%: para sempre, 1 em cada 5 clientes recebe uma oferta aleatoria — mesmo
depois que o sistema ja sabe qual e a melhor. Thompson Sampling resolve
isso com um posterior Beta(α, β) por par (perfil, oferta):

- a cada decisao, AMOSTRA uma taxa de aceite de cada posterior e escolhe a
  melhor oferta segundo a amostra;
- bracos incertos tem posteriores largos e as vezes amostram alto — sao
  explorados naturalmente;
- conforme os dados chegam, os posteriores afinam e a exploracao decai
  sozinha, sem hiperparametro para calibrar.

## 3. O diferencial: otimizar e-Profit, nao conversao

A escolha nao e `argmax p_amostrado`, e sim:

```
escolha = argmax_o  p_amostrado(o) x LTV_retido - custo(o)
```

Isso muda a decisao na pratica. No perfil CLT (MRR mediano R$ 299), a
consulta com CS e o braco que MAIS CONVERTE (50% de aceite), mas custa
R$ 250 de tempo humano — em e-Profit ela perde para o desconto de 10%
(44% de aceite, custo R$ 90). Um bandit que otimiza conversao escolhe
consulta_cs e deixa dinheiro na mesa; o CRAI escolhe desconto_10.

Como o custo depende do MRR do cliente, a decisao tambem se adapta dentro
do perfil: para um CLT de R$ 2.500/mes, o custo fixo do CS dilui e a
consulta passa a ser a escolha otima. O bandit aprende so o `p`; a camada
de e-Profit faz o resto por cliente.

## 4. Simulacao

Ambiente com verdade oculta (probabilidades reais de aceite por perfil x
oferta, calibradas em benchmarks), 6.000 clientes em risco sorteados
(50% CLT, 30% PJ, 20% freelancer, MRR lognormal por perfil). As 4
estrategias atendem o MESMO fluxo de clientes:

| Estrategia | e-Profit total | Regret acumulado | % otimo (ultimas 1000) |
|------------|---------------|------------------|------------------------|
| Aleatoria | R$ 4.251.465 | R$ 1.679.162 | 20.9% |
| Epsilon-Greedy 0.2 (baseline) | R$ 5.276.724 | R$ 505.808 | 52.3% |
| Thompson (aceite) | R$ 5.386.673 | R$ 387.056 | 59.1% |
| **Thompson (e-Profit)** | **R$ 5.475.702** | **R$ 301.139** | **87.0%** |

- Ganho vs baseline: **+R$ 198.978** (~R$ 33 por intervencao).
- A curva de regret do epsilon-greedy cresce LINEAR para sempre (explora
  20% eternamente); a do Thompson achata (figura `regret_acumulado.png`).
- O Thompson e-Profit tem taxa de aceite MENOR que o Thompson aceite
  (47% vs 50%) e mesmo assim lucra mais — otimizar conversao e otimizar
  lucro sao objetivos diferentes.

Convergencia no ultimo quartil: CLT -> desconto_10, PJ -> pausa_1_mes,
freelancer -> pix_boleto_flash — os tres bracos otimos verdadeiros.

## 5. Integracao ao pipeline (crai/)

- `crai/churn_voluntary/offer_bandit.py` reescrito: Thompson Sampling com
  posteriores persistidos em `crai/models/bandit_state.json` a cada
  resultado — o aprendizado sobrevive a restarts.
- Warm start: os posteriores das 6.000 rodadas simuladas sao exportados
  como estado inicial; sem eles, priors de benchmark (cold start).
- Regra de negocio preservada: risco >= 0.90 escala direto para humano.
- 5o braco adicionado ao agente voluntario: pix_boleto_flash.

## 6. Limitacoes conhecidas

- Ambiente estacionario: as taxas reais de aceite nao mudam com o tempo.
  Em producao, sazonalidade e mudancas de produto pedem desconto temporal
  dos posteriores (sliding window ou decay).
- LTV_retido fixo em 6 meses de MRR para todas as ofertas; um aceite de
  pausa_1_mes provavelmente retem menos LTV que um aceite de desconto.
- O custo da consulta_cs (R$ 250) e uma estimativa; o valor certo muda o
  ponto de corte do MRR.

## 7. Proximos passos

- Desconto temporal dos posteriores para ambientes nao estacionarios.
- LTV retido especifico por oferta (aceite de pausa != aceite de desconto).
- Bandit contextual (LinUCB / Thompson linear): usar MRR, tenure e risco
  como features continuas em vez de discretizar por perfil.

## 8. Artefatos

| Arquivo | Conteudo |
|---------|----------|
| `models/bandit_state.json` | posteriores Beta apos a simulacao |
| `models/meta.json` | parametros da simulacao |
| `reports/metricas.json` | todas as metricas desta pagina |
| `reports/simulacao.csv` | trajetoria por rodada e estrategia |
| `reports/figures/*.png` | figuras para a banca |

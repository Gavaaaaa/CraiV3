# Modulo 4 - Selecao de Ofertas (Thompson Sampling)

Componente do CRAI que decide **qual oferta de retencao** apresentar a um
cliente em risco de churn voluntario. Multi-Armed Bandit bayesiano
(Beta-Bernoulli) que otimiza **e-Profit** — nao taxa de conversao — e
aprende continuamente com cada aceite/recusa.

## Os 5 bracos

| Oferta | Custo |
|--------|-------|
| desconto_10 | 10% x 3 meses de MRR |
| desconto_20 | 20% x 3 meses de MRR |
| pausa_1_mes | 1 mes de MRR |
| consulta_cs | R$ 250 (tempo humano de CS) |
| pix_boleto_flash | R$ 2 (operacional) |

A escolha maximiza `p_amostrado x LTV_retido - custo`, entao o mesmo perfil
pode receber ofertas diferentes conforme o MRR: um CLT de R$ 300 recebe
desconto_10; um CLT de R$ 2.500 recebe consulta_cs (o custo humano passa a
compensar).

## Pipeline

```
src/environment.py     -> ambiente simulado (verdade oculta do bandit)
src/bandit.py          -> ThompsonSamplingBandit + baselines
src/simulate.py        -> 6000 rodadas x 4 estrategias -> bandit_state.json
src/evaluate.py        -> reports/metricas.json
src/offer_selector.py  -> interface para o agente (Modulo 5)
src/visualizar.py      -> reports/figures/*.png
src/export_to_crai.py  -> exporta posteriores para crai/models/
```

## Como rodar

```bash
pip install -r requirements.txt

python -m src.simulate
python -m src.evaluate
python -m src.visualizar
python -m src.offer_selector   # demo
python -m tests.test_bandit    # testes

python -m src.export_to_crai   # exporta posteriores para crai/models/
```

## Resultados (seed 42, 6000 rodadas)

| Estrategia | e-Profit total | Regret acumulado | % braco otimo (fim) |
|------------|---------------|------------------|---------------------|
| Aleatoria | R$ 4,25M | R$ 1.679k | 21% |
| Epsilon-Greedy 0.2 (baseline anterior) | R$ 5,28M | R$ 506k | 52% |
| Thompson (aceite) | R$ 5,39M | R$ 387k | 59% |
| **Thompson (e-Profit)** | **R$ 5,48M** | **R$ 301k** | **87%** |

Ganho vs baseline anterior: **+R$ 199 mil** em 6000 intervencoes (~R$ 33
por cliente atendido). Convergiu ao braco otimo nos 3 perfis: CLT ->
desconto_10, PJ -> pausa_1_mes, freelancer -> pix_boleto_flash.

O detalhe central: o Thompson que otimiza ACEITE escolhe consulta_cs para
CLT (converte 50%, o maximo), mas PERDE dinheiro vs desconto_10 (44% de
aceite) por causa do custo humano — figura `aceite_vs_eprofit.png`.

## Interface publica

```python
from src.offer_selector import OfferSelector

selector = OfferSelector()
r = selector.choose_offer("CLT", mrr=300.0, risk_score=0.7)
# {"offer": "desconto_10", "p_accept_estimado": 0.459,
#  "custo": 90.0, "eprofit_esperado": 736.2}
selector.record_outcome("CLT", r["offer"], accepted=True)
```

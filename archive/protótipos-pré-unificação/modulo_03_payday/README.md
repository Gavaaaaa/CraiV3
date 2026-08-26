# Modulo 3 - Inferencia de Liquidez (Payday Inference)

Componente do CRAI que responde **quando** o cliente tera saldo para uma
retentativa de cobranca. Combina o padrao INDIVIDUAL do cliente (LSTM sobre
a serie de saldo) com a sazonalidade COLETIVA do perfil (Prophet sobre a
taxa de liquidez agregada), produzindo a janela otima de retry que o
Dunning Engine consome.

## Pipeline

```
data/generate_synthetic.py  -> liquidity_series.csv (600 clientes x 180 dias)
src/train.py                -> models/lstm.pt + meta.json
src/prophet_model.py        -> models/prophet_<perfil>.json (3 perfis)
src/evaluate.py             -> reports/metricas.json + scores.csv
src/payday_inference.py     -> interface para o agente (Modulo 5)
src/visualizar.py           -> reports/figures/*.png
src/export_to_crai.py       -> exporta artefatos para crai/models/
```

## Como rodar

```bash
pip install -r requirements.txt

python -m data.generate_synthetic
python -m src.train
python -m src.prophet_model
python -m src.evaluate
python -m src.visualizar
python -m src.payday_inference   # demo
python -m tests.test_payday      # testes

python -m src.export_to_crai     # exporta artefatos para crai/models/
```

## Resultados (seed 42, 120 clientes de teste nunca vistos)

**Previsao diaria** (havera saldo no dia D+k? k = 1..14):

| Estrategia | ROC-AUC |
|------------|---------|
| Prophet (sazonal) | 0.6816 |
| LSTM (individual) | 0.9755 |
| Ensemble 0.6/0.4  | 0.9695 |

**Janela otima de retry** (dias ate a primeira liquidez real):

| Estrategia | MAE (dias) | Acerto exato | Acerto ±1d | Acerto ±2d |
|------------|-----------|--------------|------------|------------|
| Heuristica (dias fixos, baseline atual) | 5.01 | 13.6% | 28.3% | 38.8% |
| Prophet   | 2.45 | 59.8% | 63.1% | 68.8% |
| LSTM      | 0.64 | 82.5% | 89.0% | 92.0% |
| **Ensemble** | **0.62** | 81.7% | **89.3%** | **92.6%** |

Por perfil, o ensemble reduz o MAE da heuristica de 6.03 -> 0.21 dias (CLT),
3.25 -> 0.91 (PJ) e 4.36 -> 1.47 (freelancer).

Classificacao de perfil por padrao de entradas (ancoras de payday BR):
**84%** de acuracia em clientes nunca vistos.

## Interface publica

```python
from src.payday_inference import PaydayInference

inferencia = PaydayInference()
janela = inferencia.predict_next_window(serie_do_cliente)
# {"timestamp": datetime, "confidence": 0.86, "profile": "CLT",
#  "method": "lstm_prophet", "probs_14d": [...]}
```

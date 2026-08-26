# Modulo 2 - Autoencoder para Deteccao de Anomalias Comportamentais

Componente do CRAI responsavel por identificar clientes SaaS B2B cujo padrao
de uso se desviou do "comportamento saudavel", produzindo um sinal upstream
para o motor de decisao baseado em e-Profit.

## Pipeline

```
data/generate_synthetic.py   -> behavioral_data.csv (5500 linhas)
src/train.py                 -> models/autoencoder.pt + scaler.pkl
src/evaluate.py              -> reports/metricas.json + scores.csv
src/anomaly_detector.py      -> interface para o agente (Modulo 5)
src/visualizar.py            -> reports/figures/*.png
```

## Como rodar

```bash
pip install -r requirements.txt

python -m data.generate_synthetic
python -m src.train
python -m src.evaluate
python -m src.visualizar
python -m src.anomaly_detector   # demo
python -m tests.test_autoencoder # testes

python -m src.export_to_crai     # exporta artefatos para crai/models/
```

## Resultados (seed 42)

| Metrica            | Valor   |
|--------------------|---------|
| ROC-AUC            | 0.9960  |
| Average Precision  | 0.9700  |
| F1 maximo          | 0.9190  |
| Recall @ p95       | 97.4%   |
| Precision @ p95    | 66.1%   |

Separacao das populacoes: o erro medio dos anomalos e ~11.6x maior que o
dos saudaveis - margem confortavel para a etapa de decisao economica.

## Interface publica

```python
from src.anomaly_detector import AnomalyDetector

detector = AnomalyDetector()
score = detector.score(cliente_dict)
e_anomalo = detector.is_anomaly(cliente_dict)
explicacao = detector.explicar(cliente_dict)  # top features que contribuem
```

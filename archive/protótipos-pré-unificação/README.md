# Protótipos pré-unificação

Estas três pastas são **histórico**, não parte ativa do sistema.

Cada uma foi o laboratório de um módulo de ML do CRAI: dataset sintético próprio,
loop de treino, avaliação em conjunto de teste e figuras para o relatório. Elas
cumpriram o papel de validar a abordagem antes de a lógica entrar no pacote
principal.

## O que foi portado, e para onde

| Protótipo | Lógica de treino | Destino no pacote principal |
|-----------|------------------|-----------------------------|
| `modulo_02_autoencoder/` | `src/train.py`, `src/model.py`, `src/evaluate.py`, `data/generate_synthetic.py` | `crai/ml/anomaly_detector.py` (`AnomalyDetector.train()`) + `crai/ml/synthetic_data.py` (`generate_behavioral_dataset()`) |
| `modulo_03_payday/` | `src/train.py`, `src/model.py`, `src/features.py`, `src/prophet_model.py`, `src/evaluate.py`, `data/generate_synthetic.py` | `crai/ml/payday_inference.py` (`PaydayInference.train()`) + `crai/ml/synthetic_data.py` (`generate_liquidity_series()`) |
| `modulo_04_offer_bandit/` | `src/bandit.py`, `src/environment.py`, `src/simulate.py` | `crai/churn_voluntary/offer_bandit.py` (posteriores Beta persistidos em `crai/models/bandit_state.json`) |

O treino dos três modelos de ML do pacote roda hoje por um comando único:

```bash
cd crai
python -m crai.scripts.train_all
```

Os scripts `src/export_to_crai.py` de cada protótipo — que copiavam artefatos
para `crai/models/` — ficaram obsoletos: `train()` já salva direto no diretório
que `load()` lê.

## O que NÃO foi portado (e por que continua aqui)

- **Relatórios e figuras** (`reports/`): as métricas e os gráficos citados no
  README principal e no TCC (curva ROC, precision-recall, distribuição de erros,
  convergência do bandit, regret acumulado) foram gerados aqui. São a evidência
  documental dos números.
- **Scripts de visualização** (`src/visualizar.py`): geram essas figuras.
- **`models/meta.json`**: registro do treino original de cada protótipo, útil
  para comparar com o que `train()` produz hoje no pacote.

## Adaptações feitas na portabilidade

O código não foi copiado cegamente. As principais diferenças:

- **Dataset em memória**: os protótipos liam CSV de `data/`; no pacote os
  geradores devolvem `DataFrame` direto, sem passo intermediário em disco.
- **Threshold do autoencoder**: calibrado no percentil 95 dos saudáveis de
  **validação** (held-out), não do conjunto completo — evita o viés otimista de
  calibrar no mesmo dado que treinou.
- **Séries de liquidez ancoradas em hoje**: o protótipo gerava datas fixas a
  partir de 2026-01-01; o gerador do pacote termina a série no dia corrente,
  para que o prior sazonal do Prophet fique alinhado ao horizonte de predição.
- **Prior sazonal vetorizado na avaliação**: uma chamada `Prophet.predict()` por
  perfil sobre todas as datas, em vez de uma por janela.
- **Convenções do pacote**: assinatura `train(n_samples, test_size, ...) -> dict`
  igual à de `crai/ml/failure_classifier.py`, e logs com prefixo `[ANOMALY]` /
  `[PAYDAY]` no mesmo estilo dos logs de inferência.

"""
crai/scripts/train_all.py — Treina os três modelos do pacote em sequência.

Ordem: classifier (Módulo 1) → anomaly (Módulo 2) → payday (Módulo 3).
Cada um gera seu próprio dataset sintético, treina, salva os artefatos em
crai/models/ e devolve um dicionário de métricas. Ao final, imprime um
resumo comparável dos três e verifica que cada modelo salvo é recarregável
via load() — é essa verificação que garante que o pipeline de inferência
(agente LangGraph) vai encontrar os artefatos no formato esperado.

Uso:
    python -m crai.scripts.train_all
    python -m crai.scripts.train_all --quick        # amostras menores, para smoke test
"""

import argparse
import io
import sys
import time

from crai.ml.anomaly_detector import AnomalyDetector
from crai.ml.failure_classifier import FailureClassifier, MODELS_DIR
from crai.ml.payday_inference import PaydayInference

LARGURA = 70


def _cabecalho(titulo: str):
    print(f"\n{'=' * LARGURA}")
    print(f"  {titulo}")
    print(f"{'=' * LARGURA}")


def treinar_classifier(n_samples: int) -> dict:
    _cabecalho("MODULO 1/3 -- Failure Classifier (XGBoost + Random Forest + SHAP)")
    clf = FailureClassifier()
    metrics = clf.train(n_samples=n_samples)
    metrics["recarregavel"] = FailureClassifier().load()
    return metrics


def treinar_anomaly(n_samples: int) -> dict:
    _cabecalho("MODULO 2/3 -- Anomaly Detector (Autoencoder PyTorch)")
    det = AnomalyDetector()
    metrics = det.train(n_samples=n_samples)
    metrics["recarregavel"] = AnomalyDetector().load()
    return metrics


def treinar_payday(n_samples: int) -> dict:
    _cabecalho("MODULO 3/3 -- Payday Inference (LSTM + Prophet)")
    pay = PaydayInference()
    metrics = pay.train(n_samples=n_samples)
    metrics["recarregavel"] = PaydayInference().load()
    return metrics


def _resumo(classifier: dict, anomaly: dict, payday: dict):
    _cabecalho("RESUMO DO TREINO")

    print("\n  [1] Failure Classifier -- ensemble XGBoost (70%) + Random Forest (30%)")
    print(f"      AUC-ROC              : {classifier['auc']:.4f}")
    print(f"      Acuracia             : {classifier['accuracy']:.4f}")
    print(f"      Recall (recuperado)  : {classifier['recall_recovered']:.4f}")
    print(f"      e-Profit medio       : R$ {classifier['avg_eprofit']:.2f}")
    print(f"      Clientes e-Profit>0  : {classifier['n_positive_eprofit']}/{classifier['n_total_test']}")

    print("\n  [2] Anomaly Detector -- autoencoder treinado so em clientes saudaveis")
    print(f"      ROC-AUC              : {anomaly['roc_auc']:.4f}")
    print(f"      Average Precision    : {anomaly['average_precision']:.4f}")
    print(f"      Precision @ threshold: {anomaly['precision']:.4f}")
    print(f"      Recall @ threshold   : {anomaly['recall']:.4f}")
    print(f"      Threshold (p{int(anomaly['threshold_percentile'])})       : {anomaly['threshold']:.4f}")
    print(f"      Separacao saudavel/anomalo: {anomaly['separation_ratio']:.1f}x")
    print(f"      Epocas treinadas     : {anomaly['epochs_trained']}")

    print("\n  [3] Payday Inference -- LSTM individual + prior sazonal Prophet")
    print(f"      ROC-AUC diario (LSTM/Prophet/ensemble): "
          f"{payday['roc_auc_lstm']:.4f} / {payday['roc_auc_prophet']:.4f} / "
          f"{payday['roc_auc_ensemble']:.4f}")
    print(f"      MAE da janela otima  : {payday['mae_dias_ensemble']:.2f} dia(s) "
          f"(heuristica: {payday['mae_dias_heuristica']:.2f})")
    print(f"      Acerto exato / +-1d  : {payday['hit_exato_ensemble']:.1%} / "
          f"{payday['hit_1d_ensemble']:.1%}")
    print(f"      Epocas treinadas     : {payday['epochs_trained']}")
    print("      Por perfil (MAE heuristica -> ensemble):")
    for perfil, v in payday["por_perfil"].items():
        print(f"        {perfil:11s}: {v['mae_heuristica']:.2f} -> {v['mae_ensemble']:.2f} dias "
              f"({v['n_janelas']} janelas)")

    print(f"\n{'-' * LARGURA}")
    print("  VERIFICACAO DE CARREGAMENTO (load() apos train())")
    print(f"{'-' * LARGURA}")
    for nome, metrics in [("classifier", classifier), ("anomaly", anomaly), ("payday", payday)]:
        status = "OK" if metrics["recarregavel"] else "FALHOU"
        print(f"    {nome:12s}: {status}")


def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Treina os tres modelos do pacote CRAI em sequencia."
    )
    parser.add_argument("--classifier-samples", type=int, default=3000,
                        help="Transacoes sinteticas do Modulo 1 (default: 3000)")
    parser.add_argument("--anomaly-samples", type=int, default=5500,
                        help="Clientes do dataset comportamental do Modulo 2 (default: 5500)")
    parser.add_argument("--payday-customers", type=int, default=600,
                        help="Clientes com serie de liquidez do Modulo 3 (default: 600)")
    parser.add_argument("--quick", action="store_true",
                        help="Amostras reduzidas para smoke test")
    args = parser.parse_args()

    if args.quick:
        args.classifier_samples = 500
        args.anomaly_samples = 800
        args.payday_customers = 60

    print(f"\n{'=' * LARGURA}")
    print("  CRAI -- TREINO COMPLETO DOS MODELOS DE ML")
    print(f"{'=' * LARGURA}")
    print(f"  Artefatos serao salvos em: {MODELS_DIR}/")

    inicio = time.perf_counter()
    classifier = treinar_classifier(args.classifier_samples)
    anomaly = treinar_anomaly(args.anomaly_samples)
    payday = treinar_payday(args.payday_customers)
    duracao = time.perf_counter() - inicio

    _resumo(classifier, anomaly, payday)

    todos_ok = all(m["recarregavel"] for m in (classifier, anomaly, payday))
    print(f"\n{'=' * LARGURA}")
    print(f"  Tempo total: {duracao:.1f}s | Artefatos em: {MODELS_DIR}/")
    print(f"{'=' * LARGURA}\n")

    return 0 if todos_ok else 1


if __name__ == "__main__":
    sys.exit(main())

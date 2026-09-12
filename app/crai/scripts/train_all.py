"""
crai/scripts/train_all.py — Treina os três modelos do pacote em sequência.

Ordem: classifier (Módulo 1) → anomaly (Módulo 2) → payday (Módulo 3).
Cada um gera seu próprio dataset sintético, treina, salva os artefatos em
crai/models/ e devolve um dicionário de métricas. Ao final, imprime um
resumo comparável dos três e verifica que cada modelo salvo é recarregável
via load() — é essa verificação que garante que o pipeline de inferência
(agente LangGraph) vai encontrar os artefatos no formato esperado.

Desde o trabalho de base de dados/treino, aceita também:

    --fonte sintetico|sintetico_calibrado   parâmetros do gerador (default:
                                            sintetico — comportamento antigo)
    --voluntario-samples N                  treina o CANDIDATO do risk_scorer
                                            voluntário (0 desliga); nunca ativa
    --ativar-voluntario                     promove o candidato ao nome que o
                                            scorer carrega (muda o pipeline!)
    --saida-json ARQUIVO                    grava todas as métricas da rodada

Uso:
    python -m crai.scripts.train_all
    python -m crai.scripts.train_all --quick        # amostras menores, para smoke test
    python -m crai.scripts.train_all --fonte sintetico_calibrado --saida-json rodada.json
"""

import argparse
import io
import json
import sys
import time

from crai.ml.anomaly_detector import AnomalyDetector
from crai.ml.calibracao import FONTES_ACEITAS
from crai.ml.failure_classifier import FailureClassifier, MODELS_DIR
from crai.ml.payday_inference import PaydayInference
from crai.ml.voluntary_risk import VoluntaryRiskModel

LARGURA = 70


def _cabecalho(titulo: str):
    print(f"\n{'=' * LARGURA}")
    print(f"  {titulo}")
    print(f"{'=' * LARGURA}")


def treinar_classifier(n_samples: int, fonte: str = "sintetico") -> dict:
    _cabecalho("MODULO 1/3 -- Failure Classifier (XGBoost + Random Forest + SHAP)")
    clf = FailureClassifier()
    metrics = clf.train(n_samples=n_samples, fonte=fonte)
    metrics["recarregavel"] = FailureClassifier().load()
    return metrics


def treinar_anomaly(n_samples: int, fonte: str = "sintetico") -> dict:
    _cabecalho("MODULO 2/3 -- Anomaly Detector (Autoencoder PyTorch)")
    det = AnomalyDetector()
    metrics = det.train(n_samples=n_samples, fonte=fonte)
    metrics["recarregavel"] = AnomalyDetector().load()
    return metrics


def treinar_payday(n_samples: int, fonte: str = "sintetico") -> dict:
    _cabecalho("MODULO 3/3 -- Payday Inference (LSTM + Prophet)")
    pay = PaydayInference()
    metrics = pay.train(n_samples=n_samples, fonte=fonte)
    metrics["recarregavel"] = PaydayInference().load()
    return metrics


def treinar_voluntario(n_samples: int, fonte: str = "sintetico", ativar: bool = False) -> dict:
    _cabecalho("EXTRA -- risk_scorer voluntario (candidato, GradientBoosting)")
    vol = VoluntaryRiskModel()
    metrics = vol.train(n_samples=n_samples, fonte=fonte)
    metrics["recarregavel"] = VoluntaryRiskModel().load()
    metrics["ativado"] = VoluntaryRiskModel.ativar() if ativar else False
    return metrics


def _resumo(classifier: dict, anomaly: dict, payday: dict, voluntario: dict = None):
    _cabecalho("RESUMO DO TREINO")
    print(f"\n  Fonte: {classifier['fonte_usada']} | amostras: classifier "
          f"{classifier['n_amostras']} / anomaly {anomaly['n_amostras']} / "
          f"payday {payday['n_amostras']}"
          + (f" / voluntario {voluntario['n_amostras']}" if voluntario else ""))
    for nome, m in (("classifier", classifier), ("anomaly", anomaly), ("payday", payday)):
        p = m["proveniencia"]["por_status"]
        print(f"  Proveniencia {nome:10s}: ancoradas {p['ancorada']} | proxy fraco "
              f"{p['proxy_fraco']} | sinteticas puras {p['sintetica_sem_doador']}")

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

    if voluntario:
        print("\n  [extra] risk_scorer voluntario -- candidato (NAO ativo"
              + (", ATIVADO nesta rodada" if voluntario.get("ativado") else "") + ")")
        print(f"      AUC vs rotulo        : {voluntario['auc_vs_rotulo']:.4f} "
              f"(teto das regras: {voluntario['auc_regra_vs_rotulo']:.4f})")
        print(f"      Corr. com as regras  : {voluntario['corr_vs_regra']:.4f} | "
              f"MAE vs regra {voluntario['mae_vs_regra']:.4f}")

    versoes = classifier.get("versoes", {})
    print(f"\n  Versoes gravadas no meta.json: sklearn {versoes.get('scikit-learn')} | "
          f"xgboost {versoes.get('xgboost')} | torch {versoes.get('torch')} | "
          f"prophet {versoes.get('prophet')}")

    print(f"\n{'-' * LARGURA}")
    print("  VERIFICACAO DE CARREGAMENTO (load() apos train())")
    print(f"{'-' * LARGURA}")
    modulos = [("classifier", classifier), ("anomaly", anomaly), ("payday", payday)]
    if voluntario:
        modulos.append(("voluntario", voluntario))
    for nome, metrics in modulos:
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
    parser.add_argument("--fonte", choices=FONTES_ACEITAS, default="sintetico",
                        help="Parametros do gerador sintetico (default: sintetico)")
    parser.add_argument("--voluntario-samples", type=int, default=2000,
                        help="Eventos sinteticos do candidato do risk_scorer voluntario "
                             "(default: 2000; 0 desliga)")
    parser.add_argument("--ativar-voluntario", action="store_true",
                        help="Promove o candidato a voluntary_risk.joblib (o scorer passa "
                             "a usar o modelo — muda o pipeline voluntario)")
    parser.add_argument("--saida-json", type=str, default=None,
                        help="Grava as metricas de todos os modulos neste arquivo")
    args = parser.parse_args()

    if args.quick:
        args.classifier_samples = 500
        args.anomaly_samples = 800
        args.payday_customers = 60
        if args.voluntario_samples:
            args.voluntario_samples = 300

    print(f"\n{'=' * LARGURA}")
    print("  CRAI -- TREINO COMPLETO DOS MODELOS DE ML")
    print(f"{'=' * LARGURA}")
    print(f"  Artefatos serao salvos em: {MODELS_DIR}/")
    print(f"  Fonte dos parametros: {args.fonte}")

    inicio = time.perf_counter()
    classifier = treinar_classifier(args.classifier_samples, args.fonte)
    anomaly = treinar_anomaly(args.anomaly_samples, args.fonte)
    payday = treinar_payday(args.payday_customers, args.fonte)
    voluntario = (treinar_voluntario(args.voluntario_samples, args.fonte, args.ativar_voluntario)
                  if args.voluntario_samples else None)
    duracao = time.perf_counter() - inicio

    _resumo(classifier, anomaly, payday, voluntario)

    modulos = [classifier, anomaly, payday] + ([voluntario] if voluntario else [])
    todos_ok = all(m["recarregavel"] for m in modulos)
    print(f"\n{'=' * LARGURA}")
    print(f"  Tempo total: {duracao:.1f}s | Artefatos em: {MODELS_DIR}/")
    print(f"{'=' * LARGURA}\n")

    if args.saida_json:
        with open(args.saida_json, "w", encoding="utf-8") as f:
            json.dump({"fonte": args.fonte, "duracao_s": round(duracao, 1),
                       "classifier": classifier, "anomaly": anomaly, "payday": payday,
                       "voluntario": voluntario},
                      f, ensure_ascii=False, indent=2, default=str)
        print(f"  Metricas gravadas em {args.saida_json}\n")

    return 0 if todos_ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""
crai/scripts/train_all.py — Treina os três modelos do pacote em sequência.

Ordem: classifier (Módulo 1) → anomaly (Módulo 2) → payday (Módulo 3).
Cada um treina no seu dataset sintético, salva os artefatos em
crai/models/ e devolve um dicionário de métricas. Ao final, imprime um
resumo comparável dos três e verifica que cada modelo salvo é recarregável
via load() — é essa verificação que garante que o pipeline de inferência
(agente LangGraph) vai encontrar os artefatos no formato esperado.

De onde vem o dado, e para onde vai o modelo (desde 20/09/2026, Bloco B):

    --base v2 (default quando `data/v2/` existe)
        Base v2 = população compartilhada (`crai.scripts.gerar_bases_v2`):
        os quatro datasets carregam `customer_id` e vêm da mesma população.
        Sempre lida de disco — o sha256 de cada parquet é conferido contra o
        MANIFESTO.json antes do treino, e um arquivo que não confere é erro
        alto (dado de treino que não bate com o manifesto não é auditável).
        Os Módulos 1 e 4 recebem `groups=customer_id` e fazem holdout POR
        CLIENTE (`crai/ml/split.py`). Os artefatos vão para `models/v2/`;
        `models/` (14/09/2026) fica intocado e nada em produção carrega de
        `models/v2/` — a troca é decisão de quem ler o relatório.

    --base v1
        Os quatro geradores independentes de `synthetic_data`. Se `data/v1/`
        existe (gerada por `python -m crai.scripts.gerar_bases`), lê de lá com
        a mesma conferência de sha256 e de fonte; senão gera em memória, como
        sempre fez. Quem clona e roda sem gerar antes continua funcionando.
        `--quick` e `--sem-dados` sempre geram em memória. Artefatos em `models/`.

    Com a base em disco, os tamanhos são os da base; um `--classifier-samples`
    etc. passado explicitamente que difira do manifesto é erro (regenere a
    base ou use `--sem-dados`). `--dados PASTA` aponta para outra pasta.

Desde o trabalho de base de dados/treino, aceita também:

    --fonte sintetico|sintetico_calibrado   parâmetros do gerador (default:
                                            sintetico — comportamento antigo)
    --voluntario-samples N                  treina o CANDIDATO do risk_scorer
                                            voluntário (0 desliga); nunca ativa
    --ativar-voluntario                     promove o candidato ao nome que o
                                            scorer carrega (muda o pipeline!)
    --saida-json ARQUIVO                    grava todas as métricas da rodada
    --base v1|v2                            qual base (default: v2 se existir)
    --dados PASTA                           pasta da base persistida
    --sem-dados                             (v1) ignora a base em disco
    --percentil-limiar P                    percentil do limiar do autoencoder
                                            (default: p95 na v1; na v2 o treino
                                            APLICA o critério — maior percentil
                                            com recall >= RECALL_MINIMO_LIMIAR_V2
                                            — e o flag explícito o desliga)

Uso:
    python -m crai.scripts.train_all
    python -m crai.scripts.train_all --quick        # amostras menores, para smoke test
    python -m crai.scripts.train_all --base v1 --fonte sintetico_calibrado --saida-json rodada.json
    python -m crai.scripts.train_all --base v2 --saida-json rodada_v2.json
"""

import argparse
import io
import json
import sys
import time
from pathlib import Path

from crai.ml import anomaly_detector as anomaly_module
from crai.ml import failure_classifier as classifier_module
from crai.ml import payday_inference as payday_module
from crai.ml import voluntary_risk as voluntary_module
from crai.ml.anomaly_detector import AnomalyDetector
from crai.ml.calibracao import FONTES_ACEITAS
from crai.ml.failure_classifier import FailureClassifier, MODELS_DIR
from crai.ml.payday_inference import PaydayInference
from crai.ml.voluntary_risk import VoluntaryRiskModel
from crai.scripts.gerar_bases import (
    ARQUIVOS,
    ARQUIVOS_V2,
    DADOS_V1_DIR,
    DADOS_V2_DIR,
    entrada_do_manifesto,
    fonte_do_manifesto,
    ler_bases,
    ler_manifesto,
)

FONTE_PADRAO = "sintetico"

# `models/` de verdade, capturado no import: `apontar_models_dir` rebinda o
# MODELS_DIR dos quatro módulos e este é o ponto fixo de onde derivar.
MODELS_RAIZ = Path(MODELS_DIR)
SUBPASTA_MODELOS_V2 = "v2"
CURVA_LIMIAR_ARQUIVO = "curva_limiar_anomalia.json"

# Defaults de tamanho quando o dado é gerado em memória. Ficam aqui (e não no
# argparse) para o script saber se o usuário PASSOU um tamanho ou não: com a
# base em disco, o tamanho vem do manifesto e um valor explícito diferente é erro.
TAMANHOS_PADRAO = {
    "classifier_samples": 3000,
    "anomaly_samples": 5500,
    "payday_customers": 600,
    "voluntario_samples": 2000,
}

LARGURA = 70


def _cabecalho(titulo: str):
    print(f"\n{'=' * LARGURA}")
    print(f"  {titulo}")
    print(f"{'=' * LARGURA}")


def apontar_models_dir(subpasta: str = None) -> Path:
    """Faz os quatro módulos gravarem (e recarregarem) de `models/` ou `models/<subpasta>/`.

    É o mesmo mecanismo que a suíte usa (`monkeypatch.setattr(modulo,
    "MODELS_DIR", ...)`): cada módulo lê o próprio global na hora de salvar e
    de carregar, então rebindar os quatro basta. `calibracao.json` continua
    sendo lido de `models/` — é a calibração, não um artefato treinado.
    """
    destino = MODELS_RAIZ / subpasta if subpasta else MODELS_RAIZ
    for modulo in (classifier_module, anomaly_module, payday_module, voluntary_module):
        modulo.MODELS_DIR = destino
    return destino


def treinar_classifier(n_samples: int, fonte: str = "sintetico", dados=None,
                       groups=None) -> dict:
    _cabecalho("MODULO 1/3 -- Failure Classifier (XGBoost + Random Forest + SHAP)")
    clf = FailureClassifier()
    metrics = clf.train(n_samples=n_samples, fonte=fonte, dados=dados, groups=groups)
    metrics["recarregavel"] = FailureClassifier().load()
    return metrics


def treinar_anomaly(n_samples: int, fonte: str = "sintetico", dados=None,
                    threshold_percentile: float = None, recall_minimo: float = None) -> dict:
    _cabecalho("MODULO 2/3 -- Anomaly Detector (Autoencoder PyTorch)")
    det = AnomalyDetector()
    extra = {} if threshold_percentile is None else {"threshold_percentile": threshold_percentile}
    if recall_minimo is not None:
        extra["recall_minimo"] = recall_minimo
    metrics = det.train(n_samples=n_samples, fonte=fonte, dados=dados, **extra)
    metrics["recarregavel"] = AnomalyDetector().load()
    return metrics


def treinar_payday(n_samples: int, fonte: str = "sintetico", dados=None) -> dict:
    _cabecalho("MODULO 3/3 -- Payday Inference (LSTM + Prophet)")
    pay = PaydayInference()
    metrics = pay.train(n_samples=n_samples, fonte=fonte, dados=dados)
    metrics["recarregavel"] = PaydayInference().load()
    return metrics


def treinar_voluntario(n_samples: int, fonte: str = "sintetico", ativar: bool = False,
                       dados=None, groups=None) -> dict:
    _cabecalho("EXTRA -- risk_scorer voluntario (candidato, GradientBoosting)")
    vol = VoluntaryRiskModel()
    metrics = vol.train(n_samples=n_samples, fonte=fonte, dados=dados, groups=groups)
    metrics["recarregavel"] = VoluntaryRiskModel().load()
    metrics["ativado"] = VoluntaryRiskModel.ativar() if ativar else False
    return metrics


# ══════════════════════════════════════════════════════════════════════════
# DE ONDE VEM O DADO
# ══════════════════════════════════════════════════════════════════════════

def _conferir_tamanhos(args, tamanhos_disco: dict, pasta: Path):
    """Tamanhos vêm da base em disco. Um valor explícito diferente é erro, não
    aviso: treinar em 40.000 linhas achando que foram 3.000 é o tipo de engano
    que vira número errado num relatório."""
    for nome, em_disco in tamanhos_disco.items():
        pedido = getattr(args, nome)
        if pedido is not None and pedido != em_disco and not (
                nome == "voluntario_samples" and pedido == 0):
            raise SystemExit(
                f"[TRAIN_ALL] --{nome.replace('_', '-')} {pedido} difere da base em "
                f"{pasta} ({em_disco} no manifesto). Regenere a base com esse tamanho "
                "ou passe --sem-dados para gerar em memoria.")
        if pedido is None:
            setattr(args, nome, em_disco)


def _resolver_fonte(args, manifesto: dict, pasta: Path, como_regenerar: str):
    """Sem `--fonte`, a fonte é a da base em disco; com `--fonte` diferente
    da base, é erro — treinar uma base calibrada dizendo `sintetico` gravaria
    proveniência falsa no meta.json."""
    da_base = fonte_do_manifesto(manifesto)
    if args.fonte is None:
        args.fonte = da_base or FONTE_PADRAO
    elif da_base is not None and da_base != args.fonte:
        raise SystemExit(
            f"[TRAIN_ALL] A base em {pasta} foi gerada com fonte={da_base!r}, e o "
            f"treino pediu --fonte {args.fonte!r}. {como_regenerar}")


def _sha256_do_manifesto(manifesto: dict, arquivos: dict) -> dict:
    return {arquivo: entrada_do_manifesto(manifesto, nome, arquivo)["sha256"]
            for nome, arquivo in arquivos.items()}


def _tamanhos_das_bases(bases: dict) -> dict:
    return {
        "classifier_samples": int(len(bases["classificador"])),
        "anomaly_samples": int(len(bases["comportamental"])),
        "payday_customers": int(bases["liquidez"]["customer_id"].nunique()),
        "voluntario_samples": int(len(bases["voluntario"])),
    }


def resolver_base(args) -> str:
    """`args.base` explícito, senão v2 se `data/v2/` está completa, senão v1."""
    if getattr(args, "base", None):
        return args.base
    pasta = Path(args.dados) if getattr(args, "dados", None) else DADOS_V2_DIR
    return "v2" if ler_manifesto(pasta, ARQUIVOS_V2) else "v1"


def resolver_bases(args) -> tuple:
    """Decide de onde vem o dado. Devolve `(bases, descricao)`.

    `bases` é `{nome: DataFrame}` (chaves de `gerar_bases.ARQUIVOS`, mais
    `populacao` na v2) quando uma base persistida é usada, ou None para gerar
    em memória (só v1). Preenche os tamanhos em `args` (da base ou dos
    defaults), `args.base` e `args.bases_info`, que vai para o `--saida-json`.
    """
    args.base = resolver_base(args)
    if args.base == "v2":
        return _resolver_v2(args)
    return _resolver_v1(args)


def _resolver_v1(args) -> tuple:
    pasta = Path(args.dados) if args.dados else DADOS_V1_DIR
    manifesto = None if (args.sem_dados or args.quick) else ler_manifesto(pasta, ARQUIVOS)

    if manifesto is None:
        if args.fonte is None:
            args.fonte = FONTE_PADRAO
        for nome, padrao in TAMANHOS_PADRAO.items():
            if getattr(args, nome) is None:
                setattr(args, nome, padrao)
        motivo = ("--sem-dados" if args.sem_dados else "--quick" if args.quick
                  else f"{pasta} nao existe")
        args.bases_info = {"base": "v1", "origem": "gerador_em_memoria", "motivo": motivo}
        return None, f"v1, geradas em memoria ({motivo})"

    _resolver_fonte(args, manifesto, pasta,
                    "Regenere a base com a fonte certa (python -m crai.scripts.gerar_bases "
                    f"--fonte {args.fonte}) ou passe --sem-dados para gerar em memoria.")

    bases, _ = ler_bases(pasta, conferir_sha256=True, arquivos=ARQUIVOS)
    _conferir_tamanhos(args, _tamanhos_das_bases(bases), pasta)
    args.bases_info = {
        "base": "v1",
        "origem": "arquivo",
        "pasta": str(pasta.resolve()),
        "semente": manifesto["semente"],
        "fonte": fonte_do_manifesto(manifesto),
        "end_date_liquidez": manifesto.get("end_date_liquidez"),
        "sha256": _sha256_do_manifesto(manifesto, ARQUIVOS),
    }
    return bases, (f"v1, lidas de {pasta} (seed {manifesto['semente']}, fonte "
                   f"{fonte_do_manifesto(manifesto)}, sha256 conferidos)")


def _resolver_v2(args) -> tuple:
    pasta = Path(args.dados) if args.dados else DADOS_V2_DIR
    if args.sem_dados or args.quick:
        raise SystemExit(
            "[TRAIN_ALL] --sem-dados e --quick geram em memoria, e a base v2 so existe "
            "em disco (crai.scripts.gerar_bases_v2). Use-os com --base v1.")
    manifesto = ler_manifesto(pasta, ARQUIVOS_V2)
    if manifesto is None:
        raise SystemExit(
            f"[TRAIN_ALL] Base v2 nao encontrada em {pasta} (MANIFESTO.json + "
            f"{', '.join(ARQUIVOS_V2.values())}). Gere com "
            "`python -m crai.scripts.gerar_bases_v2 --seed 42 --out data/v2/` "
            "ou treine a v1 com --base v1.")
    _resolver_fonte(args, manifesto, pasta, "Passe o --fonte da base, ou nenhum.")

    # ler_bases falha alto, com o nome do arquivo, se um sha256 nao confere.
    bases, _ = ler_bases(pasta, conferir_sha256=True, arquivos=ARQUIVOS_V2)
    for nome in ARQUIVOS:
        if "customer_id" not in bases[nome].columns:
            raise SystemExit(f"[TRAIN_ALL] {pasta / ARQUIVOS[nome]} sem customer_id: "
                             "nao e uma base v2 (populacao compartilhada).")
    _conferir_tamanhos(args, _tamanhos_das_bases(bases), pasta)
    args.bases_info = {
        "base": "v2",
        "origem": "arquivo",
        "pasta": str(pasta.resolve()),
        "semente": manifesto.get("semente"),
        "fonte": fonte_do_manifesto(manifesto),
        "end_date": manifesto.get("end_date"),
        "sha256": _sha256_do_manifesto(manifesto, ARQUIVOS_V2),
        "clientes_populacao": int(len(bases["populacao"])),
    }
    return bases, (f"v2, lidas de {pasta} (seed {manifesto.get('semente')}, "
                   f"{len(bases['populacao'])} clientes na populacao, sha256 conferidos)")


# ══════════════════════════════════════════════════════════════════════════
# RESUMO
# ══════════════════════════════════════════════════════════════════════════

def _resumo(classifier: dict, anomaly: dict, payday: dict, voluntario: dict = None,
            bases_desc: str = "", models_dir: Path = None):
    _cabecalho("RESUMO DO TREINO")
    if bases_desc:
        print(f"\n  Bases: {bases_desc}")
    print(f"\n  Fonte: {classifier['fonte_usada']} | amostras: classifier "
          f"{classifier['n_amostras']} / anomaly {anomaly['n_amostras']} / "
          f"payday {payday['n_amostras']}"
          + (f" / voluntario {voluntario['n_amostras']}" if voluntario else ""))
    for nome, m in (("classifier", classifier), ("anomaly", anomaly), ("payday", payday)):
        p = m["proveniencia"]["por_status"]
        print(f"  Proveniencia {nome:10s}: ancoradas {p['ancorada']} | proxy fraco "
              f"{p['proxy_fraco']} | sinteticas puras {p['sintetica_sem_doador']}")

    print("\n  [1] Failure Classifier -- ensemble XGBoost (70%) + Random Forest (30%)")
    print(f"      Split                : {classifier.get('split', 'por_linha')}"
          + (f" ({classifier['n_clientes_treino']} clientes treino / "
             f"{classifier['n_clientes_teste']} teste)" if "n_clientes_teste" in classifier else ""))
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
        print(f"      Split                : {voluntario.get('split', 'por_linha')}")
        print(f"      AUC vs rotulo        : {voluntario['auc_vs_rotulo']:.4f} "
              f"(teto das regras: {voluntario['auc_regra_vs_rotulo']:.4f})")
        print(f"      Corr. com as regras  : {voluntario['corr_vs_regra']:.4f} | "
              f"MAE vs regra {voluntario['mae_vs_regra']:.4f}")

    versoes = classifier.get("versoes", {})
    print(f"\n  Versoes gravadas no meta.json: sklearn {versoes.get('scikit-learn')} | "
          f"xgboost {versoes.get('xgboost')} | torch {versoes.get('torch')} | "
          f"prophet {versoes.get('prophet')}")

    print(f"\n{'-' * LARGURA}")
    print(f"  VERIFICACAO DE CARREGAMENTO (load() apos train(), em {models_dir or MODELS_RAIZ})")
    print(f"{'-' * LARGURA}")
    modulos = [("classifier", classifier), ("anomaly", anomaly), ("payday", payday)]
    if voluntario:
        modulos.append(("voluntario", voluntario))
    for nome, metrics in modulos:
        status = "OK" if metrics["recarregavel"] else "FALHOU"
        print(f"    {nome:12s}: {status}")


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Treina os tres modelos do pacote CRAI em sequencia."
    )
    parser.add_argument("--classifier-samples", type=int, default=None,
                        help="Transacoes sinteticas do Modulo 1 (default: 3000, ou o da base em disco)")
    parser.add_argument("--anomaly-samples", type=int, default=None,
                        help="Clientes do dataset comportamental do Modulo 2 (default: 5500, ou o da base em disco)")
    parser.add_argument("--payday-customers", type=int, default=None,
                        help="Clientes com serie de liquidez do Modulo 3 (default: 600, ou o da base em disco)")
    parser.add_argument("--quick", action="store_true",
                        help="Amostras reduzidas para smoke test (so --base v1; gera em memoria)")
    parser.add_argument("--fonte", choices=FONTES_ACEITAS, default=None,
                        help="Parametros do gerador sintetico (default: o da base em disco; "
                             "sintetico quando gera em memoria)")
    parser.add_argument("--voluntario-samples", type=int, default=None,
                        help="Eventos sinteticos do candidato do risk_scorer voluntario "
                             "(default: 2000, ou o da base em disco; 0 desliga)")
    parser.add_argument("--base", choices=("v1", "v2"), default=None,
                        help="v1: geradores independentes (models/); v2: populacao "
                             "compartilhada (models/v2/). Default: v2 se data/v2/ existe")
    parser.add_argument("--dados", type=str, default=None,
                        help=f"Pasta da base persistida (default: {DADOS_V1_DIR} ou {DADOS_V2_DIR})")
    parser.add_argument("--sem-dados", action="store_true",
                        help="(v1) Ignora a base em disco e gera os datasets em memoria")
    parser.add_argument("--percentil-limiar", type=float, default=None,
                        help="Percentil do erro dos saudaveis que vira limiar do autoencoder "
                             "(default: THRESHOLD_PERCENTIL_V1 na v1; na v2 o treino aplica o "
                             "criterio 'maior percentil com recall >= RECALL_MINIMO_LIMIAR_V2' "
                             "a curva deste treino). Passar um valor DESLIGA o criterio na v2 "
                             "e o meta fica com criterio_limiar=null")
    parser.add_argument("--ativar-voluntario", action="store_true",
                        help="Promove o candidato a voluntary_risk.joblib (o scorer passa "
                             "a usar o modelo — muda o pipeline voluntario)")
    parser.add_argument("--saida-json", type=str, default=None,
                        help="Grava as metricas de todos os modulos neste arquivo")
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "buffer") and \
            (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    args = _parser().parse_args(argv)

    if args.quick:
        args.classifier_samples = 500
        args.anomaly_samples = 800
        args.payday_customers = 60
        if args.voluntario_samples != 0:
            args.voluntario_samples = 300

    bases, bases_desc = resolver_bases(args)
    b = bases or {}
    v2 = args.base == "v2"
    models_dir = apontar_models_dir(SUBPASTA_MODELOS_V2 if v2 else None)
    grupos = (lambda nome: b[nome]["customer_id"].to_numpy()) if v2 else (lambda nome: None)
    percentil = args.percentil_limiar
    # v2: o percentil e a SAIDA do criterio (escolher_percentil) aplicado a
    # curva DESTE treino — nao ha constante para copiar. Um --percentil-limiar
    # explicito desliga o criterio, e o meta registra isso (criterio_limiar=null).
    recall_minimo = anomaly_module.RECALL_MINIMO_LIMIAR_V2 if (v2 and percentil is None) else None

    print(f"\n{'=' * LARGURA}")
    print("  CRAI -- TREINO COMPLETO DOS MODELOS DE ML")
    print(f"{'=' * LARGURA}")
    print(f"  Base: {args.base} | artefatos serao salvos em: {models_dir}/")
    print(f"  Fonte dos parametros: {args.fonte}")
    print(f"  Bases: {bases_desc}")

    inicio = time.perf_counter()
    classifier = treinar_classifier(args.classifier_samples, args.fonte,
                                    b.get("classificador"), grupos("classificador") if v2 else None)
    anomaly = treinar_anomaly(args.anomaly_samples, args.fonte, b.get("comportamental"),
                              percentil, recall_minimo)
    if v2:
        with open(models_dir / CURVA_LIMIAR_ARQUIVO, "w", encoding="utf-8") as f:
            escolhido = anomaly_module.AnomalyDetector.escolher_percentil(
                anomaly["curva_limiar"], anomaly_module.RECALL_MINIMO_LIMIAR_V2)
            json.dump({"base": "v2", "percentil_usado": anomaly["threshold_percentile"],
                       "criterio": (f"maior percentil da curva com recall >= "
                                    f"{anomaly_module.RECALL_MINIMO_LIMIAR_V2} "
                                    "(AnomalyDetector.escolher_percentil), APLICADO PELO "
                                    "TREINO a curva deste artefato; o percentil e derivado, "
                                    "nao uma constante — outra maquina pode dar outro "
                                    "percentil pelo mesmo criterio"),
                       "criterio_limiar": anomaly.get("criterio_limiar"),
                       "percentil_pelo_criterio_nesta_curva": escolhido["percentil"],
                       "recall_no_percentil_usado": next(
                           (c["recall"] for c in anomaly["curva_limiar"]
                            if c["percentil"] == anomaly["threshold_percentile"]), None),
                       "curva": anomaly["curva_limiar"]},
                      f, ensure_ascii=False, indent=2)
        print(f"[ANOMALY] Curva de limiar gravada em {models_dir / CURVA_LIMIAR_ARQUIVO}")
    payday = treinar_payday(args.payday_customers, args.fonte, b.get("liquidez"))
    voluntario = (treinar_voluntario(args.voluntario_samples, args.fonte, args.ativar_voluntario,
                                     b.get("voluntario"), grupos("voluntario") if v2 else None)
                  if args.voluntario_samples else None)
    duracao = time.perf_counter() - inicio

    _resumo(classifier, anomaly, payday, voluntario, bases_desc, models_dir)

    modulos = [classifier, anomaly, payday] + ([voluntario] if voluntario else [])
    todos_ok = all(m["recarregavel"] for m in modulos)
    print(f"\n{'=' * LARGURA}")
    print(f"  Tempo total: {duracao:.1f}s | Artefatos em: {models_dir}/")
    print(f"{'=' * LARGURA}\n")

    if args.saida_json:
        with open(args.saida_json, "w", encoding="utf-8") as f:
            json.dump({"fonte": args.fonte, "base": args.base, "models_dir": str(models_dir),
                       "duracao_s": round(duracao, 1), "bases": args.bases_info,
                       "classifier": classifier, "anomaly": anomaly, "payday": payday,
                       "voluntario": voluntario},
                      f, ensure_ascii=False, indent=2, default=str)
        print(f"  Metricas gravadas em {args.saida_json}\n")

    return 0 if todos_ok else 1


if __name__ == "__main__":
    sys.exit(main())

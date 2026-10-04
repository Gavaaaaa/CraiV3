"""
crai/scripts/promover_voluntario_v3.py — Promove (ou reverte) o modelo voluntário v3.

    python -m crai.scripts.promover_voluntario_v3 --promover
    python -m crai.scripts.promover_voluntario_v3 --reverter

Rodado de dentro de `app/`. Promoção do voluntário v3, Bloco 3 (0.7 do Bloco 0).

--promover
    Copia `models/v3/voluntary_risk_v3.joblib` para `models/voluntary_risk.joblib`
    e grava `models/voluntary_risk_meta.json`: o meta do v3 mais
    `contrato: "v3"`, `contrato_de_producao: true`, `promovido_em`, `origem`
    (pasta e sha256 do joblib e do meta de origem) e `referencia_score_quantis`
    — os 101 quantis (p0..p100) do score do modelo no holdout por cliente da
    base v3, que `batch_scoring` usa para posicionar quando a lista é pequena
    ou o SDK ainda não tem a base do tenant. Confere antes: o meta de origem
    declara FEATURES_DE_RISCO_V3 na versão atual, e a base v3 é a do treino
    (sha256 do parquet = `proveniencia.base_sha256` do meta). Depois de gravar,
    confere que `risk_scorer.contrato_do_meta` aceita o meta novo como "v3".
    RECUSA se `models/voluntary_risk.joblib` já existir: não sobrescreve em
    silêncio (reverta antes).

--reverter
    Move `models/voluntary_risk.joblib` e `models/voluntary_risk_meta.json` para
    `models/historico/voluntary_risk_<AAAAMMDD-HHMMSS>/`. Sem esses arquivos,
    `risk_scorer.carregar_modelo()` devolve False e a régua volta a decidir.

Nos dois casos o efeito vale a partir do próximo reinício do processo: o
carregamento do modelo é cacheado (`risk_scorer.carregar_modelo`).
O candidato v2 (`voluntary_risk_candidato.*`) nunca é tocado. Com o v3
promovido, `VoluntaryRiskModel.ativar()` / `train_all --ativar-voluntario`
recusam (trava D8); voltar atrás é só por `--reverter`.

`--origem`, `--destino` e `--base` existem para os testes (tmp_path).
"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

NOME_PRODUCAO = "voluntary_risk"
NOME_V3 = "voluntary_risk_v3"
N_QUANTIS = 101


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def quantis_do_holdout(modelo, base: Path) -> list:
    """Os N_QUANTIS quantis do score do modelo no holdout por cliente da base v3
    (o mesmo split do treino: `split_por_cliente`, seed 42, 20%)."""
    import numpy as np
    import pandas as pd

    from ..ml import voluntario_v3 as V3
    from ..ml.split import split_por_cliente
    from . import treinar_voluntario_v3 as T

    df = pd.read_parquet(base / "voluntario_v3.parquet")
    _, idx_te = split_por_cliente(df["customer_id"].to_numpy(), test_size=T.TEST_SIZE,
                                  seed=T.SEED)
    scores = modelo.predict_proba(df[V3.FEATURES_DE_RISCO_V3].iloc[idx_te]
                                  .to_numpy(dtype=float))[:, 1]
    return [round(float(q), 6) for q in np.quantile(scores, np.linspace(0, 1, N_QUANTIS))]


def promover(origem: Path, destino: Path, base: Path) -> int:
    import joblib

    from ..churn_voluntary import risk_scorer as rs
    from ..ml.voluntario_v3 import FEATURES_DE_RISCO_V3, FEATURES_DE_RISCO_V3_VERSAO

    joblib_origem = origem / f"{NOME_V3}.joblib"
    meta_origem = origem / f"{NOME_V3}_meta.json"
    joblib_destino = destino / f"{NOME_PRODUCAO}.joblib"
    meta_destino = destino / f"{NOME_PRODUCAO}_meta.json"

    if joblib_destino.exists() or meta_destino.exists():
        print(f"[PROMOVER] RECUSADO: ja existe modelo de producao em {destino} "
              f"({joblib_destino.name} / {meta_destino.name}). Nada foi alterado. Para "
              f"trocar, rode antes `python -m crai.scripts.promover_voluntario_v3 --reverter`.")
        return 1
    for arq in (joblib_origem, meta_origem):
        if not arq.exists():
            print(f"[PROMOVER] RECUSADO: {arq} nao existe. Nada foi alterado.")
            return 1

    meta = json.loads(meta_origem.read_text(encoding="utf-8"))
    if (meta.get("features") != FEATURES_DE_RISCO_V3
            or meta.get("features_versao") != FEATURES_DE_RISCO_V3_VERSAO):
        print(f"[PROMOVER] RECUSADO: o meta de {origem} nao declara FEATURES_DE_RISCO_V3 "
              f"versao {FEATURES_DE_RISCO_V3_VERSAO}. Nada foi alterado.")
        return 1
    parquet = base / "voluntario_v3.parquet"
    sha_base = _sha256(parquet) if parquet.exists() else None
    if sha_base != (meta.get("proveniencia") or {}).get("base_sha256"):
        print(f"[PROMOVER] RECUSADO: a base em {base} nao e a do treino (sha256 "
              f"{(sha_base or 'ausente')[:16]} contra "
              f"{str((meta.get('proveniencia') or {}).get('base_sha256'))[:16]} no meta). "
              f"Nada foi alterado.")
        return 1

    modelo = joblib.load(joblib_origem)
    quantis = quantis_do_holdout(modelo, base)
    meta_producao = {
        **meta,
        "contrato": rs.CONTRATO_V3,
        "contrato_de_producao": True,
        "promovido_em": datetime.now().isoformat(timespec="seconds"),
        "origem": {"pasta": str(origem.resolve()), "joblib_sha256": _sha256(joblib_origem),
                   "meta_sha256": _sha256(meta_origem)},
        "referencia_score_quantis": quantis,
        "referencia_score_origem": (f"{N_QUANTIS} quantis (p0..p100) do score do modelo no "
                                    "holdout por cliente da base v3 (split_por_cliente, seed "
                                    "42, 20%), com todas as colunas"),
    }
    if rs.contrato_do_meta(meta_producao) != rs.CONTRATO_V3:
        print("[PROMOVER] RECUSADO: o meta montado nao passa em risk_scorer.contrato_do_meta. "
              "Nada foi alterado.")
        return 1

    destino.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(joblib_origem, joblib_destino)
    meta_destino.write_text(json.dumps(meta_producao, indent=2, ensure_ascii=False, default=str),
                            encoding="utf-8")
    print(f"[PROMOVER] v3 PROMOVIDO: {joblib_destino} (sha256 {_sha256(joblib_destino)[:16]}) "
          f"e {meta_destino.name} (contrato v3, {N_QUANTIS} quantis de referencia, "
          f"mediana {quantis[N_QUANTIS // 2]}).")
    print("[PROMOVER] O candidato v2 nao foi tocado. A ativacao do candidato fica travada "
          "enquanto o v3 estiver em producao.")
    print("[PROMOVER] Reinicie o servico: o carregamento do modelo e cacheado por processo.")
    return 0


def reverter(destino: Path) -> int:
    joblib_destino = destino / f"{NOME_PRODUCAO}.joblib"
    meta_destino = destino / f"{NOME_PRODUCAO}_meta.json"
    presentes = [a for a in (joblib_destino, meta_destino) if a.exists()]
    if not presentes:
        print(f"[REVERTER] Nada a reverter: nao ha {joblib_destino.name} nem "
              f"{meta_destino.name} em {destino}. A regua ja decide.")
        return 1
    pasta = destino / "historico" / f"{NOME_PRODUCAO}_{datetime.now():%Y%m%d-%H%M%S}"
    pasta.mkdir(parents=True, exist_ok=False)
    for arq in presentes:
        shutil.move(str(arq), str(pasta / arq.name))
    print(f"[REVERTER] Modelo de producao movido para {pasta}. Sem ele, a regua volta a "
          f"decidir.")
    print("[REVERTER] Reinicie o servico: o carregamento do modelo e cacheado por processo.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Promove ou reverte o modelo voluntario v3.")
    acao = ap.add_mutually_exclusive_group(required=True)
    acao.add_argument("--promover", action="store_true")
    acao.add_argument("--reverter", action="store_true")
    ap.add_argument("--origem", default="models/v3/")
    ap.add_argument("--destino", default="models/")
    ap.add_argument("--base", default="data/v3/")
    args = ap.parse_args(argv)
    if args.promover:
        return promover(Path(args.origem), Path(args.destino), Path(args.base))
    return reverter(Path(args.destino))


if __name__ == "__main__":
    sys.exit(main())

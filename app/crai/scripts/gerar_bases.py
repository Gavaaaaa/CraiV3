"""
crai/scripts/gerar_bases.py — Persiste em disco as quatro bases sintéticas de treino (v1).

Até 19/09/2026 o `train_all` gerava os quatro datasets em memória e os
descartava ao fim do treino: não havia o que auditar, e a base do Módulo 3
ainda dependia do dia em que o treino rodou (`end_date=date.today()`). Este
script gera as mesmas bases, com os mesmos geradores e a mesma semente, e as
grava em parquet ao lado de um MANIFESTO.json — de modo que rodar duas vezes
com a mesma semente produz arquivos byte a byte idênticos (sha256 iguais).

    data/v1/
        classificador.parquet    Módulo 1  — uma linha por cobrança falha
        comportamental.parquet   Módulo 2  — uma linha por cliente
        liquidez.parquet         Módulo 3  — uma linha por cliente-dia
        voluntario.parquet       candidato voluntário — uma linha por evento
        MANIFESTO.json           semente, fonte, end_date, versões das
                                 bibliotecas e, por arquivo: gerador,
                                 parâmetros, linhas, colunas, lista de
                                 colunas, dtypes, bytes e sha256

O `train_all --base v1` lê `data/v1/` se existir (conferindo o sha256 de cada
arquivo contra o manifesto) e gera em memória se não existir — quem roda sem
gerar antes continua funcionando. `data/` está no `.gitignore`: a base é
regenerável por semente, não é fonte do repositório.

v1 × v2 (desde 20/09/2026). Esta é a base v1: quatro geradores independentes,
sem cliente em comum. A base v2 (população compartilhada, `data/v2/`) vem de
`crai.scripts.gerar_bases_v2` (`populacao.py` + `visoes.py`) e é o default do
`train_all` quando existe. Os dois usam o MESMO leitor (`ler_bases` /
`ler_manifesto`, abaixo) — muda só o mapa de arquivos (`ARQUIVOS` × `ARQUIVOS_V2`).

Nada aqui usa relógio: nenhum `date.today()`, nenhum `random`/`np.random`
global. A data-fim da série de liquidez vem de `end_date_por_semente(seed)`.

Uso (a partir de app/):

    python -m crai.scripts.gerar_bases --seed 42 --out data/v1/

    # a rodada de 14/09/2026 (a que produziu os binários em models/):
    python -m crai.scripts.gerar_bases --seed 42 --fonte sintetico_calibrado \\
        --classifier-samples 40000 --anomaly-samples 25000 \\
        --payday-customers 3000 --voluntario-samples 30000
"""

import argparse
import hashlib
import io
import json
import platform
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

from crai.ml import calibracao
from crai.ml.calibracao import FONTES_ACEITAS
from crai.ml.synthetic_data import (
    SEED,
    end_date_por_semente,
    generate_behavioral_dataset,
    generate_dataset,
    generate_liquidity_series,
    generate_voluntary_dataset,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent      # app/
DADOS_V1_DIR = BASE_DIR / "data" / "v1"
DADOS_V2_DIR = BASE_DIR / "data" / "v2"
DADOS_DIR_PADRAO = DADOS_V1_DIR

# Nome lógico → arquivo. A ordem é a do treino (Módulo 1 → 2 → 3 → voluntário).
ARQUIVOS = {
    "classificador": "classificador.parquet",
    "comportamental": "comportamental.parquet",
    "liquidez": "liquidez.parquet",
    "voluntario": "voluntario.parquet",
}
# Base v2: as mesmas quatro visões + a população que as liga (uma linha por
# cliente; carrega os latentes, que NÃO entram como feature em modelo nenhum).
ARQUIVOS_V2 = {"populacao": "populacao.parquet", **ARQUIVOS}
MANIFESTO = "MANIFESTO.json"
VERSAO_FORMATO = 1

# Tamanhos padrão — os mesmos defaults do `train_all`.
PADRAO = {
    "classifier_samples": 3000,
    "anomaly_samples": 5500,
    "anomaly_rate": 0.09,
    "payday_customers": 600,
    "payday_days": 180,
    "voluntario_samples": 2000,
}


# ══════════════════════════════════════════════════════════════════════════
# GERAÇÃO
# ══════════════════════════════════════════════════════════════════════════

def gerar_bases(seed: int = SEED, fonte: str = "sintetico",
                classifier_samples: int = PADRAO["classifier_samples"],
                anomaly_samples: int = PADRAO["anomaly_samples"],
                anomaly_rate: float = PADRAO["anomaly_rate"],
                payday_customers: int = PADRAO["payday_customers"],
                payday_days: int = PADRAO["payday_days"],
                voluntario_samples: int = PADRAO["voluntario_samples"]) -> dict:
    """Gera as quatro bases em memória. Devolve `{nome: (DataFrame, parametros)}`.

    Cada gerador recebe a MESMA semente, exatamente como o `train_all` sempre
    fez (seed=42 em cada `train()`), para que a base persistida seja a mesma
    que o treino em memória via. `parametros` é o que foi passado ao gerador,
    e vai para o manifesto.
    """
    calibracao.validar_fonte(fonte)
    end_date = end_date_por_semente(seed)

    bases = {}
    p = {"n_samples": classifier_samples, "seed": seed, "fonte": fonte}
    bases["classificador"] = (generate_dataset(**p), {"gerador": "generate_dataset", **p})

    p = {"n_samples": anomaly_samples, "anomaly_rate": anomaly_rate, "seed": seed, "fonte": fonte}
    bases["comportamental"] = (generate_behavioral_dataset(**p),
                               {"gerador": "generate_behavioral_dataset", **p})

    p = {"n_customers": payday_customers, "n_days": payday_days, "seed": seed,
         "end_date": str(end_date.date()), "fonte": fonte}
    bases["liquidez"] = (generate_liquidity_series(**p),
                         {"gerador": "generate_liquidity_series", **p})

    p = {"n_samples": voluntario_samples, "seed": seed, "fonte": fonte}
    bases["voluntario"] = (generate_voluntary_dataset(**p),
                           {"gerador": "generate_voluntary_dataset", **p})
    return bases


# ══════════════════════════════════════════════════════════════════════════
# ESCRITA / LEITURA
# ══════════════════════════════════════════════════════════════════════════

def sha256_arquivo(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def versoes_para_manifesto() -> dict:
    """numpy, pandas, scikit-learn, xgboost, torch (o que a tarefa pede), mais
    python e pyarrow, que decidem os bytes do parquet."""
    v = calibracao.versoes_bibliotecas()
    try:
        import pyarrow
        pyarrow_v = pyarrow.__version__
    except ImportError:                                   # pragma: no cover
        pyarrow_v = None
    return {
        "python": platform.python_version(),
        "numpy": v.get("numpy"),
        "pandas": v.get("pandas"),
        "scikit-learn": v.get("scikit-learn"),
        "xgboost": v.get("xgboost"),
        "torch": v.get("torch"),
        "pyarrow": pyarrow_v,
    }


def escrever_bases(bases: dict, out: Path, seed: int, fonte: str,
                   arquivos: dict = ARQUIVOS) -> dict:
    """Grava os parquet (mapa `arquivos`) + MANIFESTO.json em `out`. Devolve o manifesto.

    O manifesto não carrega relógio (nenhum timestamp): com a mesma semente e
    as mesmas versões, ele também sai byte a byte igual.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    entradas = {}
    for nome, (df, parametros) in bases.items():
        arquivo = arquivos[nome]
        caminho = out / arquivo
        df.to_parquet(caminho, index=False, engine="pyarrow")
        entradas[arquivo] = {
            "base": nome,
            "semente": seed,
            "gerador": parametros["gerador"],
            "parametros": {k: v for k, v in parametros.items() if k != "gerador"},
            "linhas": int(len(df)),
            "colunas": int(df.shape[1]),
            "lista_colunas": list(map(str, df.columns)),
            "dtypes": {str(c): str(t) for c, t in df.dtypes.items()},
            "bytes": caminho.stat().st_size,
            "sha256": sha256_arquivo(caminho),
        }

    manifesto = {
        "versao_formato": VERSAO_FORMATO,
        "gerado_por": "crai.scripts.gerar_bases",
        "semente": seed,
        "fonte": fonte,
        "end_date_liquidez": str(end_date_por_semente(seed).date()),
        "versoes": versoes_para_manifesto(),
        "arquivos": entradas,
    }
    with open(out / MANIFESTO, "w", encoding="utf-8") as f:
        json.dump(manifesto, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return manifesto


def entrada_do_manifesto(manifesto: dict, nome: str, arquivo: str) -> dict:
    """A entrada de um arquivo no manifesto, nos dois formatos que existem.

    v1 (`gerar_bases`): `arquivos` é indexado pelo NOME DO ARQUIVO
    (`classificador.parquet`) e a lista de colunas é `lista_colunas`.
    v2 (`gerar_bases_v2`, pacote de 20/09/2026): indexado pelo nome LÓGICO
    (`classificador`), com `arquivo`, `lista_de_colunas`, `clientes_distintos`.
    Devolve um dicionário normalizado com `linhas`, `colunas`, `lista_colunas`,
    `sha256`, `bytes` (e o resto da entrada original).
    """
    entradas = manifesto.get("arquivos", {})
    bruto = entradas.get(arquivo) or entradas.get(nome)
    if bruto is None:
        raise KeyError(f"{MANIFESTO} sem entrada para {arquivo!r} (nem {nome!r})")
    out = dict(bruto)
    out.setdefault("arquivo", arquivo)
    if "lista_colunas" not in out and "lista_de_colunas" in out:
        out["lista_colunas"] = out["lista_de_colunas"]
    for chave in ("linhas", "sha256", "lista_colunas"):
        if chave not in out:
            raise KeyError(f"{MANIFESTO}: entrada de {arquivo!r} sem {chave!r}")
    return out


def fonte_do_manifesto(manifesto: dict) -> Optional[str]:
    """`fonte` (v1) ou `fonte_de_parametros` (v2)."""
    return manifesto.get("fonte", manifesto.get("fonte_de_parametros"))


def ler_manifesto(pasta: Path, arquivos: dict = ARQUIVOS) -> Optional[dict]:
    """Manifesto de `pasta`, ou None se a pasta não tem uma base completa."""
    pasta = Path(pasta)
    if not (pasta / MANIFESTO).exists():
        return None
    if not all((pasta / a).exists() for a in arquivos.values()):
        return None
    with open(pasta / MANIFESTO, encoding="utf-8") as f:
        return json.load(f)


def ler_bases(pasta: Path, conferir_sha256: bool = True, arquivos: dict = ARQUIVOS) -> tuple:
    """Lê os parquet de `pasta` (mapa `arquivos`). Devolve `({nome: DataFrame}, manifesto)`.

    Com `conferir_sha256`, cada arquivo é conferido contra o manifesto antes de
    ser lido: uma base editada à mão (ou gerada por outra versão do script)
    reprova aqui, com o nome do arquivo, em vez de virar um modelo diferente
    em silêncio.
    """
    pasta = Path(pasta)
    manifesto = ler_manifesto(pasta, arquivos)
    if manifesto is None:
        raise FileNotFoundError(
            f"{pasta} não tem uma base completa ({MANIFESTO} + "
            f"{', '.join(arquivos.values())}). Gere com "
            "`python -m crai.scripts.gerar_bases` (v1) ou "
            "`python -m crai.scripts.gerar_bases_v2` (v2).")

    bases = {}
    for nome, arquivo in arquivos.items():
        caminho = pasta / arquivo
        declarado = entrada_do_manifesto(manifesto, nome, arquivo)
        if conferir_sha256:
            medido = sha256_arquivo(caminho)
            if medido != declarado["sha256"]:
                raise ValueError(
                    f"{caminho}: sha256 {medido[:12]}… difere do manifesto "
                    f"({declarado['sha256'][:12]}…). A base foi alterada depois de "
                    "gerada — regenere com `python -m crai.scripts.gerar_bases`.")
        df = pd.read_parquet(caminho, engine="pyarrow")
        if len(df) != declarado["linhas"] or list(df.columns) != declarado["lista_colunas"]:
            raise ValueError(f"{caminho}: linhas/colunas diferem do manifesto.")
        bases[nome] = df
    return bases, manifesto


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gera e persiste as quatro bases sintéticas de treino (parquet + manifesto).")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"Semente de todos os geradores (default: {SEED})")
    parser.add_argument("--out", type=str, default=None,
                        help=f"Pasta de saída (default: {DADOS_DIR_PADRAO})")
    parser.add_argument("--fonte", choices=FONTES_ACEITAS, default="sintetico",
                        help="Parâmetros do gerador (default: sintetico)")
    parser.add_argument("--classifier-samples", type=int, default=PADRAO["classifier_samples"],
                        help="Cobranças do Módulo 1 (default: %(default)s)")
    parser.add_argument("--anomaly-samples", type=int, default=PADRAO["anomaly_samples"],
                        help="Clientes do Módulo 2 (default: %(default)s)")
    parser.add_argument("--anomaly-rate", type=float, default=PADRAO["anomaly_rate"],
                        help="Fração de anômalos no Módulo 2 (default: %(default)s)")
    parser.add_argument("--payday-customers", type=int, default=PADRAO["payday_customers"],
                        help="Clientes com série de liquidez do Módulo 3 (default: %(default)s)")
    parser.add_argument("--payday-days", type=int, default=PADRAO["payday_days"],
                        help="Dias por série do Módulo 3 (default: %(default)s)")
    parser.add_argument("--voluntario-samples", type=int, default=PADRAO["voluntario_samples"],
                        help="Eventos do candidato voluntário (default: %(default)s)")
    return parser


def main(argv=None) -> int:
    # Console do Windows em cp1252 derruba o print dos nomes; sob pytest (ou
    # qualquer stdout já em UTF-8) não há o que reembrulhar.
    if hasattr(sys.stdout, "buffer") and             (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    args = _parser().parse_args(argv)
    out = Path(args.out) if args.out else DADOS_DIR_PADRAO

    print(f"Gerando bases (seed={args.seed}, fonte={args.fonte}, "
          f"end_date liquidez={end_date_por_semente(args.seed).date()}) ...")
    bases = gerar_bases(
        seed=args.seed, fonte=args.fonte,
        classifier_samples=args.classifier_samples,
        anomaly_samples=args.anomaly_samples, anomaly_rate=args.anomaly_rate,
        payday_customers=args.payday_customers, payday_days=args.payday_days,
        voluntario_samples=args.voluntario_samples,
    )
    manifesto = escrever_bases(bases, out, args.seed, args.fonte)

    print(f"\nBases gravadas em {out.resolve()}:")
    print(f"  {'arquivo':24s} {'linhas':>9s} {'colunas':>7s} {'bytes':>11s}  sha256")
    for arquivo, m in manifesto["arquivos"].items():
        print(f"  {arquivo:24s} {m['linhas']:9d} {m['colunas']:7d} {m['bytes']:11d}  {m['sha256']}")
    print(f"  {MANIFESTO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

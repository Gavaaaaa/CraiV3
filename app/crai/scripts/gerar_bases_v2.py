"""
crai/scripts/gerar_bases_v2.py — Gera a base v2 (população compartilhada).

    python -m crai.scripts.gerar_bases_v2 --seed 42 --clientes 120000 --out data/v2/

Escreve, em `--out`:

    populacao.parquet        120.000 linhas  — uma por cliente
    classificador.parquet    120.000 linhas  — uma por cobrança falhada
    comportamental.parquet   120.000 linhas  — uma por cliente
    liquidez.parquet       1.800.000 linhas  — uma por cliente-dia (subamostra)
    voluntario.parquet       120.000 linhas  — uma por evento de SDK
    MANIFESTO.json

O manifesto traz, por arquivo: semente, linhas, colunas, lista de colunas,
sha256 e as versões de biblioteca; e, no topo, as verificações de integridade
da base (interseção de `customer_id`, razão invoice/MRR, consistência de MRR
entre tabelas).

Determinismo: tudo depende de `--seed` e de `END_DATE`, que é fixa. Rodar duas
vezes com a mesma semente produz os mesmos sha256. Não há `date.today()` em
lugar nenhum deste caminho.

Dois hashes por arquivo (Bloco I, 22/09/2026):

    sha256          do ARQUIVO parquet. Depende dos bytes — e os bytes dependem
                    de numpy/pandas/pyarrow (dtype default de inteiro, unidade
                    do datetime, metadados do pandas dentro do parquet). A
                    regeneração da base v2 em pandas 2.2.3 produz as mesmas
                    tabelas linha a linha com OUTROS sha256.
    hash_canonico   do CONTEÚDO: sha256 do CSV canônico da tabela — colunas na
                    ordem do DataFrame, float em `%.6f`, datas em ISO-8601 com
                    microssegundos, inteiros como inteiros, `\n` como fim de
                    linha, sem índice (`hash_canonico`). Não muda com a
                    largura do inteiro (int32/int64), com a unidade do datetime
                    (ns/us) nem com a versão do pandas. É a resposta a "essa
                    base é aquela base?", em qualquer ambiente.

Antes de gravar, `_fixar_dtypes` normaliza as colunas inteiras para `int64` e
as de data para `datetime64[us]`, para que o próprio sha256 do arquivo varie
menos entre ambientes.

AS BASES GERADAS ANTES DE 22/09/2026 NÃO TÊM `hash_canonico` no manifesto —
inclusive a base de produção em `app/data/v2/` (gerada em 20/09/2026, com o
sha256 declarado em `docs/base-v2/MANIFESTO.json`). Ela NÃO foi regenerada
para ganhar o campo, de propósito: regenerar trocaria os sha256 que a cadeia
de auditoria com o repositório de evidência declara. O campo vale a partir da
próxima geração; `hash_canonico_de_pasta` calcula-o para uma base já gravada.
"""

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ..ml import visoes as V
from ..ml.populacao import gerar_populacao, resumo


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


HASH_CANONICO_METODO = (
    "sha256 do CSV canonico: colunas na ordem do DataFrame, cabecalho, sem indice, "
    "float com '%.6f', datetime como ISO-8601 'YYYY-MM-DDTHH:MM:SS.ffffff', inteiro "
    "como inteiro, booleano como True/False, fim de linha '\\n', UTF-8; blocos de "
    "200.000 linhas (crai.scripts.gerar_bases_v2.hash_canonico)"
)
_BLOCO_CANONICO = 200_000


def _fixar_dtypes(df: pd.DataFrame) -> pd.DataFrame:
    """`int64` nas colunas inteiras, `datetime64[us]` nas de data. Não toca em
    float, texto ou booleano. Devolve uma cópia."""
    out = df.copy()
    for col in out.columns:
        dt = out[col].dtype
        if pd.api.types.is_bool_dtype(dt):
            continue
        if pd.api.types.is_integer_dtype(dt):
            out[col] = out[col].astype("int64")
        elif pd.api.types.is_datetime64_any_dtype(dt):
            out[col] = out[col].astype("datetime64[us]")
    return out


def _canonico(df: pd.DataFrame) -> pd.DataFrame:
    """A tabela com todo valor já no formato textual canônico (só o que o CSV
    imprimiria de forma diferente conforme o dtype: datas)."""
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col].dtype):
            out[col] = out[col].dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    return out


def hash_canonico(df: pd.DataFrame) -> str:
    """sha256 do CONTEÚDO da tabela, independente de dtype e de versão de pandas.

    Ver `HASH_CANONICO_METODO`. Duas tabelas com os mesmos valores dão o mesmo
    hash mesmo que uma tenha `int32` onde a outra tem `int64`, ou
    `datetime64[ns]` onde a outra tem `datetime64[us]`; um float diferente na
    sétima casa decimal NÃO muda o hash (arredondamento `%.6f`), um diferente
    na sexta muda.
    """
    h = hashlib.sha256()
    for inicio in range(0, max(len(df), 1), _BLOCO_CANONICO):
        bloco = _canonico(df.iloc[inicio:inicio + _BLOCO_CANONICO])
        texto = bloco.to_csv(index=False, header=(inicio == 0), float_format="%.6f",
                             lineterminator="\n")
        h.update(texto.encode("utf-8"))
    return h.hexdigest()


def hash_canonico_de_pasta(pasta: Path) -> dict:
    """`{nome: hash_canonico}` de uma base já gravada (para bases anteriores a
    22/09/2026, cujo manifesto não tem o campo)."""
    pasta = Path(pasta)
    return {p.stem: hash_canonico(pd.read_parquet(p)) for p in sorted(pasta.glob("*.parquet"))}


def _versoes() -> dict:
    v = {"python": platform.python_version(), "numpy": np.__version__,
         "pandas": pd.__version__}
    for nome, modulo in [("scikit-learn", "sklearn"), ("xgboost", "xgboost"),
                         ("torch", "torch"), ("prophet", "prophet"),
                         ("pyarrow", "pyarrow")]:
        try:
            v[nome] = __import__(modulo).__version__
        except Exception:
            v[nome] = None
    return v


def _verificacoes(pop, cob, comp, liq, vol) -> dict:
    """As checagens que a auditoria de 15/09 reprovou na base v1."""
    ids = [set(cob.customer_id), set(comp.customer_id),
           set(liq.customer_id), set(vol.customer_id)]
    mrr = pop.set_index("customer_id")["mrr"]
    fator = cob["invoice_amount"].to_numpy() / mrr.loc[cob["customer_id"]].to_numpy()

    return {
        "clientes_na_populacao": int(len(pop)),
        "todos_os_ids_existem_na_populacao": bool(
            set.union(*ids) <= set(pop.customer_id)
        ),
        "intersecao_das_quatro_tabelas": int(len(set.intersection(*ids))),
        "intersecao_classificador_comportamental": int(len(ids[0] & ids[1])),
        "intersecao_comportamental_liquidez": int(len(ids[1] & ids[2])),
        "mrr_identico_em_todas_as_tabelas": bool(
            (comp.set_index("customer_id")["mrr_brl"]
             .eq(mrr.loc[comp.customer_id].to_numpy()).all())
            and (vol["mrr"].to_numpy()
                 == mrr.loc[vol.customer_id].to_numpy()).all()
        ),
        "mediana_mrr": float(round(mrr.median(), 2)),
        "mediana_invoice_amount": float(round(cob["invoice_amount"].median(), 2)),
        "razao_invoice_sobre_mrr_mediana": float(
            round(cob["invoice_amount"].median() / mrr.median(), 4)
        ),
        "fator_ciclo_min": float(round(fator.min(), 4)),
        "fator_ciclo_max": float(round(fator.max(), 4)),
        "fator_ciclo_dentro_da_faixa": bool(
            (fator >= V.FAIXA_FATOR_CICLO[0] - 1e-6).all()
            and (fator <= V.FAIXA_FATOR_CICLO[1] + 1e-6).all()
        ),
        "cobrancas_por_cliente_media": float(
            round(len(cob) / cob.customer_id.nunique(), 3)
        ),
        "eventos_por_cliente_media": float(
            round(len(vol) / vol.customer_id.nunique(), 3)
        ),
        "failure_count_90d_e_contado": True,
        "card_brand_presente": "card_brand" in cob.columns,
        "codigos_de_cartao_presentes": sorted(
            set(cob["gateway_error_code"].unique())
            & {"expired_card", "card_declined", "do_not_honor"}
        ),
        "nulos_por_tabela": {
            "populacao": int(pop.isna().sum().sum()),
            "classificador": int(cob.isna().sum().sum()),
            "comportamental": int(comp.isna().sum().sum()),
            "liquidez": int(liq.isna().sum().sum()),
            "voluntario": int(vol.isna().sum().sum()),
        },
    }


def gerar(seed: int, clientes: int, cobrancas: int, eventos: int,
          clientes_liquidez: int, dias: int, fonte: str) -> dict:
    pop = gerar_populacao(clientes, seed)
    cob = V.visao_classificador(pop, n_cobrancas=cobrancas, seed=seed, fonte=fonte)
    comp = V.visao_comportamental(pop, cob, seed=seed, fonte=fonte)
    liq = V.visao_liquidez(pop, n_customers=clientes_liquidez, n_days=dias,
                           seed=seed, fonte=fonte)
    vol = V.visao_voluntario(pop, comp, n_eventos=eventos, seed=seed, fonte=fonte)
    return {"populacao": pop, "classificador": cob, "comportamental": comp,
            "liquidez": liq, "voluntario": vol}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gera a base v2 da CRAI.")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--clientes", type=int, default=120_000)
    ap.add_argument("--cobrancas", type=int, default=120_000)
    ap.add_argument("--eventos", type=int, default=120_000)
    ap.add_argument("--clientes-liquidez", type=int, default=10_000)
    ap.add_argument("--dias", type=int, default=V.JANELA_DIAS)
    ap.add_argument("--fonte", default="sintetico_calibrado",
                    choices=["sintetico", "sintetico_calibrado"])
    ap.add_argument("--out", default="data/v2/")
    ap.add_argument("--csv", action="store_true",
                    help="também escreve CSV (grande; o parquet é o formato de entrega)")
    args = ap.parse_args(argv)

    destino = Path(args.out)
    destino.mkdir(parents=True, exist_ok=True)

    tabelas = gerar(args.seed, args.clientes, args.cobrancas, args.eventos,
                    args.clientes_liquidez, args.dias, args.fonte)

    arquivos = {}
    for nome, df in tabelas.items():
        df = _fixar_dtypes(df)
        caminho = destino / f"{nome}.parquet"
        df.to_parquet(caminho, index=False)
        if args.csv:
            df.to_csv(destino / f"{nome}.csv", index=False)
        arquivos[nome] = {
            "arquivo": caminho.name,
            "linhas": int(len(df)),
            "colunas": int(df.shape[1]),
            "lista_de_colunas": list(df.columns),
            "dtypes": {str(c): str(t) for c, t in df.dtypes.items()},
            "bytes": int(caminho.stat().st_size),
            "sha256": _sha256(caminho),
            "hash_canonico": hash_canonico(df),
            "clientes_distintos": (int(df["customer_id"].nunique())
                                   if "customer_id" in df.columns else None),
        }

    manifesto = {
        "base": "CRAI v2 — população compartilhada",
        "gerado_por": "crai.scripts.gerar_bases_v2",
        "semente": args.seed,
        "end_date": V.END_DATE,
        "janela_dias": args.dias,
        "fonte_de_parametros": args.fonte,
        "populacao": resumo(tabelas["populacao"]),
        "verificacoes": _verificacoes(
            tabelas["populacao"], tabelas["classificador"],
            tabelas["comportamental"], tabelas["liquidez"], tabelas["voluntario"],
        ),
        "arquivos": arquivos,
        "hash_canonico_metodo": HASH_CANONICO_METODO,
        "versoes": _versoes(),
    }
    (destino / "MANIFESTO.json").write_text(
        json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    for nome, meta in arquivos.items():
        print(f"{nome:16s} {meta['linhas']:>9,} linhas × {meta['colunas']:>2} col  "
              f"{meta['bytes']/1e6:>7.2f} MB  sha256 {meta['sha256'][:16]}  "
              f"canonico {meta['hash_canonico'][:16]}")
    print(f"\nMANIFESTO.json em {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

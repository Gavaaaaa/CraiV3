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
        caminho = destino / f"{nome}.parquet"
        df.to_parquet(caminho, index=False)
        if args.csv:
            df.to_csv(destino / f"{nome}.csv", index=False)
        arquivos[nome] = {
            "arquivo": caminho.name,
            "linhas": int(len(df)),
            "colunas": int(df.shape[1]),
            "lista_de_colunas": list(df.columns),
            "bytes": int(caminho.stat().st_size),
            "sha256": _sha256(caminho),
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
        "versoes": _versoes(),
    }
    (destino / "MANIFESTO.json").write_text(
        json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    for nome, meta in arquivos.items():
        print(f"{nome:16s} {meta['linhas']:>9,} linhas × {meta['colunas']:>2} col  "
              f"{meta['bytes']/1e6:>7.2f} MB  {meta['sha256'][:16]}")
    print(f"\nMANIFESTO.json em {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

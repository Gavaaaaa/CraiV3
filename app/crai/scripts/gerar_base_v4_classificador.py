"""
crai/scripts/gerar_base_v4_classificador.py — Gera a base v4 do classificador de falha.

    python -m crai.scripts.gerar_base_v4_classificador --seed 42 --out data/v4/ --v2 data/v2/

Lê `populacao.parquet` e `classificador.parquet` da v2, refaz o rótulo `recovered` pelo
mecanismo de `crai.ml.classificador_v4` e grava em `--out`:

    classificador_v4.parquet   as mesmas cobranças e features da v2, o rótulo novo e as
                               colunas `oculto_*` (que o treino recusa)
    MANIFESTO_v4.json          parâmetros do mecanismo, verificações, sha256 e hash canônico

A v2 é só LIDA: o script confere os cinco arquivos contra o `MANIFESTO.json` dela antes e
depois, e recusa `--out` igual à pasta da v2. É experimento: nada aqui é lido pelo
serviço.

`--amostra-clientes N` gera só os N primeiros clientes (por `customer_id`), para os testes.
O rótulo de um cliente não depende de quem mais está na tabela, então a amostra tem
exatamente as linhas que esses clientes têm na base inteira (o caixa usa um calendário
fixo, não as datas da amostra).
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from ..ml import classificador_v4 as V4
from .gerar_base_v3_voluntario import conferir_v2 as _conferir_v2_do_v3
from .gerar_bases import fonte_do_manifesto
from .gerar_bases_v2 import (HASH_CANONICO_METODO, _fixar_dtypes, _sha256,
                             _versoes, hash_canonico)

NOME_TABELA = "classificador_v4"
MANIFESTO = "MANIFESTO_v4.json"


def conferir_v2(pasta: Path) -> dict:
    """`{nome: sha256}` dos cinco arquivos da v2, conferidos contra o manifesto dela."""
    try:
        return _conferir_v2_do_v3(pasta)
    except SystemExit as e:
        raise SystemExit(str(e).replace("[BASE-V3]", "[BASE-V4]").replace("base v3", "base v4"))


def gerar(pasta_v2: Path, seed: int, amostra_clientes: int = None) -> tuple:
    """`(base v4, verificações, fonte)`."""
    manifesto_v2 = json.loads((pasta_v2 / "MANIFESTO.json").read_text(encoding="utf-8"))
    fonte = fonte_do_manifesto(manifesto_v2) or "sintetico_calibrado"
    pop = pd.read_parquet(pasta_v2 / "populacao.parquet")
    cobrancas = pd.read_parquet(pasta_v2 / "classificador.parquet")
    if amostra_clientes:
        escolhidos = sorted(cobrancas["customer_id"].unique())[:int(amostra_clientes)]
        cobrancas = cobrancas[cobrancas["customer_id"].isin(escolhidos)].reset_index(drop=True)
    df = V4.gerar_classificador_v4(cobrancas, pop, seed=seed, fonte=fonte)
    return df, V4.verificacoes(df, cobrancas), fonte


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gera a base v4 do classificador de falha.")
    ap.add_argument("--seed", type=int, default=V4.SEED)
    ap.add_argument("--out", default="data/v4/")
    ap.add_argument("--v2", default="data/v2/")
    ap.add_argument("--amostra-clientes", type=int, default=None,
                    help="so os N primeiros clientes (para os testes)")
    args = ap.parse_args(argv)

    pasta_v2 = Path(args.v2)
    destino = Path(args.out)
    if destino.resolve() == pasta_v2.resolve():
        raise SystemExit("[BASE-V4] --out aponta para a pasta da v2; a v2 nao e regravada.")

    sha_antes = conferir_v2(pasta_v2)
    df, verif, fonte = gerar(pasta_v2, args.seed, args.amostra_clientes)

    destino.mkdir(parents=True, exist_ok=True)
    df = _fixar_dtypes(df)
    caminho = destino / f"{NOME_TABELA}.parquet"
    df.to_parquet(caminho, index=False)
    sha_depois = conferir_v2(pasta_v2)

    manifesto = {
        "base": "CRAI v4 - classificador de falha com rotulo por mecanismo",
        "gerado_por": "crai.scripts.gerar_base_v4_classificador",
        "experimento": True,
        "semente": args.seed,
        "amostra_clientes": args.amostra_clientes,
        "fonte_de_parametros": fonte,
        "fluxos": {
            "caixa": f"SeedSequence({args.seed}, spawn_key={V4.FLUXO_CAIXA} + (seed_por_cliente,))",
            "cobrancas": f"SeedSequence({args.seed}, spawn_key={V4.FLUXO_COBRANCAS} + (seed_por_cliente,))",
        },
        "mecanismo": {
            "janela_bacen_dias": V4.JANELA_BACEN_DIAS,
            "max_retentativas": V4.MAX_RETENTATIVAS,
            "dias_de_retentativa": list(V4.DIAS_DE_RETENTATIVA),
            "retentativas_por_degrau": "clip(4 - attempt_count, 0, 3)",
            "teto_do_saldo_na_falha": V4.TETO_DO_SALDO_NA_FALHA,
            "p_tecnico_ok": V4.P_TECNICO_OK,
            "p_generico_ok": V4.P_GENERICO_OK,
            "p_acao_limite": {"base": V4.P_ACAO_LIMITE[0], "coef_satisfacao": V4.P_ACAO_LIMITE[1]},
            "p_reautoriza": {"base": V4.P_REAUTORIZA[0], "coef_satisfacao": V4.P_REAUTORIZA[1]},
            "p_paga_boleto": {"base": V4.P_PAGA_BOLETO[0],
                              "coef_saude_financeira": V4.P_PAGA_BOLETO[1]},
            "p_excecao": V4.P_EXCECAO,
            "caixa": "synthetic_data._liquidity_series_customer, parametros `liquidity` da fonte",
            "calendario_do_caixa": {"inicio": V4.CAIXA_INICIO, "fim": V4.CAIXA_FIM},
            "declaracao": "parametros fixados antes de medir modelo (declaracao do desenho, "
                          "nao uma verificacao do codigo)",
        },
        "entradas_v2": {
            "pasta": str(pasta_v2.resolve()),
            "sha256_antes": sha_antes,
            "sha256_depois": sha_depois,
            "v2_igual_antes_e_depois": sha_antes == sha_depois,
        },
        "colunas_ocultas": V4.COLUNAS_OCULTAS,
        "verificacoes": verif,
        "arquivos": {
            NOME_TABELA: {
                "arquivo": caminho.name,
                "linhas": int(len(df)),
                "colunas": int(df.shape[1]),
                "lista_de_colunas": list(df.columns),
                "dtypes": {str(c): str(t) for c, t in df.dtypes.items()},
                "bytes": int(caminho.stat().st_size),
                "sha256": _sha256(caminho),
                "hash_canonico": hash_canonico(df),
                "clientes_distintos": int(df["customer_id"].nunique()),
            }
        },
        "hash_canonico_metodo": HASH_CANONICO_METODO,
        "versoes": _versoes(),
    }
    (destino / MANIFESTO).write_text(
        json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8")

    meta = manifesto["arquivos"][NOME_TABELA]
    print(f"{NOME_TABELA}: {meta['linhas']} linhas x {meta['colunas']} col, "
          f"{meta['clientes_distintos']} clientes")
    print(f"  sha256 {meta['sha256']}")
    print(f"  canonico {meta['hash_canonico']}")
    print(f"  taxa de recuperacao {verif['taxa_recuperacao']} (v2: {verif['taxa_recuperacao_v2']}) | "
          f"faixa {verif['faixa_taxa_recuperacao']} | dentro: {verif['taxa_dentro_da_faixa']}")
    print(f"  features iguais as da v2: {verif['features_iguais_as_da_v2']} | "
          f"v2 igual ao manifesto antes e depois: {sha_antes == sha_depois}")
    print(f"{MANIFESTO} em {destino}")
    if not verif["taxa_dentro_da_faixa"] and not args.amostra_clientes:
        print("[BASE-V4] ATENCAO: a taxa de recuperacao saiu da faixa declarada.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
crai/scripts/gerar_base_v3_voluntario.py — Gera a base v3 do risco voluntário.

    python -m crai.scripts.gerar_base_v3_voluntario --seed 42 --out data/v3/

Lê da base v2 (`--v2`, default `data/v2/`), com o sha256 de CADA parquet
conferido contra o MANIFESTO.json antes de qualquer cálculo:

    populacao.parquet        latentes (só para o rótulo) e MRR
    comportamental.parquet   o retrato v2 em que os rastros se ancoram
    voluntario.parquet       só `customer_id`: o esqueleto de eventos

Escreve, em `--out`:

    voluntario_v3.parquet    uma linha por evento (ver `crai.ml.voluntario_v3`)
    MANIFESTO_v3.json

Nada da v2 é regravado. Depois de gravar, o sha256 da v2 é conferido de novo e
vai para o manifesto. Determinismo: mesma semente e mesmo ambiente dão o mesmo
sha256; entre ambientes vale o `hash_canonico` (método de `gerar_bases_v2`).
"""

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from ..ml import voluntario_v3 as V3
from .gerar_bases_v2 import (HASH_CANONICO_METODO, _fixar_dtypes, _sha256,
                             _versoes, hash_canonico)

ARQUIVOS_V2 = ["populacao", "classificador", "comportamental", "liquidez", "voluntario"]
NOME_TABELA = "voluntario_v3"


def conferir_v2(pasta: Path) -> dict:
    """`{nome: sha256}` da v2, levantando `SystemExit` se algum não confere."""
    manifesto = json.loads((pasta / "MANIFESTO.json").read_text(encoding="utf-8"))
    medidos = {}
    for nome in ARQUIVOS_V2:
        atual = _sha256(pasta / f"{nome}.parquet")
        declarado = manifesto["arquivos"][nome]["sha256"]
        if atual != declarado:
            raise SystemExit(f"[BASE-V3] {pasta / (nome + '.parquet')} nao confere com o "
                             f"MANIFESTO.json da v2 (medido {atual[:16]}, declarado "
                             f"{declarado[:16]}). A base v3 so e gerada sobre a v2 declarada.")
        medidos[nome] = atual
    return medidos


def gerar(pasta_v2: Path, seed: int) -> tuple:
    pop = pd.read_parquet(pasta_v2 / "populacao.parquet")
    comp = pd.read_parquet(pasta_v2 / "comportamental.parquet")
    esqueleto = pd.read_parquet(pasta_v2 / "voluntario.parquet", columns=["customer_id"])
    df = V3.gerar_voluntario_v3(pop, comp, esqueleto["customer_id"], seed)
    return df, V3.verificacoes(df, pop)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gera a base v3 do risco voluntario.")
    ap.add_argument("--seed", type=int, default=V3.SEED)
    ap.add_argument("--out", default="data/v3/")
    ap.add_argument("--v2", default="data/v2/")
    args = ap.parse_args(argv)

    pasta_v2 = Path(args.v2)
    destino = Path(args.out)
    if destino.resolve() == pasta_v2.resolve():
        raise SystemExit("[BASE-V3] --out aponta para a pasta da v2; a v2 nao e regravada.")

    sha_antes = conferir_v2(pasta_v2)
    df, verif = gerar(pasta_v2, args.seed)
    V3.conferir_features(V3.FEATURES_DE_RISCO_V3)

    destino.mkdir(parents=True, exist_ok=True)
    df = _fixar_dtypes(df)
    caminho = destino / f"{NOME_TABELA}.parquet"
    df.to_parquet(caminho, index=False)
    sha_depois = conferir_v2(pasta_v2)

    manifesto = {
        "base": "CRAI v3 - risco voluntario com rotulo latente",
        "gerado_por": "crai.scripts.gerar_base_v3_voluntario",
        "semente": args.seed,
        "fluxos": {
            "rotulo": f"SeedSequence({args.seed}, spawn_key={V3.FLUXO_ROTULO})",
            "rastros": f"SeedSequence({args.seed}, spawn_key={V3.FLUXO_RASTROS})",
            "eventos": f"SeedSequence({args.seed}, spawn_key={V3.FLUXO_EVENTOS})",
            "deriva": f"SeedSequence({args.seed}, spawn_key={V3.FLUXO_DERIVA})",
        },
        "formula": {
            "eta": "B0 + soma(coef * z) + interacao * z_preco * (-z_saude)",
            "B0": V3.B0,
            "coeficientes": V3.COEF_LATENTES,
            "interacao_preco_saude": V3.INTERACAO_PRECO_SAUDE,
            "interacao_e_escolha_de_desenho": True,
            "medias_latentes": V3.MEDIAS_LATENTES,
            "desvios_latentes": V3.DESVIOS_LATENTES,
            "ruido_rotulo_sd": V3.RUIDO_ROTULO_SD,
            "clip_p": list(V3.CLIP_P),
            "p_sorteio": V3.P_SORTEIO,
            "taxa_alvo_p_verdadeira": V3.TAXA_ALVO_P_VERDADEIRA,
            "grao_do_rotulo": "cliente (repetido nos eventos do cliente)",
        },
        "entradas_v2": {
            "pasta": str(pasta_v2.resolve()),
            "sha256_antes": sha_antes,
            "sha256_depois": sha_depois,
            "igual_ao_manifesto_v2": sha_antes == sha_depois,
        },
        "features_de_risco_v3": {"versao": V3.FEATURES_DE_RISCO_V3_VERSAO,
                                 "lista": V3.FEATURES_DE_RISCO_V3},
        "colunas_auxiliares": V3.COLUNAS_AUXILIARES,
        "colunas_ocultas": V3.COLUNAS_OCULTAS,
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
    (destino / "MANIFESTO_v3.json").write_text(
        json.dumps(manifesto, indent=2, ensure_ascii=False), encoding="utf-8")

    meta = manifesto["arquivos"][NOME_TABELA]
    print(f"{NOME_TABELA}: {meta['linhas']} linhas x {meta['colunas']} col, "
          f"{meta['clientes_distintos']} clientes")
    print(f"  sha256 {meta['sha256']}")
    print(f"  canonico {meta['hash_canonico']}")
    print(f"  taxa de churn (evento) {verif['taxa_churn_evento']} | teto de Bayes (evento) "
          f"{verif['teto_bayes_auc_evento_base_inteira']}")
    print(f"  v2 igual ao manifesto antes e depois: {sha_antes == sha_depois}")
    print(f"MANIFESTO_v3.json em {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

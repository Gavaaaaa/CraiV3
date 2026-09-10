"""crai/scripts/preparar_amostra_real.py — Âncora real do dataset sintético.

Constrói, de forma **determinística e auditável**, os dois artefatos de dados
reais que o Sprint 3 usa para calibrar `crai/ml/synthetic_data.py`:

    data/real/amostra_300.csv        Fonte A — 300 transações reais (Olist)
    data/real/bacen_sgs_21084.json   Fonte B — inadimplência PF (BACEN SGS)
    data/real/bacen_sgs_21129.json   Fonte B — inadimplência PF cartão
    data/real/PROVENIENCIA.json      hashes, contagens e parâmetros do sorteio

Rodar de `crai/`:

    python -m crai.scripts.preparar_amostra_real

O script é idempotente: mesma seed, mesmas 300 linhas, mesmo SHA-256. É isso
que permite ao DATA_CARD afirmar *qual* amostra foi usada, e não apenas que
"uma amostra foi usada".

FONTE A — Brazilian E-Commerce Public Dataset by Olist
    Licença CC BY-NC-SA 4.0 (uso acadêmico permitido, comercial NÃO).
    Origem canônica: kaggle.com/datasets/olistbr/brazilian-ecommerce
    Kaggle exige conta; para manter o pipeline reprodutível sem credencial, o
    download usa um espelho público dos mesmos arquivos (ver ESPELHO_OLIST) e
    o SHA-256 de cada arquivo fica registrado em PROVENIENCIA.json — quem
    quiser conferir baixa do Kaggle e compara o hash.

FONTE B — BACEN, Sistema Gerenciador de Séries Temporais (SGS)
    Licença Open Data Commons ODbL, API aberta, sem chave.
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_REAL = BASE_DIR / "data" / "real"

SEED_AMOSTRA = 42
N_AMOSTRA = 300

ESPELHO_OLIST = "https://raw.githubusercontent.com/dujiaying/olist/master/data"
ARQUIVOS_OLIST = (
    "olist_order_payments_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_customers_dataset.csv",
)

SGS_URL = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.{serie}/dados"
           "?formato=json&dataInicial=01/01/2020")
SERIES_SGS = (21084, 21129)

# `not_defined` são 3 linhas sem meio de pagamento declarado; valor zero é
# quitação integral por voucher. Nenhum dos dois informa a marginal de valor
# de uma cobrança de assinatura, então saem antes do sorteio — e o número de
# linhas descartadas fica registrado na proveniência.
TIPOS_EXCLUIDOS = ("not_defined",)


def _sha256(caminho: Path) -> str:
    return hashlib.sha256(caminho.read_bytes()).hexdigest()


def baixar_olist(destino: Path) -> dict[str, str]:
    """Baixa os três CSVs da Fonte A (se ainda não estiverem em `destino`)."""
    destino.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for nome in ARQUIVOS_OLIST:
        alvo = destino / nome
        if not alvo.exists():
            print(f"[DADOS] baixando {nome} …")
            urllib.request.urlretrieve(f"{ESPELHO_OLIST}/{nome}", alvo)
        hashes[nome] = _sha256(alvo)
        print(f"[DADOS] {nome}: {alvo.stat().st_size} bytes | sha256 {hashes[nome][:16]}…")
    return hashes


def baixar_sgs() -> dict[int, int]:
    """Baixa as séries da Fonte B. Devolve {serie: nº de observações}."""
    DATA_REAL.mkdir(parents=True, exist_ok=True)
    contagens = {}
    for serie in SERIES_SGS:
        with urllib.request.urlopen(SGS_URL.format(serie=serie), timeout=60) as r:
            dados = json.loads(r.read().decode("utf-8"))
        alvo = DATA_REAL / f"bacen_sgs_{serie}.json"
        alvo.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
        contagens[serie] = len(dados)
        print(f"[DADOS] SGS {serie}: {len(dados)} observações -> {alvo.name}")
    return contagens


def montar_populacao(origem: Path) -> pd.DataFrame:
    """Junta pagamentos + timestamp do pedido + UF do cliente."""
    pag = pd.read_csv(origem / "olist_order_payments_dataset.csv")
    ped = pd.read_csv(
        origem / "olist_orders_dataset.csv",
        usecols=["order_id", "customer_id", "order_purchase_timestamp"],
    )
    cli = pd.read_csv(
        origem / "olist_customers_dataset.csv",
        usecols=["customer_id", "customer_state"],
    )

    df = pag.merge(ped, on="order_id", how="inner").merge(cli, on="customer_id", how="inner")
    df["order_purchase_timestamp"] = pd.to_datetime(df["order_purchase_timestamp"])

    bruto = len(df)
    df = df[~df["payment_type"].isin(TIPOS_EXCLUIDOS)]
    df = df[df["payment_value"] > 0]
    print(f"[DADOS] população: {bruto} -> {len(df)} linhas após filtro "
          f"(tipo indefinido / valor zero)")

    # O identificador do cliente não entra na amostra: só a UF, que é agregada.
    return df.drop(columns=["customer_id"]).reset_index(drop=True)


def amostrar_estratificado(df: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """300 linhas estratificadas por payment_type × quartil de valor.

    Alocação **proporcional** ao tamanho de cada estrato na população, com
    arredondamento por maior resto. Proporcional é a escolha certa aqui porque
    a amostra tem que preservar o mix real de meio de pagamento — é uma das
    três marginais que ela existe para medir. Um desenho balanceado daria peso
    igual a `debit_card` (1,5% do real) e destruiria justamente o número que o
    TCC quer citar.
    """
    df = df.copy()
    df["faixa_valor"] = pd.qcut(df["payment_value"], q=4,
                                labels=["Q1", "Q2", "Q3", "Q4"])
    df["estrato"] = df["payment_type"].astype(str) + "|" + df["faixa_valor"].astype(str)

    tamanhos = df["estrato"].value_counts().sort_index()
    exato = tamanhos / tamanhos.sum() * n
    cotas = np.floor(exato).astype(int)

    # Maior resto, com desempate pelo nome do estrato — determinístico.
    faltam = n - int(cotas.sum())
    if faltam > 0:
        restos = (exato - cotas).sort_values(ascending=False, kind="mergesort")
        for estrato in restos.index[:faltam]:
            cotas[estrato] += 1

    partes = []
    for estrato in sorted(cotas.index):
        k = int(cotas[estrato])
        if k == 0:
            continue
        sub = df[df["estrato"] == estrato]
        # Seed derivada do nome do estrato: o sorteio de um estrato não muda
        # quando outro muda de tamanho.
        semente = seed + int(hashlib.md5(estrato.encode()).hexdigest(), 16) % 10_000
        partes.append(sub.sample(n=min(k, len(sub)), random_state=semente))

    amostra = pd.concat(partes, ignore_index=True)
    amostra = amostra.sort_values(["payment_type", "payment_value", "order_id"],
                                  kind="mergesort").reset_index(drop=True)

    ts = amostra["order_purchase_timestamp"]
    amostra["hour_of_day"] = ts.dt.hour
    amostra["day_of_week"] = ts.dt.dayofweek
    amostra["day_of_month"] = ts.dt.day
    return amostra


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prepara a âncora real da CRAI")
    ap.add_argument("--origem", type=Path, default=DATA_REAL / "_olist_bruto",
                    help="diretório onde os CSVs da Fonte A ficam (baixa se faltar)")
    ap.add_argument("--sem-rede", action="store_true",
                    help="não baixa nada; usa o que já está em disco")
    args = ap.parse_args(argv)

    DATA_REAL.mkdir(parents=True, exist_ok=True)

    if args.sem_rede:
        hashes = {n: _sha256(args.origem / n) for n in ARQUIVOS_OLIST}
        contagens_sgs = {}
    else:
        hashes = baixar_olist(args.origem)
        contagens_sgs = baixar_sgs()

    populacao = montar_populacao(args.origem)
    amostra = amostrar_estratificado(populacao, N_AMOSTRA, SEED_AMOSTRA)

    destino = DATA_REAL / "amostra_300.csv"
    amostra.to_csv(destino, index=False, encoding="utf-8")
    sha_amostra = _sha256(destino)

    proveniencia = {
        "gerado_por": "crai/scripts/preparar_amostra_real.py",
        "seed": SEED_AMOSTRA,
        "n_amostra": int(len(amostra)),
        "fonte_a": {
            "nome": "Brazilian E-Commerce Public Dataset by Olist",
            "licenca": "CC BY-NC-SA 4.0 (nao comercial)",
            "origem_canonica": "kaggle.com/datasets/olistbr/brazilian-ecommerce",
            "espelho_usado": ESPELHO_OLIST,
            "sha256_arquivos": hashes,
            "linhas_populacao_filtrada": int(len(populacao)),
            "periodo": [str(populacao["order_purchase_timestamp"].min()),
                        str(populacao["order_purchase_timestamp"].max())],
        },
        "fonte_b": {
            "nome": "BACEN SGS",
            "licenca": "ODbL",
            "series": {str(s): contagens_sgs.get(s) for s in SERIES_SGS},
        },
        "amostra": {
            "arquivo": destino.name,
            "sha256": sha_amostra,
            "mix_payment_type": amostra["payment_type"].value_counts().to_dict(),
            "valor": {
                "min": float(amostra["payment_value"].min()),
                "media": round(float(amostra["payment_value"].mean()), 2),
                "mediana": float(amostra["payment_value"].median()),
                "max": float(amostra["payment_value"].max()),
            },
        },
    }
    (DATA_REAL / "PROVENIENCIA.json").write_text(
        json.dumps(proveniencia, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[DADOS] amostra: {len(amostra)} linhas -> {destino}")
    print(f"[DADOS] sha256: {sha_amostra}")
    print(f"[DADOS] mix: {proveniencia['amostra']['mix_payment_type']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""crai/scripts/gerar_bases_demo.py — as bases de demonstração da régua da base.

Gera TRÊS CSV de 500 clientes cada em `painel/exemplos/`, com semente fixa
(rodar duas vezes produz arquivos byte a byte idênticos), nas colunas que
`POST /clientes/importar` espera sem `mapeamento`:

    customer_id_externo; mrr; billing_profile; days_since_last; features_used_30d; email

    base_uso_diario.csv    SaaS de uso intenso: mediana de `days_since_last`
                           perto de 1, `features_used_30d` alto. Aqui, 7 dias
                           sem login já é um sinal.
    base_uso_mensal.csv    SaaS de uso esporádico, um fechamento por mês:
                           mediana perto de 25. Aqui, 7 dias sem login é rotina.
    base_saudavel.csv      Todo mundo entrou esta semana e usa o produto. É a
                           base que prova que o sistema não grita à toa: a
                           lista sai ordenada e ninguém vira "alto"/"critico".

CLIENTES-ÂNCORA. As bases diária e mensal têm, de propósito, o mesmo punhado
de clientes com `days_since_last` IDÊNTICO (exatamente 7 e exatamente 20),
mesmo MRR (R$ 300,00), mesmo perfil (CLT) e mesmo uso (3 funcionalidades):
`ANCORA-07-A`, `ANCORA-07-B`, `ANCORA-20-A`, `ANCORA-20-B`. Pontuados nas duas
bases, são eles que mostram na tela que a régua mudou. A base saudável NÃO os
tem — ela existe para ficar sem alarme, e um cliente com 7 dias sem login
numa base em que ninguém passa de 4 seria, corretamente, alarme.

Todas as três têm `SEM_DADO` linhas sem `days_since_last` nem
`features_used_30d`, para a tela ter o caso `dado_insuficiente` de verdade.

CSV em UTF-8 com BOM, separador `;`, decimal com vírgula: é o que o Excel
brasileiro abre com dois cliques e o que o importador lê.

Uso (a partir de app/):
    python -m crai.scripts.gerar_bases_demo
"""

import csv
import random
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent      # app/
DESTINO = BASE_DIR.parent / "painel" / "exemplos"

SEMENTE = 20260912
N_CLIENTES = 500
SEM_DADO = 12                       # linhas sem dado comportamental, por base
PERFIS = ("CLT", "PJ", "freelancer")
COLUNAS = ("customer_id_externo", "mrr", "billing_profile",
           "days_since_last", "features_used_30d", "email")

# (id, days_since_last, features_used_30d, mrr, perfil) — iguais nas duas bases
ANCORAS = (
    ("ANCORA-07-A", 7, 3, 300.0, "CLT"),
    ("ANCORA-07-B", 7, 3, 300.0, "CLT"),
    ("ANCORA-20-A", 20, 3, 300.0, "CLT"),
    ("ANCORA-20-B", 20, 3, 300.0, "CLT"),
)


def _escolha_ponderada(rng: random.Random, valores, pesos) -> int:
    return rng.choices(valores, weights=pesos, k=1)[0]


def _mrr(rng: random.Random) -> float:
    """Faixa de PME brasileira: a maioria entre R$ 40 e R$ 400, cauda até ~R$ 3.000."""
    valor = rng.lognormvariate(4.8, 0.65)
    return round(min(max(valor, 39.90), 3200.0), 2)


def _dias_uso_diario(rng):
    return _escolha_ponderada(rng, [0, 1, 2, 3, 4, 5, 6, 8, 12, 18, 30],
                              [34, 26, 14, 9, 6, 4, 2, 2, 1.5, 1, 0.5])


def _uso_uso_diario(rng):
    return _escolha_ponderada(rng, list(range(4, 21)), [1, 2, 3, 5, 7, 9, 10, 10, 10, 9, 8, 7, 6, 5, 4, 3, 2])


def _dias_uso_mensal(rng):
    return int(min(max(round(rng.gauss(25, 8)), 0), 60))


def _uso_uso_mensal(rng):
    return _escolha_ponderada(rng, [0, 1, 2, 3, 4, 5, 6, 7, 8], [2, 12, 20, 24, 20, 12, 6, 3, 1])


def _dias_saudavel(rng):
    return _escolha_ponderada(rng, [0, 1, 2, 3, 4], [38, 30, 17, 10, 5])


def _uso_saudavel(rng):
    return _escolha_ponderada(rng, list(range(8, 21)), [3, 5, 7, 9, 10, 10, 10, 9, 8, 7, 5, 4, 3])


PERFIS_DE_BASE = {
    "base_uso_diario.csv": (_dias_uso_diario, _uso_uso_diario, True),
    "base_uso_mensal.csv": (_dias_uso_mensal, _uso_uso_mensal, True),
    "base_saudavel.csv":   (_dias_saudavel, _uso_saudavel, False),
}


def gerar_base(nome: str, dias_fn, uso_fn, com_ancoras: bool) -> list[dict]:
    rng = random.Random(f"{SEMENTE}:{nome}")
    prefixo = nome.removeprefix("base_").removesuffix(".csv").replace("_", "-")
    linhas = []
    for i in range(1, N_CLIENTES + 1):
        cid = f"{prefixo}-{i:04d}"
        linhas.append({
            "customer_id_externo": cid,
            "mrr": _mrr(rng),
            "billing_profile": rng.choice(PERFIS),
            "days_since_last": dias_fn(rng),
            "features_used_30d": uso_fn(rng),
            "email": f"financeiro+{cid}@exemplo.com.br",
        })
    # Linhas sem dado comportamental: sorteadas, e as duas colunas vazias.
    for pos in rng.sample(range(N_CLIENTES), SEM_DADO):
        linhas[pos]["days_since_last"] = None
        linhas[pos]["features_used_30d"] = None
    if com_ancoras:
        # Substituem posições fixas para a base continuar com 500 linhas.
        for (cid, dias, uso, mrr, perfil), pos in zip(ANCORAS, (49, 149, 249, 349)):
            linhas[pos] = {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil,
                           "days_since_last": dias, "features_used_30d": uso,
                           "email": f"financeiro+{cid.lower()}@exemplo.com.br"}
    return linhas


def _celula(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float):
        return f"{valor:.2f}".replace(".", ",")
    return str(valor)


def escrever(caminho: Path, linhas: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";", lineterminator="\r\n")
        w.writerow(COLUNAS)
        for l in linhas:
            w.writerow([_celula(l[c]) for c in COLUNAS])


def _mediana(valores: list) -> float:
    v = sorted(x for x in valores if x is not None)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def main() -> None:
    for nome, (dias_fn, uso_fn, com_ancoras) in PERFIS_DE_BASE.items():
        linhas = gerar_base(nome, dias_fn, uso_fn, com_ancoras)
        escrever(DESTINO / nome, linhas)
        dias = [l["days_since_last"] for l in linhas]
        uso = [l["features_used_30d"] for l in linhas]
        ancoras = [l["customer_id_externo"] for l in linhas if l["customer_id_externo"].startswith("ANCORA")]
        print(f"{nome}: {len(linhas)} linhas | mediana days_since_last = {_mediana(dias)} | "
              f"mediana features_used_30d = {_mediana(uso)} | sem dado = {sum(1 for d in dias if d is None)} | "
              f"ancoras = {ancoras or 'nenhum'}")


if __name__ == "__main__":
    main()

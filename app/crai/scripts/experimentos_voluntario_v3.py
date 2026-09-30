"""
crai/scripts/experimentos_voluntario_v3.py — Bloco 4 do treino v3: três experimentos.

Nenhum código existente é alterado. Este script só CHAMA o que já existe
(`gerar_base_v3_voluntario.main`, `treinar_voluntario_v3.treinar`) com uma
variação declarada, e nunca escreve em `data/v2/`, `data/v3/`, `models/` nem
`models/v3/`.

    # (1) candidato só com o vetor que a produção recebe hoje (FEATURES_DE_RISCO)
    python -m crai.scripts.experimentos_voluntario_v3 treinar-vetor-producao \\
        --base data/v3/ --out models/v3_vetor_producao/

    # (2) base v3 sem o termo de interação preço x saúde
    python -m crai.scripts.experimentos_voluntario_v3 gerar --variante sem_interacao \\
        --seed 42 --out data/v3_sem_interacao/

    # (3) base v3 com o evento de cancelamento forte
    python -m crai.scripts.experimentos_voluntario_v3 gerar --variante intencao_forte \\
        --seed 42 --out data/v3_intencao_forte/

As bases (2) e (3) são treinadas com o script de sempre:

    python -m crai.scripts.treinar_voluntario_v3 --base data/v3_sem_interacao/ \\
        --out models/v3_sem_interacao/

As variantes — cada uma muda UMA coisa em relação à base v3:

    sem_interacao   o rótulo perde o termo 0,30 * z_preco * (-z_saude), o único não
                    aditivo, posto de propósito e que favorece árvores (A3). B0 é
                    resolvido de novo, pelo mesmo procedimento do Bloco 0 (média de
                    p_verdadeira = 0,15 na população), para a taxa não mudar por
                    tabela. Rastros e eventos: idênticos à v3.
    intencao_forte  o rótulo é o MESMO da v3, cliente a cliente. Só muda o tipo de
                    evento: a probabilidade de "Cancellation Page Viewed" passa a
                    seguir o risco latente inteiro, exp(K_INTENCAO * z_eta), com z_eta
                    o eta padronizado nos eventos, com a escala resolvida por bisseção
                    para a média DEPOIS do corte em 1 ser o mesmo mix de 5%. O evento continua sendo rastro das latentes, não do rótulo:
                    nada no comportamento é causado pelo rótulo.

A variação é aplicada trocando, durante a geração e só nela, uma constante ou uma
função do módulo `crai.ml.voluntario_v3` (`_variante`, com restauração garantida
em `finally`). A descrição da variante vai para `VARIANTE.json`, ao lado do
`MANIFESTO_v3.json` que o gerador de sempre escreve.
"""

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from ..churn_voluntary.risk_scorer import FEATURES_DE_RISCO
from ..ml import voluntario_v3 as V3
from ..ml.populacao import LATENTES
from . import gerar_base_v3_voluntario as G
from . import treinar_voluntario_v3 as T

APP = Path(__file__).resolve().parents[2]
# Pastas que o Bloco 4 não pode tocar.
PROTEGIDAS = [APP / "data" / "v2", APP / "data" / "v3", APP / "models",
              APP / "models" / "v3"]

VARIANTES = ("sem_interacao", "intencao_forte")
K_INTENCAO = 1.5


def _conferir_destino(out: Path):
    alvo = Path(out).resolve()
    for p in PROTEGIDAS:
        if alvo == p.resolve():
            raise SystemExit(f"[BLOCO4] --out {out} e uma pasta protegida ({p}); "
                             "o Bloco 4 grava so em pasta propria.")


def b0_para_taxa(pop: pd.DataFrame, interacao: float,
                 alvo: float = V3.TAXA_ALVO_P_VERDADEIRA) -> float:
    """B0 com média de p_verdadeira = `alvo` na população (bisseção, sem sorteio).
    O mesmo procedimento do piloto do Bloco 0."""
    z = {l: ((pop[l].to_numpy(dtype=float) - V3.MEDIAS_LATENTES[l]) / V3.DESVIOS_LATENTES[l])
         for l in LATENTES}
    eta = sum(V3.COEF_LATENTES[l] * z[l] for l in LATENTES)
    eta = eta + interacao * z["pressao_preco"] * (-z["saude_financeira"])
    lo, hi = -8.0, 4.0
    for _ in range(100):
        meio = (lo + hi) / 2
        media = (1.0 / (1.0 + np.exp(-(meio + eta)))).mean()
        lo, hi = (meio, hi) if media < alvo else (lo, meio)
    return round((lo + hi) / 2, 4)


def _tipos_de_evento_intencao_forte(z_ev: dict, rng: np.random.Generator) -> np.ndarray:
    """Como `V3._tipos_de_evento`, com o cancelamento seguindo o eta inteiro.

    Mesmo fluxo aleatório, mesma ordem de sorteio (um uniforme por evento), mesmo
    downgrade, mesmo mix na média. Só o peso do cancelamento muda."""
    eta = V3.B0 + sum(V3.COEF_LATENTES[l] * z_ev[l] for l in LATENTES)
    eta = eta + V3.INTERACAO_PRECO_SAUDE * z_ev["pressao_preco"] * (-z_ev["saude_financeira"])
    z_eta = (eta - eta.mean()) / eta.std()
    w_canc = np.exp(K_INTENCAO * z_eta)
    w_down = np.exp(0.6 * z_ev["pressao_preco"] - 0.3 * z_ev["saude_financeira"])
    # A cauda de exp passa de 1; dividir pela média e cortar em 1 tiraria massa
    # (o mix cairia abaixo de 5%). A escala é resolvida por bisseção para que a
    # média DEPOIS do corte seja a do mix.
    alvo = V3.MIX_EVENTOS["Cancellation Page Viewed"]
    lo, hi = 0.0, alvo / w_canc.min()
    for _ in range(100):
        meio = (lo + hi) / 2
        lo, hi = (meio, hi) if np.minimum(meio * w_canc, 1.0).mean() < alvo else (lo, meio)
    p_canc = np.minimum((lo + hi) / 2 * w_canc, 1.0)
    p_down = V3.MIX_EVENTOS["Downgrade Clicked"] * w_down / w_down.mean()
    p_down = p_down - np.maximum(p_canc + p_down - 1.0, 0.0)
    u = rng.random(len(p_canc))
    return np.where(u < p_canc, "Cancellation Page Viewed",
                    np.where(u < p_canc + p_down, "Downgrade Clicked", "Session Started"))


@contextlib.contextmanager
def _variante(**trocas):
    """Troca atributos de `crai.ml.voluntario_v3` e SEMPRE os restaura."""
    antes = {nome: getattr(V3, nome) for nome in trocas}
    try:
        for nome, valor in trocas.items():
            setattr(V3, nome, valor)
        yield
    finally:
        for nome, valor in antes.items():
            setattr(V3, nome, valor)


def descricao_variante(variante: str, pop: pd.DataFrame) -> tuple:
    """(trocas, descrição) de uma variante."""
    if variante == "sem_interacao":
        b0 = b0_para_taxa(pop, interacao=0.0)
        return ({"INTERACAO_PRECO_SAUDE": 0.0, "B0": b0},
                {"variante": variante,
                 "muda": "rotulo: sem o termo de interacao preco x saude",
                 "INTERACAO_PRECO_SAUDE": {"v3": V3.INTERACAO_PRECO_SAUDE, "variante": 0.0},
                 "B0": {"v3": V3.B0, "variante": b0,
                        "como": "bissecao para media de p_verdadeira = "
                                f"{V3.TAXA_ALVO_P_VERDADEIRA} na populacao"},
                 "igual_a_v3": "coeficientes das latentes, ruido, sorteio, fluxos, rastros, eventos"})
    if variante == "intencao_forte":
        return ({"_tipos_de_evento": _tipos_de_evento_intencao_forte},
                {"variante": variante,
                 "muda": "tipo de evento: P(Cancellation Page Viewed) proporcional a "
                         f"exp({K_INTENCAO} * z_eta), eta padronizado nos eventos, "
                         "escala por bissecao para media de 5% depois do corte em 1; downgrade como na "
                         "v3, descontado so onde cancelamento + downgrade passaria de 1",
                 "K_INTENCAO": K_INTENCAO,
                 "igual_a_v3": "rotulo (cliente a cliente), rastros, downgrade, fluxos, mix medio"})
    raise SystemExit(f"[BLOCO4] variante desconhecida: {variante}")


def gerar(variante: str, seed: int, out: Path, v2: Path) -> int:
    _conferir_destino(out)
    pop = pd.read_parquet(Path(v2) / "populacao.parquet")
    trocas, descricao = descricao_variante(variante, pop)
    with _variante(**trocas):
        rc = G.main(["--seed", str(seed), "--out", str(out), "--v2", str(v2)])
    (Path(out) / "VARIANTE.json").write_text(
        json.dumps({**descricao, "semente": seed, "gerado_por":
                    "crai.scripts.experimentos_voluntario_v3 gerar"},
                   indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"VARIANTE.json em {out} ({variante})")
    return rc


def treinar_vetor_producao(base: Path, out: Path, **kw) -> dict:
    """Experimento (1): o mesmo treino, só com `FEATURES_DE_RISCO` (6 colunas)."""
    _conferir_destino(out)
    print(f"[BLOCO4] Experimento (1): features = FEATURES_DE_RISCO {FEATURES_DE_RISCO}")
    return T.treinar(Path(base), Path(out), features=list(FEATURES_DE_RISCO), **kw)


def main(argv=None) -> int:
    if hasattr(sys.stdout, "buffer") and \
            (sys.stdout.encoding or "").lower().replace("-", "") != "utf8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Bloco 4 do treino v3: tres experimentos.")
    sub = ap.add_subparsers(dest="comando", required=True)
    g = sub.add_parser("gerar", help="gera uma base variante em pasta propria")
    g.add_argument("--variante", choices=VARIANTES, required=True)
    g.add_argument("--seed", type=int, default=V3.SEED)
    g.add_argument("--out", required=True)
    g.add_argument("--v2", default="data/v2/")
    t = sub.add_parser("treinar-vetor-producao",
                       help="treina so com o vetor que a producao recebe hoje")
    t.add_argument("--base", default="data/v3/")
    t.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    if args.comando == "gerar":
        return gerar(args.variante, args.seed, Path(args.out), Path(args.v2))
    treinar_vetor_producao(Path(args.base), Path(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
crai/ml/split.py — Split de treino/teste POR CLIENTE, para bases com várias linhas por cliente.

Por que existe (Bloco B, 20/09/2026). A base v2 tem várias cobranças por cliente
(Módulo 1) e vários eventos por cliente (candidato voluntário). Um
`train_test_split` por linha põe o mesmo cliente nos dois lados do holdout, e
features que são atributos DELE (`payment_history_score`, `avg_ticket`,
`tenure_months`, `mrr`) deixam de ser features e viram identificador: o modelo
"acerta" o teste reconhecendo o cliente, não aprendendo a regra. Medido em
20/09 não havia vazamento (AUC 0,7094 por linha × 0,7095 por cliente), mas a
igualdade é sorte, e para de ser sorte no dia em que uma feature por cliente
ficar mais informativa. A garantia tem que estar no código — é este módulo.

O Módulo 3 (`payday_inference`) já faz split por cliente do seu jeito (a unidade
dele é a série); o Módulo 2 tem uma linha por cliente e não precisa de grupo.
"""

import numpy as np
from sklearn.model_selection import GroupShuffleSplit


def split_por_cliente(groups, test_size: float = 0.2, seed: int = 42) -> tuple:
    """Índices (treino, teste) com nenhum grupo nos dois lados.

    `GroupShuffleSplit(n_splits=1, test_size, random_state=seed)`: a fração de
    teste é de GRUPOS (clientes), não de linhas, e todo cliente cai inteiro em
    um lado só. Devolve `(idx_treino, idx_teste)` como arrays de posição.

    Args:
        groups: identificador de grupo por linha (por exemplo `df["customer_id"]`)
        test_size: fração de clientes reservada para teste
        seed: `random_state` do sorteio de clientes
    """
    groups = np.asarray(groups)
    if groups.ndim != 1 or len(groups) == 0:
        raise ValueError("groups precisa ser um vetor 1-D não vazio, um valor por linha")
    gss = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
    idx_treino, idx_teste = next(gss.split(np.zeros(len(groups)), groups=groups))
    return idx_treino, idx_teste


def conferir_sem_vazamento(groups, idx_treino, idx_teste) -> None:
    """Levanta `AssertionError` se algum grupo aparece nos dois lados."""
    groups = np.asarray(groups)
    comuns = set(groups[idx_treino]) & set(groups[idx_teste])
    if comuns:
        raise AssertionError(
            f"{len(comuns)} cliente(s) nos dois lados do split (ex.: "
            f"{sorted(map(str, comuns))[:3]}) — o holdout está vazando identidade")

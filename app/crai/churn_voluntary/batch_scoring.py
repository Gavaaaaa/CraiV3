"""crai/churn_voluntary/batch_scoring.py — pontua toda a base importada de uma empresa.

O caminho (2) do self-service chega aqui: a empresa subiu a planilha (Sprint
2) e quer saber quem está em risco. Este módulo lê `clientes_importados` do
tenant e passa cada linha pelo MESMO motor do caminho (1) —
`risk_scorer.risco_por_features`, que é o `calculate_risk` sem exigir um
evento pontual. Regra ou modelo treinado, quem decide é o risk_scorer; aqui
não há lógica de risco, só a fila e a ordem.

A ARMADILHA DO DADO FALTANTE, e por que este módulo existe além de um `for`.
Nas regras fixas, `days_since_last` ausente vale 0 e `features_used_30d`
ausente vale 10 — e os dois juntos dão risco 0.0. No SDK isso é inofensivo: o
Segment manda os dois números sempre, o default nunca é exercido. Na planilha
NÃO é: uma base sem essas colunas viraria uma lista de clientes "sem risco
nenhum", que é o oposto de "não dá para avaliar". Por isso, linha sem
`days_since_last` E sem `features_used_30d` NÃO passa pelo motor: recebe
`risk_score: None` e `criticality: "dado_insuficiente"`, com o texto dizendo
isso, e vai para o FIM da lista — separada do risco 0.0 de verdade, que é o
cliente ativo e engajado. MRR e perfil entram só para o cadastro aparecer;
não inventam risco.

Com UMA das duas presente, o motor roda com o default da outra, e a explicação
diz qual faltou — a empresa vê que o número é parcial em vez de descobrir
depois.

Sem estado, sem escrita: é leitura + cálculo. O que persiste é a base (Sprint
2) e, no caminho do SDK, o log de ciclos; este módulo não grava nada.
"""

import logging

from . import clientes_importados
from .risk_scorer import classify_criticality, risco_por_features

logger = logging.getLogger(__name__)

CRITICIDADE_SEM_DADO = "dado_insuficiente"
TEXTO_SEM_DADO = ("sem dado de atividade — impossível avaliar risco de churn "
                  "para este cliente")

# Ordem de exibição da criticidade quando o risco empata (e para o frontend
# filtrar por "mínima"): maior primeiro.
ORDEM_CRITICIDADE = {"critico": 3, "alto": 2, "padrao": 1, CRITICIDADE_SEM_DADO: 0}


def _reais(valor) -> str:
    """1500.5 → 'R$ 1.500,50'. Formato pt-BR sem depender de locale do SO."""
    try:
        s = f"{float(valor):,.2f}"
    except (TypeError, ValueError):
        return "MRR desconhecido"
    return "R$ " + s.replace(",", "|").replace(".", ",").replace("|", ".")


def _inteiro_se_der(valor):
    """47.0 → 47 no texto; 2.5 fica 2.5."""
    f = float(valor)
    return int(f) if f.is_integer() else f


def explicar(cliente: dict, risk_score, criticality: str) -> str:
    """Uma frase curta, em PT-BR, com os números que produziram o risco.

    É a explicabilidade do caminho (2): as próprias features, ditas. Não há
    SHAP aqui porque não há modelo treinado hoje; quando houver, o texto
    continua verdadeiro — são as entradas, não os pesos.
    """
    if criticality == CRITICIDADE_SEM_DADO:
        return TEXTO_SEM_DADO

    partes = []
    dias = cliente.get("days_since_last")
    uso = cliente.get("features_used_30d")
    if dias is not None:
        d = _inteiro_se_der(dias)
        partes.append("acessou hoje" if d == 0 else f"sem login há {d} dia{'s' if d != 1 else ''}")
    else:
        partes.append("dias sem login desconhecidos (assumido 0)")
    if uso is not None:
        u = _inteiro_se_der(uso)
        partes.append(f"usa {u} funcionalidade{'s' if u != 1 else ''} nos últimos 30 dias")
    else:
        partes.append("uso de funcionalidades desconhecido (assumido 10)")
    partes.append(f"MRR {_reais(cliente.get('mrr'))}")

    texto = ", ".join(partes)
    if criticality == "critico" and risk_score is not None and risk_score < 0.90:
        texto += " — crítico pelo valor da conta, não pelo risco"
    return texto


def pontuar_cliente(cliente: dict) -> dict:
    """Uma linha da base → uma linha do ranking. Não grava nada."""
    dias = cliente.get("days_since_last")
    uso = cliente.get("features_used_30d")
    mrr = cliente.get("mrr")

    if dias is None and uso is None:
        risk, crit = None, CRITICIDADE_SEM_DADO
    else:
        risk = risco_por_features(dias, uso, mrr)
        crit = classify_criticality(risk, mrr)

    return {
        "customer_id_externo": cliente["customer_id_externo"],
        "risk_score": risk,
        "criticality": crit,
        "explicacao": explicar(cliente, risk, crit),
        "mrr": mrr,
        "billing_profile": cliente.get("billing_profile"),
        "days_since_last": dias,
        "features_used_30d": uso,
        "email": cliente.get("email"),
        "importado_em": cliente.get("importado_em"),
    }


def ordenar(linhas: list[dict]) -> list[dict]:
    """Risco decrescente; empate por criticidade e depois MRR; sem dado por último.

    `dado_insuficiente` não pode se misturar com risco 0.0: um cliente ativo e
    engajado (0.0 de verdade) e um cliente que ninguém sabe avaliar são coisas
    diferentes, e a lista precisa mostrar isso mesmo sem ler a explicação.
    """
    def chave(l):
        sem_dado = l["risk_score"] is None
        return (
            1 if sem_dado else 0,
            -(l["risk_score"] or 0.0),
            -ORDEM_CRITICIDADE.get(l["criticality"], 0),
            -(l["mrr"] or 0.0),
            l["customer_id_externo"],
        )
    return sorted(linhas, key=chave)


def pontuar_base(tenant_id: str) -> list[dict]:
    """O ranking de risco da base importada DESTE tenant.

    Lê só o tenant pedido — `clientes_importados.listar` não tem leitura sem
    filtro, de propósito. Devolve lista ordenada (ver `ordenar`).
    """
    base = clientes_importados.listar(tenant_id)
    ranking = ordenar([pontuar_cliente(c) for c in base])
    sem_dado = sum(1 for l in ranking if l["criticality"] == CRITICIDADE_SEM_DADO)
    logger.info("[BATCH-SCORING] tenant=%s clientes=%d sem_dado=%d",
                tenant_id, len(ranking), sem_dado)
    return ranking

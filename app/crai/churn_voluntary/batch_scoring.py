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

A RÉGUA DA BASE. As regras fixas comparam `days_since_last` com 30 e
`features_used_30d` com 5 — constantes iguais para toda base. Como
`pontuar_base` já lê a base inteira do tenant antes de pontuar, ele calcula
ali, em memória, os percentis 50/75/90 das duas colunas (`regua_da_base`) e
pontua cada cliente pela POSIÇÃO dele na própria base
(`risk_scorer.risco_por_posicao`). Nada é gravado e nenhum schema muda.
REGRA DE SEGURANÇA: com menos de `MINIMO_LINHAS_REGUA_DA_BASE` linhas
utilizáveis em qualquer das duas colunas, ou com distribuição degenerada, a
régua é a global e o número é IDÊNTICO ao de antes. Cada linha do ranking diz
qual régua usou em `origem_da_regua`, e a explicação diz em português.
POSIÇÃO ORDENA, CRITICIDADE EXIGE SINAL ABSOLUTO: na régua da base, "alto" e
"critico" por risco só saem se o cliente também cruzou o piso absoluto de
desengajamento (`risk_scorer.sinal_absoluto_de_desengajamento`). Numa base
saudável a lista continua ordenada — "por quem eu começo?" — e ninguém vira
alarme.

Sem estado, sem escrita: é leitura + cálculo. O que persiste é a base (Sprint
2) e, no caminho do SDK, o log de ciclos; este módulo não grava nada.
"""

import logging

import numpy as np

from . import clientes_importados
from .risk_scorer import (HIGH_RISK_THRESHOLD, PISO_ABSOLUTO_DIAS_SEM_LOGIN,
                          classify_criticality, desengajamento_de_uso,
                          is_critical_risk, modelo_ativo, posicao_na_base,
                          risco_por_features, risco_por_posicao,
                          sinal_absoluto_de_desengajamento)

logger = logging.getLogger(__name__)

CRITICIDADE_SEM_DADO = "dado_insuficiente"
TEXTO_SEM_DADO = ("sem dado de atividade — impossível avaliar risco de churn "
                  "para este cliente")

# Ordem de exibição da criticidade quando o risco empata (e para o frontend
# filtrar por "mínima"): maior primeiro.
ORDEM_CRITICIDADE = {"critico": 3, "alto": 2, "padrao": 1, CRITICIDADE_SEM_DADO: 0}

# ── Idioma da explicacao ─────────────────────────────────────────────────
# A frase de `explicar()` e produzida AQUI, nao na tela: e o sistema dizendo
# por que classificou assim. O painel e bilingue, entao ela precisa existir
# nos dois idiomas -- e precisa continuar vindo do backend, senao a tela
# passaria a mostrar o que ela acha que o sistema quis dizer.
#
# O default e "pt" em toda a cadeia: sem ninguem pedir ingles, cada byte da
# saida e identico ao de antes deste parametro existir.
IDIOMAS = ("pt", "en")


def _idioma(idioma) -> str:
    return idioma if idioma in IDIOMAS else "pt"


FRASES = {
    "sem_dado": {
        "pt": TEXTO_SEM_DADO,
        "en": ("no activity data - impossible to assess churn risk for this "
               "customer"),
    },
    "inatividade": {
        "pt": ("acima de 90% da sua base", "acima de 75% da sua base",
               "acima da metade da sua base", "dentro do normal da sua base"),
        "en": ("higher than 90% of your base", "higher than 75% of your base",
               "higher than half of your base", "within the normal range of your base"),
    },
    "uso": {
        "pt": ("menos que 90% da sua base", "menos que 75% da sua base",
               "menos que a metade da sua base", "dentro do normal da sua base"),
        "en": ("lower than 90% of your base", "lower than 75% of your base",
               "lower than half of your base", "within the normal range of your base"),
    },
    "acessou_hoje":   {"pt": "acessou hoje", "en": "logged in today"},
    "sem_login":      {"pt": "sem login há {d} dia{s}", "en": "no login for {d} day{s}"},
    "dias_desc":      {"pt": "dias sem login desconhecidos (assumido 0)",
                       "en": "days without login unknown (assumed 0)"},
    "uso_n":          {"pt": "usa {u} funcionalidade{s} nos últimos 30 dias",
                       "en": "uses {u} feature{s} in the last 30 days"},
    "uso_desc_regua": {"pt": "uso de funcionalidades desconhecido (assumido como os mais ativos da sua base)",
                       "en": "feature usage unknown (assumed to be among the most active in your base)"},
    "uso_desc":       {"pt": "uso de funcionalidades desconhecido (assumido 10)",
                       "en": "feature usage unknown (assumed 10)"},
    "mrr":            {"pt": "MRR {v}", "en": "MRR {v}"},
    "mrr_desc":       {"pt": "MRR desconhecido", "en": "MRR unknown"},
    "critico_valor":  {"pt": " — crítico pelo valor da conta, não pelo risco",
                       "en": " - critical because of the account value, not the risk"},
    "sem_sinal":      {"pt": " — primeiro da fila da sua base, mas sem sinal de abandono: ",
                       "en": " - first in line in your base, but with no sign of churn: "},
    "entrou_ha":      {"pt": "entrou há menos de {n} dias",
                       "en": "logged in less than {n} days ago"},
    "usa_produto":    {"pt": "usa o produto", "en": "uses the product"},
    "e":              {"pt": " e ", "en": " and "},
}


def _f(chave: str, idioma: str) -> str:
    return FRASES[chave][_idioma(idioma)]



# ── Régua da base ────────────────────────────────────────────────────────
REGUA_BASE = "base_do_tenant"
REGUA_GLOBAL = "padrao_global"
CAMPOS_DA_REGUA = ("days_since_last", "features_used_30d")
# Cada coluna olha para a cauda onde o sinal mora: em dias sem login, MAIS é
# pior (cauda alta); em funcionalidades usadas, MENOS é pior (cauda baixa).
PERCENTIS_DA_REGUA = {
    "days_since_last":   {"p50": 50, "p75": 75, "p90": 90},
    "features_used_30d": {"p10": 10, "p25": 25, "p50": 50},
}
# O percentil que precisa ser > 0 para haver distribuição: se 90% da base
# tem 0 dias sem login, ou se metade da base usa 0 funcionalidades, não há
# posição a medir naquela coluna.
_PERCENTIL_DE_REFERENCIA = {"days_since_last": "p90", "features_used_30d": "p50"}
MINIMO_LINHAS_REGUA_DA_BASE = 30


def regua_da_base(base: list[dict]):
    """Os percentis da base: p50/p75/p90 de `days_since_last` e p10/p25/p50 de
    `features_used_30d`.

    Nulos são ignorados, e `n` diz quantas linhas entraram no cálculo de cada
    coluna. Devolve None — "use a régua global" — quando a base não sustenta
    uma régua própria: menos de `MINIMO_LINHAS_REGUA_DA_BASE` valores em
    qualquer das duas colunas, ou percentil de referência zero (a base
    concentrada num ponto só, sem distribuição para se comparar).
    """
    regua = {}
    for campo in CAMPOS_DA_REGUA:
        valores = []
        for c in base:
            v = c.get(campo)
            if v is None or isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            v = float(v)
            if np.isfinite(v) and v >= 0:
                valores.append(v)
        if len(valores) < MINIMO_LINHAS_REGUA_DA_BASE:
            return None
        percentis = PERCENTIS_DA_REGUA[campo]
        p = np.percentile(valores, list(percentis.values()))
        resumo = {chave: round(float(x), 3) for chave, x in zip(percentis, p)}
        if resumo[_PERCENTIL_DE_REFERENCIA[campo]] <= 0:
            return None
        resumo["n"] = len(valores)
        regua[campo] = resumo
    regua["n_linhas"] = len(base)
    regua["n_utilizaveis"] = sum(
        1 for c in base if any(c.get(campo) is not None for campo in CAMPOS_DA_REGUA))
    return regua


def _frase_de_inatividade(pos: float, idioma: str = "pt") -> str:
    """Como o dono do SaaS deve ler a posição de 'dias sem login' na base dele.

    Comparações ESTRITAS: quem está exatamente no p90 ganha a frase do p75.
    Com empates no percentil, "acima de 90%" seria a afirmação mais forte do
    que os dados sustentam; a frase mais fraca é sempre verdadeira.
    """
    faixas = FRASES["inatividade"][_idioma(idioma)]
    if pos > 0.9:
        return faixas[0]
    if pos > 0.75:
        return faixas[1]
    if pos > 0.5:
        return faixas[2]
    return faixas[3]


def _frase_de_uso(desengajamento: float, idioma: str = "pt") -> str:
    """Idem para 'funcionalidades usadas': aqui, estar embaixo é o sinal.
    `desengajamento` vem de `desengajamento_de_uso`: 0,9 é o p10 da base."""
    faixas = FRASES["uso"][_idioma(idioma)]
    if desengajamento > 0.9:
        return faixas[0]
    if desengajamento > 0.75:
        return faixas[1]
    if desengajamento > 0.5:
        return faixas[2]
    return faixas[3]


def _frase_sem_sinal_absoluto(dias, uso, idioma: str = "pt") -> str:
    """Por que um cliente no topo da lista NÃO é alarme: o que se sabe dele
    está dentro do que é uso normal em escala absoluta."""
    motivos = []
    if dias is not None:
        motivos.append(_f("entrou_ha", idioma).format(n=int(PISO_ABSOLUTO_DIAS_SEM_LOGIN)))
    if uso is not None:
        motivos.append(_f("usa_produto", idioma))
    return _f("sem_sinal", idioma) + _f("e", idioma).join(motivos)


def _reais(valor, idioma: str = "pt") -> str:
    """1500.5 → 'R$ 1.500,50'. Formato pt-BR sem depender de locale do SO."""
    try:
        s = f"{float(valor):,.2f}"
    except (TypeError, ValueError):
        return _f("mrr_desc", idioma)
    if _idioma(idioma) == "en":
        return "R$ " + s
    return "R$ " + s.replace(",", "|").replace(".", ",").replace("|", ".")


def _inteiro_se_der(valor):
    """47.0 → 47 no texto; 2.5 fica 2.5."""
    f = float(valor)
    return int(f) if f.is_integer() else f


def explicar(cliente: dict, risk_score, criticality: str, regua: dict | None = None,
             idioma: str = "pt") -> str:
    """Uma frase curta, em PT-BR, com os números que produziram o risco.

    É a explicabilidade do caminho (2): as próprias features, ditas. Não há
    SHAP aqui porque não há modelo treinado hoje; quando houver, o texto
    continua verdadeiro — são as entradas, não os pesos.

    Com `regua` (a da base, de `regua_da_base`), cada número vem seguido da
    posição dele na base do próprio cliente — "sem login há 47 dias — acima de
    90% da sua base". Sem `regua`, é a frase de sempre, da régua global.

    Ainda com `regua`: quando o cliente está no topo da lista (risco >= 0,75)
    mas NÃO cruzou o piso absoluto e ficou "padrao", a frase diz o porquê —
    é a resposta a "então por que ele está em primeiro e não é alarme?".
    """
    if criticality == CRITICIDADE_SEM_DADO:
        return _f("sem_dado", idioma)

    partes = []
    dias = cliente.get("days_since_last")
    uso = cliente.get("features_used_30d")
    if dias is not None:
        d = _inteiro_se_der(dias)
        frase = (_f("acessou_hoje", idioma) if d == 0
                 else _f("sem_login", idioma).format(d=d, s="s" if d != 1 else ""))
        if regua is not None and d != 0:
            frase += " — " + _frase_de_inatividade(
                posicao_na_base(dias, regua["days_since_last"]), idioma)
        partes.append(frase)
    else:
        partes.append(_f("dias_desc", idioma))
    if uso is not None:
        u = _inteiro_se_der(uso)
        frase = _f("uso_n", idioma).format(u=u, s="s" if u != 1 else "")
        if regua is not None:
            frase += " — " + _frase_de_uso(
                desengajamento_de_uso(uso, regua["features_used_30d"]), idioma)
        partes.append(frase)
    elif regua is not None:
        partes.append(_f("uso_desc_regua", idioma))
    else:
        partes.append(_f("uso_desc", idioma))
    partes.append(_f("mrr", idioma).format(v=_reais(cliente.get("mrr"), idioma)))

    texto = ", ".join(partes)
    if risk_score is None:
        return texto

    # Na régua da base o risco crítico só conta com sinal absoluto; na global
    # o risco já é absoluto. `risco_critico_valido` é "crítico POR RISCO".
    com_sinal = regua is None or sinal_absoluto_de_desengajamento(dias, uso)
    risco_critico_valido = is_critical_risk(risk_score) and com_sinal
    if criticality == "critico" and not risco_critico_valido:
        texto += _f("critico_valor", idioma)
    elif (regua is not None and criticality == "padrao"
          and risk_score >= HIGH_RISK_THRESHOLD and not com_sinal):
        texto += _frase_sem_sinal_absoluto(dias, uso, idioma)
    return texto


def pontuar_cliente(cliente: dict, regua: dict | None = None,
                    idioma: str = "pt") -> dict:
    """Uma linha da base → uma linha do ranking. Não grava nada.

    Sem `regua` (o default, e o caso de toda base pequena), o número é o de
    sempre: `risco_por_features`, régua global. Com `regua`, o risco é pela
    posição na base — a não ser que haja modelo treinado ativo, que decide
    antes de qualquer régua, como já decidia.
    """
    dias = cliente.get("days_since_last")
    uso = cliente.get("features_used_30d")
    mrr = cliente.get("mrr")

    usar_regua_da_base = regua is not None and not modelo_ativo()
    origem_da_regua = REGUA_BASE if usar_regua_da_base else REGUA_GLOBAL
    regua_da_explicacao = regua if usar_regua_da_base else None

    if dias is None and uso is None:
        risk, crit = None, CRITICIDADE_SEM_DADO
    elif usar_regua_da_base:
        # POSIÇÃO ordena; CRITICIDADE exige também o sinal absoluto — por isso
        # os valores crus vão junto para `classify_criticality`.
        risk = risco_por_posicao(dias, uso, regua)
        crit = classify_criticality(risk, mrr, dias, uso)
    else:
        risk = risco_por_features(dias, uso, mrr)
        crit = classify_criticality(risk, mrr)

    return {
        "customer_id_externo": cliente["customer_id_externo"],
        "risk_score": risk,
        "criticality": crit,
        "explicacao": explicar(cliente, risk, crit, regua_da_explicacao, idioma),
        "mrr": mrr,
        "billing_profile": cliente.get("billing_profile"),
        "days_since_last": dias,
        "features_used_30d": uso,
        "email": cliente.get("email"),
        "importado_em": cliente.get("importado_em"),
        "origem_da_regua": origem_da_regua,
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


def pontuar_base(tenant_id: str, idioma: str = "pt") -> list[dict]:
    """O ranking de risco da base importada DESTE tenant.

    Lê só o tenant pedido — `clientes_importados.listar` não tem leitura sem
    filtro, de propósito. Devolve lista ordenada (ver `ordenar`).
    """
    base = clientes_importados.listar(tenant_id)
    regua = regua_da_base(base)
    ranking = ordenar([pontuar_cliente(c, regua, idioma) for c in base])
    sem_dado = sum(1 for l in ranking if l["criticality"] == CRITICIDADE_SEM_DADO)
    logger.info("[BATCH-SCORING] tenant=%s clientes=%d sem_dado=%d regua=%s",
                tenant_id, len(ranking), sem_dado,
                REGUA_GLOBAL if regua is None else
                f"{REGUA_BASE} (dias p50/p75/p90={regua['days_since_last']['p50']}/"
                f"{regua['days_since_last']['p75']}/{regua['days_since_last']['p90']}, "
                f"uso p10/p25/p50={regua['features_used_30d']['p10']}/"
                f"{regua['features_used_30d']['p25']}/{regua['features_used_30d']['p50']}, "
                f"n={regua['n_utilizaveis']})")
    return ranking

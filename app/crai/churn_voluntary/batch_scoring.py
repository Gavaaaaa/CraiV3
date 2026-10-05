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
from datetime import datetime, timezone

import numpy as np

from . import clientes_importados
from . import risk_scorer as _rs
from .risk_scorer import (EVENTO_DADO_ESTATICO, HIGH_RISK_THRESHOLD,
                          PISO_ABSOLUTO_DIAS_SEM_LOGIN, classify_criticality,
                          colunas_comportamentais_v3, decidir_risco,
                          desengajamento_de_uso, is_critical_risk,
                          modelo_ativo, mrr_utilizavel,
                          posicao_na_base, risco_por_features, risco_por_posicao,
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
    "dias_desc_modelo": {"pt": "dias sem login desconhecidos",
                         "en": "days without login unknown"},
    "uso_desc_modelo":  {"pt": "uso de funcionalidades desconhecido",
                         "en": "feature usage unknown"},
    "pos_grave":      {"pt": "entre os {x}% de maior risco da sua base, pelo modelo",
                       "en": "among the {x}% highest-risk customers in your base, by the model"},
    "pos_preocupante": {"pt": "entre os {x}% de maior risco da sua base, pelo modelo",
                        "en": "among the {x}% highest-risk customers in your base, by the model"},
    "pos_fora":       {"pt": "fora dos {x}% de maior risco da sua base, pelo modelo",
                       "en": "outside the {x}% highest-risk customers in your base, by the model"},
    "pos_sem_ref":    {"pt": "risco pelo modelo, sem base de comparação para posicionar",
                       "en": "risk by the model, with no base to compare against"},
    "promovido_valor": {"pt": " — grave pelo valor da conta: tem risco e o MRR está entre os {x}% maiores da sua base",
                        "en": " - serious because of the account value: at risk and the MRR is among the top {x}% of your base"},
    # Rodada 4: o motivo de quem veio por evento diz quem decidiu de verdade.
    "intencao_explicita": {"pt": " — o sistema agiu por intenção explícita do cliente (o evento), e não pela pontuação de risco",
                           "en": " - the system acted on the customer's explicit intent (the event), not on the risk score"},
}


def _f(chave: str, idioma: str) -> str:
    return FRASES[chave][_idioma(idioma)]



# ── Régua da base ────────────────────────────────────────────────────────
REGUA_BASE = "base_do_tenant"
REGUA_GLOBAL = "padrao_global"

# A VERSÃO DO ESQUEMA DE CÁLCULO da régua, que a API de clientes devolve em
# `regua_versao`. Hoje é "lote": a régua é recalculada INTEIRA a cada
# `pontuar_base` (isto é, a cada `GET /insights`), a partir de `listar()`, e
# não é persistida em lugar nenhum. Quando a régua incremental entrar, esta
# constante muda e o consumidor sabe que o contrato de defasagem mudou.
REGUA_VERSAO = "lote-v1"

# Quando foi o último cálculo em lote, por tenant, NESTE processo. É a única
# memória que existe disso: a régua não é gravada, então "quando foi
# calculada" só pode ser "quando `pontuar_base` rodou pela última vez". Zera
# no reinício — `None` significa "ninguém pediu o ranking desde que o
# processo subiu", não "nunca". Mesma classe de limitação da janela de
# idempotência (`api/idempotencia.py`), pelo mesmo motivo.
_ultimo_calculo_da_regua: dict = {}
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
        # Com modelo ativo, as colunas comportamentais da linha vão junto
        # (promoção v3, Bloco 3); sem modelo a régua não as lê, e não se
        # importa o contrato v3 à toa.
        comportamentais = _comportamentais_da_linha(cliente) if modelo_ativo() else None
        risk = risco_por_features(dias, uso, mrr, comportamentais=comportamentais)
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


def esquecer_calculos_da_regua() -> None:
    """Zera a marca de todos os tenants. Existe para o teste (`conftest`):
    um teste que pediu o ranking não pode deixar `regua_calculada_em`
    preenchido para o teste seguinte. Zera também a referência do score do
    modelo por tenant (promoção v3, Bloco 2), pelo mesmo motivo."""
    global _referencias_lidas_de
    _ultimo_calculo_da_regua.clear()
    _referencia_do_score.clear()
    _referencia_do_mrr.clear()
    _referencias_lidas_de = None          # o próximo que consultar relê o disco (Rodada 4)
    # O cache da régua do dashboard (Rodada 3) guarda um resultado deste módulo:
    # some junto. Import aqui dentro: `insights_unificados` importa este módulo.
    from . import insights_unificados
    insights_unificados.esquecer_cache()


def ultimo_calculo_da_regua(tenant_id: str):
    """ISO-8601 UTC do último `pontuar_base` deste tenant neste processo, ou
    `None` se ainda não houve um. Ver `_ultimo_calculo_da_regua`."""
    return _ultimo_calculo_da_regua.get(tenant_id)


def pontuar_base(tenant_id: str, idioma: str = "pt") -> list[dict]:
    """O ranking de risco da base importada DESTE tenant.

    Lê só o tenant pedido — `clientes_importados.listar` não tem leitura sem
    filtro, de propósito. Devolve lista ordenada (ver `ordenar`).
    """
    base = clientes_importados.listar(tenant_id)
    regua = regua_da_base(base)
    _ultimo_calculo_da_regua[tenant_id] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    ranking = ordenar(pontuar_lista(base, idioma, tenant_id=tenant_id, regua=regua))
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


# ══════════════════════════════════════════════════════════════════════════
# COM O MODELO V3 ATIVO: o risco vira ação pela POSIÇÃO (promoção v3, Bloco 2)
# ══════════════════════════════════════════════════════════════════════════
#
# Com o modelo decidindo, o risco é uma probabilidade calibrada numa base com
# taxa de cancelamento de ~17%: os cortes fixos de `classify_criticality`
# (0,75 alto, 0,90 crítico) quase nunca seriam cruzados. A criticidade passa
# a sair da POSIÇÃO do cliente na base da empresa (P2):
#
#   grave (critico)       — os FAIXA_GRAVE_PCT% de maior score
#   preocupante (alto)    — os FAIXA_PREOCUPANTE_PCT% seguintes
#   sem risco (padrao)    — o resto
#
# SEMPRE com o sinal absoluto de desengajamento, como a régua da base já faz:
# posição é relativa por construção, e uma base inteira saudável não pode ter
# 10% de graves.
#
# A PORTA DE VALOR, no caminho do v3 (D-B2-3, 30/09/2026), não marca ninguém
# como grave sozinha: ela só PROMOVE de preocupante para grave um cliente que
# já tem risco, e o limiar é relativo à base da empresa — MRR entre os
# FAIXA_VALOR_PCT% maiores da própria lista —, não R$ 2.000 fixos. O caminho
# legado (régua e modelo v2) continua com `classify_criticality` e o limiar de
# `limiar_de_alto_valor()` (variável de ambiente), idêntico.
#
# D5: quem o modelo decidiu e quem a régua decidiu (modelo falhou) têm posição
# calculada SEPARADA — probabilidade e risco de régua não estão na mesma escala.
#
# Referência da posição: a própria lista, se tiver pelo menos
# MINIMO_LINHAS_POSICAO linhas daquele grupo; senão, para o grupo do modelo,
# os quantis do score no holdout de treino gravados no meta de produção pela
# promoção (`referencia_score_quantis`); sem nenhuma das duas, ninguém é
# grave nem preocupante e a explicação diz que não houve base de comparação.
# A referência do MRR é a própria lista, com o mesmo mínimo de linhas; sem ela
# (lista pequena, ou SDK sem `pontuar_base` do tenant), ninguém é promovido.
# O grupo da régua sem referência cai em `classify_criticality`, como hoje.
#
# O contrato de saída não muda de forma: `origem_da_regua` continua sendo
# `base_do_tenant` (posição contra a própria base) ou `padrao_global`
# (referência do treino, ou nenhuma). Entram dois campos novos:
# `risco_decidido_por` ("modelo" | "regra") e `posicao_na_base` (0..1 ou None).

FAIXA_GRAVE_PCT = 10
FAIXA_PREOCUPANTE_PCT = 20
FAIXA_VALOR_PCT = 20
MINIMO_LINHAS_POSICAO = MINIMO_LINHAS_REGUA_DA_BASE
DECIDIDO_MODELO = "modelo"
DECIDIDO_REGRA = "regra"

# A referência do score do modelo por tenant, da última `pontuar_base` — o que
# o caminho do SDK usa para posicionar um evento sozinho (D4). Desde a Rodada 4
# são os QUANTIS do score (p0 a p100), e não a lista inteira, e ficam também em
# disco (`caminho_das_referencias`): sobrevivem ao reinício do serviço.
_referencia_do_score: dict = {}
# A referência do MRR por tenant (também em quantis), da mesma `pontuar_base`,
# para a promoção pelo valor no SDK.
_referencia_do_mrr: dict = {}

# ── A referência de posição em disco (Rodada 4) ──────────────────────────
#
# Antes, a referência morava só na memória do processo: depois de um reinício,
# todo evento era posicionado contra a referência de TREINO do modelo até
# alguém pedir o ranking da empresa de novo. Agora cada `pontuar_base` grava,
# por empresa, os quantis do score e do MRR num arquivo JSON, e o processo os
# lê na subida (e na primeira consulta, se ainda não leu).
#
# O QUE VAI PARA O DISCO: 101 números do score, 101 do MRR, quantos clientes
# entraram na conta e quando. Nenhum identificador, nenhum dado de cliente: são
# estatísticas da base (o p0 e o p100 são o menor e o maior valor, sem dizer de
# quem). O arquivo fica ao lado do banco de ciclos de retenção
# (`CRAI_RETENTION_DB`), ou onde `CRAI_REFERENCIAS_POSICAO` mandar.
#
# A REFERÊNCIA SEGUE A BASE: se a base da empresa deixa de ter o mínimo de
# clientes com dado, a referência dela é apagada (memória e disco), e os
# eventos voltam a ser posicionados contra a referência de treino.
ENV_REFERENCIAS = "CRAI_REFERENCIAS_POSICAO"
ARQUIVO_DAS_REFERENCIAS = "referencias_de_posicao.json"
N_QUANTIS_DA_REFERENCIA = 101
# De que arquivo a memória foi lida (None: ainda não leu neste processo).
_referencias_lidas_de = None


def caminho_das_referencias():
    """Onde ficam as referências de posição por empresa."""
    import os
    from pathlib import Path

    from . import retention_log

    explicito = os.getenv(ENV_REFERENCIAS, "").strip()
    if explicito:
        return Path(explicito)
    return Path(retention_log.caminho_do_banco()).parent / ARQUIVO_DAS_REFERENCIAS


def quantis_da_referencia(valores) -> list:
    """Os N_QUANTIS_DA_REFERENCIA quantis (p0 a p100) de uma lista de números,
    em ordem crescente. É o que se guarda: a distribuição, não os valores."""
    ordenados = np.sort(np.asarray(list(valores), dtype=float))
    quantis = np.quantile(ordenados, np.linspace(0.0, 1.0, N_QUANTIS_DA_REFERENCIA))
    return [round(float(q), 6) for q in quantis]


def _ler_arquivo_das_referencias(caminho) -> dict:
    import json

    try:
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:                        # noqa: BLE001 - arquivo torto não derruba
        logger.warning("[REFERENCIAS] %s ilegível (%r): as referências de posição em "
                       "disco foram ignoradas.", caminho, e)
        return {}
    return dados if isinstance(dados, dict) else {}


def _lista_de_numeros(bruto):
    if not isinstance(bruto, list) or len(bruto) < 2:
        return None
    try:
        return sorted(float(v) for v in bruto)
    except (TypeError, ValueError):
        return None


def carregar_referencias(forcar: bool = False) -> int:
    """Lê do disco as referências de posição de todas as empresas, uma vez
    por processo e por arquivo (`forcar` relê). Chamada na subida do serviço e,
    por garantia, antes de cada consulta. O que já está na memória (uma
    `pontuar_base` deste processo) vence o disco. Devolve quantas empresas
    têm referência. Nunca levanta."""
    global _referencias_lidas_de
    caminho = caminho_das_referencias()
    if not forcar and _referencias_lidas_de == str(caminho):
        return len(_referencia_do_score)
    _referencias_lidas_de = str(caminho)
    for tenant_id, ref in _ler_arquivo_das_referencias(caminho).items():
        if not isinstance(ref, dict):
            continue
        score, mrr = _lista_de_numeros(ref.get("score")), _lista_de_numeros(ref.get("mrr"))
        if score and tenant_id not in _referencia_do_score:
            _referencia_do_score[tenant_id] = score
        if mrr and tenant_id not in _referencia_do_mrr:
            _referencia_do_mrr[tenant_id] = mrr
    return len(_referencia_do_score)


def _guardar_referencias(tenant_id: str, scores, mrrs) -> None:
    """A referência DESTA empresa passa a ser a desta base: os quantis do
    score do modelo e do MRR, ou nada, se a base não tem o mínimo. Grava em
    disco só quando mudou. Falha de disco não derruba o ranking: a memória
    fica certa e o log diz."""
    import json
    import os

    carregar_referencias()
    score = quantis_da_referencia(scores) if scores is not None else None
    mrr = quantis_da_referencia(mrrs) if mrrs is not None else None
    if _referencia_do_score.get(tenant_id) == score and _referencia_do_mrr.get(tenant_id) == mrr:
        return
    for memoria, valor in ((_referencia_do_score, score), (_referencia_do_mrr, mrr)):
        if valor is None:
            memoria.pop(tenant_id, None)
        else:
            memoria[tenant_id] = valor
    caminho = caminho_das_referencias()
    try:
        dados = _ler_arquivo_das_referencias(caminho)
        if score is None and mrr is None:
            dados.pop(tenant_id, None)
        else:
            dados[tenant_id] = {
                "score": score, "mrr": mrr,
                "clientes_com_score": None if scores is None else len(scores),
                "clientes_com_mrr": None if mrrs is None else len(mrrs),
                "atualizada_em": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = caminho.with_name(caminho.name + f".{os.getpid()}.tmp")
        with open(temporario, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=True, sort_keys=True)
        os.replace(temporario, caminho)
    except Exception as e:                        # noqa: BLE001
        logger.warning("[REFERENCIAS] não foi possível gravar %s (%r): a referência de "
                       "posição de %s vale só até o próximo reinício.", caminho, e, tenant_id)


def posicao_pelo_modelo_ativa() -> bool:
    """A posição pelo score vale para o modelo de contrato v3.

    O modelo legado (o candidato v2) foi treinado no rótulo da própria régua e
    reproduz a escala absoluta dela (correlação 0,9974 com `risk_regra`): nele
    os cortes fixos de `classify_criticality` continuam fazendo sentido, e o
    comportamento de antes fica como estava."""
    return modelo_ativo() and _rs.contrato_ativo() == _rs.CONTRATO_V3


def faixas_de_posicao(tenant_id: str | None = None) -> tuple:
    """(X, Y) em pontos percentuais. A `configuracao_tenant` é criada pela
    Etapa 2 e ainda não existe; enquanto isso, o padrão em código."""
    return FAIXA_GRAVE_PCT, FAIXA_PREOCUPANTE_PCT


def posicao_no_score(score: float, referencia) -> float:
    """Fração da referência ESTRITAMENTE abaixo de `score` (0..1).

    Estrita de propósito: com empate, o cliente fica com a posição mais baixa
    do empate — a afirmação mais fraca, como `_interpolar` faz na régua."""
    ref = np.asarray(referencia, dtype=float)
    if ref.size == 0:
        return 0.0
    return float(np.searchsorted(ref, float(score), side="left") / ref.size)


def mrr_no_topo(posicao_mrr: float | None) -> bool:
    """O MRR está entre os FAIXA_VALOR_PCT% maiores da base?"""
    return posicao_mrr is not None and posicao_mrr >= 1 - FAIXA_VALOR_PCT / 100


def criticidade_por_posicao(posicao: float | None, dias, uso,
                            faixas: tuple = (FAIXA_GRAVE_PCT, FAIXA_PREOCUPANTE_PCT),
                            posicao_mrr: float | None = None) -> str:
    """grave/preocupante pela posição do score, SEMPRE com sinal absoluto.

    A porta de valor só promove preocupante a grave, e só com o MRR no topo
    FAIXA_VALOR_PCT% da base (`posicao_mrr`). Sem posição (sem referência):
    ninguém é grave nem preocupante."""
    grave_pct, preocupante_pct = faixas
    if posicao is None or not sinal_absoluto_de_desengajamento(dias, uso):
        return "padrao"
    if posicao >= 1 - grave_pct / 100:
        return "critico"
    if posicao >= 1 - (grave_pct + preocupante_pct) / 100:
        return "critico" if mrr_no_topo(posicao_mrr) else "alto"
    return "padrao"


def foi_promovido_pelo_valor(posicao: float | None, dias, uso, faixas: tuple,
                             posicao_mrr: float | None) -> bool:
    """Grave por causa da porta de valor (e não da posição do score)?"""
    grave_pct, _ = faixas
    return (criticidade_por_posicao(posicao, dias, uso, faixas, posicao_mrr) == "critico"
            and posicao < 1 - grave_pct / 100)


def referencia_de_mrr(clientes: list[dict]):
    """Os MRR da lista, ordenados, se houver pelo menos MINIMO_LINHAS_POSICAO."""
    valores = sorted(v for v in (mrr_utilizavel(c.get("mrr")) for c in clientes)
                     if v is not None)
    return valores if len(valores) >= MINIMO_LINHAS_POSICAO else None


def _posicao_do_mrr(mrr, referencia) -> float | None:
    valor = mrr_utilizavel(mrr)
    if referencia is None or valor is None:
        return None
    return posicao_no_score(valor, referencia)


def referencia_do_meta():
    """Os quantis do score no holdout de treino, do meta de produção, ou None."""
    import json

    try:
        with open(_rs.MODELO_META_PATH, encoding="utf-8") as f:
            quantis = json.load(f).get("referencia_score_quantis")
    except Exception:                             # noqa: BLE001 - sem meta, sem referência
        return None
    if not isinstance(quantis, list) or len(quantis) < 2:
        return None
    try:
        return sorted(float(q) for q in quantis)
    except (TypeError, ValueError):
        return None


def referencia_para_evento(tenant_id: str | None) -> tuple:
    """(referência, origem) para posicionar UM evento do SDK (D4)."""
    carregar_referencias()
    ref = _referencia_do_score.get(tenant_id)
    if ref:
        return ref, REGUA_BASE
    ref = referencia_do_meta()
    return (ref, REGUA_GLOBAL) if ref else (None, REGUA_GLOBAL)


def _comportamentais_da_linha(cliente: dict) -> dict:
    """As colunas comportamentais do contrato v3 presentes na linha."""
    return {c: cliente.get(c) for c in colunas_comportamentais_v3()
            if cliente.get(c) is not None}


def _props_do_cliente(cliente: dict) -> dict:
    """As props do risco, sem chave com None (a régua não aceita None)."""
    campos = ("days_since_last", "features_used_30d", "mrr", *colunas_comportamentais_v3())
    return {c: cliente.get(c) for c in campos if cliente.get(c) is not None}


def _explicar_pelo_modelo(cliente: dict, posicao, criticality: str, faixas: tuple,
                          idioma: str = "pt", promovido: bool = False) -> str:
    """A frase da linha que o modelo posicionou: os números, a posição e, se
    for o caso, por que o topo da lista não virou alarme."""
    dias, uso = cliente.get("days_since_last"), cliente.get("features_used_30d")
    partes = []
    if dias is not None:
        d = _inteiro_se_der(dias)
        partes.append(_f("acessou_hoje", idioma) if d == 0
                      else _f("sem_login", idioma).format(d=d, s="s" if d != 1 else ""))
    else:
        partes.append(_f("dias_desc_modelo", idioma))
    if uso is not None:
        u = _inteiro_se_der(uso)
        partes.append(_f("uso_n", idioma).format(u=u, s="s" if u != 1 else ""))
    else:
        partes.append(_f("uso_desc_modelo", idioma))
    partes.append(_f("mrr", idioma).format(v=_reais(cliente.get("mrr"), idioma)))
    texto = ", ".join(partes)

    grave_pct, preocupante_pct = faixas
    if posicao is None:
        return texto + " — " + _f("pos_sem_ref", idioma)
    no_topo = posicao >= 1 - (grave_pct + preocupante_pct) / 100
    if posicao >= 1 - grave_pct / 100:
        texto += " — " + _f("pos_grave", idioma).format(x=_inteiro_se_der(grave_pct))
    elif no_topo:
        texto += " — " + _f("pos_preocupante", idioma).format(
            x=_inteiro_se_der(grave_pct + preocupante_pct))
    else:
        texto += " — " + _f("pos_fora", idioma).format(
            x=_inteiro_se_der(grave_pct + preocupante_pct))
    if promovido:
        return texto + _f("promovido_valor", idioma).format(x=_inteiro_se_der(FAIXA_VALOR_PCT))
    if no_topo and not sinal_absoluto_de_desengajamento(dias, uso):
        return texto + _frase_sem_sinal_absoluto(dias, uso, idioma)
    return texto


def pontuar_lista(clientes: list[dict], idioma: str = "pt", tenant_id: str | None = None,
                  regua: dict | None = None) -> list[dict]:
    """Uma linha do ranking por cliente, NA ORDEM de entrada (quem ordena é
    `ordenar`).

    Sem modelo v3 ativo: exatamente o de antes — `pontuar_cliente` com a régua
    da lista (`regua`, ou `regua_da_base(clientes)`); com o modelo legado
    ativo, também o de antes (ver `posicao_pelo_modelo_ativa`).

    Com o modelo v3 ativo: risco por `decidir_risco` e criticidade pela posição
    (ver o bloco acima). Com `tenant_id` (a base importada do tenant), a
    referência do score dos clientes que o modelo decidiu é guardada para o
    SDK daquele tenant.
    """
    if not posicao_pelo_modelo_ativa():
        if regua is None:
            regua = regua_da_base(clientes)
        return [pontuar_cliente(c, regua, idioma) for c in clientes]

    faixas = faixas_de_posicao(tenant_id)
    decisoes = []
    for c in clientes:
        if c.get("days_since_last") is None and c.get("features_used_30d") is None:
            decisoes.append(None)
            continue
        risco, por_modelo, _ = decidir_risco(EVENTO_DADO_ESTATICO, _props_do_cliente(c))
        decisoes.append((risco, por_modelo))

    ref_mrr = referencia_de_mrr(clientes)

    referencias = {}
    scores_do_modelo = None
    for por_modelo in (True, False):
        scores = sorted(d[0] for d in decisoes if d is not None and d[1] == por_modelo)
        if len(scores) >= MINIMO_LINHAS_POSICAO:
            referencias[por_modelo] = (scores, REGUA_BASE)
            if por_modelo:
                scores_do_modelo = scores
        elif por_modelo:
            ref = referencia_do_meta()
            referencias[por_modelo] = (ref, REGUA_GLOBAL)
        else:
            referencias[por_modelo] = (None, REGUA_GLOBAL)
    if tenant_id is not None:
        # A referência do SDK desta empresa (memória e disco) é a desta base.
        _guardar_referencias(tenant_id, scores_do_modelo, ref_mrr)

    linhas = []
    for c, d in zip(clientes, decisoes):
        dias, uso, mrr = c.get("days_since_last"), c.get("features_used_30d"), c.get("mrr")
        linha = {
            "customer_id_externo": c["customer_id_externo"],
            "mrr": mrr,
            "billing_profile": c.get("billing_profile"),
            "days_since_last": dias,
            "features_used_30d": uso,
            "email": c.get("email"),
            "importado_em": c.get("importado_em"),
            # As colunas comportamentais presentes seguem na linha: o disparo
            # recalcula o risco e o SHAP da trilha a partir dela.
            **_comportamentais_da_linha(c),
        }
        if d is None:
            linhas.append({**linha, "risk_score": None, "criticality": CRITICIDADE_SEM_DADO,
                           "explicacao": explicar(c, None, CRITICIDADE_SEM_DADO, None, idioma),
                           "origem_da_regua": REGUA_GLOBAL, "risco_decidido_por": None,
                           "posicao_na_base": None})
            continue
        risco, por_modelo = d
        ref, origem = referencias[por_modelo]
        pos_mrr = _posicao_do_mrr(mrr, ref_mrr)
        if por_modelo:
            pos = posicao_no_score(risco, ref) if ref else None
            crit = criticidade_por_posicao(pos, dias, uso, faixas, pos_mrr)
            explicacao = _explicar_pelo_modelo(
                c, pos, crit, faixas, idioma,
                promovido=foi_promovido_pelo_valor(pos, dias, uso, faixas, pos_mrr))
        elif ref:
            pos = posicao_no_score(risco, ref)
            crit = criticidade_por_posicao(pos, dias, uso, faixas, pos_mrr)
            explicacao = explicar(c, risco, crit, None, idioma)
        else:
            pos = None
            crit = classify_criticality(risco, mrr)
            explicacao = explicar(c, risco, crit, None, idioma)
        linhas.append({**linha, "risk_score": risco, "criticality": crit,
                       "explicacao": explicacao, "origem_da_regua": origem,
                       "risco_decidido_por": DECIDIDO_MODELO if por_modelo else DECIDIDO_REGRA,
                       "posicao_na_base": None if pos is None else round(pos, 4)})
    return linhas


def criticidade_do_evento(tenant_id: str | None, risco: float, mrr, dias, uso,
                          decidido_pelo_modelo: bool) -> dict:
    """A criticidade de UM evento do SDK (D4).

    Régua decidiu: `classify_criticality(risco, mrr)`, como sempre. Modelo
    decidiu: posição contra a referência do tenant (última `pontuar_base`) ou,
    sem ela, contra os quantis do treino gravados no meta; sem nenhuma, ninguém
    é grave nem preocupante. A promoção pelo valor usa a referência de MRR do
    tenant (última `pontuar_base`); sem ela, não promove. Devolve
    `criticality`, `posicao_na_base`, `origem_da_posicao` e `mrr_no_topo`."""
    if not decidido_pelo_modelo or _rs.contrato_ativo() != _rs.CONTRATO_V3:
        return {"criticality": classify_criticality(risco, mrr), "posicao_na_base": None,
                "origem_da_posicao": None, "mrr_no_topo": None}
    ref, origem = referencia_para_evento(tenant_id)      # já leu o disco, se faltava
    pos = posicao_no_score(risco, ref) if ref else None
    pos_mrr = _posicao_do_mrr(mrr, _referencia_do_mrr.get(tenant_id))
    return {"criticality": criticidade_por_posicao(pos, dias, uso,
                                                   faixas_de_posicao(tenant_id), pos_mrr),
            "posicao_na_base": None if pos is None else round(pos, 4),
            "origem_da_posicao": origem if ref else None,
            "mrr_no_topo": None if pos_mrr is None else mrr_no_topo(pos_mrr)}


def criticidade_pela_regua(cliente: dict, regua: dict) -> str:
    """A criticidade que a RÉGUA DA BASE dá a um cliente, mesmo com o modelo
    ativo. `pontuar_cliente` deixa o modelo decidir quando ele existe; a
    comparação régua x modelo do dashboard (Rodada 3) precisa das duas respostas
    para o mesmo cliente. É a mesma conta do ramo da régua em `pontuar_cliente`:
    risco pela posição na base e criticidade com o sinal absoluto."""
    dias, uso = cliente.get("days_since_last"), cliente.get("features_used_30d")
    if dias is None and uso is None:
        return CRITICIDADE_SEM_DADO
    return classify_criticality(risco_por_posicao(dias, uso, regua), cliente.get("mrr"),
                                dias, uso)

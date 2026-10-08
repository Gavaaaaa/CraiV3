"""
crai/ml/classificador_v4.py — A base v4 do classificador de falha: rótulo por mecanismo.

Por que existe
──────────────
Na base v2 o rótulo `recovered` é `Bernoulli(fórmula sobre as próprias features + ruído)`
(`visoes._rotulo_recuperacao`). A probabilidade verdadeira de quase toda cobrança fica no
meio (só 1% acima de 0,80), e por isso nenhum corte sim/não separa bem: no holdout, o
modelo de produção e o oráculo do gerador têm a MESMA precisão em todo nível de recall
(46,2% com recall de 93,8%; medição de 07/10/2026). O que limita não é o modelo, é o
rótulo.

O v4 troca a causa. A cobrança deixa de recuperar por sorteio de fórmula e passa a
recuperar por um MECANISMO: o cliente tem ou não tem saldo no dia da nova tentativa, e a
causa da falha e o método de pagamento dizem se a nova tentativa resolve sozinha ou se
depende de uma ação do cliente. O saldo vem da MESMA simulação de fluxo de caixa que gera
a base de liquidez (`synthetic_data._liquidity_series_customer`), e o modelo NUNCA o vê:
aprende pelos rastros que ele deixa no que é visível (o dia do mês, o tamanho da fatura
diante do ticket do cliente, a causa, o método, o histórico de pagamento).

É experimento. Nada daqui é promovido, e o `FailureClassifier.load()` de produção não lê
nenhum arquivo do v4. Desenho aprovado em 08/10/2026 (`docs/evidencia_v4/DESENHO.md`); os
parâmetros abaixo foram fixados ANTES de qualquer medição de modelo, e só a taxa de
recuperação da base foi conferida contra a faixa declarada.

As linhas e as features NÃO mudam
─────────────────────────────────
A base v4 tem as mesmas 120.000 cobranças da v2 (`classificador.parquet`), na mesma
ordem, com as mesmas colunas e os mesmos valores. Só o rótulo é refeito, e entram as
colunas `oculto_*`, que o treino recusa. O cliente é o da população compartilhada: mesmo
`customer_id`, mesmo MRR.

O mecanismo (por cobrança que falhou no dia D)
──────────────────────────────────────────────
    fator        = invoice_amount / mrr          (1 na mensal, 10 na anual, <1 na proporcional)
    saldo_t      = saldo do cliente no dia t, em múltiplos da mensalidade (simulação de caixa)
    tem_saldo_t  = saldo_t >= fator

    Novas tentativas (regra do BACEN: até 3, em 7 dias): nos dias D+2, D+4 e D+6, e só
    as que restam na escada: n = clip(4 - attempt_count, 0, 3).

    Saldo insuficiente é coerente com o caixa: em D o saldo fica em U(0; 0,95) x fatura.
    Se a simulação tinha mais que isso, a diferença é um gasto feito em D, descontado
    também dos dias seguintes da janela (subtração simples, sem simular o caixa de novo).

    Pix Automático, por causa:
      insufficient_funds     recupera se há saldo em alguma nova tentativa
      processing_error       idem, e cada tentativa com saldo passa com P_TECNICO_OK
      generic_decline        idem, com P_GENERICO_OK por tentativa
      limit_exceeded         a nova tentativa esbarra no mesmo limite: só recupera se o
                             cliente age (probabilidade que cresce com a satisfação
                             latente) e há saldo em algum dia da janela
      authorization_revoked  não há nova tentativa: só recupera se o cliente autoriza de
                             novo (idem, mais raro) e há saldo na janela
    Boleto (qualquer causa): não há débito automático; recupera se o cliente paga
      (probabilidade que cresce com a saúde financeira latente) e há saldo na janela.

    Por fim, P_EXCECAO das cobranças têm o desfecho trocado por moeda honesta, como na v2.

`oculto_p_recuperacao` é a probabilidade de recuperar DADO o caixa (antes dos sorteios de
comportamento). A AUC dela é o teto de quem soubesse o saldo; o modelo, que não sabe,
fica abaixo. Essa distância é a medida do que a previsão de liquidez poderia acrescentar.

Sementes
────────
Nunca `default_rng(seed)` cru (ver `voluntario_v3`). Cada cliente tem os seus próprios
fluxos, derivados do `customer_id`: um para o caixa e outro para os sorteios das cobranças
dele. O caixa é simulado sempre no MESMO calendário (`CAIXA_INICIO` a `CAIXA_FIM`), e não
nas datas da tabela recebida. Por isso o rótulo de um cliente não depende de quem mais
está na tabela: qualquer amostra de clientes tem exatamente as linhas que esses clientes
têm na base inteira (testado). Depende, sim, da ordem das cobranças do próprio cliente,
que na base é a de `data_cobranca`.
"""

from typing import Optional

import numpy as np
import pandas as pd

from . import calibracao as _calibracao
from .populacao import LATENTES
from .synthetic_data import _business_day_index, _liquidity_series_customer, seed_por_cliente

SEED = 42
PREFIXO_OCULTO = "oculto_"

# ── O mecanismo: parâmetros fixados no Bloco 0, antes de medir modelo ─────
JANELA_BACEN_DIAS = 7
MAX_RETENTATIVAS = 3
DIAS_DE_RETENTATIVA = (2, 4, 6)          # dias depois da falha
# Saldo insuficiente: o saldo em D fica em U(0; TETO) x fatura, abaixo dela.
TETO_DO_SALDO_NA_FALHA = 0.95
P_TECNICO_OK = 0.90                      # erro de processamento: a tentativa com saldo passa
P_GENERICO_OK = 0.50                     # recusa genérica: metade das tentativas com saldo passa
# Probabilidade de o cliente agir = base + coeficiente x latente (0..1).
P_ACAO_LIMITE = (0.20, 0.40)             # sobe o limite do Pix: 0,20 + 0,40 x satisfacao
P_REAUTORIZA = (0.05, 0.25)              # autoriza de novo:     0,05 + 0,25 x satisfacao
P_PAGA_BOLETO = (0.30, 0.55)             # paga o boleto:        0,30 + 0,55 x saude_financeira
P_EXCECAO = 0.02                         # mesma disciplina da v2 (`p_excecao_rotulo`)
# Faixa declarada da taxa de recuperação realizada (a da v2 é 0,396). Testada.
FAIXA_TAXA_RECUPERACAO = (0.30, 0.50)

# O calendário do caixa: do primeiro dia de cobrança da base v2 (a janela de 180 dias que
# termina em 2026-09-14 começa a cobrar em 2026-04-01) ao último mais a janela do BACEN.
# Fixo, e não tirado da tabela: a simulação de caixa consome sorteios conforme o
# intervalo, e com datas diferentes o mesmo cliente teria outro saldo.
CAIXA_INICIO = "2026-04-01"
CAIXA_FIM = "2026-09-21"

CAUSAS_COM_RETENTATIVA = ("insufficient_funds", "processing_error", "generic_decline")
CAUSAS_QUE_PEDEM_ACAO = ("limit_exceeded", "authorization_revoked")

COLUNAS_OCULTAS = [
    "oculto_fator_da_fatura",
    "oculto_retentativas",
    "oculto_tentativas_com_saldo",
    "oculto_saldo_na_janela",
    "oculto_p_recuperacao",
    "oculto_rotulo_sorteado",
]

# ── Fluxos aleatórios ─────────────────────────────────────────────────────
FLUXO_CAIXA = (4, 1)
FLUXO_COBRANCAS = (4, 2)


def fluxo(seed: int, spawn_key: tuple) -> np.random.Generator:
    """Gerador derivado de `seed`, independente de `default_rng(seed)`."""
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=spawn_key))


def datas_do_caixa(cobrancas: Optional[pd.DataFrame] = None) -> pd.DatetimeIndex:
    """O calendário fixo do caixa. Com `cobrancas`, confere que toda cobrança cai nele com
    a janela do BACEN inteira pela frente, e levanta `ValueError` se alguma não cai."""
    datas = pd.date_range(CAIXA_INICIO, CAIXA_FIM, freq="D")
    if cobrancas is not None and len(cobrancas):
        dias = pd.to_datetime(cobrancas["data_cobranca"]).dt.normalize()
        limite = datas[-1] - pd.Timedelta(days=JANELA_BACEN_DIAS)
        if dias.min() < datas[0] or dias.max() > limite:
            raise ValueError(
                f"cobrancas de {dias.min().date()} a {dias.max().date()} fora do calendario do "
                f"caixa ({datas[0].date()} a {limite.date()}, mais {JANELA_BACEN_DIAS} dias)")
    return datas


def caixa_do_cliente(customer_id: str, perfil: str, datas: pd.DatetimeIndex,
                     bday_idx: np.ndarray, seed: int, parametros: dict) -> np.ndarray:
    """O saldo diário do cliente, em múltiplos da mensalidade, pela simulação de caixa do
    Módulo 3. Função só de `seed`, do cliente e das datas."""
    rng = fluxo(seed, (*FLUXO_CAIXA, seed_por_cliente(customer_id)))
    serie = _liquidity_series_customer(customer_id, perfil, datas, bday_idx, rng, parametros)
    return serie["balance_norm"].to_numpy(dtype=float)


def _p_de_agir(par: tuple, latente: np.ndarray) -> np.ndarray:
    return np.clip(par[0] + par[1] * latente, 0.0, 1.0)


def rotular(cobrancas: pd.DataFrame, pop: pd.DataFrame, seed: int = SEED,
            fonte: str = "sintetico_calibrado") -> pd.DataFrame:
    """O rótulo do v4 e as colunas ocultas, uma linha por cobrança, na ordem recebida.

    `cobrancas` é a visão do classificador da v2 (precisa de `customer_id`,
    `data_cobranca`, `invoice_amount`, `attempt_count`, `gateway_error_code` e
    `metodo_pagamento`); `pop` é a população compartilhada.
    """
    P = _calibracao.parametros(fonte)["liquidity"]
    n = len(cobrancas)
    ids = cobrancas["customer_id"].to_numpy()
    da_pop = pop.set_index("customer_id").loc[ids]
    mrr = da_pop["mrr"].to_numpy(dtype=float)
    satisfacao = da_pop["satisfacao"].to_numpy(dtype=float)
    saude = da_pop["saude_financeira"].to_numpy(dtype=float)
    perfil = da_pop["billing_profile"].to_numpy()

    datas = datas_do_caixa(cobrancas)
    bday_idx = _business_day_index(datas)
    dia = (pd.to_datetime(cobrancas["data_cobranca"]).dt.normalize() - datas[0]).dt.days.to_numpy()
    fator = cobrancas["invoice_amount"].to_numpy(dtype=float) / mrr
    causa = cobrancas["gateway_error_code"].to_numpy()
    boleto = cobrancas["metodo_pagamento"].to_numpy() == "boleto"
    retentativas = np.clip(4 - cobrancas["attempt_count"].to_numpy(dtype=int), 0, MAX_RETENTATIVAS)

    # Dois sorteios por cobrança, do fluxo do próprio cliente, na ordem das linhas dele:
    # o gasto que explica a falha por saldo e a moeda do desfecho.
    u_falha = np.zeros(n)
    u_desfecho = np.zeros(n)

    com_saldo = np.zeros(n, dtype=int)        # novas tentativas (as que restam) com saldo
    na_janela = np.zeros(n, dtype=int)        # algum dia da janela com saldo
    posicoes = pd.Series(np.arange(n)).groupby(ids, sort=False).indices
    for cid, linhas in posicoes.items():
        saldo = caixa_do_cliente(cid, perfil[linhas[0]], datas, bday_idx, seed, P)
        do_cliente = fluxo(seed, (*FLUXO_COBRANCAS, seed_por_cliente(cid)))
        u_falha[linhas] = do_cliente.uniform(0.0, TETO_DO_SALDO_NA_FALHA, size=len(linhas))
        u_desfecho[linhas] = do_cliente.random(len(linhas))
        for i in linhas:
            d, f = dia[i], fator[i]
            depois = saldo[d + 1: d + 1 + JANELA_BACEN_DIAS]
            if causa[i] == "insufficient_funds" and not boleto[i]:
                # Coerência com o caixa: em D o saldo estava abaixo da fatura.
                gasto = max(0.0, saldo[d] - u_falha[i] * f)
                depois = np.maximum(0.0, depois - gasto)
            tem = depois >= f
            na_janela[i] = int(tem.any())
            com_saldo[i] = int(tem[[k - 1 for k in DIAS_DE_RETENTATIVA[:retentativas[i]]]].sum())

    # ── Probabilidade de recuperar, dado o caixa ─────────────────────────
    p = np.zeros(n)
    pix = ~boleto
    p[pix & (causa == "insufficient_funds")] = (com_saldo > 0)[pix & (causa == "insufficient_funds")]
    tec = pix & (causa == "processing_error")
    p[tec] = 1.0 - (1.0 - P_TECNICO_OK) ** com_saldo[tec]
    gen = pix & (causa == "generic_decline")
    p[gen] = 1.0 - (1.0 - P_GENERICO_OK) ** com_saldo[gen]
    lim = pix & (causa == "limit_exceeded")
    p[lim] = _p_de_agir(P_ACAO_LIMITE, satisfacao[lim]) * na_janela[lim]
    rev = pix & (causa == "authorization_revoked")
    p[rev] = _p_de_agir(P_REAUTORIZA, satisfacao[rev]) * na_janela[rev]
    p[boleto] = _p_de_agir(P_PAGA_BOLETO, saude[boleto]) * na_janela[boleto]
    p = (1.0 - P_EXCECAO) * p + P_EXCECAO * 0.5

    recuperou = (u_desfecho < p).astype("int64")

    return pd.DataFrame({
        "recovered": recuperou,
        "oculto_fator_da_fatura": np.round(fator, 4),
        "oculto_retentativas": retentativas.astype("int64"),
        "oculto_tentativas_com_saldo": com_saldo.astype("int64"),
        "oculto_saldo_na_janela": na_janela.astype("int64"),
        "oculto_p_recuperacao": np.round(p, 6),
        "oculto_rotulo_sorteado": ((p > P_EXCECAO * 0.5 + 1e-9) & (p < 1 - P_EXCECAO * 0.5 - 1e-9)).astype("int64"),
    }, index=cobrancas.index)


def gerar_classificador_v4(cobrancas: pd.DataFrame, pop: pd.DataFrame, seed: int = SEED,
                           fonte: str = "sintetico_calibrado") -> pd.DataFrame:
    """A visão v4: as colunas da v2, com o `recovered` do mecanismo e as ocultas ao fim."""
    df = cobrancas.reset_index(drop=True).copy()
    novo = rotular(df, pop, seed=seed, fonte=fonte)
    df["recovered"] = novo["recovered"].to_numpy()
    for coluna in COLUNAS_OCULTAS:
        df[coluna] = novo[coluna].to_numpy()
    return df


def conferir_features(features) -> list:
    """Levanta `ValueError` se a lista traz latente, coluna `oculto_` ou o rótulo. É a
    guarda que o treino usa: as ocultas ficam no parquet para o teto, nunca na matriz."""
    features = list(features)
    proibidas = [f for f in features
                 if f.startswith(PREFIXO_OCULTO) or f in LATENTES or f == "recovered"]
    if proibidas:
        raise ValueError(f"features proibidas na matriz de treino: {proibidas} "
                         "(latente, prefixo oculto_ ou o proprio rotulo)")
    return features


def verificacoes(df: pd.DataFrame, cobrancas_v2: Optional[pd.DataFrame] = None) -> dict:
    """Os números que o MANIFESTO_v4.json grava e os testes conferem."""
    from sklearn.metrics import roc_auc_score

    taxa = float(df["recovered"].mean())
    por = lambda coluna: {str(k): round(float(v), 4)                       # noqa: E731
                          for k, v in df.groupby(coluna)["recovered"].mean().items()}
    saida = {
        "cobrancas": int(len(df)),
        "clientes": int(df["customer_id"].nunique()),
        "taxa_recuperacao": round(taxa, 4),
        "faixa_taxa_recuperacao": list(FAIXA_TAXA_RECUPERACAO),
        "taxa_dentro_da_faixa": bool(FAIXA_TAXA_RECUPERACAO[0] <= taxa <= FAIXA_TAXA_RECUPERACAO[1]),
        "taxa_por_causa": por("gateway_error_code"),
        "taxa_por_metodo": por("metodo_pagamento"),
        "taxa_por_ciclo": por("ciclo") if "ciclo" in df.columns else None,
        "taxa_por_degrau": por("attempt_count"),
        "fracao_com_saldo_na_janela": round(float(df["oculto_saldo_na_janela"].mean()), 4),
        "teto_com_o_caixa_auc_base_inteira": round(
            float(roc_auc_score(df["recovered"], df["oculto_p_recuperacao"])), 4),
        "p_acima_de_0_80": round(float((df["oculto_p_recuperacao"] > 0.80).mean()), 4),
        "p_abaixo_de_0_10": round(float((df["oculto_p_recuperacao"] < 0.10).mean()), 4),
    }
    if cobrancas_v2 is not None:
        iguais = [c for c in cobrancas_v2.columns if c != "recovered"]
        saida["features_iguais_as_da_v2"] = bool(
            df[iguais].reset_index(drop=True).equals(cobrancas_v2[iguais].reset_index(drop=True)))
        saida["taxa_recuperacao_v2"] = round(float(cobrancas_v2["recovered"].mean()), 4)
        saida["concordancia_do_rotulo_com_a_v2"] = round(
            float((df["recovered"].to_numpy() == cobrancas_v2["recovered"].to_numpy()).mean()), 4)
    return saida

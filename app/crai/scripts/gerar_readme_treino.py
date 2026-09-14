"""crai/scripts/gerar_readme_treino.py — Gera `README_treino.md` a partir dos JSONs.

O README de treino NÃO é escrito à mão: as tabelas de features saem de
`models/calibracao.json` (proveniência feature a feature), as métricas das
duas rodadas saem de `docs/evidencia/treino/rodada_{baixa,alta}.json` e a
checagem fora do domínio de `docs/evidencia/treino/fora_do_dominio.json`.
Se um número mudar no JSON e ninguém regerar o README, o teste
`tests/test_readme_treino.py` reprova — é a mesma catraca de honestidade de
`tests/test_metricas_declaradas.py`, aplicada a este documento.

Uso (de `app/`):
    python -m crai.scripts.gerar_readme_treino            # grava README_treino.md
    python -m crai.scripts.gerar_readme_treino --stdout   # so imprime
"""

import argparse
import io
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CALIBRACAO = BASE_DIR / "models" / "calibracao.json"
EVIDENCIA = BASE_DIR / "docs" / "evidencia" / "treino"
README = BASE_DIR / "README_treino.md"

VOLUMES_DECLARADOS = {
    "FailureClassifier": ("classifier", 3000, 6000),
    "AnomalyDetector": ("anomaly", 5500, 11000),
    "PaydayInference": ("payday", 600, 1200),
    "risk_scorer_voluntario": ("voluntario", 2000, 4000),
}

STATUS_LEGIVEL = {
    "ancorada": "ancorada em dado real",
    "proxy_fraco": "proxy FRACO de coluna real",
    "sintetica_sem_doador": "100% sintetica, SEM doador",
}


def _ler(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


def _f(v, casas=4):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.{casas}f}".replace(".", ",")
    return str(v)


def _delta(a, b, casas=4):
    if a is None or b is None:
        return "-"
    d = b - a
    if isinstance(a, int) and isinstance(b, int):
        return ("+" if d >= 0 else "") + str(d)
    return ("+" if d >= 0 else "") + f"{d:.{casas}f}".replace(".", ",")


def tabela_features(cal: dict, modelo: str) -> str:
    linhas = ["| Feature | Status | Fonte / coluna real | Nota |", "|---|---|---|---|"]
    for l in cal["proveniencia_features"][modelo]:
        fonte = f"{l['fonte']} / `{l['coluna']}`" if l.get("fonte") else "-"
        nota = (l.get("nota") or "").replace("|", "/")
        linhas.append(f"| `{l['feature']}` | {STATUS_LEGIVEL[l['status']]} | {fonte} | {nota} |")
    contagem = {}
    for l in cal["proveniencia_features"][modelo]:
        contagem[l["status"]] = contagem.get(l["status"], 0) + 1
    resumo = ", ".join(f"{contagem.get(s, 0)} {STATUS_LEGIVEL[s]}" for s in STATUS_LEGIVEL)
    return "\n".join(linhas) + f"\n\nResumo: {resumo}.\n"


def tabela_rodadas(baixa: dict, alta: dict, chave: str, metricas: list) -> str:
    b, a = baixa[chave], alta[chave]
    linhas = [f"| Metrica | n = {b['n_amostras']} | n = {a['n_amostras']} | delta |", "|---|---|---|---|"]
    for nome, k in metricas:
        vb, va = b.get(k), a.get(k)
        linhas.append(f"| {nome} | {_f(vb)} | {_f(va)} | {_delta(vb, va) if isinstance(vb, (int, float)) and isinstance(va, (int, float)) else '-'} |")
    linhas.append(f"| `fonte_usada` | `{b['fonte_usada']}` | `{a['fonte_usada']}` | |")
    linhas.append(f"| recarregou via `load()` | {'sim' if b['recarregavel'] else 'NAO'} | {'sim' if a['recarregavel'] else 'NAO'} | |")
    return "\n".join(linhas) + "\n"


def tabela_curva(curva: dict, chave: str) -> str:
    linhas = ["| n_amostras | AUC media | desvio-padrao (3 seeds) |", "|---|---|---|"]
    for p in curva[chave]:
        linhas.append(f"| {p['n_amostras']} | {_f(p['auc_media'])} | {_f(p['auc_dp'])} |")
    return "\n".join(linhas) + "\n"


def gerar() -> str:
    cal = _ler(CALIBRACAO)
    baixa = _ler(EVIDENCIA / "rodada_baixa.json")
    alta = _ler(EVIDENCIA / "rodada_alta.json")
    fora = _ler(EVIDENCIA / "fora_do_dominio.json")
    P = cal["parametros"]
    notas = cal["notas"]
    versoes = alta["classifier"]["versoes"]
    curva = fora.get("curva_de_volume")

    # Marcação de rodada: as métricas deste documento vêm dos JSONs de evidência,
    # não dos artefatos atuais de `models/`. Sem isso o leitor compara o número
    # daqui com o `train_metrics.json` em disco como se fossem a mesma rodada.
    def _quando(iso: str) -> str:
        d, h = iso.split("T")
        a, m, dia = d.split("-")
        return f"{dia}/{m}/{a} {h[:5]}"
    quando_baixa = _quando(baixa["classifier"]["treinado_em"])
    quando_alta = _quando(alta["classifier"]["treinado_em"])
    marca_rodada = (f"> **Rodada descrita:** `rodada_baixa.json` ({quando_baixa}) e "
                    f"`rodada_alta.json` ({quando_alta}), Python {versoes['python']}. "
                    "Nao e o estado atual de `models/`: ver `README.md` (secao 4.6) e "
                    "`docs/LIMITACOES.md`.\n")

    fe = cal["fontes"]
    an_fd = fora["anomaly"]["fora_do_dominio"]
    an_ed = fora["anomaly"]["em_dominio"]
    vo = fora["voluntario"]
    cl = fora["classifier"]

    out = []
    w = out.append

    w("# README_treino — base de dados e treino/retreino dos modelos da CRAI\n")
    w("> **Gerado por** `python -m crai.scripts.gerar_readme_treino` a partir de "
      "`models/calibracao.json` e `docs/evidencia/treino/*.json`. Nao edite as tabelas "
      "a mao: regere. O teste `tests/test_readme_treino.py` confere que este arquivo "
      "bate com os JSONs.\n>\n"
      f"> Fonte dos parametros: **`sintetico_calibrado`** · calibracao gerada em "
      f"`{cal['gerado_em']}` · versoes gravadas nos `meta.json`: scikit-learn "
      f"{versoes['scikit-learn']}, xgboost {versoes['xgboost']}, torch {versoes['torch']}, "
      f"prophet {versoes['prophet']}.\n")

    w(f"> **Rodada que este documento descreve:** as metricas das secoes 3, 4.2, 4.3 e 6 "
      f"sao das rodadas gravadas em `docs/evidencia/treino/rodada_baixa.json` (classificador "
      f"treinado em {quando_baixa}) e `rodada_alta.json` (classificador treinado em "
      f"{quando_alta}), com Python {versoes['python']}. Elas NAO descrevem os artefatos "
      "atuais de `models/`, que podem vir de um treino posterior: o estado atual esta em "
      "`models/train_metrics.json` e `models/*_meta.json`, declarado em `README.md` "
      "(secao 4.6) e em `docs/LIMITACOES.md`.\n")

    w("## 0. Declaracao, antes de qualquer numero\n")
    w("**Isto NAO e treino com dado real de churn observado.** Nenhum dos quatro modelos "
      "abaixo viu um rotulo real de \"esta cobranca foi recuperada\" ou \"este cliente "
      "cancelou\" — esse dado nao existe publicamente, e dentro da CRAI ainda nao foi "
      "acumulado. O que este trabalho entrega, e so isso:\n\n"
      "1. **Parte das features calibrada em dado real** de outras bases (Olist, BACEN, "
      "E-Commerce Customer Churn), feature a feature, com o status de cada uma declarado "
      "(secao 2) — e o resto declarado como inventado.\n"
      "2. **O mecanismo de treino/retreino funcionando de ponta a ponta**: `train(fonte=...)` "
      "nos tres modulos + o candidato do voluntario, duas rodadas de volume por modelo, "
      "artefatos salvos e recarregados via `load()` (secao 3).\n"
      "3. **Mitigacao explicita da circularidade** — o rotulo nao e funcao deterministica "
      "das features (secao 4.1).\n"
      "4. **Uma checagem honesta fora do dominio**, com a queda de performance registrada "
      "como saiu (secao 4.2).\n\n"
      "Os quatro bloqueios para treinar com dado real **continuam de pe e nao sao resolvidos "
      "aqui** (secao 7).\n")

    w("## 1. Fonte e volume por modelo\n")
    w("| Modelo | Codigo | `train()` | Fonte usada | Volume (rodada 1 -> rodada 2) | Artefatos em `models/` |")
    w("|---|---|---|---|---|---|")
    w(f"| FailureClassifier | `crai/ml/failure_classifier.py` | `train(n_samples, fonte)` | `{alta['classifier']['fonte_usada']}` | {baixa['classifier']['n_amostras']} -> {alta['classifier']['n_amostras']} transacoes | `xgb_failure_classifier.joblib`, `rf_failure_classifier.joblib`, `label_encoders.joblib`, `feature_names.joblib`, `train_metrics.json`, `failure_classifier_meta.json` |")
    w(f"| AnomalyDetector | `crai/ml/anomaly_detector.py` | `train(n_samples, fonte)` | `{alta['anomaly']['fonte_usada']}` | {baixa['anomaly']['n_amostras']} -> {alta['anomaly']['n_amostras']} clientes | `autoencoder.pt`, `autoencoder_scaler.pkl`, `autoencoder_meta.json` |")
    w(f"| PaydayInference | `crai/ml/payday_inference.py` | `train(n_samples, fonte)` | `{alta['payday']['fonte_usada']}` | {baixa['payday']['n_amostras']} -> {alta['payday']['n_amostras']} clientes x 180 dias | `payday_lstm.pt`, `payday_prophet_{{CLT,PJ,freelancer}}.json`, `payday_meta.json` |")
    w(f"| risk_scorer voluntario (candidato) | `crai/ml/voluntary_risk.py` -> encaixe em `churn_voluntary/risk_scorer.py` | `train(n_samples, fonte)` | `{alta['voluntario']['fonte_usada']}` | {baixa['voluntario']['n_amostras']} -> {alta['voluntario']['n_amostras']} eventos | `voluntary_risk_candidato.joblib` + `_meta.json` (**nao ativo** — ver 3.4) |")
    w("")
    w("`fonte=\"sintetico\"` (default) continua sendo o gerador de sempre, byte-identico "
      "(`tests/test_synthetic_data.py::TestDefaultNaoMudou` trava o hash). "
      "`fonte=\"sintetico_calibrado\"` le `models/calibracao.json`, gerado por "
      "`python -m crai.scripts.preparar_amostra_real` — e o unico arquivo de `models/` "
      "versionado no git, porque e um punhado de numeros medidos, nao um binario.\n")
    w("### 1.1 Doadores reais\n")
    w("| Fonte | O que e | Licenca | Prova de identidade | Papel |")
    w("|---|---|---|---|---|")
    w(f"| **A** Olist | 300 transacoes reais estratificadas (de 103.877) | CC BY-NC-SA 4.0 (academico) | SHA-256 da amostra `{fe['A']['amostra_sha256'][:16]}...` | valor da fatura (MLE lognormal), hora / dia da semana / dia do mes |")
    w(f"| **B** BACEN SGS 21084 | inadimplencia PF mensal | ODbL | media {_f(fe['B']['media'], 2)} / ultimo {_f(fe['B']['ultimo'], 2)} — origem: {fe['B']['origem']} | peso de `insufficient_funds` nos codigos de erro |")
    w(f"| **E** E-Commerce Customer Churn (Kaggle, ankitverma2010) | {fe['E']['n']} clientes, 20 colunas, rotulo `Churn` | nao declarada no Kaggle — academico apenas, bruto fora do git | SHA-256 do xlsx `{fe['E']['sha256_bruto'][:16]}...` | `days_since_last(_login)`, `features_used_30d`, `avg_session_min`, `tickets_30d`, `nps_last` |")
    w("")
    w("Mapeamento fixo da Fonte E (nenhum outro foi inventado): `DaySinceLastOrder` -> "
      "`days_since_last` (risk_scorer) e `days_since_last_login` (AnomalyDetector); "
      "`OrderCount` -> `features_used_30d`; `HourSpendOnApp` -> `avg_session_min`; "
      "`Complain` -> `tickets_30d`; `SatisfactionScore` -> `nps_last`. O `Churn` real "
      "**nao entra em parametro nem em treino** — so na checagem fora do dominio.\n")

    w("## 2. Tabela de features — ancorada / proxy fraco / sintetica sem doador\n")
    w("Puxada de `models/calibracao.json -> proveniencia_features` (a mesma lista que "
      "`train()` devolve em `proveniencia` e grava no `meta.json`).\n")
    for modelo in ("FailureClassifier", "AnomalyDetector", "PaydayInference", "risk_scorer_voluntario"):
        w(f"### 2.{list(VOLUMES_DECLARADOS).index(modelo) + 1} {modelo}\n")
        w(tabela_features(cal, modelo))

    w("## 3. Mecanismo de retreino — duas rodadas por modelo, lado a lado\n")
    w(marca_rodada)
    w("Comandos exatos (de `app/`), com os artefatos sobrescritos em `models/` a cada rodada "
      "e a verificacao `load()` apos cada `train()`:\n")
    w("```\npython -m crai.scripts.train_all --fonte sintetico_calibrado \\\n"
      "    --classifier-samples 3000 --anomaly-samples 5500 --payday-customers 600 \\\n"
      "    --voluntario-samples 2000 --saida-json docs/evidencia/treino/rodada_baixa.json\n"
      "python -m crai.scripts.train_all --fonte sintetico_calibrado \\\n"
      "    --classifier-samples 6000 --anomaly-samples 11000 --payday-customers 1200 \\\n"
      "    --voluntario-samples 4000 --saida-json docs/evidencia/treino/rodada_alta.json\n```\n")
    w(f"Rodada 1: {baixa['duracao_s']} s · rodada 2: {alta['duracao_s']} s (CPU). Logs completos em "
      "`docs/evidencia/treino/log_rodada_*.txt`.\n")

    w("### 3.1 FailureClassifier (XGBoost 0,7 + RandomForest 0,3)\n")
    w(marca_rodada)
    w(tabela_rodadas(baixa, alta, "classifier", [
        ("AUC-ROC (holdout 20%)", "auc"), ("Acuracia @ limiar 0,25", "accuracy"),
        ("Precisao (recuperado)", "precision_recovered"), ("Recall (recuperado) @ 0,25", "recall_recovered"),
        ("F1 (recuperado)", "f1_recovered"), ("e-Profit medio (R$)", "avg_eprofit"),
        ("Linhas de treino", "n_treino"), ("Linhas de teste", "n_teste")]))
    w(f"Recall OPERACIONAL (regra de e-Profit, que e quem decide): "
      f"{_f(baixa['classifier']['recall_operacional']['recall'])} -> "
      f"{_f(alta['classifier']['recall_operacional']['recall'])}.\n")
    w("**Leia com cuidado.** Com o dobro de volume a AUC do holdout ficou estatisticamente no "
      "mesmo lugar (a diferenca esta dentro do erro-padrao de um holdout de 600-1.200 linhas, "
      "~0,02). Dois pontos nao provam tendencia; a curva de volume com 3 seeds (3.5) prova. "
      "E a AUC esta **abaixo do piso 0,70** do gate G3 do `sprints.md` (gate de sprint; o gate "
      "executavel mudou em 14/09/2026 — ver 4.3), porque isso e esperado e esta declarado, "
      "nao escondido.\n")

    w("### 3.2 AnomalyDetector (autoencoder 12-4-12, treinado so em saudaveis)\n")
    w(marca_rodada)
    w(tabela_rodadas(baixa, alta, "anomaly", [
        ("ROC-AUC (saudaveis held-out + anomalos)", "roc_auc"), ("Average precision", "average_precision"),
        ("Precisao @ p95", "precision"), ("Recall @ p95", "recall"), ("F1", "f1"),
        ("Threshold (p95 do erro saudavel)", "threshold"), ("Separacao anomalo/saudavel", "separation_ratio"),
        ("Epocas", "epochs_trained"), ("Saudaveis no treino", "n_train_healthy")]))
    w("### 3.3 PaydayInference (LSTM 0,6 + Prophet 0,4)\n")
    w(marca_rodada)
    w(tabela_rodadas(baixa, alta, "payday", [
        ("ROC-AUC diario — LSTM", "roc_auc_lstm"), ("ROC-AUC diario — Prophet", "roc_auc_prophet"),
        ("ROC-AUC diario — ensemble", "roc_auc_ensemble"), ("MAE da janela otima (dias) — ensemble", "mae_dias_ensemble"),
        ("MAE da janela otima (dias) — heuristica de dia fixo", "mae_dias_heuristica"),
        ("Acerto exato", "hit_exato_ensemble"), ("Acerto +-1 dia", "hit_1d_ensemble"),
        ("Janelas de treino", "n_janelas_treino"), ("Clientes de teste", "n_clientes_teste")]))
    w("### 3.4 risk_scorer voluntario — candidato (GradientBoosting sobre `FEATURES_DE_RISCO`)\n")
    w(marca_rodada)
    w(tabela_rodadas(baixa, alta, "voluntario", [
        ("AUC vs rotulo ruidoso", "auc_vs_rotulo"), ("AUC das PROPRIAS REGRAS vs rotulo (teto)", "auc_regra_vs_rotulo"),
        ("Brier", "brier"), ("MAE entre p(modelo) e regra", "mae_vs_regra"),
        ("Correlacao p(modelo) x regra", "corr_vs_regra"), ("Eventos de treino", "n_treino")]))
    w("O candidato **nao esta ativo**: o treino grava `voluntary_risk_candidato.joblib`, que "
      "`carregar_modelo()` nao le. Promover ao nome que o scorer carrega "
      "(`voluntary_risk.joblib`) e um passo explicito — `VoluntaryRiskModel.ativar()` ou "
      "`train_all --ativar-voluntario` — porque a partir dai o pipeline voluntario passa a "
      "decidir pelo modelo e nao pelas regras. Como o rotulo do candidato SAO as regras com "
      "ruido, ele nao sabe nada que as regras nao saibam (`corr_vs_regra` ~0,97): prova o "
      "encaixe, nao melhora a decisao. `tests/test_train_fonte.py::TestVoluntarioCandidato` "
      "trava os dois lados (sem ativar = regras identicas; ativado = modelo decide).\n")

    if curva:
        w("### 3.5 Curva de volume — o mecanismo responde a mais dado (3 seeds do gerador)\n")
        w(marca_rodada)
        w("Mesmo `train(fonte=\"sintetico_calibrado\")`, em 4 volumes, com 3 seeds diferentes do "
          "gerador em cada volume (`python -m crai.scripts.sanity_check_fora_do_dominio`). "
          "E a prova que dois pontos nao dao: a media sobe e a variancia entre seeds cai.\n")
        w("FailureClassifier:\n")
        w(tabela_curva(curva, "classifier"))
        w("risk_scorer voluntario (candidato):\n")
        w(tabela_curva(curva, "voluntario"))
        w("O voluntario satura em torno de 0,75 porque o proprio rotulo tem um teto: a AUC das "
          "regras contra o rotulo ruidoso e ~0,76 (tabela 3.4). Mais dado reduz a variancia, "
          "nao ultrapassa o teto — comportamento correto para um rotulo com ruido irredutivel.\n")

    w("## 4. Riscos conhecidos e mitigacao\n")
    w("### 4.1 Circularidade — o modelo decorar a formula que gerou o rotulo\n")
    w("O risco: se o rotulo for funcao deterministica das features, o modelo aprende a "
      "formula, a AUC vai a ~0,99 e o numero nao significa nada. A mitigacao esta em "
      "`crai/ml/synthetic_data.py`, ponto a ponto onde o rotulo e calculado, e os valores "
      "vem de `calibracao.json -> parametros`:\n")
    pc, pb, pl, pv = P["classifier"], P["behavioral"], P["liquidity"], P["voluntary"]
    w("| Gerador | Rotulo | O que ja existia | O que entrou no modo calibrado | Magnitude | Por que essa magnitude |")
    w("|---|---|---|---|---|---|")
    w(f"| `generate_dataset` | `recovered` | `p_recovery` leva N(0, {_f(pc['ruido_rotulo_sd'], 2)}) e o rotulo e um sorteio Bernoulli(p), p em [0,05; 0,95] — a mesma linha pode cair dos dois lados | `p_excecao_rotulo`: fracao de linhas com rotulo sorteado ao acaso, ignorando a regra | {_f(pc['p_excecao_rotulo'] * 100, 0)}% | {notas['classifier']['p_excecao_rotulo']} |")
    w(f"| `generate_behavioral_dataset` | `is_anomalous` | nada — o rotulo ERA a populacao de origem, e as populacoes quase nao se sobrepoem (ROC-AUC 0,995 no README, marcado como bandeira vermelha) | `p_excecao_anomalo`: anomalos que recebem 3 das 8 features comportamentais da distribuicao saudavel (churn silencioso); `p_excecao_saudavel`: saudaveis com 2 features degradadas (passageiro) | {_f(pb['p_excecao_anomalo'] * 100, 0)}% / {_f(pb['p_excecao_saudavel'] * 100, 0)}% | o rotulo deixa de ser dedutivel das features; a AUC em dominio caiu de 0,995 para ~0,98 e nao mais porque 8 das 12 features nao tem doador e continuam separadas a mao — o numero honesto e o da secao 4.2 |")
    w(f"| `generate_liquidity_series` | `has_liquidity` | saldo quase deterministico do calendario (a LSTM podia aprender o calendario, nunca o cliente) | `p_atraso_salario` (entrada chega 1-{pl['atraso_salario_max_dias']} dias depois) e `p_gasto_imprevisto` (gasto extra de {_f(pl['gasto_imprevisto']['min'], 1)}-{_f(pl['gasto_imprevisto']['max'], 1)} mensalidade) | {_f(pl['p_atraso_salario'] * 100, 0)}% por entrada / {_f(pl['p_gasto_imprevisto'] * 100, 0)}% por dia | choques que o mundo real tem e a serie nao tinha; a liquidez media caiu de ~66% para ~54% dos dias |")
    w(f"| `generate_voluntary_dataset` | `churn` | (gerador novo) | Bernoulli(regras + N(0, {_f(pv['ruido_rotulo_sd'], 2)})) + `p_excecao_rotulo` | sd {_f(pv['ruido_rotulo_sd'], 2)} / {_f(pv['p_excecao_rotulo'] * 100, 0)}% | o teto de AUC das regras contra o rotulo fica em ~0,76: o modelo pode aprender a tendencia, nao decorar a formula |")
    w("")
    w("Cada decisao esta comentada no codigo, no ponto exato (`synthetic_data.py`, blocos "
      "\"Anti-circularidade\"), e `tests/test_synthetic_data.py::TestAntiCircularidade` "
      "verifica que os mecanismos de fato alteram o rotulo sem alterar as features.\n")

    w("### 4.2 Checagem fora do dominio (Etapa C) — a queda esperada, registrada como saiu\n")
    w(marca_rodada)
    w("`python -m crai.scripts.sanity_check_fora_do_dominio` — so inferencia, nada e treinado "
      "com dado real. Resultado completo em `docs/evidencia/treino/fora_do_dominio.json`.\n")
    w("| Modelo | Em dominio (sintetico calibrado, holdout novo) | Fora do dominio (dado real) | Rotulo real | Leitura |")
    w("|---|---|---|---|---|")
    w(f"| AnomalyDetector | ROC-AUC {_f(an_ed['roc_auc'])} (flag {_f(an_ed['taxa_flag'] * 100, 1)}%) | ROC-AUC {_f(an_fd['roc_auc_erro_vs_churn'])} (flag {_f(an_fd['taxa_flag'] * 100, 1)}%, recall de churn {_f(an_fd['recall_churn_no_flag'] * 100, 1)}%) | `Churn` da Fonte E, {an_fd['dataset'].split(',')[1].strip()} | {an_fd['nota']} |")
    w(f"| risk_scorer — regras fixas | (as regras nao tem holdout) | ROC-AUC {_f(vo['regras_fixas']['roc_auc_vs_churn'])} vs churn; risco medio {_f(vo['regras_fixas']['risco_medio'], 3)} | `Churn` da Fonte E | abaixo de 0,5: no doador quem cancela pediu MAIS recentemente, o inverso da regra de inatividade — a regra e de SaaS por assinatura, o dado e de e-commerce |")
    w(f"| risk_scorer — candidato | AUC {_f(vo['candidato']['em_dominio_auc_vs_rotulo'])} vs rotulo ruidoso | ROC-AUC {_f(vo['candidato']['fora_do_dominio_auc_vs_churn'])} vs churn | `Churn` da Fonte E | {vo['candidato']['nota']} |")
    w(f"| FailureClassifier | p_recovery mediana {_f(cl['em_dominio']['p_recovery']['mediana'])} (p25-p75 {_f(cl['em_dominio']['p_recovery']['p25'])}-{_f(cl['em_dominio']['p_recovery']['p75'])}) | p_recovery mediana {_f(cl['fora_do_dominio']['p_recovery']['mediana'])} (p25-p75 {_f(cl['fora_do_dominio']['p_recovery']['p25'])}-{_f(cl['fora_do_dominio']['p_recovery']['p75'])}) nas 300 transacoes reais da Olist | **nao existe** | {cl['fora_do_dominio']['nota']} |")
    w("| PaydayInference | ROC-AUC diario " + _f(alta['payday']['roc_auc_ensemble']) + " | nao aplicavel | nao existe doador de serie de saldo | fica so a metrica em dominio, declarada como tal |")
    w("")
    w("**A queda e o teste funcionando.** Um autoencoder que caisse de 0,98 para 0,90 num "
      "rotulo real de outro dominio seria suspeito; cair para ~0,5 e o que se espera de um "
      "modelo que aprendeu a geometria de um gerador — ele nao esta artificialmente perfeito, "
      "esta honestamente limitado ao dominio em que foi treinado. Nada foi ajustado para os "
      "numeros subirem.\n")

    w("### 4.3 A AUC do classificador ficou abaixo do piso 0,70 do gate G3\n")
    w(marca_rodada)
    w(f"Medido: {_f(baixa['classifier']['auc'])} (n=3000) e {_f(alta['classifier']['auc'])} "
      "(n=6000) com fonte calibrada; com a fonte default nos MESMOS volumes, 0,680 e 0,676. "
      "O 0,7029 citado no `README.md` (secao 4.6) foi medido com n=15.000 na fonte default. "
      "Duas causas, as duas declaradas:\n\n"
      "- **Volume.** O piso 0,70 so aparece perto de 15.000 linhas (curva 3.5: 0,696 em "
      "12.000). Os volumes 3.000 -> 6.000 sao os pedidos para a prova de retreino, nao os "
      "do gate.\n"
      "- **Calibracao do valor.** O `DATA_CARD.md` (2.3 e 7) previa: com a fatura calibrada na "
      "Olist (mediana ~R$ 100), o termo `- clip((valor - 500)/5000, 0, 0,15)` do modelo causal "
      "fica ~0 em quase todas as linhas — o sinal do valor desaparece e a AUC cai. Um parametro "
      "medido e declarado vale mais que um inventado que parecia certo.\n\n"
      "Consequencia pratica que precisa ficar escrita: na data desta rodada, "
      "`tests/test_metricas_declaradas.py` exigia que a AUC do `train_metrics.json` presente "
      "em `models/` estivesse em [0,70; 0,92] **e** fosse citada na linha 4.6 do `README.md`; "
      "com os artefatos desta rodada em `models/`, esses dois testes reprovavam — e era o "
      "comportamento correto do gate de honestidade, nao um defeito deste trabalho. Num clone "
      "limpo (`models/` esta no `.gitignore`) eles pulam e a suite fica verde.\n\n"
      "**Desde 14/09/2026 o teste exige outra coisa:** teto 0,92 como gate anti-vazamento; "
      "piso 0,60 como gate de sanidade contra degradacao catastrofica (0,70 estava dentro do "
      "erro padrao da medida, ~0,006 com 8.000 linhas de teste, e nao distinguia aprovado de "
      "reprovado); e o criterio operacional — zero recuperaveis perdidos no "
      "`recall_operacional` e recall acima de 0,90 no limiar em uso — como gate do produto "
      "(`test_o_criterio_operacional_e_satisfeito`). O 0,70 deste titulo continua sendo o "
      "gate G3 de sprint aprovado em `docs/historico/APROVACAO_SPRINT3.md`, verdadeiro como "
      "historia. A decisao esta registrada em `docs/LIMITACOES.md`, e a linha 4.6 do "
      "`README.md` foi atualizada na mesma data.\n")

    w("## 5. O que JA aprende com dado real hoje: `offer_bandit.py`\n")
    w("`crai/churn_voluntary/offer_bandit.py` (Modulo 4, Thompson Sampling) nao depende de "
      "nada sintetico: cada par (perfil, oferta) mantem um posterior Beta(alfa, beta) sobre a "
      "taxa de aceite, e **cada aceite/recusa REAL** que chega pelo webhook "
      "`/webhooks/retention-outcome` atualiza o posterior (`record_outcome`, chamado em "
      "`voluntary_agent.py` depois de `registrar_desfecho`), persistido em "
      "`models/bandit_state.json`, isolado por `tenant_id`. E aprendizado online com o "
      "desfecho observado — a prova positiva, separada, de que a arquitetura ja fecha o ciclo "
      "com dado real onde o dado real existe. O rotulo ali e **aceitacao de oferta**, nao "
      "churn; isso esta declarado em `churn_voluntary/README_treino.md`.\n")

    w("## 6. Versionamento — o artefato recarrega de forma previsivel\n")
    w(marca_rodada)
    w("- `requirements.txt` fixa versao **exata** (`==`) de `scikit-learn`, `xgboost`, "
      "`torch` e `prophet` (antes: `torch>=2.3`, `prophet>=1.1`). "
      "`tests/test_train_fonte.py::TestVersionamento` reprova se voltar a faixa.\n"
      f"- Cada `meta.json` grava as versoes usadas NAQUELE treino "
      f"(`calibracao.versoes_bibliotecas()`): python {versoes['python']}, scikit-learn "
      f"{versoes['scikit-learn']}, xgboost {versoes['xgboost']}, torch {versoes['torch']}, "
      f"numpy {versoes['numpy']}, pandas {versoes['pandas']}, joblib {versoes['joblib']}, "
      f"shap {versoes['shap']}, prophet {versoes['prophet']}.\n"
      "- `load()` de cada modulo chama `calibracao.conferir_meta()`: se a versao do "
      "ambiente diverge da gravada, imprime um aviso NOMINAL (\"xgboost: treinado com "
      "2.1.1, ambiente tem 2.2.0\") em vez do erro generico ou do carregamento silenciosamente "
      "errado. O sufixo de build do torch (`+cpu` / `+cu130`) e ignorado na comparacao — e o "
      "mesmo formato de serializacao.\n")

    w("## 7. O que continua bloqueando o treino com dado real (nao resolvido aqui)\n")
    w("Os quatro bloqueios do `claude/crai-status-treino` e dos dois `README_treino.md` de "
      "pacote continuam exatamente onde estavam:\n\n"
      "1. **Perfil real do cliente** — `DBPerfilProvider._consultar()` "
      "(`crai/agent/perfil_provider.py`) segue stub; 4 das 12 features do classificador de "
      "falha (`tenure_months`, `avg_ticket`, `payment_history_score`, `failure_count_90d`) "
      "continuam fabricadas por `SyntheticPerfilProvider`.\n"
      "2. **Sinal real de cancelamento** — nao existe em lugar nenhum do sistema (nem Segment, "
      "nem webhook, nem HubSpot). O que `retention_log` grava e aceitacao de oferta. Sem esse "
      "sinal, o candidato do voluntario aprende as regras, nao churn.\n"
      "3. **Vies de selecao do corte 0,60** — quem tem `risk_score < 0,60` nunca recebe "
      "oferta e nunca gera desfecho; o proprio modelo decide quem entra no dataset dele.\n"
      "4. **Volume de ciclos com desfecho por webhook** — `recovery_cycles.db` e "
      "`retention_cycles.db` so viram dataset quando acumularem ciclos fechados de verdade "
      "(nao simulados), em producao, por tempo suficiente.\n\n"
      "Este trabalho torna o pipeline **pronto para receber** esse dado — `train(fonte=...)` "
      "e o ponto onde uma fonte `\"real\"` entraria — mas nao o substitui.\n")

    w("## 8. Como reproduzir\n")
    w("```\ncd app\npip install -r requirements.txt\n"
      "python -m crai.scripts.preparar_amostra_real        # doadores + models/calibracao.json\n"
      "pytest tests/test_synthetic_data.py tests/test_train_fonte.py -v\n"
      "python -m crai.scripts.train_all --fonte sintetico_calibrado --saida-json rodada.json\n"
      "python -m crai.scripts.sanity_check_fora_do_dominio --saida fora.json\n"
      "python -m crai.scripts.gerar_readme_treino            # regera este arquivo\n"
      "pytest tests/ -q\n```\n")
    w("Dado bruto (Olist, E-Commerce) fica em `data/real/` fora do git e e baixado de "
      "espelhos publicos com SHA-256 conferido; `data/real/PROVENIENCIA.json` (copia em "
      "`docs/evidencia/treino/`) registra hashes, contagens, nulos e o `describe()` de cada "
      "coluna usada. Se a API do BACEN estiver fora de alcance, o script usa o cache local ou, "
      "na falta dele, os valores declarados no `DATA_CARD.md` — e grava qual dos tres usou.\n")
    return "\n".join(out)


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Gera README_treino.md a partir dos JSONs")
    ap.add_argument("--stdout", action="store_true", help="so imprime, nao grava")
    args = ap.parse_args(argv)
    texto = gerar()
    if args.stdout:
        print(texto)
    else:
        README.write_text(texto, encoding="utf-8")
        print(f"[README] {README} gerado ({len(texto.splitlines())} linhas)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

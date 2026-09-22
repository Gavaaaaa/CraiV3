# ESTADO_12SET.md — fechamento da sessão pós-migração (sábado 12/09/2026)

## 1. Gates, sem maquiagem

| Tarefa | Gate | Resultado |
|---|---|---|
| 1 — instalar e validar | `pip install -r requirements.txt` limpo; suíte = 1039/5 | **passou**: torch 2.13.0+cpu, prophet 1.4.0, psycopg2-binary 2.9.10 instalados; 1039 passed / 5 skipped, nomes idênticos antes e depois |
| 2 — contrato da API | 5 endpoints documentados; 5 fixtures JSON válidas; 4 casos no insights | **passou** |
| 3 — régua da base | regressão intocada; bases opostas → risco diferente; < 30 linhas → número de hoje; sem dado → null | **passou** |
| 3B — cauda certa + piso absoluto | base saudável sem alto/crítico; tabela dos âncoras mantida ou explicada | **passou**: 488 padrão / 12 sem dado / 0 alarmes |
| 4 — bases de demonstração | 3 CSV reproduzíveis (hash idêntico em duas rodadas), abrem no Excel | **passou** |
| 5 — treino e tempos | suíte com 2 falhas esperadas em `test_metricas_declaradas` | **passou na segunda execução** (ver §4: a primeira produziu 3 falhas) |
| 6 — DECISOES.md | existe com 3 seções e arquivos por cenário; nenhum valor alterado | **passou** |
| 7 — LGPD | dois arquivos com todos os campos preenchidos ou NÃO VERIFICADO; checagem de PII em ML | **entregue, gate é revisão humana**; checagem de PII: nenhuma linha mostra CPF, telefone, e-mail ou chave Pix entrando em ML |

Suíte final: **2 failed, 1096 passed** (as duas de `test_metricas_declaradas`, o gate de honestidade: AUC 0,669 — n=6.000, holdout de 1.200 — abaixo do piso 0,70). Sem modelos em `app/models/` a suíte fica em 1093 passed / 5 skipped. Nenhum teste existente foi alterado; 54 testes novos em `tests/test_regua_da_base.py`.

## 2. AUCs e tempos das duas rodadas (CPU, torch 2.13.0+cpu, calibração versionada)

| | rodada baixa (3000/5500/600/2000) | publicada | rodada alta (6000/11000/1200/4000) | publicada |
|---|---|---|---|---|
| duração total | 59,5 s | 69,4 s | 81,9 s | 131,6 s |
| classifier | AUC 0,660 (~4 s) | 0,6695 | AUC 0,669 (~4 s) | 0,6669 |
| anomaly | ROC-AUC 0,9788 (~11 s) | 0,9789 | 0,9815 (~12 s) | 0,981 |
| payday | ens 0,9468, MAE 0,78 d (**~47 s**) | 0,9418, 0,68 d | ens 0,9562, MAE 0,78 d (**~69 s**) | 0,9519, 0,68 d |
| voluntário | AUC 0,7133 vs regras 0,7405 (~1 s), não promovido | idem | 0,7518 vs 0,7578, não promovido | 0,7517 / 0,7578 |

O LSTM do payday é ~80 % do tempo e escala linear com o número de clientes. O MAE do payday ficou em 0,78 dia **também com a calibração versionada**: a diferença para os 0,68 publicados é o build CPU contra CUDA, não a calibração. Os tempos por modelo vêm da diferença entre os `treinado_em` gravados (precisão de 1 s).

## 3. Instalações que falharam

Nenhuma. Observação: o `requirements-lock.txt` não instala em Python 3.11 (shap 0.52.0 exige ≥ 3.12) e diverge do `requirements.txt` e da evidência em shap, numpy e joblib. Decisão registrada: o lock é o documento errado; regenerar ou remover depois da apresentação.

## 4. O que quebrou e não foi consertado

- **As duas falhas de `test_metricas_declaradas.py`**: AUC medida 0,669 (n=6.000, holdout de 1.200) abaixo do piso [0,70; 0,92]. É o gate de honestidade funcionando; não tocar.
- **`preparar_amostra_real` não é determinístico como declara**: na primeira execução reescreveu `app/models/calibracao.json` (API do BACEN respondeu; SHA-256 da amostra Olist saiu `d800c60f…` em vez de `39485c22…`) e derrubou `test_readme_treino`. Resolvido por decisão sua: `calibracao.json` restaurado do git, rodadas refeitas sem a preparação, teste voltou a passar. **A causa do SHA divergente não foi investigada**; está em `docs/LIMITACOES.md` como pendência.
- `PROMPT_POS_MIGRACAO.md` foi movido para `docs/planos/` e este fechamento para `docs/`, para a raiz ficar como a ETAPA 3 da migração definiu.

## 5. O que você precisa decidir antes da próxima sessão

1. As três contradições de `DECISOES.md` (mensalidade, success fee 15 % vs 25/20 %, faixa de ICP R$ 10k vs 25k): cada uma tem a lista de arquivos; o fee é mudança de um minuto quando decidida.
2. Se a régua da base entra assim na apresentação de quarta (piso de 7 dias / 0 funcionalidades, precedência modelo > régua > regras) e se o `origem_da_regua` e a frase "sem sinal de abandono" vão para o contrato final do painel.
3. O que fazer com o `requirements-lock.txt` e com a não-reprodutibilidade do `preparar_amostra_real`, ambos depois da apresentação.

## Comandos git que ficaram para você

Nada foi commitado. Dois commits, como nos sprints anteriores: o primeiro é a
resposta ao professor e fica sozinho no histórico; o segundo é documentação.

```
git status
git diff -- app/crai/churn_voluntary/

# Commit 1 — a resposta ao professor: o risco se adapta à base, sem gritar à toa
git add app/crai/churn_voluntary/batch_scoring.py app/crai/churn_voluntary/risk_scorer.py app/crai/churn_voluntary/insights_unificados.py
git add app/tests/test_regua_da_base.py app/crai/scripts/gerar_bases_demo.py
git add painel/exemplos/README.md painel/exemplos/base_uso_diario.csv painel/exemplos/base_uso_mensal.csv painel/exemplos/base_saudavel.csv
git commit -m "feat(churn-voluntario): risco pela posição na base do tenant, com piso absoluto de desengajamento

Percentis 50/75/90 de days_since_last e 10/25/50 de features_used_30d, em memória em pontuar_base;
<30 linhas cai nas regras fixas com número idêntico. Criticidade alto/crítico exige sinal absoluto
(>=7 dias sem login ou 0 funcionalidades): uma base saudável sai ordenada e sem alarme.
origem_da_regua na linha do ranking; explicação em português diz a régua. 54 testes novos,
regressão intocada. Três bases de demonstração reproduzíveis com clientes-âncora."

# Commit 2 — documentação: contrato do painel, LGPD, decisões, fechamento
git add .gitignore docs/LIMITACOES.md docs/CONTRATO_PAINEL.md docs/lgpd/ docs/ESTADO_12SET.md docs/planos/PROMPT_POS_MIGRACAO.md
git add DECISOES.md painel/README.md painel/fixtures/
git commit -m "docs: contrato da API do painel com fixtures, mapeamento LGPD, DECISOES.md e estado de 12/09

Cinco endpoints (dois existentes lidos do código, três especificados), fixtures geradas pelo motor
real. Mapeamento de dados pessoais e decisões automatizadas (art. 20). Três contradições de preço
e ICP preparadas para decisão. Achado de não-determinismo do preparar_amostra_real em LIMITACOES;
app/rodada_*.json ignorado."

git push
```

`app/rodada_*.json` fica de fora (já está no `.gitignore`). Os `Co-Authored-By`
e `Claude-Session` ficam a seu critério.

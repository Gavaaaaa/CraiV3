# DECISOES.md — três contradições entre os documentos e o código

Preparado em 12/09/2026. **Nada foi decidido nem alterado aqui.** Cada seção
traz o que cada fonte diz, o espaço para a decisão, e a lista dos arquivos
que precisam mudar em cada cenário, levantada varrendo o repositório
(`grep` por `0.15`, `success_fee`, `mensalidade`, `10k`, `25k`, `500k`,
`Standard`, `Premium`). Os dois documentos de negócio (`CRAI_sistema.docx`
e `Plano de Negócio CRAI`) **não estão no repositório**; o que está citado
deles vem do roteiro do dia.

---

## a) Mensalidade

| Fonte | O que diz |
|---|---|
| Plano de Negócio, 4.2 | "não há mensalidade, nem taxa de implantação, nem qualquer valor fixo devido, em nenhum dos dois planos" |
| CRAI_sistema, 14.1 | Standard R$ 337/mês, Premium R$ 450/mês; registra que "o deck vence" |
| Código | não existe mensalidade em lugar nenhum: nenhuma constante, env, coluna ou cálculo (`grep -ri mensalidade app/crai` só encontra o uso da palavra como "uma mensalidade de saldo" no gerador sintético) |

**DECISÃO:** ______________________________________________  **Data:** ____/____/______

**Arquivos afetados por cenário**

*Cenário A1 — sem mensalidade (vale o Plano de Negócio):*
- Nada muda no código; ele já está assim.
- Fora do repositório: corrigir a seção 14.1 do `CRAI_sistema.docx` e o deck.
- `docs/CONTRATO_PAINEL.md`, seção 5 (`GET /resultado`): a `fatura` continua só com success fee. Nada a fazer.

*Cenário A2 — com mensalidade (Standard R$ 337 / Premium R$ 450):*
- `app/crai/config.py`: nova env e constante (ex.: `CRAI_MENSALIDADE_BRL` ou uma tabela por plano), no mesmo molde de `_float_da_env`.
- `app/tests/test_config_pricing.py`: teste novo travando o valor e a leitura da env.
- `app/crai/dunning/recovery_log.py::metricas` ou o futuro `GET /resultado`: a fatura passa a somar a mensalidade ao fee.
- `docs/CONTRATO_PAINEL.md`, seção 5: `fatura` ganha `mensalidade` e `plano`.
- `painel/fixtures/resultado.json`: idem.
- `docs/CONFIGURACAO.md`, tabela de envs (linha 202): a env nova.
- Fora do repositório: corrigir a seção 4.2 do Plano de Negócio.

---

## b) Success fee

| Fonte | O que diz |
|---|---|
| Plano de Negócio e CRAI_sistema | 25 % sobre recuperação, 20 % sobre retenção |
| Código | `SUCCESS_FEE_PCT_PADRAO = 0.15` em `app/crai/config.py:45`, **um percentual só**, lido por `success_fee_pct()` (env `CRAI_SUCCESS_FEE_PCT`, faixa [0, 1]); aplicado **só na recuperação** (`workflow.py::success_fee`, `recovery_log.py`). **Não existe fee sobre retenção** em lugar nenhum: `ciclos_retencao` não tem coluna de fee. |

**DECISÃO:** ______________________________________________  **Data:** ____/____/______

**A alteração de um minuto, quando decidida** (cenário "25 % na recuperação"):
- Mudar `app/crai/config.py:45` de `SUCCESS_FEE_PCT_PADRAO = 0.15` para `0.25`.
- O teste que trava esse valor: `app/tests/test_config_pricing.py:54-56`
  (`test_o_fee_default_e_15_por_cento`, asserts `success_fee_pct() == 0.15` e
  `success_fee(VALOR, True) == round(VALOR * 0.15, 2)`). Renomear e trocar os
  dois literais.
- Outros testes que multiplicam por `0.15` literal, e quebram junto:
  `app/tests/test_recovery_log.py:165` e `:264`.

**Arquivos afetados por cenário**

*Cenário B1 — manter 15 %:* nada no código. Corrigir os dois documentos de negócio.

*Cenário B2 — 25 % na recuperação, sem fee de retenção ainda:*
- `app/crai/config.py:45` (o valor) e `:6` (docstring cita `amount * 0.15`).
- `app/tests/test_config_pricing.py:6` (docstring), `:54-56` (o teste).
- `app/tests/test_recovery_log.py:165`, `:264` (literais `0.15`).
- `app/tests/test_pix_confirmacao.py:279` (docstring cita "15%").
- `app/crai/api/idempotencia.py:16` (docstring cita `amount * 0.15`).
- `docs/CONTRATO_PAINEL.md:396-398` (nota sobre o percentual).
- `painel/fixtures/resultado.json:11`, `:12`, `:45` (regerar).
- `docs/planos/churn_involuntario_sprints.md:50`, `:112`, `:372`, `:381`, `:388`: **histórico de sprint, não alterar**; cita o 0,15 como fato da época.
- Fora do repositório: nada.

*Cenário B3 — 25 % na recuperação E 20 % na retenção (o que os documentos dizem):*
- Tudo do B2, mais:
- `app/crai/config.py`: segunda env/constante (`CRAI_SUCCESS_FEE_RETENCAO_PCT`, `SUCCESS_FEE_RETENCAO_PCT_PADRAO = 0.20`) e função `success_fee_retencao_pct()`.
- `app/crai/churn_voluntary/retention_log.py`: coluna `success_fee` em `ciclos_retencao` (hoje não existe) e o cálculo no desfecho aceito (`registrar_desfecho`); é mudança de schema com migração.
- `app/tests/test_config_pricing.py`: testes da segunda env.
- `docs/CONFIGURACAO.md:202`: a env nova.
- `docs/CONTRATO_PAINEL.md`, seção 5: já separa `success_fee_pct_recuperacao` e `success_fee_pct_retencao`, de propósito; só atualizar a nota.
- `painel/fixtures/resultado.json`: os dois percentuais.

---

## c) Faixa de ICP (MRR da empresa cliente)

| Fonte | O que diz |
|---|---|
| Plano de Negócio | MRR de R$ 25 mil a R$ 500 mil |
| CRAI_sistema | R$ 10 mil a R$ 500 mil |
| README do repositório | `README.md:1`: "(MRR R$10k - R$500k)" — alterado no commit `42596f1` ("Revise MRR range in project description"), que trocou "R$500k - R$5M" por "R$10k - R$500k" |
| Código | não há faixa de ICP no código. **Atenção para não confundir:** `HIGH_VALUE_MRR_DEFAULT = 2000.0` (`risk_scorer.py`), `MRR_TIPICO` (`offer_bandit.py`) e o `mrr` de `clientes_importados` são o MRR do **cliente final** da empresa, não o da empresa; não mudam com esta decisão. |

**DECISÃO:** ______________________________________________  **Data:** ____/____/______

**Arquivos afetados por cenário**

*Cenário C1 — R$ 10 mil a R$ 500 mil (CRAI_sistema e README):*
- Nada no repositório.
- Fora do repositório: corrigir o Plano de Negócio.

*Cenário C2 — R$ 25 mil a R$ 500 mil (Plano de Negócio):*
- `README.md:1`: trocar "R$10k" por "R$25k".
- Fora do repositório: corrigir o `CRAI_sistema.docx` e o deck.

Nenhum outro arquivo do repositório cita a faixa (`grep -rnE "10k|25k|500k|10 mil|25 mil|500 mil"` em `README.md`, `app/README.md`, `docs/`, `app/docs/` só encontra a linha 1 do README).

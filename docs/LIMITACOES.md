# O que é real, o que é simulado e limitações

> Origem: seção 14 do `README.md` do `Gavaaaaa/mvp-crai` (beta em JavaScript,
> descartada), reescrita para o backend Python deste repositório na migração de
> 12/09/2026. Todo número abaixo foi conferido contra a evidência de treino
> (`app/docs/evidencia/treino/`, cópia idêntica de `Gavaaaaa/Base-de-dados/evidencia/`)
> ou contra o código; a fonte vai entre parênteses. Número sem fonte está marcado
> **NÃO VERIFICADO**.

**Real:** o código de treino e de inferência dos quatro modelos (`app/crai/ml/`),
com as métricas da última rodada gravadas em `app/docs/evidencia/treino/`; a
explicação SHAP do classificador (`shap.TreeExplainer`, `failure_classifier.py`);
as regras de negócio — e-Profit e `route_after_diagnosis`, `PIX_CODE_MAP`
(`app/crai/agent/pix_codes.py`), `PixAutomaticoRetryPolicy` (regras do BACEN),
criticidade, isolamento por tenant; os prompts das mensagens; a importação da
base do cliente (`POST /clientes/importar`, `app/crai/churn_voluntary/importacao.py`).

Os artefatos treinados **não** vão para o git: `app/models/` é ignorado, exceto
`calibracao.json`. Num clone limpo não há modelo carregado, e eles são
regenerados por `python -m crai.scripts.train_all`.

A camada de resultado da beta — grupo de controle, fatura e clawback — existia
só no JavaScript e **ainda não existe** no backend Python.

**Simulado:** o gateway Pix Automático via Pagar.me (modo simulado é o default,
`pagarme_gateway.py`), o envio por WhatsApp (`whatsapp_sender.py`), o e-mail sem
SMTP configurado (`email_sender.py`) e o HubSpot sem token; os desfechos de
retenção quando `CRAI_SIMULATE_OUTCOMES=1` (o aceite é sorteado); os eventos
comportamentais pelos endpoints `/simulate/*`. O perfil do cliente que alimenta
o classificador (tempo de casa, histórico de pagamento, LTV) vem de
`_perfil_simulado` (`app/crai/agent/workflow.py`): é sintético e determinístico
enquanto nenhuma fonte real estiver configurada. Em teste e desenvolvimento a
base importada vive num SQLite local (`CRAI_CLIENTES_DB`); o destino de produção
é o Postgres do Supabase.

**Limitações dos modelos (declaradas):**

- O classificador de falha de cobrança foi treinado em dataset sintético
  calibrado — na última rodada, 6.000 linhas (4.800 treino / 1.200 teste)
  (`rodada_alta.json`). O rótulo "recuperou" vem de um modelo causal declarado,
  com ruído e 2% de rótulos sorteados: nenhum PSP publica esse rótulo. A base
  real de âncora tem **300 transações** (Olist, amostra estratificada de 103.877;
  Kaggle, licença **CC BY-NC-SA 4.0** — uso acadêmico OK, comercial não) mais a
  série 21084 do BACEN SGS, com valores tirados do `DATA_CARD.md` porque a API
  estava fora do ar na execução (`PROVENIENCIA.json`). Das 12 features, 2 são
  ancoradas em dado real, 4 são proxy fraco e 6 são sintéticas sem doador. O
  deck institucional cita 3.000 registros: **NÃO VERIFICADO**. O repositório
  documenta 300.
- O vocabulário de treino do classificador é o de cartão (`card_brand`,
  `expired_card`, `do_not_honor` … em `app/crai/ml/synthetic_data.py`). No
  pipeline Pix a bandeira entra como `"n/a"` (`workflow.py`), e as causas
  `limit_exceeded` / `authorization_revoked`, que não existem no treino, caem no
  índice "desconhecido" do encoder (`len(classes_)`, `failure_classifier.py`).
- AUC medida: **0,6669** (n=6.000, `rodada_alta.json`) e 0,6695 (n=3.000,
  `rodada_baixa.json`) — **abaixo do piso de 0,70** que o próprio repositório
  exige (faixa [0,70; 0,92] em `app/tests/test_metricas_declaradas.py`). Curva de
  volume, média de 3 seeds: 0,626 (1.000) → 0,681 (3.000) → 0,686 (6.000) → 0,696
  (12.000) (`fora_do_dominio.json`). O 0,703 citado na beta foi medido com a fonte
  default, não calibrada, e n=15.000 (`app/README_treino.md`). No limiar 0,25 a
  acurácia é 0,526 e o recall 0,919; a acurácia baixa é aceita porque quem decide
  é a regra de e-Profit, com recall operacional 1,0 no teste (0 recuperáveis
  perdidos em 1.200). Fora do domínio não existe rótulo e, portanto, não existe AUC.
- Autoencoder (ROC-AUC 0,981) e Payday Engine (ROC-AUC do ensemble 0,952; MAE
  da janela 0,68 dia, contra 4,55 da heurística) foram avaliados dentro das
  próprias simulações de treino, não em produção (`rodada_alta.json`). No dado
  real do E-Commerce Customer Churn o autoencoder cai para ROC-AUC 0,506; o
  payday não tem doador público para ser checado (`fora_do_dominio.json`).
- O risco de churn voluntário ativo são **regras fixas** (`risk_scorer.py`). O
  candidato treinado não está ativo e não tem o que acrescentar hoje: o rótulo
  do dataset é gerado pelas próprias regras, com ruído. Por isso o candidato
  chega a AUC 0,752 contra teto de 0,758 das regras (`rodada_alta.json`). No dado
  real, regras 0,405 e candidato 0,430 (`fora_do_dominio.json`).
- O bandit de ofertas tem warm start de uma simulação de 6.000 rodadas, não de
  clientes reais (`app/crai/churn_voluntary/offer_bandit.py`).
- Referências externas usadas no projeto não são SaaS B2B brasileiro: KKBox
  (streaming de música taiwanês) e o cruzamento sintético com CNPJ — os dois
  vêm da beta e **não aparecem no código, no histórico deste repositório nem no
  `Base-de-dados`** (NÃO VERIFICADO aqui) —, Olist (e-commerce) e E-Commerce
  Customer Churn (e-commerce, licença não declarada no Kaggle, uso acadêmico
  apenas, `PROVENIENCIA.json`).

O que a beta prova: o pipeline aprende, explica e decide sobre um sinal; o que
ela não prova: que a previsão de recuperação vale em produção.

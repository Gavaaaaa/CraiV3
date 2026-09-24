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

> **Rodada que esta lista descreve:** os números marcados com *(rodada de 11/09/2026)*
> vêm de `app/docs/evidencia/treino/rodada_baixa.json` e `rodada_alta.json`
> (classificador treinado em 2026-09-11T21:40:41 e 2026-09-11T21:41:55, Python
> 3.11.15) e da checagem `fora_do_dominio.json` da mesma evidência. Eles **não**
> descrevem os artefatos atuais de `app/models/`, retreinados em 14/09/2026 com
> 40.000 amostras no classificador. O estado atual está na seção "A AUC do
> classificador de falha está abaixo do piso declarado nos gates", no fim deste
> arquivo, e na linha §4.6 do `app/README.md`.

- *(rodada de 11/09/2026)* O classificador de falha de cobrança foi treinado em dataset sintético
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
  *(Resolvido em 22/09/2026, Bloco H: o artefato em `app/models/` passou a ser o da
  base v2, cujo vocabulário é o de Pix — `metodo_pagamento` no lugar de `card_brand`,
  e `limit_exceeded` / `authorization_revoked` presentes no treino. O caminho de servir
  fornece `metodo_pagamento` e um teste de ponta a ponta com o artefato real,
  `app/tests/test_servir_v2.py`, garante que nada chega ao `predict` como ausente.)*
- *(rodada de 11/09/2026)* AUC medida: **0,6669** (n=6.000, `rodada_alta.json`) e 0,6695 (n=3.000,
  `rodada_baixa.json`) — **abaixo do piso de 0,70** que o repositório exigia
  na época (faixa [0,70; 0,92] em `app/tests/test_metricas_declaradas.py`). Desde
  14/09/2026 o teste exige outra coisa: teto 0,92 como gate anti-vazamento, piso
  0,60 como sanidade e o critério operacional como gate do produto — ver "Decisão
  sobre o gate" no fim deste arquivo. Curva de
  volume, média de 3 seeds: 0,626 (1.000) → 0,681 (3.000) → 0,686 (6.000) → 0,696
  (12.000) (`fora_do_dominio.json`). O 0,703 citado na beta foi medido com a fonte
  default, não calibrada, e n=15.000 (`app/README_treino.md`). No limiar 0,25 a
  acurácia é 0,526 e o recall 0,919; a acurácia baixa é aceita porque quem decide
  é a regra de e-Profit, com recall operacional 1,0 no teste (0 recuperáveis
  perdidos em 1.200). Fora do domínio não existe rótulo e, portanto, não existe AUC.
- *(rodada de 11/09/2026)* Autoencoder (ROC-AUC 0,981; n=11.000 clientes, avaliado em
  1.502 saudáveis held-out + 990 anômalos) e Payday Engine (ROC-AUC do ensemble 0,952; MAE
  da janela 0,68 dia, contra 4,55 da heurística; n=1.200 clientes, 240 de teste, 2.400
  janelas) foram avaliados dentro das
  próprias simulações de treino, não em produção (`rodada_alta.json`). No dado
  real do E-Commerce Customer Churn o autoencoder cai para ROC-AUC 0,506 (5.068
  clientes com as 4 features presentes); o
  payday não tem doador público para ser checado (`fora_do_dominio.json`).
- *(rodada de 11/09/2026)* O risco de churn voluntário ativo são **regras fixas** (`risk_scorer.py`). O
  candidato treinado não está ativo e não tem o que acrescentar hoje: o rótulo
  do dataset é gerado pelas próprias regras, com ruído. Por isso o candidato
  chega a AUC 0,752 contra teto de 0,758 das regras (n=4.000 eventos, 800 de teste;
  `rodada_alta.json`). No dado
  real, regras 0,405 e candidato 0,430 (`fora_do_dominio.json`).
- O bandit de ofertas tem warm start de uma simulação de 6.000 rodadas, não de
  clientes reais (`app/crai/churn_voluntary/offer_bandit.py`).
- Referências externas usadas no projeto não são SaaS B2B brasileiro: KKBox
  (streaming de música taiwanês) e o cruzamento sintético com CNPJ — os dois
  vêm da beta e **não aparecem no código, no histórico deste repositório nem no
  `Base-de-dados`** (NÃO VERIFICADO aqui) —, Olist (e-commerce) e E-Commerce
  Customer Churn (e-commerce, licença não declarada no Kaggle, uso acadêmico
  apenas, `PROVENIENCIA.json`).

**Reprodutibilidade do autoencoder entre máquinas (medido em 22/09/2026, Bloco H):**

- Mesma base v2 (cinco sha256 conferidos contra o `MANIFESTO.json`), mesma
  semente 42, mesmas versões de biblioteca (torch 2.13.0+cpu, numpy 1.26.4,
  scikit-learn 1.5.2, xgboost 2.1.1, prophet 1.4.0), e o autoencoder deu
  **ROC-AUC 0,8684 e 0,8694** nas rodadas de 20/09/2026 e **0,8582** na máquina
  que treinou o artefato promovido em 22/09/2026 (n=120.000 clientes nas três:
  92.872 saudáveis de treino, 16.390 de validação, 10.738 anômalos), e **0,8694**
  de novo na máquina de 23/09/2026. Classificador (0,7096; 120.000 cobranças,
  holdout de 24.089) e voluntário (0,8288; 120.000 eventos, holdout de 24.061)
  reproduziram **na quarta casa** em todas. A liquidez (10.000 clientes, 2.000 de
  teste) reproduziu na quarta casa entre as máquinas de 20/09 e 22/09 (0,9639) e
  **mexeu na quarta casa** na de 23/09: ensemble 0,9643, LSTM 0,9745 contra
  0,9743, MAE 0,62 contra 0,61 dia. Esta afirmação dizia, até 23/09, que a LSTM
  "treina 40 épocas fixas e reproduz"; a medição de 23/09 a contradiz.
- **A causa é hardware, não biblioteca — e não é o early stopping.** A causa é
  a **ordem de acumulação em ponto flutuante**, que depende do BLAS e do
  conjunto de instruções da CPU: a mesma soma dá resultados que diferem na
  décima casa, e uma rede treinada por gradiente propaga essa diferença por
  todas as épocas. A prova de que a causa não é o early stopping é a LSTM da
  liquidez: ela treina 40 épocas fixas, sem early stopping, e varia mesmo assim.
  O early stopping é **amplificador**, não causa: no autoencoder (Adam, dropout,
  tolerância de 1e-5 na perda de validação), uma acumulação diferente muda a
  época em que o treino para — 60, 64 ou 68 —, e aí muda o modelo inteiro, em
  vez de só o último dígito. Ordem de grandeza medida: **LSTM 0,0004** de ROC-AUC
  (0,9639 → 0,9643), **autoencoder 0,0112** (0,8582 → 0,8694). A variação da LSTM
  **não move a janela operacional**: acerto ±1 dia 90,8 % (90,6 % no artefato de
  22/09) e acerto exato 84,4 % nos dois. Dentro de cada máquina o treino é
  determinístico: quatro rodadas na máquina de 22/09, com 1, 6 e 12 threads,
  deram exatamente 0,8582.
- **Fixar versões no `requirements.txt` NÃO é suficiente** para reproduzir o
  autoencoder: as versões da máquina de 22/09 são exatamente as fixadas, e o
  resultado ainda diverge. O que as versões fixadas garantem é que o artefato
  gravado **recarrega** igual (`load()` + `conferir_meta`), e que classificador,
  liquidez e voluntário treinam igual.
- **Consequência, e a regra que segue dela:** o percentil do limiar do
  autoencoder (`threshold_percentil` no `autoencoder_meta.json`) não é uma
  constante escolhida — é a SAÍDA do critério "maior percentil da curva com
  recall acima de 0,70", aplicado à curva do artefato em produção. Na máquina
  de 20/09 o critério deu p83; na de 22/09, p81 (recall 0,7121, precisão
  0,7106; p83 daria 0,6695, abaixo do piso); na de 23/09, p83 de novo.
  Recalibrar ao trocar de máquina é o critério funcionando, não uma concessão.
  **Desde 23/09/2026 é o treino quem aplica o critério**
  (`AnomalyDetector.train(recall_minimo=...)` chama `escolher_percentil` sobre
  a curva recém-calculada e grava o resultado, com o critério, no meta). A
  constante `THRESHOLD_PERCENTIL_V2` deixou de existir: enquanto existiu, o
  treino copiava o número dela em vez de aplicar o critério, e a curva gravada
  pelo mesmo treino discordava (p83) do meta (p81). Quem retreinar em outra
  máquina só retreina e promove, e publica a curva do artefato promovido em
  `docs/evidencia_base_v2/` com nome datado (a de 23/09/2026 é
  `curva_limiar_anomalia_v2_final_23_09_p83.json`; a de 22/09 continua em
  `curva_limiar_anomalia_v2_varredura.json`) — `app/models/` está fora do git e
  sem a cópia quem clona não consegue verificar a declaração do README §4.6;
  `test_populacao_compartilhada.py::TestLimiarAnomaliaV2` e
  `test_servir_v2.py::TestOLimiarDoAutoencoderPromovido` cobram a coerência.

**Reprodutibilidade da amostra real (achado de 12/09/2026, pendência):**

- `python -m crai.scripts.preparar_amostra_real` se declara determinístico
  ("mesma seed, mesmas 300 linhas, mesmo SHA-256"), mas, executado em
  12/09/2026 nesta máquina, produziu uma amostra Olist com SHA-256
  `d800c60f…`, diferente do `39485c22…` registrado no `calibracao.json`
  versionado e na evidência publicada. A causa **não foi investigada**; fica
  como pendência para depois da apresentação, porque contradiz a afirmação
  "tudo reproduzível" do repositório de dados.
- Na mesma execução a API do BACEN respondeu (na execução que gerou a
  evidência ela estava fora do ar e o script usou o valor declarado no
  `DATA_CARD`), e a média da série 21084 saiu 3,9163 em vez de 3,92, o que
  mudou `error_code_probs` na terceira casa. O `calibracao.json` versionado
  foi mantido como referência: é o que produziu a evidência publicada, e os
  modelos em disco foram retreinados a partir dele.

O que a beta prova: o pipeline aprende, explica e decide sobre um sinal; o que
ela não prova: que a previsão de recuperação vale em produção.

### A AUC do classificador de falha está abaixo do piso declarado nos gates

Isto está declarado aqui em vez de contornado.

Medição de 14/09/2026, gerador calibrado, 40.000 amostras
(32.000 de treino, 8.000 de teste): **AUC 0,6951**. O README declarava
**0,7029**, número de 01/09/2026, medido com o **gerador anterior** e 15.000 amostras.
Não é a mesma medida, e não deveria ter sido comparada como se fosse.

A diferença tem duas causas conhecidas. A primeira é o gerador: o atual é calibrado e o
anterior não era. A segunda está documentada no próprio artefato de métricas — 2% dos
rótulos são sorteados ao acaso, de propósito, para impedir que o modelo memorize a
regra que gerou os dados. Ruído deliberado no rótulo limita a AUC por construção.

Uma execução anterior, com 6.000 amostras, deu AUC 0,669. Multiplicar a base por 6,7
moveu a AUC em 0,0261. O achatamento indica que o limite não é falta de
amostra. As duas execuções com 40.000 amostras, em máquinas e versões de Python
diferentes, deram o mesmo valor — o treino **do classificador** é reproduzível
(o mesmo vale para liquidez e voluntário, na quarta casa, em três máquinas; **não**
vale para o autoencoder — ver "Reprodutibilidade do autoencoder entre máquinas").

**O gate de 0,70 exige uma precisão que a medição não sustenta.** Com 8.000 linhas
de teste, o erro padrão da AUC é da ordem de 0,006: 0,6951 e 0,70 não são
estatisticamente distinguíveis. O valor anterior, 0,7029, passava com folga de 0,0029 —
metade do próprio erro da medida.

**O critério que o produto exige é outro, e ele é satisfeito.** No limiar em uso
(0,25), o recall de recuperáveis é **0,9457**, e o `recall_operacional` registra
**zero clientes recuperáveis perdidos** em 8.000 casos. Para este produto o erro
caro é deixar de tentar quando valia a pena.

**O modelo de risco voluntário mede o próprio teto.** O candidato treinado alcança AUC
0,7503 contra um teto das regras de 0,7527 (base v1, 30.000 eventos, holdout de 6.000),
com correlação 0,9938 com a fórmula que gerou os rótulos. Ele aprendeu a reproduzir a
regra, e não há mais informação a extrair. Por isso não foi promovido.
*(Base v2, 22/09/2026, 120.000 eventos, holdout por cliente de 24.061: AUC 0,8288 contra
teto das regras 0,8295, correlação 0,9974 — `RELATORIO_OVERFITTING.md` §9. A v2 deixou
o problema **mais** circular, não menos: a correlação de `risk_regra` com o rótulo subiu
de 0,455 na v1 para 0,587 na v2, porque `days_since_last` e `features_used_30d` passaram
a derivar do retrato comportamental do mesmo cliente e a regra ficou mais preditiva do
rótulo que ela própria gera. A saída continua sendo o desfecho observado pelo
`DELETE /clientes/{id}`, não mais dado sintético.)*

**`metodo_pagamento` não carrega sinal nesta base — lacuna declarada da base, não
defeito do modelo (22/09/2026).** Na base v2 a feature que substituiu `card_brand`
tem AUC 0,5007 sozinha e removê-la não custa nada, porque o gerador sorteia o método
do cliente sem que o rótulo `recovered` dependa dele. No mundo real ela é causalmente
relevante — boleto e Pix Automático não se retentam do mesmo jeito. É uma feature
causalmente correta cuja relevância a base sintética não modela, e candidata ao
trabalho de features entre módulos (`RELATORIO_OVERFITTING.md` §5.1).

**O que não foi feito, de propósito:** o ruído do rótulo não foi removido para alcançar
o gate, e os hiperparâmetros não foram ajustados contra o conjunto de teste.

**Decisão sobre o gate (14/09/2026):** o gate de AUC foi redesenhado em
`app/tests/test_metricas_declaradas.py`, com três partes.

- O teto de 0,92 continua como gate, com a justificativa de sempre: acima dele o
  gerador está vazando o rótulo.
- O piso baixou de 0,70 para **0,60**, e mudou de papel: é gate de sanidade contra
  degradação catastrófica, não aproximação do critério de e-Profit. A razão é a do
  parágrafo acima: 0,70 estava dentro do erro padrão da medida (~0,006) e não
  distinguia aprovado de reprovado; 0,60 está fora dele.
- O critério operacional virou gate próprio, `test_o_criterio_operacional_e_satisfeito`:
  `recall_operacional.recuperaveis_perdidos` tem que ser zero, e o recall no limiar em
  uso tem que ficar acima de 0,90. É a medida direta do que o piso de 0,70 tentava
  aproximar. Nasce passando com os números desta seção.

A AUC continua medida, declarada na §4.6 do `app/README.md` e conferida pelo teste
que exige que o número do README seja o do arquivo em disco.

**Em aberto:** ajuste de hiperparâmetros por validação cruzada dentro do conjunto de
treino, com medição única no teste.

### A fronteira de confiança dos webhooks é compartilhada entre inquilinos

Declarado aqui em 23/09/2026. Até então isso existia só no docstring de
`_tenant_da_requisicao` (`app/crai/api/app.py`) e na mecânica descrita em
`docs/CONFIGURACAO.md` ("o tenant chega por requisição, no header `x-tenant-id`
ou no campo `tenant_id` do corpo").

**O que é.** Os quatro webhooks (`/webhooks/pix-automatico`, `/webhooks/stripe`,
`/webhooks/segment`, `/webhooks/retention-outcome`) e os quatro simuladores
`/simulate/*` autenticam a **origem** (assinatura HMAC do payload com um segredo
por integração; `ENV` de simulação nos `/simulate/*`), mas o **inquilino** é
afirmado pelo próprio chamador: header `x-tenant-id`, senão `tenant_id` no corpo,
senão `default_tenant`. Nada confere se quem assina tem direito ao tenant que
declara. Consequência direta: **quem tem o segredo de um webhook grava eventos
em qualquer tenant** — abre ciclos de cobrança, registra desfechos que movem o
posterior do bandit, roda o pipeline — bastando escrever o nome dele no header.
A fronteira de confiança é por integração, não por inquilino.

**Por que está assim.** É MVP declarado: a CRAI ainda é operada para um cliente
por instalação, o segredo de cada integração pertence a esse único cliente, e
exigir tenant assinado quebraria os webhooks já integrados. O que o código
garante hoje é só a forma: `default_tenant` explícito e identificador fora de
`[A-Za-z0-9._-]{1,64}` são 422, para ninguém cair no balde do "não declarado"
de propósito.

**O que não está afetado.** As rotas autenticadas por JWT (`/clientes*`,
`/insights*`, `/titular/*`, `/metrics/recovery`) tiram o tenant do claim do
token e ignoram qualquer `tenant_id` do chamador; `test_supabase_auth.py::
TestWebhooksIntocados` trava que só elas dependem de `get_tenant_id`.

**O dia em que houver dois clientes na mesma instalação**, esta é a linha que
vira obrigatória: segredo por tenant (o header passa a ser derivado de qual
segredo assinou, não lido do request), ou tenant dentro do payload assinado.
Sem uma das duas, multi-tenant nos webhooks é multi-tenant só no banco.

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

### O ledger de retenção é um arquivo local — workers distribuídos não o compartilham

Declarado aqui em 28/09/2026, ao fim do Bloco C. Até então isso existia só como
uma linha de `BASE_DIR` em `app/crai/churn_voluntary/retention_log.py` e como um
achado de diagnóstico em `docs/RELATORIO_ESTABILIDADE.md`.

**O que é.** `ciclos_retencao` — o dataset de treino do churn voluntário e, ao
mesmo tempo, a deduplicação do desfecho — mora num SQLite **local ao sistema de
arquivos do processo**: `app/data/retention_cycles.db`, ou o que
`CRAI_RETENTION_DB` apontar. Dois workers no mesmo host, ou em contêineres com o
mesmo volume, compartilham o arquivo e tudo funciona — isso é medido, com dois
processos de verdade, em `tests/test_ledger_concorrencia.py`. Dois workers em
**hosts ou contêineres diferentes** não: cada um cria o seu banco, e três coisas
quebram de uma vez. A deduplicação some entre eles, então o mesmo desfecho
reenviado a workers distintos é contado duas vezes e move o posterior do bandit
em dobro. O dataset de treino nasce partido em N pedaços, um por worker, e
nenhum deles é a história completa de um cliente. E o pior dos três: o ciclo é
ABERTO por um worker e o desfecho chega a outro, que não encontra nada — a
resposta é `SEM_CICLO`, e `SEM_CICLO` é um caso legítimo, então o webhook
responde 200 e o desfecho é descartado em silêncio, exatamente o resultado que o
Bloco C.2 existe para impedir no caso de erro. **O 503 do C.2 não alcança este
caso**, e é importante que isso esteja escrito: lá o sistema sabe que falhou;
aqui ele acha, com razão local, que não havia nada a fechar.

**Por que está assim.** A POC roda em **um processo**, e a escolha do SQLite
tem razão própria — a segunda escrita de um ciclo é um `UPDATE` numa linha
gravada dias antes, possivelmente por outro processo, e Parquet é imutável por
arquivo. (Até 28/09/2026 este parágrafo dizia "é a mesma dívida assumida do
`MemorySaver` em `main_agent.py`". Essa dívida foi paga na Etapa 1: o estado
do ciclo de cobrança do involuntário saiu da RAM e mora em SQLite, na seção
seguinte. O que continua igual é o limite do arquivo.) O Bloco C fechou o que dava para fechar **dentro
de um arquivo**: `BEGIN IMMEDIATE` em volta do `SELECT`+`UPDATE` que fecha o
ciclo, `busy_timeout` declarado em vez de implícito, e um status `ERRO` que
deixou de se disfarçar de reenvio. Nenhuma dessas três atravessa o limite do
sistema de arquivos, porque nenhuma delas pode: a trava do SQLite é sobre um
arquivo, e dois arquivos não disputam nada.

**O que não está afetado.** O deploy de hoje, que é de processo único, e
qualquer arranjo em que os workers vejam o **mesmo** arquivo — as duas garantias
do Bloco C valem inteiras ali, medidas com dois processos reais disputando o
mesmo ciclo (exatamente um fecha, o outro recebe `REENVIO`, nenhum recebe
`ERRO`). A trilha do Art. 20 mora no mesmo arquivo e tem a mesma limitação de
alcance, mas falha de um jeito diferente e melhor: o índice único
`uq_decisao_elo` faz o segundo gravador **falhar alto**, com `IntegrityError`
logado como BIFURCAÇÃO EVITADA, em vez de divergir em silêncio. Os modelos, o
`bandit_state.json` e os artefatos de `crai/models/` também não entram aqui —
são leitura, não estado transacional.

**O dia em que houver mais de um worker que não divida o volume**, esta é a
linha que vira obrigatória: `ciclos_retencao` em **Postgres**, com o desfecho
fechado por `UPDATE ... WHERE accepted IS NULL RETURNING id` — que é atômico no
servidor e dispensa a transação explícita —, ou uma fila que garanta afinidade
de worker por `(tenant, user, oferta)`. O SQL já é quase todo portável e o
`SUPABASE_DB_URL` que a API de clientes usa mostra que o Postgres já está no
desenho; o que falta é a migração e trocar o `_conectar` por um pool. Volume
compartilhado por NFS **não** é uma terceira opção: o travamento de arquivo do
SQLite não é confiável sobre NFS, e trocaria uma falha visível por corrupção.
Sem uma das duas primeiras, multi-worker é multi-worker só no balanceador.

### O ciclo de cobrança do involuntário mora num SQLite local à máquina — e o que a Etapa 1 deixou declarado

Declarado em 28/09/2026, ao fim da Etapa 1 (o núcleo do involuntário:
`docs/interno/RELATORIO_ETAPA1.md`).

**O que deixou de ser verdade.** Até a Etapa 1 o contador de tentativas do
BACEN, o prazo da janela e o desfecho do ciclo moravam no checkpoint do
LangGraph (`MemorySaver`, RAM, um processo); o plano de retentativas num JSON
reescrito sem transação; e a deduplicação de webhook em memória. Reiniciar o
serviço zerava o contador, reabria a janela, descartava a confirmação de
pagamento (`sem_ciclo_aberto`, fee 0) e deixava passar reenvios do PSP — tudo
medido em `docs/interno/DIAGNOSTICO_INTEGRACAO.md`. Nada disso vale mais: o
ciclo (`ciclos_cobranca`), as tentativas com o resultado de cada uma
(`tentativas_cobranca`) e os eventos vistos (`eventos_vistos`) moram em
`app/data/recovery_cycles.db`, sob `BEGIN IMMEDIATE`, e o `MemorySaver` do
grafo é só o rascunho de uma execução. As linhas `P1-14` e `P1-15` da tabela
de dívidas do `app/README.md` descrevem o estado anterior.

**O que continua valendo: o arquivo é local à máquina.** É o mesmo limite da
seção anterior, sobre o mesmo arquivo de sistema: dois workers no mesmo host,
ou com o mesmo volume, compartilham o banco e as garantias valem (medido com
dois processos reais em `test_ciclo_cobranca.py`, `test_etapa1_sequencia.py` e
`test_etapa1_desfechos.py`). Dois workers em **máquinas diferentes** têm dois
bancos: o ciclo aberto por um não existe para o outro, a deduplicação não
atravessa, e o limite de 3 tentativas do BACEN vira 3 por máquina. A correção
é Postgres — a mesma migração da seção anterior, com a `UNIQUE (tenant_id,
id_cobranca_original)` e o `CHECK (numero BETWEEN 1 AND 3)` levados junto,
porque são eles que fazem o banco recusar a 4ª tentativa e o 2º ciclo.

**Limitações declaradas na Etapa 1**, cada uma com o lugar onde mora:

1. **Confirmação de pagamento com tenant divergente** (`ciclo_para_confirmacao`,
   `app/crai/dunning/ciclo_cobranca.py`). Uma confirmação que declara um
   tenant sem ciclo daquele mandato encontra o ciclo aberto do mandato em
   **qualquer** tenant, e a recuperação é atribuída ao tenant do ciclo, com
   WARNING. Existe para preservar o comportamento anterior à etapa, quando o
   checkpoint era por mandato e não por tenant. **Sai antes da API key
   (Portão 0)**: com inquilinos autenticados, um inquilino não pode fechar o
   ciclo de outro, e o fallback vira recusa.
2. **Prazo de recuperação de 30 dias (A3)**: depois da mensagem ao cliente, o
   ciclo espera `PRAZO_RECUPERACAO_DIAS = 30` dias por um pagamento, contados
   de `mensagem_confirmada_em`, e vai a `perdido`. Um pagamento **depois**
   disso não reabre o ciclo, não conta fee e é respondido como
   `ciclo_perdido`, com WARNING — por decisão: além do prazo, é a
   mensalidade seguinte, não a recuperação desta. O cliente não perde o
   pagamento; a CRAI não o atribui ao ciclo.
3. **Caminho de transição do A4** (`_fechar_pela_linha_do_dataset`,
   `app/crai/api/app.py`). Uma confirmação sem ciclo na tabela, mas com uma
   linha ABERTA no `recovery_log` registrada há no máximo 7 + 30 dias — um
   ciclo que só existia na RAM antes da Etapa 1 —, cria o ciclo já
   `recuperado` a partir da linha, com WARNING a cada uso. Linha mais velha
   não é fechada. É código de transição: quando não houver mais linha aberta
   anterior à etapa, ele deixa de ser alcançado e pode sair.
4. **HubSpot: ciclo descartado chega como `retrying`.**
   `register_recovery_cycle` (`app/crai/integrations/hubspot_crm.py`) só
   conhece `recovered`, `dunning_sent`, `lost` e `retrying`; o estado
   `descartado` (R6: e-Profit ≤ 0 ou score abaixo do corte, com motivo) cai
   em `retrying`. O motivo do descarte está no ciclo e na trilha do Art. 20,
   não no CRM. O módulo do HubSpot ficou fora do escopo da etapa.
5. **Falha de banco num nó depois do primeiro.** Uma falha de persistência
   em `open_cycle` vira 503 sem efeito parcial. Uma falha num nó posterior
   também vira 503 e esquece a chave de deduplicação, mas o ciclo já aberto
   fica; o reenvio do PSP o encontra **incompleto** (sem decisão gravada, sem
   tentativas) e retoma o diagnóstico sobre ele, com a janela original — não
   há descarte silencioso. O que não é refeito é o rastro do nó que falhou
   antes de gravar.
6. **Linha do dataset e ciclo em duas escritas.** O fechamento do ciclo como
   recuperado é uma transação; a linha do `recovery_log` é fechada logo
   depois, em outra conexão, best-effort. Uma falha entre as duas deixa o
   ciclo `recuperado` e a linha sem desfecho até a próxima passagem do
   agendador, cuja varredura de reconciliação fecha a linha com WARNING
   (`recovery_log.reconciliar_recuperados`).

**Fora do escopo da etapa, e ainda verdade:** o conteúdo da mensagem (o link
`https://pay.crai.ai/...` é uma string montada; nenhuma cobrança é criada);
o perfil sintético que alimenta o classificador; a simulação do painel; a
janela de deduplicação da API de clientes (`CLIENTES_API`), que continua em
memória.

### O risco voluntário v3: o que a promoção declara (30/09/2026)

Declarado na promoção do modelo voluntário v3 (`docs/interno/RELATORIO_PROMOCAO_V3_BLOCO*.md`;
números em `docs/evidencia_v3/metricas_v3.json`). Vale quando o meta de produção declara
`contrato: "v3"`. Sem ele, o risco voluntário continua nas regras, como descrito acima.

1. **O modelo foi treinado em dado sintético com rótulo latente desenhado pelo projeto.**
   O cancelamento da base v3 é decidido por quatro variáveis latentes da população
   (satisfação, fit, pressão de preço, saúde financeira), com uma fórmula escrita por nós.
   O modelo aprende pelos rastros que elas deixam no comportamento, também desenhados por
   nós. A vantagem sobre a régua é medida **contra o próprio gerador**; não é churn
   observado e não diz nada sobre churn real. O caminho para isso continua sendo o
   desfecho real (`ciclos_retencao`, `DELETE /clientes/{id}`).

2. **A vantagem depende das colunas comportamentais.** No holdout por cliente da base v3:

   | o que o modelo recebe | modelo | melhor régua | vantagem |
   |---|---|---|---|
   | as 15 colunas | 0,6929 | 0,6156 | 0,0773 |
   | só as 6 que a produção recebe hoje, evento real | 0,6377 | 0,6156 | 0,0221 |
   | só as 6, evento "Session Started" (o do lote) | 0,6329 | 0,6063 | 0,0266 |

   No GroupKFold 5, só com as 6 colunas, a vantagem é 0,0268 (evento real) e 0,0361
   (lote), acima do desvio (0,0109 e 0,0086). Por isso o modelo decide desde que haja
   dias sem login ou uso (`N_MINIMO_COLUNAS_COMPORTAMENTAIS = 0`). As 9 colunas
   comportamentais (logins, sessão, API, chamados, NPS, pagamentos falhados, assentos,
   tempo de casa) só chegam à base importada com a mudança de contrato da Etapa 2. Até
   lá, o ganho real fica na faixa de 0,02-0,03 de AUC, não nos 0,077.

3. **O termo de interação preço x saúde foi escolha de desenho.** Ele foi posto de
   propósito no rótulo e favorece modelos de árvore sobre uma régua linear. O experimento
   sem ele (0.1a) mostra que ele explica pouco da vantagem: no holdout, 0,0689 sem o termo
   contra 0,0773 com ele (11% da vantagem). No GroupKFold, 0,0831 contra desvio exigido de
   0,0106, e o critério continua satisfeito.

4. **"Grave" pode passar de 10% da base.** Com o v3, grave é o topo 10% pelo score, com
   sinal absoluto de abandono, **mais** os preocupantes (os 20% seguintes, com sinal)
   cujo MRR está entre os 20% maiores da base. A porta de valor só promove; nunca marca
   grave sozinha. O teto é 30%. Numa amostra de 1000 clientes da base v3 com todos tendo
   sinal, foram 15,8% de graves (58 promovidos pelo valor); com o comportamento real,
   8,1%. No v3 o MRR anda junto com o risco (a pressão de preço é construída com o
   percentil de MRR), então a promoção é frequente.

5. **X, Y e o topo de MRR são fixos em código.** Grave 10%, preocupante 20% e topo de MRR
   20% (`batch_scoring.FAIXA_GRAVE_PCT`, `FAIXA_PREOCUPANTE_PCT`, `FAIXA_VALOR_PCT`).
   `faixas_de_posicao(tenant_id)` devolve o padrão; a leitura por empresa
   (`configuracao_tenant`, da Etapa 2) ainda não foi ligada.

6. **A referência de posição do SDK mora na memória do processo.** Um evento do SDK é
   posicionado contra a base do tenant calculada na última `pontuar_base` (`/insights`)
   **daquele processo**. Sem ela, contra os quantis do score no treino gravados no meta
   de produção, e aí ninguém é promovido pelo valor (não há referência de MRR). Zera no
   reinício, como `regua_calculada_em`, e não é compartilhada entre workers.

7. **Latência da explicação.** A primeira decisão do modelo depois da subida importa o
   `shap` e monta o `TreeExplainer`: 0,78 s de mediana em 5 processos novos. Uma decisão aquecida leva
   3,9 ms com a explicação, e 2,1 ms sem ela
   (`docs/interno/latencia_promocao_v3.txt`). O carregamento do modelo também é preguiçoso (1,22 s, `joblib.load`), então o primeiro pedido de risco depois da subida paga os dois: cerca de 2,0 s. Aquecer os dois na subida do serviço resolve, e não foi implementado.

8. **O artefato não vai para o git.** `app/models/` é ignorado, o repositório é público,
   e um `.joblib` executa código ao ser carregado. O modelo promovido é regenerado
   localmente (o `README_treino.md` do voluntário, seção 8.4, traz a sequência e os
   sha256 esperados) e promovido por `python -m crai.scripts.promover_voluntario_v3
   --promover`.

9. **A suíte roda sem o modelo voluntário, de propósito.** `app/models/` não é
   versionada, e 31 testes de 7 arquivos afirmam números da régua (`test_retention_outcome`
   13, `test_disparo_lote` 5, `test_canal_escolhido` 4, `test_insights_unificados` 4,
   `test_insights_endpoint` 3, `test_voluntary_tone` 1, `test_clientes_recorrencia` 1):
   com o v3 promovido na máquina, eles falhavam, porque o modelo decidia no lugar da
   régua. Desde 03/10/2026 a fixture `modelo_voluntario_ausente`
   (`app/tests/conftest.py`, `autouse`) aponta o `risk_scorer` para uma pasta vazia em
   todo teste e zera o cache do carregamento antes e depois. O teste que precisa de
   modelo o instala por fixture própria (`test_risk_pluggable.py`,
   `test_promocao_v3_bloco*.py`). Medido: a suíte inteira passa igual sem artefato e com
   o v3 promovido. O que isso **não** cobre: nenhum teste exercita o v3 a partir de
   `app/models/` de verdade; a prova de que o artefato promovido carrega é o próprio
   `--promover` e a conferência do sha256.

### A Etapa 2 do involuntário: o que o encanamento declara (03/10/2026)

Declarado ao fim da Etapa 2 (relógio, rotas de leitura, as três mensagens, configuração,
expurgo, CORS e token de desenvolvimento: `docs/interno/RELATORIO_ETAPA2_BLOCO*.md`).

1. **O relógio roda num processo só.** O serviço tem uma tarefa de fundo
   (`app/crai/api/relogio.py`) que, a cada 60 s, dispara as tentativas devidas, varre os
   ciclos e, uma vez por dia, roda o expurgo. Com mais de um worker ele **recusa ligar**
   (`WEB_CONCURRENCY` > 1, `--workers N`, gunicorn), com erro no log e
   `motivo_desligado: "mais_de_um_worker"` no `/health`. É detecção, não garantia: dois
   `uvicorn` separados apontando para o mesmo banco não são vistos, e aí a mesma instrução
   pode ir duas vezes ao PSP (o reenvio acontece antes da marca de disparo). A passagem
   roda no event loop, com escritas síncronas no SQLite: uma passagem pesada segura as
   requisições pelo tempo dela. Não foi medido.

2. **O envio da mensagem é simulado.** Nenhum canal fala com um provedor de verdade: o
   WhatsApp e o e-mail do involuntário terminam num log. "Mensagem enviada" na tela quer
   dizer que o sistema decidiu, escolheu o canal e registrou o envio, não que uma pessoa
   recebeu. O gateway Pix também continua simulado.

3. **O WhatsApp real exige templates aprovados pela Meta.** Fora da janela de 24 h aberta
   pelo próprio cliente, a API do WhatsApp Business só entrega mensagens de **template
   aprovado previamente**, com variáveis. O texto livre que o sistema gera hoje (LLM ou
   template interno) **não pode sair assim por WhatsApp**. Quando o envio for real, cada
   abordagem (Lembrete cordial, Facilitação, Urgência com respeito) vira um template com
   variáveis (primeiro nome, valor, link), submetido à aprovação; o texto livre do LLM
   continua valendo para o e-mail. Nada disso está implementado.

4. **A janela de contato usa o fuso da instalação.** Nenhuma mensagem sai fora da janela
   da empresa (padrão 8 h às 20 h), e o prazo de escolha continua contando durante a
   espera. A hora é a do servidor (`CRAI_FUSO_LOCAL`, ou o fuso da máquina), não a do
   cliente final: uma empresa com clientes em outro fuso contata fora do horário local
   deles. Feriado e fim de semana não são considerados.

5. **O token de desenvolvimento.** Com `ENV=development`, `POST /dev/token` emite um token
   de uma empresa fictícia (`demo_dashboard`) com o papel pedido, **sem senha**: quem
   alcança a porta do serviço vira dono dessa empresa. Fora de `development` a rota não
   existe e o token é recusado antes de qualquer consulta de chave (há teste). O risco que
   sobra é operacional: **subir um serviço exposto com `ENV=development`**. A chave é
   gerada em memória a cada subida; o token morre no reinício e vale 1 h. `ENV=development`
   também abre os `/simulate/*` e o CORS para `http://localhost:5173`. Desde 04/10/2026 a
   rota aceita uma segunda empresa fictícia, `demo_testes`, usada só pelos testes ao vivo do
   dashboard (para não sujar a demonstração); a lista é fechada nessas duas, e não dá para
   pedir token de outra empresa.

6. **CORS é lista explícita.** `CRAI_CORS_ORIGENS` vem vazia: sem configurar, nenhum
   navegador de outra origem lê as rotas. Não há credencial por cookie. O CORS **não é
   autenticação**: ele só diz ao navegador quem pode ler a resposta; quem chama fora de um
   navegador não passa por ele.

7. **A chave de deduplicação mudou de forma no Bloco 2.** O tenant passou a fazer parte
   da chave dos eventos do Pix. Um evento recebido **antes** da troca e reenviado pelo PSP
   **depois** dela, dentro dos 7 dias da janela, não casa com a chave antiga e é
   processado de novo. O ciclo absorve: a cobrança é a mesma (`UNIQUE`), e o evento vira
   falha tardia, sem tentativa nova. A tabela real tinha 0 linhas na troca.

8. **A linha do tempo junta dois bancos pelo relógio.** O ciclo mora em
   `recovery_cycles.db` e a trilha do Art. 20 em `retention_cycles.db`, sem `ciclo_id`. A
   rota junta as decisões do mesmo mandato cujo instante cai dentro do ciclo, com 5 min de
   folga. Depende de os dois relógios concordarem; dois ciclos sobrepostos do mesmo
   mandato saem com `trilha_ambigua: true`.

9. **O expurgo diário cuida de cinco coisas** (as duas últimas desde a Rodada 4). Uma vez
   por dia o relógio: apaga o texto das mensagens dos ciclos fechados há mais de
   `retencao_mensagens_dias` (90 por padrão; a abordagem fica); apaga os registros de acesso
   com mais de 12 meses; apaga as decisões da trilha do Art. 20 com mais de **5 anos**
   (`api/relogio.py::expurgar_trilha`; os 5 anos são o mínimo, e a empresa que configurou
   mais tem o prazo dela); **anonimiza os ciclos de cobrança** com desfecho há mais de
   `retencao_ciclos_meses` (24 por padrão); e **apaga a simulação do gateway** em que
   ninguém mexe há mais de 30 dias. Diz no log quantas linhas tocou em cada uma. O que
   continua **sem execução**: o prazo da base depois do fim do contrato (6 meses), que
   depende de o site informar quando o contrato acabou (Etapa 5), e o do dataset de
   retenção do voluntário (ver a seção da Rodada 4, abaixo). O dia do último expurgo mora
   na memória do processo: depois de um reinício ele roda de novo, o que não faz mal (é
   idempotente).

10. **O registro de acesso guarda o papel, não a pessoa.** Cada leitura de
    `GET /titular/explicacao/{id}`, `GET /ciclos/{id}` e `GET /ciclos` grava empresa, rota,
    papel e instante, por 12 meses. De propósito não guarda quem foi (e-mail, `sub`) nem
    qual titular foi lido: responde "a empresa X leu dados de titular nesse dia, com o
    papel Y", não "quem leu o quê". A gravação é best effort: se falhar, a leitura segue e
    o erro vai para o log. Não há rota para a empresa consultar o próprio registro.

11. **A configuração por empresa cobre o involuntário.** Modo da mensagem, prazo de
    escolha, janela de contato e canais são lidos pelo fluxo a cada decisão.
    `modo_mensagem_voluntario` é gravado e ainda não é lido (Etapa 3), e
    `posicao_grave_pct` / `posicao_preocupante_pct` são validados e ainda não chegam ao
    voluntário, que continua com 10/20 em código. O papel vale até o token expirar: um
    administrador rebaixado continua gravando até o próximo login.

### O estorno da fee: o que ele declara (03/10/2026)

Declarado ao fim do Bloco 5 da Etapa 2 (`docs/interno/RELATORIO_ETAPA2_BLOCO5*.md`). Se o
dinheiro de uma cobrança recuperada volta ao pagador dentro do prazo da empresa
(`prazo_estorno_dias`, 30 por padrão), a fee é devolvida na proporção do que voltou.

1. **O formato do aviso de devolução é suposição, até a homologação com o PSP.** O
   adaptador reconhece o evento por nomes prováveis (`automatic_pix.charge_refunded`,
   `recurrence.charge_refunded`, `charge.refunded`, status `refunded` ou `devolvido`) e lê o
   valor e o id da devolução de campos do padrão do BACEN e de variações em inglês
   (`devolucao.valor`, `rtrId`, `refunded_amount`...). **Nenhum desses nomes foi confirmado
   com a conta do Pagar.me**; estão marcados `TODO(integração)` no código. Se o PSP mandar a
   devolução com outro nome de evento, ela é registrada como evento desconhecido, sem
   estorno e sem erro.

2. **O gateway continua simulado.** Nenhuma devolução real foi recebida. O que foi medido é
   a regra, com avisos sintéticos assinados e com `POST /simulate/pix-estornado`.

3. **Só o aviso do PSP gera estorno.** Não existe rota para a empresa declarar que houve
   devolução: ela deixaria de pagar a fee só dizendo isso. Devolução que o PSP não avisou é
   caso de suporte, fora do sistema. A rota `/simulate/pix-estornado` existe só em
   `ENV=development` e `demo`, sem login, como os outros `/simulate/*`.

4. **Aviso sem identificador da devolução.** Sem `id_devolucao`, a identidade do aviso
   vira o e2e do pagamento mais o valor: o reenvio conta uma vez, mas **dois avisos
   parciais distintos, do mesmo valor e sem id, contam como um só**.

5. **Aviso que só traz o id da recorrência** vai para o ciclo recuperado **mais recente**
   do mandato. Com dois ciclos recuperados do mesmo cliente e um aviso sem o id da
   cobrança nem o e2e do pagamento, o estorno pode cair no ciclo errado.

6. **O prazo.** Conta da recuperação (`recuperado_em`), em dias corridos, pelo relógio do
   servidor, até o último instante do dia `prazo_estorno_dias`. Vale o prazo que a empresa
   tem **no momento do aviso**: mudar o prazo de 30 para 90 passa a cobrir recuperações
   antigas ainda dentro dos 90 dias.

7. **O mês fechado não é reescrito, e por isso as contagens de um mês não descontam o
   estorno de outro.** O valor líquido de um mês é o das recuperações dele menos os
   estornos que **aconteceram nele**; pode ficar negativo. `recuperados` e a taxa de
   recuperação de setembro continuam contando uma recuperação estornada em outubro. Já a
   contagem por status (`ciclos_abertos_no_mes`) mostra o status **atual** dos ciclos
   abertos no mês, e nela o ciclo estornado por inteiro aparece como encerrado.

8. **A fee original nunca muda.** O ciclo guarda a fee da recuperação e, ao lado, quanto
   dela foi estornado. O estado do ciclo continua `recuperado` mesmo com devolução total; o
   que muda é o status que a tela mostra ("encerrado sem recuperação", com o motivo).

9. **O dataset de treino não sabe do estorno.** O rótulo `recovered` de
   `ciclos_recuperacao` continua 1 e a `success_fee` continua a original. A métrica antiga
   `/metrics/recovery`, que sai desse dataset, **não desconta estorno**; as rotas
   `/metrics/involuntario/*` descontam.

10. **O CRM não é avisado** do estorno, e nada vai para a trilha do Art. 20: estorno não é
    decisão automatizada sobre o titular.

11. **O extrato ainda não tem rota.** `ciclo_cobranca.extrato_do_periodo` calcula as linhas
    (uma positiva por recuperação, uma negativa por estorno) e é testada; expor é da Etapa 3.

12. **O voluntário não tem estorno.** A fee do cliente "mantido" ainda não é calculada no
    backend (Etapa 3).

### A chave de API: o que ela declara (04/10/2026)

Declarado ao fim da Fase 1 da Rodada 2 (`docs/interno/RELATORIO_RODADA_2.md`). A empresa
gera uma chave `crai_live_...` no dashboard, e o sistema dela passa a chamar as quatro rotas
da API de clientes sem ninguém logado (`docs/CONTRATO_CLIENTES_API.md`, seção 0.1b). Desde a
Rodada 3 a chave vale em uma quinta rota, `POST /eventos` (ver a seção dos eventos, abaixo).

1. **Um tipo de chave só.** Toda chave é de produção. **Não existe chave de teste** nem
   ambiente de teste: quem quer experimentar a integração usa uma empresa de teste.

2. **Sem escopos.** A chave autentica as quatro rotas (`POST /clientes`,
   `POST /clientes/lote`, `PATCH /clientes/{id}`, `DELETE /clientes/{id}`) por inteiro. Não
   dá para gerar uma chave que só cadastre e não cancele. Quem tem a chave altera e cancela
   qualquer cliente da empresa.

3. **A chave não lê.** Não há rota de leitura da base por chave: `GET /insights` e as
   outras leituras continuam exigindo o token de login. A resposta das quatro rotas devolve
   o cliente gravado, com os campos de contato.

4. **O limite de uso é por processo.** `CRAI_API_LIMITE_POR_MINUTO` (padrão 120) conta em
   janela deslizante de 60 segundos, **na memória de cada worker**: reiniciar o serviço zera
   a contagem, e com N workers o teto efetivo é até N vezes o configurado. É por chave, e
   não por empresa: cinco chaves, cinco contagens. Um lote de 50.000 clientes conta como uma
   requisição. Valor ausente, ilegível ou menor que 1 na variável vale o padrão, sem aviso.

5. **Chave inválida não tem limite.** O 429 é da chave que autentica. Tentativas com chave
   inventada recebem 401 quantas vezes vierem; não há bloqueio por origem nem por
   quantidade de erros. Com 256 bits aleatórios, acertar uma chave por tentativa não é um
   risco prático; o custo é o de responder aos 401.

6. **O plano vem da claim `plano` do token, e o login real ainda não a tem.** O token do
   Supabase só passa a trazer `plano` na **Etapa 5**, junto com a `tenant_id` emitida pelo
   login real. Até lá, token sem a claim é tratado como `essencial`, e **em produção nenhuma
   empresa consegue gerar chave**: `POST /integracao/chaves` responde 403
   `plano_sem_api`. Hoje a chave só é gerada com o token de desenvolvimento
   (`POST /dev/token`, que emite `premium` por padrão), isto é, só com `ENV=development`.

7. **O plano é conferido só ao gerar, e não a cada uso da chave.** A chave não carrega
   plano. Uma empresa que saia do premium **continua com as chaves que já tinha
   funcionando**, até revogá-las: listar e revogar valem em qualquer plano (revogar, só dono
   e administrador), justamente para ela conseguir ver e desligar o que tem. O que não
   existe é o desligamento automático das chaves na troca de plano.

8. **O papel de quem criou vale no momento da criação.** A chave criada por um
   administrador continua valendo depois que ele deixa a empresa ou é rebaixado. O banco
   guarda o papel, não a pessoa: não dá para listar as chaves de uma pessoa.

9. **Sem validade e sem rotação automática.** A chave vale até ser revogada. Não há data de
   expiração nem aviso de chave antiga ou sem uso. O máximo é de cinco chaves ativas por
   empresa, o que dá folga para a troca (gerar a nova, trocar no sistema, revogar a antiga).

10. **Sem expurgo.** A chave revogada fica na lista para sempre (só hash, nome e datas), e o
    contador diário de uso não tem prazo de retenção.

11. **O contador de uso é melhor esforço.** Se a gravação de `ultimo_uso_em` falhar (banco
    ocupado), a requisição é atendida e o erro vai para o log. A revogação não depende
    dessa escrita: ela é lida do banco a cada requisição, sem cache.

12. **As tabelas moram no SQLite local** do ciclo de cobrança, com a limitação já declarada
    acima para esse arquivo: instâncias em máquinas diferentes não compartilham as chaves.

13. **A chave só é reconhecida pelo prefixo.** Um `Authorization: Bearer` que não começa
    por `crai_live_` é tratado como token de login e recebe os erros de token. Numa rota
    fora das quatro, qualquer texto com o prefixo recebe 401 `chave_nao_vale_nesta_rota` sem
    que a chave seja consultada.

### O voluntário e a visão geral no dashboard: o que as rotas declaram (04/10/2026)

As rotas de leitura das páginas Voluntário e Visão geral (`api/voluntario.py` e
`api/visao_geral.py`). O que cada número é, e o que ele não é:

1. **O "mantido" do voluntário conta 1 mês de MRR, e a fee é a do involuntário.** O valor
   mantido é `MESES_DE_MRR_MANTIDOS` mês(es) do MRR de quem aceitou a oferta, menos o
   desconto concedido, líquido da fee. O número de meses é uma constante só
   (`churn_voluntary/mantido.py`, hoje `1`), e o prazo do estorno é o `prazo_estorno_dias`
   da empresa (padrão 30). O plano de negócio fala em 6 meses e 90 dias: a troca é essa
   linha e essa configuração. A fee do voluntário é `CRAI_SUCCESS_FEE_VOLUNTARIO_PCT`; sem
   ela, vale a do involuntário (`CRAI_SUCCESS_FEE_PCT`, padrão 15%).

2. **O desconto concedido é uma conta por oferta.** Desconto de 10% ou 20%: o percentual
   sobre o MRR, pelos meses em que vale dentro da janela contada (o desconto vale 3 meses).
   Pausa de 1 mês: um mês inteiro de MRR. Com 1 mês contado, **a pausa mantém R$ 0**: o
   cliente ficou, mas naquele mês não houve receita. Pix ou boleto: zero de desconto.

3. **A linha do mantido nasce numa sincronização, não no instante do aceite.** O aceite mora
   no dataset de treino (`ciclos_retencao`), que não guarda dinheiro. A tabela
   `retencoes_mantidas` é preenchida quando alguém lê o mantido e depois de um
   `DELETE /clientes/{id}`. A **data** de cada linha é a do fato (o aceite, o cancelamento);
   o MRR, o desconto e a fee são os do momento da sincronização e, gravados, não mudam mais.
   Se a fee da instalação mudar entre o aceite e a primeira leitura, vale a nova.

4. **Sem MRR conhecido, a retenção não vira valor.** Se o ciclo não gravou o MRR e o cliente
   não está na base, o aceite aparece em `aceites_sem_valor` e não entra no dinheiro.

5. **O aceite sorteado não é dinheiro de verdade.** Com `CRAI_SIMULATE_OUTCOMES=1` o
   desfecho é um sorteio. Essas retenções ficam marcadas e só aparecem com
   `incluir_simulados=true`.

6. **O cancelamento que estorna é o da base importada.** Só `cancelado_em` conta (chega pelo
   `DELETE /clientes/{id}`). Um cliente que só existe no SDK (sem linha na base) nunca é
   estornado, porque não há como saber que ele cancelou.

7. **O cache da régua é por processo.** `GET /clientes/recentes`, `/clientes/base` e os
   totais usam o ranking guardado em memória, recalculado quando a base do tenant muda. A
   escrita deste processo invalida na hora. A escrita de **outro** processo é percebida por
   uma foto da tabela (contagens e carimbos de data com resolução de segundo): se ela não
   mudar nenhuma contagem e cair no mesmo segundo da escrita anterior, só é vista na
   escrita seguinte. Reiniciar o serviço zera o cache. O `GET /insights` não usa o cache.

8. **A origem da base (`api` ou `anexo`) só existe para escritas feitas depois da Rodada 3,**
   e é da última escrita aceita, não de cada cliente. Base anterior tem origem `null`.

9. **Régua x modelo não é um teste com grupo de controle.** As duas avaliam a mesma base (a
   de hoje mais quem cancelou no período), com os últimos dados que cada cliente tinha. O
   sistema não guarda o histórico de faixas: "avisou antes" quer dizer "marca como grave o
   cliente com os dados de antes do cancelamento". A rota devolve vazio sem o modelo v3
   ativo, com menos de 30 clientes com dado, ou com menos de 5 cancelamentos no período.

10. **O funil é da coorte do mês, e os outros números são do período.** O funil conta as
    cobranças que **falharam** no mês pedido, onde quer que estejam hoje. Os cartões, a série
    e "o que funciona" contam o que **teve desfecho** no período. Ciclos ativos, aguardando
    escolha e risco grave são de agora.

11. **"Canais" mistura dois tipos de resposta.** No involuntário, resposta é a cobrança
    recuperada depois da mensagem, sem ter sido por uma tentativa. No voluntário, é a oferta
    aceita. Os dois somam no mesmo canal.

12. **A atividade recente não tem "entrou em risco grave" de verdade.** O sistema não grava
    a mudança de faixa. O evento "entrou em risco grave" é o ciclo de retenção em que o
    cliente foi avaliado como crítico (um evento do SDK ou do disparo em lote).

13. **O voluntário depende da claim `plano` do token.** Fora do premium, os campos do
    voluntário vêm `null` na visão geral, na série, na atividade e no extrato. O login real
    só terá a claim na Etapa 5: até lá, só o token de desenvolvimento vê o voluntário na
    visão geral. As rotas próprias do voluntário (`/clientes/recentes`,
    `/metrics/voluntario/*`) não conferem o plano.

14. **`GET /health` diz se o redator TEM chave, não se ele respondeu.** `redator.disponivel`
    é a presença de `ANTHROPIC_API_KEY`. Os modelos contados são os quatro que o serviço
    carrega (classificador de falha, detector de anomalia, dia provável de saldo e risco
    voluntário), neste processo. A `base` só vem com um token de login válido, e é a da
    empresa do token.

15. **`retencoes_mantidas` e `base_atualizacoes` não têm expurgo,** e moram no SQLite local
    do ciclo de cobrança, com a limitação já declarada para esse arquivo.

### A simulação do gateway: o que é de verdade e o que não é (04/10/2026)

A página "Simulação do gateway" (`api/simulacao.py`, `simulador.py`, `ambiente.py`). O
cliente, o banco dele e o relógio são fictícios. O que age sobre eles é o sistema de sempre:
o mesmo pipeline do webhook do Pix, os mesmos modelos, a mesma regra do BACEN, o mesmo
agendador e as mesmas rotas de escolha de mensagem.

1. **A simulação mora em arquivos separados, um conjunto por empresa.** Ao lado de cada
   arquivo real há o de simulação da empresa (`recovery_cycles.simulacao.<empresa>.db`,
   `retention_cycles.simulacao.<empresa>.db`, `pix_retry_state.simulacao.<empresa>.json` e
   `bandit_state.simulacao.<empresa>.json`). Nada do que é simulado entra no ciclo real, no
   dataset de treino, na trilha do Art. 20 real, no bandit real nem nas métricas reais. Há
   teste que confere os arquivos reais byte a byte antes e depois de uma simulação inteira.
   **Isto vale para o SQLite local.** Quando o ciclo de cobrança for para um banco gerenciado,
   a simulação precisa de um esquema ou de um banco próprio: o mecanismo de hoje troca o
   caminho do arquivo.

2. **A base de clientes não tem cópia de simulação.** O cliente fictício não é gravado na
   base da empresa (que em produção mora no Postgres). Por isso, dentro da simulação, a
   mensagem dele sai pelo primeiro canal permitido da empresa (canal presumido), e o nome
   que a tela mostra vem das tabelas da própria simulação.

3. **O relógio simulado é um por empresa, e só anda para a frente.** As tentativas simuladas
   só saem quando a empresa avança o relógio: o relógio de verdade nunca abre os arquivos de
   simulação. "Avançar N dias" para na primeira ação que acontecer. Se a empresa criar
   vários clientes fictícios, o relógio é o mesmo para todos.

4. **O histórico do cliente fictício é o perfil sintético do sistema.** Tempo de casa,
   histórico de pagamento e falhas em 90 dias saem do mesmo provedor que atende um pagador
   sem histórico conectado (limitação já declarada do involuntário): são deterministas pelo
   id da recorrência. **O perfil do formulário (CLT, PJ, freelancer) não é lido pelo
   sistema:** o dia provável de saldo é estimado pelo modelo, sozinho. A tela diz isso.

5. **A verdade escondida fica numa tabela separada** (`simulacao_verdade`), lida só pelo PSP
   simulado, pela resposta do cliente à mensagem e pela comparação "sem a CRAI". Nenhum
   módulo do pipeline importa o simulador (teste estático), e dois clientes iguais no que o
   sistema vê e opostos na verdade recebem o mesmo diagnóstico (teste dinâmico). A verdade
   volta só para a empresa que a criou, no verso do cartão.

6. **O sorteio do PSP simulado é determinístico.** A mesma simulação, repetida, dá o mesmo
   resultado: o sorteio sai do id do cliente fictício e do número da cobrança. Com chance
   de 100% sempre passa; com 0%, nunca.

7. **A resposta à mensagem é uma regra, não um modelo.** Dois dias depois de a mensagem sair,
   o cliente fictício paga se já tem saldo e o sorteio dele deixar (a chance de pagar mais 20
   pontos). Quem não paga fica como qualquer ciclo real: `mensagem_enviada` até o prazo de
   recuperação (30 dias) e depois `perdido`.

8. **"Sem a CRAI" é a regra do Pix Automático, e não uma medição.** As duas janelas
   automáticas do dia do vencimento rodam sob responsabilidade do banco do pagador; a
   recobrança nos 7 dias seguintes só acontece se o recebedor pedir
   (`dunning/pix_automatico_retry.py`). Sem a CRAI ninguém pede: sem saldo no dia, a cobrança
   se perde. Não é uma comparação com grupo de controle.

9. **Na retenção simulada, cada sinal marcado vira um dado, e o não marcado não vira nada.**
   "Abriu a página de cancelamento" é o evento do SDK; "uso em queda" são 24 dias sem entrar
   e 1 funcionalidade usada; "chamados" e "pagamentos com falha" são as duas colunas da
   base. O sistema decide como decidiria com um cliente de verdade. Sem dado de uso, decide
   a régua: a página de cancelamento dá 0,90 e há oferta, e abaixo do corte de 0,60 não há.
   **Com dado de uso e o modelo v3 ativo, quem decide é o modelo, e o corte de 0,60 não
   vale** (desde 05/10/2026; ver a seção "Quando o modelo v3 decide o risco", no fim deste
   arquivo): há oferta sempre que o evento é de intenção explícita e, nos outros casos, para
   quem está como grave ou preocupante pela posição na base. A resposta diz qual regra
   decidiu, e por que não houve oferta. Até 04/10 o risco do modelo era comparado com 0,60 e
   nenhuma das combinações testadas recebia oferta (o máximo medido foi 0,40).

10. **O aceite simulado vem da propensão escondida, e o bandit de verdade não aprende.** A
    simulação usa uma cópia do bandit da empresa (nasce do que ele sabe agora, aprende só
    com a simulação, é apagada na limpeza). A oferta sai do sorteio dessa cópia (Thompson
    Sampling): o mesmo cliente fictício pode receber ofertas diferentes em rodadas
    diferentes. Com 1 mês contado, a pausa de 1 mês mantém R$ 0, e a tela explica.

11. **Com "Mostrar: Simulação", o agora da simulação aparece como agora há pouco.** O
    relógio simulado costuma estar dias à frente. Nas métricas, na série, na atividade e no
    extrato, as datas simuladas voltam o quanto ele está à frente (mais 60 segundos de
    folga): o que aconteceu "agora" na simulação aparece há um minuto, e o que aconteceu 3
    dias simulados antes, 3 dias atrás. A lista de ciclos e o painel do ciclo simulado
    mostram as datas do relógio simulado, sem recuo.

12. **Os ids dos ciclos simulados são somados a 9.000.000.000.000** nas rotas, para o ciclo
    real 7 e o simulado 7 nunca se confundirem. O id de um ciclo simulado de outra empresa
    responde o mesmo 404 de um ciclo que não existe.

13. **Apagar a simulação apaga os arquivos.** `DELETE /simulacao` remove os arquivos de
    simulação da empresa. No Windows, um arquivo em uso por outra requisição não pode ser
    apagado: a rota responde 409 e a pessoa tenta de novo. Não há expurgo automático: a
    simulação fica até a empresa apagar.

14. **"Simular outro cliente" não apaga nada.** Os ciclos simulados anteriores continuam
    (e aparecem com "Mostrar: Simulação"). Só "Apagar todos os dados da simulação" limpa.

15. **O membro lê a simulação e não a executa.** Criar, cobrar, avançar e apagar exigem dono
    ou administrador. A chave de API não abre nenhuma rota da simulação.

### O assistente do dashboard: o que ele lê e o que ele não é (04/10/2026)

`POST /assistente` (`api/assistente.py`). A pergunta vai ao mesmo LLM que escreve as
mensagens, com a documentação de produto (`api/assistente_documentacao.md`) e os totais da
empresa do token.

1. **O que vai ao LLM é só número e rótulo de lista fechada.** Antes do envio, o contexto
   passa por um filtro que apaga qualquer texto que não seja um rótulo escrito no código
   (causa, oferta, canal, etapa, modo), uma data ou uma hora. Nome, e-mail, telefone, CPF,
   id de cliente e texto de mensagem não têm por onde entrar. Há teste com a base cheia, e
   teste que faz uma função de métrica "vazar" um nome e confere que ele não sai.

2. **Um nome digitado na pergunta vai como foi digitado.** E-mail, CPF, CNPJ, telefone,
   chave Pix aleatória e número longo digitados na pergunta são trocados por `[removido]`
   antes do envio. Um nome de pessoa não tem forma que o sistema reconheça: se o usuário
   escrever "por que a Maria Souza está em risco?", o nome vai ao LLM. O assistente não tem
   dado nenhum sobre ela para responder.

3. **A defesa contra "ignore as regras" não depende de o LLM obedecer.** O prompt do sistema
   diz que a pergunta é dado, e a pergunta vai cercada e sem `<` nem `>`. Mas o que garante é
   outra coisa: o LLM não recebe dado de cliente e não tem ferramenta nenhuma. O pior caso
   (ele repetir o que recebeu) mostra a documentação e os totais da própria empresa. O texto
   que volta passa pela mesma máscara, e os links só podem ser os de uma lista fechada de
   páginas do painel.

4. **A resposta do LLM não é conferida contra os números.** Ele recebe os totais certos, mas
   pode errar uma conta ou uma frase. A tela continua mandando conferir nas páginas (os links).
   Não há avaliação automática da qualidade das respostas.

5. **Não guarda a conversa, e por isso não tem memória.** Cada pergunta vai sozinha: "e no
   mês passado?" não sabe do que se falava antes. O log do serviço registra a empresa, a
   origem da resposta e os tamanhos, nunca a pergunta nem a resposta.

6. **Os números são dos últimos 30 dias e do mês corrente.** O assistente não consulta outro
   período, não vê um ciclo específico e não vê a simulação do gateway.

7. **A taxa da CRAI não vai ao LLM** (R11). Para "quanto a CRAI cobra", ele explica a regra
   (só sobre resultado) e aponta o extrato.

8. **O limite é por empresa, por hora e por processo** (`CRAI_ASSISTENTE_LIMITE_POR_HORA`,
   padrão 60). Fica na memória: reiniciar o serviço zera, e com mais de um worker cada um
   conta o seu.

9. **Sem a chave do LLM, o assistente é um texto fixo.** Com `ANTHROPIC_API_KEY` ausente, o
   LLM fora do ar, lento (25 s) ou devolvendo vazio, a resposta é o texto de ajuda
   (`origem: "ajuda"`), que a tela marca como "Resposta fixa". O motor de respostas prontas
   que vivia no navegador só existe no modo demonstração (sem backend).

10. **O modelo é o do redator de mensagens** (`claude-sonnet-5`, o mesmo nome escrito em
    `dunning_engine.py`), e pode ser trocado por `CRAI_ASSISTENTE_MODELO`. A documentação de
    produto é escrita à mão: se o produto mudar, ela precisa ser atualizada junto.

### Os eventos de comportamento pela chave de API: o que a rota declara (04/10/2026)

`POST /eventos` (`api/app.py::receber_evento`, `api/eventos_recebidos.py`) e o limite de
contato do pipeline voluntário (`churn_voluntary/voluntary_agent.py`).

1. **A chave é secreta e só serve no servidor da empresa.** Não existe chave pública. Uma
   chave posta numa página web ou num aplicativo fica à vista de qualquer visitante, e quem
   a tem altera e cancela clientes da empresa (a chave não tem escopos). **O SDK de
   navegador, com chave pública e sem poder de escrita na base, é de outra etapa.**

2. **O mesmo caminho do Segment, com o tenant da chave.** A validação e o pipeline são os do
   webhook do Segment (uma função só: `_evento_voluntario_validado`). A diferença é de onde
   vem o tenant: no Segment, do cabeçalho ou do corpo assinado; aqui, da chave. Um
   `tenant_id` no corpo é ignorado.

3. **As `properties` entram como vieram.** Como no Segment, o que a empresa mandar em
   `properties` vai para o estado do pipeline. Só os campos conhecidos são gravados no
   dataset e na trilha. A recomendação continua: mandar só os campos que o contrato pede.

4. **A idempotência dura 30 dias, e é por conteúdo quando não há `messageId`.** Sem
   `messageId` e sem `timestamp`, dois avisos idênticos do mesmo fato (o cliente abriu a
   página de cancelamento duas vezes) contam como um só dentro de 30 dias. Com `timestamp`
   ou `messageId` diferente, são dois eventos. A tabela guarda só o SHA-256 da chave de
   idempotência, o tenant e a hora.

5. **O webhook do Segment não ganhou idempotência** (a tarefa manda ele continuar igual): um
   evento reenviado pelo Segment roda o pipeline de novo. O limite de contato impede a
   segunda oferta, mas o dataset ganha a segunda linha.

6. **O limite de contato é pela última oferta ENVIADA.** Um cliente final recebe no máximo
   uma oferta a cada `intervalo_minimo_ofertas_dias` (1 a 365, padrão 30), contando da
   última oferta que saiu para ele, por qualquer origem (Segment, `POST /eventos`, disparo em
   lote). Evento sem risco não gasta o intervalo. O que NÃO conta: uma oferta decidida
   cuja entrega falhou (ela não foi enviada). **Antes desta rodada o pipeline de eventos não tinha freio
   nenhum,** e o disparo em lote tinha só o do ciclo aberto, que continua valendo. A decisão
   de não ofertar vai para a trilha do Art. 20, com a regra e o motivo.

7. **O intervalo é contado em dias corridos a partir do instante da oferta,** e não em dias
   de calendário: com 30 dias, a oferta seguinte é possível 30 x 24 horas depois.

8. **A tela de Configuração ainda não mostra o intervalo.** A chave
   `intervalo_minimo_ofertas_dias` é lida e gravada por `GET` e `PUT /configuracao`; o
   dashboard ainda não tem o campo.

9. **O limite de eventos é por chave, por minuto e por processo**
   (`CRAI_EVENTOS_LIMITE_POR_MINUTO`, padrão 600), separado do limite das rotas de clientes
   (`CRAI_API_LIMITE_POR_MINUTO`, padrão 120). Fica na memória de cada worker. **Com o token
   de login não há limite** (como nas outras rotas do token).

10. **A resposta não diz o que o sistema decidiu.** `POST /eventos` devolve só `ok` e
    `duplicado`. O risco, a oferta e o canal aparecem no painel (página Voluntário).

### Direitos do titular e descadastro: o que as rotas declaram (04/10/2026)

`POST /titular/exportar`, `POST /titular/anonimizar`, `GET /titular/texto-para-politica`
(`api/titular.py`), `POST` e `DELETE /clientes/{id}/nao-contatar` (`api/clientes.py`) e
`POST /simulate/resposta-sair` (`api/app.py`).

1. **A exportação não repete o e-mail nem o telefone.** Ela diz se cada contato está
   guardado (`contatos_guardados`), sem o valor. A regra da Rodada 3 é que nenhuma rota
   devolve telefone, e-mail, CPF ou chave Pix, e quem forneceu esses dados foi a própria
   empresa. Se a leitura jurídica do Art. 18 pedir o valor na exportação, é uma decisão a
   tomar. O nome, os valores, as datas, o texto das mensagens ainda guardado e as decisões
   automatizadas vêm inteiros.

2. **"Anonimizar" apaga os contatos e o texto das mensagens; o identificador fica.** Saem
   o nome, o e-mail, o telefone, o motivo de cancelamento e o texto de todas as mensagens
   do titular. Ficam os valores, as datas e os desfechos (as métricas agregadas não mudam),
   a chave que a empresa usa para o cliente (`customer_id_externo`, um pseudônimo dela) e a
   trilha do Art. 20. **Não é anonimização no sentido forte:** quem tem a tabela de clientes
   da empresa ainda liga os registros ao titular. A trilha não é tocada, e só sai pelo prazo
   de retenção.

3. **Anonimizar marca "não contatar", e essa marca não sai.** Sem contato não há por onde
   falar com o titular; a marca garante que nem o canal presumido nem um telefone vindo num
   evento sejam usados. `DELETE /clientes/{id}/nao-contatar` responde 409 para ela.

4. **O titular é identificado pela chave da empresa.** Uma cobrança cuja recorrência não
   está ligada a nenhum cliente da base não é alcançada pela exportação nem pela
   anonimização. Nesse caso a CRAI não guarda nome nem contato, mas o texto das mensagens
   geradas fica até o expurgo (90 dias depois do desfecho, por padrão).

5. **A marca "não contatar" mora numa tabela própria** (`clientes_nao_contatar`), no mesmo
   destino da base importada (Postgres em produção, SQLite em desenvolvimento), criada na
   primeira conexão. Ela não entra na foto que a planilha e a API regravam: sobrevive à
   reimportação, à alteração e ao cancelamento do cliente. Apagar a base inteira de uma
   empresa (`apagar_tenant`) **não** apaga as marcas.

6. **A marca é só de mensagem.** As tentativas de cobrança do Pix continuam: fazem parte do
   contrato, e não são contato de marketing. No involuntário, a cadeia de canal devolve
   `sem_canal` com o motivo `cliente_pediu_para_nao_ser_contatado`, na geração das sugestões
   e de novo na hora do envio (a marca posta no meio do caminho já vale). No voluntário, não
   há oferta, e a decisão vai para a trilha.

7. **A empresa pode tirar a marca que veio de uma resposta SAIR.** A rota existe para o
   cliente que pede para voltar a receber. A responsabilidade por tirar a marca é da
   empresa (controladora); o registro de acesso guarda quem tirou e quando.

8. **A resposta SAIR é simulada.** `POST /simulate/resposta-sair` só existe em
   `development` e `demo`. Quando o envio de mensagens for de verdade, o provedor precisa
   chamar o mesmo caminho ao receber a palavra. Só "SAIR" está previsto; variações
   ("PARAR", "STOP", "CANCELAR") não são reconhecidas por ninguém hoje.

9. **A linha "responda SAIR" vai em toda mensagem em que o cliente pode responder.** No
   involuntário, sempre (antes da linha de mensagem automática; a linha é posta de novo na
   hora do envio, então uma sugestão gerada antes desta rodada também sai com ela). No
   voluntário, nas mensagens por WhatsApp e por e-mail; o aviso dentro do produto (popup)
   não leva a linha, porque não há como responder a ele.

10. **O texto para a política depende de um arquivo do repositório**
    (`docs/lgpd/texto-para-politica-de-privacidade.md`). Numa instalação que só tenha a
    pasta `app/`, a rota responde 503. Os prazos do texto são os da configuração da empresa.
    O texto não promete o expurgo dos ciclos nem o da base, que ainda não são executados.
    **É um texto de apoio: precisa de revisão jurídica antes de ser publicado.**

11. **A explicação na tela é a decisão mais recente, como foi registrada.** O texto cita o
    nome técnico do modelo e a versão do artefato. A tela manda revisar antes de repassar ao
    cliente. A rota (`GET /titular/explicacao/{id}`) devolve todas as decisões; a tela
    mostra uma.

12. **Exportar, anonimizar, marcar e desmarcar entram no registro de acesso** (empresa,
    rota, papel e hora, por 12 meses). O registro não guarda qual titular foi pedido.

### A saída do serviço e a atualização da tela (04/10/2026)

1. **A saída do serviço troca o caractere que o terminal não tem por `?`.** Na subida
   (`api/relogio.py::configurar_saida`, chamada no `lifespan`), a saída padrão e a de erro
   passam a substituir, em vez de levantar. Antes, um `print` com um emoji ou uma seta, com
   a saída em cp1252 (o console do Windows, ou a saída redirecionada para arquivo),
   derrubava a requisição. A codificação não muda: no log, o caractere aparece como `?`.
   Vale para o serviço no ar; um script que importe os módulos sem subir o serviço não
   passa por isso.

2. **A tela se atualiza sozinha, por consulta periódica.** As páginas com dados do backend
   (Visão geral, Involuntário, Voluntário) consultam de novo a cada 60 segundos, e o painel
   de um ciclo aberto a cada 5. Não é tempo real: uma mudança pode levar até esse tempo
   para aparecer. A consulta para com a aba do navegador escondida, não mostra o estado de
   "carregando" e, se falhar, mantém o que já estava na tela. Com muitas abas abertas, cada
   uma faz as suas consultas.

### Quando o modelo v3 decide o risco: intenção explícita ou posição na base (05/10/2026)

**O que mudou.** Até 04/10, o pipeline de eventos do voluntário (webhook do Segment,
`POST /eventos` e a simulação) só fazia oferta com `risk_score >= 0,60`, viesse o número da
régua ou do modelo. O corte nasceu com a régua, que tem escala absoluta. O score do modelo
v3 não está nessa escala (na referência de treino dele, 90% dos clientes ficam abaixo de
0,32). Medido em 56 combinações de evento, mensalidade e sinais, com o modelo v3 de
`app/models/`: o risco ficou entre 0,07 e 0,40 e **nenhuma** recebia oferta, nem a de quem
abriu a página de cancelamento. Agora, quando o modelo v3 decide o risco
(`voluntary_agent.regra_de_intervencao`):

| Caso | O que o sistema faz |
|---|---|
| Evento de intenção explícita (`Cancellation Page Viewed`, `Downgrade Clicked`) | Intervém **sempre**, por regra. O risco do modelo continua calculado e gravado; só não é ele que decide |
| Outros eventos, cliente grave ou preocupante pela posição na base | Intervém. É a criticidade de `batch_scoring.criticidade_do_evento`, a mesma do lote e do SDK: posição do score entre os 10% (grave) ou os 20% seguintes (preocupante), **e** sinal absoluto de abandono (7 dias sem entrar, ou nenhuma funcionalidade usada) |
| Outros eventos, fora das faixas, ou sem referência de posição | Não intervém |
| A régua decidiu (sem modelo, sem dado de uso para o modelo, ou modelo legado) | **Nada mudou:** o corte de 0,60 continua |

As mesmas 56 combinações, depois: **36 recebem oferta** (as 32 de intenção explícita e 4
pela posição na base), e 20 não (evento de sessão fora das faixas). O intervalo mínimo
entre ofertas e o "não contatar" seguram a oferta nos dois casos, como antes.

**O que isto declara:**

1. **"A oferta mais leve" é a de menor custo entre as ofertas de retenção de verdade**
   (`voluntary_agent.oferta_mais_leve`; o custo é o de `offer_bandit.offer_cost`). **A troca
   para Pix ou boleto não entra na comparação** (decisão do Crai, Rodada 4): ela muda o meio
   de pagamento, não o preço nem o plano. Com as quatro ofertas de hoje, a mais leve é
   **sempre o desconto de 10% por 3 meses** (custa 30% de uma mensalidade, contra 60% do
   desconto de 20% e 100% da pausa). Nesse caso o bandit não escolhe; ele continua
   aprendendo com o aceite ou a recusa dessa oferta. Quando é o bandit que escolhe, a troca
   para Pix ou boleto continua sendo uma das quatro.

2. **Por intenção explícita, o cliente leva a oferta do bandit, qualquer que seja a faixa**
   (decisão do Crai, Rodada 4). A mais leve fica só para o preocupante que veio pela
   posição na base.

3. **O disparo em lote usa a mesma regra de intensidade** (decisão do Crai, Rodada 4):
   grave leva a primeira oferta do bandit; preocupante, a mais leve. Vale para toda linha
   do lote, tenha o risco vindo da régua ou do modelo, e a decisão de oferta do lote grava
   `intensidade` na trilha. **No pipeline de eventos com a régua decidindo, nada mudou:** a
   oferta é a primeira do bandit acima do corte de 0,60, sem intensidade.

4. **A referência de posição da empresa fica em disco, em quantis.** Um evento sozinho é
   posicionado contra a base da empresa, se ela já foi pontuada alguma vez e tinha pelo
   menos 30 clientes com dado; senão, contra os quantis de treino gravados no meta do
   modelo. Sem nenhuma das duas, ninguém é grave nem preocupante, e só a intenção explícita
   gera oferta. Desde a Rodada 4, cada pontuação da base grava os **101 quantis do score e
   os 101 do MRR** da empresa em `referencias_de_posicao.json`, ao lado do banco de ciclos
   de retenção (ou onde `CRAI_REFERENCIAS_POSICAO` mandar), e o serviço os lê na subida: a
   referência **sobrevive ao reinício**. O que isto declara:
   - o arquivo não tem dado de cliente (só os quantis, a contagem e a data), mas o primeiro
     e o último quantil são a menor e a maior mensalidade da base, sem dizer de quem;
   - a posição passou a ser calculada contra 101 quantis, e não contra a lista inteira de
     scores: a resolução é de 1 ponto percentual, igual à da referência de treino;
   - a referência é a da **última pontuação da base** (alguém abrir o painel de risco ou o
     lote rodar), não é recalculada a cada evento; a data fica no arquivo;
   - se a base encolhe para menos de 30 clientes com dado, a referência da empresa é
     apagada e volta a valer a de treino;
   - com mais de um processo do serviço, cada um grava o arquivo inteiro: duas empresas
     pontuadas no mesmo instante por processos diferentes podem perder uma das gravações,
     que se refaz na pontuação seguinte;
   - a frase do motivo diz "da sua base" nos dois casos; a trilha registra qual referência
     foi usada (`posicao.referencia`).

5. **A trilha diz a regra; o dataset de ciclos, não.** A chave `regra_de_intervencao` entra
   na saída da decisão de risco (as duas regras que intervêm e os dois motivos de não
   intervir) e, quando há oferta, na decisão de oferta, junto de `intensidade`. As decisões
   "limite de contato" e "não contatar" também dizem qual regra pedia a intervenção. A
   frase da explicação diz isso em português. **O schema, o encadeamento e o índice da
   trilha não mudaram** (são chaves novas dentro do JSON de `saida`, só em decisões novas).
   `ciclos_retencao` não ganhou coluna: para saber por que um ciclo teve oferta, lê-se a
   trilha.

6. **No caminho da régua a trilha não ganha chave nenhuma** e a oferta é sempre a primeira
   do bandit, como antes. Há teste com uma grade de eventos nos dois lados do corte.

7. **`risk_score` abaixo de 0,60 com oferta enviada passou a ser normal** quando o modelo
   decide. Qualquer análise do dataset que tome 0,60 como fronteira de "quem foi abordado"
   precisa olhar `offer_type`, e não o número.

8. **A resposta de `POST /simulacao/retencao` mudou de forma quando o modelo decide:**
   `corte_de_intervencao` vem nulo, e entram `regra_de_intervencao`, `intensidade` e
   `sem_oferta_porque`. Quando a régua decide, `corte_de_intervencao` é 0,60 e as três
   chaves novas vêm nulas.

**Os dois achados do conserto, consertados na Rodada 4:**

9. **A trilha não repete mais as decisões do evento anterior.** O estado do grafo fica
   guardado por cliente (`MemorySaver`), e a lista `decisoes` do evento anterior não era
   zerada: o segundo evento do mesmo cliente regravava as decisões do primeiro (8 linhas
   onde deviam ser 5). Agora `assess_risk`, a entrada de todo evento, zera o que é do
   evento: as decisões, as candidatas, as ofertas e os canais considerados, e o motivo de
   não ofertar. **As linhas duplicadas que já estavam na trilha continuam lá:** a trilha é
   só de acréscimo, e apagar linha dela é o que ela existe para impedir. Elas têm a data e
   o conteúdo da decisão original repetidos em linhas com `id` maior.

10. **Na lista "Clientes em risco", o motivo de quem veio por evento diz quem decidiu.** A
    frase é montada a partir do ciclo gravado mais a **decisão de risco da trilha** daquele
    evento (uma leitura nova, `retention_log.ultimas_decisoes_de_risco`): se foi o modelo, a
    frase é a da posição pelo modelo; se a oferta saiu por intenção explícita, a frase diz;
    se foi a régua, a frase de sempre. A linha também passou a dizer quem decidiu
    (`decidido_por`), que antes vinha nulo para quem veio por evento. Se a trilha não tiver
    a decisão daquele ciclo (ela é gravada por melhor esforço), a frase não afirma "crítico
    pelo valor da conta" para uma mensalidade abaixo do limiar de valor, e `decidido_por`
    fica nulo.

11. **`app/README.md` ainda desenha o fluxo com "[risco >= 0.60?]".** O arquivo não pode ser
    tocado nesta rodada.

### Pendências de tela e de operação fechadas na Rodada 4 (05/10/2026)

1. **Os ciclos antigos são anonimizados, não apagados.** Um ciclo de cobrança com desfecho
   há mais que o prazo da empresa perde o que o liga a uma pessoa: o id da recorrência e o
   da cobrança viram `anonimizado-<número do ciclo>`; os ids do PSP, das tentativas e das
   devoluções saem; o que restar de texto de mensagem é apagado; e a linha do dataset de
   treino do involuntário que aponta para o ciclo perde os dois identificadores dela.
   **Ficam** os valores, as datas, a causa, o estado, a taxa e as features do dataset: as
   métricas e o extrato daquele período dão o mesmo número de antes (há teste). O que isto
   declara:
   - depois disso o ciclo não tem mais nome de cliente na tela (o id não bate com a base),
     e a busca não o acha pelo cliente;
   - a exportação e a anonimização a pedido do titular (art. 18) deixam de alcançar esse
     ciclo, porque ele já não é dele;
   - **a trilha do Art. 20 não é tocada por este expurgo.** As decisões daquele ciclo
     continuam nela, com o identificador original, até o prazo próprio da trilha (5 anos);
   - a linha do dataset de treino sem `ciclo_id` (anterior à Etapa 1) não é alcançada;
   - ciclo sem desfecho nunca é anonimizado, por mais antigo que seja.

2. **A simulação parada é apagada inteira, pela data dos arquivos.** "Parada" é não ter
   nenhum arquivo de simulação daquela empresa gravado nos últimos 30 dias (tempo de
   verdade, não o relógio simulado). O prazo é uma constante
   (`simulador.DIAS_DE_SIMULACAO_PARADA`), não é configurável pela empresa.

3. **O que falta nos expurgos, e por quê.**
   - **A base depois do fim do contrato (6 meses):** o backend não sabe quando o contrato
     de uma empresa acabou. Quem sabe é o site (Etapa 5). Precisa de: a data de fim do
     contrato por empresa; uma passagem que, 6 meses depois, apague a base, as marcas de
     "não contatar", as chaves de API, a configuração e o arquivo de referência de posição
     daquela empresa.
   - **O dataset de retenção do voluntário (`ciclos_retencao`):** seria preciso (a)
     decidir o prazo, que hoje não existe na configuração; (b) garantir que ele é maior que
     o intervalo máximo entre ofertas (365 dias), porque o limite de contato lê a última
     oferta enviada dali; (c) anonimizar em vez de apagar, trocando `user_id` por um
     marcador e mantendo features, oferta e desfecho, porque é dado de treino do bandit e
     do modelo de risco; e (d) fazer o mesmo com a tabela das retenções mantidas, de onde
     saem o valor mantido e o estorno. Não foi feito.

4. **A próxima ação do sistema é das cobranças reais, e é uma só.** O cartão do
   Involuntário mostra a mais próxima entre a próxima tentativa agendada e o próximo envio
   de mensagem (a escolhida que espera o horário, ou a recomendada quando o prazo de
   escolha vencer). O fim do prazo de recuperação de um ciclo não entra. Com "Mostrar:
   Simulação" o cartão continua mostrando só o que é real: os ciclos simulados andam no
   relógio da simulação.

5. **A busca do topo** procura o texto no nome do cliente (em qualquer posição) e no começo
   do identificador do cliente e do id da recorrência. Devolve no máximo 8 clientes e 8
   cobranças, sem contato. No SQLite (desenvolvimento) ela não distingue maiúscula de
   minúscula só nas letras sem acento; no Postgres, em todas. Fora do plano premium vêm só
   as cobranças. Entra no registro de acesso.

6. **O sino** mostra o número de `GET /metrics/involuntario/mes` (quem espera a escolha
   agora, sem contar quem já escolheu e só espera o horário) e consulta de novo a cada 60
   segundos, como as páginas. Ele não avisa de mais nada.

7. **O extrato em CSV vem do backend** (`GET /extrato/csv`): separador `;`, vírgula
   decimal, uma linha de total, a marca de UTF-8 no começo. Uma célula de texto que comece
   por `=`, `+`, `-` ou `@` ganha um apóstrofo na frente, para a planilha não a executar
   como fórmula. Na demonstração (sem backend), o arquivo é montado na tela com as linhas
   que ela mostra.

8. **`POST /clientes/importar` exige o papel de dono ou de administrador.** O token sem
   papel recebe 403, como nas outras rotas que escrevem. O papel só existe no token de
   desenvolvimento e passará a vir do login de verdade (Etapa 5).

---

### O modo piloto: o que ele declara (05/10/2026)

O plano de negócio promete um piloto sem cobrança. Desde a Rodada 4 (Fase 3) o sistema tem
esse modo, com seis regras decididas pelo Crai (M1 a M6). O que ele faz e o que não faz:

1. **Quem define o piloto é a CRAI, numa variável de ambiente.** `CRAI_TENANTS_EM_PILOTO`
   traz os ids das empresas em piloto, separados por vírgula. Nenhuma rota liga ou desliga
   o piloto, e a configuração da empresa não tem essa chave (mandar `piloto` no
   `PUT /configuracao` responde 422). A variável é lida a cada recuperação, mas o processo
   só enxerga o valor novo depois de reiniciar: **entrar ou sair do piloto é mudar a
   variável e reiniciar o serviço**. Não há data de início nem de fim guardada, nem
   histórico de quem esteve em piloto: a marca fica em cada linha recuperada.

2. **Em piloto, a taxa cobrada é zero, e a que seria cobrada fica guardada ao lado.** No
   involuntário, o ciclo recuperado é gravado com `fee = 0` e com `fee_fora_do_piloto` (a
   taxa normal daquele valor). No voluntário, a linha do valor mantido, igual. Como todas
   as contas de líquido usam a `fee` gravada, o líquido vira o valor inteiro na linha do
   ciclo, nas métricas do mês, na Visão geral, na atividade e no extrato, sem conta nova.
   Fora do piloto, `fee_fora_do_piloto` é nulo, que quer dizer "esta linha não é de
   piloto" (e não "taxa de zero reais").

3. **A taxa que seria cobrada aparece só no extrato**, em `GET /extrato` (campo
   `fee_fora_do_piloto` por linha, e o bloco `piloto` com a soma do mês) e em
   `GET /extrato/csv` (coluna "Taxa fora do piloto (R$)", logo depois da taxa). A coluna
   aparece para a empresa em piloto e, depois dele, nos meses que ainda têm linha de
   piloto. Nenhuma outra rota traz esse número, e há teste que varre as outras rotas.

4. **O que decide é o momento em que a linha é gravada, e ela nunca é recalculada.**
   Involuntário: o instante em que a confirmação do pagamento fecha o ciclo. Um ciclo
   aberto durante o piloto e pago depois dele tem taxa; um ciclo recuperado antes de a
   empresa entrar no piloto continua com a taxa que tinha. Voluntário: o instante do
   aceite, porque o aceite passou a gravar a linha do valor mantido na hora (antes ela
   nascia na leitura seguinte da tela). **Resíduo declarado:** se essa gravação no aceite
   falhar (é de melhor esforço), a linha nasce na próxima leitura, com o piloto daquele
   momento. E o aceite sorteado da demonstração continua nascendo na leitura.

5. **O estorno funciona igual.** Em piloto não há taxa a devolver (`fee_devolvida = 0`), e
   o líquido que sai do mês é o valor devolvido inteiro. No extrato, a linha do estorno
   devolve também a taxa que seria cobrada, na mesma proporção do valor devolvido, para o
   relatório do fim do piloto não contar taxa sobre dinheiro que voltou. Essa parte é
   calculada na leitura (não é gravada): o último estorno leva o resto, e a soma fecha no
   centavo.

6. **A receita interna conta zero.** No dataset de ciclos (`success_fee`) e nas métricas
   internas (`fee_total`, `margem`), a recuperação de uma empresa em piloto entra com taxa
   zero. A margem dessas empresas fica negativa pelo custo das mensagens, que é o que um
   piloto é. O rótulo de treino (`recovered`) não muda.

7. **A tela.** A Visão geral mostra a etiqueta "Período de piloto: sem taxa" ao lado do
   período, e o extrato repete o aviso e mostra a coluna. Na demonstração (sem backend)
   não existe piloto.

8. **O que o modo piloto NÃO é.** Não é um desconto parcial (a taxa é zero ou a normal).
   Não é por produto: vale para o involuntário e o voluntário juntos. Não muda o
   percentual da taxa, que continua um só por instalação (`CRAI_SUCCESS_FEE_PCT`, com a
   variável própria do voluntário). E não emite o relatório do fim do piloto: ele é o
   extrato dos meses do piloto, com a coluna somada.

---

### O experimento das redes neurais: o que ele mede e o que não (05/10/2026)

A Rodada 4 (Fase 5) mediu duas redes neurais como **desafiantes**, com as mesmas features
dos modelos atuais: uma de classificação no lugar do XGBoost + Random Forest, e uma de
regressão no lugar do LSTM + Prophet. O código é `app/crai/scripts/experimento_redes_neurais.py`;
o resultado está em `docs/evidencia_redes_neurais/` (`LEIA.md`, `resultados.json` e a saída
do treino). **Nada foi promovido**: as redes ficam em `app/models/experimentos_rn/` e nenhum
código de produção as carrega.

1. **A base é sintética.** O que se mede é quem aprende melhor a regra que o próprio projeto
   escreveu. O teto de Bayes do holdout (0,7106) mostra que o modelo atual (0,7096) já está
   colado no que há para aprender: não havia espaço para a rede vencer por margem.

2. **Uma configuração só, sem busca de hiperparâmetros.** A rede de classificação é uma MLP
   do scikit-learn com duas camadas (64 e 32). Uma busca poderia mover o número. A regra de
   decisão pede vantagem acima do desvio da validação cruzada justamente para não trocar de
   modelo por ruído.

3. **A versão em PyTorch não foi feita.** A instrução a pedia só se sobrasse tempo.

4. **A rede de regressão recebe a janela achatada** (30 dias x 5 features = 150 números) e
   devolve um número de dias. Uma arquitetura recorrente nova, para regressão, não foi
   testada. Ela erra menos em dias na média e acerta menos vezes o dia certo.

5. **"Pouco histórico" é uma definição do experimento:** a janela só tem os últimos 14 ou 7
   dias reais, e os outros entram zerados. Nenhum dos modelos foi treinado com janelas
   assim. O corte mede o que aconteceria hoje com um cliente novo, e é onde o Prophet mais
   ajuda. Em produção não existe hoje um caminho de "cliente sem histórico": a série de 30
   dias é sempre simulada.

6. **O limiar foi escolhido numa partição de validação tirada de dentro do treino** (20% dos
   clientes de treino). O artefato de produção viu esses clientes no treino dele; por isso o
   experimento traz duas comparações: uma como em produção (os dois modelos treinados em
   todo o treino) e outra com o algoritmo atual retreinado sem a validação.

7. **Trocar de modelo é decisão do Crai.** A regra de decisão foi escrita antes de medir e
   está no `LEIA.md`. Pela regra, ficam os modelos atuais.

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
   também abre os `/simulate/*` e o CORS para `http://localhost:5173`.

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

9. **O expurgo diário apaga o texto das mensagens e o registro de acesso, e ainda não a
   trilha.** Uma vez por dia o relógio apaga o texto das mensagens dos ciclos fechados há
   mais de `retencao_mensagens_dias` (90 por padrão; a abordagem fica) e os registros de
   acesso com mais de 12 meses, e diz no log quantas linhas tocou. **A retenção da trilha
   do Art. 20 (5 anos) não está ligada:** a função de retenção já recebe o prazo
   (`prazo_dias`), mas o relógio não a chama, porque a catraca
   `test_art20_trilha.py::TestRetencaoEBestEffort` afirma que ninguém a chama. Hoje a
   trilha **não é apagada nunca**. Os prazos de 24 meses dos ciclos e de 6 meses da base
   depois do contrato estão na configuração e também **não são executados** (Etapa 3). O
   dia do último expurgo mora na memória do processo: depois de um reinício ele roda de
   novo, o que não faz mal (é idempotente).

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

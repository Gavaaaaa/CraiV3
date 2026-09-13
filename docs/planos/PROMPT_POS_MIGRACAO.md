# CRAI — trabalho depois da migração (sábado 12/09)

> **Pré-requisito:** o `MIGRACAO_CRAIV3.md` já foi executado e você já commitou e
> empurrou para o `Gavaaaaa/CraiV3`. Se ainda não, rode aquele primeiro.
>
> **Como usar:** entregue este arquivo INTEIRO ao agente, de uma vez. Ele lê tudo,
> executa tarefa por tarefa na ordem, e para depois de cada uma esperando você
> digitar `ok` (ou `segue`). Você não precisa colar mais nada.
>
> **Fora de escopo hoje:** Supabase e desenvolvimento de dashboard. As duas
> dependem de decisão sua ou de conversa com outra pessoa.
>
> **Ordem:** 1 → 2 → 3 → 4 → 5 → 6. A 7 é bônus, só se sobrar tempo.
> A tarefa 2 produz o documento que você leva para a conversa com o dev do frontend.
> A tarefa 3 é a que responde o professor.

---

## PROMPT — entregue daqui para baixo ao agente

```
Leia este documento inteiro antes de executar qualquer coisa, depois execute
TAREFA POR TAREFA, na ordem.

════════════════════════════════════════════════════════════════════════
CONTEXTO
════════════════════════════════════════════════════════════════════════

A CRAI é um Retention OS para SaaS B2B brasileiro. Dois pipelines autônomos:
churn involuntário (falha de cobrança via Pix Automático) e churn voluntário
(desengajamento comportamental). Orquestração em LangGraph, backend FastAPI.
Invariante do produto: ZERO escalonamento humano, em nenhum caminho de execução.

VOCÊ ESTÁ no repositório `Gavaaaaa/CraiV3`, recém-migrado. O que já aconteceu,
numa sessão anterior, e você NÃO precisa refazer:

  - Os 77 commits do repositório antigo foram trazidos com histórico.
  - A árvore foi reorganizada: os planos foram para `docs/planos/`, as
    auditorias para `docs/historico/auditorias/`, a demo antiga para
    `docs/historico/demo-antiga/`. A pasta `archive/` de protótipos não veio.
  - O patch de treino do repositório `Gavaaaaa/Base-de-dados` foi aplicado:
    7 arquivos modificados e 8 novos, incluindo `app/crai/ml/voluntary_risk.py`,
    `app/crai/ml/calibracao.py` e `app/models/calibracao.json`.
  - Do `Gavaaaaa/mvp-crai` vieram duas coisas: a base de 500 clientes em
    `painel/exemplos/base_exemplo_clientes.csv` e o `docs/LIMITACOES.md`. Nada
    da parte visual dele atravessou, e isso é decisão de produto tomada.
  - `painel/` existe e está praticamente vazia, esperando o dashboard.

O QUE AINDA NÃO FOI FEITO, e é por onde começamos: as dependências do patch
nunca foram instaladas e a suíte nunca rodou com ele aplicado.

O pacote Python está em `app/crai/`; os testes em `app/tests/`. Rode sempre a
partir de `app/`.

FORA DE ESCOPO HOJE, e isto é firme — não comece, não prepare, não sugira:
  - Qualquer coisa que dependa de Supabase (login, JWT, chamar `/insights` ou
    `/clientes/importar` pela API, `/metrics/recovery`). A conta ainda não
    existe e será criada por mim depois. Os TESTES não precisam dela: a suíte
    aponta `CRAI_CLIENTES_DB` para um SQLite temporário.
  - Qualquer desenvolvimento de dashboard: HTML, CSS, JavaScript, front-end. A
    stack ainda está sendo decidida com a pessoa que cuida do frontend.
Se uma tarefa parecer exigir uma dessas duas coisas, PARE e me diga — não
contorne por conta própria.

O DIAGNÓSTICO QUE MOTIVA O DIA — são as duas críticas do professor:
  1. "O churn voluntário é apenas uma fórmula engessada." Correto:
     `app/crai/churn_voluntary/risk_scorer.py` decide por REGRAS FIXAS, iguais
     para toda base de cliente. `Cancellation Page Viewed` → 0,90;
     `Downgrade Clicked` → 0,75; `Session Started` →
     min(1, dias/30 × 0,7 + max(0,(5−features)/5) × 0,3). O 30 e o 5 são
     constantes globais de módulo.
  2. "Ele só analisa a base de teste do GitHub e não se adapta a cada base."
     Meio correto: a IMPORTAÇÃO aceita a base de qualquer empresa e funciona. O
     que não se adapta é o MOTOR — os limiares e a fórmula são absolutos.

MAIS CONTEXTO QUE VOCÊ PRECISA TER:
  - O encaixe plugável de modelo existe (`carregar_modelo()` procura
    `app/models/voluntary_risk.joblib`) mas o arquivo não existe:
    `modelo_ativo()` devolve False. Isso é o estado esperado hoje.
  - O rótulo do dataset voluntário é CIRCULAR:
    `churn = Bernoulli(_risco_por_regras(...) + ruído)`. O modelo aprende a
    própria fórmula que deveria substituir — candidato AUC 0,752 contra teto
    0,758 das regras. É teto matemático, não falta de dado. Consertar isso é
    trabalho de outra sessão; hoje só precisamos saber que é assim.
  - Curva de volume medida do classificador: 1.000 → 0,626 · 3.000 → 0,681 ·
    6.000 → 0,686 · 12.000 → 0,696.
  - O gateway de pagamento é SIMULAÇÃO nesta fase e está certo assim. Ele
    exemplifica como vai funcionar quando a plataforma contratante cobrar pelo
    gateway da CRAI e os dados daquele cliente forem armazenados para
    identificar churn. NÃO invista esforço em PSP real, credencial ou webhook
    assinado de produção.

TRÊS REGRAS QUE VALEM EM TODAS AS TAREFAS

REGRA 1 — NÃO COMMITE NADA. Em nenhuma tarefa: nada de `git commit`,
`git push`, `git merge`, `git tag`, `git add`, `git stash`, `git rebase`, e nada
de mexer em remoto. Deixe as mudanças na working tree. Onde a tarefa precisar de
um comando git que ESCREVE, escreva o comando para eu rodar. Commit é decisão
minha, sobre diff que eu li.

REGRA 2 — RELATÓRIO E PARADA. Ao fim de CADA tarefa, entregue este relatório e
PARE. Não comece a próxima antes de eu responder `ok`.

    RELATÓRIO DA TAREFA <n>
    1. O que fiz, em ordem — uma linha por ação
    2. Arquivos criados, alterados ou movidos — caminho completo, um por linha
    3. Comandos que EU rodei
    4. Comandos que DEIXEI para você rodar
    5. Gate: passou ou não passou, com a saída colada
    6. O que NÃO fiz, e por quê
    7. O que quebrou, ficou duvidoso, ou eu decidi por conta própria
    AGUARDANDO `ok` PARA SEGUIR.

REGRA 3 — nenhuma tarefa muda o comportamento observável dos agentes além do
que ela pedir explicitamente. Ao fim de cada tarefa rode o gate E
`cd app && pytest tests/ -q`. Se algo antigo quebrar, PARE e avise em vez de
"consertar" o teste. Um teste que quebra é informação, não obstáculo.

Não edite `app/README.md` — tem dono.

Confirme que entendeu o contexto, o que está fora de escopo e as três regras.
Depois comece pela TAREFA 1.

════════════════════════════════════════════════════════════════════════
TAREFA 1 — Instalar e validar a suíte  (≈ 45 min, a maior parte esperando)
════════════════════════════════════════════════════════════════════════

É o gate que ficou pendente da migração. Nada depois disso vale se este não
passar, e descobrir um problema de ambiente HOJE custa uma hora — descobrir na
véspera da apresentação custa a apresentação.

1. Instalar:
       cd app && pip install -r requirements.txt
   O `requirements.txt` traz torch, xgboost e prophet, que são pesados e falham
   com alguma frequência no Windows. Se QUALQUER instalação falhar, PARE e me
   diga exatamente qual pacote e com que erro. NÃO troque versão por conta
   própria: o `requirements-lock.txt` trava as versões usadas nas medições já
   publicadas (sklearn 1.5.2, xgboost 2.1.1, torch 2.13.0, prophet 1.4.0), e
   mudar uma delas invalida a comparação com a evidência do repositório de dados.

2. Rodar a suíte inteira:
       cd app && pytest tests/ -q

3. Me reportar o número exato. A referência é 1039 passed / 5 skipped num clone
   sem modelos treinados em `app/models/`. Se der diferente, quero o diff dos
   nomes dos testes, não só a contagem.

GATE: a suíte roda e você me diz o número. Se houver falha, ela é reportada com
o traceback inteiro e NÃO é consertada nesta tarefa.

Entregue o RELATÓRIO DA TAREFA 1 e pare.

════════════════════════════════════════════════════════════════════════
TAREFA 2 — Congelar o contrato da API  (≈ 1 h)
════════════════════════════════════════════════════════════════════════

NÃO é desenvolvimento de dashboard. É documentação de API mais arquivos JSON de
exemplo. É o que permite que o dashboard seja construído depois por quem for, na
stack que for, sem esperar o backend — e é o documento que eu vou entregar para
a pessoa do frontend.

1. `docs/CONTRATO_PAINEL.md` com CINCO endpoints, JSON campo a campo, tipo a
   tipo, dizendo o que pode vir nulo:

   JÁ EXISTEM — documente exatamente como estão HOJE, lendo o código, não a
   documentação antiga:

     POST /clientes/importar
         multipart. Devolve o relatório de importação: colunas reconhecidas,
         colunas ausentes, rejeitados (com número da linha DA PLANILHA e o
         motivo), linhas_sem_dado_comportamental. Leia
         `app/crai/churn_voluntary/importacao.py`.

     GET /insights
         ?limite=&criticidade_minima=. Devolve total_clientes,
         clientes_em_risco[], gerado_em, filtros. Cada linha:
         customer_id_externo, risk_score (PODE SER NULL), criticality,
         explicacao, mrr, billing_profile, days_since_last, features_used_30d,
         origem, atualizado_em. Leia `app/crai/api/app.py` e
         `app/crai/churn_voluntary/insights_unificados.py`.

         Documente com destaque que `dado_insuficiente` tem risk_score NULL e
         NUNCA 0.0, e por quê: tratar ausência de dado como "sem risco" é o erro
         mais caro possível num produto de retenção, porque silencia exatamente
         o cliente que já parou de usar. É invariante do produto, e quem for
         desenhar a tela precisa saber que não pode tratar null como zero.

   NÃO EXISTEM — especifique agora, para implementar na semana que vem:

     GET /modelos/status
         por tenant, para cada modelo: nome, ativo (bool), algoritmo,
         treinado_em, n_amostras, métrica principal e valor, e
         origem_da_decisao ("modelo" | "regras").

     POST /modelos/retreinar
         dispara o retreino do candidato daquele tenant; devolve promovido
         (bool), auc_candidato, auc_campeao, motivo.

     GET /resultado
         ?mes=. Devolve mrr_recuperado, mrr_salvo, ganho_incremental, fatura, e
         a memória de cálculo. Deixe escrito no contrato que, nesta fase, os
         números de recuperação vêm de gateway SIMULADO, e que a interface deve
         dizer isso ao usuário. Simulação declarada na tela é honesta;
         descoberta numa pergunta, não.

   Documente também o cabeçalho de autenticação que cada endpoint espera, mesmo
   sem o Supabase configurado ainda — quem for construir a tela precisa saber
   que o token existe e onde ele entra.

2. `painel/fixtures/` com uma resposta de exemplo realista por endpoint:
       importar.json   insights.json   modelos_status.json
       modelos_retreinar.json   resultado.json
   Use `painel/exemplos/base_exemplo_clientes.csv` (500 clientes) como fonte de
   nomes e valores plausíveis. O `insights.json` precisa conter os quatro casos
   que a tela tem que saber desenhar: um cliente crítico por risco, um crítico
   por valor de MRR, um padrão, e um `dado_insuficiente` com risk_score null.

3. `painel/README.md`, meia página: o que é cada fixture, e a regra de que o
   consumidor troca entre "fixtures" e "API real" por UMA variável.

NÃO crie HTML, CSS nem JavaScript nesta tarefa. Só Markdown e JSON.

GATE: os cinco endpoints documentados com JSON completo; as cinco fixtures
existindo e sendo JSON válido (valide com `python -m json.tool`); o
insights.json contendo os quatro casos.

Entregue o RELATÓRIO DA TAREFA 2 e pare.

════════════════════════════════════════════════════════════════════════
TAREFA 3 — O risco se adapta à base do cliente  (≈ 2 h 30)
════════════════════════════════════════════════════════════════════════

Esta é a resposta direta à crítica do professor, e é a tarefa mais importante do
dia. Hoje `_risco_por_regras` compara `days_since_last` contra a constante 30 e
`features_used_30d` contra a constante 5 — iguais para toda base do mundo. Um
SaaS cujos clientes entram todo dia e outro cujos clientes entram uma vez por
mês recebem a mesma régua.

DECISÃO DE ESCOPO, para esta tarefa ficar pequena e segura: NÃO mexa em schema
de banco e NÃO grave distribuição em lugar nenhum.
`app/crai/churn_voluntary/batch_scoring.py::pontuar_base(tenant_id)` já lê a
base inteira daquele tenant antes de pontuar — calcule os percentis ali, em
memória, a partir das linhas que ele já tem na mão. Isso elimina migração de
tabela, elimina dado velho, e é exatamente o caminho que a apresentação usa.
Guardar a distribuição para o caminho do SDK (evento pontual, que não tem base
para comparar) é uma tarefa separada, de outra sessão. NÃO faça hoje.

IMPLEMENTAR:

1. Em `batch_scoring.py`, calcular da base do tenant os percentis 50, 75 e 90 de
   `days_since_last` e de `features_used_30d`, ignorando os nulos, e contar
   quantas linhas entraram no cálculo.

2. Em `risk_scorer.py`, uma função nova que recebe esses percentis e devolve o
   risco pela POSIÇÃO do cliente na própria base, em vez de pelas constantes. A
   função de regras atual continua existindo, intacta, e continua sendo o chão
   do sistema.

3. REGRA DE SEGURANÇA, obrigatória: com menos de 30 linhas utilizáveis na base,
   ou sem percentis calculáveis, cai no comportamento de HOJE, com número
   idêntico ao que sai hoje. Os testes de regressão existentes precisam
   continuar verdes SEM nenhuma alteração neles. Se algum precisar mudar, você
   introduziu uma regressão — pare e me avise.

4. `explicar()` em `batch_scoring.py` passa a dizer qual régua foi usada, em
   português natural:
       "sem login há 47 dias — acima de 90% da sua base"     (régua da base)
       "sem login há 47 dias"                                 (régua global)
   Essa frase é o que eu vou ler em voz alta na apresentação. Escreva-a para ser
   entendida por um dono de SaaS, não por um engenheiro.

5. A linha do ranking passa a carregar `origem_da_regua`: "base_do_tenant" ou
   "padrao_global". Atualize o `docs/CONTRATO_PAINEL.md` e a fixture
   `insights.json` da tarefa 2 para refletir o campo novo.

GATE:
    cd app && pytest tests/test_batch_scoring.py -v
    cd app && pytest tests/test_risk_pluggable.py -v
    cd app && pytest tests/ -q
Testes NOVOS a criar (a suíte roda com SQLite, não precisa de Supabase):
  - Duas bases com perfis OPOSTOS — uma de uso diário (mediana de 1 dia sem
    login) e outra de uso mensal (mediana de 25 dias) — produzem risco
    DIFERENTE para um cliente com os MESMOS números absolutos. Este é o teste
    que prova a adaptação, e é o mais importante do dia.
  - Base com menos de 30 linhas produz exatamente o risco de hoje.
  - Base sem nenhuma coluna comportamental continua devolvendo
    `dado_insuficiente` com risk_score null, e não 0.0.
Mostre, lado a lado, o mesmo cliente pontuado nas duas bases, com as duas
explicações em português.

Entregue o RELATÓRIO DA TAREFA 3 e pare.

════════════════════════════════════════════════════════════════════════
TAREFA 4 — As bases de demonstração  (≈ 45 min)
════════════════════════════════════════════════════════════════════════

Sem elas, a tarefa 3 é um teste unitário que ninguém vê. Com elas, é uma
demonstração de dois minutos.

1. Um script `app/crai/scripts/gerar_bases_demo.py` que gera dois CSV de 500
   clientes cada, com semente fixa (reprodutíveis), nas colunas que
   `POST /clientes/importar` espera: customer_id_externo, mrr, billing_profile,
   days_since_last, features_used_30d, email.

       painel/exemplos/base_uso_diario.csv
           SaaS de uso intenso: mediana de days_since_last perto de 1,
           features_used_30d alto. Aqui, 7 dias sem login já é um sinal.
       painel/exemplos/base_uso_mensal.csv
           SaaS de uso esporádico, um fechamento por mês: mediana de
           days_since_last perto de 25. Aqui, 7 dias sem login é rotina.

   Em AMBAS, incluir de propósito um punhado de clientes com days_since_last
   IDÊNTICO (por exemplo exatamente 7 e exatamente 20 dias), com o mesmo MRR e o
   mesmo perfil. São eles que, pontuados nas duas bases, mostram na tela que a
   régua mudou. Marque-os com um id reconhecível, do tipo `ANCORA-07-A`, para eu
   conseguir achá-los rápido na demonstração.

   Incluir também, em ambas, algumas linhas sem dado comportamental, para a tela
   ter o caso `dado_insuficiente` de verdade.

2. Um `painel/exemplos/README.md` de cinco linhas: o que é cada uma das três
   bases (as duas geradas mais a `base_exemplo_clientes.csv` que já está lá),
   qual o perfil de uso de cada uma, e quais são os clientes-âncora.

GATE:
    cd app && python -m crai.scripts.gerar_bases_demo
    cd app && pytest tests/ -q
Os dois CSV existem, abrem no Excel sem erro de encoding, e rodar o script duas
vezes gera arquivos IDÊNTICOS — se a semente não estiver travada, a demonstração
muda de números entre um ensaio e outro. Mostre a mediana de days_since_last de
cada base e a lista dos clientes-âncora.

Entregue o RELATÓRIO DA TAREFA 4 e pare.

════════════════════════════════════════════════════════════════════════
TAREFA 5 — Rodar o treino e medir os tempos  (≈ 1 h, a maior parte esperando)
════════════════════════════════════════════════════════════════════════

Com as dependências instaladas na tarefa 1, agora dá para reproduzir as duas
rodadas de treino documentadas no repositório de dados. Elas provam que a
camada de IA funciona de ponta a ponta, e os TEMPOS que você medir são o que eu
preciso para dimensionar o treino em escala (95 mil linhas) da semana que vem.

    cd app
    python -m crai.scripts.preparar_amostra_real
    python -m crai.scripts.train_all --fonte sintetico_calibrado \
        --classifier-samples 3000 --anomaly-samples 5500 \
        --payday-customers 600 --voluntario-samples 2000 \
        --saida-json rodada_baixa.json
    python -m crai.scripts.train_all --fonte sintetico_calibrado \
        --classifier-samples 6000 --anomaly-samples 11000 \
        --payday-customers 1200 --voluntario-samples 4000 \
        --saida-json rodada_alta.json

Cronometre CADA rodada e, se o script permitir, cada modelo dentro dela. O LSTM
do payday é o que mais deve pesar quando o volume subir, e eu preciso saber
quanto.

NÃO promova o candidato voluntário. Ele continua candidato e o scorer continua
nas regras — isso é deliberado, porque o rótulo circular faz com que ele não
tenha nada a acrescentar hoje.

GATE:
    cd app && pytest tests/ -q
Esperado: com os modelos treinados em `app/models/`, dois testes de
`tests/test_metricas_declaradas.py` REPROVAM, porque a AUC medida (0,667) está
abaixo do piso [0,70; 0,92] que o próprio repositório exige. É o gate de
honestidade do projeto funcionando — NÃO conserte mexendo no teste nem no piso.
Registre as duas falhas no relatório.
Mostre: as AUCs das duas rodadas e os tempos medidos.

Entregue o RELATÓRIO DA TAREFA 5 e pare.

════════════════════════════════════════════════════════════════════════
TAREFA 6 — DECISOES.md  (≈ 30 min; você prepara, eu decido)
════════════════════════════════════════════════════════════════════════

Não decida nada. Prepare o arquivo com as opções e as consequências; a decisão é
minha e eu preencho.

Três contradições foram encontradas cruzando o `CRAI_sistema.docx`, o
`Plano de Negócio CRAI` e o código:

  a) MENSALIDADE
     Plano de negócio (4.2): "não há mensalidade, nem taxa de implantação, nem
     qualquer valor fixo devido, em nenhum dos dois planos".
     CRAI_sistema (14.1): Standard R$ 337/mês, Premium R$ 450/mês, e registra
     que "o deck vence".
     Código: não existe mensalidade em lugar nenhum.

  b) SUCCESS FEE
     Os dois documentos: 25% sobre recuperação, 20% sobre retenção.
     Código: `SUCCESS_FEE_PCT_PADRAO = 0.15` em `app/crai/config.py`.

  c) FAIXA DE ICP
     Plano de negócio: MRR de R$ 25 mil a R$ 500 mil.
     CRAI_sistema: R$ 10 mil a R$ 500 mil.
     README do repositório: R$ 10k–R$ 500k.

IMPLEMENTAR:

1. `DECISOES.md` na raiz, uma seção por item, cada uma com: as fontes e o que
   cada uma diz, o espaço em branco para eu escrever a decisão e a data, e — o
   mais útil — a LISTA DOS ARQUIVOS que precisam ser corrigidos em cada cenário.
   Varra o repositório de verdade para montar essa lista: `app/crai/config.py`,
   `app/tests/test_config_pricing.py`, os READMEs, e o que mais citar preço ou
   faixa de MRR.

2. NÃO altere `SUCCESS_FEE_PCT_PADRAO` ainda. Deixe anotado no DECISOES.md
   exatamente qual linha mudar e qual teste trava esse valor, para a alteração
   ser de um minuto quando eu decidir.

GATE:
    cd app && pytest tests/ -q
DECISOES.md existe com as três seções e a lista de arquivos afetados por
cenário. Nenhum valor foi alterado.

Entregue o RELATÓRIO DA TAREFA 6 e pare.

════════════════════════════════════════════════════════════════════════
TAREFA 7 — BÔNUS, só se sobrar tempo  (≈ 2 h)
════════════════════════════════════════════════════════════════════════

Mapeamento de dados pessoais para a LGPD. Não é código e não depende de nada. É
o documento que sustenta toda a camada de conformidade, e a banca vai pedir,
porque a CRAI decide sozinha sobre pessoas.

1. `docs/lgpd/mapeamento-de-dados.md`: varra o código de verdade — schemas,
   modelos Pydantic, DDL, as escritas em `app/crai/dunning/recovery_log.py`,
   `app/crai/churn_voluntary/retention_log.py`,
   `app/crai/churn_voluntary/clientes_importados.py`, o estado persistido do
   `app/crai/churn_voluntary/offer_bandit.py`, e o cache de JWKS em
   `app/crai/accounts/`. Uma linha por campo:
       tabela ou arquivo · campo · é dado pessoal? · é sensível? · base legal
       aplicável · prazo de retenção proposto · para quais terceiros vai
   Terceiros a considerar: Pagar.me, HubSpot, Segment, BSP do WhatsApp,
   Anthropic (Claude API), Supabase.
   Onde não der para determinar pelo código, escreva "NÃO VERIFICADO". Nunca
   preencha por suposição.

2. `docs/lgpd/decisao-automatizada.md`: liste cada decisão automatizada que o
   sistema toma — score de risco, escolha de oferta pelo bandit, decisão de
   retentativa —, qual modelo ou regra a produz, que features entram, e onde
   ficaria registrada a explicação. É a base do Art. 20 da LGPD e antecipa a
   exigência de explicabilidade do Marco Legal da IA.

GATE (revisão humana): os dois arquivos existem com todos os campos preenchidos
ou marcados "NÃO VERIFICADO". E um achado a verificar explicitamente: nenhuma
linha pode mostrar CPF, telefone, e-mail ou chave Pix entrando em pipeline de
ML. Se alguma mostrar, isso é um ACHADO e precisa aparecer em destaque no
relatório — é violação de uma invariante declarada do produto.

Entregue o RELATÓRIO DA TAREFA 7 e pare.

════════════════════════════════════════════════════════════════════════
FECHAMENTO
════════════════════════════════════════════════════════════════════════

Depois da última tarefa que der tempo, escreva `ESTADO_12SET.md` com:
  1. Quais tarefas passaram o gate e quais não, sem maquiagem.
  2. As AUCs medidas e os TEMPOS das duas rodadas de treino.
  3. Qualquer instalação que falhou, com o erro.
  4. O que quebrou e você não consertou.
  5. Um resumo em três linhas do que eu preciso decidir antes da próxima sessão.

E me dê a lista dos comandos git que ficaram esperando por mim, se houver.
```

---

## Depois de hoje

Com isto pronto, o que falta para a apresentação é: o rótulo por variável latente
(quebra a circularidade do dataset e faz o modelo poder ganhar das regras), a
expansão das features do risco voluntário de 6 para 14 em três camadas, os três
endpoints novos que o contrato da tarefa 2 especifica, e o dashboard — que
depende da conversa com o frontend e da conta do Supabase.

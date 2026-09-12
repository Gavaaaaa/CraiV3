# CRAI — montar o repositório novo (CraiV3) do zero

> **Como usar:** entregue este arquivo INTEIRO ao agente, de uma vez. Ele lê tudo,
> executa etapa por etapa, e para depois de cada uma esperando você digitar `ok`
> (ou `segue`). Você não precisa colar nada além deste arquivo.
>
> **Onde rodar:** na pasta local que você criou para o projeto novo (por exemplo
> `E:\CraiV3`). Ela **não** precisa ser um clone do GitHub — é uma pasta de
> trabalho comum, e é nela que o repositório vai ser montado. O commit e o push
> para o `Gavaaaaa/CraiV3` são seus, no fim, com os comandos que a etapa 7 deixa
> prontos.
>
> **O que este arquivo faz:** traz o código do repositório antigo COM os 77
> commits de histórico, remove o que não deve atravessar, reorganiza as pastas,
> aplica o patch de treino, resgata as duas coisas que sobrevivem do `mvp-crai`, e
> deixa tudo pronto para você commitar. **O agente não commita nada.**

---

## PROMPT — entregue daqui para baixo ao agente

```
Você vai montar o repositório novo da CRAI. Leia este documento inteiro antes de
executar qualquer coisa, depois execute ETAPA POR ETAPA, na ordem.

════════════════════════════════════════════════════════════════════════
CONTEXTO
════════════════════════════════════════════════════════════════════════

A CRAI é um Retention OS para SaaS B2B brasileiro: dois pipelines autônomos,
churn involuntário (falha de cobrança via Pix Automático) e churn voluntário
(desengajamento comportamental), orquestrados em LangGraph. Backend em Python
com FastAPI.

O projeto está espalhado em três repositórios e vai passar a viver em dois:

  Gavaaaaa/Crai         o sistema hoje. SERÁ SUBSTITUÍDO por este repositório
                        novo e depois arquivado.
  Gavaaaaa/mvp-crai     um dashboard em JavaScript que foi DESCARTADO. Só duas
                        coisas não-visuais dele sobrevivem. Será arquivado.
  Gavaaaaa/Base-de-dados  o trabalho de treino dos modelos. FICA COMO ESTÁ —
                        é o repositório de dados e continua vivo. Só o patch
                        dele atravessa para cá.

Você está na pasta do repositório NOVO (CraiV3), que hoje está praticamente
vazia: 1 commit ("Initial commit"), 1 branch (`main`) e 1 arquivo (o README.md
gerado pelo GitHub).

════════════════════════════════════════════════════════════════════════
TRÊS REGRAS QUE VALEM EM TODAS AS ETAPAS
════════════════════════════════════════════════════════════════════════

REGRA 1 — NÃO COMMITE NADA. Em nenhuma etapa: nada de `git commit`,
`git push`, `git merge`, `git tag`, `git add`, `git stash`, `git rebase`, e nada
de criar ou apagar repositório remoto. Você PODE rodar `git remote add`,
`git fetch`, `git status`, `git log` e `git mv`, que não alteram histórico nem
publicam nada. Qualquer comando git além desses, ESCREVA PARA EU RODAR em vez
de rodar. Commit é decisão minha, sobre diff que eu li.

REGRA 2 — RELATÓRIO E PARADA. Ao fim de CADA etapa, entregue este relatório e
PARE. Não comece a etapa seguinte antes de eu responder `ok`.

    RELATÓRIO DA ETAPA <n>
    1. O que fiz, em ordem — uma linha por ação
    2. Arquivos criados, apagados ou movidos — caminho completo, um por linha
    3. Comandos que EU rodei
    4. Comandos que DEIXEI para você rodar
    5. Verificação da etapa: passou ou não, com a saída colada
    6. O que NÃO fiz, e por quê
    7. O que quebrou, ficou duvidoso, ou eu decidi por conta própria
    AGUARDANDO `ok` PARA SEGUIR.

REGRA 3 — NA DÚVIDA, PARE E PERGUNTE. Não contorne, não improvise, não "conserte"
um teste que quebrou. Um caminho que você não encontrou é um achado, não um
obstáculo a driblar.

════════════════════════════════════════════════════════════════════════
ETAPA 1 — Trazer o repositório antigo COM o histórico
════════════════════════════════════════════════════════════════════════

O objetivo é que os 77 commits do repositório antigo — `sprint(ci1)` até
`sprint(cv6)`, cada um com seu gate — venham junto. Eles são a prova do método
de trabalho e valem nota na banca. Copiar arquivos soltos perderia isso.

ONDE VOCÊ ESTÁ: uma pasta LOCAL de trabalho, criada à mão. Ela NÃO é um clone
do GitHub, e não precisa ser. É aqui que o repositório novo vai ser montado; no
fim eu commito e empurro para o CraiV3 com os comandos que você me der na
ETAPA 7.

Mesmo sendo uma pasta local, o conteúdo do repositório antigo entra por git, e
não por cópia de arquivos. O motivo: assim vêm junto os 77 commits de histórico
(`sprint(ci1)` até `sprint(cv6)`, cada um com seu gate), que são a prova do
método de trabalho e valem nota na banca. Copiar arquivos soltos perderia isso,
e daria o mesmo trabalho.

1.1  Veja em que estado a pasta está:
         git remote -v
         git branch
         git log --oneline
         git status

     Três casos possíveis:

       CASO A — a pasta tem `.git` mas nenhum commit.
           `git remote -v` vem vazio, `git log` responde "does not have any
           commits yet", a branch aparece como `master`. Pode haver arquivos
           soltos não rastreados (este próprio documento, por exemplo) — tudo
           bem, eles não atrapalham e não serão apagados.

       CASO B — a pasta não tem `.git` nenhum.
           Rode `git init -b main` e siga pelo CASO A.

       CASO C — a pasta já é um clone do CraiV3 (`origin` aponta para lá e
           existe 1 commit "Initial commit"). Não é o esperado, mas se for,
           me avise no relatório e PARE — o caminho é outro e eu decido.

1.2  Nos casos A e B, rode estes quatro comandos. Todos são não-destrutivos:
     não apagam arquivo, não reescrevem histórico, não publicam nada.

         git remote add origin https://github.com/Gavaaaaa/CraiV3.git
         git remote add antigo https://github.com/Gavaaaaa/Crai.git
         git fetch antigo
         git checkout -b main antigo/main

     O que cada um faz, para você conferir se deu certo:
       - o primeiro registra para onde eu vou empurrar no fim (o CraiV3);
       - o segundo registra de onde vem o conteúdo (o repositório antigo);
       - o terceiro baixa os 77 commits, sem mexer na pasta ainda;
       - o quarto cria a branch `main` já a partir desse histórico, e é ele que
         enche a pasta com todos os arquivos de uma vez.

     A branch `master` do `git init` nunca chegou a existir de verdade (branch
     sem commit é "não nascida"), então ela simplesmente deixa de aparecer. Não
     há nada para apagar e não é preciso `git branch -D`.

     Se o quarto comando reclamar de algum arquivo que seria sobrescrito, PARE e
     me diga qual — significa que a pasta tem um arquivo com o mesmo nome de um
     do repositório antigo, e eu decido o que fazer com ele.

1.3  Confirme:
         git log --oneline | wc -l      → 77
         git branch                     → só `main`
         git remote -v                  → origin = CraiV3, antigo = Crai
     E a pasta agora tem `app/`, `archive/`, `demo/`, `scripts/` e os `.md` na
     raiz.

1.4  Se este documento (`MIGRACAO_CRAIV3.md`) estiver solto na raiz como arquivo
     não rastreado, deixe onde está por enquanto — a ETAPA 6 vai movê-lo para
     `docs/`, junto com o registro da migração.

Entregue o RELATÓRIO DA ETAPA 1 e pare.

════════════════════════════════════════════════════════════════════════
ETAPA 2 — Remover o que não atravessa
════════════════════════════════════════════════════════════════════════

Apague estes caminhos da pasta. Eles continuam existindo no repositório antigo,
que será ARQUIVADO e não deletado — nada se perde de verdade.

    archive/                      864 KB de protótipos pré-unificação, com PNGs
                                  de relatório. Trabalho histórico, sem
                                  consumidor.
    demo/demo_clientes.db         SQLite regenerável.
    demo/demo_insights.html       será substituído pelo dashboard novo.

    app/.env.example              APAGUE. Decisão minha, e o motivo importa: um
                                  professor abriu o repositório, viu um arquivo
                                  começando com `.env` e entendeu que havia
                                  credencial exposta. O arquivo só tinha
                                  placeholders, mas a leitura dele é razoável e
                                  o custo de ser mal interpretado é alto demais
                                  para um repositório que vai ser avaliado.
                                  A informação não se perde: a ETAPA 6 cria um
                                  `docs/CONFIGURACAO.md` com as 22 variáveis
                                  documentadas. Markdown ninguém confunde com
                                  arquivo de segredo.

Não apague mais nada. Em particular, NÃO APAGUE `app/crai/dunning/legacy_card/`:
é código de cartão desativado, mas `app/tests/test_payment_isolation.py` existe
justamente para provar que ele NÃO é importado pelo pipeline ativo e que só a
`PixAutomaticoRetryPolicy` decide retentativa. Apagar o código apagaria o teste
que prova a invariante. É um controle, não um esquecimento.

VERIFICAÇÃO: `git status` mostra as remoções e mais nada de inesperado.

Entregue o RELATÓRIO DA ETAPA 2 e pare.

════════════════════════════════════════════════════════════════════════
ETAPA 3 — Reorganizar as pastas
════════════════════════════════════════════════════════════════════════

Use `git mv` (preserva o histórico de cada arquivo), não `mv`.

MOVER PARA docs/planos/
    sprints.md
    churn_involuntario_sprints.md
    churn_voluntario_completo.md
    plano_onboarding.md
    prompt_auditoria_completa.txt

MOVER PARA docs/historico/auditorias/
    app/docs/AUDITORIA_01.md
    app/docs/AUDITORIA_01_R2.md  até  AUDITORIA_01_R11.md   (11 arquivos no total)

MOVER PARA docs/historico/
    app/docs/ACHADOS_CANAL_WHATSAPP.md
    app/docs/APROVACAO_SPRINT3.md
    app/docs/baseline_pytest.txt

MOVER PARA docs/historico/demo-antiga/
    demo/demo_server.py
    demo/demo_visual.py
    demo/demo_base_clientes.csv
    (a pasta demo/ fica vazia depois disso — remova a pasta)

CRIAR A PASTA VAZIA painel/  (com um .gitkeep), que vai receber o dashboard
novo mais adiante. Não crie HTML, CSS nem JavaScript agora: a stack do
dashboard ainda está sendo decidida.

FICA EXATAMENTE ONDE ESTÁ, sem tocar em caminho de import:
    app/crai/**, app/tests/**, app/requirements.txt, app/requirements-lock.txt,
    app/README.md, app/test_pipeline.py, app/docs/DATA_CARD.md,
    app/docs/evidencia/**, scripts/pre-commit, .gitignore

(O `app/.env.example` NÃO está nesta lista: ele foi apagado na ETAPA 2, e a
ETAPA 6.6 cria o `docs/CONFIGURACAO.md` no lugar.)

VERIFICAÇÃO: a raiz da pasta deve ter, no máximo: app/ painel/ docs/ scripts/
README.md .gitignore  (mais os arquivos que as etapas seguintes criarem).

O gate desta etapa é DIFERENCIAL, não absoluto. As dependências do projeto ainda
não estão todas instaladas nesta máquina, então a suíte não vai dar 1039 passed
hoje — e não precisa. O que precisa é que ela dê EXATAMENTE O MESMO resultado
antes e depois das movimentações:

    1. ANTES de mover qualquer coisa: `cd app && pytest tests/ -q`. Anote a
       linha final inteira (passed / failed / skipped / errors).
    2. Faça as movimentações.
    3. DEPOIS: rode de novo e compare. Os números têm que ser idênticos.

Qualquer teste que passe a falhar entre as duas rodadas foi quebrado por uma
movimentação: o caminho VOLTA e você me avisa. Não ajuste o teste. Falha que já
existia antes de você mover nada não é problema desta etapa — reporte e siga.

Entregue o RELATÓRIO DA ETAPA 3 e pare.

════════════════════════════════════════════════════════════════════════
ETAPA 4 — Aplicar o patch de treino do Base-de-dados
════════════════════════════════════════════════════════════════════════

O trabalho de treino dos modelos vive hoje num repositório separado, como patch
não aplicado. Enquanto ele não estiver aqui, o sistema não tem modelo nenhum.

4.1  Clone `https://github.com/Gavaaaaa/Base-de-dados.git` numa pasta temporária
     FORA desta (por exemplo `../_tmp_basedados`), leitura apenas.

4.2  Leia `Base-de-dados/patch_crai/APLICAR.md` INTEIRO antes de aplicar
     qualquer coisa, e siga à risca o que ele manda.

4.3  Aplique `patch_crai/crai_treino_modificados.patch`, que modifica 7 arquivos:
         app/crai/ml/anomaly_detector.py
         app/crai/ml/failure_classifier.py
         app/crai/ml/payday_inference.py
         app/crai/ml/synthetic_data.py
         app/crai/scripts/preparar_amostra_real.py
         app/crai/scripts/train_all.py
         app/requirements.txt

4.4  Copie os 8 arquivos novos de `patch_crai/arquivos_novos/` para os caminhos
     correspondentes:
         app/crai/ml/voluntary_risk.py
         app/crai/ml/calibracao.py
         app/crai/scripts/sanity_check_fora_do_dominio.py
         app/crai/scripts/gerar_readme_treino.py
         app/models/calibracao.json
         app/tests/test_train_fonte.py
         app/tests/test_synthetic_data.py
         app/tests/test_readme_treino.py

     O `.gitignore` já trata o caso do `app/models/`: ele ignora `app/models/*`
     mas abre exceção para `!app/models/calibracao.json`. Confirme com
     `git status` que o `calibracao.json` aparece como arquivo novo — se ele
     não aparecer, algo está errado e eu preciso saber.

4.5  Apague a pasta temporária do clone.

4.6  NÃO instale dependências nem rode treino nesta etapa. Isso é assunto de
     outra sessão — aqui só montamos o repositório.

VERIFICAÇÃO: `git status` mostra os 7 modificados e os 8 novos. E rode
`cd app && python -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('crai').rglob('*.py')]"`
para confirmar que nenhum arquivo Python ficou sintaticamente quebrado pelo
patch. (Não rode pytest aqui: o patch muda `requirements.txt` e as
dependências novas ainda não estão instaladas.)

Entregue o RELATÓRIO DA ETAPA 4 e pare.

════════════════════════════════════════════════════════════════════════
ETAPA 5 — Resgatar as duas coisas que sobrevivem do mvp-crai
════════════════════════════════════════════════════════════════════════

O dashboard em JavaScript do `mvp-crai` foi descartado. Dele atravessam DUAS
coisas, e as duas são não-visuais.

5.1  Clone `https://github.com/Gavaaaaa/mvp-crai.git` numa pasta temporária FORA
     desta, leitura apenas.

5.2  Traga exatamente isto:

     base_exemplo_clientes.csv  →  painel/exemplos/base_exemplo_clientes.csv
         500 clientes, 40 KB, já no formato que o importador de base espera.
         É dado, não é interface.

     README.md, seção 14 ("O que é real, o que é simulado e limitações")
                                →  docs/LIMITACOES.md
         NÃO é o arquivo inteiro: extraia só essa seção. Ela é a página de
         honestidade que vai ser mostrada na banca — o que o sistema ainda não
         prova, dito por nós antes de alguém perguntar.

         Ao migrar, conserte duas coisas:

         (a) O texto fala de encoders JS e de `node server.js`, que não existem
             mais aqui. Reescreva essas passagens para o sistema em Python.

         (b) CONFIRA TODOS OS NÚMEROS contra a evidência do repositório de
             dados que você acabou de clonar na etapa 4 (se já apagou, clone de
             novo só para conferir). Já sei de uma divergência: o texto do
             mvp-crai diz "AUC 0,703", de uma rodada antiga; a medição atual em
             `evidencia/rodada_alta.json` é 0,6669, e o piso que o próprio
             repositório exige é 0,70. Citar 0,703 na apresentação e ter 0,667
             no repositório aberto é o tipo de contradição que derruba a
             credibilidade de todo o resto. Número sem fonte na evidência vira
             "NÃO VERIFICADO", não fica como estava.

         Mantenha intactas as limitações dos MODELOS: vocabulário de cartão no
         pipeline Pix, base de âncora de 300 transações, licença CC BY-NC-SA da
         Olist (uso acadêmico, não comercial), autoencoder e payday avaliados
         dentro das próprias simulações de treino, bandit com warm start
         simulado, KKBox não ser SaaS B2B brasileiro. E preserve a frase que
         fecha a seção, que é a melhor coisa escrita no projeto: o que a beta
         prova é que o pipeline aprende, explica e decide sobre um sinal; o que
         ela não prova é que a previsão de recuperação vale em produção.

5.3  NADA DA PARTE VISUAL DO mvp-crai ATRAVESSA. Isso é decisão de produto já
     tomada e não é para ser reaberta nem contornada. Fora, sem exceção:
     public/index.html, public/app.js, public/styles.css, public/assets/**
     (INCLUSIVE os PNG de logo), src/**, server.js, data/encoders/** (5,2 MB de
     modelo exportado para JavaScript, sem consumidor aqui), tools/**,
     package.json, teste.txt.

     Se em algum momento parecer que falta um arquivo visual — logo, ícone,
     folha de estilo, layout —, PARE e me pergunte. A identidade visual virá da
     fonte de marca do projeto, junto com o frontend, não de um repositório
     descartado.

5.4  Apague a pasta temporária do clone.

VERIFICAÇÃO: os dois arquivos existem nos lugares certos, e NENHUM arquivo
visual do mvp-crai foi copiado — confirme isso explicitamente no relatório.

Entregue o RELATÓRIO DA ETAPA 5 e pare.

════════════════════════════════════════════════════════════════════════
ETAPA 6 — Os documentos que fazem o repositório se explicar sozinho
════════════════════════════════════════════════════════════════════════

Crie:

6.1  `docs/ESTRUTURA.md` — o que vive em cada pasta deste repositório e o que
     vive no `Base-de-dados`. Uma pessoa nova precisa se orientar sem perguntar.

6.2  `docs/historico/README.md` — cinco linhas dizendo o que ficou no
     repositório antigo (`archive/` de protótipos) e no `mvp-crai` (os encoders
     JS e o dashboard), e que os dois foram arquivados, não deletados.

6.3  `README.md` da raiz — REESCREVER. O atual tem 42 KB e descreve a estrutura
     antiga; ninguém lê 42 KB. Máximo duas telas: o que a CRAI é em um
     parágrafo, o quickstart de como rodar, o que é cada pasta, e o link para o
     repositório de dados. NÃO invente número nenhum: se precisar citar métrica,
     use as do `app/docs/DATA_CARD.md` e diga de onde veio.

6.4  `docs/MIGRACAO_FEITA.md` — o registro do que esta sessão fez: o que veio de
     onde, o que foi apagado, o que foi movido. É o documento que responde
     "cadê o arquivo X?" daqui a duas semanas.

6.5  Se o `MIGRACAO_CRAIV3.md` ainda estiver solto na raiz, mova-o para
     `docs/MIGRACAO_CRAIV3.md` — ele é o roteiro que gerou esta migração e vale
     guardar ao lado do registro dela, não na raiz do projeto.

6.6  `docs/CONFIGURACAO.md` — substitui o `.env.example` que a ETAPA 2 apagou.
     Leia o `.env.example` no repositório ANTIGO (ele ainda está lá, e o
     repositório antigo ainda não foi arquivado) e transcreva as 22 variáveis
     para um documento em Markdown, agrupadas por assunto, cada uma com três
     informações: o que ela faz, se é obrigatória, e o que acontece sem ela.

     As 22, na ordem em que estão hoje:
         ENV · ANTHROPIC_API_KEY · STRIPE_SECRET_KEY · STRIPE_WEBHOOK_SECRET ·
         HUBSPOT_TOKEN · PIX_WEBHOOK_SECRET · CRAI_ENCRYPTION_KEY ·
         SEGMENT_WRITE_KEY · SEGMENT_WEBHOOK_SECRET ·
         CRAI_HIGH_VALUE_MRR_THRESHOLD · CRAI_CS_SIGNATURE_NAME ·
         RETENTION_OUTCOME_WEBHOOK_SECRET · CRAI_SIMULATE_OUTCOMES ·
         CRAI_RETENTION_DB · SUPABASE_PROJECT_URL · SUPABASE_DB_URL ·
         CRAI_CLIENTES_DB · CRAI_SMTP_HOST · CRAI_SMTP_PORT · CRAI_SMTP_USER ·
         CRAI_SMTP_PASSWORD · CRAI_EMAIL_FROM

     Regras para este documento:
       - NENHUM valor de exemplo que pareça credencial. Nada de `sk-ant-...`,
         `whsec_...`, `pat-na1-...`, nem string de conexão com senha. Onde
         precisar mostrar formato, descreva em palavras ("chave da API da
         Anthropic, começa com sk-ant-") em vez de escrever um exemplo.
       - Marque as duas variáveis do Stripe como LEGADO: o webhook existe no
         código por herança do desenho antigo baseado em cartão, e não faz
         parte do caminho ativo, que é Pix Automático via Pagar.me.
       - Abra o documento com uma linha dizendo o que ele é: uma referência para
         você montar o seu `.env` local do zero. Ele não é um arquivo de
         configuração e não deve ser copiado para `.env`.

6.7  Ajuste o hook `scripts/pre-commit`. Ele hoje bloqueia qualquer arquivo
     `.env` estagiado, COM UMA EXCEÇÃO para `.env.example`. Como o arquivo
     deixou de existir, a exceção também sai: remova o
     `grep -v -E '(^|/)\.env\.example$'` do filtro, e atualize o comentário do
     topo do hook, que hoje descreve essa exceção. Depois da mudança, QUALQUER
     arquivo `.env*` estagiado é bloqueado, sem exceção — que é exatamente a
     leitura que queremos que alguém tenha ao abrir o repositório.

6.8  Duas referências ao arquivo apagado precisam de decisão, e ela é minha:
       - `app/README.md`, linha 9: `cp .env.example .env` no quickstart.
       - `app/README.md`: a frase que diz que o `.env.example` já vem
         configurado com `ENV=development`.
     Esse README tem dono e eu pedi que você não o editasse. Então NÃO edite:
     liste no relatório as linhas exatas que ficaram desatualizadas e me
     pergunte se autorizo a correção. No `README.md` da raiz, que você está
     reescrevendo de qualquer forma (6.3), o quickstart já deve nascer certo:
     aponte para o `docs/CONFIGURACAO.md` em vez do arquivo que não existe mais.

     Observação: `app/crai/api/app.py` e `app/crai/integrations/whatsapp_sender.py`
     também mencionam o `.env.example`, mas em comentário e numa checagem de
     placeholder de string — nada quebra. Reporte, não conserte.

VERIFICAÇÃO: os quatro arquivos existem e o README novo cabe em duas telas.

Entregue o RELATÓRIO DA ETAPA 6 e pare.

════════════════════════════════════════════════════════════════════════
ETAPA 7 — Conferência final e os comandos para eu rodar
════════════════════════════════════════════════════════════════════════

7.0  VARREDURA DE SEGREDOS NO HISTÓRICO, antes de publicar qualquer coisa.

     Apagar o `.env.example` na ETAPA 2 tirou o arquivo do topo, mas ele
     continua existindo nos 77 commits que vão para o CraiV3. Antes de tornar
     isso público, quero saber se em algum ponto do histórico entrou um segredo
     DE VERDADE — não um placeholder.

     Use os mesmos padrões que o hook `scripts/pre-commit` já define, varrendo o
     histórico inteiro:

         git log -p --all | grep -nE 'sk-ant-[A-Za-z0-9_-]{8,}|sk_live_[A-Za-z0-9]{8,}|sk_test_[A-Za-z0-9]{8,}|rk_live_[A-Za-z0-9]{8,}|whsec_[A-Za-z0-9]{8,}|pat-na1-[A-Za-z0-9-]{12,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}'

     Procure também por strings de conexão com senha real:

         git log -p --all | grep -nE 'postgres(ql)?://[^:]+:[^@]+@'

     COMO INTERPRETAR o resultado, e isto importa:
       - Placeholder NÃO é achado. `abcdefgh`, a palavra `SENHA`, `sk-ant-...`
         com reticências, `whsec_...` — são modelos de preenchimento e não
         valem nada para ninguém. O próprio código trata `abcdefgh` como marca
         de placeholder (`app/crai/api/app.py`).
       - Segredo real É achado, e muda o plano: PARE, me avise imediatamente e
         NÃO publique nada. Purgar o histórico sem rotacionar a credencial é
         teatro — quem já copiou, copiou. A ordem certa seria rotacionar a
         chave no provedor primeiro, e só depois decidir sobre o histórico.

     Reporte o resultado da varredura de forma conclusiva: "nenhum segredo real
     encontrado" ou a lista do que encontrou, com commit e arquivo.

     Se vier limpo, NÃO reescreva o histórico. Reescrever mudaria o hash dos 77
     commits, que são exatamente a evidência que a migração inteira existiu para
     preservar, e o ganho seria zero — placeholder não é vazamento. Em vez
     disso, acrescente ao `docs/CONFIGURACAO.md` um parágrafo curto registrando
     que o `.env.example` existiu até a migração, que continha apenas
     placeholders, que a varredura não encontrou credencial real no histórico, e
     que o hook de pre-commit passou a bloquear qualquer `.env*`. Uma pergunta
     que já tem resposta escrita deixa de ser um problema numa apresentação.

7.1  Confira a contagem de arquivos por pasta e compare com o esperado. Reporte
     qualquer divergência em vez de "corrigir":

         app/crai/agent/              7
         app/crai/api/                4
         app/crai/churn_voluntary/   11
         app/crai/dunning/            6   (mais legacy_card/ com 3)
         app/crai/ml/                 8   (6 originais + 2 do patch)
         app/crai/integrations/       6
         app/crai/accounts/           4
         app/crai/security/           3
         app/crai/scripts/            6   (4 originais + 2 do patch)
         app/tests/                  35   (32 originais + 3 do patch)
                                          — a suíte inteira fica. Nenhum teste
                                          é descartado nesta migração.
         app/docs/evidencia/          8
         docs/planos/                 5
         docs/historico/auditorias/  11   (AUDITORIA_01 + R2 até R11)
         docs/historico/demo-antiga/  3

7.2  Rode `git status` e me dê o resumo: quantos arquivos modificados, quantos
     novos, quantos apagados, quantos movidos.

7.3  Confirme que existe UMA branch local só (`git branch`) e que ela é `main`.

7.4  Escreva os comandos para EU rodar, em ordem, com uma frase explicando cada
     um. São estes — confira se algum precisa de ajuste pelo que aconteceu nas
     etapas anteriores:

         # 1. revisar o diff antes de qualquer coisa
         git status
         git diff --stat

         # 2. commitar tudo em um commit só, na main
         git add -A
         git commit -m "Migração para o CraiV3: reorganização, patch de treino e resgate do mvp-crai"

         # 3. publicar no CraiV3. O --force sobrescreve só o "Initial commit"
         #    com o README automático do GitHub, que é o único conteúdo lá.
         #    O -u troca o upstream da branch de `antigo/main` para `origin/main`
         #    — sem isso, um `git pull` futuro puxaria do repositório errado.
         git push -u origin main --force

         # 3b. a tag `baseline-pre-sprint` veio junto no fetch e marca o estado
         #     anterior aos sprints. Vale publicar: é evidência de método, e
         #     `git push` não envia tags sozinho.
         git push origin baseline-pre-sprint

         # 4. no repositório ANTIGO, apagar as 7 branches mortas. Todas já estão
         #    mergeadas na main. A única com commit exclusivo é sprint/3-dados, e
         #    esse commit (eb2edba) é um WIP marcado "PARADO pelo gate GA1" —
         #    recomendação: descartar.
         git push antigo --delete demo-interativo
         git push antigo --delete sprint/0-baseline
         git push antigo --delete sprint/1-borda
         git push antigo --delete sprint/2-estado-janela
         git push antigo --delete sprint/3-dados
         git push antigo --delete sprint/a1-auditoria
         git push antigo --delete sprint/churn-involuntario

         # 5. remover o remote do repositório antigo — a migração acabou
         git remote remove antigo

     Depois disso, arquivar no GitHub (Settings → Archive) os repositórios
     `Gavaaaaa/Crai` e `Gavaaaaa/mvp-crai`, com uma linha no topo do README de
     cada um apontando para o CraiV3.

7.5  Entregue o RELATÓRIO CONSOLIDADO da migração inteira: o que veio de cada
     repositório, o que foi apagado, o que foi movido, o que ficou pendente, e
     o que você decidiu por conta própria em qualquer etapa.

FIM. Não faça mais nada depois disso sem eu pedir.
```

---

## Depois que a migração terminar

O repositório estará montado, mas **a suíte de testes ainda não foi rodada com o
patch aplicado** — as dependências novas não estão instaladas. O primeiro passo
da próxima sessão é:

    cd app && pip install -r requirements.txt
    cd app && pytest tests/ -q

Esperado: 1039 passed, 5 skipped. Se o `pip install` falhar em torch, xgboost ou
prophet, isso é um achado importante e precisa ser resolvido antes de qualquer
coisa que dependa de modelo — não na véspera da apresentação.

Do arquivo `PROMPT_SABADO_12SET.md`, depois desta migração **pule as tarefas 1, 2
e a parte 1 da 5**, que são exatamente o que este arquivo já fez. Continuam
valendo: a tarefa 3 (contrato da API), a 4 (percentil intra-tenant — o item do
professor), a parte 2 da 5 (bases de demonstração), a 6 (DECISOES.md) e a 7
(mapeamento LGPD).

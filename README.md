# CRAI — Retention OS

A CRAI é um Retention OS para SaaS B2B brasileiro. Dois pipelines autônomos atacam as
duas formas de perder receita recorrente:

- **churn involuntário** — a cobrança recorrente via Pix Automático falha e o cliente
  sai sem nunca ter decidido sair;
- **churn voluntário** — o cliente se desengaja do produto e cancela.

Em cada caso o sistema diagnostica, decide **se vale agir** pela expectativa de lucro da
intervenção, e executa. Os pipelines são orquestrados em LangGraph, sobre um backend
Python com FastAPI. Projeto de Conclusão de Curso em Engenharia de Software.

## A invariante do produto

**Escalonamento humano é zero.** Nenhum caminho do sistema termina em "falar com um
atendente". Isso não é uma configuração: é garantido em três camadas — uma restrição no
banco (`CHECK (offer_type <> 'consulta_cs')`), o conjunto `CANAIS_HUMANOS` em
`crai/config.py`, que torna a ligação do CS inelegível como canal de envio mesmo quando
ela tem o maior retorno esperado, e testes que reprovam se qualquer uma das duas cair.

O comparativo de canal continua mostrando a ligação do CS, com o retorno calculado e o
motivo do descarte. Mostrar a alternativa descartada é diferente de oferecê-la.

## Rodar

Pré-requisito: Python 3.11.

```bash
cd app
pip install -r requirements.txt
```

Crie `.env` na raiz do repositório com uma linha, `ENV=development`. A lista completa de
variáveis está em [`docs/CONFIGURACAO.md`](docs/CONFIGURACAO.md). **Sem
`ENV=development` ou `ENV=demo`, o painel e os endpoints de simulação respondem 403** —
é a mesma trava para os dois.

```bash
uvicorn crai.api.app:app --reload
```

- **Painel:** http://localhost:8000/painel/v2/ — a barra final importa.
- **Documentação da API:** http://localhost:8000/docs.

Testes, sempre a partir de `app/`:

```bash
pytest tests/ -q
```

Os modelos não vêm no repositório. Para tê-los sem treinar, veja
[Modelos prontos (download)](#modelos-prontos-download), logo abaixo. Treinar é opcional;
sem artefatos, o sistema responde por heurística:

```bash
python -m crai.scripts.preparar_amostra_real
python -m crai.scripts.train_all --fonte sintetico_calibrado
```

Antes do primeiro commit, ative o hook que bloqueia segredos:
`git config core.hooksPath scripts`.

## Modelos prontos (download)

A pasta `app/models/` não é versionada. Num clone limpo o sistema sobe sem modelo nenhum:
**sem os modelos, ele responde por heurística** (regras fixas no lugar do diagnóstico, da
previsão de liquidez e do risco). O pacote abaixo traz os modelos de produção já treinados.

- **Download:** <https://github.com/Gavaaaaa/CraiV3/releases/download/modelos-2026-10/crai-modelos.zip>
- **sha256 esperado do zip:** `6c13b4256f1372d722333005140c726ff9c06bf5db21600e89e78719fdebdbfd`

Em PowerShell, a partir da raiz do repositório:

```powershell
Invoke-WebRequest -Uri "https://github.com/Gavaaaaa/CraiV3/releases/download/modelos-2026-10/crai-modelos.zip" -OutFile crai-modelos.zip
(Get-FileHash crai-modelos.zip -Algorithm SHA256).Hash.ToLower()
```

O valor impresso tem que ser **igual** ao sha256 acima. Só então extraia e confira:

```powershell
Expand-Archive -Path crai-modelos.zip -DestinationPath app\models -Force
cd app
python -m crai.scripts.verificar_modelos
```

`verificar_modelos` compara cada arquivo de `app\models` com o manifesto versionado em
[`docs/modelos/MANIFESTO_MODELOS.json`](docs/modelos/MANIFESTO_MODELOS.json) e diz o que
falta ou está diferente. Tem que terminar com "Tudo certo: os 18 arquivos batem com o
manifesto". Com `--zip ..\crai-modelos.zip` ele confere também o arquivo baixado.

**Baixe só deste endereço e sempre confira o sha256 antes de extrair.** Os arquivos
`.joblib` e `.pkl` executam código ao serem carregados: um pacote adulterado roda o que
quiser na sua máquina.

O pacote tem o classificador de falha, o detector de anomalia, a inferência de liquidez e o
risco voluntário v3 promovido, com os `meta.json`, as curvas de limiar e as métricas de
treino. Não tem o estado do bandit de ofertas (o serviço cria o dele), nem os candidatos e
experimentos. Todos foram treinados em dado sintético; o manifesto diz de qual treino veio
cada um.

## O painel

Cinco abas, servidas como arquivos estáticos a partir de `painel/`, sob a mesma trava de
ambiente das rotas de simulação. **Nada é calculado no navegador**: cada aba chama a API,
e a tela desenha o que voltou.

| Aba | O que é | Rota que ela chama |
|---|---|---|
| **Cobranças** | O gateway de pagamento do cliente. Dispara uma cobrança e ela é recusada. Nenhum diagnóstico acontece aqui. | `POST /simulate/painel/cobranca-falhada` |
| **Recuperação** | O churn involuntário inteiro: causa, chance de recuperação, retorno esperado, a explicação fator a fator, o plano de retentativas e o comparativo de canal. | a mesma resposta |
| **Clientes em risco** | Recebe uma planilha de clientes e devolve o ranking de risco, com a explicação de cada caso. | `POST /simulate/painel/importar` e `GET /simulate/painel/insights` |
| **Mensagens** | As ofertas candidatas por cliente, a escolhida, o canal e o texto. | `POST /simulate/painel/disparo-lote` |
| **Visão geral** | O fechamento. **Todo número vem das outras abas**, e a tela declara a origem de cada um. Sem análise, mostra estado vazio. | nenhuma — lê das anteriores |

Como saber que os modelos estão sendo usados: as barras de explicação da aba Recuperação
são valores SHAP do classificador treinado. Sem os artefatos em `app/models/`, a API
continua respondendo por heurística e **essas barras vêm vazias**.

### Bases de teste para a demonstração

A aba **Clientes em risco** tem três botões que carregam as bases abaixo direto da API.
Para usar por conta própria — anexar na tela, abrir no Excel, mandar para alguém — baixe
daqui:

| Base | O que demonstra | Download |
|---|---|---|
| Uso diário | SaaS de uso intenso, 500 clientes. Mediana de 1 dia sem login. Aqui, 7 dias parado já é sinal de abandono. | [`base_uso_diario.csv`](https://github.com/Gavaaaaa/CraiV3/raw/main/painel/exemplos/base_uso_diario.csv) |
| Uso mensal | SaaS de fechamento mensal, 500 clientes. Mediana de 24 dias sem login. Aqui, 7 dias parado é rotina. | [`base_uso_mensal.csv`](https://github.com/Gavaaaaa/CraiV3/raw/main/painel/exemplos/base_uso_mensal.csv) |
| Base saudável | 500 clientes, ninguém em risco. Prova que o sistema não grita à toa. | [`base_saudavel.csv`](https://github.com/Gavaaaaa/CraiV3/raw/main/painel/exemplos/base_saudavel.csv) |
| Colunas próprias | 500 clientes com nomes de coluna diferentes dos esperados, para exercitar o mapeamento na importação. | [`base_exemplo_clientes.csv`](https://github.com/Gavaaaaa/CraiV3/raw/main/painel/exemplos/base_exemplo_clientes.csv) |

**As duas primeiras são o ponto da demonstração, e é preciso usar as duas.** Elas contêm
os mesmos quatro clientes-âncora — `ANCORA-07-A`, `ANCORA-07-B`, `ANCORA-20-A`,
`ANCORA-20-B` — com exatamente os mesmos números: mesmo MRR, mesmo perfil de pagador,
mesma quantidade de funcionalidades usadas, mesmos dias sem login.

Importe uma, procure a âncora, importe a outra e procure de novo. **A mesma cliente sai
crítica numa base e normal na outra.** O limiar não está no código; sai da distribuição da
base. É o que a seção [A régua por percentil](#a-régua-por-percentil) explica.

Cada uma das três primeiras tem 12 linhas propositalmente sem dado de atividade, que
voltam como `dado_insuficiente` — nunca como risco zero.

As bases são geradas com semente fixa por `python -m crai.scripts.gerar_bases_demo`
(a partir de `app/`): rodar de novo produz arquivos idênticos. São UTF-8 com BOM,
separador `;` e decimal com vírgula, então abrem no Excel com dois cliques. O detalhamento
está em [`painel/exemplos/README.md`](painel/exemplos/README.md).

Com o servidor no ar, elas também ficam em
`http://localhost:8000/painel/v2/exemplos/<arquivo>.csv`.

O painel é bilíngue (português e inglês). Todo texto visível passa pelo dicionário em
`painel/idioma.js`; nenhuma frase é escrita direto na marcação.

## Dashboard novo (em desenvolvimento)

> **Provisório.** Esta seção descreve o dashboard que está sendo integrado, em `dashboard/`.
> Ele ainda não substitui o painel acima. Quase todas as páginas já falam com o backend (a
> tabela abaixo diz quais). O login é de desenvolvimento: uma empresa fictícia, sem senha,
> que só existe com `ENV=development`.

Pré-requisitos: Python 3.11 (o backend) e Node 24 com npm 11 (testado com Node 24.16).

**1. O backend, em modo de desenvolvimento.** Os bancos da demonstração ficam numa pasta
própria, para os ciclos fictícios não se misturarem com os dados de `app/data/`:

```powershell
cd app
$dados = (Resolve-Path ..).Path + "\docs\interno\dados_dashboard_demo"
New-Item -ItemType Directory -Force $dados | Out-Null
$env:ENV = "development"
$env:PYTHONIOENCODING = "utf-8"
$env:CRAI_RECOVERY_DB = "$dados\recovery_cycles.db"
$env:CRAI_RETENTION_DB = "$dados\retention_cycles.db"
$env:CRAI_RETRY_STATE = "$dados\pix_retry_state.json"
$env:CRAI_CLIENTES_DB = "$dados\clientes.db"
python -m uvicorn crai.api.app:app --port 8000
```

**2. A semente (ciclos fictícios). Opcional.** Sem ela o dashboard abre vazio, e a página
**Simulação do gateway** já deixa criar um cliente fictício e uma cobrança pela própria
tela. Para ter ciclos na lista do Involuntário, em outra janela, com o backend no ar, crie
uma cobrança Pix que falhou, da empresa fictícia `demo_dashboard`:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/simulate/pix-falhado `
  -ContentType "application/json" `
  -Body '{"id_recorrencia": "RN_demo_001", "valor": 1290.0, "codigo_falha": "AM04", "tenant_id": "demo_dashboard"}'
```

Troque `AM04` (saldo insuficiente: o sistema agenda novas tentativas) por `MD01`
(autorização revogada: vai direto às 3 mensagens) para ver um ciclo aguardando escolha, e
use `/simulate/pix-pago` com o mesmo `id_recorrencia` para ver um ciclo recuperado. Quem
desenvolve o projeto tem um script que cria um ciclo em cada situação de uma vez, uma base
de clientes e dois eventos de comportamento (`docs/interno/semear_dashboard_demo.py`); a
pasta `docs/interno/` não é versionada.

**3. O dashboard.** Em outra janela:

```powershell
cd dashboard
Set-Content -Path .env.local -Value "VITE_CRAI_API_URL=http://127.0.0.1:8000" -Encoding ascii
npm install
npm run dev
```

Abra <http://localhost:5173/involuntario>. A porta é a 5173, e o endereço tem que ser
`localhost` (não `127.0.0.1`): é a única origem que o backend libera em desenvolvimento.

O endereço do backend vem de `VITE_CRAI_API_URL`, em `dashboard/.env.local` (o git ignora o
arquivo; não existe `.env.example`). **Sem essa variável o dashboard não chama o backend** e
mostra só dados de demonstração.

**O login de desenvolvimento.** Ao abrir, o dashboard pede `POST /dev/token` e recebe um
token da empresa fictícia com o papel de dono. O token fica só na memória da página. No
topo, o seletor "Papel" troca para administrador ou membro, para ver o que cada papel pode
fazer. Fora de `ENV=development` essa rota não existe e o token é recusado.

**O que já é real e o que ainda é demonstração:**

| Página | Situação |
|---|---|
| Visão geral (os cartões, o gráfico de 30 dias, o caminho das cobranças, o que funciona, o extrato, a atividade recente e a saúde do sistema) | Real. O cartão "Comparação com grupo de controle" é só um aviso de recurso planejado, sem número |
| Involuntário (cartões, lista, painel do ciclo, escolher e pedir outras mensagens) | Real |
| Voluntário (a base, os clientes recentes, o valor mantido, a comparação entre régua e modelo, o anexo da planilha) | Real |
| Simulação do gateway | Real: o cliente, a cobrança e o relógio são fictícios, e o sistema que age sobre eles é o de verdade (diagnóstico, tentativas, mensagens, retenção). Os dados simulados ficam em arquivos separados dos reais e saem marcados com "Demonstração" |
| Assistente | Real. Responde com os números agregados da empresa; sem a chave do modelo de linguagem configurada no backend, mostra um texto fixo de ajuda com os links das páginas |
| API (listar, gerar e revogar as chaves de API da empresa; os exemplos de uso) | Real |
| Configuração, seção Mensagens (modo, prazo, janela de contato, canais) | Real |
| Configuração, Dados e privacidade (exportar e anonimizar os dados de um cliente, explicar uma decisão, não contatar, o texto para a política de privacidade, os prazos) | Real. Dos prazos, o expurgo diário executa o das mensagens (90 dias), o do registro de acesso (12 meses), o da trilha de decisões (5 anos) e o dos ciclos (24 meses: os identificadores saem, os valores ficam); o da base depois do contrato (6 meses) está definido e ainda não é executado |
| Configuração: Empresa, Equipe, Integração (webhook e teste), Notificações | Demonstração |
| O nome da empresa no topo | Fixo ("Empresa de demonstração"): o login ainda é o de desenvolvimento |
| **A tela não marca os blocos fictícios** | Esta tabela é o registro do que é real. A etiqueta "Demonstração" só aparece na Simulação do gateway e nos dados simulados que ela cria. Para religar as etiquetas nos outros blocos, ver abaixo |

**A tela se atualiza sozinha.** Com o backend ligado, a Visão geral, o Involuntário e o
Voluntário consultam o backend de novo a cada 60 segundos, e o painel de um ciclo aberto a
cada 5. É consulta periódica, não tempo real: uma mudança pode levar esse tempo para
aparecer. A consulta para com a aba do navegador escondida.

**A barra "Mostrar".** Nas páginas com números, "Dados reais" mostra só o que é da empresa;
"Simulação" junta o que foi criado na Simulação do gateway, marcado como demonstração.

**As etiquetas "Demonstração".** A tela não marca mais os blocos que usam dado fictício: o
que é real e o que não é está na tabela acima. O mecanismo continua no código, desligado.
Para religar, acrescente uma linha ao `dashboard/.env.local` e suba o `npm run dev` de novo:

```powershell
Add-Content -Path .env.local -Value "VITE_CRAI_MOSTRAR_DEMONSTRACAO=1" -Encoding ascii
```

Com ela, todo bloco que ainda usa dado fictício volta a mostrar a etiqueta (só com o backend
ligado, como antes). Sem ela, ou com qualquer outro valor, as etiquetas não aparecem. A
Simulação do gateway não depende dessa variável: o selo "Demo" do menu, o selo da página e
as marcas dos dados simulados aparecem sempre.

O envio das mensagens e o gateway de pagamento são simulados em qualquer caso: nenhum
WhatsApp ou e-mail sai de verdade, e nenhuma cobrança real é feita.

Testes do dashboard, a partir de `dashboard/`: `npm run build`, `npm test` e, com o backend
no ar, `npm run test:vivo`. O teste ao vivo usa uma segunda empresa fictícia, `demo_testes`,
para não deixar chaves nem escolhas na empresa da demonstração; ela precisa ter ciclos
(o script de semente aceita `--empresa demo_testes`).

## Os dois pipelines

### 1. Churn involuntário — a cobrança falhou

```
Webhook de Pix Automático (assinado, com janela anti-replay)
        ↓
    diagnose            XGBoost + Random Forest: causa raiz, chance de
        ↓               recuperação, e-Profit e explicação SHAP
    check_anomaly       autoencoder: este cliente está se comportando
        ↓               fora do padrão dele mesmo?
    infer_payday        LSTM + Prophet: em que dia ele provavelmente
        ↓               vai ter saldo em conta
    decide_recovery     retentar, mandar mensagem, ou encerrar
        ↓
  schedule_retry_pix  ──ou──  trigger_dunning
        ↓                          ↓
        └──────────────────────────┘
                   ↓
            update_dashboard
```

O roteamento é condicional em três pontos: depois do diagnóstico, depois da decisão e
depois do agendamento. Uma causa que retentar não resolve — autorização revogada, dados
bancários inválidos — pula direto para a mensagem, porque insistir numa cobrança que o
banco vai recusar de novo só queima tentativa e dinheiro.

**O webhook do Stripe não entra neste grafo.** Desde a Fase 3 a recobrança automática de
cartão está fora do fluxo ativo: o evento é registrado com prefixo `[CARTAO-DESATIVADO]`
e a resposta traz `pipeline: false`. A política de cartão está preservada em
`crai/dunning/legacy_card/` e não é importada por nada no caminho ativo — aplicar backoff
exponencial a uma cobrança Pix violaria o limite do BACEN.

### 2. Churn voluntário — o cliente está se desengajando

```
Evento de comportamento (SDK) ou planilha de clientes
        ↓
    assess_risk         score de risco e criticidade
        ↓
    choose_offer        Multi-Armed Bandit: qual oferta para este perfil,
        ↓               nesta empresa
    choose_channel      por onde falar, com memória do que já converteu
        ↓               com este cliente
    generate_message    Claude API, ou modelo pronto sem chave configurada
        ↓
    send_offer
        ↓
   [track_outcome]      só em simulação; em produção o desfecho chega
        ↓               por webhook, depois
    update_crm
```

## O gateway e a janela do BACEN

O único meio de pagamento ativo é o **Pix Automático**. O Banco Central limita a
recobrança a **3 tentativas dentro de 7 dias corridos** contados do vencimento — as
constantes vivem em `crai/dunning/pix_automatico_retry.py` e são a mesma fonte para o
cálculo do prazo e para o limite, sem cópia.

Dentro dessa janela, as tentativas não são distribuídas uniformemente: o Módulo de
liquidez prevê os dias em que aquele perfil de pagador provavelmente terá dinheiro em
conta, e as tentativas vão para esses dias. Quando não há previsão utilizável, o fallback
distribui pelos dias restantes.

Esgotada a janela, não existe quarta tentativa. O sistema para de cobrar e passa a falar
com o cliente.

## A decisão: e-Profit

Nenhum dos dois pipelines pergunta "é possível recuperar?". Os dois perguntam **"vale a
pena?"**:

```
e-Profit = chance de sucesso × valor recuperável − custo esperado da intervenção
```

O caso segue quando o e-Profit é positivo e o score passa do mínimo. Quando não passa, o
sistema encerra sozinho — e essa é uma decisão de produto, não uma falha: insistir num
caso que custa mais do que traz de volta é queimar o dinheiro do cliente e a paciência do
devedor.

O mesmo critério escolhe o canal: cada canal tem um custo e uma chance de resposta, e o
comparativo aparece na tela com o motivo de cada descarte.

## As ofertas e o canal

A escolha da oferta é um **Multi-Armed Bandit com Thompson Sampling**. Para cada
combinação de empresa cliente, perfil de pagador e oferta, o sistema mantém uma
distribuição Beta e sorteia dela a cada decisão — é assim que ele equilibra usar o que já
aprendeu com testar o que ainda não sabe.

As quatro ofertas são `desconto_10`, `desconto_20`, `pausa_1_mes` e `pix_boleto_flash`.
As posteriors começam em prioris de mercado e se movem a cada desfecho real que entra por
`POST /webhooks/retention-outcome`. O estado aprendido fica em
`app/models/bandit_state.json`, separado por empresa: o que um cliente aprende não vaza
para outro.

Os canais considerados são bot de WhatsApp, e-mail automático, SMS, link de Pix ou boleto
e ligação do CS — este último sempre descartado, pela invariante. A escolha leva em conta
telefone utilizável, criticidade do caso e o histórico de conversão daquele cliente. O
envio real depende de integração: nesta fase apenas o bot de WhatsApp tem, e a tela diz
isso em vez de esconder.

## A régua por percentil

O risco de churn voluntário não usa um limiar fixo para todo mundo. Sete dias sem login
num SaaS de uso diário é abandono; num sistema de fechamento mensal é terça-feira.

Então a régua sai da **distribuição da própria base do cliente** — percentis de dias sem
uso e de funcionalidades usadas nos últimos 30 dias, calculados por empresa. O mesmo
cliente, com exatamente os mesmos números, sai crítico numa base e normal em outra.

Quando a base é pequena demais para sustentar percentis, o sistema cai para a régua
padrão **e avisa na tela**, em vez de fingir que mediu.

Cliente sem dado de atividade não recebe risco zero: recebe `dado_insuficiente`, aparece
separado no fim da lista e nunca é somado nem ordenado junto com quem tem risco baixo de
verdade. Ausência de dado não é ausência de risco.

## Os modelos

| Módulo | Algoritmo | O que responde |
|---|---|---|
| Classificador de falha | XGBoost + Random Forest, com SHAP | causa raiz, chance de recuperação e a explicação fator a fator |
| Detector de anomalia | Autoencoder (PyTorch) | este cliente fugiu do padrão dele mesmo |
| Inferência de liquidez | LSTM + Prophet por perfil | em que dia tentar de novo |
| Risco voluntário | HistGradientBoosting (v3), com TreeSHAP | **promovido**: o risco de cancelamento de cada cliente, e os fatores que mais pesaram |

O modelo de risco voluntário em produção é o **v3**, promovido em 03/10/2026. O candidato
anterior (v2) não foi promovido porque aprendia a imitar a régua: era treinado contra o
rótulo que as próprias regras produzem. O v3 foi treinado numa base em que o cancelamento
não vem da régua, e por isso pôde ser medido contra ela. O que foi medido:

- no holdout por cliente da base v3 e na validação cruzada por grupos, ele ordena o risco
  melhor que as duas réguas (a fixa e a por percentil);
- a vantagem **depende das colunas de comportamento** (acessos, sessões, chamados, NPS,
  pagamentos falhados): só com as colunas que a régua também lê, ela continua existindo,
  mas é bem menor;
- o modelo decide quando há dias sem acesso ou uso; sem os dois, a régua continua
  decidindo, e a trilha registra quem decidiu;
- o risco vira ação pela **posição na base da própria empresa** (os de maior risco, e só
  com sinal real de abandono), não por um corte fixo de probabilidade.

**A ressalva que vale mais que o resto:** a base v3 é sintética, e o cancelamento dela foi
desenhado pelo próprio projeto. A vantagem é medida contra esse gerador; **não é churn
observado e não diz nada sobre churn real**. Os números estão em
`docs/evidencia_v3/metricas_v3.json`, e a leitura deles em
[`docs/LIMITACOES.md`](docs/LIMITACOES.md), na seção do risco voluntário v3.

A promoção é um comando explícito e reversível, rodado de dentro de `app/`:
`python -m crai.scripts.promover_voluntario_v3 --promover` (e `--reverter` para voltar à
régua). `app/crai/churn_voluntary/README_treino.md` explica como regenerar o artefato do
zero.

Os artefatos ficam em `app/models/` e **não são versionados**: baixe o pacote pronto em
[Modelos prontos (download)](#modelos-prontos-download). Sem eles o sistema usa
fallbacks — e a ausência aparece na tela, não em silêncio.

## As rotas

Todas as rotas de hoje. "Token" é o token de login da empresa (o tenant sai do token, nunca
da URL nem do corpo); "chave" é a chave de API da empresa (`crai_live_...`).

| Grupo | Rotas | Quem chama |
|---|---|---|
| Webhooks | `POST /webhooks/pix-automatico`, `/webhooks/stripe`, `/webhooks/segment`, `/webhooks/retention-outcome` | O PSP e o Segment, com assinatura HMAC |
| Eventos de comportamento | `POST /eventos` | O servidor da empresa, com a chave ou o token |
| API de clientes | `POST /clientes`, `POST /clientes/lote`, `PATCH /clientes/{id}`, `DELETE /clientes/{id}` | O servidor da empresa, com a chave ou o token |
| Base de clientes no painel | `POST /clientes/importar` (dono ou administrador), `GET /clientes/base`, `GET /clientes/recentes`, `POST` e `DELETE /clientes/{id}/nao-contatar` (dono ou administrador), `GET /insights`, `POST /insights/enviar` | Token |
| Involuntário | `GET /ciclos`, `GET /ciclos/{id}`, `POST /ciclos/{id}/mensagens/escolher` e `/regerar` (dono ou administrador), `GET /metrics/involuntario/mes`, `/serie` e `/funil`, `GET /metrics/recovery` | Token |
| Voluntário | `GET /metrics/voluntario/mes`, `/serie` e `/regua-x-modelo` | Token (plano premium) |
| Visão geral | `GET /metrics/visao-geral`, `GET /metrics/serie`, `GET /metrics/o-que-funciona`, `GET /atividade`, `GET /extrato` e `GET /extrato/csv` (dono ou administrador; são as únicas com a taxa da CRAI; para empresa em período de piloto, a taxa é zero e a coluna "Taxa fora do piloto" mostra o que seria cobrado), `GET /busca` | Token |
| Configuração e chaves | `GET` e `PUT /configuracao`, `GET` e `POST /integracao/chaves`, `DELETE /integracao/chaves/{id}` | Token (gravar: dono ou administrador) |
| Direitos do titular (LGPD) | `GET /titular/explicacao/{id}`, `POST /titular/exportar`, `POST /titular/anonimizar`, `GET /titular/texto-para-politica` | Token (exportar e anonimizar: dono ou administrador) |
| Simulação do gateway | `GET` e `DELETE /simulacao`, `POST /simulacao/cliente`, `/cobrar`, `/avancar` e `/retencao` | Token (escrever: dono ou administrador) |
| Assistente | `POST /assistente` | Token |
| Operação | `GET /health` | Pública (com token, diz também a base da empresa) |
| Só em desenvolvimento | `POST /dev/token` (só com `ENV=development`); `POST /simulate/pix-falhado`, `/simulate/pix-pago`, `/simulate/pix-estornado`, `/simulate/resposta-sair`, `/simulate/payment-failed`, `/simulate/churn-risk` | Quem desenvolve |
| Painel antigo | `/simulate/painel/` + `ambiente`, `cobranca-falhada`, `evento-risco`, `disparo-lote`, `importar`, `insights` | A página `/painel/v2` |

Tudo em `/simulate/*` e o painel antigo exigem `ENV=development` ou `ENV=demo`. As rotas do
dashboard exigem o token de login, e cada uma lê e grava só o que é da empresa do token: o
identificador de outra empresa responde 404, igual ao que não existe. A chave de API vale
só nas cinco rotas marcadas acima (`POST /eventos` e as quatro da API de clientes); nas
outras ela é recusada com 401. Há testes que percorrem todas as rotas do aplicativo para
conferir as duas coisas.

Os webhooks verificam HMAC e têm janela anti-replay. A borda valida a forma dos campos que
o pipeline consome — o escopo exato dessa validação está declarado em `app/README.md`.

## Pastas

| Pasta | O que tem |
|---|---|
| `app/` | O sistema: pacote `crai/` (API, agentes, modelos, integrações), `tests/`, `docs/DATA_CARD.md` e as evidências de treino. |
| `painel/` | O dashboard servido em `/painel/v2`: `index.html`, `estilo.css`, `idioma.js`, `api.js`, `render.js` e `img/`. Mais `exemplos/` com bases de clientes em CSV e `fixtures/` com uma resposta de exemplo por endpoint. |
| `docs/` | Limitações, configuração, estrutura, contrato do painel, registro da migração, planos e histórico. |
| `scripts/` | O hook de pre-commit. |

O mapa arquivo a arquivo está em [`docs/ESTRUTURA.md`](docs/ESTRUTURA.md). O contrato de
resposta de cada endpoint está em [`docs/CONTRATO_PAINEL.md`](docs/CONTRATO_PAINEL.md). Se
procura algo que existia no repositório antigo, veja
[`docs/MIGRACAO_FEITA.md`](docs/MIGRACAO_FEITA.md).

## Dados e treino

O trabalho de base de dados e treino vive em
[`Gavaaaaa/Base-de-dados`](https://github.com/Gavaaaaa/Base-de-dados). Lá estão as rodadas
de treino, a checagem fora do domínio, os parâmetros medidos nas fontes reais e os modelos
treinados.

Os modelos são treinados em **dados sintéticos calibrados**. As fontes públicas usadas —
Olist, uma série do BACEN e um dataset de churn de e-commerce — **doaram parâmetros para
features específicas**, não linhas de treino. `app/models/calibracao.json` classifica cada
feature em ancorada em fonte real, proxy fraco ou sintética sem doador, uma a uma.

Este README não cita métricas, de propósito. Os números vivem em três lugares:

- **medições:** `app/docs/evidencia/treino/`;
- **fontes e licenças dos dados:** `app/docs/DATA_CARD.md`;
- **leitura honesta dos números, com o que ainda não está provado:** `docs/LIMITACOES.md`.

Comece pelo último se a pergunta for "isso funciona mesmo?".

## Licença

Projeto acadêmico (TCC), para uso educacional. A fonte Olist, usada na calibração, é
CC BY-NC-SA 4.0: não pode ser usada comercialmente.

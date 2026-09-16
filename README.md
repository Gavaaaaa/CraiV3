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

Treinar os modelos é opcional; sem artefatos, o sistema responde por heurística:

```bash
python -m crai.scripts.preparar_amostra_real
python -m crai.scripts.train_all --fonte sintetico_calibrado
```

Antes do primeiro commit, ative o hook que bloqueia segredos:
`git config core.hooksPath scripts`.

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
| Risco voluntário | Gradient Boosting | **candidato, não promovido** |

O modelo de risco voluntário foi treinado e **não** foi ativado, por decisão medida: como
não existe rótulo real de cancelamento, ele foi treinado contra o rótulo que as próprias
regras produzem, e aprende a imitar a régua em vez de superá-la. Promovê-lo trocaria uma
conta explicável por uma caixa-preta equivalente. A promoção é explícita
(`--ativar-voluntario`) e só faz sentido quando houver desfecho real acumulado.

Os artefatos ficam em `app/models/` e **não são versionados**. Sem eles o sistema usa
fallbacks — e a ausência aparece na tela, não em silêncio.

## As rotas

| Grupo | Rotas |
|---|---|
| Webhooks | `/webhooks/pix-automatico`, `/webhooks/stripe`, `/webhooks/segment`, `/webhooks/retention-outcome` |
| Self-service (com JWT) | `POST /clientes/importar`, `GET /insights`, `POST /insights/enviar` |
| Operação | `GET /metrics/recovery`, `GET /health` |
| Simulação | `/simulate/pix-falhado`, `/simulate/pix-pago`, `/simulate/payment-failed`, `/simulate/churn-risk` |
| Painel | `/simulate/painel/` + `ambiente`, `cobranca-falhada`, `evento-risco`, `disparo-lote`, `importar`, `insights` |

Tudo em `/simulate/*` e o painel exigem `ENV=development` ou `ENV=demo`. Apenas
`/clientes/importar` e `/insights` exigem o JWT do Supabase, e existe um teste que garante
que continuem sendo só essas duas.

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

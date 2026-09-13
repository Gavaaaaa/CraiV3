# painel/

Aqui vai o dashboard da CRAI, quando a stack estiver decidida. Por enquanto a
pasta guarda o que o dashboard vai consumir, independente de stack:

- **`fixtures/`** — uma resposta de exemplo por endpoint, no formato exato do
  contrato em `docs/CONTRATO_PAINEL.md`. Servem para construir e testar a tela
  sem backend e sem Supabase.
- **`exemplos/`** — bases de clientes em CSV para o `POST /clientes/importar`
  (ver `exemplos/README.md`).

## As fixtures

| Arquivo | Endpoint | O que mostra |
|---|---|---|
| `importar.json` | `POST /clientes/importar` | relatório real da importação da base de 500 clientes, com 3 linhas rejeitadas (número da linha da planilha e motivo), a coluna `email` ausente e 12 linhas sem dado comportamental |
| `insights.json` | `GET /insights` | o ranking com os casos que a tela precisa saber desenhar: **crítico por risco** (`cli-0438`, risco 1,0), **crítico por valor de MRR** (`cli-0126`, MRR R$ 2.450 e risco sem sinal absoluto), **padrão no topo da fila** com a frase "sem sinal de abandono" (`cli-0003`), padrão com uma coluna ausente e explicação avisando (`cli-0007`), **`dado_insuficiente` com `risk_score: null`** (`cli-0216`) e uma linha vinda do SDK com `evento` (`cli-0090`). Todas as linhas do upload trazem `origem_da_regua: "base_do_tenant"` |
| `modelos_status.json` | `GET /modelos/status` | os quatro modelos, três ativos e o de risco voluntário com `origem_da_decisao: "regras"` |
| `modelos_retreinar.json` | `POST /modelos/retreinar` | um retreino que **não** promoveu o candidato, com as duas AUCs e o motivo |
| `resultado.json` | `GET /resultado` | o fechamento de um mês com `simulado: true`, o `aviso` para a tela e a memória de cálculo |
| `evento_risco.json` | `POST /simulate/painel/evento-risco` | um evento crítico **com telefone**: canal `whatsapp` escolhido, `canais_considerados` com o motivo de cada descarte (pop-up preterido pela criticidade, e-mail como reserva) e as **três `candidatas`** do bandit com `p_sucesso`, uma `escolhida`, todas `origem_texto: "template"` porque a fixture foi gerada com a Claude API desligada |
| `disparo_lote.json` | `POST /simulate/painel/disparo-lote` | um lote de 6 linhas com os casos que a tela precisa desenhar: crítico por risco com telefone (`whatsapp`), **crítico por valor** (`cli-0126`, MRR R$ 2.450), alto no site (`popup`), e os três tipos de pulo — `abaixo_do_criterio` (`cli-0003`), `dado_insuficiente` (`cli-0216`) e `cadastro_invalido` (`cli-0999`, MRR "abc"). `simulado: true` com `aviso` |

Os números e as frases de `importar.json`, `insights.json`,
`evento_risco.json` e `disparo_lote.json` foram produzidos pelo motor real
(`importacao.importar`, `batch_scoring.pontuar_cliente`, as rotas
`/simulate/painel/*` com bancos isolados), não digitados. `modelos_status`,
`modelos_retreinar` e `resultado` são a especificação de endpoints que ainda
não existem; os valores são plausíveis, tirados das rodadas de treino
documentadas.

## A regra da variável única

O consumidor escolhe a fonte de dados por **uma** variável de ambiente, e só
por ela. Sugestão de nome: `CRAI_API_BASE`.

- vazia ou ausente → lê `painel/fixtures/<endpoint>.json`;
- definida (ex.: `http://localhost:8000`) → chama a API real com o header
  `Authorization: Bearer <token do Supabase>`.

Nada mais muda entre os dois modos: mesmo caminho de código, mesmo parser,
mesma tela. Se uma tela funciona com a fixture e quebra com a API, a diferença
é o contrato, e o contrato é que se corrige.

Invariantes que a tela precisa respeitar, e que as fixtures exercitam:
`risk_score: null` não é zero (não ordene, não some, não desenhe barra);
`simulado: true` em `/resultado` e em `/simulate/painel/disparo-lote` obriga a
exibir o `aviso`; em `candidatas`, `origem_texto: "template"` é mostrado como
template, nunca como "gerado"; e nenhuma candidata nem canal escolhido é
humano (a tela não precisa de botão "falar com atendente", porque ele não
existe no produto).

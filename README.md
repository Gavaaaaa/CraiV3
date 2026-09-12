# CRAI — Retention OS

A CRAI é um Retention OS para SaaS B2B brasileiro. Dois pipelines autônomos
atacam as duas formas de perder receita:

- **churn involuntário**: a cobrança recorrente via Pix Automático falha;
- **churn voluntário**: o cliente se desengaja do produto.

Em cada caso o sistema diagnostica, decide se vale agir pela expectativa de
lucro da intervenção (e-Profit) e executa. Os pipelines são orquestrados em
LangGraph, sobre um backend Python com FastAPI. Projeto de Conclusão de Curso
(TCC) em Engenharia de Software.

## Rodar

Pré-requisito: Python 3.11.

```bash
cd app
pip install -r requirements.txt
```

Crie `app/.env` com uma linha, `ENV=development`. A lista completa de variáveis
está em [`docs/CONFIGURACAO.md`](docs/CONFIGURACAO.md). Sem `ENV=development`, os
endpoints de simulação respondem 403.

```bash
uvicorn crai.api.app:app --reload
```

- **Painel de avaliação:** http://localhost:8000/painel. Cada ação chama a API
  real. Em desenvolvimento, a base de clientes vai para um SQLite local.
- **Documentação da API:** http://localhost:8000/docs.

Testes, sempre a partir de `app/`:

```bash
pytest tests/ -q
```

Treinar os modelos é opcional; sem artefatos, o sistema usa fallbacks:

```bash
python -m crai.scripts.preparar_amostra_real
python -m crai.scripts.train_all --fonte sintetico_calibrado
```

O processo e as medições estão em [`app/README_treino.md`](app/README_treino.md).
O que o sistema ainda não prova está em [`docs/LIMITACOES.md`](docs/LIMITACOES.md).

Antes do primeiro commit, ative o hook que bloqueia segredos:
`git config core.hooksPath scripts`.

## Pastas

| Pasta | O que tem |
|---|---|
| `app/` | O sistema: pacote `crai/` (API, agentes, modelos, integrações), `tests/`, `docs/DATA_CARD.md` e as evidências de treino. |
| `painel/` | Reservada para o dashboard, ainda não construído. Hoje só tem uma base de exemplo de 500 clientes. |
| `docs/` | Limitações, configuração, estrutura, registro da migração, planos e histórico. |
| `scripts/` | O hook de pre-commit. |

O mapa arquivo a arquivo está em [`docs/ESTRUTURA.md`](docs/ESTRUTURA.md). Se
procura algo que existia no repositório antigo, veja
[`docs/MIGRACAO_FEITA.md`](docs/MIGRACAO_FEITA.md).

## Dados e treino

O trabalho de base de dados e treino vive em
[`Gavaaaaa/Base-de-dados`](https://github.com/Gavaaaaa/Base-de-dados). Lá estão
as rodadas de treino, a checagem fora do domínio, os parâmetros medidos nas
fontes reais e os modelos treinados.

Este README não cita métricas. Os números vivem em três lugares:

- **medições:** `app/docs/evidencia/treino/`;
- **fontes e licenças dos dados:** `app/docs/DATA_CARD.md`;
- **leitura honesta dos números:** `docs/LIMITACOES.md`.

## Licença

Projeto acadêmico (TCC), para uso educacional. A fonte Olist, usada na
calibração, é CC BY-NC-SA 4.0: não pode ser usada comercialmente.

# Estrutura do repositório

O que vive em cada pasta do `CraiV3` e o que vive no repositório de dados
(`Gavaaaaa/Base-de-dados`). Tudo que é Python roda a partir de `app/`.

## `app/` — o sistema (backend Python, FastAPI + LangGraph)

| Caminho | O que é |
|---|---|
| `crai/api/` | A API FastAPI (`app.py`): webhooks, endpoints do self-service, `/simulate/*` e o painel de avaliação em `/painel` (`painel.py`). |
| `crai/agent/` | O grafo do churn involuntário: `workflow.py`, `main_agent.py`, `state.py`, a tradução de códigos do PSP (`pix_codes.py`) e o provedor de perfil do cliente (`perfil_provider.py`). |
| `crai/dunning/` | Cobrança e retentativa: `pix_automatico_retry.py` (regras do BACEN), `dunning_engine.py` (mensagens), `recovery_log.py`, `retry_state.py`. |
| `crai/dunning/legacy_card/` | Código de cartão **desativado**, mantido de propósito: `tests/test_payment_isolation.py` prova que o pipeline ativo não o importa. Não apagar. |
| `crai/churn_voluntary/` | O churn voluntário: `risk_scorer.py` (regras), `batch_scoring.py`, `importacao.py` e `clientes_importados.py` (base da empresa), `insights_unificados.py`, `offer_bandit.py`, `voluntary_agent.py`, `retention_log.py`. |
| `crai/ml/` | Os modelos: `failure_classifier.py`, `anomaly_detector.py`, `payday_inference.py`, `voluntary_risk.py` (candidato, não ativo), `synthetic_data.py`, `calibracao.py`. |
| `crai/integrations/` | Gateways e canais: `payment_gateway.py`, `pagarme_gateway.py`, `whatsapp_sender.py`, `hubspot_crm.py`, `email_sender.py`. Todos simulados por default. |
| `crai/accounts/` | Validação do JWT emitido pelo Supabase Auth (`supabase_auth.py`; contrato em `README.md`). |
| `crai/security/` | Criptografia de campos sensíveis (`tokenization.py`). |
| `crai/scripts/` | Linha de comando: `train_all.py`, `preparar_amostra_real.py`, `sanity_check_fora_do_dominio.py`, `gerar_readme_treino.py`, `relatorio.py`. |
| `crai/config.py` | Parâmetros de negócio lidos do ambiente, com default. |
| `tests/` | A suíte inteira (`pytest tests/ -q`). |
| `docs/DATA_CARD.md` | As fontes de dado real, as licenças e o modelo causal dos rótulos sintéticos. |
| `docs/evidencia/` | Evidência dos sprints (8 arquivos) e, em `treino/`, as medições da última rodada de treino (JSONs, logs, `PROVENIENCIA.json`). |
| `models/` | Artefatos treinados. **Ignorado pelo git**, exceto `calibracao.json`; o resto se regenera com `train_all`. |
| `data/`, `logs/` | Estado de execução e dados brutos. Gerados localmente, ignorados pelo git. |
| `README.md` | Documentação técnica do backend. Tem dono. |
| `README_treino.md` | O relatório de treino, gerado a partir dos JSONs de `docs/evidencia/treino/`. |
| `requirements.txt`, `requirements-lock.txt` | **`requirements.txt` é a fonte da verdade**: fixa as versões gravadas nos `meta.json` de todos os artefatos publicados (numpy 1.26.4, sklearn 1.5.2, xgboost 2.1.1, torch 2.13.0+cpu, prophet 1.4.0). O lock é um `pip freeze` de outro ambiente (Python 3.12; numpy 2.4.6, shap 0.52.0) que nenhum artefato usou — mantido como registro, com cabeçalho dizendo isso; não instalar por ele. Versão fixada garante que o artefato recarrega igual e que classificador, liquidez e voluntário treinam igual (medido em três máquinas, quarta casa); **não** reproduz o autoencoder, que varia entre máquinas com as mesmas versões (`docs/LIMITACOES.md`). |
| `test_pipeline.py` | Roda cenários dos dois pipelines de ponta a ponta, sem serviços externos. |

## `painel/` — o dashboard (ainda não construído)

Reservado para o dashboard novo; a stack ainda está em decisão. Hoje só tem
`exemplos/base_exemplo_clientes.csv`, uma base sintética de 500 clientes vinda
da beta descartada (`mvp-crai`).

## `docs/` — documentação do projeto

| Caminho | O que é |
|---|---|
| `LIMITACOES.md` | O que é real, o que é simulado e o que o sistema ainda não prova. |
| `CONFIGURACAO.md` | As variáveis de ambiente, para montar o `.env` local do zero. |
| `ESTRUTURA.md` | Este arquivo. |
| `MIGRACAO_FEITA.md` | O que a migração para o CraiV3 trouxe, apagou e moveu. |
| `MIGRACAO_CRAIV3.md` | O roteiro que conduziu essa migração. |
| `planos/` | Os planos de trabalho: sprints, os dois pipelines, onboarding e o prompt de auditoria. |
| `historico/` | Material encerrado: auditorias, achados, aprovação do Sprint 3, baseline do pytest e a demo antiga. Ver `historico/README.md`. |

## `scripts/`

`pre-commit` — hook que bloqueia `.env*` e padrões de chave real nos arquivos
estagiados. Ativar uma vez, da raiz: `git config core.hooksPath scripts`.

## O que vive no `Base-de-dados` (e não aqui)

[`Gavaaaaa/Base-de-dados`](https://github.com/Gavaaaaa/Base-de-dados) é o
repositório de dados e de treino. Continua vivo. Guarda:

- `evidencia/` — as rodadas de treino, a checagem fora do domínio, os logs e os
  resultados do pytest antes e depois. As medições foram copiadas para
  `app/docs/evidencia/treino/`.
- `calibracao.json` — os parâmetros medidos nas fontes reais (cópia em
  `app/models/calibracao.json`).
- `modelos_treinados/` — os artefatos treinados, para carregar sem retreinar.
- `patch_crai/` — o patch que foi aplicado neste repositório na migração.
- `README_treino.md` — o relatório de treino.

Os dados brutos (Olist, E-Commerce Customer Churn) não ficam em nenhum dos
dois repositórios: são baixados e conferidos por SHA-256 com
`python -m crai.scripts.preparar_amostra_real`.

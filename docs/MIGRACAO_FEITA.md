# Migração para o CraiV3 — registro

Feita em 12/09/2026 seguindo o roteiro `docs/MIGRACAO_CRAIV3.md`. Este arquivo
responde "cadê o arquivo X?".

## De onde veio cada coisa

**`Gavaaaaa/Crai` (o repositório antigo), com histórico.** Entrou por git, não
por cópia:
`git remote add antigo https://github.com/Gavaaaaa/Crai.git`,
`git fetch antigo` e `git checkout -b main antigo/main`.

- Vieram os 77 commits, de `sprint(ci1)` a `sprint(cv6)`. O HEAD é `1370224`
  ("Renomeia crai/ para app/...").
- Veio também a tag `baseline-pre-sprint`.
- O `origin` aponta para `Gavaaaaa/CraiV3`.

**`Gavaaaaa/Base-de-dados` (treino dos modelos).** Aplicado conforme
`patch_crai/APLICAR.md`:

- `crai_treino_modificados.patch` modificou 7 arquivos:
  - `app/crai/ml/anomaly_detector.py`
  - `app/crai/ml/failure_classifier.py`
  - `app/crai/ml/payday_inference.py`
  - `app/crai/ml/synthetic_data.py`
  - `app/crai/scripts/preparar_amostra_real.py`
  - `app/crai/scripts/train_all.py`
  - `app/requirements.txt`
- `arquivos_novos/app/` trouxe 16 arquivos novos:
  - `app/crai/ml/voluntary_risk.py` e `app/crai/ml/calibracao.py`
  - `app/crai/scripts/sanity_check_fora_do_dominio.py` e
    `app/crai/scripts/gerar_readme_treino.py`
  - `app/models/calibracao.json`
  - `app/tests/test_train_fonte.py`, `app/tests/test_synthetic_data.py` e
    `app/tests/test_readme_treino.py`
  - `app/README_treino.md`
  - `app/docs/evidencia/treino/`, com 7 arquivos: `PROVENIENCIA.json`,
    `rodada_baixa.json`, `rodada_alta.json`, `fora_do_dominio.json`,
    `log_rodada_baixa.txt`, `log_rodada_alta.txt` e `log_fora_do_dominio.txt`.

**`Gavaaaaa/mvp-crai` (a beta em JavaScript, descartada).** Só duas coisas não
visuais:

- `base_exemplo_clientes.csv` → `painel/exemplos/base_exemplo_clientes.csv`,
  byte a byte igual.
- A seção 14 do `README.md` ("O que é real, o que é simulado e limitações") →
  `docs/LIMITACOES.md`. Foi reescrita para o backend Python e teve todos os
  números conferidos contra `app/docs/evidencia/treino/`.
- Nenhum arquivo visual ou JavaScript atravessou. A comparação foi feita por
  SHA-256 de todos os arquivos.

## O que foi apagado

Tudo continua no histórico do git e no repositório antigo.

| Caminho | Por quê | Onde achar |
|---|---|---|
| `archive/` (61 arquivos) | Protótipos pré-unificação: módulos 02 autoencoder, 03 payday e 04 offer bandit, com relatórios e PNGs. | `Gavaaaaa/Crai` ou `git show 1370224:archive/...` |
| `demo/demo_clientes.db` | SQLite regenerável. | idem |
| `demo/demo_insights.html` | Dará lugar ao dashboard novo. | idem |
| `app/.env.example` | Um arquivo começando com `.env` foi lido como credencial exposta; só tinha placeholders. | `docs/CONFIGURACAO.md` (as 22 variáveis) ou `git show 1370224:app/.env.example` |

## O que foi movido (com `git mv`, histórico preservado)

| Antes | Depois |
|---|---|
| `sprints.md`, `churn_involuntario_sprints.md`, `churn_voluntario_completo.md`, `plano_onboarding.md`, `prompt_auditoria_completa.txt` | `docs/planos/` |
| `app/docs/AUDITORIA_01.md`, `AUDITORIA_01_R2.md` … `AUDITORIA_01_R11.md` (11) | `docs/historico/auditorias/` |
| `app/docs/ACHADOS_CANAL_WHATSAPP.md`, `APROVACAO_SPRINT3.md`, `baseline_pytest.txt` | `docs/historico/` |
| `demo/demo_server.py`, `demo_visual.py`, `demo_base_clientes.csv` | `docs/historico/demo-antiga/` (a pasta `demo/` deixou de existir) |
| `MIGRACAO_CRAIV3.md` (raiz; não era versionado, então foi `mv` comum) | `docs/MIGRACAO_CRAIV3.md` |

## O que foi criado ou reescrito

- `painel/.gitkeep`: a pasta que vai receber o dashboard.
- `docs/LIMITACOES.md`, `docs/CONFIGURACAO.md`, `docs/ESTRUTURA.md`,
  `docs/historico/README.md` e este arquivo.
- `README.md` da raiz, **reescrito**. O anterior tinha 43 KB e descrevia a
  estrutura antiga; está no histórico do git.
- `scripts/pre-commit`: a exceção para `.env.example` saiu. Agora qualquer
  `.env*` estagiado é bloqueado.

## Ambiente em que a migração foi verificada

- **Pacotes instalados nesta sessão**, nas versões travadas do
  `requirements.txt`:
  - `PyJWT[crypto]==2.9.0`
  - `cryptography==50.0.1`
  - `python-multipart==0.0.12`
  - `openpyxl==3.1.5`
- **Gate de testes:** diferencial, comparando a suíte antes e depois de cada
  etapa, teste a teste. Nenhum teste que passava passou a falhar.
- **Resultado final da suíte** (`cd app && pytest tests/ -q`), com o patch
  aplicado e sem modelos treinados em `app/models/`: **1039 passed, 5 skipped**.

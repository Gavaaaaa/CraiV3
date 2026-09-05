"""tests/conftest.py — nenhum teste escreve no estado real do projeto.

POR QUE ISTO EXISTE, e por que só apareceu no Sprint 4 do churn voluntário.

O `bandit_state.json` real do projeto tinha 98 KB e SETE perfis que não
existem — `""`, `"abc"`, `"299,90"`, um inteiro de 400 dígitos. Ninguém os
escreveu de propósito: são testes de borda dos webhooks que atravessaram a API
até `record_outcome`, que aceita qualquer string como perfil e PERSISTE. A
suíte estava treinando o bandit de produção com fuzz. O Sprint 1 saneou a
LEITURA e isolou `MODELS_DIR` nos testes novos; o Sprint 4 mostrou que isso
tratava o sintoma, porque o `retention_log` — que nasceu no mesmo dia —
imediatamente acumulou 169 ciclos de teste no banco real pela mesma porta.

A causa é estrutural e não específica de um módulo: qualquer teste que chame a
API dispara o pipeline inteiro, e o pipeline persiste. A defesa também tem que
ser estrutural — um `conftest` que vale para a suíte toda, não uma fixture que
cada arquivo novo precisa lembrar de escrever.

O QUE É ISOLADO E O QUE NÃO É:

    CRAI_RETENTION_DB   isolado para TODOS os testes. É o dataset de treino do
                        churn voluntário: linha de teste ali vira linha de
                        treino depois.

    MODELS_DIR/STATE_PATH do bandit  NÃO é isolado aqui, e é deliberado: os
                        testes de ML (`test_metricas_declaradas`,
                        `test_failure_classifier`) LEEM os modelos reais de
                        `crai/models/` de propósito — é o gate de honestidade
                        do README. Redirecionar globalmente quebraria o que
                        eles existem para medir. Quem escreve no bandit isola
                        por arquivo (ver `test_voluntary_autonomy.py` e
                        `test_retention_outcome.py`).
"""

import pytest


@pytest.fixture(autouse=True)
def banco_de_ciclos_isolado(tmp_path, monkeypatch):
    """Redireciona o log de ciclos de retenção para `tmp_path` em cada teste.

    `autouse` porque a escrita acontece dentro do pipeline, longe do teste que
    a disparou: exigir que cada arquivo lembre de isolar é exatamente o que
    falhou antes. Um teste que QUEIRA o banco real sobrescreve a env
    explicitamente — e aí a intenção fica escrita.
    """
    monkeypatch.setenv("CRAI_RETENTION_DB", str(tmp_path / "ciclos_de_teste.db"))

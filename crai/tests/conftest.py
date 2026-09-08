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

from crai.api.idempotencia import limpar_tudo as limpar_idempotencia


@pytest.fixture(autouse=True)
def banco_de_ciclos_isolado(tmp_path, monkeypatch):
    """Redireciona o log de ciclos de retenção para `tmp_path` em cada teste.

    `autouse` porque a escrita acontece dentro do pipeline, longe do teste que
    a disparou: exigir que cada arquivo lembre de isolar é exatamente o que
    falhou antes. Um teste que QUEIRA o banco real sobrescreve a env
    explicitamente — e aí a intenção fica escrita.
    """
    monkeypatch.setenv("CRAI_RETENTION_DB", str(tmp_path / "ciclos_de_teste.db"))
    # Mesma razão, outro arquivo: desde o Sprint 2 do involuntário o nó de
    # agendamento grava o plano de tentativas do BACEN em `data/`. Um teste que
    # dispare o pipeline deixaria planos pendentes no estado real, e a próxima
    # passagem do agendador — na demo, na mão de quem apresenta — reenviaria
    # cobrança de cliente de teste ao PSP.
    monkeypatch.setenv("CRAI_RETRY_STATE", str(tmp_path / "pix_retry_de_teste.json"))
    # O dataset de treino do involuntário (Sprint 6). Linha de teste aqui vira
    # linha de treino depois, e envenena a métrica de negócio que a banca vê —
    # exatamente o que aconteceu com o `retention_log` do voluntário, que
    # acumulou 169 ciclos de teste no banco real antes desta defesa existir.
    monkeypatch.setenv("CRAI_RECOVERY_DB", str(tmp_path / "recuperacoes_de_teste.db"))


@pytest.fixture(autouse=True)
def janelas_de_idempotencia_limpas():
    """Nenhum teste herda os eventos que outro teste enviou.

    As janelas de idempotência (`crai/api/idempotencia.py`) são estado de
    módulo: vivem enquanto o processo viver. Numa suíte, isso significa que um
    arquivo que envia `RN_x/E_x` faz o arquivo seguinte, que envia o mesmo par,
    receber `pipeline: False` — falha que aparece só quando os dois rodam
    juntos, na ordem errada, e some quando o teste é rodado sozinho.

    É a mesma classe de acoplamento que a fixture do banco de ciclos acima
    fecha, e a defesa é a mesma: estrutural, na suíte inteira, em vez de uma
    linha que cada arquivo novo precise lembrar de escrever.
    """
    limpar_idempotencia()
    yield
    limpar_idempotencia()

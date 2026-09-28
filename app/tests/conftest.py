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

    MODELS_DIR do `crai.ml`  NÃO é isolado aqui, e é deliberado: os testes de
                        ML (`test_metricas_declaradas`,
                        `test_failure_classifier`) LEEM os modelos reais de
                        `crai/models/` de propósito — é o gate de honestidade
                        do README. Redirecionar globalmente quebraria o que
                        eles existem para medir.

    MODELS_DIR/STATE_PATH do bandit  PASSOU a ser isolado em 27/09/2026, e não
                        contradiz o parágrafo acima: `offer_bandit.MODELS_DIR`
                        é constante do próprio módulo, e mexer nela não
                        encosta nos artefatos que os testes de ML leem. A
                        regra anterior — "quem escreve no bandit isola por
                        arquivo" — falhou pelo motivo de sempre: a escrita
                        acontece dentro do pipeline, e quem a dispara nem
                        sabe. O `bandit_state.json` real acabou com quatro
                        tenants de teste dentro (`empresa-a`, `empresa-b`,
                        `painel_avaliacao`, `empresa-exemplo`). Ver
                        `estado_global_do_voluntario_isolado` e
                        `docs/RELATORIO_ESTABILIDADE.md`.
"""

import copy
import os

import numpy
import pytest

from crai.api.idempotencia import limpar_tudo as limpar_idempotencia


@pytest.fixture(scope="session", autouse=True)
def a_env_de_simulacao_nao_vaza_para_a_sessao():
    """CATRACA: a sessão não pode COMEÇAR com `CRAI_SIMULATE_OUTCOMES` ligada.

    Esta é a fixture que existe para um defeito que já aconteceu, não para um
    hipotético. `test_pipeline.py` é um script de demonstração, mas o nome
    casa com `python_files = test_*.py`: o pytest o IMPORTAVA na coleta, e o
    import executava `os.environ.setdefault("CRAI_SIMULATE_OUTCOMES", "1")`
    em nível de módulo. A env ficava ligada pela sessão inteira e ninguém
    desfazia — nem este conftest, que isolava bancos e caches, não o
    ambiente.

    O que isso muda não é um detalhe: com a env ligada,
    `build_voluntary_churn_graph` monta OUTRA topologia, com o nó
    `track_outcome`, que sorteia o desfecho e fecha o ciclo na hora. Metade
    da suíte passava a exercitar o grafo da demo achando que exercitava o de
    produção, e dois testes falhavam de forma que só aparecia na suíte cheia.
    O diagnóstico inteiro está em `docs/RELATORIO_ESTABILIDADE.md`.

    Por que uma catraca e não só o conserto: o conserto é de UM arquivo, e a
    porta continua aberta para o próximo script que alguém batize de
    `test_alguma_coisa.py`. Aqui a sessão para na hora, com o nome do
    culpado a um `sys.modules` de distância.

    O que NÃO é barrado, de propósito:
      - `CRAI_SIMULATE_OUTCOMES=0` ou ausente — é o modo de produção, que é
        o que a suíte deve exercitar por padrão;
      - `monkeypatch.setenv(...)` DENTRO de um teste — esta fixture é de
        sessão e roda uma vez, antes do primeiro teste; quem liga a env no
        próprio teste está declarando a intenção e desfaz ao sair
        (`test_retention_outcome.py`, `test_tenant_isolation_voluntary.py`,
        `test_webhook_security.py` fazem exatamente isso).
    """
    from crai.churn_voluntary import voluntary_agent as va

    if va.modo_simulacao():
        import sys
        suspeitos = [m for m in sys.modules
                     if m.split(".")[-1].startswith("test_")]
        valor = os.environ.get("CRAI_SIMULATE_OUTCOMES")
        # `pytest.exit` e não `pytest.fail`: a fixture é de sessão, então um
        # `fail` viraria UM ERRO POR TESTE — 1469 linhas para dizer uma coisa
        # só. Aqui a sessão para na primeira, com código de saída != 0 e a
        # mensagem inteira à vista.
        pytest.exit(returncode=1, reason=f"""
CRAI_SIMULATE_OUTCOMES está LIGADA no início da sessão (valor {valor!r}).

Com ela ligada, `build_voluntary_churn_graph` monta outra topologia: entra o
nó `track_outcome`, que sorteia o desfecho e fecha o ciclo na hora. A suíte
deixa de exercitar o grafo de produção e dois testes passam a falhar de forma
intermitente.

Se foi um script coletado pelo pytest que a ligou no import, mova a chamada
para dentro de `if __name__ == "__main__":` — foi o que se fez em
`app/test_pipeline.py` (ver `docs/RELATORIO_ESTABILIDADE.md`).

Se foi você, de propósito: um teste que PRECISA do modo simulação liga a env
com `monkeypatch.setenv` DENTRO do próprio teste, não no ambiente da sessão.

Módulos `test_*` já importados nesta sessão: {sorted(suspeitos)}
""")
    yield


@pytest.fixture(autouse=True)
def estado_global_do_voluntario_isolado(tmp_path, monkeypatch):
    """Os quatro estados de processo do pipeline voluntário, zerados por TESTE.

    O `banco_de_ciclos_isolado` abaixo já cuidava do que mora em disco. O que
    mora em memória de módulo continuava atravessando a suíte inteira, e
    `docs/RELATORIO_ESTABILIDADE.md` mostra os quatro flagrados em ação:

      1. `CRAI_SIMULATE_OUTCOMES` — a env que escolhe a TOPOLOGIA do grafo.
         Apagada aqui para que o default da suíte seja produção, sempre. A
         catraca de sessão acima pega quem a ligou antes de começar; esta
         linha garante que nenhum teste herde a env de outro.
      2. `_channel_history` — a memória de "por qual canal este cliente já
         converteu". Foi vista não-vazia no setup de testes que não têm nada
         com canal (`test_payment_gateway`, `test_insights_endpoint`): só
         alguns arquivos a limpavam, cada um por conta própria.
      3. Os `MemorySaver` dos DOIS grafos compilados no import
         (`voluntary_agent._AGENTES`). Ninguém os zerava: 46 threads
         acumuladas ao fim da suíte, e testes diferentes chegando a
         compartilhar checkpoint porque o `thread_id` do painel colide.
      4. O `_bandit`, que é singleton de módulo E persiste em disco. O
         caminho vai para `tmp_path` (o `conftest` NÃO isola `MODELS_DIR` do
         `crai.ml`, e não é isso que se faz aqui: `offer_bandit.MODELS_DIR` é
         constante do próprio módulo, e mexer nela não encosta nos modelos
         que `test_metricas_declaradas` lê de propósito). Os posteriores EM
         MEMÓRIA são restaurados do snapshot tirado no import — assim cada
         teste começa do mesmo warm start real, e nenhum aceite de teste
         sobrevive ao próximo.

    Sem o item 4, o `app/models/bandit_state.json` real ia acumulando tenants
    de teste — que é o incidente que a docstring deste arquivo diz ter
    fechado, reaberto por outra porta.
    """
    from crai.churn_voluntary import offer_bandit as ob
    from crai.churn_voluntary import voluntary_agent as va

    monkeypatch.delenv("CRAI_SIMULATE_OUTCOMES", raising=False)
    monkeypatch.setattr(va, "_channel_history", {})

    pasta = tmp_path / "models_bandit"
    monkeypatch.setattr(ob, "MODELS_DIR", pasta)
    monkeypatch.setattr(ob, "STATE_PATH", pasta / "bandit_state.json")
    monkeypatch.setattr(va._bandit, "state", copy.deepcopy(_warm_start(va)))
    # O RNG do Thompson Sampling também é estado: sem isto, QUAL oferta o
    # bandit amostra depende de quantos sorteios os testes anteriores fizeram.
    monkeypatch.setattr(va._bandit, "rng", numpy.random.default_rng(42))

    _zerar_checkpointers(va)
    yield
    _zerar_checkpointers(va)


_SNAPSHOT: dict = {}


def _warm_start(va) -> dict:
    """Os posteriores como o import de `voluntary_agent` os deixou.

    Tirado na primeira vez que a fixture roda e guardado: é o `_bandit.load()`
    da linha 67 do módulo, isto é, o warm start real do projeto. Restaurar
    ISTO (e não priors frios) mantém cada teste vendo o mesmo bandit que veria
    antes deste conftest existir — o que muda é só que ele não leva embora o
    que aprendeu.
    """
    if "estado" not in _SNAPSHOT:
        _SNAPSHOT["estado"] = copy.deepcopy(va._bandit.state)
    return _SNAPSHOT["estado"]


def _zerar_checkpointers(va) -> None:
    """Os `MemorySaver` são criados no import e não têm dono que os limpe."""
    for grafo in getattr(va, "_AGENTES", {}).values():
        cp = getattr(grafo, "checkpointer", None)
        for nome in ("storage", "writes"):
            alvo = getattr(cp, nome, None)
            if alvo is not None:
                alvo.clear()


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
    # A base de clientes importada (self-service, Sprint 2). Em produção mora
    # no Postgres do Supabase; aqui vai para um SQLite em `tmp_path` com o
    # MESMO SQL. `SUPABASE_DB_URL` é apagada: se um `.env` local a tiver, o
    # Postgres venceria e a suíte escreveria na base real de uma empresa.
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    monkeypatch.setenv("CRAI_CLIENTES_DB", str(tmp_path / "clientes_de_teste.db"))
    from crai.churn_voluntary import clientes_importados
    clientes_importados.esquecer_schema_garantido()


@pytest.fixture
def supabase_falso(monkeypatch):
    """Um projeto Supabase local: env configurada, JWKS servido sem rede.

    Não é autouse: só as rotas do self-service autenticam por JWT, e os testes
    dos webhooks não devem depender de um Supabase, nem falso. Devolve o
    `ProjetoFalso` — `.bearer("empresa-x")` dá o header pronto.
    """
    from crai.accounts import supabase_auth
    from tests.supabase_falso import PROJECT_URL, ProjetoFalso

    projeto = ProjetoFalso()
    monkeypatch.setenv("SUPABASE_PROJECT_URL", PROJECT_URL)
    monkeypatch.setattr(supabase_auth, "_baixar_jwks", projeto.baixar_jwks)
    supabase_auth.limpar_cache()
    yield projeto
    supabase_auth.limpar_cache()


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


@pytest.fixture(autouse=True)
def marca_da_regua_limpa():
    """Mesma classe de acoplamento: `batch_scoring` guarda, por tenant, quando
    foi o último cálculo em lote da régua (é o `regua_calculada_em` da API de
    clientes). Um teste que pediu o `/insights` não pode deixar a marca para
    o próximo, que espera `null` antes do primeiro cálculo."""
    from crai.churn_voluntary import batch_scoring
    batch_scoring.esquecer_calculos_da_regua()
    yield
    batch_scoring.esquecer_calculos_da_regua()

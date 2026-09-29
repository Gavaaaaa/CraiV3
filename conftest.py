"""conftest.py (RAIZ) — nenhuma execução do pytest escreve em `app/data/`.

POR QUE NA RAIZ, e não em `app/tests/conftest.py`. O pytest, rodado da raiz
`E:\\Crai` com os defaults (`python_files = test_*.py`), coleta `app/tests/`
E importa `app/test_pipeline.py` — um script de demonstração cujo nome casa
com o padrão. O `conftest` de `app/tests/` só alcança o que está abaixo dele;
este alcança tudo o que a raiz coleta, inclusive o script.

O INCIDENTE QUE ESTA TRAVA FECHA (28/09/2026, Etapa 1, Bloco 2). Dois testes
usaram `monkeypatch.undo()` para "o banco voltar" depois de simular uma
falha. O `undo` desfaz TODOS os patches do teste — inclusive as envs de
isolamento (`CRAI_RECOVERY_DB`, `CRAI_RETENTION_DB`, `CRAI_RETRY_STATE`,
`CRAI_CLIENTES_DB`) que as fixtures autouse tinham posto — e a chamada
seguinte rodou o pipeline REAL contra `app/data/recovery_cycles.db` e
`app/data/retention_cycles.db`: dois ciclos de teste com seis tentativas
pendentes, duas linhas no dataset de treino e seis decisões na trilha do
Art. 20. Foi visto num `ls` de conferência, não por um teste. Esta fixture é
o teste.

O QUE ELA FAZ. ANTES DA COLETA (`pytest_sessionstart`), registra tamanho e
mtime de cada arquivo sob `app/data/` (recursivo, inclusive `v2/`). A
fotografia é tirada antes da coleta de propósito: a coleta IMPORTA
`app/test_pipeline.py`, e um efeito de import escreveria antes de qualquer
fixture existir. No setup da fixture de sessão (depois da coleta) e de novo
no fim, compara. Qualquer arquivo modificado, criado ou removido FALHA a
sessão, com o nome de cada um e o que mudou — e dizendo se foi durante a
coleta ou durante os testes. É de sessão e `autouse`, então vale para todo
teste sem que nenhum arquivo precise pedi-la — a mesma escolha estrutural do
`app/tests/conftest.py` para os bancos.

O que ela NÃO faz: não impede a escrita (isso é o isolamento por env, que
continua sendo a defesa de verdade). Ela pega a escrita que escapou, na
mesma sessão, com nome. A varredura de texto que proíbe `monkeypatch.undo()`
em `app/tests/` está em `app/tests/test_isolamento_dos_bancos_reais.py`.
"""

from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent
PASTA_DE_DADOS = RAIZ / "app" / "data"

_ANTES_DA_COLETA: dict = {}


def _fotografia(pasta: Path) -> dict[str, tuple[int, int]]:
    """{caminho relativo: (tamanho, mtime_ns)} de todo arquivo sob `pasta`."""
    if not pasta.exists():
        return {}
    return {
        str(arquivo.relative_to(pasta)).replace("\\", "/"): (
            arquivo.stat().st_size, arquivo.stat().st_mtime_ns)
        for arquivo in sorted(pasta.rglob("*")) if arquivo.is_file()
    }


def _diferencas(antes: dict, depois: dict) -> list[str]:
    linhas = []
    for nome in sorted(set(antes) | set(depois)):
        a, d = antes.get(nome), depois.get(nome)
        if a is None:
            linhas.append(f"CRIADO     {nome} ({d[0]} bytes)")
        elif d is None:
            linhas.append(f"REMOVIDO   {nome}")
        elif a != d:
            linhas.append(f"MODIFICADO {nome}: {a[0]} -> {d[0]} bytes, mtime mudou")
    return linhas


def pytest_sessionstart(session):
    """A fotografia é tirada aqui, ANTES da coleta (ver docstring do módulo)."""
    _ANTES_DA_COLETA.clear()
    _ANTES_DA_COLETA.update(_fotografia(PASTA_DE_DADOS))


def _falhar_se_mudou(quando: str) -> None:
    diferencas = _diferencas(_ANTES_DA_COLETA, _fotografia(PASTA_DE_DADOS))
    if diferencas:
        pytest.fail(
            f"ALGUÉM ESCREVEU NOS DADOS REAIS de app/data/ {quando}:\n  "
            + "\n  ".join(diferencas)
            + "\n\nO isolamento por env (CRAI_RECOVERY_DB, CRAI_RETENTION_DB, "
              "CRAI_RETRY_STATE, CRAI_CLIENTES_DB) foi perdido em algum teste, ou um "
              "import de coleta escreveu — `monkeypatch.undo()` é a causa conhecida "
              "(28/09/2026). Restaure app/data/ do backup e conserte antes de continuar.",
            pytrace=False,
        )


@pytest.fixture(scope="session", autouse=True)
def nenhum_teste_escreve_em_app_data():
    """CATRACA de sessão: `app/data/` tem que estar byte a byte como começou."""
    _falhar_se_mudou("durante a COLETA (import de um módulo test_*)")
    yield
    _falhar_se_mudou("durante os TESTES desta sessão")

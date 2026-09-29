"""tests/test_isolamento_dos_bancos_reais.py — nenhum teste pode perder o isolamento.

O incidente de 28/09/2026 (Etapa 1, Bloco 2): `monkeypatch.undo()` num teste
desfez as envs de isolamento das fixtures autouse do `conftest`, e a chamada
seguinte escreveu nos bancos REAIS de `app/data/`. A trava que pega a escrita
está em `conftest.py` da RAIZ (fotografia de `app/data/` no início e no fim
da sessão). Este arquivo fecha a porta pelo outro lado: a varredura do texto
de `app/tests/` reprova qualquer chamada a `monkeypatch.undo(`.

Comentários e docstrings são descartados antes da varredura: o próprio
incidente está documentado em comentários dos testes corrigidos, e citar o
nome do defeito não é cometê-lo.
"""

import io
import tokenize
from pathlib import Path

import pytest

PASTA_DE_TESTES = Path(__file__).resolve().parent
RAIZ = PASTA_DE_TESTES.parent.parent
PROIBIDO = "monkeypatch" + ".undo("


def _codigo_sem_comentarios_nem_docstrings(fonte: str) -> str:
    """Só tokens de código: comentários e strings viram vazio."""
    partes = []
    for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        partes.append(tok.string)
    return " ".join(partes)


def _arquivos_de_teste() -> list[Path]:
    return sorted(p for p in PASTA_DE_TESTES.glob("*.py"))


def test_nenhum_teste_chama_monkeypatch_undo():
    culpados = []
    for arquivo in _arquivos_de_teste():
        codigo = _codigo_sem_comentarios_nem_docstrings(arquivo.read_text(encoding="utf-8"))
        if PROIBIDO.replace(" ", "") in codigo.replace(" ", ""):
            culpados.append(arquivo.name)
    assert not culpados, (
        f"`{PROIBIDO})` em {culpados}: desfaz as envs de isolamento das fixtures "
        "autouse e a chamada seguinte escreve nos bancos REAIS (28/09/2026). "
        "Guarde o original e restaure com `monkeypatch.setattr`."
    )


def test_a_varredura_enxerga_codigo_e_ignora_comentario(tmp_path):
    """A varredura não pode ser cega (nem paranoica): código pega, comentário não."""
    codigo = _codigo_sem_comentarios_nem_docstrings(
        "x = 1\n# monkeypatch.undo() aqui é só comentário\n'''monkeypatch.undo()'''\n")
    assert PROIBIDO not in codigo
    codigo = _codigo_sem_comentarios_nem_docstrings("def t(monkeypatch):\n    monkeypatch.undo()\n")
    assert PROIBIDO in codigo.replace(" ", "")


def test_a_catraca_da_raiz_existe_e_esta_ligada():
    """O conftest da raiz é o que vale para tudo o que a raiz coleta, inclusive
    `app/test_pipeline.py`. Se alguém o apagar, este teste avisa."""
    conftest = RAIZ / "conftest.py"
    assert conftest.exists(), "conftest.py da raiz sumiu — a catraca de app/data/ não existe mais"
    texto = conftest.read_text(encoding="utf-8")
    assert 'scope="session"' in texto and "autouse=True" in texto
    assert "nenhum_teste_escreve_em_app_data" in texto


def test_os_arquivos_de_dados_reais_estao_fora_das_envs_de_teste(monkeypatch):
    """As envs que o conftest de app/tests põe apontam para fora de app/data/."""
    import os
    pasta_real = str((RAIZ / "app" / "data").resolve()).lower()
    for env in ("CRAI_RECOVERY_DB", "CRAI_RETENTION_DB", "CRAI_RETRY_STATE", "CRAI_CLIENTES_DB"):
        valor = os.environ.get(env, "")
        assert valor, f"{env} não está isolada nesta sessão"
        assert not str(Path(valor).resolve()).lower().startswith(pasta_real), (
            f"{env} aponta para dentro de app/data/: {valor}")

"""tests/test_manifesto_declarado.py — o manifesto DECLARADO é o manifesto da base que treinou.

`app/data/` está no `.gitignore`. Até o Bloco I (22/09/2026) o repositório do
sistema não dizia, em lugar nenhum versionado, qual base treinou os modelos
que ele declara no README: o `MANIFESTO.json` com os cinco sha256 só existia
na máquina de quem gerou a base e no repositório de evidência
(`github.com/Gavaaaaa/Base-de-dados`).

`docs/base-v2/MANIFESTO.json` é a cópia declarada. Este teste garante que ela
não minta em silêncio: se `app/data/v2/` existe nesta máquina, os dois
manifestos têm que ser IGUAIS. Alguém que regenere a base (outra semente,
outro tamanho, outro pandas → outros sha256) e não atualize a cópia declarada
reprova aqui, antes de treinar um modelo que o README descreveria com a base
errada.

Num checkout sem `app/data/v2/`, a comparação pula — mas a cópia declarada
tem que existir e descrever cinco arquivos com sha256, sempre.
"""

import json
from pathlib import Path

import pytest

RAIZ_REPO = Path(__file__).resolve().parent.parent.parent
DECLARADO = RAIZ_REPO / "docs" / "base-v2" / "MANIFESTO.json"
LOCAL = RAIZ_REPO / "app" / "data" / "v2" / "MANIFESTO.json"
CINCO = ("populacao", "classificador", "comportamental", "liquidez", "voluntario")


def _ler(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding="utf-8"))


class TestOManifestoDeclarado:

    def test_a_copia_declarada_existe_e_descreve_a_base_v2(self):
        assert DECLARADO.exists(), f"{DECLARADO} não existe — o repositório não declara qual base treinou os modelos"
        m = _ler(DECLARADO)
        assert m["gerado_por"] == "crai.scripts.gerar_bases_v2"
        assert m["semente"] == 42
        assert set(m["arquivos"]) == set(CINCO)
        for nome in CINCO:
            assert len(m["arquivos"][nome]["sha256"]) == 64, nome
            assert m["arquivos"][nome]["linhas"] > 0, nome

    def test_a_base_local_e_a_declarada(self):
        """Falha se `app/data/v2/MANIFESTO.json` divergir da cópia declarada."""
        if not LOCAL.exists():
            pytest.skip(f"{LOCAL} ausente — nenhuma base v2 nesta máquina")
        local, declarado = _ler(LOCAL), _ler(DECLARADO)
        diferencas = []
        for nome in CINCO:
            for chave in ("sha256", "linhas", "colunas", "lista_de_colunas", "bytes"):
                if local["arquivos"][nome].get(chave) != declarado["arquivos"][nome].get(chave):
                    diferencas.append(f"{nome}.{chave}: local={local['arquivos'][nome].get(chave)!r} "
                                      f"declarado={declarado['arquivos'][nome].get(chave)!r}")
        for chave in ("semente", "end_date", "fonte_de_parametros", "populacao", "verificacoes"):
            if local.get(chave) != declarado.get(chave):
                diferencas.append(f"{chave} difere")
        assert not diferencas, (
            "a base em app/data/v2/ NÃO é a que docs/base-v2/MANIFESTO.json declara:\n  "
            + "\n  ".join(diferencas)
            + "\nOu a base local foi regenerada (atualize a cópia declarada e o README), "
              "ou a cópia declarada está errada.")
        assert local == declarado, "os manifestos diferem em algum campo fora dos conferidos acima"

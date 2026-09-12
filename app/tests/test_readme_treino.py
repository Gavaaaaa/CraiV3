"""tests/test_readme_treino.py — o README_treino.md não pode divergir dos JSONs.

Mesma classe de gate de `tests/test_metricas_declaradas.py`: um número escrito
num documento que nenhum teste toca só é conferido quando alguém desconfia
dele. Aqui o README de treino é GERADO (`crai/scripts/gerar_readme_treino.py`)
a partir de `models/calibracao.json` e `docs/evidencia/treino/*.json`; este
teste exige que o arquivo em disco seja exatamente o que o gerador produz
hoje. Mudou um JSON, regere o README — senão a suíte reprova.

Os JSONs de evidência estão no git; `calibracao.json` também (exceção do
`.gitignore`). Se algum faltar, o teste pula em vez de reprovar um clone que
nunca rodou o treino.

Uso:
    pytest tests/test_readme_treino.py -v
"""

from pathlib import Path

import pytest

from crai.scripts import gerar_readme_treino as g

ARQUIVOS = (g.CALIBRACAO, g.EVIDENCIA / "rodada_baixa.json",
            g.EVIDENCIA / "rodada_alta.json", g.EVIDENCIA / "fora_do_dominio.json")

requer_evidencia = pytest.mark.skipif(
    not all(Path(a).exists() for a in ARQUIVOS),
    reason="calibracao.json ou docs/evidencia/treino/*.json ausentes",
)


@requer_evidencia
class TestReadmeTreino:

    def test_readme_existe(self):
        assert g.README.exists(), "app/README_treino.md nao existe — rode gerar_readme_treino"

    def test_readme_e_o_que_o_gerador_produz(self):
        em_disco = g.README.read_text(encoding="utf-8")
        gerado = g.gerar()
        assert em_disco == gerado, (
            "README_treino.md difere do que os JSONs produzem. Regere com "
            "`python -m crai.scripts.gerar_readme_treino` em vez de editar a mao")

    def test_readme_declara_o_que_nao_e(self):
        texto = g.README.read_text(encoding="utf-8")
        assert "NAO e treino com dado real de churn observado" in texto
        for bloqueio in ("DBPerfilProvider._consultar()", "Sinal real de cancelamento",
                         "corte 0,60", "desfecho por webhook"):
            assert bloqueio in texto, f"bloqueio nao declarado: {bloqueio}"

    def test_readme_cobre_as_secoes_pedidas(self):
        texto = g.README.read_text(encoding="utf-8")
        for secao in ("Fonte e volume por modelo", "Tabela de features",
                      "Mecanismo de retreino", "Riscos conhecidos e mitigacao",
                      "offer_bandit.py", "Versionamento", "Como reproduzir"):
            assert secao in texto, f"secao ausente: {secao}"

    def test_readme_cita_os_volumes_declarados(self):
        texto = g.README.read_text(encoding="utf-8")
        for baixo, alto in ((3000, 6000), (5500, 11000), (600, 1200)):
            assert f"{baixo} -> {alto}" in texto

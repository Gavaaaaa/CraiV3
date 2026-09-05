"""tests/test_encoding_saida.py — catraca do defeito N-12.

O console padrão do Windows abre em **cp1252**. Uma string que chegue ao
`stdout` com um caractere fora dessa tabela derruba o processo com
`UnicodeEncodeError` — não degrada, não avisa: mata. Foi o que aconteceu com
`python -m crai.scripts.train_all`, que é o gate do Sprint 4, quando
`4109d84` introduziu um `->` desenhado (U+2192) dentro do `train()` do
classificador.

Este arquivo não conserta o problema; ele **trava o inventário**. A auditoria
A1-r4 mediu 12 ocorrências no pacote `crai/` do `HEAD` e 7 no
`baseline-pre-sprint`. As 5 de diferença foram introduzidas por este diff e
corrigidas neste sprint.

A auditoria A1-r5 mostrou que essa contagem, embora certa, era **estreita**: a
varredura olhava só `crai/crai/**`, e a docstring aqui dizia "qualquer módulo".
Fora do pacote, `test_pipeline.py` — que é o PRIMEIRO comando do README,
`python test_pipeline.py` — carrega outras 15 ocorrências e morre na linha 103
num console cp1252. O inventário real do projeto era 22, não 7, e este arquivo
afirmava cobri-lo.

Agora a varredura começa na RAIZ do repositório e o inventário declarado é o
inventário inteiro: 22 ocorrências, medidas nas duas pontas. Em
`baseline-pre-sprint` são as mesmas 22, arquivo por arquivo e linha por linha
em `test_pipeline.py` — ou seja, a dívida é herdada, não introduzida, e a
contribuição líquida deste diff para o N-12 é ZERO. A solução sistêmica
(`sys.stdout.reconfigure`) é do Sprint 5.

A catraca é o ponto: qualquer ocorrência NOVA, em qualquer arquivo `.py` do
repositório, falha aqui. O número declarado só pode descer.

Por que a contagem exclui docstrings: elas nunca são impressas. E por que ela
inclui f-strings explicitamente: a partir do Python 3.12 (PEP 701) o
`tokenize` devolve f-strings como `FSTRING_MIDDLE`, não como `STRING` — uma
varredura que filtre só por `STRING` deixa passar todo `print(f"...")`, que é
a forma da maior parte da saída deste projeto. A primeira versão desta
varredura cometeu exatamente esse erro e contou 4 onde havia 12.
"""

import ast
import io
import tokenize
from pathlib import Path

import pytest

# Raiz do repositório, não o pacote: a varredura precisa alcançar
# `test_pipeline.py`, que vive fora de `crai/` e é o primeiro comando do README.
RAIZ = Path(__file__).resolve().parent.parent

# Inventário medido nas auditorias A1-r4 e A1-r5, por arquivo. Estas 22
# ocorrências estão idênticas em `baseline-pre-sprint` — mesmos arquivos,
# mesmas contagens: são dívida herdada, não introduzida.
#
#   crai/agent/workflow.py                  "Decisão: mensagem_pagamento (... -> boleto)"
#   crai/churn_voluntary/voluntary_agent.py  os emoji de aceite e recusa (2 caracteres)
#   crai/dunning/dunning_engine.py           "[DUNNING] CANAL -> cliente"
#   crai/dunning/pix_automatico_retry.py     "(dd/mm hh:mm -> dd/mm hh:mm)"
#   crai/ml/anomaly_detector.py              "autoencoder (n -> gargalo)" (2 ocorrências)
#   test_pipeline.py                         cabeçalhos e separadores da saída
#                                            da demo (15 ocorrências; a linha
#                                            103 é onde o processo morre)
PENDENCIAS_PRE_EXISTENTES = {
    "crai/agent/workflow.py": 1,
    "crai/churn_voluntary/voluntary_agent.py": 2,
    "crai/dunning/dunning_engine.py": 1,
    "crai/dunning/pix_automatico_retry.py": 1,
    "crai/ml/anomaly_detector.py": 2,
    "test_pipeline.py": 15,
}

TOTAL_DECLARADO = sum(PENDENCIAS_PRE_EXISTENTES.values())

# Módulos que este sprint limpou. Aqui a exigência é zero, sem tolerância:
# `ml/failure_classifier.py` é o que derrubava o gate do Sprint 4.
MODULOS_QUE_DEVEM_ESTAR_LIMPOS = (
    "crai/ml/failure_classifier.py",
    "crai/integrations/payment_gateway.py",
    "crai/scripts/preparar_amostra_real.py",
)

# Diretórios que não são código do projeto e não devem entrar na contagem.
IGNORADOS = (".git", ".venv", "venv", "build", "dist", "__pycache__", ".pytest_cache")


def _fora_do_cp1252(texto: str) -> str:
    ruins = []
    for caractere in texto:
        if caractere.isascii():
            continue
        try:
            caractere.encode("cp1252")
        except UnicodeEncodeError:
            ruins.append(caractere)
    return "".join(ruins)


def _linhas_de_docstring(caminho: Path) -> set:
    """Docstrings nunca são impressas — ficam fora da contagem."""
    try:
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - não há arquivo assim no pacote
        return set()

    alvo = set()
    for no in ast.walk(arvore):
        corpo = getattr(no, "body", None)
        if not isinstance(no, (ast.Module, ast.ClassDef, ast.FunctionDef,
                               ast.AsyncFunctionDef)) or not corpo:
            continue
        primeiro = corpo[0]
        if (isinstance(primeiro, ast.Expr)
                and isinstance(primeiro.value, ast.Constant)
                and isinstance(primeiro.value.value, str)):
            alvo.add(primeiro.value.lineno)
    return alvo


def _ocorrencias(caminho: Path) -> list:
    """(linha, caracteres) de cada literal imprimível fora do cp1252."""
    docstrings = _linhas_de_docstring(caminho)
    with io.open(caminho, "rb") as arquivo:
        try:
            tokens = list(tokenize.tokenize(arquivo.readline))
        except tokenize.TokenError:  # pragma: no cover
            return []

    # FSTRING_MIDDLE não existe antes do 3.12; o -1 nunca casa com um tipo real.
    tipos = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)}

    achados = []
    for token in tokens:
        if token.type not in tipos or token.start[0] in docstrings:
            continue
        ruins = _fora_do_cp1252(token.string)
        if ruins:
            achados.append((token.start[0], ruins))
    return achados


def _inventario() -> dict:
    """{caminho relativo à raiz do repositório: [(linha, caracteres), ...]}"""
    mapa = {}
    for arquivo in sorted(RAIZ.rglob("*.py")):
        relativo = arquivo.relative_to(RAIZ)
        if any(parte in IGNORADOS for parte in relativo.parts):
            continue
        achados = _ocorrencias(arquivo)
        if achados:
            mapa[relativo.as_posix()] = achados
    return mapa


def _descrever(achados: list) -> str:
    return ", ".join(
        f"linha {linha} ({' '.join(f'U+{ord(c):04X}' for c in ruins)})"
        for linha, ruins in achados
    )


class TestN12Catraca:
    """O inventário de saída incompatível com cp1252 só pode diminuir."""

    @pytest.mark.parametrize("modulo", MODULOS_QUE_DEVEM_ESTAR_LIMPOS)
    def test_modulos_limpos_por_este_sprint_continuam_limpos(self, modulo):
        achados = _inventario().get(modulo, [])
        assert not achados, (
            f"N-12 voltou em {modulo}: {_descrever(achados)}. "
            "Estes módulos foram limpos neste sprint; `failure_classifier` em "
            "especial derruba `python -m crai.scripts.train_all`, o gate do "
            "Sprint 4, num console cp1252."
        )

    def test_nenhum_modulo_novo_entrou_na_lista(self):
        inesperados = {
            modulo: achados for modulo, achados in _inventario().items()
            if modulo not in PENDENCIAS_PRE_EXISTENTES
        }
        assert not inesperados, (
            "módulo fora do inventário declarado passou a imprimir caractere "
            "que o console cp1252 não codifica: "
            + "; ".join(f"{m} -> {_descrever(a)}" for m, a in inesperados.items())
        )

    def test_a_divida_herdada_nao_cresceu(self):
        inventario = _inventario()
        for modulo, esperado in PENDENCIAS_PRE_EXISTENTES.items():
            achados = inventario.get(modulo, [])
            assert len(achados) <= esperado, (
                f"{modulo}: {len(achados)} ocorrências, {esperado} declaradas "
                f"({_descrever(achados)})"
            )

    def test_a_varredura_alcanca_arquivo_fora_do_pacote(self):
        """O escopo declarado tem que ser o escopo medido.

        Catraca, nao regressao: e a correcao de uma AFIRMACAO, e afirmacao de
        docstring nenhum teste consegue reprovar retroativamente. A auditoria
        A1-r5 mostrou que este arquivo dizia varrer "qualquer modulo" e varria
        so `crai/crai/**` — deixando de fora as 15 ocorrencias de
        `test_pipeline.py`, que e o PRIMEIRO comando do README e morre na
        linha 103 num console cp1252. Este teste trava o escopo novo: se
        alguem estreitar a raiz de volta para o pacote, ele reprova.
        """
        inventario = _inventario()
        fora_do_pacote = [m for m in inventario if not m.startswith("crai/")]

        assert "test_pipeline.py" in inventario, (
            "a varredura nao alcanca `test_pipeline.py`. O inventario voltou a "
            "medir so o pacote, e a docstring deste arquivo promete o "
            f"repositorio. Arquivos vistos fora do pacote: {fora_do_pacote}"
        )

    def test_o_total_bate_com_o_medido_na_auditoria(self):
        total = sum(len(a) for a in _inventario().values())
        assert total <= TOTAL_DECLARADO, (
            f"total de {total} ocorrências contra {TOTAL_DECLARADO} declaradas "
            "nas auditorias A1-r4 e A1-r5"
        )

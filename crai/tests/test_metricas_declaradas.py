"""tests/test_metricas_declaradas.py — o README não pode mentir sobre os modelos.

A linha `§4.6` do README declara o estado dos binários em `crai/models/`. Ela
ficou **falsa por sete rodadas de auditoria**: afirmava `AUC 0,6797` e
`train_metrics.json` com schema anterior ao commit `4109d84`, enquanto o
arquivo em disco tinha `AUC 0,7029` e o schema novo.

O erro sobreviveu porque `crai/models/` está no `.gitignore`. Os auditores
recebem o diff e o repositório; os binários não estão em nenhum dos dois.
Então todo mundo — auditor e implementador — leu a *declaração* e ninguém
abriu o *arquivo*. Uma afirmação numérica que nenhum teste toca é uma
afirmação que só é verificada quando alguém suspeita dela.

Este arquivo fecha a classe: enquanto os modelos existirem na máquina, o
número do README tem que bater com o número do treino. Se não houver modelo
treinado, os testes **pulam** — um checkout limpo não pode reprovar por falta
de um artefato que o `.gitignore` garante não estar lá.

Não é um gate de qualidade do modelo (isso é o G3/G4, no `sprints.md`). É um
gate de **honestidade da documentação**, que é o que este projeto entrega para
uma banca.
"""

import json
import re
from pathlib import Path

import pytest

RAIZ_PROJETO = Path(__file__).resolve().parent.parent
METRICAS = RAIZ_PROJETO / "models" / "train_metrics.json"
README = RAIZ_PROJETO / "README.md"

# Faixa anti-vazamento dos gates G3/G4 (sprints.md). Abaixo do piso o modelo
# não sustenta a decisão; acima do teto o gerador está vazando o rótulo.
PISO_AUC, TETO_AUC = 0.70, 0.92


def _linha_46() -> str:
    for linha in README.read_text(encoding="utf-8").split("\n"):
        if linha.startswith("| **§4.6**"):
            return linha
    raise AssertionError("a linha §4.6 sumiu do README")


def _metricas() -> dict:
    if not METRICAS.exists():
        pytest.skip(f"{METRICAS} não existe — nenhum modelo treinado nesta máquina")
    return json.loads(METRICAS.read_text(encoding="utf-8"))


def _numeros_do_texto(texto: str) -> set:
    """Todo número em notação brasileira (0,7029) que aparece na linha."""
    return {float(n.replace(",", ".")) for n in re.findall(r"\d+,\d+", texto)}


class TestOReadmeNaoMenteSobreOsModelos:

    def test_a_auc_declarada_e_a_auc_medida(self):
        auc = _metricas()["auc"]
        declarados = _numeros_do_texto(_linha_46())

        assert auc in declarados, (
            f"o README §4.6 não cita a AUC real do modelo em disco ({auc}). "
            f"Números que a linha declara: {sorted(declarados)}. Foi exatamente "
            "assim que o '0,6797' sobreviveu a sete auditorias — ninguém "
            "comparou a frase com o arquivo"
        )

    def test_a_auc_medida_esta_dentro_do_gate(self):
        """Se sair da faixa, o README tem que dizer isso — e o teste, também."""
        auc = _metricas()["auc"]
        assert PISO_AUC <= auc <= TETO_AUC, (
            f"AUC {auc} fora da faixa [{PISO_AUC}; {TETO_AUC}] dos gates G3/G4. "
            "Abaixo do piso o modelo não sustenta a decisão de e-Profit; acima "
            "do teto o gerador está vazando o rótulo. A demo carrega este "
            "binário"
        )

    def test_o_schema_do_treino_e_o_atual(self):
        """`metricas_por_limiar` e `recall_operacional` nasceram em `4109d84`.

        O README afirmava que o arquivo em disco era anterior a esse commit.
        Este teste é o que torna a afirmação verificável em vez de opinável.
        """
        m = _metricas()
        for chave in ("metricas_por_limiar", "recall_operacional",
                      "limiar_classificacao"):
            assert chave in m, (
                f"`train_metrics.json` sem `{chave}` — o arquivo é anterior ao "
                "commit que introduziu a escolha de limiar por recall. "
                "Retreine antes de declarar qualquer número"
            )

    def test_o_limiar_em_uso_e_o_que_o_codigo_aplica(self):
        """A tabela por limiar tem que marcar como `em_uso` o limiar real."""
        from crai.ml.failure_classifier import LIMIAR_CLASSIFICACAO

        m = _metricas()
        assert m["limiar_classificacao"] == LIMIAR_CLASSIFICACAO, (
            f"o treino gravou limiar {m['limiar_classificacao']} e o código "
            f"aplica {LIMIAR_CLASSIFICACAO}. O relatório descreve um "
            "classificador que não é o que roda"
        )

        em_uso = [t for t in m["metricas_por_limiar"] if t.get("em_uso")]
        assert len(em_uso) == 1, (
            f"{len(em_uso)} limiares marcados como `em_uso` na tabela; "
            "esperado exatamente 1"
        )
        assert em_uso[0]["limiar"] == LIMIAR_CLASSIFICACAO

    def test_o_recall_declarado_sustenta_a_escolha_do_limiar(self):
        """O limiar foi escolhido por recall >= 0,90. Se caiu, a regra mudou.

        Não é redundante com o teste de AUC: a AUC não depende do limiar, e é
        justamente o limiar que decide quantos clientes recuperáveis o
        pipeline deixa passar.
        """
        m = _metricas()
        assert m["recall_recovered"] >= 0.90, (
            f"recall {m['recall_recovered']} abaixo de 0,90 no limiar "
            f"{m['limiar_classificacao']}. O limiar existe para sustentar esse "
            "piso; abaixo dele a escolha perde a justificativa documentada"
        )

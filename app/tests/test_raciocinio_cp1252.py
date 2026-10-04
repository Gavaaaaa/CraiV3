"""tests/test_raciocinio_cp1252.py — o raciocínio do agente cabe no console do Windows.

O DEFEITO (Rodada 2, Fase 4). `decide_recovery` monta a trilha de raciocínio em
texto e a imprime (`print(f"[RACIOCÍNIO] {passo}")`). No ramo da mensagem de
pagamento um dos passos trazia a seta desenhada (U+2192). Num terminal o Python
escreve em Unicode e nada acontece; com a saída redirecionada para arquivo no
Windows ela é codificada em cp1252, que não tem a seta, e o `print` levanta
`UnicodeEncodeError` DENTRO da requisição: o backend respondia 500 no webhook.

A catraca N-12 (`test_encoding_saida.py`) conhecia essa ocorrência e a tolerava
como dívida herdada (uma ocorrência declarada em `agent/workflow.py`). Este
arquivo mede o que ela não mede: o TEXTO que o nó produz, nos dois ramos, e o
`print` de verdade com a saída em cp1252.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from crai.agent.workflow import decide_recovery

APP = Path(__file__).resolve().parents[1]


def _estado(causa, payment_method="pix_automatico", retry_count=0, anomala=False):
    return {
        "failure_cause": causa, "recovery_score": 60, "eprofit": 100.0,
        "is_anomalous": anomala, "retry_count": retry_count, "amount": 200.0,
        "customer_id": "cus_test", "p_recovery": 0.6, "payment_method": payment_method,
    }


CASOS = [
    ("insufficient_funds", "pix_automatico", 0, "retry_automatico"),       # ainda cabe tentativa
    ("insufficient_funds", "pix_automatico", 3, "mensagem_pagamento"),     # janela esgotada
    ("authorization_revoked", "pix_automatico", 0, "mensagem_pagamento"),  # não retentável
    ("expired_card", "card", 0, "mensagem_pagamento"),                     # cartão: sem retentativa
]


@pytest.mark.asyncio
@pytest.mark.parametrize("causa, metodo, usadas, estrategia", CASOS)
@pytest.mark.parametrize("anomala", [False, True])
async def test_todo_passo_do_raciocinio_cabe_em_cp1252(causa, metodo, usadas, estrategia, anomala):
    estado = await decide_recovery(_estado(causa, metodo, usadas, anomala))
    assert estado["estrategia"] == estrategia
    assert len(estado["raciocinio"]) == 3
    for passo in estado["raciocinio"]:
        passo.encode("cp1252")                       # UnicodeEncodeError reprova
    assert chr(0x2192) not in "".join(estado["raciocinio"])     # a seta desenhada


def test_o_ramo_da_mensagem_imprime_com_a_saida_em_cp1252(tmp_path):
    """O `print` de verdade, num processo com a saída em cp1252 indo para
    arquivo: é como o backend roda no Windows com `> saida.log`."""
    codigo = (
        "import asyncio\n"
        "from crai.agent.workflow import decide_recovery\n"
        "estado = {'failure_cause': 'authorization_revoked', 'recovery_score': 60, 'eprofit': 100.0,\n"
        "          'is_anomalous': False, 'retry_count': 0, 'amount': 200.0, 'customer_id': 'cus_test',\n"
        "          'p_recovery': 0.6, 'payment_method': 'pix_automatico'}\n"
        "s = asyncio.run(decide_recovery(estado))\n"
        "print('ESTRATEGIA=' + s['estrategia'])\n"
    )
    ambiente = {**os.environ, "PYTHONIOENCODING": "cp1252", "CRAI_RELOGIO": "0"}
    ambiente.pop("PYTHONUTF8", None)
    saida = tmp_path / "saida.log"
    with saida.open("wb") as destino:
        r = subprocess.run([sys.executable, "-c", codigo], cwd=APP, env=ambiente,
                           stdout=destino, stderr=subprocess.PIPE, timeout=300)
    erro = r.stderr.decode("utf-8", errors="replace")
    assert r.returncode == 0, erro[-2000:]
    assert "UnicodeEncodeError" not in erro
    texto = saida.read_bytes().decode("cp1252")
    assert "ESTRATEGIA=mensagem_pagamento" in texto
    assert texto.count("[RACIOCÍNIO]") == 3
    assert "(Pix Automático -> boleto)" in texto

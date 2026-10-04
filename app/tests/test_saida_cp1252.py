"""tests/test_saida_cp1252.py — o serviço não cai por causa do que ele imprime.

O DEFEITO (Rodada 2, ajustes). Com a saída do backend redirecionada para arquivo
no Windows, o Python a grava em cp1252. Um `print` com um caractere fora dessa
tabela levanta `UnicodeEncodeError` NO MEIO do que estava sendo feito:

  - em `agent/workflow.py` (a seta do raciocínio), o webhook respondia 500
    (coberto por `test_raciocinio_cp1252.py`);
  - em `dunning/dunning_engine.py` (`[DUNNING] CANAL -> cliente: mensagem`), o
    envio levantava e A MENSAGEM NÃO SAÍA. É o que este arquivo mede, com o
    `print` de verdade num processo com a saída em cp1252.

Os literais do pacote estão todos em ASCII ou cp1252 (a catraca N-12, em
`test_encoding_saida.py`, exige zero em `app/crai/`). O `print` do envio leva
também um trecho da MENSAGEM, que vem do LLM e pode trazer emoji: esse trecho
passa por `_cabe_na_saida`, e o log nunca derruba o envio.
"""

import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from crai.dunning import dunning_engine
from crai.dunning.pix_automatico_retry import (
    PixAutomaticoRetryPolicy, PixRetryPolicyViolation, TentativaAgendada,
)

APP = Path(__file__).resolve().parents[1]
SETA = chr(0x2192)
FOGUETE = chr(0x1F680)


class _Saida:
    def __init__(self, encoding):
        self.encoding = encoding


class TestCabeNaSaida:

    @pytest.mark.parametrize("codificacao", ["cp1252", "ascii", "latin-1"])
    def test_o_que_a_saida_nao_tem_vira_interrogacao_e_o_resto_fica(self, monkeypatch, codificacao):
        monkeypatch.setattr(dunning_engine.sys, "stdout", _Saida(codificacao))
        texto = dunning_engine._cabe_na_saida(f"Oi {FOGUETE} pague {SETA} aqui")
        texto.encode(codificacao)                    # UnicodeEncodeError reprova
        assert FOGUETE not in texto and SETA not in texto
        assert texto.startswith("Oi ? pague ? aqui")

    def test_em_cp1252_os_acentos_do_portugues_ficam_como_estao(self, monkeypatch):
        monkeypatch.setattr(dunning_engine.sys, "stdout", _Saida("cp1252"))
        frase = "Olá, João! A cobrança de R$ 1.290,00 não foi concluída."
        assert dunning_engine._cabe_na_saida(frase) == frase

    def test_em_utf8_nada_muda(self, monkeypatch):
        monkeypatch.setattr(dunning_engine.sys, "stdout", _Saida("utf-8"))
        frase = f"Olá {FOGUETE} {SETA}"
        assert dunning_engine._cabe_na_saida(frase) == frase

    @pytest.mark.parametrize("codificacao", [None, "", "codificacao-que-nao-existe"])
    def test_saida_sem_codificacao_ou_com_uma_desconhecida_nao_levanta(self, monkeypatch, codificacao):
        monkeypatch.setattr(dunning_engine.sys, "stdout", _Saida(codificacao))
        assert dunning_engine._cabe_na_saida(f"a{FOGUETE}b")[0] == "a"


class TestOEnvioDaMensagem:

    def _rodar(self, tmp_path, codificacao):
        """`_send_message` num processo à parte, com a saída indo para arquivo
        na codificação dada. A mensagem traz acento, seta e emoji."""
        codigo = (
            "import asyncio\n"
            "from crai.dunning.dunning_engine import DunningEngine\n"
            "estado = {'channel': 'whatsapp', 'customer_id': 'RN_teste',\n"
            "          'message': 'Ol\\u00e1! Regularize aqui \\u2192 link \\U0001F680 obrigado'}\n"
            "s = asyncio.run(DunningEngine()._send_message(estado))\n"
            "print('SENT=' + str(s['sent']))\n"
        )
        ambiente = {**os.environ, "PYTHONIOENCODING": codificacao, "CRAI_RELOGIO": "0"}
        ambiente.pop("PYTHONUTF8", None)
        saida = tmp_path / "saida.log"
        with saida.open("wb") as destino:
            r = subprocess.run([sys.executable, "-c", codigo], cwd=APP, env=ambiente,
                               stdout=destino, stderr=subprocess.PIPE, timeout=300)
        return r, saida.read_bytes().decode(codificacao)

    def test_com_a_saida_em_cp1252_a_mensagem_sai(self, tmp_path):
        r, texto = self._rodar(tmp_path, "cp1252")
        erro = r.stderr.decode("utf-8", errors="replace")
        assert r.returncode == 0, erro[-2000:]
        assert "UnicodeEncodeError" not in erro
        assert "SENT=True" in texto
        # A linha do log: seta em ASCII, acento preservado, o que não cabe vira "?".
        assert "[DUNNING] WHATSAPP -> RN_teste: Olá! Regularize aqui ? link ? obrigado" in texto

    def test_com_a_saida_em_utf8_o_log_traz_a_mensagem_como_ela_e(self, tmp_path):
        r, texto = self._rodar(tmp_path, "utf-8")
        assert r.returncode == 0, r.stderr.decode("utf-8", errors="replace")[-2000:]
        assert f"[DUNNING] WHATSAPP -> RN_teste: Olá! Regularize aqui {SETA} link {FOGUETE} obrigado" in texto


class TestRegraDoBacen:

    def test_a_mensagem_do_intervalo_curto_cabe_em_cp1252(self):
        """Só o TEXTO do erro mudou (a seta virou "->"); a regra é a mesma:
        duas tentativas a menos de 1 dia de distância são recusadas."""
        inicio = datetime(2026, 10, 5, 9, 0)
        tentativas = [
            TentativaAgendada(numero=1, quando=inicio + timedelta(hours=1), valor=100.0, origem="fallback"),
            TentativaAgendada(numero=2, quando=inicio + timedelta(hours=5), valor=100.0, origem="fallback"),
        ]
        with pytest.raises(PixRetryPolicyViolation) as e:
            PixAutomaticoRetryPolicy._validar(tentativas, 100.0, inicio, inicio + timedelta(days=7), 0)
        mensagem = str(e.value)
        mensagem.encode("cp1252")                    # UnicodeEncodeError reprova
        assert "a menos de 1 dia de intervalo (05/10 10:00 -> 05/10 14:00)" in mensagem
        assert SETA not in mensagem

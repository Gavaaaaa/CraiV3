"""tests/test_etapa1_bloco4.py — Etapa 1, Bloco 4: reconciliação e o custo da suíte.

  - B4-a: ciclo `recuperado` cuja linha do `recovery_log` continuou aberta
    (falha injetada entre a transação do ciclo e o fechamento da linha) → a
    varredura do agendador fecha a linha, com WARNING;
  - B4-b: limpar ou contar num banco que não existe não CRIA o banco — é o
    que devolveu à suíte o tempo perdido no Bloco 1.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import recovery_log
from crai.dunning import retry_scheduler as sched

VALOR = 299.90
SECRET = b"s3cr3t_etapa1_b4"


def _assinar(corpo: bytes) -> dict:
    ts = int(time.time())
    mac = hmac.new(SECRET, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json"}


def _corpo(rec: str, e2e: str, evento: str) -> bytes:
    return json.dumps({"event": evento, "e2e_id": e2e, "valor": VALOR,
                       "id_recorrencia": rec, "codigo_falha": "AM04"}).encode()


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", SECRET.decode())
    monkeypatch.setenv("ENV", "development")
    with TestClient(app_module.app) as c:
        yield c


def _linha(tenant: str, e2e: str) -> dict:
    return [l for l in recovery_log.linhas(tenant) if l["e2e_id"] == e2e][0]


class TestB4aReconciliacao:

    def test_falha_entre_o_ciclo_e_a_linha_e_reconciliada_pela_varredura(
            self, cliente, monkeypatch, caplog):
        rec = "RN_b4a"
        falha = _corpo(rec, f"E_{rec}_0", "automatic_pix.charge_failed")
        cliente.post("/webhooks/pix-automatico", content=falha, headers=_assinar(falha))
        assert _linha("default_tenant", f"E_{rec}_0")["recovered"] == 0

        # A falha injetada: a transação do ciclo passa, o fechamento da linha não.
        original = recovery_log.registrar_recuperacao

        def nao_grava(*a, **k):
            return False
        monkeypatch.setattr(recovery_log, "registrar_recuperacao", nao_grava)
        pago = _corpo(rec, f"E_{rec}_pago", "automatic_pix.charge_paid")
        r = cliente.post("/webhooks/pix-automatico", content=pago, headers=_assinar(pago)).json()
        assert r["ciclo"] == "recuperado" and r["fee"] > 0
        ciclo = cc.ciclos_do_mandato(None, rec)[0]
        assert ciclo["estado"] == cc.RECUPERADO
        # `update_roi_dashboard` regravou a linha com `recovered = MAX(...)`, mas
        # sem desfecho e sem fee: é o rastro da falha, e é o que a reconciliação procura.
        linha = _linha("default_tenant", f"E_{rec}_0")
        assert linha["desfecho_em"] is None and linha["success_fee"] == 0, "o teste não injetou a falha"

        monkeypatch.setattr(recovery_log, "registrar_recuperacao", original)
        with caplog.at_level(logging.WARNING):
            resultado = sched.varrer_ciclos(datetime.now())
        assert resultado["reconciliados"] == [ciclo["id"]]
        assert [m for m in caplog.messages if "RECONCILIAÇÃO (B4-a)" in m]
        linha = _linha("default_tenant", f"E_{rec}_0")
        assert linha["recovered"] == 1 and linha["success_fee"] == r["fee"] and linha["desfecho_em"]
        assert linha["tentativas_usadas"] == 0

        # Idempotente: a passagem seguinte não encontra nada aberto.
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            assert sched.varrer_ciclos(datetime.now())["reconciliados"] == []
        assert not [m for m in caplog.messages if "RECONCILIAÇÃO" in m]

    def test_ciclo_recuperado_com_linha_fechada_nao_e_tocado(self, cliente, caplog):
        rec = "RN_b4a_ok"
        falha = _corpo(rec, f"E_{rec}_0", "automatic_pix.charge_failed")
        cliente.post("/webhooks/pix-automatico", content=falha, headers=_assinar(falha))
        pago = _corpo(rec, f"E_{rec}_pago", "automatic_pix.charge_paid")
        cliente.post("/webhooks/pix-automatico", content=pago, headers=_assinar(pago))
        with caplog.at_level(logging.WARNING):
            assert sched.varrer_ciclos(datetime.now())["reconciliados"] == []
        assert not [m for m in caplog.messages if "RECONCILIAÇÃO" in m]

    def test_horizonte_da_reconciliacao(self):
        """Só recuperados recentes (7 + 30 dias) entram na varredura."""
        agora = datetime(2026, 9, 28, 12, 0)
        velho = cc.abrir_ciclo("t", "RN_b4a_velho", VALOR, "insufficient_funds",
                               agora - timedelta(days=60), e2e_falha_original="E_velho")
        cc.transicionar(velho["id"], cc.RECUPERADO, agora - timedelta(days=50))
        novo = cc.abrir_ciclo("t", "RN_b4a_novo", VALOR, "insufficient_funds",
                              agora - timedelta(days=5), e2e_falha_original="E_novo")
        cc.transicionar(novo["id"], cc.RECUPERADO, agora - timedelta(days=2))
        limite = agora - timedelta(days=cc.JANELA_DIAS + cc.PRAZO_RECUPERACAO_DIAS)
        assert [c["id"] for c in cc.ciclos_recuperados_desde(limite)] == [novo["id"]]


class TestB4bLimparNaoCriaBanco:

    def test_limpar_e_contar_num_banco_inexistente_nao_o_criam(self, tmp_path, monkeypatch):
        banco = tmp_path / "nunca_criado.db"
        monkeypatch.setenv("CRAI_RECOVERY_DB", str(banco))
        cc.esquecer_schema_garantido()
        cc.limpar_tudo()
        cc.limpar_eventos()
        cc.limpar_eventos("pix_falha")
        assert cc.contar_eventos("pix_falha") == 0
        from crai.api.idempotencia import limpar_tudo as limpar_idempotencia
        limpar_idempotencia()
        assert not banco.exists(), "limpar criou o banco só para truncar tabelas vazias"

    def test_num_banco_existente_limpar_continua_limpando(self, tmp_path, monkeypatch):
        banco = tmp_path / "existe.db"
        monkeypatch.setenv("CRAI_RECOVERY_DB", str(banco))
        cc.esquecer_schema_garantido()
        cc.abrir_ciclo("t", "RN_b4b", VALOR, "insufficient_funds", datetime.now(),
                       e2e_falha_original="E_b4b")
        assert cc.registrar_evento_se_novo("pix_falha", "k", datetime.now(), timedelta(days=7))
        cc.limpar_tudo()
        assert cc.ciclos_do_mandato("t", "RN_b4b") == [] and cc.contar_eventos("pix_falha") == 0

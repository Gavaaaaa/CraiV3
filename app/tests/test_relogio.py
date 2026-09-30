"""tests/test_relogio.py — Etapa 2, Bloco 1: o relógio do serviço.

O QUE ESTE ARQUIVO MEDE (Portão 1):
  - o ciclo anda sozinho do começo ao fim com o relógio acelerado: uma falha
    pelo webhook e, sem nenhuma chamada do teste ao agendador, as tentativas
    2 e 3 saem nas datas do plano, cada uma vira `sem_retorno`, a mensagem
    sai depois da 3ª e o ciclo vai a `perdido` 30 dias depois;
  - a suíte roda com o relógio desligado (a fixture `relogio_desligado` do
    conftest), e o `/health` diz isso;
  - o relógio não liga com mais de um worker, e diz por quê;
  - uma passagem que levanta não mata o relógio.

O relógio "acelerado" é o relógio de verdade — a tarefa de fundo criada no
`lifespan`, com o laço e o `asyncio.sleep` reais — com duas trocas: o intervalo
vai para 10 ms (`CRAI_RELOGIO_INTERVALO_S`) e o instante de cada passagem
(`relogio._agora`) anda 6 horas por volta.
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as workflow_module
from crai.api import app as app_module
from crai.api import relogio
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import dunning_engine
from crai.dunning import pix_automatico_retry as pix_retry_module
from crai.dunning import retry_scheduler

SECRET = b"s3cr3t_relogio"
ABERTURA = datetime(2026, 9, 3, 9, 0)
PASSO = timedelta(hours=6)
ESPERA_MAXIMA_S = 60.0


def _assinar(corpo: bytes) -> dict:
    ts = int(time.time())
    mac = hmac.new(SECRET, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json"}


def _falha(rec: str, e2e: str) -> bytes:
    return json.dumps({"event": "automatic_pix.charge_failed", "e2e_id": e2e, "valor": 299.90,
                       "id_recorrencia": rec, "codigo_falha": "AM04"}).encode()


def _esperar(condicao, descricao: str):
    """Espera real (o relógio corre em outra thread, a do TestClient)."""
    limite = time.monotonic() + ESPERA_MAXIMA_S
    while time.monotonic() < limite:
        valor = condicao()
        if valor:
            return valor
        time.sleep(0.02)
    pytest.fail(f"o relógio não chegou a: {descricao} em {ESPERA_MAXIMA_S} s")


@pytest.fixture
def sem_llm(monkeypatch):
    """A mensagem sai do template: nenhum teste daqui fala com a Claude API."""
    async def recusa(*a, **k):
        raise RuntimeError("LLM desligado no teste do relógio")
    monkeypatch.setattr(dunning_engine.claude.messages, "create", recusa)


@pytest.fixture
def relogio_acelerado(monkeypatch):
    """Liga o relógio com 10 ms de intervalo e um `agora` controlado pelo teste.

    Parado em ABERTURA até `soltar()`; depois, +6 h por passagem. O grafo e a
    política ficam congelados em ABERTURA (a abertura do ciclo é o instante do
    webhook), como nos testes da Etapa 1.
    """
    estado = {"agora": ABERTURA, "andando": False}

    def agora():
        atual = estado["agora"]
        if estado["andando"]:
            estado["agora"] = atual + PASSO
        return atual

    class Congelado(datetime):
        @classmethod
        def now(cls, tz=None):
            return ABERTURA if tz is None else ABERTURA.replace(tzinfo=tz)

    monkeypatch.setenv("CRAI_RELOGIO", "1")
    monkeypatch.setenv("CRAI_RELOGIO_INTERVALO_S", "0.01")
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setattr(relogio, "_agora", agora)
    monkeypatch.setattr(workflow_module, "datetime", Congelado)
    monkeypatch.setattr(pix_retry_module, "datetime", Congelado)
    monkeypatch.setattr(app_module, "datetime", Congelado)
    # Fallback uniforme: datas do plano sem depender do dia que o modelo prevê.
    monkeypatch.setattr(workflow_module._pix_retry, "confianca_minima", 2.0)
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", SECRET.decode())

    def soltar():
        estado["andando"] = True
    return soltar


class TestCicloAndaSozinho:

    def test_falha_pelo_webhook_e_o_relogio_leva_o_ciclo_ate_perdido(
            self, relogio_acelerado, sem_llm, monkeypatch):
        chamadas_ao_agendador = []
        original = retry_scheduler.processar_tentativas_devidas

        async def espiao(agora=None):
            chamadas_ao_agendador.append(agora)
            return await original(agora)
        monkeypatch.setattr(retry_scheduler, "processar_tentativas_devidas", espiao)

        rec = "RN_relogio_a"
        with TestClient(app_module.app) as cliente:
            assert cliente.get("/health").json()["relogio"]["ligado"] is True
            corpo = _falha(rec, f"E_{rec}_0")
            r = cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
            assert r.status_code == 200 and r.json()["pipeline"] is True

            ciclo = cc.ciclos_do_mandato(None, rec)[0]
            plano = cc.tentativas_do_ciclo(ciclo["id"])
            assert [t["numero"] for t in plano] == [1, 2, 3]
            assert not any(t["disparada_em"] for t in plano), (
                "a tentativa 1 já saiu no webhook — o teste não mediria o relógio")

            relogio_acelerado()
            final = _esperar(lambda: (lambda c: c if c["estado"] == cc.PERDIDO else None)(
                cc.ciclo_por_id(ciclo["id"])), "ciclo perdido")
            saude = cliente.get("/health").json()["relogio"]

        tentativas = cc.tentativas_do_ciclo(ciclo["id"])
        # As três saíram, cada uma na data do plano (na primeira passagem em que
        # era devida), e nenhuma teve resposta do PSP: viraram `sem_retorno`.
        assert [t["resultado"] for t in tentativas] == [cc.SEM_RETORNO] * 3
        for t in tentativas:
            agendada = datetime.fromisoformat(t["agendada_para"])
            disparada = datetime.fromisoformat(t["disparada_em"])
            assert agendada <= disparada < agendada + PASSO, (t["numero"], agendada, disparada)
            assert datetime.fromisoformat(t["resultado_em"]) >= disparada + cc.PRAZO_SEM_RETORNO
        # A mensagem depois da 3ª (R1), confirmada, e o perdido 30 dias depois dela.
        terceira = datetime.fromisoformat(tentativas[2]["resultado_em"])
        confirmada = datetime.fromisoformat(final["mensagem_confirmada_em"])
        assert confirmada >= terceira
        perdido_em = datetime.fromisoformat(final["perdido_em"])
        assert perdido_em > confirmada + timedelta(days=cc.PRAZO_RECUPERACAO_DIAS)
        # Quem chamou o agendador foi o relógio, e só ele.
        assert chamadas_ao_agendador and all(isinstance(a, datetime) for a in chamadas_ao_agendador)
        assert saude["passagens"] > 0
        assert saude["ultima_passagem_ok"] is True and saude["ultima_falha"] is None


class TestDesligadoNaSuite:

    def test_a_suite_roda_com_o_relogio_desligado(self):
        with TestClient(app_module.app) as cliente:
            saude = cliente.get("/health").json()
        assert saude["status"] == "ok"
        assert saude["relogio"]["ligado"] is False
        assert saude["relogio"]["motivo_desligado"] == relogio.MOTIVO_DESLIGADO_POR_ENV
        assert saude["relogio"]["passagens"] == 0

    def test_desligado_nenhuma_passagem_acontece(self, monkeypatch):
        chamadas = []

        async def espiao(agora):
            chamadas.append(agora)
            return {"disparos": []}
        monkeypatch.setattr(relogio, "passagem", espiao)
        with TestClient(app_module.app):
            time.sleep(0.2)
        assert chamadas == []


class TestUmWorkerSo:

    @pytest.mark.parametrize("env, argv, esperado", [
        ({"WEB_CONCURRENCY": "2"}, ["uvicorn"], "WEB_CONCURRENCY=2"),
        ({}, ["uvicorn", "crai.api.app:app", "--workers", "4"], "--workers 4"),
        ({}, ["uvicorn", "crai.api.app:app", "--workers=3"], "--workers 3"),
        ({"SERVER_SOFTWARE": "gunicorn/22.0"}, ["x"], "gunicorn"),
        ({}, ["/usr/bin/gunicorn", "crai.api.app:app"], "gunicorn"),
    ])
    def test_sinais_de_mais_de_um_worker(self, env, argv, esperado):
        assert esperado in (relogio.workers_detectados(env=env, argv=argv) or "")

    @pytest.mark.parametrize("env, argv", [
        ({}, ["uvicorn", "crai.api.app:app"]),
        ({"WEB_CONCURRENCY": "1"}, ["uvicorn"]),
        ({}, ["uvicorn", "crai.api.app:app", "--workers", "1"]),
        ({"WEB_CONCURRENCY": "abc"}, ["uvicorn"]),
    ])
    def test_um_worker_nao_e_sinal(self, env, argv):
        assert relogio.workers_detectados(env=env, argv=argv) is None

    def test_o_relogio_nao_liga_com_mais_de_um_worker(self, monkeypatch, caplog):
        chamadas = []

        async def espiao(agora):
            chamadas.append(agora)
            return {"disparos": []}
        monkeypatch.setattr(relogio, "passagem", espiao)
        monkeypatch.setenv("CRAI_RELOGIO", "1")
        monkeypatch.setenv("CRAI_RELOGIO_INTERVALO_S", "0.01")
        monkeypatch.setenv("WEB_CONCURRENCY", "2")
        with caplog.at_level("ERROR", logger="crai.api.relogio"):
            with TestClient(app_module.app) as cliente:
                time.sleep(0.2)
                saude = cliente.get("/health").json()["relogio"]
        assert saude["ligado"] is False
        assert saude["motivo_desligado"] == relogio.MOTIVO_MAIS_DE_UM_WORKER
        assert chamadas == []
        assert any("mais de um worker" in r.getMessage() for r in caplog.records)


class TestPassagemQueLevanta:

    def test_uma_passagem_que_levanta_nao_mata_o_relogio(self, monkeypatch):
        chamadas = []

        async def instavel(agora):
            chamadas.append(agora)
            if len(chamadas) == 1:
                raise RuntimeError("banco fora do ar")
            return {"disparos": []}
        monkeypatch.setattr(relogio, "passagem", instavel)
        monkeypatch.setenv("CRAI_RELOGIO", "1")
        monkeypatch.setenv("CRAI_RELOGIO_INTERVALO_S", "0.01")
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
        with TestClient(app_module.app) as cliente:
            _esperar(lambda: len(chamadas) >= 3, "3 passagens")
            saude = cliente.get("/health").json()["relogio"]
        assert saude["ligado"] is True
        assert saude["ultima_passagem_ok"] is True
        assert "banco fora do ar" in saude["ultima_falha"]["erro"]
        # Instantes com fuso no /health (o `agora` do ciclo é local, sem fuso).
        assert datetime.fromisoformat(saude["ultima_passagem_em"]).tzinfo is not None
        assert datetime.fromisoformat(saude["ultima_falha"]["em"]).tzinfo is not None


class TestIntervalo:

    @pytest.mark.parametrize("bruto, esperado", [
        (None, relogio.INTERVALO_PADRAO_S), ("", relogio.INTERVALO_PADRAO_S),
        ("30", 30.0), ("0,5", 0.5), ("abc", relogio.INTERVALO_PADRAO_S),
        ("0", relogio.INTERVALO_PADRAO_S), ("99999", relogio.INTERVALO_PADRAO_S),
    ])
    def test_intervalo_da_env(self, monkeypatch, bruto, esperado):
        if bruto is None:
            monkeypatch.delenv("CRAI_RELOGIO_INTERVALO_S", raising=False)
        else:
            monkeypatch.setenv("CRAI_RELOGIO_INTERVALO_S", bruto)
        assert relogio.intervalo_s() == esperado

    @pytest.mark.parametrize("bruto, ligado", [(None, True), ("1", True), ("0", False),
                                               (" 0 ", False), ("sim", True)])
    def test_ligado_por_env(self, monkeypatch, bruto, ligado):
        if bruto is None:
            monkeypatch.delenv("CRAI_RELOGIO", raising=False)
        else:
            monkeypatch.setenv("CRAI_RELOGIO", bruto)
        assert relogio.ligado_por_env() is ligado

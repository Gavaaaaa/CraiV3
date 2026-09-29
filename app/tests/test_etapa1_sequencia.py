"""tests/test_etapa1_sequencia.py — Etapa 1, Bloco 2: a contagem certa e a janela presa.

O QUE ESTE ARQUIVO MEDE (Portão 2):
  - a sequência com o grafo REAL: falha original → tentativa 1 falha →
    tentativa 2 falha → tentativa 3 falha. Nenhuma mensagem antes da 3ª, e o
    diagnóstico roda UMA vez;
  - reinício com DOIS processos: P1 recebe a falha e dispara a tentativa 1;
    P2 recomeça do zero e recebe a falha da tentativa 1 — não abre janela,
    não reancora prazo, não passa de 3;
  - falha da mesma cobrança depois da janela não gera tentativa nova;
  - cobrança nova do mesmo mandato abre ciclo novo com janela própria;
  - B2-a: banco indisponível — só a dedup cai (segue, com WARNING
    identificável) / as duas caem (503, Retry-After 30, nenhum efeito parcial);
  - B2-b: o fluxo do grafo nunca passa pelo caminho legado de `retry_state`;
  - as varreduras: 24 h sem retorno, janela encerrada, prazo de recuperação.

Os testes de `PixAutomaticoRetryPolicy` não são tocados: a política é a
mesma; o que mudou é quem a chama e com que contador.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as workflow_module
from crai.agent.main_agent import crai_agent
from crai.api import app as app_module
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import pix_automatico_retry as pix_retry_module
from crai.dunning import retry_scheduler as sched
from crai.dunning import retry_state
from crai.dunning.pix_automatico_retry import MAX_TENTATIVAS

VALOR = 299.90
SECRET = b"s3cr3t_etapa1"
ABERTURA = datetime(2026, 9, 3, 9, 0)


def _assinar(corpo: bytes) -> dict:
    ts = int(time.time())
    mac = hmac.new(SECRET, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json"}


def _falha(rec: str, e2e: str, id_cobranca: str | None = None, codigo: str = "AM04") -> bytes:
    corpo = {"event": "automatic_pix.charge_failed", "e2e_id": e2e, "valor": VALOR,
             "id_recorrencia": rec, "codigo_falha": codigo}
    if id_cobranca:
        corpo["id_cobranca"] = id_cobranca
    return json.dumps(corpo).encode()


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", SECRET.decode())
    monkeypatch.setenv("ENV", "development")
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def relogio(monkeypatch):
    """Congela o relógio dos nós do grafo e da política, movível pelo teste.

    Substitui o `datetime` que os dois módulos importaram — o mesmo método dos
    testes da A1-r5 — para "agora" ser o instante que o teste escolhe.
    """
    estado = {"agora": ABERTURA}

    class RelogioCongelado(datetime):
        @classmethod
        def now(cls, tz=None):
            return estado["agora"]

    monkeypatch.setattr(workflow_module, "datetime", RelogioCongelado)
    monkeypatch.setattr(pix_retry_module, "datetime", RelogioCongelado)
    # Fallback uniforme: a primeira tentativa ancora na abertura da janela do
    # recebedor (dia seguinte), sem depender do dia que o modelo prevê.
    monkeypatch.setattr(workflow_module._pix_retry, "confianca_minima", 2.0)

    def mover(para: datetime):
        estado["agora"] = para

    return mover


@pytest.fixture
def espioes(monkeypatch):
    """Conta diagnósticos, chamadas à política e mensagens enviadas."""
    contagem = {"diagnosticos": 0, "politica": [], "mensagens": 0}
    original_predict = workflow_module._classifier.predict
    original_schedule = workflow_module._pix_retry.schedule
    original_campanha = workflow_module._dunning.run_campaign

    def predict(features):
        contagem["diagnosticos"] += 1
        return original_predict(features)

    async def schedule(**kwargs):
        lote = await original_schedule(**kwargs)
        contagem["politica"].append(lote)
        return lote

    async def campanha(*a, **k):
        contagem["mensagens"] += 1
        return await original_campanha(*a, **k)

    monkeypatch.setattr(workflow_module._classifier, "predict", predict)
    monkeypatch.setattr(workflow_module._pix_retry, "schedule", schedule)
    monkeypatch.setattr(workflow_module._dunning, "run_campaign", campanha)
    return contagem


def _agendador(quando: datetime) -> list[dict]:
    return asyncio.run(sched.processar_tentativas_devidas(quando))


def _ciclo(rec: str, tenant=None) -> dict:
    ciclos = cc.ciclos_do_mandato(tenant, rec)
    assert ciclos, f"nenhum ciclo para {rec}"
    return ciclos[0]


# ══════════════════════════════════════════════════════════════════════════
# A sequência com o grafo real
# ══════════════════════════════════════════════════════════════════════════

class TestSequenciaFalhaETresTentativas:

    def test_tres_falhas_de_tentativa_nenhuma_mensagem_e_um_diagnostico(
            self, cliente, relogio, espioes):
        rec = "RN_seq_a"
        r = cliente.post("/webhooks/pix-automatico", content=_falha(rec, "E_seq_a_0"),
                         headers=_assinar(_falha(rec, "E_seq_a_0")))
        assert r.status_code == 200 and r.json()["pipeline"] is True
        ciclo = _ciclo(rec)
        assert ciclo["estado"] == cc.RECOBRANDO and ciclo["estrategia"] == "retry_automatico"
        assert ciclo["causa_original"] == "insufficient_funds"
        assert ciclo["origem"] == cc.ORIGEM_WEBHOOK, "B2-b: o grafo abriu o ciclo, não o plano"
        janela = (ciclo["janela_inicio"], ciclo["janela_fim"])
        assert janela == (ABERTURA.isoformat(), (ABERTURA + timedelta(days=7)).isoformat())
        assert espioes["diagnosticos"] == 1 and len(espioes["politica"]) == 1

        plano = cc.tentativas_do_ciclo(ciclo["id"])
        assert [t["numero"] for t in plano] == [1, 2, 3]
        for numero in (1, 2, 3):
            # O agendador dispara a tentativa `numero` quando a data DELA chega
            # (a política distribui as três pela janela; não é uma por dia).
            quando = datetime.fromisoformat(plano[numero - 1]["agendada_para"]) + timedelta(hours=1)
            relogio(quando)
            disparos = _agendador(quando)
            assert [d["numero"] for d in disparos] == [numero], disparos
            id_cobranca = disparos[0]["id_cobranca"]

            # A falha dessa tentativa chega pelo webhook, com o id da cobrança.
            corpo = _falha(rec, f"E_seq_a_t{numero}", id_cobranca=id_cobranca)
            r = cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
            assert r.status_code == 200 and r.json()["pipeline"] is True

            tentativas = cc.tentativas_do_ciclo(ciclo["id"])
            assert tentativas[numero - 1]["resultado"] == cc.FALHOU
            assert tentativas[numero - 1]["e2e_resultado"] == f"E_seq_a_t{numero}"
            assert tentativas[numero - 1]["codigo_resultado"] == "AM04"
            assert cc.tentativas_executadas(ciclo["id"]) == numero
            # Bloco 3 (R1): a mensagem nasce do resultado da 3ª — nunca antes.
            assert espioes["mensagens"] == (1 if numero == 3 else 0), (
                f"mensagem saiu antes da 3ª (na tentativa {numero})")
            assert espioes["diagnosticos"] == 1, "o diagnóstico rodou de novo"
            assert len(espioes["politica"]) == 1, "a política foi chamada de novo"

        depois = _ciclo(rec)
        assert (depois["janela_inicio"], depois["janela_fim"]) == janela, "a janela mudou"
        assert len(cc.ciclos_do_mandato(None, rec)) == 1
        assert len(cc.tentativas_do_ciclo(ciclo["id"])) == MAX_TENTATIVAS
        # O resultado da 3ª concluiu o ciclo: mensagem enviada, uma vez (Bloco 3, R1).
        assert depois["estado"] == cc.MENSAGEM_ENVIADA and espioes["mensagens"] == 1
        estado = crai_agent.get_state({"configurable": {"thread_id": rec}}).values
        assert estado["retry_count"] == 3 and estado["ciclo_evento"] == cc.RESULTADO_DE_TENTATIVA
        assert estado["failure_cause"] == "insufficient_funds", "o checkpoint perdeu o diagnóstico"

    def test_a_falha_de_uma_tentativa_sem_id_cobranca_tambem_e_associada(
            self, cliente, relogio, espioes):
        """D2: PSP que não manda id de cobrança — a falha dentro da janela
        pertence ao ciclo aberto do mandato, e é o resultado da tentativa
        disparada mais recente."""
        rec = "RN_seq_b"
        corpo = _falha(rec, "E_seq_b_0")
        cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
        relogio(ABERTURA + timedelta(days=1, hours=1))
        _agendador(ABERTURA + timedelta(days=1, hours=1))

        corpo = _falha(rec, "E_seq_b_t1")
        cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))

        ciclo = _ciclo(rec)
        assert cc.tentativas_do_ciclo(ciclo["id"])[0]["resultado"] == cc.FALHOU
        assert espioes["diagnosticos"] == 1 and len(espioes["politica"]) == 1
        assert len(cc.ciclos_do_mandato(None, rec)) == 1

    def test_falha_da_mesma_cobranca_depois_da_janela_nao_gera_tentativa(
            self, cliente, relogio, espioes):
        rec = "RN_seq_c"
        corpo = _falha(rec, "E_seq_c_0", id_cobranca="inv_c")
        cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
        ciclo = _ciclo(rec)
        antes = cc.tentativas_do_ciclo(ciclo["id"])
        assert len(antes) == 3 and all(t["resultado"] == cc.PENDENTE for t in antes)

        # A mesma cobrança (mesmo id) falha de novo, 10 dias depois: janela encerrada.
        relogio(ABERTURA + timedelta(days=10))
        corpo = _falha(rec, "E_seq_c_tarde", id_cobranca="inv_c")
        r = cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
        assert r.status_code == 200

        depois = cc.tentativas_do_ciclo(ciclo["id"])
        assert len(depois) == 3, "tentativa nova gerada depois da janela"
        assert {t["resultado"] for t in depois} == {cc.CANCELADA}
        assert {t["motivo_cancelamento"] for t in depois} == {"janela_encerrada"}
        ciclo_depois = _ciclo(rec)
        assert ciclo_depois["janela_fim"] == ciclo["janela_fim"], "a janela foi reancorada"
        assert len(cc.ciclos_do_mandato(None, rec)) == 1
        assert espioes["diagnosticos"] == 1 and len(espioes["politica"]) == 1
        # Bloco 3 (D7): janela encerrada com pendentes canceladas → mensagem na hora.
        assert ciclo_depois["estado"] == cc.MENSAGEM_ENVIADA and espioes["mensagens"] == 1

    def test_cobranca_nova_do_mesmo_mandato_abre_ciclo_novo_com_janela_propria(
            self, cliente, relogio, espioes):
        rec = "RN_seq_d"
        corpo = _falha(rec, "E_seq_d_set", id_cobranca="inv_set")
        cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))

        # O mês seguinte: outra cobrança do mesmo mandato, com id próprio.
        outubro = ABERTURA + timedelta(days=30)
        relogio(outubro)
        corpo = _falha(rec, "E_seq_d_out", id_cobranca="inv_out")
        cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))

        ciclos = cc.ciclos_do_mandato(None, rec)
        assert len(ciclos) == 2
        novo, antigo = ciclos
        assert (novo["id_cobranca_original"], antigo["id_cobranca_original"]) == ("inv_out", "inv_set")
        assert novo["janela_inicio"] == outubro.isoformat()
        assert novo["janela_fim"] == (outubro + timedelta(days=7)).isoformat()
        assert antigo["janela_fim"] == (ABERTURA + timedelta(days=7)).isoformat()
        assert espioes["diagnosticos"] == 2 and len(espioes["politica"]) == 2
        assert [t.numero for t in espioes["politica"][1]] == [1, 2, 3]

    def test_cobranca_nova_com_id_diferente_dentro_da_janela_tambem_e_nova(
            self, cliente, relogio, espioes):
        """Com `id_cobranca`, a distinção não depende da janela: o PSP disse
        que é outra cobrança (uma semanal, por exemplo)."""
        rec = "RN_seq_e"
        for indice, id_cobranca in enumerate(("inv_sem1", "inv_sem2")):
            relogio(ABERTURA + timedelta(days=3 * indice))
            corpo = _falha(rec, f"E_seq_e_{indice}", id_cobranca=id_cobranca)
            cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
        assert len(cc.ciclos_do_mandato(None, rec)) == 2
        assert espioes["diagnosticos"] == 2


# ══════════════════════════════════════════════════════════════════════════
# Reinício com dois processos
# ══════════════════════════════════════════════════════════════════════════

_SCRIPT = r'''
import asyncio, json, sys, hmac, hashlib, time
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from crai.api import app as app_module
from crai.agent import workflow as workflow_module
from crai.dunning import pix_automatico_retry as pix_retry_module
from crai.dunning import retry_scheduler as sched
from crai.dunning import ciclo_cobranca as cc

SECRET = b"%(secret)s"
acoes = json.loads(sys.argv[1])
relogio = {"agora": datetime.fromisoformat(acoes[0]["agora"])}

class Congelado(datetime):
    @classmethod
    def now(cls, tz=None):
        return relogio["agora"]
workflow_module.datetime = Congelado
pix_retry_module.datetime = Congelado
workflow_module._pix_retry.confianca_minima = 2.0

diagnosticos = {"n": 0}
_pred = workflow_module._classifier.predict
def predict(f):
    diagnosticos["n"] += 1
    return _pred(f)
workflow_module._classifier.predict = predict

def assinar(corpo):
    t = int(time.time())
    return {"x-pix-signature": "t=%%d,v1=%%s" %% (t, hmac.new(SECRET, ("%%d." %% t).encode() + corpo, hashlib.sha256).hexdigest()),
            "content-type": "application/json"}

saida = []
with TestClient(app_module.app) as c:
    for acao in acoes:
        relogio["agora"] = datetime.fromisoformat(acao["agora"])
        if acao["op"] == "webhook":
            corpo = json.dumps(acao["corpo"]).encode()
            r = c.post("/webhooks/pix-automatico", content=corpo, headers=assinar(corpo))
            saida.append({"op": "webhook", "status": r.status_code, "json": r.json()})
        elif acao["op"] == "agendador":
            d = asyncio.run(sched.processar_tentativas_devidas(relogio["agora"]))
            saida.append({"op": "agendador", "disparos": [{"numero": x["numero"], "id_cobranca": x["id_cobranca"]} for x in d]})
ciclos = cc.ciclos_do_mandato(None, acoes[0]["rec"])
saida.append({"op": "dump", "diagnosticos": diagnosticos["n"],
              "ciclos": [{"id": x["id"], "estado": x["estado"], "janela_inicio": x["janela_inicio"],
                          "janela_fim": x["janela_fim"], "origem": x["origem"],
                          "tentativas": [(t["numero"], t["resultado"], bool(t["disparada_em"]), t["id_cobranca"])
                                         for t in cc.tentativas_do_ciclo(x["id"])]} for x in ciclos]})
print("RESULTADO " + json.dumps(saida))
''' % {"secret": SECRET.decode()}


def _processo(env: dict, acoes: list[dict]) -> list[dict]:
    saida = subprocess.run([sys.executable, "-c", _SCRIPT, json.dumps(acoes)], env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300,
                           cwd=str(Path(__file__).resolve().parent.parent))
    assert saida.returncode == 0, saida.stderr[-4000:]
    linha = [l for l in saida.stdout.splitlines() if l.startswith("RESULTADO ")]
    assert linha, saida.stdout[-3000:]
    return json.loads(linha[0][len("RESULTADO "):])


class TestReinicioComDoisProcessos:

    def test_p2_nao_abre_janela_nem_reancora_nem_passa_de_tres(self, tmp_path):
        """O roteiro do diagnóstico (item 10), agora com o resultado certo.

        P1: falha original, agendamento, tentativa 1 disparada. P2: processo
        novo, recebe a falha da tentativa 1 — e depois dispara a 2 e a 3.
        """
        env = {**os.environ,
               "PIX_WEBHOOK_SECRET": SECRET.decode(), "ENV": "development",
               "CRAI_RECOVERY_DB": str(tmp_path / "recuperacoes.db"),
               "CRAI_RETENTION_DB": str(tmp_path / "retencao.db"),
               "CRAI_RETRY_STATE": str(tmp_path / "planos.json"),
               "CRAI_CLIENTES_DB": str(tmp_path / "clientes.db"),
               "PYTHONIOENCODING": "utf-8"}
        env.pop("SUPABASE_DB_URL", None)
        env.pop("CRAI_SIMULATE_OUTCOMES", None)
        rec = "RN_restart_seq"
        t0 = ABERTURA

        p1 = _processo(env, [
            {"op": "webhook", "rec": rec, "agora": t0.isoformat(),
             "corpo": {"event": "automatic_pix.charge_failed", "e2e_id": "E_r_0",
                       "valor": VALOR, "id_recorrencia": rec, "codigo_falha": "AM04"}},
            {"op": "agendador", "agora": (t0 + timedelta(days=1, hours=1)).isoformat()},
        ])
        assert p1[0]["json"]["pipeline"] is True
        assert [d["numero"] for d in p1[1]["disparos"]] == [1]
        id_t1 = p1[1]["disparos"][0]["id_cobranca"]
        dump1 = p1[-1]
        assert dump1["diagnosticos"] == 1 and len(dump1["ciclos"]) == 1
        janela = (dump1["ciclos"][0]["janela_inicio"], dump1["ciclos"][0]["janela_fim"])

        # === reinício === (o fallback uniforme agenda em t0+1d, t0+4d e t0+7d)
        p2 = _processo(env, [
            {"op": "webhook", "rec": rec, "agora": (t0 + timedelta(days=1, hours=2)).isoformat(),
             "corpo": {"event": "automatic_pix.charge_failed", "e2e_id": "E_r_t1",
                       "valor": VALOR, "id_recorrencia": rec, "codigo_falha": "AM04",
                       "id_cobranca": id_t1}},
            {"op": "agendador", "agora": (t0 + timedelta(days=4, hours=1)).isoformat()},
            {"op": "agendador", "agora": (t0 + timedelta(days=7, hours=1)).isoformat()},
            {"op": "agendador", "agora": (t0 + timedelta(days=9)).isoformat()},
        ])
        assert p2[0]["json"]["pipeline"] is True
        assert [d["numero"] for d in p2[1]["disparos"]] == [2]
        assert [d["numero"] for d in p2[2]["disparos"]] == [3]
        assert p2[3]["disparos"] == [], "mais de 3 tentativas depois do reinício"
        dump2 = p2[-1]
        assert dump2["diagnosticos"] == 0, "P2 rodou o diagnóstico de novo"
        assert len(dump2["ciclos"]) == 1, "P2 abriu uma janela nova"
        ciclo = dump2["ciclos"][0]
        assert (ciclo["janela_inicio"], ciclo["janela_fim"]) == janela, "P2 reancorou o prazo"
        assert ciclo["origem"] == "webhook"
        # A última passagem (dia 9) já varreu: as tentativas 2 e 3 saíram há
        # mais de 24 h sem resultado do PSP e viraram `sem_retorno` (D6) —
        # executadas, não pagas, e distintas de `falhou`.
        assert [(n, r, d) for n, r, d, _ in ciclo["tentativas"]] == [
            (1, "falhou", True), (2, "sem_retorno", True), (3, "sem_retorno", True)]
        assert ciclo["tentativas"][0][3] == id_t1
        # Bloco 3 (R1): o sem_retorno da 3ª, pelo agendador, concluiu o ciclo.
        assert ciclo["estado"] == "mensagem_enviada"


# ══════════════════════════════════════════════════════════════════════════
# B2-a: o banco indisponível no webhook
# ══════════════════════════════════════════════════════════════════════════

class TestB2aBancoIndisponivel:

    def test_so_a_deduplicacao_cai_o_ciclo_grava_e_segue_com_warning(
            self, cliente, monkeypatch, caplog):
        def explode(*a, **k):
            raise sqlite3.OperationalError("disco fora do ar")
        monkeypatch.setattr(cc, "registrar_evento_se_novo", explode)

        rec = "RN_b2a_1"
        corpo = _falha(rec, "E_b2a_1")
        with caplog.at_level(logging.WARNING):
            r = cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))

        assert r.status_code == 200 and r.json()["pipeline"] is True
        assert _ciclo(rec)["estado"] == cc.RECOBRANDO
        avisos = [m for m in caplog.messages if "[IDEMPOTENCIA-FALLBACK]" in m]
        assert avisos, "nenhum WARNING identificável do fallback"
        assert all(rec.levelno == logging.WARNING for rec in caplog.records
                   if "[IDEMPOTENCIA-FALLBACK]" in rec.getMessage())
        # A memória do processo ainda segura o reenvio.
        r2 = cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
        assert r2.json()["motivo"] == "evento_ja_processado"

    def test_as_duas_caem_503_retry_after_e_nenhum_efeito_parcial(self, cliente, monkeypatch):
        # Os originais são guardados e RESTAURADOS à mão, nunca por
        # `monkeypatch.undo()`: o `undo` desfaz também as envs de isolamento
        # das fixtures autouse (`CRAI_RECOVERY_DB`, `CRAI_RETENTION_DB`), e a
        # chamada seguinte rodaria o pipeline real contra os bancos REAIS —
        # aconteceu em 28/09/2026 e está no relatório do Bloco 2.
        originais = (cc.registrar_evento_se_novo, cc.abrir_ciclo)

        def explode(*a, **k):
            raise sqlite3.OperationalError("disco fora do ar")
        monkeypatch.setattr(cc, "registrar_evento_se_novo", explode)
        monkeypatch.setattr(cc, "abrir_ciclo", explode)

        rec = "RN_b2a_2"
        corpo = _falha(rec, "E_b2a_2")
        r = cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))

        assert r.status_code == 503, r.text
        assert r.headers["retry-after"] == "30"
        assert r.json()["motivo"] == "falha_de_gravacao"
        with sqlite3.connect(os.environ["CRAI_RECOVERY_DB"]) as conn:
            assert conn.execute("SELECT COUNT(*) FROM ciclos_cobranca").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM tentativas_cobranca").fetchone()[0] == 0
            assert conn.execute("SELECT COUNT(*) FROM eventos_vistos").fetchone()[0] == 0
        # A trilha do Art. 20 e o ledger: nenhuma linha (a tabela pode nem existir).
        with sqlite3.connect(os.environ["CRAI_RETENTION_DB"]) as conn:
            for tabela in ("decisoes_automatizadas", "ciclos_retencao"):
                existe = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?",
                                      (tabela,)).fetchone()
                if existe:
                    assert conn.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0] == 0

        # O banco volta: o reenvio que o 503 pediu é processado como novo.
        monkeypatch.setattr(cc, "registrar_evento_se_novo", originais[0])
        monkeypatch.setattr(cc, "abrir_ciclo", originais[1])
        assert "de_teste" in os.environ["CRAI_RECOVERY_DB"], "isolamento perdido"
        r3 = cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
        assert r3.status_code == 200 and r3.json()["pipeline"] is True
        assert _ciclo(rec)["estado"] == cc.RECOBRANDO

    def test_dedup_no_banco_e_ciclo_falha_tambem_e_503_e_esquece_a_chave(
            self, cliente, monkeypatch):
        """O caso além do pedido: a dedup gravou, o ciclo não. Sem esquecer a
        chave, o reenvio seria descartado como já processado."""
        original = cc.abrir_ciclo
        monkeypatch.setattr(cc, "abrir_ciclo",
                            lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("x")))
        rec = "RN_b2a_3"
        corpo = _falha(rec, "E_b2a_3")
        r = cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
        assert r.status_code == 503
        assert cc.contar_eventos("pix_falha") == 0

        monkeypatch.setattr(cc, "abrir_ciclo", original)      # nunca `undo()` — ver acima
        assert "de_teste" in os.environ["CRAI_RECOVERY_DB"], "isolamento perdido"
        r2 = cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
        assert r2.json()["pipeline"] is True


# ══════════════════════════════════════════════════════════════════════════
# B2-b: o grafo nunca passa pelo caminho legado
# ══════════════════════════════════════════════════════════════════════════

class TestB2bCaminhoLegadoSoParaLegado:

    def test_o_grafo_abre_o_ciclo_com_a_causa_real_e_origem_webhook(self, cliente, caplog):
        rec = "RN_b2b"
        corpo = _falha(rec, "E_b2b", codigo="AB03")       # processing_error
        with caplog.at_level(logging.WARNING):
            cliente.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
        ciclo = _ciclo(rec)
        assert ciclo["origem"] == cc.ORIGEM_WEBHOOK
        assert ciclo["causa_original"] == "processing_error"
        assert ciclo["codigo_falha_original"] == "AB03"
        assert not [m for m in caplog.messages if "Plano gravado SEM ciclo" in m]

    def test_o_caminho_legado_avisa_em_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            retry_state.save_retry_state("RN_legado", VALOR, [
                {"numero": 1, "quando": ABERTURA, "valor": VALOR, "origem": "teste"}],
                pix_janela_ate=ABERTURA + timedelta(days=7), e2e_id="E_legado")
        ciclo = _ciclo("RN_legado")
        assert ciclo["origem"] == cc.ORIGEM_PLANO and ciclo["causa_original"] == "desconhecida"
        assert [m for m in caplog.messages if "Plano gravado SEM ciclo" in m]

    def test_o_painel_declara_tentativas_e_o_ciclo_nasce_declarado(self, cliente, monkeypatch):
        """`tentativas_usadas` do painel passa a significar EXECUTADAS: com 3,
        o ciclo nasce com 3 `falhou` declaradas e a mensagem sai na hora."""
        monkeypatch.setenv("ENV", "demo")
        r = cliente.post("/simulate/painel/cobranca-falhada",
                         json={"valor": VALOR, "codigo_falha": "AM04", "tentativas_usadas": 3})
        assert r.status_code == 200, r.text
        assert r.json()["estrategia"] == "mensagem_pagamento"
        assert r.json()["plano"] == []
        ciclos = [c for c in _todos_os_ciclos() if c["tenant_id"] == app_module.TENANT_PAINEL]
        assert ciclos and ciclos[0]["origem"] == cc.ORIGEM_DECLARADA
        tentativas = cc.tentativas_do_ciclo(ciclos[0]["id"])
        assert [(t["numero"], t["resultado"], t["origem_data"]) for t in tentativas] == [
            (1, "falhou", "declarada"), (2, "falhou", "declarada"), (3, "falhou", "declarada")]
        assert ciclos[0]["estado"] == cc.MENSAGEM_ENVIADA

        r = cliente.post("/simulate/painel/cobranca-falhada",
                         json={"valor": VALOR, "codigo_falha": "AM04", "tentativas_usadas": 2})
        assert r.json()["estrategia"] == "retry_automatico"
        assert len(r.json()["plano"]) == 1


def _todos_os_ciclos() -> list[dict]:
    with sqlite3.connect(os.environ["CRAI_RECOVERY_DB"]) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(l) for l in conn.execute("SELECT * FROM ciclos_cobranca ORDER BY id DESC")]


# ══════════════════════════════════════════════════════════════════════════
# As varreduras
# ══════════════════════════════════════════════════════════════════════════

class TestVarreduras:

    def _ciclo_com_plano(self, rec="RN_var", origem_janela=ABERTURA) -> dict:
        ciclo = cc.abrir_ciclo("t", rec, VALOR, "insufficient_funds", origem_janela,
                               e2e_falha_original=f"E_{rec}")
        cc.agendar_tentativas(ciclo["id"], [
            {"numero": n, "quando": origem_janela + timedelta(days=n), "valor": VALOR,
             "origem": "fallback_uniforme"} for n in (1, 2, 3)])
        return ciclo

    def test_24h_sem_retorno_vira_sem_retorno_e_conta_como_nao_paga(self):
        ciclo = self._ciclo_com_plano()
        disparo = ABERTURA + timedelta(days=1)
        cc.marcar_disparada(ciclo["id"], 1, "ch_1", disparo)

        assert cc.varrer_sem_retorno(disparo + timedelta(hours=23)) == []
        assert cc.varrer_sem_retorno(disparo + timedelta(hours=24)) == [(ciclo["id"], 1)]
        t1 = cc.tentativas_do_ciclo(ciclo["id"])[0]
        assert t1["resultado"] == cc.SEM_RETORNO and t1["resultado_em"]
        assert cc.tentativas_executadas(ciclo["id"]) == 1
        assert cc.varrer_sem_retorno(disparo + timedelta(days=5)) == [], "marcada duas vezes"

    def test_sem_retorno_da_terceira_deixa_o_ciclo_precisando_de_mensagem(self):
        ciclo = self._ciclo_com_plano("RN_var3")
        for n in (1, 2):
            cc.marcar_disparada(ciclo["id"], n, f"ch_{n}", ABERTURA + timedelta(days=n))
            cc.registrar_resultado(ciclo["id"], n, cc.FALHOU, ABERTURA + timedelta(days=n, hours=2))
        cc.marcar_disparada(ciclo["id"], 3, "ch_3", ABERTURA + timedelta(days=3))
        assert cc.ciclo_precisa_de_mensagem(ciclo["id"], ABERTURA + timedelta(days=3, hours=1)) is False

        resultado = sched.varrer_ciclos(ABERTURA + timedelta(days=4, hours=1))
        assert resultado["sem_retorno"] == [(ciclo["id"], 3)]
        assert resultado["mensagem_devida"] == [ciclo["id"]]
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.RECOBRANDO, "a conclusão é do Bloco 3"

    def test_janela_encerrada_cancela_o_que_nao_saiu(self):
        ciclo = self._ciclo_com_plano("RN_varj")
        cc.marcar_disparada(ciclo["id"], 1, "ch_1", ABERTURA + timedelta(days=1))
        cc.registrar_resultado(ciclo["id"], 1, cc.FALHOU, ABERTURA + timedelta(days=1, hours=1))

        assert cc.varrer_janelas_encerradas(ABERTURA + timedelta(days=7)) == []
        assert cc.varrer_janelas_encerradas(ABERTURA + timedelta(days=7, seconds=1)) == [ciclo["id"]]
        ts = cc.tentativas_do_ciclo(ciclo["id"])
        assert [t["resultado"] for t in ts] == ["falhou", "cancelada", "cancelada"]
        assert ts[1]["motivo_cancelamento"] == "janela_encerrada"
        assert cc.ciclo_precisa_de_mensagem(ciclo["id"], ABERTURA + timedelta(days=8)) is True

    def test_prazo_de_recuperacao_e_separado_da_janela_do_bacen(self):
        """A3: a janela do BACEN fecha no dia 7 e o ciclo NÃO vai a perdido
        por isso; só 30 dias depois da mensagem."""
        ciclo = self._ciclo_com_plano("RN_varp")
        mensagem_em = ABERTURA + timedelta(days=4)
        cc.transicionar(ciclo["id"], cc.MENSAGEM_ENVIADA, mensagem_em)
        # B3-a: o prazo conta da mensagem CONFIRMADA, não da reserva.
        assert cc.confirmar_mensagem(ciclo["id"], mensagem_em) is True

        assert cc.varrer_prazo_de_recuperacao(ABERTURA + timedelta(days=8)) == []
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.MENSAGEM_ENVIADA
        assert cc.varrer_prazo_de_recuperacao(mensagem_em + timedelta(days=30)) == []
        assert cc.varrer_prazo_de_recuperacao(mensagem_em + timedelta(days=30, seconds=1)) == [ciclo["id"]]
        depois = cc.ciclo_por_id(ciclo["id"])
        assert depois["estado"] == cc.PERDIDO and depois["perdido_em"]
        # Dentro do prazo, recuperado continua permitido (o fechamento com fee é do Bloco 3).
        outro = self._ciclo_com_plano("RN_varp2")
        cc.transicionar(outro["id"], cc.MENSAGEM_ENVIADA, mensagem_em)
        assert cc.transicionar(outro["id"], cc.RECUPERADO, mensagem_em + timedelta(days=10))["estado"] == cc.RECUPERADO

    def test_o_agendador_roda_as_varreduras_ao_fim_da_passagem(self, monkeypatch):
        ciclo = self._ciclo_com_plano("RN_varag")
        cc.marcar_disparada(ciclo["id"], 1, "ch_1", ABERTURA + timedelta(days=1))
        chamadas = []
        original = cc.varrer

        def espiao(agora):
            chamadas.append(agora)
            return original(agora)
        monkeypatch.setattr(cc, "varrer", espiao)

        _agendador(ABERTURA + timedelta(days=2, hours=1))
        assert chamadas == [ABERTURA + timedelta(days=2, hours=1)]
        assert cc.tentativas_do_ciclo(ciclo["id"])[0]["resultado"] == cc.SEM_RETORNO

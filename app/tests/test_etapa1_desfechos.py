"""tests/test_etapa1_desfechos.py — Etapa 1, Bloco 3: a mensagem depois da 3ª, e os desfechos.

O QUE ESTE ARQUIVO MEDE (Portão 3):
  - sequência completa com o grafo real: 3 falhas → mensagem → `mensagem_enviada`,
    e a mensagem sai UMA vez (R1);
  - A2: o caminho de mensagem fora do grafo grava na trilha do Art. 20 as
    MESMAS decisões que `trigger_dunning` dentro do grafo — reprova se gravar menos;
  - pagamento na tentativa 2: `recuperado`, fee > 0, tentativa 3 `cancelada`,
    nenhuma mensagem (R4);
  - pagamento depois de um REINÍCIO (dois processos): `recuperado`, fee > 0,
    `recovery_log` atualizado (R4);
  - A3: pagamento no dia 10, com a janela do BACEN fechada e a mensagem enviada
    → recuperado com fee; o mesmo pagamento depois do prazo → ciclo `perdido`
    não reaberto, e o que acontece fica registrado;
  - A4: a linha aberta do `recovery_log` só fecha um ciclo de transição se tem
    até 7 + 30 dias; linha antiga NÃO é fechada, e cada uso é logado;
  - revogação no meio da janela: pendentes canceladas, mensagem na hora com
    boleto — mesmo com score abaixo do corte (R5, D5);
  - score abaixo do corte / e-Profit ≤ 0: ciclo `descartado` com motivo e a
    decisão na trilha (R6); uma falha tardia nele não reabre o diagnóstico;
  - I-4a: falha injetada no `atualizar` de `decide_recovery` → 503 → reenvio →
    diagnóstico retomado sobre o MESMO ciclo, janela original, um ciclo só;
  - as rotas `/simulate/painel/*` e `/simulate/pix-*` continuam funcionando.
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as workflow_module
from crai.agent.main_agent import crai_agent
from crai.api import app as app_module
from crai.churn_voluntary import retention_log as trilha
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import pix_automatico_retry as pix_retry_module
from crai.dunning import recovery_log
from crai.dunning import retry_scheduler as sched
from crai.dunning.pix_automatico_retry import MAX_TENTATIVAS

VALOR = 299.90
SECRET = b"s3cr3t_etapa1_b3"
ABERTURA = datetime(2026, 9, 3, 9, 0)


def _assinar(corpo: bytes) -> dict:
    ts = int(time.time())
    mac = hmac.new(SECRET, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json"}


def _evento(rec: str, e2e: str, evento: str = "automatic_pix.charge_failed",
            id_cobranca: str | None = None, codigo: str = "AM04") -> bytes:
    corpo = {"event": evento, "e2e_id": e2e, "valor": VALOR, "id_recorrencia": rec}
    if evento.endswith("charge_failed"):
        corpo["codigo_falha"] = codigo
    if id_cobranca:
        corpo["id_cobranca"] = id_cobranca
    return json.dumps(corpo).encode()


def _falha(rec, e2e, id_cobranca=None, codigo="AM04"):
    return _evento(rec, e2e, id_cobranca=id_cobranca, codigo=codigo)


def _pago(rec, e2e, id_cobranca=None):
    return _evento(rec, e2e, "automatic_pix.charge_paid", id_cobranca=id_cobranca)


def _revogada(rec, e2e):
    return _evento(rec, e2e, "automatic_pix.authorization_revoked")


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", SECRET.decode())
    monkeypatch.setenv("ENV", "development")
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def relogio(monkeypatch):
    """Congela o relógio dos nós, da política e do handler de confirmação."""
    estado = {"agora": ABERTURA}

    class RelogioCongelado(datetime):
        @classmethod
        def now(cls, tz=None):
            return estado["agora"] if tz is None else estado["agora"].replace(tzinfo=tz)

    monkeypatch.setattr(workflow_module, "datetime", RelogioCongelado)
    monkeypatch.setattr(pix_retry_module, "datetime", RelogioCongelado)
    monkeypatch.setattr(app_module, "datetime", RelogioCongelado)
    monkeypatch.setattr(workflow_module._pix_retry, "confianca_minima", 2.0)

    def mover(para: datetime):
        estado["agora"] = para
    return mover


@pytest.fixture
def espioes(monkeypatch):
    contagem = {"diagnosticos": 0, "politica": [], "mensagens": []}
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

    async def campanha(customer_id, failure_cause, *a, **k):
        r = await original_campanha(customer_id, failure_cause, *a, **k)
        contagem["mensagens"].append({"customer_id": customer_id, "causa": failure_cause,
                                      "metodo": r["payment_method"]})
        return r

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


def _post(cliente, corpo: bytes, **headers):
    return cliente.post("/webhooks/pix-automatico", content=corpo,
                        headers={**_assinar(corpo), **headers})


def _tres_falhas(cliente, relogio, rec: str) -> dict:
    """Falha original + as três tentativas falhando pelo webhook. Devolve o ciclo."""
    r = _post(cliente, _falha(rec, f"E_{rec}_0"))
    assert r.status_code == 200 and r.json()["pipeline"] is True
    ciclo = _ciclo(rec)
    plano = cc.tentativas_do_ciclo(ciclo["id"])
    assert len(plano) == 3
    for numero in (1, 2, 3):
        quando = datetime.fromisoformat(plano[numero - 1]["agendada_para"]) + timedelta(hours=1)
        relogio(quando)
        disparos = _agendador(quando)
        assert [d["numero"] for d in disparos] == [numero], disparos
        r = _post(cliente, _falha(rec, f"E_{rec}_t{numero}", id_cobranca=disparos[0]["id_cobranca"]))
        assert r.status_code == 200
    return cc.ciclo_por_id(ciclo["id"])


def _decisoes(tenant: str, sujeito: str) -> list[dict]:
    return sorted(trilha.decisoes_do_sujeito(tenant, sujeito, limite=100), key=lambda d: d["id"])


def _linha_dataset(tenant: str, e2e: str) -> dict:
    with sqlite3.connect(os.environ["CRAI_RECOVERY_DB"]) as conn:
        conn.row_factory = sqlite3.Row
        l = conn.execute("SELECT * FROM ciclos_recuperacao WHERE tenant_id = ? AND e2e_id = ?",
                         (tenant, e2e)).fetchone()
        assert l is not None, f"linha {tenant}/{e2e} não existe no dataset"
        return dict(l)


# ══════════════════════════════════════════════════════════════════════════
# R1: a mensagem depois da 3ª
# ══════════════════════════════════════════════════════════════════════════

class TestMensagemDepoisDaTerceira:

    def test_tres_falhas_mensagem_uma_vez_e_estado_mensagem_enviada(self, cliente, relogio, espioes):
        rec = "RN_d_a"
        ciclo = _tres_falhas(cliente, relogio, rec)

        assert ciclo["estado"] == cc.MENSAGEM_ENVIADA and ciclo["mensagem_em"]
        assert espioes["mensagens"] == [{"customer_id": rec, "causa": "insufficient_funds",
                                         "metodo": "pix_automatico"}], "a mensagem não saiu uma vez"
        assert espioes["diagnosticos"] == 1 and len(espioes["politica"]) == 1
        assert [t["resultado"] for t in cc.tentativas_do_ciclo(ciclo["id"])] == ["falhou"] * 3
        # O agendador passando de novo não manda segunda mensagem.
        _agendador(ABERTURA + timedelta(days=9))
        assert len(espioes["mensagens"]) == 1
        # A linha do dataset: estratégia da abertura, mensagem enviada, 3 executadas.
        linha = _linha_dataset("default_tenant", f"E_{rec}_0")
        assert linha["tentativas_usadas"] == 3 and linha["recovered"] == 0
        assert linha["custo_total"] >= recovery_log.custo_realizado(3, True)
        # O checkpoint desta execução carrega a mensagem, como no grafo.
        estado = crai_agent.get_state({"configurable": {"thread_id": rec}}).values
        assert estado["dunning_sent"] is True and estado["message_sent"]

    def test_sem_retorno_da_terceira_pelo_agendador_tambem_manda_a_mensagem(
            self, cliente, relogio, espioes):
        rec = "RN_d_b"
        _post(cliente, _falha(rec, f"E_{rec}_0"))
        ciclo = _ciclo(rec)
        plano = cc.tentativas_do_ciclo(ciclo["id"])
        for numero in (1, 2, 3):
            quando = datetime.fromisoformat(plano[numero - 1]["agendada_para"]) + timedelta(hours=1)
            relogio(quando)
            _agendador(quando)
        assert espioes["mensagens"] == [], "mensagem antes de haver resultado"
        # 24 h depois da 3ª sem retorno: sem_retorno → mensagem, pelo agendador.
        depois = datetime.fromisoformat(plano[2]["agendada_para"]) + timedelta(days=1, hours=2)
        relogio(depois)
        _agendador(depois)
        assert len(espioes["mensagens"]) == 1
        ciclo = cc.ciclo_por_id(ciclo["id"])
        assert ciclo["estado"] == cc.MENSAGEM_ENVIADA
        assert [t["resultado"] for t in cc.tentativas_do_ciclo(ciclo["id"])] == ["sem_retorno"] * 3


class TestA2ParidadeDaTrilha:

    def _decisoes_da_mensagem(self, tenant, sujeito) -> list[tuple]:
        return [(d["dominio"], d["tipo_decisao"], d["modelo"], d["saida"].get("regra"))
                for d in _decisoes(tenant, sujeito)
                if d["tipo_decisao"] in (trilha.TIPO_OFERTA, trilha.TIPO_CANAL)]

    def test_fora_do_grafo_grava_as_mesmas_decisoes_que_dentro(self, cliente, relogio, espioes):
        # DENTRO do grafo: causa não retentável → mensagem no próprio grafo.
        rec_grafo = "RN_d_par_grafo"
        _post(cliente, _falha(rec_grafo, f"E_{rec_grafo}_0", codigo="AM02"))   # limit_exceeded
        assert _ciclo(rec_grafo)["estado"] == cc.MENSAGEM_ENVIADA
        dentro = self._decisoes_da_mensagem("default_tenant", rec_grafo)

        # FORA do grafo: 3 tentativas falham → conclusão do ciclo.
        rec_fora = "RN_d_par_fora"
        _tres_falhas(cliente, relogio, rec_fora)
        fora = self._decisoes_da_mensagem("default_tenant", rec_fora)

        assert dentro, "o grafo não gravou as decisões da campanha — o teste não mediu nada"
        assert len(fora) >= len(dentro), (
            f"o caminho fora do grafo gravou MENOS decisões da campanha: {fora} vs {dentro}")
        assert fora == dentro, f"as decisões diferem:\n dentro={dentro}\n fora={fora}"
        # E as entradas/saídas têm a mesma forma (mesmas chaves).
        chaves = lambda ds: [(sorted(d["entradas"]), sorted(d["saida"])) for d in ds
                             if d["tipo_decisao"] in (trilha.TIPO_OFERTA, trilha.TIPO_CANAL)]
        assert chaves(_decisoes("default_tenant", rec_fora)) == chaves(_decisoes("default_tenant", rec_grafo))
        assert trilha.verificar_cadeia("default_tenant")["integra"] is True


# ══════════════════════════════════════════════════════════════════════════
# R4: pagamento confirmado
# ══════════════════════════════════════════════════════════════════════════

class TestPagamentoConfirmado:

    def test_pagamento_na_tentativa_2(self, cliente, relogio, espioes):
        rec = "RN_d_p2"
        _post(cliente, _falha(rec, f"E_{rec}_0"))
        ciclo = _ciclo(rec)
        plano = cc.tentativas_do_ciclo(ciclo["id"])
        ids = {}
        for numero in (1, 2):
            quando = datetime.fromisoformat(plano[numero - 1]["agendada_para"]) + timedelta(hours=1)
            relogio(quando)
            disparos = _agendador(quando)
            ids[numero] = disparos[0]["id_cobranca"]
            if numero == 1:
                # A falha da t1 chega no mesmo dia; se chegasse só depois da
                # t2, a varredura de 24 h já a teria marcado `sem_retorno`.
                _post(cliente, _falha(rec, f"E_{rec}_t1", id_cobranca=ids[1]))

        r = _post(cliente, _pago(rec, f"E_{rec}_pago", id_cobranca=ids[2]))
        assert r.status_code == 200
        resposta = r.json()
        assert resposta["ciclo"] == "recuperado" and resposta["fee"] > 0
        assert resposta["tentativa_paga"] == 2

        ciclo = cc.ciclo_por_id(ciclo["id"])
        assert ciclo["estado"] == cc.RECUPERADO and ciclo["fee"] == resposta["fee"] and ciclo["recuperado_em"]
        ts = cc.tentativas_do_ciclo(ciclo["id"])
        assert [t["resultado"] for t in ts] == ["falhou", "paga", "cancelada"]
        assert ts[2]["motivo_cancelamento"] == "recuperado"
        assert espioes["mensagens"] == [], "mensagem para quem pagou"
        linha = _linha_dataset("default_tenant", f"E_{rec}_0")
        assert linha["recovered"] == 1 and linha["success_fee"] > 0 and linha["tentativas_usadas"] == 2
        assert linha["ciclo_id"] == ciclo["id"]
        # O agendador não dispara a 3ª de um ciclo recuperado.
        assert _agendador(ABERTURA + timedelta(days=9)) == []
        # Reenvio da confirmação: reenvio; outra confirmação do mesmo ciclo: já recuperado.
        assert _post(cliente, _pago(rec, f"E_{rec}_pago", id_cobranca=ids[2])).json()["ciclo"] == "reenvio"
        assert _post(cliente, _pago(rec, f"E_{rec}_pago2")).json()["ciclo"] == "ja_recuperado"

    def test_pagamento_sem_id_cobranca_marca_a_disparada_mais_recente(self, cliente, relogio, espioes):
        rec = "RN_d_p_sem_id"
        _post(cliente, _falha(rec, f"E_{rec}_0"))
        ciclo = _ciclo(rec)
        plano = cc.tentativas_do_ciclo(ciclo["id"])
        quando = datetime.fromisoformat(plano[0]["agendada_para"]) + timedelta(hours=1)
        relogio(quando)
        _agendador(quando)
        r = _post(cliente, _pago(rec, f"E_{rec}_pago")).json()
        assert r["ciclo"] == "recuperado" and r["tentativa_paga"] == 1
        assert [t["resultado"] for t in cc.tentativas_do_ciclo(ciclo["id"])] == ["paga", "cancelada", "cancelada"]

    def test_mensalidade_normal_continua_sem_fee(self, cliente):
        r = _post(cliente, _pago("RN_d_normal", "E_normal")).json()
        assert r["ciclo"] == "sem_ciclo_aberto" and r["fee"] == 0.0


class TestA3PrazoDeRecuperacao:

    def test_pagamento_no_dia_10_depois_da_mensagem_e_recuperado_com_fee(self, cliente, relogio, espioes):
        rec = "RN_d_a3"
        ciclo = _tres_falhas(cliente, relogio, rec)
        assert ciclo["estado"] == cc.MENSAGEM_ENVIADA
        dia_10 = ABERTURA + timedelta(days=10)          # janela do BACEN fechou no dia 7
        relogio(dia_10)
        _agendador(dia_10)                               # a varredura não leva a perdido
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.MENSAGEM_ENVIADA

        r = _post(cliente, _pago(rec, f"E_{rec}_pago")).json()
        assert r["ciclo"] == "recuperado" and r["fee"] > 0
        depois = cc.ciclo_por_id(ciclo["id"])
        assert depois["estado"] == cc.RECUPERADO and depois["fee"] > 0
        linha = _linha_dataset("default_tenant", f"E_{rec}_0")
        assert linha["recovered"] == 1 and linha["success_fee"] > 0 and linha["tentativas_usadas"] == 3
        assert linha["custo_total"] == recovery_log.custo_realizado(3, True)

    def test_pagamento_depois_do_prazo_nao_reabre_o_ciclo_perdido(self, cliente, relogio, espioes, caplog):
        rec = "RN_d_a3_tarde"
        ciclo = _tres_falhas(cliente, relogio, rec)
        mensagem_em = datetime.fromisoformat(ciclo["mensagem_em"])
        tarde = mensagem_em + timedelta(days=cc.PRAZO_RECUPERACAO_DIAS, seconds=1)
        relogio(tarde)
        _agendador(tarde)
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.PERDIDO

        with caplog.at_level(logging.WARNING):
            r = _post(cliente, _pago(rec, f"E_{rec}_pago_tarde")).json()
        # O que acontece: registrado como ciclo perdido, fee zero, nada reaberto.
        assert r["ciclo"] == "ciclo_perdido" and r["fee"] == 0.0 and r["ciclo_id"] == ciclo["id"]
        depois = cc.ciclo_por_id(ciclo["id"])
        assert depois["estado"] == cc.PERDIDO and depois["fee"] == 0
        linha = _linha_dataset("default_tenant", f"E_{rec}_0")
        assert linha["recovered"] == 0 and linha["success_fee"] == 0
        assert [m for m in caplog.messages if "NÃO reaberto" in m]
        # Reenvio: reenvio. Não vira "sem ciclo".
        assert _post(cliente, _pago(rec, f"E_{rec}_pago_tarde")).json()["ciclo"] == "reenvio"


class TestA4LinhaDoDatasetSoRecente:

    def _linha_aberta(self, rec: str, ha_dias: int) -> None:
        recovery_log.registrar_ciclo({"tenant_id": "default_tenant", "customer_id": rec,
                                      "invoice_id": f"E_{rec}_antiga", "amount": VALOR,
                                      "failure_cause": "insufficient_funds",
                                      "estrategia": "retry_automatico", "recovery_score": 60})
        registrado = (datetime.now(timezone.utc) - timedelta(days=ha_dias)).isoformat(timespec="seconds")
        with sqlite3.connect(os.environ["CRAI_RECOVERY_DB"]) as conn:
            conn.execute("UPDATE ciclos_recuperacao SET registrado_em = ? WHERE customer_id = ?",
                         (registrado, rec))

    def test_linha_antiga_nao_e_fechada_por_confirmacao_nova(self, cliente, caplog):
        rec = "RN_d_a4_velha"
        self._linha_aberta(rec, ha_dias=cc.JANELA_DIAS + cc.PRAZO_RECUPERACAO_DIAS + 1)
        with caplog.at_level(logging.WARNING):
            r = _post(cliente, _pago(rec, f"E_{rec}_pago")).json()
        assert r["ciclo"] == "sem_ciclo_aberto" and r["fee"] == 0.0
        assert _linha_dataset("default_tenant", f"E_{rec}_antiga")["recovered"] == 0
        assert cc.ciclos_do_mandato(None, rec) == []
        assert not [m for m in caplog.messages if "TRANSIÇÃO (A4)" in m]

    def test_linha_recente_fecha_pelo_caminho_de_transicao_e_loga(self, cliente, caplog):
        rec = "RN_d_a4_nova"
        self._linha_aberta(rec, ha_dias=10)
        with caplog.at_level(logging.WARNING):
            r = _post(cliente, _pago(rec, f"E_{rec}_pago")).json()
        assert r["ciclo"] == "recuperado" and r["fee"] > 0 and r["origem"] == cc.ORIGEM_RECOVERY_LOG
        assert [m for m in caplog.messages if "TRANSIÇÃO (A4)" in m]
        ciclo = _ciclo(rec)
        assert ciclo["estado"] == cc.RECUPERADO and ciclo["origem"] == cc.ORIGEM_RECOVERY_LOG
        assert ciclo["fee"] == r["fee"]
        linha = _linha_dataset("default_tenant", f"E_{rec}_antiga")
        assert linha["recovered"] == 1 and linha["success_fee"] > 0

    def test_no_limite_exato_ainda_fecha(self, cliente):
        rec = "RN_d_a4_limite"
        self._linha_aberta(rec, ha_dias=cc.JANELA_DIAS + cc.PRAZO_RECUPERACAO_DIAS - 1)
        assert _post(cliente, _pago(rec, f"E_{rec}_pago")).json()["ciclo"] == "recuperado"


# O reinício, com dois processos de verdade.
_SCRIPT = r'''
import asyncio, json, sys, hmac, hashlib, time
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from crai.api import app as app_module
from crai.agent import workflow as workflow_module
from crai.dunning import pix_automatico_retry as pix_retry_module
from crai.dunning import retry_scheduler as sched
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import recovery_log

SECRET = b"%(secret)s"
acoes = json.loads(sys.argv[1])
relogio = {"agora": datetime.fromisoformat(acoes[0]["agora"])}

class Congelado(datetime):
    @classmethod
    def now(cls, tz=None):
        return relogio["agora"] if tz is None else relogio["agora"].replace(tzinfo=tz)
workflow_module.datetime = Congelado
pix_retry_module.datetime = Congelado
app_module.datetime = Congelado
workflow_module._pix_retry.confianca_minima = 2.0

mensagens = {"n": 0}
_camp = workflow_module._dunning.run_campaign
async def campanha(*a, **k):
    mensagens["n"] += 1
    return await _camp(*a, **k)
workflow_module._dunning.run_campaign = campanha

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
rec = acoes[0]["rec"]
ciclos = cc.ciclos_do_mandato(None, rec)
linhas = [l for l in recovery_log.linhas(None) if l["customer_id"] == rec]
saida.append({"op": "dump", "mensagens": mensagens["n"],
              "ciclos": [{"id": x["id"], "estado": x["estado"], "fee": x["fee"],
                          "tentativas": [(t["numero"], t["resultado"]) for t in cc.tentativas_do_ciclo(x["id"])]} for x in ciclos],
              "dataset": [{"e2e_id": l["e2e_id"], "recovered": l["recovered"], "success_fee": l["success_fee"],
                           "tentativas_usadas": l["tentativas_usadas"], "ciclo_id": l["ciclo_id"]} for l in linhas]})
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


class TestPagamentoDepoisDeReinicio:

    def test_p2_fecha_o_ciclo_que_p1_abriu(self, tmp_path):
        """O roteiro do diagnóstico (item 10): antes, P2 respondia
        `sem_ciclo_aberto` e fee 0."""
        env = {**os.environ,
               "PIX_WEBHOOK_SECRET": SECRET.decode(), "ENV": "development",
               "CRAI_RECOVERY_DB": str(tmp_path / "recuperacoes.db"),
               "CRAI_RETENTION_DB": str(tmp_path / "retencao.db"),
               "CRAI_RETRY_STATE": str(tmp_path / "planos.json"),
               "CRAI_CLIENTES_DB": str(tmp_path / "clientes.db"),
               "PYTHONIOENCODING": "utf-8"}
        env.pop("SUPABASE_DB_URL", None)
        env.pop("CRAI_SIMULATE_OUTCOMES", None)
        rec = "RN_restart_pago"
        t0 = ABERTURA
        p1 = _processo(env, [
            {"op": "webhook", "rec": rec, "agora": t0.isoformat(),
             "corpo": {"event": "automatic_pix.charge_failed", "e2e_id": "E_rp_0",
                       "valor": VALOR, "id_recorrencia": rec, "codigo_falha": "AM04"}},
            {"op": "agendador", "agora": (t0 + timedelta(days=1, hours=1)).isoformat()},
        ])
        assert [d["numero"] for d in p1[1]["disparos"]] == [1]
        id_t1 = p1[1]["disparos"][0]["id_cobranca"]

        # === reinício ===
        p2 = _processo(env, [
            {"op": "webhook", "rec": rec, "agora": (t0 + timedelta(days=1, hours=3)).isoformat(),
             "corpo": {"event": "automatic_pix.charge_paid", "e2e_id": "E_rp_pago",
                       "valor": VALOR, "id_recorrencia": rec, "id_cobranca": id_t1}},
            {"op": "agendador", "agora": (t0 + timedelta(days=9)).isoformat()},
        ])
        resposta = p2[0]["json"]
        assert resposta["ciclo"] == "recuperado", resposta
        assert resposta["fee"] > 0 and resposta["tentativa_paga"] == 1
        assert p2[1]["disparos"] == [], "o agendador disparou tentativa de ciclo recuperado"
        dump = p2[-1]
        assert dump["mensagens"] == 0
        assert len(dump["ciclos"]) == 1
        ciclo = dump["ciclos"][0]
        assert ciclo["estado"] == "recuperado" and ciclo["fee"] > 0
        assert ciclo["tentativas"] == [[1, "paga"], [2, "cancelada"], [3, "cancelada"]]
        assert dump["dataset"] == [{"e2e_id": "E_rp_0", "recovered": 1, "success_fee": ciclo["fee"],
                                    "tentativas_usadas": 1, "ciclo_id": ciclo["id"]}]


# ══════════════════════════════════════════════════════════════════════════
# R5: autorização revogada
# ══════════════════════════════════════════════════════════════════════════

class TestAutorizacaoRevogada:

    def test_revogacao_no_meio_da_janela_cancela_pendentes_e_manda_boleto(self, cliente, relogio, espioes):
        rec = "RN_d_rev"
        _post(cliente, _falha(rec, f"E_{rec}_0"))
        ciclo = _ciclo(rec)
        plano = cc.tentativas_do_ciclo(ciclo["id"])
        quando = datetime.fromisoformat(plano[0]["agendada_para"]) + timedelta(hours=1)
        relogio(quando)
        _agendador(quando)

        r = _post(cliente, _revogada(rec, f"E_{rec}_rev"))
        assert r.status_code == 200
        resposta = r.json()
        assert resposta["ciclo"] == "mensagem_enviada" and resposta["tentativas_canceladas"] == 2
        assert resposta["metodo_pagamento"] == "boleto"
        assert espioes["mensagens"] == [{"customer_id": rec, "causa": "authorization_revoked",
                                         "metodo": "boleto"}]
        ciclo = cc.ciclo_por_id(ciclo["id"])
        assert ciclo["estado"] == cc.MENSAGEM_ENVIADA
        ts = cc.tentativas_do_ciclo(ciclo["id"])
        assert [t["resultado"] for t in ts] == ["pendente", "cancelada", "cancelada"]
        assert {t["motivo_cancelamento"] for t in ts[1:]} == {"autorizacao_revogada"}
        assert _agendador(ABERTURA + timedelta(days=9)) == []
        # Segunda revogação: nada a fazer, nenhuma segunda mensagem.
        assert _post(cliente, _revogada(rec, f"E_{rec}_rev2")).json()["ciclo"] == "sem_ciclo_aberto"
        assert len(espioes["mensagens"]) == 1
        decisoes = _decisoes("default_tenant", rec)
        assert [d["saida"].get("payment_method") for d in decisoes if d["tipo_decisao"] == trilha.TIPO_OFERTA] == ["boleto"]

    def test_falha_de_tentativa_com_causa_revogada_tambem(self, cliente, relogio, espioes):
        rec = "RN_d_rev_falha"
        _post(cliente, _falha(rec, f"E_{rec}_0"))
        ciclo = _ciclo(rec)
        plano = cc.tentativas_do_ciclo(ciclo["id"])
        quando = datetime.fromisoformat(plano[0]["agendada_para"]) + timedelta(hours=1)
        relogio(quando)
        disparos = _agendador(quando)
        _post(cliente, _falha(rec, f"E_{rec}_t1", id_cobranca=disparos[0]["id_cobranca"], codigo="MD01"))
        assert espioes["mensagens"] == [{"customer_id": rec, "causa": "authorization_revoked",
                                         "metodo": "boleto"}]
        ts = cc.tentativas_do_ciclo(ciclo["id"])
        assert [t["resultado"] for t in ts] == ["falhou", "cancelada", "cancelada"]
        assert ts[0]["codigo_resultado"] == "MD01"
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.MENSAGEM_ENVIADA
        assert espioes["diagnosticos"] == 1

    def test_revogacao_sem_ciclo_aberto_so_registra(self, cliente, espioes):
        r = _post(cliente, _revogada("RN_d_rev_nada", "E_rev_nada")).json()
        assert r["ciclo"] == "sem_ciclo_aberto" and espioes["mensagens"] == []

    def test_d5_revogacao_com_score_abaixo_do_corte_ainda_manda_a_mensagem(self, cliente, monkeypatch, espioes):
        original = workflow_module._classifier.predict

        def score_baixo(features):
            r = dict(original(features))
            r["recovery_score"] = 2
            r["p_recovery"] = 0.02
            return r
        monkeypatch.setattr(workflow_module._classifier, "predict", score_baixo)

        rec = "RN_d_rev_score"
        _post(cliente, _falha(rec, f"E_{rec}_0", codigo="MD01"))
        ciclo = _ciclo(rec)
        assert ciclo["estado"] == cc.MENSAGEM_ENVIADA
        assert [m["metodo"] for m in espioes["mensagens"]] == ["boleto"]
        assert ciclo["motivo_descarte"] is None


# ══════════════════════════════════════════════════════════════════════════
# R6: nenhum descarte silencioso
# ══════════════════════════════════════════════════════════════════════════

class TestDescarteComMotivo:

    def _predict_com(self, monkeypatch, **campos):
        original = workflow_module._classifier.predict

        def predict(features):
            r = dict(original(features))
            r.update(campos)
            return r
        monkeypatch.setattr(workflow_module._classifier, "predict", predict)

    def test_score_abaixo_do_corte_descarta_com_motivo_e_grava_na_trilha(self, cliente, monkeypatch, espioes):
        self._predict_com(monkeypatch, recovery_score=2, p_recovery=0.02)
        rec = "RN_d_score"
        _post(cliente, _falha(rec, f"E_{rec}_0"))
        ciclo = _ciclo(rec)
        assert ciclo["estado"] == cc.DESCARTADO and ciclo["motivo_descarte"] == "score_abaixo_do_corte"
        assert ciclo["descartado_em"] and ciclo["decidido_em"]
        assert cc.tentativas_do_ciclo(ciclo["id"]) == [] and espioes["mensagens"] == []
        decisoes = [d for d in _decisoes("default_tenant", rec) if d["saida"].get("estrategia") == "descartado"]
        assert len(decisoes) == 1
        d = decisoes[0]
        assert (d["dominio"], d["tipo_decisao"], d["modelo"]) == ("involuntario", "retentativa", "regra")
        assert d["saida"]["motivo_descarte"] == "score_abaixo_do_corte"
        assert d["saida"]["regra"] == "route_after_diagnosis" and d["explicacao"]
        assert trilha.verificar_cadeia("default_tenant")["integra"] is True
        # A linha do dataset existe, aberta, com a estratégia de descarte.
        linha = _linha_dataset("default_tenant", f"E_{rec}_0")
        assert linha["estrategia"] == "descartado" and linha["recovered"] == 0

        # Uma falha tardia da mesma cobrança não reabre o diagnóstico nem o ciclo.
        _post(cliente, _falha(rec, f"E_{rec}_0"))          # reenvio: dedup
        r = _post(cliente, _falha(rec, f"E_{rec}_tarde"))  # outro e2e, mesmo mandato, janela vigente
        assert r.status_code == 200
        assert espioes["diagnosticos"] == 1
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.DESCARTADO
        assert len(cc.ciclos_do_mandato(None, rec)) == 1

    def test_eprofit_nao_positivo_descarta_com_o_outro_motivo(self, cliente, monkeypatch, espioes):
        self._predict_com(monkeypatch, eprofit=-10.0, recommend_action=False)
        rec = "RN_d_eprofit"
        _post(cliente, _falha(rec, f"E_{rec}_0"))
        ciclo = _ciclo(rec)
        assert (ciclo["estado"], ciclo["motivo_descarte"]) == (cc.DESCARTADO, "eprofit_nao_positivo")
        assert [d["saida"]["motivo_descarte"] for d in _decisoes("default_tenant", rec)
                if d["saida"].get("estrategia") == "descartado"] == ["eprofit_nao_positivo"]

    def test_confirmacao_de_ciclo_descartado_nao_conta_fee(self, cliente, monkeypatch):
        self._predict_com(monkeypatch, recovery_score=2, p_recovery=0.02)
        rec = "RN_d_desc_pago"
        _post(cliente, _falha(rec, f"E_{rec}_0"))
        r = _post(cliente, _pago(rec, f"E_{rec}_pago")).json()
        assert r["ciclo"] == "ciclo_descartado" and r["fee"] == 0.0


# ══════════════════════════════════════════════════════════════════════════
# I-4a: ciclo incompleto é retomado, não descartado em silêncio
# ══════════════════════════════════════════════════════════════════════════

class TestI4CicloIncompleto:

    def test_falha_no_atualizar_de_decide_recovery_503_e_reenvio_retoma_o_diagnostico(
            self, cliente, relogio, espioes, monkeypatch):
        original = cc.atualizar
        chamadas = {"n": 0}

        def atualizar_instavel(ciclo_id, agora=None, **campos):
            if "decidido_em" in campos:
                chamadas["n"] += 1
                if chamadas["n"] == 1:
                    raise sqlite3.OperationalError("disco fora do ar no meio do grafo")
            return original(ciclo_id, agora, **campos)
        monkeypatch.setattr(cc, "atualizar", atualizar_instavel)

        rec = "RN_d_i4"
        corpo = _falha(rec, f"E_{rec}_0")
        r = _post(cliente, corpo)
        assert r.status_code == 503 and r.headers["retry-after"] == "30"
        ciclo = _ciclo(rec)
        assert ciclo["estado"] == cc.RECOBRANDO and ciclo["decidido_em"] is None
        assert cc.tentativas_do_ciclo(ciclo["id"]) == []
        assert cc.ciclo_incompleto(ciclo) is True
        janela = (ciclo["janela_inicio"], ciclo["janela_fim"])
        assert espioes["diagnosticos"] == 1

        # O PSP reenvia (o 503 pediu): o diagnóstico é retomado sobre o MESMO ciclo.
        relogio(ABERTURA + timedelta(minutes=30))
        r = _post(cliente, corpo)
        assert r.status_code == 200 and r.json()["pipeline"] is True
        ciclos = cc.ciclos_do_mandato(None, rec)
        assert len(ciclos) == 1 and ciclos[0]["id"] == ciclo["id"], "abriu ciclo novo"
        depois = ciclos[0]
        assert (depois["janela_inicio"], depois["janela_fim"]) == janela, "a janela foi reancorada"
        assert depois["decidido_em"] and depois["estrategia"] == "retry_automatico"
        assert cc.ciclo_incompleto(depois) is False
        assert espioes["diagnosticos"] == 2, "o reenvio não rodou o diagnóstico (ou rodou mais de uma vez)"
        assert len(espioes["politica"]) == 1
        tentativas = cc.tentativas_do_ciclo(ciclo["id"])
        assert [t["numero"] for t in tentativas] == [1, 2, 3]
        assert all(datetime.fromisoformat(t["agendada_para"]) <= datetime.fromisoformat(janela[1])
                   for t in tentativas), "tentativa fora da janela original"

    def test_ciclo_descartado_nunca_e_retomado(self, cliente, monkeypatch, espioes):
        original = workflow_module._classifier.predict

        def score_baixo(features):
            r = dict(original(features))
            r["recovery_score"] = 2
            r["p_recovery"] = 0.02
            return r
        monkeypatch.setattr(workflow_module._classifier, "predict", score_baixo)
        rec = "RN_d_i4_desc"
        _post(cliente, _falha(rec, f"E_{rec}_0"))
        ciclo = _ciclo(rec)
        assert ciclo["estado"] == cc.DESCARTADO and cc.ciclo_incompleto(ciclo) is False
        _post(cliente, _falha(rec, f"E_{rec}_1"))
        assert espioes["diagnosticos"] == 1 and len(cc.ciclos_do_mandato(None, rec)) == 1


# ══════════════════════════════════════════════════════════════════════════
# As rotas de simulação continuam funcionando
# ══════════════════════════════════════════════════════════════════════════

class TestRotasDeSimulacao:

    def test_painel_cobranca_falhada_com_0_2_e_3(self, cliente, monkeypatch):
        monkeypatch.setenv("ENV", "demo")
        esperado = {0: ("retry_automatico", 3), 2: ("retry_automatico", 1), 3: ("mensagem_pagamento", 0)}
        for usadas, (estrategia, planejadas) in esperado.items():
            r = cliente.post("/simulate/painel/cobranca-falhada",
                             json={"valor": VALOR, "codigo_falha": "AM04", "tentativas_usadas": usadas})
            assert r.status_code == 200, r.text
            assert r.json()["estrategia"] == estrategia, (usadas, r.json()["estrategia"])
            assert len(r.json()["plano"]) == planejadas
            assert len(r.json()["plano_itens"]) == planejadas

    def test_simulate_pix_falhado_e_pix_pago_fecham_o_ciclo(self, cliente, monkeypatch):
        monkeypatch.setenv("ENV", "demo")
        r = cliente.post("/simulate/pix-falhado", json={"id_recorrencia": "RN_sim_b3", "codigo_falha": "AM04"})
        assert r.status_code == 200 and r.json()["status"] == "pipeline_executado"
        ciclo = _ciclo("RN_sim_b3")
        assert ciclo["estado"] == cc.RECOBRANDO
        r = cliente.post("/simulate/pix-pago", json={"id_recorrencia": "RN_sim_b3"})
        assert r.status_code == 200 and r.json()["ciclo"] == "recuperado" and r.json()["fee"] > 0
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.RECUPERADO


# ══════════════════════════════════════════════════════════════════════════
# B3-a: reserva ≠ envio
# ══════════════════════════════════════════════════════════════════════════

class TestB3aReservaDeMensagem:

    def _ciclo_pronto_para_mensagem(self, rec: str) -> dict:
        """Ciclo com as 3 tentativas falhadas, ainda `recobrando`."""
        ciclo = cc.abrir_ciclo(None, rec, VALOR, "insufficient_funds", ABERTURA,
                               e2e_falha_original=f"E_{rec}", estrategia="retry_automatico",
                               recovery_score=60, p_recovery=0.6, eprofit=80.0)
        cc.atualizar(ciclo["id"], ABERTURA, decidido_em=ABERTURA.isoformat())
        cc.agendar_tentativas(ciclo["id"], [
            {"numero": n, "quando": ABERTURA + timedelta(days=n), "valor": VALOR,
             "origem": "fallback_uniforme"} for n in (1, 2, 3)])
        for n in (1, 2, 3):
            cc.marcar_disparada(ciclo["id"], n, f"ch_{rec}_{n}", ABERTURA + timedelta(days=n))
            cc.registrar_resultado(ciclo["id"], n, cc.FALHOU, ABERTURA + timedelta(days=n, hours=1))
        return cc.ciclo_por_id(ciclo["id"])

    def test_mensagem_confirmada_so_depois_de_o_envio_retornar(self, espioes):
        ciclo = self._ciclo_pronto_para_mensagem("RN_b3a_ok")
        agora = ABERTURA + timedelta(days=4)
        asyncio.run(workflow_module.concluir_ciclo_por_resultado(ciclo["id"], agora))
        depois = cc.ciclo_por_id(ciclo["id"])
        assert depois["estado"] == cc.MENSAGEM_ENVIADA
        assert depois["mensagem_em"] == agora.isoformat()
        assert depois["mensagem_confirmada_em"] == agora.isoformat()
        assert len(espioes["mensagens"]) == 1

    def test_excecao_no_envio_volta_a_recobrando_e_a_passagem_seguinte_manda_uma(
            self, monkeypatch, espioes, caplog):
        ciclo = self._ciclo_pronto_para_mensagem("RN_b3a_exc")
        original = workflow_module._dunning.run_campaign
        estado = {"cai": True}

        async def instavel(*a, **k):
            if estado["cai"]:
                raise RuntimeError("LLM fora do ar e o fallback também")
            return await original(*a, **k)
        monkeypatch.setattr(workflow_module._dunning, "run_campaign", instavel)

        agora = ABERTURA + timedelta(days=4)
        with caplog.at_level(logging.WARNING):
            resultado = asyncio.run(workflow_module.concluir_ciclo_por_resultado(ciclo["id"], agora))
        assert resultado is None
        depois = cc.ciclo_por_id(ciclo["id"])
        assert depois["estado"] == cc.RECOBRANDO, "a reserva ficou órfã"
        assert depois["mensagem_em"] is None and depois["mensagem_confirmada_em"] is None
        assert [m for m in caplog.messages if "reservada e NÃO enviada" in m]
        assert espioes["mensagens"] == []

        # A passagem seguinte do agendador reenvia — uma vez.
        estado["cai"] = False
        _agendador(agora + timedelta(hours=1))
        depois = cc.ciclo_por_id(ciclo["id"])
        assert depois["estado"] == cc.MENSAGEM_ENVIADA and depois["mensagem_confirmada_em"]
        assert len(espioes["mensagens"]) == 1
        _agendador(agora + timedelta(hours=2))
        assert len(espioes["mensagens"]) == 1, "duas mensagens"

    def test_reserva_orfa_ha_16_min_e_reenviada_uma_vez_ha_14_nao(self, espioes):
        ciclo = self._ciclo_pronto_para_mensagem("RN_b3a_orfa")
        reserva = ABERTURA + timedelta(days=4)
        # O "processo morto": reservou e não confirmou.
        cc.transicionar(ciclo["id"], cc.MENSAGEM_ENVIADA, reserva)
        assert cc.ciclo_por_id(ciclo["id"])["mensagem_confirmada_em"] is None

        _agendador(reserva + timedelta(minutes=14))
        meio = cc.ciclo_por_id(ciclo["id"])
        assert meio["estado"] == cc.MENSAGEM_ENVIADA and meio["mensagem_confirmada_em"] is None
        assert espioes["mensagens"] == [], "reenviou antes dos 15 min"

        _agendador(reserva + timedelta(minutes=16))
        depois = cc.ciclo_por_id(ciclo["id"])
        assert depois["estado"] == cc.MENSAGEM_ENVIADA
        assert depois["mensagem_confirmada_em"] == (reserva + timedelta(minutes=16)).isoformat()
        assert depois["mensagem_em"] == (reserva + timedelta(minutes=16)).isoformat()
        assert len(espioes["mensagens"]) == 1

        _agendador(reserva + timedelta(minutes=40))
        assert len(espioes["mensagens"]) == 1, "duas mensagens"

    def test_a_confirmacao_nao_e_desfeita_pela_varredura(self, espioes):
        ciclo = self._ciclo_pronto_para_mensagem("RN_b3a_conf")
        agora = ABERTURA + timedelta(days=4)
        asyncio.run(workflow_module.concluir_ciclo_por_resultado(ciclo["id"], agora))
        _agendador(agora + timedelta(hours=5))
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.MENSAGEM_ENVIADA
        assert len(espioes["mensagens"]) == 1

    def test_o_prazo_de_recuperacao_conta_da_confirmacao(self):
        ciclo = self._ciclo_pronto_para_mensagem("RN_b3a_prazo")
        reserva = ABERTURA + timedelta(days=4)
        cc.transicionar(ciclo["id"], cc.MENSAGEM_ENVIADA, reserva)
        assert cc.varrer_prazo_de_recuperacao(reserva + timedelta(days=31)) == [], (
            "o prazo contou da reserva, não da confirmação")
        confirmada = reserva + timedelta(days=2)
        cc.confirmar_mensagem(ciclo["id"], confirmada)
        assert cc.varrer_prazo_de_recuperacao(confirmada + timedelta(days=30)) == []
        assert cc.varrer_prazo_de_recuperacao(confirmada + timedelta(days=30, seconds=1)) == [ciclo["id"]]

    def test_dentro_do_grafo_reserva_e_confirmacao_vao_juntas(self, cliente, espioes):
        rec = "RN_b3a_grafo"
        _post(cliente, _falha(rec, f"E_{rec}_0", codigo="AM02"))     # mensagem no próprio grafo
        ciclo = _ciclo(rec)
        assert ciclo["estado"] == cc.MENSAGEM_ENVIADA
        assert ciclo["mensagem_em"] and ciclo["mensagem_confirmada_em"] == ciclo["mensagem_em"]

    def test_desfazer_reserva_e_a_unica_transicao_de_volta(self):
        ciclo = self._ciclo_pronto_para_mensagem("RN_b3a_unica")
        cc.transicionar(ciclo["id"], cc.MENSAGEM_ENVIADA, ABERTURA)
        with pytest.raises(cc.TransicaoInvalida):
            cc.transicionar(ciclo["id"], cc.RECOBRANDO, ABERTURA)
        cc.confirmar_mensagem(ciclo["id"], ABERTURA)
        assert cc.desfazer_reserva_de_mensagem(ciclo["id"], "teste", ABERTURA) is False, (
            "uma mensagem confirmada foi desfeita")


# ══════════════════════════════════════════════════════════════════════════
# B3-b: ciclo recuperado nunca absorve uma falha
# ══════════════════════════════════════════════════════════════════════════

class TestB3bRecuperadoNaoAbsorveFalha:

    def test_falha_sem_id_apos_recuperado_na_janela_abre_ciclo_novo(self, cliente, relogio, espioes):
        rec = "RN_b3b"
        _post(cliente, _falha(rec, f"E_{rec}_0"))
        ciclo = _ciclo(rec)
        plano = cc.tentativas_do_ciclo(ciclo["id"])
        quando = datetime.fromisoformat(plano[0]["agendada_para"]) + timedelta(hours=1)
        relogio(quando)
        _agendador(quando)
        assert _post(cliente, _pago(rec, f"E_{rec}_pago")).json()["ciclo"] == "recuperado"
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.RECUPERADO

        # Ainda dentro da janela do BACEN do ciclo recuperado: outra falha, sem id.
        relogio(quando + timedelta(days=1))
        r = _post(cliente, _falha(rec, f"E_{rec}_nova"))
        assert r.status_code == 200 and r.json()["pipeline"] is True
        ciclos = cc.ciclos_do_mandato(None, rec)
        assert len(ciclos) == 2, "o ciclo recuperado absorveu a falha"
        novo = ciclos[0]
        assert novo["id"] != ciclo["id"] and novo["estado"] == cc.RECOBRANDO
        assert novo["id_cobranca_original"] == f"E_{rec}_nova"
        assert espioes["diagnosticos"] == 2 and len(espioes["politica"]) == 2
        assert [t["numero"] for t in cc.tentativas_do_ciclo(novo["id"])] == [1, 2, 3]
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.RECUPERADO

    def test_descartado_e_mensagem_enviada_continuam_absorvendo(self, cliente, relogio, espioes):
        rec = "RN_b3b_msg"
        _post(cliente, _falha(rec, f"E_{rec}_0", codigo="AM02"))       # mensagem_enviada
        assert _ciclo(rec)["estado"] == cc.MENSAGEM_ENVIADA
        relogio(ABERTURA + timedelta(days=2))
        _post(cliente, _falha(rec, f"E_{rec}_1"))
        assert len(cc.ciclos_do_mandato(None, rec)) == 1 and espioes["diagnosticos"] == 1


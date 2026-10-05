"""tests/test_mensagens_involuntario.py — Etapa 2, Bloco 3: as três mensagens.

O QUE ESTE ARQUIVO MEDE (Portão 3 e os acréscimos do Crai):
  - modo automático: 3 falhas → 3 sugestões gravadas → a recomendada sai →
    `mensagem_enviada`;
  - modo escolha: 3 falhas → `aguardando_escolha` → escolha pela rota → sai a
    escolhida; sem escolha, no prazo de 8 h a recomendada sai sozinha, com
    `por_prazo` na trilha;
  - nunca duas mensagens (escolha e prazo disputando; dois envios disputando);
  - nada gerado antes da 3ª falha (R1), salvo as exceções declaradas;
  - não existe rota que dispare mensagem sem ser a escolha de uma das 3;
  - escolher e regerar exigem admin/owner; membro recebe 403;
  - regerar: rodadas, prazo que não reinicia, a recomendada da ÚLTIMA sai por
    prazo, a escolha de qualquer rodada (D-E2-12), a decisão na trilha;
  - `sem_canal`: gerada, não entregável, visível; enviada quando o contato
    aparece; `perdido` com motivo depois de 30 dias (D-E2-11);
  - a janela de contato: nada sai fora dela, e o prazo de 8 h continua contando;
  - o dashboard: `mensagens`, `modo_mensagem`, `escolha_ate`, `escolhida_por`,
    `cliente_nome` (lido da base, nunca copiado), `aguardando_escolha` no mês;
  - toda mensagem termina com o aviso de mensagem automática;
  - o banco cru do ciclo e o de mensagens não têm contato;
  - a revogação com o ciclo esperando escolha refaz as sugestões (D-E2-7);
  - o 7-D consertado no involuntário.
"""

import asyncio
import hashlib
import hmac
import json
import re
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as workflow_module
from crai.api import app as app_module
from crai.api import datas
from crai.churn_voluntary import clientes_importados, importacao
from crai.churn_voluntary import retention_log as trilha
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao, dunning_engine
from crai.dunning import pix_automatico_retry as pix_retry_module
from crai.dunning import retry_scheduler as sched

A, B = "empresa-a", "empresa-b"
SECRET = b"s3cr3t_mensagens"
VALOR = 299.90
ABERTURA = datetime(2026, 9, 3, 9, 0)
FUSO = ZoneInfo("America/Sao_Paulo")
TELEFONE = "+5511988887777"
EMAIL = "cliente.final@exemplo.com.br"
NOME = "Mariana Albuquerque Tavares"


# ── Montagem ──────────────────────────────────────────────────────────────

def _assinar(corpo: bytes) -> dict:
    ts = int(time.time())
    mac = hmac.new(SECRET, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json"}


def _evento(rec, e2e, evento="automatic_pix.charge_failed", id_cobranca=None, codigo="AM04"):
    corpo = {"event": evento, "e2e_id": e2e, "valor": VALOR, "id_recorrencia": rec}
    if evento.endswith("charge_failed"):
        corpo["codigo_falha"] = codigo
    if id_cobranca:
        corpo["id_cobranca"] = id_cobranca
    return json.dumps(corpo).encode()


@pytest.fixture
def producao(monkeypatch):
    """Os padrões de PRODUÇÃO da configuração (a suíte roda com os neutros)."""
    for chave, valor in configuracao.PADROES_DE_PRODUCAO.items():
        monkeypatch.setitem(configuracao.PADROES, chave, valor)


@pytest.fixture
def relogio(monkeypatch):
    estado = {"agora": ABERTURA}

    class Congelado(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return estado["agora"]
            return estado["agora"].replace(tzinfo=FUSO).astimezone(tz)

    monkeypatch.setenv(datas.ENV_FUSO, "America/Sao_Paulo")
    monkeypatch.setattr(workflow_module, "datetime", Congelado)
    monkeypatch.setattr(pix_retry_module, "datetime", Congelado)
    monkeypatch.setattr(app_module, "datetime", Congelado)
    monkeypatch.setattr(trilha, "datetime", Congelado)
    monkeypatch.setattr(datas, "agora_local", lambda: estado["agora"])
    monkeypatch.setattr(workflow_module._pix_retry, "confianca_minima", 2.0)

    def mover(para):
        estado["agora"] = para
    mover.agora = lambda: estado["agora"]
    return mover


@pytest.fixture
def sem_llm(monkeypatch):
    async def recusa(*a, **k):
        raise RuntimeError("sem LLM no teste")
    monkeypatch.setattr(dunning_engine.claude.messages, "create", recusa)


@pytest.fixture
def envios(monkeypatch):
    """Conta as mensagens que SAÍRAM (o envio passa por `run_campaign`)."""
    saidas = []
    original = workflow_module._dunning.run_campaign

    async def espiao(customer_id, failure_cause, *a, **k):
        r = await original(customer_id, failure_cause, *a, **k)
        saidas.append({"customer_id": customer_id, "texto": r["message"], "canal": r["channel"],
                       "abordagem": k.get("abordagem")})
        return r
    monkeypatch.setattr(workflow_module._dunning, "run_campaign", espiao)
    return saidas


@pytest.fixture
def cliente(monkeypatch, supabase_falso, relogio, sem_llm):
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", SECRET.decode())
    with TestClient(app_module.app) as c:
        c.projeto = supabase_falso
        yield c


def _base(tenant, rec, **extra):
    """Um cliente na base importada, ligado à recorrência `rec`."""
    linha = {"customer_id_externo": f"cli_{rec}", "mrr": "299,90", "billing_profile": "CLT",
             "id_recorrencia": rec, **extra}
    validado, motivo = importacao.validar_linha(linha)
    assert motivo is None, motivo
    clientes_importados.gravar(tenant, [validado])


def _post(c, corpo, tenant=A):
    r = c.post("/webhooks/pix-automatico", content=corpo,
               headers={**_assinar(corpo), "x-tenant-id": tenant})
    assert r.status_code == 200, r.text
    return r.json()


def _agendador(quando):
    return asyncio.run(sched.processar_tentativas_devidas(quando))


def _ciclo(rec, tenant=A):
    ciclos = cc.ciclos_do_mandato(tenant, rec)
    assert ciclos, f"nenhum ciclo para {rec}"
    return ciclos[0]


def _falhas(c, relogio, rec, quantas=3, tenant=A):
    """Falha original + `quantas` tentativas falhando pelo webhook. Devolve o
    ciclo e o instante da última falha."""
    _post(c, _evento(rec, f"E_{rec}_0"), tenant)
    ciclo = _ciclo(rec, tenant)
    quando = ABERTURA
    for numero in range(1, quantas + 1):
        plano = cc.tentativas_do_ciclo(ciclo["id"])
        quando = datetime.fromisoformat(plano[numero - 1]["agendada_para"]) + timedelta(hours=1)
        relogio(quando)
        disparos = _agendador(quando)
        assert [d["numero"] for d in disparos] == [numero]
        _post(c, _evento(rec, f"E_{rec}_t{numero}", id_cobranca=disparos[0]["id_cobranca"]), tenant)
    return cc.ciclo_por_id(ciclo["id"]), quando


def _get(c, caminho, tenant=A, **params):
    return c.get(caminho, params=params, headers=c.projeto.bearer(tenant))


def _escolher(c, ciclo_id, rodada, abordagem, papel="admin", tenant=A, **extra):
    return c.post(f"/ciclos/{ciclo_id}/mensagens/escolher",
                  json={"rodada": rodada, "abordagem": abordagem, **extra},
                  headers=c.projeto.bearer(tenant, papel=papel) if papel else c.projeto.bearer(tenant))


def _regerar(c, ciclo_id, papel="admin", tenant=A):
    return c.post(f"/ciclos/{ciclo_id}/mensagens/regerar",
                  headers=c.projeto.bearer(tenant, papel=papel) if papel else c.projeto.bearer(tenant))


def _decisoes(tenant, sujeito):
    return sorted(trilha.decisoes_do_sujeito(tenant, sujeito, limite=200), key=lambda d: d["id"])


def _escolhas_na_trilha(tenant, sujeito):
    return [d for d in _decisoes(tenant, sujeito)
            if d["tipo_decisao"] == trilha.TIPO_OFERTA
            and d["saida"].get("regra") == "escolha_entre_sugestoes"]


# ══════════════════════════════════════════════════════════════════════════
# Os dois modos
# ══════════════════════════════════════════════════════════════════════════

class TestModoAutomatico:

    def test_tres_falhas_tres_sugestoes_e_a_recomendada_sai(self, cliente, relogio, envios,
                                                            producao):
        configuracao.gravar(A, {"modo_mensagem_involuntario": "automatico"})
        rec = "RN_auto"
        _base(A, rec, telefone=TELEFONE, nome=NOME)
        ciclo, _ = _falhas(cliente, relogio, rec)

        assert ciclo["estado"] == cc.MENSAGEM_ENVIADA and ciclo["mensagem_confirmada_em"]
        mensagens = cc.mensagens_do_ciclo(ciclo["id"])
        assert [m["abordagem"] for m in mensagens] == list(cc.ABORDAGENS)
        recomendada = [m for m in mensagens if m["recomendada"]]
        assert len(recomendada) == 1 and recomendada[0]["escolhida"] == 1
        assert recomendada[0]["escolhida_por"] == "automatico" and recomendada[0]["enviada_em"]
        assert len(envios) == 1 and envios[0]["abordagem"] == recomendada[0]["abordagem"]
        # O canal saiu da base (telefone → WhatsApp), lido na hora.
        assert envios[0]["canal"] == "whatsapp"
        assert recomendada[0]["motivo_canal"] == "contato_da_base"
        escolhas = _escolhas_na_trilha(A, rec)
        assert len(escolhas) == 1 and escolhas[0]["saida"]["escolhida_por"] == "automatico"
        assert trilha.verificar_cadeia(A)["integra"] is True


class TestModoEscolha:

    def test_aguarda_a_escolha_e_sai_a_escolhida(self, cliente, relogio, envios, producao):
        rec = "RN_escolha"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        assert ciclo["estado"] == cc.AGUARDANDO_ESCOLHA and envios == []
        assert len(cc.mensagens_do_ciclo(ciclo["id"])) == 3

        detalhe = _get(cliente, f"/ciclos/{ciclo['id']}").json()
        assert detalhe["ciclo"]["status"] == cc.STATUS_EM_PROCESSO
        assert detalhe["modo_mensagem"] == "escolha" and detalhe["escolhida_por"] is None
        assert datetime.fromisoformat(detalhe["escolha_ate"]) == (
            ultima + timedelta(hours=8)).replace(tzinfo=FUSO)
        assert len(detalhe["mensagens"]) == 3
        for m in detalhe["mensagens"]:
            assert m["texto"].endswith(dunning_engine.AVISO_AUTOMATICO)
            assert m["canal"] == "email" and m["escolhida"] is False
        mes = _get(cliente, "/metrics/involuntario/mes", mes="2026-09").json()
        assert mes["aguardando_escolha"] == 1

        r = _escolher(cliente, ciclo["id"], 1, "urgencia_respeitosa", papel="owner")
        assert r.status_code == 200, r.text
        assert r.json()["enviada"] is True and r.json()["escolhida_por"] == "owner"
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.MENSAGEM_ENVIADA
        assert [e["abordagem"] for e in envios] == ["urgencia_respeitosa"]
        detalhe = _get(cliente, f"/ciclos/{ciclo['id']}").json()
        assert detalhe["escolhida_por"] == "owner" and detalhe["escolha_ate"] is None
        escolha = _escolhas_na_trilha(A, rec)[0]["saida"]
        assert (escolha["escolhida_por"], escolha["por_prazo"]) == ("owner", False)
        assert _get(cliente, "/metrics/involuntario/mes", mes="2026-09").json()["aguardando_escolha"] == 0

    def test_sem_escolha_em_8_horas_a_recomendada_sai_sozinha(self, cliente, relogio, envios,
                                                              producao):
        rec = "RN_prazo"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        _agendador(ultima + timedelta(hours=7, minutes=59))
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.AGUARDANDO_ESCOLHA and envios == []

        relogio(ultima + timedelta(hours=8, minutes=1))
        _agendador(ultima + timedelta(hours=8, minutes=1))
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.MENSAGEM_ENVIADA
        recomendada = next(m for m in cc.mensagens_do_ciclo(ciclo["id"]) if m["recomendada"])
        assert recomendada["escolhida_por"] == "prazo" and recomendada["por_prazo"] == 1
        assert [e["abordagem"] for e in envios] == [recomendada["abordagem"]]
        escolha = _escolhas_na_trilha(A, rec)[0]["saida"]
        assert escolha["por_prazo"] is True and escolha["escolhida_por"] == "prazo"
        assert _get(cliente, f"/ciclos/{ciclo['id']}").json()["escolhida_por"] == "prazo"


# ══════════════════════════════════════════════════════════════════════════
# Nunca duas mensagens
# ══════════════════════════════════════════════════════════════════════════

class TestNuncaDuasMensagens:

    def test_escolha_depois_do_prazo_e_409_e_uma_mensagem_so(self, cliente, relogio, envios,
                                                             producao):
        rec = "RN_disputa"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        relogio(ultima + timedelta(hours=9))
        _agendador(ultima + timedelta(hours=9))
        r = _escolher(cliente, ciclo["id"], 1, "lembrete_cordial")
        assert r.status_code == 409
        assert len(envios) == 1

    def test_duas_escolhas_ao_mesmo_tempo_uma_vence(self, cliente, relogio, producao):
        rec = "RN_duas"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        primeira = cc.registrar_escolha(ciclo["id"], 1, "facilitacao", "admin", ultima)
        segunda = cc.registrar_escolha(ciclo["id"], None, None, "prazo", ultima)
        assert primeira is not None and segunda is None
        # O banco também recusa: o índice único parcial.
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(cc.caminho_do_banco()) as conn:
                conn.execute("UPDATE mensagens_ciclo SET escolhida = 1 WHERE ciclo_id = ?",
                             (ciclo["id"],))

    def test_dois_envios_disputando_mandam_uma(self, cliente, relogio, envios, producao):
        rec = "RN_dois_envios"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        workflow_module.registrar_escolha(ciclo["id"], "admin", ultima, 1, "facilitacao")

        async def dois():
            return await asyncio.gather(workflow_module.enviar_mensagem_escolhida(ciclo["id"], ultima),
                                        workflow_module.enviar_mensagem_escolhida(ciclo["id"], ultima))
        resultados = asyncio.run(dois())
        assert sum(1 for r in resultados if r is not None) == 1
        assert len(envios) == 1
        # E a passagem do relógio depois não manda outra.
        _agendador(ultima + timedelta(hours=1))
        assert len(envios) == 1


# ══════════════════════════════════════════════════════════════════════════
# R1: nada antes da 3ª falha; nenhuma rota dispara mensagem
# ══════════════════════════════════════════════════════════════════════════

class TestR1:

    def test_nada_e_gerado_antes_da_terceira_falha(self, cliente, relogio, envios, producao):
        rec = "RN_r1"
        _base(A, rec, email=EMAIL)
        ciclo, _ = _falhas(cliente, relogio, rec, quantas=2)
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.RECOBRANDO
        assert cc.mensagens_do_ciclo(ciclo["id"]) == [] and envios == []
        detalhe = _get(cliente, f"/ciclos/{ciclo['id']}").json()
        assert detalhe["mensagens"] == []

    def test_excecao_declarada_revogacao_gera_as_tres(self, cliente, relogio, producao):
        rec = "RN_rev_r1"
        _base(A, rec, email=EMAIL)
        _post(cliente, _evento(rec, f"E_{rec}_0"))
        ciclo = _ciclo(rec)
        _post(cliente, _evento(rec, f"E_{rec}_rev", "automatic_pix.authorization_revoked"))
        mensagens = cc.mensagens_do_ciclo(ciclo["id"])
        assert len(mensagens) == 3 and {m["causa"] for m in mensagens} == {"authorization_revoked"}
        assert {m["metodo_pagamento"] for m in mensagens} == {"boleto"}
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.AGUARDANDO_ESCOLHA

    def test_as_unicas_rotas_de_mensagem_sao_escolher_e_regerar(self):
        rotas = {(m, r.path) for r in app_module.app.routes
                 for m in getattr(r, "methods", set()) if "mensag" in r.path}
        assert rotas == {("POST", "/ciclos/{ciclo_id}/mensagens/escolher"),
                         ("POST", "/ciclos/{ciclo_id}/mensagens/regerar")}

    def test_escolher_nao_aceita_texto(self, cliente, relogio, producao):
        rec = "RN_texto"
        _base(A, rec, email=EMAIL)
        ciclo, _ = _falhas(cliente, relogio, rec)
        r = _escolher(cliente, ciclo["id"], 1, "facilitacao", texto="Pague agora, por favor")
        assert r.status_code == 422 and r.json()["detail"]["campo"] == "texto"
        assert cc.mensagem_escolhida(ciclo["id"]) is None

    def test_producao_so_envia_por_enviar_mensagem_escolhida(self):
        """CATRACA de texto: em `app/crai`, a transição para `mensagem_enviada`
        só existe dentro de `enviar_mensagem_escolhida`."""
        raiz = Path(workflow_module.__file__).resolve().parents[1]
        achados = []
        for arquivo in raiz.rglob("*.py"):
            fonte = arquivo.read_text(encoding="utf-8")
            for m in re.finditer(r"transicionar\([^)]*MENSAGEM_ENVIADA", fonte):
                antes = fonte[:m.start()]
                funcao = re.findall(r"\n(?:async )?def (\w+)", antes)
                achados.append((arquivo.name, funcao[-1] if funcao else None))
        assert achados == [("workflow.py", "enviar_mensagem_escolhida")], achados


# ══════════════════════════════════════════════════════════════════════════
# Papéis
# ══════════════════════════════════════════════════════════════════════════

class TestPapel:

    @pytest.mark.parametrize("papel", ["membro", None])
    def test_membro_e_sem_papel_recebem_403(self, cliente, relogio, producao, papel):
        rec = f"RN_papel_{papel}"
        _base(A, rec, email=EMAIL)
        ciclo, _ = _falhas(cliente, relogio, rec)
        assert _escolher(cliente, ciclo["id"], 1, "facilitacao", papel=papel).status_code == 403
        assert _regerar(cliente, ciclo["id"], papel=papel).status_code == 403
        assert cc.mensagem_escolhida(ciclo["id"]) is None

    def test_papel_fora_do_vocabulario_e_401(self, cliente):
        r = cliente.get("/ciclos", headers=cliente.projeto.bearer(A, papel="dono"))
        assert r.status_code == 401 and r.json()["detail"]["motivo"] == "papel_invalido"

    def test_ciclo_de_outra_empresa_e_404_igual_ao_inexistente(self, cliente, relogio, producao):
        rec = "RN_outra"
        _base(B, rec, email=EMAIL)
        ciclo, _ = _falhas(cliente, relogio, rec, tenant=B)
        de_b = _escolher(cliente, ciclo["id"], 1, "facilitacao", tenant=A)
        inexistente = _escolher(cliente, 999999, 1, "facilitacao", tenant=A)
        assert de_b.status_code == inexistente.status_code == 404
        assert de_b.json() == inexistente.json()
        assert _regerar(cliente, ciclo["id"], tenant=A).json() == _regerar(
            cliente, 999999, tenant=A).json()


# ══════════════════════════════════════════════════════════════════════════
# Regerar (acréscimo à R8) e escolha de qualquer rodada (D-E2-12)
# ══════════════════════════════════════════════════════════════════════════

class TestRegerar:

    def test_rodadas_prazo_que_nao_reinicia_e_a_recomendada_da_ultima(self, cliente, relogio,
                                                                     envios, producao):
        rec = "RN_regerar"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        escolha_ate = _get(cliente, f"/ciclos/{ciclo['id']}").json()["escolha_ate"]

        relogio(ultima + timedelta(hours=2))
        r = _regerar(cliente, ciclo["id"])
        assert r.status_code == 200, r.text
        assert r.json()["rodada"] == 2 and len(r.json()["mensagens"]) == 3
        assert r.json()["escolha_ate"] == escolha_ate, "a regeração reiniciou o prazo"
        relogio(ultima + timedelta(hours=3))
        assert _regerar(cliente, ciclo["id"]).json()["rodada"] == 3
        mensagens = cc.mensagens_do_ciclo(ciclo["id"])
        assert [m["rodada"] for m in mensagens] == [1, 1, 1, 2, 2, 2, 3, 3, 3]
        regeracoes = [d for d in _decisoes(A, rec) if d["saida"].get("regra") == "regeracao_de_sugestoes"]
        assert [d["saida"]["rodada"] for d in regeracoes] == [2, 3]
        assert {d["saida"]["pedida_por_papel"] for d in regeracoes} == {"admin"}

        relogio(ultima + timedelta(hours=8, minutes=1))
        _agendador(ultima + timedelta(hours=8, minutes=1))
        escolhida = cc.mensagem_escolhida(ciclo["id"])
        assert escolhida["rodada"] == 3 and escolhida["recomendada"] == 1 and escolhida["por_prazo"] == 1
        assert len(envios) == 1
        assert _regerar(cliente, ciclo["id"]).status_code == 409

    def test_regerar_depois_do_prazo_e_409(self, cliente, relogio, producao):
        rec = "RN_regerar_tarde"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        relogio(ultima + timedelta(hours=8))
        r = _regerar(cliente, ciclo["id"])
        assert r.status_code == 409 and r.json()["detail"]["motivo"] == "prazo_de_escolha_vencido"

    def test_regerar_no_modo_automatico_e_409(self, cliente, relogio, producao):
        rec = "RN_regerar_auto"
        _base(A, rec, email=EMAIL)
        ciclo, _ = _falhas(cliente, relogio, rec)
        configuracao.gravar(A, {"modo_mensagem_involuntario": "automatico"})
        assert _regerar(cliente, ciclo["id"]).json()["detail"]["motivo"] == "modo_automatico"

    def test_a_escolha_pode_ser_de_qualquer_rodada(self, cliente, relogio, envios, producao):
        rec = "RN_rodada_1"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        relogio(ultima + timedelta(hours=1))
        _regerar(cliente, ciclo["id"])
        r = _escolher(cliente, ciclo["id"], 1, "lembrete_cordial")
        assert r.status_code == 200 and r.json()["enviada"] is True
        escolhida = cc.mensagem_escolhida(ciclo["id"])
        assert (escolhida["rodada"], escolhida["abordagem"]) == (1, "lembrete_cordial")
        assert _escolher(cliente, ciclo["id"], 9, "lembrete_cordial").status_code == 409


# ══════════════════════════════════════════════════════════════════════════
# Canal e janela
# ══════════════════════════════════════════════════════════════════════════

class TestSemCanal:

    def test_sem_contato_gerada_nao_entregavel_e_visivel(self, cliente, relogio, envios, producao):
        configuracao.gravar(A, {"modo_mensagem_involuntario": "automatico"})
        rec = "RN_sem_canal"
        _base(A, rec)                                     # mapeado, sem contato
        ciclo, ultima = _falhas(cliente, relogio, rec)
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.AGUARDANDO_ESCOLHA and envios == []
        detalhe = _get(cliente, f"/ciclos/{ciclo['id']}").json()
        escolhida = next(m for m in detalhe["mensagens"] if m["escolhida"])
        assert escolhida["canal"] == "sem_canal" and escolhida["nao_entregavel"] is True
        assert escolhida["motivo_canal"] == "sem_contato"
        assert "whatsapp" not in {m["canal"] for m in detalhe["mensagens"]}
        assert "mensagem_nao_entregavel" in [e["tipo"] for e in detalhe["linha_do_tempo"]]

        # A empresa acrescenta o telefone: a próxima passagem envia.
        _base(A, rec, telefone=TELEFONE)
        _agendador(ultima + timedelta(hours=1))
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.MENSAGEM_ENVIADA
        assert [e["canal"] for e in envios] == ["whatsapp"]

    def test_sem_mapeamento_e_sem_canal(self, cliente, relogio, envios, producao):
        configuracao.gravar(A, {"modo_mensagem_involuntario": "automatico"})
        ciclo, _ = _falhas(cliente, relogio, "RN_sem_mapa")
        m = cc.mensagem_escolhida(ciclo["id"])
        assert (m["canal"], m["motivo_canal"]) == ("sem_canal", "sem_mapeamento") and envios == []
        assert _get(cliente, "/ciclos").json()["ciclos"][0]["cliente_nome"] is None

    def test_trinta_dias_sem_canal_vira_perdido_com_motivo(self, cliente, relogio, envios, producao):
        configuracao.gravar(A, {"modo_mensagem_involuntario": "automatico"})
        rec = "RN_sem_canal_30"
        _base(A, rec)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        _agendador(ultima + timedelta(days=29))
        assert cc.ciclo_por_id(ciclo["id"])["estado"] == cc.AGUARDANDO_ESCOLHA
        _agendador(ultima + timedelta(days=30, minutes=1))
        final = cc.ciclo_por_id(ciclo["id"])
        assert final["estado"] == cc.PERDIDO and final["motivo_perdido"] == "sem_canal"
        detalhe = _get(cliente, f"/ciclos/{ciclo['id']}").json()
        assert detalhe["ciclo"]["status"] == cc.STATUS_ENCERRADO
        assert detalhe["ciclo"]["motivo_perdido"] == "sem_canal"
        assert detalhe["linha_do_tempo"][-1]["dados"]["motivo_perdido"] == "sem_canal"
        assert envios == []


class TestJanelaDeContato:

    def test_escolha_fora_da_janela_espera_o_inicio_da_proxima(self, cliente, relogio, envios,
                                                              producao):
        rec = "RN_janela"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        noite = ultima.replace(hour=22, minute=0)
        relogio(noite)
        r = _escolher(cliente, ciclo["id"], 1, "facilitacao")
        assert r.json()["enviada"] is False and r.json()["espera"] == "fora_da_janela"
        _agendador(noite + timedelta(hours=2))
        assert envios == []
        manha = (noite + timedelta(days=1)).replace(hour=8, minute=0)
        _agendador(manha - timedelta(minutes=1))
        assert envios == []
        _agendador(manha)
        assert len(envios) == 1 and cc.ciclo_por_id(ciclo["id"])["estado"] == cc.MENSAGEM_ENVIADA

    def test_escolhida_fora_da_janela_nao_tem_mais_prazo_de_escolha(self, cliente, relogio, envios,
                                                                   producao):
        # Rodada 3, Fase 7: o teste ao vivo do dashboard, rodado depois das 20h,
        # mostrou o detalhe devolvendo `escolha_ate` de um ciclo já escolhido.
        rec = "RN_janela_prazo"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        assert _get(cliente, f"/ciclos/{ciclo['id']}").json()["escolha_ate"] is not None
        relogio(ultima.replace(hour=22, minute=0))
        r = _escolher(cliente, ciclo["id"], 1, "facilitacao")
        assert r.json()["enviada"] is False and r.json()["espera"] == "fora_da_janela"
        detalhe = _get(cliente, f"/ciclos/{ciclo['id']}").json()
        assert detalhe["ciclo"]["estado"] == cc.AGUARDANDO_ESCOLHA and envios == []
        assert detalhe["escolhida_por"] == "admin" and detalhe["escolha_ate"] is None

    def test_o_prazo_de_8_horas_continua_contando_fora_da_janela(self, cliente, relogio, envios,
                                                                producao):
        configuracao.gravar(A, {"janela_contato_inicio": "08:00", "janela_contato_fim": "11:00",
                                "prazo_escolha_horas": 2})
        rec = "RN_prazo_noite"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)            # 10:00
        _agendador(ultima + timedelta(hours=2, minutes=5))        # 12:05: prazo venceu, janela fechada
        m = cc.mensagem_escolhida(ciclo["id"])
        assert m is not None and m["por_prazo"] == 1 and envios == []
        _agendador((ultima + timedelta(days=1)).replace(hour=8, minute=5))
        assert len(envios) == 1


# ══════════════════════════════════════════════════════════════════════════
# O que a tela vê, e o que o banco NÃO guarda
# ══════════════════════════════════════════════════════════════════════════

class TestClienteNomeEDadoPessoal:

    def test_cliente_nome_lido_da_base_e_nunca_copiado(self, cliente, relogio, envios, producao):
        configuracao.gravar(A, {"modo_mensagem_involuntario": "automatico"})
        rec = "RN_nome"
        _base(A, rec, telefone=TELEFONE, email=EMAIL, nome=NOME)
        ciclo, _ = _falhas(cliente, relogio, rec)
        assert _get(cliente, "/ciclos").json()["ciclos"][0]["cliente_nome"] == NOME
        assert _get(cliente, f"/ciclos/{ciclo['id']}").json()["ciclo"]["cliente_nome"] == NOME

        with sqlite3.connect(cc.caminho_do_banco()) as conn:
            conn.row_factory = sqlite3.Row
            linhas = {t: [dict(l) for l in conn.execute(f"SELECT * FROM {t}")]
                      for t in ("ciclos_cobranca", "tentativas_cobranca", "mensagens_ciclo")}
            colunas = {l[1].lower() for t in linhas for l in conn.execute(f"PRAGMA table_info({t})")}
        texto = json.dumps(linhas, ensure_ascii=False, default=str)
        for pii in (TELEFONE, "988887777", EMAIL, "exemplo.com.br", NOME, "Albuquerque", "Tavares"):
            assert pii not in texto, f"{pii!r} chegou ao banco do ciclo"
        assert not (colunas & {"telefone", "email", "nome", "phone", "cliente_nome", "cpf"})
        # O texto da mensagem tem, no máximo, o primeiro nome.
        assert all("Mariana" in m["texto"] for m in linhas["mensagens_ciclo"])
        assert "Albuquerque" not in envios[0]["texto"]

        # A trilha também não: nem o texto, nem o nome, nem o contato.
        trilha_texto = json.dumps(_decisoes(A, rec), ensure_ascii=False, default=str)
        for pii in (TELEFONE, EMAIL, NOME, "Mariana", dunning_engine.AVISO_AUTOMATICO):
            assert pii not in trilha_texto

    def test_toda_mensagem_termina_com_o_aviso_automatico(self, cliente, relogio, envios, producao):
        configuracao.gravar(A, {"modo_mensagem_involuntario": "automatico"})
        rec = "RN_aviso"
        _base(A, rec, email=EMAIL)
        _falhas(cliente, relogio, rec)
        assert envios and all(e["texto"].endswith(dunning_engine.AVISO_AUTOMATICO) for e in envios)
        with sqlite3.connect(cc.caminho_do_banco()) as conn:
            textos = [l[0] for l in conn.execute("SELECT texto FROM mensagens_ciclo")]
        assert textos and all(t.endswith(dunning_engine.AVISO_AUTOMATICO) for t in textos)


# ══════════════════════════════════════════════════════════════════════════
# D-E2-7 e 7-D
# ══════════════════════════════════════════════════════════════════════════

class TestRevogacaoDuranteAEspera:

    def test_revogacao_com_ciclo_aguardando_refaz_as_sugestoes_sem_reiniciar_o_prazo(
            self, cliente, relogio, envios, producao):
        rec = "RN_rev_espera"
        _base(A, rec, email=EMAIL)
        ciclo, ultima = _falhas(cliente, relogio, rec)
        escolha_ate = _get(cliente, f"/ciclos/{ciclo['id']}").json()["escolha_ate"]
        relogio(ultima + timedelta(hours=1))
        resposta = _post(cliente, _evento(rec, f"E_{rec}_rev", "automatic_pix.authorization_revoked"))
        assert resposta["ciclo"] == cc.AGUARDANDO_ESCOLHA
        rodada2 = [m for m in cc.mensagens_do_ciclo(ciclo["id"]) if m["rodada"] == 2]
        assert len(rodada2) == 3
        assert {m["metodo_pagamento"] for m in rodada2} == {"boleto"}
        assert _get(cliente, f"/ciclos/{ciclo['id']}").json()["escolha_ate"] == escolha_ate
        assert envios == []


class TestSeteD:

    def test_segunda_cobranca_do_mandato_nao_regrava_as_decisoes_da_primeira(
            self, cliente, relogio):
        rec = "RN_7d"
        _post(cliente, _evento(rec, "E_7d_a", id_cobranca="inv_7d_a"))
        primeira = len(_decisoes(A, rec))
        _post(cliente, _evento(rec, "E_7d_b", id_cobranca="inv_7d_b"))
        linhas = _decisoes(A, rec)
        # Sem o conserto, a segunda cobrança gravava as decisões da primeira de
        # novo junto com as dela: 3x. Com ele, cada cobrança grava as suas: 2x.
        # (O conteúdo não serve de critério aqui: com o relógio congelado, as
        # duas cobranças têm as mesmas features no mesmo instante.)
        assert primeira > 0 and len(linhas) == 2 * primeira, (primeira, len(linhas))
        assert trilha.verificar_cadeia(A)["integra"] is True


# ══════════════════════════════════════════════════════════════════════════
# Configuração
# ══════════════════════════════════════════════════════════════════════════

class TestConfiguracao:

    def test_padroes_de_producao(self):
        p = configuracao.PADROES_DE_PRODUCAO
        assert p["modo_mensagem_involuntario"] == "escolha" and p["prazo_escolha_horas"] == 8
        assert (p["janela_contato_inicio"], p["janela_contato_fim"]) == ("08:00", "20:00")
        assert p["canal_presumido"] is None
        assert (p["posicao_grave_pct"], p["posicao_preocupante_pct"]) == (10, 20)
        assert p["retencao_mensagens_dias"] == 90 and p["retencao_trilha_anos"] == 5

    @pytest.mark.parametrize("parcial", [
        {"modo_mensagem_involuntario": "manual"}, {"prazo_escolha_horas": 0},
        {"prazo_escolha_horas": True}, {"janela_contato_inicio": "8h"},
        {"janela_contato_fim": "00:00"}, {"canais_permitidos": []},
        {"canais_permitidos": ["ligacao_cs"]}, {"canais_permitidos": ["email", "email"]},
        {"posicao_grave_pct": 0}, {"posicao_preocupante_pct": 100},
        {"canal_presumido": "whatsapp"}, {"chave_inventada": 1},
    ])
    def test_valor_invalido_e_recusado(self, parcial):
        with pytest.raises(configuracao.ConfiguracaoInvalida):
            configuracao.gravar(A, parcial)

    def test_conjunto_invalido_e_recusado_e_nada_e_gravado(self, producao):
        with pytest.raises(configuracao.ConfiguracaoInvalida):
            configuracao.gravar(A, {"janela_contato_inicio": "21:00"})
        with pytest.raises(configuracao.ConfiguracaoInvalida):
            configuracao.gravar(A, {"posicao_grave_pct": 30})
        assert configuracao.ler(A)["posicao_grave_pct"] == configuracao.PADROES["posicao_grave_pct"]

    def test_configuracao_de_uma_empresa_nao_vaza_para_outra(self):
        configuracao.gravar(A, {"prazo_escolha_horas": 3, "posicao_grave_pct": 5})
        assert configuracao.ler(A)["prazo_escolha_horas"] == 3
        assert configuracao.ler(B)["prazo_escolha_horas"] == configuracao.PADROES["prazo_escolha_horas"]
        assert configuracao.ler(A)["posicao_grave_pct"] == 5

    def test_janela_de_contato(self):
        c = {**configuracao.PADROES_DE_PRODUCAO}
        assert not configuracao.dentro_da_janela(c, datetime(2026, 9, 3, 7, 59))
        assert configuracao.dentro_da_janela(c, datetime(2026, 9, 3, 8, 0))
        assert configuracao.dentro_da_janela(c, datetime(2026, 9, 3, 19, 59))
        assert not configuracao.dentro_da_janela(c, datetime(2026, 9, 3, 20, 0))
        assert configuracao.proximo_inicio_da_janela(c, datetime(2026, 9, 3, 21, 0)) == \
            datetime(2026, 9, 4, 8, 0)

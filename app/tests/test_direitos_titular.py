"""tests/test_direitos_titular.py - Rodada 3, Fase 6: direitos do titular e descadastro.

O QUE ESTE ARQUIVO MEDE:

  - NAO CONTATAR: a marca por cliente (`POST` e `DELETE /clientes/{id}/nao-contatar`),
    so para dono e administrador; com ela nenhuma mensagem sai, no involuntario
    (a cadeia de canal devolve `sem_canal`, com o motivo) nem no voluntario; as
    tentativas de cobranca do Pix NAO param;
  - a linha "Para nao receber mais mensagens, responda SAIR." em toda mensagem,
    antes do aviso de mensagem automatica;
  - `POST /simulate/resposta-sair`: a resposta SAIR marca o cliente;
  - `POST /titular/exportar` e `POST /titular/anonimizar` (art. 18): papel,
    registro de acesso, 404 identico para outra empresa, o que sai e o que nao
    sai; a anonimizacao apaga o texto das mensagens e os contatos, mantem as
    metricas agregadas e NAO toca a trilha do Art. 20;
  - `GET /titular/texto-para-politica`: o arquivo versionado, com os prazos da
    configuracao da empresa.
"""

import inspect
import json
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from crai import config as crai_config
from crai.api import app as app_module
from crai.api import registro_acesso
from crai.api import titular as titular_api
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import disparo_lote as dl
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import voluntary_agent as va
from crai.dunning import canal_involuntario
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao, dunning_engine
from tests import test_mensagens_involuntario as M
from tests.test_mensagens_involuntario import (A, B, cliente, envios, producao, relogio,  # noqa: F401
                                               sem_llm)

SAIR = "Para não receber mais mensagens, responda SAIR."
AUTOMATICA = "Esta é uma mensagem automática."
REC = "RN_titular_1"
CID = f"cli_{REC}"
MOTIVO = "cliente_pediu_para_nao_ser_contatado"
ROTAS_DA_MARCA = (("POST", f"/clientes/{CID}/nao-contatar"), ("DELETE", f"/clientes/{CID}/nao-contatar"))


def _h(c, tenant=A, papel="owner"):
    return c.projeto.bearer(tenant, papel=papel, plano="premium")


def _marcar(c, cid=CID, tenant=A, papel="owner"):
    return c.post(f"/clientes/{cid}/nao-contatar", headers=_h(c, tenant, papel))


def _desmarcar(c, cid=CID, tenant=A, papel="owner"):
    return c.delete(f"/clientes/{cid}/nao-contatar", headers=_h(c, tenant, papel))


def _com_contato(tenant=A, rec=REC, **extra):
    M._base(tenant, rec, telefone=M.TELEFONE, email=M.EMAIL, nome=M.NOME, **extra)


def _mensagens(rec=REC, tenant=A):
    return cc.mensagens_do_ciclo(M._ciclo(rec, tenant)["id"])


def _evento_de_cancelamento(c, cid=CID, tenant=A, **props):
    corpo = {"userId": cid, "event": "Cancellation Page Viewed",
             "properties": {"mrr": 500.0, "billing_profile": "PJ", **props}}
    r = c.post("/eventos", json=corpo, headers=_h(c, tenant))
    assert r.status_code == 200, r.text


def _linhas_de_retencao(tenant=A):
    conn = sqlite3.connect(rl.caminho_do_banco())
    conn.row_factory = sqlite3.Row
    try:
        return [dict(l) for l in conn.execute(
            "SELECT * FROM ciclos_retencao WHERE tenant_id = ? ORDER BY id", (tenant,))]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _chaves(valor):
    if isinstance(valor, dict):
        for k, v in valor.items():
            yield k
            yield from _chaves(v)
    elif isinstance(valor, list):
        for v in valor:
            yield from _chaves(v)


# == A marca: as rotas =====================================================

class TestMarcaNaoContatar:
    def test_marcar_e_desmarcar(self, cliente):
        _com_contato()
        r = _marcar(cliente)
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["customer_id_externo"] == CID and corpo["ja_estava_marcado"] is False
        assert corpo["nao_contatar"]["origem"] == "empresa" and corpo["nao_contatar"]["marcado_em"]
        assert ci.nao_contatar(A, CID)["origem"] == "empresa"
        de_novo = _marcar(cliente).json()
        assert de_novo["ja_estava_marcado"] is True
        assert de_novo["nao_contatar"] == corpo["nao_contatar"], "a primeira data fica"

        volta = _desmarcar(cliente)
        assert volta.status_code == 200
        assert volta.json() == {"customer_id_externo": CID, "nao_contatar": None,
                                "estava_marcado": True, "origem_da_marca_retirada": "empresa"}
        assert ci.nao_contatar(A, CID) is None
        assert _desmarcar(cliente).json()["estava_marcado"] is False

    @pytest.mark.parametrize("metodo,rota", ROTAS_DA_MARCA)
    @pytest.mark.parametrize("papel", ["membro", None])
    def test_so_dono_e_administrador(self, cliente, metodo, rota, papel):
        _com_contato()
        h = cliente.projeto.bearer(A, papel=papel) if papel else cliente.projeto.bearer(A)
        r = cliente.request(metodo, rota, headers=h)
        assert r.status_code == 403 and r.json()["detail"]["motivo"] == "papel_insuficiente"
        assert ci.nao_contatar(A, CID) is None

    def test_administrador_tambem_marca(self, cliente):
        _com_contato()
        assert _marcar(cliente, papel="admin").status_code == 200

    @pytest.mark.parametrize("metodo,rota", ROTAS_DA_MARCA)
    def test_cliente_de_outra_empresa_e_404_igual_ao_inexistente(self, cliente, metodo, rota):
        _com_contato(A)
        de_outra = cliente.request(metodo, rota, headers=_h(cliente, B))
        inexistente = cliente.request(metodo, rota.replace(CID, "nao-existe"), headers=_h(cliente, B))
        assert de_outra.status_code == inexistente.status_code == 404
        assert de_outra.json() == inexistente.json()
        assert ci.nao_contatar(A, CID) is None and ci.nao_contatar(B, CID) is None

    @pytest.mark.parametrize("metodo,rota", ROTAS_DA_MARCA)
    def test_sem_token_e_401_e_a_chave_de_api_nao_vale(self, cliente, metodo, rota):
        _com_contato()
        assert cliente.request(metodo, rota).status_code == 401
        com_chave = cliente.request(metodo, rota,
                                    headers={"Authorization": "Bearer crai_live_" + "a" * 43})
        assert com_chave.status_code == 401
        assert com_chave.json()["detail"]["motivo"] == "chave_nao_vale_nesta_rota"

    def test_a_marca_de_uma_empresa_nao_vale_na_outra(self, cliente):
        _com_contato(A)
        _com_contato(B)
        assert _marcar(cliente, tenant=A).status_code == 200
        assert ci.nao_contatar(B, CID) is None
        assert ci.marcados_nao_contatar(A).keys() == {CID} and ci.marcados_nao_contatar(B) == {}

    def test_a_marca_sobrevive_a_reimportacao_e_a_alteracao_do_cliente(self, cliente):
        _com_contato()
        _marcar(cliente)
        _com_contato(mrr="450,00")                 # a planilha sobe de novo
        ci.atualizar_parcial(A, CID, {"telefone": "+5511977776666"})
        ci.cancelar(A, CID)
        assert ci.nao_contatar(A, CID)["origem"] == "empresa"

    def test_as_duas_rotas_entram_no_registro_de_acesso(self, cliente):
        _com_contato()
        _marcar(cliente)
        _desmarcar(cliente, papel="admin")
        rotas = [(a["rota"], a["papel"]) for a in registro_acesso.acessos(A)]
        assert (registro_acesso.ROTA_NAO_CONTATAR, "owner") in rotas
        assert (registro_acesso.ROTA_VOLTAR_A_CONTATAR, "admin") in rotas

    def test_a_resposta_nao_traz_contato(self, cliente):
        _com_contato()
        for r in (_marcar(cliente), _desmarcar(cliente)):
            assert M.TELEFONE not in r.text and M.EMAIL not in r.text and "88887777" not in r.text


# == Involuntario: nenhuma mensagem sai ====================================

class TestInvoluntarioNaoContata:
    def test_sem_a_marca_a_mensagem_sai_e_leva_a_linha_de_saida(self, cliente, relogio, envios):
        _com_contato()
        M._falhas(cliente, relogio, REC)
        assert len(envios) == 1 and envios[0]["canal"] == "whatsapp"
        assert envios[0]["texto"].endswith(f"\n{SAIR}\n{AUTOMATICA}")

    def test_com_a_marca_nenhuma_mensagem_sai(self, cliente, relogio, envios):
        _com_contato()
        assert _marcar(cliente).status_code == 200
        M._falhas(cliente, relogio, REC)
        assert envios == [], "saiu mensagem para quem pediu para nao ser contatado"
        mensagens = _mensagens()
        assert len(mensagens) == 3
        assert {m["canal"] for m in mensagens} == {"sem_canal"}
        assert {m["motivo_canal"] for m in mensagens} == {MOTIVO}
        assert all(m["enviada_em"] is None for m in mensagens)
        assert M._ciclo(REC)["estado"] == cc.AGUARDANDO_ESCOLHA

    def test_a_marca_vence_o_canal_presumido_e_o_contato_da_base(self):
        _com_contato()
        ci.marcar_nao_contatar(A, CID, ci.ORIGEM_EMPRESA)
        for config in ({"canais_permitidos": ["whatsapp", "email"], "canal_presumido": "whatsapp"},
                       {"canais_permitidos": ["email"], "canal_presumido": None}):
            assert canal_involuntario.escolher_canal(A, REC, config)["canal"] == "sem_canal"
            assert canal_involuntario.escolher_canal(A, REC, config)["motivo_canal"] == MOTIVO
        assert MOTIVO == canal_involuntario.MOTIVO_NAO_CONTATAR

    def test_as_tentativas_de_cobranca_do_pix_nao_param(self, cliente, relogio, envios):
        _com_contato()
        _marcar(cliente)
        ciclo, _ = M._falhas(cliente, relogio, REC, quantas=3)
        tentativas = cc.tentativas_do_ciclo(ciclo["id"])
        assert [t["numero"] for t in tentativas] == [1, 2, 3]
        assert all(t["disparada_em"] for t in tentativas), "a marca e so de mensagem"
        assert envios == []

    def test_marca_posta_depois_das_sugestoes_segura_o_envio(self, cliente, relogio, envios, producao):
        _com_contato()
        ciclo, quando = M._falhas(cliente, relogio, REC)       # modo escolha: nada sai ainda
        assert envios == [] and M._ciclo(REC)["estado"] == cc.AGUARDANDO_ESCOLHA
        assert {m["canal"] for m in _mensagens()} == {"whatsapp"}
        relogio(quando.replace(hour=10, minute=0))             # dentro da janela de contato
        _marcar(cliente)
        r = cliente.post(f"/ciclos/{ciclo['id']}/mensagens/escolher", headers=_h(cliente),
                         json={"rodada": 1, "abordagem": "facilitacao"})
        assert r.status_code == 200, r.text
        assert r.json()["enviada"] is False and r.json()["espera"] == "sem_canal"
        assert envios == []
        escolhida = next(m for m in _mensagens() if m["escolhida"])
        assert escolhida["canal"] == "sem_canal" and escolhida["motivo_canal"] == MOTIVO
        assert escolhida["nao_entregavel_em"] and escolhida["enviada_em"] is None

    def test_tirada_a_marca_a_mensagem_escolhida_sai(self, cliente, relogio, envios, producao):
        _com_contato()
        ciclo, quando = M._falhas(cliente, relogio, REC)
        agora = quando.replace(hour=10, minute=0)
        relogio(agora)
        _marcar(cliente)
        cliente.post(f"/ciclos/{ciclo['id']}/mensagens/escolher", headers=_h(cliente),
                     json={"rodada": 1, "abordagem": "facilitacao"})
        assert envios == []
        assert _desmarcar(cliente).status_code == 200
        relogio(agora + timedelta(minutes=5))
        M._agendador(agora + timedelta(minutes=5))             # o relogio reconfere o contato
        assert len(envios) == 1 and envios[0]["abordagem"] == "facilitacao"

    def test_o_caminho_direto_sem_ciclo_tambem_nao_fala(self, cliente, relogio, envios):
        import asyncio
        from crai.agent import workflow
        _com_contato()
        _marcar(cliente)
        estado = {"customer_id": REC, "tenant_id": A, "failure_cause": "insufficient_funds",
                  "amount": 100.0, "recovery_score": 50.0, "p_recovery": 0.5}
        final = asyncio.run(workflow.trigger_dunning(estado))
        assert final["dunning_sent"] is False and envios == []

    def test_cliente_de_outra_recorrencia_continua_recebendo(self, cliente, relogio, envios):
        _com_contato()
        _com_contato(rec="RN_titular_2")
        _marcar(cliente)
        M._falhas(cliente, relogio, "RN_titular_2")
        assert len(envios) == 1


# == Voluntario: nenhuma oferta ============================================

class TestVoluntarioNaoContata:
    def test_sem_a_marca_ha_oferta(self, cliente):
        _com_contato()
        _evento_de_cancelamento(cliente)
        linhas = _linhas_de_retencao()
        assert len(linhas) == 1 and linhas[0]["offer_type"] and linhas[0]["offer_sent"] == 1

    def test_com_a_marca_nao_ha_oferta_e_a_decisao_vai_para_a_trilha(self, cliente):
        _com_contato()
        _marcar(cliente)
        _evento_de_cancelamento(cliente)
        linhas = _linhas_de_retencao()
        assert len(linhas) == 1
        assert linhas[0]["offer_type"] is None and not linhas[0]["offer_sent"]
        assert linhas[0]["risk_score"] >= va.CORTE_DE_INTERVENCAO
        decisoes = rl.decisoes_do_sujeito(A, f"user:{CID}")
        da_regra = [d for d in decisoes if d["saida"].get("regra") == va.REGRA_DO_NAO_CONTATAR]
        assert len(da_regra) == 1 and da_regra[0]["tipo_decisao"] == rl.TIPO_OFERTA
        assert "o cliente pediu para não receber mensagens" in da_regra[0]["explicacao"]
        assert rl.verificar_cadeia(A)["integra"] is True

    def test_a_marca_vale_para_qualquer_canal(self, cliente, monkeypatch):
        enviados = []

        async def espiao(to, message, tenant_id=None):
            enviados.append(to)
            return {"sent": True, "channel": "whatsapp", "to": "***", "motivo": None,
                    "simulado": True, "tenant_id": tenant_id}
        monkeypatch.setattr(va, "send_whatsapp", espiao)
        _com_contato()
        _marcar(cliente)
        for props in ({"phone": M.TELEFONE}, {"on_site_now": True}, {"on_site_now": False}):
            _evento_de_cancelamento(cliente, **props, days_since_last=float(len(enviados)))
        assert enviados == [] and all(l["offer_type"] is None for l in _linhas_de_retencao())

    def test_o_envio_relê_a_marca_posta_depois_da_escolha(self):
        import asyncio
        _com_contato()
        estado = {"tenant_id": A, "user_id": f"user:{CID}", "channel": "email",
                  "message": "Oferta", "props": {}, "offer_type": "desconto_10"}
        antes = asyncio.run(va.send_offer(dict(estado)))
        assert antes["offer_sent"] is True and antes["message"] == f"Oferta\n{SAIR}"
        ci.marcar_nao_contatar(A, CID, ci.ORIGEM_RESPOSTA_SAIR)
        depois = asyncio.run(va.send_offer(dict(estado)))
        assert depois["offer_sent"] is False and depois["sem_oferta_por"] == "nao_contatar"

    def test_o_disparo_em_lote_pula_o_marcado(self):
        linha = {"customer_id_externo": CID, "criticality": "critico", "risk_score": 0.95}
        assert dl.motivo_para_pular(linha, set(), {}, {CID}) == (
            "nao_contatar", "o cliente pediu para não receber mensagens")
        assert dl.motivo_para_pular(linha, set(), {}, {"outro"}) is None
        assert dl.motivo_para_pular(linha, set(), {}, None) is None

    def test_o_marcado_de_outra_empresa_nao_bloqueia(self, cliente):
        _com_contato(A)
        _com_contato(B)
        _marcar(cliente, tenant=B)
        _evento_de_cancelamento(cliente, tenant=A)
        assert _linhas_de_retencao(A)[0]["offer_sent"] == 1

    def test_tirada_a_marca_a_oferta_volta(self, cliente):
        _com_contato()
        _marcar(cliente)
        _evento_de_cancelamento(cliente)
        _desmarcar(cliente)
        _evento_de_cancelamento(cliente, days_since_last=3.0)
        assert [bool(l["offer_sent"]) for l in _linhas_de_retencao()] == [False, True]


# == A linha "responda SAIR" ===============================================

class TestLinhaDeSaida:
    def test_a_frase_e_uma_so_e_mora_na_configuracao(self):
        assert crai_config.AVISO_SAIR == SAIR
        assert dunning_engine.AVISO_AUTOMATICO == AUTOMATICA

    def test_com_aviso_poe_as_duas_linhas_na_ordem_e_uma_vez_so(self):
        esperado = f"Olá!\n{SAIR}\n{AUTOMATICA}"
        assert dunning_engine.com_aviso("Olá!") == esperado
        assert dunning_engine.com_aviso(esperado) == esperado
        assert dunning_engine.com_aviso(f"Olá!\n{AUTOMATICA}") == esperado
        assert dunning_engine.com_aviso(f"Olá!\n{SAIR}") == esperado
        assert dunning_engine.com_aviso(dunning_engine.com_aviso(esperado + "\n")) == esperado

    def test_as_tres_sugestoes_do_involuntario_levam_as_duas_linhas(self, cliente, relogio, producao):
        _com_contato()
        M._falhas(cliente, relogio, REC)
        textos = [m["texto"] for m in _mensagens()]
        assert len(textos) == 3
        for texto in textos:
            assert texto.endswith(f"\n{SAIR}\n{AUTOMATICA}"), texto[-120:]
            assert texto.count(SAIR) == 1 and texto.count(AUTOMATICA) == 1

    def test_o_texto_escrito_pelo_llm_tambem_leva(self, cliente, relogio, producao, monkeypatch):
        from types import SimpleNamespace

        async def llm(**kwargs):
            sugestoes = {a: f"Olá! Texto do redator para {a}, com o link {{link}} e mais palavras "
                            "para passar da validação de tamanho mínimo." for a in cc.ABORDAGENS}
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(sugestoes))])
        monkeypatch.setattr(dunning_engine.claude.messages, "create", llm)
        _com_contato()
        M._falhas(cliente, relogio, REC)
        mensagens = _mensagens()
        assert any(m["origem_texto"] == "llm" for m in mensagens), "o texto do LLM foi recusado"
        for m in mensagens:
            assert m["texto"].endswith(f"\n{SAIR}\n{AUTOMATICA}")

    def test_com_linha_de_saida_do_voluntario(self):
        assert va.com_linha_de_saida("Oferta") == f"Oferta\n{SAIR}"
        assert va.com_linha_de_saida(f"Oferta\n{SAIR}\n") == f"Oferta\n{SAIR}"
        assert va.CANAIS_COM_LINHA_DE_SAIDA == ("whatsapp", "email")

    @pytest.mark.parametrize("props,canal,tem_linha", [
        ({"phone": M.TELEFONE}, "whatsapp", True), ({"on_site_now": False}, "email", True),
        ({"on_site_now": True}, "popup", False)])
    def test_a_oferta_leva_a_linha_onde_o_cliente_pode_responder(self, cliente, monkeypatch,
                                                                 props, canal, tem_linha):
        # As rotas `/simulate/*` só respondem em desenvolvimento. O teste liga o modo
        # ele mesmo: antes dependia de um `.env` com ENV=development na máquina.
        monkeypatch.setenv("ENV", "development")

        async def recusa(*a, **k):
            raise RuntimeError("sem LLM no teste")
        monkeypatch.setattr(va.claude.messages, "create", recusa)
        final = {}
        original = va.send_offer

        async def espiao(state):
            r = await original(state)
            final.update(r)
            return r
        monkeypatch.setattr(va, "send_offer", espiao)
        r = cliente.post("/simulate/painel/evento-risco", json=props).json()
        assert r["channel"] == canal
        assert r["message"].endswith(SAIR) is tem_linha
        vencedora = next(c for c in r["candidatas"] if c["escolhida"])
        assert vencedora["texto"] == r["message"]


# == A resposta SAIR, simulada =============================================

class TestRespostaSair:
    @pytest.fixture(autouse=True)
    def _dev(self, monkeypatch):
        monkeypatch.setenv("ENV", "development")

    def _sair(self, c, tenant=A, **corpo):
        return c.post("/simulate/resposta-sair", json=corpo, headers={"x-tenant-id": tenant})

    def test_pela_recorrencia_marca_o_cliente_e_a_mensagem_seguinte_nao_sai(self, cliente, relogio, envios):
        _com_contato()
        r = self._sair(cliente, id_recorrencia=REC)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "cliente_marcado" and r.json()["ja_estava_marcado"] is False
        assert r.json()["nao_contatar"]["origem"] == "resposta_sair"
        assert ci.nao_contatar(A, CID)["origem"] == "resposta_sair"
        M._falhas(cliente, relogio, REC)
        assert envios == []
        assert {m["motivo_canal"] for m in _mensagens()} == {MOTIVO}

    def test_pelo_identificador_do_cliente_e_idempotente(self, cliente):
        _com_contato()
        primeiro = self._sair(cliente, customer_id_externo=CID).json()
        segundo = self._sair(cliente, customer_id_externo=CID).json()
        assert segundo["ja_estava_marcado"] is True
        assert segundo["nao_contatar"] == primeiro["nao_contatar"]

    def test_cliente_que_o_sistema_so_conhece_por_evento_tambem_e_marcado(self, cliente):
        assert self._sair(cliente, customer_id_externo="so-do-sdk").status_code == 200
        _evento_de_cancelamento(cliente, cid="so-do-sdk")
        assert _linhas_de_retencao()[0]["offer_type"] is None

    def test_a_empresa_pode_tirar_a_marca_da_resposta_sair(self, cliente):
        _com_contato()
        self._sair(cliente, id_recorrencia=REC)
        volta = _desmarcar(cliente).json()
        assert volta["estava_marcado"] is True and volta["origem_da_marca_retirada"] == "resposta_sair"

    @pytest.mark.parametrize("corpo", [{}, {"id_recorrencia": REC, "customer_id_externo": CID},
                                       {"id_recorrencia": "", "customer_id_externo": ""}])
    def test_precisa_de_um_identificador_e_so_um(self, cliente, corpo):
        _com_contato()
        assert self._sair(cliente, **corpo).status_code == 422
        assert ci.nao_contatar(A, CID) is None

    def test_recorrencia_fora_da_base_e_404(self, cliente):
        assert self._sair(cliente, id_recorrencia="RN_nao_existe").status_code == 404

    def test_recorrencia_de_outra_empresa_nao_marca(self, cliente):
        _com_contato(A)
        assert self._sair(cliente, tenant=B, id_recorrencia=REC).status_code == 404
        assert ci.nao_contatar(A, CID) is None

    def test_fora_de_development_e_demo_a_rota_responde_403(self, cliente, monkeypatch):
        _com_contato()
        monkeypatch.setenv("ENV", "production")
        assert self._sair(cliente, id_recorrencia=REC).status_code == 403
        assert ci.nao_contatar(A, CID) is None


# == Exportar (art. 18) ====================================================

@pytest.fixture
def titular(cliente, relogio, envios):
    """Um titular com tudo: cadastro com contato, um ciclo com 3 tentativas e 3
    mensagens (uma enviada), uma oferta de retencao e decisoes na trilha."""
    _com_contato()
    M._falhas(cliente, relogio, REC)
    _evento_de_cancelamento(cliente)
    assert len(envios) == 1
    return cliente


def _exportar(c, cid=CID, tenant=A, papel="owner"):
    return c.post("/titular/exportar", json={"customer_id_externo": cid}, headers=_h(c, tenant, papel))


def _anonimizar(c, cid=CID, tenant=A, papel="owner"):
    return c.post("/titular/anonimizar", json={"customer_id_externo": cid}, headers=_h(c, tenant, papel))


class TestExportar:
    def test_traz_o_que_a_crai_guarda_do_titular(self, titular):
        r = _exportar(titular)
        assert r.status_code == 200, r.text
        e = r.json()
        assert e["customer_id_externo"] == CID and e["exportado_em"]
        assert e["cadastro"]["nome"] == M.NOME and e["cadastro"]["id_recorrencia"] == REC
        assert e["cadastro"]["mrr"] == 299.9
        assert e["contatos_guardados"] == {"email": True, "telefone": True}
        assert e["nao_contatar"] is None
        assert len(e["cobrancas"]) == 1
        cobranca = e["cobrancas"][0]
        assert cobranca["valor_da_cobranca"] == M.VALOR and len(cobranca["tentativas"]) == 3
        assert len(cobranca["mensagens"]) == 3
        assert sum(1 for m in cobranca["mensagens"] if m["enviada_em"]) == 1
        assert all(m["texto"] and m["texto"].endswith(AUTOMATICA) for m in cobranca["mensagens"])
        assert len(e["retencao"]) == 1 and e["retencao"][0]["evento"] == "Cancellation Page Viewed"
        assert e["retencao"][0]["oferta_enviada"] is True
        dominios = {d["dominio"] for d in e["decisoes_automatizadas"]}
        assert dominios == {"involuntario", "voluntario"}
        assert all(d["explicacao"] for d in e["decisoes_automatizadas"])
        assert "Art. 18" in e["base_legal"]["dispositivo"]

    def test_nao_devolve_o_valor_do_contato_nem_a_fee(self, titular):
        r = _exportar(titular)
        for proibido in (M.TELEFONE, "88887777", M.EMAIL):
            assert proibido not in r.text, proibido
        chaves = set(_chaves(r.json()))
        assert not [k for k in chaves if "fee" in k]
        assert not chaves & {"email", "telefone", "cpf", "chave_pix", "phone", "alpha", "beta"} - {
            "email", "telefone"}                 # as duas so aparecem em `contatos_guardados`
        assert set(r.json()["cadastro"]) & {"email", "telefone", "cpf"} == set()

    @pytest.mark.parametrize("papel", ["membro", None])
    def test_so_dono_e_administrador(self, titular, papel):
        h = titular.projeto.bearer(A, papel=papel) if papel else titular.projeto.bearer(A)
        for rota in ("/titular/exportar", "/titular/anonimizar"):
            r = titular.post(rota, json={"customer_id_externo": CID}, headers=h)
            assert r.status_code == 403 and r.json()["detail"]["motivo"] == "papel_insuficiente"
        assert ci.obter(A, CID)["telefone"] == M.TELEFONE

    def test_administrador_exporta(self, titular):
        assert _exportar(titular, papel="admin").status_code == 200

    @pytest.mark.parametrize("rota", ["/titular/exportar", "/titular/anonimizar"])
    def test_titular_de_outra_empresa_e_404_igual_ao_inexistente(self, titular, rota):
        de_outra = titular.post(rota, json={"customer_id_externo": CID}, headers=_h(titular, B))
        inexistente = titular.post(rota, json={"customer_id_externo": "nao-existe"},
                                   headers=_h(titular, B))
        assert de_outra.status_code == inexistente.status_code == 404
        assert de_outra.json() == inexistente.json()
        assert de_outra.json()["detail"]["motivo"] == "titular_nao_encontrado"
        assert ci.obter(A, CID)["telefone"] == M.TELEFONE, "a outra empresa nao anonimiza o meu cliente"
        assert ci.nao_contatar(A, CID) is None

    @pytest.mark.parametrize("rota", ["/titular/exportar", "/titular/anonimizar"])
    def test_sem_token_e_401_e_a_chave_de_api_nao_vale(self, titular, rota):
        corpo = {"customer_id_externo": CID}
        assert titular.post(rota, json=corpo).status_code == 401
        r = titular.post(rota, json=corpo, headers={"Authorization": "Bearer crai_live_" + "a" * 43})
        assert r.status_code == 401 and r.json()["detail"]["motivo"] == "chave_nao_vale_nesta_rota"

    @pytest.mark.parametrize("rota", ["/titular/exportar", "/titular/anonimizar"])
    @pytest.mark.parametrize("corpo", [{}, {"customer_id_externo": ""}, {"customer_id_externo": 7},
                                       {"customer_id_externo": "x" * 201},
                                       {"customer_id_externo": CID, "tenant_id": B}, {"id": CID}])
    def test_corpo_invalido_e_422(self, titular, rota, corpo):
        assert titular.post(rota, json=corpo, headers=_h(titular)).status_code == 422
        assert ci.obter(A, CID)["telefone"] == M.TELEFONE

    def test_entra_no_registro_de_acesso_sem_o_titular(self, titular):
        antes = len(registro_acesso.acessos(A))
        _exportar(titular, papel="admin")
        acessos = registro_acesso.acessos(A)
        assert len(acessos) == antes + 1
        novo = acessos[0]                        # do mais recente ao mais antigo
        assert (novo["rota"], novo["papel"]) == (registro_acesso.ROTA_EXPORTAR, "admin")
        assert CID not in json.dumps(novo, default=str)

    def test_quem_o_sistema_so_conhece_por_evento_tambem_e_exportado(self, cliente):
        _evento_de_cancelamento(cliente, cid="so-do-sdk")
        e = _exportar(cliente, cid="so-do-sdk").json()
        assert e["cadastro"] is None and e["contatos_guardados"] == {"email": False, "telefone": False}
        assert len(e["retencao"]) == 1 and e["cobrancas"] == []

    def test_exportar_nao_altera_nada(self, titular):
        antes = (ci.obter(A, CID), _mensagens(), rl.verificar_cadeia(A))
        _exportar(titular)
        assert (ci.obter(A, CID), _mensagens(), rl.verificar_cadeia(A)) == antes


# == Anonimizar (art. 18) ==================================================

class TestAnonimizar:
    def test_apaga_os_contatos_e_o_texto_das_mensagens_e_marca_nao_contatar(self, titular):
        r = _anonimizar(titular)
        assert r.status_code == 200, r.text
        a = r.json()
        assert sorted(a["contatos_apagados"]) == ["email", "nome", "telefone"]
        assert a["mensagens_apagadas"] == 3 and a["ciclos_com_mensagem_apagada"] == 1
        assert a["nao_contatar"]["origem"] == "anonimizacao"
        linha = ci.obter(A, CID)
        assert (linha["nome"], linha["email"], linha["telefone"]) == (None, None, None)
        assert linha["mrr"] == 299.9 and linha["id_recorrencia"] == REC, "o agregado fica"
        mensagens = _mensagens()
        assert len(mensagens) == 3
        assert all(m["texto"] is None and m["texto_apagado_em"] for m in mensagens)
        assert all(m["abordagem"] and m["canal"] for m in mensagens), "a linha fica, sem o texto"
        assert ci.nao_contatar(A, CID)["origem"] == "anonimizacao"

    def test_e_idempotente(self, titular):
        primeiro = _anonimizar(titular).json()
        segundo = _anonimizar(titular).json()
        assert segundo["contatos_apagados"] == [] and segundo["mensagens_apagadas"] == 0
        assert segundo["nao_contatar"] == primeiro["nao_contatar"]

    def test_as_metricas_agregadas_nao_mudam(self, titular):
        rotas = ("/metrics/visao-geral", "/metrics/involuntario/mes", "/metrics/involuntario/funil",
                 "/metrics/o-que-funciona", "/metrics/voluntario/mes", "/metrics/serie")

        def foto():
            return {rota: titular.get(rota, headers=_h(titular)).json() for rota in rotas}
        antes = foto()
        assert antes["/metrics/involuntario/funil"]["etapas"][0]["chegaram"] == 1
        _anonimizar(titular)
        assert foto() == antes

    def test_a_trilha_do_art_20_nao_e_tocada(self, titular):
        antes = rl.verificar_cadeia(A)
        decisoes = rl.decisoes_do_sujeito(A, REC, limite=500) + rl.decisoes_do_sujeito(
            A, f"user:{CID}", limite=500)
        assert antes["integra"] is True and antes["linhas"] >= 4
        conn = sqlite3.connect(rl.caminho_do_banco())
        try:
            bruto = conn.execute("SELECT * FROM decisoes_automatizadas ORDER BY id").fetchall()
        finally:
            conn.close()
        _anonimizar(titular)
        depois = rl.verificar_cadeia(A)
        assert depois["linhas"] == antes["linhas"] and depois["ultimo_hash"] == antes["ultimo_hash"]
        assert depois["integra"] is True and depois["inicio_truncado"] is False
        conn = sqlite3.connect(rl.caminho_do_banco())
        try:
            assert conn.execute("SELECT * FROM decisoes_automatizadas ORDER BY id").fetchall() == bruto
        finally:
            conn.close()
        assert rl.decisoes_do_sujeito(A, REC, limite=500) + rl.decisoes_do_sujeito(
            A, f"user:{CID}", limite=500) == decisoes

    def test_o_modulo_das_rotas_nao_escreve_na_trilha(self):
        fonte = inspect.getsource(titular_api)
        for proibido in ("apagar_trilha_expirada(", "registrar_decisao(", "registrar_decisoes(",
                         "INTO decisoes_automatizadas", "FROM decisoes_automatizadas",
                         "DELETE FROM", "UPDATE ", "INSERT ", "sqlite3"):
            assert proibido not in fonte, proibido

    def test_depois_de_anonimizar_nada_mais_sai_e_a_marca_nao_volta(self, titular, envios):
        _anonimizar(titular)
        antes = len(envios)
        volta = _desmarcar(titular)
        assert volta.status_code == 409 and volta.json()["detail"]["motivo"] == "marca_da_anonimizacao"
        assert ci.nao_contatar(A, CID)["origem"] == "anonimizacao"
        _evento_de_cancelamento(titular, days_since_last=9.0)
        assert _linhas_de_retencao()[-1]["offer_type"] is None
        assert len(envios) == antes

    def test_as_tentativas_de_cobranca_continuam_depois_de_anonimizar(self, cliente, relogio, envios):
        _com_contato()
        _anonimizar(cliente)
        ciclo, _ = M._falhas(cliente, relogio, REC)
        assert all(t["disparada_em"] for t in cc.tentativas_do_ciclo(ciclo["id"]))
        assert envios == []

    def test_a_exportacao_depois_mostra_o_que_ficou(self, titular):
        _anonimizar(titular)
        e = _exportar(titular).json()
        assert e["contatos_guardados"] == {"email": False, "telefone": False}
        assert e["cadastro"]["nome"] is None and e["cadastro"]["mrr"] == 299.9
        assert e["nao_contatar"]["origem"] == "anonimizacao"
        assert all(m["texto"] is None and m["texto_apagado_em"]
                   for m in e["cobrancas"][0]["mensagens"])
        assert len(e["decisoes_automatizadas"]) >= 4, "a trilha continua"
        assert M.NOME not in json.dumps(e, ensure_ascii=False)

    def test_o_painel_do_ciclo_fica_sem_o_nome_e_sem_o_texto(self, titular):
        ciclo_id = M._ciclo(REC)["id"]
        _anonimizar(titular)
        detalhe = titular.get(f"/ciclos/{ciclo_id}", headers=_h(titular)).json()
        assert detalhe["ciclo"]["cliente_nome"] is None
        assert all(m["texto"] is None for m in detalhe["mensagens"])
        assert M.NOME not in json.dumps(detalhe, ensure_ascii=False)

    def test_entra_no_registro_de_acesso(self, titular):
        _anonimizar(titular)
        rotas = [a["rota"] for a in registro_acesso.acessos(A)]
        assert rotas.count(registro_acesso.ROTA_ANONIMIZAR) == 1

    def test_o_cliente_de_outra_empresa_com_o_mesmo_id_fica_inteiro(self, titular):
        _com_contato(B)
        _anonimizar(titular, tenant=A)
        assert ci.obter(B, CID)["telefone"] == M.TELEFONE and ci.nao_contatar(B, CID) is None

    def test_o_log_nao_leva_o_titular(self, titular, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            _exportar(titular)
            _anonimizar(titular)
        assert "[ART18]" in caplog.text
        for proibido in (CID, M.NOME, M.TELEFONE, M.EMAIL):
            assert proibido not in caplog.text


# == O texto para a politica ===============================================

class TestTextoParaAPolitica:
    def test_e_um_arquivo_versionado_em_docs_lgpd(self):
        caminho = titular_api.CAMINHO_DA_POLITICA
        assert caminho.exists(), caminho
        assert caminho.parent.name == "lgpd" and caminho.parent.parent.name == "docs"
        assert caminho == Path(app_module.__file__).resolve().parents[3] / "docs" / "lgpd" / \
            "texto-para-politica-de-privacidade.md"

    @pytest.mark.parametrize("papel", ["owner", "admin", "membro"])
    def test_a_rota_devolve_o_texto_com_os_prazos_da_empresa(self, cliente, producao, papel):
        r = cliente.get("/titular/texto-para-politica", headers=_h(cliente, A, papel))
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["titulo"] == "O que a CRAI faz com os dados dos nossos clientes"
        assert corpo["prazos"] == {"intervalo_minimo_ofertas_dias": 30, "retencao_mensagens_dias": 90,
                                   "retencao_trilha_anos": 5}
        texto = corpo["texto"]
        assert "{{" not in texto and "}}" not in texto
        assert "a cada 30 dias" in texto and "apagado 90 dias depois" in texto
        assert "guardado por 5 anos" in texto and "responda SAIR" in texto
        assert "Nota para quem mantém" not in texto and not texto.startswith("#")
        assert texto[0].isupper()

    def test_os_prazos_acompanham_a_configuracao_da_empresa(self, cliente, producao):
        configuracao.gravar(A, {"intervalo_minimo_ofertas_dias": 7, "retencao_mensagens_dias": 45})
        da_a = cliente.get("/titular/texto-para-politica", headers=_h(cliente, A)).json()["texto"]
        da_b = cliente.get("/titular/texto-para-politica", headers=_h(cliente, B)).json()["texto"]
        assert "a cada 7 dias" in da_a and "apagado 45 dias depois" in da_a
        assert "a cada 30 dias" in da_b and "apagado 90 dias depois" in da_b

    def test_sem_token_e_401(self, cliente):
        assert cliente.get("/titular/texto-para-politica").status_code == 401

    def test_sem_o_arquivo_a_rota_diz_que_falta(self, cliente, monkeypatch, tmp_path):
        monkeypatch.setattr(titular_api, "CAMINHO_DA_POLITICA", tmp_path / "nao-existe.md")
        r = cliente.get("/titular/texto-para-politica", headers=_h(cliente))
        assert r.status_code == 503 and r.json()["detail"]["motivo"] == "texto_indisponivel"

    def test_o_texto_nao_promete_o_que_o_sistema_nao_faz(self):
        texto = titular_api.CAMINHO_DA_POLITICA.read_text(encoding="utf-8")
        # O expurgo dos ciclos (24 meses) ainda nao e executado: o texto nao o promete.
        assert "24 meses" not in texto and "anonimizados" not in texto
        for chave in titular_api.PRAZOS_DO_TEXTO:
            assert "{{" + chave + "}}" in texto, chave

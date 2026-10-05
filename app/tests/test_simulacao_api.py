"""tests/test_simulacao_api.py - Rodada 3, Fase 3: a simulacao do gateway.

O QUE ESTE ARQUIVO MEDE (Portao B):

  - o ciclo simulado INTEIRO, so pelas rotas, nos dois desfechos: recuperado na
    2a tentativa; e tres falhas, mensagem e pagamento;
  - a retencao simulada: o aceite vem da propensao escondida, nao do bandit;
  - a verdade escondida nunca entra nas features de nenhum modelo;
  - nada da simulacao entra no ciclo real, no dataset de treino, na trilha do
    Art. 20 real, no bandit real nem nas metricas reais; o relogio de verdade
    nao dispara tentativa simulada;
  - o relogio simulado e por empresa, e a limpeza apaga tudo o que e ficticio;
  - o formulario recusa dado que pareca real;
  - isolamento por tenant, papel e 401;
  - PSP e CRM ficam simulados dentro da simulacao, mesmo com credencial.
"""

import copy
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crai import ambiente, config, simulador
from crai.agent import workflow
from crai.api import app as app_module
from crai.api import datas
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import offer_bandit
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import voluntary_agent as va
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao, dunning_engine, recovery_log, retry_scheduler
from crai.integrations import pagarme_gateway

A, B = "empresa-a", "empresa-b"
APP = Path(__file__).resolve().parents[1]
ROTAS_DE_ESCRITA = (("POST", "/simulacao/cliente"), ("POST", "/simulacao/cobrar"),
                    ("POST", "/simulacao/avancar"), ("POST", "/simulacao/retencao"),
                    ("DELETE", "/simulacao"))
VERDADE = {"dias_ate_saldo": 3, "chance_pagar": 1.0, "vai_revogar": False}
PROPENSAO_TOTAL = {o: 1.0 for o in offer_bandit.OFFERS}
PROPENSAO_NENHUMA = {o: 0.0 for o in offer_bandit.OFFERS}


@pytest.fixture
def cliente(monkeypatch, supabase_falso):
    monkeypatch.setenv(datas.ENV_FUSO, "America/Sao_Paulo")
    monkeypatch.delenv(config.ENV_SUCCESS_FEE, raising=False)
    monkeypatch.delenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, raising=False)

    async def recusa(*a, **k):
        raise RuntimeError("sem LLM no teste")
    monkeypatch.setattr(dunning_engine.claude.messages, "create", recusa)
    monkeypatch.setattr(va.claude.messages, "create", recusa)
    with TestClient(app_module.app) as c:
        c.projeto = supabase_falso
        yield c


def _h(c, tenant=A, papel="owner", plano="premium"):
    return c.projeto.bearer(tenant, papel=papel, plano=plano)


def _corpo(nome="Ana Souza", mensalidade=300.0, perfil="clt", **verdade):
    return {"nome": nome, "mensalidade": mensalidade, "perfil": perfil,
            "verdade": {**VERDADE, **verdade}}


def _criar(c, tenant=A, **kw):
    r = c.post("/simulacao/cliente", json=_corpo(**kw), headers=_h(c, tenant))
    assert r.status_code == 200, r.text
    return r.json()


def _cobrar(c, tenant=A):
    r = c.post("/simulacao/cobrar", headers=_h(c, tenant))
    assert r.status_code == 200, r.text
    return r.json()


def _avancar(c, tenant=A, **corpo):
    r = c.post("/simulacao/avancar", json=corpo or {"ate_proxima_acao": True}, headers=_h(c, tenant))
    assert r.status_code == 200, r.text
    return r.json()


def _estado(c, tenant=A, **kw):
    r = c.get("/simulacao", headers=_h(c, tenant, **kw))
    assert r.status_code == 200, r.text
    return r.json()


def _limpar(c, tenant=A):
    r = c.delete("/simulacao", headers=_h(c, tenant))
    assert r.status_code == 200, r.text
    return r.json()


def _dias_das_tentativas(estado) -> list:
    """Em quantos dias, a contar da cobranca, cai cada tentativa agendada."""
    cobrada = datetime.fromisoformat(estado["cobranca"]["em"]).date()
    return [(datetime.fromisoformat(t["agendada_para"]).date() - cobrada).days
            for t in estado["tentativas"]]


def _plano(c, nome="Ana Souza", tenant=A) -> list:
    """Sonda: em que dias o sistema agenda as tentativas para ESTE cliente. O id
    do cliente ficticio sai do nome, entao a mesma simulacao, depois de limpa,
    repete o mesmo plano."""
    _criar(c, tenant, nome=nome, dias_ate_saldo=15)
    dias = _dias_das_tentativas(_cobrar(c, tenant))
    _limpar(c, tenant)
    assert len(dias) == 3 and dias == sorted(dias) and dias[0] >= 1, dias
    return dias


def _chaves(valor):
    if isinstance(valor, dict):
        for k, v in valor.items():
            yield k
            yield from _chaves(v)
    elif isinstance(valor, list):
        for v in valor:
            yield from _chaves(v)


def _estado_do_real(tenant=A) -> dict:
    """Tudo o que e REAL desta empresa, lido fora da simulacao."""
    assert not ambiente.simulacao_ativa()
    return {"ciclos": cc.listar_ciclos(tenant, limite=200),
            "dataset": recovery_log.linhas(tenant),
            "retencao": rl.ciclos_com_oferta(tenant),
            "trilha": rl.verificar_cadeia(tenant),
            "bandit": copy.deepcopy(va._bandit.state)}


# == O ciclo inteiro, so pelas rotas =======================================

class TestCicloInteiro:
    def test_recuperado_na_segunda_tentativa(self, cliente):
        d1, d2, _ = _plano(cliente)
        if d1 == d2:
            pytest.skip("o plano pos as tentativas 1 e 2 no mesmo dia")
        _criar(cliente, dias_ate_saldo=d2)          # o dinheiro entra no dia da 2a tentativa
        e = _cobrar(cliente)
        assert e["cobranca"] == {"feita": True, "em": e["cobranca"]["em"], "resultado": "falhou",
                                 "causa": "insufficient_funds", "causa_legivel": "Saldo insuficiente"}
        assert e["ciclo"]["ciclo"]["estado"] == "recobrando" and e["ciclo"]["ciclo"]["simulado"] is True
        assert e["ciclo"]["ciclo"]["id"] > simulador.ID_DA_SIMULACAO
        assert e["ciclo"]["ciclo"]["cliente_nome"] == "Ana Souza"
        assert [t["resultado"] for t in e["tentativas"]] == ["pendente"] * 3
        assert e["proxima_acao"]["descricao"] == "Tentativa 1" and e["desfecho"] is None
        assert 0 < e["chance_recuperar"] <= 1

        e = _avancar(cliente)                        # tentativa 1: ainda sem saldo
        assert [t["resultado"] for t in e["tentativas"]] == ["falhou", "pendente", "pendente"]
        assert e["tentativas"][0]["causa"] == "insufficient_funds"
        assert e["proxima_acao"]["descricao"] == "Tentativa 2" and e["ciclo"]["mensagens"] == []

        e = _avancar(cliente)                        # tentativa 2: o dinheiro entrou
        assert [t["resultado"] for t in e["tentativas"]] == ["falhou", "paga", "cancelada"]
        assert e["ciclo"]["ciclo"]["estado"] == "recuperado"
        assert e["desfecho"]["tipo"] == "recuperado" and e["desfecho"]["via"] == "tentativa"
        assert e["desfecho"]["tentativa"] == 2 and e["desfecho"]["valor_liquido"] == 255.0
        assert e["proxima_acao"] is None and e["ciclo"]["mensagens"] == []
        assert "Pagamento recuperado na 2ª tentativa" in e["pensando"][-1]
        assert e["sem_crai"]["resultado"] == "perdido"
        # A fee nao aparece no estado da simulacao (R11): so o liquido.
        assert "fee" not in set(_chaves(e))

    def test_tres_falhas_mensagem_e_pagamento(self, cliente):
        _, _, d3 = _plano(cliente)
        assert d3 + 1 <= simulador.DIAS_ATE_SALDO_MAX
        configuracao.gravar(A, {"modo_mensagem_involuntario": "escolha",
                                "janela_contato_inicio": "00:00", "janela_contato_fim": "24:00"})
        _criar(cliente, dias_ate_saldo=d3 + 1)       # o dinheiro so entra depois da 3a tentativa
        _cobrar(cliente)
        for numero in (1, 2):
            e = _avancar(cliente)
            assert e["tentativas"][numero - 1]["resultado"] == "falhou"
            assert e["ciclo"]["mensagens"] == [], "nenhuma mensagem antes de as tres falharem (R1)"
        e = _avancar(cliente)                        # a 3a falha: nascem as 3 sugestoes
        assert [t["resultado"] for t in e["tentativas"]] == ["falhou"] * 3
        assert e["ciclo"]["ciclo"]["estado"] == "aguardando_escolha"
        assert len(e["ciclo"]["mensagens"]) == 3 and e["modo_mensagem"] == "escolha"
        assert all(m["canal"] == "whatsapp" for m in e["ciclo"]["mensagens"])
        assert e["proxima_acao"]["descricao"] == "Envio automático da recomendada"

        # A empresa escolhe pela MESMA rota de sempre, com o id do ciclo simulado.
        ciclo_id = e["ciclo"]["ciclo"]["id"]
        r = cliente.post(f"/ciclos/{ciclo_id}/mensagens/escolher", headers=_h(cliente),
                         json={"rodada": 1, "abordagem": "facilitacao"})
        assert r.status_code == 200 and r.json()["enviada"] is True
        assert r.json()["ciclo_id"] == ciclo_id
        e = _estado(cliente)
        assert e["ciclo"]["ciclo"]["estado"] == "mensagem_enviada"
        assert e["ciclo"]["escolhida_por"] == "owner"
        assert e["proxima_acao"]["descricao"] == "Prazo para resposta do cliente"

        e = _avancar(cliente)                        # 2 dias depois: o cliente paga
        assert e["resposta_a_mensagem"] == "pagou"
        assert e["ciclo"]["ciclo"]["estado"] == "recuperado"
        assert e["desfecho"] == {"tipo": "recuperado", "via": "mensagem", "tentativa": None,
                                 "valor_liquido": 255.0, "em": e["desfecho"]["em"]}
        assert "Pagamento recuperado depois da mensagem" in e["pensando"][-1]
        tipos = [ev["tipo"] for ev in e["ciclo"]["linha_do_tempo"]]
        for esperado in ("abertura", "tentativa_resultado", "sugestoes_geradas",
                         "mensagem_escolhida", "mensagem_enviada", "recuperado"):
            assert esperado in tipos, esperado

    def test_no_modo_automatico_a_recomendada_sai_sozinha(self, cliente):
        _, _, d3 = _plano(cliente)
        _criar(cliente, dias_ate_saldo=d3 + 1)
        _cobrar(cliente)
        for _ in range(3):
            e = _avancar(cliente)
        assert e["ciclo"]["ciclo"]["estado"] == "mensagem_enviada"
        assert e["ciclo"]["escolhida_por"] == "automatico" and e["modo_mensagem"] == "automatico"

    def test_sem_escolha_o_prazo_envia_a_recomendada(self, cliente):
        _, _, d3 = _plano(cliente)
        configuracao.gravar(A, {"modo_mensagem_involuntario": "escolha", "prazo_escolha_horas": 8,
                                "janela_contato_inicio": "00:00", "janela_contato_fim": "24:00"})
        _criar(cliente, dias_ate_saldo=d3 + 1)
        _cobrar(cliente)
        for _ in range(3):
            e = _avancar(cliente)
        assert e["ciclo"]["ciclo"]["estado"] == "aguardando_escolha"
        antes = datetime.fromisoformat(e["relogio"]["agora"])
        e = _avancar(cliente)
        assert e["ciclo"]["ciclo"]["estado"] == "mensagem_enviada"
        assert e["ciclo"]["escolhida_por"] == "prazo"
        assert datetime.fromisoformat(e["relogio"]["agora"]) - antes >= timedelta(hours=8)

    def test_quem_nao_paga_depois_da_mensagem_vira_encerrado_no_prazo(self, cliente):
        _, _, d3 = _plano(cliente)
        _criar(cliente, dias_ate_saldo=d3 + 1, chance_pagar=0.0)
        _cobrar(cliente)
        for _ in range(3):
            _avancar(cliente)
        e = _avancar(cliente)                        # o prazo de resposta: nao pagou
        assert e["resposta_a_mensagem"] == "nao_pagou"
        assert e["ciclo"]["ciclo"]["estado"] == "mensagem_enviada" and e["desfecho"] is None
        assert e["proxima_acao"]["descricao"] == "Fim do prazo de recuperação"
        e = _avancar(cliente)                        # 30 dias depois da mensagem
        assert e["ciclo"]["ciclo"]["estado"] == "perdido"
        assert e["desfecho"]["tipo"] == "encerrado" and e["desfecho"]["valor_liquido"] == 0

    def test_revogacao_na_primeira_tentativa_vai_direto_para_a_mensagem(self, cliente):
        _criar(cliente, dias_ate_saldo=10, vai_revogar=True)
        _cobrar(cliente)
        e = _avancar(cliente)
        assert e["tentativas"][0]["causa"] == "authorization_revoked"
        assert [t["resultado"] for t in e["tentativas"][1:]] == ["cancelada", "cancelada"]
        assert e["ciclo"]["ciclo"]["estado"] == "mensagem_enviada"
        assert e["sem_crai"]["resultado"] == "perdido"

    def test_quem_tem_saldo_paga_de_primeira_e_nao_ha_ciclo(self, cliente):
        _criar(cliente, dias_ate_saldo=0, chance_pagar=1.0)
        e = _cobrar(cliente)
        assert e["cobranca"]["resultado"] == "paga" and e["ciclo"] is None
        assert e["desfecho"] == {"tipo": "recuperado", "via": "tentativa", "tentativa": 0,
                                 "valor_liquido": 300.0, "em": e["cobranca"]["em"]}
        assert e["sem_crai"]["resultado"] == "recuperado"
        assert _estado_do_real()["ciclos"] == []

    def test_avancar_por_dias_para_na_primeira_acao(self, cliente):
        d1, _, _ = _plano(cliente)
        _criar(cliente, dias_ate_saldo=15)
        e0 = _cobrar(cliente)
        e = _avancar(cliente, dias=30)
        andou = (datetime.fromisoformat(e["relogio"]["agora"])
                 - datetime.fromisoformat(e0["relogio"]["agora"])).days
        assert 1 <= andou <= d1 + 1, "parou na tentativa 1, e nao 30 dias depois"
        assert [t["resultado"] for t in e["tentativas"]] == ["falhou", "pendente", "pendente"]


# == A retencao simulada ===================================================

class TestRetencao:
    def _simular(self, c, tenant=A, nome="Loja Ponto Certo", mrr=1200.0, propensao=None, **sinais):
        corpo = {"nome": nome, "mrr": mrr, "propensao": propensao or PROPENSAO_TOTAL,
                 "sinais": {"uso_caiu": True, "tickets": False, "atraso": False,
                            "abriu_cancelamento": False, **sinais}}
        r = c.post("/simulacao/retencao", json=corpo, headers=_h(c, tenant))
        assert r.status_code == 200, r.text
        return r.json()

    def test_com_sinal_de_risco_ha_oferta_e_o_aceite_vem_da_propensao(self, cliente):
        r = self._simular(cliente, propensao=PROPENSAO_TOTAL)
        assert r["faixa"] in ("grave", "preocupante") and r["decidido_por"] == "regua"
        assert r["oferta"] in offer_bandit.OFFERS and r["oferta_legivel"]
        assert r["aceitou"] is True and r["simulado"] is True
        esperado = round(1200.0 - {"desconto_10": 120.0, "desconto_20": 240.0,
                                   "pausa_1_mes": 1200.0, "pix_boleto_flash": 0.0}[r["oferta"]], 2)
        assert r["valor_mantido_liquido"] == round(esperado * 0.85, 2)
        assert "24 dias" in r["motivo"] and r["motivo"][0] == "S"
        assert r["risco"] >= r["corte_de_intervencao"]
        recusa = self._simular(cliente, nome="Loja Dois", propensao=PROPENSAO_NENHUMA)
        assert recusa["oferta"] in offer_bandit.OFFERS
        assert recusa["aceitou"] is False and recusa["valor_mantido_liquido"] == 0

    def test_o_aceite_segue_a_propensao_da_oferta_escolhida_e_so_dela(self, cliente):
        primeira = self._simular(cliente, propensao=PROPENSAO_NENHUMA)
        _limpar(cliente)
        so_a_escolhida = {**PROPENSAO_NENHUMA, primeira["oferta"]: 1.0}
        assert self._simular(cliente, propensao=so_a_escolhida)["aceitou"] is True
        _limpar(cliente)
        todas_menos_ela = {**PROPENSAO_TOTAL, primeira["oferta"]: 0.0}
        assert self._simular(cliente, propensao=todas_menos_ela)["aceitou"] is False

    def test_sem_sinal_nao_ha_oferta(self, cliente):
        r = self._simular(cliente, uso_caiu=False)
        assert r["faixa"] == "sem_risco" and r["oferta"] is None and r["aceitou"] is None
        assert r["valor_mantido_liquido"] == 0 and r["risco"] == 0
        assert r["risco"] < r["corte_de_intervencao"] == va.CORTE_DE_INTERVENCAO
        assert r["motivo"] == "Sem dado de uso"

    def test_quem_abre_a_pagina_de_cancelamento_e_risco_grave_com_oferta(self, cliente, monkeypatch):
        eventos = []
        original = app_module._run_voluntary_pipeline

        async def espiao(user_id, event, props, **kw):
            eventos.append((event, dict(props)))
            return await original(user_id, event, props, **kw)
        monkeypatch.setattr(app_module, "_run_voluntary_pipeline", espiao)
        r = self._simular(cliente, uso_caiu=False, abriu_cancelamento=True)
        assert r["faixa"] == "grave" and r["decidido_por"] == "regua" and r["risco"] == 0.9
        assert r["oferta"] in offer_bandit.OFFERS and r["aceitou"] is True
        assert r["motivo"] == "Abriu a página de cancelamento, sem dado de uso"
        # O sistema recebeu o evento do SDK, e so o que o formulario marcou.
        assert eventos == [("Cancellation Page Viewed",
                            {"mrr": 1200.0, "billing_profile": "PJ", "on_site_now": True})]

    def test_cada_sinal_vira_um_dado_e_sinal_nao_marcado_nao_vira_nada(self):
        base = {"uso_caiu": False, "tickets": False, "atraso": False, "abriu_cancelamento": False}
        assert simulador.props_dos_sinais(500.0, base) == {
            "mrr": 500.0, "billing_profile": "PJ", "on_site_now": False}
        tudo = simulador.props_dos_sinais(500.0, {k: True for k in base})
        assert tudo == {"mrr": 500.0, "billing_profile": "PJ", "on_site_now": True,
                        "days_since_last": 24, "features_used_30d": 1,
                        "tickets_30d": 3, "failed_pay_90d": 2}
        assert "propensao" not in tudo
        assert simulador.evento_dos_sinais(base, "Session Started") == "Session Started"
        assert simulador.evento_dos_sinais({**base, "abriu_cancelamento": True},
                                           "Session Started") == "Cancellation Page Viewed"

    def test_o_bandit_de_verdade_nao_aprende_com_a_simulacao(self, cliente):
        antes = copy.deepcopy(va._bandit.state)
        for i in range(4):
            self._simular(cliente, nome=f"Loja Numero {'abcd'[i]}")
        assert va._bandit.state == antes
        assert not Path(offer_bandit.STATE_PATH).exists(), "o estado real do bandit foi gravado"
        copia = ambiente.caminho_simulado(Path(offer_bandit.STATE_PATH), A)
        assert copia.exists()
        aprendido = json.loads(copia.read_text(encoding="utf-8"))
        assert aprendido != {A: antes.get(A)} and list(aprendido) == [A]
        _limpar(cliente)
        assert not copia.exists()

    def test_o_mantido_simulado_so_aparece_para_quem_pede(self, cliente):
        # A oferta de cada cliente ficticio sai do sorteio do bandit, e a pausa de 1 mes
        # mantem R$ 0 (com 1 mes contado): cria clientes ate um manter dinheiro. A soma
        # e o que as metricas mostram.
        nomes, respostas = [], []
        for letra in "ABCDEFGHIJKL":
            nomes.append(f"Loja {letra}")
            respostas.append(self._simular(cliente, nome=nomes[-1]))
            if respostas[-1]["valor_mantido_liquido"] > 0:
                break
        assert all(r["aceitou"] is True for r in respostas)
        total = round(sum(r["valor_mantido_liquido"] for r in respostas), 2)
        assert total > 0, "em 12 clientes ficticios nenhuma oferta diferente da pausa"
        real = cliente.get("/metrics/voluntario/mes", headers=_h(cliente)).json()
        assert real["valor_liquido_mantido"] == 0 and real["ofertas_enviadas"] == 0
        assert real["clientes_mantidos"] == 0
        com = cliente.get("/metrics/voluntario/mes", params={"incluir_simulados": "true"},
                          headers=_h(cliente)).json()
        assert com["valor_liquido_mantido"] == total
        assert com["clientes_mantidos"] == len(nomes) == com["ofertas_aceitas"]
        recentes = cliente.get("/clientes/recentes", params={"incluir_simulados": "true"},
                               headers=_h(cliente)).json()["clientes"]
        assert sorted((c["nome"], c["simulado"]) for c in recentes) == sorted(
            (n, True) for n in nomes)
        assert all(c["abordagem"]["situacao"] == "aceita" for c in recentes)
        assert {c["abordagem"]["oferta"] for c in recentes} == {r["oferta"] for r in respostas}
        assert cliente.get("/clientes/recentes", headers=_h(cliente)).json()["clientes"] == []

    def test_a_propensao_escondida_nao_volta_em_nenhuma_leitura(self, cliente):
        self._simular(cliente, propensao={**PROPENSAO_NENHUMA, "desconto_20": 0.37})
        for rota, params in (("/clientes/recentes", {"incluir_simulados": "true"}),
                             ("/atividade", {"incluir_simulados": "true"}), ("/simulacao", {})):
            r = cliente.get(rota, params=params, headers=_h(cliente))
            assert "propensao" not in set(_chaves(r.json())) and "0.37" not in r.text, rota


# == A verdade escondida nunca chega aos modelos ===========================

class TestAVerdadeFicaDeFora:
    def test_nenhum_modulo_do_pipeline_conhece_a_verdade(self):
        ofensores = []
        for pasta in ("agent", "ml", "dunning", "churn_voluntary", "integrations", "accounts"):
            for arquivo in (APP / "crai" / pasta).rglob("*.py"):
                fonte = arquivo.read_text(encoding="utf-8")
                if ("simulacao_verdade" in fonte or "verdade_de" in fonte
                        or "import simulador" in fonte or "from ..simulador" in fonte
                        or "psp_responde" in fonte):
                    ofensores.append(arquivo.name)
        assert ofensores == []

    def test_so_o_psp_simulado_e_o_sem_a_crai_leem_a_tabela_da_verdade(self):
        import ast
        import inspect
        arvore = ast.parse(inspect.getsource(simulador))
        leitores = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.FunctionDef):
                for s in ast.walk(no):
                    if isinstance(s, ast.Constant) and isinstance(s.value, str) \
                            and "simulacao_verdade" in s.value and "SELECT" in s.value.upper():
                        leitores.add(no.name)
                    if isinstance(s, ast.Call) and getattr(s.func, "id", "") == "verdade_de":
                        leitores.add(no.name)
        assert leitores == {"verdade_de", "psp_responde", "responde_a_mensagem", "sem_crai"}

    def test_duas_verdades_opostas_recebem_o_mesmo_diagnostico(self, cliente, monkeypatch):
        vistos = []
        original = workflow._classifier.predict

        def espiao(features, *a, **k):
            vistos.append(copy.deepcopy(features))
            return original(features, *a, **k)
        monkeypatch.setattr(workflow._classifier, "predict", espiao)

        def rodar(**verdade):
            _criar(cliente, **verdade)
            e = _cobrar(cliente)
            foto = {"chance": e["chance_recuperar"], "plano": _dias_das_tentativas(e),
                    "contribuicoes": (e["ciclo"]["diagnostico"] or {}).get("contribuicoes"),
                    "recorrencia": e["id_recorrencia"], "causa": e["cobranca"]["causa"]}
            _limpar(cliente)
            return foto

        um = rodar(dias_ate_saldo=1, chance_pagar=0.05, vai_revogar=False)
        outro = rodar(dias_ate_saldo=14, chance_pagar=1.0, vai_revogar=True)
        assert um == outro
        assert len(vistos) == 2 and vistos[0] == vistos[1]
        texto = json.dumps(vistos, default=str)
        for proibido in ("dias_ate_saldo", "chance_pagar", "vai_revogar", "verdade"):
            assert proibido not in texto

    def test_o_evento_que_entra_no_pipeline_nao_carrega_a_verdade(self, cliente, monkeypatch):
        eventos = []
        original = app_module._run_involuntary_pipeline

        async def espiao(**kw):
            eventos.append(copy.deepcopy(kw))
            return await original(**kw)
        monkeypatch.setattr(app_module, "_run_involuntary_pipeline", espiao)
        _criar(cliente, dias_ate_saldo=9, chance_pagar=0.42, vai_revogar=True)
        _cobrar(cliente)
        _avancar(cliente)
        assert len(eventos) == 2
        for kw in eventos:
            assert set(kw["event"]) == {"e2e_id", "valor", "status", "id_recorrencia",
                                        "codigo_falha", "id_cobranca", "degradacoes"}
            assert "0.42" not in json.dumps(kw, default=str)

    def test_a_verdade_volta_so_para_a_empresa_que_a_criou(self, cliente):
        _criar(cliente, dias_ate_saldo=7, chance_pagar=0.4)
        assert _estado(cliente)["cliente"]["verdade"] == {
            "dias_ate_saldo": 7, "chance_pagar": 0.4, "vai_revogar": False}
        de_b = _estado(cliente, B)
        assert de_b["existe"] is False and de_b["cliente"] is None


# == Nada da simulacao encosta no que e real ===============================

class TestNadaEntraNoReal:
    @pytest.fixture
    def simulacao_completa(self, cliente):
        antes = _estado_do_real()
        _, _, d3 = _plano(cliente)
        _criar(cliente, dias_ate_saldo=d3 + 1)
        _cobrar(cliente)
        for _ in range(4):
            estado = _avancar(cliente)
        assert estado["ciclo"]["ciclo"]["estado"] == "recuperado"
        retencao = cliente.post("/simulacao/retencao", headers=_h(cliente), json={
            "nome": "Loja Ponto Certo", "mrr": 900.0, "propensao": PROPENSAO_TOTAL,
            "sinais": {"uso_caiu": True, "tickets": True, "atraso": False,
                       "abriu_cancelamento": False}}).json()
        assert retencao["aceitou"] is True
        return antes, estado, retencao

    def test_ciclo_dataset_trilha_e_bandit_reais_ficam_como_estavam(self, simulacao_completa):
        antes, _, _ = simulacao_completa
        depois = _estado_do_real()
        assert depois == antes
        assert depois["ciclos"] == [] and depois["dataset"] == [] and depois["retencao"] == []
        assert depois["trilha"]["integra"] is True and depois["trilha"]["linhas"] == 0
        assert not Path(offer_bandit.STATE_PATH).exists()

    def test_com_ciclo_e_trilha_reais_a_simulacao_nao_muda_um_byte_deles(self, cliente):
        import asyncio
        from crai.integrations.payment_gateway import STATUS_COBRANCA_FALHADA
        evento = {"e2e_id": "E_real_0", "valor": 200.0, "status": STATUS_COBRANCA_FALHADA,
                  "id_recorrencia": "RN_real_1", "codigo_falha": "AM04",
                  "id_cobranca": "cob_real_1", "degradacoes": []}
        asyncio.run(app_module._run_involuntary_pipeline(
            event=evento, payment_method="pix_automatico", customer_id="RN_real_1",
            amount=200.0, invoice_id="E_real_0", tenant_id=A))
        antes = _estado_do_real()
        assert len(antes["ciclos"]) == 1 and len(antes["dataset"]) == 1
        assert antes["trilha"]["integra"] is True and antes["trilha"]["linhas"] >= 3
        # A simulacao LE a configuracao real da empresa, e a primeira leitura cria a
        # tabela dela (como qualquer GET /configuracao). Lida aqui, antes da foto.
        configuracao.ler(A)
        reais = [cc.caminho_do_banco(), recovery_log.caminho_do_banco(), rl.caminho_do_banco()]
        fotos = {p: p.read_bytes() for p in reais if p.exists()}
        assert rl.caminho_do_banco() in fotos

        _, _, d3 = _plano(cliente)
        _criar(cliente, dias_ate_saldo=d3 + 1)
        _cobrar(cliente)
        for _ in range(4):
            estado = _avancar(cliente)
        assert estado["ciclo"]["ciclo"]["estado"] == "recuperado"
        cliente.post("/simulacao/retencao", headers=_h(cliente), json={
            "nome": "Loja Ponto Certo", "mrr": 900.0, "propensao": PROPENSAO_TOTAL,
            "sinais": {"uso_caiu": True, "tickets": True, "atraso": False,
                       "abriu_cancelamento": False}})
        _limpar(cliente)

        depois = _estado_do_real()
        assert depois == antes
        assert depois["trilha"]["ultimo_hash"] == antes["trilha"]["ultimo_hash"] is not None
        for p, foto in fotos.items():
            assert p.read_bytes() == foto, f"{p.name} mudou durante a simulacao"

    def test_a_simulacao_tem_os_proprios_arquivos_com_o_que_ela_fez(self, simulacao_completa):
        with ambiente.em_simulacao(A):
            assert len(cc.listar_ciclos(A, limite=200)) == 1
            assert len(recovery_log.linhas(A)) >= 1
            trilha = rl.verificar_cadeia(A)
            assert trilha["integra"] is True and trilha["linhas"] >= 3
            assert len(rl.ciclos_com_oferta(A)) == 1
        for arquivo in simulador.arquivos(A):
            assert f".simulacao.{A}" in arquivo.name

    def test_as_metricas_reais_nao_contam_o_simulado(self, cliente, simulacao_completa):
        for rota in ("/metrics/involuntario/mes", "/metrics/visao-geral"):
            r = cliente.get(rota, headers=_h(cliente)).json()
            assert r.get("valor_liquido_recuperado", r.get("recuperado_involuntario")) == 0, rota
        assert cliente.get("/ciclos", headers=_h(cliente)).json()["ciclos"] == []
        assert cliente.get("/atividade", headers=_h(cliente)).json()["atividades"] == []
        assert cliente.get("/extrato", headers=_h(cliente)).json()["linhas"] == []
        assert cliente.get("/metrics/recovery", headers=_h(cliente)).json().get("ciclos", 0) == 0

    def test_com_a_barra_mostrar_o_simulado_aparece_marcado(self, cliente, simulacao_completa):
        _, estado, retencao = simulacao_completa
        sim = {"incluir_simulados": "true"}
        lista = cliente.get("/ciclos", params=sim, headers=_h(cliente)).json()["ciclos"]
        assert [(c["simulado"], c["cliente_nome"], c["status"]) for c in lista] == [
            (True, "Ana Souza", "recuperado")]
        assert lista[0]["id"] == estado["ciclo"]["ciclo"]["id"] and lista[0]["valor_liquido"] == 255.0
        detalhe = cliente.get(f"/ciclos/{lista[0]['id']}", headers=_h(cliente))
        assert detalhe.status_code == 200 and detalhe.json()["ciclo"]["simulado"] is True
        cartoes = cliente.get("/metrics/visao-geral", params=sim, headers=_h(cliente)).json()
        assert cartoes["recuperado_involuntario"] == 255.0 and cartoes["cobrancas_recuperadas"] == 1
        assert cartoes["retido_voluntario"] == retencao["valor_mantido_liquido"]
        mes = cliente.get("/metrics/involuntario/mes", params=sim, headers=_h(cliente)).json()
        serie = cliente.get("/metrics/serie", params=sim, headers=_h(cliente)).json()["pontos"]
        # O relogio simulado esta dias a frente: o agora dele aparece como agora ha
        # pouco (hoje; ou ontem, se o teste rodar no primeiro minuto do dia).
        assert round(sum(p["involuntario"] for p in serie), 2) == 255.0
        assert 255.0 in (serie[-1]["involuntario"], serie[-2]["involuntario"])
        assert mes["valor_liquido_recuperado"] in (255.0, 0)   # 0 so se o mes virou no meio
        atividades = cliente.get("/atividade", params={**sim, "limite": 50},
                                 headers=_h(cliente)).json()["atividades"]
        assert atividades and all(a["simulado"] is True for a in atividades)
        agora = datas.agora_local()
        quando = [datas.para_local(a["em"]) for a in atividades]
        assert all(q <= agora for q in quando), "nada simulado aparece no futuro"
        assert agora - max(quando) < timedelta(minutes=5), "o ultimo evento aparece como de agora"
        assert quando == sorted(quando, reverse=True)
        assert any(a["texto"] == "Cobrança de Ana Souza recuperada depois da mensagem"
                   for a in atividades)
        assert all(str(a["id"]).startswith("sim-") for a in atividades)
        extrato = cliente.get("/extrato", params=sim, headers=_h(cliente)).json()["linhas"]
        assert extrato and all(l["simulado"] is True for l in extrato)
        funcionou = cliente.get("/metrics/o-que-funciona", params=sim, headers=_h(cliente)).json()
        assert funcionou["causas"][0]["casos"] == 1

    def test_o_relogio_de_verdade_nao_dispara_tentativa_simulada(self, cliente):
        import asyncio
        _criar(cliente, dias_ate_saldo=15)
        _cobrar(cliente)
        disparos = asyncio.run(retry_scheduler.processar_tentativas_devidas(
            datetime.now() + timedelta(days=30)))
        assert disparos == []
        assert [t["resultado"] for t in _estado(cliente)["tentativas"]] == ["pendente"] * 3
        assert datetime.fromisoformat(_estado(cliente)["relogio"]["agora"]).date() == \
            datas.agora_local().date()

    def test_o_real_nao_aparece_dentro_da_simulacao(self, cliente):
        aberto = datas.agora_local() - timedelta(days=1)
        real = cc.abrir_ciclo(A, "RN_real", 100.0, "insufficient_funds", aberto,
                              id_cobranca="cob-real")
        _criar(cliente, dias_ate_saldo=15)
        e = _cobrar(cliente)
        lista = cliente.get("/ciclos", params={"incluir_simulados": "true"},
                            headers=_h(cliente)).json()["ciclos"]
        assert sorted((c["simulado"], c["id_recorrencia"]) for c in lista) == [
            (False, "RN_real"), (True, e["id_recorrencia"])]
        # O id interno pode ate coincidir: o id publico e que os separa.
        assert cliente.get(f"/ciclos/{real['id']}", headers=_h(cliente)).json()["ciclo"][
            "id_recorrencia"] == "RN_real"
        _avancar(cliente, dias=20)
        assert cc.tentativas_do_ciclo(real["id"]) == []
        assert cc.ciclo_por_id(real["id"])["estado"] == "recobrando"


# == O relogio por empresa e a limpeza =====================================

class TestRelogioELimpeza:
    def test_cada_empresa_tem_o_seu_relogio(self, cliente):
        _criar(cliente, A, dias_ate_saldo=15)
        _cobrar(cliente, A)
        _criar(cliente, B, nome="Bruno Lima", dias_ate_saldo=15)
        e_b = _cobrar(cliente, B)
        _avancar(cliente, A, dias=3)
        _avancar(cliente, A, dias=3)
        depois_b = _estado(cliente, B)
        assert depois_b["relogio"] == e_b["relogio"]
        assert [t["resultado"] for t in depois_b["tentativas"]] == ["pendente"] * 3
        assert datetime.fromisoformat(_estado(cliente, A)["relogio"]["agora"]) > \
            datetime.fromisoformat(depois_b["relogio"]["agora"])

    def test_o_relogio_so_anda_para_a_frente(self, cliente):
        _criar(cliente, dias_ate_saldo=15)
        antes = _cobrar(cliente)["relogio"]["agora"]
        _avancar(cliente, dias=2)
        meio = _estado(cliente)["relogio"]["agora"]
        _criar(cliente, nome="Carla Menezes", dias_ate_saldo=15)
        assert antes < meio == _estado(cliente)["relogio"]["agora"]

    def test_limpar_apaga_tudo_o_que_e_ficticio_e_so_da_empresa(self, cliente):
        _criar(cliente, A, dias_ate_saldo=15)
        _cobrar(cliente, A)
        _criar(cliente, B, nome="Bruno Lima", dias_ate_saldo=15)
        _cobrar(cliente, B)
        ci.gravar(A, [{"customer_id_externo": "real-1", "mrr": 10.0, "billing_profile": "PJ"}])
        real = cc.abrir_ciclo(A, "RN_real", 100.0, "insufficient_funds", datas.agora_local(),
                              id_cobranca="cob-real")
        assert any(p.exists() for p in simulador.arquivos(A))
        r = _limpar(cliente, A)
        assert r["limpo"] is True and r["arquivos_apagados"] >= 1 and r["existe"] is False
        assert not any(p.exists() for p in simulador.arquivos(A))
        assert _estado(cliente, A)["existe"] is False
        assert cliente.get("/ciclos", params={"incluir_simulados": "true"},
                           headers=_h(cliente, A)).json()["ciclos"][0]["id"] == real["id"]
        # O que e real da empresa, e a simulacao da outra, continuam la.
        assert cc.ciclo_por_id(real["id"]) is not None and ci.contar(A) == 1
        assert _estado(cliente, B)["ciclo"] is not None
        # Limpar de novo, sem nada, nao e erro.
        assert _limpar(cliente, A)["arquivos_apagados"] == 0

    def test_ler_a_simulacao_de_quem_nunca_simulou_nao_cria_arquivo(self, cliente):
        assert _estado(cliente)["existe"] is False
        cliente.get("/ciclos", params={"incluir_simulados": "true"}, headers=_h(cliente))
        cliente.get("/metrics/visao-geral", params={"incluir_simulados": "true"}, headers=_h(cliente))
        assert not any(p.exists() for p in simulador.arquivos(A))


# == O formulario so aceita dado inventado =================================

class TestSoDadoInventado:
    @pytest.mark.parametrize("nome", [
        "ana@exemplo.com.br", "123.456.789-09", "Ana 12345678909", "11 98888-7777",
        "+55 (11) 98888-7777", "12.345.678/0001-90", "Ana 123456",
        "123e4567-e89b-12d3-a456-426614174000"])
    def test_nome_que_parece_dado_real_e_recusado(self, cliente, nome):
        r = cliente.post("/simulacao/cliente", json=_corpo(nome=nome), headers=_h(cliente))
        assert r.status_code == 422 and r.json()["detail"]["motivo"] == "dado_que_parece_real"
        assert r.json()["detail"]["campo"] == "nome"
        assert not simulador.existe(A)
        ret = cliente.post("/simulacao/retencao", headers=_h(cliente), json={
            "nome": nome, "mrr": 100.0, "propensao": PROPENSAO_TOTAL,
            "sinais": {"uso_caiu": True, "tickets": False, "atraso": False,
                       "abriu_cancelamento": False}})
        assert ret.status_code == 422 and ret.json()["detail"]["motivo"] == "dado_que_parece_real"

    @pytest.mark.parametrize("campo", ["cpf", "email", "telefone", "chave_pix", "cartao", "conta"])
    def test_nao_ha_campo_para_dado_pessoal(self, cliente, campo):
        r = cliente.post("/simulacao/cliente", json={**_corpo(), campo: "x"}, headers=_h(cliente))
        assert r.status_code == 422 and r.json()["detail"]["motivo"] == "campo_desconhecido"

    @pytest.mark.parametrize("ruim", [
        {"nome": "ab"}, {"nome": "x" * 41}, {"mensalidade": 9.99}, {"mensalidade": 50000.01},
        {"mensalidade": "300"}, {"perfil": "mei"}, {"verdade": {"dias_ate_saldo": 16}},
        {"verdade": {"dias_ate_saldo": -1}}, {"verdade": {"chance_pagar": 1.01}},
        {"verdade": {"vai_revogar": "sim"}}, {"verdade": {"dias_ate_saldo": 1.5}}])
    def test_validacao_do_cliente(self, cliente, ruim):
        corpo = _corpo()
        if "verdade" in ruim:
            corpo["verdade"] = {**corpo["verdade"], **ruim["verdade"]}
        else:
            corpo.update(ruim)
        r = cliente.post("/simulacao/cliente", json=corpo, headers=_h(cliente))
        assert r.status_code == 422, r.text
        assert not simulador.existe(A)

    def test_validacao_da_retencao_e_do_avanco(self, cliente):
        base = {"nome": "Loja Um", "mrr": 100.0, "propensao": PROPENSAO_TOTAL,
                "sinais": {"uso_caiu": True, "tickets": False, "atraso": False,
                           "abriu_cancelamento": False}}
        for ruim in ({"mrr": 5}, {"propensao": {"desconto_20": 1.0}},
                     {"propensao": {**PROPENSAO_TOTAL, "oferta_nova": 1.0}},
                     {"sinais": {"uso_caiu": True}}, {"propensao": {**PROPENSAO_TOTAL, "desconto_10": 2}},
                     {"sinais": {"uso_caiu": True, "tickets": False, "atraso": False}},
                     {"sinais": {"uso_caiu": True, "tickets": False, "atraso": False,
                                 "abriu_cancelamento": False, "cpf": True}},
                     {"sinais": {"uso_caiu": True, "tickets": False, "atraso": False,
                                 "abriu_cancelamento": "sim"}}):
            r = cliente.post("/simulacao/retencao", json={**base, **ruim}, headers=_h(cliente))
            assert r.status_code == 422, ruim
        _criar(cliente)
        for ruim in ({}, {"dias": 0}, {"dias": 61}, {"dias": "3"}, {"ate_proxima_acao": False},
                     {"dias": 1, "ate_proxima_acao": True}, {"outro": 1}):
            r = cliente.post("/simulacao/avancar", json=ruim, headers=_h(cliente))
            assert r.status_code == 422, ruim


# == Tenant, papel e ambiente ==============================================

class TestTenantPapelEAmbiente:
    def test_o_ciclo_simulado_de_outra_empresa_e_404_igual_ao_inexistente(self, cliente):
        _criar(cliente, A, dias_ate_saldo=15)
        de_a = _cobrar(cliente, A)["ciclo"]["ciclo"]["id"]
        inexistente = cliente.get(f"/ciclos/{simulador.ID_DA_SIMULACAO + 987654}", headers=_h(cliente, B))
        for h in (_h(cliente, B),):
            r = cliente.get(f"/ciclos/{de_a}", headers=h)
            assert r.status_code == 404 and r.json() == inexistente.json()
            r = cliente.post(f"/ciclos/{de_a}/mensagens/escolher", headers=h,
                             json={"rodada": 1, "abordagem": "facilitacao"})
            assert r.status_code == 404 and r.json() == inexistente.json()
            r = cliente.post(f"/ciclos/{de_a}/mensagens/regerar", headers=h)
            assert r.status_code == 404 and r.json() == inexistente.json()
        # B com a propria simulacao continua sem enxergar o ciclo de A.
        _criar(cliente, B, nome="Bruno Lima", dias_ate_saldo=15)
        de_b = _cobrar(cliente, B)["ciclo"]["ciclo"]
        assert de_b["cliente_nome"] == "Bruno Lima"
        lista_b = cliente.get("/ciclos", params={"incluir_simulados": "true"},
                              headers=_h(cliente, B)).json()["ciclos"]
        assert [c["cliente_nome"] for c in lista_b] == ["Bruno Lima"]
        assert "Ana Souza" not in cliente.get("/simulacao", headers=_h(cliente, B)).text

    def test_as_acoes_de_uma_empresa_nao_mexem_na_outra(self, cliente):
        _criar(cliente, A, dias_ate_saldo=15)
        antes = _cobrar(cliente, A)
        assert cliente.post("/simulacao/cobrar", headers=_h(cliente, B)).status_code == 409
        assert cliente.post("/simulacao/avancar", json={"dias": 5},
                            headers=_h(cliente, B)).status_code == 409
        _limpar(cliente, B)
        assert _estado(cliente, A) == antes

    @pytest.mark.parametrize("metodo,rota", ROTAS_DE_ESCRITA)
    def test_membro_nao_cria_nao_avanca_e_nao_limpa(self, cliente, metodo, rota):
        _criar(cliente)
        for papel in ("membro", None):
            h = cliente.projeto.bearer(A, papel=papel) if papel else cliente.projeto.bearer(A)
            r = cliente.request(metodo, rota, headers=h, json={})
            assert r.status_code == 403 and r.json()["detail"]["motivo"] == "papel_insuficiente"
        assert _estado(cliente, papel="membro")["cliente"]["nome"] == "Ana Souza"

    @pytest.mark.parametrize("metodo,rota", ROTAS_DE_ESCRITA + (("GET", "/simulacao"),))
    def test_sem_token_e_401(self, cliente, metodo, rota):
        assert cliente.request(metodo, rota, json={}).status_code == 401

    def test_409_sem_cliente_e_cobrar_duas_vezes(self, cliente):
        for rota, corpo in (("/simulacao/cobrar", None), ("/simulacao/avancar", {"dias": 1})):
            r = cliente.post(rota, json=corpo, headers=_h(cliente))
            assert r.status_code == 409, rota
        _criar(cliente, dias_ate_saldo=15)
        _cobrar(cliente)
        r = cliente.post("/simulacao/cobrar", headers=_h(cliente))
        assert r.status_code == 409 and r.json()["detail"]["motivo"] == "cliente_ja_cobrado"

    def test_funciona_em_producao_e_as_rotas_de_demo_antigas_continuam_fechadas(self, cliente, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        _criar(cliente, dias_ate_saldo=15)
        assert _cobrar(cliente)["ciclo"]["ciclo"]["estado"] == "recobrando"
        assert cliente.post("/simulate/pix-falhado", json={}).status_code == 403

    def test_psp_e_crm_ficam_simulados_mesmo_com_credencial(self, cliente, monkeypatch):
        monkeypatch.setenv(pagarme_gateway.ENV_LIVE, "1")
        monkeypatch.setenv(pagarme_gateway.ENV_API_KEY, "sk_de_teste_que_nao_pode_ser_usada")
        assert pagarme_gateway.modo_real() is True

        class NaoPodeSerChamado:
            def __getattr__(self, nome):
                raise AssertionError("o CRM de verdade foi chamado dentro da simulacao")
        for crm in (workflow._hubspot, va._hubspot):
            monkeypatch.setattr(crm, "dry", False)
            monkeypatch.setattr(crm, "client", NaoPodeSerChamado(), raising=False)

        async def rede(*a, **k):
            raise AssertionError("o PSP de verdade foi chamado dentro da simulacao")
        monkeypatch.setattr(pagarme_gateway, "_reenviar_de_verdade", rede)

        _, _, d3 = _plano(cliente)
        _criar(cliente, dias_ate_saldo=d3 + 1)
        _cobrar(cliente)
        for _ in range(4):
            e = _avancar(cliente)
        assert e["ciclo"]["ciclo"]["estado"] == "recuperado"
        r = cliente.post("/simulacao/retencao", headers=_h(cliente), json={
            "nome": "Loja Ponto Certo", "mrr": 900.0, "propensao": PROPENSAO_TOTAL,
            "sinais": {"uso_caiu": True, "tickets": False, "atraso": False,
                       "abriu_cancelamento": False}})
        assert r.status_code == 200 and r.json()["aceitou"] is True
        # Fora da simulacao, o modo real continua sendo o que a env diz.
        assert pagarme_gateway.modo_real() is True and not ambiente.simulacao_ativa()

    def test_a_chave_de_api_nao_abre_a_simulacao(self, cliente):
        r = cliente.get("/simulacao", headers={"Authorization": "Bearer crai_live_" + "a" * 43})
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "chave_nao_vale_nesta_rota"


# == O ambiente em si ======================================================

class TestAmbiente:
    def test_fora_da_simulacao_tudo_e_neutro(self):
        p = Path("x/recovery_cycles.db")
        assert ambiente.caminho(p) == p and ambiente.relogio_simulado() is None
        assert ambiente.simulacao_ativa() is False and ambiente.tenant_da_simulacao() is None

    def test_dentro_o_arquivo_e_o_da_empresa_e_o_relogio_e_o_dela(self, monkeypatch):
        monkeypatch.setenv(datas.ENV_FUSO, "America/Sao_Paulo")
        agora = datetime(2030, 1, 2, 3, 4, 5)
        with ambiente.em_simulacao(A, agora):
            assert ambiente.caminho(Path("x/recovery_cycles.db")) == \
                Path(f"x/recovery_cycles.simulacao.{A}.db")
            assert ambiente.relogio_simulado() == agora and workflow._agora() == agora
            assert app_module._agora() == agora
            # A trilha e o dataset gravam em UTC: 03:04:05 de Sao Paulo sao 06:04:05.
            assert rl._agora() == "2030-01-02T06:04:05+00:00" == recovery_log._agora()
            assert cc.caminho_do_banco().name.endswith(f".simulacao.{A}.db")
            assert rl.caminho_do_banco().name.endswith(f".simulacao.{A}.db")
            assert recovery_log.caminho_do_banco().name.endswith(f".simulacao.{A}.db")
            with ambiente.fora_da_simulacao():
                assert not ambiente.simulacao_ativa()
                assert ".simulacao." not in cc.caminho_do_banco().name
            assert ambiente.simulacao_ativa()
        assert not ambiente.simulacao_ativa()
        assert ".simulacao." not in rl.caminho_do_banco().name

    @pytest.mark.parametrize("tenant", ["", None, 7])
    def test_sem_tenant_nao_ha_simulacao(self, tenant):
        with pytest.raises(ValueError):
            with ambiente.em_simulacao(tenant):
                pass
        with pytest.raises(ValueError):
            ambiente.caminho_simulado(Path("x.db"), tenant)

    @pytest.mark.parametrize("tenant", ["../outro", "a/b", "a" + chr(92) + "b", "a b", "x" * 65,
                                        "a.b", "Empresa-A", "c:evil", "a" + chr(10)])
    def test_tenant_torto_nao_escolhe_o_nome_do_arquivo(self, tenant):
        import re
        real = Path("pasta/recovery_cycles.db")
        simulado = ambiente.caminho_simulado(real, tenant)
        assert simulado.parent == real.parent
        assert re.fullmatch(r"recovery_cycles\.simulacao\.h[0-9a-f]{32}\.db", simulado.name)

    def test_dois_tenants_nunca_dividem_arquivo(self):
        real = Path("recovery_cycles.db")
        tenants = ["empresa-a", "Empresa-A", "EMPRESA-A", "empresa_a", "empresa-a.db", "empresa-a ",
                   "h" + "0" * 32]
        nomes = {ambiente.caminho_simulado(real, t).name.lower() for t in tenants}
        assert len(nomes) == len(tenants)
        simples = ambiente.caminho_simulado(real, "empresa-a").name
        assert simples == "recovery_cycles.simulacao.empresa-a.db"

    def test_a_configuracao_da_simulacao_e_a_real_mais_um_canal(self, monkeypatch):
        monkeypatch.setitem(configuracao.PADROES, "canal_presumido", None)
        configuracao.gravar(A, {"prazo_escolha_horas": 5, "canais_permitidos": ["email", "whatsapp"]})
        assert configuracao.ler(A)["canal_presumido"] is None
        with ambiente.em_simulacao(A):
            sim = configuracao.ler(A)
        assert sim["prazo_escolha_horas"] == 5 and sim["canal_presumido"] == "email"
        assert configuracao.ler(A)["canal_presumido"] is None

    def test_o_simulador_nao_grava_fora_da_simulacao(self):
        with pytest.raises(RuntimeError):
            simulador.relogio(A)

    def test_a_limpeza_so_apaga_arquivo_de_simulacao(self, monkeypatch):
        monkeypatch.setattr(ambiente, "caminho_simulado", lambda real, tenant: Path(real))
        with pytest.raises(RuntimeError):
            simulador.limpar(A)
        assert not ambiente.simulacao_ativa()

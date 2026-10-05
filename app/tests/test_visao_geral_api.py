"""tests/test_visao_geral_api.py - Rodada 3, Fase 2: a visao geral do dashboard.

O QUE ESTE ARQUIVO MEDE:

  - `GET /metrics/visao-geral` e `/metrics/serie`: os dois churns, liquidos,
    batendo com uma conta feita a mao sobre os mesmos ciclos; o voluntario
    `null` fora do plano premium;
  - `GET /metrics/involuntario/funil`: cada etapa, o valor e onde a cobranca
    voltou;
  - `GET /metrics/o-que-funciona`: por causa, por oferta e por canal;
  - `GET /atividade`: as frases, a ordem e o que nao pode aparecer;
  - `GET /extrato`: a memoria de calculo, com a fee, so para dono e
    administrador, e no registro de acesso;
  - `GET /health`: modelos, redator e, so com token, a base da empresa;
  - R11: a fee nao aparece em nenhuma rota, fora o extrato;
  - LGPD: nenhum contato em nenhuma resposta; isolamento por tenant em cada
    rota; 401 sem token.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from crai import config
from crai.api import app as app_module
from crai.api import datas, registro_acesso
from crai.api import visao_geral as vg
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import mantido
from crai.churn_voluntary import retention_log as rl
from crai.dunning import ciclo_cobranca as cc

A, B = "empresa-a", "empresa-b"
ROTAS = ("/metrics/visao-geral", "/metrics/serie", "/metrics/involuntario/funil",
         "/metrics/o-que-funciona", "/atividade", "/extrato")
SEM_FEE = tuple(r for r in ROTAS if r != "/extrato")
CONTATOS = ("pessoa@exemplo.com.br", "5511988887777", "88887777")


@pytest.fixture
def cliente(monkeypatch, supabase_falso):
    monkeypatch.setenv(datas.ENV_FUSO, "America/Sao_Paulo")
    monkeypatch.delenv(config.ENV_SUCCESS_FEE, raising=False)
    monkeypatch.delenv(config.ENV_SUCCESS_FEE_VOLUNTARIO, raising=False)
    with TestClient(app_module.app) as c:
        c.projeto = supabase_falso
        yield c


def _get(cliente, tenant, caminho, papel="owner", plano="premium", **params):
    return cliente.get(caminho, params=params,
                       headers=cliente.projeto.bearer(tenant, papel=papel, plano=plano))


def _chaves(valor):
    if isinstance(valor, dict):
        for k, v in valor.items():
            yield k
            yield from _chaves(v)
    elif isinstance(valor, list):
        for v in valor:
            yield from _chaves(v)


AGORA = None


def _agora():
    """Hoje ao meio-dia, hora local: longe da virada do dia."""
    return datas.agora_local().replace(hour=12, minute=0, second=0, microsecond=0)


def _ciclo(tenant, rec, valor=200.0, causa="insufficient_funds", dias_atras=5):
    aberto = _agora() - timedelta(days=dias_atras)
    return cc.abrir_ciclo(tenant, rec, valor, causa, aberto, id_cobranca=f"cob-{rec}")["id"], aberto


def _tentar(ciclo_id, numero, resultado, quando, valor=200.0):
    cc.agendar_tentativas(ciclo_id, [{"numero": numero, "quando": quando, "valor": valor}], quando)
    assert cc.marcar_disparada(ciclo_id, numero, f"t-{ciclo_id}-{numero}", quando)
    if resultado == cc.FALHOU:
        assert cc.registrar_resultado(ciclo_id, numero, cc.FALHOU, quando, codigo="AM04")


def _mensagem(ciclo_id, quando, canal="whatsapp"):
    cc.transicionar(ciclo_id, cc.AGUARDANDO_ESCOLHA, quando)
    sugestoes = [{"abordagem": a, "texto": f"texto {a}", "recomendada": a == "lembrete_cordial",
                  "origem_texto": "template", "codigo_template": "t"} for a in cc.ABORDAGENS]
    cc.gravar_rodada(ciclo_id, sugestoes, canal, "contato_da_base", "insufficient_funds",
                     "pix_automatico", "cordial", quando)
    escolhida = cc.registrar_escolha(ciclo_id, 1, "lembrete_cordial", "owner", quando)
    cc.transicionar(ciclo_id, cc.MENSAGEM_ENVIADA, quando)
    cc.marcar_mensagem(escolhida["id"], quando, enviada=True, canal=canal)
    assert cc.confirmar_mensagem(ciclo_id, quando)


def _recuperado_na_tentativa(tenant, rec, numero=2, valor=200.0, dias_atras=5, causa="insufficient_funds"):
    """Falhou, as tentativas anteriores falharam e a `numero` pagou. Fee de 15%."""
    ciclo_id, aberto = _ciclo(tenant, rec, valor, causa, dias_atras)
    for n in range(1, numero):
        _tentar(ciclo_id, n, cc.FALHOU, aberto + timedelta(hours=n), valor)
    quando = aberto + timedelta(hours=numero)
    _tentar(ciclo_id, numero, None, quando, valor)
    cc.fechar_como_recuperado(ciclo_id, round(valor * 0.15, 2), quando, id_cobranca=f"t-{ciclo_id}-{numero}")
    return ciclo_id, quando


def _recuperado_pela_mensagem(tenant, rec, valor=200.0, dias_atras=5, canal="whatsapp"):
    ciclo_id, aberto = _ciclo(tenant, rec, valor, dias_atras=dias_atras)
    for n in (1, 2, 3):
        _tentar(ciclo_id, n, cc.FALHOU, aberto + timedelta(hours=n), valor)
    _mensagem(ciclo_id, aberto + timedelta(hours=4), canal)
    quando = aberto + timedelta(hours=6)
    cc.fechar_como_recuperado(ciclo_id, round(valor * 0.15, 2), quando)
    return ciclo_id, quando


def _perdido_depois_da_mensagem(tenant, rec, valor=200.0, dias_atras=5, canal="email"):
    ciclo_id, aberto = _ciclo(tenant, rec, valor, dias_atras=dias_atras)
    for n in (1, 2, 3):
        _tentar(ciclo_id, n, cc.FALHOU, aberto + timedelta(hours=n), valor)
    _mensagem(ciclo_id, aberto + timedelta(hours=4), canal)
    cc.transicionar(ciclo_id, cc.PERDIDO, aberto + timedelta(hours=8))
    return ciclo_id


def _descartado(tenant, rec, valor=1.0, dias_atras=5, causa="limit_exceeded"):
    ciclo_id, aberto = _ciclo(tenant, rec, valor, causa, dias_atras)
    cc.descartar(ciclo_id, "eprofit_nao_positivo", aberto + timedelta(minutes=1))
    return ciclo_id


def _oferta(tenant, cid, oferta="desconto_20", mrr=500.0, canal="email", criticidade="critico"):
    return rl.registrar_ciclo({
        "tenant_id": tenant, "user_id": f"user:{cid}", "event": "Cancellation Page Viewed",
        "props": {"mrr": mrr, "days_since_last": 20, "features_used_30d": 1},
        "risk_score": 0.93, "profile": "PJ", "criticality": criticidade,
        "offer_type": oferta, "channel": canal, "offer_sent": True, "accepted": None})


def _aceitar(tenant, cid, oferta="desconto_20", aceitou=True, origem="webhook"):
    assert rl.registrar_desfecho(tenant, f"user:{cid}", oferta, aceitou,
                                 origem=origem) is rl.ResultadoDesfecho.FECHADO


def _base(tenant, *clientes):
    ci.gravar(tenant, [{"customer_id_externo": cid, "mrr": mrr, "billing_profile": "PJ",
                        "days_since_last": dias, "features_used_30d": uso, **extra}
                       for cid, mrr, dias, uso, extra in clientes])


@pytest.fixture
def cenario(cliente):
    """A empresa A com os dois churns: 3 recuperadas (1a tentativa, 2a tentativa
    e depois da mensagem), 1 perdida, 1 descartada, 1 em andamento; no
    voluntario, 1 aceite e 1 recusa."""
    _base(A,
          ("c-ana", 500.0, 60, 0, {"nome": "Ana Prado", "id_recorrencia": "RN_ana",
                                   "email": "pessoa@exemplo.com.br", "telefone": "+5511988887777"}),
          ("c-bia", 300.0, 55, 0, {"nome": "Bia Lemos"}),
          ("c-caio", 100.0, 0, 9, {}))
    _recuperado_na_tentativa(A, "RN_ana", 1, valor=200.0, dias_atras=6)
    _recuperado_na_tentativa(A, "RN_dois", 2, valor=400.0, dias_atras=4)
    _recuperado_pela_mensagem(A, "RN_msg", valor=1000.0, dias_atras=3)
    _perdido_depois_da_mensagem(A, "RN_perdido", valor=300.0, dias_atras=3)
    _descartado(A, "RN_descartado", valor=1.0, dias_atras=2)
    aberto_id, aberto = _ciclo(A, "RN_aberto", 150.0, dias_atras=1)
    _tentar(aberto_id, 1, cc.FALHOU, aberto + timedelta(hours=1), 150.0)
    _oferta(A, "c-ana")
    _aceitar(A, "c-ana")
    _oferta(A, "c-bia", "desconto_10", mrr=300.0, canal="whatsapp")
    _aceitar(A, "c-bia", "desconto_10", aceitou=False)
    return cliente


# == GET /metrics/visao-geral ==============================================

class TestCartoes:
    def test_os_numeros_batem_com_a_conta_a_mao(self, cenario):
        r = _get(cenario, A, "/metrics/visao-geral").json()
        # Involuntario: 200 + 400 + 1000 = 1600; menos 15% = 1360.
        assert r["recuperado_involuntario"] == 1360.0
        assert r["cobrancas_recuperadas"] == 3
        assert r["ciclos_com_desfecho"] == 5
        assert r["taxa_recuperacao"] == 0.6
        assert r["ciclos_ativos"] == 1 and r["aguardando_escolha"] == 0
        # Voluntario: 500 - 20% = 400; menos 15% = 340.
        assert r["retido_voluntario"] == 340.0 and r["clientes_mantidos"] == 1
        assert r["mantido"] == 1700.0
        assert r["clientes_risco_grave"] == 2 and r["risco_grave_com_oferta"] == 2
        assert r["dias"] == 30
        hoje = datas.agora_local().date()
        assert r["periodo"] == {"de": (hoje - timedelta(days=29)).isoformat(), "ate": hoje.isoformat()}

    def test_fora_do_premium_o_voluntario_e_null_e_o_mantido_e_so_o_involuntario(self, cenario):
        r = _get(cenario, A, "/metrics/visao-geral", plano="essencial").json()
        assert r["retido_voluntario"] is None and r["clientes_mantidos"] is None
        assert r["clientes_risco_grave"] is None and r["risco_grave_com_oferta"] is None
        assert r["recuperado_involuntario"] == 1360.0 and r["mantido"] == 1360.0

    def test_sem_desfecho_a_taxa_e_null_e_nao_zero(self, cliente):
        r = _get(cliente, A, "/metrics/visao-geral").json()
        assert r["taxa_recuperacao"] is None and r["ciclos_com_desfecho"] == 0
        assert r["mantido"] == 0 and r["recuperado_involuntario"] == 0

    def test_o_periodo_e_o_pedido(self, cenario):
        # Em 2 dias so cabe o descartado (aberto ha 2 dias): nenhuma recuperacao.
        r = _get(cenario, A, "/metrics/visao-geral", dias=2).json()
        assert r["cobrancas_recuperadas"] == 0 and r["recuperado_involuntario"] == 0
        assert r["ciclos_ativos"] == 1, "ativos sao de agora, nao do periodo"
        for ruim in (0, 366):
            assert _get(cenario, A, "/metrics/visao-geral", dias=ruim).status_code == 422

    def test_o_estorno_do_involuntario_sai_do_recuperado(self, cenario):
        ciclo = cc.ciclo_da_cobranca(A, "cob-RN_dois")
        cc.registrar_estorno(ciclo["id"], "D1", 400.0, 30, _agora())
        r = _get(cenario, A, "/metrics/visao-geral").json()
        # Sai o liquido dos 400 (340): 1360 - 340 = 1020.
        assert r["recuperado_involuntario"] == 1020.0 and r["mantido"] == 1360.0

    def test_aceite_sorteado_fica_fora_a_nao_ser_que_se_peca(self, cliente):
        _base(A, ("c1", 500.0, 60, 0, {}))
        _oferta(A, "c1")
        _aceitar(A, "c1", origem="simulacao")
        assert _get(cliente, A, "/metrics/visao-geral").json()["retido_voluntario"] == 0
        com = _get(cliente, A, "/metrics/visao-geral", incluir_simulados="true").json()
        assert com["retido_voluntario"] == 340.0


# == GET /metrics/serie ====================================================

class TestSerie:
    def test_os_dois_churns_por_dia(self, cenario):
        r = _get(cenario, A, "/metrics/serie").json()
        assert r["dias"] == 30 and len(r["pontos"]) == 30
        por_dia = {p["dia"]: p for p in r["pontos"]}
        hoje = _agora()
        dia = lambda atras: (hoje - timedelta(days=atras)).date().isoformat()   # noqa: E731
        assert por_dia[dia(6)]["involuntario"] == 170.0        # 200 menos 15%
        assert por_dia[dia(4)]["involuntario"] == 340.0        # 400 menos 15%
        assert por_dia[dia(3)]["involuntario"] == 850.0        # 1000 menos 15%
        assert por_dia[datas.agora_local().date().isoformat()]["voluntario"] == 340.0
        assert round(sum(p["involuntario"] for p in r["pontos"]), 2) == 1360.0
        assert round(sum(p["voluntario"] for p in r["pontos"]), 2) == 340.0

    def test_fora_do_premium_o_voluntario_vem_null_em_todo_ponto(self, cenario):
        pontos = _get(cenario, A, "/metrics/serie", plano="essencial").json()["pontos"]
        assert all(p["voluntario"] is None for p in pontos)
        assert round(sum(p["involuntario"] for p in pontos), 2) == 1360.0

    def test_bate_com_a_serie_de_cada_churn(self, cenario):
        juntos = _get(cenario, A, "/metrics/serie").json()["pontos"]
        inv = _get(cenario, A, "/metrics/involuntario/serie").json()["pontos"]
        vol = _get(cenario, A, "/metrics/voluntario/serie").json()["pontos"]
        assert [p["involuntario"] for p in juntos] == [p["valor_liquido_recuperado"] for p in inv]
        assert [p["voluntario"] for p in juntos] == [p["valor_liquido_mantido"] for p in vol]


# == GET /metrics/involuntario/funil =======================================

class TestFunil:
    def _mes_inteiro(self, cliente, tenant=A):
        """O cenario e montado nos ultimos 6 dias: pode atravessar a virada do
        mes. O teste soma o mes corrente e o anterior."""
        hoje = datas.agora_local()
        anterior = (hoje.replace(day=1) - timedelta(days=1))
        meses = [f"{hoje.year:04d}-{hoje.month:02d}", f"{anterior.year:04d}-{anterior.month:02d}"]
        return [_get(cliente, tenant, "/metrics/involuntario/funil", mes=m).json() for m in meses]

    def _somado(self, funis):
        etapas = {}
        for f in funis:
            for e in f["etapas"]:
                alvo = etapas.setdefault(e["etapa"], {"chegaram": 0, "valor": 0.0,
                                                      "recuperados_aqui": 0,
                                                      "valor_recuperado_aqui": 0.0})
                for k in alvo:
                    alvo[k] += e[k]
        desfecho = {k: sum(f["desfecho"][k] for f in funis) for k in funis[0]["desfecho"]}
        return etapas, desfecho

    def test_cada_etapa_e_onde_a_cobranca_voltou(self, cenario):
        funis = self._mes_inteiro(cenario)
        assert [e["etapa"] for e in funis[0]["etapas"]] == [
            "falhas", "tentativa_1", "tentativa_2", "tentativa_3", "mensagem"]
        assert funis[0]["etapas"][0]["rotulo"] == "Cobranças que falharam"
        etapas, desfecho = self._somado(funis)
        # 6 cobrancas: 200 + 400 + 1000 + 300 + 1 + 150.
        assert etapas["falhas"]["chegaram"] == 6 and round(etapas["falhas"]["valor"], 2) == 2051.0
        # Tentativa 1: todas menos a descartada.
        assert etapas["tentativa_1"]["chegaram"] == 5
        assert etapas["tentativa_1"]["recuperados_aqui"] == 1
        assert etapas["tentativa_1"]["valor_recuperado_aqui"] == 200.0
        # Tentativa 2: a de 400, a da mensagem e a perdida.
        assert etapas["tentativa_2"]["chegaram"] == 3
        assert etapas["tentativa_2"]["recuperados_aqui"] == 1
        assert etapas["tentativa_2"]["valor_recuperado_aqui"] == 400.0
        assert etapas["tentativa_3"]["chegaram"] == 2 and etapas["tentativa_3"]["recuperados_aqui"] == 0
        assert etapas["mensagem"]["chegaram"] == 2 and round(etapas["mensagem"]["valor"], 2) == 1300.0
        assert etapas["mensagem"]["recuperados_aqui"] == 1
        assert etapas["mensagem"]["valor_recuperado_aqui"] == 1000.0
        assert desfecho == {"recuperados": 3, "encerrados": 2, "em_andamento": 1}

    def test_estorno_total_tira_a_recuperacao_do_funil(self, cenario):
        ciclo = cc.ciclo_da_cobranca(A, "cob-RN_dois")
        cc.registrar_estorno(ciclo["id"], "D1", 400.0, 30, _agora())
        etapas, desfecho = self._somado(self._mes_inteiro(cenario))
        assert etapas["tentativa_2"]["recuperados_aqui"] == 0
        assert desfecho == {"recuperados": 2, "encerrados": 3, "em_andamento": 1}

    def test_mes_sem_cobranca_vem_zerado_e_mes_invalido_e_422(self, cliente):
        r = _get(cliente, A, "/metrics/involuntario/funil", mes="2020-01").json()
        assert r["mes"] == "2020-01"
        assert all(e["chegaram"] == 0 and e["valor"] == 0 for e in r["etapas"])
        assert r["desfecho"] == {"recuperados": 0, "encerrados": 0, "em_andamento": 0}
        assert _get(cliente, A, "/metrics/involuntario/funil", mes="2020-13").status_code == 422


# == GET /metrics/o-que-funciona ===========================================

class TestOQueFunciona:
    def test_causas_ofertas_e_canais(self, cenario):
        r = _get(cenario, A, "/metrics/o-que-funciona").json()
        causas = {c["rotulo"]: c for c in r["causas"]}
        saldo = causas["Saldo insuficiente"]
        # 4 com desfecho por saldo insuficiente (3 recuperadas, 1 perdida).
        assert saldo["casos"] == 4 and saldo["sucessos"] == 3 and saldo["taxa"] == 0.75
        assert saldo["valor_liquido"] == 1360.0
        limite = next(c for c in r["causas"] if c["casos"] == 1)
        assert limite["sucessos"] == 0 and limite["taxa"] == 0 and limite["valor_liquido"] == 0
        assert r["causas"][0]["rotulo"] == "Saldo insuficiente", "do mais comum ao menos"
        ofertas = {o["rotulo"]: o for o in r["ofertas"]}
        assert ofertas["Desconto de 20% por 3 meses"] == {
            "rotulo": "Desconto de 20% por 3 meses", "casos": 1, "sucessos": 1, "taxa": 1.0}
        assert ofertas["Desconto de 10% por 3 meses"]["taxa"] == 0
        canais = {c["rotulo"]: c for c in r["canais"]}
        # WhatsApp: a mensagem do involuntario que recuperou (1 de 1) e a oferta recusada.
        assert canais["WhatsApp"]["casos"] == 2 and canais["WhatsApp"]["sucessos"] == 1
        # E-mail: a mensagem do ciclo perdido e a oferta aceita.
        assert canais["E-mail"]["casos"] == 2 and canais["E-mail"]["sucessos"] == 1

    def test_recuperada_por_tentativa_depois_da_mensagem_nao_e_resposta_ao_canal(self, cliente):
        ciclo_id, aberto = _ciclo(A, "RN_x", 100.0, dias_atras=3)
        _tentar(ciclo_id, 1, cc.FALHOU, aberto + timedelta(hours=1), 100.0)
        _mensagem(ciclo_id, aberto + timedelta(hours=2))
        _tentar(ciclo_id, 2, None, aberto + timedelta(hours=3), 100.0)
        cc.fechar_como_recuperado(ciclo_id, 15.0, aberto + timedelta(hours=3),
                                  id_cobranca=f"t-{ciclo_id}-2")
        canais = _get(cliente, A, "/metrics/o-que-funciona").json()["canais"]
        assert canais == [{"rotulo": "WhatsApp", "casos": 1, "sucessos": 0, "taxa": 0.0}]

    def test_sem_caso_nenhum_as_listas_vem_vazias(self, cliente):
        r = _get(cliente, A, "/metrics/o-que-funciona").json()
        assert r["causas"] == [] and r["ofertas"] == [] and r["canais"] == []

    def test_fora_do_premium_sem_ofertas_e_canais_so_do_involuntario(self, cenario):
        r = _get(cenario, A, "/metrics/o-que-funciona", plano="essencial").json()
        assert r["ofertas"] == []
        canais = {c["rotulo"]: c for c in r["canais"]}
        assert canais["WhatsApp"]["casos"] == 1 and canais["E-mail"]["casos"] == 1


# == GET /atividade ========================================================

class TestAtividade:
    def test_as_frases_a_ordem_e_o_valor_liquido(self, cenario):
        r = _get(cenario, A, "/atividade", limite=50).json()
        itens = r["atividades"]
        assert r["dias"] == 30
        instantes = [i["em"] for i in itens]
        assert instantes == sorted(instantes, reverse=True), "do mais novo ao mais antigo"
        textos = {i["texto"]: i for i in itens}
        ana = textos["Cobrança de Ana Prado recuperada na 1ª tentativa"]
        assert ana["tipo"] == "recuperado" and ana["valor"] == 170.0 and ana["simulado"] is False
        assert textos["Cobrança de um cliente sem cadastro recuperada na 2ª tentativa"]["valor"] == 340.0
        assert textos["Cobrança de um cliente sem cadastro recuperada depois da mensagem"]["valor"] == 850.0
        aceite = textos["Ana Prado aceitou desconto de 20% por 3 meses"]
        assert aceite["tipo"] == "oferta_aceita" and aceite["valor"] == 340.0
        assert "Mensagem enviada por WhatsApp para um cliente sem cadastro" in textos
        assert "Mensagem enviada por E-mail para um cliente sem cadastro" in textos
        assert any(t.startswith("Tentativa 1 não passou para um cliente sem cadastro: saldo insuficiente")
                   for t in textos)
        assert "Ana Prado entrou em risco grave" in textos
        assert {i["tipo"] for i in itens} <= {"recuperado", "tentativa_falhou", "oferta_aceita",
                                              "mensagem_enviada", "risco_grave", "estorno", "escolha"}
        assert all(i["texto"][:1] == i["texto"][:1].upper() for i in itens)
        assert len({i["id"] for i in itens}) == len(itens)

    def test_estorno_e_escolha_pendente(self, cenario):
        ciclo = cc.ciclo_da_cobranca(A, "cob-RN_ana")
        # O estorno acontece AGORA, e nao "hoje ao meio-dia": antes do meio-dia esse
        # instante estaria no futuro, e a atividade (certa) nao lista o que ainda nao houve.
        cc.registrar_estorno(ciclo["id"], "D1", 200.0, 30, datas.agora_local())
        esperando, aberto = _ciclo(A, "RN_espera", 90.0, dias_atras=1)
        cc.transicionar(esperando, cc.AGUARDANDO_ESCOLHA, aberto + timedelta(hours=1))
        ci.cancelar(A, "c-ana")
        textos = [i["texto"] for i in _get(cenario, A, "/atividade", limite=50).json()["atividades"]]
        assert ("O valor recuperado de Ana Prado voltou ao cliente dentro do prazo e saiu do "
                "recuperado") in textos
        assert "Mensagens sugeridas para um cliente sem cadastro, aguardando a sua escolha" in textos
        assert "Ana Prado cancelou 0 dias depois do aceite; o valor saiu do mantido" in textos

    def test_limite(self, cenario):
        assert len(_get(cenario, A, "/atividade").json()["atividades"]) <= 12
        assert len(_get(cenario, A, "/atividade", limite=2).json()["atividades"]) == 2
        for ruim in (0, 101):
            assert _get(cenario, A, "/atividade", limite=ruim).status_code == 422

    def test_fora_do_premium_so_o_involuntario(self, cenario):
        tipos = {i["tipo"] for i in _get(cenario, A, "/atividade", plano="essencial",
                                         limite=50).json()["atividades"]}
        assert "oferta_aceita" not in tipos and "risco_grave" not in tipos
        assert "recuperado" in tipos

    def test_o_que_e_antigo_fica_de_fora(self, cliente):
        _recuperado_na_tentativa(A, "RN_velho", 1, dias_atras=45)
        assert _get(cliente, A, "/atividade").json()["atividades"] == []

    def test_entra_no_registro_de_acesso(self, cenario):
        _get(cenario, A, "/atividade", papel="membro")
        assert ("GET /atividade", "membro") in [(a["rota"], a["papel"])
                                               for a in registro_acesso.acessos(A)]


# == GET /extrato ==========================================================

class TestExtrato:
    def _linhas(self, cliente, tenant=A, **params):
        hoje = datas.agora_local()
        anterior = (hoje.replace(day=1) - timedelta(days=1))
        linhas = []
        for m in (f"{hoje.year:04d}-{hoje.month:02d}", f"{anterior.year:04d}-{anterior.month:02d}"):
            linhas += _get(cliente, tenant, "/extrato", mes=m, **params).json()["linhas"]
        return linhas

    def test_uma_linha_por_recuperacao_e_por_cliente_mantido_com_a_fee(self, cenario):
        linhas = {l["descricao"]: l for l in self._linhas(cenario)}
        assert len(linhas) == 4
        primeira = linhas["Recuperado na 1ª tentativa"]
        assert primeira["cliente"] == "Ana Prado" and primeira["origem"] == "involuntario"
        assert (primeira["valor_base"], primeira["fee"], primeira["liquido"]) == (200.0, 30.0, 170.0)
        assert primeira["tipo"] == "recuperacao" and primeira["estornado"] is False
        segunda = linhas["Recuperado na 2ª tentativa"]
        assert segunda["cliente"] is None and segunda["id_cliente"] == "RN_dois"
        assert (segunda["valor_base"], segunda["fee"], segunda["liquido"]) == (400.0, 60.0, 340.0)
        assert linhas["Recuperado depois da mensagem"]["liquido"] == 850.0
        mantida = linhas["Aceitou desconto de 20% por 3 meses"]
        assert mantida["origem"] == "voluntario" and mantida["tipo"] == "mantido"
        assert (mantida["mrr"], mantida["desconto"]) == (500.0, 100.0)
        assert (mantida["valor_base"], mantida["fee"], mantida["liquido"]) == (400.0, 60.0, 340.0)
        assert all(l["simulado"] is False for l in linhas.values())

    def test_os_totais_fecham_com_as_linhas_e_com_os_cartoes(self, cenario):
        hoje = datas.agora_local()
        r = _get(cenario, A, "/extrato", mes=f"{hoje.year:04d}-{hoje.month:02d}").json()
        assert r["totais"] == {
            "valor_base": round(sum(l["valor_base"] for l in r["linhas"]), 2),
            "fee": round(sum(l["fee"] for l in r["linhas"]), 2),
            "liquido": round(sum(l["liquido"] for l in r["linhas"]), 2)}
        todas = self._linhas(cenario)
        cartoes = _get(cenario, A, "/metrics/visao-geral").json()
        assert round(sum(l["liquido"] for l in todas), 2) == cartoes["mantido"] == 1700.0
        for l in todas:
            assert round(l["valor_base"] - l["fee"], 2) == l["liquido"]

    def test_o_estorno_e_uma_linha_negativa_no_mes_em_que_aconteceu(self, cenario):
        ciclo = cc.ciclo_da_cobranca(A, "cob-RN_ana")
        cc.registrar_estorno(ciclo["id"], "D1", 200.0, 30, datas.agora_local())
        ci.cancelar(A, "c-ana")
        hoje = datas.agora_local()
        r = _get(cenario, A, "/extrato", mes=f"{hoje.year:04d}-{hoje.month:02d}").json()
        estornos = [l for l in r["linhas"] if l["tipo"] == "estorno"]
        assert len(estornos) == 2
        inv = next(l for l in estornos if l["origem"] == "involuntario")
        assert (inv["valor_base"], inv["fee"], inv["liquido"]) == (-200.0, -30.0, -170.0)
        assert inv["cliente"] == "Ana Prado" and inv["estornado"] is True
        vol = next(l for l in estornos if l["origem"] == "voluntario")
        assert (vol["valor_base"], vol["fee"], vol["liquido"]) == (-400.0, -60.0, -340.0)
        assert vol["descricao"] == "Cancelou 0 dias depois do aceite: estorno"
        # A linha positiva da recuperacao continua la, marcada como estornada depois.
        positiva = next(l for l in self._linhas(cenario) if l["id"] == f"rec-{ciclo['id']}")
        assert positiva["liquido"] == 170.0 and positiva["estornado"] is True
        cartoes = _get(cenario, A, "/metrics/visao-geral").json()
        assert round(sum(l["liquido"] for l in self._linhas(cenario)), 2) == cartoes["mantido"]

    def test_so_dono_e_administrador(self, cenario):
        assert _get(cenario, A, "/extrato", papel="admin").status_code == 200
        for papel in ("membro", None):
            r = cenario.get("/extrato", headers=cenario.projeto.bearer(A, papel=papel)
                            if papel else cenario.projeto.bearer(A))
            assert r.status_code == 403
            assert r.json()["detail"]["motivo"] == "papel_insuficiente"

    def test_entra_no_registro_de_acesso_so_quando_atendida(self, cenario):
        _get(cenario, A, "/extrato", papel="membro")
        assert registro_acesso.acessos(A) == []
        _get(cenario, A, "/extrato", papel="admin")
        assert [(a["rota"], a["papel"]) for a in registro_acesso.acessos(A)] == [
            ("GET /extrato", "admin")]

    def test_fora_do_premium_so_o_involuntario(self, cenario):
        linhas = self._linhas(cenario, plano="essencial")
        assert {l["origem"] for l in linhas} == {"involuntario"} and len(linhas) == 3

    def test_mes_invalido(self, cenario):
        assert _get(cenario, A, "/extrato", mes="outubro").status_code == 422


# == R11, LGPD e isolamento, rota por rota =================================

class TestOQueNaoPodeAparecer:
    @pytest.mark.parametrize("rota", SEM_FEE)
    def test_r11_a_fee_nao_aparece_fora_do_extrato(self, cenario, rota):
        corpo = _get(cenario, A, rota, limite=50).json() if rota == "/atividade" \
            else _get(cenario, A, rota).json()
        chaves = set(_chaves(corpo))
        # (`taxa`, em `/metrics/o-que-funciona`, e a proporcao de sucesso, nao a fee.)
        assert not {"fee", "fee_estornada", "valor_base", "taxa_da_crai"} & chaves, rota
        assert not [c for c in chaves if "fee" in c], rota

    def test_o_extrato_e_a_unica_rota_com_fee(self, cenario):
        com_fee = []
        for rota in app_module.app.routes:
            caminho = getattr(rota, "path", "")
            if not hasattr(rota, "methods") or "GET" not in rota.methods or "{" in caminho:
                continue
            if caminho.startswith(("/simulate", "/dev", "/docs", "/openapi", "/redoc", "/painel")):
                continue
            r = _get(cenario, A, caminho)
            if r.status_code != 200:
                continue
            # Rodada 4: o extrato tambem sai em arquivo (`/extrato/csv`), com a mesma fee.
            if r.headers["content-type"].startswith("text/csv"):
                tem_fee = "Taxa da CRAI" in r.text
            else:
                tem_fee = "fee" in set(_chaves(r.json()))
            if tem_fee:
                com_fee.append(caminho)
        assert com_fee == ["/extrato", "/extrato/csv"]

    @pytest.mark.parametrize("rota", ROTAS)
    def test_nenhum_contato_em_nenhuma_resposta(self, cenario, rota):
        r = _get(cenario, A, rota, limite=50) if rota == "/atividade" else _get(cenario, A, rota)
        assert r.status_code == 200
        for contato in CONTATOS:
            assert contato not in r.text, (rota, contato)
        assert not {"email", "telefone", "phone", "cpf", "chave_pix"} & set(_chaves(r.json())), rota

    @pytest.mark.parametrize("rota", ROTAS)
    def test_sem_token_e_401(self, cliente, rota):
        assert cliente.get(rota).status_code == 401

    @pytest.mark.parametrize("rota", ROTAS)
    def test_nada_de_a_aparece_para_b(self, cenario, rota):
        vazio = _get(cenario, "empresa-c", rota).json()
        r = _get(cenario, B, rota)
        assert r.status_code == 200
        for de_a in ("Ana Prado", "Bia Lemos", "RN_ana", "RN_dois", "RN_msg", "c-ana", "1360",
                     "340.0", "850.0"):
            assert de_a not in r.text, (rota, de_a)
        # B, que nao tem nada, responde igual a uma empresa que nunca existiu.
        assert r.json() == vazio

    def test_os_numeros_de_b_sao_so_os_de_b(self, cenario):
        _recuperado_na_tentativa(B, "RN_ana", 1, valor=50.0, dias_atras=2)
        b = _get(cenario, B, "/metrics/visao-geral").json()
        assert b["recuperado_involuntario"] == 42.5 and b["cobrancas_recuperadas"] == 1
        assert b["retido_voluntario"] == 0 and b["clientes_risco_grave"] == 0
        # O nome e da base de A: o mesmo id de recorrencia em B nao o enxerga.
        textos = [i["texto"] for i in _get(cenario, B, "/atividade").json()["atividades"]]
        assert textos and all("Ana Prado" not in t for t in textos)
        assert _get(cenario, A, "/metrics/visao-geral").json()["recuperado_involuntario"] == 1360.0

    @pytest.mark.parametrize("rota", ROTAS)
    def test_o_tenant_da_query_e_ignorado(self, cenario, rota):
        r = _get(cenario, B, rota, tenant_id=A)
        assert r.status_code == 200 and "Ana Prado" not in r.text and "RN_ana" not in r.text


# == GET /health ===========================================================

class TestHealth:
    def test_sem_token_continua_publica_e_nao_fala_de_empresa_nenhuma(self, cenario):
        r = cenario.get("/health")
        assert r.status_code == 200
        corpo = r.json()
        assert corpo["status"] == "ok" and "relogio" in corpo
        assert "base" not in corpo
        assert set(corpo["modelos"]) == {"carregados", "total", "ausentes"}
        assert corpo["modelos"]["total"] == 4
        assert corpo["modelos"]["carregados"] + len(corpo["modelos"]["ausentes"]) == 4
        # A suite roda sem o modelo do voluntario (conftest): ele aparece como ausente.
        assert "risco_voluntario" in corpo["modelos"]["ausentes"]

    def test_o_redator_segue_a_chave_do_llm(self, cliente, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        assert cliente.get("/health").json()["redator"] == {"disponivel": False}
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-de-teste")
        r = cliente.get("/health")
        assert r.json()["redator"] == {"disponivel": True}
        assert "sk-ant-de-teste" not in r.text

    def test_com_token_traz_a_base_da_empresa_do_token(self, cenario):
        a = cenario.get("/health", headers=cenario.projeto.bearer(A)).json()
        assert a["base"]["informada"] is True and a["base"]["atualizada_em"] is not None
        assert a["base"]["origem"] is None            # gravada sem passar por rota
        cenario.post("/clientes", headers=cenario.projeto.bearer(A, papel="owner"),
                     json={"customer_id_externo": "novo", "mrr": 10.0, "billing_profile": "PJ"})
        assert cenario.get("/health", headers=cenario.projeto.bearer(A)).json()["base"]["origem"] == "api"
        b = cenario.get("/health", headers=cenario.projeto.bearer(B)).json()
        assert b["base"] == {"informada": True, "atualizada_em": None, "origem": None}

    def test_token_invalido_nao_derruba_o_health(self, cenario):
        for cabecalho in ("Bearer lixo", "Bearer crai_live_" + "a" * 43, "lixo"):
            r = cenario.get("/health", headers={"Authorization": cabecalho})
            assert r.status_code == 200 and "base" not in r.json()

    def test_modelo_ausente_aparece_na_contagem(self, cliente, monkeypatch):
        from crai.agent import workflow
        monkeypatch.setattr(workflow._classifier, "is_fitted", False)
        monkeypatch.setattr(workflow._detector, "is_fitted", True)
        monkeypatch.setattr(workflow._payday, "is_fitted", True)
        modelos = cliente.get("/health").json()["modelos"]
        assert modelos["carregados"] == 2
        assert modelos["ausentes"] == ["classificador_de_falha", "risco_voluntario"]

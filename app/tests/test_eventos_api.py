"""tests/test_eventos_api.py - Rodada 3, Fase 5: eventos de comportamento pela chave de API.

O QUE ESTE ARQUIVO MEDE (S1 a S8):

  S1  `POST /eventos` autentica pela chave (`crai_live_`) ou pelo token de
      login, e a chave passa a valer em cinco rotas;
  S2  o corpo e o do webhook do Segment: a mesma validacao (cada corpo torto
      recebe a MESMA resposta nos dois) e o mesmo caminho (o pipeline recebe os
      mesmos argumentos);
  S3  o webhook do Segment continua igual;
  S4  o tenant vem da chave; o `tenant_id` do corpo e ignorado;
  S5  o limite de contato: uma oferta por cliente a cada
      `intervalo_minimo_ofertas_dias`, venha o evento de onde vier;
  S6  o limite proprio de eventos por chave, separado do das rotas de clientes;
  S7  idempotencia: o mesmo evento reenviado conta uma vez;
  S8  a chave e secreta (declarado na documentacao da rota).
"""

import hashlib
import hmac
import inspect
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from crai.accounts import chaves_api
from crai.api import app as app_module
from crai.api import eventos_recebidos
from crai.churn_voluntary import disparo_lote as dl
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import voluntary_agent as va
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao

A, B = "empresa-a", "empresa-b"
SEGREDO_SEGMENT = "segredo_segment_de_teste"
CANCELAMENTO = "Cancellation Page Viewed"
INVENTADA = "crai_live_" + "A" * 43


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setenv("SEGMENT_WEBHOOK_SECRET", SEGREDO_SEGMENT)
    monkeypatch.delenv("CRAI_SIMULATE_OUTCOMES", raising=False)      # o grafo de producao
    monkeypatch.delenv(chaves_api.ENV_LIMITE, raising=False)
    monkeypatch.delenv(chaves_api.ENV_LIMITE_EVENTOS, raising=False)
    chaves_api.limpar_limites()
    va._channel_history.clear()
    yield
    chaves_api.limpar_limites()
    va._channel_history.clear()


@pytest.fixture
def producao(monkeypatch):
    """Os padroes de PRODUCAO da configuracao (a suite roda com os neutros)."""
    for chave, valor in configuracao.PADROES_DE_PRODUCAO.items():
        monkeypatch.setitem(configuracao.PADROES, chave, valor)


@pytest.fixture
def cliente(supabase_falso):
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        c.projeto = supabase_falso
        yield c


def _login(c, tenant=A, papel="owner", plano="premium") -> dict:
    return c.projeto.bearer(tenant, papel=papel, plano=plano)


def _bearer(chave: str) -> dict:
    return {"Authorization": f"Bearer {chave}"}


def _gerar(c, tenant=A):
    r = c.post("/integracao/chaves", json={"nome": "Servidor"}, headers=_login(c, tenant))
    assert r.status_code == 201, r.text
    return r.json()["chave_inteira"], r.json()["chave"]


def _corpo(user="c-1", evento=CANCELAMENTO, **extra) -> dict:
    return {"userId": user, "event": evento,
            "properties": {"mrr": 500.0, "billing_profile": "PJ"}, **extra}


def _evento(c, chave, corpo=None, **extra):
    return c.post("/eventos", json=corpo if corpo is not None else _corpo(**extra),
                  headers=_bearer(chave))


def _segment(c, corpo, tenant=None):
    bruto = corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode()
    headers = {"x-signature": hmac.new(SEGREDO_SEGMENT.encode(), bruto, hashlib.sha1).hexdigest(),
               "content-type": "application/json"}
    if tenant:
        headers[app_module.TENANT_HEADER] = tenant
    return c.post("/webhooks/segment", content=bruto, headers=headers)


def _linhas(tenant=A) -> list:
    """Todas as linhas de `ciclos_retencao` do tenant, da mais antiga a mais nova."""
    conn = sqlite3.connect(rl.caminho_do_banco())
    conn.row_factory = sqlite3.Row
    try:
        return [dict(l) for l in conn.execute(
            "SELECT * FROM ciclos_retencao WHERE tenant_id = ? ORDER BY id", (tenant,))]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _ofertas(tenant=A) -> list:
    return [l for l in _linhas(tenant) if l["offer_type"] and l["offer_sent"]]


def _decisoes(tenant=A) -> list:
    conn = sqlite3.connect(rl.caminho_do_banco())
    conn.row_factory = sqlite3.Row
    try:
        return [dict(l) for l in conn.execute(
            "SELECT * FROM decisoes_automatizadas WHERE tenant_id = ? ORDER BY id", (tenant,))]
    finally:
        conn.close()


@pytest.fixture
def espiao(monkeypatch):
    """Guarda cada chamada do pipeline voluntario, e deixa ele rodar."""
    chamadas = []
    original = app_module._run_voluntary_pipeline

    async def espiar(user_id, event, props, tenant_id=app_module.TENANT_PADRAO):
        chamadas.append({"user_id": user_id, "event": event, "props": dict(props),
                         "tenant_id": tenant_id})
        return await original(user_id=user_id, event=event, props=props, tenant_id=tenant_id)
    monkeypatch.setattr(app_module, "_run_voluntary_pipeline", espiar)
    return chamadas


def _adiantar(monkeypatch, **delta):
    """O relogio do dataset e da trilha passa a marcar agora + delta."""
    quando = datetime.now(timezone.utc) + timedelta(**delta)
    monkeypatch.setattr(rl, "_agora", lambda: quando.isoformat(timespec="seconds"))


# == S1: a chave ou o token ================================================

class TestAutenticacao:
    def test_a_chave_autentica_e_o_evento_roda_o_pipeline(self, cliente, espiao):
        chave, _ = _gerar(cliente)
        r = _evento(cliente, chave)
        assert r.status_code == 200 and r.json() == {"status": "ok", "duplicado": False}
        assert espiao == [{"user_id": "user:c-1", "event": CANCELAMENTO,
                           "props": {"mrr": 500.0, "billing_profile": "PJ"}, "tenant_id": A}]
        assert len(_ofertas(A)) == 1

    @pytest.mark.parametrize("papel", ["owner", "admin", "membro"])
    def test_o_token_de_login_tambem_autentica(self, cliente, espiao, papel):
        r = cliente.post("/eventos", json=_corpo(), headers=_login(cliente, A, papel))
        assert r.status_code == 200 and espiao[0]["tenant_id"] == A

    def test_sem_authorization_e_401_e_nada_roda(self, cliente, espiao):
        assert cliente.post("/eventos", json=_corpo()).status_code == 401
        assert espiao == []

    def test_chave_inventada_e_revogada_respondem_o_mesmo_401(self, cliente, espiao):
        chave, dados = _gerar(cliente)
        assert cliente.delete(f"/integracao/chaves/{dados['id']}",
                              headers=_login(cliente)).status_code == 200
        revogada, inventada = _evento(cliente, chave), _evento(cliente, INVENTADA)
        assert revogada.status_code == inventada.status_code == 401
        assert revogada.json() == inventada.json()
        assert revogada.json()["detail"]["motivo"] == "chave_invalida"
        assert espiao == []

    def test_a_rota_esta_na_lista_fechada_da_chave_que_tem_cinco_rotas(self):
        assert chaves_api.ROTA_DE_EVENTOS == ("POST", "/eventos")
        assert chaves_api.ROTA_DE_EVENTOS in chaves_api.ROTAS_COM_CHAVE
        assert len(chaves_api.ROTAS_COM_CHAVE) == 5

    def test_cada_evento_conta_como_uso_da_chave(self, cliente):
        chave, dados = _gerar(cliente)
        for i in range(3):
            assert _evento(cliente, chave, user=f"c-{i}").status_code == 200
        lista = cliente.get("/integracao/chaves", headers=_login(cliente)).json()["chaves"]
        da_chave = next(c for c in lista if c["id"] == dados["id"])
        assert da_chave["usos_hoje"] == 3 and da_chave["ultimo_uso_em"]

    def test_a_resposta_nao_diz_o_risco_nem_a_oferta(self, cliente):
        chave, _ = _gerar(cliente)
        r = _evento(cliente, chave)
        assert set(r.json()) == {"status", "duplicado"}
        assert len(_ofertas(A)) == 1, "houve oferta, e a resposta nao fala dela"

    def test_s8_a_documentacao_da_rota_diz_que_e_pelo_servidor(self):
        doc = inspect.getdoc(app_module.receber_evento)
        assert "SERVIDOR da" in doc and "crai_live_" in doc


# == S2 e S3: o mesmo corpo, a mesma validacao, o mesmo caminho ============

TORTOS = [
    b"nao e json", b"[]", b'"texto"', b"5", b"null", b"",
    b'{"userId": "a", "properties": {"mrr": NaN}}',
    b'{"userId": "a", "properties": {"mrr": Infinity}}',
    {}, {"event": CANCELAMENTO}, {"userId": ""}, {"userId": 5}, {"userId": ["a"]},
    {"userId": {"a": 1}}, {"anonymousId": 7}, {"userId": None},
    {"userId": "a", "event": 7}, {"userId": "a", "event": ["x"]},
    {"userId": "a", "properties": "texto"}, {"userId": "a", "properties": [1]},
    {"userId": "a", "properties": {"billing_profile": 3}},
    {"userId": "a", "properties": {"billing_profile": ["PJ"]}},
    {"userId": "a", "properties": {"mrr": 10 ** 400}},
]


class TestMesmaValidacaoEMesmoCaminho:
    @pytest.mark.parametrize("corpo", TORTOS, ids=[str(i) for i in range(len(TORTOS))])
    def test_corpo_torto_recebe_a_mesma_resposta_do_webhook_do_segment(self, cliente, espiao, corpo):
        chave, _ = _gerar(cliente)
        bruto = corpo if isinstance(corpo, bytes) else json.dumps(corpo).encode()
        do_segment = _segment(cliente, bruto)
        dos_eventos = cliente.post("/eventos", content=bruto, headers={
            **_bearer(chave), "content-type": "application/json"})
        assert do_segment.status_code in (400, 422), do_segment.text
        assert dos_eventos.status_code == do_segment.status_code
        assert dos_eventos.json() == do_segment.json()
        assert espiao == [], "corpo recusado nao chega ao pipeline"

    @pytest.mark.parametrize("corpo", [
        _corpo(), {"anonymousId": "visita-9", "event": "Session Started"},
        {"userId": "c-2", "event": "Downgrade Clicked",
         "properties": {"mrr": 99.9, "billing_profile": "CLT", "days_since_last": 3,
                        "features_used_30d": 7, "on_site_now": True}},
        {"userId": "c-3", "event": "Evento Que A CRAI Nao Conhece", "properties": None},
        {"userId": "c-4"},
    ])
    def test_o_pipeline_recebe_os_mesmos_argumentos_pelos_dois_caminhos(self, cliente, espiao, corpo):
        chave, _ = _gerar(cliente)
        assert _segment(cliente, corpo, tenant=A).status_code == 200
        assert _evento(cliente, chave, corpo).status_code == 200
        assert len(espiao) == 2 and espiao[0] == espiao[1]

    def test_identificado_e_anonimo_vivem_em_espacos_separados(self, cliente, espiao):
        chave, _ = _gerar(cliente)
        _evento(cliente, chave, {"userId": "mesmo", "event": "Session Started"})
        _evento(cliente, chave, {"anonymousId": "mesmo", "event": "Session Started"})
        assert [c["user_id"] for c in espiao] == ["user:mesmo", "anon:mesmo"]

    def test_as_duas_rotas_usam_a_mesma_funcao_de_validacao_e_o_mesmo_pipeline(self):
        for rota in (app_module.segment_webhook, app_module.receber_evento):
            fonte = inspect.getsource(rota)
            assert "_evento_voluntario_validado(payload" in fonte, rota.__name__
            assert "_run_voluntary_pipeline(" in fonte, rota.__name__
            # Nenhuma das duas valida o evento por conta propria.
            assert "_campo_com_forma(payload" not in fonte, rota.__name__
            assert "_identidade_voluntaria(" not in fonte, rota.__name__

    def test_o_webhook_do_segment_continua_exigindo_a_assinatura(self, cliente, espiao):
        chave, _ = _gerar(cliente)
        sem = cliente.post("/webhooks/segment", json=_corpo())
        com_chave = cliente.post("/webhooks/segment", json=_corpo(), headers=_bearer(chave))
        assert sem.status_code == com_chave.status_code == 401 and espiao == []
        assert _segment(cliente, _corpo(), tenant=A).json() == {"status": "ok"}

    def test_o_webhook_do_segment_nao_ganhou_idempotencia(self, cliente, espiao):
        corpo = _corpo(evento="Session Started", messageId="evt-1")
        assert _segment(cliente, corpo, tenant=A).status_code == 200
        assert _segment(cliente, corpo, tenant=A).status_code == 200
        assert len(espiao) == 2


# == S4: o tenant vem da chave =============================================

class TestTenantDaChave:
    def test_o_tenant_do_corpo_e_do_cabecalho_sao_ignorados(self, cliente, espiao):
        chave_a, _ = _gerar(cliente, A)
        r = cliente.post("/eventos", json=_corpo(tenant_id=B),
                         headers={**_bearer(chave_a), app_module.TENANT_HEADER: B})
        assert r.status_code == 200
        assert espiao[0]["tenant_id"] == A
        assert len(_linhas(A)) == 1 and _linhas(B) == []

    def test_o_mesmo_cliente_em_duas_empresas_sao_dois_clientes(self, cliente, producao):
        chave_a, _ = _gerar(cliente, A)
        chave_b, _ = _gerar(cliente, B)
        assert _evento(cliente, chave_a, user="c-1").status_code == 200
        # A oferta da empresa A nao segura a da B (o limite de contato e por empresa)...
        assert _evento(cliente, chave_b, user="c-1").status_code == 200
        assert len(_ofertas(A)) == 1 and len(_ofertas(B)) == 1
        # ...e o evento da B nao aparece para a A em lugar nenhum.
        assert {l["tenant_id"] for l in _linhas(A)} == {A}
        assert rl.ultima_oferta_enviada(A).keys() == {"user:c-1"} == rl.ultima_oferta_enviada(B).keys()

    def test_a_idempotencia_e_por_empresa(self, cliente, espiao):
        chave_a, _ = _gerar(cliente, A)
        chave_b, _ = _gerar(cliente, B)
        corpo = _corpo(evento="Session Started", messageId="evt-igual")
        assert _evento(cliente, chave_a, corpo).json()["duplicado"] is False
        assert _evento(cliente, chave_b, corpo).json()["duplicado"] is False
        assert [c["tenant_id"] for c in espiao] == [A, B]


# == S5: o limite de contato ===============================================

class TestLimiteDeContato:
    def test_o_padrao_de_producao_e_30_dias(self):
        assert configuracao.PADROES_DE_PRODUCAO["intervalo_minimo_ofertas_dias"] == 30

    def test_segundo_evento_dentro_do_intervalo_nao_gera_oferta(self, cliente, producao):
        chave, _ = _gerar(cliente)
        assert _evento(cliente, chave, messageId="e1").status_code == 200
        assert _evento(cliente, chave, messageId="e2").status_code == 200
        linhas = _linhas(A)
        assert len(linhas) == 2 and len(_ofertas(A)) == 1
        assert linhas[1]["offer_type"] is None and not linhas[1]["offer_sent"]
        assert linhas[1]["risk_score"] >= va.CORTE_DE_INTERVENCAO
        # A decisao de NAO ofertar vai para a trilha, com a regra e o motivo.
        da_regra = [d for d in _decisoes(A) if va.REGRA_DO_LIMITE_DE_CONTATO in d["saida"]]
        assert len(da_regra) == 1
        assert da_regra[0]["tipo_decisao"] == rl.TIPO_OFERTA and da_regra[0]["modelo"] == rl.MODELO_REGRA
        assert "permite uma oferta a cada 30 dias" in da_regra[0]["explicacao"]
        assert rl.verificar_cadeia(A)["integra"] is True

    def test_depois_do_intervalo_ha_oferta_de_novo(self, cliente, producao, monkeypatch):
        chave, _ = _gerar(cliente)
        _evento(cliente, chave, messageId="e1")
        _adiantar(monkeypatch, days=29, hours=23)
        _evento(cliente, chave, messageId="e2")
        assert len(_ofertas(A)) == 1, "29 dias e 23 horas: ainda dentro do intervalo"
        _adiantar(monkeypatch, days=30, minutes=1)
        _evento(cliente, chave, messageId="e3")
        assert len(_ofertas(A)) == 2

    def test_o_intervalo_e_o_da_configuracao_da_empresa(self, cliente, producao, monkeypatch):
        r = cliente.put("/configuracao", json={"intervalo_minimo_ofertas_dias": 7},
                        headers=_login(cliente, A))
        assert r.status_code == 200 and r.json()["configuracao"]["intervalo_minimo_ofertas_dias"] == 7
        chave_a, _ = _gerar(cliente, A)
        chave_b, _ = _gerar(cliente, B)
        for chave in (chave_a, chave_b):
            _evento(cliente, chave, messageId="e1")
        _adiantar(monkeypatch, days=8)
        for chave in (chave_a, chave_b):
            _evento(cliente, chave, messageId="e2")
        assert len(_ofertas(A)) == 2, "a empresa A escolheu 7 dias"
        assert len(_ofertas(B)) == 1, "a empresa B continua com os 30 do padrao"

    @pytest.mark.parametrize("valor", [0, -1, 366, "30", 7.5, True, None, [30]])
    def test_valor_invalido_do_intervalo_e_recusado(self, cliente, valor):
        r = cliente.put("/configuracao", json={"intervalo_minimo_ofertas_dias": valor},
                        headers=_login(cliente, A))
        assert r.status_code == 422, valor
        with pytest.raises(configuracao.ConfiguracaoInvalida):
            configuracao.validar({"intervalo_minimo_ofertas_dias": valor})

    @pytest.mark.parametrize("valor", [1, 30, 365])
    def test_valor_valido_do_intervalo(self, valor):
        assert configuracao.validar({"intervalo_minimo_ofertas_dias": valor}) == {
            "intervalo_minimo_ofertas_dias": valor}

    def test_a_configuracao_mostra_o_intervalo(self, cliente, producao):
        r = cliente.get("/configuracao", headers=_login(cliente, A))
        assert r.json()["configuracao"]["intervalo_minimo_ofertas_dias"] == 30

    def test_vale_entre_as_origens_segment_depois_eventos(self, cliente, producao):
        chave, _ = _gerar(cliente)
        assert _segment(cliente, _corpo(), tenant=A).status_code == 200
        assert _evento(cliente, chave).status_code == 200
        assert len(_linhas(A)) == 2 and len(_ofertas(A)) == 1

    def test_vale_entre_as_origens_eventos_depois_segment(self, cliente, producao):
        chave, _ = _gerar(cliente)
        assert _evento(cliente, chave).status_code == 200
        assert _segment(cliente, _corpo(), tenant=A).status_code == 200
        assert _segment(cliente, _corpo(evento="Downgrade Clicked"), tenant=A).status_code == 200
        assert len(_linhas(A)) == 3 and len(_ofertas(A)) == 1

    def test_o_limite_e_por_cliente_final(self, cliente, producao):
        chave, _ = _gerar(cliente)
        for user in ("c-1", "c-2", "c-1", "c-3", "c-2"):
            _evento(cliente, chave, user=user, messageId=f"e-{len(_linhas(A))}")
        assert sorted(l["user_id"] for l in _ofertas(A)) == ["user:c-1", "user:c-2", "user:c-3"]

    def test_evento_sem_risco_nao_gasta_o_intervalo(self, cliente, producao):
        chave, _ = _gerar(cliente)
        _evento(cliente, chave, evento="Session Started", messageId="e1")
        assert _ofertas(A) == [] and rl.ultima_oferta_enviada(A) == {}
        _evento(cliente, chave, messageId="e2")
        assert len(_ofertas(A)) == 1

    def test_o_disparo_em_lote_respeita_o_mesmo_limite(self, cliente, producao):
        chave, _ = _gerar(cliente)
        _evento(cliente, chave, user="c-1")
        assert rl.registrar_desfecho(A, "user:c-1", _ofertas(A)[0]["offer_type"], False) \
            is rl.ResultadoDesfecho.FECHADO
        linha = {"customer_id_externo": "c-1", "criticality": "critico", "risk_score": 0.95}
        recentes = {u: va.oferta_recente(A, u, rl.ultima_oferta_enviada(A))
                    for u in rl.ultima_oferta_enviada(A)}
        motivo = dl.motivo_para_pular(linha, set(), recentes)
        assert motivo == ("limite_de_contato", "o cliente recebeu uma oferta há menos de 1 dia, "
                                               "e a empresa permite uma oferta a cada 30 dias")
        assert va.frase_do_limite({"dias_desde_a_ultima_oferta": 1,
                                   "intervalo_minimo_ofertas_dias": 7}).startswith(
            "o cliente recebeu uma oferta há 1 dia, ")
        assert "há 12 dias" in va.frase_do_limite({"dias_desde_a_ultima_oferta": 12,
                                                   "intervalo_minimo_ofertas_dias": 30})
        assert dl.motivo_para_pular({**linha, "customer_id_externo": "c-2"}, set(), recentes) is None
        assert dl.motivo_para_pular(linha, set()) is None

    def test_oferta_recente_e_dias_desde(self, producao):
        agora = datetime.now(timezone.utc)
        ha = lambda **d: (agora - timedelta(**d)).isoformat(timespec="seconds")   # noqa: E731
        assert va.oferta_recente(A, "u", {}) is None
        assert va.oferta_recente(A, "u", {"u": ha(days=3, hours=1)}) == {
            "dias_desde_a_ultima_oferta": 3, "intervalo_minimo_ofertas_dias": 30}
        assert va.oferta_recente(A, "u", {"u": ha(days=30, minutes=1)}) is None
        assert va.oferta_recente(A, "u", {"u": "data torta"}) is None
        assert va.oferta_recente(A, "u", {"outro": ha(days=1)}) is None
        assert va.dias_desde(ha(days=2)) == pytest.approx(2, abs=0.01)
        assert va.dias_desde(None) is None


# == S6: o limite proprio de eventos =======================================

class TestLimiteDeEventos:
    def test_o_padrao_e_600_por_minuto(self, monkeypatch):
        assert chaves_api.limite_de_eventos_por_minuto() == 600 == chaves_api.LIMITE_EVENTOS_PADRAO
        for torto in ("", "abc", "0", "-3", "2.5"):
            monkeypatch.setenv(chaves_api.ENV_LIMITE_EVENTOS, torto)
            assert chaves_api.limite_de_eventos_por_minuto() == 600, torto
        monkeypatch.setenv(chaves_api.ENV_LIMITE_EVENTOS, "7")
        assert chaves_api.limite_de_eventos_por_minuto() == 7

    def test_acima_do_limite_e_429_e_o_evento_nao_e_processado(self, cliente, espiao, monkeypatch):
        monkeypatch.setenv(chaves_api.ENV_LIMITE_EVENTOS, "2")
        chave, _ = _gerar(cliente)
        assert _evento(cliente, chave, user="c-1").status_code == 200
        assert _evento(cliente, chave, user="c-2").status_code == 200
        r = _evento(cliente, chave, user="c-3")
        assert r.status_code == 429 and r.json()["detail"]["motivo"] == "limite_de_eventos"
        assert 1 <= int(r.headers["Retry-After"]) <= 60
        assert "2 eventos por minuto" in r.json()["detail"]["detalhe"]
        assert len(espiao) == 2 and eventos_recebidos.contar(A) == 2

    def test_e_separado_do_limite_das_rotas_de_clientes(self, cliente, monkeypatch):
        monkeypatch.setenv(chaves_api.ENV_LIMITE_EVENTOS, "1")
        monkeypatch.setenv(chaves_api.ENV_LIMITE, "1")
        chave, _ = _gerar(cliente)
        cadastro = {"customer_id_externo": "c-9", "mrr": 100.0, "billing_profile": "PJ"}
        assert _evento(cliente, chave, user="c-1").status_code == 200
        assert _evento(cliente, chave, user="c-2").status_code == 429
        # O limite dos eventos estourou, e a API de clientes continua atendendo...
        assert cliente.post("/clientes", json=cadastro, headers=_bearer(chave)).status_code in (200, 201)
        r = cliente.post("/clientes", json=cadastro, headers=_bearer(chave))
        # ...ate estourar o dela, que tem o motivo dela.
        assert r.status_code == 429 and r.json()["detail"]["motivo"] == "limite_de_uso"

    def test_e_por_chave(self, cliente, monkeypatch):
        monkeypatch.setenv(chaves_api.ENV_LIMITE_EVENTOS, "1")
        chave_1, _ = _gerar(cliente)
        chave_2, _ = _gerar(cliente)
        assert _evento(cliente, chave_1, user="c-1").status_code == 200
        assert _evento(cliente, chave_1, user="c-2").status_code == 429
        assert _evento(cliente, chave_2, user="c-3").status_code == 200

    def test_a_janela_e_de_um_minuto(self, cliente, monkeypatch):
        monkeypatch.setenv(chaves_api.ENV_LIMITE_EVENTOS, "1")
        relogio = [100.0]
        monkeypatch.setattr(chaves_api, "_relogio", lambda: relogio[0])
        chave, _ = _gerar(cliente)
        assert _evento(cliente, chave, user="c-1").status_code == 200
        relogio[0] += 59
        assert _evento(cliente, chave, user="c-2").status_code == 429
        relogio[0] += 1
        assert _evento(cliente, chave, user="c-3").status_code == 200


# == S7: idempotencia ======================================================

class TestIdempotencia:
    def test_o_mesmo_message_id_conta_uma_vez(self, cliente, espiao):
        chave, _ = _gerar(cliente)
        primeiro = _evento(cliente, chave, messageId="evt-1")
        segundo = _evento(cliente, chave, messageId="evt-1")
        assert primeiro.json() == {"status": "ok", "duplicado": False}
        assert segundo.status_code == 200 and segundo.json() == {"status": "ok", "duplicado": True}
        assert len(espiao) == 1 and len(_linhas(A)) == 1

    def test_o_message_id_manda_mesmo_com_o_resto_diferente(self, cliente, espiao):
        chave, _ = _gerar(cliente)
        _evento(cliente, chave, user="c-1", evento="Session Started", messageId="evt-1")
        r = _evento(cliente, chave, user="c-2", evento=CANCELAMENTO, messageId="evt-1")
        assert r.json()["duplicado"] is True and len(espiao) == 1

    def test_sem_message_id_o_corpo_identico_conta_uma_vez(self, cliente, espiao):
        chave, _ = _gerar(cliente)
        corpo = _corpo(evento="Session Started", timestamp="2026-10-04T12:00:00Z")
        assert _evento(cliente, chave, corpo).json()["duplicado"] is False
        assert _evento(cliente, chave, dict(reversed(list(corpo.items())))).json()["duplicado"] is True
        assert len(espiao) == 1

    def test_sem_message_id_outro_timestamp_e_outro_evento(self, cliente, espiao):
        chave, _ = _gerar(cliente)
        for hora in ("12:00:00", "12:00:01"):
            r = _evento(cliente, chave, _corpo(evento="Session Started",
                                                 timestamp=f"2026-10-04T{hora}Z"))
            assert r.json()["duplicado"] is False
        assert len(espiao) == 2

    def test_message_ids_diferentes_sao_eventos_diferentes(self, cliente, espiao):
        chave, _ = _gerar(cliente)
        for i in range(3):
            assert _evento(cliente, chave, evento="Session Started",
                           messageId=f"evt-{i}").json()["duplicado"] is False
        assert len(espiao) == 3 and eventos_recebidos.contar(A) == 3

    @pytest.mark.parametrize("message_id", ["", "   ", 7, ["a"], {"a": 1}, "x" * 129, True])
    def test_message_id_torto_e_422_e_nada_roda(self, cliente, espiao, message_id):
        chave, _ = _gerar(cliente)
        r = _evento(cliente, chave, messageId=message_id)
        assert r.status_code == 422 and r.json()["detail"]["campo"] == "messageId"
        assert espiao == [] and eventos_recebidos.contar(A) == 0

    def test_message_id_no_tamanho_maximo_passa(self, cliente):
        chave, _ = _gerar(cliente)
        assert _evento(cliente, chave, messageId="x" * 128).status_code == 200

    def test_se_o_processamento_falha_o_reenvio_processa(self, cliente, monkeypatch):
        chave, _ = _gerar(cliente)
        chamadas = []
        original = app_module._run_voluntary_pipeline

        async def falha_uma_vez(user_id, event, props, tenant_id=app_module.TENANT_PADRAO):
            chamadas.append(user_id)
            if len(chamadas) == 1:
                raise RuntimeError("queda no meio do processamento")
            return await original(user_id=user_id, event=event, props=props, tenant_id=tenant_id)
        monkeypatch.setattr(app_module, "_run_voluntary_pipeline", falha_uma_vez)
        assert _evento(cliente, chave, messageId="evt-1").status_code == 500
        assert eventos_recebidos.contar(A) == 0
        reenvio = _evento(cliente, chave, messageId="evt-1")
        assert reenvio.status_code == 200 and reenvio.json()["duplicado"] is False
        assert _evento(cliente, chave, messageId="evt-1").json()["duplicado"] is True
        assert len(chamadas) == 2 and len(_linhas(A)) == 1

    def test_a_tabela_guarda_so_o_hash(self, cliente):
        chave, _ = _gerar(cliente)
        _evento(cliente, chave, user="cliente-final-secreto", messageId="evt-secreto-123")
        conn = sqlite3.connect(cc.caminho_do_banco())
        try:
            linhas = conn.execute("SELECT tenant_id, chave, recebido_em FROM eventos_recebidos").fetchall()
        finally:
            conn.close()
        assert len(linhas) == 1 and linhas[0][0] == A
        assert len(linhas[0][1]) == 64 and int(linhas[0][1], 16) >= 0
        bruto = cc.caminho_do_banco().read_bytes()
        assert b"evt-secreto-123" not in bruto and b"cliente-final-secreto" not in bruto

    def test_registro_antigo_e_apagado_e_o_reenvio_vira_evento_novo(self):
        agora = datetime(2026, 10, 4, 12, 0, 0)
        assert eventos_recebidos.registrar(A, "a" * 64, agora) is True
        assert eventos_recebidos.registrar(A, "a" * 64, agora + timedelta(days=29)) is False
        assert eventos_recebidos.registrar(A, "b" * 64, agora + timedelta(days=31)) is True
        assert eventos_recebidos.contar(A) == 1, "o de 31 dias atras saiu"
        assert eventos_recebidos.registrar(A, "a" * 64, agora + timedelta(days=31)) is True

    def test_a_chave_de_idempotencia(self):
        k = eventos_recebidos.chave_do_evento
        base = _corpo(timestamp="t1")
        assert k(A, {**base, "messageId": "m1"}) == k(A, {"messageId": " m1 ", "userId": "outro"})
        assert k(A, {**base, "messageId": "m1"}) != k(B, {**base, "messageId": "m1"})
        assert k(A, base) == k(A, {**base, "context": {"ip": "ignorado"}})
        assert k(A, base) != k(A, {**base, "timestamp": "t2"})
        assert k(A, base) != k(A, {**base, "properties": {"mrr": 501.0, "billing_profile": "PJ"}})
        assert len(k(A, base)) == 64

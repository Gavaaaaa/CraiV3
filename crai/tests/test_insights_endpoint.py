"""tests/test_insights_endpoint.py — a rota que o frontend consome.

O que o Sprint 4 do self-service promete, e o que aqui é verificado:

  (a) sem auth → 401; com auth → lista ordenada, com `total_clientes`,
      `gerado_em` e as duas origens;
  (b) `?limite=N` e `?criticidade_minima=alto|critico` filtram; valor torto é
      422 com motivo, nunca filtro silencioso;
  (c) tenant A nunca recebe insight de tenant B;
  (d) POST /insights/enviar manda para o e-mail DO TOKEN (e só ele), simula
      sem SMTP, e reporta falha de SMTP sem virar 500;
  (e) involuntário e voluntário (evento a evento) seguem intactos: o webhook
      do Segment continua gravando ciclo e o ciclo aparece no /insights.

Uso:
    pytest tests/test_insights_endpoint.py -v
"""

import hashlib
import hmac
import json
import smtplib

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import voluntary_agent as va
from crai.churn_voluntary import offer_bandit as ob
from crai.integrations import email_sender

TENANT = "empresa-exemplo"
SEGREDO = "segredo_de_teste"


def _cliente(cid, mrr=300.0, perfil="CLT", dias=None, uso=None, email=None):
    return {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil,
            "days_since_last": dias, "features_used_30d": uso, "email": email}


@pytest.fixture
def cliente():
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def base_padrao():
    ci.gravar(TENANT, [
        _cliente("frio", dias=30, uso=0, mrr=100.0),
        _cliente("quase", dias=18, uso=1, mrr=100.0),
        _cliente("grande", dias=2, uso=12, mrr=2500.0),
        _cliente("ativo", dias=0, uso=30, mrr=100.0),
        _cliente("sem-dado", mrr=100.0),
    ])


# ── (a) identidade e forma da resposta ───────────────────────────────────

class TestRota:
    def test_sem_auth_e_401(self, cliente, supabase_falso):
        r = cliente.get("/insights")
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "sem_authorization"

    def test_token_invalido_e_401(self, cliente, supabase_falso):
        r = cliente.get("/insights", headers={"Authorization": "Bearer lixo"})
        assert r.status_code == 401

    def test_com_auth_lista_ordenada(self, cliente, supabase_falso, base_padrao):
        r = cliente.get("/insights", headers=supabase_falso.bearer(TENANT))
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["total_clientes"] == 5
        assert corpo["gerado_em"].endswith("+00:00")
        assert corpo["filtros"] == {"limite": None, "criticidade_minima": None}
        ids = [l["customer_id_externo"] for l in corpo["clientes_em_risco"]]
        assert ids == ["frio", "quase", "grande", "ativo", "sem-dado"]
        assert all(l["origem"] == "upload" for l in corpo["clientes_em_risco"])
        assert corpo["clientes_em_risco"][-1]["risk_score"] is None
        assert corpo["clientes_em_risco"][-1]["criticality"] == "dado_insuficiente"
        for l in corpo["clientes_em_risco"]:
            assert {"customer_id_externo", "risk_score", "criticality", "explicacao",
                    "origem", "atualizado_em"} <= set(l)

    def test_base_vazia_e_lista_vazia(self, cliente, supabase_falso):
        r = cliente.get("/insights", headers=supabase_falso.bearer("empresa-nova"))
        assert r.status_code == 200
        assert r.json() == {**r.json(), "total_clientes": 0, "clientes_em_risco": []}

    def test_sem_destino_da_base_e_500_claro(self, cliente, supabase_falso, monkeypatch):
        monkeypatch.delenv("CRAI_CLIENTES_DB", raising=False)
        r = cliente.get("/insights", headers=supabase_falso.bearer(TENANT))
        assert r.status_code == 500
        assert r.json()["detail"]["motivo"] == "base_nao_configurada"


# ── (b) filtros ──────────────────────────────────────────────────────────

class TestFiltros:
    def test_limite(self, cliente, supabase_falso, base_padrao):
        r = cliente.get("/insights?limite=2", headers=supabase_falso.bearer(TENANT))
        corpo = r.json()
        assert corpo["total_clientes"] == 5                      # total ANTES do filtro
        assert [l["customer_id_externo"] for l in corpo["clientes_em_risco"]] == ["frio", "quase"]
        assert corpo["filtros"]["limite"] == 2

    def test_criticidade_minima(self, cliente, supabase_falso, base_padrao):
        h = supabase_falso.bearer(TENANT)
        alto = cliente.get("/insights?criticidade_minima=alto", headers=h).json()
        assert {l["customer_id_externo"] for l in alto["clientes_em_risco"]} == {"frio", "grande"}
        critico = cliente.get("/insights?criticidade_minima=CRITICO", headers=h).json()
        assert {l["customer_id_externo"] for l in critico["clientes_em_risco"]} == {"frio", "grande"}
        assert critico["filtros"]["criticidade_minima"] == "critico"

    def test_filtro_torto_e_422(self, cliente, supabase_falso, base_padrao):
        h = supabase_falso.bearer(TENANT)
        r = cliente.get("/insights?criticidade_minima=urgente", headers=h)
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "criticidade_invalida"
        r = cliente.get("/insights?limite=0", headers=h)
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "limite_invalido"
        r = cliente.get("/insights?limite=abc", headers=h)
        assert r.status_code == 422


# ── (c) isolamento ───────────────────────────────────────────────────────

class TestIsolamento:
    def test_tenant_a_nunca_recebe_insight_de_tenant_b(self, cliente, supabase_falso):
        ci.gravar("empresa-a", [_cliente("a-1", dias=30, uso=0)])
        ci.gravar("empresa-b", [_cliente("b-1", dias=30, uso=0)])
        a = cliente.get("/insights", headers=supabase_falso.bearer("empresa-a")).json()
        b = cliente.get("/insights", headers=supabase_falso.bearer("empresa-b")).json()
        assert [l["customer_id_externo"] for l in a["clientes_em_risco"]] == ["a-1"]
        assert [l["customer_id_externo"] for l in b["clientes_em_risco"]] == ["b-1"]

    def test_header_x_tenant_id_nao_muda_o_tenant(self, cliente, supabase_falso):
        ci.gravar("empresa-b", [_cliente("b-1", dias=30, uso=0)])
        h = {**supabase_falso.bearer("empresa-a"), "x-tenant-id": "empresa-b"}
        r = cliente.get("/insights?tenant_id=empresa-b", headers=h)
        assert r.status_code == 200
        assert r.json()["clientes_em_risco"] == []


# ── (d) envio por e-mail ─────────────────────────────────────────────────

class TestEnviar:
    def test_simula_sem_smtp_e_manda_para_o_email_do_token(self, cliente, supabase_falso,
                                                            base_padrao, monkeypatch):
        monkeypatch.delenv("CRAI_SMTP_HOST", raising=False)
        h = supabase_falso.bearer(TENANT, email="financeiro@empresa.com.br")
        r = cliente.post("/insights/enviar?limite=3", headers=h)
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["enviado"] is True
        assert corpo["simulado"] is True
        assert corpo["destinatario"] == "financeiro@empresa.com.br"
        assert corpo["linhas"] == 3
        assert corpo["total_clientes"] == 5

    def test_token_sem_email_e_422(self, cliente, supabase_falso, base_padrao):
        h = supabase_falso.bearer(TENANT)
        r = cliente.post("/insights/enviar", headers=h)
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "conta_sem_email"

    def test_sem_auth_e_401(self, cliente, supabase_falso):
        assert cliente.post("/insights/enviar").status_code == 401

    def test_destinatario_nao_vem_do_corpo(self, cliente, supabase_falso, base_padrao,
                                           monkeypatch):
        """Nem query nem JSON escolhem o destinatário — só o token."""
        monkeypatch.delenv("CRAI_SMTP_HOST", raising=False)
        h = supabase_falso.bearer(TENANT, email="dona@empresa.com.br")
        r = cliente.post("/insights/enviar?destinatario=alvo@outra.com", headers=h,
                         json={"destinatario": "alvo@outra.com"})
        assert r.status_code == 200
        assert r.json()["destinatario"] == "dona@empresa.com.br"

    def test_smtp_configurado_envia_de_verdade(self, cliente, supabase_falso, base_padrao,
                                               monkeypatch):
        enviados = []

        class SMTPFalso:
            def __init__(self, host, porta, timeout):
                enviados.append(("conectou", host, porta))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def starttls(self):
                enviados.append(("starttls",))

            def login(self, u, s):
                enviados.append(("login", u))

            def send_message(self, msg):
                enviados.append(("msg", msg["To"], msg["From"], msg["Subject"],
                                 msg.get_content()))

        monkeypatch.setenv("CRAI_SMTP_HOST", "smtp.exemplo.com")
        monkeypatch.setenv("CRAI_SMTP_PORT", "2525")
        monkeypatch.setenv("CRAI_SMTP_USER", "usuario")
        monkeypatch.setenv("CRAI_SMTP_PASSWORD", "senha")
        monkeypatch.setenv("CRAI_EMAIL_FROM", "crai@exemplo.com")
        monkeypatch.setattr(smtplib, "SMTP", SMTPFalso)

        h = supabase_falso.bearer(TENANT, email="fin@empresa.com.br")
        r = cliente.post("/insights/enviar?criticidade_minima=alto", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["simulado"] is False and r.json()["enviado"] is True

        assert enviados[0] == ("conectou", "smtp.exemplo.com", 2525)
        assert ("starttls",) in enviados and ("login", "usuario") in enviados
        _, para, de, assunto, corpo = enviados[-1]
        assert para == "fin@empresa.com.br" and de == "crai@exemplo.com"
        assert "empresa-exemplo" in assunto
        assert "frio" in corpo and "grande" in corpo
        assert "ativo" not in corpo                 # filtrado por criticidade
        assert "R$" in corpo

    def test_falha_de_smtp_e_502_com_motivo_nao_500(self, cliente, supabase_falso,
                                                    base_padrao, monkeypatch):
        class SMTPQuebrado:
            def __init__(self, *a, **k):
                raise ConnectionRefusedError("recusado")

        monkeypatch.setenv("CRAI_SMTP_HOST", "smtp.exemplo.com")
        monkeypatch.setattr(smtplib, "SMTP", SMTPQuebrado)
        h = supabase_falso.bearer(TENANT, email="fin@empresa.com.br")
        r = cliente.post("/insights/enviar", headers=h)
        assert r.status_code == 502
        assert r.json()["enviado"] is False
        assert "recusado" in r.json()["motivo"]

    def test_corpo_do_email_nao_vaza_email_dos_clientes_finais(self):
        linhas = [{"customer_id_externo": "c-1", "risk_score": 0.9, "criticality": "critico",
                   "origem": "upload", "explicacao": "x", "email": "cliente@final.com"}]
        corpo = email_sender.montar_corpo("empresa", linhas)
        assert "cliente@final.com" not in corpo
        assert "c-1" in corpo


# ── (e) o caminho do SDK segue intacto, e alimenta o /insights ───────────

class TestSdkIntacto:
    @pytest.fixture(autouse=True)
    def ambiente(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ENV", "development")
        monkeypatch.setenv("SEGMENT_WEBHOOK_SECRET", SEGREDO)
        monkeypatch.setattr(ob, "MODELS_DIR", tmp_path)
        monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "bandit_state.json")
        va._channel_history.clear()

    def test_evento_do_segment_vira_linha_sdk_no_insights(self, cliente, supabase_falso):
        payload = json.dumps({
            "userId": "cli-sdk-1", "event": "Cancellation Page Viewed",
            "properties": {"days_since_last": 3, "features_used_30d": 8,
                           "mrr": 450.0, "billing_profile": "PJ"},
            "tenant_id": TENANT,
        }).encode()
        assinatura = hmac.new(SEGREDO.encode(), payload, hashlib.sha1).hexdigest()
        r = cliente.post("/webhooks/segment", content=payload,
                         headers={"x-signature": assinatura,
                                  "content-type": "application/json"})
        assert r.status_code == 200, r.text

        ciclos = rl.ultimo_ciclo_por_cliente(TENANT)
        assert len(ciclos) == 1 and ciclos[0]["user_id"] == "user:cli-sdk-1"

        r = cliente.get("/insights", headers=supabase_falso.bearer(TENANT))
        (linha,) = r.json()["clientes_em_risco"]
        assert linha["customer_id_externo"] == "cli-sdk-1"
        assert linha["origem"] == "sdk"
        assert linha["risk_score"] == 0.90
        assert linha["criticality"] == "critico"

    def test_segment_sem_supabase_configurado_continua_funcionando(self, cliente, monkeypatch):
        """O caminho (1) não depende do Supabase: sem SUPABASE_PROJECT_URL o
        webhook aceita o evento assinado como sempre."""
        monkeypatch.delenv("SUPABASE_PROJECT_URL", raising=False)
        payload = json.dumps({"userId": "u1", "event": "Session Started",
                              "properties": {"days_since_last": 1, "features_used_30d": 9}}).encode()
        assinatura = hmac.new(SEGREDO.encode(), payload, hashlib.sha1).hexdigest()
        r = cliente.post("/webhooks/segment", content=payload,
                         headers={"x-signature": assinatura,
                                  "content-type": "application/json"})
        assert r.status_code == 200, r.text

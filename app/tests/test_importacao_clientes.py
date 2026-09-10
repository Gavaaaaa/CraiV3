"""tests/test_importacao_clientes.py — a empresa anexa a base que já tem.

O que o Sprint 2 do self-service promete, e o que aqui é verificado:

  (a) CSV e XLSX com o mesmo conteúdo importam IGUAL;
  (b) reimportar o mesmo `customer_id_externo` ATUALIZA, não duplica;
  (c) uma linha inválida é reportada com o número da linha da planilha e
      NÃO derruba o resto do lote;
  (d) `mapeamento` resolve coluna com nome diferente;
  (e) coluna obrigatória ausente aparece em `colunas_nao_encontradas` e nada
      é importado; opcional ausente é só reportada;
  (f) `linhas_sem_dado_comportamental` conta o que o Sprint 3 não vai pontuar;
  (g) tenant A nunca lê a base de tenant B; sem JWT é 401.

A base vai para um SQLite em `tmp_path` (`CRAI_CLIENTES_DB`, isolado no
`conftest`) com o MESMO SQL que roda no Postgres do Supabase em produção.
O Supabase de autenticação é o `supabase_falso` do `conftest`.

Uso:
    pytest tests/test_importacao_clientes.py -v
"""

import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import importacao

TENANT = "empresa-exemplo"

LINHAS = [
    {"customer_id_externo": "c-001", "mrr": "1500.00", "billing_profile": "PJ",
     "days_since_last": "47", "features_used_30d": "1", "email": "fin@c001.com"},
    {"customer_id_externo": "c-002", "mrr": "300", "billing_profile": "CLT",
     "days_since_last": "2", "features_used_30d": "12", "email": ""},
    {"customer_id_externo": "c-003", "mrr": "220.5", "billing_profile": "freelancer",
     "days_since_last": "", "features_used_30d": "", "email": ""},
]


def _csv(linhas, colunas=None, sep=",", encoding="utf-8") -> bytes:
    df = pd.DataFrame(linhas)
    if colunas:
        df = df[colunas]
    return df.to_csv(index=False, sep=sep).encode(encoding)


def _xlsx(linhas) -> bytes:
    buf = io.BytesIO()
    pd.DataFrame(linhas).to_excel(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def cliente():
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c


def _enviar(cliente, headers, conteudo: bytes, nome="base.csv", mapeamento=None):
    dados = {"mapeamento": mapeamento} if mapeamento is not None else {}
    return cliente.post("/clientes/importar", headers=headers,
                        files={"arquivo": (nome, conteudo)}, data=dados)


# ── (a) CSV e XLSX importam igual ────────────────────────────────────────

class TestFormatos:
    def test_csv_importa(self, cliente, supabase_falso):
        r = _enviar(cliente, supabase_falso.bearer(TENANT), _csv(LINHAS))
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["importados"] == 3
        assert corpo["rejeitados"] == []
        assert corpo["colunas_nao_encontradas"] == []
        assert corpo["linhas_sem_dado_comportamental"] == 1     # c-003

        base = {c["customer_id_externo"]: c for c in ci.listar(TENANT)}
        assert base["c-001"]["mrr"] == 1500.0
        assert base["c-001"]["billing_profile"] == "PJ"
        assert base["c-001"]["days_since_last"] == 47.0
        assert base["c-001"]["features_used_30d"] == 1.0
        assert base["c-001"]["email"] == "fin@c001.com"
        assert base["c-002"]["email"] is None
        assert base["c-003"]["days_since_last"] is None
        assert base["c-003"]["features_used_30d"] is None
        assert base["c-003"]["importado_em"].endswith("+00:00")

    def test_xlsx_importa_igual_ao_csv(self, cliente, supabase_falso):
        r_csv = _enviar(cliente, supabase_falso.bearer("empresa-csv"), _csv(LINHAS))
        r_xlsx = _enviar(cliente, supabase_falso.bearer("empresa-xlsx"), _xlsx(LINHAS),
                         nome="base.xlsx")
        assert r_csv.status_code == 200 and r_xlsx.status_code == 200, r_xlsx.text
        assert r_csv.json() == r_xlsx.json()

        def _sem_carimbo(linhas):
            return [{k: v for k, v in l.items() if k not in ("importado_em", "tenant_id")}
                    for l in linhas]
        assert _sem_carimbo(ci.listar("empresa-csv")) == _sem_carimbo(ci.listar("empresa-xlsx"))

    def test_csv_brasileiro_ponto_e_virgula_latin1_e_numero_pt_br(self, cliente, supabase_falso):
        linhas = [
            {"customer_id_externo": "c-010", "mrr": "R$ 1.234,56",
             "billing_profile": "pj", "days_since_last": "3,0",
             "features_used_30d": "4", "email": "joão@empresa.com.br"},
        ]
        conteudo = _csv(linhas, sep=";", encoding="latin-1")
        r = _enviar(cliente, supabase_falso.bearer(TENANT), conteudo)
        assert r.status_code == 200, r.text
        assert r.json()["importados"] == 1
        (c,) = ci.listar(TENANT)
        assert c["mrr"] == 1234.56
        assert c["billing_profile"] == "PJ"          # normalizado para o vocabulário do bandit
        assert c["days_since_last"] == 3.0
        assert c["email"] == "joão@empresa.com.br"

    def test_csv_com_bom_do_excel(self, cliente, supabase_falso):
        conteudo = b"\xef\xbb\xbf" + _csv(LINHAS[:1])
        r = _enviar(cliente, supabase_falso.bearer(TENANT), conteudo)
        assert r.status_code == 200, r.text
        assert r.json()["importados"] == 1


# ── (b) reimportar atualiza ──────────────────────────────────────────────

class TestReimportacao:
    def test_mesmo_customer_id_atualiza_em_vez_de_duplicar(self, cliente, supabase_falso):
        h = supabase_falso.bearer(TENANT)
        assert _enviar(cliente, h, _csv(LINHAS)).json()["importados"] == 3

        atualizada = [dict(LINHAS[0], mrr="1800", days_since_last="60")]
        r = _enviar(cliente, h, _csv(atualizada))
        assert r.json()["importados"] == 1

        base = ci.listar(TENANT)
        assert len(base) == 3, "reimportar não pode duplicar"
        c1 = next(c for c in base if c["customer_id_externo"] == "c-001")
        assert c1["mrr"] == 1800.0
        assert c1["days_since_last"] == 60.0

    def test_duplicata_dentro_do_mesmo_arquivo_a_ultima_vence(self, cliente, supabase_falso):
        linhas = [dict(LINHAS[0], mrr="100"), dict(LINHAS[0], mrr="200")]
        r = _enviar(cliente, supabase_falso.bearer(TENANT), _csv(linhas))
        assert r.json()["importados"] == 1
        (c,) = ci.listar(TENANT)
        assert c["mrr"] == 200.0


# ── (c) linha inválida não derruba o lote ────────────────────────────────

class TestLinhasInvalidas:
    def test_linha_torta_e_reportada_e_o_resto_entra(self, cliente, supabase_falso):
        linhas = [
            LINHAS[0],
            dict(LINHAS[1], mrr="muito"),                        # linha 3 da planilha
            dict(LINHAS[2], billing_profile="MEI"),              # linha 4
            dict(LINHAS[0], customer_id_externo="", mrr="10"),   # linha 5
            dict(LINHAS[1], customer_id_externo="c-020", days_since_last="ontem"),  # 6
            dict(LINHAS[1], customer_id_externo="c-021", email="sem-arroba"),       # 7
            dict(LINHAS[1], customer_id_externo="c-022", mrr="-5"),                 # 8
            dict(LINHAS[1], customer_id_externo="c-023"),                           # 9 ok
        ]
        r = _enviar(cliente, supabase_falso.bearer(TENANT), _csv(linhas))
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["importados"] == 2
        assert {c["customer_id_externo"] for c in ci.listar(TENANT)} == {"c-001", "c-023"}

        por_linha = {rj["linha"]: rj["motivo"] for rj in corpo["rejeitados"]}
        assert set(por_linha) == {3, 4, 5, 6, 7, 8}
        assert "mrr" in por_linha[3] and "muito" in por_linha[3]
        assert "billing_profile" in por_linha[4] and "CLT, PJ, freelancer" in por_linha[4]
        assert "customer_id_externo" in por_linha[5]
        assert "days_since_last" in por_linha[6]
        assert "email" in por_linha[7]
        assert "mrr" in por_linha[8]

    def test_todas_rejeitadas_vem_com_mensagem(self, cliente, supabase_falso):
        r = _enviar(cliente, supabase_falso.bearer(TENANT), _csv([dict(LINHAS[0], mrr="")]))
        assert r.status_code == 200
        assert r.json()["importados"] == 0
        assert "nada importado" in r.json()["mensagem"]


# ── (d) mapeamento de colunas ────────────────────────────────────────────

class TestMapeamento:
    def test_coluna_com_outro_nome_via_mapeamento(self, cliente, supabase_falso):
        linhas = [{"ID do cliente": "c-001", "MRR (R$)": "1500", "Perfil": "PJ",
                   "Última atividade": "47", "Funcionalidades": "1"}]
        mapa = ('{"ID do cliente": "customer_id_externo", "MRR (R$)": "mrr", '
                '"Perfil": "billing_profile", "Última atividade": "days_since_last", '
                '"Funcionalidades": "features_used_30d"}')
        r = _enviar(cliente, supabase_falso.bearer(TENANT), _csv(linhas), mapeamento=mapa)
        assert r.status_code == 200, r.text
        assert r.json()["importados"] == 1
        assert r.json()["colunas_nao_encontradas"] == ["email"]
        (c,) = ci.listar(TENANT)
        assert c["days_since_last"] == 47.0

    def test_nome_exato_e_case_insensitive(self, cliente, supabase_falso):
        linhas = [{"CUSTOMER_ID_EXTERNO": "c-001", " Mrr ": "10", "Billing_Profile": "clt"}]
        r = _enviar(cliente, supabase_falso.bearer(TENANT), _csv(linhas))
        assert r.status_code == 200, r.text
        assert r.json()["importados"] == 1

    def test_mapeamento_torto_e_422(self, cliente, supabase_falso):
        h = supabase_falso.bearer(TENANT)
        r = _enviar(cliente, h, _csv(LINHAS), mapeamento="isto não é json")
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "mapeamento_invalido"
        r = _enviar(cliente, h, _csv(LINHAS), mapeamento='{"x": "campo_que_nao_existe"}')
        assert r.status_code == 422
        assert "campo_que_nao_existe" in r.json()["detail"]["detalhe"]


# ── (e) colunas ausentes ─────────────────────────────────────────────────

class TestColunasAusentes:
    def test_obrigatoria_ausente_nada_importa(self, cliente, supabase_falso):
        conteudo = _csv(LINHAS, colunas=["customer_id_externo", "billing_profile"])
        r = _enviar(cliente, supabase_falso.bearer(TENANT), conteudo)
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["importados"] == 0
        assert "mrr" in corpo["colunas_nao_encontradas"]
        assert "mrr" in corpo["mensagem"]
        assert "mapeamento" in corpo["mensagem"]
        assert ci.listar(TENANT) == []

    def test_opcional_ausente_so_e_reportada(self, cliente, supabase_falso):
        conteudo = _csv(LINHAS, colunas=["customer_id_externo", "mrr", "billing_profile"])
        r = _enviar(cliente, supabase_falso.bearer(TENANT), conteudo)
        corpo = r.json()
        assert corpo["importados"] == 3
        assert corpo["colunas_nao_encontradas"] == ["days_since_last", "features_used_30d", "email"]
        # (f) sem as duas colunas comportamentais, TODAS as linhas ficam sem dado
        assert corpo["linhas_sem_dado_comportamental"] == 3

    def test_uma_so_das_comportamentais_nao_conta_como_sem_dado(self, cliente, supabase_falso):
        conteudo = _csv(LINHAS[:2], colunas=["customer_id_externo", "mrr",
                                             "billing_profile", "days_since_last"])
        r = _enviar(cliente, supabase_falso.bearer(TENANT), conteudo)
        assert r.json()["linhas_sem_dado_comportamental"] == 0


# ── Arquivo inteiro recusado ─────────────────────────────────────────────

class TestArquivoRecusado:
    def test_extensao_nao_suportada_e_415(self, cliente, supabase_falso):
        r = _enviar(cliente, supabase_falso.bearer(TENANT), b"{}", nome="base.json")
        assert r.status_code == 415
        assert r.json()["detail"]["motivo"] == "extensao_nao_suportada"

    def test_arquivo_vazio_e_422(self, cliente, supabase_falso):
        r = _enviar(cliente, supabase_falso.bearer(TENANT), b"")
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "arquivo_vazio"

    def test_so_cabecalho_e_422(self, cliente, supabase_falso):
        r = _enviar(cliente, supabase_falso.bearer(TENANT),
                    b"customer_id_externo,mrr,billing_profile\n")
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "sem_linhas"

    def test_xlsx_corrompido_e_422(self, cliente, supabase_falso):
        r = _enviar(cliente, supabase_falso.bearer(TENANT), b"nao e um xlsx", nome="b.xlsx")
        assert r.status_code == 422
        assert r.json()["detail"]["motivo"] == "arquivo_ilegivel"

    def test_grande_demais_e_413(self, monkeypatch):
        monkeypatch.setattr(importacao, "TAMANHO_MAXIMO_BYTES", 10)
        with pytest.raises(importacao.ArquivoInvalido) as exc:
            importacao.ler_tabela("b.csv", b"x" * 11)
        assert exc.value.status == 413


# ── (g) identidade e isolamento ──────────────────────────────────────────

class TestIdentidade:
    def test_sem_jwt_e_401(self, cliente, supabase_falso):
        r = _enviar(cliente, {}, _csv(LINHAS))
        assert r.status_code == 401
        assert ci.listar(TENANT) == []

    def test_tenant_a_nao_le_a_base_de_tenant_b(self, cliente, supabase_falso):
        _enviar(cliente, supabase_falso.bearer("empresa-a"), _csv(LINHAS[:2]))
        _enviar(cliente, supabase_falso.bearer("empresa-b"), _csv(LINHAS[2:]))
        assert {c["customer_id_externo"] for c in ci.listar("empresa-a")} == {"c-001", "c-002"}
        assert {c["customer_id_externo"] for c in ci.listar("empresa-b")} == {"c-003"}
        assert ci.listar("empresa-c") == []
        assert ci.contar("empresa-a") == 2

    def test_mesmo_customer_id_em_dois_tenants_sao_linhas_distintas(self, cliente, supabase_falso):
        _enviar(cliente, supabase_falso.bearer("empresa-a"), _csv([dict(LINHAS[0], mrr="1")]))
        _enviar(cliente, supabase_falso.bearer("empresa-b"), _csv([dict(LINHAS[0], mrr="2")]))
        assert ci.listar("empresa-a")[0]["mrr"] == 1.0
        assert ci.listar("empresa-b")[0]["mrr"] == 2.0

    def test_tenant_vem_do_token_nao_do_corpo(self, cliente, supabase_falso):
        """Nem header `x-tenant-id` nem campo no form mudam o destino: quem
        diz a empresa é o JWT. Uma empresa logada não pode gravar na base de
        outra só por mandar um header."""
        h = {**supabase_falso.bearer("empresa-a"), "x-tenant-id": "empresa-b"}
        r = cliente.post("/clientes/importar", headers=h,
                         files={"arquivo": ("b.csv", _csv(LINHAS[:1]))},
                         data={"tenant_id": "empresa-b"})
        assert r.status_code == 200, r.text
        assert ci.contar("empresa-a") == 1
        assert ci.contar("empresa-b") == 0


# ── Destino da base ──────────────────────────────────────────────────────

class TestDestino:
    def test_sem_destino_configurado_e_500_claro(self, cliente, supabase_falso, monkeypatch):
        monkeypatch.delenv("CRAI_CLIENTES_DB", raising=False)
        monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
        r = _enviar(cliente, supabase_falso.bearer(TENANT), _csv(LINHAS))
        assert r.status_code == 500
        assert r.json()["detail"]["motivo"] == "base_nao_configurada"
        assert "SUPABASE_DB_URL" in r.json()["detail"]["detalhe"]

    def test_postgres_vence_o_sqlite_quando_os_dois_existem(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://x")
        monkeypatch.setenv("CRAI_CLIENTES_DB", "/tmp/x.db")
        assert ci._destino() == ("postgres", "postgresql://x")

    def test_upsert_traduz_placeholders_para_o_psycopg2(self):
        """O SQL é escrito uma vez com `?`; no Postgres vira `%s`. Se alguém
        colocar um `?` literal numa string do SQL, esta contagem denuncia."""
        assert ci._UPSERT.count("?") == 8
        assert ci._UPSERT.replace("?", "%s").count("%s") == 8
        assert "?" not in ci.SCHEMA_SQL


# ── Invariante: os webhooks não mudaram ──────────────────────────────────

class TestWebhooksIntocados:
    def test_segment_continua_sem_jwt_e_com_hmac(self, cliente, monkeypatch):
        """O caminho (1) — SDK — não passa pelo Supabase. Sem assinatura HMAC
        continua sendo 401 pelo motivo de sempre, e um JWT válido não substitui
        a assinatura."""
        monkeypatch.setenv("SEGMENT_WEBHOOK_SECRET", "segredo")
        monkeypatch.delenv("SUPABASE_PROJECT_URL", raising=False)
        r = cliente.post("/webhooks/segment", json={"event": "Session Started"})
        assert r.status_code == 401
        assert "Authorization" not in r.headers.get("WWW-Authenticate", "")

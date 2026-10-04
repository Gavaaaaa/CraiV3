"""tests/test_dev_token.py — Etapa 2, Bloco 4: o token de desenvolvimento.

O QUE ESTE ARQUIVO MEDE (Portão Final: "token de desenvolvimento impossível
fora de `development`"):
  - a rota `POST /dev/token` só é montada com `ENV=development` — no serviço de
    verdade (subprocesso) e na função de montagem; `demo` não conta;
  - montada, ela ainda responde 404 se a env deixar de ser `development`;
  - o token é da empresa fictícia `demo_dashboard`, com o papel pedido, e as
    rotas do self-service o aceitam SEM Supabase configurado;
  - fora de `development` o mesmo token é recusado com 401, antes de qualquer
    consulta de chave — inclusive se um JWKS publicar um `kid` com o prefixo;
  - a chave é desta subida: token de outra subida, ou assinado por outra chave
    com o mesmo `kid`, é recusado;
  - o token do Supabase continua valendo em `development`, e o token de
    desenvolvimento não carrega dado de pessoa.
"""

import os
import subprocess
import sys
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient

from crai.accounts import supabase_auth
from crai.api import app as app_module
from crai.api import dev_token
from tests.supabase_falso import ProjetoFalso, PROJECT_URL

APP = Path(__file__).resolve().parents[1]
FORA_DE_DESENVOLVIMENTO = ["production", "demo", "staging", "", "Development2"]


@pytest.fixture(autouse=True)
def chave_desta_subida_esquecida():
    """Nenhum teste herda a chave que outro gerou."""
    dev_token.esquecer_chave()
    yield
    dev_token.esquecer_chave()


@pytest.fixture
def desenvolvimento(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    # Sem Supabase: é o caso de quem só subiu o backend na própria máquina.
    monkeypatch.delenv("SUPABASE_PROJECT_URL", raising=False)
    supabase_auth.limpar_cache()


def _mini_app():
    aplicacao = FastAPI()
    montou = dev_token.montar(aplicacao)
    return TestClient(aplicacao), montou


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _rotas_do_servico(env: str) -> str:
    """Sobe o módulo do serviço num interpretador novo, com `ENV` dada, e diz
    se `/dev/token` está entre as rotas."""
    codigo = ("from crai.api.app import app; "
              "print('ROTA=' + str('/dev/token' in [getattr(r, 'path', '') for r in app.routes]))")
    ambiente = {**os.environ, "ENV": env, "CRAI_RELOGIO": "0"}
    r = subprocess.run([sys.executable, "-c", codigo], cwd=APP, env=ambiente,
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    return [l for l in r.stdout.splitlines() if l.startswith("ROTA=")][-1]


# ── A rota só existe em development ──────────────────────────────────────

class TestARotaSoExisteEmDesenvolvimento:

    def test_no_servico_de_verdade_a_rota_existe_so_com_env_development(self):
        assert _rotas_do_servico("development") == "ROTA=True"
        assert _rotas_do_servico("production") == "ROTA=False"

    @pytest.mark.parametrize("env", FORA_DE_DESENVOLVIMENTO)
    def test_fora_de_development_nao_monta_nem_gera_chave(self, monkeypatch, env):
        monkeypatch.setenv("ENV", env)
        cliente, montou = _mini_app()
        assert montou is False
        assert cliente.post("/dev/token").status_code == 404
        assert dev_token._par is None

    def test_sem_env_definida_vale_producao(self, monkeypatch):
        monkeypatch.delenv("ENV", raising=False)
        cliente, montou = _mini_app()
        assert montou is False and cliente.post("/dev/token").status_code == 404
        assert dev_token.preparar() is False and dev_token._par is None

    def test_montada_e_a_env_muda_depois_responde_404(self, monkeypatch, desenvolvimento):
        cliente, montou = _mini_app()
        assert montou is True and cliente.post("/dev/token").status_code == 200
        monkeypatch.setenv("ENV", "production")
        r = cliente.post("/dev/token")
        assert r.status_code == 404 and "token" not in r.text

    def test_emitir_fora_de_development_levanta(self, monkeypatch):
        monkeypatch.setenv("ENV", "production")
        with pytest.raises(RuntimeError):
            dev_token.emitir("owner")
        assert dev_token._par is None


# ── O que a rota emite ───────────────────────────────────────────────────

class TestOQueARotaEmite:

    def test_padrao_e_owner_da_empresa_ficticia(self, desenvolvimento):
        cliente, _ = _mini_app()
        r = cliente.post("/dev/token")
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["tenant_id"] == dev_token.TENANT_DEMO == "demo_dashboard"
        assert corpo["papel"] == "owner" and corpo["tipo"] == "Bearer"
        cabecalho = jwt.get_unverified_header(corpo["token"])
        assert cabecalho["alg"] == "ES256" and cabecalho["kid"].startswith("crai-dev-")

    @pytest.mark.parametrize("papel", ["owner", "admin", "membro"])
    def test_emite_o_papel_pedido(self, desenvolvimento, papel):
        cliente, _ = _mini_app()
        r = cliente.post("/dev/token", json={"papel": papel})
        assert r.status_code == 200, r.text
        claims = supabase_auth.validar_token(r.json()["token"])
        assert claims["papel"] == papel and claims["tenant_id"] == "demo_dashboard"

    @pytest.mark.parametrize("corpo", [{"papel": "dono"}, {"papel": None}, {"papel": ["owner"]},
                                       {"papel": "owner", "tenant_id": "empresa-a"},
                                       {"tenant_id": "empresa-a"}])
    def test_papel_fora_do_vocabulario_e_campo_estranho_sao_422(self, desenvolvimento, corpo):
        cliente, _ = _mini_app()
        r = cliente.post("/dev/token", json=corpo)
        assert r.status_code == 422, r.text
        assert "token" not in r.json()

    def test_o_token_nao_carrega_dado_de_pessoa(self, desenvolvimento):
        claims = supabase_auth.validar_token(dev_token.emitir("admin")["token"])
        assert set(claims) == {"iss", "sub", "aud", "role", "tenant_id", "papel", "iat", "exp"}
        assert claims["sub"] == "dev-admin" and "@" not in str(claims)
        assert claims["exp"] - claims["iat"] == dev_token.VALIDADE_SEGUNDOS


# ── O token nas rotas do self-service ────────────────────────────────────

class TestOTokenNasRotas:

    def test_em_development_as_rotas_aceitam_sem_supabase(self, desenvolvimento):
        token = dev_token.emitir("owner")["token"]
        with TestClient(app_module.app) as c:
            r = c.get("/configuracao", headers=_bearer(token))
            assert r.status_code == 200, r.text
            assert c.get("/ciclos", headers=_bearer(token)).json()["ciclos"] == []

    def test_o_papel_do_token_vale_nas_rotas(self, desenvolvimento):
        with TestClient(app_module.app) as c:
            membro = _bearer(dev_token.emitir("membro")["token"])
            assert c.get("/configuracao", headers=membro).status_code == 200
            r = c.put("/configuracao", json={"prazo_escolha_horas": 4}, headers=membro)
            assert r.status_code == 403 and r.json()["detail"]["motivo"] == "papel_insuficiente"
            admin = _bearer(dev_token.emitir("admin")["token"])
            assert c.put("/configuracao", json={"prazo_escolha_horas": 4},
                         headers=admin).status_code == 200

    @pytest.mark.parametrize("env", FORA_DE_DESENVOLVIMENTO)
    def test_fora_de_development_o_token_e_recusado(self, monkeypatch, desenvolvimento, env):
        token = dev_token.emitir("owner")["token"]
        monkeypatch.setenv("ENV", env)
        with TestClient(app_module.app) as c:
            for metodo, rota in (("GET", "/configuracao"), ("GET", "/ciclos"),
                                 ("PUT", "/configuracao")):
                r = c.request(metodo, rota, headers=_bearer(token), json={})
                assert r.status_code == 401, (rota, r.text)
                assert r.json()["detail"]["motivo"] == "token_de_desenvolvimento_recusado"

    def test_sem_env_o_token_e_recusado(self, monkeypatch, desenvolvimento):
        token = dev_token.emitir("owner")["token"]
        monkeypatch.delenv("ENV", raising=False)
        with pytest.raises(supabase_auth.TokenInvalido) as e:
            supabase_auth.validar_token(token)
        assert e.value.motivo == "token_de_desenvolvimento_recusado"

    def test_em_producao_com_supabase_o_token_e_401_e_o_do_supabase_segue_valendo(
            self, monkeypatch, desenvolvimento, supabase_falso):
        token = dev_token.emitir("owner")["token"]
        monkeypatch.setenv("ENV", "production")
        with TestClient(app_module.app) as c:
            r = c.get("/configuracao", headers=_bearer(token))
            assert r.status_code == 401
            assert r.json()["detail"]["motivo"] == "token_de_desenvolvimento_recusado"
            # A recusa veio antes de qualquer consulta ao JWKS.
            assert supabase_falso.buscas == 0
            assert c.get("/configuracao",
                         headers=supabase_falso.bearer("empresa-a")).status_code == 200

    def test_jwks_publicando_um_kid_com_o_prefixo_nao_abre_a_porta(self, monkeypatch):
        """Em produção, um token com `kid` de desenvolvimento é recusado mesmo
        que o JWKS do projeto publique uma chave com esse `kid`."""
        projeto = ProjetoFalso(kid="crai-dev-publicado")
        monkeypatch.setenv("ENV", "production")
        monkeypatch.setenv("SUPABASE_PROJECT_URL", PROJECT_URL)
        monkeypatch.setattr(supabase_auth, "_baixar_jwks", projeto.baixar_jwks)
        supabase_auth.limpar_cache()
        with pytest.raises(supabase_auth.TokenInvalido) as e:
            supabase_auth.validar_token(projeto.token("empresa-a", papel="owner"))
        assert e.value.motivo == "token_de_desenvolvimento_recusado"
        assert projeto.buscas == 0
        supabase_auth.limpar_cache()

    def test_em_development_o_token_do_supabase_continua_valendo(self, monkeypatch,
                                                                 supabase_falso):
        monkeypatch.setenv("ENV", "development")
        with TestClient(app_module.app) as c:
            assert c.get("/configuracao",
                         headers=supabase_falso.bearer("empresa-a")).status_code == 200


# ── A chave é desta subida ───────────────────────────────────────────────

class TestAChaveEDestaSubida:

    def test_token_de_outra_subida_e_recusado(self, desenvolvimento):
        antigo = dev_token.emitir("owner")["token"]
        dev_token.esquecer_chave()                       # o serviço reiniciou
        novo = dev_token.emitir("owner")["token"]
        assert supabase_auth.validar_token(novo)["tenant_id"] == "demo_dashboard"
        with pytest.raises(supabase_auth.TokenInvalido) as e:
            supabase_auth.validar_token(antigo)
        assert e.value.motivo == "chave_de_desenvolvimento_desconhecida"

    def test_token_assinado_por_outra_chave_com_o_mesmo_kid_e_recusado(self, desenvolvimento):
        legitimo = dev_token.emitir("owner")["token"]
        kid = jwt.get_unverified_header(legitimo)["kid"]
        claims = jwt.decode(legitimo, options={"verify_signature": False})
        forjado = jwt.encode({**claims, "tenant_id": "empresa-a"},
                             ec.generate_private_key(ec.SECP256R1()),
                             algorithm="ES256", headers={"kid": kid})
        with pytest.raises(supabase_auth.TokenInvalido) as e:
            supabase_auth.validar_token(forjado)
        assert e.value.motivo == "assinatura_invalida"

    def test_token_expirado_e_recusado(self, monkeypatch, desenvolvimento):
        monkeypatch.setattr(dev_token, "VALIDADE_SEGUNDOS", -60)
        with pytest.raises(supabase_auth.TokenInvalido) as e:
            supabase_auth.validar_token(dev_token.emitir("owner")["token"])
        assert e.value.motivo == "token_expirado"

    def test_hs256_com_kid_de_desenvolvimento_e_recusado(self, desenvolvimento):
        kid = jwt.get_unverified_header(dev_token.emitir("owner")["token"])["kid"]
        forjado = jwt.encode({"tenant_id": "demo_dashboard", "aud": "authenticated",
                              "exp": 4102444800}, "segredo-qualquer-com-32-bytes-ou-mais!",
                             algorithm="HS256", headers={"kid": kid})
        with pytest.raises(supabase_auth.TokenInvalido) as e:
            supabase_auth.validar_token(forjado)
        assert e.value.motivo == "algoritmo_recusado"

    def test_a_chave_nao_vem_de_env_nem_vai_para_o_disco(self):
        fonte = Path(dev_token.__file__).read_text(encoding="utf-8")
        codigo = fonte.split('"""', 2)[2]                # sem a docstring do módulo
        assert "open(" not in codigo and "write" not in codigo
        assert codigo.count("os.getenv(") == 1           # só a leitura de ENV

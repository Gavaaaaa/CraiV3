"""tests/test_supabase_auth.py — a CRAI só confia no token que o Supabase assinou.

O que o Sprint 1 do self-service promete, e o que aqui é verificado:

  (a) token assinado com a chave do projeto + claim `tenant_id` → o endpoint
      recebe exatamente esse tenant;
  (b) sem `Authorization`, expirado, assinado com OUTRA chave, ou válido mas
      sem `tenant_id` → 401, com motivo legível;
  (c) `HS256` é recusado antes de qualquer verificação (confusão de algoritmo);
  (d) sem `SUPABASE_PROJECT_URL` → 500 claro, nunca "deixa passar";
  (e) JWKS fica em cache, e um `kid` novo força UMA renovação (rotação).

NADA AQUI BATE NO SUPABASE. O par de chaves ES256 é gerado na hora com
`cryptography`, e `_baixar_jwks` é substituído por uma função que devolve o
JWKS local — o mesmo formato que o endpoint real serve.

Uso:
    pytest tests/test_supabase_auth.py -v
"""

import time

import jwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from crai.accounts import supabase_auth
from crai.accounts.auth import get_tenant_id
from crai.accounts.supabase_auth import (
    ConfiguracaoAusente,
    TokenInvalido,
    tenant_das_claims,
    validar_token,
)

from tests.supabase_falso import PROJECT_URL, par_de_chaves as _par_de_chaves, token as _token


@pytest.fixture
def chaves(monkeypatch):
    """Um projeto Supabase de mentira: env configurada e JWKS servido localmente.

    Devolve um dict mutável — o teste de rotação troca as chaves publicadas e
    conta quantas vezes o "endpoint" foi consultado.
    """
    privada, jwk = _par_de_chaves("chave-1")
    projeto = {"privada": privada, "jwks": {"keys": [jwk]}, "buscas": 0}

    def _jwks_local(url):
        assert url == f"{PROJECT_URL}/auth/v1/.well-known/jwks.json"
        projeto["buscas"] += 1
        return projeto["jwks"]

    monkeypatch.setenv("SUPABASE_PROJECT_URL", PROJECT_URL)
    monkeypatch.setattr(supabase_auth, "_baixar_jwks", _jwks_local)
    supabase_auth.limpar_cache()
    yield projeto
    supabase_auth.limpar_cache()


@pytest.fixture
def cliente():
    """Uma app mínima com um endpoint protegido: o que importa é a dependency."""
    app = FastAPI()

    @app.get("/quem-sou")
    async def quem_sou(tenant_id: str = Depends(get_tenant_id)):
        return {"tenant_id": tenant_id}

    return TestClient(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── (a) o caminho feliz ──────────────────────────────────────────────────

class TestTokenValido:
    def test_get_tenant_id_resolve_o_tenant_da_claim(self, chaves, cliente):
        token = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa-exemplo"})
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 200, r.text
        assert r.json() == {"tenant_id": "empresa-exemplo"}

    def test_validar_token_devolve_as_claims(self, chaves):
        token = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa-exemplo"})
        claims = validar_token(token)
        assert claims["tenant_id"] == "empresa-exemplo"
        assert claims["sub"] == "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"
        assert claims["role"] == "authenticated"

    def test_bearer_e_case_insensitive_e_tolera_espacos(self, chaves, cliente):
        token = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa-exemplo"})
        r = cliente.get("/quem-sou", headers={"Authorization": f"  bearer   {token} "})
        assert r.status_code == 200
        assert r.json()["tenant_id"] == "empresa-exemplo"


# ── (b) os 401 ───────────────────────────────────────────────────────────

class TestRecusas401:
    def test_sem_header_authorization(self, chaves, cliente):
        r = cliente.get("/quem-sou")
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "sem_authorization"
        assert r.headers["WWW-Authenticate"] == "Bearer"

    def test_header_sem_bearer(self, chaves, cliente):
        token = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa-exemplo"})
        r = cliente.get("/quem-sou", headers={"Authorization": token})
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "authorization_malformado"

    def test_token_expirado(self, chaves, cliente):
        token = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa-exemplo"},
                       exp=int(time.time()) - 60)
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "token_expirado"

    def test_token_sem_exp_e_recusado(self, chaves):
        agora = int(time.time())
        corpo = {"aud": "authenticated", "iat": agora, "tenant_id": "empresa-exemplo"}
        token = jwt.encode(corpo, chaves["privada"], algorithm="ES256",
                           headers={"kid": "chave-1"})
        with pytest.raises(TokenInvalido) as exc:
            validar_token(token)
        assert exc.value.motivo == "token_invalido"
        assert "exp" in exc.value.mensagem

    def test_assinado_com_chave_errada(self, chaves, cliente):
        # Mesmo `kid` publicado, outra chave privada: a assinatura não confere.
        intrusa, _ = _par_de_chaves("chave-1")
        token = _token(intrusa, "chave-1", {"tenant_id": "empresa-exemplo"})
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "assinatura_invalida"

    def test_kid_que_o_projeto_nao_publica(self, chaves, cliente):
        outra, _ = _par_de_chaves("chave-de-outro-projeto")
        token = _token(outra, "chave-de-outro-projeto", {"tenant_id": "empresa-exemplo"})
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "chave_desconhecida"

    def test_valido_mas_sem_tenant_id(self, chaves, cliente):
        token = _token(chaves["privada"], "chave-1", {})
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 401
        detail = r.json()["detail"]
        assert detail["motivo"] == "sem_tenant"
        # A mensagem precisa apontar para a causa e para o conserto.
        assert "tenant_id" in detail["detalhe"]
        assert "Custom Access Token Hook" in detail["detalhe"]
        assert "README" in detail["detalhe"]

    def test_tenant_id_vazio_ou_nao_texto(self, chaves):
        with pytest.raises(TokenInvalido) as exc:
            tenant_das_claims({"tenant_id": "   "})
        assert exc.value.motivo == "tenant_invalido"
        with pytest.raises(TokenInvalido) as exc:
            tenant_das_claims({"tenant_id": 42})
        assert exc.value.motivo == "tenant_invalido"

    def test_default_tenant_e_reservado(self, chaves, cliente):
        token = _token(chaves["privada"], "chave-1", {"tenant_id": "default_tenant"})
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "tenant_reservado"

    def test_tenant_fora_do_formato(self, chaves, cliente):
        token = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa exemplo/ltda"})
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "tenant_com_forma_invalida"

    def test_audiencia_diferente(self, chaves, cliente):
        token = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa-exemplo"},
                       aud="outro-servico")
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "audiencia_invalida"

    def test_lixo_no_lugar_do_token(self, chaves, cliente):
        r = cliente.get("/quem-sou", headers=_bearer("isto.nao.e.jwt"))
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "token_malformado"


# ── (c) confusão de algoritmo ────────────────────────────────────────────

class TestAlgoritmo:
    def test_hs256_e_recusado_antes_de_verificar(self, chaves, cliente):
        """Assinar com HMAC usando a chave PÚBLICA como segredo é o ataque
        clássico contra validadores que aceitam qualquer `alg`. Aqui o `alg`
        é recusado no header, antes de a chave ser sequer consultada."""
        corpo = {"aud": "authenticated", "exp": int(time.time()) + 3600,
                 "tenant_id": "empresa-exemplo"}
        token = jwt.encode(corpo, "qualquer-segredo", algorithm="HS256",
                           headers={"kid": "chave-1"})
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "algoritmo_recusado"
        assert chaves["buscas"] == 0, "o JWKS nem deveria ter sido consultado"

    def test_token_sem_kid(self, chaves, cliente):
        corpo = {"aud": "authenticated", "exp": int(time.time()) + 3600,
                 "tenant_id": "empresa-exemplo"}
        token = jwt.encode(corpo, chaves["privada"], algorithm="ES256")
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "sem_kid"


# ── (d) instalação sem configuração falha ALTO ───────────────────────────

class TestConfiguracao:
    def test_sem_project_url_e_500_nunca_deixa_passar(self, monkeypatch, cliente):
        monkeypatch.delenv("SUPABASE_PROJECT_URL", raising=False)
        supabase_auth.limpar_cache()
        privada, _ = _par_de_chaves("chave-1")
        token = _token(privada, "chave-1", {"tenant_id": "empresa-exemplo"})
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 500
        assert r.json()["detail"]["motivo"] == "supabase_nao_configurado"
        assert "SUPABASE_PROJECT_URL" in r.json()["detail"]["detalhe"]

    def test_project_url_vazia_conta_como_ausente(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_PROJECT_URL", "   ")
        with pytest.raises(ConfiguracaoAusente):
            supabase_auth.project_url()

    def test_jwks_url_tolera_barra_no_fim(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_PROJECT_URL", PROJECT_URL + "/")
        assert supabase_auth.jwks_url() == f"{PROJECT_URL}/auth/v1/.well-known/jwks.json"

    def test_jwks_fora_do_ar_e_500_nao_401(self, monkeypatch, cliente):
        monkeypatch.setenv("SUPABASE_PROJECT_URL", PROJECT_URL)
        supabase_auth.limpar_cache()

        def _caiu(url):
            raise OSError("connection refused")

        monkeypatch.setattr(supabase_auth, "_baixar_jwks", _caiu)
        privada, _ = _par_de_chaves("chave-1")
        token = _token(privada, "chave-1", {"tenant_id": "empresa-exemplo"})
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 500
        assert r.json()["detail"]["motivo"] == "jwks_indisponivel"


# ── (e) cache e rotação de chaves ────────────────────────────────────────

class TestCacheDoJwks:
    def test_varios_tokens_uma_busca(self, chaves, cliente):
        for _ in range(5):
            token = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa-exemplo"})
            assert cliente.get("/quem-sou", headers=_bearer(token)).status_code == 200
        assert chaves["buscas"] == 1

    def test_cache_expira_em_no_maximo_dez_minutos(self, chaves, cliente, monkeypatch):
        token = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa-exemplo"})
        assert cliente.get("/quem-sou", headers=_bearer(token)).status_code == 200
        assert chaves["buscas"] == 1

        assert supabase_auth.JWKS_TTL_SEGUNDOS <= 600
        agora = time.monotonic()
        monkeypatch.setattr(supabase_auth.time, "monotonic",
                            lambda: agora + supabase_auth.JWKS_TTL_SEGUNDOS + 1)
        assert cliente.get("/quem-sou", headers=_bearer(token)).status_code == 200
        assert chaves["buscas"] == 2

    def test_rotacao_kid_novo_renova_o_cache_uma_vez(self, chaves, cliente):
        token1 = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa-exemplo"})
        assert cliente.get("/quem-sou", headers=_bearer(token1)).status_code == 200
        assert chaves["buscas"] == 1

        # O Supabase rotacionou: publica a chave-2 (e mantém a 1 por enquanto).
        privada2, jwk2 = _par_de_chaves("chave-2")
        chaves["jwks"] = {"keys": chaves["jwks"]["keys"] + [jwk2]}
        token2 = _token(privada2, "chave-2", {"tenant_id": "empresa-exemplo"})
        r = cliente.get("/quem-sou", headers=_bearer(token2))
        assert r.status_code == 200, r.text
        assert chaves["buscas"] == 2

        # Depois de renovado, os dois `kid` servem sem nova busca.
        assert cliente.get("/quem-sou", headers=_bearer(token1)).status_code == 200
        assert cliente.get("/quem-sou", headers=_bearer(token2)).status_code == 200
        assert chaves["buscas"] == 2

    def test_kid_desconhecido_renova_so_uma_vez_e_recusa(self, chaves, cliente):
        outra, _ = _par_de_chaves("chave-fantasma")
        token = _token(outra, "chave-fantasma", {"tenant_id": "empresa-exemplo"})
        r = cliente.get("/quem-sou", headers=_bearer(token))
        assert r.status_code == 401
        assert r.json()["detail"]["motivo"] == "chave_desconhecida"
        assert chaves["buscas"] == 2, "uma busca inicial + UMA renovação, não um loop"

    def test_chave_hs256_no_jwks_e_ignorada(self, chaves, cliente):
        chaves["jwks"] = {"keys": chaves["jwks"]["keys"]
                          + [{"kty": "oct", "kid": "hmac", "alg": "HS256", "k": "c2VncmVkbw"}]}
        token = _token(chaves["privada"], "chave-1", {"tenant_id": "empresa-exemplo"})
        assert cliente.get("/quem-sou", headers=_bearer(token)).status_code == 200
        assert "hmac" not in supabase_auth._chaves()


# ── Invariante: os webhooks não passam por aqui ──────────────────────────

class TestWebhooksIntocados:
    def test_so_as_rotas_do_self_service_dependem_de_get_tenant_id(self):
        """Os webhooks (Segment, Pix, Stripe, retention-outcome) e os
        `/simulate/*` continuam com `_tenant_da_requisicao`; a dependency só
        entra nas rotas NOVAS do self-service. Uma rota antiga passando a
        exigir JWT quebraria toda integração já feita — e é isso que esta
        catraca impede."""
        from crai.api import app as app_module

        SELF_SERVICE = ("/clientes/", "/insights")
        for rota in app_module.app.routes:
            deps = getattr(getattr(rota, "dependant", None), "dependencies", [])
            nomes = {getattr(d.call, "__name__", "") for d in deps}
            usa = bool(nomes & {"get_tenant_id", "get_conta"})
            e_self_service = rota.path.startswith(SELF_SERVICE)
            assert usa == e_self_service, (
                f"{rota.path}: usa get_tenant_id={usa}, self-service={e_self_service}")

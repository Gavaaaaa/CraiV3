"""tests/test_art20_explicacao.py — o direito à explicação (LGPD Art. 20), Bloco G.

Os oito itens de G.6, contra o app real:

  1. decisão gravada aparece em `GET /titular/explicacao/{sujeito_id}`, com
     explicação em pt-BR não vazia;
  2. tenant A pede explicação de sujeito de B → 404, corpo IDÊNTICO ao de
     sujeito inexistente (comparados os corpos, não só os status);
  3. sujeito sem decisão → 404;
  4. a resposta não contém e-mail, telefone, nome, texto de mensagem nem
     motivo de cancelamento — conferido no JSON inteiro da resposta;
  5. a resposta não contém coeficiente, hiperparâmetro nem estado do bandit;
  6. decisão de regra traz `contribuicoes: null` e a explicação nomeia a regra;
  7. cadeia quebrada aparece como tal na resposta;
  8. Decreto 11.034/2022: nenhuma decisão gravada adia ou condiciona um
     cancelamento (e o verificador pega uma que adiasse).

Mais: sem JWT é 401; paginação; a explicação sai de UMA função dirigida por
dicionários de rótulos, não de uma frase por tipo escrita à mão.

Uso:
    pytest tests/test_art20_explicacao.py -v
"""

import inspect
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as wf
from crai.api import app as app_module
from crai.api import titular as titular_api
from crai.churn_voluntary import disparo_lote
from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import voluntary_agent as va
from crai.config import CANAIS_HUMANOS

A, B = "empresa-a", "empresa-b"
SUJEITO = "user:cliente-final-1"

EMAIL = "fulano.da.silva@cliente-final.com.br"
TELEFONE = "11912345678"
NOME = "Fulano da Silva Sauro"
TEXTO_MENSAGEM = "Oi Fulano, vimos que você quase cancelou — que tal 20% de desconto?"
MOTIVO = "mudou de fornecedor porque o suporte demorou"
DADO_CRU = (EMAIL, TELEFONE, NOME, "Fulano", TEXTO_MENSAGEM, MOTIVO)

# Segredo comercial (Art. 20 §1º): nada disto pode aparecer como chave nem como texto.
CHAVES_DE_SEGREDO = {"alpha", "beta", "coef", "coef_", "coeficientes", "pesos", "weights",
                     "hiperparametros", "hyperparameters", "n_estimators", "max_depth",
                     "learning_rate", "feature_importances_", "booster", "intercept_"}
TEXTOS_DE_SEGREDO = ("n_estimators", "max_depth", "learning_rate", "\"alpha\"", "\"beta\"")


@pytest.fixture(autouse=True)
def _isolamento(monkeypatch, tmp_path):
    monkeypatch.setattr(ob, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(ob, "STATE_PATH", tmp_path / "bandit_state.json")
    monkeypatch.setattr(va, "_channel_history", {})
    monkeypatch.setenv("ENV", "development")

    async def api_fora(**kwargs):
        raise RuntimeError("Claude fora do ar (teste)")

    monkeypatch.setattr(va.claude.messages, "create", api_fora)
    from crai.dunning import dunning_engine
    monkeypatch.setattr(dunning_engine.claude.messages, "create", api_fora)


@pytest.fixture
def cliente():
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def api(cliente, supabase_falso):
    def _get(tenant, sujeito, **params):
        return cliente.get(f"/titular/explicacao/{sujeito}", params=params,
                           headers=supabase_falso.bearer(tenant))
    return _get


def _estado_sujo(tenant=A, user=SUJEITO):
    return {
        "tenant_id": tenant, "user_id": user, "event": "Cancellation Page Viewed",
        "props": {"days_since_last": 23, "features_used_30d": 2, "mrr": 1800,
                  "billing_profile": "PJ", "on_site_now": False,
                  "email": EMAIL, "phone": TELEFONE, "name": NOME, "nome": NOME,
                  "motivo_cancelamento": MOTIVO},
        "message": TEXTO_MENSAGEM, "motivo_cancelamento": MOTIVO, "email": EMAIL,
        "offer_sent": True, "accepted": None, "retained": False,
    }


async def _ciclo_voluntario(tenant=A, user=SUJEITO):
    s = _estado_sujo(tenant, user)
    s = await va.assess_risk(s)
    s = await va.choose_offer(s)
    s = await va.choose_channel(s)
    s = {**s, "message": TEXTO_MENSAGEM}
    return await va.update_crm(s)


async def _ciclo_involuntario(tenant=A, customer="cus_final_1", causa="card_declined"):
    s = {
        "tenant_id": tenant, "customer_id": customer, "amount": 250.0, "invoice_id": "in_1",
        "payment_method": "card", "retry_count": 0, "email": EMAIL, "motivo_cancelamento": MOTIVO,
        "payment_event": {"data": {"object": {
            "amount": 25000, "failure_code": causa, "customer": customer, "attempt_count": 1,
            "payment_method_details": {"brand": "visa"},
            "billing_details": {"name": NOME, "email": EMAIL, "phone": TELEFONE}}}},
    }
    s = await wf.diagnose_failure(s)
    s = await wf.decide_recovery(s)
    s = await wf.trigger_dunning(s)
    return await wf.update_roi_dashboard(s)


def _chaves(valor, acumulado=None):
    acumulado = set() if acumulado is None else acumulado
    if isinstance(valor, dict):
        for k, v in valor.items():
            acumulado.add(str(k).lower())
            _chaves(v, acumulado)
    elif isinstance(valor, list):
        for v in valor:
            _chaves(v, acumulado)
    return acumulado


# ── 1. A decisão gravada aparece, explicada ──────────────────────────────

class TestRota:
    @pytest.mark.asyncio
    async def test_decisoes_do_ciclo_aparecem_com_explicacao_em_pt_br(self, api):
        await _ciclo_voluntario()
        r = api(A, SUJEITO)
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["sujeito_id"] == SUJEITO
        tipos = [d["tipo_decisao"] for d in corpo["decisoes"]]
        assert tipos == ["canal", "oferta", "risco"]           # mais recentes primeiro
        for d in corpo["decisoes"]:
            assert d["explicacao"] and len(d["explicacao"]) > 40
            assert "o sistema" in d["explicacao"]              # pt-BR, não jargão
            assert d["decidido_em"].endswith("+00:00")
            assert set(d) == {"id", "decidido_em", "dominio", "tipo_decisao", "modelo", "modelo_versao",
                              "explicacao", "entradas", "saida", "contribuicoes", "hash_linha",
                              "hash_anterior"}
        risco = corpo["decisoes"][-1]
        assert "23 dias sem acesso ao produto" in risco["explicacao"]
        assert "R$ 1.800,00" in risco["explicacao"]
        assert corpo["cadeia"]["integra"] is True and corpo["cadeia"]["linhas"] == 3
        assert "Art. 20" in corpo["base_legal"]["dispositivo"]
        assert corpo["paginacao"]["tem_mais"] is False

    def test_sem_jwt_e_401(self, cliente, supabase_falso):
        assert cliente.get(f"/titular/explicacao/{SUJEITO}").status_code == 401

    @pytest.mark.asyncio
    async def test_paginacao_mais_recentes_primeiro(self, api):
        await _ciclo_voluntario()
        await _ciclo_voluntario()
        p1 = api(A, SUJEITO, limite=4).json()
        assert len(p1["decisoes"]) == 4 and p1["paginacao"]["tem_mais"] is True
        p2 = api(A, SUJEITO, limite=4, antes_de=p1["paginacao"]["proximo_antes_de"]).json()
        assert len(p2["decisoes"]) == 2 and p2["paginacao"]["tem_mais"] is False
        ids = [d["id"] for d in p1["decisoes"] + p2["decisoes"]]
        assert ids == sorted(ids, reverse=True) and len(set(ids)) == 6
        # página além do fim de um sujeito que existe: 200 vazia, não 404
        alem = api(A, SUJEITO, antes_de=ids[-1])
        assert alem.status_code == 200 and alem.json()["decisoes"] == []

    def test_limite_invalido_e_422(self, api):
        assert api(A, SUJEITO, limite=0).json()["detail"]["motivo"] == "limite_invalido"
        assert api(A, SUJEITO, limite=201).json()["detail"]["motivo"] == "limite_invalido"


# ── 2 e 3. Os 404 ────────────────────────────────────────────────────────

class TestQuatrocentosEQuatro:
    @pytest.mark.asyncio
    async def test_sujeito_de_outro_tenant_e_404_com_corpo_identico_ao_de_inexistente(self, api):
        await _ciclo_voluntario(tenant=B, user="user:so-de-b")
        assert api(B, "user:so-de-b").status_code == 200        # B enxerga o seu

        de_outro = api(A, "user:so-de-b")
        inexistente = api(A, "user:nunca-existiu")
        assert de_outro.status_code == inexistente.status_code == 404
        assert de_outro.json() == inexistente.json()           # corpo, não só status
        assert de_outro.json()["detail"]["motivo"] == "sujeito_sem_decisao"
        assert de_outro.headers.get("content-length") == inexistente.headers.get("content-length")

    def test_sujeito_sem_decisao_e_404(self, api):
        r = api(A, "user:sem-nada")
        assert r.status_code == 404
        assert r.json()["detail"]["motivo"] == "sujeito_sem_decisao"


# ── 4 e 5. Nem dado cru, nem segredo comercial ───────────────────────────

class TestNadaVaza:
    @pytest.mark.asyncio
    async def test_resposta_nao_contem_email_texto_nem_motivo(self, api):
        await _ciclo_voluntario()
        await _ciclo_involuntario()
        for sujeito in (SUJEITO, "cus_final_1"):
            texto = api(A, sujeito).text
            for cru in DADO_CRU:
                assert cru not in texto, f"{cru!r} vazou na resposta de {sujeito}"
            assert "http" not in json.dumps(api(A, sujeito).json()["decisoes"])

    @pytest.mark.asyncio
    async def test_resposta_nao_contem_coeficiente_hiperparametro_nem_estado_do_bandit(self, api):
        await _ciclo_voluntario()
        await _ciclo_involuntario()
        for sujeito in (SUJEITO, "cus_final_1"):
            corpo = api(A, sujeito).json()
            assert not (_chaves(corpo["decisoes"]) & CHAVES_DE_SEGREDO), _chaves(corpo["decisoes"]) & CHAVES_DE_SEGREDO
            texto = json.dumps(corpo["decisoes"], ensure_ascii=False)
            for t in TEXTOS_DE_SEGREDO:
                assert t not in texto
        oferta = next(d for d in api(A, SUJEITO).json()["decisoes"] if d["tipo_decisao"] == "oferta")
        assert all(set(c) == {"offer", "p_estimado"} for c in oferta["saida"]["ofertas_consideradas"])
        # e o que o §1º manda entregar, entra: features com valor e direção.
        risco = next(d for d in api(A, "cus_final_1").json()["decisoes"] if d["tipo_decisao"] == "risco")
        if risco["modelo"] == "failure_classifier":
            assert len(risco["contribuicoes"]) == 5
            assert all({"feature", "direcao"} <= set(c) for c in risco["contribuicoes"])

    def test_a_rota_tira_segredo_ate_de_linha_antiga(self, api):
        """Defesa em profundidade: uma linha gravada com alpha/beta (antes do
        filtro existir) não sai pela rota."""
        d = rl.decisao(A, SUJEITO, "voluntario", "oferta", "offer_bandit", {"profile": "PJ"},
                       {"offer_type": "desconto_10"})
        d["saida"]["alpha"], d["saida"]["beta"] = 10.0, 12.0     # burlando o filtro da montagem
        d["entradas"]["email"] = EMAIL
        rl.registrar_decisao(d)
        (dec,) = api(A, SUJEITO).json()["decisoes"]
        assert "alpha" not in dec["saida"] and "beta" not in dec["saida"]
        assert "email" not in dec["entradas"]


# ── 6. Decisão de regra ──────────────────────────────────────────────────

class TestDecisaoDeRegra:
    @pytest.mark.asyncio
    async def test_regra_traz_contribuicoes_null_e_a_explicacao_nomeia_a_regra(self, api):
        await _ciclo_voluntario()
        corpo = api(A, SUJEITO).json()
        canal = next(d for d in corpo["decisoes"] if d["tipo_decisao"] == "canal")
        assert canal["modelo"] == "regra" and canal["contribuicoes"] is None
        assert canal["saida"]["regra"] in canal["explicacao"]
        assert "sem modelo treinado" in canal["explicacao"]
        risco = next(d for d in corpo["decisoes"] if d["tipo_decisao"] == "risco")
        if risco["modelo"] == "regra":
            assert risco["contribuicoes"] is None
            assert "risk_scorer.risco_por_features" in risco["explicacao"]

    @pytest.mark.asyncio
    async def test_retentativa_do_involuntario_e_regra_nomeada(self, api):
        await _ciclo_involuntario()
        ret = next(d for d in api(A, "cus_final_1").json()["decisoes"] if d["tipo_decisao"] == "retentativa")
        assert ret["modelo"] == "regra" and ret["contribuicoes"] is None
        assert "decide_recovery" in ret["explicacao"]


# ── G.2: uma função, dicionários de rótulos ──────────────────────────────

class TestExplicacaoPorDicionario:
    def test_ha_uma_funcao_so_e_ela_cobre_os_quatro_tipos(self):
        fonte = inspect.getsource(rl.frase_da_decisao)
        # A função não tem um ramo por tipo: consulta os dicionários.
        assert "ROTULOS_DE_TIPO" in fonte and "ROTULOS_DE_FEATURE" in fonte and "ROTULOS_DE_SAIDA" in fonte
        for tipo in rl.TIPOS_DECISAO:
            assert tipo in rl.ROTULOS_DE_TIPO
            assert f'== "{tipo}"' not in fonte and f"== '{tipo}'" not in fonte
        # Toda feature que os pontos de decisão gravam tem rótulo.
        for feature in ("days_since_last", "features_used_30d", "mrr", "billing_profile", "event",
                        "failure_cause", "recovery_score", "tentativas_usadas", "telefone_disponivel"):
            assert feature in rl.ROTULOS_DE_FEATURE

    def test_feature_nova_sem_rotulo_nao_quebra_e_aparece_como_chave_valor(self):
        f = rl.frase_da_decisao("2026-09-21T10:00:00+00:00", "voluntario", "risco", "regra", None,
                                {"feature_inventada": 7}, {"risk_score": 0.5, "regra": "r"}, None)
        assert "feature_inventada = 7" in f and "21/09/2026" in f


# ── 7. Cadeia quebrada aparece na resposta ───────────────────────────────

class TestCadeiaNaResposta:
    @pytest.mark.asyncio
    async def test_cadeia_quebrada_aparece_como_tal(self, api):
        await _ciclo_voluntario()
        assert api(A, SUJEITO).json()["cadeia"]["integra"] is True
        con = sqlite3.connect(rl.caminho_do_banco())
        segundo = con.execute("SELECT id FROM decisoes_automatizadas ORDER BY id").fetchall()[1][0]
        con.execute("UPDATE decisoes_automatizadas SET saida = '{}' WHERE id = ?", (segundo,))
        con.commit()
        con.close()
        cadeia = api(A, SUJEITO).json()["cadeia"]
        assert cadeia["integra"] is False
        assert cadeia["hash_proprio_invalido_em_id"] == segundo
        assert cadeia["quebra_em_id"] == segundo + 1

    @pytest.mark.asyncio
    async def test_cadeia_e_a_do_tenant_do_token(self, api):
        await _ciclo_voluntario(tenant=A)
        await _ciclo_voluntario(tenant=B, user="user:de-b")
        assert api(A, SUJEITO).json()["cadeia"]["tenant_id"] == A
        assert api(B, "user:de-b").json()["cadeia"]["tenant_id"] == B


# ── 8. Decreto 11.034/2022 ───────────────────────────────────────────────

class TestDecreto11034:
    @pytest.mark.asyncio
    async def test_decreto_11034_2022_nenhuma_decisao_gravada_adia_ou_condiciona_cancelamento(self):
        """O agente nunca dificulta, obstrui ou atrasa um cancelamento
        (Decreto 11.034/2022). A trilha é a evidência: percorrida inteira,
        nenhuma decisão tem `tipo_decisao` ou `saida` que adie ou condicione."""
        await _ciclo_voluntario()
        await _ciclo_involuntario()
        await _ciclo_involuntario(customer="cus_pix", causa="insufficient_funds")
        await disparo_lote.disparar([
            {"customer_id_externo": f"c-{i}", "mrr": 300.0, "billing_profile": "CLT",
             "days_since_last": 45, "features_used_30d": 0, "phone": TELEFONE} for i in range(3)
        ], A)
        assert rl.verificar_cadeia(A)["linhas"] >= 3 + 4 + 4 + 9
        assert rl.decisoes_que_obstruem_cancelamento(A) == []

        for d in rl.decisoes_do_sujeito(A, SUJEITO, limite=500):
            assert d["tipo_decisao"] in rl.TIPOS_DECISAO
            assert d["saida"].get("offer_type") != "consulta_cs"
            assert d["saida"].get("channel") not in CANAIS_HUMANOS

    def test_o_verificador_pega_uma_decisao_que_condicionasse(self):
        rl.registrar_decisoes([
            rl.decisao(A, SUJEITO, "voluntario", "oferta", "offer_bandit", {}, {"offer_type": "consulta_cs"}),
            rl.decisao(A, SUJEITO, "involuntario", "canal", "regra", {}, {"channel": "ligacao_cs"}),
            rl.decisao(A, SUJEITO, "voluntario", "canal", "regra", {}, {"channel": "email",
                                                                         "prazo_para_cancelar": "30d"}),
            rl.decisao(A, SUJEITO, "voluntario", "canal", "regra", {}, {"channel": "email"}),
        ])
        v = rl.decisoes_que_obstruem_cancelamento(A)
        assert [x["motivo"].split(":")[0] for x in v] == [
            "oferta que condiciona o cancelamento", "canal humano", "saída com marca de obstrução"]
        assert rl.decisoes_que_obstruem_cancelamento(B) == []


# ── Operadora: a rota exige tenant; não há rota pública ──────────────────

class TestOperadora:
    def test_a_rota_do_titular_exige_tenant_e_nao_ha_outra(self):
        rotas = [r for r in app_module.app.routes if "titular" in getattr(r, "path", "")]
        assert [r.path for r in rotas] == ["/titular/explicacao/{sujeito_id}"]
        deps = {getattr(d.call, "__name__", "") for d in rotas[0].dependant.dependencies}
        assert "get_tenant_id" in deps
        assert "Art. 20 §1º" in titular_api.explicacao_do_titular.__doc__
        assert "segredo comercial" in titular_api.explicacao_do_titular.__doc__

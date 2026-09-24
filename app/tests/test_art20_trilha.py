"""tests/test_art20_trilha.py — a trilha de decisão do Art. 20 da LGPD (Bloco F).

O que o Bloco F promete, e o que aqui é verificado:

  (a) `decisoes_automatizadas` é uma tabela nova no MESMO módulo e banco do
      `ciclos_retencao`, e é append-only: no módulo não existe UPDATE nem
      DELETE sobre ela fora de `apagar_trilha_expirada` — testado lendo o
      código-fonte, não prometido;
  (b) encadeamento por hash: cinco decisões, a terceira alterada direto no
      banco, `verificar_cadeia` aponta a quebra na QUARTA (e a própria
      terceira, quando o hash dela não foi regravado);
  (c) NENHUM dado cru entra: e-mail, telefone, texto da mensagem,
      `motivo_cancelamento` e nome presentes no estado, e a linha gravada não
      contém nenhum deles — conferido no CONTEÚDO das colunas, JSON inclusive,
      nos dois domínios;
  (d) tenant A nunca lê linha de B;
  (e) os pontos de decisão (voluntário: risco/oferta/canal; involuntário:
      risco/retentativa/oferta/canal; lote) gravam com modelo, versão e
      explicação em pt-BR; decisão de regra tem `contribuicoes` NULL e nomeia
      a regra; o classificador grava as 5 contribuições SHAP da hora;
  (f) as rotas de CRUD da API de clientes NÃO entram na trilha;
  (g) retenção: `RETENCAO_TRILHA_DIAS` declarada, `apagar_trilha_expirada`
      pronta e não chamada por ninguém; best effort: falha no banco não
      levanta.

A CRAI é OPERADORA: não há rota pública para o titular, e o teste (h) trava
que qualquer rota com "titular" no caminho exija `get_tenant_id`.

Uso:
    pytest tests/test_art20_trilha.py -v
"""

import ast
import inspect
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as wf
from crai.api import app as app_module
from crai.churn_voluntary import disparo_lote
from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import voluntary_agent as va
from crai.dunning.dunning_engine import DunningEngine

A, B = "empresa-a", "empresa-b"

# O que NUNCA pode aparecer numa linha gravada.
EMAIL = "fulano.da.silva@cliente-final.com.br"
TELEFONE = "11912345678"
NOME = "Fulano da Silva Sauro"
TEXTO_MENSAGEM = "Oi Fulano, vimos que você quase cancelou — que tal 20% de desconto?"
MOTIVO = "mudou de fornecedor porque o suporte demorou"
PROIBIDOS = (EMAIL, TELEFONE, NOME, "Fulano", TEXTO_MENSAGEM, MOTIVO)


def _linhas_cruas(tenant_id=None) -> list[dict]:
    """Tudo que está gravado, como texto — sem passar pelo leitor do módulo."""
    con = rl._conectar()                     # garante o schema; nada mais
    try:
        sql = "SELECT * FROM decisoes_automatizadas"
        params = ()
        if tenant_id:
            sql += " WHERE tenant_id = ?"
            params = (tenant_id,)
        return [dict(l) for l in con.execute(sql + " ORDER BY id", params)]
    finally:
        con.close()


def _texto_da_linha(l: dict) -> str:
    return " ".join(str(v) for v in l.values() if v is not None)


def _assert_sem_dado_cru(linhas: list[dict]):
    assert linhas, "nenhuma linha gravada — o teste não provou nada"
    for l in linhas:
        texto = _texto_da_linha(l)
        for proibido in PROIBIDOS:
            assert proibido not in texto, f"{proibido!r} vazou na linha {l['id']}: {texto[:200]}"
        # E as chaves, em qualquer profundidade do JSON:
        for campo in ("entradas", "saida", "contribuicoes"):
            if l.get(campo):
                _assert_sem_chave_proibida(json.loads(l[campo]), l["id"])


def _assert_sem_chave_proibida(valor, id_linha):
    if isinstance(valor, dict):
        for k, v in valor.items():
            assert str(k).lower() not in rl.CHAVES_FORA_DA_TRILHA, f"chave {k!r} na linha {id_linha}"
            _assert_sem_chave_proibida(v, id_linha)
    elif isinstance(valor, list):
        for v in valor:
            _assert_sem_chave_proibida(v, id_linha)


def _dec(tenant, sujeito="user:s-1", tipo="risco", **kw):
    base = dict(dominio="voluntario", tipo_decisao=tipo, modelo="regra",
                entradas={"days_since_last": 10, "mrr": 100}, saida={"risk_score": 0.5, "regra": "x"})
    base.update(kw)
    return rl.decisao(tenant, sujeito, **base)


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


# ── (a) Tabela nova, mesmo módulo, append-only de verdade ────────────────

class TestAppendOnly:
    def test_mesmo_banco_das_duas_tabelas(self):
        rl.registrar_ciclo({"tenant_id": A, "user_id": "user:x", "event": "e", "props": {}})
        rl.registrar_decisao(_dec(A))
        con = sqlite3.connect(rl.caminho_do_banco())
        tabelas = {l[0] for l in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert {"ciclos_retencao", "decisoes_automatizadas"} <= tabelas

    def test_nenhum_update_nem_delete_fora_da_retencao(self):
        """Lê o código-fonte do módulo: toda string SQL com UPDATE/DELETE sobre
        `decisoes_automatizadas` tem que estar dentro de `apagar_trilha_expirada`."""
        fonte = inspect.getsource(rl)
        arvore = ast.parse(fonte)
        ofensores = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.FunctionDef):
                for s in ast.walk(no):
                    if isinstance(s, ast.Constant) and isinstance(s.value, str):
                        sql = s.value.upper()
                        if "DECISOES_AUTOMATIZADAS" in sql and ("UPDATE " in sql or "DELETE " in sql):
                            if no.name != "apagar_trilha_expirada":
                                ofensores.append((no.name, s.value[:60]))
        assert ofensores == [], ofensores
        # E o `ciclos_retencao` continua com o UPDATE do desfecho — não é
        # ele que virou append-only.
        assert "UPDATE ciclos_retencao" in fonte

    def test_a_linha_carrega_as_catorze_colunas(self):
        rl.registrar_decisao(_dec(A))
        (l,) = _linhas_cruas(A)
        assert set(l) == {"id", "tenant_id", "sujeito_id", "decidido_em", "dominio",
                          "tipo_decisao", "modelo", "modelo_versao", "entradas", "saida",
                          "explicacao", "contribuicoes", "hash_anterior", "hash_linha"}
        assert l["decidido_em"].endswith("+00:00")
        assert len(l["hash_linha"]) == 64 and l["hash_anterior"] == rl.HASH_GENESE


# ── (b) Cadeia por hash ──────────────────────────────────────────────────

class TestCadeia:
    def _cinco(self, tenant=A):
        ids = rl.registrar_decisoes([_dec(tenant, sujeito=f"user:{i}") for i in range(5)])
        assert all(ids) and ids == sorted(ids)
        return ids

    def _alterar(self, id_linha, regravar_hash=False):
        con = sqlite3.connect(rl.caminho_do_banco())
        con.row_factory = sqlite3.Row
        con.execute("UPDATE decisoes_automatizadas SET saida = ? WHERE id = ?",
                    (json.dumps({"risk_score": 0.01, "regra": "x"}), id_linha))
        if regravar_hash:
            l = con.execute("SELECT * FROM decisoes_automatizadas WHERE id = ?", (id_linha,)).fetchone()
            novo = rl._hash_da_linha(l["tenant_id"], l["sujeito_id"], l["decidido_em"], l["dominio"],
                                     l["tipo_decisao"], l["modelo"], l["modelo_versao"], l["entradas"],
                                     l["saida"], l["explicacao"], l["contribuicoes"], l["hash_anterior"])
            con.execute("UPDATE decisoes_automatizadas SET hash_linha = ? WHERE id = ?", (novo, id_linha))
        con.commit()
        con.close()

    def test_cadeia_integra_encadeia_cada_linha_na_anterior(self):
        ids = self._cinco()
        linhas = _linhas_cruas(A)
        for anterior, atual in zip(linhas, linhas[1:]):
            assert atual["hash_anterior"] == anterior["hash_linha"]
        r = rl.verificar_cadeia(A)
        assert r["integra"] is True and r["linhas"] == 5 and r["quebra_em_id"] is None
        assert r["ultimo_hash"] == linhas[-1]["hash_linha"]

    def test_alterar_a_terceira_quebra_na_quarta(self):
        ids = self._cinco()
        self._alterar(ids[2])
        r = rl.verificar_cadeia(A)
        assert r["integra"] is False
        assert r["quebra_em_id"] == ids[3]                     # a quarta
        assert r["hash_proprio_invalido_em_id"] == ids[2]      # e a própria terceira

    def test_alterar_a_terceira_regravando_o_hash_ainda_quebra_na_quarta(self):
        """É para isto que existe o encadeamento: quem regrava o hash da
        linha alterada não engana a linha seguinte."""
        ids = self._cinco()
        self._alterar(ids[2], regravar_hash=True)
        r = rl.verificar_cadeia(A)
        assert r["integra"] is False
        assert r["quebra_em_id"] == ids[3]
        assert r["hash_proprio_invalido_em_id"] is None

    def test_cadeia_e_por_tenant(self):
        ids_a = self._cinco(A)
        ids_b = self._cinco(B)
        self._alterar(ids_b[1])
        assert rl.verificar_cadeia(A)["integra"] is True
        assert rl.verificar_cadeia(B)["quebra_em_id"] == ids_b[2]
        assert _linhas_cruas(B)[0]["hash_anterior"] == rl.HASH_GENESE   # B começa do zero
        assert _linhas_cruas(A)[-1]["hash_linha"] != _linhas_cruas(B)[-1]["hash_linha"]

    def test_tenant_sem_trilha_e_integro_e_vazio(self):
        r = rl.verificar_cadeia("ninguem")
        assert r == {"tenant_id": "ninguem", "integra": True, "linhas": 0, "quebra_em_id": None,
                     "hash_proprio_invalido_em_id": None, "bifurcacao_em_id": None,
                     "inicio_truncado": False, "ultimo_hash": None}


def _inserir_cru(con, tenant, sujeito, hash_anterior, hash_linha):
    con.execute("INSERT INTO decisoes_automatizadas (tenant_id, sujeito_id, decidido_em, "
                "dominio, tipo_decisao, modelo, entradas, saida, explicacao, hash_anterior, "
                "hash_linha) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (tenant, sujeito, "2026-01-01T00:00:00+00:00", "voluntario", "risco", "regra",
                 "{}", "{}", "x", hash_anterior, hash_linha))


class TestSemBifurcacao:
    """Dois gravadores do mesmo tenant: um grava, o outro falha; a cadeia
    continua linear. É o índice único `(tenant_id, hash_anterior)`."""

    def test_o_lote_encadeia_as_proprias_linhas_e_nao_todas_no_mesmo_antecessor(self):
        rl.registrar_decisoes([_dec(A, sujeito=f"user:{i}") for i in range(5)])
        linhas = _linhas_cruas(A)
        antecessores = [l["hash_anterior"] for l in linhas]
        assert len(set(antecessores)) == 5                   # nenhum repetido
        assert antecessores[0] == rl.HASH_GENESE
        assert antecessores[1:] == [l["hash_linha"] for l in linhas[:-1]]

    def test_segundo_gravador_com_o_mesmo_antecessor_falha_alto_e_nada_bifurca(self, monkeypatch, capsys):
        rl.registrar_decisao(_dec(A, sujeito="user:base"))
        ponta_antiga = rl.verificar_cadeia(A)["ultimo_hash"]

        # Gravador 1: normal, avança a ponta.
        assert rl.registrar_decisao(_dec(A, sujeito="user:g1")) is not None
        # Gravador 2: leu a ponta ANTES do gravador 1 (fora da trava — o
        # cenário de dois workers em outro banco) e tenta encadear nela.
        with pytest.MonkeyPatch.context() as mp:      # não `monkeypatch.undo()`: desfaria o conftest
            mp.setattr(rl, "_ultimo_hash", lambda conn, t: ponta_antiga)
            assert rl.registrar_decisao(_dec(A, sujeito="user:g2")) is None

        assert "BIFURCAÇÃO EVITADA" in capsys.readouterr().out
        linhas = _linhas_cruas(A)
        assert [l["sujeito_id"] for l in linhas] == ["user:base", "user:g1"]   # g2 não entrou
        r = rl.verificar_cadeia(A)
        assert r["integra"] is True and r["bifurcacao_em_id"] is None and r["linhas"] == 2
        # E o gravador 2, ao tentar de novo lendo a ponta certa, entra.
        assert rl.registrar_decisao(_dec(A, sujeito="user:g2")) is not None
        assert rl.verificar_cadeia(A)["integra"] is True

    def test_duas_primeiras_linhas_do_mesmo_tenant_tambem_e_bifurcacao(self):
        """A gênese é um valor, não NULL — senão o índice único não pegaria."""
        rl.registrar_decisao(_dec(A))
        con = rl._conectar()
        with pytest.raises(sqlite3.IntegrityError):
            _inserir_cru(con, A, "user:x", rl.HASH_GENESE, "f" * 64)
        con.close()

    def test_gravadores_concorrentes_de_verdade_serializam_e_a_cadeia_fica_linear(self):
        import threading
        erros, ids = [], []

        def gravar(n):
            try:
                ids.extend(rl.registrar_decisoes([_dec(A, sujeito=f"user:t{n}-{i}") for i in range(20)]))
            except Exception as e:                       # noqa: BLE001
                erros.append(e)

        threads = [threading.Thread(target=gravar, args=(n,)) for n in range(4)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        assert erros == []
        gravadas = [i for i in ids if i is not None]
        # Sob a trava do SQLite ninguém lê ponta velha: todo lote entra, em
        # série. Se algum tivesse lido ponta velha, teria falhado, não bifurcado.
        assert len(gravadas) == len(_linhas_cruas(A))
        r = rl.verificar_cadeia(A)
        assert r["integra"] is True and r["bifurcacao_em_id"] is None

    def test_verificar_cadeia_denuncia_bifurcacao_numa_base_sem_o_indice(self, capsys):
        """Base criada antes do índice e já bifurcada: a conexão continua
        funcionando (o índice não pode ser criado, e isso é logado), e a
        verificação aponta o ramo."""
        rl.registrar_decisoes([_dec(A, sujeito="user:1"), _dec(A, sujeito="user:2")])
        con = rl._conectar()
        con.execute("DROP INDEX uq_decisao_elo")
        primeira = con.execute(
            "SELECT hash_linha FROM decisoes_automatizadas ORDER BY id LIMIT 1").fetchone()[0]
        _inserir_cru(con, A, "user:ramo", primeira, "e" * 64)
        con.commit()
        con.close()
        r = rl.verificar_cadeia(A)
        assert r["integra"] is False and r["bifurcacao_em_id"] == 3
        assert "já tem bifurcação" in capsys.readouterr().out
        # e o dataset de treino continua gravando na mesma base:
        assert rl.registrar_ciclo({"tenant_id": A, "user_id": "user:x", "event": "e", "props": {}})


# ── (c) O que nunca entra — lendo o conteúdo ─────────────────────────────

def _estado_voluntario_sujo(tenant=A, user="user:cliente-final-1"):
    return {
        "tenant_id": tenant, "user_id": user, "event": "Cancellation Page Viewed",
        "props": {"days_since_last": 23, "features_used_30d": 2, "mrr": 1800,
                  "billing_profile": "PJ", "on_site_now": False,
                  "email": EMAIL, "phone": TELEFONE, "name": NOME, "nome": NOME,
                  "motivo_cancelamento": MOTIVO},
        "message": TEXTO_MENSAGEM, "motivo_cancelamento": MOTIVO, "email": EMAIL,
        "offer_sent": True, "accepted": None, "retained": False,
    }


class TestNadaCruNoVoluntario:
    @pytest.mark.asyncio
    async def test_ciclo_completo_com_email_texto_e_motivo_no_estado(self):
        s = _estado_voluntario_sujo()
        s = await va.assess_risk(s)
        s = await va.choose_offer(s)
        s = await va.choose_channel(s)
        s = {**s, "message": TEXTO_MENSAGEM}          # o texto que a pessoa leu
        assert len(s["decisoes"]) == 3
        s = await va.update_crm(s)

        linhas = _linhas_cruas(A)
        assert [l["tipo_decisao"] for l in linhas] == ["risco", "oferta", "canal"]
        _assert_sem_dado_cru(linhas)
        # e o que DEVE estar lá, está:
        entradas_risco = json.loads(linhas[0]["entradas"])
        assert entradas_risco["days_since_last"] == 23.0 and entradas_risco["mrr"] == 1800.0
        assert "telefone_disponivel" in json.loads(linhas[2]["entradas"])   # o SE, não o número

    def test_o_filtro_tira_as_chaves_em_qualquer_profundidade(self):
        d = rl.decisao(A, "user:x", "voluntario", "canal", "regra",
                       entradas={"ok": 1, "email": EMAIL, "aninhado": {"phone": TELEFONE, "lista": [{"name": NOME}]}},
                       saida={"channel": "email", "message": TEXTO_MENSAGEM, "motivo_cancelamento": MOTIVO,
                              "candidatas": [{"texto": TEXTO_MENSAGEM}], "alpha": 3, "beta": 4})
        rl.registrar_decisao(d)
        _assert_sem_dado_cru(_linhas_cruas(A))
        (l,) = _linhas_cruas(A)
        assert json.loads(l["entradas"]) == {"ok": 1, "aninhado": {"lista": [{}]}}
        assert json.loads(l["saida"]) == {"channel": "email"}


def _estado_involuntario_sujo(tenant=A, customer="cus_final_1", causa="card_declined"):
    return {
        "tenant_id": tenant, "customer_id": customer, "amount": 250.0, "invoice_id": "in_1",
        "payment_method": "card",
        "payment_event": {"data": {"object": {
            "amount": 25000, "failure_code": causa, "customer": customer, "attempt_count": 1,
            "payment_method_details": {"brand": "visa"},
            "billing_details": {"name": NOME, "email": EMAIL, "phone": TELEFONE},
        }}},
        "email": EMAIL, "motivo_cancelamento": MOTIVO, "retry_count": 0,
    }


class TestNadaCruNoInvoluntario:
    @pytest.mark.asyncio
    async def test_ciclo_completo_diagnostico_decisao_campanha(self):
        s = _estado_involuntario_sujo()
        s = await wf.diagnose_failure(s)
        s = await wf.decide_recovery(s)
        assert s["estrategia"] == "mensagem_pagamento"
        s = await wf.trigger_dunning(s)
        assert s["message_sent"] and "http" in s["message_sent"]
        tipos = [d["tipo_decisao"] for d in s["decisoes"]]
        assert tipos == ["risco", "retentativa", "oferta", "canal"]
        s = await wf.update_roi_dashboard(s)

        linhas = _linhas_cruas(A)
        assert [l["tipo_decisao"] for l in linhas] == tipos
        _assert_sem_dado_cru(linhas)
        # O texto da mensagem (template com link) não está em lugar nenhum;
        # o código do template e a origem, sim.
        for l in linhas:
            assert s["message_sent"] not in _texto_da_linha(l)
            assert "http" not in _texto_da_linha(l)
        saida_oferta = json.loads(linhas[2]["saida"])
        assert saida_oferta["origem_texto"] == "template"
        assert saida_oferta["texto_codigo"] == "card_declined"
        assert saida_oferta["payment_method"] == "pix_automatico"


# ── (d) Tenant A nunca lê linha de B ─────────────────────────────────────

class TestIsolamentoPorTenant:
    def test_leitura_por_sujeito_e_cadeia_sao_cercadas(self):
        rl.registrar_decisoes([_dec(A, sujeito="user:mesmo-id"), _dec(B, sujeito="user:mesmo-id")])
        de_a = rl.decisoes_do_sujeito(A, "user:mesmo-id")
        de_b = rl.decisoes_do_sujeito(B, "user:mesmo-id")
        assert len(de_a) == len(de_b) == 1
        assert de_a[0]["tenant_id"] == A and de_b[0]["tenant_id"] == B
        assert rl.decisoes_do_sujeito(A, "user:so-de-b") == []
        rl.registrar_decisao(_dec(B, sujeito="user:so-de-b"))
        assert rl.decisoes_do_sujeito(A, "user:so-de-b") == []      # igual a inexistente
        assert rl.verificar_cadeia(A)["linhas"] == 1
        assert rl.verificar_cadeia(B)["linhas"] == 2

    def test_paginacao_mais_recentes_primeiro(self):
        rl.registrar_decisoes([_dec(A, sujeito="user:p", tipo=t) for t in ("risco", "oferta", "canal")])
        pagina = rl.decisoes_do_sujeito(A, "user:p", limite=2)
        assert [d["tipo_decisao"] for d in pagina] == ["canal", "oferta"]
        resto = rl.decisoes_do_sujeito(A, "user:p", limite=2, antes_de=pagina[-1]["id"])
        assert [d["tipo_decisao"] for d in resto] == ["risco"]
        assert isinstance(pagina[0]["entradas"], dict)             # o leitor desserializa


# ── (e) Os pontos de decisão ─────────────────────────────────────────────

class TestPontosDeDecisao:
    @pytest.mark.asyncio
    async def test_voluntario_risco_e_regra_com_contribuicoes_null_e_nomeia_a_regra(self):
        s = await va.assess_risk(_estado_voluntario_sujo())
        (d,) = s["decisoes"]
        assert d["dominio"] == "voluntario" and d["tipo_decisao"] == "risco"
        assert d["modelo"] == "regra" and d["modelo_versao"] is None
        assert d["contribuicoes"] is None
        assert d["saida"]["regra"] == "risk_scorer.risco_por_features"
        assert "risco_por_features" in d["explicacao"]
        assert "23 dias sem acesso" in d["explicacao"]
        assert "R$ 1.800,00" in d["explicacao"]
        assert d["decidido_em"].endswith("+00:00")

    @pytest.mark.asyncio
    async def test_voluntario_oferta_e_o_bandit_sem_alpha_beta(self):
        s = await va.assess_risk(_estado_voluntario_sujo())
        s = await va.choose_offer(s)
        d = s["decisoes"][1]
        assert d["tipo_decisao"] == "oferta" and d["modelo"] == "offer_bandit"
        assert d["contribuicoes"] is None
        assert d["saida"]["offer_type"] == s["offer_type"]
        assert all(set(c) == {"offer", "p_estimado"} for c in d["saida"]["ofertas_consideradas"])
        assert "alpha" not in json.dumps(d) and "beta" not in json.dumps(d)
        assert "offer_bandit" in d["explicacao"]

    @pytest.mark.asyncio
    async def test_voluntario_canal_nomeia_a_regra_e_nao_grava_o_telefone(self):
        s = await va.assess_risk(_estado_voluntario_sujo())
        s = await va.choose_offer(s)
        s = await va.choose_channel(s)
        d = s["decisoes"][2]
        assert d["tipo_decisao"] == "canal" and d["modelo"] == "regra"
        assert d["saida"]["channel"] == s["channel"]
        assert d["saida"]["regra"].startswith("choose_channel.")
        assert d["entradas"]["telefone_disponivel"] is True
        assert TELEFONE not in json.dumps(d)
        assert d["saida"]["regra"].split(".")[1] in d["explicacao"]

    @pytest.mark.asyncio
    async def test_involuntario_risco_vem_do_classificador_com_5_contribuicoes_shap(self):
        if not wf._classifier.is_fitted:
            pytest.skip("classificador não carregado nesta instalação")
        s = await wf.diagnose_failure(_estado_involuntario_sujo())
        (d,) = s["decisoes"]
        assert d["modelo"] == "failure_classifier"
        assert d["modelo_versao"] == wf._VERSAO_CLASSIFICADOR
        assert d["modelo_versao"] and "#" in d["modelo_versao"]
        assert len(d["contribuicoes"]) == 5
        assert all({"feature", "contribution_pct", "direcao"} <= set(c) for c in d["contribuicoes"])
        assert d["saida"]["recovery_score"] == s["recovery_score"]
        assert "Pesaram, nesta ordem" in d["explicacao"]
        assert "failure_classifier" in d["explicacao"]

    @pytest.mark.asyncio
    async def test_involuntario_retentativa_e_regra(self):
        s = await wf.decide_recovery({**_estado_involuntario_sujo(causa="insufficient_funds"),
                                      "failure_cause": "insufficient_funds", "recovery_score": 60,
                                      "eprofit": 100.0, "is_anomalous": False,
                                      "payment_method": "pix_automatico", "p_recovery": 0.6})
        (d,) = s["decisoes"]
        assert d["tipo_decisao"] == "retentativa" and d["modelo"] == "regra"
        assert d["saida"]["estrategia"] == "retry_automatico"
        assert d["saida"]["regra"] == "decide_recovery"
        assert d["entradas"]["limite_tentativas"] == 3
        assert d["contribuicoes"] is None
        assert "decide_recovery" in d["explicacao"] and "saldo insuficiente" in d["explicacao"]

    @pytest.mark.asyncio
    async def test_campanha_grava_oferta_e_canal_com_codigo_do_texto_e_nao_o_texto(self):
        eng = DunningEngine()
        r = await eng.run_campaign("cus_z", "processing_error", 0.5, 300.0, tenant_id=A)
        oferta, canal = r["decisoes"]
        assert oferta["tipo_decisao"] == "oferta" and oferta["saida"]["payment_method"] == "boleto"
        assert oferta["saida"]["texto_codigo"] == "processing_error"
        assert oferta["saida"]["origem_texto"] == "template"
        assert r["message"] not in json.dumps(oferta) and "http" not in json.dumps(oferta)
        assert canal["tipo_decisao"] == "canal" and canal["saida"]["channel"] == "whatsapp"
        assert "boleto" in oferta["explicacao"] and "WhatsApp" in canal["explicacao"]

    @pytest.mark.asyncio
    async def test_lote_grava_risco_oferta_e_canal_por_cliente_numa_transacao(self):
        clientes = [
            {"customer_id_externo": f"c-{i}", "mrr": 300.0, "billing_profile": "CLT",
             "days_since_last": 45, "features_used_30d": 0, "phone": TELEFONE,
             "email": EMAIL, "nome": NOME} for i in range(3)
        ]
        resultado = await disparo_lote.disparar(clientes, A)
        assert resultado["simulado"] is True
        linhas = _linhas_cruas(A)
        por_sujeito: dict = {}
        for l in linhas:
            por_sujeito.setdefault(l["sujeito_id"], []).append(l["tipo_decisao"])
        assert len(por_sujeito) == 3
        assert all(v == ["risco", "oferta", "canal"] for v in por_sujeito.values())
        _assert_sem_dado_cru(linhas)
        assert rl.verificar_cadeia(A)["integra"] is True
        assert json.loads(linhas[0]["saida"])["regra"].startswith("batch_scoring.")


# ── (f) CRUD da API de clientes NÃO entra na trilha ──────────────────────

class TestCrudNaoEntra:
    def test_post_patch_delete_de_cliente_nao_gravam_decisao(self, supabase_falso):
        with TestClient(app_module.app, raise_server_exceptions=False) as c:
            h = supabase_falso.bearer(A)
            corpo = {"customer_id_externo": "c-1", "mrr": 100.0, "billing_profile": "PJ",
                     "days_since_last": 40, "features_used_30d": 0, "email": EMAIL}
            assert c.post("/clientes", json=corpo, headers=h).status_code == 200
            assert c.patch("/clientes/c-1", json={"mrr": 200.0}, headers=h).status_code == 200
            assert c.request("DELETE", "/clientes/c-1", json={"motivo": MOTIVO}, headers=h).status_code == 200
            assert c.post("/clientes/lote", json={"clientes": [corpo]}, headers=h).status_code == 200
        assert _linhas_cruas() == []


# ── (g) Retenção e best effort ───────────────────────────────────────────

class TestRetencaoEBestEffort:
    def test_retencao_declarada_e_a_funcao_nao_e_chamada_por_ninguem(self):
        assert isinstance(rl.RETENCAO_TRILHA_DIAS, int) and rl.RETENCAO_TRILHA_DIAS > 0
        assert "CONFIRMADO" in inspect.getsource(rl).split("RETENCAO_TRILHA_DIAS = ")[0][-1200:]
        import pathlib
        raiz = pathlib.Path(rl.__file__).resolve().parent.parent
        chamadores = [p for p in raiz.rglob("*.py")
                      if "apagar_trilha_expirada(" in p.read_text(encoding="utf-8")
                      and p.name != "retention_log.py"]
        assert chamadores == [], chamadores

    def test_apagar_trilha_expirada_tira_so_o_antigo_e_a_cadeia_segue_verificavel(self):
        antiga = _dec(A, decidido_em="2020-01-01T00:00:00+00:00")
        rl.registrar_decisoes([antiga, _dec(A), _dec(A)])
        assert rl.apagar_trilha_expirada() == 1
        assert rl.verificar_cadeia(A)["linhas"] == 2
        r = rl.verificar_cadeia(A)
        assert r["integra"] is True and r["inicio_truncado"] is True
        assert rl.apagar_trilha_expirada(tenant_id=B) == 0

    def test_falha_no_banco_nao_levanta(self, monkeypatch):
        def quebrado():
            raise sqlite3.OperationalError("disco cheio (teste)")
        monkeypatch.setattr(rl, "_conectar", quebrado)
        assert rl.registrar_decisoes([_dec(A), _dec(A)]) == [None, None]
        assert rl.registrar_decisao(_dec(A)) is None
        assert rl.decisoes_do_sujeito(A, "user:s-1") == []
        assert rl.verificar_cadeia(A)["integra"] is False and "erro" in rl.verificar_cadeia(A)

    def test_decisao_mal_montada_e_ignorada_com_log(self, capsys):
        ruim = rl.decisao(A, "user:x", "dominio-inexistente", "risco", "regra", {}, {})
        assert ruim.get("_falha")
        assert rl.registrar_decisoes([ruim, _dec(A)])[0] is None
        assert rl.verificar_cadeia(A)["linhas"] == 1
        assert "[ART20]" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_no_grafo_a_falha_da_trilha_nao_derruba_a_decisao(self, monkeypatch):
        monkeypatch.setattr(rl, "_conectar", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        s = await va.assess_risk(_estado_voluntario_sujo())
        s = await va.update_crm({**s, "offer_type": None})
        assert s["risk_score"] > 0                        # decidiu, mesmo sem trilha


# ── (h) Operadora: não existe rota pública para o titular ────────────────

class TestOperadora:
    def test_toda_rota_do_titular_exige_tenant(self):
        # CATRACA QUE NÃO ENCONTRA O QUE VERIFICAR REPROVA. Com um
        # `_IncludedRouter` sem `.path` em `app.routes` (FastAPI mais novo), o
        # `getattr(..., "")` nunca casa "titular" e o teste passava sem olhar
        # rota nenhuma — a lista tem que ser não-vazia antes de iterar.
        rotas = [r for r in app_module.app.routes if "titular" in getattr(r, "path", "")]
        assert rotas, ("nenhuma rota com 'titular' em app.routes — a catraca não tem "
                       "o que verificar (include_router deixou de achatar as rotas?)")
        for rota in rotas:
            deps = getattr(getattr(rota, "dependant", None), "dependencies", [])
            nomes = {getattr(d.call, "__name__", "") for d in deps}
            assert nomes & {"get_tenant_id", "get_conta"}, rota.path

    def test_a_catraca_reprova_quando_nao_ha_rota_para_verificar(self, monkeypatch):
        monkeypatch.setattr(app_module.app.router, "routes", [])
        with pytest.raises(AssertionError):
            self.test_toda_rota_do_titular_exige_tenant()

"""tests/test_intervencao_modelo_v3.py - o conserto da pendencia 1 da Rodada 3.

Quando o MODELO v3 decide o risco, intervir ou nao deixa de usar o corte fixo
de 0,60 (a escala do modelo nao e a da regua: em 56 combinacoes medidas o risco
ficou entre 0,07 e 0,40, e nenhuma recebia oferta). O QUE ESTE ARQUIVO MEDE:

  1. evento de intencao explicita (pagina de cancelamento, rebaixar o plano):
     o sistema intervem SEMPRE, por regra, e o risco do modelo continua
     calculado e gravado;
  2. nos outros eventos: intervem quem esta como grave ou preocupante pela
     posicao na base (com o sinal absoluto de abandono); sem referencia de
     posicao, nao intervem;
  3. a faixa define a intensidade: grave leva a oferta do bandit; o resto, a
     de menor custo entre as que o bandit considerou;
  4. quando a REGUA decide (sem modelo, sem dado para o modelo, ou modelo
     legado), nada muda: o corte de 0,60 continua, e a trilha nao ganha chave;
  5. a trilha diz qual regra levou a intervencao, a frase diz isso em
     portugues, e a cadeia continua integra;
  o intervalo minimo entre ofertas e o "nao contatar" seguram a oferta nos dois
  casos.

O modelo e o de `models/v3/` (so lido), copiado para a pasta temporaria que o
`conftest` ja aponta, com meta de producao. A referencia de posicao e a do
meta, escolhida por teste para a posicao do cliente ser conhecida.
"""

import json
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from crai.accounts import chaves_api
from crai.api import app as app_module
from crai.churn_voluntary import batch_scoring as bs
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import offer_bandit as ob
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary import risk_scorer as rs
from crai.churn_voluntary import voluntary_agent as va
from crai.dunning import configuracao

APP = Path(__file__).resolve().parents[1]
V3 = APP / "models" / "v3"

pytestmark = pytest.mark.skipif(not (V3 / "voluntary_risk_v3.joblib").exists(),
                                reason="artefato do v3 ausente (app/models/v3)")

A = "empresa-a"
CANCELAMENTO, REBAIXAR, SESSAO = "Cancellation Page Viewed", "Downgrade Clicked", "Session Started"
SAUDAVEL = {"mrr": 500.0, "billing_profile": "PJ", "days_since_last": 1,
            "features_used_30d": 8, "logins_30d": 38}
ABANDONO = {"mrr": 500.0, "billing_profile": "PJ", "days_since_last": 30,
            "features_used_30d": 0, "tickets_30d": 3}

# Referencias de posicao (os quantis do score gravados no meta de producao).
REF_TODOS_ABAIXO = [i / 100000 for i in range(101)]        # qualquer score fica no topo
REF_TODOS_ACIMA = [0.95 + i / 10000 for i in range(101)]   # qualquer score fica no fundo


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.delenv("CRAI_SIMULATE_OUTCOMES", raising=False)      # o grafo de producao
    monkeypatch.delenv(chaves_api.ENV_LIMITE_EVENTOS, raising=False)
    monkeypatch.setattr(bs, "_referencia_do_score", {})
    monkeypatch.setattr(bs, "_referencia_do_mrr", {})
    chaves_api.limpar_limites()
    va._channel_history.clear()

    async def recusa(*a, **k):
        raise RuntimeError("sem LLM no teste")
    monkeypatch.setattr(va.claude.messages, "create", recusa)
    yield
    chaves_api.limpar_limites()
    va._channel_history.clear()


@pytest.fixture
def producao(monkeypatch):
    """Os padroes de PRODUCAO da configuracao (o intervalo de 30 dias entre ofertas)."""
    for chave, valor in configuracao.PADROES_DE_PRODUCAO.items():
        monkeypatch.setitem(configuracao.PADROES, chave, valor)


@pytest.fixture
def cliente(supabase_falso):
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        c.projeto = supabase_falso
        yield c


def _instalar(quantis=None):
    """O modelo v3 ativo, com (ou sem) a referencia de posicao no meta."""
    meta = json.loads((V3 / "voluntary_risk_v3_meta.json").read_text(encoding="utf-8"))
    meta.update({"contrato": "v3", "contrato_de_producao": True})
    if quantis is not None:
        meta["referencia_score_quantis"] = list(quantis)
    shutil.copyfile(V3 / "voluntary_risk_v3.joblib", rs.MODELO_PATH)
    rs.MODELO_META_PATH.write_text(json.dumps(meta), encoding="utf-8")
    assert rs.carregar_modelo(forcar=True) is True
    assert rs.contrato_ativo() == rs.CONTRATO_V3


def _ref_que_deixa_preocupante(evento, props):
    """Uma referencia em que este cliente fica a 79% da base: preocupante."""
    score = rs.calculate_risk(evento, props)
    return sorted([max(score - 0.01, 0.0)] * 80 + [score + 0.01] * 21)


def _login(c, tenant=A):
    return c.projeto.bearer(tenant, papel="owner", plano="premium")


def _chave(c, tenant=A) -> str:
    r = c.post("/integracao/chaves", json={"nome": "Servidor"}, headers=_login(c, tenant))
    assert r.status_code == 201, r.text
    return r.json()["chave_inteira"]


def _evento(c, chave, user="c-1", evento=CANCELAMENTO, props=None, **extra):
    corpo = {"userId": user, "event": evento, "properties": dict(props or SAUDAVEL), **extra}
    r = c.post("/eventos", json=corpo, headers={"Authorization": f"Bearer {chave}"})
    assert r.status_code == 200, r.text
    return r


def _sql(consulta, *args) -> list:
    conn = sqlite3.connect(rl.caminho_do_banco())
    conn.row_factory = sqlite3.Row
    try:
        return [dict(l) for l in conn.execute(consulta, args)]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _linhas(tenant=A) -> list:
    return _sql("SELECT * FROM ciclos_retencao WHERE tenant_id = ? ORDER BY id", tenant)


def _ofertas(tenant=A) -> list:
    return [l for l in _linhas(tenant) if l["offer_type"] and l["offer_sent"]]


def _decisoes(tenant=A, tipo=None) -> list:
    todas = _sql("SELECT * FROM decisoes_automatizadas WHERE tenant_id = ? ORDER BY id", tenant)
    for d in todas:
        d["saida"] = json.loads(d["saida"])
        d["entradas"] = json.loads(d["entradas"])
    return [d for d in todas if tipo is None or d["tipo_decisao"] == tipo]


def _adiantar(monkeypatch, **delta):
    quando = datetime.now(timezone.utc) + timedelta(**delta)
    monkeypatch.setattr(rl, "_agora", lambda: quando.isoformat(timespec="seconds"))


def _custo(oferta, mrr=500.0) -> float:
    return ob.offer_cost(oferta, mrr)


# == As tres funcoes da regra ==============================================

class TestARegra:
    def test_os_eventos_de_intencao_sao_os_dois_de_risco_fixo(self):
        assert va.EVENTOS_DE_INTENCAO == {CANCELAMENTO, REBAIXAR} == set(rs.FIXED_RISK)
        assert SESSAO not in va.EVENTOS_DE_INTENCAO

    @pytest.mark.parametrize("evento", [CANCELAMENTO, REBAIXAR])
    @pytest.mark.parametrize("faixa", ["critico", "alto", "padrao"])
    @pytest.mark.parametrize("posicao", [None, 0.0, 0.5, 0.99])
    def test_intencao_explicita_intervem_sempre(self, evento, faixa, posicao):
        assert va.regra_de_intervencao(evento, faixa, posicao) == va.REGRA_INTENCAO_EXPLICITA

    @pytest.mark.parametrize("evento", [SESSAO, "Disparo em lote", "Evento Qualquer"])
    def test_nos_outros_eventos_decide_a_faixa_pela_posicao(self, evento):
        assert va.regra_de_intervencao(evento, "critico", 0.95) == va.REGRA_POSICAO_NA_BASE
        assert va.regra_de_intervencao(evento, "alto", 0.75) == va.REGRA_POSICAO_NA_BASE
        assert va.regra_de_intervencao(evento, "padrao", 0.95) == va.SEM_INTERVENCAO_FORA_DAS_FAIXAS
        assert va.regra_de_intervencao(evento, "padrao", 0.10) == va.SEM_INTERVENCAO_FORA_DAS_FAIXAS

    @pytest.mark.parametrize("faixa", ["critico", "alto", "padrao"])
    def test_sem_referencia_de_posicao_nao_intervem(self, faixa):
        assert va.regra_de_intervencao(SESSAO, faixa, None) == va.SEM_INTERVENCAO_SEM_REFERENCIA

    def test_so_duas_regras_intervem(self):
        assert va.REGRAS_QUE_INTERVEM == (va.REGRA_INTENCAO_EXPLICITA, va.REGRA_POSICAO_NA_BASE)
        assert va.SEM_INTERVENCAO_FORA_DAS_FAIXAS not in va.REGRAS_QUE_INTERVEM
        assert va.SEM_INTERVENCAO_SEM_REFERENCIA not in va.REGRAS_QUE_INTERVEM

    def test_a_faixa_define_a_intensidade(self):
        assert va.intensidade_da_faixa("critico") == va.INTENSIDADE_DO_BANDIT
        assert va.intensidade_da_faixa("alto") == va.INTENSIDADE_MAIS_LEVE
        assert va.intensidade_da_faixa("padrao") == va.INTENSIDADE_MAIS_LEVE

    def test_a_mais_leve_e_a_de_menor_custo_e_o_empate_fica_com_a_do_bandit(self):
        rodada = [{"offer": "pausa_1_mes", "custo": 500.0}, {"offer": "desconto_10", "custo": 150.0},
                  {"offer": "desconto_20", "custo": 300.0}]
        assert va.oferta_mais_leve(rodada)["offer"] == "desconto_10"
        empate = [{"offer": "a", "custo": 9.0}, {"offer": "b", "custo": 2.0}, {"offer": "c", "custo": 2.0}]
        assert va.oferta_mais_leve(empate)["offer"] == "b"

    def test_todo_valor_que_o_agente_grava_tem_frase_na_trilha(self):
        for regra in (*va.REGRAS_QUE_INTERVEM, va.SEM_INTERVENCAO_FORA_DAS_FAIXAS,
                      va.SEM_INTERVENCAO_SEM_REFERENCIA):
            assert regra in rl.ROTULOS_DE_REGRA_DE_INTERVENCAO, regra
        assert set(rl.ROTULOS_DE_INTENSIDADE) == {va.INTENSIDADE_DO_BANDIT, va.INTENSIDADE_MAIS_LEVE}
        assert "regra_de_intervencao" in rl.ROTULOS_DE_SAIDA and "intensidade" in rl.ROTULOS_DE_SAIDA


# == 1. Intencao explicita =================================================

class TestIntencaoExplicita:
    @pytest.mark.parametrize("evento", [CANCELAMENTO, REBAIXAR])
    def test_uso_saudavel_com_evento_de_intencao_recebe_oferta(self, cliente, evento):
        _instalar(REF_TODOS_ACIMA)                       # pela posicao, e o ultimo da base
        _evento(cliente, _chave(cliente), evento=evento, props=SAUDAVEL)
        linhas = _linhas()
        assert len(linhas) == 1 and len(_ofertas()) == 1
        linha = linhas[0]
        assert linha["offer_type"] in ob.OFFERS and linha["offer_sent"]
        # O risco do modelo continua calculado e gravado, e fica ABAIXO do corte antigo.
        assert linha["risk_score"] == rs.calculate_risk(evento, SAUDAVEL)
        assert 0 < linha["risk_score"] < va.CORTE_DE_INTERVENCAO

        risco = _decisoes(tipo=rl.TIPO_RISCO)
        assert len(risco) == 1 and risco[0]["modelo"] == rs.NOME_DO_MODELO
        assert risco[0]["saida"]["regra_de_intervencao"] == va.REGRA_INTENCAO_EXPLICITA
        assert risco[0]["saida"]["risk_score"] == round(linha["risk_score"], 4)
        assert risco[0]["saida"]["criticality"] == "padrao"
        assert risco[0]["contribuicoes"], "o TreeSHAP do modelo continua na trilha"
        assert "intenção explícita" in risco[0]["explicacao"]
        assert "qualquer que seja a pontuação de risco" in risco[0]["explicacao"]

        oferta = _decisoes(tipo=rl.TIPO_OFERTA)
        assert len(oferta) == 1
        assert oferta[0]["saida"]["regra_de_intervencao"] == va.REGRA_INTENCAO_EXPLICITA
        assert oferta[0]["saida"]["offer_type"] == linha["offer_type"]
        assert "intenção explícita" in oferta[0]["explicacao"]
        assert rl.verificar_cadeia(A)["integra"] is True

    def test_sem_referencia_de_posicao_a_intencao_ainda_intervem(self, cliente):
        _instalar()                                      # meta sem quantis, empresa sem base
        _evento(cliente, _chave(cliente), evento=CANCELAMENTO, props=SAUDAVEL)
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]
        assert risco["saida"]["posicao"]["na_base"] is None
        assert risco["saida"]["regra_de_intervencao"] == va.REGRA_INTENCAO_EXPLICITA
        assert len(_ofertas()) == 1

    def test_quem_nao_e_grave_leva_a_oferta_mais_leve(self, cliente):
        _instalar(REF_TODOS_ACIMA)
        _evento(cliente, _chave(cliente), evento=CANCELAMENTO, props=SAUDAVEL)
        saida = _decisoes(tipo=rl.TIPO_OFERTA)[0]["saida"]
        assert saida["intensidade"] == va.INTENSIDADE_MAIS_LEVE
        consideradas = [c["offer"] for c in saida["ofertas_consideradas"]]
        assert len(consideradas) == va.N_CANDIDATAS and saida["offer_type"] in consideradas
        assert _custo(saida["offer_type"]) == min(_custo(o) for o in consideradas)

    def test_grave_pela_posicao_leva_a_oferta_do_bandit(self, cliente):
        _instalar(REF_TODOS_ABAIXO)
        _evento(cliente, _chave(cliente), evento=CANCELAMENTO, props=ABANDONO)
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]["saida"]
        assert risco["criticality"] == "critico"
        assert risco["regra_de_intervencao"] == va.REGRA_INTENCAO_EXPLICITA, "a intencao vem primeiro"
        saida = _decisoes(tipo=rl.TIPO_OFERTA)[0]["saida"]
        assert saida["intensidade"] == va.INTENSIDADE_DO_BANDIT
        assert saida["offer_type"] == saida["ofertas_consideradas"][0]["offer"]

    def test_o_limite_de_30_dias_segura_a_oferta(self, cliente, producao, monkeypatch):
        _instalar(REF_TODOS_ACIMA)
        chave = _chave(cliente)
        _evento(cliente, chave, evento=CANCELAMENTO, messageId="e1")
        _evento(cliente, chave, evento=REBAIXAR, messageId="e2")
        assert len(_linhas()) == 2 and len(_ofertas()) == 1
        segunda = _linhas()[1]
        assert segunda["offer_type"] is None and not segunda["offer_sent"]
        do_limite = [d for d in _decisoes(tipo=rl.TIPO_OFERTA)
                     if d["saida"].get("regra") == va.REGRA_DO_LIMITE_DE_CONTATO]
        assert len(do_limite) == 1 and do_limite[0]["modelo"] == rl.MODELO_REGRA
        assert do_limite[0]["saida"]["regra_de_intervencao"] == va.REGRA_INTENCAO_EXPLICITA
        assert "permite uma oferta a cada 30 dias" in do_limite[0]["explicacao"]
        _adiantar(monkeypatch, days=29, hours=23)
        _evento(cliente, chave, evento=CANCELAMENTO, messageId="e3")
        assert len(_ofertas()) == 1, "29 dias e 23 horas: ainda dentro do intervalo"
        _adiantar(monkeypatch, days=30, minutes=1)
        _evento(cliente, chave, evento=CANCELAMENTO, messageId="e4")
        assert len(_ofertas()) == 2
        assert rl.verificar_cadeia(A)["integra"] is True

    def test_o_nao_contatar_segura_a_oferta(self, cliente):
        _instalar(REF_TODOS_ACIMA)
        ci.marcar_nao_contatar(A, "c-1", "empresa")
        _evento(cliente, _chave(cliente), evento=CANCELAMENTO, props=SAUDAVEL)
        assert len(_linhas()) == 1 and _ofertas() == []
        da_marca = [d for d in _decisoes(tipo=rl.TIPO_OFERTA)
                    if d["saida"].get("regra") == va.REGRA_DO_NAO_CONTATAR]
        assert len(da_marca) == 1
        assert da_marca[0]["saida"]["regra_de_intervencao"] == va.REGRA_INTENCAO_EXPLICITA
        assert va.FRASE_DO_NAO_CONTATAR in da_marca[0]["explicacao"]
        # O risco continua avaliado e gravado para quem tem a marca.
        assert _decisoes(tipo=rl.TIPO_RISCO)[0]["saida"]["risk_score"] > 0
        assert rl.verificar_cadeia(A)["integra"] is True


# == 2 e 3. Posicao na base, e a intensidade ===============================

class TestPosicaoNaBase:
    def test_sem_risco_com_evento_de_sessao_nao_recebe(self, cliente):
        _instalar(REF_TODOS_ACIMA)
        _evento(cliente, _chave(cliente), evento=SESSAO, props=SAUDAVEL)
        linhas = _linhas()
        assert len(linhas) == 1 and _ofertas() == []
        assert linhas[0]["offer_type"] is None and linhas[0]["risk_score"] > 0
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]
        assert risco["modelo"] == rs.NOME_DO_MODELO
        assert risco["saida"]["regra_de_intervencao"] == va.SEM_INTERVENCAO_FORA_DAS_FAIXAS
        assert "sem intervenção" in risco["explicacao"]
        assert _decisoes(tipo=rl.TIPO_OFERTA) == [] and _decisoes(tipo=rl.TIPO_CANAL) == []

    def test_grave_pela_posicao_recebe_a_oferta_do_bandit(self, cliente):
        _instalar(REF_TODOS_ABAIXO)
        _evento(cliente, _chave(cliente), evento=SESSAO, props=ABANDONO)
        linha = _linhas()[0]
        assert len(_ofertas()) == 1 and linha["offer_type"] in ob.OFFERS
        # O corte fixo nao entra: o risco do modelo esta abaixo de 0,60, e ha oferta.
        assert linha["risk_score"] < va.CORTE_DE_INTERVENCAO
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]
        assert risco["saida"]["criticality"] == "critico"
        assert risco["saida"]["posicao"]["na_base"] >= 0.90
        assert risco["saida"]["regra_de_intervencao"] == va.REGRA_POSICAO_NA_BASE
        assert "posição na base" in risco["explicacao"]
        oferta = _decisoes(tipo=rl.TIPO_OFERTA)[0]
        assert oferta["saida"]["regra_de_intervencao"] == va.REGRA_POSICAO_NA_BASE
        assert oferta["saida"]["intensidade"] == va.INTENSIDADE_DO_BANDIT
        assert oferta["saida"]["offer_type"] == oferta["saida"]["ofertas_consideradas"][0]["offer"]
        assert oferta["saida"]["offer_type"] == linha["offer_type"]
        assert "por ser um caso grave" in oferta["explicacao"]
        assert rl.verificar_cadeia(A)["integra"] is True

    def test_preocupante_recebe_a_oferta_mais_leve(self, cliente):
        _instalar()
        _instalar(_ref_que_deixa_preocupante(SESSAO, ABANDONO))
        _evento(cliente, _chave(cliente), evento=SESSAO, props=ABANDONO)
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]["saida"]
        assert risco["criticality"] == "alto" and 0.70 <= risco["posicao"]["na_base"] < 0.90
        assert risco["regra_de_intervencao"] == va.REGRA_POSICAO_NA_BASE
        oferta = _decisoes(tipo=rl.TIPO_OFERTA)[0]
        saida = oferta["saida"]
        assert saida["intensidade"] == va.INTENSIDADE_MAIS_LEVE
        consideradas = [c["offer"] for c in saida["ofertas_consideradas"]]
        assert _custo(saida["offer_type"]) == min(_custo(o) for o in consideradas)
        # A probabilidade gravada e a da oferta que saiu, e nao a da primeira do bandit.
        assert saida["p_estimado"] == next(c["p_estimado"] for c in saida["ofertas_consideradas"]
                                           if c["offer"] == saida["offer_type"])
        assert "menor custo entre as consideradas" in oferta["explicacao"]
        assert _linhas()[0]["offer_type"] == saida["offer_type"] and len(_ofertas()) == 1

    def test_no_topo_da_base_mas_sem_sinal_de_abandono_nao_recebe(self, cliente):
        _instalar(REF_TODOS_ABAIXO)
        _evento(cliente, _chave(cliente), evento=SESSAO, props=SAUDAVEL)
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]["saida"]
        assert risco["posicao"]["na_base"] >= 0.90 and risco["criticality"] == "padrao"
        assert risco["regra_de_intervencao"] == va.SEM_INTERVENCAO_FORA_DAS_FAIXAS
        assert _ofertas() == []

    def test_sem_referencia_de_posicao_nao_intervem(self, cliente):
        _instalar()                                      # meta sem quantis, empresa sem base
        _evento(cliente, _chave(cliente), evento=SESSAO, props=ABANDONO)
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]
        assert risco["saida"]["posicao"]["na_base"] is None
        assert risco["saida"]["regra_de_intervencao"] == va.SEM_INTERVENCAO_SEM_REFERENCIA
        assert "não há referência de posição" in risco["explicacao"]
        assert _ofertas() == [] and _linhas()[0]["offer_type"] is None

    def test_a_referencia_e_a_base_da_empresa_quando_ela_existe(self, cliente, monkeypatch):
        _instalar(REF_TODOS_ACIMA)                       # pelo meta, ninguem seria grave
        base = [{"customer_id_externo": f"b{i:03d}", "mrr": 400.0, "billing_profile": "PJ",
                 "days_since_last": float(i % 4), "features_used_30d": 9.0, "logins_30d": 30.0}
                for i in range(60)]
        monkeypatch.setattr(bs.clientes_importados, "listar", lambda t, **k: base)
        bs.pontuar_base(A)
        assert A in bs._referencia_do_score
        _evento(cliente, _chave(cliente), evento=SESSAO, props=ABANDONO)
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]["saida"]
        assert risco["posicao"]["referencia"] == bs.REGUA_BASE
        esperado = bs.criticidade_do_evento(A, _linhas()[0]["risk_score"], 500.0, 30, 0, True)
        assert risco["criticality"] == esperado["criticality"]
        intervem = esperado["criticality"] in ("critico", "alto")
        assert (risco["regra_de_intervencao"] == va.REGRA_POSICAO_NA_BASE) is intervem
        assert (len(_ofertas()) == 1) is intervem

    def test_o_limite_de_30_dias_segura_a_oferta(self, cliente, producao, monkeypatch):
        _instalar(REF_TODOS_ABAIXO)
        chave = _chave(cliente)
        _evento(cliente, chave, evento=SESSAO, props=ABANDONO, messageId="e1")
        _evento(cliente, chave, evento=SESSAO, props=ABANDONO, messageId="e2")
        assert len(_linhas()) == 2 and len(_ofertas()) == 1
        do_limite = [d for d in _decisoes(tipo=rl.TIPO_OFERTA)
                     if d["saida"].get("regra") == va.REGRA_DO_LIMITE_DE_CONTATO]
        assert len(do_limite) == 1
        assert do_limite[0]["saida"]["regra_de_intervencao"] == va.REGRA_POSICAO_NA_BASE
        _adiantar(monkeypatch, days=30, minutes=1)
        _evento(cliente, chave, evento=SESSAO, props=ABANDONO, messageId="e3")
        assert len(_ofertas()) == 2
        assert rl.verificar_cadeia(A)["integra"] is True

    def test_o_nao_contatar_segura_a_oferta(self, cliente):
        _instalar(REF_TODOS_ABAIXO)
        ci.marcar_nao_contatar(A, "c-1", "empresa")
        _evento(cliente, _chave(cliente), evento=SESSAO, props=ABANDONO)
        assert _ofertas() == []
        da_marca = [d for d in _decisoes(tipo=rl.TIPO_OFERTA)
                    if d["saida"].get("regra") == va.REGRA_DO_NAO_CONTATAR]
        assert len(da_marca) == 1
        assert da_marca[0]["saida"]["regra_de_intervencao"] == va.REGRA_POSICAO_NA_BASE
        # Outro cliente, sem a marca, recebe.
        _evento(cliente, _chave(cliente), user="c-2", evento=SESSAO, props=ABANDONO)
        assert len(_ofertas()) == 1
        assert rl.verificar_cadeia(A)["integra"] is True


# == 4. Quando a regua decide, nada muda ===================================

CHAVES_DE_RISCO_DA_REGUA = {"risk_score", "profile", "criticality", "regra", "motivo_da_regra"}
CHAVES_DE_OFERTA_DE_SEMPRE = {"offer_type", "p_estimado", "ofertas_consideradas"}

GRADE_DA_REGUA = [
    (CANCELAMENTO, {}), (REBAIXAR, {}), ("Evento Qualquer", {}), (SESSAO, {}),
    *[(SESSAO, {"days_since_last": d, "features_used_30d": f})
      for d in (0, 10, 17, 18, 19, 26, 30, 45) for f in (0, 3, 10)],
    (CANCELAMENTO, {"days_since_last": 1, "features_used_30d": 9}),
    (REBAIXAR, {"days_since_last": 40, "features_used_30d": 0}),
]


class TestQuandoAReguaDecide:
    def _conferir_caminho_antigo(self):
        linhas = _linhas()
        assert len(linhas) == len(GRADE_DA_REGUA)
        for linha in linhas:
            assert bool(linha["offer_type"]) is (linha["risk_score"] >= va.CORTE_DE_INTERVENCAO), linha
        # A trilha nao ganhou chave nenhuma, e a oferta e sempre a primeira do bandit.
        for d in _decisoes(tipo=rl.TIPO_RISCO):
            assert d["modelo"] == rl.MODELO_REGRA
            assert set(d["saida"]) <= CHAVES_DE_RISCO_DA_REGUA, d["saida"]
            assert "intervenção" not in d["explicacao"]
        for d in _decisoes(tipo=rl.TIPO_OFERTA):
            assert d["modelo"] == "offer_bandit"
            assert set(d["saida"]) == CHAVES_DE_OFERTA_DE_SEMPRE, d["saida"]
            assert d["saida"]["offer_type"] == d["saida"]["ofertas_consideradas"][0]["offer"]
            assert "intervenção" not in d["explicacao"] and "intensidade" not in d["explicacao"]
        assert len(_ofertas()) == len(_decisoes(tipo=rl.TIPO_OFERTA)) > 0
        assert any(not l["offer_type"] for l in linhas), "a grade tem os dois lados do corte"
        assert rl.verificar_cadeia(A)["integra"] is True

    def test_sem_modelo_o_corte_de_0_60_decide_em_toda_a_grade(self, cliente):
        assert rs.modelo_ativo() is False
        chave = _chave(cliente)
        for i, (evento, extra) in enumerate(GRADE_DA_REGUA):
            _evento(cliente, chave, user=f"g-{i}", evento=evento,
                    props={"mrr": 500.0, "billing_profile": "PJ", **extra})
        self._conferir_caminho_antigo()
        riscos = {l["user_id"]: l["risk_score"] for l in _linhas()}
        for i, (evento, extra) in enumerate(GRADE_DA_REGUA):
            esperado = rs._risco_por_regras(evento, {"mrr": 500.0, "billing_profile": "PJ", **extra})
            assert riscos[f"user:g-{i}"] == esperado

    def test_o_estado_do_pipeline_nao_ganha_regra_nem_intensidade(self):
        import asyncio
        final = asyncio.run(app_module._run_voluntary_pipeline(
            "user:r-1", CANCELAMENTO, {"mrr": 500.0, "billing_profile": "PJ"}, tenant_id=A))
        assert final["offer_type"] in ob.OFFERS and final["risk_score"] == 0.90
        assert final.get("regra_de_intervencao") is None
        assert final.get("intensidade_da_oferta") is None
        assert final["offer_type"] == final["ofertas_consideradas"][0]["offer"]
        vencedora = next(c for c in final["candidatas"] if c["escolhida"])
        assert vencedora["motivo_codigo"] == "maior_eprofit"
        assert {c["motivo_codigo"] for c in final["candidatas"] if not c["escolhida"]} == {
            "abaixo_da_escolhida"}

    def test_modelo_ativo_sem_dado_de_uso_e_a_regua_que_decide(self, cliente):
        _instalar(REF_TODOS_ACIMA)
        chave = _chave(cliente)
        sem_uso = {"mrr": 500.0, "billing_profile": "PJ"}
        _evento(cliente, chave, user="s-1", evento=CANCELAMENTO, props=sem_uso)
        _evento(cliente, chave, user="s-2", evento=REBAIXAR, props=sem_uso)
        _evento(cliente, chave, user="s-3", evento=SESSAO, props=sem_uso)
        riscos = [(l["risk_score"], bool(l["offer_type"])) for l in _linhas()]
        assert riscos == [(0.90, True), (0.75, True), (0.0, False)]
        for d in _decisoes(tipo=rl.TIPO_RISCO):
            assert d["modelo"] == rl.MODELO_REGRA
            assert d["saida"]["motivo_da_regra"] == rs.MOTIVO_SEM_DADO_PARA_O_MODELO
            assert "regra_de_intervencao" not in d["saida"]
        for d in _decisoes(tipo=rl.TIPO_OFERTA):
            assert set(d["saida"]) == CHAVES_DE_OFERTA_DE_SEMPRE
            assert d["saida"]["offer_type"] == d["saida"]["ofertas_consideradas"][0]["offer"]

    @pytest.mark.parametrize("risco_do_modelo, ha_oferta", [(0.30, False), (0.59, False),
                                                           (0.60, True), (0.85, True)])
    def test_modelo_legado_continua_com_o_corte_fixo(self, cliente, monkeypatch,
                                                     risco_do_modelo, ha_oferta):
        class Legado:
            def predict_proba(self, X):
                return [[1 - risco_do_modelo, risco_do_modelo]]
        monkeypatch.setattr(rs, "_modelo", Legado())
        monkeypatch.setattr(rs, "_modelo_consultado", True)
        assert rs.contrato_ativo() == rs.CONTRATO_LEGADO
        # Nem a pagina de cancelamento passa por cima do corte: a regra nova e so do v3.
        _evento(cliente, _chave(cliente), evento=CANCELAMENTO, props=SAUDAVEL)
        linha = _linhas()[0]
        assert linha["risk_score"] == risco_do_modelo and bool(linha["offer_type"]) is ha_oferta
        risco = _decisoes(tipo=rl.TIPO_RISCO)[0]
        assert risco["modelo"] == rs.NOME_DO_MODELO
        assert "regra_de_intervencao" not in risco["saida"]
        for d in _decisoes(tipo=rl.TIPO_OFERTA):
            assert set(d["saida"]) == CHAVES_DE_OFERTA_DE_SEMPRE


# == 5. A trilha ===========================================================

class TestTrilha:
    def test_a_cadeia_fica_integra_com_todos_os_casos_misturados(self, cliente, producao):
        chave = _chave(cliente)
        _evento(cliente, chave, user="t-1", evento=CANCELAMENTO,
                props={"mrr": 500.0, "billing_profile": "PJ"})          # regua, sem modelo
        _instalar(REF_TODOS_ABAIXO)
        _evento(cliente, chave, user="t-2", evento=CANCELAMENTO, props=SAUDAVEL)   # intencao
        _evento(cliente, chave, user="t-3", evento=SESSAO, props=ABANDONO)         # posicao, grave
        _evento(cliente, chave, user="t-4", evento=SESSAO, props=SAUDAVEL)         # sem intervencao
        ci.marcar_nao_contatar(A, "t-5", "empresa")
        _evento(cliente, chave, user="t-5", evento=REBAIXAR, props=SAUDAVEL)       # nao contatar
        _instalar()
        _evento(cliente, chave, user="t-6", evento=SESSAO, props=ABANDONO)         # sem referencia
        cadeia = rl.verificar_cadeia(A)
        assert cadeia["integra"] is True and cadeia["linhas"] == len(_decisoes())
        regras = [d["saida"].get("regra_de_intervencao") for d in _decisoes(tipo=rl.TIPO_RISCO)]
        assert regras == [None, va.REGRA_INTENCAO_EXPLICITA, va.REGRA_POSICAO_NA_BASE,
                          va.SEM_INTERVENCAO_FORA_DAS_FAIXAS, va.REGRA_INTENCAO_EXPLICITA,
                          va.SEM_INTERVENCAO_SEM_REFERENCIA]
        assert rl.decisoes_que_obstruem_cancelamento(A) == []

    def test_a_frase_e_em_portugues_sem_chave_crua(self, cliente):
        _instalar(REF_TODOS_ABAIXO)
        chave = _chave(cliente)
        _evento(cliente, chave, user="f-1", evento=CANCELAMENTO, props=SAUDAVEL)
        _evento(cliente, chave, user="f-2", evento=SESSAO, props=ABANDONO)
        _evento(cliente, chave, user="f-3", evento=SESSAO, props=SAUDAVEL)
        for d in _decisoes():
            for cru in ("regra_de_intervencao", "intencao_explicita", "posicao_na_base",
                        "fora_das_faixas", "oferta_mais_leve", "oferta_do_bandit",
                        "intensidade ="):
                assert cru not in d["explicacao"], (cru, d["explicacao"])
            assert " = " not in d["explicacao"], d["explicacao"]

    def test_a_explicacao_do_titular_diz_a_regra(self, cliente):
        _instalar(REF_TODOS_ACIMA)
        _evento(cliente, _chave(cliente), user="c-9", evento=CANCELAMENTO, props=SAUDAVEL)
        r = cliente.get("/titular/explicacao/user:c-9", headers=_login(cliente))
        assert r.status_code == 200, r.text
        texto = json.dumps(r.json(), ensure_ascii=False)
        assert "intenção explícita" in texto

    def test_as_candidatas_do_painel_dizem_por_que_a_mais_leve_venceu(self):
        import asyncio
        _instalar(REF_TODOS_ACIMA)
        final = asyncio.run(app_module._run_voluntary_pipeline(
            "user:p-1", CANCELAMENTO, dict(SAUDAVEL), tenant_id=A))
        assert final["regra_de_intervencao"] == va.REGRA_INTENCAO_EXPLICITA
        assert final["intensidade_da_oferta"] == va.INTENSIDADE_MAIS_LEVE
        candidatas = final["candidatas"]
        assert len(candidatas) == va.N_CANDIDATAS
        vencedora = [c for c in candidatas if c["escolhida"]]
        assert len(vencedora) == 1 and vencedora[0]["oferta"] == final["offer_type"]
        assert vencedora[0]["motivo_codigo"] == "menor_custo"
        assert "menor custo" in vencedora[0]["motivo"]
        for c in candidatas:
            if not c["escolhida"]:
                assert c["motivo_codigo"] == "custo_maior_que_a_escolhida"
                assert _custo(c["oferta"]) >= _custo(final["offer_type"])


# == A simulacao do gateway (a resposta diz a regra) =======================

PROPENSAO_TOTAL = {o: 1.0 for o in ob.OFFERS}


class TestRetencaoSimulada:
    def _simular(self, c, nome="Loja Ponto Certo", **sinais):
        corpo = {"nome": nome, "mrr": 1200.0, "propensao": PROPENSAO_TOTAL,
                 "sinais": {"uso_caiu": False, "tickets": False, "atraso": False,
                            "abriu_cancelamento": False, **sinais}}
        r = c.post("/simulacao/retencao", json=corpo, headers=_login(c))
        assert r.status_code == 200, r.text
        return r.json()

    def test_com_o_modelo_a_pagina_de_cancelamento_gera_oferta(self, cliente):
        _instalar(REF_TODOS_ACIMA)
        r = self._simular(cliente, abriu_cancelamento=True, uso_caiu=True)
        assert r["decidido_por"] == "modelo" and r["oferta"] in ob.OFFERS and r["aceitou"] is True
        assert r["regra_de_intervencao"] == va.REGRA_INTENCAO_EXPLICITA
        assert r["corte_de_intervencao"] is None and r["sem_oferta_porque"] is None
        assert r["risco"] < va.CORTE_DE_INTERVENCAO
        assert r["porque"].startswith("O cliente mostrou intenção explícita de sair.")

    def test_com_o_modelo_grave_pela_posicao_gera_oferta(self, cliente):
        _instalar(REF_TODOS_ABAIXO)
        r = self._simular(cliente, uso_caiu=True)
        assert r["decidido_por"] == "modelo" and r["faixa"] == "grave"
        assert r["regra_de_intervencao"] == va.REGRA_POSICAO_NA_BASE
        assert r["intensidade"] == va.INTENSIDADE_DO_BANDIT and r["oferta"] in ob.OFFERS
        assert r["porque"].startswith("Pela posição na base")
        assert "maior retorno esperado" in r["porque"]
        # O motivo fala da posicao pelo modelo, e nao repete a frase da regua.
        assert "de maior risco da sua base, pelo modelo" in r["motivo"]
        assert "pelo valor da conta" not in r["motivo"]

    def test_com_o_modelo_fora_das_faixas_nao_ha_oferta_e_a_resposta_diz_por_que(self, cliente):
        _instalar(REF_TODOS_ACIMA)
        r = self._simular(cliente, uso_caiu=True)
        assert r["decidido_por"] == "modelo" and r["faixa"] == "sem_risco"
        assert r["oferta"] is None and r["aceitou"] is None and r["porque"] is None
        assert r["regra_de_intervencao"] == va.SEM_INTERVENCAO_FORA_DAS_FAIXAS
        assert r["corte_de_intervencao"] is None, "o corte fixo nao explica nada aqui"
        assert r["sem_oferta_porque"].startswith("Quem decidiu o risco foi o modelo de IA.")
        assert "fora dos 30% de maior risco da sua base, pelo modelo" in r["motivo"]

    def test_com_a_regua_a_resposta_e_a_de_antes(self, cliente):
        r = self._simular(cliente, abriu_cancelamento=True)
        assert r["decidido_por"] == "regua" and r["risco"] == 0.9 and r["oferta"] in ob.OFFERS
        assert r["corte_de_intervencao"] == va.CORTE_DE_INTERVENCAO
        assert r["regra_de_intervencao"] is None and r["intensidade"] is None
        assert r["sem_oferta_porque"] is None
        assert r["porque"].startswith("O sistema sorteia a partir do que já aprendeu")
        sem = self._simular(cliente, nome="Loja Dois")
        assert sem["oferta"] is None and sem["corte_de_intervencao"] == va.CORTE_DE_INTERVENCAO
        assert sem["regra_de_intervencao"] is None and sem["sem_oferta_porque"] is None

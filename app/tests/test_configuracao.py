"""tests/test_configuracao.py — Etapa 2, Bloco 4: a configuração pela rota.

O QUE ESTE ARQUIVO MEDE (Portão Final: "configuração de uma empresa muda o
comportamento só dela"):
  - `GET /configuracao`: a configuração efetiva, sem chave interna, para
    qualquer papel; `PUT /configuracao`: só `owner` e `admin`;
  - o PUT é parcial, devolve o conjunto inteiro, e recusa com 422 — sem gravar
    nada — chave desconhecida, chave interna, valor fora da faixa e conjunto
    inválido;
  - o tenant vem do token: o PUT da empresa A não muda a leitura da B;
  - o FLUXO lê a configuração da empresa: com A no automático e B no padrão
    (escolha), as mesmas 3 falhas mandam a mensagem de A e deixam a de B
    esperando; o prazo de escolha gravado por A muda o `escolha_ate` só dela;
  - a trilha do Art. 20 continua íntegra depois disso.

As fixtures e os ajudantes do fluxo (webhook assinado, relógio congelado,
agendador) são os de `test_mensagens_involuntario.py`, importados daqui para o
fluxo medido ser exatamente o do Bloco 3.
"""

from datetime import datetime, timedelta

import pytest

from crai.churn_voluntary import retention_log as trilha
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao
from tests.test_mensagens_involuntario import (  # noqa: F401 — fixtures usadas por nome
    A, B, EMAIL, FUSO, TELEFONE, _base, _falhas, _get,
    cliente, envios, producao, relogio, sem_llm,
)


def _put(c, corpo, papel="admin", tenant=A):
    cabecalho = c.projeto.bearer(tenant, papel=papel) if papel else c.projeto.bearer(tenant)
    return c.put("/configuracao", json=corpo, headers=cabecalho)


def _ler(c, tenant=A, papel=None):
    cabecalho = c.projeto.bearer(tenant, papel=papel) if papel else c.projeto.bearer(tenant)
    r = c.get("/configuracao", headers=cabecalho)
    assert r.status_code == 200, r.text
    return r.json()


# ══════════════════════════════════════════════════════════════════════════
# As duas rotas
# ══════════════════════════════════════════════════════════════════════════

class TestLeitura:

    def test_sem_nada_gravado_devolve_os_padroes_de_producao(self, cliente, producao):
        corpo = _ler(cliente)
        assert corpo["configuracao"] == configuracao.publica(dict(configuracao.PADROES_DE_PRODUCAO))
        assert corpo["configuracao"]["modo_mensagem_involuntario"] == "escolha"
        assert corpo["configuracao"]["prazo_escolha_horas"] == 8
        assert (corpo["configuracao"]["janela_contato_inicio"],
                corpo["configuracao"]["janela_contato_fim"]) == ("08:00", "20:00")
        assert corpo["configuracao"]["canais_permitidos"] == ["whatsapp", "email"]
        assert corpo["configuracao"]["retencao_mensagens_dias"] == 90
        assert corpo["configuracao"]["retencao_trilha_anos"] == 5

    def test_a_chave_interna_nunca_sai(self, cliente):
        # A suíte roda com `canal_presumido` ligado (conftest): mesmo assim não sai.
        assert configuracao.ler(A)["canal_presumido"] == "whatsapp"
        assert "canal_presumido" not in _ler(cliente)["configuracao"]

    @pytest.mark.parametrize("papel, pode", [("owner", True), ("admin", True),
                                             ("membro", False), (None, False)])
    def test_qualquer_papel_le_e_a_resposta_diz_se_pode_editar(self, cliente, papel, pode):
        assert _ler(cliente, papel=papel)["pode_editar"] is pode

    def test_sem_token_e_401_nas_duas(self, cliente):
        assert cliente.get("/configuracao").status_code == 401
        assert cliente.put("/configuracao", json={"prazo_escolha_horas": 4}).status_code == 401
        assert configuracao._gravada(A) == {}

    def test_papel_inventado_e_401(self, cliente):
        r = cliente.get("/configuracao", headers=cliente.projeto.bearer(A, papel="dono"))
        assert r.status_code == 401 and r.json()["detail"]["motivo"] == "papel_invalido"


class TestGravacao:

    @pytest.mark.parametrize("papel", ["owner", "admin"])
    def test_owner_e_admin_gravam(self, cliente, producao, papel):
        r = _put(cliente, {"modo_mensagem_involuntario": "automatico"}, papel=papel)
        assert r.status_code == 200, r.text
        assert r.json()["configuracao"]["modo_mensagem_involuntario"] == "automatico"
        assert _ler(cliente)["configuracao"]["modo_mensagem_involuntario"] == "automatico"

    @pytest.mark.parametrize("papel", ["membro", None])
    def test_membro_e_token_sem_papel_recebem_403_e_nada_e_gravado(self, cliente, papel):
        r = _put(cliente, {"modo_mensagem_involuntario": "automatico"}, papel=papel)
        assert r.status_code == 403
        assert r.json()["detail"]["motivo"] == "papel_insuficiente"
        assert configuracao._gravada(A) == {}

    def test_o_put_e_parcial_e_devolve_o_conjunto_inteiro(self, cliente, producao):
        assert _put(cliente, {"prazo_escolha_horas": 4}).status_code == 200
        r = _put(cliente, {"janela_contato_inicio": "09:00", "janela_contato_fim": "18:30",
                           "canais_permitidos": ["email", "whatsapp"]})
        assert r.status_code == 200, r.text
        esperado = configuracao.publica(dict(configuracao.PADROES_DE_PRODUCAO))
        esperado.update(prazo_escolha_horas=4, janela_contato_inicio="09:00",
                        janela_contato_fim="18:30", canais_permitidos=["email", "whatsapp"])
        assert r.json()["configuracao"] == esperado
        assert _ler(cliente)["configuracao"] == esperado
        # No banco fica só o que a empresa gravou: os padrões continuam padrões.
        assert set(configuracao._gravada(A)) == {
            "prazo_escolha_horas", "janela_contato_inicio", "janela_contato_fim",
            "canais_permitidos"}

    def test_grava_quem_foi_pelo_papel_nunca_pelo_nome(self, cliente):
        assert _put(cliente, {"prazo_escolha_horas": 4}, papel="owner").status_code == 200
        conn = configuracao._conectar()
        try:
            linha = dict(conn.execute("SELECT * FROM configuracao_tenant WHERE tenant_id = ?",
                                      (A,)).fetchone())
        finally:
            conn.close()
        assert linha["atualizada_por_papel"] == "owner"
        assert set(linha) == {"tenant_id", "valores", "atualizada_em", "atualizada_por_papel"}
        assert "@" not in str(linha)

    @pytest.mark.parametrize("corpo, campo", [
        ({"chave_que_nao_existe": 1}, "chave_que_nao_existe"),
        ({"canal_presumido": "whatsapp"}, "canal_presumido"),
        ({"modo_mensagem_involuntario": "manual"}, "modo_mensagem_involuntario"),
        ({"prazo_escolha_horas": 0}, "prazo_escolha_horas"),
        ({"prazo_escolha_horas": 73}, "prazo_escolha_horas"),
        ({"prazo_escolha_horas": "8"}, "prazo_escolha_horas"),
        ({"prazo_escolha_horas": True}, "prazo_escolha_horas"),
        ({"janela_contato_inicio": "8h"}, "janela_contato_inicio"),
        ({"janela_contato_fim": "00:00"}, "janela_contato_fim"),
        ({"janela_contato_inicio": "20:00", "janela_contato_fim": "08:00"}, "janela_contato_fim"),
        ({"janela_contato_inicio": "21:00"}, "janela_contato_fim"),
        ({"canais_permitidos": []}, "canais_permitidos"),
        ({"canais_permitidos": ["whatsapp", "whatsapp"]}, "canais_permitidos"),
        ({"canais_permitidos": ["sms"]}, "canais_permitidos"),
        ({"canais_permitidos": "whatsapp"}, "canais_permitidos"),
        ({"retencao_mensagens_dias": 0}, "retencao_mensagens_dias"),
        ({"retencao_trilha_anos": 0}, "retencao_trilha_anos"),
        ({"posicao_grave_pct": 30}, "posicao_preocupante_pct"),
    ])
    def test_valor_invalido_e_422_com_o_campo_e_nada_e_gravado(self, cliente, producao,
                                                               corpo, campo):
        # Uma chave válida junto com a inválida: nem ela é gravada.
        r = _put(cliente, {"retencao_ciclos_meses": 36, **corpo})
        assert r.status_code == 422, r.text
        detalhe = r.json()["detail"]
        assert detalhe["motivo"] == "configuracao_invalida" and detalhe["campo"] == campo
        assert configuracao._gravada(A) == {}

    @pytest.mark.parametrize("corpo", [[], "automatico", 3, None])
    def test_corpo_que_nao_e_objeto_e_422(self, cliente, corpo):
        r = _put(cliente, corpo)
        assert r.status_code == 422, r.text
        assert configuracao._gravada(A) == {}


# ══════════════════════════════════════════════════════════════════════════
# A configuração de uma empresa muda só ela
# ══════════════════════════════════════════════════════════════════════════

class TestSoADela:

    def test_o_put_de_uma_empresa_nao_muda_a_leitura_da_outra(self, cliente, producao):
        antes_b = _ler(cliente, tenant=B)
        r = _put(cliente, {"modo_mensagem_involuntario": "automatico", "prazo_escolha_horas": 2,
                           "janela_contato_inicio": "10:00", "janela_contato_fim": "12:00",
                           "canais_permitidos": ["email"], "retencao_mensagens_dias": 30})
        assert r.status_code == 200, r.text
        assert _ler(cliente, tenant=B) == antes_b
        assert configuracao._gravada(B) == {}
        assert _ler(cliente)["configuracao"]["canais_permitidos"] == ["email"]

    def test_o_fluxo_segue_o_modo_de_cada_empresa(self, cliente, relogio, envios, producao):
        """A mesma sequência (falha + 3 tentativas falhadas) nas duas empresas.
        A gravou `automatico` pela rota; B ficou no padrão (`escolha`)."""
        assert _put(cliente, {"modo_mensagem_involuntario": "automatico"}).status_code == 200
        _base(A, "RN_cfg_a", telefone=TELEFONE)
        _base(B, "RN_cfg_b", telefone=TELEFONE)

        ciclo_a, _ = _falhas(cliente, relogio, "RN_cfg_a", tenant=A)
        relogio(datetime(2026, 9, 3, 9, 0))
        ciclo_b, _ = _falhas(cliente, relogio, "RN_cfg_b", tenant=B)

        assert ciclo_a["estado"] == cc.MENSAGEM_ENVIADA
        assert ciclo_b["estado"] == cc.AGUARDANDO_ESCOLHA
        assert [e["customer_id"] for e in envios] == ["RN_cfg_a"]
        escolhida = cc.mensagem_escolhida(ciclo_a["id"])
        assert escolhida["escolhida_por"] == "automatico"
        assert cc.mensagem_escolhida(ciclo_b["id"]) is None
        assert _get(cliente, f"/ciclos/{ciclo_a['id']}").json()["modo_mensagem"] == "automatico"
        assert _get(cliente, f"/ciclos/{ciclo_b['id']}",
                    tenant=B).json()["modo_mensagem"] == "escolha"
        assert trilha.verificar_cadeia(A)["integra"] is True
        assert trilha.verificar_cadeia(B)["integra"] is True

    def test_o_prazo_de_escolha_de_uma_empresa_vale_so_para_ela(self, cliente, relogio, envios,
                                                               producao):
        assert _put(cliente, {"prazo_escolha_horas": 2}).status_code == 200
        _base(A, "RN_prazo_a", email=EMAIL)
        _base(B, "RN_prazo_b", email=EMAIL)
        ciclo_a, ultima_a = _falhas(cliente, relogio, "RN_prazo_a", tenant=A)
        relogio(datetime(2026, 9, 3, 9, 0))
        ciclo_b, ultima_b = _falhas(cliente, relogio, "RN_prazo_b", tenant=B)
        assert ciclo_a["estado"] == ciclo_b["estado"] == cc.AGUARDANDO_ESCOLHA

        ate_a = _get(cliente, f"/ciclos/{ciclo_a['id']}").json()["escolha_ate"]
        ate_b = _get(cliente, f"/ciclos/{ciclo_b['id']}", tenant=B).json()["escolha_ate"]
        assert datetime.fromisoformat(ate_a) == (ultima_a + timedelta(hours=2)).replace(tzinfo=FUSO)
        assert datetime.fromisoformat(ate_b) == (ultima_b + timedelta(hours=8)).replace(tzinfo=FUSO)

    def test_a_configuracao_fixa_do_painel_nao_e_alterada_pela_rota(self, cliente):
        """O tenant do painel de avaliação tem configuração fixa em código: um
        PUT com o token dele grava a linha, mas a efetiva continua a fixa."""
        from crai.api.app import TENANT_PAINEL
        r = _put(cliente, {"modo_mensagem_involuntario": "escolha"}, tenant=TENANT_PAINEL)
        assert r.status_code == 200, r.text
        assert r.json()["configuracao"]["modo_mensagem_involuntario"] == "automatico"

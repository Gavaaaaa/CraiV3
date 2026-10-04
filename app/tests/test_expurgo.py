"""tests/test_expurgo.py — Etapa 2, Bloco 4: o expurgo diário e o registro de acesso.

O QUE ESTE ARQUIVO MEDE (Portão Final: "o expurgo apaga o texto de mensagem com
mais de 90 dias depois do desfecho e mantém a abordagem; não toca no que está
dentro do prazo"):
  - o texto das mensagens: o limite exato (um minuto antes, um minuto depois),
    a abordagem que fica, o ciclo aberto que nunca é tocado, a idempotência, o
    prazo de cada empresa (`retencao_mensagens_dias`), e o que a rota mostra
    depois;
  - a passagem do relógio: o expurgo roda uma vez por dia, registra no log
    quantas linhas tocou, aparece no `/health`, e uma falha dele aparece (não
    some em silêncio) e é tentada de novo;
  - o registro de acesso: as três rotas gravam `tenant`, `rota` (o modelo, não
    o id), `papel` e `quando`, e mais nada; 404 não grava; o registro é da
    empresa; uma falha ao registrar não derruba a leitura; 12 meses de
    retenção;
  - a retenção da trilha com o prazo recebido (D-E2-2): com 5 anos, a decisão
    de 3 anos fica e a de 6 sai, e a cadeia continua verificável.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest

from crai.api import registro_acesso, relogio as relogio_module
from crai.churn_voluntary import retention_log as trilha
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao
from tests.test_mensagens_involuntario import (  # noqa: F401 — fixtures usadas por nome
    A, B, EMAIL, NOME, _base, _falhas, _get,
    cliente, envios, producao, relogio, sem_llm,
)

ABERTURA = datetime(2026, 1, 5, 9, 0)
DESFECHO = datetime(2026, 1, 10, 9, 0)
NOVENTA_DIAS = DESFECHO + timedelta(days=90)
MINUTO = timedelta(minutes=1)


# ── Montagem ──────────────────────────────────────────────────────────────

def _ciclo_com_mensagens(tenant, rec, aberto_em=ABERTURA) -> int:
    """Um ciclo em `aguardando_escolha` com uma rodada de 3 sugestões."""
    ciclo = cc.abrir_ciclo(tenant, rec, 100.0, "insufficient_funds", aberto_em,
                           id_cobranca=f"cob_{rec}")
    cc.transicionar(ciclo["id"], cc.AGUARDANDO_ESCOLHA, aberto_em)
    sugestoes = [{"abordagem": a, "texto": f"Texto {a} para {rec}. Esta é uma mensagem automática.",
                  "recomendada": a == cc.ABORDAGENS[0], "origem_texto": "template",
                  "codigo_template": f"{a}.insufficient_funds"} for a in cc.ABORDAGENS]
    assert cc.gravar_rodada(ciclo["id"], sugestoes, "whatsapp", "contato_da_base",
                            "insufficient_funds", "pix", "cordial", aberto_em) == 1
    return ciclo["id"]


def _recuperado(tenant, rec, em=DESFECHO) -> int:
    ciclo_id = _ciclo_com_mensagens(tenant, rec)
    cc.fechar_como_recuperado(ciclo_id, fee=20.0, agora=em)
    return ciclo_id


def _perdido(tenant, rec, em=DESFECHO) -> int:
    ciclo_id = _ciclo_com_mensagens(tenant, rec)
    cc.transicionar(ciclo_id, cc.PERDIDO, em)
    return ciclo_id


def _textos(ciclo_id) -> list:
    return [m["texto"] for m in cc.mensagens_do_ciclo(ciclo_id)]


@pytest.fixture(autouse=True)
def expurgo_do_dia_esquecido():
    """O dia do último expurgo é estado do processo do relógio."""
    relogio_module._estado["ultimo_expurgo_em"] = None
    yield
    relogio_module._estado["ultimo_expurgo_em"] = None


def _dec(tenant, sujeito, decidido_em):
    return trilha.decisao(tenant, sujeito, dominio="involuntario", tipo_decisao="risco",
                          modelo="regra", entradas={"amount": 100.0},
                          saida={"recovery_score": 50, "regra": "x"}, decidido_em=decidido_em)


# ══════════════════════════════════════════════════════════════════════════
# O texto das mensagens
# ══════════════════════════════════════════════════════════════════════════

class TestTextoDasMensagens:

    def test_um_minuto_antes_dos_90_dias_nada_e_tocado(self):
        ciclo_id = _recuperado(A, "RN_antes")
        antes = cc.mensagens_do_ciclo(ciclo_id)
        assert cc.apagar_texto_expirado(NOVENTA_DIAS - MINUTO, A, 90) == 0
        assert cc.apagar_texto_expirado(NOVENTA_DIAS, A, 90) == 0     # "mais de" 90 dias
        assert cc.mensagens_do_ciclo(ciclo_id) == antes
        assert all(t and "RN_antes" in t for t in _textos(ciclo_id))

    @pytest.mark.parametrize("fechar", [_recuperado, _perdido])
    def test_depois_dos_90_dias_o_texto_sai_e_a_abordagem_fica(self, fechar):
        ciclo_id = fechar(A, "RN_depois")
        antes = cc.mensagens_do_ciclo(ciclo_id)
        agora = NOVENTA_DIAS + MINUTO
        assert cc.apagar_texto_expirado(agora, A, 90) == 3
        depois = cc.mensagens_do_ciclo(ciclo_id)
        assert [m["texto"] for m in depois] == [None, None, None]
        assert all(m["texto_apagado_em"] == agora.isoformat() for m in depois)
        # Todo o resto da linha fica: abordagem, canal, recomendada, datas.
        ignorar = {"texto", "texto_apagado_em"}
        assert [{k: v for k, v in m.items() if k not in ignorar} for m in depois] == \
               [{k: v for k, v in m.items() if k not in ignorar} for m in antes]
        assert [m["abordagem"] for m in depois] == list(cc.ABORDAGENS)

    def test_ciclo_sem_desfecho_nunca_e_tocado(self):
        aguardando = _ciclo_com_mensagens(A, "RN_aberto")
        enviado = _ciclo_com_mensagens(A, "RN_enviado")
        cc.registrar_escolha(enviado, 1, cc.ABORDAGENS[0], "owner", ABERTURA)
        cc.transicionar(enviado, cc.MENSAGEM_ENVIADA, ABERTURA)
        assert cc.apagar_texto_expirado(ABERTURA + timedelta(days=3650), A, 90) == 0
        assert all(_textos(aguardando)) and all(_textos(enviado))

    def test_e_idempotente(self):
        ciclo_id = _recuperado(A, "RN_duas_vezes")
        primeira = NOVENTA_DIAS + MINUTO
        assert cc.apagar_texto_expirado(primeira, A, 90) == 3
        assert cc.apagar_texto_expirado(primeira + timedelta(days=5), A, 90) == 0
        assert {m["texto_apagado_em"] for m in cc.mensagens_do_ciclo(ciclo_id)} == {
            primeira.isoformat()}

    def test_so_a_empresa_pedida(self):
        de_a, de_b = _recuperado(A, "RN_a"), _recuperado(B, "RN_b")
        assert cc.apagar_texto_expirado(NOVENTA_DIAS + MINUTO, A, 90) == 3
        assert _textos(de_a) == [None] * 3 and all(_textos(de_b))

    def test_a_rota_mostra_a_abordagem_e_o_texto_nulo(self, cliente):
        ciclo_id = _recuperado(A, "RN_rota")
        assert cc.apagar_texto_expirado(NOVENTA_DIAS + MINUTO, A, 90) == 3
        mensagens = _get(cliente, f"/ciclos/{ciclo_id}").json()["mensagens"]
        assert [m["abordagem"] for m in mensagens] == list(cc.ABORDAGENS)
        assert [m["texto"] for m in mensagens] == [None, None, None]
        assert [m["canal"] for m in mensagens] == ["whatsapp"] * 3


# ══════════════════════════════════════════════════════════════════════════
# A passagem diária do relógio
# ══════════════════════════════════════════════════════════════════════════

class TestPassagemDiaria:

    def test_o_expurgo_usa_o_prazo_de_cada_empresa(self, producao):
        configuracao.gravar(A, {"retencao_mensagens_dias": 30})
        de_a, de_b = _recuperado(A, "RN_30"), _recuperado(B, "RN_90")
        resultado = relogio_module.expurgar(DESFECHO + timedelta(days=40))
        assert resultado["textos_de_mensagem_apagados"] == 3
        assert _textos(de_a) == [None] * 3 and all(_textos(de_b))
        resultado = relogio_module.expurgar(NOVENTA_DIAS + MINUTO)
        assert resultado["textos_de_mensagem_apagados"] == 3
        assert _textos(de_b) == [None] * 3

    def test_roda_uma_vez_por_dia_e_diz_no_log_quantas_linhas_tocou(self, producao, caplog):
        _recuperado(A, "RN_log")
        registro_acesso.registrar(A, registro_acesso.ROTA_CICLOS, "owner",
                                  NOVENTA_DIAS - timedelta(days=400))
        agora = NOVENTA_DIAS + MINUTO
        with caplog.at_level(logging.INFO, logger=relogio_module.__name__):
            primeira = asyncio.run(relogio_module.passagem(agora))
            segunda = asyncio.run(relogio_module.passagem(agora + timedelta(hours=3)))
            terceira = asyncio.run(relogio_module.passagem(agora + timedelta(days=1)))
        assert primeira["expurgo"] == {"textos_de_mensagem_apagados": 3,
                                       "registros_de_acesso_apagados": 1}
        assert "expurgo" not in segunda                      # mesmo dia
        assert terceira["expurgo"] == {"textos_de_mensagem_apagados": 0,
                                       "registros_de_acesso_apagados": 0}
        linhas = [r.getMessage() for r in caplog.records if "[EXPURGO]" in r.getMessage()]
        assert len(linhas) == 2
        assert "3 texto(s) de mensagem" in linhas[0] and "1 registro(s) de acesso" in linhas[0]
        assert "0 texto(s) de mensagem" in linhas[1]
        saude = relogio_module.estado_para_health()
        assert saude["ultimo_expurgo_em"] == (agora + timedelta(days=1)).date().isoformat()

    def test_falha_do_expurgo_nao_some_e_e_tentada_de_novo(self, monkeypatch):
        ciclo_id = _recuperado(A, "RN_falha")
        original = cc.apagar_texto_expirado

        def quebrado(*a, **k):
            raise RuntimeError("disco cheio (teste)")
        monkeypatch.setattr(cc, "apagar_texto_expirado", quebrado)
        agora = NOVENTA_DIAS + MINUTO
        with pytest.raises(RuntimeError):
            asyncio.run(relogio_module.passagem(agora))
        assert relogio_module.estado_para_health()["ultimo_expurgo_em"] is None
        assert all(_textos(ciclo_id))

        monkeypatch.setattr(cc, "apagar_texto_expirado", original)
        resultado = asyncio.run(relogio_module.passagem(agora + MINUTO))
        assert resultado["expurgo"]["textos_de_mensagem_apagados"] == 3

    def test_o_expurgo_nao_toca_ciclo_nem_tentativa(self):
        ciclo_id = _recuperado(A, "RN_intacto")
        ciclo_antes = cc.ciclo_por_id(ciclo_id)
        relogio_module.expurgar(NOVENTA_DIAS + MINUTO)
        assert cc.ciclo_por_id(ciclo_id) == ciclo_antes


# ══════════════════════════════════════════════════════════════════════════
# O registro de acesso
# ══════════════════════════════════════════════════════════════════════════

class TestRegistroDeAcesso:

    def test_as_tres_rotas_registram_tenant_rota_papel_e_quando(self, cliente, relogio):
        ciclo_id = _recuperado(A, "RN_acesso")
        trilha.registrar_decisoes([_dec(A, "RN_acesso", "2026-09-01T12:00:00+00:00")])
        membro = cliente.projeto.bearer(A, papel="membro")

        assert cliente.get("/ciclos", headers=membro).status_code == 200
        assert cliente.get(f"/ciclos/{ciclo_id}",
                           headers=cliente.projeto.bearer(A, papel="owner")).status_code == 200
        assert cliente.get("/titular/explicacao/RN_acesso",
                           headers=cliente.projeto.bearer(A)).status_code == 200

        linhas = registro_acesso.acessos(A)
        assert sorted((l["rota"], l["papel"]) for l in linhas) == sorted([
            ("GET /ciclos", "membro"),
            ("GET /ciclos/{ciclo_id}", "owner"),
            ("GET /titular/explicacao/{sujeito_id}", None)])
        assert {l["tenant_id"] for l in linhas} == {A}
        assert {l["quando"] for l in linhas} == {relogio.agora().isoformat(timespec="seconds")}
        assert registro_acesso.acessos(B) == []

    def test_a_linha_nao_tem_quem_nem_qual_titular(self, cliente):
        """Só tenant, rota, papel e quando: nem o id pedido, nem o e-mail ou o
        `sub` de quem pediu, nem o nome do cliente."""
        _base(A, "RN_sigilo", email=EMAIL, nome=NOME)
        ciclo_id = _recuperado(A, "RN_sigilo")
        trilha.registrar_decisoes([_dec(A, "RN_sigilo", "2026-09-01T12:00:00+00:00")])
        cabecalho = cliente.projeto.bearer(A, papel="admin", email="pessoa.logada@empresa.com.br")
        assert cliente.get("/ciclos", headers=cabecalho).json()["ciclos"][0]["cliente_nome"] == NOME
        assert cliente.get(f"/ciclos/{ciclo_id}", headers=cabecalho).status_code == 200
        assert cliente.get("/titular/explicacao/RN_sigilo", headers=cabecalho).status_code == 200

        conn = registro_acesso._conectar()
        try:
            colunas = [c[1] for c in conn.execute("PRAGMA table_info(acessos_titular)")]
            cru = [tuple(l) for l in conn.execute("SELECT * FROM acessos_titular")]
        finally:
            conn.close()
        assert colunas == ["id", "tenant_id", "rota", "papel", "quando"]
        assert len(cru) == 3
        texto = repr(cru)
        for proibido in ("RN_sigilo", str(ciclo_id) + "'", "pessoa.logada", "@", NOME,
                         "Mariana", "0f1e2d3c"):
            assert proibido not in texto, proibido

    def test_404_e_401_nao_registram(self, cliente):
        de_b = _recuperado(B, "RN_de_b")
        token_a = cliente.projeto.bearer(A, papel="owner")
        assert cliente.get(f"/ciclos/{de_b}", headers=token_a).status_code == 404
        assert cliente.get("/ciclos/999999", headers=token_a).status_code == 404
        assert cliente.get("/titular/explicacao/ninguem", headers=token_a).status_code == 404
        assert cliente.get("/ciclos").status_code == 401
        assert cliente.get("/ciclos?limite=0", headers=token_a).status_code == 422
        assert registro_acesso.acessos(A) == [] and registro_acesso.acessos(B) == []

    def test_falha_ao_registrar_nao_derruba_a_leitura(self, cliente, monkeypatch, caplog):
        ciclo_id = _recuperado(A, "RN_resiliente")

        def quebrado():
            raise RuntimeError("banco do registro fora (teste)")
        monkeypatch.setattr(registro_acesso, "_conectar", quebrado)
        with caplog.at_level(logging.ERROR, logger=registro_acesso.__name__):
            r = cliente.get(f"/ciclos/{ciclo_id}", headers=cliente.projeto.bearer(A))
        assert r.status_code == 200, r.text
        assert any("[ACESSO]" in rec.getMessage() for rec in caplog.records)

    def test_retencao_de_12_meses(self):
        agora = datetime(2026, 10, 3, 12, 0)
        registro_acesso.registrar(A, registro_acesso.ROTA_CICLOS, "owner",
                                  datetime(2025, 10, 3, 11, 59))          # 12 meses e 1 min
        registro_acesso.registrar(A, registro_acesso.ROTA_CICLO, "admin",
                                  datetime(2025, 10, 3, 12, 0))           # 12 meses exatos
        registro_acesso.registrar(B, registro_acesso.ROTA_EXPLICACAO, None,
                                  datetime(2026, 9, 1, 8, 0))
        assert registro_acesso.expurgar(agora) == 1
        assert [l["rota"] for l in registro_acesso.acessos(A)] == [registro_acesso.ROTA_CICLO]
        assert len(registro_acesso.acessos(B)) == 1
        assert registro_acesso.expurgar(agora) == 0

    def test_o_limite_de_12_meses_em_29_de_fevereiro(self):
        assert registro_acesso.limite_de_retencao(datetime(2028, 2, 29, 10, 0)) == \
            datetime(2027, 2, 28, 10, 0)
        assert registro_acesso.limite_de_retencao(datetime(2026, 1, 31, 0, 0)) == \
            datetime(2025, 1, 31, 0, 0)


# ══════════════════════════════════════════════════════════════════════════
# A retenção da trilha recebe o prazo (D-E2-2)
# ══════════════════════════════════════════════════════════════════════════

class TestRetencaoDaTrilhaComPrazo:

    def test_com_5_anos_a_decisao_de_3_anos_fica_e_a_de_6_sai(self):
        agora = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
        trilha.registrar_decisoes([
            _dec(A, "RN_t", "2020-06-01T00:00:00+00:00"),      # mais de 6 anos
            _dec(A, "RN_t", "2023-06-01T00:00:00+00:00"),      # mais de 3 anos
            _dec(A, "RN_t", "2026-09-01T00:00:00+00:00"),
        ])
        cinco_anos = configuracao.PADROES_DE_PRODUCAO["retencao_trilha_anos"] * 365
        assert cinco_anos == 1825
        assert trilha.apagar_trilha_expirada(agora, prazo_dias=cinco_anos) == 1
        cadeia = trilha.verificar_cadeia(A)
        assert cadeia["integra"] is True and cadeia["linhas"] == 2
        assert cadeia["inicio_truncado"] is True
        assert trilha.apagar_trilha_expirada(agora, prazo_dias=cinco_anos) == 0

    def test_sem_prazo_vale_a_constante_de_sempre(self):
        agora = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
        trilha.registrar_decisoes([_dec(A, "RN_t", "2023-06-01T00:00:00+00:00"),
                                   _dec(A, "RN_t", "2026-09-01T00:00:00+00:00")])
        assert trilha.RETENCAO_TRILHA_DIAS == 730
        assert trilha.apagar_trilha_expirada(agora) == 1
        assert trilha.verificar_cadeia(A)["linhas"] == 1

    @pytest.mark.parametrize("prazo", [0, -1, 1.5, "1825", True])
    def test_prazo_torto_e_recusado_e_nada_e_apagado(self, prazo):
        trilha.registrar_decisoes([_dec(A, "RN_t", "2020-06-01T00:00:00+00:00")])
        with pytest.raises(ValueError):
            trilha.apagar_trilha_expirada(prazo_dias=prazo)
        assert trilha.verificar_cadeia(A)["linhas"] == 1

    def test_o_expurgo_do_relogio_nao_grava_nem_altera_linha_da_trilha(self):
        trilha.registrar_decisoes([_dec(A, "RN_t", "2026-09-01T00:00:00+00:00")])
        antes = trilha.decisoes_do_sujeito(A, "RN_t")
        relogio_module.expurgar(datetime(2026, 10, 3, 12, 0))
        assert trilha.decisoes_do_sujeito(A, "RN_t") == antes
        assert trilha.verificar_cadeia(A)["integra"] is True

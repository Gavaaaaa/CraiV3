"""tests/test_rodada3_pendencias.py - Rodada 3, Fase 7: pendencias das rodadas anteriores.

O QUE ESTE ARQUIVO MEDE:

  - A RETENCAO DA TRILHA DO ART. 20 NO RELOGIO: o expurgo diario apaga as
    decisoes com mais de 5 anos, so pela ponta antiga, com a cadeia verificavel
    no que sobra; o prazo e sempre passado (nunca a constante antiga de 2
    anos); a empresa que configurou mais que 5 anos tem o prazo dela, e a que
    configurou menos fica com os 5; falha nao some e e tentada de novo;
  - A SAIDA DO SERVICO: depois da subida, um caractere que o terminal nao
    representa vira `?`, e nao um erro que derruba a requisicao.
"""

import asyncio
import inspect
import io
import logging
import sys
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.api import datas
from crai.api import relogio as relogio_module
from crai.churn_voluntary import retention_log as trilha
from crai.dunning import configuracao

A, B = "empresa-a", "empresa-b"
AGORA = datetime(2026, 10, 4, 12, 0)                    # hora local, como o relogio le
HA_6_ANOS = "2020-06-01T00:00:00+00:00"
HA_3_ANOS = "2023-06-01T00:00:00+00:00"
RECENTE = "2026-09-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    monkeypatch.setenv(datas.ENV_FUSO, "America/Sao_Paulo")
    relogio_module._estado["ultimo_expurgo_em"] = None
    yield
    relogio_module._estado["ultimo_expurgo_em"] = None


@pytest.fixture
def producao(monkeypatch):
    for chave, valor in configuracao.PADROES_DE_PRODUCAO.items():
        monkeypatch.setitem(configuracao.PADROES, chave, valor)


def _dec(tenant, sujeito, decidido_em):
    return trilha.decisao(tenant, sujeito, dominio="involuntario", tipo_decisao="risco",
                          modelo="regra", entradas={"amount": 100.0},
                          saida={"recovery_score": 50, "regra": "x"}, decidido_em=decidido_em)


def _datas(tenant, sujeito="RN_t"):
    return sorted(d["decidido_em"] for d in trilha.decisoes_do_sujeito(tenant, sujeito, limite=200))


# == A retencao da trilha no relogio =======================================

class TestRetencaoDaTrilhaNoRelogio:
    def test_o_que_tem_mais_de_5_anos_sai_e_o_resto_fica_encadeado(self, producao):
        trilha.registrar_decisoes([_dec(A, "RN_t", HA_6_ANOS), _dec(A, "RN_t", HA_3_ANOS),
                                   _dec(A, "RN_t", RECENTE)])
        assert relogio_module.expurgar_trilha(AGORA) == 1
        assert _datas(A) == [HA_3_ANOS, RECENTE]
        cadeia = trilha.verificar_cadeia(A)
        assert cadeia["integra"] is True and cadeia["linhas"] == 2
        assert cadeia["inicio_truncado"] is True
        assert relogio_module.expurgar_trilha(AGORA) == 0, "idempotente"

    def test_o_limite_e_de_5_anos_exatos(self, producao):
        agora_utc = datas.com_fuso(AGORA).astimezone(timezone.utc)
        limite = agora_utc - timedelta(days=5 * 365)
        um_segundo_antes = (limite - timedelta(seconds=1)).isoformat(timespec="seconds")
        um_segundo_depois = (limite + timedelta(seconds=1)).isoformat(timespec="seconds")
        trilha.registrar_decisoes([_dec(A, "RN_t", um_segundo_antes), _dec(A, "RN_t", um_segundo_depois)])
        assert relogio_module.expurgar_trilha(AGORA) == 1
        assert _datas(A) == [um_segundo_depois]

    def test_trilha_sem_nada_antigo_fica_identica(self, producao):
        trilha.registrar_decisoes([_dec(A, "RN_t", HA_3_ANOS), _dec(A, "RN_t", RECENTE)])
        antes = (trilha.verificar_cadeia(A), trilha.decisoes_do_sujeito(A, "RN_t"))
        foto = trilha.caminho_do_banco().read_bytes()
        assert relogio_module.expurgar_trilha(AGORA) == 0
        assert (trilha.verificar_cadeia(A), trilha.decisoes_do_sujeito(A, "RN_t")) == antes
        assert antes[0]["inicio_truncado"] is False
        assert trilha.caminho_do_banco().read_bytes() == foto

    def test_o_prazo_e_sempre_passado_e_e_o_de_5_anos(self, producao, monkeypatch):
        trilha.registrar_decisoes([_dec(A, "RN_t", RECENTE), _dec(B, "RN_t", RECENTE)])
        chamadas = []
        original = trilha.apagar_trilha_expirada

        def espiao(agora=None, tenant_id=None, prazo_dias=None):
            chamadas.append((agora, tenant_id, prazo_dias))
            return original(agora, tenant_id, prazo_dias=prazo_dias)
        monkeypatch.setattr(trilha, "apagar_trilha_expirada", espiao)
        relogio_module.expurgar_trilha(AGORA)
        assert [(t, p) for _, t, p in chamadas] == [(A, 1825), (B, 1825)]
        for agora, _, _ in chamadas:
            assert agora.tzinfo is not None and agora.utcoffset() == timedelta(0)
            assert agora == datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)   # 12:00 de Sao Paulo
        assert trilha.RETENCAO_TRILHA_DIAS == 730, "a constante antiga nao e a que o relogio usa"

    def test_a_constante_antiga_apagaria_o_que_o_relogio_guarda(self, producao):
        trilha.registrar_decisoes([_dec(A, "RN_t", HA_3_ANOS)])
        assert relogio_module.expurgar_trilha(AGORA) == 0
        assert _datas(A) == [HA_3_ANOS], "3 anos: com 2 anos de prazo teria saido"

    def test_empresa_que_configurou_mais_tem_o_prazo_dela(self, producao):
        configuracao.gravar(A, {"retencao_trilha_anos": 10})
        for tenant in (A, B):
            trilha.registrar_decisoes([_dec(tenant, "RN_t", HA_6_ANOS), _dec(tenant, "RN_t", RECENTE)])
        assert relogio_module.expurgar_trilha(AGORA) == 1
        assert _datas(A) == [HA_6_ANOS, RECENTE], "a empresa A pediu 10 anos"
        assert _datas(B) == [RECENTE], "a empresa B fica com os 5 do padrao"

    def test_empresa_que_configurou_menos_fica_com_os_5_anos_aprovados(self, producao):
        configuracao.gravar(A, {"retencao_trilha_anos": 1})
        trilha.registrar_decisoes([_dec(A, "RN_t", HA_6_ANOS), _dec(A, "RN_t", HA_3_ANOS)])
        assert relogio_module.expurgar_trilha(AGORA) == 1
        assert _datas(A) == [HA_3_ANOS], "3 anos nao sai: o minimo e 5"

    def test_cada_empresa_e_tratada_sozinha(self, producao):
        trilha.registrar_decisoes([_dec(A, "RN_t", HA_6_ANOS), _dec(A, "RN_t", RECENTE)])
        trilha.registrar_decisoes([_dec(B, "RN_t", HA_3_ANOS)])
        antes_b = trilha.verificar_cadeia(B)
        relogio_module.expurgar_trilha(AGORA)
        assert trilha.verificar_cadeia(B) == antes_b
        assert trilha.tenants_da_trilha() == [A, B]

    def test_trilha_vazia_nao_e_erro(self, producao):
        assert trilha.tenants_da_trilha() == []
        assert relogio_module.expurgar_trilha(AGORA) == 0

    def test_a_passagem_diaria_chama_a_retencao_uma_vez_por_dia(self, producao, caplog):
        trilha.registrar_decisoes([_dec(A, "RN_t", HA_6_ANOS), _dec(A, "RN_t", RECENTE)])
        with caplog.at_level(logging.INFO, logger=relogio_module.__name__):
            primeira = asyncio.run(relogio_module.passagem(AGORA))
            segunda = asyncio.run(relogio_module.passagem(AGORA + timedelta(hours=3)))
            terceira = asyncio.run(relogio_module.passagem(AGORA + timedelta(days=1)))
        assert primeira["expurgo_da_trilha"] == 1
        assert "expurgo_da_trilha" not in segunda and "expurgo" not in segunda
        assert terceira["expurgo_da_trilha"] == 0
        linhas = [r.getMessage() for r in caplog.records if "[RETENCAO-TRILHA]" in r.getMessage()]
        assert len(linhas) == 2
        assert "1 decisão(ões) da trilha do Art. 20 apagada(s), em 1 empresa(s)" in linhas[0]

    def test_falha_na_retencao_nao_some_e_e_tentada_de_novo(self, producao, monkeypatch):
        trilha.registrar_decisoes([_dec(A, "RN_t", HA_6_ANOS)])
        original = trilha.apagar_trilha_expirada

        def quebrado(*a, **k):
            raise RuntimeError("disco cheio (teste)")
        monkeypatch.setattr(trilha, "apagar_trilha_expirada", quebrado)
        with pytest.raises(RuntimeError):
            asyncio.run(relogio_module.passagem(AGORA))
        assert relogio_module.estado_para_health()["ultimo_expurgo_em"] is None
        assert _datas(A) == [HA_6_ANOS]
        monkeypatch.setattr(trilha, "apagar_trilha_expirada", original)
        assert asyncio.run(relogio_module.passagem(AGORA + timedelta(minutes=1)))["expurgo_da_trilha"] == 1

    def test_a_trilha_da_simulacao_do_gateway_nao_e_tocada_pelo_relogio(self, producao):
        from crai import ambiente
        with ambiente.em_simulacao(A):
            trilha.registrar_decisoes([_dec(A, "RN_sim", HA_6_ANOS)])
        assert relogio_module.expurgar_trilha(AGORA) == 0
        with ambiente.em_simulacao(A):
            assert len(trilha.decisoes_do_sujeito(A, "RN_sim")) == 1

    def test_o_expurgo_das_mensagens_continua_sem_tocar_a_trilha(self, producao):
        trilha.registrar_decisoes([_dec(A, "RN_t", HA_6_ANOS)])
        relogio_module.expurgar(AGORA)
        assert _datas(A) == [HA_6_ANOS], "quem apaga a trilha e `expurgar_trilha`, e so ele"

    def test_nada_alem_da_retencao_remove_linha_da_trilha(self):
        fonte = inspect.getsource(relogio_module)
        assert "decisoes_automatizadas" not in fonte, "o relogio nao escreve SQL na trilha"
        assert fonte.count("apagar_trilha_expirada(") == 1


# == A saida do servico ====================================================

def _saida_cp1252():
    """Uma saida como a do console do Windows: cp1252, e que LEVANTA no que nao tem."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict", newline="\n")


TEXTO_DE_FORA = "Cliente \U0001F600 pagou \u2192 ok"  # emoji e seta, escritos por escape: fora do cp1252


class TestSaidaDoServico:
    def test_antes_de_configurar_o_caractere_derruba(self):
        saida = _saida_cp1252()
        with pytest.raises(UnicodeEncodeError):
            print(TEXTO_DE_FORA, file=saida, flush=True)

    def test_depois_de_configurar_o_caractere_vira_interrogacao(self):
        saida = _saida_cp1252()
        assert relogio_module.configurar_saida(saida) == [saida]
        print(TEXTO_DE_FORA, file=saida, flush=True)
        print("Ação concluída: R$ 10,00", file=saida, flush=True)
        escrito = saida.buffer.getvalue().decode("cp1252")
        assert escrito == "Cliente ? pagou ? ok\nAção concluída: R$ 10,00\n"
        assert saida.encoding == "cp1252", "a codificacao nao muda; so o que acontece com o que falta"
        assert saida.errors == "replace"

    def test_sem_argumentos_vale_para_a_saida_padrao_e_a_de_erro(self, monkeypatch):
        saida, erro = _saida_cp1252(), _saida_cp1252()
        monkeypatch.setattr(sys, "stdout", saida)
        monkeypatch.setattr(sys, "stderr", erro)
        assert relogio_module.configurar_saida() == [saida, erro]
        print(TEXTO_DE_FORA)
        print(TEXTO_DE_FORA, file=sys.stderr)
        saida.flush()
        erro.flush()
        assert saida.buffer.getvalue() == erro.buffer.getvalue() == b"Cliente ? pagou ? ok\n"

    def test_fluxo_que_nao_sabe_se_reconfigurar_fica_como_esta(self):
        class SemReconfigure:
            def write(self, texto):
                return len(texto)

        class QueLevanta:
            def reconfigure(self, **k):
                raise OSError("fluxo fechado")
        assert relogio_module.configurar_saida(SemReconfigure(), QueLevanta(), io.StringIO()) == []

    def test_a_subida_do_servico_configura_a_saida_antes_de_ligar_o_relogio(self, monkeypatch):
        ordem = []
        monkeypatch.setattr(relogio_module, "configurar_saida", lambda *a: ordem.append("saida") or [])
        monkeypatch.setattr(relogio_module, "ligar", lambda: ordem.append("relogio") or False)
        with TestClient(app_module.app):
            assert ordem == ["saida", "relogio"]
        fonte = inspect.getsource(relogio_module.ciclo_de_vida)
        assert fonte.index("configurar_saida()") < fonte.index("ligar()")

    def test_com_o_servico_no_ar_um_texto_de_fora_nao_derruba_a_requisicao(self, monkeypatch):
        saida = _saida_cp1252()
        monkeypatch.setattr(sys, "stdout", saida)
        monkeypatch.setattr(sys, "stderr", _saida_cp1252())

        @app_module.app.get("/_teste/imprime-texto-de-fora")
        async def imprime():                      # uma rota que imprime o que veio de fora
            print(TEXTO_DE_FORA)
            return {"ok": True}
        try:
            with TestClient(app_module.app) as c:
                r = c.get("/_teste/imprime-texto-de-fora")
            assert r.status_code == 200 and r.json() == {"ok": True}
            saida.flush()
            assert b"Cliente ? pagou ? ok" in saida.buffer.getvalue()
        finally:
            app_module.app.router.routes[:] = [
                rota for rota in app_module.app.router.routes
                if getattr(rota, "path", "") != "/_teste/imprime-texto-de-fora"]

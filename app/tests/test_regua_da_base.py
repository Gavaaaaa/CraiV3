"""tests/test_regua_da_base.py — o risco se adapta à base do cliente, sem gritar à toa.

A crítica que motiva isto: as regras fixas comparam `days_since_last` com 30
e `features_used_30d` com 5, para toda base do mundo. O que aqui é verificado:

  (a) duas bases de perfis OPOSTOS — uso diário (mediana de 1 dia sem login)
      e uso mensal (mediana de 25) — produzem risco DIFERENTE para um cliente
      com os MESMOS números absolutos, e a explicação diz em qual posição da
      própria base ele está. É o teste que prova a adaptação;
  (b) REGRA DE SEGURANÇA: base com menos de 30 linhas utilizáveis, ou sem
      distribuição, produz EXATAMENTE o risco de hoje (`risco_por_features`),
      com `origem_da_regua: "padrao_global"` e a explicação de sempre;
  (c) base sem nenhuma coluna comportamental continua devolvendo
      `dado_insuficiente` com `risk_score: None`, nunca 0.0 — a régua da base
      não inventa risco para quem não tem dado;
  (d) os percentis olham a cauda certa (alta para dias, BAIXA para uso),
      ignoram nulos e contam quantas linhas entraram;
  (e) o ranking carrega `origem_da_regua` nos dois caminhos (upload e SDK);
  (f) modelo treinado ativo continua decidindo antes de qualquer régua;
  (g) O SISTEMA NÃO GRITA NUMA BASE SAUDÁVEL: posição ordena a lista, mas
      "alto"/"critico" exigem também sinal absoluto de desengajamento. Uma
      base em que todo mundo entrou esta semana e usa o produto sai ordenada
      e com ninguém em alarme.

Os testes de regressão existentes (`test_batch_scoring`, `test_risk_pluggable`)
não foram alterados: toda base deles tem menos de 30 linhas e cai na régua
global, com o número de sempre.

Uso:
    pytest tests/test_regua_da_base.py -v
"""

import pytest

from crai.churn_voluntary import batch_scoring as bs
from crai.churn_voluntary import clientes_importados as ci
from crai.churn_voluntary import insights_unificados as iu
from crai.churn_voluntary import risk_scorer as rs
from crai.churn_voluntary.risk_scorer import (
    PISO_ABSOLUTO_DIAS_SEM_LOGIN,
    classify_criticality,
    risco_por_features,
    risco_por_posicao,
    sinal_absoluto_de_desengajamento,
)

DIARIO = "saas-uso-diario"
MENSAL = "saas-uso-mensal"
SAUDAVEL = "saas-saudavel"


@pytest.fixture(autouse=True)
def modelo_isolado(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(rs, "MODELO_PATH", tmp_path / "voluntary_risk.joblib")
    monkeypatch.setattr(rs, "MODELO_META_PATH", tmp_path / "voluntary_risk_meta.json")
    monkeypatch.setattr(rs, "_modelo", None)
    monkeypatch.setattr(rs, "_modelo_consultado", False)


def _cliente(cid, mrr=300.0, perfil="CLT", dias=None, uso=None, email=None):
    return {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil,
            "days_since_last": dias, "features_used_30d": uso, "email": email}


def _base(prefixo, dias, usos, n=60):
    """n clientes ciclando pelos valores dados — determinístico, sem random."""
    return [_cliente(f"{prefixo}-{i:03d}", dias=float(dias[i % len(dias)]),
                     uso=float(usos[i % len(usos)])) for i in range(n)]


# Uso diário: quase todo mundo entrou hoje ou ontem, e usa muita coisa.
BASE_DIARIA = _base("d", dias=[0, 0, 1, 1, 1, 1, 2, 2, 3, 5], usos=[8, 10, 12, 15, 18, 20])
# Uso mensal: um fechamento por mês; 25 dias sem login é a mediana.
BASE_MENSAL = _base("m", dias=[10, 18, 22, 25, 25, 25, 28, 30, 35, 40], usos=[1, 2, 3, 4, 5, 6])
# Saudável: ninguém passou de 4 dias sem entrar, e todo mundo usa bastante.
BASE_SAUDAVEL = _base("s", dias=[0, 0, 0, 1, 1, 1, 2, 2, 3, 4], usos=[9, 11, 13, 15, 17, 20])

# O MESMO cliente, com os MESMOS números, em cada base.
ANCORA = _cliente("ANCORA-07-A", dias=7.0, uso=3.0, mrr=300.0)


def _linha(tenant, cid):
    return next(l for l in bs.pontuar_base(tenant) if l["customer_id_externo"] == cid)


# ── (a) a adaptação ──────────────────────────────────────────────────────

class TestAdaptacao:
    def test_medianas_das_duas_bases_sao_opostas(self):
        ci.gravar(DIARIO, BASE_DIARIA)
        ci.gravar(MENSAL, BASE_MENSAL)
        rd = bs.regua_da_base(ci.listar(DIARIO))
        rm = bs.regua_da_base(ci.listar(MENSAL))
        assert rd["days_since_last"]["p50"] == 1.0
        assert rm["days_since_last"]["p50"] == 25.0

    def test_mesmo_cliente_recebe_risco_diferente_em_bases_opostas(self):
        """O teste mais importante do dia: 7 dias sem login é alarme numa base
        de uso diário e rotina numa base de uso mensal."""
        ci.gravar(DIARIO, BASE_DIARIA + [ANCORA])
        ci.gravar(MENSAL, BASE_MENSAL + [ANCORA])
        na_diaria = _linha(DIARIO, "ANCORA-07-A")
        na_mensal = _linha(MENSAL, "ANCORA-07-A")

        assert na_diaria["origem_da_regua"] == bs.REGUA_BASE
        assert na_mensal["origem_da_regua"] == bs.REGUA_BASE
        assert na_diaria["risk_score"] != na_mensal["risk_score"]
        assert na_diaria["risk_score"] > na_mensal["risk_score"]
        # E nenhum dos dois é o número da régua global.
        assert na_diaria["risk_score"] != risco_por_features(7.0, 3.0, 300.0)
        assert na_mensal["risk_score"] != risco_por_features(7.0, 3.0, 300.0)

    def test_explicacao_diz_a_posicao_na_propria_base(self):
        ci.gravar(DIARIO, BASE_DIARIA + [ANCORA])
        ci.gravar(MENSAL, BASE_MENSAL + [ANCORA])
        na_diaria = _linha(DIARIO, "ANCORA-07-A")["explicacao"]
        na_mensal = _linha(MENSAL, "ANCORA-07-A")["explicacao"]

        assert na_diaria.startswith("sem login há 7 dias — acima de 90% da sua base")
        assert na_mensal.startswith("sem login há 7 dias — dentro do normal da sua base")
        # 3 funcionalidades numa base que usa de 8 a 20: embaixo de todo mundo.
        assert "usa 3 funcionalidades nos últimos 30 dias — menos que 90% da sua base" in na_diaria
        # Na base mensal (usa de 1 a 6), 3 é a mediana: normal.
        assert "usa 3 funcionalidades nos últimos 30 dias — dentro do normal da sua base" in na_mensal
        for texto in (na_diaria, na_mensal):
            assert "MRR R$ 300,00" in texto
            assert "assumido" not in texto

    def test_na_base_diaria_sete_dias_e_critico_na_mensal_e_rotina(self):
        ci.gravar(DIARIO, BASE_DIARIA + [ANCORA])
        ci.gravar(MENSAL, BASE_MENSAL + [ANCORA])
        assert _linha(DIARIO, "ANCORA-07-A")["criticality"] == "critico"
        assert _linha(MENSAL, "ANCORA-07-A")["criticality"] == "padrao"

    def test_ordem_dentro_da_base_continua_por_inatividade_e_uso(self):
        """A régua muda os números, não o sentido: mais dias sem login e menos
        uso continuam subindo o risco dentro da mesma base."""
        ci.gravar(MENSAL, BASE_MENSAL + [
            _cliente("frio-uso-3", dias=40.0, uso=3.0), _cliente("morno-uso-3", dias=25.0, uso=3.0),
            _cliente("dias-25-uso-1", dias=25.0, uso=1.0), _cliente("dias-25-uso-6", dias=25.0, uso=6.0),
        ])
        ranking = bs.pontuar_base(MENSAL)
        riscos = [l["risk_score"] for l in ranking]
        assert riscos == sorted(riscos, reverse=True)
        por_id = {l["customer_id_externo"]: l["risk_score"] for l in ranking}
        assert por_id["frio-uso-3"] > por_id["morno-uso-3"]          # mais dias → mais risco
        assert por_id["dias-25-uso-1"] > por_id["dias-25-uso-6"]     # menos uso → mais risco

    def test_acessou_hoje_com_uso_maximo_e_zero_tambem_na_regua_da_base(self):
        ci.gravar(DIARIO, BASE_DIARIA + [_cliente("ativo", dias=0.0, uso=50.0)])
        assert _linha(DIARIO, "ativo")["risk_score"] == 0.0
        assert _linha(DIARIO, "ativo")["explicacao"].startswith("acessou hoje,")


# ── (b) regra de segurança: cai no comportamento de hoje ─────────────────

class TestRegraDeSeguranca:
    def test_base_pequena_produz_exatamente_o_risco_de_hoje(self):
        pequena = BASE_MENSAL[:28] + [ANCORA]           # 29 linhas: abaixo do mínimo
        assert len(pequena) == 29
        ci.gravar(MENSAL, pequena)
        for linha in bs.pontuar_base(MENSAL):
            assert linha["origem_da_regua"] == bs.REGUA_GLOBAL
            assert linha["risk_score"] == risco_por_features(
                linha["days_since_last"], linha["features_used_30d"], linha["mrr"])
            assert linha["criticality"] == classify_criticality(linha["risk_score"], linha["mrr"])
        assert _linha(MENSAL, "ANCORA-07-A")["risk_score"] == risco_por_features(7.0, 3.0, 300.0)
        assert _linha(MENSAL, "ANCORA-07-A")["explicacao"] == (
            "sem login há 7 dias, usa 3 funcionalidades nos últimos 30 dias, MRR R$ 300,00")

    def test_exatamente_trinta_linhas_utilizaveis_ja_usa_a_regua_da_base(self):
        ci.gravar(MENSAL, BASE_MENSAL[:30])
        assert all(l["origem_da_regua"] == bs.REGUA_BASE for l in bs.pontuar_base(MENSAL))

    def test_regua_da_base_devolve_none_para_base_pequena(self):
        assert bs.regua_da_base(BASE_MENSAL[:29]) is None
        assert bs.regua_da_base([]) is None

    def test_uma_coluna_com_menos_de_trinta_valores_cai_na_global(self):
        """60 linhas com dias, mas só 10 com uso: sem régua para o uso, sem
        régua nenhuma — a decisão é uma por base, não meia por coluna."""
        base = [_cliente(f"x-{i}", dias=float(i % 40),
                         uso=float(i) if i < 10 else None) for i in range(60)]
        assert bs.regua_da_base(base) is None
        ci.gravar(MENSAL, base)
        for linha in bs.pontuar_base(MENSAL):
            assert linha["origem_da_regua"] == bs.REGUA_GLOBAL
            assert linha["risk_score"] == risco_por_features(
                linha["days_since_last"], linha["features_used_30d"], linha["mrr"])

    def test_base_sem_distribuicao_de_dias_cai_na_global(self):
        """Todo mundo com 0 dias sem login: p90 = 0, não há posição a medir."""
        base = [_cliente(f"z-{i}", dias=0.0, uso=float(1 + i % 7)) for i in range(60)]
        assert bs.regua_da_base(base) is None

    def test_base_sem_distribuicao_de_uso_cai_na_global(self):
        """Dois terços da base usam 0 funcionalidades: p50 = 0, sem cauda baixa."""
        base = [_cliente(f"z-{i}", dias=float(i % 30), uso=0.0 if i % 3 else 5.0) for i in range(60)]
        assert bs.regua_da_base(base) is None

    def test_pontuar_cliente_sem_regua_e_o_de_sempre(self):
        linha = bs.pontuar_cliente(_cliente("c", dias=18.0, uso=1.0, mrr=100.0))
        assert linha["risk_score"] == 0.66                # o valor medido em 31a2450
        assert linha["origem_da_regua"] == bs.REGUA_GLOBAL
        assert "sua base" not in linha["explicacao"]

    def test_regras_fixas_continuam_intactas(self):
        """`_risco_por_regras` é o chão do sistema e não mudou uma linha."""
        assert rs._risco_por_regras("Session Started", {"days_since_last": 30, "features_used_30d": 0}) == 1.0
        assert rs._risco_por_regras("Session Started", {"days_since_last": 18, "features_used_30d": 1}) == 0.66
        assert rs._risco_por_regras("Cancellation Page Viewed", {}) == 0.90

    def test_classify_criticality_sem_valores_absolutos_e_a_de_sempre(self):
        """A assinatura antiga (risco, mrr) continua dando o de sempre: é o
        caminho do SDK e da régua global."""
        assert classify_criticality(0.95, 100.0) == "critico"
        assert classify_criticality(0.80, 100.0) == "alto"
        assert classify_criticality(0.10, 100.0) == "padrao"
        assert classify_criticality(0.10, 2500.0) == "critico"


# ── (c) dado insuficiente continua null ──────────────────────────────────

class TestDadoInsuficiente:
    def test_base_sem_coluna_comportamental_continua_null_e_nunca_zero(self):
        base = [_cliente(f"s-{i}", mrr=100.0 + i) for i in range(60)]
        assert bs.regua_da_base(base) is None
        ci.gravar(MENSAL, base)
        ranking = bs.pontuar_base(MENSAL)
        assert len(ranking) == 60
        for linha in ranking:
            assert linha["risk_score"] is None
            assert linha["criticality"] == bs.CRITICIDADE_SEM_DADO
            assert linha["explicacao"] == bs.TEXTO_SEM_DADO
            assert linha["origem_da_regua"] == bs.REGUA_GLOBAL

    def test_linha_sem_dado_numa_base_grande_continua_null(self):
        """A régua da base existe (60 linhas com dado), mas o cliente sem dado
        não passa pelo motor: null, e por último."""
        ci.gravar(MENSAL, BASE_MENSAL + [_cliente("sem-dado", mrr=9000.0)])
        ranking = bs.pontuar_base(MENSAL)
        ultimo = ranking[-1]
        assert ultimo["customer_id_externo"] == "sem-dado"
        assert ultimo["risk_score"] is None
        assert ultimo["criticality"] == bs.CRITICIDADE_SEM_DADO
        assert ranking[0]["origem_da_regua"] == bs.REGUA_BASE

    def test_uma_feature_ausente_na_regua_da_base_nao_inventa_risco(self):
        ci.gravar(DIARIO, BASE_DIARIA + [_cliente("so-uso", uso=50.0), _cliente("so-dias", dias=0.0)])
        assert _linha(DIARIO, "so-uso")["risk_score"] == 0.0
        assert "dias sem login desconhecidos (assumido 0)" in _linha(DIARIO, "so-uso")["explicacao"]
        assert _linha(DIARIO, "so-dias")["risk_score"] == 0.0
        assert "assumido como os mais ativos da sua base" in _linha(DIARIO, "so-dias")["explicacao"]


# ── (d) os percentis: cada coluna na cauda certa ─────────────────────────

class TestPercentis:
    def test_dias_na_cauda_alta_e_uso_na_cauda_baixa(self):
        regua = bs.regua_da_base(BASE_MENSAL)
        assert set(regua["days_since_last"]) == {"p50", "p75", "p90", "n"}
        assert set(regua["features_used_30d"]) == {"p10", "p25", "p50", "n"}
        assert regua["features_used_30d"]["p10"] <= regua["features_used_30d"]["p25"] \
            <= regua["features_used_30d"]["p50"]

    def test_ignora_nulos_e_conta_quem_entrou(self):
        base = BASE_MENSAL + [_cliente("nulo-1"), _cliente("nulo-2"), _cliente("so-dias", dias=99.0)]
        regua = bs.regua_da_base(base)
        assert regua["days_since_last"]["n"] == 61
        assert regua["features_used_30d"]["n"] == 60
        assert regua["n_linhas"] == 63
        assert regua["n_utilizaveis"] == 61
        assert regua["days_since_last"]["p50"] == 25.0
        assert regua["days_since_last"]["p50"] <= regua["days_since_last"]["p75"] \
            <= regua["days_since_last"]["p90"]

    @pytest.mark.parametrize("valor, esperado", [
        (0.0, 0.0), (5.0, 0.25), (10.0, 0.5), (20.0, 0.75), (30.0, 0.9), (60.0, 1.0), (999.0, 1.0),
    ])
    def test_posicao_na_base_interpola_entre_os_percentis(self, valor, esperado):
        p = {"p50": 10.0, "p75": 20.0, "p90": 30.0}
        assert rs.posicao_na_base(valor, p) == pytest.approx(esperado)

    @pytest.mark.parametrize("valor, esperado", [
        (0.0, 1.0), (2.0, 0.9), (4.0, 0.75), (8.0, 0.5), (12.0, 0.25), (16.0, 0.0), (99.0, 0.0),
    ])
    def test_desengajamento_de_uso_le_a_cauda_baixa(self, valor, esperado):
        p = {"p10": 2.0, "p25": 4.0, "p50": 8.0}
        assert rs.desengajamento_de_uso(valor, p) == pytest.approx(esperado)

    def test_percentis_iguais_ficam_com_a_afirmacao_mais_fraca(self):
        """Se p50 = p75 = p90, 40% da base está naquele valor: quem está nele
        não está "acima de 90%" — está na mediana."""
        p = {"p50": 5.0, "p75": 5.0, "p90": 5.0}
        assert rs.posicao_na_base(5.0, p) == pytest.approx(0.5)
        assert rs.posicao_na_base(2.5, p) == pytest.approx(0.25)
        assert rs.posicao_na_base(10.0, p) == pytest.approx(1.0)
        q = {"p10": 3.0, "p25": 3.0, "p50": 3.0}
        assert rs.desengajamento_de_uso(3.0, q) == pytest.approx(0.5)
        assert rs.desengajamento_de_uso(0.0, q) == pytest.approx(1.0)

    def test_risco_por_posicao_usa_os_mesmos_pesos_das_regras(self):
        regua = {"days_since_last": {"p50": 10.0, "p75": 20.0, "p90": 30.0},
                 "features_used_30d": {"p10": 2.0, "p25": 4.0, "p50": 8.0}}
        # inatividade no p90 (0,9 × 0,7) + uso na mediana (0,5 × 0,3)
        assert risco_por_posicao(30.0, 8.0, regua) == pytest.approx(0.78)
        assert risco_por_posicao(0.0, 16.0, regua) == 0.0             # acessou hoje, 2·p50 de uso
        assert risco_por_posicao(60.0, 0.0, regua) == 1.0
        assert risco_por_posicao(0.0, 0.0, regua) == pytest.approx(0.3)   # só o uso, no máximo
        assert 0.0 <= risco_por_posicao(7.0, 3.0, regua) <= 1.0


# ── (e) o campo chega ao /insights nos dois caminhos ─────────────────────

class TestOrigemDaRegua:
    def test_linha_do_upload_carrega_origem_da_regua(self):
        ci.gravar(MENSAL, BASE_MENSAL)
        for linha in iu.clientes_em_risco(MENSAL):
            assert linha["origem"] == iu.ORIGEM_UPLOAD
            assert linha["origem_da_regua"] == bs.REGUA_BASE

    def test_linha_do_sdk_e_sempre_regua_global(self):
        linha = iu._linha_do_sdk({"user_id": "user:c1", "risk_score": 0.5, "criticality": "padrao",
                                  "event": "Session Started", "days_since_last": 7.0,
                                  "features_used_30d": 3.0, "mrr": 100.0,
                                  "registrado_em": "2026-09-12T00:00:00+00:00"})
        assert linha["origem_da_regua"] == bs.REGUA_GLOBAL
        assert "sua base" not in linha["explicacao"]


# ── (f) modelo treinado decide antes de qualquer régua ───────────────────

class TestModeloAtivo:
    def test_modelo_ativo_vence_a_regua_da_base(self, monkeypatch):
        class ModeloFixo:
            def predict_proba(self, X):
                return [[0.58, 0.42] for _ in X]

        monkeypatch.setattr(rs, "_modelo", ModeloFixo())
        monkeypatch.setattr(rs, "_modelo_consultado", True)
        ci.gravar(MENSAL, BASE_MENSAL + [ANCORA])
        linha = _linha(MENSAL, "ANCORA-07-A")
        assert linha["risk_score"] == 0.42
        assert linha["origem_da_regua"] == bs.REGUA_GLOBAL
        assert "sua base" not in linha["explicacao"]


# ── (g) o sistema não grita numa base saudável ───────────────────────────

class TestBaseSaudavel:
    def test_ninguem_em_alto_nem_critico(self):
        """Todo mundo entrou esta semana e usa o produto. A lista sai
        ordenada — há um primeiro da fila — mas ninguém é alarme."""
        ci.gravar(SAUDAVEL, BASE_SAUDAVEL)
        ranking = bs.pontuar_base(SAUDAVEL)
        assert len(ranking) == 60
        assert all(l["origem_da_regua"] == bs.REGUA_BASE for l in ranking)
        assert {l["criticality"] for l in ranking} == {"padrao"}
        riscos = [l["risk_score"] for l in ranking]
        assert riscos == sorted(riscos, reverse=True)
        assert riscos[0] > riscos[-1]                      # a ordem existe

    def test_primeiro_da_fila_explica_por_que_nao_e_alarme(self):
        ci.gravar(SAUDAVEL, BASE_SAUDAVEL)
        primeiro = bs.pontuar_base(SAUDAVEL)[0]
        assert primeiro["risk_score"] >= rs.HIGH_RISK_THRESHOLD
        assert primeiro["criticality"] == "padrao"
        assert primeiro["explicacao"].endswith(
            "— primeiro da fila da sua base, mas sem sinal de abandono: "
            f"entrou há menos de {int(PISO_ABSOLUTO_DIAS_SEM_LOGIN)} dias e usa o produto")

    def test_com_sinal_absoluto_o_alarme_volta(self):
        """A mesma base saudável mais um cliente que sumiu há uma semana: ele,
        e só ele, vira alarme."""
        ci.gravar(SAUDAVEL, BASE_SAUDAVEL + [_cliente("sumiu", dias=7.0, uso=9.0)])
        ranking = bs.pontuar_base(SAUDAVEL)
        assert ranking[0]["customer_id_externo"] == "sumiu"
        assert ranking[0]["criticality"] in ("alto", "critico")
        assert all(l["criticality"] == "padrao" for l in ranking[1:])

    def test_uso_zero_tambem_e_sinal_absoluto(self):
        ci.gravar(SAUDAVEL, BASE_SAUDAVEL + [_cliente("nao-usa", dias=2.0, uso=0.0)])
        linha = _linha(SAUDAVEL, "nao-usa")
        assert linha["criticality"] in ("alto", "critico")

    def test_a_porta_do_mrr_nao_passa_pelo_piso(self):
        """Crítico por VALOR é sobre dinheiro, não sobre risco: continua sem
        exigir sinal, e a explicação diz isso."""
        ci.gravar(SAUDAVEL, BASE_SAUDAVEL + [_cliente("grande", dias=1.0, uso=15.0, mrr=2500.0)])
        linha = _linha(SAUDAVEL, "grande")
        assert linha["criticality"] == "critico"
        assert linha["explicacao"].endswith("— crítico pelo valor da conta, não pelo risco")

    def test_as_bases_de_demonstracao_nao_gritam_sem_os_ancoras(self):
        """A base de uso diário, sozinha, também é saudável: ninguém passou de
        5 dias. Só os âncoras (7 e 20 dias) viram alarme nela."""
        ci.gravar(DIARIO, BASE_DIARIA)
        assert {l["criticality"] for l in bs.pontuar_base(DIARIO)} == {"padrao"}
        ci.gravar(DIARIO, BASE_DIARIA + [ANCORA])
        alarmes = [l["customer_id_externo"] for l in bs.pontuar_base(DIARIO)
                   if l["criticality"] != "padrao"]
        assert alarmes == ["ANCORA-07-A"]

    @pytest.mark.parametrize("dias, uso, esperado", [
        (7.0, 10.0, True), (6.9, 10.0, False), (2.0, 0.0, True), (2.0, 1.0, False),
        (None, 0.0, True), (None, 5.0, False), (10.0, None, True), (None, None, False),
    ])
    def test_sinal_absoluto(self, dias, uso, esperado):
        assert sinal_absoluto_de_desengajamento(dias, uso) is esperado

    def test_classify_criticality_com_valores_exige_sinal(self):
        assert classify_criticality(0.95, 100.0, 2.0, 10.0) == "padrao"      # posição alta, sem sinal
        assert classify_criticality(0.95, 100.0, 7.0, 10.0) == "critico"     # 7 dias: sinal
        assert classify_criticality(0.80, 100.0, 2.0, 0.0) == "alto"         # uso zero: sinal
        assert classify_criticality(0.95, 2500.0, 2.0, 10.0) == "critico"    # valor: sem piso

"""tests/test_perfil_provider.py — Sprint 7: o perfil vira ponto de extensão.

O QUE ESTAVA FECHADO. Quatro das doze entradas do classificador — tenure,
histórico de pagamento, falhas em 90 dias e ticket médio, e por consequência o
LTV — não vêm do evento do PSP. Vêm do negócio, e como nada disso está
conectado, `_perfil_simulado` as fabricava com `np.random` no meio de
`_features_pix`. O perfil sintético é aceitável enquanto declarado; o que não
podia continuar é ele ser IRREMOVÍVEL — plugar a fonte real exigiria reescrever
o caminho de features, justamente na véspera do treino.

AS DUAS PROPRIEDADES QUE ESTE ARQUIVO GUARDA:

    1. REPRODUTIBILIDADE. O provedor sintético devolve exatamente os mesmos
       valores de antes deste sprint, para a mesma semente. Um refactor que
       mexesse num centavo de LTV invalidaria a demo e faria o e-Profit divergir
       do que o treino viu — e mexeu: a primeira versão de `ml/ltv.py` trocou
       `round` por `np.round` e moveu 11 LTVs em 2.000 por um centavo.
    2. SUBSTITUIBILIDADE. Um provedor alternativo entra sem que `_features_pix`,
       o nó de diagnóstico ou o schema do dataset mudem de forma.
"""

from datetime import datetime

import numpy as np
import pytest

from crai.agent import workflow as workflow_module
from crai.agent.perfil_provider import (
    CAMPOS_DO_PERFIL,
    ENV_FONTE_REAL,
    DBPerfilProvider,
    PerfilProvider,
    SyntheticPerfilProvider,
    provedor_padrao,
)
from crai.agent.workflow import _features_pix, _perfil_simulado
from crai.ml.ltv import RETENCAO_PADRAO, ltv_estimado
from crai.ml.synthetic_data import seed_por_cliente

VALOR = 299.90


def _perfil_como_era(chave: str, invoice: float) -> dict:
    """A implementação ANTERIOR ao Sprint 7, copiada literalmente.

    Serve de oráculo: o provedor sintético tem que reproduzi-la campo a campo.
    Copiar em vez de importar é o ponto — se o código de produção mudar de
    comportamento, este oráculo NÃO muda junto, e o teste reprova.
    """
    now = datetime.now()
    rng = np.random.default_rng(seed=seed_por_cliente(chave))

    tenure = int(rng.exponential(scale=12))
    payment_history = round(float(np.clip(rng.beta(5, 2), 0, 1)), 3)
    failure_count = int(rng.poisson(1.5))
    avg_ticket = round(invoice * rng.uniform(0.9, 1.1), 2)
    ltv = round(max(invoice, tenure * avg_ticket * 0.9 / 12), 2)

    return {
        "tenure_months": tenure, "day_of_month": now.day,
        "invoice_amount": invoice, "avg_ticket": avg_ticket,
        "payment_history_score": payment_history,
        "failure_count_90d": failure_count, "hour_of_day": now.hour,
        "day_of_week": now.weekday(), "ltv_estimated": ltv,
    }


class TestReprodutibilidade:

    def test_o_sintetico_reproduz_o_perfil_anterior(self):
        provedor = SyntheticPerfilProvider()
        for i in range(200):
            chave, valor = f"RN_{i}", round(10 + i * 0.37, 2)
            assert provedor.get_perfil(chave, valor) == _perfil_como_era(chave, valor), (
                f"o perfil de {chave} mudou — a demo deixa de ser reprodutível e "
                "o e-Profit passa a divergir do que o treino viu")

    def test_o_ltv_nao_se_moveu_um_centavo(self):
        """A regressão real que este sprint quase introduziu.

        `round` do Python e `np.round` não concordam em todo float. Unificar a
        fórmula com o arredondamento do numpy movia ~0,5% dos LTVs em um
        centavo — e o LTV é o multiplicador do e-Profit.
        """
        provedor = SyntheticPerfilProvider()
        for i in range(500):
            chave, valor = f"RN_ltv_{i}", round(15 + i * 1.13, 2)
            assert (provedor.get_perfil(chave, valor)["ltv_estimated"]
                    == _perfil_como_era(chave, valor)["ltv_estimated"])

    def test_a_mesma_semente_da_o_mesmo_perfil(self):
        provedor = SyntheticPerfilProvider()
        a = provedor.get_perfil("RN_estavel", VALOR)
        b = SyntheticPerfilProvider().get_perfil("RN_estavel", VALOR)
        assert a == b

    def test_clientes_diferentes_tem_perfis_diferentes(self):
        provedor = SyntheticPerfilProvider()
        a = provedor.get_perfil("RN_a", VALOR)
        b = provedor.get_perfil("RN_b", VALOR)
        assert a["tenure_months"] != b["tenure_months"] or a["avg_ticket"] != b["avg_ticket"]

    def test_o_perfil_traz_todos_os_campos_do_contrato(self):
        perfil = SyntheticPerfilProvider().get_perfil("RN_campos", VALOR)
        assert set(CAMPOS_DO_PERFIL) <= set(perfil), (
            f"faltando: {sorted(set(CAMPOS_DO_PERFIL) - set(perfil))}")


class TestSubstituibilidade:

    def test_um_provedor_alternativo_atravessa_o_pipeline(self, monkeypatch):
        """Sem herdar nada: `PerfilProvider` é `Protocol`."""

        class ProvedorFalso:
            def get_perfil(self, customer_id, invoice_amount):
                return {
                    "tenure_months": 99, "day_of_month": 1,
                    "invoice_amount": invoice_amount, "avg_ticket": 42.0,
                    "payment_history_score": 0.123, "failure_count_90d": 7,
                    "hour_of_day": 3, "day_of_week": 4, "ltv_estimated": 12345.0,
                }

        monkeypatch.setattr(workflow_module, "_perfil_provider", ProvedorFalso())

        features = _features_pix(
            {"valor": VALOR, "id_recorrencia": "RN_mock",
             "codigo_falha": "insufficient_funds"},
            VALOR, customer_id="RN_mock",
        )

        assert features["tenure_months"] == 99
        assert features["ltv_estimated"] == 12345.0
        # E o resto do caminho de features segue intacto.
        assert features["gateway_error_code"] == "insufficient_funds"
        assert features["card_brand"] == "n/a"
        assert features["attempt_count"] == 1

    def test_qualquer_objeto_com_o_metodo_satisfaz_o_protocolo(self):
        class Qualquer:
            def get_perfil(self, customer_id, invoice_amount):
                return {}

        assert isinstance(Qualquer(), PerfilProvider)
        assert isinstance(SyntheticPerfilProvider(), PerfilProvider)
        assert isinstance(DBPerfilProvider(), PerfilProvider)

    def test_o_workflow_continua_usando_o_provedor_de_modulo(self):
        """`_perfil_simulado` virou fachada — é o que mantém o resto intacto."""
        assert _perfil_simulado("RN_fachada", VALOR) == (
            workflow_module._perfil_provider.get_perfil("RN_fachada", VALOR))


class TestDBPerfilProviderStub:

    def test_sem_env_cai_no_sintetico(self, monkeypatch):
        monkeypatch.delenv(ENV_FONTE_REAL, raising=False)
        provedor = DBPerfilProvider()

        assert provedor.disponivel() is False
        assert provedor.get_perfil("RN_stub", VALOR) == _perfil_como_era("RN_stub", VALOR)

    def test_com_env_mas_sem_dado_cai_no_sintetico(self, monkeypatch):
        """Cliente sem histórico é caso NORMAL — assinante novo, ou migrado.

        Derrubar a recuperação por falta de perfil trocaria um dado aproximado
        por nenhuma cobrança.
        """
        monkeypatch.setenv(ENV_FONTE_REAL, "postgres://exemplo")
        provedor = DBPerfilProvider()

        assert provedor.disponivel() is True
        assert provedor.get_perfil("RN_sem_hist", VALOR) == _perfil_como_era(
            "RN_sem_hist", VALOR)

    def test_quando_a_fonte_responde_o_perfil_real_prevalece(self, monkeypatch):
        """O contrato do ponto de conexão, exercido antes de existir fonte."""
        monkeypatch.setenv(ENV_FONTE_REAL, "postgres://exemplo")

        class ComBanco(DBPerfilProvider):
            def _consultar(self, customer_id, invoice_amount):
                return {"tenure_months": 36, "day_of_month": 10,
                        "invoice_amount": invoice_amount, "avg_ticket": 250.0,
                        "payment_history_score": 0.95, "failure_count_90d": 0,
                        "hour_of_day": 9, "day_of_week": 1,
                        "ltv_estimated": 6750.0}

        perfil = ComBanco().get_perfil("RN_real", VALOR)
        assert perfil["tenure_months"] == 36
        assert perfil["payment_history_score"] == 0.95

    def test_a_env_e_lida_a_cada_chamada(self, monkeypatch):
        provedor = DBPerfilProvider()
        monkeypatch.delenv(ENV_FONTE_REAL, raising=False)
        assert provedor.disponivel() is False
        monkeypatch.setenv(ENV_FONTE_REAL, "x")
        assert provedor.disponivel() is True

    def test_o_provedor_padrao_do_pipeline_e_o_db_com_fallback(self):
        provedor = provedor_padrao()
        assert isinstance(provedor, DBPerfilProvider)
        assert isinstance(provedor._fallback, SyntheticPerfilProvider)


class TestFormulaUnicaDeLTV:

    def test_a_formula_escalar_bate_com_a_do_perfil(self):
        perfil = SyntheticPerfilProvider().get_perfil("RN_formula", VALOR)
        assert perfil["ltv_estimated"] == ltv_estimado(
            perfil["tenure_months"], perfil["avg_ticket"], VALOR, RETENCAO_PADRAO)

    def test_o_ltv_nunca_e_menor_que_a_cobranca(self):
        """Um cliente que deve R$ 300 vale ao menos R$ 300.

        Assumir menos faria o e-Profit rejeitar recuperações que se pagam.
        """
        assert ltv_estimado(0, 0.0, 300.0) == 300.0
        assert ltv_estimado(1, 1.0, 300.0) == 300.0

    def test_a_versao_vetorizada_bate_com_o_gerador_de_dataset(self):
        """O dataset já gerado não pode mudar de valor."""
        rng = np.random.default_rng(7)
        tenure = rng.integers(0, 80, 500)
        ticket = np.round(rng.uniform(20, 900, 500), 2)
        invoice = np.round(rng.uniform(20, 900, 500), 2)
        fator = np.clip(0.85 + rng.uniform(0, 1, 500) * 0.10, 0.80, 0.98)

        esperado = np.maximum((tenure * ticket * fator / 12).round(2), invoice).round(2)
        assert np.array_equal(ltv_estimado(tenure, ticket, invoice, fator), esperado)

    def test_escalar_devolve_float_e_array_devolve_array(self):
        assert isinstance(ltv_estimado(12, 100.0, 50.0), float)
        assert isinstance(
            ltv_estimado(np.array([12]), np.array([100.0]), np.array([50.0])),
            np.ndarray)


class TestPipelineIntacto:

    def test_as_features_de_pix_seguem_completas(self):
        features = _features_pix(
            {"valor": VALOR, "id_recorrencia": "RN_intacto",
             "codigo_falha": "AM04"},
            VALOR, customer_id="RN_intacto",
        )
        from crai.ml.failure_classifier import ALL_FEATURES

        faltando = set(ALL_FEATURES) - set(features)
        assert not faltando, f"features ausentes após o refactor: {sorted(faltando)}"
        assert features["ltv_estimated"] > 0

    def test_o_readme_de_treino_existe_e_aponta_para_o_dataset(self):
        """O guia é entregável do sprint — sem ele, "é só plugar" é promessa."""
        from pathlib import Path

        readme = Path(__file__).resolve().parent.parent / "crai" / "agent" / "README_treino.md"
        assert readme.exists(), "README_treino.md não foi criado"

        texto = readme.read_text(encoding="utf-8")
        for termo in ("recovery_cycles.db", "PerfilProvider", "PIX_CODE_MAP",
                      "recovered", "ltv"):
            assert termo.lower() in texto.lower(), (
                f"o guia de treino não menciona {termo!r}")

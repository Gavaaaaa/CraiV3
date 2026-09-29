"""tests/test_ciclo_cobranca.py — Etapa 1, Bloco 1: o ciclo como fonte da verdade.

O QUE ESTE ARQUIVO MEDE (Portão 1):
  - tabelas criadas do zero e sobre um banco pré-existente chegam ao MESMO
    schema, e a migração de colunas acrescentadas funciona;
  - o BANCO recusa a 4ª tentativa no mesmo ciclo e um segundo ciclo para a
    mesma cobrança — o teste tenta e prova o erro, inclusive por INSERT cru;
  - A1: `id_cobranca_original` nunca vazio; dois clientes da mesma empresa sem
    `id_cobranca` abrem dois ciclos sem colidir;
  - reenvio de webhook depois de REINÍCIO é deduplicado — com dois processos;
  - nenhum dado pessoal nas tabelas novas — abrindo o banco cru;
  - a migração do `pix_retry_state.json` é idempotente;
  - a fachada `retry_state` mantém o contrato do agendador.
"""

import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from crai.dunning import ciclo_cobranca as cc
from crai.dunning import retry_state
from crai.dunning.pix_automatico_retry import MAX_TENTATIVAS, fim_da_janela

TENANT = "empresa_ciclo"
VALOR = 299.90
AGORA = datetime(2026, 9, 28, 10, 0, 0)
# A migração que roda na garantia de schema usa o relógio REAL para decidir
# "janela aberta ou encerrada"; os fixtures de migração são relativos a ele.
HOJE = datetime.now().replace(microsecond=0)


def _banco() -> Path:
    return Path(os.environ["CRAI_RECOVERY_DB"])


def _abrir(id_rec="RN_c1", **kw) -> dict:
    kw.setdefault("e2e_falha_original", f"E_{id_rec}")
    return cc.abrir_ciclo(TENANT, id_rec, VALOR, "insufficient_funds", AGORA, **kw)


def _plano(n=MAX_TENTATIVAS) -> list[dict]:
    return [{"numero": i + 1, "quando": AGORA + timedelta(days=i + 1),
             "valor": VALOR, "origem": "fallback_uniforme"} for i in range(n)]


# ══════════════════════════════════════════════════════════════════════════
# Schema
# ══════════════════════════════════════════════════════════════════════════

def _schema_de(caminho: Path) -> dict:
    """`sqlite_master` + `table_info` das tabelas novas, para comparar bancos."""
    with sqlite3.connect(caminho) as conn:
        master = sorted(
            (l[0], l[1], l[2], (l[3] or "").replace("\n", " ").split())
            for l in conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND tbl_name IN "
                "('ciclos_cobranca','tentativas_cobranca','eventos_vistos')"))
        colunas = {
            t: [tuple(l) for l in conn.execute(f"PRAGMA table_info({t})")]
            for t in ("ciclos_cobranca", "tentativas_cobranca", "eventos_vistos")
        }
    return {"master": master, "colunas": colunas}


class TestSchema:

    def test_do_zero_e_sobre_banco_pre_existente_chegam_ao_mesmo_schema(self, tmp_path, monkeypatch):
        from crai.dunning import recovery_log

        # Banco A: criado do zero pelo módulo.
        a = tmp_path / "a.db"
        monkeypatch.setenv("CRAI_RECOVERY_DB", str(a))
        cc.esquecer_schema_garantido()
        cc.ciclo_por_id(1)
        schema_a = _schema_de(a)

        # Banco B: já existia, com o dataset do involuntário dentro (é o caso
        # real: `recovery_cycles.db` existe desde o Sprint 6).
        b = tmp_path / "b.db"
        monkeypatch.setenv("CRAI_RECOVERY_DB", str(b))
        cc.esquecer_schema_garantido()
        recovery_log.registrar_ciclo({"tenant_id": "t", "customer_id": "c",
                                      "invoice_id": "E_pre", "amount": 10.0})
        cc.ciclo_por_id(1)
        schema_b = _schema_de(b)

        assert schema_a == schema_b
        assert {n for _, n, _, _ in schema_a["master"]} >= {
            "ciclos_cobranca", "tentativas_cobranca", "eventos_vistos",
            "idx_ciclo_mandato", "idx_tentativa_id_cobranca"}

    def test_coluna_acrescentada_depois_chega_a_um_banco_antigo(self, tmp_path, monkeypatch):
        """O mecanismo de `_COLUNAS_ACRESCENTADAS`, no padrão de `recovery_log`."""
        banco = tmp_path / "antigo.db"
        monkeypatch.setenv("CRAI_RECOVERY_DB", str(banco))
        cc.esquecer_schema_garantido()
        cc.ciclo_por_id(1)                       # cria o schema de hoje

        monkeypatch.setattr(cc, "_COLUNAS_ACRESCENTADAS",
                            (("ciclos_cobranca", "coluna_futura", "TEXT"),))
        cc.esquecer_schema_garantido()            # "outro processo, versão nova"
        cc.ciclo_por_id(1)

        with sqlite3.connect(banco) as conn:
            colunas = {l[1] for l in conn.execute("PRAGMA table_info(ciclos_cobranca)")}
        assert "coluna_futura" in colunas

        cc.esquecer_schema_garantido()
        cc.ciclo_por_id(1)                        # idempotente: não tenta acrescentar de novo

    def test_o_limite_do_check_e_a_constante_do_bacen(self):
        assert f"numero BETWEEN 1 AND {MAX_TENTATIVAS}" in cc._SCHEMA
        assert MAX_TENTATIVAS == 3

    def test_busy_timeout_espelha_o_do_ledger(self):
        from crai.churn_voluntary import retention_log
        assert cc.BUSY_TIMEOUT_MS == retention_log.BUSY_TIMEOUT_MS_PADRAO
        conn = cc._conectar()
        try:
            assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == cc.BUSY_TIMEOUT_MS
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            conn.close()


# ══════════════════════════════════════════════════════════════════════════
# O banco garante o limite
# ══════════════════════════════════════════════════════════════════════════

class TestOBancoRecusa:

    def test_quarta_tentativa_pela_api(self):
        ciclo = _abrir()
        cc.agendar_tentativas(ciclo["id"], _plano(3))
        with pytest.raises(cc.LimiteDeTentativas):
            cc.agendar_tentativas(ciclo["id"], [{"numero": 4, "quando": AGORA, "valor": VALOR}])
        assert [t["numero"] for t in cc.tentativas_do_ciclo(ciclo["id"])] == [1, 2, 3]

    def test_quarta_tentativa_por_insert_cru(self):
        ciclo = _abrir()
        cc.agendar_tentativas(ciclo["id"], _plano(3))
        with sqlite3.connect(_banco()) as conn:
            with pytest.raises(sqlite3.IntegrityError, match="numero BETWEEN 1 AND 3"):
                conn.execute(
                    "INSERT INTO tentativas_cobranca (ciclo_id, numero, agendada_para, valor) "
                    "VALUES (?, 4, ?, ?)", (ciclo["id"], AGORA.isoformat(), VALOR))
            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
                conn.execute(
                    "INSERT INTO tentativas_cobranca (ciclo_id, numero, agendada_para, valor) "
                    "VALUES (?, 3, ?, ?)", (ciclo["id"], AGORA.isoformat(), VALOR))
            with pytest.raises(sqlite3.IntegrityError):   # numero 0
                conn.execute(
                    "INSERT INTO tentativas_cobranca (ciclo_id, numero, agendada_para, valor) "
                    "VALUES (?, 0, ?, ?)", (ciclo["id"], AGORA.isoformat(), VALOR))

    def test_plano_de_quatro_volta_atras_inteiro(self):
        ciclo = _abrir()
        with pytest.raises(cc.LimiteDeTentativas):
            cc.agendar_tentativas(ciclo["id"], _plano(4))
        assert cc.tentativas_do_ciclo(ciclo["id"]) == []

    def test_segundo_ciclo_para_a_mesma_cobranca_pela_api(self):
        primeiro = _abrir("RN_dup", id_cobranca="inv_001")
        with pytest.raises(cc.CicloJaExiste) as erro:
            _abrir("RN_dup", id_cobranca="inv_001")
        assert erro.value.ciclo_id == primeiro["id"]
        # Nem depois de fechado: um ciclo por cobrança, sempre (R3).
        cc.transicionar(primeiro["id"], cc.MENSAGEM_ENVIADA, AGORA)
        cc.transicionar(primeiro["id"], cc.PERDIDO, AGORA)
        with pytest.raises(cc.CicloJaExiste):
            _abrir("RN_dup", id_cobranca="inv_001")

    def test_segundo_ciclo_por_insert_cru(self):
        ciclo = _abrir("RN_dup2", id_cobranca="inv_002")
        with sqlite3.connect(_banco()) as conn:
            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
                conn.execute(
                    """INSERT INTO ciclos_cobranca
                       (tenant_id, id_recorrencia, id_cobranca_original, valor, causa_original,
                        janela_inicio, janela_fim, estado, aberto_em, atualizado_em)
                       VALUES (?, ?, ?, ?, 'x', ?, ?, 'recobrando', ?, ?)""",
                    (ciclo["tenant_id"], "RN_dup2", "inv_002", VALOR,
                     AGORA.isoformat(), AGORA.isoformat(), AGORA.isoformat(), AGORA.isoformat()))
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):   # estado inventado
                conn.execute(
                    """INSERT INTO ciclos_cobranca
                       (tenant_id, id_recorrencia, id_cobranca_original, valor, causa_original,
                        janela_inicio, janela_fim, estado, aberto_em, atualizado_em)
                       VALUES ('t', 'r', 'c', 1, 'x', 'a', 'b', 'inventado', 'a', 'a')""")

    def test_a_mesma_cobranca_em_tenants_diferentes_sao_dois_ciclos(self):
        a = cc.abrir_ciclo("empresa_a", "RN_x", VALOR, "insufficient_funds", AGORA,
                           id_cobranca="inv_9")
        b = cc.abrir_ciclo("empresa_b", "RN_x", VALOR, "insufficient_funds", AGORA,
                           id_cobranca="inv_9")
        assert a["id"] != b["id"]

    def test_tentativa_de_ciclo_inexistente_e_recusada(self):
        with pytest.raises(cc.LimiteDeTentativas, match="FOREIGN KEY|recusou"):
            cc.agendar_tentativas(999_999, _plano(1))


class TestA1IdentidadeNuncaVazia:

    def test_dois_clientes_sem_id_cobranca_abrem_dois_ciclos(self):
        """A1: sem `id_cobranca`, a identidade é o e2e da falha que abriu."""
        a = cc.abrir_ciclo(TENANT, "RN_cliente_a", VALOR, "insufficient_funds", AGORA,
                           e2e_falha_original="E_a_001")
        b = cc.abrir_ciclo(TENANT, "RN_cliente_b", VALOR, "insufficient_funds", AGORA,
                           e2e_falha_original="E_b_001")
        assert a["id_cobranca_original"] == "E_a_001"
        assert b["id_cobranca_original"] == "E_b_001"
        assert a["id"] != b["id"]

    def test_id_cobranca_ganha_do_e2e(self):
        c = _abrir("RN_id", id_cobranca="inv_77", e2e_falha_original="E_77")
        assert c["id_cobranca_original"] == "inv_77"
        assert c["e2e_falha_original"] == "E_77"

    def test_sem_id_e_sem_e2e_a_identidade_e_derivada_e_nao_colide(self):
        a = cc.abrir_ciclo(TENANT, "RN_anon", VALOR, "processing_error", AGORA)
        b = cc.abrir_ciclo(TENANT, "RN_anon", VALOR, "processing_error",
                           AGORA + timedelta(seconds=1))
        assert a["id_cobranca_original"].startswith("anon:RN_anon:")
        assert a["id_cobranca_original"] != b["id_cobranca_original"]
        assert a["id_cobranca_original"] != ""

    def test_o_e2e_em_branco_nao_conta_como_identidade(self):
        c = cc.abrir_ciclo(TENANT, "RN_branco", VALOR, "processing_error", AGORA,
                           id_cobranca="  ", e2e_falha_original="")
        assert c["id_cobranca_original"].startswith("anon:")


# ══════════════════════════════════════════════════════════════════════════
# Estados e tentativas
# ══════════════════════════════════════════════════════════════════════════

class TestEstados:

    def test_janela_gravada_na_abertura_e_presa(self):
        c = _abrir("RN_j")
        assert c["janela_inicio"] == AGORA.isoformat()
        assert c["janela_fim"] == fim_da_janela(AGORA).isoformat()
        c2 = cc.atualizar(c["id"], AGORA + timedelta(days=1), recovery_score=42.0)
        assert (c2["janela_inicio"], c2["janela_fim"]) == (c["janela_inicio"], c["janela_fim"])

    @pytest.mark.parametrize("de, para", [
        (cc.RECOBRANDO, cc.MENSAGEM_ENVIADA), (cc.RECOBRANDO, cc.RECUPERADO),
        (cc.RECOBRANDO, cc.DESCARTADO), (cc.MENSAGEM_ENVIADA, cc.RECUPERADO),
        (cc.MENSAGEM_ENVIADA, cc.PERDIDO),
    ])
    def test_transicoes_permitidas_gravam_a_data(self, de, para):
        c = _abrir("RN_t", estado=de)
        depois = cc.transicionar(c["id"], para, AGORA + timedelta(hours=1))
        assert depois["estado"] == para
        assert depois[cc._COLUNA_DA_TRANSICAO[para]] == (AGORA + timedelta(hours=1)).isoformat()

    @pytest.mark.parametrize("de, para", [
        (cc.RECUPERADO, cc.RECOBRANDO), (cc.RECUPERADO, cc.PERDIDO),
        (cc.PERDIDO, cc.RECUPERADO), (cc.DESCARTADO, cc.MENSAGEM_ENVIADA),
        (cc.MENSAGEM_ENVIADA, cc.RECOBRANDO), (cc.RECOBRANDO, cc.PERDIDO),
    ])
    def test_transicoes_proibidas_levantam(self, de, para):
        c = _abrir("RN_p", estado=de)
        with pytest.raises(cc.TransicaoInvalida):
            cc.transicionar(c["id"], para, AGORA)
        assert cc.ciclo_por_id(c["id"])["estado"] == de

    def test_descarte_leva_o_motivo(self):
        c = _abrir("RN_d")
        d = cc.descartar(c["id"], "score_abaixo_do_corte", AGORA)
        assert (d["estado"], d["motivo_descarte"]) == (cc.DESCARTADO, "score_abaixo_do_corte")
        assert d["descartado_em"] == AGORA.isoformat()

    def test_prazo_de_recuperacao_e_separado_da_janela_do_bacen(self):
        """A3: a constante existe, é maior que a janela, e está justificada."""
        assert cc.PRAZO_RECUPERACAO_DIAS == 30
        assert cc.PRAZO_RECUPERACAO_DIAS > cc.JANELA_DIAS
        assert cc.MENSAGEM_ENVIADA in cc.TRANSICOES and cc.RECUPERADO in cc.TRANSICOES[cc.MENSAGEM_ENVIADA]


class TestTentativas:

    def test_agendar_disparar_resultado_e_contagem_executada(self):
        c = _abrir("RN_e")
        cc.agendar_tentativas(c["id"], _plano(3))
        assert cc.tentativas_executadas(c["id"]) == 0

        assert cc.marcar_disparada(c["id"], 1, "ch_1", AGORA + timedelta(days=1)) is True
        assert cc.marcar_disparada(c["id"], 1, "ch_1b", AGORA) is False, "disparo duplo"
        assert cc.tentativas_executadas(c["id"]) == 1

        assert cc.registrar_resultado(c["id"], 1, cc.FALHOU, AGORA, codigo="AM04", e2e="E_r1")
        assert cc.registrar_resultado(c["id"], 1, cc.PAGA, AGORA) is False, "resultado só uma vez"
        t1 = cc.tentativas_do_ciclo(c["id"])[0]
        assert (t1["resultado"], t1["codigo_resultado"], t1["e2e_resultado"]) == ("falhou", "AM04", "E_r1")

    def test_regravar_o_plano_preserva_o_que_ja_saiu(self):
        c = _abrir("RN_m")
        cc.agendar_tentativas(c["id"], _plano(3))
        cc.marcar_disparada(c["id"], 1, "ch_1", AGORA)
        novo = [{"numero": n, "quando": AGORA + timedelta(days=10 + n), "valor": VALOR,
                 "origem": "payday_engine"} for n in (1, 2, 3)]
        tentativas = cc.agendar_tentativas(c["id"], novo)
        assert tentativas[0]["disparada_em"] and tentativas[0]["agendada_para"] == _plano(3)[0]["quando"].isoformat()
        assert tentativas[1]["agendada_para"] == novo[1]["quando"].isoformat()

    def test_cancelar_pendentes_nao_toca_na_disparada(self):
        c = _abrir("RN_k")
        cc.agendar_tentativas(c["id"], _plano(3))
        cc.marcar_disparada(c["id"], 1, "ch_1", AGORA)
        assert cc.cancelar_pendentes(c["id"], "recuperado", AGORA) == 2
        ts = cc.tentativas_do_ciclo(c["id"])
        assert [t["resultado"] for t in ts] == ["pendente", "cancelada", "cancelada"]
        assert ts[1]["motivo_cancelamento"] == "recuperado"
        assert cc.ciclos_recobrando_com_pendentes() == []

    def test_declarada_conta_como_executada(self):
        c = _abrir("RN_decl")
        with sqlite3.connect(_banco()) as conn:
            conn.execute(
                "INSERT INTO tentativas_cobranca (ciclo_id, numero, agendada_para, origem_data, "
                "valor, resultado) VALUES (?, 1, ?, 'declarada', ?, 'falhou')",
                (c["id"], AGORA.isoformat(), VALOR))
        assert cc.tentativas_executadas(c["id"]) == 1


# ══════════════════════════════════════════════════════════════════════════
# A fachada retry_state mantém o contrato do agendador
# ══════════════════════════════════════════════════════════════════════════

class TestFachadaRetryState:

    def test_registro_tem_a_forma_de_sempre_e_o_ciclo(self):
        retry_state.save_retry_state("RN_f", VALOR, _plano(3), fim_da_janela(AGORA),
                                     tenant_id=TENANT, e2e_id="E_f")
        r = retry_state.get_retry_state("RN_f", tenant_id=TENANT)
        assert {"customer_id", "tenant_id", "e2e_id", "valor_original", "pix_janela_ate",
                "atualizado_em", "tentativas", "ciclo_id"} <= set(r)
        assert r["e2e_id"] == "E_f" and r["pix_janela_ate"] == fim_da_janela(AGORA).isoformat()
        assert [t["numero"] for t in r["tentativas"]] == [1, 2, 3]
        assert {"numero", "quando", "valor", "origem", "disparada_em", "id_cobranca",
                "resultado"} <= set(r["tentativas"][0])
        ciclo = cc.ciclo_por_id(r["ciclo_id"])
        assert ciclo["id_cobranca_original"] == "E_f" and ciclo["origem"] == cc.ORIGEM_PLANO

    def test_segundo_plano_da_mesma_janela_continua_o_ciclo(self):
        prazo = fim_da_janela(AGORA)
        retry_state.save_retry_state("RN_g", VALOR, _plano(3), prazo, tenant_id=TENANT, e2e_id="E_g1")
        retry_state.marcar_disparada("RN_g", 1, "ch_g1", AGORA, tenant_id=TENANT)
        retry_state.save_retry_state("RN_g", VALOR, _plano(3), prazo, tenant_id=TENANT, e2e_id="E_g2")
        assert len(cc.ciclos_do_mandato(TENANT, "RN_g")) == 1
        r = retry_state.get_retry_state("RN_g", tenant_id=TENANT)
        assert r["tentativas"][0]["disparada_em"] and r["tentativas"][0]["id_cobranca"] == "ch_g1"

    def test_janela_nova_abre_ciclo_novo(self):
        retry_state.save_retry_state("RN_h", VALOR, _plano(3), fim_da_janela(AGORA),
                                     tenant_id=TENANT, e2e_id="E_h1")
        depois = AGORA + timedelta(days=40)
        retry_state.save_retry_state("RN_h", VALOR, _plano(3), fim_da_janela(depois),
                                     tenant_id=TENANT, e2e_id="E_h2")
        assert len(cc.ciclos_do_mandato(TENANT, "RN_h")) == 2

    def test_cancelada_nunca_e_devida_e_pendentes_somem_do_agendador(self):
        retry_state.save_retry_state("RN_i", VALOR, _plano(3), fim_da_janela(AGORA),
                                     tenant_id=TENANT, e2e_id="E_i")
        r = retry_state.get_retry_state("RN_i", tenant_id=TENANT)
        cc.cancelar_pendentes(r["ciclo_id"], "autorizacao_revogada", AGORA)
        r = retry_state.get_retry_state("RN_i", tenant_id=TENANT)
        assert retry_state.tentativas_devidas(r, AGORA + timedelta(days=30)) == []
        assert retry_state.planos_pendentes() == []


# ══════════════════════════════════════════════════════════════════════════
# Migração do JSON (1.3)
# ══════════════════════════════════════════════════════════════════════════

def _json_real(tmp_path, monkeypatch, janela_ate: datetime) -> Path:
    """O formato exato do `app/data/pix_retry_state.json` de 27/09/2026."""
    arquivo = tmp_path / "pix_retry_state.json"
    dados = {
        "demo_tenant:RN_maria_001": {
            "customer_id": "RN_maria_001", "tenant_id": "demo_tenant",
            "e2e_id": "E60701190RN_maria_001", "valor_original": 299.9,
            "pix_janela_ate": janela_ate.isoformat(),
            "atualizado_em": (janela_ate - timedelta(days=7)).isoformat(),
            "tentativas": [
                {"numero": n, "quando": (janela_ate - timedelta(days=6 - n)).isoformat(),
                 "valor": 299.9, "origem": "payday_engine",
                 "disparada_em": (janela_ate - timedelta(days=6 - n, hours=-1)).isoformat(),
                 "id_cobranca": f"ch_sim_maria_{n}"} for n in (1, 2, 3)],
        },
        "demo_tenant:RN_joao_002": {
            "customer_id": "RN_joao_002", "tenant_id": "demo_tenant",
            "e2e_id": "E60701190RN_joao_002", "valor_original": 149.0,
            "pix_janela_ate": janela_ate.isoformat(),
            "atualizado_em": (janela_ate - timedelta(days=7)).isoformat(),
            "tentativas": [
                {"numero": 3, "quando": (janela_ate - timedelta(days=6)).isoformat(),
                 "valor": 149.0, "origem": "payday_engine",
                 "disparada_em": (janela_ate - timedelta(days=6, hours=-1)).isoformat(),
                 "id_cobranca": "ch_sim_joao_3"}],
        },
        "RN_sem_tenant": {
            "customer_id": "RN_sem_tenant", "tenant_id": None, "e2e_id": None,
            "valor_original": 50.0, "pix_janela_ate": janela_ate.isoformat(),
            "atualizado_em": (janela_ate - timedelta(days=7)).isoformat(),
            "tentativas": [{"numero": 1, "quando": (janela_ate - timedelta(days=5)).isoformat(),
                            "valor": 50.0, "origem": "fallback_uniforme",
                            "disparada_em": None, "id_cobranca": None}],
        },
    }
    arquivo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    monkeypatch.setenv(retry_state.ENV_CAMINHO, str(arquivo))
    return arquivo


class TestMigracaoDoJson:

    def test_rodar_duas_vezes_nao_duplica_nada(self, tmp_path, monkeypatch):
        janela_ate = HOJE + timedelta(days=6)       # janela ainda aberta
        _json_real(tmp_path, monkeypatch, janela_ate)
        cc.esquecer_schema_garantido()             # "primeira execução" do processo

        primeira = cc.migrar_retry_state_json(HOJE)
        segunda = cc.migrar_retry_state_json(HOJE)
        cc.esquecer_schema_garantido()
        terceira = cc.migrar_retry_state_json(HOJE)

        assert (primeira, segunda, terceira) in {(3, 0, 0), (0, 0, 0)}, (
            "a primeira chamada migra (ou a garantia de schema já migrou); as demais, nada")
        with sqlite3.connect(_banco()) as conn:
            assert conn.execute("SELECT COUNT(*) FROM ciclos_cobranca").fetchone()[0] == 3
            assert conn.execute("SELECT COUNT(*) FROM tentativas_cobranca").fetchone()[0] == 3 + 3 + 1

    def test_o_que_cada_plano_vira(self, tmp_path, monkeypatch):
        janela_ate = HOJE + timedelta(days=6)
        _json_real(tmp_path, monkeypatch, janela_ate)
        cc.esquecer_schema_garantido()
        cc.migrar_retry_state_json(HOJE)

        maria = cc.ciclo_da_cobranca("demo_tenant", "E60701190RN_maria_001")
        assert maria["estado"] == cc.RECOBRANDO and maria["origem"] == cc.ORIGEM_MIGRACAO
        assert maria["janela_fim"] == janela_ate.isoformat()
        assert maria["causa_original"] == "desconhecida"
        ts = cc.tentativas_do_ciclo(maria["id"])
        assert [(t["numero"], t["resultado"], bool(t["disparada_em"])) for t in ts] == [
            (1, "pendente", True), (2, "pendente", True), (3, "pendente", True)]
        assert ts[0]["id_cobranca"] == "ch_sim_maria_1"
        assert cc.tentativas_executadas(maria["id"]) == 3

        joao = cc.ciclo_da_cobranca("demo_tenant", "E60701190RN_joao_002")
        ts = cc.tentativas_do_ciclo(joao["id"])
        assert [(t["numero"], t["resultado"], t["origem_data"]) for t in ts] == [
            (1, "falhou", cc.ORIGEM_DECLARADA), (2, "falhou", cc.ORIGEM_DECLARADA),
            (3, "pendente", "payday_engine")]
        assert cc.tentativas_executadas(joao["id"]) == 3, "a migração não afrouxa o limite"

        anon = cc.ciclos_do_mandato(cc.TENANT_PADRAO, "RN_sem_tenant")[0]
        assert anon["id_cobranca_original"].startswith("anon:RN_sem_tenant:")
        assert retry_state.planos_pendentes()[0]["customer_id"] == "RN_sem_tenant"

    def test_janela_ja_encerrada_vira_perdido_sem_pendentes(self, tmp_path, monkeypatch):
        _json_real(tmp_path, monkeypatch, HOJE - timedelta(days=1))
        cc.esquecer_schema_garantido()
        cc.migrar_retry_state_json(HOJE)
        maria = cc.ciclo_da_cobranca("demo_tenant", "E60701190RN_maria_001")
        assert maria["estado"] == cc.PERDIDO and maria["perdido_em"]
        assert {t["resultado"] for t in cc.tentativas_do_ciclo(maria["id"])} == {"sem_retorno"}
        anon = cc.ciclos_do_mandato(cc.TENANT_PADRAO, "RN_sem_tenant")[0]
        assert [t["resultado"] for t in cc.tentativas_do_ciclo(anon["id"])] == ["cancelada"]
        assert retry_state.planos_pendentes() == []

    def test_linha_recuperada_no_dataset_vira_ciclo_recuperado(self, tmp_path, monkeypatch):
        from crai.dunning import recovery_log
        recovery_log.registrar_ciclo({"tenant_id": "demo_tenant", "customer_id": "RN_maria_001",
                                      "invoice_id": "E60701190RN_maria_001", "amount": 299.9,
                                      "failure_cause": "insufficient_funds"})
        recovery_log.registrar_recuperacao("RN_maria_001", "E60701190RN_maria_001", 299.9,
                                           "demo_tenant", tentativas_usadas=3)
        _json_real(tmp_path, monkeypatch, HOJE + timedelta(days=6))
        cc.esquecer_schema_garantido()
        cc.migrar_retry_state_json(HOJE)
        maria = cc.ciclo_da_cobranca("demo_tenant", "E60701190RN_maria_001")
        assert maria["estado"] == cc.RECUPERADO and maria["fee"] > 0
        assert maria["causa_original"] == "insufficient_funds"
        assert [t["resultado"] for t in cc.tentativas_do_ciclo(maria["id"])] == [
            "sem_retorno", "sem_retorno", "paga"]

    def test_json_ilegivel_nao_derruba_e_nao_concede_nada(self, tmp_path, monkeypatch):
        arquivo = tmp_path / "corrompido.json"
        arquivo.write_text("{ isto não é json", encoding="utf-8")
        monkeypatch.setenv(retry_state.ENV_CAMINHO, str(arquivo))
        cc.esquecer_schema_garantido()
        assert cc.migrar_retry_state_json(HOJE) == 0
        assert retry_state.planos_pendentes() == []

    def test_o_json_nao_e_reescrito(self, tmp_path, monkeypatch):
        arquivo = _json_real(tmp_path, monkeypatch, HOJE + timedelta(days=6))
        antes = arquivo.read_bytes()
        cc.esquecer_schema_garantido()
        cc.migrar_retry_state_json(HOJE)
        retry_state.save_retry_state("RN_novo", VALOR, _plano(2), fim_da_janela(AGORA),
                                     tenant_id=TENANT, e2e_id="E_novo")
        retry_state.limpar_tudo()
        assert arquivo.read_bytes() == antes


# ══════════════════════════════════════════════════════════════════════════
# Deduplicação fora da RAM (1.2)
# ══════════════════════════════════════════════════════════════════════════

class TestDeduplicacaoPersistida:

    def test_uma_instancia_nova_ve_o_que_a_anterior_gravou(self):
        """Uma instância nova da janela é o que um processo reiniciado tem."""
        from crai.api.idempotencia import JanelaDeIdempotencia
        antes = JanelaDeIdempotencia("pix_falha", persistente=True)
        assert antes.registrar_se_novo('["RN_r", "E_r"]', AGORA) is True
        depois = JanelaDeIdempotencia("pix_falha", persistente=True)   # "reinício"
        assert depois.registrar_se_novo('["RN_r", "E_r"]', AGORA + timedelta(hours=1)) is False
        assert len(depois) == 1

    def test_ttl_de_sete_dias(self):
        from crai.api.idempotencia import JanelaDeIdempotencia, TTL_PADRAO
        janela = JanelaDeIdempotencia("pix_falha", persistente=True)
        assert janela.registrar_se_novo("k", AGORA) is True
        assert janela.registrar_se_novo("k", AGORA + TTL_PADRAO - timedelta(seconds=1)) is False
        assert janela.registrar_se_novo("k", AGORA + TTL_PADRAO + timedelta(seconds=1)) is True

    def test_escopos_separados_e_limpeza_por_escopo(self):
        from crai.api.idempotencia import CICLOS_FECHADOS, EVENTOS_DE_FALHA
        assert EVENTOS_DE_FALHA.persistente and CICLOS_FECHADOS.persistente
        assert EVENTOS_DE_FALHA.registrar_se_novo("mesma", AGORA) is True
        assert CICLOS_FECHADOS.registrar_se_novo("mesma", AGORA) is True
        EVENTOS_DE_FALHA.limpar()
        assert EVENTOS_DE_FALHA.registrar_se_novo("mesma", AGORA) is True
        assert CICLOS_FECHADOS.registrar_se_novo("mesma", AGORA) is False

    def test_banco_indisponivel_cai_para_a_memoria_sem_levantar(self, monkeypatch):
        from crai.api.idempotencia import JanelaDeIdempotencia

        def explode(*a, **k):
            raise sqlite3.OperationalError("disco")
        monkeypatch.setattr(cc, "registrar_evento_se_novo", explode)
        janela = JanelaDeIdempotencia("pix_falha", persistente=True)
        assert janela.registrar_se_novo("z", AGORA) is True
        assert janela.registrar_se_novo("z", AGORA) is False, "a memória do processo ainda segura"

    def test_a_janela_de_clientes_continua_em_memoria(self):
        from crai.api.idempotencia import CLIENTES_API
        assert CLIENTES_API.persistente is False


# O reenvio depois de um REINÍCIO, com dois processos de verdade.
PIX_SECRET = "segredo_de_teste_ciclo"

_SCRIPT_WEBHOOK = r'''
import json, sys, hmac, hashlib, time
from fastapi.testclient import TestClient
from crai.api import app as app_module

async def stub(event, payment_method="card", **kw):
    print("PIPELINE_RODOU")
app_module._run_involuntary_pipeline = stub

def assinar(corpo: bytes):
    t = int(time.time())
    return {"x-pix-signature": f"t={t},v1=" + hmac.new(
        b"%s", f"{t}.".encode() + corpo, hashlib.sha256).hexdigest()}

falha = json.dumps({"event": "automatic_pix.charge_failed",
                    "data": {"valor": 299.90, "automatic_pix": {"recurrence_id": "RN_restart"},
                             "pix": {"end_to_end_id": "E_restart_falha"}}}).encode()
pago = json.dumps({"event": "automatic_pix.charge_paid",
                   "data": {"valor": 299.90, "automatic_pix": {"recurrence_id": "RN_restart"},
                            "pix": {"end_to_end_id": "E_restart_pago"}}}).encode()
with TestClient(app_module.app) as c:
    r1 = c.post("/webhooks/pix-automatico", content=falha, headers=assinar(falha)).json()
    r2 = c.post("/webhooks/pix-automatico", content=pago, headers=assinar(pago)).json()
print("RESULTADO", json.dumps({"falha": r1, "pago": r2}))
''' % PIX_SECRET


def _processo(env: dict) -> dict:
    saida = subprocess.run([sys.executable, "-c", _SCRIPT_WEBHOOK], env=env,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300,
                           cwd=str(Path(__file__).resolve().parent.parent))
    assert saida.returncode == 0, saida.stderr[-3000:]
    linha = [l for l in saida.stdout.splitlines() if l.startswith("RESULTADO ")]
    assert linha, saida.stdout[-2000:]
    return {**json.loads(linha[0][len("RESULTADO "):]),
            "pipeline_rodou": "PIPELINE_RODOU" in saida.stdout}


class TestReenvioDepoisDeReinicio:

    def test_dois_processos_o_segundo_reconhece_o_reenvio(self, tmp_path):
        """P1 recebe a falha e a confirmação; P2 é o backend reiniciado e
        recebe as MESMAS entregas. Antes desta etapa (dedup em RAM), P2
        rodava o pipeline de novo e tentava fechar o ciclo de novo."""
        env = {**os.environ,
               "PIX_WEBHOOK_SECRET": PIX_SECRET, "ENV": "development",
               "CRAI_RECOVERY_DB": str(tmp_path / "recuperacoes.db"),
               "CRAI_RETENTION_DB": str(tmp_path / "retencao.db"),
               "CRAI_RETRY_STATE": str(tmp_path / "planos.json"),
               "CRAI_CLIENTES_DB": str(tmp_path / "clientes.db"),
               "PYTHONIOENCODING": "utf-8"}
        env.pop("SUPABASE_DB_URL", None)
        env.pop("CRAI_SIMULATE_OUTCOMES", None)

        p1 = _processo(env)
        assert p1["falha"]["pipeline"] is True and p1["pipeline_rodou"]
        assert p1["pago"]["ciclo"] == "sem_ciclo_aberto"    # o stub não abriu ciclo: (a) já registrou

        p2 = _processo(env)                                  # reinício
        assert p2["falha"]["pipeline"] is False
        assert p2["falha"]["motivo"] == "evento_ja_processado"
        assert p2["pipeline_rodou"] is False, "o pipeline rodou de novo depois do reinício"
        assert p2["pago"]["ciclo"] == "reenvio"

        with sqlite3.connect(tmp_path / "recuperacoes.db") as conn:
            escopos = sorted(l[0] for l in conn.execute("SELECT escopo FROM eventos_vistos"))
        assert escopos == ["pix_falha", "pix_recuperacao"]


# ══════════════════════════════════════════════════════════════════════════
# Nenhum dado pessoal (abre o banco cru)
# ══════════════════════════════════════════════════════════════════════════

CHAVE_PIX = "11122233344"
NOME = "Fulana Pessoa Real"
EMAIL = "fulana@exemplo.com"
TELEFONE = "+5511999998888"


class TestSemDadoPessoal:

    def test_nada_do_pagador_chega_as_tabelas_novas(self, monkeypatch):
        """Roda o pipeline REAL pelo webhook assinado com um payload cheio de
        dado pessoal, e depois abre o banco cru e procura."""
        from fastapi.testclient import TestClient
        from crai.api import app as app_module

        monkeypatch.setenv("PIX_WEBHOOK_SECRET", PIX_SECRET)
        corpo = json.dumps({
            "event": "automatic_pix.charge_failed",
            "data": {"id": "inv_pii_001", "valor": 299.90,
                     "automatic_pix": {"recurrence_id": "RN_pii", "failure_reason": "AM04"},
                     "pix": {"end_to_end_id": "E_pii_001",
                             "payer": {"pix_key": CHAVE_PIX, "name": NOME, "email": EMAIL,
                                       "phone": TELEFONE, "cpf": CHAVE_PIX, "ispb": "60701190"}}},
        }).encode()
        t = int(time.time())
        assinatura = hmac.new(PIX_SECRET.encode(), f"{t}.".encode() + corpo,
                              hashlib.sha256).hexdigest()
        with TestClient(app_module.app) as c:
            r = c.post("/webhooks/pix-automatico", content=corpo,
                       headers={"x-pix-signature": f"t={t},v1={assinatura}"})
        assert r.status_code == 200 and r.json()["pipeline"] is True

        with sqlite3.connect(_banco()) as conn:
            conn.row_factory = sqlite3.Row
            linhas = []
            for tabela in ("ciclos_cobranca", "tentativas_cobranca", "eventos_vistos"):
                for l in conn.execute(f"SELECT * FROM {tabela}"):
                    linhas.append(json.dumps(dict(l), ensure_ascii=False, default=str))
            colunas = {l[1].lower() for tabela in ("ciclos_cobranca", "tentativas_cobranca",
                                                    "eventos_vistos")
                       for l in conn.execute(f"PRAGMA table_info({tabela})")}
        with sqlite3.connect(_banco()) as conn:
            ciclos = conn.execute("SELECT COUNT(*) FROM ciclos_cobranca").fetchone()[0]
            tentativas = conn.execute("SELECT COUNT(*) FROM tentativas_cobranca").fetchone()[0]
        assert ciclos >= 1 and tentativas >= 1, (
            "o pipeline não gravou ciclo nem tentativa — o teste não mediu nada")
        texto = "\n".join(linhas)
        for pii in (CHAVE_PIX, NOME, EMAIL, TELEFONE, "Fulana", "exemplo.com"):
            assert pii not in texto, f"dado pessoal {pii!r} chegou às tabelas do ciclo"
        proibidas = {"pix_key", "chave_pix", "cpf", "email", "nome", "name", "telefone",
                     "phone", "endereco", "payer_key"}
        assert not (colunas & proibidas), colunas & proibidas

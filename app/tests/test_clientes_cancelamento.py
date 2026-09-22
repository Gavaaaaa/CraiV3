"""tests/test_clientes_cancelamento.py — o cancelamento mora em `clientes_importados`.

O que o Bloco C promete, e o que aqui é verificado:

  (a) MIGRAÇÃO: base criada do zero e base pré-existente (sem as colunas
      novas) chegam ao MESMO schema — nos dois back-ends. `CREATE TABLE IF
      NOT EXISTS` sozinho não acrescentaria coluna a tabela que já existe.
  (b) `gravar()` em lote NÃO reativa quem cancelou; `upsert_um` também não,
      a não ser com `reativar=True`.
  (c) `cancelar` é idempotente e preserva a primeira data; a linha fica.
  (d) `atualizar_parcial` só toca no que veio.
  (e) `listar()` exclui cancelados por padrão; `incluir_cancelados=True`
      traz tudo; os chamadores (scoring em lote, insights) herdam o padrão.
  (f) isolamento por tenant nas quatro operações.

POSTGRES SEM POSTGRES. Não há servidor local e o `conftest` apaga
`SUPABASE_DB_URL` de propósito (a suíte nunca escreve na base real de uma
empresa). O ramo Postgres é exercitado com um `psycopg2.connect` falso que
atende a um SQLite em memória: traduz `%s` → `?`, aceita `ADD COLUMN IF NOT
EXISTS` engolindo "duplicate column", e GRAVA todo SQL recebido. É o
suficiente para provar que o módulo emite `ADD COLUMN IF NOT EXISTS` (e não
`PRAGMA`) quando `_postgres=True`, e que o SQL das quatro operações passa
inteiro pela tradução de placeholder. A sintaxe do DDL no Postgres real é a
documentada (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, PG >= 9.6).

Uso:
    pytest tests/test_clientes_cancelamento.py -v
"""

import sqlite3

import pytest

from crai.churn_voluntary import batch_scoring, insights_unificados
from crai.churn_voluntary import clientes_importados as ci

TENANT = "empresa-a"
OUTRO = "empresa-b"

# O DDL de ANTES do Bloco C, para simular uma base já criada em produção.
DDL_ANTIGO = f"""
CREATE TABLE IF NOT EXISTS {ci.TABELA} (
    tenant_id           TEXT NOT NULL,
    customer_id_externo TEXT NOT NULL,
    mrr                 DOUBLE PRECISION NOT NULL,
    billing_profile     TEXT NOT NULL,
    days_since_last     DOUBLE PRECISION,
    features_used_30d   DOUBLE PRECISION,
    email               TEXT,
    importado_em        TEXT NOT NULL,
    PRIMARY KEY (tenant_id, customer_id_externo)
);
"""

COLUNAS_NOVAS = tuple(c for c, _ in ci.COLUNAS_MIGRACAO)


def _cliente(cid, mrr=100.0, dias=None, uso=None, perfil="PJ", email=None):
    return {"customer_id_externo": cid, "mrr": mrr, "billing_profile": perfil,
            "days_since_last": dias, "features_used_30d": uso, "email": email}


def _colunas_sqlite(caminho) -> set:
    con = sqlite3.connect(caminho)
    try:
        return {l[1] for l in con.execute(f"PRAGMA table_info({ci.TABELA})")}
    finally:
        con.close()


@pytest.fixture
def caminho_sqlite(monkeypatch, tmp_path):
    caminho = str(tmp_path / "clientes_migracao.db")
    monkeypatch.setenv("CRAI_CLIENTES_DB", caminho)
    ci.esquecer_schema_garantido()
    return caminho


# ── Postgres falso: psycopg2.connect que atende a um SQLite em memória ────

class _CursorPg:
    def __init__(self, con: sqlite3.Connection, registro: list):
        self._con = con
        self._registro = registro
        self._cur = None

    def execute(self, sql, params=()):
        self._registro.append(sql)
        assert "?" not in sql, "o módulo tem que ter traduzido `?` para `%s`"
        sql_sqlite = sql.replace("%s", "?")
        if "ADD COLUMN IF NOT EXISTS" in sql_sqlite:
            try:
                self._cur = self._con.execute(
                    sql_sqlite.replace("IF NOT EXISTS", ""), params)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e):
                    raise
                self._cur = None
            return
        self._cur = self._con.execute(sql_sqlite, params)

    @property
    def description(self):
        return None if self._cur is None else self._cur.description

    def fetchall(self):
        return self._cur.fetchall()


class _ConexaoPg:
    def __init__(self, con: sqlite3.Connection, registro: list):
        self._con = con
        self._registro = registro

    def cursor(self):
        return _CursorPg(self._con, self._registro)

    def commit(self):
        self._con.commit()

    def rollback(self):
        self._con.rollback()

    def close(self):
        pass                                   # a mesma memória serve a próxima conexão


@pytest.fixture
def postgres_falso(monkeypatch):
    """Ativa o ramo Postgres do módulo apontando `psycopg2.connect` para um
    SQLite em memória compartilhado. Devolve (conexão sqlite, SQL recebido)."""
    import psycopg2

    con = sqlite3.connect(":memory:")
    registro: list = []
    monkeypatch.setattr(psycopg2, "connect", lambda url: _ConexaoPg(con, registro))
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://falso/teste")
    ci.esquecer_schema_garantido()
    yield con, registro
    ci.esquecer_schema_garantido()


def _colunas_memoria(con) -> set:
    return {l[1] for l in con.execute(f"PRAGMA table_info({ci.TABELA})")}


# ── (a) Migração ─────────────────────────────────────────────────────────

class TestMigracaoSqlite:
    def test_base_do_zero_tem_as_colunas_novas(self, caminho_sqlite):
        ci.gravar(TENANT, [_cliente("c-1")])
        assert _colunas_sqlite(caminho_sqlite) == set(ci.COLUNAS)

    def test_base_pre_existente_ganha_as_colunas_e_mantem_as_linhas(self, caminho_sqlite):
        con = sqlite3.connect(caminho_sqlite)
        con.execute(DDL_ANTIGO)
        con.execute(f"INSERT INTO {ci.TABELA} VALUES (?,?,?,?,?,?,?,?)",
                    (TENANT, "antigo", 50.0, "PJ", 3.0, 4.0, None, "2025-01-01T00:00:00+00:00"))
        con.commit()
        con.close()
        assert not (set(COLUNAS_NOVAS) & _colunas_sqlite(caminho_sqlite))

        ci.esquecer_schema_garantido()
        (linha,) = ci.listar(TENANT)               # a primeira conexão migra
        assert _colunas_sqlite(caminho_sqlite) == set(ci.COLUNAS)
        assert linha["customer_id_externo"] == "antigo"
        assert linha["cancelado_em"] is None
        assert linha["motivo_cancelamento"] is None
        assert linha["atualizado_em"] is None

    def test_do_zero_e_pre_existente_chegam_ao_mesmo_schema(self, monkeypatch, tmp_path):
        nova = str(tmp_path / "nova.db")
        velha = str(tmp_path / "velha.db")
        con = sqlite3.connect(velha)
        con.execute(DDL_ANTIGO)
        con.commit()
        con.close()
        for caminho in (nova, velha):
            monkeypatch.setenv("CRAI_CLIENTES_DB", caminho)
            ci.esquecer_schema_garantido()
            ci.gravar(TENANT, [_cliente("c-1")])
        assert _colunas_sqlite(nova) == _colunas_sqlite(velha) == set(ci.COLUNAS)

    def test_migracao_e_idempotente(self, caminho_sqlite):
        for _ in range(3):
            ci.esquecer_schema_garantido()
            ci.gravar(TENANT, [_cliente("c-1")])
        assert _colunas_sqlite(caminho_sqlite) == set(ci.COLUNAS)

    def test_esquecer_schema_zera_o_estado_da_migracao(self, caminho_sqlite, monkeypatch):
        """Sem o `esquecer`, a segunda base (mesmo caminho, arquivo recriado)
        ficaria sem migração — é disso que os testes dependem."""
        chamadas = []
        original = ci._migrar_schema
        monkeypatch.setattr(ci, "_migrar_schema", lambda conn: (chamadas.append(1), original(conn)))
        ci.gravar(TENANT, [_cliente("c-1")])
        ci.gravar(TENANT, [_cliente("c-2")])
        assert len(chamadas) == 1                   # uma vez por destino
        ci.esquecer_schema_garantido()
        ci.gravar(TENANT, [_cliente("c-3")])
        assert len(chamadas) == 2                   # zerou: rodou de novo


class TestMigracaoPostgres:
    def test_base_do_zero_emite_add_column_if_not_exists(self, postgres_falso):
        con, registro = postgres_falso
        ci.gravar(TENANT, [_cliente("c-1")])
        alters = [s for s in registro if s.startswith("ALTER TABLE")]
        assert len(alters) == len(ci.COLUNAS_MIGRACAO)
        for (coluna, tipo), sql in zip(ci.COLUNAS_MIGRACAO, alters):
            assert sql == f"ALTER TABLE {ci.TABELA} ADD COLUMN IF NOT EXISTS {coluna} {tipo}"
        assert not any("PRAGMA" in s for s in registro), "PRAGMA é só do SQLite"
        assert _colunas_memoria(con) == set(ci.COLUNAS)

    def test_base_pre_existente_chega_ao_mesmo_schema(self, postgres_falso):
        con, registro = postgres_falso
        con.execute(DDL_ANTIGO)
        con.execute(f"INSERT INTO {ci.TABELA} VALUES (?,?,?,?,?,?,?,?)",
                    (TENANT, "antigo", 50.0, "PJ", 3.0, 4.0, None, "2025-01-01T00:00:00+00:00"))
        con.commit()
        assert not (set(COLUNAS_NOVAS) & _colunas_memoria(con))

        (linha,) = ci.listar(TENANT)
        assert _colunas_memoria(con) == set(ci.COLUNAS)
        assert linha["customer_id_externo"] == "antigo"
        assert linha["cancelado_em"] is None
        assert sum(1 for s in registro if "ADD COLUMN IF NOT EXISTS" in s) == 3

    def test_as_quatro_operacoes_passam_pela_traducao_de_placeholder(self, postgres_falso):
        """Todo SQL chega ao psycopg2 falso com `%s`, nunca `?` — o cursor
        falso falha alto se não chegar."""
        con, registro = postgres_falso
        ci.upsert_um(TENANT, _cliente("c-1", dias=3.0, uso=4.0))
        ci.atualizar_parcial(TENANT, "c-1", {"mrr": 200.0})
        ci.cancelar(TENANT, "c-1", motivo="x")
        assert ci.listar(TENANT) == []
        assert ci.listar(TENANT, incluir_cancelados=True)[0]["mrr"] == 200.0
        assert any("%s" in s for s in registro)


# ── (b) O lote não ressuscita ────────────────────────────────────────────

class TestGravarNaoReativa:
    def test_gravar_em_lote_sobre_cancelado_atualiza_a_foto_mas_nao_reativa(self):
        ci.gravar(TENANT, [_cliente("c-1", mrr=100.0, dias=10.0, uso=2.0)])
        cancelado = ci.cancelar(TENANT, "c-1", motivo="caro")
        assert cancelado["cancelado_em"] is not None

        ci.gravar(TENANT, [_cliente("c-1", mrr=999.0, dias=1.0, uso=30.0)])
        (linha,) = ci.listar(TENANT, incluir_cancelados=True)
        assert linha["mrr"] == 999.0                         # a foto atualizou
        assert linha["cancelado_em"] == cancelado["cancelado_em"]
        assert linha["motivo_cancelamento"] == "caro"        # e o desfecho ficou
        assert ci.listar(TENANT) == []

    def test_upsert_um_sem_reativar_nao_reativa(self):
        ci.gravar(TENANT, [_cliente("c-1")])
        antes = ci.cancelar(TENANT, "c-1")
        depois = ci.upsert_um(TENANT, _cliente("c-1", mrr=5.0))
        assert depois["mrr"] == 5.0
        assert depois["cancelado_em"] == antes["cancelado_em"]

    def test_upsert_um_com_reativar_limpa_o_cancelamento(self):
        ci.gravar(TENANT, [_cliente("c-1")])
        ci.cancelar(TENANT, "c-1", motivo="caro")
        linha = ci.upsert_um(TENANT, _cliente("c-1", mrr=5.0), reativar=True)
        assert linha["cancelado_em"] is None
        assert linha["motivo_cancelamento"] is None
        assert [l["customer_id_externo"] for l in ci.listar(TENANT)] == ["c-1"]

    def test_upsert_um_cria_quando_nao_existe_e_devolve_a_linha(self):
        linha = ci.upsert_um(TENANT, _cliente("novo", mrr=42.0, dias=1.0, uso=2.0, email="a@b.co"))
        assert linha["customer_id_externo"] == "novo"
        assert linha["tenant_id"] == TENANT
        assert linha["mrr"] == 42.0
        assert linha["importado_em"].endswith("+00:00")
        assert linha["cancelado_em"] is None
        assert set(linha) == set(ci.COLUNAS)

    def test_reativar_sobre_quem_nao_estava_cancelado_nao_muda_nada(self):
        a = ci.upsert_um(TENANT, _cliente("c-1"))
        b = ci.upsert_um(TENANT, _cliente("c-1"), reativar=True)
        assert a["cancelado_em"] is b["cancelado_em"] is None


# ── (c) Cancelar ─────────────────────────────────────────────────────────

class TestCancelar:
    def test_grava_data_iso_utc_e_motivo_e_a_linha_fica(self):
        ci.gravar(TENANT, [_cliente("c-1", dias=3.0, uso=4.0)])
        linha = ci.cancelar(TENANT, "c-1", motivo="mudou de fornecedor")
        assert linha["cancelado_em"].endswith("+00:00")
        assert linha["motivo_cancelamento"] == "mudou de fornecedor"
        assert linha["days_since_last"] == 3.0                 # nada mais mudou
        assert ci.contar(TENANT) == 0                          # saiu da contagem ativa
        assert ci.contar(TENANT, incluir_cancelados=True) == 1  # mas a linha existe
        assert ci.obter(TENANT, "c-1")["cancelado_em"] == linha["cancelado_em"]

    def test_cancelar_duas_vezes_preserva_a_primeira_data_e_o_primeiro_motivo(self, monkeypatch):
        ci.gravar(TENANT, [_cliente("c-1")])
        monkeypatch.setattr(ci, "_agora", lambda: "2026-01-01T00:00:00+00:00")
        primeira = ci.cancelar(TENANT, "c-1", motivo="primeiro")
        monkeypatch.setattr(ci, "_agora", lambda: "2026-02-02T00:00:00+00:00")
        segunda = ci.cancelar(TENANT, "c-1", motivo="segundo")
        assert primeira["cancelado_em"] == "2026-01-01T00:00:00+00:00"
        assert segunda["cancelado_em"] == "2026-01-01T00:00:00+00:00"
        assert segunda["motivo_cancelamento"] == "primeiro"

    def test_sem_motivo_fica_nulo(self):
        ci.gravar(TENANT, [_cliente("c-1")])
        assert ci.cancelar(TENANT, "c-1")["motivo_cancelamento"] is None

    def test_motivo_e_truncado_em_500(self):
        ci.gravar(TENANT, [_cliente("c-1")])
        linha = ci.cancelar(TENANT, "c-1", motivo="x" * 800)
        assert len(linha["motivo_cancelamento"]) == ci.MOTIVO_CANCELAMENTO_MAX == 500

    def test_inexistente_devolve_none(self):
        assert ci.cancelar(TENANT, "nao-existe") is None


# ── (d) Atualização parcial ──────────────────────────────────────────────

class TestAtualizarParcial:
    def test_mudar_mrr_nao_toca_nas_colunas_comportamentais(self):
        ci.gravar(TENANT, [_cliente("c-1", mrr=100.0, dias=47.0, uso=1.0, perfil="PJ", email="a@b.co")])
        linha = ci.atualizar_parcial(TENANT, "c-1", {"mrr": 250.0})
        assert linha["mrr"] == 250.0
        assert linha["days_since_last"] == 47.0
        assert linha["features_used_30d"] == 1.0
        assert linha["billing_profile"] == "PJ"
        assert linha["email"] == "a@b.co"
        assert linha["atualizado_em"].endswith("+00:00")
        assert linha["cancelado_em"] is None

    def test_campo_presente_com_none_limpa(self):
        ci.gravar(TENANT, [_cliente("c-1", dias=47.0, uso=1.0)])
        linha = ci.atualizar_parcial(TENANT, "c-1", {"days_since_last": None})
        assert linha["days_since_last"] is None
        assert linha["features_used_30d"] == 1.0

    def test_varios_campos_de_uma_vez(self):
        ci.gravar(TENANT, [_cliente("c-1", perfil="PJ")])
        linha = ci.atualizar_parcial(TENANT, "c-1", {"billing_profile": "CLT", "mrr": "10"})
        assert linha["billing_profile"] == "CLT"
        assert linha["mrr"] == 10.0

    def test_campos_vazio_devolve_a_linha_sem_carimbar(self):
        ci.gravar(TENANT, [_cliente("c-1")])
        linha = ci.atualizar_parcial(TENANT, "c-1", {})
        assert linha["customer_id_externo"] == "c-1"
        assert linha["atualizado_em"] is None

    def test_chave_desconhecida_e_value_error(self):
        ci.gravar(TENANT, [_cliente("c-1")])
        with pytest.raises(ValueError, match="cancelado_em"):
            ci.atualizar_parcial(TENANT, "c-1", {"cancelado_em": "2026-01-01"})
        with pytest.raises(ValueError):
            ci.atualizar_parcial(TENANT, "c-1", {"tenant_id": OUTRO})

    def test_nao_toca_no_cancelamento_nem_no_importado_em(self):
        ci.gravar(TENANT, [_cliente("c-1")])
        antes = ci.cancelar(TENANT, "c-1", motivo="m")
        depois = ci.atualizar_parcial(TENANT, "c-1", {"mrr": 1.0})
        assert depois["cancelado_em"] == antes["cancelado_em"]
        assert depois["motivo_cancelamento"] == "m"
        assert depois["importado_em"] == antes["importado_em"]

    def test_inexistente_devolve_none(self):
        assert ci.atualizar_parcial(TENANT, "nao-existe", {"mrr": 1.0}) is None


# ── (e) listar() e seus chamadores ───────────────────────────────────────

class TestListar:
    def test_padrao_exclui_cancelados_e_incluir_traz(self):
        ci.gravar(TENANT, [_cliente("ativo"), _cliente("saiu")])
        ci.cancelar(TENANT, "saiu")
        assert [l["customer_id_externo"] for l in ci.listar(TENANT)] == ["ativo"]
        assert [l["customer_id_externo"] for l in ci.listar(TENANT, incluir_cancelados=True)] \
            == ["ativo", "saiu"]

    def test_scoring_em_lote_nao_pontua_cancelado(self):
        ci.gravar(TENANT, [_cliente("frio", dias=45.0, uso=0.0), _cliente("saiu", dias=60.0, uso=0.0)])
        ci.cancelar(TENANT, "saiu")
        assert [l["customer_id_externo"] for l in batch_scoring.pontuar_base(TENANT)] == ["frio"]

    def test_insights_unificados_nao_listam_cancelado(self):
        ci.gravar(TENANT, [_cliente("frio", dias=45.0, uso=0.0), _cliente("saiu", dias=60.0, uso=0.0)])
        ci.cancelar(TENANT, "saiu")
        ids = [l["customer_id_externo"] for l in insights_unificados.clientes_em_risco(TENANT)]
        assert "saiu" not in ids and "frio" in ids

    def test_contar_segue_o_listar(self):
        n, k = 7, 3
        ci.gravar(TENANT, [_cliente(f"c-{i}") for i in range(n)])
        for i in range(k):
            ci.cancelar(TENANT, f"c-{i}")
        assert ci.contar(TENANT) == n - k == len(ci.listar(TENANT))
        assert ci.contar(TENANT, incluir_cancelados=True) == n             == len(ci.listar(TENANT, incluir_cancelados=True))

    def test_obter_traz_cancelado(self):
        ci.gravar(TENANT, [_cliente("saiu")])
        ci.cancelar(TENANT, "saiu")
        assert ci.obter(TENANT, "saiu")["cancelado_em"] is not None
        assert ci.obter(TENANT, "nunca-existiu") is None


# ── (f) Isolamento por tenant nas quatro ─────────────────────────────────

class TestIsolamentoPorTenant:
    def test_cancelar_e_atualizar_de_outro_tenant_e_none(self):
        ci.gravar(TENANT, [_cliente("c-1", mrr=100.0)])
        assert ci.cancelar(OUTRO, "c-1") is None
        assert ci.atualizar_parcial(OUTRO, "c-1", {"mrr": 1.0}) is None
        assert ci.obter(OUTRO, "c-1") is None
        (linha,) = ci.listar(TENANT)
        assert linha["mrr"] == 100.0 and linha["cancelado_em"] is None

    def test_upsert_um_em_outro_tenant_cria_outra_linha(self):
        ci.upsert_um(TENANT, _cliente("c-1", mrr=1.0))
        ci.upsert_um(OUTRO, _cliente("c-1", mrr=2.0))
        assert ci.listar(TENANT)[0]["mrr"] == 1.0
        assert ci.listar(OUTRO)[0]["mrr"] == 2.0

    def test_reativar_em_outro_tenant_nao_reativa_o_meu(self):
        ci.gravar(TENANT, [_cliente("c-1")])
        ci.cancelar(TENANT, "c-1")
        ci.upsert_um(OUTRO, _cliente("c-1"), reativar=True)
        assert ci.obter(TENANT, "c-1")["cancelado_em"] is not None
        assert ci.listar(TENANT) == []

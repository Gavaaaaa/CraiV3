"""tests/test_migracao_estado.py — Etapa 2, Bloco 3: o `CHECK` de estado ganha
`aguardando_escolha` num banco que já existe.

SQLite não altera `CHECK` com `ALTER TABLE`: `ciclo_cobranca._migrar_check_de_estado`
recria a tabela numa transação. Aqui:
  - um banco com o DDL EXATO de 28/09/2026 (sem `decidido_em` nem
    `mensagem_confirmada_em`, como o arquivo real ainda estava), com linhas em
    todos os estados e tentativas: todas as linhas preservadas, coluna a
    coluna; chaves estrangeiras íntegras; a sequência do AUTOINCREMENT
    preservada; o estado novo aceito; e a segunda execução não faz nada;
  - uma CÓPIA do banco real (`app/data/recovery_cycles.db`, copiado para
    `tmp_path`, o original só lido). Sem o arquivo (clone limpo: `app/data/` é
    ignorado pelo git), o teste é pulado com o motivo.
"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from crai.dunning import ciclo_cobranca as cc

REAL = Path(__file__).resolve().parents[1] / "data" / "recovery_cycles.db"

# O DDL de `ciclos_cobranca` e `tentativas_cobranca` como estava em 28/09/2026.
DDL_28_09 = """
CREATE TABLE ciclos_cobranca (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id             TEXT    NOT NULL,
    id_recorrencia        TEXT    NOT NULL,
    id_cobranca_original  TEXT    NOT NULL,
    e2e_falha_original    TEXT,
    valor                 REAL    NOT NULL,
    causa_original        TEXT    NOT NULL,
    codigo_falha_original TEXT,
    janela_inicio         TEXT    NOT NULL,
    janela_fim            TEXT    NOT NULL,
    estado                TEXT    NOT NULL
        CHECK (estado IN ('recobrando', 'mensagem_enviada', 'recuperado', 'perdido', 'descartado')),
    estrategia            TEXT,
    motivo_descarte       TEXT,
    recovery_score        REAL,
    p_recovery            REAL,
    eprofit               REAL,
    fee                   REAL    NOT NULL DEFAULT 0,
    origem                TEXT    NOT NULL DEFAULT 'webhook',
    aberto_em             TEXT    NOT NULL,
    mensagem_em           TEXT,
    recuperado_em         TEXT,
    perdido_em            TEXT,
    descartado_em         TEXT,
    atualizado_em         TEXT    NOT NULL,
    UNIQUE (tenant_id, id_cobranca_original)
);
CREATE INDEX idx_ciclo_mandato ON ciclos_cobranca (tenant_id, id_recorrencia, estado);
CREATE TABLE tentativas_cobranca (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ciclo_id            INTEGER NOT NULL REFERENCES ciclos_cobranca(id),
    numero              INTEGER NOT NULL CHECK (numero BETWEEN 1 AND 3),
    agendada_para       TEXT    NOT NULL,
    origem_data         TEXT,
    valor               REAL    NOT NULL,
    disparada_em        TEXT,
    id_cobranca         TEXT,
    resultado           TEXT    NOT NULL DEFAULT 'pendente'
        CHECK (resultado IN ('pendente','paga','falhou','sem_retorno','cancelada')),
    codigo_resultado    TEXT,
    e2e_resultado       TEXT,
    motivo_cancelamento TEXT,
    resultado_em        TEXT,
    UNIQUE (ciclo_id, numero)
);
CREATE TABLE eventos_vistos (escopo TEXT NOT NULL, chave TEXT NOT NULL, visto_em TEXT NOT NULL,
                             PRIMARY KEY (escopo, chave));
"""
ESTADOS_ANTIGOS = ("recobrando", "mensagem_enviada", "recuperado", "perdido", "descartado")


def _banco_antigo(caminho: Path) -> None:
    with sqlite3.connect(caminho) as conn:
        conn.executescript(DDL_28_09)
        for i, estado in enumerate(ESTADOS_ANTIGOS, start=1):
            conn.execute(
                """INSERT INTO ciclos_cobranca (tenant_id, id_recorrencia, id_cobranca_original,
                       valor, causa_original, janela_inicio, janela_fim, estado, fee, aberto_em,
                       atualizado_em, motivo_descarte)
                   VALUES (?, ?, ?, ?, 'insufficient_funds', '2026-09-01T09:00:00',
                           '2026-09-08T09:00:00', ?, ?, '2026-09-01T09:00:00',
                           '2026-09-02T09:00:00', ?)""",
                ("t", f"RN_{i}", f"inv_{i}", 100.0 + i, estado, 15.0 if estado == "recuperado" else 0,
                 "eprofit_nao_positivo" if estado == "descartado" else None))
            conn.execute("INSERT INTO tentativas_cobranca (ciclo_id, numero, agendada_para, valor, "
                         "resultado) VALUES (?, 1, '2026-09-02T09:00:00', 10.0, 'falhou')", (i,))
        # Um ciclo apagado: a sequência (7) fica acima do maior id (5).
        conn.execute("""INSERT INTO ciclos_cobranca (tenant_id, id_recorrencia, id_cobranca_original,
                            valor, causa_original, janela_inicio, janela_fim, estado, aberto_em,
                            atualizado_em) VALUES ('t','RN_x','inv_x',1,'c','a','b','recobrando','a','a')""")
        conn.execute("""INSERT INTO ciclos_cobranca (tenant_id, id_recorrencia, id_cobranca_original,
                            valor, causa_original, janela_inicio, janela_fim, estado, aberto_em,
                            atualizado_em) VALUES ('t','RN_y','inv_y',1,'c','a','b','recobrando','a','a')""")
        conn.execute("DELETE FROM ciclos_cobranca WHERE id_recorrencia IN ('RN_x', 'RN_y')")


def _linhas(caminho: Path, tabela: str) -> list:
    with sqlite3.connect(caminho) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(l) for l in conn.execute(f"SELECT * FROM {tabela} ORDER BY id")]


def _migrar(caminho: Path, monkeypatch) -> None:
    monkeypatch.setenv("CRAI_RECOVERY_DB", str(caminho))
    cc.esquecer_schema_garantido()
    cc.ciclo_por_id(1)                     # a primeira conexão garante o schema e migra


def _conferir(caminho: Path, antes_ciclos: list, antes_tentativas: list) -> None:
    depois = _linhas(caminho, "ciclos_cobranca")
    assert len(depois) == len(antes_ciclos)
    for a, d in zip(antes_ciclos, depois):
        assert {k: d[k] for k in a} == a, "uma coluna mudou de valor na migração"
    assert _linhas(caminho, "tentativas_cobranca") == antes_tentativas
    with sqlite3.connect(caminho) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'ciclos_cobranca'").fetchone()[0]
        assert all(repr(e) in sql for e in cc.ESTADOS)
        indices = {l[0] for l in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'ciclos_cobranca'")}
        assert {"idx_ciclo_mandato", "idx_ciclo_tenant_atualizado"} <= indices
        assert "ciclos_cobranca_nova" not in {l[0] for l in conn.execute(
            "SELECT name FROM sqlite_master")}


class TestMigracaoDoCheck:

    def test_banco_de_28_09_todas_as_linhas_preservadas(self, tmp_path, monkeypatch):
        banco = tmp_path / "antigo.db"
        _banco_antigo(banco)
        ciclos, tentativas = _linhas(banco, "ciclos_cobranca"), _linhas(banco, "tentativas_cobranca")
        _migrar(banco, monkeypatch)
        _conferir(banco, ciclos, tentativas)
        with sqlite3.connect(banco) as conn:
            assert conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'ciclos_cobranca'"
                                ).fetchone()[0] == 7, "a sequência do AUTOINCREMENT voltou"
        # O estado novo é aceito, e o próximo id continua depois do 7.
        novo = cc.abrir_ciclo("t", "RN_novo", 10.0, "c", id_cobranca="inv_novo",
                              estado=cc.AGUARDANDO_ESCOLHA)
        assert novo["id"] == 8 and novo["estado"] == cc.AGUARDANDO_ESCOLHA
        # O CHECK continua recusando o que não é estado.
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(banco) as conn:
                conn.execute("UPDATE ciclos_cobranca SET estado = 'inventado' WHERE id = 1")

    def test_a_segunda_execucao_nao_faz_nada(self, tmp_path, monkeypatch):
        banco = tmp_path / "antigo.db"
        _banco_antigo(banco)
        _migrar(banco, monkeypatch)
        with sqlite3.connect(banco) as conn:
            assert cc._migrar_check_de_estado(conn) is False
        cc.esquecer_schema_garantido()
        cc.ciclo_por_id(1)
        assert len(_linhas(banco, "ciclos_cobranca")) == len(ESTADOS_ANTIGOS)

    def test_banco_novo_nasce_com_o_estado_e_nao_migra(self, tmp_path, monkeypatch):
        banco = tmp_path / "novo.db"
        monkeypatch.setenv("CRAI_RECOVERY_DB", str(banco))
        cc.esquecer_schema_garantido()
        cc.ciclo_por_id(1)
        with sqlite3.connect(banco) as conn:
            assert cc._migrar_check_de_estado(conn) is False
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'ciclos_cobranca'").fetchone()[0]
        assert "'aguardando_escolha'" in sql

    def test_copia_do_banco_real(self, tmp_path, monkeypatch):
        if not REAL.exists():
            pytest.skip("app/data/recovery_cycles.db não existe neste clone (app/data/ é ignorado "
                        "pelo git): a migração sobre o banco real não pôde ser medida aqui")
        copia = tmp_path / "copia_real.db"
        shutil.copy2(REAL, copia)
        ciclos, tentativas = _linhas(copia, "ciclos_cobranca"), _linhas(copia, "tentativas_cobranca")
        monkeypatch.setenv("CRAI_RETRY_STATE", str(tmp_path / "sem_json.json"))
        _migrar(copia, monkeypatch)
        _conferir(copia, ciclos, tentativas)
        assert len(ciclos) >= 1, "o banco real não tinha ciclo: o teste não mediu nada"

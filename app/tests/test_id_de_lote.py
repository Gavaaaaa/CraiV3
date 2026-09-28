"""tests/test_id_de_lote.py — o id do lote não pode voltar a ser o relógio.

POR QUE ISTO EXISTE. As duas rotas do painel montavam o identificador com
`int(datetime.now(timezone.utc).timestamp())` — o relógio em SEGUNDOS:

    app.py:1725   id_rec  = f"RN_painel_{...}"    -> id_recorrencia, thread_id,
                                                     e2e_id, customer_id
    app.py:1839   user_id = f"painel_{...}"       -> thread_id, chave de
                                                     `_channel_history`, e a
                                                     chave (tenant, user_id,
                                                     offer_type) do ciclo

Um id de resolução grosseira erra dos DOIS lados ao mesmo tempo, e é por isso
que passa despercebido: FUNDE duas chamadas independentes que caiam no mesmo
segundo, e PARTE em duas um lote cujas chamadas cruzem a virada do segundo.

Não é só estética de teste. Com o id fundido, dois clientes do painel dividem
o checkpoint do LangGraph (e com ele o contador de tentativas do BACEN), a
memória de "por qual canal este cliente já converteu" e o ciclo aberto do
`retention_log`. Foi exatamente assim que
`test_canal_escolhido::test_dados_diferentes_canais_diferentes` virou
intermitente: as três requisições do teste eram um cliente só para o histórico
de canal sempre que caíam no mesmo segundo — ver
`docs/RELATORIO_ESTABILIDADE.md`.

COMO ESTE ARQUIVO REPROVA A VOLTA DO DEFEITO. Não basta afirmar que dois ids
saem diferentes: rodando rápido, `int(time.timestamp())` também daria dois
iguais e o teste pegaria por acaso. Aqui o RELÓGIO É CONGELADO
(`relogio_parado`) e as chamadas acontecem no mesmo instante declarado. Se o
id voltar a derivar de `datetime.now()`, em qualquer resolução, as duas
chamadas produzem a MESMA string e estes testes reprovam — de forma
determinística, não por sorte.

A ponta oposta, e é a que diz que a correção não foi só "sortear sempre": com
`lote_id` explícito o agrupamento SOBREVIVE à virada do segundo, que é o que
um identificador de lote existe para fazer.

Uso:
    pytest tests/test_id_de_lote.py -v
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from crai.api import app as app_module
from crai.api.app import id_de_lote
from crai.churn_voluntary import retention_log as rl

TENANT_PAINEL = app_module.TENANT_PAINEL


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    with TestClient(app_module.app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def relogio_parado(monkeypatch):
    """Congela `datetime.now()` DENTRO de `crai.api.app`, e devolve o controle.

    É o instrumento do arquivo inteiro: com o relógio parado, qualquer id
    derivado dele vira uma constante, e um teste que exija ids distintos passa
    a reprovar em vez de depender de quanto tempo a máquina levou entre duas
    chamadas.
    """
    instante = {"agora": datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)}

    class RelogioFalso(datetime):
        @classmethod
        def now(cls, tz=None):
            return instante["agora"] if tz else instante["agora"].replace(tzinfo=None)

    monkeypatch.setattr(app_module, "datetime", RelogioFalso)
    return instante


def _user_ids_dos_ciclos() -> list[str]:
    """Os `user_id` que o pipeline gravou, na ordem. O `conftest` já aponta o
    banco para `tmp_path`, então isto vê só o que este teste produziu."""
    with sqlite3.connect(rl.caminho_do_banco()) as conn:
        return [linha[0] for linha in conn.execute(
            "SELECT user_id FROM ciclos_retencao WHERE tenant_id = ? ORDER BY id",
            (TENANT_PAINEL,))]


# ── A função, isolada ────────────────────────────────────────────────────

class TestIdDeLote:

    def test_dois_lotes_no_mesmo_instante_recebem_ids_diferentes(self, relogio_parado):
        """O relógio está PARADO: só um id que não depende dele pode diferir."""
        a, b = id_de_lote("painel"), id_de_lote("painel")
        assert a != b, (
            f"dois lotes gerados no mesmo instante receberam o mesmo id ({a}). "
            "O identificador voltou a derivar do relógio — ver a docstring "
            "deste arquivo e `crai/api/app.py::id_de_lote`")

    def test_o_prefixo_continua_dizendo_de_onde_veio(self, relogio_parado):
        assert id_de_lote("RN_painel").startswith("RN_painel_")
        assert id_de_lote("painel").startswith("painel_")

    def test_o_mesmo_lote_id_atravessa_a_virada_do_segundo(self, relogio_parado):
        """A ponta oposta: sortear sempre não resolveria nada se o lote não
        pudesse se manter junto. Aqui o relógio ANDA entre as duas chamadas."""
        primeiro = id_de_lote("painel", "lote-da-manha")
        relogio_parado["agora"] += timedelta(seconds=37)
        segundo = id_de_lote("painel", "lote-da-manha")
        assert primeiro == segundo == "painel_lote-da-manha"

    def test_lote_id_de_fora_e_saneado(self):
        """O valor vem do navegador e vira chave de checkpoint, de log e de
        tenant. Mesmo saneamento do `cliente` em `PainelCobranca`."""
        assert id_de_lote("painel", "a/b:c d;e") == "painel_abcde"
        assert id_de_lote("painel", "x" * 200) == "painel_" + "x" * 48
        # Só lixo é o mesmo que não mandar nada: cai no sorteio, não numa
        # chave vazia que agruparia todo mundo.
        assert id_de_lote("painel", "///") != id_de_lote("painel", "///")


# ── A rota, ponta a ponta ────────────────────────────────────────────────

class TestRotaDoPainelNaoAgrupaPeloRelogio:

    def test_duas_chamadas_no_mesmo_instante_sao_dois_clientes(
            self, cliente, relogio_parado):
        """Com o relógio parado, as duas requisições têm de virar DUAS linhas
        de ciclo com `user_id` distintos. Com o id antigo virariam uma chave
        só — e é aí que o histórico de canal de uma decide o canal da outra."""
        assert cliente.post("/simulate/painel/evento-risco", json={}).status_code == 200
        assert cliente.post("/simulate/painel/evento-risco", json={}).status_code == 200

        ids = _user_ids_dos_ciclos()
        assert len(ids) == 2, ids
        assert ids[0] != ids[1], (
            f"as duas chamadas do painel dividiram o id {ids[0]} — o "
            "identificador voltou a sair do relógio")

    def test_com_lote_id_explicito_as_chamadas_ficam_juntas(
            self, cliente, relogio_parado):
        """E o relógio ANDA no meio, justamente para provar que não é ele quem
        mantém o grupo."""
        corpo = {"lote_id": "campanha-setembro"}
        assert cliente.post("/simulate/painel/evento-risco", json=corpo).status_code == 200
        relogio_parado["agora"] += timedelta(seconds=41)
        assert cliente.post("/simulate/painel/evento-risco", json=corpo).status_code == 200

        ids = _user_ids_dos_ciclos()
        assert len(ids) == 2, ids
        assert ids[0] == ids[1] == "user:painel_campanha-setembro", ids

    def test_a_cobranca_falhada_segue_a_mesma_regra(self, cliente, relogio_parado):
        """A outra rota que montava o id com o relógio (`RN_painel_...`).

        Aqui o id não aparece na resposta — e não é para aparecer só por causa
        de um teste. Ele é observado onde de fato importa: é o `thread_id` do
        checkpoint do involuntário, que carrega o contador de tentativas do
        BACEN. Duas cobranças fundidas num `thread_id` só somariam tentativas
        de clientes diferentes dentro da mesma janela regulada.

        O `MemorySaver` do `crai_agent` é global do módulo e ninguém o limpa
        entre testes (é a mesma dívida que o `conftest` fechou para os grafos
        do voluntário), então a comparação é sobre o que ESTAS duas chamadas
        acrescentaram, não sobre o conteúdo inteiro.
        """
        threads = app_module.crai_agent.checkpointer.storage
        antes = set(threads)

        a = cliente.post("/simulate/painel/cobranca-falhada", json={})
        b = cliente.post("/simulate/painel/cobranca-falhada", json={})
        assert a.status_code == b.status_code == 200, (a.text, b.text)

        novos = [t for t in threads if t not in antes and t.startswith("RN_painel_")]
        assert len(novos) == 2, (
            f"as duas cobranças do painel abriram {len(novos)} checkpoint(s), "
            f"esperado 2: {novos}. Com o relógio parado, um só significa que "
            "o id voltou a sair dele — e com ele o contador do BACEN passa a "
            "ser compartilhado entre cobranças distintas")

    def test_o_relogio_falso_realmente_congela_a_rota(self, cliente, relogio_parado):
        """CATRACA DO INSTRUMENTO. Se `relogio_parado` deixar de alcançar o
        módulo, os testes acima passariam por tempo decorrido em vez de por
        mérito do id — e não avisariam. Aqui se cobra que o congelamento
        funciona: `app_module.datetime.now()` não anda sozinho."""
        primeiro = app_module.datetime.now(timezone.utc)
        for _ in range(1000):
            app_module.datetime.now(timezone.utc)
        assert app_module.datetime.now(timezone.utc) == primeiro

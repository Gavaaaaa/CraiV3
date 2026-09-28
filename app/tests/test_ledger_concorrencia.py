"""tests/test_ledger_concorrencia.py — dois workers não fecham o mesmo ciclo.

POR QUE ISTO EXISTE. `registrar_desfecho` fazia um `SELECT` do ciclo ABERTO e,
depois, um `UPDATE` que o fechava — duas operações soltas, fora de transação de
escrita. Em modo deferido o `SELECT` não toma trava nenhuma: dois workers
podiam ler a MESMA linha aberta, os dois seguirem para o `UPDATE`, e os DOIS
responderem "fechei". O chamador conta o desfecho quando ouve isso, então o
posterior do bandit receberia o mesmo aceite duas vezes.

A tabela do Art. 20, no mesmo arquivo, já tratava esse risco com
`BEGIN IMMEDIATE` (`registrar_decisoes`) — o ciclo de retenção não. Aqui a
correção é cobrada com DOIS PROCESSOS DE VERDADE, não threads: o GIL esconde
exatamente a classe de intercalação que se quer provar, e a trava do SQLite é
entre CONEXÕES, não entre threads de um processo.

E O TERCEIRO CASO, que é o grave. A função devolvia `False` para reenvio, para
ciclo inexistente E para erro de gravação. Os dois primeiros são legítimos; o
terceiro é um desfecho que existiu e se perdeu — rótulo de treino e prova de
faturamento. Saía pela mesma porta, em silêncio. O segundo teste deste arquivo
força a contenção além do `busy_timeout` e cobra que ERRO apareça, e que seja
DISTINTO de reenvio.

Uso:
    pytest tests/test_ledger_concorrencia.py -v
"""

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

import crai
from crai.churn_voluntary import retention_log as rl
from crai.churn_voluntary.retention_log import ResultadoDesfecho

RAIZ_APP = Path(crai.__file__).resolve().parent.parent
TENANT = "empresa_a"
OFERTA = "pausa_1_mes"
N_CORRIDAS = 40
# Cada corrida tem a sua largada, e não só a primeira. Com uma largada só, os
# dois processos se sobrepunham na corrida 0 e derivavam depois — medido: o
# teste sem `BEGIN IMMEDIATE` só reprovava em 1 de 3 execuções. Com a largada
# por corrida eles se reencontram a cada 5 ms e a disputa é real do começo ao
# fim.
INTERVALO_S = 0.005

# O worker roda num PROCESSO separado: importa o módulo do zero, abre a própria
# conexão, e espera o mesmo instante que o irmão para disparar. Imprime uma
# linha JSON por corrida, com os instantes em volta da chamada — é o que
# permite medir se a corrida ACONTECEU, em vez de supor.
WORKER = '''
import json, os, sys, time
os.environ["CRAI_RETENTION_DB"] = sys.argv[1]
marca, largada, n = sys.argv[2], float(sys.argv[3]), int(sys.argv[4])
intervalo = float(sys.argv[5])
from crai.churn_voluntary.retention_log import registrar_desfecho
for i in range(n):
    while time.perf_counter() < largada + i * intervalo:
        pass
    t0 = time.perf_counter()
    r = registrar_desfecho("empresa_a", f"user:c{i}", "pausa_1_mes", True, origem=marca)
    t1 = time.perf_counter()
    print(json.dumps({"i": i, "marca": marca, "resultado": r.value,
                      "t0": t0, "t1": t1}), flush=True)
'''


@pytest.fixture
def banco(tmp_path, monkeypatch):
    """Um banco só para este teste, com `N_CORRIDAS` ciclos ABERTOS."""
    caminho = tmp_path / "concorrencia.db"
    monkeypatch.setenv("CRAI_RETENTION_DB", str(caminho))
    for i in range(N_CORRIDAS):
        rl.registrar_ciclo({"tenant_id": TENANT, "user_id": f"user:c{i}",
                            "offer_type": OFERTA, "accepted": None, "props": {}})
    return caminho


def _rodar_workers(caminho: Path) -> list[dict]:
    """Os dois processos, largando no mesmo instante do relógio monotônico."""
    script = caminho.parent / "worker.py"
    script.write_text(WORKER, encoding="utf-8")
    largada = time.perf_counter() + 2.0
    ambiente = {**dict(__import__("os").environ), "PYTHONPATH": str(RAIZ_APP),
                "PYTHONIOENCODING": "utf-8"}
    processos = [
        subprocess.Popen(
            [sys.executable, str(script), str(caminho), marca,
             str(largada), str(N_CORRIDAS), str(INTERVALO_S)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ambiente)
        for marca in ("A", "B")
    ]
    linhas = []
    for p in processos:
        saida, erro = p.communicate(timeout=120)
        assert p.returncode == 0, f"worker morreu: {erro}"
        linhas += [json.loads(l) for l in saida.splitlines() if l.startswith("{")]
    return linhas


class TestDoisWorkersDisputandoOMesmoCiclo:

    def test_exatamente_um_fecha_o_outro_ve_reenvio_e_ninguem_ve_erro(self, banco):
        linhas = _rodar_workers(banco)
        assert len(linhas) == 2 * N_CORRIDAS, len(linhas)

        erros = [l for l in linhas if l["resultado"] == ResultadoDesfecho.ERRO.value]
        assert not erros, (
            f"{len(erros)} chamada(s) devolveram ERRO numa disputa que o "
            f"`busy_timeout` deveria absorver: {erros[:3]}")

        for i in range(N_CORRIDAS):
            da_corrida = [l["resultado"] for l in linhas if l["i"] == i]
            fechados = da_corrida.count(ResultadoDesfecho.FECHADO.value)
            reenvios = da_corrida.count(ResultadoDesfecho.REENVIO.value)
            assert (fechados, reenvios) == (1, 1), (
                f"corrida {i}: {da_corrida}. Esperado exatamente um FECHADO e "
                "um REENVIO — dois FECHADO significa que os dois workers leram "
                "a mesma linha aberta e o bandit contaria o desfecho em dobro")

    def test_a_disputa_realmente_aconteceu(self, banco):
        """CATRACA DO INSTRUMENTO. Se os dois processos nunca se sobrepuserem,
        o teste acima passa por serialização acidental e não prova nada. Aqui
        se cobra que ao menos uma corrida teve os dois dentro da chamada ao
        mesmo tempo."""
        linhas = _rodar_workers(banco)
        sobrepostas = 0
        for i in range(N_CORRIDAS):
            a, b = sorted((l for l in linhas if l["i"] == i), key=lambda l: l["t0"])
            if b["t0"] < a["t1"]:
                sobrepostas += 1
        # O piso é 1, e baixo de propósito: a PRÓPRIA correção reduz o que
        # esta medida enxerga. Com `BEGIN IMMEDIATE`, o worker que perde a
        # trava fica parado esperando, sai da grade de largadas e passa a
        # alternar com o irmão em vez de colidir — medido, 4 sobreposições em
        # 40 com a correção, contra 10+ sem ela. Exigir muito aqui seria
        # reprovar o conserto. Quem pega a regressão de verdade é o teste
        # acima (mediu 3/3 execuções reprovando sem `BEGIN IMMEDIATE`); este
        # existe só para barrar o caso degenerado em que os processos rodam
        # inteiramente em fila e não exercitam concorrência nenhuma.
        assert sobrepostas >= 1, (
            f"nenhuma das {N_CORRIDAS} corridas se sobrepôs no tempo — os "
            "processos rodaram em fila, e o teste de exclusão mútua acima "
            "não exercitou concorrência nenhuma")

    def test_o_banco_fica_consistente(self, banco):
        _rodar_workers(banco)
        with sqlite3.connect(banco) as conn:
            total, fechados = conn.execute(
                "SELECT COUNT(*), COUNT(accepted) FROM ciclos_retencao").fetchone()
        assert (total, fechados) == (N_CORRIDAS, N_CORRIDAS), (
            f"{total} linhas, {fechados} fechadas — esperado "
            f"{N_CORRIDAS}/{N_CORRIDAS}: nenhuma linha nova, nenhuma aberta")


class TestATransacaoDeEscritaAbreAntesDaLeitura:
    """A prova DETERMINÍSTICA, que o teste de dois processos não consegue dar.

    Uma corrida entre processos é, por natureza, probabilística: medido, o
    teste acima reprova em 2 de 3 execuções quando se tira o
    `BEGIN IMMEDIATE`, não em 3 de 3, e aumentar o número de corridas não
    melhora (os processos derivam da grade de largadas). Ele é a prova de que
    o invariante VALE sob concorrência real — não serve como catraca de
    regressão.

    A catraca é aqui: com o `trace_callback` do próprio `sqlite3`, olha-se a
    ORDEM em que os comandos saíram. `BEGIN IMMEDIATE` tem de vir ANTES do
    `SELECT` que procura o ciclo aberto. É exatamente o invariante — a trava
    de escrita tomada antes da leitura —, e reprova sempre que ele cair.
    """

    def test_o_begin_immediate_precede_o_select_do_ciclo_aberto(
            self, banco, monkeypatch):
        executadas: list[str] = []
        real = rl._conectar

        def espiando(*a, **k):
            conn = real(*a, **k)
            conn.set_trace_callback(executadas.append)
            return conn

        monkeypatch.setattr(rl, "_conectar", espiando)
        assert (rl.registrar_desfecho(TENANT, "user:c0", OFERTA, True)
                is ResultadoDesfecho.FECHADO)

        sql = [" ".join(c.split()).upper() for c in executadas]
        inicio = [i for i, c in enumerate(sql) if c.startswith("BEGIN IMMEDIATE")]
        leitura = [i for i, c in enumerate(sql)
                   if c.startswith("SELECT ID FROM CICLOS_RETENCAO")]
        assert inicio, (
            "`registrar_desfecho` não abriu transação IMMEDIATE. O `SELECT` do "
            "ciclo aberto e o `UPDATE` que o fecha voltaram a ser duas "
            f"operações soltas. Comandos vistos: {sql}")
        assert leitura, sql
        assert inicio[0] < leitura[0], (
            "o `SELECT` do ciclo aberto saiu ANTES do `BEGIN IMMEDIATE`: a "
            "leitura acontece sem trava, e dois workers podem ler a mesma "
            f"linha aberta. Ordem: {sql}")


class TestOEscopoDaEnvDeBusyTimeout:
    """A env de contenção não pode alcançar a trilha do Art. 20.

    `CRAI_RETENTION_BUSY_TIMEOUT_MS` existe para UM teste: o de contenção
    abaixo, que precisa estourar o timeout em milissegundos em vez de segurar
    a suíte por 5 s. O `_conectar`, porém, é o mesmo para as DUAS tabelas
    deste arquivo — os ciclos e `decisoes_automatizadas`.

    Se a env fosse lida lá dentro, bastaria um teste baixá-la para 50 ms e,
    adiante, encostar num ponto que grava decisão (direto, ou pelo pipeline,
    que grava a trilha em todo `update_crm`) para a trilha passar a falhar
    por contenção. Reprovaria de forma intermitente, e a semanas de distância
    de quem mexeu. Por isso o override mora em
    `_busy_timeout_do_desfecho_ms` e só `registrar_desfecho` o passa.

    Este teste é a catraca disso, e não uma promessa em comentário: lê o
    `PRAGMA busy_timeout` que cada caminho realmente emite.
    """

    def _pragmas(self, monkeypatch, acao) -> list[int]:
        """Os `PRAGMA busy_timeout` que saíram, em milissegundos.

        O espião entra em `sqlite3.connect`, e não em `_conectar`: o PRAGMA é
        emitido DENTRO do `_conectar`, antes de ele devolver a conexão, então
        um `set_trace_callback` posto no retorno chegaria tarde e não veria
        nada — foi o que aconteceu na primeira versão deste teste.
        """
        vistos: list[int] = []
        real = rl.sqlite3.connect

        def anotar(comando: str) -> None:
            if "PRAGMA busy_timeout" in comando:
                vistos.append(int(comando.split("=")[1]))

        def espiando(*a, **k):
            conn = real(*a, **k)
            conn.set_trace_callback(anotar)
            return conn

        monkeypatch.setattr(rl.sqlite3, "connect", espiando)
        acao()
        return vistos

    def test_a_env_de_busy_timeout_nao_alcanca_a_trilha(self, banco, monkeypatch):
        monkeypatch.setenv("CRAI_RETENTION_BUSY_TIMEOUT_MS", "50")

        decisao = {"tenant_id": TENANT, "sujeito_id": "user:c0",
                   "decidido_em": "2026-09-28T00:00:00+00:00",
                   "dominio": "voluntario", "tipo_decisao": "oferta",
                   "modelo": "offer_bandit", "entradas": {"profile": "PJ"},
                   "saida": {"offer_type": OFERTA},
                   "explicacao": "teste de escopo do busy_timeout"}
        da_trilha = self._pragmas(monkeypatch, lambda: rl.registrar_decisao(decisao))

        assert da_trilha, "a trilha não abriu conexão — o teste não mediu nada"
        assert all(ms == rl.BUSY_TIMEOUT_MS_PADRAO for ms in da_trilha), (
            f"a trilha recebeu busy_timeout {da_trilha} com a env em 50 ms. A "
            "env vazou para `_conectar` e agora qualquer teste que a baixe "
            "pode derrubar a gravação de decisão por contenção")

    def test_a_env_alcanca_o_desfecho_que_e_para_quem_ela_existe(
            self, banco, monkeypatch):
        monkeypatch.setenv("CRAI_RETENTION_BUSY_TIMEOUT_MS", "50")
        do_desfecho = self._pragmas(
            monkeypatch,
            lambda: rl.registrar_desfecho(TENANT, "user:c0", OFERTA, True))
        assert do_desfecho == [50], do_desfecho

    def test_sem_a_env_os_dois_caminhos_usam_o_padrao(self, banco, monkeypatch):
        monkeypatch.delenv("CRAI_RETENTION_BUSY_TIMEOUT_MS", raising=False)
        do_desfecho = self._pragmas(
            monkeypatch,
            lambda: rl.registrar_desfecho(TENANT, "user:c1", OFERTA, True))
        assert do_desfecho == [rl.BUSY_TIMEOUT_MS_PADRAO], do_desfecho


class TestErroNaoSeDisfarcaDeReenvio:

    def test_contencao_alem_do_busy_timeout_devolve_ERRO(self, banco, monkeypatch):
        """O caso que o `bool` escondia.

        Um gravador segura a trava de escrita e não solta. Com o
        `busy_timeout` baixado para 50 ms, a chamada desiste — e tem de dizer
        ERRO, não `False`. O ciclo continua ABERTO no banco: é a prova de que
        o desfecho se perdeu de verdade, e de que responder 200 aqui
        descartaria um dado que existiu.

        A ENV BAIXADA AQUI NÃO ENCOSTA NA TRILHA DO ART. 20. Ela é lida por
        `_busy_timeout_do_desfecho_ms`, que só `registrar_desfecho` chama; o
        `_conectar` — que a trilha divide com os ciclos — continua no padrão
        de 5 s. Este teste também não exercita nenhum ponto que grave
        decisão: chama `registrar_desfecho` direto, sem passar pelo grafo. A
        catraca estrutural disso é
        `TestOEscopoDaEnvDeBusyTimeout::test_a_env_de_busy_timeout_nao_alcanca_a_trilha`.
        """
        monkeypatch.setenv("CRAI_RETENTION_BUSY_TIMEOUT_MS", "50")

        travador = sqlite3.connect(banco, timeout=0)
        try:
            travador.execute("BEGIN IMMEDIATE")
            travador.execute(
                "UPDATE ciclos_retencao SET origem_desfecho = 'trava' WHERE id = 1")

            t0 = time.perf_counter()
            resultado = rl.registrar_desfecho(TENANT, "user:c0", OFERTA, True)
            decorrido = time.perf_counter() - t0
        finally:
            travador.rollback()
            travador.close()

        assert resultado is ResultadoDesfecho.ERRO, resultado
        assert resultado is not ResultadoDesfecho.REENVIO
        assert decorrido < 5.0, (
            f"a chamada levou {decorrido:.1f}s — o `busy_timeout` da env não "
            "foi respeitado e caiu no padrão de 5 s")

        with sqlite3.connect(banco) as conn:
            aberto = conn.execute(
                "SELECT accepted FROM ciclos_retencao WHERE user_id = 'user:c0'"
            ).fetchone()[0]
        assert aberto is None, (
            "o ciclo foi fechado apesar do ERRO — o status estaria mentindo")

    def test_sem_a_trava_o_mesmo_ciclo_fecha_e_depois_da_reenvio(self, banco):
        """O contraste que dá sentido ao teste acima: os três status saem do
        MESMO caminho, e só o do meio é patológico."""
        assert (rl.registrar_desfecho(TENANT, "user:c0", OFERTA, True)
                is ResultadoDesfecho.FECHADO)
        assert (rl.registrar_desfecho(TENANT, "user:c0", OFERTA, True)
                is ResultadoDesfecho.REENVIO)
        assert (rl.registrar_desfecho(TENANT, "user:nunca-existiu", OFERTA, True)
                is ResultadoDesfecho.SEM_CICLO)

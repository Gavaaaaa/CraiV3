"""crai/api/relogio.py — o relógio do serviço (Etapa 2, Bloco 1).

O PROBLEMA QUE ESTE MÓDULO FECHA (defeito 7-A do diagnóstico de 29/09/2026). A
lógica que dispara as tentativas 2 e 3 e varre os ciclos existe desde a Etapa 1
(`dunning/retry_scheduler.processar_tentativas_devidas`), mas nada dentro do
serviço a chamava: sem um cron externo, um ciclo ficava parado em `recobrando`
depois da primeira falha — nem tentativa 2 e 3, nem `sem_retorno`, nem a
mensagem pelo agendador, nem `perdido`. E nada avisava que o relógio estava
parado.

O QUE ELE É. Uma tarefa de fundo, criada no `lifespan` do FastAPI, que chama
`passagem(agora)` a cada `CRAI_RELOGIO_INTERVALO_S` segundos (padrão 60). Uma
vez por dia a passagem roda também o EXPURGO (`expurgar`, Bloco 4). A
passagem é a mesma função que o teste e a demo chamam com um `agora` adiantado —
o relógio só decide QUANDO ela roda. O que roda está em `ciclo_cobranca.varrer`,
no mesmo lugar para o agendador e para quem o chame à mão.

    CRAI_RELOGIO=0            desliga (a suíte faz isso em todo teste, ver
                              `app/tests/conftest.py`); ausente ou outro valor
                              liga — o serviço nasce com relógio.
    CRAI_RELOGIO_INTERVALO_S  segundos entre passagens, 0,01..3600. Torto ou
                              fora da faixa cai no padrão, com WARNING.

UMA PASSAGEM QUE LEVANTA NÃO MATA O RELÓGIO. A exceção é logada em ERROR,
guardada em `ultima_falha` (que o `/health` mostra) e o laço segue: o relógio
parado em silêncio é exatamente o defeito que este módulo existe para fechar.

UM PROCESSO SÓ. Com mais de um worker, cada um rodaria o seu relógio. A reserva
de mensagem e a marca de disparo são tomadas sob `BEGIN IMMEDIATE`, mas o
reenvio da instrução ao PSP acontece ANTES da marca
(`retry_scheduler.disparar_tentativa`): dois relógios podem mandar a mesma
instrução. Por isso o relógio RECUSA ligar quando detecta mais de um worker
(`workers_detectados`), com log em ERROR e o motivo no `/health`. É detecção, não
garantia — dois `uvicorn` separados apontando para o mesmo banco não são vistos.
A limitação está em `docs/LIMITACOES.md`.

A PASSAGEM RODA NO EVENT LOOP. `processar_tentativas_devidas` é `async`, mas as
escritas no SQLite dentro dela são síncronas (o mesmo padrão dos webhooks): uma
passagem com muito a fazer segura o loop pelo tempo das escritas.
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..dunning import ciclo_cobranca, configuracao, retry_scheduler
from . import registro_acesso

logger = logging.getLogger(__name__)

ENV_LIGADO = "CRAI_RELOGIO"
ENV_INTERVALO = "CRAI_RELOGIO_INTERVALO_S"
INTERVALO_PADRAO_S = 60.0
INTERVALO_MINIMO_S = 0.01
INTERVALO_MAXIMO_S = 3600.0

MOTIVO_DESLIGADO_POR_ENV = "desligado_por_env"
MOTIVO_MAIS_DE_UM_WORKER = "mais_de_um_worker"

# O que o `/health` mostra. Estado de processo: é o relógio DESTE processo.
_estado: dict = {}
_tarefa: Optional[asyncio.Task] = None


def _estado_inicial() -> dict:
    return {"ligado": False, "motivo_desligado": None, "intervalo_s": None,
            "ultima_passagem_em": None, "ultima_passagem_ok": None,
            "ultima_falha": None, "passagens": 0, "ultimo_expurgo_em": None}


_estado.update(_estado_inicial())


def _agora() -> datetime:
    """Ponto único de leitura do relógio. Hora local sem fuso, a mesma que o
    ciclo de cobrança grava e o agendador compara (`ciclo_cobranca`, TEMPO).
    Existe para ser substituível: o teste acelerado avança o tempo por aqui."""
    return datetime.now()


def ligado_por_env() -> bool:
    return os.getenv(ENV_LIGADO, "").strip() != "0"


def intervalo_s() -> float:
    bruto = os.getenv(ENV_INTERVALO)
    if bruto is None or not bruto.strip():
        return INTERVALO_PADRAO_S
    try:
        valor = float(bruto.strip().replace(",", "."))
    except ValueError:
        logger.warning("[RELOGIO] %s=%r não é número — usando %s s.",
                       ENV_INTERVALO, bruto, INTERVALO_PADRAO_S)
        return INTERVALO_PADRAO_S
    if not (INTERVALO_MINIMO_S <= valor <= INTERVALO_MAXIMO_S):
        logger.warning("[RELOGIO] %s=%s fora de [%s, %s] — usando %s s.", ENV_INTERVALO,
                       valor, INTERVALO_MINIMO_S, INTERVALO_MAXIMO_S, INTERVALO_PADRAO_S)
        return INTERVALO_PADRAO_S
    return valor


def _workers_no_argv(argv: list) -> Optional[int]:
    """`--workers N` ou `--workers=N` na linha de comando (uvicorn). No worker
    nascido por `spawn` o `sys.argv` é o do processo pai, então o worker
    também enxerga o número."""
    for i, arg in enumerate(argv):
        valor = None
        if arg == "--workers" and i + 1 < len(argv):
            valor = argv[i + 1]
        elif arg.startswith("--workers="):
            valor = arg.split("=", 1)[1]
        if valor is not None:
            try:
                return int(valor)
            except ValueError:
                return None
    return None


def workers_detectados(env: Optional[dict] = None, argv: Optional[list] = None) -> Optional[str]:
    """O motivo, em texto, se há sinal de mais de um worker; None se não há.

    Três sinais: `WEB_CONCURRENCY` > 1 (o padrão que o uvicorn lê para
    `--workers`); `--workers N` > 1 na linha de comando; gunicorn — cujo número
    de workers não é legível daqui, então é recusado sempre.
    """
    env = os.environ if env is None else env
    argv = sys.argv if argv is None else argv
    concorrencia = (env.get("WEB_CONCURRENCY") or "").strip()
    if concorrencia:
        try:
            if int(concorrencia) > 1:
                return f"WEB_CONCURRENCY={concorrencia}"
        except ValueError:
            pass
    n = _workers_no_argv(list(argv))
    if n is not None and n > 1:
        return f"--workers {n}"
    if (env.get("SERVER_SOFTWARE") or "").lower().startswith("gunicorn") or (
            argv and "gunicorn" in Path(str(argv[0])).name.lower()):
        return "gunicorn (número de workers não verificável)"
    return None


def expurgar(agora: datetime) -> dict:
    """O expurgo: o que sai do banco porque o prazo de guarda acabou.

      - o TEXTO das mensagens do involuntário, `retencao_mensagens_dias` (da
        configuração de cada empresa; padrão 90) depois do desfecho do ciclo —
        fica a abordagem (`ciclo_cobranca.apagar_texto_expirado`);
      - o registro de acesso às rotas de titular com mais de 12 meses
        (`registro_acesso.expurgar`).

    Devolve quantas linhas tocou, e diz isso no log. Uma falha LEVANTA, e a
    passagem que a chamou fica marcada como falha no `/health`: retenção que
    falha em silêncio é dado guardado além do prazo sem ninguém saber.

    A RETENÇÃO DA TRILHA DO ART. 20 (5 anos, D-E2-2) NÃO É CHAMADA DAQUI. A
    função de retenção da trilha (`retention_log`) recebe o prazo em `prazo_dias`,
    mas a catraca `test_art20_trilha.py::TestRetencaoEBestEffort` afirma que
    ninguém a chama, e mudar esse teste é decisão do Crai. Ver
    `docs/LIMITACOES.md`.
    """
    textos = 0
    tenants = ciclo_cobranca.tenants_com_texto_de_mensagem()
    for tenant_id in tenants:
        dias = configuracao.ler(tenant_id)["retencao_mensagens_dias"]
        textos += ciclo_cobranca.apagar_texto_expirado(agora, tenant_id, dias)
    acessos = registro_acesso.expurgar(agora)
    logger.info("[EXPURGO] %s: %d texto(s) de mensagem apagado(s), em %d empresa(s) "
                "verificada(s); %d registro(s) de acesso apagado(s).",
                agora.date().isoformat(), textos, len(tenants), acessos)
    return {"textos_de_mensagem_apagados": textos, "registros_de_acesso_apagados": acessos}


async def passagem(agora: datetime) -> dict:
    """O que o relógio faz a cada volta. Devolve o que aconteceu, para o log e
    para o teste. As varreduras e a mensagem devida vêm de dentro de
    `processar_tentativas_devidas` (→ `varrer_ciclos` → `ciclo_cobranca.varrer`).

    Uma vez por dia (o dia de `agora`), depois das tentativas, roda o expurgo.
    O dia fica em memória: depois de um reinício ele roda de novo na primeira
    passagem, e não faz mal — o expurgo é idempotente. Se ele levantar, o dia
    NÃO é marcado e a próxima passagem tenta de novo."""
    disparos = await retry_scheduler.processar_tentativas_devidas(agora)
    resultado = {"disparos": disparos}
    hoje = agora.date().isoformat()
    if _estado.get("ultimo_expurgo_em") != hoje:
        resultado["expurgo"] = expurgar(agora)
        _estado["ultimo_expurgo_em"] = hoje
    return resultado


async def _laco(intervalo: float) -> None:
    while True:
        agora = _agora()
        try:
            resultado = await passagem(agora)
            _estado["ultima_passagem_ok"] = True
            if resultado["disparos"]:
                logger.info("[RELOGIO] passagem de %s: %d disparo(s)",
                            agora.isoformat(timespec="seconds"), len(resultado["disparos"]))
        except asyncio.CancelledError:
            raise
        except Exception as e:                   # noqa: BLE001 — a passagem não mata o relógio
            _estado["ultima_passagem_ok"] = False
            _estado["ultima_falha"] = {"em": _com_fuso(agora), "erro": repr(e)[:300]}
            logger.error("[RELOGIO] a passagem de %s levantou (%r) — o relógio segue.",
                         agora.isoformat(timespec="seconds"), e, exc_info=True)
        _estado["ultima_passagem_em"] = _com_fuso(agora)
        _estado["passagens"] += 1
        await asyncio.sleep(intervalo)


def _com_fuso(agora: datetime) -> str:
    """ISO 8601 com offset. `agora` é hora local sem fuso: `astimezone()`
    a interpreta no fuso da máquina, que é o fuso em que ela foi lida."""
    return agora.astimezone().isoformat(timespec="seconds")


def ligar() -> bool:
    """Cria a tarefa de fundo no loop corrente, se permitido. True se ligou."""
    global _tarefa
    _estado.clear()
    _estado.update(_estado_inicial())
    if not ligado_por_env():
        _estado["motivo_desligado"] = MOTIVO_DESLIGADO_POR_ENV
        logger.info("[RELOGIO] desligado por %s=0.", ENV_LIGADO)
        return False
    sinal = workers_detectados()
    if sinal:
        _estado["motivo_desligado"] = MOTIVO_MAIS_DE_UM_WORKER
        logger.error("[RELOGIO] NÃO LIGADO: sinal de mais de um worker (%s). Com dois "
                     "relógios, a mesma instrução pode ir duas vezes ao PSP. Rode o serviço "
                     "com um worker só, ou desligue o relógio (%s=0) e use um cron externo "
                     "chamando o agendador. Ver docs/LIMITACOES.md.", sinal, ENV_LIGADO)
        return False
    intervalo = intervalo_s()
    _estado.update(ligado=True, intervalo_s=intervalo)
    _tarefa = asyncio.get_running_loop().create_task(_laco(intervalo), name="crai-relogio")
    logger.info("[RELOGIO] ligado: uma passagem a cada %s s.", intervalo)
    return True


async def desligar() -> None:
    global _tarefa
    tarefa, _tarefa = _tarefa, None
    if tarefa is not None:
        tarefa.cancel()
        try:
            await tarefa
        except asyncio.CancelledError:
            pass
    _estado["ligado"] = False


@asynccontextmanager
async def ciclo_de_vida(app):
    """O `lifespan` do FastAPI: liga o relógio na subida, desliga na descida."""
    ligar()
    try:
        yield
    finally:
        await desligar()


def estado_para_health() -> dict:
    return {k: _estado.get(k) for k in ("ligado", "motivo_desligado", "intervalo_s",
                                        "ultima_passagem_em", "ultima_passagem_ok",
                                        "ultima_falha", "passagens", "ultimo_expurgo_em")}

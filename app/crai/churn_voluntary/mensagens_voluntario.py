"""crai/churn_voluntary/mensagens_voluntario.py — as 3 mensagens do voluntário (14/10/2026).

O QUE MUDA. Até aqui a oferta de retenção saía no instante em que o sistema a
decidia. Agora o voluntário segue o mesmo modo de envio do involuntário
(`configuracao.modo_mensagem_voluntario`):

    escolha     o sistema escreve 3 mensagens para a oferta que escolheu e
                recomenda uma. Elas esperam a empresa por `prazo_escolha_horas`
                (o mesmo prazo do involuntário). Sem escolha no prazo, sai a
                recomendada da rodada mais recente.
    automatico  nada espera: o envio continua como sempre foi (`send_offer`).

A OFERTA NÃO MUDA AQUI. Quem decide a oferta é o bandit (`choose_offer`); as 3
mensagens são três jeitos de apresentar A MESMA oferta, como as 3 abordagens
do involuntário. A empresa escolhe a redação, não o desconto.

O QUE NÃO ESPERA. O aviso dentro do produto (`popup`) só existe enquanto o
cliente está no site: oito horas depois ele já foi embora. Por isso o pop-up sai
na hora, mesmo no modo escolha. Só WhatsApp e e-mail esperam.

ONDE MORA. Tabela `mensagens_voluntario`, no mesmo arquivo dos ciclos de
retenção (`retention_log.caminho_do_banco`): dentro da simulação do gateway o
arquivo é o da simulação, como o resto. Uma linha por oferta que esperou ou
espera a escolha, ligada ao ciclo de retenção (`ciclo_id`).

LGPD. O telefone de destino (`destino`) só fica guardado enquanto a mensagem
espera; sai no envio ou no descarte. O texto das mensagens é apagado
`retencao_mensagens_dias` depois do envio (o expurgo diário do relógio); fica a
abordagem. A anonimização de um titular apaga o texto e o destino dele na hora.
Nada de nome, e-mail ou CPF vai para o texto: as mensagens não usam o nome.

ESCALONAMENTO HUMANO ZERO. Todo texto, do modelo pronto ou da Claude API, passa
por `voluntary_agent.encaminha_para_humano`; o gerado que falhar é trocado pelo
modelo pronto daquela abordagem.

Este módulo não importa `voluntary_agent` no topo (ele importa este): os
imports de lá são feitos dentro das funções.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .. import ambiente
from ..dunning import configuracao
from . import retention_log

logger = logging.getLogger(__name__)

ABORDAGENS = ("cuidado_proximo", "facilitacao", "direto_ao_ponto")
ROTULO_DA_ABORDAGEM = {"cuidado_proximo": "Cuidado próximo", "facilitacao": "Facilitação",
                       "direto_ao_ponto": "Direto ao ponto"}

AGUARDANDO = "aguardando"
ENVIADA = "enviada"
NAO_ENVIADA = "nao_enviada"     # a entrega falhou, ou o cliente pediu para não ser contatado
ESTADOS = (AGUARDANDO, ENVIADA, NAO_ENVIADA)

# Quem escolheu a mensagem que saiu.
POR_PRAZO = "prazo"             # o prazo venceu: saiu a recomendada
POR_AUTOMATICO = "automatico"   # a empresa passou para o modo automático com a mensagem esperando

CANAIS_QUE_ESPERAM = ("whatsapp", "email")
TEXTO_MAXIMO = 480
MAXIMO_DE_RODADAS = 10

# As ofertas ditas como substantivo, nos dois idiomas: o texto é "uma opção para
# você: <oferta>". Os rótulos do agente (`OFFER_LABELS`) são frases com verbo
# ("pausar a assinatura...") e não encaixam aqui.
OFERTA_NO_TEXTO = {
    "pt": {"desconto_10": "10% de desconto por 3 meses",
           "desconto_20": "20% de desconto por 3 meses",
           "pausa_1_mes": "uma pausa de 1 mês na assinatura, sem custo",
           "pix_boleto_flash": "a troca para Pix ou boleto em 1 clique"},
    "en": {"desconto_10": "10% off for 3 months",
           "desconto_20": "20% off for 3 months",
           "pausa_1_mes": "a free 1-month pause on your subscription",
           "pix_boleto_flash": "a one-click switch to Pix or bank slip"},
}
OFERTA_DESCONHECIDA = {"pt": "uma condição especial", "en": "a special offer"}

# Duas redações por abordagem: a rodada ímpar usa a primeira, a par a segunda
# ("Quero outro tipo de mensagem" sem a Claude API ainda muda o texto). Nenhum
# termo de encaminhamento humano; nada que dificulte cancelar (Decreto 11.034).
MODELOS = {
    "cuidado_proximo": (
        {"pt": "Olá! Notamos que você tem usado menos a plataforma, e queremos que ela continue "
               "valendo a pena para você. Por isso preparamos uma condição só sua: {oferta}. "
               "Se fizer sentido, é só responder esta mensagem.",
         "en": "Hi! We noticed you've been using the platform less, and we want it to keep "
               "being worth it for you. So we've prepared something just for you: {oferta}. "
               "If it makes sense, just reply to this message."},
        {"pt": "Olá! Você é importante para a gente, e queremos entender o que pode melhorar. "
               "Enquanto isso, deixamos reservado para você: {oferta}. Basta responder aqui "
               "para ativar.",
         "en": "Hi! You matter to us, and we want to understand what could be better. "
               "Meanwhile, we've set this aside for you: {oferta}. Just reply here to "
               "activate it."},
    ),
    "facilitacao": (
        {"pt": "Olá! Se algo está pesando no momento, a gente facilita. Uma opção para você: "
               "{oferta}. Para aceitar, basta responder SIM.",
         "en": "Hi! If something is weighing on you right now, we can make it easier. An "
               "option for you: {oferta}. To accept, just reply YES."},
        {"pt": "Olá! Para facilitar a sua rotina, liberamos para você: {oferta}. Não precisa "
               "preencher nada: responda esta mensagem e a gente ativa.",
         "en": "Hi! To make things easier, we've unlocked this for you: {oferta}. No forms to "
               "fill in: reply to this message and we'll turn it on."},
    ),
    "direto_ao_ponto": (
        {"pt": "Olá. Temos uma proposta para você continuar: {oferta}. Se preferir cancelar, "
               "o caminho continua o mesmo, sem nenhuma etapa a mais.",
         "en": "Hi. Here's an offer for you to stay: {oferta}. If you'd rather cancel, the "
               "way to do it stays the same, with no extra steps."},
        {"pt": "Olá. Uma oferta para você ficar: {oferta}. Para aceitar, responda esta "
               "mensagem. Cancelar continua igual, sem etapas extras.",
         "en": "Hi. An offer for you to stay: {oferta}. To accept, reply to this message. "
               "Cancelling stays the same, with no extra steps."},
    ),
}

_DDL = """
CREATE TABLE IF NOT EXISTS mensagens_voluntario (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           TEXT    NOT NULL,
    user_id             TEXT    NOT NULL,
    ciclo_id            INTEGER,
    criado_em           TEXT    NOT NULL,   -- UTC, ISO 8601
    prazo_ate           TEXT    NOT NULL,   -- UTC: sem escolha até aqui, sai a recomendada
    estado              TEXT    NOT NULL,   -- aguardando | enviada | nao_enviada
    oferta              TEXT    NOT NULL,
    canal               TEXT    NOT NULL,
    criticidade         TEXT,
    evento              TEXT,
    destino             TEXT,               -- telefone do WhatsApp; só enquanto espera
    sugestoes           TEXT    NOT NULL,   -- JSON: as rodadas de 3 mensagens
    escolhida_rodada    INTEGER,
    escolhida_abordagem TEXT,
    escolhida_por       TEXT,               -- owner | admin | member | prazo | automatico
    escolhida_em        TEXT,
    enviada_em          TEXT,
    motivo_nao_enviada  TEXT,
    texto_apagado_em    TEXT
);
CREATE INDEX IF NOT EXISTS idx_msg_vol_tenant ON mensagens_voluntario (tenant_id, estado);
CREATE INDEX IF NOT EXISTS idx_msg_vol_cliente ON mensagens_voluntario (tenant_id, user_id);
"""


def _conectar():
    conn = retention_log._conectar()
    conn.executescript(_DDL)
    return conn


def _utc(valor) -> Optional[datetime]:
    """Qualquer instante (com ou sem fuso; sem fuso é hora local da instalação) em UTC."""
    from ..api import datas                      # import tardio: `api` importa este pacote
    d = datas.com_fuso(valor)
    return d.astimezone(timezone.utc) if d is not None else None


def _texto_utc(valor) -> Optional[str]:
    d = _utc(valor)
    return d.isoformat(timespec="seconds") if d is not None else None


def agora_utc() -> datetime:
    """O agora do registro: o relógio simulado dentro da simulação do gateway."""
    simulado = ambiente.relogio_simulado()
    if simulado is not None:
        return _utc(simulado)
    return datetime.now(timezone.utc)


# ── O texto ────────────────────────────────────────────────────────────────

def abordagem_recomendada(evento: Optional[str], criticidade: Optional[str]) -> str:
    """A recomendada entre as 3:

      1. intenção explícita de sair (abriu a página de cancelamento, clicou em
         rebaixar): Direto ao ponto. O cliente já está decidindo; a mensagem
         diz a oferta e deixa claro que cancelar continua igual;
      2. risco grave: Cuidado próximo;
      3. o resto (preocupante): Facilitação.
    """
    from .risk_scorer import FIXED_RISK
    if evento in FIXED_RISK:
        return "direto_ao_ponto"
    if criticidade == "critico":
        return "cuidado_proximo"
    return "facilitacao"


def _rotulo_da_oferta(oferta: str, idioma: str) -> str:
    return OFERTA_NO_TEXTO[idioma].get(oferta, OFERTA_DESCONHECIDA[idioma])


def textos_do_modelo(abordagem: str, oferta: str, rodada: int) -> dict:
    """{"pt": ..., "en": ...}: o modelo pronto desta abordagem, nesta rodada."""
    variantes = MODELOS[abordagem]
    modelo = variantes[(max(1, rodada) - 1) % len(variantes)]
    return {i: modelo[i].format(oferta=_rotulo_da_oferta(oferta, i)) for i in ("pt", "en")}


def _redator_ligado() -> bool:
    """A Claude API só é chamada com a chave configurada: sem ela, o texto é o
    modelo pronto, sem tentar a chamada (e sem esperar ela falhar)."""
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


def _prompt(oferta: str, canal: str, criticidade: Optional[str], rodada: int) -> str:
    rotulo_canal = retention_log.ROTULOS_DE_CANAL.get(canal, "texto")
    tom = {"critico": "o cliente está entre os de maior risco da base",
           "alto": "o cliente mostra sinais de que pode cancelar"}.get(criticidade or "",
                                                                     "o cliente mostrou risco")
    return "\n".join([
        f"Escreva 3 versões de uma mensagem de {rotulo_canal} para um cliente de uma assinatura "
        f"que pode cancelar ({tom}).",
        f"A oferta, que já foi decidida e não muda: {_rotulo_da_oferta(oferta, 'pt')}.",
        "As três abordagens:",
        '- "cuidado_proximo": pessoal e atencioso, reconhece a relação;',
        '- "facilitacao": mostra o jeito mais fácil de aceitar a oferta agora;',
        '- "direto_ao_ponto": diz a oferta sem rodeios e deixa claro que cancelar continua '
        "igual, sem etapas a mais.",
        "Regras: no máximo 3 frases cada; português brasileiro natural; sem culpar o cliente; "
        "não use o nome do cliente; não ofereça contato com pessoas (atendimento, suporte, "
        "ligação, especialista, equipe); não invente prazos, multas ou condições além da "
        "oferta; nunca dificulte o cancelamento; não assine; não diga que a mensagem é "
        "automática." + (" Escreva diferente das versões anteriores." if rodada > 1 else ""),
        'Responda APENAS com um objeto JSON com as chaves "cuidado_proximo", "facilitacao" e '
        '"direto_ao_ponto", cada uma com o texto.',
    ])


def _texto_valido(texto) -> bool:
    from .voluntary_agent import encaminha_para_humano
    return (isinstance(texto, str) and bool(texto.strip()) and len(texto) <= TEXTO_MAXIMO
            and encaminha_para_humano(texto) is None)


def _json(bruto: str) -> dict:
    inicio, fim = bruto.find("{"), bruto.rfind("}")
    if inicio < 0 or fim <= inicio:
        return {}
    try:
        dados = json.loads(bruto[inicio:fim + 1])
    except ValueError:
        return {}
    return dados if isinstance(dados, dict) else {}


async def _gerados(oferta: str, canal: str, criticidade: Optional[str], rodada: int) -> dict:
    """{abordagem: texto} da Claude API, só os que passaram no filtro. Vazio se
    o redator está desligado ou a chamada falhou. Nunca levanta."""
    if not _redator_ligado():
        return {}
    from .voluntary_agent import claude
    try:
        resposta = await claude.messages.create(
            model="claude-sonnet-4-20250514", max_tokens=700,
            messages=[{"role": "user", "content": _prompt(oferta, canal, criticidade, rodada)}])
        dados = _json(resposta.content[0].text)
    except Exception as e:                       # noqa: BLE001
        logger.warning("[MSG-VOL] Claude API indisponível (%r): modelos prontos", e)
        return {}
    return {a: dados[a].strip() for a in ABORDAGENS if _texto_valido(dados.get(a))}


async def gerar_rodada(oferta: str, canal: str, criticidade: Optional[str],
                       evento: Optional[str], rodada: int) -> list:
    """As 3 sugestões de uma rodada, na ordem de `ABORDAGENS`, com a recomendada marcada."""
    gerados = await _gerados(oferta, canal, criticidade, rodada)
    recomendada = abordagem_recomendada(evento, criticidade)
    rodada_ = []
    for a in ABORDAGENS:
        if a in gerados:
            textos, origem = {"pt": gerados[a]}, "gerado"
        else:
            textos, origem = textos_do_modelo(a, oferta, rodada), "template"
        rodada_.append({"rodada": rodada, "abordagem": a, "recomendada": a == recomendada,
                        "origem": origem, "textos": textos})
    return rodada_


# ── O registro ─────────────────────────────────────────────────────────────

def espera_escolha(tenant_id: str, canal: Optional[str]) -> bool:
    """Esta oferta espera a escolha da empresa? Só no modo escolha, só por
    WhatsApp ou e-mail, e nunca dentro da simulação do gateway nem no modo de
    simulação do grafo (lá o aceite é sorteado logo depois do envio)."""
    from .voluntary_agent import modo_simulacao
    if ambiente.simulacao_ativa() or modo_simulacao():
        return False
    if canal not in CANAIS_QUE_ESPERAM:
        return False
    return configuracao.ler(tenant_id)["modo_mensagem_voluntario"] == configuracao.MODO_ESCOLHA


async def criar(state: dict) -> Optional[int]:
    """Grava a oferta que vai esperar a escolha, com a 1ª rodada de mensagens.
    Devolve o id, ou None se a gravação falhou (aí quem chama envia na hora:
    uma oferta decidida não fica perdida por falha de registro)."""
    tenant = state.get("tenant_id") or retention_log.TENANT_PADRAO
    oferta, canal = state["offer_type"], state["channel"]
    criticidade, evento = state.get("criticality"), state.get("event")
    agora = agora_utc()
    prazo = agora + timedelta(hours=configuracao.ler(tenant)["prazo_escolha_horas"])
    props = state.get("props") if isinstance(state.get("props"), dict) else {}
    destino = props.get("phone") if canal == "whatsapp" and isinstance(props.get("phone"), str) else None
    try:
        sugestoes = await gerar_rodada(oferta, canal, criticidade, evento, 1)
        with _conectar() as conn:
            cur = conn.execute(
                "INSERT INTO mensagens_voluntario (tenant_id, user_id, criado_em, prazo_ate, "
                "estado, oferta, canal, criticidade, evento, destino, sugestoes) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (tenant, state["user_id"], agora.isoformat(timespec="seconds"),
                 prazo.isoformat(timespec="seconds"), AGUARDANDO, oferta, canal, criticidade,
                 evento, destino, json.dumps(sugestoes, ensure_ascii=False)))
            return cur.lastrowid
    except Exception as e:                       # noqa: BLE001
        logger.error("[MSG-VOL] Falha ao gravar a mensagem pendente: %r", e)
        return None


def ligar_ciclo(mensagem_id: int, ciclo_id: Optional[int]) -> None:
    """Liga a mensagem ao ciclo de retenção gravado no fim do grafo. Nunca levanta."""
    if not mensagem_id or not ciclo_id:
        return
    try:
        with _conectar() as conn:
            conn.execute("UPDATE mensagens_voluntario SET ciclo_id = ? WHERE id = ?",
                         (ciclo_id, mensagem_id))
    except Exception as e:                       # noqa: BLE001
        logger.error("[MSG-VOL] Falha ao ligar a mensagem %s ao ciclo %s: %r",
                     mensagem_id, ciclo_id, e)


def _linha(l) -> dict:
    d = dict(l)
    try:
        d["sugestoes"] = json.loads(d.get("sugestoes") or "[]")
    except ValueError:
        d["sugestoes"] = []
    return d


def ler(tenant_id: str, mensagem_id: int) -> Optional[dict]:
    """A mensagem DESTE tenant; de outro tenant é igual a inexistente."""
    with _conectar() as conn:
        l = conn.execute("SELECT * FROM mensagens_voluntario WHERE id = ? AND tenant_id = ?",
                         (mensagem_id, tenant_id)).fetchone()
    return _linha(l) if l else None


def listar(tenant_id: str, so_aguardando: bool = True) -> list:
    """As mensagens do tenant, das mais antigas às mais novas (as que vencem
    primeiro vêm antes). Nunca levanta: sem leitura, a lista vem vazia."""
    try:
        sql = "SELECT * FROM mensagens_voluntario WHERE tenant_id = ?"
        if so_aguardando:
            sql += f" AND estado = '{AGUARDANDO}'"
        with _conectar() as conn:
            return [_linha(l) for l in conn.execute(sql + " ORDER BY prazo_ate, id",
                                                    (tenant_id,)).fetchall()]
    except Exception as e:                       # noqa: BLE001
        logger.error("[MSG-VOL] Falha ao listar as mensagens: %r", e)
        return []


def por_ciclo(tenant_id: str) -> dict:
    """{ciclo_id: mensagem}, para a lista de clientes mostrar o botão. Nunca levanta."""
    return {m["ciclo_id"]: m for m in listar(tenant_id, so_aguardando=False) if m["ciclo_id"]}


def contar_aguardando(tenant_id: str) -> int:
    try:
        with _conectar() as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM mensagens_voluntario WHERE tenant_id = ? AND estado = ?",
                (tenant_id, AGUARDANDO)).fetchone()[0])
    except Exception as e:                       # noqa: BLE001
        logger.error("[MSG-VOL] Falha ao contar as mensagens: %r", e)
        return 0


def aguardando_do_cliente(tenant_id: str, user_id: str) -> bool:
    """Este cliente já tem uma oferta esperando a escolha? Conta como oferta
    recente para o limite de contato: um segundo evento não abre outra."""
    try:
        with _conectar() as conn:
            return conn.execute(
                "SELECT 1 FROM mensagens_voluntario WHERE tenant_id = ? AND user_id = ? "
                "AND estado = ? LIMIT 1", (tenant_id, user_id, AGUARDANDO)).fetchone() is not None
    except Exception as e:                       # noqa: BLE001
        logger.error("[MSG-VOL] Falha ao ler a mensagem pendente do cliente: %r", e)
        return False


def ultima_rodada(sugestoes: list) -> list:
    if not sugestoes:
        return []
    n = max(s["rodada"] for s in sugestoes)
    return [s for s in sugestoes if s["rodada"] == n]


def recomendada(sugestoes: list) -> Optional[dict]:
    """A recomendada da rodada mais recente: a que sai se ninguém escolher."""
    rodada = ultima_rodada(sugestoes)
    return next((s for s in rodada if s.get("recomendada")), rodada[0] if rodada else None)


def texto_em(sugestao: Optional[dict], idioma: str = "pt") -> Optional[str]:
    if not sugestao:
        return None
    textos = sugestao.get("textos") or {}
    return textos.get(idioma) or textos.get("pt")


# ── Escolher, regerar e enviar ──────────────────────────────────────────────

class NaoAguarda(Exception):
    """A mensagem não espera mais a escolha (já escolhida, enviada, ou o prazo venceu)."""


class SugestaoInexistente(Exception):
    """A rodada e a abordagem pedidas não existem nesta mensagem."""


async def regerar(tenant_id: str, mensagem_id: int) -> dict:
    """Outras 3 mensagens para a mesma oferta: uma rodada nova. O prazo NÃO é
    reiniciado, e a recomendada da última rodada é a que sai por prazo."""
    m = ler(tenant_id, mensagem_id)
    if m is None or m["estado"] != AGUARDANDO:
        raise NaoAguarda()
    if _utc(m["prazo_ate"]) <= agora_utc():
        raise NaoAguarda("prazo")
    rodada = max((s["rodada"] for s in m["sugestoes"]), default=0) + 1
    if rodada > MAXIMO_DE_RODADAS:
        raise NaoAguarda("rodadas")
    novas = await gerar_rodada(m["oferta"], m["canal"], m["criticidade"], m["evento"], rodada)
    with _conectar() as conn:
        cur = conn.execute(
            "UPDATE mensagens_voluntario SET sugestoes = ? WHERE id = ? AND tenant_id = ? "
            "AND estado = ? AND escolhida_abordagem IS NULL",
            (json.dumps(m["sugestoes"] + novas, ensure_ascii=False), mensagem_id, tenant_id,
             AGUARDANDO))
        if cur.rowcount != 1:
            raise NaoAguarda()
    return {"rodada": rodada, "sugestoes": novas}


def registrar_escolha(tenant_id: str, mensagem_id: int, rodada: int, abordagem: str,
                      por: str) -> dict:
    """Marca a escolhida. A marca é a própria conferência: duas escolhas ao
    mesmo tempo (a empresa e o prazo) não passam as duas."""
    m = ler(tenant_id, mensagem_id)
    if m is None or m["estado"] != AGUARDANDO or m["escolhida_abordagem"]:
        raise NaoAguarda()
    if not any(s["rodada"] == rodada and s["abordagem"] == abordagem for s in m["sugestoes"]):
        raise SugestaoInexistente()
    with _conectar() as conn:
        cur = conn.execute(
            "UPDATE mensagens_voluntario SET escolhida_rodada = ?, escolhida_abordagem = ?, "
            "escolhida_por = ?, escolhida_em = ? WHERE id = ? AND tenant_id = ? AND estado = ? "
            "AND escolhida_abordagem IS NULL",
            (rodada, abordagem, por, agora_utc().isoformat(timespec="seconds"), mensagem_id,
             tenant_id, AGUARDANDO))
        if cur.rowcount != 1:
            raise NaoAguarda()
    return ler(tenant_id, mensagem_id)


def escolhida(m: dict) -> Optional[dict]:
    return next((s for s in m["sugestoes"] if s["rodada"] == m["escolhida_rodada"]
                 and s["abordagem"] == m["escolhida_abordagem"]), None)


async def enviar(tenant_id: str, mensagem_id: int) -> dict:
    """Envia a escolhida: WhatsApp pelo `whatsapp_sender`, e-mail simulado como
    no `send_offer`. A marca de "não contatar" é relida AGORA (pode ter vindo
    depois da oferta). O ciclo de retenção passa a contar como oferta enviada.
    Devolve a mensagem atualizada."""
    from . import voluntary_agent as va
    m = ler(tenant_id, mensagem_id)
    if m is None or m["estado"] != AGUARDANDO or not m["escolhida_abordagem"]:
        raise NaoAguarda()
    texto = texto_em(escolhida(m), "pt") or ""
    motivo = None
    if va.pediu_para_nao_ser_contatado(tenant_id, m["user_id"]):
        enviado, motivo = False, "nao_contatar"
    elif m["canal"] == "whatsapp":
        resultado = await va.send_whatsapp(m["destino"], va.com_linha_de_saida(texto),
                                           tenant_id=tenant_id)
        enviado = bool(resultado.get("sent"))
        motivo = None if enviado else (resultado.get("motivo") or "falha_na_entrega")
    else:
        logger.info("[MSG-VOL] Enviando via %s: %s", m["canal"].upper(), texto[:90])
        enviado = True
    agora = agora_utc().isoformat(timespec="seconds")
    with _conectar() as conn:
        conn.execute(
            "UPDATE mensagens_voluntario SET estado = ?, enviada_em = ?, motivo_nao_enviada = ?, "
            "destino = NULL WHERE id = ? AND tenant_id = ?",
            (ENVIADA if enviado else NAO_ENVIADA, agora if enviado else None, motivo,
             mensagem_id, tenant_id))
        if enviado and m["ciclo_id"]:
            conn.execute("UPDATE ciclos_retencao SET offer_sent = 1 WHERE id = ? AND tenant_id = ?",
                         (m["ciclo_id"], tenant_id))
    return ler(tenant_id, mensagem_id)


async def enviar_devidas(agora=None) -> dict:
    """A passagem do relógio: sai a recomendada de toda mensagem cujo prazo
    venceu, e de toda mensagem de empresa que passou para o modo automático.
    Fora da simulação, no banco de verdade. Uma mensagem que falhar não segura
    as outras."""
    limite = _utc(agora) if agora is not None else agora_utc()
    try:
        with _conectar() as conn:
            linhas = [_linha(l) for l in conn.execute(
                "SELECT * FROM mensagens_voluntario WHERE estado = ? ORDER BY id",
                (AGUARDANDO,)).fetchall()]
    except Exception as e:                       # noqa: BLE001
        logger.error("[MSG-VOL] Falha ao ler as mensagens pendentes: %r", e)
        return {"enviadas": 0, "falhas": 1}
    enviadas = falhas = 0
    for m in linhas:
        try:
            modo = configuracao.ler(m["tenant_id"])["modo_mensagem_voluntario"]
            if m["escolhida_abordagem"]:
                por = None                      # escolhida e não enviada: tenta de novo
            elif modo == configuracao.MODO_AUTOMATICO:
                por = POR_AUTOMATICO
            elif _utc(m["prazo_ate"]) <= limite:
                por = POR_PRAZO
            else:
                continue
            if por is not None:
                s = recomendada(m["sugestoes"])
                if s is None:
                    continue
                registrar_escolha(m["tenant_id"], m["id"], s["rodada"], s["abordagem"], por)
            await enviar(m["tenant_id"], m["id"])
            enviadas += 1
        except NaoAguarda:
            continue
        except Exception as e:                   # noqa: BLE001
            falhas += 1
            logger.error("[MSG-VOL] Falha ao enviar a mensagem %s: %r", m["id"], e)
    return {"enviadas": enviadas, "falhas": falhas}


# ── Retenção e titular ──────────────────────────────────────────────────────

def _sem_texto(sugestoes: list) -> str:
    return json.dumps([{**s, "textos": None} for s in sugestoes], ensure_ascii=False)


def apagar_texto_expirado(agora, tenant_id: str, dias: int) -> int:
    """Apaga o texto das mensagens enviadas (ou não entregues) há mais de `dias`.
    Fica a abordagem. LEVANTA se falhar (o expurgo não finge que apagou)."""
    corte = (_utc(agora) - timedelta(days=dias)).isoformat(timespec="seconds")
    with _conectar() as conn:
        linhas = conn.execute(
            "SELECT id, sugestoes FROM mensagens_voluntario WHERE tenant_id = ? AND estado != ? "
            "AND texto_apagado_em IS NULL AND COALESCE(enviada_em, escolhida_em, criado_em) < ?",
            (tenant_id, AGUARDANDO, corte)).fetchall()
        quando = _texto_utc(agora)
        for l in linhas:
            conn.execute("UPDATE mensagens_voluntario SET sugestoes = ?, destino = NULL, "
                         "texto_apagado_em = ? WHERE id = ?",
                         (_sem_texto(json.loads(l["sugestoes"] or "[]")), quando, l["id"]))
    return len(linhas)


def tenants_com_texto() -> list:
    with _conectar() as conn:
        return [l[0] for l in conn.execute(
            "SELECT DISTINCT tenant_id FROM mensagens_voluntario WHERE texto_apagado_em IS NULL "
            "AND estado != ?", (AGUARDANDO,)).fetchall()]


def apagar_do_cliente(tenant_id: str, user_ids: list, agora) -> int:
    """Anonimização de um titular: o texto e o destino de todas as mensagens
    dele saem agora, e a que esperava a escolha não sai mais. Devolve quantas."""
    if not user_ids:
        return 0
    marcas = ",".join("?" for _ in user_ids)
    with _conectar() as conn:
        linhas = conn.execute(
            f"SELECT id, sugestoes, estado FROM mensagens_voluntario WHERE tenant_id = ? "
            f"AND user_id IN ({marcas}) AND texto_apagado_em IS NULL",
            (tenant_id, *user_ids)).fetchall()
        quando = _texto_utc(agora)
        for l in linhas:
            conn.execute(
                "UPDATE mensagens_voluntario SET sugestoes = ?, destino = NULL, "
                "texto_apagado_em = ?, estado = CASE WHEN estado = ? THEN ? ELSE estado END, "
                "motivo_nao_enviada = CASE WHEN estado = ? THEN 'nao_contatar' "
                "ELSE motivo_nao_enviada END WHERE id = ?",
                (_sem_texto(json.loads(l["sugestoes"] or "[]")), quando, AGUARDANDO,
                 NAO_ENVIADA, AGUARDANDO, l["id"]))
    return len(linhas)


def do_cliente(tenant_id: str, user_id: str) -> list:
    """As mensagens deste titular, para a exportação (LGPD, Art. 18). Nunca levanta."""
    try:
        with _conectar() as conn:
            return [_linha(l) for l in conn.execute(
                "SELECT * FROM mensagens_voluntario WHERE tenant_id = ? AND user_id = ? "
                "ORDER BY id", (tenant_id, user_id)).fetchall()]
    except Exception as e:                       # noqa: BLE001
        logger.error("[MSG-VOL] Falha ao ler as mensagens do titular: %r", e)
        return []

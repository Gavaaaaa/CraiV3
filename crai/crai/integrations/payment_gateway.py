"""crai/integrations/payment_gateway.py — Camada de adaptação de PSP.

Isola o pipeline do formato de evento de cada provedor de pagamento. O agente
nunca vê o payload cru do PSP: vê sempre o mesmo schema normalizado, o que
permite trocar de PSP de Pix (Iugu, Pagar.me, outro) sem tocar em
crai/agent/ nem em crai/ml/.

Referências do formato de entrada:
  - Iugu, Pix Automático por API (objeto `automatic_pix`, parâmetro `journey`,
    objetos de resposta 'pix automático' e 'pix') — referência primária, é a
    documentação pública mais concreta disponível hoje:
    dev.iugu.com/docs/cobrar-com-pix-automatico-por-api
  - BACEN, especificação oficial do fluxo do Recebedor, independente de PSP:
    github.com/bacen/pix-api

O parsing é deliberadamente tolerante a variações de nome de campo (ver
`_primeiro_preenchido`): o PSP definitivo ainda não foi fechado pela equipe, e
o que precisa permanecer estável é o schema de SAÍDA, não o de entrada.

TOLERANTE NÃO É SILENCIOSO — a regra do Sprint 1
------------------------------------------------
Toda degradação aplicada durante o parsing é registrada em `logger.warning`
**e** marcada no evento normalizado, no campo `degradacoes`. O pipeline nunca
recebe um default sem saber que é um default. E o que este módulo não sabe
interpretar ele **recusa** (`PayloadPixInvalido` → HTTP 422), em vez de
entregar um evento inventado — diagnosticar uma cobrança de R$ 0 é pior que
devolver 422, porque o e-Profit vai a zero e o churn involuntário legítimo é
descartado sem rastro.

PRIVACIDADE — o schema normalizado contém cinco campos de DADO:

    e2e_id, valor, status, ispb_pagador, id_recorrencia

…mais um sexto campo, `degradacoes`, que é **metadado de qualidade do
parsing**: uma lista de rótulos fixos, tirados de `DEGRADACOES_CONHECIDAS`.
Nenhum valor vindo do payload entra nele, então a promessa de privacidade
continua valendo sobre os cinco.

A chave Pix do pagador (CPF / telefone / e-mail) **nunca** entra no schema.
Quando precisa ser guardada para conciliação, vai cifrada por
`PixAutomaticoAdapter.store_encrypted_pix_key()`, num arquivo segregado, e não
tem nenhum caminho de código até crai/ml/ ou crai/agent/.
"""

import json
import logging
import math
import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from ..security.tokenization import encrypt_sensitive_field

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
VAULT_PATH = BASE_DIR / "vault" / "pix_keys.json"

CARD_NOT_IMPLEMENTED = (
    "Suporte a cartão de crédito/débito planejado para roadmap futuro — "
    "não implementado nesta fase"
)

# ── Status normalizados (os 4 eventos cobertos por esta fase) ────────────
STATUS_AUTORIZACAO_CONCEDIDA = "autorizacao_concedida"
STATUS_AUTORIZACAO_REVOGADA = "autorizacao_revogada"
STATUS_COBRANCA_CONFIRMADA = "cobranca_confirmada"
STATUS_COBRANCA_FALHADA = "cobranca_falhada"
STATUS_DESCONHECIDO = "desconhecido"

PIX_STATUSES = frozenset({
    STATUS_AUTORIZACAO_CONCEDIDA,
    STATUS_AUTORIZACAO_REVOGADA,
    STATUS_COBRANCA_CONFIRMADA,
    STATUS_COBRANCA_FALHADA,
})

# Nomes de evento do PSP → status normalizado. Aceita as variações mais
# prováveis (Iugu usa 'automatic_pix.*'; o BACEN fala em recorrência).
EVENT_STATUS_MAP = {
    "automatic_pix.authorization_created":   STATUS_AUTORIZACAO_CONCEDIDA,
    "automatic_pix.authorization_approved":  STATUS_AUTORIZACAO_CONCEDIDA,
    "recurrence.authorized":                 STATUS_AUTORIZACAO_CONCEDIDA,
    "automatic_pix.authorization_revoked":   STATUS_AUTORIZACAO_REVOGADA,
    "automatic_pix.authorization_cancelled": STATUS_AUTORIZACAO_REVOGADA,
    "recurrence.revoked":                    STATUS_AUTORIZACAO_REVOGADA,
    "automatic_pix.charge_succeeded":        STATUS_COBRANCA_CONFIRMADA,
    "automatic_pix.charge_paid":             STATUS_COBRANCA_CONFIRMADA,
    "recurrence.charge_confirmed":           STATUS_COBRANCA_CONFIRMADA,
    "automatic_pix.charge_failed":           STATUS_COBRANCA_FALHADA,
    "automatic_pix.charge_declined":         STATUS_COBRANCA_FALHADA,
    "recurrence.charge_failed":              STATUS_COBRANCA_FALHADA,
}

# ── Status CRU do PSP → status normalizado ───────────────────────────────
# Nem todo PSP manda nome de evento; muitos mandam só o status do objeto. Sem
# este mapa, `failed` nunca casava contra PIX_STATUSES (que só tem tokens
# internos em português) e TODA cobrança falhada desses PSPs virava
# `desconhecido` → HTTP 200 {"pipeline": false}, silenciosamente.
#
# São DOIS mapas, e não um, porque o nome do campo carrega a semântica:
# `authorization_status: "approved"` é a AUTORIZAÇÃO concedida, não uma
# cobrança confirmada. Achatar os dois no mesmo mapa transformaria toda
# autorização aprovada numa cobrança paga que nunca aconteceu.
STATUS_CRU_COBRANCA = {
    "failed": STATUS_COBRANCA_FALHADA,
    "declined": STATUS_COBRANCA_FALHADA,
    "rejected": STATUS_COBRANCA_FALHADA,
    "refused": STATUS_COBRANCA_FALHADA,
    "error": STATUS_COBRANCA_FALHADA,
    "unpaid": STATUS_COBRANCA_FALHADA,
    "paid": STATUS_COBRANCA_CONFIRMADA,
    "succeeded": STATUS_COBRANCA_CONFIRMADA,
    "success": STATUS_COBRANCA_CONFIRMADA,
    "approved": STATUS_COBRANCA_CONFIRMADA,
    "settled": STATUS_COBRANCA_CONFIRMADA,
    "confirmed": STATUS_COBRANCA_CONFIRMADA,
}

STATUS_CRU_AUTORIZACAO = {
    "approved": STATUS_AUTORIZACAO_CONCEDIDA,
    "authorized": STATUS_AUTORIZACAO_CONCEDIDA,
    "active": STATUS_AUTORIZACAO_CONCEDIDA,
    "created": STATUS_AUTORIZACAO_CONCEDIDA,
    "revoked": STATUS_AUTORIZACAO_REVOGADA,
    "cancelled": STATUS_AUTORIZACAO_REVOGADA,
    "canceled": STATUS_AUTORIZACAO_REVOGADA,
    "expired": STATUS_AUTORIZACAO_REVOGADA,
}

# ── Qualidade do parsing (campo `degradacoes`) ───────────────────────────
DEGRADACAO_ENVELOPE_AUSENTE = "envelope_ausente"
DEGRADACAO_LOTE_DE_1 = "lote_de_1"
DEGRADACAO_VALOR_AUSENTE = "valor_ausente"
DEGRADACAO_VALOR_ILEGIVEL = "valor_ilegivel"
DEGRADACAO_VALOR_NAO_POSITIVO = "valor_nao_positivo"
DEGRADACAO_STATUS_DESCONHECIDO = "status_desconhecido"

DEGRADACOES_CONHECIDAS = frozenset({
    DEGRADACAO_ENVELOPE_AUSENTE,
    DEGRADACAO_LOTE_DE_1,
    DEGRADACAO_VALOR_AUSENTE,
    DEGRADACAO_VALOR_ILEGIVEL,
    DEGRADACAO_VALOR_NAO_POSITIVO,
    DEGRADACAO_STATUS_DESCONHECIDO,
})

# Degradações que impedem o evento de entrar no pipeline de recuperação: sem
# saber o valor, não há LTV, não há e-Profit e não há decisão defensável.
DEGRADACOES_BLOQUEANTES = frozenset({
    DEGRADACAO_VALOR_AUSENTE,
    DEGRADACAO_VALOR_ILEGIVEL,
    DEGRADACAO_VALOR_NAO_POSITIVO,
})

# ── Motivos de recusa (viram HTTP 422 na borda) ──────────────────────────
MOTIVO_LOTE_NAO_SUPORTADO = "lote_nao_suportado"
MOTIVO_PAYLOAD_NAO_E_OBJETO = "payload_nao_e_objeto"
MOTIVO_SEM_IDENTIFICACAO = "evento_sem_identificacao"


class PayloadPixInvalido(Exception):
    """Payload que a CRAI recusa processar.

    Recusar é uma decisão de projeto, não uma falha: um lote processado pela
    metade ou um payload que não é objeto JSON não têm normalização correta
    possível, e inventar uma é pior que devolver 422 para o PSP retentar.
    """

    def __init__(self, motivo: str, detalhe: str = ""):
        self.motivo = motivo
        self.detalhe = detalhe
        super().__init__(f"{motivo}: {detalhe}" if detalhe else motivo)


# Campos do payload que carregam a chave Pix do pagador. Existem só para serem
# EXCLUÍDOS do schema normalizado e cifrados à parte — nunca copiados adiante.
CAMPOS_CHAVE_PIX = ("pix_key", "chave", "chave_pix", "key", "payer_key")


def _primeiro_preenchido(fonte: dict, *caminhos: str, default=None):
    """Primeiro caminho pontilhado que exista e não seja vazio.

    Ex.: _primeiro_preenchido(data, "pix.end_to_end_id", "e2e_id")
    Absorve a variação de nomenclatura entre PSPs sem espalhar `.get()`
    encadeado pelo parser.
    """
    for caminho in caminhos:
        atual = fonte
        for parte in caminho.split("."):
            if not isinstance(atual, dict):
                atual = None
                break
            atual = atual.get(parte)
        if atual not in (None, "", {}):
            return atual
    return default


# Símbolo de moeda, espaço comum e espaço não-quebrável (PSPs que formatam para
# exibição costumam mandar   entre "R$" e o número).
_LIXO_MONETARIO = str.maketrans("", "", "R$  \t\n")


# Um único separador seguido de EXATAMENTE três dígitos, com 1 a 3 dígitos
# antes: "10,000", "1.299", "2,500". Sem o locale declarado pelo PSP não existe
# leitura correta — pt-BR lê mil, en-US lê um vírgula alguma coisa, e as duas
# são plausíveis. Ver `_para_float`.
_GRUPO_DE_MILHAR_AMBIGUO = re.compile(r"^[+-]?\d{1,3}[.,]\d{3}$")


def _para_float(bruto) -> Optional[float]:
    """Converte para float qualquer forma plausível de valor monetário.

    Aceita `None`, `int`, `float`, e strings em pt-BR (`"299,90"`,
    `"1.299,90"`, `"R$ 1.299,90"`) ou en-US (`"299.90"`, `"1,299.90"`).
    Quando os dois separadores aparecem, o que vem **por último** é o decimal e
    o outro é milhar — isso não tem ambiguidade.

    **Recusa o que é ambíguo, em vez de chutar.** `"10,000"` pode ser dez mil
    (en-US) ou dez (pt-BR); `"1.299"` pode ser mil duzentos e noventa e nove
    (pt-BR) ou um vírgula duzentos e noventa e nove (en-US). Chutar aqui
    transformaria R$ 10.000,00 em R$ 10,00 **em silêncio** — o pior desfecho
    possível, porque atravessa o portão de qualidade e vira decisão de negócio
    com o valor errado. Nesses casos devolve `None`, o evento é marcado como
    degradado e a borda recusa com 422. Um 422 que o PSP retenta com o campo
    bem formatado é barato; uma cobrança diagnosticada por um centésimo do
    valor real, não.

    Também recusa `inf` e `nan`: `json.loads` aceita os literais `Infinity` e
    `NaN`, e qualquer um deles atravessando o parser reproduz o P0-1 um andar
    adiante (o sklearn levanta `ValueError` e o FastAPI devolve 500).

    **Nunca levanta.** Quem chama decide se o `None` vira degradação ou recusa.
    """
    if bruto is None or isinstance(bruto, bool):
        return None
    if isinstance(bruto, (int, float)):
        valor = float(bruto)
        return valor if math.isfinite(valor) else None
    if not isinstance(bruto, str):
        return None

    texto = bruto.translate(_LIXO_MONETARIO)
    if not texto:
        return None

    if _GRUPO_DE_MILHAR_AMBIGUO.match(texto):
        logger.warning(
            "[PIX] Valor %r é ambíguo (um separador seguido de exatamente três "
            "dígitos): milhar ou decimal depende do locale do PSP, que o payload "
            "não declara. Recusando em vez de adivinhar.", bruto,
        )
        return None

    ultima_virgula = texto.rfind(",")
    ultimo_ponto = texto.rfind(".")
    if ultima_virgula > ultimo_ponto:
        texto = texto.replace(".", "").replace(",", ".")
    elif ultimo_ponto > ultima_virgula:
        texto = texto.replace(",", "")

    try:
        valor = float(texto)
    except ValueError:
        return None
    return valor if math.isfinite(valor) else None


class PaymentGatewayAdapter(ABC):
    """Contrato de qualquer PSP: entrega sempre o mesmo schema normalizado."""

    @abstractmethod
    async def parse_pix_event(self, raw_payload: dict) -> dict:
        """Normaliza um evento de Pix Automático do PSP.

        Returns:
            dict com as chaves de dado e2e_id, valor, status, ispb_pagador e
            id_recorrencia, mais `degradacoes` — a lista de defaults que
            precisaram ser aplicados durante o parsing (vazia = evento íntegro).
        """

    def parse_card_event(self, raw_payload: dict) -> dict:
        """Reservado para o adapter de cartão do roadmap futuro.

        A assinatura existe para que a interface já esteja pronta, mas nenhuma
        subclasse desta fase a implementa: o escopo da Fase 3 é exclusivamente
        Pix Automático.
        """
        raise NotImplementedError(CARD_NOT_IMPLEMENTED)


class PixAutomaticoAdapter(PaymentGatewayAdapter):
    """Adapta eventos de Pix Automático para o schema normalizado da CRAI."""

    async def parse_pix_event(self, raw_payload: dict) -> dict:
        """Normaliza os 4 eventos de Pix Automático cobertos por esta fase.

        Args:
            raw_payload: corpo do webhook do PSP, já desserializado

        Returns:
            {e2e_id, valor, status, ispb_pagador, id_recorrencia, degradacoes}.
            Os cinco primeiros são dado; `degradacoes` é a lista (possivelmente
            vazia) dos defaults que precisaram ser aplicados. A chave Pix do
            pagador é deliberadamente descartada.

        Raises:
            PayloadPixInvalido: payload que não é objeto JSON, ou lote com mais
                de um evento. Vira HTTP 422 na borda.
        """
        if not isinstance(raw_payload, dict):
            raise PayloadPixInvalido(
                MOTIVO_PAYLOAD_NAO_E_OBJETO,
                f"corpo do webhook é {type(raw_payload).__name__}, esperado objeto JSON",
            )

        degradacoes: list[str] = []
        dados = self._resolver_envelope(raw_payload, degradacoes)

        normalizado = {
            "e2e_id": str(_primeiro_preenchido(
                dados, "pix.end_to_end_id", "pix.e2e_id", "end_to_end_id", "e2e_id",
                default="",
            )),
            "valor": self._extrair_valor(dados, degradacoes),
            "status": self._extrair_status(raw_payload, dados, degradacoes),
            "ispb_pagador": str(_primeiro_preenchido(
                dados, "pix.payer.ispb", "payer.ispb", "debtor.ispb",
                "pix.debtor_ispb", "ispb_pagador", "ispb",
                default="",
            )),
            "id_recorrencia": str(_primeiro_preenchido(
                dados, "automatic_pix.recurrence_id", "automatic_pix.id",
                "recurrence_id", "id_recorrencia", "recurrence.id",
                default="",
            )),
            "degradacoes": degradacoes,
        }

        logger.info(
            "[PIX] Evento normalizado: status=%s | e2e=%s | recorrencia=%s | "
            "ISPB=%s | degradacoes=%s",
            normalizado["status"], normalizado["e2e_id"][:16],
            normalizado["id_recorrencia"], normalizado["ispb_pagador"],
            degradacoes or "nenhuma",
        )
        return normalizado

    @staticmethod
    def _resolver_envelope(raw_payload: dict, degradacoes: list[str]) -> dict:
        """Decide, de forma determinística, de onde os campos são lidos.

        O acesso anterior — um `get("data")` encadeado com `or` no envelope —
        tinha dois defeitos que dependiam do humor do PSP:

          - `data: [{...}]` (lote) → o `or` não dispara, o parser lê os campos
            de uma LISTA e devolve o normalizado inteiro em default. O pipeline
            rodava em cima de um evento vazio como se fosse válido (P0-2).
          - `data: {}` → o `or` dispara e o parser lê o ENVELOPE; `data: {...}`
            faz ele ler o `data`. Duas fontes diferentes para o mesmo webhook,
            escolhidas por o PSP mandar o campo vazio ou ausente (P0-3).
        """
        dados = raw_payload.get("data")

        if isinstance(dados, dict) and dados:
            return dados

        if isinstance(dados, list):
            if len(dados) > 1:
                raise PayloadPixInvalido(
                    MOTIVO_LOTE_NAO_SUPORTADO,
                    f"{len(dados)} eventos num único payload — processar um lote "
                    f"pela metade é pior que recusá-lo inteiro",
                )
            if len(dados) == 1 and isinstance(dados[0], dict):
                logger.warning("[PIX] Envelope veio como lote de 1 — desempacotado.")
                degradacoes.append(DEGRADACAO_LOTE_DE_1)
                return dados[0]

        logger.warning(
            "[PIX] Envelope 'data' ausente ou inutilizável (%s) — lendo do nível raiz.",
            type(dados).__name__,
        )
        degradacoes.append(DEGRADACAO_ENVELOPE_AUSENTE)
        return raw_payload

    @staticmethod
    def _extrair_valor(dados: dict, degradacoes: list[str]) -> float:
        """Valor em reais. PSPs costumam expor centavos (`*_cents`).

        Nunca levanta e nunca devolve um `0.0` mudo: quando o valor não pôde
        ser lido, o zero vem acompanhado de degradação + warning, e a borda
        recusa o evento antes que ele entre no pipeline (P0-1 e P0-5).
        """
        bruto_centavos = _primeiro_preenchido(
            dados, "total_cents", "amount_cents", "pix.amount_cents", "valor_centavos",
        )
        if bruto_centavos is not None:
            centavos = _para_float(bruto_centavos)
            if centavos is None:
                logger.warning("[PIX] Campo de centavos ilegível (%r) — valor "
                               "degradado para 0.0", bruto_centavos)
                degradacoes.append(DEGRADACAO_VALOR_ILEGIVEL)
                return 0.0
            return PixAutomaticoAdapter._validar_positivo(
                round(centavos / 100, 2), bruto_centavos, degradacoes)

        bruto_reais = _primeiro_preenchido(dados, "pix.valor", "valor", "amount", "total")
        if bruto_reais is None:
            logger.warning("[PIX] Nenhum campo de valor no payload — valor degradado para 0.0")
            degradacoes.append(DEGRADACAO_VALOR_AUSENTE)
            return 0.0

        reais = _para_float(bruto_reais)
        if reais is None:
            logger.warning("[PIX] Valor ilegível (%r) — valor degradado para 0.0", bruto_reais)
            degradacoes.append(DEGRADACAO_VALOR_ILEGIVEL)
            return 0.0
        return PixAutomaticoAdapter._validar_positivo(
            round(reais, 2), bruto_reais, degradacoes)

    @staticmethod
    def _validar_positivo(valor: float, bruto, degradacoes: list[str]) -> float:
        """Uma cobrança de R$ 0,00 ou negativa não é uma cobrança.

        Sem isto, um valor negativo atravessava o parser, virava LTV negativo,
        e-Profit negativo e — porque o pipeline segue até o fim — um deal no
        HubSpot com valor negativo. Melhor recusar na borda.
        """
        if valor > 0:
            return valor
        logger.warning(
            "[PIX] Valor não-positivo (%r → %.2f) — uma cobrança de R$ 0,00 ou "
            "negativa não é uma cobrança.", bruto, valor,
        )
        degradacoes.append(DEGRADACAO_VALOR_NAO_POSITIVO)
        return valor

    @staticmethod
    def _extrair_status(raw_payload: dict, dados: dict, degradacoes: list[str]) -> str:
        """Nome do evento do PSP → status normalizado, com três tentativas.

        1. `EVENT_STATUS_MAP` pelo nome do evento (caminho feliz).
        2. Status da **cobrança** (`status`, `charge_status`, `payment_status`).
           É o que conserta o P0-4: antes, o fallback comparava o status cru do
           PSP contra `PIX_STATUSES`, que só tem tokens internos em português —
           `"failed"` nunca casava, virava `desconhecido`, e a cobrança falhada
           sumia com HTTP 200. O branch era código morto.
        3. Status da **autorização** (`authorization_status`), só depois.
        4. Token interno da CRAI, para o PSP que já fala o dialeto de saída.

        **A ordem entre 2 e 3 importa e já esteve errada.** O campo
        `authorization_status` descreve o CONTRATO de recorrência, que continua
        aprovado enquanto o pagador não revoga; `status` descreve o QUE
        ACONTECEU nesta cobrança. Ler a autorização primeiro fazia um payload
        com `authorization_status: "approved"` e `status: "failed"` — que é a
        forma mais comum, e é literalmente o formato da fixture de referência
        deste repositório — virar `autorizacao_concedida`, sem degradação e sem
        warning. A cobrança falhada sumia de novo, e de um jeito pior que o
        P0-4 original, que ao menos logava.

        Cada campo é consultado no seu mapa primeiro e no outro depois: PSPs
        existem que mandam `revoked` no campo `status`, e perder isso foi o
        preço de dividir o mapa único em dois.
        """
        evento = _primeiro_preenchido(
            raw_payload, "event", "event_type", "type", default="",
        )
        status = EVENT_STATUS_MAP.get(str(evento).strip().lower())
        if status:
            return status

        cobranca = str(_primeiro_preenchido(
            dados, "status", "charge_status", "payment_status", default="",
        )).strip().lower()
        if cobranca:
            status = (STATUS_CRU_COBRANCA.get(cobranca)
                      or STATUS_CRU_AUTORIZACAO.get(cobranca))
            if status:
                return status
            if cobranca in PIX_STATUSES:
                return cobranca

        autorizacao = str(_primeiro_preenchido(
            dados, "automatic_pix.authorization_status", "authorization_status",
            default="",
        )).strip().lower()
        if autorizacao:
            status = (STATUS_CRU_AUTORIZACAO.get(autorizacao)
                      or STATUS_CRU_COBRANCA.get(autorizacao))
            if status:
                return status
            if autorizacao in PIX_STATUSES:
                return autorizacao

        logger.warning(
            "[PIX] Evento não reconhecido (event=%r, status=%r, authorization_status=%r)",
            evento, cobranca, autorizacao,
        )
        degradacoes.append(DEGRADACAO_STATUS_DESCONHECIDO)
        return STATUS_DESCONHECIDO

    # ── Conciliação: chave Pix cifrada, fora do caminho do pipeline ──────
    def store_encrypted_pix_key(self, id_recorrencia: str, raw_payload: dict) -> str:
        """Cifra e persiste a chave Pix do pagador, para conciliação financeira.

        Fica num arquivo segregado (`crai/vault/pix_keys.json`, fora do git),
        indexado pelo id da recorrência. Nenhum módulo de crai/ml/ ou
        crai/agent/ lê esse arquivo — a única forma de recuperar o valor é
        `decrypt_sensitive_field`, com a chave do ambiente.

        Returns:
            O token cifrado (nunca a chave em texto puro).

        Raises:
            EncryptionKeyMissing: se CRAI_ENCRYPTION_KEY não estiver definida.
                Falha explícita é melhor que persistir em texto puro.
            ValueError: se o payload não contiver chave Pix.
            PayloadPixInvalido: se o payload não for objeto JSON ou for um lote.
        """
        if not isinstance(raw_payload, dict):
            raise PayloadPixInvalido(
                MOTIVO_PAYLOAD_NAO_E_OBJETO,
                f"corpo é {type(raw_payload).__name__}, esperado objeto JSON",
            )
        # Mesma resolução de envelope do parser: um único lugar decide de onde
        # os campos são lidos, senão o cofre e o pipeline podem discordar sobre
        # qual evento estão olhando.
        dados = self._resolver_envelope(raw_payload, [])
        chave = _primeiro_preenchido(
            dados,
            *[f"pix.payer.{c}" for c in CAMPOS_CHAVE_PIX],
            *[f"payer.{c}" for c in CAMPOS_CHAVE_PIX],
            *CAMPOS_CHAVE_PIX,
        )
        if not chave:
            raise ValueError("Payload não contém chave Pix do pagador")

        token = encrypt_sensitive_field(str(chave))

        VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(VAULT_PATH, encoding="utf-8") as f:
                cofre = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            cofre = {}

        cofre[str(id_recorrencia)] = token
        with open(VAULT_PATH, "w", encoding="utf-8") as f:
            json.dump(cofre, f, indent=2, ensure_ascii=False)

        # O log registra o id da recorrência, jamais a chave.
        logger.info("[PIX] Chave Pix cifrada e arquivada (recorrencia=%s)", id_recorrencia)
        return token

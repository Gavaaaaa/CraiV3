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

PRIVACIDADE — o schema normalizado contém APENAS cinco campos:

    e2e_id, valor, status, ispb_pagador, id_recorrencia

A chave Pix do pagador (CPF / telefone / e-mail) **nunca** entra nele. Quando
precisa ser guardada para conciliação, vai cifrada por
`PixAutomaticoAdapter.store_encrypted_pix_key()`, num arquivo segregado, e não
tem nenhum caminho de código até crai/ml/ ou crai/agent/.
"""

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path

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


class PaymentGatewayAdapter(ABC):
    """Contrato de qualquer PSP: entrega sempre o mesmo schema normalizado."""

    @abstractmethod
    async def parse_pix_event(self, raw_payload: dict) -> dict:
        """Normaliza um evento de Pix Automático do PSP.

        Returns:
            dict com exatamente as chaves e2e_id, valor, status,
            ispb_pagador e id_recorrencia.
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
            {e2e_id, valor, status, ispb_pagador, id_recorrencia} — e nada mais.
            A chave Pix do pagador é deliberadamente descartada aqui.
        """
        dados = raw_payload.get("data") or raw_payload

        normalizado = {
            "e2e_id": str(_primeiro_preenchido(
                dados, "pix.end_to_end_id", "pix.e2e_id", "end_to_end_id", "e2e_id",
                default="",
            )),
            "valor": self._extrair_valor(dados),
            "status": self._extrair_status(raw_payload, dados),
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
        }

        logger.info(
            "[PIX] Evento normalizado: status=%s | e2e=%s | recorrencia=%s | ISPB=%s",
            normalizado["status"], normalizado["e2e_id"][:16],
            normalizado["id_recorrencia"], normalizado["ispb_pagador"],
        )
        return normalizado

    @staticmethod
    def _extrair_valor(dados: dict) -> float:
        """Valor em reais. PSPs costumam expor centavos (`*_cents`)."""
        centavos = _primeiro_preenchido(
            dados, "total_cents", "amount_cents", "pix.amount_cents", "valor_centavos",
        )
        if centavos is not None:
            return round(float(centavos) / 100, 2)

        reais = _primeiro_preenchido(
            dados, "pix.valor", "valor", "amount", "total", default=0,
        )
        return round(float(reais), 2)

    @staticmethod
    def _extrair_status(raw_payload: dict, dados: dict) -> str:
        """Mapeia o nome do evento do PSP para o status normalizado."""
        evento = _primeiro_preenchido(
            raw_payload, "event", "event_type", "type", default="",
        )
        status = EVENT_STATUS_MAP.get(str(evento).strip().lower())
        if status:
            return status

        # Alguns PSPs não mandam nome de evento, só o status do objeto.
        bruto = str(_primeiro_preenchido(
            dados, "automatic_pix.authorization_status", "status", default="",
        )).strip().lower()
        if bruto in PIX_STATUSES:
            return bruto

        logger.warning("[PIX] Evento não reconhecido (event=%r, status=%r)", evento, bruto)
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
        """
        dados = raw_payload.get("data") or raw_payload
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

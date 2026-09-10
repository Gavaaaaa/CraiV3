"""crai/integrations/email_sender.py — o resumo de insights por e-mail.

ESTADO ATUAL: SIMULADO por default, como o WhatsApp e o HubSpot. Sem as
variáveis de SMTP configuradas, o envio é um log e a resposta diz
`simulado: True` — o endpoint funciona na demo e na suíte sem credencial
nenhuma, que é o padrão do resto do projeto. Com `CRAI_SMTP_HOST` (e o resto)
configurado, manda de verdade via `smtplib` da biblioteca padrão, STARTTLS.

Escolha de provedor (SendGrid, Resend, SES...) fica para depois: todos
oferecem SMTP, então a interface `send_insights_email` não muda quando a
decisão vier — só a env.

O QUE VAI NO E-MAIL: texto puro, sem HTML, com o ranking em linhas. É o
formato que sobrevive a qualquer cliente de e-mail e que não esconde link
nenhum. PII mínima: o id externo do cliente e os números do risco; o e-mail
de cada cliente final NÃO entra no corpo.
"""

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)

ENV_HOST = "CRAI_SMTP_HOST"
ENV_PORT = "CRAI_SMTP_PORT"
ENV_USER = "CRAI_SMTP_USER"
ENV_PASSWORD = "CRAI_SMTP_PASSWORD"
ENV_FROM = "CRAI_EMAIL_FROM"

REMETENTE_PADRAO = "insights@crai.local"
LINHAS_NO_EMAIL = 20


def configurado() -> bool:
    """Lido a cada chamada, como `modo_real()` no pagarme_gateway."""
    return bool((os.getenv(ENV_HOST) or "").strip())


def montar_corpo(empresa: str, clientes_em_risco: list[dict],
                 total_clientes: int | None = None) -> str:
    total = len(clientes_em_risco) if total_clientes is None else total_clientes
    mostrados = clientes_em_risco[:LINHAS_NO_EMAIL]
    linhas = [
        f"CRAI — clientes em risco de churn voluntário — {empresa}",
        "",
        f"Clientes avaliados: {total}. Mostrando os {len(mostrados)} de maior risco.",
        "",
    ]
    if not mostrados:
        linhas.append("Nenhum cliente avaliado ainda. Suba a base em /clientes/importar "
                      "ou integre o SDK.")
    for i, c in enumerate(mostrados, start=1):
        risco = c.get("risk_score")
        risco_txt = "sem dado" if risco is None else f"{risco:.2f}"
        linhas.append(f"{i:>2}. {c.get('customer_id_externo')}  risco {risco_txt}  "
                      f"[{c.get('criticality')}]  origem {c.get('origem', '?')}")
        linhas.append(f"    {c.get('explicacao', '')}")
    linhas += ["", "Gerado automaticamente pela CRAI."]
    return "\n".join(linhas)


def send_insights_email(empresa: str, clientes_em_risco: list[dict],
                        destinatario: str, total_clientes: int | None = None) -> dict:
    """Envia (ou simula) o resumo. Devolve {enviado, simulado, destinatario, motivo}.

    NUNCA levanta: falha de SMTP vira `enviado: False` com o motivo. O
    endpoint que chama já autenticou a empresa e gerou o ranking; um servidor
    de e-mail fora do ar não pode transformar isso num 500.
    """
    corpo = montar_corpo(empresa, clientes_em_risco, total_clientes)
    assunto = f"[CRAI] {len(clientes_em_risco)} cliente(s) em risco — {empresa}"

    if not configurado():
        logger.info("[EMAIL] SIMULADO para %s — %s\n%s", destinatario, assunto, corpo)
        return {"enviado": True, "simulado": True, "destinatario": destinatario,
                "motivo": None, "linhas": len(clientes_em_risco[:LINHAS_NO_EMAIL])}

    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = os.getenv(ENV_FROM) or REMETENTE_PADRAO
    msg["To"] = destinatario
    msg.set_content(corpo)

    host = os.getenv(ENV_HOST).strip()
    porta = int(os.getenv(ENV_PORT) or 587)
    usuario = os.getenv(ENV_USER) or ""
    senha = os.getenv(ENV_PASSWORD) or ""
    try:
        with smtplib.SMTP(host, porta, timeout=15) as smtp:
            smtp.starttls()
            if usuario:
                smtp.login(usuario, senha)
            smtp.send_message(msg)
        logger.info("[EMAIL] enviado para %s via %s:%s", destinatario, host, porta)
        return {"enviado": True, "simulado": False, "destinatario": destinatario,
                "motivo": None, "linhas": len(clientes_em_risco[:LINHAS_NO_EMAIL])}
    except Exception as e:                        # noqa: BLE001 — SMTP é terceiro
        logger.error("[EMAIL] falha ao enviar para %s: %s", destinatario, e)
        return {"enviado": False, "simulado": False, "destinatario": destinatario,
                "motivo": str(e), "linhas": 0}

"""tests/test_prompt_minimo.py — Etapa 2, Bloco 3: o que vai ao LLM é o mínimo.

LGPD: o prompt das 3 sugestões leva o primeiro nome (só se a base tiver), a
causa em português, o valor, o tempo de casa EM FAIXA (só o real, mandado pela
empresa), a abordagem, o canal e o meio de pagamento. Nunca e-mail, telefone,
CPF, chave Pix, o id da recorrência inteiro nem o e2e — e o link de pagamento
também não vai (ele carrega o começo do id). Estes testes montam o prompt de
verdade, pelo caminho do webhook, e REPROVAM se qualquer um aparecer.
"""

import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from crai.agent import workflow as workflow_module
from crai.api import app as app_module
from crai.churn_voluntary import clientes_importados, importacao
from crai.dunning import ciclo_cobranca as cc
from crai.dunning import configuracao, dunning_engine
from crai.dunning import pix_automatico_retry as pix_retry_module
from crai.dunning import retry_scheduler as sched

A = "empresa-a"
SECRET = b"s3cr3t_prompt"
REC = "RN_prompt_7f3a9c21d4"
E2E = "E60701190202609031200abcdef123"
TELEFONE = "+5511988887777"
EMAIL = "mariana.tavares@exemplo.com.br"
NOME = "Mariana Albuquerque Tavares"
CPF = "11122233344"
CHAVE_PIX = "mariana.chave@pix.com.br"
ABERTURA = datetime(2026, 9, 3, 9, 0)


def _assinar(corpo):
    ts = int(time.time())
    mac = hmac.new(SECRET, f"{ts}.".encode() + corpo, hashlib.sha256).hexdigest()
    return {"x-pix-signature": f"t={ts},v1={mac}", "content-type": "application/json",
            "x-tenant-id": A}


def _falha(e2e, id_cobranca=None):
    """O payload do PSP cheio de dado pessoal do pagador (como em
    `TestSemDadoPessoal`): nada disso pode chegar ao prompt."""
    corpo = {"event": "automatic_pix.charge_failed",
             "data": {"id": id_cobranca or f"inv_{e2e}", "valor": 299.90,
                      "automatic_pix": {"recurrence_id": REC, "failure_reason": "AM04"},
                      "pix": {"end_to_end_id": e2e,
                              "payer": {"pix_key": CHAVE_PIX, "name": NOME, "email": EMAIL,
                                        "phone": TELEFONE, "cpf": CPF, "ispb": "60701190"}}}}
    return json.dumps(corpo).encode()


class _Resposta:
    def __init__(self, texto):
        self.content = [type("Bloco", (), {"text": texto})()]


@pytest.fixture
def prompts(monkeypatch):
    """Guarda todo prompt que iria ao LLM e responde com o que o teste mandar."""
    estado = {"prompts": [], "resposta": None}

    async def falso(*a, model=None, max_tokens=None, messages=None, **k):
        estado["prompts"].append(messages[0]["content"])
        if estado["resposta"] is None:
            raise RuntimeError("sem LLM")
        return _Resposta(estado["resposta"])
    monkeypatch.setattr(dunning_engine.claude.messages, "create", falso)
    return estado


@pytest.fixture
def cliente(monkeypatch, prompts):
    monkeypatch.setenv("PIX_WEBHOOK_SECRET", SECRET.decode())

    class Congelado(datetime):
        @classmethod
        def now(cls, tz=None):
            return ABERTURA if tz is None else ABERTURA.replace(tzinfo=tz)
    monkeypatch.setattr(workflow_module, "datetime", Congelado)
    monkeypatch.setattr(pix_retry_module, "datetime", Congelado)
    monkeypatch.setattr(workflow_module._pix_retry, "confianca_minima", 2.0)
    with TestClient(app_module.app) as c:
        yield c


def _base(**extra):
    linha = {"customer_id_externo": "cli_prompt", "mrr": "299,90", "billing_profile": "CLT",
             "id_recorrencia": REC, **extra}
    validado, motivo = importacao.validar_linha(linha)
    assert motivo is None, motivo
    clientes_importados.gravar(A, [validado])


def _ate_a_mensagem(c):
    """Falha original e as 3 tentativas falhando: a mensagem é gerada."""
    c.post("/webhooks/pix-automatico", content=_falha(E2E), headers=_assinar(_falha(E2E)))
    ciclo = cc.ciclos_do_mandato(A, REC)[0]
    for numero in (1, 2, 3):
        plano = cc.tentativas_do_ciclo(ciclo["id"])
        quando = datetime.fromisoformat(plano[numero - 1]["agendada_para"]) + timedelta(hours=1)
        disparos = asyncio.run(sched.processar_tentativas_devidas(quando))
        corpo = _falha(f"{E2E}_t{numero}", id_cobranca=disparos[0]["id_cobranca"])
        c.post("/webhooks/pix-automatico", content=corpo, headers=_assinar(corpo))
    return cc.ciclo_por_id(ciclo["id"])


PROIBIDOS = (TELEFONE, "988887777", EMAIL, "exemplo.com.br", CPF, CHAVE_PIX, "Albuquerque",
             "Tavares", NOME, REC, E2E, "pay.crai.ai", REC[:8])


def _sem_proibido(prompt):
    for proibido in PROIBIDOS:
        assert proibido not in prompt, f"{proibido!r} foi ao LLM"


class TestPromptMinimo:

    def test_o_prompt_montado_de_verdade_nao_leva_contato_nem_identificador(self, cliente, prompts):
        _base(telefone=TELEFONE, email=EMAIL, nome=NOME, tenure_days="400")
        _ate_a_mensagem(cliente)
        assert len(prompts["prompts"]) == 1, "uma chamada ao LLM para as 3 sugestões"
        prompt = prompts["prompts"][0]
        _sem_proibido(prompt)
        assert "Mariana" in prompt, "o primeiro nome da base deveria ir"
        assert "de 1 a 3 anos" in prompt and "400" not in prompt, "tempo de casa só em faixa"
        assert "Saldo insuficiente" in prompt and "299.90" in prompt
        assert dunning_engine.MARCADOR_LINK in prompt

    def test_sem_tenure_real_o_tempo_de_casa_nao_vai(self, cliente, prompts):
        """O `tenure_months` do perfil é SINTÉTICO: nunca entra no prompt."""
        _base(email=EMAIL, nome=NOME)
        _ate_a_mensagem(cliente)
        prompt = prompts["prompts"][0]
        assert "Tempo como cliente" not in prompt and "meses" not in prompt
        _sem_proibido(prompt)

    def test_sem_base_nem_o_primeiro_nome_vai(self, cliente, prompts):
        _ate_a_mensagem(cliente)
        prompt = prompts["prompts"][0]
        assert "Primeiro nome" not in prompt
        _sem_proibido(prompt)

    @pytest.mark.parametrize("causa", ["insufficient_funds", "limit_exceeded",
                                       "authorization_revoked", "processing_error"])
    def test_a_funcao_do_prompt_nem_recebe_identificador(self, causa):
        prompt = dunning_engine.montar_prompt("whatsapp", causa, 99.9, "boleto", "direto",
                                              "Ana", "menos de 3 meses")
        assert "Ana" in prompt and "menos de 3 meses" in prompt
        assert "@" not in prompt and "http" not in prompt


class TestRespostaDoLLM:

    def _resposta(self, **textos):
        return json.dumps({"lembrete_cordial": "Oi! Lembrete da mensalidade: {link}",
                           "facilitacao": "Oi! Resolva rápido por aqui: {link}",
                           "urgencia_respeitosa": "Oi! Ainda está em aberto: {link}", **textos})

    def test_texto_do_llm_ganha_o_link_e_o_aviso(self, cliente, prompts):
        prompts["resposta"] = self._resposta()
        _base(email=EMAIL)
        ciclo = _ate_a_mensagem(cliente)
        for m in cc.mensagens_do_ciclo(ciclo["id"]):
            assert m["origem_texto"] == "llm"
            assert "{link}" not in m["texto"] and "https://pay.crai.ai/" in m["texto"]
            assert m["texto"].endswith(dunning_engine.AVISO_AUTOMATICO)

    @pytest.mark.parametrize("ruim", [
        "Fale com nossa equipe de suporte: {link}",    # encaminhamento humano
        "Pague aqui, sem link nenhum",                  # sem o marcador
        "Dois links: {link} e {link}",                  # marcador repetido
        "x" * 600 + "{link}",                           # longo demais
    ])
    def test_texto_do_llm_recusado_vira_template_so_naquela_abordagem(self, cliente, prompts, ruim):
        prompts["resposta"] = self._resposta(facilitacao=ruim)
        _base(email=EMAIL)
        ciclo = _ate_a_mensagem(cliente)
        por_abordagem = {m["abordagem"]: m for m in cc.mensagens_do_ciclo(ciclo["id"])}
        assert por_abordagem["facilitacao"]["origem_texto"] == "template"
        assert por_abordagem["facilitacao"]["codigo_template"] == "facilitacao.insufficient_funds"
        assert por_abordagem["lembrete_cordial"]["origem_texto"] == "llm"
        assert all(m["texto"].endswith(dunning_engine.AVISO_AUTOMATICO)
                   for m in por_abordagem.values())

    def test_json_torto_vira_template_nas_tres(self, cliente, prompts):
        prompts["resposta"] = "não é json"
        _base(email=EMAIL)
        ciclo = _ate_a_mensagem(cliente)
        assert {m["origem_texto"] for m in cc.mensagens_do_ciclo(ciclo["id"])} == {"template"}


class TestRecomendada:

    @pytest.mark.parametrize("causa, p, esperada", [
        ("authorization_revoked", 0.9, "facilitacao"), ("limit_exceeded", 0.1, "facilitacao"),
        ("processing_error", 0.1, "lembrete_cordial"), ("insufficient_funds", 0.7, "lembrete_cordial"),
        ("insufficient_funds", 0.4, "facilitacao"), ("insufficient_funds", 0.39, "urgencia_respeitosa"),
        ("insufficient_funds", None, "facilitacao"),
    ])
    def test_regra_da_recomendada(self, causa, p, esperada):
        assert dunning_engine.abordagem_recomendada(causa, p) == esperada

    def test_todo_template_termina_com_o_aviso_e_nao_encaminha_para_humano(self):
        from crai.churn_voluntary.voluntary_agent import encaminha_para_humano
        for abordagem, por_causa in dunning_engine.TEMPLATES_POR_ABORDAGEM.items():
            for causa, modelo in por_causa.items():
                texto = dunning_engine.com_aviso(modelo.format(
                    saudacao="Olá!", valor="10.00", metodo="boleto", link="https://x"))
                assert texto.endswith(dunning_engine.AVISO_AUTOMATICO), (abordagem, causa)
                assert encaminha_para_humano(texto) is None, (abordagem, causa)
        assert set(dunning_engine.TEMPLATES_POR_ABORDAGEM) == set(cc.ABORDAGENS)

    def test_a_mensagem_do_caminho_antigo_tambem_termina_com_o_aviso(self):
        async def recusa(*a, **k):
            raise RuntimeError("sem LLM")
        original = dunning_engine.claude.messages.create
        dunning_engine.claude.messages.create = recusa
        try:
            r = asyncio.run(dunning_engine.DunningEngine().run_campaign(
                "cus_x", "insufficient_funds", 0.5, 10.0))
        finally:
            dunning_engine.claude.messages.create = original
        assert r["message"].endswith(dunning_engine.AVISO_AUTOMATICO)

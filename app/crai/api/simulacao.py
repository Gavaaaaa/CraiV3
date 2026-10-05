"""crai/api/simulacao.py — a página "Simulação do gateway" do dashboard (Rodada 3, Fase 3).

    GET    /simulacao           o estado da simulação da empresa (ou vazio)
    POST   /simulacao/cliente   cria o cliente fictício, com a verdade escondida
    POST   /simulacao/cobrar    a cobrança do dia vai ao PSP simulado
    POST   /simulacao/avancar   avança o relógio simulado da empresa
    POST   /simulacao/retencao  um cliente fictício em risco (voluntário)
    DELETE /simulacao           apaga tudo o que é fictício da empresa

O QUE É DE VERDADE E O QUE É FICTÍCIO. O cliente, o banco dele e o relógio são
fictícios (`crai/simulador.py`). O resto é o sistema de sempre: a falha entra
pelo mesmo `_run_involuntary_pipeline` do webhook do Pix, os modelos
diagnosticam, a regra do BACEN agenda as tentativas, o agendador as dispara
quando o relógio chega, as 3 mensagens são geradas, a empresa escolhe pela
mesma rota de sempre (`POST /ciclos/{id}/mensagens/escolher`) e a confirmação
de pagamento fecha o ciclo com a fee.

O QUE ESTAS ROTAS GARANTEM:

  ISOLAMENTO   a simulação de uma empresa mora nos arquivos de simulação DELA
               (`crai/ambiente.py`). Nada do que é simulado entra no ciclo
               real, no dataset de treino, na trilha do Art. 20 real, no bandit
               real nem nas métricas reais; e o relógio de verdade nunca abre
               esses arquivos.
  TENANT       não há id de empresa em rota nenhuma: o tenant é o do token. O
               ciclo simulado de outra empresa responde o mesmo 404 de um ciclo
               que não existe.
  SEM REDE     dentro da simulação o PSP, o CRM e o envio de mensagem ficam
               sempre no modo simulado, mesmo com credencial configurada. Por
               isso a simulação funciona em qualquer ambiente, e não só em
               `development` ou `demo`.
  PAPEL        só `owner` e `admin` criam, cobram, avançam e limpam. Membro lê.
  LGPD         o formulário recusa o que pareça dado real (CPF, e-mail,
               telefone, chave Pix), e não tem campo para nenhum deles.
  A VERDADE    a verdade escondida volta só para quem a criou (o verso do
               cartão da tela). Nenhum nó do pipeline a lê.

O RELÓGIO. `POST /simulacao/avancar` aceita `{"dias": N}` (1 a 60) ou
`{"ate_proxima_acao": true}`. Avançar N dias para na primeira ação que
acontecer (uma tentativa, uma mensagem, a resposta do cliente, um desfecho).

Este módulo NÃO importa de `app.py` no topo (é o `app.py` que o monta): as duas
funções do pipeline que ele usa são importadas na hora da chamada.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import ambiente, simulador
from ..accounts import get_conta
from ..accounts.auth import exigir_papel
from ..agent.pix_codes import CAUSA_LEGIVEL, CAUSA_REVOGADA, causa_do_codigo
from ..churn_voluntary import batch_scoring, mantido, retention_log, voluntary_agent
from ..churn_voluntary.risk_scorer import EVENTO_DADO_ESTATICO
from ..dunning import ciclo_cobranca as cc
from ..dunning import configuracao, retry_scheduler
from ..integrations.payment_gateway import STATUS_COBRANCA_FALHADA
from . import ciclos as ciclos_api
from . import datas
from . import voluntario as vol

logger = logging.getLogger(__name__)

router = APIRouter(tags=["simulacao"])

DIAS_MAXIMO = 60
# Quantas vezes "avançar até a próxima ação" procura a ação seguinte quando a
# primeira data não mudou nada (a mensagem esperando a janela de contato, por exemplo).
PASSOS_ATE_A_ACAO = 6
# O cliente em risco chega como uma foto de comportamento, não como um evento
# de cancelamento: o risco sai dos sinais, pela régua ou pelo modelo.
EVENTO_DO_CLIENTE_EM_RISCO = EVENTO_DADO_ESTATICO
MOTIVO_DESCARTE = {"eprofit_nao_positivo": "o retorno esperado ficou abaixo do custo da ação",
                   "score_abaixo_do_corte": "a chance de recuperar é baixa demais para agir",
                   "janela_encerrada": "a janela de novas tentativas acabou"}
ROTULO_DO_CANAL = {"whatsapp": "WhatsApp", "email": "e-mail", "sem_canal": "nenhum canal"}
ROTULO_DA_ABORDAGEM = {"lembrete_cordial": "lembrete cordial", "facilitacao": "facilitação",
                       "urgencia_respeitosa": "urgência com respeito"}


def _erro(e: simulador.SimulacaoInvalida) -> HTTPException:
    return HTTPException(status_code=422, detail={"motivo": e.motivo, "campo": e.campo,
                                                  "detalhe": e.detalhe})


def _409(motivo: str, detalhe: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"motivo": motivo, "detalhe": detalhe})


def _dia(valor) -> str:
    return cc._data(valor).strftime("%d/%m")


def _lista(itens: list) -> str:
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]


# ── O relógio da empresa ──────────────────────────────────────────────────

def _relogio(tenant_id: str) -> Optional[dict]:
    """O relógio simulado da empresa, ou None se ela nunca simulou."""
    if not simulador.existe(tenant_id):
        return None
    with ambiente.em_simulacao(tenant_id):
        return simulador.relogio(tenant_id)


def _agora_da_simulacao(tenant_id: str) -> datetime:
    """O instante do relógio simulado; se ainda não existe, o de agora."""
    rel = _relogio(tenant_id)
    return rel["agora"] if rel else datas.agora_local().replace(microsecond=0)


# ── O estado que a página mostra ──────────────────────────────────────────

def _proxima_acao(pagador: dict, ciclo: Optional[dict], tentativas: list, config: dict,
                  agora: datetime) -> Optional[dict]:
    """O que o sistema (ou o cliente fictício) vai fazer em seguida, e quando."""
    if ciclo is None:
        return None
    estado = ciclo["estado"]
    if estado == cc.RECOBRANDO:
        pendentes = [t for t in tentativas if t["resultado"] == cc.PENDENTE
                     and not t["disparada_em"]]
        if pendentes:
            t = min(pendentes, key=lambda x: x["agendada_para"])
            return {"quando": cc._data(t["agendada_para"]), "tipo": "tentativa",
                    "descricao": f"Tentativa {t['numero']}"}
        return {"quando": agora + timedelta(minutes=1), "tipo": "mensagem",
                "descricao": "Mensagem depois das tentativas"}
    if estado == cc.AGUARDANDO_ESCOLHA:
        if cc.mensagem_escolhida(ciclo["id"]) is not None:
            return {"quando": configuracao.proximo_inicio_da_janela(config, agora),
                    "tipo": "mensagem", "descricao": "Envio da mensagem escolhida"}
        if config["modo_mensagem_involuntario"] == configuracao.MODO_AUTOMATICO:
            return {"quando": configuracao.proximo_inicio_da_janela(config, agora),
                    "tipo": "mensagem", "descricao": "Envio da mensagem recomendada"}
        inicio = cc._data(ciclo.get("aguardando_escolha_em")) or agora
        limite = inicio + timedelta(hours=config["prazo_escolha_horas"])
        return {"quando": configuracao.proximo_inicio_da_janela(config, max(limite, agora)),
                "tipo": "mensagem", "descricao": "Envio automático da recomendada"}
    if estado == cc.MENSAGEM_ENVIADA:
        enviada = cc._data(ciclo.get("mensagem_confirmada_em") or ciclo.get("mensagem_em")) or agora
        if pagador.get("resposta_a_mensagem") is None:
            return {"quando": enviada + timedelta(days=simulador.DIAS_PARA_RESPONDER),
                    "tipo": "resposta", "descricao": "Prazo para resposta do cliente"}
        return {"quando": enviada + timedelta(days=cc.PRAZO_RECUPERACAO_DIAS, minutes=1),
                "tipo": "prazo", "descricao": "Fim do prazo de recuperação"}
    return None


def _desfecho(pagador: dict, ciclo: Optional[dict], tentativas: list) -> Optional[dict]:
    if pagador.get("cobranca_inicial") == simulador.PAGA:
        return {"tipo": "recuperado", "via": "tentativa", "tentativa": 0,
                "valor_liquido": round(float(pagador["mensalidade"]), 2),
                "em": datas.iso_com_fuso(pagador["cobrado_em"])}
    if ciclo is None:
        return None
    com_mensagem = bool(ciclo.get("mensagem_confirmada_em"))
    if ciclo["estado"] == cc.RECUPERADO:
        paga = next((t["numero"] for t in tentativas if t["resultado"] == cc.PAGA), None)
        return {"tipo": "recuperado", "via": "tentativa" if paga else "mensagem",
                "tentativa": paga,
                "valor_liquido": round(float(ciclo["valor"] or 0) - float(ciclo.get("fee") or 0), 2),
                "em": datas.iso_com_fuso(ciclo.get("recuperado_em"))}
    if ciclo["estado"] in (cc.PERDIDO, cc.DESCARTADO):
        return {"tipo": "encerrado", "via": "mensagem" if com_mensagem else "tentativa",
                "tentativa": None, "valor_liquido": 0.0,
                "motivo": ciclo.get("motivo_descarte") or ciclo.get("motivo_perdido"),
                "em": datas.iso_com_fuso(ciclo.get("perdido_em") or ciclo.get("descartado_em"))}
    return None


def _pensando(pagador: dict, ciclo: Optional[dict], tentativas: list, mensagens: list,
              config: dict) -> list:
    """"O que o sistema está pensando", em frases: só o que o sistema enxerga e
    decidiu, lido do ciclo. A verdade escondida não passa por aqui."""
    if pagador.get("cobranca_inicial") == simulador.PAGA:
        return ["A cobrança passou no dia do vencimento. Não há ciclo para abrir, e a CRAI "
                "não cobra nada por isso."]
    if ciclo is None:
        return []
    diag = pagador.get("diagnostico") or {}
    causa = CAUSA_LEGIVEL.get(ciclo["causa_original"], ciclo["causa_original"])
    linhas = [f"Causa da falha: {causa[:1].lower() + causa[1:]}."]
    if ciclo.get("p_recovery") is not None:
        frase = f"Chance de recuperar: {round(float(ciclo['p_recovery']) * 100)}%."
        if diag.get("anomala"):
            frase += " Já com o desconto de 30% por comportamento fora do padrão."
        linhas.append(frase)
    if diag.get("dia_provavel_saldo"):
        frase = f"Dia provável de saldo: {_dia(diag['dia_provavel_saldo'])}."
        if diag.get("perfil_inferido"):
            # O sistema não recebe o perfil do formulário: estima um, sozinho.
            frase += f" Perfil de recebimento estimado pelo sistema: {diag['perfil_inferido']}."
        linhas.append(frase)
    if ciclo["estado"] == cc.DESCARTADO:
        motivo = MOTIVO_DESCARTE.get(ciclo.get("motivo_descarte"), "não vale a ação")
        return linhas + [f"O sistema decidiu não agir: {motivo}. O ciclo fica registrado como "
                         "encerrado sem recuperação."]
    if tentativas:
        dias = [_dia(t["agendada_para"]) for t in tentativas]
        linhas.append(f"Plano: {len(tentativas)} "
                      f"{'tentativa' if len(tentativas) == 1 else 'tentativas'} dentro de 7 dias, "
                      f"{'no dia' if len(dias) == 1 else 'nos dias'} {_lista(dias)}. Nenhuma "
                      "mensagem antes de todas falharem.")
    elif mensagens or ciclo["estado"] != cc.RECOBRANDO:
        linhas.append("Esta causa não se resolve cobrando de novo: o sistema vai direto para a "
                      "mensagem.")
    for t in tentativas:
        if t["resultado"] == cc.FALHOU:
            causa_t = causa_do_codigo(t.get("codigo_resultado")) if t.get("codigo_resultado") else None
            if causa_t == CAUSA_REVOGADA:
                linhas.append(f"Tentativa {t['numero']}: autorização revogada. Cobrar de novo não "
                              "adianta: as outras tentativas são canceladas e o sistema vai para "
                              "a mensagem.")
            else:
                legivel = CAUSA_LEGIVEL.get(causa_t, "sem motivo informado")
                linhas.append(f"Tentativa {t['numero']} não passou "
                              f"({legivel[:1].lower() + legivel[1:]}).")
        elif t["resultado"] == cc.PAGA:
            linhas.append(f"Tentativa {t['numero']} paga.")
    if mensagens:
        if config["modo_mensagem_involuntario"] == configuracao.MODO_ESCOLHA:
            linhas.append("3 mensagens escritas para este cliente. A empresa escolhe uma; sem "
                          f"escolha em {config['prazo_escolha_horas']} h, a recomendada sai "
                          "sozinha.")
        else:
            linhas.append("3 mensagens escritas para este cliente. No modo automático, a "
                          "recomendada sai sem esperar escolha.")
    enviada = next((m for m in mensagens if m["enviada_em"]), None)
    if enviada is not None:
        por = {"prazo": "pelo prazo", "automatico": "pelo modo automático"}.get(
            enviada["escolhida_por"], "pela empresa")
        linhas.append(f"Mensagem enviada por {ROTULO_DO_CANAL.get(enviada['canal'], enviada['canal'])}"
                      f" ({ROTULO_DA_ABORDAGEM.get(enviada['abordagem'], enviada['abordagem'])}, "
                      f"escolhida {por}). O sistema espera o pagamento pelo meio oferecido.")
    if ciclo["estado"] == cc.RECUPERADO:
        paga = next((t["numero"] for t in tentativas if t["resultado"] == cc.PAGA), None)
        como = f"na {paga}ª tentativa" if paga else "depois da mensagem"
        linhas.append(f"Pagamento recuperado {como}. O ciclo fecha e o valor entra no extrato.")
    elif ciclo["estado"] == cc.PERDIDO:
        linhas.append("O prazo de recuperação acabou sem pagamento. O ciclo encerra sem "
                      "recuperação, e nada é cobrado da empresa.")
    return linhas


def _tentativa_publica(t: dict) -> dict:
    causa = None
    if t["resultado"] == cc.FALHOU and t.get("codigo_resultado"):
        causa = causa_do_codigo(t["codigo_resultado"])
    return {"numero": t["numero"], "agendada_para": datas.iso_com_fuso(t["agendada_para"]),
            "disparada_em": datas.iso_com_fuso(t.get("disparada_em")),
            "resultado": t["resultado"], "resultado_em": datas.iso_com_fuso(t.get("resultado_em")),
            "causa": causa, "causa_legivel": CAUSA_LEGIVEL.get(causa) if causa else None,
            "motivo_cancelamento": t.get("motivo_cancelamento")}


def _vazio(tenant_id: str) -> dict:
    with ambiente.fora_da_simulacao():
        config = configuracao.ler(tenant_id)
    return {"existe": False, "relogio": None, "cliente": None, "id_recorrencia": None,
            "cobranca": None, "ciclo": None, "tentativas": [], "proxima_acao": None,
            "desfecho": None, "pensando": [], "sem_crai": None,
            "modo_mensagem": config["modo_mensagem_involuntario"],
            "prazo_escolha_horas": config["prazo_escolha_horas"]}


def estado_da_simulacao(tenant_id: str) -> dict:
    """O estado da simulação da empresa: o cliente fictício mais recente, a
    cobrança dele, o ciclo (na mesma forma de `GET /ciclos/{id}`), o relógio, o
    que o sistema está pensando e a comparação com o que teria acontecido sem
    a CRAI. Sem simulação, `existe: false` e o resto vazio."""
    rel = _relogio(tenant_id)
    if rel is None:
        return _vazio(tenant_id)
    agora = rel["agora"]
    with ambiente.em_simulacao(tenant_id, agora):
        pagador = simulador.pagador_atual(tenant_id)
        config = configuracao.ler(tenant_id)
        base = {**_vazio(tenant_id), "existe": True,
                "relogio": {"agora": datas.iso_com_fuso(agora),
                            "iniciado_em": datas.iso_com_fuso(rel["iniciado_em"])}}
        if pagador is None:
            return base
        ciclo = cc.ciclo_do_tenant(tenant_id, pagador["ciclo_id"]) if pagador["ciclo_id"] else None
        tentativas = cc.tentativas_do_ciclo(ciclo["id"]) if ciclo else []
        mensagens = cc.mensagens_do_ciclo(ciclo["id"]) if ciclo else []
        cobrado = pagador.get("cobrado_em") is not None
        causa_inicial = (causa_do_codigo(pagador["codigo_inicial"])
                         if pagador.get("codigo_inicial") else None)
        proxima = _proxima_acao(pagador, ciclo, tentativas, config, agora)
        detalhe = None
        if ciclo is not None:
            detalhe = ciclos_api.detalhe_do_ciclo(
                tenant_id, ciclo, agora, {pagador["id_recorrencia"]: pagador["nome"]},
                simulado=True)
        return {
            **base,
            # O cliente fictício volta inteiro para quem o criou, com a verdade
            # escondida: é o verso do cartão da tela ("o sistema não vê este lado").
            "cliente": {"nome": pagador["nome"], "mensalidade": pagador["mensalidade"],
                        "perfil": pagador["perfil"], "verdade": simulador.verdade_de(pagador["id"])},
            "id_recorrencia": pagador["id_recorrencia"],
            "cobranca": {"feita": cobrado, "em": datas.iso_com_fuso(pagador.get("cobrado_em")),
                         "resultado": pagador.get("cobranca_inicial"), "causa": causa_inicial,
                         "causa_legivel": CAUSA_LEGIVEL.get(causa_inicial) if causa_inicial else None},
            "ciclo": detalhe,
            "tentativas": [_tentativa_publica(t) for t in tentativas],
            "proxima_acao": ({"quando": datas.iso_com_fuso(proxima["quando"]),
                              "descricao": proxima["descricao"], "tipo": proxima["tipo"]}
                             if proxima else None),
            "resposta_a_mensagem": pagador.get("resposta_a_mensagem"),
            "desfecho": _desfecho(pagador, ciclo, tentativas),
            "pensando": _pensando(pagador, ciclo, tentativas, mensagens, config),
            "sem_crai": simulador.sem_crai(pagador) if cobrado else None,
            "chance_recuperar": ciclo.get("p_recovery") if ciclo else None,
            "dia_provavel_saldo": datas.iso_com_fuso((pagador.get("diagnostico") or {})
                                                     .get("dia_provavel_saldo")),
        }


# ── O que acontece quando o relógio anda ──────────────────────────────────

async def _falha_no_pipeline(tenant_id: str, pagador: dict, codigo: str, e2e: str,
                             id_cobranca: Optional[str]) -> dict:
    """A cobrança falhada do PSP simulado entra pelo MESMO pipeline do webhook
    do Pix. O evento leva só o que o PSP de verdade mandaria: nada da verdade
    escondida, a não ser o código de falha que ela produziu."""
    from . import app as app_module                       # import tardio (ver docstring)
    evento = {"e2e_id": e2e, "valor": float(pagador["mensalidade"]),
              "status": STATUS_COBRANCA_FALHADA, "id_recorrencia": pagador["id_recorrencia"],
              "codigo_falha": codigo, "id_cobranca": id_cobranca, "degradacoes": []}
    return await app_module._run_involuntary_pipeline(
        event=evento, payment_method="pix_automatico", customer_id=pagador["id_recorrencia"],
        amount=float(pagador["mensalidade"]), invoice_id=e2e, tenant_id=tenant_id)


async def _pagamento(tenant_id: str, pagador: dict, e2e: str, id_cobranca: Optional[str]) -> dict:
    """A confirmação de pagamento do PSP simulado: fecha o ciclo como recuperado
    pela mesma função do webhook de verdade."""
    from . import app as app_module                       # import tardio
    return await app_module._fechar_ciclo_recuperado(
        customer_id=pagador["id_recorrencia"], e2e_id=e2e, valor=float(pagador["mensalidade"]),
        tenant_id=tenant_id, id_cobranca=id_cobranca)


async def cobrar(tenant_id: str, pagador: dict, agora: datetime) -> None:
    """A cobrança do dia do vencimento vai ao PSP simulado. Se passa, não há o
    que recuperar. Se falha, o sistema recebe a falha como receberia do PSP."""
    with ambiente.em_simulacao(tenant_id, agora):
        simulador.acertar_relogio(tenant_id, agora)
        pagou, codigo = simulador.psp_responde(pagador, 0, 0)
        marca = agora.isoformat(timespec="seconds")
        if pagou:
            simulador.atualizar_pagador(pagador["id"], cobrado_em=marca,
                                        cobranca_inicial=simulador.PAGA)
            return
        rec = pagador["id_recorrencia"]
        id_cobranca = f"cob_{rec}"
        simulador.atualizar_pagador(pagador["id"], cobrado_em=marca,
                                    cobranca_inicial=simulador.FALHOU, codigo_inicial=codigo)
        final = await _falha_no_pipeline(tenant_id, pagador, codigo, f"E_{rec}_0", id_cobranca) or {}
        ciclo = cc.ciclo_da_cobranca(tenant_id, id_cobranca)
        simulador.atualizar_pagador(
            pagador["id"], ciclo_id=ciclo["id"] if ciclo else None,
            diagnostico={"dia_provavel_saldo": final.get("optimal_retry_at"),
                         "perfil_inferido": final.get("profile_type"),
                         "anomala": bool(final.get("is_anomalous")),
                         "estrategia": final.get("estrategia")})


async def processar(tenant_id: str, agora: datetime) -> None:
    """UMA passagem do relógio simulado em `agora`: o mesmo agendador do relógio
    de verdade dispara o que está devido; o PSP simulado responde a cada
    tentativa; e o cliente fictício responde à mensagem quando o prazo chega."""
    with ambiente.em_simulacao(tenant_id, agora):
        simulador.acertar_relogio(tenant_id, agora)
        disparos = await retry_scheduler.processar_tentativas_devidas(agora)
        por_recorrencia = {p["id_recorrencia"]: p for p in simulador.pagadores(tenant_id)}
        for d in disparos:
            pagador = por_recorrencia.get(d["customer_id"])
            if pagador is None:
                continue
            rec, numero = pagador["id_recorrencia"], d["numero"]
            pagou, codigo = simulador.psp_responde(
                pagador, simulador.dia_relativo(pagador, agora), numero)
            if pagou:
                await _pagamento(tenant_id, pagador, f"E_{rec}_t{numero}_pago", d.get("id_cobranca"))
            else:
                await _falha_no_pipeline(tenant_id, pagador, codigo, f"E_{rec}_t{numero}",
                                         d.get("id_cobranca"))
        for pagador in por_recorrencia.values():
            if not pagador.get("ciclo_id") or pagador.get("resposta_a_mensagem") is not None:
                continue
            ciclo = cc.ciclo_por_id(pagador["ciclo_id"])
            enviada = cc._data(ciclo.get("mensagem_confirmada_em")) if ciclo else None
            if (ciclo is None or ciclo["estado"] != cc.MENSAGEM_ENVIADA or enviada is None
                    or agora < enviada + timedelta(days=simulador.DIAS_PARA_RESPONDER)):
                continue
            pagou = simulador.responde_a_mensagem(pagador, simulador.dia_relativo(pagador, agora))
            if pagou:
                await _pagamento(tenant_id, pagador, f"E_{pagador['id_recorrencia']}_msg_pago", None)
            simulador.atualizar_pagador(
                pagador["id"], resposta_em=agora.isoformat(timespec="seconds"),
                resposta_a_mensagem=simulador.RESPOSTA_PAGOU if pagou else simulador.RESPOSTA_NAO_PAGOU)


def _assinatura(tenant_id: str, agora: datetime) -> tuple:
    """Uma foto do que já aconteceu na simulação: se ela muda, houve uma ação."""
    with ambiente.em_simulacao(tenant_id, agora):
        fotos = []
        for p in simulador.pagadores(tenant_id):
            if not p.get("ciclo_id"):
                fotos.append((p["id"], p.get("cobranca_inicial")))
                continue
            ciclo = cc.ciclo_por_id(p["ciclo_id"])
            tentativas = cc.tentativas_do_ciclo(p["ciclo_id"])
            mensagens = cc.mensagens_do_ciclo(p["ciclo_id"])
            fotos.append((p["id"], ciclo["estado"], p.get("resposta_a_mensagem"),
                          tuple((t["numero"], bool(t["disparada_em"]), t["resultado"])
                                for t in tentativas),
                          len(mensagens), sum(1 for m in mensagens if m["escolhida"]),
                          sum(1 for m in mensagens if m["enviada_em"])))
        return tuple(fotos)


def _proxima_da_empresa(tenant_id: str, agora: datetime) -> Optional[datetime]:
    """Quando acontece a próxima ação do cliente fictício atual."""
    with ambiente.em_simulacao(tenant_id, agora):
        pagador = simulador.pagador_atual(tenant_id)
        if pagador is None or not pagador.get("ciclo_id"):
            return None
        ciclo = cc.ciclo_por_id(pagador["ciclo_id"])
        proxima = _proxima_acao(pagador, ciclo, cc.tentativas_do_ciclo(ciclo["id"]),
                                configuracao.ler(tenant_id), agora)
        return proxima["quando"] if proxima else None


async def avancar(tenant_id: str, dias: Optional[int], ate_proxima_acao: bool) -> None:
    agora = _agora_da_simulacao(tenant_id)
    if ate_proxima_acao:
        for _ in range(PASSOS_ATE_A_ACAO):
            quando = _proxima_da_empresa(tenant_id, agora)
            if quando is None:
                return
            antes = _assinatura(tenant_id, agora)
            agora = max(quando, agora) + timedelta(seconds=1)
            await processar(tenant_id, agora)
            if _assinatura(tenant_id, agora) != antes:
                return
        return
    for _ in range(int(dias)):
        antes = _assinatura(tenant_id, agora)
        agora = agora + timedelta(days=1)
        await processar(tenant_id, agora)
        if _assinatura(tenant_id, agora) != antes:
            return


def _motivo(cliente: dict, props: dict, user_id: str, risco, criticidade: str,
            posicao_do_modelo: dict | None = None) -> str:
    """O motivo em uma frase: os dados que o sistema recebeu, ditos. Sem dado
    de uso, a frase é a do evento (e não um "sem login há N dias" inventado).

    `posicao_do_modelo` (a `posicao` da saída da decisão de risco) só vem
    quando o MODELO v3 decidiu: aí a frase é a da posição na base, a mesma do
    ranking. A frase da régua diria "crítico pelo valor da conta, não pelo
    risco" de quem é grave pela posição, o que não é verdade."""
    partes = []
    if cliente["sinais"]["abriu_cancelamento"]:
        partes.append("abriu a página de cancelamento")
    if "days_since_last" in props:
        linha = {"customer_id_externo": user_id, "mrr": cliente["mrr"],
                 "days_since_last": props["days_since_last"],
                 "features_used_30d": props["features_used_30d"]}
        if posicao_do_modelo is not None:
            posicao = posicao_do_modelo.get("na_base")
            faixas = batch_scoring.faixas_de_posicao()
            promovido = (criticidade == "critico" and posicao is not None
                         and posicao < 1 - faixas[0] / 100)
            partes.append(batch_scoring._explicar_pelo_modelo(
                linha, posicao, criticidade, faixas, promovido=promovido))
        else:
            partes.append(batch_scoring.explicar(linha, risco, criticidade, None))
    else:
        partes.append("sem dado de uso")
    if "tickets_30d" in props:
        partes.append(f"{props['tickets_30d']} chamados de suporte nos últimos 30 dias")
    if "failed_pay_90d" in props:
        partes.append(f"{props['failed_pay_90d']} pagamentos que falharam nos últimos 90 dias")
    frase = ", ".join(partes)
    return frase[:1].upper() + frase[1:]


# Quando o MODELO v3 decide o risco, a oferta não depende do corte fixo: a
# resposta diz qual regra decidiu (conserto de 05/10/2026). No caminho da régua
# estas frases não são usadas, e a resposta é a de antes.
_PORQUE_DA_REGRA = {
    voluntary_agent.REGRA_INTENCAO_EXPLICITA:
        "O cliente mostrou intenção explícita de sair. Nesse caso o sistema age sempre, por "
        "regra, qualquer que seja o risco calculado. ",
    voluntary_agent.REGRA_POSICAO_NA_BASE:
        "Pela posição na base, o cliente está entre os de maior risco e tem sinal de abandono. ",
}
_SEM_OFERTA_PELA_REGRA = {
    voluntary_agent.SEM_INTERVENCAO_FORA_DAS_FAIXAS:
        "Quem decidiu o risco foi o modelo de IA. Pela posição na base, este cliente não está "
        "entre os graves nem os preocupantes (ou não tem sinal de abandono: 7 dias sem entrar, "
        "ou nenhuma funcionalidade usada), e não houve evento de intenção explícita. O sistema "
        "não interveio: oferecer desconto a quem não ia sair só custa margem.",
    voluntary_agent.SEM_INTERVENCAO_SEM_REFERENCIA:
        "Quem decidiu o risco foi o modelo de IA, e não há referência de posição na base para "
        "comparar este cliente. Sem evento de intenção explícita, o sistema não interveio.",
}


def _porque_da_oferta(regra, intensidade, estimada) -> str:
    """Por que esta oferta, em uma ou duas frases. Sem `regra` (a régua decidiu
    o risco), a frase de sempre."""
    chance = (f" (chance de aceite aprendida até aqui: {round(estimada * 100)}%)."
              if estimada is not None else ".")
    if intensidade == voluntary_agent.INTENSIDADE_MAIS_LEVE:
        return (_PORQUE_DA_REGRA.get(regra, "")
                + "Como o caso é preocupante, e não grave, o sistema escolheu a oferta de "
                  "retenção de menor custo" + chance)
    return (_PORQUE_DA_REGRA.get(regra, "")
            + "O sistema sorteia a partir do que já aprendeu sobre cada oferta para este "
              "perfil. Nesta rodada, esta teve o maior retorno esperado" + chance)


# ── As rotas ──────────────────────────────────────────────────────────────

@router.get("/simulacao")
async def ler_simulacao(conta: dict = Depends(get_conta)) -> dict:
    """O estado da simulação da empresa do token, ou `existe: false`."""
    return estado_da_simulacao(conta["tenant_id"])


@router.post("/simulacao/cliente")
async def criar_cliente(corpo: dict = Body(...), conta: dict = Depends(get_conta)) -> dict:
    """Cria o cliente fictício: `{nome, mensalidade, perfil, verdade:
    {dias_ate_saldo, chance_pagar, vai_revogar}}`. Exige `owner` ou `admin`.
    422 com `dado_que_parece_real` se o nome parecer CPF, e-mail, telefone ou
    chave Pix. Nenhum outro campo é aceito. A cobrança só acontece em
    `POST /simulacao/cobrar`."""
    exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    try:
        cliente = simulador.validar_cliente(corpo)
    except simulador.SimulacaoInvalida as e:
        raise _erro(e) from None
    agora = _agora_da_simulacao(tenant_id)
    with ambiente.em_simulacao(tenant_id, agora):
        simulador.acertar_relogio(tenant_id, agora)
        simulador.criar_pagador(tenant_id, cliente, agora)
    logger.info("[SIMULACAO] tenant=%s: cliente fictício criado", tenant_id)
    return estado_da_simulacao(tenant_id)


@router.post("/simulacao/cobrar")
async def cobrar_cliente(conta: dict = Depends(get_conta)) -> dict:
    """A cobrança do dia do cliente fictício atual vai ao PSP simulado, que
    responde conforme a verdade escondida. Se falhar, o sistema recebe a falha
    e age sozinho a partir daí. Exige `owner` ou `admin`. 409 sem cliente ou se
    ele já foi cobrado."""
    exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    agora = _agora_da_simulacao(tenant_id)
    pagador = None
    if simulador.existe(tenant_id):
        with ambiente.em_simulacao(tenant_id, agora):
            pagador = simulador.pagador_atual(tenant_id)
    if pagador is None:
        raise _409("sem_cliente_ficticio", "crie o cliente fictício antes de cobrar")
    if pagador.get("cobrado_em"):
        raise _409("cliente_ja_cobrado", "este cliente fictício já foi cobrado; avance o "
                                         "relógio ou crie outro cliente")
    await cobrar(tenant_id, pagador, agora)
    return estado_da_simulacao(tenant_id)


@router.post("/simulacao/avancar")
async def avancar_relogio(corpo: dict = Body(...), conta: dict = Depends(get_conta)) -> dict:
    """Avança o relógio simulado da empresa: `{"dias": N}` (1 a 60) ou
    `{"ate_proxima_acao": true}`. Para na primeira ação que acontecer. Só os
    ciclos simulados desta empresa andam: o relógio de verdade não muda. Exige
    `owner` ou `admin`."""
    exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    if not isinstance(corpo, dict) or set(corpo) - {"dias", "ate_proxima_acao"} or len(corpo) != 1:
        raise HTTPException(status_code=422, detail={
            "motivo": "corpo_invalido", "campo": "corpo",
            "detalhe": 'esperado {"dias": N} ou {"ate_proxima_acao": true}'})
    dias, ate = corpo.get("dias"), corpo.get("ate_proxima_acao")
    if "dias" in corpo and (isinstance(dias, bool) or not isinstance(dias, int)
                            or not 1 <= dias <= DIAS_MAXIMO):
        raise HTTPException(status_code=422, detail={
            "motivo": "dias_invalido", "campo": "dias",
            "detalhe": f"esperado inteiro entre 1 e {DIAS_MAXIMO}"})
    if "ate_proxima_acao" in corpo and ate is not True:
        raise HTTPException(status_code=422, detail={
            "motivo": "corpo_invalido", "campo": "ate_proxima_acao", "detalhe": "esperado true"})
    if _relogio(tenant_id) is None:
        raise _409("sem_simulacao", "não há simulação para avançar: crie um cliente fictício")
    await avancar(tenant_id, dias, bool(ate))
    return estado_da_simulacao(tenant_id)


@router.delete("/simulacao")
async def limpar_simulacao(conta: dict = Depends(get_conta)) -> dict:
    """Apaga TUDO o que é fictício da empresa: clientes, ciclos, tentativas,
    mensagens, retenções, a trilha e o dataset da simulação, a cópia do bandit e
    o relógio. Nada do que é real é tocado. Exige `owner` ou `admin`."""
    exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    try:
        apagados = simulador.limpar(tenant_id)
    except PermissionError:
        raise _409("simulacao_em_uso", "a simulação está sendo usada por outra requisição; "
                                       "tente de novo em instantes") from None
    return {**estado_da_simulacao(tenant_id), "limpo": True, "arquivos_apagados": apagados}


@router.post("/simulacao/retencao")
async def simular_retencao(corpo: dict = Body(...), conta: dict = Depends(get_conta)) -> dict:
    """Um cliente fictício em risco: `{nome, mrr, sinais: {uso_caiu, tickets,
    atraso, abriu_cancelamento}, propensao: {<oferta>: 0..1}}`. Cada sinal
    marcado vira o dado que o sistema receberia de um cliente de verdade, e o
    sistema avalia o risco (pelo modelo ou pela régua, como estiver), decide se
    intervém e escolhe a oferta e o canal (com uma cópia separada do bandit).
    O aceite vem da propensão escondida, não do bandit. Quando a régua decide o
    risco, não há oferta abaixo do corte de intervenção (`corte_de_intervencao`).
    Quando o modelo de IA decide, o corte não vale (`corte_de_intervencao` vem
    nulo): o sistema intervém por intenção explícita ou pela posição na base
    (`regra_de_intervencao`), e `sem_oferta_porque` diz por que não interveio.
    Exige `owner` ou `admin`."""
    exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    try:
        cliente = simulador.validar_cliente_em_risco(corpo)
    except simulador.SimulacaoInvalida as e:
        raise _erro(e) from None
    from . import app as app_module                       # import tardio
    agora = _agora_da_simulacao(tenant_id)
    with ambiente.em_simulacao(tenant_id, agora):
        simulador.acertar_relogio(tenant_id, agora)
        registro = simulador.criar_cliente_em_risco(tenant_id, cliente, agora)
        user_id = registro["user_id"]
        props = simulador.props_dos_sinais(cliente["mrr"], cliente["sinais"])
        evento = simulador.evento_dos_sinais(cliente["sinais"], EVENTO_DO_CLIENTE_EM_RISCO)
        final = await app_module._run_voluntary_pipeline(
            user_id, evento, props, tenant_id=tenant_id) or {}
        risco, criticidade = final.get("risk_score"), final.get("criticality") or "padrao"
        oferta, canal = final.get("offer_type"), final.get("channel")
        decisao_de_risco = next((d for d in final.get("decisoes") or []
                                 if d.get("tipo_decisao") == retention_log.TIPO_RISCO), {})
        por_modelo = decisao_de_risco.get("modelo") not in (None, retention_log.MODELO_REGRA)
        # Só existe quando o modelo v3 decidiu o risco (ver `voluntary_agent`).
        regra = (decisao_de_risco.get("saida") or {}).get("regra_de_intervencao")
        intensidade = final.get("intensidade_da_oferta") if regra else None
        motivo = _motivo(cliente, props, user_id, risco, criticidade,
                         (decisao_de_risco.get("saida") or {}).get("posicao") if regra else None)
        aceitou = None
        valores = None
        if oferta and final.get("offer_sent"):
            aceitou = simulador.aceita_a_oferta(user_id, oferta, cliente["propensao"])
            await voluntary_agent.registrar_resultado_externo(
                user_id, oferta, final.get("profile") or "PJ", aceitou, tenant_id=tenant_id)
            if aceitou:
                mantido.sincronizar_sem_levantar(tenant_id)
                valores = mantido.valores_da_retencao(oferta, cliente["mrr"], tenant_id)
        faixa = vol.FAIXA_DA_CRITICIDADE.get(criticidade, "sem_risco")
        simulador.registrar_resultado_da_retencao(
            registro["id"], faixa=faixa, motivo=motivo,
            decidido_por="modelo" if por_modelo else "regua", oferta=oferta, canal=canal,
            aceitou=None if aceitou is None else int(aceitou))
        consideradas = final.get("ofertas_consideradas") or []
        config = configuracao.ler(tenant_id)
    escolhida = next((c for c in consideradas if c.get("offer") == oferta),
                     consideradas[0] if consideradas else None)
    estimada = escolhida.get("p_estimado") if escolhida else None
    return {
        "faixa": faixa,
        "motivo": motivo,
        "decidido_por": "modelo" if por_modelo else "regua",
        # O risco que o sistema calculou (0 a 1) e o corte abaixo do qual ele
        # não intervém: é o que explica um "sem oferta" QUANDO A RÉGUA DECIDE.
        # Quando o modelo de IA decide, o corte não vale (vem nulo), e quem
        # explica é a regra de intervenção.
        "risco": None if risco is None else round(float(risco), 2),
        "corte_de_intervencao": None if regra else voluntary_agent.CORTE_DE_INTERVENCAO,
        "regra_de_intervencao": regra,
        "intensidade": intensidade,
        "sem_oferta_porque": None if oferta else _SEM_OFERTA_PELA_REGRA.get(regra),
        "oferta": oferta,
        "oferta_legivel": retention_log.ROTULOS_DE_OFERTA.get(oferta) if oferta else None,
        "canal": canal,
        "canal_legivel": retention_log.ROTULOS_DE_CANAL.get(canal, canal) if canal else None,
        "porque": None if not oferta else _porque_da_oferta(regra, intensidade, estimada),
        "aceitou": aceitou,
        "valor_mantido_liquido": valores["liquido"] if valores else 0.0,
        "sem_crai": ("O sistema não interveio: com ou sem a CRAI, este cliente segue como está."
                     if not oferta
                     else "Sem a CRAI, ninguém perceberia os sinais até o pedido de cancelamento, "
                          "quando já é tarde para oferecer algo."),
        "meses_de_mrr": mantido.MESES_DE_MRR_MANTIDOS,
        "prazo_estorno_dias": config["prazo_estorno_dias"],
        "simulado": True,
    }

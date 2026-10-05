"""crai/api/titular.py — o direito à explicação (LGPD, Art. 20) para a CONTROLADORA.

    GET  /titular/explicacao/{sujeito_id}    autenticada por tenant
    POST /titular/exportar                   Art. 18: tudo o que a CRAI guarda do titular
    POST /titular/anonimizar                 Art. 18: apaga contatos e texto de mensagem
    GET  /titular/texto-para-politica        o texto pronto para a política da empresa

(As três últimas entraram na Rodada 3, Fase 6: ver o bloco no fim do arquivo.)

QUEM É QUEM. O titular é o cliente final da empresa cliente. A empresa
cliente é a CONTROLADORA; a CRAI é OPERADORA. O pedido de revisão do titular
(Art. 20, caput) chega à controladora, e é ela quem responde. Esta rota
existe para que ela consiga: devolve, para um `sujeito_id` do SEU tenant, as
decisões automatizadas registradas na trilha (`retention_log.
decisoes_automatizadas`), cada uma com a explicação legível montada no
momento da decisão. **Não existe rota pública para o titular**, de propósito.

O QUE O §1º PEDE E O QUE ESTA ROTA ENTREGA. O Art. 20 §1º obriga o
controlador a fornecer "informações claras e adequadas a respeito dos
critérios e dos procedimentos utilizados para a decisão automatizada,
observados os segredos comercial e industrial". Por isso:

  ENTRA    quais features pesaram, em que direção e com que valores
           (`entradas`, `contribuicoes`); o que foi decidido (`saida`); qual
           modelo e qual versão de artefato decidiram; a frase em pt-BR
           (`explicacao`); e a integridade da trilha (`cadeia`).
  NÃO ENTRA — SEGREDO COMERCIAL E INDUSTRIAL (Art. 20 §1º, parte final) —
           os coeficientes e pesos internos dos modelos, os hiperparâmetros
           de treino, e os estados alpha/beta do bandit. A trilha já não os
           grava (`retention_log.CHAVES_FORA_DA_TRILHA`); a rota os tira de
           novo na saída, por defesa em profundidade. É a diferença entre
           uma escolha defensável e uma omissão: o que pesou é dito; como o
           modelo foi construído, não.

404 IDÊNTICO. Sujeito sem decisão registrada → 404. Sujeito de OUTRO tenant
→ 404 também, nunca 403, com o MESMO corpo: responder diferente confirmaria
a existência daquela pessoa para quem não deveria saber que ela existe. É o
mesmo padrão das rotas de cliente (`api/clientes.py`).

Este módulo NÃO importa de `app.py` (é o `app.py` que o monta).
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from ..accounts import get_conta, get_tenant_id
from ..accounts.auth import exigir_papel
from ..churn_voluntary import clientes_importados, mantido
from ..churn_voluntary import retention_log as trilha
from ..dunning import ciclo_cobranca as cc
from ..dunning import configuracao
from . import datas, registro_acesso

logger = logging.getLogger(__name__)

router = APIRouter(tags=["titular"])

LIMITE_PADRAO = 20
LIMITE_MAXIMO = 200

# O que a resposta declara sobre a própria base legal — texto fixo, para a
# controladora saber o que está (e o que não está) recebendo.
BASE_LEGAL = {
    "dispositivo": "LGPD, Lei 13.709/2018, Art. 20, caput e §1º",
    "papel_da_crai": "operadora; a controladora é a empresa cliente, e é ela quem responde ao titular",
    "o_que_entra": "critérios e procedimento: as features consideradas, com valores e direção; a "
                   "decisão; o modelo e a versão do artefato; a explicação em português",
    "o_que_nao_entra": "segredo comercial e industrial (Art. 20 §1º, parte final): coeficientes e "
                       "pesos internos dos modelos, hiperparâmetros de treino e os estados "
                       "alpha/beta do algoritmo de ofertas",
}


def _404() -> HTTPException:
    return HTTPException(status_code=404, detail={
        "motivo": "sujeito_sem_decisao",
        "detalhe": "não há decisão automatizada registrada para este identificador nesta empresa"})


def _publica(d: dict) -> dict:
    """A linha da trilha como a controladora a recebe.

    `_sem_dado_cru` de novo na saída: a trilha já grava limpa, mas uma linha
    antiga (ou uma chave nova na lista de segredos) não pode vazar por aqui.
    """
    return {
        "id": d["id"],
        "decidido_em": d["decidido_em"],
        "dominio": d["dominio"],
        "tipo_decisao": d["tipo_decisao"],
        "modelo": d["modelo"],
        "modelo_versao": d.get("modelo_versao"),
        "explicacao": d["explicacao"],
        "entradas": trilha._sem_dado_cru(d.get("entradas") or {}),
        "saida": trilha._sem_dado_cru(d.get("saida") or {}),
        "contribuicoes": trilha._sem_dado_cru(d["contribuicoes"]) if d.get("contribuicoes") else None,
        "hash_linha": d["hash_linha"],
        "hash_anterior": d.get("hash_anterior"),
    }


@router.get("/titular/explicacao/{sujeito_id}")
async def explicacao_do_titular(
    sujeito_id: str,
    limite: Optional[int] = None,
    antes_de: Optional[int] = None,
    tenant_id: str = Depends(get_tenant_id),
    conta: dict = Depends(get_conta),
) -> dict:
    """As decisões automatizadas sobre um sujeito DESTE tenant, mais recentes
    primeiro, com paginação — para a controladora responder ao titular.

    Art. 20 §1º: entram os critérios e o procedimento (features com valores
    e direção, decisão, modelo e versão, explicação em pt-BR); NÃO entram
    coeficientes, hiperparâmetros nem os estados alpha/beta do bandit —
    segredo comercial e industrial, ressalvado pelo mesmo parágrafo.

    `?limite=N` (1..200, padrão 20) e `?antes_de=<id>` paginam; a resposta
    traz `proximo_antes_de` para a página seguinte. `cadeia` é o
    `verificar_cadeia` do tenant inteiro, para a controladora saber se a
    trilha que está lendo está íntegra. 404 para sujeito sem decisão e para
    sujeito de outro tenant, com corpo idêntico.
    """
    limite = LIMITE_PADRAO if limite is None else limite
    if limite < 1 or limite > LIMITE_MAXIMO:
        raise HTTPException(status_code=422, detail={
            "motivo": "limite_invalido", "campo": "limite",
            "detalhe": f"esperado inteiro entre 1 e {LIMITE_MAXIMO}"})
    if antes_de is not None and antes_de < 1:
        raise HTTPException(status_code=422, detail={
            "motivo": "antes_de_invalido", "campo": "antes_de",
            "detalhe": "esperado o id da última decisão recebida"})

    pagina = trilha.decisoes_do_sujeito(tenant_id, sujeito_id, limite=limite + 1, antes_de=antes_de)
    if not pagina and (antes_de is None
                       or not trilha.decisoes_do_sujeito(tenant_id, sujeito_id, limite=1)):
        raise _404()
    tem_mais = len(pagina) > limite
    pagina = pagina[:limite]

    logger.info("[ART20] tenant=%s explicacao sujeito=%s decisoes=%d", tenant_id, sujeito_id, len(pagina))
    # Quem leu dado de titular, e quando (Bloco 4): tenant, rota, papel. Nunca o
    # sujeito pedido nem quem pediu. O papel vem de `get_conta`; `get_tenant_id`
    # continua sendo a dependency que a catraca do Art. 20 confere por nome.
    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_EXPLICACAO, conta.get("papel"),
                              datas.agora_local())
    return {
        "sujeito_id": sujeito_id,
        "decisoes": [_publica(d) for d in pagina],
        "paginacao": {"limite": limite, "antes_de": antes_de,
                      "proximo_antes_de": pagina[-1]["id"] if tem_mais and pagina else None,
                      "tem_mais": tem_mais},
        "cadeia": trilha.verificar_cadeia(tenant_id),
        "base_legal": BASE_LEGAL,
    }


# ══════════════════════════════════════════════════════════════════════════
# Rodada 3, Fase 6: exportar e anonimizar (LGPD, Art. 18), e o texto pronto
# para a política de privacidade da empresa.
# ══════════════════════════════════════════════════════════════════════════
#
#     POST /titular/exportar              {"customer_id_externo": "..."}
#     POST /titular/anonimizar            {"customer_id_externo": "..."}
#     GET  /titular/texto-para-politica
#
# QUEM PODE. Exportar e anonimizar: só `owner` e `admin`, e as duas entram no
# registro de acesso (tenant, rota, papel e hora; nunca o titular pedido). O
# texto da política não tem dado de ninguém: qualquer papel lê.
#
# O TITULAR É IDENTIFICADO PELA CHAVE QUE A EMPRESA USA (`customer_id_externo`).
# Titular de outra empresa responde o MESMO 404 de um titular que não existe.
#
# O QUE A EXPORTAÇÃO TRAZ: tudo o que a CRAI guarda sobre aquele titular, nas
# quatro frentes: o cadastro (a linha da base), as cobranças que falharam (os
# ciclos, com as tentativas e as mensagens), a retenção (os ciclos do
# voluntário e os valores mantidos) e as decisões automatizadas (a trilha do
# Art. 20, com a explicação). O QUE ELA NÃO TRAZ: o VALOR do e-mail e do
# telefone. A exportação diz se cada contato está guardado (`contatos_guardados`),
# sem repetir o dado: quem o forneceu foi a própria empresa, e a regra da
# Rodada 3 é que nenhuma rota devolve telefone, e-mail, CPF ou chave Pix. Também
# não traz a fee (R11) nem os segredos comerciais que `_publica` já tira.
#
# O QUE A ANONIMIZAÇÃO FAZ, nesta ordem:
#   1. marca o titular como "não contatar" (nada mais sai para ele);
#   2. apaga o TEXTO de todas as mensagens dos ciclos dele;
#   3. apaga o nome, o e-mail, o telefone e o motivo de cancelamento da base.
# O QUE FICA, de propósito: os valores, as datas e os desfechos (são as
# métricas agregadas, que a tarefa manda manter), a chave que a empresa usa
# para o cliente (um pseudônimo dela) e a TRILHA DO ART. 20 inteira. A trilha
# não é tocada: ela não guarda contato nem texto de mensagem, e só sai pelo
# prazo de retenção (`apagar_trilha_expirada`).

LIMITE_DE_DECISOES_NA_EXPORTACAO = 1000
ARQUIVO_DA_POLITICA = "texto-para-politica-de-privacidade.md"
CAMINHO_DA_POLITICA = (Path(__file__).resolve().parents[3] / "docs" / "lgpd" / ARQUIVO_DA_POLITICA)

# Do cadastro, o que a exportação repete. Os contatos ficam de fora (ver acima).
CAMPOS_DO_CADASTRO = ("customer_id_externo", "nome", "mrr", "billing_profile", "id_recorrencia",
                      "days_since_last", "features_used_30d", *clientes_importados.COMPORTAMENTO_V3,
                      "importado_em", "atualizado_em", "cancelado_em", "motivo_cancelamento")
CONTATOS = ("email", "telefone")
# As chaves da configuração da empresa que o texto da política cita.
PRAZOS_DO_TEXTO = ("intervalo_minimo_ofertas_dias", "retencao_mensagens_dias",
                   "retencao_trilha_anos")


def _404_titular() -> HTTPException:
    return HTTPException(status_code=404, detail={
        "motivo": "titular_nao_encontrado",
        "detalhe": "não há dado deste identificador nesta empresa"})


def _id_do_corpo(corpo) -> str:
    if not isinstance(corpo, dict) or set(corpo) != {"customer_id_externo"}:
        raise HTTPException(status_code=422, detail={
            "motivo": "corpo_invalido", "campo": "customer_id_externo",
            "detalhe": 'esperado {"customer_id_externo": "..."}, e só este campo'})
    cid = corpo["customer_id_externo"]
    if not isinstance(cid, str) or not cid.strip() or len(cid) > 200:
        raise HTTPException(status_code=422, detail={
            "motivo": "identificador_invalido", "campo": "customer_id_externo",
            "detalhe": "esperado um texto de 1 a 200 caracteres"})
    return cid.strip()


def _cliente_da_base(tenant_id: str, cid: str) -> Optional[dict]:
    try:
        return clientes_importados.obter(tenant_id, cid)
    except clientes_importados.ConfiguracaoAusente:
        return None


def _marca(tenant_id: str, cid: str) -> Optional[dict]:
    try:
        return clientes_importados.nao_contatar(tenant_id, cid)
    except clientes_importados.ConfiguracaoAusente:
        return None


def dados_do_titular(tenant_id: str, cid: str) -> Optional[dict]:
    """Tudo o que a CRAI guarda sobre o titular `cid` DESTE tenant, ou None se
    não há nada (titular inexistente, ou de outra empresa)."""
    cliente = _cliente_da_base(tenant_id, cid)
    user_id = f"user:{cid}"
    recorrencia = cliente.get("id_recorrencia") if cliente else None
    ciclos = cc.ciclos_da_recorrencia(tenant_id, recorrencia) if recorrencia else []
    retencao = trilha.ciclos_do_cliente(tenant_id, user_id)
    decisoes = trilha.decisoes_do_sujeito(tenant_id, user_id,
                                          limite=LIMITE_DE_DECISOES_NA_EXPORTACAO)
    if recorrencia:
        decisoes += trilha.decisoes_do_sujeito(tenant_id, recorrencia,
                                               limite=LIMITE_DE_DECISOES_NA_EXPORTACAO)
    marca = _marca(tenant_id, cid)
    if cliente is None and not retencao and not decisoes and marca is None:
        return None
    return {"cliente": cliente, "recorrencia": recorrencia, "ciclos": ciclos,
            "retencao": retencao, "decisoes": sorted(decisoes, key=lambda d: d["id"]),
            "mantidas": mantido.do_cliente(tenant_id, cid), "marca": marca}


def _ciclo_exportado(ciclo: dict) -> dict:
    return {
        "aberto_em": datas.iso_com_fuso(ciclo.get("aberto_em")),
        "situacao": ciclo["estado"],
        "valor_da_cobranca": ciclo.get("valor"),
        "causa_da_falha": ciclo.get("causa_original"),
        "recuperado_em": datas.iso_com_fuso(ciclo.get("recuperado_em")),
        "tentativas": [{"numero": t["numero"],
                        "agendada_para": datas.iso_com_fuso(t.get("agendada_para")),
                        "resultado": t.get("resultado"),
                        "resultado_em": datas.iso_com_fuso(t.get("resultado_em"))}
                       for t in cc.tentativas_do_ciclo(ciclo["id"])],
        "mensagens": [{"abordagem": m["abordagem"], "canal": m["canal"],
                       "texto": None if m.get("texto_apagado_em") else m.get("texto"),
                       "texto_apagado_em": datas.iso_com_fuso(m.get("texto_apagado_em")),
                       "escolhida": bool(m["escolhida"]),
                       "gerada_em": datas.iso_com_fuso(m.get("gerada_em")),
                       "enviada_em": datas.iso_com_fuso(m.get("enviada_em"))}
                      for m in cc.mensagens_do_ciclo(ciclo["id"])],
    }


def _retencao_exportada(ciclo: dict) -> dict:
    return {"registrado_em": datas.iso_com_fuso(ciclo.get("registrado_em")),
            "evento": ciclo.get("event"), "mrr": ciclo.get("mrr"),
            "dias_sem_entrar": ciclo.get("days_since_last"),
            "funcionalidades_usadas_30d": ciclo.get("features_used_30d"),
            "risco": ciclo.get("risk_score"), "criticidade": ciclo.get("criticality"),
            "oferta": ciclo.get("offer_type"), "canal": ciclo.get("channel"),
            "oferta_enviada": bool(ciclo.get("offer_sent")),
            "aceitou": None if ciclo.get("accepted") is None else bool(ciclo["accepted"]),
            "desfecho_em": datas.iso_com_fuso(ciclo.get("desfecho_em"))}


@router.post("/titular/exportar")
async def exportar_titular(corpo: dict = Body(...), conta: dict = Depends(get_conta)) -> dict:
    """EXPORTAÇÃO (LGPD, Art. 18, II e V): tudo o que a CRAI guarda sobre um
    cliente final da empresa do token. Corpo: `{"customer_id_externo": "..."}`.
    Exige `owner` ou `admin`. Entra no registro de acesso. 404 idêntico para
    titular inexistente e de outra empresa. A resposta diz quais contatos
    estão guardados sem repetir o valor deles, e não traz a fee."""
    exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    cid = _id_do_corpo(corpo)
    dados = dados_do_titular(tenant_id, cid)
    if dados is None:
        raise _404_titular()
    cliente = dados["cliente"] or {}
    marca = dados["marca"]
    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_EXPORTAR, conta.get("papel"),
                              datas.agora_local())
    logger.info("[ART18] tenant=%s exportacao: %d ciclo(s), %d decisao(oes)", tenant_id,
                len(dados["ciclos"]) + len(dados["retencao"]), len(dados["decisoes"]))
    return {
        "customer_id_externo": cid,
        "exportado_em": datas.iso_com_fuso(datas.agora_local()),
        "cadastro": ({c: cliente.get(c) for c in CAMPOS_DO_CADASTRO} if dados["cliente"] else None),
        "contatos_guardados": {c: bool(cliente.get(c)) for c in CONTATOS},
        "nao_contatar": ({"marcado_em": datas.iso_com_fuso(marca["marcado_em"]),
                          "origem": marca["origem"]} if marca else None),
        "cobrancas": [_ciclo_exportado(c) for c in dados["ciclos"]],
        "retencao": [_retencao_exportada(c) for c in dados["retencao"]],
        "valores_mantidos": [{"oferta": m["offer_type"], "aceito_em": datas.iso_com_fuso(m["aceito_em"]),
                              "mrr": m["mrr"], "desconto": m["desconto"],
                              "cancelamento_em": datas.iso_com_fuso(m.get("cancelamento_em"))}
                             for m in dados["mantidas"]],
        "decisoes_automatizadas": [_publica(d) for d in dados["decisoes"]],
        "base_legal": {
            "dispositivo": "LGPD, Lei 13.709/2018, Art. 18, II e V (acesso e portabilidade)",
            "papel_da_crai": BASE_LEGAL["papel_da_crai"],
            "o_que_nao_entra": "o valor do e-mail e do telefone (a exportação diz se estão "
                               "guardados; quem os forneceu foi a empresa); e o que o Art. 20 §1º "
                               "ressalva como segredo comercial e industrial",
        },
    }


@router.post("/titular/anonimizar")
async def anonimizar_titular(corpo: dict = Body(...), conta: dict = Depends(get_conta)) -> dict:
    """ANONIMIZAÇÃO (LGPD, Art. 18, IV e VI) de um cliente final da empresa do
    token. Corpo: `{"customer_id_externo": "..."}`. Exige `owner` ou `admin`.
    Entra no registro de acesso. Não dá para desfazer.

    Apaga o texto das mensagens e os contatos (nome, e-mail, telefone, motivo
    de cancelamento) daquele titular, e o marca como "não contatar". MANTÉM os
    valores, as datas e os desfechos (as métricas agregadas não mudam), a chave
    que a empresa usa para ele e a trilha do Art. 20, que não é tocada.
    Idempotente: anonimizar de novo devolve zeros. 404 idêntico para titular
    inexistente e de outra empresa."""
    exigir_papel(conta, "owner", "admin")
    tenant_id = conta["tenant_id"]
    cid = _id_do_corpo(corpo)
    dados = dados_do_titular(tenant_id, cid)
    if dados is None:
        raise _404_titular()
    agora = datas.agora_local()
    # 1) Antes de tudo: nada mais sai para este titular.
    marca = clientes_importados.marcar_nao_contatar(
        tenant_id, cid, clientes_importados.ORIGEM_ANONIMIZACAO)
    # 2) O texto das mensagens. 3) Os contatos.
    mensagens = (cc.apagar_texto_da_recorrencia(tenant_id, dados["recorrencia"], agora)
                 if dados["recorrencia"] else 0)
    apagados = clientes_importados.apagar_contatos(tenant_id, cid) if dados["cliente"] else []
    registro_acesso.registrar(tenant_id, registro_acesso.ROTA_ANONIMIZAR, conta.get("papel"),
                              agora)
    logger.info("[ART18] tenant=%s anonimizacao: %d contato(s), %d mensagem(ns)", tenant_id,
                len(apagados or []), mensagens)
    return {
        "customer_id_externo": cid,
        "anonimizado_em": datas.iso_com_fuso(agora),
        "contatos_apagados": list(apagados or []),
        "mensagens_apagadas": mensagens,
        "ciclos_com_mensagem_apagada": len(dados["ciclos"]),
        "nao_contatar": {"marcado_em": datas.iso_com_fuso(marca["marcado_em"]),
                         "origem": marca["origem"]},
        "o_que_ficou": "os valores, as datas e os desfechos das cobranças e das ofertas (as "
                       "métricas agregadas não mudam), o identificador que a sua empresa usa para "
                       "o cliente e o registro das decisões automatizadas, que é guardado pelo "
                       "prazo da trilha e não contém contato nem texto de mensagem",
    }


@router.get("/titular/texto-para-politica")
async def texto_para_politica(conta: dict = Depends(get_conta)) -> dict:
    """O texto pronto "O que a CRAI faz com os dados dos seus clientes", para a
    empresa colar na política de privacidade dela. É um arquivo versionado
    (`docs/lgpd/texto-para-politica-de-privacidade.md`); a rota devolve o
    título e o corpo. Qualquer papel lê: não há dado de ninguém aqui."""
    try:
        bruto = CAMINHO_DA_POLITICA.read_text(encoding="utf-8")
    except OSError:
        raise HTTPException(status_code=503, detail={
            "motivo": "texto_indisponivel",
            "detalhe": f"o arquivo docs/lgpd/{ARQUIVO_DA_POLITICA} não está nesta instalação"})
    linhas = [l.rstrip() for l in bruto.replace("\r\n", "\n").split("\n")]
    # O arquivo tem uma nota para quem mantém o repositório, acima da linha "---".
    if "---" in linhas:
        linhas = linhas[linhas.index("---") + 1:]
    titulo = next((l[2:].strip() for l in linhas if l.startswith("# ")), "")
    corpo = "\n".join(l for l in linhas if not l.startswith("# ")).strip()
    # Os prazos do texto são os da configuração DESTA empresa, e não números fixos.
    config = configuracao.ler(conta["tenant_id"])
    valores = {chave: config[chave] for chave in PRAZOS_DO_TEXTO}
    for chave, valor in valores.items():
        corpo = corpo.replace("{{" + chave + "}}", str(valor))
    return {"titulo": titulo, "texto": corpo, "prazos": valores,
            "arquivo": f"docs/lgpd/{ARQUIVO_DA_POLITICA}"}

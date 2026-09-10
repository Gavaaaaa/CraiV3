"""crai/scripts/relatorio.py -- executa o sistema e escreve o relatorio da execucao.

O QUE ISTO E. Nao e uma demonstracao: e a saida do sistema. O script roda os
pipelines reais -- o agente de churn involuntario (`agent/main_agent.py`), o
agente de churn voluntario (`churn_voluntary/voluntary_agent.py`) e o caminho
self-service (`importacao` + `batch_scoring` + `insights_unificados`) -- e
renderiza em HTML o estado final que cada um produziu. Nenhum numero da pagina
e escrito a mao: todos vem do `ainvoke` do grafo ou do retorno das funcoes.

POR QUE EXISTE. `test_pipeline.py` ja executa os cenarios, mas a saida dele e o
terminal, que rola e some. Uma banca (ou qualquer pessoa que clone o
repositorio) precisa de um artefato que sobreviva a execucao e possa ser lido
com calma -- inclusive a trilha de raciocinio do agente e a decomposicao SHAP,
que sao o que distingue este sistema de um conjunto de regras.

COMO RODAR (a partir da pasta `crai/`):

    python -m crai.scripts.train_all      # sem isto, tudo cai na heuristica
    python -m crai.scripts.relatorio

Gera `relatorio_execucao.html` na pasta `crai/` e imprime o caminho.

DEPENDENCIAS EXTERNAS: nenhuma. Sem `ANTHROPIC_API_KEY` as mensagens saem do
template de fallback; sem `SUPABASE_DB_URL` a base importada vai para um SQLite
local. As duas coisas ficam declaradas no rodape do relatorio, para o relatorio
nunca afirmar mais do que a execucao entregou.

CONSOLE: todo `print` deste modulo usa apenas caracteres que o console do
Windows (cp1252) codifica. Ver `tests/test_encoding_saida.py`.
"""

import asyncio
import html
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent      # .../crai/
SAIDA = RAIZ / "relatorio_execucao.html"

# WINDOWS: blindagem da saida, ANTES de importar qualquer modulo que imprima.
# No console o Python escreve via API Unicode e tudo funciona; mas quando a
# saida e redirecionada para arquivo ou pipe (`> saida.txt`, terminal de IDE,
# CI), ele cai para a codificacao local -- cp1252 no Windows pt-BR -- e um
# unico caractere fora dela derruba o processo inteiro com UnicodeEncodeError.
# O agente imprime setas na trilha de raciocinio (`agent/workflow.py`), e isso
# e divida declarada em `tests/test_encoding_saida.py`. Aqui a saida e
# reconfigurada para UTF-8 com `errors="replace"`: no pior caso um caractere
# vira '?', nunca uma execucao perdida no meio.
for _fluxo in (sys.stdout, sys.stderr):
    try:
        _fluxo.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):        # stream substituido ou sem suporte
        pass

# SQLite local para a base importada, a menos que o ambiente ja declare um
# destino. `clientes_importados._destino()` le as duas envs a cada chamada e
# falha alto se nenhuma existir -- de proposito, para nunca gravar "em algum
# lugar" por default.
if not os.getenv("SUPABASE_DB_URL") and not os.getenv("CRAI_CLIENTES_DB"):
    os.environ["CRAI_CLIENTES_DB"] = str(RAIZ / "relatorio_clientes.db")

from crai.agent.main_agent import crai_agent                    # noqa: E402
from crai.agent.state import AgentState                         # noqa: E402
from crai.churn_voluntary import (                              # noqa: E402
    importacao, insights_unificados,
)
from crai.churn_voluntary.state import ChurnVoluntaryState      # noqa: E402
from crai.churn_voluntary.voluntary_agent import agente_do_modo  # noqa: E402
from crai.ml.failure_classifier import FailureClassifier        # noqa: E402

TENANT = "empresa_demo"

ROTULO_CRIT = {"critico": "Critico", "alto": "Alto", "padrao": "Padrao",
               "dado_insuficiente": "Sem dado"}
COR_CRIT = {"critico": "#f87171", "alto": "#ffb86c", "padrao": "#5fd39b",
            "dado_insuficiente": "#9aa0a6"}
ROTULO_OFERTA = {"desconto_10": "Desconto de 10%", "desconto_20": "Desconto de 20%",
                 "pausa_1_mes": "Pausa de 1 mes",
                 "pix_boleto_flash": "Pix / Boleto Flash"}

# `shap_explanation["readable"]` so nomeia as features de maior contribuicao.
# As demais aparecem com o nome cru do dataset; este mapa as traduz para o
# relatorio nao misturar portugues e ingles na mesma lista.
ROTULO_FEATURE = {
    "tenure_months": "Tenure (meses)", "payment_history_score": "Historico de pagamento",
    "gateway_error_code": "Codigo de erro", "invoice_amount": "Valor da fatura R$",
    "avg_ticket": "Ticket medio R$", "day_of_month": "Dia do mes",
    "hour_of_day": "Hora da cobranca", "day_of_week": "Dia da semana",
    "failure_count_90d": "Falhas (90 dias)", "attempt_count": "Tentativas anteriores",
    "card_brand": "Bandeira do cartao", "ltv_estimated": "LTV estimado R$",
}

# Cenarios de entrada. Sao ENTRADAS, nao resultados: tudo o que aparece no
# relatorio a partir daqui foi calculado pelo sistema.
CENARIOS_INVOLUNTARIO = [
    ("Saldo insuficiente, cliente novo", "RN_ana_001", 299.90, "AM04", 0),
    ("Saldo insuficiente, cliente antigo", "RN_bruno_002", 149.00, "AM04", 0),
    ("Limite excedido, ticket alto", "RN_clara_003", 899.00, "AM02", 1),
    ("Autorizacao revogada pelo pagador", "RN_diego_004", 199.00, "MD01", 0),
]

CENARIOS_VOLUNTARIO = [
    ("Abriu a pagina de cancelamento", "ana_silva", "Cancellation Page Viewed",
     {"days_since_last": 12, "features_used_30d": 3, "mrr": 890.0,
      "billing_profile": "PJ", "on_site_now": True}),
    ("Clicou em fazer downgrade", "bruno_costa", "Downgrade Clicked",
     {"days_since_last": 5, "features_used_30d": 6, "mrr": 2450.0,
      "billing_profile": "CLT", "on_site_now": True}),
    ("Inativo ha semanas", "clara_dias", "Session Started",
     {"days_since_last": 31, "features_used_30d": 1, "mrr": 640.0,
      "billing_profile": "freelancer", "on_site_now": False}),
    ("Cliente engajado", "diego_reis", "Session Started",
     {"days_since_last": 1, "features_used_30d": 18, "mrr": 1180.0,
      "billing_profile": "CLT", "on_site_now": True}),
]

BASE_CSV = """customer_id_externo,mrr,billing_profile,days_since_last,features_used_30d,email
ACME-2291,890,PJ,47,1,financeiro@acme-exemplo.com.br
Vertice-0834,1450,CLT,38,2,contato@vertice-exemplo.com.br
NovaLog-7712,2680,PJ,12,6,ops@novalog-exemplo.com.br
Ipe-4408,640,freelancer,29,2,ana@ipe-exemplo.com.br
Solaris-1190,3200,CLT,4,11,admin@solaris-exemplo.com.br
Kaeta-6621,410,freelancer,26,3,kaeta@exemplo.com.br
Ribalta-3345,1180,PJ,18,4,ti@ribalta-exemplo.com.br
Orbita-9080,750,CLT,9,8,suporte@orbita-exemplo.com.br
Palma-5517,1620,PJ,2,14,diretoria@palma-exemplo.com.br
Trilha-2204,520,freelancer,0,19,oi@trilha-exemplo.com.br
Corvo-8863,980,CLT,6,9,financeiro@corvo-exemplo.com.br
Marena-7351,1340,PJ,1,16,contato@marena-exemplo.com.br
Aurora-1128,1750,CLT,,,fin@aurora-exemplo.com.br
Bandeira-4490,860,PJ,,,contato@bandeira-exemplo.com.br
"""


# -- execucao ---------------------------------------------------------------
async def roda_involuntario(nome, id_rec, valor, codigo, tentativas):
    evento = {"e2e_id": f"E60701190{id_rec}", "valor": valor,
              "status": "cobranca_falhada", "ispb_pagador": "60701190",
              "id_recorrencia": id_rec, "codigo_falha": codigo}
    inicial: AgentState = {
        "payment_event": evento, "payment_method": "pix_automatico",
        "tenant_id": TENANT, "customer_id": id_rec,
        "invoice_id": evento["e2e_id"], "amount": valor, "features": None,
        "failure_cause": None, "recovery_score": None, "p_recovery": None,
        "eprofit": None, "recommend_action": None, "ltv_estimated": None,
        "shap_explanation": None, "feature_importance": None,
        "is_anomalous": None, "reconstruction_error": None,
        "anomaly_explanation": None, "optimal_retry_at": None,
        "estrategia": None, "raciocinio": None, "confidence": None,
        "profile_type": None, "retry_count": tentativas, "next_retry_at": None,
        "retry_exhausted": False, "recovered": False, "pix_retry_schedule": None,
        "dunning_sent": False, "channel": None, "metodo_pagamento": None,
        "message_sent": None,
    }
    final = await crai_agent.ainvoke(inicial, {"configurable": {"thread_id": id_rec}})
    return {"nome": nome, "id": id_rec, "valor": valor, "codigo": codigo, **final}


async def roda_voluntario(nome, user_id, evento, props):
    inicial: ChurnVoluntaryState = {
        "tenant_id": TENANT, "user_id": f"user:{user_id}", "event": evento,
        "props": props, "risk_score": 0.0, "profile": "CLT",
        "criticality": "padrao", "offer_type": None, "channel": None,
        "on_site_now": props.get("on_site_now", False),
        "prior_channel_success": None, "message": None, "offer_sent": False,
        "accepted": None, "retained": False, "is_critical": False,
    }
    final = await agente_do_modo().ainvoke(
        inicial, {"configurable": {"thread_id": f"{TENANT}:{user_id}"}})
    return {"nome": nome, "user_id": user_id, "evento": evento,
            "props": props, **final}


def roda_self_service():
    rel = importacao.importar(TENANT, "base_clientes.csv", BASE_CSV.encode("utf-8"))
    return rel, insights_unificados.clientes_em_risco(TENANT)


# -- renderizacao -----------------------------------------------------------
def esc(v):
    return html.escape(str(v)) if v is not None else "--"


def brl(v):
    if v is None:
        return "--"
    return ("R$ " + f"{float(v):,.2f}").replace(",", "X").replace(".", ",").replace("X", ".")


def partes_shap(bruto):
    """A explicacao SHAP e um dict: `features` (dados) + `readable` (PT-BR).

    As duas listas saem na MESMA ordem (contribuicao decrescente), entao dao
    para casar: o texto de `readable` vira o rotulo e o item de `features` da
    o numero e o sinal. Se as duas divergirem em tamanho -- o que nao deve
    acontecer, mas um relatorio nao pode quebrar por causa disso --, o nome
    cru da feature serve de rotulo.

    Devolve [(rotulo, contribuicao_pct, direcao)].
    """
    if not isinstance(bruto, dict):
        return []
    feats = bruto.get("features") or []
    rotulos = [p.strip() for p in str(bruto.get("readable", "")).split("|") if p.strip()]
    saida = []
    for i, f in enumerate(feats):
        if i < len(rotulos):
            # "Codigo de erro insufficient_funds (+27.9%)" -> tira o percentual,
            # que ja vem estruturado em `contribution_pct`.
            rotulo = rotulos[i].rsplit("(", 1)[0].strip()
        else:
            # `readable` so nomeia as principais; o resto recebe o nome em
            # PT-BR aqui, para o relatorio nao misturar dois idiomas.
            rotulo = (f"{ROTULO_FEATURE.get(f.get('feature'), f.get('feature'))} "
                      f"{f.get('value')}")
        saida.append((rotulo, f.get("contribution_pct") or 0.0,
                      f.get("direction") or "+"))
    return saida


def barra_shap(rotulo, pct, direcao):
    """Uma contribuicao SHAP como barra divergente a partir do centro.

    Divergente porque o dado e divergente: a feature empurra o score PARA CIMA
    (+) ou PARA BAIXO (-). Cada barra carrega o rotulo e o percentual com
    sinal, entao a cor nunca e a unica portadora da informacao.
    """
    positiva = direcao == "+"
    cor = "#5fd39b" if positiva else "#f87171"
    largura = min(abs(float(pct)) * 2.0, 100.0)      # 50% de contribuicao = barra cheia
    lado = ("left:50%" if positiva else f"right:50%")
    return f"""<div class="shaprow">
      <div class="shaplabel">{esc(rotulo)}</div>
      <div class="shapbar"><div class="shapeixo"></div>
        <div class="shapfill" style="{lado};width:{largura / 2:.1f}%;
          background:{cor}"></div></div>
      <div class="shappct" style="color:{cor}">{'+' if positiva else '-'}{abs(float(pct)):.1f}%</div>
    </div>"""


def formata_tentativa(t):
    """Uma entrada do plano de retentativa: dict com numero/quando/valor/origem."""
    if not isinstance(t, dict):
        return esc(t)
    quando = t.get("quando")
    if hasattr(quando, "strftime"):
        quando = quando.strftime("%d/%m/%Y as %H:%M")
    origem = {"fallback_uniforme": "espacamento uniforme (previsao fraca)",
              "payday": "ancorada na liquidez prevista"}.get(t.get("origem"),
                                                             t.get("origem"))
    return (f"tentativa {t.get('numero')} &middot; {esc(quando)} &middot; "
            f"{brl(t.get('valor'))} &middot; {esc(origem)}")


def bloco_involuntario(c):
    shap = partes_shap(c.get("shap_explanation"))
    shap_html = ("".join(barra_shap(*p) for p in shap) if shap
                 else '<div class="racio">Modelo nao treinado nesta execucao: '
                      'sem decomposicao SHAP.</div>')
    plano = c.get("pix_retry_schedule") or []
    plano_html = "".join(f"<li>{formata_tentativa(t)}</li>" for t in plano)
    racio = c.get("raciocinio")
    if isinstance(racio, (list, tuple)):
        racio_html = "".join(f"<div class='rlinha'>{esc(l)}</div>" for l in racio)
    elif racio:
        racio_html = f"<div class='rlinha'>{esc(racio)}</div>"
    else:
        racio_html = ""
    eprofit = c.get("eprofit")
    cor_ep = "#5fd39b" if (eprofit or 0) > 0 else "#f87171"
    return f"""
    <div class="cenario">
      <div class="ctitulo">{esc(c['nome'])}
        <span class="cmeta">{esc(c['id'])} &middot; {brl(c['valor'])} &middot;
          recusa do PSP: {esc(c['codigo'])}</span></div>
      <div class="kpis">
        <div class="kpi"><div class="kl">Causa diagnosticada</div>
          <div class="kv">{esc(c.get('failure_cause'))}</div></div>
        <div class="kpi"><div class="kl">Score de recuperabilidade</div>
          <div class="kv">{esc(c.get('recovery_score'))}<span class="ku">/100</span></div></div>
        <div class="kpi"><div class="kl">e-Profit</div>
          <div class="kv" style="color:{cor_ep}">{brl(eprofit)}</div></div>
        <div class="kpi"><div class="kl">Decisao</div>
          <div class="kv">{esc(c.get('estrategia'))}</div></div>
      </div>
      <div class="sub">Decomposicao SHAP -- quanto cada variavel empurrou o score</div>
      <div class="shapwrap">{shap_html}</div>
      {f'<div class="sub">Trilha de raciocinio do agente</div>'
       f'<div class="racio">{racio_html}</div>' if racio_html else ''}
      {f'<div class="sub">Plano de retentativa (limite do BACEN: 3 em 7 dias)</div>'
       f'<ul class="lista">{plano_html}</ul>' if plano_html else ''}
      {f'<div class="sub">Mensagem enviada</div><div class="msg">{esc(c.get("message_sent"))}</div>'
       if c.get("message_sent") else ''}
    </div>"""


def bloco_voluntario(c):
    crit = c.get("criticality", "padrao")
    risco = c.get("risk_score")
    pct = "--" if risco is None else f"{risco * 100:.0f}%"
    abordado = c.get("offer_type") is not None
    decisao = (f'Oferta: {ROTULO_OFERTA.get(c.get("offer_type"), c.get("offer_type"))}'
               if abordado else "Nao abordar (risco abaixo do corte de 60%)")
    return f"""
    <div class="cenario">
      <div class="ctitulo">{esc(c['nome'])}
        <span class="cmeta">{esc(c['user_id'])} &middot; evento
          "{esc(c['evento'])}" &middot; MRR {brl(c['props'].get('mrr'))}</span></div>
      <div class="kpis">
        <div class="kpi"><div class="kl">Risco calculado</div>
          <div class="kv">{pct}</div></div>
        <div class="kpi"><div class="kl">Criticidade</div>
          <div class="kv" style="color:{COR_CRIT.get(crit, '#f5ece1')}">
            {ROTULO_CRIT.get(crit, esc(crit))}</div></div>
        <div class="kpi"><div class="kl">Perfil</div>
          <div class="kv">{esc(c.get('profile'))}</div></div>
        <div class="kpi"><div class="kl">Decisao do agente</div>
          <div class="kv" style="font-size:16px">{esc(decisao)}</div></div>
      </div>
      {f'<div class="sub">Canal escolhido</div><div class="racio">{esc(c.get("channel"))}</div>'
       if c.get("channel") else ''}
      {f'<div class="sub">Mensagem gerada</div><div class="msg">{esc(c.get("message"))}</div>'
       if c.get("message") else ''}
    </div>"""


def linha_ranking(i, c):
    crit = c["criticality"]
    largura = 0 if c["risk_score"] is None else round(c["risk_score"] * 100)
    pct = "--" if c["risk_score"] is None else f"{largura}<span class='pc'>%</span>"
    return f"""<tr><td class="pos">{i}</td>
      <td><div class="cid">{esc(c['customer_id_externo'])}</div>
        <div class="cmeta2">{esc(c.get('billing_profile'))} &middot; origem
          {esc(c.get('origem'))}</div></td>
      <td class="num">{brl(c.get('mrr'))}</td>
      <td><div class="riscowrap"><div class="track">
        <div class="fill" style="width:{largura}%"></div></div>
        <span class="rv">{pct}</span></div></td>
      <td><span class="badge" style="--c:{COR_CRIT[crit]}"><span class="dot"></span>
        {ROTULO_CRIT[crit]}</span></td>
      <td class="exp">{esc(c['explicacao'])}</td></tr>"""


def monta_html(involuntario, voluntario, relatorio_import, ranking, ambiente):
    criticos = [c for c in ranking if c["criticality"] == "critico"]
    sem_dado = [c for c in ranking if c["criticality"] == "dado_insuficiente"]
    mrr_risco = sum(c.get("mrr") or 0 for c in ranking
                    if c["criticality"] in ("critico", "alto"))
    abordados = sum(1 for c in voluntario if c.get("offer_type"))
    return f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CRAI -- Relatorio de execucao</title><style>
:root{{--bg:#1a120a;--surface:#241a10;--surface2:#2e2215;--line:#3d2d1c;
--ink:#f5ece1;--ink2:#c9b9a6;--ink3:#94836f;--accent:#ef9311;--accent2:#ffb86c}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,
BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}}
.wrap{{max-width:1120px;margin:0 auto;padding:36px 26px 70px}}
header{{display:flex;align-items:baseline;gap:13px;flex-wrap:wrap;
padding-bottom:18px;border-bottom:1px solid var(--line)}}
.logo{{font-size:26px;font-weight:700;letter-spacing:-.02em;color:var(--accent2)}}
.sub0{{color:var(--ink2)}}
.quando{{margin-left:auto;font-size:12.5px;color:var(--ink3);
font-family:ui-monospace,Menlo,monospace}}
.amb{{background:var(--surface);border:1px solid var(--line);border-radius:10px;
padding:13px 16px;margin:18px 0 26px;font-size:13px;color:var(--ink2);
font-family:ui-monospace,Menlo,monospace;line-height:1.8}}
h2{{font-size:19px;font-weight:650;margin:34px 0 3px}}
.h2s{{color:var(--ink3);font-size:13.5px;margin-bottom:16px;max-width:700px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));
gap:12px;margin-bottom:18px}}
.tile{{background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:16px 18px}}
.tile .n{{font-size:30px;font-weight:700;letter-spacing:-.03em;line-height:1.1}}
.tile .l{{font-size:12.5px;color:var(--ink2);margin-top:4px}}
.cenario{{background:var(--surface);border:1px solid var(--line);
border-radius:12px;padding:20px 22px;margin-bottom:14px}}
.ctitulo{{font-size:16px;font-weight:650;margin-bottom:14px}}
.cmeta{{display:block;font-size:12.5px;color:var(--ink3);font-weight:400;
margin-top:3px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));
gap:11px;margin-bottom:6px}}
.kpi{{background:var(--bg);border:1px solid var(--line);border-radius:9px;
padding:11px 13px}}
.kl{{font-size:11px;color:var(--ink3);text-transform:uppercase;
letter-spacing:.05em}}
.kv{{font-size:19px;font-weight:650;margin-top:4px}}
.ku{{font-size:12px;color:var(--ink3);font-weight:400}}
.sub{{font-size:11.5px;color:var(--ink3);text-transform:uppercase;
letter-spacing:.05em;margin:16px 0 7px}}
.lista{{margin:0;padding-left:18px;color:var(--ink2);font-size:13.5px;
line-height:1.75}}
.racio{{background:var(--bg);border:1px solid var(--line);border-radius:9px;
padding:12px 14px;color:var(--ink2);font-size:13.5px;line-height:1.65}}
.rlinha{{padding:3px 0}}
.rlinha+.rlinha{{border-top:1px solid rgba(61,45,28,.5);margin-top:5px;
padding-top:8px}}
.shapwrap{{background:var(--bg);border:1px solid var(--line);border-radius:9px;
padding:12px 14px}}
.shaprow{{display:flex;align-items:center;gap:12px;padding:5px 0}}
.shaplabel{{flex:0 0 40%;font-size:13px;color:var(--ink2);text-align:right}}
.shapbar{{flex:1;position:relative;height:12px;min-width:110px}}
.shapeixo{{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;
background:var(--ink3);opacity:.55}}
.shapfill{{position:absolute;top:2px;height:8px;border-radius:3px}}
.shappct{{flex:0 0 58px;font-size:13px;font-weight:600;
font-variant-numeric:tabular-nums}}
.msg{{background:var(--surface2);border-left:3px solid var(--accent);
padding:13px 15px;border-radius:0 9px 9px 0;font-size:14px;line-height:1.6;
white-space:pre-wrap}}
.tablewrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px;
background:var(--surface)}}
table{{width:100%;border-collapse:collapse;min-width:860px}}
th{{text-align:left;font-size:11.5px;font-weight:600;color:var(--ink3);
text-transform:uppercase;letter-spacing:.05em;padding:12px 15px;
border-bottom:1px solid var(--line);white-space:nowrap}}
td{{padding:13px 15px;border-bottom:1px solid rgba(61,45,28,.55)}}
tr:last-child td{{border-bottom:none}}
.pos{{color:var(--ink3);font-size:13px;width:38px;
font-family:ui-monospace,Menlo,monospace}}
.cid{{font-weight:600;font-size:14px}}
.cmeta2{{font-size:12px;color:var(--ink3);margin-top:2px}}
.num{{font-variant-numeric:tabular-nums;white-space:nowrap;font-size:14px}}
.riscowrap{{display:flex;align-items:center;gap:9px;width:165px}}
.track{{flex:1;height:7px;background:rgba(245,236,225,.10);border-radius:4px;
overflow:hidden;min-width:70px}}
.fill{{height:100%;border-radius:4px;background:var(--accent2)}}
.rv{{font-variant-numeric:tabular-nums;font-size:14px;font-weight:600;
min-width:34px;text-align:right}}
.pc{{font-size:11px;color:var(--ink3);font-weight:400}}
.badge{{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;
font-weight:600;padding:4px 10px 4px 8px;border-radius:20px;
background:color-mix(in srgb,var(--c) 15%,transparent);
border:1px solid color-mix(in srgb,var(--c) 38%,transparent);color:var(--c);
white-space:nowrap}}
.dot{{width:6px;height:6px;border-radius:50%;background:var(--c);flex:none}}
.exp{{color:var(--ink2);font-size:13px;max-width:370px}}
.rodape{{margin-top:30px;padding:18px 20px;background:var(--surface);
border:1px solid var(--line);border-radius:12px;color:var(--ink2);
font-size:13px;line-height:1.7}}
.rodape b{{color:var(--ink)}}
code{{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;
color:var(--accent2)}}
</style></head><body><div class="wrap">

<header><span class="logo">CRAI</span>
  <span class="sub0">Relatorio de execucao</span>
  <span class="quando">{ambiente['quando']}</span></header>

<div class="amb">{ambiente['linhas']}</div>

<h2>1. Churn involuntario -- falha de cobranca no Pix Automatico</h2>
<div class="h2s">Para cada cobranca recusada, o agente diagnostica a causa,
  calcula o e-Profit da intervencao e decide se vale insistir. A decomposicao
  SHAP mostra o peso de cada variavel no score.</div>
{"".join(bloco_involuntario(c) for c in involuntario)}

<h2>2. Churn voluntario -- o cliente sinaliza saida</h2>
<div class="h2s">O evento comportamental chega, o agente calcula o risco e o
  Thompson Sampling escolhe a oferta. Dos {len(voluntario)} cenarios,
  {abordados} foram abordados: abaixo do corte de 60% o sistema decide nao
  gastar a intervencao.</div>
{"".join(bloco_voluntario(c) for c in voluntario)}

<h2>3. Onboarding self-service -- a empresa anexa a base que ja tem</h2>
<div class="h2s">Caminho para quem nao tem tracking comportamental
  instrumentado: {relatorio_import['importados']} clientes importados da
  planilha, {len(relatorio_import['rejeitados'])} rejeitados,
  {relatorio_import['linhas_sem_dado_comportamental']} sem sinal de atividade.
  <br><br>O ranking abaixo tem <b>{len(ranking)}</b> linhas, e nao
  {relatorio_import['importados']}: os {len(ranking) - relatorio_import['importados']}
  clientes que passaram pelo agente na secao 2 entraram junto, com
  <code>origem: sdk</code>. E a fusao das duas fontes -- planilha e SDK --
  numa lista so, por cliente, com o dado mais recente vencendo. Nao foi
  configurado nada para isso acontecer: as duas secoes acima rodaram no mesmo
  tenant, e o sistema juntou.</div>
<div class="tiles">
  <div class="tile"><div class="n">{len(ranking)}</div>
    <div class="l">clientes analisados</div></div>
  <div class="tile"><div class="n" style="color:{COR_CRIT['critico']}">{len(criticos)}</div>
    <div class="l">em risco critico</div></div>
  <div class="tile"><div class="n">{brl(mrr_risco)}</div>
    <div class="l">MRR em risco</div></div>
  <div class="tile"><div class="n" style="color:{COR_CRIT['dado_insuficiente']}">{len(sem_dado)}</div>
    <div class="l">sem dado suficiente</div></div>
</div>
<div class="tablewrap"><table><thead><tr><th>#</th><th>Cliente</th><th>MRR</th>
  <th>Risco</th><th>Criticidade</th><th>Por que</th></tr></thead>
  <tbody>{"".join(linha_ranking(i, c) for i, c in enumerate(ranking, 1))}</tbody>
</table></div>

<div class="rodape">
  <b>Como este relatorio foi produzido.</b> Cada numero acima e o estado final
  que o sistema devolveu, capturado de <code>crai_agent.ainvoke()</code>,
  <code>agente_do_modo().ainvoke()</code> e
  <code>insights_unificados.clientes_em_risco()</code>. Os cenarios sao as
  ENTRADAS; as causas, scores, e-Profit, ofertas, canais e mensagens sao
  SAIDAS calculadas na execucao que gerou esta pagina. Reproduza com
  <code>python -m crai.scripts.relatorio</code>.
  <br><br>
  <b>O que esta execucao nao prova.</b> Os modelos foram treinados em dados
  sinteticos com distribuicoes do mercado brasileiro, entao os numeros medem o
  comportamento do sistema, nao a performance dele sobre clientes reais.
  {ambiente['ressalvas']}
</div>

</div></body></html>"""


def descreve_ambiente():
    """O que estava ligado na hora da execucao. Vai no relatorio para que ele
    nunca afirme mais do que a execucao entregou."""
    modelos_ok = FailureClassifier().load()
    llm = bool(os.getenv("ANTHROPIC_API_KEY"))
    destino = "Postgres (Supabase)" if os.getenv("SUPABASE_DB_URL") else "SQLite local"
    linhas = "<br>".join([
        f"executado em: {platform.system()} {platform.release()} &middot; "
        f"Python {platform.python_version()}",
        f"modelos de ML: {'treinados, carregados de crai/models/' if modelos_ok else 'AUSENTES -- rodando na heuristica de fallback'}",
        f"geracao de mensagem: {'Claude API' if llm else 'template de fallback (sem ANTHROPIC_API_KEY)'}",
        f"base de clientes importada: {destino}",
    ])
    ressalvas = []
    if not modelos_ok:
        ressalvas.append("Os modelos nao estavam treinados nesta execucao: os "
                         "scores vieram da heuristica de fallback, e nao ha "
                         "decomposicao SHAP. Rode "
                         "<code>python -m crai.scripts.train_all</code> antes.")
    if not llm:
        ressalvas.append("As mensagens sairam do template de fallback, nao da "
                         "LLM: <code>ANTHROPIC_API_KEY</code> nao estava "
                         "configurada.")
    return {
        "quando": datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M"),
        "linhas": linhas,
        "ressalvas": " ".join(ressalvas),
    }


async def principal():
    print("")
    print("  CRAI -- relatorio de execucao")
    print("  " + "-" * 46)

    ambiente = descreve_ambiente()

    print("  [1/3] churn involuntario (Pix Automatico)...")
    involuntario = []
    for nome, id_rec, valor, codigo, tent in CENARIOS_INVOLUNTARIO:
        involuntario.append(await roda_involuntario(nome, id_rec, valor, codigo, tent))

    print("  [2/3] churn voluntario (evento comportamental)...")
    voluntario = []
    for nome, uid, evento, props in CENARIOS_VOLUNTARIO:
        voluntario.append(await roda_voluntario(nome, uid, evento, props))

    print("  [3/3] onboarding self-service (base anexada)...")
    relatorio_import, ranking = roda_self_service()

    SAIDA.write_text(
        monta_html(involuntario, voluntario, relatorio_import, ranking, ambiente),
        encoding="utf-8")

    abordados = sum(1 for c in voluntario if c.get("offer_type"))
    criticos = sum(1 for c in ranking if c["criticality"] == "critico")
    print("")
    print(f"  {len(involuntario)} cobrancas recuperadas analisadas")
    print(f"  {len(voluntario)} eventos de risco processados ({abordados} abordados)")
    print(f"  {len(ranking)} clientes pontuados na base importada ({criticos} criticos)")
    print("")
    print(f"  Relatorio: {SAIDA}")
    print("  Abra esse arquivo no navegador.")
    print("")


def main():
    asyncio.run(principal())


if __name__ == "__main__":
    sys.exit(main())

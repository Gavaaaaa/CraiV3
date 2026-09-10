"""demo_server.py — demonstração INTERATIVA da CRAI, para apresentar ao vivo.

Sobe um servidor local com uma página onde cada botão dispara o sistema de
verdade e mostra o resultado na hora. Nada é maquete: cada clique chama os
mesmos módulos que a API de produção chama.

    Painel 1 — Base de clientes (caminho self-service / upload)
        `churn_voluntary.importacao.importar()`          -> Sprint 2
        `churn_voluntary.insights_unificados`            -> Sprints 3 e 4

    Painel 2 — Evento comportamental (caminho SDK, tempo real)
        `churn_voluntary.voluntary_agent` (grafo LangGraph) -> risco, oferta
        escolhida pelo Thompson Sampling, canal e mensagem gerada.

POR QUE UM SERVIDOR SEPARADO DA API. As rotas reais (`/clientes/importar`,
`/insights`) exigem um JWT do Supabase, e o projeto Supabase pode ainda não
existir. A autenticação é camada de BORDA (`accounts/auth.py`): tirá-la do
caminho não muda o motor de análise, que é o que a demonstração mostra. A
página é servida pelo próprio servidor, então não há CORS no meio.

COMO RODAR (a partir da pasta `crai/`):

    python demo/demo_server.py

Depois abra http://localhost:8100 no navegador. Ctrl+C encerra.
"""

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
TENANT = "demo_banca"

# SQLite local em vez do Postgres do Supabase. `clientes_importados._destino()`
# lê estas envs a cada chamada e falha alto se nenhuma existir — de propósito.
os.environ["CRAI_CLIENTES_DB"] = str(BASE / "demo_clientes.db")
os.environ.pop("SUPABASE_DB_URL", None)
sys.path.insert(0, str(BASE.parent / "crai"))   # pasta que CONTÉM o pacote crai/

from fastapi import FastAPI, UploadFile, File                      # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse           # noqa: E402
from pydantic import BaseModel                                     # noqa: E402

from crai.churn_voluntary import importacao, insights_unificados   # noqa: E402
from crai.churn_voluntary.state import ChurnVoluntaryState         # noqa: E402
from crai.churn_voluntary.voluntary_agent import agente_do_modo    # noqa: E402

app = FastAPI(title="CRAI — demonstração")

BASE_EXEMPLO = """customer_id_externo,mrr,billing_profile,days_since_last,features_used_30d,email
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

OFFER_LABEL = {
    "desconto_10": "Desconto de 10%",
    "desconto_20": "Desconto de 20%",
    "pausa_1_mes": "Pausa de 1 mês",
    "pix_boleto_flash": "Pix / Boleto Flash",
}


# -- Painel 1: base de clientes -----------------------------------------------
@app.post("/demo/importar")
async def demo_importar(arquivo: UploadFile = File(None)):
    """Importa a planilha (a enviada, ou a de exemplo). Código real do Sprint 2."""
    if arquivo is not None and arquivo.filename:
        nome, conteudo = arquivo.filename, await arquivo.read()
    else:
        nome, conteudo = "base_exemplo.csv", BASE_EXEMPLO.encode("utf-8")
    try:
        relatorio = importacao.importar(TENANT, nome, conteudo)
    except importacao.ArquivoInvalido as e:
        return JSONResponse({"erro": str(e)}, status_code=422)
    return JSONResponse({"arquivo": nome, **relatorio})


@app.get("/demo/insights")
async def demo_insights():
    """O ranking de risco. Mesmo código que `GET /insights` chama."""
    ranking = insights_unificados.clientes_em_risco(TENANT)
    return JSONResponse({"clientes": ranking, "total": len(ranking)})


# -- Painel 2: evento comportamental ao vivo ----------------------------------
class Evento(BaseModel):
    user_id: str = "cliente_demo"
    event: str = "Cancellation Page Viewed"
    days_since_last: float = 21
    features_used_30d: float = 2
    mrr: float = 1200
    billing_profile: str = "CLT"
    on_site_now: bool = True


@app.post("/demo/evento")
async def demo_evento(ev: Evento):
    """Roda o grafo LangGraph do churn voluntário e devolve a decisão final.

    É o caminho (1), do SDK: o mesmo pipeline que `/webhooks/segment` dispara.
    O estado final traz o risco calculado, a criticidade, a oferta que o
    Thompson Sampling escolheu, o canal e a mensagem gerada.
    """
    inicial: ChurnVoluntaryState = {
        "tenant_id": TENANT, "user_id": f"user:{ev.user_id}", "event": ev.event,
        "props": {
            "days_since_last": ev.days_since_last,
            "features_used_30d": ev.features_used_30d,
            "mrr": ev.mrr, "billing_profile": ev.billing_profile,
            "on_site_now": ev.on_site_now,
        },
        "risk_score": 0.0, "profile": "CLT", "criticality": "padrao",
        "offer_type": None, "channel": None, "on_site_now": ev.on_site_now,
        "prior_channel_success": None, "message": None, "offer_sent": False,
        "accepted": None, "retained": False, "is_critical": False,
    }
    config = {"configurable": {"thread_id": f"{TENANT}:{ev.user_id}"}}
    final = await agente_do_modo().ainvoke(inicial, config)

    return JSONResponse({
        "risk_score": final.get("risk_score"),
        "criticality": final.get("criticality"),
        "profile": final.get("profile"),
        "offer_type": final.get("offer_type"),
        "offer_label": OFFER_LABEL.get(final.get("offer_type") or "", "—"),
        "channel": final.get("channel"),
        "message": final.get("message"),
        "offer_sent": final.get("offer_sent"),
        "abordado": final.get("offer_type") is not None,
    })


@app.get("/", response_class=HTMLResponse)
async def pagina():
    return PAGINA


PAGINA = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CRAI — demonstração ao vivo</title>
<style>
  :root{--bg:#1a120a;--surface:#241a10;--surface2:#2e2215;--line:#3d2d1c;
    --ink:#f5ece1;--ink2:#c9b9a6;--ink3:#94836f;--accent:#ef9311;--accent2:#ffb86c;
    --critico:#f87171;--alto:#ffb86c;--padrao:#5fd39b;--semdado:#9aa0a6;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased}
  .wrap{max-width:1180px;margin:0 auto;padding:34px 26px 70px}
  header{display:flex;align-items:baseline;gap:13px;flex-wrap:wrap;
    padding-bottom:18px;border-bottom:1px solid var(--line)}
  .logo{font-size:25px;font-weight:700;letter-spacing:-.02em;color:var(--accent2)}
  .sub{color:var(--ink2)}
  .tenant{margin-left:auto;font-size:12.5px;color:var(--ink3);
    font-family:ui-monospace,Menlo,monospace}
  .tabs{display:flex;gap:8px;margin:22px 0 20px}
  .tab{background:var(--surface);border:1px solid var(--line);color:var(--ink2);
    padding:9px 16px;border-radius:9px;cursor:pointer;font-size:14px;
    font-weight:550;font-family:inherit}
  .tab.on{background:var(--accent);border-color:var(--accent);color:#1a120a}
  .painel{display:none} .painel.on{display:block}
  h2{font-size:17px;font-weight:650;margin:0 0 4px}
  .h2sub{color:var(--ink3);font-size:13px;margin-bottom:16px;max-width:640px}
  .card{background:var(--surface);border:1px solid var(--line);
    border-radius:12px;padding:20px;margin-bottom:16px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  button.go{background:var(--accent);color:#1a120a;border:none;padding:11px 20px;
    border-radius:9px;font-size:14.5px;font-weight:650;cursor:pointer;
    font-family:inherit}
  button.go:hover{background:var(--accent2)}
  button.go:disabled{opacity:.5;cursor:default}
  button.ghost{background:transparent;color:var(--ink2);border:1px solid var(--line);
    padding:11px 18px;border-radius:9px;font-size:14px;cursor:pointer;
    font-family:inherit}
  input[type=file]{color:var(--ink3);font-size:13px;font-family:inherit}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
  label{display:block;font-size:12px;color:var(--ink3);margin-bottom:5px}
  input,select{width:100%;background:var(--bg);border:1px solid var(--line);
    color:var(--ink);padding:9px 11px;border-radius:8px;font-size:14px;
    font-family:inherit}
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));
    gap:12px;margin-bottom:16px}
  .tile{background:var(--surface);border:1px solid var(--line);
    border-radius:12px;padding:16px 18px}
  .tile .n{font-size:31px;font-weight:700;letter-spacing:-.03em;line-height:1.1}
  .tile .l{font-size:12.5px;color:var(--ink2);margin-top:4px}
  .tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;
    background:var(--surface)}
  table{width:100%;border-collapse:collapse;min-width:880px}
  th{text-align:left;font-size:11.5px;font-weight:600;color:var(--ink3);
    text-transform:uppercase;letter-spacing:.05em;padding:12px 15px;
    border-bottom:1px solid var(--line);white-space:nowrap}
  td{padding:13px 15px;border-bottom:1px solid rgba(61,45,28,.55)}
  tr:last-child td{border-bottom:none}
  tr:hover td{background:var(--surface2)}
  .pos{color:var(--ink3);font-size:13px;width:40px;font-family:ui-monospace,Menlo,monospace}
  .cid{font-weight:600;font-size:14px}
  .meta{font-size:12px;color:var(--ink3);margin-top:2px}
  .num{font-variant-numeric:tabular-nums;white-space:nowrap;font-size:14px}
  .riscowrap{display:flex;align-items:center;gap:9px;width:175px}
  .track{flex:1;height:7px;background:rgba(245,236,225,.10);border-radius:4px;
    overflow:hidden;min-width:70px}
  /* Barra = MAGNITUDE, uma cor só. Criticidade é STATUS e vive no selo, com
     rótulo — nunca cor sozinha. */
  .fill{height:100%;border-radius:4px;background:var(--accent2);
    transition:width .5s cubic-bezier(.4,0,.2,1)}
  .rv{font-variant-numeric:tabular-nums;font-size:14px;font-weight:600;
    min-width:34px;text-align:right}
  .pc{font-size:11px;color:var(--ink3);font-weight:400}
  .badge{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;
    font-weight:600;padding:4px 10px 4px 8px;border-radius:20px;
    background:color-mix(in srgb,var(--c) 15%,transparent);
    border:1px solid color-mix(in srgb,var(--c) 38%,transparent);
    color:var(--c);white-space:nowrap}
  .dot{width:6px;height:6px;border-radius:50%;background:var(--c);flex:none}
  .exp{color:var(--ink2);font-size:13px;max-width:380px}
  .log{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;
    color:var(--ink2);background:var(--bg);border:1px solid var(--line);
    border-radius:9px;padding:12px 14px;margin-top:12px;white-space:pre-wrap}
  .passos{display:grid;gap:10px;margin-top:6px}
  .passo{display:flex;gap:13px;align-items:flex-start;padding:13px 15px;
    background:var(--bg);border:1px solid var(--line);border-radius:10px;
    opacity:0;transform:translateY(6px);
    transition:opacity .35s,transform .35s}
  .passo.on{opacity:1;transform:none}
  .passo .k{font-size:11.5px;color:var(--ink3);text-transform:uppercase;
    letter-spacing:.05em;min-width:112px;padding-top:2px}
  .passo .v{font-size:14.5px;font-weight:600}
  .passo .v small{display:block;font-weight:400;font-size:12.5px;
    color:var(--ink3);margin-top:3px}
  .msg{background:var(--surface2);border-left:3px solid var(--accent);
    padding:14px 16px;border-radius:0 9px 9px 0;font-size:14px;
    line-height:1.6;white-space:pre-wrap}
  .nota{color:var(--ink3);font-size:12.5px;margin-top:14px;line-height:1.6}
  code{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:var(--accent2)}
</style></head><body><div class="wrap">

<header><span class="logo">CRAI</span>
  <span class="sub">demonstração ao vivo</span>
  <span class="tenant">tenant: demo_banca</span></header>

<div class="tabs">
  <button class="tab on" onclick="aba(0)">1 · Base de clientes anexada</button>
  <button class="tab" onclick="aba(1)">2 · Evento comportamental (SDK)</button>
</div>

<!-- ------------- Painel 1 ------------- -->
<div class="painel on" id="p0">
  <h2>A empresa sobe a base que já tem, e a IA analisa</h2>
  <div class="h2sub">Para quem não tem tracking comportamental instrumentado.
    Cada clique executa <code>importacao.importar()</code> e
    <code>insights_unificados.clientes_em_risco()</code> de verdade.</div>

  <div class="card"><div class="row">
    <button class="go" id="bImp" onclick="importar()">Importar base de clientes</button>
    <input type="file" id="arq" accept=".csv,.xlsx">
    <span style="color:var(--ink3);font-size:12.5px">
      sem arquivo, usa a base de exemplo (14 clientes)</span>
  </div><div id="logImp"></div></div>

  <div class="card" id="cardAna" style="display:none">
    <button class="go" id="bAna" onclick="analisar()">Analisar risco de churn</button>
  </div>

  <div id="res"></div>
</div>

<!-- ------------- Painel 2 ------------- -->
<div class="painel" id="p1">
  <h2>Um cliente age agora, e o agente decide sozinho</h2>
  <div class="h2sub">O caminho do SDK: o evento chega, o agente LangGraph
    calcula o risco, o Thompson Sampling escolhe a oferta, o sistema escolhe o
    canal e escreve a mensagem. Sem humano no meio.</div>

  <div class="card">
    <div class="grid">
      <div><label>Evento</label><select id="ev">
        <option>Cancellation Page Viewed</option>
        <option>Downgrade Clicked</option>
        <option>Session Started</option></select></div>
      <div><label>Dias sem login</label><input id="dias" type="number" value="21"></div>
      <div><label>Funcionalidades (30d)</label><input id="feat" type="number" value="2"></div>
      <div><label>MRR (R$)</label><input id="mrr" type="number" value="1200"></div>
      <div><label>Perfil</label><select id="perf">
        <option>CLT</option><option>PJ</option><option>freelancer</option></select></div>
    </div>
    <div class="row" style="margin-top:16px">
      <button class="go" id="bEv" onclick="rodarEvento()">Rodar o agente</button>
      <button class="ghost" onclick="preset(0)">Cenário: risco alto</button>
      <button class="ghost" onclick="preset(1)">Cenário: conta valiosa</button>
      <button class="ghost" onclick="preset(2)">Cenário: cliente saudável</button>
    </div>
  </div>

  <div id="resEv"></div>
</div>

<script>
const COR={critico:'var(--critico)',alto:'var(--alto)',padrao:'var(--padrao)',
  dado_insuficiente:'var(--semdado)'};
const ROT={critico:'Crítico',alto:'Alto',padrao:'Padrão',dado_insuficiente:'Sem dado'};
const brl=v=>v==null?'—':'R$ '+Math.round(v).toLocaleString('pt-BR');
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

function aba(i){document.querySelectorAll('.tab').forEach((t,j)=>t.classList.toggle('on',i===j));
  document.querySelectorAll('.painel').forEach((p,j)=>p.classList.toggle('on',i===j));}

async function importar(){
  const b=document.getElementById('bImp'); b.disabled=true; b.textContent='Importando…';
  const f=document.getElementById('arq').files[0];
  const fd=new FormData(); if(f) fd.append('arquivo',f);
  const r=await(await fetch('/demo/importar',{method:'POST',body:fd})).json();
  b.disabled=false; b.textContent='Importar base de clientes';
  document.getElementById('logImp').innerHTML=r.erro
    ? `<div class="log" style="color:var(--critico)">${esc(r.erro)}</div>`
    : `<div class="log">arquivo: ${esc(r.arquivo)}
importados: ${r.importados}   rejeitados: ${r.rejeitados.length}   sem dado comportamental: ${r.linhas_sem_dado_comportamental}${
  r.rejeitados.length?'\\n'+r.rejeitados.map(x=>`  linha ${x.linha}: ${esc(x.motivo)}`).join('\\n'):''}</div>`;
  if(!r.erro) document.getElementById('cardAna').style.display='block';
}

async function analisar(){
  const b=document.getElementById('bAna'); b.disabled=true; b.textContent='Analisando…';
  const r=await(await fetch('/demo/insights')).json();
  b.disabled=false; b.textContent='Analisar risco de churn';
  const cs=r.clientes, crit=cs.filter(c=>c.criticality==='critico'),
    sd=cs.filter(c=>c.criticality==='dado_insuficiente'),
    mrrR=cs.filter(c=>['critico','alto'].includes(c.criticality))
           .reduce((a,c)=>a+(c.mrr||0),0);
  document.getElementById('res').innerHTML=`
    <div class="tiles">
      <div class="tile"><div class="n">${cs.length}</div><div class="l">clientes na base</div></div>
      <div class="tile"><div class="n" style="color:var(--critico)">${crit.length}</div><div class="l">em risco crítico</div></div>
      <div class="tile"><div class="n">${brl(mrrR)}</div><div class="l">MRR em risco</div></div>
      <div class="tile"><div class="n" style="color:var(--semdado)">${sd.length}</div><div class="l">sem dado suficiente</div></div>
    </div>
    <div class="tablewrap"><table><thead><tr>
      <th>#</th><th>Cliente</th><th>MRR</th><th>Risco</th><th>Criticidade</th><th>Por quê</th>
    </tr></thead><tbody>${cs.map((c,i)=>{
      const w=c.risk_score==null?0:Math.round(c.risk_score*100);
      return `<tr><td class="pos">${i+1}</td>
        <td><div class="cid">${esc(c.customer_id_externo)}</div>
            <div class="meta">${esc(c.billing_profile||'—')} · origem ${esc(c.origem)}</div></td>
        <td class="num">${brl(c.mrr)}</td>
        <td><div class="riscowrap"><div class="track"><div class="fill" style="width:0%"
             data-w="${w}"></div></div><span class="rv">${
             c.risk_score==null?'—':w+'<span class="pc">%</span>'}</span></div></td>
        <td><span class="badge" style="--c:${COR[c.criticality]}">
            <span class="dot"></span>${ROT[c.criticality]}</span></td>
        <td class="exp">${esc(c.explicacao)}</td></tr>`}).join('')}
    </tbody></table></div>`;
  requestAnimationFrame(()=>document.querySelectorAll('.fill[data-w]')
    .forEach(f=>f.style.width=f.dataset.w+'%'));
}

function preset(i){
  const v=[[ 'Cancellation Page Viewed',34,1,900,'PJ'],
           [ 'Session Started',9,7,3400,'CLT'],
           [ 'Session Started',1,17,800,'CLT']][i];
  ev.value=v[0]; dias.value=v[1]; feat.value=v[2]; mrr.value=v[3]; perf.value=v[4];
}

async function rodarEvento(){
  const b=document.getElementById('bEv'); b.disabled=true; b.textContent='Rodando o agente…';
  const r=await(await fetch('/demo/evento',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({user_id:'cliente_demo_'+Date.now(),event:ev.value,
      days_since_last:+dias.value,features_used_30d:+feat.value,
      mrr:+mrr.value,billing_profile:perf.value,on_site_now:true})})).json();
  b.disabled=false; b.textContent='Rodar o agente';
  const pct=Math.round((r.risk_score||0)*100);
  const passos=[
    ['Risco calculado',`${pct}% <small>perfil ${esc(r.profile)} · criticidade ${
      ROT[r.criticality]||esc(r.criticality)}</small>`],
    r.abordado
      ? ['Oferta escolhida',`${esc(r.offer_label)} <small>Thompson Sampling, otimizando e-Profit</small>`]
      : ['Decisão',`Não abordar <small>risco abaixo do corte de 60% — intervir custaria mais do que retornaria</small>`],
  ];
  if(r.abordado) passos.push(['Canal',`${esc(r.channel)} <small>escolhido pelo histórico e pela criticidade</small>`]);
  document.getElementById('resEv').innerHTML=`
    <div class="card"><div class="passos">${passos.map(([k,v])=>
      `<div class="passo"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('')}
    </div>${r.message?`<div style="margin-top:14px">
      <div class="k" style="font-size:11.5px;color:var(--ink3);text-transform:uppercase;
        letter-spacing:.05em;margin-bottom:8px">Mensagem gerada</div>
      <div class="msg">${esc(r.message)}</div></div>`:''}
    <div class="nota">Saída do grafo <code>voluntary_agent</code>. A mensagem sai da
      Claude API quando <code>ANTHROPIC_API_KEY</code> está configurada; sem ela, do
      template de fallback. O desfecho não é sorteado: em modo produção o ciclo fica
      aberto esperando o retorno real.</div></div>`;
  document.querySelectorAll('.passo').forEach((p,i)=>setTimeout(()=>p.classList.add('on'),i*260));
}
</script></div></body></html>"""


if __name__ == "__main__":
    import uvicorn
    print("\n  CRAI — demonstração ao vivo")
    print("  Abra:  http://localhost:8100\n")
    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="warning")

"""crai/api/painel.py -- console de avaliacao da API, servido em `GET /painel`.

O QUE E. Uma pagina que exercita a propria API: cada botao chama uma rota
`/simulate/painel/*` do `app.py`, que por sua vez roda os grafos e os modulos
reais e devolve o ESTADO FINAL. Nao ha logica de negocio aqui -- este arquivo
e so a camada de apresentacao, e todo numero exibido veio da resposta HTTP.

PARA QUE SERVE. O `/docs` (Swagger) ja permite chamar as rotas, mas devolve
JSON cru e nao mostra o raciocinio do agente de forma legivel. Para quem
precisa AVALIAR o sistema -- uma banca, um professor, alguem decidindo uma
integracao -- este painel torna a mesma execucao inspecionavel sem ler JSON.

RESTRICAO DE AMBIENTE. Tanto esta pagina quanto as rotas que ela chama passam
por `_require_simulation_env()`: so respondem com `ENV=development` ou
`ENV=demo`. Em producao devolvem 403, como todo o resto de `/simulate/*`.

MESMA ORIGEM. A pagina e servida pela propria API, entao as chamadas saem para
o mesmo host e nao existe CORS no caminho -- e por isso que ela mora aqui, e
nao num arquivo HTML solto que o avaliador abriria do disco.
"""

PAGINA_PAINEL = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CRAI -- painel de avaliacao</title><style>
:root{--bg:#1a120a;--surface:#241a10;--surface2:#2e2215;--line:#3d2d1c;
--ink:#f5ece1;--ink2:#c9b9a6;--ink3:#94836f;--accent:#ef9311;--accent2:#ffb86c;
--critico:#f87171;--alto:#ffb86c;--padrao:#5fd39b;--semdado:#9aa0a6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,
BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:1140px;margin:0 auto;padding:32px 24px 70px}
header{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
padding-bottom:16px;border-bottom:1px solid var(--line)}
.logo{font-size:25px;font-weight:700;letter-spacing:-.02em;color:var(--accent2)}
.sub{color:var(--ink2)}
.badge-env{margin-left:auto;font-size:12px;color:var(--ink3);
font-family:ui-monospace,Menlo,monospace}
.aviso{background:var(--surface);border:1px solid var(--line);border-radius:10px;
padding:12px 15px;margin:16px 0 20px;font-size:13px;color:var(--ink2);
line-height:1.65}
.aviso b{color:var(--ink)}
.tabs{display:flex;gap:8px;margin:0 0 20px;flex-wrap:wrap}
.tab{background:var(--surface);border:1px solid var(--line);color:var(--ink2);
padding:9px 15px;border-radius:9px;cursor:pointer;font-size:13.5px;
font-weight:550;font-family:inherit}
.tab.on{background:var(--accent);border-color:var(--accent);color:#1a120a}
.painel{display:none}.painel.on{display:block}
h2{font-size:17px;font-weight:650;margin:0 0 3px}
.h2s{color:var(--ink3);font-size:13px;margin-bottom:15px;max-width:680px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:18px 20px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:11px}
label{display:block;font-size:11.5px;color:var(--ink3);margin-bottom:5px}
input,select{width:100%;background:var(--bg);border:1px solid var(--line);
color:var(--ink);padding:9px 11px;border-radius:8px;font-size:14px;
font-family:inherit}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:15px}
button.go{background:var(--accent);color:#1a120a;border:none;padding:11px 19px;
border-radius:9px;font-size:14px;font-weight:650;cursor:pointer;font-family:inherit}
button.go:hover{background:var(--accent2)}
button.go:disabled{opacity:.5;cursor:default}
button.ghost{background:transparent;color:var(--ink2);border:1px solid var(--line);
padding:10px 16px;border-radius:9px;font-size:13.5px;cursor:pointer;
font-family:inherit}
input[type=file]{color:var(--ink3);font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
gap:10px;margin-bottom:4px}
.kpi{background:var(--bg);border:1px solid var(--line);border-radius:9px;
padding:11px 13px}
.kl{font-size:10.5px;color:var(--ink3);text-transform:uppercase;letter-spacing:.05em}
.kv{font-size:18px;font-weight:650;margin-top:4px}
.ku{font-size:12px;color:var(--ink3);font-weight:400}
.sub2{font-size:11px;color:var(--ink3);text-transform:uppercase;
letter-spacing:.05em;margin:15px 0 7px}
.shapwrap,.racio{background:var(--bg);border:1px solid var(--line);
border-radius:9px;padding:11px 13px}
.racio{color:var(--ink2);font-size:13.5px;line-height:1.6}
.rlinha{padding:3px 0}
.rlinha+.rlinha{border-top:1px solid rgba(61,45,28,.5);margin-top:5px;padding-top:8px}
.shaprow{display:flex;align-items:center;gap:11px;padding:4px 0}
.shaplabel{flex:0 0 40%;font-size:12.5px;color:var(--ink2);text-align:right}
.shapbar{flex:1;position:relative;height:12px;min-width:100px}
.shapeixo{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;
background:var(--ink3);opacity:.55}
.shapfill{position:absolute;top:2px;height:8px;border-radius:3px}
.shappct{flex:0 0 55px;font-size:12.5px;font-weight:600;
font-variant-numeric:tabular-nums}
.lista{margin:0;padding-left:18px;color:var(--ink2);font-size:13px;line-height:1.75}
.msg{background:var(--surface2);border-left:3px solid var(--accent);
padding:12px 14px;border-radius:0 9px 9px 0;font-size:14px;line-height:1.6;
white-space:pre-wrap}
.log{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:var(--ink2);
background:var(--bg);border:1px solid var(--line);border-radius:9px;
padding:11px 13px;margin-top:11px;white-space:pre-wrap}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
gap:11px;margin-bottom:14px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:12px;
padding:15px 17px}
.tile .n{font-size:28px;font-weight:700;letter-spacing:-.03em;line-height:1.1}
.tile .l{font-size:12px;color:var(--ink2);margin-top:4px}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;
background:var(--surface)}
table{width:100%;border-collapse:collapse;min-width:840px}
th{text-align:left;font-size:11px;font-weight:600;color:var(--ink3);
text-transform:uppercase;letter-spacing:.05em;padding:11px 14px;
border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:12px 14px;border-bottom:1px solid rgba(61,45,28,.55)}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--surface2)}
.pos{color:var(--ink3);font-size:12.5px;width:36px;
font-family:ui-monospace,Menlo,monospace}
.cid{font-weight:600;font-size:13.5px}
.cmeta{font-size:11.5px;color:var(--ink3);margin-top:2px}
.num{font-variant-numeric:tabular-nums;white-space:nowrap;font-size:13.5px}
.riscowrap{display:flex;align-items:center;gap:9px;width:160px}
.track{flex:1;height:7px;background:rgba(245,236,225,.10);border-radius:4px;
overflow:hidden;min-width:66px}
.fill{height:100%;border-radius:4px;background:var(--accent2);
transition:width .5s cubic-bezier(.4,0,.2,1)}
.rv{font-variant-numeric:tabular-nums;font-size:13.5px;font-weight:600;
min-width:32px;text-align:right}
.pc{font-size:10.5px;color:var(--ink3);font-weight:400}
.selo{display:inline-flex;align-items:center;gap:6px;font-size:12px;
font-weight:600;padding:4px 10px 4px 8px;border-radius:20px;
background:color-mix(in srgb,var(--c) 15%,transparent);
border:1px solid color-mix(in srgb,var(--c) 38%,transparent);color:var(--c);
white-space:nowrap}
.dot{width:6px;height:6px;border-radius:50%;background:var(--c);flex:none}
.exp{color:var(--ink2);font-size:12.5px;max-width:350px}
code{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:var(--accent2)}
.rota{font-family:ui-monospace,Menlo,monospace;font-size:11.5px;color:var(--ink3);
margin-top:10px}
</style></head><body><div class="wrap">

<header><span class="logo">CRAI</span>
  <span class="sub">painel de avaliacao</span>
  <span class="badge-env" id="envinfo">carregando...</span></header>

<div class="aviso"><b>Cada botao desta pagina chama a API deste servidor.</b>
  Os numeros exibidos sao o estado final que os grafos e os modulos devolveram
  nesta execucao -- nada e pre-calculado. As rotas usadas aparecem embaixo de
  cada resultado, e podem ser conferidas em <code>/docs</code>.</div>

<div class="tabs">
  <button class="tab on" onclick="aba(0)">1 &middot; Cobranca falhou (Pix)</button>
  <button class="tab" onclick="aba(1)">2 &middot; Cliente em risco (SDK)</button>
  <button class="tab" onclick="aba(2)">3 &middot; Base anexada</button>
</div>

<!-- 1 -->
<div class="painel on" id="p0">
  <h2>Churn involuntario: a cobranca recorrente falhou</h2>
  <div class="h2s">O agente diagnostica a causa, calcula o e-Profit da
    intervencao e decide se vale insistir -- respeitando o limite do BACEN de
    3 tentativas em 7 dias.</div>
  <div class="card">
    <div class="grid">
      <div><label>Valor da cobranca (R$)</label>
        <input id="i_valor" type="number" value="299.90" step="0.01"></div>
      <div><label>Recusa do PSP</label><select id="i_codigo">
        <option value="AM04">AM04 -- saldo insuficiente</option>
        <option value="AM02">AM02 -- limite excedido</option>
        <option value="MD01">MD01 -- autorizacao revogada</option></select></div>
      <div><label>Tentativas ja usadas</label>
        <input id="i_tent" type="number" value="0" min="0" max="3"></div>
    </div>
    <div class="row"><button class="go" id="b_inv" onclick="rodarInvoluntario()">
      Processar cobranca</button></div>
  </div>
  <div id="r_inv"></div>
</div>

<!-- 2 -->
<div class="painel" id="p1">
  <h2>Churn voluntario: o cliente sinaliza saida</h2>
  <div class="h2s">O evento comportamental chega pelo SDK, o agente calcula o
    risco e o Thompson Sampling escolhe a oferta. Abaixo do corte de 60% o
    sistema decide nao gastar a intervencao.</div>
  <div class="card">
    <div class="grid">
      <div><label>Evento</label><select id="v_ev">
        <option>Cancellation Page Viewed</option>
        <option>Downgrade Clicked</option>
        <option>Session Started</option></select></div>
      <div><label>Dias sem login</label><input id="v_dias" type="number" value="21"></div>
      <div><label>Funcionalidades (30d)</label><input id="v_feat" type="number" value="2"></div>
      <div><label>MRR (R$)</label><input id="v_mrr" type="number" value="1200"></div>
      <div><label>Perfil</label><select id="v_perf">
        <option>CLT</option><option>PJ</option><option>freelancer</option></select></div>
    </div>
    <div class="row">
      <button class="go" id="b_vol" onclick="rodarVoluntario()">Rodar o agente</button>
      <button class="ghost" onclick="cen(0)">Risco alto</button>
      <button class="ghost" onclick="cen(1)">Conta valiosa</button>
      <button class="ghost" onclick="cen(2)">Cliente saudavel</button>
    </div>
  </div>
  <div id="r_vol"></div>
</div>

<!-- 3 -->
<div class="painel" id="p2">
  <h2>Onboarding self-service: a empresa anexa a base que ja tem</h2>
  <div class="h2s">Caminho para quem nao tem tracking instrumentado. Aceita
    CSV e XLSX; sem arquivo, usa uma base de exemplo de 14 clientes.</div>
  <div class="card">
    <div class="row" style="margin-top:0">
      <button class="go" id="b_imp" onclick="importar()">Importar base</button>
      <input type="file" id="arq" accept=".csv,.xlsx">
    </div>
    <div id="log_imp"></div>
  </div>
  <div class="card" id="card_ana" style="display:none">
    <button class="go" id="b_ana" onclick="analisar()">Analisar risco de churn</button>
  </div>
  <div id="r_base"></div>
</div>

<script>
const COR={critico:'var(--critico)',alto:'var(--alto)',padrao:'var(--padrao)',
  dado_insuficiente:'var(--semdado)'};
const ROT={critico:'Critico',alto:'Alto',padrao:'Padrao',dado_insuficiente:'Sem dado'};
const brl=v=>v==null?'--':'R$ '+Number(v).toLocaleString('pt-BR',
  {minimumFractionDigits:2,maximumFractionDigits:2});
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const rota=t=>`<div class="rota">rota: ${esc(t)}</div>`;

function aba(i){document.querySelectorAll('.tab').forEach((t,j)=>t.classList.toggle('on',i===j));
  document.querySelectorAll('.painel').forEach((p,j)=>p.classList.toggle('on',i===j));}

fetch('/simulate/painel/ambiente').then(r=>r.json()).then(a=>{
  document.getElementById('envinfo').textContent =
    `ENV=${a.env} | modelos: ${a.modelos?'treinados':'heuristica'} | LLM: ${a.llm?'Claude API':'fallback'}`;
}).catch(()=>{});

function shapHtml(l){
  if(!l||!l.length) return '<div class="racio">Modelos nao treinados nesta '+
    'execucao: sem decomposicao SHAP. Rode python -m crai.scripts.train_all.</div>';
  return '<div class="shapwrap">'+l.map(f=>{
    const p=f.direction==='+', cor=p?'var(--padrao)':'var(--critico)',
      w=Math.min(Math.abs(f.contribution_pct)*2,100)/2;
    return `<div class="shaprow"><div class="shaplabel">${esc(f.rotulo)}</div>
      <div class="shapbar"><div class="shapeixo"></div>
      <div class="shapfill" style="${p?'left:50%':'right:50%'};width:${w.toFixed(1)}%;
        background:${cor}"></div></div>
      <div class="shappct" style="color:${cor}">${p?'+':'-'}${Math.abs(f.contribution_pct).toFixed(1)}%</div>
      </div>`}).join('')+'</div>';
}

async function rodarInvoluntario(){
  const b=document.getElementById('b_inv'); b.disabled=true; b.textContent='Processando...';
  const r=await(await fetch('/simulate/painel/cobranca-falhada',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({valor:+i_valor.value,codigo_falha:i_codigo.value,
      tentativas_usadas:+i_tent.value})})).json();
  b.disabled=false; b.textContent='Processar cobranca';
  const ep=r.eprofit, cor=ep>0?'var(--padrao)':'var(--critico)';
  document.getElementById('r_inv').innerHTML=`<div class="card">
    <div class="kpis">
      <div class="kpi"><div class="kl">Causa diagnosticada</div>
        <div class="kv">${esc(r.failure_cause)}</div></div>
      <div class="kpi"><div class="kl">Score</div>
        <div class="kv">${esc(r.recovery_score)}<span class="ku">/100</span></div></div>
      <div class="kpi"><div class="kl">e-Profit</div>
        <div class="kv" style="color:${cor}">${brl(ep)}</div></div>
      <div class="kpi"><div class="kl">Decisao</div>
        <div class="kv" style="font-size:15px">${esc(r.estrategia)}</div></div>
    </div>
    <div class="sub2">Decomposicao SHAP -- quanto cada variavel empurrou o score</div>
    ${shapHtml(r.shap)}
    ${r.raciocinio&&r.raciocinio.length?`<div class="sub2">Trilha de raciocinio</div>
      <div class="racio">${r.raciocinio.map(l=>`<div class="rlinha">${esc(l)}</div>`).join('')}</div>`:''}
    ${r.plano&&r.plano.length?`<div class="sub2">Plano de retentativa (BACEN: 3 em 7 dias)</div>
      <ul class="lista">${r.plano.map(t=>`<li>${esc(t)}</li>`).join('')}</ul>`:''}
    ${r.mensagem?`<div class="sub2">Mensagem enviada</div><div class="msg">${esc(r.mensagem)}</div>`:''}
    ${rota('POST /simulate/painel/cobranca-falhada')}</div>`;
}

function cen(i){const v=[['Cancellation Page Viewed',34,1,900,'PJ'],
  ['Session Started',9,7,3400,'CLT'],['Session Started',1,17,800,'CLT']][i];
  v_ev.value=v[0];v_dias.value=v[1];v_feat.value=v[2];v_mrr.value=v[3];v_perf.value=v[4];}

async function rodarVoluntario(){
  const b=document.getElementById('b_vol'); b.disabled=true; b.textContent='Rodando...';
  const r=await(await fetch('/simulate/painel/evento-risco',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({event:v_ev.value,days_since_last:+v_dias.value,
      features_used_30d:+v_feat.value,mrr:+v_mrr.value,billing_profile:v_perf.value})})).json();
  b.disabled=false; b.textContent='Rodar o agente';
  const pct=r.risk_score==null?'--':Math.round(r.risk_score*100)+'%';
  document.getElementById('r_vol').innerHTML=`<div class="card">
    <div class="kpis">
      <div class="kpi"><div class="kl">Risco calculado</div><div class="kv">${pct}</div></div>
      <div class="kpi"><div class="kl">Criticidade</div>
        <div class="kv" style="color:${COR[r.criticality]||''}">${ROT[r.criticality]||esc(r.criticality)}</div></div>
      <div class="kpi"><div class="kl">Perfil</div><div class="kv">${esc(r.profile)}</div></div>
      <div class="kpi"><div class="kl">Decisao</div><div class="kv" style="font-size:15px">${
        r.abordado?esc(r.offer_label):'Nao abordar'}</div></div>
    </div>
    ${!r.abordado?`<div class="racio" style="margin-top:12px">Risco abaixo do
      corte de 60%: o sistema decidiu nao gastar a intervencao. E a metrica
      e-Profit em acao -- intervir aqui custaria mais do que o retorno esperado.</div>`:''}
    ${r.channel?`<div class="sub2">Canal escolhido</div><div class="racio">${esc(r.channel)}</div>`:''}
    ${r.message?`<div class="sub2">Mensagem gerada</div><div class="msg">${esc(r.message)}</div>`:''}
    ${rota('POST /simulate/painel/evento-risco')}</div>`;
}

async function importar(){
  const b=document.getElementById('b_imp'); b.disabled=true; b.textContent='Importando...';
  const f=document.getElementById('arq').files[0];
  // Sem arquivo escolhido: manda POST sem corpo (nao um FormData vazio) --
  // um multipart/form-data com zero partes quebra o parser do servidor e
  // devolve "There was an error parsing the body" em vez de cair na base
  // de exemplo, que e o comportamento documentado nesta aba.
  let opts={method:'POST'};
  if(f){const fd=new FormData(); fd.append('arquivo',f); opts.body=fd;}
  const res=await fetch('/simulate/painel/importar',opts);
  const r=await res.json();
  b.disabled=false; b.textContent='Importar base';
  if(!res.ok){document.getElementById('log_imp').innerHTML=
    `<div class="log" style="color:var(--critico)">${esc(JSON.stringify(r.detail||r))}</div>`;return;}
  document.getElementById('log_imp').innerHTML=`<div class="log">arquivo: ${esc(r.arquivo)}
importados: ${r.importados}   rejeitados: ${r.rejeitados.length}   sem dado comportamental: ${r.linhas_sem_dado_comportamental}${
  r.rejeitados.length?'\\n'+r.rejeitados.map(x=>`  linha ${x.linha}: ${esc(x.motivo)}`).join('\\n'):''}</div>`;
  document.getElementById('card_ana').style.display='block';
}

async function analisar(){
  const b=document.getElementById('b_ana'); b.disabled=true; b.textContent='Analisando...';
  const r=await(await fetch('/simulate/painel/insights')).json();
  b.disabled=false; b.textContent='Analisar risco de churn';
  const cs=r.clientes_em_risco||[], crit=cs.filter(c=>c.criticality==='critico'),
    sd=cs.filter(c=>c.criticality==='dado_insuficiente'),
    mrrR=cs.filter(c=>['critico','alto'].includes(c.criticality))
          .reduce((a,c)=>a+(c.mrr||0),0);
  document.getElementById('r_base').innerHTML=`
    <div class="tiles">
      <div class="tile"><div class="n">${cs.length}</div><div class="l">clientes analisados</div></div>
      <div class="tile"><div class="n" style="color:var(--critico)">${crit.length}</div>
        <div class="l">em risco critico</div></div>
      <div class="tile"><div class="n">${brl(mrrR)}</div><div class="l">MRR em risco</div></div>
      <div class="tile"><div class="n" style="color:var(--semdado)">${sd.length}</div>
        <div class="l">sem dado suficiente</div></div>
    </div>
    <div class="tablewrap"><table><thead><tr><th>#</th><th>Cliente</th><th>MRR</th>
      <th>Risco</th><th>Criticidade</th><th>Por que</th></tr></thead><tbody>${
      cs.map((c,i)=>{const w=c.risk_score==null?0:Math.round(c.risk_score*100);
      return `<tr><td class="pos">${i+1}</td>
        <td><div class="cid">${esc(c.customer_id_externo)}</div>
          <div class="cmeta">${esc(c.billing_profile)} &middot; origem ${esc(c.origem)}</div></td>
        <td class="num">${brl(c.mrr)}</td>
        <td><div class="riscowrap"><div class="track">
          <div class="fill" style="width:0%" data-w="${w}"></div></div>
          <span class="rv">${c.risk_score==null?'--':w+'<span class="pc">%</span>'}</span></div></td>
        <td><span class="selo" style="--c:${COR[c.criticality]}"><span class="dot"></span>${
          ROT[c.criticality]}</span></td>
        <td class="exp">${esc(c.explicacao)}</td></tr>`}).join('')}
    </tbody></table></div>${rota('POST /simulate/painel/importar + GET /simulate/painel/insights')}`;
  requestAnimationFrame(()=>document.querySelectorAll('.fill[data-w]')
    .forEach(f=>f.style.width=f.dataset.w+'%'));
}
</script></div></body></html>"""

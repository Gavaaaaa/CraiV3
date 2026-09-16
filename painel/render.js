(function(){
"use strict";

/* ═══════════ UTILIDADES ═══════════ */
var raiz = document.documentElement;
var reduz = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
function loc(){ return idioma === "pt" ? "pt-BR" : "en-US"; }
function moeda(v){
  var n = Math.round(v);
  return idioma === "pt"
    ? "R$ " + n.toLocaleString("pt-BR")
    : "R$" + n.toLocaleString("en-US");
}
function dataCurta(d){
  return d.toLocaleDateString(loc(), { month:"short", year:"numeric" });
}
function dataLonga(d){
  return d.toLocaleDateString(loc(), { day:"numeric", month:"long", year:"numeric" });
}
function el(tag, cls, html){ var e = document.createElement(tag); if(cls) e.className = cls; if(html != null) e.innerHTML = html; return e; }

var IC = {
  check:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 8.5 6 12l7.5-8"/></svg>',
  x:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>',
  relogio:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="8" cy="8" r="6"/><path d="M8 4.6V8l2.4 1.6"/></svg>',
  alerta:'<svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M8 2.8 1.9 13.2h12.2L8 2.8z"/><path d="M8 6.6v3M8 11.3v.1"/></svg>',
  busca:'<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="8.6" cy="8.6" r="5.4"/><path d="M12.6 12.6 17 17"/></svg>',
  msg:'<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M17 12.3a2 2 0 0 1-2 2H6.5L3 17.5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v7.3z"/></svg>',
  raio:'<svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor"><path d="M9 1.2 3.4 8.9h3.3l-.7 5.9L12.6 7H9.3l-.3-5.8z"/></svg>',
  cartao:'<svg width="20" height="14" viewBox="0 0 20 14" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="1" y="1" width="18" height="12" rx="2"/><path d="M1 5.2h18"/></svg>',
  boleto:'<svg width="18" height="16" viewBox="0 0 18 16" fill="currentColor"><rect x="1" y="2" width="1.4" height="12"/><rect x="3.8" y="2" width="0.8" height="12"/><rect x="6" y="2" width="2" height="12"/><rect x="9.2" y="2" width="0.8" height="12"/><rect x="11.4" y="2" width="1.6" height="12"/><rect x="14.4" y="2" width="0.9" height="12"/><rect x="16.3" y="2" width="1.2" height="12"/></svg>',
  vazio:'<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 7.5 12 3l8.5 4.5v9L12 21l-8.5-4.5z"/><path d="M3.5 7.5 12 12l8.5-4.5M12 12v9"/></svg>',
  ok_grande:'<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8 12.2l2.6 2.6L16 9.4"/></svg>'
};

var NIVEL = {
  critico:{ k:"n_critico", cls:"f", cor:"var(--falha)", ic:IC.alerta },
  alto:{ k:"n_alto", cls:"a", cor:"var(--atencao)", ic:IC.relogio },
  padrao:{ k:"n_normal", cls:"o", cor:"var(--ok)", ic:IC.check },
  dado_insuficiente:{ k:"n_semdado", cls:"n", cor:"var(--neutro)", ic:IC.busca }
};

/* ═══════════ ESTADO COMPARTILHADO ═══════════
   A Visão geral lê daqui. Nada é inventado lá. */
var estado = {
  analise: null,        // resultado da aba Clientes em risco
  rotuloBase: null,
  cobrancasRecusadas: 0,
  valorRecusado: 0,
  faturasRecuperadas: 0,
  valorRecuperado: 0,
  contatados: 0,
  taxaAceite: null      // da oferta escolhida na aba Mensagens
};

/* ═══════════ ABAS ═══════════ */
var abas = [].slice.call(document.querySelectorAll(".aba"));
var telas = [].slice.call(document.querySelectorAll(".tela"));
function abrir(nome){
  abas.forEach(function(a){ a.setAttribute("aria-selected", String(a.dataset.tela === nome)); });
  telas.forEach(function(x){ x.classList.toggle("on", x.dataset.tela === nome); });
  window.scrollTo({ top:0, behavior: reduz ? "auto" : "smooth" });
  if(nome === "recuperar") desenharRecuperacao();
  if(nome === "mensagens") mostrarPainelMsg();
  if(nome === "visao") desenharVisao();
}
abas.forEach(function(a){ a.addEventListener("click", function(){ abrir(a.dataset.tela); }); });

document.getElementById("tema").addEventListener("click", function(){
  var at = raiz.getAttribute("data-theme");
  if(!at) at = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  raiz.setAttribute("data-theme", at === "dark" ? "light" : "dark");
  if(ultimaAnalise) desenharDistribuicao(ultimaAnalise);
  if(document.querySelector('[data-tela="visao"]').classList.contains("on")) desenharVisao();
});

/* ═══════════ TROCA DE IDIOMA ═══════════ */
function aplicarIdioma(){
  document.documentElement.lang = idioma === "pt" ? "pt-BR" : "en";
  [].slice.call(document.querySelectorAll("[data-i18n]")).forEach(function(n){
    n.textContent = t(n.dataset.i18n);
  });
  [].slice.call(document.querySelectorAll(".idioma button")).forEach(function(b){
    b.setAttribute("aria-pressed", String(b.dataset.idioma === idioma));
  });
  montarMetodos();
  montarExemplos();
  desenharRecuperacao();
  montarSelectMsg(true);
  if(ultimaAnalise) mostrarResultado(ultimaAnalise, estado.rotuloBase);
  else if(ultimoErro) mostrarErro(ultimoErro.err, ultimoErro.rotulo);
  if(clienteMsg) mostrarMensagem(clienteMsg); else mostrarSemBase();
  if(!document.getElementById("msgTodos").hidden) montarLote();
  desenharVisao();
  document.getElementById("areaResultado").innerHTML = "";
}
[].slice.call(document.querySelectorAll(".idioma button")).forEach(function(b){
  b.addEventListener("click", function(){
    idioma = b.dataset.idioma;
    try{ localStorage.setItem("crai_idioma", idioma); }catch(e){}
    aplicarIdioma();
  });
});

/* ═══════════ BASES DE EXEMPLO ═══════════
   Os três botões enviam os CSVs de painel/exemplos/ pelo MESMO caminho do
   arquivo do usuário: API.exemplo() busca o arquivo e rodarAnalise() o manda
   para POST /simulate/painel/importar. Nada é lido nem calculado aqui. */
var EXEMPLOS = {
  diaria:  { k:"ex_diaria",   arquivo:"base_uso_diario.csv" },
  mensal:  { k:"ex_mensal",   arquivo:"base_uso_mensal.csv" },
  saudavel:{ k:"ex_saudavel", arquivo:"base_saudavel.csv" }
};

/* ═══════════ ABA: CLIENTES EM RISCO ═══════════
   O JavaScript desenha, o Python calcula. A base vai para
   POST /simulate/painel/importar, o ranking vem de GET /simulate/painel/insights
   e a tela mostra o que voltou: risco, criticidade, explicação e régua são da
   API, já na ordem em que ela devolveu. Quando a API não responde, a tela
   mostra erro e descarta o resultado anterior — nunca dado antigo. */
var ultimaAnalise = null;
var ultimoErro = null;
var zEnvio = document.getElementById("zonaEnvio");
var zAnalise = document.getElementById("zonaAnalise");
var zResultado = document.getElementById("zonaResultado");

function esc(s){
  return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function montarExemplos(){
  var host = document.getElementById("exemplos");
  host.innerHTML = "";
  Object.keys(EXEMPLOS).forEach(function(k){
    var e = EXEMPLOS[k];
    var b = el("button","exemplo","<b>" + t(e.k) + "</b><span>" + t(e.k + "_d") + "</span>");
    b.type = "button";
    b.addEventListener("click", function(){
      rodarAnalise(function(){ return API.exemplo(e.arquivo); }, t(e.k));
    });
    host.appendChild(b);
  });
}

var solta = document.getElementById("solta"), arquivo = document.getElementById("arquivo");
solta.addEventListener("click", function(){ arquivo.click(); });
solta.addEventListener("keydown", function(ev){ if(ev.key === "Enter" || ev.key === " "){ ev.preventDefault(); arquivo.click(); } });
["dragenter","dragover"].forEach(function(x){ solta.addEventListener(x, function(ev){ ev.preventDefault(); solta.classList.add("sobre"); }); });
["dragleave","drop"].forEach(function(x){ solta.addEventListener(x, function(ev){ ev.preventDefault(); solta.classList.remove("sobre"); }); });
solta.addEventListener("drop", function(ev){ if(ev.dataTransfer.files[0]) receber(ev.dataTransfer.files[0]); });
arquivo.addEventListener("change", function(){ if(arquivo.files[0]) receber(arquivo.files[0]); arquivo.value = ""; });

function receber(f){
  rodarAnalise(function(){ return Promise.resolve(f); }, f.name);
}

/* Some com o resultado anterior: a Visão geral e a aba Mensagens voltam ao
   estado vazio até a API responder de novo. */
function descartarAnalise(){
  ultimaAnalise = null;
  estado.analise = null;
  estado.rotuloBase = null;
  clienteMsg = null;
  loteResultado = null;
  atualizarPontoVisao();
  montarSelectMsg(true);          // a lista de clientes da aba Mensagens some junto
}

/* `obterArquivo` devolve uma Promise<File>: o arquivo do usuário ou o CSV de
   exemplo buscado do servidor. Daqui em diante o caminho é um só. */
function rodarAnalise(obterArquivo, rotulo){
  descartarAnalise();
  ultimoErro = null;
  zEnvio.hidden = true; zResultado.hidden = true; zAnalise.hidden = false;
  zAnalise.innerHTML = '<div class="painel pad"><div style="display:flex;align-items:center;gap:16px">' +
    '<div class="spin"></div><div><div style="font-size:17px;font-weight:600;font-family:\'Inter Tight\',sans-serif" id="passoTxt"></div>' +
    '<div class="sub" id="passoSub"></div></div></div><div class="barra"><i id="barraI"></i></div></div>';
  function passo(k, pct){
    var a = document.getElementById("passoTxt"), b = document.getElementById("passoSub"), c = document.getElementById("barraI");
    if(a) a.textContent = t(k);
    if(b) b.textContent = t(k + "s", { rotulo:rotulo });
    if(c) c.style.width = pct + "%";
  }
  var relatorio = null;
  passo("an1", 12);
  API.ambiente()
    .then(function(amb){
      if(!amb || amb.modelos !== true) throw API.erro("modelos_desligados", { ambiente:amb });
      passo("an2", 38);
      return obterArquivo();
    })
    .then(function(f){ return API.importar(f); })
    .then(function(r){
      relatorio = r;
      if(typeof r.importados !== "number") throw API.erro("resposta_invalida", { rota:"/simulate/painel/importar" });
      if(r.importados === 0) throw API.erro("nada_importado", { relatorio:r });
      passo("an3", 72);
      return API.insights();
    })
    .then(function(ins){
      if(!ins || !Array.isArray(ins.clientes_em_risco) || typeof ins.total_clientes !== "number")
        throw API.erro("resposta_invalida", { rota:"/simulate/painel/insights" });
      passo("an4", 100);
      var res = { relatorio:relatorio, clientes:ins.clientes_em_risco, total:ins.total_clientes, gerado_em:ins.gerado_em };
      ultimaAnalise = res;
      estado.analise = res;
      estado.rotuloBase = rotulo;
      atualizarPontoVisao();
      setTimeout(function(){ mostrarResultado(res, rotulo); }, reduz ? 0 : 260);
    })
    .catch(function(err){ mostrarErro(err, rotulo); });
}

function textoDoDetail(d){
  if(d == null || d === "") return "";
  if(typeof d === "string") return d;
  if(d.motivo || d.detalhe) return [d.motivo, d.detalhe].filter(Boolean).join(": ");
  try{ return JSON.stringify(d); }catch(e){ return String(d); }
}

/* Estado de erro: título e orientação passam pelo dicionário; o que a API
   disse (motivo, detalhe, mensagem) é mostrado como chegou. */
function mostrarErro(err, rotulo){
  descartarAnalise();
  ultimoErro = { err:err, rotulo:rotulo };
  zAnalise.hidden = true; zEnvio.hidden = true; zResultado.hidden = false;
  var tipo = (err && err.tipo) || "rede";
  var titulo, orient, daApi = "";
  if(tipo === "tempo"){ titulo = t("erro_tempo_t"); orient = t("erro_tempo_d", { s:err.segundos || Math.round(API.TEMPO_LIMITE_MS / 1000) }); }
  else if(tipo === "http"){ titulo = t("erro_http_t"); orient = t("erro_http_d", { status:err.status, rota:err.rota }); daApi = textoDoDetail(err.detail); }
  else if(tipo === "resposta_invalida"){ titulo = t("erro_resposta_t"); orient = t("erro_resposta_d", { rota:err.rota || "" }); }
  else if(tipo === "modelos_desligados"){ titulo = t("erro_modelos_t"); orient = t("erro_modelos_d"); daApi = err.ambiente ? JSON.stringify(err.ambiente) : ""; }
  else if(tipo === "nada_importado"){
    titulo = t("erro_importacao_t"); orient = t("erro_importacao_d");
    var r = err.relatorio || {};
    daApi = (r.mensagem || "") + (r.colunas_nao_encontradas && r.colunas_nao_encontradas.length
      ? "\n" + t("r_colunas_faltando") + ": " + r.colunas_nao_encontradas.join(", ") : "");
  }
  else { titulo = t("erro_rede_t"); orient = t("erro_rede_d"); daApi = err && err.causa ? String(err.causa) : (err && err.message) || ""; }

  zResultado.innerHTML = '<div class="painel pad">' +
    '<div class="tag f" style="margin-bottom:12px">' + IC.alerta + " " + esc(titulo) + "</div>" +
    '<p class="sub">' + esc(orient) + "</p>" +
    (daApi ? '<pre class="sub" style="white-space:pre-wrap;margin:14px 0 0;padding:12px 14px;border-radius:10px;background:var(--surface-3);font-size:13.5px">' + esc(daApi) + "</pre>" : "") +
    '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:18px">' +
    '<button type="button" class="btn p" id="tentarDeNovo">' + esc(t("tentar_de_novo")) + "</button>" +
    '<button type="button" class="btn" id="outraBase">' + esc(t("outra_base")) + "</button></div></div>";
  document.getElementById("tentarDeNovo").addEventListener("click", function(){
    zResultado.hidden = true; zEnvio.hidden = false; ultimoErro = null;
    window.scrollTo({ top:0, behavior: reduz ? "auto" : "smooth" });
  });
  document.getElementById("outraBase").addEventListener("click", function(){
    zResultado.hidden = true; zEnvio.hidden = false; ultimoErro = null;
    window.scrollTo({ top:0, behavior: reduz ? "auto" : "smooth" });
  });
}

function dataHora(iso){
  var d = iso ? new Date(iso) : null;
  return d && !isNaN(d.getTime()) ? d.toLocaleString(loc(), { dateStyle:"short", timeStyle:"short" }) : "—";
}
function pct(v){ return Math.round(v * 100) + "%"; }

function mostrarResultado(res, rotulo){
  zAnalise.hidden = true; zEnvio.hidden = true; zResultado.hidden = false;
  var criticos = res.clientes.filter(function(c){ return c.criticality === "critico"; });
  var altos = res.clientes.filter(function(c){ return c.criticality === "alto"; });
  var semDado = res.clientes.filter(function(c){ return c.criticality === "dado_insuficiente"; });
  var emRisco = criticos.concat(altos);
  var receita = emRisco.reduce(function(s,c){ return s + (typeof c.mrr === "number" ? c.mrr : 0); }, 0);
  var rel = res.relatorio || {};

  zResultado.innerHTML = "";
  var topo = el("div","destaques");
  topo.innerHTML =
    '<div class="destaque" style="--cor:var(--falha)"><div class="rot">' + t("d_criticos") + '</div><div class="big num">' + criticos.length + '</div><div class="pe">' + t("d_criticos_pe") + '</div></div>' +
    '<div class="destaque" style="--cor:var(--atencao)"><div class="rot">' + t("d_altos") + '</div><div class="big num">' + altos.length + '</div><div class="pe">' + t("d_altos_pe") + '</div></div>' +
    '<div class="destaque" style="--cor:var(--ok)"><div class="rot">' + t("d_receita") + '</div><div class="big num">' + moeda(receita) + '</div><div class="pe">' + t("d_receita_pe") + '</div></div>';
  zResultado.appendChild(topo);

  var g2 = el("div","grid2");
  var pE = el("div","painel pad");
  pE.innerHTML = "<h2>" + t("g_dist_t") + '</h2><p class="sub" style="margin-bottom:20px">' +
    t("g_dist_s", { n:res.total.toLocaleString(loc()), rotulo:esc(rotulo) }) + '</p><div class="g-wrap" id="gDist"></div>';
  g2.appendChild(pE);

  /* A régua e o relatório de importação, como a API informou. Os percentis da
     régua (p50/p75/p90) não vêm na resposta, então não aparecem aqui. */
  var nBase = res.clientes.filter(function(c){ return c.origem_da_regua === "base_do_tenant"; }).length;
  var nPadrao = res.clientes.filter(function(c){ return c.origem_da_regua === "padrao_global"; }).length;
  var rej = rel.rejeitados || [];
  var rejTxt = rej.length ? rej.slice(0, 3).map(function(x){ return t("linha_n", { n:x.linha, motivo:esc(x.motivo) }); }).join("<br>") +
    (rej.length > 3 ? "<br>" + t("e_mais", { n:rej.length - 3 }) : "") : "";
  var colFalt = rel.colunas_nao_encontradas || [];
  var pD = el("div","painel pad");
  pD.innerHTML = "<h2>" + t("g_regua_t") + '</h2><p class="sub" style="margin-bottom:18px">' + t("g_regua_s") + "</p>" +
    '<div style="display:flex;flex-direction:column;gap:14px">' +
    (nBase ? lr(t("r_regua_base"), t("n_clientes", { n:nBase.toLocaleString(loc()) })) : "") +
    (nPadrao ? lr(t("r_regua_padrao"), t("n_clientes", { n:nPadrao.toLocaleString(loc()) })) : "") +
    lr(t("r_importados"), typeof rel.importados === "number" ? rel.importados.toLocaleString(loc()) : "—") +
    lr(t("r_rejeitados"), String(rej.length)) +
    (rejTxt ? '<div class="sub" style="font-size:13.5px;margin-top:-6px">' + rejTxt + "</div>" : "") +
    lr(t("r_colunas_faltando"), colFalt.length ? esc(colFalt.join(", ")) : t("nenhuma")) +
    lr(t("r_sem_dado"), typeof rel.linhas_sem_dado_comportamental === "number" ? String(rel.linhas_sem_dado_comportamental) : "—") +
    lr(t("r_gerado_em"), dataHora(res.gerado_em)) + "</div>" +
    (nPadrao ? '<div class="fonte-dado" style="margin-top:16px"><span>' + IC.alerta + " " + t("regua_padrao_aviso") + "</span></div>" : "");
  g2.appendChild(pD);
  zResultado.appendChild(g2);

  var lista = el("div","painel pad");
  lista.style.marginTop = "18px";
  lista.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:18px">' +
    "<div><h2>" + t("lista_t") + '</h2><p class="sub">' + t("lista_s") + "</p></div>" +
    '<button type="button" class="btn" id="outraBase">' + t("outra_base") + "</button></div>" +
    '<div class="clientes" id="listaRisco"></div>';
  zResultado.appendChild(lista);

  var alvo = document.getElementById("listaRisco");
  var busca = el("div","campo", '<input type="search" id="buscaCliente" placeholder="' + esc(t("buscar_cliente")) +
    '" aria-label="' + esc(t("buscar_cliente")) + '" autocomplete="off">');
  busca.style.maxWidth = "380px"; busca.style.marginBottom = "14px";
  lista.insertBefore(busca, alvo);

  function linhaCliente(c, i, clicavel){
    var n = NIVEL[c.criticality] || NIVEL.padrao;
    var linha = el(clicavel ? "button" : "div", "cli-linha");
    if(clicavel) linha.type = "button";
    linha.style.animationDelay = (i * 45) + "ms";
    linha.innerHTML = '<div><div class="cli-nome">' + esc(c.customer_id_externo) + '</div><div class="cli-porque">' + esc(c.explicacao) + "</div></div>" +
      '<div class="cli-dir"><span class="tag ' + n.cls + '">' + n.ic + " " + t(n.k) + "</span>" +
      '<div class="medidor"><div class="v" style="color:' + n.cor + '">' + (c.risk_score == null ? "—" : pct(c.risk_score)) + "</div>" +
      (c.risk_score == null ? "" : '<div class="t"><i style="width:' + pct(c.risk_score) + ";background:" + n.cor + '"></i></div>') + "</div></div>";
    if(clicavel) linha.addEventListener("click", function(){
      clienteMsg = c; montarSelectMsg(true);
      var s = document.getElementById("selMsgCliente");
      s.value = c.customer_id_externo;
      mostrarMensagem(c); abrir("mensagens");
    });
    return linha;
  }

  /* Sem filtro: quem está em risco (ou, se ninguém, os primeiros da fila) e,
     sempre no fim, os sem dado. Com filtro: qualquer cliente do ranking que a
     API devolveu, pelo id, com a criticidade e a frase que vieram dela. */
  function desenharLinhas(filtro){
    alvo.innerHTML = "";
    if(filtro){
      var achados = res.clientes.filter(function(c){ return String(c.customer_id_externo).toLowerCase().indexOf(filtro) !== -1; }).slice(0, 20);
      if(!achados.length){ alvo.appendChild(el("div","vazio",'<div class="t">' + t("busca_vazia") + "</div>")); return; }
      achados.forEach(function(c, i){ alvo.appendChild(linhaCliente(c, i, c.risk_score != null)); });
      return;
    }
    var mostrar = emRisco.length ? emRisco.slice(0, 8)
      : res.clientes.filter(function(c){ return c.risk_score != null; }).slice(0, 5);
    if(!emRisco.length){
      alvo.appendChild(el("div","vazio",
        '<div style="color:var(--ok)">' + IC.ok_grande + "</div>" +
        '<div class="t">' + t("vazio_t") + '</div>' +
        '<p class="sub" style="margin-top:6px;max-width:48ch;margin-inline:auto">' + t("vazio_d") + "</p>"));
    }
    mostrar.forEach(function(c, i){ alvo.appendChild(linhaCliente(c, i, true)); });

    /* `risk_score: null` não é zero: sem barra, com travessão e o motivo que a
       API deu, sempre no fim da lista. */
    if(semDado.length){
      alvo.appendChild(el("div", null, '<h3 style="margin-top:14px">' + t("semdado_t", { n:semDado.length }) +
        '</h3><p class="sub" style="margin-bottom:10px">' + t("semdado_d") + "</p>"));
      semDado.forEach(function(c, i){ alvo.appendChild(linhaCliente(c, i, false)); });
    }
  }
  desenharLinhas("");
  document.getElementById("buscaCliente").addEventListener("input", function(ev){
    desenharLinhas(String(ev.target.value || "").trim().toLowerCase());
  });

  document.getElementById("outraBase").addEventListener("click", function(){
    zResultado.hidden = true; zEnvio.hidden = false;
    window.scrollTo({ top:0, behavior: reduz ? "auto" : "smooth" });
  });
  desenharDistribuicao(res);
  montarSelectMsg(true);
}
function lr(rot, val){
  return '<div style="display:flex;justify-content:space-between;align-items:baseline;gap:14px;padding-bottom:13px;border-bottom:1px solid var(--line)">' +
    '<span style="color:var(--ink-2);font-size:14.5px">' + rot + '</span>' +
    '<b class="num" style="font-size:20px;font-weight:600;white-space:nowrap">' + val + "</b></div>";
}

function corDe(n){ return getComputedStyle(raiz).getPropertyValue(n).trim(); }

/* Uma barra por criticidade da API. `dado_insuficiente` é a sua própria
   faixa: nunca entra em padrão/alto/crítico. */
function desenharDistribuicao(res){
  var host = document.getElementById("gDist");
  if(!host) return;
  var faixas = [
    { k:"n_normal", f:function(c){ return c.criticality === "padrao"; }, cor:corDe("--ok") },
    { k:"n_alto", f:function(c){ return c.criticality === "alto"; }, cor:corDe("--atencao") },
    { k:"n_critico", f:function(c){ return c.criticality === "critico"; }, cor:corDe("--falha") },
    { k:"n_semdado", f:function(c){ return c.criticality === "dado_insuficiente"; }, cor:corDe("--neutro") }
  ].map(function(x){ x.n = res.clientes.filter(x.f).length; return x; }).filter(function(x){ return x.n > 0; });
  var max = Math.max.apply(null, faixas.map(function(x){ return x.n; }));
  var h = '<div style="display:flex;flex-direction:column;gap:16px">';
  faixas.forEach(function(x){
    var p = res.total ? Math.round((x.n / res.total) * 100) : 0;
    h += '<div><div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;margin-bottom:7px">' +
      '<span style="font-size:14.5px;font-weight:500">' + t(x.k) + "</span>" +
      '<span style="font-size:14px;color:var(--ink-2)"><b class="num" style="font-size:18px;color:var(--ink)">' + x.n + "</b> · " + p + '%</span></div>' +
      '<div style="height:12px;border-radius:6px;background:var(--surface-3);overflow:hidden">' +
      '<i style="display:block;height:100%;border-radius:6px;background:' + x.cor + ";width:" + ((x.n / max) * 100) + '%;transition:width .7s cubic-bezier(.22,1,.36,1)"></i></div></div>';
  });
  host.innerHTML = h + "</div>";
}

/* ═══════════ ABA: COBRANÇAS ═══════════
   Dispara uma cobrança de teste. A recusa vai para
   POST /simulate/painel/cobranca-falhada, que roda o grafo do churn
   involuntário inteiro e devolve a decisão; a resposta vira o caso que a aba
   Recuperação mostra. A rota aceita valor, código de falha e tentativas já
   usadas — não existe cliente na requisição, então a tela não inventa um. */
var METODOS = [
  { id:"pix", k:"m_pix", ic:IC.raio, res:"falha", ativo:true },
  { id:"debito", k:"m_debito", ic:IC.cartao, res:"pendente", ativo:false },
  { id:"boleto", k:"m_boleto", ic:IC.boleto, res:"pendente", ativo:false }
];
var metodoSel = "pix";

/* Clientes de exemplo da aba Cobranças.
   O `id` NÃO é enfeite: vai no corpo da requisição e vira o `customer_id` do
   grafo, de onde `perfil_provider.get_perfil` deriva o perfil do pagador por
   `seed_por_cliente` (md5 estável). Mesmo cliente, mesmo histórico de
   pagamento, mesmo tempo de casa, mesmas falhas em 90 dias — sempre. Trocar
   um `id` aqui troca o perfil que a API devolve.
   O `valor` é só o preenchimento inicial do campo: quem manda é o que estiver
   escrito na hora do disparo. */
var CLIENTES_COBRANCA = [
  { id:"ancora",     nome:"Ancora Serviços",       valor:489,  codigo:"AM04", tentativas:0 },
  { id:"vitalis",    nome:"Clínica Vitalis",       valor:1290, codigo:"AM04", tentativas:0 },
  { id:"rota",       nome:"Rota Logística",        valor:740,  codigo:"AM04", tentativas:3 },
  { id:"marcenaria", nome:"Marcenaria Horizonte",  valor:560,  codigo:"MD01", tentativas:0 },
  { id:"nuvem",      nome:"Nuvem Contábil",        valor:219,  codigo:"AB03", tentativas:0 }
];
function clienteSel(){
  var s = document.getElementById("selCliente");
  var id = s ? s.value : "";
  return CLIENTES_COBRANCA.find(function(c){ return c.id === id; }) || null;
}

function montarMetodos(){
  var host = document.getElementById("metodos");
  host.innerHTML = "";
  METODOS.forEach(function(m){
    var b = el("button","opcao");
    b.type = "button";
    b.setAttribute("aria-pressed", String(m.id === metodoSel));
    if(!m.ativo){ b.disabled = true; b.style.opacity = ".45"; b.style.cursor = "not-allowed"; }
    b.innerHTML = '<span class="marca-sel" style="color:#141517">' + IC.check + "</span>" +
      '<span class="ic">' + m.ic + "</span><span><b>" + t(m.k) + "</b><span>" + t(m.k + "_d") + "</span></span>";
    b.addEventListener("click", function(){
      if(!m.ativo) return;
      metodoSel = m.id;
      [].slice.call(host.children).forEach(function(o,i){ o.setAttribute("aria-pressed", String(METODOS[i].id === metodoSel)); });
      document.getElementById("areaResultado").innerHTML = "";
    });
    host.appendChild(b);
  });
  var selCli = document.getElementById("selCliente");
  if(selCli && !selCli.options.length){
    CLIENTES_COBRANCA.forEach(function(c){
      var o = el("option", null, c.nome); o.value = c.id; selCli.appendChild(o);
    });
    selCli.addEventListener("change", function(){
      var c = clienteSel();
      if(c) document.getElementById("inpValor").value = moedaDec(c.valor);
      document.getElementById("areaResultado").innerHTML = "";
    });
  }
}

/* "R$ 1.234,56" / "1234.56" / "1234,56" → número; null se não der. */
function lerValor(txt){
  var s = String(txt || "").replace(/[R$\s]/g, "");
  if(/,\d{1,2}$/.test(s)) s = s.replace(/\./g, "").replace(",", ".");
  else s = s.replace(/,/g, "");
  var n = parseFloat(s);
  return isNaN(n) ? null : n;
}

document.getElementById("btnCobrar").addEventListener("click", function(){
  var area = document.getElementById("areaResultado");
  var valorTxt = document.getElementById("inpValor").value;
  var valor = lerValor(valorTxt);
  var cli = clienteSel();
  // Causa da recusa e contador de tentativas vem do cliente, nao de um campo:
  // num gateway real o codigo chega na resposta da recusa e o contador e da
  // recorrencia. Nenhum dos dois e digitado por alguem.
  var codigo = cli ? cli.codigo : "AM04";
  var tentativas = cli ? cli.tentativas : 0;
  var m = METODOS.find(function(x){ return x.id === metodoSel; });
  var btn = this;

  if(m.res === "pendente"){
    area.innerHTML = bloco("n", corDe("--neutro"), IC.relogio, t("res_boleto_t"), t("res_boleto_d"), t("res_boleto_r"), null);
    return;
  }
  if(valor == null){
    area.innerHTML = bloco("f", corDe("--falha"), IC.x, t("valor_invalido_t"), t("valor_invalido_d"), "", null);
    return;
  }
  btn.disabled = true;
  area.innerHTML = '<div class="resultado n"><div class="processando"><div class="spin"></div><span>' +
    t("enviando", { valor:esc(valorTxt), metodo:t(m.k) }) + "</span></div></div>";

  API.ambiente()
    .then(function(amb){
      if(!amb || amb.modelos !== true) throw API.erro("modelos_desligados", { ambiente:amb });
      return API.cobrancaFalhada({ valor:valor, codigo_falha:codigo, tentativas_usadas:tentativas,
                                   cliente:(cli ? cli.id : null) });
    })
    .then(function(d){
      if(!d || typeof d.recovery_score !== "number" || !Array.isArray(d.canais_considerados))
        throw API.erro("resposta_invalida", { rota:"/simulate/painel/cobranca-falhada" });
      estado.cobrancasRecusadas++;
      estado.valorRecusado += valor;
      atualizarPontoVisao();
      registrarCaso({ valor:valor, codigo:codigo, tentativas:tentativas,
                      cliente:(cli ? cli.nome : null) }, d);
      area.innerHTML = bloco("f", corDe("--falha"), IC.x, t("res_falha_t"),
        t("res_falha_d", { causa:esc(d.failure_cause_legivel || d.failure_cause || "—"), valor:moedaDec(valor), codigo:esc(codigo) }),
        t("res_falha_r", { decisao:esc(d.estrategia_legivel || d.estrategia || "—") }), t("res_falha_b"));
      var b = area.querySelector("#irRecuperar");
      if(b) b.addEventListener("click", function(){ abrir("recuperar"); });
    })
    .catch(function(err){ area.innerHTML = painelErroMsg(err); })
    .finally(function(){ btn.disabled = false; });
});
function bloco(cls, cor, ic, titulo, desc, rod, botao){
  return '<div class="resultado ' + cls + '"><div class="res-in">' +
    '<span class="res-ic" style="background:' + cor + '">' + ic + "</span>" +
    '<span class="res-corpo"><span class="t">' + titulo + '</span><span class="d">' + desc + "</span></span></div>" +
    (rod || botao ? '<div class="res-acao"><span class="txt">' + rod + "</span>" +
    (botao ? '<button type="button" class="btn p" id="irRecuperar">' + botao + "</button>" : "") + "</div>" : "") + "</div>";
}

/* ═══════════ ABA: RECUPERAÇÃO — o backend decide, a tela exibe ═══════════
   Cada caso é a resposta de POST /simulate/painel/cobranca-falhada mais a
   entrada que a produziu. Nada aqui é recalculado: chance, retorno, decisão,
   SHAP, plano, raciocínio, mensagem e o comparativo de canal vêm da API. */
var casos = [];
var casoAtivo = -1;
var CANAL_INV_K = { bot_whatsapp:"ci_bot_whatsapp", email_auto:"ci_email_auto", sms:"ci_sms",
                    ligacao_cs:"ci_ligacao_cs", pix_boleto_link:"ci_pix_boleto_link" };
var FEATURES_LIDAS = ["payment_history_score", "failure_count_90d", "tenure_months", "avg_ticket", "attempt_count"];

function registrarCaso(entrada, resposta){
  casos.push({ n:casos.length + 1, entrada:entrada, resposta:resposta });
  casoAtivo = casos.length - 1;
}
function caso(){ return casoAtivo >= 0 ? casos[casoAtivo] : null; }
function nDec(v, casas){ return (typeof v === "number" ? v : 0).toFixed(casas).replace(".", idioma === "pt" ? "," : "."); }

function desenharRecuperacao(){
  var host = document.getElementById("recupCorpo");
  if(!caso()){
    host.innerHTML = '<div class="painel pad"><div class="vazio"><div style="color:var(--ink-3)">' + IC.vazio + '</div>' +
      '<div class="t">' + t("rec_vazio_t") + '</div><p class="sub" style="margin-top:6px;max-width:52ch;margin-inline:auto">' +
      t("rec_vazio_d") + '</p><button type="button" class="btn p" style="margin-top:20px" id="irCobrar">' +
      t("rec_ir_cobrar") + '</button></div></div>';
    var b = document.getElementById("irCobrar");
    if(b) b.addEventListener("click", function(){ abrir("cobrar"); });
    return;
  }
  host.innerHTML = '<div class="rec-grid">' + colunaFatos() + '<div class="rec-fluxo" id="recFluxo"></div></div>';
  ligarCasos();
  desenharFluxo();
}

/* "O que o sistema já sabe": a entrada, a causa que a API diagnosticou e os
   sinais que o classificador leu (os `rotulo` do SHAP, como vieram). O perfil
   de pagador não vem na resposta, e a tela diz isso em vez de inventar. */
function colunaFatos(){
  var c = caso(), d = c.resposta;
  var h = '<aside class="painel pad rec-ctrl">';
  if(casos.length > 1){
    h += '<h3>' + t("rec_qual_caso") + '</h3><div class="caso-sel">';
    casos.forEach(function(x, i){
      h += '<button type="button" class="caso-b" data-i="' + i + '" aria-pressed="' + (i === casoAtivo) + '">' +
        '<span><b>' + esc(x.entrada.cliente || t("caso_n", { n:x.n })) + '</b><span>' + esc(x.resposta.failure_cause_legivel || x.resposta.failure_cause || "") + '</span></span>' +
        '<span class="cv">' + moeda(x.entrada.valor) + '</span></button>';
    });
    h += '</div>';
  }
  h += '<h2>' + t("rec_sabe_t") + '</h2><p class="sub" style="margin-bottom:10px">' + t("rec_sabe_d") + '</p>' +
    (c.entrada.cliente
      ? fato(IC.busca, t("f_cliente"), esc(c.entrada.cliente), t("f_cliente_de"))
      : "") +
    fato(IC.cartao, t("f_valor"), moedaDec(c.entrada.valor), t("f_valor_de")) +
    fato(IC.alerta, t("f_causa"), esc(d.failure_cause_legivel || d.failure_cause || "—"), t("f_causa_de", { codigo:esc(c.entrada.codigo) })) +
    fato(IC.relogio, t("f_tentativas"), String(c.entrada.tentativas), t("f_tentativas_de"));
  var lidas = (d.shap || []).filter(function(x){ return FEATURES_LIDAS.indexOf(x.feature) !== -1; });
  lidas.forEach(function(x){ h += fato(IC.check, esc(x.rotulo), "", t("f_lido_de")); });
  h += fato(IC.busca, t("f_perfil"), "—", t("f_nao_devolvido")) +
    '<button type="button" class="btn" style="margin-top:20px;width:100%;justify-content:center" id="recRecomecar">' +
      t("rec_recomecar") + '</button></aside>';
  return h;
}
function fato(ic, nome, valor, de){
  return '<div class="fato"><span class="fato-ic">' + ic + '</span>' +
    '<span><span class="fato-n">' + nome + '</span>' + (valor ? '<span class="fato-v">' + valor + '</span>' : "") +
    '<span class="fato-de">' + de + '</span></span></div>';
}
function ligarCasos(){
  [].slice.call(document.querySelectorAll(".caso-b")).forEach(function(b){
    b.addEventListener("click", function(){ casoAtivo = +b.dataset.i; desenharRecuperacao(); });
  });
  document.getElementById("recRecomecar").addEventListener("click", function(){
    casos = []; casoAtivo = -1; desenharRecuperacao();
  });
}

function desenharFluxo(){
  var c = caso(), d = c.resposta;
  var host = document.getElementById("recFluxo");
  var age = !!d.estrategia;
  var tit = age ? esc(d.estrategia_legivel || d.estrategia) : t("dec_nao");
  var racio = (d.raciocinio || []).map(function(x){ return String(x); });

  var h = '<div class="decisao ' + (age ? "sim" : "nao") + '">' +
    '<span class="dic" style="background:' + (age ? "var(--ok)" : "var(--falha)") + '">' + (age ? IC.check : IC.x) + '</span>' +
    '<span><span class="dt">' + tit + '</span><span class="dd">' + (age ? t("dec_sim_d") : t("dec_nao_d")) + '</span></span></div>';

  h += '<div class="numeros">' +
    '<div><div class="rot">' + t("big_chance") + '</div><div class="v">' + Math.round(d.recovery_score) + '%</div>' +
      '<div class="pe">' + t("big_chance_pe") + '</div></div>' +
    '<div><div class="rot">' + t("big_retorno") + '</div><div class="v" style="color:' + (typeof d.eprofit === "number" && d.eprofit >= 0 ? "var(--ok)" : "var(--falha)") + '">' +
      (typeof d.eprofit === "number" ? (d.eprofit < 0 ? "−" : "") + moedaDec(Math.abs(d.eprofit)) : "—") + '</div><div class="pe">' + t("big_retorno_pe") + '</div></div>' +
    '<div><div class="rot">' + t("big_custo") + '</div><div class="v">—</div>' +
      '<div class="pe">' + t("big_custo_pe") + '</div></div></div>';

  h += passo("p1_t", t("p1_d", { valor:moedaDec(c.entrada.valor), codigo:esc(c.entrada.codigo), causa:esc(d.failure_cause_legivel || d.failure_cause || "—"), n:c.entrada.tentativas }), "");

  /* SHAP: ordenado pelo peso absoluto, como a tarefa pede; rótulo e valores da API. */
  var shap = (d.shap || []).slice().sort(function(a, b){ return Math.abs(b.shap_value) - Math.abs(a.shap_value); });
  var maxAbs = Math.max.apply(null, shap.map(function(x){ return Math.abs(x.shap_value); }).concat([0])) || 1;
  var razoes = shap.length ? '<div class="razoes">' : '<p class="sub">' + t("p2_sem_shap") + "</p>";
  shap.forEach(function(x){
    var pos = x.shap_value >= 0, w = Math.abs(x.shap_value) / maxAbs * 50;
    razoes += '<div class="razao"><span class="rn">' + esc(x.rotulo) + '</span>' +
      '<span class="rb"><i style="' + (pos ? "left:50%" : "right:50%") + ';width:' + w.toFixed(1) + '%;background:' +
      (pos ? "var(--ok)" : "var(--falha)") + '"></i></span>' +
      '<span class="rv" style="color:' + (pos ? "var(--ok)" : "var(--falha)") + '">' +
      (pos ? "+" : "−") + nDec(Math.abs(x.shap_value), 2) + (typeof x.contribution_pct === "number" ? ' <small>' + nDec(x.contribution_pct, 1) + "%</small>" : "") + '</span></div>';
  });
  if(shap.length) razoes += '</div>';
  h += passo("p2_t", t("p2_d"), razoes);

  h += passo("p_racio_t", t("p_racio_d"), racio.length
    ? '<ol class="raciocinio">' + racio.map(function(x){ return "<li>" + esc(x) + "</li>"; }).join("") + "</ol>"
    : '<p class="sub">' + t("p_racio_vazio") + "</p>");

  var plano = d.plano || [];
  h += passo("p3_t", plano.length ? t("p3_d") : t("p3_sem_plano", { decisao:esc(d.estrategia_legivel || d.estrategia || "—") }),
    plano.length ? '<div class="tent">' + plano.map(function(x, i){
      return '<div class="tent-l futura"><span class="tent-n">' + (i + 1) + '</span><span><span class="tent-q">' + esc(x) + "</span></span></div>";
    }).join("") + "</div>" : "");

  /* Comparativo de canal: os cinco canais por e-Profit, com o motivo de cada um.
     A mensagem sai por WhatsApp por limitação de integração; se outro canal tem
     o maior e-Profit, isso fica visível junto com a razão. */
  var canais = d.canais_considerados || [];
  var escolhido = canais.find(function(x){ return x.escolhido && CANAIS_HUMANOS.indexOf(x.canal) === -1; });
  var melhor = canais.find(function(x){ return x.melhor_eprofit; });
  var tabela = '<div class="comparativo">';
  canais.forEach(function(x){
    var humano = CANAIS_HUMANOS.indexOf(x.canal) !== -1;
    var vence = !!x.escolhido && !humano;
    tabela += '<div class="comp-l' + (vence ? " vence" : "") + '">' +
      '<span class="comp-n">' + esc(CANAL_INV_K[x.canal] ? t(CANAL_INV_K[x.canal]) : x.canal) + "</span>" +
      '<span class="comp-e num">' + (typeof x.eprofit === "number" ? moedaDec(x.eprofit) : "—") + "</span>" +
      '<span class="comp-t">' + (vence ? '<span class="tag o">' + IC.check + " " + t("escolhido") + "</span>" : "") +
      (x.melhor_eprofit ? '<span class="tag a">' + t("melhor_eprofit") + "</span>" : "") +
      (humano ? '<span class="tag n">' + t("canal_humano") + "</span>" : "") + "</span>" +
      '<span class="comp-m">' + esc(x.motivo) + "</span></div>";
  });
  tabela += "</div>";
  var nota = (escolhido && melhor && melhor.canal !== escolhido.canal)
    ? '<div class="fonte-dado" style="margin-top:16px"><span>' + IC.alerta + " " + t("canal_nota", {
        escolhido:esc(CANAL_INV_K[escolhido.canal] ? t(CANAL_INV_K[escolhido.canal]) : escolhido.canal),
        melhor:esc(CANAL_INV_K[melhor.canal] ? t(CANAL_INV_K[melhor.canal]) : melhor.canal) }) + "</span></div>" : "";
  h += passo("p_canal_t", t("p_canal_d"), canais.length ? tabela + nota : '<p class="sub">' + t("p_canal_vazio") + "</p>");

  var msg = d.mensagem;
  h += passo("p5_t", msg ? t("p5_d_api", { canal:esc(d.channel ? (CANAL_INV_K["bot_" + d.channel] ? t(CANAL_INV_K["bot_" + d.channel]) : d.channel) : "—") }) : t("p5_sem_msg"),
    (msg ? '<div class="balao escolhido"><div class="txt">' + esc(msg) + "</div></div>" : "") +
    (msg ? '<button type="button" class="btn p" style="margin-top:16px" id="irMsgCaso">' + t("ir_msg_caso") + "</button>" : ""));

  host.innerHTML = h;
  var bm = document.getElementById("irMsgCaso");
  if(bm) bm.addEventListener("click", function(){ abrirCasoMsg(caso()); });
}

function passo(tk, desc, extra){
  return '<div class="painel pad passo"><h2>' + t(tk) + '</h2>' +
    '<p class="sub" style="max-width:64ch">' + desc + '</p>' + (extra ? '<div style="margin-top:18px">' + extra + '</div>' : "") + '</div>';
}

/* ═══════════ ABA: MENSAGENS ═══════════
   O backend decide; a tela exibe. Nos dois modos a decisão vem de
   POST /simulate/painel/disparo-lote: oferta, as três candidatas (texto,
   taxa de aceite aprendida, retorno esperado, motivo, origem do texto), o
   canal e os canais descartados com o porquê. Nenhum texto de mensagem é
   montado aqui, e nenhum canal é escolhido aqui.

   O modo "um cliente" mostra a decisão do lote da base para aquele cliente.
   Não existe decisão avulsa consistente com o /insights: `evento-risco`
   exige um evento do SDK e é o evento que decide o risco (um cliente da
   planilha não tem evento), e o lote com UM cliente no corpo cai na régua
   global (uma linha não sustenta a régua da base) — medido: ANCORA-07-A é
   crítico na base de uso diário e "padrao" sozinho. Então a tela roda o lote
   inteiro (simulado) e lê a entrada deste cliente na resposta. */
var clienteMsg = null;
var loteResultado = null;    // a última resposta de POST /simulate/painel/disparo-lote
var CANAL_K = { whatsapp:"c_whats", popup:"c_popup", email:"c_email" };
var CANAIS_HUMANOS = ["ligacao_cs"];   // espelho de crai/config.py: nunca é opção de envio

function fonteMsg(){
  if(!ultimaAnalise) return [];
  return ultimaAnalise.clientes.filter(function(c){ return c.criticality === "critico" || c.criticality === "alto"; });
}
function montarSelectMsg(recriar){
  var sel = document.getElementById("selMsgCliente");
  var f = fonteMsg();
  if(recriar) sel.innerHTML = "";
  if(sel.children.length) return;
  f.slice(0, 15).forEach(function(c){
    var o = el("option", null, c.customer_id_externo + " — " + t(NIVEL[c.criticality].k));
    o.value = c.customer_id_externo;
    sel.appendChild(o);
  });
  sel._fonte = f;
  if(!sel._lig){
    sel._lig = true;
    sel.addEventListener("change", function(){
      var c = (sel._fonte || []).find(function(x){ return x.customer_id_externo === sel.value; });
      if(c){ clienteMsg = c; mostrarMensagem(c); }
    });
  }
  if(!clienteMsg && f.length) clienteMsg = f[0];
}
function mostrarSemBase(){
  if(ultimaAnalise){
    document.getElementById("msgDetalhe").innerHTML =
      '<div class="painel pad"><div class="vazio"><div style="color:var(--ok)">' + IC.ok_grande + "</div>" +
      '<div class="t">' + t("msg_ninguem_t") + '</div>' +
      '<p class="sub" style="margin-top:6px;max-width:50ch;margin-inline:auto">' + t("msg_ninguem_d") + "</p>" +
      '<button type="button" class="btn p" style="margin-top:18px" id="irLote">' + t("modo_todos") + "</button></div></div>";
    var bl = document.getElementById("irLote");
    if(bl) bl.addEventListener("click", function(){ trocarModo(false); });
    return;
  }
  document.getElementById("msgDetalhe").innerHTML =
    '<div class="painel pad"><div class="vazio"><div style="color:var(--ink-3)">' + IC.vazio + "</div>" +
    '<div class="t">' + t("msg_sem_base_t") + '</div>' +
    '<p class="sub" style="margin-top:6px;max-width:46ch;margin-inline:auto">' + t("msg_sem_base_d") + "</p>" +
    '<button type="button" class="btn p" style="margin-top:18px" id="irRisco">' + t("ir_risco") + "</button></div></div>";
  var b = document.getElementById("irRisco");
  if(b) b.addEventListener("click", function(){ abrir("risco"); });
}

function moedaDec(v){
  if(typeof v !== "number") return "—";
  return idioma === "pt" ? "R$ " + v.toLocaleString("pt-BR", { minimumFractionDigits:2, maximumFractionDigits:2 })
                         : "R$" + v.toLocaleString("en-US", { minimumFractionDigits:2, maximumFractionDigits:2 });
}
function pctOuTraco(v){ return typeof v === "number" ? Math.round(v * 100) + "%" : "—"; }

/* A taxa de aceite que a Visão geral usa: média, ponderada pelo MRR, da
   p_sucesso da candidata escolhida entre os clientes tratados. Vem da API;
   sem envio ainda, fica null e a Visão geral diz isso. */
function taxaDosTratados(clientes){
  var num = 0, den = 0;
  (clientes || []).forEach(function(c){
    var esc = (c.candidatas || []).find(function(x){ return x.escolhida; });
    if(!esc || typeof esc.p_sucesso !== "number" || typeof c.mrr !== "number") return;
    num += esc.p_sucesso * c.mrr; den += c.mrr;
  });
  return den ? num / den : null;
}

/* Um balão por candidata, com tudo o que a API disse sobre ela. */
function balaoCandidata(x, i){
  var origem = x.origem_texto === "gerado" ? t("origem_gerado") : t("origem_template");
  var h = '<div class="balao' + (x.escolhida ? " escolhido" : "") + '" style="animation-delay:' + (i * 70) + 'ms">' +
    '<div class="balao-cab"><span class="balao-tom">' + esc(x.oferta_label) + "</span>" +
    (x.escolhida ? '<span class="tag o">' + IC.check + " " + t("escolhida_bandit") + "</span>" : "") + "</div>" +
    '<div class="txt">' + esc(x.texto) + "</div>" +
    '<div class="pe" style="flex-wrap:wrap;gap:8px 16px"><span>' + t("aceite_aprendido", { p:pctOuTraco(x.p_sucesso) }) + "</span>" +
    "<span>" + t("retorno_esperado", { v:moedaDec(x.eprofit_amostrado) }) + "</span>" +
    '<span class="tag n">' + esc(origem) + "</span></div>";
  if(x.escolhida && typeof x.alpha === "number" && typeof x.beta === "number"){
    h += '<div class="sub" style="margin-top:10px;font-size:13.5px">' +
      t("posterior_linha", { p:pctOuTraco(x.p_sucesso), a:x.alpha.toLocaleString(loc()), b:x.beta.toLocaleString(loc()), peso:(x.alpha + x.beta).toLocaleString(loc()) }) + "</div>";
  }
  h += '<div class="sub" style="margin-top:6px;font-size:13.5px">' + esc(x.motivo) + "</div></div>";
  return h;
}

/* Um cartão por canal considerado. Escolhido só se a API disse e o canal
   não é humano; canal humano aparece sempre como descartado, com o motivo. */
function cartaoCanal(x){
  var humano = CANAIS_HUMANOS.indexOf(x.canal) !== -1;
  var vence = !!x.escolhido && !humano;
  var nome = CANAL_K[x.canal] ? t(CANAL_K[x.canal]) : x.canal;
  return '<div class="canal' + (vence ? " vence" : "") + '">' +
    (vence ? '<span class="tag o">' + IC.check + " " + t("escolhido") + "</span>" : '<span class="tag n">' + t("descartado") + "</span>") +
    '<div class="n">' + esc(nome) + '</div><div class="d">' + esc(x.motivo) + "</div></div>";
}

/* A decisão completa de um cliente tratado, como veio do lote. */
function painelDecisao(d, aviso){
  var n = NIVEL[d.criticality] || NIVEL.alto;
  var h = '<div class="painel pad" style="margin-bottom:18px">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:6px">' +
    "<h2>" + esc(d.customer_id_externo) + '</h2><span class="tag ' + n.cls + '">' + n.ic + " " + t(n.k) + "</span></div>" +
    '<p class="sub" style="margin-bottom:22px">' + t("decisao_resumo", { oferta:esc(d.offer_label), risco:pctOuTraco(d.risk_score), mrr:moedaDec(d.mrr) }) + "</p>" +
    "<h3>" + t("tres_formas") + '</h3><div style="display:flex;flex-direction:column;gap:12px">' +
    (d.candidatas || []).map(balaoCandidata).join("") + "</div></div>" +
    '<div class="painel pad"><h2>' + t("por_onde") + '</h2><p class="sub" style="margin-bottom:20px">' + t("por_onde_s") + "</p>" +
    '<div class="canais">' + (d.canais_considerados || []).map(cartaoCanal).join("") + "</div>";
  if(d.envio){
    h += '<div class="resultado ' + (d.envio.simulado ? "n" : "o") + '" style="margin-top:22px"><div class="res-in">' +
      '<span class="res-ic" style="background:' + (d.envio.simulado ? "var(--neutro)" : "var(--ok)") + '">' + IC.check + "</span>" +
      '<span class="res-corpo"><span class="t">' + (d.envio.simulado ? t("envio_simulado_t") : t("msg_enviada_t")) + "</span>" +
      '<span class="d">' + esc(aviso || "") + (d.envio.registrado ? " " + t("envio_registrado", { id:d.envio.ciclo_id == null ? "—" : d.envio.ciclo_id }) : " " + t("envio_nao_registrado")) + "</span></span></div></div>";
  }
  return h + "</div>";
}

/* Um cliente pulado pelo lote: motivo e a frase da API. */
function painelPulado(p){
  return '<div class="painel pad"><div class="tag n" style="margin-bottom:12px">' + IC.alerta + " " + esc(t("pulado_" + p.motivo) || p.motivo) + "</div>" +
    "<h2>" + esc(p.customer_id_externo) + '</h2><p class="sub" style="margin-top:8px">' + esc(p.detalhe) + "</p></div>";
}

function painelErroMsg(err){
  var tipo = (err && err.tipo) || "rede", titulo, orient, daApi = "";
  if(tipo === "tempo"){ titulo = t("erro_tempo_t"); orient = t("erro_tempo_d", { s:err.segundos || Math.round(API.TEMPO_LIMITE_MS / 1000) }); }
  else if(tipo === "http"){ titulo = t("erro_http_t"); orient = t("erro_http_d", { status:err.status, rota:err.rota }); daApi = textoDoDetail(err.detail); }
  else if(tipo === "resposta_invalida"){ titulo = t("erro_resposta_t"); orient = t("erro_resposta_d", { rota:err.rota || "" }); }
  else if(tipo === "modelos_desligados"){ titulo = t("erro_modelos_t"); orient = t("erro_modelos_d"); daApi = err.ambiente ? JSON.stringify(err.ambiente) : ""; }
  else { titulo = t("erro_rede_t"); orient = t("erro_rede_d"); daApi = err && err.causa ? String(err.causa) : (err && err.message) || ""; }
  return '<div class="painel pad"><div class="tag f" style="margin-bottom:12px">' + IC.alerta + " " + esc(titulo) + "</div>" +
    '<p class="sub">' + esc(orient) + "</p>" +
    (daApi ? '<pre class="sub" style="white-space:pre-wrap;margin:14px 0 0;padding:12px 14px;border-radius:10px;background:var(--surface-3);font-size:13.5px">' + esc(daApi) + "</pre>" : "") + "</div>";
}

function mostrarMensagem(c){
  if(!c){ mostrarSemBase(); return; }
  var host = document.getElementById("msgDetalhe");
  var n = NIVEL[c.criticality] || NIVEL.alto;

  if(loteResultado && !loteResultado.erro){
    var r = loteResultado;
    var tratado = r.clientes.find(function(x){ return x.customer_id_externo === c.customer_id_externo; });
    if(tratado){ host.innerHTML = painelDecisao(tratado, r.simulado ? r.aviso : ""); return; }
    var pulado = r.pulados.find(function(x){ return x.customer_id_externo === c.customer_id_externo; });
    if(pulado){ host.innerHTML = painelPulado(pulado); return; }
  }

  var cabecalho = '<div style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:6px">' +
    "<h2>" + esc(c.customer_id_externo) + '</h2><span class="tag ' + n.cls + '">' + n.ic + " " + t(n.k) + "</span></div>" +
    '<p class="sub" style="margin-bottom:22px">' + esc(c.explicacao || "") + "</p>";
  if(loteResultado && loteResultado.erro){
    host.innerHTML = '<div class="painel pad" style="margin-bottom:18px">' + cabecalho + "</div>" + painelErroMsg(loteResultado.erro);
    return;
  }
  host.innerHTML = '<div class="painel pad">' + cabecalho +
    '<p class="sub" style="margin-bottom:18px">' + (loteResultado ? t("um_fora_do_lote") : t("um_explica")) + "</p>" +
    '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">' +
    '<button type="button" class="btn p g" id="btnDecidirUm">' + t("decidir_um") + "</button>" +
    '<span class="sub">' + t("envio_simulado") + "</span></div></div>";

  document.getElementById("btnDecidirUm").addEventListener("click", function(){
    var b = this; b.disabled = true; b.textContent = t("lote_enviando");
    executarLote(function(){ if(clienteMsg === c) mostrarMensagem(c); });
  });
}

/* O caso de cobranca recusada que a aba Mensagens esta exibindo, quando o
   usuario chega aqui pelo botao da aba Recuperacao. `null` = modo normal
   (churn voluntario, com a lista de clientes da analise).

   A mensagem do churn INVOLUNTARIO e uma so: o motor de dunning gera um texto,
   com um tom, para o canal escolhido. As tres candidatas com probabilidade
   aprendida existem no churn VOLUNTARIO, onde o bandit compara ofertas. Esta
   tela mostra o que cada pipeline realmente produz, e nao inventa a diferenca. */
var casoMsg = null;

function abrirCasoMsg(c){ casoMsg = c; abrir("mensagens"); }
function sairCasoMsg(){ casoMsg = null; mostrarPainelMsg(); }

/* Mostra/esconde os controles do modo normal enquanto um caso esta aberto. */
function alternarControlesMsg(mostrar){
  var m = document.querySelector(".modo-msg");
  if(m) m.hidden = !mostrar;
  var sel = document.getElementById("selMsgCliente");
  var cx = sel && sel.closest(".painel");
  if(cx) cx.hidden = !mostrar;
  if(!mostrar) document.getElementById("msgTodos").hidden = true;
}

function mostrarPainelMsg(){
  if(casoMsg){ alternarControlesMsg(false); mostrarCaso(casoMsg); return; }
  alternarControlesMsg(true);
  document.getElementById("msgUm").hidden = false;
  if(clienteMsg) mostrarMensagem(clienteMsg); else mostrarSemBase();
}

/* A mensagem que a API gerou para uma cobranca recusada. */
function mostrarCaso(c){
  var d = c.resposta;
  var host = document.getElementById("msgDetalhe");
  var canalK = d.channel && CANAL_INV_K["bot_" + d.channel] ? t(CANAL_INV_K["bot_" + d.channel]) : (d.channel || "—");
  var h = '<div class="painel pad">' +
    '<div style="display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:6px">' +
    "<h2>" + esc(c.entrada.cliente || t("caso_n", { n:c.n })) + "</h2>" +
    '<span class="tag n">' + esc(d.failure_cause_legivel || d.failure_cause || "") + "</span></div>" +
    '<p class="sub" style="margin-bottom:20px">' + t("caso_msg_d", { valor:moedaDec(c.entrada.valor) }) + "</p>";

  h += d.mensagem
    ? '<div class="balao escolhido"><div class="txt">' + esc(d.mensagem) + "</div></div>" +
      '<div class="sub" style="margin-top:12px">' + t("caso_msg_canal", { canal:esc(canalK) }) + "</div>"
    : '<p class="sub">' + t("caso_msg_sem") + "</p>";

  h += '<div class="fonte-dado" style="margin-top:18px"><span>' + IC.alerta + " " + t("caso_msg_nota") + "</span></div>" +
    '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:20px">' +
    '<button type="button" class="btn p" id="voltarRecup">' + t("voltar_recup") + "</button>" +
    '<button type="button" class="btn" id="sairCaso">' + t("sair_caso") + "</button></div></div>";

  host.innerHTML = h;
  document.getElementById("voltarRecup").addEventListener("click", function(){ abrir("recuperar"); });
  document.getElementById("sairCaso").addEventListener("click", sairCasoMsg);
}

var bUm = document.getElementById("modoUm"), bTodos = document.getElementById("modoTodos");
bUm.addEventListener("click", function(){ trocarModo(true); });
bTodos.addEventListener("click", function(){ trocarModo(false); });
function trocarModo(um){
  bUm.setAttribute("aria-pressed", String(um));
  bTodos.setAttribute("aria-pressed", String(!um));
  bUm.classList.toggle("p", um);
  bTodos.classList.toggle("p", !um);
  document.getElementById("msgUm").hidden = !um;
  document.getElementById("msgTodos").hidden = um;
  if(!um) montarLote();
}
function montarLote(){
  var host = document.getElementById("msgTodos");
  var f = fonteMsg();
  if(!ultimaAnalise && !loteResultado){
    host.innerHTML = '<div class="painel pad"><div class="vazio"><div style="color:var(--ink-3)">' + IC.vazio + "</div>" +
      '<div class="t">' + t("msg_sem_base_t") + '</div><p class="sub" style="margin-top:6px;max-width:46ch;margin-inline:auto">' +
      t("msg_sem_base_d") + '</p><button type="button" class="btn p" style="margin-top:18px" id="irRisco2">' + t("ir_risco") + "</button></div></div>";
    var b2 = document.getElementById("irRisco2");
    if(b2) b2.addEventListener("click", function(){ abrir("risco"); });
    return;
  }
  var receita = f.reduce(function(s,c){ return s + (typeof c.mrr === "number" ? c.mrr : 0); }, 0);
  host.innerHTML = '<div class="painel pad"><h2>' + t("lote_t") + '</h2><p class="sub" style="margin-bottom:22px">' + t("lote_s") + "</p>" +
    '<div class="destaques" style="margin-bottom:22px">' +
    '<div class="destaque" style="--cor:var(--atencao)"><div class="rot">' + t("lote_fila") + '</div><div class="big num">' + f.length + '</div><div class="pe">' + t("lote_fila_pe") + "</div></div>" +
    '<div class="destaque" style="--cor:var(--ok)"><div class="rot">' + t("lote_receita") + '</div><div class="big num">' + moeda(receita) + '</div><div class="pe">' + t("lote_receita_pe") + "</div></div></div>" +
    '<button type="button" class="btn p g" id="btnLote">' + t("lote_btn") + '</button><div id="filaLote"></div></div>';
  document.getElementById("btnLote").addEventListener("click", function(){ rodarLote(); });
  if(loteResultado) desenharLote(loteResultado);
}

/* O lote inteiro: sem corpo, a API trata a base importada do tenant do painel.
   Guarda a resposta em `loteResultado` e alimenta a Visão geral. */
function executarLote(aoTerminar){
  API.disparoLote()
    .then(function(r){
      if(!r || !r.resumo || !Array.isArray(r.clientes) || !Array.isArray(r.pulados)) throw API.erro("resposta_invalida", { rota:"/simulate/painel/disparo-lote" });
      loteResultado = r;
      if(r.clientes.length){
        estado.contatados += r.clientes.length;
        var taxa = taxaDosTratados(r.clientes);
        if(taxa != null) estado.taxaAceite = taxa;
        atualizarPontoVisao();
      }
      aoTerminar();
    })
    .catch(function(err){ loteResultado = { erro:err }; aoTerminar(); });
}
function rodarLote(){
  var b = document.getElementById("btnLote");
  if(b){ b.disabled = true; b.textContent = t("lote_enviando"); }
  executarLote(function(){ desenharLote(loteResultado); });
}

function desenharLote(r){
  var b = document.getElementById("btnLote");
  var fila = document.getElementById("filaLote");
  if(!fila) return;
  if(r.erro){
    if(b){ b.disabled = false; b.textContent = t("lote_btn"); }
    fila.innerHTML = '<div style="margin-top:18px">' + painelErroMsg(r.erro) + "</div>";
    return;
  }
  if(b){ b.disabled = false; b.textContent = t("lote_de_novo"); }
  var res = r.resumo;
  var h = "";
  if(r.simulado) h += '<div class="resultado n" style="margin-top:22px"><div class="res-in"><span class="res-ic" style="background:var(--neutro)">' + IC.alerta + "</span>" +
    '<span class="res-corpo"><span class="t">' + t("envio_simulado_t") + '</span><span class="d">' + esc(r.aviso || "") + "</span></span></div></div>";
  h += '<div class="destaques" style="margin:22px 0 18px">' +
    '<div class="destaque" style="--cor:var(--ok)"><div class="rot">' + t("lote_tratados") + '</div><div class="big num">' + res.processados + '</div><div class="pe">' + t("lote_tratados_pe", { mrr:moeda(res.mrr_envolvido || 0) }) + "</div></div>" +
    '<div class="destaque" style="--cor:var(--neutro)"><div class="rot">' + t("lote_pulados") + '</div><div class="big num">' + res.pulados + '</div><div class="pe">' + t("lote_pulados_pe", { n:res.recebidos }) + "</div></div>" +
    '<div class="destaque" style="--cor:var(--acento-viva)"><div class="rot">' + t("lote_por_canal") + '</div><div class="big num" style="font-size:20px">' + esc(Object.keys(res.por_canal || {}).map(function(k){ return (CANAL_K[k] ? t(CANAL_K[k]) : k) + " " + res.por_canal[k]; }).join(" · ") || "—") + '</div><div class="pe">' + esc(Object.keys(res.por_oferta || {}).map(function(k){ return k + " " + res.por_oferta[k]; }).join(" · ")) + "</div></div></div>";

  if(r.clientes.length){
    h += "<h3>" + t("lote_lista_t", { n:r.clientes.length }) + '</h3><div class="fila" id="filaIn">';
    r.clientes.forEach(function(c, i){
      var escolhidoCanal = (c.canais_considerados || []).find(function(x){ return x.escolhido; });
      var escolhida = (c.candidatas || []).find(function(x){ return x.escolhida; });
      h += '<div class="fila-l" style="grid-template-columns:26px 1fr auto auto"><span class="ok-mini">' + IC.check + "</span>" +
        '<span><span class="nome">' + esc(c.customer_id_externo) + '</span><span class="oq" style="display:block">' +
        esc(c.offer_label) + " · " + esc(CANAL_K[c.channel] ? t(CANAL_K[c.channel]) : c.channel) + (escolhidoCanal ? " — " + esc(escolhidoCanal.motivo) : "") +
        (escolhida ? " · " + t("aceite_aprendido", { p:pctOuTraco(escolhida.p_sucesso) }) : "") + "</span></span>" +
        '<span class="tag n">' + t("por_mes", { v:moeda(c.mrr || 0) }) + "</span>" +
        '<button type="button" class="btn" data-ver="' + i + '">' + t("ver_decisao") + "</button></div>";
    });
    h += "</div>";
  } else {
    h += '<div class="vazio" style="padding:36px 16px"><div class="t">' + t("lote_ninguem_t") + '</div><p class="sub" style="margin-top:6px;max-width:52ch;margin-inline:auto">' + t("lote_ninguem_d") + "</p></div>";
  }

  var motivos = Object.keys(res.por_motivo || {});
  if(motivos.length){
    h += '<h3 style="margin-top:22px">' + t("lote_pulados_t", { n:res.pulados }) + "</h3>";
    motivos.forEach(function(m){
      var exemplo = r.pulados.find(function(p){ return p.motivo === m; });
      h += '<div class="fila-l" style="grid-template-columns:1fr auto;margin-top:8px"><span><span class="nome">' + esc(t("pulado_" + m) || m) +
        '</span><span class="oq" style="display:block">' + (exemplo ? esc(exemplo.detalhe) : "") + "</span></span>" +
        '<span class="tag n">' + res.por_motivo[m] + "</span></div>";
    });
  }
  h += '<div id="detalheLote" style="margin-top:22px"></div>';
  fila.innerHTML = h;
  [].slice.call(fila.querySelectorAll("[data-ver]")).forEach(function(btn){
    btn.addEventListener("click", function(){
      var c = r.clientes[+btn.dataset.ver];
      document.getElementById("detalheLote").innerHTML = painelDecisao(c, r.simulado ? r.aviso : "");
      document.getElementById("detalheLote").scrollIntoView({ behavior: reduz ? "auto" : "smooth", block:"start" });
    });
  });
}


/* ═══════════ ABA: VISÃO GERAL ═══════════ */
var dataAlvo = null;
function mesesAte(d){
  var hoje = new Date();
  var m = (d.getFullYear() - hoje.getFullYear()) * 12 + (d.getMonth() - hoje.getMonth());
  return Math.max(1, m);
}
function atualizarPontoVisao(){
  var p = document.getElementById("pontoVisao");
  p.hidden = !estado.analise;
}
function desenharVisao(){
  var host = document.getElementById("visaoCorpo");
  if(!estado.analise){
    host.innerHTML = '<div class="painel pad"><div class="vazio"><div style="color:var(--ink-3)">' + IC.vazio + "</div>" +
      '<div class="t">' + t("visao_vazio_t") + '</div><p class="sub" style="margin-top:6px;max-width:50ch;margin-inline:auto">' +
      t("visao_vazio_d") + '</p><button type="button" class="btn p" style="margin-top:18px" id="irRisco3">' + t("ir_risco") + "</button></div></div>";
    var b = document.getElementById("irRisco3");
    if(b) b.addEventListener("click", function(){ abrir("risco"); });
    return;
  }

  var emRisco = estado.analise.clientes.filter(function(c){ return c.criticality === "critico" || c.criticality === "alto"; });
  var mrrRisco = emRisco.reduce(function(s,c){ return s + (c.mrr || 0); }, 0);
  var taxa = estado.taxaAceite;            // vem da API (aba Mensagens); null até o primeiro envio
  var temTaxa = typeof taxa === "number";
  var mrrSalvo = temTaxa ? mrrRisco * taxa : 0;

  if(!dataAlvo){ dataAlvo = new Date(); dataAlvo.setMonth(dataAlvo.getMonth() + 6); }
  var meses = mesesAte(dataAlvo);
  var acumulado = mrrSalvo * meses + estado.valorRecuperado;

  var iso = dataAlvo.toISOString().slice(0,10);
  var min = new Date(); min.setMonth(min.getMonth() + 1);

  host.innerHTML =
    '<div class="painel pad" style="margin-bottom:18px">' +
      '<div class="proj-cab">' +
        '<div class="campo"><label for="inpData">' + t("proj_data") + '</label>' +
        '<input type="date" id="inpData" value="' + iso + '" min="' + min.toISOString().slice(0,10) + '"></div>' +
        '<div class="proj-atalhos" id="atalhos"></div>' +
      "</div>" +
      '<div class="destaques" style="margin-bottom:0">' +
        '<div class="destaque" style="--cor:var(--atencao)"><div class="rot">' + t("vg_mrr_risco") + '</div>' +
        '<div class="big num">' + moeda(mrrRisco) + '</div><div class="pe">' + t("vg_mrr_risco_pe", { n:emRisco.length }) + "</div></div>" +
        '<div class="destaque" style="--cor:var(--ok)"><div class="rot">' + t("vg_mrr_salvo") + '</div>' +
        '<div class="big num">' + (temTaxa ? moeda(mrrSalvo) : "—") + '</div><div class="pe">' + (temTaxa ? t("vg_mrr_salvo_pe", { pct:Math.round(taxa*100) }) : t("vg_sem_taxa")) + "</div></div>" +
        '<div class="destaque" style="--cor:var(--acento-viva)"><div class="rot">' + t("vg_ganho", { data:dataLonga(dataAlvo) }) + '</div>' +
        '<div class="big num">' + (temTaxa ? moeda(acumulado) : "—") + '</div><div class="pe">' + (temTaxa ? t("vg_ganho_pe", { meses:meses }) : t("vg_sem_taxa")) + "</div></div>" +
      "</div>" +
    "</div>" +
    '<div class="painel pad" style="margin-bottom:18px">' +
      "<h2>" + t("g_proj_t") + '</h2><p class="sub" style="margin-bottom:22px">' + t("g_proj_s") + "</p>" +
      '<div class="g-wrap" id="gProj"></div>' +
      '<div class="fonte-dado"><span><b>' + t("formula_t") + ".</b> " + t("formula_d") + "</span></div>" +
    "</div>" +
    '<div class="painel pad"><h2>' + t("origem_t") + '</h2><p class="sub" style="margin-bottom:18px">' + t("origem_s") + "</p>" +
      '<div class="origem">' +
        og("o_base", estado.rotuloBase + " · " + estado.analise.total.toLocaleString(loc())) +
        og("o_risco", String(emRisco.length)) +
        og("o_mrr", moeda(mrrRisco)) +
        og("o_taxa", temTaxa ? Math.round(taxa * 100) + "%" : t("nenhuma")) +
        og("o_cobr", estado.cobrancasRecusadas ? estado.cobrancasRecusadas + " · " + moeda(estado.valorRecusado) : t("nenhuma")) +
        og("o_recup", estado.faturasRecuperadas ? estado.faturasRecuperadas + " · " + moeda(estado.valorRecuperado) : t("nenhuma")) +
        og("o_contatados", estado.contatados ? String(estado.contatados) : t("nenhum")) +
      "</div></div>";

  var inp = document.getElementById("inpData");
  inp.addEventListener("change", function(){
    var d = new Date(inp.value + "T12:00:00");
    if(!isNaN(d.getTime())){ dataAlvo = d; desenharVisao(); }
  });
  var at = document.getElementById("atalhos");
  [[3,"at_3m"],[6,"at_6m"],[12,"at_12m"]].forEach(function(a){
    var b = el("button","atalho", t(a[1]));
    b.type = "button";
    b.setAttribute("aria-pressed", String(meses === a[0]));
    b.addEventListener("click", function(){
      var d = new Date(); d.setMonth(d.getMonth() + a[0]); dataAlvo = d; desenharVisao();
    });
    at.appendChild(b);
  });

  if(temTaxa) desenharProjecao(mrrSalvo, meses, estado.valorRecuperado);
  else document.getElementById("gProj").innerHTML = '<div class="vazio" style="padding:36px 16px"><div class="t">' + t("vg_sem_taxa_t") + '</div><p class="sub" style="margin-top:6px;max-width:52ch;margin-inline:auto">' + t("vg_sem_taxa_d") + "</p></div>";
}
function og(k, valor){
  return '<div class="origem-l"><span>' + t(k) + '<span class="de">' + t(k + "_de") + "</span></span>" +
    '<span class="v">' + valor + "</span></div>";
}

function desenharProjecao(mrrSalvo, meses, base){
  var host = document.getElementById("gProj");
  if(!host) return;
  var n = Math.min(meses, 24);
  var vals = [], rot = [], hoje = new Date();
  for(var i = 1; i <= n; i++){
    vals.push(base + mrrSalvo * i);
    var d = new Date(hoje.getFullYear(), hoje.getMonth() + i, 1);
    rot.push(dataCurta(d));
  }
  var W = 640, H = 240, ml = 10, mr = 10, mt = 18, mb = 34;
  var iw = W - ml - mr, ih = H - mt - mb;
  var max = vals[vals.length - 1] * 1.1 || 1;
  var bw = iw / n;
  var ac = corDe("--acento-viva"), ok = corDe("--ok"), ink3 = corDe("--ink-3"), ln = corDe("--line");

  var svg = '<svg class="grafico" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" style="height:240px" role="img" aria-label="' + t("g_proj_alt") + '">';
  [0.5, 1].forEach(function(f){
    var y = mt + ih - f * ih * .88;
    svg += '<line x1="' + ml + '" y1="' + y.toFixed(1) + '" x2="' + (W - mr) + '" y2="' + y.toFixed(1) + '" stroke="' + ln + '" stroke-width="1"/>';
  });
  vals.forEach(function(v, i){
    var h = (v / max) * ih;
    var x = ml + i * bw + bw * .16;
    var w = bw * .68;
    var y = mt + ih - h;
    var ult = i === n - 1;
    svg += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + w.toFixed(1) + '" height="' + Math.max(2, h).toFixed(1) +
      '" rx="4" fill="' + (ult ? ok : ac) + '" opacity="' + (ult ? 1 : .55 + .45 * (i / n)) + '"></rect>';
  });
  var passo = n > 12 ? 3 : (n > 6 ? 2 : 1);
  rot.forEach(function(r, i){
    if(i % passo !== 0 && i !== n - 1) return;
    svg += '<text x="' + (ml + i * bw + bw / 2).toFixed(1) + '" y="' + (H - 12) + '" text-anchor="middle" font-size="11" font-family="Inter,sans-serif" fill="' + ink3 + '">' + r + "</text>";
  });
  svg += "</svg>" + '<div class="g-dica" id="dicaP"></div>';
  host.innerHTML = svg;

  var dica = document.getElementById("dicaP");
  var s = host.querySelector("svg");
  s.addEventListener("mousemove", function(ev){
    var r = s.getBoundingClientRect();
    var i = Math.floor(((ev.clientX - r.left) / r.width) * n);
    i = Math.max(0, Math.min(n - 1, i));
    dica.innerHTML = "<b>" + moeda(vals[i]) + "</b><span>" + rot[i] + " · " + (i + 1) + " " + (i === 0 ? t("mes") : t("meses")) + "</span>";
    dica.style.left = Math.min(r.width - 140, Math.max(0, ((ml + i * bw + bw / 2) / W) * r.width - 60)) + "px";
    dica.style.top = Math.max(0, ((mt + ih - (vals[i] / max) * ih) / H) * r.height - 62) + "px";
    dica.style.opacity = "1";
  });
  s.addEventListener("mouseleave", function(){ dica.style.opacity = "0"; });
}

/* ═══════════ INÍCIO ═══════════ */
aplicarIdioma();
mostrarSemBase();
atualizarPontoVisao();
window.addEventListener("resize", function(){
  if(document.querySelector('[data-tela="visao"]').classList.contains("on")) desenharVisao();
});
})();

/* api.js — ligação do painel CRAI com a API.

   O JavaScript desenha, o Python calcula. Tudo o que a tela mostra de risco,
   criticidade, explicação, régua e relatório de importação vem daqui.

   Uma única fonte de dados: a API, na mesma origem que serve este painel.
   As fixtures de painel/fixtures/ NÃO são fonte de dados em tempo de execução;
   servem para conferir a forma da resposta antes de escrever código. Nenhum
   caminho abaixo cai nelas quando a API falha, demora ou devolve erro: a
   promessa é rejeitada com um erro tipado, e a tela mostra estado de erro —
   nunca dado de fixture, nunca dado antigo, nunca zero.

   Reaproveitável pelo site: só depende de fetch. */

var API = (function(){
  "use strict";

  var BASE = "";              // mesma origem: o painel é servido pelo FastAPI
  var TEMPO_LIMITE_MS = 60000;

  /* Erro tipado. `tipo` é um de:
       rede              — fetch não completou (servidor fora, CORS, offline)
       tempo             — passou de TEMPO_LIMITE_MS sem resposta
       http              — status fora de 2xx; `status` e `detail` (o JSON da API)
       resposta_invalida — 2xx, mas o corpo não é o JSON esperado
       modelos_desligados, nada_importado — levantados pela tela a partir de
                           respostas válidas (ver render.js) */
  function erro(tipo, extra){
    var e = new Error(tipo);
    e.tipo = tipo;
    if(extra) for(var k in extra) e[k] = extra[k];
    return e;
  }

  function pedir(rota, opcoes){
    var controlador = new AbortController();
    var relogio = setTimeout(function(){ controlador.abort(); }, TEMPO_LIMITE_MS);
    var o = opcoes || {};
    o.signal = controlador.signal;
    o.headers = o.headers || {};
    o.headers["Accept"] = "application/json";
    return fetch(BASE + rota, o).then(function(r){
      return r.text().then(function(corpo){
        var json = null;
        try{ json = corpo ? JSON.parse(corpo) : null; }catch(e){ json = null; }
        if(!r.ok) throw erro("http", { status:r.status, rota:rota, detail: json && json.detail !== undefined ? json.detail : corpo });
        if(json === null || typeof json !== "object") throw erro("resposta_invalida", { rota:rota, corpo:corpo });
        return json;
      });
    }, function(e){
      throw erro(e && e.name === "AbortError" ? "tempo" : "rede", { rota:rota, causa:String(e && e.message || e), segundos: TEMPO_LIMITE_MS / 1000 });
    }).finally(function(){ clearTimeout(relogio); });
  }

  /* GET /simulate/painel/ambiente → {env, modelos, llm} */
  function ambiente(){ return pedir("/simulate/painel/ambiente"); }

  /* POST /simulate/painel/importar (multipart, campo `arquivo`).
     `mapeamento` ({coluna_no_arquivo: campo}) só é enviado quando informado. */
  function importar(arquivo, mapeamento){
    var fd = new FormData();
    fd.append("arquivo", arquivo, arquivo.name);
    if(mapeamento) fd.append("mapeamento", JSON.stringify(mapeamento));
    return pedir("/simulate/painel/importar", { method:"POST", body:fd });
  }

  /* GET /simulate/painel/insights → {total_clientes, clientes_em_risco, gerado_em, filtros} */
  function insights(){ return pedir("/simulate/painel/insights"); }

  /* POST /simulate/painel/cobranca-falhada → roda o grafo do churn involuntário
     e devolve a decisão: causa, score, e-Profit, estratégia, SHAP, plano,
     raciocínio, mensagem, canal e canais_considerados. */
  function cobrancaFalhada(entrada){
    return pedir("/simulate/painel/cobranca-falhada", {
      method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify(entrada || {}) });
  }

  /* POST /simulate/painel/disparo-lote → o backend decide oferta, mensagem e
     canal para cada cliente que precisa, e registra o envio (simulado).
     Sem `clientes`, o lote é a base importada do tenant do painel; com uma
     lista, só aqueles. `limite` corta os N piores depois da ordenação. */
  function disparoLote(clientes, limite){
    var corpo = {};
    if(clientes && clientes.length) corpo.clientes = clientes;
    if(limite) corpo.limite = limite;
    return pedir("/simulate/painel/disparo-lote", {
      method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify(corpo) });
  }

  /* Busca um CSV de painel/exemplos/ (servido junto com o painel) e devolve um
     File, para entrar no MESMO caminho do arquivo do usuário: importar(). */
  function exemplo(nome){
    var rota = "exemplos/" + nome;
    return fetch(rota).then(function(r){
      if(!r.ok) throw erro("http", { status:r.status, rota:rota, detail:"" });
      return r.blob();
    }, function(e){
      throw erro("rede", { rota:rota, causa:String(e && e.message || e) });
    }).then(function(b){ return new File([b], nome, { type:"text/csv" }); });
  }

  return { ambiente:ambiente, importar:importar, insights:insights, cobrancaFalhada:cobrancaFalhada, disparoLote:disparoLote, exemplo:exemplo, erro:erro, TEMPO_LIMITE_MS:TEMPO_LIMITE_MS };
})();

"""demo_visual.py — demonstração VISUAL do onboarding self-service da CRAI.

O QUE ESTE SCRIPT FAZ. Ele não simula nada: ele executa os módulos reais do
pacote — `churn_voluntary.importacao` (Sprint 2), `batch_scoring` (Sprint 3) e
`insights_unificados` (Sprint 4) — sobre uma base de clientes de exemplo, e
escreve o resultado num arquivo HTML que abre no navegador.

POR QUE ELE EXISTE. A rota `GET /insights` exige um JWT do Supabase válido, e o
projeto Supabase pode ainda não estar criado. A autenticação é uma camada de
BORDA (`accounts/auth.py`), não do motor: chamando as funções direto, a
demonstração exercita exatamente o mesmo código de análise que a rota chamaria,
sem depender de nenhum serviço externo.

COMO RODAR (a partir da pasta `crai/`):

    python demo/demo_visual.py

Gera `demo_insights.html` na mesma pasta e imprime o caminho. Abra no navegador.
O banco fica em `demo_clientes.db` (SQLite, apagado e recriado a cada execução).
"""

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "demo_clientes.db"
SAIDA = BASE / "demo_insights.html"
CSV = BASE / "demo_base_clientes.csv"
TENANT = "demo_banca"

# SQLite em vez do Postgres do Supabase: `clientes_importados._destino()` lê
# estas envs a cada chamada, e sem nenhuma das duas ele falha alto de propósito.
os.environ["CRAI_CLIENTES_DB"] = str(DB)
os.environ.pop("SUPABASE_DB_URL", None)      # Postgres venceria se estivesse setada
sys.path.insert(0, str(BASE.parent / "app"))   # pasta que CONTÉM o pacote crai/

from crai.churn_voluntary import (          # noqa: E402
    batch_scoring, importacao, insights_unificados,
)

# -- Base de exemplo ----------------------------------------------------------
# Empresa fictícia. Os números foram escolhidos para exercitar os quatro
# desfechos que o motor produz: crítico por risco, crítico por valor da conta,
# alto, padrão, e "dado_insuficiente" (as duas últimas linhas, sem sinal de
# atividade nenhum — o caso em que o sistema se recusa a inventar um risco).
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

ROTULO = {
    "critico": "Crítico",
    "alto": "Alto",
    "padrao": "Padrão",
    "dado_insuficiente": "Sem dado",
}
# Status, não série categórica: cada cor vem sempre acompanhada do rótulo em
# texto e do número, nunca sozinha.
COR = {
    "critico": "#f87171",
    "alto": "#ffb86c",
    "padrao": "#5fd39b",
    "dado_insuficiente": "#9aa0a6",
}


def _brl(valor) -> str:
    if valor is None:
        return "—"
    return f"R$ {valor:,.0f}".replace(",", ".")


def _pct(risco) -> str:
    return "—" if risco is None else f"{risco * 100:.0f}<span class='pc'>%</span>"


def _esc(texto) -> str:
    return (str(texto).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _linha_html(i: int, c: dict) -> str:
    crit = c["criticality"]
    risco = c["risk_score"]
    largura = 0 if risco is None else round(risco * 100)
    return f"""
    <tr>
      <td class="pos">{i}</td>
      <td>
        <div class="cid">{_esc(c['customer_id_externo'])}</div>
        <div class="meta">{_esc(c.get('billing_profile') or '—')} · origem
          <span class="origem">{_esc(c.get('origem', 'upload'))}</span></div>
      </td>
      <td class="num">{_brl(c.get('mrr'))}</td>
      <td class="riscocel">
        <div class="riscowrap">
          <div class="track"><div class="fill" style="width:{largura}%"></div></div>
          <span class="riscoval">{_pct(risco)}</span>
        </div>
      </td>
      <td><span class="badge" style="--c:{COR[crit]}">
            <span class="dot"></span>{ROTULO[crit]}</span></td>
      <td class="exp">{_esc(c['explicacao'])}</td>
    </tr>"""


def montar_html(ranking: list, relatorio: dict) -> str:
    total = len(ranking)
    criticos = [c for c in ranking if c["criticality"] == "critico"]
    sem_dado = [c for c in ranking if c["criticality"] == "dado_insuficiente"]
    mrr_risco = sum(c.get("mrr") or 0 for c in ranking
                    if c["criticality"] in ("critico", "alto"))
    linhas = "".join(_linha_html(i, c) for i, c in enumerate(ranking, 1))

    return f"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CRAI — Insights de churn voluntário</title>
<style>
  :root {{
    --bg:#1a120a; --surface:#241a10; --surface2:#2e2215; --line:#3d2d1c;
    --ink:#f5ece1; --ink2:#c9b9a6; --ink3:#94836f; --accent:#ef9311;
    --accent2:#ffb86c;
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    -webkit-font-smoothing:antialiased; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:40px 28px 72px; }}
  header {{ display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;
    padding-bottom:22px; border-bottom:1px solid var(--line); }}
  .logo {{ font-size:26px; font-weight:700; letter-spacing:-.02em;
    color:var(--accent2); }}
  .sub {{ color:var(--ink2); font-size:15px; }}
  .tenant {{ margin-left:auto; font-size:13px; color:var(--ink3);
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
    gap:14px; margin:26px 0 30px; }}
  .tile {{ background:var(--surface); border:1px solid var(--line);
    border-radius:12px; padding:18px 20px; }}
  .tile .n {{ font-size:34px; font-weight:700; letter-spacing:-.03em;
    line-height:1.1; }}
  .tile .l {{ font-size:13px; color:var(--ink2); margin-top:5px; }}
  .tile .h {{ font-size:12px; color:var(--ink3); margin-top:8px; }}
  h2 {{ font-size:17px; font-weight:650; margin:0 0 4px; }}
  .h2sub {{ color:var(--ink3); font-size:13px; margin-bottom:14px; }}
  .tablewrap {{ overflow-x:auto; border:1px solid var(--line);
    border-radius:12px; background:var(--surface); }}
  table {{ width:100%; border-collapse:collapse; min-width:920px; }}
  th {{ text-align:left; font-size:12px; font-weight:600; color:var(--ink3);
    text-transform:uppercase; letter-spacing:.05em; padding:13px 16px;
    border-bottom:1px solid var(--line); white-space:nowrap; }}
  td {{ padding:14px 16px; border-bottom:1px solid rgba(61,45,28,.55);
    vertical-align:middle; }}
  tr:last-child td {{ border-bottom:none; }}
  tr:hover td {{ background:var(--surface2); }}
  .pos {{ color:var(--ink3); font-size:13px; width:42px;
    font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .cid {{ font-weight:600; font-size:14px; }}
  .meta {{ font-size:12px; color:var(--ink3); margin-top:2px; }}
  .origem {{ color:var(--ink2); }}
  .num {{ font-variant-numeric:tabular-nums; white-space:nowrap;
    font-size:14px; }}
  .riscocel {{ width:190px; }}
  .riscowrap {{ display:flex; align-items:center; gap:10px; }}
  .track {{ flex:1; height:7px; background:rgba(245,236,225,.10);
    border-radius:4px; overflow:hidden; min-width:80px; }}
  /* A barra codifica MAGNITUDE (o risco), numa cor só. A criticidade é
     STATUS e mora no selo ao lado, com rótulo — nunca cor sozinha. Misturar
     as duas na mesma marca fazia 73% em verde parecer menor que 28% em
     vermelho. */
  .fill {{ height:100%; border-radius:4px; background:var(--accent2); }}
  .riscoval {{ font-variant-numeric:tabular-nums; font-size:14px;
    font-weight:600; min-width:38px; text-align:right; }}
  .pc {{ font-size:11px; color:var(--ink3); font-weight:400; margin-left:1px; }}
  .badge {{ display:inline-flex; align-items:center; gap:6px; font-size:12.5px;
    font-weight:600; padding:4px 10px 4px 8px; border-radius:20px;
    background:color-mix(in srgb,var(--c) 15%,transparent);
    border:1px solid color-mix(in srgb,var(--c) 38%,transparent);
    color:var(--c); white-space:nowrap; }}
  .dot {{ width:6px; height:6px; border-radius:50%; background:var(--c);
    flex:none; }}
  .exp {{ color:var(--ink2); font-size:13px; max-width:400px; }}
  .rodape {{ margin-top:26px; padding:16px 20px; background:var(--surface);
    border:1px solid var(--line); border-radius:12px; color:var(--ink2);
    font-size:13px; line-height:1.65; }}
  .rodape b {{ color:var(--ink); font-weight:600; }}
  code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
    font-size:12.5px; color:var(--accent2); }}
</style></head><body><div class="wrap">

<header>
  <span class="logo">CRAI</span>
  <span class="sub">Insights de churn voluntário</span>
  <span class="tenant">tenant: {TENANT}</span>
</header>

<div class="tiles">
  <div class="tile"><div class="n">{total}</div>
    <div class="l">clientes na base</div>
    <div class="h">{relatorio['importados']} importados da planilha</div></div>
  <div class="tile"><div class="n" style="color:{COR['critico']}">{len(criticos)}</div>
    <div class="l">em risco crítico</div>
    <div class="h">por risco alto ou valor da conta</div></div>
  <div class="tile"><div class="n">{_brl(mrr_risco)}</div>
    <div class="l">MRR em risco</div>
    <div class="h">soma de crítico + alto</div></div>
  <div class="tile"><div class="n" style="color:{COR['dado_insuficiente']}">{len(sem_dado)}</div>
    <div class="l">sem dado suficiente</div>
    <div class="h">não avaliados, em vez de risco zero</div></div>
</div>

<h2>Ranking de risco</h2>
<div class="h2sub">Ordenado por risco decrescente. Clientes sem sinal de
  atividade ficam no fim — o sistema não atribui risco a eles.</div>

<div class="tablewrap"><table>
  <thead><tr>
    <th>#</th><th>Cliente</th><th>MRR</th><th>Risco</th>
    <th>Criticidade</th><th>Por quê</th>
  </tr></thead>
  <tbody>{linhas}</tbody>
</table></div>

<div class="rodape">
  <b>Esta página é saída real do sistema, não uma maquete.</b> Os números vieram
  de <code>importacao.importar()</code> (Sprint 2), <code>batch_scoring</code>
  (Sprint 3) e <code>insights_unificados.clientes_em_risco()</code> (Sprint 4),
  executados sobre a planilha <code>demo_base_clientes.csv</code>. É o mesmo
  código que a rota <code>GET /insights</code> chama — sem a camada de
  autenticação, que exige o projeto Supabase configurado.
</div>

</div></body></html>"""


def main() -> None:
    if DB.exists():
        DB.unlink()

    CSV.write_text(BASE_CSV, encoding="utf-8")
    print(f"[1/3] Planilha de exemplo escrita em {CSV.name}")

    relatorio = importacao.importar(TENANT, CSV.name, CSV.read_bytes())
    print(f"[2/3] Importação: {relatorio['importados']} clientes, "
          f"{len(relatorio['rejeitados'])} rejeitados, "
          f"{relatorio['linhas_sem_dado_comportamental']} sem dado comportamental")

    ranking = insights_unificados.clientes_em_risco(TENANT)
    criticos = sum(1 for c in ranking if c["criticality"] == "critico")
    print(f"[3/3] Análise: {len(ranking)} clientes pontuados, {criticos} críticos")

    SAIDA.write_text(montar_html(ranking, relatorio), encoding="utf-8")
    print(f"\nPronto -> {SAIDA}")
    print("Abra esse arquivo no navegador.")


if __name__ == "__main__":
    main()

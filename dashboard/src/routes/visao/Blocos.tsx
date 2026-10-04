import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  IconAlert,
  IconArrowRight,
  IconCheck,
  IconClock,
  IconDownload,
  IconPulse,
  IconSend,
  IconShield,
  IconUndo,
  IconUsers,
  IconX,
} from '../../components/icons/Icons'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { MOSTRAR_DEMONSTRACAO, agoraDaTela } from '../../data/api'
import { AGORA } from '../../data/mock'
import type { Atividade, Funil, ItemDesempenho, LinhaExtrato, OQueFunciona, SaudeSistema, TipoAtividade } from '../../data/tipos'
import { baixarCsv } from '../../lib/csv'
import { cx } from '../../lib/cx'
import { fmt } from '../../lib/format'

const pct = (v: number) => `${Math.round(v * 100)}%`

/** Cabeçalho padrão de cartão: título e uma linha de apoio. */
export function CabecalhoCartao({ titulo, apoio, direita }: { titulo: string; apoio?: ReactNode; direita?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h3 className="t-h3 text-paper">{titulo}</h3>
        {apoio ? <p className="t-apoio mt-1 text-silver">{apoio}</p> : null}
      </div>
      {direita}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Funil do involuntário                                               */
/* ------------------------------------------------------------------ */

export function FunilInvoluntario({ funil }: { funil: Funil }) {
  const max = Math.max(...funil.etapas.map((e) => e.chegaram), 1)
  const { recuperados, encerrados, em_andamento } = funil.desfecho
  return (
    <div>
      <CabecalhoCartao
        titulo="Funil de recuperação em setembro"
        apoio="Quantas cobranças chegaram a cada etapa e quanto voltou em cada uma. O valor é o total das cobranças naquela etapa."
      />
      <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1fr)_280px]">
        <ol className="flex flex-col gap-5">
          {funil.etapas.map((e, i) => (
            <li key={e.etapa}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-apoio font-[560] text-paper">
                  {i > 0 ? <span className="tabular mr-2 text-muted">{i}</span> : null}
                  {e.rotulo}
                </span>
                <span className="tabular text-rotulo text-silver">
                  <span className="font-[600] text-paper">{e.chegaram}</span> · {fmt.brlInteiro(e.valor)}
                </span>
              </div>
              <div className="mt-1.5 h-2 w-full rounded-full bg-paper/[0.06]" aria-hidden="true">
                <div className="h-2 rounded-full" style={{ width: `${(e.chegaram / max) * 100}%`, background: 'var(--color-serie-inv)' }} />
              </div>
              {e.recuperados_aqui > 0 ? (
                <div className="t-label mt-1.5 flex items-center gap-1.5 text-silver">
                  <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden="true" />
                  {e.recuperados_aqui} {e.recuperados_aqui === 1 ? 'recuperada' : 'recuperadas'} aqui ·{' '}
                  <span className="tabular text-paper">{fmt.brlInteiro(e.valor_recuperado_aqui)}</span>
                </div>
              ) : null}
            </li>
          ))}
        </ol>
        <div className="lg:border-l lg:border-line lg:pl-8">
          <div className="t-label mb-3 text-silver">Desfecho das {funil.etapas[0]?.chegaram ?? 0} cobranças</div>
          <div className="flex flex-wrap gap-2 lg:flex-col">
            <Desfecho ponto="bg-ok" numero={recuperados} rotulo="Recuperadas" />
            <Desfecho ponto="bg-graphite" numero={encerrados} rotulo="Encerradas sem recuperação" />
            <Desfecho ponto="bg-orange pulse-orange" numero={em_andamento} rotulo="Em andamento" />
          </div>
          <p className="t-label mt-4 text-silver">
            A mensagem só sai depois de a 3ª tentativa falhar, ou antes, quando o cliente revoga a autorização.
          </p>
        </div>
      </div>
    </div>
  )
}

function Desfecho({ ponto, numero, rotulo }: { ponto: string; numero: number; rotulo: string }) {
  return (
    <div className="min-w-[92px] flex-1 rounded-[10px] border border-line bg-ink/25 px-3 py-2.5">
      <div className="text-[20px] leading-none font-[660] tracking-[-0.02em] text-paper">{numero}</div>
      <div className="t-label mt-1.5 flex items-start gap-1.5 text-silver">
        <span className={cx('mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full', ponto)} aria-hidden="true" />
        {rotulo}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* O que mais funciona                                                 */
/* ------------------------------------------------------------------ */

const POUCOS_CASOS = 5

export function OQueMaisFunciona({ dados, comVoluntario }: { dados: OQueFunciona; comVoluntario: boolean }) {
  const causaTop = [...dados.causas].sort((a, b) => b.casos - a.casos)[0]
  const ofertaTop = [...dados.ofertas].filter((o) => o.casos >= POUCOS_CASOS).sort((a, b) => b.taxa - a.taxa)[0]
  const canais = [...dados.canais].sort((a, b) => b.taxa - a.taxa)
  const vezes = canais.length > 1 && canais[canais.length - 1].taxa > 0 ? canais[0].taxa / canais[canais.length - 1].taxa : null
  return (
    <div>
      <CabecalhoCartao
        titulo="O que mais funciona"
        apoio="Últimos 30 dias. Com poucos casos, a porcentagem ainda muda muito; o selo avisa."
      />
      <div className={cx('mt-5 grid gap-6', comVoluntario ? 'md:grid-cols-3' : 'md:grid-cols-2')}>
        <Bloco
          titulo="Causas de falha"
          conclusao={causaTop ? `${causaTop.rotulo} é a causa mais comum, e ${pct(causaTop.taxa)} dessas cobranças voltaram.` : ''}
          itens={dados.causas}
          cor="var(--color-serie-inv)"
          medida="recuperadas"
        />
        {comVoluntario ? (
          <Bloco
            titulo="Ofertas"
            conclusao={ofertaTop ? `${ofertaTop.rotulo} é a oferta mais aceita.` : ''}
            itens={dados.ofertas}
            cor="var(--color-serie-vol)"
            medida="aceitaram"
          />
        ) : null}
        <Bloco
          titulo="Canais"
          conclusao={
            vezes
              ? `${canais[0].rotulo} tem ${vezes.toFixed(1).replace('.', ',')} vezes mais resposta que ${canais[canais.length - 1].rotulo}.`
              : ''
          }
          itens={dados.canais}
          cor="var(--color-silver)"
          medida="responderam"
        />
      </div>
    </div>
  )
}

function Bloco({
  titulo,
  conclusao,
  itens,
  cor,
  medida,
}: {
  titulo: string
  conclusao: string
  itens: ItemDesempenho[]
  cor: string
  medida: string
}) {
  return (
    <section className="min-w-0">
      <h4 className="t-label font-[600] tracking-[0.02em] text-silver">{titulo}</h4>
      <p className="mt-1.5 min-h-[42px] text-apoio leading-[1.5] text-paper">{conclusao}</p>
      <ul className="mt-3 flex flex-col gap-3">
        {itens.map((it) => (
          <li key={it.rotulo}>
            <div className="flex items-baseline justify-between gap-2 text-rotulo">
              <span className="truncate text-paper" title={it.rotulo}>
                {it.rotulo}
              </span>
              <span className="tabular shrink-0 font-[600] text-paper">{pct(it.taxa)}</span>
            </div>
            <div className="mt-1 h-1.5 w-full rounded-full bg-paper/[0.06]" aria-hidden="true">
              <div className="h-1.5 rounded-full" style={{ width: `${Math.max(it.taxa * 100, it.taxa > 0 ? 3 : 0)}%`, background: cor }} />
            </div>
            <div className="t-label mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-muted">
              <span className="text-silver">
                {medida.charAt(0).toUpperCase() + medida.slice(1)} · {it.casos} {it.casos === 1 ? 'caso' : 'casos'}
              </span>
              {it.casos < POUCOS_CASOS ? <Badge className="px-1.5 py-0 text-rotulo">Poucos casos</Badge> : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

/* ------------------------------------------------------------------ */
/* Atividade recente                                                   */
/* ------------------------------------------------------------------ */

const TIPO: Record<TipoAtividade, { Icone: typeof IconCheck; classe: string; rotulo: string }> = {
  recuperado: { Icone: IconCheck, classe: 'bg-ok/15 text-ok', rotulo: 'Recuperado' },
  oferta_aceita: { Icone: IconUsers, classe: 'bg-serie-vol/20 text-[#5cc9b5]', rotulo: 'Oferta aceita' },
  tentativa_falhou: { Icone: IconX, classe: 'bg-danger/15 text-[#f08a80]', rotulo: 'Tentativa não passou' },
  mensagem_enviada: { Icone: IconSend, classe: 'bg-paper/[0.07] text-silver', rotulo: 'Mensagem enviada' },
  escolha: { Icone: IconClock, classe: 'bg-orange/15 text-orange', rotulo: 'Aguardando você' },
  risco_grave: { Icone: IconAlert, classe: 'bg-danger/15 text-[#f08a80]', rotulo: 'Risco grave' },
  estorno: { Icone: IconUndo, classe: 'bg-paper/[0.07] text-silver', rotulo: 'Estorno' },
}

export function AtividadeRecente({ itens }: { itens: Atividade[] }) {
  return (
    <div className="flex h-full flex-col">
      <CabecalhoCartao titulo="Atividade recente" apoio="O que o sistema fez, do mais novo ao mais antigo." />
      {itens.length === 0 ? (
        <p className="t-apoio mt-8 text-center text-silver">Nada aconteceu ainda nos últimos 30 dias.</p>
      ) : (
        <ol className="mt-4 flex flex-col">
          {itens.map((a, i) => {
            const t = TIPO[a.tipo]
            return (
              <li key={a.id} className="relative flex gap-3 pb-4">
                {i < itens.length - 1 ? <span className="absolute top-9 bottom-1 left-[15px] w-px bg-line" aria-hidden="true" /> : null}
                <span className={cx('flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px]', t.classe)}>
                  <t.Icone width={16} height={16} />
                  <span className="sr-only">{t.rotulo}</span>
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-apoio leading-[1.45] text-paper">
                    {a.simulado ? (
                      <Badge tone="amber" className="mr-1.5 align-[1px] px-1.5 py-0 text-rotulo">
                        Demonstração
                      </Badge>
                    ) : null}
                    {a.texto}
                  </p>
                  <div className="t-label mt-0.5 flex items-center gap-2 text-silver">
                    <span>{fmt.relativo(a.em, AGORA).replace(/^./, (c) => c.toUpperCase())}</span>
                    {a.valor ? (
                      <>
                        <span className="text-muted">·</span>
                        <span className="tabular text-paper">+ {fmt.brl(a.valor)}</span>
                      </>
                    ) : null}
                  </div>
                </div>
              </li>
            )
          })}
        </ol>
      )}
      <Link
        to="/involuntario"
        className="t-label mt-auto inline-flex items-center gap-1.5 self-start rounded-[6px] font-[560] text-silver transition-colors hover:text-paper"
      >
        Ver todos os clientes em recuperação <IconArrowRight width={14} height={14} />
      </Link>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Saúde do sistema                                                    */
/* ------------------------------------------------------------------ */

export function saudeGeral(s: SaudeSistema | null): 'ok' | 'atencao' | 'carregando' {
  if (!s) return 'carregando'
  const tudoOk = s.relogio.ativo && s.modelos.carregados === s.modelos.total && s.redator.disponivel
  return tudoOk ? 'ok' : 'atencao'
}

export function SaudeDoSistema({ saude }: { saude: SaudeSistema | null }) {
  const geral = saudeGeral(saude)
  // As linhas que ainda são fictícias só ganham a etiqueta com a variável ligada (api.ts).
  const demo = MOSTRAR_DEMONSTRACAO ? (saude?.demonstracao ?? []) : []
  const linhas: { ok: boolean; rotulo: string; detalhe: string; demo?: boolean }[] = saude
    ? [
        {
          ok: saude.relogio.ativo,
          rotulo: 'Relógio das tentativas',
          detalhe: saude.relogio.ativo
            ? saude.relogio.ultima_passagem
              ? `Ativo. Última passagem ${fmt.relativo(saude.relogio.ultima_passagem, agoraDaTela()) === 'agora' ? 'há menos de 1 min' : fmt.relativo(saude.relogio.ultima_passagem, agoraDaTela())}.`
              : 'Ativo. Aguardando a primeira passagem.'
            : 'Parado. As tentativas agendadas não estão saindo.',
        },
        {
          ok: saude.modelos.carregados === saude.modelos.total,
          rotulo: 'Decisões automáticas',
          detalhe: `${saude.modelos.carregados} de ${saude.modelos.total} modelos carregados.`,
          demo: demo.includes('modelos'),
        },
        {
          ok: saude.redator.disponivel,
          rotulo: 'Redator de mensagens',
          detalhe: saude.redator.disponivel ? 'Disponível.' : 'Fora do ar; o sistema usa os textos de reserva.',
          demo: demo.includes('redator'),
        },
        {
          ok: saude.base !== null,
          rotulo: 'Base de clientes',
          detalhe: saude.base
            ? `Atualizada ${saude.base.origem === 'api' ? 'pela API' : 'por anexo'} ${fmt.relativo(saude.base.atualizada_em, AGORA)}.`
            : 'Nenhuma base enviada ainda.',
          demo: demo.includes('base'),
        },
      ]
    : []
  return (
    <div id="saude" className="flex h-full scroll-mt-6 flex-col">
      <CabecalhoCartao
        titulo="Saúde do sistema"
        apoio="Se algo parar, é aqui que você fica sabendo."
        direita={<SeloSaude geral={geral} />}
      />
      <ul className="mt-5 grid gap-3 sm:grid-cols-2">
        {linhas.map((l) => (
          <li key={l.rotulo} className="flex items-start gap-3 rounded-[12px] border border-line bg-ink/25 px-4 py-3.5">
            <span className={cx('mt-[7px] h-2 w-2 shrink-0 rounded-full', l.ok ? 'bg-ok' : 'bg-danger')} aria-hidden="true" />
            <div className="min-w-0">
              <div className="text-apoio font-[560] text-paper">
                {l.rotulo}
                {l.demo ? <Badge tone="amber" className="ml-2 px-1.5 py-0 text-rotulo">Demonstração</Badge> : null}
                <span className="sr-only">{l.ok ? ': funcionando' : ': com problema'}</span>
              </div>
              <div className="t-label text-silver">{l.detalhe}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function SeloSaude({ geral, compacto = false }: { geral: ReturnType<typeof saudeGeral>; compacto?: boolean }) {
  if (geral === 'carregando') return <Badge>Verificando…</Badge>
  return (
    <span
      className={cx(
        'inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-rotulo font-[520] whitespace-nowrap',
        geral === 'ok' ? 'border-ok/45 text-ok' : 'border-danger/50 text-[#f08a80]',
      )}
    >
      {compacto ? (
        <IconPulse width={14} height={14} />
      ) : (
        <span className={cx('h-1.5 w-1.5 rounded-full', geral === 'ok' ? 'bg-ok' : 'bg-danger')} aria-hidden="true" />
      )}
      {geral === 'ok' ? 'Tudo funcionando' : 'Precisa de atenção'}
    </span>
  )
}

/* ------------------------------------------------------------------ */
/* Grupo de controle: planejado                                        */
/* ------------------------------------------------------------------ */

export function GrupoDeControle() {
  return (
    <div className="anim-entrada flex h-full flex-col justify-between rounded-[18px] border border-dashed border-graphite/70 p-5">
      <div className="flex items-start justify-between gap-3">
        <span className="t-label text-silver">Comparação com grupo de controle</span>
        <Badge>Planejado</Badge>
      </div>
      <div className="mt-3">
        <p className="text-apoio leading-[1.5] text-paper">
          Esta tela mostra o que foi recuperado e mantido. Medir quanto disso aconteceu por causa da CRAI exige comparar com clientes que
          não recebem a ação.
        </p>
        <p className="t-label mt-2 flex items-center gap-1.5 text-silver">
          <IconShield width={14} height={14} /> Entra numa próxima versão.
        </p>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Extrato do mês                                                      */
/* ------------------------------------------------------------------ */

const ORIGEM = {
  involuntario: { rotulo: 'Involuntário', cor: 'var(--color-serie-inv)' },
  voluntario: { rotulo: 'Voluntário', cor: 'var(--color-serie-vol)' },
}

export function ExtratoDoMes({ linhas, mes }: { linhas: LinhaExtrato[]; mes: string }) {
  const [todas, setTodas] = useState(false)
  const visiveis = todas ? linhas : linhas.slice(0, 6)
  const total = linhas.reduce(
    (t, l) => ({ base: t.base + (l.estornado ? 0 : l.valor_base), taxa: t.taxa + l.taxa, liquido: t.liquido + l.liquido }),
    { base: 0, taxa: 0, liquido: 0 },
  )

  function exportar() {
    baixarCsv(
      `crai-extrato-${mes}.csv`,
      [
        'Data',
        'Cliente',
        'Origem',
        'O que aconteceu',
        'Valor (R$)',
        'Taxa da CRAI (R$)',
        'Líquido para você (R$)',
        'Situação',
        'Demonstração',
      ],
      linhas.map((l) => [
        new Date(l.data).toLocaleDateString('pt-BR'),
        l.cliente,
        ORIGEM[l.origem].rotulo,
        l.descricao,
        l.valor_base,
        l.taxa,
        l.liquido,
        l.estornado ? 'Estornado' : 'Confirmado',
        l.simulado ? 'Sim' : 'Não',
      ]),
    )
  }

  return (
    <div>
      <div className="px-5 pt-5">
        <CabecalhoCartao
          titulo="Extrato de setembro"
          apoio="Cada valor recuperado ou mantido, com a taxa da CRAI. É a memória de cálculo da sua fatura."
          direita={
            <Button variant="ghost" size="sm" onClick={exportar} disabled={linhas.length === 0}>
              <IconDownload width={15} height={15} /> Exportar CSV
            </Button>
          }
        />
      </div>
      <div className="scroll-fino mt-4 overflow-x-auto">
        <table className="w-full min-w-[760px] text-left text-apoio">
          <thead>
            <tr className="t-label text-silver">
              <th className="px-5 py-2.5 font-[500]">Data</th>
              <th className="px-3 py-2.5 font-[500]">Cliente</th>
              <th className="px-3 py-2.5 font-[500]">O que aconteceu</th>
              <th className="px-3 py-2.5 text-right font-[500]">Valor</th>
              <th className="px-3 py-2.5 text-right font-[500]">Taxa da CRAI</th>
              <th className="px-5 py-2.5 text-right font-[500] whitespace-nowrap">Líquido para você</th>
            </tr>
          </thead>
          <tbody>
            {visiveis.length === 0 ? (
              <tr className="border-t border-line">
                <td colSpan={6} className="px-5 py-10 text-center text-silver">
                  Nenhum valor recuperado ou mantido neste mês ainda.
                </td>
              </tr>
            ) : (
              visiveis.map((l) => (
                <tr key={l.id} className="border-t border-line">
                  <td className="tabular px-5 py-3 whitespace-nowrap text-silver">{fmt.dataCurta(l.data)}</td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-[560] text-paper">{l.cliente}</span>
                      {l.simulado ? (
                        <Badge tone="amber" className="px-1.5 py-0 text-rotulo">
                          Demonstração
                        </Badge>
                      ) : null}
                    </div>
                    <div className="t-label flex items-center gap-1.5 text-silver">
                      <span className="h-2 w-2 rounded-[2px]" style={{ background: ORIGEM[l.origem].cor }} aria-hidden="true" />
                      {ORIGEM[l.origem].rotulo}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-silver">
                    {l.estornado ? <Badge className="mr-2">Estornado</Badge> : null}
                    {l.descricao}
                  </td>
                  <td className={cx('tabular px-3 py-3 text-right', l.estornado ? 'text-muted line-through' : 'text-paper')}>
                    {fmt.brl(l.valor_base)}
                  </td>
                  <td className="tabular px-3 py-3 text-right whitespace-nowrap text-silver">{l.taxa ? `− ${fmt.brl(l.taxa)}` : '—'}</td>
                  <td className="tabular px-5 py-3 text-right font-[600] text-paper">{fmt.brl(l.liquido)}</td>
                </tr>
              ))
            )}
          </tbody>
          {linhas.length ? (
            <tfoot>
              <tr className="border-t border-graphite/60">
                <td colSpan={3} className="px-5 py-3">
                  {linhas.length > 6 ? (
                    <button type="button" onClick={() => setTodas((t) => !t)} className="t-label font-[560] text-silver hover:text-paper">
                      {todas ? 'Mostrar só as 6 mais recentes' : `Mostrar as ${linhas.length} linhas`}
                    </button>
                  ) : null}
                </td>
                <td className="tabular px-3 py-3 text-right text-silver">{fmt.brl(total.base)}</td>
                <td className="tabular px-3 py-3 text-right whitespace-nowrap text-silver">− {fmt.brl(total.taxa)}</td>
                <td className="tabular px-5 py-3 text-right font-[680] text-paper">{fmt.brl(total.liquido)}</td>
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
      <p className="t-label px-5 pt-1 pb-5 text-muted">
        Voluntário: conta 1 mês da mensalidade de quem aceitou a oferta, menos o desconto dado. Se o cliente cancelar em até 30 dias, o
        valor é estornado.
      </p>
    </div>
  )
}

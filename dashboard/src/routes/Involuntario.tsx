import { AnimatePresence, motion } from 'framer-motion'
import { useMemo, useState } from 'react'
import { IconAlert, IconArrowRight, IconClock, IconRefresh, IconSpark } from '../components/icons/Icons'
import { usePainel } from '../components/layout/Shell'
import { Badge } from '../components/ui/Badge'
import { Card } from '../components/ui/Card'
import { ErroCarregar, Vazio } from '../components/ui/Estados'
import { StatTile } from '../components/ui/StatTile'
import { STATUS, StatusPill } from '../components/ui/StatusPill'
import { MODO_REAL, agoraDaTela, api } from '../data/api'
import { CAUSA_LEGIVEL } from '../data/mock'
import type { PontoSerie, StatusTela } from '../data/tipos'
import { cx } from '../lib/cx'
import { fmt } from '../lib/format'
import { useCarregar } from '../lib/useCarregar'
import { CicloDrawer } from './involuntario/CicloDrawer'

type Filtro = StatusTela | 'todos'

const nomeDoMes = new Intl.DateTimeFormat('pt-BR', { month: 'long' })
/** "2026-09" vira "setembro". Sem o mês (ainda carregando), o texto de sempre. */
function mesPorExtenso(mes: string | undefined): string {
  if (!mes) return MODO_REAL ? 'este mês' : 'setembro'
  return nomeDoMes.format(new Date(`${mes}-15T12:00:00`))
}

const FILTROS: { valor: Filtro; rotulo: string }[] = [
  { valor: 'todos', rotulo: 'Todos' },
  { valor: 'em_analise', rotulo: STATUS.em_analise.rotulo },
  { valor: 'em_processo', rotulo: STATUS.em_processo.rotulo },
  { valor: 'recuperado', rotulo: STATUS.recuperado.rotulo },
  { valor: 'encerrado', rotulo: 'Encerrado' },
]

/** Sparkline de 30 dias, na cor da série do involuntário; o último ponto em destaque. */
function Sparkline({ pontos }: { pontos: PontoSerie[] }) {
  const w = 220
  const h = 44
  const max = Math.max(1, ...pontos.map((p) => p.valor))
  const xs = pontos.map((p, i) => ({
    x: (i / (pontos.length - 1)) * w,
    y: h - 4 - (p.valor / max) * (h - 8),
  }))
  const caminho = xs.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  const ultimo = xs[xs.length - 1]
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-11 w-full" role="img" aria-label="Valor recuperado por dia nos últimos 30 dias">
      <path d={caminho} fill="none" stroke="var(--color-ink)" strokeOpacity={0.55} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={ultimo.x} cy={ultimo.y} r={4} fill="var(--color-ink)" />
    </svg>
  )
}

export function Involuntario() {
  const { modo } = usePainel()
  const [filtro, setFiltro] = useState<Filtro>('todos')
  const [busca, setBusca] = useState('')
  const [aberto, setAberto] = useState<number | null>(null)

  const topo = useCarregar(() => Promise.all([api.metricasMes(), api.serie()]), [])
  const [metricas, serie] = topo.dados ?? [null, []]
  const lista = useCarregar(() => api.ciclos({ status: filtro, busca, incluirSimulados: modo === 'simulacao' }), [filtro, busca, modo])
  const ciclos = lista.dados
  const semFiltro = filtro === 'todos' && !busca.trim()

  const contagem = useMemo(() => {
    const c: Record<Filtro, number> = { todos: 0, em_analise: 0, em_processo: 0, recuperado: 0, encerrado: 0 }
    for (const x of ciclos ?? []) {
      c.todos++
      c[x.status]++
    }
    return c
  }, [ciclos])

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="t-h2 text-paper">Churn involuntário</h2>
          <p className="t-apoio mt-1 text-silver">Cobranças Pix que falharam e o que o sistema está fazendo com cada uma.</p>
        </div>
        <nav className="flex gap-1 rounded-[10px] border border-line bg-slate/50 p-1" aria-label="Abas">
          <span className="rounded-[7px] bg-paper px-3 py-1.5 text-[13px] font-[560] text-ink">Clientes em recuperação</span>
          <span className="rounded-[7px] px-3 py-1.5 text-[13px] font-[520] text-silver">
            Mensagens {metricas?.aguardando_escolha ? <Badge tone="orange" className="ml-1 px-1.5 py-0">{metricas.aguardando_escolha}</Badge> : null}
          </span>
        </nav>
      </div>

      {topo.erro ? <ErroCarregar mensagem={topo.erro} onTentar={topo.recarregar} /> : null}

      {/* Bento: o cartão laranja é o único colorido; os outros são vidro */}
      <section className="grid grid-cols-2 gap-4 lg:grid-cols-4 xl:grid-cols-6" aria-label="Resumo do mês">
        <StatTile
          className="col-span-2 row-span-2 min-h-[210px]"
          tone="orange"
          hero
          rotulo={`Recuperado para você em ${mesPorExtenso(metricas?.mes)}`}
          valor={metricas ? fmt.brlInteiro(metricas.valor_liquido_recuperado) : '—'}
          apoio={
            <div className="flex flex-col gap-2">
              <span>Já descontada a taxa da CRAI. {metricas ? `${metricas.recuperados} cobranças recuperadas.` : ''}</span>
              {serie.length ? <Sparkline pontos={serie} /> : null}
            </div>
          }
        />
        <StatTile rotulo="Ciclos ativos" valor={metricas?.ciclos_ativos ?? '—'} apoio="Em análise ou em processo" icone={<IconRefresh width={17} height={17} />} />
        <StatTile rotulo="Recuperados no mês" valor={metricas?.recuperados ?? '—'} apoio={metricas ? `${metricas.encerrados_sem_recuperacao} encerrados sem recuperação` : ''} icone={<IconSpark width={17} height={17} />} />
        <StatTile
          rotulo="Taxa de recuperação"
          valor={metricas && metricas.taxa_recuperacao !== null ? fmt.pontos(metricas.taxa_recuperacao * 100) : '—'}
          apoio={metricas && metricas.taxa_recuperacao === null ? 'Ainda sem ciclos com desfecho neste mês' : 'Sobre os ciclos com desfecho'}
        />
        <StatTile
          rotulo="Aguardando sua escolha"
          valor={metricas?.aguardando_escolha ?? '—'}
          apoio={metricas?.aguardando_escolha ? 'Mensagem a escolher hoje' : 'Nenhuma mensagem pendente'}
          icone={<IconAlert width={17} height={17} />}
        />
        <StatTile
          rotulo="Próxima ação do sistema"
          valor={<span className="t-h3 text-paper">{metricas?.proxima_acao?.descricao ?? (MODO_REAL ? 'Ainda não informada pelo servidor' : 'Nada agendado')}</span>}
          apoio={metricas?.proxima_acao ? `${fmt.dataCurta(metricas.proxima_acao.quando)}, ${fmt.relativo(metricas.proxima_acao.quando, agoraDaTela())}` : MODO_REAL ? 'Abra um ciclo para ver as tentativas agendadas' : ''}
          icone={<IconClock width={17} height={17} />}
          className="col-span-2 lg:col-span-3 xl:col-span-4"
        />
      </section>

      {/* Tabela */}
      <Card className="p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
          <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Filtrar por status">
            {FILTROS.map((f) => (
              <button
                key={f.valor}
                type="button"
                role="tab"
                aria-selected={filtro === f.valor}
                onClick={() => setFiltro(f.valor)}
                className={cx(
                  'rounded-full border px-3 py-1.5 text-[13px] font-[520] transition-colors',
                  filtro === f.valor ? 'border-orange/60 bg-orange/10 text-orange' : 'border-line text-silver hover:border-graphite hover:text-paper',
                )}
              >
                {f.rotulo}
                {filtro === 'todos' || filtro === f.valor ? (
                  <span className="tabular ml-1.5 text-[12px] opacity-70">{contagem[f.valor]}</span>
                ) : null}
              </button>
            ))}
          </div>
          <input
            type="search"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
            placeholder="Nome ou id da recorrência"
            aria-label="Buscar na lista"
            className="h-9 w-64 rounded-[8px] border border-line bg-ink/40 px-3 text-[13.5px] text-paper placeholder:text-muted focus:border-amber/60 focus:outline-none"
          />
        </div>

        <div className="scroll-fino overflow-x-auto">
          <table className="w-full min-w-[820px] text-left">
            <thead>
              <tr className="t-label text-silver">
                <th className="px-5 py-3 font-[500]">Cliente</th>
                <th className="px-3 py-3 font-[500]">Valor</th>
                <th className="px-3 py-3 font-[500]">Motivo da falha</th>
                <th className="px-3 py-3 font-[500]">Status</th>
                <th className="px-3 py-3 font-[500]">Tentativas</th>
                <th className="px-3 py-3 font-[500]">Próxima ação</th>
                <th className="px-5 py-3 font-[500]"><span className="sr-only">Abrir</span></th>
              </tr>
            </thead>
            <tbody>
              {lista.erro ? (
                <tr>
                  <td colSpan={7} className="p-4">
                    <ErroCarregar mensagem={lista.erro} onTentar={lista.recarregar} />
                  </td>
                </tr>
              ) : ciclos === null ? (
                Array.from({ length: 6 }).map((_, i) => (
                  <tr key={i} className="border-t border-line">
                    <td colSpan={7} className="px-5 py-4">
                      <div className="h-4 w-2/3 animate-pulse rounded bg-paper/[0.06]" />
                    </td>
                  </tr>
                ))
              ) : ciclos.length === 0 ? (
                <tr className="border-t border-line">
                  <td colSpan={7}>
                    {semFiltro ? (
                      <Vazio
                        titulo="Nenhuma cobrança em recuperação"
                        texto="Quando uma cobrança Pix falhar, ela aparece aqui com o status e o que o sistema está fazendo. Se a integração é nova, confira a saúde do sistema na visão geral."
                        icone={<IconRefresh width={22} height={22} />}
                      />
                    ) : (
                      <Vazio titulo="Nenhum cliente neste filtro" texto="Tente outro status ou limpe a busca." acao={<button type="button" onClick={() => { setFiltro('todos'); setBusca('') }} className="t-label rounded-[8px] border border-line px-3 py-1.5 font-[560] text-silver hover:text-paper">Limpar filtros</button>} />
                    )}
                  </td>
                </tr>
              ) : (
                ciclos.map((c) => (
                  <tr
                    key={c.id}
                    onClick={() => setAberto(c.id)}
                    className="group cursor-pointer border-t border-line transition-colors hover:bg-paper/[0.035]"
                  >
                    <td className="px-5 py-3.5">
                      <div className="flex items-center gap-2">
                        <span className="font-[560] text-paper">{c.cliente ?? <span className="text-silver">Cliente sem cadastro</span>}</span>
                        {c.simulado ? <Badge tone="amber">Demonstração</Badge> : null}
                      </div>
                      <div className="t-label text-muted">{c.id_recorrencia}</div>
                    </td>
                    <td className="tabular px-3 py-3.5 font-[560] text-paper">
                      {fmt.brl(c.valor_cobranca)}
                      {c.valor_liquido !== null ? <div className="t-label font-[500] text-ok">{fmt.brl(c.valor_liquido)} para você</div> : null}
                    </td>
                    <td className="px-3 py-3.5 text-silver">{c.causa_legivel ?? CAUSA_LEGIVEL[c.causa]}</td>
                    <td className="px-3 py-3.5"><StatusPill status={c.status} /></td>
                    <td className="px-3 py-3.5">
                      <Tentativas feitas={c.tentativas_executadas} total={c.tentativas_total} />
                    </td>
                    <td className="px-3 py-3.5 text-silver">
                      {c.proxima_acao ? (
                        <>
                          <div className="text-paper">{c.proxima_acao_descricao}</div>
                          <div className="t-label">{fmt.dataCurta(c.proxima_acao)}, {fmt.relativo(c.proxima_acao, agoraDaTela())}</div>
                        </>
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="px-5 py-3.5 text-right">
                      <IconArrowRight className="inline text-muted transition-[color,transform] group-hover:translate-x-0.5 group-hover:text-orange" width={18} height={18} />
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <AnimatePresence>
        {aberto !== null ? (
          <motion.div key="drawer" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <CicloDrawer id={aberto} onClose={() => setAberto(null)} />
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  )
}

/** Três traços: cheio para tentativa feita, vazio para as que faltam. */
function Tentativas({ feitas, total }: { feitas: number; total: number }) {
  if (total === 0) return <span className="text-muted">—</span>
  return (
    <div className="flex items-center gap-2" aria-label={`${feitas} de ${total} tentativas`}>
      <div className="flex gap-1">
        {Array.from({ length: total }).map((_, i) => (
          <span key={i} className={cx('h-1.5 w-5 rounded-full', i < feitas ? 'bg-orange' : 'bg-paper/[0.12]')} />
        ))}
      </div>
      <span className="tabular text-[12.5px] text-silver">
        {feitas}/{total}
      </span>
    </div>
  )
}

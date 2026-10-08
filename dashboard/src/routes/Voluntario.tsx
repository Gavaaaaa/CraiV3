import { AnimatePresence, motion } from 'framer-motion'
import { useSearchParams } from 'react-router-dom'
import { IconAlert, IconSend, IconSpark, IconUsers } from '../components/icons/Icons'
import { usePainel } from '../components/layout/Shell'
import { Abas, type Aba } from '../components/ui/Abas'
import { Badge } from '../components/ui/Badge'
import { Card } from '../components/ui/Card'
import { FaixaDemonstracao } from '../components/ui/Demonstracao'
import { ErroCarregar, Vazio } from '../components/ui/Estados'
import { StatTile } from '../components/ui/StatTile'
import { MODO_REAL, api, etiquetaDeDemonstracao } from '../data/api'
import type { ClienteRisco } from '../data/tipos'
import type { BaseClientes, PontoSerie } from '../data/tipos'
import { fmt } from '../lib/format'
import { localeAtual, t } from '../lib/idioma'
import { useCarregar } from '../lib/useCarregar'
import { useReducedMotion } from '../lib/useReducedMotion'
import { GraficoTrintaDias } from './visao/GraficoTrintaDias'
import { QuemDecideORisco, SuaBase, TabelaClientes } from './voluntario/Blocos'

type AbaVol = 'clientes' | 'mantido' | 'base' | 'decisao'

const nomeDoMes = new Intl.DateTimeFormat(localeAtual(), { month: 'long' })
/** O rótulo do cartão laranja: "Mantido para você em setembro". O mês é o que o backend devolveu. */
function rotuloDoMes(mes: string | undefined): string {
  if (!mes) return MODO_REAL ? t('Mantido para você neste mês') : t('Mantido para você em setembro')
  return t('Mantido para você em {mes}', { mes: nomeDoMes.format(new Date(`${mes}-15T12:00:00`)) })
}

/** "1 mês da mensalidade", "6 meses da mensalidade": o número vem do backend. */
const mesesDaRegra = (n: number) => (n === 1 ? t('1 mês') : t('{n} meses', { n }))
const ABAS_VALIDAS: AbaVol[] = ['base', 'clientes', 'mantido', 'decisao']

/** Sparkline do cartão laranja, na cor do fundo escuro. */
function Sparkline({ pontos }: { pontos: PontoSerie[] }) {
  const w = 220
  const h = 44
  const max = Math.max(1, ...pontos.map((p) => p.valor))
  const xs = pontos.map((p, i) => ({ x: (i / (pontos.length - 1)) * w, y: h - 4 - (p.valor / max) * (h - 8) }))
  const caminho = xs.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
  const ultimo = xs[xs.length - 1]
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-11 w-full" role="img" aria-label={t('Valor mantido por dia nos últimos 30 dias')}>
      <path d={caminho} fill="none" stroke="var(--color-sobre-destaque)" strokeOpacity={0.55} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={ultimo.x} cy={ultimo.y} r={4} fill="var(--color-sobre-destaque)" />
    </svg>
  )
}

/** O cartão "Quem decide o risco": só afirma o que a base diz. */
function quemDecide(base: BaseClientes | null): string {
  if (!base || !base.total) return '—'
  if (!base.decididos_pelo_modelo) return t('A régua decide em toda a base')
  return t('Modelo de IA em {pct}% da base', { pct: Math.round((base.decididos_pelo_modelo / base.total) * 100) })
}

export function Voluntario() {
  const { modo, empresa } = usePainel()
  const sim = modo === 'simulacao'
  const premium = empresa?.plano === 'premium'
  const reduzido = useReducedMotion()

  const [params, setParams] = useSearchParams()
  const pedida = params.get('aba') as AbaVol | null
  // A base vem primeiro: é por ela que entra quem não usa a API.
  const aba: AbaVol = pedida && ABAS_VALIDAS.includes(pedida) ? pedida : 'base'
  const irPara = (v: AbaVol) => setParams(v === 'base' ? {} : { aba: v }, { replace: true })

  const carga = useCarregar(
    () =>
      Promise.all([
        api.clientesRecentes({ incluirSimulados: sim, limite: 10 }),
        api.serieVoluntario({ incluirSimulados: sim }),
        api.resumoVoluntario({ incluirSimulados: sim }),
        api.baseClientes(),
        api.comparacaoReguaModelo(),
      ]),
    [sim],
  )
  const [clientes, serie, resumo, base, comparacao] = carga.dados ?? [null, null, null, null, null]
  const semBase = carga.dados !== null && base === null

  // A busca do topo leva a `?aba=clientes&cliente=<id>`: aquele cliente é lido à parte (ele pode
  // não estar entre os 10 mais recentes) e vai para o topo da lista.
  const idBuscado = params.get('cliente')?.trim() || null
  const cargaDoBuscado = useCarregar<ClienteRisco | 'nao_encontrado' | null>(
    () => (idBuscado ? api.clientesRecentes({ cliente: idBuscado, limite: 1, incluirSimulados: sim }).then((l) => l[0] ?? 'nao_encontrado') : Promise.resolve(null)),
    [idBuscado, sim],
  )
  const buscado = idBuscado ? cargaDoBuscado.dados : null
  const limparBusca = () => setParams({ aba: 'clientes' }, { replace: true })

  if (empresa && !premium) {
    return (
      <div className="flex flex-col gap-5">
        <div>
          <h2 className="t-h2 text-paper">{t('Churn voluntário')}</h2>
          <p className="t-apoio mt-1 text-silver">{t('Sua base, os clientes em risco e o que a CRAI fez para mantê-los.')}</p>
        </div>
        <Card className="p-8 text-center">
          <div className="t-h3 text-paper">{t('O churn voluntário faz parte do plano premium')}</div>
          <p className="t-apoio mx-auto mt-2 max-w-md text-silver">
            {t('No premium, a CRAI acompanha os sinais de risco dos seus clientes e faz a oferta certa antes do cancelamento. Você só paga sobre o que ficou.')}
          </p>
        </Card>
      </div>
    )
  }

  const graves = clientes ? clientes.filter((c) => c.faixa === 'grave').length : null

  const abas: Aba<AbaVol>[] = [
    { valor: 'base', rotulo: t('Sua base') },
    { valor: 'clientes', rotulo: t('Clientes em risco'), extra: graves ? <Badge tone="danger" className="px-1.5 py-0 text-rotulo">{t('{n} graves', { n: graves })}</Badge> : null },
    { valor: 'mantido', rotulo: t('Dinheiro mantido') },
    { valor: 'decisao', rotulo: t('Quem decide o risco') },
  ]

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="t-h2 text-paper">{t('Churn voluntário')}</h2>
          <p className="t-apoio mt-1 text-silver">{t('Sua base, os clientes em risco e o que a CRAI fez para mantê-los.')}</p>
        </div>
        {sim ? <Badge tone="amber">{t('Inclui demonstração')}</Badge> : null}
      </div>

      {carga.erro ? <ErroCarregar mensagem={carga.erro} onTentar={carga.recarregar} /> : null}
      {semBase ? (
        <div role="status" className="rounded-[12px] border border-amber/40 bg-amber/[0.06] px-4 py-3 text-apoio text-paper">
          {t('Ainda não há base de clientes. Anexe a planilha na aba "Sua base" ou ligue a API na configuração; a avaliação de risco começa em seguida.')}
        </div>
      ) : null}

      {/* Bento: o laranja é o único colorido */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6" aria-label={t('Resumo do mês')}>
        <StatTile
          className="row-span-2 min-h-[210px] sm:col-span-2"
          tone="orange"
          hero
          demo={etiquetaDeDemonstracao('resumoVoluntario')}
          rotulo={rotuloDoMes(resumo?.mes)}
          valor={resumo ? fmt.brlInteiro(resumo.valor_liquido_mantido) : '—'}
          apoio={
            <div className="flex flex-col gap-2">
              <span>
                {t('Já descontada a taxa da CRAI.')}{' '}
                {resumo
                  ? (resumo.clientes_mantidos === 1
                      ? t('{n} cliente que ficou', { n: resumo.clientes_mantidos })
                      : t('{n} clientes que ficaram', { n: resumo.clientes_mantidos })) +
                    (resumo.estornos ? (resumo.estornos === 1 ? t(', {n} estorno', { n: resumo.estornos }) : t(', {n} estornos', { n: resumo.estornos })) : '') +
                    '.'
                  : ''}
              </span>
              {serie ? <Sparkline pontos={serie} /> : null}
            </div>
          }
        />
        <StatTile demo={etiquetaDeDemonstracao('resumoVoluntario')} rotulo={t('Em risco grave')} valor={resumo?.grave ?? '—'} apoio={t('Na base inteira; já receberam ou vão receber uma oferta')} icone={<IconAlert width={17} height={17} />} />
        <StatTile demo={etiquetaDeDemonstracao('resumoVoluntario')} rotulo={t('Preocupantes')} valor={resumo?.preocupante ?? '—'} apoio={t('Oferta mais leve, ou só acompanhar')} icone={<IconUsers width={17} height={17} />} />
        <StatTile
          demo={etiquetaDeDemonstracao('resumoVoluntario')}
          rotulo={t('Ofertas aceitas no mês')}
          valor={resumo ? t('{aceitas} de {enviadas}', { aceitas: resumo.ofertas_aceitas, enviadas: resumo.ofertas_enviadas }) : '—'}
          apoio={resumo ? (resumo.ofertas_enviadas ? t('{pct}% de aceite', { pct: Math.round((resumo.ofertas_aceitas / resumo.ofertas_enviadas) * 100) }) : t('Nenhuma oferta enviada ainda')) : ''}
          icone={<IconSend width={17} height={17} />}
        />
        <StatTile
          demo={etiquetaDeDemonstracao('baseClientes')}
          rotulo={t('Quem decide o risco')}
          valor={<span className="t-h3 text-paper">{quemDecide(base)}</span>}
          apoio={base && base.decididos_pelo_modelo > 0 ? t('No resto, a régua. Detalhes na aba ao lado.') : t('Detalhes na aba ao lado.')}
          icone={<IconSpark width={17} height={17} />}
        />
      </section>

      <section aria-label={t('Detalhes')} className="flex flex-col gap-4">
        <Abas abas={abas} ativa={aba} onChange={irPara} rotulo={t('Detalhes do voluntário')} idBase="vol" />
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={aba}
            role="tabpanel"
            id={`vol-painel-${aba}`}
            aria-labelledby={`vol-aba-${aba}`}
            initial={reduzido ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduzido ? { opacity: 1 } : { opacity: 0, y: -4 }}
            transition={{ duration: reduzido ? 0 : 0.22, ease: [0.16, 1, 0.3, 1] }}
          >
            <FaixaDemonstracao de={['clientesRecentes', 'serieVoluntario', 'baseClientes', 'comparacaoReguaModelo']} />
            {aba === 'clientes' ? (
              <Card className="p-0">
                {idBuscado && buscado ? (
                  <div role="status" className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-amber/[0.05] px-5 py-3 text-apoio text-paper">
                    <span>
                      {buscado === 'nao_encontrado'
                        ? t('O cliente buscado não está na base da sua empresa.')
                        : t('O cliente buscado está no topo da lista, antes dos mais recentes.')}
                    </span>
                    <button type="button" onClick={limparBusca} className="t-label rounded-[8px] border border-line px-3 py-1.5 font-[560] text-silver hover:text-paper">
                      {t('Limpar a busca')}
                    </button>
                  </div>
                ) : null}
                {carga.erro ? <div className="p-4"><ErroCarregar mensagem={carga.erro} onTentar={carga.recarregar} /></div> : <TabelaClientes clientes={clientes} total={base?.total ?? null} buscado={buscado && buscado !== 'nao_encontrado' ? buscado : null} />}
              </Card>
            ) : aba === 'mantido' ? (
              <Card className="p-5 md:p-6">
                {serie ? (
                  serie.every((p) => p.valor === 0) ? (
                    <Vazio titulo={t('Nada mantido ainda nos últimos 30 dias')} texto={t('O gráfico aparece quando o primeiro cliente em risco aceitar uma oferta.')} />
                  ) : (
                  <GraficoTrintaDias
                    pontos={serie.map((p) => ({ dia: p.dia, involuntario: 0, voluntario: p.valor }))}
                    series={[{ chave: 'voluntario', rotulo: t('Voluntário (clientes mantidos)'), cor: 'var(--color-serie-vol)' }]}
                    titulo={t('Dinheiro mantido nos últimos 30 dias')}
                    subtitulo={t('Já descontada a taxa da CRAI. Passe o mouse ou use as setas do teclado para ver cada dia.')}
                  />
                  )
                ) : (
                  <div className="min-h-[420px] animate-pulse rounded-[12px] bg-paper/[0.04]" aria-busy="true" />
                )}
                <p className="mt-4 rounded-[12px] border border-line bg-ink/25 px-4 py-3 text-apoio leading-[1.5] text-silver">
                  <span className="font-[600] text-paper">{t('Como o valor é contado:')}</span>{' '}
                  {t('{meses} da mensalidade do cliente que aceitou a oferta, menos o desconto dado, líquido da taxa da CRAI, no dia do aceite. Se o cliente cancelar em até {dias} dias, o valor é estornado.', {
                    meses: mesesDaRegra(resumo?.meses_de_mrr ?? 1),
                    dias: resumo?.prazo_estorno_dias ?? 30,
                  })}
                </p>
              </Card>
            ) : aba === 'base' ? (
              <Card className="p-5 md:p-6">
                <SuaBase base={base} aoImportar={carga.recarregar} />
              </Card>
            ) : (
              <Card className="p-5 md:p-6">
                <QuemDecideORisco base={base} comparacao={comparacao} />
              </Card>
            )}
          </motion.div>
        </AnimatePresence>
      </section>
    </div>
  )
}

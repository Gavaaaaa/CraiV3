import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { IconAlert, IconArrowRight, IconClock, IconRefresh, IconSpark } from '../components/icons/Icons'
import { usePainel } from '../components/layout/Shell'
import { Abas, type Aba } from '../components/ui/Abas'
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
import { localeAtual, t } from '../lib/idioma'
import { useCarregar } from '../lib/useCarregar'
import { useModoMensagem } from '../lib/useModoMensagem'
import { AbaMensagens } from './involuntario/AbaMensagens'
import { CicloDrawer } from './involuntario/CicloDrawer'

/** Os quatro status, mais o filtro de quem espera a escolha da empresa (o endereço antigo do sino). */
type Filtro = StatusTela | 'todos' | 'aguardando_escolha'

/** As duas abas da página: a tela de sempre, e as cobranças esperando a escolha da mensagem. */
type AbaInv = 'clientes' | 'mensagens'

const nomeDoMes = new Intl.DateTimeFormat(localeAtual(), { month: 'long' })
/** "2026-09" vira "setembro". Sem o mês (ainda carregando), o texto de sempre. */
function mesPorExtenso(mes: string | undefined): string {
  if (!mes) return MODO_REAL ? t('este mês') : t('setembro')
  return nomeDoMes.format(new Date(`${mes}-15T12:00:00`))
}

const FILTROS: { valor: Filtro; rotulo: string }[] = [
  { valor: 'todos', rotulo: t('Todos') },
  { valor: 'em_analise', rotulo: STATUS.em_analise.rotulo },
  { valor: 'em_processo', rotulo: STATUS.em_processo.rotulo },
  { valor: 'recuperado', rotulo: STATUS.recuperado.rotulo },
  { valor: 'encerrado', rotulo: t('Encerrado') },
  { valor: 'aguardando_escolha', rotulo: t('Aguardando escolha') },
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
    <svg viewBox={`0 0 ${w} ${h}`} className="h-11 w-full" role="img" aria-label={t('Valor recuperado por dia nos últimos 30 dias')}>
      <path d={caminho} fill="none" stroke="var(--color-sobre-destaque)" strokeOpacity={0.55} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={ultimo.x} cy={ultimo.y} r={4} fill="var(--color-sobre-destaque)" />
    </svg>
  )
}

export function Involuntario() {
  const { modo } = usePainel()
  // O endereço pode pedir a aba (`?aba=mensagens`, o caminho do sino), um filtro
  // (`?filtro=aguardando_escolha`, o endereço antigo do sino, que continua valendo) e um ciclo
  // aberto (`?ciclo=12`, o caminho da busca do topo).
  const [params, setParams] = useSearchParams()
  const aba: AbaInv = params.get('aba') === 'mensagens' ? 'mensagens' : 'clientes'
  const filtroPedido = params.get('filtro') === 'aguardando_escolha' ? 'aguardando_escolha' : 'todos'
  const [filtro, setFiltro] = useState<Filtro>(filtroPedido)
  const [busca, setBusca] = useState('')
  const cicloPedido = Number(params.get('ciclo'))
  const [aberto, setAberto] = useState<number | null>(Number.isInteger(cicloPedido) && cicloPedido > 0 ? cicloPedido : null)
  // Aberto pela aba Mensagens, o painel do ciclo começa já na escolha da mensagem.
  const [abertoNaEscolha, setAbertoNaEscolha] = useState(false)
  useEffect(() => {
    if (params.get('filtro') === 'aguardando_escolha') setFiltro('aguardando_escolha')
    const c = Number(params.get('ciclo'))
    if (Number.isInteger(c) && c > 0) setAberto(c)
  }, [params])

  function semParametro(nome: 'filtro' | 'ciclo') {
    if (!params.has(nome)) return
    const outros = new URLSearchParams(params)
    outros.delete(nome)
    setParams(outros, { replace: true })
  }
  // A aba entra no histórico do navegador: o botão voltar devolve a aba anterior.
  function irParaAba(v: AbaInv) {
    if (v === aba) return
    const outros = new URLSearchParams(params)
    if (v === 'mensagens') outros.set('aba', 'mensagens')
    else outros.delete('aba')
    setParams(outros)
  }
  function escolherFiltro(f: Filtro) {
    setFiltro(f)
    if (f !== 'aguardando_escolha') semParametro('filtro')
  }
  function abrirCiclo(id: number, naEscolha = false) {
    setAbertoNaEscolha(naEscolha)
    setAberto(id)
  }
  function fecharCiclo() {
    setAberto(null)
    setAbertoNaEscolha(false)
    semParametro('ciclo')
  }

  const sim = modo === 'simulacao'
  // No modo automático ninguém escolhe: o número conta as mensagens que o sistema ainda vai enviar.
  const automatico = useModoMensagem() === 'automatico'
  const topo = useCarregar(() => Promise.all([api.metricasMes({ incluirSimulados: sim }), api.serie({ incluirSimulados: sim })]), [sim])
  const [metricas, serie] = topo.dados ?? [null, []]
  const soEsperando = filtro === 'aguardando_escolha'
  const lista = useCarregar(
    () => api.ciclos(soEsperando ? { status: 'todos', aguardandoEscolha: true, busca, incluirSimulados: sim } : { status: filtro, busca, incluirSimulados: sim }),
    [filtro, busca, modo],
  )
  const ciclos = lista.dados
  const semFiltro = filtro === 'todos' && !busca.trim()

  const contagem = useMemo(() => {
    const c: Record<Filtro, number> = { todos: 0, em_analise: 0, em_processo: 0, recuperado: 0, encerrado: 0, aguardando_escolha: 0 }
    for (const x of ciclos ?? []) {
      c.todos++
      c[x.status]++
    }
    // O número deste filtro é o do backend (quem já escolheu e só espera o horário não conta).
    c.aguardando_escolha = soEsperando ? (ciclos ?? []).length : (metricas?.aguardando_escolha ?? 0)
    return c
  }, [ciclos, metricas, soEsperando])

  // O número da aba Mensagens é o do backend: o mesmo do sino e do cartão "Aguardando sua escolha".
  const abas: Aba<AbaInv>[] = [
    { valor: 'clientes', rotulo: t('Clientes em recuperação') },
    { valor: 'mensagens', rotulo: t('Mensagens'), extra: metricas?.aguardando_escolha ? <Badge tone="orange" className="px-1.5 py-0">{metricas.aguardando_escolha}</Badge> : null },
  ]

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="t-h2 text-paper">{t('Churn involuntário')}</h2>
          <p className="t-apoio mt-1 text-silver">{t('Cobranças Pix que falharam e o que o sistema está fazendo com cada uma.')}</p>
        </div>
        <Abas abas={abas} ativa={aba} onChange={irParaAba} rotulo={t('Abas do involuntário')} idBase="inv" />
      </div>

      {topo.erro ? <ErroCarregar mensagem={topo.erro} onTentar={topo.recarregar} /> : null}

      {aba === 'mensagens' ? (
        <div role="tabpanel" id="inv-painel-mensagens" aria-labelledby="inv-aba-mensagens">
          <AbaMensagens incluirSimulados={sim} onEscolher={(id) => abrirCiclo(id, true)} onAbrir={(id) => abrirCiclo(id)} />
        </div>
      ) : (
        <div role="tabpanel" id="inv-painel-clientes" aria-labelledby="inv-aba-clientes" className="flex flex-col gap-5">
        {/* Bento: o cartão laranja é o único colorido; os outros são vidro */}
        <section className="grid grid-cols-2 gap-4 lg:grid-cols-4 xl:grid-cols-6" aria-label={t('Resumo do mês')}>
          <StatTile
            className="col-span-2 row-span-2 min-h-[210px]"
            tone="orange"
            hero
            rotulo={t('Recuperado para você em {mes}', { mes: mesPorExtenso(metricas?.mes) })}
            valor={metricas ? fmt.brlInteiro(metricas.valor_liquido_recuperado) : '—'}
            apoio={
              <div className="flex flex-col gap-2">
                <span>{t('Já descontada a taxa da CRAI.')} {metricas ? t('{n} cobranças recuperadas.', { n: metricas.recuperados }) : ''}</span>
                {serie.length ? <Sparkline pontos={serie} /> : null}
              </div>
            }
          />
          <StatTile rotulo={t('Ciclos ativos')} valor={metricas?.ciclos_ativos ?? '—'} apoio={t('Em análise ou em processo')} icone={<IconRefresh width={17} height={17} />} />
          <StatTile rotulo={t('Recuperados no mês')} valor={metricas?.recuperados ?? '—'} apoio={metricas ? t('{n} encerrados sem recuperação', { n: metricas.encerrados_sem_recuperacao }) : ''} icone={<IconSpark width={17} height={17} />} />
          <StatTile
            rotulo={t('Taxa de recuperação')}
            valor={metricas && metricas.taxa_recuperacao !== null ? fmt.pontos(metricas.taxa_recuperacao * 100) : '—'}
            apoio={metricas && metricas.taxa_recuperacao === null ? t('Ainda sem ciclos com desfecho neste mês') : t('Sobre os ciclos com desfecho')}
          />
          <StatTile
            rotulo={automatico ? t('Mensagens a enviar') : t('Aguardando sua escolha')}
            valor={metricas?.aguardando_escolha ?? '—'}
            apoio={
              metricas?.aguardando_escolha
                ? automatico
                  ? t('O sistema envia sozinho (modo automático)')
                  : t('Mensagem a escolher hoje')
                : t('Nenhuma mensagem pendente')
            }
            icone={<IconAlert width={17} height={17} />}
          />
          <StatTile
            rotulo={t('Próxima ação do sistema')}
            valor={<span className="t-h3 text-paper">{metricas?.proxima_acao?.descricao ?? (metricas ? t('Nada agendado') : '—')}</span>}
            apoio={
              metricas?.proxima_acao ? (
                <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  <span>
                    {fmt.dataCurta(metricas.proxima_acao.quando)}, {fmt.relativo(metricas.proxima_acao.quando, agoraDaTela())}
                  </span>
                  {metricas.proxima_acao.ciclo_id ? (
                    <button type="button" onClick={() => metricas.proxima_acao?.ciclo_id && abrirCiclo(metricas.proxima_acao.ciclo_id)} className="font-[560] text-amber underline-offset-2 hover:underline">
                      {t('Ver o ciclo')}
                    </button>
                  ) : null}
                </span>
              ) : metricas ? (
                t('Nenhuma tentativa de cobrança nem mensagem pendente agora')
              ) : (
                ''
              )
            }
            icone={<IconClock width={17} height={17} />}
            className="col-span-2 lg:col-span-3 xl:col-span-4"
          />
        </section>

        {/* Tabela */}
        <Card className="p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
            <div className="flex flex-wrap gap-1.5" role="tablist" aria-label={t('Filtrar por status')}>
              {FILTROS.map((f) => (
                <button
                  key={f.valor}
                  type="button"
                  role="tab"
                  aria-selected={filtro === f.valor}
                  onClick={() => escolherFiltro(f.valor)}
                  className={cx(
                    'rounded-full border px-3 py-1.5 text-rotulo font-[520] transition-colors',
                    filtro === f.valor ? 'border-orange/60 bg-orange/10 text-orange' : 'border-line text-silver hover:border-graphite hover:text-paper',
                  )}
                >
                  {f.rotulo}
                  {filtro === 'todos' || filtro === f.valor ? (
                    <span className="tabular ml-1.5 text-rotulo opacity-70">{contagem[f.valor]}</span>
                  ) : null}
                </button>
              ))}
            </div>
            <input
              type="search"
              value={busca}
              onChange={(e) => setBusca(e.target.value)}
              placeholder={t('Nome ou id da recorrência')}
              aria-label={t('Buscar na lista')}
              className="h-9 w-64 rounded-[8px] border border-campo bg-ink/40 px-3 text-apoio text-paper placeholder:text-muted focus:border-amber/60 focus:outline-none"
            />
          </div>

          {/* `relative`: o rótulo invisível "Abrir" (sr-only, posição absoluta) fica preso a esta área
              rolável. Sem isso ele se prendia ao cartão, escapava do corte e alargava a página inteira
              na janela estreita. */}
          <div className="scroll-fino relative overflow-x-auto" data-rolagem="tabela-de-ciclos">
            <table className="w-full min-w-[820px] text-left">
              <thead>
                <tr className="t-label text-silver">
                  <th className="px-5 py-3 font-[500]">{t('Cliente')}</th>
                  <th className="px-3 py-3 font-[500]">{t('Valor')}</th>
                  <th className="px-3 py-3 font-[500]">{t('Motivo da falha')}</th>
                  <th className="px-3 py-3 font-[500]">{t('Status')}</th>
                  <th className="px-3 py-3 font-[500]">{t('Tentativas')}</th>
                  <th className="px-3 py-3 font-[500]">{t('Próxima ação')}</th>
                  <th className="px-5 py-3 font-[500]"><span className="sr-only">{t('Abrir')}</span></th>
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
                          titulo={t('Nenhuma cobrança em recuperação')}
                          texto={t('Quando uma cobrança Pix falhar, ela aparece aqui com o status e o que o sistema está fazendo. Se a integração é nova, confira a saúde do sistema na visão geral.')}
                          icone={<IconRefresh width={22} height={22} />}
                        />
                      ) : (
                        <Vazio
                          titulo={soEsperando && !busca.trim() ? t('Nenhuma cobrança esperando a sua escolha') : t('Nenhum cliente neste filtro')}
                          texto={soEsperando && !busca.trim() ? t('Quando o sistema escrever as 3 mensagens de uma cobrança, ela aparece aqui para você escolher.') : t('Tente outro status ou limpe a busca.')}
                          acao={<button type="button" onClick={() => { escolherFiltro('todos'); setBusca('') }} className="t-label rounded-[8px] border border-line px-3 py-1.5 font-[560] text-silver hover:text-paper">{t('Limpar filtros')}</button>}
                        />
                      )}
                    </td>
                  </tr>
                ) : (
                  ciclos.map((c) => (
                    <tr
                      key={c.id}
                      onClick={() => abrirCiclo(c.id)}
                      className="group cursor-pointer border-t border-line transition-colors hover:bg-paper/[0.035]"
                    >
                      <td className="px-5 py-3.5">
                        <div className="flex items-center gap-2">
                          <span className="font-[560] text-paper">{c.cliente ?? <span className="text-silver">{t('Cliente sem cadastro')}</span>}</span>
                          {c.simulado ? <Badge tone="amber">{t('Demonstração')}</Badge> : null}
                        </div>
                        <div className="t-label text-muted">{c.id_recorrencia}</div>
                      </td>
                      <td className="tabular px-3 py-3.5 font-[560] text-paper">
                        {fmt.brl(c.valor_cobranca)}
                        {c.valor_liquido !== null ? <div className="t-label font-[500] text-ok">{t('{valor} para você', { valor: fmt.brl(c.valor_liquido) })}</div> : null}
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
        </div>
      )}

      <AnimatePresence>
        {aberto !== null ? (
          <motion.div key="drawer" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <CicloDrawer id={aberto} onClose={fecharCiclo} irParaEscolha={abertoNaEscolha} />
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
    <div className="flex items-center gap-2" aria-label={t('{feitas} de {total} tentativas', { feitas, total })}>
      <div className="flex gap-1">
        {Array.from({ length: total }).map((_, i) => (
          <span key={i} className={cx('h-1.5 w-5 rounded-full', i < feitas ? 'bg-orange' : 'bg-paper/[0.12]')} />
        ))}
      </div>
      <span className="tabular text-rotulo text-silver">
        {feitas}/{total}
      </span>
    </div>
  )
}

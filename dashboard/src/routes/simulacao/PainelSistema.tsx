import { AnimatePresence, motion } from 'framer-motion'
import { useState } from 'react'
import { IconArrowRight, IconCheck, IconClock, IconRefresh, IconUndo } from '../../components/icons/Icons'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { ABORDAGEM } from '../../data/simulador'
import type { EstadoSimulacao, EventoLinhaDoTempo, Sugestao } from '../../data/tipos'
import { cx } from '../../lib/cx'
import { fmt } from '../../lib/format'
import { useReducedMotion } from '../../lib/useReducedMotion'

const TOM: Record<NonNullable<EventoLinhaDoTempo['tom']>, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  neutro: 'bg-graphite',
}

const diaSimulado = (iso: string, inicio: string) => {
  const dias = Math.round((new Date(iso).setHours(12, 0, 0, 0) - new Date(inicio).setHours(12, 0, 0, 0)) / 86_400_000)
  return `${fmt.dataCurta(iso)} · dia ${dias}`
}

interface Props {
  estado: EstadoSimulacao
  ocupado: boolean
  onAvancar: (dias: number) => void
  onAvancarAteAcao: () => void
  onRecomecar: () => void
}

/** O lado direito depois de a cobrança começar: relógio, o que o sistema pensa, sem × com a CRAI, linha do tempo. */
export function PainelSistema({ estado, ocupado, onAvancar, onAvancarAteAcao, onRecomecar }: Props) {
  const acabou = !!estado.desfecho
  return (
    <div className="flex flex-col gap-4">
      {/* Relógio simulado */}
      <section className="rounded-[14px] border border-line bg-ink/30 p-4" aria-label="Relógio simulado">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="t-label flex items-center gap-1.5 text-silver">
              <IconClock width={14} height={14} /> Hoje na simulação
            </div>
            <div className="mt-1 text-[18px] font-[640] tracking-[-0.01em] text-paper">{diaSimulado(estado.hoje, estado.inicio)}</div>
          </div>
          {estado.proxima_acao ? (
            <div className="text-right">
              <div className="t-label text-silver">Próxima ação do sistema</div>
              <div className="mt-1 text-apoio font-[560] text-paper">{estado.proxima_acao.descricao}</div>
              <div className="t-label text-silver">{fmt.dataCurta(estado.proxima_acao.quando)}, {fmt.relativo(estado.proxima_acao.quando, new Date(estado.hoje))}</div>
            </div>
          ) : null}
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          {acabou ? (
            <Button size="sm" onClick={onRecomecar}>
              <IconUndo width={15} height={15} /> Simular outro cliente
            </Button>
          ) : (
            <>
              <Button size="sm" onClick={onAvancarAteAcao} disabled={ocupado || !estado.proxima_acao}>
                Avançar até a próxima ação <IconArrowRight width={15} height={15} />
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onAvancar(1)} disabled={ocupado}>
                Avançar 1 dia
              </Button>
              <Button size="sm" variant="quiet" onClick={onRecomecar} disabled={ocupado} className="ml-auto">
                Recomeçar
              </Button>
            </>
          )}
        </div>
        <p className="t-label mt-3 text-muted">O relógio simulado só vale para este cliente fictício. Os dados reais continuam no relógio de verdade.</p>
      </section>

      <Pensando linhas={estado.pensando} chance={estado.chance_recuperar} contribuicoes={estado.contribuicoes} />

      <SemComCrai estado={estado} />

      {/* Linha do tempo */}
      <section className="rounded-[14px] border border-line bg-ink/30 p-4">
        <h3 className="t-h3 text-paper">Linha do tempo</h3>
        <ol className="relative mt-4 ml-1.5 flex flex-col gap-4 border-l border-line pl-5">
          {estado.linha_do_tempo.map((e, i) => (
            <li key={i} className="relative">
              <span aria-hidden="true" className={cx('absolute top-1.5 -left-[25px] h-2.5 w-2.5 rounded-full ring-4 ring-[#1f1710]', TOM[e.tom ?? 'neutro'])} />
              <div className="t-label text-muted">{fmt.dataHora(e.em)}</div>
              <div className="text-apoio font-[560] text-paper">{e.titulo}</div>
              {e.detalhe ? <div className="t-apoio text-silver">{e.detalhe}</div> : null}
            </li>
          ))}
        </ol>
      </section>
    </div>
  )
}

/** Painel recolhível "O que o sistema está pensando". */
function Pensando({ linhas, chance, contribuicoes }: { linhas: string[]; chance: number | null; contribuicoes: EstadoSimulacao['contribuicoes'] }) {
  const [aberto, setAberto] = useState(true)
  const reduzido = useReducedMotion()
  return (
    <section className="rounded-[14px] border border-orange/30 bg-orange/[0.05]">
      <button
        type="button"
        onClick={() => setAberto((a) => !a)}
        aria-expanded={aberto}
        className="flex w-full items-center justify-between gap-3 rounded-[14px] px-4 py-3.5 text-left hover:bg-paper/[0.03]"
      >
        <span className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-orange pulse-orange" aria-hidden="true" />
          <span className="t-h3 text-paper">O que o sistema está pensando</span>
        </span>
        <span className="flex items-center gap-3">
          {chance !== null ? <span className="tabular text-normal font-[640] text-orange">{Math.round(chance * 100)}% de chance</span> : null}
          <IconArrowRight width={16} height={16} className={cx('text-silver transition-transform', aberto && 'rotate-90')} />
        </span>
      </button>
      <AnimatePresence initial={false}>
        {aberto ? (
          <motion.div
            key="corpo"
            initial={reduzido ? false : { height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={reduzido ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4">
              <ol className="flex flex-col gap-2">
                {linhas.map((l, i) => (
                  <li key={i} className={cx('flex gap-2.5 text-apoio leading-[1.5]', i === linhas.length - 1 ? 'text-paper' : 'text-silver')}>
                    <span className="tabular mt-[3px] text-rotulo text-muted">{String(i + 1).padStart(2, '0')}</span>
                    {l}
                  </li>
                ))}
              </ol>
              {contribuicoes.length ? (
                <div className="mt-4 border-t border-line pt-3">
                  <div className="t-label mb-2 text-silver">Por que essa chance</div>
                  <ul className="flex flex-col gap-1">
                    {contribuicoes.map((c) => (
                      <li key={c.fator} className="flex items-center justify-between gap-3 text-rotulo">
                        <span className="text-paper">{c.fator}</span>
                        <span className={cx('tabular shrink-0 font-[560]', c.pontos >= 0 ? 'text-ok' : 'text-[#f08a80]')}>
                          {c.pontos >= 0 ? '+' : ''}
                          {c.pontos} pts
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <p className="t-label mt-3 text-muted">Tudo o que está aqui vem só do que o sistema enxerga: causa, valor, perfil. A verdade escondida fica de fora.</p>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  )
}

/** Sem a CRAI × com a CRAI: só é possível porque a verdade escondida diz o que teria acontecido. */
function SemComCrai({ estado }: { estado: EstadoSimulacao }) {
  if (!estado.sem_crai || !estado.cliente) return null
  const com = estado.desfecho
  const valor = estado.cliente.mensalidade
  return (
    <section className="rounded-[14px] border border-line bg-ink/30 p-4">
      <h3 className="t-h3 text-paper">Sem a CRAI × com a CRAI</h3>
      <p className="t-apoio mt-1 text-silver">A verdade escondida permite dizer o que teria acontecido sem a ação do sistema.</p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Coluna titulo="Sem a CRAI" tom={estado.sem_crai.resultado === 'recuperado' ? 'ok' : 'danger'}>
          <div className="text-[16px] font-[640] text-paper">{estado.sem_crai.resultado === 'recuperado' ? fmt.brl(valor) + ' pago' : fmt.brl(valor) + ' perdidos'}</div>
          <p className="t-apoio mt-1.5 text-silver">{estado.sem_crai.explicacao}</p>
        </Coluna>
        <Coluna titulo="Com a CRAI" tom={com ? (com.tipo === 'recuperado' ? 'ok' : 'danger') : 'andamento'}>
          {com ? (
            com.tipo === 'recuperado' ? (
              <>
                <div className="text-[16px] font-[640] text-paper">{fmt.brl(com.valor_liquido)} para você</div>
                <p className="t-apoio mt-1.5 text-silver">
                  {com.tentativa === 0 ? 'Pagou de primeira; nada a cobrar.' : `Recuperado ${com.via === 'tentativa' ? `na ${com.tentativa}ª tentativa` : 'pela mensagem'}, já descontada a taxa da CRAI.`}
                </p>
              </>
            ) : (
              <>
                <div className="text-[16px] font-[640] text-paper">{fmt.brl(valor)} perdidos</div>
                <p className="t-apoio mt-1.5 text-silver">Nem as tentativas nem a mensagem resolveram. Sem recuperação, a CRAI não cobra nada.</p>
              </>
            )
          ) : (
            <>
              <div className="text-[16px] font-[640] text-paper">Em andamento</div>
              <p className="t-apoio mt-1.5 text-silver">Avance o relógio para ver o desfecho.</p>
            </>
          )}
        </Coluna>
      </div>
    </section>
  )
}

function Coluna({ titulo, tom, children }: { titulo: string; tom: 'ok' | 'danger' | 'andamento'; children: React.ReactNode }) {
  return (
    <div className={cx('rounded-[12px] border p-3.5', tom === 'ok' ? 'border-ok/35 bg-ok/[0.06]' : tom === 'danger' ? 'border-danger/35 bg-danger/[0.06]' : 'border-line bg-paper/[0.03]')}>
      <div className="t-label mb-2 flex items-center gap-1.5 text-silver">
        <span className={cx('h-1.5 w-1.5 rounded-full', tom === 'ok' ? 'bg-ok' : tom === 'danger' ? 'bg-danger' : 'bg-orange pulse-orange')} aria-hidden="true" />
        {titulo}
      </div>
      {children}
    </div>
  )
}

/** As 3 mensagens no lugar do cartão, quando as 3 tentativas falharam. */
export function MensagensSimuladas({ sugestoes, modoEscolha, ocupado, onEscolher }: { sugestoes: Sugestao[]; modoEscolha: boolean; ocupado: boolean; onEscolher: (a: Sugestao['abordagem']) => void }) {
  return (
    <div className="w-full max-w-[560px]">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="t-h3 text-paper">3 mensagens sugeridas</h3>
        <span className="t-label text-warn">Sem escolha em 8 h, a recomendada é enviada</span>
      </div>
      <p className="t-apoio mt-1 text-silver">
        {modoEscolha ? 'Escritas para este cliente, uma por abordagem. Escolha uma ou avance o relógio.' : 'A recomendada será enviada automaticamente (modo automático na configuração).'}
      </p>
      <ul className="mt-4 flex flex-col gap-3">
        {sugestoes.map((s) => (
          <li key={s.abordagem} className={cx('rounded-[14px] border p-4', s.recomendada ? 'border-orange/50 bg-orange/[0.06]' : 'border-line bg-ink/30')}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-[600] text-paper">{ABORDAGEM[s.abordagem]}</span>
              {s.recomendada ? <Badge tone="orange">Recomendada</Badge> : null}
              <Badge className="ml-auto">WhatsApp</Badge>
            </div>
            <p className="t-apoio mt-2 text-paper/90">{s.texto}</p>
            <div className="mt-3 flex items-center justify-between gap-3">
              <span className="t-label text-muted">{s.motivo_canal}</span>
              {modoEscolha ? (
                <Button size="sm" variant={s.recomendada ? 'primary' : 'ghost'} disabled={ocupado} onClick={() => onEscolher(s.abordagem)}>
                  <IconCheck width={15} height={15} /> Enviar esta
                </Button>
              ) : null}
            </div>
          </li>
        ))}
      </ul>
      <p className="t-label mt-3 flex items-center gap-1.5 text-muted">
        <IconRefresh width={13} height={13} /> Na página do involuntário dá para pedir outras três. Aqui, a demonstração segue com estas.
      </p>
    </div>
  )
}

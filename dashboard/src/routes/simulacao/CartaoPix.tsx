import { motion } from 'framer-motion'
import type { ReactNode } from 'react'
import { Badge } from '../../components/ui/Badge'
import type { ClienteFicticio, EstadoSimulacao } from '../../data/tipos'
import { cx } from '../../lib/cx'
import { fmt } from '../../lib/format'
import { useReducedMotion } from '../../lib/useReducedMotion'
import { CAUSA } from '../../data/simulador'

/** O que o cartão está fazendo agora, na linguagem do brilho. */
export type Brilho = 'nenhum' | 'processando' | 'recusada' | 'recuperada' | 'aguardando' | 'encerrada'

const SOMBRA: Record<Brilho, string> = {
  nenhum: '0 30px 70px -30px rgba(0,0,0,0.8)',
  processando: '0 0 0 0 rgba(239,147,17,0)',
  recusada: '0 0 90px 10px rgba(194,69,58,0.45), 0 30px 70px -30px rgba(0,0,0,0.8)',
  recuperada: '0 0 90px 10px rgba(63,178,111,0.45), 0 30px 70px -30px rgba(0,0,0,0.8)',
  aguardando: '0 0 60px 4px rgba(255,184,108,0.18), 0 30px 70px -30px rgba(0,0,0,0.8)',
  encerrada: '0 30px 70px -30px rgba(0,0,0,0.8)',
}
const BORDA: Record<Brilho, string> = {
  nenhum: 'rgba(166,170,173,0.18)',
  processando: 'rgba(239,147,17,0.7)',
  recusada: 'rgba(194,69,58,0.8)',
  recuperada: 'rgba(63,178,111,0.8)',
  aguardando: 'rgba(255,184,108,0.35)',
  encerrada: 'rgba(166,170,173,0.25)',
}

interface Props {
  cliente: ClienteFicticio | null
  empresa: string
  idRecorrencia: string
  proximaCobranca: string | null
  virado: boolean
  brilho: Brilho
  faixa?: ReactNode // a faixa de status embaixo do cartão (recusada, recuperada…)
  estado?: EstadoSimulacao | null
}

/**
 * O cartão da autorização Pix Automático (frente) que vira para a verdade escondida (verso).
 * Sem bandeira, sem logo de banco, sem logo do Pix: só o texto. Nunca mostra chave, CPF ou conta.
 */
export function CartaoPix({ cliente, empresa, idRecorrencia, proximaCobranca, virado, brilho, faixa, estado }: Props) {
  const reduzido = useReducedMotion()
  const processando = brilho === 'processando'

  const frente = (
    <Face className="justify-between">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="t-label tracking-[0.08em] whitespace-nowrap text-silver uppercase">Pix Automático</div>
          <div className="mt-0.5 text-rotulo text-muted">Autorização de débito recorrente</div>
        </div>
        <Badge tone="amber">Dados fictícios</Badge>
      </div>
      <div>
        <div className="t-label text-silver">Mensalidade</div>
        <div className="mt-1 text-[34px] leading-none font-[680] tracking-[-0.03em] text-paper">
          {cliente && cliente.mensalidade > 0 ? fmt.brl(cliente.mensalidade) : 'R$ —'}
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3">
        <Dado rotulo="Pagador" valor={cliente?.nome || 'Nome fictício'} />
        <Dado rotulo="Recebedor" valor={empresa} />
        <Dado rotulo="Recorrência" valor={idRecorrencia ? `${idRecorrencia.slice(0, 7)}••••` : 'RN_sim_••••'} mono />
        <Dado rotulo="Próxima cobrança" valor={proximaCobranca ? fmt.dataCurta(proximaCobranca) : '—'} />
      </dl>
    </Face>
  )

  const v = cliente?.verdade
  const verso = (
    <Face className="justify-between border-dashed">
      <div className="-mx-5 -mt-5 rounded-t-[18px] border-b border-dashed border-amber/40 bg-amber/[0.08] px-5 py-2.5 sm:-mx-6 sm:-mt-6 sm:px-6">
        <div className="t-label flex items-center gap-2 text-amber">
          <span aria-hidden="true">◐</span> O sistema não vê este lado
        </div>
      </div>
      <p className="t-apoio text-silver">O que acontece de verdade quando a cobrança chega ao banco do cliente. Os modelos da CRAI decidem sem ver isso.</p>
      <dl className="grid grid-cols-3 gap-3">
        <Dado rotulo="Dinheiro entra em" valor={v ? (v.dias_ate_saldo === 0 ? 'Já tem' : `${v.dias_ate_saldo} ${v.dias_ate_saldo === 1 ? 'dia' : 'dias'}`) : '—'} grande />
        <Dado rotulo="Chance de pagar" valor={v ? `${Math.round(v.chance_pagar * 100)}%` : '—'} grande />
        <Dado rotulo="Vai revogar" valor={v ? (v.vai_revogar ? 'Sim' : 'Não') : '—'} grande />
      </dl>
    </Face>
  )

  return (
    <div className="flex w-full max-w-[460px] flex-col items-stretch gap-3">
      <div className="relative w-full" style={{ perspective: 1400 }}>
        <motion.div
          className="relative min-h-[280px] w-full sm:aspect-[1.586]"
          style={{ transformStyle: 'preserve-3d' }}
          animate={{ rotateY: virado ? 180 : 0 }}
          transition={reduzido ? { duration: 0 } : { duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
        >
          <motion.div
            className="absolute inset-0 rounded-[18px]"
            style={{ backfaceVisibility: 'hidden', WebkitBackfaceVisibility: 'hidden' }}
            animate={
              processando && !reduzido
                ? { boxShadow: ['0 0 0 0 rgba(239,147,17,0.0)', '0 0 90px 14px rgba(239,147,17,0.5)', '0 0 30px 2px rgba(239,147,17,0.15)'] }
                : { boxShadow: processando ? '0 0 70px 8px rgba(239,147,17,0.4)' : SOMBRA[brilho] }
            }
            transition={processando && !reduzido ? { duration: 1.4, repeat: Infinity, ease: 'easeInOut' } : { duration: 0.5 }}
          >
            <div className="h-full rounded-[18px]" style={{ boxShadow: `inset 0 0 0 1px ${BORDA[brilho]}` }}>
              {frente}
            </div>
          </motion.div>
          <div
            className="absolute inset-0 rounded-[18px]"
            style={{ backfaceVisibility: 'hidden', WebkitBackfaceVisibility: 'hidden', transform: 'rotateY(180deg)', boxShadow: SOMBRA.nenhum }}
          >
            {verso}
          </div>
        </motion.div>
      </div>
      {faixa}
      {estado?.causa && brilho === 'recusada' ? <span className="sr-only">Cobrança recusada: {CAUSA[estado.causa]}</span> : null}
    </div>
  )
}

function Face({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cx(
        'flex h-full flex-col rounded-[18px] border border-line p-5 sm:p-6',
        'bg-[linear-gradient(150deg,#332a20_0%,#231b13_55%,#1a120a_100%)]',
        className,
      )}
    >
      {children}
    </div>
  )
}

function Dado({ rotulo, valor, mono, grande }: { rotulo: string; valor: string; mono?: boolean; grande?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="t-label text-muted">{rotulo}</dt>
      <dd className={cx('truncate', grande ? 'mt-0.5 text-[18px] font-[640] text-paper' : 'text-apoio font-[560] text-paper', mono && 'tabular')}>{valor}</dd>
    </div>
  )
}

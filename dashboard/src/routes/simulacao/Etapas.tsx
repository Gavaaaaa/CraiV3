import { IconCheck } from '../../components/icons/Icons'
import type { EstadoSimulacao, EtapaSimulacao } from '../../data/tipos'
import { cx } from '../../lib/cx'

const ETAPAS: { valor: EtapaSimulacao; rotulo: string }[] = [
  { valor: 'cobranca', rotulo: 'Cobrança' },
  { valor: 'tentativa_1', rotulo: 'Tentativa 1' },
  { valor: 'tentativa_2', rotulo: 'Tentativa 2' },
  { valor: 'tentativa_3', rotulo: 'Tentativa 3' },
  { valor: 'mensagem', rotulo: 'Mensagem' },
  { valor: 'desfecho', rotulo: 'Desfecho' },
]

/** As etapas do ciclo no topo, como no vídeo: check nas concluídas, a atual em laranja. */
export function Etapas({ estado }: { estado: EstadoSimulacao }) {
  const comecou = estado.fase !== 'formulario'
  const canceladas = new Set(estado.tentativas.filter((t) => t.resultado === 'cancelada').map((t) => `tentativa_${t.numero}`))
  const pagas = new Set(estado.tentativas.filter((t) => t.resultado === 'paga').map((t) => `tentativa_${t.numero}`))
  const puladas = new Set<EtapaSimulacao>()
  if (estado.desfecho?.via === 'tentativa') {
    puladas.add('mensagem')
    estado.tentativas.filter((t) => t.resultado === 'agendada').forEach((t) => puladas.add(`tentativa_${t.numero}` as EtapaSimulacao))
  }

  return (
    <ol className="scroll-fino relative flex items-start gap-0 overflow-x-auto pb-1" aria-label="Etapas do ciclo">
      {ETAPAS.map((e, i) => {
        const concluida = estado.etapas_concluidas.includes(e.valor) && !canceladas.has(e.valor) && !puladas.has(e.valor)
        const atual = comecou && !estado.desfecho && estado.etapa_atual === e.valor
        const cancelada = canceladas.has(e.valor) || puladas.has(e.valor)
        const fim = e.valor === 'desfecho' && !!estado.desfecho
        const ok = fim && estado.desfecho?.tipo === 'recuperado'
        return (
          <li key={e.valor} className="flex min-w-[96px] flex-1 flex-col items-center">
            <div className="flex w-full items-center">
              <span className={cx('h-px flex-1', i === 0 ? 'bg-transparent' : concluida || atual || cancelada || fim ? 'bg-orange/50' : 'bg-line')} aria-hidden="true" />
              <span
                className={cx(
                  'flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-[12px] font-[600] transition-colors duration-300',
                  fim && ok && 'border-ok bg-ok text-ink',
                  fim && !ok && 'border-graphite bg-graphite text-paper',
                  !fim && concluida && (pagas.has(e.valor) ? 'border-ok bg-ok text-ink' : 'border-orange bg-orange text-ink'),
                  !fim && atual && 'border-orange bg-orange/15 text-orange pulse-orange',
                  !fim && cancelada && 'border-line bg-ink/40 text-muted line-through',
                  !fim && !concluida && !atual && !cancelada && 'border-line bg-ink/40 text-muted',
                )}
                aria-hidden="true"
              >
                {concluida || fim ? <IconCheck width={15} height={15} /> : cancelada ? '–' : i + 1}
              </span>
              <span className={cx('h-px flex-1', i === ETAPAS.length - 1 ? 'bg-transparent' : concluida || fim ? 'bg-orange/50' : 'bg-line')} aria-hidden="true" />
            </div>
            <span className={cx('mt-2 text-center text-[12.5px] font-[520] whitespace-nowrap', atual ? 'text-orange' : concluida || fim ? 'text-paper' : 'text-silver')}>
              {e.rotulo}
              <span className="sr-only">
                {fim ? (ok ? ': recuperado' : ': encerrado') : concluida ? ': concluída' : atual ? ': etapa atual' : cancelada ? ': cancelada' : ''}
              </span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}

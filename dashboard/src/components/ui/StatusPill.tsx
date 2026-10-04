import type { StatusTela } from '../../data/tipos'
import { cx } from '../../lib/cx'

/** Status com cor, ponto e texto: nunca só a cor. */
export const STATUS: Record<StatusTela, { rotulo: string; classe: string; ponto: string }> = {
  em_analise: { rotulo: 'Em análise', classe: 'border-warn/45 text-warn', ponto: 'bg-warn' },
  em_processo: { rotulo: 'Em processo', classe: 'border-orange/45 text-orange', ponto: 'bg-orange pulse-orange' },
  recuperado: { rotulo: 'Recuperado', classe: 'border-ok/45 text-ok', ponto: 'bg-ok' },
  encerrado: { rotulo: 'Encerrado sem recuperação', classe: 'border-line text-silver', ponto: 'bg-graphite' },
}

export function StatusPill({ status, className }: { status: StatusTela; className?: string }) {
  const s = STATUS[status]
  return (
    <span
      className={cx(
        'inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-rotulo font-[520] whitespace-nowrap',
        s.classe,
        className,
      )}
    >
      <span className={cx('h-1.5 w-1.5 rounded-full', s.ponto)} aria-hidden="true" />
      {s.rotulo}
    </span>
  )
}

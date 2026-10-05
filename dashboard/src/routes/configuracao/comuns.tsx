import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'

/** Cabeçalho de seção da configuração. */
export function Secao({ titulo, apoio, children, acoes }: { titulo: string; apoio?: ReactNode; children: ReactNode; acoes?: ReactNode }) {
  return (
    <section className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="t-h2 text-paper">{titulo}</h3>
          {apoio ? <p className="t-apoio mt-1 max-w-[640px] text-silver">{apoio}</p> : null}
        </div>
        {acoes}
      </div>
      {children}
    </section>
  )
}

/** Um bloco dentro da seção. */
export function Bloco({ titulo, apoio, children, className }: { titulo: string; apoio?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cx('rounded-[14px] border border-line bg-ink/25 p-5', className)}>
      <h4 className="t-h3 text-paper">{titulo}</h4>
      {apoio ? <p className="t-apoio mt-1 text-silver">{apoio}</p> : null}
      <div className="mt-4">{children}</div>
    </div>
  )
}

export const campo = 'h-10 w-full rounded-[10px] border border-campo bg-ink/40 px-3 text-apoio text-paper placeholder:text-muted focus:border-amber/60 focus:outline-none disabled:opacity-60'
export const seletor = cx(campo, 'seletor')

export function Rotulo({ children, apoio }: { children: ReactNode; apoio?: string }) {
  return (
    <span className="mb-1.5 block">
      <span className="t-label text-silver">{children}</span>
      {apoio ? <span className="t-label block text-muted">{apoio}</span> : null}
    </span>
  )
}

/** Interruptor acessível (checkbox por baixo). */
export function Interruptor({ ligado, onChange, rotulo, apoio, disabled }: { ligado: boolean; onChange: (v: boolean) => void; rotulo: string; apoio?: string; disabled?: boolean }) {
  return (
    <label className={cx('flex items-start justify-between gap-4 py-3', disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer')}>
      <span>
        <span className="block text-apoio text-paper">{rotulo}</span>
        {apoio ? <span className="t-label block text-silver">{apoio}</span> : null}
      </span>
      <span className="relative mt-0.5 inline-flex h-6 w-11 shrink-0 items-center">
        <input type="checkbox" checked={ligado} disabled={disabled} onChange={(e) => onChange(e.target.checked)} className="peer sr-only" />
        <span className="absolute inset-0 rounded-full bg-paper/[0.12] transition-colors peer-checked:bg-orange peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-amber" />
        <span className="absolute left-0.5 h-5 w-5 rounded-full bg-paper transition-transform peer-checked:translate-x-5" />
      </span>
    </label>
  )
}

/** Aviso curto dentro de um bloco. */
export function Aviso({ tom = 'neutro', children }: { tom?: 'neutro' | 'ok' | 'warn' | 'danger'; children: ReactNode }) {
  const classes = {
    neutro: 'border-line bg-paper/[0.03] text-silver',
    ok: 'border-ok/40 bg-ok/[0.06] text-paper',
    warn: 'border-warn/40 bg-warn/[0.06] text-paper',
    danger: 'border-danger/40 bg-danger/[0.06] text-paper',
  }[tom]
  return (
    <div role={tom === 'danger' || tom === 'warn' ? 'alert' : 'status'} className={cx('rounded-[10px] border px-3.5 py-2.5 text-apoio leading-[1.5]', classes)}>
      {children}
    </div>
  )
}

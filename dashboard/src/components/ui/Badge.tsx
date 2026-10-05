import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'

type Tone = 'neutral' | 'orange' | 'amber' | 'ok' | 'warn' | 'danger' | 'ink'

const tones: Record<Tone, string> = {
  neutral: 'border-line text-silver',
  orange: 'border-orange/40 text-orange',
  amber: 'border-amber/40 text-amber',
  ok: 'border-ok/45 text-ok',
  warn: 'border-warn/50 text-warn',
  danger: 'border-danger/50 text-danger-texto',
  ink: 'border-sobre-destaque/20 bg-sobre-destaque/15 text-sobre-destaque',
}

export function Badge({ children, tone = 'neutral', className }: { children: ReactNode; tone?: Tone; className?: string }) {
  return (
    <span
      className={cx(
        'inline-flex items-center gap-1.5 rounded-[6px] border px-2 py-0.5 text-rotulo leading-[1.45] font-[500] whitespace-nowrap tabular',
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  )
}

import type { ReactNode } from 'react'
import { cx } from '../../lib/cx'
import { Badge } from './Badge'
import { Card } from './Card'

interface Props {
  rotulo: string
  valor: ReactNode
  apoio?: ReactNode
  icone?: ReactNode
  tone?: 'glass' | 'orange'
  hero?: boolean
  /** O número ainda é de demonstração (modo real ligado, rota ainda não integrada). */
  demo?: boolean
  className?: string
}

/** Cartão de número: rótulo, valor, uma linha de apoio. O hero é um só por tela. */
export function StatTile({ rotulo, valor, apoio, icone, tone = 'glass', hero = false, demo = false, className }: Props) {
  const escuro = tone === 'orange'
  return (
    <Card tone={tone} className={cx('flex flex-col justify-between p-5', className)}>
      <div className="flex items-start justify-between gap-3">
        <span className={cx('t-label', escuro ? 'text-sobre-destaque' : 'text-silver')}>
          {rotulo}
          {demo ? <Badge tone={escuro ? 'ink' : 'amber'} className="ml-2 align-middle">Demonstração</Badge> : null}
        </span>
        {icone ? (
          <span
            className={cx(
              'flex h-8 w-8 items-center justify-center rounded-[10px]',
              escuro ? 'bg-sobre-destaque/15 text-sobre-destaque' : 'bg-paper/[0.06] text-amber',
            )}
          >
            {icone}
          </span>
        ) : null}
      </div>
      <div className={cx('mt-4', hero ? 't-hero' : 't-number', escuro ? 'text-sobre-destaque' : 'text-paper')}>{valor}</div>
      {apoio ? <div className={cx('t-apoio mt-2', escuro ? 'text-sobre-destaque' : 'text-silver')}>{apoio}</div> : null}
    </Card>
  )
}

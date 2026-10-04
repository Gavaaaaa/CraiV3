import type { HTMLAttributes, PointerEvent, ReactNode } from 'react'
import { cx } from '../../lib/cx'

interface CardProps extends HTMLAttributes<HTMLElement> {
  as?: 'div' | 'article' | 'section' | 'li' | 'aside'
  tone?: 'glass' | 'orange'
  children: ReactNode
}

/** Cartão do bento: vidro quente com brilho que segue o cursor (o mesmo Card do site). */
export function Card({ as: Tag = 'div', tone = 'glass', className, children, onPointerMove, ...rest }: CardProps) {
  function mover(e: PointerEvent<HTMLElement>) {
    if (e.pointerType === 'mouse') {
      const r = e.currentTarget.getBoundingClientRect()
      e.currentTarget.style.setProperty('--mx', `${e.clientX - r.left}px`)
      e.currentTarget.style.setProperty('--my', `${e.clientY - r.top}px`)
    }
    onPointerMove?.(e)
  }
  return (
    <Tag
      className={cx('anim-entrada rounded-[18px]', tone === 'orange' ? 'card-orange' : 'card-glass spotlight', className)}
      onPointerMove={mover}
      {...rest}
    >
      {children}
    </Tag>
  )
}

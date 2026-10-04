import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cx } from '../../lib/cx'

type Variant = 'primary' | 'ghost' | 'quiet'
type Size = 'sm' | 'md'

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
  children: ReactNode
}

const variants: Record<Variant, string> = {
  // Ação em claro sobre escuro: o laranja fica reservado para sinal (seleção, estado).
  primary: 'btn-shine rounded-[8px] bg-paper text-ink hover:bg-white',
  ghost: 'rounded-[8px] border border-graphite text-paper hover:border-silver hover:bg-paper/[0.04]',
  quiet: 'rounded-[8px] text-silver hover:bg-paper/[0.06] hover:text-paper',
}
const sizes: Record<Size, string> = { sm: 'h-8 px-3 text-[13px]', md: 'h-10 px-4 text-[14px]' }

export function Button({ variant = 'primary', size = 'md', className, children, ...rest }: Props) {
  return (
    <button
      className={cx(
        'relative inline-flex items-center justify-center gap-2 font-[560] whitespace-nowrap transition-[background-color,border-color,color,transform] duration-150 ease-out select-none active:scale-[0.985] disabled:cursor-not-allowed disabled:opacity-60',
        variants[variant],
        sizes[size],
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  )
}

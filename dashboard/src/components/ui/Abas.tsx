import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cx } from '../../lib/cx'

export interface Aba<T extends string> {
  valor: T
  rotulo: string
  /** Um sinal ao lado do rótulo (contagem, alerta). */
  extra?: ReactNode
}

/**
 * Abas em pílula, no mesmo desenho do alternador do involuntário: a ativa em claro.
 * Setas do teclado trocam de aba (padrão de acessibilidade para "tablist").
 * No celular, a fileira rola para o lado em vez de quebrar.
 */
export function Abas<T extends string>({
  abas,
  ativa,
  onChange,
  rotulo,
  idBase,
}: {
  abas: Aba<T>[]
  ativa: T
  onChange: (v: T) => void
  rotulo: string
  idBase: string
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const trilho = useRef<HTMLDivElement>(null)

  // No celular a fileira rola: traz a aba ativa para dentro da tela, sem mexer na rolagem da página.
  useEffect(() => {
    const t = trilho.current
    const b = refs.current[abas.findIndex((a) => a.valor === ativa)]
    if (!t || !b) return
    const esq = b.offsetLeft - 8
    const dir = b.offsetLeft + b.offsetWidth + 8 - t.clientWidth
    if (esq < t.scrollLeft) t.scrollLeft = esq
    else if (dir > t.scrollLeft) t.scrollLeft = dir
  }, [ativa, abas])

  function teclado(e: KeyboardEvent<HTMLDivElement>) {
    const i = abas.findIndex((a) => a.valor === ativa)
    let j = i
    if (e.key === 'ArrowRight') j = (i + 1) % abas.length
    else if (e.key === 'ArrowLeft') j = (i - 1 + abas.length) % abas.length
    else if (e.key === 'Home') j = 0
    else if (e.key === 'End') j = abas.length - 1
    else return
    e.preventDefault()
    onChange(abas[j].valor)
    refs.current[j]?.focus()
  }

  return (
    <div ref={trilho} className="scroll-fino relative -mx-1 max-w-[calc(100%+8px)] min-w-0 overflow-x-auto px-1 pb-1">
      <div
        role="tablist"
        aria-label={rotulo}
        onKeyDown={teclado}
        className="inline-flex min-w-max gap-1 rounded-[12px] border border-line bg-slate/50 p-1"
      >
        {abas.map((a, i) => {
          const sel = a.valor === ativa
          return (
            <button
              key={a.valor}
              ref={(el) => {
                refs.current[i] = el
              }}
              type="button"
              role="tab"
              id={`${idBase}-aba-${a.valor}`}
              aria-selected={sel}
              aria-controls={`${idBase}-painel-${a.valor}`}
              tabIndex={sel ? 0 : -1}
              onClick={() => onChange(a.valor)}
              className={cx(
                'inline-flex items-center gap-2 rounded-[8px] px-3.5 py-2 text-[13.5px] whitespace-nowrap transition-colors duration-200',
                sel ? 'bg-paper font-[580] text-ink' : 'font-[520] text-silver hover:bg-paper/[0.05] hover:text-paper',
              )}
            >
              {a.rotulo}
              {a.extra}
            </button>
          )
        })}
      </div>
    </div>
  )
}

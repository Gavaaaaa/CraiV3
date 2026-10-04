import { cx } from '../../lib/cx'

export type Modo = 'reais' | 'simulacao'

/** O alternador flutuante do vídeo ("Dashboard 1 / 2"), aqui "Dados reais / Simulação". */
export function ModoToggle({ modo, onChange }: { modo: Modo; onChange: (m: Modo) => void }) {
  const opcoes: { valor: Modo; rotulo: string }[] = [
    { valor: 'reais', rotulo: 'Dados reais' },
    { valor: 'simulacao', rotulo: 'Simulação' },
  ]
  return (
    <div
      role="radiogroup"
      aria-label="Modo dos dados"
      className="fixed bottom-5 left-1/2 z-40 flex -translate-x-1/2 items-center gap-1 rounded-full border border-line bg-bar/90 p-1 shadow-[0_20px_50px_-20px_rgba(0,0,0,0.9)] backdrop-blur-md"
    >
      <span className="t-label pr-1 pl-3 text-muted">Mostrar</span>
      {opcoes.map((o) => (
        <button
          key={o.valor}
          type="button"
          role="radio"
          aria-checked={modo === o.valor}
          onClick={() => onChange(o.valor)}
          className={cx(
            'rounded-full px-3.5 py-1.5 text-[13px] font-[560] transition-colors duration-200',
            modo === o.valor ? 'bg-paper text-ink' : 'text-silver hover:text-paper',
          )}
        >
          {o.rotulo}
        </button>
      ))}
    </div>
  )
}

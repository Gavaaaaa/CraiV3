import type { ReactNode } from 'react'
import { IconAlert, IconRefresh } from '../icons/Icons'
import { cx } from '../../lib/cx'
import { Button } from './Button'

/** Erro ao carregar: diz o que houve, em português, e oferece tentar de novo. */
export function ErroCarregar({ mensagem, onTentar, className }: { mensagem: string; onTentar: () => void; className?: string }) {
  return (
    <div role="alert" className={cx('flex flex-col items-center justify-center gap-3 rounded-[14px] border border-danger/40 bg-danger/[0.06] p-6 text-center', className)}>
      <span className="flex h-10 w-10 items-center justify-center rounded-[12px] bg-danger/15 text-[#f5a29a]">
        <IconAlert width={20} height={20} />
      </span>
      <div>
        <div className="text-normal font-[600] text-paper">Não deu para carregar</div>
        <p className="t-apoio mt-1 max-w-md text-silver">{mensagem} Os seus dados continuam guardados; só a tela não conseguiu buscá-los agora.</p>
      </div>
      <Button size="sm" variant="ghost" onClick={onTentar}>
        <IconRefresh width={15} height={15} /> Tentar de novo
      </Button>
    </div>
  )
}

/** Estado vazio: o que significa e o que fazer, nunca só "nenhum dado". */
export function Vazio({ titulo, texto, acao, icone, className }: { titulo: string; texto: string; acao?: ReactNode; icone?: ReactNode; className?: string }) {
  return (
    <div className={cx('flex flex-col items-center justify-center gap-3 p-8 text-center', className)}>
      {icone ? <span className="flex h-11 w-11 items-center justify-center rounded-[12px] bg-paper/[0.06] text-amber">{icone}</span> : null}
      <div>
        <div className="text-normal font-[600] text-paper">{titulo}</div>
        <p className="t-apoio mt-1 max-w-md text-silver">{texto}</p>
      </div>
      {acao}
    </div>
  )
}

/** Esqueleto de carregamento, com a altura do que vai chegar. */
export function Carregando({ altura = 320, className }: { altura?: number; className?: string }) {
  return (
    <div className={cx('flex flex-col gap-3', className)} style={{ minHeight: altura }} aria-busy="true" aria-label="Carregando">
      <div className="h-4 w-1/3 animate-pulse rounded bg-paper/[0.07]" />
      <div className="h-3 w-1/2 animate-pulse rounded bg-paper/[0.05]" />
      <div className="mt-4 flex-1 animate-pulse rounded-[12px] bg-paper/[0.04]" />
    </div>
  )
}

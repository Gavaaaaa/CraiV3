import { emDemonstracao } from '../../data/api'
import { cx } from '../../lib/cx'
import { Badge } from './Badge'

/**
 * A etiqueta dos blocos que ainda mostram dado fictício COM o modo real ligado. `de` são os
 * nomes das funções de `api.ts` que alimentam o bloco: se alguma ainda não fala com o
 * backend, a etiqueta aparece. Sem o modo real, nunca aparece (nada muda na tela).
 */
export function Demonstracao({ de, escuro = false, className }: { de: string[]; escuro?: boolean; className?: string }) {
  if (!emDemonstracao(...de)) return null
  return (
    <Badge tone={escuro ? 'ink' : 'amber'} className={className}>
      Demonstração
    </Badge>
  )
}

/** A mesma etiqueta, numa faixa acima de um bloco inteiro (uma aba, uma seção). */
export function FaixaDemonstracao({ de, className }: { de: string[]; className?: string }) {
  if (!emDemonstracao(...de)) return null
  return (
    <div className={cx('mb-2 flex items-center justify-end gap-2', className)}>
      <span className="t-label text-muted">Este bloco ainda usa dados fictícios</span>
      <Badge tone="amber">Demonstração</Badge>
    </div>
  )
}

import { etiquetaDeDemonstracao } from '../../data/api'
import { cx } from '../../lib/cx'
import { t } from '../../lib/idioma'
import { Badge } from './Badge'

/**
 * A etiqueta dos blocos que ainda mostram dado fictício COM o modo real ligado. `de` são os
 * nomes das funções de `api.ts` que alimentam o bloco: se alguma ainda não fala com o
 * backend, a etiqueta aparece. Sem o modo real, nunca aparece (nada muda na tela).
 *
 * Desde a Rodada 2 a etiqueta só aparece com `VITE_CRAI_MOSTRAR_DEMONSTRACAO=1`
 * (`etiquetaDeDemonstracao`, em `api.ts`). O padrão é não aparecer.
 */
export function Demonstracao({ de, escuro = false, className }: { de: string[]; escuro?: boolean; className?: string }) {
  if (!etiquetaDeDemonstracao(...de)) return null
  return (
    <Badge tone={escuro ? 'ink' : 'amber'} className={className}>
      {t('Demonstração')}
    </Badge>
  )
}

/** A mesma etiqueta, numa faixa acima de um bloco inteiro (uma aba, uma seção). */
export function FaixaDemonstracao({ de, className }: { de: string[]; className?: string }) {
  if (!etiquetaDeDemonstracao(...de)) return null
  return (
    <div className={cx('mb-2 flex items-center justify-end gap-2', className)}>
      <span className="t-label text-muted">{t('Este bloco ainda usa dados fictícios')}</span>
      <Badge tone="amber">{t('Demonstração')}</Badge>
    </div>
  )
}

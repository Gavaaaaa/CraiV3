import { MODO_REAL } from '../../data/api'
import type { Empresa, Papel } from '../../data/tipos'
import { t } from '../../lib/idioma'
import { BuscaDoTopo } from './BuscaDoTopo'
import { SinoDoTopo } from './SinoDoTopo'

/** O papel, em português, como a Configuração já o chama. */
export const PAPEL_LEGIVEL: Record<Papel, string> = { owner: t('Dono'), admin: t('Administrador'), membro: t('Membro') }

/**
 * Do vídeo: saudação à esquerda; busca, sino, plano e papel à direita. A busca e o sino são de
 * verdade (Rodada 4). A data saiu do topo: na demonstração ela era a data fixa dos dados de
 * exemplo, e parecia errada.
 */
export function Topbar({ empresa, onTrocarPapel }: { empresa: Empresa | null; onTrocarPapel?: (papel: Papel) => void }) {
  const papel = empresa?.papel ?? null
  return (
    <header className="flex items-center justify-between gap-6 px-4 pt-6 pb-2 md:px-8">
      <div className="min-w-0">
        <h1 className="t-h1 truncate text-paper">
          {t('Olá, {nome}', { nome: empresa?.nome ?? '…' })}
        </h1>
        <p className="t-apoio mt-1 text-silver">{t('O que a CRAI fez pela sua receita hoje.')}</p>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <BuscaDoTopo />
        <SinoDoTopo />
        <div className="hidden items-center gap-3 rounded-[10px] border border-line bg-slate/60 px-3 py-2 md:flex">
          <div className="hidden text-right lg:block">
            <div className="t-label text-silver">{empresa?.plano === 'premium' ? t('Plano premium') : t('Plano essencial')}</div>
          </div>
          {MODO_REAL && onTrocarPapel ? (
            // Login de desenvolvimento: o selo do papel É o controle. Trocar o papel aqui pede outro token.
            <select
              aria-label={t('Papel do login de desenvolvimento')}
              title={t('Login de desenvolvimento: troque o papel para testar as permissões')}
              value={papel ?? 'owner'}
              onChange={(e) => onTrocarPapel(e.target.value as Papel)}
              className="seletor h-8 rounded-[8px] border border-orange bg-transparent pl-2.5 text-rotulo font-[560] text-orange focus:border-amber/60 focus:outline-none"
            >
              {(Object.keys(PAPEL_LEGIVEL) as Papel[]).map((p) => (
                <option key={p} value={p}>{PAPEL_LEGIVEL[p]}</option>
              ))}
            </select>
          ) : (
            // Fora do login de desenvolvimento, o papel é só informação: sem borda, sem cursor, sem realce.
            <div className="text-rotulo font-[560] text-paper" data-papel={papel ?? undefined}>
              {papel ? PAPEL_LEGIVEL[papel] : '—'}
            </div>
          )}
        </div>
      </div>
    </header>
  )
}

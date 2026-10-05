import { MODO_REAL, agoraDaTela } from '../../data/api'
import type { Empresa, Papel } from '../../data/tipos'
import { fmt } from '../../lib/format'
import { Badge } from '../ui/Badge'
import { BuscaDoTopo } from './BuscaDoTopo'
import { SinoDoTopo } from './SinoDoTopo'

/** Do vídeo: saudação à esquerda; busca, sino e data à direita. A busca e o sino são de verdade (Rodada 4). */
export function Topbar({ empresa, onTrocarPapel }: { empresa: Empresa | null; onTrocarPapel?: (papel: Papel) => void }) {
  return (
    <header className="flex items-center justify-between gap-6 px-4 pt-6 pb-2 md:px-8">
      <div className="min-w-0">
        <h1 className="t-h1 truncate text-paper">
          Olá, {empresa?.nome ?? '…'}
        </h1>
        <p className="t-apoio mt-1 text-silver">O que a CRAI fez pela sua receita hoje.</p>
      </div>
      <div className="flex shrink-0 items-center gap-3">
        <BuscaDoTopo />
        {MODO_REAL && onTrocarPapel ? (
          <label className="hidden items-center gap-2 md:flex" title="Login de desenvolvimento: troque o papel para testar as permissões">
            <span className="t-label text-muted">Papel</span>
            <select
              aria-label="Papel do login de desenvolvimento"
              value={empresa?.papel ?? 'owner'}
              onChange={(e) => onTrocarPapel(e.target.value as Papel)}
              className="h-8 rounded-[8px] border border-line bg-slate/60 px-2 text-rotulo text-silver focus:border-amber/60 focus:outline-none"
            >
              <option value="owner">Dono</option>
              <option value="admin">Administrador</option>
              <option value="membro">Membro</option>
            </select>
          </label>
        ) : null}
        <SinoDoTopo />
        <div className="hidden items-center gap-3 rounded-[10px] border border-line bg-slate/60 px-3 py-2 lg:flex">
          <div className="text-right">
            <div className="text-rotulo font-[560] text-paper">{fmt.dataLonga(agoraDaTela())}</div>
            <div className="t-label text-silver">{empresa?.plano === 'premium' ? 'Plano premium' : 'Plano essencial'}</div>
          </div>
          <Badge tone={empresa?.papel === 'owner' ? 'orange' : 'neutral'}>{empresa ? empresa.papel.charAt(0).toUpperCase() + empresa.papel.slice(1) : '—'}</Badge>
        </div>
      </div>
    </header>
  )
}

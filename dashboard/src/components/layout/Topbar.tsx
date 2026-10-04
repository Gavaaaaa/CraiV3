import { MODO_REAL, agoraDaTela } from '../../data/api'
import type { Empresa, Papel } from '../../data/tipos'
import { fmt } from '../../lib/format'
import { Badge } from '../ui/Badge'
import { IconBell, IconSearch } from '../icons/Icons'

/** Do vídeo: saudação à esquerda; busca, sino e data à direita. */
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
        <label className="relative hidden md:block">
          <span className="sr-only">Buscar cliente</span>
          <IconSearch className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" width={17} height={17} />
          <input
            type="search"
            placeholder="Buscar cliente"
            className="h-10 w-56 rounded-[10px] border border-line bg-slate/60 pr-3 pl-9 text-apoio text-paper placeholder:text-muted focus:border-amber/60 focus:outline-none"
          />
        </label>
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
        <button
          type="button"
          aria-label="Notificações"
          className="relative flex h-10 w-10 items-center justify-center rounded-[10px] border border-line bg-slate/60 text-silver hover:text-paper"
        >
          <IconBell width={18} height={18} />
          <span aria-hidden="true" className="absolute top-2 right-2 h-1.5 w-1.5 rounded-full bg-orange" />
        </button>
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

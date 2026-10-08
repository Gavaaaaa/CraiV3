import { NavLink } from 'react-router-dom'
import { cx } from '../../lib/cx'
import { t } from '../../lib/idioma'
import { IconChat, IconGear, IconGrid, IconKey, IconPlay, IconRefresh, IconUsers } from '../icons/Icons'

interface Item {
  to: string
  rotulo: string
  Icone: typeof IconGrid
  selo?: string
  bloqueado?: boolean
}

/** Do vídeo: barra estreita, só ícones, o ativo "encaixado" com um recorte na borda. */
export function Sidebar({ premium }: { premium: boolean }) {
  const itens: Item[] = [
    { to: '/simulacao', rotulo: t('Simulação do gateway'), Icone: IconPlay, selo: t('Demo') },
    { to: '/', rotulo: t('Visão geral'), Icone: IconGrid },
    { to: '/involuntario', rotulo: t('Churn involuntário'), Icone: IconRefresh },
    { to: '/voluntario', rotulo: t('Churn voluntário'), Icone: IconUsers, bloqueado: !premium },
    { to: '/assistente', rotulo: t('Assistente'), Icone: IconChat },
    // A aba API abre em qualquer plano: no essencial a empresa vê e revoga; só gerar é do premium.
    { to: '/api', rotulo: t('API'), Icone: IconKey },
  ]
  return (
    <nav
      aria-label={t('Seções do painel')}
      className="fixed inset-y-0 left-0 z-30 flex w-[76px] flex-col items-center bg-bar py-5"
    >
      {/* Wordmark tipográfico, como no site: o A em silver cruzado por uma seta laranja */}
      <NavLink to="/" className="mb-6 select-none" aria-label={t('CRAI, visão geral')}>
        <span className="relative inline-flex items-baseline text-[22px] leading-none font-[680] tracking-[-0.04em] text-paper">
          <span>CR</span>
          <span className="relative text-silver">
            A
            <svg
              className="pointer-events-none absolute overflow-visible"
              style={{ left: '-0.1em', top: '0.14em', width: 'calc(100% + 0.2em)', height: '0.73em' }}
              viewBox="0 0 24 24"
              preserveAspectRatio="none"
              fill="none"
              aria-hidden="true"
            >
              <path d="M2 21.5 L10 13.5 L22 3" stroke="var(--color-marca)" strokeWidth={4.5} strokeLinecap="round" strokeLinejoin="round" />
              <path d="M15 3 L22 3 L22 10" stroke="var(--color-marca)" strokeWidth={4.5} strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <span>I</span>
        </span>
      </NavLink>

      <ul className="flex w-full flex-col items-center gap-1.5">
        {itens.map(({ to, rotulo, Icone, selo, bloqueado }) => (
          <li key={to} className="w-full">
            <NavLink
              to={to}
              end={to === '/'}
              title={bloqueado ? t('{rotulo} (plano premium)', { rotulo }) : rotulo}
              aria-label={rotulo}
              aria-disabled={bloqueado || undefined}
              className={({ isActive }) =>
                cx(
                  'group relative mx-auto flex h-12 w-12 items-center justify-center rounded-[14px] transition-colors duration-200',
                  // Com selo, o ícone sobe e o selo fica embaixo dele, dentro do mesmo quadrado.
                  selo && 'flex-col gap-[3px]',
                  isActive ? 'bg-ink text-orange' : 'text-silver hover:bg-paper/[0.06] hover:text-paper',
                  bloqueado && 'opacity-45',
                )
              }
            >
              {({ isActive }) => (
                <>
                  {/* O recorte do vídeo: um trilho laranja que "entra" pela borda do conteúdo */}
                  {isActive ? (
                    <span
                      aria-hidden="true"
                      className="absolute -right-[14px] top-1/2 h-7 w-[3px] -translate-y-1/2 rounded-full bg-orange"
                    />
                  ) : null}
                  <Icone width={selo ? 18 : 21} height={selo ? 18 : 21} />
                  {selo ? (
                    <span aria-hidden="true" className="rounded-[5px] bg-amber px-1 text-rotulo leading-[1.15] font-[700] text-ink">
                      {selo}
                    </span>
                  ) : null}
                  {/* Rótulo flutuante ao passar o mouse */}
                  <span
                    role="tooltip"
                    className="pointer-events-none absolute left-[calc(100%+14px)] z-40 rounded-[8px] max-md:hidden border border-line bg-slate px-2.5 py-1.5 text-rotulo whitespace-nowrap text-paper opacity-0 shadow-lg transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100"
                  >
                    {rotulo}
                    {bloqueado ? <span className="text-silver"> · {t('Plano premium')}</span> : null}
                  </span>
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>

      <div className="mt-auto flex flex-col items-center gap-1.5">
        <NavLink
          to="/configuracao"
          aria-label={t('Configuração')}
          className={({ isActive }) =>
            cx(
              'group relative flex h-12 w-12 items-center justify-center rounded-[14px] transition-colors duration-200',
              isActive ? 'bg-ink text-orange' : 'text-silver hover:bg-paper/[0.06] hover:text-paper',
            )
          }
        >
          <IconGear width={21} height={21} />
        </NavLink>
        {/* O botão "Sair" saiu: não há login de verdade para sair (o de desenvolvimento é automático).
            Volta quando a autenticação existir. */}
      </div>
    </nav>
  )
}

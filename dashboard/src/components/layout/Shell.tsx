import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { MODO_REAL, api, iniciarSessao, trocarPapelDeDesenvolvimento } from '../../data/api'
import type { Empresa, Papel } from '../../data/tipos'
import { t } from '../../lib/idioma'
import { ModoToggle, type Modo } from './ModoToggle'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'

interface Contexto {
  empresa: Empresa | null
  modo: Modo
}
const Ctx = createContext<Contexto>({ empresa: null, modo: 'reais' })
export const usePainel = () => useContext(Ctx)

/**
 * As páginas em que "Mostrar: Dados reais / Simulação" muda o que aparece. Nas outras (API,
 * Configuração, Assistente, Simulação do gateway) a barra não faz efeito e não é mostrada.
 */
export const PAGINAS_COM_MODO = ['/', '/involuntario', '/voluntario']

export function Shell({ children }: { children: ReactNode }) {
  const [empresa, setEmpresa] = useState<Empresa | null>(null)
  const [modo, setModo] = useState<Modo>('reais')
  const { pathname } = useLocation()
  const mostrarModo = PAGINAS_COM_MODO.includes(pathname.replace(/\/+$/, '') || '/')
  useEffect(() => {
    // Sem a empresa (erro de rede), o painel abre mesmo assim; as páginas avisam por conta própria.
    api.empresa().then(setEmpresa).catch(() => setEmpresa(null))
    // Modo real: o login de desenvolvimento é pedido já na abertura. Se o backend estiver
    // fora do ar, as páginas mostram o estado de erro delas; aqui nada quebra.
    if (MODO_REAL) iniciarSessao().catch(() => undefined)
  }, [])
  // Trocar o papel (só em desenvolvimento) pede outro token e recarrega a página aberta.
  const [sessao, setSessao] = useState(0)
  function trocarPapel(papel: Papel) {
    trocarPapelDeDesenvolvimento(papel)
    setEmpresa((e) => (e ? { ...e, papel } : e))
    setSessao((s) => s + 1)
  }
  return (
    <Ctx.Provider value={{ empresa, modo }}>
      <div className="fundo-painel min-h-screen">
        {/* Para quem navega pelo teclado: pula a barra lateral e o cabeçalho */}
        <a
          href="#conteudo"
          className="sr-only z-50 rounded-[8px] bg-paper px-3 py-2 text-apoio font-[600] text-ink focus:not-sr-only focus:fixed focus:top-3 focus:left-[88px]"
        >
          {t('Pular para o conteúdo')}
        </a>
        <Sidebar premium={empresa?.plano === 'premium'} />
        <div className="pl-[76px]">
          <Topbar empresa={empresa} onTrocarPapel={trocarPapel} />
          <main key={sessao} id="conteudo" tabIndex={-1} className="px-4 pt-4 pb-28 outline-none md:px-8">{children}</main>
        </div>
        {mostrarModo ? <ModoToggle modo={modo} onChange={setModo} /> : null}
      </div>
    </Ctx.Provider>
  )
}

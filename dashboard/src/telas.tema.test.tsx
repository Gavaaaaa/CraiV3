// @vitest-environment jsdom
/**
 * Tema e botões, Parte 2 (com o ajuste): o tema se escolhe na seção "Aparência" da
 * Configuração, e só lá; a escolha fica guardada só no navegador; o atributo em <html>; o
 * script do index.html antes da primeira pintura; e o movimento reduzido.
 */
// @ts-expect-error O projeto das telas não carrega os tipos do Node; aqui só se leem dois arquivos.
import { readFileSync } from 'node:fs'
// @ts-expect-error Idem: o caminho dos arquivos a partir deste (no jsdom a URL do módulo não é file:).
import { fileURLToPath } from 'node:url'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { CHAVE_DO_TEMA, TEMA_PADRAO, lerTemaGuardado } from './lib/tema'

const ESPERA = { timeout: 5000 }
let reduzido = false
const aqui = (relativo: string) => fileURLToPath(new URL(relativo, import.meta.url))

beforeEach(() => {
  reduzido = false
  window.matchMedia = ((consulta: string) => ({
    matches: consulta.includes('reduce') ? reduzido : false,
    media: consulta,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Element.prototype.scrollIntoView = () => undefined
  Element.prototype.scrollTo = (() => undefined) as typeof Element.prototype.scrollTo
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('a demonstração não pode chamar a rede')
    }),
  )
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-tema')
  document.documentElement.classList.remove('tema-em-troca')
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function abrir(caminho = '/configuracao?secao=aparencia') {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}
const grupo = () => screen.getByRole('radiogroup', { name: 'Tema do painel' })
const opcao = (nome: 'Escuro' | 'Claro') => within(grupo()).getByRole('radio', { name: new RegExp(`^${nome}`) })
const html = () => document.documentElement

describe('a seção "Aparência" da Configuração', () => {
  it('existe na lista de seções, com o desenho das outras, e começa no escuro', async () => {
    abrir()
    expect(await screen.findByRole('heading', { name: 'Aparência' }, ESPERA)).toBeTruthy()
    expect(screen.getByText('Escolha como o painel aparece neste navegador.')).toBeTruthy()
    expect(within(grupo()).getAllByRole('radio').map((r) => within(r).getByText(/^(Escuro|Claro)$/).textContent)).toEqual(['Escuro', 'Claro'])
    expect(opcao('Escuro').getAttribute('aria-checked')).toBe('true')
    expect(opcao('Claro').getAttribute('aria-checked')).toBe('false')
    expect(html().hasAttribute('data-tema')).toBe(false)
    expect(TEMA_PADRAO).toBe('escuro')
    // A seção diz que a escolha fica só neste navegador, e não tem botão de salvar.
    expect(screen.getByText(/A escolha fica guardada só neste navegador/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Salvar' })).toBeNull()
    // A navegação da Configuração lista a seção.
    expect(screen.getByRole('button', { name: /Aparência/ }).getAttribute('aria-current')).toBe('page')
  })

  it('o topo não tem mais botão de tema', async () => {
    abrir('/api')
    await screen.findByRole('button', { name: /esperando a sua escolha de mensagem$/ }, ESPERA)
    expect(screen.queryByRole('button', { name: /Mudar para o tema/ })).toBeNull()
    expect(document.querySelector('[data-tema-atual]')).toBeNull()
  })

  it('escolher "Claro" vale na hora: o atributo em <html>, a preferência só no navegador, e "Escuro" volta', async () => {
    abrir()
    await screen.findByRole('radiogroup', { name: 'Tema do painel' }, ESPERA)
    fireEvent.click(opcao('Claro'))
    await waitFor(() => expect(html().getAttribute('data-tema')).toBe('claro'), ESPERA)
    expect(opcao('Claro').getAttribute('aria-checked')).toBe('true')
    expect(opcao('Escuro').getAttribute('aria-checked')).toBe('false')
    expect(window.localStorage.getItem(CHAVE_DO_TEMA)).toBe('claro')
    // Só a preferência: nenhuma outra chave, nenhum identificador, nenhuma chamada de rede.
    expect(window.localStorage.length).toBe(1)
    expect(window.sessionStorage.length).toBe(0)
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()

    fireEvent.click(opcao('Escuro'))
    await waitFor(() => expect(html().hasAttribute('data-tema')).toBe(false), ESPERA)
    expect(window.localStorage.getItem(CHAVE_DO_TEMA)).toBe('escuro')
    expect(opcao('Escuro').getAttribute('aria-checked')).toBe('true')
  })

  it('funciona pelo teclado: só a opção marcada entra no Tab, e as setas trocam e movem o foco', async () => {
    abrir()
    await screen.findByRole('radiogroup', { name: 'Tema do painel' }, ESPERA)
    expect(opcao('Escuro').getAttribute('tabindex')).toBe('0')
    expect(opcao('Claro').getAttribute('tabindex')).toBe('-1')
    opcao('Escuro').focus()
    fireEvent.keyDown(grupo(), { key: 'ArrowRight' })
    await waitFor(() => expect(html().getAttribute('data-tema')).toBe('claro'), ESPERA)
    expect(document.activeElement).toBe(opcao('Claro'))
    expect(opcao('Claro').getAttribute('tabindex')).toBe('0')
    fireEvent.keyDown(grupo(), { key: 'ArrowLeft' })
    await waitFor(() => expect(html().hasAttribute('data-tema')).toBe(false), ESPERA)
    expect(document.activeElement).toBe(opcao('Escuro'))
  })

  it('qualquer papel troca o tema: nada na seção está desabilitado nem pede dono ou administrador', async () => {
    abrir()
    await screen.findByRole('radiogroup', { name: 'Tema do painel' }, ESPERA)
    for (const r of within(grupo()).getAllByRole('radio')) expect((r as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByText(/Você tem o papel de membro/)).toBeNull()
  })

  it('a escolha guardada vale na abertura seguinte', async () => {
    window.localStorage.setItem(CHAVE_DO_TEMA, 'claro')
    expect(lerTemaGuardado()).toBe('claro')
    abrir()
    await screen.findByRole('radiogroup', { name: 'Tema do painel' }, ESPERA)
    await waitFor(() => expect(opcao('Claro').getAttribute('aria-checked')).toBe('true'), ESPERA)
    expect(html().getAttribute('data-tema')).toBe('claro')
  })

  it('valor estranho guardado vale como escuro', () => {
    window.localStorage.setItem(CHAVE_DO_TEMA, 'roxo')
    expect(lerTemaGuardado()).toBe('escuro')
  })

  it('sem localStorage, o tema funciona na sessão, sem erro', async () => {
    const quebrado = {
      getItem: () => {
        throw new Error('bloqueado')
      },
      setItem: () => {
        throw new Error('bloqueado')
      },
      removeItem: () => undefined,
      clear: () => undefined,
      key: () => null,
      length: 0,
    }
    const original = Object.getOwnPropertyDescriptor(window, 'localStorage')!
    Object.defineProperty(window, 'localStorage', { configurable: true, value: quebrado })
    try {
      expect(lerTemaGuardado()).toBe('escuro')
      abrir()
      await screen.findByRole('radiogroup', { name: 'Tema do painel' }, ESPERA)
      fireEvent.click(opcao('Claro'))
      await waitFor(() => expect(html().getAttribute('data-tema')).toBe('claro'), ESPERA)
      expect(opcao('Claro').getAttribute('aria-checked')).toBe('true')
    } finally {
      Object.defineProperty(window, 'localStorage', original)
    }
  })

  it('a troca anima as cores por um instante, mas não com movimento reduzido', async () => {
    abrir()
    await screen.findByRole('radiogroup', { name: 'Tema do painel' }, ESPERA)
    fireEvent.click(opcao('Claro'))
    expect(html().classList.contains('tema-em-troca')).toBe(true)
    await waitFor(() => expect(html().classList.contains('tema-em-troca')).toBe(false), ESPERA)
    cleanup()

    reduzido = true
    abrir()
    await screen.findByRole('radiogroup', { name: 'Tema do painel' }, ESPERA)
    fireEvent.click(opcao('Escuro'))
    expect(html().classList.contains('tema-em-troca')).toBe(false)
    await waitFor(() => expect(html().hasAttribute('data-tema')).toBe(false), ESPERA)
  })
})

describe('o index.html', () => {
  const indexHtml: string = readFileSync(aqui('../index.html'), 'utf-8')

  it('aplica o tema guardado antes da primeira pintura, com try/catch, e antes do módulo do painel', () => {
    const script = indexHtml.indexOf('<script>')
    const modulo = indexHtml.indexOf('<script type="module"')
    expect(script).toBeGreaterThan(0)
    expect(script).toBeLessThan(modulo)
    const trecho = indexHtml.slice(script, indexHtml.indexOf('</script>', script))
    expect(trecho).toContain("localStorage.getItem('crai_tema') === 'claro'")
    expect(trecho).toContain("setAttribute('data-tema', 'claro')")
    expect(trecho).toMatch(/try \{[\s\S]*\} catch/)
  })
})

describe('o index.css', () => {
  const css: string = readFileSync(aqui('./index.css'), 'utf-8')

  it('o color-scheme acompanha o tema, e a transição da troca respeita o movimento reduzido', () => {
    expect(css).toMatch(/^:root \{\s*color-scheme: dark;/m)
    expect(css).toMatch(/:root\[data-tema='claro'\] \{\s*color-scheme: light;/)
    expect(css).toMatch(/html\.tema-em-troca[\s\S]*transition:/)
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{[\s\S]*html\.tema-em-troca[\s\S]*transition: none;/)
  })
})

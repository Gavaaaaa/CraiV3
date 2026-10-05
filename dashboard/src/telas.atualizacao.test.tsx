// @vitest-environment jsdom
/**
 * Rodada 3, Fase 7: a tela se atualiza sozinha (D8), sem piscar e sem perder o que a pessoa
 * está digitando. Aqui as telas de verdade, em MODO REAL, com o backend de mentira.
 *
 * Os intervalos de verdade (60 s e 5 s) estão conferidos em `lib/useAtualizarACada.test.tsx`.
 * Neste arquivo eles são encurtados, para a tela se atualizar durante o teste sem relógio falso.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
  vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', '')
})

vi.mock('./lib/useAtualizarACada', async (original) => ({
  ...(await original<typeof import('./lib/useAtualizarACada')>()),
  ATUALIZAR_PAGINA_MS: 120,
  ATUALIZAR_CICLO_ABERTO_MS: 60,
}))

import App from './App'
import { MODO_REAL } from './data/api'
import { DETALHE_DO_CICLO, LINHA_DO_CICLO, cicloRecuperado } from './testes/cicloDeExemplo'
import { ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'

const ESPERA = { timeout: 5000 }
const lista = (ciclos: unknown[]) => ({ ciclos, proximo_cursor: null, tem_mais: false })
const ANTES = { 'GET /ciclos': lista([LINHA_DO_CICLO]), 'GET /ciclos/2': DETALHE_DO_CICLO }
const depois = () => {
  const { linha, detalhe } = cicloRecuperado()
  return { 'GET /ciclos': lista([linha]), 'GET /ciclos/2': detalhe }
}

beforeEach(() => prepararJsdom())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}

/** Reprova se, daqui em diante, a tela mostrar o esqueleto de carga ou o "Carregando…". */
function vigiarPiscadas(): () => void {
  let piscou = ''
  const vigia = new MutationObserver(() => {
    if (document.querySelector('.animate-pulse')) piscou = 'o esqueleto de carga voltou'
    if (screen.queryByText('Carregando…')) piscou = 'o "Carregando…" voltou'
  })
  vigia.observe(document.body, { childList: true, subtree: true })
  return () => {
    vigia.disconnect()
    expect(piscou).toBe('')
  }
}

describe('a tela se atualiza sozinha em modo real', () => {
  it('o modo real está ligado neste arquivo', () => {
    expect(MODO_REAL).toBe(true)
  })

  it('a lista do involuntário mostra o que mudou no backend, sem a pessoa recarregar e sem piscar', async () => {
    ligarBackendFalso(ANTES)
    abrir('/involuntario')
    const tabela = await screen.findByRole('table')
    expect(await within(tabela).findByText('Clínica Horizonte (fictícia)', {}, ESPERA)).toBeTruthy()
    expect(within(tabela).getByText('Em processo')).toBeTruthy()
    const conferir = vigiarPiscadas()

    const chamadas = ligarBackendFalso(depois())
    expect(await within(tabela).findByText('Recuperado', {}, ESPERA)).toBeTruthy()
    expect(within(tabela).queryByText('Em processo')).toBeNull()
    expect(within(tabela).getByText(/R\$\s4\.165,00 para você/)).toBeTruthy()
    // A consulta periódica refaz a lista e os números do topo.
    expect(chamadas.some((c) => c.startsWith('GET /ciclos'))).toBe(true)
    expect(chamadas.some((c) => c.startsWith('GET /metrics/involuntario/mes'))).toBe(true)
    conferir()
  })

  it('a busca que a pessoa digitou continua no campo, e a consulta periódica a respeita', async () => {
    const outra = { ...LINHA_DO_CICLO, id: 9, id_recorrencia: 'RN_demo_outra', cliente_nome: 'Padaria Modelo (fictícia)' }
    ligarBackendFalso({ ...ANTES, 'GET /ciclos': lista([LINHA_DO_CICLO, outra]) })
    abrir('/involuntario')
    const tabela = await screen.findByRole('table')
    await within(tabela).findByText('Padaria Modelo (fictícia)', {}, ESPERA)
    const campo = screen.getByLabelText('Buscar na lista') as HTMLInputElement
    fireEvent.change(campo, { target: { value: 'Clínica' } })
    await waitFor(() => expect(within(tabela).queryByText('Padaria Modelo (fictícia)')).toBeNull(), ESPERA)
    expect(within(tabela).getByText('Clínica Horizonte (fictícia)')).toBeTruthy()

    // O backend muda: o ciclo da Clínica foi recuperado. A outra continua lá, fora da busca.
    const chamadas = ligarBackendFalso({ ...depois(), 'GET /ciclos': lista([cicloRecuperado().linha, outra]) })
    expect(await within(tabela).findByText('Recuperado', {}, ESPERA)).toBeTruthy()
    expect(campo.value).toBe('Clínica')
    expect(document.body.contains(campo)).toBe(true)
    expect(within(tabela).queryByText('Padaria Modelo (fictícia)')).toBeNull()
    expect(chamadas.some((c) => c.startsWith('GET /ciclos?'))).toBe(true)
  })

  it('o painel de um ciclo aberto acompanha o ciclo, sem fechar e sem voltar ao "Carregando…"', async () => {
    ligarBackendFalso(ANTES)
    abrir('/involuntario')
    fireEvent.click(await screen.findByText('Clínica Horizonte (fictícia)', {}, ESPERA))
    const painel = await screen.findByRole('dialog')
    expect(await within(painel).findAllByText('Enviar esta', {}, ESPERA)).toHaveLength(3)
    expect(within(painel).queryByText('Líquido para você')).toBeNull()
    const conferir = vigiarPiscadas()

    const chamadas = ligarBackendFalso(depois())
    expect(await within(painel).findByText('Líquido para você', {}, ESPERA)).toBeTruthy()
    expect(within(painel).getByText(/R\$\s4\.165,00/)).toBeTruthy()
    expect(within(painel).queryByText('Enviar esta')).toBeNull()
    expect(document.body.contains(painel)).toBe(true)
    expect(chamadas.some((c) => c === 'GET /ciclos/2')).toBe(true)
    conferir()
  })

  it('se a consulta do painel falhar, o painel continua com o que já mostrava', async () => {
    ligarBackendFalso(ANTES)
    abrir('/involuntario')
    fireEvent.click(await screen.findByText('Clínica Horizonte (fictícia)', {}, ESPERA))
    const painel = await screen.findByRole('dialog')
    await within(painel).findAllByText('Enviar esta', {}, ESPERA)

    const chamadas = ligarBackendFalso({ ...ANTES, 'GET /ciclos/2': { __status: 500, corpo: {} } })
    await waitFor(() => expect(chamadas.filter((c) => c === 'GET /ciclos/2').length).toBeGreaterThanOrEqual(2), ESPERA)
    expect(within(painel).getAllByText('Enviar esta')).toHaveLength(3)
    expect(within(painel).queryByRole('alert')).toBeNull()
    expect(within(painel).queryByText('Tentar de novo')).toBeNull()
  })

  it('a visão geral e o voluntário também consultam de novo', async () => {
    let chamadas = ligarBackendFalso()
    abrir('/')
    await waitFor(() => expect(chamadas.filter((c) => c.startsWith('GET /metrics/visao-geral')).length).toBeGreaterThanOrEqual(2), ESPERA)
    cleanup()

    chamadas = ligarBackendFalso()
    abrir('/voluntario')
    await waitFor(() => expect(chamadas.filter((c) => c.startsWith('GET /metrics/voluntario/mes')).length).toBeGreaterThanOrEqual(2), ESPERA)
  })
})

describe('involuntário em janela estreita: a tabela rola dentro do cartão, não a página', () => {
  // A medida de verdade é no navegador (`docs/interno/prints_rodada3.mjs`: 197 px de rolagem
  // antes, 0 depois). Aqui fica a causa: um rótulo invisível (sr-only, posição absoluta) dentro
  // da área rolável só é cortado por ela se ela for o bloco de referência dele.
  it('todo rótulo invisível da tabela fica preso à área rolável', async () => {
    ligarBackendFalso(ANTES)
    abrir('/involuntario')
    const tabela = await screen.findByRole('table')
    await within(tabela).findByText('Clínica Horizonte (fictícia)', {}, ESPERA)
    const area = tabela.closest('.overflow-x-auto') as HTMLElement
    expect(area).toBeTruthy()
    const invisiveis = [...area.querySelectorAll('.sr-only')]
    expect(invisiveis.length).toBeGreaterThan(0)
    for (const rotulo of invisiveis) {
      const referencia = rotulo.parentElement?.closest('.relative, .absolute, .fixed, .sticky')
      expect(referencia && area.contains(referencia)).toBe(true)
    }
  })
})

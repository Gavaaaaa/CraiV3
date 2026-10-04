// @vitest-environment jsdom
/**
 * Rodada 2, ajustes: a barra "Mostrar: Dados reais / Simulação" só aparece onde faz efeito,
 * e o painel do ciclo mostra um número só, com a linha do desconto por anomalia.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import App from './App'
import { PAGINAS_COM_MODO } from './components/layout/Shell'
import { api } from './data/api'
import { prepararJsdom } from './testes/modoRealFalso'

beforeAll(() => {
  prepararJsdom()
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('a demonstração não pode chamar a rede')
    }),
  )
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}

const ESPERA = { timeout: 5000 }
const barra = () => screen.queryByRole('radiogroup', { name: 'Modo dos dados' })

describe('a barra "Mostrar: Dados reais / Simulação"', () => {
  it('a lista das páginas em que ela faz efeito', () => {
    expect(PAGINAS_COM_MODO).toEqual(['/', '/involuntario', '/voluntario'])
  })

  it.each([
    ['/', 'Mantido para você nos últimos 30 dias'],
    ['/?aba=extrato', 'Mantido para você nos últimos 30 dias'],
    ['/involuntario', 'Recuperado para você em setembro'],
    ['/voluntario', 'Mantido para você em setembro'],
  ])('aparece em %s', async (caminho, ancora) => {
    abrir(caminho)
    expect(await screen.findByText(ancora, {}, ESPERA)).toBeTruthy()
    expect(barra()).toBeTruthy()
    expect(within(barra()!).getAllByRole('radio').map((r) => r.textContent)).toEqual(['Dados reais', 'Simulação'])
  })

  it.each([
    ['/api', /^Exemplo de uso/],
    ['/configuracao', 'Quem escolhe a mensagem'],
    ['/configuracao?secao=equipe', 'Membros'],
    ['/assistente', /O que o assistente vê/],
    ['/simulacao', /^O gateway de pagamento real será integrado em breve/],
  ])('não aparece em %s', async (caminho, ancora) => {
    abrir(caminho)
    expect((await screen.findAllByText(ancora, {}, ESPERA)).length).toBeGreaterThan(0)
    expect(barra()).toBeNull()
  })

  it('some e volta ao navegar, e continua funcionando onde aparece', async () => {
    abrir('/involuntario')
    expect(await screen.findByText('Recuperado para você em setembro', {}, ESPERA)).toBeTruthy()
    fireEvent.click(within(barra()!).getByRole('radio', { name: 'Simulação' }))
    expect(within(barra()!).getByRole('radio', { name: 'Simulação' }).getAttribute('aria-checked')).toBe('true')

    fireEvent.click(screen.getByRole('link', { name: 'API' }))
    expect(await screen.findByRole('button', { name: /^Exemplo de uso/ }, ESPERA)).toBeTruthy()
    expect(barra()).toBeNull()

    fireEvent.click(screen.getByRole('link', { name: 'Churn involuntário' }))
    await waitFor(() => expect(barra()).toBeTruthy(), ESPERA)
  }, 20_000)
})

describe('o painel do ciclo mostra um número só', () => {
  it('sem desconto: o número e a legenda de sempre, sem a linha do desconto', async () => {
    abrir('/involuntario')
    fireEvent.click(await screen.findByText('Clínica Horizonte', {}, ESPERA))
    const painel = await screen.findByRole('dialog')
    expect(await within(painel).findByText('72%', {}, ESPERA)).toBeTruthy()
    expect(within(painel).getByText('Chance de recuperar, estimada na abertura do ciclo')).toBeTruthy()
    expect(within(painel).queryByText(/desconto de \d+% por comportamento fora do padrão/)).toBeNull()
  })

  it('com desconto: o número que o sistema usou, e a linha dizendo que ele já tem o desconto', async () => {
    const original = await api.ciclo(38)
    vi.spyOn(api, 'ciclo').mockResolvedValue({
      ...original!,
      chance_recuperar: 0.1677,
      desconto_anomalia_pct: 30,
      linha_do_tempo: [
        { em: original!.aberto_em, tipo: 'diagnostico', titulo: 'Diagnóstico: 17% de chance de recuperar' },
        {
          em: original!.aberto_em,
          tipo: 'diagnostico',
          titulo: 'Decisão registrada: avaliação da cobrança',
          detalhe: 'Avaliação inicial da cobrança. O número do topo é o que o sistema usou e já tem o desconto de 30% por comportamento fora do padrão.',
        },
      ],
    })
    abrir('/involuntario')
    fireEvent.click(await screen.findByText('Clínica Horizonte', {}, ESPERA))
    const painel = await screen.findByRole('dialog')
    expect(await within(painel).findByText('17%', {}, ESPERA)).toBeTruthy()
    expect(within(painel).getByText('Já com o desconto de 30% por comportamento fora do padrão.')).toBeTruthy()
    // Um número só: o de antes do desconto (24%) não aparece em lugar nenhum do painel.
    expect(painel.textContent).not.toMatch(/24\s?%|24\/100/)
    expect(painel.textContent?.match(/17%/g)).toHaveLength(2) // o do topo e o da linha do tempo
  })
})

// @vitest-environment jsdom
/**
 * As telas de pé, em modo de DEMONSTRAÇÃO (sem `VITE_CRAI_API_URL`): cada página abre sem
 * erro, mostra o que mostrava antes, e a etiqueta "Demonstração" dos blocos ainda não
 * integrados NÃO aparece (sem o modo real, nada muda na tela).
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import App from './App'

beforeAll(() => {
  // O jsdom não tem `matchMedia` (usado para "movimento reduzido").
  window.matchMedia = ((consulta: string) => ({
    matches: false,
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
  // Qualquer chamada de rede em demonstração reprova o teste.
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('a demonstração não pode chamar a rede')
    }),
  )
})

afterEach(() => cleanup())

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}

/** As etiquetas novas têm exatamente este texto (os avisos antigos são frases maiores). */
const etiquetas = () => screen.queryAllByText('Demonstração', { exact: true })

describe('demonstração: as páginas abrem como antes', () => {
  it('visão geral', async () => {
    abrir('/')
    expect(await screen.findByText('Mantido para você nos últimos 30 dias')).toBeTruthy()
    expect(await screen.findByText('Olá, NimbusFlow Tecnologia')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('Taxa de recuperação')).toBeTruthy())
    expect(etiquetas()).toHaveLength(0)
  })

  it('involuntário: a lista, e o painel do ciclo com o prazo e as contribuições em texto', async () => {
    abrir('/involuntario')
    expect(await screen.findByText('Recuperado para você em setembro')).toBeTruthy()
    const linha = await screen.findByText('Clínica Horizonte')
    expect(screen.getAllByText('Cliente sem cadastro').length).toBeGreaterThan(0)
    expect(etiquetas()).toHaveLength(0)

    fireEvent.click(linha)
    const painel = await screen.findByRole('dialog')
    expect(await within(painel).findByText('Por que o sistema agiu assim')).toBeTruthy()
    expect(within(painel).getByText('72%')).toBeTruthy()
    expect(within(painel).getAllByText('Aumentou a chance de recuperar').length).toBe(3)
    expect(within(painel).getAllByText('Reduziu a chance de recuperar').length).toBe(2)
    expect(within(painel).queryByText(/pts$/)).toBeNull()
    expect(await within(painel).findByText('Envio automático em 6 h 40 min')).toBeTruthy()
    expect(within(painel).getAllByText('Enviar esta')).toHaveLength(3)
    expect(within(painel).getByText('Quero outro tipo de mensagem')).toBeTruthy()
  })

  it('involuntário: "Enviar esta" escolhe a mensagem e o painel recarrega', async () => {
    abrir('/involuntario')
    fireEvent.click(await screen.findByText('Clínica Horizonte'))
    const painel = await screen.findByRole('dialog')
    const botoes = await within(painel).findAllByText('Enviar esta')
    fireEvent.click(botoes[1])
    expect(await within(painel).findByText('Escolhida', {}, { timeout: 5000 })).toBeTruthy()
    expect(within(painel).queryByText('Enviar esta')).toBeNull()
    expect(within(painel).getByText('Mensagem enviada')).toBeTruthy()
  })

  it('configuração: a seção de mensagens, sem etiqueta', async () => {
    abrir('/configuracao')
    expect(await screen.findByText('Quem escolhe a mensagem', {}, { timeout: 5000 })).toBeTruthy()
    expect(screen.getByText('Horário permitido para contato')).toBeTruthy()
    // Na demonstração o SMS continua na lista de canais, como antes.
    expect(screen.getAllByText('SMS').length).toBeGreaterThan(0)
    expect(etiquetas()).toHaveLength(0)
  })

  it('voluntário e assistente', async () => {
    abrir('/voluntario')
    expect(await screen.findByText('Mantido para você em setembro', {}, { timeout: 5000 })).toBeTruthy()
    expect(etiquetas()).toHaveLength(0)
    cleanup()
    abrir('/assistente')
    expect(await screen.findByText('O que o assistente vê', { exact: false }, { timeout: 5000 })).toBeTruthy()
    expect(etiquetas()).toHaveLength(0)
  })

  it('simulação continua com o selo permanente dela', async () => {
    abrir('/simulacao')
    expect(await screen.findByText('Simulação do gateway', {}, { timeout: 5000 })).toBeTruthy()
    await waitFor(() => expect(etiquetas().length).toBeGreaterThan(0), { timeout: 5000 })
  })
})

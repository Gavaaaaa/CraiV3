// @vitest-environment jsdom
/**
 * Rodada 2, Fase 3: COM `VITE_CRAI_MOSTRAR_DEMONSTRACAO=1`, as etiquetas "Demonstração"
 * voltam como eram na Etapa 4A: nos blocos que ainda usam dado fictício com o modo real
 * ligado. As contagens são as que o teste ao vivo da Etapa 4A conferia.
 *
 * O modo real é ligado aqui com um backend de mentira (`testes/modoRealFalso.ts`).
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
  vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', '1')
})

import App from './App'
import { MODO_REAL, MOSTRAR_DEMONSTRACAO, etiquetaDeDemonstracao } from './data/api'
import { etiquetas, ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'

const ESPERA = { timeout: 5000 }

beforeAll(() => {
  prepararJsdom()
  ligarBackendFalso()
})
afterEach(() => cleanup())

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}

describe('com a variável, e o modo real ligado', () => {
  it('a variável liga as etiquetas só dos blocos fictícios', () => {
    expect(MODO_REAL).toBe(true)
    expect(MOSTRAR_DEMONSTRACAO).toBe(true)
    expect(etiquetaDeDemonstracao('resumoVisaoGeral')).toBe(true)
    expect(etiquetaDeDemonstracao('ciclos', 'configuracao', 'chaves')).toBe(false)
  })

  it('visão geral: os cartões do topo e a aba aberta', async () => {
    abrir('/')
    expect(await screen.findByText('Mantido para você nos últimos 30 dias', {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(etiquetas().length).toBeGreaterThanOrEqual(7), ESPERA)
  })

  it('saúde do sistema: o relógio é de verdade, e as outras três linhas ficam marcadas', async () => {
    abrir('/?aba=saude')
    expect(await screen.findByText('Relógio das tentativas', {}, ESPERA)).toBeTruthy()
    const item = (rotulo: string) => screen.getByText(rotulo).closest('li') as HTMLElement
    expect(within(item('Relógio das tentativas')).queryByText('Demonstração')).toBeNull()
    for (const rotulo of ['Decisões automáticas', 'Redator de mensagens', 'Base de clientes']) {
      expect(within(item(rotulo)).getByText('Demonstração'), rotulo).toBeTruthy()
    }
  })

  it('voluntário e assistente', async () => {
    abrir('/voluntario')
    expect(await screen.findByText('Mantido para você em setembro', {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(etiquetas().length).toBeGreaterThanOrEqual(6), ESPERA)
    cleanup()
    abrir('/assistente')
    await waitFor(() => expect(etiquetas().length).toBeGreaterThanOrEqual(1), ESPERA)
  })

  it('configuração: mensagens é de verdade; as outras cinco seções ficam marcadas', async () => {
    abrir('/configuracao')
    expect(await screen.findByText('Quem escolhe a mensagem', {}, ESPERA)).toBeTruthy()
    expect(etiquetas()).toHaveLength(5)
    cleanup()
    // Numa seção fictícia aberta, a faixa do bloco soma mais uma.
    abrir('/configuracao?secao=equipe')
    expect(await screen.findByText('Membros', {}, ESPERA)).toBeTruthy()
    expect(etiquetas()).toHaveLength(6)
    expect(screen.getByText('Este bloco ainda usa dados fictícios')).toBeTruthy()
  })

  it('o que já é de verdade continua sem etiqueta: involuntário e a aba API', async () => {
    abrir('/involuntario')
    expect((await screen.findAllByText(/^Recuperado para você em /, {}, ESPERA)).length).toBeGreaterThan(0)
    expect(etiquetas()).toHaveLength(0)
    cleanup()
    abrir('/api')
    expect(await screen.findByText('Você ainda não tem uma chave', {}, ESPERA)).toBeTruthy()
    expect(etiquetas()).toHaveLength(0)
  })

  it('a Simulação do gateway é a mesma, com ou sem a variável', async () => {
    abrir('/simulacao')
    expect(await screen.findByText('Simulação do gateway', {}, ESPERA)).toBeTruthy()
    const titulo = await screen.findByRole('heading', { name: 'Simulação do gateway' }, ESPERA)
    expect(titulo.parentElement?.textContent).toBe('Simulação do gatewayDemonstração')
    expect(screen.getByRole('link', { name: 'Simulação do gateway' }).textContent).toContain('Demo')
  })
})

// @vitest-environment jsdom
/**
 * Rodada 2, Fase 3: SEM `VITE_CRAI_MOSTRAR_DEMONSTRACAO`, nenhuma etiqueta "Demonstração"
 * aparece fora da Simulação do gateway, mesmo com o modo real ligado (que é quando elas
 * apareciam). O teste percorre todas as páginas, abas e seções.
 *
 * O modo real é ligado aqui com um backend de mentira (`testes/modoRealFalso.ts`).
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
  vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', '')
})

import App from './App'
import { MODO_REAL, MOSTRAR_DEMONSTRACAO, emDemonstracao, etiquetaDeDemonstracao } from './data/api'
import { PAGINAS, etiquetas, ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'

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

describe('sem a variável, com o modo real ligado', () => {
  it('o modo real está ligado, a variável não, e o registro do que é fictício continua existindo', () => {
    expect(MODO_REAL).toBe(true)
    expect(MOSTRAR_DEMONSTRACAO).toBe(false)
    // O mecanismo não foi apagado: o painel ainda sabe o que é fictício (a seção Equipe da
    // configuração depende do site e continua de demonstração)...
    expect(emDemonstracao('membros')).toBe(true)
    expect(emDemonstracao('ciclos', 'chaves')).toBe(false)
    // ...só não mostra a etiqueta.
    expect(etiquetaDeDemonstracao('membros')).toBe(false)
    // Rodada 3, Fase 2: a visão geral deixou de ser fictícia.
    expect(emDemonstracao('resumoVisaoGeral', 'serieDupla', 'funil', 'oQueFunciona', 'atividade', 'extrato', 'saude')).toBe(false)
  })

  it.each(PAGINAS)('nenhuma etiqueta "Demonstração" em $caminho', async ({ caminho, ancora }) => {
    abrir(caminho)
    expect((await screen.findAllByText(ancora, {}, ESPERA)).length).toBeGreaterThan(0)
    // A empresa do topo chegou: a página já passou da primeira pintura.
    expect(await screen.findByText('Olá, Empresa de demonstração', {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), ESPERA)
    expect(etiquetas()).toHaveLength(0)
  })

  it('saúde do sistema: nenhuma linha marcada', async () => {
    abrir('/?aba=saude')
    expect(await screen.findByText('Relógio das tentativas', {}, ESPERA)).toBeTruthy()
    for (const rotulo of ['Decisões automáticas', 'Redator de mensagens', 'Base de clientes']) {
      expect(screen.getByText(rotulo).closest('li')?.textContent).not.toContain('Demonstração')
    }
  })

  it('a Simulação do gateway não mudou: o selo "Demo" no menu e o selo permanente da página', async () => {
    abrir('/simulacao')
    expect(await screen.findByText('Simulação do gateway', {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(etiquetas().length).toBeGreaterThan(0), ESPERA)
    // O selo permanente fica ao lado do título da página.
    const titulo = await screen.findByRole('heading', { name: 'Simulação do gateway' }, ESPERA)
    expect(titulo.parentElement?.textContent).toBe('Simulação do gatewayDemonstração')
    // E o item do menu continua com o selo "Demo".
    expect(screen.getByRole('link', { name: 'Simulação do gateway' }).textContent).toContain('Demo')
  })
})

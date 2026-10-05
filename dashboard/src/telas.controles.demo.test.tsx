// @vitest-environment jsdom
/**
 * Tema e botões, Parte 1, em modo de DEMONSTRAÇÃO (sem backend): o selo do papel é só
 * informação, em português; o "Sair" sem login saiu; a planilha modelo baixa de verdade.
 */
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import App from './App'
import { MODO_REAL } from './data/api'
import { COLUNAS_DA_PLANILHA, EXEMPLO_DA_PLANILHA, NOME_DA_PLANILHA } from './routes/voluntario/Blocos'
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
afterEach(() => cleanup())

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}
const ESPERA = { timeout: 5000 }

describe('o selo do papel, fora do login de desenvolvimento', () => {
  it('o modo real está desligado neste arquivo', () => {
    expect(MODO_REAL).toBe(false)
  })

  it('é só informação, em português: nem botão, nem seletor, nem "Owner"', async () => {
    abrir('/api')
    const selo = await screen.findByText('Dono', {}, ESPERA)
    expect(selo.getAttribute('data-papel')).toBe('owner')
    expect(selo.tagName).toBe('DIV')
    expect(selo.closest('button, a, select, label')).toBeNull()
    expect(selo.className).not.toMatch(/hover:|cursor-pointer|border/)
    expect(screen.queryByText('Owner')).toBeNull()
    expect(screen.queryByLabelText('Papel do login de desenvolvimento')).toBeNull()
  })
})

describe('a barra lateral', () => {
  it('não tem mais o botão "Sair" (não há login de que sair)', async () => {
    abrir('/api')
    await screen.findByText('Dono', {}, ESPERA)
    expect(screen.queryByRole('button', { name: 'Sair' })).toBeNull()
  })
})

describe('a planilha modelo da base de clientes', () => {
  it('as colunas são as que a importação aceita: as três obrigatórias primeiro, 18 ao todo', () => {
    expect(COLUNAS_DA_PLANILHA.slice(0, 3)).toEqual(['customer_id_externo', 'mrr', 'billing_profile'])
    expect(COLUNAS_DA_PLANILHA).toHaveLength(18)
    expect(new Set(COLUNAS_DA_PLANILHA).size).toBe(18)
    expect(EXEMPLO_DA_PLANILHA).toHaveLength(COLUNAS_DA_PLANILHA.length)
    // O perfil de cobrança é um dos três que o backend conhece; os números vêm em pt-BR.
    expect(['CLT', 'PJ', 'freelancer']).toContain(EXEMPLO_DA_PLANILHA[2])
    expect(EXEMPLO_DA_PLANILHA[1]).toMatch(/^\d+,\d{2}$/)
  })

  it('"Baixar planilha modelo" é um botão que baixa o CSV com o cabeçalho e a linha de exemplo', async () => {
    const blobs: Blob[] = []
    const nomes: string[] = []
    URL.createObjectURL = vi.fn((b: Blob) => (blobs.push(b), 'blob:teste')) as unknown as typeof URL.createObjectURL
    URL.revokeObjectURL = vi.fn()
    const clique = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      nomes.push(this.download)
    })
    abrir('/voluntario')
    const botao = await screen.findByRole('button', { name: /Baixar planilha modelo/ }, ESPERA)
    expect(botao.textContent).not.toContain('em breve')
    expect(document.querySelector('a[href="#"]')).toBeNull()
    fireEvent.click(botao)
    await waitFor(() => expect(blobs).toHaveLength(1), ESPERA)
    expect(nomes).toEqual([NOME_DA_PLANILHA])
    const texto = await blobs[0].text()
    const linhas = texto.replace(/^﻿/, '').split('\r\n')
    expect(linhas[0]).toBe(COLUNAS_DA_PLANILHA.join(';'))
    expect(linhas[1]).toBe(EXEMPLO_DA_PLANILHA.join(';'))
    expect(new Uint8Array(await blobs[0].arrayBuffer()).slice(0, 3)).toEqual(new Uint8Array([0xef, 0xbb, 0xbf]))
    clique.mockRestore()
  })
})

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
    // A seção Equipe da configuração depende do site e continua fictícia.
    expect(etiquetaDeDemonstracao('membros')).toBe(true)
    expect(etiquetaDeDemonstracao('ciclos', 'configuracao', 'chaves')).toBe(false)
  })

  it('visão geral: já é de verdade, e nenhum cartão nem aba ganha etiqueta', async () => {
    // Rodada 3, Fase 2: a visão geral passou a ler o backend.
    expect(etiquetaDeDemonstracao('resumoVisaoGeral', 'serieDupla', 'funil', 'oQueFunciona', 'atividade', 'extrato')).toBe(false)
    for (const caminho of ['/', '/?aba=caminho', '/?aba=funciona', '/?aba=extrato', '/?aba=atividade']) {
      abrir(caminho)
      expect(await screen.findByText('Mantido para você nos últimos 30 dias', {}, ESPERA)).toBeTruthy()
      await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), ESPERA)
      expect(etiquetas(), caminho).toHaveLength(0)
      cleanup()
    }
  })

  it('saúde do sistema: as quatro linhas são de verdade, e nenhuma fica marcada', async () => {
    abrir('/?aba=saude')
    expect(await screen.findByText('Relógio das tentativas', {}, ESPERA)).toBeTruthy()
    const item = (rotulo: string) => screen.getByText(rotulo).closest('li') as HTMLElement
    for (const rotulo of ['Relógio das tentativas', 'Decisões automáticas', 'Redator de mensagens', 'Base de clientes']) {
      expect(within(item(rotulo)).queryByText('Demonstração'), rotulo).toBeNull()
    }
    // O que a tela diz é o que o backend de mentira respondeu.
    expect(item('Decisões automáticas').textContent).toContain('4 de 4 modelos carregados.')
    expect(item('Base de clientes').textContent).toContain('Nenhuma base enviada ainda.')
  })

  it('o voluntário e o assistente já são de verdade: nenhuma etiqueta', async () => {
    // Rodada 3, Fase 1: a página do voluntário passou a ler o backend. Mesmo com a variável
    // ligada, nenhum bloco dela é fictício.
    abrir('/voluntario')
    expect(await screen.findByText(/^Mantido para você em /, {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), ESPERA)
    expect(etiquetaDeDemonstracao('clientesRecentes', 'serieVoluntario', 'resumoVoluntario', 'baseClientes', 'comparacaoReguaModelo')).toBe(false)
    expect(etiquetas()).toHaveLength(0)
    cleanup()
    // Rodada 3, Fase 4: o assistente passou a perguntar ao backend.
    abrir('/assistente')
    expect(await screen.findByText('O que o assistente vê', {}, ESPERA)).toBeTruthy()
    expect(etiquetaDeDemonstracao('assistente')).toBe(false)
    expect(etiquetas()).toHaveLength(0)
  })

  it('configuração: mensagens e dados e privacidade são de verdade; as outras quatro seções ficam marcadas', async () => {
    // Rodada 3, Fase 6: a seção Dados e privacidade passou a falar com o backend. Ficam
    // de demonstração Empresa, Equipe, Integração e Notificações (dependem do site).
    abrir('/configuracao')
    expect(await screen.findByText('Quem escolhe a mensagem', {}, ESPERA)).toBeTruthy()
    expect(etiquetas()).toHaveLength(4)
    expect(etiquetaDeDemonstracao('exportarTitular', 'anonimizarTitular', 'explicacaoDecisao', 'naoContatar', 'voltarAContatar', 'textoParaPolitica')).toBe(false)
    cleanup()
    // Numa seção fictícia aberta, a faixa do bloco soma mais uma.
    abrir('/configuracao?secao=equipe')
    expect(await screen.findByText('Membros', {}, ESPERA)).toBeTruthy()
    expect(etiquetas()).toHaveLength(5)
    expect(screen.getByText('Este bloco ainda usa dados fictícios')).toBeTruthy()
    cleanup()
    // Na seção Dados e privacidade aberta, nenhuma faixa: só as quatro do menu.
    abrir('/configuracao?secao=dados')
    expect(await screen.findByText('Por quanto tempo a CRAI guarda', {}, ESPERA)).toBeTruthy()
    expect(etiquetas()).toHaveLength(4)
    expect(screen.queryByText('Este bloco ainda usa dados fictícios')).toBeNull()
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

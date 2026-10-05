// @vitest-environment jsdom
/**
 * Rodada 3, Fase 4: a página do Assistente em MODO REAL pergunta ao backend
 * (`POST /assistente`) e mostra o que ele respondeu. O backend aqui é o de mentira
 * (`testes/modoRealFalso.ts`).
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
  vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', '1')
})

import App from './App'
import { MODO_REAL, MOSTRAR_DEMONSTRACAO, emDemonstracao } from './data/api'
import { adaptarAssistente, type AssistenteApi } from './data/adaptadores'
import { corposEnviados, etiquetas, ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'

const ESPERA = { timeout: 4000 }

const DO_LLM: AssistenteApi = {
  texto: 'Nos últimos 30 dias, a CRAI recuperou R$ 1.360,00 em 3 cobranças.\n\nO extrato tem cada valor, linha por linha.',
  links: [
    { rotulo: 'Ver o extrato', para: '/?aba=extrato' },
    { rotulo: 'Ver o involuntário', para: '/involuntario' },
  ],
  sugestoes: ['Quanto a CRAI cobra?', 'O que acontece depois da 3ª tentativa?'],
  origem: 'assistente',
}

beforeEach(() => prepararJsdom())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function abrir() {
  window.history.pushState({}, '', '/assistente')
  return render(<App />)
}

async function perguntar(texto: string) {
  const campo = await screen.findByLabelText('Sua pergunta', {}, ESPERA)
  fireEvent.change(campo, { target: { value: texto } })
  fireEvent.click(screen.getByRole('button', { name: 'Enviar pergunta' }))
}

describe('assistente em modo real', () => {
  it('a função está no mapa de rotas reais, e a página não tem etiqueta nem com a variável ligada', async () => {
    expect(MODO_REAL).toBe(true)
    expect(MOSTRAR_DEMONSTRACAO).toBe(true)
    expect(emDemonstracao('assistente')).toBe(false)
    ligarBackendFalso()
    abrir()
    expect(await screen.findByText('O que o assistente vê', {}, ESPERA)).toBeTruthy()
    expect(etiquetas()).toHaveLength(0)
  })

  it('o aviso do que o assistente não vê continua na tela', async () => {
    ligarBackendFalso()
    abrir()
    const painel = (await screen.findByText('O que o assistente vê', {}, ESPERA)).closest('div')!.parentElement as HTMLElement
    expect(within(painel).getByText('E-mail, telefone, CPF ou chave Pix dos seus clientes. Nunca.')).toBeTruthy()
    expect(within(painel).getByText('O nome ou o identificador de qualquer cliente: ele lê só os totais.')).toBeTruthy()
    expect(within(painel).getByText('O texto das mensagens enviadas.')).toBeTruthy()
    expect(within(painel).getByText('Esta conversa depois que você sair: nada é guardado.')).toBeTruthy()
  })

  it('a pergunta vai sozinha ao backend, e a resposta dele aparece com os links e as sugestões', async () => {
    const chamadas = ligarBackendFalso({ 'POST /assistente': DO_LLM })
    abrir()
    await perguntar('Quanto recuperei este mês?')
    expect(await screen.findByText(/a CRAI recuperou R\$ 1\.360,00 em 3 cobranças\./, {}, ESPERA)).toBeTruthy()
    expect(screen.getByText('O extrato tem cada valor, linha por linha.')).toBeTruthy()
    // O corpo é só a pergunta: nem a conversa, nem a empresa, nem nada da tela.
    expect(corposEnviados().filter((c) => c.chave === 'POST /assistente').map((c) => c.corpo)).toEqual([{ pergunta: 'Quanto recuperei este mês?' }])
    expect(chamadas.filter((c) => c.includes('/assistente'))).toEqual(['POST /assistente'])
    expect(screen.getByRole('link', { name: /Ver o extrato/ }).getAttribute('href')).toBe('/?aba=extrato')
    expect(screen.getByRole('link', { name: /Ver o involuntário/ }).getAttribute('href')).toBe('/involuntario')
    expect(screen.getByRole('button', { name: 'Quanto a CRAI cobra?' })).toBeTruthy()
    expect(screen.queryByText('Resposta fixa')).toBeNull()

    // A segunda pergunta também vai sozinha: a conversa não é enviada.
    fireEvent.click(screen.getByRole('button', { name: 'O que acontece depois da 3ª tentativa?' }))
    await waitFor(() => expect(corposEnviados().filter((c) => c.chave === 'POST /assistente')).toHaveLength(2), ESPERA)
    expect(corposEnviados().filter((c) => c.chave === 'POST /assistente')[1].corpo).toEqual({ pergunta: 'O que acontece depois da 3ª tentativa?' })
  })

  it('sem o LLM, o backend manda o texto de ajuda, e a tela marca como resposta fixa', async () => {
    ligarBackendFalso()
    abrir()
    await perguntar('Quanto recuperei este mês?')
    expect(await screen.findByText('Resposta fixa', {}, ESPERA)).toBeTruthy()
    expect(screen.getByText(/^O assistente está indisponível agora\./)).toBeTruthy()
    expect(screen.getByRole('link', { name: /Ver a visão geral/ }).getAttribute('href')).toBe('/')
  })

  it('no limite de perguntas, a tela diz por quê', async () => {
    ligarBackendFalso({ 'POST /assistente': { __status: 429, corpo: { detail: { motivo: 'limite_do_assistente', tentar_em_segundos: 120 } } } })
    abrir()
    await perguntar('Quanto recuperei este mês?')
    expect(await screen.findByText(/A sua empresa chegou ao limite de perguntas ao assistente nesta hora\./, {}, ESPERA)).toBeTruthy()
    expect(screen.getByText('Resposta fixa')).toBeTruthy()
  })
})

describe('adaptarAssistente', () => {
  it('origem "ajuda" vira o texto fixo da tela; "assistente" continua', () => {
    expect(adaptarAssistente(DO_LLM).origem).toBe('assistente')
    expect(adaptarAssistente({ ...DO_LLM, origem: 'ajuda' }).origem).toBe('texto_fixo')
    expect(adaptarAssistente({ ...DO_LLM, origem: 'outra' as 'ajuda' }).origem).toBe('texto_fixo')
  })

  it('só caminho do próprio painel vira link', () => {
    const r = adaptarAssistente({
      ...DO_LLM,
      links: [
        { rotulo: 'ver o extrato', para: '/?aba=extrato' },
        { rotulo: 'Fora', para: 'https://site.de.fora' },
        { rotulo: 'Fora 2', para: '//site.de.fora' },
        { rotulo: 'Script', para: 'javascript:alert(1)' },
        { rotulo: 'Relativo', para: 'configuracao' },
        { rotulo: 7, para: '/x' } as unknown as { rotulo: string; para: string },
      ],
    })
    expect(r.links).toEqual([{ rotulo: 'Ver o extrato', para: '/?aba=extrato' }])
  })

  it('texto e sugestões começam com maiúscula, e sugestão vazia some', () => {
    const r = adaptarAssistente({ texto: 'você recuperou bastante.', links: [], sugestoes: ['quanto a CRAI cobra?', '', '  ', 5 as unknown as string], origem: 'assistente' })
    expect(r.texto).toBe('Você recuperou bastante.')
    expect(r.sugestoes).toEqual(['Quanto a CRAI cobra?'])
  })

  it('resposta torta do backend não derruba a tela', () => {
    const r = adaptarAssistente({ texto: 'Ok.', origem: 'assistente' } as AssistenteApi)
    expect(r).toEqual({ texto: 'Ok.', links: [], sugestoes: [], origem: 'assistente' })
  })
})

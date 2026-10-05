// @vitest-environment jsdom
/**
 * Rodada 3, Fase 7: a atualização periódica da tela (D8). Aqui, o relógio em si e o
 * `useCarregar` em MODO REAL: 60 segundos nas páginas, 5 no painel de um ciclo aberto, sem
 * passar por "carregando" e sem perder o que já estava na tela.
 */
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
})

import { MODO_REAL } from '../data/api'
import { ATUALIZAR_CICLO_ABERTO_MS, ATUALIZAR_PAGINA_MS, mesmoConteudo, useAtualizarACada } from './useAtualizarACada'
import { useCarregar } from './useCarregar'
import fonteDoCarregar from './useCarregar.ts?raw'
import fonteDoPainel from '../routes/involuntario/CicloDrawer.tsx?raw'

let escondida = false
beforeEach(() => {
  escondida = false
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => escondida })
  vi.useFakeTimers()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

const passar = (ms: number) => act(async () => void (await vi.advanceTimersByTimeAsync(ms)))

describe('os intervalos', () => {
  it('60 segundos nas páginas e 5 no painel de um ciclo aberto', () => {
    expect(ATUALIZAR_PAGINA_MS).toBe(60_000)
    expect(ATUALIZAR_CICLO_ABERTO_MS).toBe(5_000)
  })

  it('o useCarregar usa o intervalo das páginas, e o painel do ciclo usa o dele', () => {
    expect(fonteDoCarregar).toMatch(/ATUALIZAR_PAGINA_MS,\s+MODO_REAL,/)
    expect(fonteDoPainel).toMatch(/ATUALIZAR_CICLO_ABERTO_MS,\s+MODO_REAL,/)
  })
})

describe('useAtualizarACada', () => {
  it('chama a cada intervalo, e não antes', async () => {
    const acao = vi.fn()
    renderHook(() => useAtualizarACada(acao, 60_000))
    await passar(59_999)
    expect(acao).toHaveBeenCalledTimes(0)
    await passar(1)
    expect(acao).toHaveBeenCalledTimes(1)
    await passar(120_000)
    expect(acao).toHaveBeenCalledTimes(3)
  })

  it('desligado, não chama nunca', async () => {
    const acao = vi.fn()
    renderHook(() => useAtualizarACada(acao, 5_000, false))
    await passar(60_000)
    expect(acao).not.toHaveBeenCalled()
  })

  it('para ao sair da tela', async () => {
    const acao = vi.fn()
    const { unmount } = renderHook(() => useAtualizarACada(acao, 5_000))
    await passar(5_000)
    unmount()
    await passar(60_000)
    expect(acao).toHaveBeenCalledTimes(1)
  })

  it('não repete por cima de uma consulta que ainda não voltou', async () => {
    let soltar: () => void = () => undefined
    const acao = vi.fn(() => new Promise<void>((r) => (soltar = r)))
    renderHook(() => useAtualizarACada(acao, 5_000))
    await passar(5_000)
    await passar(20_000)
    expect(acao).toHaveBeenCalledTimes(1)
    soltar()
    await passar(5_000)
    expect(acao).toHaveBeenCalledTimes(2)
  })

  it('uma consulta que falha não quebra nada, e a próxima acontece', async () => {
    const acao = vi.fn(() => Promise.reject(new Error('rede')))
    renderHook(() => useAtualizarACada(acao, 5_000))
    await passar(10_000)
    expect(acao).toHaveBeenCalledTimes(2)
  })

  it('com a aba escondida não consulta; quando ela volta, consulta na hora', async () => {
    const acao = vi.fn()
    renderHook(() => useAtualizarACada(acao, 60_000))
    escondida = true
    await passar(180_000)
    expect(acao).not.toHaveBeenCalled()
    escondida = false
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(acao).toHaveBeenCalledTimes(1)
  })

  it('vale sempre a ação mais recente, sem reiniciar o relógio', async () => {
    const primeira = vi.fn()
    const segunda = vi.fn()
    const { rerender } = renderHook(({ acao }) => useAtualizarACada(acao, 60_000), { initialProps: { acao: primeira } })
    await passar(40_000)
    rerender({ acao: segunda })
    await passar(20_000)
    expect(primeira).not.toHaveBeenCalled()
    expect(segunda).toHaveBeenCalledTimes(1)
  })
})

describe('mesmoConteudo', () => {
  it('compara pelo conteúdo, não pela referência', () => {
    expect(mesmoConteudo({ a: [1, 2], b: null }, { a: [1, 2], b: null })).toBe(true)
    expect(mesmoConteudo({ a: [1, 2] }, { a: [1, 3] })).toBe(false)
    expect(mesmoConteudo(null, { a: 1 })).toBe(false)
  })
})

describe('useCarregar em modo real: a consulta periódica é silenciosa', () => {
  it('o modo real está ligado neste arquivo', () => {
    expect(MODO_REAL).toBe(true)
  })

  it('aos 60 segundos traz o dado novo sem passar por "carregando"', async () => {
    let resposta = { total: 1 }
    const carregar = vi.fn(() => Promise.resolve(resposta))
    const estados: boolean[] = []
    const { result } = renderHook(() => {
      const carga = useCarregar(carregar, [])
      estados.push(carga.carregando)
      return carga
    })
    await passar(0)
    expect(result.current.dados).toEqual({ total: 1 })
    expect(result.current.carregando).toBe(false)
    const ate = estados.length

    resposta = { total: 2 }
    await passar(60_000)
    expect(carregar).toHaveBeenCalledTimes(2)
    expect(result.current.dados).toEqual({ total: 2 })
    expect(estados.slice(ate)).not.toContain(true)
  })

  it('resposta igual não troca o dado: nada é redesenhado', async () => {
    const carregar = vi.fn(() => Promise.resolve({ lista: [1, 2, 3] }))
    let desenhos = 0
    const { result } = renderHook(() => {
      desenhos += 1
      return useCarregar(carregar, [])
    })
    await passar(0)
    const antes = result.current.dados
    const desenhosAntes = desenhos
    await passar(180_000)
    expect(carregar).toHaveBeenCalledTimes(4)
    expect(result.current.dados).toBe(antes)
    expect(desenhos).toBe(desenhosAntes)
  })

  it('consulta que falha deixa na tela o que já estava, sem erro', async () => {
    let falhar = false
    const carregar = vi.fn(() => (falhar ? Promise.reject(new Error('rede')) : Promise.resolve({ total: 7 })))
    const { result } = renderHook(() => useCarregar(carregar, []))
    await passar(0)
    falhar = true
    await passar(60_000)
    expect(carregar).toHaveBeenCalledTimes(2)
    expect(result.current.dados).toEqual({ total: 7 })
    expect(result.current.erro).toBeNull()
    expect(result.current.carregando).toBe(false)
  })

  it('a tela que abriu com erro se recupera sozinha na consulta seguinte', async () => {
    let falhar = true
    const carregar = vi.fn(() => (falhar ? Promise.reject(new Error('rede')) : Promise.resolve({ total: 7 })))
    const { result } = renderHook(() => useCarregar(carregar, []))
    await passar(0)
    expect(result.current.erro).toBe('Algo deu errado ao carregar. Tente de novo.')
    falhar = false
    await passar(60_000)
    expect(result.current.erro).toBeNull()
    expect(result.current.dados).toEqual({ total: 7 })
  })

  it('a resposta silenciosa de um filtro que já mudou é descartada', async () => {
    const soltar: Record<string, (v: { filtro: string; n: number }) => void> = {}
    let n = 0
    const carregarDe = (filtro: string) => () =>
      new Promise<{ filtro: string; n: number }>((r) => {
        n += 1
        soltar[`${filtro}${n}`] = r
      })
    const { result, rerender } = renderHook(({ filtro }) => useCarregar(carregarDe(filtro), [filtro]), { initialProps: { filtro: 'todos' } })
    await act(async () => soltar.todos1({ filtro: 'todos', n: 1 }))
    await passar(60_000) // a consulta silenciosa de "todos" saiu (todos2) e ainda não voltou
    rerender({ filtro: 'recuperado' }) // a pessoa trocou o filtro (recuperado3)
    await act(async () => soltar.recuperado3({ filtro: 'recuperado', n: 3 }))
    await act(async () => soltar.todos2({ filtro: 'todos', n: 2 })) // a resposta velha chega depois
    expect(result.current.dados).toEqual({ filtro: 'recuperado', n: 3 })
  })
})

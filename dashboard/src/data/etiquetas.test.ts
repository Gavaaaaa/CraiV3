/**
 * Rodada 2, Fase 3: a variável `VITE_CRAI_MOSTRAR_DEMONSTRACAO`.
 *
 * A etiqueta "Demonstração" de um bloco só aparece quando as TRÊS coisas são verdade: a
 * variável está em `1`, o modo real está ligado e o bloco ainda usa dado fictício. O
 * padrão (sem a variável) é não aparecer.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

async function carregarApi(url: string, variavel: string | undefined) {
  vi.resetModules()
  vi.stubEnv('VITE_CRAI_API_URL', url)
  if (variavel !== undefined) vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', variavel)
  return import('./api')
}

afterEach(() => vi.unstubAllEnvs())

describe('a variável das etiquetas', () => {
  it('sem a variável, nada é etiquetado, com ou sem o modo real', async () => {
    for (const url of ['', 'http://backend.teste']) {
      const api = await carregarApi(url, undefined)
      expect(api.MOSTRAR_DEMONSTRACAO).toBe(false)
      expect(api.etiquetaDeDemonstracao('resumoVisaoGeral')).toBe(false)
      expect(api.etiquetaDeDemonstracao('assistente', 'funil', 'ciclos')).toBe(false)
    }
  })

  it('só o valor 1 liga: vazio, 0, true e "sim" não ligam', async () => {
    for (const valor of ['', '0', 'true', 'sim', ' ']) {
      const api = await carregarApi('http://backend.teste', valor)
      expect(api.MOSTRAR_DEMONSTRACAO, `valor ${JSON.stringify(valor)}`).toBe(false)
      expect(api.etiquetaDeDemonstracao('resumoVisaoGeral')).toBe(false)
    }
    expect((await carregarApi('http://backend.teste', '1')).MOSTRAR_DEMONSTRACAO).toBe(true)
    expect((await carregarApi('http://backend.teste', ' 1 ')).MOSTRAR_DEMONSTRACAO).toBe(true)
  })

  it('com a variável e o modo real: etiqueta só no que ainda é fictício', async () => {
    const api = await carregarApi('http://backend.teste', '1')
    expect(api.etiquetaDeDemonstracao('resumoVisaoGeral')).toBe(true)
    expect(api.etiquetaDeDemonstracao('ciclos', 'funil')).toBe(true)
    for (const real of Object.keys(api.ROTAS_REAIS)) expect(api.etiquetaDeDemonstracao(real), real).toBe(false)
  })

  it('com a variável e sem o modo real: nada, como sempre foi na demonstração', async () => {
    const api = await carregarApi('', '1')
    expect(api.MOSTRAR_DEMONSTRACAO).toBe(true)
    expect(api.MODO_REAL).toBe(false)
    expect(api.etiquetaDeDemonstracao('resumoVisaoGeral')).toBe(false)
  })

  it('o registro do que é fictício não depende da variável', async () => {
    const semVariavel = await carregarApi('http://backend.teste', undefined)
    const comVariavel = await carregarApi('http://backend.teste', '1')
    for (const api of [semVariavel, comVariavel]) {
      expect(api.emDemonstracao('resumoVisaoGeral')).toBe(true)
      expect(api.emDemonstracao('ciclos')).toBe(false)
    }
  })
})

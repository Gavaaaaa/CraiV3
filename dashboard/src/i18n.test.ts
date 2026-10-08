/**
 * A catraca do inglês. Todo texto passado a `t('...')` no código tem tradução no dicionário,
 * a tradução usa os mesmos marcadores `{nome}` e começa com maiúscula quando o português
 * começa. E `t()` troca de idioma de verdade.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EN } from './i18n/en'

// O código do painel, lido como texto (sem os testes, os dados de teste e o próprio dicionário).
const FONTES = Object.entries(
  import.meta.glob(['./**/*.ts', './**/*.tsx', '!./**/*.test.*', '!./testes/**', '!./i18n/**'], { query: '?raw', import: 'default', eager: true }) as Record<string, string>,
)

/** Os textos literais passados a `t()`: `t('...')`, `t("...")` e `` t(`...`) `` sem `${}`. */
function textosDe(fonte: string): string[] {
  const achados: string[] = []
  const re = /\bt\(\s*(['"`])((?:\\.|(?!\1)[^\\])*)\1/g
  let m: RegExpExecArray | null
  while ((m = re.exec(fonte))) {
    if (m[1] === '`' && m[2].includes('${')) continue
    achados.push(m[2].replace(/\\(['"`\\])/g, '$1').replace(/\\n/g, '\n'))
  }
  return achados
}

const marcadores = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

describe('o dicionário do inglês', () => {
  const usados = new Map<string, string>()
  for (const [arquivo, fonte] of FONTES) {
    for (const texto of textosDe(fonte)) usados.set(texto, arquivo)
  }

  it('o código usa t() em muitos lugares (a varredura acha os textos)', () => {
    expect(usados.size).toBeGreaterThan(800)
  })

  it('todo texto passado a t() tem tradução', () => {
    const faltando = [...usados].filter(([texto]) => !(texto in EN)).map(([texto, onde]) => `${onde}: ${texto}`)
    expect(faltando).toEqual([])
  })

  it('a tradução usa os mesmos marcadores {nome} do português', () => {
    const errados = Object.entries(EN).filter(([pt, en]) => marcadores(pt).join() !== marcadores(en).join())
    expect(errados).toEqual([])
  })

  it('começa com maiúscula quando o português começa', () => {
    const minusculas = Object.entries(EN).filter(([pt, en]) => /^[A-ZÀ-Ý]/.test(pt) && /^[a-z]/.test(en))
    expect(minusculas).toEqual([])
  })
})

describe('t()', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.resetModules()
  })

  it('em português devolve o próprio texto, com os marcadores preenchidos', async () => {
    vi.resetModules()
    const { t, idiomaAtual } = await import('./lib/idioma')
    expect(idiomaAtual()).toBe('pt')
    expect(t('Visão geral')).toBe('Visão geral')
    expect(t('Olá, {nome}', { nome: 'NimbusFlow' })).toBe('Olá, NimbusFlow')
  })

  it('com o inglês guardado no navegador, devolve a tradução', async () => {
    vi.stubGlobal('localStorage', { getItem: (k: string) => (k === 'crai_idioma' ? 'en' : null), setItem: () => undefined })
    vi.resetModules()
    const { t, idiomaAtual, localeAtual } = await import('./lib/idioma')
    expect(idiomaAtual()).toBe('en')
    expect(localeAtual()).toBe('en-US')
    expect(t('Visão geral')).toBe(EN['Visão geral'])
    expect(t('Olá, {nome}', { nome: 'NimbusFlow' })).toBe(EN['Olá, {nome}'].replace('{nome}', 'NimbusFlow'))
    // Texto sem tradução (o que vem pronto do backend) passa como está.
    expect(t('Uma frase que o backend escreveu')).toBe('Uma frase que o backend escreveu')
  })
})

/**
 * Rodada 2, ajustes: os tamanhos de letra do painel moram num lugar só (`index.css`), e
 * nenhum texto fica abaixo de 13.5px.
 *
 * É uma catraca sobre o código-fonte: reprova se alguém escrever um tamanho pequeno direto
 * numa tela (`text-[13px]`), ou baixar um dos três tamanhos em `index.css`. O tamanho que o
 * navegador de fato desenha foi medido à parte, página por página (ver o relatório).
 */
// @ts-expect-error O projeto das telas não carrega os tipos do Node; aqui só se lê um arquivo.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// O `index.css` é lido do disco: nos testes o Vite entrega os `.css` vazios.
const css: string = readFileSync(new URL('./index.css', import.meta.url), 'utf-8')

const MENOR = 13.5

// O código-fonte de cada tela, como texto (o Vite entrega com `?raw`; nenhum módulo do Node).
const fontes = import.meta.glob('./**/*.tsx', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
const telas = Object.entries(fontes).filter(([caminho]) => !caminho.includes('.test.'))

describe('os tamanhos de letra', () => {
  it('index.css: texto normal 16px, apoio 15px, rótulo 13.5px', () => {
    expect(css).toMatch(/--text-rotulo:\s*13\.5px;/)
    expect(css).toMatch(/--text-apoio:\s*15px;/)
    expect(css).toMatch(/--text-normal:\s*16px;/)
    // As classes de sempre usam os mesmos três valores.
    expect(css).toMatch(/body\s*\{[^}]*font-size:\s*var\(--text-normal\)/)
    expect(css).toMatch(/\.t-label\s*\{[^}]*font-size:\s*var\(--text-rotulo\)/)
    expect(css).toMatch(/\.t-apoio\s*\{[^}]*font-size:\s*var\(--text-apoio\)/)
  })

  it('index.css não declara nenhum tamanho abaixo de 13.5px', () => {
    const tamanhos = [...css.matchAll(/(?:font-size|--text-[a-z]+):\s*([0-9.]+)px/g)].map((m) => Number(m[1]))
    expect(tamanhos.length).toBeGreaterThanOrEqual(8)
    expect(tamanhos.filter((t) => t < MENOR)).toEqual([])
  })

  it('há telas para olhar (a catraca não passa sem ter o que verificar)', () => {
    expect(telas.length).toBeGreaterThan(25)
    expect(telas.some(([caminho]) => caminho.endsWith('/routes/Api.tsx'))).toBe(true)
  })

  it('nenhuma tela escreve tamanho pequeno direto: usa text-rotulo, text-apoio ou text-normal', () => {
    const achados: string[] = []
    for (const [arquivo, texto] of telas) {
      for (const m of texto.matchAll(/text-\[([0-9.]+)px\]/g)) {
        if (Number(m[1]) < 16) achados.push(`${arquivo}: ${m[0]}`)
      }
      // Tamanhos do Tailwind menores que o rótulo (text-xs é 12px).
      for (const m of texto.matchAll(/(?<![\w-])text-xs(?![\w-])/g)) achados.push(`${arquivo}: ${m[0]}`)
    }
    expect(achados).toEqual([])
  })

  it('nenhum texto de gráfico (SVG) fica abaixo de 13.5px', () => {
    const achados: string[] = []
    for (const [arquivo, texto] of telas) {
      for (const m of texto.matchAll(/fontSize=\{([0-9.]+)\}/g)) {
        if (Number(m[1]) < MENOR) achados.push(`${arquivo}: ${m[0]}`)
      }
      for (const m of texto.matchAll(/fontSize:\s*['"]?([0-9.]+)/g)) {
        if (Number(m[1]) < MENOR) achados.push(`${arquivo}: ${m[0]}`)
      }
    }
    expect(achados).toEqual([])
  })

  it('as classes de tamanho são usadas de verdade nas telas', () => {
    const tudo = telas.map(([, texto]) => texto).join('\n')
    for (const classe of ['text-rotulo', 'text-apoio', 'text-normal', 't-label', 't-apoio']) {
      expect(tudo.split(classe).length - 1, classe).toBeGreaterThan(3)
    }
  })
})

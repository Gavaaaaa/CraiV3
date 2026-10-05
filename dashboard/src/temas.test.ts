/**
 * Tema e botões, Parte 2: o contraste dos dois temas, medido nos tokens de `index.css`.
 *
 * Para cada par de texto e fundo realmente usado nas telas, a razão de contraste (WCAG 2) tem
 * de ser 4,5:1 para texto, e 3:1 para texto grande, para bordas de controle e para marcas de
 * gráfico. A lista dos pares está aqui, e vale nos dois temas. Par que não passa é corrigido
 * no token, nunca tirado da lista.
 */
// @ts-expect-error O projeto das telas não carrega os tipos do Node; aqui só se lê um arquivo.
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const css: string = readFileSync(new URL('./index.css', import.meta.url), 'utf-8')

type Cor = [number, number, number, number] // r, g, b, alfa

function bloco(inicio: RegExp): Record<string, string> {
  const i = css.search(inicio)
  expect(i, `bloco ${inicio}`).toBeGreaterThanOrEqual(0)
  const fim = css.indexOf('\n}', i)
  const corpo = css.slice(i, fim)
  const tokens: Record<string, string> = {}
  for (const m of corpo.matchAll(/--([a-z0-9-]+):\s*([^;]+);/g)) tokens[m[1]] = m[2].trim()
  return tokens
}

function cor(valor: string): Cor {
  const hex = /^#([0-9a-f]{6})$/i.exec(valor)
  if (hex) return [parseInt(hex[1].slice(0, 2), 16), parseInt(hex[1].slice(2, 4), 16), parseInt(hex[1].slice(4, 6), 16), 1]
  const rgba = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+))?\s*\)$/.exec(valor)
  if (rgba) return [Number(rgba[1]), Number(rgba[2]), Number(rgba[3]), rgba[4] === undefined ? 1 : Number(rgba[4])]
  throw new Error(`cor que o teste não lê: ${valor}`)
}

/** `frente` com a sua transparência (ou uma `alfa` a mais) por cima de `fundo`, que é opaco. */
function sobre(frente: Cor, fundo: Cor, alfa = 1): Cor {
  const a = frente[3] * alfa
  return [frente[0] * a + fundo[0] * (1 - a), frente[1] * a + fundo[1] * (1 - a), frente[2] * a + fundo[2] * (1 - a), 1]
}

function luminancia([r, g, b]: Cor): number {
  const canal = (c: number) => {
    const v = c / 255
    return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b)
}

function contraste(a: Cor, b: Cor): number {
  const la = luminancia(a)
  const lb = luminancia(b)
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05)
}

/** Os tokens de um tema: o escuro é o `@theme` mais o `:root`; o claro sobrepõe os dele. */
function tema(nome: 'escuro' | 'claro'): Record<string, Cor> {
  const base = { ...bloco(/@theme \{/), ...bloco(/^:root \{/m) }
  const valores = nome === 'claro' ? { ...base, ...bloco(/^:root\[data-tema='claro'\] \{/m) } : base
  const cores: Record<string, Cor> = {}
  for (const [k, v] of Object.entries(valores)) {
    if (/^#|^rgba?\(/.test(v)) cores[k.replace(/^color-/, '')] = cor(v)
  }
  return cores
}

const ESCURO = tema('escuro')
const CLARO = tema('claro')
const TEMAS = { escuro: ESCURO, claro: CLARO }

/** As superfícies em que o texto aparece, já compostas (o vidro e as caixas são translúcidos). */
function superficies(t: Record<string, Cor>) {
  return {
    pagina: t.ink,
    cartao: t.card,
    barra: t.bar,
    slate: t.slate,
    vidro: sobre(t['vidro-b'], t.ink),
    caixa: sobre(t.slate, t.ink, 0.6), // `bg-slate/60`: o topo, o sino, as abas
    inset: sobre(t.ink, t.card, 0.4), // `bg-ink/40`: campos e seções dentro dos cartões
    'fundo-ok': t['fundo-ok'],
    'fundo-erro': t['fundo-erro'],
  }
}

/** [texto, fundos, mínimo, onde]. O que está como `sobre:` é texto em cima de um preenchimento. */
const PARES: [string, string[], number, string][] = [
  // Texto comum (4,5:1)
  ['paper', ['pagina', 'cartao', 'barra', 'slate', 'vidro', 'caixa', 'inset'], 4.5, 'o texto principal, em toda parte'],
  ['silver', ['pagina', 'cartao', 'vidro', 'caixa', 'inset', 'slate'], 4.5, 'texto de apoio, cabeçalhos de tabela, dicas do menu'],
  ['muted', ['pagina', 'cartao', 'vidro', 'inset', 'caixa'], 4.5, 'texto de apoio mais fraco (ids, prazos, notas)'],
  ['orange', ['pagina', 'cartao', 'vidro', 'inset', 'caixa'], 4.5, 'filtro ativo, selos, números em laranja, o papel no topo'],
  ['amber', ['pagina', 'cartao', 'vidro'], 4.5, 'avisos em âmbar (API, simulação), links de download'],
  ['ok', ['pagina', 'cartao', 'vidro', 'inset', 'fundo-ok'], 4.5, 'status recuperado, "para você", aviso de salvo'],
  ['warn', ['pagina', 'cartao', 'vidro', 'inset'], 4.5, 'status em análise, prazo da escolha'],
  ['danger-texto', ['pagina', 'cartao', 'vidro', 'inset'], 4.5, 'selos e ícones de risco, "reduziu a chance"'],
  ['danger-aviso', ['cartao', 'vidro', 'fundo-erro'], 4.5, 'texto dos avisos de erro'],
  ['serie-vol-texto', ['cartao', 'vidro'], 4.5, 'ícone da atividade "Oferta aceita"'],
  // Bordas de controle e marcas de gráfico (3:1)
  ['graphite', ['pagina', 'cartao', 'vidro', 'slate'], 3, 'borda do botão fantasma, da área de anexar, pontos neutros'],
  ['campo', ['pagina', 'cartao', 'vidro', 'caixa', 'inset'], 3, 'borda dos campos de formulário'],
  ['amber', ['pagina', 'cartao', 'barra', 'caixa'], 3, 'o anel de foco do teclado'],
  ['orange', ['caixa', 'cartao'], 3, 'a borda do seletor de papel e do filtro ativo'],
  ['serie-inv', ['pagina', 'cartao', 'vidro'], 3, 'as colunas do involuntário no gráfico, o funil'],
  ['serie-vol', ['pagina', 'cartao', 'vidro'], 3, 'a linha do voluntário no gráfico'],
  ['silver', ['vidro'], 3, 'eixos e rótulos do gráfico (13,5 px: também passa em 4,5)'],
]

/** Texto em cima de preenchimento colorido: [texto, preenchimento, mínimo, onde]. */
const SOBRE_PREENCHIMENTO: [string, string, number, string][] = [
  ['ink', 'paper', 4.5, 'a aba ativa, o botão principal, o balão da pessoa no assistente'],
  ['ink', 'paper-forte', 4.5, 'o botão principal ao passar o mouse'],
  ['ink', 'orange', 4.5, 'o número do sino, o trilho das tentativas'],
  ['ink', 'amber', 4.5, 'o selo "Demo" no menu'],
  ['ink', 'ok', 4.5, 'as etapas concluídas da simulação'],
  ['sobre-destaque', 'marca', 4.5, 'o cartão laranja (o começo do degradê)'],
  ['sobre-destaque', 'marca-fim', 4.5, 'o cartão laranja (o fim do degradê, #c96a00)'],
]

describe('os tokens dos dois temas', () => {
  it('index.css tem o tema claro sob :root[data-tema="claro"], e o escuro é o padrão', () => {
    expect(css).toMatch(/color-scheme: dark;/)
    expect(css).toMatch(/:root\[data-tema='claro'\] \{[^}]*color-scheme: light;/)
    expect(Object.keys(bloco(/^:root\[data-tema='claro'\] \{/m)).length).toBeGreaterThanOrEqual(28)
  })

  it('todo token de cor do escuro tem valor no claro (e o que não muda está igual de propósito)', () => {
    const fixos = ['marca', 'sobre-destaque', 'sombra']
    for (const nome of Object.keys(ESCURO)) {
      expect(CLARO[nome], nome).toBeDefined()
      const igual = ESCURO[nome].join() === CLARO[nome].join()
      if (fixos.includes(nome)) expect(igual, `${nome} não muda com o tema`).toBe(true)
    }
    expect(ESCURO.marca).toEqual(cor('#ef9311'))
  })

  it('o laranja da marca continua sendo #ef9311 no cartão laranja', () => {
    expect(css).toMatch(/\.card-orange \{[^}]*#f39c1f[^}]*#c96a00/)
    expect(css).toMatch(/\.card-orange \{[^}]*color: var\(--color-sobre-destaque\)/)
  })
})

describe.each(['escuro', 'claro'] as const)('contraste no tema %s', (nome) => {
  const t = TEMAS[nome]
  const s = superficies(t)

  it.each(PARES)('%s sobre %s: ao menos %s (%s)', (texto, fundos, minimo) => {
    const falhas: string[] = []
    for (const fundo of fundos) {
      const razao = contraste(sobre(t[texto], s[fundo as keyof typeof s]), s[fundo as keyof typeof s])
      if (razao < minimo) falhas.push(`${texto} sobre ${fundo}: ${razao.toFixed(2)}`)
    }
    expect(falhas).toEqual([])
  })

  it.each(SOBRE_PREENCHIMENTO)('%s sobre o preenchimento %s: ao menos %s (%s)', (texto, fundo, minimo) => {
    const preenchimento = fundo === 'marca-fim' ? cor('#c96a00') : t[fundo]
    expect(contraste(t[texto], preenchimento)).toBeGreaterThanOrEqual(minimo)
  })
})

describe('a tabela do relatório', () => {
  it('imprime a razão de cada par nos dois temas (para copiar no relatório)', () => {
    const linhas: string[] = []
    for (const [texto, fundos, minimo] of PARES) {
      for (const fundo of fundos) {
        const e = contraste(sobre(ESCURO[texto], superficies(ESCURO)[fundo as never]), superficies(ESCURO)[fundo as never])
        const c = contraste(sobre(CLARO[texto], superficies(CLARO)[fundo as never]), superficies(CLARO)[fundo as never])
        linhas.push(`| ${texto} | ${fundo} | ${minimo}:1 | ${e.toFixed(2)} | ${c.toFixed(2)} |`)
      }
    }
    for (const [texto, fundo, minimo] of SOBRE_PREENCHIMENTO) {
      const pe = fundo === 'marca-fim' ? cor('#c96a00') : ESCURO[fundo]
      const pc = fundo === 'marca-fim' ? cor('#c96a00') : CLARO[fundo]
      linhas.push(`| ${texto} | preenchimento ${fundo} | ${minimo}:1 | ${contraste(ESCURO[texto], pe).toFixed(2)} | ${contraste(CLARO[texto], pc).toFixed(2)} |`)
    }
    if (import.meta.env.CRAI_TABELA_DE_CONTRASTE) console.log(linhas.join('\n'))
    expect(linhas.length).toBeGreaterThan(40)
  })
})

/**
 * O texto para a política de privacidade vem do backend em Markdown simples: parágrafos
 * separados por linha em branco e trechos em negrito entre dois asteriscos. A tela mostra o
 * texto formatado (sem os asteriscos), e o botão de copiar entrega o texto limpo.
 */

export interface Trecho {
  texto: string
  forte: boolean
}

/** Os parágrafos do texto, cada um como uma lista de trechos (negrito ou não). */
export function paragrafosComNegrito(texto: string): Trecho[][] {
  return texto
    .replace(/\r\n/g, '\n')
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((paragrafo) => {
      const trechos: Trecho[] = []
      // Um par de `**` abre e fecha o negrito. Asterisco sem par fica como texto.
      const partes = paragrafo.split('**')
      const comPar = partes.length % 2 === 1
      partes.forEach((parte, i) => {
        if (!parte) return
        trechos.push({ texto: parte, forte: comPar && i % 2 === 1 })
      })
      if (!comPar) return [{ texto: paragrafo, forte: false }]
      return trechos
    })
}

/** O mesmo texto sem as marcas do negrito, com um parágrafo por bloco: é o que vai para a área de transferência. */
export function semMarcasDeNegrito(texto: string): string {
  return paragrafosComNegrito(texto)
    .map((p) => p.map((t) => t.texto).join(''))
    .join('\n\n')
}

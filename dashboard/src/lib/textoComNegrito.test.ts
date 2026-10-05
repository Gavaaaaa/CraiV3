import { describe, expect, it } from 'vitest'
import { paragrafosComNegrito, semMarcasDeNegrito } from './textoComNegrito'

describe('texto com negrito (o texto para a política de privacidade)', () => {
  const TEXTO = 'Usamos a CRAI.\n\n**Mensagens.** Você recebe uma oferta a cada 30 dias.\r\n\r\n**Seus direitos.** Peça uma cópia.'

  it('separa os parágrafos e marca o que está entre dois asteriscos', () => {
    expect(paragrafosComNegrito(TEXTO)).toEqual([
      [{ texto: 'Usamos a CRAI.', forte: false }],
      [
        { texto: 'Mensagens.', forte: true },
        { texto: ' Você recebe uma oferta a cada 30 dias.', forte: false },
      ],
      [
        { texto: 'Seus direitos.', forte: true },
        { texto: ' Peça uma cópia.', forte: false },
      ],
    ])
  })

  it('o texto limpo não tem asterisco e mantém um parágrafo por bloco', () => {
    const limpo = semMarcasDeNegrito(TEXTO)
    expect(limpo).toBe('Usamos a CRAI.\n\nMensagens. Você recebe uma oferta a cada 30 dias.\n\nSeus direitos. Peça uma cópia.')
    expect(limpo).not.toContain('*')
  })

  it('asterisco sem par fica como texto, e texto vazio não vira parágrafo', () => {
    expect(paragrafosComNegrito('Preço **cheio, sem fechar')).toEqual([[{ texto: 'Preço **cheio, sem fechar', forte: false }]])
    expect(paragrafosComNegrito('')).toEqual([])
    expect(paragrafosComNegrito('\n\n  \n')).toEqual([])
  })
})

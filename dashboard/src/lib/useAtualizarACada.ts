import { useEffect, useRef } from 'react'

/** De quanto em quanto tempo uma página com dados do backend consulta de novo. */
export const ATUALIZAR_PAGINA_MS = 60_000
/** O painel de um ciclo aberto consulta mais vezes: é onde a pessoa espera ver a mudança. */
export const ATUALIZAR_CICLO_ABERTO_MS = 5_000

/**
 * Chama `acao` a cada `ms`, enquanto `ligado`. É uma consulta periódica, não tempo real.
 *
 * - Com a aba do navegador escondida, não consulta; quando ela volta, consulta na hora se já
 *   passou o intervalo.
 * - Uma consulta que ainda não voltou não é repetida por cima.
 * - `acao` pode mudar a cada desenho da tela: vale sempre a mais recente, sem reiniciar o relógio.
 */
export function useAtualizarACada(acao: () => void | Promise<unknown>, ms: number, ligado = true): void {
  const atual = useRef(acao)
  atual.current = acao

  useEffect(() => {
    if (!ligado) return
    let vivo = true
    let emCurso = false
    let ultima = Date.now()

    const consultar = () => {
      if (emCurso || document.hidden) return
      emCurso = true
      ultima = Date.now()
      Promise.resolve()
        .then(() => atual.current())
        .catch(() => undefined) // quem atualiza em silêncio decide o que fazer com a falha
        .finally(() => {
          if (vivo) emCurso = false
        })
    }
    const aoVoltar = () => {
      if (!document.hidden && Date.now() - ultima >= ms) consultar()
    }

    const relogio = setInterval(consultar, ms)
    document.addEventListener('visibilitychange', aoVoltar)
    return () => {
      vivo = false
      clearInterval(relogio)
      document.removeEventListener('visibilitychange', aoVoltar)
    }
  }, [ms, ligado])
}

/** As duas respostas dizem a mesma coisa? (O que vem do backend é JSON puro.) */
export function mesmoConteudo(a: unknown, b: unknown): boolean {
  if (a === b) return true
  try {
    return JSON.stringify(a) === JSON.stringify(b)
  } catch {
    return false
  }
}

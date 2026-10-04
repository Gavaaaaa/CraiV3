import { useCallback, useEffect, useState, type DependencyList } from 'react'
import { ErroApi } from '../data/api'

export interface Carga<T> {
  dados: T | null
  erro: string | null
  carregando: boolean
  recarregar: () => void
}

/**
 * Carrega dados de `api.ts` com os três estados que toda tela precisa: carregando, erro e pronto.
 * Ao recarregar, mantém os dados anteriores na tela (sem piscar) até os novos chegarem.
 */
export function useCarregar<T>(carregar: () => Promise<T>, deps: DependencyList): Carga<T> {
  const [dados, setDados] = useState<T | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [carregando, setCarregando] = useState(true)
  const [tentativa, setTentativa] = useState(0)

  useEffect(() => {
    let vivo = true
    setCarregando(true)
    setErro(null)
    carregar()
      .then((r) => {
        if (!vivo) return
        setDados(r)
        setCarregando(false)
      })
      .catch((e: unknown) => {
        if (!vivo) return
        setErro(e instanceof ErroApi ? e.message : 'Algo deu errado ao carregar. Tente de novo.')
        setCarregando(false)
      })
    return () => {
      vivo = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tentativa])

  const recarregar = useCallback(() => setTentativa((t) => t + 1), [])
  return { dados, erro, carregando, recarregar }
}

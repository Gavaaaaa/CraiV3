import { useCallback, useEffect, useRef, useState, type DependencyList } from 'react'
import { ErroApi, MODO_REAL } from '../data/api'
import { ATUALIZAR_PAGINA_MS, mesmoConteudo, useAtualizarACada } from './useAtualizarACada'

export interface Carga<T> {
  dados: T | null
  erro: string | null
  carregando: boolean
  recarregar: () => void
}

/**
 * Carrega dados de `api.ts` com os três estados que toda tela precisa: carregando, erro e pronto.
 * Ao recarregar, mantém os dados anteriores na tela (sem piscar) até os novos chegarem.
 *
 * Com o backend ligado, consulta de novo a cada 60 segundos, EM SILÊNCIO: não passa pelo estado
 * "carregando", não redesenha nada se a resposta veio igual e, se a consulta falhar, deixa na
 * tela o que já estava. O que a pessoa digitou (a busca, um formulário) mora no estado da
 * página, não aqui, e por isso não é tocado. Na demonstração os dados são fixos: não há consulta.
 */
export function useCarregar<T>(carregar: () => Promise<T>, deps: DependencyList): Carga<T> {
  const [dados, setDados] = useState<T | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [carregando, setCarregando] = useState(true)
  const [tentativa, setTentativa] = useState(0)
  // A carga da vez: uma resposta silenciosa que chega depois de a página pedir outra coisa
  // (outro filtro, outra busca) é descartada.
  const carga = useRef(0)
  const ocupado = useRef(true)

  useEffect(() => {
    let vivo = true
    carga.current += 1
    ocupado.current = true
    setCarregando(true)
    setErro(null)
    carregar()
      .then((r) => {
        if (!vivo) return
        ocupado.current = false
        setDados(r)
        setCarregando(false)
      })
      .catch((e: unknown) => {
        if (!vivo) return
        ocupado.current = false
        setErro(e instanceof ErroApi ? e.message : 'Algo deu errado ao carregar. Tente de novo.')
        setCarregando(false)
      })
    return () => {
      vivo = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tentativa])

  const montado = useRef(true)
  useEffect(() => {
    montado.current = true
    return () => {
      montado.current = false
    }
  }, [])

  useAtualizarACada(
    () => {
      if (ocupado.current) return
      const pedida = carga.current
      return carregar().then((r) => {
        if (!montado.current || pedida !== carga.current) return
        // Resposta igual não mexe no estado: nada é redesenhado.
        if (!mesmoConteudo(dados, r)) setDados(r)
        if (erro !== null) setErro(null)
      })
    },
    ATUALIZAR_PAGINA_MS,
    MODO_REAL,
  )

  const recarregar = useCallback(() => setTentativa((t) => t + 1), [])
  return { dados, erro, carregando, recarregar }
}

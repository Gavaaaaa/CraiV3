import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { BUSCA_MINIMO_DE_LETRAS, ErroApi, api } from '../../data/api'
import type { ResultadoBusca } from '../../data/tipos'
import { fmt } from '../../lib/format'
import { Badge } from '../ui/Badge'
import { StatusPill } from '../ui/StatusPill'
import { IconSearch } from '../icons/Icons'

/** Quanto a busca espera a pessoa parar de digitar antes de consultar. */
export const ATRASO_DA_BUSCA_MS = 250

/**
 * A busca do topo: procura pelo nome ou pelo identificador, nos clientes da base e nos ciclos
 * de cobrança da empresa, e leva ao resultado. Um ciclo abre o painel dele no Involuntário; um
 * cliente abre a lista de clientes do Voluntário com ele no topo.
 */
export function BuscaDoTopo() {
  const [texto, setTexto] = useState('')
  const [resultado, setResultado] = useState<ResultadoBusca | null>(null)
  const [buscando, setBuscando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)
  const [aberto, setAberto] = useState(false)
  const navegar = useNavigate()
  const caixa = useRef<HTMLDivElement>(null)
  const procurado = texto.trim()
  const procura = procurado.length >= BUSCA_MINIMO_DE_LETRAS

  useEffect(() => {
    if (!procura) {
      setResultado(null)
      setErro(null)
      setBuscando(false)
      return
    }
    let vivo = true
    setBuscando(true)
    const espera = setTimeout(() => {
      api
        .buscar(procurado)
        .then((r) => {
          if (!vivo) return
          setResultado(r)
          setErro(null)
          setBuscando(false)
        })
        .catch((e: unknown) => {
          if (!vivo) return
          setResultado(null)
          setErro(e instanceof ErroApi ? e.message : 'Não deu para buscar agora. Tente de novo.')
          setBuscando(false)
        })
    }, ATRASO_DA_BUSCA_MS)
    return () => {
      vivo = false
      clearTimeout(espera)
    }
  }, [procurado, procura])

  // Fecha ao clicar fora e com Esc.
  useEffect(() => {
    if (!aberto) return
    const fora = (e: MouseEvent) => {
      if (caixa.current && !caixa.current.contains(e.target as Node)) setAberto(false)
    }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setAberto(false)
    document.addEventListener('mousedown', fora)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', fora)
      document.removeEventListener('keydown', esc)
    }
  }, [aberto])

  function ir(para: string) {
    setAberto(false)
    setTexto('')
    navegar(para)
  }

  const nada = resultado !== null && resultado.clientes.length === 0 && resultado.ciclos.length === 0
  const item = 'flex w-full items-center justify-between gap-3 rounded-[8px] px-3 py-2 text-left hover:bg-paper/[0.06] focus:bg-paper/[0.06] focus:outline-none'

  return (
    <div ref={caixa} className="relative hidden md:block" role="search">
      <label className="relative block">
        <span className="sr-only">Buscar cliente</span>
        <IconSearch className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" width={17} height={17} />
        <input
          type="search"
          value={texto}
          onChange={(e) => {
            setTexto(e.target.value)
            setAberto(true)
          }}
          onFocus={() => setAberto(true)}
          placeholder="Buscar cliente"
          aria-label="Buscar cliente pelo nome ou pelo identificador"
          aria-expanded={aberto && procura}
          aria-controls="resultados-da-busca"
          className="h-10 w-56 rounded-[10px] border border-line bg-slate/60 pr-3 pl-9 text-apoio text-paper placeholder:text-muted focus:border-amber/60 focus:outline-none"
        />
      </label>

      {aberto && procura ? (
        <div
          id="resultados-da-busca"
          aria-label="Resultados da busca"
          aria-busy={buscando}
          className="scroll-fino absolute top-12 right-0 z-40 max-h-[70vh] w-[min(440px,90vw)] overflow-y-auto rounded-[12px] border border-line bg-card p-2 shadow-[0_24px_60px_-20px_rgba(0,0,0,0.8)]"
        >
          {erro ? (
            <p role="alert" className="px-3 py-2 text-apoio text-[#f5a29a]">
              {erro}
            </p>
          ) : resultado === null ? (
            <p className="px-3 py-2 text-apoio text-silver">Buscando…</p>
          ) : nada ? (
            <p className="px-3 py-2 text-apoio text-silver">Nada encontrado para “{procurado}”. A busca procura pelo nome ou pelo identificador.</p>
          ) : (
            <>
              {resultado.ciclos.length ? (
                <section aria-label="Cobranças">
                  <h3 className="t-label px-3 pt-1 pb-1 text-muted">Cobranças</h3>
                  <ul>
                    {resultado.ciclos.map((c) => (
                      <li key={`ciclo-${c.id}`}>
                        <button type="button" className={item} onClick={() => ir(`/involuntario?ciclo=${c.id}`)}>
                          <span className="min-w-0">
                            <span className="block truncate text-apoio font-[560] text-paper">{c.cliente ?? 'Cliente sem cadastro'}</span>
                            <span className="t-label block truncate text-muted">
                              {c.id_recorrencia} · {fmt.brl(c.valor_cobranca)}
                            </span>
                          </span>
                          <StatusPill status={c.status} />
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
              {resultado.clientes.length ? (
                <section aria-label="Clientes" className={resultado.ciclos.length ? 'mt-1 border-t border-line pt-1' : undefined}>
                  <h3 className="t-label px-3 pt-1 pb-1 text-muted">Clientes</h3>
                  <ul>
                    {resultado.clientes.map((c) => (
                      <li key={`cliente-${c.id}`}>
                        <button type="button" className={item} onClick={() => ir(`/voluntario?aba=clientes&cliente=${encodeURIComponent(c.id)}`)}>
                          <span className="min-w-0">
                            <span className="block truncate text-apoio font-[560] text-paper">{c.nome}</span>
                            <span className="t-label block truncate text-muted">
                              {c.id}
                              {c.mrr !== null ? ` · ${fmt.brl(c.mrr)} por mês` : ''}
                            </span>
                          </span>
                          <span className="flex shrink-0 items-center gap-1.5">
                            {c.cancelado ? <Badge>Cancelou</Badge> : null}
                            {c.nao_contatar ? <Badge tone="amber">Não contatar</Badge> : null}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </div>
  )
}

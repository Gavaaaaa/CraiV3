import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import { IconArrowRight, IconChat, IconSend, IconShield, IconUndo } from '../components/icons/Icons'
import { usePainel } from '../components/layout/Shell'
import { Badge } from '../components/ui/Badge'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Demonstracao } from '../components/ui/Demonstracao'
import { ErroApi, api } from '../data/api'
import { PERGUNTAS_PRONTAS } from '../data/assistente'
import type { RespostaAssistente } from '../data/tipos'
import { cx } from '../lib/cx'
import { useReducedMotion } from '../lib/useReducedMotion'

interface Mensagem {
  id: number
  de: 'voce' | 'assistente'
  texto: string
  links?: RespostaAssistente['links']
  sugestoes?: string[]
  origem?: RespostaAssistente['origem']
}

const LIMITE = 400

/** Um trecho de texto com **negrito** simples e parágrafos. */
function Texto({ texto }: { texto: string }) {
  return (
    <>
      {texto.split(/\n\n+/).map((p, i) => (
        <p key={i} className={cx('text-[14.5px] leading-[1.6]', i > 0 && 'mt-3')}>
          {p.split(/(\*\*[^*]+\*\*)/).map((parte, j) =>
            parte.startsWith('**') ? (
              <strong key={j} className="font-[620] text-paper">
                {parte.slice(2, -2)}
              </strong>
            ) : (
              <span key={j}>{parte}</span>
            ),
          )}
        </p>
      ))}
    </>
  )
}

export function Assistente() {
  const { empresa } = usePainel()
  const reduzido = useReducedMotion()
  const [mensagens, setMensagens] = useState<Mensagem[]>([])
  const [texto, setTexto] = useState('')
  const [pensando, setPensando] = useState(false)
  const fim = useRef<HTMLDivElement>(null)
  const campo = useRef<HTMLTextAreaElement>(null)
  const proximoId = useRef(1)

  useEffect(() => {
    fim.current?.scrollIntoView({ behavior: reduzido ? 'auto' : 'smooth', block: 'end' })
  }, [mensagens, pensando, reduzido])

  async function perguntar(pergunta: string) {
    const p = pergunta.trim().slice(0, LIMITE)
    if (!p || pensando) return
    setTexto('')
    setMensagens((m) => [...m, { id: proximoId.current++, de: 'voce', texto: p }])
    setPensando(true)
    try {
      const r = await api.assistente(p)
      setMensagens((m) => [...m, { id: proximoId.current++, de: 'assistente', texto: r.texto, links: r.links, sugestoes: r.sugestoes, origem: r.origem }])
    } catch (e) {
      const texto = e instanceof ErroApi ? e.message : 'Algo deu errado.'
      setMensagens((m) => [
        ...m,
        {
          id: proximoId.current++,
          de: 'assistente',
          texto: `${texto} Não consegui responder agora. Os números continuam certos nas páginas do painel; tente de novo em instantes.`,
          links: [{ rotulo: 'Ir para a visão geral', para: '/' }],
          sugestoes: [p],
          origem: 'texto_fixo',
        },
      ])
    }
    setPensando(false)
    campo.current?.focus()
  }

  function enviar(e: FormEvent) {
    e.preventDefault()
    void perguntar(texto)
  }
  function teclado(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void perguntar(texto)
    }
  }

  const ultima = mensagens[mensagens.length - 1]
  const sugestoes = mensagens.length === 0 ? PERGUNTAS_PRONTAS : (ultima?.de === 'assistente' ? ultima.sugestoes ?? [] : [])
  const vazio = mensagens.length === 0

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="t-h2 flex items-center gap-2 text-paper">
            Assistente <Demonstracao de={['assistente']} />
          </h2>
          <p className="t-apoio mt-1 text-silver">Perguntas sobre os seus números e sobre como o sistema funciona, em português claro.</p>
        </div>
        {mensagens.length ? (
          <Button variant="quiet" size="sm" onClick={() => setMensagens([])}>
            <IconUndo width={15} height={15} /> Limpar conversa
          </Button>
        ) : null}
      </div>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        {/* A conversa */}
        <Card className="flex min-h-[560px] flex-col p-0">
          <div className="scroll-fino flex-1 overflow-y-auto px-5 pt-5 pb-2 md:px-6" aria-live="polite" aria-label="Conversa">
            {vazio ? (
              <div className="flex h-full min-h-[300px] flex-col items-center justify-center text-center">
                <span className="flex h-12 w-12 items-center justify-center rounded-[14px] bg-paper/[0.06] text-amber">
                  <IconChat width={24} height={24} />
                </span>
                <div className="t-h3 mt-4 text-paper">Olá{empresa ? `, ${empresa.nome}` : ''}. O que você quer saber?</div>
                <p className="t-apoio mt-1 max-w-md text-silver">
                  Eu leio os mesmos números que aparecem no painel e explico como o sistema decide. Comece por uma das perguntas abaixo ou escreva a sua.
                </p>
              </div>
            ) : (
              <ol className="flex flex-col gap-4">
                <AnimatePresence initial={false}>
                  {mensagens.map((m) => (
                    <motion.li
                      key={m.id}
                      initial={reduzido ? false : { opacity: 0, y: 8 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                      className={cx('flex', m.de === 'voce' ? 'justify-end' : 'justify-start')}
                    >
                      {m.de === 'voce' ? (
                        <div className="max-w-[85%] rounded-[16px] rounded-br-[6px] bg-paper px-4 py-2.5 text-[14.5px] leading-[1.55] text-ink">
                          <span className="sr-only">Você: </span>
                          {m.texto}
                        </div>
                      ) : (
                        <div className="flex max-w-[92%] gap-3">
                          <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-[9px] bg-orange/15 text-orange" aria-hidden="true">
                            <IconChat width={15} height={15} />
                          </span>
                          <div className="min-w-0">
                            <span className="sr-only">Assistente: </span>
                            <div className={cx('rounded-[16px] rounded-tl-[6px] border px-4 py-3 text-paper/90', m.origem === 'texto_fixo' ? 'border-warn/40 bg-warn/[0.06]' : 'border-line bg-ink/30')}>
                              {m.origem === 'texto_fixo' ? <Badge tone="warn" className="mb-2">Resposta fixa</Badge> : null}
                              <Texto texto={m.texto} />
                              {m.links?.length ? (
                                <div className="mt-3 flex flex-wrap gap-2">
                                  {m.links.map((l) => (
                                    <Link
                                      key={l.para}
                                      to={l.para}
                                      className="inline-flex items-center gap-1.5 rounded-[8px] border border-line px-2.5 py-1 text-[12.5px] font-[560] text-silver transition-colors hover:border-graphite hover:text-paper"
                                    >
                                      {l.rotulo} <IconArrowRight width={13} height={13} />
                                    </Link>
                                  ))}
                                </div>
                              ) : null}
                            </div>
                          </div>
                        </div>
                      )}
                    </motion.li>
                  ))}
                </AnimatePresence>
                {pensando ? (
                  <li className="flex gap-3" aria-label="O assistente está escrevendo">
                    <span className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-[9px] bg-orange/15 text-orange" aria-hidden="true">
                      <IconChat width={15} height={15} />
                    </span>
                    <div className="flex items-center gap-1.5 rounded-[16px] rounded-tl-[6px] border border-line bg-ink/30 px-4 py-3.5">
                      {[0, 1, 2].map((i) => (
                        <span
                          key={i}
                          className={cx('h-1.5 w-1.5 rounded-full bg-silver', !reduzido && 'animate-bounce')}
                          style={{ animationDelay: `${i * 140}ms` }}
                          aria-hidden="true"
                        />
                      ))}
                      <span className="sr-only">Escrevendo…</span>
                    </div>
                  </li>
                ) : null}
              </ol>
            )}
            <div ref={fim} />
          </div>

          {/* Sugestões e campo */}
          <div className="border-t border-line px-5 py-4 md:px-6">
            {sugestoes.length ? (
              <div className="mb-3 flex flex-wrap gap-2" aria-label="Perguntas prontas">
                {sugestoes.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => void perguntar(s)}
                    disabled={pensando}
                    className="rounded-full border border-line bg-ink/25 px-3 py-1.5 text-[13px] font-[520] text-paper transition-colors hover:border-amber/60 hover:bg-amber/[0.06] disabled:opacity-50"
                  >
                    {s}
                  </button>
                ))}
              </div>
            ) : null}
            <form onSubmit={enviar} className="flex items-end gap-2">
              <label className="flex-1">
                <span className="sr-only">Sua pergunta</span>
                <textarea
                  ref={campo}
                  value={texto}
                  onChange={(e) => setTexto(e.target.value.slice(0, LIMITE))}
                  onKeyDown={teclado}
                  rows={1}
                  placeholder="Escreva a sua pergunta"
                  className="scroll-fino max-h-32 min-h-[44px] w-full resize-none rounded-[12px] border border-line bg-ink/40 px-4 py-2.5 text-[14.5px] text-paper placeholder:text-muted focus:border-amber/60 focus:outline-none"
                />
              </label>
              <Button type="submit" size="md" disabled={!texto.trim() || pensando} aria-label="Enviar pergunta">
                <IconSend width={16} height={16} />
                <span className="hidden sm:inline">Enviar</span>
              </Button>
            </form>
            <div className="t-label mt-2 flex flex-wrap items-center justify-between gap-2 text-muted">
              <span>Enter envia; Shift+Enter quebra a linha.</span>
              <span className="tabular">
                {texto.length}/{LIMITE}
              </span>
            </div>
          </div>
        </Card>

        {/* O que o assistente vê e não vê */}
        <aside className="flex flex-col gap-4">
          <Card className="p-5">
            <div className="flex items-center gap-2">
              <span className="flex h-8 w-8 items-center justify-center rounded-[10px] bg-ok/15 text-ok">
                <IconShield width={16} height={16} />
              </span>
              <h3 className="t-h3 text-paper">O que o assistente vê</h3>
            </div>
            <ul className="mt-4 flex flex-col gap-2.5 text-[13.5px] leading-[1.5]">
              <Item ok>Os mesmos números que o painel mostra: valores, contagens, faixas de risco, funil.</Item>
              <Item ok>Como o sistema funciona: tentativas, mensagens, ofertas, taxa, prazos.</Item>
              <Item>E-mail, telefone, CPF ou chave Pix dos seus clientes. Nunca.</Item>
              <Item>O texto das mensagens enviadas.</Item>
              <Item>Esta conversa depois que você sair: nada é guardado.</Item>
            </ul>
          </Card>
          <Card className="p-5">
            <h3 className="t-h3 text-paper">Ele não muda nada</h3>
            <p className="t-apoio mt-2 text-silver">
              O assistente só lê e explica. Para escolher uma mensagem, mudar o modo de envio ou anexar a base, use as páginas do painel; ele indica o caminho.
            </p>
          </Card>
        </aside>
      </div>
    </div>
  )
}

function Item({ ok, children }: { ok?: boolean; children: React.ReactNode }) {
  return (
    <li className="flex gap-2.5">
      <span className={cx('mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full', ok ? 'bg-ok' : 'bg-danger')} aria-hidden="true" />
      <span className={ok ? 'text-paper' : 'text-silver'}>
        <span className="sr-only">{ok ? 'Vê: ' : 'Não vê: '}</span>
        {children}
      </span>
    </li>
  )
}

import { motion } from 'framer-motion'
import { useCallback, useEffect, useRef, useState } from 'react'
import { IconCheck, IconClose, IconRefresh } from '../../components/icons/Icons'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { ErroCarregar } from '../../components/ui/Estados'
import { StatusPill } from '../../components/ui/StatusPill'
import { ErroApi, MODO_REAL, agoraDaTela, api } from '../../data/api'
import { CAUSA_LEGIVEL } from '../../data/mock'
import type { Abordagem, CicloDetalhe, Configuracao, EventoLinhaDoTempo, Sugestao, SugestaoDoCiclo } from '../../data/tipos'
import { cx } from '../../lib/cx'
import { fmt } from '../../lib/format'
import { ATUALIZAR_CICLO_ABERTO_MS, mesmoConteudo, useAtualizarACada } from '../../lib/useAtualizarACada'
import { useReducedMotion } from '../../lib/useReducedMotion'

export const ABORDAGEM: Record<Abordagem, string> = {
  lembrete_cordial: 'Lembrete cordial',
  facilitacao: 'Facilitação',
  urgencia_com_respeito: 'Urgência com respeito',
}

export const CANAL: Record<Sugestao['canal'], string> = {
  whatsapp: 'WhatsApp',
  email: 'E-mail',
  sms: 'SMS',
  sem_canal: 'Sem canal disponível',
}

const TOM: Record<NonNullable<EventoLinhaDoTempo['tom']>, string> = {
  ok: 'bg-ok',
  warn: 'bg-warn',
  danger: 'bg-danger',
  neutro: 'bg-graphite',
}

/** "Envio automático em 6 h 40 min", calculado do prazo que o backend manda (`escolha_ate`). */
export function textoDoPrazo(escolhaAte: string, agora: Date): string {
  const minutos = Math.floor((new Date(escolhaAte).getTime() - agora.getTime()) / 60_000)
  if (minutos <= 0) return 'Envio automático a qualquer momento'
  const h = Math.floor(minutos / 60)
  const m = minutos % 60
  if (h === 0) return `Envio automático em ${m} min`
  return m === 0 ? `Envio automático em ${h} h` : `Envio automático em ${h} h ${m} min`
}

/**
 * Painel lateral com a linha do tempo de um ciclo. Com `irParaEscolha` (a aba Mensagens), o
 * painel abre já nas mensagens sugeridas: a seção delas é rolada para a vista e o primeiro
 * botão "Enviar esta" recebe o foco.
 */
export function CicloDrawer({ id, onClose, irParaEscolha = false }: { id: number; onClose: () => void; irParaEscolha?: boolean }) {
  const [ciclo, setCiclo] = useState<CicloDetalhe | null>(null)
  const [config, setConfig] = useState<Configuracao | null>(null)
  const [sugestoes, setSugestoes] = useState<SugestaoDoCiclo[]>([])
  const [regerando, setRegerando] = useState(false)
  const [enviando, setEnviando] = useState(false)
  const [erroAcao, setErroAcao] = useState<string | null>(null)
  const reduced = useReducedMotion()

  // A rodada na tela é a mais recente; as anteriores ficam registradas na linha do tempo.
  const rodada = Math.max(1, ...sugestoes.map((s) => s.rodada))
  const daRodada = sugestoes.filter((s) => s.rodada === rodada)
  const escolhida = sugestoes.find((s) => s.escolhida) ?? null
  const modoEscolha = (ciclo?.modo_mensagem ?? config?.modo_mensagem_involuntario) === 'escolha'
  const aguardando = ciclo?.estado === 'aguardando_escolha' && !escolhida
  const ocupado = regerando || enviando

  const [erro, setErro] = useState<string | null>(null)
  // A consulta da vez: a resposta de uma consulta silenciosa que chega depois de outra carga
  // (a de "Enviar esta", por exemplo) é descartada, para não pôr na tela um estado mais velho.
  const consulta = useRef(0)
  const carregar = useCallback(
    () => {
      consulta.current += 1
      return api
        .ciclo(id)
        .then((c) => {
          if (c === null) {
            setErro('Não encontramos este ciclo.')
            return
          }
          setCiclo(c)
          setSugestoes(c.sugestoes)
        })
        .catch((e: unknown) => setErro(e instanceof ErroApi ? e.message : 'Não deu para abrir este ciclo.'))
    },
    [id],
  )
  useEffect(() => {
    void carregar()
    api.configuracao().then(setConfig).catch(() => setConfig(null))
  }, [carregar])

  // Com o painel aberto, o ciclo é consultado de novo a cada 5 segundos, em silêncio: sem
  // "Carregando", sem redesenhar se nada mudou, e sem trocar a tela por erro se a consulta
  // falhar. Enquanto a pessoa envia ou pede outras mensagens, a consulta espera.
  const ocupadoAgora = useRef(false)
  ocupadoAgora.current = regerando || enviando
  useAtualizarACada(
    () => {
      if (ocupadoAgora.current || erro !== null || ciclo === null) return
      const pedida = (consulta.current += 1)
      return api.ciclo(id).then((c) => {
        if (c === null || pedida !== consulta.current || ocupadoAgora.current) return
        // Resposta igual não mexe no estado: nada é redesenhado.
        if (!mesmoConteudo(ciclo, c)) setCiclo(c)
        if (!mesmoConteudo(sugestoes, c.sugestoes)) setSugestoes(c.sugestoes)
      })
    },
    ATUALIZAR_CICLO_ABERTO_MS,
    MODO_REAL,
  )

  async function regerar() {
    setRegerando(true)
    setErroAcao(null)
    try {
      const novas = await api.regerarSugestoes(id)
      setSugestoes((atuais) => [...atuais, ...novas])
    } catch (e) {
      setErroAcao(e instanceof ErroApi ? e.message : 'Não deu para gerar outras mensagens.')
    }
    setRegerando(false)
  }

  async function escolher(s: SugestaoDoCiclo) {
    setEnviando(true)
    setErroAcao(null)
    try {
      await api.escolherMensagem(id, s.rodada, s.abordagem)
      await carregar() // o painel recarrega: estado, escolhida e linha do tempo vêm do servidor
    } catch (e) {
      setErroAcao(e instanceof ErroApi ? e.message : 'Não deu para enviar esta mensagem.')
    }
    setEnviando(false)
  }

  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onClose])

  // Aberto pela aba Mensagens: assim que o ciclo chega, a escolha fica à vista (uma vez só).
  const secaoDaEscolha = useRef<HTMLElement>(null)
  const jaFoi = useRef(false)
  useEffect(() => {
    if (!irParaEscolha || jaFoi.current || ciclo === null || !secaoDaEscolha.current) return
    jaFoi.current = true
    secaoDaEscolha.current.scrollIntoView({ block: 'start', behavior: reduced ? 'auto' : 'smooth' })
    secaoDaEscolha.current.querySelector<HTMLButtonElement>('button:not([disabled])')?.focus({ preventScroll: true })
  }, [irParaEscolha, ciclo, reduced])

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label="Detalhe do ciclo">
      <button type="button" aria-label="Fechar" onClick={onClose} className="absolute inset-0 bg-sombra/60 backdrop-blur-[2px]" />
      <motion.aside
        initial={reduced ? false : { x: 40, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
        className="scroll-fino absolute inset-y-0 right-0 w-full max-w-[560px] overflow-y-auto border-l border-line bg-card shadow-painel"
      >
        {erro ? (
          <div className="p-6">
            <button type="button" onClick={onClose} aria-label="Fechar" className="mb-4 rounded-[8px] p-2 text-silver hover:bg-paper/[0.06] hover:text-paper">
              <IconClose />
            </button>
            <ErroCarregar mensagem={erro} onTentar={onClose} />
          </div>
        ) : ciclo === null ? (
          <div className="p-6 text-silver">Carregando…</div>
        ) : (
          <div className="flex flex-col gap-6 p-6">
            <header className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="t-h2 text-paper">{ciclo.cliente ?? 'Cliente sem cadastro'}</h2>
                  {ciclo.simulado ? <Badge tone="amber">Demonstração</Badge> : null}
                </div>
                <div className="t-apoio mt-1 text-silver">
                  {fmt.brl(ciclo.valor_cobranca)} · {ciclo.causa_legivel ?? CAUSA_LEGIVEL[ciclo.causa]} · <span className="text-muted">{ciclo.id_recorrencia}</span>
                </div>
                <div className="mt-3"><StatusPill status={ciclo.status} /></div>
              </div>
              <button type="button" onClick={onClose} aria-label="Fechar" className="rounded-[8px] p-2 text-silver hover:bg-paper/[0.06] hover:text-paper">
                <IconClose />
              </button>
            </header>

            {/* Diagnóstico em linguagem simples (as contribuições do SHAP) */}
            <section className="rounded-[14px] border border-line bg-ink/40 p-4">
              <div className="flex items-baseline justify-between">
                <h3 className="t-h3 text-paper">Por que o sistema agiu assim</h3>
                <span className="t-number text-orange">{ciclo.chance_recuperar !== null ? `${Math.round(ciclo.chance_recuperar * 100)}%` : '—'}</span>
              </div>
              <p className="t-apoio mt-1 text-silver">
                {ciclo.chance_recuperar !== null ? 'Chance de recuperar, estimada na abertura do ciclo' : 'Este ciclo não tem estimativa de recuperação registrada'}
              </p>
              {ciclo.chance_recuperar !== null && ciclo.desconto_anomalia_pct !== null ? (
                <p className="t-label mt-1 text-muted">Já com o desconto de {ciclo.desconto_anomalia_pct}% por comportamento fora do padrão.</p>
              ) : null}
              {ciclo.contribuicoes.length ? (
                <ul className="mt-3 flex flex-col gap-1.5">
                  {ciclo.contribuicoes.map((c, i) => (
                    <li key={`${c.fator}-${i}`} className="flex items-center justify-between gap-3 text-apoio">
                      <span className="text-paper">{c.fator}</span>
                      {c.efeito ? (
                        <span className={cx('text-right font-[560]', c.efeito.startsWith('Aumentou') ? 'text-ok' : c.efeito.startsWith('Reduziu') ? 'text-danger-texto' : 'text-silver')}>
                          {c.efeito}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="t-label mt-3 text-muted">Sem fatores registrados: o diagnóstico deste ciclo foi feito por regra fixa.</p>
              )}
            </section>

            {/* As 3 sugestões: enquanto o ciclo espera a escolha, e depois, para ver qual saiu */}
            {daRodada.length ? (
              <section ref={secaoDaEscolha} data-secao="escolha" className="scroll-mt-6">
                <div className="flex items-baseline justify-between gap-3">
                  <h3 className="t-h3 text-paper">Mensagens sugeridas{rodada > 1 ? <span className="t-label ml-2 text-silver">{rodada}ª rodada</span> : null}</h3>
                  {aguardando && ciclo.escolha_ate ? <span className="t-label text-warn">{textoDoPrazo(ciclo.escolha_ate, agoraDaTela())}</span> : null}
                </div>
                <p className="t-apoio mt-1 text-silver">
                  {escolhida
                    ? 'A mensagem marcada como escolhida é a que foi enviada, ou a que sai quando houver canal e horário permitido.'
                    : modoEscolha
                      ? 'Escolha uma ou peça outras três. Sem escolha no prazo, a recomendada é enviada.'
                      : 'A recomendada será enviada automaticamente. Para escolher, mude o modo na configuração.'}
                </p>
                {erroAcao ? (
                  <p role="alert" className="mt-3 rounded-[10px] border border-danger/40 bg-danger/[0.06] px-3 py-2 text-apoio text-danger-aviso">
                    {erroAcao}
                  </p>
                ) : null}
                <ul className={cx('mt-3 flex flex-col gap-3 transition-opacity', ocupado && 'opacity-40')} aria-busy={ocupado}>
                  {daRodada.map((s) => (
                    <li
                      key={`${s.abordagem}-${s.rodada}`}
                      className={cx(
                        'rounded-[14px] border p-4',
                        s.recomendada ? 'border-orange/50 bg-orange/[0.06]' : 'border-line bg-ink/30',
                      )}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-[600] text-paper">{ABORDAGEM[s.abordagem]}</span>
                        {s.recomendada ? <Badge tone="orange">Recomendada</Badge> : null}
                        {s.escolhida ? <Badge tone="ok">Escolhida</Badge> : null}
                        <Badge tone="neutral" className="ml-auto">{CANAL[s.canal]}</Badge>
                      </div>
                      <p className="t-apoio mt-2 text-paper/90">{s.texto ?? 'O texto desta mensagem foi apagado depois do prazo de guarda (90 dias depois do desfecho).'}</p>
                      <div className="mt-3 flex items-center justify-between gap-3">
                        <span className="t-label text-muted">{s.motivo_canal}</span>
                        {modoEscolha && aguardando ? (
                          <Button size="sm" variant={s.recomendada ? 'primary' : 'ghost'} disabled={ocupado} onClick={() => escolher(s)}>
                            <IconCheck width={15} height={15} /> Enviar esta
                          </Button>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>

                {modoEscolha && aguardando ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button size="sm" variant="ghost" onClick={regerar} disabled={ocupado}>
                      <IconRefresh width={15} height={15} className={regerando ? 'animate-spin' : undefined} />
                      {regerando ? 'Gerando outras três…' : 'Quero outro tipo de mensagem'}
                    </Button>
                  </div>
                ) : null}

              </section>
            ) : null}

            {/* Linha do tempo */}
            <section>
              <h3 className="t-h3 text-paper">Linha do tempo</h3>
              <ol className="relative mt-4 ml-2 flex flex-col gap-5 border-l border-line pl-6">
                {ciclo.linha_do_tempo.map((e, i) => (
                  <li key={i} className="relative">
                    <span aria-hidden="true" className={cx('absolute top-1.5 -left-[31px] h-2.5 w-2.5 rounded-full ring-4 ring-card', TOM[e.tom ?? 'neutro'])} />
                    <div className="t-label text-muted">{fmt.dataHora(e.em)}</div>
                    <div className="font-[560] text-paper">{e.titulo}</div>
                    {e.detalhe ? <div className="t-apoio text-silver">{e.detalhe}</div> : null}
                  </li>
                ))}
              </ol>
            </section>

            {ciclo.valor_liquido !== null ? (
              <section className="rounded-[14px] border border-ok/30 bg-ok/[0.06] p-4">
                <div className="t-label text-ok">Recuperado</div>
                <div className="mt-1 flex items-baseline justify-between">
                  <span className="text-paper">Líquido para você</span>
                  <span className="t-number tabular text-paper">{fmt.brl(ciclo.valor_liquido)}</span>
                </div>
                <div className="t-label mt-1 text-silver">Cobrança de {fmt.brl(ciclo.valor_cobranca)}, já descontada a taxa da CRAI.</div>
              </section>
            ) : null}
            {ciclo.motivo_descarte ? (
              <section className="rounded-[14px] border border-line bg-ink/40 p-4">
                <div className="t-label text-silver">Encerrado sem ação</div>
                <div className="mt-1 text-paper">{ciclo.motivo_descarte}</div>
              </section>
            ) : null}
          </div>
        )}
      </motion.aside>
    </div>
  )
}

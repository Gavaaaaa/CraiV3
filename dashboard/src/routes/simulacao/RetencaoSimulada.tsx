import { AnimatePresence, motion } from 'framer-motion'
import { useState, type FormEvent } from 'react'
import { IconRefresh, IconUndo, IconUsers } from '../../components/icons/Icons'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { api } from '../../data/api'
import { OFERTA } from '../../data/simulador'
import type { ClienteRiscoFicticio, OfertaRetencao, ResultadoRetencaoSimulada } from '../../data/tipos'
import { cx } from '../../lib/cx'
import { fmt } from '../../lib/format'
import { useReducedMotion } from '../../lib/useReducedMotion'
import { pareceDadoReal } from './FormularioCliente'

const NOMES = ['Loja Ponto Certo', 'Café Aroma', 'Studio Pilates Move', 'Clínica Bem Viver', 'Escola de Idiomas Fala', 'Oficina do Bairro', 'Pet Shop Amigo']

const FAIXA = {
  grave: { rotulo: 'Grave', classe: 'border-danger/50 text-[#f08a80]', ponto: 'bg-danger' },
  preocupante: { rotulo: 'Preocupante', classe: 'border-warn/50 text-warn', ponto: 'bg-warn' },
  sem_risco: { rotulo: 'Sem risco', classe: 'border-ok/45 text-ok', ponto: 'bg-ok' },
}

const OFERTAS: OfertaRetencao[] = ['desconto', 'suporte', 'plano_leve']
const CURTO: Record<OfertaRetencao, string> = { desconto: 'Desconto', suporte: 'Suporte', plano_leve: 'Plano leve' }

/** Aba do voluntário: cria um cliente fictício em risco; o aceite vem da propensão escondida. */
export function RetencaoSimulada() {
  const [nome, setNome] = useState('Loja Ponto Certo')
  const [mrr, setMrr] = useState('1200')
  const [sinais, setSinais] = useState({ uso_caiu: true, tickets: false, atraso: true })
  const [propensao, setPropensao] = useState<Record<OfertaRetencao, number>>({ desconto: 70, suporte: 30, plano_leve: 45 })
  const [erro, setErro] = useState<string | null>(null)
  const [ocupado, setOcupado] = useState(false)
  const [cliente, setCliente] = useState<ClienteRiscoFicticio | null>(null)
  const [resultado, setResultado] = useState<ResultadoRetencaoSimulada | null>(null)
  const reduzido = useReducedMotion()

  async function criar(e: FormEvent) {
    e.preventDefault()
    const n = nome.trim()
    if (n.length < 3) return setErro('Dê um nome ao cliente fictício.')
    const suspeito = pareceDadoReal(n)
    if (suspeito) return setErro(suspeito)
    const valor = Number(mrr.replace(',', '.'))
    if (!Number.isFinite(valor) || valor < 10 || valor > 100_000) return setErro('A mensalidade precisa ficar entre R$ 10 e R$ 100.000.')
    setErro(null)
    const c: ClienteRiscoFicticio = {
      nome: n,
      mrr: valor,
      sinais,
      propensao: { desconto: propensao.desconto / 100, suporte: propensao.suporte / 100, plano_leve: propensao.plano_leve / 100 },
    }
    setCliente(c)
    setResultado(null)
    setOcupado(true)
    const r = await api.simularRetencao(c)
    setResultado(r)
    setOcupado(false)
  }

  function recomecar() {
    setCliente(null)
    setResultado(null)
  }

  const campo = 'h-10 w-full rounded-[10px] border border-line bg-ink/40 px-3 text-[14px] text-paper placeholder:text-muted focus:border-amber/60 focus:outline-none'
  const faixa = resultado ? FAIXA[resultado.faixa] : null

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      {/* Esquerda: o "cartão" do cliente em risco */}
      <div className="flex flex-col items-center gap-4">
        <motion.div
          layout
          className="w-full max-w-[460px] rounded-[18px] border border-line bg-[linear-gradient(150deg,#332a20_0%,#231b13_55%,#1a120a_100%)] p-6"
          animate={{
            boxShadow: ocupado && !reduzido
              ? ['0 0 0 0 rgba(31,158,138,0)', '0 0 80px 12px rgba(31,158,138,0.45)', '0 0 30px 2px rgba(31,158,138,0.15)']
              : resultado
                ? resultado.aceitou
                  ? '0 0 90px 10px rgba(63,178,111,0.4)'
                  : resultado.faixa === 'sem_risco'
                    ? '0 30px 70px -30px rgba(0,0,0,0.8)'
                    : '0 0 90px 10px rgba(194,69,58,0.35)'
                : '0 30px 70px -30px rgba(0,0,0,0.8)',
          }}
          transition={ocupado && !reduzido ? { duration: 1.4, repeat: Infinity, ease: 'easeInOut' } : { duration: 0.5 }}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="t-label tracking-[0.08em] text-silver uppercase">Cliente em risco</div>
              <div className="mt-0.5 text-[12px] text-muted">Churn voluntário · demonstração</div>
            </div>
            <Badge tone="amber">Dados fictícios</Badge>
          </div>
          <div className="mt-6 flex items-center gap-3">
            <span className="flex h-11 w-11 items-center justify-center rounded-[12px] bg-paper/[0.06] text-amber">
              <IconUsers width={20} height={20} />
            </span>
            <div className="min-w-0">
              <div className="truncate text-[18px] font-[640] text-paper">{cliente?.nome ?? nome ?? 'Nome fictício'}</div>
              <div className="t-label text-silver">Mensalidade {fmt.brl(cliente?.mrr ?? (Number(mrr) || 0))}</div>
            </div>
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-2">
            {faixa ? (
              <span className={cx('inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-[12.5px] font-[520]', faixa.classe)}>
                <span className={cx('h-1.5 w-1.5 rounded-full', faixa.ponto)} aria-hidden="true" />
                {faixa.rotulo}
              </span>
            ) : (
              <Badge>{ocupado ? 'Avaliando o risco…' : 'Ainda sem avaliação'}</Badge>
            )}
            {resultado ? <span className="t-label text-silver">Decidido pela régua (sem dados de comportamento ainda)</span> : null}
          </div>
          {resultado ? <p className="t-apoio mt-3 text-paper/90">{resultado.motivo}</p> : null}
        </motion.div>

        <AnimatePresence>
          {resultado && resultado.faixa !== 'sem_risco' ? (
            <motion.div
              key="resultado"
              initial={reduzido ? false : { opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              className={cx('w-full max-w-[460px] rounded-[14px] border p-4', resultado.aceitou ? 'border-ok/40 bg-ok/[0.06]' : 'border-danger/40 bg-danger/[0.06]')}
            >
              <div className="t-label text-silver">O que o sistema fez</div>
              <div className="mt-1 text-[15px] font-[600] text-paper">
                {OFERTA[resultado.oferta]} · por WhatsApp
              </div>
              <p className="t-apoio mt-1.5 text-silver">{resultado.porque}</p>
              <div className="mt-3 flex items-baseline justify-between gap-3 border-t border-line pt-3">
                <span className={cx('text-[15px] font-[640]', resultado.aceitou ? 'text-ok' : 'text-[#f08a80]')}>
                  {resultado.aceitou ? 'Cliente aceitou a oferta' : 'Cliente recusou a oferta'}
                </span>
                {resultado.aceitou ? <span className="tabular text-[17px] font-[680] text-paper">{fmt.brl(resultado.valor_mantido_liquido)} mantidos</span> : null}
              </div>
              <p className="t-label mt-2 text-muted">
                {resultado.aceitou
                  ? '1 mês da mensalidade, menos o desconto dado, líquido da taxa. Se cancelar em 30 dias, o valor é estornado.'
                  : 'Sem aceite, nada é cobrado. O sistema aprende com a recusa e tenta outra abordagem no próximo ciclo.'}
              </p>
            </motion.div>
          ) : null}
        </AnimatePresence>
        {resultado ? (
          <div className="w-full max-w-[460px] rounded-[14px] border border-line bg-ink/30 p-4">
            <div className="t-label text-silver">Sem a CRAI</div>
            <p className="t-apoio mt-1 text-paper/90">{resultado.sem_crai}</p>
          </div>
        ) : null}
      </div>

      {/* Direita: o formulário ou o resultado */}
      <div className="card-glass rounded-[18px] p-5 md:p-6">
        {resultado ? (
          <div className="flex flex-col gap-4">
            <div>
              <h3 className="t-h3 text-paper">Como o sistema decidiu</h3>
              <p className="t-apoio mt-1 text-silver">Só com o que ele enxerga: os sinais de comportamento e o valor. A propensão escondida fica fora.</p>
            </div>
            <ol className="flex flex-col gap-2 text-[13.5px] leading-[1.5]">
              <li className="flex gap-2.5 text-silver"><span className="tabular text-[11px] text-muted">01</span>Faixa de risco pela régua: {faixa?.rotulo.toLowerCase()}. {resultado.motivo}</li>
              <li className="flex gap-2.5 text-silver"><span className="tabular text-[11px] text-muted">02</span>{resultado.faixa === 'sem_risco' ? 'Sem risco: nenhuma oferta. Oferecer desconto a quem não ia sair só custa margem.' : `Oferta escolhida: ${OFERTA[resultado.oferta].toLowerCase()}. ${resultado.porque}`}</li>
              <li className="flex gap-2.5 text-paper"><span className="tabular text-[11px] text-muted">03</span>{resultado.faixa === 'sem_risco' ? 'O cliente continua monitorado.' : resultado.aceitou ? 'A resposta veio da propensão escondida: aceitou. O valor entra no "Dinheiro mantido".' : 'A resposta veio da propensão escondida: recusou. O sistema registra e ajusta a próxima escolha.'}</li>
            </ol>
            {cliente ? (
              <div className="rounded-[12px] border border-dashed border-amber/40 bg-amber/[0.04] p-3.5">
                <div className="t-label text-amber">Propensão escondida deste cliente</div>
                <ul className="mt-2 grid grid-cols-3 gap-2">
                  {OFERTAS.map((o) => (
                    <li key={o} className={cx('rounded-[8px] border px-2.5 py-2', o === resultado.oferta ? 'border-amber/50 bg-amber/[0.08]' : 'border-line')}>
                      <div className="tabular text-[16px] font-[640] text-paper">{Math.round(cliente.propensao[o] * 100)}%</div>
                      <div className="t-label text-silver">{CURTO[o]}</div>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            <Button size="sm" variant="ghost" onClick={recomecar} className="self-start">
              <IconUndo width={15} height={15} /> Criar outro cliente
            </Button>
          </div>
        ) : (
          <form onSubmit={criar} className="flex flex-col gap-5" aria-label="Cliente fictício em risco">
            <div>
              <h3 className="t-h3 text-paper">Criar cliente fictício em risco</h3>
              <p className="t-apoio mt-1 text-silver">Escolha os sinais que o sistema vê e, escondida dele, a chance de o cliente aceitar cada oferta.</p>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="block sm:col-span-2">
                <span className="t-label text-silver">Nome inventado</span>
                <div className="mt-1.5 flex gap-2">
                  <input value={nome} onChange={(e) => { setNome(e.target.value); setErro(null) }} className={campo} maxLength={40} autoComplete="off" />
                  <Button type="button" variant="ghost" onClick={() => setNome(NOMES.filter((n) => n !== nome)[Math.floor(Math.random() * (NOMES.length - 1))])} aria-label="Sortear um nome">
                    <IconRefresh width={16} height={16} />
                  </Button>
                </div>
              </label>
              <label className="block">
                <span className="t-label text-silver">Mensalidade (R$)</span>
                <input value={mrr} onChange={(e) => setMrr(e.target.value)} inputMode="decimal" className={cx(campo, 'tabular mt-1.5')} />
              </label>
            </div>
            <fieldset>
              <legend className="t-label text-silver">Sinais que o sistema vê</legend>
              <div className="mt-2 flex flex-col gap-2">
                {(
                  [
                    ['uso_caiu', 'O uso caiu pela metade em 2 semanas'],
                    ['tickets', 'Abriu 3 chamados de suporte no mês'],
                    ['atraso', 'Pagou atrasado 2 vezes'],
                  ] as const
                ).map(([k, r]) => (
                  <label key={k} className="flex cursor-pointer items-center gap-3 rounded-[10px] border border-line bg-ink/30 px-3 py-2.5 text-[13.5px] text-paper has-[:checked]:border-orange/50">
                    <input type="checkbox" checked={sinais[k]} onChange={(e) => setSinais({ ...sinais, [k]: e.target.checked })} className="h-4 w-4 accent-[#ef9311]" />
                    {r}
                  </label>
                ))}
              </div>
            </fieldset>
            <fieldset className="rounded-[14px] border border-dashed border-amber/40 bg-amber/[0.04] p-4">
              <legend className="t-label px-1.5 text-amber">Propensão escondida · o sistema não vê</legend>
              <p className="t-apoio text-silver">Chance de o cliente aceitar cada oferta, se ela for feita.</p>
              <div className="mt-3 flex flex-col gap-3">
                {OFERTAS.map((o) => (
                  <label key={o} className="block">
                    <span className="flex items-baseline justify-between gap-3">
                      <span className="text-[13.5px] text-paper">{OFERTA[o]}</span>
                      <span className="tabular text-[13.5px] font-[600] text-amber">{propensao[o]}%</span>
                    </span>
                    <input type="range" min={0} max={100} step={5} value={propensao[o]} onChange={(e) => setPropensao({ ...propensao, [o]: Number(e.target.value) })} className="mt-1.5 w-full accent-[#ef9311]" />
                  </label>
                ))}
              </div>
            </fieldset>
            {erro ? <p role="alert" className="rounded-[10px] border border-danger/50 bg-danger/10 px-3 py-2 text-[13.5px] text-[#f5a29a]">{erro}</p> : null}
            <Button type="submit" disabled={ocupado} className="w-full">
              {ocupado ? 'Avaliando…' : 'Criar cliente em risco'}
            </Button>
            <div className="flex flex-wrap items-center justify-center gap-2">
              <Badge tone="amber">Demonstração</Badge>
              <Badge>Dados fictícios</Badge>
              <span className="t-label text-muted">Nenhuma mensagem é enviada de verdade.</span>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

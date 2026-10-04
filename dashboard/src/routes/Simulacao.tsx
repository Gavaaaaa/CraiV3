import { AnimatePresence, LayoutGroup, motion } from 'framer-motion'
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { IconAlert, IconCheck, IconSend } from '../components/icons/Icons'
import { usePainel } from '../components/layout/Shell'
import { Abas, type Aba } from '../components/ui/Abas'
import { Badge } from '../components/ui/Badge'
import { ErroCarregar } from '../components/ui/Estados'
import { ErroApi, api } from '../data/api'
import { CAUSA } from '../data/simulador'
import type { Abordagem, ClienteFicticio, Configuracao, EstadoSimulacao } from '../data/tipos'
import { cx } from '../lib/cx'
import { fmt } from '../lib/format'
import { useReducedMotion } from '../lib/useReducedMotion'
import { CartaoPix, type Brilho } from './simulacao/CartaoPix'
import { Etapas } from './simulacao/Etapas'
import { FormularioCliente } from './simulacao/FormularioCliente'
import { MensagensSimuladas, PainelSistema } from './simulacao/PainelSistema'
import { RetencaoSimulada } from './simulacao/RetencaoSimulada'

type AbaSim = 'cobranca' | 'retencao'
const DURACAO_PROCESSANDO = 1600

/** Quantas cobranças já foram respondidas (inicial + tentativas) — para saber se uma nova aconteceu. */
function respondidas(e: EstadoSimulacao): number {
  return (e.fase === 'formulario' ? 0 : 1) + e.tentativas.filter((t) => t.resultado === 'paga' || t.resultado === 'falhou').length + (e.desfecho?.via === 'mensagem' ? 1 : 0)
}

export function Simulacao() {
  const { empresa } = usePainel()
  const premium = empresa?.plano === 'premium'
  const reduzido = useReducedMotion()

  const [params, setParams] = useSearchParams()
  const aba: AbaSim = params.get('aba') === 'retencao' ? 'retencao' : 'cobranca'
  const irPara = (v: AbaSim) => setParams(v === 'cobranca' ? {} : { aba: v }, { replace: true })

  const [estado, setEstado] = useState<EstadoSimulacao | null>(null)
  const [config, setConfig] = useState<Configuracao | null>(null)
  const [virado, setVirado] = useState(false)
  const [rascunho, setRascunho] = useState<ClienteFicticio | null>(null)
  const [processando, setProcessando] = useState(false)
  const [ocupado, setOcupado] = useState(false)
  const timer = useRef<number | null>(null)

  const [erro, setErro] = useState<string | null>(null)
  const [tentativaCarga, setTentativaCarga] = useState(0)
  useEffect(() => {
    let vivo = true
    setErro(null)
    Promise.all([api.simulacao(), api.configuracao()])
      .then(([e, c]) => {
        if (!vivo) return
        setEstado(e)
        setConfig(c)
      })
      .catch((e: unknown) => vivo && setErro(e instanceof ErroApi ? e.message : 'Algo deu errado ao carregar a simulação.'))
    return () => {
      vivo = false
      if (timer.current) window.clearTimeout(timer.current)
    }
  }, [tentativaCarga])

  /** Roda uma ação da simulação; se falhar, avisa e destrava os botões. */
  async function acao(f: () => Promise<void>) {
    setOcupado(true)
    setErro(null)
    try {
      await f()
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : 'Algo deu errado. Tente de novo.')
      setOcupado(false)
    }
  }

  /** Aplica um estado novo; se houve cobrança nova, mostra o "processando" antes do resultado. */
  function aplicar(novo: EstadoSimulacao, anterior: EstadoSimulacao | null) {
    const houveCobranca = respondidas(novo) > respondidas(anterior ?? novo) || (anterior?.fase === 'formulario' && novo.fase !== 'formulario')
    if (houveCobranca && !reduzido) {
      setVirado(false)
      setProcessando(true)
      timer.current = window.setTimeout(() => {
        setEstado(novo)
        setProcessando(false)
        setOcupado(false)
      }, DURACAO_PROCESSANDO)
    } else {
      setEstado(novo)
      setOcupado(false)
    }
  }

  const simular = (cliente: ClienteFicticio) => acao(async () => aplicar(await api.simularCobranca(cliente), estado))
  const avancar = (dias: number) => acao(async () => aplicar(await api.simulacaoAvancar(dias), estado))
  const avancarAteAcao = () => acao(async () => aplicar(await api.simulacaoAvancarAteProximaAcao(), estado))
  const escolher = (abordagem: Abordagem) =>
    acao(async () => {
      setEstado(await api.simulacaoEscolherMensagem(abordagem))
      setOcupado(false)
    })
  const recomecar = () =>
    acao(async () => {
      setVirado(false)
      setEstado(await api.simulacaoLimpar())
      setOcupado(false)
    })

  const abas: Aba<AbaSim>[] = [
    { valor: 'cobranca', rotulo: 'Cobrança Pix (involuntário)' },
    { valor: 'retencao', rotulo: 'Cliente em risco (voluntário)', extra: premium ? null : <Badge className="px-1.5 py-0 text-rotulo">Premium</Badge> },
  ]

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="t-h2 text-paper">Simulação do gateway</h2>
            <Badge tone="amber">Demonstração</Badge>
          </div>
          <p className="t-apoio mt-1 max-w-[720px] text-silver">
            O gateway de pagamento real será integrado em breve; aqui você vê o sistema agindo sobre um cliente fictício. Nada é enviado a nenhum banco nem a nenhuma pessoa.
          </p>
        </div>
        <Abas abas={abas} ativa={aba} onChange={irPara} rotulo="Tipo de simulação" idBase="sim" />
      </div>

      {aba === 'retencao' ? (
        premium ? (
          <RetencaoSimulada />
        ) : (
          <div className="card-glass rounded-[18px] p-8 text-center">
            <div className="t-h3 text-paper">A simulação do voluntário faz parte do plano premium</div>
            <p className="t-apoio mx-auto mt-2 max-w-md text-silver">No premium, a CRAI também acompanha os sinais de risco dos seus clientes e faz ofertas antes do cancelamento.</p>
          </div>
        )
      ) : erro && estado === null ? (
        <ErroCarregar mensagem={erro} onTentar={() => setTentativaCarga((t) => t + 1)} />
      ) : estado === null ? (
        <div className="card-glass min-h-[420px] rounded-[18px]" aria-busy="true" />
      ) : (
        <>
        {erro ? (
          <div role="alert" className="rounded-[12px] border border-danger/40 bg-danger/[0.06] px-4 py-3 text-apoio text-paper">
            {erro} Nada foi perdido; tente a ação de novo.
          </div>
        ) : null}
        <SimulacaoCobranca
          estado={estado}
          empresa={empresa?.nome ?? 'Sua empresa'}
          modoEscolha={config?.modo_mensagem_involuntario === 'escolha'}
          rascunho={rascunho}
          onRascunho={setRascunho}
          virado={virado}
          setVirado={setVirado}
          processando={processando}
          ocupado={ocupado}
          onSimular={simular}
          onAvancar={avancar}
          onAvancarAteAcao={avancarAteAcao}
          onEscolher={escolher}
          onRecomecar={recomecar}
        />
        </>
      )}
    </div>
  )
}

interface PropsCobranca {
  estado: EstadoSimulacao
  empresa: string
  modoEscolha: boolean
  rascunho: ClienteFicticio | null
  onRascunho: (c: ClienteFicticio) => void
  virado: boolean
  setVirado: (v: boolean) => void
  processando: boolean
  ocupado: boolean
  onSimular: (c: ClienteFicticio) => void
  onAvancar: (d: number) => void
  onAvancarAteAcao: () => void
  onEscolher: (a: Abordagem) => void
  onRecomecar: () => void
}

function SimulacaoCobranca(p: PropsCobranca) {
  const { estado, processando } = p
  const reduzido = useReducedMotion()
  const comecou = estado.fase !== 'formulario'
  const mostrandoMensagens = estado.fase === 'mensagens' && !processando

  const brilho: Brilho = processando
    ? 'processando'
    : estado.fase === 'recusada'
      ? 'recusada'
      : estado.fase === 'recuperada'
        ? 'recuperada'
        : estado.fase === 'encerrada'
          ? 'encerrada'
          : estado.fase === 'mensagem_enviada' || estado.fase === 'mensagens'
            ? 'aguardando'
            : 'nenhum'

  const proximaCobranca = estado.proxima_acao && /Tentativa/.test(estado.proxima_acao.descricao) ? estado.proxima_acao.quando : estado.tentativas[0]?.agendada_para ?? null
  const ultimaTentativa = [...estado.tentativas].reverse().find((t) => t.resultado === 'paga' || t.resultado === 'falhou')

  return (
    <div className="card-glass rounded-[18px] p-5 md:p-6">
      <Etapas estado={estado} />

      <LayoutGroup>
        <div className={cx('mt-6 grid gap-6', processando ? 'grid-cols-1' : 'lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]')}>
          {/* Esquerda: o cartão (ou as 3 mensagens) */}
          <motion.div layout className={cx('flex flex-col items-center gap-4 self-start', processando ? 'py-6' : 'lg:sticky lg:top-6')} transition={{ layout: { duration: reduzido ? 0 : 0.5, ease: [0.16, 1, 0.3, 1] } }}>
            <AnimatePresence mode="wait" initial={false}>
              {mostrandoMensagens ? (
                <motion.div key="mensagens" initial={reduzido ? false : { opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="w-full">
                  <MensagensSimuladas sugestoes={estado.sugestoes} modoEscolha={p.modoEscolha} ocupado={p.ocupado} onEscolher={p.onEscolher} />
                </motion.div>
              ) : (
                <motion.div key="cartao" layout initial={false} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="flex w-full flex-col items-center gap-3">
                  <CartaoPix
                    cliente={estado.cliente ?? p.rascunho}
                    empresa={p.empresa}
                    idRecorrencia={estado.id_recorrencia}
                    proximaCobranca={comecou ? proximaCobranca : estado.hoje}
                    virado={p.virado}
                    brilho={brilho}
                    estado={estado}
                    faixa={<FaixaStatus estado={estado} processando={processando} ultima={ultimaTentativa?.numero ?? null} />}
                  />
                  {comecou && !processando ? (
                    <button
                      type="button"
                      onClick={() => p.setVirado(!p.virado)}
                      aria-pressed={p.virado}
                      className="t-label rounded-[8px] px-2 py-1 font-[560] text-silver underline-offset-4 hover:text-paper hover:underline"
                    >
                      {p.virado ? 'Virar de volta' : 'Ver a verdade escondida (o sistema não vê)'}
                    </button>
                  ) : null}
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>

          {/* Direita: formulário antes; relógio, pensamento e linha do tempo depois */}
          <AnimatePresence initial={false}>
            {!processando ? (
              <motion.div key={comecou ? 'painel' : 'form'} initial={reduzido ? false : { opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0, transition: { duration: 0.15 } }} className="min-w-0">
                {comecou ? (
                  <PainelSistema estado={estado} ocupado={p.ocupado} onAvancar={p.onAvancar} onAvancarAteAcao={p.onAvancarAteAcao} onRecomecar={p.onRecomecar} />
                ) : (
                  <FormularioCliente onSimular={p.onSimular} onRascunho={p.onRascunho} ocupado={p.ocupado} />
                )}
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </LayoutGroup>
    </div>
  )
}

/** A faixa embaixo do cartão: o que acabou de acontecer, sempre com texto (nunca só a cor). */
function FaixaStatus({ estado, processando, ultima }: { estado: EstadoSimulacao; processando: boolean; ultima: number | null }) {
  if (processando) {
    return (
      <Faixa tom="orange" icone={<IconSend width={15} height={15} />}>
        Enviando cobrança Pix…
        <span className="t-label ml-2 text-silver">{ultima === null && estado.fase === 'formulario' ? 'Cobrança do dia' : `Tentativa ${(ultima ?? 0) + 1}`}</span>
      </Faixa>
    )
  }
  switch (estado.fase) {
    case 'formulario':
      return <p className="t-label text-center text-muted">Preencha o cliente ao lado e clique em "Simular cobrança".</p>
    case 'recusada':
      return (
        <Faixa tom="danger" icone={<IconAlert width={15} height={15} />}>
          Recusada: {estado.causa ? CAUSA[estado.causa].toLowerCase() : ''}
          {estado.proxima_acao ? <span className="t-label ml-2 text-silver">Próxima: {estado.proxima_acao.descricao.toLowerCase()} em {fmt.dataCurta(estado.proxima_acao.quando)}</span> : null}
        </Faixa>
      )
    case 'mensagem_enviada':
      return (
        <Faixa tom="amber" icone={<IconSend width={15} height={15} />}>
          Mensagem enviada por WhatsApp
          <span className="t-label ml-2 text-silver">Aguardando resposta por 2 dias</span>
        </Faixa>
      )
    case 'recuperada':
      return (
        <Faixa tom="ok" icone={<IconCheck width={15} height={15} />}>
          {estado.desfecho?.tentativa === 0 ? 'Pagamento aprovado de primeira' : `Pagamento recuperado ${estado.desfecho?.via === 'mensagem' ? 'pela mensagem' : `na ${estado.desfecho?.tentativa}ª tentativa`}`}
          {estado.desfecho && estado.desfecho.tentativa !== 0 ? <span className="tabular ml-2 font-[680] text-paper">{fmt.brl(estado.desfecho.valor_liquido)} para você</span> : null}
        </Faixa>
      )
    case 'encerrada':
      return (
        <Faixa tom="neutro" icone={<IconAlert width={15} height={15} />}>
          Encerrado sem recuperação
          <span className="t-label ml-2 text-silver">Nada é cobrado</span>
        </Faixa>
      )
    default:
      return null
  }
}

function Faixa({ tom, icone, children }: { tom: 'orange' | 'danger' | 'ok' | 'amber' | 'neutro'; icone: React.ReactNode; children: React.ReactNode }) {
  const classes = {
    orange: 'border-orange/50 bg-orange/10 text-orange',
    danger: 'border-danger/50 bg-danger/10 text-[#f5a29a]',
    ok: 'border-ok/50 bg-ok/10 text-ok',
    amber: 'border-amber/40 bg-amber/[0.08] text-amber',
    neutro: 'border-line bg-paper/[0.04] text-silver',
  }[tom]
  return (
    <div role="status" className={cx('flex w-full flex-wrap items-center gap-2 rounded-[12px] border px-4 py-2.5 text-apoio font-[600]', classes)}>
      {icone}
      <span className="flex flex-wrap items-baseline gap-x-1">{children}</span>
    </div>
  )
}

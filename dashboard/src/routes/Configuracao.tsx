import { AnimatePresence, motion } from 'framer-motion'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { IconBell, IconChat, IconGear, IconShield, IconTable, IconUsers } from '../components/icons/Icons'
import { usePainel } from '../components/layout/Shell'
import { Badge } from '../components/ui/Badge'
import { Card } from '../components/ui/Card'
import { Demonstracao, FaixaDemonstracao } from '../components/ui/Demonstracao'
import { ErroCarregar } from '../components/ui/Estados'
import { ErroApi, api } from '../data/api'
import type { Configuracao as Config, EmpresaDetalhe, Membro, Papel } from '../data/tipos'
import { cx } from '../lib/cx'
import { useReducedMotion } from '../lib/useReducedMotion'
import { SecaoDados, SecaoEmpresa, SecaoEquipe, SecaoIntegracao, SecaoMensagens, SecaoNotificacoes } from './configuracao/Secoes'

type SecaoId = 'mensagens' | 'empresa' | 'equipe' | 'integracao' | 'dados' | 'notificacoes'

/** `de`: as funções de `api.ts` que alimentam a seção (decide a etiqueta "Demonstração"). */
const SECOES: { id: SecaoId; rotulo: string; apoio: string; Icone: typeof IconGear; premium?: boolean; de: string[] }[] = [
  { id: 'mensagens', rotulo: 'Mensagens', apoio: 'Modo, prazo, horário e canais', Icone: IconChat, de: ['configuracao', 'salvarConfiguracao'] },
  { id: 'empresa', rotulo: 'Empresa', apoio: 'Como aparece para o cliente', Icone: IconGear, de: ['empresaDetalhe'] },
  { id: 'equipe', rotulo: 'Equipe', apoio: 'Pessoas e papéis', Icone: IconUsers, de: ['membros', 'mudarPapel'] },
  { id: 'integracao', rotulo: 'Integração', apoio: 'Chaves de API e webhook', Icone: IconTable, premium: true, de: ['integracao'] },
  { id: 'dados', rotulo: 'Dados e privacidade', apoio: 'Direitos do cliente, prazos', Icone: IconShield, de: ['exportarTitular', 'anonimizarTitular', 'explicacaoDecisao'] },
  { id: 'notificacoes', rotulo: 'Notificações', apoio: 'Avisos para a equipe', Icone: IconBell, de: ['notificacoes'] },
]

export function Configuracao() {
  const { empresa } = usePainel()
  const reduzido = useReducedMotion()
  const premium = empresa?.plano === 'premium'
  const papel: Papel = empresa?.papel ?? 'membro'
  const podeEditar = papel === 'owner' || papel === 'admin'

  const [params, setParams] = useSearchParams()
  const pedida = params.get('secao') as SecaoId | null
  const secao: SecaoId = pedida && SECOES.some((s) => s.id === pedida) ? pedida : 'mensagens'
  const irPara = (v: SecaoId) => setParams(v === 'mensagens' ? {} : { secao: v }, { replace: true })

  const [config, setConfig] = useState<Config | null>(null)
  const [detalhe, setDetalhe] = useState<EmpresaDetalhe | null>(null)
  const [membros, setMembros] = useState<Membro[] | null>(null)
  const [aviso, setAviso] = useState<{ texto: string; erro: boolean } | null>(null)

  const [erro, setErro] = useState<string | null>(null)
  const [tentativa, setTentativa] = useState(0)
  useEffect(() => {
    let vivo = true
    setErro(null)
    Promise.all([api.configuracao(), api.empresaDetalhe(), api.membros()])
      .then(([c, d, m]) => {
        if (!vivo) return
        setConfig(c)
        setDetalhe(d)
        setMembros(m)
      })
      .catch((e: unknown) => vivo && setErro(e instanceof ErroApi ? e.message : 'Algo deu errado ao carregar a configuração.'))
    return () => {
      vivo = false
    }
  }, [tentativa])

  function avisar(texto: string, erro = false) {
    const a = { texto, erro }
    setAviso(a)
    window.setTimeout(() => setAviso((x) => (x === a ? null : x)), erro ? 5000 : 2800)
  }
  async function salvar(c: Config) {
    try {
      setConfig(await api.salvarConfiguracao(c))
      avisar('Configuração salva. Vale a partir de agora.')
    } catch (e) {
      avisar(`Não foi salvo: ${e instanceof ErroApi ? e.message : 'algo deu errado.'} Tente de novo.`, true)
    }
  }
  async function mudarPapel(id: string, p: Papel) {
    try {
      setMembros(await api.mudarPapel(id, p))
      avisar('Papel atualizado.')
    } catch (e) {
      avisar(`Não foi salvo: ${e instanceof ErroApi ? e.message : 'algo deu errado.'}`, true)
    }
  }

  const atual = SECOES.find((s) => s.id === secao)!

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="t-h2 text-paper">Configuração</h2>
        <p className="t-apoio mt-1 text-silver">Como o sistema age em nome da sua empresa. Cada mudança vale a partir do momento em que é salva.</p>
      </div>

      <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
        {/* Navegação das seções: lista à esquerda no computador, fileira rolável no celular */}
        <nav aria-label="Seções da configuração" className="min-w-0 lg:sticky lg:top-6 lg:self-start">
          <ul className="scroll-fino -mx-1 flex gap-1 overflow-x-auto px-1 pb-1 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0">
            {SECOES.map((s) => {
              const sel = s.id === secao
              const bloqueada = s.premium && !premium
              return (
                <li key={s.id} className="shrink-0 lg:shrink">
                  <button
                    type="button"
                    aria-current={sel ? 'page' : undefined}
                    onClick={() => irPara(s.id)}
                    className={cx(
                      'flex w-full items-center gap-3 rounded-[12px] border px-3 py-2.5 text-left transition-colors',
                      sel ? 'border-orange/50 bg-orange/[0.08]' : 'border-transparent hover:border-line hover:bg-paper/[0.03]',
                      bloqueada && 'opacity-60',
                    )}
                  >
                    <span className={cx('flex h-8 w-8 shrink-0 items-center justify-center rounded-[9px]', sel ? 'bg-orange/15 text-orange' : 'bg-paper/[0.05] text-silver')}>
                      <s.Icone width={16} height={16} />
                    </span>
                    <span className="min-w-0">
                      <span className={cx('flex items-center gap-2 text-apoio font-[560] whitespace-nowrap', sel ? 'text-paper' : 'text-silver')}>
                        {s.rotulo}
                        {bloqueada ? <Badge className="px-1.5 py-0 text-rotulo">Premium</Badge> : null}
                        <Demonstracao de={s.de} className="px-1.5 py-0 text-rotulo" />
                      </span>
                      <span className="t-label hidden text-muted lg:block">{s.apoio}</span>
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
          <div className="mt-4 hidden rounded-[12px] border border-line bg-ink/25 px-3.5 py-3 lg:block">
            <div className="t-label text-silver">Você está como</div>
            <div className="mt-0.5 text-apoio font-[560] text-paper">{papel === 'owner' ? 'Dono' : papel === 'admin' ? 'Administrador' : 'Membro (só leitura)'}</div>
          </div>
        </nav>

        <Card className="min-w-0 p-5 md:p-6">
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={atual.id}
              initial={reduzido ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduzido ? { opacity: 1 } : { opacity: 0 }}
              transition={{ duration: reduzido ? 0 : 0.2 }}
            >
              <FaixaDemonstracao de={atual.de} />
              {erro ? (
                <ErroCarregar mensagem={erro} onTentar={() => setTentativa((t) => t + 1)} />
              ) : config === null ? (
                <div className="min-h-[360px] animate-pulse rounded-[12px] bg-paper/[0.04]" aria-busy="true" />
              ) : secao === 'mensagens' ? (
                <SecaoMensagens config={config} onSalvar={salvar} podeEditar={podeEditar} />
              ) : secao === 'empresa' ? (
                <SecaoEmpresa empresa={detalhe} />
              ) : secao === 'equipe' ? (
                <SecaoEquipe membros={membros} papelAtual={papel} onMudar={mudarPapel} />
              ) : secao === 'integracao' ? (
                <SecaoIntegracao premium={premium} podeEditar={podeEditar} />
              ) : secao === 'dados' ? (
                <SecaoDados config={config} podeEditar={podeEditar} />
              ) : (
                <SecaoNotificacoes config={config} onSalvar={salvar} podeEditar={podeEditar} />
              )}
            </motion.div>
          </AnimatePresence>
        </Card>
      </div>

      {/* Aviso de salvo, discreto, em cima do alternador */}
      <AnimatePresence>
        {aviso ? (
          <motion.div
            key="aviso"
            role="status"
            initial={reduzido ? false : { opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className={cx(
              'fixed bottom-20 left-1/2 z-40 max-w-[calc(100vw-32px)] -translate-x-1/2 rounded-full border px-4 py-2 text-apoio font-[560] shadow-[0_20px_50px_-20px_rgba(0,0,0,0.9)]',
              aviso.erro ? 'border-danger/50 bg-[#2a1c1a] text-[#f5a29a]' : 'border-ok/50 bg-[#1f2a22] text-ok',
            )}
          >
            {aviso.texto}
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  )
}

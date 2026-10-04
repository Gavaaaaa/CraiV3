import { AnimatePresence, motion } from 'framer-motion'
import { Link, useSearchParams } from 'react-router-dom'
import { IconAlert, IconArrowRight, IconRefresh, IconSpark, IconUsers } from '../components/icons/Icons'
import { usePainel } from '../components/layout/Shell'
import { Abas, type Aba } from '../components/ui/Abas'
import { Badge } from '../components/ui/Badge'
import { Card } from '../components/ui/Card'
import { FaixaDemonstracao } from '../components/ui/Demonstracao'
import { Carregando, ErroCarregar, Vazio } from '../components/ui/Estados'
import { StatTile } from '../components/ui/StatTile'
import { api, etiquetaDeDemonstracao } from '../data/api'
import { fmt } from '../lib/format'
import { useCarregar } from '../lib/useCarregar'
import { useReducedMotion } from '../lib/useReducedMotion'
import { GraficoTrintaDias } from './visao/GraficoTrintaDias'
import {
  AtividadeRecente,
  ExtratoDoMes,
  FunilInvoluntario,
  GrupoDeControle,
  OQueMaisFunciona,
  SaudeDoSistema,
  SeloSaude,
  saudeGeral,
} from './visao/Blocos'

const dataLonga = new Intl.DateTimeFormat('pt-BR', { day: 'numeric', month: 'long' })
const periodoTexto = (de: string, ate: string) => {
  const a = dataLonga.format(new Date(`${de}T12:00:00`))
  const b = dataLonga.format(new Date(`${ate}T12:00:00`))
  return `${a} a ${b}`
}

type AbaVisao = 'mantido' | 'caminho' | 'funciona' | 'extrato' | 'atividade' | 'saude'
const ABAS_VALIDAS: AbaVisao[] = ['mantido', 'caminho', 'funciona', 'extrato', 'atividade', 'saude']

/** Divisão do valor mantido entre os dois churns, dentro do cartão laranja. */
function Divisao({ inv, vol }: { inv: number; vol: number | null }) {
  const total = inv + (vol ?? 0)
  const pInv = total ? (inv / total) * 100 : 0
  return (
    <div className="mt-5">
      <div className="flex h-2 w-full gap-[2px] overflow-hidden rounded-full" aria-hidden="true">
        <span className="h-full rounded-l-full bg-ink/70" style={{ width: `${pInv}%` }} />
        {vol !== null ? <span className="h-full flex-1 rounded-r-full bg-ink/25" /> : null}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-ink">
        <div>
          <dt className="t-label flex items-center gap-1.5 text-ink/75">
            <span className="h-2 w-2 rounded-[2px] bg-ink/70" aria-hidden="true" /> Recuperado (involuntário)
          </dt>
          <dd className="mt-0.5 text-[17px] font-[640] tracking-[-0.01em]">{fmt.brlInteiro(inv)}</dd>
        </div>
        <div>
          <dt className="t-label flex items-center gap-1.5 text-ink/75">
            <span className="h-2 w-2 rounded-[2px] bg-ink/25" aria-hidden="true" /> Retido (voluntário)
          </dt>
          <dd className="mt-0.5 text-[17px] font-[640] tracking-[-0.01em]">{vol !== null ? fmt.brlInteiro(vol) : 'Plano premium'}</dd>
        </div>
      </dl>
    </div>
  )
}

export function VisaoGeral() {
  const { modo, empresa } = usePainel()
  const sim = modo === 'simulacao'
  const premium = empresa?.plano === 'premium'

  // Uma carga só, com os três estados (carregando, erro, pronto); os blocos abrem conforme chegam.
  const carga = useCarregar(
    () =>
      Promise.all([
        api.resumoVisaoGeral({ incluirSimulados: sim }),
        api.serieDupla({ incluirSimulados: sim }),
        api.atividade({ incluirSimulados: sim, limite: 12 }),
        api.extrato({ incluirSimulados: sim }),
        api.funil(),
        api.oQueFunciona(),
        api.saude(),
      ]),
    [sim],
  )
  const [resumo, serie, atividade, extrato, funil, oqf, saude] = carga.dados ?? [null, null, null, null, null, null, null]

  // A aba fica no endereço (?aba=extrato): dá para voltar direto nela e mandar o link.
  const [params, setParams] = useSearchParams()
  const pedida = params.get('aba') as AbaVisao | null
  const aba: AbaVisao = pedida && ABAS_VALIDAS.includes(pedida) ? pedida : 'mantido'
  const irPara = (v: AbaVisao) => setParams(v === 'mantido' ? {} : { aba: v }, { replace: true })
  const reduzido = useReducedMotion()
  const geral = saudeGeral(saude)

  const abas: Aba<AbaVisao>[] = [
    { valor: 'mantido', rotulo: 'Dinheiro mantido' },
    { valor: 'caminho', rotulo: 'Funil de recuperação' },
    { valor: 'funciona', rotulo: 'O que mais funciona' },
    { valor: 'extrato', rotulo: 'Extrato' },
    { valor: 'atividade', rotulo: 'Atividade' },
    {
      valor: 'saude',
      rotulo: 'Saúde do sistema',
      extra:
        geral === 'carregando' ? null : (
          <span
            className={geral === 'ok' ? 'h-1.5 w-1.5 rounded-full bg-ok' : 'h-1.5 w-1.5 rounded-full bg-danger'}
            aria-label={geral === 'ok' ? 'Tudo funcionando' : 'Precisa de atenção'}
          />
        ),
    },
  ]

  const mantido = resumo ? resumo.recuperado_involuntario + (resumo.retido_voluntario ?? 0) : null
  // Os cartões do topo ainda vêm de uma rota que o backend não tem (Etapa 3).
  const demo = etiquetaDeDemonstracao('resumoVisaoGeral')
  const fonteDaAba: Record<AbaVisao, string[]> = {
    mantido: ['serieDupla'],
    caminho: ['funil'],
    funciona: ['oQueFunciona'],
    extrato: ['extrato'],
    atividade: ['atividade'],
    saude: ['saude'],
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="t-h2 text-paper">Visão geral</h2>
          <p className="t-apoio mt-1 text-silver">Tudo o que a CRAI recuperou e manteve para você, nos dois tipos de churn.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {sim ? <Badge tone="amber">Inclui demonstração</Badge> : null}
          <span className="t-label rounded-[8px] border border-line bg-slate/50 px-3 py-1.5 text-silver">
            Últimos 30 dias{resumo ? ` · ${periodoTexto(resumo.periodo.de, resumo.periodo.ate)}` : ''}
          </span>
          <button type="button" onClick={() => irPara('saude')} className="rounded-full" aria-label="Abrir a saúde do sistema">
            <SeloSaude geral={geral} compacto />
          </button>
        </div>
      </div>

      {carga.erro ? <ErroCarregar mensagem={carga.erro} onTentar={carga.recarregar} /> : null}

      {/* Bento: o laranja é o único colorido */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6" aria-label="Resumo dos últimos 30 dias">
        <StatTile
          className="row-span-2 min-h-[230px] sm:col-span-2"
          tone="orange"
          hero
          demo={demo}
          rotulo="Mantido para você nos últimos 30 dias"
          valor={mantido !== null ? fmt.brlInteiro(mantido) : '—'}
          apoio={
            <>
              <span>Recuperado mais retido, já descontada a taxa da CRAI.</span>
              {resumo ? <Divisao inv={resumo.recuperado_involuntario} vol={resumo.retido_voluntario} /> : null}
            </>
          }
        />
        <StatTile
          demo={demo}
          rotulo="Recuperado do involuntário"
          valor={resumo ? fmt.brlInteiro(resumo.recuperado_involuntario) : '—'}
          apoio={resumo ? `${resumo.cobrancas_recuperadas} cobranças Pix que voltaram` : ''}
          icone={<IconRefresh width={17} height={17} />}
        />
        <StatTile
          demo={demo}
          rotulo="Retido do voluntário"
          valor={resumo ? (resumo.retido_voluntario !== null ? fmt.brlInteiro(resumo.retido_voluntario) : '—') : '—'}
          apoio={
            resumo
              ? resumo.retido_voluntario !== null
                ? `${resumo.clientes_mantidos} clientes que ficaram`
                : 'Disponível no plano premium'
              : ''
          }
          icone={<IconUsers width={17} height={17} />}
        />
        <StatTile
          demo={demo}
          rotulo="Ciclos ativos"
          valor={resumo?.ciclos_ativos ?? '—'}
          apoio={
            resumo ? (
              <Link to="/involuntario" className="inline-flex items-center gap-1 hover:text-paper">
                {resumo.aguardando_escolha ? `${resumo.aguardando_escolha} aguardando sua escolha` : 'Ver no involuntário'}
                <IconArrowRight width={13} height={13} />
              </Link>
            ) : (
              ''
            )
          }
          icone={<IconSpark width={17} height={17} />}
        />
        <StatTile
          demo={demo}
          rotulo="Clientes em risco grave"
          valor={premium ? (resumo?.clientes_risco_grave ?? '—') : '—'}
          apoio={premium ? (resumo ? `${resumo.risco_grave_com_oferta} já receberam uma oferta` : '') : 'Disponível no plano premium'}
          icone={<IconAlert width={17} height={17} />}
        />
        <StatTile
          demo={demo}
          rotulo="Taxa de recuperação"
          valor={resumo ? fmt.pontos(resumo.taxa_recuperacao * 100) : '—'}
          apoio={resumo ? `Das ${resumo.ciclos_com_desfecho} cobranças que já tiveram desfecho` : ''}
        />
        <div className="min-h-[150px] sm:col-span-2 lg:col-span-3 xl:col-span-3">
          <GrupoDeControle />
        </div>
      </section>

      {/* Uma coisa por vez: cada gráfico na sua aba */}
      <section aria-label="Detalhes" className="flex flex-col gap-4">
        <Abas abas={abas} ativa={aba} onChange={irPara} rotulo="Detalhes da visão geral" idBase="visao" />
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={aba}
            role="tabpanel"
            id={`visao-painel-${aba}`}
            aria-labelledby={`visao-aba-${aba}`}
            initial={reduzido ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduzido ? { opacity: 1 } : { opacity: 0, y: -4 }}
            transition={{ duration: reduzido ? 0 : 0.22, ease: [0.16, 1, 0.3, 1] }}
          >
            <FaixaDemonstracao de={fonteDaAba[aba]} />
            {aba === 'mantido' ? (
              <Card className="p-5 md:p-6">
                {serie ? (
                  serie.every((p) => p.involuntario === 0 && p.voluntario === 0) ? (
                    <Vazio
                      titulo="Nada mantido ainda nos últimos 30 dias"
                      texto="O gráfico aparece quando a primeira cobrança for recuperada ou o primeiro cliente aceitar uma oferta. Se a integração é nova, isso costuma levar poucos dias."
                      icone={<IconSpark width={22} height={22} />}
                      acao={
                        <Link to="/simulacao" className="t-label rounded-[8px] border border-line px-3 py-1.5 font-[560] text-silver hover:text-paper">
                          Ver o sistema agindo na simulação
                        </Link>
                      }
                    />
                  ) : (
                    <GraficoTrintaDias pontos={serie} comVoluntario={premium} />
                  )
                ) : (
                  <Carregando altura={420} />
                )}
              </Card>
            ) : aba === 'caminho' ? (
              <Card className="p-5 md:p-6">
                {funil ? (
                  funil.etapas[0]?.chegaram === 0 ? (
                    <Vazio titulo="Nenhuma cobrança falhou em setembro" texto="Quando uma cobrança Pix falhar, o caminho dela aparece aqui: tentativas, mensagem e desfecho." />
                  ) : (
                    <FunilInvoluntario funil={funil} />
                  )
                ) : (
                  <Carregando altura={380} />
                )}
              </Card>
            ) : aba === 'funciona' ? (
              <Card className="p-5 md:p-6">
                {oqf ? <OQueMaisFunciona dados={oqf} comVoluntario={premium} /> : <Carregando altura={300} />}
              </Card>
            ) : aba === 'extrato' ? (
              <Card className="p-0">
                {extrato ? (
                  <ExtratoDoMes linhas={extrato} mes="2026-09" />
                ) : (
                  <div className="p-5">
                    <Carregando altura={320} />
                  </div>
                )}
              </Card>
            ) : aba === 'atividade' ? (
              <Card className="p-5 md:p-6">{atividade ? <AtividadeRecente itens={atividade} /> : <Carregando altura={320} />}</Card>
            ) : (
              <Card className="p-5 md:p-6">
                <SaudeDoSistema saude={saude} />
              </Card>
            )}
          </motion.div>
        </AnimatePresence>
      </section>
    </div>
  )
}

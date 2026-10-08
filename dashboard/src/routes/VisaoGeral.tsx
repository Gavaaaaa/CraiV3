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
import { ErroApi, agoraDaTela, api, etiquetaDeDemonstracao } from '../data/api'
import { fmt } from '../lib/format'
import { localeAtual, t } from '../lib/idioma'
import { useCarregar } from '../lib/useCarregar'
import { useModoMensagem } from '../lib/useModoMensagem'
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

const dataLonga = new Intl.DateTimeFormat(localeAtual(), { day: 'numeric', month: 'long' })
const periodoTexto = (de: string, ate: string) => {
  const a = dataLonga.format(new Date(`${de}T12:00:00`))
  const b = dataLonga.format(new Date(`${ate}T12:00:00`))
  return t('{de} a {ate}', { de: a, ate: b })
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
        <span className="h-full rounded-l-full bg-sobre-destaque/70" style={{ width: `${pInv}%` }} />
        {vol !== null ? <span className="h-full flex-1 rounded-r-full bg-sobre-destaque/25" /> : null}
      </div>
      <dl className="mt-3 grid grid-cols-2 gap-3 text-sobre-destaque">
        <div>
          <dt className="t-label flex items-center gap-1.5 text-sobre-destaque">
            <span className="h-2 w-2 rounded-[2px] bg-sobre-destaque/70" aria-hidden="true" /> {t('Recuperado (involuntário)')}
          </dt>
          <dd className="mt-0.5 text-[17px] font-[640] tracking-[-0.01em]">{fmt.brlInteiro(inv)}</dd>
        </div>
        <div>
          <dt className="t-label flex items-center gap-1.5 text-sobre-destaque">
            <span className="h-2 w-2 rounded-[2px] bg-sobre-destaque/25" aria-hidden="true" /> {t('Retido (voluntário)')}
          </dt>
          <dd className="mt-0.5 text-[17px] font-[640] tracking-[-0.01em]">{vol !== null ? fmt.brlInteiro(vol) : t('Plano premium')}</dd>
        </div>
      </dl>
    </div>
  )
}

export function VisaoGeral() {
  const { modo, empresa } = usePainel()
  const automatico = useModoMensagem() === 'automatico'
  const sim = modo === 'simulacao'
  const premium = empresa?.plano === 'premium'

  // Uma carga só, com os três estados (carregando, erro, pronto); os blocos abrem conforme chegam.
  const carga = useCarregar(
    () =>
      Promise.all([
        api.resumoVisaoGeral({ incluirSimulados: sim }),
        api.serieDupla({ incluirSimulados: sim }),
        api.atividade({ incluirSimulados: sim, limite: 12 }),
        // O extrato traz a taxa da CRAI: o backend só o entrega ao dono e aos administradores.
        // Para o membro, o resto da página abre normalmente e a aba diz por que está vazia.
        api.extrato({ incluirSimulados: sim }).catch((e: unknown) => {
          if (e instanceof ErroApi && e.codigo === 'sem_permissao') return 'sem_permissao' as const
          throw e
        }),
        api.funil({ incluirSimulados: sim }),
        api.oQueFunciona({ incluirSimulados: sim }),
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
    { valor: 'mantido', rotulo: t('Dinheiro mantido') },
    { valor: 'caminho', rotulo: t('Funil de recuperação') },
    { valor: 'funciona', rotulo: t('O que mais funciona') },
    { valor: 'extrato', rotulo: t('Extrato') },
    { valor: 'atividade', rotulo: t('Atividade') },
    {
      valor: 'saude',
      rotulo: t('Saúde do sistema'),
      extra:
        geral === 'carregando' ? null : (
          <span
            className={geral === 'ok' ? 'h-1.5 w-1.5 rounded-full bg-ok' : 'h-1.5 w-1.5 rounded-full bg-danger'}
            aria-label={geral === 'ok' ? t('Tudo funcionando') : t('Precisa de atenção')}
          />
        ),
    },
  ]

  const mantido = resumo ? resumo.recuperado_involuntario + (resumo.retido_voluntario ?? 0) : null
  // O mês do extrato e do funil é o corrente (o backend usa o mesmo quando a tela não pede outro).
  const mesAtual = fmt.mesDe(agoraDaTela())
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
          <h2 className="t-h2 text-paper">{t('Visão geral')}</h2>
          <p className="t-apoio mt-1 text-silver">{t('Tudo o que a CRAI recuperou e manteve para você, nos dois tipos de churn.')}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {sim ? <Badge tone="amber">{t('Inclui demonstração')}</Badge> : null}
          {resumo?.piloto ? (
            <span className="t-label rounded-[8px] border border-line bg-slate/50 px-3 py-1.5 text-silver" data-piloto>
              {t('Período de piloto: sem taxa')}
            </span>
          ) : null}
          <span className="t-label rounded-[8px] border border-line bg-slate/50 px-3 py-1.5 text-silver">
            {t('Últimos 30 dias')}{resumo ? ` · ${periodoTexto(resumo.periodo.de, resumo.periodo.ate)}` : ''}
          </span>
          <button type="button" onClick={() => irPara('saude')} className="rounded-full" aria-label={t('Abrir a saúde do sistema')}>
            <SeloSaude geral={geral} compacto />
          </button>
        </div>
      </div>

      {carga.erro ? <ErroCarregar mensagem={carga.erro} onTentar={carga.recarregar} /> : null}

      {/* Bento: o laranja é o único colorido */}
      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-6" aria-label={t('Resumo dos últimos 30 dias')}>
        <StatTile
          className="row-span-2 min-h-[230px] sm:col-span-2"
          tone="orange"
          hero
          demo={demo}
          rotulo={t('Mantido para você nos últimos 30 dias')}
          valor={mantido !== null ? fmt.brlInteiro(mantido) : '—'}
          apoio={
            <>
              <span>{resumo?.piloto ? t('Recuperado mais retido. No período de piloto, a CRAI não cobra taxa.') : t('Recuperado mais retido, já descontada a taxa da CRAI.')}</span>
              {resumo ? <Divisao inv={resumo.recuperado_involuntario} vol={resumo.retido_voluntario} /> : null}
            </>
          }
        />
        <StatTile
          demo={demo}
          rotulo={t('Recuperado do involuntário')}
          valor={resumo ? fmt.brlInteiro(resumo.recuperado_involuntario) : '—'}
          apoio={resumo ? (resumo.cobrancas_recuperadas === 1 ? t('{n} cobrança Pix que voltou', { n: resumo.cobrancas_recuperadas }) : t('{n} cobranças Pix que voltaram', { n: resumo.cobrancas_recuperadas })) : ''}
          icone={<IconRefresh width={17} height={17} />}
        />
        <StatTile
          demo={demo}
          rotulo={t('Retido do voluntário')}
          valor={resumo ? (resumo.retido_voluntario !== null ? fmt.brlInteiro(resumo.retido_voluntario) : '—') : '—'}
          apoio={
            resumo
              ? resumo.retido_voluntario !== null
                ? resumo.clientes_mantidos === 1
                  ? t('{n} cliente que ficou', { n: resumo.clientes_mantidos ?? 0 })
                  : t('{n} clientes que ficaram', { n: resumo.clientes_mantidos ?? 0 })
                : t('Disponível no plano premium')
              : ''
          }
          icone={<IconUsers width={17} height={17} />}
        />
        <StatTile
          demo={demo}
          rotulo={t('Ciclos ativos')}
          valor={resumo?.ciclos_ativos ?? '—'}
          apoio={
            resumo ? (
              <Link to="/involuntario" className="inline-flex items-center gap-1 hover:text-paper">
                {resumo.aguardando_escolha
                  ? automatico
                    ? resumo.aguardando_escolha === 1
                      ? t('{n} mensagem a enviar', { n: resumo.aguardando_escolha })
                      : t('{n} mensagens a enviar', { n: resumo.aguardando_escolha })
                    : t('{n} aguardando sua escolha', { n: resumo.aguardando_escolha })
                  : t('Ver no involuntário')}
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
          rotulo={t('Clientes em risco grave')}
          valor={premium ? (resumo?.clientes_risco_grave ?? '—') : '—'}
          apoio={premium ? (resumo && resumo.risco_grave_com_oferta !== null ? (resumo.risco_grave_com_oferta === 1 ? t('{n} já recebeu uma oferta', { n: resumo.risco_grave_com_oferta }) : t('{n} já receberam uma oferta', { n: resumo.risco_grave_com_oferta })) : '') : t('Disponível no plano premium')}
          icone={<IconAlert width={17} height={17} />}
        />
        <StatTile
          demo={demo}
          rotulo={t('Taxa de recuperação')}
          valor={resumo && resumo.taxa_recuperacao !== null ? fmt.pontos(resumo.taxa_recuperacao * 100) : '—'}
          apoio={
            resumo
              ? resumo.taxa_recuperacao === null
                ? t('Nenhuma cobrança teve desfecho no período')
                : t('Das {n} cobranças que já tiveram desfecho', { n: resumo.ciclos_com_desfecho })
              : ''
          }
        />
        <div className="min-h-[150px] sm:col-span-2 lg:col-span-3 xl:col-span-3">
          <GrupoDeControle />
        </div>
      </section>

      {/* Uma coisa por vez: cada gráfico na sua aba */}
      <section aria-label={t('Detalhes')} className="flex flex-col gap-4">
        <Abas abas={abas} ativa={aba} onChange={irPara} rotulo={t('Detalhes da visão geral')} idBase="visao" />
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
                      titulo={t('Nada mantido ainda nos últimos 30 dias')}
                      texto={t('O gráfico aparece quando a primeira cobrança for recuperada ou o primeiro cliente aceitar uma oferta. Se a integração é nova, isso costuma levar poucos dias.')}
                      icone={<IconSpark width={22} height={22} />}
                      acao={
                        <Link to="/simulacao" className="t-label rounded-[8px] border border-line px-3 py-1.5 font-[560] text-silver hover:text-paper">
                          {t('Ver o sistema agindo na simulação')}
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
                    <Vazio titulo={t('Nenhuma cobrança falhou em {mes}', { mes: fmt.mesPorExtenso(funil.mes) })} texto={t('Quando uma cobrança Pix falhar, o caminho dela aparece aqui: tentativas, mensagem e desfecho.')} />
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
                {extrato === 'sem_permissao' ? (
                  <div className="p-5">
                    <Vazio
                      titulo={t('O extrato é só para o dono e os administradores')}
                      texto={t('Ele traz a taxa da CRAI de cada valor recuperado ou mantido. Peça a um administrador da sua empresa para abrir esta aba.')}
                    />
                  </div>
                ) : extrato ? (
                  <ExtratoDoMes linhas={extrato} mes={mesAtual} incluirSimulados={sim} piloto={resumo?.piloto ?? false} />
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

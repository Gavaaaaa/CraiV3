import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import {
  IconAlert,
  IconArrowRight,
  IconCheck,
  IconClock,
  IconDownload,
  IconPulse,
  IconSend,
  IconShield,
  IconUndo,
  IconUsers,
  IconX,
} from '../../components/icons/Icons'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { ErroApi, MOSTRAR_DEMONSTRACAO, agoraDaTela, api } from '../../data/api'
import type { Atividade, Funil, ItemDesempenho, LinhaExtrato, OQueFunciona, SaudeSistema, TipoAtividade } from '../../data/tipos'
import { baixarCsv, baixarCsvPronto } from '../../lib/csv'
import { cx } from '../../lib/cx'
import { fmt } from '../../lib/format'
import { idiomaAtual, localeAtual, t } from '../../lib/idioma'

const pct = (v: number) => `${Math.round(v * 100)}%`

/** Cabeçalho padrão de cartão: título e uma linha de apoio. */
export function CabecalhoCartao({ titulo, apoio, direita }: { titulo: string; apoio?: ReactNode; direita?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h3 className="t-h3 text-paper">{titulo}</h3>
        {apoio ? <p className="t-apoio mt-1 text-silver">{apoio}</p> : null}
      </div>
      {direita}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Funil do involuntário                                               */
/* ------------------------------------------------------------------ */

export function FunilInvoluntario({ funil }: { funil: Funil }) {
  const max = Math.max(...funil.etapas.map((e) => e.chegaram), 1)
  const { recuperados, encerrados, em_andamento } = funil.desfecho
  return (
    <div>
      <CabecalhoCartao
        titulo={t('Funil de recuperação em {mes}', { mes: fmt.mesPorExtenso(funil.mes) })}
        apoio={t('Quantas cobranças chegaram a cada etapa e quanto voltou em cada uma. O valor é o total das cobranças naquela etapa.')}
      />
      <div className="mt-6 grid gap-8 lg:grid-cols-[minmax(0,1fr)_280px]">
        <ol className="flex flex-col gap-5">
          {funil.etapas.map((e, i) => (
            <li key={e.etapa}>
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-apoio font-[560] text-paper">
                  {i > 0 ? <span className="tabular mr-2 text-muted">{i}</span> : null}
                  {e.rotulo}
                </span>
                <span className="tabular text-rotulo text-silver">
                  <span className="font-[600] text-paper">{e.chegaram}</span> · {fmt.brlInteiro(e.valor)}
                </span>
              </div>
              <div className="mt-1.5 h-2 w-full rounded-full bg-paper/[0.06]" aria-hidden="true">
                <div className="h-2 rounded-full" style={{ width: `${(e.chegaram / max) * 100}%`, background: 'var(--color-serie-inv)' }} />
              </div>
              {e.recuperados_aqui > 0 ? (
                <div className="t-label mt-1.5 flex items-center gap-1.5 text-silver">
                  <span className="h-1.5 w-1.5 rounded-full bg-ok" aria-hidden="true" />
                  {e.recuperados_aqui === 1
                    ? t('{n} recuperada aqui ·', { n: e.recuperados_aqui })
                    : t('{n} recuperadas aqui ·', { n: e.recuperados_aqui })}{' '}
                  <span className="tabular text-paper">{fmt.brlInteiro(e.valor_recuperado_aqui)}</span>
                </div>
              ) : null}
            </li>
          ))}
        </ol>
        <div className="lg:border-l lg:border-line lg:pl-8">
          <div className="t-label mb-3 text-silver">{t('Desfecho das {n} cobranças', { n: funil.etapas[0]?.chegaram ?? 0 })}</div>
          <div className="flex flex-wrap gap-2 lg:flex-col">
            <Desfecho ponto="bg-ok" numero={recuperados} rotulo={t('Recuperadas')} />
            <Desfecho ponto="bg-graphite" numero={encerrados} rotulo={t('Encerradas sem recuperação')} />
            <Desfecho ponto="bg-orange pulse-orange" numero={em_andamento} rotulo={t('Em andamento')} />
          </div>
          <p className="t-label mt-4 text-silver">
            {t('A mensagem só sai depois de a 3ª tentativa falhar, ou antes, quando o cliente revoga a autorização.')}
          </p>
        </div>
      </div>
    </div>
  )
}

function Desfecho({ ponto, numero, rotulo }: { ponto: string; numero: number; rotulo: string }) {
  return (
    <div className="min-w-[92px] flex-1 rounded-[10px] border border-line bg-ink/25 px-3 py-2.5">
      <div className="text-[20px] leading-none font-[660] tracking-[-0.02em] text-paper">{numero}</div>
      <div className="t-label mt-1.5 flex items-start gap-1.5 text-silver">
        <span className={cx('mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full', ponto)} aria-hidden="true" />
        {rotulo}
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* O que mais funciona                                                 */
/* ------------------------------------------------------------------ */

const POUCOS_CASOS = 5

export function OQueMaisFunciona({ dados, comVoluntario }: { dados: OQueFunciona; comVoluntario: boolean }) {
  const causaTop = [...dados.causas].sort((a, b) => b.casos - a.casos)[0]
  const ofertaTop = [...dados.ofertas].filter((o) => o.casos >= POUCOS_CASOS).sort((a, b) => b.taxa - a.taxa)[0]
  const canais = [...dados.canais].sort((a, b) => b.taxa - a.taxa)
  const vezes = canais.length > 1 && canais[canais.length - 1].taxa > 0 ? canais[0].taxa / canais[canais.length - 1].taxa : null
  return (
    <div>
      <CabecalhoCartao
        titulo={t('O que mais funciona')}
        apoio={t('Últimos 30 dias. Com poucos casos, a porcentagem ainda muda muito; o selo avisa.')}
      />
      {dados.causas.length + dados.ofertas.length + dados.canais.length === 0 ? (
        <p className="t-apoio mt-8 text-center text-silver">{t('Ainda não há cobranças nem ofertas com desfecho nos últimos 30 dias.')}</p>
      ) : null}
      <div className={cx('mt-5 grid gap-6', comVoluntario ? 'md:grid-cols-3' : 'md:grid-cols-2')}>
        <Bloco
          titulo={t('Causas de falha')}
          conclusao={causaTop ? t('{causa} é a causa mais comum, e {pct} dessas cobranças voltaram.', { causa: causaTop.rotulo, pct: pct(causaTop.taxa) }) : ''}
          itens={dados.causas}
          cor="var(--color-serie-inv)"
          medida={t('recuperadas')}
        />
        {comVoluntario ? (
          <Bloco
            titulo={t('Ofertas')}
            conclusao={ofertaTop ? t('{oferta} é a oferta mais aceita.', { oferta: ofertaTop.rotulo }) : ''}
            itens={dados.ofertas}
            cor="var(--color-serie-vol)"
            medida={t('aceitaram')}
          />
        ) : null}
        <Bloco
          titulo={t('Canais')}
          conclusao={
            vezes
              ? t('{canal} tem {vezes} vezes mais resposta que {outro}.', {
                  canal: canais[0].rotulo,
                  vezes: idiomaAtual() === 'en' ? vezes.toFixed(1) : vezes.toFixed(1).replace('.', ','),
                  outro: canais[canais.length - 1].rotulo,
                })
              : ''
          }
          itens={dados.canais}
          cor="var(--color-silver)"
          medida={t('responderam')}
        />
      </div>
    </div>
  )
}

function Bloco({
  titulo,
  conclusao,
  itens,
  cor,
  medida,
}: {
  titulo: string
  conclusao: string
  itens: ItemDesempenho[]
  cor: string
  medida: string
}) {
  return (
    <section className="min-w-0">
      <h4 className="t-label font-[600] tracking-[0.02em] text-silver">{titulo}</h4>
      <p className="mt-1.5 min-h-[42px] text-apoio leading-[1.5] text-paper">{conclusao}</p>
      <ul className="mt-3 flex flex-col gap-3">
        {itens.map((it) => (
          <li key={it.rotulo}>
            <div className="flex items-baseline justify-between gap-2 text-rotulo">
              <span className="truncate text-paper" title={it.rotulo}>
                {it.rotulo}
              </span>
              <span className="tabular shrink-0 font-[600] text-paper">{pct(it.taxa)}</span>
            </div>
            <div className="mt-1 h-1.5 w-full rounded-full bg-paper/[0.06]" aria-hidden="true">
              <div className="h-1.5 rounded-full" style={{ width: `${Math.max(it.taxa * 100, it.taxa > 0 ? 3 : 0)}%`, background: cor }} />
            </div>
            <div className="t-label mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-muted">
              <span className="text-silver">
                {medida.charAt(0).toUpperCase() + medida.slice(1)} · {it.casos === 1 ? t('{n} caso', { n: it.casos }) : t('{n} casos', { n: it.casos })}
              </span>
              {it.casos < POUCOS_CASOS ? <Badge className="px-1.5 py-0 text-rotulo">{t('Poucos casos')}</Badge> : null}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

/* ------------------------------------------------------------------ */
/* Atividade recente                                                   */
/* ------------------------------------------------------------------ */

const TIPO: Record<TipoAtividade, { Icone: typeof IconCheck; classe: string; rotulo: string }> = {
  recuperado: { Icone: IconCheck, classe: 'bg-ok/15 text-ok', rotulo: t('Recuperado') },
  oferta_aceita: { Icone: IconUsers, classe: 'bg-serie-vol/20 text-serie-vol-texto', rotulo: t('Oferta aceita') },
  tentativa_falhou: { Icone: IconX, classe: 'bg-danger/15 text-danger-texto', rotulo: t('Tentativa não passou') },
  mensagem_enviada: { Icone: IconSend, classe: 'bg-paper/[0.07] text-silver', rotulo: t('Mensagem enviada') },
  escolha: { Icone: IconClock, classe: 'bg-orange/15 text-orange', rotulo: t('Aguardando você') },
  risco_grave: { Icone: IconAlert, classe: 'bg-danger/15 text-danger-texto', rotulo: t('Risco grave') },
  estorno: { Icone: IconUndo, classe: 'bg-paper/[0.07] text-silver', rotulo: t('Estorno') },
}

export function AtividadeRecente({ itens }: { itens: Atividade[] }) {
  return (
    <div className="flex h-full flex-col">
      <CabecalhoCartao titulo={t('Atividade recente')} apoio={t('O que o sistema fez, do mais novo ao mais antigo.')} />
      {itens.length === 0 ? (
        <p className="t-apoio mt-8 text-center text-silver">{t('Nada aconteceu ainda nos últimos 30 dias.')}</p>
      ) : (
        <ol className="mt-4 flex flex-col">
          {itens.map((a, i) => {
            const tipo = TIPO[a.tipo]
            return (
              <li key={a.id} className="relative flex gap-3 pb-4">
                {i < itens.length - 1 ? <span className="absolute top-9 bottom-1 left-[15px] w-px bg-line" aria-hidden="true" /> : null}
                <span className={cx('flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px]', tipo.classe)}>
                  <tipo.Icone width={16} height={16} />
                  <span className="sr-only">{tipo.rotulo}</span>
                </span>
                <div className="min-w-0 flex-1">
                  <p className="text-apoio leading-[1.45] text-paper">
                    {a.simulado ? (
                      <Badge tone="amber" className="mr-1.5 align-[1px] px-1.5 py-0 text-rotulo">
                        {t('Demonstração')}
                      </Badge>
                    ) : null}
                    {a.texto}
                  </p>
                  <div className="t-label mt-0.5 flex items-center gap-2 text-silver">
                    <span>{fmt.relativo(a.em, agoraDaTela()).replace(/^./, (c) => c.toUpperCase())}</span>
                    {a.valor ? (
                      <>
                        <span className="text-muted">·</span>
                        <span className="tabular text-paper">+ {fmt.brl(a.valor)}</span>
                      </>
                    ) : null}
                  </div>
                </div>
              </li>
            )
          })}
        </ol>
      )}
      <Link
        to="/involuntario"
        className="t-label mt-auto inline-flex items-center gap-1.5 self-start rounded-[6px] font-[560] text-silver transition-colors hover:text-paper"
      >
        {t('Ver todos os clientes em recuperação')} <IconArrowRight width={14} height={14} />
      </Link>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Saúde do sistema                                                    */
/* ------------------------------------------------------------------ */

export function saudeGeral(s: SaudeSistema | null): 'ok' | 'atencao' | 'carregando' {
  if (!s) return 'carregando'
  const tudoOk = s.relogio.ativo && s.modelos.carregados === s.modelos.total && s.redator.disponivel
  return tudoOk ? 'ok' : 'atencao'
}

export function SaudeDoSistema({ saude }: { saude: SaudeSistema | null }) {
  const geral = saudeGeral(saude)
  // As linhas que ainda são fictícias só ganham a etiqueta com a variável ligada (api.ts).
  const demo = MOSTRAR_DEMONSTRACAO ? (saude?.demonstracao ?? []) : []
  const linhas: { ok: boolean; rotulo: string; detalhe: string; demo?: boolean }[] = saude
    ? [
        {
          ok: saude.relogio.ativo,
          rotulo: t('Relógio das tentativas'),
          detalhe: saude.relogio.ativo
            ? saude.relogio.ultima_passagem
              ? t('Ativo. Última passagem {quando}.', {
                  quando: ['agora', 'now'].includes(fmt.relativo(saude.relogio.ultima_passagem, agoraDaTela()))
                    ? t('há menos de 1 min')
                    : fmt.relativo(saude.relogio.ultima_passagem, agoraDaTela()),
                })
              : t('Ativo. Aguardando a primeira passagem.')
            : t('Parado. As tentativas agendadas não estão saindo.'),
        },
        {
          ok: saude.modelos.carregados === saude.modelos.total,
          rotulo: t('Decisões automáticas'),
          detalhe: t('{carregados} de {total} modelos carregados.', { carregados: saude.modelos.carregados, total: saude.modelos.total }),
          demo: demo.includes('modelos'),
        },
        {
          ok: saude.redator.disponivel,
          rotulo: t('Redator de mensagens'),
          detalhe: saude.redator.disponivel ? t('Disponível.') : t('Fora do ar; o sistema usa os textos de reserva.'),
          demo: demo.includes('redator'),
        },
        {
          ok: saude.base !== null,
          rotulo: t('Base de clientes'),
          detalhe: saude.base
            ? saude.base.origem === 'api'
              ? t('Atualizada pela API {quando}.', { quando: fmt.relativo(saude.base.atualizada_em, agoraDaTela()) })
              : saude.base.origem === 'anexo'
                ? t('Atualizada por anexo {quando}.', { quando: fmt.relativo(saude.base.atualizada_em, agoraDaTela()) })
                : t('Atualizada {quando}.', { quando: fmt.relativo(saude.base.atualizada_em, agoraDaTela()) })
            : t('Nenhuma base enviada ainda.'),
          demo: demo.includes('base'),
        },
      ]
    : []
  return (
    <div id="saude" className="flex h-full scroll-mt-6 flex-col">
      <CabecalhoCartao
        titulo={t('Saúde do sistema')}
        apoio={t('Se algo parar, é aqui que você fica sabendo.')}
        direita={<SeloSaude geral={geral} />}
      />
      <ul className="mt-5 grid gap-3 sm:grid-cols-2">
        {linhas.map((l) => (
          <li key={l.rotulo} className="flex items-start gap-3 rounded-[12px] border border-line bg-ink/25 px-4 py-3.5">
            <span className={cx('mt-[7px] h-2 w-2 shrink-0 rounded-full', l.ok ? 'bg-ok' : 'bg-danger')} aria-hidden="true" />
            <div className="min-w-0">
              <div className="text-apoio font-[560] text-paper">
                {l.rotulo}
                {l.demo ? <Badge tone="amber" className="ml-2 px-1.5 py-0 text-rotulo">{t('Demonstração')}</Badge> : null}
                <span className="sr-only">{l.ok ? t(': funcionando') : t(': com problema')}</span>
              </div>
              <div className="t-label text-silver">{l.detalhe}</div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function SeloSaude({ geral, compacto = false }: { geral: ReturnType<typeof saudeGeral>; compacto?: boolean }) {
  if (geral === 'carregando') return <Badge>{t('Verificando…')}</Badge>
  return (
    <span
      className={cx(
        'inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-rotulo font-[520] whitespace-nowrap',
        geral === 'ok' ? 'border-ok/45 text-ok' : 'border-danger/50 text-danger-texto',
      )}
    >
      {compacto ? (
        <IconPulse width={14} height={14} />
      ) : (
        <span className={cx('h-1.5 w-1.5 rounded-full', geral === 'ok' ? 'bg-ok' : 'bg-danger')} aria-hidden="true" />
      )}
      {geral === 'ok' ? t('Tudo funcionando') : t('Precisa de atenção')}
    </span>
  )
}

/* ------------------------------------------------------------------ */
/* Grupo de controle: planejado                                        */
/* ------------------------------------------------------------------ */

export function GrupoDeControle() {
  return (
    <div className="anim-entrada flex h-full flex-col justify-between rounded-[18px] border border-dashed border-graphite/70 p-5">
      <div className="flex items-start justify-between gap-3">
        <span className="t-label text-silver">{t('Comparação com grupo de controle')}</span>
        <Badge>{t('Planejado')}</Badge>
      </div>
      <div className="mt-3">
        <p className="text-apoio leading-[1.5] text-paper">
          {t('Esta tela mostra o que foi recuperado e mantido. Medir quanto disso aconteceu por causa da CRAI exige comparar com clientes que não recebem a ação.')}
        </p>
        <p className="t-label mt-2 flex items-center gap-1.5 text-silver">
          <IconShield width={14} height={14} /> {t('Entra numa próxima versão.')}
        </p>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Extrato do mês                                                      */
/* ------------------------------------------------------------------ */

const ORIGEM = {
  involuntario: { rotulo: t('Involuntário'), cor: 'var(--color-serie-inv)' },
  voluntario: { rotulo: t('Voluntário'), cor: 'var(--color-serie-vol)' },
}

export function ExtratoDoMes({
  linhas,
  mes,
  incluirSimulados = false,
  piloto = false,
}: {
  linhas: LinhaExtrato[]
  mes: string
  incluirSimulados?: boolean
  /** A empresa está em período de piloto agora (quem define é a CRAI). */
  piloto?: boolean
}) {
  const [todas, setTodas] = useState(false)
  const [baixando, setBaixando] = useState(false)
  const [erroDoArquivo, setErroDoArquivo] = useState<string | null>(null)
  const visiveis = todas ? linhas : linhas.slice(0, 6)
  // Na demonstração o estorno é a própria linha, zerada. No backend é uma linha à parte, com
  // os valores negativos, no mês em que aconteceu: o total é a soma simples das linhas.
  const zerada = (l: LinhaExtrato) => l.estornado && l.tipo === undefined
  const total = linhas.reduce(
    (t, l) => ({ base: t.base + (zerada(l) ? 0 : l.valor_base), taxa: t.taxa + l.taxa, liquido: t.liquido + l.liquido }),
    { base: 0, taxa: 0, liquido: 0 },
  )
  const situacao = (l: LinhaExtrato) => (l.tipo === 'estorno' ? t('Estorno') : l.estornado ? t('Estornado') : t('Confirmado'))
  // A coluna "Taxa fora do piloto" aparece para a empresa em piloto e, depois dele, nos meses que
  // ainda têm linha de piloto. Em todo o resto o extrato é o de sempre.
  const dePiloto = (l: LinhaExtrato) => typeof l.taxa_fora_do_piloto === 'number'
  const comPiloto = piloto || linhas.some(dePiloto)
  const totalFora = linhas.reduce((t, l) => t + (l.taxa_fora_do_piloto ?? 0), 0)
  const colunas = comPiloto ? 7 : 6

  // Com o backend ligado, o arquivo é o do backend (a mesma conta do extrato, e o download fica
  // no registro de acesso). Na demonstração não há backend: o arquivo sai das linhas da tela.
  async function exportar() {
    setErroDoArquivo(null)
    setBaixando(true)
    try {
      const pronto = await api.extratoCsv({ incluirSimulados })
      if (pronto !== null) baixarCsvPronto(`crai-extrato-${mes}.csv`, pronto)
      else exportarDaTela()
    } catch (e) {
      setErroDoArquivo(e instanceof ErroApi ? e.message : t('Não deu para baixar o extrato agora. Tente de novo.'))
    }
    setBaixando(false)
  }

  function exportarDaTela() {
    baixarCsv(
      `crai-extrato-${mes}.csv`,
      [
        t('Data'),
        t('Cliente'),
        t('Origem'),
        t('O que aconteceu'),
        t('Valor (R$)'),
        t('Taxa da CRAI (R$)'),
        t('Líquido para você (R$)'),
        t('Situação'),
        t('Demonstração'),
      ],
      linhas.map((l) => [
        new Date(l.data).toLocaleDateString(localeAtual()),
        l.cliente,
        ORIGEM[l.origem].rotulo,
        l.descricao,
        l.valor_base,
        l.taxa,
        l.liquido,
        situacao(l),
        l.simulado ? t('Sim') : t('Não'),
      ]),
    )
  }

  return (
    <div>
      <div className="px-5 pt-5">
        <CabecalhoCartao
          titulo={t('Extrato de {mes}', { mes: fmt.mesPorExtenso(mes) })}
          apoio={t('Cada valor recuperado ou mantido, com a taxa da CRAI. É a memória de cálculo da sua fatura.')}
          direita={
            <Button variant="ghost" size="sm" onClick={exportar} disabled={linhas.length === 0 || baixando}>
              <IconDownload width={15} height={15} /> {baixando ? t('Baixando…') : t('Exportar CSV')}
            </Button>
          }
        />
        {piloto ? (
          <p className="t-label mt-2 text-silver" data-piloto>
            {t('Período de piloto: sem taxa')}
          </p>
        ) : null}
        {erroDoArquivo ? (
          <p role="alert" className="mt-3 rounded-[10px] border border-danger/40 bg-danger/[0.06] px-3 py-2 text-apoio text-danger-aviso">
            {erroDoArquivo}
          </p>
        ) : null}
      </div>
      <div className="scroll-fino mt-4 overflow-x-auto">
        <table className={cx('w-full text-left text-apoio', comPiloto ? 'min-w-[900px]' : 'min-w-[760px]')}>
          <thead>
            <tr className="t-label text-silver">
              <th className="px-5 py-2.5 font-[500]">{t('Data')}</th>
              <th className="px-3 py-2.5 font-[500]">{t('Cliente')}</th>
              <th className="px-3 py-2.5 font-[500]">{t('O que aconteceu')}</th>
              <th className="px-3 py-2.5 text-right font-[500]">{t('Valor')}</th>
              <th className="px-3 py-2.5 text-right font-[500]">{t('Taxa da CRAI')}</th>
              {comPiloto ? <th className="px-3 py-2.5 text-right font-[500] whitespace-nowrap">{t('Taxa fora do piloto')}</th> : null}
              <th className="px-5 py-2.5 text-right font-[500] whitespace-nowrap">{t('Líquido para você')}</th>
            </tr>
          </thead>
          <tbody>
            {visiveis.length === 0 ? (
              <tr className="border-t border-line">
                <td colSpan={colunas} className="px-5 py-10 text-center text-silver">
                  {t('Nenhum valor recuperado ou mantido neste mês ainda.')}
                </td>
              </tr>
            ) : (
              visiveis.map((l) => (
                <tr key={l.id} className="border-t border-line">
                  <td className="tabular px-5 py-3 whitespace-nowrap text-silver">{fmt.dataCurta(l.data)}</td>
                  <td className="px-3 py-3">
                    <div className="flex items-center gap-2">
                      <span className="font-[560] text-paper">{l.cliente}</span>
                      {l.simulado ? (
                        <Badge tone="amber" className="px-1.5 py-0 text-rotulo">
                          {t('Demonstração')}
                        </Badge>
                      ) : null}
                    </div>
                    <div className="t-label flex items-center gap-1.5 text-silver">
                      <span className="h-2 w-2 rounded-[2px]" style={{ background: ORIGEM[l.origem].cor }} aria-hidden="true" />
                      {ORIGEM[l.origem].rotulo}
                    </div>
                  </td>
                  <td className="px-3 py-3 text-silver">
                    {l.estornado ? <Badge className="mr-2">{situacao(l)}</Badge> : null}
                    {l.descricao}
                  </td>
                  <td className={cx('tabular px-3 py-3 text-right whitespace-nowrap', zerada(l) ? 'text-muted line-through' : 'text-paper')}>
                    {l.valor_base < 0 ? `− ${fmt.brl(-l.valor_base)}` : fmt.brl(l.valor_base)}
                  </td>
                  <td className="tabular px-3 py-3 text-right whitespace-nowrap text-silver">
                    {l.taxa > 0 ? `− ${fmt.brl(l.taxa)}` : l.taxa < 0 ? `+ ${fmt.brl(-l.taxa)}` : '—'}
                  </td>
                  {comPiloto ? (
                    <td className="tabular px-3 py-3 text-right whitespace-nowrap text-silver">
                      {!dePiloto(l) ? '—' : (l.taxa_fora_do_piloto ?? 0) < 0 ? `− ${fmt.brl(-(l.taxa_fora_do_piloto ?? 0))}` : fmt.brl(l.taxa_fora_do_piloto ?? 0)}
                    </td>
                  ) : null}
                  <td className="tabular px-5 py-3 text-right font-[600] whitespace-nowrap text-paper">
                    {l.liquido < 0 ? `− ${fmt.brl(-l.liquido)}` : fmt.brl(l.liquido)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
          {linhas.length ? (
            <tfoot>
              <tr className="border-t border-graphite/60">
                <td colSpan={3} className="px-5 py-3">
                  {linhas.length > 6 ? (
                    <button type="button" onClick={() => setTodas((t) => !t)} className="t-label font-[560] text-silver hover:text-paper">
                      {todas ? t('Mostrar só as 6 mais recentes') : t('Mostrar as {n} linhas', { n: linhas.length })}
                    </button>
                  ) : null}
                </td>
                <td className="tabular px-3 py-3 text-right text-silver">{fmt.brl(total.base)}</td>
                <td className="tabular px-3 py-3 text-right whitespace-nowrap text-silver">
                  {total.taxa < 0 ? `+ ${fmt.brl(-total.taxa)}` : total.taxa === 0 && comPiloto ? fmt.brl(0) : `− ${fmt.brl(total.taxa)}`}
                </td>
                {comPiloto ? <td className="tabular px-3 py-3 text-right whitespace-nowrap text-silver">{totalFora < 0 ? `− ${fmt.brl(-totalFora)}` : fmt.brl(totalFora)}</td> : null}
                <td className="tabular px-5 py-3 text-right font-[680] whitespace-nowrap text-paper">{total.liquido < 0 ? `− ${fmt.brl(-total.liquido)}` : fmt.brl(total.liquido)}</td>
              </tr>
            </tfoot>
          ) : null}
        </table>
      </div>
      <p className="t-label px-5 pt-1 pb-5 text-muted">
        {t('Voluntário: conta a mensalidade de quem aceitou a oferta, menos o desconto dado. Se o cliente cancelar dentro do prazo, o valor é estornado no mês do cancelamento.')}
        {comPiloto ? t(' Taxa fora do piloto: o que a CRAI cobraria fora do período de piloto. Esse valor não é cobrado.') : ''}
      </p>
    </div>
  )
}

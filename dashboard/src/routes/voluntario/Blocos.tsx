import { useRef, useState, type DragEvent } from 'react'
import { IconCheck, IconDownload, IconSpark, IconTable } from '../../components/icons/Icons'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Vazio } from '../../components/ui/Estados'
import { ErroApi, agoraDaTela, api } from '../../data/api'
import type { BaseClientes, ClienteRisco, ComparacaoReguaModelo, FaixaRisco, ResultadoImportacao } from '../../data/tipos'
import { baixarCsv } from '../../lib/csv'
import { cx } from '../../lib/cx'
import { fmt } from '../../lib/format'
import { t } from '../../lib/idioma'

/* ------------------------------------------------------------------ */
/* Faixa de risco: sempre ponto + texto                                */
/* ------------------------------------------------------------------ */

export const FAIXA: Record<FaixaRisco, { rotulo: string; classe: string; ponto: string; ordem: number }> = {
  grave: { rotulo: t('Grave'), classe: 'border-danger/50 text-danger-texto', ponto: 'bg-danger', ordem: 0 },
  preocupante: { rotulo: t('Preocupante'), classe: 'border-warn/50 text-warn', ponto: 'bg-warn', ordem: 1 },
  sem_risco: { rotulo: t('Sem risco'), classe: 'border-ok/45 text-ok', ponto: 'bg-ok', ordem: 2 },
  sem_dado: { rotulo: t('Sem dado suficiente'), classe: 'border-line text-silver', ponto: 'bg-graphite', ordem: 3 },
}

export function FaixaPill({ faixa, className }: { faixa: FaixaRisco; className?: string }) {
  const f = FAIXA[faixa]
  return (
    <span className={cx('inline-flex items-center gap-2 rounded-full border px-2.5 py-1 text-rotulo font-[520] whitespace-nowrap', f.classe, className)}>
      <span className={cx('h-1.5 w-1.5 rounded-full', f.ponto)} aria-hidden="true" />
      {f.rotulo}
    </span>
  )
}

const STATUS_OFERTA = {
  aguardando: { rotulo: t('A enviar'), classe: 'text-silver' },
  enviada: { rotulo: t('Enviada'), classe: 'text-amber' },
  aceita: { rotulo: t('Aceita'), classe: 'text-ok' },
  recusada: { rotulo: t('Recusada'), classe: 'text-silver' },
}

/* ------------------------------------------------------------------ */
/* Tabela dos 10 clientes                                              */
/* ------------------------------------------------------------------ */

type Filtro = FaixaRisco | 'todos'

interface PropsDaTabela {
  clientes: ClienteRisco[] | null
  total: number | null
  /** O cliente que a busca do topo pediu: vai para o topo da lista, destacado. */
  buscado?: ClienteRisco | null
}

export function TabelaClientes({ clientes: recentes, total, buscado = null }: PropsDaTabela) {
  const [filtro, setFiltro] = useState<Filtro>('todos')
  const clientes = recentes === null ? null : buscado ? [buscado, ...recentes.filter((c) => c.id !== buscado.id)] : recentes
  const lista = (clientes ?? []).filter((c) => filtro === 'todos' || c.faixa === filtro)
  const contagem = (f: Filtro) => (clientes ?? []).filter((c) => f === 'todos' || c.faixa === f).length
  const filtros: { valor: Filtro; rotulo: string }[] = [
    { valor: 'todos', rotulo: t('Todos') },
    { valor: 'grave', rotulo: t('Grave') },
    { valor: 'preocupante', rotulo: t('Preocupante') },
    { valor: 'sem_risco', rotulo: t('Sem risco') },
    { valor: 'sem_dado', rotulo: t('Sem dado') },
  ]
  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-4">
        <div>
          <h3 className="t-h3 text-paper">{t('Últimos 10 clientes atualizados')}</h3>
          <p className="t-apoio mt-0.5 text-silver">{t('Do mais recente ao mais antigo. O motivo é o que pesou na avaliação; a abordagem é o que o sistema fez.')}</p>
          <p className="t-label mt-1 text-muted">{t('Grave é o topo 10% da base pelo risco; Preocupante, os 20% seguintes. Sempre com um sinal real de abandono.')}</p>
        </div>
        <div className="flex flex-wrap gap-1.5" role="tablist" aria-label={t('Filtrar por faixa')}>
          {filtros.map((f) => (
            <button
              key={f.valor}
              type="button"
              role="tab"
              aria-selected={filtro === f.valor}
              onClick={() => setFiltro(f.valor)}
              className={cx(
                'rounded-full border px-3 py-1.5 text-rotulo font-[520] transition-colors',
                filtro === f.valor ? 'border-orange/60 bg-orange/10 text-orange' : 'border-line text-silver hover:border-graphite hover:text-paper',
              )}
            >
              {f.rotulo}
              <span className="tabular ml-1.5 text-rotulo opacity-70">{contagem(f.valor)}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="scroll-fino overflow-x-auto">
        <table className="w-full min-w-[900px] text-left">
          <thead>
            <tr className="t-label text-silver">
              <th className="px-5 py-3 font-[500]">{t('Cliente')}</th>
              <th className="px-3 py-3 font-[500]">{t('Mensalidade')}</th>
              <th className="px-3 py-3 font-[500]">{t('Risco')}</th>
              <th className="px-3 py-3 font-[500]">{t('Motivo')}</th>
              <th className="px-3 py-3 font-[500]">{t('Abordagem')}</th>
              <th className="px-5 py-3 font-[500]">{t('Quem decidiu')}</th>
            </tr>
          </thead>
          <tbody>
            {clientes === null ? (
              Array.from({ length: 6 }).map((_, i) => (
                <tr key={i} className="border-t border-line">
                  <td colSpan={6} className="px-5 py-4">
                    <div className="h-4 w-2/3 animate-pulse rounded bg-paper/[0.06]" />
                  </td>
                </tr>
              ))
            ) : lista.length === 0 ? (
              <tr className="border-t border-line">
                <td colSpan={6}>
                  {clientes.length === 0 ? (
                    <Vazio titulo={t('Nenhum cliente avaliado ainda')} texto={t('Anexe a base na aba “Sua base” ou ligue a API. Assim que a base entrar, os clientes aparecem aqui com a faixa de risco e o motivo.')} icone={<IconTable width={22} height={22} />} />
                  ) : (
                    <Vazio titulo={t('Nenhum cliente nesta faixa')} texto={t('Tente outra faixa.')} />
                  )}
                </td>
              </tr>
            ) : (
              lista.map((c) => (
                <tr key={c.id} data-buscado={buscado?.id === c.id || undefined} className={cx('border-t border-line align-top transition-colors hover:bg-paper/[0.035]', buscado?.id === c.id && 'bg-amber/[0.07]')}>
                  <td className="px-5 py-3.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-[560] text-paper">{c.nome}</span>
                      {buscado?.id === c.id ? <Badge tone="orange">{t('Buscado')}</Badge> : null}
                      {c.nao_contatar ? (
                        <span title={t('Pediu para não receber mensagens, ou foi marcado pela empresa. Nenhuma mensagem sai para este cliente.')}>
                          <Badge tone="amber">{t('Não contatar')}</Badge>
                        </span>
                      ) : null}
                      {c.simulado ? <Badge tone="amber">{t('Demonstração')}</Badge> : null}
                    </div>
                    {c.atualizado_em ? <div className="t-label text-muted">{t('Atualizado {quando}', { quando: fmt.relativo(c.atualizado_em, agoraDaTela()) })}</div> : null}
                  </td>
                  <td className="tabular px-3 py-3.5 font-[560] whitespace-nowrap text-paper">{c.mrr === null ? '—' : fmt.brl(c.mrr)}</td>
                  <td className="px-3 py-3.5"><FaixaPill faixa={c.faixa} /></td>
                  <td className="max-w-[300px] px-3 py-3.5 text-apoio leading-[1.45] text-silver">{c.motivo}</td>
                  <td className="px-3 py-3.5">
                    {c.abordagem ? (
                      <>
                        <div className="text-apoio text-paper">{c.abordagem.oferta}</div>
                        <div className="t-label text-silver">
                          {c.abordagem.canal} · <span className={STATUS_OFERTA[c.abordagem.status].classe}>{STATUS_OFERTA[c.abordagem.status].rotulo}</span>
                        </div>
                      </>
                    ) : (
                      <span className="t-label text-muted">{c.faixa === 'sem_dado' ? t('Aguardando dados') : t('Nenhuma; monitorado')}</span>
                    )}
                  </td>
                  <td className="px-5 py-3.5">
                    {c.risco_decidido_por ? (
                      <>
                        <QuemDecidiu por={c.risco_decidido_por} />
                        <div className="t-label mt-1 text-silver">
                          {c.risco_decidido_por === 'modelo' ? t('Pelo comportamento') : t('Por regras fixas')}
                          {c.posicao_na_base !== null && total ? t(' · {posicao}º de {total} pelo risco', { posicao: c.posicao_na_base, total: fmt.numero(total) }) : ''}
                        </div>
                      </>
                    ) : (
                      <span className="t-label text-muted">{c.faixa === 'sem_dado' ? t('Sem avaliação') : t('Não registrado')}</span>
                    )}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function QuemDecidiu({ por }: { por: 'modelo' | 'regua' }) {
  return por === 'modelo' ? (
    <Badge tone="orange" className="gap-1.5">
      <IconSpark width={12} height={12} /> {t('Modelo de IA')}
    </Badge>
  ) : (
    <Badge className="gap-1.5">
      <IconTable width={12} height={12} /> {t('Régua')}
    </Badge>
  )
}

/* ------------------------------------------------------------------ */
/* Sua base                                                            */
/* ------------------------------------------------------------------ */

const ORIGEM_DA_BASE = { api: t('Pela API'), anexo: t('Por anexo') }

/**
 * As colunas que a importação aceita, na ordem do contrato (`crai/churn_voluntary/importacao.py`
 * e `docs/CONTRATO_CLIENTES_API.md`): as três obrigatórias primeiro, depois as opcionais.
 */
export const COLUNAS_DA_PLANILHA = [
  'customer_id_externo',
  'mrr',
  'billing_profile',
  'days_since_last',
  'features_used_30d',
  'email',
  'id_recorrencia',
  'logins_7d',
  'logins_30d',
  'avg_session_min',
  'api_calls_7d',
  'tickets_30d',
  'failed_pay_90d',
  'nps_last',
  'seats',
  'tenure_days',
  'telefone',
  'nome',
]
/** Uma linha de exemplo, inventada, no formato que o backend lê (número em pt-BR, perfil CLT, PJ ou freelancer). */
export const EXEMPLO_DA_PLANILHA = ['cli-001', '480,00', 'PJ', '3', '5', 'contato@exemplo.com.br', 'RN_exemplo_001', '4', '15', '12,5', '0', '1', '0', '8', '3', '400', '+5511999990000', 'Cliente de exemplo (apague esta linha)']
export const NOME_DA_PLANILHA = 'modelo-base-de-clientes.csv'

/** A planilha modelo é montada no navegador: o cabeçalho que a importação aceita e uma linha de exemplo. */
export function baixarPlanilhaModelo() {
  baixarCsv(NOME_DA_PLANILHA, COLUNAS_DA_PLANILHA, [EXEMPLO_DA_PLANILHA])
}

export function SuaBase({ base, aoImportar }: { base: BaseClientes | null; aoImportar?: () => void }) {
  const [arrastando, setArrastando] = useState(false)
  const [enviando, setEnviando] = useState(false)
  const [resultado, setResultado] = useState<ResultadoImportacao | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)

  async function receber(arquivo: File | undefined) {
    if (!arquivo) return
    if (!/\.(csv|xlsx)$/i.test(arquivo.name)) return setErro(t('Só CSV ou XLSX. Outros formatos não são lidos.'))
    if (arquivo.size > 25 * 1024 * 1024) return setErro(t('O arquivo passa de 25 MB. Divida em duas partes.'))
    setErro(null)
    setResultado(null)
    setEnviando(true)
    try {
      const r = await api.importarBase(arquivo)
      setResultado(r)
      // A base mudou: a página lê de novo os totais e a lista.
      if (!r.demonstracao && r.importados > 0) aoImportar?.()
    } catch (e) {
      setErro(e instanceof ErroApi ? e.message : t('Não foi possível enviar o arquivo. Tente de novo.'))
    } finally {
      setEnviando(false)
    }
  }
  function soltar(e: DragEvent) {
    e.preventDefault()
    setArrastando(false)
    void receber(e.dataTransfer.files[0])
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <div>
        <h3 className="t-h3 text-paper">{t('Sua base de clientes')}</h3>
        <p className="t-apoio mt-1 text-silver">
          {t('Se a sua empresa usa a API da CRAI, a base é atualizada sozinha. O anexo serve para começar ou para corrigir.')}
        </p>
        <dl className="mt-5 grid grid-cols-2 gap-3">
          <Dado rotulo={t('Clientes na base')} valor={base ? fmt.numero(base.total) : '—'} />
          <Dado rotulo={t('Com dados de comportamento')} valor={base ? fmt.numero(base.com_dados_comportamento) : '—'} apoio={base && base.total ? t('{pct}% da base; o risco é avaliado para estes', { pct: Math.round((base.com_dados_comportamento / base.total) * 100) }) : ''} />
          <Dado rotulo={t('Última atualização')} valor={base ? fmt.relativo(base.atualizada_em, agoraDaTela()).replace(/^./, (c) => c.toUpperCase()) : '—'} apoio={base ? (base.origem ? ORIGEM_DA_BASE[base.origem] : t('Origem não registrada')) : ''} />
          <Dado rotulo={t('O que a base precisa ter')} valor={t('Id do cliente e mensalidade')} apoio={t('Opcional: id da recorrência (liga ao involuntário), e-mail, telefone, uso, chamados')} pequeno />
        </dl>
        <button type="button" onClick={baixarPlanilhaModelo} className="t-label mt-4 inline-flex items-center gap-1.5 text-silver underline-offset-4 hover:text-paper hover:underline">
          <IconDownload width={14} height={14} /> {t('Baixar planilha modelo')}
        </button>
      </div>

      <div>
        <div
          onDragOver={(e) => {
            e.preventDefault()
            setArrastando(true)
          }}
          onDragLeave={() => setArrastando(false)}
          onDrop={soltar}
          className={cx(
            'flex min-h-[220px] flex-col items-center justify-center rounded-[16px] border border-dashed p-6 text-center transition-colors',
            arrastando ? 'border-amber bg-amber/[0.08]' : 'border-graphite/70 bg-ink/25',
          )}
        >
          <input ref={input} type="file" accept=".csv,.xlsx" className="sr-only" onChange={(e) => void receber(e.target.files?.[0])} aria-label={t('Anexar a base em CSV ou XLSX')} />
          <span className="flex h-11 w-11 items-center justify-center rounded-[12px] bg-paper/[0.06] text-amber">
            <IconTable width={22} height={22} />
          </span>
          <div className="mt-3 text-normal font-[560] text-paper">{enviando ? t('Lendo o arquivo…') : t('Arraste a base aqui')}</div>
          <p className="t-apoio mt-1 text-silver">{t('CSV ou XLSX, até 25 MB. Uma linha por cliente.')}</p>
          <Button variant="ghost" size="sm" className="mt-4" onClick={() => input.current?.click()} disabled={enviando}>
            {t('Escolher arquivo')}
          </Button>
          <p className="t-label mt-4 max-w-[380px] text-muted">
            {t('Os dados ficam só na sua conta. A CRAI usa a base para avaliar risco e falar com o cliente em seu nome; nunca para treinar modelos de outras empresas.')}
          </p>
        </div>
        {erro ? <p role="alert" className="mt-3 rounded-[10px] border border-danger/50 bg-danger/10 px-3 py-2 text-apoio text-danger-aviso">{erro}</p> : null}
        {resultado ? (
          <div role="status" className="mt-3 rounded-[14px] border border-ok/40 bg-ok/[0.06] p-4">
            <div className="flex items-center gap-2 text-apoio font-[600] text-ok">
              <IconCheck width={16} height={16} /> {t('Base recebida: {arquivo}', { arquivo: resultado.arquivo })}
            </div>
            <ul className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-apoio text-paper sm:grid-cols-4">
              <li><span className="tabular font-[640]">{fmt.numero(resultado.linhas)}</span> <span className="text-silver">{t('linhas')}</span></li>
              {resultado.demonstracao ? null : <li><span className="tabular font-[640]">{fmt.numero(resultado.importados)}</span> <span className="text-silver">{t('na base')}</span></li>}
              {resultado.demonstracao ? null : <li><span className="tabular font-[640]">{fmt.numero(resultado.rejeitados)}</span> <span className="text-silver">{t('recusadas')}</span></li>}
              {resultado.novos !== null ? <li><span className="tabular font-[640]">{resultado.novos}</span> <span className="text-silver">{t('novos')}</span></li> : null}
              {resultado.atualizados !== null ? <li><span className="tabular font-[640]">{resultado.atualizados}</span> <span className="text-silver">{t('corrigidos')}</span></li> : null}
              {resultado.sem_id_recorrencia !== null ? <li><span className="tabular font-[640]">{resultado.sem_id_recorrencia}</span> <span className="text-silver">{t('sem id da recorrência')}</span></li> : null}
              {resultado.sem_comportamento !== null ? <li><span className="tabular font-[640]">{fmt.numero(resultado.sem_comportamento)}</span> <span className="text-silver">{t('sem dado de comportamento')}</span></li> : null}
            </ul>
            {resultado.avisos.map((a) => (
              <p key={a} className="t-label mt-2 text-silver">{a}</p>
            ))}
            {resultado.demonstracao ? (
              <p className="t-label mt-2 text-muted">{t('Demonstração: o arquivo não sai do seu navegador. Na versão final, a base é enviada e a avaliação de risco roda em seguida.')}</p>
            ) : (
              <p className="t-label mt-2 text-muted">{t('A avaliação de risco já usa a base nova.')}</p>
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}

function Dado({ rotulo, valor, apoio, pequeno }: { rotulo: string; valor: string; apoio?: string; pequeno?: boolean }) {
  return (
    <div className="rounded-[12px] border border-line bg-ink/25 px-3.5 py-3">
      <dt className="t-label text-silver">{rotulo}</dt>
      <dd className={cx('mt-1 text-paper', pequeno ? 'text-apoio font-[560]' : 'text-[20px] font-[640] tracking-[-0.01em]')}>{valor}</dd>
      {apoio ? <dd className="t-label mt-1 text-muted">{apoio}</dd> : null}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Quem decide o risco + régua × modelo                                */
/* ------------------------------------------------------------------ */

export function QuemDecideORisco({ base, comparacao }: { base: BaseClientes | null; comparacao: ComparacaoReguaModelo | null }) {
  const pct = base && base.total ? Math.round((base.decididos_pelo_modelo / base.total) * 100) : null
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <div>
        <h3 className="t-h3 text-paper">{t('Quem decide o risco de cada cliente')}</h3>
        <p className="t-apoio mt-1 text-silver">{t('Dois jeitos de avaliar, e a tabela sempre mostra qual foi usado e a posição do cliente na base.')}</p>
        <ul className="mt-5 flex flex-col gap-3">
          <li className="rounded-[14px] border border-orange/30 bg-orange/[0.05] p-4">
            <div className="flex items-center gap-2">
              <QuemDecidiu por="modelo" />
              <span className="t-label text-silver">{pct !== null ? t('{pct}% da base', { pct }) : ''}</span>
            </div>
            <p className="mt-2 text-apoio leading-[1.5] text-paper">
              {t('Um modelo de IA aprende com o comportamento: uso, chamados, atrasos, tempo de casa. Decide quando o cliente tem esses dados.')}
            </p>
          </li>
          <li className="rounded-[14px] border border-line bg-ink/25 p-4">
            <div className="flex items-center gap-2">
              <QuemDecidiu por="regua" />
              <span className="t-label text-silver">{pct !== null ? t('{pct}% da base', { pct: 100 - pct }) : ''}</span>
            </div>
            <p className="mt-2 text-apoio leading-[1.5] text-paper">
              {t('Uma régua de regras fixas (atraso, chamados, queda de uso). Decide quando ainda não há dados de comportamento, como em clientes novos. É previsível, mas avisa menos cedo.')}
            </p>
          </li>
        </ul>
        <div className="mt-4 rounded-[14px] border border-line bg-ink/25 p-4">
          <h4 className="t-h3 text-paper">{t('Como as faixas são definidas')}</h4>
          <ul className="mt-2 flex flex-col gap-1.5 text-apoio leading-[1.5] text-paper">
            <li className="flex gap-2"><span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-danger" aria-hidden="true" /><span><span className="font-[600]">{t('Grave:')}</span> {t('os 10% da base com mais risco, e só com um sinal real de abandono (uso caindo, chamados, atraso).')}</span></li>
            <li className="flex gap-2"><span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-warn" aria-hidden="true" /><span><span className="font-[600]">{t('Preocupante:')}</span> {t('os 20% seguintes, também com sinal real.')}</span></li>
            <li className="flex gap-2"><span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-ok" aria-hidden="true" /><span><span className="font-[600]">{t('Sem risco:')}</span> {t('o resto da base com dados.')} <span className="font-[600]">{t('Sem dado suficiente:')}</span> {t('ainda não dá para avaliar.')}</span></li>
          </ul>
          <p className="t-label mt-3 text-silver">{t('Clientes de mensalidade alta entram como Preocupante e só sobem para Grave se os sinais continuarem: uma oferta cara feita cedo demais custa margem sem precisar.')}</p>
        </div>
      </div>
      <div>
        <h3 className="t-h3 text-paper">{t('Régua × modelo nos últimos {dias} dias', { dias: comparacao?.dias ?? 30 })}</h3>
        <p className="t-apoio mt-1 text-silver">
          {comparacao
            ? t('Entre os {clientes} clientes com dados de comportamento, {cancelamentos} cancelaram. Quem tinha avisado antes?', {
                clientes: fmt.numero(comparacao.clientes_com_dados),
                cancelamentos: comparacao.cancelamentos,
              })
            : ''}
        </p>
        {!comparacao ? (
          <p className="mt-5 rounded-[12px] border border-line bg-ink/25 px-3.5 py-3 text-apoio leading-[1.5] text-silver">
            {t('A comparação aparece quando o modelo de IA estiver avaliando a sua base e houver cancelamentos suficientes no período para comparar. Até lá, a tabela mostra quem decidiu o risco de cada cliente.')}
          </p>
        ) : null}
        {comparacao ? (
          <div className="mt-5 flex flex-col gap-5">
            <Comparativo
              rotulo={t('Avisou antes do cancelamento')}
              apoio={t('De {n} cancelamentos', { n: comparacao.cancelamentos })}
              regua={comparacao.regua.avisou_antes}
              modelo={comparacao.modelo.avisou_antes}
              max={comparacao.cancelamentos}
              melhorMaior
            />
            <Comparativo
              rotulo={t('Marcou como grave')}
              apoio={t('Quanto menor, menos ofertas desnecessárias')}
              regua={comparacao.regua.marcou_grave}
              modelo={comparacao.modelo.marcou_grave}
              max={Math.max(comparacao.regua.marcou_grave, comparacao.modelo.marcou_grave)}
            />
            <p className="rounded-[12px] border border-line bg-ink/25 px-3.5 py-3 text-apoio leading-[1.5] text-paper">
              {t('O modelo tinha marcado {modeloAvisou} dos {cancelamentos} cancelamentos, com {modeloGraves} clientes como graves. A régua tinha marcado {reguaAvisou}, com {reguaGraves} como graves. As duas avaliam a mesma base, com os últimos dados de cada cliente.', {
                modeloAvisou: comparacao.modelo.avisou_antes,
                cancelamentos: comparacao.cancelamentos,
                modeloGraves: fmt.numero(comparacao.modelo.marcou_grave),
                reguaAvisou: comparacao.regua.avisou_antes,
                reguaGraves: fmt.numero(comparacao.regua.marcou_grave),
              })}
            </p>
          </div>
        ) : null}
      </div>
    </div>
  )
}

function Comparativo({ rotulo, apoio, regua, modelo, max, melhorMaior }: { rotulo: string; apoio: string; regua: number; modelo: number; max: number; melhorMaior?: boolean }) {
  const linha = (nome: string, v: number, cor: string, destaque: boolean) => (
    <div className="flex items-center gap-3">
      <span className="w-[88px] shrink-0 text-rotulo text-silver">{nome}</span>
      <div className="h-2 flex-1 rounded-full bg-paper/[0.06]" aria-hidden="true">
        <div className="h-2 rounded-full" style={{ width: `${max ? (v / max) * 100 : 0}%`, background: cor }} />
      </div>
      <span className={cx('tabular w-14 shrink-0 text-right text-apoio', destaque ? 'font-[680] text-paper' : 'font-[520] text-silver')}>
        {v}{melhorMaior ? t(' de {max}', { max }) : ''}
      </span>
    </div>
  )
  const modeloMelhor = melhorMaior ? modelo >= regua : modelo <= regua
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-apoio font-[560] text-paper">{rotulo}</span>
        <span className="t-label text-muted">{apoio}</span>
      </div>
      <div className="mt-2 flex flex-col gap-2">
        {linha(t('Modelo de IA'), modelo, 'var(--color-serie-vol)', modeloMelhor)}
        {linha(t('Régua'), regua, 'var(--color-silver)', !modeloMelhor)}
      </div>
    </div>
  )
}

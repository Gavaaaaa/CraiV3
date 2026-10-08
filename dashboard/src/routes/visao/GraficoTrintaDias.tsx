import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { IconChart, IconTable } from '../../components/icons/Icons'
import type { PontoSerieDupla } from '../../data/tipos'
import { cx } from '../../lib/cx'
import { fmt } from '../../lib/format'
import { localeAtual, t } from '../../lib/idioma'

/*
 * Gráfico de 30 dias, em dois painéis que dividem o mesmo eixo de datas:
 *   em cima, o acumulado (uma linha); embaixo, o valor de cada dia (colunas empilhadas).
 * Os dois painéis têm escalas próprias. Linha acumulada e colunas diárias no MESMO
 * painel exigiriam dois eixos verticais, e isso faz o leitor ver relações que não existem.
 */

const INV = 'var(--color-serie-inv)'
const VOL = 'var(--color-serie-vol)'
const LINHA_ACUM = 'var(--color-paper)'

/** O menor tamanho de letra do painel (o mesmo `--text-rotulo` de index.css): vale também no gráfico. */
const LETRA = 13.5
// A margem esquerda guarda os valores do eixo ("R$ 12,5 mil") no tamanho da letra acima.
const M = { esq: 78, dir: 20 }
const TOPO = 24
const ALT_ACUM = 112
const ESPACO = 46
const ALT_DIA = 196
const ALT_EIXO = 30
const ALT_TOTAL = TOPO + ALT_ACUM + ESPACO + ALT_DIA + ALT_EIXO

const compacto = new Intl.NumberFormat(localeAtual(), { style: 'currency', currency: 'BRL', notation: 'compact', maximumFractionDigits: 1 })
const eixo = (v: number) => (v === 0 ? 'R$ 0' : compacto.format(v))

/** Topo "redondo" do eixo, com passos 1, 2, 2,5 ou 5 vezes uma potência de 10. */
function escala(max: number, marcas = 3): number[] {
  if (max <= 0) return [0, 1]
  const bruto = max / marcas
  const pot = 10 ** Math.floor(Math.log10(bruto))
  const passo = [1, 2, 2.5, 5, 10].map((m) => m * pot).find((p) => p >= bruto)!
  const n = Math.ceil(max / passo)
  return Array.from({ length: n + 1 }, (_, i) => i * passo)
}

const MESES = [t('jan'), t('fev'), t('mar'), t('abr'), t('mai'), t('jun'), t('jul'), t('ago'), t('set'), t('out'), t('nov'), t('dez')]
const diaMes = (dia: string) => {
  const [, m, d] = dia.split('-').map(Number)
  return t('{dia} {mes}', { dia: d, mes: MESES[m - 1] })
}

/** Coluna com o topo arredondado (4px) e a base reta. */
function colunaTopoRedondo(x: number, y: number, w: number, h: number, r = 4) {
  const rr = Math.min(r, h, w / 2)
  return `M${x},${y + h} V${y + rr} Q${x},${y} ${x + rr},${y} H${x + w - rr} Q${x + w},${y} ${x + w},${y + rr} V${y + h} Z`
}

type Chave = 'involuntario' | 'voluntario'
export interface SerieCfg {
  chave: Chave
  rotulo: string
  cor: string
}
const SERIES_PADRAO: SerieCfg[] = [
  { chave: 'involuntario', rotulo: t('Involuntário (cobranças recuperadas)'), cor: INV },
  { chave: 'voluntario', rotulo: t('Voluntário (clientes mantidos)'), cor: VOL },
]

interface Props {
  pontos: PontoSerieDupla[]
  /** Atalho da visão geral: as duas séries, ou só o involuntário. */
  comVoluntario?: boolean
  /** Ou a lista explícita de séries (uma ou duas). */
  series?: SerieCfg[]
  titulo?: string
  subtitulo?: string
}

export function GraficoTrintaDias({ pontos, comVoluntario = true, series: seriesProp, titulo, subtitulo }: Props) {
  const series = seriesProp ?? (comVoluntario ? SERIES_PADRAO : [SERIES_PADRAO[0]])
  const s1 = series[0]
  const s2 = series[1] ?? null
  const caixa = useRef<HTMLDivElement>(null)
  const [largura, setLargura] = useState(720)
  const [ativo, setAtivo] = useState<number | null>(null)
  const [tabela, setTabela] = useState(false)

  useEffect(() => {
    const el = caixa.current
    // Sem `ResizeObserver` (navegador muito antigo, ou o jsdom dos testes), fica a largura padrão.
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(([e]) => setLargura(Math.max(240, Math.floor(e.contentRect.width))))
    ro.observe(el)
    return () => ro.disconnect()
  }, [tabela])

  const dados = useMemo(() => {
    let acum = 0
    return pontos.map((p) => {
      const v1 = p[s1.chave]
      const v2 = s2 ? p[s2.chave] : 0
      const total = v1 + v2
      acum += total
      return { dia: p.dia, v1, v2, total, acum }
    })
  }, [pontos, s1, s2])

  const larguraPlot = largura - M.esq - M.dir
  const banda = larguraPlot / Math.max(1, dados.length)
  const larguraColuna = Math.min(24, Math.max(4, banda * 0.62))
  const cx0 = (i: number) => M.esq + banda * i + banda / 2

  const marcasDia = escala(Math.max(...dados.map((d) => d.total), 1))
  const topoDia = marcasDia[marcasDia.length - 1]
  const marcasAcum = escala(Math.max(dados[dados.length - 1]?.acum ?? 0, 1), 2)
  const topoAcum = marcasAcum[marcasAcum.length - 1]

  const baseAcum = TOPO + ALT_ACUM
  const yAcum = (v: number) => baseAcum - (v / topoAcum) * (ALT_ACUM - 8)
  const baseDia = baseAcum + ESPACO + ALT_DIA
  const yDia = (v: number) => baseDia - (v / topoDia) * (ALT_DIA - 8)

  const caminhoAcum = dados.map((d, i) => `${i ? 'L' : 'M'}${cx0(i).toFixed(1)},${yAcum(d.acum).toFixed(1)}`).join(' ')
  const areaAcum = `${caminhoAcum} L${cx0(dados.length - 1).toFixed(1)},${baseAcum} L${cx0(0).toFixed(1)},${baseAcum} Z`
  const ultimo = dados[dados.length - 1]

  function indicePelo(e: PointerEvent<SVGSVGElement>) {
    const r = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - r.left) / r.width) * largura
    const i = Math.floor((x - M.esq) / banda)
    return Math.min(dados.length - 1, Math.max(0, i))
  }

  function teclado(e: KeyboardEvent<SVGSVGElement>) {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      e.preventDefault()
      const passo = e.key === 'ArrowLeft' ? -1 : 1
      setAtivo((a) => Math.min(dados.length - 1, Math.max(0, (a ?? dados.length - 1) + passo)))
    } else if (e.key === 'Home') setAtivo(0)
    else if (e.key === 'End') setAtivo(dados.length - 1)
    else if (e.key === 'Escape') setAtivo(null)
  }

  const p = ativo !== null ? dados[ativo] : null
  const tipX = ativo !== null ? (cx0(ativo) / largura) * 100 : 0
  const tipADireita = ativo !== null && ativo < dados.length * 0.62

  const cadaQuantos = largura < 520 ? 10 : 5
  const marcasX = dados
    .map((d, i) => ({ i, d }))
    .filter(({ i }) => (i % cadaQuantos === 0 && dados.length - 1 - i >= cadaQuantos / 2) || i === dados.length - 1)

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="t-h3 text-paper">{titulo ?? t('Dinheiro mantido nos últimos 30 dias')}</h3>
          <p className="t-apoio mt-1 text-silver">{subtitulo ?? t('Já descontada a taxa da CRAI. Passe o mouse ou use as setas do teclado para ver cada dia.')}</p>
        </div>
        <button
          type="button"
          onClick={() => setTabela((t) => !t)}
          className="inline-flex h-8 items-center gap-1.5 rounded-[8px] border border-line px-2.5 text-rotulo font-[520] text-silver transition-colors hover:border-graphite hover:text-paper"
        >
          {tabela ? <IconChart width={15} height={15} /> : <IconTable width={15} height={15} />}
          {tabela ? t('Ver como gráfico') : t('Ver como tabela')}
        </button>
      </div>

      {/* Legenda: retângulo para colunas, traço para a linha */}
      <ul className="t-label mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-silver" aria-label={t('Legenda')}>
        <li className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: s1.cor }} aria-hidden="true" />
          {s1.rotulo}
        </li>
        {s2 ? (
          <li className="flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: s2.cor }} aria-hidden="true" />
            {s2.rotulo}
          </li>
        ) : null}
        <li className="flex items-center gap-2">
          <span className="h-[2px] w-4 rounded-full" style={{ background: LINHA_ACUM }} aria-hidden="true" />
          {t('Acumulado')}
        </li>
      </ul>

      {tabela ? (
        <div className="scroll-fino mt-4 max-h-[400px] overflow-auto rounded-[12px] border border-line">
          <table className="w-full text-left text-apoio">
            <thead className="sticky top-0 bg-card">
              <tr className="t-label text-silver">
                <th className="px-4 py-2.5 font-[500]">{t('Dia')}</th>
                <th className="px-3 py-2.5 text-right font-[500]">{s1.rotulo.split(' (')[0]}</th>
                {s2 ? <th className="px-3 py-2.5 text-right font-[500]">{s2.rotulo.split(' (')[0]}</th> : null}
                {s2 ? <th className="px-3 py-2.5 text-right font-[500]">{t('Total do dia')}</th> : null}
                <th className="px-4 py-2.5 text-right font-[500]">{t('Acumulado')}</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {dados.map((d) => (
                <tr key={d.dia} className={cx('border-t border-line', d.total === 0 && 'text-muted')}>
                  <td className="px-4 py-2 text-silver">{diaMes(d.dia)}</td>
                  <td className="px-3 py-2 text-right">{d.v1 ? fmt.brl(d.v1) : '—'}</td>
                  {s2 ? <td className="px-3 py-2 text-right">{d.v2 ? fmt.brl(d.v2) : '—'}</td> : null}
                  {s2 ? <td className="px-3 py-2 text-right text-paper">{d.total ? fmt.brl(d.total) : '—'}</td> : null}
                  <td className="px-4 py-2 text-right text-silver">{fmt.brl(d.acum)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={caixa} className="relative mt-3 flex-1">
          <svg
            width="100%"
            height={ALT_TOTAL}
            viewBox={`0 0 ${largura} ${ALT_TOTAL}`}
            role="img"
            tabIndex={0}
            aria-label={t('Dinheiro mantido por dia nos últimos 30 dias. Total acumulado: {total}. Use as setas para percorrer os dias.', { total: fmt.brl(ultimo?.acum ?? 0) })}
            className="block touch-pan-y rounded-[8px] outline-none focus-visible:outline-2 focus-visible:outline-amber"
            onPointerMove={(e) => setAtivo(indicePelo(e))}
            onPointerDown={(e) => setAtivo(indicePelo(e))}
            onPointerLeave={(e) => e.pointerType === 'mouse' && setAtivo(null)}
            onFocus={() => setAtivo((a) => a ?? dados.length - 1)}
            onBlur={() => setAtivo(null)}
            onKeyDown={teclado}
          >
            {/* Grade: linhas finas, sólidas, discretas */}
            <g className="tabular" fontSize={LETRA} fill="var(--color-silver)">
              {marcasAcum.map((v) => (
                <g key={`a${v}`}>
                  <line x1={M.esq} x2={largura - M.dir} y1={yAcum(v)} y2={yAcum(v)} stroke="var(--color-line)" />
                  <text x={M.esq - 10} y={yAcum(v)} dy="0.32em" textAnchor="end">
                    {eixo(v)}
                  </text>
                </g>
              ))}
              {marcasDia.map((v) => (
                <g key={`d${v}`}>
                  <line x1={M.esq} x2={largura - M.dir} y1={yDia(v)} y2={yDia(v)} stroke="var(--color-line)" />
                  <text x={M.esq - 10} y={yDia(v)} dy="0.32em" textAnchor="end">
                    {eixo(v)}
                  </text>
                </g>
              ))}
            </g>

            {/* Títulos dos painéis */}
            <text x={M.esq} y={13} fontSize={LETRA} fontWeight={560} fill="var(--color-silver)">
              {t('Acumulado')}
            </text>
            <text x={M.esq} y={baseAcum + ESPACO - 14} fontSize={LETRA} fontWeight={560} fill="var(--color-silver)">
              {t('Por dia')}
            </text>

            {/* Painel de cima: acumulado */}
            <path d={areaAcum} fill={LINHA_ACUM} fillOpacity={0.06} />
            <path d={caminhoAcum} fill="none" stroke={LINHA_ACUM} strokeOpacity={0.9} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            {ultimo ? (
              <>
                <circle cx={cx0(dados.length - 1)} cy={yAcum(ultimo.acum)} r={6} fill="var(--color-card)" />
                <circle cx={cx0(dados.length - 1)} cy={yAcum(ultimo.acum)} r={4} fill={LINHA_ACUM} />
                <text
                  x={cx0(dados.length - 1) - 10}
                  y={yAcum(ultimo.acum) - 12}
                  textAnchor="end"
                  fontSize={LETRA}
                  fontWeight={620}
                  fill="var(--color-paper)"
                >
                  {fmt.brlInteiro(ultimo.acum)}
                </text>
              </>
            ) : null}

            {/* Painel de baixo: colunas empilhadas, 2px de respiro entre os segmentos */}
            {dados.map((d, i) => {
              const x = cx0(i) - larguraColuna / 2
              const hInv = d.v1 > 0 ? Math.max(2, baseDia - yDia(d.v1)) : 0
              const hVol = d.v2 > 0 ? Math.max(2, baseDia - yDia(d.v2)) : 0
              const apagado = ativo !== null && ativo !== i
              const yVol = baseDia - hInv - (hInv ? 2 : 0) - hVol
              return (
                <g key={d.dia} opacity={apagado ? 0.45 : 1} style={{ transition: 'opacity 120ms ease-out' }}>
                  {hInv ? (
                    hVol ? (
                      <rect x={x} y={baseDia - hInv} width={larguraColuna} height={hInv} fill={s1.cor} />
                    ) : (
                      <path d={colunaTopoRedondo(x, baseDia - hInv, larguraColuna, hInv)} fill={s1.cor} />
                    )
                  ) : null}
                  {hVol && s2 ? <path d={colunaTopoRedondo(x, yVol, larguraColuna, hVol)} fill={s2.cor} /> : null}
                </g>
              )
            })}
            <line x1={M.esq} x2={largura - M.dir} y1={baseDia} y2={baseDia} stroke="var(--color-graphite)" strokeOpacity={0.6} />

            {/* Eixo de datas */}
            <g fontSize={LETRA} fill="var(--color-silver)" className="tabular">
              {marcasX.map(({ i, d }) => (
                <text key={d.dia} x={cx0(i)} y={baseDia + 20} textAnchor="middle">
                  {i === dados.length - 1 ? t('Hoje') : diaMes(d.dia)}
                </text>
              ))}
            </g>

            {/* Mira: acha o dia, atravessa os dois painéis */}
            {ativo !== null ? (
              <g pointerEvents="none">
                <line x1={cx0(ativo)} x2={cx0(ativo)} y1={TOPO} y2={baseDia} stroke="var(--color-silver)" strokeOpacity={0.45} />
                <circle cx={cx0(ativo)} cy={yAcum(dados[ativo].acum)} r={6} fill="var(--color-card)" />
                <circle cx={cx0(ativo)} cy={yAcum(dados[ativo].acum)} r={4} fill={LINHA_ACUM} />
              </g>
            ) : null}
          </svg>

          {p ? (
            <div
              role="status"
              className="pointer-events-none absolute top-8 z-10 w-[220px] rounded-[10px] border border-line bg-slate/95 px-3.5 py-3 shadow-flutuante backdrop-blur"
              style={
                tipADireita
                  ? { left: `calc(${tipX}% + 14px)` }
                  : { right: `calc(${100 - tipX}% + 14px)` }
              }
            >
              <div className="t-label text-silver">{ativo === dados.length - 1 ? t('Hoje, {dia}', { dia: diaMes(p.dia) }) : diaMes(p.dia)}</div>
              <ul className="mt-2 flex flex-col gap-1.5 text-rotulo">
                <LinhaTip cor={s1.cor} tipo="barra" valor={p.v1} rotulo={s1.rotulo.split(' (')[0]} />
                {s2 ? <LinhaTip cor={s2.cor} tipo="barra" valor={p.v2} rotulo={s2.rotulo.split(' (')[0]} /> : null}
                {s2 ? (
                  <li className="mt-1 flex items-baseline justify-between gap-3 border-t border-line pt-1.5">
                    <span className="text-silver">{t('Total do dia')}</span>
                    <span className="tabular font-[640] text-paper">{fmt.brl(p.total)}</span>
                  </li>
                ) : null}
                <LinhaTip cor={LINHA_ACUM} tipo="linha" valor={p.acum} rotulo={t('Acumulado')} />
              </ul>
            </div>
          ) : null}
        </div>
      )}
    </div>
  )
}

function LinhaTip({ cor, tipo, valor, rotulo }: { cor: string; tipo: 'barra' | 'linha'; valor: number; rotulo: string }) {
  return (
    <li className="flex items-baseline justify-between gap-3">
      <span className="flex items-center gap-2 text-silver">
        <span
          aria-hidden="true"
          className={tipo === 'barra' ? 'h-[3px] w-3 rounded-full' : 'h-[2px] w-3 rounded-full'}
          style={{ background: cor }}
        />
        {rotulo}
      </span>
      <span className={cx('tabular font-[620]', valor ? 'text-paper' : 'text-muted')}>{valor ? fmt.brl(valor) : 'R$ 0'}</span>
    </li>
  )
}

const locale = 'pt-BR'
const brl = new Intl.NumberFormat(locale, { style: 'currency', currency: 'BRL' })
const brlInteiro = new Intl.NumberFormat(locale, {
  style: 'currency',
  currency: 'BRL',
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
})
const numero = new Intl.NumberFormat(locale)
const dataCurta = new Intl.DateTimeFormat(locale, { day: '2-digit', month: 'short' })
const dataHora = new Intl.DateTimeFormat(locale, {
  day: '2-digit',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
})
const dataLonga = new Intl.DateTimeFormat(locale, { weekday: 'long', day: 'numeric', month: 'long' })
const nomeDoMes = new Intl.DateTimeFormat(locale, { month: 'long' })

export const fmt = {
  brl: (v: number) => brl.format(v),
  brlInteiro: (v: number) => brlInteiro.format(Math.round(v)),
  numero: (v: number) => numero.format(v),
  pontos: (v: number, casas = 0) => `${v.toFixed(casas).replace('.', ',')}%`,
  dataCurta: (iso: string) => dataCurta.format(new Date(iso)).replace('.', ''),
  dataHora: (iso: string) => dataHora.format(new Date(iso)).replace('.', ''),
  dataLonga: (d: Date) => {
    const s = dataLonga.format(d)
    return s.charAt(0).toUpperCase() + s.slice(1)
  },
  /** "2026-09" vira "setembro". */
  mesPorExtenso: (mes: string) => nomeDoMes.format(new Date(`${mes}-15T12:00:00`)),
  /** "2026-09": o mês de uma data, no fuso do navegador. */
  mesDe: (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`,
  /** "em 2 dias", "hoje", "há 3 h" — relativo ao agora. */
  relativo: (iso: string, agora = new Date()) => {
    const diff = new Date(iso).getTime() - agora.getTime()
    const h = Math.round(diff / 3_600_000)
    const d = Math.round(diff / 86_400_000)
    if (Math.abs(h) < 1) return 'agora'
    if (Math.abs(h) < 24) return h > 0 ? `em ${h} h` : `há ${-h} h`
    if (d === 1) return 'amanhã'
    if (d === -1) return 'ontem'
    return d > 0 ? `em ${d} dias` : `há ${-d} dias`
  },
}

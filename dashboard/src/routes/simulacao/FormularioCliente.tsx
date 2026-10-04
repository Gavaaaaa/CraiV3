import { useEffect, useState, type FormEvent } from 'react'
import { IconRefresh, IconSend } from '../../components/icons/Icons'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import type { ClienteFicticio, PerfilPagador } from '../../data/tipos'
import { cx } from '../../lib/cx'

const NOMES = ['Ana Souza', 'Bruno Lima', 'Carla Menezes', 'Diego Farias', 'Elisa Prado', 'Fábio Nunes', 'Gabi Torres', 'Heitor Campos', 'Iara Bastos', 'Júlio Rezende']

/** Recusa o que pareça dado real: CPF, CNPJ, telefone, e-mail, chave Pix, sequências longas de números. */
export function pareceDadoReal(texto: string): string | null {
  const t = texto.trim()
  if (/@/.test(t)) return 'Parece um e-mail. Aqui só entra um nome inventado.'
  if (/\d{3}\.?\d{3}\.?\d{3}-?\d{2}/.test(t)) return 'Parece um CPF. Aqui só entra um nome inventado.'
  if (/\d{2}\.?\d{3}\.?\d{3}\/?\d{4}-?\d{2}/.test(t)) return 'Parece um CNPJ. Aqui só entra um nome inventado.'
  if (/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(t)) return 'Parece uma chave Pix. Aqui só entra um nome inventado.'
  if (/(\+?55\s?)?\(?\d{2}\)?\s?9?\d{4}-?\d{4}/.test(t)) return 'Parece um telefone. Aqui só entra um nome inventado.'
  if (/\d{6,}/.test(t)) return 'Números longos não entram aqui: pode ser um dado real.'
  return null
}

const PERFIS: { valor: PerfilPagador; rotulo: string; dica: string }[] = [
  { valor: 'clt', rotulo: 'CLT', dica: 'Salário fixo, entra até o 5º dia útil' },
  { valor: 'pj', rotulo: 'PJ', dica: 'Caixa da empresa, primeira semana' },
  { valor: 'freelancer', rotulo: 'Freelancer', dica: 'Entradas irregulares' },
]

export function FormularioCliente({ onSimular, onRascunho, ocupado }: { onSimular: (c: ClienteFicticio) => void; onRascunho?: (c: ClienteFicticio) => void; ocupado: boolean }) {
  const [nome, setNome] = useState('Ana Souza')
  const [mensalidade, setMensalidade] = useState('890')
  const [perfil, setPerfil] = useState<PerfilPagador>('clt')
  const [dias, setDias] = useState(5)
  const [chance, setChance] = useState(80)
  const [revoga, setRevoga] = useState(false)
  const [erro, setErro] = useState<string | null>(null)

  // O cartão ao lado mostra o que está sendo digitado
  useEffect(() => {
    const valor = Number(mensalidade.replace(',', '.'))
    onRascunho?.({ nome: nome.trim(), mensalidade: Number.isFinite(valor) ? valor : 0, perfil, verdade: { dias_ate_saldo: dias, chance_pagar: chance / 100, vai_revogar: revoga } })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nome, mensalidade, perfil, dias, chance, revoga])

  function sortear() {
    const outros = NOMES.filter((n) => n !== nome)
    setNome(outros[Math.floor(Math.random() * outros.length)])
    setErro(null)
  }

  function enviar(e: FormEvent) {
    e.preventDefault()
    const n = nome.trim()
    if (n.length < 3) return setErro('Dê um nome ao cliente fictício (pelo menos 3 letras).')
    const suspeito = pareceDadoReal(n)
    if (suspeito) return setErro(suspeito)
    const valor = Number(mensalidade.replace(',', '.'))
    if (!Number.isFinite(valor) || valor < 10 || valor > 50_000) return setErro('A mensalidade precisa ficar entre R$ 10 e R$ 50.000.')
    setErro(null)
    onSimular({ nome: n, mensalidade: Math.round(valor * 100) / 100, perfil, verdade: { dias_ate_saldo: dias, chance_pagar: chance / 100, vai_revogar: revoga } })
  }

  const campo = 'h-10 w-full rounded-[10px] border border-line bg-ink/40 px-3 text-apoio text-paper placeholder:text-muted focus:border-amber/60 focus:outline-none'

  return (
    <form onSubmit={enviar} className="flex flex-col gap-5" aria-label="Cliente fictício">
      <div>
        <h3 className="t-h3 text-paper">Cliente fictício</h3>
        <p className="t-apoio mt-1 text-silver">Invente um pagador. Nada aqui é real: nenhum campo de cartão, CPF, conta ou chave Pix.</p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block sm:col-span-2">
          <span className="t-label text-silver">Nome inventado</span>
          <div className="mt-1.5 flex gap-2">
            <input value={nome} onChange={(e) => { setNome(e.target.value); setErro(null) }} className={campo} maxLength={40} autoComplete="off" />
            <Button type="button" variant="ghost" size="md" onClick={sortear} aria-label="Sortear um nome">
              <IconRefresh width={16} height={16} />
            </Button>
          </div>
        </label>
        <label className="block">
          <span className="t-label text-silver">Mensalidade (R$)</span>
          <input value={mensalidade} onChange={(e) => setMensalidade(e.target.value)} inputMode="decimal" className={cx(campo, 'tabular mt-1.5')} />
        </label>
        <div>
          <span className="t-label text-silver">Perfil do pagador</span>
          <div role="radiogroup" aria-label="Perfil do pagador" className="mt-1.5 flex gap-1 rounded-[10px] border border-line bg-ink/40 p-1">
            {PERFIS.map((p) => (
              <button
                key={p.valor}
                type="button"
                role="radio"
                aria-checked={perfil === p.valor}
                title={p.dica}
                onClick={() => setPerfil(p.valor)}
                className={cx('flex-1 rounded-[7px] py-1.5 text-rotulo font-[540] transition-colors', perfil === p.valor ? 'bg-paper text-ink' : 'text-silver hover:text-paper')}
              >
                {p.rotulo}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* A verdade escondida: o que só o banco do cliente sabe */}
      <fieldset className="rounded-[14px] border border-dashed border-amber/40 bg-amber/[0.04] p-4">
        <legend className="t-label px-1.5 text-amber">Verdade escondida · o sistema não vê</legend>
        <p className="t-apoio text-silver">O que acontece de verdade quando a cobrança chega ao banco do cliente. É o que torna a demonstração honesta.</p>
        <div className="mt-4 flex flex-col gap-4">
          <Faixa rotulo="O dinheiro entra em" valor={dias === 0 ? 'já tem saldo' : `${dias} ${dias === 1 ? 'dia' : 'dias'}`} min={0} max={15} atual={dias} onChange={setDias} />
          <Faixa rotulo="Chance de pagar quando tem saldo" valor={`${chance}%`} min={0} max={100} passo={5} atual={chance} onChange={setChance} />
          <label className="flex cursor-pointer items-center justify-between gap-3">
            <span className="text-apoio text-paper">Vai revogar a autorização na 1ª tentativa</span>
            <span className="relative inline-flex h-6 w-11 shrink-0 items-center">
              <input type="checkbox" checked={revoga} onChange={(e) => setRevoga(e.target.checked)} className="peer sr-only" />
              <span className="absolute inset-0 rounded-full bg-paper/[0.12] transition-colors peer-checked:bg-orange peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-amber" />
              <span className="absolute left-0.5 h-5 w-5 rounded-full bg-paper transition-transform peer-checked:translate-x-5" />
            </span>
          </label>
        </div>
      </fieldset>

      {erro ? (
        <p role="alert" className="rounded-[10px] border border-danger/50 bg-danger/10 px-3 py-2 text-apoio text-[#f5a29a]">
          {erro}
        </p>
      ) : null}

      <div className="flex flex-col gap-3">
        <Button type="submit" size="md" disabled={ocupado} className="w-full">
          <IconSend width={16} height={16} /> {ocupado ? 'Enviando cobrança Pix…' : 'Simular cobrança'}
        </Button>
        <div className="flex flex-wrap items-center justify-center gap-2">
          <Badge tone="amber">Demonstração</Badge>
          <Badge>Dados fictícios</Badge>
          <span className="t-label text-muted">Nada é enviado a nenhum banco.</span>
        </div>
      </div>
    </form>
  )
}

function Faixa({ rotulo, valor, min, max, passo = 1, atual, onChange }: { rotulo: string; valor: string; min: number; max: number; passo?: number; atual: number; onChange: (v: number) => void }) {
  return (
    <label className="block">
      <span className="flex items-baseline justify-between gap-3">
        <span className="text-apoio text-paper">{rotulo}</span>
        <span className="tabular text-apoio font-[600] text-amber">{valor}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={passo}
        value={atual}
        onChange={(e) => onChange(Number(e.target.value))}
        className="mt-2 w-full accent-[#ef9311]"
      />
    </label>
  )
}

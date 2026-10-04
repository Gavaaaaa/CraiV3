import { useEffect, useState } from 'react'
import { IconAlert, IconCheck, IconClose, IconDownload, IconRefresh, IconShield, IconTable } from '../../components/icons/Icons'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { CANAIS_DISPONIVEIS, MODO_REAL, api } from '../../data/api'
import { AGORA } from '../../data/mock'
import type { Canal, ChaveApi, Configuracao, EmpresaDetalhe, ExplicacaoDecisao, Integracao, Membro, Papel, ResultadoTesteIntegracao } from '../../data/tipos'
import { cx } from '../../lib/cx'
import { fmt } from '../../lib/format'
import { Aviso, Bloco, Interruptor, Rotulo, Secao, campo, seletor } from './comuns'

const CANAL: Record<Canal, string> = { whatsapp: 'WhatsApp', email: 'E-mail', sms: 'SMS', sem_canal: 'Sem canal disponível' }
// O backend aceita a janela até 24:00; na demonstração a lista continua de 0h a 23h.
const HORAS = Array.from({ length: MODO_REAL ? 25 : 24 }, (_, i) => i)
const hora = (h: number) => `${String(h).padStart(2, '0')}h`

interface PropsConfig {
  config: Configuracao
  onSalvar: (c: Configuracao) => Promise<void>
  podeEditar: boolean
}

/* ------------------------------------------------------------------ */
/* Mensagens                                                           */
/* ------------------------------------------------------------------ */

export function SecaoMensagens({ config, onSalvar, podeEditar }: PropsConfig) {
  const [c, setC] = useState<Configuracao>(config)
  const [salvando, setSalvando] = useState(false)
  useEffect(() => setC(config), [config])
  const mudou = JSON.stringify(c) !== JSON.stringify(config)

  function mover(i: number, para: number) {
    const canais = [...c.canais]
    if (para < 0 || para >= canais.length) return
    ;[canais[i], canais[para]] = [canais[para], canais[i]]
    setC({ ...c, canais })
  }
  function alternarCanal(canal: Canal) {
    const tem = c.canais.includes(canal)
    if (tem && c.canais.length === 1) return
    setC({ ...c, canais: tem ? c.canais.filter((x) => x !== canal) : [...c.canais, canal] })
  }
  async function salvar() {
    setSalvando(true)
    await onSalvar(c)
    setSalvando(false)
  }

  return (
    <Secao
      titulo="Mensagens"
      apoio="Como o sistema fala com os seus clientes depois que as 3 tentativas de cobrança falham. Nada é enviado antes disso."
      acoes={
        podeEditar ? (
          <Button onClick={salvar} disabled={!mudou || salvando}>
            {salvando ? 'Salvando…' : 'Salvar'}
          </Button>
        ) : null
      }
    >
      {!podeEditar ? <Aviso>Você tem o papel de membro: pode ver a configuração, mas só um administrador ou o dono muda.</Aviso> : null}

      <Bloco titulo="Quem escolhe a mensagem" apoio="O sistema sempre escreve 3 mensagens e recomenda uma. A diferença é quem decide.">
        <div role="radiogroup" aria-label="Modo de envio" className="grid gap-3 sm:grid-cols-2">
          {(
            [
              ['escolha', 'Eu escolho', 'As 3 mensagens esperam a sua decisão. Sem escolha no prazo, a recomendada é enviada. É a opção que registra revisão humana.'],
              ['automatico', 'Automático', 'A recomendada sai na hora, sem esperar. Você acompanha o que foi enviado e por quê.'],
            ] as const
          ).map(([valor, rotulo, texto]) => {
            const sel = c.modo_mensagem_involuntario === valor
            return (
              <button
                key={valor}
                type="button"
                role="radio"
                aria-checked={sel}
                disabled={!podeEditar}
                onClick={() => setC({ ...c, modo_mensagem_involuntario: valor })}
                className={cx('rounded-[12px] border p-4 text-left transition-colors disabled:cursor-not-allowed', sel ? 'border-orange/60 bg-orange/[0.07]' : 'border-line hover:border-graphite')}
              >
                <span className="flex items-center gap-2">
                  <span className={cx('flex h-4 w-4 items-center justify-center rounded-full border', sel ? 'border-orange' : 'border-graphite')} aria-hidden="true">
                    {sel ? <span className="h-2 w-2 rounded-full bg-orange" /> : null}
                  </span>
                  <span className="font-[600] text-paper">{rotulo}</span>
                  {valor === 'escolha' ? <Badge tone="orange">Recomendado</Badge> : null}
                </span>
                <span className="t-apoio mt-2 block text-silver">{texto}</span>
              </button>
            )
          })}
        </div>
        <label className={cx('mt-4 block max-w-xs', c.modo_mensagem_involuntario !== 'escolha' && 'opacity-50')}>
          <Rotulo apoio="Depois disso, a recomendada é enviada sozinha.">Prazo para escolher</Rotulo>
          <select value={c.prazo_escolha_horas} disabled={!podeEditar || c.modo_mensagem_involuntario !== 'escolha'} onChange={(e) => setC({ ...c, prazo_escolha_horas: Number(e.target.value) })} className={seletor}>
            {[4, 8, 12, 24, 48].map((h) => (
              <option key={h} value={h}>
                {h} horas{h === 8 ? ' (padrão)' : ''}
              </option>
            ))}
          </select>
        </label>
      </Bloco>

      <Bloco titulo="Horário permitido para contato" apoio="Fora dessa janela, a mensagem espera o próximo horário permitido. Vale para todos os canais.">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block w-36">
            <Rotulo>Das</Rotulo>
            <select value={c.janela_contato.inicio} disabled={!podeEditar} onChange={(e) => setC({ ...c, janela_contato: { ...c.janela_contato, inicio: Number(e.target.value) } })} className={seletor}>
              {HORAS.filter((h) => h < c.janela_contato.fim).map((h) => (
                <option key={h} value={h}>{hora(h)}</option>
              ))}
            </select>
          </label>
          <label className="block w-36">
            <Rotulo>Até</Rotulo>
            <select value={c.janela_contato.fim} disabled={!podeEditar} onChange={(e) => setC({ ...c, janela_contato: { ...c.janela_contato, fim: Number(e.target.value) } })} className={seletor}>
              {HORAS.filter((h) => h > c.janela_contato.inicio).map((h) => (
                <option key={h} value={h}>{hora(h)}</option>
              ))}
            </select>
          </label>
          <span className="t-label pb-3 text-muted">Padrão: 8h às 20h, horário de Brasília.</span>
        </div>
      </Bloco>

      <Bloco titulo="Canais e ordem de preferência" apoio="O sistema usa o primeiro canal da lista que o cliente tiver. Sem nenhum, a tela mostra “Sem canal disponível” e nada é enviado.">
        <ol className="flex flex-col gap-2">
          {c.canais.map((canal, i) => (
            <li key={canal} className="flex items-center gap-3 rounded-[10px] border border-line bg-ink/30 px-3 py-2">
              <span className="tabular w-5 text-[12px] text-muted">{i + 1}</span>
              <span className="flex-1 text-[14px] text-paper">{CANAL[canal]}</span>
              <button type="button" aria-label={`Subir ${CANAL[canal]}`} disabled={!podeEditar || i === 0} onClick={() => mover(i, i - 1)} className="rounded-[6px] px-2 py-1 text-silver hover:bg-paper/[0.06] hover:text-paper disabled:opacity-30">↑</button>
              <button type="button" aria-label={`Descer ${CANAL[canal]}`} disabled={!podeEditar || i === c.canais.length - 1} onClick={() => mover(i, i + 1)} className="rounded-[6px] px-2 py-1 text-silver hover:bg-paper/[0.06] hover:text-paper disabled:opacity-30">↓</button>
              <button type="button" aria-label={`Desligar ${CANAL[canal]}`} disabled={!podeEditar || c.canais.length === 1} onClick={() => alternarCanal(canal)} className="rounded-[6px] p-1 text-muted hover:bg-paper/[0.06] hover:text-paper disabled:opacity-30">
                <IconClose width={15} height={15} />
              </button>
            </li>
          ))}
        </ol>
        {CANAIS_DISPONIVEIS.filter((x) => !c.canais.includes(x)).length ? (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="t-label text-silver">Desligados:</span>
            {CANAIS_DISPONIVEIS
              .filter((x) => !c.canais.includes(x))
              .map((x) => (
                <Button key={x} size="sm" variant="ghost" disabled={!podeEditar} onClick={() => alternarCanal(x)}>
                  Ligar {CANAL[x]}
                </Button>
              ))}
          </div>
        ) : null}
      </Bloco>
    </Secao>
  )
}

/* ------------------------------------------------------------------ */
/* Empresa (só leitura)                                                */
/* ------------------------------------------------------------------ */

export function SecaoEmpresa({ empresa }: { empresa: EmpresaDetalhe | null }) {
  return (
    <Secao titulo="Empresa" apoio="Como a sua empresa aparece para os seus clientes. Estes dados vêm do cadastro no site da CRAI; para mudar, use o site.">
      <Bloco titulo="Identificação">
        <dl className="grid gap-4 sm:grid-cols-2">
          <Dado rotulo="Nome" valor={empresa?.nome} />
          <Dado rotulo="Plano" valor={empresa ? (empresa.plano === 'premium' ? 'Premium (recuperação + retenção)' : 'Essencial (recuperação)') : undefined} />
          <Dado rotulo="CNPJ" valor={empresa?.cnpj_mascarado} apoio="Mascarado aqui por segurança" />
          <Dado rotulo="Cliente da CRAI desde" valor={empresa ? fmt.dataCurta(empresa.desde) + ' de 2026' : undefined} />
        </dl>
      </Bloco>
      <Bloco titulo="Nas mensagens" apoio="Toda mensagem sai em nome da sua empresa, nunca da CRAI. O cliente final não vê a CRAI.">
        <dl className="grid gap-4 sm:grid-cols-2">
          <Dado rotulo="Nome que aparece" valor={empresa?.nome_nas_mensagens} />
          <Dado rotulo="Assinatura" valor={empresa?.assinatura} />
          <Dado rotulo="Idioma" valor={empresa ? 'Português (Brasil)' : undefined} />
        </dl>
        <div className="mt-4 rounded-[12px] border border-line bg-slate/40 p-4">
          <div className="t-label mb-2 text-silver">Exemplo de como o cliente recebe</div>
          <p className="text-[14px] leading-[1.55] text-paper/90">
            Oi, Ana! A mensalidade de R$ 890,00 da {empresa?.nome_nas_mensagens ?? '…'} não pôde ser debitada este mês. Quando puder, regularize por este link.
            <br />
            <span className="text-silver">— {empresa?.assinatura ?? '…'}</span>
          </p>
        </div>
      </Bloco>
    </Secao>
  )
}

function Dado({ rotulo, valor, apoio }: { rotulo: string; valor?: string; apoio?: string }) {
  return (
    <div>
      <dt className="t-label text-silver">{rotulo}</dt>
      <dd className="mt-0.5 text-[15px] font-[560] text-paper">{valor ?? '—'}</dd>
      {apoio ? <dd className="t-label text-muted">{apoio}</dd> : null}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Equipe                                                              */
/* ------------------------------------------------------------------ */

const PAPEL: Record<Papel, { rotulo: string; texto: string }> = {
  owner: { rotulo: 'Dono', texto: 'Tudo, inclusive papéis e chaves de API.' },
  admin: { rotulo: 'Administrador', texto: 'Escolhe mensagens, importa a base, exporta ou anonimiza clientes, gerencia chaves.' },
  membro: { rotulo: 'Membro', texto: 'Só lê. Vê o painel inteiro, mas não muda nada.' },
}

export function SecaoEquipe({ membros, papelAtual, onMudar }: { membros: Membro[] | null; papelAtual: Papel; onMudar: (id: string, papel: Papel) => Promise<void> }) {
  const [ocupado, setOcupado] = useState<string | null>(null)
  async function mudar(id: string, papel: Papel) {
    setOcupado(id)
    await onMudar(id, papel)
    setOcupado(null)
  }
  return (
    <Secao titulo="Equipe" apoio="Quem entra no painel da sua empresa e o que cada um pode fazer. O papel vale em todas as páginas.">
      <Bloco titulo="Membros">
        <div className="scroll-fino overflow-x-auto">
          <table className="w-full min-w-[560px] text-left">
            <thead>
              <tr className="t-label text-silver">
                <th className="py-2 pr-3 font-[500]">Pessoa</th>
                <th className="px-3 py-2 font-[500]">Papel</th>
                <th className="px-3 py-2 font-[500]">Desde</th>
              </tr>
            </thead>
            <tbody>
              {(membros ?? []).map((m) => (
                <tr key={m.id} className="border-t border-line">
                  <td className="py-3 pr-3">
                    <div className="flex items-center gap-2 font-[560] text-paper">
                      {m.nome}
                      {m.voce ? <Badge tone="orange">Você</Badge> : null}
                    </div>
                    <div className="t-label text-muted">{m.email_mascarado}</div>
                  </td>
                  <td className="px-3 py-3">
                    {papelAtual === 'owner' && !m.voce ? (
                      <select value={m.papel} disabled={ocupado === m.id} onChange={(e) => void mudar(m.id, e.target.value as Papel)} className={cx(seletor, 'w-44')} aria-label={`Papel de ${m.nome}`}>
                        {(['owner', 'admin', 'membro'] as Papel[]).map((p) => (
                          <option key={p} value={p}>{PAPEL[p].rotulo}</option>
                        ))}
                      </select>
                    ) : (
                      <Badge tone={m.papel === 'owner' ? 'orange' : 'neutral'}>{PAPEL[m.papel].rotulo}</Badge>
                    )}
                  </td>
                  <td className="px-3 py-3 text-silver">{fmt.dataCurta(m.desde)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="sm" disabled title="Os convites são feitos no site da CRAI">
            Convidar pessoa
          </Button>
          <span className="t-label text-muted">Os convites são feitos no site da CRAI, no cadastro da empresa.</span>
        </div>
      </Bloco>
      <Bloco titulo="O que cada papel pode fazer">
        <ul className="grid gap-3 sm:grid-cols-3">
          {(['owner', 'admin', 'membro'] as Papel[]).map((p) => (
            <li key={p} className="rounded-[12px] border border-line bg-ink/30 p-3.5">
              <div className="font-[600] text-paper">{PAPEL[p].rotulo}</div>
              <p className="t-apoio mt-1 text-silver">{PAPEL[p].texto}</p>
            </li>
          ))}
        </ul>
        <p className="t-label mt-3 text-muted">Quem escolheu uma mensagem, importou a base ou exportou um cliente fica registrado pelo papel, não pelo nome, na trilha de decisões.</p>
      </Bloco>
    </Secao>
  )
}

/* ------------------------------------------------------------------ */
/* Integração (premium)                                                */
/* ------------------------------------------------------------------ */

export function SecaoIntegracao({ premium, podeEditar }: { premium: boolean; podeEditar: boolean }) {
  const [dados, setDados] = useState<Integracao | null>(null)
  const [nova, setNova] = useState<{ inteira: string; ambiente: 'live' | 'test' } | null>(null)
  const [copiado, setCopiado] = useState(false)
  const [teste, setTeste] = useState<ResultadoTesteIntegracao | null>(null)
  const [testando, setTestando] = useState(false)
  const [criando, setCriando] = useState(false)

  useEffect(() => {
    if (premium) api.integracao().then(setDados)
  }, [premium])

  async function criar(ambiente: 'live' | 'test') {
    setCriando(true)
    const r = await api.criarChave(ambiente)
    setNova({ inteira: r.inteira, ambiente })
    setCopiado(false)
    setDados((d) => (d ? { ...d, chaves: [r.chave, ...d.chaves] } : d))
    setCriando(false)
  }
  async function revogar(c: ChaveApi) {
    if (!window.confirm(`Revogar a chave ${c.inicio}…? Qualquer sistema que a use para de funcionar na hora.`)) return
    const chaves = await api.revogarChave(c.id)
    setDados((d) => (d ? { ...d, chaves } : d))
  }
  async function copiar() {
    if (!nova) return
    try {
      await navigator.clipboard.writeText(nova.inteira)
      setCopiado(true)
    } catch {
      setCopiado(false)
    }
  }
  async function testar() {
    setTestando(true)
    setTeste(await api.testarIntegracao())
    setTestando(false)
  }

  if (!premium) {
    return (
      <Secao titulo="Integração" apoio="Chaves de API e webhook para a sua base se atualizar sozinha e as cobranças chegarem em tempo real.">
        <Aviso>A integração por API faz parte do plano premium. No essencial, a base entra por anexo na página do voluntário.</Aviso>
      </Secao>
    )
  }

  const ativas = (dados?.chaves ?? []).filter((c) => !c.revogada_em)
  const revogadas = (dados?.chaves ?? []).filter((c) => c.revogada_em)

  return (
    <Secao
      titulo="Integração"
      apoio="Chaves de API e webhook para a sua base se atualizar sozinha e as cobranças chegarem em tempo real. Só o dono e os administradores veem esta seção."
      acoes={
        <Button variant="ghost" onClick={testar} disabled={testando || !podeEditar}>
          <IconRefresh width={15} height={15} className={testando ? 'animate-spin' : undefined} /> {testando ? 'Testando…' : 'Testar integração'}
        </Button>
      }
    >
      {teste ? (
        <Aviso tom={teste.ok ? 'ok' : 'danger'}>
          <div className="font-[600]">{teste.ok ? 'Integração funcionando' : 'A integração tem um problema'}</div>
          <ul className="mt-2 flex flex-col gap-1">
            {teste.passos.map((p) => (
              <li key={p.rotulo} className="flex items-start gap-2">
                <span className={cx('mt-[6px] h-1.5 w-1.5 shrink-0 rounded-full', p.ok ? 'bg-ok' : 'bg-danger')} aria-hidden="true" />
                <span>
                  <span className="text-paper">{p.rotulo}.</span> <span className="text-silver">{p.detalhe}</span>
                </span>
              </li>
            ))}
          </ul>
        </Aviso>
      ) : null}

      <Bloco titulo="Chaves de API" apoio="Duas chaves: live para produção e test para testes, sem efeito nos dados reais. A chave inteira aparece uma vez só; depois, só o início.">
        {nova ? (
          <div className="mb-4 rounded-[12px] border border-amber/50 bg-amber/[0.08] p-4">
            <div className="flex items-center gap-2 text-[14px] font-[600] text-amber">
              <IconAlert width={16} height={16} /> Copie agora: esta chave não será mostrada de novo
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <code className="tabular flex-1 rounded-[8px] border border-line bg-ink/60 px-3 py-2 text-[13px] break-all text-paper">{nova.inteira}</code>
              <Button size="sm" onClick={copiar}>
                {copiado ? <IconCheck width={15} height={15} /> : null} {copiado ? 'Copiada' : 'Copiar'}
              </Button>
              <Button size="sm" variant="quiet" onClick={() => setNova(null)}>
                Já guardei
              </Button>
            </div>
          </div>
        ) : null}
        <div className="scroll-fino overflow-x-auto">
          <table className="w-full min-w-[620px] text-left">
            <thead>
              <tr className="t-label text-silver">
                <th className="py-2 pr-3 font-[500]">Chave</th>
                <th className="px-3 py-2 font-[500]">Ambiente</th>
                <th className="px-3 py-2 font-[500]">Criada</th>
                <th className="px-3 py-2 font-[500]">Último uso</th>
                <th className="py-2 pl-3 font-[500]"><span className="sr-only">Ações</span></th>
              </tr>
            </thead>
            <tbody>
              {ativas.map((c) => (
                <tr key={c.id} className="border-t border-line">
                  <td className="tabular py-3 pr-3 font-[560] text-paper">{c.inicio}…</td>
                  <td className="px-3 py-3"><Badge tone={c.ambiente === 'live' ? 'orange' : 'neutral'}>{c.ambiente === 'live' ? 'Live' : 'Test'}</Badge></td>
                  <td className="px-3 py-3 text-silver">{fmt.dataCurta(c.criada_em)}</td>
                  <td className="px-3 py-3 text-silver">{c.ultimo_uso ? fmt.relativo(c.ultimo_uso, AGORA) : 'Nunca'}</td>
                  <td className="py-3 pl-3 text-right">
                    <Button size="sm" variant="quiet" disabled={!podeEditar} onClick={() => void revogar(c)}>Revogar</Button>
                  </td>
                </tr>
              ))}
              {revogadas.map((c) => (
                <tr key={c.id} className="border-t border-line opacity-50">
                  <td className="tabular py-3 pr-3 text-silver line-through">{c.inicio}…</td>
                  <td className="px-3 py-3"><Badge>Revogada</Badge></td>
                  <td className="px-3 py-3 text-silver">{fmt.dataCurta(c.criada_em)}</td>
                  <td className="px-3 py-3 text-silver">Revogada em {c.revogada_em ? fmt.dataCurta(c.revogada_em) : ''}</td>
                  <td />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button size="sm" disabled={!podeEditar || criando} onClick={() => void criar('live')}>Criar chave live</Button>
          <Button size="sm" variant="ghost" disabled={!podeEditar || criando} onClick={() => void criar('test')}>Criar chave test</Button>
        </div>
      </Bloco>

      <Bloco titulo="Webhook" apoio="A CRAI avisa o seu sistema quando uma cobrança é recuperada ou um cliente aceita uma oferta. Cada aviso vai assinado com o segredo.">
        <dl className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <dt className="t-label text-silver">Endereço</dt>
            <dd className="tabular mt-0.5 text-[14px] break-all text-paper">{dados?.webhook.url ?? '—'}</dd>
          </div>
          <Dado rotulo="Segredo da assinatura" valor={dados?.webhook.segredo_inicio ? `${dados.webhook.segredo_inicio}…` : '—'} apoio="Só o início; o segredo inteiro foi mostrado na criação" />
          <Dado rotulo="Último evento entregue" valor={dados?.webhook.ultimo_evento ? fmt.relativo(dados.webhook.ultimo_evento, AGORA).replace(/^./, (x) => x.toUpperCase()) : 'Nenhum'} />
        </dl>
        <p className="t-label mt-4 text-muted">Para mudar o endereço, use a API ou peça ao suporte. Em breve dá para editar aqui.</p>
      </Bloco>
    </Secao>
  )
}

/* ------------------------------------------------------------------ */
/* Dados e privacidade                                                 */
/* ------------------------------------------------------------------ */

const TEXTO_POLITICA = `Usamos a CRAI, um serviço de recuperação de cobranças e retenção de clientes, como operadora dos seus dados. A CRAI trata, em nosso nome e só para essa finalidade, os dados da sua assinatura (identificador da cobrança, valor, situação do pagamento) e, quando você nos forneceu, o seu nome e um canal de contato (e-mail ou telefone), para tentar novamente uma cobrança que falhou e para falar com você sobre a sua assinatura. Nenhum dado seu é usado para treinar modelos de outras empresas nem vendido a terceiros. O texto das mensagens que enviamos a você é apagado 90 dias depois do encerramento; os registros da cobrança ficam por 24 meses e depois são anonimizados. Você pode pedir a exportação ou a eliminação dos seus dados, e a explicação de qualquer decisão automatizada sobre a sua cobrança, pelos nossos canais de atendimento; nós repassamos o pedido à CRAI e respondemos no prazo da lei.`

export function SecaoDados({ config, podeEditar }: { config: Configuracao; podeEditar: boolean }) {
  const [idExport, setIdExport] = useState('')
  const [exportando, setExportando] = useState(false)
  const [exportado, setExportado] = useState<{ arquivo: string; linhas: number } | null>(null)
  const [confirmar, setConfirmar] = useState(false)
  const [anonimizando, setAnonimizando] = useState(false)
  const [anonimizado, setAnonimizado] = useState<{ ciclos_anonimizados: number; mensagens_apagadas: number } | null>(null)
  const [idExpl, setIdExpl] = useState('')
  const [explicando, setExplicando] = useState(false)
  const [explicacao, setExplicacao] = useState<ExplicacaoDecisao | null>(null)
  const [copiado, setCopiado] = useState(false)

  async function exportar() {
    if (!idExport.trim()) return
    setExportando(true)
    setExportado(await api.exportarTitular(idExport.trim()))
    setExportando(false)
  }
  async function anonimizar() {
    setAnonimizando(true)
    setAnonimizado(await api.anonimizarTitular(idExport.trim()))
    setAnonimizando(false)
    setConfirmar(false)
  }
  async function explicar() {
    if (!idExpl.trim()) return
    setExplicando(true)
    setExplicacao(await api.explicacaoDecisao(idExpl.trim()))
    setExplicando(false)
  }
  async function copiarPolitica() {
    try {
      await navigator.clipboard.writeText(TEXTO_POLITICA)
      setCopiado(true)
      setTimeout(() => setCopiado(false), 2500)
    } catch {
      setCopiado(false)
    }
  }

  const r = config.retencao_dias

  return (
    <Secao titulo="Dados e privacidade" apoio="A sua empresa é a controladora dos dados dos seus clientes; a CRAI é a operadora. Aqui estão as ferramentas para atender um cliente que pede os dados dele, e o que a CRAI guarda por quanto tempo.">
      <div className="grid gap-5 lg:grid-cols-2">
        <Bloco titulo="Direitos do cliente final (art. 18)" apoio="Quando um cliente seu pede os dados dele ou pede para apagar, use o identificador que a sua empresa usa para ele.">
          <label className="block">
            <Rotulo>Identificador do cliente na sua base</Rotulo>
            <input value={idExport} onChange={(e) => { setIdExport(e.target.value); setExportado(null); setAnonimizado(null); setConfirmar(false) }} placeholder="Ex.: cliente_10482" className={campo} disabled={!podeEditar} />
          </label>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="ghost" disabled={!podeEditar || !idExport.trim() || exportando} onClick={exportar}>
              <IconDownload width={15} height={15} /> {exportando ? 'Montando…' : 'Exportar dados'}
            </Button>
            {!confirmar ? (
              <Button size="sm" variant="ghost" disabled={!podeEditar || !idExport.trim()} onClick={() => setConfirmar(true)}>
                Anonimizar
              </Button>
            ) : (
              <Button size="sm" disabled={anonimizando} onClick={anonimizar} className="border border-danger/60 !bg-danger/20 !text-[#f5a29a] hover:!bg-danger/30">
                {anonimizando ? 'Anonimizando…' : 'Confirmar: não dá para desfazer'}
              </Button>
            )}
          </div>
          {exportado ? (
            <div className="mt-3">
              <Aviso tom="ok">
                Arquivo pronto: <span className="tabular">{exportado.arquivo}</span> ({exportado.linhas} registros). Demonstração: na versão final, o download começa aqui.
              </Aviso>
            </div>
          ) : null}
          {anonimizado ? (
            <div className="mt-3">
              <Aviso tom="ok">
                Pronto. {anonimizado.ciclos_anonimizados} ciclos anonimizados e {anonimizado.mensagens_apagadas} mensagens apagadas. Os totais do painel não mudam; o cliente deixa de ser identificável.
              </Aviso>
            </div>
          ) : null}
          <p className="t-label mt-3 text-muted">Quem exportou ou anonimizou fica registrado (papel e data) por 12 meses, como a lei pede.</p>
        </Bloco>

        <Bloco titulo="Explicação de uma decisão (art. 20)" apoio="O cliente tem direito a saber por que o sistema decidiu algo sobre a cobrança dele, em linguagem simples.">
          <label className="block">
            <Rotulo>Identificador do cliente ou da cobrança</Rotulo>
            <div className="flex gap-2">
              <input value={idExpl} onChange={(e) => setIdExpl(e.target.value)} placeholder="Ex.: RN_7f3a9c21" className={campo} />
              <Button size="md" variant="ghost" disabled={!idExpl.trim() || explicando} onClick={explicar}>
                {explicando ? '…' : 'Ver'}
              </Button>
            </div>
          </label>
          {explicacao ? (
            <div className="mt-4 rounded-[12px] border border-line bg-slate/40 p-4">
              <div className="t-label text-silver">Decisão de {fmt.dataCurta(explicacao.quando)}</div>
              <p className="mt-1 text-[14px] leading-[1.55] text-paper">{explicacao.decisao}</p>
              <ul className="mt-3 flex flex-col gap-1">
                {explicacao.fatores.map((f) => (
                  <li key={f.fator} className="flex items-center justify-between gap-3 text-[13.5px]">
                    <span className="text-paper">{f.fator}</span>
                    <span className={cx('tabular font-[560]', f.pontos >= 0 ? 'text-ok' : 'text-[#f08a80]')}>{f.pontos >= 0 ? '+' : ''}{f.pontos} pts</span>
                  </li>
                ))}
              </ul>
              {explicacao.revisao_humana ? (
                <p className="t-label mt-3 flex items-start gap-1.5 text-silver">
                  <IconShield width={13} height={13} className="mt-[3px] shrink-0" /> {explicacao.revisao_humana}
                </p>
              ) : null}
              <p className="t-label mt-2 text-muted">Este texto pode ser repassado ao cliente como está. Não há nome de algoritmo nem dado técnico.</p>
            </div>
          ) : null}
        </Bloco>
      </div>

      <Bloco titulo="Por quanto tempo a CRAI guarda" apoio="Prazos padrão até revisão jurídica. Depois do prazo, o dado é apagado ou anonimizado numa passagem diária.">
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Prazo rotulo="Texto das mensagens" valor={`${r.mensagens} dias`} apoio="Depois do desfecho; fica só a abordagem" />
          <Prazo rotulo="Ciclos de cobrança" valor={`${r.ciclos_meses} meses`} apoio="Depois, anonimizados" />
          <Prazo rotulo="Base de clientes" valor={`Contrato + ${r.base_meses_apos_contrato} meses`} apoio="Apagada ao fim" />
          <Prazo rotulo="Trilha de decisões" valor={`${r.trilha_anos} anos`} apoio="Sem dado pessoal; só códigos" />
        </dl>
      </Bloco>

      <Bloco titulo="Texto pronto para a sua política de privacidade" apoio="A lei pede que o seu cliente saiba que a CRAI existe e o que ela faz. Cole este parágrafo na sua política.">
        <div className="relative">
          <textarea readOnly value={TEXTO_POLITICA} rows={8} className="scroll-fino w-full resize-none rounded-[12px] border border-line bg-ink/40 p-4 pr-4 text-[13.5px] leading-[1.6] text-paper/90 focus:border-amber/60 focus:outline-none" aria-label="Texto para a política de privacidade" />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button size="sm" variant="ghost" onClick={copiarPolitica}>
            {copiado ? <IconCheck width={15} height={15} /> : <IconTable width={15} height={15} />} {copiado ? 'Copiado' : 'Copiar texto'}
          </Button>
          <span className="t-label text-muted">Revise com quem cuida do jurídico da sua empresa antes de publicar.</span>
        </div>
      </Bloco>
    </Secao>
  )
}

function Prazo({ rotulo, valor, apoio }: { rotulo: string; valor: string; apoio: string }) {
  return (
    <div className="rounded-[12px] border border-line bg-ink/30 px-3.5 py-3">
      <dt className="t-label text-silver">{rotulo}</dt>
      <dd className="mt-1 text-[18px] font-[640] tracking-[-0.01em] text-paper">{valor}</dd>
      <dd className="t-label mt-0.5 text-muted">{apoio}</dd>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Notificações                                                        */
/* ------------------------------------------------------------------ */

export function SecaoNotificacoes({ config, onSalvar, podeEditar }: PropsConfig) {
  const [n, setN] = useState(config.notificacoes)
  const [salvando, setSalvando] = useState(false)
  useEffect(() => setN(config.notificacoes), [config])
  const mudou = JSON.stringify(n) !== JSON.stringify(config.notificacoes)
  async function salvar() {
    setSalvando(true)
    await onSalvar({ ...config, notificacoes: n })
    setSalvando(false)
  }
  return (
    <Secao
      titulo="Notificações"
      apoio="Avisos por e-mail para a sua equipe. Nenhum vai para o cliente final."
      acoes={
        podeEditar ? (
          <Button onClick={salvar} disabled={!mudou || salvando}>
            {salvando ? 'Salvando…' : 'Salvar'}
          </Button>
        ) : null
      }
    >
      <Bloco titulo="Quando avisar">
        <div className="divide-y divide-line">
          <Interruptor ligado={n.escolha_pendente} disabled={!podeEditar} onChange={(v) => setN({ ...n, escolha_pendente: v })} rotulo="Uma mensagem está esperando a minha escolha" apoio="Na hora, com o prazo restante. Só no modo 'Eu escolho'." />
          <Interruptor ligado={n.risco_grave} disabled={!podeEditar} onChange={(v) => setN({ ...n, risco_grave: v })} rotulo="Um cliente entrou em risco grave" apoio="Na hora, com o motivo em uma frase." />
          <Interruptor ligado={n.resumo_semanal} disabled={!podeEditar} onChange={(v) => setN({ ...n, resumo_semanal: v })} rotulo="Resumo semanal" apoio="Segunda de manhã: recuperado, mantido, ciclos abertos e o que precisa de você." />
        </div>
      </Bloco>
    </Secao>
  )
}

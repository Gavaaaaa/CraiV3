import { useEffect, useState, type KeyboardEvent } from 'react'
import { Link } from 'react-router-dom'
import { IconArrowRight, IconCheck, IconClose, IconDownload, IconMoon, IconRefresh, IconShield, IconSun, IconTable } from '../../components/icons/Icons'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import type { AnonimizacaoTitular, ExportacaoTitular, MarcaNaoContatar } from '../../data/adaptadores'
import { CANAIS_DISPONIVEIS, ErroApi, MODO_REAL, api } from '../../data/api'
import { AGORA } from '../../data/mock'
import type { Canal, Configuracao, EmpresaDetalhe, ExplicacaoDecisao, Integracao, Membro, Papel, ResultadoTesteIntegracao } from '../../data/tipos'
import { INTERVALO_ENTRE_OFERTAS, INTERVALO_PADRAO_ENTRE_OFERTAS } from '../../data/adaptadores'
import { cx } from '../../lib/cx'
import { fmt } from '../../lib/format'
import { type Idioma, t, useIdioma } from '../../lib/idioma'
import { type Tema, useTema } from '../../lib/tema'
import { paragrafosComNegrito, semMarcasDeNegrito } from '../../lib/textoComNegrito'
import { Aviso, Bloco, Interruptor, Rotulo, Secao, campo, seletor } from './comuns'

const CANAL: Record<Canal, string> = { whatsapp: 'WhatsApp', email: t('E-mail'), sms: 'SMS', sem_canal: t('Sem canal disponível') }
// O backend aceita a janela até 24:00; na demonstração a lista continua de 0h a 23h.
const HORAS = Array.from({ length: MODO_REAL ? 25 : 24 }, (_, i) => i)
const hora = (h: number) => t('{h}h', { h: String(h).padStart(2, '0') })

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
  // O intervalo é digitado: fica como texto até virar um número válido (de 1 a 365).
  const [intervalo, setIntervalo] = useState(String(config.intervalo_minimo_ofertas_dias))
  useEffect(() => {
    setC(config)
    setIntervalo(String(config.intervalo_minimo_ofertas_dias))
  }, [config])
  const dias = Number(intervalo.trim())
  const intervaloValido = /^\d{1,3}$/.test(intervalo.trim()) && dias >= INTERVALO_ENTRE_OFERTAS.minimo && dias <= INTERVALO_ENTRE_OFERTAS.maximo
  const nova: Configuracao = intervaloValido ? { ...c, intervalo_minimo_ofertas_dias: dias } : c
  const mudou = JSON.stringify(nova) !== JSON.stringify(config)

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
    if (!intervaloValido) return
    setSalvando(true)
    await onSalvar(nova)
    setSalvando(false)
  }

  return (
    <Secao
      titulo={t('Mensagens')}
      apoio={t('Como o sistema fala com os seus clientes depois que as 3 tentativas de cobrança falham. Nada é enviado antes disso.')}
      acoes={
        podeEditar ? (
          <Button onClick={salvar} disabled={!mudou || salvando || !intervaloValido}>
            {salvando ? t('Salvando…') : t('Salvar')}
          </Button>
        ) : null
      }
    >
      {!podeEditar ? <Aviso>{t('Você tem o papel de membro: pode ver a configuração, mas só um administrador ou o dono muda.')}</Aviso> : null}

      <Bloco titulo={t('Quem escolhe a mensagem')} apoio={t('O sistema sempre escreve 3 mensagens e recomenda uma. A diferença é quem decide.')}>
        <div role="radiogroup" aria-label={t('Modo de envio')} className="grid gap-3 sm:grid-cols-2">
          {(
            [
              ['escolha', t('Eu escolho'), t('As 3 mensagens esperam a sua decisão. Sem escolha no prazo, a recomendada é enviada. É a opção que registra revisão humana.')],
              ['automatico', t('Automático'), t('A recomendada sai na hora, sem esperar. Você acompanha o que foi enviado e por quê.')],
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
                  {valor === 'escolha' ? <Badge tone="orange">{t('Recomendado')}</Badge> : null}
                </span>
                <span className="t-apoio mt-2 block text-silver">{texto}</span>
              </button>
            )
          })}
        </div>
        <label className={cx('mt-4 block max-w-xs', c.modo_mensagem_involuntario !== 'escolha' && 'opacity-50')}>
          <Rotulo apoio={t('Depois disso, a recomendada é enviada sozinha.')}>{t('Prazo para escolher')}</Rotulo>
          <select value={c.prazo_escolha_horas} disabled={!podeEditar || c.modo_mensagem_involuntario !== 'escolha'} onChange={(e) => setC({ ...c, prazo_escolha_horas: Number(e.target.value) })} className={seletor}>
            {[4, 8, 12, 24, 48].map((h) => (
              <option key={h} value={h}>
                {h === 8 ? t('{h} horas (padrão)', { h }) : t('{h} horas', { h })}
              </option>
            ))}
          </select>
        </label>
      </Bloco>

      <Bloco titulo={t('Horário permitido para contato')} apoio={t('Fora dessa janela, a mensagem espera o próximo horário permitido. Vale para todos os canais.')}>
        <div className="flex flex-wrap items-end gap-3">
          <label className="block w-36">
            <Rotulo>{t('Das')}</Rotulo>
            <select value={c.janela_contato.inicio} disabled={!podeEditar} onChange={(e) => setC({ ...c, janela_contato: { ...c.janela_contato, inicio: Number(e.target.value) } })} className={seletor}>
              {HORAS.filter((h) => h < c.janela_contato.fim).map((h) => (
                <option key={h} value={h}>{hora(h)}</option>
              ))}
            </select>
          </label>
          <label className="block w-36">
            <Rotulo>{t('Até')}</Rotulo>
            <select value={c.janela_contato.fim} disabled={!podeEditar} onChange={(e) => setC({ ...c, janela_contato: { ...c.janela_contato, fim: Number(e.target.value) } })} className={seletor}>
              {HORAS.filter((h) => h > c.janela_contato.inicio).map((h) => (
                <option key={h} value={h}>{hora(h)}</option>
              ))}
            </select>
          </label>
          <span className="t-label pb-3 text-muted">{t('Padrão: 8h às 20h, horário de Brasília.')}</span>
        </div>
      </Bloco>

      <Bloco titulo={t('Canais e ordem de preferência')} apoio={t('O sistema usa o primeiro canal da lista que o cliente tiver. Sem nenhum, a tela mostra “Sem canal disponível” e nada é enviado.')}>
        <ol className="flex flex-col gap-2">
          {c.canais.map((canal, i) => (
            <li key={canal} className="flex items-center gap-3 rounded-[10px] border border-line bg-ink/30 px-3 py-2">
              <span className="tabular w-5 text-rotulo text-muted">{i + 1}</span>
              <span className="flex-1 text-apoio text-paper">{CANAL[canal]}</span>
              <button type="button" aria-label={t('Subir {canal}', { canal: CANAL[canal] })} disabled={!podeEditar || i === 0} onClick={() => mover(i, i - 1)} className="rounded-[6px] px-2 py-1 text-silver hover:bg-paper/[0.06] hover:text-paper disabled:opacity-30">↑</button>
              <button type="button" aria-label={t('Descer {canal}', { canal: CANAL[canal] })} disabled={!podeEditar || i === c.canais.length - 1} onClick={() => mover(i, i + 1)} className="rounded-[6px] px-2 py-1 text-silver hover:bg-paper/[0.06] hover:text-paper disabled:opacity-30">↓</button>
              <button type="button" aria-label={t('Desligar {canal}', { canal: CANAL[canal] })} disabled={!podeEditar || c.canais.length === 1} onClick={() => alternarCanal(canal)} className="rounded-[6px] p-1 text-muted hover:bg-paper/[0.06] hover:text-paper disabled:opacity-30">
                <IconClose width={15} height={15} />
              </button>
            </li>
          ))}
        </ol>
        {CANAIS_DISPONIVEIS.filter((x) => !c.canais.includes(x)).length ? (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="t-label text-silver">{t('Desligados:')}</span>
            {CANAIS_DISPONIVEIS
              .filter((x) => !c.canais.includes(x))
              .map((x) => (
                <Button key={x} size="sm" variant="ghost" disabled={!podeEditar} onClick={() => alternarCanal(x)}>
                  {t('Ligar {canal}', { canal: CANAL[x] })}
                </Button>
              ))}
          </div>
        ) : null}
      </Bloco>

      <Bloco
        titulo={t('Intervalo entre ofertas de retenção')}
        apoio={t('Um mesmo cliente recebe no máximo uma oferta de retenção dentro deste intervalo, venha o sinal de onde vier: um evento, a base ou o disparo em lote. Vale para o churn voluntário.')}
      >
        <label className="block max-w-xs">
          <Rotulo apoio={t('De {min} a {max} dias. Padrão: {padrao} dias.', { min: INTERVALO_ENTRE_OFERTAS.minimo, max: INTERVALO_ENTRE_OFERTAS.maximo, padrao: INTERVALO_PADRAO_ENTRE_OFERTAS })}>{t('Dias entre uma oferta e a próxima')}</Rotulo>
          <input
            type="number"
            inputMode="numeric"
            min={INTERVALO_ENTRE_OFERTAS.minimo}
            max={INTERVALO_ENTRE_OFERTAS.maximo}
            step={1}
            value={intervalo}
            disabled={!podeEditar}
            aria-invalid={!intervaloValido}
            onChange={(e) => setIntervalo(e.target.value)}
            className={campo}
          />
        </label>
        {!intervaloValido ? (
          <p role="alert" className="mt-2 text-apoio text-danger-aviso">
            {t('Digite um número inteiro de {min} a {max}.', { min: INTERVALO_ENTRE_OFERTAS.minimo, max: INTERVALO_ENTRE_OFERTAS.maximo })}
          </p>
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
    <Secao titulo={t('Empresa')} apoio={t('Como a sua empresa aparece para os seus clientes. Estes dados vêm do cadastro no site da CRAI; para mudar, use o site.')}>
      <Bloco titulo={t('Identificação')}>
        <dl className="grid gap-4 sm:grid-cols-2">
          <Dado rotulo={t('Nome')} valor={empresa?.nome} />
          <Dado rotulo={t('Plano')} valor={empresa ? (empresa.plano === 'premium' ? t('Premium (recuperação + retenção)') : t('Essencial (recuperação)')) : undefined} />
          <Dado rotulo="CNPJ" valor={empresa?.cnpj_mascarado} apoio={t('Mascarado aqui por segurança')} />
          <Dado rotulo={t('Cliente da CRAI desde')} valor={empresa ? fmt.dataComAno(empresa.desde) : undefined} />
        </dl>
      </Bloco>
      <Bloco titulo={t('Nas mensagens')} apoio={t('Toda mensagem sai em nome da sua empresa, nunca da CRAI. O cliente final não vê a CRAI.')}>
        <dl className="grid gap-4 sm:grid-cols-2">
          <Dado rotulo={t('Nome que aparece')} valor={empresa?.nome_nas_mensagens} />
          <Dado rotulo={t('Assinatura')} valor={empresa?.assinatura} />
          <Dado rotulo={t('Idioma')} valor={empresa ? t('Português (Brasil)') : undefined} />
        </dl>
        <div className="mt-4 rounded-[12px] border border-line bg-slate/40 p-4">
          <div className="t-label mb-2 text-silver">{t('Exemplo de como o cliente recebe')}</div>
          <p className="text-apoio leading-[1.55] text-paper/90">
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
      <dd className="mt-0.5 text-normal font-[560] text-paper">{valor ?? '—'}</dd>
      {apoio ? <dd className="t-label text-muted">{apoio}</dd> : null}
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* Equipe                                                              */
/* ------------------------------------------------------------------ */

const PAPEL: Record<Papel, { rotulo: string; texto: string }> = {
  owner: { rotulo: t('Dono'), texto: t('Tudo, inclusive papéis e chaves de API.') },
  admin: { rotulo: t('Administrador'), texto: t('Escolhe mensagens, importa a base, exporta ou anonimiza clientes, gerencia chaves.') },
  membro: { rotulo: t('Membro'), texto: t('Só lê. Vê o painel inteiro, mas não muda nada.') },
}

export function SecaoEquipe({ membros, papelAtual, onMudar }: { membros: Membro[] | null; papelAtual: Papel; onMudar: (id: string, papel: Papel) => Promise<void> }) {
  const [ocupado, setOcupado] = useState<string | null>(null)
  async function mudar(id: string, papel: Papel) {
    setOcupado(id)
    await onMudar(id, papel)
    setOcupado(null)
  }
  return (
    <Secao titulo={t('Equipe')} apoio={t('Quem entra no painel da sua empresa e o que cada um pode fazer. O papel vale em todas as páginas.')}>
      <Bloco titulo={t('Membros')}>
        <div className="scroll-fino overflow-x-auto">
          <table className="w-full min-w-[560px] text-left">
            <thead>
              <tr className="t-label text-silver">
                <th className="py-2 pr-3 font-[500]">{t('Pessoa')}</th>
                <th className="px-3 py-2 font-[500]">{t('Papel')}</th>
                <th className="px-3 py-2 font-[500]">{t('Desde')}</th>
              </tr>
            </thead>
            <tbody>
              {(membros ?? []).map((m) => (
                <tr key={m.id} className="border-t border-line">
                  <td className="py-3 pr-3">
                    <div className="flex items-center gap-2 font-[560] text-paper">
                      {m.nome}
                      {m.voce ? <Badge tone="orange">{t('Você')}</Badge> : null}
                    </div>
                    <div className="t-label text-muted">{m.email_mascarado}</div>
                  </td>
                  <td className="px-3 py-3">
                    {papelAtual === 'owner' && !m.voce ? (
                      <select value={m.papel} disabled={ocupado === m.id} onChange={(e) => void mudar(m.id, e.target.value as Papel)} className={cx(seletor, 'w-44')} aria-label={t('Papel de {nome}', { nome: m.nome })}>
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
        {/* Sem botão de convite: convidar exige o cadastro do site, que o painel não tem. A frase diz onde é. */}
        <p className="t-label mt-4 text-muted">{t('Para convidar uma pessoa, use o site da CRAI, no cadastro da empresa.')}</p>
      </Bloco>
      <Bloco titulo={t('O que cada papel pode fazer')}>
        <ul className="grid gap-3 sm:grid-cols-3">
          {(['owner', 'admin', 'membro'] as Papel[]).map((p) => (
            <li key={p} className="rounded-[12px] border border-line bg-ink/30 p-3.5">
              <div className="font-[600] text-paper">{PAPEL[p].rotulo}</div>
              <p className="t-apoio mt-1 text-silver">{PAPEL[p].texto}</p>
            </li>
          ))}
        </ul>
        <p className="t-label mt-3 text-muted">{t('Quem escolheu uma mensagem, importou a base ou exportou um cliente fica registrado pelo papel, não pelo nome, na trilha de decisões.')}</p>
      </Bloco>
    </Secao>
  )
}

/* ------------------------------------------------------------------ */
/* Integração (premium)                                                */
/* ------------------------------------------------------------------ */

export function SecaoIntegracao({ premium, podeEditar }: { premium: boolean; podeEditar: boolean }) {
  const [dados, setDados] = useState<Integracao | null>(null)
  const [teste, setTeste] = useState<ResultadoTesteIntegracao | null>(null)
  const [testando, setTestando] = useState(false)

  useEffect(() => {
    if (premium) api.integracao().then(setDados)
  }, [premium])

  async function testar() {
    setTestando(true)
    setTeste(await api.testarIntegracao())
    setTestando(false)
  }

  if (!premium) {
    return (
      <Secao titulo={t('Integração')} apoio={t('Chaves de API e webhook para a sua base se atualizar sozinha e as cobranças chegarem em tempo real.')}>
        <Aviso>{t('A integração por API faz parte do plano premium. No essencial, a base entra por anexo na página do voluntário.')}</Aviso>
      </Secao>
    )
  }

  return (
    <Secao
      titulo={t('Integração')}
      apoio={t('Chaves de API e webhook para a sua base se atualizar sozinha e as cobranças chegarem em tempo real. Só o dono e os administradores veem esta seção.')}
      acoes={
        <Button variant="ghost" onClick={testar} disabled={testando || !podeEditar}>
          <IconRefresh width={15} height={15} className={testando ? 'animate-spin' : undefined} /> {testando ? t('Testando…') : t('Testar integração')}
        </Button>
      }
    >
      {teste ? (
        <Aviso tom={teste.ok ? 'ok' : 'danger'}>
          <div className="font-[600]">{teste.ok ? t('Integração funcionando') : t('A integração tem um problema')}</div>
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

      <Bloco titulo={t('Chaves de API')} apoio={t('As chaves agora ficam na aba API: lá você gera, vê e revoga as chaves e encontra o exemplo de uso.')}>
        <Link
          to="/api"
          className="inline-flex h-8 items-center gap-2 rounded-[8px] border border-graphite px-3 text-rotulo font-[560] text-paper transition-colors hover:border-silver hover:bg-paper/[0.04]"
        >
          {t('Abrir a aba API')} <IconArrowRight width={15} height={15} />
        </Link>
      </Bloco>

      <Bloco titulo="Webhook" apoio={t('A CRAI avisa o seu sistema quando uma cobrança é recuperada ou um cliente aceita uma oferta. Cada aviso vai assinado com o segredo.')}>
        <dl className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <dt className="t-label text-silver">{t('Endereço')}</dt>
            <dd className="tabular mt-0.5 text-apoio break-all text-paper">{dados?.webhook.url ?? '—'}</dd>
          </div>
          <Dado rotulo={t('Segredo da assinatura')} valor={dados?.webhook.segredo_inicio ? `${dados.webhook.segredo_inicio}…` : '—'} apoio={t('Só o início; o segredo inteiro foi mostrado na criação')} />
          <Dado rotulo={t('Último evento entregue')} valor={dados?.webhook.ultimo_evento ? fmt.relativo(dados.webhook.ultimo_evento, AGORA).replace(/^./, (x) => x.toUpperCase()) : t('Nenhum')} />
        </dl>
        <p className="t-label mt-4 text-muted">{t('Para mudar o endereço, use a API ou peça ao suporte. Em breve dá para editar aqui.')}</p>
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
  const [exportado, setExportado] = useState<ExportacaoTitular | null>(null)
  const [confirmar, setConfirmar] = useState(false)
  const [anonimizando, setAnonimizando] = useState(false)
  const [anonimizado, setAnonimizado] = useState<AnonimizacaoTitular | null>(null)
  const [marcando, setMarcando] = useState(false)
  const [marca, setMarca] = useState<MarcaNaoContatar | null>(null)
  const [erroDireitos, setErroDireitos] = useState<string | null>(null)
  const [idExpl, setIdExpl] = useState('')
  const [explicando, setExplicando] = useState(false)
  const [explicacao, setExplicacao] = useState<ExplicacaoDecisao | null>(null)
  const [semExplicacao, setSemExplicacao] = useState(false)
  const [erroExpl, setErroExpl] = useState<string | null>(null)
  const [copiado, setCopiado] = useState(false)
  // O texto da política vem do backend, com os prazos da empresa. Na demonstração, o de exemplo.
  const [politica, setPolitica] = useState<string | null>(MODO_REAL ? null : TEXTO_POLITICA)
  const [erroPolitica, setErroPolitica] = useState<string | null>(null)

  useEffect(() => {
    let vivo = true
    api
      .textoParaPolitica()
      .then((t) => vivo && setPolitica(t ? t.texto : TEXTO_POLITICA))
      .catch((e: unknown) => vivo && setErroPolitica(e instanceof ErroApi ? e.message : t('Não deu para carregar o texto.')))
    return () => {
      vivo = false
    }
  }, [])

  const frase = (e: unknown) => (e instanceof ErroApi ? e.message : t('Algo deu errado. Tente de novo.'))
  function limpar() {
    setExportado(null)
    setAnonimizado(null)
    setMarca(null)
    setErroDireitos(null)
    setConfirmar(false)
  }

  async function exportar() {
    if (!idExport.trim()) return
    limpar()
    setExportando(true)
    try {
      setExportado(await api.exportarTitular(idExport.trim()))
    } catch (e) {
      setErroDireitos(frase(e))
    }
    setExportando(false)
  }
  async function anonimizar() {
    setAnonimizando(true)
    setErroDireitos(null)
    try {
      setAnonimizado(await api.anonimizarTitular(idExport.trim()))
    } catch (e) {
      setErroDireitos(frase(e))
    }
    setAnonimizando(false)
    setConfirmar(false)
  }
  async function contato(voltar: boolean) {
    if (!idExport.trim()) return
    limpar()
    setMarcando(true)
    try {
      setMarca(voltar ? await api.voltarAContatar(idExport.trim()) : await api.naoContatar(idExport.trim()))
    } catch (e) {
      setErroDireitos(frase(e))
    }
    setMarcando(false)
  }
  async function explicar() {
    if (!idExpl.trim()) return
    setExplicando(true)
    setErroExpl(null)
    setSemExplicacao(false)
    setExplicacao(null)
    try {
      const achada = await api.explicacaoDecisao(idExpl.trim())
      setExplicacao(achada)
      setSemExplicacao(achada === null)
    } catch (e) {
      setErroExpl(frase(e))
    }
    setExplicando(false)
  }
  async function copiarPolitica() {
    if (!politica) return
    try {
      // Vai o texto limpo, sem as marcas do negrito: é o que a empresa cola na política dela.
      await navigator.clipboard.writeText(semMarcasDeNegrito(politica))
      setCopiado(true)
      setTimeout(() => setCopiado(false), 2500)
    } catch {
      setCopiado(false)
    }
  }

  const r = config.retencao_dias

  return (
    <Secao titulo={t('Dados e privacidade')} apoio={t('A sua empresa é a controladora dos dados dos seus clientes; a CRAI é a operadora. Aqui estão as ferramentas para atender um cliente que pede os dados dele, e o que a CRAI guarda por quanto tempo.')}>
      <div className="grid gap-5 lg:grid-cols-2">
        <Bloco titulo={t('Direitos do cliente final (art. 18)')} apoio={t('Quando um cliente seu pede os dados dele ou pede para apagar, use o identificador que a sua empresa usa para ele.')}>
          <label className="block">
            <Rotulo>{t('Identificador do cliente na sua base')}</Rotulo>
            <input value={idExport} onChange={(e) => { setIdExport(e.target.value); limpar() }} placeholder={t('Ex.: cliente_10482')} className={campo} disabled={!podeEditar} />
          </label>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="ghost" disabled={!podeEditar || !idExport.trim() || exportando} onClick={exportar}>
              <IconDownload width={15} height={15} /> {exportando ? t('Montando…') : t('Exportar dados')}
            </Button>
            {!confirmar ? (
              <Button size="sm" variant="ghost" disabled={!podeEditar || !idExport.trim()} onClick={() => setConfirmar(true)}>
                {t('Anonimizar')}
              </Button>
            ) : (
              <Button size="sm" disabled={anonimizando} onClick={anonimizar} className="border border-danger/60 !bg-danger/20 !text-danger-aviso hover:!bg-danger/30">
                {anonimizando ? t('Anonimizando…') : t('Confirmar: não dá para desfazer')}
              </Button>
            )}
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant="ghost" disabled={!podeEditar || !idExport.trim() || marcando} onClick={() => contato(false)}>
              {t('Não contatar')}
            </Button>
            <Button size="sm" variant="quiet" disabled={!podeEditar || !idExport.trim() || marcando} onClick={() => contato(true)}>
              {t('Voltar a contatar')}
            </Button>
          </div>
          <p className="t-label mt-2 text-muted">{t('Com "Não contatar", nenhuma mensagem sai mais para este cliente. As tentativas de cobrança continuam.')}</p>
          {erroDireitos ? (
            <div className="mt-3">
              <Aviso tom="danger">{erroDireitos}</Aviso>
            </div>
          ) : null}
          {exportado ? (
            <div className="mt-3">
              <Aviso tom="ok">
                {t('Arquivo pronto:')} <span className="tabular">{exportado.arquivo}</span> {t('({n} registros).', { n: exportado.linhas })}{' '}
                {exportado.conteudo ? (
                  <>
                    <a href={`data:application/json;charset=utf-8,${encodeURIComponent(exportado.conteudo)}`} download={exportado.arquivo} className="font-[600] text-amber underline underline-offset-4">
                      {t('Baixar o arquivo')}
                    </a>
                    {t('. Ele diz quais contatos estão guardados, sem repetir o e-mail e o telefone.')}
                  </>
                ) : (
                  t('Demonstração: na versão final, o download começa aqui.')
                )}
              </Aviso>
            </div>
          ) : null}
          {anonimizado ? (
            <div className="mt-3">
              <Aviso tom="ok">
                {anonimizado.contatos_apagados === undefined
                  ? t('Pronto. {ciclos} ciclos anonimizados e {mensagens} mensagens apagadas. Os totais do painel não mudam; o cliente deixa de ser identificável.', { ciclos: anonimizado.ciclos_anonimizados, mensagens: anonimizado.mensagens_apagadas })
                  : t('Pronto. {contatos} e {mensagens}. Os totais do painel não mudam, e nenhuma mensagem sai mais para este cliente.', {
                      contatos: anonimizado.contatos_apagados === 1 ? t('{n} contato apagado', { n: anonimizado.contatos_apagados }) : t('{n} contatos apagados', { n: anonimizado.contatos_apagados }),
                      mensagens: anonimizado.mensagens_apagadas === 1 ? t('{n} mensagem apagada', { n: anonimizado.mensagens_apagadas }) : t('{n} mensagens apagadas', { n: anonimizado.mensagens_apagadas }),
                    })}
              </Aviso>
            </div>
          ) : null}
          {marca ? (
            <div className="mt-3">
              <Aviso tom="ok">
                {marca.marcado
                  ? marca.ja_estava
                    ? t('Este cliente já estava marcado: nenhuma mensagem sai para ele.')
                    : t('Pronto. Nenhuma mensagem sai mais para este cliente.')
                  : marca.ja_estava
                    ? t('Pronto. Este cliente volta a receber mensagens.')
                    : t('Este cliente não estava marcado: ele já recebe mensagens.')}
              </Aviso>
            </div>
          ) : null}
          <p className="t-label mt-3 text-muted">{t('Quem exportou ou anonimizou fica registrado (papel e data) por 12 meses, como a lei pede.')}</p>
        </Bloco>

        <Bloco titulo={t('Explicação de uma decisão (art. 20)')} apoio={t('O cliente tem direito a saber por que o sistema decidiu algo sobre a cobrança dele, em linguagem simples.')}>
          <label className="block">
            <Rotulo>{t('Identificador do cliente ou da cobrança')}</Rotulo>
            <div className="flex gap-2">
              <input value={idExpl} onChange={(e) => setIdExpl(e.target.value)} placeholder={t('Ex.: RN_7f3a9c21')} className={campo} />
              <Button size="md" variant="ghost" disabled={!idExpl.trim() || explicando} onClick={explicar}>
                {explicando ? '…' : t('Ver')}
              </Button>
            </div>
          </label>
          {erroExpl ? (
            <div className="mt-3">
              <Aviso tom="danger">{erroExpl}</Aviso>
            </div>
          ) : null}
          {semExplicacao ? (
            <div className="mt-3">
              <Aviso>{t('Não há decisão registrada para este identificador na sua empresa.')}</Aviso>
            </div>
          ) : null}
          {explicacao ? (
            <div className="mt-4 rounded-[12px] border border-line bg-slate/40 p-4">
              <div className="t-label text-silver">{t('Decisão de {data}', { data: fmt.dataCurta(explicacao.quando) })}</div>
              <p className="mt-1 text-apoio leading-[1.55] text-paper">{explicacao.decisao}</p>
              <ul className="mt-3 flex flex-col gap-1">
                {explicacao.fatores.map((f) => (
                  <li key={f.fator} className="flex items-center justify-between gap-3 text-apoio">
                    <span className="text-paper">{f.fator}</span>
                    <span className={cx('tabular font-[560]', f.pontos >= 0 ? 'text-ok' : 'text-danger-texto')}>{f.pontos >= 0 ? '+' : ''}{f.pontos} pts</span>
                  </li>
                ))}
              </ul>
              {explicacao.revisao_humana ? (
                <p className="t-label mt-3 flex items-start gap-1.5 text-silver">
                  <IconShield width={13} height={13} className="mt-[3px] shrink-0" /> {explicacao.revisao_humana}
                </p>
              ) : null}
              <p className="t-label mt-2 text-muted">
                {MODO_REAL
                  ? t('É a decisão mais recente, como o sistema a registrou na hora. Revise o texto antes de repassar ao cliente.')
                  : t('Este texto pode ser repassado ao cliente como está. Não há nome de algoritmo nem dado técnico.')}
              </p>
            </div>
          ) : null}
        </Bloco>
      </div>

      <Bloco
        titulo={t('Por quanto tempo a CRAI guarda')}
        apoio={t('Prazos padrão até revisão jurídica. O texto das mensagens e a trilha de decisões são apagados numa passagem diária, e os ciclos antigos perdem os identificadores nela. O prazo da base depende do fim do contrato, e a execução automática dele ainda não está ligada.')}
      >
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Prazo rotulo={t('Texto das mensagens')} valor={t('{n} dias', { n: r.mensagens })} apoio={t('Depois do desfecho; fica só a abordagem')} />
          <Prazo rotulo={t('Ciclos de cobrança')} valor={t('{n} meses', { n: r.ciclos_meses })} apoio={t('Depois do desfecho; saem os identificadores, ficam os valores')} />
          <Prazo rotulo={t('Base de clientes')} valor={t('Contrato + {n} meses', { n: r.base_meses_apos_contrato })} apoio={t('Prazo definido; ainda não executado')} />
          <Prazo rotulo={t('Trilha de decisões')} valor={t('{n} anos', { n: r.trilha_anos })} apoio={t('Sem dado de contato; apagada depois do prazo')} />
        </dl>
      </Bloco>

      <Bloco titulo={t('Texto pronto para a sua política de privacidade')} apoio={t('A lei pede que o seu cliente saiba que a CRAI existe e o que ela faz. Cole este parágrafo na sua política.')}>
        <div className="relative">
          {erroPolitica ? <Aviso tom="danger">{erroPolitica}</Aviso> : null}
          <div
            role="region"
            tabIndex={0}
            aria-busy={politica === null && !erroPolitica}
            aria-label={t('Texto para a política de privacidade')}
            className={cx('scroll-fino w-full overflow-y-auto rounded-[12px] border border-campo bg-ink/40 p-4 text-apoio leading-[1.6] text-paper/90 focus:border-amber/60 focus:outline-none', MODO_REAL ? 'max-h-[380px] min-h-[220px]' : 'max-h-[260px] min-h-[140px]')}
          >
            {paragrafosComNegrito(politica ?? '').map((paragrafo, i) => (
              <p key={i} className={i ? 'mt-3' : undefined}>
                {paragrafo.map((trecho, j) =>
                  trecho.forte ? (
                    <strong key={j} className="font-[640] text-paper">
                      {trecho.texto}
                    </strong>
                  ) : (
                    <span key={j}>{trecho.texto}</span>
                  ),
                )}
              </p>
            ))}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button size="sm" variant="ghost" onClick={copiarPolitica} disabled={!politica}>
            {copiado ? <IconCheck width={15} height={15} /> : <IconTable width={15} height={15} />} {copiado ? t('Copiado') : t('Copiar texto')}
          </Button>
          <span className="t-label text-muted">{t('Revise com quem cuida do jurídico da sua empresa antes de publicar.')}</span>
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
      titulo={t('Notificações')}
      apoio={t('Avisos por e-mail para a sua equipe. Nenhum vai para o cliente final.')}
      acoes={
        podeEditar ? (
          <Button onClick={salvar} disabled={!mudou || salvando}>
            {salvando ? t('Salvando…') : t('Salvar')}
          </Button>
        ) : null
      }
    >
      <Bloco titulo={t('Quando avisar')}>
        <div className="divide-y divide-line">
          <Interruptor ligado={n.escolha_pendente} disabled={!podeEditar} onChange={(v) => setN({ ...n, escolha_pendente: v })} rotulo={t('Uma mensagem está esperando a minha escolha')} apoio={t("Na hora, com o prazo restante. Só no modo 'Eu escolho'.")} />
          <Interruptor ligado={n.risco_grave} disabled={!podeEditar} onChange={(v) => setN({ ...n, risco_grave: v })} rotulo={t('Um cliente entrou em risco grave')} apoio={t('Na hora, com o motivo em uma frase.')} />
          <Interruptor ligado={n.resumo_semanal} disabled={!podeEditar} onChange={(v) => setN({ ...n, resumo_semanal: v })} rotulo={t('Resumo semanal')} apoio={t('Segunda de manhã: recuperado, mantido, ciclos abertos e o que precisa de você.')} />
        </div>
      </Bloco>
    </Secao>
  )
}

/* ------------------------------------------------------------------ */
/* Aparência: o tema e o idioma do painel                              */
/* ------------------------------------------------------------------ */

const TEMAS: { valor: Tema; rotulo: string; texto: string; Icone: typeof IconSun }[] = [
  { valor: 'escuro', rotulo: t('Escuro'), texto: t('O padrão, a cara do site. Fundo escuro, texto claro.'), Icone: IconMoon },
  { valor: 'claro', rotulo: t('Claro'), texto: t('Papel claro, texto escuro, o mesmo laranja de destaque.'), Icone: IconSun },
]

/**
 * O tema do painel. É preferência de quem olha, não configuração da empresa: qualquer papel
 * troca, vale na hora, nada vai ao backend e a escolha fica só neste navegador (`lib/tema.ts`).
 */
export function SecaoAparencia() {
  const [tema, definir] = useTema()
  const [idioma, definirIdioma] = useIdioma()
  // Setas do teclado trocam a opção (o padrão de acessibilidade para "radiogroup").
  function teclado(e: KeyboardEvent<HTMLDivElement>) {
    const i = TEMAS.findIndex((t) => t.valor === tema)
    let j = i
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') j = (i + 1) % TEMAS.length
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') j = (i - 1 + TEMAS.length) % TEMAS.length
    else return
    e.preventDefault()
    definir(TEMAS[j].valor)
    ;(e.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]')[j] ?? null)?.focus()
  }
  return (
    <Secao titulo={t('Aparência')} apoio={t('Escolha como o painel aparece neste navegador.')}>
      <Bloco titulo={t('Tema')} apoio={t('A troca vale na hora, para qualquer papel. Nada é enviado à CRAI.')}>
        <div role="radiogroup" aria-label={t('Tema do painel')} onKeyDown={teclado} className="grid gap-3 sm:grid-cols-2">
          {TEMAS.map(({ valor, rotulo, texto, Icone }) => {
            const sel = tema === valor
            return (
              <button
                key={valor}
                type="button"
                role="radio"
                aria-checked={sel}
                tabIndex={sel ? 0 : -1}
                data-tema-opcao={valor}
                onClick={() => definir(valor)}
                className={cx('rounded-[12px] border p-4 text-left transition-colors', sel ? 'border-orange/60 bg-orange/[0.07]' : 'border-line hover:border-graphite')}
              >
                <span className="flex items-center gap-2">
                  <span className={cx('flex h-4 w-4 items-center justify-center rounded-full border', sel ? 'border-orange' : 'border-graphite')} aria-hidden="true">
                    {sel ? <span className="h-2 w-2 rounded-full bg-orange" /> : null}
                  </span>
                  <span className="font-[600] text-paper">{rotulo}</span>
                  <Icone width={16} height={16} className="ml-auto text-silver" />
                </span>
                <span className="t-apoio mt-2 block text-silver">{texto}</span>
              </button>
            )
          })}
        </div>
        <p className="t-label mt-3 text-muted">{t('A escolha fica guardada só neste navegador. Em outro computador ou navegador, o painel abre no tema escuro até você escolher de novo.')}</p>
      </Bloco>
      <SeletorDeIdioma idioma={idioma} definir={definirIdioma} />
    </Secao>
  )
}

/* ------------------------------------------------------------------ */
/* Aparência: o idioma das telas                                       */
/* ------------------------------------------------------------------ */

// Cada idioma aparece com o próprio nome, para quem não lê o outro achar o seu.
const IDIOMAS: { valor: Idioma; rotulo: string; texto: string }[] = [
  { valor: 'pt', rotulo: 'Português', texto: 'Português do Brasil, o padrão.' },
  { valor: 'en', rotulo: 'English', texto: 'Screens, menus and notices in English.' },
]

/**
 * O idioma das telas. Como o tema, é preferência de quem olha: qualquer papel troca, nada vai
 * ao backend e a escolha fica neste navegador (`lib/idioma.ts`). A troca recarrega a página.
 * As mensagens aos clientes finais continuam em português, e o aviso abaixo diz isso.
 */
function SeletorDeIdioma({ idioma, definir }: { idioma: Idioma; definir: (novo: Idioma) => void }) {
  function teclado(e: KeyboardEvent<HTMLDivElement>) {
    const i = IDIOMAS.findIndex((x) => x.valor === idioma)
    let j = i
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') j = (i + 1) % IDIOMAS.length
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') j = (i - 1 + IDIOMAS.length) % IDIOMAS.length
    else return
    e.preventDefault()
    definir(IDIOMAS[j].valor)
  }
  return (
    <Bloco titulo={t('Idioma')} apoio={t('O idioma das telas do painel. A página recarrega na troca.')}>
      <div role="radiogroup" aria-label={t('Idioma do painel')} onKeyDown={teclado} className="grid gap-3 sm:grid-cols-2">
        {IDIOMAS.map(({ valor, rotulo, texto }) => {
          const sel = idioma === valor
          return (
            <button
              key={valor}
              type="button"
              role="radio"
              aria-checked={sel}
              tabIndex={sel ? 0 : -1}
              lang={valor === 'en' ? 'en' : 'pt-BR'}
              data-idioma-opcao={valor}
              onClick={() => (sel ? undefined : definir(valor))}
              className={cx('rounded-[12px] border p-4 text-left transition-colors', sel ? 'border-orange/60 bg-orange/[0.07]' : 'border-line hover:border-graphite')}
            >
              <span className="flex items-center gap-2">
                <span className={cx('flex h-4 w-4 items-center justify-center rounded-full border', sel ? 'border-orange' : 'border-graphite')} aria-hidden="true">
                  {sel ? <span className="h-2 w-2 rounded-full bg-orange" /> : null}
                </span>
                <span className="font-[600] text-paper">{rotulo}</span>
              </span>
              <span className="t-apoio mt-2 block text-silver">{texto}</span>
            </button>
          )
        })}
      </div>
      <p className="t-label mt-3 text-muted">
        {t('As mensagens enviadas aos seus clientes continuam em português, e também os textos que o sistema escreve sobre cada decisão. Vale só para este navegador.')}
      </p>
    </Bloco>
  )
}

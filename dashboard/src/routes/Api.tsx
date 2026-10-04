import { useCallback, useEffect, useState } from 'react'
import { IconCheck } from '../components/icons/Icons'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Carregando, ErroCarregar } from '../components/ui/Estados'
import { ENDERECO_DA_API, ErroApi, agoraDaTela, api } from '../data/api'
import type { ChaveApi, ChavesDaEmpresa } from '../data/tipos'
import { fmt } from '../lib/format'
import { PainelOQueAApiFaz } from './api/OQueAApiFaz'
import { Aviso, campo } from './configuracao/comuns'

/** O marcador que fica no lugar da chave no exemplo. Nunca uma chave de verdade. */
export const MARCADOR_DA_CHAVE = 'SUA_CHAVE_AQUI'

export const AVISO_DA_CHAVE = 'Copie agora. Por segurança, ela não aparece de novo.'

/** A chave como a lista a mostra: o começo, seis pontos e o final. */
export const mascarar = (c: ChaveApi) => `${c.inicio}••••••${c.final}`

function exemploDeChamada(endereco: string): string {
  return [
    `curl -X POST "${endereco}/clientes" \\`,
    `  -H "Authorization: Bearer ${MARCADOR_DA_CHAVE}" \\`,
    '  -H "Content-Type: application/json" \\',
    `  -d '{"customer_id_externo": "c-001", "mrr": 1500.0, "billing_profile": "PJ"}'`,
  ].join('\n')
}

/** Um botão "Copiar" que confirma por alguns segundos. O texto copiado não é guardado aqui. */
function Copiar({ texto, rotulo = 'Copiar' }: { texto: string; rotulo?: string }) {
  const [copiado, setCopiado] = useState(false)
  async function copiar() {
    try {
      await navigator.clipboard.writeText(texto)
      setCopiado(true)
      setTimeout(() => setCopiado(false), 2500)
    } catch {
      setCopiado(false)
    }
  }
  return (
    <Button size="sm" variant="ghost" onClick={() => void copiar()}>
      {copiado ? <IconCheck width={15} height={15} /> : null} {copiado ? 'Copiado' : rotulo}
    </Button>
  )
}

const linkDiscreto = 'text-rotulo font-[560] text-silver underline decoration-graphite underline-offset-4 hover:text-paper disabled:cursor-not-allowed disabled:no-underline disabled:opacity-60'

export function PaginaApi() {
  const [explicacao, setExplicacao] = useState(false)
  const fecharExplicacao = useCallback(() => setExplicacao(false), [])

  const [dados, setDados] = useState<ChavesDaEmpresa | null>(null)
  const [erro, setErro] = useState<string | null>(null)
  const [tentativa, setTentativa] = useState(0)

  // As chaves geradas NESTA visita, inteiras, por id. Vivem só neste estado: ao sair da
  // página o componente some, e o campo volta a mostrar só o começo e o final.
  const [inteiras, setInteiras] = useState<Record<string, string>>({})
  const [gerando, setGerando] = useState(false)
  const [erroAcao, setErroAcao] = useState<string | null>(null)

  const [confirmando, setConfirmando] = useState<string | null>(null)
  const [revogando, setRevogando] = useState<string | null>(null)
  const [verRevogadas, setVerRevogadas] = useState(false)
  const [verExemplo, setVerExemplo] = useState(false)

  useEffect(() => {
    let vivo = true
    setErro(null)
    api
      .chaves()
      .then((r) => {
        if (vivo) setDados(r)
      })
      .catch((e: unknown) => {
        if (vivo) setErro(e instanceof ErroApi ? e.message : 'Algo deu errado ao carregar. Tente de novo.')
      })
    return () => {
      vivo = false
    }
  }, [tentativa])

  const recarregar = () => setTentativa((t) => t + 1)

  async function gerar() {
    if (gerando) return
    setGerando(true)
    setErroAcao(null)
    try {
      const r = await api.criarChave()
      setInteiras((atual) => ({ ...atual, [r.chave.id]: r.inteira }))
      // A chave nova entra na lista na hora, sem esperar a releitura.
      setDados((d) => (d ? { ...d, chaves: [r.chave, ...d.chaves.filter((c) => c.id !== r.chave.id)], ativas: d.ativas + 1 } : d))
      recarregar()
    } catch (falha) {
      setErroAcao(falha instanceof ErroApi ? falha.message : 'Não deu para gerar a chave. Tente de novo.')
    }
    setGerando(false)
  }

  async function revogar(chave: ChaveApi) {
    setRevogando(chave.id)
    setErroAcao(null)
    try {
      await api.revogarChave(chave.id)
      setInteiras(({ [chave.id]: _revogada, ...resto }) => resto)
      recarregar()
    } catch (falha) {
      setErroAcao(falha instanceof ErroApi ? falha.message : 'Não deu para revogar a chave. Tente de novo.')
    }
    setRevogando(null)
    setConfirmando(null)
  }

  const ativas = dados ? dados.chaves.filter((c) => !c.revogada_em) : []
  const revogadas = dados ? dados.chaves.filter((c) => c.revogada_em) : []
  const noLimite = dados ? ativas.length >= dados.limite_ativas : false
  const exemplo = exemploDeChamada(ENDERECO_DA_API)

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="t-h2 text-paper">API</h2>
        <Button variant="ghost" onClick={() => setExplicacao(true)}>
          O que a API faz
        </Button>
      </div>

      <Card as="section" className="p-5 md:p-6" aria-label="Endereço da API">
        <h3 className="t-h3 text-paper">Endereço da API</h3>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input readOnly value={ENDERECO_DA_API} aria-label="Endereço da API" className={`${campo} tabular min-w-0 flex-1`} />
          <Copiar texto={ENDERECO_DA_API} />
        </div>
      </Card>

      <Card as="section" className="p-5 md:p-6" aria-label="Chave de API">
        <h3 className="t-h3 text-paper">Chave de API</h3>

        <div className="mt-3 flex flex-col gap-4">
          {erroAcao ? <Aviso tom="danger">{erroAcao}</Aviso> : null}

          {erro ? (
            <ErroCarregar mensagem={erro} onTentar={recarregar} />
          ) : dados === null ? (
            <Carregando altura={96} />
          ) : (
            <>
              {ativas.length === 0 ? <p className="t-apoio text-silver">Você ainda não tem uma chave</p> : null}

              {ativas.map((c) => {
                const inteira = inteiras[c.id]
                return (
                  <div key={c.id} data-testid="chave-ativa">
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        readOnly
                        value={inteira ?? mascarar(c)}
                        aria-label={inteira ? 'Chave de API, inteira' : 'Chave de API'}
                        className={`${campo} tabular min-w-0 flex-1`}
                      />
                      {inteira ? <Copiar texto={inteira} /> : null}
                      {dados.pode_revogar && confirmando !== c.id ? (
                        <Button size="sm" variant="quiet" onClick={() => setConfirmando(c.id)}>
                          Revogar
                        </Button>
                      ) : null}
                    </div>
                    {inteira ? (
                      <p role="alert" className="t-label mt-1.5 text-amber">
                        {AVISO_DA_CHAVE}
                      </p>
                    ) : null}
                    {confirmando === c.id ? (
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <span className="t-apoio text-paper">Revogar esta chave? O sistema que a usa para na hora.</span>
                        <Button
                          size="sm"
                          disabled={revogando === c.id}
                          onClick={() => void revogar(c)}
                          className="border border-danger/60 !bg-danger/20 !text-[#f5a29a] hover:!bg-danger/30"
                        >
                          {revogando === c.id ? 'Revogando…' : 'Confirmar'}
                        </Button>
                        <Button size="sm" variant="quiet" disabled={revogando === c.id} onClick={() => setConfirmando(null)}>
                          Cancelar
                        </Button>
                      </div>
                    ) : null}
                    <p className="t-label mt-1.5 text-muted">
                      Criada em {fmt.dataCurta(c.criada_em)} · Último uso: {c.ultimo_uso ? fmt.relativo(c.ultimo_uso, agoraDaTela()) : 'nunca'}
                    </p>
                  </div>
                )
              })}

              {!dados.plano_permite_gerar ? (
                <Aviso>Gerar chave faz parte do plano Premium.</Aviso>
              ) : !dados.pode_gerar ? (
                <Aviso>Seu papel não permite gerar nem revogar chaves.</Aviso>
              ) : noLimite ? (
                <Aviso>Limite de {dados.limite_ativas} chaves ativas. Revogue uma para gerar outra.</Aviso>
              ) : ativas.length === 0 ? (
                <div>
                  <Button onClick={() => void gerar()} disabled={gerando}>
                    {gerando ? 'Gerando…' : 'Gerar chave'}
                  </Button>
                </div>
              ) : (
                <div>
                  <button type="button" onClick={() => void gerar()} disabled={gerando} className={linkDiscreto}>
                    {gerando ? 'Gerando…' : 'Gerar outra chave'}
                  </button>
                </div>
              )}

              {revogadas.length ? (
                <div>
                  <button type="button" aria-expanded={verRevogadas} onClick={() => setVerRevogadas((v) => !v)} className={linkDiscreto}>
                    {verRevogadas ? 'Esconder' : 'Ver'} chaves revogadas ({revogadas.length})
                  </button>
                  {verRevogadas ? (
                    <ul className="mt-2 flex flex-col gap-1.5" aria-label="Chaves revogadas">
                      {revogadas.map((c) => (
                        <li key={c.id} className="t-label flex flex-wrap items-baseline gap-x-3 text-muted">
                          <span className="tabular text-silver line-through">{mascarar(c)}</span>
                          <span>Revogada em {c.revogada_em ? fmt.dataCurta(c.revogada_em) : ''}</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </div>
      </Card>

      <Card as="section" className="p-5 md:p-6" aria-label="Exemplo de uso">
        <button
          type="button"
          aria-expanded={verExemplo}
          onClick={() => setVerExemplo((v) => !v)}
          className="t-h3 flex w-full items-center justify-between gap-3 text-left text-paper"
        >
          Exemplo de uso
          <span aria-hidden="true" className="t-label text-silver">
            {verExemplo ? 'Fechar' : 'Abrir'}
          </span>
        </button>
        {verExemplo ? (
          <div className="mt-3">
            <pre className="scroll-fino overflow-x-auto rounded-[12px] border border-line bg-ink/60 p-4 text-rotulo leading-[1.6] text-paper/90">
              <code>{exemplo}</code>
            </pre>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <Copiar texto={exemplo} rotulo="Copiar exemplo" />
              <span className="t-label text-muted">Troque {MARCADOR_DA_CHAVE} pela sua chave.</span>
            </div>
          </div>
        ) : null}
      </Card>

      {explicacao ? <PainelOQueAApiFaz onFechar={fecharExplicacao} /> : null}
    </div>
  )
}

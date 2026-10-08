import { useCallback, useEffect, useState } from 'react'
import { IconCheck } from '../components/icons/Icons'
import { Button } from '../components/ui/Button'
import { Card } from '../components/ui/Card'
import { Carregando, ErroCarregar } from '../components/ui/Estados'
import { ENDERECO_DA_API, ErroApi, agoraDaTela, api } from '../data/api'
import type { ChaveApi, ChavesDaEmpresa } from '../data/tipos'
import { fmt } from '../lib/format'
import { t } from '../lib/idioma'
import { PainelOQueAApiFaz } from './api/OQueAApiFaz'
import { Aviso, campo } from './configuracao/comuns'

/** O marcador que fica no lugar da chave no exemplo. Nunca uma chave de verdade. */
export const MARCADOR_DA_CHAVE = 'SUA_CHAVE_AQUI'

export const AVISO_DA_CHAVE = t('Copie agora. Por segurança, ela não aparece de novo.')

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

/** O segundo exemplo (Rodada 3): avisar um evento de comportamento do cliente final. */
function exemploDeEvento(endereco: string): string {
  return [
    `curl -X POST "${endereco}/eventos" \\`,
    `  -H "Authorization: Bearer ${MARCADOR_DA_CHAVE}" \\`,
    '  -H "Content-Type: application/json" \\',
    `  -d '{"userId": "c-001", "event": "Cancellation Page Viewed", "messageId": "evento-0001"}'`,
  ].join('\n')
}

/** Um botão "Copiar" que confirma por alguns segundos. O texto copiado não é guardado aqui. */
function Copiar({ texto, rotulo = t('Copiar') }: { texto: string; rotulo?: string }) {
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
      {copiado ? <IconCheck width={15} height={15} /> : null} {copiado ? t('Copiado') : rotulo}
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
        if (vivo) setErro(e instanceof ErroApi ? e.message : t('Algo deu errado ao carregar. Tente de novo.'))
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
      setErroAcao(falha instanceof ErroApi ? falha.message : t('Não deu para gerar a chave. Tente de novo.'))
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
      setErroAcao(falha instanceof ErroApi ? falha.message : t('Não deu para revogar a chave. Tente de novo.'))
    }
    setRevogando(null)
    setConfirmando(null)
  }

  const ativas = dados ? dados.chaves.filter((c) => !c.revogada_em) : []
  const revogadas = dados ? dados.chaves.filter((c) => c.revogada_em) : []
  const noLimite = dados ? ativas.length >= dados.limite_ativas : false
  const exemplo = exemploDeChamada(ENDERECO_DA_API)
  const exemploEvento = exemploDeEvento(ENDERECO_DA_API)

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="t-h2 text-paper">API</h2>
        <Button variant="ghost" onClick={() => setExplicacao(true)}>
          {t('O que a API faz')}
        </Button>
      </div>

      <Card as="section" className="p-5 md:p-6" aria-label={t('Endereço da API')}>
        <h3 className="t-h3 text-paper">{t('Endereço da API')}</h3>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input readOnly value={ENDERECO_DA_API} aria-label={t('Endereço da API')} className={`${campo} tabular min-w-0 flex-1`} />
          <Copiar texto={ENDERECO_DA_API} />
        </div>
      </Card>

      <Card as="section" className="p-5 md:p-6" aria-label={t('Chave de API')}>
        <h3 className="t-h3 text-paper">{t('Chave de API')}</h3>

        <div className="mt-3 flex flex-col gap-4">
          {erroAcao ? <Aviso tom="danger">{erroAcao}</Aviso> : null}

          {erro ? (
            <ErroCarregar mensagem={erro} onTentar={recarregar} />
          ) : dados === null ? (
            <Carregando altura={96} />
          ) : (
            <>
              {ativas.length === 0 ? <p className="t-apoio text-silver">{t('Você ainda não tem uma chave')}</p> : null}

              {ativas.map((c) => {
                const inteira = inteiras[c.id]
                return (
                  <div key={c.id} data-testid="chave-ativa">
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        readOnly
                        value={inteira ?? mascarar(c)}
                        aria-label={inteira ? t('Chave de API, inteira') : t('Chave de API')}
                        className={`${campo} tabular min-w-0 flex-1`}
                      />
                      {inteira ? <Copiar texto={inteira} /> : null}
                      {dados.pode_revogar && confirmando !== c.id ? (
                        <Button size="sm" variant="quiet" onClick={() => setConfirmando(c.id)}>
                          {t('Revogar')}
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
                        <span className="t-apoio text-paper">{t('Revogar esta chave? O sistema que a usa para na hora.')}</span>
                        <Button
                          size="sm"
                          disabled={revogando === c.id}
                          onClick={() => void revogar(c)}
                          className="border border-danger/60 !bg-danger/20 !text-danger-aviso hover:!bg-danger/30"
                        >
                          {revogando === c.id ? t('Revogando…') : t('Confirmar')}
                        </Button>
                        <Button size="sm" variant="quiet" disabled={revogando === c.id} onClick={() => setConfirmando(null)}>
                          {t('Cancelar')}
                        </Button>
                      </div>
                    ) : null}
                    <p className="t-label mt-1.5 text-muted">
                      {t('Criada em {data} · Último uso: {uso}', { data: fmt.dataCurta(c.criada_em), uso: c.ultimo_uso ? fmt.relativo(c.ultimo_uso, agoraDaTela()) : t('nunca') })}
                    </p>
                  </div>
                )
              })}

              {!dados.plano_permite_gerar ? (
                <Aviso>{t('Gerar chave faz parte do plano Premium.')}</Aviso>
              ) : !dados.pode_gerar ? (
                <Aviso>{t('Seu papel não permite gerar nem revogar chaves.')}</Aviso>
              ) : noLimite ? (
                <Aviso>{t('Limite de {n} chaves ativas. Revogue uma para gerar outra.', { n: dados.limite_ativas })}</Aviso>
              ) : ativas.length === 0 ? (
                <div>
                  <Button onClick={() => void gerar()} disabled={gerando}>
                    {gerando ? t('Gerando…') : t('Gerar chave')}
                  </Button>
                </div>
              ) : (
                <div>
                  <button type="button" onClick={() => void gerar()} disabled={gerando} className={linkDiscreto}>
                    {gerando ? t('Gerando…') : t('Gerar outra chave')}
                  </button>
                </div>
              )}

              {revogadas.length ? (
                <div>
                  <button type="button" aria-expanded={verRevogadas} onClick={() => setVerRevogadas((v) => !v)} className={linkDiscreto}>
                    {verRevogadas ? t('Esconder chaves revogadas ({n})', { n: revogadas.length }) : t('Ver chaves revogadas ({n})', { n: revogadas.length })}
                  </button>
                  {verRevogadas ? (
                    <ul className="mt-2 flex flex-col gap-1.5" aria-label={t('Chaves revogadas')}>
                      {revogadas.map((c) => (
                        <li key={c.id} className="t-label flex flex-wrap items-baseline gap-x-3 text-muted">
                          <span className="tabular text-silver line-through">{mascarar(c)}</span>
                          <span>{t('Revogada em {data}', { data: c.revogada_em ? fmt.dataCurta(c.revogada_em) : '' })}</span>
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

      <Card as="section" className="p-5 md:p-6" aria-label={t('Exemplo de uso')}>
        <button
          type="button"
          aria-expanded={verExemplo}
          onClick={() => setVerExemplo((v) => !v)}
          className="t-h3 flex w-full items-center justify-between gap-3 text-left text-paper"
        >
          {t('Exemplo de uso')}
          <span aria-hidden="true" className="t-label text-silver">
            {verExemplo ? t('Fechar') : t('Abrir')}
          </span>
        </button>
        {verExemplo ? (
          <div className="mt-3">
            <pre className="scroll-fino overflow-x-auto rounded-[12px] border border-line bg-ink/60 p-4 text-rotulo leading-[1.6] text-paper/90">
              <code>{exemplo}</code>
            </pre>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <Copiar texto={exemplo} rotulo={t('Copiar exemplo')} />
              <span className="t-label text-muted">{t('Troque {marcador} pela sua chave.', { marcador: MARCADOR_DA_CHAVE })}</span>
            </div>

            <h4 className="t-label mt-5 font-[600] text-paper">{t('Avisar um evento')}</h4>
            <p className="t-label mt-1 text-silver">
              {t('O cliente c-001 abriu a página de cancelamento. Use em userId o mesmo identificador do cadastro. O messageId evita que um reenvio conte duas vezes.')}
            </p>
            <pre data-exemplo="evento" className="scroll-fino mt-2 overflow-x-auto rounded-[12px] border border-line bg-ink/60 p-4 text-rotulo leading-[1.6] text-paper/90">
              <code>{exemploEvento}</code>
            </pre>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <Copiar texto={exemploEvento} rotulo={t('Copiar exemplo do evento')} />
              <span className="t-label text-muted">{t('Esta chamada sai do servidor da sua empresa. A chave é secreta: não a coloque em página nem em aplicativo.')}</span>
            </div>
          </div>
        ) : null}
      </Card>

      {explicacao ? <PainelOQueAApiFaz onFechar={fecharExplicacao} /> : null}
    </div>
  )
}

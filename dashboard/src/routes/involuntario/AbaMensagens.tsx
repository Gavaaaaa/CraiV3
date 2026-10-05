import { IconArrowRight, IconChat } from '../../components/icons/Icons'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { ErroCarregar, Vazio } from '../../components/ui/Estados'
import { agoraDaTela, api } from '../../data/api'
import type { CicloDetalhe, CicloResumo, SugestaoDoCiclo } from '../../data/tipos'
import { fmt } from '../../lib/format'
import { useCarregar } from '../../lib/useCarregar'
import { ABORDAGEM, CANAL, textoDoPrazo } from './CicloDrawer'

/** Uma cobrança que espera a escolha: a linha da lista e, quando deu para ler, o detalhe dela. */
export interface Pendente {
  resumo: CicloResumo
  detalhe: CicloDetalhe | null
}

/** A sugestão recomendada da rodada mais recente (a que sai sozinha se ninguém escolher). */
export function recomendadaDe(detalhe: CicloDetalhe | null): SugestaoDoCiclo | null {
  if (!detalhe || !detalhe.sugestoes.length) return null
  const rodada = Math.max(...detalhe.sugestoes.map((s) => s.rodada))
  const daRodada = detalhe.sugestoes.filter((s) => s.rodada === rodada)
  return daRodada.find((s) => s.recomendada) ?? daRodada[0] ?? null
}

/**
 * As cobranças que esperam a escolha da empresa agora, lidas da mesma lista para onde o sino
 * levava (`GET /ciclos?aguardando_escolha=true`). A lista não traz o canal, o prazo nem a
 * mensagem recomendada: isso vem do detalhe de cada ciclo (`GET /ciclos/{id}`), lido em
 * paralelo. São poucos ciclos (o número do sino), e um detalhe que falhar deixa a linha só
 * com o que a lista tem.
 */
export function carregarPendentes(incluirSimulados: boolean): Promise<Pendente[]> {
  return api.ciclos({ status: 'todos', aguardandoEscolha: true, incluirSimulados }).then((lista) =>
    Promise.all(lista.map((resumo) => api.ciclo(resumo.id).catch(() => null).then((detalhe) => ({ resumo, detalhe })))),
  )
}

/**
 * A aba "Mensagens" do Involuntário: cada linha é uma cobrança esperando a escolha da mensagem.
 * `onEscolher` abre o ciclo já na escolha; `onAbrir` abre o ciclo de costume (a cobrança sem canal).
 */
export function AbaMensagens({ incluirSimulados, onEscolher, onAbrir }: { incluirSimulados: boolean; onEscolher: (id: number) => void; onAbrir: (id: number) => void }) {
  const carga = useCarregar(() => carregarPendentes(incluirSimulados), [incluirSimulados])
  const pendentes = carga.dados
  const agora = agoraDaTela()

  return (
    <Card className="p-0" data-aba-mensagens>
      <div className="border-b border-line px-5 py-4">
        <h3 className="t-h3 text-paper">Mensagens esperando a sua escolha</h3>
        <p className="t-apoio mt-1 text-silver">
          O sistema escreveu três mensagens para cada cobrança. Sem escolha no prazo, a recomendada é enviada.
        </p>
      </div>
      {carga.erro ? (
        <div className="p-4">
          <ErroCarregar mensagem={carga.erro} onTentar={carga.recarregar} />
        </div>
      ) : pendentes === null ? (
        <ul className="flex flex-col" aria-busy="true" aria-label="Carregando as mensagens">
          {Array.from({ length: 3 }).map((_, i) => (
            <li key={i} className="border-t border-line px-5 py-4 first:border-t-0">
              <div className="h-4 w-1/2 animate-pulse rounded bg-paper/[0.06]" />
              <div className="mt-2 h-3 w-2/3 animate-pulse rounded bg-paper/[0.05]" />
            </li>
          ))}
        </ul>
      ) : pendentes.length === 0 ? (
        <Vazio
          titulo="Nenhuma cobrança espera a sua escolha agora"
          texto="Quando o sistema escrever as três mensagens de uma cobrança, ela aparece aqui."
          icone={<IconChat width={22} height={22} />}
        />
      ) : (
        <ul className="flex flex-col" aria-label="Cobranças esperando a escolha da mensagem">
          {pendentes.map(({ resumo, detalhe }) => {
            const recomendada = recomendadaDe(detalhe)
            const prazo = detalhe?.escolha_ate ?? null
            const semCanal = recomendada?.canal === 'sem_canal'
            return (
              <li key={resumo.id} className="flex flex-col gap-3 border-t border-line px-5 py-4 first:border-t-0 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-[560] text-paper">{resumo.cliente ?? <span className="text-silver">Cliente sem cadastro</span>}</span>
                    <span className="tabular font-[560] text-paper">{fmt.brl(resumo.valor_cobranca)}</span>
                    {resumo.simulado ? <Badge tone="amber">Demonstração</Badge> : null}
                  </div>
                  <div className="t-label mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-muted">
                    <span>{resumo.id_recorrencia}</span>
                    {recomendada ? <span>Canal: {CANAL[recomendada.canal]}</span> : null}
                    {prazo && !semCanal ? (
                      <span className="text-warn">
                        Escolha até {fmt.dataHora(prazo)} · {textoDoPrazo(prazo, agora)}
                      </span>
                    ) : null}
                  </div>
                  {semCanal && recomendada ? (
                    // Sem canal, nada sai: nem agora, nem no prazo. O que o backend faz está em
                    // `enviar_mensagem_escolhida` (reconfere o contato a cada passagem) e em
                    // `varrer_sem_canal_vencidos` (30 dias sem contato: o ciclo é encerrado).
                    <p className="t-apoio mt-2 text-warn" data-sem-canal>
                      <span className="font-[560]">Nada será enviado: {recomendada.motivo_canal.replace(/^./, (c) => c.toLowerCase())}.</span>{' '}
                      O sistema reconfere o contato na sua base a cada passagem; se ele aparecer, a mensagem recomendada sai. Sem contato em 30 dias, a cobrança é encerrada sem recuperação.
                    </p>
                  ) : recomendada ? (
                    <p className="t-apoio mt-2 line-clamp-2 text-silver">
                      <span className="font-[560] text-paper">Recomendada: {ABORDAGEM[recomendada.abordagem]}.</span>{' '}
                      {recomendada.texto ?? 'O texto desta mensagem foi apagado depois do prazo de guarda.'}
                    </p>
                  ) : (
                    <p className="t-apoio mt-2 text-silver">{detalhe ? 'As mensagens desta cobrança ainda não foram geradas.' : 'Abra a cobrança para ver as mensagens.'}</p>
                  )}
                </div>
                {semCanal ? (
                  // Escolher não faz sentido sem canal: o que resolve é o contato na base. O botão abre o ciclo.
                  <Button size="sm" variant="ghost" className="shrink-0" onClick={() => onAbrir(resumo.id)}>
                    Ver a cobrança <IconArrowRight width={15} height={15} />
                  </Button>
                ) : (
                  <Button size="sm" variant="primary" className="shrink-0" onClick={() => onEscolher(resumo.id)}>
                    Escolher a mensagem <IconArrowRight width={15} height={15} />
                  </Button>
                )}
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}

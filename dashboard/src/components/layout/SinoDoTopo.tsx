import { useNavigate } from 'react-router-dom'
import { api } from '../../data/api'
import { useCarregar } from '../../lib/useCarregar'
import { IconBell } from '../icons/Icons'

/** O endereço para onde o sino leva quando há ciclos esperando a escolha. */
export const LISTA_DO_SINO = '/involuntario?filtro=aguardando_escolha'

/**
 * O sino do topo: quantos ciclos esperam a escolha da empresa AGORA (o número do backend) e o
 * caminho até eles. Sem nada pendente, fica sem número e sem ponto de aviso. Atualiza sozinho,
 * como as páginas.
 */
export function SinoDoTopo() {
  const carga = useCarregar(() => api.pendenciasDeEscolha(), [])
  const navegar = useNavigate()
  const pendentes = carga.dados ?? 0
  const rotulo = pendentes
    ? `${pendentes} ${pendentes === 1 ? 'cobrança esperando' : 'cobranças esperando'} a sua escolha de mensagem`
    : 'Nenhuma cobrança esperando a sua escolha de mensagem'
  return (
    <button
      type="button"
      aria-label={rotulo}
      title={rotulo}
      onClick={() => navegar(pendentes ? LISTA_DO_SINO : '/involuntario')}
      className="relative flex h-10 w-10 items-center justify-center rounded-[10px] border border-line bg-slate/60 text-silver hover:text-paper"
    >
      <IconBell width={18} height={18} />
      {pendentes > 0 ? (
        <span
          data-pendentes={pendentes}
          aria-hidden="true"
          className="tabular absolute -top-1.5 -right-1.5 min-w-[22px] rounded-full bg-orange px-1.5 py-px text-center text-rotulo leading-[1.3] font-[680] text-ink"
        >
          {pendentes > 99 ? '99+' : pendentes}
        </span>
      ) : null}
    </button>
  )
}

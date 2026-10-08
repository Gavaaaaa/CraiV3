import { useNavigate } from 'react-router-dom'
import { api } from '../../data/api'
import { useCarregar } from '../../lib/useCarregar'
import { t } from '../../lib/idioma'
import { useModoMensagem } from '../../lib/useModoMensagem'
import { useReducedMotion } from '../../lib/useReducedMotion'
import { IconBell } from '../icons/Icons'

/** O endereço para onde o sino leva quando há ciclos esperando a escolha: a aba Mensagens do Involuntário. */
export const LISTA_DO_SINO = '/involuntario?aba=mensagens'

/**
 * O sino do topo: quantos ciclos esperam a escolha da empresa AGORA (o número do backend) e o
 * caminho até eles. Sem nada pendente, fica sem número e sem ponto de aviso. Atualiza sozinho,
 * como as páginas.
 *
 * O clique leva à aba Mensagens e rola a página para o topo: quem já está no Involuntário vê a
 * aba trocar, em vez de um filtro mudar numa lista abaixo da dobra. Sem pendência, leva ao
 * Involuntário como ele abre de costume.
 *
 * No modo automático o número é o mesmo, mas o texto muda: são mensagens que o sistema ainda vai
 * enviar sozinho, não escolhas que a empresa deve.
 */
export function SinoDoTopo() {
  const carga = useCarregar(() => api.pendenciasDeEscolha(), [])
  const navegar = useNavigate()
  const reduzido = useReducedMotion()
  const pendentes = carga.dados ?? 0
  const automatico = useModoMensagem() === 'automatico'
  const rotulo = automatico
    ? pendentes
      ? pendentes === 1
        ? t('{n} mensagem que o sistema vai enviar sozinho', { n: pendentes })
        : t('{n} mensagens que o sistema vai enviar sozinho', { n: pendentes })
      : t('Nenhuma mensagem pendente')
    : pendentes
      ? pendentes === 1
        ? t('{n} cobrança esperando a sua escolha de mensagem', { n: pendentes })
        : t('{n} cobranças esperando a sua escolha de mensagem', { n: pendentes })
      : t('Nenhuma cobrança esperando a sua escolha de mensagem')
  function abrir() {
    navegar(pendentes ? LISTA_DO_SINO : '/involuntario')
    try {
      window.scrollTo({ top: 0, behavior: reduzido ? 'auto' : 'smooth' })
    } catch {
      /* navegador sem rolagem com opções: a página já trocou de aba */
    }
  }
  return (
    <button
      type="button"
      aria-label={rotulo}
      title={rotulo}
      onClick={abrir}
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

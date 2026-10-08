import { api } from '../data/api'
import type { Configuracao } from '../data/tipos'
import { useCarregar } from './useCarregar'

export type ModoMensagem = Configuracao['modo_mensagem_involuntario']

/**
 * O modo de mensagem do involuntário (Configuração → Mensagens): `escolha` (a empresa escolhe
 * entre as três) ou `automatico` (a recomendada sai sozinha). As telas que falam de "escolher"
 * leem daqui, para não oferecer uma escolha que o backend não aceita no modo automático.
 *
 * Enquanto carrega, ou se a leitura falhar, devolve `null`: quem usa trata `null` como o modo
 * escolha, que é o padrão da empresa nova. Com o backend ligado, relê a cada 60 segundos, como
 * as páginas.
 */
export function useModoMensagem(): ModoMensagem | null {
  return useCarregar(() => api.configuracao().then((c) => c.modo_mensagem_involuntario), []).dados
}

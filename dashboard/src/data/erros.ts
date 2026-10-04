/** Erro que a tela mostra: mensagem em português, sem jargão. */
export type CodigoErro =
  | 'rede' // o servidor não respondeu
  | 'servidor' // respondeu com erro
  | 'nao_autorizado' // 401: token recusado
  | 'sem_permissao' // 403: o papel não permite
  | 'nao_encontrado' // 404
  | 'conflito' // 409: o estado mudou
  | 'invalido' // 422: dado recusado

export class ErroApi extends Error {
  readonly codigo: CodigoErro
  /** Status HTTP, quando a resposta chegou. */
  readonly status: number | null
  /** O `motivo` curto que o backend manda no corpo do erro, quando manda. */
  readonly motivo: string | null

  constructor(codigo: CodigoErro, mensagem: string, status: number | null = null, motivo: string | null = null) {
    super(mensagem)
    this.codigo = codigo
    this.status = status
    this.motivo = motivo
  }
}

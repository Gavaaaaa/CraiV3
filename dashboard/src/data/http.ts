/**
 * A conversa com o backend em MODO REAL: endereço, token de desenvolvimento e erros.
 *
 * - O endereço vem de `VITE_CRAI_API_URL` (em `dashboard/.env.local`). Sem ele, o dashboard
 *   inteiro continua em demonstração e nada daqui é chamado.
 * - O token fica SÓ EM MEMÓRIA (nesta variável de módulo): nunca em `localStorage`,
 *   `sessionStorage` nem cookie. Recarregar a página pede outro.
 * - Nenhum dado de resposta vai para o console.
 */
import { ErroApi } from './erros'
import type { Papel } from './tipos'

export const API_URL = String(import.meta.env.VITE_CRAI_API_URL ?? '')
  .trim()
  .replace(/\/+$/, '')

/** Com `VITE_CRAI_API_URL` definida, as rotas de `ROTAS_REAIS` (api.ts) chamam o backend. */
export const MODO_REAL = API_URL !== ''

let token: string | null = null
let papelAtual: Papel = 'owner'
let pedidoDeToken: Promise<string> | null = null

/** O papel do token de desenvolvimento em uso. */
export const papelDev = (): Papel => papelAtual

/** Troca o papel de desenvolvimento: o token atual é esquecido e o próximo pedido usa o novo papel. */
export function definirPapelDev(papel: Papel): void {
  papelAtual = papel
  token = null
  pedidoDeToken = null
}

async function requisitar(caminho: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_URL}${caminho}`, init)
  } catch {
    throw new ErroApi('rede', 'Não foi possível falar com o servidor da CRAI.')
  }
}

/** `POST /dev/token`: só existe no backend com `ENV=development`. */
async function obterToken(): Promise<string> {
  if (token) return token
  if (!pedidoDeToken) {
    const papel = papelAtual
    const pedido = (async () => {
      const r = await requisitar('/dev/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ papel }),
      })
      if (!r.ok) {
        throw new ErroApi(
          'nao_autorizado',
          r.status === 404
            ? 'O login de desenvolvimento não existe neste servidor. Ele só funciona com o backend em modo de desenvolvimento.'
            : 'Não foi possível entrar no servidor da CRAI.',
          r.status,
        )
      }
      const corpo = (await r.json()) as { token: string }
      // Se o papel mudou enquanto o pedido estava no ar, este token não é guardado.
      if (papel === papelAtual) token = corpo.token
      return corpo.token
    })()
    pedidoDeToken = pedido
    pedido
      .catch(() => undefined)
      .finally(() => {
        if (pedidoDeToken === pedido) pedidoDeToken = null
      })
  }
  return pedidoDeToken
}

/** Pede o token de desenvolvimento já na abertura do painel (o erro fica com quem chamou). */
export async function iniciarSessao(): Promise<void> {
  await obterToken()
}

const CONFLITOS: Record<string, string> = {
  ciclo_nao_aguarda_escolha: 'Este ciclo não está mais esperando uma escolha. A mensagem já foi escolhida ou enviada.',
  prazo_de_escolha_vencido: 'O prazo de escolha acabou. A mensagem recomendada é enviada pelo sistema.',
  modo_automatico: 'A empresa está no modo automático: a mensagem sai sem esperar escolha.',
}

async function erroDaResposta(r: Response): Promise<ErroApi> {
  let motivo: string | null = null
  try {
    const corpo: unknown = await r.json()
    const detalhe = (corpo as { detail?: unknown } | null)?.detail
    if (detalhe && typeof detalhe === 'object' && !Array.isArray(detalhe)) {
      const m = (detalhe as { motivo?: unknown }).motivo
      if (typeof m === 'string') motivo = m
    }
  } catch {
    // Corpo que não é JSON: fica só o status.
  }
  switch (r.status) {
    case 401:
      return new ErroApi('nao_autorizado', 'O servidor não aceitou a sua sessão. Recarregue a página para entrar de novo.', 401, motivo)
    case 403:
      return new ErroApi('sem_permissao', 'Seu papel não permite esta ação.', 403, motivo)
    case 404:
      return new ErroApi('nao_encontrado', 'Não encontramos o que você pediu. Pode ter sido removido ou não ser da sua empresa.', 404, motivo)
    case 409:
      return new ErroApi(
        'conflito',
        (motivo && CONFLITOS[motivo]) || 'Esta ação não é mais possível: a situação mudou enquanto a tela estava aberta. Atualize e tente de novo.',
        409,
        motivo,
      )
    case 422:
      return new ErroApi('invalido', 'O servidor recusou os dados enviados. Confira os campos e tente de novo.', 422, motivo)
    default:
      return new ErroApi('servidor', 'O servidor da CRAI respondeu com erro. Tente de novo em instantes.', r.status, motivo)
  }
}

export interface OpcoesDeChamada {
  /** Rotas públicas (`/health`): não pedem nem enviam token. */
  semToken?: boolean
}

/**
 * Chama uma rota do backend e devolve o JSON. Com 401, esquece o token, pede outro e tenta
 * mais UMA vez (o token vale 1 h e morre quando o backend reinicia).
 */
export async function chamar<T>(
  metodo: 'GET' | 'POST' | 'PUT',
  caminho: string,
  corpo?: unknown,
  opcoes: OpcoesDeChamada = {},
): Promise<T> {
  const enviar = async (): Promise<Response> => {
    const headers: Record<string, string> = {}
    if (corpo !== undefined) headers['Content-Type'] = 'application/json'
    if (!opcoes.semToken) headers.Authorization = `Bearer ${await obterToken()}`
    return requisitar(caminho, { method: metodo, headers, body: corpo !== undefined ? JSON.stringify(corpo) : undefined })
  }
  let resposta = await enviar()
  if (resposta.status === 401 && !opcoes.semToken) {
    token = null
    resposta = await enviar()
  }
  if (!resposta.ok) throw await erroDaResposta(resposta)
  return (await resposta.json()) as T
}

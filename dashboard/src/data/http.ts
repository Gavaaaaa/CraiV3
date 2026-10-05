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

export type Plano = 'essencial' | 'premium'

/** As duas empresas fictícias do login de desenvolvimento: a da demonstração e a dos testes ao vivo. */
export type EmpresaDev = 'demo_dashboard' | 'demo_testes'

let token: string | null = null
let papelAtual: Papel = 'owner'
let planoAtual: Plano = 'premium'
let empresaAtual: EmpresaDev = 'demo_dashboard'
let pedidoDeToken: Promise<string> | null = null

/** O papel do token de desenvolvimento em uso. */
export const papelDev = (): Papel => papelAtual

/** Troca o papel de desenvolvimento: o token atual é esquecido e o próximo pedido usa o novo papel. */
export function definirPapelDev(papel: Papel): void {
  papelAtual = papel
  token = null
  pedidoDeToken = null
}

/** O plano do token de desenvolvimento em uso (premium, a não ser que um teste peça o essencial). */
export const planoDev = (): Plano => planoAtual

/** Troca o plano de desenvolvimento, do mesmo jeito que o papel: o próximo pedido de token usa o novo. */
export function definirPlanoDev(plano: Plano): void {
  planoAtual = plano
  token = null
  pedidoDeToken = null
}

/** A empresa fictícia do token de desenvolvimento em uso (a da demonstração, a não ser que um teste troque). */
export const empresaDev = (): EmpresaDev => empresaAtual

/**
 * Troca a empresa de desenvolvimento. Os testes ao vivo usam `demo_testes`, para não deixar
 * chaves, escolhas nem configuração na empresa da demonstração.
 */
export function definirEmpresaDev(empresa: EmpresaDev): void {
  empresaAtual = empresa
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
    const plano = planoAtual
    const empresa = empresaAtual
    const pedido = (async () => {
      const r = await requisitar('/dev/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // O backend emite premium, da empresa da demonstração, por padrão: o plano e a
        // empresa só vão no corpo quando são outros.
        body: JSON.stringify({ papel, ...(plano === 'premium' ? {} : { plano }), ...(empresa === 'demo_dashboard' ? {} : { empresa }) }),
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
      // Se o papel, o plano ou a empresa mudou enquanto o pedido estava no ar, este token não é guardado.
      if (papel === papelAtual && plano === planoAtual && empresa === empresaAtual) token = corpo.token
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
  limite_de_chaves: 'A empresa já tem o máximo de chaves ativas. Revogue uma para gerar outra.',
  // A simulação do gateway (Rodada 3)
  sem_cliente_ficticio: 'Crie o cliente fictício antes de cobrar.',
  cliente_ja_cobrado: 'Este cliente fictício já foi cobrado. Avance o relógio ou comece outra simulação.',
  sem_simulacao: 'Não há simulação em andamento. Crie um cliente fictício para começar.',
  simulacao_em_uso: 'A simulação está sendo usada por outra ação. Tente de novo em instantes.',
  // Direitos do titular (Rodada 3)
  marca_da_anonimizacao: 'Este cliente foi anonimizado: os contatos dele foram apagados, e não dá para voltar a contatá-lo.',
}

/** O que não foi encontrado (404), quando o backend diz o quê. */
const NAO_ENCONTRADO: Record<string, string> = {
  titular_nao_encontrado: 'Não há dado deste identificador na sua empresa. Confira o identificador que a sua base usa.',
  cliente_nao_encontrado: 'Não encontramos este cliente na sua base. Confira o identificador.',
}

const SEM_PERMISSAO: Record<string, string> = {
  plano_sem_api: 'Gerar chave faz parte do plano Premium.',
}

/** Os limites de uso (429). */
const LIMITES: Record<string, string> = {
  limite_do_assistente: 'A sua empresa chegou ao limite de perguntas ao assistente nesta hora. Tente de novo mais tarde.',
}

/** O que o formulário da simulação do gateway recusa (`POST /simulacao/cliente` e `/simulacao/retencao`). */
const SIMULACAO_RECUSADA: Record<string, string> = {
  dado_que_parece_real: 'O nome parece um dado real (CPF, e-mail, telefone ou chave Pix). Aqui só entra um nome inventado.',
  campo_desconhecido: 'A simulação só aceita os campos do formulário. Nenhum outro dado é enviado.',
}

/** O arquivo da base recusado inteiro (`POST /clientes/importar`). */
const ARQUIVO_RECUSADO: Record<string, string> = {
  extensao_nao_suportada: 'Só CSV ou XLSX. Outros formatos não são lidos.',
  arquivo_vazio: 'O arquivo está vazio.',
  arquivo_grande_demais: 'O arquivo é grande demais. Divida em duas partes.',
  linhas_demais: 'O arquivo tem linhas demais para uma importação. Divida em duas partes.',
  arquivo_ilegivel: 'Não foi possível ler o arquivo. Confira se ele abre na sua planilha.',
  sem_linhas: 'O arquivo só tem o cabeçalho, sem nenhuma linha de cliente.',
  encoding_desconhecido: 'Não foi possível ler o texto do arquivo. Salve como CSV UTF-8 e tente de novo.',
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
      return new ErroApi('sem_permissao', (motivo && SEM_PERMISSAO[motivo]) || 'Seu papel não permite esta ação.', 403, motivo)
    case 404:
      return new ErroApi('nao_encontrado', (motivo && NAO_ENCONTRADO[motivo]) || 'Não encontramos o que você pediu. Pode ter sido removido ou não ser da sua empresa.', 404, motivo)
    case 409:
      return new ErroApi(
        'conflito',
        (motivo && CONFLITOS[motivo]) || 'Esta ação não é mais possível: a situação mudou enquanto a tela estava aberta. Atualize e tente de novo.',
        409,
        motivo,
      )
    case 413:
    case 415:
      return new ErroApi('invalido', (motivo && ARQUIVO_RECUSADO[motivo]) || 'O servidor recusou o arquivo enviado.', r.status, motivo)
    case 422:
      return new ErroApi(
        'invalido',
        (motivo && (ARQUIVO_RECUSADO[motivo] || SIMULACAO_RECUSADA[motivo])) || 'O servidor recusou os dados enviados. Confira os campos e tente de novo.',
        422,
        motivo,
      )
    case 429:
      return new ErroApi('servidor', (motivo && LIMITES[motivo]) || 'Muitas chamadas em pouco tempo. Tente de novo em instantes.', 429, motivo)
    default:
      return new ErroApi('servidor', 'O servidor da CRAI respondeu com erro. Tente de novo em instantes.', r.status, motivo)
  }
}

/**
 * Como `chamar`, para uma rota que devolve TEXTO em vez de JSON (o extrato em CSV): a mesma
 * autenticação, a mesma segunda tentativa no 401 e o mesmo tratamento de erro.
 */
export async function chamarTexto(caminho: string): Promise<string> {
  const enviar = async (): Promise<Response> => requisitar(caminho, { method: 'GET', headers: { Authorization: `Bearer ${await obterToken()}` } })
  let resposta = await enviar()
  if (resposta.status === 401) {
    token = null
    resposta = await enviar()
  }
  if (!resposta.ok) throw await erroDaResposta(resposta)
  return resposta.text()
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
  metodo: 'GET' | 'POST' | 'PUT' | 'DELETE',
  caminho: string,
  corpo?: unknown,
  opcoes: OpcoesDeChamada = {},
): Promise<T> {
  // Um `FormData` (o anexo da base) vai como está: quem escreve o Content-Type, com o limite
  // entre as partes, é o próprio `fetch`.
  const arquivo = typeof FormData !== 'undefined' && corpo instanceof FormData
  const enviar = async (): Promise<Response> => {
    const headers: Record<string, string> = {}
    if (corpo !== undefined && !arquivo) headers['Content-Type'] = 'application/json'
    if (!opcoes.semToken) headers.Authorization = `Bearer ${await obterToken()}`
    const body = corpo === undefined ? undefined : arquivo ? (corpo as FormData) : JSON.stringify(corpo)
    return requisitar(caminho, { method: metodo, headers, body })
  }
  let resposta = await enviar()
  if (resposta.status === 401 && !opcoes.semToken) {
    token = null
    resposta = await enviar()
  }
  if (!resposta.ok) throw await erroDaResposta(resposta)
  return (await resposta.json()) as T
}

/**
 * Um backend de mentira para abrir as telas em MODO REAL dentro do `npm test`, sem backend
 * no ar. Responde só às rotas de `ROTAS_REAIS`, com o mínimo que cada tela precisa (listas
 * vazias, totais zerados). Não é o teste ao vivo: serve para conferir o que a tela MOSTRA
 * quando o modo real está ligado (as etiquetas "Demonstração", por exemplo).
 *
 * Quem usa liga o modo real ANTES dos imports, com `vi.hoisted` e `vi.stubEnv`.
 */
import { screen } from '@testing-library/react'
import { vi } from 'vitest'

export const URL_FALSA = 'http://backend.de.mentira'

const HOJE = new Date()
const dia = (atras: number) => new Date(HOJE.getTime() - atras * 86_400_000).toISOString().slice(0, 10)

const RESPOSTAS: Record<string, unknown> = {
  'POST /dev/token': { token: 'token-de-mentira', tipo: 'Bearer' },
  'GET /ciclos': { ciclos: [], proximo_cursor: null, tem_mais: false },
  'GET /metrics/involuntario/mes': {
    mes: dia(0).slice(0, 7),
    inicio: `${dia(0).slice(0, 7)}-01`,
    fim: dia(0),
    valor_liquido_recuperado: 0,
    recuperados: 0,
    encerrados_sem_recuperacao: 0,
    taxa_recuperacao: null,
    ciclos_abertos_no_mes: { em_analise: 0, em_processo: 0, recuperado: 0, encerrado_sem_recuperacao: 0 },
    aguardando_escolha: 0,
  },
  'GET /metrics/involuntario/serie': {
    dias: 30,
    pontos: Array.from({ length: 30 }, (_, i) => ({ dia: dia(29 - i), valor_liquido_recuperado: 0, recuperados: 0, encerrados_sem_recuperacao: 0, taxa_recuperacao: null })),
  },
  'GET /configuracao': {
    configuracao: {
      modo_mensagem_involuntario: 'escolha',
      prazo_escolha_horas: 8,
      janela_contato_inicio: '08:00',
      janela_contato_fim: '20:00',
      canais_permitidos: ['whatsapp', 'email'],
      retencao_mensagens_dias: 90,
      retencao_ciclos_meses: 24,
      retencao_base_meses_apos_contrato: 6,
      retencao_trilha_anos: 5,
    },
    pode_editar: true,
  },
  'GET /health': { status: 'ok', relogio: { ligado: true, motivo_desligado: null, ultima_passagem_em: HOJE.toISOString(), ultima_passagem_ok: true } },
  'GET /integracao/chaves': { chaves: [], ativas: 0, limite_ativas: 5, pode_revogar: true, plano_permite_gerar: true, pode_gerar: true },
}

/** Troca o `fetch` por um que responde pelas rotas acima. Rota desconhecida reprova o teste. */
export function ligarBackendFalso(): string[] {
  const chamadas: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const caminho = url.replace(URL_FALSA, '').split('?')[0]
      const chave = `${init.method ?? 'GET'} ${caminho}`
      chamadas.push(chave)
      if (!(chave in RESPOSTAS)) throw new Error(`o backend de mentira não conhece ${chave}`)
      return new Response(JSON.stringify(RESPOSTAS[chave]), { status: 200 })
    }),
  )
  return chamadas
}

/** O que o jsdom não tem e as telas usam. */
export function prepararJsdom(): void {
  window.matchMedia = ((consulta: string) => ({
    matches: false,
    media: consulta,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Element.prototype.scrollIntoView = () => undefined
  Element.prototype.scrollTo = (() => undefined) as typeof Element.prototype.scrollTo
  // O gráfico de 30 dias mede a própria largura; no jsdom não há o que medir.
  if (!('ResizeObserver' in globalThis)) {
    vi.stubGlobal(
      'ResizeObserver',
      class {
        observe(): void {}
        unobserve(): void {}
        disconnect(): void {}
      },
    )
  }
}

/** As etiquetas têm exatamente este texto (os avisos antigos são frases maiores). */
export const etiquetas = () => screen.queryAllByText('Demonstração', { exact: true })

/**
 * Todas as páginas do painel fora da Simulação do gateway, com as abas e as seções, e um
 * texto que só aparece quando a página terminou de carregar.
 */
export const PAGINAS: { caminho: string; ancora: string | RegExp }[] = [
  { caminho: '/', ancora: 'Mantido para você nos últimos 30 dias' },
  { caminho: '/?aba=caminho', ancora: 'Mantido para você nos últimos 30 dias' },
  { caminho: '/?aba=funciona', ancora: 'Mantido para você nos últimos 30 dias' },
  { caminho: '/?aba=extrato', ancora: 'Mantido para você nos últimos 30 dias' },
  { caminho: '/?aba=atividade', ancora: 'Mantido para você nos últimos 30 dias' },
  { caminho: '/?aba=saude', ancora: 'Relógio das tentativas' },
  { caminho: '/involuntario', ancora: /^Recuperado para você em / },
  { caminho: '/voluntario', ancora: 'Mantido para você em setembro' },
  { caminho: '/voluntario?aba=clientes', ancora: 'Mantido para você em setembro' },
  { caminho: '/voluntario?aba=mantido', ancora: 'Mantido para você em setembro' },
  { caminho: '/voluntario?aba=decisao', ancora: 'Mantido para você em setembro' },
  { caminho: '/assistente', ancora: /O que o assistente vê/ },
  { caminho: '/configuracao', ancora: 'Quem escolhe a mensagem' },
  { caminho: '/configuracao?secao=empresa', ancora: 'Identificação' },
  { caminho: '/configuracao?secao=equipe', ancora: 'Membros' },
  { caminho: '/configuracao?secao=integracao', ancora: 'Webhook' },
  { caminho: '/configuracao?secao=dados', ancora: 'Por quanto tempo a CRAI guarda' },
  { caminho: '/configuracao?secao=notificacoes', ancora: 'Quando avisar' },
  { caminho: '/api', ancora: 'Você ainda não tem uma chave' },
]

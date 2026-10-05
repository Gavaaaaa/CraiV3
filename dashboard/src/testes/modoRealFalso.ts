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
  'GET /health': {
    status: 'ok',
    relogio: { ligado: true, motivo_desligado: null, ultima_passagem_em: HOJE.toISOString(), ultima_passagem_ok: true },
    modelos: { carregados: 4, total: 4, ausentes: [] },
    redator: { disponivel: true },
    base: { informada: true, atualizada_em: null, origem: null },
  },
  'GET /integracao/chaves': { chaves: [], ativas: 0, limite_ativas: 5, pode_revogar: true, plano_permite_gerar: true, pode_gerar: true },
  // Rodada 3, Fase 1: a página do voluntário, com a base vazia.
  'GET /clientes/recentes': { clientes: [], total_na_base: 0 },
  'GET /clientes/base': { base: null },
  'GET /metrics/voluntario/mes': {
    mes: dia(0).slice(0, 7),
    valor_liquido_mantido: 0,
    clientes_mantidos: 0,
    estornos: { quantidade: 0, valor_liquido_estornado: 0 },
    aceites_sem_valor: 0,
    grave: 0,
    preocupante: 0,
    ofertas_enviadas: 0,
    ofertas_aceitas: 0,
    meses_de_mrr: 1,
    prazo_estorno_dias: 30,
  },
  'GET /metrics/voluntario/serie': {
    dias: 30,
    pontos: Array.from({ length: 30 }, (_, i) => ({ dia: dia(29 - i), valor_liquido_mantido: 0, clientes_mantidos: 0, valor_liquido_estornado: 0 })),
  },
  'GET /metrics/voluntario/regua-x-modelo': { dias: 30, minimo_de_cancelamentos: 5, comparacao: null, motivo_vazio: 'modelo_inativo' },
  // Rodada 3, Fase 2: a visão geral, sem nada recuperado nem mantido.
  'GET /metrics/visao-geral': {
    dias: 30,
    periodo: { de: dia(29), ate: dia(0) },
    recuperado_involuntario: 0,
    cobrancas_recuperadas: 0,
    retido_voluntario: 0,
    clientes_mantidos: 0,
    ciclos_ativos: 0,
    aguardando_escolha: 0,
    clientes_risco_grave: 0,
    risco_grave_com_oferta: 0,
    taxa_recuperacao: null,
    ciclos_com_desfecho: 0,
    mantido: 0,
  },
  'GET /metrics/serie': { dias: 30, pontos: Array.from({ length: 30 }, (_, i) => ({ dia: dia(29 - i), involuntario: 0, voluntario: 0 })) },
  'GET /metrics/involuntario/funil': {
    mes: dia(0).slice(0, 7),
    etapas: [
      ['falhas', 'Cobranças que falharam'],
      ['tentativa_1', 'Tentativa 1'],
      ['tentativa_2', 'Tentativa 2'],
      ['tentativa_3', 'Tentativa 3'],
      ['mensagem', 'Mensagem'],
    ].map(([etapa, rotulo]) => ({ etapa, rotulo, chegaram: 0, valor: 0, recuperados_aqui: 0, valor_recuperado_aqui: 0 })),
    desfecho: { recuperados: 0, encerrados: 0, em_andamento: 0 },
  },
  'GET /metrics/o-que-funciona': { dias: 30, causas: [], ofertas: [], canais: [] },
  'GET /atividade': { dias: 30, atividades: [] },
  'GET /extrato': { mes: dia(0).slice(0, 7), linhas: [], totais: { valor_base: 0, fee: 0, liquido: 0 }, meses_de_mrr: 1 },
  // Rodada 3, Fase 3: a empresa nunca simulou nada.
  'GET /simulacao': {
    existe: false,
    relogio: null,
    cliente: null,
    id_recorrencia: null,
    cobranca: null,
    ciclo: null,
    tentativas: [],
    proxima_acao: null,
    desfecho: null,
    pensando: [],
    sem_crai: null,
    modo_mensagem: 'escolha',
    prazo_escolha_horas: 8,
  },
}

/** O texto fixo do assistente, como o backend responde sem o LLM. */
RESPOSTAS['POST /assistente'] = {
  texto: 'O assistente está indisponível agora. Os números continuam certos nas páginas do painel: Visão geral, Involuntário e Voluntário.',
  links: [{ rotulo: 'Ver a visão geral', para: '/' }],
  sugestoes: [],
  origem: 'ajuda',
}

/** O texto para a política, como o backend o devolve (com os prazos da empresa já postos). */
RESPOSTAS['GET /titular/texto-para-politica'] = {
  titulo: 'O que a CRAI faz com os dados dos nossos clientes',
  texto: 'Usamos a CRAI, um serviço de recuperação de cobranças e de retenção de clientes.\n\n**Mensagens.** Você recebe no máximo uma oferta de retenção a cada 30 dias. Para não receber mais mensagens, responda SAIR a qualquer mensagem.',
  prazos: { intervalo_minimo_ofertas_dias: 30, retencao_mensagens_dias: 90, retencao_trilha_anos: 5 },
  arquivo: 'docs/lgpd/texto-para-politica-de-privacidade.md',
}

/** O corpo JSON de cada chamada com corpo, na ordem: `{ chave: 'POST /rota', corpo }`. */
let corpos: { chave: string; corpo: unknown }[] = []
export const corposEnviados = () => corpos

/**
 * Troca o `fetch` por um que responde pelas rotas acima. Rota desconhecida reprova o teste.
 * `outras` troca ou acrescenta respostas (o teste que quer ver um dado na tela manda o dado).
 */
export function ligarBackendFalso(outras: Record<string, unknown> = {}): string[] {
  const chamadas: string[] = []
  const respostas = { ...RESPOSTAS, ...outras }
  corpos = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      const caminho = url.replace(URL_FALSA, '').split('?')[0]
      const chave = `${init.method ?? 'GET'} ${caminho}`
      chamadas.push(`${init.method ?? 'GET'} ${url.replace(URL_FALSA, '')}`)
      if (typeof init.body === 'string') corpos.push({ chave, corpo: JSON.parse(init.body) })
      if (!(chave in respostas)) throw new Error(`o backend de mentira não conhece ${chave}`)
      // `{ __status, corpo }` responde com erro (o 403 do membro no extrato, por exemplo).
      const r = respostas[chave] as { __status?: number; corpo?: unknown } | null
      if (r && typeof r === 'object' && typeof r.__status === 'number') return new Response(JSON.stringify(r.corpo ?? {}), { status: r.__status })
      return new Response(JSON.stringify(respostas[chave]), { status: 200 })
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
  { caminho: '/voluntario', ancora: /^Mantido para você em / },
  { caminho: '/voluntario?aba=clientes', ancora: /^Mantido para você em / },
  { caminho: '/voluntario?aba=mantido', ancora: /^Mantido para você em / },
  { caminho: '/voluntario?aba=decisao', ancora: /^Mantido para você em / },
  { caminho: '/assistente', ancora: /O que o assistente vê/ },
  { caminho: '/configuracao', ancora: 'Quem escolhe a mensagem' },
  { caminho: '/configuracao?secao=empresa', ancora: 'Identificação' },
  { caminho: '/configuracao?secao=equipe', ancora: 'Membros' },
  { caminho: '/configuracao?secao=integracao', ancora: 'Webhook' },
  { caminho: '/configuracao?secao=dados', ancora: 'Por quanto tempo a CRAI guarda' },
  { caminho: '/configuracao?secao=notificacoes', ancora: 'Quando avisar' },
  { caminho: '/api', ancora: 'Você ainda não tem uma chave' },
]

/**
 * Adaptadores: traduzem a resposta do backend (Etapa 2) para os tipos que as telas já usam.
 *
 * São funções puras, sem rede e sem estado, e têm teste (`adaptadores.test.ts`) com as respostas
 * de exemplo dos relatórios do backend. Regras que valem aqui:
 *
 * - a `fee` (taxa da CRAI) nunca aparece: o backend não manda, e nenhum tipo da tela a tem;
 * - nenhum contato do cliente final (telefone, e-mail, CPF) passa: o backend não manda, e o
 *   adaptador só copia campos conhecidos, nunca o objeto inteiro;
 * - todo texto que vai para a tela começa com letra maiúscula.
 */
import type {
  Abordagem,
  Canal,
  CausaFalha,
  CicloDetalhe,
  CicloResumo,
  Configuracao,
  ContribuicaoTexto,
  EstadoCiclo,
  EventoLinhaDoTempo,
  MetricasMes,
  PontoSerie,
  SaudeSistema,
  StatusTela,
  SugestaoDoCiclo,
} from './tipos'

/* ------------------------------------------------------------------ */
/* O que o backend manda                                                */
/* ------------------------------------------------------------------ */

export type StatusApi = 'em_analise' | 'em_processo' | 'recuperado' | 'encerrado_sem_recuperacao'
export type AbordagemApi = 'lembrete_cordial' | 'facilitacao' | 'urgencia_respeitosa'

/** Uma linha de `GET /ciclos` (e o campo `ciclo` de `GET /ciclos/{id}`). */
export interface CicloApi {
  id: number
  status: StatusApi
  estado: string
  id_recorrencia: string
  cliente_nome: string | null
  valor_cobranca: number
  valor_liquido: number | null
  causa: string | null
  causa_legivel: string | null
  tentativas_executadas: number
  aberto_em: string
  atualizado_em: string
  desfecho_em: string | null
  motivo_descarte: string | null
  motivo_perdido: string | null
  mensagem: { reservada_em: string | null; enviada_em: string | null } | null
  /** Estorno (Etapa 2, Bloco 5). Ausentes num backend anterior ao Bloco 5. */
  estorno?: EstornoApi | null
  estorno_parcial?: boolean
  motivo_encerramento?: string | null
}

/** O estorno acumulado dentro do prazo. A fee não vem: só quanto da cobrança voltou. */
export interface EstornoApi {
  valor_devolvido: number
  total: boolean
  ultimo_em: string | null
}

export interface ListaDeCiclosApi {
  ciclos: CicloApi[]
  proximo_cursor: string | null
  tem_mais: boolean
}

export interface MensagemApi {
  rodada: number
  abordagem: string
  texto: string | null
  canal: string
  motivo_canal: string
  recomendada: boolean
  escolhida: boolean
  nao_entregavel: boolean
  gerada_em: string | null
  enviada_em: string | null
}

export interface EventoApi {
  quando: string
  tipo: string
  dados: Record<string, unknown>
}

export interface CicloDetalheApi {
  ciclo: CicloApi
  diagnostico: {
    decidido_em: string | null
    explicacao: string
    com_modelo: boolean
    contribuicoes: { fator: string; efeito: string | null }[]
  } | null
  mensagens: MensagemApi[]
  modo_mensagem: 'automatico' | 'escolha'
  escolha_ate: string | null
  escolhida_por: 'owner' | 'admin' | 'prazo' | 'automatico' | null
  linha_do_tempo: EventoApi[]
  trilha_ambigua: boolean
}

export interface MetricasMesApi {
  mes: string
  inicio: string
  fim: string
  valor_liquido_recuperado: number
  recuperados: number
  encerrados_sem_recuperacao: number
  taxa_recuperacao: number | null
  ciclos_abertos_no_mes: Record<StatusApi, number>
  aguardando_escolha: number
  /** Bloco 5: os estornos no prazo recebidos no mês (já descontados do valor líquido). */
  estornos?: { quantidade: number; ciclos_estornados_por_inteiro: number; valor_liquido_estornado: number }
}

export interface SerieApi {
  dias: number
  pontos: { dia: string; valor_liquido_recuperado: number; recuperados: number; encerrados_sem_recuperacao: number; taxa_recuperacao: number | null }[]
}

export interface ConfiguracaoApi {
  modo_mensagem_involuntario: 'automatico' | 'escolha'
  prazo_escolha_horas: number
  janela_contato_inicio: string // "08:00"
  janela_contato_fim: string // "20:00" (aceita "24:00")
  canais_permitidos: string[]
  retencao_mensagens_dias: number
  retencao_ciclos_meses: number
  retencao_base_meses_apos_contrato: number
  retencao_trilha_anos: number
  [outra: string]: unknown
}

export interface RespostaConfiguracaoApi {
  configuracao: ConfiguracaoApi
  pode_editar: boolean
}

export interface SaudeApi {
  status: string
  relogio: {
    ligado: boolean
    motivo_desligado: string | null
    ultima_passagem_em: string | null
    ultima_passagem_ok: boolean | null
  }
}

/* ------------------------------------------------------------------ */
/* Pequenas traduções                                                   */
/* ------------------------------------------------------------------ */

const brl = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' })

/** Todo texto de tela começa com maiúscula. */
export function maiuscula(texto: string): string {
  return texto ? texto.charAt(0).toUpperCase() + texto.slice(1) : texto
}

/** R10: o backend diz `encerrado_sem_recuperacao`; a tela, `encerrado`. */
export function statusDaTela(status: StatusApi): StatusTela {
  return status === 'encerrado_sem_recuperacao' ? 'encerrado' : status
}

export function statusParaApi(status: StatusTela): StatusApi {
  return status === 'encerrado' ? 'encerrado_sem_recuperacao' : status
}

export function abordagemDaTela(abordagem: string): Abordagem {
  if (abordagem === 'urgencia_respeitosa') return 'urgencia_com_respeito'
  if (abordagem === 'facilitacao') return 'facilitacao'
  return 'lembrete_cordial'
}

export function abordagemParaApi(abordagem: Abordagem): AbordagemApi {
  return abordagem === 'urgencia_com_respeito' ? 'urgencia_respeitosa' : abordagem
}

const ABORDAGEM_LEGIVEL: Record<Abordagem, string> = {
  lembrete_cordial: 'Lembrete cordial',
  facilitacao: 'Facilitação',
  urgencia_com_respeito: 'Urgência com respeito',
}

const CANAL_LEGIVEL: Record<Canal, string> = {
  whatsapp: 'WhatsApp',
  email: 'E-mail',
  sms: 'SMS',
  sem_canal: 'Sem canal disponível',
}

function canalDaTela(canal: unknown): Canal {
  return canal === 'whatsapp' || canal === 'email' || canal === 'sms' ? canal : 'sem_canal'
}

const CAUSAS: CausaFalha[] = ['insufficient_funds', 'limit_exceeded', 'authorization_revoked', 'processing_error', 'generic_decline']

function causaDaTela(causa: string | null): CausaFalha {
  return CAUSAS.includes(causa as CausaFalha) ? (causa as CausaFalha) : 'generic_decline'
}

const ESTADOS: EstadoCiclo[] = ['recobrando', 'aguardando_escolha', 'mensagem_enviada', 'recuperado', 'perdido', 'descartado']

function estadoDaTela(estado: string, status: StatusApi): EstadoCiclo {
  if (ESTADOS.includes(estado as EstadoCiclo)) return estado as EstadoCiclo
  // Estado que esta versão da tela não conhece: o status (R10) diz onde ele aparece.
  if (status === 'recuperado') return 'recuperado'
  if (status === 'encerrado_sem_recuperacao') return 'perdido'
  return 'recobrando'
}

/** O motivo do canal vem como código fechado; a tela mostra uma frase. */
export function motivoDoCanal(codigo: string): string {
  switch (codigo) {
    case 'contato_da_base':
      return 'Contato cadastrado na sua base'
    case 'sem_contato':
      return 'O cliente não tem telefone nem e-mail na sua base'
    case 'sem_mapeamento':
      return 'O cliente desta cobrança não está na sua base'
    case 'presumido_sem_contato':
    case 'presumido_sem_mapeamento':
      return 'Canal presumido: o cliente não tem contato na base'
    default:
      return 'Canal escolhido pelo sistema'
  }
}

const MOTIVO_DESCARTE: Record<string, string> = {
  eprofit_nao_positivo: 'Retorno esperado abaixo do custo da ação',
  score_abaixo_do_corte: 'Chance de recuperação baixa demais para agir',
  autorizacao_revogada: 'O cliente revogou a autorização do Pix Automático',
  janela_encerrada: 'A janela de novas tentativas acabou',
}

function motivoLegivel(codigo: string | null): string | null {
  if (!codigo) return null
  return MOTIVO_DESCARTE[codigo] ?? maiuscula(codigo.replace(/_/g, ' '))
}

/* ------------------------------------------------------------------ */
/* Ciclos                                                               */
/* ------------------------------------------------------------------ */

/** `GET /ciclos`: uma linha da tabela. Copia campo a campo: o que o tipo não tem, não passa. */
export function adaptarCiclo(c: CicloApi): CicloResumo {
  return {
    id: c.id,
    cliente: c.cliente_nome,
    id_recorrencia: c.id_recorrencia,
    valor_cobranca: c.valor_cobranca,
    valor_liquido: c.valor_liquido,
    causa: causaDaTela(c.causa),
    causa_legivel: c.causa_legivel,
    estado: estadoDaTela(c.estado, c.status),
    status: statusDaTela(c.status),
    tentativas_executadas: c.tentativas_executadas,
    // A regra do Pix Automático dá até 3 novas tentativas; ciclo descartado não tenta.
    tentativas_total: c.estado === 'descartado' ? 0 : 3,
    // O backend ainda não informa a próxima ação na lista (pendência para a Etapa 3).
    proxima_acao: null,
    proxima_acao_descricao: null,
    aberto_em: c.aberto_em,
    atualizado_em: c.atualizado_em,
    // O backend ainda não marca ciclo de simulação (pendência para a Etapa 3).
    simulado: false,
    estorno: c.estorno
      ? { valor_devolvido: c.estorno.valor_devolvido, total: c.estorno.total, parcial: !c.estorno.total, ultimo_em: c.estorno.ultimo_em }
      : null,
  }
}

export function adaptarMensagem(m: MensagemApi): SugestaoDoCiclo {
  return {
    rodada: m.rodada,
    abordagem: abordagemDaTela(m.abordagem),
    texto: m.texto,
    canal: canalDaTela(m.canal),
    motivo_canal: motivoDoCanal(m.motivo_canal),
    recomendada: m.recomendada,
    escolhida: m.escolhida,
    nao_entregavel: m.nao_entregavel,
  }
}

export function adaptarContribuicoes(itens: { fator: string; efeito: string | null }[]): ContribuicaoTexto[] {
  return itens.map((c) => ({ fator: maiuscula(c.fator), efeito: c.efeito ? maiuscula(c.efeito) : null }))
}

const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null)
const numero = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

const ORDINAL = (n: number | null) => (n === null ? '' : ` ${n}`)

const DECISAO: Record<string, string> = {
  risco: 'Decisão registrada: avaliação da cobrança',
  retentativa: 'Decisão registrada: nova tentativa',
  oferta: 'Decisão registrada: mensagem',
  canal: 'Decisão registrada: canal',
}

const QUEM_ESCOLHEU: Record<string, string> = {
  owner: 'Escolhida pelo dono',
  admin: 'Escolhida por um administrador',
  prazo: 'O prazo de escolha acabou: saiu a recomendada',
  automatico: 'Modo automático: saiu a recomendada',
}

/**
 * Um evento da linha do tempo do backend (`{quando, tipo, dados}`) vira o que a tela desenha
 * (`{em, tipo, titulo, detalhe, tom}`). Devolve null para o que a tela não mostra (a reserva
 * interna do envio). Tipo desconhecido NÃO some: vira um aviso neutro.
 */
export function adaptarEvento(e: EventoApi): EventoLinhaDoTempo | null {
  const d = e.dados ?? {}
  const n = numero(d.numero)
  switch (e.tipo) {
    case 'abertura':
      return { em: e.quando, tipo: 'abertura', titulo: 'Cobrança falhou', detalhe: texto(d.causa_legivel) ?? undefined, tom: 'danger' }
    case 'diagnostico': {
      const p = numero(d.p_recovery)
      return {
        em: e.quando,
        tipo: 'diagnostico',
        titulo: p !== null ? `Diagnóstico: ${Math.round(p * 100)}% de chance de recuperar` : 'Diagnóstico concluído',
      }
    }
    case 'decisao':
      return {
        em: e.quando,
        tipo: 'diagnostico',
        titulo: DECISAO[String(d.tipo_decisao)] ?? 'Decisão registrada',
        detalhe: texto(d.explicacao) ?? undefined,
      }
    case 'tentativa_agendada':
      return { em: e.quando, tipo: 'tentativa', titulo: `Tentativa${ORDINAL(n)} agendada` }
    case 'tentativa_disparada':
      return { em: e.quando, tipo: 'tentativa', titulo: `Tentativa${ORDINAL(n)} enviada ao banco` }
    case 'tentativa_resultado': {
      const r = texto(d.resultado)
      if (r === 'paga') return { em: e.quando, tipo: 'tentativa', titulo: `Tentativa${ORDINAL(n)} paga`, tom: 'ok' }
      if (r === 'falhou') return { em: e.quando, tipo: 'tentativa', titulo: `Tentativa${ORDINAL(n)} falhou`, tom: 'danger' }
      if (r === 'sem_retorno') return { em: e.quando, tipo: 'tentativa', titulo: `Tentativa${ORDINAL(n)} sem resposta do banco`, tom: 'warn' }
      return { em: e.quando, tipo: 'tentativa', titulo: `Tentativa${ORDINAL(n)}: resultado registrado` }
    }
    case 'tentativa_cancelada': {
      const motivo = texto(d.motivo)
      return {
        em: e.quando,
        tipo: 'tentativa',
        titulo: `Tentativa${ORDINAL(n)} cancelada`,
        detalhe: motivo === 'recuperado' ? 'O pagamento entrou antes' : (motivoLegivel(motivo) ?? undefined),
      }
    }
    case 'sugestoes_geradas': {
      const rodada = numero(d.rodada) ?? 1
      const recomendada = texto(d.recomendada)
      const canal = CANAL_LEGIVEL[canalDaTela(d.canal)]
      return {
        em: e.quando,
        tipo: 'sugestoes',
        titulo: rodada > 1 ? `Outras 3 mensagens sugeridas (${rodada}ª rodada)` : '3 mensagens sugeridas',
        detalhe: recomendada ? `Recomendada: ${ABORDAGEM_LEGIVEL[abordagemDaTela(recomendada)]}. Canal: ${canal}.` : `Canal: ${canal}.`,
      }
    }
    case 'mensagem_escolhida': {
      const abordagem = texto(d.abordagem)
      const quem = QUEM_ESCOLHEU[String(d.escolhida_por)] ?? 'Mensagem escolhida'
      return {
        em: e.quando,
        tipo: 'escolha',
        titulo: abordagem ? `Mensagem escolhida: ${ABORDAGEM_LEGIVEL[abordagemDaTela(abordagem)]}` : 'Mensagem escolhida',
        detalhe: `${quem}.`,
      }
    }
    case 'mensagem_nao_entregavel':
      return { em: e.quando, tipo: 'aviso', titulo: 'Sem canal disponível', detalhe: `${motivoDoCanal(String(d.motivo_canal))}. A mensagem não foi entregue.`, tom: 'warn' }
    case 'mensagem_reservada':
      return null // passo interno do envio; a tela mostra só a mensagem enviada
    case 'mensagem_enviada':
      return { em: e.quando, tipo: 'mensagem', titulo: 'Mensagem enviada', tom: 'ok' }
    case 'recuperado': {
      const liquido = numero(d.valor_liquido)
      return { em: e.quando, tipo: 'desfecho', titulo: 'Pagamento recuperado', detalhe: liquido !== null ? `${brl.format(liquido)} líquidos para você.` : undefined, tom: 'ok' }
    }
    case 'perdido':
      return {
        em: e.quando,
        tipo: 'desfecho',
        titulo: 'Encerrado sem recuperação',
        detalhe: texto(d.motivo_perdido) === 'sem_canal' ? 'Não havia canal para entregar a mensagem em 30 dias.' : 'O pagamento não veio no prazo depois da mensagem.',
      }
    case 'descartado':
      return { em: e.quando, tipo: 'desfecho', titulo: 'Encerrado sem ação', detalhe: motivoLegivel(texto(d.motivo_descarte)) ?? undefined }
    case 'estorno': {
      const valor = numero(d.valor_devolvido)
      const noPrazo = d.no_prazo === true
      return {
        em: e.quando,
        tipo: 'aviso',
        titulo: noPrazo ? 'Pagamento devolvido dentro do prazo' : 'Pagamento devolvido depois do prazo',
        detalhe:
          (valor !== null ? `${brl.format(valor)} devolvidos ao cliente. ` : '') +
          (noPrazo
            ? d.total === true
              ? 'Todo o valor voltou: esta cobrança deixou de contar como recuperada.'
              : 'O valor saiu do que foi recuperado para você.'
            : 'O prazo de estorno já tinha acabado: os valores não mudam.'),
        tom: noPrazo ? 'danger' : 'neutro',
      }
    }
    default:
      return { em: e.quando, tipo: 'aviso', titulo: 'Evento registrado' }
  }
}

/** `GET /ciclos/{id}`: o painel lateral. */
export function adaptarDetalhe(d: CicloDetalheApi): CicloDetalhe {
  const diagnostico = d.linha_do_tempo.find((e) => e.tipo === 'diagnostico')
  return {
    ...adaptarCiclo(d.ciclo),
    chance_recuperar: diagnostico ? numero(diagnostico.dados?.p_recovery) : null,
    dia_provavel_saldo: null,
    contribuicoes: adaptarContribuicoes(d.diagnostico?.contribuicoes ?? []),
    tentativas: [],
    sugestoes: [...d.mensagens].sort((a, b) => a.rodada - b.rodada).map(adaptarMensagem),
    modo_mensagem: d.modo_mensagem,
    escolha_ate: d.escolha_ate,
    escolha_por: d.escolhida_por,
    motivo_descarte: motivoLegivel(d.ciclo.motivo_descarte),
    linha_do_tempo: d.linha_do_tempo.map(adaptarEvento).filter((e): e is EventoLinhaDoTempo => e !== null),
  }
}

/* ------------------------------------------------------------------ */
/* Métricas                                                             */
/* ------------------------------------------------------------------ */

/**
 * `GET /metrics/involuntario/mes`. `ciclosAtivos` vem de fora (a contagem de `GET /ciclos` em
 * análise ou em processo): o resumo do mês só conta os ciclos ABERTOS no mês.
 */
export function adaptarMetricas(m: MetricasMesApi, ciclosAtivos: number): MetricasMes {
  return {
    mes: m.mes,
    valor_liquido_recuperado: m.valor_liquido_recuperado,
    ciclos_ativos: ciclosAtivos,
    recuperados: m.recuperados,
    encerrados_sem_recuperacao: m.encerrados_sem_recuperacao,
    aguardando_escolha: m.aguardando_escolha,
    taxa_recuperacao: m.taxa_recuperacao,
    // O backend ainda não informa a próxima ação do sistema (pendência para a Etapa 3).
    proxima_acao: null,
  }
}

/** `GET /metrics/involuntario/serie`: um ponto por dia, líquido. */
export function adaptarSerie(s: SerieApi): PontoSerie[] {
  return s.pontos.map((p) => ({ dia: p.dia, valor: p.valor_liquido_recuperado }))
}

/* ------------------------------------------------------------------ */
/* Configuração                                                         */
/* ------------------------------------------------------------------ */

/** "08:00" → 8; "24:00" → 24. A tela trabalha com horas cheias. */
export function horaDaTela(hhmm: string): number {
  const h = Number.parseInt(String(hhmm).split(':')[0] ?? '', 10)
  return Number.isFinite(h) ? Math.min(24, Math.max(0, h)) : 0
}

export function horaParaApi(hora: number): string {
  return `${String(hora).padStart(2, '0')}:00`
}

/** Os canais que o backend aceita hoje na configuração do involuntário. */
export const CANAIS_DO_BACKEND: Canal[] = ['whatsapp', 'email']

/**
 * `GET /configuracao`. As notificações ainda não existem no backend: vêm de `locais`
 * (demonstração). Os prazos de retenção são do backend e só leitura na tela.
 */
export function adaptarConfiguracao(c: ConfiguracaoApi, locais: Pick<Configuracao, 'notificacoes'>): Configuracao {
  return {
    modo_mensagem_involuntario: c.modo_mensagem_involuntario,
    prazo_escolha_horas: c.prazo_escolha_horas,
    janela_contato: { inicio: horaDaTela(c.janela_contato_inicio), fim: horaDaTela(c.janela_contato_fim) },
    canais: c.canais_permitidos.map(canalDaTela).filter((x) => x !== 'sem_canal'),
    notificacoes: { ...locais.notificacoes },
    retencao_dias: {
      mensagens: c.retencao_mensagens_dias,
      ciclos_meses: c.retencao_ciclos_meses,
      base_meses_apos_contrato: c.retencao_base_meses_apos_contrato,
      trilha_anos: c.retencao_trilha_anos,
    },
  }
}

/**
 * O corpo do `PUT /configuracao`: SÓ o que mudou em relação ao que foi lido (o PUT é parcial).
 * Assim uma janela com minutos (ex.: 18:30), que a tela mostra arredondada, não é regravada
 * sem querer. Canal que o backend não aceita (SMS) não é enviado.
 */
export function configuracaoParaApi(nova: Configuracao, lida: Configuracao): Partial<ConfiguracaoApi> {
  const corpo: Partial<ConfiguracaoApi> = {}
  if (nova.modo_mensagem_involuntario !== lida.modo_mensagem_involuntario) corpo.modo_mensagem_involuntario = nova.modo_mensagem_involuntario
  if (nova.prazo_escolha_horas !== lida.prazo_escolha_horas) corpo.prazo_escolha_horas = nova.prazo_escolha_horas
  if (nova.janela_contato.inicio !== lida.janela_contato.inicio) corpo.janela_contato_inicio = horaParaApi(nova.janela_contato.inicio)
  if (nova.janela_contato.fim !== lida.janela_contato.fim) corpo.janela_contato_fim = horaParaApi(nova.janela_contato.fim)
  const canais = nova.canais.filter((c) => CANAIS_DO_BACKEND.includes(c))
  if (canais.join(',') !== lida.canais.filter((c) => CANAIS_DO_BACKEND.includes(c)).join(',')) corpo.canais_permitidos = canais
  return corpo
}

/* ------------------------------------------------------------------ */
/* Saúde                                                                */
/* ------------------------------------------------------------------ */

/**
 * `GET /health`: hoje o backend informa só o relógio. Modelos, redator e base continuam com
 * o valor de demonstração, e a tela marca essas linhas (pendência para a Etapa 3).
 */
export function adaptarSaude(h: SaudeApi, demonstracao: SaudeSistema): SaudeSistema {
  return {
    relogio: { ativo: h.relogio.ligado && h.relogio.ultima_passagem_ok !== false, ultima_passagem: h.relogio.ultima_passagem_em },
    modelos: { ...demonstracao.modelos },
    redator: { ...demonstracao.redator },
    base: demonstracao.base ? { ...demonstracao.base } : null,
    demonstracao: ['modelos', 'redator', 'base'],
  }
}

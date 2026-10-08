/**
 * Adaptadores: traduzem a resposta do backend (Etapa 2) para os tipos que as telas já usam.
 *
 * São funções puras, sem rede e sem estado, e têm teste (`adaptadores.test.ts`) com as respostas
 * de exemplo dos relatórios do backend. Regras que valem aqui:
 *
 * - a `fee` (taxa da CRAI) nunca aparece: o backend não manda, e nenhum tipo da tela a tem;
 * - nenhum contato do cliente final (telefone, e-mail, CPF) passa: o backend não manda, e o
 *   adaptador só copia campos conhecidos, nunca o objeto inteiro;
 * - todo texto que vai para a tela começa com letra maiúscula;
 * - as frases de vocabulário fixo que o backend manda prontas passam por `doBackend()`, que as
 *   põe no idioma do painel (em português, passam como estão). Não passam: o `efeito` das
 *   contribuições (a tela traduz), o texto das mensagens aos clientes finais, o registro de
 *   decisões do Art. 20 e o texto para a política de privacidade.
 */
import type {
  Abordagem,
  Atividade,
  BaseClientes,
  Canal,
  CausaFalha,
  ChaveApi,
  ChavesDaEmpresa,
  CicloDetalhe,
  CicloResumo,
  ClienteFicticio,
  ClienteRisco,
  ComparacaoReguaModelo,
  Configuracao,
  ContribuicaoTexto,
  EstadoCiclo,
  EstadoSimulacao,
  EtapaFunil,
  EtapaSimulacao,
  EventoLinhaDoTempo,
  ExplicacaoDecisao,
  FaixaRisco,
  FaseSimulacao,
  Funil,
  ItemDesempenho,
  LinhaExtrato,
  MetricasMes,
  OfertaRetencao,
  OQueFunciona,
  PontoSerie,
  PontoSerieDupla,
  RespostaAssistente,
  ResultadoBusca,
  ResultadoImportacao,
  ResultadoRetencaoSimulada,
  ResumoVisaoGeral,
  ResumoVoluntario,
  SaudeSistema,
  StatusTela,
  Sugestao,
  SugestaoDoCiclo,
  TentativaSimulada,
  TipoAtividade,
} from './tipos'
import { t } from '../lib/idioma'
import { doBackend } from '../lib/doBackend'
import { fmt } from '../lib/format'

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
  /** Ciclo da simulação do gateway (Rodada 3). Ausente num backend anterior. */
  simulado?: boolean
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
    /**
     * O desconto por comportamento fora do padrão que o sistema aplicou sobre a pontuação do
     * diagnóstico (Rodada 2). Ausente num backend anterior; null quando não houve desconto.
     */
    desconto_por_anomalia?: { percentual: number; pontuacao_antes: number | null; pontuacao_usada: number | null } | null
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
  /** Rodada 4: o que o sistema faz em seguida. Ausente num backend anterior; null se nada está agendado. */
  proxima_acao?: { quando: string; tipo: string; descricao: string; ciclo_id: number | null } | null
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
  /** Rodada 3, S5: o intervalo mínimo entre ofertas de retenção. Ausente num backend anterior. */
  intervalo_minimo_ofertas_dias?: number
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
  /** Rodada 3: ausentes num backend anterior. `base` só vem com o token de login. */
  modelos?: { carregados: number; total: number; ausentes?: string[] }
  redator?: { disponivel: boolean }
  base?: { informada: boolean; atualizada_em: string | null; origem: string | null }
}

/* ------------------------------------------------------------------ */
/* Pequenas traduções                                                   */
/* ------------------------------------------------------------------ */

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
  lembrete_cordial: t('Lembrete cordial'),
  facilitacao: t('Facilitação'),
  urgencia_com_respeito: t('Urgência com respeito'),
}

const CANAL_LEGIVEL: Record<Canal, string> = {
  whatsapp: 'WhatsApp',
  email: t('E-mail'),
  sms: 'SMS',
  sem_canal: t('Sem canal disponível'),
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
      return t('Contato cadastrado na sua base')
    case 'sem_contato':
      return t('O cliente não tem telefone nem e-mail na sua base')
    case 'sem_mapeamento':
      return t('O cliente desta cobrança não está na sua base')
    case 'presumido_sem_contato':
    case 'presumido_sem_mapeamento':
      return t('Canal presumido: o cliente não tem contato na base')
    case 'cliente_pediu_para_nao_ser_contatado':
      // Rodada 3, Fase 6: o descadastro. Nenhuma mensagem sai para este cliente.
      return t('O cliente pediu para não ser contatado')
    default:
      return t('Canal escolhido pelo sistema')
  }
}

const MOTIVO_DESCARTE: Record<string, string> = {
  eprofit_nao_positivo: t('Retorno esperado abaixo do custo da ação'),
  score_abaixo_do_corte: t('Chance de recuperação baixa demais para agir'),
  autorizacao_revogada: t('O cliente revogou a autorização do Pix Automático'),
  janela_encerrada: t('A janela de novas tentativas acabou'),
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
    causa_legivel: doBackend(c.causa_legivel),
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
    // Ciclo da simulação do gateway: só vem quando a tela pede `incluir_simulados`.
    simulado: c.simulado === true,
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
  return itens.map((c) => ({ fator: doBackend(maiuscula(c.fator)), efeito: c.efeito ? maiuscula(c.efeito) : null }))
}

const texto = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null)
const numero = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)


const DECISAO: Record<string, string> = {
  risco: t('Decisão registrada: avaliação da cobrança'),
  retentativa: t('Decisão registrada: nova tentativa'),
  oferta: t('Decisão registrada: mensagem'),
  canal: t('Decisão registrada: canal'),
}

const QUEM_ESCOLHEU: Record<string, string> = {
  owner: t('Escolhida pelo dono'),
  admin: t('Escolhida por um administrador'),
  prazo: t('O prazo de escolha acabou: saiu a recomendada'),
  automatico: t('Modo automático: saiu a recomendada'),
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
      return { em: e.quando, tipo: 'abertura', titulo: t('Cobrança falhou'), detalhe: doBackend(texto(d.causa_legivel)) ?? undefined, tom: 'danger' }
    case 'diagnostico': {
      const p = numero(d.p_recovery)
      return {
        em: e.quando,
        tipo: 'diagnostico',
        titulo: p !== null ? t('Diagnóstico: {pct}% de chance de recuperar', { pct: Math.round(p * 100) }) : t('Diagnóstico concluído'),
      }
    }
    case 'decisao':
      return {
        em: e.quando,
        tipo: 'diagnostico',
        titulo: DECISAO[String(d.tipo_decisao)] ?? t('Decisão registrada'),
        detalhe: texto(d.explicacao) ?? undefined,
      }
    case 'tentativa_agendada':
      return { em: e.quando, tipo: 'tentativa', titulo: n === null ? t('Tentativa agendada') : t('Tentativa {n} agendada', { n }) }
    case 'tentativa_disparada':
      return { em: e.quando, tipo: 'tentativa', titulo: n === null ? t('Tentativa enviada ao banco') : t('Tentativa {n} enviada ao banco', { n }) }
    case 'tentativa_resultado': {
      const r = texto(d.resultado)
      if (r === 'paga') return { em: e.quando, tipo: 'tentativa', titulo: n === null ? t('Tentativa paga') : t('Tentativa {n} paga', { n }), tom: 'ok' }
      if (r === 'falhou') return { em: e.quando, tipo: 'tentativa', titulo: n === null ? t('Tentativa falhou') : t('Tentativa {n} falhou', { n }), tom: 'danger' }
      if (r === 'sem_retorno') return { em: e.quando, tipo: 'tentativa', titulo: n === null ? t('Tentativa sem resposta do banco') : t('Tentativa {n} sem resposta do banco', { n }), tom: 'warn' }
      return { em: e.quando, tipo: 'tentativa', titulo: n === null ? t('Tentativa: resultado registrado') : t('Tentativa {n}: resultado registrado', { n }) }
    }
    case 'tentativa_cancelada': {
      const motivo = texto(d.motivo)
      return {
        em: e.quando,
        tipo: 'tentativa',
        titulo: n === null ? t('Tentativa cancelada') : t('Tentativa {n} cancelada', { n }),
        detalhe: motivo === 'recuperado' ? t('O pagamento entrou antes') : (motivoLegivel(motivo) ?? undefined),
      }
    }
    case 'sugestoes_geradas': {
      const rodada = numero(d.rodada) ?? 1
      const recomendada = texto(d.recomendada)
      const canal = CANAL_LEGIVEL[canalDaTela(d.canal)]
      return {
        em: e.quando,
        tipo: 'sugestoes',
        titulo: rodada > 1 ? t('Outras 3 mensagens sugeridas ({rodada}ª rodada)', { rodada }) : t('3 mensagens sugeridas'),
        detalhe: recomendada
          ? t('Recomendada: {abordagem}. Canal: {canal}.', { abordagem: ABORDAGEM_LEGIVEL[abordagemDaTela(recomendada)], canal })
          : t('Canal: {canal}.', { canal }),
      }
    }
    case 'mensagem_escolhida': {
      const abordagem = texto(d.abordagem)
      const quem = QUEM_ESCOLHEU[String(d.escolhida_por)] ?? t('Mensagem escolhida')
      return {
        em: e.quando,
        tipo: 'escolha',
        titulo: abordagem ? t('Mensagem escolhida: {abordagem}', { abordagem: ABORDAGEM_LEGIVEL[abordagemDaTela(abordagem)] }) : t('Mensagem escolhida'),
        detalhe: `${quem}.`,
      }
    }
    case 'mensagem_nao_entregavel':
      return { em: e.quando, tipo: 'aviso', titulo: t('Sem canal disponível'), detalhe: t('{motivo}. A mensagem não foi entregue.', { motivo: motivoDoCanal(String(d.motivo_canal)) }), tom: 'warn' }
    case 'mensagem_reservada':
      return null // passo interno do envio; a tela mostra só a mensagem enviada
    case 'mensagem_enviada':
      return { em: e.quando, tipo: 'mensagem', titulo: t('Mensagem enviada'), tom: 'ok' }
    case 'recuperado': {
      const liquido = numero(d.valor_liquido)
      return { em: e.quando, tipo: 'desfecho', titulo: t('Pagamento recuperado'), detalhe: liquido !== null ? t('{valor} líquidos para você.', { valor: fmt.brl(liquido) }) : undefined, tom: 'ok' }
    }
    case 'perdido':
      return {
        em: e.quando,
        tipo: 'desfecho',
        titulo: t('Encerrado sem recuperação'),
        detalhe: texto(d.motivo_perdido) === 'sem_canal' ? t('Não havia canal para entregar a mensagem em 30 dias.') : t('O pagamento não veio no prazo depois da mensagem.'),
      }
    case 'descartado':
      return { em: e.quando, tipo: 'desfecho', titulo: t('Encerrado sem ação'), detalhe: motivoLegivel(texto(d.motivo_descarte)) ?? undefined }
    case 'estorno': {
      const valor = numero(d.valor_devolvido)
      const noPrazo = d.no_prazo === true
      return {
        em: e.quando,
        tipo: 'aviso',
        titulo: noPrazo ? t('Pagamento devolvido dentro do prazo') : t('Pagamento devolvido depois do prazo'),
        detalhe:
          (valor !== null ? t('{valor} devolvidos ao cliente.', { valor: fmt.brl(valor) }) + ' ' : '') +
          (noPrazo
            ? d.total === true
              ? t('Todo o valor voltou: esta cobrança deixou de contar como recuperada.')
              : t('O valor saiu do que foi recuperado para você.')
            : t('O prazo de estorno já tinha acabado: os valores não mudam.')),
        tom: noPrazo ? 'danger' : 'neutro',
      }
    }
    default:
      return { em: e.quando, tipo: 'aviso', titulo: t('Evento registrado') }
  }
}

/**
 * O que a linha do tempo diz, num ciclo com desconto, no lugar do texto da avaliação
 * inicial: aquele texto traz a pontuação de ANTES do desconto, e o painel mostra um número
 * só, o que o sistema usou.
 */
export function avisoDoDesconto(percentual: number): string {
  return t('Avaliação inicial da cobrança. O número do topo é o que o sistema usou e já tem o desconto de {percentual}% por comportamento fora do padrão.', { percentual })
}

/** `GET /ciclos/{id}`: o painel lateral. */
export function adaptarDetalhe(d: CicloDetalheApi): CicloDetalhe {
  const diagnostico = d.linha_do_tempo.find((e) => e.tipo === 'diagnostico')
  const desconto = numero(d.diagnostico?.desconto_por_anomalia?.percentual)
  const evento = (e: EventoApi): EventoLinhaDoTempo | null => {
    const adaptado = adaptarEvento(e)
    // Com desconto, a decisão de risco não repete na tela a pontuação de antes dele.
    if (adaptado && desconto !== null && e.tipo === 'decisao' && e.dados?.tipo_decisao === 'risco') {
      return { ...adaptado, detalhe: avisoDoDesconto(desconto) }
    }
    return adaptado
  }
  return {
    ...adaptarCiclo(d.ciclo),
    // O número que o sistema usou para decidir (com o desconto, quando houve).
    chance_recuperar: diagnostico ? numero(diagnostico.dados?.p_recovery) : null,
    desconto_anomalia_pct: desconto,
    dia_provavel_saldo: null,
    contribuicoes: adaptarContribuicoes(d.diagnostico?.contribuicoes ?? []),
    tentativas: [],
    sugestoes: [...d.mensagens].sort((a, b) => a.rodada - b.rodada).map(adaptarMensagem),
    modo_mensagem: d.modo_mensagem,
    escolha_ate: d.escolha_ate,
    escolha_por: d.escolhida_por,
    motivo_descarte: motivoLegivel(d.ciclo.motivo_descarte),
    linha_do_tempo: d.linha_do_tempo.map(evento).filter((e): e is EventoLinhaDoTempo => e !== null),
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
    // Rodada 4: a próxima ação vem do backend. Sem ela (nada agendado, ou backend anterior), null.
    proxima_acao:
      m.proxima_acao && typeof m.proxima_acao.quando === 'string' && typeof m.proxima_acao.descricao === 'string'
        ? { quando: m.proxima_acao.quando, descricao: doBackend(maiuscula(m.proxima_acao.descricao)), ciclo_id: numero(m.proxima_acao.ciclo_id) }
        : null,
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

/** O intervalo entre ofertas que o backend usa quando a empresa não escolheu outro (e os limites do campo). */
export const INTERVALO_PADRAO_ENTRE_OFERTAS = 30
export const INTERVALO_ENTRE_OFERTAS = { minimo: 1, maximo: 365 }

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
    intervalo_minimo_ofertas_dias: numero(c.intervalo_minimo_ofertas_dias) ?? INTERVALO_PADRAO_ENTRE_OFERTAS,
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
  if (nova.intervalo_minimo_ofertas_dias !== lida.intervalo_minimo_ofertas_dias) corpo.intervalo_minimo_ofertas_dias = nova.intervalo_minimo_ofertas_dias
  return corpo
}

/* ------------------------------------------------------------------ */
/* Saúde                                                                */
/* ------------------------------------------------------------------ */

/**
 * `GET /health`: o relógio, os modelos carregados, o redator e (com o token) a base da empresa.
 * O que um backend anterior à Rodada 3 não informa continua com o valor de demonstração, e a
 * tela marca essas linhas; com tudo informado, `demonstracao` não existe.
 */
export function adaptarSaude(h: SaudeApi, demonstracao: SaudeSistema): SaudeSistema {
  const faltando: NonNullable<SaudeSistema['demonstracao']> = []
  if (!h.modelos) faltando.push('modelos')
  if (!h.redator) faltando.push('redator')
  if (!h.base?.informada) faltando.push('base')
  const origem = h.base?.origem
  return {
    relogio: { ativo: h.relogio.ligado && h.relogio.ultima_passagem_ok !== false, ultima_passagem: h.relogio.ultima_passagem_em },
    modelos: h.modelos ? { carregados: h.modelos.carregados, total: h.modelos.total } : { ...demonstracao.modelos },
    redator: h.redator ? { disponivel: h.redator.disponivel } : { ...demonstracao.redator },
    base: h.base?.informada
      ? h.base.atualizada_em
        ? { origem: origem === 'api' || origem === 'anexo' ? origem : null, atualizada_em: h.base.atualizada_em }
        : null
      : demonstracao.base
        ? { ...demonstracao.base }
        : null,
    ...(faltando.length ? { demonstracao: faltando } : {}),
  }
}

/* ------------------------------------------------------------------ */
/* Visão geral (Rodada 3, Fase 2)                                       */
/* ------------------------------------------------------------------ */

/** `GET /metrics/visao-geral`. Tudo líquido: a fee não vem. Voluntário `null` fora do premium. */
export interface VisaoGeralApi {
  dias: number
  periodo: { de: string; ate: string }
  recuperado_involuntario: number
  cobrancas_recuperadas: number
  retido_voluntario: number | null
  clientes_mantidos: number | null
  ciclos_ativos: number
  aguardando_escolha: number
  clientes_risco_grave: number | null
  risco_grave_com_oferta: number | null
  taxa_recuperacao: number | null
  ciclos_com_desfecho: number
  mantido: number
  /** Rodada 4: a empresa está em período de piloto. Ausente num backend anterior. */
  piloto?: boolean
}

export function adaptarVisaoGeral(r: VisaoGeralApi): ResumoVisaoGeral {
  return {
    periodo: { de: r.periodo.de, ate: r.periodo.ate },
    recuperado_involuntario: r.recuperado_involuntario,
    cobrancas_recuperadas: r.cobrancas_recuperadas,
    retido_voluntario: r.retido_voluntario,
    clientes_mantidos: r.clientes_mantidos,
    ciclos_ativos: r.ciclos_ativos,
    aguardando_escolha: r.aguardando_escolha,
    clientes_risco_grave: r.clientes_risco_grave,
    risco_grave_com_oferta: r.risco_grave_com_oferta,
    taxa_recuperacao: r.taxa_recuperacao,
    ciclos_com_desfecho: r.ciclos_com_desfecho,
    piloto: r.piloto === true,
  }
}

/** `GET /metrics/serie`: `voluntario` é null em todo ponto fora do plano premium. */
export interface SerieDuplaApi {
  dias: number
  pontos: { dia: string; involuntario: number; voluntario: number | null }[]
}

export function adaptarSerieDupla(r: SerieDuplaApi): PontoSerieDupla[] {
  return r.pontos.map((p) => ({ dia: p.dia, involuntario: p.involuntario, voluntario: p.voluntario ?? 0 }))
}

/** `GET /metrics/involuntario/funil` */
export interface FunilApi {
  mes: string
  etapas: { etapa: string; rotulo: string; chegaram: number; valor: number; recuperados_aqui: number; valor_recuperado_aqui: number }[]
  desfecho: { recuperados: number; encerrados: number; em_andamento: number }
}

const ETAPAS_DO_FUNIL: EtapaFunil['etapa'][] = ['falhas', 'tentativa_1', 'tentativa_2', 'tentativa_3', 'mensagem']

export function adaptarFunil(r: FunilApi): Funil {
  return {
    mes: r.mes,
    // Só as etapas que a tela conhece, na ordem dela.
    etapas: ETAPAS_DO_FUNIL.flatMap((etapa) => {
      const e = r.etapas.find((x) => x.etapa === etapa)
      return e ? [{ etapa, rotulo: doBackend(e.rotulo), chegaram: e.chegaram, valor: e.valor, recuperados_aqui: e.recuperados_aqui, valor_recuperado_aqui: e.valor_recuperado_aqui }] : []
    }),
    desfecho: { recuperados: r.desfecho.recuperados, encerrados: r.desfecho.encerrados, em_andamento: r.desfecho.em_andamento },
  }
}

/** `GET /metrics/o-que-funciona`: `taxa` aqui é a proporção de sucesso (0..1), não a fee. */
export interface OQueFuncionaApi {
  causas: { rotulo: string; casos: number; sucessos: number; taxa: number | null; valor_liquido?: number }[]
  ofertas: { rotulo: string; casos: number; sucessos: number; taxa: number | null }[]
  canais: { rotulo: string; casos: number; sucessos: number; taxa: number | null }[]
}

export function adaptarOQueFunciona(r: OQueFuncionaApi): OQueFunciona {
  const item = (i: { rotulo: string; casos: number; taxa: number | null; valor_liquido?: number }): ItemDesempenho => ({
    rotulo: doBackend(maiuscula(i.rotulo)),
    taxa: i.taxa ?? 0,
    casos: i.casos,
    ...(typeof i.valor_liquido === 'number' ? { valor: i.valor_liquido } : {}),
  })
  return { causas: r.causas.map(item), ofertas: r.ofertas.map(item), canais: r.canais.map(item) }
}

/** `GET /atividade`: a frase já vem pronta; nenhum contato e nenhum texto de mensagem. */
export interface AtividadeApi {
  atividades: { id: string; em: string; tipo: string; texto: string; valor: number | null; simulado: boolean }[]
}

const TIPOS_DE_ATIVIDADE: TipoAtividade[] = ['recuperado', 'tentativa_falhou', 'oferta_aceita', 'mensagem_enviada', 'risco_grave', 'estorno', 'escolha']

export function adaptarAtividade(r: AtividadeApi): Atividade[] {
  // Evento de um tipo que esta versão da tela não conhece não é mostrado com o ícone de outro.
  return r.atividades
    .filter((a) => TIPOS_DE_ATIVIDADE.includes(a.tipo as TipoAtividade))
    .map((a) => ({ id: a.id, em: a.em, tipo: a.tipo as TipoAtividade, texto: doBackend(maiuscula(a.texto)), valor: a.valor, simulado: Boolean(a.simulado) }))
}

/** `GET /extrato`: a ÚNICA resposta que traz a fee (é a memória de cálculo da fatura). */
export interface ExtratoApi {
  mes: string
  linhas: {
    id: string
    data: string
    cliente: string | null
    id_cliente: string
    origem: string
    tipo: string
    descricao: string
    valor_base: number
    fee: number
    /** Rodada 4: só na linha de piloto; `null` nas outras, e ausente num backend anterior. */
    fee_fora_do_piloto?: number | null
    liquido: number
    estornado: boolean
    simulado: boolean
  }[]
}

export function adaptarExtrato(r: ExtratoApi): LinhaExtrato[] {
  return r.linhas.map((l) => ({
    id: l.id,
    data: l.data,
    cliente: l.cliente?.trim() || l.id_cliente,
    origem: l.origem === 'voluntario' ? 'voluntario' : 'involuntario',
    tipo: l.tipo === 'estorno' ? 'estorno' : l.tipo === 'mantido' ? 'mantido' : 'recuperacao',
    descricao: doBackend(maiuscula(l.descricao)),
    valor_base: l.valor_base,
    taxa: l.fee,
    taxa_fora_do_piloto: typeof l.fee_fora_do_piloto === 'number' ? l.fee_fora_do_piloto : null,
    liquido: l.liquido,
    estornado: Boolean(l.estornado),
    simulado: Boolean(l.simulado),
  }))
}

/* ------------------------------------------------------------------ */
/* Chaves de API (Rodada 2)                                             */
/* ------------------------------------------------------------------ */

/** Uma chave como o backend a devolve em `/integracao/chaves`. Sem o hash e sem a chave inteira. */
export interface ChaveApiBackend {
  id: string
  nome: string
  prefixo: string
  final: string
  criada_em: string
  criada_por_papel: string
  ultimo_uso_em: string | null
  revogada_em: string | null
  situacao: 'ativa' | 'revogada'
  usos_hoje: number
}

/** `GET /integracao/chaves` */
export interface ListaDeChavesApi {
  chaves: ChaveApiBackend[]
  ativas: number
  limite_ativas: number
  pode_revogar: boolean
  plano_permite_gerar: boolean
  pode_gerar: boolean
}

/** `POST /integracao/chaves`: a única resposta que traz a chave inteira. */
export interface ChaveCriadaApi {
  chave: ChaveApiBackend
  chave_inteira: string
}

/** `DELETE /integracao/chaves/{id}` */
export interface ChaveRevogadaApi {
  chave: ChaveApiBackend
  ja_estava_revogada: boolean
}

/** Só os campos conhecidos passam: se o backend um dia mandar algo a mais, não chega à tela. */
export function adaptarChave(c: ChaveApiBackend): ChaveApi {
  return {
    id: c.id,
    nome: c.nome,
    inicio: c.prefixo,
    final: c.final,
    criada_em: c.criada_em,
    ultimo_uso: c.ultimo_uso_em,
    revogada_em: c.revogada_em,
  }
}

export function adaptarChaves(r: ListaDeChavesApi): ChavesDaEmpresa {
  return {
    chaves: r.chaves.map(adaptarChave),
    ativas: r.ativas,
    limite_ativas: r.limite_ativas,
    pode_revogar: r.pode_revogar,
    plano_permite_gerar: r.plano_permite_gerar,
    pode_gerar: r.pode_gerar,
  }
}

/* ------------------------------------------------------------------ */
/* Voluntário (Rodada 3, Fase 1)                                        */
/* ------------------------------------------------------------------ */

/** Uma linha de `GET /clientes/recentes`. Não traz e-mail, telefone, CPF nem chave Pix. */
export interface ClienteRecenteApi {
  id: string
  nome: string | null
  mrr: number | null
  faixa: string
  motivo: string | null
  decidido_por: 'modelo' | 'regua' | null
  posicao_no_ranking: number | null
  abordagem: {
    oferta: string
    oferta_legivel: string | null
    canal: string | null
    canal_legivel: string | null
    situacao: string
    simulado?: boolean
  } | null
  atualizado_em: string | null
  /** Rodada 4. Ausente num backend anterior. */
  nao_contatar?: boolean
  simulado: boolean
}

export interface ClientesRecentesApi {
  clientes: ClienteRecenteApi[]
  total_na_base: number
}

const FAIXAS: FaixaRisco[] = ['grave', 'preocupante', 'sem_risco', 'sem_dado']
const SITUACOES = ['aguardando', 'enviada', 'aceita', 'recusada'] as const

/** Só os campos conhecidos passam, um a um. Faixa que a tela não conhece vira "sem dado". */
export function adaptarClienteRecente(c: ClienteRecenteApi): ClienteRisco {
  const situacao = SITUACOES.find((s) => s === c.abordagem?.situacao)
  return {
    id: c.id,
    nome: c.nome?.trim() || c.id,
    mrr: typeof c.mrr === 'number' ? c.mrr : null,
    faixa: FAIXAS.includes(c.faixa as FaixaRisco) ? (c.faixa as FaixaRisco) : 'sem_dado',
    motivo: doBackend(maiuscula(c.motivo ?? '')),
    risco_decidido_por: c.decidido_por === 'modelo' || c.decidido_por === 'regua' ? c.decidido_por : null,
    posicao_na_base: c.posicao_no_ranking,
    abordagem:
      c.abordagem && situacao
        ? {
            oferta: doBackend(maiuscula(c.abordagem.oferta_legivel ?? c.abordagem.oferta)),
            canal: doBackend(maiuscula(c.abordagem.canal_legivel ?? c.abordagem.canal ?? t('Sem canal disponível'))),
            status: situacao,
          }
        : null,
    atualizado_em: c.atualizado_em,
    nao_contatar: c.nao_contatar === true,
    simulado: Boolean(c.simulado),
  }
}

/* ------------------------------------------------------------------ */
/* Busca do topo (Rodada 4)                                             */
/* ------------------------------------------------------------------ */

/** `GET /busca?q=`. Nenhum contato na resposta: do cliente, só o id, o nome e a mensalidade. */
export interface BuscaApi {
  q: string
  clientes: { id: string; nome: string | null; mrr: number | null; cancelado: boolean; nao_contatar: boolean }[]
  ciclos: { id: number; id_recorrencia: string; cliente_nome: string | null; status: StatusApi; estado: string; valor_cobranca: number; causa_legivel: string | null; atualizado_em: string }[]
  limite: number
}

/** Só os campos conhecidos passam, um a um. */
export function adaptarBusca(r: BuscaApi): ResultadoBusca {
  return {
    clientes: (Array.isArray(r.clientes) ? r.clientes : []).map((c) => ({
      id: c.id,
      nome: c.nome?.trim() || c.id,
      mrr: typeof c.mrr === 'number' ? c.mrr : null,
      cancelado: c.cancelado === true,
      nao_contatar: c.nao_contatar === true,
    })),
    ciclos: (Array.isArray(r.ciclos) ? r.ciclos : []).map((c) => ({
      id: c.id,
      cliente: c.cliente_nome?.trim() || null,
      id_recorrencia: c.id_recorrencia,
      status: statusDaTela(c.status),
      valor_cobranca: c.valor_cobranca,
      causa_legivel: doBackend(c.causa_legivel ?? null),
    })),
  }
}

/** `GET /clientes/base`: `base` é null quando a empresa ainda não tem cliente nenhum. */
export interface BaseApi {
  base: {
    total: number
    com_dados_comportamento: number
    decididos_pelo_modelo: number
    modelo_ativo: boolean
    atualizada_em: string
    origem: string | null
  } | null
}

export function adaptarBase(r: BaseApi): BaseClientes | null {
  if (!r.base) return null
  return {
    total: r.base.total,
    com_dados_comportamento: r.base.com_dados_comportamento,
    decididos_pelo_modelo: r.base.decididos_pelo_modelo,
    atualizada_em: r.base.atualizada_em,
    origem: r.base.origem === 'api' || r.base.origem === 'anexo' ? r.base.origem : null,
  }
}

/** `GET /metrics/voluntario/mes`. O valor já vem líquido: a fee não vem. */
export interface MesVoluntarioApi {
  mes: string
  valor_liquido_mantido: number
  clientes_mantidos: number
  estornos: { quantidade: number; valor_liquido_estornado: number }
  aceites_sem_valor: number
  grave: number
  preocupante: number
  ofertas_enviadas: number
  ofertas_aceitas: number
  meses_de_mrr: number
  prazo_estorno_dias: number
}

export function adaptarResumoVoluntario(r: MesVoluntarioApi): ResumoVoluntario {
  return {
    mes: r.mes,
    valor_liquido_mantido: r.valor_liquido_mantido,
    clientes_mantidos: r.clientes_mantidos,
    estornos: r.estornos.quantidade,
    grave: r.grave,
    preocupante: r.preocupante,
    ofertas_enviadas: r.ofertas_enviadas,
    ofertas_aceitas: r.ofertas_aceitas,
    meses_de_mrr: r.meses_de_mrr,
    prazo_estorno_dias: r.prazo_estorno_dias,
  }
}

/** `GET /metrics/voluntario/serie` */
export interface SerieVoluntarioApi {
  dias: number
  pontos: { dia: string; valor_liquido_mantido: number; clientes_mantidos: number; valor_liquido_estornado: number }[]
}

export function adaptarSerieVoluntario(r: SerieVoluntarioApi): PontoSerie[] {
  return r.pontos.map((p) => ({ dia: p.dia, valor: p.valor_liquido_mantido }))
}

/** `GET /metrics/voluntario/regua-x-modelo`: `comparacao` é null sem desfecho suficiente. */
export interface ReguaXModeloApi {
  dias: number
  comparacao: {
    clientes_com_dados: number
    cancelamentos: number
    regua: { marcou_grave: number; avisou_antes: number }
    modelo: { marcou_grave: number; avisou_antes: number }
  } | null
  motivo_vazio: string | null
}

export function adaptarComparacao(r: ReguaXModeloApi): ComparacaoReguaModelo | null {
  if (!r.comparacao) return null
  return {
    dias: r.dias,
    clientes_com_dados: r.comparacao.clientes_com_dados,
    cancelamentos: r.comparacao.cancelamentos,
    regua: { marcou_grave: r.comparacao.regua.marcou_grave, avisou_antes: r.comparacao.regua.avisou_antes },
    modelo: { marcou_grave: r.comparacao.modelo.marcou_grave, avisou_antes: r.comparacao.modelo.avisou_antes },
  }
}

/** `POST /clientes/importar` */
export interface ImportacaoApi {
  importados: number
  rejeitados: { linha: number; motivo: string }[]
  colunas_nao_encontradas: string[]
  linhas_sem_dado_comportamental: number
  mensagem?: string
}

/** Quantos motivos de recusa a tela lista (o resto vira "e mais N"). */
const AVISOS_DA_IMPORTACAO = 5

export function adaptarImportacao(arquivo: string, r: ImportacaoApi): ResultadoImportacao {
  const avisos = r.rejeitados.slice(0, AVISOS_DA_IMPORTACAO).map((x) => t('Linha {linha}: {motivo}', { linha: x.linha, motivo: x.motivo }))
  if (r.rejeitados.length > AVISOS_DA_IMPORTACAO) avisos.push(t('E mais {n} linhas recusadas.', { n: r.rejeitados.length - AVISOS_DA_IMPORTACAO }))
  if (r.mensagem) avisos.unshift(maiuscula(r.mensagem))
  return {
    arquivo,
    linhas: r.importados + r.rejeitados.length,
    importados: r.importados,
    rejeitados: r.rejeitados.length,
    novos: null,
    atualizados: null,
    sem_id_recorrencia: null,
    sem_comportamento: r.linhas_sem_dado_comportamental,
    avisos,
    demonstracao: false,
  }
}

/* ------------------------------------------------------------------ */
/* Simulação do gateway (Rodada 3, Fase 3)                              */
/* ------------------------------------------------------------------ */

export interface TentativaSimuladaApi {
  numero: number
  agendada_para: string
  disparada_em: string | null
  resultado: 'pendente' | 'paga' | 'falhou' | 'cancelada'
  resultado_em: string | null
  causa: string | null
  causa_legivel: string | null
  motivo_cancelamento: string | null
}

/** `GET /simulacao` e a resposta de toda ação da simulação. */
export interface SimulacaoApi {
  existe: boolean
  relogio: { agora: string; iniciado_em: string } | null
  cliente: ClienteFicticio | null
  id_recorrencia: string | null
  cobranca: { feita: boolean; em: string | null; resultado: 'paga' | 'falhou' | null; causa: string | null; causa_legivel: string | null } | null
  /** O ciclo simulado, na mesma forma de `GET /ciclos/{id}`. */
  ciclo: CicloDetalheApi | null
  tentativas: TentativaSimuladaApi[]
  proxima_acao: { quando: string; descricao: string; tipo: string } | null
  resposta_a_mensagem?: 'pagou' | 'nao_pagou' | null
  desfecho: { tipo: 'recuperado' | 'encerrado'; via: 'tentativa' | 'mensagem'; tentativa: number | null; valor_liquido: number; em: string | null } | null
  pensando: string[]
  sem_crai: { resultado: 'recuperado' | 'perdido'; explicacao: string } | null
  chance_recuperar?: number | null
  dia_provavel_saldo?: string | null
  modo_mensagem: 'automatico' | 'escolha'
  prazo_escolha_horas: number
}

const ETAPAS_DA_SIMULACAO: EtapaSimulacao[] = ['cobranca', 'tentativa_1', 'tentativa_2', 'tentativa_3', 'mensagem', 'desfecho']

/**
 * O estado da simulação como a página o desenha. Tudo vem do backend: o que o sistema
 * diagnosticou, as tentativas que ele agendou, as mensagens que ele escreveu. A tela só
 * deriva em que etapa do desenho o ciclo está.
 */
export function adaptarSimulacao(s: SimulacaoApi, agoraDeVerdade: string = new Date().toISOString()): EstadoSimulacao {
  const hoje = s.relogio?.agora ?? agoraDeVerdade
  const vazio: EstadoSimulacao = {
    id_ciclo: null,
    cliente: null,
    id_recorrencia: '',
    hoje,
    inicio: hoje,
    fase: 'formulario',
    etapas_concluidas: [],
    etapa_atual: 'cobranca',
    causa: null,
    chance_recuperar: null,
    dia_provavel_saldo: null,
    contribuicoes: [],
    tentativas: [],
    proxima_acao: null,
    sugestoes: [],
    mensagem_enviada: null,
    desfecho: null,
    pensando: [],
    sem_crai: null,
    linha_do_tempo: [],
    modo_mensagem: s.modo_mensagem,
    prazo_escolha_horas: s.prazo_escolha_horas,
  }
  // Cliente criado e ainda não cobrado (só acontece por fora da tela): a página volta ao formulário.
  if (!s.existe || !s.cliente || !s.cobranca?.feita) return vazio

  const detalhe = s.ciclo ? adaptarDetalhe(s.ciclo) : null
  const estadoDoCiclo = s.ciclo?.ciclo.estado ?? null
  const tentativas: TentativaSimulada[] = s.tentativas.map((t) => ({
    numero: t.numero,
    agendada_para: t.agendada_para,
    resultado: t.resultado === 'pendente' ? 'agendada' : t.resultado,
    causa: t.causa ? causaDaTela(t.causa) : null,
  }))
  const desfecho = s.desfecho ? { ...s.desfecho, em: s.desfecho.em ?? hoje } : null

  // As sugestões da ÚLTIMA rodada, com texto (a simulação acabou de escrevê-las).
  const ultimaRodada = Math.max(0, ...(s.ciclo?.mensagens ?? []).map((m) => m.rodada))
  const sugestoes: Sugestao[] = (s.ciclo?.mensagens ?? [])
    .filter((m) => m.rodada === ultimaRodada)
    .map((m) => ({ ...adaptarMensagem(m), texto: m.texto ?? '' }))
  const enviada = (s.ciclo?.mensagens ?? []).find((m) => m.enviada_em)
  const escolhidaPor = s.ciclo?.escolhida_por ?? null

  const fase: FaseSimulacao = desfecho
    ? desfecho.tipo === 'recuperado'
      ? 'recuperada'
      : 'encerrada'
    : estadoDoCiclo === 'aguardando_escolha'
      ? 'mensagens'
      : estadoDoCiclo === 'mensagem_enviada'
        ? 'mensagem_enviada'
        : 'recusada'

  const concluidas: EtapaSimulacao[] = ['cobranca']
  for (const t of tentativas) if (t.resultado === 'paga' || t.resultado === 'falhou') concluidas.push(`tentativa_${t.numero}` as EtapaSimulacao)
  if (enviada) concluidas.push('mensagem')
  if (desfecho) concluidas.push('desfecho')
  const proximaTentativa = tentativas.find((t) => t.resultado === 'agendada')
  const etapaAtual: EtapaSimulacao = desfecho
    ? 'desfecho'
    : estadoDoCiclo === 'aguardando_escolha' || (estadoDoCiclo === 'recobrando' && !proximaTentativa)
      ? 'mensagem'
      : estadoDoCiclo === 'mensagem_enviada'
        ? 'desfecho'
        : proximaTentativa
          ? (`tentativa_${proximaTentativa.numero}` as EtapaSimulacao)
          : 'desfecho'

  // A causa que a faixa do cartão mostra: a da última cobrança que falhou.
  const ultimaFalha = [...tentativas].reverse().find((t) => t.resultado === 'falhou')
  const causa = ultimaFalha?.causa ?? (s.cobranca.causa ? causaDaTela(s.cobranca.causa) : null)

  return {
    ...vazio,
    id_ciclo: s.ciclo?.ciclo.id ?? null,
    cliente: s.cliente,
    id_recorrencia: s.id_recorrencia ?? '',
    inicio: s.cobranca.em ?? s.relogio?.iniciado_em ?? hoje,
    fase,
    etapas_concluidas: ETAPAS_DA_SIMULACAO.filter((e) => concluidas.includes(e)),
    etapa_atual: etapaAtual,
    causa,
    chance_recuperar: typeof s.chance_recuperar === 'number' ? s.chance_recuperar : (detalhe?.chance_recuperar ?? null),
    dia_provavel_saldo: s.dia_provavel_saldo ?? null,
    contribuicoes: detalhe?.contribuicoes ?? [],
    tentativas,
    proxima_acao: s.proxima_acao ? { quando: s.proxima_acao.quando, descricao: doBackend(maiuscula(s.proxima_acao.descricao)) } : null,
    sugestoes,
    mensagem_enviada:
      enviada && enviada.enviada_em
        ? { abordagem: abordagemDaTela(enviada.abordagem), canal: canalDaTela(enviada.canal), em: enviada.enviada_em, escolhida_por: escolhidaPor ?? 'automatico' }
        : null,
    desfecho,
    pensando: s.pensando.map((linha) => doBackend(maiuscula(linha))),
    sem_crai: s.sem_crai ? { resultado: s.sem_crai.resultado, explicacao: doBackend(s.sem_crai.explicacao) } : null,
    linha_do_tempo: detalhe?.linha_do_tempo ?? [],
  }
}

/** As quatro ofertas que o sistema faz de verdade, na ordem do formulário. */
export const OFERTAS_DE_RETENCAO: OfertaRetencao[] = ['desconto_10', 'desconto_20', 'pausa_1_mes', 'pix_boleto_flash']

/** `POST /simulacao/retencao`. */
export interface RetencaoSimuladaApi {
  faixa: string
  motivo: string
  decidido_por: 'modelo' | 'regua'
  risco?: number | null
  /** null quando o modelo de IA decidiu o risco: o corte fixo não vale nesse caso. */
  corte_de_intervencao?: number | null
  /** Só quando o modelo de IA decidiu: a regra de intervenção, a intensidade e o porquê de não haver oferta. */
  regra_de_intervencao?: string | null
  intensidade?: string | null
  sem_oferta_porque?: string | null
  oferta: string | null
  oferta_legivel: string | null
  canal: string | null
  canal_legivel: string | null
  porque: string | null
  aceitou: boolean | null
  valor_mantido_liquido: number
  sem_crai: string
  meses_de_mrr: number
  prazo_estorno_dias: number
  simulado: boolean
}

export function adaptarRetencaoSimulada(r: RetencaoSimuladaApi): ResultadoRetencaoSimulada {
  const faixa = r.faixa === 'grave' || r.faixa === 'preocupante' ? r.faixa : 'sem_risco'
  const oferta = OFERTAS_DE_RETENCAO.find((o) => o === r.oferta) ?? null
  return {
    faixa,
    motivo: doBackend(maiuscula(r.motivo)),
    decidido_por: r.decidido_por,
    risco: numero(r.risco),
    corte_de_intervencao: numero(r.corte_de_intervencao) ?? 0.6,
    sem_oferta_porque: typeof r.sem_oferta_porque === 'string' && r.sem_oferta_porque.trim() ? doBackend(maiuscula(r.sem_oferta_porque)) : null,
    oferta,
    oferta_legivel: r.oferta_legivel ? doBackend(maiuscula(r.oferta_legivel)) : null,
    canal_legivel: doBackend(r.canal_legivel),
    porque: r.porque ? doBackend(maiuscula(r.porque)) : null,
    aceitou: r.aceitou,
    valor_mantido_liquido: r.valor_mantido_liquido,
    sem_crai: doBackend(maiuscula(r.sem_crai)),
    meses_de_mrr: r.meses_de_mrr,
    prazo_estorno_dias: r.prazo_estorno_dias,
  }
}

/* ------------------------------------------------------------------ */
/* Assistente (Rodada 3, Fase 4)                                        */
/* ------------------------------------------------------------------ */

/** `POST /assistente`. `origem: "ajuda"` é o texto fixo (sem o LLM, ou com ele fora do ar). */
export interface AssistenteApi {
  texto: string
  links: { rotulo: string; para: string }[]
  sugestoes: string[]
  origem: 'assistente' | 'ajuda'
}

/** Só caminho do próprio painel vira link: começa com uma barra, e não é endereço de fora. */
const caminhoDoPainel = (para: unknown): para is string => typeof para === 'string' && /^\/(?!\/)/.test(para) && !para.includes(':')

export function adaptarAssistente(r: AssistenteApi): RespostaAssistente {
  return {
    texto: doBackend(maiuscula(String(r.texto ?? ''))),
    links: (Array.isArray(r.links) ? r.links : [])
      .filter((l) => l && typeof l.rotulo === 'string' && caminhoDoPainel(l.para))
      .map((l) => ({ rotulo: doBackend(maiuscula(l.rotulo)), para: l.para })),
    sugestoes: (Array.isArray(r.sugestoes) ? r.sugestoes : []).filter((s): s is string => typeof s === 'string' && s.trim().length > 0).map((s) => doBackend(maiuscula(s))),
    // Tudo o que não é resposta do LLM é o texto fixo: a tela marca como "Resposta fixa".
    origem: r.origem === 'assistente' ? 'assistente' : 'texto_fixo',
  }
}

/* ------------------------------------------------------------------ */
/* Direitos do titular e descadastro (Rodada 3, Fase 6)                 */
/* ------------------------------------------------------------------ */

/** `POST /titular/exportar`: tudo o que a CRAI guarda de um cliente final. */
export interface ExportacaoApi {
  customer_id_externo: string
  exportado_em: string
  cobrancas: unknown[]
  retencao: unknown[]
  valores_mantidos: unknown[]
  decisoes_automatizadas: unknown[]
  [campo: string]: unknown
}

/** O que a tela mostra da exportação, e o arquivo que ela oferece para baixar. */
export interface ExportacaoTitular {
  arquivo: string
  linhas: number
  /** O JSON inteiro, como veio do backend. Ausente na demonstração (não há arquivo de verdade). */
  conteudo?: string
}

export function adaptarExportacao(r: ExportacaoApi): ExportacaoTitular {
  const conta = (v: unknown) => (Array.isArray(v) ? v.length : 0)
  // O nome do arquivo usa só o que é seguro num nome de arquivo.
  const id = String(r.customer_id_externo ?? '').replace(/[^A-Za-z0-9._-]/g, '_') || 'cliente'
  return {
    arquivo: `crai-titular-${id}.json`,
    linhas: conta(r.cobrancas) + conta(r.retencao) + conta(r.valores_mantidos) + conta(r.decisoes_automatizadas),
    conteudo: JSON.stringify(r, null, 2),
  }
}

/** `POST /titular/anonimizar`. */
export interface AnonimizacaoApi {
  contatos_apagados: string[]
  mensagens_apagadas: number
  ciclos_com_mensagem_apagada: number
  nao_contatar: { marcado_em: string; origem: string } | null
}

export interface AnonimizacaoTitular {
  ok: true
  ciclos_anonimizados: number
  mensagens_apagadas: number
  /** Quantos contatos (nome, e-mail, telefone) foram apagados. Ausente na demonstração. */
  contatos_apagados?: number
}

export function adaptarAnonimizacao(r: AnonimizacaoApi): AnonimizacaoTitular {
  return {
    ok: true,
    ciclos_anonimizados: numero(r.ciclos_com_mensagem_apagada) ?? 0,
    mensagens_apagadas: numero(r.mensagens_apagadas) ?? 0,
    contatos_apagados: Array.isArray(r.contatos_apagados) ? r.contatos_apagados.length : 0,
  }
}

/** `POST` e `DELETE /clientes/{id}/nao-contatar`. */
export interface NaoContatarApi {
  customer_id_externo: string
  nao_contatar: { marcado_em: string; origem: string } | null
  ja_estava_marcado?: boolean
  estava_marcado?: boolean
}

export interface MarcaNaoContatar {
  /** true: nenhuma mensagem sai para este cliente. */
  marcado: boolean
  /** A marca já existia (ao marcar) ou existia (ao tirar). */
  ja_estava: boolean
  desde: string | null
}

export function adaptarNaoContatar(r: NaoContatarApi): MarcaNaoContatar {
  return { marcado: r.nao_contatar !== null, ja_estava: Boolean(r.ja_estava_marcado ?? r.estava_marcado), desde: r.nao_contatar?.marcado_em ?? null }
}

/** `GET /titular/texto-para-politica`. */
export interface TextoPoliticaApi {
  titulo: string
  texto: string
}

/** `GET /titular/explicacao/{id}`: as decisões da trilha, da mais recente à mais antiga. */
export interface ExplicacaoApi {
  sujeito_id: string
  decisoes: { decidido_em: string; explicacao: string }[]
}

/** A tela mostra a decisão mais recente, com a frase que o backend escreveu. Sem pontos inventados. */
export function adaptarExplicacao(r: ExplicacaoApi): ExplicacaoDecisao | null {
  const d = r.decisoes[0]
  if (!d) return null
  return { cliente: r.sujeito_id, decisao: maiuscula(d.explicacao), quando: d.decidido_em, fatores: [], revisao_humana: null }
}

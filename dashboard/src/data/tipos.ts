/**
 * Formas de dado que o backend devolve (rotas da Etapa 2).
 * A tela só conhece estes tipos; quem os preenche é `api.ts`.
 */

/** Estados do ciclo no backend. */
export type EstadoCiclo =
  | 'recobrando'
  | 'aguardando_escolha'
  | 'mensagem_enviada'
  | 'recuperado'
  | 'perdido'
  | 'descartado'

/** Os quatro status da tela (R10). */
export type StatusTela = 'em_analise' | 'em_processo' | 'recuperado' | 'encerrado'

export type CausaFalha =
  | 'insufficient_funds'
  | 'limit_exceeded'
  | 'authorization_revoked'
  | 'processing_error'
  | 'generic_decline'

export type ResultadoTentativa = 'pendente' | 'paga' | 'falhou' | 'sem_retorno' | 'cancelada'

export type Abordagem = 'lembrete_cordial' | 'facilitacao' | 'urgencia_com_respeito'

export type Canal = 'whatsapp' | 'email' | 'sms' | 'sem_canal'

export interface CicloResumo {
  id: number
  cliente: string | null // nome da base, quando mapeado
  id_recorrencia: string
  valor_cobranca: number // bruto: o valor da cobrança que falhou (é o que a tabela mostra)
  valor_liquido: number | null // só quando recuperado: já descontada a taxa da CRAI (é o que soma no cartão)
  causa: CausaFalha
  /** O motivo da falha já em português, quando o backend manda (vale mais que o mapa da tela). */
  causa_legivel?: string | null
  estado: EstadoCiclo
  status: StatusTela
  tentativas_executadas: number
  tentativas_total: number
  proxima_acao: string | null // ISO
  proxima_acao_descricao: string | null
  aberto_em: string
  atualizado_em: string
  simulado: boolean
  /**
   * Estorno (Etapa 2, Bloco 5): o dinheiro recuperado voltou ao cliente dentro do prazo.
   * Com devolução total, o ciclo aparece como encerrado e `valor_liquido` é null; com
   * parcial, continua recuperado, com o líquido já reduzido. Null ou ausente: sem estorno.
   */
  estorno?: { valor_devolvido: number; total: boolean; parcial: boolean; ultimo_em: string | null } | null
}

export interface Tentativa {
  numero: number
  agendada_para: string
  disparada_em: string | null
  resultado: ResultadoTentativa
  resultado_em: string | null
  codigo_resultado: string | null
  motivo_cancelamento: string | null
}

export interface Contribuicao {
  fator: string // já em linguagem simples
  pontos: number // + ou -
}

export interface Sugestao {
  abordagem: Abordagem
  texto: string
  canal: Canal
  motivo_canal: string
  recomendada: boolean
  escolhida: boolean
}

/** Uma sugestão de um ciclo de verdade: tem rodada, e o texto some depois de 90 dias (retenção). */
export interface SugestaoDoCiclo extends Omit<Sugestao, 'texto'> {
  texto: string | null
  rodada: number
  /** Gerada, mas sem canal para entregar (o cliente não tem contato na base). */
  nao_entregavel: boolean
}

/** Uma contribuição do diagnóstico como o backend manda: o fator e o efeito, em texto. */
export interface ContribuicaoTexto {
  fator: string
  efeito: string | null // "Aumentou a chance de recuperar" | "Reduziu a chance de recuperar"
}

export interface EventoLinhaDoTempo {
  em: string
  tipo: 'abertura' | 'diagnostico' | 'tentativa' | 'sugestoes' | 'escolha' | 'mensagem' | 'desfecho' | 'aviso'
  titulo: string
  detalhe?: string
  tom?: 'ok' | 'warn' | 'danger' | 'neutro'
}

export interface CicloDetalhe extends CicloResumo {
  chance_recuperar: number | null // 0..1; null quando o diagnóstico não registrou a estimativa
  /** Desconto por comportamento fora do padrão já aplicado em `chance_recuperar` (em %), ou null. */
  desconto_anomalia_pct: number | null
  dia_provavel_saldo: string | null
  contribuicoes: ContribuicaoTexto[]
  tentativas: Tentativa[]
  /** Todas as rodadas de sugestões do ciclo, da primeira à última. */
  sugestoes: SugestaoDoCiclo[]
  /** O modo da empresa quando o ciclo foi lido (null na demonstração: vale a configuração). */
  modo_mensagem: 'automatico' | 'escolha' | null
  /** Até quando a empresa escolhe; depois disso a recomendada sai sozinha. Null se não espera escolha. */
  escolha_ate: string | null
  escolha_por: 'automatico' | 'admin' | 'owner' | 'prazo' | null
  motivo_descarte: string | null
  linha_do_tempo: EventoLinhaDoTempo[]
}

export interface MetricasMes {
  mes: string // "2026-09"
  valor_liquido_recuperado: number
  ciclos_ativos: number
  recuperados: number
  encerrados_sem_recuperacao: number
  aguardando_escolha: number
  taxa_recuperacao: number | null // 0..1, sobre ciclos com desfecho; null quando ainda não há desfecho
  proxima_acao: { quando: string; descricao: string } | null
}

export interface PontoSerie {
  dia: string // "2026-09-01"
  valor: number
}

export interface Configuracao {
  modo_mensagem_involuntario: 'automatico' | 'escolha'
  prazo_escolha_horas: number
  janela_contato: { inicio: number; fim: number } // horas, ex.: 8 às 20
  canais: Canal[] // permitidos, na ordem de preferência
  notificacoes: { resumo_semanal: boolean; risco_grave: boolean; escolha_pendente: boolean }
  retencao_dias: { mensagens: number; ciclos_meses: number; base_meses_apos_contrato: number; trilha_anos: number }
}

export interface EmpresaDetalhe {
  nome: string
  nome_nas_mensagens: string
  assinatura: string
  idioma: 'pt-BR'
  plano: 'essencial' | 'premium'
  cnpj_mascarado: string // só os 4 últimos dígitos
  desde: string
}

export type Papel = 'owner' | 'admin' | 'membro'

export interface Membro {
  id: string
  nome: string
  email_mascarado: string // a•••@empresa.com
  papel: Papel
  desde: string
  voce: boolean
}

/** Uma chave de API como a tela a mostra. A chave inteira NÃO está aqui: só o começo e o final. */
export interface ChaveApi {
  id: string
  /** O nome que a empresa deu à chave. */
  nome: string
  inicio: string // "crai_live_a1b2"
  final: string // "9f3c"
  criada_em: string
  ultimo_uso: string | null
  revogada_em: string | null
}

/** `GET /integracao/chaves`: as chaves da empresa e o que quem está logado pode fazer. */
export interface ChavesDaEmpresa {
  chaves: ChaveApi[]
  ativas: number
  limite_ativas: number
  /** O papel revoga (dono e administrador), em qualquer plano. */
  pode_revogar: boolean
  /** A empresa é do plano premium: só nele se gera chave. */
  plano_permite_gerar: boolean
  /** O papel gera E o plano permite. */
  pode_gerar: boolean
}

export interface Integracao {
  webhook: { url: string | null; segredo_inicio: string | null; ultimo_evento: string | null }
}

export interface ResultadoTesteIntegracao {
  ok: boolean
  passos: { rotulo: string; ok: boolean; detalhe: string }[]
}

export interface ExplicacaoDecisao {
  cliente: string
  decisao: string
  quando: string
  fatores: Contribuicao[]
  revisao_humana: string | null
}

export interface Empresa {
  nome: string
  plano: 'essencial' | 'premium'
  papel: 'owner' | 'admin' | 'membro'
}

/* ------------------------------------------------------------------ */
/* Visão geral (rotas da Etapa 3, Bloco 4; ver a lista no HANDOFF)      */
/* ------------------------------------------------------------------ */

/** Um dia do gráfico de 30 dias: os dois churns, já líquidos da taxa. */
export interface PontoSerieDupla {
  dia: string // "2026-09-01"
  involuntario: number
  voluntario: number
}

export interface ResumoVisaoGeral {
  periodo: { de: string; ate: string }
  recuperado_involuntario: number // líquido
  cobrancas_recuperadas: number
  retido_voluntario: number | null // líquido; null quando a empresa não é premium
  clientes_mantidos: number | null // null quando a empresa não é premium
  ciclos_ativos: number
  aguardando_escolha: number
  clientes_risco_grave: number | null // null quando a empresa não é premium
  risco_grave_com_oferta: number | null
  taxa_recuperacao: number | null // 0..1, sobre ciclos com desfecho; null quando ainda não há desfecho
  ciclos_com_desfecho: number
}

export interface EtapaFunil {
  etapa: 'falhas' | 'tentativa_1' | 'tentativa_2' | 'tentativa_3' | 'mensagem'
  rotulo: string
  chegaram: number
  valor: number // valor das cobranças que chegaram à etapa
  recuperados_aqui: number
  valor_recuperado_aqui: number
}

export interface Funil {
  mes: string
  etapas: EtapaFunil[]
  desfecho: { recuperados: number; encerrados: number; em_andamento: number }
}

export interface ItemDesempenho {
  rotulo: string
  taxa: number // 0..1
  casos: number
  valor?: number
}

export interface OQueFunciona {
  causas: ItemDesempenho[] // taxa = recuperação
  ofertas: ItemDesempenho[] // taxa = aceite
  canais: ItemDesempenho[] // taxa = resposta
}

export type TipoAtividade = 'recuperado' | 'tentativa_falhou' | 'oferta_aceita' | 'mensagem_enviada' | 'risco_grave' | 'estorno' | 'escolha'

export interface Atividade {
  id: number | string
  em: string
  tipo: TipoAtividade
  texto: string
  valor: number | null
  simulado: boolean
}

export interface SaudeSistema {
  relogio: { ativo: boolean; ultima_passagem: string | null }
  modelos: { carregados: number; total: number }
  redator: { disponivel: boolean }
  /** `origem` null: a base é anterior ao registro de origem, e a tela não adivinha. */
  base: { origem: 'api' | 'anexo' | null; atualizada_em: string } | null
  /** Em modo real, as linhas que um backend antigo não informa e continuam de demonstração. */
  demonstracao?: ('modelos' | 'redator' | 'base')[]
}

export interface LinhaExtrato {
  id: string
  data: string
  cliente: string
  origem: 'involuntario' | 'voluntario'
  /**
   * O backend manda uma linha por fato: a recuperação ou o cliente mantido (valores
   * positivos) e, à parte, o estorno (valores negativos, no mês em que aconteceu). Ausente
   * na demonstração, onde o estorno é a própria linha zerada.
   */
  tipo?: 'recuperacao' | 'mantido' | 'estorno'
  descricao: string
  valor_base: number // cobrança recuperada, ou MRR menos o desconto
  taxa: number
  liquido: number
  estornado: boolean
  simulado: boolean
}

/* ------------------------------------------------------------------ */
/* Simulação do gateway (rotas da Etapa 3, Bloco 2)                     */
/* ------------------------------------------------------------------ */

export type PerfilPagador = 'clt' | 'pj' | 'freelancer'

/** O que o usuário define e os modelos NUNCA veem. Fica fora das features. */
export interface VerdadeEscondida {
  dias_ate_saldo: number // em quantos dias o dinheiro entra na conta (0 = já tem)
  chance_pagar: number // 0..1: chance de a cobrança passar quando há saldo
  vai_revogar: boolean // revoga a autorização na 1ª tentativa
}

export interface ClienteFicticio {
  nome: string
  mensalidade: number
  perfil: PerfilPagador
  verdade: VerdadeEscondida
}

export type EtapaSimulacao = 'cobranca' | 'tentativa_1' | 'tentativa_2' | 'tentativa_3' | 'mensagem' | 'desfecho'

/** O que o cartão está mostrando agora. */
export type FaseSimulacao =
  | 'formulario' // ainda não começou
  | 'processando' // cobrança em andamento (brilho laranja)
  | 'recusada' // acabou de falhar (brilho vermelho)
  | 'aguardando' // esperando o relógio chegar na próxima ação
  | 'mensagens' // as 3 tentativas falharam: escolher/enviar a mensagem
  | 'mensagem_enviada' // esperando a resposta do cliente
  | 'recuperada' // brilho verde
  | 'encerrada' // sem recuperação

export interface TentativaSimulada {
  numero: number
  agendada_para: string
  resultado: 'agendada' | 'paga' | 'falhou' | 'cancelada'
  causa: CausaFalha | null
}

export interface EstadoSimulacao {
  id_ciclo: number | null
  cliente: ClienteFicticio | null
  id_recorrencia: string
  hoje: string // dia simulado (ISO)
  inicio: string
  fase: FaseSimulacao
  etapas_concluidas: EtapaSimulacao[]
  etapa_atual: EtapaSimulacao
  causa: CausaFalha | null
  chance_recuperar: number | null
  dia_provavel_saldo: string | null
  /** O fator e o efeito, em texto, como o diagnóstico de um ciclo de verdade. */
  contribuicoes: ContribuicaoTexto[]
  tentativas: TentativaSimulada[]
  proxima_acao: { quando: string; descricao: string } | null
  sugestoes: Sugestao[]
  mensagem_enviada: { abordagem: Abordagem; canal: Canal; em: string; escolhida_por: 'owner' | 'admin' | 'prazo' | 'automatico' } | null
  desfecho: { tipo: 'recuperado' | 'encerrado'; via: 'tentativa' | 'mensagem'; tentativa: number | null; valor_liquido: number; em: string } | null
  pensando: string[] // "O que o sistema está pensando", em frases
  sem_crai: { resultado: 'recuperado' | 'perdido'; explicacao: string } | null
  linha_do_tempo: EventoLinhaDoTempo[]
  /** A configuração da empresa que vale para a mensagem do ciclo simulado. */
  modo_mensagem: 'automatico' | 'escolha'
  prazo_escolha_horas: number
}

/** As quatro ofertas que o sistema faz de verdade (as mesmas do backend). */
export type OfertaRetencao = 'desconto_10' | 'desconto_20' | 'pausa_1_mes' | 'pix_boleto_flash'

export interface ClienteRiscoFicticio {
  nome: string
  mrr: number
  /** Cada sinal marcado vira um dado que o sistema recebe; o não marcado não vira nada. */
  sinais: { uso_caiu: boolean; tickets: boolean; atraso: boolean; abriu_cancelamento: boolean }
  /** Propensão escondida a aceitar cada oferta (0..1). O sistema não vê. */
  propensao: Record<OfertaRetencao, number>
}

export interface ResultadoRetencaoSimulada {
  faixa: 'grave' | 'preocupante' | 'sem_risco'
  motivo: string
  /** Quem decidiu a faixa: o modelo de IA ou a régua. */
  decidido_por: 'modelo' | 'regua'
  /** O risco que o sistema calculou (0 a 1) e o corte abaixo do qual ele não intervém. */
  risco: number | null
  corte_de_intervencao: number
  /** null: sem risco, o sistema não faz oferta. */
  oferta: OfertaRetencao | null
  oferta_legivel: string | null
  canal_legivel: string | null
  porque: string | null
  /** null quando não houve oferta. */
  aceitou: boolean | null
  valor_mantido_liquido: number
  sem_crai: string
  /** A regra do mantido que o backend aplica (meses de mensalidade e prazo do estorno). */
  meses_de_mrr: number
  prazo_estorno_dias: number
}

/* ------------------------------------------------------------------ */
/* Voluntário (rotas da Etapa 3, Bloco 1)                               */
/* ------------------------------------------------------------------ */

export type FaixaRisco = 'grave' | 'preocupante' | 'sem_risco' | 'sem_dado'

export interface ClienteRisco {
  id: string
  nome: string // o nome da base; sem nome, o id que a empresa usa
  mrr: number | null // null: cliente que só chegou por evento, sem mensalidade conhecida
  faixa: FaixaRisco
  motivo: string // uma frase, em linguagem simples
  /** null: não há avaliação (sem dado), ou ela veio de um evento que não registra quem decidiu. */
  risco_decidido_por: 'modelo' | 'regua' | null
  posicao_na_base: number | null // 1 = maior risco da base; null quando não há dado suficiente
  /** A oferta e o canal já vêm em português (o backend manda o texto pronto). */
  abordagem: { oferta: string; canal: string; status: 'aguardando' | 'enviada' | 'aceita' | 'recusada' } | null
  atualizado_em: string | null
  simulado: boolean
}

export interface BaseClientes {
  total: number
  com_dados_comportamento: number
  /** Para quantos clientes o modelo de IA decide o risco hoje (0 se não há modelo ativo). */
  decididos_pelo_modelo: number
  atualizada_em: string
  /** null: a base é anterior ao registro de origem, e a tela não adivinha. */
  origem: 'api' | 'anexo' | null
}

export interface ResumoVoluntario {
  mes: string
  valor_liquido_mantido: number
  clientes_mantidos: number
  estornos: number
  grave: number
  preocupante: number
  ofertas_enviadas: number
  ofertas_aceitas: number
  /** A regra do "mantido", como o backend a aplica: quantos meses de mensalidade e o prazo do estorno. */
  meses_de_mrr: number
  prazo_estorno_dias: number
}

export interface ComparacaoReguaModelo {
  dias: number
  clientes_com_dados: number
  cancelamentos: number
  regua: { marcou_grave: number; avisou_antes: number }
  modelo: { marcou_grave: number; avisou_antes: number }
}

export interface ResultadoImportacao {
  arquivo: string
  linhas: number
  /** Quantas linhas entraram na base e quantas foram recusadas (com o motivo em `avisos`). */
  importados: number
  rejeitados: number
  /** O backend não separa novos de corrigidos, nem conta os sem id da recorrência: null. */
  novos: number | null
  atualizados: number | null
  sem_id_recorrencia: number | null
  /** Importadas sem dado de comportamento: ficam como "Sem dado suficiente". */
  sem_comportamento: number | null
  avisos: string[]
  /** Só na demonstração: o arquivo não saiu do navegador. */
  demonstracao: boolean
}

/* ------------------------------------------------------------------ */
/* Assistente (rota da Etapa 3, Bloco 3)                                */
/* ------------------------------------------------------------------ */

export interface RespostaAssistente {
  texto: string // pode ter parágrafos separados por linha em branco
  links: { rotulo: string; para: string }[] // "Ver no involuntário"
  sugestoes: string[] // próximas perguntas
  origem: 'assistente' | 'texto_fixo' // texto_fixo = o redator está fora do ar
}

import type {
  Atividade,
  ChaveApi,
  Configuracao,
  EmpresaDetalhe,
  Membro,
  BaseClientes,
  ClienteRisco,
  ComparacaoReguaModelo,
  FaixaRisco,
  ResumoVoluntario,
  CausaFalha,
  Funil,
  LinhaExtrato,
  OQueFunciona,
  PontoSerieDupla,
  SaudeSistema,
  CicloDetalhe,
  CicloResumo,
  Empresa,
  EstadoCiclo,
  MetricasMes,
  PontoSerie,
  StatusTela,
} from './tipos'

/** "Agora" fixo, para a tela ficar estável enquanto os dados são de mentira. */
export const AGORA = new Date('2026-09-30T14:20:00-03:00')

function d(dias: number, hora = 9): string {
  const x = new Date(AGORA)
  x.setDate(x.getDate() + dias)
  x.setHours(hora, 0, 0, 0)
  return x.toISOString()
}

export const empresa: Empresa = { nome: 'NimbusFlow Tecnologia', plano: 'premium', papel: 'owner' }

export const CAUSA_LEGIVEL: Record<CausaFalha, string> = {
  insufficient_funds: 'Saldo insuficiente',
  limit_exceeded: 'Limite do Pix excedido',
  authorization_revoked: 'Autorização revogada',
  processing_error: 'Erro no processamento',
  generic_decline: 'Recusa sem motivo informado',
}

export function statusDoEstado(estado: EstadoCiclo, executadas: number): StatusTela {
  if (estado === 'recuperado') return 'recuperado'
  if (estado === 'perdido' || estado === 'descartado') return 'encerrado'
  if (estado === 'recobrando' && executadas === 0) return 'em_analise'
  return 'em_processo'
}

interface Base {
  id: number
  cliente: string | null
  rec: string
  valor: number
  causa: CausaFalha
  estado: EstadoCiclo
  exec: number
  aberto: number // dias relativos
  prox?: [number, string]
  simulado?: boolean
}

const base: Base[] = [
  { id: 41, cliente: 'Studio Vetor Ltda.', rec: 'RN_7f3a9c21', valor: 1290, causa: 'insufficient_funds', estado: 'recobrando', exec: 0, aberto: 0, prox: [1, 'Tentativa 1 no dia provável de saldo'] },
  { id: 40, cliente: 'Padaria do Bairro ME', rec: 'RN_2b81de04', valor: 349.9, causa: 'insufficient_funds', estado: 'recobrando', exec: 1, aberto: -2, prox: [2, 'Tentativa 2 agendada'] },
  { id: 39, cliente: null, rec: 'RN_9e0c44f7', valor: 2480, causa: 'processing_error', estado: 'recobrando', exec: 2, aberto: -4, prox: [1, 'Tentativa 3 agendada'] },
  { id: 38, cliente: 'Clínica Horizonte', rec: 'RN_c31a77b2', valor: 4900, causa: 'insufficient_funds', estado: 'aguardando_escolha', exec: 3, aberto: -7, prox: [0, 'Escolha da mensagem: 6 h 40 min restantes'] },
  { id: 37, cliente: 'Loja Marés', rec: 'RN_5d2e19aa', valor: 799, causa: 'authorization_revoked', estado: 'mensagem_enviada', exec: 0, aberto: -1 },
  { id: 36, cliente: 'Escola Aurora', rec: 'RN_a8f06c3d', valor: 1890, causa: 'limit_exceeded', estado: 'mensagem_enviada', exec: 3, aberto: -9 },
  { id: 35, cliente: 'Agência Norte', rec: 'RN_e4b2c910', valor: 3200, causa: 'insufficient_funds', estado: 'recuperado', exec: 2, aberto: -6 },
  { id: 34, cliente: 'Oficina Central', rec: 'RN_1c9d55e8', valor: 560, causa: 'processing_error', estado: 'recuperado', exec: 1, aberto: -8 },
  { id: 33, cliente: 'Consultório Vida', rec: 'RN_77aa02bd', valor: 1150, causa: 'insufficient_funds', estado: 'recuperado', exec: 3, aberto: -14 },
  { id: 32, cliente: null, rec: 'RN_b06e3f12', valor: 2100, causa: 'generic_decline', estado: 'perdido', exec: 3, aberto: -41 },
  { id: 31, cliente: 'Mercado Sol', rec: 'RN_f2d8a1c4', valor: 420, causa: 'insufficient_funds', estado: 'descartado', exec: 0, aberto: -3 },
  { id: 30, cliente: 'Cliente fictício · Ana Souza', rec: 'RN_sim_0001', valor: 300, causa: 'insufficient_funds', estado: 'recobrando', exec: 1, aberto: -1, prox: [2, 'Tentativa 2 (simulação)'], simulado: true },
]

export const ciclos: CicloResumo[] = base.map((b) => ({
  id: b.id,
  cliente: b.cliente,
  id_recorrencia: b.rec,
  valor_cobranca: b.valor,
  valor_liquido: b.estado === 'recuperado' ? Math.round(b.valor * (1 - 0.25) * 100) / 100 : null,
  causa: b.causa,
  estado: b.estado,
  status: statusDoEstado(b.estado, b.exec),
  tentativas_executadas: b.exec,
  tentativas_total: b.estado === 'descartado' ? 0 : 3,
  proxima_acao: b.prox ? d(b.prox[0], 10) : null,
  proxima_acao_descricao: b.prox ? b.prox[1] : null,
  aberto_em: d(b.aberto, 8),
  atualizado_em: d(Math.min(0, b.aberto + 1), 11),
  simulado: b.simulado ?? false,
}))

/* ------------------------------------------------------------------ */
/* Extrato do mês: a fonte dos totais e das séries                      */
/* ------------------------------------------------------------------ */

/** Taxas do plano de negócio (4.2): 25% no involuntário, 20% no voluntário. */
export const TAXA = { involuntario: 0.25, voluntario: 0.2 }

const centavos = (v: number) => Math.round(v * 100) / 100

function linha(
  id: string,
  dias: number,
  hora: number,
  cliente: string,
  origem: 'involuntario' | 'voluntario',
  descricao: string,
  valor_base: number,
  extra: { estornado?: boolean; simulado?: boolean } = {},
): LinhaExtrato {
  const taxa = extra.estornado ? 0 : centavos(valor_base * TAXA[origem])
  return {
    id,
    data: d(dias, hora),
    cliente,
    origem,
    descricao,
    valor_base,
    taxa,
    liquido: extra.estornado ? 0 : centavos(valor_base - taxa),
    estornado: extra.estornado ?? false,
    simulado: extra.simulado ?? false,
  }
}

export const extrato: LinhaExtrato[] = [
  linha('rec-35', -3, 10, 'Agência Norte', 'involuntario', 'Recuperado na 2ª tentativa', 3200),
  linha('rec-34', -7, 9, 'Oficina Central', 'involuntario', 'Recuperado na 1ª tentativa', 560),
  linha('rec-33', -10, 9, 'Consultório Vida', 'involuntario', 'Recuperado na 3ª tentativa', 1150),
  linha('rec-29', -12, 9, 'Academia Ritmo', 'involuntario', 'Recuperado na 1ª tentativa', 2890),
  linha('rec-27', -15, 15, 'Construtora Base Forte', 'involuntario', 'Recuperado depois da mensagem', 5400),
  linha('rec-25', -18, 10, 'Floricultura Jardim', 'involuntario', 'Recuperado na 2ª tentativa', 1990),
  linha('rec-22', -21, 9, 'Colégio Integral', 'involuntario', 'Recuperado na 2ª tentativa', 4200),
  linha('rec-19', -24, 9, 'Laboratório Alfa', 'involuntario', 'Recuperado na 1ª tentativa', 3450),
  linha('rec-16', -27, 11, 'Restaurante Sabor', 'involuntario', 'Recuperado na 3ª tentativa', 1736.67),
  linha('ret-12', -3, 16, 'Café Aroma', 'voluntario', 'Aceitou desconto de 20% por 3 meses (MRR R$ 890,00)', 712),
  linha('ret-11', -5, 11, 'Rede Pilates Move', 'voluntario', 'Aceitou desconto de 20% por 3 meses (MRR R$ 2.400,00)', 1920),
  linha('ret-10', -9, 14, 'Contábil Prisma', 'voluntario', 'Aceitou suporte dedicado, sem desconto (MRR R$ 3.600,00)', 3600),
  linha('ret-09', -14, 10, 'Imobiliária Casa Clara', 'voluntario', 'Aceitou desconto de 20% por 3 meses (MRR R$ 1.500,00)', 1200),
  linha('ret-08', -20, 15, 'Pet Shop Amigo', 'voluntario', 'Cancelou 12 dias depois do aceite: estornado', 552, { estornado: true }),
  linha('ret-07', -25, 10, 'Instituto Saber', 'voluntario', 'Aceitou desconto de 20% por 3 meses (MRR R$ 4.200,00)', 3360),
  linha('sim-02', -1, 16, 'Cliente fictício · Bruno Lima', 'voluntario', 'Aceitou desconto de 20% (MRR R$ 300,00)', 240, { simulado: true }),
].sort((a, b) => (a.data < b.data ? 1 : -1))

const somaLiquido = (origem: LinhaExtrato['origem']) =>
  centavos(extrato.filter((l) => l.origem === origem && !l.simulado).reduce((t, l) => t + l.liquido, 0))

export const metricasMes: MetricasMes = {
  mes: '2026-09',
  valor_liquido_recuperado: somaLiquido('involuntario'),
  ciclos_ativos: ciclos.filter((c) => c.status === 'em_analise' || c.status === 'em_processo').length,
  recuperados: 9,
  encerrados_sem_recuperacao: 2,
  aguardando_escolha: 1,
  taxa_recuperacao: 9 / 11,
  proxima_acao: { quando: d(1, 10), descricao: '3 tentativas agendadas para amanhã' },
}

/** Os 30 dias que terminam hoje. */
function diasDoPeriodo(): string[] {
  return Array.from({ length: 30 }, (_, i) => {
    const dia = new Date(AGORA)
    dia.setDate(dia.getDate() - (29 - i))
    return diaLocal(dia)
  })
}

/** "2026-09-30" no fuso do navegador (evita o dia pular com o UTC). */
export function diaLocal(x: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${x.getFullYear()}-${p(x.getMonth() + 1)}-${p(x.getDate())}`
}

/** Os dois churns por dia, líquidos da taxa. */
export function serieDupla(incluirSimulados: boolean): PontoSerieDupla[] {
  return diasDoPeriodo().map((dia) => {
    const doDia = extrato.filter((l) => diaLocal(new Date(l.data)) === dia && (incluirSimulados || !l.simulado))
    const soma = (o: LinhaExtrato['origem']) => centavos(doDia.filter((l) => l.origem === o).reduce((t, l) => t + l.liquido, 0))
    return { dia, involuntario: soma('involuntario'), voluntario: soma('voluntario') }
  })
}

/** Série de um só churn, usada na sparkline da página do involuntário. */
export const serie30: PontoSerie[] = serieDupla(false).map((p) => ({ dia: p.dia, valor: p.involuntario }))

/* ------------------------------------------------------------------ */
/* Funil, o que funciona, atividade e saúde                             */
/* ------------------------------------------------------------------ */

/** Coerente com a lista de ciclos: 17 falhas reais em setembro, 9 recuperadas, 2 encerradas, 6 em andamento. */
export const funil: Funil = {
  mes: '2026-09',
  etapas: [
    { etapa: 'falhas', rotulo: 'Cobranças que falharam', chegaram: 17, valor: 38805.57, recuperados_aqui: 0, valor_recuperado_aqui: 0 },
    { etapa: 'tentativa_1', rotulo: 'Tentativa 1', chegaram: 14, valor: 36296.57, recuperados_aqui: 3, valor_recuperado_aqui: 6900 },
    { etapa: 'tentativa_2', rotulo: 'Tentativa 2', chegaram: 10, valor: 29046.67, recuperados_aqui: 3, valor_recuperado_aqui: 9390 },
    { etapa: 'tentativa_3', rotulo: 'Tentativa 3', chegaram: 6, valor: 17176.67, recuperados_aqui: 2, valor_recuperado_aqui: 2886.67 },
    { etapa: 'mensagem', rotulo: 'Mensagem', chegaram: 5, valor: 15089, recuperados_aqui: 1, valor_recuperado_aqui: 5400 },
  ],
  desfecho: { recuperados: 9, encerrados: 2, em_andamento: 6 },
}

export const oQueFunciona: OQueFunciona = {
  causas: [
    { rotulo: 'Saldo insuficiente', taxa: 6 / 7, casos: 7, valor: 13940 },
    { rotulo: 'Recusa sem motivo informado', taxa: 0.5, casos: 2, valor: 4200 },
    { rotulo: 'Erro no processamento', taxa: 1, casos: 1, valor: 560 },
    { rotulo: 'Limite do Pix excedido', taxa: 0, casos: 1, valor: 0 },
  ],
  ofertas: [
    { rotulo: 'Desconto de 20% por 3 meses', taxa: 5 / 12, casos: 12 },
    { rotulo: 'Suporte dedicado', taxa: 2 / 6, casos: 6 },
    { rotulo: 'Plano mais leve', taxa: 2 / 8, casos: 8 },
  ],
  canais: [
    { rotulo: 'WhatsApp', taxa: 14 / 22, casos: 22 },
    { rotulo: 'E-mail', taxa: 8 / 26, casos: 26 },
    { rotulo: 'SMS', taxa: 2 / 11, casos: 11 },
  ],
}

const hm = (dias: number, hora: number, minuto = 0) => {
  const x = new Date(AGORA)
  x.setDate(x.getDate() + dias)
  x.setHours(hora, minuto, 0, 0)
  return x.toISOString()
}

export const atividades: Atividade[] = ([
  { id: 9, em: hm(0, 13, 48), tipo: 'risco_grave', texto: 'Loja Ponto Certo entrou em risco grave: o uso caiu pela metade em 2 semanas', valor: null, simulado: false },
  { id: 8, em: hm(0, 11, 0), tipo: 'tentativa_falhou', texto: 'Tentativa 2 não passou para um cliente sem cadastro: erro no processamento', valor: null, simulado: false },
  { id: 7, em: hm(-1, 16, 30), tipo: 'oferta_aceita', texto: 'Bruno Lima aceitou desconto de 20%', valor: 192, simulado: true },
  { id: 6, em: hm(-1, 10, 15), tipo: 'mensagem_enviada', texto: 'Mensagem enviada por e-mail para Loja Marés, que revogou a autorização', valor: null, simulado: false },
  { id: 5, em: hm(-1, 9, 0), tipo: 'escolha', texto: '3 mensagens sugeridas para Clínica Horizonte, aguardando a sua escolha', valor: null, simulado: false },
  { id: 4, em: hm(-3, 16, 0), tipo: 'oferta_aceita', texto: 'Café Aroma aceitou desconto de 20% por 3 meses', valor: 569.6, simulado: false },
  { id: 3, em: hm(-3, 10, 0), tipo: 'recuperado', texto: 'Cobrança de Agência Norte recuperada na 2ª tentativa', valor: 2400, simulado: false },
  { id: 2, em: hm(-5, 11, 0), tipo: 'oferta_aceita', texto: 'Rede Pilates Move aceitou desconto de 20% por 3 meses', valor: 1536, simulado: false },
  { id: 1, em: hm(-8, 9, 30), tipo: 'estorno', texto: 'Pet Shop Amigo cancelou 12 dias depois do aceite; o valor saiu do mantido', valor: null, simulado: false },
] as Atividade[]).sort((a, b) => (a.em < b.em ? 1 : -1))

export const saude: SaudeSistema = {
  relogio: { ativo: true, ultima_passagem: new Date(AGORA.getTime() - 40_000).toISOString() },
  modelos: { carregados: 3, total: 3 },
  redator: { disponivel: true },
  base: { origem: 'api', atualizada_em: new Date(AGORA.getTime() - 2 * 3_600_000).toISOString() },
}

export const riscoGrave = { clientes: 4, com_oferta: 2 }

/** Um ciclo em detalhe: o da Clínica Horizonte (aguardando escolha). */
export const detalhes: Record<number, CicloDetalhe> = {
  38: {
    ...ciclos.find((c) => c.id === 38)!,
    chance_recuperar: 0.72,
    desconto_anomalia_pct: null,
    dia_provavel_saldo: d(-6, 9),
    contribuicoes: [
      { fator: 'Causa: saldo insuficiente', efeito: 'Aumentou a chance de recuperar' },
      { fator: 'Cliente há 3 anos', efeito: 'Aumentou a chance de recuperar' },
      { fator: 'Bom histórico de pagamento', efeito: 'Aumentou a chance de recuperar' },
      { fator: 'Valor alto para o perfil', efeito: 'Reduziu a chance de recuperar' },
      { fator: 'Já é a 3ª tentativa', efeito: 'Reduziu a chance de recuperar' },
    ],
    tentativas: [
      { numero: 1, agendada_para: d(-6, 9), disparada_em: d(-6, 9), resultado: 'falhou', resultado_em: d(-6, 9), codigo_resultado: 'AM04', motivo_cancelamento: null },
      { numero: 2, agendada_para: d(-4, 9), disparada_em: d(-4, 9), resultado: 'falhou', resultado_em: d(-4, 10), codigo_resultado: 'AM04', motivo_cancelamento: null },
      { numero: 3, agendada_para: d(-1, 9), disparada_em: d(-1, 9), resultado: 'falhou', resultado_em: d(-1, 9), codigo_resultado: 'AM04', motivo_cancelamento: null },
    ],
    sugestoes: [
      {
        abordagem: 'lembrete_cordial',
        texto: 'Olá! A mensalidade de R$ 4.900,00 da Clínica Horizonte não pôde ser debitada. Quando for possível, é só regularizar por este link. Qualquer dúvida, estamos por aqui.',
        canal: 'whatsapp',
        motivo_canal: 'Telefone cadastrado; canal preferido da empresa',
        recomendada: true,
        escolhida: false,
        rodada: 1,
        nao_entregavel: false,
      },
      {
        abordagem: 'facilitacao',
        texto: 'Olá! Vimos que o débito de R$ 4.900,00 não passou. Se ficar mais fácil, dá para pagar por boleto ou Pix avulso neste link, sem alterar a sua assinatura.',
        canal: 'whatsapp',
        motivo_canal: 'Telefone cadastrado; canal preferido da empresa',
        recomendada: false,
        escolhida: false,
        rodada: 1,
        nao_entregavel: false,
      },
      {
        abordagem: 'urgencia_com_respeito',
        texto: 'Olá. A mensalidade de R$ 4.900,00 continua pendente e o acesso será suspenso em 5 dias. Para manter tudo funcionando, regularize por este link.',
        canal: 'whatsapp',
        motivo_canal: 'Telefone cadastrado; canal preferido da empresa',
        recomendada: false,
        escolhida: false,
        rodada: 1,
        nao_entregavel: false,
      },
    ],
    modo_mensagem: null,
    // 6 h 40 min depois do "agora" fixo da demonstração
    escolha_ate: new Date(AGORA.getTime() + (6 * 60 + 40) * 60_000).toISOString(),
    escolha_por: null,
    motivo_descarte: null,
    linha_do_tempo: [
      { em: d(-7, 8), tipo: 'abertura', titulo: 'Cobrança falhou', detalhe: 'R$ 4.900,00 · Saldo insuficiente (AM04)', tom: 'danger' },
      { em: d(-7, 8), tipo: 'diagnostico', titulo: 'Diagnóstico: 72% de chance de recuperar', detalhe: 'Dia provável de saldo: 24/09. 3 tentativas agendadas: 24, 26 e 29/09.' },
      { em: d(-6, 9), tipo: 'tentativa', titulo: 'Tentativa 1 falhou', detalhe: 'Saldo insuficiente', tom: 'danger' },
      { em: d(-4, 10), tipo: 'tentativa', titulo: 'Tentativa 2 falhou', detalhe: 'Saldo insuficiente', tom: 'danger' },
      { em: d(-1, 9), tipo: 'tentativa', titulo: 'Tentativa 3 falhou', detalhe: 'Saldo insuficiente. Limite do BACEN atingido.', tom: 'danger' },
      { em: d(-1, 9), tipo: 'sugestoes', titulo: '3 mensagens sugeridas', detalhe: 'Recomendada: Lembrete cordial, por WhatsApp.' },
      { em: d(0, 14), tipo: 'aviso', titulo: 'Aguardando a sua escolha', detalhe: 'Sem escolha até as 17h20, a recomendada é enviada.', tom: 'warn' },
    ],
  },
}

/* ------------------------------------------------------------------ */
/* Voluntário                                                          */
/* ------------------------------------------------------------------ */

export const baseClientes: BaseClientes = {
  total: 1240,
  com_dados_comportamento: 812,
  decididos_pelo_modelo: 812,
  atualizada_em: new Date(AGORA.getTime() - 2 * 3_600_000).toISOString(),
  origem: 'api',
}

/** As ofertas e os canais da demonstração, já no texto que a tabela mostra. */
const OFERTA_DEMO = {
  desconto: 'Desconto de 20% por 3 meses',
  suporte: 'Suporte dedicado por 30 dias',
  plano_leve: 'Plano mais leve, sem multa',
}
const CANAL_DEMO = { whatsapp: 'WhatsApp', email: 'E-mail', sms: 'SMS' }

const cr = (
  id: string,
  nome: string,
  mrr: number,
  faixa: FaixaRisco,
  motivo: string,
  risco_decidido_por: 'modelo' | 'regua',
  horasAtras: number,
  abordagem: { oferta: keyof typeof OFERTA_DEMO; canal: keyof typeof CANAL_DEMO; status: NonNullable<ClienteRisco['abordagem']>['status'] } | null = null,
  simulado = false,
  posicao_na_base: number | null = null,
): ClienteRisco => ({
  id,
  nome,
  mrr,
  faixa,
  motivo,
  risco_decidido_por,
  posicao_na_base,
  abordagem: abordagem ? { oferta: OFERTA_DEMO[abordagem.oferta], canal: CANAL_DEMO[abordagem.canal], status: abordagem.status } : null,
  atualizado_em: new Date(AGORA.getTime() - horasAtras * 3_600_000).toISOString(),
  nao_contatar: false,
  simulado,
})

export const clientesRisco: ClienteRisco[] = [
  cr('c-101', 'Loja Ponto Certo', 1200, 'grave', 'O uso caiu pela metade em 2 semanas e o pagamento atrasou 2 vezes.', 'modelo', 1, { oferta: 'desconto', canal: 'whatsapp', status: 'enviada' }, false, 7),
  cr('c-102', 'Cliente fictício · Bruno Lima', 300, 'grave', 'Sinais marcados na simulação: uso em queda e atraso.', 'regua', 2, { oferta: 'desconto', canal: 'whatsapp', status: 'aceita' }, true, 41),
  cr('c-103', 'Escritório Vértice', 3400, 'grave', 'Abriu 3 chamados no mês e parou de usar o módulo principal. Estava como Preocupante há 12 dias; com mensalidade alta, só sobe para Grave quando os sinais continuam.', 'modelo', 5, { oferta: 'suporte', canal: 'email', status: 'aguardando' }, false, 19),
  cr('c-104', 'Café Aroma', 890, 'preocupante', 'Uso 30% abaixo do normal nas últimas 3 semanas.', 'modelo', 9, { oferta: 'desconto', canal: 'whatsapp', status: 'aceita' }, false, 168),
  cr('c-105', 'Academia Ritmo', 2890, 'sem_risco', 'Uso estável e pagamentos em dia.', 'modelo', 12, null, false, 904),
  cr('c-106', 'Distribuidora Norte Sul', 5600, 'preocupante', 'Reduziu o número de usuários ativos de 12 para 7. Mensalidade alta: entra como Preocupante e só sobe para Grave se os sinais continuarem.', 'modelo', 20, { oferta: 'plano_leve', canal: 'email', status: 'recusada' }, false, 96),
  cr('c-107', 'Padaria do Bairro ME', 349.9, 'sem_dado', 'Cliente novo, sem dados de comportamento ainda; a régua não tem o que avaliar.', 'regua', 26, null, false, null),
  cr('c-108', 'Clínica Horizonte', 4900, 'preocupante', 'Cobrança em recuperação no involuntário e 1 chamado aberto.', 'regua', 30, { oferta: 'suporte', canal: 'whatsapp', status: 'enviada' }, false, 231),
  cr('c-109', 'Rede Pilates Move', 2400, 'sem_risco', 'Aceitou a oferta há 5 dias e voltou a usar normalmente.', 'modelo', 44, null, false, 512),
  cr('c-110', 'Contábil Prisma', 3600, 'sem_risco', 'Uso acima da média e nenhum chamado em 60 dias.', 'modelo', 52, null, false, 1103),
  cr('c-111', 'Pet Shop Amigo', 690, 'grave', 'Cancelou 12 dias depois de aceitar a oferta; o valor foi estornado.', 'modelo', 70, { oferta: 'desconto', canal: 'whatsapp', status: 'aceita' }, false, 3),
]

export const resumoVoluntario: ResumoVoluntario = {
  mes: '2026-09',
  valor_liquido_mantido: extrato.filter((l) => l.origem === 'voluntario' && !l.simulado).reduce((t, l) => t + l.liquido, 0),
  clientes_mantidos: extrato.filter((l) => l.origem === 'voluntario' && !l.simulado && !l.estornado).length,
  estornos: extrato.filter((l) => l.origem === 'voluntario' && l.estornado).length,
  grave: 4,
  preocupante: 7,
  ofertas_enviadas: 26,
  ofertas_aceitas: 9,
  meses_de_mrr: 1,
  prazo_estorno_dias: 30,
}

export const comparacaoReguaModelo: ComparacaoReguaModelo = {
  dias: 30,
  clientes_com_dados: 812,
  cancelamentos: 5,
  regua: { marcou_grave: 31, avisou_antes: 2 },
  modelo: { marcou_grave: 14, avisou_antes: 4 },
}

/* ------------------------------------------------------------------ */
/* Configuração                                                        */
/* ------------------------------------------------------------------ */

export const configuracaoPadrao: Configuracao = {
  modo_mensagem_involuntario: 'escolha',
  prazo_escolha_horas: 8,
  janela_contato: { inicio: 8, fim: 20 },
  canais: ['whatsapp', 'email', 'sms'],
  intervalo_minimo_ofertas_dias: 30,
  notificacoes: { resumo_semanal: true, risco_grave: true, escolha_pendente: true },
  retencao_dias: { mensagens: 90, ciclos_meses: 24, base_meses_apos_contrato: 6, trilha_anos: 5 },
}

export const empresaDetalhe: EmpresaDetalhe = {
  nome: 'NimbusFlow Tecnologia',
  nome_nas_mensagens: 'NimbusFlow',
  assinatura: 'Equipe NimbusFlow',
  idioma: 'pt-BR',
  plano: 'premium',
  cnpj_mascarado: '••.•••.•••/0001-42',
  desde: '2026-08-12T12:00:00Z',
}

export const membros: Membro[] = [
  { id: 'm1', nome: 'Marina Duarte', email_mascarado: 'm•••••@nimbusflow.com.br', papel: 'owner', desde: '2026-08-12T12:00:00Z', voce: true },
  { id: 'm2', nome: 'Rafael Costa', email_mascarado: 'r•••••@nimbusflow.com.br', papel: 'admin', desde: '2026-08-20T12:00:00Z', voce: false },
  { id: 'm3', nome: 'Juliana Prado', email_mascarado: 'j•••••@nimbusflow.com.br', papel: 'membro', desde: '2026-09-03T12:00:00Z', voce: false },
]

export const chavesApi: ChaveApi[] = [
  { id: 'k1', nome: 'Sistema de cobrança', inicio: 'crai_live_7f3a', final: 'k2Qd', criada_em: '2026-09-02T14:00:00Z', ultimo_uso: new Date(AGORA.getTime() - 2 * 3_600_000).toISOString(), revogada_em: null },
  { id: 'k2', nome: 'Planilha da equipe de sucesso', inicio: 'crai_live_c91d', final: 'x8Lm', criada_em: '2026-08-28T10:00:00Z', ultimo_uso: '2026-09-25T18:30:00Z', revogada_em: null },
  { id: 'k0', nome: 'Integração antiga', inicio: 'crai_live_2b8e', final: 'p0Ra', criada_em: '2026-08-15T10:00:00Z', ultimo_uso: '2026-09-01T09:00:00Z', revogada_em: '2026-09-02T14:05:00Z' },
]

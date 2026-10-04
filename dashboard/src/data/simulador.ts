/**
 * Motor da simulação de demonstração. Roda no navegador enquanto o backend não tem o
 * simulador da Etapa 3 (pagador com verdade escondida, PSP simulado, relógio por empresa).
 * Na integração, `api.ts` troca estas funções por chamadas às rotas e o estado devolvido
 * tem a mesma forma (`EstadoSimulacao`), então a tela não muda.
 *
 * Regra de honestidade: os modelos "veem" só o que está em `pensando` e nos campos do
 * ciclo. A `verdade` do cliente fictício só é lida pelo PSP simulado, aqui embaixo.
 */
import { AGORA, TAXA } from './mock'
import type {
  Abordagem,
  CausaFalha,
  ClienteFicticio,
  ClienteRiscoFicticio,
  Contribuicao,
  EstadoSimulacao,
  EtapaSimulacao,
  EventoLinhaDoTempo,
  OfertaRetencao,
  ResultadoRetencaoSimulada,
  Sugestao,
} from './tipos'

/* ---------- utilidades ---------- */

/** Sorteio determinístico: o mesmo nome dá sempre o mesmo resultado (bom para demonstrar). */
function semente(texto: string): number {
  let h = 2166136261
  for (let i = 0; i < texto.length; i++) {
    h ^= texto.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return (h >>> 0) / 4294967295
}

const somaDias = (iso: string, dias: number, hora = 9) => {
  const x = new Date(iso)
  x.setDate(x.getDate() + dias)
  x.setHours(hora, 0, 0, 0)
  return x.toISOString()
}
const diasEntre = (a: string, b: string) => Math.round((new Date(b).setHours(12, 0, 0, 0) - new Date(a).setHours(12, 0, 0, 0)) / 86_400_000)
const dataCurta = (iso: string) => new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
const centavos = (v: number) => Math.round(v * 100) / 100

/* ---------- o que o sistema estima (sem ver a verdade) ---------- */

/** Dia provável de saldo por perfil, em dias a partir da cobrança. É a estimativa do sistema, não a verdade. */
const ESTIMATIVA_SALDO: Record<ClienteFicticio['perfil'], { dias: number; texto: string }> = {
  clt: { dias: 5, texto: 'Perfil CLT: o salário costuma entrar até o 5º dia útil' },
  pj: { dias: 4, texto: 'Perfil PJ: o caixa costuma ter entrada na primeira semana' },
  freelancer: { dias: 2, texto: 'Perfil freelancer: entradas irregulares; o sistema tenta cedo e espalha as tentativas' },
}

function diagnostico(cliente: ClienteFicticio, causa: CausaFalha): { chance: number; contribuicoes: Contribuicao[] } {
  const c: Contribuicao[] = []
  let pontos = 50
  if (causa === 'insufficient_funds') {
    c.push({ fator: 'Causa: saldo insuficiente (passa quando o dinheiro entra)', pontos: 14 })
    pontos += 14
  } else if (causa === 'processing_error') {
    c.push({ fator: 'Causa: erro no processamento (costuma passar na próxima)', pontos: 20 })
    pontos += 20
  } else if (causa === 'authorization_revoked') {
    c.push({ fator: 'Causa: autorização revogada (só a mensagem resolve)', pontos: -18 })
    pontos -= 18
  }
  const perfil = { clt: 8, pj: 4, freelancer: -5 }[cliente.perfil]
  c.push({ fator: { clt: 'Perfil CLT: renda previsível', pj: 'Perfil PJ: caixa razoavelmente previsível', freelancer: 'Perfil freelancer: renda irregular' }[cliente.perfil], pontos: perfil })
  pontos += perfil
  if (cliente.mensalidade > 2000) {
    c.push({ fator: 'Valor alto para o perfil', pontos: -7 })
    pontos -= 7
  } else if (cliente.mensalidade < 500) {
    c.push({ fator: 'Valor baixo: fácil de regularizar', pontos: 5 })
    pontos += 5
  }
  c.push({ fator: 'Cliente fictício: sem histórico de pagamento', pontos: -3 })
  pontos -= 3
  return { chance: Math.min(0.95, Math.max(0.05, pontos / 100)), contribuicoes: c }
}

/** Sem a CRAI: o banco do pagador só tenta nas duas janelas do dia do vencimento (regra do Pix Automático). */
function semCrai(cliente: ClienteFicticio): EstadoSimulacao['sem_crai'] {
  const v = cliente.verdade
  if (v.vai_revogar) {
    return { resultado: 'perdido', explicacao: 'O cliente revoga a autorização. Sem a CRAI, ninguém fala com ele e a assinatura acaba.' }
  }
  if (v.dias_ate_saldo === 0 && v.chance_pagar >= 0.5) {
    return { resultado: 'recuperado', explicacao: 'O dinheiro já está na conta. O próprio banco resolveria na segunda janela do dia do vencimento.' }
  }
  return {
    resultado: 'perdido',
    explicacao: `Sem a CRAI, o banco só tenta de novo no dia do vencimento (duas janelas). O dinheiro entra em ${v.dias_ate_saldo} ${v.dias_ate_saldo === 1 ? 'dia' : 'dias'}, então a cobrança se perde e a empresa precisa correr atrás por conta própria.`,
  }
}

/* ---------- o PSP simulado (a única parte que lê a verdade) ---------- */

function pspResponde(cliente: ClienteFicticio, diaRelativo: number, numero: number): { pagou: boolean; causa: CausaFalha | null } {
  const v = cliente.verdade
  if (v.vai_revogar && numero === 1) return { pagou: false, causa: 'authorization_revoked' }
  if (diaRelativo < v.dias_ate_saldo) return { pagou: false, causa: 'insufficient_funds' }
  // Sorteios espalhados (sequência áurea): com 80% de chance, no máximo uma em cada cinco tentativas falha
  const sorteio = (semente(cliente.nome) + numero * 0.6180339887) % 1
  if (sorteio < v.chance_pagar) return { pagou: true, causa: null }
  return { pagou: false, causa: 'processing_error' }
}

/* ---------- as 3 mensagens ---------- */

function primeiroNome(nome: string) {
  return nome.trim().split(/\s+/)[0]
}

function sugestoesPara(cliente: ClienteFicticio, causa: CausaFalha, chance: number): Sugestao[] {
  const n = primeiroNome(cliente.nome)
  const v = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(cliente.mensalidade)
  const recomendada: Abordagem = causa === 'authorization_revoked' ? 'facilitacao' : chance >= 0.55 ? 'lembrete_cordial' : 'urgencia_com_respeito'
  const canal = 'whatsapp' as const
  const motivo = 'Canal padrão da demonstração (o cliente fictício não tem contato real)'
  return [
    {
      abordagem: 'lembrete_cordial',
      texto: `Oi, ${n}! A mensalidade de ${v} não pôde ser debitada este mês. Sem pressa: quando puder, regularize por este link e seguimos normalmente.`,
      canal,
      motivo_canal: motivo,
      recomendada: recomendada === 'lembrete_cordial',
      escolhida: false,
    },
    {
      abordagem: 'facilitacao',
      texto:
        causa === 'authorization_revoked'
          ? `Oi, ${n}! Vimos que a autorização do Pix Automático foi cancelada. Se preferir, dá para pagar a mensalidade de ${v} por boleto ou Pix avulso neste link, sem mudar a assinatura.`
          : `Oi, ${n}! O débito de ${v} não passou. Se ficar mais fácil, geramos um boleto ou um Pix avulso agora mesmo, neste link, e a assinatura continua igual.`,
      canal,
      motivo_canal: motivo,
      recomendada: recomendada === 'facilitacao',
      escolhida: false,
    },
    {
      abordagem: 'urgencia_com_respeito',
      texto: `Olá, ${n}. A mensalidade de ${v} está pendente e o acesso será pausado em 5 dias. O link abaixo resolve em um minuto.`,
      canal,
      motivo_canal: motivo,
      recomendada: recomendada === 'urgencia_com_respeito',
      escolhida: false,
    },
  ]
}

/* ---------- o estado ---------- */

export function estadoVazio(): EstadoSimulacao {
  return {
    id_ciclo: null,
    cliente: null,
    id_recorrencia: '',
    hoje: AGORA.toISOString(),
    inicio: AGORA.toISOString(),
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
  }
}

const ORDEM: EtapaSimulacao[] = ['cobranca', 'tentativa_1', 'tentativa_2', 'tentativa_3', 'mensagem', 'desfecho']

function concluir(e: EstadoSimulacao, etapa: EtapaSimulacao) {
  if (!e.etapas_concluidas.includes(etapa)) e.etapas_concluidas.push(etapa)
  const prox = ORDEM[ORDEM.indexOf(etapa) + 1]
  if (prox) e.etapa_atual = prox
}

function evento(e: EstadoSimulacao, ev: EventoLinhaDoTempo) {
  e.linha_do_tempo.push(ev)
}

/** "Simular cobrança": a cobrança do dia vai ao PSP simulado; se falhar, o sistema abre o ciclo e planeja. */
export function iniciar(cliente: ClienteFicticio): EstadoSimulacao {
  const e = estadoVazio()
  e.cliente = cliente
  e.id_ciclo = 9000 + Math.floor(semente(cliente.nome) * 900)
  e.id_recorrencia = `RN_sim_${Math.floor(semente(cliente.nome + 'rec') * 9000 + 1000)}`
  e.sem_crai = semCrai(cliente)

  const cobranca = pspResponde(cliente, 0, 0)
  if (cobranca.pagou) {
    concluir(e, 'cobranca')
    e.fase = 'recuperada'
    e.etapa_atual = 'desfecho'
    e.etapas_concluidas = [...ORDEM]
    e.desfecho = { tipo: 'recuperado', via: 'tentativa', tentativa: 0, valor_liquido: cliente.mensalidade, em: e.hoje }
    e.pensando = ['A cobrança passou de primeira. Não há ciclo para abrir e a CRAI não cobra nada por isso.']
    evento(e, { em: e.hoje, tipo: 'desfecho', titulo: 'Cobrança aprovada de primeira', detalhe: 'Nada para a CRAI fazer.', tom: 'ok' })
    return e
  }

  const causa = cobranca.causa!
  e.causa = causa
  concluir(e, 'cobranca')
  const { chance, contribuicoes } = diagnostico(cliente, causa)
  e.chance_recuperar = chance
  e.contribuicoes = contribuicoes
  const est = ESTIMATIVA_SALDO[cliente.perfil]
  e.dia_provavel_saldo = somaDias(e.hoje, est.dias)

  // 3 tentativas dentro de 7 dias (regra do Pix Automático), a primeira perto do dia provável de saldo
  const d1 = Math.min(Math.max(est.dias, 1), 4)
  const dias = [d1, d1 + 2, 7]
  e.tentativas = dias.map((d, i) => ({ numero: i + 1, agendada_para: somaDias(e.hoje, d), resultado: 'agendada', causa: null }))
  e.fase = 'recusada'
  e.proxima_acao = { quando: e.tentativas[0].agendada_para, descricao: 'Tentativa 1' }
  e.pensando = [
    `Causa da falha: ${CAUSA[causa]}.`,
    `${est.texto}. Dia provável de saldo: ${dataCurta(e.dia_provavel_saldo)}.`,
    `Chance de recuperar: ${Math.round(chance * 100)}%.`,
    `Plano: 3 tentativas dentro de 7 dias, nos dias ${dias.map((d) => dataCurta(somaDias(e.hoje, d))).join(', ')}. Nenhuma mensagem antes de as três falharem.`,
  ]
  evento(e, { em: e.hoje, tipo: 'abertura', titulo: 'Cobrança falhou', detalhe: `${CAUSA[causa]}`, tom: 'danger' })
  evento(e, {
    em: e.hoje,
    tipo: 'diagnostico',
    titulo: `Diagnóstico: ${Math.round(chance * 100)}% de chance de recuperar`,
    detalhe: `Dia provável de saldo: ${dataCurta(e.dia_provavel_saldo)}. 3 tentativas agendadas: ${dias.map((d) => dataCurta(somaDias(e.hoje, d))).join(', ')}.`,
  })
  return e
}

/** Avança o relógio simulado em N dias, executando o que estiver agendado. Para na primeira ação que acontecer. */
export function avancar(estado: EstadoSimulacao, dias: number): EstadoSimulacao {
  const e: EstadoSimulacao = structuredClone(estado)
  if (!e.cliente || e.desfecho || e.fase === 'formulario') return e
  const cliente = e.cliente

  for (let passo = 0; passo < dias; passo++) {
    e.hoje = somaDias(e.hoje, 1, 9)
    const rel = diasEntre(e.inicio, e.hoje)

    // Mensagem esperando escolha: sem escolha, o prazo de 8 h manda a recomendada
    if (e.fase === 'mensagens') {
      enviarMensagem(e, e.sugestoes.find((s) => s.recomendada)!.abordagem, 'automatico', somaDias(e.inicio, rel - 1, 17))
      return e
    }

    // Resposta ao envio da mensagem: 2 dias depois
    if (e.fase === 'mensagem_enviada' && e.mensagem_enviada) {
      const dias_msg = diasEntre(e.mensagem_enviada.em, e.hoje)
      if (dias_msg >= 2) {
        const v = cliente.verdade
        const temSaldo = rel >= v.dias_ate_saldo
        const sorteio = (semente(cliente.nome) + 4 * 0.6180339887) % 1
        const pagou = temSaldo && sorteio < Math.min(0.95, v.chance_pagar + 0.2)
        if (pagou) {
          fechar(e, 'recuperado', 'mensagem', null)
        } else {
          e.fase = 'encerrada'
          e.desfecho = { tipo: 'encerrado', via: 'mensagem', tentativa: null, valor_liquido: 0, em: e.hoje }
          e.proxima_acao = null
          concluir(e, 'desfecho')
          e.pensando = [...e.pensando, 'O cliente não respondeu à mensagem. O ciclo encerra sem recuperação e fica registrado no funil.']
          evento(e, { em: e.hoje, tipo: 'desfecho', titulo: 'Encerrado sem recuperação', detalhe: 'Sem resposta em 2 dias. Nada mais é tentado.', tom: 'neutro' })
        }
        return e
      }
      continue
    }

    // Tentativa agendada para hoje?
    const t = e.tentativas.find((x) => x.resultado === 'agendada' && diasEntre(x.agendada_para, e.hoje) === 0)
    if (!t) continue

    const r = pspResponde(cliente, rel, t.numero)
    t.resultado = r.pagou ? 'paga' : 'falhou'
    t.causa = r.causa
    const etapa = `tentativa_${t.numero}` as EtapaSimulacao
    concluir(e, etapa)

    if (r.pagou) {
      fechar(e, 'recuperado', 'tentativa', t.numero)
      return e
    }

    e.causa = r.causa
    if (r.causa === 'authorization_revoked') {
      // Exceção declarada: revogação pula as tentativas restantes e vai para a mensagem
      e.tentativas.filter((x) => x.resultado === 'agendada').forEach((x) => (x.resultado = 'cancelada'))
      concluir(e, 'tentativa_3')
      e.etapa_atual = 'mensagem'
      evento(e, { em: e.hoje, tipo: 'tentativa', titulo: `Tentativa ${t.numero}: autorização revogada`, detalhe: 'O cliente cancelou o Pix Automático. As outras tentativas são canceladas.', tom: 'danger' })
      e.pensando = [...e.pensando, 'Autorização revogada: cobrar de novo não adianta. O sistema pula para a mensagem, com a abordagem de facilitação (outro meio de pagar).']
      abrirMensagens(e)
      return e
    }

    evento(e, { em: e.hoje, tipo: 'tentativa', titulo: `Tentativa ${t.numero} falhou`, detalhe: CAUSA[r.causa!], tom: 'danger' })
    const prox = e.tentativas.find((x) => x.resultado === 'agendada')
    if (prox) {
      e.fase = 'recusada'
      e.proxima_acao = { quando: prox.agendada_para, descricao: `Tentativa ${prox.numero}` }
      e.pensando = [...e.pensando, `Tentativa ${t.numero} não passou (${CAUSA[r.causa!]}). Próxima: ${dataCurta(prox.agendada_para)}. Ainda sem mensagem.`]
    } else {
      e.pensando = [...e.pensando, 'As 3 tentativas falharam. Agora sim: 3 mensagens escritas para este cliente, e a empresa escolhe (ou o sistema manda a recomendada em 8 h).']
      abrirMensagens(e)
    }
    return e
  }
  return e
}

function abrirMensagens(e: EstadoSimulacao) {
  e.sugestoes = sugestoesPara(e.cliente!, e.causa!, e.chance_recuperar ?? 0.5)
  e.fase = 'mensagens'
  e.proxima_acao = { quando: somaDias(e.hoje, 0, 17), descricao: 'Envio automático da recomendada' }
  evento(e, { em: e.hoje, tipo: 'sugestoes', titulo: '3 mensagens sugeridas', detalhe: `Recomendada: ${ABORDAGEM[e.sugestoes.find((s) => s.recomendada)!.abordagem]}, por WhatsApp.` })
}

export function escolherMensagem(estado: EstadoSimulacao, abordagem: Abordagem, por: 'owner' | 'admin'): EstadoSimulacao {
  const e: EstadoSimulacao = structuredClone(estado)
  enviarMensagem(e, abordagem, por)
  return e
}

function enviarMensagem(e: EstadoSimulacao, abordagem: Abordagem, por: 'owner' | 'admin' | 'automatico', em?: string) {
  if (e.fase !== 'mensagens') return
  const s = e.sugestoes.find((x) => x.abordagem === abordagem)!
  s.escolhida = true
  const quando = em ?? e.hoje
  e.mensagem_enviada = { abordagem, canal: s.canal, em: quando, escolhida_por: por }
  e.fase = 'mensagem_enviada'
  concluir(e, 'mensagem')
  e.proxima_acao = { quando: somaDias(quando, 2, 9), descricao: 'Prazo para resposta do cliente' }
  evento(e, {
    em: quando,
    tipo: 'mensagem',
    titulo: `Mensagem enviada: ${ABORDAGEM[abordagem]}`,
    detalhe: por === 'automatico' ? 'Sem escolha no prazo de 8 h, o sistema enviou a recomendada.' : 'Escolhida por você. A decisão fica na trilha com revisão humana.',
    tom: 'ok',
  })
  e.pensando = [...e.pensando, por === 'automatico' ? 'Prazo de 8 h venceu sem escolha: a recomendada foi enviada por WhatsApp.' : 'Mensagem escolhida por uma pessoa e enviada por WhatsApp. Agora o sistema espera 2 dias pela resposta.']
}

function fechar(e: EstadoSimulacao, tipo: 'recuperado', via: 'tentativa' | 'mensagem', tentativa: number | null) {
  const bruto = e.cliente!.mensalidade
  const liquido = centavos(bruto * (1 - TAXA.involuntario))
  e.fase = 'recuperada'
  e.desfecho = { tipo, via, tentativa, valor_liquido: liquido, em: e.hoje }
  e.proxima_acao = null
  e.etapas_concluidas = [...ORDEM]
  e.etapa_atual = 'desfecho'
  const como = via === 'tentativa' ? `na ${tentativa}ª tentativa` : 'pelo link da mensagem'
  e.pensando = [...e.pensando, `Pagamento recuperado ${como}. O ciclo fecha e o valor entra no extrato.`]
  evento(e, { em: e.hoje, tipo: 'desfecho', titulo: `Pagamento recuperado ${como}`, detalhe: `Líquido para a empresa: ${new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(liquido)}.`, tom: 'ok' })
}

/** Quantos dias até a próxima ação agendada (para "Avançar até a próxima ação"). */
export function diasAteProximaAcao(e: EstadoSimulacao): number {
  if (!e.proxima_acao) return 0
  if (e.fase === 'mensagens') return 1
  return Math.max(1, diasEntre(e.hoje, e.proxima_acao.quando))
}

export const CAUSA: Record<CausaFalha, string> = {
  insufficient_funds: 'Saldo insuficiente',
  limit_exceeded: 'Limite do Pix excedido',
  authorization_revoked: 'Autorização revogada',
  processing_error: 'Erro no processamento',
  generic_decline: 'Recusa sem motivo informado',
}

export const ABORDAGEM: Record<Abordagem, string> = {
  lembrete_cordial: 'Lembrete cordial',
  facilitacao: 'Facilitação',
  urgencia_com_respeito: 'Urgência com respeito',
}

/* ---------- voluntário: cliente fictício em risco ---------- */

/** Na demonstração, "mensalidade alta" é a partir daqui; no backend, é o corte da própria base. */
export const MRR_ALTO = 3000

export const OFERTA: Record<OfertaRetencao, string> = {
  desconto: 'Desconto de 20% por 3 meses',
  suporte: 'Suporte dedicado por 30 dias',
  plano_leve: 'Plano mais leve, sem multa',
}

export function simularRetencao(c: ClienteRiscoFicticio): ResultadoRetencaoSimulada {
  const sinais = [c.sinais.uso_caiu, c.sinais.tickets, c.sinais.atraso].filter(Boolean).length
  // Regra das faixas: Grave = topo 10% da base pelo risco, Preocupante = 20% seguintes, sempre com
  // sinal real. Cliente de mensalidade alta entra como Preocupante e só sobe para Grave depois.
  const mrrAlto = c.mrr >= MRR_ALTO
  const faixa = sinais >= 2 ? (mrrAlto ? 'preocupante' : 'grave') : sinais === 1 ? 'preocupante' : 'sem_risco'
  const motivos: string[] = []
  if (c.sinais.uso_caiu) motivos.push('o uso caiu pela metade em 2 semanas')
  if (c.sinais.tickets) motivos.push('abriu 3 chamados de suporte no mês')
  if (c.sinais.atraso) motivos.push('pagou atrasado 2 vezes')
  let motivo = motivos.length ? motivos.join(', ').replace(/^./, (x) => x.toUpperCase()) + '.' : 'Nenhum sinal de risco nos dados de comportamento.'
  if (sinais >= 2 && mrrAlto) motivo += ' Mensalidade alta: entra como Preocupante e só sobe para Grave se os sinais continuarem.'

  // O que o sistema decide (sem ver a propensão): pela combinação de sinais e valor
  const oferta: OfertaRetencao = c.sinais.tickets ? 'suporte' : c.sinais.uso_caiu && c.mrr >= 2000 ? 'plano_leve' : 'desconto'
  const porque = {
    suporte: 'Chamados abertos indicam problema de uso, não de preço: suporte dedicado costuma reter mais nesse caso.',
    plano_leve: 'Uso em queda com mensalidade alta: um plano mais leve segura o cliente sem descontar em cima de um valor grande.',
    desconto: 'Para este perfil, o desconto curto é a oferta que mais aceita nos últimos 30 dias.',
  }[oferta]

  const sorteio = semente(`${c.nome}:oferta:${oferta}`)
  const aceitou = faixa !== 'sem_risco' && sorteio < c.propensao[oferta]
  const base = oferta === 'desconto' ? c.mrr * 0.8 : oferta === 'plano_leve' ? c.mrr * 0.6 : c.mrr
  const valor = aceitou ? centavos(base * (1 - TAXA.voluntario)) : 0

  return {
    faixa,
    motivo,
    oferta,
    canal: 'whatsapp',
    porque,
    aceitou,
    valor_mantido_liquido: valor,
    sem_crai:
      faixa === 'sem_risco'
        ? 'Sem sinais, nada muda: o cliente segue normalmente.'
        : 'Sem a CRAI, ninguém perceberia os sinais até o pedido de cancelamento, quando já é tarde para oferecer algo.',
  }
}

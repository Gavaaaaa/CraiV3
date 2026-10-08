/**
 * Motor da simulação de demonstração. Roda no navegador enquanto o backend não tem o
 * simulador da Etapa 3 (pagador com verdade escondida, PSP simulado, relógio por empresa).
 * Na integração, `api.ts` troca estas funções por chamadas às rotas e o estado devolvido
 * tem a mesma forma (`EstadoSimulacao`), então a tela não muda.
 *
 * Regra de honestidade: os modelos "veem" só o que está em `pensando` e nos campos do
 * ciclo. A `verdade` do cliente fictício só é lida pelo PSP simulado, aqui embaixo.
 */
import { t, localeAtual } from '../lib/idioma'
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
const dataCurta = (iso: string) => new Date(iso).toLocaleDateString(localeAtual(), { day: '2-digit', month: '2-digit' })
const centavos = (v: number) => Math.round(v * 100) / 100

/* ---------- o que o sistema estima (sem ver a verdade) ---------- */

/** Dia provável de saldo por perfil, em dias a partir da cobrança. É a estimativa do sistema, não a verdade. */
const ESTIMATIVA_SALDO: Record<ClienteFicticio['perfil'], { dias: number; texto: string }> = {
  clt: { dias: 5, texto: t('Perfil CLT: o salário costuma entrar até o 5º dia útil') },
  pj: { dias: 4, texto: t('Perfil PJ: o caixa costuma ter entrada na primeira semana') },
  freelancer: { dias: 2, texto: t('Perfil freelancer: entradas irregulares; o sistema tenta cedo e espalha as tentativas') },
}

function diagnostico(cliente: ClienteFicticio, causa: CausaFalha): { chance: number; contribuicoes: Contribuicao[] } {
  const c: Contribuicao[] = []
  let pontos = 50
  if (causa === 'insufficient_funds') {
    c.push({ fator: t('Causa: saldo insuficiente (passa quando o dinheiro entra)'), pontos: 14 })
    pontos += 14
  } else if (causa === 'processing_error') {
    c.push({ fator: t('Causa: erro no processamento (costuma passar na próxima)'), pontos: 20 })
    pontos += 20
  } else if (causa === 'authorization_revoked') {
    c.push({ fator: t('Causa: autorização revogada (só a mensagem resolve)'), pontos: -18 })
    pontos -= 18
  }
  const perfil = { clt: 8, pj: 4, freelancer: -5 }[cliente.perfil]
  c.push({ fator: { clt: t('Perfil CLT: renda previsível'), pj: t('Perfil PJ: caixa razoavelmente previsível'), freelancer: t('Perfil freelancer: renda irregular') }[cliente.perfil], pontos: perfil })
  pontos += perfil
  if (cliente.mensalidade > 2000) {
    c.push({ fator: t('Valor alto para o perfil'), pontos: -7 })
    pontos -= 7
  } else if (cliente.mensalidade < 500) {
    c.push({ fator: t('Valor baixo: fácil de regularizar'), pontos: 5 })
    pontos += 5
  }
  c.push({ fator: t('Cliente fictício: sem histórico de pagamento'), pontos: -3 })
  pontos -= 3
  return { chance: Math.min(0.95, Math.max(0.05, pontos / 100)), contribuicoes: c }
}

/** Sem a CRAI: o banco do pagador só tenta nas duas janelas do dia do vencimento (regra do Pix Automático). */
function semCrai(cliente: ClienteFicticio): EstadoSimulacao['sem_crai'] {
  const v = cliente.verdade
  if (v.vai_revogar) {
    return { resultado: 'perdido', explicacao: t('O cliente revoga a autorização. Sem a CRAI, ninguém fala com ele e a assinatura acaba.') }
  }
  if (v.dias_ate_saldo === 0 && v.chance_pagar >= 0.5) {
    return { resultado: 'recuperado', explicacao: t('O dinheiro já está na conta. O próprio banco resolveria na segunda janela do dia do vencimento.') }
  }
  return {
    resultado: 'perdido',
    explicacao:
      v.dias_ate_saldo === 1
        ? t('Sem a CRAI, o banco só tenta de novo no dia do vencimento (duas janelas). O dinheiro entra em {n} dia, então a cobrança se perde e a empresa precisa correr atrás por conta própria.', { n: v.dias_ate_saldo })
        : t('Sem a CRAI, o banco só tenta de novo no dia do vencimento (duas janelas). O dinheiro entra em {n} dias, então a cobrança se perde e a empresa precisa correr atrás por conta própria.', { n: v.dias_ate_saldo }),
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
  const motivo = t('Canal padrão da demonstração (o cliente fictício não tem contato real)')
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
    modo_mensagem: 'escolha',
    prazo_escolha_horas: 8,
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
    e.pensando = [t('A cobrança passou de primeira. Não há ciclo para abrir e a CRAI não cobra nada por isso.')]
    evento(e, { em: e.hoje, tipo: 'desfecho', titulo: t('Cobrança aprovada de primeira'), detalhe: t('Nada para a CRAI fazer.'), tom: 'ok' })
    return e
  }

  const causa = cobranca.causa!
  e.causa = causa
  concluir(e, 'cobranca')
  const { chance, contribuicoes } = diagnostico(cliente, causa)
  e.chance_recuperar = chance
  e.contribuicoes = contribuicoes.map((c) => ({ fator: c.fator, efeito: c.pontos >= 0 ? 'Aumentou a chance de recuperar' : 'Reduziu a chance de recuperar' }))
  const est = ESTIMATIVA_SALDO[cliente.perfil]
  e.dia_provavel_saldo = somaDias(e.hoje, est.dias)

  // 3 tentativas dentro de 7 dias (regra do Pix Automático), a primeira perto do dia provável de saldo
  const d1 = Math.min(Math.max(est.dias, 1), 4)
  const dias = [d1, d1 + 2, 7]
  e.tentativas = dias.map((d, i) => ({ numero: i + 1, agendada_para: somaDias(e.hoje, d), resultado: 'agendada', causa: null }))
  e.fase = 'recusada'
  e.proxima_acao = { quando: e.tentativas[0].agendada_para, descricao: t('Tentativa {n}', { n: 1 }) }
  e.pensando = [
    t('Causa da falha: {causa}.', { causa: CAUSA[causa] }),
    t('{estimativa}. Dia provável de saldo: {data}.', { estimativa: est.texto, data: dataCurta(e.dia_provavel_saldo) }),
    t('Chance de recuperar: {pct}%.', { pct: Math.round(chance * 100) }),
    t('Plano: 3 tentativas dentro de 7 dias, nos dias {dias}. Nenhuma mensagem antes de as três falharem.', { dias: dias.map((d) => dataCurta(somaDias(e.hoje, d))).join(', ') }),
  ]
  evento(e, { em: e.hoje, tipo: 'abertura', titulo: t('Cobrança falhou'), detalhe: `${CAUSA[causa]}`, tom: 'danger' })
  evento(e, {
    em: e.hoje,
    tipo: 'diagnostico',
    titulo: t('Diagnóstico: {pct}% de chance de recuperar', { pct: Math.round(chance * 100) }),
    detalhe: t('Dia provável de saldo: {data}. 3 tentativas agendadas: {dias}.', { data: dataCurta(e.dia_provavel_saldo), dias: dias.map((d) => dataCurta(somaDias(e.hoje, d))).join(', ') }),
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
          e.pensando = [...e.pensando, t('O cliente não respondeu à mensagem. O ciclo encerra sem recuperação e fica registrado no funil.')]
          evento(e, { em: e.hoje, tipo: 'desfecho', titulo: t('Encerrado sem recuperação'), detalhe: t('Sem resposta em 2 dias. Nada mais é tentado.'), tom: 'neutro' })
        }
        return e
      }
      continue
    }

    // Tentativa agendada para hoje?
    const tent = e.tentativas.find((x) => x.resultado === 'agendada' && diasEntre(x.agendada_para, e.hoje) === 0)
    if (!tent) continue

    const r = pspResponde(cliente, rel, tent.numero)
    tent.resultado = r.pagou ? 'paga' : 'falhou'
    tent.causa = r.causa
    const etapa = `tentativa_${tent.numero}` as EtapaSimulacao
    concluir(e, etapa)

    if (r.pagou) {
      fechar(e, 'recuperado', 'tentativa', tent.numero)
      return e
    }

    e.causa = r.causa
    if (r.causa === 'authorization_revoked') {
      // Exceção declarada: revogação pula as tentativas restantes e vai para a mensagem
      e.tentativas.filter((x) => x.resultado === 'agendada').forEach((x) => (x.resultado = 'cancelada'))
      concluir(e, 'tentativa_3')
      e.etapa_atual = 'mensagem'
      evento(e, { em: e.hoje, tipo: 'tentativa', titulo: t('Tentativa {n}: autorização revogada', { n: tent.numero }), detalhe: t('O cliente cancelou o Pix Automático. As outras tentativas são canceladas.'), tom: 'danger' })
      e.pensando = [...e.pensando, t('Autorização revogada: cobrar de novo não adianta. O sistema pula para a mensagem, com a abordagem de facilitação (outro meio de pagar).')]
      abrirMensagens(e)
      return e
    }

    evento(e, { em: e.hoje, tipo: 'tentativa', titulo: t('Tentativa {n} falhou', { n: tent.numero }), detalhe: CAUSA[r.causa!], tom: 'danger' })
    const prox = e.tentativas.find((x) => x.resultado === 'agendada')
    if (prox) {
      e.fase = 'recusada'
      e.proxima_acao = { quando: prox.agendada_para, descricao: t('Tentativa {n}', { n: prox.numero }) }
      e.pensando = [...e.pensando, t('Tentativa {n} não passou ({causa}). Próxima: {data}. Ainda sem mensagem.', { n: tent.numero, causa: CAUSA[r.causa!], data: dataCurta(prox.agendada_para) })]
    } else {
      e.pensando = [...e.pensando, t('As 3 tentativas falharam. Agora sim: 3 mensagens escritas para este cliente, e a empresa escolhe (ou o sistema manda a recomendada em 8 h).')]
      abrirMensagens(e)
    }
    return e
  }
  return e
}

function abrirMensagens(e: EstadoSimulacao) {
  e.sugestoes = sugestoesPara(e.cliente!, e.causa!, e.chance_recuperar ?? 0.5)
  e.fase = 'mensagens'
  e.proxima_acao = { quando: somaDias(e.hoje, 0, 17), descricao: t('Envio automático da recomendada') }
  evento(e, { em: e.hoje, tipo: 'sugestoes', titulo: t('3 mensagens sugeridas'), detalhe: t('Recomendada: {abordagem}, por WhatsApp.', { abordagem: ABORDAGEM[e.sugestoes.find((s) => s.recomendada)!.abordagem] }) })
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
  e.proxima_acao = { quando: somaDias(quando, 2, 9), descricao: t('Prazo para resposta do cliente') }
  evento(e, {
    em: quando,
    tipo: 'mensagem',
    titulo: t('Mensagem enviada: {abordagem}', { abordagem: ABORDAGEM[abordagem] }),
    detalhe: por === 'automatico' ? t('Sem escolha no prazo de 8 h, o sistema enviou a recomendada.') : t('Escolhida por você. A decisão fica na trilha com revisão humana.'),
    tom: 'ok',
  })
  e.pensando = [...e.pensando, por === 'automatico' ? t('Prazo de 8 h venceu sem escolha: a recomendada foi enviada por WhatsApp.') : t('Mensagem escolhida por uma pessoa e enviada por WhatsApp. Agora o sistema espera 2 dias pela resposta.')]
}

function fechar(e: EstadoSimulacao, tipo: 'recuperado', via: 'tentativa' | 'mensagem', tentativa: number | null) {
  const bruto = e.cliente!.mensalidade
  const liquido = centavos(bruto * (1 - TAXA.involuntario))
  e.fase = 'recuperada'
  e.desfecho = { tipo, via, tentativa, valor_liquido: liquido, em: e.hoje }
  e.proxima_acao = null
  e.etapas_concluidas = [...ORDEM]
  e.etapa_atual = 'desfecho'
  const titulo = via === 'tentativa' ? t('Pagamento recuperado na {n}ª tentativa', { n: tentativa ?? '' }) : t('Pagamento recuperado pelo link da mensagem')
  e.pensando = [
    ...e.pensando,
    via === 'tentativa'
      ? t('Pagamento recuperado na {n}ª tentativa. O ciclo fecha e o valor entra no extrato.', { n: tentativa ?? '' })
      : t('Pagamento recuperado pelo link da mensagem. O ciclo fecha e o valor entra no extrato.'),
  ]
  evento(e, { em: e.hoje, tipo: 'desfecho', titulo, detalhe: t('Líquido para a empresa: {valor}.', { valor: new Intl.NumberFormat(localeAtual(), { style: 'currency', currency: 'BRL' }).format(liquido) }), tom: 'ok' })
}

/** Quantos dias até a próxima ação agendada (para "Avançar até a próxima ação"). */
export function diasAteProximaAcao(e: EstadoSimulacao): number {
  if (!e.proxima_acao) return 0
  if (e.fase === 'mensagens') return 1
  return Math.max(1, diasEntre(e.hoje, e.proxima_acao.quando))
}

export const CAUSA: Record<CausaFalha, string> = {
  insufficient_funds: t('Saldo insuficiente'),
  limit_exceeded: t('Limite do Pix excedido'),
  authorization_revoked: t('Autorização revogada'),
  processing_error: t('Erro no processamento'),
  generic_decline: t('Recusa sem motivo informado'),
}

export const ABORDAGEM: Record<Abordagem, string> = {
  lembrete_cordial: t('Lembrete cordial'),
  facilitacao: t('Facilitação'),
  urgencia_com_respeito: t('Urgência com respeito'),
}

/* ---------- voluntário: cliente fictício em risco ---------- */

/** Na demonstração, "mensalidade alta" é a partir daqui; no backend, é o corte da própria base. */
export const MRR_ALTO = 3000

export const OFERTA: Record<OfertaRetencao, string> = {
  desconto_10: t('Desconto de 10% por 3 meses'),
  desconto_20: t('Desconto de 20% por 3 meses'),
  pausa_1_mes: t('Pausa de 1 mês na assinatura, sem custo'),
  pix_boleto_flash: t('Troca para Pix ou boleto em 1 clique'),
}

/** Quanto do MRR o desconto concedido tira, com 1 mês contado (a mesma conta do backend). */
const DESCONTO_CONCEDIDO: Record<OfertaRetencao, number> = { desconto_10: 0.1, desconto_20: 0.2, pausa_1_mes: 1, pix_boleto_flash: 0 }

/** A retenção simulada da DEMONSTRAÇÃO (sem backend). No modo real, quem decide é o sistema de verdade. */
export function simularRetencao(c: ClienteRiscoFicticio): ResultadoRetencaoSimulada {
  const sinais = [c.sinais.uso_caiu, c.sinais.tickets, c.sinais.atraso, c.sinais.abriu_cancelamento].filter(Boolean).length
  // Regra das faixas: Grave = topo 10% da base pelo risco, Preocupante = 20% seguintes, sempre com
  // sinal real. Cliente de mensalidade alta entra como Preocupante e só sobe para Grave depois.
  const mrrAlto = c.mrr >= MRR_ALTO
  const faixa = c.sinais.abriu_cancelamento || (sinais >= 2 && !mrrAlto) ? 'grave' : sinais >= 1 ? 'preocupante' : 'sem_risco'
  const motivos: string[] = []
  if (c.sinais.abriu_cancelamento) motivos.push(t('abriu a página de cancelamento'))
  if (c.sinais.uso_caiu) motivos.push(t('sem entrar há 24 dias, usando 1 funcionalidade'))
  if (c.sinais.tickets) motivos.push(t('abriu 3 chamados de suporte no mês'))
  if (c.sinais.atraso) motivos.push(t('teve 2 pagamentos com falha em 90 dias'))
  let motivo = motivos.length ? motivos.join(', ').replace(/^./, (x) => x.toUpperCase()) + '.' : t('Nenhum sinal de risco nos dados de comportamento.')
  if (faixa === 'preocupante' && sinais >= 2 && mrrAlto) motivo += ' ' + t('Mensalidade alta: entra como Preocupante e só sobe para Grave se os sinais continuarem.')

  const risco = faixa === 'grave' ? 0.9 : faixa === 'preocupante' ? 0.8 : 0.1
  const base = { faixa, motivo, decidido_por: 'regua', risco, corte_de_intervencao: 0.6, sem_oferta_porque: null, meses_de_mrr: 1, prazo_estorno_dias: 30 } as const
  if (faixa === 'sem_risco') {
    return { ...base, oferta: null, oferta_legivel: null, canal_legivel: null, porque: null, aceitou: null, valor_mantido_liquido: 0, sem_crai: t('O sistema não interveio: com ou sem a CRAI, este cliente segue como está.') }
  }

  // O que o sistema decide (sem ver a propensão): pela combinação de sinais e valor
  const oferta: OfertaRetencao = c.sinais.atraso ? 'pix_boleto_flash' : c.sinais.uso_caiu && c.mrr >= 2000 ? 'pausa_1_mes' : c.sinais.tickets ? 'desconto_20' : 'desconto_10'
  const porque = {
    pix_boleto_flash: t('Pagamentos atrasados indicam atrito na cobrança, não no produto: trocar o meio de pagamento resolve sem dar desconto.'),
    pausa_1_mes: t('Uso em queda com mensalidade alta: uma pausa segura o cliente sem descontar em cima de um valor grande.'),
    desconto_20: t('Chamados abertos indicam insatisfação: o desconto maior é a oferta mais aceita nesse caso.'),
    desconto_10: t('Para este perfil, o desconto curto é a oferta mais aceita nos últimos 30 dias.'),
  }[oferta]

  const aceitou = semente(`${c.nome}:oferta:${oferta}`) < c.propensao[oferta]
  const valor = aceitou ? centavos(c.mrr * (1 - DESCONTO_CONCEDIDO[oferta]) * (1 - TAXA.voluntario)) : 0

  return {
    ...base,
    oferta,
    oferta_legivel: OFERTA[oferta],
    canal_legivel: 'WhatsApp',
    porque,
    aceitou,
    valor_mantido_liquido: valor,
    sem_crai: t('Sem a CRAI, ninguém perceberia os sinais até o pedido de cancelamento, quando já é tarde para oferecer algo.'),
  }
}

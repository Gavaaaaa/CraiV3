import { backend } from '../i18n/en/backend'
import { idiomaAtual } from './idioma'

/**
 * As frases que o BACKEND manda prontas, no idioma do painel.
 *
 * O backend escreve em português (mudar isso lá exige o portão de testes do dono). Aqui, só as
 * frases de VOCABULÁRIO FIXO viram inglês: os rótulos (causa, oferta, canal, etapa do funil), os
 * fatores da explicação do classificador, a próxima ação, as frases de modelo da atividade, o
 * motivo do risco voluntário, as linhas da simulação e o texto fixo do assistente.
 *
 * - Em português, o texto passa como está, sempre.
 * - Em inglês: primeiro o dicionário (`i18n/en/backend.ts`), depois as regras abaixo, na ordem.
 *   Valores em reais e números passam como vieram; datas dd/mm viram mm/dd, como no resto do
 *   inglês do painel.
 * - Texto que nada reconhece (a resposta do LLM, uma frase nova do backend) passa como está:
 *   nunca sai meio traduzido.
 *
 * NÃO passa por aqui: o `efeito` das contribuições (a tela já traduz com `t()`), o texto das
 * mensagens aos clientes finais, o registro de decisões do Art. 20 ("Em dd/mm/aaaa, no fluxo
 * de ..."), o texto para a política de privacidade e os nomes.
 */
export function doBackend(texto: string): string
export function doBackend(texto: string | null): string | null
export function doBackend(texto: string | null | undefined): string | null | undefined
export function doBackend(texto: string | null | undefined): string | null | undefined {
  if (!texto || idiomaAtual() !== 'en') return texto
  return traduzir(texto) ?? texto
}

/** A tradução, ou null se o texto não é uma frase conhecida. */
function traduzir(texto: string): string | null {
  const exata = doDicionario(texto)
  if (exata !== null) return exata
  for (const regra of REGRAS) {
    const m = regra.padrao.exec(texto)
    if (!m) continue
    const saida = regra.montar(...m.slice(1).map((g) => g ?? ''))
    if (saida !== null) return comMaiuscula(texto, saida)
  }
  return comMaiuscula(texto, porPedacos(texto))
}

/* ------------------------------------------------------------------ */
/* Ajudas                                                               */
/* ------------------------------------------------------------------ */

const maiusculaInicial = (s: string) => /^\p{Lu}/u.test(s)
const primeiraMinuscula = (s: string) => s.charAt(0).toLowerCase() + s.slice(1)
const primeiraMaiuscula = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

/** A tradução começa com maiúscula quando o português começa. */
function comMaiuscula(original: string, traducao: string | null): string | null {
  if (traducao === null) return null
  return maiusculaInicial(original) ? primeiraMaiuscula(traducao) : traducao
}

/**
 * Do dicionário, com ou sem a primeira letra maiúscula (o backend manda "Saldo insuficiente"
 * num lugar e "saldo insuficiente" no meio de outra frase). Null se não está lá.
 */
function doDicionario(texto: string): string | null {
  if (texto in backend) return backend[texto]
  const minuscula = primeiraMinuscula(texto)
  if (minuscula !== texto && minuscula in backend) return primeiraMaiuscula(backend[minuscula])
  return null
}

/** Do dicionário, ou o texto como veio (para códigos, como um perfil "CLT" ou uma bandeira). */
const doDicionarioOuComoVeio = (texto: string) => doDicionario(texto) ?? texto

/** "1" é singular em inglês; o resto, plural. O número passa como veio. */
const n = (numero: string, um: string, varios: string) => `${numero} ${numero === '1' ? um : varios}`

/** "09/10" → "10/09"; "09/10/2026" → "10/09/2026". */
const data = (ddmm: string) => ddmm.replace(/^(\d{2})\/(\d{2})(\/\d{4})?$/, (_, d: string, m: string, a?: string) => `${m}/${d}${a ?? ''}`)

/** "09/10, 10/10 e 11/10" → "10/09, 10/10 and 10/11". */
function listaDeDatas(lista: string): string | null {
  const itens = lista.split(/, | e /)
  if (!itens.every((i) => /^\d{2}\/\d{2}$/.test(i))) return null
  const em = itens.map(data)
  return em.length === 1 ? em[0] : `${em.slice(0, -1).join(', ')} and ${em[em.length - 1]}`
}

/** Quem aparece na atividade: um nome da base (passa como veio) ou um dos textos fixos. */
function quem(texto: string): string {
  if (/^[Uu]m cliente sem cadastro$/.test(texto)) return 'an unregistered customer'
  const id = /^[Cc]liente (\S+)$/.exec(texto)
  return id ? `customer ${id[1]}` : texto
}

const PERFIL: Record<string, string> = { CLT: 'salaried (CLT)', PJ: 'business (PJ)', freelancer: 'freelancer' }

/** Um número como o backend escreve ("12", "2,5", "1.234,56"), sem levar a pontuação que vem depois. */
const NUMERO = '(-?\\d(?:[\\d.,]*\\d)?)'
const REAIS = '(R\\$ ?-?\\d(?:[\\d.,]*\\d)?)'

interface Regra {
  padrao: RegExp
  /** O inglês, a partir dos grupos; null quando uma parte de vocabulário fixo não é conhecida. */
  montar: (...g: string[]) => string | null
}

/**
 * Uma regra. O padrão casa a frase INTEIRA; a primeira letra vale maiúscula ou minúscula (o
 * painel põe maiúscula no começo de todo texto).
 */
function regra(fonte: string, montar: Regra['montar']): Regra {
  const inicio = fonte.charAt(0)
  const corpo = /\p{L}/u.test(inicio) ? `[${inicio.toLowerCase()}${inicio.toUpperCase()}]${fonte.slice(1)}` : fonte
  return { padrao: new RegExp(`^${corpo}$`, 'u'), montar }
}

/* ------------------------------------------------------------------ */
/* As regras, na ordem                                                  */
/* ------------------------------------------------------------------ */

const REGRAS: Regra[] = [
  /* Fatores da explicação do classificador (retention_log.py ROTULOS_DE_FEATURE) */
  regra(`${NUMERO} meses como cliente`, (x) => `${n(x, 'month', 'months')} as a customer`),
  regra(`${NUMERO} dias como cliente`, (x) => `${n(x, 'day', 'days')} as a customer`),
  regra(`histórico de pagamento ${NUMERO}% positivo`, (x) => `payment history ${x}% positive`),
  regra(`cobrança de ${REAIS}`, (v) => `charge of ${v}`),
  regra(`${NUMERO} falhas de pagamento nos últimos 90 dias`, (x) => `${n(x, 'payment failure', 'payment failures')} in the last 90 days`),
  regra('causa da falha de pagamento: (.+)', (c) => (doDicionario(c) ? `payment failure cause: ${doDicionario(c)}` : null)),
  regra(`${NUMERO} dias sem acesso ao produto`, (x) => `${n(x, 'day', 'days')} without accessing the product`),
  regra(`${NUMERO} funcionalidades usadas nos últimos 30 dias`, (x) => `${n(x, 'feature', 'features')} used in the last 30 days`),
  regra(`${NUMERO} assentos contratados`, (x) => `${n(x, 'seat', 'seats')} contracted`),
  regra(`${NUMERO} acessos nos últimos (7|30) dias`, (x, d) => `${n(x, 'login', 'logins')} in the last ${d} days`),
  regra(`sessões de ${NUMERO} minutos em média`, (x) => `average session of ${x} minutes`),
  regra(`${NUMERO} chamadas de integração \\(API\\) nos últimos 7 dias`, (x) => `${n(x, 'integration (API) call', 'integration (API) calls')} in the last 7 days`),
  regra(`${NUMERO} chamados de suporte nos últimos 30 dias`, (x) => `${n(x, 'support ticket', 'support tickets')} in the last 30 days`),
  regra(`${NUMERO} pagamentos que falharam nos últimos 90 dias`, (x) => `${n(x, 'failed payment', 'failed payments')} in the last 90 days`),
  regra(`última nota de satisfação \\(NPS\\): ${NUMERO}`, (x) => `latest satisfaction score (NPS): ${x}`),
  regra(`mensalidade de ${REAIS}`, (v) => `subscription fee of ${v}`),
  regra('perfil de cobrança (\\S+)', (p) => `billing profile ${p}`),
  regra("evento '([^']+)'", (e) => `event '${e}'`),
  regra('canal em que já converteu antes: (\\S+)', (c) => `channel that converted before: ${doDicionarioOuComoVeio(c)}`),
  regra('criticidade (\\S+)', (c) => `criticality ${c}`),
  regra(`pontuação de risco ${NUMERO}`, (x) => `risk score ${x}`),
  regra(`pontuação de recuperação ${NUMERO}/100`, (x) => `recovery score ${x}/100`),
  regra(`probabilidade de recuperação ${NUMERO}%`, (x) => `recovery probability ${x}%`),
  regra(`retorno esperado da intervenção ${REAIS}`, (v) => `expected return from intervening ${v}`),
  regra('meio de pagamento (Pix Automático|\\S+)', (m) => `payment method ${doDicionarioOuComoVeio(m)}`),
  regra(`${NUMERO} tentativas de cobrança já usadas`, (x) => `${n(x, 'charge attempt', 'charge attempts')} already used`),
  regra(`limite de ${NUMERO} tentativas na janela`, (x) => `limit of ${n(x, 'attempt', 'attempts')} in the window`),
  regra(`${NUMERO}ª tentativa da cobrança`, (x) => `charge attempt ${x}`),
  regra('bandeira (\\S+)', (b) => `card brand ${b}`),
  regra(`valor estimado do cliente ${REAIS}`, (v) => `estimated customer value ${v}`),
  regra(`${NUMERO} pagamentos bem-sucedidos`, (x) => `${n(x, 'successful payment', 'successful payments')}`),
  regra(`${NUMERO} dias desde o cadastro`, (x) => `${n(x, 'day', 'days')} since sign-up`),
  regra(`cobrança às ${NUMERO}h`, (x) => `charge at ${x}:00`),
  regra(`cobrança no dia ${NUMERO} do mês`, (x) => `charge on day ${x} of the month`),
  regra('cobrança num\\(a\\) (.+)', (d) => (doDicionario(d) ? `charge on a ${doDicionario(d)}` : null)),
  regra(`gasto médio de ${REAIS}`, (v) => `average spend of ${v}`),
  regra('vencimento em (\\d{2}/\\d{2}/\\d{4})', (d) => `due on ${data(d)}`),
  regra('canal (.+)', (c) => (doDicionario(c) ?? (/^(WhatsApp|SMS)$/.test(c) ? c : null)) && `channel ${doDicionarioOuComoVeio(c)}`),

  /* Próxima ação e funil (api/ciclos.py, api/simulacao.py, visao_geral.py ETAPAS_DO_FUNIL) */
  regra(`tentativa ${NUMERO} de cobrança`, (x) => `charge attempt ${x}`),
  regra(`tentativa ${NUMERO}`, (x) => `Attempt ${x}`),

  /* Simulação: "O que o sistema está pensando" (api/simulacao.py _pensando) */
  regra('causa da falha: (.+)\\.', (c) => (doDicionario(c) ? `failure cause: ${doDicionario(c)}.` : null)),
  regra(`chance de recuperar: ${NUMERO}%\\.(?: Já com o desconto de ${NUMERO}% por comportamento fora do padrão\\.)?`, (x, d) =>
    `chance of recovery: ${x}%.` + (d ? ` Already includes the ${d}% discount for unusual behavior.` : ''),
  ),
  regra('dia provável de saldo: (\\d{2}/\\d{2})\\.(?: Perfil de recebimento estimado pelo sistema: (.+)\\.)?', (d, p) =>
    `likely funds date: ${data(d)}.` + (p ? ` Income profile estimated by the system: ${PERFIL[p] ?? p}.` : ''),
  ),
  regra('o sistema decidiu não agir: (.+)\\. O ciclo fica registrado como encerrado sem recuperação\\.', (m) =>
    doDicionario(m) ? `the system decided not to act: ${doDicionario(m)}. The cycle is logged as closed without recovery.` : null,
  ),
  regra(`plano: ${NUMERO} tentativas? dentro de 7 dias, (?:no dia|nos dias) (.+)\\. Nenhuma mensagem antes de todas falharem\\.`, (x, dias) => {
    const em = listaDeDatas(dias)
    return em ? `plan: ${n(x, 'attempt', 'attempts')} within 7 days, on ${em}. No message until all of them fail.` : null
  }),
  regra(`tentativa ${NUMERO}: autorização revogada\\. Cobrar de novo não adianta: as outras tentativas são canceladas e o sistema vai para a mensagem\\.`, (x) =>
    `Attempt ${x}: authorization revoked. Charging again won't help: the other attempts are canceled and the system moves on to the message.`,
  ),
  regra(`tentativa ${NUMERO} não passou \\((.+)\\)\\.`, (x, c) => (doDicionario(c) ? `Attempt ${x} failed (${doDicionario(c)}).` : null)),
  regra(`tentativa ${NUMERO} paga\\.`, (x) => `Attempt ${x} paid.`),
  regra(`3 mensagens escritas para este cliente\\. A empresa escolhe uma; sem escolha em ${NUMERO} h, a recomendada sai sozinha\\.`, (h) =>
    `3 messages written for this customer. The company picks one; if none is chosen within ${h} h, the recommended one goes out on its own.`,
  ),
  regra('mensagem enviada por (.+?) \\((.+?), escolhida (pelo prazo|pelo modo automático|pela empresa)\\)\\. O sistema espera o pagamento pelo meio oferecido\\.', (c, a, por) => {
    const abordagem = doDicionario(a)
    if (!abordagem) return null
    const quemEscolheu = ({ 'pelo prazo': 'by the deadline', 'pelo modo automático': 'by automatic mode', 'pela empresa': 'by the company' } as Record<string, string>)[por]
    return `message sent via ${doDicionarioOuComoVeio(c)} (${abordagem}, chosen ${quemEscolheu}). The system waits for payment through the method offered.`
  }),
  regra(`pagamento recuperado na ${NUMERO}ª tentativa\\. O ciclo fecha e o valor entra no extrato\\.`, (x) =>
    `payment recovered on attempt ${x}. The cycle closes and the amount goes into the statement.`,
  ),

  /* Simulação: "Sem a CRAI" (simulador.py sem_crai) */
  regra(
    `sem a CRAI, o banco só tenta nas duas janelas do dia do vencimento\\. O dinheiro entra em ${NUMERO} dias?, então a cobrança se perde e a empresa precisa correr atrás por conta própria\\.`,
    (x) => `without CRAI, the bank only tries in the two windows on the due date. The money arrives in ${n(x, 'day', 'days')}, so the charge is lost and the company has to chase it on its own.`,
  ),

  /* Retenção simulada: por que esta oferta (api/simulacao.py _porque_da_oferta) */
  regra(
    '(O cliente mostrou intenção explícita de sair\\. Nesse caso o sistema age sempre, por regra, qualquer que seja o risco calculado\\. |Pela posição na base, o cliente está entre os de maior risco e tem sinal de abandono\\. )?' +
      '(Como o caso é preocupante, e não grave, o sistema escolheu a oferta de retenção de menor custo|O sistema sorteia a partir do que já aprendeu sobre cada oferta para este perfil\\. Nesta rodada, esta teve o maior retorno esperado)' +
      `(?: \\(chance de aceite aprendida até aqui: ${NUMERO}%\\))?\\.`,
    (regraDaBase, porque, chance) => {
      const antes = regraDaBase.startsWith('O cliente')
        ? 'The customer showed explicit intent to leave. In that case the system always acts, by rule, whatever the calculated risk. '
        : regraDaBase
          ? 'By position in the base, the customer is among the highest-risk and shows a sign of churn. '
          : ''
      const meio = porque.startsWith('Como')
        ? 'Since the case is Concerning, not Severe, the system chose the lowest-cost retention offer'
        : 'The system draws from what it has learned so far about each offer for this profile. This round, this one had the highest expected return'
      return antes + meio + (chance ? ` (acceptance chance learned so far: ${chance}%).` : '.')
    },
  ),

  /* Ofertas legíveis com outros números (retention_log.py ROTULOS_DE_OFERTA) */
  regra(`desconto de ${NUMERO}% por ${NUMERO} (?:mês|meses)`, (p, x) => `${p}% off for ${n(x, 'month', 'months')}`),
  regra(`pausa de ${NUMERO} (?:mês|meses) na assinatura, sem custo`, (x) => `${x}-month subscription pause, at no cost`),

  /* Extrato (api/visao_geral.py linhas_do_*) */
  regra(`recuperado na ${NUMERO}ª tentativa`, (x) => `recovered on attempt ${x}`),
  regra('aceitou (.+)', (o) => (doDicionarioDeOferta(o) ? `accepted ${doDicionarioDeOferta(o)}` : null)),
  regra(`cancelou ${NUMERO} dias? depois do aceite: estorno`, (x) => `canceled ${n(x, 'day', 'days')} after accepting: refund`),

  /* Atividade recente (api/visao_geral.py eventos_do_*) */
  regra(`cobrança de (.+?) recuperada na ${NUMERO}ª tentativa`, (q, x) => `charge from ${quem(q)} recovered on attempt ${x}`),
  regra('cobrança de (.+?) recuperada depois da mensagem', (q) => `charge from ${quem(q)} recovered after the message`),
  regra('cobrança de (.+?) recuperada', (q) => `charge from ${quem(q)} recovered`),
  regra(`tentativa ${NUMERO} não passou para (.+?)(?:: (.+))?`, (x, q, c) => {
    if (!c) return `Attempt ${x} failed for ${quem(q)}`
    const causa = doDicionario(c)
    return causa ? `Attempt ${x} failed for ${quem(q)}: ${causa}` : null
  }),
  regra('mensagem enviada por (WhatsApp|E-mail|SMS|Aviso dentro do produto) para (.+)', (c, q) => `message sent via ${doDicionarioOuComoVeio(c)} to ${quem(q)}`),
  regra('mensagem enviada para (.+)', (q) => `message sent to ${quem(q)}`),
  regra('mensagens sugeridas para (.+), aguardando a sua escolha', (q) => `suggested messages for ${quem(q)}, awaiting your choice`),
  regra('([Oo] valor recuperado|[Pp]arte do valor recuperado) de (.+) voltou ao cliente dentro do prazo e saiu do recuperado', (parte, q) =>
    `${parte.toLowerCase() === 'o valor recuperado' ? 'the amount recovered' : 'part of the amount recovered'} from ${quem(q)} went back to the customer within the window and was removed from the recovered amount`,
  ),
  regra('(.+?) aceitou (.+)', (q, o) => (doDicionarioDeOferta(o) ? `${quem(q)} accepted ${doDicionarioDeOferta(o)}` : null)),
  regra(`(.+?) cancelou ${NUMERO} dias? depois do aceite; o valor saiu do mantido`, (q, x) =>
    `${quem(q)} canceled ${n(x, 'day', 'days')} after accepting; the amount was removed from kept revenue`,
  ),
  regra('(.+?) entrou em risco grave', (q) => `${quem(q)} is now at severe risk`),
]

/** Uma oferta legível: do dicionário, ou pelas regras de desconto e pausa. */
function doDicionarioDeOferta(texto: string): string | null {
  const exata = doDicionario(texto)
  if (exata !== null) return exata
  for (const r of REGRAS_DE_OFERTA) {
    const m = r.padrao.exec(texto)
    if (m) return comMaiuscula(texto, r.montar(...m.slice(1).map((g) => g ?? '')))
  }
  return null
}

const REGRAS_DE_OFERTA: Regra[] = [
  regra(`desconto de ${NUMERO}% por ${NUMERO} (?:mês|meses)`, (p, x) => `${p}% off for ${n(x, 'month', 'months')}`),
  regra(`pausa de ${NUMERO} (?:mês|meses) na assinatura, sem custo`, (x) => `${x}-month subscription pause, at no cost`),
]

/* ------------------------------------------------------------------ */
/* O motivo do risco voluntário, por pedaços                            */
/* ------------------------------------------------------------------ */

/**
 * O motivo do voluntário (churn_voluntary/batch_scoring.py `explicar` e `_explicar_pelo_modelo`,
 * insights_unificados.py "último evento: ...", e o `_motivo` da retenção simulada) é montado de
 * pedaços fixos com números no meio, ligados por vírgula, travessão e ponto e vírgula. Cada
 * pedaço conhecido vira inglês; o resultado só vale se SOBRAREM apenas os separadores (nada em
 * português fica no meio do inglês).
 */
type Pedaco = [RegExp, (...g: string[]) => string]

const SEM_SINAL = '(?:entrou há menos de \\d+ dias|usa o produto)'

const PEDACOS: Pedaco[] = [
  [/último evento: ([^;]+);/u, (e) => `last event: ${e};`],
  [/sem dado de atividade — impossível avaliar risco de churn para este cliente/u, () => 'no activity data — impossible to assess churn risk for this customer'],
  [
    new RegExp(`primeiro da fila da sua base, mas sem sinal de abandono: (${SEM_SINAL}(?: e ${SEM_SINAL})?)`, 'u'),
    (motivos) =>
      'first in line in your base, but with no sign of churn: ' +
      motivos
        .replace(/entrou há menos de (\d+) dias/u, 'logged in less than $1 days ago')
        .replace('usa o produto', 'uses the product')
        .replace(' e ', ' and '),
  ],
  [/grave pelo valor da conta: tem risco e o MRR está entre os (\d+)% maiores da sua base/u, (x) => `severe because of the account value: at risk, and the MRR is among the top ${x}% of your base`],
  [/o sistema agiu por intenção explícita do cliente \(o evento\), e não pela pontuação de risco/u, () => "the system acted on the customer's explicit intent (the event), not on the risk score"],
  [/crítico pelo valor da conta, não pelo risco/u, () => 'severe because of the account value, not the risk'],
  [/(entre os|fora dos) (\d+)% de maior risco da sua base, pelo modelo/u, (onde, x) => `${onde === 'entre os' ? 'among' : 'outside'} the ${x}% highest-risk customers in your base, by the model`],
  [/risco pelo modelo, sem base de comparação para posicionar/u, () => 'risk by the model, with no base to compare against'],
  [/dias sem login desconhecidos \(assumido 0\)/u, () => 'days without login unknown (assumed 0)'],
  [/dias sem login desconhecidos/u, () => 'days without login unknown'],
  [/acessou hoje/u, () => 'logged in today'],
  [/sem login há (\d(?:[\d.,]*\d)?) dias?/u, (x) => `no login for ${n(x, 'day', 'days')}`],
  [/usa (\d(?:[\d.,]*\d)?) funcionalidades? nos últimos 30 dias/u, (x) => `uses ${n(x, 'feature', 'features')} in the last 30 days`],
  [/uso de funcionalidades desconhecido \(assumido como os mais ativos da sua base\)/u, () => 'feature usage unknown (assumed to be among the most active in your base)'],
  [/uso de funcionalidades desconhecido \(assumido 10\)/u, () => 'feature usage unknown (assumed 10)'],
  [/uso de funcionalidades desconhecido/u, () => 'feature usage unknown'],
  [/MRR (R\$ ?-?\d(?:[\d.,]*\d)?)/u, (v) => `MRR ${v}`],
  [/MRR desconhecido/u, () => 'MRR unknown'],
  [/acima de (\d+)% da sua base/u, (x) => `higher than ${x}% of your base`],
  [/acima da metade da sua base/u, () => 'higher than half of your base'],
  [/menos que (\d+)% da sua base/u, (x) => `lower than ${x}% of your base`],
  [/menos que a metade da sua base/u, () => 'lower than half of your base'],
  [/dentro do normal da sua base/u, () => 'within the normal range of your base'],
  [/abriu a página de cancelamento/u, () => 'opened the cancellation page'],
  [/sem dado de uso/u, () => 'no usage data'],
  [/(\d+) chamados de suporte nos últimos 30 dias/u, (x) => `${n(x, 'support ticket', 'support tickets')} in the last 30 days`],
  [/(\d+) pagamentos que falharam nos últimos 90 dias/u, (x) => `${n(x, 'failed payment', 'failed payments')} in the last 90 days`],
]

/** Os separadores que podem sobrar entre os pedaços. */
const SO_SEPARADORES = /^[\s,;:.—–-]*$/u

function porPedacos(texto: string): string | null {
  // "Sem login há…" → "sem login há…" (mas "MRR…" fica como está).
  let resto = /^\p{Lu}\p{Ll}/u.test(texto) ? primeiraMinuscula(texto) : texto
  const traduzidos: string[] = []
  for (const [padrao, montar] of PEDACOS) {
    resto = resto.replace(new RegExp(padrao.source, 'gu'), (...args: unknown[]) => {
      // (o trecho, os grupos..., a posição, o texto inteiro)
      const grupos = args.slice(1, -2).map((g) => (typeof g === 'string' ? g : ''))
      traduzidos.push(montar(...grupos))
      return `\u0000${traduzidos.length - 1}\u0001`
    })
  }
  if (!traduzidos.length || !SO_SEPARADORES.test(resto.replace(/\u0000\d+\u0001/g, ''))) return null
  return resto.replace(/\u0000(\d+)\u0001/g, (_, i: string) => traduzidos[Number(i)])
}

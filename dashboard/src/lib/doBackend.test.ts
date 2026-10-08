/**
 * `doBackend()`: as frases de vocabulário fixo que o backend manda prontas. Em português passam
 * iguais; em inglês viram inglês, com números e valores como vieram; o que não é conhecido passa
 * sem mudança (nunca meio traduzido).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'

type DoBackend = typeof import('./doBackend').doBackend

async function emIngles(): Promise<DoBackend> {
  vi.stubGlobal('localStorage', { getItem: (k: string) => (k === 'crai_idioma' ? 'en' : null), setItem: () => undefined })
  vi.resetModules()
  return (await import('./doBackend')).doBackend
}

async function emPortugues(): Promise<DoBackend> {
  vi.resetModules()
  return (await import('./doBackend')).doBackend
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.resetModules()
})

/** Um exemplo de cada família, como o backend escreve (com a maiúscula que o adaptador põe). */
const EXEMPLOS: Record<string, string> = {
  // fatores do classificador
  '2 meses como cliente': '2 months as a customer',
  '1 meses como cliente': '1 month as a customer',
  'Histórico de pagamento 90% positivo': 'Payment history 90% positive',
  'Cobrança de R$ 720,00': 'Charge of R$ 720,00',
  '0 falhas de pagamento nos últimos 90 dias': '0 payment failures in the last 90 days',
  'Causa da falha de pagamento: saldo insuficiente': 'Payment failure cause: insufficient funds',
  'Causa da falha de pagamento: autorização de recorrência revogada': 'Payment failure cause: recurring authorization revoked',
  'Meio de pagamento Pix Automático': 'Payment method Pix Automático',
  'Cobrança num(a) segunda-feira': 'Charge on a Monday',
  'Vencimento em 09/10/2026': 'Due on 10/09/2026',
  'Retorno esperado da intervenção R$ 1.234,56': 'Expected return from intervening R$ 1.234,56',
  'Canal e-mail': 'Channel email',
  'Comportamento anômalo detectado': 'Unusual behavior detected',
  'Fator do diagnóstico sem rótulo legível': 'Diagnosis factor without a readable label',
  // causas legíveis
  'Saldo insuficiente': 'Insufficient funds',
  'Limite excedido': 'Limit exceeded',
  'Autorização revogada': 'Authorization revoked',
  'Erro de processamento': 'Processing error',
  'Cartão expirado': 'Card expired',
  'Cartão recusado': 'Card declined',
  'Recusado pelo emissor': 'Declined by the issuer',
  'Recusa genérica': 'Generic decline',
  // próxima ação
  'Tentativa 1 de cobrança': 'Charge attempt 1',
  'Tentativa 2': 'Attempt 2',
  'Envio da mensagem escolhida': 'Sending the chosen message',
  'Envio da mensagem recomendada': 'Sending the recommended message',
  'Envio automático da mensagem recomendada': 'Automatic sending of the recommended message',
  'Envio automático da recomendada': 'Automatic sending of the recommended one',
  'Mensagem depois das tentativas': 'Message after the attempts',
  'Prazo para resposta do cliente': "Deadline for the customer's reply",
  'Fim do prazo de recuperação': 'End of the recovery window',
  // funil e "o que mais funciona"
  'Cobranças que falharam': 'Failed charges',
  'Mensagem': 'Message',
  'Desconto de 20% por 3 meses': '20% off for 3 months',
  'Pausa de 1 mês na assinatura, sem custo': '1-month subscription pause, at no cost',
  'Troca para Pix ou boleto em 1 clique': 'Switch to Pix or boleto in 1 click',
  'E-mail': 'Email',
  'Aviso dentro do produto': 'In-product notice',
  'WhatsApp': 'WhatsApp',
  // atividade recente
  'Cobrança de Agência Norte recuperada na 2ª tentativa': 'Charge from Agência Norte recovered on attempt 2',
  'Cobrança de um cliente sem cadastro recuperada depois da mensagem': 'Charge from an unregistered customer recovered after the message',
  'Tentativa 2 não passou para Loja Marés: saldo insuficiente': 'Attempt 2 failed for Loja Marés: insufficient funds',
  'Mensagem enviada por WhatsApp para Café Aroma': 'Message sent via WhatsApp to Café Aroma',
  'Mensagens sugeridas para Clínica Horizonte, aguardando a sua escolha': 'Suggested messages for Clínica Horizonte, awaiting your choice',
  'Parte do valor recuperado de Loja Marés voltou ao cliente dentro do prazo e saiu do recuperado':
    'Part of the amount recovered from Loja Marés went back to the customer within the window and was removed from the recovered amount',
  'Bruno Lima aceitou desconto de 10% por 3 meses': 'Bruno Lima accepted 10% off for 3 months',
  'Cliente c_123 aceitou pausa de 1 mês na assinatura, sem custo': 'Customer c_123 accepted 1-month subscription pause, at no cost',
  'Pet Shop Amigo cancelou 1 dia depois do aceite; o valor saiu do mantido': 'Pet Shop Amigo canceled 1 day after accepting; the amount was removed from kept revenue',
  'Loja Ponto Certo entrou em risco grave': 'Loja Ponto Certo is now at severe risk',
  // extrato
  'Recuperado na 3ª tentativa': 'Recovered on attempt 3',
  'Aceitou desconto de 20% por 3 meses': 'Accepted 20% off for 3 months',
  'Cancelou 12 dias depois do aceite: estorno': 'Canceled 12 days after accepting: refund',
  // motivo do risco voluntário
  'Sem login há 12 dias, usa 4 funcionalidades nos últimos 30 dias, MRR R$ 720,00 — fora dos 30% de maior risco da sua base, pelo modelo':
    'No login for 12 days, uses 4 features in the last 30 days, MRR R$ 720,00 — outside the 30% highest-risk customers in your base, by the model',
  'Acessou hoje, usa 1 funcionalidade nos últimos 30 dias, MRR R$ 1.500,50 — entre os 10% de maior risco da sua base, pelo modelo':
    'Logged in today, uses 1 feature in the last 30 days, MRR R$ 1.500,50 — among the 10% highest-risk customers in your base, by the model',
  'Último evento: cancel_page_view; sem login há 1 dia, usa 3 funcionalidades nos últimos 30 dias, MRR R$ 300,00 — crítico pelo valor da conta, não pelo risco':
    'Last event: cancel_page_view; no login for 1 day, uses 3 features in the last 30 days, MRR R$ 300,00 — severe because of the account value, not the risk',
  'Sem login há 3 dias, usa 9 funcionalidades nos últimos 30 dias, MRR R$ 200,00 — entre os 30% de maior risco da sua base, pelo modelo — primeiro da fila da sua base, mas sem sinal de abandono: entrou há menos de 7 dias e usa o produto':
    'No login for 3 days, uses 9 features in the last 30 days, MRR R$ 200,00 — among the 30% highest-risk customers in your base, by the model — first in line in your base, but with no sign of churn: logged in less than 7 days ago and uses the product',
  'Sem login há 47 dias — acima de 90% da sua base, usa 2 funcionalidades nos últimos 30 dias — menos que a metade da sua base, MRR R$ 890,00':
    'No login for 47 days — higher than 90% of your base, uses 2 features in the last 30 days — lower than half of your base, MRR R$ 890,00',
  'Dias sem login desconhecidos, uso de funcionalidades desconhecido, MRR desconhecido — risco pelo modelo, sem base de comparação para posicionar':
    'Days without login unknown, feature usage unknown, MRR unknown — risk by the model, with no base to compare against',
  'Sem dado de atividade — impossível avaliar risco de churn para este cliente': 'No activity data — impossible to assess churn risk for this customer',
  'Abriu a página de cancelamento, sem dado de uso, 3 chamados de suporte nos últimos 30 dias, 2 pagamentos que falharam nos últimos 90 dias':
    'Opened the cancellation page, no usage data, 3 support tickets in the last 30 days, 2 failed payments in the last 90 days',
  // simulação do gateway: "O que o sistema está pensando"
  'Causa da falha: saldo insuficiente.': 'Failure cause: insufficient funds.',
  'Chance de recuperar: 43%.': 'Chance of recovery: 43%.',
  'Chance de recuperar: 19%. Já com o desconto de 30% por comportamento fora do padrão.': 'Chance of recovery: 19%. Already includes the 30% discount for unusual behavior.',
  'Dia provável de saldo: 09/10. Perfil de recebimento estimado pelo sistema: CLT.': 'Likely funds date: 10/09. Income profile estimated by the system: salaried (CLT).',
  'Plano: 3 tentativas dentro de 7 dias, nos dias 09/10, 10/10 e 11/10. Nenhuma mensagem antes de todas falharem.':
    'Plan: 3 attempts within 7 days, on 10/09, 10/10 and 10/11. No message until all of them fail.',
  'Plano: 1 tentativa dentro de 7 dias, no dia 09/10. Nenhuma mensagem antes de todas falharem.': 'Plan: 1 attempt within 7 days, on 10/09. No message until all of them fail.',
  'Tentativa 1 não passou (saldo insuficiente).': 'Attempt 1 failed (insufficient funds).',
  'Tentativa 2 paga.': 'Attempt 2 paid.',
  'Pagamento recuperado na 2ª tentativa. O ciclo fecha e o valor entra no extrato.': 'Payment recovered on attempt 2. The cycle closes and the amount goes into the statement.',
  'O sistema decidiu não agir: a chance de recuperar é baixa demais para agir. O ciclo fica registrado como encerrado sem recuperação.':
    'The system decided not to act: the chance of recovery is too low to act. The cycle is logged as closed without recovery.',
  '3 mensagens escritas para este cliente. A empresa escolhe uma; sem escolha em 8 h, a recomendada sai sozinha.':
    '3 messages written for this customer. The company picks one; if none is chosen within 8 h, the recommended one goes out on its own.',
  'Mensagem enviada por e-mail (urgência com respeito, escolhida pelo prazo). O sistema espera o pagamento pelo meio oferecido.':
    'Message sent via email (respectful urgency, chosen by the deadline). The system waits for payment through the method offered.',
  // simulação: "Sem a CRAI"
  'Sem a CRAI, o banco só tenta nas duas janelas do dia do vencimento. O dinheiro entra em 4 dias, então a cobrança se perde e a empresa precisa correr atrás por conta própria.':
    'Without CRAI, the bank only tries in the two windows on the due date. The money arrives in 4 days, so the charge is lost and the company has to chase it on its own.',
  'O cliente revoga a autorização. Sem a CRAI, ninguém fala com ele e a assinatura acaba.': 'The customer revokes the authorization. Without CRAI, nobody talks to them and the subscription ends.',
  // retenção simulada
  'Como o caso é preocupante, e não grave, o sistema escolheu a oferta de retenção de menor custo (chance de aceite aprendida até aqui: 37%).':
    'Since the case is Concerning, not Severe, the system chose the lowest-cost retention offer (acceptance chance learned so far: 37%).',
  'O cliente mostrou intenção explícita de sair. Nesse caso o sistema age sempre, por regra, qualquer que seja o risco calculado. O sistema sorteia a partir do que já aprendeu sobre cada oferta para este perfil. Nesta rodada, esta teve o maior retorno esperado.':
    'The customer showed explicit intent to leave. In that case the system always acts, by rule, whatever the calculated risk. The system draws from what it has learned so far about each offer for this profile. This round, this one had the highest expected return.',
  'Sem a CRAI, ninguém perceberia os sinais até o pedido de cancelamento, quando já é tarde para oferecer algo.':
    "Without CRAI, nobody would notice the signals until the cancellation request, when it's too late to offer anything.",
  // assistente
  'Ver a visão geral': 'View the overview',
  'Ver o involuntário': 'View involuntary churn',
  'Ver o voluntário': 'View voluntary churn',
}

const AJUDA =
  'O assistente está indisponível agora. Os números continuam certos nas páginas do painel: Visão geral, Involuntário e Voluntário.\n\n' +
  'O que cada página mostra: a Visão geral soma o que foi recuperado e mantido e tem o extrato; o Involuntário lista as cobranças Pix que falharam e o que o sistema fez com cada uma; o Voluntário mostra os clientes em risco e as ofertas; a Configuração tem as regras das mensagens e os prazos de guarda dos dados.'

/** O que nunca muda: desconhecido, resposta do LLM, o registro do Art. 20, mensagem ao cliente. */
const DESCONHECIDOS = [
  'Uma frase nova que o backend escreveu',
  'Em setembro você recuperou R$ 4.300,00 em 12 cobranças.',
  'Em 09/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 43/100.',
  'Olá, Bruno! Sua cobrança de R$ 720,00 não passou. Pague pelo link: https://exemplo',
  'Causa da falha de pagamento: um_codigo_novo',
  'Tentativa 2 não passou para Loja Marés: um motivo novo',
  'Sem login há 12 dias, uma frase nova do backend',
  'Cliente Feliz aceitou uma oferta nova',
]

describe('doBackend em português', () => {
  it('devolve todo texto exatamente como veio', async () => {
    const doBackend = await emPortugues()
    for (const pt of [...Object.keys(EXEMPLOS), AJUDA, ...DESCONHECIDOS]) expect(doBackend(pt)).toBe(pt)
    expect(doBackend(null)).toBeNull()
    expect(doBackend(undefined)).toBeUndefined()
    expect(doBackend('')).toBe('')
  })
})

describe('doBackend em inglês', () => {
  it('traduz um exemplo de cada família de frase', async () => {
    const doBackend = await emIngles()
    const errados = Object.entries(EXEMPLOS)
      .map(([pt, en]) => ({ pt, esperado: en, veio: doBackend(pt) }))
      .filter((x) => x.veio !== x.esperado)
    expect(errados).toEqual([])
  })

  it('traduz o texto fixo do assistente', async () => {
    const doBackend = await emIngles()
    const en = doBackend(AJUDA)
    expect(en.startsWith('The assistant is unavailable right now.')).toBe(true)
    expect(en).toContain('What each page shows:')
  })

  it('a primeira letra segue o português: minúscula no meio da frase, maiúscula no começo', async () => {
    const doBackend = await emIngles()
    expect(doBackend('saldo insuficiente')).toBe('insufficient funds')
    expect(doBackend('Saldo insuficiente')).toBe('Insufficient funds')
    expect(doBackend('e-mail')).toBe('email')
    expect(doBackend('2 meses como cliente')).toBe('2 months as a customer')
  })

  it('números e valores em reais passam como vieram', async () => {
    const doBackend = await emIngles()
    expect(doBackend('Mensalidade de R$ 12.345,67')).toBe('Subscription fee of R$ 12.345,67')
    expect(doBackend('Sessões de 4,50 minutos em média')).toBe('Average session of 4,50 minutes')
    expect(doBackend('Probabilidade de recuperação 43%')).toBe('Recovery probability 43%')
    expect(doBackend('Sem login há 2,5 dias, usa 10 funcionalidades nos últimos 30 dias, MRR R$ 2.400,00')).toBe(
      'No login for 2,5 days, uses 10 features in the last 30 days, MRR R$ 2.400,00',
    )
  })

  it('texto desconhecido (ou com uma parte desconhecida) passa sem mudança', async () => {
    const doBackend = await emIngles()
    for (const pt of DESCONHECIDOS) expect(doBackend(pt)).toBe(pt)
    expect(doBackend(null)).toBeNull()
    expect(doBackend(undefined)).toBeUndefined()
  })
})

describe('os adaptadores usam doBackend', () => {
  it('em inglês, os campos de vocabulário fixo chegam traduzidos; o efeito e o registro do Art. 20 não', async () => {
    vi.stubGlobal('localStorage', { getItem: (k: string) => (k === 'crai_idioma' ? 'en' : null), setItem: () => undefined })
    vi.resetModules()
    const a = await import('../data/adaptadores')
    expect(a.adaptarContribuicoes([{ fator: 'causa da falha de pagamento: saldo insuficiente', efeito: 'reduziu a chance de recuperar' }])).toEqual([
      { fator: 'Payment failure cause: insufficient funds', efeito: 'Reduziu a chance de recuperar' },
    ])
    expect(a.adaptarFunil({ mes: '2026-10', etapas: [{ etapa: 'falhas', rotulo: 'Cobranças que falharam', chegaram: 3, valor: 900, recuperados_aqui: 0, valor_recuperado_aqui: 0 }], desfecho: { recuperados: 0, encerrados: 0, em_andamento: 3 } }).etapas[0].rotulo).toBe('Failed charges')
    expect(a.adaptarAssistente({ texto: AJUDA, links: [{ rotulo: 'Ver o voluntário', para: '/voluntario' }], sugestoes: [], origem: 'ajuda' }).links[0].rotulo).toBe('View voluntary churn')
    const registro = 'Em 09/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 43/100.'
    expect(a.adaptarExplicacao({ sujeito_id: 'c1', decisoes: [{ decidido_em: '2026-10-09', explicacao: registro }] })?.decisao).toBe(registro)
  })
})

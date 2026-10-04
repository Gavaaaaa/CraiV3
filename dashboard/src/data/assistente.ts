/**
 * Respostas de demonstração do assistente. Na integração, `api.assistente` chama
 * `POST /assistente`, que usa o LLM do sistema com a documentação de produto e lê só os
 * agregados do tenant (nunca contato nem texto de mensagem). Aqui, as respostas são montadas
 * dos mesmos números que as outras páginas mostram, para nunca contradizer o painel.
 */
import { clientesRisco, comparacaoReguaModelo, funil, metricasMes, resumoVoluntario, saude } from './mock'
import type { RespostaAssistente } from './tipos'

const brl = (v: number) => new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(v)
const pct = (v: number) => `${Math.round(v * 100)}%`

export const PERGUNTAS_PRONTAS = [
  'Quanto recuperei este mês?',
  'Por que este cliente está em risco?',
  'O que acontece depois da 3ª tentativa?',
  'Quanto a CRAI cobra?',
  'O que vocês fazem com os dados dos meus clientes?',
]

const normalizar = (t: string) =>
  t
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')

export function responder(pergunta: string): RespostaAssistente {
  const q = normalizar(pergunta)
  const tem = (...palavras: string[]) => palavras.some((p) => q.includes(normalizar(p)))

  if (tem('quanto recuperei', 'recuperado', 'recuperei', 'quanto ganhei', 'mantido', 'quanto a crai')) {
    if (tem('cobra', 'taxa', 'preco', 'custa')) return taxa()
    const inv = metricasMes.valor_liquido_recuperado
    const vol = resumoVoluntario.valor_liquido_mantido
    return {
      texto: `Em setembro, a CRAI recuperou ${brl(inv)} de cobranças Pix que tinham falhado (${metricasMes.recuperados} cobranças, ${pct(metricasMes.taxa_recuperacao ?? 0)} das que já tiveram desfecho) e manteve ${brl(vol)} de clientes que iam cancelar (${resumoVoluntario.clientes_mantidos} clientes, ${resumoVoluntario.estornos} estorno).\n\nSão ${brl(inv + vol)} no total, já descontada a taxa da CRAI. O extrato tem cada valor, linha por linha.`,
      links: [
        { rotulo: 'Ver o extrato', para: '/?aba=extrato' },
        { rotulo: 'Ver o involuntário', para: '/involuntario' },
      ],
      sugestoes: ['Quanto a CRAI cobra?', 'Quantos clientes estão em risco grave?'],
      origem: 'assistente',
    }
  }

  if (tem('em risco', 'risco grave', 'por que este cliente', 'esse cliente', 'este cliente')) {
    const graves = clientesRisco.filter((c) => c.faixa === 'grave' && !c.simulado)
    const exemplo = graves[0]
    return {
      texto: `Um cliente entra em risco quando o comportamento dele muda: uso caindo, chamados de suporte se acumulando, pagamentos atrasando. Para ${pct(comparacaoReguaModelo.clientes_com_dados / 1240)} da sua base, quem avalia é o modelo de IA, que aprende com esses sinais; para o resto, uma régua de regras fixas. A tabela mostra quem decidiu e a posição do cliente na base.\n\nGrave são os 10% da base com mais risco; Preocupante, os 20% seguintes; sempre com um sinal real de abandono. Clientes de mensalidade alta entram como Preocupante e só sobem para Grave se os sinais continuarem.\n\nHoje há ${resumoVoluntario.grave} clientes em risco grave e ${resumoVoluntario.preocupante} preocupantes. ${exemplo ? `Por exemplo, ${exemplo.nome}: ${exemplo.motivo.charAt(0).toLowerCase()}${exemplo.motivo.slice(1)}` : ''} Na página do voluntário, cada cliente tem o motivo em uma frase e a oferta que o sistema fez.`,
      links: [{ rotulo: 'Ver os clientes em risco', para: '/voluntario?aba=clientes' }],
      sugestoes: ['Que oferta funciona melhor?', 'Quem decide o risco, o modelo ou a régua?'],
      origem: 'assistente',
    }
  }

  if (tem('3a tentativa', 'terceira tentativa', 'depois da 3', 'tres tentativas', '3 tentativas', 'tentativas')) {
    const t3 = funil.etapas.find((e) => e.etapa === 'tentativa_3')!
    const msg = funil.etapas.find((e) => e.etapa === 'mensagem')!
    return {
      texto: `Quando uma cobrança Pix falha, o sistema estima o dia em que o cliente deve ter saldo e agenda até 3 tentativas dentro de 7 dias (é o limite do Pix Automático). Nesse período, nenhuma mensagem é enviada: só cobrança.\n\nSe a 3ª tentativa também falha, o sistema escreve 3 mensagens diferentes para aquele cliente (Lembrete cordial, Facilitação, Urgência com respeito) e escolhe o canal. Você escolhe qual enviar ou, sem escolha em 8 h, a recomendada sai sozinha. Se o cliente não responde em 2 dias, o ciclo encerra sem recuperação e fica registrado.\n\nEm setembro, ${t3.chegaram} cobranças chegaram à 3ª tentativa, ${msg.chegaram} foram para a mensagem e ${msg.recuperados_aqui} voltou por ela.`,
      links: [
        { rotulo: 'Ver o funil', para: '/?aba=caminho' },
        { rotulo: 'Ver a simulação', para: '/simulacao' },
      ],
      sugestoes: ['Posso escolher a mensagem antes de enviar?', 'Por que só Pix, e não cartão?'],
      origem: 'assistente',
    }
  }

  if (tem('escolher a mensagem', 'antes de enviar', 'modo automatico', 'escolha')) {
    return {
      texto: `Sim. Na configuração, em Mensagens, você escolhe entre dois modos: **escolha**, em que as 3 sugestões esperam a sua decisão por até 8 h (padrão), ou **automático**, em que a recomendada sai na hora. Nos dois casos a decisão fica registrada com quem escolheu e quando; é a revisão humana que a LGPD pede para decisões automatizadas.`,
      links: [{ rotulo: 'Abrir a configuração', para: '/configuracao' }],
      sugestoes: ['O que acontece depois da 3ª tentativa?'],
      origem: 'assistente',
    }
  }

  if (tem('cobra', 'taxa', 'preco', 'custa', 'quanto pago', 'mensalidade da crai')) return taxa()

  if (tem('oferta', 'funciona melhor', 'canal')) {
    return {
      texto: `Nos últimos 30 dias, a oferta mais aceita foi o desconto de 20% por 3 meses (42% de aceite em 12 casos), seguida do suporte dedicado (33%). O WhatsApp teve 64% de resposta, contra 31% do e-mail e 18% do SMS.\n\nO sistema usa esses números para escolher a oferta e o canal de cada cliente, e continua aprendendo com cada aceite e cada recusa. Com poucos casos a porcentagem ainda muda muito; a tela avisa quando é o caso.`,
      links: [{ rotulo: 'Ver o que mais funciona', para: '/?aba=funciona' }],
      sugestoes: ['Por que este cliente está em risco?'],
      origem: 'assistente',
    }
  }

  if (tem('modelo', 'regua', 'quem decide', 'algoritmo', 'ia ')) {
    const c = comparacaoReguaModelo
    return {
      texto: `Dois jeitos de avaliar o risco. O modelo de IA aprende com o comportamento (uso, chamados, atrasos, tempo de casa) e decide quando o cliente tem esses dados. A régua é um conjunto de regras fixas, usada para clientes novos, sem dados ainda.\n\nNos últimos ${c.dias} dias, entre os ${c.clientes_com_dados} clientes com dados, ${c.cancelamentos} cancelaram: o modelo tinha avisado ${c.modelo.avisou_antes}, a régua ${c.regua.avisou_antes}. E o modelo marcou ${c.modelo.marcou_grave} como grave contra ${c.regua.marcou_grave} da régua: menos alarme falso.`,
      links: [{ rotulo: 'Ver a comparação', para: '/voluntario?aba=decisao' }],
      sugestoes: ['Por que este cliente está em risco?'],
      origem: 'assistente',
    }
  }

  if (tem('dados', 'lgpd', 'privacidade', 'cpf', 'e-mail', 'telefone', 'contato')) {
    return {
      texto: `A CRAI é operadora dos dados: a sua empresa continua sendo a controladora, e os dados são usados só para recuperar cobranças e reter clientes. Os modelos nunca veem CPF, chave Pix, e-mail ou telefone; o contato é lido na hora do envio e não é copiado. O texto das mensagens é apagado 90 dias depois do desfecho; os ciclos ficam 24 meses e depois são anonimizados.\n\nEu, o assistente, também não vejo dado de contato: leio só os números agregados que o painel mostra, e esta conversa não é guardada. Na configuração, em Dados e privacidade, há o texto pronto para a sua política de privacidade e as ferramentas de exportar ou anonimizar um cliente.`,
      links: [{ rotulo: 'Dados e privacidade', para: '/configuracao?secao=dados' }],
      sugestoes: ['Quanto a CRAI cobra?'],
      origem: 'assistente',
    }
  }

  if (tem('cartao', 'so pix', 'por que pix')) {
    return {
      texto: `Porque o Pix Automático é onde a CRAI faz diferença: a regra do Banco Central permite até 3 novas tentativas em 7 dias, e o sistema escolhe o dia certo para cada uma, com base no perfil do pagador. Cartão tem retentativa inteligente embutida nos gateways; Pix não tinha ninguém cuidando. O gateway de pagamento real será integrado em breve; por enquanto a página de simulação mostra o sistema agindo sobre um cliente fictício.`,
      links: [{ rotulo: 'Ver a simulação', para: '/simulacao' }],
      sugestoes: ['O que acontece depois da 3ª tentativa?'],
      origem: 'assistente',
    }
  }

  if (tem('parou', 'funcionando', 'saude', 'relogio', 'fora do ar')) {
    return {
      texto: saude.relogio.ativo
        ? `Está tudo funcionando: o relógio das tentativas passou há menos de 1 minuto, os ${saude.modelos.total} modelos estão carregados e o redator de mensagens está disponível.`
        : `O relógio das tentativas está parado. As tentativas agendadas não estão saindo; a saúde do sistema na visão geral mostra o detalhe.`,
      links: [{ rotulo: 'Ver a saúde do sistema', para: '/?aba=saude' }],
      sugestoes: ['Quanto recuperei este mês?'],
      origem: 'assistente',
    }
  }

  return {
    texto: `Não encontrei isso nos dados do painel. Consigo responder sobre o que a CRAI recuperou e manteve, por que um cliente está em risco, como funcionam as tentativas e as mensagens, quanto a CRAI cobra e o que é feito com os dados. Tente uma das perguntas abaixo ou reformule.`,
    links: [],
    sugestoes: PERGUNTAS_PRONTAS.slice(0, 3),
    origem: 'assistente',
  }
}

function taxa(): RespostaAssistente {
  return {
    texto: `A CRAI só cobra sobre resultado. Não há mensalidade nem taxa de implantação. Cada cobrança recuperada e cada cliente mantido entram no extrato com o valor bruto, a taxa da CRAI e o líquido para você; se um pagamento recuperado for estornado ou um cliente mantido cancelar em 30 dias, o valor sai da conta. Sem recuperação, não há cobrança.`,
    links: [{ rotulo: 'Ver o extrato', para: '/?aba=extrato' }],
    sugestoes: ['Quanto recuperei este mês?'],
    origem: 'assistente',
  }
}

/** Quando o redator está fora do ar, o backend devolve este texto fixo. */
export const TEXTO_FIXO: RespostaAssistente = {
  texto: 'O assistente está indisponível agora. Os números continuam certos nas páginas do painel: Visão geral, Involuntário e Voluntário.',
  links: [{ rotulo: 'Ir para a visão geral', para: '/' }],
  sugestoes: [],
  origem: 'texto_fixo',
}

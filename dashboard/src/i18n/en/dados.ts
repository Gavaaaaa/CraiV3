/** Tradução para o inglês: dados. A chave é o texto em português, exatamente como está no código. */
export const dados: Record<string, string> = {
  // data/adaptadores.ts: abordagens e canais
  'Lembrete cordial': 'Friendly reminder',
  'Facilitação': 'Easier payment',
  'Urgência com respeito': 'Respectful urgency',
  'E-mail': 'Email',
  'Sem canal disponível': 'No channel available',

  // data/adaptadores.ts: motivo do canal
  'Contato cadastrado na sua base': 'Contact on file in your customer base',
  'O cliente não tem telefone nem e-mail na sua base': 'The customer has no phone or email in your customer base',
  'O cliente desta cobrança não está na sua base': 'The customer for this charge is not in your customer base',
  'Canal presumido: o cliente não tem contato na base': 'Assumed channel: the customer has no contact in your base',
  'O cliente pediu para não ser contatado': 'The customer asked not to be contacted',
  'Canal escolhido pelo sistema': 'Channel chosen by the system',

  // data/adaptadores.ts: motivos de descarte
  'Retorno esperado abaixo do custo da ação': 'Expected return below the cost of acting',
  'Chance de recuperação baixa demais para agir': 'Recovery chance too low to act',
  'O cliente revogou a autorização do Pix Automático': 'The customer revoked the Pix Automático authorization',
  'A janela de novas tentativas acabou': 'The retry window has ended',

  // data/adaptadores.ts: decisões e quem escolheu
  'Decisão registrada: avaliação da cobrança': 'Decision logged: charge assessment',
  'Decisão registrada: nova tentativa': 'Decision logged: retry',
  'Decisão registrada: mensagem': 'Decision logged: message',
  'Decisão registrada: canal': 'Decision logged: channel',
  'Decisão registrada': 'Decision logged',
  'Escolhida pelo dono': 'Chosen by the owner',
  'Escolhida por um administrador': 'Chosen by an admin',
  'O prazo de escolha acabou: saiu a recomendada': 'The choice deadline passed: the recommended one was sent',
  'Modo automático: saiu a recomendada': 'Automatic mode: the recommended one was sent',

  // data/adaptadores.ts: linha do tempo
  'Cobrança falhou': 'Charge failed',
  'Diagnóstico: {pct}% de chance de recuperar': 'Diagnosis: {pct}% chance of recovery',
  'Diagnóstico concluído': 'Diagnosis complete',
  'Tentativa agendada': 'Attempt scheduled',
  'Tentativa {n} agendada': 'Attempt {n} scheduled',
  'Tentativa enviada ao banco': 'Attempt sent to the bank',
  'Tentativa {n} enviada ao banco': 'Attempt {n} sent to the bank',
  'Tentativa paga': 'Attempt paid',
  'Tentativa {n} paga': 'Attempt {n} paid',
  'Tentativa falhou': 'Attempt failed',
  'Tentativa {n} falhou': 'Attempt {n} failed',
  'Tentativa sem resposta do banco': 'Attempt got no response from the bank',
  'Tentativa {n} sem resposta do banco': 'Attempt {n} got no response from the bank',
  'Tentativa: resultado registrado': 'Attempt: result logged',
  'Tentativa {n}: resultado registrado': 'Attempt {n}: result logged',
  'Tentativa cancelada': 'Attempt canceled',
  'Tentativa {n} cancelada': 'Attempt {n} canceled',
  'O pagamento entrou antes': 'The payment came in first',
  'Outras 3 mensagens sugeridas ({rodada}ª rodada)': '3 more messages suggested (round {rodada})',
  '3 mensagens sugeridas': '3 suggested messages',
  'Recomendada: {abordagem}. Canal: {canal}.': 'Recommended: {abordagem}. Channel: {canal}.',
  'Canal: {canal}.': 'Channel: {canal}.',
  'Mensagem escolhida': 'Message chosen',
  'Mensagem escolhida: {abordagem}': 'Message chosen: {abordagem}',
  '{motivo}. A mensagem não foi entregue.': '{motivo}. The message was not delivered.',
  'Mensagem enviada': 'Message sent',
  'Pagamento recuperado': 'Payment recovered',
  '{valor} líquidos para você.': '{valor} net to you.',
  'Encerrado sem recuperação': 'Closed without recovery',
  'Não havia canal para entregar a mensagem em 30 dias.': 'There was no channel to deliver the message within 30 days.',
  'O pagamento não veio no prazo depois da mensagem.': 'The payment did not arrive in time after the message.',
  'Encerrado sem ação': 'Closed without action',
  'Pagamento devolvido dentro do prazo': 'Payment refunded within the refund window',
  'Pagamento devolvido depois do prazo': 'Payment refunded after the refund window',
  '{valor} devolvidos ao cliente.': '{valor} refunded to the customer.',
  'Todo o valor voltou: esta cobrança deixou de contar como recuperada.':
    'The full amount was refunded: this charge no longer counts as recovered.',
  'O valor saiu do que foi recuperado para você.': 'The amount was deducted from what was recovered for you.',
  'O prazo de estorno já tinha acabado: os valores não mudam.': 'The refund window had already closed: the amounts stay the same.',
  'Evento registrado': 'Event logged',
  'Avaliação inicial da cobrança. O número do topo é o que o sistema usou e já tem o desconto de {percentual}% por comportamento fora do padrão.':
    'Initial charge assessment. The number at the top is the one the system used and already includes the {percentual}% discount for unusual behavior.',

  // data/adaptadores.ts: importação da base
  'Linha {linha}: {motivo}': 'Row {linha}: {motivo}',
  'E mais {n} linhas recusadas.': 'And {n} more rows rejected.',

  // data/assistente.ts: perguntas prontas e sugeridas
  'Quanto recuperei este mês?': 'How much did I recover this month?',
  'Por que este cliente está em risco?': 'Why is this customer at risk?',
  'O que acontece depois da 3ª tentativa?': 'What happens after the 3rd attempt?',
  'Quanto a CRAI cobra?': 'How much does CRAI charge?',
  'O que vocês fazem com os dados dos meus clientes?': "What do you do with my customers' data?",
  'Quantos clientes estão em risco grave?': 'How many customers are at high risk?',
  'Que oferta funciona melhor?': 'Which offer works best?',
  'Quem decide o risco, o modelo ou a régua?': 'Who decides the risk, the model or the rules?',
  'Posso escolher a mensagem antes de enviar?': 'Can I choose the message before it is sent?',
  'Por que só Pix, e não cartão?': 'Why only Pix, and not cards?',

  // data/assistente.ts: links
  'Ver o extrato': 'View the statement',
  'Ver o involuntário': 'View involuntary churn',
  'Ver os clientes em risco': 'View at-risk customers',
  'Ver o funil': 'View the funnel',
  'Ver a simulação': 'View the simulation',
  'Abrir a configuração': 'Open settings',
  'Ver o que mais funciona': 'See what works best',
  'Ver a comparação': 'View the comparison',
  'Dados e privacidade': 'Data and privacy',
  'Ver a saúde do sistema': 'View system health',
  'Ir para a visão geral': 'Go to the overview',

  // data/assistente.ts: respostas de demonstração
  'Em setembro, a CRAI recuperou {inv} de cobranças Pix que tinham falhado ({recuperados} cobranças, {taxa} das que já tiveram desfecho) e manteve {vol} de clientes que iam cancelar ({mantidos} clientes, {estornos} estorno).\n\nSão {total} no total, já descontada a taxa da CRAI. O extrato tem cada valor, linha por linha.':
    'In September, CRAI recovered {inv} in failed Pix charges ({recuperados} charges, {taxa} of those already closed) and kept {vol} from customers who were about to cancel ({mantidos} customers, {estornos} refund).\n\nThat is {total} in total, net of the CRAI fee. The statement shows every amount, line by line.',
  'Um cliente entra em risco quando o comportamento dele muda: uso caindo, chamados de suporte se acumulando, pagamentos atrasando. Para {pct} da sua base, quem avalia é o modelo de IA, que aprende com esses sinais; para o resto, uma régua de regras fixas. A tabela mostra quem decidiu e a posição do cliente na base.\n\nGrave são os 10% da base com mais risco; Preocupante, os 20% seguintes; sempre com um sinal real de abandono. Clientes de mensalidade alta entram como Preocupante e só sobem para Grave se os sinais continuarem.\n\nHoje há {graves} clientes em risco grave e {preocupantes} preocupantes. {exemplo} Na página do voluntário, cada cliente tem o motivo em uma frase e a oferta que o sistema fez.':
    'A customer becomes at risk when their behavior changes: usage dropping, support tickets piling up, late payments. For {pct} of your base, the AI model makes the call, learning from these signals; for the rest, a set of fixed rules does. The table shows who decided and where the customer ranks in your base.\n\nHigh risk is the 10% of your base with the most risk; Concerning, the next 20%; always with a real sign of churn. High-paying customers start as Concerning and only move up to High risk if the signals continue.\n\nToday there are {graves} customers at high risk and {preocupantes} concerning. {exemplo} On the voluntary churn page, each customer has a one-sentence reason and the offer the system made.',
  'Por exemplo, {nome}: {motivo}': 'For example, {nome}: {motivo}',
  'Quando uma cobrança Pix falha, o sistema estima o dia em que o cliente deve ter saldo e agenda até 3 tentativas dentro de 7 dias (é o limite do Pix Automático). Nesse período, nenhuma mensagem é enviada: só cobrança.\n\nSe a 3ª tentativa também falha, o sistema escreve 3 mensagens diferentes para aquele cliente (Lembrete cordial, Facilitação, Urgência com respeito) e escolhe o canal. Você escolhe qual enviar ou, sem escolha em 8 h, a recomendada sai sozinha. Se o cliente não responde em 2 dias, o ciclo encerra sem recuperação e fica registrado.\n\nEm setembro, {terceira} cobranças chegaram à 3ª tentativa, {mensagem} foram para a mensagem e {recuperadas} voltou por ela.':
    'When a Pix charge fails, the system estimates the day the customer should have funds and schedules up to 3 attempts within 7 days (the Pix Automático limit). During that period, no message is sent: only charges.\n\nIf the 3rd attempt also fails, the system writes 3 different messages for that customer (Friendly reminder, Easy payment, Respectful urgency) and picks the channel. You choose which one to send or, with no choice within 8 h, the recommended one goes out on its own. If the customer does not respond within 2 days, the cycle closes without recovery and is logged.\n\nIn September, {terceira} charges reached the 3rd attempt, {mensagem} went to the message stage and {recuperadas} was recovered by it.',
  'Sim. Na configuração, em Mensagens, você escolhe entre dois modos: **escolha**, em que as 3 sugestões esperam a sua decisão por até 8 h (padrão), ou **automático**, em que a recomendada sai na hora. Nos dois casos a decisão fica registrada com quem escolheu e quando; é a revisão humana que a LGPD pede para decisões automatizadas.':
    'Yes. In Settings, under Messages, you pick one of two modes: **choice**, where the 3 suggestions wait for your decision for up to 8 h (default), or **automatic**, where the recommended one goes out right away. Either way, the decision is logged with who chose and when; this is the human review the LGPD requires for automated decisions.',
  'Nos últimos 30 dias, a oferta mais aceita foi o desconto de 20% por 3 meses (42% de aceite em 12 casos), seguida do suporte dedicado (33%). O WhatsApp teve 64% de resposta, contra 31% do e-mail e 18% do SMS.\n\nO sistema usa esses números para escolher a oferta e o canal de cada cliente, e continua aprendendo com cada aceite e cada recusa. Com poucos casos a porcentagem ainda muda muito; a tela avisa quando é o caso.':
    'In the last 30 days, the most accepted offer was 20% off for 3 months (42% acceptance across 12 cases), followed by dedicated support (33%). WhatsApp had a 64% response rate, versus 31% for email and 18% for SMS.\n\nThe system uses these numbers to choose the offer and channel for each customer, and keeps learning from every acceptance and every refusal. With few cases, the percentage can still swing a lot; the screen flags when that happens.',
  'Dois jeitos de avaliar o risco. O modelo de IA aprende com o comportamento (uso, chamados, atrasos, tempo de casa) e decide quando o cliente tem esses dados. A régua é um conjunto de regras fixas, usada para clientes novos, sem dados ainda.\n\nNos últimos {dias} dias, entre os {com_dados} clientes com dados, {cancelamentos} cancelaram: o modelo tinha avisado {modelo_avisou}, a régua {regua_avisou}. E o modelo marcou {modelo_grave} como grave contra {regua_grave} da régua: menos alarme falso.':
    'Two ways to assess risk. The AI model learns from behavior (usage, tickets, late payments, tenure) and decides when the customer has that data. The rules are a fixed set of rules, used for new customers who have no data yet.\n\nIn the last {dias} days, among the {com_dados} customers with data, {cancelamentos} canceled: the model had flagged {modelo_avisou}, the rules {regua_avisou}. And the model marked {modelo_grave} as high risk versus {regua_grave} for the rules: fewer false alarms.',
  'A CRAI é operadora dos dados: a sua empresa continua sendo a controladora, e os dados são usados só para recuperar cobranças e reter clientes. Os modelos nunca veem CPF, chave Pix, e-mail ou telefone; o contato é lido na hora do envio e não é copiado. O texto das mensagens é apagado 90 dias depois do desfecho; os ciclos ficam 24 meses e depois são anonimizados.\n\nEu, o assistente, também não vejo dado de contato: leio só os números agregados que o painel mostra, e esta conversa não é guardada. Na configuração, em Dados e privacidade, há o texto pronto para a sua política de privacidade e as ferramentas de exportar ou anonimizar um cliente.':
    'CRAI is the data processor: your company remains the controller, and the data is used only to recover charges and retain customers. The models never see CPF, Pix key, email or phone; contact details are read at send time and are not copied. Message text is deleted 90 days after the outcome; cycles are kept for 24 months and then anonymized.\n\nI, the assistant, do not see contact data either: I only read the aggregate numbers the dashboard shows, and this conversation is not stored. In Settings, under Data and privacy, you will find ready-made text for your privacy policy and the tools to export or anonymize a customer.',
  'Porque o Pix Automático é onde a CRAI faz diferença: a regra do Banco Central permite até 3 novas tentativas em 7 dias, e o sistema escolhe o dia certo para cada uma, com base no perfil do pagador. Cartão tem retentativa inteligente embutida nos gateways; Pix não tinha ninguém cuidando. O gateway de pagamento real será integrado em breve; por enquanto a página de simulação mostra o sistema agindo sobre um cliente fictício.':
    "Because Pix Automático is where CRAI makes a difference: the Central Bank rule allows up to 3 new attempts within 7 days, and the system picks the right day for each one based on the payer's profile. Cards already have smart retries built into the gateways; nobody was taking care of Pix. The real payment gateway will be integrated soon; for now, the simulation page shows the system acting on a fictional customer.",
  'Está tudo funcionando: o relógio das tentativas passou há menos de 1 minuto, os {total} modelos estão carregados e o redator de mensagens está disponível.':
    'Everything is working: the attempt clock ran less than 1 minute ago, all {total} models are loaded and the message writer is available.',
  'O relógio das tentativas está parado. As tentativas agendadas não estão saindo; a saúde do sistema na visão geral mostra o detalhe.':
    'The attempt clock has stopped. Scheduled attempts are not going out; system health on the overview shows the details.',
  'Não encontrei isso nos dados do painel. Consigo responder sobre o que a CRAI recuperou e manteve, por que um cliente está em risco, como funcionam as tentativas e as mensagens, quanto a CRAI cobra e o que é feito com os dados. Tente uma das perguntas abaixo ou reformule.':
    "I couldn't find that in the dashboard data. I can answer about what CRAI recovered and kept, why a customer is at risk, how attempts and messages work, how much CRAI charges and what is done with the data. Try one of the questions below or rephrase.",
  'A CRAI só cobra sobre resultado. Não há mensalidade nem taxa de implantação. Cada cobrança recuperada e cada cliente mantido entram no extrato com o valor bruto, a taxa da CRAI e o líquido para você; se um pagamento recuperado for estornado ou um cliente mantido cancelar em 30 dias, o valor sai da conta. Sem recuperação, não há cobrança.':
    'CRAI only charges on results. There is no monthly fee and no setup fee. Each recovered charge and each kept customer goes on the statement with the gross amount, the CRAI fee and your net; if a recovered payment is refunded or a kept customer cancels within 30 days, the amount is taken off. No recovery, no charge.',
  'O assistente está indisponível agora. Os números continuam certos nas páginas do painel: Visão geral, Involuntário e Voluntário.':
    'The assistant is unavailable right now. The numbers are still accurate on the dashboard pages: Overview, Involuntary churn and Voluntary churn.',
}

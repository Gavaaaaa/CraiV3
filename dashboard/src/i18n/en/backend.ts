/**
 * Tradução para o inglês: as frases FIXAS que o backend manda prontas. A chave é o texto como o
 * backend escreve (os rótulos que ele manda em minúscula ficam em minúscula aqui; `doBackend()`
 * acha também a versão com a primeira letra maiúscula e devolve a tradução com maiúscula).
 *
 * As frases com partes que mudam (números, valores, nomes) não estão aqui: são as regras de
 * `lib/doBackend.ts`, que usam este dicionário para as partes de vocabulário fixo (a causa, a
 * oferta, o canal).
 *
 * Fonte de cada grupo no backend (app/crai): ver o comentário de cada bloco.
 */
export const backend: Record<string, string> = {
  // agent/pix_codes.py CAUSA_LEGIVEL e churn_voluntary/retention_log.py ROTULOS_DE_CAUSA
  'saldo insuficiente': 'insufficient funds',
  'limite excedido': 'limit exceeded',
  'autorização revogada': 'authorization revoked',
  'erro de processamento': 'processing error',
  'cartão expirado': 'card expired',
  'cartão recusado': 'card declined',
  'recusado pelo emissor': 'declined by the issuer',
  'recusa genérica': 'generic decline',
  'autorização de recorrência revogada': 'recurring authorization revoked',
  'sem motivo informado': 'no reason given',

  // retention_log.py ROTULOS_DE_OFERTA (os descontos e a pausa com outros números são regras)
  'desconto de 10% por 3 meses': '10% off for 3 months',
  'desconto de 20% por 3 meses': '20% off for 3 months',
  'pausa de 1 mês na assinatura, sem custo': '1-month subscription pause, at no cost',
  'troca para Pix ou boleto em 1 clique': 'switch to Pix or boleto in 1 click',
  'nenhuma oferta': 'no offer',

  // visao_geral.py ROTULO_DO_CANAL, retention_log.py ROTULOS_DE_CANAL, simulacao.py ROTULO_DO_CANAL
  'e-mail': 'email',
  'aviso dentro do produto': 'in-product notice',
  'notificação no aplicativo': 'app notification',
  'sem canal disponível': 'no channel available',
  'nenhum canal': 'no channel',

  // retention_log.py ROTULOS_DE_METODO
  'cartão': 'card',

  // retention_log.py DIAS_DA_SEMANA
  'segunda-feira': 'Monday',
  'terça-feira': 'Tuesday',
  'quarta-feira': 'Wednesday',
  'quinta-feira': 'Thursday',
  'sexta-feira': 'Friday',
  'sábado': 'Saturday',
  'domingo': 'Sunday',

  // simulacao.py ROTULO_DA_ABORDAGEM
  'lembrete cordial': 'friendly reminder',
  'facilitação': 'easier payment',
  'urgência com respeito': 'respectful urgency',

  // visao_geral.py ETAPAS_DO_FUNIL ("Tentativa N" é regra)
  'cobranças que falharam': 'failed charges',
  'mensagem': 'message',

  // retention_log.py ROTULOS_DE_FEATURE: os fatores sem número; api/ciclos.py: o fator sem rótulo
  'cliente no produto neste momento': 'customer in the product right now',
  'cliente fora do produto': 'customer not in the product',
  'telefone disponível para contato': 'phone available for contact',
  'sem telefone para contato': 'no phone for contact',
  'sem histórico de canal': 'no channel history',
  'comportamento anômalo detectado': 'unusual behavior detected',
  'sem anomalia de comportamento': 'no unusual behavior',
  'fator do diagnóstico sem rótulo legível': 'diagnosis factor without a readable label',

  // api/ciclos.py e api/simulacao.py: a próxima ação ("Tentativa N" é regra)
  'envio da mensagem escolhida': 'sending the chosen message',
  'envio da mensagem recomendada': 'sending the recommended message',
  'envio automático da mensagem recomendada': 'automatic sending of the recommended message',
  'envio automático da recomendada': 'automatic sending of the recommended one',
  'mensagem depois das tentativas': 'message after the attempts',
  'prazo para resposta do cliente': "deadline for the customer's reply",
  'fim do prazo de recuperação': 'end of the recovery window',

  // api/visao_geral.py: a descrição das linhas do extrato
  'recuperado': 'recovered',
  'recuperado depois da mensagem': 'recovered after the message',
  'devolução ao cliente dentro do prazo: estorno': 'returned to the customer within the window: refund',

  // api/simulacao.py MOTIVO_DESCARTE (dentro de "O sistema decidiu não agir: ...")
  'o retorno esperado ficou abaixo do custo da ação': 'the expected return was below the cost of acting',
  'a chance de recuperar é baixa demais para agir': 'the chance of recovery is too low to act',
  'a janela de novas tentativas acabou': 'the window for new attempts has closed',
  'não vale a ação': "it isn't worth acting",

  // api/simulacao.py _pensando: as linhas sem parte que muda
  'A cobrança passou no dia do vencimento. Não há ciclo para abrir, e a CRAI não cobra nada por isso.':
    "The charge went through on the due date. There's no cycle to open, and CRAI charges nothing for it.",
  'Esta causa não se resolve cobrando de novo: o sistema vai direto para a mensagem.':
    "This cause isn't solved by charging again: the system goes straight to the message.",
  '3 mensagens escritas para este cliente. No modo automático, a recomendada sai sem esperar escolha.':
    '3 messages written for this customer. In automatic mode, the recommended one goes out without waiting for a choice.',
  'Pagamento recuperado depois da mensagem. O ciclo fecha e o valor entra no extrato.':
    'Payment recovered after the message. The cycle closes and the amount goes into the statement.',
  'O prazo de recuperação acabou sem pagamento. O ciclo encerra sem recuperação, e nada é cobrado da empresa.':
    'The recovery window ended with no payment. The cycle closes without recovery, and the company is charged nothing.',

  // simulador.py sem_crai: a explicação "Sem a CRAI" (a de N dias é regra)
  'O cliente revoga a autorização. Sem a CRAI, ninguém fala com ele e a assinatura acaba.':
    'The customer revokes the authorization. Without CRAI, nobody talks to them and the subscription ends.',
  'A cobrança passou no dia do vencimento. Não havia o que recuperar.': 'The charge went through on the due date. There was nothing to recover.',
  'O dinheiro já estava na conta. O próprio banco resolveria na segunda janela do dia do vencimento.':
    "The money was already in the account. The bank itself would have handled it in the due date's second window.",
  'Havia saldo, mas a cobrança não passou nas duas janelas do dia do vencimento. Sem a CRAI, ninguém pede uma nova tentativa e a cobrança se perde.':
    "There were funds, but the charge didn't go through in either window on the due date. Without CRAI, nobody requests a new attempt and the charge is lost.",

  // api/simulacao.py: a retenção simulada
  'Quem decidiu o risco foi o modelo de IA. Pela posição na base, este cliente não está entre os graves nem os preocupantes (ou não tem sinal de abandono: 7 dias sem entrar, ou nenhuma funcionalidade usada), e não houve evento de intenção explícita. O sistema não interveio: oferecer desconto a quem não ia sair só custa margem.':
    "The AI model decided the risk. By position in the base, this customer is neither Severe nor Concerning (or shows no sign of churn: 7 days without logging in, or no feature used), and there was no explicit-intent event. The system didn't step in: offering a discount to someone who wasn't leaving only costs margin.",
  'Quem decidiu o risco foi o modelo de IA, e não há referência de posição na base para comparar este cliente. Sem evento de intenção explícita, o sistema não interveio.':
    "The AI model decided the risk, and there is no position reference in the base to compare this customer against. With no explicit-intent event, the system didn't step in.",
  'O sistema não interveio: com ou sem a CRAI, este cliente segue como está.':
    "The system didn't step in: with or without CRAI, this customer stays as they are.",
  'Sem a CRAI, ninguém perceberia os sinais até o pedido de cancelamento, quando já é tarde para oferecer algo.':
    "Without CRAI, nobody would notice the signals until the cancellation request, when it's too late to offer anything.",

  // churn_voluntary/batch_scoring.py: o motivo sem dado
  'sem dado de atividade — impossível avaliar risco de churn para este cliente': 'no activity data — impossible to assess churn risk for this customer',

  // api/assistente.py: o texto fixo de ajuda e os rótulos dos links (PAGINAS)
  'O assistente está indisponível agora. Os números continuam certos nas páginas do painel: Visão geral, Involuntário e Voluntário.\n\nO que cada página mostra: a Visão geral soma o que foi recuperado e mantido e tem o extrato; o Involuntário lista as cobranças Pix que falharam e o que o sistema fez com cada uma; o Voluntário mostra os clientes em risco e as ofertas; a Configuração tem as regras das mensagens e os prazos de guarda dos dados.':
    'The assistant is unavailable right now. The numbers are still correct on the dashboard pages: Overview, Involuntary and Voluntary.\n\nWhat each page shows: the Overview adds up what was recovered and kept and has the statement; Involuntary lists the Pix charges that failed and what the system did with each one; Voluntary shows the at-risk customers and the offers; Settings has the message rules and the data retention periods.',
  'Ver a visão geral': 'View the overview',
  'Ver o extrato': 'View the statement',
  'Ver o funil': 'View the funnel',
  'Ver o que mais funciona': 'View what works best',
  'Ver a saúde do sistema': 'View system health',
  'Ver o involuntário': 'View involuntary churn',
  'Ver o voluntário': 'View voluntary churn',
  'Ver os clientes em risco': 'View at-risk customers',
  'Ver a comparação': 'View the comparison',
  'Ver a simulação': 'View the simulation',
  'Abrir a configuração': 'Open settings',
  'Dados e privacidade': 'Data and privacy',
  'Ver a aba API': 'View the API tab',
}

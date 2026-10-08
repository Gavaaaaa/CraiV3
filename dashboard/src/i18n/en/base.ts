/** Tradução para o inglês: base. A chave é o texto em português, exatamente como está no código. */
export const base: Record<string, string> = {
  // App.tsx
  'Carregando a página': 'Loading page',

  // components/ui
  'Demonstração': 'Demo',
  'Este bloco ainda usa dados fictícios': 'This block still uses fictional data',
  'Não deu para carregar': "Couldn't load",
  'Os seus dados continuam guardados; só a tela não conseguiu buscá-los agora.': "Your data is still safe; the screen just couldn't fetch it right now.",
  'Tentar de novo': 'Try again',
  'Carregando': 'Loading',
  'Em análise': 'Under review',
  'Em processo': 'In progress',
  'Recuperado': 'Recovered',
  'Encerrado sem recuperação': 'Closed without recovery',

  // components/layout/BuscaDoTopo.tsx
  'Não deu para buscar agora. Tente de novo.': "Couldn't search right now. Try again.",
  'Buscar cliente': 'Search customer',
  'Buscar cliente pelo nome ou pelo identificador': 'Search customer by name or ID',
  'Resultados da busca': 'Search results',
  'Buscando…': 'Searching…',
  'Nada encontrado para “{q}”. A busca procura pelo nome ou pelo identificador.': 'Nothing found for “{q}”. Search looks up name or ID.',
  'Cobranças': 'Charges',
  'Cliente sem cadastro': 'Unregistered customer',
  'Clientes': 'Customers',
  '{valor} por mês': '{valor} per month',
  'Cancelou': 'Canceled',
  'Não contatar': 'Do not contact',

  // components/layout/ModoToggle.tsx
  'Dados reais': 'Real data',
  'Simulação': 'Simulation',
  'Modo dos dados': 'Data mode',
  'Mostrar': 'Show',

  // components/layout/Shell.tsx
  'Pular para o conteúdo': 'Skip to content',

  // components/layout/Sidebar.tsx
  'Simulação do gateway': 'Gateway simulation',
  'Demo': 'Demo',
  'Visão geral': 'Overview',
  'Churn involuntário': 'Involuntary churn',
  'Churn voluntário': 'Voluntary churn',
  'Assistente': 'Assistant',
  'API': 'API',
  'Seções do painel': 'Dashboard sections',
  'CRAI, visão geral': 'CRAI, overview',
  '{rotulo} (plano premium)': '{rotulo} (premium plan)',
  'Plano premium': 'Premium plan',
  'Configuração': 'Settings',

  // components/layout/SinoDoTopo.tsx
  '{n} mensagem que o sistema vai enviar sozinho': '{n} message the system will send automatically',
  '{n} mensagens que o sistema vai enviar sozinho': '{n} messages the system will send automatically',
  'Nenhuma mensagem pendente': 'No pending messages',
  '{n} cobrança esperando a sua escolha de mensagem': '{n} charge awaiting your message choice',
  '{n} cobranças esperando a sua escolha de mensagem': '{n} charges awaiting your message choice',
  'Nenhuma cobrança esperando a sua escolha de mensagem': 'No charges awaiting your message choice',

  // components/layout/Topbar.tsx
  'Dono': 'Owner',
  'Administrador': 'Admin',
  'Membro': 'Member',
  'Olá, {nome}': 'Hello, {nome}',
  'O que a CRAI fez pela sua receita hoje.': "What CRAI did for your revenue today.",
  'Plano essencial': 'Essential plan',
  'Papel do login de desenvolvimento': 'Development login role',
  'Login de desenvolvimento: troque o papel para testar as permissões': 'Development login: switch roles to test permissions',

  // data/http.ts
  'Não foi possível falar com o servidor da CRAI.': "Couldn't reach the CRAI server.",
  'O login de desenvolvimento não existe neste servidor. Ele só funciona com o backend em modo de desenvolvimento.':
    "Development login isn't available on this server. It only works with the backend in development mode.",
  'Não foi possível entrar no servidor da CRAI.': "Couldn't sign in to the CRAI server.",
  'Este ciclo não está mais esperando uma escolha. A mensagem já foi escolhida ou enviada.':
    'This cycle is no longer awaiting a choice. The message has already been chosen or sent.',
  'O prazo de escolha acabou. A mensagem recomendada é enviada pelo sistema.': 'The choice deadline has passed. The system sends the recommended message.',
  'A empresa está no modo automático: a mensagem sai sem esperar escolha.': 'The company is in automatic mode: the message goes out without waiting for a choice.',
  'A empresa já tem o máximo de chaves ativas. Revogue uma para gerar outra.': 'The company already has the maximum number of active keys. Revoke one to generate another.',
  'Crie o cliente fictício antes de cobrar.': 'Create the fictional customer before charging.',
  'Este cliente fictício já foi cobrado. Avance o relógio ou comece outra simulação.':
    'This fictional customer has already been charged. Advance the clock or start another simulation.',
  'Não há simulação em andamento. Crie um cliente fictício para começar.': 'No simulation in progress. Create a fictional customer to start.',
  'A simulação está sendo usada por outra ação. Tente de novo em instantes.': 'The simulation is in use by another action. Try again in a moment.',
  'Este cliente foi anonimizado: os contatos dele foram apagados, e não dá para voltar a contatá-lo.':
    "This customer has been anonymized: their contact details were deleted, and they can't be contacted again.",
  'Não há dado deste identificador na sua empresa. Confira o identificador que a sua base usa.':
    'There is no data for this ID in your company. Check the ID your customer base uses.',
  'Não encontramos este cliente na sua base. Confira o identificador.': "We couldn't find this customer in your base. Check the ID.",
  'Gerar chave faz parte do plano Premium.': 'Generating keys is part of the Premium plan.',
  'A sua empresa chegou ao limite de perguntas ao assistente nesta hora. Tente de novo mais tarde.':
    'Your company has reached the hourly limit of questions to the assistant. Try again later.',
  'O nome parece um dado real (CPF, e-mail, telefone ou chave Pix). Aqui só entra um nome inventado.':
    'The name looks like real data (CPF, email, phone or Pix key). Only a made-up name is allowed here.',
  'A simulação só aceita os campos do formulário. Nenhum outro dado é enviado.': 'The simulation only accepts the form fields. No other data is sent.',
  'Só CSV ou XLSX. Outros formatos não são lidos.': 'CSV or XLSX only. Other formats are not read.',
  'O arquivo está vazio.': 'The file is empty.',
  'O arquivo é grande demais. Divida em duas partes.': 'The file is too large. Split it into two parts.',
  'O arquivo tem linhas demais para uma importação. Divida em duas partes.': 'The file has too many rows for one import. Split it into two parts.',
  'Não foi possível ler o arquivo. Confira se ele abre na sua planilha.': "Couldn't read the file. Check that it opens in your spreadsheet app.",
  'O arquivo só tem o cabeçalho, sem nenhuma linha de cliente.': 'The file only has the header, with no customer rows.',
  'Não foi possível ler o texto do arquivo. Salve como CSV UTF-8 e tente de novo.': "Couldn't read the file's text. Save it as CSV UTF-8 and try again.",
  'O servidor não aceitou a sua sessão. Recarregue a página para entrar de novo.': "The server didn't accept your session. Reload the page to sign in again.",
  'Seu papel não permite esta ação.': "Your role doesn't allow this action.",
  'Não encontramos o que você pediu. Pode ter sido removido ou não ser da sua empresa.':
    "We couldn't find what you asked for. It may have been removed or may not belong to your company.",
  'Esta ação não é mais possível: a situação mudou enquanto a tela estava aberta. Atualize e tente de novo.':
    'This action is no longer possible: things changed while the screen was open. Refresh and try again.',
  'O servidor recusou o arquivo enviado.': 'The server rejected the uploaded file.',
  'O servidor recusou os dados enviados. Confira os campos e tente de novo.': 'The server rejected the submitted data. Check the fields and try again.',
  'Muitas chamadas em pouco tempo. Tente de novo em instantes.': 'Too many requests in a short time. Try again in a moment.',
  'O servidor da CRAI respondeu com erro. Tente de novo em instantes.': 'The CRAI server returned an error. Try again in a moment.',

  // data/api.ts
  'Chave de API {dia}/{mes}/{ano}': 'API key {mes}/{dia}/{ano}',
  'Causa da falha': 'Failure cause',
  'Histórico de pagamento': 'Payment history',
  'Valor da cobrança': 'Charge amount',
  'Aumentou a chance de recuperar': 'Increased the chance of recovery',
  'Reduziu a chance de recuperar': 'Reduced the chance of recovery',
  'Retorno esperado abaixo do custo da ação': 'Expected return below the cost of acting',
  'Cobrança falhou': 'Charge failed',
  'Mensagem escolhida': 'Message chosen',
  'Escolhida pelo sistema: a recomendada, no modo automático.': 'Chosen by the system: the recommended one, in automatic mode.',
  'Escolhida pelo dono.': 'Chosen by the owner.',
  'Mensagem enviada': 'Message sent',
  'Empresa de demonstração': 'Demo company',
  'Canal padrão da empresa': "Company's default channel",
  'Esta simulação não tem mensagem esperando escolha. Atualize a página.': 'This simulation has no message awaiting a choice. Refresh the page.',
  '3 linhas sem e-mail nem telefone: ficam com "Sem canal disponível".': '3 rows with no email or phone: set to "No channel available".',
  'Chave live aceita': 'Live key accepted',
  'crai_live_7f3a autenticou.': 'crai_live_7f3a authenticated.',
  'Webhook respondeu': 'Webhook responded',
  'HTTP 200 em 180 ms, assinatura conferida.': 'HTTP 200 in 180 ms, signature verified.',
  'Evento de teste recebido': 'Test event received',
  'cobranca.falhou (teste) chegou e foi ignorado, como esperado.': 'cobranca.falhou (test) arrived and was ignored, as expected.',
  'Agendar 3 tentativas de cobrança (dias 04/10, 06/10 e 07/10) e, se falharem, enviar uma mensagem de lembrete cordial por WhatsApp.':
    'Schedule 3 charge attempts (Oct 4, Oct 6 and Oct 7) and, if they fail, send a friendly reminder message via WhatsApp.',
  'Causa da falha: saldo insuficiente': 'Failure cause: insufficient funds',
  'Cliente há mais de 2 anos': 'Customer for over 2 years',
  'Bom histórico de pagamento': 'Good payment history',
  'Valor alto para o perfil': 'High amount for the profile',
  'A mensagem foi escolhida por um administrador da empresa em 07/10, não pelo sistema.': 'The message was chosen by a company admin on Oct 7, not by the system.',
}

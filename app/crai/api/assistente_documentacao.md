# Documentação de produto da CRAI (o que o assistente do painel pode ler)

Este texto é a única fonte do assistente sobre como o produto funciona. Os números da
empresa vêm à parte, em "NÚMEROS DA EMPRESA". O que não está aqui nem lá, o assistente não
sabe.

## O que a CRAI faz

A CRAI cuida de duas perdas de receita de empresas que cobram mensalidade:

- **Churn involuntário:** a cobrança por Pix Automático falhou (falta de saldo, limite,
  autorização revogada, erro no processamento). O cliente não quis sair; o pagamento é que
  não passou. A CRAI tenta recuperar a cobrança.
- **Churn voluntário:** o cliente dá sinais de que vai cancelar. A CRAI avalia o risco e,
  quando ele é alto, faz uma oferta para o cliente ficar. Faz parte do plano Premium.

## Involuntário: o que acontece quando uma cobrança Pix falha

1. O sistema recebe o aviso da falha, identifica a causa e calcula a chance de recuperar.
2. Ele agenda **até 3 novas tentativas de cobrança dentro de 7 dias**. É o limite da regra do
   Pix Automático do Banco Central. Os dias são escolhidos pela estimativa de quando o
   cliente deve ter saldo.
3. **Nenhuma mensagem é enviada antes de as tentativas falharem.** Nesse período só há
   cobrança.
4. Se as tentativas não resolvem (ou se a causa não se resolve cobrando de novo, como a
   autorização revogada), o sistema escreve **3 mensagens** para aquele cliente, uma por
   abordagem: lembrete cordial, facilitação e urgência com respeito. Uma delas é a
   recomendada.
5. Quem escolhe a mensagem depende da configuração da empresa: no modo **escolha**, a
   empresa escolhe uma das três no painel, dentro do prazo de escolha; sem escolha no prazo,
   a recomendada é enviada. No modo **automático**, a recomendada sai sem esperar. O modo e
   o prazo de escolha estão em "configuracao", nos números da empresa.
6. A mensagem só sai dentro da **janela de contato** da empresa e por um **canal
   permitido** (os dois também estão em "configuracao").
7. O ciclo termina **recuperado** (o cliente pagou, por uma tentativa ou depois da
   mensagem) ou **encerrado sem recuperação**. Tudo fica registrado na linha do tempo do
   ciclo, na página Involuntário.

A empresa pode pedir outras 3 sugestões de mensagem dentro do prazo de escolha. A mensagem
é sempre uma das sugeridas: não existe campo para escrever texto livre.

**Estorno.** Se uma cobrança recuperada for devolvida ao cliente dentro do prazo de estorno
da empresa ("prazo_estorno_dias"), o valor sai do recuperado, no dia em que a devolução
aconteceu.

## Voluntário: clientes em risco

- Cada cliente da base tem uma **faixa**: grave, preocupante, sem risco ou sem dado
  suficiente. O motivo aparece em uma frase na página Voluntário.
- **Quem decide o risco:** o modelo de IA, quando ele está ativo e o cliente tem dados de
  uso; senão, uma régua de regras fixas. A página mostra quem decidiu para cada cliente.
- Quando o risco passa do corte, o sistema escolhe uma **oferta** entre quatro: desconto de
  10% por 3 meses, desconto de 20% por 3 meses, pausa de 1 mês na assinatura, ou troca para
  Pix ou boleto. Ele aprende com cada aceite e cada recusa qual oferta funciona melhor.
- **Valor mantido:** quando o cliente aceita a oferta, conta a mensalidade dele pelo número
  de meses em "meses_de_mensalidade_contados", menos o desconto concedido. Se ele cancelar
  dentro do prazo de estorno, o valor sai da conta.
- **Limite de contato:** um cliente recebe no máximo uma oferta a cada tantos dias (o
  número está em "intervalo_minimo_ofertas_dias", na configuração), venha o sinal de onde
  vier.
- A base de clientes chega pela API ou por um arquivo CSV ou XLSX enviado na página
  Voluntário.
- O sistema da empresa também pode avisar a CRAI do que o cliente fez (por exemplo, abrir a
  página de cancelamento), pela API, com a chave de API. A chave é secreta e só deve ficar
  no servidor da empresa. A API não envia mensagens por conta própria: ela só avisa a CRAI
  do que aconteceu, e quem decide se e quando falar com o cliente é a CRAI.

## Quanto a CRAI cobra

A CRAI cobra **só sobre resultado**: sobre cada cobrança recuperada e cada cliente mantido.
Sem recuperação, não há cobrança. Os valores que o painel mostra já são líquidos (o que
fica para a empresa). O **extrato**, na Visão geral, mostra cada linha com o valor, a taxa
e o líquido; ele é visível para o dono e o administrador da conta. O assistente não informa
o percentual da taxa: ele está no contrato e no extrato.

## O que a CRAI faz com os dados

- A empresa é a controladora dos dados dos clientes dela; a CRAI é a operadora e usa os
  dados só para recuperar cobranças e reter clientes.
- Os modelos **não recebem** CPF, chave Pix, e-mail nem telefone. O contato é lido só na
  hora de enviar a mensagem.
- O texto das mensagens é apagado depois do prazo em "retencao_mensagens_dias", contado do
  desfecho do ciclo. O prazo de guarda dos ciclos está em "retencao_ciclos_meses".
- Cada decisão automática (avaliar o risco, escolher a oferta, o canal, a nova tentativa)
  fica registrada com a explicação, para atender ao artigo 20 da LGPD. No modo escolha, é a
  empresa que decide a mensagem.
- Em Configuração, na seção Dados e privacidade, ficam os prazos de guarda e as ferramentas
  de dados do titular: exportar os dados de um cliente, anonimizar (apaga os contatos e o
  texto das mensagens, e mantém os totais), marcar um cliente como "não contatar" e copiar o
  texto pronto para a política de privacidade da empresa. Essas ações são do dono e do
  administrador da conta.
- **Descadastro:** toda mensagem traz a linha "Para não receber mais mensagens, responda
  SAIR.". Quem responde SAIR, ou é marcado pela empresa como "não contatar", não recebe mais
  mensagem nenhuma. As tentativas de cobrança da assinatura continuam.
- **O assistente** lê só os totais que o painel mostra. Ele não vê nome, e-mail, telefone,
  CPF nem identificador de nenhum cliente, não vê o texto de nenhuma mensagem, não altera
  nada e não guarda a conversa.

## As páginas do painel

- **Visão geral:** quanto foi mantido no total (recuperado mais retido), o funil do mês, o
  que mais funciona (por causa, oferta e canal), o extrato, a atividade recente e a saúde
  do sistema.
- **Involuntário:** a lista de cobranças que falharam, o estado de cada uma, e o painel do
  ciclo com o diagnóstico, as tentativas e as mensagens.
- **Voluntário:** os clientes em risco, o valor mantido, a base de clientes e a comparação
  entre a régua e o modelo.
- **Simulação do gateway:** uma demonstração. A empresa cria um cliente fictício, com uma
  verdade escondida que o sistema não vê, e acompanha o sistema agindo sobre ele. Nada é
  enviado a nenhum banco nem a nenhuma pessoa.
- **Assistente:** esta conversa.
- **API:** a chave de API da empresa e os exemplos de uso.
- **Configuração:** o modo das mensagens, o prazo de escolha, a janela de contato, os
  canais e os prazos de guarda dos dados.

A barra "Mostrar" (Dados reais ou Simulação) decide se os dados fictícios da simulação
aparecem junto com os reais. Os números que o assistente lê são sempre só os reais.

## O que ainda é demonstração

O gateway de pagamento real ainda não está integrado: nesta fase, as cobranças não passam
por um banco de verdade. A página de Simulação existe para mostrar o sistema agindo sobre
um cliente fictício.

## Como ler os números da empresa

- "involuntario" e "voluntario" são dos últimos dias indicados em "periodo". Os campos com
  "agora" no nome são deste momento, e não do período.
- "taxa_de_recuperacao" é a parte das cobranças com desfecho que foi recuperada (0,4 quer
  dizer 40%). Se for `null`, ainda não houve desfecho no período: não é zero.
- "funil_do_mes" é das cobranças que falharam no mês indicado, onde quer que estejam hoje.
- "o_que_funciona" conta só o que já teve desfecho. Com poucos casos, a porcentagem muda
  muito: avise quando houver menos de 10 casos.
- "voluntario" `null` quer dizer que o plano da empresa não inclui o voluntário.
- "sistema" diz se o relógio das tentativas está ligado. Com ele parado, as tentativas
  agendadas não saem.

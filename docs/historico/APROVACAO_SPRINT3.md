<!--
Documento do CEO, transcrito na íntegra e sem edição.

Está no repositório por cobrança da auditoria A1-r8: a linha `§4.6` do README
cita a "meta de 0,78–0,85 fixada na aprovação do Sprint 3" e essa aprovação só
existia fora do repositório, o que torna a afirmação não-auditável. Um auditor
que recebe o diff e o repositório não tinha como conferir a origem do número.

As três condições abaixo são BLOQUEANTES para fechar o Sprint 3. Nenhuma foi
cumprida ainda — o Sprint 3 está parado pelo gate GA1 (ver sprints.md).
-->

Aprovado — proveniência dos dados (seção 2 do plano) liberada. Pode destravar Sprint 3 e 4, com as condições abaixo.

Concordo com o diagnóstico: o problema é sinal fraco na fórmula causal, não vazamento — o caminho certo é fortalecer os coeficientes, não adicionar ruído. Mire o AUC em 0,78–0,85 (folga dos dois lados do intervalo [0,70; 0,92] do gate G3).

Antes de seguir, três condições:

1. Documentação por coeficiente, não por lote. Todo coeficiente que você fortalecer entra no DATA_CARD.md com a hipótese de negócio que o justifica — igual ao que já fez para os existentes. Não me mostre só "AUC entrou na faixa"; me mostre a tabela de antes/depois de cada coeficiente alterado com o motivo.

2. Resolva o termo do R$ 500 explicitamente, não deixe morrer em silêncio. Se ele ficou sem efeito em 97% das linhas depois da calibração, escolha um caminho e declare qual foi:
   - recalibrar o limiar para um valor que faça sentido na distribuição real da Olist (ex.: talvez o percentil 75 ou 90 dos valores reais, não R$ 500 fixo), ou
   - remover o termo do modelo causal e documentar por que ele deixou de ser um fator relevante nesta base.
   Não me traga a opção de "deixar como está, já que não atrapalha" — um termo morto na fórmula é uma regra de negócio que a documentação afirma existir e o código não cumpre.

3. Faça a suavização do histograma de dia-do-mês antes de rodar o gate final. Você já identificou que 300 pontos em 31 dias dá ruído de amostragem baixo (~10/dia) que pode virar "sazonalidade" falsa — aplique a suavização e rode o teste de fidelidade (KS) de novo depois disso, não antes.

Quando terminar, me mostre: a tabela de coeficientes alterados com justificativa, o resultado do gate G3 completo (KS, χ², AUC, anti-cópia, reprodutibilidade), e a decisão tomada sobre o termo do R$ 500. Só considero o Sprint 3 fechado com isso na mão.

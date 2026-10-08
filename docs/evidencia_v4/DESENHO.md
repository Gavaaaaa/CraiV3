# Classificador de falha v4: o desenho

**Data:** 08/10/2026
**Situação:** escrito e aprovado antes de qualquer medição de modelo. O que mudou depois está na última seção, com o motivo.
**O que é:** uma base nova para o classificador de falha, em que a cobrança recupera por um mecanismo, e não por sorteio de fórmula. É experimento: nada é promovido.

## O problema que isto ataca

Na base v2, o modelo de produção marca 82% das cobranças como recuperáveis e erra mais da metade (precisão de 46,2% com recall de 93,8%). Em 07/10 foi medido que o oráculo do gerador tem a mesma precisão: o limite é o rótulo, não o modelo. Na v2 o rótulo é uma moeda viciada por uma fórmula, e quase toda cobrança tem chance intermediária.

## O que muda e o que não muda

| | Base v2 | Base v4 |
|---|---|---|
| Cobranças | 120.000, de 33.177 clientes | As mesmas, na mesma ordem |
| Features (11) | | As mesmas, com os mesmos valores |
| População | Compartilhada (120.000 clientes) | A mesma: mesmo `customer_id`, mesmo MRR |
| Rótulo `recovered` | Sorteio de uma fórmula sobre as features | Mecanismo: saldo na data da nova tentativa, causa e método |
| Colunas a mais | | `oculto_*`, que o treino recusa |

Como as features são as mesmas, um modelo treinado na v4 entra no backend sem mudar nada em quem monta a entrada.

## O mecanismo

Para cada cobrança que falhou no dia D:

- **Saldo.** O saldo diário do cliente vem da mesma simulação de fluxo de caixa que gera a base de liquidez (salário de CLT, notas de PJ, entradas de freelancer, atrasos e gastos imprevistos). Está em múltiplos da mensalidade. O modelo nunca vê o saldo.
- **Tamanho da fatura.** A fatura precisa de saldo igual a `fatura ÷ mensalidade`: 1 na mensal, 10 na anual, menos de 1 na proporcional.
- **Novas tentativas.** Até 3, em 7 dias (regra do BACEN), nos dias D+2, D+4 e D+6. Quem já está mais adiante na escada tem menos tentativas: `3, 2, 1, 0` para o 1º, 2º, 3º e 4º degrau.
- **Saldo insuficiente é coerente com o caixa.** Se a simulação tinha saldo em D, um gasto naquele dia leva o saldo para abaixo da fatura, e o desconto continua nos dias seguintes.

| Método | Causa | Quando recupera |
|---|---|---|
| Pix Automático | Saldo insuficiente | Se há saldo em alguma nova tentativa |
| Pix Automático | Erro de processamento | Idem; cada tentativa com saldo passa em 90% das vezes |
| Pix Automático | Recusa genérica | Idem; cada tentativa com saldo passa em 50% das vezes |
| Pix Automático | Limite excedido | Só se o cliente sobe o limite (20% + 40% × satisfação) e há saldo em algum dia da janela |
| Pix Automático | Autorização revogada | Só se o cliente autoriza de novo (5% + 25% × satisfação) e há saldo na janela |
| Boleto | Qualquer | Só se o cliente paga (30% + 55% × saúde financeira) e há saldo na janela |

Por fim, 2% das cobranças têm o desfecho trocado por moeda honesta, como na v2.

A satisfação e a saúde financeira são variáveis ocultas da população. O modelo não as vê.

## Parâmetros: fixados antes, sem ajuste pelo resultado

Todos os números da tabela acima foram escolhidos antes de treinar. A única conferência feita na base foi a **taxa de recuperação**, contra a faixa declarada de 30% a 50% (a da v2 é 39,6%). Deu 36,5% no rascunho e 36,6% na base final (entre um e outro mudou só a organização dos sorteios, para o rótulo de um cliente não depender dos outros), então nenhum parâmetro foi mexido.

Regra para o que vier: se algum parâmetro do gerador for alterado depois de ver uma métrica de modelo, a alteração entra no relatório com o antes e o depois. Ajustar o gerador até o número ficar bonito é o que este experimento não pode fazer.

## O que será medido

As mesmas medições dos outros modelos, no mesmo holdout por cliente (20%, semente 42):

1. AUC, Brier e erro de calibração.
2. Validação cruzada por cliente, em 5 partes (média e desvio).
3. Permutação do rótulo, 3 sementes (tem de cair para perto de 0,50).
4. Ablação: a queda da AUC ao tirar cada feature.
5. O limiar pela mesma regra de hoje: o maior com recall de pelo menos 90%, escolhido fora do teste.
6. A matriz de confusão no holdout, nesse limiar e no limiar em uso (0,25).
7. Precisão em recall fixo (93,8%, 90%, 80%, 70%, 50%), para comparar com a tabela da v2.
8. Faixas do score (alta, média, baixa) com a taxa real de recuperação de cada uma.
9. O teto de quem soubesse o saldo (a AUC de `oculto_p_recuperacao`).
10. O modelo de produção de hoje aplicado à base v4, sem retreinar.

Dois cenários de features:

- **A, o contrato de hoje:** as 11 features. É o que poderia ser promovido sem mexer no backend.
- **B, com o que o backend conseguiria fornecer:** as 11, mais o perfil do pagador (CLT, PJ, freelancer) e a razão entre a fatura e o ticket médio do cliente. Mostra quanto se ganharia com uma mudança pequena em quem monta a entrada.

## Critérios, escritos antes

- **O experimento é válido** se: a permutação do rótulo cai para perto de 0,50; nenhuma coluna `oculto_` entra na matriz de treino (o treino aborta se entrar); as features são idênticas às da v2; a taxa de recuperação fica na faixa.
- **A base nova resolve o problema declarado** se, com recall de pelo menos 90% no holdout, a precisão do cenário A ficar acima da da v2 (47,7% com recall de 90%) por mais que o desvio da validação cruzada. Não há alvo numérico prometido.
- **Promover não faz parte deste experimento.** É decisão à parte, depois do relatório e do portão de testes.

## O que este experimento não prova

- A base continua sintética. A precisão sobe porque o rótulo novo é mais previsível, e fomos nós que desenhamos o mecanismo. Isso mostra que o modelo aprende o mecanismo; não diz nada sobre cobranças reais.
- O resultado na v4 e o resultado na v2 são em rótulos diferentes. A comparação "antes e depois" é entre duas bases, não entre dois modelos na mesma base.
- Para os clientes que também estão na tabela de liquidez, o saldo simulado aqui é outra realização do mesmo gerador (mesmo perfil, mesmos parâmetros), não a mesma série.

## O que mudou depois do desenho

Nenhum parâmetro do mecanismo foi alterado. Estas quatro coisas entraram depois de ver as medições de A e B, e por isso estão marcadas onde aparecem:

1. **Cenário C (exploratório).** As features de B, mais a previsão de liquidez do Módulo 3 para os dias D+2, D+4 e D+6. Entrou para responder "o que reduziria os falsos positivos", depois que o critério não foi satisfeito. Está fora do critério.
2. **Calendário fixo do caixa.** A revisão independente achou que o rótulo de um cliente dependia do intervalo de datas da tabela. O caixa passou a ser simulado num calendário fixo (01/04/2026 a 21/09/2026). A base gerada ficou idêntica, byte a byte.
3. **A v2 medida pelo mesmo método.** A coluna da v2 passou a ser medida pelo próprio script de treino da v4, com as mesmas funções, em vez de copiada do relatório anterior.
4. **A comparação sem a taxa de base.** O critério compara precisões entre bases com taxas de recuperação diferentes (40,5% e 36,7% no teste). A taxa de falsos positivos, que não depende disso, passou a ser reportada ao lado. O critério declarado não foi reescrito: continua valendo como foi escrito, e o resultado dele é reportado como está.

## Onde fica cada coisa

| O quê | Onde |
|---|---|
| O mecanismo | `app/crai/ml/classificador_v4.py` |
| Gerar a base | `python -m crai.scripts.gerar_base_v4_classificador` (a partir de `app/`), grava em `app/data/v4/` |
| Treinar e medir | `python -m crai.scripts.treinar_classificador_v4`, grava em `app/models/v4/` |
| Testes | `app/tests/test_classificador_v4.py` |
| Medições | `docs/evidencia_v4/metricas_v4.json` e `LEIA.md` |

"""crai/dunning/legacy_card/ — Retentativa de cartão, fora do pipeline ativo.

Este pacote é **código preservado, não código morto por acidente**: ele guarda a
lógica de recobrança automática de cartão de crédito/débito que rodava no
pipeline principal até a Fase 3.

Por que saiu do fluxo ativo
---------------------------
A Fase 3 definiu que o sistema de cobrança da CRAI é exclusivamente **Pix
Automático**. Cartão não está em uso real no projeto. Manter os dois caminhos
de retentativa vivos dentro do mesmo grafo significaria manter permanentemente
uma superfície onde um erro de roteamento aplicaria a política errada — e as
duas políticas são incompatíveis por natureza:

    cartão  : backoff exponencial livre, sem teto de tentativas
    Pix     : no máximo 3 tentativas em 7 dias corridos, valor original (BACEN)

Aplicar backoff exponencial a uma cobrança Pix violaria a regulação. Como o
caminho de cartão não tem uso real hoje, a decisão foi retirá-lo do pipeline em
vez de sustentar essa coexistência indefinidamente.

O que continua funcionando
--------------------------
O endpoint `/webhooks/stripe` segue no ar, com a validação de assinatura HMAC
da Fase 1. Ele apenas **registra** o evento e sinaliza nos logs, com o prefixo
`[CARTAO-DESATIVADO]`, que a recobrança automática está fora do pipeline ativo.
Nenhum evento de cartão é silenciosamente descartado.

Como reativar no futuro
-----------------------
1. Implementar `parse_card_event()` num adapter de cartão em
   `crai/integrations/payment_gateway.py` (a assinatura já existe na ABC).
2. Reintroduzir o nó `schedule_retry_card` (ver `card_retry.py` neste pacote)
   no grafo de `crai/agent/main_agent.py`, com uma aresta condicional em
   `payment_method` — o campo de estado que faz esse roteamento continua no
   `AgentState` e é preenchido na entrada do pipeline.
3. Voltar `_run_involuntary_pipeline` a ser chamado por `/webhooks/stripe`.

Nada aqui é importado pelo fluxo ativo.
"""

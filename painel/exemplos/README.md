# painel/exemplos/ — bases de clientes para demonstração

Quatro CSV para o `POST /clientes/importar`. As três primeiras são geradas por
`python -m crai.scripts.gerar_bases_demo` (a partir de `app/`), com semente
fixa: rodar de novo produz arquivos idênticos. UTF-8 com BOM, separador `;`,
decimal com vírgula; abrem no Excel com dois cliques.

| Arquivo | O que é | Perfil de uso | Clientes-âncora |
|---|---|---|---|
| `base_uso_diario.csv` | SaaS de uso intenso, 500 clientes | mediana de 1 dia sem login, uso alto (mediana 12 funcionalidades). Aqui, 7 dias sem login já é sinal. | `ANCORA-07-A`, `ANCORA-07-B` (7 dias), `ANCORA-20-A`, `ANCORA-20-B` (20 dias) |
| `base_uso_mensal.csv` | SaaS de uso esporádico, um fechamento por mês, 500 clientes | mediana de 24 dias sem login, uso baixo (mediana 3). Aqui, 7 dias sem login é rotina. | os mesmos quatro, com os mesmos números |
| `base_saudavel.csv` | SaaS em que ninguém está em risco, 500 clientes | ninguém passou de 4 dias sem login, todo mundo usa o produto (mediana 14). A lista sai ordenada e **ninguém** vira alto/crítico. | nenhum, de propósito |
| `base_exemplo_clientes.csv` | base de 500 clientes vinda do mvp-crai, com colunas próprias | mediana de 1 dia sem login | nenhum |

Os âncoras têm o MESMO MRR (R$ 300,00), o MESMO perfil (CLT) e o MESMO uso (3
funcionalidades) nas duas bases: pontuados numa e na outra, são eles que
mostram na tela que a régua mudou. Cada uma das três bases geradas tem 12
linhas sem `days_since_last` nem `features_used_30d`, que voltam como
`dado_insuficiente`.

A `base_exemplo_clientes.csv` usa nomes próprios de coluna e só importa com
este `mapeamento`:

```json
{"id_cliente": "customer_id_externo", "perfil_pagador": "billing_profile",
 "dias_sem_uso": "days_since_last", "features_usadas_30d": "features_used_30d"}
```

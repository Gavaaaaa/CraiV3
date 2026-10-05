/**
 * Um ciclo de exemplo, na forma exata do backend: a resposta de `GET /ciclos/2` e a linha dele
 * em `GET /ciclos`, capturadas do backend local em 04/10/2026 com a semente de demonstração
 * (`docs/interno/semear_dashboard_demo.py`, só dados fictícios). Serve aos testes de tela que
 * abrem o painel de um ciclo em MODO REAL.
 */
import type { CicloApi, CicloDetalheApi } from '../data/adaptadores'

export const LINHA_DO_CICLO = {
  "id": 2,
  "simulado": false,
  "status": "em_processo",
  "estado": "aguardando_escolha",
  "id_recorrencia": "RN_demo_escolha_tel_1004200652",
  "cliente_nome": "Clínica Horizonte (fictícia)",
  "valor_cobranca": 4900.0,
  "valor_liquido": null,
  "causa": "authorization_revoked",
  "causa_legivel": "Autorização revogada",
  "tentativas_executadas": 0,
  "aberto_em": "2026-10-04T20:06:52-03:00",
  "atualizado_em": "2026-10-04T20:06:52-03:00",
  "desfecho_em": null,
  "motivo_descarte": null,
  "motivo_perdido": null,
  "mensagem": null,
  "estorno": null,
  "estorno_parcial": false,
  "motivo_encerramento": null
} as unknown as CicloApi

export const DETALHE_DO_CICLO = {
  "ciclo": {
    "id": 2,
    "simulado": false,
    "status": "em_processo",
    "estado": "aguardando_escolha",
    "id_recorrencia": "RN_demo_escolha_tel_1004200652",
    "cliente_nome": "Clínica Horizonte (fictícia)",
    "valor_cobranca": 4900.0,
    "valor_liquido": null,
    "causa": "authorization_revoked",
    "causa_legivel": "Autorização revogada",
    "tentativas_executadas": 0,
    "aberto_em": "2026-10-04T20:06:52-03:00",
    "atualizado_em": "2026-10-04T20:06:52-03:00",
    "desfecho_em": null,
    "motivo_descarte": null,
    "motivo_perdido": null,
    "mensagem": null,
    "estorno": null,
    "estorno_parcial": false,
    "motivo_encerramento": null,
    "janela_inicio": "2026-10-04T20:06:52-03:00",
    "janela_fim": "2026-10-11T20:06:52-03:00"
  },
  "diagnostico": {
    "decidido_em": "2026-10-04T23:06:52+00:00",
    "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 31/100; probabilidade de recuperação 31%; retorno esperado R$ 2.303,56; intervenção recomendada. Pesaram, nesta ordem: causa da falha de pagamento: autorização de recorrência revogada (reduziu a chance), 19 meses como cliente (aumentou a chance), histórico de pagamento 92% positivo (aumentou a chance), 2 falhas de pagamento nos últimos 90 dias (reduziu a chance), cobrança de R$ 4.900,00 (reduziu a chance). A decisão foi tomada pelo modelo de diagnóstico de falha de pagamento (failure_classifier), versão 2026-09-27T19:57:57#e347fbc3d391.",
    "com_modelo": true,
    "contribuicoes": [
      {
        "fator": "causa da falha de pagamento: autorização de recorrência revogada",
        "efeito": "reduziu a chance de recuperar"
      },
      {
        "fator": "19 meses como cliente",
        "efeito": "aumentou a chance de recuperar"
      },
      {
        "fator": "histórico de pagamento 92% positivo",
        "efeito": "aumentou a chance de recuperar"
      },
      {
        "fator": "2 falhas de pagamento nos últimos 90 dias",
        "efeito": "reduziu a chance de recuperar"
      },
      {
        "fator": "cobrança de R$ 4.900,00",
        "efeito": "reduziu a chance de recuperar"
      }
    ],
    "desconto_por_anomalia": null
  },
  "mensagens": [
    {
      "rodada": 1,
      "abordagem": "lembrete_cordial",
      "texto": "Olá, Clínica! Lembrete rápido: a autorização do Pix Automático foi cancelada e a mensalidade de R$ 4900.00 ficou em aberto. Você pode pagar por boleto: https://pay.crai.ai/boleto/RN_demo_\nPara não receber mais mensagens, responda SAIR.\nEsta é uma mensagem automática.",
      "canal": "whatsapp",
      "motivo_canal": "contato_da_base",
      "recomendada": false,
      "escolhida": false,
      "nao_entregavel": false,
      "gerada_em": "2026-10-04T20:06:52-03:00",
      "enviada_em": null
    },
    {
      "rodada": 1,
      "abordagem": "facilitacao",
      "texto": "Olá, Clínica! Para facilitar: reative a recorrência no app do seu banco, ou pague os R$ 4900.00 por boleto neste link: https://pay.crai.ai/boleto/RN_demo_\nPara não receber mais mensagens, responda SAIR.\nEsta é uma mensagem automática.",
      "canal": "whatsapp",
      "motivo_canal": "contato_da_base",
      "recomendada": true,
      "escolhida": false,
      "nao_entregavel": false,
      "gerada_em": "2026-10-04T20:06:52-03:00",
      "enviada_em": null
    },
    {
      "rodada": 1,
      "abordagem": "urgencia_respeitosa",
      "texto": "Olá, Clínica! Com a autorização do Pix Automático cancelada, a mensalidade de R$ 4900.00 continua em aberto. Pedimos que regularize assim que possível por boleto: https://pay.crai.ai/boleto/RN_demo_\nPara não receber mais mensagens, responda SAIR.\nEsta é uma mensagem automática.",
      "canal": "whatsapp",
      "motivo_canal": "contato_da_base",
      "recomendada": false,
      "escolhida": false,
      "nao_entregavel": false,
      "gerada_em": "2026-10-04T20:06:52-03:00",
      "enviada_em": null
    }
  ],
  "modo_mensagem": "escolha",
  "escolha_ate": "2026-10-05T04:06:52-03:00",
  "escolhida_por": null,
  "linha_do_tempo": [
    {
      "quando": "2026-10-04T23:06:52+00:00",
      "tipo": "decisao",
      "dados": {
        "tipo_decisao": "risco",
        "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 31/100; probabilidade de recuperação 31%; retorno esperado R$ 2.303,56; intervenção recomendada. Pesaram, nesta ordem: causa da falha de pagamento: autorização de recorrência revogada (reduziu a chance), 19 meses como cliente (aumentou a chance), histórico de pagamento 92% positivo (aumentou a chance), 2 falhas de pagamento nos últimos 90 dias (reduziu a chance), cobrança de R$ 4.900,00 (reduziu a chance). A decisão foi tomada pelo modelo de diagnóstico de falha de pagamento (failure_classifier), versão 2026-09-27T19:57:57#e347fbc3d391."
      }
    },
    {
      "quando": "2026-10-04T23:06:52+00:00",
      "tipo": "decisao",
      "dados": {
        "tipo_decisao": "retentativa",
        "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema decidiu como tratar a cobrança que falhou: mensagem com link de pagamento. Foram considerados: causa da falha de pagamento: autorização de recorrência revogada, pontuação de recuperação 31/100, retorno esperado da intervenção R$ 2.303,56, sem anomalia de comportamento, meio de pagamento Pix Automático, 0 tentativas de cobrança já usadas, limite de 3 tentativas na janela. A decisão veio da regra 'decide_recovery', sem modelo treinado: 'Autorização revogada' não se resolve por retentativa — a autorização de recorrência foi revogada — sem mandato não existe cobrança a reenviar; é preciso reautorizar. Contatar com mensagem personalizada; urgência alta pelo score/anomalia."
      }
    },
    {
      "quando": "2026-10-04T20:06:52-03:00",
      "tipo": "abertura",
      "dados": {
        "origem": "webhook",
        "causa_legivel": "Autorização revogada",
        "janela_inicio": "2026-10-04T20:06:52-03:00",
        "janela_fim": "2026-10-11T20:06:52-03:00"
      }
    },
    {
      "quando": "2026-10-04T20:06:52-03:00",
      "tipo": "diagnostico",
      "dados": {
        "estrategia": "mensagem_pagamento",
        "recovery_score": 31.0,
        "p_recovery": 0.3139
      }
    },
    {
      "quando": "2026-10-04T20:06:52-03:00",
      "tipo": "sugestoes_geradas",
      "dados": {
        "rodada": 1,
        "recomendada": "facilitacao",
        "canal": "whatsapp"
      }
    }
  ],
  "trilha_ambigua": false
} as unknown as CicloDetalheApi

/** O mesmo ciclo depois de o pagamento ser confirmado (o que o painel deve passar a mostrar). */
export function cicloRecuperado(): { linha: CicloApi; detalhe: CicloDetalheApi } {
  const mudou = { status: 'recuperado', estado: 'recuperado', valor_liquido: 4165 }
  return {
    linha: { ...LINHA_DO_CICLO, ...mudou } as CicloApi,
    detalhe: { ...DETALHE_DO_CICLO, ciclo: { ...DETALHE_DO_CICLO.ciclo, ...mudou }, escolha_ate: null } as CicloDetalheApi,
  }
}

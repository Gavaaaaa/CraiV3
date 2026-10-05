/**
 * Respostas DE VERDADE do backend para a simulação do gateway, capturadas rodando as rotas
 * (`GET /simulacao`, `POST /simulacao/cobrar`, `POST /simulacao/avancar`, `POST /simulacao/retencao`)
 * com o cliente fictício "Ana Souza". Servem de contrato para os adaptadores e para as telas:
 * se o backend mudar a forma, este arquivo é regravado e os testes mostram o que quebrou.
 *
 * As datas são as do dia da captura; os testes não dependem delas.
 */
import type { CicloApi, RetencaoSimuladaApi, SimulacaoApi } from '../data/adaptadores'

/** Empresa que nunca simulou. */
export const SIM_VAZIA = {
  "existe": false,
  "relogio": null,
  "cliente": null,
  "id_recorrencia": null,
  "cobranca": null,
  "ciclo": null,
  "tentativas": [],
  "proxima_acao": null,
  "desfecho": null,
  "pensando": [],
  "sem_crai": null,
  "modo_mensagem": "automatico",
  "prazo_escolha_horas": 8
} as unknown as SimulacaoApi

/** A cobrança do dia falhou: ciclo aberto, 3 tentativas agendadas. */
export const SIM_COBRADA = {
  "existe": true,
  "relogio": {
    "agora": "2026-10-04T16:14:26-03:00",
    "iniciado_em": "2026-10-04T16:14:26-03:00"
  },
  "cliente": {
    "nome": "Ana Souza",
    "mensalidade": 890.0,
    "perfil": "clt",
    "verdade": {
      "dias_ate_saldo": 8,
      "chance_pagar": 1.0,
      "vai_revogar": false
    }
  },
  "id_recorrencia": "RN_sim_e961dcb93f_1",
  "cobranca": {
    "feita": true,
    "em": "2026-10-04T16:14:26-03:00",
    "resultado": "falhou",
    "causa": "insufficient_funds",
    "causa_legivel": "Saldo insuficiente"
  },
  "ciclo": {
    "ciclo": {
      "id": 9000000000001,
      "simulado": true,
      "status": "em_analise",
      "estado": "recobrando",
      "id_recorrencia": "RN_sim_e961dcb93f_1",
      "cliente_nome": "Ana Souza",
      "valor_cobranca": 890.0,
      "valor_liquido": null,
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "tentativas_executadas": 0,
      "aberto_em": "2026-10-04T16:14:26-03:00",
      "atualizado_em": "2026-10-04T16:14:26-03:00",
      "desfecho_em": null,
      "motivo_descarte": null,
      "motivo_perdido": null,
      "mensagem": null,
      "estorno": null,
      "estorno_parcial": false,
      "motivo_encerramento": null,
      "janela_inicio": "2026-10-04T16:14:26-03:00",
      "janela_fim": "2026-10-11T16:14:26-03:00"
    },
    "diagnostico": {
      "decidido_em": "2026-10-04T19:14:26+00:00",
      "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 68/100; probabilidade de recuperação 68%; retorno esperado R$ 602,94; intervenção recomendada. Pesaram, nesta ordem: histórico de pagamento 78% positivo (aumentou a chance), 11 meses como cliente (aumentou a chance), causa da falha de pagamento: saldo insuficiente (aumentou a chance), cobrança de R$ 890,00 (aumentou a chance), 1 falhas de pagamento nos últimos 90 dias (aumentou a chance). A decisão foi tomada pelo modelo de diagnóstico de falha de pagamento (failure_classifier), versão 2026-09-27T19:57:57#e347fbc3d391.",
      "com_modelo": true,
      "contribuicoes": [
        {
          "fator": "histórico de pagamento 78% positivo",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "11 meses como cliente",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "causa da falha de pagamento: saldo insuficiente",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "cobrança de R$ 890,00",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "1 falhas de pagamento nos últimos 90 dias",
          "efeito": "aumentou a chance de recuperar"
        }
      ],
      "desconto_por_anomalia": null
    },
    "mensagens": [],
    "modo_mensagem": "escolha",
    "escolha_ate": null,
    "escolhida_por": null,
    "linha_do_tempo": [
      {
        "quando": "2026-10-04T16:14:26-03:00",
        "tipo": "abertura",
        "dados": {
          "origem": "webhook",
          "causa_legivel": "Saldo insuficiente",
          "janela_inicio": "2026-10-04T16:14:26-03:00",
          "janela_fim": "2026-10-11T16:14:26-03:00"
        }
      },
      {
        "quando": "2026-10-04T16:14:26-03:00",
        "tipo": "diagnostico",
        "dados": {
          "estrategia": "retry_automatico",
          "recovery_score": 68.0,
          "p_recovery": 0.6775
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "risco",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 68/100; probabilidade de recuperação 68%; retorno esperado R$ 602,94; intervenção recomendada. Pesaram, nesta ordem: histórico de pagamento 78% positivo (aumentou a chance), 11 meses como cliente (aumentou a chance), causa da falha de pagamento: saldo insuficiente (aumentou a chance), cobrança de R$ 890,00 (aumentou a chance), 1 falhas de pagamento nos últimos 90 dias (aumentou a chance). A decisão foi tomada pelo modelo de diagnóstico de falha de pagamento (failure_classifier), versão 2026-09-27T19:57:57#e347fbc3d391."
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "retentativa",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema decidiu como tratar a cobrança que falhou: nova tentativa automática de cobrança. Foram considerados: causa da falha de pagamento: saldo insuficiente, pontuação de recuperação 68/100, retorno esperado da intervenção R$ 602,94, sem anomalia de comportamento, meio de pagamento Pix Automático, 0 tentativas de cobrança já usadas, limite de 3 tentativas na janela. A decisão veio da regra 'decide_recovery', sem modelo treinado: 'Saldo insuficiente' costuma ser resolvido por nova tentativa de cobrança na janela regulada do BACEN (0/3 tentativas usadas), mirando a liquidez prevista (Módulo 3) — insistir aqui tem retorno esperado positivo."
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "retentativa",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema decidiu como tratar a cobrança que falhou: datas pela distribuição uniforme na janela; janela regulada até 11/10/2026. Foram considerados: 0 tentativas de cobrança já usadas, limite de 3 tentativas na janela, cobrança de R$ 890,00, meio de pagamento Pix Automático, vencimento em 04/10/2026. A decisão veio da regra 'PixAutomaticoRetryPolicy.fallback_uniforme', sem modelo treinado."
        }
      },
      {
        "quando": "2026-10-05T16:14:26-03:00",
        "tipo": "tentativa_agendada",
        "dados": {
          "numero": 1
        }
      },
      {
        "quando": "2026-10-08T16:14:26-03:00",
        "tipo": "tentativa_agendada",
        "dados": {
          "numero": 2
        }
      },
      {
        "quando": "2026-10-11T16:14:26-03:00",
        "tipo": "tentativa_agendada",
        "dados": {
          "numero": 3
        }
      }
    ],
    "trilha_ambigua": false
  },
  "tentativas": [
    {
      "numero": 1,
      "agendada_para": "2026-10-05T16:14:26-03:00",
      "disparada_em": null,
      "resultado": "pendente",
      "resultado_em": null,
      "causa": null,
      "causa_legivel": null,
      "motivo_cancelamento": null
    },
    {
      "numero": 2,
      "agendada_para": "2026-10-08T16:14:26-03:00",
      "disparada_em": null,
      "resultado": "pendente",
      "resultado_em": null,
      "causa": null,
      "causa_legivel": null,
      "motivo_cancelamento": null
    },
    {
      "numero": 3,
      "agendada_para": "2026-10-11T16:14:26-03:00",
      "disparada_em": null,
      "resultado": "pendente",
      "resultado_em": null,
      "causa": null,
      "causa_legivel": null,
      "motivo_cancelamento": null
    }
  ],
  "proxima_acao": {
    "quando": "2026-10-05T16:14:26-03:00",
    "descricao": "Tentativa 1",
    "tipo": "tentativa"
  },
  "desfecho": null,
  "pensando": [
    "Causa da falha: saldo insuficiente.",
    "Chance de recuperar: 68%.",
    "Dia provável de saldo: 12/10. Perfil de recebimento estimado pelo sistema: PJ.",
    "Plano: 3 tentativas dentro de 7 dias, nos dias 05/10, 08/10 e 11/10. Nenhuma mensagem antes de todas falharem."
  ],
  "sem_crai": {
    "resultado": "perdido",
    "explicacao": "Sem a CRAI, o banco só tenta nas duas janelas do dia do vencimento. O dinheiro entra em 8 dias, então a cobrança se perde e a empresa precisa correr atrás por conta própria."
  },
  "modo_mensagem": "escolha",
  "prazo_escolha_horas": 8,
  "resposta_a_mensagem": null,
  "chance_recuperar": 0.6775,
  "dia_provavel_saldo": "2026-10-12T10:00:00-03:00"
} as unknown as SimulacaoApi

/** As 3 tentativas falharam: 3 mensagens esperando a escolha da empresa. */
export const SIM_MENSAGENS = {
  "existe": true,
  "relogio": {
    "agora": "2026-10-11T16:14:27-03:00",
    "iniciado_em": "2026-10-04T16:14:26-03:00"
  },
  "cliente": {
    "nome": "Ana Souza",
    "mensalidade": 890.0,
    "perfil": "clt",
    "verdade": {
      "dias_ate_saldo": 8,
      "chance_pagar": 1.0,
      "vai_revogar": false
    }
  },
  "id_recorrencia": "RN_sim_e961dcb93f_1",
  "cobranca": {
    "feita": true,
    "em": "2026-10-04T16:14:26-03:00",
    "resultado": "falhou",
    "causa": "insufficient_funds",
    "causa_legivel": "Saldo insuficiente"
  },
  "ciclo": {
    "ciclo": {
      "id": 9000000000001,
      "simulado": true,
      "status": "em_processo",
      "estado": "aguardando_escolha",
      "id_recorrencia": "RN_sim_e961dcb93f_1",
      "cliente_nome": "Ana Souza",
      "valor_cobranca": 890.0,
      "valor_liquido": null,
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "tentativas_executadas": 3,
      "aberto_em": "2026-10-04T16:14:26-03:00",
      "atualizado_em": "2026-10-11T16:14:27-03:00",
      "desfecho_em": null,
      "motivo_descarte": null,
      "motivo_perdido": null,
      "mensagem": null,
      "estorno": null,
      "estorno_parcial": false,
      "motivo_encerramento": null,
      "janela_inicio": "2026-10-04T16:14:26-03:00",
      "janela_fim": "2026-10-11T16:14:26-03:00"
    },
    "diagnostico": {
      "decidido_em": "2026-10-04T19:14:26+00:00",
      "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 68/100; probabilidade de recuperação 68%; retorno esperado R$ 602,94; intervenção recomendada. Pesaram, nesta ordem: histórico de pagamento 78% positivo (aumentou a chance), 11 meses como cliente (aumentou a chance), causa da falha de pagamento: saldo insuficiente (aumentou a chance), cobrança de R$ 890,00 (aumentou a chance), 1 falhas de pagamento nos últimos 90 dias (aumentou a chance). A decisão foi tomada pelo modelo de diagnóstico de falha de pagamento (failure_classifier), versão 2026-09-27T19:57:57#e347fbc3d391.",
      "com_modelo": true,
      "contribuicoes": [
        {
          "fator": "histórico de pagamento 78% positivo",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "11 meses como cliente",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "causa da falha de pagamento: saldo insuficiente",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "cobrança de R$ 890,00",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "1 falhas de pagamento nos últimos 90 dias",
          "efeito": "aumentou a chance de recuperar"
        }
      ],
      "desconto_por_anomalia": null
    },
    "mensagens": [
      {
        "rodada": 1,
        "abordagem": "lembrete_cordial",
        "texto": "Olá! Passando para lembrar da mensalidade de R$ 890.00, que ainda não foi compensada. Quando puder, regularize por Pix Automático: https://pay.crai.ai/pix_automatico/RN_sim_e\nEsta é uma mensagem automática.",
        "canal": "whatsapp",
        "motivo_canal": "presumido_sem_mapeamento",
        "recomendada": false,
        "escolhida": false,
        "nao_entregavel": false,
        "gerada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": null
      },
      {
        "rodada": 1,
        "abordagem": "facilitacao",
        "texto": "Olá! Sabemos que imprevistos acontecem. Para facilitar, você pode quitar os R$ 890.00 por Pix Automático, em poucos toques: https://pay.crai.ai/pix_automatico/RN_sim_e\nEsta é uma mensagem automática.",
        "canal": "whatsapp",
        "motivo_canal": "presumido_sem_mapeamento",
        "recomendada": true,
        "escolhida": false,
        "nao_entregavel": false,
        "gerada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": null
      },
      {
        "rodada": 1,
        "abordagem": "urgencia_respeitosa",
        "texto": "Olá! A mensalidade de R$ 890.00 segue em aberto depois das tentativas de cobrança. Pedimos que regularize assim que possível por Pix Automático: https://pay.crai.ai/pix_automatico/RN_sim_e\nEsta é uma mensagem automática.",
        "canal": "whatsapp",
        "motivo_canal": "presumido_sem_mapeamento",
        "recomendada": false,
        "escolhida": false,
        "nao_entregavel": false,
        "gerada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": null
      }
    ],
    "modo_mensagem": "escolha",
    "escolha_ate": "2026-10-12T00:14:27-03:00",
    "escolhida_por": null,
    "linha_do_tempo": [
      {
        "quando": "2026-10-04T16:14:26-03:00",
        "tipo": "abertura",
        "dados": {
          "origem": "webhook",
          "causa_legivel": "Saldo insuficiente",
          "janela_inicio": "2026-10-04T16:14:26-03:00",
          "janela_fim": "2026-10-11T16:14:26-03:00"
        }
      },
      {
        "quando": "2026-10-04T16:14:26-03:00",
        "tipo": "diagnostico",
        "dados": {
          "estrategia": "retry_automatico",
          "recovery_score": 68.0,
          "p_recovery": 0.6775
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "risco",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 68/100; probabilidade de recuperação 68%; retorno esperado R$ 602,94; intervenção recomendada. Pesaram, nesta ordem: histórico de pagamento 78% positivo (aumentou a chance), 11 meses como cliente (aumentou a chance), causa da falha de pagamento: saldo insuficiente (aumentou a chance), cobrança de R$ 890,00 (aumentou a chance), 1 falhas de pagamento nos últimos 90 dias (aumentou a chance). A decisão foi tomada pelo modelo de diagnóstico de falha de pagamento (failure_classifier), versão 2026-09-27T19:57:57#e347fbc3d391."
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "retentativa",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema decidiu como tratar a cobrança que falhou: nova tentativa automática de cobrança. Foram considerados: causa da falha de pagamento: saldo insuficiente, pontuação de recuperação 68/100, retorno esperado da intervenção R$ 602,94, sem anomalia de comportamento, meio de pagamento Pix Automático, 0 tentativas de cobrança já usadas, limite de 3 tentativas na janela. A decisão veio da regra 'decide_recovery', sem modelo treinado: 'Saldo insuficiente' costuma ser resolvido por nova tentativa de cobrança na janela regulada do BACEN (0/3 tentativas usadas), mirando a liquidez prevista (Módulo 3) — insistir aqui tem retorno esperado positivo."
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "retentativa",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema decidiu como tratar a cobrança que falhou: datas pela distribuição uniforme na janela; janela regulada até 11/10/2026. Foram considerados: 0 tentativas de cobrança já usadas, limite de 3 tentativas na janela, cobrança de R$ 890,00, meio de pagamento Pix Automático, vencimento em 04/10/2026. A decisão veio da regra 'PixAutomaticoRetryPolicy.fallback_uniforme', sem modelo treinado."
        }
      },
      {
        "quando": "2026-10-05T16:14:27-03:00",
        "tipo": "tentativa_disparada",
        "dados": {
          "numero": 1
        }
      },
      {
        "quando": "2026-10-05T16:14:27-03:00",
        "tipo": "tentativa_resultado",
        "dados": {
          "numero": 1,
          "resultado": "falhou"
        }
      },
      {
        "quando": "2026-10-08T16:14:27-03:00",
        "tipo": "tentativa_disparada",
        "dados": {
          "numero": 2
        }
      },
      {
        "quando": "2026-10-08T16:14:27-03:00",
        "tipo": "tentativa_resultado",
        "dados": {
          "numero": 2,
          "resultado": "falhou"
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "tentativa_disparada",
        "dados": {
          "numero": 3
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "tentativa_resultado",
        "dados": {
          "numero": 3,
          "resultado": "falhou"
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "sugestoes_geradas",
        "dados": {
          "rodada": 1,
          "recomendada": "facilitacao",
          "canal": "whatsapp"
        }
      }
    ],
    "trilha_ambigua": false
  },
  "tentativas": [
    {
      "numero": 1,
      "agendada_para": "2026-10-05T16:14:26-03:00",
      "disparada_em": "2026-10-05T16:14:27-03:00",
      "resultado": "falhou",
      "resultado_em": "2026-10-05T16:14:27-03:00",
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "motivo_cancelamento": null
    },
    {
      "numero": 2,
      "agendada_para": "2026-10-08T16:14:26-03:00",
      "disparada_em": "2026-10-08T16:14:27-03:00",
      "resultado": "falhou",
      "resultado_em": "2026-10-08T16:14:27-03:00",
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "motivo_cancelamento": null
    },
    {
      "numero": 3,
      "agendada_para": "2026-10-11T16:14:26-03:00",
      "disparada_em": "2026-10-11T16:14:27-03:00",
      "resultado": "falhou",
      "resultado_em": "2026-10-11T16:14:27-03:00",
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "motivo_cancelamento": null
    }
  ],
  "proxima_acao": {
    "quando": "2026-10-12T00:14:27-03:00",
    "descricao": "Envio automático da recomendada",
    "tipo": "mensagem"
  },
  "desfecho": null,
  "pensando": [
    "Causa da falha: saldo insuficiente.",
    "Chance de recuperar: 68%.",
    "Dia provável de saldo: 12/10. Perfil de recebimento estimado pelo sistema: PJ.",
    "Plano: 3 tentativas dentro de 7 dias, nos dias 05/10, 08/10 e 11/10. Nenhuma mensagem antes de todas falharem.",
    "Tentativa 1 não passou (saldo insuficiente).",
    "Tentativa 2 não passou (saldo insuficiente).",
    "Tentativa 3 não passou (saldo insuficiente).",
    "3 mensagens escritas para este cliente. A empresa escolhe uma; sem escolha em 8 h, a recomendada sai sozinha."
  ],
  "sem_crai": {
    "resultado": "perdido",
    "explicacao": "Sem a CRAI, o banco só tenta nas duas janelas do dia do vencimento. O dinheiro entra em 8 dias, então a cobrança se perde e a empresa precisa correr atrás por conta própria."
  },
  "modo_mensagem": "escolha",
  "prazo_escolha_horas": 8,
  "resposta_a_mensagem": null,
  "chance_recuperar": 0.6775,
  "dia_provavel_saldo": "2026-10-12T10:00:00-03:00"
} as unknown as SimulacaoApi

/** A empresa escolheu, e a mensagem saiu. */
export const SIM_ENVIADA = {
  "existe": true,
  "relogio": {
    "agora": "2026-10-11T16:14:27-03:00",
    "iniciado_em": "2026-10-04T16:14:26-03:00"
  },
  "cliente": {
    "nome": "Ana Souza",
    "mensalidade": 890.0,
    "perfil": "clt",
    "verdade": {
      "dias_ate_saldo": 8,
      "chance_pagar": 1.0,
      "vai_revogar": false
    }
  },
  "id_recorrencia": "RN_sim_e961dcb93f_1",
  "cobranca": {
    "feita": true,
    "em": "2026-10-04T16:14:26-03:00",
    "resultado": "falhou",
    "causa": "insufficient_funds",
    "causa_legivel": "Saldo insuficiente"
  },
  "ciclo": {
    "ciclo": {
      "id": 9000000000001,
      "simulado": true,
      "status": "em_processo",
      "estado": "mensagem_enviada",
      "id_recorrencia": "RN_sim_e961dcb93f_1",
      "cliente_nome": "Ana Souza",
      "valor_cobranca": 890.0,
      "valor_liquido": null,
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "tentativas_executadas": 3,
      "aberto_em": "2026-10-04T16:14:26-03:00",
      "atualizado_em": "2026-10-11T16:14:27-03:00",
      "desfecho_em": null,
      "motivo_descarte": null,
      "motivo_perdido": null,
      "mensagem": {
        "reservada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": "2026-10-11T16:14:27-03:00"
      },
      "estorno": null,
      "estorno_parcial": false,
      "motivo_encerramento": null,
      "janela_inicio": "2026-10-04T16:14:26-03:00",
      "janela_fim": "2026-10-11T16:14:26-03:00"
    },
    "diagnostico": {
      "decidido_em": "2026-10-04T19:14:26+00:00",
      "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 68/100; probabilidade de recuperação 68%; retorno esperado R$ 602,94; intervenção recomendada. Pesaram, nesta ordem: histórico de pagamento 78% positivo (aumentou a chance), 11 meses como cliente (aumentou a chance), causa da falha de pagamento: saldo insuficiente (aumentou a chance), cobrança de R$ 890,00 (aumentou a chance), 1 falhas de pagamento nos últimos 90 dias (aumentou a chance). A decisão foi tomada pelo modelo de diagnóstico de falha de pagamento (failure_classifier), versão 2026-09-27T19:57:57#e347fbc3d391.",
      "com_modelo": true,
      "contribuicoes": [
        {
          "fator": "histórico de pagamento 78% positivo",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "11 meses como cliente",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "causa da falha de pagamento: saldo insuficiente",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "cobrança de R$ 890,00",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "1 falhas de pagamento nos últimos 90 dias",
          "efeito": "aumentou a chance de recuperar"
        }
      ],
      "desconto_por_anomalia": null
    },
    "mensagens": [
      {
        "rodada": 1,
        "abordagem": "lembrete_cordial",
        "texto": "Olá! Passando para lembrar da mensalidade de R$ 890.00, que ainda não foi compensada. Quando puder, regularize por Pix Automático: https://pay.crai.ai/pix_automatico/RN_sim_e\nEsta é uma mensagem automática.",
        "canal": "whatsapp",
        "motivo_canal": "presumido_sem_mapeamento",
        "recomendada": false,
        "escolhida": false,
        "nao_entregavel": false,
        "gerada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": null
      },
      {
        "rodada": 1,
        "abordagem": "facilitacao",
        "texto": "Olá! Sabemos que imprevistos acontecem. Para facilitar, você pode quitar os R$ 890.00 por Pix Automático, em poucos toques: https://pay.crai.ai/pix_automatico/RN_sim_e\nEsta é uma mensagem automática.",
        "canal": "whatsapp",
        "motivo_canal": "presumido_sem_mapeamento",
        "recomendada": true,
        "escolhida": true,
        "nao_entregavel": false,
        "gerada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": "2026-10-11T16:14:27-03:00"
      },
      {
        "rodada": 1,
        "abordagem": "urgencia_respeitosa",
        "texto": "Olá! A mensalidade de R$ 890.00 segue em aberto depois das tentativas de cobrança. Pedimos que regularize assim que possível por Pix Automático: https://pay.crai.ai/pix_automatico/RN_sim_e\nEsta é uma mensagem automática.",
        "canal": "whatsapp",
        "motivo_canal": "presumido_sem_mapeamento",
        "recomendada": false,
        "escolhida": false,
        "nao_entregavel": false,
        "gerada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": null
      }
    ],
    "modo_mensagem": "escolha",
    "escolha_ate": null,
    "escolhida_por": "owner",
    "linha_do_tempo": [
      {
        "quando": "2026-10-04T16:14:26-03:00",
        "tipo": "abertura",
        "dados": {
          "origem": "webhook",
          "causa_legivel": "Saldo insuficiente",
          "janela_inicio": "2026-10-04T16:14:26-03:00",
          "janela_fim": "2026-10-11T16:14:26-03:00"
        }
      },
      {
        "quando": "2026-10-04T16:14:26-03:00",
        "tipo": "diagnostico",
        "dados": {
          "estrategia": "retry_automatico",
          "recovery_score": 68.0,
          "p_recovery": 0.6775
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "risco",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 68/100; probabilidade de recuperação 68%; retorno esperado R$ 602,94; intervenção recomendada. Pesaram, nesta ordem: histórico de pagamento 78% positivo (aumentou a chance), 11 meses como cliente (aumentou a chance), causa da falha de pagamento: saldo insuficiente (aumentou a chance), cobrança de R$ 890,00 (aumentou a chance), 1 falhas de pagamento nos últimos 90 dias (aumentou a chance). A decisão foi tomada pelo modelo de diagnóstico de falha de pagamento (failure_classifier), versão 2026-09-27T19:57:57#e347fbc3d391."
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "retentativa",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema decidiu como tratar a cobrança que falhou: nova tentativa automática de cobrança. Foram considerados: causa da falha de pagamento: saldo insuficiente, pontuação de recuperação 68/100, retorno esperado da intervenção R$ 602,94, sem anomalia de comportamento, meio de pagamento Pix Automático, 0 tentativas de cobrança já usadas, limite de 3 tentativas na janela. A decisão veio da regra 'decide_recovery', sem modelo treinado: 'Saldo insuficiente' costuma ser resolvido por nova tentativa de cobrança na janela regulada do BACEN (0/3 tentativas usadas), mirando a liquidez prevista (Módulo 3) — insistir aqui tem retorno esperado positivo."
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "retentativa",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema decidiu como tratar a cobrança que falhou: datas pela distribuição uniforme na janela; janela regulada até 11/10/2026. Foram considerados: 0 tentativas de cobrança já usadas, limite de 3 tentativas na janela, cobrança de R$ 890,00, meio de pagamento Pix Automático, vencimento em 04/10/2026. A decisão veio da regra 'PixAutomaticoRetryPolicy.fallback_uniforme', sem modelo treinado."
        }
      },
      {
        "quando": "2026-10-05T16:14:27-03:00",
        "tipo": "tentativa_disparada",
        "dados": {
          "numero": 1
        }
      },
      {
        "quando": "2026-10-05T16:14:27-03:00",
        "tipo": "tentativa_resultado",
        "dados": {
          "numero": 1,
          "resultado": "falhou"
        }
      },
      {
        "quando": "2026-10-08T16:14:27-03:00",
        "tipo": "tentativa_disparada",
        "dados": {
          "numero": 2
        }
      },
      {
        "quando": "2026-10-08T16:14:27-03:00",
        "tipo": "tentativa_resultado",
        "dados": {
          "numero": 2,
          "resultado": "falhou"
        }
      },
      {
        "quando": "2026-10-11T19:14:27+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "oferta",
          "explicacao": "Em 11/10/2026, no fluxo de recuperação de pagamento, o sistema escolheu o que oferecer a este cliente: abordagem = facilitacao; abordagem_recomendada = facilitacao; rodada = 1; escolhida_por = owner; por_prazo = False. Foram considerados: causa da falha de pagamento: saldo insuficiente, rodada = 1. A decisão veio da regra 'escolha_entre_sugestoes', sem modelo treinado: escolhida pela empresa (papel owner) entre as 3 sugestões geradas."
        }
      },
      {
        "quando": "2026-10-11T19:14:27+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "oferta",
          "explicacao": "Em 11/10/2026, no fluxo de recuperação de pagamento, o sistema escolheu o que oferecer a este cliente: meio de pagamento Pix Automático; tom amigável; modelo de texto 'facilitacao.insufficient_funds'; texto de modelo pronto (não gerado); abordagem = facilitacao. Foram considerados: causa da falha de pagamento: saldo insuficiente, probabilidade de recuperação 68%, cobrança de R$ 890,00. A decisão veio da regra 'DunningEngine._select_payment', sem modelo treinado: Pix Automático recupera na hora, contornando o cartão."
        }
      },
      {
        "quando": "2026-10-11T19:14:27+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "canal",
          "explicacao": "Em 11/10/2026, no fluxo de recuperação de pagamento, o sistema escolheu por onde contatar este cliente: canal WhatsApp. Foram considerados: causa da falha de pagamento: saldo insuficiente. A decisão veio da regra 'canal_involuntario.escolher_canal', sem modelo treinado: primeiro canal permitido pela empresa com contato na base (presumido_sem_mapeamento); canais humanos são proibidos."
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "tentativa_disparada",
        "dados": {
          "numero": 3
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "tentativa_resultado",
        "dados": {
          "numero": 3,
          "resultado": "falhou"
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "sugestoes_geradas",
        "dados": {
          "rodada": 1,
          "recomendada": "facilitacao",
          "canal": "whatsapp"
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "mensagem_escolhida",
        "dados": {
          "rodada": 1,
          "abordagem": "facilitacao",
          "escolhida_por": "owner"
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "mensagem_reservada",
        "dados": {}
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "mensagem_enviada",
        "dados": {}
      }
    ],
    "trilha_ambigua": false
  },
  "tentativas": [
    {
      "numero": 1,
      "agendada_para": "2026-10-05T16:14:26-03:00",
      "disparada_em": "2026-10-05T16:14:27-03:00",
      "resultado": "falhou",
      "resultado_em": "2026-10-05T16:14:27-03:00",
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "motivo_cancelamento": null
    },
    {
      "numero": 2,
      "agendada_para": "2026-10-08T16:14:26-03:00",
      "disparada_em": "2026-10-08T16:14:27-03:00",
      "resultado": "falhou",
      "resultado_em": "2026-10-08T16:14:27-03:00",
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "motivo_cancelamento": null
    },
    {
      "numero": 3,
      "agendada_para": "2026-10-11T16:14:26-03:00",
      "disparada_em": "2026-10-11T16:14:27-03:00",
      "resultado": "falhou",
      "resultado_em": "2026-10-11T16:14:27-03:00",
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "motivo_cancelamento": null
    }
  ],
  "proxima_acao": {
    "quando": "2026-10-13T16:14:27-03:00",
    "descricao": "Prazo para resposta do cliente",
    "tipo": "resposta"
  },
  "desfecho": null,
  "pensando": [
    "Causa da falha: saldo insuficiente.",
    "Chance de recuperar: 68%.",
    "Dia provável de saldo: 12/10. Perfil de recebimento estimado pelo sistema: PJ.",
    "Plano: 3 tentativas dentro de 7 dias, nos dias 05/10, 08/10 e 11/10. Nenhuma mensagem antes de todas falharem.",
    "Tentativa 1 não passou (saldo insuficiente).",
    "Tentativa 2 não passou (saldo insuficiente).",
    "Tentativa 3 não passou (saldo insuficiente).",
    "3 mensagens escritas para este cliente. A empresa escolhe uma; sem escolha em 8 h, a recomendada sai sozinha.",
    "Mensagem enviada por WhatsApp (facilitação, escolhida pela empresa). O sistema espera o pagamento pelo meio oferecido."
  ],
  "sem_crai": {
    "resultado": "perdido",
    "explicacao": "Sem a CRAI, o banco só tenta nas duas janelas do dia do vencimento. O dinheiro entra em 8 dias, então a cobrança se perde e a empresa precisa correr atrás por conta própria."
  },
  "modo_mensagem": "escolha",
  "prazo_escolha_horas": 8,
  "resposta_a_mensagem": null,
  "chance_recuperar": 0.6775,
  "dia_provavel_saldo": "2026-10-12T10:00:00-03:00"
} as unknown as SimulacaoApi

/** O cliente fictício pagou depois da mensagem. */
export const SIM_RECUPERADA = {
  "existe": true,
  "relogio": {
    "agora": "2026-10-13T16:14:28-03:00",
    "iniciado_em": "2026-10-04T16:14:26-03:00"
  },
  "cliente": {
    "nome": "Ana Souza",
    "mensalidade": 890.0,
    "perfil": "clt",
    "verdade": {
      "dias_ate_saldo": 8,
      "chance_pagar": 1.0,
      "vai_revogar": false
    }
  },
  "id_recorrencia": "RN_sim_e961dcb93f_1",
  "cobranca": {
    "feita": true,
    "em": "2026-10-04T16:14:26-03:00",
    "resultado": "falhou",
    "causa": "insufficient_funds",
    "causa_legivel": "Saldo insuficiente"
  },
  "ciclo": {
    "ciclo": {
      "id": 9000000000001,
      "simulado": true,
      "status": "recuperado",
      "estado": "recuperado",
      "id_recorrencia": "RN_sim_e961dcb93f_1",
      "cliente_nome": "Ana Souza",
      "valor_cobranca": 890.0,
      "valor_liquido": 756.5,
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "tentativas_executadas": 3,
      "aberto_em": "2026-10-04T16:14:26-03:00",
      "atualizado_em": "2026-10-13T16:14:28-03:00",
      "desfecho_em": "2026-10-13T16:14:28-03:00",
      "motivo_descarte": null,
      "motivo_perdido": null,
      "mensagem": {
        "reservada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": "2026-10-11T16:14:27-03:00"
      },
      "estorno": null,
      "estorno_parcial": false,
      "motivo_encerramento": null,
      "janela_inicio": "2026-10-04T16:14:26-03:00",
      "janela_fim": "2026-10-11T16:14:26-03:00"
    },
    "diagnostico": {
      "decidido_em": "2026-10-04T19:14:26+00:00",
      "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 68/100; probabilidade de recuperação 68%; retorno esperado R$ 602,94; intervenção recomendada. Pesaram, nesta ordem: histórico de pagamento 78% positivo (aumentou a chance), 11 meses como cliente (aumentou a chance), causa da falha de pagamento: saldo insuficiente (aumentou a chance), cobrança de R$ 890,00 (aumentou a chance), 1 falhas de pagamento nos últimos 90 dias (aumentou a chance). A decisão foi tomada pelo modelo de diagnóstico de falha de pagamento (failure_classifier), versão 2026-09-27T19:57:57#e347fbc3d391.",
      "com_modelo": true,
      "contribuicoes": [
        {
          "fator": "histórico de pagamento 78% positivo",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "11 meses como cliente",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "causa da falha de pagamento: saldo insuficiente",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "cobrança de R$ 890,00",
          "efeito": "aumentou a chance de recuperar"
        },
        {
          "fator": "1 falhas de pagamento nos últimos 90 dias",
          "efeito": "aumentou a chance de recuperar"
        }
      ],
      "desconto_por_anomalia": null
    },
    "mensagens": [
      {
        "rodada": 1,
        "abordagem": "lembrete_cordial",
        "texto": "Olá! Passando para lembrar da mensalidade de R$ 890.00, que ainda não foi compensada. Quando puder, regularize por Pix Automático: https://pay.crai.ai/pix_automatico/RN_sim_e\nEsta é uma mensagem automática.",
        "canal": "whatsapp",
        "motivo_canal": "presumido_sem_mapeamento",
        "recomendada": false,
        "escolhida": false,
        "nao_entregavel": false,
        "gerada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": null
      },
      {
        "rodada": 1,
        "abordagem": "facilitacao",
        "texto": "Olá! Sabemos que imprevistos acontecem. Para facilitar, você pode quitar os R$ 890.00 por Pix Automático, em poucos toques: https://pay.crai.ai/pix_automatico/RN_sim_e\nEsta é uma mensagem automática.",
        "canal": "whatsapp",
        "motivo_canal": "presumido_sem_mapeamento",
        "recomendada": true,
        "escolhida": true,
        "nao_entregavel": false,
        "gerada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": "2026-10-11T16:14:27-03:00"
      },
      {
        "rodada": 1,
        "abordagem": "urgencia_respeitosa",
        "texto": "Olá! A mensalidade de R$ 890.00 segue em aberto depois das tentativas de cobrança. Pedimos que regularize assim que possível por Pix Automático: https://pay.crai.ai/pix_automatico/RN_sim_e\nEsta é uma mensagem automática.",
        "canal": "whatsapp",
        "motivo_canal": "presumido_sem_mapeamento",
        "recomendada": false,
        "escolhida": false,
        "nao_entregavel": false,
        "gerada_em": "2026-10-11T16:14:27-03:00",
        "enviada_em": null
      }
    ],
    "modo_mensagem": "escolha",
    "escolha_ate": null,
    "escolhida_por": "owner",
    "linha_do_tempo": [
      {
        "quando": "2026-10-04T16:14:26-03:00",
        "tipo": "abertura",
        "dados": {
          "origem": "webhook",
          "causa_legivel": "Saldo insuficiente",
          "janela_inicio": "2026-10-04T16:14:26-03:00",
          "janela_fim": "2026-10-11T16:14:26-03:00"
        }
      },
      {
        "quando": "2026-10-04T16:14:26-03:00",
        "tipo": "diagnostico",
        "dados": {
          "estrategia": "retry_automatico",
          "recovery_score": 68.0,
          "p_recovery": 0.6775
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "risco",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente: pontuação de recuperação 68/100; probabilidade de recuperação 68%; retorno esperado R$ 602,94; intervenção recomendada. Pesaram, nesta ordem: histórico de pagamento 78% positivo (aumentou a chance), 11 meses como cliente (aumentou a chance), causa da falha de pagamento: saldo insuficiente (aumentou a chance), cobrança de R$ 890,00 (aumentou a chance), 1 falhas de pagamento nos últimos 90 dias (aumentou a chance). A decisão foi tomada pelo modelo de diagnóstico de falha de pagamento (failure_classifier), versão 2026-09-27T19:57:57#e347fbc3d391."
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "retentativa",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema decidiu como tratar a cobrança que falhou: nova tentativa automática de cobrança. Foram considerados: causa da falha de pagamento: saldo insuficiente, pontuação de recuperação 68/100, retorno esperado da intervenção R$ 602,94, sem anomalia de comportamento, meio de pagamento Pix Automático, 0 tentativas de cobrança já usadas, limite de 3 tentativas na janela. A decisão veio da regra 'decide_recovery', sem modelo treinado: 'Saldo insuficiente' costuma ser resolvido por nova tentativa de cobrança na janela regulada do BACEN (0/3 tentativas usadas), mirando a liquidez prevista (Módulo 3) — insistir aqui tem retorno esperado positivo."
        }
      },
      {
        "quando": "2026-10-04T19:14:26+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "retentativa",
          "explicacao": "Em 04/10/2026, no fluxo de recuperação de pagamento, o sistema decidiu como tratar a cobrança que falhou: datas pela distribuição uniforme na janela; janela regulada até 11/10/2026. Foram considerados: 0 tentativas de cobrança já usadas, limite de 3 tentativas na janela, cobrança de R$ 890,00, meio de pagamento Pix Automático, vencimento em 04/10/2026. A decisão veio da regra 'PixAutomaticoRetryPolicy.fallback_uniforme', sem modelo treinado."
        }
      },
      {
        "quando": "2026-10-05T16:14:27-03:00",
        "tipo": "tentativa_disparada",
        "dados": {
          "numero": 1
        }
      },
      {
        "quando": "2026-10-05T16:14:27-03:00",
        "tipo": "tentativa_resultado",
        "dados": {
          "numero": 1,
          "resultado": "falhou"
        }
      },
      {
        "quando": "2026-10-08T16:14:27-03:00",
        "tipo": "tentativa_disparada",
        "dados": {
          "numero": 2
        }
      },
      {
        "quando": "2026-10-08T16:14:27-03:00",
        "tipo": "tentativa_resultado",
        "dados": {
          "numero": 2,
          "resultado": "falhou"
        }
      },
      {
        "quando": "2026-10-11T19:14:27+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "oferta",
          "explicacao": "Em 11/10/2026, no fluxo de recuperação de pagamento, o sistema escolheu o que oferecer a este cliente: abordagem = facilitacao; abordagem_recomendada = facilitacao; rodada = 1; escolhida_por = owner; por_prazo = False. Foram considerados: causa da falha de pagamento: saldo insuficiente, rodada = 1. A decisão veio da regra 'escolha_entre_sugestoes', sem modelo treinado: escolhida pela empresa (papel owner) entre as 3 sugestões geradas."
        }
      },
      {
        "quando": "2026-10-11T19:14:27+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "oferta",
          "explicacao": "Em 11/10/2026, no fluxo de recuperação de pagamento, o sistema escolheu o que oferecer a este cliente: meio de pagamento Pix Automático; tom amigável; modelo de texto 'facilitacao.insufficient_funds'; texto de modelo pronto (não gerado); abordagem = facilitacao. Foram considerados: causa da falha de pagamento: saldo insuficiente, probabilidade de recuperação 68%, cobrança de R$ 890,00. A decisão veio da regra 'DunningEngine._select_payment', sem modelo treinado: Pix Automático recupera na hora, contornando o cartão."
        }
      },
      {
        "quando": "2026-10-11T19:14:27+00:00",
        "tipo": "decisao",
        "dados": {
          "tipo_decisao": "canal",
          "explicacao": "Em 11/10/2026, no fluxo de recuperação de pagamento, o sistema escolheu por onde contatar este cliente: canal WhatsApp. Foram considerados: causa da falha de pagamento: saldo insuficiente. A decisão veio da regra 'canal_involuntario.escolher_canal', sem modelo treinado: primeiro canal permitido pela empresa com contato na base (presumido_sem_mapeamento); canais humanos são proibidos."
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "tentativa_disparada",
        "dados": {
          "numero": 3
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "tentativa_resultado",
        "dados": {
          "numero": 3,
          "resultado": "falhou"
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "sugestoes_geradas",
        "dados": {
          "rodada": 1,
          "recomendada": "facilitacao",
          "canal": "whatsapp"
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "mensagem_escolhida",
        "dados": {
          "rodada": 1,
          "abordagem": "facilitacao",
          "escolhida_por": "owner"
        }
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "mensagem_reservada",
        "dados": {}
      },
      {
        "quando": "2026-10-11T16:14:27-03:00",
        "tipo": "mensagem_enviada",
        "dados": {}
      },
      {
        "quando": "2026-10-13T16:14:28-03:00",
        "tipo": "recuperado",
        "dados": {
          "valor_liquido": 756.5
        }
      }
    ],
    "trilha_ambigua": false
  },
  "tentativas": [
    {
      "numero": 1,
      "agendada_para": "2026-10-05T16:14:26-03:00",
      "disparada_em": "2026-10-05T16:14:27-03:00",
      "resultado": "falhou",
      "resultado_em": "2026-10-05T16:14:27-03:00",
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "motivo_cancelamento": null
    },
    {
      "numero": 2,
      "agendada_para": "2026-10-08T16:14:26-03:00",
      "disparada_em": "2026-10-08T16:14:27-03:00",
      "resultado": "falhou",
      "resultado_em": "2026-10-08T16:14:27-03:00",
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "motivo_cancelamento": null
    },
    {
      "numero": 3,
      "agendada_para": "2026-10-11T16:14:26-03:00",
      "disparada_em": "2026-10-11T16:14:27-03:00",
      "resultado": "falhou",
      "resultado_em": "2026-10-11T16:14:27-03:00",
      "causa": "insufficient_funds",
      "causa_legivel": "Saldo insuficiente",
      "motivo_cancelamento": null
    }
  ],
  "proxima_acao": null,
  "desfecho": {
    "tipo": "recuperado",
    "via": "mensagem",
    "tentativa": null,
    "valor_liquido": 756.5,
    "em": "2026-10-13T16:14:28-03:00"
  },
  "pensando": [
    "Causa da falha: saldo insuficiente.",
    "Chance de recuperar: 68%.",
    "Dia provável de saldo: 12/10. Perfil de recebimento estimado pelo sistema: PJ.",
    "Plano: 3 tentativas dentro de 7 dias, nos dias 05/10, 08/10 e 11/10. Nenhuma mensagem antes de todas falharem.",
    "Tentativa 1 não passou (saldo insuficiente).",
    "Tentativa 2 não passou (saldo insuficiente).",
    "Tentativa 3 não passou (saldo insuficiente).",
    "3 mensagens escritas para este cliente. A empresa escolhe uma; sem escolha em 8 h, a recomendada sai sozinha.",
    "Mensagem enviada por WhatsApp (facilitação, escolhida pela empresa). O sistema espera o pagamento pelo meio oferecido.",
    "Pagamento recuperado depois da mensagem. O ciclo fecha e o valor entra no extrato."
  ],
  "sem_crai": {
    "resultado": "perdido",
    "explicacao": "Sem a CRAI, o banco só tenta nas duas janelas do dia do vencimento. O dinheiro entra em 8 dias, então a cobrança se perde e a empresa precisa correr atrás por conta própria."
  },
  "modo_mensagem": "escolha",
  "prazo_escolha_horas": 8,
  "resposta_a_mensagem": "pagou",
  "chance_recuperar": 0.6775,
  "dia_provavel_saldo": "2026-10-12T10:00:00-03:00"
} as unknown as SimulacaoApi

/** A resposta de `POST /ciclos/{id}/mensagens/escolher` no ciclo simulado. */
export const SIM_ESCOLHA = {
  "ciclo_id": 9000000000001,
  "rodada": 1,
  "abordagem": "facilitacao",
  "escolhida_por": "owner",
  "enviada": true,
  "espera": null
}

/** O ciclo simulado na lista de `GET /ciclos?incluir_simulados=true`. */
export const CICLO_SIMULADO = {
  "id": 9000000000001,
  "simulado": true,
  "status": "recuperado",
  "estado": "recuperado",
  "id_recorrencia": "RN_sim_e961dcb93f_1",
  "cliente_nome": "Ana Souza",
  "valor_cobranca": 890.0,
  "valor_liquido": 756.5,
  "causa": "insufficient_funds",
  "causa_legivel": "Saldo insuficiente",
  "tentativas_executadas": 3,
  "aberto_em": "2026-10-04T16:14:26-03:00",
  "atualizado_em": "2026-10-13T16:14:28-03:00",
  "desfecho_em": "2026-10-13T16:14:28-03:00",
  "motivo_descarte": null,
  "motivo_perdido": null,
  "mensagem": {
    "reservada_em": "2026-10-11T16:14:27-03:00",
    "enviada_em": "2026-10-11T16:14:27-03:00"
  },
  "estorno": null,
  "estorno_parcial": false,
  "motivo_encerramento": null
} as unknown as CicloApi

export const RETENCAO_ACEITA = {
  "faixa": "grave",
  "motivo": "Abriu a página de cancelamento, sem dado de uso",
  "decidido_por": "regua",
  "risco": 0.9,
  "corte_de_intervencao": 0.6,
  "oferta": "pausa_1_mes",
  "oferta_legivel": "pausa de 1 mês na assinatura, sem custo",
  "canal": "popup",
  "canal_legivel": "aviso dentro do produto",
  "porque": "O sistema sorteia a partir do que já aprendeu sobre cada oferta para este perfil. Nesta rodada, esta teve o maior retorno esperado (chance de aceite aprendida até aqui: 64%).",
  "aceitou": true,
  "valor_mantido_liquido": 0.0,
  "sem_crai": "Sem a CRAI, ninguém perceberia os sinais até o pedido de cancelamento, quando já é tarde para oferecer algo.",
  "meses_de_mrr": 1,
  "prazo_estorno_dias": 30,
  "simulado": true
} as unknown as RetencaoSimuladaApi
export const RETENCAO_RECUSADA = {
  "faixa": "preocupante",
  "motivo": "Sem login há 24 dias, usa 1 funcionalidade nos últimos 30 dias, MRR R$ 1.200,00",
  "decidido_por": "regua",
  "risco": 0.8,
  "corte_de_intervencao": 0.6,
  "oferta": "pausa_1_mes",
  "oferta_legivel": "pausa de 1 mês na assinatura, sem custo",
  "canal": "email",
  "canal_legivel": "e-mail",
  "porque": "O sistema sorteia a partir do que já aprendeu sobre cada oferta para este perfil. Nesta rodada, esta teve o maior retorno esperado (chance de aceite aprendida até aqui: 65%).",
  "aceitou": false,
  "valor_mantido_liquido": 0.0,
  "sem_crai": "Sem a CRAI, ninguém perceberia os sinais até o pedido de cancelamento, quando já é tarde para oferecer algo.",
  "meses_de_mrr": 1,
  "prazo_estorno_dias": 30,
  "simulado": true
} as unknown as RetencaoSimuladaApi
export const RETENCAO_SEM_RISCO = {
  "faixa": "sem_risco",
  "motivo": "Sem dado de uso",
  "decidido_por": "regua",
  "risco": 0.0,
  "corte_de_intervencao": 0.6,
  "oferta": null,
  "oferta_legivel": null,
  "canal": null,
  "canal_legivel": null,
  "porque": null,
  "aceitou": null,
  "valor_mantido_liquido": 0.0,
  "sem_crai": "O sistema não interveio: com ou sem a CRAI, este cliente segue como está.",
  "meses_de_mrr": 1,
  "prazo_estorno_dias": 30,
  "simulado": true
} as unknown as RetencaoSimuladaApi

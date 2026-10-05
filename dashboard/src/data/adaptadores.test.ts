/**
 * Os adaptadores contra as respostas de exemplo do backend (relatórios da Etapa 2, Blocos 2,
 * 3 e 4). O que se mede: status, linha do tempo, valores, nenhuma `fee`, datas com fuso.
 */
import { describe, expect, it } from 'vitest'
import {
  adaptarChave,
  adaptarChaves,
  avisoDoDesconto,
  abordagemDaTela,
  abordagemParaApi,
  adaptarCiclo,
  adaptarConfiguracao,
  adaptarDetalhe,
  adaptarEvento,
  adaptarMensagem,
  adaptarMetricas,
  adaptarSaude,
  adaptarSerie,
  configuracaoParaApi,
  horaDaTela,
  horaParaApi,
  motivoDoCanal,
  statusDaTela,
  statusParaApi,
  type CicloApi,
  type CicloDetalheApi,
  type ConfiguracaoApi,
  type MetricasMesApi,
} from './adaptadores'
import type { Configuracao, SaudeSistema } from './tipos'

/** A linha de `GET /ciclos` do Relatório 2, com os campos que o Bloco 3 acrescentou. */
const RECUPERADO: CicloApi = {
  id: 12,
  status: 'recuperado',
  estado: 'recuperado',
  id_recorrencia: 'RN_8841',
  cliente_nome: 'Mariana Albuquerque Tavares',
  valor_cobranca: 299.9,
  valor_liquido: 254.92,
  causa: 'insufficient_funds',
  causa_legivel: 'Saldo insuficiente',
  tentativas_executadas: 3,
  aberto_em: '2026-09-03T09:00:00-03:00',
  atualizado_em: '2026-09-13T09:00:00-03:00',
  desfecho_em: '2026-09-13T09:00:00-03:00',
  motivo_descarte: null,
  motivo_perdido: null,
  mensagem: { reservada_em: '2026-09-11T10:00:00-03:00', enviada_em: '2026-09-11T10:00:00-03:00' },
}

const AGUARDANDO: CicloApi = {
  ...RECUPERADO,
  id: 13,
  status: 'em_processo',
  estado: 'aguardando_escolha',
  cliente_nome: null,
  valor_liquido: null,
  desfecho_em: null,
  mensagem: null,
}

const DETALHE: CicloDetalheApi = {
  ciclo: AGUARDANDO,
  diagnostico: {
    decidido_em: '2026-09-03T09:00:00-03:00',
    explicacao: 'Em 03/09/2026, no fluxo de recuperação de pagamento, o sistema avaliou o risco deste cliente.',
    com_modelo: true,
    contribuicoes: [
      { fator: '2 meses como cliente', efeito: 'reduziu a chance de recuperar' },
      { fator: 'saldo insuficiente', efeito: 'aumentou a chance de recuperar' },
      { fator: 'fator do diagnóstico sem rótulo legível', efeito: null },
    ],
  },
  mensagens: [
    { rodada: 2, abordagem: 'urgencia_respeitosa', texto: 'Texto C2', canal: 'email', motivo_canal: 'contato_da_base', recomendada: false, escolhida: false, nao_entregavel: false, gerada_em: '2026-09-06T10:30:00-03:00', enviada_em: null },
    { rodada: 1, abordagem: 'lembrete_cordial', texto: 'Texto A', canal: 'email', motivo_canal: 'contato_da_base', recomendada: true, escolhida: false, nao_entregavel: false, gerada_em: '2026-09-06T10:05:00-03:00', enviada_em: null },
    { rodada: 1, abordagem: 'facilitacao', texto: null, canal: 'sem_canal', motivo_canal: 'sem_contato', recomendada: false, escolhida: false, nao_entregavel: true, gerada_em: '2026-09-06T10:05:00-03:00', enviada_em: null },
  ],
  modo_mensagem: 'escolha',
  escolha_ate: '2026-09-06T18:05:00-03:00',
  escolhida_por: null,
  linha_do_tempo: [
    { quando: '2026-09-03T09:00:00-03:00', tipo: 'abertura', dados: { origem: 'webhook', causa_legivel: 'Saldo insuficiente', janela_inicio: '2026-09-03T09:00:00-03:00', janela_fim: '2026-09-10T09:00:00-03:00' } },
    { quando: '2026-09-03T09:00:00-03:00', tipo: 'diagnostico', dados: { estrategia: 'retry', recovery_score: 72, p_recovery: 0.72 } },
    { quando: '2026-09-03T09:00:00-03:00', tipo: 'decisao', dados: { tipo_decisao: 'risco', explicacao: 'Pesaram, nesta ordem: 2 meses como cliente (reduziu a chance).' } },
    { quando: '2026-09-04T10:00:00-03:00', tipo: 'tentativa_disparada', dados: { numero: 1 } },
    { quando: '2026-09-04T10:05:00-03:00', tipo: 'tentativa_resultado', dados: { numero: 1, resultado: 'falhou' } },
    { quando: '2026-09-06T10:05:00-03:00', tipo: 'sugestoes_geradas', dados: { rodada: 1, recomendada: 'lembrete_cordial', canal: 'email' } },
    { quando: '2026-09-06T10:05:00-03:00', tipo: 'mensagem_reservada', dados: {} },
  ],
  trilha_ambigua: false,
}

/** Procura uma chave em qualquer profundidade do que o adaptador devolveu. */
function temChave(valor: unknown, chave: string): boolean {
  if (Array.isArray(valor)) return valor.some((v) => temChave(v, chave))
  if (valor && typeof valor === 'object') {
    return Object.entries(valor).some(([k, v]) => k === chave || temChave(v, chave))
  }
  return false
}

const COM_FUSO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$/

describe('status (R10)', () => {
  it('encerrado_sem_recuperacao vira encerrado, e os outros três passam iguais', () => {
    expect(statusDaTela('encerrado_sem_recuperacao')).toBe('encerrado')
    expect(statusDaTela('em_analise')).toBe('em_analise')
    expect(statusDaTela('em_processo')).toBe('em_processo')
    expect(statusDaTela('recuperado')).toBe('recuperado')
  })

  it('o filtro da tela volta para o código do backend', () => {
    expect(statusParaApi('encerrado')).toBe('encerrado_sem_recuperacao')
    expect(statusParaApi('em_processo')).toBe('em_processo')
  })

  it('perdido e descartado aparecem como encerrados: nenhum ciclo some', () => {
    const perdido = adaptarCiclo({ ...RECUPERADO, status: 'encerrado_sem_recuperacao', estado: 'perdido', valor_liquido: null })
    const descartado = adaptarCiclo({ ...RECUPERADO, status: 'encerrado_sem_recuperacao', estado: 'descartado', valor_liquido: null, motivo_descarte: 'eprofit_nao_positivo' })
    expect([perdido.status, perdido.estado]).toEqual(['encerrado', 'perdido'])
    expect([descartado.status, descartado.estado]).toEqual(['encerrado', 'descartado'])
    expect(descartado.tentativas_total).toBe(0)
    expect(perdido.tentativas_total).toBe(3)
  })

  it('estado que a tela ainda não conhece não quebra: o status decide onde aparece', () => {
    const c = adaptarCiclo({ ...AGUARDANDO, estado: 'estado_do_futuro' })
    expect(c.status).toBe('em_processo')
    expect(c.estado).toBe('recobrando')
  })
})

describe('valores (R11)', () => {
  it('valor_cobranca é o bruto e valor_liquido só existe no recuperado', () => {
    const recuperado = adaptarCiclo(RECUPERADO)
    expect(recuperado.valor_cobranca).toBe(299.9)
    expect(recuperado.valor_liquido).toBe(254.92)
    const aberto = adaptarCiclo(AGUARDANDO)
    expect(aberto.valor_cobranca).toBe(299.9)
    expect(aberto.valor_liquido).toBeNull()
  })

  it('a fee nunca aparece, nem que o backend a mandasse por engano', () => {
    const comFee = { ...RECUPERADO, fee: 44.98, tenant_id: 'empresa-a' } as CicloApi
    const linha = adaptarCiclo(comFee)
    const detalhe = adaptarDetalhe({ ...DETALHE, ciclo: comFee })
    for (const saida of [linha, detalhe]) {
      expect(temChave(saida, 'fee')).toBe(false)
      expect(temChave(saida, 'tenant_id')).toBe(false)
      expect(JSON.stringify(saida)).not.toContain('44.98')
    }
  })

  it('o que o tipo da tela não tem não passa: nenhum contato vaza pelo adaptador', () => {
    const comContato = { ...RECUPERADO, telefone: '+5511988887777', email: 'cliente@exemplo.com.br', cpf: '00000000000' } as CicloApi
    const saidas = [adaptarCiclo(comContato), adaptarDetalhe({ ...DETALHE, ciclo: comContato })]
    const texto = JSON.stringify(saidas)
    for (const proibido of ['+5511988887777', 'cliente@exemplo.com.br', '00000000000']) {
      expect(texto).not.toContain(proibido)
    }
    for (const chave of ['telefone', 'email', 'cpf']) {
      expect(temChave(saidas, chave)).toBe(false)
    }
  })
})

describe('linha da tabela', () => {
  it('cliente_nome vira cliente; sem nome fica null (a tela mostra "Cliente sem cadastro")', () => {
    expect(adaptarCiclo(RECUPERADO).cliente).toBe('Mariana Albuquerque Tavares')
    expect(adaptarCiclo(AGUARDANDO).cliente).toBeNull()
  })

  it('a causa em português vem do backend, e causa desconhecida não quebra o tipo', () => {
    expect(adaptarCiclo(RECUPERADO).causa_legivel).toBe('Saldo insuficiente')
    const outra = adaptarCiclo({ ...RECUPERADO, causa: 'codigo_novo', causa_legivel: 'Motivo novo do banco' })
    expect(outra.causa).toBe('generic_decline')
    expect(outra.causa_legivel).toBe('Motivo novo do banco')
  })

  it('as datas saem como vieram: ISO 8601 com fuso', () => {
    const linha = adaptarCiclo(RECUPERADO)
    expect(linha.aberto_em).toMatch(COM_FUSO)
    expect(linha.atualizado_em).toMatch(COM_FUSO)
    expect(new Date(linha.aberto_em).toISOString()).toBe('2026-09-03T12:00:00.000Z')
  })

  it('o que o backend ainda não informa fica declarado, não inventado', () => {
    const linha = adaptarCiclo(RECUPERADO)
    expect(linha.proxima_acao).toBeNull()
    expect(linha.proxima_acao_descricao).toBeNull()
    expect(linha.simulado).toBe(false)
  })
})

describe('detalhe do ciclo', () => {
  const d = adaptarDetalhe(DETALHE)

  it('a chance de recuperar vem do diagnóstico; sem ele, null', () => {
    expect(d.chance_recuperar).toBe(0.72)
    expect(adaptarDetalhe({ ...DETALHE, linha_do_tempo: [] }).chance_recuperar).toBeNull()
  })

  it('sem desconto por anomalia, nada muda: o texto da decisão de risco aparece como veio', () => {
    expect(d.desconto_anomalia_pct).toBeNull()
    expect(d.linha_do_tempo.find((e) => e.titulo === 'Decisão registrada: avaliação da cobrança')?.detalhe).toBe(
      'Pesaram, nesta ordem: 2 meses como cliente (reduziu a chance).',
    )
    // Um backend anterior, que não manda o campo, dá no mesmo.
    expect(adaptarDetalhe({ ...DETALHE, diagnostico: { ...DETALHE.diagnostico!, desconto_por_anomalia: null } }).desconto_anomalia_pct).toBeNull()
  })

  it('com desconto por anomalia, o painel fica com um número só: o que o sistema usou', () => {
    const comDesconto = adaptarDetalhe({
      ...DETALHE,
      diagnostico: { ...DETALHE.diagnostico!, desconto_por_anomalia: { percentual: 30, pontuacao_antes: 24, pontuacao_usada: 16 } },
      linha_do_tempo: [
        { quando: '2026-09-03T09:00:00-03:00', tipo: 'diagnostico', dados: { estrategia: 'mensagem_pagamento', recovery_score: 16, p_recovery: 0.1677 } },
        { quando: '2026-09-03T09:00:00-03:00', tipo: 'decisao', dados: { tipo_decisao: 'risco', explicacao: 'O sistema avaliou o risco: pontuação de recuperação 24/100; probabilidade de recuperação 24%.' } },
        { quando: '2026-09-03T09:00:01-03:00', tipo: 'decisao', dados: { tipo_decisao: 'retentativa', explicacao: 'A pontuação de recuperação foi reduzida em 30% por comportamento fora do padrão: de 24/100 para 16/100.' } },
      ],
    })
    expect(comDesconto.chance_recuperar).toBe(0.1677)
    expect(comDesconto.desconto_anomalia_pct).toBe(30)
    const [diagnostico, risco, retentativa] = comDesconto.linha_do_tempo
    expect(diagnostico.titulo).toBe('Diagnóstico: 17% de chance de recuperar')
    // A avaliação inicial não repete a pontuação de antes do desconto.
    expect(risco.detalhe).toBe(avisoDoDesconto(30))
    expect(risco.detalhe).not.toMatch(/24/)
    expect(risco.detalhe).toContain('desconto de 30% por comportamento fora do padrão')
    // A decisão que aplicou o desconto conta de quanto para quanto, como a trilha gravou.
    expect(retentativa.detalhe).toContain('de 24/100 para 16/100')
  })

  it('as contribuições são fator e efeito em texto, com maiúscula, sem pontos', () => {
    expect(d.contribuicoes).toEqual([
      { fator: '2 meses como cliente', efeito: 'Reduziu a chance de recuperar' },
      { fator: 'Saldo insuficiente', efeito: 'Aumentou a chance de recuperar' },
      { fator: 'Fator do diagnóstico sem rótulo legível', efeito: null },
    ])
    expect(temChave(d, 'pontos')).toBe(false)
    expect(adaptarDetalhe({ ...DETALHE, diagnostico: null }).contribuicoes).toEqual([])
  })

  it('as mensagens trazem todas as rodadas, em ordem, com a abordagem da tela', () => {
    expect(d.sugestoes.map((s) => [s.rodada, s.abordagem])).toEqual([
      [1, 'lembrete_cordial'],
      [1, 'facilitacao'],
      [2, 'urgencia_com_respeito'],
    ])
    expect(d.sugestoes[0].recomendada).toBe(true)
  })

  it('texto apagado pelo expurgo chega null, e mensagem sem canal não mostra um canal que o cliente não tem', () => {
    const semCanal = d.sugestoes[1]
    expect(semCanal.texto).toBeNull()
    expect(semCanal.canal).toBe('sem_canal')
    expect(semCanal.nao_entregavel).toBe(true)
    expect(semCanal.motivo_canal).toBe('O cliente não tem telefone nem e-mail na sua base')
  })

  it('escolha_ate, escolhida_por e o modo vêm do backend', () => {
    expect(d.escolha_ate).toBe('2026-09-06T18:05:00-03:00')
    expect(d.escolha_ate).toMatch(COM_FUSO)
    expect(d.escolha_por).toBeNull()
    expect(d.modo_mensagem).toBe('escolha')
    const escolhido = adaptarDetalhe({ ...DETALHE, escolha_ate: null, escolhida_por: 'prazo' })
    expect([escolhido.escolha_ate, escolhido.escolha_por]).toEqual([null, 'prazo'])
  })

  it('o motivo do descarte vira frase', () => {
    const descartado = adaptarDetalhe({ ...DETALHE, ciclo: { ...AGUARDANDO, estado: 'descartado', status: 'encerrado_sem_recuperacao', motivo_descarte: 'eprofit_nao_positivo' } })
    expect(descartado.motivo_descarte).toBe('Retorno esperado abaixo do custo da ação')
    expect(d.motivo_descarte).toBeNull()
  })
})

describe('linha do tempo', () => {
  const eventos = adaptarDetalhe(DETALHE).linha_do_tempo

  it('cada evento vira {em, tipo, titulo, detalhe, tom}, com título em português', () => {
    expect(eventos.map((e) => [e.tipo, e.titulo])).toEqual([
      ['abertura', 'Cobrança falhou'],
      ['diagnostico', 'Diagnóstico: 72% de chance de recuperar'],
      ['diagnostico', 'Decisão registrada: avaliação da cobrança'],
      ['tentativa', 'Tentativa 1 enviada ao banco'],
      ['tentativa', 'Tentativa 1 falhou'],
      ['sugestoes', '3 mensagens sugeridas'],
    ])
    expect(eventos[0]).toEqual({ em: '2026-09-03T09:00:00-03:00', tipo: 'abertura', titulo: 'Cobrança falhou', detalhe: 'Saldo insuficiente', tom: 'danger' })
    expect(eventos[5].detalhe).toBe('Recomendada: Lembrete cordial. Canal: E-mail.')
  })

  it('toda data tem fuso e todo título começa com maiúscula', () => {
    for (const e of eventos) {
      expect(e.em).toMatch(COM_FUSO)
      expect(e.titulo.charAt(0)).toBe(e.titulo.charAt(0).toUpperCase())
    }
  })

  it('a reserva interna do envio não aparece; a mensagem enviada, sim', () => {
    expect(adaptarEvento({ quando: '2026-09-11T10:00:00-03:00', tipo: 'mensagem_reservada', dados: {} })).toBeNull()
    expect(adaptarEvento({ quando: '2026-09-11T10:00:00-03:00', tipo: 'mensagem_enviada', dados: {} })).toMatchObject({ tipo: 'mensagem', titulo: 'Mensagem enviada', tom: 'ok' })
  })

  it('os desfechos: recuperado com o líquido, perdido com o motivo, descartado com a frase', () => {
    const quando = '2026-09-13T09:00:00-03:00'
    const recuperado = adaptarEvento({ quando, tipo: 'recuperado', dados: { valor_liquido: 254.92 } })
    expect(recuperado).toMatchObject({ tipo: 'desfecho', titulo: 'Pagamento recuperado', tom: 'ok' })
    expect(recuperado?.detalhe).toContain('254,92')
    expect(adaptarEvento({ quando, tipo: 'perdido', dados: { motivo_perdido: 'sem_canal' } })?.detalhe).toBe('Não havia canal para entregar a mensagem em 30 dias.')
    expect(adaptarEvento({ quando, tipo: 'perdido', dados: { motivo_perdido: null } })?.titulo).toBe('Encerrado sem recuperação')
    expect(adaptarEvento({ quando, tipo: 'descartado', dados: { motivo_descarte: 'score_abaixo_do_corte' } })).toMatchObject({ titulo: 'Encerrado sem ação', detalhe: 'Chance de recuperação baixa demais para agir' })
  })

  it('escolha, regeração, sem canal e resultados de tentativa', () => {
    const quando = '2026-09-06T11:00:00-03:00'
    expect(adaptarEvento({ quando, tipo: 'mensagem_escolhida', dados: { rodada: 1, abordagem: 'urgencia_respeitosa', escolhida_por: 'admin' } })).toMatchObject({
      tipo: 'escolha',
      titulo: 'Mensagem escolhida: Urgência com respeito',
      detalhe: 'Escolhida por um administrador.',
    })
    expect(adaptarEvento({ quando, tipo: 'mensagem_escolhida', dados: { abordagem: 'lembrete_cordial', escolhida_por: 'prazo' } })?.detalhe).toBe('O prazo de escolha acabou: saiu a recomendada.')
    expect(adaptarEvento({ quando, tipo: 'sugestoes_geradas', dados: { rodada: 2, recomendada: 'facilitacao', canal: 'whatsapp' } })?.titulo).toBe('Outras 3 mensagens sugeridas (2ª rodada)')
    expect(adaptarEvento({ quando, tipo: 'mensagem_nao_entregavel', dados: { motivo_canal: 'sem_mapeamento' } })).toMatchObject({ tipo: 'aviso', titulo: 'Sem canal disponível', tom: 'warn' })
    expect(adaptarEvento({ quando, tipo: 'tentativa_resultado', dados: { numero: 2, resultado: 'paga' } })).toMatchObject({ titulo: 'Tentativa 2 paga', tom: 'ok' })
    expect(adaptarEvento({ quando, tipo: 'tentativa_resultado', dados: { numero: 3, resultado: 'sem_retorno' } })).toMatchObject({ titulo: 'Tentativa 3 sem resposta do banco', tom: 'warn' })
    expect(adaptarEvento({ quando, tipo: 'tentativa_agendada', dados: { numero: 2 } })?.titulo).toBe('Tentativa 2 agendada')
    expect(adaptarEvento({ quando, tipo: 'tentativa_cancelada', dados: { numero: 3, motivo: 'recuperado' } })).toMatchObject({ titulo: 'Tentativa 3 cancelada', detalhe: 'O pagamento entrou antes' })
  })

  it('evento de tipo desconhecido não some nem quebra a tela', () => {
    expect(adaptarEvento({ quando: '2026-10-01T09:00:00-03:00', tipo: 'tipo_do_futuro', dados: { x: 1 } })).toEqual({
      em: '2026-10-01T09:00:00-03:00',
      tipo: 'aviso',
      titulo: 'Evento registrado',
    })
  })
})

describe('estorno (Etapa 2, Bloco 5)', () => {
  it('ciclo sem estorno: o campo é null, e um backend anterior ao Bloco 5 não quebra', () => {
    expect(adaptarCiclo(RECUPERADO).estorno).toBeNull()
    expect(adaptarCiclo({ ...RECUPERADO, estorno: null, estorno_parcial: false, motivo_encerramento: null }).estorno).toBeNull()
  })

  it('devolução total: estado recuperado, status encerrado, sem líquido', () => {
    const c = adaptarCiclo({
      ...RECUPERADO,
      status: 'encerrado_sem_recuperacao',
      valor_liquido: null,
      estorno: { valor_devolvido: 299.9, total: true, ultimo_em: '2026-09-15T10:00:00-03:00' },
      estorno_parcial: false,
      motivo_encerramento: 'estorno_no_prazo',
    })
    expect([c.estado, c.status, c.valor_liquido]).toEqual(['recuperado', 'encerrado', null])
    expect(c.valor_cobranca).toBe(299.9)
    expect(c.estorno).toEqual({ valor_devolvido: 299.9, total: true, parcial: false, ultimo_em: '2026-09-15T10:00:00-03:00' })
    expect(c.estorno?.ultimo_em).toMatch(COM_FUSO)
  })

  it('devolução parcial: continua recuperado, com o líquido que o backend já reduziu', () => {
    const c = adaptarCiclo({
      ...RECUPERADO,
      valor_liquido: 143.95,
      estorno: { valor_devolvido: 119.96, total: false, ultimo_em: '2026-09-15T10:00:00-03:00' },
      estorno_parcial: true,
    })
    expect([c.status, c.valor_liquido]).toEqual(['recuperado', 143.95])
    expect(c.estorno).toMatchObject({ valor_devolvido: 119.96, total: false, parcial: true })
  })

  it('o evento de estorno na linha do tempo: total, parcial e fora do prazo', () => {
    const quando = '2026-09-15T10:00:00-03:00'
    const total = adaptarEvento({ quando, tipo: 'estorno', dados: { valor_devolvido: 299.9, no_prazo: true, total: true } })
    expect(total).toMatchObject({ em: quando, tipo: 'aviso', titulo: 'Pagamento devolvido dentro do prazo', tom: 'danger' })
    expect(total?.detalhe).toContain('299,90')
    expect(total?.detalhe).toContain('deixou de contar como recuperada')

    const parcial = adaptarEvento({ quando, tipo: 'estorno', dados: { valor_devolvido: 119.96, no_prazo: true, total: false } })
    expect(parcial?.detalhe).toContain('O valor saiu do que foi recuperado para você.')

    const tarde = adaptarEvento({ quando, tipo: 'estorno', dados: { valor_devolvido: 299.9, no_prazo: false, total: false } })
    expect(tarde).toMatchObject({ titulo: 'Pagamento devolvido depois do prazo', tom: 'neutro' })
    expect(tarde?.detalhe).toContain('os valores não mudam')
  })

  it('o detalhe de um ciclo estornado converte, com o evento na linha do tempo e sem fee', () => {
    const d = adaptarDetalhe({
      ...DETALHE,
      ciclo: { ...RECUPERADO, status: 'encerrado_sem_recuperacao', valor_liquido: null, estorno: { valor_devolvido: 299.9, total: true, ultimo_em: '2026-09-15T10:00:00-03:00' }, motivo_encerramento: 'estorno_no_prazo' },
      escolha_ate: null,
      linha_do_tempo: [
        ...DETALHE.linha_do_tempo,
        { quando: '2026-09-13T09:00:00-03:00', tipo: 'recuperado', dados: { valor_liquido: 254.92 } },
        { quando: '2026-09-15T10:00:00-03:00', tipo: 'estorno', dados: { valor_devolvido: 299.9, no_prazo: true, total: true } },
      ],
    })
    expect(d.status).toBe('encerrado')
    expect(d.linha_do_tempo.map((e) => e.titulo).slice(-2)).toEqual(['Pagamento recuperado', 'Pagamento devolvido dentro do prazo'])
    expect(d.linha_do_tempo.some((e) => e.titulo === 'Evento registrado')).toBe(false)
    expect(temChave(d, 'fee')).toBe(false)
  })

  it('o mês com estorno: o líquido já vem descontado e pode ser negativo', () => {
    const mes = adaptarMetricas(
      {
        mes: '2026-10',
        inicio: '2026-10-01T00:00:00-03:00',
        fim: '2026-11-01T00:00:00-03:00',
        valor_liquido_recuperado: -239.92,
        recuperados: 0,
        encerrados_sem_recuperacao: 0,
        taxa_recuperacao: null,
        ciclos_abertos_no_mes: { em_analise: 0, em_processo: 0, recuperado: 0, encerrado_sem_recuperacao: 0 },
        aguardando_escolha: 0,
        estornos: { quantidade: 1, ciclos_estornados_por_inteiro: 1, valor_liquido_estornado: 239.92 },
      },
      0,
    )
    expect(mes.valor_liquido_recuperado).toBe(-239.92)
    expect(mes.taxa_recuperacao).toBeNull()
  })
})

describe('abordagem e canal', () => {
  it('o código da abordagem vai e volta', () => {
    expect(abordagemDaTela('urgencia_respeitosa')).toBe('urgencia_com_respeito')
    expect(abordagemParaApi('urgencia_com_respeito')).toBe('urgencia_respeitosa')
    expect(abordagemParaApi('lembrete_cordial')).toBe('lembrete_cordial')
    expect(abordagemParaApi(abordagemDaTela('facilitacao'))).toBe('facilitacao')
  })

  it('o motivo do canal vira frase', () => {
    expect(motivoDoCanal('contato_da_base')).toBe('Contato cadastrado na sua base')
    expect(motivoDoCanal('sem_mapeamento')).toBe('O cliente desta cobrança não está na sua base')
    expect(motivoDoCanal('codigo_novo')).toBe('Canal escolhido pelo sistema')
  })

  it('a mensagem adaptada não carrega data nem campo a mais', () => {
    const m = adaptarMensagem(DETALHE.mensagens[0])
    expect(Object.keys(m).sort()).toEqual(['abordagem', 'canal', 'escolhida', 'motivo_canal', 'nao_entregavel', 'recomendada', 'rodada', 'texto'])
  })
})

describe('métricas', () => {
  /** A resposta de `GET /metrics/involuntario/mes` do Relatório 2. */
  const MES: MetricasMesApi = {
    mes: '2026-09',
    inicio: '2026-09-01T00:00:00-03:00',
    fim: '2026-10-01T00:00:00-03:00',
    valor_liquido_recuperado: 297.92,
    recuperados: 2,
    encerrados_sem_recuperacao: 2,
    taxa_recuperacao: 0.5,
    ciclos_abertos_no_mes: { em_analise: 1, em_processo: 0, recuperado: 3, encerrado_sem_recuperacao: 1 },
    aguardando_escolha: 1,
  }

  it('o mês: líquido, contagens e taxa; os ciclos ativos vêm da contagem de agora', () => {
    expect(adaptarMetricas(MES, 4)).toEqual({
      mes: '2026-09',
      valor_liquido_recuperado: 297.92,
      ciclos_ativos: 4,
      recuperados: 2,
      encerrados_sem_recuperacao: 2,
      aguardando_escolha: 1,
      taxa_recuperacao: 0.5,
      proxima_acao: null,
    })
  })

  it('sem desfecho no mês a taxa continua null (zero diria que tudo se perdeu)', () => {
    expect(adaptarMetricas({ ...MES, taxa_recuperacao: null }, 0).taxa_recuperacao).toBeNull()
  })

  it('a série vira um ponto por dia com o valor líquido', () => {
    const serie = adaptarSerie({
      dias: 2,
      pontos: [
        { dia: '2026-09-04', valor_liquido_recuperado: 0, recuperados: 0, encerrados_sem_recuperacao: 0, taxa_recuperacao: null },
        { dia: '2026-09-05', valor_liquido_recuperado: 85, recuperados: 1, encerrados_sem_recuperacao: 0, taxa_recuperacao: 1 },
      ],
    })
    expect(serie).toEqual([
      { dia: '2026-09-04', valor: 0 },
      { dia: '2026-09-05', valor: 85 },
    ])
  })
})

describe('configuração', () => {
  /** A resposta de `GET /configuracao` (Bloco 4) sem nada gravado. */
  const PADRAO: ConfiguracaoApi = {
    modo_mensagem_involuntario: 'escolha',
    prazo_escolha_horas: 8,
    janela_contato_inicio: '08:00',
    janela_contato_fim: '20:00',
    canais_permitidos: ['whatsapp', 'email'],
    modo_mensagem_voluntario: 'escolha',
    retencao_mensagens_dias: 90,
    retencao_ciclos_meses: 24,
    retencao_base_meses_apos_contrato: 6,
    retencao_trilha_anos: 5,
    posicao_grave_pct: 10,
    posicao_preocupante_pct: 20,
  }
  const NOTIFICACOES: Pick<Configuracao, 'notificacoes'> = { notificacoes: { resumo_semanal: true, risco_grave: false, escolha_pendente: true } }

  it('a resposta do backend vira o tipo da tela', () => {
    expect(adaptarConfiguracao(PADRAO, NOTIFICACOES)).toEqual({
      modo_mensagem_involuntario: 'escolha',
      prazo_escolha_horas: 8,
      janela_contato: { inicio: 8, fim: 20 },
      canais: ['whatsapp', 'email'],
      intervalo_minimo_ofertas_dias: 30, // Rodada 4: o campo novo; ausente na resposta, vale o padrão do backend
      notificacoes: { resumo_semanal: true, risco_grave: false, escolha_pendente: true },
      retencao_dias: { mensagens: 90, ciclos_meses: 24, base_meses_apos_contrato: 6, trilha_anos: 5 },
    })
    expect(adaptarConfiguracao({ ...PADRAO, intervalo_minimo_ofertas_dias: 45 }, NOTIFICACOES).intervalo_minimo_ofertas_dias).toBe(45)
  })

  it('as horas: "08:00" é 8, "24:00" é 24, e de volta', () => {
    expect([horaDaTela('08:00'), horaDaTela('24:00'), horaDaTela('18:30'), horaDaTela('00:00')]).toEqual([8, 24, 18, 0])
    expect([horaParaApi(8), horaParaApi(24), horaParaApi(0)]).toEqual(['08:00', '24:00', '00:00'])
  })

  it('o PUT leva só o que mudou', () => {
    const lida = adaptarConfiguracao(PADRAO, NOTIFICACOES)
    expect(configuracaoParaApi(lida, lida)).toEqual({})
    expect(configuracaoParaApi({ ...lida, modo_mensagem_involuntario: 'automatico' }, lida)).toEqual({ modo_mensagem_involuntario: 'automatico' })
    expect(configuracaoParaApi({ ...lida, prazo_escolha_horas: 4, janela_contato: { inicio: 9, fim: 20 } }, lida)).toEqual({
      prazo_escolha_horas: 4,
      janela_contato_inicio: '09:00',
    })
    expect(configuracaoParaApi({ ...lida, canais: ['email', 'whatsapp'] }, lida)).toEqual({ canais_permitidos: ['email', 'whatsapp'] })
  })

  it('janela com minutos não é regravada sem querer, notificações e SMS não vão ao backend', () => {
    const lida = adaptarConfiguracao({ ...PADRAO, janela_contato_fim: '18:30' }, NOTIFICACOES)
    expect(lida.janela_contato.fim).toBe(18)
    const nova: Configuracao = {
      ...lida,
      prazo_escolha_horas: 12,
      canais: ['whatsapp', 'email', 'sms'],
      notificacoes: { resumo_semanal: false, risco_grave: false, escolha_pendente: false },
    }
    expect(configuracaoParaApi(nova, lida)).toEqual({ prazo_escolha_horas: 12 })
  })
})

describe('saúde', () => {
  const DEMO: SaudeSistema = {
    relogio: { ativo: true, ultima_passagem: '2026-09-30T14:19:20-03:00' },
    modelos: { carregados: 3, total: 3 },
    redator: { disponivel: true },
    base: { origem: 'api', atualizada_em: '2026-09-30T12:20:00-03:00' },
  }
  const saude = (relogio: Partial<{ ligado: boolean; motivo_desligado: string | null; ultima_passagem_em: string | null; ultima_passagem_ok: boolean | null }>) =>
    adaptarSaude({ status: 'ok', relogio: { ligado: true, motivo_desligado: null, ultima_passagem_em: '2026-10-03T16:45:52-03:00', ultima_passagem_ok: true, ...relogio } }, DEMO)

  it('o relógio é o do backend, com a data com fuso', () => {
    const s = saude({})
    expect(s.relogio).toEqual({ ativo: true, ultima_passagem: '2026-10-03T16:45:52-03:00' })
    expect(s.relogio.ultima_passagem).toMatch(COM_FUSO)
  })

  it('relógio desligado, ou com a última passagem em falha, não aparece como ativo', () => {
    expect(saude({ ligado: false, motivo_desligado: 'mais_de_um_worker' }).relogio.ativo).toBe(false)
    expect(saude({ ultima_passagem_ok: false }).relogio.ativo).toBe(false)
    expect(saude({ ultima_passagem_em: null, ultima_passagem_ok: null }).relogio).toEqual({ ativo: true, ultima_passagem: null })
  })

  it('o que o backend ainda não informa fica marcado como demonstração', () => {
    expect(saude({}).demonstracao).toEqual(['modelos', 'redator', 'base'])
  })
})

describe('chaves de API (Rodada 2)', () => {
  // Chave de exemplo, visivelmente falsa: o backend manda só o começo e o final.
  const DO_BACKEND = {
    id: 'chv_0123456789abcdef',
    nome: 'Sistema de cobrança',
    prefixo: 'crai_live_EXEM',
    final: 'PLO0',
    criada_em: '2026-10-04T09:00:00-03:00',
    criada_por_papel: 'admin',
    ultimo_uso_em: null,
    revogada_em: null,
    situacao: 'ativa' as const,
    usos_hoje: 3,
  }

  it('a chave da tela tem o nome, o começo e o final, e nada além do que a tela mostra', () => {
    const chave = adaptarChave(DO_BACKEND)
    expect(chave).toEqual({
      id: 'chv_0123456789abcdef',
      nome: 'Sistema de cobrança',
      inicio: 'crai_live_EXEM',
      final: 'PLO0',
      criada_em: '2026-10-04T09:00:00-03:00',
      ultimo_uso: null,
      revogada_em: null,
    })
    expect('ambiente' in chave).toBe(false)
  })

  it('campo a mais na resposta não chega à tela', () => {
    const comExtra = { ...DO_BACKEND, hash: 'nao-deveria-vir', chave_inteira: 'nao-deveria-vir' }
    expect(JSON.stringify(adaptarChave(comExtra))).not.toContain('nao-deveria-vir')
  })

  it('a lista leva o que a pessoa pode fazer', () => {
    const revogada = { ...DO_BACKEND, id: 'chv_2', revogada_em: '2026-10-04T10:00:00-03:00', situacao: 'revogada' as const, ultimo_uso_em: '2026-10-04T09:30:00-03:00' }
    const lista = adaptarChaves({ chaves: [DO_BACKEND, revogada], ativas: 1, limite_ativas: 5, pode_revogar: true, plano_permite_gerar: false, pode_gerar: false })
    expect([lista.ativas, lista.limite_ativas, lista.pode_gerar]).toEqual([1, 5, false])
    // Plano essencial com papel de dono: revoga, mas não gera.
    expect([lista.pode_revogar, lista.plano_permite_gerar]).toEqual([true, false])
    expect(lista.chaves.map((c) => [c.id, c.ultimo_uso, c.revogada_em])).toEqual([
      ['chv_0123456789abcdef', null, null],
      ['chv_2', '2026-10-04T09:30:00-03:00', '2026-10-04T10:00:00-03:00'],
    ])
  })
})

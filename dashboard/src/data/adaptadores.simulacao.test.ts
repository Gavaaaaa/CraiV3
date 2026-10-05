/**
 * Rodada 3, Fase 3: os adaptadores da simulação do gateway contra respostas DE VERDADE do
 * backend (`testes/simulacaoDeExemplo.ts`). O que se mede: a tela deriva só a etapa do
 * desenho; o diagnóstico, as tentativas, as mensagens e as frases são os do backend; a `fee`
 * não passa; e a verdade escondida só existe dentro do cliente fictício.
 */
import { describe, expect, it } from 'vitest'
import { adaptarCiclo, adaptarRetencaoSimulada, adaptarSimulacao, type SimulacaoApi } from './adaptadores'
import {
  CICLO_SIMULADO,
  RETENCAO_ACEITA,
  RETENCAO_PELO_MODELO_COM_OFERTA,
  RETENCAO_PELO_MODELO_MAIS_LEVE,
  RETENCAO_PELO_MODELO_SEM_OFERTA,
  RETENCAO_RECUSADA,
  RETENCAO_SEM_RISCO,
  SIM_COBRADA,
  SIM_ENVIADA,
  SIM_MENSAGENS,
  SIM_RECUPERADA,
  SIM_VAZIA,
} from '../testes/simulacaoDeExemplo'

const AGORA = '2026-10-04T12:00:00-03:00'

function chaves(valor: unknown, saida: string[] = []): string[] {
  if (Array.isArray(valor)) valor.forEach((v) => chaves(v, saida))
  else if (valor && typeof valor === 'object') {
    for (const [k, v] of Object.entries(valor)) {
      saida.push(k)
      chaves(v, saida)
    }
  }
  return saida
}

describe('adaptarSimulacao', () => {
  it('empresa que nunca simulou: formulário em branco, com a configuração da empresa', () => {
    const e = adaptarSimulacao(SIM_VAZIA, AGORA)
    expect(e.fase).toBe('formulario')
    expect(e.cliente).toBeNull()
    expect(e.id_ciclo).toBeNull()
    expect(e.hoje).toBe(AGORA)
    expect(e.tentativas).toEqual([])
    expect(e.modo_mensagem).toBe(SIM_VAZIA.modo_mensagem)
    expect(e.prazo_escolha_horas).toBe(SIM_VAZIA.prazo_escolha_horas)
  })

  it('cliente criado e ainda não cobrado: a tela continua no formulário', () => {
    const naoCobrado: SimulacaoApi = { ...SIM_COBRADA, cobranca: { feita: false, em: null, resultado: null, causa: null, causa_legivel: null }, ciclo: null, tentativas: [] }
    expect(adaptarSimulacao(naoCobrado, AGORA).fase).toBe('formulario')
  })

  it('cobrança recusada: o diagnóstico e o plano são os do backend', () => {
    const e = adaptarSimulacao(SIM_COBRADA, AGORA)
    expect(e.fase).toBe('recusada')
    expect(e.etapas_concluidas).toEqual(['cobranca'])
    expect(e.etapa_atual).toBe('tentativa_1')
    expect(e.causa).toBe('insufficient_funds')
    expect(e.id_ciclo).toBe(SIM_COBRADA.ciclo!.ciclo.id)
    expect(e.id_recorrencia).toBe(SIM_COBRADA.id_recorrencia)
    expect(e.hoje).toBe(SIM_COBRADA.relogio!.agora)
    expect(e.inicio).toBe(SIM_COBRADA.cobranca!.em)
    expect(e.chance_recuperar).toBe(SIM_COBRADA.chance_recuperar)
    expect(e.dia_provavel_saldo).toBe(SIM_COBRADA.dia_provavel_saldo)
    expect(e.tentativas.map((t) => [t.numero, t.resultado, t.causa])).toEqual([
      [1, 'agendada', null],
      [2, 'agendada', null],
      [3, 'agendada', null],
    ])
    expect(e.tentativas.map((t) => t.agendada_para)).toEqual(SIM_COBRADA.tentativas.map((t) => t.agendada_para))
    expect(e.proxima_acao).toEqual({ quando: SIM_COBRADA.proxima_acao!.quando, descricao: 'Tentativa 1' })
    // As contribuições são as do diagnóstico de verdade, em texto, com maiúscula.
    const doBackend = SIM_COBRADA.ciclo!.diagnostico!.contribuicoes
    expect(e.contribuicoes).toHaveLength(doBackend.length)
    expect(e.contribuicoes.length).toBeGreaterThan(0)
    e.contribuicoes.forEach((c, i) => {
      expect(c.fator.toLowerCase()).toBe(doBackend[i].fator.toLowerCase())
      expect(c.fator[0]).toBe(c.fator[0].toUpperCase())
      expect(c.efeito).toMatch(/^(Aumentou|Reduziu) a chance de recuperar$/)
    })
    expect(e.pensando).toHaveLength(SIM_COBRADA.pensando.length)
    expect(e.pensando[0]).toBe('Causa da falha: saldo insuficiente.')
    expect(e.sem_crai).toEqual(SIM_COBRADA.sem_crai)
    expect(e.sugestoes).toEqual([])
    expect(e.mensagem_enviada).toBeNull()
    expect(e.desfecho).toBeNull()
    expect(e.linha_do_tempo.length).toBeGreaterThan(0)
  })

  it('as três tentativas falharam: as 3 mensagens do backend, com o texto e a recomendada', () => {
    const e = adaptarSimulacao(SIM_MENSAGENS, AGORA)
    expect(e.fase).toBe('mensagens')
    expect(e.etapa_atual).toBe('mensagem')
    expect(e.etapas_concluidas).toEqual(['cobranca', 'tentativa_1', 'tentativa_2', 'tentativa_3'])
    expect(e.tentativas.map((t) => t.resultado)).toEqual(['falhou', 'falhou', 'falhou'])
    expect(e.causa).toBe('insufficient_funds')
    expect(e.sugestoes).toHaveLength(3)
    expect(e.sugestoes.map((s) => s.abordagem).sort()).toEqual(['facilitacao', 'lembrete_cordial', 'urgencia_com_respeito'])
    expect(e.sugestoes.filter((s) => s.recomendada)).toHaveLength(1)
    const textos = SIM_MENSAGENS.ciclo!.mensagens.map((m) => m.texto)
    for (const s of e.sugestoes) expect(textos).toContain(s.texto)
    expect(e.modo_mensagem).toBe('escolha')
    expect(e.proxima_acao?.descricao).toBe('Envio automático da recomendada')
  })

  it('mensagem enviada: quem escolheu, por onde, e o que a tela espera', () => {
    const e = adaptarSimulacao(SIM_ENVIADA, AGORA)
    expect(e.fase).toBe('mensagem_enviada')
    expect(e.etapa_atual).toBe('desfecho')
    expect(e.etapas_concluidas).toContain('mensagem')
    expect(e.mensagem_enviada).toMatchObject({ abordagem: 'facilitacao', canal: 'whatsapp', escolhida_por: 'owner' })
    expect(e.mensagem_enviada!.em).toBeTruthy()
    expect(e.proxima_acao?.descricao).toBe('Prazo para resposta do cliente')
  })

  it('recuperada pela mensagem: o líquido do backend, sem conta na tela', () => {
    const e = adaptarSimulacao(SIM_RECUPERADA, AGORA)
    expect(e.fase).toBe('recuperada')
    expect(e.etapas_concluidas).toEqual(['cobranca', 'tentativa_1', 'tentativa_2', 'tentativa_3', 'mensagem', 'desfecho'])
    expect(e.desfecho).toEqual(SIM_RECUPERADA.desfecho)
    expect(e.desfecho).toMatchObject({ tipo: 'recuperado', via: 'mensagem', tentativa: null })
    expect(e.proxima_acao).toBeNull()
    expect(e.pensando[e.pensando.length - 1]).toMatch(/^Pagamento recuperado depois da mensagem\./)
  })

  it('encerrado sem recuperação e pago de primeira', () => {
    const encerrado: SimulacaoApi = { ...SIM_ENVIADA, desfecho: { tipo: 'encerrado', via: 'mensagem', tentativa: null, valor_liquido: 0, em: null }, proxima_acao: null }
    const e = adaptarSimulacao(encerrado, AGORA)
    expect(e.fase).toBe('encerrada')
    expect(e.desfecho?.em).toBe(encerrado.relogio!.agora)
    const dePrimeira: SimulacaoApi = {
      ...SIM_COBRADA,
      cobranca: { feita: true, em: SIM_COBRADA.cobranca!.em, resultado: 'paga', causa: null, causa_legivel: null },
      ciclo: null,
      tentativas: [],
      proxima_acao: null,
      desfecho: { tipo: 'recuperado', via: 'tentativa', tentativa: 0, valor_liquido: 890, em: SIM_COBRADA.cobranca!.em },
      pensando: ['A cobrança passou no dia do vencimento.'],
    }
    const pago = adaptarSimulacao(dePrimeira, AGORA)
    expect(pago.fase).toBe('recuperada')
    expect(pago.id_ciclo).toBeNull()
    expect(pago.causa).toBeNull()
    expect(pago.desfecho?.tentativa).toBe(0)
  })

  it('nenhum estado adaptado leva a fee, e a verdade escondida só existe dentro do cliente', () => {
    for (const exemplo of [SIM_VAZIA, SIM_COBRADA, SIM_MENSAGENS, SIM_ENVIADA, SIM_RECUPERADA]) {
      const e = adaptarSimulacao(exemplo, AGORA)
      const todas = chaves(e)
      expect(todas.filter((k) => /fee/i.test(k))).toEqual([])
      const { cliente, ...resto } = e
      expect(chaves(resto).filter((k) => ['verdade', 'dias_ate_saldo', 'chance_pagar', 'vai_revogar'].includes(k))).toEqual([])
      if (cliente) expect(cliente.verdade).toEqual(exemplo.cliente!.verdade)
    }
  })

  it('todo texto começa com maiúscula', () => {
    for (const exemplo of [SIM_COBRADA, SIM_MENSAGENS, SIM_ENVIADA, SIM_RECUPERADA]) {
      const e = adaptarSimulacao(exemplo, AGORA)
      const textos = [...e.pensando, ...e.contribuicoes.map((c) => c.fator), ...e.linha_do_tempo.map((l) => l.titulo), e.proxima_acao?.descricao ?? 'A', e.sem_crai?.explicacao ?? 'A']
      for (const t of textos) expect(t[0], t).toBe(t[0].toUpperCase())
    }
  })
})

describe('adaptarRetencaoSimulada', () => {
  it('oferta aceita: a oferta, o canal e o valor são os do backend', () => {
    const r = adaptarRetencaoSimulada(RETENCAO_ACEITA)
    expect(r.faixa).toBe(RETENCAO_ACEITA.faixa)
    expect(r.oferta).toBe(RETENCAO_ACEITA.oferta)
    expect(r.oferta_legivel?.toLowerCase()).toBe(RETENCAO_ACEITA.oferta_legivel?.toLowerCase())
    expect(r.oferta_legivel![0]).toBe(r.oferta_legivel![0].toUpperCase())
    expect(r.canal_legivel).toBe(RETENCAO_ACEITA.canal_legivel)
    expect(r.aceitou).toBe(true)
    expect(r.valor_mantido_liquido).toBe(RETENCAO_ACEITA.valor_mantido_liquido)
    expect(r.decidido_por).toBe(RETENCAO_ACEITA.decidido_por)
    expect(r.risco).toBe(RETENCAO_ACEITA.risco)
    expect(r.corte_de_intervencao).toBe(RETENCAO_ACEITA.corte_de_intervencao)
    expect(r.risco! >= r.corte_de_intervencao).toBe(true)
    expect(r.meses_de_mrr).toBe(RETENCAO_ACEITA.meses_de_mrr)
    expect(r.prazo_estorno_dias).toBe(RETENCAO_ACEITA.prazo_estorno_dias)
    expect(r.motivo[0]).toBe(r.motivo[0].toUpperCase())
  })

  it('recusada e sem risco', () => {
    const recusada = adaptarRetencaoSimulada(RETENCAO_RECUSADA)
    expect(recusada.aceitou).toBe(false)
    expect(recusada.valor_mantido_liquido).toBe(0)
    expect(recusada.oferta).not.toBeNull()
    const semRisco = adaptarRetencaoSimulada(RETENCAO_SEM_RISCO)
    expect(semRisco).toMatchObject({ faixa: 'sem_risco', oferta: null, oferta_legivel: null, canal_legivel: null, porque: null, aceitou: null, valor_mantido_liquido: 0 })
    expect(semRisco.risco! < semRisco.corte_de_intervencao).toBe(true)
    // Backend anterior, sem o risco na resposta: a tela não inventa um número.
    const { risco: _r, corte_de_intervencao: _c, ...antigo } = RETENCAO_SEM_RISCO
    expect(adaptarRetencaoSimulada(antigo).risco).toBeNull()
  })

  it('com o modelo de IA decidindo, o porquê de não haver oferta é a frase do backend', () => {
    // O corte fixo não vale quando o modelo decide: o backend manda nulo e explica pela regra.
    expect(RETENCAO_PELO_MODELO_SEM_OFERTA.corte_de_intervencao).toBeNull()
    const sem = adaptarRetencaoSimulada(RETENCAO_PELO_MODELO_SEM_OFERTA)
    expect(sem.decidido_por).toBe('modelo')
    expect(sem.oferta).toBeNull()
    expect(sem.sem_oferta_porque).toBe(RETENCAO_PELO_MODELO_SEM_OFERTA.sem_oferta_porque)
    expect(sem.sem_oferta_porque).toMatch(/^Quem decidiu o risco foi o modelo de IA\./)
    expect(sem.risco).toBe(RETENCAO_PELO_MODELO_SEM_OFERTA.risco)

    // Intenção explícita: há oferta mesmo com o risco baixo e a faixa "sem risco" pela posição.
    const com = adaptarRetencaoSimulada(RETENCAO_PELO_MODELO_COM_OFERTA)
    expect(com.decidido_por).toBe('modelo')
    expect(com.faixa).toBe('sem_risco')
    expect(com.oferta).toBe(RETENCAO_PELO_MODELO_COM_OFERTA.oferta)
    expect(com.sem_oferta_porque).toBeNull()
    expect(com.porque).toMatch(/^O cliente mostrou intenção explícita de sair\./)
    expect(com.risco! < 0.6).toBe(true)
  })

  it('a mais leve é uma oferta de retenção de verdade, nunca a troca para Pix ou boleto', () => {
    const leve = adaptarRetencaoSimulada(RETENCAO_PELO_MODELO_MAIS_LEVE)
    expect(RETENCAO_PELO_MODELO_MAIS_LEVE.intensidade).toBe('oferta_mais_leve')
    expect(leve.faixa).toBe('preocupante')
    expect(leve.oferta).toBe('desconto_10')
    expect(leve.porque).toMatch(/oferta de retenção de menor custo/)
    // Por intenção explícita sai a do bandit, qualquer que seja a faixa.
    expect(RETENCAO_PELO_MODELO_COM_OFERTA.intensidade).toBe('oferta_do_bandit')
    expect(RETENCAO_PELO_MODELO_COM_OFERTA.regra_de_intervencao).toBe('intencao_explicita')
  })

  it('quando a régua decide, não há frase do backend para o "sem oferta" (a tela explica pelo corte)', () => {
    for (const r of [RETENCAO_ACEITA, RETENCAO_RECUSADA, RETENCAO_SEM_RISCO]) expect(adaptarRetencaoSimulada(r).sem_oferta_porque).toBeNull()
    expect(adaptarRetencaoSimulada({ ...RETENCAO_SEM_RISCO, sem_oferta_porque: '   ' }).sem_oferta_porque).toBeNull()
  })

  it('oferta ou faixa que a tela não conhece não vira outra coisa', () => {
    const r = adaptarRetencaoSimulada({ ...RETENCAO_ACEITA, oferta: 'oferta_nova', faixa: 'sem_dado' })
    expect(r.oferta).toBeNull()
    expect(r.faixa).toBe('sem_risco')
  })

  it('a propensão escondida não vem na resposta', () => {
    expect(chaves(RETENCAO_ACEITA)).not.toContain('propensao')
    expect(chaves(adaptarRetencaoSimulada(RETENCAO_ACEITA))).not.toContain('propensao')
  })
})

describe('adaptarCiclo com a marca de simulação', () => {
  it('ciclo simulado sai marcado; sem o campo, é real', () => {
    expect(adaptarCiclo(CICLO_SIMULADO).simulado).toBe(true)
    expect(adaptarCiclo(CICLO_SIMULADO).cliente).toBe('Ana Souza')
    const { simulado: _fora, ...semCampo } = CICLO_SIMULADO
    expect(adaptarCiclo(semCampo).simulado).toBe(false)
    expect(adaptarCiclo({ ...CICLO_SIMULADO, simulado: false }).simulado).toBe(false)
  })
})

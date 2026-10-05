/**
 * Rodada 3, Fase 2: os adaptadores da visão geral contra a forma das rotas novas
 * (`/metrics/visao-geral`, `/metrics/serie`, `/metrics/involuntario/funil`,
 * `/metrics/o-que-funciona`, `/atividade`, `/extrato` e o `/health` ampliado).
 */
import { describe, expect, it } from 'vitest'
import {
  adaptarAtividade,
  adaptarExtrato,
  adaptarFunil,
  adaptarOQueFunciona,
  adaptarSaude,
  adaptarSerieDupla,
  adaptarVisaoGeral,
  type SaudeApi,
  type VisaoGeralApi,
} from './adaptadores'
import type { SaudeSistema } from './tipos'

const VISAO: VisaoGeralApi = {
  dias: 30,
  periodo: { de: '2026-09-05', ate: '2026-10-04' },
  recuperado_involuntario: 1360,
  cobrancas_recuperadas: 3,
  retido_voluntario: 340,
  clientes_mantidos: 1,
  ciclos_ativos: 2,
  aguardando_escolha: 1,
  clientes_risco_grave: 4,
  risco_grave_com_oferta: 1,
  taxa_recuperacao: 0.6,
  ciclos_com_desfecho: 5,
  mantido: 1700,
}

describe('os cartões', () => {
  it('leva os números como vieram, sem a fee', () => {
    const r = adaptarVisaoGeral(VISAO)
    expect(r).toEqual({
      periodo: { de: '2026-09-05', ate: '2026-10-04' },
      recuperado_involuntario: 1360,
      cobrancas_recuperadas: 3,
      retido_voluntario: 340,
      clientes_mantidos: 1,
      ciclos_ativos: 2,
      aguardando_escolha: 1,
      clientes_risco_grave: 4,
      risco_grave_com_oferta: 1,
      taxa_recuperacao: 0.6,
      ciclos_com_desfecho: 5,
      piloto: false, // Rodada 4: o campo novo; ausente na resposta, a empresa não está em piloto
    })
    expect(adaptarVisaoGeral({ ...VISAO, piloto: true }).piloto).toBe(true)
    expect(JSON.stringify(adaptarVisaoGeral({ ...VISAO, fee: 240 } as VisaoGeralApi))).not.toContain('fee')
  })

  it('fora do plano premium, o voluntário chega como null; sem desfecho, a taxa também', () => {
    const r = adaptarVisaoGeral({ ...VISAO, retido_voluntario: null, clientes_mantidos: null, clientes_risco_grave: null, risco_grave_com_oferta: null, taxa_recuperacao: null })
    expect([r.retido_voluntario, r.clientes_mantidos, r.clientes_risco_grave, r.risco_grave_com_oferta, r.taxa_recuperacao]).toEqual([null, null, null, null, null])
  })
})

describe('a série dos dois churns', () => {
  it('um ponto por dia; o voluntário null (fora do premium) vira zero no gráfico', () => {
    expect(
      adaptarSerieDupla({
        dias: 2,
        pontos: [
          { dia: '2026-10-03', involuntario: 170, voluntario: 340 },
          { dia: '2026-10-04', involuntario: -170, voluntario: null },
        ],
      }),
    ).toEqual([
      { dia: '2026-10-03', involuntario: 170, voluntario: 340 },
      { dia: '2026-10-04', involuntario: -170, voluntario: 0 },
    ])
  })
})

describe('o funil', () => {
  it('só as etapas que a tela conhece, na ordem dela', () => {
    const etapa = (nome: string, chegaram: number) => ({ etapa: nome, rotulo: nome, chegaram, valor: chegaram * 10, recuperados_aqui: 0, valor_recuperado_aqui: 0 })
    const f = adaptarFunil({
      mes: '2026-10',
      etapas: [etapa('mensagem', 2), etapa('etapa_nova', 9), etapa('falhas', 6), etapa('tentativa_1', 5), etapa('tentativa_2', 3), etapa('tentativa_3', 2)],
      desfecho: { recuperados: 3, encerrados: 2, em_andamento: 1 },
    })
    expect(f.mes).toBe('2026-10')
    expect(f.etapas.map((e) => [e.etapa, e.chegaram])).toEqual([
      ['falhas', 6],
      ['tentativa_1', 5],
      ['tentativa_2', 3],
      ['tentativa_3', 2],
      ['mensagem', 2],
    ])
    expect(f.desfecho).toEqual({ recuperados: 3, encerrados: 2, em_andamento: 1 })
  })
})

describe('o que mais funciona', () => {
  it('a taxa é a proporção de sucesso; o líquido só nas causas', () => {
    const r = adaptarOQueFunciona({
      causas: [{ rotulo: 'saldo insuficiente', casos: 4, sucessos: 3, taxa: 0.75, valor_liquido: 1360 }],
      ofertas: [{ rotulo: 'Desconto de 20% por 3 meses', casos: 2, sucessos: 1, taxa: 0.5 }],
      canais: [{ rotulo: 'WhatsApp', casos: 0, sucessos: 0, taxa: null }],
    })
    expect(r.causas).toEqual([{ rotulo: 'Saldo insuficiente', taxa: 0.75, casos: 4, valor: 1360 }])
    expect(r.ofertas).toEqual([{ rotulo: 'Desconto de 20% por 3 meses', taxa: 0.5, casos: 2 }])
    expect(r.canais).toEqual([{ rotulo: 'WhatsApp', taxa: 0, casos: 0 }])
  })

  it('sem desfecho, as três listas vêm vazias', () => {
    expect(adaptarOQueFunciona({ causas: [], ofertas: [], canais: [] })).toEqual({ causas: [], ofertas: [], canais: [] })
  })
})

describe('a atividade', () => {
  it('a frase vem pronta; tipo desconhecido não é mostrado com o ícone de outro', () => {
    const r = adaptarAtividade({
      atividades: [
        { id: 'rec-7', em: '2026-10-04T09:00:00-03:00', tipo: 'recuperado', texto: 'cobrança de Ana Prado recuperada na 1ª tentativa', valor: 170, simulado: false },
        { id: 'x-1', em: '2026-10-04T08:00:00-03:00', tipo: 'tipo_novo', texto: 'Algo de uma versão futura', valor: null, simulado: false },
      ],
    })
    expect(r).toEqual([{ id: 'rec-7', em: '2026-10-04T09:00:00-03:00', tipo: 'recuperado', texto: 'Cobrança de Ana Prado recuperada na 1ª tentativa', valor: 170, simulado: false }])
  })

  it('campo a mais (um contato, por exemplo) não chega à tela', () => {
    const comExtra = { id: 'rec-7', em: '2026-10-04T09:00:00-03:00', tipo: 'recuperado', texto: 'Cobrança recuperada', valor: 1, simulado: false, telefone: '+5511988887777', email: 'pessoa@exemplo.com.br' }
    const texto = JSON.stringify(adaptarAtividade({ atividades: [comExtra] }))
    expect(texto).not.toContain('5511988887777')
    expect(texto).not.toContain('pessoa@exemplo.com.br')
  })
})

describe('o extrato', () => {
  const linha = { id: 'rec-7', data: '2026-10-04T09:00:00-03:00', cliente: 'Ana Prado', id_cliente: 'RN_ana', origem: 'involuntario', tipo: 'recuperacao', descricao: 'recuperado na 1ª tentativa', valor_base: 200, fee: 30, liquido: 170, estornado: false, simulado: false }

  it('a fee do backend é a taxa da tela (é a única rota em que ela vem)', () => {
    expect(adaptarExtrato({ mes: '2026-10', linhas: [linha] })).toEqual([
      { id: 'rec-7', data: '2026-10-04T09:00:00-03:00', cliente: 'Ana Prado', origem: 'involuntario', tipo: 'recuperacao', descricao: 'Recuperado na 1ª tentativa', valor_base: 200, taxa: 30, taxa_fora_do_piloto: null, liquido: 170, estornado: false, simulado: false },
    ])
  })

  it('em linha de piloto, a taxa é zero e a que seria cobrada vem ao lado', () => {
    const [dePiloto] = adaptarExtrato({ mes: '2026-10', linhas: [{ ...linha, fee: 0, liquido: 200, fee_fora_do_piloto: 30 }] })
    expect([dePiloto.taxa, dePiloto.liquido, dePiloto.taxa_fora_do_piloto]).toEqual([0, 200, 30])
    const [estorno] = adaptarExtrato({ mes: '2026-10', linhas: [{ ...linha, tipo: 'estorno', valor_base: -200, fee: 0, liquido: -200, fee_fora_do_piloto: -30, estornado: true }] })
    expect(estorno.taxa_fora_do_piloto).toBe(-30)
    // Fora do piloto o backend manda null: não é uma taxa de zero reais.
    expect(adaptarExtrato({ mes: '2026-10', linhas: [{ ...linha, fee_fora_do_piloto: null }] })[0].taxa_fora_do_piloto).toBeNull()
  })

  it('o estorno é uma linha negativa; sem nome, aparece o id que a empresa usa', () => {
    const [estorno] = adaptarExtrato({ mes: '2026-10', linhas: [{ ...linha, id: 'est-7-1', cliente: null, tipo: 'estorno', valor_base: -200, fee: -30, liquido: -170, estornado: true }] })
    expect([estorno.tipo, estorno.cliente, estorno.valor_base, estorno.taxa, estorno.liquido, estorno.estornado]).toEqual(['estorno', 'RN_ana', -200, -30, -170, true])
  })

  it('o cliente mantido é do voluntário', () => {
    const [mantida] = adaptarExtrato({ mes: '2026-10', linhas: [{ ...linha, id: 'ret-1', origem: 'voluntario', tipo: 'mantido' }] })
    expect([mantida.origem, mantida.tipo]).toEqual(['voluntario', 'mantido'])
  })
})

describe('a saúde do sistema', () => {
  const DEMO: SaudeSistema = { relogio: { ativo: true, ultima_passagem: null }, modelos: { carregados: 3, total: 3 }, redator: { disponivel: true }, base: { origem: 'api', atualizada_em: '2026-09-30T12:00:00Z' } }
  const RELOGIO: SaudeApi['relogio'] = { ligado: true, motivo_desligado: null, ultima_passagem_em: '2026-10-04T09:00:00-03:00', ultima_passagem_ok: true }

  it('com tudo informado, nada é de demonstração', () => {
    const s = adaptarSaude(
      { status: 'ok', relogio: RELOGIO, modelos: { carregados: 3, total: 4, ausentes: ['risco_voluntario'] }, redator: { disponivel: false }, base: { informada: true, atualizada_em: '2026-10-04T08:00:00-03:00', origem: 'anexo' } },
      DEMO,
    )
    expect(s).toEqual({
      relogio: { ativo: true, ultima_passagem: '2026-10-04T09:00:00-03:00' },
      modelos: { carregados: 3, total: 4 },
      redator: { disponivel: false },
      base: { origem: 'anexo', atualizada_em: '2026-10-04T08:00:00-03:00' },
    })
    expect('demonstracao' in s).toBe(false)
  })

  it('empresa sem base: a linha diz que não há base, e não mostra a de demonstração', () => {
    const s = adaptarSaude({ status: 'ok', relogio: RELOGIO, modelos: { carregados: 4, total: 4 }, redator: { disponivel: true }, base: { informada: true, atualizada_em: null, origem: null } }, DEMO)
    expect(s.base).toBeNull()
    expect('demonstracao' in s).toBe(false)
  })

  it('origem não registrada chega como null', () => {
    const s = adaptarSaude({ status: 'ok', relogio: RELOGIO, modelos: { carregados: 4, total: 4 }, redator: { disponivel: true }, base: { informada: true, atualizada_em: '2026-10-04T08:00:00-03:00', origem: null } }, DEMO)
    expect(s.base).toEqual({ origem: null, atualizada_em: '2026-10-04T08:00:00-03:00' })
  })

  it('backend anterior à Rodada 3: o que ele não informa continua marcado como demonstração', () => {
    const s = adaptarSaude({ status: 'ok', relogio: RELOGIO }, DEMO)
    expect(s.demonstracao).toEqual(['modelos', 'redator', 'base'])
    expect(s.modelos).toEqual(DEMO.modelos)
  })

  it('sem a base na resposta (o health foi sem token), só a base fica marcada', () => {
    const s = adaptarSaude({ status: 'ok', relogio: RELOGIO, modelos: { carregados: 4, total: 4 }, redator: { disponivel: true } }, DEMO)
    expect(s.demonstracao).toEqual(['base'])
  })
})

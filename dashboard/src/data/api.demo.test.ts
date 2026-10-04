/**
 * Sem `VITE_CRAI_API_URL` o dashboard é o de antes: tudo de demonstração, nenhuma chamada
 * de rede, nenhuma etiqueta nova. (O `vitest.config.ts` zera a variável para estes testes.)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CANAIS_DISPONIVEIS, MODO_REAL, ROTAS_REAIS, agoraDaTela, api, emDemonstracao } from './api'
import { AGORA, ciclos as ciclosDoMock, metricasMes as metricasDoMock } from './mock'

const fetchEspiao = vi.fn(async () => {
  throw new Error('o modo de demonstração não pode chamar a rede')
})

beforeEach(() => {
  fetchEspiao.mockClear()
  vi.stubGlobal('fetch', fetchEspiao)
})
afterEach(() => vi.unstubAllGlobals())

describe('sem a URL do backend', () => {
  it('o modo real está desligado e nada é marcado como demonstração', () => {
    expect(MODO_REAL).toBe(false)
    expect(emDemonstracao('resumoVisaoGeral')).toBe(false)
    expect(emDemonstracao('assistente', 'funil')).toBe(false)
    expect(agoraDaTela()).toBe(AGORA)
    expect(CANAIS_DISPONIVEIS).toEqual(['whatsapp', 'email', 'sms'])
  })

  it('as rotas marcadas como reais continuam devolvendo o mock, sem rede', async () => {
    const [lista, detalhe, config, metricas, serie, saude, empresa] = await Promise.all([
      api.ciclos(),
      api.ciclo(38),
      api.configuracao(),
      api.metricasMes(),
      api.serie(),
      api.saude(),
      api.empresa(),
    ])
    expect(lista).toHaveLength(ciclosDoMock.length)
    expect(lista.map((c) => c.id).sort()).toEqual(ciclosDoMock.map((c) => c.id).sort())
    expect(detalhe?.cliente).toBe('Clínica Horizonte')
    expect(detalhe?.sugestoes).toHaveLength(3)
    expect(config.janela_contato).toEqual({ inicio: 8, fim: 20 })
    expect(metricas).toEqual(metricasDoMock)
    expect(serie).toHaveLength(30)
    expect(saude.demonstracao).toBeUndefined()
    expect(empresa.nome).toBe('NimbusFlow Tecnologia')
    expect(fetchEspiao).not.toHaveBeenCalled()
  })

  it('os filtros da lista funcionam como antes', async () => {
    expect((await api.ciclos({ status: 'recuperado' })).every((c) => c.status === 'recuperado')).toBe(true)
    expect((await api.ciclos({ busca: 'horizonte' })).map((c) => c.id)).toEqual([38])
    expect((await api.ciclos({ incluirSimulados: false })).some((c) => c.simulado)).toBe(false)
  })

  it('o prazo do ciclo de demonstração dá as mesmas 6 h 40 min de antes', async () => {
    const detalhe = await api.ciclo(38)
    const minutos = (new Date(detalhe!.escolha_ate!).getTime() - agoraDaTela().getTime()) / 60_000
    expect(minutos).toBe(6 * 60 + 40)
  })

  it('regerar e escolher funcionam na demonstração, sem rede', async () => {
    const novas = await api.regerarSugestoes(38)
    expect(novas.map((s) => s.rodada)).toEqual([2, 2, 2])
    const r = await api.escolherMensagem(38, 2, 'facilitacao')
    expect(r).toEqual({ enviada: true, espera: null })
    const depois = await api.ciclo(38)
    expect(depois?.estado).toBe('mensagem_enviada')
    expect(depois?.sugestoes.filter((s) => s.escolhida).map((s) => [s.rodada, s.abordagem])).toEqual([[2, 'facilitacao']])
    expect(fetchEspiao).not.toHaveBeenCalled()
  })
})

describe('o mapa único', () => {
  it('lista exatamente as funções integradas nesta etapa, e todas existem em api', () => {
    expect(Object.keys(ROTAS_REAIS).sort()).toEqual(
      ['ciclo', 'ciclos', 'configuracao', 'escolherMensagem', 'metricasMes', 'regerarSugestoes', 'salvarConfiguracao', 'saude', 'serie'].sort(),
    )
    for (const nome of Object.keys(ROTAS_REAIS)) {
      expect(typeof (api as Record<string, unknown>)[nome]).toBe('function')
    }
  })

  it('nenhum tipo da tela tem fee', async () => {
    const detalhe = await api.ciclo(35)
    expect(detalhe && 'fee' in detalhe).toBe(false)
    expect(JSON.stringify(await api.ciclos())).not.toContain('"fee"')
  })
})

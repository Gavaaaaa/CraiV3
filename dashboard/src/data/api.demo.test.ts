/**
 * Sem `VITE_CRAI_API_URL` o dashboard é o de antes: tudo de demonstração, nenhuma chamada
 * de rede, nenhuma etiqueta nova. (O `vitest.config.ts` zera a variável para estes testes.)
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CANAIS_DISPONIVEIS, MODO_REAL, MOSTRAR_DEMONSTRACAO, ROTAS_REAIS, agoraDaTela, api, emDemonstracao, etiquetaDeDemonstracao, nomePadraoDaChave } from './api'
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
    expect(MOSTRAR_DEMONSTRACAO).toBe(false)
    expect(etiquetaDeDemonstracao('resumoVisaoGeral', 'assistente')).toBe(false)
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

describe('chaves de API na demonstração', () => {
  it('lista, gera e revoga sem rede, e a chave de demonstração é visivelmente de mentira', async () => {
    const antes = await api.chaves()
    expect([antes.pode_gerar, antes.pode_revogar, antes.plano_permite_gerar]).toEqual([true, true, true])
    expect(antes.limite_ativas).toBe(5)
    expect(antes.ativas).toBe(antes.chaves.filter((c) => !c.revogada_em).length)
    for (const c of antes.chaves) {
      expect(c.nome.length).toBeGreaterThan(0)
      expect(c.inicio).toMatch(/^crai_live_.{4}$/)
      expect(c.final).toHaveLength(4)
      expect('ambiente' in c).toBe(false)
    }

    const { chave, inteira } = await api.criarChave('  Sistema de teste  ')
    expect(inteira).toMatch(/^crai_live_DEMO[A-Za-z0-9]{39}$/)
    expect(chave.nome).toBe('Sistema de teste')
    expect([chave.inicio, chave.final]).toEqual([inteira.slice(0, 14), inteira.slice(-4)])
    // A lista nunca traz a chave inteira.
    const depois = await api.chaves()
    expect(depois.ativas).toBe(antes.ativas + 1)
    expect(JSON.stringify(depois)).not.toContain(inteira)

    const revogada = await api.revogarChave(chave.id)
    expect(revogada.revogada_em).not.toBeNull()
    // Revogar de novo mantém a data original.
    expect((await api.revogarChave(chave.id)).revogada_em).toBe(revogada.revogada_em)
    expect((await api.chaves()).ativas).toBe(antes.ativas)
    expect(fetchEspiao).not.toHaveBeenCalled()
  })

  it('sem nome, a chave nasce com o nome padrão: "Chave de API" mais a data', async () => {
    expect(nomePadraoDaChave(new Date(2026, 9, 4))).toBe('Chave de API 04/10/2026')
    expect(nomePadraoDaChave(new Date(2026, 0, 9))).toBe('Chave de API 09/01/2026')
    const { chave } = await api.criarChave()
    expect(chave.nome).toBe(nomePadraoDaChave())
    expect(chave.nome).toMatch(/^Chave de API \d{2}\/\d{2}\/\d{4}$/)
    await api.revogarChave(chave.id)
    expect(fetchEspiao).not.toHaveBeenCalled()
  })

  it('nome vazio é recusado, e a sexta chave ativa dá conflito', async () => {
    await expect(api.criarChave('   ')).rejects.toMatchObject({ status: 422, motivo: 'nome_invalido' })
    const ativas = (await api.chaves()).ativas
    const criadas: string[] = []
    for (let i = ativas; i < 5; i++) criadas.push((await api.criarChave(`Chave ${i}`)).chave.id)
    await expect(api.criarChave('A sexta')).rejects.toMatchObject({ status: 409, motivo: 'limite_de_chaves' })
    for (const id of criadas) await api.revogarChave(id)
    await expect(api.revogarChave('nao-existe')).rejects.toMatchObject({ status: 404 })
  })

  it('a integração da configuração não traz mais a lista de chaves', async () => {
    const integracao = await api.integracao()
    expect(Object.keys(integracao)).toEqual(['webhook'])
  })
})

describe('o mapa único', () => {
  it('lista exatamente as funções integradas nesta etapa, e todas existem em api', () => {
    expect(Object.keys(ROTAS_REAIS).sort()).toEqual(
      [
        'chaves',
        'ciclo',
        'ciclos',
        'configuracao',
        'criarChave',
        'escolherMensagem',
        'metricasMes',
        'regerarSugestoes',
        'revogarChave',
        'salvarConfiguracao',
        'saude',
        'serie',
        // Rodada 3, Fase 1: a página do voluntário
        'baseClientes',
        'clientesRecentes',
        'comparacaoReguaModelo',
        'importarBase',
        'resumoVoluntario',
        'serieVoluntario',
        // Rodada 3, Fase 2: a visão geral
        'atividade',
        'extrato',
        'funil',
        'oQueFunciona',
        'resumoVisaoGeral',
        'serieDupla',
        // Rodada 3, Fase 3: a simulação do gateway
        'simulacao',
        'simulacaoAvancar',
        'simulacaoAvancarAteProximaAcao',
        'simulacaoEscolherMensagem',
        'simulacaoLimpar',
        'simularCobranca',
        'simularRetencao',
        // Rodada 3, Fase 4: o assistente
        'assistente',
        // Rodada 3, Fase 6: direitos do titular e descadastro
        'anonimizarTitular',
        'explicacaoDecisao',
        'exportarTitular',
        'naoContatar',
        'textoParaPolitica',
        'voltarAContatar',
        // Rodada 4, Fase 2: a busca do topo e o extrato em arquivo
        'buscar',
        'extratoCsv',
      ].sort(),
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

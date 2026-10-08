/**
 * O modo de mensagem na demonstração (sem `VITE_CRAI_API_URL`): a escolha e a troca para o modo
 * automático mudam o detalhe, a lista, o sino e o número do mês juntos, como no backend. Antes,
 * o sino e a aba continuavam pedindo uma escolha que já tinha sido feita, ou que o modo
 * automático não permite.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

type Api = typeof import('./api')['api']

/** Uma demonstração nova a cada teste: o estado da demonstração vive no módulo. */
async function demonstracaoNova(): Promise<Api> {
  vi.resetModules()
  return (await import('./api')).api
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('o modo de demonstração não pode chamar a rede')
    }),
  )
})

describe('a escolha na demonstração', () => {
  it('escolher uma mensagem tira a cobrança do sino, da aba Mensagens e do número do mês', async () => {
    const api = await demonstracaoNova()
    expect(await api.pendenciasDeEscolha()).toBe(1)
    expect((await api.metricasMes()).aguardando_escolha).toBe(1)
    expect((await api.ciclos({ aguardandoEscolha: true })).map((c) => c.id)).toEqual([38])

    await api.escolherMensagem(38, 1, 'facilitacao')

    expect(await api.pendenciasDeEscolha()).toBe(0)
    expect((await api.metricasMes()).aguardando_escolha).toBe(0)
    expect(await api.ciclos({ aguardandoEscolha: true })).toEqual([])
    const linha = (await api.ciclos()).find((c) => c.id === 38)
    expect(linha?.estado).toBe('mensagem_enviada')
    expect(linha?.proxima_acao_descricao).toBeNull()
    const detalhe = await api.ciclo(38)
    expect(detalhe?.escolha_por).toBe('owner')
    expect(detalhe?.escolha_ate).toBeNull()
  })
})

describe('o modo automático na demonstração', () => {
  it('trocar para o automático envia a recomendada de quem esperava, como o relógio do backend', async () => {
    const api = await demonstracaoNova()
    const config = await api.configuracao()
    expect(config.modo_mensagem_involuntario).toBe('escolha')

    await api.salvarConfiguracao({ ...config, modo_mensagem_involuntario: 'automatico' })

    expect((await api.configuracao()).modo_mensagem_involuntario).toBe('automatico')
    expect(await api.pendenciasDeEscolha()).toBe(0)
    const detalhe = await api.ciclo(38)
    expect(detalhe?.estado).toBe('mensagem_enviada')
    expect(detalhe?.escolha_por).toBe('automatico')
    expect(detalhe?.escolha_ate).toBeNull()
    // Sai a recomendada da rodada mais recente, e só ela.
    const escolhidas = detalhe?.sugestoes.filter((s) => s.escolhida) ?? []
    expect(escolhidas).toHaveLength(1)
    expect(escolhidas[0].recomendada).toBe(true)
    expect(detalhe?.linha_do_tempo.some((e) => e.detalhe === 'Escolhida pelo sistema: a recomendada, no modo automático.')).toBe(true)
  })

  it('voltar para "eu escolho" não desfaz o que já saiu', async () => {
    const api = await demonstracaoNova()
    const config = await api.configuracao()
    await api.salvarConfiguracao({ ...config, modo_mensagem_involuntario: 'automatico' })
    await api.salvarConfiguracao({ ...config, modo_mensagem_involuntario: 'escolha' })
    expect(await api.pendenciasDeEscolha()).toBe(0)
    expect((await api.ciclo(38))?.estado).toBe('mensagem_enviada')
  })
})

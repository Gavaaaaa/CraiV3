/**
 * A conversa com o backend, com um `fetch` de mentira: o token em memória, o 401 que pede
 * token novo UMA vez, e cada erro com a mensagem em português que a tela mostra.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ErroApi } from './erros'

const BASE = 'http://backend.teste'

interface Chamada {
  url: string
  metodo: string
  autorizacao: string | null
  corpo: string | null
}

type Resposta = { status: number; corpo?: unknown }

/** Um backend de mentira: `/dev/token` emite `tok-1`, `tok-2`...; as outras rotas respondem pela fila. */
function backendFalso(fila: Resposta[], opcoes: { tokenStatus?: number } = {}) {
  const chamadas: Chamada[] = []
  let emitidos = 0
  const fetchFalso = vi.fn(async (url: string, init: RequestInit = {}) => {
    const headers = (init.headers ?? {}) as Record<string, string>
    chamadas.push({ url, metodo: init.method ?? 'GET', autorizacao: headers.Authorization ?? null, corpo: (init.body as string) ?? null })
    if (url === `${BASE}/dev/token`) {
      const status = opcoes.tokenStatus ?? 200
      emitidos += 1
      return new Response(JSON.stringify(status === 200 ? { token: `tok-${emitidos}` } : { detail: 'Not Found' }), { status })
    }
    const r = fila.shift() ?? { status: 200, corpo: {} }
    return new Response(JSON.stringify(r.corpo ?? {}), { status: r.status })
  })
  vi.stubGlobal('fetch', fetchFalso)
  return chamadas
}

async function carregarHttp() {
  vi.resetModules()
  vi.stubEnv('VITE_CRAI_API_URL', `${BASE}/`)
  return import('./http')
}

async function erroDe(promessa: Promise<unknown>): Promise<ErroApi> {
  try {
    await promessa
  } catch (e) {
    return e as ErroApi
  }
  throw new Error('a chamada deveria ter falhado')
}

beforeEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

describe('modo real', () => {
  it('sem VITE_CRAI_API_URL o modo real fica desligado', async () => {
    vi.resetModules()
    vi.stubEnv('VITE_CRAI_API_URL', ' ')
    const http = await import('./http')
    expect(http.MODO_REAL).toBe(false)
    expect(http.API_URL).toHaveLength(0)
  })

  it('com a URL, liga, e a barra final não duplica', async () => {
    const http = await carregarHttp()
    expect(http.MODO_REAL).toBe(true)
    expect(http.API_URL).toBe(BASE)
  })
})

describe('token de desenvolvimento', () => {
  it('pede o token como owner na primeira chamada e o reusa nas seguintes', async () => {
    const chamadas = backendFalso([{ status: 200, corpo: { ciclos: [] } }, { status: 200, corpo: { ok: 1 } }])
    const http = await carregarHttp()
    await http.chamar('GET', '/ciclos')
    await http.chamar('GET', '/configuracao')
    expect(chamadas.map((c) => `${c.metodo} ${c.url}`)).toEqual([
      `POST ${BASE}/dev/token`,
      `GET ${BASE}/ciclos`,
      `GET ${BASE}/configuracao`,
    ])
    expect(JSON.parse(chamadas[0].corpo ?? '{}')).toEqual({ papel: 'owner' })
    expect(chamadas[1].autorizacao).toBe('Bearer tok-1')
    expect(chamadas[2].autorizacao).toBe('Bearer tok-1')
  })

  it('duas chamadas ao mesmo tempo pedem um token só', async () => {
    const chamadas = backendFalso([])
    const http = await carregarHttp()
    await Promise.all([http.chamar('GET', '/ciclos'), http.chamar('GET', '/configuracao')])
    expect(chamadas.filter((c) => c.url.endsWith('/dev/token'))).toHaveLength(1)
  })

  it('trocar o papel esquece o token e pede outro com o papel novo', async () => {
    const chamadas = backendFalso([])
    const http = await carregarHttp()
    await http.chamar('GET', '/ciclos')
    http.definirPapelDev('membro')
    expect(http.papelDev()).toBe('membro')
    await http.chamar('GET', '/ciclos')
    const pedidos = chamadas.filter((c) => c.url.endsWith('/dev/token'))
    expect(pedidos.map((c) => JSON.parse(c.corpo ?? '{}').papel)).toEqual(['owner', 'membro'])
    expect(chamadas[chamadas.length - 1].autorizacao).toBe('Bearer tok-2')
  })

  it('o plano essencial vai no pedido do token; o premium, que é o padrão, não', async () => {
    const chamadas = backendFalso([])
    const http = await carregarHttp()
    expect(http.planoDev()).toBe('premium')
    await http.chamar('GET', '/integracao/chaves')
    http.definirPlanoDev('essencial')
    expect(http.planoDev()).toBe('essencial')
    await http.chamar('GET', '/integracao/chaves')
    const pedidos = chamadas.filter((c) => c.url.endsWith('/dev/token'))
    expect(pedidos.map((c) => JSON.parse(c.corpo ?? '{}'))).toEqual([{ papel: 'owner' }, { papel: 'owner', plano: 'essencial' }])
    expect(chamadas[chamadas.length - 1].autorizacao).toBe('Bearer tok-2')
  })

  it('a empresa dos testes ao vivo vai no pedido do token; a da demonstração, que é o padrão, não', async () => {
    const chamadas = backendFalso([])
    const http = await carregarHttp()
    expect(http.empresaDev()).toBe('demo_dashboard')
    await http.chamar('GET', '/ciclos')
    http.definirEmpresaDev('demo_testes')
    expect(http.empresaDev()).toBe('demo_testes')
    await http.chamar('GET', '/ciclos')
    http.definirPlanoDev('essencial')
    await http.chamar('GET', '/ciclos')
    const pedidos = chamadas.filter((c) => c.url.endsWith('/dev/token'))
    expect(pedidos.map((c) => JSON.parse(c.corpo ?? '{}'))).toEqual([
      { papel: 'owner' },
      { papel: 'owner', empresa: 'demo_testes' },
      { papel: 'owner', plano: 'essencial', empresa: 'demo_testes' },
    ])
    // Trocar a empresa esquece o token: o da outra empresa nunca é reaproveitado.
    expect(chamadas[chamadas.length - 1].autorizacao).toBe('Bearer tok-3')
  })

  it('DELETE vai com o token e sem corpo', async () => {
    const chamadas = backendFalso([{ status: 200, corpo: { chave: {}, ja_estava_revogada: false } }])
    const http = await carregarHttp()
    await http.chamar('DELETE', '/integracao/chaves/chv_1')
    const ultima = chamadas[chamadas.length - 1]
    expect([ultima.metodo, ultima.url, ultima.autorizacao, ultima.corpo]).toEqual(['DELETE', `${BASE}/integracao/chaves/chv_1`, 'Bearer tok-1', null])
  })

  it('rota pública não pede nem manda token', async () => {
    const chamadas = backendFalso([{ status: 200, corpo: { status: 'ok' } }])
    const http = await carregarHttp()
    await http.chamar('GET', '/health', undefined, { semToken: true })
    expect(chamadas).toHaveLength(1)
    expect(chamadas[0].autorizacao).toBeNull()
  })

  it('o token não vai para o armazenamento do navegador', async () => {
    const gravados: string[] = []
    const armazem = { setItem: (k: string) => gravados.push(k), getItem: () => null, removeItem: () => undefined }
    vi.stubGlobal('localStorage', armazem)
    vi.stubGlobal('sessionStorage', armazem)
    backendFalso([])
    const http = await carregarHttp()
    await http.chamar('GET', '/ciclos')
    expect(gravados).toEqual([])
  })
})

describe('erros', () => {
  it('401 pede um token novo e tenta de novo UMA vez', async () => {
    const chamadas = backendFalso([{ status: 401, corpo: { detail: { motivo: 'token_expirado' } } }, { status: 200, corpo: { ciclos: [] } }])
    const http = await carregarHttp()
    expect(await http.chamar('GET', '/ciclos')).toEqual({ ciclos: [] })
    expect(chamadas.map((c) => c.url.slice(BASE.length))).toEqual(['/dev/token', '/ciclos', '/dev/token', '/ciclos'])
    expect(chamadas[3].autorizacao).toBe('Bearer tok-2')
  })

  it('401 duas vezes vira erro, sem terceira tentativa', async () => {
    const chamadas = backendFalso([{ status: 401 }, { status: 401, corpo: { detail: { motivo: 'assinatura_invalida' } } }])
    const http = await carregarHttp()
    const erro = await erroDe(http.chamar('GET', '/ciclos'))
    expect([erro.codigo, erro.status, erro.motivo]).toEqual(['nao_autorizado', 401, 'assinatura_invalida'])
    expect(chamadas.filter((c) => c.url.endsWith('/ciclos'))).toHaveLength(2)
  })

  it('403 diz que o papel não permite', async () => {
    backendFalso([{ status: 403, corpo: { detail: { motivo: 'papel_insuficiente', detalhe: 'esta operação exige o papel owner ou admin' } } }])
    const http = await carregarHttp()
    const erro = await erroDe(http.chamar('POST', '/ciclos/7/mensagens/escolher', { rodada: 1, abordagem: 'facilitacao' }))
    expect(erro.codigo).toBe('sem_permissao')
    expect(erro.message).toBe('Seu papel não permite esta ação.')
    expect(erro.motivo).toBe('papel_insuficiente')
  })

  it('403 por plano diz que gerar chave é do Premium, e o limite de chaves tem a sua frase', async () => {
    backendFalso([
      { status: 403, corpo: { detail: { motivo: 'plano_sem_api', detalhe: 'gerar chave de API faz parte do plano Premium' } } },
      { status: 409, corpo: { detail: { motivo: 'limite_de_chaves', detalhe: 'a empresa já tem 5 chaves ativas' } } },
    ])
    const http = await carregarHttp()
    const plano = await erroDe(http.chamar('POST', '/integracao/chaves', { nome: 'x' }))
    expect([plano.codigo, plano.status, plano.motivo, plano.message]).toEqual(['sem_permissao', 403, 'plano_sem_api', 'Gerar chave faz parte do plano Premium.'])
    const limite = await erroDe(http.chamar('POST', '/integracao/chaves', { nome: 'x' }))
    expect([limite.codigo, limite.status, limite.motivo]).toEqual(['conflito', 409, 'limite_de_chaves'])
    expect(limite.message).toBe('A empresa já tem o máximo de chaves ativas. Revogue uma para gerar outra.')
  })

  it('404 e 409 têm mensagem em português', async () => {
    backendFalso([
      { status: 404, corpo: { detail: { motivo: 'ciclo_nao_encontrado' } } },
      { status: 409, corpo: { detail: { motivo: 'ciclo_nao_aguarda_escolha' } } },
      { status: 409, corpo: { detail: { motivo: 'motivo_novo' } } },
    ])
    const http = await carregarHttp()
    const naoAchou = await erroDe(http.chamar('GET', '/ciclos/999'))
    expect([naoAchou.codigo, naoAchou.status]).toEqual(['nao_encontrado', 404])
    expect(naoAchou.message).toMatch(/^Não encontramos/)
    const conflito = await erroDe(http.chamar('POST', '/ciclos/7/mensagens/regerar'))
    expect(conflito.codigo).toBe('conflito')
    expect(conflito.message).toBe('Este ciclo não está mais esperando uma escolha. A mensagem já foi escolhida ou enviada.')
    const outro = await erroDe(http.chamar('POST', '/ciclos/7/mensagens/regerar'))
    expect(outro.message).toMatch(/^Esta ação não é mais possível/)
  })

  it('422 e 500 também viram ErroApi, mesmo com corpo que não é JSON', async () => {
    backendFalso([{ status: 422, corpo: { detail: [{ type: 'missing' }] } }])
    const http = await carregarHttp()
    expect((await erroDe(http.chamar('PUT', '/configuracao', { x: 1 }))).codigo).toBe('invalido')
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => (url.endsWith('/dev/token') ? new Response('{"token":"t"}') : new Response('<html>erro</html>', { status: 500 }))),
    )
    const erro = await erroDe(http.chamar('GET', '/ciclos'))
    expect([erro.codigo, erro.status]).toEqual(['servidor', 500])
  })

  it('backend fora do ar vira erro de rede, não exceção crua', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('fetch failed')
      }),
    )
    const http = await carregarHttp()
    const erro = await erroDe(http.chamar('GET', '/ciclos'))
    expect(erro.codigo).toBe('rede')
    expect(erro.message).toBe('Não foi possível falar com o servidor da CRAI.')
    expect((await erroDe(http.chamar('GET', '/health', undefined, { semToken: true }))).codigo).toBe('rede')
  })

  it('servidor sem a rota de desenvolvimento explica o que falta', async () => {
    backendFalso([], { tokenStatus: 404 })
    const http = await carregarHttp()
    const erro = await erroDe(http.chamar('GET', '/ciclos'))
    expect(erro.codigo).toBe('nao_autorizado')
    expect(erro.message).toMatch(/^O login de desenvolvimento não existe neste servidor/)
  })
})

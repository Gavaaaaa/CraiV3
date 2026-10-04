// @vitest-environment jsdom
/**
 * A aba "API" de pé, em modo de DEMONSTRAÇÃO (sem `VITE_CRAI_API_URL`), no desenho simples
 * (Rodada 2, ajustes): o endereço, as chaves ativas como campos de leitura, gerar com um
 * clique, as revogadas e o exemplo fechados por padrão.
 *
 * Nenhuma chave de verdade passa por aqui: a de demonstração começa por `crai_live_DEMO`.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { ErroApi, api, nomePadraoDaChave } from './data/api'
import type { ChaveApi, ChavesDaEmpresa } from './data/tipos'
import { AVISO_DA_CHAVE, MARCADOR_DA_CHAVE } from './routes/Api'
import { O_QUE_A_API_FAZ } from './routes/api/OQueAApiFaz'

const fetchEspiao = vi.fn(async () => {
  throw new Error('a demonstração não pode chamar a rede')
})

beforeAll(() => {
  // O jsdom não tem `matchMedia` (usado para "movimento reduzido").
  window.matchMedia = ((consulta: string) => ({
    matches: false,
    media: consulta,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Element.prototype.scrollIntoView = () => undefined
  Element.prototype.scrollTo = (() => undefined) as typeof Element.prototype.scrollTo
  vi.stubGlobal('fetch', fetchEspiao)
})

const consoles = ['log', 'info', 'warn', 'error', 'debug'] as const
let espioes: ReturnType<typeof vi.spyOn>[] = []

beforeEach(() => {
  fetchEspiao.mockClear()
  window.localStorage.clear()
  window.sessionStorage.clear()
  espioes = consoles.map((nome) => vi.spyOn(console, nome).mockImplementation(() => undefined))
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}

const ESPERA = { timeout: 5000 }
const etiquetas = () => screen.queryAllByText('Demonstração', { exact: true })

/** Tudo o que foi escrito no console durante o teste, num texto só. */
const escritoNoConsole = () => JSON.stringify(espioes.flatMap((e) => e.mock.calls))

const bloco = (nome: string) => screen.getByRole('region', { name: nome })
const campos = () => within(bloco('Chave de API')).queryAllByRole('textbox') as HTMLInputElement[]

const CHAVE = (id: string, extra: Partial<ChaveApi> = {}): ChaveApi => ({
  id,
  nome: 'Chave de API 04/10/2026',
  inicio: 'crai_live_N2Hp',
  final: 'm92g',
  criada_em: '2026-09-02T14:00:00Z',
  ultimo_uso: null,
  revogada_em: null,
  ...extra,
})
const LISTA = (chaves: ChaveApi[], extra: Partial<ChavesDaEmpresa> = {}): ChavesDaEmpresa => ({
  chaves,
  ativas: chaves.filter((c) => !c.revogada_em).length,
  limite_ativas: 5,
  pode_revogar: true,
  plano_permite_gerar: true,
  pode_gerar: true,
  ...extra,
})

describe('aba API em demonstração: a página, de cima para baixo', () => {
  it('título, "O que a API faz", o endereço com "Copiar", e nenhum parágrafo solto', async () => {
    const copiar = vi.fn(async () => undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: copiar }, configurable: true })
    abrir('/api')
    expect(await screen.findByRole('heading', { name: 'API' }, ESPERA)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'O que a API faz' })).toBeTruthy()

    const endereco = within(bloco('Endereço da API'))
    const campo = endereco.getByRole('textbox', { name: 'Endereço da API' }) as HTMLInputElement
    expect(campo.readOnly).toBe(true)
    expect(campo.value).toBe('https://api.exemplo-crai.com.br')
    fireEvent.click(endereco.getByRole('button', { name: 'Copiar' }))
    await waitFor(() => expect(copiar).toHaveBeenCalledWith('https://api.exemplo-crai.com.br'))

    // Os três blocos, nesta ordem, e nenhuma tabela.
    await waitFor(() => expect(campos().length).toBeGreaterThan(0), ESPERA)
    expect(screen.getAllByRole('region').map((r) => r.getAttribute('aria-label'))).toEqual(['Endereço da API', 'Chave de API', 'Exemplo de uso'])
    expect(document.querySelector('table')).toBeNull()
    // A explicação mora no painel: a página não tem parágrafo explicativo solto.
    const paragrafos = Array.from(document.querySelectorAll('main p')).map((p) => p.textContent ?? '')
    for (const texto of paragrafos) expect(texto.length, texto).toBeLessThan(80)
    expect(etiquetas()).toHaveLength(0)
    expect(fetchEspiao).not.toHaveBeenCalled()
  })

  it('a barra lateral tem o item "API" depois de "Assistente", sem bloqueio', async () => {
    abrir('/api')
    expect(await screen.findByRole('heading', { name: 'API' }, ESPERA)).toBeTruthy()
    const itens = within(screen.getByRole('navigation', { name: 'Seções do painel' }))
      .getAllByRole('link')
      .map((a) => [a.getAttribute('aria-label'), a.getAttribute('href')])
    const posicao = itens.findIndex(([rotulo]) => rotulo === 'Assistente')
    expect(itens[posicao + 1]).toEqual(['API', '/api'])
    await waitFor(() => expect(campos().length).toBeGreaterThan(0), ESPERA)
    expect(screen.getByRole('link', { name: 'API' }).getAttribute('aria-disabled')).toBeNull()
  })

  it('cada chave ativa é um campo de leitura com o começo e o final, e uma linha discreta embaixo', async () => {
    abrir('/api')
    await waitFor(() => expect(campos()).toHaveLength(2), ESPERA)
    expect(campos().map((c) => [c.value, c.readOnly])).toEqual([
      ['crai_live_7f3a••••••k2Qd', true],
      ['crai_live_c91d••••••x8Lm', true],
    ])
    const ativas = within(bloco('Chave de API')).getAllByTestId('chave-ativa')
    expect(ativas).toHaveLength(2)
    expect(within(ativas[0]).getByText(/^Criada em 02 de set · Último uso: há 2 h$/)).toBeTruthy()
    expect(within(ativas[0]).getByRole('button', { name: 'Revogar' })).toBeTruthy()
    // Com chave ativa, gerar vira um link menor; o botão grande some.
    expect(screen.getByRole('button', { name: 'Gerar outra chave' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Gerar chave' })).toBeNull()
    expect(screen.queryByText('Você ainda não tem uma chave')).toBeNull()
    // Nada de nome de chave nem de formulário.
    expect(screen.queryByText('Sistema de cobrança')).toBeNull()
    expect(within(bloco('Chave de API')).queryByPlaceholderText(/./)).toBeNull()
  })

  it('sem chave ativa: só o texto e o botão "Gerar chave"', async () => {
    vi.spyOn(api, 'chaves').mockResolvedValue(LISTA([]))
    abrir('/api')
    expect(await screen.findByText('Você ainda não tem uma chave', {}, ESPERA)).toBeTruthy()
    const chave = within(bloco('Chave de API'))
    expect(chave.queryAllByRole('textbox')).toHaveLength(0)
    expect(chave.getAllByRole('button').map((b) => b.textContent)).toEqual(['Gerar chave'])
  })

  it('as revogadas ficam atrás de "Ver chaves revogadas (N)", fechado por padrão', async () => {
    abrir('/api')
    const link = await screen.findByRole('button', { name: 'Ver chaves revogadas (1)' }, ESPERA)
    expect(link.getAttribute('aria-expanded')).toBe('false')
    expect(screen.queryByRole('list', { name: 'Chaves revogadas' })).toBeNull()
    expect(screen.queryByText('crai_live_2b8e••••••p0Ra')).toBeNull()

    fireEvent.click(link)
    const lista = screen.getByRole('list', { name: 'Chaves revogadas' })
    expect(within(lista).getByText('crai_live_2b8e••••••p0Ra')).toBeTruthy()
    expect(within(lista).getByText('Revogada em 02 de set')).toBeTruthy()
    expect(within(lista).queryByRole('button')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Esconder chaves revogadas (1)' }))
    expect(screen.queryByRole('list', { name: 'Chaves revogadas' })).toBeNull()
  })

  it('"Exemplo de uso" vem fechado e abre com um clique, com marcador no lugar da chave', async () => {
    abrir('/api')
    const botao = await screen.findByRole('button', { name: /^Exemplo de uso/ }, ESPERA)
    expect(botao.getAttribute('aria-expanded')).toBe('false')
    expect(document.querySelector('pre')).toBeNull()
    expect(screen.queryByText('Como usar')).toBeNull()

    fireEvent.click(botao)
    expect(botao.getAttribute('aria-expanded')).toBe('true')
    const exemplo = document.querySelector('pre')?.textContent ?? ''
    expect(exemplo).toContain(`Authorization: Bearer ${MARCADOR_DA_CHAVE}`)
    expect(exemplo).toContain('https://api.exemplo-crai.com.br/clientes')
    expect(exemplo).not.toMatch(/crai_live_/)
    expect(screen.getByRole('button', { name: 'Copiar exemplo' })).toBeTruthy()

    fireEvent.click(botao)
    expect(document.querySelector('pre')).toBeNull()
  })

  it('o botão "O que a API faz" abre o painel com o texto, e ele fecha com Esc, clique fora e botão', async () => {
    abrir('/api')
    const botao = await screen.findByRole('button', { name: 'O que a API faz' }, ESPERA)
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(botao)
    const painel = await screen.findByRole('dialog', { name: 'O que a API faz' })
    // O texto aprovado, frase por frase.
    expect(within(painel).getByText(O_QUE_A_API_FAZ.abertura)).toBeTruthy()
    for (const b of O_QUE_A_API_FAZ.blocos) {
      expect(within(painel).getByText(b.titulo)).toBeTruthy()
      for (const item of b.itens) expect(within(painel).getByText(item)).toBeTruthy()
    }
    expect(within(painel).getByText('Não dá acesso a este painel.')).toBeTruthy()
    expect(within(painel).getByText('Envie só os campos que a documentação pede. Nunca envie CPF, dados de cartão ou senhas.')).toBeTruthy()

    // Esc
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    // Clique fora (o fundo)
    fireEvent.click(botao)
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Fechar a explicação' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    // Botão de fechar
    fireEvent.click(botao)
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Fechar' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})

describe('aba API em demonstração: gerar e revogar', () => {
  it('gerar é um clique, sem pedir nome; a chave inteira aparece no próprio campo, com "Copiar" e o aviso', async () => {
    const copiar = vi.fn(async () => undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: copiar }, configurable: true })
    const criar = vi.spyOn(api, 'criarChave')
    abrir('/api')
    await waitFor(() => expect(campos()).toHaveLength(2), ESPERA)

    fireEvent.click(screen.getByRole('button', { name: 'Gerar outra chave' }))
    const campo = (await within(bloco('Chave de API')).findByRole('textbox', { name: 'Chave de API, inteira' }, ESPERA)) as HTMLInputElement
    const chave = campo.value
    expect(chave).toMatch(/^crai_live_DEMO[A-Za-z0-9]{39}$/)
    expect(campo.readOnly).toBe(true)
    // Um clique só: nenhum nome pedido, e o dashboard mandou o nome padrão.
    expect(criar).toHaveBeenCalledTimes(1)
    expect(criar.mock.calls[0]).toEqual([])
    const nova = (await api.chaves()).chaves[0]
    expect(nova.nome).toBe(nomePadraoDaChave())

    // O aviso e o "Copiar" ficam junto do campo da chave nova, e só dele.
    const linha = campo.closest('[data-testid="chave-ativa"]') as HTMLElement
    expect(within(linha).getByText(AVISO_DA_CHAVE)).toBeTruthy()
    expect(AVISO_DA_CHAVE).toBe('Copie agora. Por segurança, ela não aparece de novo.')
    fireEvent.click(within(linha).getByRole('button', { name: 'Copiar' }))
    await waitFor(() => expect(copiar).toHaveBeenCalledWith(chave))
    expect(screen.getAllByText(AVISO_DA_CHAVE)).toHaveLength(1)
    await waitFor(() => expect(campos()).toHaveLength(3), ESPERA)
    expect(campos().filter((c) => c.value === chave)).toHaveLength(1)
    expect(within(linha).getByText(/Último uso: nunca$/)).toBeTruthy()

    // Nunca foi para o armazenamento do navegador, para a URL nem para o console.
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
    expect(window.location.href).not.toContain('crai_live_')
    expect(escritoNoConsole()).not.toContain(chave.slice('crai_live_'.length))
    expect(fetchEspiao).not.toHaveBeenCalled()
  })

  it('ao sair da página e voltar, o campo mostra só o começo e o final', async () => {
    abrir('/api')
    await waitFor(() => expect(campos().length).toBeGreaterThanOrEqual(2), ESPERA)
    fireEvent.click(screen.getByRole('button', { name: 'Gerar outra chave' }))
    const campo = (await within(bloco('Chave de API')).findByRole('textbox', { name: 'Chave de API, inteira' }, ESPERA)) as HTMLInputElement
    const chave = campo.value
    const segredo = chave.slice('crai_live_'.length)

    // Sai para a configuração...
    fireEvent.click(screen.getByRole('link', { name: 'Configuração' }))
    expect(await screen.findByText('Quem escolhe a mensagem', {}, ESPERA)).toBeTruthy()
    expect(document.documentElement.outerHTML).not.toContain(segredo)
    // ...e volta.
    fireEvent.click(screen.getByRole('link', { name: 'API' }))
    await waitFor(() => expect(campos().length).toBeGreaterThanOrEqual(3), ESPERA)
    expect(campos().map((c) => c.value)).toContain(`${chave.slice(0, 14)}••••••${chave.slice(-4)}`)
    expect(campos().some((c) => c.value.includes(segredo))).toBe(false)
    expect(within(bloco('Chave de API')).queryByRole('textbox', { name: 'Chave de API, inteira' })).toBeNull()
    expect(screen.queryByText(AVISO_DA_CHAVE)).toBeNull()
    expect(document.documentElement.outerHTML).not.toContain(segredo)
  }, 20_000) // Abre duas páginas carregadas sob demanda: precisa de mais que os 5 s padrão.

  it('a primeira chave: o botão "Gerar chave" vira o link "Gerar outra chave"', async () => {
    let lista = LISTA([])
    vi.spyOn(api, 'chaves').mockImplementation(async () => lista)
    vi.spyOn(api, 'criarChave').mockImplementation(async () => {
      const chave = CHAVE('nova')
      lista = LISTA([chave])
      return { chave, inteira: 'crai_live_N2HpDEMODEMODEMODEMODEMODEMODEMODEMODEm92g' }
    })
    abrir('/api')
    fireEvent.click(await screen.findByRole('button', { name: 'Gerar chave' }, ESPERA))
    const campo = (await screen.findByRole('textbox', { name: 'Chave de API, inteira' }, ESPERA)) as HTMLInputElement
    expect(campo.value).toBe('crai_live_N2HpDEMODEMODEMODEMODEMODEMODEMODEMODEm92g')
    expect(screen.queryByText('Você ainda não tem uma chave')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Gerar chave' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Gerar outra chave' })).toBeTruthy()
  })

  it('revogar pede confirmação dentro da tela, nunca pelo confirm() do navegador', async () => {
    const confirmDoNavegador = vi.fn(() => true)
    vi.stubGlobal('confirm', confirmDoNavegador)
    const revogar = vi.spyOn(api, 'revogarChave')
    abrir('/api')
    await waitFor(() => expect(campos().length).toBeGreaterThanOrEqual(2), ESPERA)
    const antes = campos().length
    const revogadasAntes = (await api.chaves()).chaves.filter((c) => c.revogada_em).length
    const linha = within(bloco('Chave de API')).getAllByTestId('chave-ativa')[0]
    const valor = (within(linha).getByRole('textbox') as HTMLInputElement).value

    // Primeiro clique: só a pergunta. Cancelar não revoga.
    fireEvent.click(within(linha).getByRole('button', { name: 'Revogar' }))
    expect(within(linha).getByText('Revogar esta chave? O sistema que a usa para na hora.')).toBeTruthy()
    fireEvent.click(within(linha).getByRole('button', { name: 'Cancelar' }))
    expect(revogar).not.toHaveBeenCalled()
    expect(campos()).toHaveLength(antes)

    // Confirmar revoga: o campo some das ativas e vai para as revogadas.
    fireEvent.click(within(linha).getByRole('button', { name: 'Revogar' }))
    fireEvent.click(within(linha).getByRole('button', { name: 'Confirmar' }))
    await waitFor(() => expect(campos()).toHaveLength(antes - 1), ESPERA)
    expect(campos().map((c) => c.value)).not.toContain(valor)
    expect(await screen.findByRole('button', { name: `Ver chaves revogadas (${revogadasAntes + 1})` }, ESPERA)).toBeTruthy()
    expect(revogar).toHaveBeenCalledTimes(1)
    expect(confirmDoNavegador).not.toHaveBeenCalled()
  })
})

describe('aba API: os estados', () => {
  it('carregando, e depois erro com "Tentar de novo"', async () => {
    let falhar: (e: unknown) => void = () => undefined
    const chaves = vi.spyOn(api, 'chaves').mockImplementationOnce(() => new Promise((_, recusar) => (falhar = recusar)))
    abrir('/api')
    await waitFor(() => expect(chaves).toHaveBeenCalledTimes(1), ESPERA)
    expect(screen.getByLabelText('Carregando')).toBeTruthy()
    falhar(new ErroApi('rede', 'Não foi possível falar com o servidor da CRAI.'))
    expect(await screen.findByText('Não deu para carregar', {}, ESPERA)).toBeTruthy()
    // O endereço e o exemplo não dependem do servidor: continuam lá.
    expect(screen.getByRole('textbox', { name: 'Endereço da API' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Tentar de novo' }))
    await waitFor(() => expect(campos().length).toBeGreaterThan(0), ESPERA)
    expect(chaves).toHaveBeenCalledTimes(2)
  })

  it('plano essencial: a aba abre, mostra e revoga; gerar é do Premium', async () => {
    vi.spyOn(api, 'empresa').mockResolvedValue({ nome: 'NimbusFlow Tecnologia', plano: 'essencial', papel: 'owner' })
    let lista = LISTA([CHAVE('a'), CHAVE('b', { inicio: 'crai_live_Zz99', final: 'q1W2', revogada_em: '2026-09-10T10:00:00Z' })], {
      plano_permite_gerar: false,
      pode_gerar: false,
    })
    const chaves = vi.spyOn(api, 'chaves').mockImplementation(async () => lista)
    const revogar = vi.spyOn(api, 'revogarChave').mockImplementation(async (id) => {
      const revogada = { ...CHAVE(id), revogada_em: '2026-10-04T12:00:00Z' }
      lista = { ...lista, chaves: lista.chaves.map((c) => (c.id === id ? revogada : c)), ativas: 0 }
      return revogada
    })
    abrir('/api')
    await waitFor(() => expect(campos()).toHaveLength(1), ESPERA)
    expect(chaves).toHaveBeenCalled()
    expect(campos()[0].value).toBe('crai_live_N2Hp••••••m92g')
    expect(screen.getByText('Gerar chave faz parte do plano Premium.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Gerar/ })).toBeNull()
    expect(screen.getByRole('button', { name: 'Ver chaves revogadas (1)' })).toBeTruthy()
    // O item da barra lateral não fica bloqueado; o do voluntário, sim.
    expect(screen.getByRole('link', { name: 'API' }).getAttribute('aria-disabled')).toBeNull()
    expect(screen.getByRole('link', { name: 'Churn voluntário' }).getAttribute('aria-disabled')).toBe('true')

    // E revoga.
    fireEvent.click(screen.getByRole('button', { name: 'Revogar' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar' }))
    await waitFor(() => expect(campos()).toHaveLength(0), ESPERA)
    expect(revogar).toHaveBeenCalledWith('a')
    expect(screen.getByText('Você ainda não tem uma chave')).toBeTruthy()
    expect(screen.getByText('Gerar chave faz parte do plano Premium.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Ver chaves revogadas (2)' })).toBeTruthy()
  })

  it('membro: vê as chaves, com o aviso, sem gerar nem revogar', async () => {
    vi.spyOn(api, 'empresa').mockResolvedValue({ nome: 'NimbusFlow Tecnologia', plano: 'premium', papel: 'membro' })
    vi.spyOn(api, 'chaves').mockResolvedValue(LISTA([CHAVE('a')], { pode_revogar: false, pode_gerar: false }))
    abrir('/api')
    await waitFor(() => expect(campos()).toHaveLength(1), ESPERA)
    expect(screen.getByText('Seu papel não permite gerar nem revogar chaves.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Gerar/ })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Revogar' })).toBeNull()
    // A explicação continua disponível para todo mundo.
    expect(screen.getByRole('button', { name: 'O que a API faz' })).toBeTruthy()
  })

  it('no limite de chaves ativas, gerar some e fica o motivo', async () => {
    vi.spyOn(api, 'chaves').mockResolvedValue(LISTA(['a', 'b', 'c', 'd', 'e'].map((id) => CHAVE(id))))
    abrir('/api')
    await waitFor(() => expect(campos()).toHaveLength(5), ESPERA)
    expect(screen.getByText('Limite de 5 chaves ativas. Revogue uma para gerar outra.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /^Gerar/ })).toBeNull()
  })

  it('erro ao gerar aparece na tela, em português, e nenhuma chave é mostrada', async () => {
    vi.spyOn(api, 'criarChave').mockRejectedValue(new ErroApi('sem_permissao', 'Gerar chave faz parte do plano Premium.', 403, 'plano_sem_api'))
    abrir('/api')
    fireEvent.click(await screen.findByRole('button', { name: 'Gerar outra chave' }, ESPERA))
    expect((await screen.findByRole('alert', {}, ESPERA)).textContent).toBe('Gerar chave faz parte do plano Premium.')
    expect(screen.queryByRole('textbox', { name: 'Chave de API, inteira' })).toBeNull()
  })
})

describe('configuração › integração', () => {
  it('a lista de chaves saiu de lá; ficou o atalho "Abrir a aba API"', async () => {
    abrir('/configuracao?secao=integracao')
    const atalho = await screen.findByRole('link', { name: 'Abrir a aba API' }, ESPERA)
    expect(atalho.getAttribute('href')).toBe('/api')
    expect(screen.queryByText('Criar chave live')).toBeNull()
    expect(screen.queryByText('Criar chave test')).toBeNull()
    expect(screen.queryByText(/crai_test_/)).toBeNull()
    // O que sobrou da seção continua lá.
    expect(screen.getByText('Webhook')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Testar integração/ })).toBeTruthy()

    fireEvent.click(atalho)
    expect(await screen.findByRole('button', { name: /^Exemplo de uso/ }, ESPERA)).toBeTruthy()
    expect(window.location.pathname).toBe('/api')
  })
})

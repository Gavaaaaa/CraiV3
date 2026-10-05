// @vitest-environment jsdom
/**
 * Rodada 4, Fase 2: as pendências de tela, em MODO REAL, com o backend de mentira.
 * O sino e a busca do topo, o cartão "Próxima ação do sistema", o extrato em CSV vindo do
 * backend, a marca "Não contatar" na lista de clientes, o campo do intervalo entre ofertas, o
 * texto da política sem os asteriscos, e a barra "Mostrar" só onde ela muda alguma coisa.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
  vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', '')
})

import App from './App'
import { MODO_REAL } from './data/api'
import { DETALHE_DO_CICLO, LINHA_DO_CICLO } from './testes/cicloDeExemplo'
import { corposEnviados, ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'

const ESPERA = { timeout: 5000 }
const MES = new Date().toISOString().slice(0, 7)
const lista = (ciclos: unknown[]) => ({ ciclos, proximo_cursor: null, tem_mais: false })

const mes = (extra: Record<string, unknown> = {}) => ({
  mes: MES,
  inicio: `${MES}-01`,
  fim: `${MES}-28`,
  valor_liquido_recuperado: 0,
  recuperados: 0,
  encerrados_sem_recuperacao: 0,
  taxa_recuperacao: null,
  ciclos_abertos_no_mes: { em_analise: 0, em_processo: 0, recuperado: 0, encerrado_sem_recuperacao: 0 },
  aguardando_escolha: 0,
  proxima_acao: null,
  ...extra,
})

const cliente = (id: string, nome: string, extra: Record<string, unknown> = {}) => ({
  id,
  nome,
  mrr: 480,
  faixa: 'grave',
  motivo: '41 dias sem login, 0 funcionalidades usadas, MRR R$ 480,00',
  decidido_por: 'regua',
  posicao_no_ranking: 1,
  abordagem: null,
  atualizado_em: new Date(Date.now() - 3 * 3_600_000).toISOString(),
  origem: 'upload',
  nao_contatar: false,
  simulado: false,
  ...extra,
})

const BUSCA = {
  q: 'pra',
  clientes: [
    { id: 'cli-ana', nome: 'Ana Prado', mrr: 500, cancelado: false, nao_contatar: false },
    { id: 'cli-bia', nome: 'Bia Prado Lemos', mrr: 300, cancelado: true, nao_contatar: true },
  ],
  ciclos: [{ id: 2, id_recorrencia: 'RN_ana', cliente_nome: 'Ana Prado', status: 'em_processo', estado: 'aguardando_escolha', valor_cobranca: 4900, causa_legivel: 'Autorização revogada', atualizado_em: new Date().toISOString() }],
  limite: 8,
}

beforeEach(() => prepararJsdom())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}

const endereco = () => window.location.pathname + window.location.search
const sino = () => screen.getByRole('button', { name: /esperando a sua escolha de mensagem$/ })

describe('o sino do topo', () => {
  it('o modo real está ligado neste arquivo', () => {
    expect(MODO_REAL).toBe(true)
  })

  it('sem nada pendente, não tem número nem ponto de aviso', async () => {
    const chamadas = ligarBackendFalso()
    abrir('/api')
    await waitFor(() => expect(chamadas).toContain('GET /metrics/involuntario/mes'), ESPERA)
    expect(sino().getAttribute('aria-label')).toBe('Nenhuma cobrança esperando a sua escolha de mensagem')
    expect(document.querySelector('[data-pendentes]')).toBeNull()
    expect(sino().querySelectorAll('span').length).toBe(0)
  })

  it('mostra o número do backend e leva à aba Mensagens, com quem espera a escolha', async () => {
    // Tema e botões (1.2): o sino passou a levar à aba Mensagens do Involuntário (antes, ao
    // filtro "Aguardando escolha" da lista), e rola a página para o topo.
    window.scrollTo = vi.fn() as unknown as typeof window.scrollTo
    const chamadas = ligarBackendFalso({ 'GET /metrics/involuntario/mes': mes({ aguardando_escolha: 3 }), 'GET /ciclos': lista([LINHA_DO_CICLO]), 'GET /ciclos/2': DETALHE_DO_CICLO })
    abrir('/api')
    await waitFor(() => expect(document.querySelector('[data-pendentes]')?.textContent).toBe('3'), ESPERA)
    expect(sino().getAttribute('aria-label')).toBe('3 cobranças esperando a sua escolha de mensagem')

    fireEvent.click(sino())
    await waitFor(() => expect(endereco()).toBe('/involuntario?aba=mensagens'), ESPERA)
    expect(window.scrollTo).toHaveBeenCalledWith(expect.objectContaining({ top: 0 }))
    const abaMensagens = await screen.findByRole('tab', { name: /^Mensagens/ }, ESPERA)
    expect(abaMensagens.getAttribute('aria-selected')).toBe('true')
    await waitFor(() => expect(chamadas.some((c) => c.startsWith('GET /ciclos?') && c.includes('aguardando_escolha=true'))).toBe(true), ESPERA)
    expect(await screen.findByText('Clínica Horizonte (fictícia)', {}, ESPERA)).toBeTruthy()
  })

  it('uma só pendente fala no singular', async () => {
    ligarBackendFalso({ 'GET /metrics/involuntario/mes': mes({ aguardando_escolha: 1 }) })
    abrir('/assistente')
    await waitFor(() => expect(document.querySelector('[data-pendentes]')?.textContent).toBe('1'), ESPERA)
    expect(sino().getAttribute('aria-label')).toBe('1 cobrança esperando a sua escolha de mensagem')
  })
})

describe('involuntário: o filtro do sino e a próxima ação', () => {
  it('sem ninguém esperando, a lista diz isso, e "Todos" tira o filtro do endereço', async () => {
    const chamadas = ligarBackendFalso()
    abrir('/involuntario?filtro=aguardando_escolha')
    expect(await screen.findByText('Nenhuma cobrança esperando a sua escolha', {}, ESPERA)).toBeTruthy()
    fireEvent.click(screen.getByRole('tab', { name: /^Todos/ }))
    await waitFor(() => expect(endereco()).toBe('/involuntario'), ESPERA)
    await waitFor(() => expect(chamadas.filter((c) => c.startsWith('GET /ciclos?')).pop()).not.toContain('aguardando_escolha'), ESPERA)
  })

  it('o cartão mostra a próxima ação que o backend informou, e abre o ciclo', async () => {
    const quando = new Date(Date.now() + 26 * 3_600_000).toISOString()
    const chamadas = ligarBackendFalso({
      'GET /metrics/involuntario/mes': mes({ proxima_acao: { quando, tipo: 'tentativa', descricao: 'Tentativa 2 de cobrança', ciclo_id: 2 } }),
      'GET /ciclos': lista([LINHA_DO_CICLO]),
      'GET /ciclos/2': DETALHE_DO_CICLO,
    })
    abrir('/involuntario')
    expect(await screen.findByText('Tentativa 2 de cobrança', {}, ESPERA)).toBeTruthy()
    expect(screen.queryByText('Ainda não informada pelo servidor')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Ver o ciclo' }))
    const painel = await screen.findByRole('dialog', {}, ESPERA)
    expect(await within(painel).findByText('Por que o sistema agiu assim', {}, ESPERA)).toBeTruthy()
    expect(chamadas).toContain('GET /ciclos/2')
  })

  it('sem nada agendado, o cartão diz "Nada agendado", e não que o servidor não informou', async () => {
    ligarBackendFalso()
    abrir('/involuntario')
    expect(await screen.findByText('Nada agendado', {}, ESPERA)).toBeTruthy()
    expect(screen.getByText('Nenhuma tentativa de cobrança nem mensagem pendente agora')).toBeTruthy()
    expect(screen.queryByText('Ainda não informada pelo servidor')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Ver o ciclo' })).toBeNull()
  })
})

describe('a busca do topo', () => {
  const campo = () => screen.getByRole('searchbox', { name: 'Buscar cliente pelo nome ou pelo identificador' }) as HTMLInputElement

  it('com uma letra não consulta; com duas, consulta uma vez e mostra clientes e cobranças', async () => {
    const chamadas = ligarBackendFalso({ 'GET /busca': BUSCA })
    abrir('/api')
    fireEvent.change(campo(), { target: { value: 'p' } })
    await new Promise((r) => setTimeout(r, 400))
    expect(chamadas.filter((c) => c.startsWith('GET /busca'))).toEqual([])
    expect(screen.queryByLabelText('Resultados da busca')).toBeNull()

    fireEvent.change(campo(), { target: { value: 'pr' } })
    fireEvent.change(campo(), { target: { value: 'pra' } })
    const resultados = await screen.findByLabelText('Resultados da busca', {}, ESPERA)
    expect(await within(resultados).findByText('Bia Prado Lemos', {}, ESPERA)).toBeTruthy()
    expect(chamadas.filter((c) => c.startsWith('GET /busca'))).toEqual(['GET /busca?q=pra'])
    expect(within(resultados).getByText('Cobranças')).toBeTruthy()
    expect(within(resultados).getByText('Clientes')).toBeTruthy()
    expect(within(resultados).getByText(/RN_ana · R\$\s4\.900,00/)).toBeTruthy()
    expect(within(resultados).getByText('Cancelou')).toBeTruthy()
    expect(within(resultados).getByText('Não contatar')).toBeTruthy()
  })

  it('uma cobrança leva ao painel do ciclo', async () => {
    const chamadas = ligarBackendFalso({ 'GET /busca': BUSCA, 'GET /ciclos': lista([LINHA_DO_CICLO]), 'GET /ciclos/2': DETALHE_DO_CICLO })
    abrir('/api')
    fireEvent.change(campo(), { target: { value: 'pra' } })
    const resultados = await screen.findByLabelText('Resultados da busca', {}, ESPERA)
    fireEvent.click(await within(resultados).findByText(/RN_ana ·/, {}, ESPERA))
    await waitFor(() => expect(endereco()).toBe('/involuntario?ciclo=2'), ESPERA)
    expect(await screen.findByRole('dialog', {}, ESPERA)).toBeTruthy()
    expect(chamadas).toContain('GET /ciclos/2')
    expect(campo().value).toBe('')
    // Fechar o painel tira o ciclo do endereço.
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(endereco()).toBe('/involuntario'), ESPERA)
  })

  it('um cliente leva à lista de clientes, com ele no topo', async () => {
    const recentes = { clientes: [cliente('cli-zeca', 'Zeca Lima'), cliente('cli-ana', 'Ana Prado')], total_na_base: 2 }
    const chamadas = ligarBackendFalso({ 'GET /busca': BUSCA, 'GET /clientes/recentes': recentes })
    abrir('/api')
    fireEvent.change(campo(), { target: { value: 'pra' } })
    const resultados = await screen.findByLabelText('Resultados da busca', {}, ESPERA)
    const clientes = await within(resultados).findByRole('region', { name: 'Clientes' }, ESPERA)
    fireEvent.click(within(clientes).getByText('Ana Prado'))
    await waitFor(() => expect(endereco()).toBe('/voluntario?aba=clientes&cliente=cli-ana'), ESPERA)
    expect(await screen.findByText('O cliente buscado está no topo da lista, antes dos mais recentes.', {}, ESPERA)).toBeTruthy()
    expect(chamadas.some((c) => c.startsWith('GET /clientes/recentes?') && c.includes('cliente=cli-ana'))).toBe(true)
    const linhas = [...document.querySelectorAll('tbody tr')]
    expect(linhas[0].getAttribute('data-buscado')).toBe('true')
    expect(within(linhas[0] as HTMLElement).getByText('Buscado')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Limpar a busca' }))
    await waitFor(() => expect(endereco()).toBe('/voluntario?aba=clientes'), ESPERA)
  })

  it('nada encontrado e erro do backend aparecem em frase', async () => {
    ligarBackendFalso()
    abrir('/api')
    fireEvent.change(campo(), { target: { value: 'zzz' } })
    expect(await screen.findByText(/Nada encontrado para “zzz”\./, {}, ESPERA)).toBeTruthy()
    cleanup()
    ligarBackendFalso({ 'GET /busca': { __status: 500, corpo: {} } })
    abrir('/api')
    fireEvent.change(campo(), { target: { value: 'zzz' } })
    expect((await screen.findByRole('alert', {}, ESPERA)).textContent).toMatch(/servidor da CRAI respondeu com erro/)
  })
})

describe('voluntário: quem pediu para não ser contatado', () => {
  it('a lista marca o cliente, e só ele', async () => {
    ligarBackendFalso({ 'GET /clientes/recentes': { clientes: [cliente('c-1', 'Ana Prado', { nao_contatar: true }), cliente('c-2', 'Bia Lemos')], total_na_base: 2 } })
    abrir('/voluntario?aba=clientes')
    const ana = (await screen.findByText('Ana Prado', {}, ESPERA)).closest('tr') as HTMLElement
    const bia = screen.getByText('Bia Lemos').closest('tr') as HTMLElement
    expect(within(ana).getByText('Não contatar')).toBeTruthy()
    expect(within(bia).queryByText('Não contatar')).toBeNull()
  })

  it('cliente buscado que não é da empresa: a tela diz, sem inventar linha', async () => {
    ligarBackendFalso()
    abrir('/voluntario?aba=clientes&cliente=nao-existe')
    expect(await screen.findByText('O cliente buscado não está na base da sua empresa.', {}, ESPERA)).toBeTruthy()
    expect(document.querySelector('[data-buscado]')).toBeNull()
  })
})

describe('configuração: o intervalo entre ofertas', () => {
  const campo = () => screen.getByLabelText(/Dias entre uma oferta e a próxima/) as HTMLInputElement
  const salvar = () => screen.getByRole('button', { name: 'Salvar' }) as HTMLButtonElement

  it('mostra o valor do backend e grava só o que mudou', async () => {
    ligarBackendFalso()
    abrir('/configuracao')
    await screen.findByText('Intervalo entre ofertas de retenção', {}, ESPERA)
    expect(campo().value).toBe('30')
    expect(salvar().disabled).toBe(true)
    fireEvent.change(campo(), { target: { value: '45' } })
    expect(salvar().disabled).toBe(false)
    ligarBackendFalso({ 'PUT /configuracao': { configuracao: { modo_mensagem_involuntario: 'escolha', prazo_escolha_horas: 8, janela_contato_inicio: '08:00', janela_contato_fim: '20:00', canais_permitidos: ['whatsapp', 'email'], intervalo_minimo_ofertas_dias: 45, retencao_mensagens_dias: 90, retencao_ciclos_meses: 24, retencao_base_meses_apos_contrato: 6, retencao_trilha_anos: 5 }, pode_editar: true } })
    fireEvent.click(salvar())
    await waitFor(() => expect(corposEnviados()).toEqual([{ chave: 'PUT /configuracao', corpo: { intervalo_minimo_ofertas_dias: 45 } }]), ESPERA)
    await waitFor(() => expect(campo().value).toBe('45'), ESPERA)
  })

  it.each(['0', '366', '12,5', 'abc', ''])('valor fora da regra (%s) não deixa salvar e avisa', async (valor) => {
    ligarBackendFalso()
    abrir('/configuracao')
    await screen.findByText('Intervalo entre ofertas de retenção', {}, ESPERA)
    fireEvent.change(campo(), { target: { value: valor } })
    expect(salvar().disabled).toBe(true)
    expect(screen.getByRole('alert').textContent).toBe('Digite um número inteiro de 1 a 365.')
    expect(campo().getAttribute('aria-invalid')).toBe('true')
  })
})

describe('extrato: o arquivo vem do backend', () => {
  const EXTRATO = {
    mes: MES,
    linhas: [{ id: 'rec-1', data: new Date().toISOString(), cliente: 'Ana Prado', id_cliente: 'RN_ana', origem: 'involuntario', tipo: 'recuperacao', descricao: 'Recuperado na 1ª tentativa', valor_base: 200, fee: 30, liquido: 170, estornado: false, simulado: false }],
    totais: { valor_base: 200, fee: 30, liquido: 170 },
    meses_de_mrr: 1,
  }
  const DO_BACKEND = '﻿Data;Cliente\r\n05/10/2026;Ana Prado\r\n'

  function vigiarDownload() {
    const blobs: Blob[] = []
    URL.createObjectURL = vi.fn((b: Blob) => (blobs.push(b), 'blob:teste')) as unknown as typeof URL.createObjectURL
    URL.revokeObjectURL = vi.fn()
    return blobs
  }

  it('"Exportar CSV" baixa o arquivo que o backend montou', async () => {
    const blobs = vigiarDownload()
    const chamadas = ligarBackendFalso({ 'GET /extrato': EXTRATO, 'GET /extrato/csv': { __texto: DO_BACKEND } })
    abrir('/?aba=extrato')
    fireEvent.click(await screen.findByRole('button', { name: /Exportar CSV/ }, ESPERA))
    await waitFor(() => expect(blobs).toHaveLength(1), ESPERA)
    expect(chamadas.filter((c) => c.startsWith('GET /extrato/csv'))).toEqual(['GET /extrato/csv'])
    // O navegador tira a marca de UTF-8 ao ler a resposta; o arquivo baixado tem de voltar com ela.
    expect((await blobs[0].text()).replace(/^﻿/, '')).toBe(DO_BACKEND.replace('﻿', ''))
    expect(new Uint8Array(await blobs[0].arrayBuffer()).slice(0, 3)).toEqual(new Uint8Array([0xef, 0xbb, 0xbf]))
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('com "Mostrar: Simulação", o arquivo pede os simulados também', async () => {
    vigiarDownload()
    const chamadas = ligarBackendFalso({ 'GET /extrato': EXTRATO, 'GET /extrato/csv': { __texto: DO_BACKEND } })
    abrir('/?aba=extrato')
    await screen.findByRole('button', { name: /Exportar CSV/ }, ESPERA)
    fireEvent.click(screen.getByRole('radio', { name: 'Simulação' }))
    await waitFor(() => expect(chamadas.some((c) => c === 'GET /extrato?incluir_simulados=true')).toBe(true), ESPERA)
    fireEvent.click(await screen.findByRole('button', { name: /Exportar CSV/ }, ESPERA))
    await waitFor(() => expect(chamadas).toContain('GET /extrato/csv?incluir_simulados=true'), ESPERA)
  })

  it('se o backend recusar, a tela diz por quê e nada é baixado', async () => {
    const blobs = vigiarDownload()
    ligarBackendFalso({ 'GET /extrato': EXTRATO, 'GET /extrato/csv': { __status: 403, corpo: { detail: { motivo: 'papel_insuficiente' } } } })
    abrir('/?aba=extrato')
    fireEvent.click(await screen.findByRole('button', { name: /Exportar CSV/ }, ESPERA))
    expect((await screen.findByRole('alert', {}, ESPERA)).textContent).toBe('Seu papel não permite esta ação.')
    expect(blobs).toHaveLength(0)
  })
})

describe('texto para a política de privacidade', () => {
  it('aparece formatado e é copiado sem os asteriscos', async () => {
    const copiados: string[] = []
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: (t: string) => (copiados.push(t), Promise.resolve()) } })
    ligarBackendFalso()
    abrir('/configuracao?secao=dados')
    const texto = await screen.findByLabelText('Texto para a política de privacidade', {}, ESPERA)
    await waitFor(() => expect(texto.querySelector('strong')?.textContent).toBe('Mensagens.'), ESPERA)
    expect(texto.textContent).not.toContain('*')
    expect(texto.querySelectorAll('p')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: /Copiar texto/ }))
    await waitFor(() => expect(copiados).toHaveLength(1), ESPERA)
    expect(copiados[0]).not.toContain('*')
    expect(copiados[0]).toContain('Mensagens. Você recebe no máximo uma oferta de retenção a cada 30 dias.')
    expect(copiados[0].split('\n\n')).toHaveLength(2)
  })
})

describe('a barra "Mostrar" só aparece onde o clique muda alguma coisa', () => {
  it.each([
    ['/', 'GET /metrics/visao-geral'],
    ['/involuntario', 'GET /ciclos'],
    ['/voluntario', 'GET /clientes/recentes'],
  ])('em %s, "Simulação" pede os dados de novo, com os simulados', async (pagina, rota) => {
    const chamadas = ligarBackendFalso()
    abrir(pagina)
    await waitFor(() => expect(chamadas.some((c) => c.startsWith(rota))).toBe(true), ESPERA)
    expect(chamadas.some((c) => c.includes('incluir_simulados'))).toBe(false)
    fireEvent.click(await screen.findByRole('radio', { name: 'Simulação' }, ESPERA))
    await waitFor(() => expect(chamadas.some((c) => c.startsWith(rota) && c.includes('incluir_simulados=true'))).toBe(true), ESPERA)
    // E "Dados reais" volta a pedir sem eles.
    const antes = chamadas.length
    fireEvent.click(screen.getByRole('radio', { name: 'Dados reais' }))
    await waitFor(() => expect(chamadas.slice(antes).some((c) => c.startsWith(rota) && !c.includes('incluir_simulados'))).toBe(true), ESPERA)
  })

  it.each(['/simulacao', '/assistente', '/api', '/configuracao'])('em %s a barra não aparece', async (pagina) => {
    const chamadas = ligarBackendFalso()
    abrir(pagina)
    await waitFor(() => expect(chamadas).toContain('GET /metrics/involuntario/mes'), ESPERA)
    expect(screen.queryByRole('radiogroup', { name: 'Modo dos dados' })).toBeNull()
    expect(screen.queryByRole('radio', { name: 'Dados reais' })).toBeNull()
    expect(screen.queryByRole('radio', { name: 'Simulação' })).toBeNull()
  })
})

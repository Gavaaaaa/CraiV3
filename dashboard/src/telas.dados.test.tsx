// @vitest-environment jsdom
/**
 * Rodada 3, Fase 6: Configuração › Dados e privacidade em MODO REAL. Exportar, anonimizar,
 * "Não contatar", a explicação e o texto para a política falam com o backend (aqui, o de
 * mentira de `testes/modoRealFalso.ts`).
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
  vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', '1')
})

import App from './App'
import { MODO_REAL, emDemonstracao } from './data/api'
import { adaptarAnonimizacao, adaptarExplicacao, adaptarExportacao, adaptarNaoContatar, motivoDoCanal } from './data/adaptadores'
import { corposEnviados, ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'

const ESPERA = { timeout: 4000 }

const EXPORTACAO = {
  customer_id_externo: 'c-1',
  exportado_em: '2026-10-04T12:00:00-03:00',
  cadastro: { customer_id_externo: 'c-1', nome: 'Cliente de teste', mrr: 200 },
  contatos_guardados: { email: true, telefone: false },
  nao_contatar: null,
  cobrancas: [{ aberto_em: '2026-10-01T09:00:00-03:00', mensagens: [], tentativas: [] }],
  retencao: [{ evento: 'Cancellation Page Viewed' }],
  valores_mantidos: [],
  decisoes_automatizadas: [{ id: 1 }, { id: 2 }, { id: 3 }],
}
const MARCADO = { customer_id_externo: 'c-1', nao_contatar: { marcado_em: '2026-10-04T12:00:00-03:00', origem: 'empresa' }, ja_estava_marcado: false }
const ANONIMIZADO = { contatos_apagados: ['nome', 'email', 'telefone'], mensagens_apagadas: 3, ciclos_com_mensagem_apagada: 1, nao_contatar: { marcado_em: 'x', origem: 'anonimizacao' } }
const EXPLICACAO = {
  sujeito_id: 'RN_1',
  decisoes: [{ decidido_em: '2026-10-03T12:00:00+00:00', explicacao: 'em 03/10/2026, no fluxo de recuperação de pagamento, o sistema decidiu como tratar a cobrança que falhou: nova tentativa automática de cobrança.' }],
}
const NAO_ENCONTRADO = { __status: 404, corpo: { detail: { motivo: 'titular_nao_encontrado' } } }

beforeEach(() => prepararJsdom())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function abrir(respostas: Record<string, unknown> = {}) {
  const chamadas = ligarBackendFalso(respostas)
  window.history.pushState({}, '', '/configuracao?secao=dados')
  render(<App />)
  await screen.findByText('Por quanto tempo a CRAI guarda', {}, ESPERA)
  return chamadas
}

function identificar(id = 'c-1') {
  fireEvent.change(screen.getByPlaceholderText('Ex.: cliente_10482'), { target: { value: id } })
}

const corposDe = (chave: string) => corposEnviados().filter((c) => c.chave === chave).map((c) => c.corpo)

describe('dados e privacidade em modo real', () => {
  it('as seis funções da seção estão no mapa de rotas reais, e a seção não tem faixa de demonstração', async () => {
    expect(MODO_REAL).toBe(true)
    expect(emDemonstracao('exportarTitular', 'anonimizarTitular', 'explicacaoDecisao', 'naoContatar', 'voltarAContatar', 'textoParaPolitica')).toBe(false)
    await abrir()
    expect(screen.queryByText('Este bloco ainda usa dados fictícios')).toBeNull()
  })

  it('o texto para a política é o do backend, com o botão de copiar', async () => {
    const chamadas = await abrir()
    // Rodada 4: o texto deixou de ser um campo com o Markdown cru e aparece formatado.
    const campo = await screen.findByLabelText('Texto para a política de privacidade', {}, ESPERA)
    await waitFor(() => expect(campo.textContent).toContain('Você recebe no máximo uma oferta de retenção a cada 30 dias.'), ESPERA)
    expect(campo.textContent).toContain('responda SAIR')
    expect(campo.textContent).not.toContain('24 meses')
    expect(campo.textContent).not.toContain('**')
    expect([...campo.querySelectorAll('strong')].map((s) => s.textContent)).toEqual(['Mensagens.'])
    expect(chamadas).toContain('GET /titular/texto-para-politica')
    expect((screen.getByRole('button', { name: /Copiar texto/ }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('exportar: manda só o identificador, e oferece o arquivo que o backend devolveu', async () => {
    await abrir({ 'POST /titular/exportar': EXPORTACAO })
    identificar()
    fireEvent.click(screen.getByRole('button', { name: /Exportar dados/ }))
    const link = (await screen.findByRole('link', { name: 'Baixar o arquivo' }, ESPERA)) as HTMLAnchorElement
    expect(corposDe('POST /titular/exportar')).toEqual([{ customer_id_externo: 'c-1' }])
    expect(link.getAttribute('download')).toBe('crai-titular-c-1.json')
    const conteudo = JSON.parse(decodeURIComponent(link.getAttribute('href')!.split(',')[1]))
    expect(conteudo).toEqual(EXPORTACAO)
    expect(screen.getByText(/crai-titular-c-1\.json/)).toBeTruthy()
    expect(screen.getByText(/\(5 registros\)/)).toBeTruthy()
    expect(screen.getByText(/Ele diz quais contatos estão guardados, sem repetir o e-mail e o telefone\./)).toBeTruthy()
    expect(screen.queryByText(/Demonstração: na versão final/)).toBeNull()
  })

  it('titular que não existe: a frase do erro, e nenhum arquivo', async () => {
    await abrir({ 'POST /titular/exportar': NAO_ENCONTRADO })
    identificar('nao-existe')
    fireEvent.click(screen.getByRole('button', { name: /Exportar dados/ }))
    const aviso = await screen.findByRole('alert', {}, ESPERA)
    expect(aviso.textContent).toBe('Não há dado deste identificador na sua empresa. Confira o identificador que a sua base usa.')
    expect(screen.queryByRole('link', { name: 'Baixar o arquivo' })).toBeNull()
  })

  it('"Não contatar" e "Voltar a contatar" chamam as rotas do cliente', async () => {
    const chamadas = await abrir({
      'POST /clientes/c-1/nao-contatar': MARCADO,
      'DELETE /clientes/c-1/nao-contatar': { customer_id_externo: 'c-1', nao_contatar: null, estava_marcado: true },
    })
    identificar()
    fireEvent.click(screen.getByRole('button', { name: 'Não contatar' }))
    expect(await screen.findByText('Pronto. Nenhuma mensagem sai mais para este cliente.', {}, ESPERA)).toBeTruthy()
    expect(chamadas).toContain('POST /clientes/c-1/nao-contatar')
    fireEvent.click(screen.getByRole('button', { name: 'Voltar a contatar' }))
    expect(await screen.findByText('Pronto. Este cliente volta a receber mensagens.', {}, ESPERA)).toBeTruthy()
    expect(chamadas).toContain('DELETE /clientes/c-1/nao-contatar')
    expect(screen.getByText(/As tentativas de cobrança continuam\./)).toBeTruthy()
  })

  it('cliente anonimizado não volta a ser contatado: a frase do 409', async () => {
    await abrir({ 'DELETE /clientes/c-1/nao-contatar': { __status: 409, corpo: { detail: { motivo: 'marca_da_anonimizacao' } } } })
    identificar()
    fireEvent.click(screen.getByRole('button', { name: 'Voltar a contatar' }))
    expect((await screen.findByRole('alert', {}, ESPERA)).textContent).toContain('Este cliente foi anonimizado')
  })

  it('anonimizar pede confirmação, manda só o identificador e diz o que foi apagado', async () => {
    await abrir({ 'POST /titular/anonimizar': ANONIMIZADO })
    identificar()
    fireEvent.click(screen.getByRole('button', { name: 'Anonimizar' }))
    expect(corposDe('POST /titular/anonimizar')).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Confirmar: não dá para desfazer' }))
    expect(await screen.findByText('Pronto. 3 contatos apagados e 3 mensagens apagadas. Os totais do painel não mudam, e nenhuma mensagem sai mais para este cliente.', {}, ESPERA)).toBeTruthy()
    expect(corposDe('POST /titular/anonimizar')).toEqual([{ customer_id_externo: 'c-1' }])
  })

  it('a explicação é a frase que o backend registrou, sem pontos inventados', async () => {
    const chamadas = await abrir({ 'GET /titular/explicacao/RN_1': EXPLICACAO })
    fireEvent.change(screen.getByPlaceholderText('Ex.: RN_7f3a9c21'), { target: { value: 'RN_1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ver' }))
    expect(await screen.findByText(/^Em 03\/10\/2026, no fluxo de recuperação de pagamento/, {}, ESPERA)).toBeTruthy()
    expect(chamadas).toContain('GET /titular/explicacao/RN_1?limite=1')
    expect(screen.queryByText(/ pts$/)).toBeNull()
    expect(screen.getByText(/Revise o texto antes de repassar ao cliente\./)).toBeTruthy()
  })

  it('sem decisão registrada, a tela diz isso (tentando também como cliente)', async () => {
    const sem = { __status: 404, corpo: { detail: { motivo: 'sujeito_sem_decisao' } } }
    const chamadas = await abrir({ 'GET /titular/explicacao/c-9': sem, 'GET /titular/explicacao/user%3Ac-9': sem })
    fireEvent.change(screen.getByPlaceholderText('Ex.: RN_7f3a9c21'), { target: { value: 'c-9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Ver' }))
    expect(await screen.findByText('Não há decisão registrada para este identificador na sua empresa.', {}, ESPERA)).toBeTruthy()
    expect(chamadas.filter((c) => c.startsWith('GET /titular/explicacao/'))).toEqual(['GET /titular/explicacao/c-9?limite=1', 'GET /titular/explicacao/user%3Ac-9?limite=1'])
  })

  it('os prazos dizem o que é executado e o que ainda não é', async () => {
    await abrir()
    const bloco = screen.getByText('Por quanto tempo a CRAI guarda').closest('section, div')!.parentElement as HTMLElement
    expect(within(bloco).getAllByText('Prazo definido; ainda não executado')).toHaveLength(1)
    expect(within(bloco).getByText('Depois do desfecho; saem os identificadores, ficam os valores')).toBeTruthy()
    expect(within(bloco).getByText('Sem dado de contato; apagada depois do prazo')).toBeTruthy()
  })
})

describe('adaptadores dos direitos do titular', () => {
  it('exportação: o nome do arquivo é seguro, e os registros são contados', () => {
    const e = adaptarExportacao({ ...EXPORTACAO, customer_id_externo: '../c 1/x' })
    expect(e.arquivo).toBe('crai-titular-.._c_1_x.json')
    expect(e.linhas).toBe(5)
    expect(JSON.parse(e.conteudo!).contatos_guardados).toEqual({ email: true, telefone: false })
  })

  it('o motivo do canal de quem pediu para não ser contatado tem frase própria', () => {
    expect(motivoDoCanal('cliente_pediu_para_nao_ser_contatado')).toBe('O cliente pediu para não ser contatado')
  })

  it('anonimização, não contatar e explicação', () => {
    expect(adaptarAnonimizacao(ANONIMIZADO)).toEqual({ ok: true, ciclos_anonimizados: 1, mensagens_apagadas: 3, contatos_apagados: 3 })
    expect(adaptarNaoContatar(MARCADO)).toEqual({ marcado: true, ja_estava: false, desde: '2026-10-04T12:00:00-03:00' })
    expect(adaptarNaoContatar({ customer_id_externo: 'c-1', nao_contatar: null, estava_marcado: false })).toEqual({ marcado: false, ja_estava: false, desde: null })
    const x = adaptarExplicacao(EXPLICACAO)!
    expect(x.decisao.startsWith('Em 03/10/2026')).toBe(true)
    expect(x.fatores).toEqual([])
    expect(adaptarExplicacao({ sujeito_id: 'x', decisoes: [] })).toBeNull()
  })
})

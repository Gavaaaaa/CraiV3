// @vitest-environment jsdom
/**
 * Tema e botões, Parte 1: os controles do topo que não respondiam, em MODO REAL com o backend
 * de mentira. As abas do Involuntário são abas de verdade (a aba Mensagens lista quem espera a
 * escolha), o sino leva à aba Mensagens, o selo do papel está em português e é um controle só
 * no login de desenvolvimento, e a catraca: nenhum botão ou aba sem ação em nenhuma página.
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
import { PAGINAS, ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'

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
/** O backend com uma cobrança esperando a escolha (a Clínica Horizonte, ciclo 2). */
const COM_PENDENCIA = { 'GET /metrics/involuntario/mes': mes({ aguardando_escolha: 1 }), 'GET /ciclos': lista([LINHA_DO_CICLO]), 'GET /ciclos/2': DETALHE_DO_CICLO }
/** A mesma cobrança, de um cliente SEM canal: as mensagens foram geradas, mas não há como entregar. */
const DETALHE_SEM_CANAL = {
  ...DETALHE_DO_CICLO,
  mensagens: DETALHE_DO_CICLO.mensagens.map((m) => ({ ...m, canal: 'sem_canal', motivo_canal: 'sem_contato', nao_entregavel: true })),
}
const SEM_CANAL = { ...COM_PENDENCIA, 'GET /ciclos/2': DETALHE_SEM_CANAL }

beforeEach(() => {
  prepararJsdom()
  window.scrollTo = vi.fn() as unknown as typeof window.scrollTo
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}
const endereco = () => window.location.pathname + window.location.search
const aba = (nome: RegExp) => screen.getByRole('tab', { name: nome })
const sino = () => screen.getByRole('button', { name: /esperando a sua escolha de mensagem$/ })

describe('as abas do Involuntário', () => {
  it('o modo real está ligado neste arquivo', () => {
    expect(MODO_REAL).toBe(true)
  })

  it('são abas de verdade: tablist, aria-selected, a aba no endereço e o teclado', async () => {
    ligarBackendFalso(COM_PENDENCIA)
    abrir('/involuntario')
    const abas = await screen.findByRole('tablist', { name: 'Abas do involuntário' }, ESPERA)
    expect(within(abas).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Clientes em recuperação', 'Mensagens1'])
    expect(aba(/^Clientes em recuperação/).getAttribute('aria-selected')).toBe('true')
    expect(aba(/^Mensagens/).getAttribute('aria-selected')).toBe('false')
    // O número da aba é o do backend (o mesmo do sino).
    await waitFor(() => expect(document.querySelector('[data-pendentes]')?.textContent).toBe('1'), ESPERA)

    fireEvent.click(aba(/^Mensagens/))
    await waitFor(() => expect(endereco()).toBe('/involuntario?aba=mensagens'), ESPERA)
    expect(aba(/^Mensagens/).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tabpanel').id).toBe('inv-painel-mensagens')
    expect(screen.queryByText('Resumo do mês')).toBeNull()

    // Setas do teclado trocam de aba, e o botão voltar do navegador também.
    fireEvent.keyDown(abas, { key: 'ArrowLeft' })
    await waitFor(() => expect(endereco()).toBe('/involuntario'), ESPERA)
    expect(aba(/^Clientes em recuperação/).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByRole('tabpanel').id).toBe('inv-painel-clientes')
  })

  it('o endereço antigo do sino (?filtro=aguardando_escolha) continua abrindo a lista filtrada', async () => {
    const chamadas = ligarBackendFalso(COM_PENDENCIA)
    abrir('/involuntario?filtro=aguardando_escolha')
    expect((await screen.findByRole('tab', { name: /^Clientes em recuperação/ }, ESPERA)).getAttribute('aria-selected')).toBe('true')
    expect((await screen.findByRole('tab', { name: /Aguardando escolha/ }, ESPERA)).getAttribute('aria-selected')).toBe('true')
    await waitFor(() => expect(chamadas.some((c) => c.startsWith('GET /ciclos?') && c.includes('aguardando_escolha=true'))).toBe(true), ESPERA)
  })
})

describe('a aba Mensagens', () => {
  it('lista quem espera a escolha: cliente, valor, canal, prazo e a mensagem recomendada', async () => {
    const chamadas = ligarBackendFalso(COM_PENDENCIA)
    abrir('/involuntario?aba=mensagens')
    const painel = await screen.findByRole('list', { name: 'Cobranças esperando a escolha da mensagem' }, ESPERA)
    // A lista vem de `GET /ciclos?aguardando_escolha=true`; o canal, o prazo e a recomendada, do detalhe.
    expect(chamadas.some((c) => c.startsWith('GET /ciclos?') && c.includes('aguardando_escolha=true'))).toBe(true)
    expect(chamadas).toContain('GET /ciclos/2')
    const linha = within(painel).getAllByRole('listitem')[0]
    expect(within(linha).getByText('Clínica Horizonte (fictícia)')).toBeTruthy()
    expect(within(linha).getByText(/R\$\s4\.900,00/)).toBeTruthy()
    expect(within(linha).getByText('Canal: WhatsApp')).toBeTruthy()
    expect(within(linha).getByText(/^Escolha até .* · Envio automático/)).toBeTruthy()
    expect(within(linha).getByText('Recomendada: Facilitação.')).toBeTruthy()
    expect(within(linha).getByText(/Para facilitar: reative a recorrência/)).toBeTruthy()
    expect(within(linha).getByRole('button', { name: 'Escolher a mensagem' })).toBeTruthy()
  })

  it('"Escolher a mensagem" abre o painel do ciclo já na escolha', async () => {
    ligarBackendFalso(COM_PENDENCIA)
    abrir('/involuntario?aba=mensagens')
    fireEvent.click(await screen.findByRole('button', { name: 'Escolher a mensagem' }, ESPERA))
    const dialogo = await screen.findByRole('dialog', {}, ESPERA)
    const escolha = await within(dialogo).findByText('Mensagens sugeridas', {}, ESPERA)
    const botoes = within(dialogo).getAllByRole('button', { name: /Enviar esta/ })
    expect(botoes).toHaveLength(3)
    // O foco vai para o primeiro "Enviar esta": a escolha fica à mão, sem rolar.
    await waitFor(() => expect(document.activeElement).toBe(botoes[0]), ESPERA)
    expect(escolha.closest('[data-secao="escolha"]')).toBeTruthy()
    // Fechar o painel devolve a aba Mensagens, como estava.
    fireEvent.keyDown(window, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull(), ESPERA)
    expect(endereco()).toBe('/involuntario?aba=mensagens')
  })

  it('cobrança de cliente sem canal: diz que nada será enviado e o que acontece, sem "Envio automático" nem "Escolher"', async () => {
    ligarBackendFalso(SEM_CANAL)
    abrir('/involuntario?aba=mensagens')
    const painel = await screen.findByRole('list', { name: 'Cobranças esperando a escolha da mensagem' }, ESPERA)
    const linha = within(painel).getAllByRole('listitem')[0]
    expect(within(linha).getByText('Canal: Sem canal disponível')).toBeTruthy()
    expect(within(linha).queryByText(/Envio automático/)).toBeNull()
    expect(within(linha).queryByText(/Escolha até/)).toBeNull()
    expect(within(linha).queryByText(/^Recomendada:/)).toBeNull()
    const aviso = within(linha).getByText(/Nada será enviado: o cliente não tem telefone nem e-mail na sua base\./)
    expect(aviso.closest('p')?.textContent).toContain('O sistema reconfere o contato na sua base a cada passagem')
    expect(aviso.closest('p')?.textContent).toContain('Sem contato em 30 dias, a cobrança é encerrada sem recuperação.')
    // Escolher não faz sentido: o botão abre a cobrança, de costume (sem ir para a escolha).
    expect(within(linha).queryByRole('button', { name: 'Escolher a mensagem' })).toBeNull()
    fireEvent.click(within(linha).getByRole('button', { name: 'Ver a cobrança' }))
    const dialogo = await screen.findByRole('dialog', {}, ESPERA)
    expect(await within(dialogo).findByText('Por que o sistema agiu assim', {}, ESPERA)).toBeTruthy()
    const focado = document.activeElement
    expect(focado?.tagName === 'BUTTON' && /Enviar esta/.test(focado.textContent ?? '')).toBe(false)
  })

  it('sem nada pendente, diz isso numa frase', async () => {
    ligarBackendFalso()
    abrir('/involuntario?aba=mensagens')
    expect(await screen.findByText('Nenhuma cobrança espera a sua escolha agora', {}, ESPERA)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Escolher a mensagem' })).toBeNull()
  })

  it('se o backend falhar, a aba mostra o erro e oferece tentar de novo', async () => {
    ligarBackendFalso({ 'GET /ciclos': { __status: 500, corpo: {} } })
    abrir('/involuntario?aba=mensagens')
    const alerta = await screen.findByRole('alert', {}, ESPERA)
    expect(alerta.textContent).toMatch(/Não deu para carregar/)
    expect(within(alerta).getByRole('button', { name: /Tentar de novo/ })).toBeTruthy()
  })
})

describe('o sino', () => {
  it('com pendência, leva à aba Mensagens e rola a página para o topo, mesmo já estando no Involuntário', async () => {
    ligarBackendFalso(COM_PENDENCIA)
    abrir('/involuntario')
    await waitFor(() => expect(document.querySelector('[data-pendentes]')?.textContent).toBe('1'), ESPERA)
    expect(screen.getByRole('tabpanel').id).toBe('inv-painel-clientes')
    fireEvent.click(sino())
    await waitFor(() => expect(endereco()).toBe('/involuntario?aba=mensagens'), ESPERA)
    expect(screen.getByRole('tabpanel').id).toBe('inv-painel-mensagens')
    expect(await screen.findByText('Mensagens esperando a sua escolha', {}, ESPERA)).toBeTruthy()
    expect(window.scrollTo).toHaveBeenCalledWith(expect.objectContaining({ top: 0 }))
  })

  it('sem pendência, continua levando ao Involuntário, na aba de sempre', async () => {
    ligarBackendFalso()
    abrir('/api')
    await waitFor(() => expect(sino().getAttribute('aria-label')).toBe('Nenhuma cobrança esperando a sua escolha de mensagem'), ESPERA)
    fireEvent.click(sino())
    await waitFor(() => expect(endereco()).toBe('/involuntario'), ESPERA)
    expect((await screen.findByRole('tab', { name: /^Clientes em recuperação/ }, ESPERA)).getAttribute('aria-selected')).toBe('true')
  })
})

describe('o selo do papel', () => {
  it('no login de desenvolvimento é um controle só, em português, e trocar o papel muda o que a pessoa pode fazer', async () => {
    ligarBackendFalso()
    abrir('/configuracao')
    await screen.findByText('Quem escolhe a mensagem', {}, ESPERA)
    expect(screen.queryByText('Owner')).toBeNull()
    expect(screen.queryByText('Papel')).toBeNull()
    const seletor = screen.getByLabelText('Papel do login de desenvolvimento') as HTMLSelectElement
    expect(seletor.tagName).toBe('SELECT')
    expect([...seletor.options].map((o) => o.textContent)).toEqual(['Dono', 'Administrador', 'Membro'])
    expect(seletor.value).toBe('owner')
    expect(screen.getAllByLabelText(/Papel/).length).toBe(1)
    fireEvent.change(seletor, { target: { value: 'membro' } })
    await waitFor(() => expect((screen.getByLabelText('Papel do login de desenvolvimento') as HTMLSelectElement).value).toBe('membro'), ESPERA)
    expect(await screen.findByText('Membro (só leitura)', {}, ESPERA)).toBeTruthy()
  })
})

/* ------------------------------------------------------------------ */
/* A catraca dos controles mortos                                       */
/* ------------------------------------------------------------------ */

/** As props que o React guardou no elemento (é por elas que se sabe se há um `onClick`). */
function propsDoReact(el: Element): Record<string, unknown> {
  const chave = Object.keys(el).find((k) => k.startsWith('__reactProps$'))
  return chave ? ((el as unknown as Record<string, Record<string, unknown>>)[chave] ?? {}) : {}
}

/** Um botão "tem ação" quando reage ao clique, ou envia um formulário que reage ao envio. */
function temAcao(el: Element): boolean {
  const p = propsDoReact(el)
  if (typeof p.onClick === 'function' || typeof p.onPointerDown === 'function' || typeof p.onMouseDown === 'function') return true
  const form = el.closest('form')
  return (el as HTMLButtonElement).type === 'submit' && form !== null && typeof propsDoReact(form).onSubmit === 'function'
}

const INTERATIVO = 'button, a[href], select, input, textarea, [role="tab"], [role="radio"], [role="button"], [role="link"]'
const descrever = (el: Element) => `<${el.tagName.toLowerCase()} ${el.getAttribute('aria-label') ?? el.textContent?.trim().slice(0, 40) ?? ''}>`

/** As páginas renderizadas: as de sempre, a Simulação, a aba Mensagens e o painel do ciclo. */
const PAGINAS_DA_CATRACA: { caminho: string; ancora: string | RegExp }[] = [
  ...PAGINAS,
  { caminho: '/simulacao', ancora: /^O gateway de pagamento real será integrado em breve/ },
  { caminho: '/involuntario?aba=mensagens', ancora: 'Clínica Horizonte (fictícia)' },
  { caminho: '/involuntario?ciclo=2', ancora: 'Por que o sistema agiu assim' },
  { caminho: '/configuracao?secao=aparencia', ancora: 'Escolha como o painel aparece neste navegador.' },
  { caminho: '/involuntario?aba=mensagens&sem_canal=1', ancora: /Nada será enviado/ },
]

describe('catraca: nenhum controle morto nas páginas', () => {
  it.each(PAGINAS_DA_CATRACA.map((p) => [p.caminho, p.ancora] as const))('%s: todo botão e toda aba têm ação; nada com cara de aba fora de uma aba', async (caminho, ancora) => {
    ligarBackendFalso(caminho.includes('sem_canal=1') ? SEM_CANAL : COM_PENDENCIA)
    abrir(caminho)
    await screen.findAllByText(ancora, {}, ESPERA)
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), ESPERA)

    const mortos: string[] = []
    for (const el of document.querySelectorAll('button, [role="button"], [role="tab"]')) {
      if (!temAcao(el)) mortos.push(descrever(el))
    }
    expect(mortos, 'controles sem ação').toEqual([])

    // Toda aba está dentro de uma tablist, e tudo o que é desenhado como aba ativa (o preenchimento
    // claro) é um controle de verdade: botão, aba, rádio ou link.
    for (const el of document.querySelectorAll('[role="tab"]')) expect(el.closest('[role="tablist"]'), descrever(el)).not.toBeNull()
    const comCaraDeAba: string[] = []
    for (const el of document.querySelectorAll('[class]')) {
      const classes = (el.getAttribute('class') ?? '').split(/\s+/)
      if (!classes.includes('bg-paper')) continue
      // O botão de alternância (o "pino" claro) mora num rótulo que embrulha a caixa de seleção.
      const dentroDeAlternancia = (el.closest('label')?.querySelector('input') ?? null) !== null
      if (!el.matches(INTERATIVO) && !el.closest(INTERATIVO) && !dentroDeAlternancia) comCaraDeAba.push(descrever(el))
    }
    expect(comCaraDeAba, 'com cara de aba, sem ser controle').toEqual([])

    // Nenhum link para lugar nenhum, nenhum cursor de clique em quem não reage ao clique.
    expect([...document.querySelectorAll('a[href="#"]')].map(descrever)).toEqual([])
    for (const el of document.querySelectorAll('.cursor-pointer')) {
      const p = propsDoReact(el)
      const reage = typeof p.onClick === 'function' || el.matches(INTERATIVO) || el.querySelector('input, select, button') !== null
      expect(reage, `cursor de clique sem ação: ${descrever(el)}`).toBe(true)
    }
  })
})

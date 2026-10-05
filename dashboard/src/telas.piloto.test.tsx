// @vitest-environment jsdom
/**
 * Rodada 4, Fase 3: o modo piloto na tela, em MODO REAL, com o backend de mentira.
 * A Visão geral e o extrato dizem "Período de piloto: sem taxa" (M6), e a taxa que seria
 * cobrada fora do piloto aparece SÓ no extrato, numa coluna própria (M3). Fora do piloto,
 * a tela é a de sempre.
 */
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
  vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', '')
})

import App from './App'
import { fmt } from './lib/format'
import { ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'

const ESPERA = { timeout: 5000 }
const MES = new Date().toISOString().slice(0, 7)
const AVISO = 'Período de piloto: sem taxa'
const dia = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString().slice(0, 10)

const visao = (extra: Record<string, unknown> = {}) => ({
  dias: 30,
  periodo: { de: dia(29), ate: dia(0) },
  recuperado_involuntario: 200,
  cobrancas_recuperadas: 1,
  retido_voluntario: 400,
  clientes_mantidos: 1,
  ciclos_ativos: 0,
  aguardando_escolha: 0,
  clientes_risco_grave: 0,
  risco_grave_com_oferta: 0,
  taxa_recuperacao: 1,
  ciclos_com_desfecho: 1,
  mantido: 600,
  ...extra,
})

const linha = (extra: Record<string, unknown> = {}) => ({
  id: 'rec-1',
  data: new Date().toISOString(),
  cliente: 'Ana Prado',
  id_cliente: 'RN_ana',
  origem: 'involuntario',
  tipo: 'recuperacao',
  descricao: 'Recuperado na 1ª tentativa',
  valor_base: 200,
  fee: 30,
  fee_fora_do_piloto: null,
  liquido: 170,
  estornado: false,
  simulado: false,
  ...extra,
})

const extrato = (linhas: unknown[], piloto: { ativo: boolean; fee_fora_do_piloto: number | null }) => ({
  mes: MES,
  linhas,
  totais: { valor_base: 0, fee: 0, liquido: 0 },
  piloto,
  meses_de_mrr: 1,
})

const NO_PILOTO = [
  linha({ fee: 0, liquido: 200, fee_fora_do_piloto: 30 }),
  linha({ id: 'ret-2', cliente: 'Bia Lemos', id_cliente: 'c-bia', origem: 'voluntario', tipo: 'mantido', descricao: 'Aceitou desconto de 20% por 3 meses', valor_base: 400, fee: 0, liquido: 400, fee_fora_do_piloto: 60 }),
]

beforeEach(() => prepararJsdom())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}

async function tabelaDoExtrato() {
  await screen.findByText(`Extrato de ${fmt.mesPorExtenso(MES)}`, {}, ESPERA)
  await screen.findAllByText('Ana Prado', {}, ESPERA)
  const tabela = screen.getByRole('columnheader', { name: 'Taxa da CRAI' }).closest('table') as HTMLTableElement
  // O bloco do extrato: o cabeçalho, a tabela e a nota do rodapé.
  const bloco = tabela.parentElement!.parentElement as HTMLElement
  const cabecalho = [...tabela.querySelectorAll('thead th')].map((th) => th.textContent)
  const linhas = [...tabela.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent ?? ''))
  const total = [...tabela.querySelectorAll('tfoot td')].map((td) => td.textContent ?? '')
  return { bloco, cabecalho, linhas, total }
}

describe('empresa em período de piloto', () => {
  it('a Visão geral avisa, de forma discreta, e o cartão principal não fala em taxa descontada', async () => {
    ligarBackendFalso({ 'GET /metrics/visao-geral': visao({ piloto: true }) })
    abrir('/')
    const aviso = await screen.findByText(AVISO, {}, ESPERA)
    expect(aviso.tagName).toBe('SPAN')
    expect(aviso.className).toContain('t-label')
    expect(screen.getByText('Recuperado mais retido. No período de piloto, a CRAI não cobra taxa.')).toBeTruthy()
    expect(screen.queryByText('Recuperado mais retido, já descontada a taxa da CRAI.')).toBeNull()
    // O valor que seria cobrado não aparece fora do extrato.
    expect(document.body.textContent).not.toContain('Taxa fora do piloto')
  })

  it('o extrato avisa e ganha a coluna "Taxa fora do piloto", depois da taxa', async () => {
    ligarBackendFalso({
      'GET /metrics/visao-geral': visao({ piloto: true }),
      'GET /extrato': extrato(NO_PILOTO, { ativo: true, fee_fora_do_piloto: 90 }),
    })
    abrir('/?aba=extrato')
    const { bloco, cabecalho, linhas, total } = await tabelaDoExtrato()
    await waitFor(() => expect(within(bloco).getByText(AVISO)).toBeTruthy(), ESPERA)
    expect(cabecalho).toEqual(['Data', 'Cliente', 'O que aconteceu', 'Valor', 'Taxa da CRAI', 'Taxa fora do piloto', 'Líquido para você'])
    const ana = linhas.find((l) => l[1].includes('Ana Prado'))!
    expect(ana.slice(3)).toEqual([fmt.brl(200), '—', fmt.brl(30), fmt.brl(200)])
    const bia = linhas.find((l) => l[1].includes('Bia Lemos'))!
    expect(bia.slice(3)).toEqual([fmt.brl(400), '—', fmt.brl(60), fmt.brl(400)])
    // O total: nada cobrado, 90 que seriam cobrados, e o líquido é o valor inteiro.
    expect(total.slice(1)).toEqual([fmt.brl(600), fmt.brl(0), fmt.brl(90), fmt.brl(600)])
    expect(within(bloco).getByText(/Taxa fora do piloto: o que a CRAI cobraria fora do período de piloto\. Esse valor não é cobrado\./)).toBeTruthy()
  })

  it('o estorno de uma linha de piloto devolve a taxa que seria cobrada', async () => {
    ligarBackendFalso({
      'GET /metrics/visao-geral': visao({ piloto: true }),
      'GET /extrato': extrato(
        [linha({ fee: 0, liquido: 200, fee_fora_do_piloto: 30, estornado: true }), linha({ id: 'est-1-0', tipo: 'estorno', descricao: 'Devolução ao cliente dentro do prazo: estorno', valor_base: -200, fee: 0, liquido: -200, fee_fora_do_piloto: -30, estornado: true })],
        { ativo: true, fee_fora_do_piloto: 0 },
      ),
    })
    abrir('/?aba=extrato')
    const { linhas, total } = await tabelaDoExtrato()
    const estorno = linhas.find((l) => l[2].includes('Devolução'))!
    expect(estorno.slice(3)).toEqual([`− ${fmt.brl(200)}`, '—', `− ${fmt.brl(30)}`, `− ${fmt.brl(200)}`])
    expect(total.slice(1)).toEqual([fmt.brl(0), fmt.brl(0), fmt.brl(0), fmt.brl(0)])
  })
})

describe('fora do piloto', () => {
  it('nem a Visão geral nem o extrato falam de piloto, e o extrato tem as 6 colunas de sempre', async () => {
    ligarBackendFalso({
      'GET /metrics/visao-geral': visao({ piloto: false }),
      'GET /extrato': extrato([linha()], { ativo: false, fee_fora_do_piloto: null }),
    })
    abrir('/?aba=extrato')
    const { cabecalho, linhas, total } = await tabelaDoExtrato()
    expect(cabecalho).toEqual(['Data', 'Cliente', 'O que aconteceu', 'Valor', 'Taxa da CRAI', 'Líquido para você'])
    expect(linhas[0].slice(3)).toEqual([fmt.brl(200), `− ${fmt.brl(30)}`, fmt.brl(170)])
    expect(total.slice(1)).toEqual([fmt.brl(200), `− ${fmt.brl(30)}`, fmt.brl(170)])
    expect(screen.queryByText(AVISO)).toBeNull()
    expect(document.body.textContent).not.toContain('piloto')
    expect(screen.getByText('Recuperado mais retido, já descontada a taxa da CRAI.')).toBeTruthy()
  })

  it('um backend anterior, sem o campo, é fora do piloto', async () => {
    ligarBackendFalso()
    abrir('/')
    await screen.findByText('Recuperado mais retido, já descontada a taxa da CRAI.', {}, ESPERA)
    expect(screen.queryByText(AVISO)).toBeNull()
  })

  it('quem saiu do piloto continua vendo a coluna no mês em que ele valeu, sem o aviso', async () => {
    ligarBackendFalso({
      'GET /metrics/visao-geral': visao({ piloto: false }),
      'GET /extrato': extrato([linha({ fee: 0, liquido: 200, fee_fora_do_piloto: 30 }), linha({ id: 'rec-2', cliente: 'Caio Reis', id_cliente: 'RN_caio' })], { ativo: false, fee_fora_do_piloto: 30 }),
    })
    abrir('/?aba=extrato')
    const { cabecalho, linhas, total } = await tabelaDoExtrato()
    expect(cabecalho).toContain('Taxa fora do piloto')
    expect(linhas.find((l) => l[1].includes('Ana Prado'))!.slice(3)).toEqual([fmt.brl(200), '—', fmt.brl(30), fmt.brl(200)])
    // A linha de depois do piloto: taxa cobrada, e nada na coluna do piloto.
    expect(linhas.find((l) => l[1].includes('Caio Reis'))!.slice(3)).toEqual([fmt.brl(200), `− ${fmt.brl(30)}`, '—', fmt.brl(170)])
    expect(total.slice(1)).toEqual([fmt.brl(400), `− ${fmt.brl(30)}`, fmt.brl(30), fmt.brl(370)])
    expect(screen.queryByText(AVISO)).toBeNull()
  })
})

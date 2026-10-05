// @vitest-environment jsdom
/**
 * Rodada 3, Fase 1: a página do Voluntário em MODO REAL mostra o que o backend devolveu, e
 * nada do que era fictício. O backend aqui é o de mentira (`testes/modoRealFalso.ts`), com
 * respostas na forma exata das rotas novas.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
  vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', '')
})

import App from './App'
import { MODO_REAL, emDemonstracao } from './data/api'
import { clientesRisco } from './data/mock'
import { ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'

const ESPERA = { timeout: 5000 }
const MES = new Date().toISOString().slice(0, 7)
const NOME_DO_MES = new Intl.DateTimeFormat('pt-BR', { month: 'long' }).format(new Date(`${MES}-15T12:00:00`))

const RECENTES = {
  clientes: [
    {
      id: 'cli-001',
      nome: 'Oficina Boa Viagem',
      mrr: 480,
      faixa: 'grave',
      motivo: '41 dias sem login, 0 funcionalidades usadas, MRR R$ 480,00',
      decidido_por: 'regua',
      posicao_no_ranking: 1,
      abordagem: { oferta: 'desconto_20', oferta_legivel: 'desconto de 20% por 3 meses', canal: 'popup', canal_legivel: 'aviso dentro do produto', situacao: 'aceita', simulado: false },
      atualizado_em: new Date(Date.now() - 3 * 3_600_000).toISOString(),
      origem: 'upload',
      simulado: false,
    },
    {
      id: 'cli-002',
      nome: null,
      mrr: null,
      faixa: 'sem_dado',
      motivo: 'sem dado de atividade',
      decidido_por: null,
      posicao_no_ranking: null,
      abordagem: null,
      atualizado_em: null,
      origem: 'sdk',
      simulado: false,
    },
  ],
  total_na_base: 2,
}

const MES_VOLUNTARIO = {
  mes: MES,
  valor_liquido_mantido: 326.4,
  clientes_mantidos: 1,
  estornos: { quantidade: 1, valor_liquido_estornado: 85 },
  aceites_sem_valor: 0,
  grave: 1,
  preocupante: 0,
  ofertas_enviadas: 3,
  ofertas_aceitas: 1,
  meses_de_mrr: 6,
  prazo_estorno_dias: 90,
}

const BASE = { base: { total: 2, com_dados_comportamento: 1, decididos_pelo_modelo: 0, modelo_ativo: false, atualizada_em: new Date(Date.now() - 2 * 3_600_000).toISOString(), origem: 'anexo' } }

const COM_DADOS = {
  'GET /clientes/recentes': RECENTES,
  'GET /clientes/base': BASE,
  'GET /metrics/voluntario/mes': MES_VOLUNTARIO,
}

// A cada teste: o `unstubAllGlobals` do fim desfaz também o que o jsdom não tem (o
// `ResizeObserver` do gráfico), então a preparação é refeita.
beforeEach(() => prepararJsdom())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}

async function carregada() {
  expect(await screen.findByText(`Mantido para você em ${NOME_DO_MES}`, {}, ESPERA)).toBeTruthy()
  await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), ESPERA)
}

describe('voluntário em modo real', () => {
  it('as cinco funções da página estão no mapa de rotas reais', () => {
    expect(MODO_REAL).toBe(true)
    expect(emDemonstracao('clientesRecentes', 'baseClientes', 'resumoVoluntario', 'serieVoluntario', 'comparacaoReguaModelo', 'importarBase')).toBe(false)
  })

  it('os cartões mostram os números do backend, com o mês que ele devolveu', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/voluntario')
    await carregada()
    expect(screen.getByText(/R\$\s326/)).toBeTruthy()
    expect(screen.getByText(/1 cliente que ficou, 1 estorno\./)).toBeTruthy()
    expect(screen.getByText('1 de 3')).toBeTruthy()
    expect(screen.getByText('33% de aceite')).toBeTruthy()
    // Sem modelo ativo, a tela não afirma que o modelo decide.
    expect(screen.getByText('A régua decide em toda a base')).toBeTruthy()
    expect(screen.queryByText(/Modelo de IA em \d+% da base/)).toBeNull()
  })

  it('a tabela mostra os clientes do backend e nenhum dos fictícios', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/voluntario?aba=clientes')
    await carregada()
    const tabela = within(screen.getByRole('table'))
    expect(tabela.getByText('Oficina Boa Viagem')).toBeTruthy()
    expect(tabela.getByText('41 dias sem login, 0 funcionalidades usadas, MRR R$ 480,00')).toBeTruthy()
    // A oferta e o canal vêm em texto pronto do backend, com maiúscula.
    expect(tabela.getByText('Desconto de 20% por 3 meses')).toBeTruthy()
    expect(tabela.getByText(/Aviso dentro do produto/)).toBeTruthy()
    expect(tabela.getByText('Aceita')).toBeTruthy()
    expect(tabela.getByText(/1º de 2 pelo risco/)).toBeTruthy()
    // Sem nome na base, aparece o id que a empresa usa; sem avaliação, a tela diz isso.
    expect(tabela.getByText('cli-002')).toBeTruthy()
    expect(tabela.getByText('Sem avaliação')).toBeTruthy()
    expect(tabela.getByText('Sem dado de atividade')).toBeTruthy()
    for (const ficticio of clientesRisco) expect(screen.queryByText(ficticio.nome)).toBeNull()
  })

  it('a regra do mantido é a que o backend aplica', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/voluntario?aba=mantido')
    await carregada()
    const regra = screen.getByText('Como o valor é contado:').parentElement as HTMLElement
    expect(regra.textContent).toContain('6 meses da mensalidade')
    expect(regra.textContent).toContain('cancelar em até 90 dias')
    // A série veio toda zerada: o estado vazio, e não um gráfico inventado.
    expect(screen.getByText('Nada mantido ainda nos últimos 30 dias')).toBeTruthy()
  })

  it('sem comparação no backend, a aba não mostra números de comparação', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/voluntario?aba=decisao')
    await carregada()
    expect(screen.getByText(/A comparação aparece quando o modelo de IA estiver avaliando a sua base/)).toBeTruthy()
    expect(screen.queryByText('Avisou antes do cancelamento')).toBeNull()
    expect(screen.queryByText('Marcou como grave')).toBeNull()
  })

  it('com comparação, a frase é montada com os números do backend', async () => {
    ligarBackendFalso({
      ...COM_DADOS,
      'GET /clientes/base': { base: { ...BASE.base, total: 60, com_dados_comportamento: 60, decididos_pelo_modelo: 45, modelo_ativo: true } },
      'GET /metrics/voluntario/regua-x-modelo': { dias: 30, comparacao: { clientes_com_dados: 60, cancelamentos: 6, regua: { marcou_grave: 9, avisou_antes: 2 }, modelo: { marcou_grave: 7, avisou_antes: 4 } }, motivo_vazio: null },
    })
    abrir('/voluntario?aba=decisao')
    await carregada()
    expect(screen.getByText('Modelo de IA em 75% da base')).toBeTruthy()
    expect(screen.getByText(/Entre os 60 clientes com dados de comportamento, 6 cancelaram/)).toBeTruthy()
    expect(screen.getByText(/O modelo tinha marcado 4 dos 6 cancelamentos, com 7 clientes como graves\. A régua tinha marcado 2, com 9 como graves\./)).toBeTruthy()
  })

  it('sem base, a página avisa e a origem não é inventada', async () => {
    ligarBackendFalso()
    abrir('/voluntario')
    await carregada()
    expect(screen.getByText(/Ainda não há base de clientes/)).toBeTruthy()
    expect(screen.queryByText('Pela API')).toBeNull()
    expect(screen.queryByText('Por anexo')).toBeNull()
  })

  it('a origem da base é a que o backend registrou', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/voluntario')
    await carregada()
    expect(screen.getByText('Por anexo')).toBeTruthy()
    cleanup()
    ligarBackendFalso({ ...COM_DADOS, 'GET /clientes/base': { base: { ...BASE.base, origem: null } } })
    abrir('/voluntario')
    await carregada()
    expect(screen.getByText('Origem não registrada')).toBeTruthy()
  })

  it('a barra "Mostrar: Simulação" pede os simulados ao backend', async () => {
    const chamadas = ligarBackendFalso(COM_DADOS)
    abrir('/voluntario')
    await carregada()
    expect(chamadas.some((c) => c.includes('incluir_simulados'))).toBe(false)
    fireEvent.click(screen.getByRole('radio', { name: 'Simulação' }))
    await waitFor(() => expect(chamadas).toContain('GET /metrics/voluntario/mes?incluir_simulados=true'), ESPERA)
    expect(chamadas).toContain('GET /metrics/voluntario/serie?dias=30&incluir_simulados=true')
  })

  it('o anexo sobe de verdade, mostra o que o backend respondeu e a página lê a base de novo', async () => {
    const chamadas = ligarBackendFalso({
      ...COM_DADOS,
      'POST /clientes/importar': { importados: 7, rejeitados: [{ linha: 4, motivo: 'mrr ausente' }], colunas_nao_encontradas: [], linhas_sem_dado_comportamental: 2 },
    })
    abrir('/voluntario')
    await carregada()
    const antes = chamadas.filter((c) => c.startsWith('GET /clientes/base')).length
    const arquivo = new File(['customer_id_externo,mrr,billing_profile\nc1,100,PJ\n'], 'base.csv', { type: 'text/csv' })
    fireEvent.change(screen.getByLabelText('Anexar a base em CSV ou XLSX'), { target: { files: [arquivo] } })
    const recebida = (await screen.findByText(/Base recebida: base\.csv/, {}, ESPERA)).closest('[role="status"]') as HTMLElement
    expect(chamadas).toContain('POST /clientes/importar')
    expect(recebida.textContent).toContain('8 linhas')
    expect(recebida.textContent).toContain('7 na base')
    expect(recebida.textContent).toContain('1 recusadas')
    expect(recebida.textContent).toContain('2 sem dado de comportamento')
    expect(recebida.textContent).toContain('Linha 4: mrr ausente')
    // Nada do que o backend não informa é mostrado, e o aviso de demonstração não aparece.
    expect(recebida.textContent).not.toContain('novos')
    expect(recebida.textContent).not.toContain('corrigidos')
    expect(recebida.textContent).not.toContain('Demonstração')
    await waitFor(() => expect(chamadas.filter((c) => c.startsWith('GET /clientes/base')).length).toBeGreaterThan(antes), ESPERA)
  })
})

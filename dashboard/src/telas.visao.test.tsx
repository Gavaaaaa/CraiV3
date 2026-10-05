// @vitest-environment jsdom
/**
 * Rodada 3, Fase 2: a Visão geral em MODO REAL mostra o que o backend devolveu, e nada do que
 * era fictício. O backend aqui é o de mentira (`testes/modoRealFalso.ts`), com respostas na
 * forma exata das rotas novas.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
  vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', '')
})

import App from './App'
import { MODO_REAL, emDemonstracao } from './data/api'
import { atividades as atividadesDoMock, extrato as extratoDoMock } from './data/mock'
import { ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'

const ESPERA = { timeout: 5000 }
const HOJE = new Date()
const MES = `${HOJE.getFullYear()}-${String(HOJE.getMonth() + 1).padStart(2, '0')}`
const NOME_DO_MES = new Intl.DateTimeFormat('pt-BR', { month: 'long' }).format(new Date(`${MES}-15T12:00:00`))
const haHoras = (h: number) => new Date(Date.now() - h * 3_600_000).toISOString()
const dia = (atras: number) => new Date(Date.now() - atras * 86_400_000).toISOString().slice(0, 10)

const VISAO = {
  dias: 30,
  periodo: { de: dia(29), ate: dia(0) },
  recuperado_involuntario: 1360,
  cobrancas_recuperadas: 3,
  retido_voluntario: 340,
  clientes_mantidos: 1,
  ciclos_ativos: 2,
  aguardando_escolha: 1,
  clientes_risco_grave: 4,
  risco_grave_com_oferta: 1,
  taxa_recuperacao: 0.6,
  ciclos_com_desfecho: 5,
  mantido: 1700,
}

const FUNIL = {
  mes: MES,
  etapas: [
    { etapa: 'falhas', rotulo: 'Cobranças que falharam', chegaram: 6, valor: 2051, recuperados_aqui: 0, valor_recuperado_aqui: 0 },
    { etapa: 'tentativa_1', rotulo: 'Tentativa 1', chegaram: 5, valor: 2050, recuperados_aqui: 1, valor_recuperado_aqui: 200 },
    { etapa: 'tentativa_2', rotulo: 'Tentativa 2', chegaram: 3, valor: 1700, recuperados_aqui: 1, valor_recuperado_aqui: 400 },
    { etapa: 'tentativa_3', rotulo: 'Tentativa 3', chegaram: 2, valor: 1300, recuperados_aqui: 0, valor_recuperado_aqui: 0 },
    { etapa: 'mensagem', rotulo: 'Mensagem', chegaram: 2, valor: 1300, recuperados_aqui: 1, valor_recuperado_aqui: 1000 },
  ],
  desfecho: { recuperados: 3, encerrados: 2, em_andamento: 1 },
}

const FUNCIONA = {
  dias: 30,
  causas: [
    { rotulo: 'Saldo insuficiente', casos: 8, sucessos: 6, taxa: 0.75, valor_liquido: 1360 },
    { rotulo: 'Limite do Pix excedido', casos: 1, sucessos: 0, taxa: 0, valor_liquido: 0 },
  ],
  ofertas: [{ rotulo: 'Desconto de 20% por 3 meses', casos: 6, sucessos: 3, taxa: 0.5 }],
  canais: [
    { rotulo: 'WhatsApp', casos: 6, sucessos: 3, taxa: 0.5 },
    { rotulo: 'E-mail', casos: 8, sucessos: 2, taxa: 0.25 },
  ],
}

const ATIVIDADE = {
  dias: 30,
  atividades: [
    { id: 'ret-1', em: haHoras(2), tipo: 'oferta_aceita', texto: 'Ana Prado aceitou desconto de 20% por 3 meses', valor: 340, simulado: false },
    { id: 'rec-7', em: haHoras(30), tipo: 'recuperado', texto: 'Cobrança de um cliente sem cadastro recuperada na 2ª tentativa', valor: 340, simulado: false },
    { id: 'xyz-1', em: haHoras(31), tipo: 'tipo_que_a_tela_nao_conhece', texto: 'Evento de uma versão futura', valor: null, simulado: false },
  ],
}

const EXTRATO = {
  mes: MES,
  linhas: [
    { id: 'est-7-1', data: haHoras(1), cliente: 'Ana Prado', id_cliente: 'RN_ana', origem: 'involuntario', tipo: 'estorno', descricao: 'Devolução ao cliente dentro do prazo: estorno', valor_base: -200, fee: -30, liquido: -170, estornado: true, simulado: false },
    { id: 'ret-1', data: haHoras(2), cliente: null, id_cliente: 'c-ana', origem: 'voluntario', tipo: 'mantido', descricao: 'Aceitou desconto de 20% por 3 meses', valor_base: 400, fee: 60, liquido: 340, estornado: false, simulado: false },
    { id: 'rec-7', data: haHoras(30), cliente: 'Ana Prado', id_cliente: 'RN_ana', origem: 'involuntario', tipo: 'recuperacao', descricao: 'Recuperado na 1ª tentativa', valor_base: 200, fee: 30, liquido: 170, estornado: true, simulado: false },
  ],
  totais: { valor_base: 400, fee: 60, liquido: 340 },
  meses_de_mrr: 1,
}

const SAUDE = {
  status: 'ok',
  relogio: { ligado: true, motivo_desligado: null, ultima_passagem_em: new Date().toISOString(), ultima_passagem_ok: true },
  modelos: { carregados: 2, total: 4, ausentes: ['dia_provavel_de_saldo', 'risco_voluntario'] },
  redator: { disponivel: false },
  base: { informada: true, atualizada_em: haHoras(3), origem: 'api' },
}

const COM_DADOS = {
  'GET /metrics/visao-geral': VISAO,
  'GET /metrics/serie': { dias: 30, pontos: Array.from({ length: 30 }, (_, i) => ({ dia: dia(29 - i), involuntario: i === 29 ? 1360 : 0, voluntario: i === 29 ? 340 : 0 })) },
  'GET /metrics/involuntario/funil': FUNIL,
  'GET /metrics/o-que-funciona': FUNCIONA,
  'GET /atividade': ATIVIDADE,
  'GET /extrato': EXTRATO,
  'GET /health': SAUDE,
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
  expect(await screen.findByText('Mantido para você nos últimos 30 dias', {}, ESPERA)).toBeTruthy()
  await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), ESPERA)
}

describe('visão geral em modo real', () => {
  it('as sete funções da página estão no mapa de rotas reais', () => {
    expect(MODO_REAL).toBe(true)
    expect(emDemonstracao('resumoVisaoGeral', 'serieDupla', 'funil', 'oQueFunciona', 'atividade', 'extrato', 'saude')).toBe(false)
  })

  it('os cartões mostram os números do backend', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/')
    await carregada()
    const resumo = within(screen.getByLabelText('Resumo dos últimos 30 dias'))
    expect(resumo.getByText(/R\$\s1\.700/)).toBeTruthy() // 1360 + 340
    expect(resumo.getByText('3 cobranças Pix que voltaram')).toBeTruthy()
    expect(resumo.getByText('1 cliente que ficou')).toBeTruthy()
    expect(resumo.getByText('1 aguardando sua escolha')).toBeTruthy()
    expect(resumo.getByText('1 já recebeu uma oferta')).toBeTruthy()
    expect(resumo.getByText('60%')).toBeTruthy()
    expect(resumo.getByText('Das 5 cobranças que já tiveram desfecho')).toBeTruthy()
    // O cartão do grupo de controle continua planejado, sem número.
    expect(resumo.getByText('Planejado')).toBeTruthy()
  })

  it('sem desfecho, a taxa é um traço e a tela diz por quê, em vez de mostrar 0%', async () => {
    ligarBackendFalso()
    abrir('/')
    await carregada()
    const taxa = screen.getByText('Taxa de recuperação').closest('div')?.parentElement as HTMLElement
    expect(screen.getByText('Nenhuma cobrança teve desfecho no período')).toBeTruthy()
    expect(taxa.textContent).not.toContain('0%')
    expect(screen.getByText('Nada mantido ainda nos últimos 30 dias')).toBeTruthy()
  })

  it('o funil é o do mês que o backend devolveu, com os números dele', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/?aba=caminho')
    await carregada()
    expect(screen.getByText(`Funil de recuperação em ${NOME_DO_MES}`)).toBeTruthy()
    expect(screen.getByText('Desfecho das 6 cobranças')).toBeTruthy()
    expect(screen.getAllByText(/recuperada aqui/)).toHaveLength(3)
  })

  it('o funil vazio diz o mês certo, não "setembro" fixo', async () => {
    ligarBackendFalso()
    abrir('/?aba=caminho')
    await carregada()
    expect(screen.getByText(`Nenhuma cobrança falhou em ${NOME_DO_MES}`)).toBeTruthy()
  })

  it('o que mais funciona: as frases saem dos números do backend', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/?aba=funciona')
    await carregada()
    expect(screen.getByText('Saldo insuficiente é a causa mais comum, e 75% dessas cobranças voltaram.')).toBeTruthy()
    expect(screen.getByText('Desconto de 20% por 3 meses é a oferta mais aceita.')).toBeTruthy()
    expect(screen.getByText('WhatsApp tem 2,0 vezes mais resposta que E-mail.')).toBeTruthy()
    expect(screen.getAllByText('Poucos casos')).toHaveLength(1)
  })

  it('o que mais funciona, sem desfecho nenhum, não inventa frase', async () => {
    ligarBackendFalso()
    abrir('/?aba=funciona')
    await carregada()
    expect(screen.getByText('Ainda não há cobranças nem ofertas com desfecho nos últimos 30 dias.')).toBeTruthy()
    expect(screen.queryByText(/é a causa mais comum/)).toBeNull()
  })

  it('a atividade é a do backend: nenhuma frase fictícia, e evento desconhecido não aparece', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/?aba=atividade')
    await carregada()
    expect(screen.getByText('Ana Prado aceitou desconto de 20% por 3 meses')).toBeTruthy()
    expect(screen.getByText('Cobrança de um cliente sem cadastro recuperada na 2ª tentativa')).toBeTruthy()
    expect(screen.queryByText('Evento de uma versão futura')).toBeNull()
    for (const ficticia of atividadesDoMock) expect(screen.queryByText(ficticia.texto)).toBeNull()
  })

  it('o extrato: o mês certo, o estorno como linha negativa e o total que fecha', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/?aba=extrato')
    await carregada()
    expect(screen.getByText(`Extrato de ${NOME_DO_MES}`)).toBeTruthy()
    const linhas = within(screen.getByRole('table')).getAllByRole('row')
    const estorno = linhas.find((l) => l.textContent?.includes('Devolução ao cliente'))!
    expect(estorno.textContent).toContain('Estorno')
    expect(estorno.textContent).toMatch(/− R\$\s200,00/)
    expect(estorno.textContent).toMatch(/\+ R\$\s30,00/) // a taxa devolvida
    expect(estorno.textContent).toMatch(/− R\$\s170,00/)
    // A recuperação que foi estornada depois continua com o valor dela, marcada.
    const recuperacao = linhas.find((l) => l.textContent?.includes('Recuperado na 1ª tentativa'))!
    expect(recuperacao.textContent).toContain('Estornado')
    expect(recuperacao.textContent).toMatch(/R\$\s170,00/)
    // Sem nome na base, aparece o id que a empresa usa.
    expect(within(screen.getByRole('table')).getByText('c-ana')).toBeTruthy()
    const rodape = linhas[linhas.length - 1]
    expect(rodape.textContent).toMatch(/R\$\s400,00/)
    expect(rodape.textContent).toMatch(/− R\$\s60,00/)
    expect(rodape.textContent).toMatch(/R\$\s340,00/)
    for (const ficticia of extratoDoMock) expect(screen.queryByText(ficticia.cliente)).toBeNull()
  })

  it('para quem não pode ver o extrato, a página abre e a aba explica', async () => {
    ligarBackendFalso({ ...COM_DADOS, 'GET /extrato': { __status: 403, corpo: { detail: { motivo: 'papel_insuficiente', detalhe: 'x' } } } })
    abrir('/?aba=extrato')
    await carregada()
    expect(screen.getByText('O extrato é só para o dono e os administradores')).toBeTruthy()
    // O resto da página não caiu junto.
    expect(screen.getByText('3 cobranças Pix que voltaram')).toBeTruthy()
    expect(screen.queryByText('Tentar de novo')).toBeNull()
  })

  it('a saúde do sistema diz o que o backend informou', async () => {
    ligarBackendFalso(COM_DADOS)
    abrir('/?aba=saude')
    await carregada()
    const item = (rotulo: string) => screen.getByText(rotulo).closest('li') as HTMLElement
    expect(item('Decisões automáticas').textContent).toContain('2 de 4 modelos carregados.')
    expect(item('Redator de mensagens').textContent).toContain('Fora do ar; o sistema usa os textos de reserva.')
    expect(item('Base de clientes').textContent).toMatch(/Atualizada pela API há 3 h\./)
    expect(screen.getAllByText('Precisa de atenção').length).toBeGreaterThan(0)
  })

  it('a barra "Mostrar: Simulação" pede os simulados em todas as rotas da página', async () => {
    const chamadas = ligarBackendFalso(COM_DADOS)
    abrir('/')
    await carregada()
    expect(chamadas.some((c) => c.includes('incluir_simulados'))).toBe(false)
    fireEvent.click(screen.getByRole('radio', { name: 'Simulação' }))
    await waitFor(() => expect(chamadas).toContain('GET /metrics/visao-geral?dias=30&incluir_simulados=true'), ESPERA)
    for (const esperada of [
      'GET /metrics/serie?dias=30&incluir_simulados=true',
      'GET /metrics/involuntario/funil?incluir_simulados=true',
      'GET /metrics/o-que-funciona?dias=30&incluir_simulados=true',
      'GET /atividade?limite=12&incluir_simulados=true',
      'GET /extrato?incluir_simulados=true',
    ]) {
      expect(chamadas, esperada).toContain(esperada)
    }
  })
})

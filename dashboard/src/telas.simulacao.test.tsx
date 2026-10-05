// @vitest-environment jsdom
/**
 * Rodada 3, Fase 3: a página "Simulação do gateway" em MODO REAL fala com as rotas da
 * simulação e mostra o que o sistema de verdade fez. O backend aqui é o de mentira
 * (`testes/modoRealFalso.ts`), respondendo com respostas capturadas do backend de verdade
 * (`testes/simulacaoDeExemplo.ts`).
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.hoisted(() => {
  vi.stubEnv('VITE_CRAI_API_URL', 'http://backend.de.mentira')
  vi.stubEnv('VITE_CRAI_MOSTRAR_DEMONSTRACAO', '')
})

import App from './App'
import { MODO_REAL, emDemonstracao } from './data/api'
import { corposEnviados, ligarBackendFalso, prepararJsdom } from './testes/modoRealFalso'
import {
  CICLO_SIMULADO,
  RETENCAO_ACEITA,
  RETENCAO_PELO_MODELO_COM_OFERTA,
  RETENCAO_PELO_MODELO_MAIS_LEVE,
  RETENCAO_PELO_MODELO_SEM_OFERTA,
  RETENCAO_SEM_RISCO,
  SIM_COBRADA,
  SIM_ENVIADA,
  SIM_ESCOLHA,
  SIM_MENSAGENS,
  SIM_RECUPERADA,
  SIM_VAZIA,
} from './testes/simulacaoDeExemplo'

const ESPERA = { timeout: 6000 }
const ID_DO_CICLO = SIM_MENSAGENS.ciclo!.ciclo.id

beforeEach(() => prepararJsdom())
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}

const corposDe = (chave: string) => corposEnviados().filter((c) => c.chave === chave).map((c) => c.corpo)

describe('simulação do gateway em modo real', () => {
  it('as sete funções da página estão no mapa de rotas reais', () => {
    expect(MODO_REAL).toBe(true)
    expect(
      emDemonstracao('simulacao', 'simularCobranca', 'simulacaoAvancar', 'simulacaoAvancarAteProximaAcao', 'simulacaoEscolherMensagem', 'simulacaoLimpar', 'simularRetencao'),
    ).toBe(false)
  })

  it('o selo de demonstração da página aparece sempre, mesmo ligada ao backend', async () => {
    ligarBackendFalso()
    abrir('/simulacao')
    expect(await screen.findByRole('form', { name: 'Cliente fictício' }, ESPERA)).toBeTruthy()
    expect(screen.getAllByText('Demonstração', { exact: true }).length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText(/Nada é enviado a nenhum banco nem a nenhuma pessoa\./)).toBeTruthy()
    // O formulário diz o que o sistema de verdade não lê.
    expect(screen.getByText('O sistema não recebe o perfil: ele estima o dia de saldo sozinho.')).toBeTruthy()
  })

  it('simular cobrança: cria o cliente fictício, cobra, e mostra o que o sistema decidiu', async () => {
    const chamadas = ligarBackendFalso({
      'POST /simulacao/cliente': SIM_VAZIA,
      'POST /simulacao/cobrar': SIM_COBRADA,
    })
    abrir('/simulacao')
    const formulario = await screen.findByRole('form', { name: 'Cliente fictício' }, ESPERA)
    fireEvent.submit(formulario)
    // O painel só aparece depois da animação da cobrança.
    expect(await screen.findByText('O que o sistema está pensando', {}, ESPERA)).toBeTruthy()

    expect(chamadas.filter((c) => c.startsWith('POST'))).toEqual(['POST /dev/token', 'POST /simulacao/cliente', 'POST /simulacao/cobrar'].filter((c) => chamadas.includes(c)))
    // O corpo leva só os campos do formulário: nenhum dado de pessoa de verdade tem onde entrar.
    const [corpo] = corposDe('POST /simulacao/cliente') as Record<string, unknown>[]
    expect(Object.keys(corpo).sort()).toEqual(['mensalidade', 'nome', 'perfil', 'verdade'])
    expect(Object.keys(corpo.verdade as object).sort()).toEqual(['chance_pagar', 'dias_ate_saldo', 'vai_revogar'])
    expect(corpo).toMatchObject({ nome: 'Ana Souza', mensalidade: 890, perfil: 'clt' })

    // As frases, a chance e o plano são os do backend.
    expect(screen.getByText('Causa da falha: saldo insuficiente.')).toBeTruthy()
    expect(screen.getByText(`${Math.round(SIM_COBRADA.chance_recuperar! * 100)}% de chance`)).toBeTruthy()
    expect(screen.getByText(/^Plano: 3 tentativas dentro de 7 dias/)).toBeTruthy()
    expect(screen.getByText('Por que essa chance')).toBeTruthy()
    expect(screen.getAllByText('Aumentou a chance de recuperar').length).toBeGreaterThan(0)
    expect(screen.queryByText(/ pts$/)).toBeNull()
    expect(screen.getByText('Próxima ação do sistema')).toBeTruthy()
    expect(screen.getAllByText('Tentativa 1').length).toBeGreaterThan(0)
    // Sem a CRAI: a explicação do backend, que é quem conhece a verdade escondida.
    expect(screen.getByText(SIM_COBRADA.sem_crai!.explicacao)).toBeTruthy()
    expect(screen.getByText('Em andamento')).toBeTruthy()
  })

  it('avançar o relógio chama a rota, com o corpo certo em cada botão', async () => {
    ligarBackendFalso({ 'GET /simulacao': SIM_COBRADA, 'POST /simulacao/avancar': SIM_COBRADA })
    abrir('/simulacao')
    fireEvent.click(await screen.findByRole('button', { name: /Avançar até a próxima ação/ }, ESPERA))
    await waitFor(() => expect(corposDe('POST /simulacao/avancar')).toEqual([{ ate_proxima_acao: true }]), ESPERA)
    await waitFor(() => expect((screen.getByRole('button', { name: 'Avançar 1 dia' }) as HTMLButtonElement).disabled).toBe(false), ESPERA)
    fireEvent.click(screen.getByRole('button', { name: 'Avançar 1 dia' }))
    await waitFor(() => expect(corposDe('POST /simulacao/avancar')).toEqual([{ ate_proxima_acao: true }, { dias: 1 }]), ESPERA)
  })

  it('as 3 mensagens são as do backend, e a escolha vai pela rota do ciclo, no ciclo simulado', async () => {
    const chamadas = ligarBackendFalso({
      'GET /simulacao': SIM_MENSAGENS,
      [`POST /ciclos/${ID_DO_CICLO}/mensagens/escolher`]: SIM_ESCOLHA,
    })
    abrir('/simulacao')
    expect(await screen.findByRole('heading', { name: '3 mensagens sugeridas' }, ESPERA)).toBeTruthy()
    expect(screen.getByText(`Sem escolha em ${SIM_MENSAGENS.prazo_escolha_horas} h, a recomendada é enviada`)).toBeTruthy()
    for (const m of SIM_MENSAGENS.ciclo!.mensagens) expect(screen.getByText(m.texto as string, { normalizer: (t) => t })).toBeTruthy()
    expect(screen.getAllByText('Recomendada')).toHaveLength(1)
    const botoes = screen.getAllByRole('button', { name: /Enviar esta/ })
    expect(botoes).toHaveLength(3)

    const facilitacao = screen.getByText('Facilitação').closest('li') as HTMLElement
    fireEvent.click(within(facilitacao).getByRole('button', { name: /Enviar esta/ }))
    await waitFor(() => expect(corposDe(`POST /ciclos/${ID_DO_CICLO}/mensagens/escolher`)).toEqual([{ rodada: 1, abordagem: 'facilitacao' }]), ESPERA)
    // A tela relê a simulação depois de escolher.
    await waitFor(() => expect(chamadas.filter((c) => c === 'GET /simulacao').length).toBeGreaterThanOrEqual(3), ESPERA)
  })

  it('mensagem enviada: o canal é o que o backend usou', async () => {
    ligarBackendFalso({ 'GET /simulacao': SIM_ENVIADA })
    abrir('/simulacao')
    // Na faixa do cartão e na frase do que o sistema está pensando.
    const faixa = (await screen.findAllByRole('status', {}, ESPERA)).find((f) => /^Mensagem enviada por WhatsApp/.test(f.textContent ?? ''))
    expect(faixa).toBeTruthy()
    expect(screen.getAllByText('Prazo para resposta do cliente').length).toBeGreaterThan(0)
  })

  it('recuperada: o líquido do backend; "Simular outro cliente" não apaga nada, "Apagar" apaga', async () => {
    const chamadas = ligarBackendFalso({ 'GET /simulacao': SIM_RECUPERADA, 'DELETE /simulacao': { ...SIM_VAZIA, limpo: true, arquivos_apagados: 3 } })
    abrir('/simulacao')
    expect(await screen.findByText('Pagamento recuperado pela mensagem', {}, ESPERA)).toBeTruthy()
    expect(screen.getAllByText(/R\$\s756,50 para você/).length).toBeGreaterThan(0)
    expect(screen.getByText(/^Pagamento recuperado depois da mensagem\./)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: /Simular outro cliente/ }))
    expect(await screen.findByRole('form', { name: 'Cliente fictício' }, ESPERA)).toBeTruthy()
    expect(chamadas).not.toContain('DELETE /simulacao')
    cleanup()

    abrir('/simulacao')
    fireEvent.click(await screen.findByRole('button', { name: 'Apagar todos os dados da simulação' }, ESPERA))
    expect(await screen.findByRole('form', { name: 'Cliente fictício' }, ESPERA)).toBeTruthy()
    expect(chamadas).toContain('DELETE /simulacao')
  })

  it('o backend recusa um nome que parece dado real: a frase aparece, e o formulário continua', async () => {
    ligarBackendFalso({
      'POST /simulacao/cliente': { __status: 422, corpo: { detail: { motivo: 'dado_que_parece_real', campo: 'nome', detalhe: 'o nome parece um telefone' } } },
    })
    abrir('/simulacao')
    fireEvent.submit(await screen.findByRole('form', { name: 'Cliente fictício' }, ESPERA))
    const aviso = await screen.findByRole('alert', {}, ESPERA)
    expect(aviso.textContent).toContain('O nome parece um dado real (CPF, e-mail, telefone ou chave Pix). Aqui só entra um nome inventado.')
    expect(screen.getByRole('form', { name: 'Cliente fictício' })).toBeTruthy()
  })

  it('o membro não simula: o 403 do backend vira aviso', async () => {
    ligarBackendFalso({
      'POST /simulacao/cliente': { __status: 403, corpo: { detail: { motivo: 'papel_insuficiente' } } },
    })
    abrir('/simulacao')
    fireEvent.submit(await screen.findByRole('form', { name: 'Cliente fictício' }, ESPERA))
    expect((await screen.findByRole('alert', {}, ESPERA)).textContent).toContain('Seu papel não permite esta ação.')
  })
})

describe('cliente em risco (voluntário) em modo real', () => {
  it('o formulário tem as quatro ofertas de verdade e manda só o que o formulário tem', async () => {
    ligarBackendFalso({ 'POST /simulacao/retencao': RETENCAO_ACEITA })
    abrir('/simulacao?aba=retencao')
    const formulario = await screen.findByRole('form', { name: 'Cliente fictício em risco' }, ESPERA)
    for (const oferta of ['Desconto de 10% por 3 meses', 'Desconto de 20% por 3 meses', 'Pausa de 1 mês na assinatura, sem custo', 'Troca para Pix ou boleto em 1 clique']) {
      expect(within(formulario).getByText(oferta)).toBeTruthy()
    }
    expect(within(formulario).getByText('Abriu a página de cancelamento')).toBeTruthy()
    expect(within(formulario).getByText(/Cada sinal marcado vira um dado que o sistema recebe/)).toBeTruthy()
    fireEvent.submit(formulario)

    expect(await screen.findByText('Como o sistema decidiu', {}, ESPERA)).toBeTruthy()
    const [corpo] = corposDe('POST /simulacao/retencao') as Record<string, unknown>[]
    expect(Object.keys(corpo).sort()).toEqual(['mrr', 'nome', 'propensao', 'sinais'])
    expect(Object.keys(corpo.propensao as object).sort()).toEqual(['desconto_10', 'desconto_20', 'pausa_1_mes', 'pix_boleto_flash'])
    for (const p of Object.values(corpo.propensao as Record<string, number>)) expect(p >= 0 && p <= 1).toBe(true)
    expect(Object.keys(corpo.sinais as object).sort()).toEqual(['abriu_cancelamento', 'atraso', 'tickets', 'uso_caiu'])
    for (const s of Object.values(corpo.sinais as Record<string, unknown>)) expect(typeof s).toBe('boolean')

    // O que a tela mostra é o que o backend respondeu: oferta, canal, quem decidiu, aceite.
    expect(screen.getByText('Decidido pela régua')).toBeTruthy()
    expect(screen.getByText(/Pausa de 1 mês na assinatura, sem custo · por aviso dentro do produto/)).toBeTruthy()
    expect(screen.getByText(/^Abriu a página de cancelamento, sem dado de uso$/)).toBeTruthy()
    expect(screen.getByText(/Risco calculado: 90%\. Oferta escolhida: pausa de 1 mês/)).toBeTruthy()
    expect(screen.getByText('Cliente aceitou a oferta')).toBeTruthy()
    expect(screen.getByText(/1 mês da mensalidade, menos o desconto dado, líquido da taxa\. Se cancelar em 30 dias, o valor é estornado\./)).toBeTruthy()
    // A pausa mantém R$ 0 com 1 mês contado, e a tela explica.
    expect(screen.getByText(/o valor mantido é zero\./)).toBeTruthy()
    expect(screen.queryByText(/por WhatsApp/)).toBeNull()
  })

  it('sem sinal de risco, não há oferta nem aceite na tela', async () => {
    ligarBackendFalso({ 'POST /simulacao/retencao': RETENCAO_SEM_RISCO })
    abrir('/simulacao?aba=retencao')
    fireEvent.submit(await screen.findByRole('form', { name: 'Cliente fictício em risco' }, ESPERA))
    expect(await screen.findByText('Como o sistema decidiu', {}, ESPERA)).toBeTruthy()
    expect(screen.getByText('Sem risco')).toBeTruthy()
    expect(screen.queryByText('O que o sistema fez')).toBeNull()
    expect(screen.queryByText(/Cliente (aceitou|recusou) a oferta/)).toBeNull()
    // A tela diz por que não houve oferta, com o risco e o corte que o backend informou.
    expect(screen.getByText(/Risco calculado: 0%\. Abaixo de 60% o sistema não intervém: nenhuma oferta\./)).toBeTruthy()
    expect(screen.getByText('O sistema não interveio: com ou sem a CRAI, este cliente segue como está.')).toBeTruthy()
  })

  it('com o modelo de IA decidindo e sem oferta, a tela mostra a frase do backend, e não o corte de 60%', async () => {
    ligarBackendFalso({ 'POST /simulacao/retencao': RETENCAO_PELO_MODELO_SEM_OFERTA })
    abrir('/simulacao?aba=retencao')
    fireEvent.submit(await screen.findByRole('form', { name: 'Cliente fictício em risco' }, ESPERA))
    expect(await screen.findByText('Como o sistema decidiu', {}, ESPERA)).toBeTruthy()
    expect(screen.getByText('Decidido pelo modelo de IA')).toBeTruthy()
    expect(screen.queryByText('O que o sistema fez')).toBeNull()
    expect(screen.getByText(/Risco calculado: 20%\. Quem decidiu o risco foi o modelo de IA\. Pela posição na base, este cliente não está entre os graves nem os preocupantes/)).toBeTruthy()
    expect(screen.queryByText(/Abaixo de 60%/)).toBeNull()
  })

  it('com o modelo de IA decidindo, quem abre a página de cancelamento recebe oferta, e a tela diz a regra', async () => {
    ligarBackendFalso({ 'POST /simulacao/retencao': RETENCAO_PELO_MODELO_COM_OFERTA })
    abrir('/simulacao?aba=retencao')
    fireEvent.submit(await screen.findByRole('form', { name: 'Cliente fictício em risco' }, ESPERA))
    expect(await screen.findByText('Como o sistema decidiu', {}, ESPERA)).toBeTruthy()
    expect(screen.getByText('Decidido pelo modelo de IA')).toBeTruthy()
    expect(screen.getByText('O que o sistema fez')).toBeTruthy()
    // Rodada 4: por intenção explícita sai a oferta do bandit (antes, a de menor custo, que era a troca para Pix ou boleto).
    expect(screen.getByText(/Pausa de 1 mês na assinatura, sem custo · por aviso dentro do produto/)).toBeTruthy()
    expect(screen.getAllByText(/O cliente mostrou intenção explícita de sair\. Nesse caso o sistema age sempre, por regra/).length).toBeGreaterThan(0)
    expect(screen.getByText(/Risco calculado: 21%\. Oferta escolhida: pausa de 1 mês na assinatura, sem custo\./)).toBeTruthy()
    expect(screen.getAllByText(/Nesta rodada, esta teve o maior retorno esperado/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/menor custo/)).toBeNull()
    expect(screen.getByText('Cliente aceitou a oferta')).toBeTruthy()
    expect(screen.queryByText(/Abaixo de 60%/)).toBeNull()
  })

  it('com o modelo de IA decidindo, o preocupante pela posição na base leva a oferta de retenção de menor custo', async () => {
    ligarBackendFalso({ 'POST /simulacao/retencao': RETENCAO_PELO_MODELO_MAIS_LEVE })
    abrir('/simulacao?aba=retencao')
    fireEvent.submit(await screen.findByRole('form', { name: 'Cliente fictício em risco' }, ESPERA))
    expect(await screen.findByText('Como o sistema decidiu', {}, ESPERA)).toBeTruthy()
    expect(screen.getByText('Preocupante')).toBeTruthy()
    expect(screen.getByText(/Desconto de 10% por 3 meses · por e-mail/)).toBeTruthy()
    expect(screen.getAllByText(/Como o caso é preocupante, e não grave, o sistema escolheu a oferta de retenção de menor custo/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/Troca para Pix ou boleto em 1 clique · por/)).toBeNull()
  })

  it('o formulário diz quando o sistema age com o modelo de IA decidindo', async () => {
    ligarBackendFalso()
    abrir('/simulacao?aba=retencao')
    const formulario = await screen.findByRole('form', { name: 'Cliente fictício em risco' }, ESPERA)
    expect(within(formulario).getByText(/Quando o modelo decide, o sistema age sempre que houver intenção explícita \(a página de cancelamento\) e, nos outros casos, quando o cliente está entre os de maior risco da base\./)).toBeTruthy()
  })

  it('erro do backend: o formulário volta com a frase', async () => {
    ligarBackendFalso({ 'POST /simulacao/retencao': { __status: 422, corpo: { detail: { motivo: 'dado_que_parece_real', campo: 'nome' } } } })
    abrir('/simulacao?aba=retencao')
    fireEvent.submit(await screen.findByRole('form', { name: 'Cliente fictício em risco' }, ESPERA))
    expect((await screen.findByRole('alert', {}, ESPERA)).textContent).toContain('O nome parece um dado real')
    expect(screen.getByRole('form', { name: 'Cliente fictício em risco' })).toBeTruthy()
  })
})

describe('a barra "Mostrar: Simulação" no involuntário', () => {
  it('sem a barra, nada de simulado é pedido; com ela, a lista e os números pedem os simulados', async () => {
    const chamadas = ligarBackendFalso({
      'GET /ciclos': { ciclos: [CICLO_SIMULADO], proximo_cursor: null, tem_mais: false },
    })
    abrir('/involuntario')
    expect(await screen.findByText(/^Recuperado para você em /, {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), ESPERA)
    expect(chamadas.filter((c) => c.includes('incluir_simulados'))).toEqual([])

    const barra = screen.getByRole('radiogroup', { name: 'Modo dos dados' })
    fireEvent.click(within(barra).getByRole('radio', { name: 'Simulação' }))
    await waitFor(() => {
      const comSimulados = chamadas.filter((c) => c.includes('incluir_simulados=true'))
      expect(comSimulados.some((c) => c.startsWith('GET /ciclos?') && !c.includes('status='))).toBe(true)
      expect(comSimulados.some((c) => c.startsWith('GET /metrics/involuntario/mes?'))).toBe(true)
      expect(comSimulados.some((c) => c.startsWith('GET /metrics/involuntario/serie?'))).toBe(true)
    }, ESPERA)
    // O ciclo simulado aparece com o nome fictício e a marca.
    const linha = (await screen.findByText('Ana Souza', {}, ESPERA)).closest('tr') as HTMLElement
    expect(within(linha).getByText('Demonstração')).toBeTruthy()
  })
})

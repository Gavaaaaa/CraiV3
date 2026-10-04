// @vitest-environment jsdom
/**
 * As telas de pé em MODO REAL, contra o backend local com a semente rodada. Roda junto com o
 * teste da camada de dados em `npm run test:vivo`. Só lê: não escolhe mensagem nem grava
 * configuração, então pode rodar quantas vezes for preciso.
 *
 * O que se mede: o Involuntário e a Configuração mostram o que o backend respondeu; as
 * páginas e os blocos que ainda usam dado fictício mostram a etiqueta "Demonstração"; o
 * seletor de papel de desenvolvimento troca o que a pessoa pode fazer.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import App from './App'
import { MODO_REAL, trocarPapelDeDesenvolvimento } from './data/api'

beforeAll(() => {
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
})

afterEach(() => {
  cleanup()
  trocarPapelDeDesenvolvimento('owner')
})

function abrir(caminho: string) {
  window.history.pushState({}, '', caminho)
  return render(<App />)
}

const etiquetas = () => screen.queryAllByText('Demonstração', { exact: true })
const ESPERA = { timeout: 15_000 }
const mesAtual = new Intl.DateTimeFormat('pt-BR', { month: 'long' }).format(new Date())

describe('telas em modo real', () => {
  it('o modo real está ligado', () => {
    expect(MODO_REAL).toBe(true)
  })

  it('involuntário: a lista e os cartões vêm do backend, sem etiqueta de demonstração', async () => {
    abrir('/involuntario')
    expect(await screen.findByText(`Recuperado para você em ${mesAtual}`, {}, ESPERA)).toBeTruthy()
    // Os clientes da semente, com o nome lido da base; o ciclo sem cadastro aparece como tal.
    const fict = await screen.findAllByText(/\(fictíci[oa]\)$/, {}, ESPERA)
    expect(fict.length).toBeGreaterThanOrEqual(5)
    expect(screen.getAllByText('Cliente sem cadastro').length).toBeGreaterThan(0)
    // Os quatro status na tela.
    for (const status of ['Em análise', 'Em processo', 'Recuperado', 'Encerrado sem recuperação']) {
      expect(screen.getAllByText(status).length, status).toBeGreaterThan(0)
    }
    expect(screen.getByText('Ainda não informada pelo servidor')).toBeTruthy()
    expect(etiquetas()).toHaveLength(0)
    // Nenhum contato na tela.
    expect(document.body.textContent).not.toMatch(/@exemplo\.com\.br|\+55119/)
  })

  it('involuntário: o painel de um ciclo sem canal mostra as mensagens e a linha do tempo', async () => {
    abrir('/involuntario')
    const semCadastro = await screen.findAllByText('Cliente sem cadastro', {}, ESPERA)
    fireEvent.click(semCadastro[0])
    const painel = await screen.findByRole('dialog', {}, ESPERA)
    expect(await within(painel).findByText('Por que o sistema agiu assim', {}, ESPERA)).toBeTruthy()
    expect(within(painel).getByText('Linha do tempo')).toBeTruthy()
    expect(within(painel).getByText('Cobrança falhou')).toBeTruthy()
    expect(await within(painel).findByText('Mensagens sugeridas', {}, ESPERA)).toBeTruthy()
    expect(within(painel).getAllByText('Sem canal disponível').length).toBeGreaterThanOrEqual(3)
    expect(within(painel).getAllByText('O cliente desta cobrança não está na sua base').length).toBe(3)
    expect(within(painel).getByText(/^Envio automático/)).toBeTruthy()
    expect(within(painel).queryByText(/pts$/)).toBeNull()
    expect(painel.textContent).not.toContain('fee')
  })

  it('involuntário: ciclo recuperado mostra o líquido que o backend calculou', async () => {
    abrir('/involuntario')
    // Cada execução da semente cria um ciclo recuperado com este nome: basta o primeiro.
    fireEvent.click((await screen.findAllByText('Agência Norte (fictícia)', {}, ESPERA))[0])
    const painel = await screen.findByRole('dialog', {}, ESPERA)
    expect(await within(painel).findByText('Líquido para você', {}, ESPERA)).toBeTruthy()
    expect(within(painel).getByText('Pagamento recuperado')).toBeTruthy()
    expect(within(painel).getByText(/Cobrança de R\$\s3\.200,00, já descontada a taxa da CRAI\./)).toBeTruthy()
  })

  it('configuração: mensagens de verdade; as outras seções com a etiqueta', async () => {
    abrir('/configuracao')
    expect(await screen.findByText('Quem escolhe a mensagem', {}, ESPERA)).toBeTruthy()
    expect(screen.getByText('Salvar')).toBeTruthy()
    // O backend não aceita SMS no involuntário: a opção não é oferecida no modo real.
    expect(screen.queryByText('SMS')).toBeNull()
    expect(screen.queryByText('Ligar SMS')).toBeNull()
    // Cinco seções ainda são de demonstração: Empresa, Equipe, Integração, Dados e Notificações.
    expect(etiquetas()).toHaveLength(5)
  })

  it('o seletor de papel de desenvolvimento troca o que a pessoa pode fazer', async () => {
    abrir('/configuracao')
    expect(await screen.findByText('Salvar', {}, ESPERA)).toBeTruthy()
    const seletor = screen.getByLabelText('Papel do login de desenvolvimento') as HTMLSelectElement
    expect(seletor.value).toBe('owner')
    fireEvent.change(seletor, { target: { value: 'membro' } })
    expect(await screen.findByText(/Você tem o papel de membro/, {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(screen.queryByText('Salvar')).toBeNull(), ESPERA)
    expect(screen.getByText('Membro (só leitura)')).toBeTruthy()
  })

  it('visão geral, voluntário e assistente continuam em demonstração, com a etiqueta', async () => {
    abrir('/')
    expect(await screen.findByText('Olá, Empresa de demonstração', {}, ESPERA)).toBeTruthy()
    expect(await screen.findByText('Mantido para você nos últimos 30 dias', {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(etiquetas().length).toBeGreaterThanOrEqual(7), ESPERA)
    cleanup()
    abrir('/voluntario')
    expect(await screen.findByText('Mantido para você em setembro', {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(etiquetas().length).toBeGreaterThanOrEqual(6), ESPERA)
    cleanup()
    abrir('/assistente')
    await waitFor(() => expect(etiquetas().length).toBeGreaterThanOrEqual(1), ESPERA)
  })

  it('saúde do sistema: o relógio é o de verdade, e o resto está marcado', async () => {
    abrir('/?aba=saude')
    expect(await screen.findByText('Relógio das tentativas', {}, ESPERA)).toBeTruthy()
    expect(screen.getByText(/^Ativo\. /)).toBeTruthy()
    const item = (rotulo: string) => screen.getByText(rotulo).closest('li')!
    expect(within(item('Relógio das tentativas')).queryByText('Demonstração')).toBeNull()
    for (const rotulo of ['Decisões automáticas', 'Redator de mensagens', 'Base de clientes']) {
      expect(within(item(rotulo)).getByText('Demonstração'), rotulo).toBeTruthy()
    }
  })
})

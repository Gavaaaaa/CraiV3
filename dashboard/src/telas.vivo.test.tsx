// @vitest-environment jsdom
/**
 * As telas de pé em MODO REAL, contra o backend local com a semente rodada. Roda junto com o
 * teste da camada de dados em `npm run test:vivo`, na EMPRESA DOS TESTES (`demo_testes`):
 * nada daqui aparece na empresa da demonstração. Não escolhe mensagem nem grava configuração;
 * gera uma chave de API e a revoga em seguida.
 *
 * O que se mede: o Involuntário e a Configuração mostram o que o backend respondeu; o
 * seletor de papel de desenvolvimento troca o que a pessoa pode fazer.
 *
 * AS ETIQUETAS "DEMONSTRAÇÃO" (Rodada 2, Fase 3). Por padrão elas não aparecem mais, e este
 * arquivo confere isso. Para conferir que voltam como eram, rode com a variável ligada:
 *   PowerShell:  $env:VITE_CRAI_MOSTRAR_DEMONSTRACAO = "1"; npm run test:vivo
 * Com ela, os mesmos testes exigem as contagens da Etapa 4A.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it } from 'vitest'
import App from './App'
import { ENDERECO_DA_API, MODO_REAL, MOSTRAR_DEMONSTRACAO, trocarEmpresaDeDesenvolvimento, trocarPapelDeDesenvolvimento } from './data/api'
import { prepararJsdom } from './testes/modoRealFalso'

beforeAll(() => {
  // A empresa dos testes ao vivo, e não a da demonstração.
  trocarEmpresaDeDesenvolvimento('demo_testes')
  // O que o jsdom não tem e as telas usam (o gráfico de 30 dias mede a própria largura).
  prepararJsdom()
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

  it('configuração: mensagens de verdade; as outras seções, com a etiqueta só se a variável estiver ligada', async () => {
    abrir('/configuracao')
    expect(await screen.findByText('Quem escolhe a mensagem', {}, ESPERA)).toBeTruthy()
    expect(screen.getByText('Salvar')).toBeTruthy()
    // O backend não aceita SMS no involuntário: a opção não é oferecida no modo real.
    expect(screen.queryByText('SMS')).toBeNull()
    expect(screen.queryByText('Ligar SMS')).toBeNull()
    // Quatro seções ainda são de demonstração: Empresa, Equipe, Integração e Notificações
    // (Dados e privacidade passou a ser de verdade na Rodada 3, Fase 6).
    // A etiqueta delas só aparece com VITE_CRAI_MOSTRAR_DEMONSTRACAO=1.
    expect(etiquetas()).toHaveLength(MOSTRAR_DEMONSTRACAO ? 4 : 0)
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

  it('a visão geral, o voluntário e o assistente já são de verdade: nenhuma etiqueta, com ou sem a variável', async () => {
    abrir('/')
    expect(await screen.findByText('Olá, Empresa de demonstração', {}, ESPERA)).toBeTruthy()
    expect(await screen.findByText('Mantido para você nos últimos 30 dias', {}, ESPERA)).toBeTruthy()
    // Rodada 3, Fase 2: a visão geral lê o backend. Nenhuma etiqueta, com ou sem a variável.
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), ESPERA)
    expect(etiquetas()).toHaveLength(0)
    cleanup()
    // Rodada 3, Fase 1: o voluntário lê o backend. Nenhuma etiqueta, com ou sem a variável.
    abrir('/voluntario')
    expect(await screen.findByText(/^Mantido para você em /, {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(document.querySelector('[aria-busy="true"]')).toBeNull(), ESPERA)
    expect(etiquetas()).toHaveLength(0)
    cleanup()
    // Rodada 3, Fase 4: o assistente pergunta ao backend. Nenhuma etiqueta.
    abrir('/assistente')
    expect(await screen.findByText('O que o assistente vê', { exact: false }, ESPERA)).toBeTruthy()
    expect(etiquetas()).toHaveLength(0)
  })

  it('a Simulação do gateway mantém o selo dela, com ou sem a variável', async () => {
    abrir('/simulacao')
    const titulo = await screen.findByRole('heading', { name: 'Simulação do gateway' }, ESPERA)
    expect(titulo.parentElement?.textContent).toBe('Simulação do gatewayDemonstração')
    expect(screen.getByRole('link', { name: 'Simulação do gateway' }).textContent).toContain('Demo')
  })

  it('aba API: gera com um clique, a chave aparece no campo, e revoga com confirmação na tela', async () => {
    abrir('/api')
    const chaveDeApi = () => within(screen.getByRole('region', { name: 'Chave de API' }))
    const campos = () => chaveDeApi().queryAllByRole('textbox') as HTMLInputElement[]
    // O endereço mostrado é o do backend.
    expect(((await screen.findByRole('textbox', { name: 'Endereço da API' }, ESPERA)) as HTMLInputElement).value).toBe(ENDERECO_DA_API)
    // O exemplo vem fechado; aberto, traz o marcador, não uma chave.
    expect(document.querySelector('pre')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /^Exemplo de uso/ }))
    expect(document.querySelector('pre')?.textContent).toContain('Authorization: Bearer SUA_CHAVE_AQUI')
    expect(document.querySelector('pre')?.textContent).not.toMatch(/crai_live_/)

    // Gerar: um clique, sem nome. (Com ou sem chave ativa na empresa dos testes.)
    const gerar = await screen.findByRole('button', { name: /^Gerar (outra )?chave$/ }, ESPERA)
    const antes = campos().length
    fireEvent.click(gerar)
    const campo = (await chaveDeApi().findByRole('textbox', { name: 'Chave de API, inteira' }, ESPERA)) as HTMLInputElement
    const chave = campo.value
    expect(chave).toMatch(/^crai_live_[A-Za-z0-9_-]{43}$/)
    expect(screen.getByText('Copie agora. Por segurança, ela não aparece de novo.')).toBeTruthy()
    await waitFor(() => expect(campos()).toHaveLength(antes + 1), ESPERA)
    const linha = campo.closest('[data-testid="chave-ativa"]') as HTMLElement
    expect(within(linha).getByText(/Último uso: nunca$/)).toBeTruthy()
    expect(within(linha).getByRole('button', { name: 'Copiar' })).toBeTruthy()
    expect(window.localStorage.length + window.sessionStorage.length).toBe(0)
    expect(window.location.href).not.toContain('crai_live_')
    expect(document.querySelector('table')).toBeNull()

    // Revogar, com a confirmação dentro da tela: o campo some e a chave vai para as revogadas.
    fireEvent.click(within(linha).getByRole('button', { name: 'Revogar' }))
    fireEvent.click(within(linha).getByRole('button', { name: 'Confirmar' }))
    await waitFor(() => expect(campos()).toHaveLength(antes), ESPERA)
    expect(document.documentElement.outerHTML).not.toContain(chave.slice('crai_live_'.length))
    const revogadas = await screen.findByRole('button', { name: /^Ver chaves revogadas \(\d+\)$/ }, ESPERA)
    fireEvent.click(revogadas)
    expect(within(screen.getByRole('list', { name: 'Chaves revogadas' })).getAllByText(`${chave.slice(0, 14)}••••••${chave.slice(-4)}`).length).toBe(1)
    expect(etiquetas()).toHaveLength(0)
  })

  it('aba API: como membro, as chaves aparecem com o aviso e sem os botões', async () => {
    abrir('/api')
    expect(await screen.findByRole('button', { name: /^Gerar (outra )?chave$/ }, ESPERA)).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Papel do login de desenvolvimento'), { target: { value: 'membro' } })
    expect(await screen.findByText('Seu papel não permite gerar nem revogar chaves.', {}, ESPERA)).toBeTruthy()
    await waitFor(() => expect(screen.queryByRole('button', { name: /^Gerar/ })).toBeNull(), ESPERA)
    expect(screen.queryByRole('button', { name: 'Revogar' })).toBeNull()
  })

  it('a barra "Mostrar" só aparece no Involuntário, no Voluntário e na Visão geral', async () => {
    const barra = () => screen.queryByRole('radiogroup', { name: 'Modo dos dados' })
    abrir('/involuntario')
    expect(await screen.findByText(`Recuperado para você em ${mesAtual}`, {}, ESPERA)).toBeTruthy()
    expect(barra()).toBeTruthy()
    cleanup()
    abrir('/api')
    expect(await screen.findByRole('textbox', { name: 'Endereço da API' }, ESPERA)).toBeTruthy()
    expect(barra()).toBeNull()
    cleanup()
    abrir('/configuracao')
    expect(await screen.findByText('Quem escolhe a mensagem', {}, ESPERA)).toBeTruthy()
    expect(barra()).toBeNull()
  })

  it('saúde do sistema: as quatro linhas são de verdade, com ou sem a variável', async () => {
    abrir('/?aba=saude')
    expect(await screen.findByText('Relógio das tentativas', {}, ESPERA)).toBeTruthy()
    expect(screen.getByText(/^Ativo\. /)).toBeTruthy()
    const item = (rotulo: string) => screen.getByText(rotulo).closest('li')!
    // Rodada 3, Fase 2: o backend informa modelos, redator e base. Nenhuma linha é marcada.
    for (const rotulo of ['Relógio das tentativas', 'Decisões automáticas', 'Redator de mensagens', 'Base de clientes']) {
      expect(within(item(rotulo)).queryByText('Demonstração'), rotulo).toBeNull()
    }
    expect(item('Decisões automáticas').textContent).toMatch(/\d de 4 modelos carregados\./)
  })
})

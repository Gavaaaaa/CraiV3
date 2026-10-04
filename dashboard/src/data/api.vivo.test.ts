/**
 * Teste de integração AO VIVO: a própria camada `api.ts`, em modo real, contra o backend local.
 *
 * Fora do `npm test` comum. Para rodar:
 *   1. backend no ar com ENV=development (ver docs/interno/COMO_RODAR_DASHBOARD.md);
 *   2. semente da EMPRESA DOS TESTES: python docs/interno/semear_dashboard_demo.py --empresa demo_testes;
 *   3. npm run test:vivo
 *
 * A EMPRESA DOS TESTES (`demo_testes`). Estes testes geram chaves, escolhem mensagem e gravam
 * configuração. Para não sujar a empresa da demonstração (`demo_dashboard`, a que aparece ao
 * abrir o dashboard), eles usam uma segunda empresa fictícia, que só existe para isso.
 *
 * Cada execução ESCOLHE uma mensagem de um ciclo que aguardava escolha. Para rodar de novo,
 * rode a semente de novo (ela cria ciclos novos).
 *
 * LGPD: o backend só tem dado sintético da empresa fictícia; ainda assim o teste imprime só
 * contagens e status, nunca nome de cliente nem texto de mensagem.
 *
 * CHAVE DE API: o teste gera uma chave de verdade no backend local, usa e revoga. A chave
 * nunca é impressa: só o status de cada chamada.
 */
import { describe, expect, it } from 'vitest'
import {
  ENDERECO_DA_API,
  ErroApi,
  MODO_REAL,
  ROTAS_REAIS,
  api,
  emDemonstracao,
  trocarEmpresaDeDesenvolvimento,
  trocarPapelDeDesenvolvimento,
  trocarPlanoDeDesenvolvimento,
} from './api'
import type { CicloDetalhe, CicloResumo, StatusTela } from './tipos'

const COM_FUSO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$/

async function erroDe(promessa: Promise<unknown>): Promise<ErroApi> {
  try {
    await promessa
  } catch (e) {
    expect(e).toBeInstanceOf(ErroApi)
    return e as ErroApi
  }
  throw new Error('a chamada deveria ter falhado')
}

function temChave(valor: unknown, chave: string): boolean {
  if (Array.isArray(valor)) return valor.some((v) => temChave(v, chave))
  if (valor && typeof valor === 'object') return Object.entries(valor).some(([k, v]) => k === chave || temChave(v, chave))
  return false
}

/** O primeiro ciclo que espera escolha e tem canal para entregar. */
async function cicloAguardando(lista: CicloResumo[]): Promise<CicloDetalhe> {
  for (const c of lista.filter((x) => x.estado === 'aguardando_escolha')) {
    const d = await api.ciclo(c.id)
    if (d && d.modo_mensagem === 'escolha' && d.escolha_ate && !d.sugestoes.some((s) => s.escolhida) && d.sugestoes.some((s) => s.canal !== 'sem_canal')) {
      return d
    }
  }
  throw new Error('Nenhum ciclo aguardando escolha com canal. Rode a semente de novo: python docs/interno/semear_dashboard_demo.py --empresa demo_testes')
}

// Tudo o que este arquivo faz acontece na empresa dos testes, nunca na da demonstração.
trocarEmpresaDeDesenvolvimento('demo_testes')

// Os testes de um arquivo rodam em ordem: cada passo usa o que o anterior deixou.
describe('dashboard em modo real contra o backend local', () => {
  let lista: CicloResumo[] = []
  let alvo: CicloDetalhe

  it('o modo real está ligado e o backend responde', async () => {
    expect(MODO_REAL).toBe(true)
    expect(emDemonstracao('ciclos', 'ciclo', 'metricasMes')).toBe(false)
    expect(emDemonstracao('resumoVisaoGeral')).toBe(true)
    const saude = await api.saude()
    expect(typeof saude.relogio.ativo).toBe('boolean')
    expect(saude.demonstracao).toEqual(['modelos', 'redator', 'base'])
    console.log(`[vivo] relógio ativo: ${saude.relogio.ativo}`)
  })

  it('pede o token de desenvolvimento e lista os ciclos da empresa fictícia', async () => {
    trocarPapelDeDesenvolvimento('owner')
    const empresa = await api.empresa()
    expect(empresa.papel).toBe('owner')
    lista = await api.ciclos()
    expect(lista.length).toBeGreaterThanOrEqual(4)
    const porStatus: Record<StatusTela, number> = { em_analise: 0, em_processo: 0, recuperado: 0, encerrado: 0 }
    for (const c of lista) {
      porStatus[c.status] += 1
      expect(c.aberto_em).toMatch(COM_FUSO)
      expect(c.atualizado_em).toMatch(COM_FUSO)
      expect(typeof c.valor_cobranca).toBe('number')
      expect(c.valor_liquido === null).toBe(c.status !== 'recuperado')
    }
    console.log(`[vivo] ${lista.length} ciclos: ${JSON.stringify(porStatus)}`)
    // A semente cria pelo menos um ciclo em cada um dos quatro status.
    for (const status of Object.keys(porStatus) as StatusTela[]) expect(porStatus[status], status).toBeGreaterThan(0)
    expect(temChave(lista, 'fee')).toBe(false)
    expect(lista.some((c) => c.cliente !== null)).toBe(true)
    expect(lista.some((c) => c.estado === 'aguardando_escolha')).toBe(true)
  })

  it('o filtro por status chega ao backend', async () => {
    const recuperados = await api.ciclos({ status: 'recuperado' })
    expect(recuperados.length).toBeGreaterThan(0)
    expect(recuperados.every((c) => c.status === 'recuperado' && c.valor_liquido !== null && c.valor_liquido < c.valor_cobranca)).toBe(true)
    const encerrados = await api.ciclos({ status: 'encerrado' })
    expect(encerrados.length).toBeGreaterThan(0)
    expect(encerrados.every((c) => c.status === 'encerrado')).toBe(true)
  })

  it('abre o detalhe de cada ciclo e o adaptador converte sem erro', async () => {
    let eventos = 0
    for (const c of lista) {
      const d = await api.ciclo(c.id)
      expect(d).not.toBeNull()
      expect(d!.id).toBe(c.id)
      expect(d!.linha_do_tempo.length).toBeGreaterThan(0)
      expect(d!.linha_do_tempo.filter((e) => e.tipo === 'abertura')).toHaveLength(1)
      for (const e of d!.linha_do_tempo) {
        expect(e.em).toMatch(COM_FUSO)
        expect(e.titulo.length).toBeGreaterThan(0)
        expect(e.titulo).not.toBe('Evento registrado')
      }
      expect(temChave(d, 'fee')).toBe(false)
      expect(temChave(d, 'pontos')).toBe(false)
      eventos += d!.linha_do_tempo.length
    }
    console.log(`[vivo] ${lista.length} detalhes abertos, ${eventos} eventos de linha do tempo convertidos`)
  })

  it('ciclo que não existe dá erro em português, não tela branca', async () => {
    const erro = await erroDe(api.ciclo(987654321))
    expect([erro.codigo, erro.status]).toEqual(['nao_encontrado', 404])
  })

  it('lê e grava a configuração de verdade', async () => {
    const antes = await api.configuracao()
    expect(antes.modo_mensagem_involuntario).toBe('escolha')
    const novoPrazo = antes.prazo_escolha_horas === 12 ? 24 : 12
    const gravada = await api.salvarConfiguracao({ ...antes, prazo_escolha_horas: novoPrazo })
    expect(gravada.prazo_escolha_horas).toBe(novoPrazo)
    expect((await api.configuracao()).prazo_escolha_horas).toBe(novoPrazo)
    // Volta ao que estava, para a tela do Crai abrir com o que a semente deixou.
    const restaurada = await api.salvarConfiguracao({ ...gravada, prazo_escolha_horas: antes.prazo_escolha_horas })
    expect(restaurada).toEqual(antes)
    console.log(`[vivo] configuração: prazo ${antes.prazo_escolha_horas} h -> ${novoPrazo} h -> ${restaurada.prazo_escolha_horas} h`)
  })

  it('métricas do mês e série de 30 dias', async () => {
    const [mes, serie] = await Promise.all([api.metricasMes(), api.serie()])
    expect(mes.mes).toMatch(/^\d{4}-\d{2}$/)
    expect(mes.recuperados).toBeGreaterThan(0)
    expect(mes.valor_liquido_recuperado).toBeGreaterThan(0)
    expect(mes.aguardando_escolha).toBeGreaterThan(0)
    expect(mes.ciclos_ativos).toBe(lista.filter((c) => c.status === 'em_analise' || c.status === 'em_processo').length)
    expect(serie).toHaveLength(30)
    // A série cobre 30 dias e pode pegar o fim do mês anterior: nunca soma menos que o mês.
    expect(serie.reduce((t, p) => t + p.valor, 0)).toBeGreaterThanOrEqual(mes.valor_liquido_recuperado - 0.01)
    console.log(`[vivo] mês ${mes.mes}: ${mes.recuperados} recuperado(s), ${mes.aguardando_escolha} aguardando escolha, ${mes.ciclos_ativos} ativo(s)`)
  })

  it('como membro: escolher, regerar e gravar a configuração dão 403', async () => {
    alvo = await cicloAguardando(lista)
    const sugestao = alvo.sugestoes[0]
    trocarPapelDeDesenvolvimento('membro')
    expect((await api.empresa()).papel).toBe('membro')
    // Membro lê...
    expect((await api.ciclo(alvo.id))?.id).toBe(alvo.id)
    // ...mas não escolhe, não regera e não grava.
    const escolher = await erroDe(api.escolherMensagem(alvo.id, sugestao.rodada, sugestao.abordagem))
    expect([escolher.codigo, escolher.status, escolher.message]).toEqual(['sem_permissao', 403, 'Seu papel não permite esta ação.'])
    expect((await erroDe(api.regerarSugestoes(alvo.id))).status).toBe(403)
    const config = await api.configuracao()
    expect((await erroDe(api.salvarConfiguracao({ ...config, prazo_escolha_horas: config.prazo_escolha_horas === 4 ? 8 : 4 }))).status).toBe(403)
    // Nada mudou no ciclo.
    const depois = await api.ciclo(alvo.id)
    expect(depois?.estado).toBe('aguardando_escolha')
    expect(depois?.sugestoes.some((s) => s.escolhida)).toBe(false)
    console.log('[vivo] membro: 403 em escolher, regerar e gravar configuração')
    trocarPapelDeDesenvolvimento('owner')
  })

  it('como dono: regera (outra rodada, mesmo prazo) e escolhe uma mensagem', async () => {
    const rodadaAntes = Math.max(...alvo.sugestoes.map((s) => s.rodada))
    const novas = await api.regerarSugestoes(alvo.id)
    expect(novas).toHaveLength(3)
    expect(novas.every((s) => s.rodada === rodadaAntes + 1)).toBe(true)
    expect(novas.filter((s) => s.recomendada)).toHaveLength(1)
    expect(novas.map((s) => s.abordagem).sort()).toEqual(['facilitacao', 'lembrete_cordial', 'urgencia_com_respeito'])

    const comDuasRodadas = await api.ciclo(alvo.id)
    expect(comDuasRodadas?.sugestoes).toHaveLength(alvo.sugestoes.length + 3)
    expect(comDuasRodadas?.escolha_ate).toBe(alvo.escolha_ate) // regerar não reinicia o prazo

    const escolhida = novas.find((s) => s.abordagem === 'urgencia_com_respeito')!
    const resposta = await api.escolherMensagem(alvo.id, escolhida.rodada, escolhida.abordagem)
    expect(typeof resposta.enviada).toBe('boolean')

    const depois = await api.ciclo(alvo.id)
    expect(depois?.escolha_por).toBe('owner')
    expect(depois?.escolha_ate).toBeNull()
    expect(depois?.sugestoes.filter((s) => s.escolhida).map((s) => [s.rodada, s.abordagem])).toEqual([[escolhida.rodada, 'urgencia_com_respeito']])
    expect(depois?.linha_do_tempo.some((e) => e.tipo === 'escolha')).toBe(true)
    if (resposta.enviada) {
      expect(depois?.estado).toBe('mensagem_enviada')
      expect(depois?.linha_do_tempo.some((e) => e.titulo === 'Mensagem enviada')).toBe(true)
    } else {
      // Fora da janela de contato: a escolha fica gravada e o relógio envia quando a janela abrir.
      expect(resposta.espera).toBe('fora_da_janela')
      expect(depois?.estado).toBe('aguardando_escolha')
    }
    console.log(`[vivo] dono: regerou (rodada ${rodadaAntes + 1}) e escolheu; enviada=${resposta.enviada}${resposta.espera ? `, espera=${resposta.espera}` : ''}`)
  })

  it('escolher de novo o mesmo ciclo dá conflito, com mensagem em português', async () => {
    const erro = await erroDe(api.escolherMensagem(alvo.id, 1, 'lembrete_cordial'))
    expect([erro.codigo, erro.status]).toEqual(['conflito', 409])
    expect(erro.message).toMatch(/^Este ciclo não está mais esperando uma escolha/)
    expect((await erroDe(api.regerarSugestoes(alvo.id))).status).toBe(409)
  })

  const NOME_DA_CHAVE = 'Teste ao vivo do dashboard'
  const CLIENTE_DO_TESTE = 'vivo-chave-api'

  it('chave de API: gera pela camada, usa num POST /clientes, revoga, e a chamada seguinte dá 401', async () => {
    trocarPapelDeDesenvolvimento('owner')
    trocarPlanoDeDesenvolvimento('premium')
    expect(emDemonstracao('chaves', 'criarChave', 'revogarChave')).toBe(false)

    // Sobra de uma execução interrompida: revoga as chaves ativas com o nome deste teste.
    const antes = await api.chaves()
    for (const c of antes.chaves.filter((x) => x.nome === NOME_DA_CHAVE && !x.revogada_em)) await api.revogarChave(c.id)
    expect(antes.pode_gerar).toBe(true)
    expect(antes.limite_ativas).toBe(5)

    const { chave, inteira } = await api.criarChave(NOME_DA_CHAVE)
    expect(inteira).toMatch(/^crai_live_[A-Za-z0-9_-]{43}$/)
    expect([chave.nome, chave.inicio, chave.final]).toEqual([NOME_DA_CHAVE, inteira.slice(0, 14), inteira.slice(-4)])
    expect(chave.criada_em).toMatch(COM_FUSO)
    expect([chave.ultimo_uso, chave.revogada_em]).toEqual([null, null])
    // A lista traz a chave nova, e nunca a chave inteira.
    const comANova = await api.chaves()
    expect(comANova.chaves.find((c) => c.id === chave.id)?.inicio).toBe(chave.inicio)
    expect(JSON.stringify(comANova)).not.toContain(inteira.slice('crai_live_'.length))

    // O "sistema da empresa": chama a API só com a chave, sem login.
    const usar = (metodo: string, caminho: string, corpo?: unknown) =>
      fetch(`${ENDERECO_DA_API}${caminho}`, {
        method: metodo,
        headers: { Authorization: `Bearer ${inteira}`, 'Content-Type': 'application/json' },
        body: corpo === undefined ? undefined : JSON.stringify(corpo),
      })
    const cliente = { customer_id_externo: CLIENTE_DO_TESTE, mrr: 100, billing_profile: 'PJ' }
    const criado = await usar('POST', '/clientes', cliente)
    expect(criado.status).toBe(200)
    expect(((await criado.json()) as { cliente: { customer_id_externo: string } }).cliente.customer_id_externo).toBe(CLIENTE_DO_TESTE)
    const atualizado = await usar('PATCH', `/clientes/${CLIENTE_DO_TESTE}`, { mrr: 120 })
    expect(atualizado.status).toBe(200)
    // A chave não abre o painel nem as próprias rotas de chave.
    expect((await usar('GET', '/ciclos')).status).toBe(401)
    expect((await usar('GET', '/integracao/chaves')).status).toBe(401)
    // O uso ficou registrado.
    const usada = (await api.chaves()).chaves.find((c) => c.id === chave.id)
    expect(usada?.ultimo_uso).toMatch(COM_FUSO)
    // O cliente do teste sai da base ativa (cancelado), ainda com a chave.
    expect((await usar('DELETE', `/clientes/${CLIENTE_DO_TESTE}`)).status).toBe(200)

    const revogada = await api.revogarChave(chave.id)
    expect(revogada.revogada_em).toMatch(COM_FUSO)
    const depois = await usar('POST', '/clientes', cliente)
    expect(depois.status).toBe(401)
    expect(((await depois.json()) as { detail: { motivo: string } }).detail.motivo).toBe('chave_invalida')
    // Revogar de novo: a mesma data.
    expect((await api.revogarChave(chave.id)).revogada_em).toBe(revogada.revogada_em)
    console.log(`[vivo] chave de API: criada, POST /clientes ${criado.status}, PATCH ${atualizado.status}, revogada, POST seguinte ${depois.status}`)
  })

  it('chave de API: como membro, 403 ao gerar e ao revogar', async () => {
    trocarPapelDeDesenvolvimento('owner')
    trocarPlanoDeDesenvolvimento('premium')
    const { chave } = await api.criarChave(NOME_DA_CHAVE)

    trocarPapelDeDesenvolvimento('membro')
    const lista = await api.chaves() // membro vê a lista
    expect([lista.pode_gerar, lista.pode_revogar, lista.plano_permite_gerar]).toEqual([false, false, true])
    const gerar = await erroDe(api.criarChave(NOME_DA_CHAVE))
    expect([gerar.codigo, gerar.status, gerar.motivo, gerar.message]).toEqual(['sem_permissao', 403, 'papel_insuficiente', 'Seu papel não permite esta ação.'])
    const revogar = await erroDe(api.revogarChave(chave.id))
    expect([revogar.status, revogar.motivo]).toEqual([403, 'papel_insuficiente'])

    trocarPapelDeDesenvolvimento('owner')
    expect((await api.chaves()).chaves.find((c) => c.id === chave.id)?.revogada_em).toBeNull()
    await api.revogarChave(chave.id)
    console.log(`[vivo] chave de API: membro ${gerar.status} ao gerar e ${revogar.status} ao revogar`)
  })

  it('chave de API: no plano essencial, lista e revoga; só gerar dá 403', async () => {
    trocarPapelDeDesenvolvimento('owner')
    trocarPlanoDeDesenvolvimento('premium')
    const { chave } = await api.criarChave(NOME_DA_CHAVE)

    trocarPlanoDeDesenvolvimento('essencial')
    expect((await api.empresa()).plano).toBe('essencial')
    // Lista: 200, e a resposta diz que o plano não gera, mas o dono revoga.
    const lista = await api.chaves()
    expect(lista.chaves.some((c) => c.id === chave.id)).toBe(true)
    expect([lista.plano_permite_gerar, lista.pode_gerar, lista.pode_revogar]).toEqual([false, false, true])
    // Gerar: 403, com a frase do plano.
    const gerar = await erroDe(api.criarChave(NOME_DA_CHAVE))
    expect([gerar.codigo, gerar.status, gerar.motivo, gerar.message]).toEqual(['sem_permissao', 403, 'plano_sem_api', 'Gerar chave faz parte do plano Premium.'])
    // Revogar: funciona.
    const revogada = await api.revogarChave(chave.id)
    expect(revogada.revogada_em).toMatch(COM_FUSO)
    // O resto do painel continua funcionando no essencial.
    expect((await api.configuracao()).prazo_escolha_horas).toBeGreaterThan(0)

    trocarPlanoDeDesenvolvimento('premium')
    expect((await api.empresa()).plano).toBe('premium')
    console.log(`[vivo] chave de API no essencial: lista 200, gerar ${gerar.status} (${gerar.motivo}), revogar 200`)
  })

  it('nada do que os testes fizeram aparece na empresa da demonstração', async () => {
    const dosTestes = await api.chaves()
    expect(dosTestes.chaves.some((c) => c.nome === NOME_DA_CHAVE)).toBe(true)
    const idsDosTestes = new Set(dosTestes.chaves.map((c) => c.id))
    const ciclosDosTestes = new Set((await api.ciclos()).map((c) => c.id))

    trocarEmpresaDeDesenvolvimento('demo_dashboard')
    try {
      const daDemonstracao = await api.chaves()
      expect(daDemonstracao.chaves.some((c) => idsDosTestes.has(c.id))).toBe(false)
      expect(daDemonstracao.chaves.some((c) => c.nome === NOME_DA_CHAVE || c.nome.startsWith('Tela ao vivo'))).toBe(false)
      expect((await api.ciclos()).some((c) => ciclosDosTestes.has(c.id))).toBe(false)
      console.log(`[vivo] empresa da demonstração: ${daDemonstracao.chaves.length} chave(s), nenhuma dos testes`)
    } finally {
      trocarEmpresaDeDesenvolvimento('demo_testes')
    }
  })

  it('o mapa de rotas reais é o desta etapa', () => {
    expect(Object.keys(ROTAS_REAIS)).toHaveLength(12)
  })
})

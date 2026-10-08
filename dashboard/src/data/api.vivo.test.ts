/**
 * Teste de integração AO VIVO: a própria camada `api.ts`, em modo real, contra o backend local.
 *
 * Fora do `npm test` comum. Para rodar:
 *   1. backend no ar com ENV=development (ver o README.md da raiz, seção Como rodar);
 *   2. semente da EMPRESA DOS TESTES: python -m crai.scripts.semear_demo --empresa demo_testes (a partir de app/);
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
  throw new Error('Nenhum ciclo aguardando escolha com canal. Rode a semente de novo: python -m crai.scripts.semear_demo --empresa demo_testes (a partir de app/)')
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
    // Rodada 3, Fase 2: a visão geral é de verdade; a seção Equipe continua fictícia.
    expect(emDemonstracao('resumoVisaoGeral')).toBe(false)
    expect(emDemonstracao('membros')).toBe(true)
    const saude = await api.saude()
    expect(typeof saude.relogio.ativo).toBe('boolean')
    // O backend informa as quatro linhas: nenhuma fica de demonstração.
    expect(saude.demonstracao).toBeUndefined()
    expect(saude.modelos.total).toBe(4)
    expect(saude.modelos.carregados).toBeLessThanOrEqual(4)
    expect(typeof saude.redator.disponivel).toBe('boolean')
    console.log(`[vivo] relógio ativo: ${saude.relogio.ativo}; modelos ${saude.modelos.carregados} de ${saude.modelos.total}; redator ${saude.redator.disponivel}`)
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

  it('voluntário: a base, os clientes recentes, o mês, a série e a comparação vêm do backend', async () => {
    trocarPapelDeDesenvolvimento('owner')
    trocarPlanoDeDesenvolvimento('premium')
    const { chave, inteira } = await api.criarChave(NOME_DA_CHAVE)
    const usar = (metodo: string, caminho: string, corpo?: unknown) =>
      fetch(`${ENDERECO_DA_API}${caminho}`, {
        method: metodo,
        headers: { Authorization: `Bearer ${inteira}`, 'Content-Type': 'application/json' },
        body: corpo === undefined ? undefined : JSON.stringify(corpo),
      })
    const sufixo = Date.now().toString(36)
    const ids = [`vol-vivo-${sufixo}-a`, `vol-vivo-${sufixo}-b`, `vol-vivo-${sufixo}-c`]
    try {
      const lote = await usar('POST', '/clientes/lote', {
        clientes: [
          { customer_id_externo: ids[0], mrr: 410, billing_profile: 'PJ', days_since_last: 62, features_used_30d: 0, nome: 'Cliente de teste A' },
          { customer_id_externo: ids[1], mrr: 220, billing_profile: 'CLT', days_since_last: 0, features_used_30d: 9 },
          { customer_id_externo: ids[2], mrr: 150, billing_profile: 'PJ' },
        ],
      })
      expect(lote.status).toBe(200)

      const base = await api.baseClientes()
      expect(base).not.toBeNull()
      expect(base!.origem).toBe('api')
      expect(base!.total).toBeGreaterThanOrEqual(3)
      expect(base!.com_dados_comportamento).toBeLessThanOrEqual(base!.total)
      expect(base!.decididos_pelo_modelo).toBeLessThanOrEqual(base!.total)
      expect(base!.atualizada_em).toMatch(COM_FUSO)

      const recentes = await api.clientesRecentes({ limite: 10 })
      const porId = new Map(recentes.map((c) => [c.id, c]))
      for (const id of ids) expect(porId.has(id), id).toBe(true)
      expect(porId.get(ids[0])!.nome).toBe('Cliente de teste A')
      expect(porId.get(ids[1])!.nome).toBe(ids[1]) // sem nome na base: o id
      expect(porId.get(ids[2])!.faixa).toBe('sem_dado')
      expect(porId.get(ids[2])!.risco_decidido_por).toBeNull()
      expect(['grave', 'preocupante', 'sem_risco']).toContain(porId.get(ids[0])!.faixa)
      expect(porId.get(ids[0])!.motivo.length).toBeGreaterThan(0)
      for (const c of recentes) {
        expect(c.atualizado_em === null || COM_FUSO.test(c.atualizado_em)).toBe(true)
        expect(c.motivo.charAt(0)).toBe(c.motivo.charAt(0).toUpperCase())
      }
      for (const proibida of ['fee', 'email', 'telefone', 'cpf', 'chave_pix']) expect(temChave(recentes, proibida), proibida).toBe(false)

      const mes = await api.resumoVoluntario()
      expect(mes.mes).toMatch(/^\d{4}-\d{2}$/)
      expect(mes.meses_de_mrr).toBeGreaterThanOrEqual(1)
      expect(mes.prazo_estorno_dias).toBeGreaterThanOrEqual(1)
      expect(mes.ofertas_aceitas).toBeLessThanOrEqual(Math.max(mes.ofertas_aceitas, mes.ofertas_enviadas))
      expect(temChave(mes, 'fee')).toBe(false)

      const serie = await api.serieVoluntario()
      expect(serie).toHaveLength(30)
      expect(serie.every((p) => /^\d{4}-\d{2}-\d{2}$/.test(p.dia) && typeof p.valor === 'number')).toBe(true)

      // Sem cancelamentos suficientes (ou sem modelo ativo), a comparação vem vazia: null.
      const comparacao = await api.comparacaoReguaModelo()
      if (comparacao !== null) expect(comparacao.cancelamentos).toBeGreaterThanOrEqual(5)

      // O anexo, de verdade: sobe um CSV e a origem da base passa a ser "anexo".
      const csv = `customer_id_externo,mrr,billing_profile\n${ids[2]},175,PJ\nlinha-ruim,,PJ\n`
      const importado = await api.importarBase(new File([csv], 'base-do-teste.csv', { type: 'text/csv' }))
      expect([importado.importados, importado.rejeitados, importado.demonstracao]).toEqual([1, 1, false])
      expect([importado.novos, importado.atualizados, importado.sem_id_recorrencia]).toEqual([null, null, null])
      expect((await api.baseClientes())!.origem).toBe('anexo')
      const recusado = await erroDe(api.importarBase(new File(['x'], 'base.txt', { type: 'text/plain' })))
      expect([recusado.status, recusado.message]).toEqual([415, 'Só CSV ou XLSX. Outros formatos não são lidos.'])

      console.log(`[vivo] voluntário: base ${base!.total} clientes (origem api, depois anexo), ${recentes.length} recentes, comparação ${comparacao === null ? 'vazia' : 'presente'}`)
    } finally {
      // Os clientes do teste saem da base ativa, e a chave é revogada.
      for (const id of ids) await usar('DELETE', `/clientes/${id}`)
      await api.revogarChave(chave.id)
    }
    const depois = await api.clientesRecentes({ limite: 10 })
    for (const id of ids) expect(depois.some((c) => c.id === id)).toBe(false)
  })

  it('visão geral: os cartões, a série, o funil, o que funciona, a atividade e o extrato vêm do backend', async () => {
    trocarPapelDeDesenvolvimento('owner')
    trocarPlanoDeDesenvolvimento('premium')
    const [resumo, serie, funil, funciona, atividade, extrato, mesInv] = await Promise.all([
      api.resumoVisaoGeral(),
      api.serieDupla(),
      api.funil(),
      api.oQueFunciona(),
      api.atividade({ limite: 50 }),
      api.extrato(),
      api.metricasMes(),
    ])
    // A semente recupera uma cobrança: os cartões e o extrato contam a mesma história.
    expect(resumo.cobrancas_recuperadas).toBeGreaterThanOrEqual(1)
    expect(resumo.recuperado_involuntario).toBeGreaterThan(0)
    expect(resumo.retido_voluntario).not.toBeNull()
    expect(resumo.ciclos_ativos).toBeGreaterThanOrEqual(1)
    expect(resumo.taxa_recuperacao).not.toBeNull()
    expect(resumo.periodo.ate >= resumo.periodo.de).toBe(true)
    expect(serie).toHaveLength(30)
    const somaDaSerie = serie.reduce((t, p) => t + p.involuntario, 0)
    expect(Math.abs(somaDaSerie - resumo.recuperado_involuntario)).toBeLessThan(0.01)

    expect(funil.etapas.map((e) => e.etapa)).toEqual(['falhas', 'tentativa_1', 'tentativa_2', 'tentativa_3', 'mensagem'])
    expect(funil.etapas[0].chegaram).toBeGreaterThanOrEqual(funil.desfecho.recuperados)
    expect(funil.mes).toBe(mesInv.mes)
    expect(funciona.causas.length).toBeGreaterThanOrEqual(1)
    for (const c of funciona.causas) expect(c.taxa >= 0 && c.taxa <= 1).toBe(true)

    expect(atividade.length).toBeGreaterThanOrEqual(3)
    expect(atividade.some((a) => a.tipo === 'recuperado' && (a.valor ?? 0) > 0)).toBe(true)
    for (const a of atividade) {
      expect(a.em).toMatch(COM_FUSO)
      expect(a.texto.charAt(0)).toBe(a.texto.charAt(0).toUpperCase())
      expect(a.simulado).toBe(false)
    }
    // Nenhuma rota da página traz a taxa, fora o extrato.
    for (const semTaxa of [resumo, serie, funil, atividade]) expect(temChave(semTaxa, 'fee')).toBe(false)

    const recuperacoes = extrato.filter((l) => l.tipo === 'recuperacao')
    expect(recuperacoes.length).toBeGreaterThanOrEqual(1)
    for (const l of extrato) {
      expect(Math.abs(l.valor_base - l.taxa - l.liquido)).toBeLessThan(0.011)
      expect(l.data).toMatch(COM_FUSO)
    }
    expect(recuperacoes.every((l) => l.taxa > 0 && l.liquido < l.valor_base)).toBe(true)

    // O extrato é só do dono e do administrador: para o membro, erro de permissão em português.
    trocarPapelDeDesenvolvimento('membro')
    const negado = await erroDe(api.extrato())
    expect([negado.codigo, negado.status, negado.message]).toEqual(['sem_permissao', 403, 'Seu papel não permite esta ação.'])
    expect((await api.resumoVisaoGeral()).cobrancas_recuperadas).toBe(resumo.cobrancas_recuperadas)
    trocarPapelDeDesenvolvimento('owner')

    // Fora do plano premium, o voluntário vem vazio (null), não zero.
    trocarPlanoDeDesenvolvimento('essencial')
    const essencial = await api.resumoVisaoGeral()
    expect([essencial.retido_voluntario, essencial.clientes_risco_grave]).toEqual([null, null])
    expect(essencial.recuperado_involuntario).toBe(resumo.recuperado_involuntario)
    trocarPlanoDeDesenvolvimento('premium')
    console.log(`[vivo] visão geral: ${resumo.cobrancas_recuperadas} recuperada(s), ${atividade.length} eventos, ${extrato.length} linha(s) de extrato; membro ${negado.status} no extrato`)
  })

  it('saúde do sistema: com o token, o backend diz também a base da empresa', async () => {
    const saude = await api.saude()
    expect(saude.demonstracao).toBeUndefined()
    // A semente cadastrou clientes pela API: a base existe, com a origem registrada.
    expect(saude.base).not.toBeNull()
    expect(saude.base!.atualizada_em).toMatch(COM_FUSO)
    expect(['api', 'anexo']).toContain(saude.base!.origem)
    // Sem token o /health continua público, e não fala de empresa nenhuma.
    const publico = (await (await fetch(`${ENDERECO_DA_API}/health`)).json()) as Record<string, unknown>
    expect(publico.status).toBe('ok')
    expect('base' in publico).toBe(false)
  })

  it('simulação do gateway: o ciclo inteiro pelas rotas, e nada do simulado entra nos números reais', async () => {
    await api.simulacaoLimpar()
    const antes = await api.resumoVisaoGeral()
    const reaisAntes = await api.ciclos({})
    expect((await api.simulacao()).fase).toBe('formulario')

    // O dinheiro só entra no 8º dia: depois da janela de 7 dias das tentativas. Com saldo, paga sempre.
    let e = await api.simularCobranca({ nome: 'Ana Souza', mensalidade: 300, perfil: 'clt', verdade: { dias_ate_saldo: 8, chance_pagar: 1, vai_revogar: false } })
    expect(e.fase).toBe('recusada')
    expect(e.causa).toBe('insufficient_funds')
    expect(e.id_ciclo).toBeGreaterThan(9_000_000_000_000)
    expect(e.tentativas.map((t) => t.resultado)).toEqual(['agendada', 'agendada', 'agendada'])
    expect(e.chance_recuperar).toBeGreaterThan(0)
    expect(e.contribuicoes.length).toBeGreaterThan(0)
    expect(e.pensando.length).toBeGreaterThanOrEqual(3)
    expect(e.sem_crai?.resultado).toBe('perdido')
    expect(e.hoje).toMatch(COM_FUSO)
    const inicio = e.hoje

    // O relógio simulado anda; as três tentativas falham (ainda não há saldo) e nascem as mensagens.
    const fases: string[] = [e.fase]
    let escolheu = false
    for (let passo = 0; passo < 12 && e.fase !== 'recuperada' && e.fase !== 'encerrada'; passo++) {
      if (e.fase === 'mensagens' && e.modo_mensagem === 'escolha' && !escolheu) {
        expect(e.tentativas.map((t) => t.resultado)).toEqual(['falhou', 'falhou', 'falhou'])
        expect(e.sugestoes).toHaveLength(3)
        for (const s of e.sugestoes) expect(s.texto.length).toBeGreaterThan(20)
        // A escolha vai pela rota de sempre do ciclo, com o id do ciclo simulado.
        e = await api.simulacaoEscolherMensagem('facilitacao')
        escolheu = true
      } else {
        e = await api.simulacaoAvancarAteProximaAcao()
      }
      fases.push(e.fase)
    }
    expect(e.fase, fases.join(' > ')).toBe('recuperada')
    expect(e.desfecho).toMatchObject({ tipo: 'recuperado', via: 'mensagem', tentativa: null })
    expect(e.desfecho!.valor_liquido).toBeGreaterThan(0)
    expect(e.desfecho!.valor_liquido).toBeLessThan(300)
    expect(e.mensagem_enviada).not.toBeNull()
    expect(new Date(e.hoje).getTime()).toBeGreaterThan(new Date(inicio).getTime() + 7 * 86_400_000)
    expect(temChave(e, 'fee')).toBe(false)

    // Nada do simulado entrou no que é real...
    const depois = await api.resumoVisaoGeral()
    expect([depois.cobrancas_recuperadas, depois.recuperado_involuntario, depois.ciclos_ativos]).toEqual([antes.cobrancas_recuperadas, antes.recuperado_involuntario, antes.ciclos_ativos])
    const reaisDepois = await api.ciclos({})
    expect(reaisDepois.map((c) => c.id).sort()).toEqual(reaisAntes.map((c) => c.id).sort())
    expect(reaisDepois.some((c) => c.simulado)).toBe(false)
    // ...e com a barra "Mostrar: Simulação" ele aparece, marcado.
    const comSimulados = await api.ciclos({ incluirSimulados: true })
    const simulados = comSimulados.filter((c) => c.simulado)
    expect(simulados).toHaveLength(1)
    expect(simulados[0]).toMatchObject({ id: e.id_ciclo, cliente: 'Ana Souza', status: 'recuperado' })
    expect(comSimulados).toHaveLength(reaisAntes.length + 1)
    const comSim = await api.resumoVisaoGeral({ incluirSimulados: true })
    expect(comSim.cobrancas_recuperadas).toBe(antes.cobrancas_recuperadas + 1)
    expect(Math.abs(comSim.recuperado_involuntario - antes.recuperado_involuntario - e.desfecho!.valor_liquido)).toBeLessThan(0.011)
    const detalhe = await api.ciclo(e.id_ciclo!)
    expect(detalhe?.simulado).toBe(true)

    // O cliente fictício em risco: a oferta é do sistema; o aceite, da propensão escondida.
    const aceitaTudo = { desconto_10: 1, desconto_20: 1, pausa_1_mes: 1, pix_boleto_flash: 1 }
    // Quem abre a página de cancelamento, sem dado de uso: a régua dá risco alto, e há oferta.
    const retencao = await api.simularRetencao({
      nome: 'Loja Ponto Certo',
      mrr: 1200,
      sinais: { abriu_cancelamento: true, uso_caiu: false, tickets: false, atraso: false },
      propensao: aceitaTudo,
    })
    expect([retencao.faixa, retencao.decidido_por, retencao.risco]).toEqual(['grave', 'regua', 0.9])
    expect(retencao.oferta).not.toBeNull()
    expect(retencao.aceitou).toBe(true)
    // Com dado de uso, decide o modelo de IA (se estiver ativo) ou a régua, e a regra da oferta
    // depende de quem decidiu. Régua: só há oferta com o risco no corte de intervenção ou acima
    // dele. Modelo: o corte fixo não vale; sem evento de intenção explícita, só há oferta para
    // quem está como grave ou preocupante pela posição na base, e o backend diz por que não houve.
    const comUso = await api.simularRetencao({
      nome: 'Café Aroma',
      mrr: 1200,
      sinais: { abriu_cancelamento: false, uso_caiu: true, tickets: false, atraso: false },
      propensao: aceitaTudo,
    })
    expect(comUso.risco).not.toBeNull()
    if (comUso.decidido_por === 'regua') {
      expect(comUso.oferta !== null).toBe(comUso.risco! >= comUso.corte_de_intervencao)
      expect(comUso.faixa).toBe('preocupante')
      expect(comUso.sem_oferta_porque).toBeNull()
    } else {
      expect(comUso.oferta !== null).toBe(comUso.faixa !== 'sem_risco')
      expect(comUso.sem_oferta_porque === null).toBe(comUso.oferta !== null)
      if (comUso.oferta === null) expect(comUso.sem_oferta_porque).toMatch(/^Quem decidiu o risco foi o modelo de IA\./)
    }
    expect(comUso.aceitou).toBe(comUso.oferta === null ? null : true)
    // Com o modelo de IA ativo, quem tem dado de uso E abre a página de cancelamento recebe oferta
    // sempre (intenção explícita), mesmo com o risco calculado abaixo do corte antigo.
    const intencao = await api.simularRetencao({
      nome: 'Estúdio Vale',
      mrr: 1200,
      sinais: { abriu_cancelamento: true, uso_caiu: true, tickets: false, atraso: false },
      propensao: aceitaTudo,
    })
    expect(intencao.oferta).not.toBeNull()
    expect(intencao.aceitou).toBe(true)
    expect(intencao.sem_oferta_porque).toBeNull()
    if (intencao.decidido_por === 'modelo') expect(intencao.porque).toMatch(/^O cliente mostrou intenção explícita de sair\./)
    expect((await api.clientesRecentes({ limite: 50 })).some((c) => c.simulado)).toBe(false)
    expect((await api.clientesRecentes({ limite: 50, incluirSimulados: true })).filter((c) => c.simulado).map((c) => c.nome).sort()).toEqual(['Café Aroma', 'Estúdio Vale', 'Loja Ponto Certo'])

    // O membro lê a simulação, e não avança nem apaga.
    trocarPapelDeDesenvolvimento('membro')
    expect((await api.simulacao()).fase).toBe('recuperada')
    const negado = await erroDe(api.simulacaoAvancar(1))
    expect([negado.codigo, negado.status]).toEqual(['sem_permissao', 403])
    expect((await erroDe(api.simulacaoLimpar())).status).toBe(403)
    trocarPapelDeDesenvolvimento('owner')

    // Apagar tira tudo o que é fictício, e só isso.
    expect((await api.simulacaoLimpar()).fase).toBe('formulario')
    expect((await api.ciclos({ incluirSimulados: true })).some((c) => c.simulado)).toBe(false)
    expect((await api.clientesRecentes({ limite: 50, incluirSimulados: true })).some((c) => c.simulado)).toBe(false)
    expect((await api.ciclos({})).length).toBe(reaisAntes.length)
    console.log(`[vivo] simulação: ${fases.join(' > ')}; líquido simulado ${e.desfecho!.valor_liquido > 0 ? 'positivo' : 'zero'}; retenção ${retencao.faixa} (${retencao.decidido_por}), com dado de uso ${comUso.faixa} (${comUso.decidido_por}, risco ${comUso.risco}); membro ${negado.status}`)
  })

  it('assistente: a pergunta vai ao backend, e a resposta volta na forma da tela', async () => {
    const r = await api.assistente('Quanto recuperei este mês?')
    expect(['assistente', 'texto_fixo']).toContain(r.origem)
    expect(r.texto.length).toBeGreaterThan(20)
    expect(r.texto.charAt(0)).toBe(r.texto.charAt(0).toUpperCase())
    // Todo link é uma página do próprio painel.
    for (const l of r.links) expect(l.para.startsWith('/') && !l.para.startsWith('//')).toBe(true)
    expect(temChave(r, 'fee')).toBe(false)
    // Corpo fora do contrato: o backend recusa, e o erro chega em português.
    const longa = await erroDe(api.assistente('x'.repeat(501)))
    expect([longa.codigo, longa.status, longa.motivo]).toEqual(['invalido', 422, 'pergunta_invalida'])
    // O membro também pergunta: o assistente só lê.
    trocarPapelDeDesenvolvimento('membro')
    expect((await api.assistente('O que acontece depois da 3ª tentativa?')).texto.length).toBeGreaterThan(20)
    trocarPapelDeDesenvolvimento('owner')
    console.log(`[vivo] assistente: origem ${r.origem}, ${r.links.length} link(s)`)
  })

  it('eventos pela chave de API: o servidor da empresa avisa, o reenvio conta uma vez, e a chave revogada para na hora', async () => {
    trocarPapelDeDesenvolvimento('owner')
    trocarPlanoDeDesenvolvimento('premium')
    const { chave, inteira } = await api.criarChave(NOME_DA_CHAVE)
    const avisar = (corpo: unknown, credencial = inteira) =>
      fetch(`${ENDERECO_DA_API}/eventos`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${credencial}`, 'Content-Type': 'application/json' },
        body: JSON.stringify(corpo),
      })
    // Um evento sem risco (uma sessão comum): o sistema recebe, avalia e não oferta nada.
    const evento = { userId: 'cliente-do-teste-de-eventos', event: 'Session Started', messageId: `vivo-${Date.now()}` }
    const primeiro = await avisar(evento)
    expect(primeiro.status).toBe(200)
    expect(await primeiro.json()).toEqual({ status: 'ok', duplicado: false })
    const reenvio = await avisar(evento)
    expect(reenvio.status).toBe(200)
    expect(await reenvio.json()).toEqual({ status: 'ok', duplicado: true })
    // O mesmo corpo do webhook do Segment, com a mesma validação.
    const semIdentidade = await avisar({ event: 'Session Started' })
    expect(semIdentidade.status).toBe(422)
    const idTorto = await avisar({ ...evento, messageId: 7 })
    expect(idTorto.status).toBe(422)
    // Sem a chave, e com uma chave inventada: 401.
    expect((await fetch(`${ENDERECO_DA_API}/eventos`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(evento) })).status).toBe(401)
    expect((await avisar(evento, `crai_live_${'A'.repeat(43)}`)).status).toBe(401)
    // O uso do evento conta na chave.
    expect((await api.chaves()).chaves.find((c) => c.id === chave.id)?.ultimo_uso).toMatch(COM_FUSO)
    await api.revogarChave(chave.id)
    const depois = await avisar({ ...evento, messageId: `vivo-depois-${Date.now()}` })
    expect(depois.status).toBe(401)
    console.log(`[vivo] eventos: primeiro ${primeiro.status}, reenvio duplicado, sem identidade ${semIdentidade.status}, depois de revogar ${depois.status}`)
  })

  it('direitos do titular e descadastro: exportar, não contatar, anonimizar, e o texto da política', async () => {
    trocarPapelDeDesenvolvimento('owner')
    trocarPlanoDeDesenvolvimento('premium')
    const { chave, inteira } = await api.criarChave(NOME_DA_CHAVE)
    const id = `titular-do-teste-${Date.now()}`
    try {
      // Um cliente com contato, cadastrado pela API da empresa.
      const cadastro = await fetch(`${ENDERECO_DA_API}/clientes`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${inteira}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ customer_id_externo: id, mrr: 200, billing_profile: 'PJ', nome: 'Cliente de teste do titular', email: 'titular.teste@exemplo.com.br', telefone: '+5511900000099' }),
      })
      expect(cadastro.status).toBe(200)

      // Exportar: o arquivo diz que há contato guardado, sem repetir o valor.
      const exportado = await api.exportarTitular(id)
      expect(exportado.arquivo).toBe(`crai-titular-${id}.json`)
      const conteudo = JSON.parse(exportado.conteudo!) as { cadastro: { nome: string }; contatos_guardados: Record<string, boolean>; nao_contatar: unknown }
      expect(conteudo.cadastro.nome).toBe('Cliente de teste do titular')
      expect(conteudo.contatos_guardados).toEqual({ email: true, telefone: true })
      expect(exportado.conteudo).not.toContain('titular.teste@exemplo.com.br')
      expect(exportado.conteudo).not.toContain('5511900000099')
      expect(temChave(conteudo, 'fee')).toBe(false)

      // Não contatar, e a volta.
      expect(await api.naoContatar(id)).toMatchObject({ marcado: true, ja_estava: false })
      expect(await api.naoContatar(id)).toMatchObject({ marcado: true, ja_estava: true })
      expect(await api.voltarAContatar(id)).toEqual({ marcado: false, ja_estava: true, desde: null })

      // O membro não exporta, não marca e não anonimiza.
      trocarPapelDeDesenvolvimento('membro')
      // Uma chamada de cada vez: três promessas soltas juntas deixam uma recusa sem dono por um instante.
      for (const negada of [() => api.exportarTitular(id), () => api.naoContatar(id), () => api.anonimizarTitular(id)]) expect((await erroDe(negada())).status).toBe(403)
      trocarPapelDeDesenvolvimento('owner')

      // Anonimizar: os contatos saem, e a marca de não contatar fica (e não sai mais).
      const anonimizado = await api.anonimizarTitular(id)
      expect(anonimizado.contatos_apagados).toBe(3)
      const depois = JSON.parse((await api.exportarTitular(id)).conteudo!) as { cadastro: { nome: string | null }; contatos_guardados: Record<string, boolean>; nao_contatar: { origem: string } }
      expect(depois.cadastro.nome).toBeNull()
      expect(depois.contatos_guardados).toEqual({ email: false, telefone: false })
      expect(depois.nao_contatar.origem).toBe('anonimizacao')
      const volta = await erroDe(api.voltarAContatar(id))
      expect([volta.status, volta.motivo]).toEqual([409, 'marca_da_anonimizacao'])

      // Titular que não existe: a frase em português.
      const inexistente = await erroDe(api.exportarTitular('nao-existe-na-base'))
      expect([inexistente.status, inexistente.motivo]).toEqual([404, 'titular_nao_encontrado'])
      expect(await api.explicacaoDecisao('nao-existe-na-base')).toBeNull()
    } finally {
      await fetch(`${ENDERECO_DA_API}/clientes/${id}`, { method: 'DELETE', headers: { Authorization: `Bearer ${inteira}` } })
      await api.revogarChave(chave.id)
    }

    // O texto para a política vem com os prazos da empresa, sem marcador sobrando.
    const politica = await api.textoParaPolitica()
    expect(politica!.titulo).toBe('O que a CRAI faz com os dados dos nossos clientes')
    expect(politica!.texto).toContain('responda SAIR')
    expect(politica!.texto).not.toContain('{{')
    // A explicação de uma decisão de verdade: a de um ciclo da semente.
    const comDecisao = (await api.ciclos({})).find((c) => c.id_recorrencia)
    const explicacao = await api.explicacaoDecisao(comDecisao!.id_recorrencia)
    expect(explicacao).not.toBeNull()
    expect(explicacao!.decisao.length).toBeGreaterThan(30)
    expect(explicacao!.quando).toMatch(COM_FUSO)
    console.log(`[vivo] titular: exportado sem o valor dos contatos, não contatar ida e volta, anonimizado, texto da política com ${politica!.texto.length} caracteres`)
  })

  it('rodada 4: a busca, o extrato em arquivo, a próxima ação e a lista do sino vêm do backend', async () => {
    const todos = await api.ciclos()
    expect(todos.length).toBeGreaterThan(0)
    // A busca do topo: pelo começo do id da recorrência de um ciclo que existe.
    const alvo = todos[0]
    const achado = await api.buscar(alvo.id_recorrencia.slice(0, 8))
    expect(achado.ciclos.some((c) => c.id === alvo.id)).toBe(true)
    expect(JSON.stringify(achado)).not.toMatch(/@|\+55|"fee"/)
    expect(await api.buscar('zzzzqqqq')).toEqual({ clientes: [], ciclos: [] })
    expect(await api.buscar('a')).toEqual({ clientes: [], ciclos: [] }) // uma letra: nem consulta

    // O extrato em arquivo: o cabeçalho é o do backend; o membro recebe 403.
    const arquivo = await api.extratoCsv()
    expect(arquivo).not.toBeNull()
    expect(arquivo!.replace(String.fromCharCode(0xfeff), '').split('\r\n')[0]).toBe(
      'Data;Cliente;Identificador;Origem;O que aconteceu;Valor (R$);Taxa da CRAI (R$);Líquido para você (R$);Situação;Demonstração',
    )
    trocarPapelDeDesenvolvimento('membro')
    const negado = await erroDe(api.extratoCsv())
    expect([negado.codigo, negado.status]).toEqual(['sem_permissao', 403])
    trocarPapelDeDesenvolvimento('owner')

    // A próxima ação (a semente deixa um ciclo com tentativas agendadas) e a lista do sino.
    const mes = await api.metricasMes()
    expect(mes.proxima_acao).not.toBeNull()
    expect(mes.proxima_acao!.descricao.length).toBeGreaterThan(0)
    expect(COM_FUSO.test(mes.proxima_acao!.quando)).toBe(true)
    const esperando = await api.ciclos({ aguardandoEscolha: true })
    expect(esperando.length).toBe(await api.pendenciasDeEscolha())
    expect(esperando.length).toBe(mes.aguardando_escolha)
    expect(esperando.every((c) => c.estado === 'aguardando_escolha')).toBe(true)

    // O intervalo entre ofertas vem na configuração; a lista de clientes diz quem não quer contato.
    expect((await api.configuracao()).intervalo_minimo_ofertas_dias).toBe(30)
    const clientes = await api.clientesRecentes({ limite: 5 })
    expect(clientes.every((c) => typeof c.nao_contatar === 'boolean')).toBe(true)
    if (clientes.length) expect((await api.clientesRecentes({ cliente: clientes[0].id })).map((c) => c.id)).toEqual([clientes[0].id])
    expect(await api.clientesRecentes({ cliente: 'cliente-que-nao-existe' })).toEqual([])
    console.log(`[vivo] rodada 4: busca ${achado.ciclos.length} ciclo(s); próxima ação "${mes.proxima_acao!.descricao}"; ${esperando.length} esperando a escolha`)
  })

  it('rodada 4, modo piloto: a visão geral diz se a empresa está em piloto, e a linha de piloto do extrato não tem taxa cobrada', async () => {
    const resumo = await api.resumoVisaoGeral()
    expect(typeof resumo.piloto).toBe('boolean')
    const linhas = await api.extrato()
    for (const l of linhas) {
      // A linha é de piloto (taxa zero, líquido inteiro, e a taxa que seria cobrada ao lado) ou não é (null).
      if (typeof l.taxa_fora_do_piloto === 'number') {
        expect(l.taxa).toBe(0)
        expect(l.liquido).toBe(l.valor_base)
      } else {
        expect(l.taxa_fora_do_piloto).toBeNull()
      }
    }
    // Nenhuma outra leitura traz a taxa que seria cobrada.
    const fora = JSON.stringify([resumo, await api.metricasMes(), await api.ciclos(), await api.atividade()])
    expect(fora).not.toContain('fora_do_piloto')
    console.log(`[vivo] modo piloto: empresa ${resumo.piloto ? 'EM piloto' : 'fora do piloto'}; ${linhas.filter((l) => typeof l.taxa_fora_do_piloto === 'number').length} de ${linhas.length} linha(s) do extrato são de piloto`)
  })

  it('o mapa de rotas reais é o desta etapa', () => {
    // 12 da Rodada 2, mais 6 da página do voluntário (Fase 1), 6 da visão geral (Fase 2), 7 da
    // simulação do gateway (Fase 3), o assistente (Fase 4) e 6 dos direitos do titular (Fase 6).
    // Rodada 4, Fase 2: mais a busca do topo e o extrato em arquivo.
    expect(Object.keys(ROTAS_REAIS)).toHaveLength(40)
  })
})

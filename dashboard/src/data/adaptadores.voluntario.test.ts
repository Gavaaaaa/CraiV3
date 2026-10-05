/**
 * Rodada 3, Fase 1: os adaptadores do voluntário contra a forma das rotas novas
 * (`/clientes/recentes`, `/clientes/base`, `/metrics/voluntario/*`, `/clientes/importar`).
 * O que se mede: a tradução para os tipos da tela, que nada além do conhecido passa (nenhum
 * contato, nenhuma `fee`) e que o que o backend não informa chega como null, não como número.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  adaptarBase,
  adaptarClienteRecente,
  adaptarComparacao,
  adaptarImportacao,
  adaptarResumoVoluntario,
  adaptarSerieVoluntario,
  type ClienteRecenteApi,
  type MesVoluntarioApi,
} from './adaptadores'

const GRAVE: ClienteRecenteApi = {
  id: 'cli-001',
  nome: 'Oficina Boa Viagem',
  mrr: 480,
  faixa: 'grave',
  motivo: '41 dias sem login, 0 funcionalidades usadas, MRR R$ 480,00',
  decidido_por: 'modelo',
  posicao_no_ranking: 3,
  abordagem: { oferta: 'pausa_1_mes', oferta_legivel: 'pausa de 1 mês na assinatura, sem custo', canal: 'email', canal_legivel: 'e-mail', situacao: 'enviada', simulado: false },
  atualizado_em: '2026-10-04T09:00:00-03:00',
  simulado: false,
}

const MES: MesVoluntarioApi = {
  mes: '2026-10',
  valor_liquido_mantido: 340,
  clientes_mantidos: 2,
  estornos: { quantidade: 1, valor_liquido_estornado: 340 },
  aceites_sem_valor: 1,
  grave: 4,
  preocupante: 7,
  ofertas_enviadas: 9,
  ofertas_aceitas: 2,
  meses_de_mrr: 1,
  prazo_estorno_dias: 30,
}

describe('clientes recentes', () => {
  it('traduz a linha, com a oferta e o canal em texto de tela', () => {
    expect(adaptarClienteRecente(GRAVE)).toEqual({
      id: 'cli-001',
      nome: 'Oficina Boa Viagem',
      mrr: 480,
      faixa: 'grave',
      motivo: '41 dias sem login, 0 funcionalidades usadas, MRR R$ 480,00',
      risco_decidido_por: 'modelo',
      posicao_na_base: 3,
      abordagem: { oferta: 'Pausa de 1 mês na assinatura, sem custo', canal: 'E-mail', status: 'enviada' },
      atualizado_em: '2026-10-04T09:00:00-03:00',
      simulado: false,
    })
  })

  it('sem nome mostra o id; sem mensalidade, sem avaliação e sem oferta chegam como null', () => {
    const c = adaptarClienteRecente({ ...GRAVE, nome: '  ', mrr: null, faixa: 'sem_dado', decidido_por: null, posicao_no_ranking: null, abordagem: null, atualizado_em: null, motivo: 'sem dado de atividade' })
    expect(c.nome).toBe('cli-001')
    expect([c.mrr, c.risco_decidido_por, c.posicao_na_base, c.abordagem, c.atualizado_em]).toEqual([null, null, null, null, null])
    expect(c.motivo).toBe('Sem dado de atividade')
  })

  it('faixa ou situação que a tela não conhece não vira uma faixa de risco inventada', () => {
    const c = adaptarClienteRecente({ ...GRAVE, faixa: 'catastrofico', abordagem: { ...GRAVE.abordagem!, situacao: 'em_analise' } })
    expect(c.faixa).toBe('sem_dado')
    expect(c.abordagem).toBeNull()
  })

  it('contato, fee ou qualquer campo a mais na resposta não chega à tela', () => {
    const comExtra = { ...GRAVE, email: 'pessoa@exemplo.com.br', telefone: '+5511988887777', cpf: '000.000.000-00', fee: 72, chave_pix: 'x' }
    const texto = JSON.stringify(adaptarClienteRecente(comExtra))
    for (const proibido of ['pessoa@exemplo.com.br', '5511988887777', '000.000.000-00', 'fee', 'chave_pix', 'email', 'telefone']) {
      expect(texto).not.toContain(proibido)
    }
  })

  it('sem o texto pronto, o código da oferta e do canal aparece, nunca um texto de outra oferta', () => {
    const c = adaptarClienteRecente({ ...GRAVE, abordagem: { oferta: 'oferta_nova', oferta_legivel: null, canal: null, canal_legivel: null, situacao: 'aguardando' } })
    expect(c.abordagem).toEqual({ oferta: 'Oferta_nova', canal: 'Sem canal disponível', status: 'aguardando' })
  })
})

describe('a base', () => {
  it('sem cliente nenhum é null', () => {
    expect(adaptarBase({ base: null })).toBeNull()
  })

  it('leva os totais, e a origem só quando o backend a registrou', () => {
    const base = { total: 40, com_dados_comportamento: 30, decididos_pelo_modelo: 12, modelo_ativo: true, atualizada_em: '2026-10-04T09:00:00-03:00', origem: 'api' }
    expect(adaptarBase({ base })).toEqual({ total: 40, com_dados_comportamento: 30, decididos_pelo_modelo: 12, atualizada_em: '2026-10-04T09:00:00-03:00', origem: 'api' })
    expect(adaptarBase({ base: { ...base, origem: null } })?.origem).toBeNull()
    expect(adaptarBase({ base: { ...base, origem: 'telepatia' } })?.origem).toBeNull()
  })
})

describe('o mês e a série', () => {
  it('o resumo leva o líquido, as contagens e a regra aplicada; a fee não existe no tipo', () => {
    const r = adaptarResumoVoluntario(MES)
    expect(r).toEqual({
      mes: '2026-10',
      valor_liquido_mantido: 340,
      clientes_mantidos: 2,
      estornos: 1,
      grave: 4,
      preocupante: 7,
      ofertas_enviadas: 9,
      ofertas_aceitas: 2,
      meses_de_mrr: 1,
      prazo_estorno_dias: 30,
    })
    expect(JSON.stringify(adaptarResumoVoluntario({ ...MES, fee: 60 } as MesVoluntarioApi))).not.toContain('fee')
  })

  it('a série é um ponto por dia, com o líquido do dia (que pode ser negativo no dia do estorno)', () => {
    const serie = adaptarSerieVoluntario({
      dias: 2,
      pontos: [
        { dia: '2026-10-03', valor_liquido_mantido: 340, clientes_mantidos: 1, valor_liquido_estornado: 0 },
        { dia: '2026-10-04', valor_liquido_mantido: -340, clientes_mantidos: 0, valor_liquido_estornado: 340 },
      ],
    })
    expect(serie).toEqual([
      { dia: '2026-10-03', valor: 340 },
      { dia: '2026-10-04', valor: -340 },
    ])
  })
})

describe('régua x modelo', () => {
  it('sem desfecho suficiente é null: a aba esconde a comparação', () => {
    expect(adaptarComparacao({ dias: 30, comparacao: null, motivo_vazio: 'cancelamentos_insuficientes' })).toBeNull()
  })

  it('com comparação, leva os dois lados', () => {
    expect(
      adaptarComparacao({ dias: 30, comparacao: { clientes_com_dados: 60, cancelamentos: 6, regua: { marcou_grave: 9, avisou_antes: 2 }, modelo: { marcou_grave: 7, avisou_antes: 4 } }, motivo_vazio: null }),
    ).toEqual({ dias: 30, clientes_com_dados: 60, cancelamentos: 6, regua: { marcou_grave: 9, avisou_antes: 2 }, modelo: { marcou_grave: 7, avisou_antes: 4 } })
  })
})

describe('a importação', () => {
  it('o que o backend não separa chega como null, nunca como número', () => {
    const r = adaptarImportacao('base.csv', { importados: 7, rejeitados: [{ linha: 4, motivo: 'mrr ausente' }], colunas_nao_encontradas: [], linhas_sem_dado_comportamental: 2 })
    expect(r).toEqual({
      arquivo: 'base.csv',
      linhas: 8,
      importados: 7,
      rejeitados: 1,
      novos: null,
      atualizados: null,
      sem_id_recorrencia: null,
      sem_comportamento: 2,
      avisos: ['Linha 4: mrr ausente'],
      demonstracao: false,
    })
  })

  it('lista só os primeiros motivos de recusa e diz quantos faltam', () => {
    const rejeitados = Array.from({ length: 8 }, (_, i) => ({ linha: i + 2, motivo: 'mrr ausente' }))
    const r = adaptarImportacao('b.csv', { importados: 0, rejeitados, colunas_nao_encontradas: [], linhas_sem_dado_comportamental: 0, mensagem: 'nada importado: todas as linhas foram rejeitadas' })
    expect(r.avisos).toHaveLength(7)
    expect(r.avisos[0]).toBe('Nada importado: todas as linhas foram rejeitadas')
    expect(r.avisos[6]).toBe('E mais 3 linhas recusadas.')
  })
})

/* A camada HTTP com o anexo: o arquivo vai como multipart, e a recusa do arquivo tem frase própria. */
describe('o anexo na camada HTTP', () => {
  const BASE = 'http://backend.teste'
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.unstubAllEnvs()
  })

  async function carregarHttp() {
    vi.resetModules()
    vi.stubEnv('VITE_CRAI_API_URL', BASE)
    return import('./http')
  }

  it('o FormData vai como está, sem Content-Type escrito à mão e com o token', async () => {
    const vistos: { url: string; headers: Record<string, string>; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init: RequestInit = {}) => {
        vistos.push({ url, headers: (init.headers ?? {}) as Record<string, string>, body: init.body })
        return new Response(JSON.stringify(url.endsWith('/dev/token') ? { token: 'tok' } : { importados: 1, rejeitados: [], colunas_nao_encontradas: [], linhas_sem_dado_comportamental: 0 }))
      }),
    )
    const http = await carregarHttp()
    const corpo = new FormData()
    corpo.append('arquivo', new Blob(['a,b\n1,2\n']), 'base.csv')
    await http.chamar('POST', '/clientes/importar', corpo)
    const envio = vistos.find((v) => v.url.endsWith('/clientes/importar'))!
    expect(envio.body).toBe(corpo)
    expect(envio.headers['Content-Type']).toBeUndefined()
    expect(envio.headers.Authorization).toBe('Bearer tok')
  })

  it.each([
    [415, 'extensao_nao_suportada', 'Só CSV ou XLSX. Outros formatos não são lidos.'],
    [413, 'arquivo_grande_demais', 'O arquivo é grande demais. Divida em duas partes.'],
    [422, 'sem_linhas', 'O arquivo só tem o cabeçalho, sem nenhuma linha de cliente.'],
    [415, 'motivo_novo', 'O servidor recusou o arquivo enviado.'],
  ])('arquivo recusado com %i %s tem frase em português', async (status, motivo, frase) => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) =>
        url.endsWith('/dev/token') ? new Response('{"token":"t"}') : new Response(JSON.stringify({ detail: { motivo, detalhe: 'x' } }), { status }),
      ),
    )
    const http = await carregarHttp()
    await expect(http.chamar('POST', '/clientes/importar', new FormData())).rejects.toMatchObject({ codigo: 'invalido', status, message: frase })
  })
})

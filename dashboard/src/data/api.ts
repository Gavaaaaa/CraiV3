/**
 * A ÚNICA camada de dados do dashboard. Todas as telas passam por aqui.
 *
 * DUAS CAMADAS (Etapa 4A, integração parcial):
 *
 * - Sem `VITE_CRAI_API_URL`, tudo devolve dados de demonstração (mock.ts) com um pequeno
 *   atraso, exatamente como antes.
 * - Com `VITE_CRAI_API_URL`, as funções listadas em `ROTAS_REAIS` chamam o backend (`http.ts`)
 *   e passam a resposta pelos adaptadores (`adaptadores.ts`). AS DEMAIS CONTINUAM COM O MOCK,
 *   e a tela marca esses blocos com a etiqueta "Demonstração" (`emDemonstracao`).
 *
 * As telas não sabem de onde o dado veio: as assinaturas e os tipos são os mesmos.
 */
import {
  AGORA,
  atividades,
  chavesApi,
  configuracaoPadrao,
  empresaDetalhe,
  membros,
  baseClientes,
  clientesRisco,
  comparacaoReguaModelo,
  resumoVoluntario,
  ciclos,
  detalhes,
  empresa,
  extrato,
  funil,
  metricasMes,
  oQueFunciona,
  riscoGrave,
  saude,
  serie30,
  serieDupla,
} from './mock'
import {
  CANAIS_DO_BACKEND,
  abordagemParaApi,
  adaptarCiclo,
  adaptarConfiguracao,
  adaptarDetalhe,
  adaptarMensagem,
  adaptarMetricas,
  adaptarSaude,
  adaptarSerie,
  configuracaoParaApi,
  statusParaApi,
  type CicloDetalheApi,
  type ConfiguracaoApi,
  type ListaDeCiclosApi,
  type MensagemApi,
  type MetricasMesApi,
  type RespostaConfiguracaoApi,
  type SaudeApi,
  type SerieApi,
} from './adaptadores'
import { responder } from './assistente'
import { ErroApi } from './erros'
import { MODO_REAL, chamar, definirPapelDev, papelDev } from './http'
import { avancar, diasAteProximaAcao, escolherMensagem, estadoVazio, iniciar, simularRetencao } from './simulador'
import type {
  Abordagem,
  Canal,
  ChaveApi,
  EmpresaDetalhe,
  ExplicacaoDecisao,
  Integracao,
  Membro,
  Papel,
  ResultadoTesteIntegracao,
  RespostaAssistente,
  BaseClientes,
  ClienteRisco,
  ComparacaoReguaModelo,
  ResultadoImportacao,
  ResumoVoluntario,
  Atividade,
  ClienteFicticio,
  ClienteRiscoFicticio,
  EstadoSimulacao,
  ResultadoRetencaoSimulada,
  CicloDetalhe,
  CicloResumo,
  Configuracao,
  Empresa,
  Funil,
  LinhaExtrato,
  MetricasMes,
  OQueFunciona,
  PontoSerie,
  PontoSerieDupla,
  ResumoVisaoGeral,
  SaudeSistema,
  StatusTela,
  SugestaoDoCiclo,
} from './tipos'

export { ErroApi } from './erros'
export { MODO_REAL, iniciarSessao } from './http'

/**
 * O mapa único do que já fala com o backend quando o modo real está ligado. Tudo o que NÃO
 * está aqui continua em demonstração. Para ligar mais uma rota: implemente o ramo real na
 * função, acrescente o nome aqui e tire a etiqueta "Demonstração" do bloco que a usa.
 */
export const ROTAS_REAIS = {
  ciclos: true, // GET /ciclos
  ciclo: true, // GET /ciclos/{id}
  configuracao: true, // GET /configuracao
  salvarConfiguracao: true, // PUT /configuracao
  regerarSugestoes: true, // POST /ciclos/{id}/mensagens/regerar
  escolherMensagem: true, // POST /ciclos/{id}/mensagens/escolher
  metricasMes: true, // GET /metrics/involuntario/mes
  serie: true, // GET /metrics/involuntario/serie
  saude: true, // GET /health
} as const

type RotaReal = keyof typeof ROTAS_REAIS
const real = (rota: RotaReal): boolean => MODO_REAL && ROTAS_REAIS[rota]

/**
 * A tela pergunta: este bloco ainda mostra dado de demonstração? Sim, se o modo real está
 * ligado e alguma das funções que o alimentam não está em `ROTAS_REAIS`. Sem o modo real,
 * nunca: o dashboard inteiro é demonstração e nada muda na tela.
 */
export function emDemonstracao(...funcoes: string[]): boolean {
  return MODO_REAL && funcoes.some((f) => !(f in ROTAS_REAIS))
}

/** "Agora" para textos relativos: o relógio de verdade no modo real; o fixo do mock na demonstração. */
export function agoraDaTela(): Date {
  return MODO_REAL ? new Date() : AGORA
}

/** Os canais que a configuração oferece: o backend ainda não aceita SMS no involuntário. */
export const CANAIS_DISPONIVEIS: Canal[] = MODO_REAL ? CANAIS_DO_BACKEND : ['whatsapp', 'email', 'sms']

/** O papel do login de desenvolvimento (só existe no modo real). */
export const papelDeDesenvolvimento = papelDev
export const trocarPapelDeDesenvolvimento = definirPapelDev

/**
 * Estados de tela para testar sem backend: abra a página com `?estado=erro` (toda chamada
 * falha) ou `?estado=vazio` (listas vazias, totais zerados). Na integração, o Claude Code
 * usa isto nos roteiros Playwright; em produção, o parâmetro é ignorado. Vale só para o que
 * vem do mock: as rotas reais mostram o que o backend responder.
 */
export const estadoTeste: 'normal' | 'vazio' | 'erro' = (() => {
  if (typeof window === 'undefined') return 'normal'
  const v = new URLSearchParams(window.location.search).get('estado')
  return v === 'vazio' || v === 'erro' ? v : 'normal'
})()
const vazio = estadoTeste === 'vazio'

const espera = async (ms = 250) => {
  await new Promise((r) => setTimeout(r, ms))
  if (estadoTeste === 'erro') throw new ErroApi('servidor', 'Não foi possível falar com o servidor da CRAI.')
}

export interface FiltroCiclos {
  status?: StatusTela | 'todos'
  busca?: string
  incluirSimulados?: boolean
}

/** Quantos ciclos uma chamada traz. A paginação por cursor do backend ainda não é usada pela tela. */
const LIMITE_DE_CICLOS = 200

function filtrarPorBusca(lista: CicloResumo[], busca: string | undefined): CicloResumo[] {
  const q = (busca ?? '').trim().toLowerCase()
  if (!q) return lista
  return lista.filter((c) => (c.cliente ?? '').toLowerCase().includes(q) || c.id_recorrencia.toLowerCase().includes(q))
}

/** O detalhe de demonstração de um ciclo (o escrito à mão, ou um genérico). */
function detalheDeDemonstracao(id: number): CicloDetalhe | null {
  if (detalhesAtuais[id]) return detalhesAtuais[id]
  const resumo = ciclos.find((c) => c.id === id)
  if (!resumo) return null
  // Detalhe genérico para os ciclos sem linha do tempo escrita à mão
  return {
    ...resumo,
    chance_recuperar: 0.58,
    dia_provavel_saldo: null,
    contribuicoes: [
      { fator: 'Causa da falha', efeito: 'Aumentou a chance de recuperar' },
      { fator: 'Histórico de pagamento', efeito: 'Aumentou a chance de recuperar' },
      { fator: 'Valor da cobrança', efeito: 'Reduziu a chance de recuperar' },
    ],
    tentativas: [],
    sugestoes: [],
    modo_mensagem: null,
    escolha_ate: null,
    escolha_por: null,
    motivo_descarte: resumo.estado === 'descartado' ? 'Retorno esperado abaixo do custo da ação' : null,
    linha_do_tempo: [{ em: resumo.aberto_em, tipo: 'abertura', titulo: 'Cobrança falhou', tom: 'danger' }],
  }
}

export const api = {
  /** A empresa logada. No modo real ainda não há rota: é a empresa fictícia do login de desenvolvimento. */
  async empresa(): Promise<Empresa> {
    if (MODO_REAL) return { nome: 'Empresa de demonstração', plano: 'premium', papel: papelDev() }
    await espera(80)
    return empresa
  },

  /** GET /ciclos */
  async ciclos(filtro: FiltroCiclos = {}): Promise<CicloResumo[]> {
    if (real('ciclos')) {
      const parametros = new URLSearchParams({ limite: String(LIMITE_DE_CICLOS) })
      if (filtro.status && filtro.status !== 'todos') parametros.set('status', statusParaApi(filtro.status))
      const r = await chamar<ListaDeCiclosApi>('GET', `/ciclos?${parametros}`)
      // A busca por nome é feita aqui: o backend busca só pelo id da recorrência.
      return filtrarPorBusca(r.ciclos.map(adaptarCiclo), filtro.busca)
    }
    await espera()
    if (vazio) return []
    return filtrarPorBusca(
      ciclos
        .filter((c) => (filtro.incluirSimulados ?? true) || !c.simulado)
        .filter((c) => !filtro.status || filtro.status === 'todos' || c.status === filtro.status),
      filtro.busca,
    ).sort((a, b) => (a.atualizado_em < b.atualizado_em ? 1 : -1))
  },

  /** GET /ciclos/{id} */
  async ciclo(id: number): Promise<CicloDetalhe | null> {
    if (real('ciclo')) return adaptarDetalhe(await chamar<CicloDetalheApi>('GET', `/ciclos/${id}`))
    await espera()
    return detalheDeDemonstracao(id)
  },

  /** GET /configuracao */
  async configuracao(): Promise<Configuracao> {
    if (real('configuracao')) {
      const r = await chamar<RespostaConfiguracaoApi>('GET', '/configuracao')
      configuracaoLida = r.configuracao
      return adaptarConfiguracao(r.configuracao, configuracaoAtual)
    }
    await espera(60)
    return configuracaoAtual
  },

  /** PUT /configuracao (parcial: só o que mudou em relação ao que foi lido) */
  async salvarConfiguracao(nova: Configuracao): Promise<Configuracao> {
    if (real('salvarConfiguracao')) {
      const lida = configuracaoLida ?? (await chamar<RespostaConfiguracaoApi>('GET', '/configuracao')).configuracao
      const corpo = configuracaoParaApi(nova, adaptarConfiguracao(lida, configuracaoAtual))
      // As notificações ainda não existem no backend: ficam só nesta sessão (demonstração).
      configuracaoAtual = { ...configuracaoAtual, notificacoes: { ...nova.notificacoes } }
      if (Object.keys(corpo).length) {
        configuracaoLida = (await chamar<RespostaConfiguracaoApi>('PUT', '/configuracao', corpo)).configuracao
      } else {
        configuracaoLida = lida
      }
      return adaptarConfiguracao(configuracaoLida, configuracaoAtual)
    }
    await espera(500)
    configuracaoAtual = structuredClone(nova)
    return configuracaoAtual
  },

  /** POST /ciclos/{id}/mensagens/regerar — gera outras 3 para o mesmo cliente (uma rodada nova) */
  async regerarSugestoes(id: number): Promise<SugestaoDoCiclo[]> {
    if (real('regerarSugestoes')) {
      const r = await chamar<{ rodada: number; mensagens: MensagemApi[] }>('POST', `/ciclos/${id}/mensagens/regerar`)
      return r.mensagens.map(adaptarMensagem)
    }
    await espera(900)
    const d = detalheDeDemonstracao(id)
    const canal = d?.sugestoes[0]?.canal ?? 'whatsapp'
    const motivo = d?.sugestoes[0]?.motivo_canal ?? 'Canal padrão da empresa'
    const rodada = Math.max(0, ...(d?.sugestoes ?? []).map((s) => s.rodada)) + 1
    const novas: SugestaoDoCiclo[] = [
      { rodada, abordagem: 'lembrete_cordial', canal, motivo_canal: motivo, recomendada: true, escolhida: false, nao_entregavel: false,
        texto: 'Oi! Passando para avisar que a mensalidade de R$ 4.900,00 não foi debitada este mês. Sem pressa: quando puder, regularize por este link e seguimos normalmente.' },
      { rodada, abordagem: 'facilitacao', canal, motivo_canal: motivo, recomendada: false, escolhida: false, nao_entregavel: false,
        texto: 'Oi! O débito automático de R$ 4.900,00 não passou. Se preferir, geramos um boleto ou um Pix avulso agora mesmo, neste link, e a assinatura continua igual.' },
      { rodada, abordagem: 'urgencia_com_respeito', canal, motivo_canal: motivo, recomendada: false, escolhida: false, nao_entregavel: false,
        texto: 'Olá. Precisamos da sua atenção: a mensalidade de R$ 4.900,00 está pendente há 7 dias e o acesso será pausado em breve. O link abaixo resolve em um minuto.' },
    ]
    if (d && detalhesAtuais[id]) detalhesAtuais[id] = { ...d, sugestoes: [...d.sugestoes, ...novas] }
    return novas
  },

  /**
   * POST /ciclos/{id}/mensagens/escolher — a empresa escolhe UMA das sugestões geradas (de
   * qualquer rodada). Nunca manda texto: só a rodada e o código da abordagem.
   */
  async escolherMensagem(id: number, rodada: number, abordagem: Abordagem): Promise<{ enviada: boolean; espera: 'fora_da_janela' | 'sem_canal' | null }> {
    if (real('escolherMensagem')) {
      const r = await chamar<{ enviada: boolean; espera: 'fora_da_janela' | 'sem_canal' | null }>(
        'POST',
        `/ciclos/${id}/mensagens/escolher`,
        { rodada, abordagem: abordagemParaApi(abordagem) },
      )
      return { enviada: r.enviada, espera: r.espera }
    }
    await espera(500)
    const d = detalheDeDemonstracao(id)
    if (d && detalhesAtuais[id]) {
      const agora = AGORA.toISOString()
      detalhesAtuais[id] = {
        ...d,
        estado: 'mensagem_enviada',
        escolha_ate: null,
        escolha_por: 'owner',
        proxima_acao: null,
        proxima_acao_descricao: null,
        sugestoes: d.sugestoes.map((s) => ({ ...s, escolhida: s.rodada === rodada && s.abordagem === abordagem })),
        linha_do_tempo: [
          ...d.linha_do_tempo.filter((e) => e.tipo !== 'aviso'),
          { em: agora, tipo: 'escolha', titulo: 'Mensagem escolhida', detalhe: 'Escolhida pelo dono.' },
          { em: agora, tipo: 'mensagem', titulo: 'Mensagem enviada', tom: 'ok' },
        ],
      }
    }
    return { enviada: true, espera: null }
  },

  /** GET /metrics/involuntario/mes */
  async metricasMes(): Promise<MetricasMes> {
    if (real('metricasMes')) {
      // "Ciclos ativos" é de agora, não do mês: conta os ciclos em análise ou em processo.
      const [mes, ativos] = await Promise.all([
        chamar<MetricasMesApi>('GET', '/metrics/involuntario/mes'),
        chamar<ListaDeCiclosApi>('GET', `/ciclos?status=em_analise,em_processo&limite=${LIMITE_DE_CICLOS}`),
      ])
      return adaptarMetricas(mes, ativos.ciclos.length)
    }
    await espera(120)
    if (vazio) return { ...metricasMes, valor_liquido_recuperado: 0, ciclos_ativos: 0, recuperados: 0, encerrados_sem_recuperacao: 0, aguardando_escolha: 0, taxa_recuperacao: 0, proxima_acao: null }
    return metricasMes
  },

  /** GET /metrics/involuntario/serie?dias=30 */
  async serie(): Promise<PontoSerie[]> {
    if (real('serie')) return adaptarSerie(await chamar<SerieApi>('GET', '/metrics/involuntario/serie?dias=30'))
    await espera(120)
    if (vazio) return serie30.map((p) => ({ ...p, valor: 0 }))
    return serie30
  },

  /* ---------------- Visão geral ---------------- */

  /** GET /metrics/visao-geral?dias=30 (rota nova, ver a lista no HANDOFF) */
  async resumoVisaoGeral(opcoes: { incluirSimulados?: boolean } = {}): Promise<ResumoVisaoGeral> {
    await espera(140)
    const sim = opcoes.incluirSimulados ?? false
    const linhas = vazio ? [] : extrato.filter((l) => sim || !l.simulado)
    const soma = (o: LinhaExtrato['origem']) => linhas.filter((l) => l.origem === o).reduce((t, l) => t + l.liquido, 0)
    const serie = serieDupla(sim)
    return {
      periodo: { de: serie[0].dia, ate: serie[serie.length - 1].dia },
      recuperado_involuntario: soma('involuntario'),
      cobrancas_recuperadas: linhas.filter((l) => l.origem === 'involuntario' && !l.estornado).length,
      retido_voluntario: empresa.plano === 'premium' ? soma('voluntario') : null,
      clientes_mantidos: linhas.filter((l) => l.origem === 'voluntario' && !l.estornado).length,
      ciclos_ativos: vazio ? 0 : ciclos.filter((c) => (sim || !c.simulado) && (c.status === 'em_analise' || c.status === 'em_processo')).length,
      aguardando_escolha: vazio ? 0 : ciclos.filter((c) => (sim || !c.simulado) && c.estado === 'aguardando_escolha').length,
      clientes_risco_grave: vazio ? 0 : riscoGrave.clientes,
      risco_grave_com_oferta: vazio ? 0 : riscoGrave.com_oferta,
      taxa_recuperacao: vazio ? 0 : (metricasMes.taxa_recuperacao ?? 0),
      ciclos_com_desfecho: vazio ? 0 : metricasMes.recuperados + metricasMes.encerrados_sem_recuperacao,
    }
  },

  /** GET /metrics/serie?dias=30 — involuntário (Etapa 2) e voluntário (Etapa 3) juntos */
  async serieDupla(opcoes: { incluirSimulados?: boolean } = {}): Promise<PontoSerieDupla[]> {
    await espera(160)
    return serieDupla(opcoes.incluirSimulados ?? false).map((p) => (vazio ? { ...p, involuntario: 0, voluntario: 0 } : p))
  },

  /** GET /metrics/involuntario/funil?mes= */
  async funil(): Promise<Funil> {
    await espera(180)
    if (vazio) return { ...funil, etapas: funil.etapas.map((e) => ({ ...e, chegaram: 0, valor: 0, recuperados_aqui: 0, valor_recuperado_aqui: 0 })), desfecho: { recuperados: 0, encerrados: 0, em_andamento: 0 } }
    return funil
  },

  /** GET /metrics/o-que-funciona?dias=30 */
  async oQueFunciona(): Promise<OQueFunciona> {
    await espera(200)
    if (vazio) return { causas: [], ofertas: [], canais: [] }
    return oQueFunciona
  },

  /** GET /atividade?limite= */
  async atividade(opcoes: { incluirSimulados?: boolean; limite?: number } = {}): Promise<Atividade[]> {
    await espera(150)
    if (vazio) return []
    return atividades.filter((a) => opcoes.incluirSimulados || !a.simulado).slice(0, opcoes.limite ?? 7)
  },

  /** GET /health: o relógio é real; modelos, redator e base ainda são de demonstração */
  async saude(): Promise<SaudeSistema> {
    if (real('saude')) return adaptarSaude(await chamar<SaudeApi>('GET', '/health', undefined, { semToken: true }), saude)
    await espera(90)
    if (vazio) return { ...saude, base: null }
    return saude
  },

  /** GET /extrato?mes= (o CSV é montado na tela, a partir das mesmas linhas) */
  async extrato(opcoes: { incluirSimulados?: boolean } = {}): Promise<LinhaExtrato[]> {
    await espera(200)
    if (vazio) return []
    return extrato.filter((l) => opcoes.incluirSimulados || !l.simulado)
  },

  /* ---------------- Simulação do gateway ---------------- */
  // Rotas da Etapa 3, Bloco 2 (nomes propostos; ver a lista no HANDOFF). Estado guardado por empresa.

  /** GET /simulacao */
  async simulacao(): Promise<EstadoSimulacao> {
    await espera(60)
    return simulacaoAtual
  },

  /** POST /simulacao/cliente + POST /simulacao/cobrar — cria o cliente fictício e dispara a cobrança */
  async simularCobranca(cliente: ClienteFicticio): Promise<EstadoSimulacao> {
    await espera(200)
    simulacaoAtual = iniciar(cliente)
    return simulacaoAtual
  },

  /** POST /simulacao/avancar {dias} — relógio simulado da empresa */
  async simulacaoAvancar(dias: number): Promise<EstadoSimulacao> {
    await espera(180)
    simulacaoAtual = avancar(simulacaoAtual, dias)
    return simulacaoAtual
  },

  /** POST /simulacao/avancar {ate_proxima_acao: true} */
  async simulacaoAvancarAteProximaAcao(): Promise<EstadoSimulacao> {
    await espera(180)
    simulacaoAtual = avancar(simulacaoAtual, diasAteProximaAcao(simulacaoAtual))
    return simulacaoAtual
  },

  /** POST /ciclos/{id}/mensagens/escolher (a mesma rota da Etapa 2, no ciclo simulado) */
  async simulacaoEscolherMensagem(abordagem: Abordagem): Promise<EstadoSimulacao> {
    await espera(250)
    simulacaoAtual = escolherMensagem(simulacaoAtual, abordagem, 'owner')
    return simulacaoAtual
  },

  /** DELETE /simulacao — limpa os dados fictícios da empresa */
  async simulacaoLimpar(): Promise<EstadoSimulacao> {
    await espera(80)
    simulacaoAtual = estadoVazio()
    return simulacaoAtual
  },

  /** POST /simulacao/retencao — cliente fictício em risco; o aceite vem da propensão escondida */
  async simularRetencao(cliente: ClienteRiscoFicticio): Promise<ResultadoRetencaoSimulada> {
    await espera(500)
    return simularRetencao(cliente)
  },

  /* ---------------- Voluntário ---------------- */

  /** GET /clientes/base (resumo da base importada) */
  async baseClientes(): Promise<BaseClientes | null> {
    await espera(80)
    if (vazio) return null
    return baseClientes
  },

  /** GET /clientes/recentes?limite=10 */
  async clientesRecentes(opcoes: { incluirSimulados?: boolean; limite?: number } = {}): Promise<ClienteRisco[]> {
    await espera(200)
    if (vazio) return []
    return clientesRisco.filter((c) => opcoes.incluirSimulados || !c.simulado).slice(0, opcoes.limite ?? 10)
  },

  /** GET /metrics/voluntario/mes */
  async resumoVoluntario(): Promise<ResumoVoluntario> {
    await espera(120)
    if (vazio) return { ...resumoVoluntario, valor_liquido_mantido: 0, clientes_mantidos: 0, estornos: 0, grave: 0, preocupante: 0, ofertas_enviadas: 0, ofertas_aceitas: 0 }
    return resumoVoluntario
  },

  /** GET /metrics/voluntario/serie?dias=30 (só o voluntário) */
  async serieVoluntario(opcoes: { incluirSimulados?: boolean } = {}): Promise<PontoSerie[]> {
    await espera(160)
    return serieDupla(opcoes.incluirSimulados ?? false).map((p) => ({ dia: p.dia, valor: vazio ? 0 : p.voluntario }))
  },

  /** GET /metrics/voluntario/regua-x-modelo?dias=30 */
  async comparacaoReguaModelo(): Promise<ComparacaoReguaModelo | null> {
    await espera(150)
    if (vazio) return null
    return comparacaoReguaModelo
  },

  /** POST /clientes/importar (multipart). Aqui só lê o nome e o tamanho; nada sobe. */
  async importarBase(arquivo: File): Promise<ResultadoImportacao> {
    await espera(1400)
    const linhas = Math.max(12, Math.round(arquivo.size / 96))
    return {
      arquivo: arquivo.name,
      linhas,
      novos: Math.round(linhas * 0.04),
      atualizados: Math.round(linhas * 0.31),
      sem_id_recorrencia: Math.round(linhas * 0.02),
      avisos: ['3 linhas sem e-mail nem telefone: ficam com "Sem canal disponível".'],
    }
  },

  /* ---------------- Assistente ---------------- */

  /** POST /assistente {pergunta}. As conversas não são guardadas: cada pergunta vai sozinha. */
  async assistente(pergunta: string): Promise<RespostaAssistente> {
    await espera(700 + Math.min(900, pergunta.length * 12))
    return responder(pergunta)
  },

  /* ---------------- Configuração ---------------- */

  /** GET /empresa (Supabase, só leitura) */
  async empresaDetalhe(): Promise<EmpresaDetalhe> {
    await espera(80)
    return empresaDetalhe
  },

  /** GET /equipe (Supabase `membros_empresa`) */
  async membros(): Promise<Membro[]> {
    await espera(120)
    return membrosAtuais
  },

  /** PUT /equipe/{id} {papel} — só owner */
  async mudarPapel(id: string, papel: Papel): Promise<Membro[]> {
    await espera(300)
    membrosAtuais = membrosAtuais.map((m) => (m.id === id ? { ...m, papel } : m))
    return membrosAtuais
  },

  /** GET /integracao (chaves só com o início; webhook) */
  async integracao(): Promise<Integracao> {
    await espera(120)
    return { chaves: chavesAtuais, webhook: { url: 'https://api.nimbusflow.com.br/crai/webhook', segredo_inicio: 'whsec_9a1f', ultimo_evento: new Date(AGORA_MS - 40 * 60_000).toISOString() } }
  },

  /** POST /integracao/chaves {ambiente} — a chave inteira aparece UMA vez */
  async criarChave(ambiente: 'live' | 'test'): Promise<{ chave: ChaveApi; inteira: string }> {
    await espera(400)
    const sufixo = Math.random().toString(36).slice(2, 6)
    const inteira = `crai_${ambiente}_${sufixo}${Math.random().toString(36).slice(2, 14)}${Math.random().toString(36).slice(2, 14)}`
    const chave: ChaveApi = { id: `k${Date.now()}`, ambiente, inicio: inteira.slice(0, 14), criada_em: new Date().toISOString(), ultimo_uso: null, revogada_em: null }
    chavesAtuais = [chave, ...chavesAtuais]
    return { chave, inteira }
  },

  /** DELETE /integracao/chaves/{id} — revogação imediata */
  async revogarChave(id: string): Promise<ChaveApi[]> {
    await espera(250)
    chavesAtuais = chavesAtuais.map((c) => (c.id === id ? { ...c, revogada_em: new Date().toISOString() } : c))
    return chavesAtuais
  },

  /** POST /integracao/testar */
  async testarIntegracao(): Promise<ResultadoTesteIntegracao> {
    await espera(1500)
    return {
      ok: true,
      passos: [
        { rotulo: 'Chave live aceita', ok: true, detalhe: 'crai_live_7f3a autenticou.' },
        { rotulo: 'Webhook respondeu', ok: true, detalhe: 'HTTP 200 em 180 ms, assinatura conferida.' },
        { rotulo: 'Evento de teste recebido', ok: true, detalhe: 'cobranca.falhou (teste) chegou e foi ignorado, como esperado.' },
      ],
    }
  },

  /** POST /titular/exportar {id_cliente} — art. 18 */
  async exportarTitular(idCliente: string): Promise<{ arquivo: string; linhas: number }> {
    await espera(900)
    return { arquivo: `crai-titular-${idCliente}.json`, linhas: 14 }
  },

  /** POST /titular/anonimizar {id_cliente} — art. 18; mantém os agregados */
  async anonimizarTitular(idCliente: string): Promise<{ ok: true; ciclos_anonimizados: number; mensagens_apagadas: number }> {
    await espera(1100)
    return { ok: true, ciclos_anonimizados: idCliente.length % 3, mensagens_apagadas: idCliente.length % 4 }
  },

  /** GET /titular/explicacao?id_cliente= — art. 20 (rota já existe no backend) */
  async explicacaoDecisao(idCliente: string): Promise<ExplicacaoDecisao | null> {
    await espera(700)
    if (!idCliente.trim()) return null
    return {
      cliente: idCliente,
      decisao: 'Agendar 3 tentativas de cobrança (dias 04/10, 06/10 e 07/10) e, se falharem, enviar uma mensagem de lembrete cordial por WhatsApp.',
      quando: new Date(AGORA_MS - 3 * 86_400_000).toISOString(),
      fatores: [
        { fator: 'Causa da falha: saldo insuficiente', pontos: 14 },
        { fator: 'Cliente há mais de 2 anos', pontos: 12 },
        { fator: 'Bom histórico de pagamento', pontos: 6 },
        { fator: 'Valor alto para o perfil', pontos: -5 },
      ],
      revisao_humana: 'A mensagem foi escolhida por um administrador da empresa em 07/10, não pelo sistema.',
    }
  },
}

const AGORA_MS = new Date('2026-09-30T14:20:00-03:00').getTime()
let configuracaoAtual: Configuracao = structuredClone(configuracaoPadrao)
/** A última configuração que o backend devolveu (modo real): base para o PUT parcial. */
let configuracaoLida: ConfiguracaoApi | null = null
/** Os detalhes de demonstração, numa cópia que a escolha e a regeração podem mudar. */
const detalhesAtuais: Record<number, CicloDetalhe> = structuredClone(detalhes)
let membrosAtuais: Membro[] = [...membros]
let chavesAtuais: ChaveApi[] = [...chavesApi]


let simulacaoAtual: EstadoSimulacao = estadoVazio()

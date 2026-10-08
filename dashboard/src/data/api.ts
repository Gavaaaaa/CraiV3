/**
 * A ÚNICA camada de dados do dashboard. Todas as telas passam por aqui.
 *
 * DUAS CAMADAS (Etapa 4A, integração parcial):
 *
 * - Sem `VITE_CRAI_API_URL`, tudo devolve dados de demonstração (mock.ts) com um pequeno
 *   atraso, exatamente como antes.
 * - Com `VITE_CRAI_API_URL`, as funções listadas em `ROTAS_REAIS` chamam o backend (`http.ts`)
 *   e passam a resposta pelos adaptadores (`adaptadores.ts`). AS DEMAIS CONTINUAM COM O MOCK.
 *   `emDemonstracao` diz quais blocos ainda usam dado fictício. A etiqueta "Demonstração"
 *   desses blocos só aparece na tela com `VITE_CRAI_MOSTRAR_DEMONSTRACAO=1`
 *   (`etiquetaDeDemonstracao`); o padrão é não aparecer.
 *
 * A CHAVE DE API (Rodada 2) passa por aqui uma única vez, na resposta de `criarChave`, e vai
 * direto para quem chamou: não é guardada neste módulo, nem em `localStorage`,
 * `sessionStorage`, URL ou console.
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
  adaptarAnonimizacao,
  adaptarAssistente,
  adaptarAtividade,
  adaptarBase,
  adaptarBusca,
  adaptarChave,
  adaptarChaves,
  adaptarCiclo,
  adaptarClienteRecente,
  adaptarComparacao,
  adaptarConfiguracao,
  adaptarDetalhe,
  adaptarExplicacao,
  adaptarExportacao,
  adaptarExtrato,
  adaptarFunil,
  adaptarImportacao,
  adaptarMensagem,
  adaptarMetricas,
  adaptarNaoContatar,
  adaptarOQueFunciona,
  adaptarResumoVoluntario,
  adaptarRetencaoSimulada,
  adaptarSaude,
  adaptarSerie,
  adaptarSerieDupla,
  adaptarSerieVoluntario,
  adaptarSimulacao,
  adaptarVisaoGeral,
  configuracaoParaApi,
  statusParaApi,
  type AnonimizacaoApi,
  type AnonimizacaoTitular,
  type AssistenteApi,
  type AtividadeApi,
  type BuscaApi,
  type BaseApi,
  type ChaveCriadaApi,
  type ChaveRevogadaApi,
  type CicloDetalheApi,
  type ClientesRecentesApi,
  type ConfiguracaoApi,
  type ExplicacaoApi,
  type ExportacaoApi,
  type ExportacaoTitular,
  type ExtratoApi,
  type FunilApi,
  type ImportacaoApi,
  type ListaDeChavesApi,
  type ListaDeCiclosApi,
  type MensagemApi,
  type MesVoluntarioApi,
  type MarcaNaoContatar,
  type MetricasMesApi,
  type NaoContatarApi,
  type OQueFuncionaApi,
  type ReguaXModeloApi,
  type RespostaConfiguracaoApi,
  type RetencaoSimuladaApi,
  type SaudeApi,
  type SerieApi,
  type SerieDuplaApi,
  type SerieVoluntarioApi,
  type SimulacaoApi,
  type TextoPoliticaApi,
  type VisaoGeralApi,
} from './adaptadores'
import { responder } from './assistente'
import { ErroApi } from './erros'
import { t } from '../lib/idioma'
import { API_URL, MODO_REAL, chamar, chamarTexto, definirEmpresaDev, definirPapelDev, definirPlanoDev, papelDev, planoDev } from './http'
import { avancar, diasAteProximaAcao, escolherMensagem, estadoVazio, iniciar, simularRetencao } from './simulador'
import type {
  Abordagem,
  Canal,
  ChaveApi,
  ChavesDaEmpresa,
  EmpresaDetalhe,
  ExplicacaoDecisao,
  Integracao,
  Membro,
  Papel,
  ResultadoTesteIntegracao,
  RespostaAssistente,
  ResultadoBusca,
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
 * função e acrescente o nome aqui (a etiqueta "Demonstração" do bloco some sozinha).
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
  chaves: true, // GET /integracao/chaves
  criarChave: true, // POST /integracao/chaves
  revogarChave: true, // DELETE /integracao/chaves/{id}
  // Rodada 3, Fase 1: a página do voluntário
  clientesRecentes: true, // GET /clientes/recentes
  baseClientes: true, // GET /clientes/base
  resumoVoluntario: true, // GET /metrics/voluntario/mes
  serieVoluntario: true, // GET /metrics/voluntario/serie
  comparacaoReguaModelo: true, // GET /metrics/voluntario/regua-x-modelo
  importarBase: true, // POST /clientes/importar
  // Rodada 3, Fase 2: a visão geral
  resumoVisaoGeral: true, // GET /metrics/visao-geral
  serieDupla: true, // GET /metrics/serie
  funil: true, // GET /metrics/involuntario/funil
  oQueFunciona: true, // GET /metrics/o-que-funciona
  atividade: true, // GET /atividade
  extrato: true, // GET /extrato
  // Rodada 3, Fase 3: a simulação do gateway (o cliente é fictício; o sistema que age é o de verdade)
  simulacao: true, // GET /simulacao
  simularCobranca: true, // POST /simulacao/cliente + POST /simulacao/cobrar
  simulacaoAvancar: true, // POST /simulacao/avancar {dias}
  simulacaoAvancarAteProximaAcao: true, // POST /simulacao/avancar {ate_proxima_acao}
  simulacaoEscolherMensagem: true, // POST /ciclos/{id}/mensagens/escolher, no ciclo simulado
  simulacaoLimpar: true, // DELETE /simulacao
  simularRetencao: true, // POST /simulacao/retencao
  // Rodada 3, Fase 4: o assistente
  assistente: true, // POST /assistente
  // Rodada 3, Fase 6: direitos do titular e descadastro
  exportarTitular: true, // POST /titular/exportar
  anonimizarTitular: true, // POST /titular/anonimizar
  explicacaoDecisao: true, // GET /titular/explicacao/{id}
  naoContatar: true, // POST /clientes/{id}/nao-contatar
  voltarAContatar: true, // DELETE /clientes/{id}/nao-contatar
  textoParaPolitica: true, // GET /titular/texto-para-politica
  // Rodada 4, Fase 2: a busca do topo e o extrato em arquivo
  buscar: true, // GET /busca?q=
  extratoCsv: true, // GET /extrato/csv
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

/**
 * As etiquetas "Demonstração" dos blocos fictícios (Rodada 2, Fase 3). Por decisão de
 * produto elas NÃO aparecem mais na tela; o mecanismo continua inteiro e volta com
 * `VITE_CRAI_MOSTRAR_DEMONSTRACAO=1` em `dashboard/.env.local`. O registro do que é real e
 * do que é fictício mora no `README.md` da raiz e em `ROTAS_REAIS`.
 *
 * Não vale para a Simulação do gateway: o selo "Demo" do menu, o selo da página e as
 * marcas dos dados simulados não passam por aqui e aparecem sempre.
 */
export const MOSTRAR_DEMONSTRACAO = String(import.meta.env.VITE_CRAI_MOSTRAR_DEMONSTRACAO ?? '').trim() === '1'

/** A tela pergunta: mostro a etiqueta "Demonstração" neste bloco? Só com a variável ligada. */
export function etiquetaDeDemonstracao(...funcoes: string[]): boolean {
  return MOSTRAR_DEMONSTRACAO && emDemonstracao(...funcoes)
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
/** O plano do login de desenvolvimento (premium por padrão; o essencial serve para testar o bloqueio). */
export const trocarPlanoDeDesenvolvimento = definirPlanoDev
/** A empresa fictícia do login de desenvolvimento: a da demonstração, ou `demo_testes` nos testes ao vivo. */
export const trocarEmpresaDeDesenvolvimento = definirEmpresaDev

/** O endereço da API que a aba "API" mostra: o mesmo `VITE_CRAI_API_URL`; na demonstração, um de exemplo. */
export const ENDERECO_DA_API = MODO_REAL ? API_URL : 'https://api.exemplo-crai.com.br'

/** A busca do topo só procura a partir de duas letras (a mesma regra do backend). */
export const BUSCA_MINIMO_DE_LETRAS = 2

/** O nome de uma chave: de 1 a 60 caracteres (a mesma regra do backend). */
export const NOME_DA_CHAVE_MAX = 60

/** A tela gera a chave com um clique, sem pedir nome: o nome é "Chave de API" mais a data. */
export function nomePadraoDaChave(agora: Date = new Date()): string {
  const dois = (n: number) => String(n).padStart(2, '0')
  return t('Chave de API {dia}/{mes}/{ano}', { dia: dois(agora.getDate()), mes: dois(agora.getMonth() + 1), ano: agora.getFullYear() })
}
const LIMITE_DE_CHAVES_ATIVAS = 5

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
  if (estadoTeste === 'erro') throw new ErroApi('servidor', t('Não foi possível falar com o servidor da CRAI.'))
}

export interface FiltroCiclos {
  status?: StatusTela | 'todos'
  busca?: string
  incluirSimulados?: boolean
  /** Só os ciclos que esperam a escolha da empresa agora (a lista para onde o sino leva). */
  aguardandoEscolha?: boolean
}

/** Quantos ciclos uma chamada traz. A paginação por cursor do backend ainda não é usada pela tela. */
const LIMITE_DE_CICLOS = 200

/** O parâmetro da barra "Mostrar": só vai na URL quando a pessoa pediu os dados de simulação. */
const simulados = (incluir: boolean | undefined, separador: '?' | '&'): string => (incluir ? `${separador}incluir_simulados=true` : '')

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
    desconto_anomalia_pct: null,
    dia_provavel_saldo: null,
    contribuicoes: [
      { fator: t('Causa da falha'), efeito: 'Aumentou a chance de recuperar' },
      { fator: t('Histórico de pagamento'), efeito: 'Aumentou a chance de recuperar' },
      { fator: t('Valor da cobrança'), efeito: 'Reduziu a chance de recuperar' },
    ],
    tentativas: [],
    sugestoes: [],
    modo_mensagem: null,
    escolha_ate: null,
    escolha_por: null,
    motivo_descarte: resumo.estado === 'descartado' ? t('Retorno esperado abaixo do custo da ação') : null,
    linha_do_tempo: [{ em: resumo.aberto_em, tipo: 'abertura', titulo: t('Cobrança falhou'), tom: 'danger' }],
  }
}

/** Demonstração: quantas cobranças (não simuladas) esperam a escolha agora: o número do sino e da aba. */
function pendentesDeDemonstracao(): number {
  return ciclos.filter((c) => !c.simulado && c.estado === 'aguardando_escolha').length
}

/**
 * Demonstração: a escolha de uma sugestão, pela empresa (`owner`) ou pelo modo automático. O
 * detalhe e a linha da lista mudam juntos, para o sino, a aba Mensagens e a lista concordarem.
 */
function escolherNaDemonstracao(id: number, rodada: number, abordagem: Abordagem, por: 'owner' | 'automatico'): void {
  const d = detalheDeDemonstracao(id)
  const agora = AGORA.toISOString()
  if (d && detalhesAtuais[id]) {
    detalhesAtuais[id] = {
      ...d,
      estado: 'mensagem_enviada',
      escolha_ate: null,
      escolha_por: por,
      proxima_acao: null,
      proxima_acao_descricao: null,
      sugestoes: d.sugestoes.map((s) => ({ ...s, escolhida: s.rodada === rodada && s.abordagem === abordagem })),
      linha_do_tempo: [
        ...d.linha_do_tempo.filter((e) => e.tipo !== 'aviso'),
        {
          em: agora,
          tipo: 'escolha',
          titulo: t('Mensagem escolhida'),
          detalhe: por === 'automatico' ? t('Escolhida pelo sistema: a recomendada, no modo automático.') : t('Escolhida pelo dono.'),
        },
        { em: agora, tipo: 'mensagem', titulo: t('Mensagem enviada'), tom: 'ok' },
      ],
    }
  }
  const linha = ciclos.find((c) => c.id === id)
  if (linha) Object.assign(linha, { estado: 'mensagem_enviada', proxima_acao: null, proxima_acao_descricao: null, atualizado_em: agora })
}

/**
 * Demonstração do modo automático: o que o relógio do backend faz na passagem seguinte. Cada
 * cobrança que esperava a escolha sai pela recomendada da rodada mais recente.
 */
function enviarRecomendadasNaDemonstracao(): void {
  for (const c of ciclos.filter((x) => !x.simulado && x.estado === 'aguardando_escolha')) {
    const sugestoes = detalheDeDemonstracao(c.id)?.sugestoes ?? []
    if (!sugestoes.length) continue
    const rodada = Math.max(...sugestoes.map((s) => s.rodada))
    const daRodada = sugestoes.filter((s) => s.rodada === rodada)
    const recomendada = daRodada.find((s) => s.recomendada) ?? daRodada[0]
    escolherNaDemonstracao(c.id, recomendada.rodada, recomendada.abordagem, 'automatico')
  }
}

export const api = {
  /** A empresa logada. No modo real ainda não há rota: é a empresa fictícia do login de desenvolvimento. */
  async empresa(): Promise<Empresa> {
    if (MODO_REAL) return { nome: t('Empresa de demonstração'), plano: planoDev(), papel: papelDev() }
    await espera(80)
    return empresa
  },

  /** GET /ciclos */
  async ciclos(filtro: FiltroCiclos = {}): Promise<CicloResumo[]> {
    if (real('ciclos')) {
      const parametros = new URLSearchParams({ limite: String(LIMITE_DE_CICLOS) })
      if (filtro.status && filtro.status !== 'todos') parametros.set('status', statusParaApi(filtro.status))
      // A barra "Mostrar: Simulação" junta os ciclos da simulação do gateway desta empresa.
      if (filtro.incluirSimulados) parametros.set('incluir_simulados', 'true')
      // A mesma conta do número "aguardando sua escolha": quem já escolheu não entra.
      if (filtro.aguardandoEscolha) parametros.set('aguardando_escolha', 'true')
      const r = await chamar<ListaDeCiclosApi>('GET', `/ciclos?${parametros}`)
      // A busca por nome é feita aqui: o backend busca só pelo id da recorrência.
      return filtrarPorBusca(r.ciclos.map(adaptarCiclo), filtro.busca)
    }
    await espera()
    if (vazio) return []
    return filtrarPorBusca(
      ciclos
        .filter((c) => (filtro.incluirSimulados ?? true) || !c.simulado)
        .filter((c) => !filtro.status || filtro.status === 'todos' || c.status === filtro.status)
        .filter((c) => !filtro.aguardandoEscolha || c.estado === 'aguardando_escolha'),
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
    // No modo automático ninguém escolhe: as que esperavam saem pela recomendada, como no backend.
    if (configuracaoAtual.modo_mensagem_involuntario === 'automatico') enviarRecomendadasNaDemonstracao()
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
    const motivo = d?.sugestoes[0]?.motivo_canal ?? t('Canal padrão da empresa')
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
    escolherNaDemonstracao(id, rodada, abordagem, 'owner')
    return { enviada: true, espera: null }
  },

  /** GET /metrics/involuntario/mes */
  async metricasMes(opcoes: { incluirSimulados?: boolean } = {}): Promise<MetricasMes> {
    if (real('metricasMes')) {
      // "Ciclos ativos" é de agora, não do mês: conta os ciclos em análise ou em processo.
      const [mes, ativos] = await Promise.all([
        chamar<MetricasMesApi>('GET', `/metrics/involuntario/mes${simulados(opcoes.incluirSimulados, '?')}`),
        chamar<ListaDeCiclosApi>('GET', `/ciclos?status=em_analise,em_processo&limite=${LIMITE_DE_CICLOS}${simulados(opcoes.incluirSimulados, '&')}`),
      ])
      return adaptarMetricas(mes, ativos.ciclos.length)
    }
    await espera(120)
    if (vazio) return { ...metricasMes, valor_liquido_recuperado: 0, ciclos_ativos: 0, recuperados: 0, encerrados_sem_recuperacao: 0, aguardando_escolha: 0, taxa_recuperacao: 0, proxima_acao: null }
    return { ...metricasMes, aguardando_escolha: pendentesDeDemonstracao() }
  },

  /**
   * Quantos ciclos esperam a escolha da empresa AGORA (o sino do topo). É o mesmo número de
   * `GET /metrics/involuntario/mes`, numa chamada só.
   */
  async pendenciasDeEscolha(): Promise<number> {
    if (real('metricasMes')) return (await chamar<MetricasMesApi>('GET', '/metrics/involuntario/mes')).aguardando_escolha
    await espera(60)
    return vazio ? 0 : pendentesDeDemonstracao()
  },

  /** GET /metrics/involuntario/serie?dias=30 */
  async serie(opcoes: { incluirSimulados?: boolean } = {}): Promise<PontoSerie[]> {
    if (real('serie')) return adaptarSerie(await chamar<SerieApi>('GET', `/metrics/involuntario/serie?dias=30${simulados(opcoes.incluirSimulados, '&')}`))
    await espera(120)
    if (vazio) return serie30.map((p) => ({ ...p, valor: 0 }))
    return serie30
  },

  /* ---------------- Visão geral ---------------- */

  /** GET /metrics/visao-geral?dias=30 */
  async resumoVisaoGeral(opcoes: { incluirSimulados?: boolean } = {}): Promise<ResumoVisaoGeral> {
    if (real('resumoVisaoGeral')) {
      return adaptarVisaoGeral(await chamar<VisaoGeralApi>('GET', `/metrics/visao-geral?dias=30${simulados(opcoes.incluirSimulados, '&')}`))
    }
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
      piloto: false,
    }
  },

  /** GET /metrics/serie?dias=30 — involuntário (Etapa 2) e voluntário (Etapa 3) juntos */
  async serieDupla(opcoes: { incluirSimulados?: boolean } = {}): Promise<PontoSerieDupla[]> {
    if (real('serieDupla')) {
      return adaptarSerieDupla(await chamar<SerieDuplaApi>('GET', `/metrics/serie?dias=30${simulados(opcoes.incluirSimulados, '&')}`))
    }
    await espera(160)
    return serieDupla(opcoes.incluirSimulados ?? false).map((p) => (vazio ? { ...p, involuntario: 0, voluntario: 0 } : p))
  },

  /** GET /metrics/involuntario/funil?mes= (sem o mês: o corrente) */
  async funil(opcoes: { incluirSimulados?: boolean } = {}): Promise<Funil> {
    if (real('funil')) return adaptarFunil(await chamar<FunilApi>('GET', `/metrics/involuntario/funil${simulados(opcoes.incluirSimulados, '?')}`))
    await espera(180)
    if (vazio) return { ...funil, etapas: funil.etapas.map((e) => ({ ...e, chegaram: 0, valor: 0, recuperados_aqui: 0, valor_recuperado_aqui: 0 })), desfecho: { recuperados: 0, encerrados: 0, em_andamento: 0 } }
    return funil
  },

  /** GET /metrics/o-que-funciona?dias=30 */
  async oQueFunciona(opcoes: { incluirSimulados?: boolean } = {}): Promise<OQueFunciona> {
    if (real('oQueFunciona')) {
      return adaptarOQueFunciona(await chamar<OQueFuncionaApi>('GET', `/metrics/o-que-funciona?dias=30${simulados(opcoes.incluirSimulados, '&')}`))
    }
    await espera(200)
    if (vazio) return { causas: [], ofertas: [], canais: [] }
    return oQueFunciona
  },

  /** GET /atividade?limite= */
  async atividade(opcoes: { incluirSimulados?: boolean; limite?: number } = {}): Promise<Atividade[]> {
    if (real('atividade')) {
      return adaptarAtividade(await chamar<AtividadeApi>('GET', `/atividade?limite=${opcoes.limite ?? 7}${simulados(opcoes.incluirSimulados, '&')}`))
    }
    await espera(150)
    if (vazio) return []
    return atividades.filter((a) => opcoes.incluirSimulados || !a.simulado).slice(0, opcoes.limite ?? 7)
  },

  /** GET /health: o relógio, os modelos, o redator e, por ir com o token, a base da empresa */
  async saude(): Promise<SaudeSistema> {
    if (real('saude')) return adaptarSaude(await chamar<SaudeApi>('GET', '/health'), saude)
    await espera(90)
    if (vazio) return { ...saude, base: null }
    return saude
  },

  /**
   * GET /extrato?mes= (o CSV é montado na tela, a partir das mesmas linhas). É a única rota
   * que traz a taxa da CRAI, e o backend só a atende para dono e administrador: para o
   * membro a chamada falha com `sem_permissao`, e a tela diz isso no lugar da tabela.
   */
  async extrato(opcoes: { incluirSimulados?: boolean } = {}): Promise<LinhaExtrato[]> {
    if (real('extrato')) return adaptarExtrato(await chamar<ExtratoApi>('GET', `/extrato${simulados(opcoes.incluirSimulados, '?')}`))
    await espera(200)
    if (vazio) return []
    return extrato.filter((l) => opcoes.incluirSimulados || !l.simulado)
  },

  /* ---------------- Simulação do gateway ---------------- */
  // Rotas da Etapa 3, Bloco 2 (nomes propostos; ver a lista no HANDOFF). Estado guardado por empresa.

  /** GET /simulacao */
  async simulacao(): Promise<EstadoSimulacao> {
    if (real('simulacao')) return adaptarSimulacao(await chamar<SimulacaoApi>('GET', '/simulacao'))
    await espera(60)
    return simulacaoAtual
  },

  /** POST /simulacao/cliente + POST /simulacao/cobrar — cria o cliente fictício e dispara a cobrança */
  async simularCobranca(cliente: ClienteFicticio): Promise<EstadoSimulacao> {
    if (real('simularCobranca')) {
      // O corpo leva só o que o formulário tem: nome inventado, mensalidade, perfil e a verdade escondida.
      await chamar<SimulacaoApi>('POST', '/simulacao/cliente', {
        nome: cliente.nome,
        mensalidade: cliente.mensalidade,
        perfil: cliente.perfil,
        verdade: { dias_ate_saldo: cliente.verdade.dias_ate_saldo, chance_pagar: cliente.verdade.chance_pagar, vai_revogar: cliente.verdade.vai_revogar },
      })
      return adaptarSimulacao(await chamar<SimulacaoApi>('POST', '/simulacao/cobrar'))
    }
    await espera(200)
    simulacaoAtual = iniciar(cliente)
    return simulacaoAtual
  },

  /** POST /simulacao/avancar {dias} — relógio simulado da empresa */
  async simulacaoAvancar(dias: number): Promise<EstadoSimulacao> {
    if (real('simulacaoAvancar')) return adaptarSimulacao(await chamar<SimulacaoApi>('POST', '/simulacao/avancar', { dias }))
    await espera(180)
    simulacaoAtual = avancar(simulacaoAtual, dias)
    return simulacaoAtual
  },

  /** POST /simulacao/avancar {ate_proxima_acao: true} */
  async simulacaoAvancarAteProximaAcao(): Promise<EstadoSimulacao> {
    if (real('simulacaoAvancarAteProximaAcao')) return adaptarSimulacao(await chamar<SimulacaoApi>('POST', '/simulacao/avancar', { ate_proxima_acao: true }))
    await espera(180)
    simulacaoAtual = avancar(simulacaoAtual, diasAteProximaAcao(simulacaoAtual))
    return simulacaoAtual
  },

  /** POST /ciclos/{id}/mensagens/escolher (a mesma rota da Etapa 2, no ciclo simulado) */
  async simulacaoEscolherMensagem(abordagem: Abordagem): Promise<EstadoSimulacao> {
    if (real('simulacaoEscolherMensagem')) {
      // A escolha vale para o ciclo simulado que o backend tem AGORA, na última rodada de sugestões.
      const atual = await chamar<SimulacaoApi>('GET', '/simulacao')
      const id = atual.ciclo?.ciclo.id
      if (!id) throw new ErroApi('conflito', t('Esta simulação não tem mensagem esperando escolha. Atualize a página.'))
      const rodada = Math.max(1, ...atual.ciclo!.mensagens.map((m) => m.rodada))
      await chamar('POST', `/ciclos/${id}/mensagens/escolher`, { rodada, abordagem: abordagemParaApi(abordagem) })
      return adaptarSimulacao(await chamar<SimulacaoApi>('GET', '/simulacao'))
    }
    await espera(250)
    simulacaoAtual = escolherMensagem(simulacaoAtual, abordagem, 'owner')
    return simulacaoAtual
  },

  /** DELETE /simulacao — limpa os dados fictícios da empresa */
  async simulacaoLimpar(): Promise<EstadoSimulacao> {
    if (real('simulacaoLimpar')) return adaptarSimulacao(await chamar<SimulacaoApi>('DELETE', '/simulacao'))
    await espera(80)
    simulacaoAtual = estadoVazio()
    return simulacaoAtual
  },

  /** POST /simulacao/retencao — cliente fictício em risco; o aceite vem da propensão escondida */
  async simularRetencao(cliente: ClienteRiscoFicticio): Promise<ResultadoRetencaoSimulada> {
    if (real('simularRetencao')) {
      return adaptarRetencaoSimulada(
        await chamar<RetencaoSimuladaApi>('POST', '/simulacao/retencao', { nome: cliente.nome, mrr: cliente.mrr, sinais: cliente.sinais, propensao: cliente.propensao }),
      )
    }
    await espera(500)
    return simularRetencao(cliente)
  },

  /* ---------------- Voluntário ---------------- */

  /** GET /clientes/base (resumo da base importada); null quando a empresa não tem cliente nenhum */
  async baseClientes(): Promise<BaseClientes | null> {
    if (real('baseClientes')) return adaptarBase(await chamar<BaseApi>('GET', '/clientes/base'))
    await espera(80)
    if (vazio) return null
    return baseClientes
  },

  /** GET /clientes/recentes?limite=10 */
  async clientesRecentes(opcoes: { incluirSimulados?: boolean; limite?: number; cliente?: string } = {}): Promise<ClienteRisco[]> {
    if (real('clientesRecentes')) {
      // `cliente`: só aquele cliente, esteja ou não entre os mais recentes (a busca do topo leva a ele).
      const so = opcoes.cliente ? `&cliente=${encodeURIComponent(opcoes.cliente)}` : ''
      const r = await chamar<ClientesRecentesApi>('GET', `/clientes/recentes?limite=${opcoes.limite ?? 10}${simulados(opcoes.incluirSimulados, '&')}${so}`)
      return r.clientes.map(adaptarClienteRecente)
    }
    await espera(200)
    if (vazio) return []
    return clientesRisco
      .filter((c) => opcoes.incluirSimulados || !c.simulado)
      .filter((c) => !opcoes.cliente || c.id === opcoes.cliente)
      .slice(0, opcoes.limite ?? 10)
  },

  /** GET /busca?q= — clientes e ciclos da empresa, pelo nome ou pelo identificador (Rodada 4) */
  async buscar(q: string): Promise<ResultadoBusca> {
    const texto = q.trim()
    if (texto.length < BUSCA_MINIMO_DE_LETRAS) return { clientes: [], ciclos: [] }
    if (real('buscar')) return adaptarBusca(await chamar<BuscaApi>('GET', `/busca?q=${encodeURIComponent(texto)}`))
    await espera(120)
    const termo = texto.toLowerCase()
    return {
      clientes: clientesRisco
        .filter((c) => !c.simulado && (c.nome.toLowerCase().includes(termo) || c.id.toLowerCase().startsWith(termo)))
        .slice(0, 8)
        .map((c) => ({ id: c.id, nome: c.nome, mrr: c.mrr, cancelado: false, nao_contatar: c.nao_contatar })),
      ciclos: ciclos
        .filter((c) => !c.simulado && ((c.cliente ?? '').toLowerCase().includes(termo) || c.id_recorrencia.toLowerCase().startsWith(termo)))
        .slice(0, 8)
        .map((c) => ({ id: c.id, cliente: c.cliente, id_recorrencia: c.id_recorrencia, status: c.status, valor_cobranca: c.valor_cobranca, causa_legivel: c.causa_legivel ?? null })),
    }
  },

  /**
   * GET /extrato/csv — o extrato do mês em arquivo, montado pelo backend (e gravado no registro
   * de acesso). null na demonstração: sem backend, a tela monta o arquivo com as linhas que mostra.
   */
  async extratoCsv(opcoes: { incluirSimulados?: boolean } = {}): Promise<string | null> {
    if (real('extratoCsv')) return chamarTexto(`/extrato/csv${simulados(opcoes.incluirSimulados, '?')}`)
    return null
  },

  /** GET /metrics/voluntario/mes */
  async resumoVoluntario(opcoes: { incluirSimulados?: boolean } = {}): Promise<ResumoVoluntario> {
    if (real('resumoVoluntario')) {
      return adaptarResumoVoluntario(await chamar<MesVoluntarioApi>('GET', `/metrics/voluntario/mes${simulados(opcoes.incluirSimulados, '?')}`))
    }
    await espera(120)
    if (vazio) return { ...resumoVoluntario, valor_liquido_mantido: 0, clientes_mantidos: 0, estornos: 0, grave: 0, preocupante: 0, ofertas_enviadas: 0, ofertas_aceitas: 0 }
    return resumoVoluntario
  },

  /** GET /metrics/voluntario/serie?dias=30 (só o voluntário) */
  async serieVoluntario(opcoes: { incluirSimulados?: boolean } = {}): Promise<PontoSerie[]> {
    if (real('serieVoluntario')) {
      return adaptarSerieVoluntario(await chamar<SerieVoluntarioApi>('GET', `/metrics/voluntario/serie?dias=30${simulados(opcoes.incluirSimulados, '&')}`))
    }
    await espera(160)
    return serieDupla(opcoes.incluirSimulados ?? false).map((p) => ({ dia: p.dia, valor: vazio ? 0 : p.voluntario }))
  },

  /** GET /metrics/voluntario/regua-x-modelo?dias=30; null quando não há desfecho suficiente para comparar */
  async comparacaoReguaModelo(): Promise<ComparacaoReguaModelo | null> {
    if (real('comparacaoReguaModelo')) return adaptarComparacao(await chamar<ReguaXModeloApi>('GET', '/metrics/voluntario/regua-x-modelo?dias=30'))
    await espera(150)
    if (vazio) return null
    return comparacaoReguaModelo
  },

  /** POST /clientes/importar (multipart). Na demonstração só lê o nome e o tamanho; nada sobe. */
  async importarBase(arquivo: File): Promise<ResultadoImportacao> {
    if (real('importarBase')) {
      const corpo = new FormData()
      corpo.append('arquivo', arquivo, arquivo.name)
      return adaptarImportacao(arquivo.name, await chamar<ImportacaoApi>('POST', '/clientes/importar', corpo))
    }
    await espera(1400)
    const linhas = Math.max(12, Math.round(arquivo.size / 96))
    return {
      arquivo: arquivo.name,
      linhas,
      importados: linhas,
      rejeitados: 0,
      novos: Math.round(linhas * 0.04),
      atualizados: Math.round(linhas * 0.31),
      sem_id_recorrencia: Math.round(linhas * 0.02),
      sem_comportamento: null,
      avisos: [t('3 linhas sem e-mail nem telefone: ficam com "Sem canal disponível".')],
      demonstracao: true,
    }
  },

  /* ---------------- Assistente ---------------- */

  /** POST /assistente {pergunta}. As conversas não são guardadas: cada pergunta vai sozinha. */
  async assistente(pergunta: string): Promise<RespostaAssistente> {
    // Vai só a pergunta: o backend não recebe a conversa, e não a guarda.
    if (real('assistente')) return adaptarAssistente(await chamar<AssistenteApi>('POST', '/assistente', { pergunta }))
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

  /** GET /integracao (o webhook; as chaves ficam na aba API) */
  async integracao(): Promise<Integracao> {
    await espera(120)
    return { webhook: { url: 'https://api.nimbusflow.com.br/crai/webhook', segredo_inicio: 'whsec_9a1f', ultimo_evento: new Date(AGORA_MS - 40 * 60_000).toISOString() } }
  },

  /* ---------------- Chaves de API ---------------- */

  /** GET /integracao/chaves — a lista, sem a chave inteira */
  async chaves(): Promise<ChavesDaEmpresa> {
    if (real('chaves')) return adaptarChaves(await chamar<ListaDeChavesApi>('GET', '/integracao/chaves'))
    await espera(120)
    const lista = vazio ? [] : chavesAtuais
    return {
      chaves: lista,
      ativas: lista.filter((c) => !c.revogada_em).length,
      limite_ativas: LIMITE_DE_CHAVES_ATIVAS,
      pode_revogar: empresa.papel !== 'membro',
      plano_permite_gerar: empresa.plano === 'premium',
      pode_gerar: empresa.papel !== 'membro' && empresa.plano === 'premium',
    }
  },

  /**
   * POST /integracao/chaves {nome} — a chave inteira aparece UMA vez, nesta resposta. Quem
   * chama mostra e esquece: nada aqui a guarda. Sem `nome`, vai o nome padrão (a tela gera
   * com um clique, sem perguntar).
   */
  async criarChave(nome: string = nomePadraoDaChave()): Promise<{ chave: ChaveApi; inteira: string }> {
    if (real('criarChave')) {
      const r = await chamar<ChaveCriadaApi>('POST', '/integracao/chaves', { nome })
      return { chave: adaptarChave(r.chave), inteira: r.chave_inteira }
    }
    await espera(400)
    const limpo = nome.trim()
    if (!limpo || limpo.length > NOME_DA_CHAVE_MAX) throw new ErroApi('invalido', t('O servidor recusou os dados enviados. Confira os campos e tente de novo.'), 422, 'nome_invalido')
    if (chavesAtuais.filter((c) => !c.revogada_em).length >= LIMITE_DE_CHAVES_ATIVAS) {
      throw new ErroApi('conflito', t('A empresa já tem o máximo de chaves ativas. Revogue uma para gerar outra.'), 409, 'limite_de_chaves')
    }
    // Chave de demonstração: tem a forma de uma chave, começa por DEMO e não abre nada.
    const letras = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    const sorteio = Array.from({ length: 39 }, () => letras[Math.floor(Math.random() * letras.length)]).join('')
    const inteira = `crai_live_DEMO${sorteio}`
    const chave: ChaveApi = { id: `k${Date.now()}`, nome: limpo, inicio: inteira.slice(0, 14), final: inteira.slice(-4), criada_em: new Date().toISOString(), ultimo_uso: null, revogada_em: null }
    chavesAtuais = [chave, ...chavesAtuais]
    return { chave, inteira }
  },

  /** DELETE /integracao/chaves/{id} — revogação imediata; revogar de novo mantém a data original */
  async revogarChave(id: string): Promise<ChaveApi> {
    if (real('revogarChave')) {
      return adaptarChave((await chamar<ChaveRevogadaApi>('DELETE', `/integracao/chaves/${encodeURIComponent(id)}`)).chave)
    }
    await espera(250)
    const atual = chavesAtuais.find((c) => c.id === id)
    if (!atual) throw new ErroApi('nao_encontrado', t('Não encontramos o que você pediu. Pode ter sido removido ou não ser da sua empresa.'), 404, 'chave_nao_encontrada')
    const revogada: ChaveApi = atual.revogada_em ? atual : { ...atual, revogada_em: new Date().toISOString() }
    chavesAtuais = chavesAtuais.map((c) => (c.id === id ? revogada : c))
    return revogada
  },

  /** POST /integracao/testar */
  async testarIntegracao(): Promise<ResultadoTesteIntegracao> {
    await espera(1500)
    return {
      ok: true,
      passos: [
        { rotulo: t('Chave live aceita'), ok: true, detalhe: t('crai_live_7f3a autenticou.') },
        { rotulo: t('Webhook respondeu'), ok: true, detalhe: t('HTTP 200 em 180 ms, assinatura conferida.') },
        { rotulo: t('Evento de teste recebido'), ok: true, detalhe: t('cobranca.falhou (teste) chegou e foi ignorado, como esperado.') },
      ],
    }
  },

  /** POST /titular/exportar {customer_id_externo} — art. 18 */
  async exportarTitular(idCliente: string): Promise<ExportacaoTitular> {
    if (real('exportarTitular')) return adaptarExportacao(await chamar<ExportacaoApi>('POST', '/titular/exportar', { customer_id_externo: idCliente }))
    await espera(900)
    return { arquivo: `crai-titular-${idCliente}.json`, linhas: 14 }
  },

  /** POST /titular/anonimizar {customer_id_externo} — art. 18; mantém os agregados */
  async anonimizarTitular(idCliente: string): Promise<AnonimizacaoTitular> {
    if (real('anonimizarTitular')) return adaptarAnonimizacao(await chamar<AnonimizacaoApi>('POST', '/titular/anonimizar', { customer_id_externo: idCliente }))
    await espera(1100)
    return { ok: true, ciclos_anonimizados: idCliente.length % 3, mensagens_apagadas: idCliente.length % 4 }
  },

  /** POST /clientes/{id}/nao-contatar — nenhuma mensagem sai mais para este cliente */
  async naoContatar(idCliente: string): Promise<MarcaNaoContatar> {
    if (real('naoContatar')) return adaptarNaoContatar(await chamar<NaoContatarApi>('POST', `/clientes/${encodeURIComponent(idCliente)}/nao-contatar`))
    await espera(500)
    return { marcado: true, ja_estava: false, desde: new Date(AGORA_MS).toISOString() }
  },

  /** DELETE /clientes/{id}/nao-contatar — a volta */
  async voltarAContatar(idCliente: string): Promise<MarcaNaoContatar> {
    if (real('voltarAContatar')) return adaptarNaoContatar(await chamar<NaoContatarApi>('DELETE', `/clientes/${encodeURIComponent(idCliente)}/nao-contatar`))
    await espera(500)
    return { marcado: false, ja_estava: true, desde: null }
  },

  /** GET /titular/texto-para-politica — o texto pronto, com os prazos da empresa. null na demonstração (a tela usa o de exemplo). */
  async textoParaPolitica(): Promise<{ titulo: string; texto: string } | null> {
    if (real('textoParaPolitica')) {
      const r = await chamar<TextoPoliticaApi>('GET', '/titular/texto-para-politica')
      return { titulo: r.titulo, texto: r.texto }
    }
    await espera(60)
    return null
  },

  /** GET /titular/explicacao/{id} — art. 20. O identificador pode ser o do cliente ou o da cobrança. */
  async explicacaoDecisao(idCliente: string): Promise<ExplicacaoDecisao | null> {
    if (real('explicacaoDecisao')) {
      // A trilha guarda a cobrança pelo id da recorrência e o cliente como `user:<id>`: a tela
      // aceita os dois, e tenta nas duas formas. 404 nas duas: não há decisão registrada.
      for (const sujeito of [idCliente, `user:${idCliente}`]) {
        try {
          const achada = adaptarExplicacao(await chamar<ExplicacaoApi>('GET', `/titular/explicacao/${encodeURIComponent(sujeito)}?limite=1`))
          if (achada) return achada
        } catch (e) {
          if (!(e instanceof ErroApi) || e.status !== 404) throw e
        }
      }
      return null
    }
    await espera(700)
    if (!idCliente.trim()) return null
    return {
      cliente: idCliente,
      decisao: t('Agendar 3 tentativas de cobrança (dias 04/10, 06/10 e 07/10) e, se falharem, enviar uma mensagem de lembrete cordial por WhatsApp.'),
      quando: new Date(AGORA_MS - 3 * 86_400_000).toISOString(),
      fatores: [
        { fator: t('Causa da falha: saldo insuficiente'), pontos: 14 },
        { fator: t('Cliente há mais de 2 anos'), pontos: 12 },
        { fator: t('Bom histórico de pagamento'), pontos: 6 },
        { fator: t('Valor alto para o perfil'), pontos: -5 },
      ],
      revisao_humana: t('A mensagem foi escolhida por um administrador da empresa em 07/10, não pelo sistema.'),
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

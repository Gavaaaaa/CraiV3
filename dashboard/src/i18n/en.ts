/**
 * O dicionário do inglês. A chave é o texto em português, EXATAMENTE como está no código
 * (com os marcadores `{nome}` das partes que mudam); o valor é a tradução. Um texto sem
 * tradução aparece em português: nada quebra, e o teste `i18n.test.ts` acusa.
 *
 * Dividido por área para cada parte do painel ter o seu arquivo.
 */
import { backend } from './en/backend'
import { base } from './en/base'
import { ciclos } from './en/ciclos'
import { configuracao } from './en/configuracao'
import { dados } from './en/dados'
import { simulacao } from './en/simulacao'
import { textos } from './en/textos'
import { visao } from './en/visao'

// `backend` vem primeiro: num texto igual, vale a tradução escrita para a tela.
export const EN: Record<string, string> = { ...backend, ...base, ...dados, ...textos, ...visao, ...ciclos, ...configuracao, ...simulacao }

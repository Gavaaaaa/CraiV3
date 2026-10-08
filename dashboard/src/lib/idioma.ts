import { useSyncExternalStore } from 'react'
import { EN } from '../i18n/en'

/**
 * O idioma das telas do painel: português (o padrão) ou inglês.
 *
 * Como o tema, é preferência de quem olha: fica no `localStorage` deste navegador, na chave
 * `crai_idioma` (`pt` ou `en`), e nada vai ao backend. Sem `localStorage`, vale o português.
 *
 * O texto de origem é o português do código; `t()` troca pelo inglês do dicionário
 * (`i18n/en.ts`) quando o idioma é inglês. Trocar o idioma RECARREGA a página: assim os textos
 * definidos fora dos componentes (rótulos, dados de demonstração) também são lidos de novo.
 *
 * O que vem pronto do backend com vocabulário fixo (rótulos, fatores, o motivo do risco, as
 * linhas da simulação) é traduzido por `doBackend()` (`lib/doBackend.ts`), nos adaptadores.
 * Não muda de idioma: a explicação de cada decisão (o registro do Art. 20), as respostas do
 * assistente escritas pelo modelo e as mensagens enviadas aos clientes finais, que continuam em
 * português.
 */
export type Idioma = 'pt' | 'en'

export const CHAVE_DO_IDIOMA = 'crai_idioma'
export const IDIOMA_PADRAO: Idioma = 'pt'

export function lerIdiomaGuardado(): Idioma {
  try {
    return globalThis.localStorage?.getItem(CHAVE_DO_IDIOMA) === 'en' ? 'en' : IDIOMA_PADRAO
  } catch {
    return IDIOMA_PADRAO
  }
}

let atual: Idioma = lerIdiomaGuardado()
if (typeof document !== 'undefined') document.documentElement.lang = atual === 'en' ? 'en' : 'pt-BR'

/** O idioma em uso agora. */
export function idiomaAtual(): Idioma {
  return atual
}

/** O locale dos números e das datas: `pt-BR` ou `en-US`. */
export function localeAtual(): string {
  return atual === 'en' ? 'en-US' : 'pt-BR'
}

/**
 * Troca o idioma, guarda a escolha neste navegador e recarrega a página. `recarregar: false`
 * é para os testes, que trocam o idioma sem página para recarregar.
 */
export function definirIdioma(novo: Idioma, { recarregar = true }: { recarregar?: boolean } = {}): void {
  try {
    globalThis.localStorage?.setItem(CHAVE_DO_IDIOMA, novo)
  } catch {
    /* sem localStorage, o idioma vale só até a página recarregar */
  }
  atual = novo
  if (typeof document !== 'undefined') document.documentElement.lang = novo === 'en' ? 'en' : 'pt-BR'
  ouvintes.forEach((f) => f())
  if (recarregar && typeof window !== 'undefined') window.location.reload()
}

const ouvintes = new Set<() => void>()
function assinar(f: () => void): () => void {
  ouvintes.add(f)
  return () => ouvintes.delete(f)
}

/** O idioma em uso, para os componentes, e a função que troca. */
export function useIdioma(): [Idioma, (novo: Idioma) => void] {
  const idioma = useSyncExternalStore(assinar, idiomaAtual, idiomaAtual)
  return [idioma, (novo) => definirIdioma(novo)]
}

/**
 * O texto no idioma em uso. `pt` é o texto em português, exatamente como no código; as partes
 * que mudam vão como `{nome}` e chegam em `vars`:
 *
 *     t('{n} aguardando sua escolha', { n: 3 })
 */
export function t(pt: string, vars?: Record<string, string | number>): string {
  const texto = atual === 'en' ? (EN[pt] ?? pt) : pt
  return vars ? texto.replace(/\{(\w+)\}/g, (marca, nome: string) => (nome in vars ? String(vars[nome]) : marca)) : texto
}

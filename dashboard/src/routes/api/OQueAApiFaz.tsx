import { motion } from 'framer-motion'
import { useEffect } from 'react'
import { IconClose } from '../../components/icons/Icons'
import { useReducedMotion } from '../../lib/useReducedMotion'

/** O texto do painel, como foi aprovado. Mudou o texto, muda aqui e só aqui. */
export const O_QUE_A_API_FAZ = {
  titulo: 'O que a API faz',
  abertura: 'A API liga o seu sistema ao da CRAI. Com ela, a sua base de clientes se atualiza sozinha, sem ninguém precisar subir planilha.',
  blocos: [
    {
      titulo: 'O que o seu sistema passa a fazer',
      itens: [
        'Cadastrar e atualizar clientes, um por vez ou vários de uma vez.',
        'Avisar quando um cliente muda de plano ou de valor.',
        'Avisar quando um cliente cancela.',
        'Avisar o que o cliente fez, como abrir a página de cancelamento.',
      ],
    },
    {
      titulo: 'Por que isso importa',
      itens: [
        'A lista de clientes em risco fica sempre em dia.',
        'Cada cancelamento avisado ajuda o sistema a reconhecer os próximos.',
      ],
    },
    {
      titulo: 'O que é a chave',
      itens: [
        'É a senha do seu sistema para falar com a CRAI.',
        'Ela aparece uma única vez, na hora em que você gera. Guarde em local seguro.',
        'Quem tiver a chave pode alterar a sua base de clientes. Se ela vazar, revogue aqui e gere outra: a antiga para de funcionar na hora.',
      ],
    },
    {
      titulo: 'O que a chave não faz',
      itens: [
        'Não dá acesso a este painel.',
        'Não envia mensagens por conta própria. Ela só avisa a CRAI do que aconteceu; quem decide se e quando falar com o cliente é a CRAI, pelas regras da sua configuração.',
        'Não mostra dados de outras empresas.',
      ],
    },
    {
      titulo: 'Cuidado com os dados',
      itens: ['Envie só os campos que a documentação pede. Nunca envie CPF, dados de cartão ou senhas.'],
    },
  ],
} as const

/** Painel lateral com a explicação. Fecha com Esc, com clique fora e com o botão de fechar. */
export function PainelOQueAApiFaz({ onFechar }: { onFechar: () => void }) {
  const reduzido = useReducedMotion()
  useEffect(() => {
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onFechar()
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [onFechar])

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label={O_QUE_A_API_FAZ.titulo}>
      {/* O fundo é um botão: clicar fora do painel fecha */}
      <button type="button" aria-label="Fechar a explicação" onClick={onFechar} className="absolute inset-0 bg-ink/60 backdrop-blur-[2px]" />
      <motion.aside
        initial={reduzido ? false : { x: 40, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}
        className="scroll-fino absolute inset-y-0 right-0 w-full max-w-[520px] overflow-y-auto border-l border-line bg-card shadow-[0_0_80px_rgba(0,0,0,0.6)]"
      >
        <div className="flex flex-col gap-5 p-6">
          <header className="flex items-start justify-between gap-4">
            <h3 className="t-h2 text-paper">{O_QUE_A_API_FAZ.titulo}</h3>
            <button type="button" onClick={onFechar} aria-label="Fechar" className="rounded-[8px] p-2 text-silver hover:bg-paper/[0.06] hover:text-paper">
              <IconClose />
            </button>
          </header>
          <p className="text-normal leading-[1.6] text-paper/90">{O_QUE_A_API_FAZ.abertura}</p>
          {O_QUE_A_API_FAZ.blocos.map((bloco) => (
            <section key={bloco.titulo}>
              <h4 className="text-apoio font-[640] text-paper">{bloco.titulo}</h4>
              <ul className="mt-2 flex flex-col gap-1.5">
                {bloco.itens.map((item) => (
                  <li key={item} className="flex items-start gap-2 text-apoio leading-[1.55] text-silver">
                    <span className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-orange" aria-hidden="true" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      </motion.aside>
    </div>
  )
}

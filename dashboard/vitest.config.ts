import { defineConfig } from 'vitest/config'

// Testes unitários (`npm test`): adaptadores, camada HTTP com fetch de mentira e o modo de
// demonstração. Não precisam de backend. A URL da API é zerada aqui para que um
// `.env.local` da máquina não ligue o modo real nestes testes; a variável das etiquetas
// "Demonstração" também, pelo mesmo motivo (os testes que precisam dela a ligam).
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
    exclude: ['src/**/*.vivo.test.{ts,tsx}', 'node_modules/**'],
    env: { VITE_CRAI_API_URL: '', VITE_CRAI_MOSTRAR_DEMONSTRACAO: '' },
  },
})

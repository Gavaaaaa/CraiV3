import { defineConfig } from 'vitest/config'

// Teste de integração AO VIVO (`npm run test:vivo`), fora do `npm test` comum: precisa do
// backend no ar em modo de desenvolvimento e da semente rodada. O endereço vem de
// VITE_CRAI_API_URL; sem ela, http://127.0.0.1:8000.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.vivo.test.{ts,tsx}'],
    env: { VITE_CRAI_API_URL: process.env.VITE_CRAI_API_URL || 'http://127.0.0.1:8000' },
    testTimeout: 60_000,
    hookTimeout: 60_000,
    fileParallelism: false,
  },
})

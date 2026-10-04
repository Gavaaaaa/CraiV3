import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// O endereço do backend vem de VITE_CRAI_API_URL (nunca escrito no código).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173 },
})

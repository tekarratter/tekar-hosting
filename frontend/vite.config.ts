import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/',
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://localhost:9900', ws: true, changeOrigin: true },
      '/mcp': { target: 'http://localhost:9900', changeOrigin: true },
    },
  },
  build: { chunkSizeWarningLimit: 2000 },
})

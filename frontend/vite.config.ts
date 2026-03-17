import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    allowedHosts: true,
    proxy: {
      '/auth': 'http://localhost:8000',
      '/manuscripts': 'http://localhost:8000',
      '/bible': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/stripe': 'http://localhost:8000',
    },
  },
})

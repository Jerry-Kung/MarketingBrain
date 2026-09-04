import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // 开发时把 /api 代理到后端（默认 19783）
      '/api': {
        target: 'http://localhost:19783',
        changeOrigin: true,
      },
    },
  },
})

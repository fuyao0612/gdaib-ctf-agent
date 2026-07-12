import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { '/api': 'http://127.0.0.1:8000' } },
  preview: { host: '0.0.0.0', port: 4173 },
  test: { environment: 'jsdom', setupFiles: './src/test-setup.ts', globals: true, include: ['src/**/*.test.{ts,tsx}'] },
})

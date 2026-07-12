import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e', timeout: 30_000, fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  webServer: [
    { command: 'python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000', url: 'http://127.0.0.1:8000/api/v1/health', reuseExistingServer: true, cwd: '../..' },
    { command: 'npm run dev -- --port 5173', url: 'http://127.0.0.1:5173', reuseExistingServer: true },
  ],
})

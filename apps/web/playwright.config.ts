import { defineConfig } from '@playwright/test'
import { randomBytes } from 'node:crypto'

process.env.YUWANG_E2E_ADMIN_TOKEN ??= randomBytes(32).toString('base64url')

export default defineConfig({
  testDir: './e2e', timeout: 60_000, fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  webServer: [
    { command: 'python tests/protocol_provider_server.py --port 8899', url: 'http://127.0.0.1:8899/health', reuseExistingServer: false, cwd: '../..' },
    { command: 'python tests/run_e2e_api.py', url: 'http://127.0.0.1:8000/api/v1/health', reuseExistingServer: false, cwd: '../..' },
    { command: 'npm run dev -- --port 5173', url: 'http://127.0.0.1:5173', reuseExistingServer: true },
  ],
})

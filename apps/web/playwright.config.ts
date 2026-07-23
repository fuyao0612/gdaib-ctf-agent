import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './e2e', timeout: 60_000, fullyParallel: false,
  use: { baseURL: 'http://127.0.0.1:5173', trace: 'retain-on-failure' },
  webServer: [
    { command: 'python tests/protocol_provider_server.py --port 8899', url: 'http://127.0.0.1:8899/health', reuseExistingServer: false, cwd: '../..' },
    { command: 'python tests/run_e2e_api.py', url: 'http://127.0.0.1:8000/api/v1/health', reuseExistingServer: false, cwd: '../..' },
    // 直接启动 Vite，避免 Windows 上 npm.cmd 先退出、遗留 Node 子进程，导致
    // Playwright 在所有断言通过后仍无法可靠回收隔离的开发服务器。
    { command: 'node node_modules/vite/bin/vite.js --host 0.0.0.0 --port 5173', url: 'http://127.0.0.1:5173', reuseExistingServer: true },
  ],
})

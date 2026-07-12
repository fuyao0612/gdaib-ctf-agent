import { expect, test } from '@playwright/test'

async function configureProtocolProvider(page: import('@playwright/test').Page) {
  if (await page.locator('.settings-backdrop').count() === 0) await page.locator('.settings-button').click()
  await expect(page.getByText('首次配置向导')).toBeVisible()
  await page.locator('.admin-login input').fill(process.env.YUWANG_E2E_ADMIN_TOKEN!)
  await page.locator('.admin-login button').click()
  await expect(page.locator('.settings-content')).toBeVisible()

  const form = page.locator('.settings-form').first()
  await form.locator('select').first().selectOption('custom')
  const inputs = form.locator('input')
  await inputs.nth(0).fill('Protocol acceptance provider')
  await inputs.nth(1).fill('http://127.0.0.1:8899/v1')
  await inputs.nth(2).fill('protocol-test-model')
  await inputs.nth(3).fill(`protocol-${Date.now()}`)
  await form.locator('.check-row input').nth(1).check()
  await form.locator('button.primary').click()
  const providerRow = page.locator('.settings-content > section').first().locator('.provider-row')
  await expect(providerRow).toContainText('Protocol acceptance provider')
  await providerRow.locator('button').first().click()
  await expect(page.locator('.settings-notice')).toBeVisible()
  await page.locator('.settings-panel > header button').click()
}

async function uploadAndRun(page: import('@playwright/test').Page, message: string, file: string) {
  await page.locator('input[type="file"]').setInputFiles({
    name: file,
    mimeType: 'text/plain',
    buffer: Buffer.from(`evidence-${file}`),
  })
  await expect(page.locator('.attachments')).toContainText(file)
  await page.locator('textarea').fill(message)
  await page.locator('.verification-row input').fill('[a-f0-9]{64}')
  await page.locator('.run-actions .primary').click()
}

test('production browser flow covers settings, SSE, stop/retry, reports and refresh recovery', async ({ page }) => {
  await page.goto('/')
  await configureProtocolProvider(page)

  await page.locator('.sidebar .primary.full').click()
  await page.locator('.modal input').fill(`E2E-${Date.now()}`)
  await page.locator('.modal button[type="submit"]').click()

  await uploadAndRun(page, 'Inspect this controlled attachment', 'sample.txt')
  await expect(page.getByTestId('event-tool_finished')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByTestId('final-report')).toBeVisible({ timeout: 20_000 })
  await expect(page.locator('.badge-completed')).toBeVisible()
  await expect(page.getByTestId('final-report').locator('a')).toHaveCount(2)

  await page.reload()
  await page.locator('.thread-item').first().click()
  await expect(page.getByTestId('final-report')).toBeVisible()

  await uploadAndRun(page, 'slow: verify stop and retry recovery', 'retry.txt')
  await expect(page.locator('.run-actions .danger')).toBeVisible()
  await page.locator('.run-actions .danger').click()
  await expect(page.locator('.badge-stopped')).toBeVisible({ timeout: 20_000 })
  await page.locator('.run-actions button').filter({ hasText: /.+/ }).first().click()
  await expect(page.locator('.badge-completed')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('final-report')).toBeVisible()
})

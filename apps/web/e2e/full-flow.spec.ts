import { expect, test } from '@playwright/test'

async function configureProtocolProvider(page: import('@playwright/test').Page) {
  await page.waitForTimeout(250)
  if (!await page.locator('.settings-backdrop').isVisible()) await page.locator('.settings-button').click()
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

  const profileName = `Advisory Agent ${Date.now()}`
  const center = page.getByTestId('agent-profile-center')
  await center.getByRole('button', { name: '专家模式' }).click()
  await center.getByLabel('Agent 名称').fill(profileName)
  await center.getByLabel('完成模式').selectOption('advisory')
  await center.getByRole('button', { name: '创建 Agent 配置' }).click()
  let profileRow = center.locator('.provider-row').filter({ hasText: profileName })
  await expect(profileRow).toContainText('v1')
  await profileRow.getByRole('button', { name: '编辑' }).click()
  await center.getByLabel('Agent 名称').fill(`${profileName} updated`)
  await center.getByRole('button', { name: /保存新版本/ }).click()
  profileRow = center.locator('.provider-row').filter({ hasText: `${profileName} updated` })
  await expect(profileRow).toContainText('v2')
  await profileRow.getByRole('button', { name: '版本' }).click()
  await center.locator('.version-history details').filter({ hasText: 'v1' }).getByRole('button', { name: '回滚到此版本' }).click()
  await expect(center.locator('.provider-row').filter({ hasText: profileName })).toContainText('v3')
  await page.locator('.settings-panel > header button').click()
  return profileName
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
  const advisoryProfile = await configureProtocolProvider(page)

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
  await page.getByRole('button', { name: '重试' }).click()
  await expect(page.locator('.badge-completed')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('final-report')).toBeVisible()

  await page.locator('.sidebar .primary.full').click()
  await page.getByLabel('任务名称').fill(`Advisory-${Date.now()}`)
  const advisoryOption = page.getByLabel('Agent 配置').getByRole('option', { name: new RegExp(advisoryProfile) })
  await page.getByLabel('Agent 配置').selectOption((await advisoryOption.getAttribute('value'))!)
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByText('建议回答：模型生成，未经外部验证')).toBeVisible()
  await page.getByLabel('任务消息').fill('advisory-only: explain a safe rollout')
  await page.locator('.run-actions .primary').click()
  await expect(page.getByTestId('final-report')).toBeVisible({ timeout: 20_000 })

  await page.getByLabel('任务消息').fill('human-input: complete this plan')
  await page.locator('.run-actions .primary').click()
  await expect(page.locator('.badge-waiting_input')).toBeVisible({ timeout: 20_000 })
  await page.getByLabel('补充信息').fill('Scope is the isolated staging environment.')
  await page.getByRole('button', { name: '提交并继续' }).click()
  await expect(page.locator('.badge-completed')).toBeVisible({ timeout: 20_000 })

})

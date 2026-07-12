import { expect, test } from '@playwright/test'

test('真实后端完成失败重规划闭环并展示报告', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '创建第一个任务' }).click()
  await page.getByLabel('任务名称').fill(`E2E-${Date.now()}`)
  await page.getByRole('button', { name: '创建', exact: true }).click()
  await expect(page.getByText('启动运行 ↗')).toBeVisible()
  await page.getByLabel('上传附件').setInputFiles({ name: 'sample.txt', mimeType: 'text/plain', buffer: Buffer.from('safe evidence') })
  await expect(page.getByText(/sample.txt/)).toBeVisible()
  await page.getByRole('button', { name: '启动运行 ↗' }).click()
  await expect(page.getByTestId('event-replanned')).toContainText('首次工具调用失败')
  await expect(page.getByTestId('final-report')).toContainText('成功条件已验证', { timeout: 15_000 })
  await expect(page.getByText('2', { exact: true }).first()).toBeVisible()
})

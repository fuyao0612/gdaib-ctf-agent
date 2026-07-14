import { expect, test, type Locator } from "@playwright/test";

const desktopViewports = [
  { width: 1024, height: 768 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
];

async function expectNoHorizontalOverflow(
  page: import("@playwright/test").Page,
) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow, "页面不应出现非预期横向滚动").toBeLessThanOrEqual(1);
}

async function expectNoOverlap(
  first: Locator,
  second: Locator,
  message: string,
) {
  const [a, b] = await Promise.all([first.boundingBox(), second.boundingBox()]);
  expect(a, `${message}：第一个元素必须可见`).not.toBeNull();
  expect(b, `${message}：第二个元素必须可见`).not.toBeNull();
  const horizontal =
    a!.x < b!.x + b!.width - 0.5 && b!.x < a!.x + a!.width - 0.5;
  const vertical =
    a!.y < b!.y + b!.height - 0.5 && b!.y < a!.y + a!.height - 0.5;
  expect(horizontal && vertical, message).toBe(false);
}

async function configureProtocolProvider(
  page: import("@playwright/test").Page,
) {
  await page.waitForTimeout(250);
  if (!(await page.locator(".settings-backdrop").isVisible()))
    await page.locator(".settings-button").click();
  await expect(page.getByLabel("配置进度")).toBeVisible();
  await page
    .locator(".admin-login input")
    .fill(process.env.YUWANG_E2E_ADMIN_TOKEN!);
  await page.locator(".admin-login button").click();
  await expect(page.locator(".settings-content")).toBeVisible();

  const form = page.locator(".settings-form").first();
  await form.locator("select").first().selectOption("custom");
  const inputs = form.locator("input");
  await inputs.nth(0).fill("http://127.0.0.1:8899/v1");
  await inputs.nth(1).fill("protocol-test-model");
  await inputs.nth(2).fill(`protocol-${Date.now()}`);
  await form.locator("button.primary").click();
  const providerRow = page
    .locator(".settings-content > section")
    .first()
    .locator(".provider-row");
  await expect(providerRow).toContainText("自定义模型服务");
  await providerRow.locator("button").first().click();
  await expect(page.locator(".settings-notice")).toContainText("连接测试成功");
  await expect(
    page.getByLabel("配置进度").locator("li.ready"),
  ).toHaveCount(4);
  await page.getByRole("button", { name: "高级模式" }).click();

  const profileName = `Advisory Agent ${Date.now()}`;
  const center = page.getByTestId("agent-profile-center");
  await center.getByLabel("Agent 名称").fill(profileName);
  await center.getByLabel("规划策略").selectOption("direct");
  await center.getByLabel("完成模式").selectOption("advisory");
  await center.getByRole("button", { name: "创建 Agent 配置" }).click();
  let profileRow = center
    .locator(".provider-row")
    .filter({ hasText: profileName });
  await expect(profileRow).toContainText("v1");
  await profileRow.getByRole("button", { name: "编辑" }).click();
  await center.getByLabel("Agent 名称").fill(`${profileName} updated`);
  await center.getByRole("button", { name: /保存新版本/ }).click();
  profileRow = center
    .locator(".provider-row")
    .filter({ hasText: `${profileName} updated` });
  await expect(profileRow).toContainText("v2");
  await profileRow.getByRole("button", { name: "版本" }).click();
  await center
    .locator(".version-history details")
    .filter({ hasText: "v1" })
    .getByRole("button", { name: "回滚到此版本" })
    .click();
  await expect(
    center.locator(".provider-row").filter({ hasText: profileName }),
  ).toContainText("v3");
  const hybridName = `Hybrid Agent ${Date.now()}`;
  await center.getByRole("button", { name: "新建配置" }).click();
  await center.getByLabel("Agent 名称").fill(hybridName);
  await center.getByLabel("规划策略").selectOption("hybrid");
  await center.getByLabel("完成模式").selectOption("advisory");
  await center.getByRole("button", { name: "创建 Agent 配置" }).click();
  await expect(
    center.locator(".provider-row").filter({ hasText: hybridName }),
  ).toContainText("v1");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  return { profileName, hybridName };
}

async function uploadAndRun(
  page: import("@playwright/test").Page,
  message: string,
  file: string,
) {
  await page.locator('input[type="file"]').setInputFiles({
    name: file,
    mimeType: "text/plain",
    buffer: Buffer.from(`evidence-${file}`),
  });
  await expect(page.locator(".attachments")).toContainText(file);
  await page.locator("textarea").fill(message);
  await page.locator(".verification-row input").fill("[a-f0-9]{64}");
  await page.locator(".run-actions .primary").click();
}

test("responsive layouts keep setup, settings, composer and audit accessible", async ({
  browser,
}, testInfo) => {
  const longTitle =
    `超长对话名称-${"用于验证中文标题不会挤坏侧栏".repeat(6)}`.slice(0, 150);
  const longFile = `${"very-long-evidence-file-name-".repeat(4)}sample.txt`;
  let threadCreated = false;

  for (const viewport of desktopViewports) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    await page.goto("/");
    await page.waitForLoadState("networkidle");

    const settings = page.locator(".settings-panel");
    const guide = page.locator(".setup-progress");
    await expect(settings).toBeVisible();
    await expect(guide).toBeVisible();
    const guideBox = await guide.boundingBox();
    expect(
      guideBox?.height ?? 999,
      "首次配置向导不能占据大块空白",
    ).toBeLessThan(140);
    const loginButton = page.getByRole("button", { name: "进入设置" });
    await expect(loginButton).toBeVisible();
    expect((await loginButton.boundingBox())!.y).toBeLessThan(viewport.height);
    await expectNoHorizontalOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(
        `setup-${viewport.width}x${viewport.height}.png`,
      ),
      fullPage: true,
    });

    await page
      .getByLabel("管理员令牌")
      .fill(process.env.YUWANG_E2E_ADMIN_TOKEN!);
    await loginButton.click();
    await expect(page.locator(".settings-content")).toBeVisible();
    await page.getByRole("button", { name: "高级模式" }).click();
    const settingsScroll = page.locator(".settings-scroll");
    await settingsScroll.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    const saveDefaults = page.getByRole("button", { name: "保存 Agent 设置" });
    await saveDefaults.scrollIntoViewIfNeeded();
    await expect(saveDefaults).toBeVisible();
    const panelBox = await settings.boundingBox();
    expect(panelBox!.x).toBeGreaterThanOrEqual(0);
    expect(panelBox!.y).toBeGreaterThanOrEqual(0);
    expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(viewport.width);
    expect(panelBox!.y + panelBox!.height).toBeLessThanOrEqual(viewport.height);
    await expectNoOverlap(
      settings.locator("header > div").first(),
      settings.locator(".settings-header-actions"),
      "设置标题与操作按钮不能重叠",
    );
    await expectNoHorizontalOverflow(page);

    await page.getByRole("button", { name: "关闭", exact: true }).click();
    await page.reload();
    await page.waitForLoadState("networkidle");

    if (!threadCreated) {
      await page.locator(".sidebar .primary.full").click();
      await page.getByLabel("任务名称").fill(longTitle);
      await page.getByRole("button", { name: "创建", exact: true }).click();
      threadCreated = true;
    } else {
      await page
        .locator(".thread-item")
        .filter({ hasText: "超长对话名称" })
        .first()
        .click();
    }

    await page.locator('input[type="file"]').setInputFiles({
      name: longFile,
      mimeType: "text/plain",
      buffer: Buffer.from("safe responsive layout evidence"),
    });

    await expect(page.getByLabel("任务消息")).toBeVisible();
    await expect(page.locator(".run-actions .primary")).toBeVisible();
    await expect(page.locator(".attachments")).toContainText(
      "very-long-evidence",
    );
    const heading = page.getByTestId("thread-heading");
    const topbarActions = page.locator(".topbar-actions");
    await expectNoOverlap(
      heading,
      topbarActions,
      "长标题不能覆盖状态和操作区",
    );
    await expect(page.locator(".topbar h2")).toHaveCSS(
      "text-overflow",
      "ellipsis",
    );
    const selectedThread = page.locator(".thread-row.selected");
    await expectNoOverlap(
      selectedThread.locator(".thread-item"),
      selectedThread.locator(".thread-actions"),
      "侧栏长标题不能覆盖线程按钮",
    );
    await expectNoOverlap(
      page.locator(".provider-select"),
      page.locator(".run-actions"),
      "模型选择区与运行按钮不能重叠",
    );
    await expectNoHorizontalOverflow(page);

    const auditToggle = page.getByRole("button", {
      name: "运行审计",
      exact: true,
    });
    if (viewport.width <= 1180) {
      await expect(auditToggle).toBeVisible();
      expect((await auditToggle.boundingBox())!.width).toBeGreaterThan(70);
      await expectNoOverlap(
        page.getByTestId("thread-status"),
        auditToggle,
        "运行模式标签与审计按钮不能重叠",
      );
      await auditToggle.click();
      await expect(page.locator(".inspector.open")).toBeVisible();
      await page.waitForTimeout(250);
      const inspectorBox = await page.locator(".inspector.open").boundingBox();
      expect(inspectorBox!.x + inspectorBox!.width).toBeLessThanOrEqual(
        viewport.width,
      );
      await expectNoOverlap(
        page.locator(".inspector-head > span"),
        page.locator(".inspector-head > div"),
        "审计抽屉标题与关闭按钮不能重叠",
      );
      await page.screenshot({
        path: testInfo.outputPath(
          `audit-drawer-${viewport.width}x${viewport.height}.png`,
        ),
      });
      await page.getByRole("button", { name: "关闭运行审计" }).click();
      await expect(page.locator(".inspector.open")).toHaveCount(0);
      await page.waitForTimeout(250);
    } else {
      await expect(page.locator(".inspector")).toBeVisible();
    }

    await page.screenshot({
      path: testInfo.outputPath(
        `workbench-${viewport.width}x${viewport.height}.png`,
      ),
      fullPage: true,
    });
    await context.close();
  }
});

test("production browser flow covers settings, SSE, stop/retry, reports and refresh recovery", async ({
  page,
}) => {
  await page.goto("/");
  const { profileName: advisoryProfile, hybridName } =
    await configureProtocolProvider(page);

  await page.locator(".sidebar .primary.full").click();
  await page.locator(".modal input").fill(`E2E-${Date.now()}`);
  await page.locator('.modal button[type="submit"]').click();

  await uploadAndRun(
    page,
    "long-event: Inspect this controlled attachment",
    "sample.txt",
  );
  const longEvent = page.getByTestId("event-plan_updated");
  await expect(longEvent).toContainText("超长事件内容", { timeout: 20_000 });
  expect(
    await longEvent.evaluate(
      (element) => element.scrollWidth - element.clientWidth,
    ),
  ).toBeLessThanOrEqual(1);
  await expect(page.getByTestId("event-tool_finished")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("result-completed")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.locator(".badge-completed")).toBeVisible();
  await expect(page.getByTestId("run-progress").locator("li.completed")).toHaveCount(5);
  for (const stage of ["理解任务", "制定计划", "执行动作", "验证结果", "生成汇报"])
    await expect(page.getByTestId("run-progress").getByText(stage)).toBeVisible();
  await expect(page.getByTestId("budget-audit")).toContainText("策略：dynamic");
  await expect(page.getByTestId("result-completed").locator(".full-report a")).toHaveCount(2);

  await page.reload();
  await expect(page.getByTestId("result-completed")).toBeVisible();
  await expect(page.getByTestId("run-progress").locator("li.completed")).toHaveCount(5);

  await uploadAndRun(page, "slow: verify stop and retry recovery", "retry.txt");
  await expect(page.locator(".run-actions .danger")).toBeVisible();
  await page.locator(".run-actions .danger").click();
  await expect(page.locator(".badge-stopped")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("result-stopped")).toContainText("任务已停止");
  await page.getByRole("button", { name: "重试" }).click();
  await expect(page.locator(".badge-completed")).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByTestId("result-completed")).toBeVisible();

  await page.locator(".sidebar .primary.full").click();
  await page.getByLabel("任务名称").fill(`Advisory-${Date.now()}`);
  const advisoryOption = page
    .getByLabel("Agent 配置")
    .getByRole("option", { name: new RegExp(advisoryProfile) });
  await page
    .getByLabel("Agent 配置")
    .selectOption((await advisoryOption.getAttribute("value"))!);
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(
    page.getByText("建议回答：模型生成，未经外部验证"),
  ).toBeVisible();
  await page
    .getByLabel("任务消息")
    .fill("advisory-only: explain a safe rollout");
  await page.locator(".run-actions .primary").click();
  await expect(page.getByTestId("result-completed")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.locator(".message.assistant")).toContainText(
    "Review the plan",
  );
  await expect(page.getByTestId("budget-audit")).toContainText("策略：direct");
  await expect(page.locator(".memory-list article").first()).toBeVisible();
  const memoryCount = await page.locator(".memory-list article").count();
  await page
    .locator(".memory-list article")
    .first()
    .getByRole("button", { name: /删除/ })
    .click();
  await expect(page.locator(".memory-list article")).toHaveCount(
    memoryCount - 1,
  );

  await page.getByLabel("任务消息").fill("human-input: complete this plan");
  await page.locator(".run-actions .primary").click();
  await expect(page.locator(".badge-waiting_input")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("result-waiting_input")).toContainText(
    "等待用户补充",
  );
  await page
    .getByLabel("补充信息")
    .fill("Scope is the isolated staging environment.");
  await page.getByRole("button", { name: "提交并继续" }).click();
  await expect(page.locator(".badge-completed")).toBeVisible({
    timeout: 20_000,
  });

  await page.getByLabel("任务消息").fill("hard-fail: reject this run explicitly");
  await page.locator(".run-actions .primary").click();
  await expect(page.locator(".badge-failed")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("result-failed")).toContainText("测试要求明确失败");
  await expect(page.getByTestId("result-failed")).toContainText("未生成最终答案");

  await page.locator(".sidebar .primary.full").click();
  await page.getByLabel("任务名称").fill(`Hybrid-${Date.now()}`);
  const hybridOption = page
    .getByLabel("Agent 配置")
    .getByRole("option", { name: new RegExp(hybridName) });
  await page
    .getByLabel("Agent 配置")
    .selectOption((await hybridOption.getAttribute("value"))!);
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await page
    .getByLabel("任务消息")
    .fill("advisory-only: summarize this configuration");
  await page.locator(".run-actions .primary").click();
  await expect(page.locator(".badge-completed")).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId("budget-audit")).toContainText("策略：hybrid");

  await page.route("**/api/v1/threads/*/turns", (route) =>
    route.fulfill({
      status: 429,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "quota_exceeded",
          message: "模型额度不足，请检查 Provider 配置",
        },
      }),
    }),
  );
  await page.getByLabel("任务消息").fill("verify visible quota error");
  await page.locator(".run-actions .primary").click();
  await expect(page.getByRole("alert")).toContainText("模型额度不足");
});

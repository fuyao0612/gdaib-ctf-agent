import { expect, test, type Page } from "@playwright/test";

const viewports = [
  { width: 1024, height: 768 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
  { width: 2048, height: 1152 },
];

async function expectNoHorizontalOverflow(page: Page) {
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow, "页面不应出现横向溢出").toBeLessThanOrEqual(1);
}

async function configure(page: Page) {
  await page.goto("/");
  await expect(page.getByRole("dialog", { name: "设置中心" })).toBeVisible();
  await page
    .getByLabel("管理员令牌")
    .fill(process.env.YUWANG_E2E_ADMIN_TOKEN!);
  await page.getByRole("button", { name: "进入设置" }).click();
  await expect(page.locator(".settings-content")).toBeVisible();

  const providerSection = page.locator(".settings-content > section").first();
  const providerForm = providerSection.locator(".settings-form");
  await providerForm.locator("select").first().selectOption("custom");
  const inputs = providerForm.locator("input");
  await inputs.nth(0).fill("http://127.0.0.1:8899/v1");
  await inputs.nth(1).fill("protocol-test-model");
  await inputs.nth(2).fill("e2e-protocol-key");
  await providerForm.getByRole("button", { name: "创建 Provider" }).click();
  const providerRow = providerSection.locator(".provider-row").first();
  await expect(providerRow).toContainText("自定义模型服务");
  await providerRow.getByRole("button", { name: /测试/ }).click();
  await expect(page.locator(".settings-notice")).toContainText("连接测试成功");

  const chatSection = page
    .locator(".settings-content > section")
    .filter({ hasText: "聊天与界面" });
  await expect(chatSection.getByLabel("新对话默认模式")).toHaveValue("chat");
  await expect(chatSection.getByLabel("外观")).toHaveValue("light");
  await chatSection.getByLabel("默认聊天模型").selectOption({ index: 1 });
  await chatSection.getByRole("button", { name: "保存聊天设置" }).click();
  await expect(page.locator(".settings-notice")).toContainText("聊天与界面偏好已保存");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
}

async function createThread(page: Page, title: string, mode: "chat" | "agent") {
  await page.getByRole("button", { name: /新建对话/ }).click();
  await page.getByLabel("对话名称").fill(title);
  await page.getByLabel("默认回复方式").selectOption(mode);
  if (mode === "agent") {
    await expect(page.getByLabel("Agent 配置")).not.toHaveValue("");
    await page.getByLabel("计划控制").selectOption("auto");
  }
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByTestId("thread-heading")).toContainText(title);
}

async function sendChat(page: Page, content: string, expectedCount: number) {
  const input = page.getByLabel("消息");
  await input.fill(content);
  await input.press("Enter");
  await expect(page.locator(".message.assistant")).toHaveCount(expectedCount, {
    timeout: 15_000,
  });
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeVisible({ timeout: 15_000 });
}

test("first setup exposes chat defaults and a light interface", async ({ page }) => {
  await configure(page);
  await expect(page.getByRole("heading", { name: "开始一段新对话" })).toBeVisible();
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(245, 246, 248)");
  await expectNoHorizontalOverflow(page);

  await createThread(page, "普通聊天", "chat");
  await expect(
    page.getByRole("button", { name: "对话", exact: true }),
  ).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.getByText("任务设置")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "运行审计" })).toHaveCount(0);
  await sendChat(page, "你好", 1);
  await expect(page.locator(".message.assistant").last()).toContainText(
    "普通对话不会创建 Agent 任务",
  );
  await page.reload();
  await expect(page.locator(".message.user")).toContainText("你好");
  await expect(page.locator(".message.assistant")).toContainText("普通对话不会创建");
  await expect(page.locator(".run-progress")).toHaveCount(0);
});

test("long chat scrolls inside the workspace at every target viewport", async ({
  page,
}, testInfo) => {
  await configure(page);
  await createThread(page, "长对话滚动验收", "chat");
  for (let index = 1; index <= 20; index += 1)
    await sendChat(page, `第 ${index} 轮：请记住这是一条用于滚动验收的长消息。`, index);

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await page.waitForTimeout(100);
    const workspace = page.locator(".workspace");
    const conversation = page.getByTestId("conversation-scroll");
    const composer = page.locator(".composer");
    const [workspaceBox, composerBox] = await Promise.all([
      workspace.boundingBox(),
      composer.boundingBox(),
    ]);
    expect(workspaceBox).not.toBeNull();
    expect(composerBox).not.toBeNull();
    expect(workspaceBox!.height).toBeLessThanOrEqual(viewport.height + 1);
    expect(composerBox!.y).toBeGreaterThanOrEqual(0);
    expect(composerBox!.y + composerBox!.height).toBeLessThanOrEqual(
      viewport.height + 1,
    );
    const sizes = await conversation.evaluate((element) => ({
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
    }));
    expect(sizes.scrollHeight).toBeGreaterThan(sizes.clientHeight);

    await conversation.evaluate((element) => { element.scrollTop = 0; });
    await conversation.hover();
    await page.mouse.wheel(0, 500);
    await expect
      .poll(() => conversation.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(0);
    await conversation.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect(page.locator(".message.assistant").last()).toBeInViewport();
    await expect(page.getByLabel("消息")).toBeInViewport();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(`chat-${viewport.width}x${viewport.height}.png`),
    });
  }

  const conversation = page.getByTestId("conversation-scroll");
  await conversation.evaluate((element) => {
    element.scrollTop = Math.floor(element.scrollHeight / 3);
  });
  const before = await conversation.evaluate((element) => element.scrollTop);
  await sendChat(page, "用户正在向上阅读时不要强制滚到底部。", 21);
  const after = await conversation.evaluate((element) => element.scrollTop);
  expect(Math.abs(after - before)).toBeLessThan(120);
});

test("Agent mode keeps controls, report, drawer, stop and retry isolated", async ({
  page,
}) => {
  await configure(page);
  await createThread(page, "Agent 控制验收", "agent");
  await expect(
    page.getByRole("button", { name: "Agent 任务", exact: true }),
  ).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.locator('input[type="file"]').setInputFiles({
    name: "agent-evidence.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("controlled evidence"),
  });
  await page.getByLabel("消息").fill("long-event: verify controlled evidence");
  await page.locator(".agent-options").getByText("任务设置").click();
  await page.getByLabel("成功答案正则").fill("[a-f0-9]{64}");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByTestId("result-completed")).toBeVisible({ timeout: 30_000 });
  await expect(page.locator(".message.assistant").last()).toBeVisible();
  await expect(page.getByText("展开结论、证据与任务报告")).toBeVisible();

  const auditButton = page.getByRole("button", {
    name: "运行审计",
    exact: true,
  });
  await auditButton.click();
  await expect(page.locator(".inspector.open")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await page.getByRole("button", { name: "关闭运行审计" }).click();
  await expect(page.locator(".inspector.open")).toHaveCount(0);

  await page.locator('input[type="file"]').setInputFiles({
    name: "guidance-evidence.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("guidance controlled evidence"),
  });
  await page.getByLabel("消息").fill("slow: verify ordered guidance");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByTestId("run-control-panel")).toBeVisible();
  await page
    .getByRole("textbox", { name: "追加指引", exact: true })
    .fill("第一条：先核对证据来源");
  await page.getByRole("button", { name: "排队追加指引" }).click();
  await expect(page.getByLabel("追加指引记录")).toContainText("#1");
  await page
    .getByRole("textbox", { name: "追加指引", exact: true })
    .fill("第二条：恢复后保留原授权范围");
  await page.getByRole("button", { name: "排队追加指引" }).click();
  const guidanceList = page.getByLabel("追加指引记录");
  await expect(guidanceList).toContainText("#1");
  await expect(guidanceList).toContainText("#2");
  await expect(page.locator(".badge-completed")).toBeVisible({ timeout: 30_000 });
  await expect(guidanceList.getByText("已应用并重规划")).toHaveCount(2);

  await page.locator('input[type="file"]').setInputFiles({
    name: "pause-evidence.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("pause controlled evidence"),
  });
  await page.getByLabel("消息").fill("slow: verify pause and resume recovery");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByTestId("run-control-panel")).toBeVisible();
  await page
    .getByRole("button", { name: "运行审计", exact: true })
    .click();
  await expect(page.locator(".inspector.open")).toContainText(
    "file_metadata 执行成功",
    { timeout: 20_000 },
  );
  await page.getByRole("button", { name: "关闭运行审计" }).click();
  await page.getByRole("button", { name: "安全暂停" }).click();
  await expect(page.getByTestId("result-paused")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "从检查点继续" }).click();
  await expect(page.locator(".badge-completed")).toBeVisible({ timeout: 30_000 });

  await page.locator('input[type="file"]').setInputFiles({
    name: "retry-evidence.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("retry controlled evidence"),
  });
  await page.getByLabel("消息").fill("slow: verify stop and retry recovery");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("button", { name: "停止", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "停止", exact: true }).click();
  await expect(page.locator(".badge-stopped")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.locator(".badge-completed")).toBeVisible({ timeout: 30_000 });

  await page.getByRole("button", { name: "对话", exact: true }).click();
  await expect(page.getByRole("button", { name: "运行审计" })).toHaveCount(0);
  const assistantCount = await page.locator(".message.assistant").count();
  await sendChat(page, "你好", assistantCount + 1);
  await expect(page.locator(".run-progress")).toHaveCount(0);
});

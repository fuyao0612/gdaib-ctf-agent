import { expect, test, type Page } from "@playwright/test";

const viewports = [
  { width: 375, height: 667 },
  { width: 390, height: 844 },
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
  await expect(chatSection.getByLabel("外观")).toHaveValue("light");
  await chatSection.getByLabel("默认聊天模型").selectOption({ index: 1 });
  await chatSection.getByRole("button", { name: "保存聊天设置" }).click();
  await expect(page.locator(".settings-notice")).toContainText("聊天与界面偏好已保存");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
}

async function createThread(page: Page, title: string) {
  await page.getByRole("button", { name: /新建对话/ }).click();
  await page.getByLabel("对话名称").fill(title);
  await page.getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByTestId("thread-heading")).toContainText(title);
}

async function attachFile(page: Page, name: string, content: string) {
  await page.locator('input[type="file"]').setInputFiles({
    name,
    mimeType: "text/plain",
    buffer: Buffer.from(content),
  });
  // 上传成功后才显示在待发送列表中。这里等待该确认，避免测试误把“已选择文件”
  // 当成“会随消息发送”。
  await expect(page.locator(".attachments")).toContainText(name);
}

function messageInput(page: Page) {
  // 记忆删除按钮的可访问名称可能包含历史文本中的“消息”。精确选择 textbox，
  // 才能稳定指向统一输入区，而不是依赖会随业务内容变化的无障碍名称。
  return page.getByRole("textbox", { name: "消息", exact: true });
}

async function sendChat(page: Page, content: string, expectedCount: number) {
  const input = messageInput(page);
  await input.fill(content);
  await input.press("Enter");
  await expect(page.locator(".message.assistant")).toHaveCount(expectedCount, {
    timeout: 15_000,
  });
  await expect(
    page.getByRole("button", { name: "发送", exact: true }),
  ).toBeVisible({ timeout: 15_000 });
}

async function wheelConversation(
  page: Page,
  conversation: ReturnType<Page["getByTestId"]>,
  deltaY: number,
) {
  const box = await conversation.boundingBox();
  expect(box).not.toBeNull();
  const point = {
    x: box!.x + Math.min(24, box!.width / 2),
    y: box!.y + Math.min(24, box!.height / 2),
  };
  // `locator.hover()` 会尝试把整个滚动容器滚入视口；长对话的容器本身正是
  // 滚动元素，自动滚入会让 Playwright 在部分尺寸下把命中点放到容器外。
  // 这里验证实际命中点后再发送真实鼠标滚轮事件，覆盖用户可滚动的行为而不引入该假阳性。
  await expect
    .poll(() =>
      page.evaluate(
        ({ x, y }) =>
          document.elementFromPoint(x, y)?.closest('[data-testid="conversation-scroll"]')
            ?.getAttribute("data-testid"),
        point,
      ),
    )
    .toBe("conversation-scroll");
  await page.mouse.move(point.x, point.y);
  await page.mouse.wheel(0, deltaY);
}

async function openTaskControls(page: Page) {
  const controls = page.locator(".task-controls");
  await expect(controls).toBeVisible();
  if (!(await controls.evaluate((element) => (element as HTMLDetailsElement).open)))
    await controls.locator(":scope > summary").click();
  await expect(page.getByTestId("run-control-panel")).toBeVisible();
}

test("first setup exposes chat defaults and a light interface", async ({ page }) => {
  await configure(page);
  await expect(page.getByRole("heading", { name: "开始一段新对话" })).toBeVisible();
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(245, 246, 248)");
  await expectNoHorizontalOverflow(page);

  await createThread(page, "普通聊天");
  await expect(page.getByLabel("默认回复方式")).toHaveCount(0);
  await expect(page.getByText("任务设置")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "运行审计" })).toHaveCount(0);
  await sendChat(page, "你好", 1);
  await expect(page.locator(".message.assistant").last()).toContainText(
    "你好，我是御网智元。",
  );
  await page.reload();
  await expect(page.locator(".message.user")).toContainText("你好");
  await expect(page.locator(".message.assistant")).toContainText("你好，我是御网智元。");
  await expect(page.locator(".run-progress")).toHaveCount(0);
});

test("long chat scrolls inside the workspace at every target viewport", async ({
  page,
}, testInfo) => {
  await configure(page);
  await createThread(page, "长对话滚动验收");
  for (let index = 1; index <= 20; index += 1)
    await sendChat(page, `第 ${index} 轮：请记住这是一条用于滚动验收的长消息。`, index);

  for (const viewport of viewports) {
    await page.setViewportSize(viewport);
    await page.waitForTimeout(100);
    if (viewport.width <= 700) {
      const closeSidebar = page.getByRole("button", { name: "收起侧栏" });
      if (await closeSidebar.isVisible()) await closeSidebar.click();
    }
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
    await wheelConversation(page, conversation, 500);
    await expect
      .poll(() => conversation.evaluate((element) => element.scrollTop))
      .toBeGreaterThan(0);
    await conversation.evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await expect(page.locator(".message.assistant").last()).toBeInViewport();
    await expect(messageInput(page)).toBeInViewport();
    await expectNoHorizontalOverflow(page);
    await page.screenshot({
      path: testInfo.outputPath(`chat-${viewport.width}x${viewport.height}.png`),
    });
  }

  const conversation = page.getByTestId("conversation-scroll");
  await wheelConversation(page, conversation, -1000);
  await expect
    .poll(() =>
      conversation.evaluate(
        (element) => element.scrollHeight - element.scrollTop - element.clientHeight,
      ),
    )
    .toBeGreaterThan(120);
  const before = await conversation.evaluate((element) => element.scrollTop);
  await sendChat(page, "用户正在向上阅读时不要强制滚到底部。", 21);
  const after = await conversation.evaluate((element) => element.scrollTop);
  expect(Math.abs(after - before)).toBeLessThan(120);
});

test("统一消息可自动执行并保留控制、报告、审计、停止与重试", async ({
  page,
}) => {
  await configure(page);
  await createThread(page, "自动执行控制验收");
  await attachFile(page, "agent-evidence.txt", "controlled evidence");
  await messageInput(page).fill("执行任务：long-event: verify controlled evidence");
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

  await attachFile(page, "guidance-evidence.txt", "guidance controlled evidence");
  await messageInput(page).fill("执行任务：slow: verify ordered guidance");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const guidanceInput = messageInput(page);
  await expect(guidanceInput).toBeEditable();
  await guidanceInput.fill("第一条：先核对证据来源");
  await guidanceInput.press("Enter");
  await expect(guidanceInput).toBeEditable();
  await guidanceInput.fill("第二条：恢复后保留原授权范围");
  await guidanceInput.press("Enter");
  // 控制面板仅作为高级查看入口：指引记录必须来自主输入框的两次提交。
  await openTaskControls(page);
  const guidanceList = page.getByLabel("追加指引记录");
  await expect(guidanceList).toContainText("#1");
  await expect(guidanceList).toContainText("#2");
  await expect(page.locator(".badge-completed")).toBeVisible({ timeout: 30_000 });
  await expect(guidanceList).toContainText("已在检查点应用");
  await expect(guidanceList).not.toContainText("已应用并重规划");

  await attachFile(page, "pause-evidence.txt", "pause controlled evidence");
  await messageInput(page).fill("执行任务：slow: verify pause and resume recovery");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await openTaskControls(page);
  // 前一轮的终态控制面可能仍在重绘；先确认当前慢任务已进入可暂停状态，
  // 再打开审计抽屉，避免把上一 Run 的控件误当成新 Run。
  await expect(page.getByRole("button", { name: "安全暂停" })).toBeVisible({
    timeout: 20_000,
  });
  await page
    .getByRole("button", { name: "运行审计", exact: true })
    .click();
  const inspector = page.locator(".inspector.open");
  await expect(inspector).toBeVisible();
  await expect(inspector).toContainText(
    "file_metadata 执行成功",
    { timeout: 20_000 },
  );
  await page.getByRole("button", { name: "关闭运行审计" }).click();
  await page.getByRole("button", { name: "安全暂停" }).click();
  await expect(page.getByTestId("result-paused")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "从检查点继续" }).click();
  await expect(page.locator(".badge-completed")).toBeVisible({ timeout: 30_000 });

  await attachFile(page, "retry-evidence.txt", "retry controlled evidence");
  await messageInput(page).fill("执行任务：slow: verify stop and retry recovery");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const stopInput = messageInput(page);
  await expect(stopInput).toBeEditable();
  await stopInput.fill("停止");
  await stopInput.press("Enter");
  await expect(page.locator(".badge-stopped")).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.locator(".badge-completed")).toBeVisible({ timeout: 30_000 });

});

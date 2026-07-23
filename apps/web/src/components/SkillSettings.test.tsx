import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";
import type { SkillDefinition } from "../types";
import SkillSettings from "./SkillSettings";

const existing: SkillDefinition = {
  id: "skill-1",
  name: "发布检查",
  description: "发布前核对",
  prompt: "检查发布条件。",
  steps: ["检查测试"],
  checklist: ["无密钥"],
  enabled: true,
  created_at: "2026-07-23T00:00:00Z",
  updated_at: "2026-07-23T00:00:00Z",
};

describe("SkillSettings", () => {
  afterEach(() => vi.restoreAllMocks());

  it("创建声明式 Skill 时仅提交模板字段", async () => {
    const create = vi.spyOn(api, "createSkill").mockResolvedValue(existing);
    const onRefresh = vi.fn(async () => undefined);
    const onNotice = vi.fn();
    render(
      <SkillSettings
        csrf="csrf-test"
        skills={[]}
        onRefresh={onRefresh}
        onNotice={onNotice}
        onError={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText("Skill 名称"), {
      target: { value: "发布检查" },
    });
    fireEvent.change(screen.getByLabelText("Skill 提示词"), {
      target: { value: "检查发布条件。" },
    });
    fireEvent.change(screen.getByLabelText("Skill 步骤"), {
      target: { value: "检查测试\n检查镜像" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建 Skill" }));

    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("csrf-test", expect.objectContaining({
        name: "发布检查",
        prompt: "检查发布条件。",
        steps: ["检查测试", "检查镜像"],
        enabled: true,
      })),
    );
    expect(onRefresh).toHaveBeenCalledOnce();
    expect(onNotice).toHaveBeenCalledWith("Skill 已创建");
  });

  it("可停用 Skill，并在删除前要求二次确认", async () => {
    const update = vi.spyOn(api, "updateSkill").mockResolvedValue({
      ...existing,
      enabled: false,
    });
    const remove = vi.spyOn(api, "deleteSkill").mockResolvedValue(undefined);
    const onRefresh = vi.fn(async () => undefined);
    const onNotice = vi.fn();
    render(
      <SkillSettings
        csrf="csrf-test"
        skills={[existing]}
        onRefresh={onRefresh}
        onNotice={onNotice}
        onError={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /发布检查/ }));
    fireEvent.change(screen.getByLabelText("Skill 状态"), {
      target: { value: "disabled" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存 Skill" }));
    await waitFor(() =>
      expect(update).toHaveBeenCalledWith(
        "csrf-test",
        "skill-1",
        expect.objectContaining({ enabled: false }),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(screen.getByText("确认删除“发布检查”？")).toBeInTheDocument();
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith("csrf-test", "skill-1"));
    expect(onNotice).toHaveBeenCalledWith("Skill 已删除，引用它的对话已取消选择");
  });
});

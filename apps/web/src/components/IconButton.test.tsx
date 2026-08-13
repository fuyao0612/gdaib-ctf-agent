import { render, screen } from "@testing-library/react";
import { Settings } from "lucide-react";
import { describe, expect, it } from "vitest";
import IconButton from "./IconButton";

describe("IconButton", () => {
  it("用稳定名称呈现真实图标且默认不会提交表单", () => {
    const { container } = render(<IconButton icon={Settings} label="打开设置" />);
    const button = screen.getByRole("button", { name: "打开设置" });

    expect(button).toHaveAttribute("type", "button");
    expect(button).toHaveAttribute("title", "打开设置");
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });
});

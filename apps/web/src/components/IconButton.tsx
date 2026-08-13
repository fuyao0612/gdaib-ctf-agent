import type { ButtonHTMLAttributes } from "react";
import type { LucideIcon } from "lucide-react";

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  icon: LucideIcon;
  label: string;
  size?: number;
}

/** 统一桌面工作台的无文字图标按钮，并为键盘与读屏用户保留完整名称。 */
export default function IconButton({
  icon: Icon,
  label,
  size = 18,
  className = "",
  type = "button",
  ...props
}: Props) {
  return (
    <button
      {...props}
      type={type}
      className={`icon-button ${className}`.trim()}
      aria-label={label}
      title={label}
    >
      <Icon size={size} strokeWidth={1.8} aria-hidden="true" />
    </button>
  );
}

"""对照 pyproject.toml 验证健康接口版本，避免 CI 复制版本常量。"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def project_version() -> str:
    """从项目唯一版本源读取预期值。"""

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(pyproject["project"]["version"])


def validate_health(payload: dict[str, Any], expected: str) -> None:
    """给 CI 返回可行动的错误，而不是无上下文的 AssertionError。"""

    actual = payload.get("version")
    if payload.get("status") != "ok":
        raise ValueError(f"健康接口状态异常：{payload.get('status')!r}")
    if actual != expected:
        raise ValueError(f"健康接口版本不一致：期望 {expected}，实际 {actual!r}")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("健康接口没有返回 JSON 对象")
        expected = project_version()
        validate_health(payload, expected)
    except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError) as exc:
        raise SystemExit(f"健康接口验收失败：{exc}") from exc
    print(f"健康接口版本验证通过：v{expected}")


if __name__ == "__main__":
    main()

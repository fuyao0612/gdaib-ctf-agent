"""调用正在运行的御网智元正式 API；不导入 tests，也不创建协议替身。"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

import httpx


def fail(message: str) -> None:
    raise SystemExit(f"错误：{message}")


def main() -> None:
    base_url = os.getenv("YUWANG_API_URL", "http://localhost:8080/api/v1")
    try:
        with httpx.Client(base_url=base_url, timeout=30, follow_redirects=False) as client:
            setup = client.get("/setup/status").raise_for_status().json()
            checks = setup.get("checks", {})
            if not checks.get("provider"):
                fail("Provider 尚未真实连接成功。请先在 Web 设置中心保存并执行连接测试。")
            if not checks.get("agent"):
                fail("默认 Agent 无法解析到已连接 Provider。请检查默认配置。")

            login = client.post("/admin/session").raise_for_status().json()
            client.headers["X-CSRF-Token"] = login["csrf_token"]
            providers: list[dict[str, Any]] = client.get("/providers").raise_for_status().json()
            profiles: list[dict[str, Any]] = client.get("/agent-profiles").raise_for_status().json()
            provider = next((item for item in providers if item["is_default"]), providers[0])
            profile = next((item for item in profiles if item["is_default"]), profiles[0])

            thread = client.post(
                "/threads",
                json={
                    "title": "正式 API 最小示例",
                    "mode": "normal",
                    "agent_profile_id": profile["profile_id"],
                },
            ).raise_for_status().json()
            run = client.post(
                f"/threads/{thread['id']}/turns",
                json={
                    "content": (
                        "请调用 localhost_http_probe 检查 "
                        "http://127.0.0.1:8000/api/v1/health，"
                        "并把 HTTP 200 作为候选结果。"
                    ),
                    "artifact_ids": [],
                    "provider_config_id": provider["id"],
                    "authorized_targets": ["127.0.0.1"],
                    "verification_rules": [{"kind": "regex", "value": "200"}],
                },
            ).raise_for_status().json()
            print(f"已创建 Run：{run['id']}，Provider：{provider['name']}")

            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                current = client.get(f"/runs/{run['id']}").raise_for_status().json()
                if current["status"] not in {"queued", "running"}:
                    break
                time.sleep(1)
            else:
                fail("等待 Run 超过 180 秒。可在 Web 运行审计中继续查看或停止。")

            print(f"状态：{current['status']}")
            if current.get("error"):
                print(f"原因：{current['error']}")
            audit = client.get(f"/runs/{run['id']}/audit").raise_for_status().json()
            print(
                "消耗：模型 {model_calls} 次，工具 {tool_calls} 次，Token {tokens}".format(
                    **audit["usage"]
                )
            )
            if current["status"] == "completed":
                report = client.get(f"/runs/{run['id']}/report").raise_for_status().json()
                print("\n" + report["markdown"])
            elif current["status"] == "waiting_input":
                print("运行正在等待补充，请在 Web 中继续。")
            else:
                print("运行未完成；请根据原因检查配置，并在 Web 中查看审计或重试。")
    except httpx.ConnectError:
        fail(f"无法连接 {base_url}。请先运行 .\\yuwang.ps1 start 和 status。")
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("error", {}).get("message", exc.response.text)
        except ValueError:
            detail = exc.response.text
        fail(f"API 返回 HTTP {exc.response.status_code}：{detail}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\n已取消。")

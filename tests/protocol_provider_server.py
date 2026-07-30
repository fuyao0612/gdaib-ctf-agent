"""Deterministic OpenAI-compatible protocol server for browser acceptance tests."""

from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class Handler(BaseHTTPRequestHandler):
    server_version = "YuwangProtocolTest/1.0"

    def do_GET(self) -> None:  # noqa: N802
        self._json(200, {"status": "ok"}) if self.path == "/health" else self._json(
            404, {"error": "not found"}
        )

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        prompt = request["messages"][-1]["content"]
        if "response_format" not in request:
            user_messages = [
                item["content"] for item in request["messages"] if item["role"] == "user"
            ]
            text = (
                "你好，我是御网智元。普通对话不会创建 Agent 任务。"
                if len(user_messages) == 1
                else f"我记得前文：{user_messages[0][:60]}"
            )
            if request.get("stream"):
                self._stream(text)
            else:
                self._chat_json(text)
            return
        schema_name = request.get("response_format", {}).get("json_schema", {}).get("name")
        content = self._completion(schema_name, prompt)
        self._json(
            200,
            {
                "id": "protocol-test",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": json.dumps(content)}}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
            },
        )

    @staticmethod
    def _completion(schema_name: str | None, prompt: str) -> dict[str, Any]:
        if schema_name == "connectionprobe" or '"status":"ok"' in prompt:
            return {"status": "ok"}
        context = json.loads(prompt)
        if schema_name == "messageintent" or "user_message_untrusted" in context:
            message = str(context.get("user_message_untrusted", ""))
            if message in {"你好", "我想准备发布说明。"} or "不要执行" in message or "只说明" in message:
                return {"kind": "chat", "clarification_question": None}
            if "帮我处理一下" in message:
                return {
                    "kind": "clarify",
                    "clarification_question": "请补充目标和预期交付物。",
                }
            return {"kind": "run", "clarification_question": None}
        if schema_name == "taskbriefdraft" or "生成公开 Task Brief" in context.get(
            "purpose", ""
        ):
            user_input = context["untrusted_user_input"]
            constraints = context["trusted_execution_constraints"]
            task = user_input["task"].lower()
            needs_clarification = (
                "clarify-first" in task and not user_input["supplemental_inputs"]
            )
            return {
                "goal": "Complete the authorized task with an auditable result.",
                "authorized_scope": constraints["authorized_targets"],
                "constraints": constraints["constraints"],
                "success_criteria": constraints["success_conditions"],
                "expected_output": "A concise auditable result.",
                "known_information": ["The original request is preserved."],
                "assumptions": [],
                "risks": ["Do not expand the authorized scope."],
                "needs_clarification": needs_clarification,
                "clarification_questions": (
                    ["Please clarify the intended audience."] if needs_clarification else []
                ),
            }
        user_input = context["untrusted_user_input"]
        if "slow" in user_input["task"].lower():
            time.sleep(1.2)
        purpose = context.get("purpose", "")
        if schema_name == "agentplan" or purpose.startswith(("生成动态计划", "重新规划")):
            task = user_input["task"].lower()
            guidance = user_input["supplemental_inputs"]
            return {
                "summary": (
                    "超长事件内容用于验证工作台在连续中文、路径和摘要混排时仍会正确换行。" * 8
                    if "long-event" in task
                    else (
                        f"Apply {len(guidance)} queued guidance items, then verify the artifact digest."
                        if guidance
                        else "Inspect the uploaded artifact metadata and verify its digest."
                    )
                ),
                "steps": (
                    [
                        "Apply queued guidance without expanding scope",
                        "Reuse controlled attachment evidence",
                        "Return sourced digest evidence",
                    ]
                    if guidance
                    else [
                        "Read controlled attachment metadata",
                        "Return sourced digest evidence",
                    ]
                ),
            }
        if schema_name == "importantfacts":
            return {"facts": ["用户偏好分阶段、可回滚的实施方案"]}
        if schema_name == "runretrospectivedraft":
            return {
                "summary": "已根据持久化步骤生成公开复盘。",
                "outcome_review": "复盘不改变已记录的验证状态。",
                "step_reviews": [{
                    "step": 1,
                    "assessment": "步骤已执行并记录观察。",
                    "contribution": "为最终结论提供可核对的事实。",
                }],
                "effective_actions": ["按计划执行已授权动作"],
                "failed_attempts": [],
                "lessons": ["结论应保持对持久化事实的引用"],
                "next_steps": ["仅在授权范围内继续验证"],
            }
        observations = context["untrusted_tool_content"]
        task = user_input["task"].lower()
        if "human-input" in task:
            if not user_input["supplemental_inputs"]:
                return Handler._action("request_input", "Please provide the missing scope.")
            return {
                "kind": "finish",
                "summary": "Produce an advisory answer after human input.",
                "answer": "A staged, reversible rollout is recommended.",
                "reason": "The user supplied the missing scope, so the advisory answer can be completed.",
            }
        if "advisory-only" in task:
            return {
                "kind": "finish",
                "summary": "Produce an advisory answer without external evidence.",
                "answer": "Review the plan with the authorized operator before execution.",
                "reason": "This task only requests an advisory answer and records no external evidence requirement.",
            }
        if "hard-fail" in task:
            return Handler._action("fail", "测试要求明确失败")
        if not observations:
            attachments = context["untrusted_attachment_content"]
            if not attachments:
                return {
                "kind": "finish",
                "summary": "Complete the advisory task without a tool call.",
                "answer": "The requested advisory task completed successfully.",
                "reason": "No controlled attachment is available, so the advisory task can end without a tool call.",
                }
            attachment = attachments[0]
            return {
                "kind": "call_tool",
                "summary": "Compute metadata for the controlled attachment.",
                "tool_name": "file_metadata",
                "tool_input": {"path": attachment["storage_ref"]},
                "reason": "The controlled attachment is the next persisted source available for verification.",
            }
        latest = observations[-1]
        return {
            "kind": "finish",
            "summary": "Submit the tool-produced digest for deterministic verification.",
            "candidate": {
                "value": latest["output"]["sha256"],
                "source_call_id": latest["call_id"],
                "location": "/sha256",
            },
            "reason": "The latest successful tool observation contains the candidate needed for deterministic verification.",
        }

    @staticmethod
    def _action(kind: str, summary: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "summary": summary,
            "reason": "The current task state requires this public next action.",
        }

    def _json(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionAbortedError):
            return

    def _chat_json(self, content: str) -> None:
        self._json(
            200,
            {
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            },
        )

    def _stream(self, content: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        midpoint = max(1, len(content) // 2)
        chunks = [content[:midpoint], content[midpoint:]]
        try:
            for chunk in chunks:
                item = {"choices": [{"delta": {"content": chunk}}]}
                self.wfile.write(f"data: {json.dumps(item, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.04)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError):
            return

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

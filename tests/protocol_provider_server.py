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
        if "slow" in context.get("untrusted_task", "").lower():
            time.sleep(1.2)
        if schema_name == "agentplan" or "计划" in context.get("purpose", ""):
            return {
                "summary": "Inspect the uploaded artifact metadata and verify its digest.",
                "steps": ["Read controlled attachment metadata", "Return sourced digest evidence"],
                "success_approach": "Bind the SHA-256 candidate to the successful tool call.",
            }
        observations = context.get("observations_untrusted", context.get("observations", []))
        task = context.get("untrusted_task", "").lower()
        if "human-input" in task:
            if not context.get("supplemental_inputs"):
                return {"kind": "request_input", "summary": "Please provide the missing scope."}
            return {
                "kind": "finish",
                "summary": "Produce an advisory answer after human input.",
                "answer": "A staged, reversible rollout is recommended.",
            }
        if "advisory-only" in task:
            return {
                "kind": "finish",
                "summary": "Produce an advisory answer without external evidence.",
                "answer": "Review the plan with the authorized operator before execution.",
            }
        if not observations:
            attachment = context.get("attachments_untrusted", context.get("attachments", []))[0]
            return {
                "kind": "call_tool",
                "summary": "Compute metadata for the controlled attachment.",
                "tool_name": "file_metadata",
                "tool_input": {"path": attachment["storage_ref"]},
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

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8899)
    args = parser.parse_args()
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()

from __future__ import annotations

from uuid import uuid4

from yuwang.domain.models import Run, TaskSpec
from yuwang.reports.generator import ReportGenerator


def test_ctf_report_renders_persisted_timeline_without_duplicate_h1() -> None:
    run = Run(thread_id=uuid4())
    task = TaskSpec(body="完成授权 CTF 题", scenario="ctf")
    call_id = str(uuid4())
    steps = [
        {
            "sequence": 1, "call_id": call_id, "goal": "读取首页", "action_summary": "请求首页",
            "observation_summary": "HTTP 200；页面显式链接：/robots.txt", "decision": "下一步：读取 robots.txt",
        },
        {
            "sequence": 2, "call_id": str(uuid4()), "goal": "读取 robots", "action_summary": "请求 robots.txt",
            "observation_summary": "robots.txt 暴露路径：/dev-notes.txt", "decision": "下一步：读取开发说明",
        },
        {
            "sequence": 3, "call_id": str(uuid4()), "goal": "读取说明", "action_summary": "请求 /dev-notes.txt",
            "observation_summary": "开发说明给出 /api/debug 的访问条件", "decision": "下一步：请求调试接口",
        },
        {
            "sequence": 4, "call_id": str(uuid4()), "goal": "读取调试接口", "action_summary": "请求 /api/debug",
            "observation_summary": "响应正文包含 flag_b64 字段", "decision": "下一步：使用 Base64 解码",
        },
        {
            "sequence": 5, "call_id": str(uuid4()), "goal": "解码", "action_summary": "执行 Base64 解码",
            "observation_summary": "解码得到 1 个高置信 Flag 候选", "decision": "下一步：校验 Flag 格式",
        },
        {
            "sequence": 6, "call_id": str(uuid4()), "goal": "校验候选", "action_summary": "检查 Flag 候选格式",
            "observation_summary": "候选 Flag flag{demo}；格式校验状态：format_matched；尚未经过赛题平台验证", "decision": "结束：已完成格式校验",
        },
    ]
    markdown, data = ReportGenerator().generate(run, task, [], {
        "validation_status": "partial", "trace": {"steps": steps, "metrics": {"logical_model_calls": 2, "provider_requests": 3, "tool_calls": 6, "tool_failures": 0}, "artifacts": [{"filename": "debug.json", "kind": "http_response", "size": 42, "sha256": "a" * 64}]},
        "evidence_records": [{"candidate": "flag{demo}", "source_call_id": call_id, "rule_kind": "flag_format", "verified": False}],
    })
    assert data["report_kind"] == "ctf"
    assert sum(line.startswith("# ") and not line.startswith("## ") for line in markdown.splitlines()) == 1
    for path in ("/robots.txt", "/dev-notes.txt", "/api/debug"):
        assert path in markdown
    assert "尚未经过赛题平台验证" in markdown
    assert "Artifact 清单" in markdown
    assert "逻辑模型调用：2，实际 Provider 请求：3" in markdown

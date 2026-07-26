"""显式运行本地评测的命令行入口。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException

from apps.api.config import Settings
from apps.api.context import ApiContext

from .cases import EvaluationCase, builtin_evaluation_cases
from .runner import EvaluationRunner


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="御网智元本地评测")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("list", help="列出内置评测用例，不调用模型")
    run = subcommands.add_parser("run", help="执行选定用例；必须显式指定已配置 Provider")
    run.add_argument("--case", dest="case_ids", action="append", help="用例 ID，可重复指定")
    run.add_argument("--smoke", action="store_true", help="只执行带 smoke 标签的用例")
    run.add_argument("--attempts", type=int, default=1, help="每题尝试次数，默认 1")
    run.add_argument("--provider-id", type=UUID, required=True, help="已连接测试的 Provider 配置 ID")
    run.add_argument("--database", type=Path, default=Path("data/yuwang.db"))
    run.add_argument("--artifacts", type=Path, default=Path("data/artifacts"))
    return parser.parse_args()


def select_cases(arguments: argparse.Namespace) -> tuple[EvaluationCase, ...]:
    cases = builtin_evaluation_cases()
    if arguments.case_ids:
        selected = tuple(case for case in cases if case.case_id in set(arguments.case_ids))
        missing = sorted(set(arguments.case_ids) - {case.case_id for case in selected})
        if missing:
            raise ValueError(f"未知评测用例：{'、'.join(missing)}")
        return selected
    if arguments.smoke:
        return tuple(case for case in cases if "smoke" in case.tags)
    raise ValueError("为避免意外消耗，请使用 --case 或 --smoke 明确选择评测范围")


async def run(arguments: argparse.Namespace) -> int:
    cases = select_cases(arguments)
    config = Settings(database_path=arguments.database, artifact_root=arguments.artifacts)
    context = ApiContext(config)
    provider_configs, provider = context.resolve_provider_chain(arguments.provider_id)
    if provider_configs[0].connection_status != "ok":
        raise ValueError("指定的 Provider 尚未通过连接测试，请先在设置中心完成测试")
    profile = context.profile_service.resolve(None)
    runner = EvaluationRunner(
        arguments.database,
        provider=provider,
        registry=context.registry,
        policy=context.policy,
        profile=profile,
        provider_config=provider_configs[0],
        artifact_root=arguments.artifacts,
    )
    results = await runner.run(cases, attempts=arguments.attempts)
    records = [
        runner.repository.get_evaluation_record(result.record_id)
        for result in results
        if result.record_id is not None
    ]
    print(
        json.dumps(
            {
                "results": [record.model_dump(mode="json") for record in records if record],
                "summary": {
                    "passed": sum(result.status == "passed" for result in results),
                    "failed": sum(result.status == "failed" for result in results),
                    "skipped": sum(result.status == "skipped" for result in results),
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if any(result.status == "failed" for result in results) else 0


def main() -> int:
    arguments = parse_arguments()
    try:
        if arguments.command == "list":
            print(
                json.dumps(
                    [case.model_dump(mode="json") for case in builtin_evaluation_cases()],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        return asyncio.run(run(arguments))
    except (KeyError, ValueError, HTTPException) as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
        print(f"评测未执行：{message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

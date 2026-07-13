"""检测重复动作、重复观察和循环规划。

这些函数没有外部依赖，便于单独理解和测试。指纹只用于进度判断，不作为
安全哈希或数据签名。
"""

from __future__ import annotations

import hashlib
import json

from yuwang.agent.state import AgentDeclaredFailure, AgentStateModel
from yuwang.domain.models import AgentAction, Observation


def action_fingerprint(action: AgentAction) -> str:
    """把影响执行的动作字段归一化为稳定指纹。"""

    value = json.dumps(
        {
            "kind": action.kind,
            "tool": action.tool_name,
            "input": action.tool_input,
            "candidate": action.candidate.model_dump(mode="json") if action.candidate else None,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(value.encode()).hexdigest()


def observation_digest(observation: Observation) -> str:
    """忽略调用编号，只比较观察是否带来新结果。"""

    value = json.dumps(
        {
            "success": observation.success,
            "output": observation.output,
            "error": observation.error,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(value.encode()).hexdigest()


def track_plan_progress(state: AgentStateModel) -> None:
    """记录计划指纹，第三次出现相同计划时安全终止。"""

    if not state.plan:
        return
    fingerprint = hashlib.sha256(state.plan.model_dump_json().encode()).hexdigest()
    repeats = state.plan_fingerprints.count(fingerprint)
    state.plan_fingerprints.append(fingerprint)
    if repeats >= 2:
        raise AgentDeclaredFailure("检测到循环规划，已安全终止")

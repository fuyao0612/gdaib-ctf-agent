from yuwang.evaluation import BUILTIN_EVALUATION_CASES, EvaluationCase, builtin_evaluation_cases


def test_builtin_evaluation_cases_cover_p0_p1_without_ctf_or_executable_payloads():
    cases = builtin_evaluation_cases()

    assert cases == BUILTIN_EVALUATION_CASES
    assert len(cases) >= 30
    assert len({case.case_id for case in cases}) == len(cases)
    assert {
        "普通聊天",
        "意图判断",
        "多步任务",
        "用户纠偏",
        "长上下文",
        "附件",
        "运行控制",
        "模型切换",
        "Provider 生命周期",
        "错误处理",
        "验证语义",
        "Prompt Injection",
        "恢复",
        "Skills",
        "权限分级",
        "运行历史",
    } <= {case.category for case in cases}
    assert all("ctf" not in " ".join(case.user_messages).casefold() for case in cases)
    assert all("```" not in " ".join(case.user_messages) for case in cases)


def test_evaluation_case_rejects_unknown_fields_and_invalid_identifiers():
    try:
        EvaluationCase(
            case_id="Invalid ID",
            name="无效",
            category="测试",
            user_messages=("测试",),
            expected_outcome="chat",
            assertions=("不创建 Run",),
        )
    except ValueError as error:
        assert "case_id" in str(error)
    else:
        raise AssertionError("评测用例应拒绝无效标识符")

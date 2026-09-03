from pathlib import Path

import pytest
import yaml

from yuwang.evaluation import load_task_package
from yuwang.evaluation.packages import MAX_TASK_PACKAGE_ARTIFACT_BYTES, _read_input_file


def test_controlled_local_task_packages_are_complete_and_do_not_expose_judge_to_manifest():
    root = Path("evaluation_cases")
    manifests = [load_task_package(path.parent) for path in sorted(root.rglob("manifest.yaml"))]

    assert {item.scenario for item in manifests} >= {
        "ctf",
        "incident_response",
        "vulnerability_analysis",
        "reverse_static",
    }
    assert {"development", "acceptance"} <= {path.name for path in root.iterdir() if path.is_dir()}
    assert len(manifests) >= 6
    assert all("expected_sha256" not in str(item.model_dump()) for item in manifests)
    assert all(
        all(criterion.get("validator_type") != "result_exists" for criterion in item.criteria)
        for item in manifests
    )
    assert all(
        {"local_judge", "authorization_scope", "budget_respected"}
        <= {str(criterion.get("validator_type")) for criterion in item.criteria}
        for item in manifests
    )


def test_reverse_package_contract_matches_text_search_and_private_judge():
    root = Path("evaluation_cases/reverse-local")
    manifest = load_task_package(root)
    assert manifest.input_artifacts == ["sample.strings"]
    assert "ctf.artifact_content_search" in manifest.allowed_tools
    assert "ctf.binary_static_metadata_analyze" not in manifest.allowed_tools
    assert {"algorithm", "constant", "evidence"} <= set(
        manifest.expected_result_schema["required_fields"]
    )
    assert {str(item["validator_type"]) for item in manifest.criteria} >= {
        "local_judge",
        "tool_called",
        "evidence_source_tool",
    }
    judge = yaml.safe_load((root / "verifier" / "judge.yaml").read_text(encoding="utf-8"))
    assert judge == {
        "judge_type": "structured_fields",
        "expected_fields": {"algorithm": "AES-256-GCM", "constant": "0xC0FFEE"},
    }


def test_multi_layer_encoding_package_requires_chain_and_candidate_selection():
    root = Path("evaluation_cases/acceptance/multi-layer-encoding-local")
    manifest = load_task_package(root)
    assert manifest.scenario == "ctf"
    assert manifest.allowed_tools == ["ctf.artifact_content_search", "ctf.encoding_decode"]
    assert manifest.expected_result_schema["required_fields"] == [
        "value",
        "decode_chain",
        "evidence",
    ]
    assert any(item["validator_type"] == "tool_sequence" for item in manifest.criteria)
    judge = yaml.safe_load((root / "verifier" / "judge.yaml").read_text(encoding="utf-8"))
    assert judge["expected_fields"]["decode_chain"] == ["base64", "base64"]


def test_multi_artifact_package_requires_all_inputs_and_two_tool_families():
    root = Path("evaluation_cases/acceptance/multi-artifact-correlation-local")
    manifest = load_task_package(root)
    assert manifest.input_artifacts == ["timeline.log", "network.log", "files.log"]
    assert any(item["validator_type"] == "artifact_coverage" for item in manifest.criteria)
    assert any(
        item["validator_type"] == "tool_called"
        and set(item["expected_value"]) == {"ctf.artifact_content_search", "ctf.ioc_extract"}
        for item in manifest.criteria
    )
    judge = yaml.safe_load((root / "verifier" / "judge.yaml").read_text(encoding="utf-8"))
    assert set(judge["expected_fields"]) == {"event", "source_ip", "hostname", "file_sha256"}


def test_localhost_web_package_is_scoped_to_single_loopback_target():
    root = Path("evaluation_cases/acceptance/localhost-web-analysis-local")
    manifest = load_task_package(root)
    assert manifest.authorization_scope == ["http://127.0.0.1:8080"]
    assert manifest.allowed_tools == ["builtin.localhost_http_probe"]
    assert "builtin.localhost_http_probe" in manifest.criteria[1]["expected_value"]
    judge = yaml.safe_load((root / "verifier" / "judge.yaml").read_text(encoding="utf-8"))
    assert judge["expected_fields"]["status_code"] == 200
    assert judge["expected_fields"]["explicit_links"] == []
    assert "/api/v1/health" in (root / "inputs" / "web-hints.txt").read_text(encoding="utf-8")


def test_complex_ioc_package_requires_mixed_types_and_redaction_contract():
    root = Path("evaluation_cases/acceptance/complex-ioc-local")
    manifest = load_task_package(root)
    assert manifest.scenario == "incident_response"
    assert manifest.expected_result_schema["required_fields"][-1] == "evidence"
    assert any(item["validator_type"] == "local_judge" for item in manifest.criteria)
    judge = yaml.safe_load((root / "verifier" / "judge.yaml").read_text(encoding="utf-8"))
    assert judge["expected_fields"]["valid_ipv4s"] == ["192.0.2.10"]
    assert "999.999.1.1" in (root / "inputs" / "mixed.log").read_text(encoding="utf-8")


def test_jwt_package_requires_static_analysis_and_bound_evidence():
    root = Path("evaluation_cases/acceptance/jwt-static-analysis-local")
    manifest = load_task_package(root)
    assert manifest.scenario == "vulnerability_analysis"
    assert manifest.allowed_tools == ["ctf.jwt_analyze"]
    assert any(item["validator_type"] == "evidence_source_tool" for item in manifest.criteria)
    judge = yaml.safe_load((root / "verifier" / "judge.yaml").read_text(encoding="utf-8"))
    assert judge["expected_fields"] == {
        "candidate_count": 1,
        "algorithm": "none",
        "subject": "alice",
        "risks": ["empty_signature", "missing_exp"],
    }


def test_task_package_input_size_is_bounded(tmp_path):
    path = tmp_path / "large.bin"
    path.write_bytes(b"x" * (MAX_TASK_PACKAGE_ARTIFACT_BYTES + 1))
    with pytest.raises(ValueError, match="超过"):
        _read_input_file(path, "large.bin")

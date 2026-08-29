from pathlib import Path

from yuwang.evaluation import load_task_package


def test_controlled_local_task_packages_are_complete_and_do_not_expose_judge_to_manifest():
    root = Path("evaluation_cases")
    manifests = [load_task_package(path.parent) for path in sorted(root.rglob("manifest.yaml"))]

    assert {item.scenario for item in manifests} >= {
        "ctf", "incident_response", "vulnerability_analysis", "reverse_static",
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

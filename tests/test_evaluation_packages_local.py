from pathlib import Path

from yuwang.evaluation import load_task_package


def test_controlled_local_task_packages_are_complete_and_do_not_expose_judge_to_manifest():
    root = Path("evaluation_cases")
    manifests = [load_task_package(path) for path in sorted(root.iterdir()) if path.is_dir()]

    assert {item.scenario for item in manifests} == {
        "ctf", "incident_response", "vulnerability_analysis", "reverse_static",
    }
    assert all("expected_sha256" not in str(item.model_dump()) for item in manifests)

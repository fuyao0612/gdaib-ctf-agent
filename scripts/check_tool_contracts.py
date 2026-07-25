"""在不执行外部网络或 CTF Artifact 的前提下检查所有内置工具契约。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from yuwang.tooling import create_reference_registry, validate_registry_contracts
from yuwang.tooling.ctf import register_ctf_tools


class ContractArtifactRepository:
    """仅用于构造 CTF 工具的静态 Spec；脚本不会读取或写入任何 Artifact。"""

    def get_artifact(self, artifact_id: Any) -> None:
        del artifact_id
        return None

    def save_artifact(self, value: Any) -> Any:
        raise AssertionError("契约检查不得创建 Artifact")

    def get_run(self, run_id: Any) -> None:
        del run_id
        return None


def main() -> None:
    with TemporaryDirectory(prefix="yuwang-tool-contract-") as temporary:
        registry = create_reference_registry(Path(temporary))
        register_ctf_tools(registry, ContractArtifactRepository(), Path(temporary))
        ids = validate_registry_contracts(registry)
    print(f"工具契约检查通过：{len(ids)} 个启用工具")


if __name__ == "__main__":
    main()

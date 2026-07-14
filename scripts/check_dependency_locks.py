"""检查 Python/前端直接依赖锁文件与项目声明是否一致。"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]


def read_python_lock(path: Path) -> dict[str, str]:
    """读取只包含直接依赖的 `name==version` 锁文件。"""
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[^]]+\])?==([^\s]+)", line)
        if match is None:
            raise AssertionError(f"{path.name} 含有无法识别的依赖行：{line}")
        result[match.group(1).lower().replace("_", "-")] = match.group(2)
    return result


def check_python_locks(project: dict[str, object]) -> None:
    """保证运行锁和开发锁满足 pyproject 中的直接依赖约束。"""
    runtime = read_python_lock(ROOT / "requirements.runtime.lock")
    development = read_python_lock(ROOT / "requirements.lock")
    groups = (
        ("requirements.runtime.lock", runtime, project["dependencies"]),
        (
            "requirements.lock",
            development,
            [*project["dependencies"], *project["optional-dependencies"]["dev"]],
        ),
    )
    for filename, locked, declarations in groups:
        for declaration in declarations:
            requirement = Requirement(declaration)
            name = requirement.name.lower().replace("_", "-")
            if name not in locked:
                raise AssertionError(f"{filename} 缺少直接依赖 {requirement.name}")
            if Version(locked[name]) not in requirement.specifier:
                raise AssertionError(
                    f"{filename} 中的 {requirement.name}=={locked[name]} "
                    f"不满足 {requirement.specifier}"
                )


def check_frontend_lock(project_version: str) -> None:
    """保证 package.json 没有 latest，且根依赖与 package-lock 完全一致。"""
    package = json.loads((ROOT / "apps/web/package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "apps/web/package-lock.json").read_text(encoding="utf-8"))
    root_lock = lock["packages"][""]
    if package["version"] != project_version or root_lock["version"] != project_version:
        raise AssertionError("前端、锁文件与 Python 项目版本号不一致")
    for group in ("dependencies", "devDependencies"):
        if any(value == "latest" for value in package[group].values()):
            raise AssertionError(f"package.json 的 {group} 仍使用 latest")
        if package[group] != root_lock[group]:
            raise AssertionError(f"package-lock.json 的 {group} 与 package.json 不一致")


def check_ci_version_source() -> None:
    """CI 必须复用版本校验脚本，不得再次复制版本常量。"""

    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    expected_command = "python scripts/check_health_version.py"
    if expected_command not in workflow:
        raise AssertionError(f"CI 健康检查必须调用 {expected_command}")
    if re.search(r"version.+==.+['\"]\d+\.\d+\.\d+['\"]", workflow):
        raise AssertionError("CI 工作流仍含硬编码健康接口版本号")


def main() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    version = project["version"]
    source = (ROOT / "src/yuwang/__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in source:
        raise AssertionError("src/yuwang/__init__.py 与 pyproject.toml 版本号不一致")
    check_python_locks(project)
    check_frontend_lock(version)
    check_ci_version_source()
    print(f"依赖锁文件和 v{version} 版本号一致。")


if __name__ == "__main__":
    main()

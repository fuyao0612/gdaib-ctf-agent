"""Fail when production source contains test-double terminology."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [ROOT / "src", ROOT / "apps" / "api", ROOT / "apps" / "web" / "src"]
EXCLUDED = {"node_modules", "dist", "__pycache__"}
pattern = re.compile(r"\bmo" r"ck\b", re.IGNORECASE)
violations: list[str] = []
for target in TARGETS:
    for path in target.rglob("*"):
        if not path.is_file() or any(part in EXCLUDED for part in path.parts):
            continue
        if ".test." in path.name or path.suffix not in {".py", ".ts", ".tsx", ".js", ".jsx"}:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            violations.append(str(path.relative_to(ROOT)))
if violations:
    raise SystemExit(
        "Production surface contains forbidden test-double references: " + ", ".join(violations)
    )
print("Production source surface is clean.")

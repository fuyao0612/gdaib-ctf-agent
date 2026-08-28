"""生成独立、可重复的无害黄金案例输入，不修改用户 data 目录。"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def create_attachment_case(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "challenge.zip"
    encoded_flag = "ZmxhZ3tkZWNvZGVkX2N0Zn0="
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("notes/readme.txt", "受控本地 CTF 附件。请验证候选，不要执行附件。\n")
        archive.writestr("notes/decoys.txt", "候选：flag{not_the_answer}\n编码：not-base64***\n")
        archive.writestr("evidence/payload.txt", encoded_flag + "\n")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化本地无害黄金案例输入")
    parser.add_argument("--output", type=Path, default=Path("data/golden-demo/A-ctf-attachment"))
    args = parser.parse_args()
    target = create_attachment_case(args.output)
    print(f"created: {target}")


if __name__ == "__main__":
    main()

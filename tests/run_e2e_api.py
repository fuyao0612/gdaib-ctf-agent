"""Start an isolated API instance for Playwright with runtime-only secrets."""

from __future__ import annotations

import os
import tempfile

import uvicorn
from cryptography.fernet import Fernet

root = tempfile.mkdtemp(prefix="yuwang-e2e-")
os.environ.update(
    {
        "YUWANG_DATABASE_PATH": os.path.join(root, "yuwang.db"),
        "YUWANG_ARTIFACT_ROOT": os.path.join(root, "artifacts"),
        "YUWANG_MASTER_KEY": Fernet.generate_key().decode(),
        "YUWANG_ADMIN_TOKEN": os.environ["YUWANG_E2E_ADMIN_TOKEN"],
        "YUWANG_ALLOW_INSECURE_LOCAL_PROVIDER": "true",
    }
)

if __name__ == "__main__":
    uvicorn.run("apps.api.main:app", host="127.0.0.1", port=8000)

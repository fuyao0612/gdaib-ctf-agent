from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken


class SecretCipher:
    """Authenticated encryption backed only by an environment-provided master key."""

    def __init__(self, master_key: str) -> None:
        if not master_key:
            raise ValueError("YUWANG_MASTER_KEY 未配置")
        try:
            self._fernet = Fernet(master_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("YUWANG_MASTER_KEY 必须是 Fernet 32 字节 URL-safe Base64 密钥") from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError) as exc:
            raise ValueError("Provider 密钥无法解密；请检查主密钥") from exc

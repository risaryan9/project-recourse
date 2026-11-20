"""Encryption of Gmail refresh tokens at rest (Fernet: AES-128-CBC + HMAC-SHA256).

The key is TOKEN_ENCRYPTION_KEY. Generate one with:
    uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from recourse.config import Settings


class TokenCipherError(RuntimeError):
    pass


class TokenCipher:
    def __init__(self, key: str) -> None:
        try:
            self._fernet = Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as e:
            raise TokenCipherError("TOKEN_ENCRYPTION_KEY is not a valid Fernet key") from e

    @classmethod
    def from_settings(cls, settings: Settings) -> TokenCipher:
        key = settings.token_encryption_key
        if key is None or not key.get_secret_value().strip():
            raise TokenCipherError("TOKEN_ENCRYPTION_KEY is not set")
        return cls(key.get_secret_value().strip())

    def encrypt(self, token: str) -> str:
        return self._fernet.encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt(self, blob: str) -> str:
        try:
            return self._fernet.decrypt(blob.encode("ascii")).decode("utf-8")
        except InvalidToken as e:
            raise TokenCipherError("the stored token cannot be decrypted with this key") from e

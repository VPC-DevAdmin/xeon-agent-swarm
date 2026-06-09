"""
Connector-secret encryption.

Secrets (API keys, bot tokens, OAuth refresh tokens) are never stored in
plaintext. They are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) keyed by
MASTER_ENCRYPTION_KEY before being written to connector_secrets.ciphertext, and
decrypted only inside the backend at the moment a tool call needs them.

Key rotation: each ciphertext records the key_version it was encrypted under.
To rotate, add the new key to MASTER_ENCRYPTION_KEYS (comma-separated,
newest first) and bump CURRENT_KEY_VERSION; decryption tries keys in order via
MultiFernet, new writes use the current key. Old ciphertexts stay readable
until re-encrypted.

Hard rules (see the rescope discussion):
  1. No plaintext secrets in the DB — only Fernet ciphertext.
  2. The master key is never in code or the DB — it comes from the environment
     (or a KMS in production).
  3. Secrets never leave the backend — API responses strip them.
  4. Every decrypt is audit-logged by the caller (see SecretService).
  5. Rotation is by key_version.

Production hardening path: swap Fernet for Vault transit / KMS envelope
encryption. The storage layer is unchanged because we only ever persist opaque
ciphertext + a key_version.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class EncryptionError(RuntimeError):
    pass


class SecretCipher:
    """Wraps a MultiFernet built from the configured master key(s)."""

    def __init__(self, keys: list[str] | None = None, current_version: int = 1):
        keys = keys if keys is not None else self._keys_from_env()
        if not keys:
            raise EncryptionError(
                "No encryption key configured. Set MASTER_ENCRYPTION_KEY "
                "(or MASTER_ENCRYPTION_KEYS for rotation). Generate one with: "
                "python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        try:
            self._fernets = [Fernet(k.encode() if isinstance(k, str) else k) for k in keys]
        except Exception as exc:  # malformed key
            raise EncryptionError(f"Invalid Fernet key: {exc}") from exc
        self._multi = MultiFernet(self._fernets)
        self.current_version = current_version

    @staticmethod
    def _keys_from_env() -> list[str]:
        # MASTER_ENCRYPTION_KEYS (comma-separated, newest first) takes priority;
        # else the single MASTER_ENCRYPTION_KEY.
        multi = os.getenv("MASTER_ENCRYPTION_KEYS", "").strip()
        if multi:
            return [k.strip() for k in multi.split(",") if k.strip()]
        single = os.getenv("MASTER_ENCRYPTION_KEY", "").strip()
        return [single] if single else []

    def encrypt(self, plaintext: str) -> tuple[bytes, int]:
        """Return (ciphertext, key_version). Encrypts with the newest key."""
        token = self._multi.encrypt(plaintext.encode("utf-8"))
        return token, self.current_version

    def decrypt(self, ciphertext: bytes) -> str:
        """Decrypt, trying all configured keys (newest first)."""
        try:
            return self._multi.decrypt(ciphertext).decode("utf-8")
        except InvalidToken as exc:
            raise EncryptionError(
                "Failed to decrypt secret — wrong key or corrupt ciphertext"
            ) from exc

    def rotate(self, ciphertext: bytes) -> bytes:
        """Re-encrypt an old ciphertext under the newest key."""
        return self._multi.rotate(ciphertext)


_cipher: SecretCipher | None = None


def get_cipher() -> SecretCipher:
    """Lazily build the process-wide cipher from the environment."""
    global _cipher
    if _cipher is None:
        version = int(os.getenv("CURRENT_KEY_VERSION", "1"))
        _cipher = SecretCipher(current_version=version)
    return _cipher


def reset_cipher() -> None:
    """Test hook — drop the cached cipher so env changes take effect."""
    global _cipher
    _cipher = None

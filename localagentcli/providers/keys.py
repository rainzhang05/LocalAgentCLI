"""API key storage — OS keychain with encrypted file fallback."""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import cast

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "localagentcli"


class KeyManager:
    """Manages API key storage with keychain priority and encrypted fallback."""

    def __init__(self, secrets_dir: Path):
        self._secrets_dir = secrets_dir
        self._keyring_available: bool | None = None

    def store_key(self, provider_name: str, api_key: str) -> None:
        """Store an API key. Tries OS keychain first, falls back to encrypted file."""
        if self._try_keyring_store(provider_name, api_key):
            return
        self._file_store(provider_name, api_key)

    def retrieve_key(self, provider_name: str) -> str | None:
        """Retrieve a stored API key."""
        key = self._try_keyring_retrieve(provider_name)
        if key is not None:
            return key
        return self._file_retrieve(provider_name)

    def delete_key(self, provider_name: str) -> None:
        """Delete a stored API key from both keychain and file."""
        self._try_keyring_delete(provider_name)
        self._file_delete(provider_name)

    def has_key(self, provider_name: str) -> bool:
        """Check if a key exists for a provider."""
        return self.retrieve_key(provider_name) is not None

    # -----------------------------------------------------------------------
    # Keychain methods
    # -----------------------------------------------------------------------

    def _is_keyring_available(self) -> bool:
        """Check if the keyring backend is functional."""
        if self._keyring_available is not None:
            return self._keyring_available
        try:
            import keyring  # noqa: F811

            keyring.get_password(KEYRING_SERVICE, "__probe__")
            self._keyring_available = True
        except Exception:
            self._keyring_available = False
        return self._keyring_available

    def _try_keyring_store(self, provider_name: str, api_key: str) -> bool:
        """Attempt to store key in OS keychain. Returns True on success."""
        if not self._is_keyring_available():
            return False
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, provider_name, api_key)
            return True
        except Exception:
            logger.debug("Keyring store failed for %s, falling back to file", provider_name)
            return False

    def _try_keyring_retrieve(self, provider_name: str) -> str | None:
        """Attempt to retrieve key from OS keychain."""
        if not self._is_keyring_available():
            return None
        try:
            import keyring

            return cast(str | None, keyring.get_password(KEYRING_SERVICE, provider_name))
        except Exception:
            logger.debug("Keyring retrieve failed for %s", provider_name)
            return None

    def _try_keyring_delete(self, provider_name: str) -> None:
        """Attempt to delete key from OS keychain."""
        if not self._is_keyring_available():
            return
        try:
            import keyring

            keyring.delete_password(KEYRING_SERVICE, provider_name)
        except Exception:
            logger.debug("Keyring delete failed for %s", provider_name)

    # -----------------------------------------------------------------------
    # Encrypted file fallback
    # -----------------------------------------------------------------------

    def _get_machine_key(self) -> bytes:
        """Get or create a machine-specific encryption key."""
        key_path = self._secrets_dir / ".machine_key"
        if key_path.exists():
            return key_path.read_bytes()
        key = os.urandom(32)
        key_path.write_bytes(key)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return key

    def _encrypt(self, plaintext: str) -> str:
        """Encrypt a string using XOR with machine key, return base64."""
        key = self._get_machine_key()
        data = plaintext.encode("utf-8")
        encrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return base64.b64encode(encrypted).decode("ascii")

    def _decrypt(self, ciphertext: str) -> str:
        """Decrypt a base64 XOR-encrypted string."""
        key = self._get_machine_key()
        encrypted = base64.b64decode(ciphertext)
        decrypted = bytes(b ^ key[i % len(key)] for i, b in enumerate(encrypted))
        return decrypted.decode("utf-8")

    def _key_file_path(self, provider_name: str) -> Path:
        """Return the file path for a provider's encrypted key."""
        safe_name = provider_name.replace("/", "_").replace("\\", "_")
        return self._secrets_dir / f"{safe_name}.key"

    def _file_store(self, provider_name: str, api_key: str) -> None:
        """Store an API key as an encrypted file."""
        self._secrets_dir.mkdir(parents=True, exist_ok=True)
        path = self._key_file_path(provider_name)
        data = json.dumps({"provider": provider_name, "key": self._encrypt(api_key)})
        path.write_text(data, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def _file_retrieve(self, provider_name: str) -> str | None:
        """Retrieve an API key from encrypted file storage."""
        path = self._key_file_path(provider_name)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return self._decrypt(data["key"])
        except Exception:
            logger.debug("Failed to read key file for %s", provider_name)
            return None

    def _file_delete(self, provider_name: str) -> None:
        """Delete the encrypted key file for a provider."""
        path = self._key_file_path(provider_name)
        if path.exists():
            try:
                path.unlink()
            except OSError:
                logger.debug("Failed to delete key file for %s", provider_name)

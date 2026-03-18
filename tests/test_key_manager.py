"""Tests for KeyManager — API key storage with keychain and file fallback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localagentcli.providers.keys import KEYRING_SERVICE, KeyManager


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    """A temporary secrets directory."""
    d = tmp_path / "secrets"
    d.mkdir()
    return d


@pytest.fixture
def key_manager(secrets_dir: Path) -> KeyManager:
    """A KeyManager with keyring disabled (file fallback only)."""
    km = KeyManager(secrets_dir)
    km._keyring_available = False
    return km


# ---------------------------------------------------------------------------
# File fallback tests
# ---------------------------------------------------------------------------


class TestFileStorage:
    def test_store_and_retrieve(self, key_manager: KeyManager):
        key_manager.store_key("openai", "sk-test-123")
        assert key_manager.retrieve_key("openai") == "sk-test-123"

    def test_has_key_true(self, key_manager: KeyManager):
        key_manager.store_key("openai", "sk-test")
        assert key_manager.has_key("openai") is True

    def test_has_key_false(self, key_manager: KeyManager):
        assert key_manager.has_key("nonexistent") is False

    def test_delete_key(self, key_manager: KeyManager):
        key_manager.store_key("openai", "sk-test")
        key_manager.delete_key("openai")
        assert key_manager.has_key("openai") is False

    def test_delete_nonexistent(self, key_manager: KeyManager):
        key_manager.delete_key("nonexistent")  # should not raise

    def test_multiple_providers(self, key_manager: KeyManager):
        key_manager.store_key("openai", "sk-openai")
        key_manager.store_key("anthropic", "sk-anthropic")
        assert key_manager.retrieve_key("openai") == "sk-openai"
        assert key_manager.retrieve_key("anthropic") == "sk-anthropic"

    def test_overwrite_key(self, key_manager: KeyManager):
        key_manager.store_key("openai", "old-key")
        key_manager.store_key("openai", "new-key")
        assert key_manager.retrieve_key("openai") == "new-key"

    def test_empty_string_key(self, key_manager: KeyManager):
        key_manager.store_key("test", "")
        assert key_manager.retrieve_key("test") == ""

    def test_special_characters_in_key(self, key_manager: KeyManager):
        key = "sk-abc123!@#$%^&*()_+-=[]{}|;':\",./<>?"
        key_manager.store_key("test", key)
        assert key_manager.retrieve_key("test") == key

    def test_unicode_key(self, key_manager: KeyManager):
        key_manager.store_key("test", "key-with-unicode-\u00e9\u00e0\u00fc")
        assert key_manager.retrieve_key("test") == "key-with-unicode-\u00e9\u00e0\u00fc"

    def test_key_file_created(self, key_manager: KeyManager, secrets_dir: Path):
        key_manager.store_key("openai", "sk-test")
        assert (secrets_dir / "openai.key").exists()

    def test_key_file_deleted(self, key_manager: KeyManager, secrets_dir: Path):
        key_manager.store_key("openai", "sk-test")
        key_manager.delete_key("openai")
        assert not (secrets_dir / "openai.key").exists()

    def test_slash_in_provider_name(self, key_manager: KeyManager, secrets_dir: Path):
        key_manager.store_key("my/provider", "sk-test")
        assert key_manager.retrieve_key("my/provider") == "sk-test"
        assert (secrets_dir / "my_provider.key").exists()


# ---------------------------------------------------------------------------
# Machine key tests
# ---------------------------------------------------------------------------


class TestMachineKey:
    def test_machine_key_created(self, key_manager: KeyManager, secrets_dir: Path):
        key_manager.store_key("test", "value")
        assert (secrets_dir / ".machine_key").exists()

    def test_machine_key_reused(self, key_manager: KeyManager, secrets_dir: Path):
        key_manager.store_key("test", "value")
        key1 = (secrets_dir / ".machine_key").read_bytes()
        key_manager.store_key("test2", "value2")
        key2 = (secrets_dir / ".machine_key").read_bytes()
        assert key1 == key2

    def test_machine_key_32_bytes(self, key_manager: KeyManager, secrets_dir: Path):
        key_manager.store_key("test", "value")
        key = (secrets_dir / ".machine_key").read_bytes()
        assert len(key) == 32


# ---------------------------------------------------------------------------
# Encryption tests
# ---------------------------------------------------------------------------


class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self, key_manager: KeyManager):
        plaintext = "sk-test-key-12345"
        encrypted = key_manager._encrypt(plaintext)
        assert encrypted != plaintext
        assert key_manager._decrypt(encrypted) == plaintext

    def test_encrypted_is_base64(self, key_manager: KeyManager):
        import base64

        encrypted = key_manager._encrypt("test")
        base64.b64decode(encrypted)  # should not raise


# ---------------------------------------------------------------------------
# Keyring integration tests (mocked)
# ---------------------------------------------------------------------------


class TestKeyringIntegration:
    def test_keyring_available_stores_in_keyring(self, secrets_dir: Path):
        km = KeyManager(secrets_dir)
        km._keyring_available = True
        with patch("keyring.set_password") as mock_set:
            km.store_key("openai", "sk-test")
            mock_set.assert_called_once_with(KEYRING_SERVICE, "openai", "sk-test")

    def test_keyring_available_retrieves_from_keyring(self, secrets_dir: Path):
        km = KeyManager(secrets_dir)
        km._keyring_available = True
        with patch("keyring.get_password", return_value="sk-test"):
            assert km.retrieve_key("openai") == "sk-test"

    def test_keyring_available_deletes_from_keyring(self, secrets_dir: Path):
        km = KeyManager(secrets_dir)
        km._keyring_available = True
        with patch("keyring.delete_password") as mock_del:
            km.delete_key("openai")
            mock_del.assert_called_once_with(KEYRING_SERVICE, "openai")

    def test_keyring_store_failure_falls_back_to_file(self, secrets_dir: Path):
        km = KeyManager(secrets_dir)
        km._keyring_available = True
        with patch("keyring.set_password", side_effect=Exception("keyring error")):
            km.store_key("openai", "sk-test")
        # Should have fallen back to file
        km._keyring_available = False
        assert km.retrieve_key("openai") == "sk-test"

    def test_keyring_retrieve_failure_falls_back_to_file(self, secrets_dir: Path):
        km = KeyManager(secrets_dir)
        # Store via file
        km._keyring_available = False
        km.store_key("openai", "sk-test")
        # Now try keyring first (fails), then file
        km._keyring_available = True
        with patch("keyring.get_password", side_effect=Exception("fail")):
            assert km.retrieve_key("openai") == "sk-test"

    def test_is_keyring_available_probe(self, secrets_dir: Path):
        km = KeyManager(secrets_dir)
        mock_keyring = MagicMock()
        mock_keyring.get_password.return_value = None
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            km._keyring_available = None
            result = km._is_keyring_available()
            assert result is True

    def test_is_keyring_available_probe_fails(self, secrets_dir: Path):
        km = KeyManager(secrets_dir)
        mock_keyring = MagicMock()
        mock_keyring.get_password.side_effect = Exception("no backend")
        with patch.dict("sys.modules", {"keyring": mock_keyring}):
            km._keyring_available = None
            result = km._is_keyring_available()
            assert result is False

    def test_is_keyring_available_caches(self, secrets_dir: Path):
        km = KeyManager(secrets_dir)
        km._keyring_available = True
        assert km._is_keyring_available() is True

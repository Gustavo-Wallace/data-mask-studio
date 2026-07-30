from pathlib import Path

from data_mask_studio.security.key_provider import KEY_SIZE, LocalKeyProvider
from data_mask_studio.vault.key_provider import VaultKeyProvider


class FakeProtector:
    def __init__(self) -> None:
        self.protect_calls = 0
        self.unprotect_calls = 0

    def protect(self, data: bytes) -> bytes:
        self.protect_calls += 1
        return b"protected:" + data[::-1]

    def unprotect(self, data: bytes) -> bytes:
        self.unprotect_calls += 1
        assert data.startswith(b"protected:")
        return data.removeprefix(b"protected:")[::-1]


def test_local_key_is_protected_and_reused(tmp_path: Path) -> None:
    protector = FakeProtector()
    provider = LocalKeyProvider(tmp_path / "DataMaskStudio", protector)

    first_key = provider.get_key()
    stored_content = provider.key_path.read_bytes()
    second_key = provider.get_key()

    assert len(first_key) == KEY_SIZE
    assert second_key == first_key
    assert stored_content != first_key
    assert first_key not in stored_content
    assert protector.protect_calls == 1
    assert protector.unprotect_calls == 1
    assert list(provider.key_path.parent.glob("*.tmp")) == []


def test_default_key_directory_uses_local_app_data(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    provider = LocalKeyProvider(protector=FakeProtector())

    assert provider.key_path == tmp_path / "DataMaskStudio" / "secret.key"


def test_vault_key_is_created_separately_and_protected(tmp_path: Path) -> None:
    protector = FakeProtector()
    storage = tmp_path / "DataMaskStudio"
    hmac_provider = LocalKeyProvider(storage, protector)
    provider = VaultKeyProvider(storage, protector)

    hmac_key = hmac_provider.get_key()
    vault_key = provider.get_key()

    assert provider.key_path.name == "vault_key.dpapi"
    assert hmac_provider.key_path.name == "secret.key"
    assert provider.key_path != hmac_provider.key_path
    assert vault_key != hmac_key
    assert provider.key_path.exists()
    assert provider.key_path.read_bytes() != vault_key
    assert len(vault_key) == KEY_SIZE

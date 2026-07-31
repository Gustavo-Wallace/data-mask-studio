import sqlite3
import struct
from pathlib import Path

import pytest

from data_mask_studio.anonymization import ColumnConfig, generate_token
from data_mask_studio.backup import (
    FORMAT_VERSION,
    MAGIC,
    BackupCancelled,
    BackupError,
    BackupCompatibilityError,
    BackupValidationError,
    CancellationRequest,
    EnvironmentPaths,
    create_backup,
    derive_key,
    restore_backup,
    signature_matches,
    validate_backup,
)
from data_mask_studio.backup.crypto import SCRYPT_PARAMETERS
from data_mask_studio.consultant import ConsultantService, ConsultationStatus
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.security import LocalKeyProvider
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository

PASSWORD = "frase-senha longa e unica"
HMAC_KEY = b"H" * 32
VAULT_KEY = b"V" * 32


class FixedKeyProvider:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def get_key(self) -> bytes:
        return self.key


class FakeProtector:
    def __init__(self, context: bytes) -> None:
        self.context = context
        self.counter = 0

    def protect(self, data: bytes) -> bytes:
        self.counter += 1
        return self.context + bytes([self.counter]) + data

    def unprotect(self, data: bytes) -> bytes:
        prefix_size = len(self.context) + 1
        if not data.startswith(self.context) or len(data) <= prefix_size:
            raise RuntimeError("wrong protection context")
        return data[prefix_size:]


def environment_paths(tmp_path: Path) -> EnvironmentPaths:
    directory = tmp_path / "environment"
    return EnvironmentPaths(
        directory=directory,
        hmac_key_path=directory / "secret.key",
        vault_key_path=directory / "vault_key.dpapi",
        vault_database_path=directory / "vault.db",
        profiles_path=directory / "profiles.json",
    )


def prepare_environment(
    tmp_path: Path,
    *,
    with_mapping: bool = True,
    with_profiles: bool = True,
    protector: FakeProtector | None = None,
) -> tuple[EnvironmentPaths, LocalKeyProvider, LocalKeyProvider, FakeProtector]:
    paths = environment_paths(tmp_path)
    paths.directory.mkdir(parents=True)
    protector = protector or FakeProtector(b"OLD")
    hmac_provider = LocalKeyProvider(
        paths.directory, protector, key_file_name=paths.hmac_key_path.name
    )
    vault_provider = LocalKeyProvider(
        paths.directory, protector, key_file_name=paths.vault_key_path.name
    )
    paths.hmac_key_path.write_bytes(protector.protect(HMAC_KEY))
    paths.vault_key_path.write_bytes(protector.protect(VAULT_KEY))
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    if with_mapping:
        with repository.transaction() as transaction:
            transaction.upsert_batch(
                [MappingCandidate("NOME-ABCDEFGHI234", "NOME", "Valor protegido", "Nome")]
            )
    if with_profiles:
        ProfileService(ProfileRepository(paths.profiles_path)).create(
            "Perfil seguro",
            [ColumnConfig("Nome", True, "NOME", NormalizationRule.EXACT)],
        )
    return paths, hmac_provider, vault_provider, protector


def create_test_backup(
    tmp_path: Path,
    *,
    with_mapping: bool = True,
    with_profiles: bool = True,
) -> tuple[Path, EnvironmentPaths]:
    paths, hmac_provider, vault_provider, _ = prepare_environment(
        tmp_path, with_mapping=with_mapping, with_profiles=with_profiles
    )
    backup = tmp_path / "backup.dmsbackup"
    create_backup(
        backup,
        PASSWORD,
        PASSWORD,
        paths=paths,
        hmac_key_provider=hmac_provider,
        vault_key_provider=vault_provider,
        app_version="0.2.0",
    )
    return backup, paths


def test_backup_with_vault_and_profiles_is_valid(tmp_path: Path) -> None:
    backup, _ = create_test_backup(tmp_path)

    result = validate_backup(backup, PASSWORD, current_app_version="0.2.0")

    assert result.format_version == FORMAT_VERSION
    assert result.mapping_count == 1
    assert result.profile_count == 1
    assert result.vault_present
    assert result.is_compatible
    assert signature_matches(backup)
    assert backup.read_bytes().startswith(MAGIC)


def test_backup_without_profiles_and_with_empty_vault(tmp_path: Path) -> None:
    backup, _ = create_test_backup(
        tmp_path, with_mapping=False, with_profiles=False
    )

    result = validate_backup(backup, PASSWORD, current_app_version="0.2.0")

    assert result.mapping_count == 0
    assert result.profile_count == 0
    assert result.vault_present


def test_backup_without_existing_vault_is_valid(tmp_path: Path) -> None:
    paths, hmac_provider, vault_provider, _ = prepare_environment(
        tmp_path, with_mapping=False, with_profiles=False
    )
    for database_file in paths.directory.glob("vault.db*"):
        database_file.unlink()
    backup = tmp_path / "without-vault.dmsbackup"

    create_backup(
        backup,
        PASSWORD,
        PASSWORD,
        paths=paths,
        hmac_key_provider=hmac_provider,
        vault_key_provider=vault_provider,
        app_version="0.2.0",
    )
    result = validate_backup(backup, PASSWORD, current_app_version="0.2.0")

    assert not result.vault_present
    assert result.mapping_count == 0


def test_scrypt_derivation_is_deterministic_for_same_salt() -> None:
    salt = b"S" * SCRYPT_PARAMETERS.salt_size

    first = derive_key(PASSWORD, salt)
    second = derive_key(PASSWORD, salt)

    assert first == second
    assert len(first) == 32


def test_random_salt_and_nonce_make_equal_backups_different(tmp_path: Path) -> None:
    paths, hmac_provider, vault_provider, _ = prepare_environment(tmp_path)
    first = tmp_path / "first.dmsbackup"
    second = tmp_path / "second.dmsbackup"

    for destination in (first, second):
        create_backup(
            destination,
            PASSWORD,
            PASSWORD,
            paths=paths,
            hmac_key_provider=hmac_provider,
            vault_key_provider=vault_provider,
            app_version="0.2.0",
        )

    assert first.read_bytes() != second.read_bytes()
    assert validate_backup(first, PASSWORD, current_app_version="0.2.0").mapping_count == 1
    assert validate_backup(second, PASSWORD, current_app_version="0.2.0").mapping_count == 1


def test_backup_never_contains_plaintext_secrets_or_vault_value(tmp_path: Path) -> None:
    backup, _ = create_test_backup(tmp_path)
    data = backup.read_bytes()

    assert HMAC_KEY not in data
    assert VAULT_KEY not in data
    assert "Valor protegido".encode() not in data
    assert "Perfil seguro".encode() not in data
    assert b"Nome" not in data
    assert PASSWORD.encode() not in data


def test_wrong_password_tampering_and_non_backup_are_rejected(tmp_path: Path) -> None:
    backup, _ = create_test_backup(tmp_path)
    tampered = tmp_path / "tampered.dmsbackup"
    data = bytearray(backup.read_bytes())
    data[-20] ^= 1
    tampered.write_bytes(data)
    invalid = tmp_path / "invalid.dmsbackup"
    invalid.write_bytes(b"not a backup")

    for path, password in (
        (backup, "senha incorreta com tamanho"),
        (tampered, PASSWORD),
        (invalid, PASSWORD),
    ):
        with pytest.raises(BackupValidationError, match="Verifique a senha"):
            validate_backup(path, password, current_app_version="0.2.0")


def test_unsupported_format_version_is_rejected(tmp_path: Path) -> None:
    backup, _ = create_test_backup(tmp_path)
    data = bytearray(backup.read_bytes())
    data[len(MAGIC) : len(MAGIC) + 2] = struct.pack(">H", 99)
    backup.write_bytes(data)

    with pytest.raises(BackupCompatibilityError):
        validate_backup(backup, PASSWORD, current_app_version="0.2.0")


def test_invalid_crypto_parameters_are_rejected_before_derivation(tmp_path: Path) -> None:
    backup, _ = create_test_backup(tmp_path)
    data = bytearray(backup.read_bytes())
    marker = b'"n":32768'
    position = data.find(marker)
    assert position > 0
    data[position : position + len(marker)] = b'"n":65536'
    backup.write_bytes(data)

    with pytest.raises(BackupValidationError):
        validate_backup(backup, PASSWORD, current_app_version="0.2.0")


def test_restore_is_portable_and_preserves_keys_tokens_profiles_and_consultation(
    tmp_path: Path,
) -> None:
    backup, source_paths = create_test_backup(tmp_path / "source")
    destination_paths = environment_paths(tmp_path / "destination")
    destination_paths.directory.mkdir(parents=True)
    destination_paths.hmac_key_path.write_bytes(b"previous-hmac-blob")
    destination_paths.vault_key_path.write_bytes(b"previous-vault-blob")
    new_protector = FakeProtector(b"NEW")
    token_before = generate_token(HMAC_KEY, "NOME", "Mesmo valor")

    result = restore_backup(
        backup,
        PASSWORD,
        paths=destination_paths,
        protector=new_protector,
        current_app_version="0.2.0",
    )

    restored_hmac = LocalKeyProvider(
        destination_paths.directory,
        new_protector,
        key_file_name=destination_paths.hmac_key_path.name,
    ).get_key()
    restored_vault = LocalKeyProvider(
        destination_paths.directory,
        new_protector,
        key_file_name=destination_paths.vault_key_path.name,
    ).get_key()
    repository = VaultRepository(
        destination_paths.vault_database_path, VaultCipher(restored_vault)
    )
    mapping = repository.get_decrypted_mapping("NOME-ABCDEFGHI234")
    consultation = ConsultantService(lambda: repository).consult("NOME-ABCDEFGHI234")

    assert result.mapping_count == 1
    assert restored_hmac == HMAC_KEY
    assert restored_vault == VAULT_KEY
    assert generate_token(restored_hmac, "NOME", "Mesmo valor") == token_before
    assert mapping is not None and mapping.original_value == "Valor protegido"
    assert consultation[0].status is ConsultationStatus.FOUND
    assert len(ProfileRepository(destination_paths.profiles_path).load()) == 1
    assert destination_paths.hmac_key_path.read_bytes().startswith(b"NEW")
    assert destination_paths.vault_key_path.read_bytes().startswith(b"NEW")
    assert destination_paths.hmac_key_path.read_bytes() != source_paths.hmac_key_path.read_bytes()


def test_restore_without_profiles_removes_existing_profiles(tmp_path: Path) -> None:
    backup, _ = create_test_backup(tmp_path / "source", with_profiles=False)
    destination_paths, _, _, destination_protector = prepare_environment(
        tmp_path / "destination", with_profiles=True
    )

    result = restore_backup(
        backup,
        PASSWORD,
        paths=destination_paths,
        protector=destination_protector,
        current_app_version="0.2.0",
    )

    assert result.profile_count == 0
    assert not destination_paths.profiles_path.exists()


@pytest.mark.parametrize("failure_call", [1, 3, 4])
def test_restore_rolls_back_without_mixed_state_after_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_call: int
) -> None:
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "destination")
    original = {
        path: path.read_bytes()
        for path in (
            paths.hmac_key_path,
            paths.vault_key_path,
            paths.vault_database_path,
            paths.profiles_path,
        )
    }
    from data_mask_studio.backup import restorer

    real_replace = restorer._replace_file
    calls = 0

    def fail_selected(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("simulated restore failure")
        real_replace(source, destination)

    monkeypatch.setattr(restorer, "_replace_file", fail_selected)

    with pytest.raises(Exception):
        restore_backup(
            backup,
            PASSWORD,
            paths=paths,
            protector=protector,
            current_app_version="0.2.0",
        )

    assert all(path.read_bytes() == content for path, content in original.items())
    assert not list(paths.directory.glob(".dms-restore-*"))


def test_creation_and_validation_cancellation_leave_no_final_or_temporary_file(
    tmp_path: Path,
) -> None:
    paths, hmac_provider, vault_provider, _ = prepare_environment(tmp_path)
    destination = tmp_path / "cancelled.dmsbackup"
    cancellation = CancellationRequest()
    cancellation.request()

    with pytest.raises(BackupCancelled):
        create_backup(
            destination,
            PASSWORD,
            PASSWORD,
            paths=paths,
            hmac_key_provider=hmac_provider,
            vault_key_provider=vault_provider,
            cancellation=cancellation,
            app_version="0.2.0",
        )

    assert not destination.exists()
    assert not list(tmp_path.glob("*.tmp"))

    valid_backup, _ = create_test_backup(tmp_path / "valid")
    with pytest.raises(BackupCancelled):
        validate_backup(
            valid_backup,
            PASSWORD,
            cancellation=cancellation,
            current_app_version="0.2.0",
        )


def test_existing_backup_is_not_overwritten_without_permission(tmp_path: Path) -> None:
    paths, hmac_provider, vault_provider, _ = prepare_environment(tmp_path)
    destination = tmp_path / "existing.dmsbackup"
    previous = b"existing backup sentinel"
    destination.write_bytes(previous)

    with pytest.raises(BackupError, match="já existe"):
        create_backup(
            destination,
            PASSWORD,
            PASSWORD,
            paths=paths,
            hmac_key_provider=hmac_provider,
            vault_key_provider=vault_provider,
            app_version="0.2.0",
        )

    assert destination.read_bytes() == previous


def test_restore_cancellation_before_replacement_preserves_environment(
    tmp_path: Path,
) -> None:
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "destination")
    original = {
        path: path.read_bytes()
        for path in (
            paths.hmac_key_path,
            paths.vault_key_path,
            paths.vault_database_path,
            paths.profiles_path,
        )
    }
    cancellation = CancellationRequest()
    cancellation.request()

    with pytest.raises(BackupCancelled):
        restore_backup(
            backup,
            PASSWORD,
            paths=paths,
            protector=protector,
            cancellation=cancellation,
            current_app_version="0.2.0",
        )

    assert all(path.read_bytes() == content for path, content in original.items())


def test_snapshot_integrity_is_valid(tmp_path: Path) -> None:
    backup, _ = create_test_backup(tmp_path)
    destination = environment_paths(tmp_path / "restored")
    destination.directory.mkdir(parents=True)
    restore_backup(
        backup,
        PASSWORD,
        paths=destination,
        protector=FakeProtector(b"NEW"),
        current_app_version="0.2.0",
    )
    connection = sqlite3.connect(destination.vault_database_path)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()

import sqlite3
from pathlib import Path

import pytest

from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.app import create_application
from data_mask_studio.backup import (
    EnvironmentPaths,
    create_backup,
    create_sqlite_snapshot,
    restore_backup,
)
from data_mask_studio.gui.main_window import MainWindow
from data_mask_studio.integrity import IntegrityAuditor, IntegrityStatus
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService
from data_mask_studio.vault import (
    MappingCandidate,
    VaultCipher,
    VaultEncryptionError,
    VaultError,
    VaultRepository,
)
from data_mask_studio.vault.database import CREATE_MAPPINGS_SQL, CREATE_VARIATIONS_SQL

KEY = b"3" * 32
HMAC_KEY = b"H" * 32
PREFIX = "ID"
HEADER = "Identificador"
CANONICAL = "123"
ORIGINAL = "ID-123"
CODE = TokenGenerator(HMAC_KEY).generate(PREFIX, CANONICAL)
PASSWORD = "frase-senha longa para migracao"


class FixedKeyProvider:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def get_key(self) -> bytes:
        return self.key


class FakeProtector:
    def protect(self, data: bytes) -> bytes:
        return b"protected:" + data

    def unprotect(self, data: bytes) -> bytes:
        if not data.startswith(b"protected:"):
            raise RuntimeError("invalid protected data")
        return data.removeprefix(b"protected:")


def environment_paths(directory: Path) -> EnvironmentPaths:
    return EnvironmentPaths(
        directory=directory,
        hmac_key_path=directory / "secret.key",
        vault_key_path=directory / "vault_key.dpapi",
        vault_database_path=directory / "vault.db",
        profiles_path=directory / "profiles.json",
    )


def create_v2_database(path: Path, cipher: VaultCipher) -> None:
    canonical = cipher.encrypt(CODE, PREFIX, CANONICAL)
    variation = cipher.encrypt(CODE, PREFIX, ORIGINAL)
    with sqlite3.connect(path) as connection:
        connection.execute(CREATE_MAPPINGS_SQL)
        connection.execute(CREATE_VARIATIONS_SQL)
        connection.execute(
            "CREATE INDEX vault_variations_code_idx ON vault_variations(code)"
        )
        connection.execute(
            "INSERT INTO vault_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                CODE,
                PREFIX,
                canonical.ciphertext,
                canonical.nonce,
                HEADER,
                NormalizationRule.DIGITS_ONLY.value,
                "2025-01-01T00:00:00+00:00",
                "2025-02-01T00:00:00+00:00",
                5,
            ),
        )
        connection.execute(
            "INSERT INTO vault_variations "
            "(identifier, code, encrypted_value, nonce, normalization_rule, "
            "first_seen, last_seen, occurrence_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                7,
                CODE,
                variation.ciphertext,
                variation.nonce,
                NormalizationRule.DIGITS_ONLY.value,
                "2025-01-01T00:00:00+00:00",
                "2025-02-01T00:00:00+00:00",
                5,
            ),
        )
        connection.execute("PRAGMA user_version = 2")


def encrypted_state(path: Path) -> tuple[tuple[object, ...], tuple[object, ...]]:
    with sqlite3.connect(path) as connection:
        mapping = connection.execute(
            "SELECT canonical_encrypted_value, canonical_nonce FROM vault_mappings"
        ).fetchone()
        variation = connection.execute(
            "SELECT encrypted_value, nonce FROM vault_variations"
        ).fetchone()
    assert mapping is not None and variation is not None
    return mapping, variation


def complete_state(path: Path) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
    with sqlite3.connect(path) as connection:
        mappings = connection.execute(
            "SELECT * FROM vault_mappings ORDER BY code"
        ).fetchall()
        variations = connection.execute(
            "SELECT * FROM vault_variations ORDER BY identifier"
        ).fetchall()
    return mappings, variations


def audit(paths: EnvironmentPaths):
    return IntegrityAuditor(
        paths,
        FixedKeyProvider(HMAC_KEY),
        FixedKeyProvider(KEY),
    ).run()


def audit_check(report, name: str):
    return next(item for item in report.checks if item.check_type == name)


def test_v2_migration_reencrypts_and_preserves_codes_and_metadata(tmp_path: Path) -> None:
    path = tmp_path / "vault.db"
    cipher = VaultCipher(KEY)
    create_v2_database(path, cipher)
    before = encrypted_state(path)
    counts_before = tuple(len(rows) for rows in complete_state(path))

    repository = VaultRepository(path, cipher)
    mapping = repository.get_decrypted_mapping(CODE)

    assert mapping is not None
    assert mapping.code == CODE
    assert mapping.source_header == HEADER
    assert mapping.normalization_rule is NormalizationRule.DIGITS_ONLY
    assert mapping.first_seen == "2025-01-01T00:00:00+00:00"
    assert mapping.last_seen == "2025-02-01T00:00:00+00:00"
    assert mapping.occurrence_count == 5
    assert mapping.canonical_value == CANONICAL
    assert mapping.variations[0].original_value == ORIGINAL
    assert encrypted_state(path) != before
    assert tuple(len(rows) for rows in complete_state(path)) == counts_before == (1, 1)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT identifier FROM vault_variations"
        ).fetchone()[0] == 7


def test_v3_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "vault.db"
    cipher = VaultCipher(KEY)
    create_v2_database(path, cipher)
    VaultRepository(path, cipher)
    first = encrypted_state(path)

    VaultRepository(path, cipher)

    assert encrypted_state(path) == first


def test_v2_migration_processes_multiple_bounded_batches(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "vault.db"
    cipher = VaultCipher(KEY)
    create_v2_database(path, cipher)
    second_canonical = "456"
    second_original = "ID-456"
    second_code = TokenGenerator(HMAC_KEY).generate(PREFIX, second_canonical)
    canonical = cipher.encrypt(second_code, PREFIX, second_canonical)
    variation = cipher.encrypt(second_code, PREFIX, second_original)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO vault_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                second_code,
                PREFIX,
                canonical.ciphertext,
                canonical.nonce,
                HEADER,
                NormalizationRule.DIGITS_ONLY.value,
                "2025-03-01T00:00:00+00:00",
                "2025-04-01T00:00:00+00:00",
                3,
            ),
        )
        connection.execute(
            "INSERT INTO vault_variations "
            "(code, encrypted_value, nonce, normalization_rule, first_seen, "
            "last_seen, occurrence_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                second_code,
                variation.ciphertext,
                variation.nonce,
                NormalizationRule.DIGITS_ONLY.value,
                "2025-03-01T00:00:00+00:00",
                "2025-04-01T00:00:00+00:00",
                3,
            ),
        )
    monkeypatch.setattr("data_mask_studio.vault.database.MIGRATION_BATCH_SIZE", 1)

    repository = VaultRepository(path, cipher)

    assert repository.count() == 2
    second = repository.get_decrypted_mapping(second_code)
    assert second is not None
    assert second.canonical_value == second_canonical
    assert second.variations[0].original_value == second_original
    assert audit(environment_paths(tmp_path)).status is IntegrityStatus.INTACT


def test_read_only_diagnostic_reports_pending_migration_without_corruption(
    tmp_path: Path,
) -> None:
    paths = environment_paths(tmp_path)
    create_v2_database(paths.vault_database_path, VaultCipher(KEY))
    before = encrypted_state(paths.vault_database_path)

    report = audit(paths)

    expected = "O cofre precisa ser migrado antes da verificação de integridade."
    assert report.status is IntegrityStatus.ATTENTION
    assert report.schema_version == 2
    assert audit_check(report, "Versão e estrutura do esquema").message == expected
    aes = audit_check(report, "Autenticação AES-GCM")
    assert aes.status is IntegrityStatus.ATTENTION
    assert aes.examined == 0
    assert aes.failures == 0
    assert encrypted_state(paths.vault_database_path) == before
    assert ORIGINAL not in report.to_safe_text()
    assert CANONICAL not in report.to_safe_text()
    assert CODE not in report.to_safe_text()


def test_application_startup_migrates_v2_before_tabs_are_available(
    tmp_path: Path,
) -> None:
    paths = environment_paths(tmp_path / "local")
    paths.directory.mkdir()
    cipher = VaultCipher(KEY)
    create_v2_database(paths.vault_database_path, cipher)
    profiles = ProfileService(ProfileRepository(paths.profiles_path))

    application = create_application([])
    window = MainWindow(
        key_provider=FixedKeyProvider(HMAC_KEY),
        vault_repository_factory=lambda: VaultRepository(
            paths.vault_database_path, cipher
        ),
        profile_service=profiles,
        backup_paths=paths,
        vault_key_provider=FixedKeyProvider(KEY),
        data_protector=FakeProtector(),
    )

    with sqlite3.connect(paths.vault_database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    assert window.page_stack.count() == 9
    assert audit(paths).status is IntegrityStatus.INTACT
    assert audit_check(audit(paths), "Autenticação AES-GCM").failures == 0

    window.close()
    application.processEvents()


def test_sqlite_snapshot_includes_committed_wal_changes(tmp_path: Path) -> None:
    source = tmp_path / "wal-source.db"
    snapshot = tmp_path / "snapshot.db"
    connection = sqlite3.connect(source)
    try:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
        connection.execute("PRAGMA wal_autocheckpoint = 0")
        connection.execute("CREATE TABLE entries (identifier INTEGER PRIMARY KEY)")
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("INSERT INTO entries VALUES (1)")
        connection.commit()
        assert Path(f"{source}-wal").stat().st_size > 0

        create_sqlite_snapshot(source, snapshot)
    finally:
        connection.close()

    with sqlite3.connect(snapshot) as copied:
        assert copied.execute("SELECT identifier FROM entries").fetchall() == [(1,)]
        assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_v3_migration_rolls_back_all_reencryption_on_failure(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "vault.db"
    cipher = VaultCipher(KEY)
    create_v2_database(path, cipher)
    before = encrypted_state(path)
    complete_before = complete_state(path)

    def fail(*_args, **_kwargs):
        raise RuntimeError("forced migration failure")

    monkeypatch.setattr(cipher, "encrypt_variation", fail)
    with pytest.raises(VaultError):
        VaultRepository(path, cipher)

    assert encrypted_state(path) == before
    assert complete_state(path) == complete_before
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("source_header", "Cabeçalho alterado"),
        ("normalization_rule", NormalizationRule.EXACT.value),
    ],
)
def test_mapping_metadata_is_authenticated(
    tmp_path: Path, column: str, value: str
) -> None:
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(KEY))
    candidate = MappingCandidate(
        CODE,
        PREFIX,
        ORIGINAL,
        HEADER,
        canonical_value=CANONICAL,
        normalization_rule=NormalizationRule.DIGITS_ONLY,
    )
    with repository.transaction() as transaction:
        transaction.upsert_batch([candidate])
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(f"UPDATE vault_mappings SET {column} = ?", (value,))

    with pytest.raises(VaultEncryptionError):
        repository.get_decrypted_mapping(CODE)


def test_variation_identifier_and_mapping_link_are_authenticated(tmp_path: Path) -> None:
    repository = VaultRepository(tmp_path / "vault.db", VaultCipher(KEY))
    with repository.transaction() as transaction:
        transaction.upsert_batch(
            [
                MappingCandidate(
                    CODE,
                    PREFIX,
                    ORIGINAL,
                    HEADER,
                    canonical_value=CANONICAL,
                    normalization_rule=NormalizationRule.DIGITS_ONLY,
                )
            ]
        )
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute("UPDATE vault_variations SET identifier = identifier + 1")

    with pytest.raises(VaultEncryptionError):
        repository.get_decrypted_mapping(CODE)


def test_v2_backup_is_restored_and_migrated_to_v3(
    tmp_path: Path, monkeypatch
) -> None:
    source = environment_paths(tmp_path / "source")
    source.directory.mkdir()
    create_v2_database(source.vault_database_path, VaultCipher(KEY))
    source.hmac_key_path.write_bytes(b"protected:hmac")
    source.vault_key_path.write_bytes(b"protected:vault")
    backup_path = tmp_path / "legacy.dmsbackup"
    monkeypatch.setattr("data_mask_studio.backup.creator.SCHEMA_VERSION", 2)
    create_backup(
        backup_path,
        PASSWORD,
        PASSWORD,
        paths=source,
        hmac_key_provider=FixedKeyProvider(b"H" * 32),
        vault_key_provider=FixedKeyProvider(KEY),
        app_version="0.6.0",
    )

    destination = environment_paths(tmp_path / "destination")
    result = restore_backup(
        backup_path,
        PASSWORD,
        paths=destination,
        protector=FakeProtector(),
        current_app_version="0.7.0",
    )

    assert result.mapping_count == 1
    with sqlite3.connect(destination.vault_database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    repository = VaultRepository(destination.vault_database_path, VaultCipher(KEY))
    mapping = repository.get_decrypted_mapping(CODE)
    assert mapping is not None
    assert mapping.canonical_value == CANONICAL
    assert mapping.variations[0].original_value == ORIGINAL
    assert mapping.code == CODE
    assert mapping.occurrence_count == 5
    assert mapping.first_seen == "2025-01-01T00:00:00+00:00"
    assert mapping.last_seen == "2025-02-01T00:00:00+00:00"
    report = audit(destination)
    assert report.status is IntegrityStatus.INTACT
    assert audit_check(report, "Autenticação AES-GCM").failures == 0

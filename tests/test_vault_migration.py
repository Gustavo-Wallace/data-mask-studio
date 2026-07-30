import sqlite3
from pathlib import Path

from data_mask_studio.consultant import ConsultantService, ConsultationStatus
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.vault import VaultCipher, VaultRepository

KEY = b"M" * 32
CODE = "LEGACY-ABCDEFGHI234"
ORIGINAL_VALUE = "legacy-sensitive-value"

V1_SCHEMA = """
CREATE TABLE vault_mappings (
    code TEXT PRIMARY KEY,
    prefix TEXT NOT NULL,
    encrypted_value BLOB NOT NULL,
    nonce BLOB NOT NULL,
    source_header TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK (occurrence_count > 0)
)
"""


def create_v1_database(path: Path, cipher: VaultCipher) -> None:
    encrypted = cipher.encrypt(CODE, "LEGACY", ORIGINAL_VALUE)
    with sqlite3.connect(path) as connection:
        connection.execute(V1_SCHEMA)
        connection.execute(
            "INSERT INTO vault_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                CODE,
                "LEGACY",
                encrypted.ciphertext,
                encrypted.nonce,
                "Legacy Header",
                "2025-01-01T00:00:00+00:00",
                "2025-02-01T00:00:00+00:00",
                4,
            ),
        )
        connection.execute("PRAGMA user_version = 1")


def test_v1_database_is_migrated_preserving_mapping(tmp_path: Path) -> None:
    database_path = tmp_path / "vault.db"
    cipher = VaultCipher(KEY)
    create_v1_database(database_path, cipher)

    repository = VaultRepository(database_path, cipher)
    mapping = repository.get_decrypted_mapping(CODE)

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        variation_count = connection.execute(
            "SELECT COUNT(*) FROM vault_variations WHERE code = ?", (CODE,)
        ).fetchone()[0]
    assert version == 2
    assert variation_count == 1
    assert mapping is not None
    assert mapping.normalization_rule is NormalizationRule.EXACT
    assert mapping.original_value == ORIGINAL_VALUE
    assert mapping.occurrence_count == 4
    assert mapping.variations[0].occurrence_count == 4


def test_migration_is_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "vault.db"
    cipher = VaultCipher(KEY)
    create_v1_database(database_path, cipher)
    first_repository = VaultRepository(database_path, cipher)
    first_mapping = first_repository.get_decrypted_mapping(CODE)

    second_repository = VaultRepository(database_path, cipher)
    second_mapping = second_repository.get_decrypted_mapping(CODE)

    assert first_mapping == second_mapping
    assert second_repository.count() == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM vault_variations").fetchone()[0] == 1


def test_legacy_mapping_can_be_queried_after_migration(tmp_path: Path) -> None:
    database_path = tmp_path / "vault.db"
    cipher = VaultCipher(KEY)
    create_v1_database(database_path, cipher)
    repository = VaultRepository(database_path, cipher)

    result = ConsultantService(lambda: repository).consult(CODE)[0]

    assert result.status is ConsultationStatus.FOUND
    assert result.mapping is not None
    assert result.mapping.original_value == ORIGINAL_VALUE
    assert result.mapping.normalization_rule is NormalizationRule.EXACT

import sqlite3
from pathlib import Path

import pytest

from data_mask_studio.vault import (
    MappingCandidate,
    VaultCipher,
    VaultCollisionError,
    VaultRepository,
)
from data_mask_studio.vault.database import connect

KEY = b"R" * 32


def make_repository(tmp_path: Path) -> tuple[VaultRepository, VaultCipher]:
    cipher = VaultCipher(KEY)
    return VaultRepository(tmp_path / "vault.db", cipher), cipher


def candidate(
    code: str = "NOME-ABCDEFGHI234",
    value: str = "sensitive-test-value",
    *,
    prefix: str = "NOME",
    header: str = "Nome Completo",
    occurrences: int = 1,
) -> MappingCandidate:
    return MappingCandidate(code, prefix, value, header, occurrences)


def test_database_and_versioned_schema_are_created_automatically(
    tmp_path: Path,
) -> None:
    repository, _ = make_repository(tmp_path)

    assert repository.database_path.exists()
    with sqlite3.connect(repository.database_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("vault_mappings",),
        ).fetchone()
    assert version == 1
    assert table == ("vault_mappings",)
    repository_connection = connect(repository.database_path)
    try:
        assert repository_connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        repository_connection.close()


def test_new_mapping_is_encrypted_and_can_be_validated(tmp_path: Path) -> None:
    repository, cipher = make_repository(tmp_path)
    mapping = candidate()

    with repository.transaction() as transaction:
        transaction.upsert_batch([mapping])
        summary = transaction.summary()

    record = repository.get_record(mapping.code)
    assert record is not None
    assert repository.count() == 1
    assert summary.new_mappings == 1
    assert summary.updated_mappings == 0
    assert mapping.original_value.encode("utf-8") not in record.encrypted_value
    assert cipher.decrypt(
        record.code, record.prefix, record.encrypted_value, record.nonce
    ) == mapping.original_value
    database_files = repository.database_path.parent.glob("vault.db*")
    assert all(mapping.original_value.encode("utf-8") not in path.read_bytes() for path in database_files)


def test_repeated_mapping_increments_occurrences_without_duplicate(
    tmp_path: Path,
) -> None:
    repository, _ = make_repository(tmp_path)
    mapping = candidate(occurrences=2)

    with repository.transaction() as transaction:
        transaction.upsert_batch([mapping])
    first_record = repository.get_record(mapping.code)
    assert first_record is not None

    with repository.transaction() as transaction:
        transaction.upsert_batch([candidate(occurrences=3)])
        summary = transaction.summary()

    second_record = repository.get_record(mapping.code)
    assert second_record is not None
    assert repository.count() == 1
    assert second_record.occurrence_count == 5
    assert second_record.encrypted_value == first_record.encrypted_value
    assert second_record.nonce == first_record.nonce
    assert second_record.first_seen == first_record.first_seen
    assert second_record.last_seen >= first_record.last_seen
    assert summary.new_mappings == 0
    assert summary.updated_mappings == 1


def test_mapping_persists_between_repository_instances(tmp_path: Path) -> None:
    first_repository, _ = make_repository(tmp_path)
    with first_repository.transaction() as transaction:
        transaction.upsert_batch([candidate()])

    second_repository = VaultRepository(first_repository.database_path, VaultCipher(KEY))

    assert second_repository.get_record("NOME-ABCDEFGHI234") is not None


def test_collision_is_detected_without_overwriting_mapping(tmp_path: Path) -> None:
    repository, cipher = make_repository(tmp_path)
    with repository.transaction() as transaction:
        transaction.upsert_batch([candidate()])

    with pytest.raises(VaultCollisionError):
        with repository.transaction() as transaction:
            transaction.upsert_batch([candidate(value="different-sensitive-value")])

    record = repository.get_record("NOME-ABCDEFGHI234")
    assert record is not None
    assert record.occurrence_count == 1
    assert cipher.decrypt(
        record.code, record.prefix, record.encrypted_value, record.nonce
    ) == "sensitive-test-value"


def test_transaction_rolls_back_all_changes_after_error(tmp_path: Path) -> None:
    repository, _ = make_repository(tmp_path)

    with pytest.raises(RuntimeError, match="forced failure"):
        with repository.transaction() as transaction:
            transaction.upsert_batch(
                [
                    candidate(),
                    candidate(
                        "EMAIL-ABCDEFGHI234",
                        "second-sensitive-value",
                        prefix="EMAIL",
                        header="E-mail",
                    ),
                ]
            )
            raise RuntimeError("forced failure")

    assert repository.count() == 0


def test_multiple_prefixes_and_headers_are_preserved(tmp_path: Path) -> None:
    repository, _ = make_repository(tmp_path)
    mappings = [
        candidate(),
        candidate(
            "EMAIL-ABCDEFGHI234",
            "second-sensitive-value",
            prefix="EMAIL",
            header="E-mail Corporativo",
        ),
    ]

    with repository.transaction() as transaction:
        transaction.upsert_batch(mappings)

    first = repository.get_record(mappings[0].code)
    second = repository.get_record(mappings[1].code)
    assert first is not None and second is not None
    assert (first.prefix, first.source_header) == ("NOME", "Nome Completo")
    assert (second.prefix, second.source_header) == ("EMAIL", "E-mail Corporativo")

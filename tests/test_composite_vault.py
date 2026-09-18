from dataclasses import replace
import json
import sqlite3

import pytest

from data_mask_studio.backup import EnvironmentPaths, create_backup, validate_backup
from data_mask_studio.backup.snapshot import create_sqlite_snapshot
from data_mask_studio.backup.validator import extracted_backup
from data_mask_studio.integrity import IntegrityAuditor, IntegrityStatus
from data_mask_studio.normalization import NormalizationRule as Rule
from data_mask_studio.processing.composite_identity import generate_composite_token, serialize_composite_identity
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository
from data_mask_studio.vault import database
from data_mask_studio.vault.composite_models import CompositeMappingCandidate
from data_mask_studio.vault.composite_payload import composite_aad, decode_payload, encode_tuple
from data_mask_studio.vault.composite_repository import CompositeVaultRepository
from data_mask_studio.vault.exceptions import VaultCollisionError, VaultEncryptionError, VaultError

AES = b"V" * 32
HMAC = b"H" * 32


class Provider:
    def __init__(self, key):
        self.key = key

    def get_key(self):
        return self.key


def candidate(original=("Gustavo", "999.999.999-99"), occurrences=1):
    values = ("gustavo", "99999999999")
    return CompositeMappingCandidate(generate_composite_token(HMAC, "CORR", values), "CORR",
                                     values, original, (Rule.PERSON_NAME, Rule.CPF), occurrences)


@pytest.fixture
def repository(tmp_path):
    return CompositeVaultRepository(tmp_path / "vault.db", VaultCipher(AES))


def state(path):
    with sqlite3.connect(path) as connection:
        return tuple(connection.execute(f"SELECT * FROM {table}").fetchall()
                     for table in ("vault_mappings", "vault_variations"))


def schema3(path):
    cipher = VaultCipher(AES)
    code = "CPF-ABCDEFGHI234"
    encrypted = cipher.encrypt_mapping(code, "CPF", "CPF", Rule.CPF, "99999999999")
    variation = cipher.encrypt_variation(7, code, "CPF", "CPF", Rule.CPF, "999.999.999-99")
    with sqlite3.connect(path) as connection:
        connection.execute(database.CREATE_MAPPINGS_SQL)
        connection.execute(database.CREATE_VARIATIONS_SQL)
        connection.execute("CREATE INDEX vault_variations_code_idx ON vault_variations(code)")
        connection.execute("INSERT INTO vault_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (code, "CPF", encrypted.ciphertext, encrypted.nonce, "CPF", Rule.CPF.value,
                            "2025-01-01", "2025-01-02", 3))
        connection.execute("INSERT INTO vault_variations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (7, code, variation.ciphertext, variation.nonce, Rule.CPF.value,
                            "2025-01-01", "2025-01-02", 3))
        connection.execute("PRAGMA user_version = 3")
    return code


def test_migration_preserves_every_scalar_byte_and_is_idempotent(tmp_path):
    path = tmp_path / "vault.db"
    code = schema3(path)
    before = state(path)
    repo = VaultRepository(path, VaultCipher(AES))
    assert state(path) == before
    mapping = repo.get_decrypted_mapping(code)
    assert mapping.canonical_value == "99999999999"
    assert mapping.original_value == "999.999.999-99"
    assert mapping.occurrence_count == mapping.variations[0].occurrence_count == 3
    assert mapping.first_seen == "2025-01-01"
    assert mapping.last_seen == "2025-01-02"
    assert mapping.code == code
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    database.initialize_schema(path, VaultCipher(AES))
    assert state(path) == before


def test_migration_rollback_including_ddl(tmp_path, monkeypatch):
    path = tmp_path / "vault.db"
    schema3(path)
    before = state(path)
    create = database.create_composite_schema

    def fail(connection):
        create(connection)
        raise RuntimeError("injected failure")

    monkeypatch.setattr(database, "create_composite_schema", fail)
    with pytest.raises(VaultError):
        database.initialize_schema(path, VaultCipher(AES))
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT name FROM sqlite_master WHERE name LIKE 'composite_%'").fetchall() == []
    assert state(path) == before
    monkeypatch.setattr(database, "create_composite_schema", create)
    database.initialize_schema(path, VaultCipher(AES))
    assert state(path) == before


def test_clean_schema_and_future_rejection(repository):
    with sqlite3.connect(repository.database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM composite_mappings").fetchone()[0] == 0
        connection.execute("PRAGMA user_version = 5")
    for readonly in (False, True):
        with pytest.raises(VaultError):
            CompositeVaultRepository(repository.database_path, VaultCipher(AES), read_only=readonly)


def test_mapping_tuple_variations_and_occurrences(repository):
    first = candidate(occurrences=2)
    repository.upsert_composite_mapping(first)
    repository.upsert_composite_mapping(candidate(("GUSTAVO", "99999999999"), 3))
    repository.upsert_composite_mapping(first)
    mapping = repository.get_composite_mapping(first.code)
    assert mapping.canonical_values == first.canonical_values
    assert mapping.normalization_rules == first.normalization_rules
    assert mapping.component_count == 2
    assert mapping.identity_version == mapping.payload_version == 1
    assert mapping.occurrence_count == 7
    assert [v.original_values for v in mapping.variations] == [first.original_values, ("GUSTAVO", "99999999999")]
    assert [v.occurrence_count for v in mapping.variations] == [4, 3]
    assert repository.list_composite_variations(first.code) == mapping.variations
    assert mapping.first_seen <= mapping.last_seen
    assert first.original_values[0] not in repr(first)
    assert first.canonical_values[0] not in repr(mapping)
    assert all("GUSTAVO" not in repr(v) for v in mapping.variations)
    assert repository.as_read_only().get_composite_mapping(first.code) == mapping
    with pytest.raises(VaultError):
        repository.as_read_only().upsert_composite_mapping(first)


@pytest.mark.parametrize("values", [("José", "🔒"), ("", "ABC"), ("", ""), ("A;", "B\0C")])
def test_exact_unicode_empty_tuple_round_trip(repository, values):
    item = CompositeMappingCandidate(generate_composite_token(HMAC, "CORR", values), "CORR", values, values, (Rule.EXACT, Rule.EXACT))
    repository.upsert_composite_mapping(item)
    assert repository.get_composite_mapping(item.code).variations[0].original_values == values
    assert decode_payload(serialize_composite_identity(values)) == values
    assert decode_payload(encode_tuple(values), original=True) == values


@pytest.mark.parametrize("column, value", [
    ("encrypted_value", b"tampered"), ("identity_version", 2), ("payload_version", 2),
    ("aad_version", 2), ("component_count", 3), ("rules", '["cpf","person_name"]'),
    ("prefix", "OTHER"),
])
def test_mapping_ciphertext_and_aad_tampering(repository, column, value):
    item = candidate()
    repository.upsert_composite_mapping(item)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(f"UPDATE composite_mappings SET {column} = ?", (value,))
    with pytest.raises(VaultEncryptionError):
        repository.get_composite_mapping(item.code)


@pytest.mark.parametrize("column, value", [("encrypted_value", b"tampered"), ("identifier", "another-id")])
def test_variation_ciphertext_and_identifier_tampering(repository, column, value):
    item = candidate()
    repository.upsert_composite_mapping(item)
    with sqlite3.connect(repository.database_path) as connection:
        connection.execute(f"UPDATE composite_variations SET {column} = ?", (value,))
    with pytest.raises(VaultEncryptionError):
        repository.get_composite_mapping(item.code)


def test_variation_reparenting_and_record_type_aad_fail(repository):
    first = candidate()
    second = replace(first, code="OTHER-ABCDEFGHI234", prefix="OTHER")
    repository.upsert_composite_mapping(first)
    repository.upsert_composite_mapping(second)
    with sqlite3.connect(repository.database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM composite_mappings WHERE code = ?", (first.code,)).fetchone()
        with pytest.raises(VaultEncryptionError):
            repository._cipher.decrypt_payload(row["encrypted_value"], row["nonce"], composite_aad(row, "variation"))
        with pytest.raises(VaultEncryptionError):
            repository._cipher.decrypt_mapping(first.code, first.prefix, "Header", Rule.EXACT, row["encrypted_value"], row["nonce"])
        connection.execute("UPDATE composite_variations SET code = ? WHERE code = ?", (second.code, first.code))
    with pytest.raises(VaultEncryptionError):
        repository.get_composite_mapping(second.code)


def test_collision_never_overwrites_identity_or_counters(repository):
    item = candidate()
    repository.upsert_composite_mapping(item)
    before = repository.get_composite_mapping(item.code)
    other = replace(item, canonical_values=("outro", "99999999999"), original_values=("Outro", "99999999999"))
    with pytest.raises(VaultCollisionError):
        repository.upsert_composite_mapping(other)
    assert repository.get_composite_mapping(item.code) == before


@pytest.mark.parametrize("scalar_first", [True, False])
def test_global_code_uniqueness_and_explicit_type(repository, scalar_first):
    item = candidate()
    scalar = MappingCandidate(item.code, item.prefix, "scalar", "Header")

    def insert_scalar():
        with repository.transaction() as transaction:
            transaction.upsert_batch([scalar])

    if scalar_first:
        insert_scalar()
        assert repository.get_composite_mapping(item.code) is None
        with pytest.raises(VaultCollisionError):
            repository.upsert_composite_mapping(item)
    else:
        repository.upsert_composite_mapping(item)
        assert repository.get_decrypted_mapping(item.code) is None
        with pytest.raises(VaultCollisionError):
            insert_scalar()


def test_upsert_failure_rolls_back_mapping_and_counts(repository, monkeypatch):
    item = candidate()
    encrypt = repository._cipher.encrypt_payload

    def fail(payload, aad):
        if b'composite_variation' in aad:
            raise VaultError("injected failure")
        return encrypt(payload, aad)

    monkeypatch.setattr(repository._cipher, "encrypt_payload", fail)
    with pytest.raises(VaultError):
        repository.upsert_composite_mapping(item)
    assert repository.get_composite_mapping(item.code) is None


def test_existing_counts_rollback_and_metadata_conflict(repository, monkeypatch):
    item = candidate()
    repository.upsert_composite_mapping(item)
    before = repository.get_composite_mapping(item.code)
    # Mesmo canonical, regras diferentes: não sobrescrever metadata autenticada.
    changed = replace(item, original_values=item.canonical_values, normalization_rules=(Rule.EXACT, Rule.EXACT))
    with pytest.raises(VaultCollisionError):
        repository.upsert_composite_mapping(changed)

    def fail(*args):
        raise VaultError("injected failure")

    monkeypatch.setattr(repository._cipher, "encrypt_payload", fail)
    with pytest.raises(VaultError):
        repository.upsert_composite_mapping(candidate(("GUSTAVO", "99999999999")))
    assert repository.get_composite_mapping(item.code) == before


@pytest.mark.parametrize("changes", [
    {"occurrences": 0}, {"identity_version": 2}, {"payload_version": 2},
    {"normalization_rules": (Rule.EXACT,)}, {"original_values": ("Gustavo",)},
    {"original_values": ("Outra pessoa", "99999999999")},
])
def test_invalid_candidate_is_controlled_and_not_persisted(repository, changes):
    item = replace(candidate(), **changes)
    with pytest.raises(VaultError) as error:
        repository.upsert_composite_mapping(item)
    assert "Gustavo" not in str(error.value)
    assert "Outra pessoa" not in str(error.value)
    assert repository.get_composite_mapping(item.code) is None


def test_sqlite_cross_type_trigger_and_no_plaintext(repository):
    item = candidate()
    repository.upsert_composite_mapping(item)
    with sqlite3.connect(repository.database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="collision"):
            connection.execute("INSERT INTO vault_mappings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (item.code, item.prefix, b"cipher", b"nonce", "Header", "exact", "date", "date", 1))
    for path in repository.database_path.parent.glob("vault.db*"):
        stored = path.read_bytes()
        for value in (*item.canonical_values, *item.original_values):
            assert value.encode("utf-8") not in stored


@pytest.mark.parametrize("payload", [b"", b"DMSCI\x02" + b"\0" * 20, serialize_composite_identity(("A", "B")) + b"extra", serialize_composite_identity(("A", "B"))[:-1]])
def test_malformed_authenticated_payload_rejected(repository, payload):
    item = candidate()
    repository.upsert_composite_mapping(item)
    with sqlite3.connect(repository.database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM composite_mappings").fetchone()
        encrypted = repository._cipher.encrypt_payload(payload, composite_aad(row))
        connection.execute("UPDATE composite_mappings SET encrypted_value = ?, nonce = ?", (encrypted.ciphertext, encrypted.nonce))
    with pytest.raises(VaultError):
        repository.get_composite_mapping(item.code)


def test_snapshot_backup_and_audit_recognize_composites(repository, tmp_path):
    item = candidate()
    # Mantém WAL aberto durante o snapshot; não copiar sidecars manualmente.
    keeper = database.connect(repository.database_path)
    try:
        repository.upsert_composite_mapping(item)
        snapshot = tmp_path / "snapshot.db"
        create_sqlite_snapshot(repository.database_path, snapshot)
        assert CompositeVaultRepository(snapshot, VaultCipher(AES), read_only=True).get_composite_mapping(item.code)
    finally:
        keeper.close()
    paths = EnvironmentPaths(tmp_path, tmp_path / "secret.key", tmp_path / "vault_key.dpapi", repository.database_path, tmp_path / "profiles.json")
    report = IntegrityAuditor(paths, Provider(HMAC), Provider(AES)).run()
    assert report.schema_version == 4
    assert report.status is IntegrityStatus.INTACT
    assert any("compostos" in check.check_type and check.examined == 1 for check in report.checks)
    assert "Gustavo" not in report.to_safe_text()
    backup = tmp_path / "composite.dmsbackup"
    password = "senha longa de teste seguro"
    create_backup(backup, password, password, paths=paths, hmac_key_provider=Provider(HMAC), vault_key_provider=Provider(AES))
    assert validate_backup(backup, password).vault_schema_version == 4
    with extracted_backup(backup, password) as extracted:
        restored = CompositeVaultRepository(extracted.vault_snapshot_path, VaultCipher(extracted.vault_key), read_only=True)
        assert restored.get_composite_mapping(item.code) == repository.get_composite_mapping(item.code)

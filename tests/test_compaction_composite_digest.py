"""Compaction compares every persisted scalar/composite field using real SQLite."""

from contextlib import closing
from dataclasses import replace
import sqlite3

import pytest

from data_mask_studio.integrity import IntegrityAuditor, IntegrityStatus
from data_mask_studio.maintenance import MaintenanceCancelled, MaintenanceError, VaultCompactor
from data_mask_studio.maintenance.compaction_messages import CompactionReason
import data_mask_studio.maintenance.vault_compactor as module
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.vault import VaultCipher, VaultRepository
from data_mask_studio.vault.composite_models import CompositeMappingCandidate
from data_mask_studio.normalization import NormalizationRule as Rule
from test_maintenance import prepare_environment, FixedKeyProvider, HMAC_KEY, VAULT_KEY


TABLES = {
    "vault_mappings": ("code", "prefix", "canonical_encrypted_value", "canonical_nonce", "source_header",
                       "normalization_rule", "first_seen", "last_seen", "total_occurrences"),
    "vault_variations": ("identifier", "code", "encrypted_value", "nonce", "normalization_rule",
                         "first_seen", "last_seen", "occurrence_count"),
    "composite_mappings": ("code", "prefix", "identity_version", "payload_version", "aad_version",
                           "component_count", "rules", "encrypted_value", "nonce", "first_seen",
                           "last_seen", "total_occurrences"),
    "composite_variations": ("identifier", "code", "encrypted_value", "nonce", "first_seen",
                             "last_seen", "occurrence_count"),
}


@pytest.fixture
def mixed(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    paths, repository, _ = prepare_environment(tmp_path)
    composite = repository.composite_repository()
    items = []
    for canonical, original in (
        (("gustavo", "99999999999"), ("Gustavo", "999.999.999-99")),
        (("ana", "12345678900"), ("Ana", "123.456.789-00")),
    ):
        code = generate_composite_token(HMAC_KEY, "CORR", canonical)
        item = CompositeMappingCandidate(code, "CORR", canonical, original, (Rule.PERSON_NAME, Rule.CPF), 2)
        composite.upsert_composite_mapping(item)
        composite.upsert_composite_mapping(replace(item, original_values=(original[0].upper(), canonical[1]), occurrences=3))
        items.append(item)
    module._checkpoint(paths.vault_database_path)
    auditor = IntegrityAuditor(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY))
    assert auditor.run().status is IntegrityStatus.INTACT
    return paths, repository, tuple(items)


def edit(database, query, parameters=()):
    # Close/checkpoint every writer so the digest reads durable DB contents,
    # not a stale main file underneath an outstanding synthetic WAL writer.
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(query, parameters)
        connection.commit()
        assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0


def contents(database):
    with closing(sqlite3.connect(database)) as connection:
        return tuple(tuple(connection.execute(f"SELECT * FROM {table} ORDER BY {columns[0]}"))
                     for table, columns in TABLES.items())


def damage(database, kind):
    if kind == "remove":
        edit(database, "DELETE FROM composite_variations")
        edit(database, "DELETE FROM composite_mappings")
    elif kind == "mapping":
        edit(database, "UPDATE composite_mappings SET total_occurrences = total_occurrences + 1")
    elif kind == "variation":
        edit(database, "UPDATE composite_variations SET occurrence_count = occurrence_count + 1")
    else:
        edit(database, "DELETE FROM composite_variations WHERE identifier = "
                       "(SELECT identifier FROM composite_variations ORDER BY identifier LIMIT 1)")


@pytest.mark.parametrize("kind", ["remove", "remove-variation"])
def test_composite_record_removal_changes_digest(mixed, kind):
    paths, _, _ = mixed
    before = module._logical_digest(paths.vault_database_path)
    damage(paths.vault_database_path, kind)
    assert module._logical_digest(paths.vault_database_path) != before


@pytest.mark.parametrize("table,column", [(table, column) for table, columns in TABLES.items() for column in columns])
def test_every_scalar_and_composite_field_affects_digest(mixed, table, column):
    paths, _, _ = mixed
    before = module._logical_digest(paths.vault_database_path)
    with closing(sqlite3.connect(paths.vault_database_path)) as connection:
        actual_columns = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
        assert set(actual_columns) == set(TABLES[table])
        value = connection.execute(f"SELECT {column} FROM {table} ORDER BY {TABLES[table][0]} LIMIT 1").fetchone()[0]
        if column == TABLES[table][0] and isinstance(value, int):
            changed = connection.execute(f"SELECT MAX({column}) + 1 FROM {table}").fetchone()[0]
        else:
            changed = value + (b"changed" if isinstance(value, bytes) else 1 if isinstance(value, int) else "changed")
    edit(paths.vault_database_path, f"UPDATE {table} SET {column} = ? WHERE {TABLES[table][0]} = "
         f"(SELECT {TABLES[table][0]} FROM {table} ORDER BY {TABLES[table][0]} LIMIT 1)", (changed,))
    assert module._logical_digest(paths.vault_database_path) != before


def test_digest_distinguishes_blob_and_text_with_identical_bytes(mixed):
    paths, _, _ = mixed
    before = module._logical_digest(paths.vault_database_path)
    edit(paths.vault_database_path, "UPDATE vault_mappings SET source_header = CAST(source_header AS BLOB)")
    assert module._logical_digest(paths.vault_database_path) != before


def test_reversed_physical_row_order_preserves_digest(mixed, tmp_path):
    paths, _, _ = mixed
    database = paths.vault_database_path
    before, expected = module._logical_digest(database), contents(database)
    reordered = tmp_path / "reordered.db"
    module.create_sqlite_snapshot(database, reordered)
    with closing(sqlite3.connect(reordered)) as connection:
        physical = {table: tuple(connection.execute(f"SELECT * FROM {table} ORDER BY rowid")) for table in TABLES}
        for table in reversed(TABLES):
            connection.execute(f"DELETE FROM {table}")
        for table, columns in TABLES.items():
            placeholders = ",".join("?" for _ in columns)
            connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", reversed(physical[table]))
        connection.commit()
        assert tuple(connection.execute("SELECT * FROM composite_mappings ORDER BY rowid")) == tuple(reversed(physical["composite_mappings"]))
        assert tuple(connection.execute("SELECT * FROM composite_variations ORDER BY rowid")) == tuple(reversed(physical["composite_variations"]))
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
    assert contents(reordered) == expected
    assert module._logical_digest(reordered) == before


@pytest.mark.parametrize("missing", [("composite_variations",), ("composite_mappings",),
                                     ("composite_variations", "composite_mappings")])
def test_schema_four_requires_both_composite_tables_even_if_scalar_only(mixed, missing):
    paths, _, _ = mixed
    damage(paths.vault_database_path, "remove")
    for table in missing:
        edit(paths.vault_database_path, f"DROP TABLE {table}")
    with pytest.raises(MaintenanceError) as caught:
        module._logical_digest(paths.vault_database_path)
    assert caught.value.explanation.reason is CompactionReason.COMPARISON_FAILED
    with pytest.raises(MaintenanceError):
        VaultCompactor(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)).compact()


@pytest.mark.parametrize("version", [1, 2, 3, 5])
def test_compaction_still_rejects_non_current_schema_without_migration(mixed, version):
    paths, _, _ = mixed
    edit(paths.vault_database_path, f"PRAGMA user_version = {version}")
    before = contents(paths.vault_database_path)
    with pytest.raises(MaintenanceError) as caught:
        VaultCompactor(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)).compact()
    assert caught.value.explanation.reason is CompactionReason.STRUCTURE_INVALID
    assert contents(paths.vault_database_path) == before
    with closing(sqlite3.connect(paths.vault_database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == version


@pytest.mark.parametrize("stage,reason", [("snapshot", CompactionReason.SNAPSHOT_MISMATCH),
                                         ("vacuum", CompactionReason.CONTENT_CHANGED),
                                         ("post", CompactionReason.POST_VALIDATION_FAILED)])
@pytest.mark.parametrize("kind", ["remove", "mapping", "variation"])
def test_compaction_rejects_composite_changes_and_restores_original_when_needed(mixed, monkeypatch, stage, reason, kind):
    paths, _, _ = mixed
    database = paths.vault_database_path
    before = contents(database)
    function = {"snapshot": "create_sqlite_snapshot", "vacuum": "_vacuum_into", "post": "_optimize"}[stage]
    original = getattr(module, function)

    def tamper(*args, **kwargs):
        result = original(*args, **kwargs)
        damage(args[1] if stage != "post" else args[0], kind)
        return result

    monkeypatch.setattr(module, function, tamper)
    with pytest.raises(MaintenanceError) as caught:
        VaultCompactor(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)).compact()
    assert caught.value.explanation.reason is reason
    assert caught.value.explanation.rollback_completed is (stage == "post")
    assert contents(database) == before
    assert not list(paths.directory.glob("dms-vault-compaction-*"))


def test_intact_mixed_compaction_preserves_all_records_and_authentication(mixed):
    paths, _, items = mixed
    before, digest = contents(paths.vault_database_path), module._logical_digest(paths.vault_database_path)
    result = VaultCompactor(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)).compact()
    assert result.audit.status is IntegrityStatus.INTACT
    assert contents(paths.vault_database_path) == before
    assert module._logical_digest(paths.vault_database_path) == digest
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY), read_only=True)
    for item in items:
        mapping = repository.composite_repository().get_composite_mapping(item.code)
        assert mapping.canonical_values == item.canonical_values
        assert mapping.occurrence_count == 5 and len(mapping.variations) == 2


def test_mixed_snapshot_is_retained_when_post_swap_verification_and_rollback_fail(mixed, monkeypatch):
    paths, _, items = mixed
    before, digest = contents(paths.vault_database_path), module._logical_digest(paths.vault_database_path)
    optimize = module._optimize

    def damage_after_swap(database):
        optimize(database)
        damage(database, "remove")

    def cannot_restore(*args):
        raise PermissionError("Synthetic rollback failure")

    monkeypatch.setattr(module, "_optimize", damage_after_swap)
    monkeypatch.setattr(module, "_restore_snapshot", cannot_restore)
    with pytest.raises(MaintenanceError) as caught:
        VaultCompactor(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)).compact()
    assert caught.value.explanation.reason is CompactionReason.ROLLBACK_FAILED
    root = paths.directory / caught.value.explanation.recovery_directory
    assert root.parent == paths.directory and root.is_dir()
    snapshot = root / "vault-snapshot.db"
    assert contents(snapshot) == before and module._logical_digest(snapshot) == digest
    repository = VaultRepository(snapshot, VaultCipher(VAULT_KEY), read_only=True)
    assert all(repository.composite_repository().get_composite_mapping(item.code) for item in items)
    assert contents(paths.vault_database_path) != before


def test_interruption_after_mixed_vacuum_preserves_original(mixed, monkeypatch):
    paths, _, _ = mixed
    before = contents(paths.vault_database_path)
    vacuum = module._vacuum_into
    cancelled = False

    def interrupt(*args):
        nonlocal cancelled
        vacuum(*args)
        cancelled = True

    monkeypatch.setattr(module, "_vacuum_into", interrupt)
    with pytest.raises(MaintenanceCancelled):
        VaultCompactor(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)).compact(should_cancel=lambda: cancelled)
    assert contents(paths.vault_database_path) == before
    assert not list(paths.directory.glob("dms-vault-compaction-*"))

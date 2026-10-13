"""Real paged SQLite backups with WAL commits/checkpoints between copy steps."""

import sqlite3
import threading
import hashlib
import multiprocessing
from contextlib import closing
from pathlib import Path

import pytest

from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.backup import (
    BackupCancelled, BackupError, BackupValidationError, CancellationRequest,
    create_backup, restore_backup, validate_backup,
)
from data_mask_studio.backup import creator
from data_mask_studio.backup import validator
from data_mask_studio.backup import snapshot as snapshot_module, restorer
from data_mask_studio.backup.validator import extracted_backup
from data_mask_studio.backup.models import BackupStage
from data_mask_studio.backup.exceptions import VALIDATION_FAILURE_MESSAGE
from data_mask_studio.environment import environment_lease
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository
from data_mask_studio.vault.composite_models import CompositeMappingCandidate
from test_backup import (
    prepare_environment, environment_paths, FakeProtector, PASSWORD, HMAC_KEY, VAULT_KEY,
)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


def seed_vault(root):
    paths, hmac, aes, _ = prepare_environment(root, with_mapping=False, with_profiles=False)
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    candidates = []
    for index in range(320):
        value = f"Synthetic scalar {index:04d}:" + "X" * 2048
        code = TokenGenerator(HMAC_KEY).generate("SCALAR", value)
        candidates.append(MappingCandidate(code, "SCALAR", value, "Synthetic header"))
    with repository.transaction() as transaction:
        transaction.upsert_batch(candidates)
    composite_codes = []
    for index in range(2):
        values = (f"Synthetic component {index}", "Synthetic second component")
        code = generate_composite_token(HMAC_KEY, "PAIR", values)
        repository.composite_repository().upsert_composite_mapping(CompositeMappingCandidate(
            code, "PAIR", values, values, (NormalizationRule.EXACT, NormalizationRule.EXACT),
        ))
        composite_codes.append(code)
    with closing(sqlite3.connect(paths.vault_database_path)) as connection:
        # Arrange healthy tables in contiguous page ranges so a torn copy is
        # deterministic, not a timing-dependent race over a tiny database.
        connection.execute("VACUUM")
        assert connection.execute("PRAGMA page_count").fetchone()[0] > 256
    assert not any(paths.vault_database_path.parent.glob("vault.db-*"))
    return paths, hmac, aes, tuple(item.code for item in candidates), tuple(composite_codes)


def mismatch_count(path):
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        return sum(connection.execute(f"""
            SELECT COUNT(*) FROM {mappings} AS m
            WHERE m.total_occurrences != COALESCE((
                SELECT SUM(v.occurrence_count) FROM {variations} AS v WHERE v.code = m.code
            ), 0)
        """).fetchone()[0] for mappings, variations in (
            ("vault_mappings", "vault_variations"), ("composite_mappings", "composite_variations")))


def attest_records(path, scalar_codes, composite_codes):
    repository = VaultRepository(path, VaultCipher(VAULT_KEY), read_only=True)
    with repository.read_session() as session:
        for offset in range(0, len(scalar_codes), 100):
            records = session.get_many(scalar_codes[offset:offset + 100])
            assert len(records) == len(scalar_codes[offset:offset + 100])
            for record in records.values():
                assert record.occurrence_count == sum(v.occurrence_count for v in record.variations)
    composites = repository.composite_repository()
    for code in composite_codes:
        record = composites.get_composite_mapping(code)
        assert record is not None
        assert record.occurrence_count == sum(v.occurrence_count for v in record.variations)


def commit_and_checkpoint(database, connect=sqlite3.connect):
    # Same shared environment lease as legitimate DMS writers. The transaction
    # changes counters together; encrypted values, identities and dates stay intact.
    with environment_lease(database.parent):
        with closing(connect(database, timeout=2)) as connection:
            connection.execute("PRAGMA wal_autocheckpoint = 0")
            connection.execute("BEGIN IMMEDIATE")
            for table, counter in (("vault_mappings", "total_occurrences"),
                                   ("vault_variations", "occurrence_count"),
                                   ("composite_mappings", "total_occurrences"),
                                   ("composite_variations", "occurrence_count")):
                connection.execute(f"UPDATE {table} SET {counter} = {counter} + 1")
            connection.commit()
            assert database.with_name(database.name + "-wal").is_file()
            return connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()


def process_writer(database, pipe):
    try:
        pipe.send((True, commit_and_checkpoint(Path(database))))
    except BaseException as error:
        pipe.send((False, type(error).__name__))
    finally:
        pipe.close()


def writer_between_pages(monkeypatch, database, *, mode="thread", cancellation=None):
    real_connect = sqlite3.connect
    observed = {"steps": 0, "committed": False, "checkpoint": None, "errors": [], "uris": []}

    def writer():
        try:
            observed["checkpoint"] = commit_and_checkpoint(database, real_connect)
            observed["committed"] = True
        except BaseException as error:
            observed["errors"].append(type(error).__name__)

    class SourceConnection(sqlite3.Connection):
        def backup(self, target, *, pages=-1, progress=None, name="main", sleep=0.250):
            def between(status, remaining, total):
                observed["steps"] += 1
                if remaining and not observed["committed"]:
                    if mode == "process":
                        context = multiprocessing.get_context("spawn")
                        incoming, outgoing = context.Pipe(duplex=False)
                        process = context.Process(target=process_writer, args=(str(database), outgoing))
                        process.start()
                        outgoing.close()
                        try:
                            assert incoming.poll(15), "Synthetic writer did not finish"
                            success, result = incoming.recv()
                            assert success, "Synthetic process writer/checkpoint failed"
                            observed["checkpoint"] = result
                            observed["committed"] = True
                            process.join(10)
                            assert process.exitcode == 0
                        finally:
                            if process.is_alive():
                                process.terminate()
                                process.join(10)
                            incoming.close()
                            process.close()
                    else:
                        thread = threading.Thread(target=writer)
                        thread.start()
                        thread.join(10)
                        assert not thread.is_alive(), "Synthetic writer did not finish"
                    assert not observed["errors"], "Synthetic writer/checkpoint failed"
                    if cancellation is not None:
                        cancellation.request()
                if progress is not None:
                    progress(status, remaining, total)
            return super().backup(target, pages=pages, progress=between, name=name, sleep=sleep)

    def connect(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith(database.resolve().as_uri() + "?mode=ro"):
            observed["uris"].append(path)
            kwargs["factory"] = SourceConnection
        return real_connect(path, *args, **kwargs)

    monkeypatch.setattr(snapshot_module.sqlite3, "connect", connect)
    return observed


@pytest.mark.parametrize("mode", ["thread", "process"])
def test_paged_snapshot_with_wal_commit_and_checkpoint_is_consistent(tmp_path, monkeypatch, mode):
    paths, _, _, scalars, composites = seed_vault(tmp_path / "source")
    observed = writer_between_pages(monkeypatch, paths.vault_database_path, mode=mode)
    destination = tmp_path / "snapshot.db"
    snapshot_module.create_sqlite_snapshot(paths.vault_database_path, destination)
    assert observed["committed"] and observed["steps"] > 1
    assert observed["checkpoint"][0] == 0
    assert all("immutable" not in uri for uri in observed["uris"])
    assert mismatch_count(paths.vault_database_path) == 0
    assert mismatch_count(destination) == 0, "Paged snapshot mixed committed occurrence counts"
    attest_records(destination, scalars, composites)


def test_create_validate_and_staged_restore_after_concurrent_checkpoint(tmp_path, monkeypatch):
    paths, hmac, aes, scalars, composites = seed_vault(tmp_path / "source")
    observed = writer_between_pages(monkeypatch, paths.vault_database_path)
    archive = tmp_path / "synthetic.dmsbackup"
    create_backup(archive, PASSWORD, PASSWORD, paths=paths,
                  hmac_key_provider=hmac, vault_key_provider=aes)
    assert observed["committed"] and observed["steps"] > 1
    validation = validate_backup(archive, PASSWORD)
    assert validation.mapping_count == len(scalars)
    verified = []
    verify = restorer._verify_restored_environment

    def staged_attestation(staged, *args):
        verified.append(staged.directory.name)
        return verify(staged, *args)

    monkeypatch.setattr(restorer, "_verify_restored_environment", staged_attestation)
    target = environment_paths(tmp_path / "restored")
    restore_backup(archive, PASSWORD, paths=target, protector=FakeProtector(b"NEW"))
    assert verified[0] == "B"
    assert mismatch_count(target.vault_database_path) == 0
    attest_records(target.vault_database_path, scalars, composites)
    with extracted_backup(archive, PASSWORD) as extracted:
        assert mismatch_count(extracted.vault_snapshot_path) == 0


@pytest.mark.parametrize("table", ["vault_mappings", "composite_mappings"])
def test_creation_rejects_structurally_valid_inconsistent_counters(tmp_path, table):
    paths, hmac, aes, _, _ = seed_vault(tmp_path / "source")
    with closing(sqlite3.connect(paths.vault_database_path)) as connection:
        connection.execute(f"UPDATE {table} SET total_occurrences = total_occurrences + 1")
        connection.commit()
    archive = tmp_path / "invalid.dmsbackup"
    with pytest.raises(BackupError):
        create_backup(archive, PASSWORD, PASSWORD, paths=paths,
                      hmac_key_provider=hmac, vault_key_provider=aes)
    assert not archive.exists()
    assert not list(tmp_path.glob(".dms-backup-*"))
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("table", ["vault_mappings", "composite_mappings"])
def test_validation_rejects_authenticated_legacy_torn_backup(tmp_path, monkeypatch, table):
    paths, hmac, aes, scalar_codes, _ = seed_vault(tmp_path / "source")
    with closing(sqlite3.connect(paths.vault_database_path)) as connection:
        connection.execute(f"UPDATE {table} SET total_occurrences = total_occurrences + 1")
        connection.commit()
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    archive = tmp_path / "old-logically-inconsistent.dmsbackup"
    # Construct the previously accepted archive with genuine format/authentication;
    # bypass only the new creator's counter check, never the validator under test.
    with monkeypatch.context() as patch:
        patch.setattr(creator, "inspect_snapshot", lambda _path: (4, len(scalar_codes)))
        create_backup(archive, PASSWORD, PASSWORD, paths=paths,
                      hmac_key_provider=hmac, vault_key_provider=aes)
    before = archive.read_bytes()
    with pytest.raises(BackupValidationError) as error:
        validate_backup(archive, PASSWORD)
    assert str(error.value) == VALIDATION_FAILURE_MESSAGE
    assert archive.read_bytes() == before
    target, _, _, protector = prepare_environment(tmp_path / "target")
    previous = {path.name: hashlib.sha256(path.read_bytes()).digest() for path in (
        target.hmac_key_path, target.vault_key_path, target.vault_database_path, target.profiles_path,
    )}
    with monkeypatch.context() as patch:
        patch.setattr(restorer, "_verify_restored_environment",
                      lambda *a: pytest.fail("Invalid snapshot must be rejected before staged restore"))
        with pytest.raises(BackupError):
            restore_backup(archive, PASSWORD, paths=target, protector=protector)
    assert previous == {path.name: hashlib.sha256(path.read_bytes()).digest() for path in (
        target.hmac_key_path, target.vault_key_path, target.vault_database_path, target.profiles_path,
    )}
    assert not list(target.directory.glob(".dms-restore-operation-*"))


def test_static_snapshot_is_healthy_and_does_not_modify_source(tmp_path):
    paths, _, _, scalars, composites = seed_vault(tmp_path / "source")
    original = hashlib.sha256(paths.vault_database_path.read_bytes()).digest()
    destination = tmp_path / "static.db"
    snapshot_module.create_sqlite_snapshot(paths.vault_database_path, destination)
    assert hashlib.sha256(paths.vault_database_path.read_bytes()).digest() == original
    assert snapshot_module.inspect_snapshot(destination) == (4, len(scalars))
    assert mismatch_count(destination) == 0
    attest_records(destination, scalars, composites)


def test_snapshot_includes_committed_changes_still_in_wal(tmp_path):
    paths, _, _, scalars, composites = seed_vault(tmp_path / "source")
    with closing(sqlite3.connect(paths.vault_database_path)) as writer:
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        writer.execute("BEGIN IMMEDIATE")
        for table, column in (("vault_mappings", "total_occurrences"),
                              ("vault_variations", "occurrence_count"),
                              ("composite_mappings", "total_occurrences"),
                              ("composite_variations", "occurrence_count")):
            writer.execute(f"UPDATE {table} SET {column} = 2")
        writer.commit()
        assert paths.vault_database_path.with_name("vault.db-wal").stat().st_size > 0
        destination = tmp_path / "wal.db"
        snapshot_module.create_sqlite_snapshot(paths.vault_database_path, destination)
    assert mismatch_count(destination) == 0
    with closing(sqlite3.connect(destination)) as connection:
        for table in ("vault_mappings", "composite_mappings"):
            assert connection.execute(f"SELECT DISTINCT total_occurrences FROM {table}").fetchall() == [(2,)]
    attest_records(destination, scalars, composites)


@pytest.mark.parametrize("stage", ["snapshot", "encryption"])
def test_cancellation_during_creation_cleans_up_and_keeps_source_healthy(tmp_path, monkeypatch, stage):
    paths, hmac, aes, scalars, composites = seed_vault(tmp_path / "source")
    cancellation = CancellationRequest()
    if stage == "snapshot":
        observed = writer_between_pages(monkeypatch, paths.vault_database_path, cancellation=cancellation)

    def progress(value):
        if stage == "encryption" and value.stage is BackupStage.ENCRYPTING:
            cancellation.request()

    archive = tmp_path / "cancelled.dmsbackup"
    with pytest.raises(BackupCancelled):
        create_backup(archive, PASSWORD, PASSWORD, paths=paths,
            hmac_key_provider=hmac, vault_key_provider=aes,
            cancellation=cancellation, progress_callback=progress)
    if stage == "snapshot":
        assert observed["committed"] and not observed["errors"]
    assert not archive.exists()
    assert not list(tmp_path.glob(".dms-backup-*")) and not list(tmp_path.glob("*.tmp"))
    assert mismatch_count(paths.vault_database_path) == 0
    attest_records(paths.vault_database_path, scalars, composites)


def test_legacy_schema_one_inspection_remains_supported(tmp_path):
    from test_vault_migration import create_v1_database, KEY
    path = tmp_path / "legacy.db"
    create_v1_database(path, VaultCipher(KEY))
    assert snapshot_module.inspect_snapshot(path) == (1, 1)


def test_validation_cancellation_removes_extracted_snapshot(tmp_path, monkeypatch):
    paths, hmac, aes, _, _ = seed_vault(tmp_path / "source")
    archive = tmp_path / "valid.dmsbackup"
    create_backup(archive, PASSWORD, PASSWORD, paths=paths,
                  hmac_key_provider=hmac, vault_key_provider=aes)
    original = hashlib.sha256(archive.read_bytes()).digest()
    temporary_root = tmp_path / "validation-temporaries"
    temporary_root.mkdir()
    directories = []
    temporary_directory = validator.tempfile.TemporaryDirectory

    def isolated_directory(*args, **kwargs):
        directory = temporary_directory(*args, **kwargs, dir=temporary_root)
        directories.append(Path(directory.name))
        return directory

    class CancelSecondChunk(CancellationRequest):
        checks = 0

        def raise_if_requested(self):
            self.checks += 1
            if self.checks == 2:
                self.request()
            super().raise_if_requested()

    monkeypatch.setattr(validator.tempfile, "TemporaryDirectory", isolated_directory)
    cancellation = CancelSecondChunk()
    with pytest.raises(BackupCancelled):
        validate_backup(archive, PASSWORD, cancellation=cancellation)
    assert cancellation.checks == 2 and directories
    assert all(not path.exists() for path in directories)
    assert hashlib.sha256(archive.read_bytes()).digest() == original


def test_snapshot_source_is_sqlite_enforced_read_only(tmp_path, monkeypatch):
    paths, _, _, _, _ = seed_vault(tmp_path / "source")
    original = hashlib.sha256(paths.vault_database_path.read_bytes()).digest()
    connect = sqlite3.connect
    attempts = []

    class ReadOnlyProbe(sqlite3.Connection):
        def backup(self, *args, **kwargs):
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                self.execute("PRAGMA user_version = 99")
            attempts.append(True)
            return super().backup(*args, **kwargs)

    def source_probe(path, *args, **kwargs):
        if isinstance(path, str) and path.startswith(paths.vault_database_path.resolve().as_uri() + "?mode=ro"):
            kwargs["factory"] = ReadOnlyProbe
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(snapshot_module.sqlite3, "connect", source_probe)
    snapshot_module.create_sqlite_snapshot(paths.vault_database_path, tmp_path / "read-only.db")
    assert attempts == [True]
    assert hashlib.sha256(paths.vault_database_path.read_bytes()).digest() == original

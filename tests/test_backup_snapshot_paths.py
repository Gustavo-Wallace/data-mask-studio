"""SQLite snapshot inspection must preserve literal filesystem path identity."""

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pytest

from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.backup import BackupError, create_backup, restore_backup, validate_backup
from data_mask_studio.backup import snapshot as snapshot_module, restorer
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing.composite_identity import generate_composite_token
from data_mask_studio.vault import MappingCandidate, VaultCipher, VaultRepository
from data_mask_studio.vault.composite_models import CompositeMappingCandidate
from test_backup import (
    environment_paths, prepare_environment, FakeProtector, PASSWORD, HMAC_KEY, VAULT_KEY,
)
from test_backup_snapshot_consistency import attest_records

PATH_CASES = (
    "ordinary", "hash#directory", "encoded%23directory", "literal%directory",
    "directory with spaces", "ação_日本_雪",
)


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))


def healthy_environment(root):
    paths, hmac, aes, _ = prepare_environment(root, with_mapping=False, with_profiles=False)
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    candidates = [MappingCandidate(
        TokenGenerator(HMAC_KEY).generate("SCALAR", value), "SCALAR", value, "Synthetic",
    ) for value in ("First synthetic value", "Second synthetic value")]
    with repository.transaction() as transaction:
        transaction.upsert_batch(candidates)
    values = ("Synthetic first component", "Synthetic second component")
    composite_code = generate_composite_token(HMAC_KEY, "PAIR", values)
    repository.composite_repository().upsert_composite_mapping(CompositeMappingCandidate(
        composite_code, "PAIR", values, values, (NormalizationRule.EXACT, NormalizationRule.EXACT),
    ))
    return paths, hmac, aes, tuple(candidate.code for candidate in candidates), (composite_code,)


@pytest.mark.parametrize("directory", PATH_CASES)
@pytest.mark.parametrize("relative", [False, True])
def test_snapshot_inspection_preserves_special_directory_paths(tmp_path, monkeypatch, directory, relative):
    paths, _, _, _, _ = healthy_environment(tmp_path / directory)
    snapshot = paths.directory.parent / "snapshot.db"
    snapshot_module.create_sqlite_snapshot(paths.vault_database_path, snapshot)
    original = snapshot.read_bytes()
    files_before = set(tmp_path.rglob("*"))
    if relative:
        monkeypatch.chdir(tmp_path)
        inspected = Path(directory) / snapshot.name
    else:
        inspected = snapshot
    assert snapshot_module.inspect_snapshot(inspected) == (4, 2)
    assert snapshot.read_bytes() == original
    # A normal read-only WAL connection may create empty SQLite sidecars.
    # These are not writes to the snapshot or evidence of an incorrect path.
    files_after = set(tmp_path.rglob("*"))
    assert files_before <= files_after
    assert files_after - files_before <= {
        Path(f"{snapshot}-wal"), Path(f"{snapshot}-shm"),
    }
    wal = Path(f"{snapshot}-wal")
    assert not wal.exists() or wal.stat().st_size == 0


@pytest.mark.parametrize("directory", PATH_CASES)
def test_real_backup_create_validate_and_staged_restore_in_special_paths(tmp_path, monkeypatch, directory):
    root = tmp_path / directory
    paths, hmac, aes, scalars, composites = healthy_environment(root / "source")
    archive = root / "synthetic.dmsbackup"
    scratch = root / "temporary extraction"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    original = paths.vault_database_path.read_bytes()
    create_backup(archive, PASSWORD, PASSWORD, paths=paths,
                  hmac_key_provider=hmac, vault_key_provider=aes)
    result = validate_backup(archive, PASSWORD)
    assert result.is_compatible and result.mapping_count == 2
    assert paths.vault_database_path.read_bytes() == original
    verified = []
    original_verify = restorer._verify_restored_environment

    def attest(staged, *args):
        verified.append(staged.directory.name)
        return original_verify(staged, *args)

    monkeypatch.setattr(restorer, "_verify_restored_environment", attest)
    target = environment_paths(root / "restored")
    restore_backup(archive, PASSWORD, paths=target, protector=FakeProtector(b"RESTORED"))
    assert verified[0] == "B"  # Real cryptographic attestation before publication.
    assert snapshot_module.inspect_snapshot(target.vault_database_path) == (4, 2)
    attest_records(target.vault_database_path, scalars, composites)
    assert not list(scratch.iterdir())
    assert not list(root.glob(".dms-backup-*"))


@pytest.mark.parametrize("directory", ["hash#extraction", "encoded%23extraction"])
def test_valid_backup_validation_uses_encoded_extraction_path(tmp_path, monkeypatch, directory):
    paths, hmac, aes, _, _ = healthy_environment(tmp_path / "ordinary-source")
    archive = tmp_path / "ordinary.dmsbackup"
    create_backup(archive, PASSWORD, PASSWORD, paths=paths,
                  hmac_key_provider=hmac, vault_key_provider=aes)
    scratch = tmp_path / directory
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    assert validate_backup(archive, PASSWORD).mapping_count == 2
    assert not list(scratch.iterdir())


def test_literal_percent_escape_does_not_inspect_a_different_existing_database(tmp_path):
    paths, _, _, _, _ = healthy_environment(tmp_path / "literal%23directory")
    decoy = tmp_path / "literal#directory" / "environment" / "vault.db"
    decoy.parent.mkdir(parents=True)
    with closing(sqlite3.connect(decoy)) as connection:
        connection.execute("PRAGMA user_version = 1")
        connection.execute("CREATE TABLE vault_mappings (code TEXT)")
        connection.execute("INSERT INTO vault_mappings VALUES ('SYNTHETIC-DECOY')")
        connection.commit()
    original = decoy.read_bytes()
    assert snapshot_module.inspect_snapshot(paths.vault_database_path) == (4, 2)
    assert decoy.read_bytes() == original


def test_special_filename_inspection_is_sqlite_enforced_read_only(tmp_path, monkeypatch):
    paths, _, _, _, _ = healthy_environment(tmp_path / "source")
    snapshot = tmp_path / "snapshot #%23 % espaço_日本.db"
    snapshot_module.create_sqlite_snapshot(paths.vault_database_path, snapshot)
    original = snapshot.read_bytes()
    connect = sqlite3.connect
    attempts = []

    def read_only_probe(database, *args, **kwargs):
        assert database == f"{snapshot.resolve().as_uri()}?mode=ro"
        assert kwargs["uri"] is True
        assert "immutable" not in database
        connection = connect(database, *args, **kwargs)
        try:
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute("PRAGMA user_version = 99")
        except BaseException:
            connection.close()
            raise
        attempts.append(True)
        return connection

    monkeypatch.setattr(snapshot_module.sqlite3, "connect", read_only_probe)
    assert snapshot_module.inspect_snapshot(snapshot) == (4, 2)
    assert attempts == [True]
    assert snapshot.read_bytes() == original


@pytest.mark.parametrize("directory", ["ordinary", "invalid#%23 %_日本"])
@pytest.mark.parametrize("contents", [None, b"", b"not a SQLite database"])
def test_missing_and_invalid_snapshots_fail_closed_without_creating_files(tmp_path, directory, contents):
    root = tmp_path / directory
    root.mkdir()
    snapshot = root / "snapshot.db"
    if contents is not None:
        snapshot.write_bytes(contents)
    files_before = set(tmp_path.rglob("*"))
    with pytest.raises(BackupError):
        snapshot_module.inspect_snapshot(snapshot)
    assert set(tmp_path.rglob("*")) == files_before
    if contents is not None:
        assert snapshot.read_bytes() == contents
    else:
        assert not snapshot.exists()

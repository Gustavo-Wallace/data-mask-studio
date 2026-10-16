"""Integrity diagnostics must not claim unrelated files as DMS temporaries."""

import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path

import pytest

from data_mask_studio.integrity import IntegrityStatus
from data_mask_studio.environment import EnvironmentError, RESTORE_JOURNAL
from data_mask_studio.environment_restore import RestoreOperation
from data_mask_studio.maintenance import (
    TemporaryItem, cleanup_temporaries, locate_temporaries,
)
from data_mask_studio.publication import Publication
from data_mask_studio.security import WindowsDPAPIProtector
from test_integrity import HMAC_KEY, auditor, check, database_hashes, prepare_valid_environment


TEMPORARY_CHECK = "Arquivos temporários abandonados"


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    paths, _, _ = prepare_valid_environment(tmp_path)
    assert auditor(paths).run().status is IntegrityStatus.INTACT
    return paths


@pytest.mark.parametrize("names", [
    ("other-app.tmp",),
    ("other-app.tmp", "report.tmp", "download.tmp"),
])
def test_unrelated_temporaries_do_not_change_healthy_integrity(environment, names):
    paths = environment
    sentinels = [paths.directory / name for name in names]
    old = time.time() - 7200
    for sentinel in sentinels:
        sentinel.write_bytes(b"unrelated synthetic sentinel")
        os.utime(sentinel, (old, old))

    report = auditor(paths).run()
    assert locate_temporaries(paths.directory) == []
    forged = [TemporaryItem(path, path.stat().st_size, 7200, False, False, selected=True)
              for path in sentinels]
    cleanup = cleanup_temporaries(forged, paths.directory)
    assert cleanup.removed == cleanup.failed == 0
    assert cleanup.preserved == len(sentinels)
    assert all(path.read_bytes() == b"unrelated synthetic sentinel" for path in sentinels)
    assert check(report, TEMPORARY_CHECK).status is IntegrityStatus.INTACT
    assert report.status is IntegrityStatus.INTACT


@pytest.mark.parametrize("prefix,suffix", [
    (".dms-write-", ""), (".dms-access-", ""), (".secret-", ".tmp"),
    (".profiles.json.", ".tmp"), (".saved.dmsbackup.", ".tmp"),
])
def test_real_tempfile_contract_requires_attention_and_only_owned_file_is_cleaned(
    environment, prefix, suffix,
):
    paths = environment
    descriptor, name = tempfile.mkstemp(dir=paths.directory, prefix=prefix, suffix=suffix)
    os.close(descriptor)
    owned = Path(name)
    sentinel = paths.directory / "other-app.tmp"
    sentinel.write_bytes(b"unrelated")
    old = time.time() - 7200
    for path in (owned, sentinel):
        os.utime(path, (old, old))
    before = database_hashes(paths)

    report = auditor(paths).run()
    result = check(report, TEMPORARY_CHECK)
    assert report.status is result.status is IntegrityStatus.ATTENTION
    assert result.examined == result.failures == 1
    assert result.identifiers == ("temporary#1",)
    assert owned.exists() and sentinel.read_bytes() == b"unrelated"
    assert database_hashes(paths) == before
    items = locate_temporaries(paths.directory)
    assert [item.path for item in items] == [owned]
    items[0].selected = True
    cleanup = cleanup_temporaries(items, paths.directory)
    assert cleanup.removed == 1 and cleanup.failed == 0
    assert not owned.exists() and sentinel.read_bytes() == b"unrelated"
    assert auditor(paths).run().status is IntegrityStatus.INTACT


@pytest.mark.parametrize("prefix", [".dms-backup-", ".dms-restore-"])
def test_owned_work_directory_is_still_reported_without_being_deleted(environment, prefix):
    paths = environment
    work = Path(tempfile.mkdtemp(dir=paths.directory, prefix=prefix))
    evidence = work / "synthetic-evidence"
    evidence.write_bytes(b"preserve")
    result = check(auditor(paths).run(), TEMPORARY_CHECK)
    assert result.status is IntegrityStatus.ATTENTION
    assert result.examined == result.failures == 1
    assert evidence.read_bytes() == b"preserve"


@pytest.mark.parametrize("name,is_directory", [
    (".secret-abandoned.tmp", False), (".dms-write-ab12_cd3.extra", False),
    (".report.csv.ab12_cd3.tmp", False), (".dms-backup-short", True),
    (".dms-restore-operation-not-a-uuid", True),
])
def test_ambiguous_names_do_not_establish_ownership(environment, name, is_directory):
    paths = environment
    path = paths.directory / name
    if is_directory:
        path.mkdir()
    else:
        path.write_bytes(b"unrelated")
    assert auditor(paths).run().status is IntegrityStatus.INTACT
    assert locate_temporaries(paths.directory) == []
    assert path.exists()


def test_interrupted_restore_work_is_reported_but_never_offered_for_cleanup(environment):
    paths = environment
    operation = RestoreOperation(paths.directory, WindowsDPAPIProtector())
    evidence = operation.work / "A" / "synthetic-evidence"
    evidence.write_bytes(b"preserve recovery")
    result = check(auditor(paths).run(), TEMPORARY_CHECK)
    assert result.status is IntegrityStatus.ATTENTION
    assert result.failures == 1
    assert locate_temporaries(paths.directory) == []
    item = TemporaryItem(operation.work, 1, 7200, False, False, selected=True)
    assert cleanup_temporaries([item], paths.directory).preserved == 1
    assert evidence.read_bytes() == b"preserve recovery"


def test_restore_journal_still_blocks_audit_without_touching_recovery(environment):
    paths = environment
    operation = RestoreOperation(paths.directory, WindowsDPAPIProtector())
    operation.record("STAGED")
    journal = paths.directory / RESTORE_JOURNAL
    before = journal.read_bytes()
    with pytest.raises(EnvironmentError, match="recuperação"):
        auditor(paths).run()
    assert journal.read_bytes() == before
    assert operation.work.is_dir()
    assert locate_temporaries(paths.directory) == []


@pytest.mark.parametrize("state", ["READY", "COMMITTED"])
def test_pending_publication_evidence_still_requires_attention_and_is_not_cleanable(
    environment, state,
):
    paths = environment
    source = paths.directory / "synthetic-source.csv"
    source.write_text("header\nsynthetic\n", encoding="utf-8")
    publication = Publication(paths.vault_database_path, source,
                              paths.directory / "synthetic-output.csv", HMAC_KEY, False)
    descriptor, name = tempfile.mkstemp(dir=paths.directory, prefix=publication.temp_prefix, suffix=".tmp")
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(b"synthetic masked output")
    staged = Path(name)
    publication.prepare(staged)
    if state == "COMMITTED":
        publication.committed()
    journal_before = publication.path.read_bytes()
    before = database_hashes(paths)
    result = check(auditor(paths).run(), TEMPORARY_CHECK)
    assert result.status is IntegrityStatus.ATTENTION
    assert result.examined == result.failures == 2
    assert database_hashes(paths) == before
    assert locate_temporaries(paths.directory, [publication.directory]) == []
    items = [TemporaryItem(path, 1, 7200, False, False, selected=True)
             for path in (publication.path, staged)]
    assert cleanup_temporaries(items, paths.directory, [publication.directory]).preserved == 2
    assert publication.path.read_bytes() == journal_before
    assert staged.read_bytes() == b"synthetic masked output"
    assert not Path(publication.data["destination"]).exists()


@pytest.mark.parametrize("prefix,suffix,subdirectory", [
    (".dms-package-", ".tmp", ""),
    (".dms-journal-", "", "publication-operations"),
])
def test_package_and_journal_staging_are_attention_only_evidence(
    environment, prefix, suffix, subdirectory,
):
    paths = environment
    root = paths.directory / subdirectory
    root.mkdir(exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=root, prefix=prefix, suffix=suffix)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(b"synthetic staged evidence")
    evidence = Path(name)
    result = check(auditor(paths).run(), TEMPORARY_CHECK)
    assert result.status is IntegrityStatus.ATTENTION
    assert result.failures == 1
    assert locate_temporaries(paths.directory, [root]) == []
    item = TemporaryItem(evidence, 1, 7200, False, False, selected=True)
    assert cleanup_temporaries([item], paths.directory, [root]).preserved == 1
    assert evidence.read_bytes() == b"synthetic staged evidence"


def test_publication_scan_is_limited_to_known_directory_and_names(environment):
    paths = environment
    publications = paths.directory / "publication-operations"
    publications.mkdir()
    sentinel = publications / "other-app.tmp"
    sentinel.write_bytes(b"unrelated")
    unrelated = paths.directory / "other-directory"
    unrelated.mkdir()
    journal = unrelated / (".dms-operation-" + "a" * 32 + ".json")
    journal.write_bytes(b"outside publication namespace")
    assert auditor(paths).run().status is IntegrityStatus.INTACT
    assert sentinel.read_bytes() == b"unrelated"
    assert journal.read_bytes() == b"outside publication namespace"


@pytest.mark.parametrize("damage", ["aes", "counter", "sqlite"])
def test_integrity_failures_remain_visible_with_unrelated_tmp(environment, damage):
    paths = environment
    sentinel = paths.directory / "other-app.tmp"
    sentinel.write_bytes(b"unrelated")
    if damage == "sqlite":
        paths.vault_database_path.write_bytes(b"not sqlite")
        expected = "Integridade do SQLite"
    else:
        with closing(sqlite3.connect(paths.vault_database_path)) as connection:
            if damage == "aes":
                connection.execute("UPDATE vault_mappings SET canonical_encrypted_value = zeroblob(32)")
                expected = "Autenticação AES-GCM"
            else:
                connection.execute("UPDATE vault_mappings SET total_occurrences = total_occurrences + 1")
                expected = "Contadores de ocorrências"
            connection.commit()
    report = auditor(paths).run()
    assert report.status is IntegrityStatus.FAILURE
    assert check(report, expected).status is IntegrityStatus.FAILURE
    assert check(report, TEMPORARY_CHECK).status is IntegrityStatus.INTACT
    assert sentinel.read_bytes() == b"unrelated"


@pytest.mark.skipif(os.name != "nt", reason="Windows filesystem regression")
def test_windows_special_directory_and_case_insensitive_tmp_extension(tmp_path):
    root = tmp_path / "Dados de teste # %23 São Paulo"
    root.mkdir()
    paths, _, _ = prepare_valid_environment(root)
    sentinel = paths.directory / "OTHER-APP.TMP"
    sentinel.write_bytes(b"unrelated")
    assert auditor(paths).run().status is IntegrityStatus.INTACT
    assert locate_temporaries(paths.directory) == []
    assert sentinel.read_bytes() == b"unrelated"


def test_successful_compaction_does_not_mistake_its_active_snapshot_for_abandonment(
    tmp_path, monkeypatch,
):
    from data_mask_studio.maintenance import VaultCompactor
    from test_maintenance import FixedKeyProvider, HMAC_KEY, VAULT_KEY, prepare_environment

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    paths, _, _ = prepare_environment(tmp_path)
    result = VaultCompactor(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY)).compact()
    assert result.audit.status is IntegrityStatus.INTACT
    assert check(result.audit, TEMPORARY_CHECK).status is IntegrityStatus.INTACT
    assert not list(paths.directory.glob("dms-vault-compaction-*"))

import json
import multiprocessing
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from data_mask_studio.environment import environment_lease, EnvironmentError, RESTORE_JOURNAL, GENERATION_FILE
from data_mask_studio.environment_restore import recover_restore, RestoreOperation
from data_mask_studio.backup import restore_backup, BackupError
from data_mask_studio.security import LocalKeyProvider
from data_mask_studio.vault import VaultRepository, VaultCipher
from test_backup import create_test_backup, prepare_environment, FakeProtector, PASSWORD, VAULT_KEY


def state(paths):
    return {p.name: p.read_bytes() for p in (paths.hmac_key_path, paths.vault_key_path,
            paths.vault_database_path, paths.profiles_path) if p.exists()}


def _hold(directory, exclusive, pipe):
    with environment_lease(Path(directory), exclusive=exclusive):
        pipe.send("locked")
        if pipe.poll(30): pipe.recv()


@pytest.fixture
def holder():
    processes = []
    def start(directory, exclusive):
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(target=_hold, args=(str(directory), exclusive, child))
        process.start()
        processes.append((process, parent))
        assert parent.poll(20) and parent.recv() == "locked"
        return process, parent
    yield start
    for process, pipe in processes:
        if process.is_alive():
            pipe.send("release")
            process.join(10)
        if process.is_alive():
            process.terminate()
            process.join()
        pipe.close()


@pytest.mark.parametrize("exclusive", [False, True])
def test_restore_rejected_before_mutation_cross_process(tmp_path, holder, exclusive):
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target")
    before = state(paths)
    holder(paths.directory, exclusive)
    with pytest.raises(EnvironmentError):
        restore_backup(backup, PASSWORD, paths=paths, protector=protector)
    assert state(paths) == before
    assert not (paths.directory / RESTORE_JOURNAL).exists()


def test_exclusive_blocks_normal_operations_and_shared_readers_coexist(tmp_path, holder):
    paths, hmac, _, _ = prepare_environment(tmp_path)
    repository = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    process, pipe = holder(paths.directory, False)
    with environment_lease(paths.directory):
        assert repository.count() == 1
    pipe.send("release")
    process.join(10)
    holder(paths.directory, True)
    with pytest.raises(EnvironmentError): hmac.get_key()
    with pytest.raises(EnvironmentError):
        with repository.transaction(): pass
    with pytest.raises(EnvironmentError): repository.count()
    with environment_lease(tmp_path / "different", exclusive=True): pass


def test_process_death_releases_lock_without_deleting_lockfile(tmp_path, holder):
    process, _ = holder(tmp_path, True)
    process.terminate()
    process.join(10)
    assert (tmp_path / ".dms-environment.lock").exists()
    with environment_lease(tmp_path, exclusive=True): pass


def test_stale_repository_rejected_after_restore(tmp_path):
    from data_mask_studio.profiles import ProfileRepository
    backup, source_paths = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target")
    old = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    restore_backup(backup, PASSWORD, paths=paths, protector=protector)
    with pytest.raises(EnvironmentError): old.count()
    with pytest.raises(EnvironmentError): old.as_read_only()
    assert VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY)).count() == 1
    assert paths.profiles_path.read_bytes() == source_paths.profiles_path.read_bytes()
    assert ProfileRepository(paths.profiles_path).load()[0].format_version == 2


CRASH_PROGRAM = '''
import sys, os
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / 'tests'))
from test_backup import FakeProtector, PASSWORD, environment_paths
from data_mask_studio.backup import restore_backup
from data_mask_studio.backup import restorer
from data_mask_studio.environment_restore import RestoreOperation
backup, target, stage = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
record = RestoreOperation.record
replace = restorer._replace_file
count = 0
def crash_record(self, state):
    record(self, state)
    if state == stage: os._exit(73)
def crash_replace(src, dst):
    global count
    replace(src, dst)
    count += 1
    if stage == 'swap' + str(count): os._exit(73)
RestoreOperation.record = crash_record
restorer._replace_file = crash_replace
restore_backup(backup, PASSWORD, paths=environment_paths(target), protector=FakeProtector(b'OLD'))
'''


@pytest.mark.parametrize("stage", ["STAGED", "SWAPPING", "swap1", "swap2", "swap3", "COMMITTED", "CLEANUP"])
def test_real_crash_recovery_is_generation_atomic_and_idempotent(tmp_path, stage):
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target", with_mapping=False)
    before = state(paths)
    result = subprocess.run([sys.executable, "-c", CRASH_PROGRAM, str(backup), str(tmp_path / "target"), stage],
                            capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    with pytest.raises(EnvironmentError): LocalKeyProvider(paths.directory, protector).get_key()
    assert recover_restore(paths.directory, protector)
    if stage not in {"COMMITTED", "CLEANUP"}:
        assert state(paths) == before
    else:
        assert VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY)).count() == 1
        assert (paths.directory / GENERATION_FILE).exists()
    recovered = state(paths)
    assert not recover_restore(paths.directory, protector)
    assert state(paths) == recovered
    assert not (paths.directory / RESTORE_JOURNAL).exists()
    assert not list(paths.directory.glob(".dms-restore-operation-*"))


@pytest.mark.parametrize("problem", ["password", "corrupt", "staged_key", "staged_vault", "staged_profiles"])
def test_invalid_restore_never_mutates_active_generation(tmp_path, monkeypatch, problem):
    from data_mask_studio.backup import restorer
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target")
    before = state(paths)
    if problem == "corrupt": backup.write_bytes(b"invalid")
    if problem.startswith("staged_"):
        verify = restorer._verify_restored_environment
        def invalid(staged, *args):
            name = {"staged_key": "secret.key", "staged_vault": "vault.db", "staged_profiles": "profiles.json"}[problem]
            (staged.directory / name).write_bytes(b"invalid")
            return verify(staged, *args)
        monkeypatch.setattr(restorer, "_verify_restored_environment", invalid)
    with pytest.raises(BackupError):
        restore_backup(backup, "wrong" if problem == "password" else PASSWORD, paths=paths, protector=protector)
    assert state(paths) == before
    assert not (paths.directory / RESTORE_JOURNAL).exists()


@pytest.mark.parametrize("problem", ["version", "id", "paths", "state", "corrupt"])
def test_bad_restore_journal_fails_closed(tmp_path, problem):
    paths, _, _, protector = prepare_environment(tmp_path)
    operation = RestoreOperation(paths.directory, protector)
    operation.prepare()
    before = state(paths)
    data = dict(operation.data)
    if problem == "version": data["restore_journal_version"] = 999
    if problem == "id": data["id"] = "../../outside"
    if problem == "state": data["state"] = "future"
    if problem == "paths": data["generations"]["A"]["../../outside"] = None
    journal = paths.directory / RESTORE_JOURNAL
    journal.write_bytes(b"bad" if problem == "corrupt" else protector.protect(json.dumps(data).encode()))
    with pytest.raises(EnvironmentError): recover_restore(paths.directory, protector)
    assert state(paths) == before and journal.exists()


def test_cleanup_failure_is_only_cleanup_on_next_startup(tmp_path, monkeypatch):
    import data_mask_studio.environment_restore as module
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target", with_mapping=False)
    with monkeypatch.context() as patch:
        patch.setattr(module, "cleanup", lambda *args: (_ for _ in ()).throw(OSError("cleanup")))
        with pytest.raises(BackupError): restore_backup(backup, PASSWORD, paths=paths, protector=protector)
    before = state(paths)
    assert recover_restore(paths.directory, protector)
    assert state(paths) == before
    assert VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY)).count() == 1


def test_restore_can_repair_missing_keys_but_pending_f05_blocks_it(tmp_path):
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target")
    paths.hmac_key_path.unlink()
    paths.vault_key_path.unlink()
    restore_backup(backup, PASSWORD, paths=paths, protector=protector)
    assert paths.hmac_key_path.exists() and paths.vault_key_path.exists()
    before = state(paths)
    pending = paths.directory / "publication-operations"
    pending.mkdir()
    (pending / ".dms-operation-invalid.json").write_bytes(b"invalid")
    with pytest.raises(BackupError): restore_backup(backup, PASSWORD, paths=paths, protector=protector)
    assert state(paths) == before
    assert (pending / ".dms-operation-invalid.json").exists()


@pytest.mark.parametrize("committed", [False, True])
def test_f05_is_resolved_with_old_key_before_generation_switch(tmp_path, committed):
    from data_mask_studio.publication import Publication
    from test_backup import HMAC_KEY
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target")
    source = tmp_path / "input.csv"
    source.write_bytes(b"original")
    destination = tmp_path / "out.csv"
    operation = Publication(paths.vault_database_path, source, destination, HMAC_KEY, False)
    with tempfile.NamedTemporaryFile(dir=tmp_path, prefix=operation.temp_prefix, suffix=".tmp", delete=False) as stream:
        stream.write(b"masked output")
        temp = Path(stream.name)
    operation.prepare(temp)
    before = state(paths)
    if committed:
        operation.committed()
        restore_backup(backup, PASSWORD, paths=paths, protector=protector)
        assert destination.read_bytes() == b"masked output"
        assert not operation.path.exists()
    else:
        with pytest.raises(BackupError): restore_backup(backup, PASSWORD, paths=paths, protector=protector)
        assert not destination.exists() and operation.path.exists()
        assert state(paths) == before


def _paused_restore(backup, paths, pipe):
    from data_mask_studio.backup import restorer
    replace = restorer._replace_file
    first = True
    def pause(src, dst):
        nonlocal first
        if first:
            first = False
            pipe.send("swapping")
            if not pipe.poll(30): raise RuntimeError("test timeout")
            pipe.recv()
        return replace(src, dst)
    restorer._replace_file = pause
    restore_backup(backup, PASSWORD, paths=paths, protector=FakeProtector(b"OLD"))


def test_real_restore_blocks_second_restore_and_processing(tmp_path):
    from data_mask_studio.csv_tools import anonymize_csv
    from data_mask_studio.anonymization import ColumnConfig
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target")
    repo = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    source = tmp_path / "input.csv"
    source.write_text("Name\nAlice\n")
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_paused_restore, args=(backup, paths, child))
    process.start()
    try:
        assert parent.poll(20) and parent.recv() == "swapping"
        before = state(paths)
        with pytest.raises(EnvironmentError): restore_backup(backup, PASSWORD, paths=paths, protector=protector)
        with pytest.raises(EnvironmentError):
            anonymize_csv(source, tmp_path / "out.csv", encoding="utf-8", delimiter=",",
                configurations=[ColumnConfig("Name", True, "NAME")], secret_key=b"H" * 32, vault_repository=repo)
        assert not (tmp_path / "out.csv").exists() and state(paths) == before
        parent.send("continue")
        process.join(20)
        assert process.exitcode == 0
    finally:
        if process.is_alive(): process.terminate(); process.join()
        parent.close()


def test_journal_and_staging_never_persist_raw_keys_with_real_dpapi(tmp_path, monkeypatch):
    from data_mask_studio.security import WindowsDPAPIProtector
    from test_backup import HMAC_KEY
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, _ = prepare_environment(tmp_path / "target")
    real_record = RestoreOperation.record
    class Crash(BaseException): pass
    def stop(self, state):
        real_record(self, state)
        if state == "STAGED": raise Crash()
    monkeypatch.setattr(RestoreOperation, "record", stop)
    protector = WindowsDPAPIProtector()
    with pytest.raises(Crash): restore_backup(backup, PASSWORD, paths=paths, protector=protector)
    journal = (paths.directory / RESTORE_JOURNAL).read_bytes()
    assert HMAC_KEY not in journal and VAULT_KEY not in journal
    work = next(paths.directory.glob(".dms-restore-operation-*"))
    for name, key in (("secret.key", HMAC_KEY), ("vault_key.dpapi", VAULT_KEY)):
        blob = (work / "B" / name).read_bytes()
        assert key not in blob and protector.unprotect(blob) == key
    assert recover_restore(paths.directory, protector)


def test_precommit_recovery_preserves_wal_generation(tmp_path):
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target")
    program = '''
import sqlite3, sys, os
connection = sqlite3.connect(sys.argv[1])
connection.execute('PRAGMA journal_mode=WAL')
connection.execute('UPDATE vault_mappings SET total_occurrences=11')
connection.execute('UPDATE vault_variations SET occurrence_count=11')
connection.commit()
os._exit(0)
'''
    subprocess.run([sys.executable, "-c", program, str(paths.vault_database_path)], check=True, timeout=20)
    wal = Path(str(paths.vault_database_path) + "-wal")
    assert wal.exists() and wal.stat().st_size > 0
    keys = (paths.hmac_key_path.read_bytes(), paths.vault_key_path.read_bytes())
    result = subprocess.run([sys.executable, "-c", CRASH_PROGRAM, str(backup), str(tmp_path / "target"), "swap2"],
                            capture_output=True, timeout=30)
    assert result.returncode == 73, result.stderr.decode(errors="replace")
    recover_restore(paths.directory, protector)
    repo = VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY))
    mapping = repo.get_decrypted_mapping("NOME-ABCDEFGHI234")
    assert mapping.occurrence_count == mapping.variations[0].occurrence_count == 11
    assert keys == (paths.hmac_key_path.read_bytes(), paths.vault_key_path.read_bytes())


def test_cancel_after_staging_cleans_without_active_mutation(tmp_path, monkeypatch):
    from data_mask_studio.backup import CancellationRequest, BackupCancelled
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target")
    before = state(paths)
    cancellation = CancellationRequest()
    original = RestoreOperation.prepare
    def cancel(self):
        original(self)
        cancellation.request()
    monkeypatch.setattr(RestoreOperation, "prepare", cancel)
    with pytest.raises(BackupCancelled):
        restore_backup(backup, PASSWORD, paths=paths, protector=protector, cancellation=cancellation)
    assert state(paths) == before
    assert not (paths.directory / RESTORE_JOURNAL).exists()
    assert not list(paths.directory.glob(".dms-restore-operation-*"))


def test_corrupt_recovery_copy_is_not_installed(tmp_path):
    backup, _ = create_test_backup(tmp_path / "source")
    paths, _, _, protector = prepare_environment(tmp_path / "target")
    result = subprocess.run([sys.executable, "-c", CRASH_PROGRAM, str(backup), str(tmp_path / "target"), "STAGED"],
                            capture_output=True, timeout=30)
    assert result.returncode == 73
    before = state(paths)
    work = next(paths.directory.glob(".dms-restore-operation-*"))
    (work / "A" / "secret.key").write_bytes(b"invalid")
    with pytest.raises(EnvironmentError): recover_restore(paths.directory, protector)
    assert state(paths) == before and (paths.directory / RESTORE_JOURNAL).exists()

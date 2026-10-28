"""A failed rollback must not destroy the known-good encrypted safety copy."""

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle

from data_mask_studio.backup.snapshot import inspect_snapshot
from data_mask_studio.app import create_application
from data_mask_studio.environment import environment_lease, generation
from data_mask_studio.integrity import IntegrityStatus
from data_mask_studio.gui.maintenance_widget import MaintenanceWidget
from data_mask_studio.maintenance import (
    MaintenanceCancelled, MaintenanceError, TemporaryItem, VaultCompactor,
    cleanup_temporaries, locate_temporaries,
)
import data_mask_studio.maintenance.vault_compactor as compactor_module
from data_mask_studio.vault import VaultCipher, VaultRepository
from test_maintenance import (
    prepare_environment, FixedKeyProvider, HMAC_KEY, VAULT_KEY, SECRET_VALUE, CANONICAL_VALUE,
)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    paths, repository, code = prepare_environment(tmp_path, bloat=True)
    expected = repository.get_decrypted_mapping(code)
    sentinel = paths.directory / "other-app.tmp"
    sentinel.write_bytes(b"unrelated synthetic file")
    created = []
    mkdtemp = compactor_module.tempfile.mkdtemp

    def record_directory(*args, **kwargs):
        value = mkdtemp(*args, **kwargs)
        if kwargs.get("prefix") == "dms-vault-compaction-":
            created.append(Path(value))
        return value

    monkeypatch.setattr(compactor_module.tempfile, "mkdtemp", record_directory)
    compactor = VaultCompactor(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY))
    return paths, code, expected, compactor, created, sentinel


def fail_after_real_replacement(monkeypatch, original_error, *, fail_rollback):
    copy_database = compactor_module._copy_database
    observed = {"replacement": False, "rollback": False, "snapshot_valid": False}
    rollback_error = PermissionError("SYNTHETIC_PRIVATE_ROLLBACK_DETAIL")

    def copy(source, destination, *, compact_destination=False):
        if source.name == "vault-snapshot.db":
            observed["rollback"] = True
            observed["snapshot_valid"] = inspect_snapshot(source) == (4, 2)
            if fail_rollback:
                raise rollback_error  # Controlled filesystem fault in the actual rollback helper.
        result = copy_database(source, destination, compact_destination=compact_destination)
        if compact_destination:
            observed["replacement"] = True
        return result

    def damage_and_fail(database):
        assert observed["replacement"]
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE vault_mappings SET total_occurrences = total_occurrences + 1")
            connection.commit()
            # Make the controlled damage durable in the DB, not only in a WAL
            # that the existing rollback helper removes before attempting copy.
            assert connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        raise original_error

    monkeypatch.setattr(compactor_module, "_copy_database", copy)
    monkeypatch.setattr(compactor_module, "_optimize", damage_and_fail)
    return observed, rollback_error


@pytest.mark.parametrize("error_type", [MaintenanceError, OSError])
def test_compaction_and_rollback_failure_retains_valid_snapshot_after_exit(environment, monkeypatch, error_type):
    paths, code, expected, compactor, created, sentinel = environment
    previous_generation = generation(paths.directory)
    original_error = error_type("SYNTHETIC_PRIVATE_COMPACTION_DETAIL")
    observed, rollback_error = fail_after_real_replacement(monkeypatch, original_error, fail_rollback=True)
    with pytest.raises(Exception) as caught:
        compactor.compact()
    assert observed == {"replacement": True, "rollback": True, "snapshot_valid": True}
    assert len(created) == 1
    root = created[0]
    snapshot = root / "vault-snapshot.db"
    assert snapshot.is_file(), "Compound failure deleted the valid safety snapshot"
    assert root.parent == paths.directory.resolve()
    assert inspect_snapshot(snapshot) == (4, 2)
    recovered = VaultRepository(snapshot, VaultCipher(VAULT_KEY), read_only=True)
    assert recovered.get_decrypted_mapping(code) == expected
    assert compactor_module._logical_digest(paths.vault_database_path) != compactor_module._logical_digest(snapshot)
    assert (root / "vault-compacted.db").is_file()
    assert isinstance(caught.value, MaintenanceError)
    assert caught.value.__cause__ is original_error
    assert caught.value.__context__ is rollback_error
    message = str(caught.value)
    assert "restauração" in message and "falhou" in message
    assert root.name in message and snapshot.name in message
    for sensitive in (str(paths.directory), str(paths.vault_database_path), SECRET_VALUE,
                      CANONICAL_VALUE, code, "SYNTHETIC_PRIVATE_COMPACTION_DETAIL",
                      "SYNTHETIC_PRIVATE_ROLLBACK_DETAIL"):
        assert sensitive not in message
    for data in (SECRET_VALUE.encode(), CANONICAL_VALUE.encode(), HMAC_KEY, VAULT_KEY):
        assert data not in snapshot.read_bytes()
    assert not (root / "secret.key").exists() and not (root / "vault_key.dpapi").exists()
    assert generation(paths.directory) == previous_generation
    assert sentinel.read_bytes() == b"unrelated synthetic file"
    # Failure releases the existing exclusive lease; retained evidence is not a lock.
    with environment_lease(paths.directory, exclusive=True):
        pass

    old = time.time() - 7200
    os.utime(root, (old, old))
    os.utime(snapshot, (old, old))
    roots = [root.parent, root]
    assert not any(item.path in (root, snapshot) for item in locate_temporaries(paths.directory, roots))
    # Revalidation during removal must also reject manually supplied candidates.
    candidates = [TemporaryItem(path, 1, 7200, False, False, selected=True)
                  for path in (root, snapshot)]
    result = cleanup_temporaries(candidates, paths.directory, roots)
    assert result.removed == result.failed == 0 and result.preserved == 2
    assert snapshot.is_file() and inspect_snapshot(snapshot) == (4, 2)
    assert recovered.get_decrypted_mapping(code) == expected


@pytest.mark.parametrize("outcome", ["success", "rollback_maintenance_error", "rollback_os_error"])
def test_success_and_successful_rollback_clean_only_owned_work_directory(environment, monkeypatch, outcome):
    paths, code, expected, compactor, created, sentinel = environment
    original_generation = generation(paths.directory)
    profiles_before = paths.profiles_path.read_bytes()
    if outcome == "success":
        result = compactor.compact()
        assert result.audit.status is not IntegrityStatus.FAILURE
        assert result.size_after < result.size_before
        assert result.recovered_bytes == result.size_before - result.size_after
    else:
        error_type = MaintenanceError if outcome == "rollback_maintenance_error" else OSError
        original_error = error_type("SYNTHETIC_COMPACTION_FAILURE")
        observed, _ = fail_after_real_replacement(monkeypatch, original_error, fail_rollback=False)
        with pytest.raises(MaintenanceError) as caught:
            compactor.compact()
        assert observed == {"replacement": True, "rollback": True, "snapshot_valid": True}
        if error_type is MaintenanceError:
            assert caught.value is original_error
        else:
            assert caught.value.__cause__ is original_error
    assert len(created) == 1 and created[0].parent == paths.directory.resolve()
    assert not created[0].exists()
    assert inspect_snapshot(paths.vault_database_path) == (4, 2)
    assert VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY)).get_decrypted_mapping(code) == expected
    assert generation(paths.directory) == original_generation
    assert paths.profiles_path.read_bytes() == profiles_before
    assert sentinel.read_bytes() == b"unrelated synthetic file"
    with environment_lease(paths.directory, exclusive=True):
        pass


@pytest.mark.parametrize("phase", ["initial", "after_snapshot", "after_vacuum"])
def test_cancel_before_replacement_cleans_snapshot_and_preserves_environment(environment, monkeypatch, phase):
    paths, code, expected, compactor, created, sentinel = environment
    cancel = phase == "initial"
    original_generation = generation(paths.directory)
    copies = []
    original_copy = compactor_module._copy_database

    def record_copy(*args, **kwargs):
        copies.append(True)
        return original_copy(*args, **kwargs)

    monkeypatch.setattr(compactor_module, "_copy_database", record_copy)
    if phase == "after_snapshot":
        original_snapshot = compactor_module.create_sqlite_snapshot

        def cancel_after_snapshot(*args, **kwargs):
            nonlocal cancel
            original_snapshot(*args, **kwargs)
            cancel = True

        monkeypatch.setattr(compactor_module, "create_sqlite_snapshot", cancel_after_snapshot)
    elif phase == "after_vacuum":
        original_vacuum = compactor_module._vacuum_into

        def cancel_after_vacuum(*args, **kwargs):
            nonlocal cancel
            original_vacuum(*args, **kwargs)
            cancel = True

        monkeypatch.setattr(compactor_module, "_vacuum_into", cancel_after_vacuum)
    with pytest.raises(MaintenanceCancelled):
        compactor.compact(should_cancel=lambda: cancel)
    assert not copies
    assert len(created) == 1 and not created[0].exists()
    assert inspect_snapshot(paths.vault_database_path) == (4, 2)
    assert VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY)).get_decrypted_mapping(code) == expected
    assert generation(paths.directory) == original_generation
    assert sentinel.read_bytes() == b"unrelated synthetic file"
    with environment_lease(paths.directory, exclusive=True):
        pass


def test_failure_before_replacement_cleans_work_and_does_not_attempt_rollback(environment, monkeypatch):
    paths, code, expected, compactor, created, sentinel = environment
    original_error = OSError("Synthetic failure before replacement")

    def fail_vacuum(*args, **kwargs):
        raise original_error

    def forbidden_rollback(*args, **kwargs):
        pytest.fail("No replacement occurred, so rollback must not run")

    monkeypatch.setattr(compactor_module, "_vacuum_into", fail_vacuum)
    monkeypatch.setattr(compactor_module, "_restore_snapshot", forbidden_rollback)
    with pytest.raises(MaintenanceError) as caught:
        compactor.compact()
    assert caught.value.__cause__ is original_error
    assert len(created) == 1 and not created[0].exists()
    assert VaultRepository(paths.vault_database_path, VaultCipher(VAULT_KEY)).get_decrypted_mapping(code) == expected
    assert sentinel.read_bytes() == b"unrelated synthetic file"


def test_compound_failure_reaches_gui_as_safe_error_with_retained_snapshot(environment, monkeypatch):
    paths, _, _, _, created, _ = environment
    app = create_application([])
    widget = MaintenanceWidget(paths, FixedKeyProvider(HMAC_KEY), FixedKeyProvider(VAULT_KEY))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    original_error = OSError("SYNTHETIC_PRIVATE_COMPACTION_DETAIL")
    fail_after_real_replacement(monkeypatch, original_error, fail_rollback=True)
    changes = []
    widget.environment_changed.connect(lambda: changes.append(True))
    widget.start_compaction()
    worker = widget._worker
    assert worker is not None and worker.wait(5000)
    app.processEvents()
    assert widget._worker is None and not widget.has_running_worker()
    assert widget.status_label.property("feedbackState") == "error"
    assert "restauração" in widget.status_label.text() and "falhou" in widget.status_label.text()
    assert created[0].name in widget.status_label.text()
    assert "SYNTHETIC_PRIVATE" not in widget.status_label.text()
    assert str(paths.directory) not in widget.status_label.text()
    assert not changes  # No successful compaction/environment-change notification.
    assert not widget.compaction_result.toPlainText()
    assert inspect_snapshot(created[0] / "vault-snapshot.db") == (4, 2)

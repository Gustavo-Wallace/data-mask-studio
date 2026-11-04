"""Compaction explanations must not become compaction or recovery decisions."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import pickle
import sqlite3
from threading import Event, Thread

import pytest
from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.i18n import Translator
from data_mask_studio.maintenance import vault_compactor as compactor_module
from data_mask_studio.maintenance import (
    CompactionExplanation, CompactionPhase, CompactionReason, MaintenanceError, MaintenanceCancelled,
)
from data_mask_studio.gui.compaction_presentation import compaction_error_text
from data_mask_studio.gui.maintenance_worker import CompactionWorker
from data_mask_studio.integrity import CheckResult, IntegrityStatus
from data_mask_studio.backup.snapshot import inspect_snapshot
from data_mask_studio.vault import VaultRepository, VaultCipher
from test_compaction_recovery import fail_after_real_replacement
from test_maintenance_compaction_localization import page, isolated_appdata, accept_and_pause, result_for
from test_maintenance import VAULT_KEY
from test_maintenance_interface import wait


def test_actual_checkpoint_phase_retranslates_while_worker_is_running(page, monkeypatch):
    app, widget, _, _, _ = page
    entered, release = Event(), Event()
    checkpoint = compactor_module._checkpoint

    def paused(database):
        entered.set()
        assert release.wait(5)
        checkpoint(database)

    monkeypatch.setattr(compactor_module, "_checkpoint", paused)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    widget.start_compaction()
    worker = widget._worker
    try:
        assert entered.wait(5)
        app.processEvents()
        widget.retranslate(Translator("en"))
        assert widget.status_label.text() == "Running SQLite checkpoint"
        assert widget._worker is worker and worker.isRunning()
    finally:
        release.set()
        assert worker.wait(10000)
        app.processEvents()


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_real_post_replacement_failure_has_localized_recovery_meaning(page, monkeypatch, rollback_fails):
    app, widget, paths, _, _ = page
    fail_after_real_replacement(monkeypatch, OSError("Synthetic private detail"), fail_rollback=rollback_fails)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    widget.start_compaction()
    wait(app, widget)
    widget.retranslate(Translator("en"))
    text = widget.status_label.text()
    if rollback_fails:
        assert "Do not use the vault until manual review." in text
        assert "snapshot: vault-snapshot.db" in text
        assert next(paths.directory.glob("dms-vault-compaction-*")).name in text
    else:
        assert "The original vault was restored from the safety snapshot." in text
    assert "Synthetic private detail" not in text
    assert widget.progress.value() == 0 and widget.status_label.property("feedbackState") == "error"
    cached = widget._compaction_error
    assert cached[1].rollback_completed is (not rollback_fails)
    assert cached[1].reason is (CompactionReason.ROLLBACK_FAILED if rollback_fails else CompactionReason.COMPACTION_FAILED)
    translators = [Translator(locale) for locale in ("pt-BR", "en", "pt-BR", "en")]

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must not touch recovery evidence or rerun recovery")

    with monkeypatch.context() as guard:
        guard.setattr(compactor_module, "_restore_snapshot", forbidden)
        guard.setattr(compactor_module, "create_sqlite_snapshot", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        for name in ("stat", "open", "exists", "iterdir", "unlink", "resolve"):
            guard.setattr(Path, name, forbidden)
        for translator in translators:
            widget.retranslate(translator)
            app.processEvents()
            assert widget._compaction_error is cached
            assert widget.status_label.text() == compaction_error_text(
                MaintenanceError(cached[0], explanation=cached[1]), translator)
            assert widget.progress.value() == 0 and widget.status_label.property("feedbackState") == "error"


PHASES = [
    (CompactionPhase.CHECKPOINT, "Executando checkpoint do SQLite", "Running SQLite checkpoint", True),
    (CompactionPhase.SNAPSHOT, "Criando snapshot de segurança", "Creating safety snapshot", True),
    (CompactionPhase.VALIDATING, "Validando o cofre atual", "Validating the current vault", True),
    (CompactionPhase.COMPACTING, "Gerando banco compactado", "Creating compacted database", True),
    (CompactionPhase.REPLACING, "Substituindo o cofre com segurança", "Replacing the vault safely", False),
    (CompactionPhase.AUDITING, "Executando auditoria posterior", "Running post-compaction audit", False),
]


@pytest.mark.parametrize("phase,raw,english,allowed", PHASES, ids=[row[0].value for row in PHASES])
def test_all_real_phases_have_additive_metadata_and_no_io_retranslation(page, monkeypatch, phase, raw, english, allowed):
    app, widget, _, _, _ = page
    entered, release = Event(), Event()
    compact = widget._compactor.compact
    legacy = []
    calls = []

    def paused(**kwargs):
        calls.append(True)
        callback = kwargs["phase_callback"]

        def pause_at_phase(value, can_cancel):
            callback(value, can_cancel)
            if value is phase:
                entered.set()
                assert release.wait(5)

        kwargs["phase_callback"] = pause_at_phase
        return compact(**kwargs)

    monkeypatch.setattr(widget._compactor, "compact", paused)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    start = CompactionWorker.start

    def observe(worker):
        worker.phase_changed.connect(lambda text, permitted: legacy.append((text, permitted)), Qt.ConnectionType.DirectConnection)
        start(worker)

    monkeypatch.setattr(CompactionWorker, "start", observe)
    widget.start_compaction()
    worker = widget._worker
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    try:
        assert entered.wait(5)
        app.processEvents()
        assert widget.status_label.text() == raw
        widget.compaction_result.setFocus()
        focus = app.focusWidget()

        def forbidden(*args, **kwargs):
            pytest.fail("Retranslation must not perform compaction, database or filesystem operations")

        with monkeypatch.context() as guard:
            guard.setattr(widget._compactor, "compact", forbidden)
            guard.setattr(compactor_module, "create_sqlite_snapshot", forbidden)
            guard.setattr(sqlite3, "connect", forbidden)
            for name in ("stat", "exists", "open", "iterdir", "unlink", "resolve"):
                guard.setattr(Path, name, forbidden)
            for translator in translators:
                widget.retranslate(translator)
                app.processEvents()
                assert widget.status_label.text() == (english if translator.locale == "en" else raw)
                assert widget.cancel_button.isEnabled() is allowed
                assert widget._worker is worker and worker.isRunning() and calls == [True]
                assert widget.progress.maximum() == 0 and app.focusWidget() is focus
                assert widget.status_label.property("feedbackState") == "neutral"
    finally:
        release.set()
        assert worker.wait(10000)
        app.processEvents()
    assert legacy == [(message, can_cancel) for _, message, _, can_cancel in PHASES]
    assert widget._last_compaction is not None and widget.progress.value() == 1


REASONS = [
    (CompactionReason.VAULT_MISSING, "O cofre local não foi encontrado.", "The local vault does not exist yet."),
    (CompactionReason.SNAPSHOT_MISMATCH, "O snapshot de segurança não corresponde ao cofre atual.", "The safety snapshot does not match the current vault."),
    (CompactionReason.CONTENT_CHANGED, "O banco compactado não preservou o conteúdo lógico.", "The compacted database did not preserve the logical contents."),
    (CompactionReason.POST_VALIDATION_FAILED, "A validação posterior à compactação falhou.", "Post-compaction validation failed."),
    (CompactionReason.POST_AUDIT_FAILED, "A auditoria posterior à compactação encontrou uma falha.", "The post-compaction audit found a failure."),
    (CompactionReason.COMPACTION_FAILED, "Não foi possível compactar o cofre com segurança.", "The vault could not be compacted safely."),
    (CompactionReason.VAULT_BUSY, "O cofre está ocupado por outra operação.", "The vault is busy with another operation."),
    (CompactionReason.CHECKPOINT_FAILED, "Não foi possível concluir o checkpoint do cofre.", "The vault checkpoint could not be completed."),
    (CompactionReason.VACUUM_FAILED, "Não foi possível gerar o banco compactado.", "The compacted database could not be created."),
    (CompactionReason.STRUCTURE_INVALID, "O banco não passou na validação estrutural.", "The database failed structural validation."),
    (CompactionReason.DATABASE_INVALID, "O banco não pôde ser validado.", "The database could not be validated."),
    (CompactionReason.COMPARISON_FAILED, "Não foi possível comparar o conteúdo do cofre.", "The vault contents could not be compared."),
    (CompactionReason.OPTIMIZE_FAILED, "Não foi possível otimizar o banco compactado.", "The compacted database could not be optimized."),
    (CompactionReason.REPLACEMENT_FAILED, "Não foi possível substituir o cofre de forma transacional.", "The vault could not be replaced transactionally."),
    (CompactionReason.CANCELLED, "A compactação foi cancelada antes da substituição do cofre.", "The operation was cancelled safely."),
]


@pytest.mark.parametrize("reason,raw,english", REASONS, ids=[row[0].value for row in REASONS])
def test_real_error_producers_preserve_legacy_errors_and_add_safe_metadata(page, tmp_path, monkeypatch, reason, raw, english):
    _, widget, paths, _, _ = page
    database = paths.vault_database_path
    compactor = widget._compactor
    action = compactor.compact
    if reason is CompactionReason.VAULT_MISSING:
        compactor._paths = replace(paths, vault_database_path=paths.directory / "missing.db")
    elif reason in (CompactionReason.SNAPSHOT_MISMATCH, CompactionReason.CONTENT_CHANGED, CompactionReason.POST_VALIDATION_FAILED):
        digest = compactor_module._logical_digest
        change_at = {CompactionReason.SNAPSHOT_MISMATCH: 2, CompactionReason.CONTENT_CHANGED: 3,
                     CompactionReason.POST_VALIDATION_FAILED: 4}[reason]
        calls = []

        def changed(path):
            calls.append(path)
            value = digest(path)
            return b"synthetic mismatched digest" if len(calls) == change_at else value

        monkeypatch.setattr(compactor_module, "_logical_digest", changed)
    elif reason is CompactionReason.POST_AUDIT_FAILED:
        audit = result_for(paths).audit
        failed = CheckResult("Synthetic check", IntegrityStatus.FAILURE, 1, 1, "Synthetic safe failure")
        monkeypatch.setattr(compactor._auditor, "run", lambda: replace(audit, checks=(failed,)))
    elif reason is CompactionReason.COMPACTION_FAILED:
        def fail(*args):
            raise OSError("Synthetic private error")
        monkeypatch.setattr(compactor_module, "_vacuum_into", fail)
    elif reason is CompactionReason.VAULT_BUSY:
        class BusyConnection:
            def execute(self, query):
                assert query == "PRAGMA wal_checkpoint(TRUNCATE)"
                return self
            def fetchone(self):
                return (1, 0, 0)
            def close(self):
                pass
        monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: BusyConnection())
        action = lambda: compactor_module._checkpoint(database)
    elif reason is CompactionReason.CHECKPOINT_FAILED:
        def inaccessible(*args, **kwargs):
            raise sqlite3.OperationalError("Synthetic database access failure")
        monkeypatch.setattr(sqlite3, "connect", inaccessible)
        action = lambda: compactor_module._checkpoint(database)
    elif reason is CompactionReason.STRUCTURE_INVALID:
        database = tmp_path / "synthetic-schema.db"
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA user_version=99")
        action = lambda: compactor_module._validate_database(database)
    elif reason is CompactionReason.CANCELLED:
        action = lambda: compactor_module._raise_if_cancelled(lambda: True)
    else:
        invalid = tmp_path / "synthetic-invalid.db"
        invalid.write_bytes(b"synthetic invalid SQLite bytes")
        actions = {
            CompactionReason.VACUUM_FAILED: lambda: compactor_module._vacuum_into(invalid, tmp_path / "new.db", lambda: False),
            CompactionReason.DATABASE_INVALID: lambda: compactor_module._validate_database(invalid),
            CompactionReason.COMPARISON_FAILED: lambda: compactor_module._logical_digest(invalid),
            CompactionReason.OPTIMIZE_FAILED: lambda: compactor_module._optimize(invalid),
            CompactionReason.REPLACEMENT_FAILED: lambda: compactor_module._copy_database(invalid, database),
        }
        action = actions[reason]
    with pytest.raises(MaintenanceError) as caught:
        action()
    error = caught.value
    assert str(error) == raw and error.args == (raw,)
    assert error.explanation.reason is reason
    assert error.explanation.rollback_completed is (reason in (CompactionReason.POST_VALIDATION_FAILED, CompactionReason.POST_AUDIT_FAILED))
    english_result = compaction_error_text(error, Translator("en"))
    assert english_result.splitlines()[0] == english
    assert str(error) == raw
    if error.explanation.rollback_completed:
        assert english_result.endswith("The original vault was restored from the safety snapshot.")


@pytest.mark.parametrize("reason,raw,english", REASONS, ids=[row[0].value for row in REASONS])
@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_completed_failure_can_retranslate_without_service_or_filesystem_access(page, monkeypatch, reason, raw, english, locale):
    app, widget, _, _, _ = page
    accept_and_pause(monkeypatch)
    widget.start_compaction()
    worker = widget._worker
    error = MaintenanceError(raw, explanation=CompactionExplanation(reason))
    worker.failed.emit(error)
    worker.finished.emit()
    cached = widget._compaction_error
    assert cached == (raw, error.explanation)
    app.clipboard().setText("Synthetic clipboard sentinel")
    translator = Translator(locale)

    def forbidden(*args, **kwargs):
        pytest.fail("Retranslation must not run services, access SQLite, or use the filesystem")

    with monkeypatch.context() as guard:
        guard.setattr(widget._compactor, "compact", forbidden)
        guard.setattr(compactor_module, "create_sqlite_snapshot", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        for name in ("stat", "exists", "open", "iterdir", "unlink", "resolve"):
            guard.setattr(Path, name, forbidden)
        widget.retranslate(translator)
        app.processEvents()
    if locale == "en":
        assert widget.status_label.text() == english
    else:
        assert widget.status_label.text() == ("O cofre local ainda não existe." if reason is CompactionReason.VAULT_MISSING
                                              else Translator(locale).text("restoration.csv.status.cancelled")
                                              if reason is CompactionReason.CANCELLED else raw)
    assert widget._compaction_error is cached and error.args == (raw,)
    assert widget._worker is None and widget.progress.value() == 0
    assert widget.status_label.property("feedbackState") == "error"
    assert app.clipboard().text() == "Synthetic clipboard sentinel"


def test_optional_explanation_is_frozen_and_preserves_exception_and_result_contracts():
    legacy = MaintenanceError("safe", "detail")
    assert legacy.args == ("safe", "detail") and legacy.explanation is None
    explanation = CompactionExplanation(CompactionReason.ROLLBACK_FAILED, recovery_directory="dms-vault-compaction-ab12_cd3")
    with pytest.raises(FrozenInstanceError):
        explanation.reason = CompactionReason.COMPACTION_FAILED
    typed = MaintenanceError("legacy safe text", explanation=explanation)
    restored = pickle.loads(pickle.dumps(typed))
    assert restored.args == typed.args and restored.explanation == explanation
    assert MaintenanceCancelled("safe").explanation is None


def test_unknown_safe_original_error_keeps_its_identity_and_literal_text_after_real_rollback(page, monkeypatch):
    _, widget, paths, repository, code = page
    expected = repository.get_decrypted_mapping(code)
    error = MaintenanceError("Unclassified safe error & <literal> {directory}")
    fail_after_real_replacement(monkeypatch, error, fail_rollback=False)
    with pytest.raises(MaintenanceError) as caught:
        widget._compactor.compact()
    assert caught.value is error and error.args == ("Unclassified safe error & <literal> {directory}",)
    assert error.explanation == CompactionExplanation(rollback_completed=True)
    assert compaction_error_text(error, Translator("en")) == (str(error) + "\n"
        + "The original vault was restored from the safety snapshot.")
    assert repository.get_decrypted_mapping(code) == expected
    assert not list(paths.directory.glob("dms-vault-compaction-*"))


@pytest.mark.parametrize("cancelled", [False, True])
def test_vacuum_operational_error_metadata_preserves_existing_cancel_classification(page, tmp_path, cancelled):
    _, _, paths, _, _ = page
    # Missing destination parent gives the existing OperationalError path; an
    # explicit cancellation request keeps the original cancellation precedence.
    action = lambda: compactor_module._vacuum_into(paths.vault_database_path,
        tmp_path / "missing-parent" / "compacted.db", lambda: cancelled)
    with pytest.raises(MaintenanceError) as caught:
        action()
    assert isinstance(caught.value, MaintenanceCancelled) is cancelled
    assert caught.value.explanation.reason is (CompactionReason.CANCELLED if cancelled else CompactionReason.VACUUM_FAILED)


@pytest.mark.parametrize("metadata", [None, "compaction_failed", CompactionExplanation("future_reason"), object()])
def test_unclassified_safe_errors_stay_literal_without_text_recognition(metadata):
    raw = "Não foi possível compactar o cofre com segurança. & <literal> {directory}"
    error = MaintenanceError(raw, explanation=metadata)
    if metadata is None:
        del error.explanation  # Legacy objects need not carry the optional field.
    for locale in ("en", "pt-BR", "en"):
        assert compaction_error_text(error, Translator(locale)) == raw


@pytest.mark.parametrize("directory", [None, "C:\\private\\path", "../secret", "dms-vault-compaction-/private", "dms-vault-compaction-<secret>"])
def test_unsupported_recovery_parameters_never_add_private_information(directory):
    error = MaintenanceError("Existing safe recovery guidance", explanation=CompactionExplanation(
        CompactionReason.ROLLBACK_FAILED, recovery_directory=directory))
    assert compaction_error_text(error, Translator("en")) == str(error)


@pytest.mark.parametrize("terminal", ["failed", "cancelled"])
def test_queued_typed_phase_from_retired_worker_cannot_restore_outdated_feedback(page, monkeypatch, terminal):
    app, widget, _, _, _ = page
    accept_and_pause(monkeypatch)
    widget.start_compaction()
    old = widget._worker
    (old.failed.emit(MaintenanceError("safe")) if terminal == "failed" else old.cancelled.emit())
    old.finished.emit()
    widget.start_compaction()
    current = widget._worker
    before = widget.status_label.text()

    def late():
        old.phase_explained.emit(CompactionPhase.REPLACING, False)
        old.failed.emit(MaintenanceError("safe", explanation=CompactionExplanation(CompactionReason.COMPACTION_FAILED)))
        old.finished.emit()

    thread = Thread(target=late)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    app.processEvents()
    widget.retranslate(Translator("en"))
    assert widget._worker is current and widget._compaction_error is None
    assert widget.status_label.text() == "Preparing safe compaction..." and widget.progress.maximum() == 0
    assert before == Translator("pt-BR").text("maintenance.compaction.status.preparing")
    current.phase_changed.emit("Legacy phase", True)
    widget.cancel()
    current.phase_explained.emit(CompactionPhase.SNAPSHOT, True)
    widget.retranslate(Translator("en"))
    assert widget.status_label.text() == "Cancellation requested..."
    assert not widget.cancel_button.isEnabled() and current._cancellation.is_set()
    current.cancelled.emit()
    current.finished.emit()
    assert widget.progress.value() == 0

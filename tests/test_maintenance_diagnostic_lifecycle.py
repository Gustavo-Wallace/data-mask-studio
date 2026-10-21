"""Consolidated diagnostics keep audit details and never reuse stale success."""

import os
import sqlite3
from pathlib import Path
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.maintenance_worker import DiagnosticWorker
from data_mask_studio.integrity import IntegrityStatus
from data_mask_studio.maintenance import (
    MaintenanceCancelled, MaintenanceStatus, safe_diagnostic_report,
)
from test_maintenance import diagnostics, prepare_environment
from test_maintenance_interface import prepare, wait, widget_for


@pytest.fixture
def diagnostic_gui(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    paths, code = prepare(tmp_path)
    widget = widget_for(paths)
    widget.resize(1000, 700)
    widget.show()
    app.processEvents()
    return app, widget, paths, code


def complete_diagnostic(app, widget):
    widget.start_diagnostic()
    wait(app, widget)
    assert widget._last_diagnostic is not None


def test_details_and_copied_report_include_safe_failure_references(diagnostic_gui):
    app, widget, paths, code = diagnostic_gui
    with sqlite3.connect(paths.vault_database_path) as connection:
        connection.execute("UPDATE vault_mappings SET total_occurrences = 9")
    complete_diagnostic(app, widget)
    text = widget.overview_output.toPlainText()
    assert "Contadores de ocorrências" in text
    assert "examinados=" in text and "falhas=1" in text
    assert "mapping#1" in text
    assert "Início da auditoria:" in text and "Fim da auditoria:" in text
    widget.copy_report_button.click()
    copied = app.clipboard().text()
    assert "mapping#1" in copied and "Contadores de ocorrências" in copied
    for unsafe in (code, "123.456.789-00", "12345678900", str(paths.directory)):
        assert unsafe not in text and unsafe not in copied
    assert widget.status_label.property("feedbackState") == "error"
    assert widget.progress.maximum() > 0 and widget.progress.value() == 0


@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
def test_new_run_discards_success_and_terminal_states_are_independent(
    diagnostic_gui, monkeypatch, outcome,
):
    app, widget, _, _ = diagnostic_gui
    complete_diagnostic(app, widget)
    previous = widget._last_diagnostic
    monkeypatch.setattr(DiagnosticWorker, "start", lambda self: None)
    widget.start_diagnostic()
    worker = widget._worker
    assert widget._last_diagnostic is None
    assert not widget.copy_report_button.isEnabled()
    assert "Estado geral: Saudável" not in widget.overview_output.toPlainText()
    app.clipboard().setText("unchanged clipboard")
    widget.copy_report()
    assert app.clipboard().text() == "unchanged clipboard"
    worker.progress.emit(13, 16)
    if outcome == "success":
        worker.completed.emit(previous)
    elif outcome == "failure":
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
    else:
        widget.cancel_button.click()
        worker.cancelled.emit()
    worker.finished.emit()
    assert widget._worker is None and widget.sections.isEnabled()
    assert widget.cancel_button.isHidden() and widget.progress.maximum() > 0
    assert "PRIVATE_SENTINEL" not in widget.status_label.text()
    if outcome == "success":
        assert widget.progress.value() == widget.progress.maximum()
        assert widget.copy_report_button.isEnabled()
    else:
        assert widget.progress.value() == 0
        assert widget._last_diagnostic is None
        assert not widget.copy_report_button.isEnabled()
        assert "Estado geral: Saudável" not in widget.overview_output.toPlainText()


@pytest.mark.parametrize("reason", ["environment", "late_cancel"])
def test_invalidated_or_cancelled_worker_cannot_republish_success(
    diagnostic_gui, monkeypatch, reason,
):
    app, widget, _, _ = diagnostic_gui
    complete_diagnostic(app, widget)
    previous = widget._last_diagnostic
    monkeypatch.setattr(DiagnosticWorker, "start", lambda self: None)
    widget.start_diagnostic()
    worker = widget._worker
    if reason == "environment":
        widget.invalidate_diagnostic()
    else:
        widget.cancel_button.click()
    worker.completed.emit(previous)
    worker.finished.emit()
    assert widget._last_diagnostic is None
    assert widget.progress.value() == 0
    assert not widget.copy_report_button.isEnabled()
    assert "Estado geral: Saudável" not in widget.overview_output.toPlainText()


@pytest.mark.parametrize("callback", ["_environment_restored", "_environment_maintained"])
def test_main_window_environment_events_invalidate_diagnostic(tmp_path, monkeypatch, callback):
    from test_main_navigation import build_window

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    window = build_window(tmp_path)
    widget = window.maintenance_widget
    complete_diagnostic(app, widget)
    getattr(window, callback)()
    assert widget._last_diagnostic is None
    assert not widget.copy_report_button.isEnabled()
    assert not widget.overview_output.toPlainText()
    assert widget.progress.value() == 0
    assert "desatualizado" in widget.status_label.text().casefold()
    assert "Integridade" in [button.text() for button in window.navigation.buttons]


def test_statistics_progress_is_included_and_cancellable(tmp_path):
    paths, _, _ = prepare_environment(tmp_path)
    cancellation = Event()
    progress = []

    def update(done, total):
        progress.append((done, total))
        if done == 13:
            cancellation.set()

    with pytest.raises(MaintenanceCancelled):
        diagnostics(paths).run(should_cancel=cancellation.is_set, progress_callback=update)
    assert (12, 16) in progress and (13, 16) in progress
    assert all(done < total for done, total in progress)


def test_statistics_failure_cannot_leave_healthy_status(tmp_path, monkeypatch):
    import data_mask_studio.maintenance.diagnostics as module

    paths, _, _ = prepare_environment(tmp_path)
    def fail_statistics(*args, **kwargs):
        raise OSError("PRIVATE_SENTINEL")

    # The auditor has its own reference: only additional statistics fail.
    monkeypatch.setattr(module, "create_sqlite_snapshot", fail_statistics)
    progress = []
    result = diagnostics(paths).run(progress_callback=lambda done, total: progress.append((done, total)))
    assert result.audit.status is IntegrityStatus.INTACT
    assert result.status is MaintenanceStatus.FAILURE
    assert result.statistics.errors
    assert all(done < total for done, total in progress)
    assert "PRIVATE_SENTINEL" not in safe_diagnostic_report(result)


def test_successful_service_progress_reaches_completion_only_after_statistics(tmp_path):
    paths, _, _ = prepare_environment(tmp_path)
    progress = []
    result = diagnostics(paths).run(progress_callback=lambda done, total: progress.append((done, total)))
    assert result.status is MaintenanceStatus.HEALTHY
    assert progress[-1] == (16, 16)
    assert all(total == 16 and 0 <= done <= total for done, total in progress)
    assert [done for done, _ in progress] == sorted(done for done, _ in progress)


@pytest.mark.parametrize("corrupt", [False, True])
def test_all_scalar_and_composite_checks_are_visible_without_values(diagnostic_gui, corrupt):
    from test_composite_integrity import CANONICAL, ORIGINAL, setup

    app, widget, paths, scalar_code = diagnostic_gui
    _, composite = setup(paths.directory)
    if corrupt:
        with sqlite3.connect(paths.vault_database_path) as connection:
            connection.execute("UPDATE composite_mappings SET nonce=X'00'")
    complete_diagnostic(app, widget)
    result = widget._last_diagnostic
    assert result.status is (MaintenanceStatus.FAILURE if corrupt else MaintenanceStatus.HEALTHY)
    widget.copy_report()
    for text in (widget.overview_output.toPlainText(), app.clipboard().text()):
        for check in result.audit.checks:
            assert check.check_type in text
            assert f"examinados={check.examined}; falhas={check.failures}; {check.message}" in text
            for identifier in check.identifiers:
                assert identifier in text
        assert "Mappings compostos" in text and "Autenticação AES-GCM" in text
        for secret in (*CANONICAL, *ORIGINAL, scalar_code, composite.code, str(paths.directory)):
            assert secret not in text


def test_attention_and_failure_reports_have_distinct_status(diagnostic_gui):
    app, widget, paths, _ = diagnostic_gui
    sentinel = paths.directory / ".profiles.json.ab12_cd3.tmp"
    sentinel.write_bytes(b"synthetic abandoned temporary")
    complete_diagnostic(app, widget)
    assert widget._last_diagnostic.status is MaintenanceStatus.ATTENTION
    assert widget.status_label.property("feedbackState") == "warning"
    assert widget.progress.value() == widget.progress.maximum()
    assert "Arquivos temporários abandonados" in widget.overview_output.toPlainText()
    assert sentinel.is_file()  # Auditing never deletes artifacts.
    with sqlite3.connect(paths.vault_database_path) as connection:
        connection.execute("UPDATE vault_mappings SET canonical_nonce=X'00'")
    complete_diagnostic(app, widget)
    assert widget._last_diagnostic.status is MaintenanceStatus.FAILURE
    assert widget.status_label.property("feedbackState") == "error"
    assert widget.progress.value() == 0


def test_queued_real_worker_result_is_rejected_after_invalidation(diagnostic_gui):
    app, widget, _, _ = diagnostic_gui
    widget.start_diagnostic()
    worker = widget._worker
    assert worker.wait(10000)  # Signals are queued, not yet delivered to the GUI.
    widget.invalidate_diagnostic()
    app.processEvents()
    assert widget._worker is None
    assert widget._last_diagnostic is None
    assert not widget.copy_report_button.isEnabled()
    assert widget.progress.value() == 0
    assert "desatualizado" in widget.status_label.text()


def test_late_signals_from_previous_worker_do_not_change_new_run(diagnostic_gui, monkeypatch):
    app, widget, _, _ = diagnostic_gui
    complete_diagnostic(app, widget)
    previous = widget._last_diagnostic
    monkeypatch.setattr(DiagnosticWorker, "start", lambda self: None)
    widget.start_diagnostic()
    old_worker = widget._worker
    old_worker.failed.emit(RuntimeError())
    old_worker.finished.emit()
    widget.start_diagnostic()
    current = widget._worker
    old_worker.progress.emit(16, 16)
    old_worker.completed.emit(previous)
    old_worker.failed.emit(RuntimeError())
    old_worker.cancelled.emit()
    old_worker.finished.emit()
    assert widget._worker is current and widget._last_diagnostic is None
    assert widget.progress.maximum() == 0
    assert widget.status_label.text() == "Atualizando diagnóstico..."
    current.completed.emit(previous)
    current.finished.emit()
    assert widget._last_diagnostic is previous


@pytest.mark.parametrize("phase", ["snapshot", "query", "directory"])
def test_statistics_cancellation_closes_snapshot_and_does_not_complete(
    tmp_path, monkeypatch, phase,
):
    import data_mask_studio.maintenance.diagnostics as module

    paths, _, _ = prepare_environment(tmp_path)
    service = diagnostics(paths)
    cancellation = Event()
    snapshots = []
    original_snapshot = module.create_sqlite_snapshot
    original_connect = module.connect_read_only
    original_rglob = Path.rglob

    def snapshot(source, destination, request):
        snapshots.append(destination)
        if phase == "snapshot":
            cancellation.set()
        original_snapshot(source, destination, request)

    def connect(path):
        connection = original_connect(path)
        if phase == "query":
            connection.set_trace_callback(lambda sql: cancellation.set())
        return connection

    def rglob(path, pattern):
        if path == paths.directory and phase == "directory":
            cancellation.set()
        yield from original_rglob(path, pattern)

    monkeypatch.setattr(module, "create_sqlite_snapshot", snapshot)
    monkeypatch.setattr(module, "connect_read_only", connect)
    monkeypatch.setattr(Path, "rglob", rglob)
    progress = []
    with pytest.raises(MaintenanceCancelled):
        service.run(should_cancel=cancellation.is_set,
                    progress_callback=lambda done, total: progress.append((done, total)))
    assert snapshots and all(not path.parent.exists() for path in snapshots)
    assert all(done < total for done, total in progress)


@pytest.mark.parametrize("phase", ["profiles", "storage"])
def test_additional_statistics_errors_are_safe_and_cannot_report_success(tmp_path, monkeypatch, phase):
    import data_mask_studio.maintenance.diagnostics as module
    from data_mask_studio.profiles import ProfileError

    paths, _, _ = prepare_environment(tmp_path)
    service = diagnostics(paths)
    # Preserve a real successful authoritative audit, fail only extra statistics.
    audit = service._auditor.run()
    monkeypatch.setattr(service._auditor, "run", lambda **kwargs: audit)

    def fail(*args):
        raise (ProfileError if phase == "profiles" else OSError)("PRIVATE_SENTINEL")

    if phase == "profiles":
        monkeypatch.setattr(module.ProfileRepository, "load", fail)
    else:
        monkeypatch.setattr(module, "_free_space", fail)
    result = service.run()
    assert result.audit is audit
    assert result.status is MaintenanceStatus.FAILURE and result.statistics.errors
    assert "coleta incompleta" in safe_diagnostic_report(result)
    assert "PRIVATE_SENTINEL" not in safe_diagnostic_report(result)


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_compaction_invalidates_diagnostic_without_losing_its_own_feedback(
    diagnostic_gui, monkeypatch, outcome,
):
    from PySide6.QtWidgets import QMessageBox
    from data_mask_studio.gui.maintenance_worker import CompactionWorker
    from data_mask_studio.maintenance import CompactionResult

    app, widget, _, _ = diagnostic_gui
    complete_diagnostic(app, widget)
    previous = widget._last_diagnostic
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(CompactionWorker, "start", lambda self: None)
    widget.sections.setCurrentIndex(3)
    widget.start_compaction()
    worker = widget._worker
    assert widget._last_diagnostic is None and not widget.copy_report_button.isEnabled()
    if outcome == "completed":
        worker.completed.emit(CompactionResult(100, 90, 10, previous.audit))
    elif outcome == "failed":
        worker.failed.emit(RuntimeError())
    else:
        worker.cancelled.emit()
    worker.finished.emit()
    compact_feedback = widget.status_label.text()
    widget.sections.setCurrentIndex(0)
    assert widget.progress.value() == 0
    assert "desatualizado" in widget.status_label.text()
    assert not widget.overview_output.toPlainText()
    widget.sections.setCurrentIndex(3)
    assert widget.status_label.text() == compact_feedback


def test_profile_change_invalidates_main_window_diagnostic(tmp_path, monkeypatch):
    from test_main_navigation import build_window

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    window = build_window(tmp_path)
    complete_diagnostic(app, window.maintenance_widget)
    window.anonymization_widget.profiles_changed.emit()
    assert window.maintenance_widget._last_diagnostic is None
    assert not window.maintenance_widget.copy_report_button.isEnabled()


def test_cancel_after_result_delivery_but_before_thread_finish_clears_success(diagnostic_gui, monkeypatch):
    app, widget, _, _ = diagnostic_gui
    complete_diagnostic(app, widget)
    result = widget._last_diagnostic
    monkeypatch.setattr(DiagnosticWorker, "start", lambda self: None)
    widget.start_diagnostic()
    worker = widget._worker
    worker.completed.emit(result)
    assert widget._last_diagnostic is result
    assert widget.copy_report_button.isEnabledTo(widget.sections)
    widget.cancel_button.click()
    worker.finished.emit()
    assert widget._last_diagnostic is None and not widget.copy_report_button.isEnabled()
    assert not widget.overview_output.toPlainText()
    assert widget.progress.value() == 0
    assert "cancelada" in widget.status_label.text()


def test_removing_known_residue_invalidates_old_attention_result(diagnostic_gui, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    app, widget, paths, _ = diagnostic_gui
    residue = paths.directory / ".profiles.json.ab12_cd3.tmp"
    residue.write_bytes(b"synthetic abandoned DMS temporary")
    os.utime(residue, (0, 0))
    complete_diagnostic(app, widget)
    assert widget._last_diagnostic.status is MaintenanceStatus.ATTENTION
    widget.sections.setCurrentIndex(2)
    widget.start_temporary_scan()
    wait(app, widget)
    assert len(widget.temporary_items) == 1
    widget.temporary_items[0].selected = True
    monkeypatch.setattr(
        "data_mask_studio.gui.maintenance_widget.confirm_destructive_action",
        lambda *args: QMessageBox.StandardButton.Yes,
    )
    widget.start_cleanup()
    wait(app, widget)
    assert not residue.exists()
    assert widget._last_diagnostic is None and not widget.copy_report_button.isEnabled()
    assert "1 removido(s)" in widget.status_label.text()
    widget.sections.setCurrentIndex(0)
    assert widget.progress.value() == 0 and "desatualizado" in widget.status_label.text()
    assert widget.status_label.property("feedbackState") == "warning"


def test_real_worker_cancellation_during_statistics_has_no_success_feedback(diagnostic_gui, monkeypatch):
    from PySide6.QtCore import Qt

    app, widget, _, _ = diagnostic_gui
    start = DiagnosticWorker.start
    reached = []

    def start_with_cancel(worker):
        def cancel_at_statistics(done, total):
            if done == 13:
                reached.append((done, total))
                worker.request_cancel()
        worker.progress.connect(cancel_at_statistics, Qt.ConnectionType.DirectConnection)
        start(worker)

    monkeypatch.setattr(DiagnosticWorker, "start", start_with_cancel)
    widget.start_diagnostic()
    wait(app, widget)
    assert reached == [(13, 16)]
    assert widget._worker is None and widget._last_diagnostic is None
    assert widget.progress.value() == 0 and not widget.copy_report_button.isEnabled()
    assert "cancelada" in widget.status_label.text()


@pytest.mark.parametrize("mask", [False, True])
def test_individual_generation_invalidates_only_when_vault_may_change(tmp_path, monkeypatch, mask):
    from test_main_navigation import build_window

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    window = build_window(tmp_path)
    source = tmp_path / "synthetic.csv"
    source.write_text("Nome\nSynthetic Person\n", encoding="utf-8")
    anonymization = window.anonymization_widget
    anonymization.load_csv(str(source))
    if mask:
        anonymization.select_all_columns()
    anonymization.validate_current_configuration()
    complete_diagnostic(app, window.maintenance_widget)
    previous = window.maintenance_widget._last_diagnostic
    anonymization._start_processing(tmp_path / "output.csv", overwrite=False)
    worker = anonymization._worker
    assert worker is not None
    assert (window.maintenance_widget._last_diagnostic is None) is mask
    assert worker.wait(10000)
    app.processEvents()
    assert (tmp_path / "output.csv").is_file()
    if not mask:
        assert window.maintenance_widget._last_diagnostic is previous


def test_batch_processing_invalidates_diagnostic_before_possible_vault_writes(tmp_path, monkeypatch):
    from test_main_navigation import build_window
    from data_mask_studio.anonymization import ColumnConfig

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    window = build_window(tmp_path)
    window._profile_service.create("Synthetic profile", [ColumnConfig("Nome", True, "NOME")])
    source = tmp_path / "synthetic.csv"
    source.write_text("Nome\nSynthetic Person\n", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    batch = window.batch_widget
    batch.refresh_profiles()
    batch.add_paths([source])
    batch.validate_files()
    assert batch._validation_worker.wait(10000)
    app.processEvents()
    complete_diagnostic(app, window.maintenance_widget)
    batch.output_field.setText(str(output))
    batch.start_processing()
    worker = batch._processing_worker
    assert worker is not None
    assert window.maintenance_widget._last_diagnostic is None
    assert worker.wait(10000)
    app.processEvents()
    assert batch.files[0].status.value == "completed"

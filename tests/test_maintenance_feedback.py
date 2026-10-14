"""Maintenance feedback belongs to its operation, not to every tab."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.backup_worker import BackupValidationWorker
from data_mask_studio.gui.maintenance_worker import (
    CompactionWorker, DiagnosticWorker, TemporaryScanWorker,
)
from test_maintenance_interface import prepare, wait, widget_for


@pytest.fixture
def maintenance(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    paths, _ = prepare(tmp_path)
    widget = widget_for(paths)
    widget.show()
    app.processEvents()
    return app, widget


def feedback(widget):
    return (
        widget.progress.minimum(), widget.progress.maximum(), widget.progress.value(),
        widget.status_label.text(), widget.status_label.property("feedbackState"),
    )


@pytest.mark.parametrize("tab", range(4))
def test_initial_diagnostic_feedback_is_visible_only_in_overview(maintenance, tab):
    app, widget = maintenance
    widget.sections.setCurrentIndex(tab)
    app.processEvents()
    assert widget.progress.isVisible() is (tab == 0)
    assert widget.status_label.isVisible() is (tab == 0)
    assert widget.sections.currentWidget().isVisible()


@pytest.mark.parametrize("tab", [1, 2, 3])
def test_completed_diagnostic_feedback_does_not_leak_and_survives_tab_switches(maintenance, tab):
    app, widget = maintenance
    widget.start_diagnostic()
    wait(app, widget)
    assert widget.progress.isVisible() and widget.status_label.isVisible()
    assert widget.progress.value() == widget.progress.maximum() == 1
    assert widget.status_label.text() == "Diagnóstico concluído: Saudável."
    assert widget.status_label.property("feedbackState") == "success"
    previous = feedback(widget)
    report = widget.overview_output.toPlainText()
    assert "Mapeamentos: 1" in report
    widget.sections.setCurrentIndex(tab)
    app.processEvents()
    assert not widget.progress.isVisible() and not widget.status_label.isVisible()
    widget.sections.setCurrentIndex(0)
    app.processEvents()
    assert widget.progress.isVisible() and widget.status_label.isVisible()
    assert feedback(widget) == previous
    assert widget.overview_output.toPlainText() == report
    assert widget.copy_report_button.isEnabled()


def test_temporary_operation_keeps_own_feedback_without_replacing_diagnostic_state(maintenance):
    app, widget = maintenance
    widget.start_diagnostic()
    wait(app, widget)
    diagnostic = feedback(widget)
    widget.sections.setCurrentIndex(2)
    widget.locate_button.click()
    wait(app, widget)
    temporary = feedback(widget)
    assert widget.progress.isVisible() and widget.status_label.isVisible()
    assert "temporário(s) conhecido(s)" in widget.status_label.text()
    assert widget.status_label.property("feedbackState") == "success"
    widget.sections.setCurrentIndex(0)
    assert feedback(widget) == diagnostic
    widget.sections.setCurrentIndex(2)
    assert feedback(widget) == temporary


def test_running_diagnostic_progress_and_cancel_remain_accessible(maintenance, monkeypatch):
    app, widget = maintenance
    monkeypatch.setattr(DiagnosticWorker, "start", lambda self: None)
    widget.refresh_button.click()
    worker = widget._worker
    assert worker is not None
    assert widget.progress.isVisible() and widget.progress.maximum() == 0
    assert widget.cancel_button.isVisible() and widget.cancel_button.isEnabled()
    worker.progress.emit(3, 12)
    assert widget.progress.value() == 3 and widget.progress.maximum() == 12
    previous = feedback(widget)
    # Tabs are disabled to users while running; even a programmatic switch must
    # not reset the ongoing diagnostic's counters or status.
    widget.sections.setCurrentIndex(1)
    assert not widget.progress.isVisible() and not widget.status_label.isVisible()
    widget.sections.setCurrentIndex(0)
    app.processEvents()
    assert feedback(widget) == previous
    assert widget.progress.isVisible() and widget.cancel_button.isEnabled()
    widget.cancel_button.click()
    assert worker._cancellation.is_set()
    worker.cancelled.emit()
    worker.finished.emit()
    assert widget.cancel_button.isHidden()
    assert widget.status_label.property("feedbackState") == "warning"


@pytest.mark.parametrize("tab,worker_type,button", [
    (1, BackupValidationWorker, "validate_backup_button"),
    (2, TemporaryScanWorker, "locate_button"),
    (3, CompactionWorker, "compact_button"),
])
@pytest.mark.parametrize("outcome", ["failure", "cancelled"])
def test_other_operations_keep_progress_cancel_and_terminal_feedback(
    maintenance, monkeypatch, tab, worker_type, button, outcome,
):
    app, widget = maintenance
    widget.sections.setCurrentIndex(tab)
    widget.backup_path_field.setText("synthetic.dmsbackup")
    monkeypatch.setattr(worker_type, "start", lambda self: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Yes)
    getattr(widget, button).click()
    worker = widget._worker
    assert worker is not None
    assert widget.progress.isVisible() and widget.status_label.isVisible()
    assert widget.progress.maximum() == 0
    assert not widget.sections.isEnabled()
    assert widget.cancel_button.isVisible() and widget.cancel_button.isEnabled()
    if outcome == "failure":
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
        assert "PRIVATE_SENTINEL" not in widget.status_label.text()
        assert widget.status_label.property("feedbackState") == "error"
    else:
        widget.cancel_button.click()
        if isinstance(worker, BackupValidationWorker):
            assert worker._cancellation.is_requested()
        else:
            assert worker._cancellation.is_set()
        worker.cancelled.emit()
        assert widget.status_label.property("feedbackState") == "warning"
    worker.finished.emit()
    assert widget._worker is None and widget.sections.isEnabled()
    assert widget.cancel_button.isHidden() and widget.progress.maximum() > 0
    terminal = feedback(widget)
    widget.sections.setCurrentIndex(0)
    widget.sections.setCurrentIndex(tab)
    app.processEvents()
    assert widget.progress.isVisible() and widget.status_label.isVisible()
    assert feedback(widget) == terminal


def test_backup_input_error_is_scoped_to_backup_tab(maintenance):
    app, widget = maintenance
    diagnostic = feedback(widget)
    widget.sections.setCurrentIndex(1)
    widget.validate_backup_button.click()
    assert widget.status_label.isVisible()
    assert widget.status_label.text() == "Selecione um backup e informe sua senha."
    assert widget.status_label.property("feedbackState") == "error"
    widget.sections.setCurrentIndex(0)
    app.processEvents()
    assert feedback(widget) == diagnostic

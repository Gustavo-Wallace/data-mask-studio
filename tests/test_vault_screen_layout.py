"""Operational screen layout and state contracts, without pixel snapshots."""

import os
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QLabel, QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import TokenGenerator
from data_mask_studio.app import create_application
from data_mask_studio.gui.integrity_worker import IntegrityWorker
from data_mask_studio.gui.maintenance_worker import DiagnosticWorker
from data_mask_studio.integrity import AuditReport, CheckResult, IntegrityStatus
from data_mask_studio.vault import MappingCandidate
from test_main_navigation import build_window


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-local"))
    app = create_application([])
    window = build_window(tmp_path)
    window.show()
    app.processEvents()
    return app, window


def seed_mapping(window):
    original = "Synthetic Person"
    code = TokenGenerator(b"H" * 32).generate("NOME", original)
    repository = window._vault_repository_factory()
    with repository.transaction() as transaction:
        transaction.upsert_batch([MappingCandidate(code, "NOME", original, "Nome", 2)])
    return code, original


def safe_report(window):
    paths = window._backup_paths
    now = datetime.now(timezone.utc)
    return AuditReport(now, now, paths.vault_database_path, paths.profiles_path, 4,
                       (CheckResult("Verificação sintética", IntegrityStatus.INTACT, 1, 0,
                                    "Resultado agregado seguro."),))


@pytest.mark.parametrize("name,attribute", [("consultant", "results_output"),
                                           ("integrity", "report_view")])
def test_empty_result_is_compact_and_filled_result_gets_expansion_priority(page, name, attribute):
    app, window = page
    widget = getattr(window, f"{name}_widget")
    output = getattr(widget, attribute)
    window.set_current_page(window.page_index(widget))
    window.resize(1280, 820)
    app.processEvents()
    empty_height = output.height()
    assert output.isVisible() and output.isReadOnly()
    assert output.maximumHeight() == 160 and empty_height <= 160
    assert output.accessibleDescription() == output.empty_text
    assert widget.layout().stretch(widget.layout().indexOf(output)) == 1
    output.setPlainText("\n".join(f"Synthetic report line {index}" for index in range(80)))
    app.processEvents()
    assert output.maximumHeight() == 16_777_215
    assert output.height() > empty_height
    window.resize(1280, 1000)
    app.processEvents()
    assert output.height() > empty_height
    output.clear()
    app.processEvents()
    assert output.isVisible() and output.maximumHeight() == 160


def test_consultant_warning_copy_and_clear_remain_explicit_and_transient(page):
    app, window = page
    widget = window.consultant_widget
    window.set_current_page(window.page_index(widget))
    app.processEvents()
    warning = next(label for label in widget.findChildren(QLabel)
                   if label.text().startswith("Atenção: os resultados"))
    assert warning.property("feedbackState") == "warning" and warning.wordWrap()
    assert warning.isVisible() and warning.geometry().bottom() < widget.results_output.geometry().top()
    assert widget.consult_button.property("role") == "primary"
    assert widget.clear_button.property("role") is None and widget.copy_button.property("role") is None
    assert not widget.copy_button.isEnabled()
    code, original = seed_mapping(window)
    app.clipboard().setText("Synthetic clipboard sentinel")
    widget.codes_input.setPlainText(code)
    widget.consult_button.click()
    assert f"Valor original principal: {original}" in widget.results_output.toPlainText()
    assert "Ocorrências totais: 2" in widget.results_output.toPlainText()
    assert widget.copy_button.isEnabled() and widget.status_label.property("feedbackState") == "success"
    assert app.clipboard().text() == "Synthetic clipboard sentinel"
    widget.copy_button.click()
    assert app.clipboard().text() == widget.results_output.toPlainText()
    widget.clear_button.click()
    app.processEvents()
    assert widget.codes_input.hasFocus() and not widget.copy_button.isEnabled()
    assert widget.results_output.toPlainText() == "" and widget.results_output.maximumHeight() == 160
    assert widget.status_label.property("feedbackState") == "neutral"


@pytest.mark.parametrize("outcome,state", [("success", "success"), ("cancelled", "warning"), ("failure", "error")])
def test_integrity_running_and_terminal_states_keep_existing_controls_and_progress(page, monkeypatch, outcome, state):
    app, window = page
    widget = window.integrity_widget
    window.set_current_page(window.page_index(widget))
    assert widget.status_label.property("feedbackState") == "neutral"
    assert widget.run_button.isEnabled() and not widget.copy_button.isEnabled()
    assert widget.progress_bar.value() == 0 and widget.cancel_button.isHidden()
    monkeypatch.setattr(IntegrityWorker, "start", lambda self: None)
    widget.run_button.click()
    worker = widget._worker
    assert worker is not None and not widget.run_button.isEnabled()
    assert widget.cancel_button.isVisible() and not widget.copy_button.isEnabled()
    assert widget.status_label.property("feedbackState") == "neutral"
    worker.progress.emit(3, 12)
    assert widget.progress_bar.value() == 3
    if outcome == "success":
        report = safe_report(window)
        worker.progress.emit(12, 12)
        worker.completed.emit(report)
        assert widget.report_view.toPlainText() == report.to_safe_text()
        assert widget.progress_bar.value() == 12
        assert "ainda não executada" not in widget.last_check_label.text()
    elif outcome == "cancelled":
        widget.cancel_button.click()
        assert worker._cancellation.is_set() and not widget.cancel_button.isEnabled()
        worker.cancelled.emit()
    else:
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
        assert "PRIVATE_SENTINEL" not in widget.status_label.text()
    worker.finished.emit()
    assert widget._worker is None and widget.run_button.isEnabled() and widget.cancel_button.isHidden()
    assert widget.copy_button.isEnabled() is (outcome == "success")
    assert widget.status_label.property("feedbackState") == state


def test_maintenance_tabs_actions_and_result_priorities_preserve_controls(page):
    app, window = page
    widget = window.maintenance_widget
    window.set_current_page(window.page_index(widget))
    assert [widget.sections.tabText(index) for index in range(4)] == [
        "Visão geral", "Validar backup", "Temporários", "Compactação",
    ]
    assert not widget.copy_report_button.isEnabled() and not widget.cleanup_button.isEnabled()
    assert widget.refresh_button.property("role") == widget.validate_backup_button.property("role") == "primary"
    assert widget.locate_button.property("role") == "primary"
    assert widget.cleanup_button.property("role") == "destructive"
    assert widget.compact_button.property("role") == "attention"
    assert widget.compaction_info.property("feedbackState") == "warning"
    assert widget.backup_password_field.echoMode() == QLineEdit.EchoMode.Password
    assert widget.layout().stretch(0) == 0 and widget.layout().stretch(widget.layout().count() - 1) == 1
    for tab, output in ((0, widget.overview_output), (1, widget.backup_result), (3, widget.compaction_result)):
        widget.sections.setCurrentIndex(tab)
        app.processEvents()
        assert output.isVisible() and output.isReadOnly() and output.maximumHeight() == 160
        assert output.parentWidget().layout().stretch(output.parentWidget().layout().indexOf(output)) == 1
    widget.backup_result.setPlainText("Synthetic safe validation")
    assert widget.layout().stretch(0) == 1 and widget.layout().stretch(widget.layout().count() - 1) == 0
    for index in range(4):
        widget.sections.setCurrentIndex(index)
        app.processEvents()
        assert widget.layout().stretch(0) == 1  # Switching tabs keeps the populated geometry.
    widget.compaction_result.setPlainText("Synthetic compaction result")
    widget.backup_result.clear()
    assert widget.layout().stretch(0) == 1
    widget.compaction_result.clear()
    assert widget.layout().stretch(0) == 0
    widget.temporary_table.setRowCount(1)
    assert widget.layout().stretch(0) == 1
    widget.temporary_table.setRowCount(0)
    assert widget.layout().stretch(0) == 0
    assert widget.overview_output.isReadOnly() and widget.backup_path_field.isReadOnly()
    assert not widget.copy_report_button.isEnabled() and not widget.cleanup_button.isEnabled()


def test_real_audit_and_diagnostic_preserve_safe_reports_and_copy_contract(page):
    app, window = page
    code, original = seed_mapping(window)
    for widget, output, button, copy in (
        (window.integrity_widget, window.integrity_widget.report_view,
         window.integrity_widget.run_button, window.integrity_widget.copy_button),
        (window.maintenance_widget, window.maintenance_widget.overview_output,
         window.maintenance_widget.refresh_button, window.maintenance_widget.copy_report_button),
    ):
        window.set_current_page(window.page_index(widget))
        button.click()
        worker = widget._worker
        assert worker is not None and worker.wait(10000)
        app.processEvents()
        assert output.toPlainText() and code not in output.toPlainText() and original not in output.toPlainText()
        assert output.maximumHeight() == 16_777_215
        assert widget.status_label.property("feedbackState") == "success" and copy.isEnabled()
        copy.click()
        assert app.clipboard().text() and code not in app.clipboard().text() and original not in app.clipboard().text()
    assert window.integrity_widget.report_view.toPlainText() == window.integrity_widget._last_report.to_safe_text()
    assert "Mapeamentos: 1" in window.maintenance_widget.overview_output.toPlainText()
    assert window.maintenance_widget.layout().stretch(0) == 1


def test_result_sizing_of_other_pages_is_unchanged(page):
    _, window = page
    for output, empty_height in (
        (window.batch_widget.summary_output, 80),
        (window.restoration_widget.summary, 125),
        (window.html_restoration_widget.summary, 220),
        (window.batch_restoration_widget.summary_output, 110),
        (window.backup_widget.restore_summary, 80),
    ):
        assert output.maximumHeight() == empty_height


@pytest.mark.parametrize("outcome,state", [("cancelled", "warning"), ("failure", "error")])
def test_maintenance_busy_and_terminal_feedback_remain_safe(page, monkeypatch, outcome, state):
    _, window = page
    widget = window.maintenance_widget
    window.set_current_page(window.page_index(widget))
    monkeypatch.setattr(DiagnosticWorker, "start", lambda self: None)
    widget.refresh_button.click()
    worker = widget._worker
    assert worker is not None and not widget.sections.isEnabled()
    assert widget.cancel_button.isVisible() and widget.progress.maximum() == 0
    if outcome == "cancelled":
        widget.cancel_button.click()
        assert worker._cancellation.is_set() and not widget.cancel_button.isEnabled()
        worker.cancelled.emit()
    else:
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
        assert "PRIVATE_SENTINEL" not in widget.status_label.text()
    worker.finished.emit()
    assert widget._worker is None and widget.sections.isEnabled() and widget.cancel_button.isHidden()
    assert widget.progress.maximum() > 0 and widget.status_label.property("feedbackState") == state


@pytest.mark.parametrize("name,attribute", [("consultant", "results_output"), ("integrity", "report_view"),
                                           ("maintenance", "overview_output")])
@pytest.mark.parametrize("size", [(960, 640), (1280, 820), "maximized"])
def test_empty_and_populated_pages_keep_footer_reachable_at_supported_sizes(page, name, attribute, size):
    app, window = page
    widget = getattr(window, f"{name}_widget")
    output = getattr(widget, attribute)
    index = window.page_index(widget)
    window.set_current_page(index)
    if size == "maximized":
        window.showMaximized()
    else:
        window.resize(*size)
    for text in ("", "\n".join("Synthetic result" for _ in range(100))):
        output.setPlainText(text)
        app.processEvents()
        shell = window.page_shells[index]
        assert shell.header.isVisible() and output.isVisible()
        assert output.viewport().height() > 0
        shell.scroll_area.ensureWidgetVisible(widget.status_label)
        app.processEvents()
        top = widget.status_label.mapTo(shell.scroll_area.viewport(), QPoint()).y()
        assert top >= 0 and top + widget.status_label.height() <= shell.scroll_area.viewport().height()
        assert shell.scroll_area.verticalScrollBar().maximum() == 0

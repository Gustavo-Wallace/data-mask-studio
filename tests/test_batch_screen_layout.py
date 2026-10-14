"""Batch presentation and lifecycle contracts, without image assertions."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QHeaderView

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.batch import BatchFileStatus, BatchProgress, BatchSummary
from data_mask_studio.gui.batch_worker import BatchProcessingWorker, BatchValidationWorker
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from test_main_navigation import build_window


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    window = build_window(tmp_path)
    window.show()
    window.resize(1280, 820)
    window.set_current_page(window.page_index(window.batch_widget))
    app.processEvents()
    return app, window


def configure(widget, tmp_path, count=2):
    service = widget._profile_service
    profile = service.create("Synthetic profile", [ColumnConfig("Nome", True, "NOME"), ColumnConfig("Cidade")])
    widget.refresh_profiles()
    paths = []
    for index in range(count):
        path = tmp_path / f"synthetic-{index:03}.csv"
        path.write_text("Nome,Cidade\nSynthetic Person,Test City\n", encoding="utf-8")
        paths.append(path)
    widget.add_paths(paths)
    output = tmp_path / "output"
    output.mkdir()
    widget.output_field.setText(str(output))
    return profile, paths, output


def completed_summary(output, count=2, *, errors=0, cancelled=0):
    return BatchSummary(count, count, count - errors - cancelled, 0, errors, cancelled,
                        count, count, 0, 0.1, output)


def previous_result(widget, output, count=2):
    widget.overall_progress.setRange(0, count)
    widget._progress_changed(BatchProgress(count, count, "previous.csv", 12, count, 0))
    widget._processing_completed(completed_summary(output, count))


def test_clearing_list_restores_idle_progress_and_status(page, tmp_path):
    _, window = page
    widget = window.batch_widget
    _, _, output = configure(widget, tmp_path)
    previous_result(widget, output)
    widget.clear_button.click()
    assert not widget.files and widget.file_table.rowCount() == 0
    assert not widget.summary_output.toPlainText() and not widget.open_output_button.isEnabled()
    assert widget.overall_progress.maximum() == 1 and widget.overall_progress.value() == 0
    assert widget.current_progress_label.text() == "Nenhum processamento em andamento."
    assert widget.status_label.text() == "Adicione arquivos CSV para começar."
    assert widget.status_label.property("feedbackState") == "neutral"


def test_new_validation_does_not_retain_previous_processing_progress(page, tmp_path, monkeypatch):
    _, window = page
    widget = window.batch_widget
    _, _, output = configure(widget, tmp_path)
    previous_result(widget, output)
    monkeypatch.setattr(BatchValidationWorker, "start", lambda self: None)
    widget.validate_button.click()
    worker = widget._validation_worker
    assert worker is not None
    assert widget.overall_progress.maximum() == 1 and widget.overall_progress.value() == 0
    assert widget.current_progress_label.text() == ""
    assert widget.status_label.text() == "Validando arquivos..."
    worker.finished.emit()


def test_initial_workflow_stays_visible_compact_and_accessible(page):
    _, window = page
    widget = window.batch_widget
    assert not widget.files and widget.file_table.isVisible()
    assert widget.file_table.maximumHeight() == 160 and widget.file_table.height() <= 160
    assert widget.file_table.accessibleDescription() == "Nenhum arquivo adicionado."
    assert widget.summary_output.maximumHeight() == 80 and widget.summary_output.height() <= 80
    assert widget.summary_output.empty_text == "O resumo do lote aparecerá aqui."
    assert widget.summary_output.isReadOnly()
    for control in (widget.add_files_button, widget.add_folder_button, widget.remove_button,
                    widget.clear_button, widget.profile_combo, widget.output_field,
                    widget.choose_output_button, widget.validate_button, widget.start_button,
                    widget.open_output_button, widget.overall_progress, widget.status_label):
        assert control.isVisible()
    assert isinstance(widget.profile_combo, ScrollSafeComboBox)
    assert widget.add_files_button.property("role") == widget.add_folder_button.property("role") == "primary"
    assert widget.start_button.property("role") == "primary"
    assert all(button.property("role") is None for button in (
        widget.remove_button, widget.clear_button, widget.validate_button, widget.open_output_button,
    ))
    assert widget.add_files_button.isEnabled() and widget.add_folder_button.isEnabled()
    assert not any(button.isEnabled() for button in (
        widget.remove_button, widget.clear_button, widget.validate_button,
        widget.start_button, widget.open_output_button,
    ))
    assert widget.cancel_button.isHidden()
    assert [widget.file_table.horizontalHeaderItem(index).text() for index in range(5)] == [
        "Arquivo", "Caminho", "Status", "Colunas", "Resultado",
    ]


def test_file_and_folder_selection_keep_duplicates_removal_and_clear_rules(page, tmp_path, monkeypatch):
    app, window = page
    widget = window.batch_widget
    first = tmp_path / "synthetic.csv"
    first.write_text("Nome,Cidade\nSynthetic,Test City\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(first), str(first)], ""))
    widget.add_files_button.click()
    assert len(widget.files) == widget.file_table.rowCount() == 1
    assert widget.remove_button.isEnabled() and widget.clear_button.isEnabled()
    folder = tmp_path / "inputs"
    folder.mkdir()
    second = folder / "second.csv"
    second.write_bytes(first.read_bytes())
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder))
    widget.add_folder_button.click()
    widget.add_folder_button.click()
    assert len(widget.files) == widget.file_table.rowCount() == 2
    assert widget.file_table.item(1, 0).toolTip() == widget.file_table.item(1, 1).toolTip() == str(second)
    widget.file_table.selectRow(0)
    widget.file_table.setFocus()
    QTest.keyClick(widget.file_table, Qt.Key.Key_Down)
    assert widget.file_table.currentRow() == 1
    widget.remove_button.click()
    assert len(widget.files) == 1 and widget.files[0].path == first
    widget.clear_button.click()
    app.processEvents()
    assert not widget.files and widget.file_table.maximumHeight() == 160
    assert not widget.remove_button.isEnabled() and not widget.clear_button.isEnabled()


def test_profile_output_and_real_validation_keep_ready_to_run_contract(page, tmp_path, monkeypatch):
    app, window = page
    widget = window.batch_widget
    profile, paths, output = configure(widget, tmp_path)
    assert widget.profile_combo.currentData() == profile.identifier
    assert widget.validate_button.isEnabled() and not widget.start_button.isEnabled()
    widget.output_field.clear()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(output))
    widget.choose_output_button.click()
    assert widget.output_field.text() == str(output)
    widget.validate_button.click()
    worker = widget._validation_worker
    assert worker is not None and worker.wait(5000)
    app.processEvents()
    assert widget._validation_worker is None and widget.start_button.isEnabled()
    assert all(item.status is BatchFileStatus.COMPATIBLE for item in widget.files)
    assert all(item.headers == ("Nome", "Cidade") and item.column_count == 2
               and item.encoding == "utf-8" and item.delimiter == "," for item in widget.files)
    assert widget.status_label.property("feedbackState") == "success"
    widget.profile_combo.setFocus()
    for control in (widget.output_field, widget.choose_output_button, widget.validate_button, widget.start_button):
        QTest.keyClick(app.focusWidget(), Qt.Key.Key_Tab)
        assert control.hasFocus()
    replacement = widget._profile_service.create(
        "Second synthetic profile", [ColumnConfig("Nome", True, "PESSOA"), ColumnConfig("Cidade")],
    )
    widget.refresh_profiles()
    widget.profile_combo.setCurrentIndex(widget.profile_combo.findData(replacement.identifier))
    assert all(item.status is BatchFileStatus.PENDING for item in widget.files)
    assert not widget.start_button.isEnabled()
    assert [item.path for item in widget.files] == paths


def test_populated_views_share_spare_space_and_empty_views_compact_again(page, tmp_path):
    app, window = page
    widget = window.batch_widget
    empty_table = widget.file_table.height()
    empty_summary = widget.summary_output.height()
    _, _, output = configure(widget, tmp_path, 40)
    app.processEvents()
    assert widget.file_table.maximumHeight() == 16_777_215
    assert widget.file_table.height() > empty_table
    assert widget.file_table.verticalScrollBar().maximum() > 0
    previous_result(widget, output, 40)
    app.processEvents()
    filled_table, filled_summary = widget.file_table.height(), widget.summary_output.height()
    assert widget.summary_output.maximumHeight() == 16_777_215
    assert filled_summary > empty_summary
    assert widget.layout().stretch(widget.layout().indexOf(widget.file_table)) == 1
    assert widget.layout().stretch(widget.layout().indexOf(widget.summary_output)) == 1
    window.resize(1280, 1000)
    app.processEvents()
    assert widget.file_table.height() > filled_table and widget.summary_output.height() > filled_summary
    assert window.page_shells[window.page_index(widget)].scroll_area.verticalScrollBar().maximum() == 0
    widget.clear_files()
    app.processEvents()
    assert widget.file_table.maximumHeight() == 160 and widget.summary_output.maximumHeight() == 80


@pytest.mark.parametrize("outcome,state", [("success", "success"), ("partial", "warning"),
                                          ("cancelled", "warning"), ("failure", "error")])
def test_processing_progress_signals_and_terminal_feedback_keep_original_semantics(
    page, tmp_path, monkeypatch, outcome, state,
):
    _, window = page
    widget = window.batch_widget
    _, _, output = configure(widget, tmp_path)
    for item in widget.files:
        item.status = BatchFileStatus.COMPATIBLE
    previous_result(widget, output)
    widget._update_actions()
    monkeypatch.setattr(BatchProcessingWorker, "start", lambda self: None)
    widget.start_button.click()
    worker = widget._processing_worker
    assert worker is not None
    assert widget.overall_progress.maximum() == 2 and widget.overall_progress.value() == 0
    assert not widget.summary_output.toPlainText() and widget.current_progress_label.text() == ""
    assert widget.cancel_button.isVisible() and widget.cancel_button.isEnabled()
    assert not any(control.isEnabled() for control in (
        widget.add_files_button, widget.add_folder_button, widget.clear_button,
        widget.profile_combo, widget.output_field, widget.validate_button, widget.start_button,
    ))
    assert widget.status_label.property("feedbackState") == "neutral"
    worker.progress.emit(BatchProgress(1, 2, "synthetic-000.csv", 10, 1, 0))
    assert widget.overall_progress.value() == 1
    assert widget.current_progress_label.text() == (
        "Arquivo 1 de 2: synthetic-000.csv — 10 registros; 1 concluído(s), 0 erro(s)."
    )
    if outcome == "failure":
        worker.failed.emit(RuntimeError("PRIVATE_SENTINEL"))
        assert widget.status_label.text() == "Falha inesperada no lote."
        assert "PRIVATE_SENTINEL" not in widget.status_label.text()
        assert widget.overall_progress.value() == 1
    else:
        cancelled = int(outcome in ("partial", "cancelled"))
        if outcome == "cancelled":
            widget.cancel_button.click()
            assert worker._cancellation.is_requested() and not widget.cancel_button.isEnabled()
        summary = completed_summary(output, cancelled=cancelled)
        worker.completed.emit(summary)
        assert widget.overall_progress.value() == 2  # Includes cancelled/skipped files, as before.
        assert widget.status_label.text() == "Processamento em lote encerrado."
        assert f"Arquivos concluídos: {summary.completed_files}" in widget.summary_output.toPlainText()
        assert f"Arquivos cancelados ou ignorados: {cancelled}" in widget.summary_output.toPlainText()
        assert widget.open_output_button.isEnabled()
    worker.finished.emit()
    assert widget._processing_worker is None and widget.cancel_button.isHidden()
    assert widget.current_progress_label.text() == ""
    assert widget.status_label.property("feedbackState") == state
    assert widget.add_files_button.isEnabled() and widget.profile_combo.isEnabled()


@pytest.mark.parametrize("size", [(960, 640), (1280, 820), "maximized"])
def test_table_summary_and_footer_remain_accessible_at_supported_sizes(page, tmp_path, size):
    app, window = page
    widget = window.batch_widget
    shell = window.page_shells[window.page_index(widget)]
    if size == "maximized":
        window.showMaximized()
        assert window.isMaximized()
    else:
        window.resize(*size)
    for phase in ("empty", "files", "result"):
        if phase == "files":
            configure(widget, tmp_path, 30)
        elif phase == "result":
            widget.summary_output.setPlainText("Synthetic aggregate result\n" * 100)
        app.processEvents()
        assert widget.file_table.isVisible() and widget.summary_output.isVisible()
        assert widget.file_table.viewport().height() > 0 and widget.summary_output.viewport().height() > 0
        if phase != "empty":
            assert widget.file_table.verticalScrollBar().maximum() > 0
        if phase == "result":
            assert widget.summary_output.verticalScrollBar().maximum() > 0
        assert [widget.file_table.horizontalHeader().sectionResizeMode(index) for index in range(5)] == [
            QHeaderView.ResizeMode.Stretch, QHeaderView.ResizeMode.Stretch,
            QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.ResizeToContents,
            QHeaderView.ResizeMode.Stretch,
        ]
        for control in (widget.start_button, widget.overall_progress, widget.status_label):
            shell.scroll_area.ensureWidgetVisible(control)
            app.processEvents()
            top = control.mapTo(shell.scroll_area.viewport(), QPoint()).y()
            assert 0 <= top <= shell.scroll_area.viewport().height() - control.height()

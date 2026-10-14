"""Batch restoration sizing preserves the existing review and worker contracts."""

import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.batch_restoration import BatchCSVColumn, BatchRestorationStatus
from data_mask_studio.restoration import RepresentationPolicy
from test_main_navigation import build_window
from test_restoration_options_consistency import CODE, seed_vault


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    window = build_window(tmp_path)
    window.show()
    window.resize(1280, 820)
    window.set_current_page(window.page_index(window.batch_restoration_widget))
    app.processEvents()
    return app, window


def populate(widget, tmp_path, count=24):
    paths = []
    for index in range(count):
        path = tmp_path / f"synthetic-{index:02}.csv"
        path.write_text("Name,Kind\nSYNTHETIC,TEST\n", encoding="utf-8")
        paths.append(path)
    widget.add_paths(paths)
    return paths


def review(widget, count=24):
    item = widget.files[0]
    item.status = BatchRestorationStatus.COMPATIBLE
    item.columns = [BatchCSVColumn(index, f"Header {index}", 2, 2, 0) for index in range(count)]
    widget.file_table.selectRow(0)
    widget._refresh_column_table(item)
    return item


def settle_layout(app):
    # The parent resize posts a second layout request for the splitter children.
    app.processEvents()
    app.processEvents()


def test_empty_regions_remain_visible_compact_and_actions_keep_their_roles(page):
    _, window = page
    widget = window.batch_restoration_widget
    for table in (widget.file_table, widget.column_table):
        assert table.isVisible() and table.rowCount() == 0
        assert table.height() == table.minimumHeight() == table.maximumHeight()
        assert table.viewport().height() >= 2 * table.verticalHeader().defaultSectionSize()
    assert widget.file_table.empty_text == "Nenhum arquivo adicionado."
    assert widget.column_table.empty_text == "Selecione um CSV analisado para revisar as colunas."
    assert widget.summary_output.empty_text == "O resumo final aparecerá aqui."
    assert widget.summary_output.isReadOnly()
    assert widget.summary_output.maximumHeight() == widget.summary_output.minimumHeight()
    assert widget.status_label.geometry().top() > widget.summary_output.geometry().bottom()
    assert all(control.isVisible() for control in (
        widget.select_candidates_button, widget.unselect_columns_button, widget.output_field,
        widget.choose_output_button, widget.options_toggle, widget.options_summary,
        widget.analyze_button, widget.start_button, widget.open_output_button,
        widget.current_progress, widget.overall_progress, widget.status_label,
    ))
    assert widget.add_files_button.property("role") is None
    assert widget.add_folder_button.property("role") is None
    assert widget.analyze_button.property("role") == "secondary"
    assert widget.start_button.property("role") == "primary"
    assert widget.open_output_button.property("role") is None
    assert widget.add_files_button.isEnabled() and widget.add_folder_button.isEnabled()
    assert not any(button.isEnabled() for button in (
        widget.remove_button, widget.clear_button, widget.analyze_button, widget.start_button,
        widget.open_output_button,
    ))
    # These existing entry controls are no-ops without a selected CSV.
    widget.select_candidates_button.click()
    widget.unselect_columns_button.click()
    assert widget.column_table.rowCount() == 0
    assert widget.cancel_button.isHidden()
    assert [widget.file_table.horizontalHeaderItem(index).text() for index in range(8)] == [
        "Arquivo", "Tipo", "Codificação", "Status", "Códigos", "No cofre", "Ausentes", "Resultado",
    ]
    assert [widget.column_table.horizontalHeaderItem(index).text() for index in range(4)] == [
        "Restaurar", "Cabeçalho", "Códigos válidos", "No cofre / ausentes",
    ]


def test_files_and_folder_selection_keep_deduplication_keyboard_removal_and_clear(page, tmp_path, monkeypatch):
    app, window = page
    widget = window.batch_restoration_widget
    paths = populate(widget, tmp_path, 3)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(paths[0])], "CSV"))
    widget.add_files_button.click()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    widget.add_folder_button.click()
    assert len(widget.files) == widget.file_table.rowCount() == 3
    assert widget.file_table.item(0, 0).toolTip() == str(paths[0])
    widget.file_table.selectRow(0)
    widget.file_table.setFocus()
    QTest.keyClick(widget.file_table, Qt.Key.Key_Down)
    assert widget.file_table.currentRow() == 1
    widget.remove_button.click()
    assert [item.path for item in widget.files] == [paths[0], paths[2]]
    widget.clear_button.click()
    app.processEvents()
    assert not widget.files and widget.column_table.rowCount() == 0
    assert not widget.summary_output.toPlainText()
    for table in (widget.file_table, widget.column_table):
        assert table.height() == table.maximumHeight() == table.minimumHeight()
    assert not widget.analyze_button.isEnabled() and not widget.start_button.isEnabled()


def test_real_analysis_preserves_incompatible_files_and_independent_column_choices(page, tmp_path, monkeypatch):
    app, window = page
    widget = window.batch_restoration_widget
    seed_vault(window)
    sources = [tmp_path / name for name in ("first.csv", "second.csv", "no-codes.html")]
    for source in sources[:2]:
        source.write_text(f"Name,Kind\n{CODE},SYNTHETIC\n", encoding="utf-8")
    sources[2].write_text("<p>Synthetic text only</p>", encoding="utf-8")
    widget.add_paths(sources)
    widget.analyze_button.click()
    worker = widget._analysis_worker
    assert worker is not None and not widget.options_toggle.isEnabled()
    assert worker.wait(5000)
    app.processEvents()
    assert [item.status for item in widget.files] == [
        BatchRestorationStatus.COMPATIBLE, BatchRestorationStatus.COMPATIBLE,
        BatchRestorationStatus.INCOMPATIBLE,
    ]
    assert widget.file_table.item(2, 7).toolTip() == widget.files[2].result_message
    assert widget.status_label.property("feedbackState") == "warning"
    assert widget.overall_progress.value() == widget.overall_progress.maximum() == 3
    assert widget.current_progress.maximum() == 1 and widget.current_progress.value() == 0
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(output))
    widget.choose_output_button.click()
    assert widget.output_field.text() == str(output) and not widget.start_button.isEnabled()
    for index in (0, 1):
        widget.file_table.selectRow(index)
        app.processEvents()
        assert widget.column_table.rowCount() == 2
        assert not any(column.selected for column in widget.files[index].columns)
        checkbox = widget.column_table.cellWidget(0, 0)
        checkbox.setFocus()
        QTest.keyClick(checkbox, Qt.Key.Key_Space)
        assert checkbox.isChecked() and widget.files[index].columns[0].selected
        assert not widget.column_table.cellWidget(1, 0).isEnabled()
        assert widget.column_table.item(0, 3).text() == "1 / 0"
    assert widget.start_button.isEnabled()
    widget.file_table.selectRow(2)
    app.processEvents()
    assert widget.column_table.rowCount() == 0 and widget.column_table.isVisible()
    widget.file_table.selectRow(0)
    app.processEvents()
    assert widget.column_table.cellWidget(0, 0).isChecked()
    assert [column.selected for column in widget.files[1].columns] == [True, False]
    widget.unselect_columns_button.click()
    assert not widget.start_button.isEnabled()
    widget.select_candidates_button.click()
    assert widget.start_button.isEnabled()


def test_populated_regions_expand_share_space_and_preserve_manual_divider(page, tmp_path):
    app, window = page
    widget = window.batch_restoration_widget
    window.resize(1280, 1000)
    app.processEvents()
    empty_file = widget.file_table.height()
    empty_column = widget.column_table.height()
    empty_summary = widget.summary_output.height()
    populate(widget, tmp_path)
    settle_layout(app)
    assert widget.file_table.maximumHeight() == 16_777_215
    assert widget.file_table.height() > empty_file
    assert widget.column_table.height() == empty_column
    review(widget)
    settle_layout(app)
    assert widget.column_table.maximumHeight() == 16_777_215
    assert widget.column_table.height() > empty_column
    widget.summary_output.setPlainText("Synthetic aggregate report\n" * 60)
    settle_layout(app)
    assert widget.summary_output.maximumHeight() == 16_777_215
    assert widget.summary_output.height() > empty_summary
    for table in (widget.file_table, widget.column_table):
        assert table.height() >= table.minimumHeight()
        assert table.verticalScrollBar().maximum() > 0
    sizes = widget.splitter.sizes()
    widget.splitter.setSizes([sizes[0] + 10, sizes[1] - 10])
    app.processEvents()
    manual = widget.splitter.sizes()
    widget._refresh_column_table(widget.files[0])
    app.processEvents()
    assert widget.splitter.sizes() == manual
    assert not widget.splitter.childrenCollapsible()
    widget.summary_output.clear()
    widget.clear_files()
    app.processEvents()
    assert widget.file_table.height() == empty_file and widget.column_table.height() == empty_column
    assert widget.summary_output.maximumHeight() == widget.summary_output.minimumHeight()


@pytest.mark.parametrize("size", [(960, 640), (1280, 820), "maximized"])
def test_tables_summary_and_footer_remain_accessible_at_representative_sizes(page, tmp_path, size):
    app, window = page
    widget = window.batch_restoration_widget
    shell = window.page_shells[window.page_index(widget)]
    if size == "maximized":
        window.showMaximized()
        assert window.isMaximized()
    else:
        window.resize(*size)
    for phase in ("empty", "files", "review", "result"):
        if phase == "files":
            populate(widget, tmp_path)
        elif phase == "review":
            review(widget)
        elif phase == "result":
            widget.summary_output.setPlainText("Synthetic aggregate report\n" * 100)
        settle_layout(app)
        for table in (widget.file_table, widget.column_table):
            assert table.isVisible() and table.viewport().height() > 0
            assert table.height() >= table.minimumHeight()
        assert widget.summary_output.isVisible() and widget.summary_output.viewport().height() > 0
        assert widget.status_label.geometry().top() > widget.summary_output.geometry().bottom()
        assert widget.column_table.geometry().bottom() < widget._column_panel.height()
        if phase == "result":
            assert widget.file_table.verticalScrollBar().maximum() > 0
            assert widget.column_table.verticalScrollBar().maximum() > 0
            assert widget.summary_output.verticalScrollBar().maximum() > 0
        if size == (1280, 820):
            assert shell.scroll_area.verticalScrollBar().maximum() == 0
        for control in (widget.output_field, widget.start_button, widget.current_progress,
                        widget.overall_progress, widget.status_label):
            shell.scroll_area.ensureWidgetVisible(control)
            app.processEvents()
            top = control.mapTo(shell.scroll_area.viewport(), QPoint()).y()
            assert 0 <= top <= shell.scroll_area.viewport().height() - control.height()


@pytest.mark.parametrize("representation", list(RepresentationPolicy))
def test_real_csv_and_html_restoration_keep_output_content_counters_and_sources(
    page, tmp_path, monkeypatch, representation,
):
    app, window = page
    widget = window.batch_restoration_widget
    seed_vault(window)
    csv_source = tmp_path / "synthetic.csv"
    csv_source.write_text(f"Name,Kind\n{CODE},SYNTHETIC\n", encoding="utf-8")
    html_source = tmp_path / "synthetic.html"
    html_source.write_text(f"<p>{CODE}</p>", encoding="utf-8")
    originals = {source: source.read_bytes() for source in (csv_source, html_source)}
    widget.add_paths(originals)
    widget.analyze_files()
    worker = widget._analysis_worker
    assert worker is not None and worker.wait(5000)
    app.processEvents()
    widget.file_table.selectRow(0)
    widget.select_candidates_button.click()
    output = tmp_path / "output"
    output.mkdir()
    widget.output_field.setText(str(output))
    widget.representation_combo.setCurrentIndex(widget.representation_combo.findData(representation.value))
    assert not widget.options_controls.isVisible() and widget.start_button.isEnabled()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Yes)
    widget.start_button.click()
    worker = widget._processing_worker
    assert worker is not None and widget.cancel_button.isVisible()
    assert worker.wait(5000)
    app.processEvents()
    assert widget._processing_worker is None and not widget.has_running_workers()
    assert all(item.status is BatchRestorationStatus.COMPLETED for item in widget.files)
    expected = "  Synthetic Person  " if representation is RepresentationPolicy.FIRST_ORIGINAL else "Synthetic Person"
    with widget.files[0].output_path.open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.reader(stream)) == [["Name", "Kind"], [expected, "SYNTHETIC"]]
    assert widget.files[1].output_path.read_text(encoding="utf-8-sig") == f"<p>{expected}</p>"
    assert all(source.read_bytes() == content for source, content in originals.items())
    assert widget.overall_progress.maximum() == widget.overall_progress.value() == 2
    assert widget.current_progress.maximum() == 1 and widget.current_progress.value() == 0
    assert widget.current_progress_label.text() == "Nenhum processamento em andamento."
    assert "Concluídos: 2" in widget.summary_output.toPlainText()
    assert "Ocorrências restauradas: 2" in widget.summary_output.toPlainText()
    assert widget.open_output_button.isEnabled() and widget.cancel_button.isHidden()
    assert widget.status_label.property("feedbackState") == "success"

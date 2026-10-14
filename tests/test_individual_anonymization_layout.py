"""Responsive single-CSV workspace without altering its processing contracts."""

import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import AnonymizationResult, ColumnAction, generate_token
from data_mask_studio.app import create_application
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError
from data_mask_studio.gui.anonymization_worker import AnonymizationWorker
from data_mask_studio.normalization import NormalizationRule
from test_composite_gui import create as create_composite
from test_main_navigation import build_window


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-app-data"))
    app = create_application([])
    window = build_window(tmp_path)
    window.show()
    window.resize(1280, 820)
    settle(app)
    return app, window, window.anonymization_widget


def settle(app):
    for _ in range(3):
        app.processEvents()


def load(page, tmp_path, *, columns=3, mixed=False):
    app, _, widget = page
    source = tmp_path / f"synthetic-{columns}.csv"
    headers = ["NOME", "CPF", "IDADE"] + [f"COLUNA_{i}" for i in range(3, columns)]
    rows = [
        ["  Pessoa Sintética  ", "999.999.999-99", "24"] + ["SINTÉTICO"] * (columns - 3),
        ["Outra Pessoa", "888.888.888-88", "30"] + ["TESTE"] * (columns - 3),
    ]
    with source.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([headers, *rows])
    widget.load_csv(str(source))
    if mixed:
        widget._normalization_fields[0].setCurrentIndex(
            widget._normalization_fields[0].findData(NormalizationRule.COLLAPSE_WHITESPACE.value),
        )
        widget._output_name_fields[0].setText("PESSOA")
        widget._action_fields[1].setCurrentIndex(widget._action_fields[1].findData(ColumnAction.MASK.value))
        widget._prefix_fields[1].setText("DOC")
        widget._action_fields[2].setCurrentIndex(widget._action_fields[2].findData(ColumnAction.EXCLUDE.value))
        widget.validate_current_configuration()
        assert widget._configuration_validated
    settle(app)
    return source, headers, rows


def assert_workspace(widget):
    for control in (
        widget.select_button, widget.clear_button, widget.analyze_button,
        widget.file_name_label, widget.path_field, widget.encoding_label,
        widget.delimiter_label, widget.column_count_label, widget.profile_toggle,
        widget.select_all_button, widget.unselect_all_button, widget.selected_count_label,
        widget.config_table, widget.composite_section, widget.composite_section.add_button,
        widget.transfer_controls.checkbox, widget.validate_button, widget.generate_button,
        widget.status_label,
    ):
        assert control.isVisible()
    assert widget.path_field.isReadOnly() and widget.path_field.accessibleName()
    assert widget.generate_button.property("role") == "primary"
    assert widget.validate_button.property("role") != "primary"
    assert widget.config_table.geometry().bottom() < widget.composite_section.geometry().top()
    assert widget.composite_section.geometry().bottom() < widget.transfer_controls.geometry().top()


def assert_reachable(app, area, control):
    area.ensureWidgetVisible(control)
    settle(app)
    top = control.mapTo(area.viewport(), QPoint()).y()
    assert 0 <= top <= area.viewport().height() - control.height()
    if control.width() <= area.viewport().width():
        left = control.mapTo(area.viewport(), QPoint()).x()
        assert 0 <= left <= area.viewport().width() - control.width()


def test_empty_small_and_cleared_tables_keep_sections_visible_without_excess_height(page, tmp_path):
    app, _, widget = page
    table = widget.config_table
    assert_workspace(widget)
    empty_height = table.height()
    assert empty_height == table.minimumHeight() == table.maximumHeight()
    assert table.viewport().height() >= 2 * table.verticalHeader().defaultSectionSize()
    assert table.empty_text == "Selecione um CSV para configurar as colunas."
    assert not widget.profile_controls.isVisible() and not widget.transfer_controls.fields.isVisible()
    load(page, tmp_path)
    assert_workspace(widget)
    assert table.height() <= table.maximumHeight()
    assert table.maximumHeight() - empty_height == table.verticalHeader().defaultSectionSize()
    assert table.verticalScrollBar().maximum() == 0
    widget.clear_selection()
    settle(app)
    assert_workspace(widget)
    assert table.height() == empty_height and table.rowCount() == 0
    assert not widget.generate_button.isEnabled() and not widget.transfer_controls.checkbox.isEnabled()


def test_many_columns_use_spare_space_and_keep_internal_scrolling_after_resize(page, tmp_path):
    app, window, widget = page
    load(page, tmp_path, columns=60)
    table = widget.config_table
    height = table.height()
    assert table.rowCount() == 60 and height > table.minimumHeight()
    assert table.verticalScrollBar().maximum() > 0
    window.resize(1600, 1000)
    settle(app)
    assert table.height() > height and table.verticalScrollBar().maximum() > 0
    assert [table.horizontalHeaderItem(i).text() for i in range(5)] == [
        "Ação", "Cabeçalho", "Cabeçalho de saída", "Prefixo", "Normalização",
    ]
    assert table.verticalHeader().defaultSectionSize() == 30
    load(page, tmp_path, mixed=True)
    assert table.rowCount() == 3 and table.height() <= table.maximumHeight()
    assert table.height() < height
    assert_workspace(widget)


def test_long_metadata_and_progress_do_not_force_page_horizontal_overflow(page, tmp_path):
    app, window, widget = page
    load(page, tmp_path)
    window.resize(960, 640)
    widget._set_processing_state(True)
    widget.processed_count_label.show()
    widget.progress_bar.show()
    widget._processing_progress(0)
    settle(app)
    area = window.page_shells[0].scroll_area
    original_width = widget.minimumSizeHint().width()
    original_scroll = area.horizontalScrollBar().maximum()
    widget.file_name_label.setText("Synthetic_" + "very_long_name_" * 15 + ".csv")
    path = "C:\\Synthetic\\" + "long_directory\\" * 30 + "synthetic.csv"
    widget.path_field.setText(path)
    widget._processing_progress(1_234_567)
    settle(app)
    # Existing action-button minima can require horizontal scroll at high DPI;
    # longer metadata/counters must not increase that minimum or its overflow.
    assert widget.minimumSizeHint().width() <= original_width
    assert area.horizontalScrollBar().maximum() <= original_scroll
    assert widget.path_field.text() == widget.path_field.toolTip() == path
    assert widget.file_name_label.wordWrap() and widget.processed_count_label.wordWrap()
    assert_reachable(app, area, widget.cancel_button)
    assert_reachable(app, area, widget.status_label)
    widget._processing_cancelled()


@pytest.mark.parametrize("size", [(960, 640), (1280, 820), (1600, 1000)])
@pytest.mark.parametrize("state", ["idle", "small", "many", "mixed", "composite", "package", "profiles"])
def test_responsive_workspace_and_optional_controls_remain_reachable(page, tmp_path, size, state):
    app, window, widget = page
    if state != "idle":
        load(page, tmp_path, columns=50 if state == "many" else 3,
             mixed=state in ("mixed", "composite", "package", "profiles"))
    if state in ("composite", "package"):
        create_composite(widget, mask=False)
        widget.validate_current_configuration()
    if state == "package":
        controls = widget.transfer_controls
        controls.set_output_path(tmp_path / "prepared.csv")
        controls.checkbox.setChecked(True)
        assert controls.destination.text() == str(tmp_path / "prepared.dmspackage")
        assert controls.password.echoMode() is QLineEdit.EchoMode.Password
    if state == "profiles":
        widget.profile_toggle.click()
    window.resize(*size)
    settle(app)
    assert_workspace(widget)
    area = window.page_shells[0].scroll_area
    if size != (960, 640) or state != "profiles":
        assert area.horizontalScrollBar().maximum() == 0
    else:
        # Keep the original profile button row usable even with the offscreen
        # plugin's wide fallback glyphs; native Windows captures use Segoe UI.
        assert area.horizontalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAsNeeded
    if size != (960, 640) and state in ("idle", "small", "many", "mixed"):
        assert area.verticalScrollBar().maximum() == 0
    for control in (widget.select_button, widget.profile_toggle, widget.composite_section.add_button,
                    widget.validate_button, widget.generate_button, widget.status_label):
        assert_reachable(app, area, control)
    if state == "package":
        assert controls.fields.isVisible()
        for control in (controls.destination, controls.browse_button, controls.password,
                        controls.confirmation, controls.show_passwords):
            assert_reachable(app, area, control)
        controls.password.setFocus()
        QTest.keyClick(controls.password, Qt.Key.Key_Tab)
        assert controls.confirmation.hasFocus()
    if state == "profiles":
        assert widget.profile_controls.isVisible()
        for control in (widget.profile_combo, widget.apply_profile_button, widget.save_profile_button,
                        widget.update_profile_button, widget.rename_profile_button, widget.delete_profile_button):
            assert_reachable(app, area, control)
    if state == "mixed":
        field = widget._output_name_fields[0]
        field.setFocus()
        QTest.keyClick(field, Qt.Key.Key_End)
        QTest.keyClicks(field, "_FINAL")
        assert widget._column_configs[0].output_name == "PESSOA_FINAL"
        assert not widget._configuration_validated and not widget.generate_button.isEnabled()
    if state in ("composite", "package"):
        assert widget.composite_section.table.isVisible()
        assert widget.composite_section.table.columnWidth(1) == widget.config_table.columnWidth(2)
        assert widget.composite_section.table.columnWidth(3) == widget.config_table.columnWidth(3)


@pytest.mark.parametrize("outcome", ["success", "cancel", "failure"])
def test_processing_and_terminal_controls_remain_reachable_with_original_feedback(page, tmp_path, monkeypatch, outcome):
    app, window, widget = page
    load(page, tmp_path, mixed=True)
    window.resize(960, 640)
    monkeypatch.setattr(AnonymizationWorker, "start", lambda self: None)
    output = tmp_path / "prepared.csv"
    widget._start_processing(output, overwrite=False)
    worker = widget._worker
    assert worker is not None
    worker.progress.emit(123456)
    settle(app)
    assert_workspace(widget)
    assert not widget.config_table.isEnabled() and widget.cancel_button.isVisible()
    area = window.page_shells[0].scroll_area
    assert area.horizontalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert_reachable(app, area, widget.cancel_button)
    assert_reachable(app, area, widget.processed_count_label)
    if outcome == "success":
        worker.completed.emit(AnonymizationResult(output, 2, 0.1))
    elif outcome == "cancel":
        worker.cancelled.emit()
    else:
        worker.failed.emit(CSVAnonymizationError("Falha sintética segura."))
    worker.finished.emit()
    settle(app)
    assert widget._worker is None and widget.config_table.isEnabled()
    assert not widget.cancel_button.isVisible()
    assert widget.output_path_label.isVisible() == (outcome == "success")
    assert widget.progress_bar.isVisible() == (outcome == "success")
    assert widget.status_label.property("feedbackState") == {
        "success": "success", "cancel": "warning", "failure": "error",
    }[outcome]
    for control in (widget.generate_button, widget.status_label):
        assert_reachable(app, area, control)
    if outcome == "success":
        assert_reachable(app, area, widget.output_path_label)
        assert_reachable(app, area, widget.open_folder_button)


def test_real_mixed_anonymization_keeps_normalized_values_tokens_and_exclusion(page, tmp_path):
    app, window, widget = page
    source, _, rows = load(page, tmp_path, mixed=True)
    original = source.read_bytes()
    destination = tmp_path / "prepared.csv"
    widget._start_processing(destination, overwrite=False)
    worker = widget._worker
    assert worker is not None and worker.wait(15000)
    settle(app)
    assert widget._worker is None and widget._last_processing_error is None
    assert source.read_bytes() == original
    with destination.open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.reader(stream)) == [
            ["PESSOA", "CPF"],
            *[[row[0].strip(), generate_token(b"H" * 32, "DOC", row[1])] for row in rows],
        ]
    assert widget._last_output_path == destination
    assert widget.progress_bar.value() == widget.progress_bar.maximum() == 1
    assert widget.processed_count_label.text() == "2 registros processados"
    assert "2 novos mapeamentos" in widget.status_label.text()
    assert widget.generate_button.isEnabled() and widget.open_folder_button.isVisible()
    area = window.page_shells[0].scroll_area
    assert_reachable(app, area, widget.output_path_label)
    assert_reachable(app, area, widget.status_label)

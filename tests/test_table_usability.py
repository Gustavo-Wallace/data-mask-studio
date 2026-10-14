"""Responsive column priorities and opt-in full text; no screenshot assertions."""

import csv
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QHelpEvent, Qt as GuiQt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle, QToolTip

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.batch_restoration import BatchCSVColumn
from data_mask_studio.app import create_application
from data_mask_studio.gui.components.presentation import TruncatedTextToolTipDelegate
from data_mask_studio.gui.visual_tokens import METRICS
from test_main_navigation import build_window


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated"))
    app = create_application([])
    window = build_window(tmp_path)
    long_header = "Synthetic source header with a deliberately long descriptive name " * 8
    source = tmp_path / "source.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([(long_header, "CPF"), ("Synthetic", "123")])
    queued = tmp_path / ("synthetic_customers_export_" * 8 + ".csv")
    queued.write_bytes(source.read_bytes())
    window.anonymization_widget.load_csv(str(source))
    window.restoration_widget.load_csv(str(source))
    window.batch_widget.add_paths([queued])
    window.batch_restoration_widget.add_paths([queued])
    review = window.batch_restoration_widget.files[0]
    review.columns = [BatchCSVColumn(0, long_header, 1, 1, 0), BatchCSVColumn(1, "CPF", 0, 0, 0)]
    window.batch_restoration_widget.file_table.selectRow(0)
    window.batch_restoration_widget._refresh_column_table(review)
    window.show()
    app.processEvents()
    window.resize(1100, 760)
    app.processEvents()
    return app, window, source, queued


def hover_cell(app, table, row, column):
    rect = table.visualRect(table.model().index(row, column)).intersected(table.viewport().rect())
    assert not rect.isEmpty()
    point = rect.center()
    event = QHelpEvent(QEvent.Type.ToolTip, point, table.viewport().mapToGlobal(point))
    app.sendEvent(table.viewport(), event)


@pytest.mark.parametrize("width", [960, 1100, 1600])
def test_batch_filename_path_and_result_share_width_without_a_content_sized_filename(page, width):
    app, window, _, _ = page
    widget = window.batch_widget
    window.set_current_page(window.page_index(widget))
    window.resize(width, 760)
    app.processEvents()
    app.processEvents()
    table = widget.file_table
    header = table.horizontalHeader()
    assert [header.sectionResizeMode(i) for i in range(5)] == [
        QHeaderView.ResizeMode.Stretch, QHeaderView.ResizeMode.Stretch,
        QHeaderView.ResizeMode.ResizeToContents, QHeaderView.ResizeMode.ResizeToContents,
        QHeaderView.ResizeMode.Stretch,
    ]
    filename_width = table.fontMetrics().horizontalAdvance(table.item(0, 0).text())
    assert table.columnWidth(0) < filename_width
    assert header.length() <= table.viewport().width()
    assert all(table.columnWidth(i) > 0 for i in range(5))
    assert table.selectionBehavior() is QAbstractItemView.SelectionBehavior.SelectRows
    assert table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert not table.wordWrap() and not table.isSortingEnabled()
    assert table.verticalHeader().defaultSectionSize() == 30


def test_batch_flexible_columns_grow_when_window_grows_and_compact_columns_stay_stable(page):
    app, window, _, _ = page
    table = window.batch_widget.file_table
    window.set_current_page(window.page_index(window.batch_widget))
    window.resize(960, 760)
    app.processEvents()
    before = [table.columnWidth(i) for i in range(5)]
    window.resize(1600, 760)
    app.processEvents()
    assert all(table.columnWidth(i) > before[i] for i in (0, 1, 4))
    assert [table.columnWidth(i) for i in (2, 3)] == [before[i] for i in (2, 3)]


def test_wide_configuration_bounds_prefix_to_editor_capacity_and_gives_space_back_to_header(page):
    app, window, _, _ = page
    widget = window.anonymization_widget
    window.set_current_page(window.page_index(widget))
    window.resize(1600, 760)
    app.processEvents()
    before_header = widget.config_table.columnWidth(1)
    window.resize(2400, 760)
    app.processEvents()
    app.processEvents()
    table = widget.config_table
    prefix = widget._prefix_fields[0]
    padding = table.fontMetrics().horizontalAdvance("MM")
    frame = 2 * table.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth)
    limit = prefix.fontMetrics().horizontalAdvance("W" * prefix.maxLength()) + padding + frame
    assert table.columnWidth(3) <= limit
    assert table.columnWidth(1) > before_header and table.columnWidth(1) > table.columnWidth(3)
    assert widget._output_name_fields[0].width() >= widget._output_name_fields[0].fontMetrics().horizontalAdvance("Manter original")
    widget._action_fields[0].setCurrentIndex(widget._action_fields[0].findData(ColumnAction.MASK.value))
    prefix.setText("W" * prefix.maxLength())
    assert prefix.width() >= prefix.fontMetrics().horizontalAdvance(prefix.text()) + 2 * METRICS["input_padding"]
    assert widget._column_configs[0].prefix == prefix.text() and prefix.maxLength() == 24
    assert table.verticalHeader().defaultSectionSize() == 30


@pytest.mark.parametrize("font_factor", [1.0, 1.25])
def test_configuration_keeps_control_minima_and_horizontal_scroll_at_narrow_width(page, font_factor):
    app, window, _, _ = page
    widget = window.anonymization_widget
    table = widget.config_table
    window.set_current_page(window.page_index(widget))
    font = table.font()
    font.setPointSizeF(font.pointSizeF() * font_factor)
    table.setFont(font)
    window.resize(960, 640)
    table.refresh_column_layout()
    app.processEvents()
    app.processEvents()
    assert table.horizontalScrollBarPolicy() is Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert table.columnWidth(3) >= table.fontMetrics().horizontalAdvance(widget._prefix_fields[0].placeholderText())
    for column, control in ((0, widget._action_fields[0]), (4, widget._normalization_fields[0])):
        assert table.columnWidth(column) >= control.minimumSizeHint().width()
    assert table.columnWidth(2) >= table.horizontalHeader().sectionSizeHint(2)
    assert table.verticalHeader().defaultSectionSize() == 30


@pytest.mark.parametrize("kind", ["configuration", "restore", "review"])
def test_header_full_value_tooltip_is_opt_in_only_for_text_that_does_not_fit(page, monkeypatch, kind):
    app, window, _, _ = page
    if kind == "configuration":
        widget, table = window.anonymization_widget, window.anonymization_widget.config_table
    elif kind == "restore":
        widget, table = window.restoration_widget, window.restoration_widget.table
    else:
        widget, table = window.batch_restoration_widget, window.batch_restoration_widget.column_table
    window.set_current_page(window.page_index(widget))
    app.processEvents()
    assert isinstance(table.itemDelegateForColumn(1), TruncatedTextToolTipDelegate)
    shown, hidden = [], []
    monkeypatch.setattr(QToolTip, "showText", lambda *args: shown.append(args[1]))
    monkeypatch.setattr(QToolTip, "hideText", lambda: hidden.append(True))
    item = table.item(0, 1)
    raw = item.text()
    item.setToolTip("EXTRA_HIDDEN_SENTINEL")
    hover_cell(app, table, 0, 1)
    assert shown == [GuiQt.convertFromPlainText(raw)]
    assert "EXTRA_HIDDEN_SENTINEL" not in shown[0]
    shown.clear()
    item.setText("ID")
    app.processEvents()
    hover_cell(app, table, 0, 1)
    assert not shown and hidden
    assert table.itemDelegateForColumn(0) is None


def test_header_tooltip_preserves_literal_markup_and_whitespace_without_interpreting_html(page, monkeypatch):
    app, window, _, _ = page
    table = window.anonymization_widget.config_table
    raw = '  Synthetic <img src="not-a-resource"> & <b>literal header</b>  ' * 8
    table.item(0, 1).setText(raw)
    shown = []
    monkeypatch.setattr(QToolTip, "showText", lambda *args: shown.append(args[1]))
    hover_cell(app, table, 0, 1)
    assert shown == [GuiQt.convertFromPlainText(raw)]
    assert "<img" not in shown[0] and "&lt;img" in shown[0]
    assert table.item(0, 1).text() == raw


def test_existing_batch_path_and_result_tooltips_remain_available_without_new_metadata(page):
    _, window, _, queued = page
    result = "Synthetic aggregate result with a long explanatory message " * 8
    for widget in (window.batch_widget, window.batch_restoration_widget):
        widget.files[0].result_message = result
    window.batch_widget._refresh_table()
    window.batch_restoration_widget._refresh_file_table()
    batch = window.batch_widget.file_table
    restored = window.batch_restoration_widget.file_table
    assert batch.item(0, 0).toolTip() == batch.item(0, 1).toolTip() == str(queued)
    assert restored.item(0, 0).toolTip() == str(queued)
    assert batch.item(0, 4).toolTip() == batch.item(0, 4).text() == result
    assert restored.item(0, 7).toolTip() == restored.item(0, 7).text() == result
    assert not batch.item(0, 2).toolTip() and not batch.item(0, 3).toolTip()
    assert all(not restored.item(0, column).toolTip() for column in range(1, 7))
    assert len(window.batch_widget.files) == len(window.batch_restoration_widget.files) == 1


@pytest.mark.parametrize("width", [960, 1600])
def test_restore_tables_keep_compact_checkbox_counters_and_flexible_headers(page, width):
    app, window, _, _ = page
    for widget, table in ((window.restoration_widget, window.restoration_widget.table),
                          (window.batch_restoration_widget, window.batch_restoration_widget.column_table)):
        window.set_current_page(window.page_index(widget))
        window.resize(width, 760)
        app.processEvents()
        header = table.horizontalHeader()
        assert header.sectionResizeMode(0) is QHeaderView.ResizeMode.ResizeToContents
        assert header.sectionResizeMode(1) is QHeaderView.ResizeMode.Stretch
        for column in range(2, table.columnCount()):
            assert header.sectionResizeMode(column) is QHeaderView.ResizeMode.ResizeToContents
        assert table.columnWidth(1) > 0 and table.verticalHeader().defaultSectionSize() == 30
        if widget is window.restoration_widget:
            assert table.selectionMode() is QAbstractItemView.SelectionMode.NoSelection
    files = window.batch_restoration_widget.file_table
    assert files.horizontalHeader().sectionResizeMode(0) is QHeaderView.ResizeMode.Stretch
    assert files.horizontalHeader().sectionResizeMode(7) is QHeaderView.ResizeMode.Stretch
    assert all(files.horizontalHeader().sectionResizeMode(i) is QHeaderView.ResizeMode.ResizeToContents for i in range(1, 7))
    assert not files.wordWrap() and files.selectionBehavior() is QAbstractItemView.SelectionBehavior.SelectRows

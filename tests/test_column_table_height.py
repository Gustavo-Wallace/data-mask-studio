"""Physical table preview uses row metrics, retaining the compact filtered view."""

from PySide6.QtCore import QPoint

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from test_individual_anonymization_layout import page, load, settle, assert_reachable


def assert_preview_minimum(table):
    row_height = table.verticalHeader().defaultSectionSize()
    frame = table.horizontalHeader().height() + 2 * table.frameWidth()
    if table.horizontalScrollBar().isVisible():
        frame += table.horizontalScrollBar().height()
    assert table.minimumHeight() == frame + round(5.25 * row_height)
    assert table.viewport().height() >= round(5.25 * row_height)
    assert table.verticalScrollBar().maximum() > 0


def test_default_preview_reserves_five_rows_and_partial_sixth_with_actions_visible(page, tmp_path):
    app, window, widget = page
    load(page, tmp_path, columns=12)
    table = widget.config_table
    # Laptop-sized page, including the possible horizontal scrollbar overhead.
    window.resize(960, 820)
    settle(app)
    assert_preview_minimum(table)
    ratio = table.viewport().height() / table.verticalHeader().defaultSectionSize()
    assert 5.2 <= ratio <= 5.3
    assert table.rowViewportPosition(5) < table.viewport().height()
    assert table.rowViewportPosition(5) + table.rowHeight(5) > table.viewport().height()
    area = window.page_shells[0].scroll_area
    assert area.verticalScrollBar().maximum() == 0
    for control in (widget.composite_section, widget.validate_button, widget.generate_button, widget.status_label):
        top = control.mapTo(area.viewport(), QPoint()).y()
        assert top >= 0 and top + control.height() <= area.viewport().height()


def test_height_tracks_row_metrics_and_horizontal_scrollbar_changes(page, tmp_path):
    app, window, widget = page
    load(page, tmp_path, columns=12)
    table = widget.config_table
    window.resize(960, 820)
    settle(app)
    assert_preview_minimum(table)
    window.resize(1280, 820)
    settle(app)
    assert_preview_minimum(table)
    table.verticalHeader().setDefaultSectionSize(round(table.verticalHeader().defaultSectionSize() * 1.25))
    widget._update_configuration_layout()
    settle(app)
    assert_preview_minimum(table)


def test_filtering_and_clearing_keep_compact_heights_and_editor_identity(page, tmp_path):
    app, _, widget = page
    load(page, tmp_path, columns=12)
    table = widget.config_table
    editors = list(widget._output_name_fields)
    widget._output_name_fields[0].setText("PERSON")
    for query, count in (("PERSON", 1), ("not-found", 0)):
        widget.column_search_field.setText(query)
        settle(app)
        assert sum(not table.isRowHidden(row) for row in range(table.rowCount())) == count
        assert table.minimumHeight() == table.maximumHeight()
        assert table.verticalScrollBar().maximum() == 0
        assert widget._output_name_fields == editors and widget._output_name_fields[0].text() == "PERSON"
    widget.column_search_field.clear()
    settle(app)
    assert_preview_minimum(table)
    widget.column_action_filter.setCurrentIndex(widget.column_action_filter.findData("mask"))
    settle(app)
    assert table.minimumHeight() == table.maximumHeight()
    widget.clear_selection()
    settle(app)
    assert table.rowCount() == 0 and table.minimumHeight() == table.maximumHeight()


def test_populated_composites_and_actions_remain_reachable_at_minimum_window_size(page, tmp_path):
    app, window, widget = page
    load(page, tmp_path, columns=12)
    composite = CompositeColumnConfig("PERSON", components=(
        CompositeSource(SourceColumnRef("NOME")), CompositeSource(SourceColumnRef("CPF"))))
    widget.composite_section.set_configurations((composite,), notify=True)
    window.resize(960, 640)
    settle(app)
    area = window.page_shells[0].scroll_area
    for control in (widget.composite_section.add_button, widget.validate_button, widget.generate_button):
        assert_reachable(app, area, control)
    assert widget.composite_section.configurations == (composite,)
    assert len(widget._column_configs) == 12

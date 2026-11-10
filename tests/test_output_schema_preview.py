"""The optional schema preview is a projection of the real, value-free plan."""

from copy import deepcopy
from pathlib import Path
import builtins
import sqlite3

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QAbstractItemView, QDialog, QDialogButtonBox

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction, ColumnConfig
from data_mask_studio.csv_tools.models import CSVHeaderReplacement, CSVInspectionResult
from data_mask_studio.csv_tools.source_binding import source_ref_at
from data_mask_studio.gui import anonymization_widget as module
from data_mask_studio.gui.output_schema_dialog import OutputSchemaDialog
from data_mask_studio.gui.styles import apply_widget_palette
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from data_mask_studio.i18n import Translator
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.processing.planner import PlanningError, build_processing_plan
from test_bootstrap_localization import app
from test_main_navigation import build_window


def forbidden(*args, **kwargs):
    pytest.fail("Schema preview must not read data, process, persist or start workers")


@pytest.fixture
def page(app, tmp_path):
    window = build_window(tmp_path)
    widget = window.anonymization_widget
    # Deliberately nonexistent: preview must rely on inspected headers, not read rows.
    widget._show_result(CSVInspectionResult(tmp_path / "headers-only.csv", "utf-8", ",", ["Name", "ID", "Note"]))
    window.show()
    app.processEvents()
    return window, widget


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("composite_action", [ColumnAction.PRESERVE, ColumnAction.MASK])
def test_exact_plan_order_headers_duplicate_sources_exclusions_and_composites(app, tmp_path, locale, composite_action):
    inspection = CSVInspectionResult(tmp_path / "not-read.csv", "utf-8", ",", ["Name", "Name", "column_3"],
                                     (CSVHeaderReplacement(3, "column_3"),))
    configs = [ColumnConfig("Name", action=ColumnAction.MASK, prefix="NAME", output_name="  Full name  "),
               ColumnConfig("Name", output_name="Second name"),
               ColumnConfig("column_3", action=ColumnAction.EXCLUDE, output_name="Full name")]
    composite = CompositeColumnConfig("  Combined  ", "PAIR" if composite_action is ColumnAction.MASK else "",
        (CompositeSource(source_ref_at(inspection, 1)), CompositeSource(source_ref_at(inspection, 2))),
        action=composite_action)
    original = deepcopy(configs)
    plan = build_processing_plan(inspection, configs, (composite,))
    translator = Translator(locale)
    dialog = OutputSchemaDialog(plan, translator=translator)
    assert plan.final_headers == ("Full name", "Second name", "Combined")
    assert [dialog.table.item(row, 2).text() for row in range(3)] == list(plan.final_headers)
    assert [dialog.table.item(row, 0).text() for row in range(4)] == ["1", "2", "3", "—"]
    assert dialog.table.item(3, 2).text() == "—"  # Excluded header is not presented as emitted.
    assert dialog.table.item(3, 3).text() == translator.text("masking.action.exclude")
    assert dialog.table.item(0, 1).text() == "#1: Name (1)"
    assert dialog.table.item(1, 1).text() == "#2: Name (2)"
    assert "#2: Name (2)" in dialog.table.item(2, 1).text()
    assert "#3: " in dialog.table.item(2, 1).text()
    assert "column_3" in dialog.table.item(2, 1).text()
    assert dialog.table.item(2, 3).text() == translator.text(f"masking.action.{composite_action.value}")
    assert dialog.count_label.text() == translator.text("masking.schema.count", count=3)
    assert dialog.table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
    assert not dialog.table.isSortingEnabled()
    assert all(not dialog.table.item(row, col).flags() & Qt.ItemFlag.ItemIsEditable
               for row in range(4) for col in range(4))
    assert configs == original and dialog._plan is plan


def test_all_physical_sources_excluded_can_emit_only_a_composite(app, tmp_path):
    inspection = CSVInspectionResult(tmp_path / "not-read.csv", "utf-8", ",", ["A", "B"])
    configs = [ColumnConfig(header, action=ColumnAction.EXCLUDE) for header in inspection.headers]
    composite = CompositeColumnConfig("Only output", components=tuple(
        CompositeSource(source_ref_at(inspection, index)) for index in (1, 0)))
    plan = build_processing_plan(inspection, configs, (composite,))
    dialog = OutputSchemaDialog(plan, translator=Translator("en"))
    assert dialog.table.rowCount() == 3
    assert [dialog.table.item(row, 0).text() for row in range(3)] == ["1", "—", "—"]
    assert dialog.table.item(0, 2).text() == "Only output"
    assert dialog.table.item(0, 1).text() == "Composite — #2: B + #1: A"
    assert dialog.count_label.text() == "Output columns: 1"


def test_preview_uses_live_unvalidated_configuration_without_side_effects(page, app, monkeypatch):
    window, widget = page
    widget._output_name_fields[0].setText("Person")
    widget._action_fields[1].setCurrentIndex(widget._action_fields[1].findData("mask"))
    widget._prefix_fields[1].setText("ID")
    widget.column_search_field.setText("nothing matches")
    widget.transfer_controls.checkbox.setChecked(True)
    widget.transfer_controls.password.setText("Synthetic password")
    widget.transfer_controls.confirmation.setText("Mismatch is irrelevant to a schema")
    widget._output_name_fields[0].setFocus()
    widget._output_name_fields[0].selectAll()
    expected = widget._build_current_plan()
    state = (deepcopy(widget._column_configs), widget._configuration_validated, widget._configuration_dirty,
             widget.generate_button.isEnabled(), widget.status_label.text(), app.focusWidget(),
             widget._output_name_fields[0].selectedText(), widget.column_search_field.text(),
             widget.transfer_controls.password.text(), widget.transfer_controls.confirmation.text())
    seen = []

    def inspect_dialog(dialog):
        seen.append(dialog._plan)
        assert [dialog.table.item(row, 2).text() for row in range(3)] == ["Person", "ID", "Note"]
        assert "Synthetic password" not in str([dialog.table.item(row, col).text()
                                                 for row in range(3) for col in range(4)])
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(OutputSchemaDialog, "exec", inspect_dialog)
    with monkeypatch.context() as guard:
        guard.setattr(module, "inspect_csv", forbidden)
        guard.setattr(module.AnonymizationWorker, "start", forbidden)
        guard.setattr(module.DetectionWorker, "start", forbidden)
        guard.setattr(widget._key_provider, "get_key", forbidden)
        guard.setattr(widget, "_vault_repository_factory", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        guard.setattr(builtins, "open", forbidden)
        for method in ("open", "read_text", "read_bytes", "write_text", "write_bytes", "stat"):
            guard.setattr(Path, method, forbidden)
        widget._show_output_schema()
    assert seen == [expected]
    assert widget._schema_dialog is None and widget._worker is None and widget._detection_worker is None
    assert state == (widget._column_configs, widget._configuration_validated, widget._configuration_dirty,
                     widget.generate_button.isEnabled(), widget.status_label.text(), app.focusWidget(),
                     widget._output_name_fields[0].selectedText(), widget.column_search_field.text(),
                     widget.transfer_controls.password.text(), widget.transfer_controls.confirmation.text())


@pytest.mark.parametrize("problem", ["collision", "mask_prefix", "all_excluded", "composite_collision",
                                    "component_missing", "unknown_source", "missing_profile_source"])
def test_invalid_or_incomplete_configuration_never_opens_a_final_schema(page, monkeypatch, problem):
    _, widget = page
    if problem == "collision":
        widget._output_name_fields[0].setText("ID")
    elif problem == "mask_prefix":
        widget._action_fields[0].setCurrentIndex(widget._action_fields[0].findData("mask"))
        widget._prefix_fields[0].clear()
    elif problem == "all_excluded":
        for field in widget._action_fields:
            field.setCurrentIndex(field.findData("exclude"))
    elif problem in ("composite_collision", "component_missing"):
        components = tuple(CompositeSource(source_ref_at(widget._inspection_result, index)) for index in (0, 1))
        if problem == "component_missing":
            components = components[:1]
        widget.composite_section.set_configurations((CompositeColumnConfig("ID", components=components),), notify=True)
    elif problem == "unknown_source":
        widget._unreviewed_sources.add(1)
    else:
        widget._profile_missing_headers = ("Missing",)
    with pytest.raises(PlanningError):
        widget._build_current_plan()
    before = deepcopy(widget._column_configs)
    monkeypatch.setattr(OutputSchemaDialog, "exec", forbidden)
    widget.preview_schema_button.click()
    assert widget._schema_dialog is None
    assert widget.status_label.text() and widget.status_label.property("feedbackState") == "error"
    assert widget._column_configs == before


def test_preview_availability_tracks_source_processing_and_analysis(page, monkeypatch):
    _, widget = page
    monkeypatch.setattr(widget, "_build_current_plan", forbidden)
    for setter in (widget._set_processing_state, widget._set_detection_state):
        setter(True)
        assert not widget.preview_schema_button.isEnabled()
        widget._show_output_schema()
        setter(False)
        assert widget.preview_schema_button.isEnabled()
    widget._worker = object()
    try:
        widget._show_output_schema()
    finally:
        widget._worker = None
    widget.clear_selection()
    assert not widget.preview_schema_button.isEnabled()
    widget._show_output_schema()


def test_open_dialog_retranslation_reuses_plan_rows_selection_and_scroll_without_io(page, app, monkeypatch):
    window, widget = page
    plan = widget._build_current_plan()
    dialog = OutputSchemaDialog(plan, widget, translator=Translator("pt-BR"))
    widget._schema_dialog = dialog
    dialog.show()
    dialog.table.selectRow(1)
    dialog.table.setFocus()
    app.processEvents()
    items = [dialog.table.item(row, col) for row in range(3) for col in range(4)]
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    try:
        with monkeypatch.context() as guard:
            guard.setattr(widget, "_build_current_plan", forbidden)
            guard.setattr(module, "inspect_csv", forbidden)
            guard.setattr(Path, "open", forbidden)
            guard.setattr(sqlite3, "connect", forbidden)
            for translator in translators:
                window.retranslate(translator)
                assert dialog._plan is plan and widget._schema_dialog is dialog
                assert items == [dialog.table.item(row, col) for row in range(3) for col in range(4)]
                assert [index.row() for index in dialog.table.selectionModel().selectedRows()] == [1]
                assert dialog.table.hasFocus()
                assert dialog.table.item(0, 3).text() == translator.text("masking.action.preserve")
                assert dialog.count_label.text() == translator.text("masking.schema.count", count=3)
                assert [dialog.table.item(row, 2).text() for row in range(3)] == list(plan.final_headers)
    finally:
        widget._schema_dialog = None
        dialog.close()
        dialog.deleteLater()


def test_real_modal_preview_close_restores_focus_and_does_not_validate_or_start_work(page, app):
    window, widget = page
    field = widget._output_name_fields[0]
    field.setFocus()
    field.selectAll()
    app.processEvents()
    seen = []

    def close_preview():
        dialog = widget._schema_dialog
        seen.append(dialog)
        dialog.buttons.button(QDialogButtonBox.StandardButton.Close).click()

    QTimer.singleShot(0, close_preview)
    widget._show_output_schema()
    app.processEvents()
    assert len(seen) == 1 and widget._schema_dialog is None
    assert window.focusWidget() is field, (app.focusWidget(), app.activeWindow(), window.focusWidget())
    # The offscreen platform does not automatically reactivate the parent.
    window.activateWindow()
    app.processEvents()
    assert field.hasFocus()
    assert not widget._configuration_validated and not widget.generate_button.isEnabled()
    assert widget._worker is None


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_many_columns_scroll_with_literal_headers_and_preserve_view_on_retranslation(app, tmp_path, colors):
    headers = ["<b>Literal header</b>"] + [f"Field {index}" for index in range(1, 40)]
    inspection = CSVInspectionResult(tmp_path / "not-read.csv", "utf-8", ",", headers)
    plan = build_processing_plan(inspection, [ColumnConfig(header) for header in headers])
    dialog = OutputSchemaDialog(plan, translator=Translator("pt-BR"))
    apply_widget_palette(dialog, colors)
    dialog.resize(600, 320)
    dialog.show()
    app.processEvents()
    scroll = dialog.table.verticalScrollBar()
    assert scroll.maximum() > 0
    scroll.setValue(scroll.maximum() // 2)
    position = scroll.value()
    item = dialog.table.item(0, 1)
    assert item.text() == "#1: <b>Literal header</b>"
    assert dialog.table.item(0, 2).text() == headers[0]
    assert dialog.table.palette().color(QPalette.ColorRole.Base) == QColor(colors.base)
    assert not dialog.table.grab().isNull()
    for locale in ("en", "pt-BR"):
        dialog.retranslate(Translator(locale))
        assert scroll.value() == position
        assert dialog.table.item(0, 1) is item
        assert dialog.table.item(0, 2).text() == headers[0]

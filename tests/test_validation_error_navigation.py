"""Explicit failed validation reveals existing field indicators without edits."""

from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtTest import QTest

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction, validate_configuration
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.gui import anonymization_widget as module
from data_mask_studio.i18n import Translator
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.processing.planner import PlanningError
from test_column_search import page, forbidden, visible


def invalid_prefix(widget, row=1):
    field = widget._action_fields[row]
    field.setCurrentIndex(field.findData("mask"))
    widget._prefix_fields[row].setText("!")
    return widget._prefix_fields[row]


def press_validate(widget):
    widget.validate_button.setFocus()
    widget.validate_button.click()


def test_explicit_validation_focuses_prefix_indicator_without_changing_feedback_or_configuration(page):
    app, _, widget = page
    target = invalid_prefix(widget)
    widget.validate_current_configuration()
    feedback = widget.status_label.text(), widget.status_label.property("feedbackState")
    configurations = deepcopy(widget._column_configs)
    results = validate_configuration(widget._column_configs)
    press_validate(widget)
    assert app.focusWidget() is target
    assert widget.config_table.currentRow() == 1 and widget.config_table.currentColumn() == 3
    assert target.property("validationError") is True and target.text() == "!"
    assert widget._column_configs == configurations
    assert validate_configuration(widget._column_configs) == results
    assert (widget.status_label.text(), widget.status_label.property("feedbackState")) == feedback
    assert not widget._configuration_validated and not widget.generate_button.isEnabled()


@pytest.mark.parametrize("kind", ["prefix", "output"])
def test_duplicate_source_headers_are_targeted_by_physical_index_not_text(page, tmp_path, kind):
    app, _, widget = page
    source = tmp_path / "duplicates.csv"
    source.write_text("Nome,CPF,Nome,Email\nA,123,B,synthetic@example.invalid\n", encoding="utf-8")
    widget.load_csv(str(source))
    widget._output_name_fields[0].setText("FIRST_NAME")
    if kind == "prefix":
        widget._output_name_fields[2].setText("SECOND_NAME")
        widget._action_fields[0].setCurrentIndex(widget._action_fields[0].findData("mask"))
        widget._prefix_fields[0].setText("FIRST_NAME")
        target = invalid_prefix(widget, 2)
        column = 3
    else:
        widget._output_name_fields[3].setText("Nome")
        target, column = widget._output_name_fields[2], 2
    configurations = deepcopy(widget._column_configs)
    press_validate(widget)
    assert app.focusWidget() is target and widget.config_table.currentRow() == 2
    assert widget.config_table.currentColumn() == column
    assert target.property("validationError") is True
    assert widget._column_configs == configurations
    assert widget._inspection_result.headers == ["Nome", "CPF", "Nome", "Email"]


@pytest.mark.parametrize("query,action,expected_query,expected_action", [
    ("Nome", "mask", "", "mask"), ("CPF", "preserve", "CPF", "all"),
    ("Nome", "preserve", "", "all"), ("CPF", "mask", "CPF", "mask"),
])
def test_reveal_changes_only_criteria_that_exclude_the_invalid_row(
    page, query, action, expected_query, expected_action,
):
    app, _, widget = page
    target = invalid_prefix(widget)
    widget.column_search_field.setText(query)
    widget.column_action_filter.setCurrentIndex(widget.column_action_filter.findData(action))
    configurations = deepcopy(widget._column_configs)
    press_validate(widget)
    assert app.focusWidget() is target and 1 in visible(widget)
    assert widget.column_search_field.text() == expected_query
    assert widget.column_action_filter.currentData() == expected_action
    assert widget._column_configs == configurations
    assert widget.config_table.rowCount() == 4 and target.property("validationError") is True


def test_typing_filtering_and_implicit_validation_do_not_navigate(page):
    app, _, widget = page
    edited = widget._output_name_fields[1]
    edited.setFocus()
    QTest.keyClicks(edited, "Nome")
    assert app.focusWidget() is edited
    assert widget._output_name_fields[0].property("validationError") is True
    widget.column_search_field.setFocus()
    widget.column_search_field.setText("email")
    widget.column_action_filter.setCurrentIndex(widget.column_action_filter.findData("exclude"))
    widget.validate_current_configuration()
    assert app.focusWidget() is widget.column_search_field
    assert widget.column_search_field.text() == "email" and widget.column_action_filter.currentData() == "exclude"
    assert not widget._configuration_validated


def test_explicit_validation_scrolls_inner_table_and_outer_page_to_a_hidden_late_row(page, tmp_path):
    app, window, widget = page
    source = tmp_path / "many.csv"
    headers = [f"HEADER_{row:02d}" for row in range(40)]
    source.write_text(",".join(headers) + "\n" + ",".join(["SYNTHETIC"] * 40) + "\n", encoding="utf-8")
    widget.load_csv(str(source))
    target = invalid_prefix(widget, 35)
    widget.column_search_field.setText("HEADER_00")
    widget.column_action_filter.setCurrentIndex(widget.column_action_filter.findData("preserve"))
    window.resize(960, 640)
    for _ in range(3):
        app.processEvents()
    press_validate(widget)
    for _ in range(3):
        app.processEvents()
    assert app.focusWidget() is target and not widget.config_table.isRowHidden(35)
    table = widget.config_table
    top = target.mapTo(table.viewport(), QPoint())
    assert 0 <= top.y() and top.y() + target.height() <= table.viewport().height()
    assert 0 <= top.x() and top.x() + target.width() <= table.viewport().width()
    area = window.page_shells[0].scroll_area
    outer = target.mapTo(area.viewport(), QPoint())
    assert 0 <= outer.y() and outer.y() + target.height() <= area.viewport().height()
    assert target.property("validationError") is True


def test_known_composite_prefix_indicator_is_navigable_without_changing_physical_filters(page):
    app, _, widget = page
    configuration = CompositeColumnConfig("PERSON_CONTACT", prefix="!", action=ColumnAction.MASK,
        components=(CompositeSource(SourceColumnRef("Nome")), CompositeSource(SourceColumnRef("EMAIL"))))
    section = widget.composite_section
    section.set_configurations((configuration,), notify=True)
    widget.column_search_field.setText("not-found")
    widget.column_action_filter.setCurrentIndex(widget.column_action_filter.findData("exclude"))
    target = section.table.cellWidget(0, 3)
    press_validate(widget)
    assert app.focusWidget() is target and target.property("validationError") is True
    assert section.configurations == (configuration,)
    assert widget.column_search_field.text() == "not-found" and widget.column_action_filter.currentData() == "exclude"
    assert getattr(section, "_editor_dialog", None) is None


@pytest.mark.parametrize("kind", ["all-excluded", "composite-output", "unclassified"])
def test_errors_without_an_identifiable_field_keep_feedback_and_do_not_guess_targets(page, monkeypatch, kind):
    app, _, widget = page
    if kind == "all-excluded":
        for field in widget._action_fields:
            field.setCurrentIndex(field.findData("exclude"))
    elif kind == "composite-output":
        widget.composite_section.set_configurations((CompositeColumnConfig("Nome", components=(
            CompositeSource(SourceColumnRef("Nome")), CompositeSource(SourceColumnRef("CPF")))),), notify=True)
    else:
        def fail(*args, **kwargs):
            raise PlanningError("Synthetic CPF prefix error: no structured field reference")
        monkeypatch.setattr(widget, "_build_current_plan", fail)
    widget.column_search_field.setText("email")
    widget.column_action_filter.setCurrentIndex(widget.column_action_filter.findData("mask"))
    configurations = deepcopy(widget._column_configs)
    widget.validate_current_configuration()
    feedback = widget.status_label.text()
    press_validate(widget)
    assert app.focusWidget() is widget.validate_button
    assert widget.column_search_field.text() == "email" and widget.column_action_filter.currentData() == "mask"
    assert widget._column_configs == configurations and widget.status_label.text() == feedback
    assert widget.status_label.property("feedbackState") == "error"
    assert not widget._configuration_validated


@pytest.mark.parametrize("attribute", ["_worker", "_detection_worker"])
def test_owned_worker_prevents_navigation_and_filter_reset(page, monkeypatch, attribute):
    app, _, widget = page
    invalid_prefix(widget)
    widget.column_search_field.setText("Nome")
    worker = QThread()
    with monkeypatch.context() as guard:
        guard.setattr(widget, attribute, worker)
        press_validate(widget)
        assert app.focusWidget() is widget.validate_button
        assert widget.column_search_field.text() == "Nome" and getattr(widget, attribute) is worker


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_navigation_reuses_validation_and_preserves_values_widgets_and_output_references(page, monkeypatch, locale):
    app, _, widget = page
    widget.retranslate(Translator(locale))
    target = invalid_prefix(widget)
    expected = validate_configuration(widget._column_configs)
    configurations, fields = deepcopy(widget._column_configs), list(widget._prefix_fields)
    reference = Path("synthetic-published.csv")
    widget._last_output_path = reference
    calls, original = [], module.validate_configuration
    def validate(configs):
        calls.append(True)
        return original(configs)
    with monkeypatch.context() as guard:
        guard.setattr(module, "validate_configuration", validate)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "stat", forbidden)
        guard.setattr(QThread, "start", forbidden)
        press_validate(widget)
    # Same two GUI calls as before: validation + refreshing existing indicators.
    assert calls == [True, True] and app.focusWidget() is target
    assert validate_configuration(widget._column_configs) == expected
    assert widget._column_configs == configurations and widget._prefix_fields == fields
    assert widget._last_output_path == reference
    assert widget.status_label.text() == expected.error_message

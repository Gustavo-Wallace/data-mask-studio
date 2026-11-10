"""Action criteria combine with header search without filtering the actual plan."""

from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.detection.models import ColumnSuggestion, ConfidenceLevel, SuggestedType
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.profiles.models import ProfileColumn
from test_column_search import page, visible, forbidden


def select(widget, action):
    widget.column_action_filter.setCurrentIndex(widget.column_action_filter.findData(action))


@pytest.fixture
def mixed(page):
    _, _, widget = page
    for row, action in enumerate(("mask", "preserve", "exclude", "mask")):
        field = widget._action_fields[row]
        field.setCurrentIndex(field.findData(action))
    for row, name in enumerate(("PERSON_NAME", "DOCUMENTO", "CONTACT", "NOTES")):
        widget._output_name_fields[row].setText(name)
    return page


@pytest.mark.parametrize("action,expected", [("all", [0, 1, 2, 3]), ("mask", [0, 3]),
                                            ("preserve", [1]), ("exclude", [2])])
def test_all_action_categories_use_semantic_codes_and_keep_every_physical_row(mixed, action, expected):
    _, _, widget = mixed
    configurations = deepcopy(widget._column_configs)
    select(widget, action)
    assert visible(widget) == expected
    assert widget.column_search_count.text() == f"Visíveis: {len(expected)}/4"
    assert widget.config_table.rowCount() == len(widget._column_configs) == 4
    assert widget._column_configs == configurations
    assert isinstance(widget.column_action_filter, ScrollSafeComboBox)
    assert [widget.column_action_filter.itemData(i) for i in range(4)] == ["all", "mask", "preserve", "exclude"]


def test_search_and_action_are_combined_with_and_for_original_and_output_headers(mixed):
    _, _, widget = mixed
    for action, query, expected in (("mask", "person", [0]), ("mask", "ObSeRvAçÃo", [3]),
                                    ("preserve", "documento", [1]), ("exclude", "CONTACT", [2]),
                                    ("mask", "CPF", []), ("preserve", "contact", []),
                                    ("all", "CPF", [1])):
        select(widget, action)
        widget.column_search_field.setText(query)
        assert visible(widget) == expected
        assert widget.column_search_count.text() == f"Visíveis: {len(expected)}/4"
        if not expected:
            assert not widget.config_table._has_visible_rows()
            assert widget.config_table.empty_text == "Nenhuma coluna corresponde aos filtros."


def test_action_and_output_edits_refresh_visibility_without_losing_the_edit(mixed):
    _, _, widget = mixed
    action_editors, output_editors = list(widget._action_fields), list(widget._output_name_fields)
    select(widget, "mask")
    widget._action_fields[0].setCurrentIndex(widget._action_fields[0].findData("preserve"))
    assert visible(widget) == [3]
    assert widget._column_configs[0].action is ColumnAction.PRESERVE
    assert widget._column_configs[0].output_name == "PERSON_NAME"
    widget._action_fields[1].setCurrentIndex(widget._action_fields[1].findData("mask"))
    assert visible(widget) == [1, 3]
    widget.column_search_field.setText("documento")
    assert visible(widget) == [1]
    widget._output_name_fields[1].setText("Registro")
    assert visible(widget) == [] and widget._column_configs[1].output_name == "Registro"
    widget.column_search_field.clear()
    select(widget, "all")
    assert visible(widget) == [0, 1, 2, 3]
    assert widget._action_fields == action_editors and widget._output_name_fields == output_editors
    assert all(a is b for a, b in zip(widget._action_fields, action_editors))


def test_bulk_buttons_affect_all_rows_even_when_the_combined_filter_matches_nothing(mixed):
    _, _, widget = mixed
    outputs = [field.text() for field in widget._output_name_fields]
    widget.column_search_field.setText("CPF")
    select(widget, "mask")
    assert visible(widget) == [] and widget.select_all_button.isEnabled()
    widget.select_all_button.click()
    assert all(config.action is ColumnAction.MASK for config in widget._column_configs)
    assert visible(widget) == [1] and widget.selected_count_label.text() == "4 de 4 colunas para mascarar"
    select(widget, "preserve")
    assert visible(widget) == [] and widget.unselect_all_button.isEnabled()
    widget.unselect_all_button.click()
    assert all(config.action is ColumnAction.PRESERVE for config in widget._column_configs)
    assert visible(widget) == [1] and widget.selected_count_label.text() == "0 de 4 colunas para mascarar"
    assert [field.text() for field in widget._output_name_fields] == outputs
    assert widget.column_search_field.text() == "CPF" and widget.column_action_filter.currentData() == "preserve"


@pytest.mark.parametrize("valid", [True, False], ids=["valid-plan", "validation-error"])
def test_filter_changes_preserve_configuration_plan_composites_validation_and_selection(mixed, monkeypatch, valid):
    app, _, widget = mixed
    composite = CompositeColumnConfig("PersonDocument", components=(
        CompositeSource(SourceColumnRef("Nome")), CompositeSource(SourceColumnRef("CPF"))))
    widget.composite_section.set_configurations((composite,), notify=True)
    if not valid:
        widget._output_name_fields[1].setText("PERSON_NAME")
    widget.validate_current_configuration()
    assert widget._configuration_validated is valid
    before_plan = widget._build_current_plan() if valid else None
    configurations = deepcopy(widget._column_configs)
    editors = tuple(tuple(getattr(widget, name)) for name in (
        "_action_fields", "_output_name_fields", "_prefix_fields", "_normalization_fields"))
    flags = (widget._configuration_validated, widget._configuration_dirty, widget.generate_button.isEnabled())
    feedback = widget.status_label.text(), widget.status_label.property("feedbackState")
    indicators = [(field.property("validationError"), field.toolTip()) for field in widget._output_name_fields]
    widget.config_table.setCurrentCell(1, 2)
    current = widget.config_table.currentIndex()
    widget.column_action_filter.setFocus()
    widget._output_name_fields[1].setSelection(0, 4)
    selection = widget._output_name_fields[1].selectedText()
    with monkeypatch.context() as guard:
        for name in ("_configuration_changed", "_refresh_validation_indicators", "validate_current_configuration",
                     "_build_current_plan", "_update_transfer_controls"):
            guard.setattr(widget, name, forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "stat", forbidden)
        guard.setattr(QThread, "start", forbidden)
        for action in ("mask", "preserve", "exclude", "all"):
            select(widget, action)
            assert widget._column_configs == configurations
            assert (widget._configuration_validated, widget._configuration_dirty, widget.generate_button.isEnabled()) == flags
            assert (widget.status_label.text(), widget.status_label.property("feedbackState")) == feedback
            assert [(field.property("validationError"), field.toolTip()) for field in widget._output_name_fields] == indicators
            assert widget._output_name_fields[1].selectedText() == selection
            assert widget.config_table.currentIndex() == current
    assert tuple(tuple(getattr(widget, name)) for name in (
        "_action_fields", "_output_name_fields", "_prefix_fields", "_normalization_fields")) == editors
    assert widget.composite_section.configurations == (composite,)
    if valid:
        assert widget._build_current_plan() == before_plan
        assert len(before_plan.physical_columns) == 4 and len(before_plan.outputs) == 4
    assert app.focusWidget() is widget.column_action_filter


def test_clearing_search_keeps_action_criterion_and_csv_lifecycle_resets_both(mixed, tmp_path):
    app, _, widget = mixed
    select(widget, "exclude")
    widget.column_search_field.setText("contact")
    assert visible(widget) == [2]
    clear = widget.column_search_field.findChild(QAbstractButton)
    app.processEvents()
    QTest.mouseClick(clear, Qt.MouseButton.LeftButton)
    assert not widget.column_search_field.text() and widget.column_action_filter.currentData() == "exclude"
    assert visible(widget) == [2]
    for set_busy in (widget._set_detection_state, widget._set_processing_state):
        set_busy(True)
        assert not widget.column_action_filter.isEnabled()
        assert widget.column_action_filter.currentData() == "exclude"
        set_busy(False)
        assert widget.column_action_filter.isEnabled()
    source = tmp_path / "new.csv"
    source.write_text("Different\nSynthetic\n", encoding="utf-8")
    widget.load_csv(str(source))
    assert widget.column_action_filter.currentData() == "all" and not widget.column_search_field.text()
    assert visible(widget) == [0] and widget.column_search_count.text() == "Visíveis: 1/1"
    widget.clear_selection()
    assert widget.column_action_filter.currentData() == "all" and not widget.column_action_filter.isEnabled()
    assert widget.column_search_count.text() == "Visíveis: 0/0"


def test_blocked_profile_and_detection_updates_refresh_action_filter(mixed):
    _, _, widget = mixed
    select(widget, "mask")
    widget._suggestions = (ColumnSuggestion("Nome", SuggestedType.COMMON_TEXT, False, "KEEP",
        NormalizationRule.EXACT, ConfidenceLevel.HIGH, "Synthetic explanation", 1, 1),)
    widget._apply_detection_rows((0,))
    assert visible(widget) == [3] and widget._column_configs[0].action is ColumnAction.PRESERVE
    profiles = tuple(ProfileColumn(config.header, config.prefix, config.normalization_rule,
                                   action=ColumnAction.MASK if row == 2 else ColumnAction.PRESERVE,
                                   output_name=config.output_name)
                     for row, config in enumerate(widget._column_configs))
    widget._apply_profile_configurations(profiles)
    assert visible(widget) == [2] and widget.column_action_filter.currentData() == "mask"


def test_localized_choices_and_keyboard_selection_preserve_codes_and_active_filter(mixed, monkeypatch):
    app, _, widget = mixed
    widget.column_action_filter.setFocus()
    QTest.keyClick(widget.column_action_filter, Qt.Key.Key_Down)
    assert widget.column_action_filter.currentData() == "mask" and visible(widget) == [0, 3]
    select(widget, "exclude")
    widget.column_search_field.setText("contact")
    before = deepcopy(widget._column_configs)
    translators = [Translator(locale) for locale in ("en", "pt-BR")]
    expected = [["All", "Mask", "Preserve", "Exclude"], ["Todas", "Mascarar", "Preservar", "Excluir"]]
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", forbidden)
        guard.setattr(widget, "_apply_column_filter", forbidden)
        guard.setattr(widget, "_configuration_changed", forbidden)
        for translator, labels in zip(translators, expected):
            widget.retranslate(translator)
            assert [widget.column_action_filter.itemText(i) for i in range(4)] == labels
            assert [widget.column_action_filter.itemData(i) for i in range(4)] == ["all", "mask", "preserve", "exclude"]
            assert widget.column_action_filter.accessibleName() == translator.text("masking.search.action_filter")
            assert widget.column_action_filter.currentData() == "exclude"
            assert widget.column_search_field.text() == "contact" and visible(widget) == [2]
            assert widget.column_search_count.text() == translator.text("masking.search.count", visible=1, total=4)
    assert widget._column_configs == before and app.focusWidget() is widget.column_action_filter

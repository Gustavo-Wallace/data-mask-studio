"""Search only hides physical table rows, never configuration or plan entries."""

from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.app import create_application
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.profiles.models import ProfileColumn
from test_main_navigation import build_window


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    window = build_window(tmp_path)
    window.resize(1280, 820)
    window.show()
    widget = window.anonymization_widget
    source = tmp_path / "synthetic.csv"
    source.write_text("Nome,CPF,EMAIL,Observação\nPessoa Sintética,123,synthetic@example.invalid,Teste\n",
                      encoding="utf-8")
    widget.load_csv(str(source))
    app.processEvents()
    return app, window, widget


def visible(widget):
    return [row for row in range(widget.config_table.rowCount()) if not widget.config_table.isRowHidden(row)]


def forbidden(*args, **kwargs):
    pytest.fail("Searching must not validate, process, persist or access files")


@pytest.mark.parametrize("query,expected", [
    ("nOmE", [0]), ("CPF", [1]), ("documento", [1]), ("CONTATO", [2]),
    ("observação", [3]), ("observacao", []), ("does-not-exist", []), ("", [0, 1, 2, 3]),
])
def test_search_matches_original_or_current_output_header_case_insensitively(page, query, expected):
    _, _, widget = page
    widget._output_name_fields[1].setText("DOCUMENTO")
    widget._output_name_fields[2].setText("Contato")
    configs = deepcopy(widget._column_configs)
    widget.column_search_field.setText(query)
    assert visible(widget) == expected
    assert widget.config_table.rowCount() == len(widget._column_configs) == 4
    assert widget.column_search_count.text() == f"Visíveis: {len(expected)}/4"
    assert widget._column_configs == configs
    if not expected:
        assert not widget.config_table._has_visible_rows()
        assert widget.config_table.empty_text == "Nenhuma coluna corresponde aos filtros."


def test_live_typing_and_native_clear_action_reveal_existing_rows_without_rebuilding(page):
    app, _, widget = page
    editors = list(widget._output_name_fields)
    widget.column_search_field.setFocus()
    QTest.keyClicks(widget.column_search_field, "cpf")
    assert visible(widget) == [1] and widget.column_search_field.hasFocus()
    clear = widget.column_search_field.findChild(QAbstractButton)
    assert clear is not None and clear.isVisible()
    assert clear.toolTip() == clear.accessibleName() == "Limpar busca"
    QTest.mouseClick(clear, Qt.MouseButton.LeftButton)
    assert not widget.column_search_field.text() and visible(widget) == [0, 1, 2, 3]
    assert widget._output_name_fields == editors
    assert all(a is b for a, b in zip(widget._output_name_fields, editors))
    assert app.focusWidget() is widget.column_search_field


def test_duplicate_headers_keep_physical_identity_and_all_matches(page, tmp_path):
    _, _, widget = page
    source = tmp_path / "duplicates.csv"
    source.write_text("Nome,CPF,Nome,Email\nA,123,B,synthetic@example.invalid\n", encoding="utf-8")
    widget.load_csv(str(source))
    widget._output_name_fields[0].setText("NOME_A")
    widget._output_name_fields[2].setText("NOME_B")
    items = [widget.config_table.item(row, 1) for row in range(4)]
    before = widget._build_current_plan()
    widget.column_search_field.setText("NOME")
    assert visible(widget) == [0, 2]
    assert [widget.config_table.item(row, 1) for row in range(4)] == items
    widget.column_search_field.clear()
    assert visible(widget) == [0, 1, 2, 3]
    assert widget._build_current_plan() == before
    assert [column.input_index for column in before.physical_columns] == [0, 1, 2, 3]


def test_filtering_preserves_plan_composites_edits_selection_and_successful_validation(page, monkeypatch):
    app, _, widget = page
    widget._action_fields[0].setCurrentIndex(widget._action_fields[0].findData("mask"))
    widget._prefix_fields[0].setText("PERSON")
    widget._action_fields[1].setCurrentIndex(widget._action_fields[1].findData("exclude"))
    widget._output_name_fields[2].setText("CONTACT")
    widget._normalization_fields[3].setCurrentIndex(
        widget._normalization_fields[3].findData(NormalizationRule.COLLAPSE_WHITESPACE.value))
    composite = CompositeColumnConfig("PersonContact", components=(
        CompositeSource(SourceColumnRef("Nome")), CompositeSource(SourceColumnRef("EMAIL"))))
    widget.composite_section.set_configurations((composite,), notify=True)
    widget.validate_current_configuration()
    assert widget._configuration_validated and widget.generate_button.isEnabled()
    before = widget._build_current_plan()
    configs, editors = deepcopy(widget._column_configs), list(widget._output_name_fields)
    status = (widget.status_label.text(), widget.status_label.property("feedbackState"))
    flags = widget._configuration_validated, widget._configuration_dirty
    widget.config_table.setCurrentCell(2, 2)
    current = widget.config_table.currentIndex()
    widget.column_search_field.setFocus()
    editors[2].setSelection(0, 4)
    assert app.focusWidget() is widget.column_search_field
    with monkeypatch.context() as guard:
        for name in ("_configuration_changed", "_refresh_validation_indicators", "validate_current_configuration",
                     "_build_current_plan", "_update_transfer_controls"):
            guard.setattr(widget, name, forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "stat", forbidden)
        guard.setattr(QThread, "start", forbidden)
        for query in ("cpf", "not-found", "Nome", ""):
            widget.column_search_field.setText(query)
            assert widget._column_configs == configs and widget._output_name_fields == editors
            assert editors[2].selectedText() == "CONT"
            assert widget.config_table.currentIndex() == current
            assert (widget._configuration_validated, widget._configuration_dirty) == flags
            assert (widget.status_label.text(), widget.status_label.property("feedbackState")) == status
            assert widget.generate_button.isEnabled()
    assert widget._build_current_plan() == before
    assert widget.composite_section.configurations == (composite,)
    assert app.focusWidget() is widget.column_search_field


def test_search_preserves_invalid_fields_and_error_feedback(page):
    _, _, widget = page
    widget._output_name_fields[1].setText("Nome")  # Existing collision validation.
    widget.validate_current_configuration()
    assert not widget._configuration_validated and not widget.generate_button.isEnabled()
    fields = [(field.property("validationError"), field.toolTip()) for field in widget._output_name_fields]
    status = widget.status_label.text(), widget.status_label.property("feedbackState")
    for query in ("email", "not-found", ""):
        widget.column_search_field.setText(query)
        assert [(field.property("validationError"), field.toolTip()) for field in widget._output_name_fields] == fields
        assert (widget.status_label.text(), widget.status_label.property("feedbackState")) == status
        assert not widget.generate_button.isEnabled()


def test_bulk_actions_still_affect_hidden_columns_and_do_not_change_search(page):
    _, _, widget = page
    widget.column_search_field.setText("CPF")
    assert visible(widget) == [1]
    widget.select_all_button.click()
    assert all(config.action is ColumnAction.MASK for config in widget._column_configs)
    assert all(field.currentData() == "mask" for field in widget._action_fields)
    assert widget.selected_count_label.text() == "4 de 4 colunas para mascarar"
    widget.unselect_all_button.click()
    assert all(config.action is ColumnAction.PRESERVE for config in widget._column_configs)
    assert all(field.currentData() == "preserve" for field in widget._action_fields)
    assert widget.column_search_field.text() == "CPF" and visible(widget) == [1]


def test_output_edits_and_blocked_profile_updates_refresh_current_search(page):
    _, _, widget = page
    widget.column_search_field.setText("documento")
    assert visible(widget) == []
    widget._output_name_fields[1].setText("DOCUMENTO")
    assert visible(widget) == [1]
    widget._output_name_fields[1].setText("Registro")
    assert visible(widget) == []
    columns = tuple(ProfileColumn(config.header, config.prefix, config.normalization_rule,
                                  action=config.action, output_name="Documento" if row == 2 else "")
                    for row, config in enumerate(widget._column_configs))
    widget._apply_profile_configurations(columns)
    assert visible(widget) == [2] and widget.column_search_field.text() == "documento"
    assert len(widget._column_configs) == 4


def test_selection_lifecycle_resets_query_and_busy_states_disable_only_search_interaction(page, tmp_path):
    _, _, widget = page
    widget.column_search_field.setText("CPF")
    for set_busy in (widget._set_detection_state, widget._set_processing_state):
        set_busy(True)
        assert not widget.column_search_field.isEnabled()
        assert widget.column_search_field.text() == "CPF" and visible(widget) == [1]
        set_busy(False)
        assert widget.column_search_field.isEnabled()
    source = tmp_path / "new.csv"
    source.write_text("Different\nSynthetic\n", encoding="utf-8")
    widget.load_csv(str(source))
    assert not widget.column_search_field.text() and visible(widget) == [0]
    assert widget.column_search_count.text() == "Visíveis: 1/1"
    widget.clear_selection()
    assert not widget.column_search_field.isEnabled() and not widget.column_search_field.text()
    assert widget.column_search_count.text() == "Visíveis: 0/0"
    assert widget.config_table.empty_text == "Selecione um CSV para configurar as colunas."


def test_retranslation_preserves_query_and_no_match_state_without_io_or_revalidation(page, monkeypatch):
    app, _, widget = page
    widget.column_search_field.setText("not-found")
    widget.column_search_field.setFocus()
    configs, editors = deepcopy(widget._column_configs), list(widget._output_name_fields)
    translators = [Translator(locale) for locale in ("en", "pt-BR")]
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", forbidden)
        guard.setattr(widget, "_configuration_changed", forbidden)
        guard.setattr(QThread, "start", forbidden)
        for translator in translators:
            widget.retranslate(translator)
            assert widget.column_search_field.text() == "not-found" and visible(widget) == []
            assert widget.column_search_count.text() == translator.text("masking.search.count", visible=0, total=4)
            assert widget.config_table.empty_text == translator.text("masking.search.empty")
            clear = widget.column_search_field.findChild(QAbstractButton)
            assert clear.toolTip() == clear.accessibleName() == translator.text("masking.search.clear")
            assert widget._column_configs == configs and widget._output_name_fields == editors
    assert app.focusWidget() is widget.column_search_field

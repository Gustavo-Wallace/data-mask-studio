"""Contextual help is localized metadata, not configuration or validation."""

from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtGui import QHelpEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QToolTip, QWidget

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.anonymization.anonymizer import _normalize_with_fallback
from data_mask_studio.anonymization.prefix_rules import validate_prefix
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.gui.composite_dialog import CompositeDialog
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NORMALIZATION_OPTIONS, NormalizationRule, normalize_value
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from test_column_search import page, forbidden


def composite():
    return CompositeColumnConfig("PERSON_CONTACT", prefix="PAIR", action=ColumnAction.MASK,
        components=(CompositeSource(SourceColumnRef("Nome"), NormalizationRule.PERSON_NAME),
                    CompositeSource(SourceColumnRef("EMAIL"), NormalizationRule.EMAIL)))


def assert_option_help(combo, translator):
    assert combo.count() == len(NORMALIZATION_OPTIONS)
    for index in range(combo.count()):
        rule = NormalizationRule(combo.itemData(index))
        expected = translator.text(f"masking.normalization.help.{rule.value}")
        assert combo.itemData(index, Qt.ItemDataRole.ToolTipRole) == expected
        assert combo.itemData(index, Qt.ItemDataRole.AccessibleDescriptionRole) == expected
        assert expected


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_prefix_and_all_normalization_choices_have_contextual_and_accessible_help(page, locale):
    _, _, widget = page
    translator = Translator(locale)
    widget.retranslate(translator)
    prefix_help = translator.text("masking.column.prefix_tooltip")
    assert "2–24" in prefix_help and "A–Z" in prefix_help and "0–9" in prefix_help
    assert widget.config_table.horizontalHeaderItem(3).toolTip() == prefix_help
    normalization_help = translator.text("masking.column.normalization_tooltip")
    assert widget.config_table.horizontalHeaderItem(4).toolTip() == normalization_help
    for prefix, normalization in zip(widget._prefix_fields, widget._normalization_fields):
        assert prefix.toolTip() == prefix.accessibleDescription() == prefix_help
        assert not prefix.isEnabled()  # Help must not enable a Preserve prefix.
        assert normalization.toolTip() == normalization.accessibleDescription() == normalization_help
        assert_option_help(normalization, translator)


def test_documented_limits_match_existing_rules_without_changing_them():
    # Help describes the implemented checks, not stronger validity guarantees.
    assert validate_prefix("A") and validate_prefix("A" * 25)
    assert validate_prefix("aB") and validate_prefix("AB-CD")
    assert validate_prefix("AB_12") is None
    assert normalize_value("000.000.000-00", NormalizationRule.CPF) == "00000000000"
    assert normalize_value("00.000.000/0000-00", NormalizationRule.CNPJ) == "00000000000000"
    assert normalize_value("  JOÃO  da\tSilva  ", NormalizationRule.PERSON_NAME) == "joao da silva"
    assert _normalize_with_fallback("synthetic-invalid-cpf", NormalizationRule.CPF) == (
        "synthetic-invalid-cpf", NormalizationRule.EXACT, True)
    for locale in ("pt-BR", "en"):
        translator = Translator(locale)
        assert "11" in translator.text("masking.normalization.help.cpf")
        assert "14" in translator.text("masking.normalization.help.cnpj")
        assert "8" in translator.text("masking.normalization.help.phone")
        assert "15" in translator.text("masking.normalization.help.phone")
    english = Translator("en")
    assert "does not verify check digits" in english.text("masking.normalization.help.cpf")
    assert "does not verify check digits" in english.text("masking.normalization.help.cnpj")
    assert "without changing the chosen action" in english.text("masking.column.normalization_tooltip")
    assert "may not restore the exact original spelling" in english.text("masking.column.normalization_tooltip")


def test_retranslation_of_help_preserves_widgets_plan_filters_focus_workers_and_validation(page, monkeypatch):
    app, _, widget = page
    action = widget._action_fields[0]
    action.setCurrentIndex(action.findData("mask"))
    prefix, normalization = widget._prefix_fields[0], widget._normalization_fields[0]
    prefix.setText("PERSON")
    normalization.setCurrentIndex(normalization.findData(NormalizationRule.PERSON_NAME.value))
    configuration = composite()
    widget.composite_section.set_configurations((configuration,), notify=True)
    widget.validate_current_configuration()
    assert widget._configuration_validated and widget.generate_button.isEnabled()
    plan, columns = widget._build_current_plan(), deepcopy(widget._column_configs)
    widget.column_search_field.setText("Nome")
    widget.column_action_filter.setCurrentIndex(widget.column_action_filter.findData("mask"))
    prefix.setFocus()
    prefix.setSelection(0, 3)
    controls = widget.findChildren(QWidget)
    value, selection = prefix.text(), prefix.selectedText()
    current, visible = normalization.currentData(), [widget.config_table.isRowHidden(row) for row in range(4)]
    flags = widget._configuration_validated, widget._configuration_dirty, widget.generate_button.isEnabled()
    references = widget._worker, widget._detection_worker
    changed, prefix_changes = QSignalSpy(normalization.currentIndexChanged), QSignalSpy(prefix.textChanged)
    section_changes = QSignalSpy(widget.composite_section.changed)
    translators = [Translator(locale) for locale in ("en", "pt-BR", "en")]
    worker = QThread()  # An owned, not-yet-retired reference must also remain untouched.
    with monkeypatch.context() as guard:
        guard.setattr(widget, "_worker", worker)
        for name in ("_configuration_changed", "_refresh_validation_indicators", "validate_current_configuration",
                     "_build_current_plan", "_apply_column_filter"):
            guard.setattr(widget, name, forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(Path, "stat", forbidden)
        guard.setattr(QThread, "start", forbidden)
        for translator in translators:
            widget.retranslate(translator)
            assert widget._worker is worker and widget._detection_worker is references[1]
            assert widget.findChildren(QWidget) == controls
            assert prefix.text() == value and prefix.selectedText() == selection and prefix.hasFocus()
            assert normalization.currentData() == current
            assert [widget.config_table.isRowHidden(row) for row in range(4)] == visible
            assert widget._column_configs == columns and widget.composite_section.configurations == (configuration,)
            assert (widget._configuration_validated, widget._configuration_dirty, widget.generate_button.isEnabled()) == flags
            assert_option_help(normalization, translator)
    assert changed.count() == prefix_changes.count() == section_changes.count() == 0
    assert widget._build_current_plan() == plan and app.focusWidget() is prefix


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_composite_help_explains_output_and_keeps_existing_validation_error_priority(page, locale):
    _, _, widget = page
    section = widget.composite_section
    translator = Translator(locale)
    section.retranslate(translator)
    context = translator.text("masking.composite.context_help")
    assert section.toolTip() == section.accessibleDescription() == section.add_button.accessibleDescription() == context
    assert "JSON" in context
    assert section.add_button.toolTip() == translator.text("masking.composite.add_tooltip")
    configuration = composite()
    section.set_configurations((configuration,))
    field, action = section.table.cellWidget(0, 3), section.table.cellWidget(0, 0)
    assert section.table.horizontalHeaderItem(3).toolTip() == translator.text("masking.composite.prefix_hint")
    field.setText("!")
    error = field.toolTip()
    assert error and error != translator.text("masking.composite.prefix_hint")
    assert field.property("validationError") is True
    changes = QSignalSpy(section.changed)
    section.retranslate(Translator("en" if locale == "pt-BR" else "pt-BR"))
    assert field.toolTip() == field.accessibleDescription() == error
    assert field.text() == "!" and field.property("validationError") is True
    assert changes.count() == 0
    field.setText("PAIR")
    assert field.toolTip() == section._translator.text("masking.composite.prefix_hint")
    assert field.property("validationError") is False and changes.count() == 1
    action.setCurrentIndex(action.findData(ColumnAction.PRESERVE))
    assert not field.isEnabled() and not field.text() and changes.count() == 2


def test_existing_composite_editor_shares_option_help_without_changing_sources_or_choices(page):
    app, _, widget = page
    configuration = composite()
    dialog = CompositeDialog(widget._inspection_result, lambda _: None, configuration, widget)
    dialog.show()
    app.processEvents()
    row = dialog.rows[0]
    row.normalization.setFocus()
    source, rule = row.source.currentData(), row.normalization.currentData()
    controls = dialog.findChildren(QWidget)
    changes = QSignalSpy(row.normalization.currentIndexChanged)
    for locale in ("en", "pt-BR", "en"):
        translator = Translator(locale)
        dialog.retranslate(translator)
        assert row.source.currentData() == source and row.normalization.currentData() == rule
        assert dialog.findChildren(QWidget) == controls and row.normalization.hasFocus()
        assert row.normalization.toolTip() == row.normalization.accessibleDescription() == translator.text(
            "masking.composite.normalization_tooltip")
        assert_option_help(row.normalization, translator)
        # Existing visible order/excluded-source explanation is not duplicated or changed.
        assert dialog.help_label.text() == translator.text("composite.editor.help")
    assert changes.count() == 0
    QTest.keyClick(row.normalization, Qt.Key.Key_Up)
    assert changes.count() == 1 and row.component().reference == source
    assert row.component().normalization_rule is NormalizationRule.COLLAPSE_WHITESPACE
    dialog.reject()


def test_native_tooltip_display_is_plain_localized_help_without_configuration_changes(page):
    app, _, widget = page
    control = widget._normalization_fields[0]
    before = deepcopy(widget._column_configs)
    expected = control.toolTip()
    event = QHelpEvent(QHelpEvent.Type.ToolTip, QPoint(3, 3), control.mapToGlobal(QPoint(3, 3)))
    try:
        QApplication.sendEvent(control, event)
        app.processEvents()
        assert QToolTip.isVisible() and QToolTip.text() == expected
        assert widget._column_configs == before and widget._worker is widget._detection_worker is None
    finally:
        QToolTip.hideText()

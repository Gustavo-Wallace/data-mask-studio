"""Individual masking tables translate presentation, never configured values."""

from dataclasses import replace

import pytest
from PySide6.QtWidgets import QDialog, QLineEdit, QPushButton

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.app import create_application
from data_mask_studio.csv_tools.csv_inspector import inspect_csv
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.gui.action_styles import ACTION_INDICATOR_STYLES
from data_mask_studio.gui.column_configuration_table import ColumnConfigurationTable
from data_mask_studio.gui.composite_dialog import CompositeDialog
from data_mask_studio.gui.composite_section import CompositeSection
from data_mask_studio.i18n import Translator
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource


@pytest.mark.parametrize(
    "locale,headers,empty",
    [
        ("pt-BR", ["Ação", "Cabeçalho", "Cabeçalho de saída", "Prefixo", "Normalização"],
         "Selecione um CSV para configurar as colunas."),
        ("en", ["Action", "Header", "Output header", "Prefix", "Normalization"],
         "Select a CSV to configure its columns."),
    ],
)
def test_configuration_headers_and_empty_state_follow_explicit_translator(locale, headers, empty):
    create_application([])
    table = ColumnConfigurationTable(translator=Translator(locale))
    assert [table.horizontalHeaderItem(index).text() for index in range(5)] == headers
    assert table.empty_text == table.accessibleDescription() == empty
    assert table.rowCount() == 0


def configuration():
    return CompositeColumnConfig(
        "USER HEADER", "CORR",
        (
            CompositeSource(SourceColumnRef("NOME", occurrence=1), NormalizationRule.PERSON_NAME),
            CompositeSource(SourceColumnRef("column_2", is_synthetic=True), NormalizationRule.DIGITS_ONLY),
        ),
        action=ColumnAction.MASK,
    )


@pytest.mark.parametrize("initial_locale", ["pt-BR", "en"])
def test_composite_retranslation_preserves_widgets_values_ids_and_signals(initial_locale):
    app = create_application([])
    section = CompositeSection(lambda: None, lambda _: None, translator=Translator(initial_locale))
    original = configuration()
    section.set_configurations((original,))
    action = section.table.cellWidget(0, 0)
    prefix = section.table.cellWidget(0, 3)
    buttons = section.table.cellWidget(0, 4).findChildren(QPushButton)
    changes, action_changes, prefix_changes = [], [], []
    section.changed.connect(lambda: changes.append(True))
    action.currentIndexChanged.connect(action_changes.append)
    prefix.textChanged.connect(prefix_changes.append)
    section.show()
    prefix.setFocus()
    app.processEvents()
    for locale in ("en", "pt-BR", "en"):
        section.retranslate(Translator(locale))
        app.processEvents()
        assert section.configurations == (original,)
        assert section.table.cellWidget(0, 0) is action
        assert section.table.cellWidget(0, 3) is prefix
        assert ColumnAction(action.currentData()) is ColumnAction.MASK
        assert prefix.text() == "CORR" and prefix.isEnabled()
        assert prefix.hasFocus()
        assert action.styleSheet() == ACTION_INDICATOR_STYLES[ColumnAction.MASK]
        assert [action.itemData(index) for index in range(action.count())] == [
            ColumnAction.PRESERVE, ColumnAction.MASK,
        ]
        assert not changes and not action_changes and not prefix_changes
    assert section.title() == "Composite columns"
    assert [action.itemText(index) for index in range(action.count())] == ["Preserve", "Mask"]
    assert [button.text() for button in buttons] == ["Edit", "Delete"]
    assert section.table.item(0, 1).text() == "USER HEADER"
    assert section.table.item(0, 2).text() == "NOME (2) + column_2 (generated)"
    assert "Person name" in section.table.item(0, 2).toolTip()
    assert "Digits only" in section.table.item(0, 2).toolTip()
    assert action.accessibleName() == "Action for composite column USER HEADER"
    assert prefix.accessibleName() == "Prefix for composite column USER HEADER"
    assert prefix.placeholderText() == "Available when masking"
    action.setCurrentIndex(action.findData(ColumnAction.PRESERVE))
    assert section.configurations == (replace(original, action=ColumnAction.PRESERVE, prefix=""),)
    assert not prefix.isEnabled() and prefix.text() == ""
    assert len(changes) == len(action_changes) == 1 and not prefix_changes


def test_composite_owned_labels_translate_but_freeform_validation_errors_do_not():
    create_application([])
    section = CompositeSection(lambda: None, lambda _: None, translator=Translator("en"))
    assert section.title() == "Composite columns"
    assert section.empty_label.text() == "No composite columns configured."
    assert section.add_button.text() == "+ Add"
    section.set_configurations((replace(configuration(), prefix="invalid prefix"),))
    prefix = section.table.cellWidget(0, 3)
    previous_error = prefix.toolTip()
    previous_style = prefix.styleSheet()
    assert previous_error and previous_style
    section.retranslate(Translator("pt-BR"))
    assert prefix.toolTip() == previous_error and prefix.styleSheet() == previous_style
    prefix.setText("CORR")
    assert not prefix.styleSheet()
    section.retranslate(Translator("en"))
    assert prefix.toolTip() == "Use the prefix without a hyphen, for example CORR."


def test_composite_editor_receives_main_section_translator(tmp_path, monkeypatch):
    create_application([])
    source = tmp_path / "synthetic.csv"
    source.write_text("NOME,CPF\nSynthetic,123\n", encoding="utf-8")
    inspection = inspect_csv(source)
    section = CompositeSection(lambda: inspection, lambda _: None, translator=Translator("en"))
    observed = []

    def reject(dialog):
        observed.append((dialog.windowTitle(), dialog.rows[0].normalization.itemText(0)))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(CompositeDialog, "exec", reject)
    section.add_button.click()
    assert observed == [("Composite column", "Exact value")]
    assert section.configurations == ()


def test_translated_sizing_keeps_owned_cell_and_horizontal_scroll():
    app = create_application([])
    table = ColumnConfigurationTable()
    table.setRowCount(1)
    field = QLineEdit("UNCHANGED PREFIX")
    field.setMaxLength(24)
    table.setCellWidget(0, 3, field)
    table.resize(320, 200)
    table.show()
    app.processEvents()
    table.retranslate(Translator("en"))
    app.processEvents()
    assert table.cellWidget(0, 3) is field and field.text() == "UNCHANGED PREFIX"
    assert table.columnWidth(3) >= table.fontMetrics().horizontalAdvance("Available when masking")
    assert table.columnWidth(2) >= table.fontMetrics().horizontalAdvance("Keep original")
    assert table.horizontalScrollBar().maximum() > 0

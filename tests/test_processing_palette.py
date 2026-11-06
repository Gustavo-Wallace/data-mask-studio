"""Processing presentation remains independent of semantic choices and I/O."""
from copy import deepcopy
from pathlib import Path
import sqlite3

import pytest
from PySide6.QtCore import QThread, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QLineEdit, QVBoxLayout, QWidget

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.csv_tools.csv_anonymizer import anonymize_csv
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.gui import anonymization_widget as mask_module
from data_mask_studio.gui.action_styles import set_action_indicator, set_validation_indicator
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.styles import apply_widget_palette, application_stylesheet
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from data_mask_studio.restoration import RestorationConfiguration, SelectedColumn, restore_csv
from data_mask_studio.vault import VaultCipher, VaultRepository
from test_bootstrap_localization import app
from test_localization_integration import worker_references
from test_navigation_localization import build_localized_window
from test_theme_palette import contrast


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("action,surface,border", [
    (ColumnAction.PRESERVE, "action_preserve", "neutral_indicator"),
    (ColumnAction.MASK, "action_mask", "accent"),
    (ColumnAction.EXCLUDE, "danger_surface", "danger_border"),
])
@pytest.mark.parametrize("state", ["normal", "focus", "hover", "disabled"])
def test_action_states_use_semantic_colors_and_literal_choices(app, colors, locale, action, surface, border, state):
    root = QWidget()
    layout = QVBoxLayout(root)
    combo = ScrollSafeComboBox()
    translator = Translator(locale)
    for value in ColumnAction:
        combo.addItem(translator.text(f"masking.action.{value.value}"), value.value)
    combo.setCurrentIndex(combo.findData(action.value))
    set_action_indicator(combo, action)
    layout.addWidget(combo)
    outside = QLineEdit("Synthetic")
    layout.addWidget(outside)
    apply_widget_palette(root, colors)
    root.show()
    app.processEvents()
    outside.setFocus()
    combo.setEnabled(state != "disabled")
    if state == "focus":
        combo.setFocus()
        assert combo.hasFocus()
    elif state == "hover":
        QTest.mouseMove(combo, combo.rect().center())
    app.processEvents()
    group = QPalette.ColorGroup.Disabled if state == "disabled" else QPalette.ColorGroup.Active
    foreground = colors.disabled_text if state == "disabled" else colors.input_text
    assert combo.palette().color(group, QPalette.ColorRole.Base) == QColor(getattr(colors, surface))
    assert combo.palette().color(group, QPalette.ColorRole.Text) == QColor(foreground)
    assert combo.grab().toImage().pixelColor(0, combo.height() // 2) == QColor(getattr(colors, border))
    assert combo.currentData() == action.value and combo.currentText() == translator.text(f"masking.action.{action.value}")
    assert combo.property("columnAction") == action.value and not combo.styleSheet()
    assert contrast(colors.input_text, getattr(colors, surface)) >= 4.5
    if state == "focus":
        changes = QSignalSpy(combo.currentIndexChanged)
        combo.showPopup()
        app.processEvents()
        assert combo.view().isVisible()
        palette = combo.view().palette()
        assert palette.color(QPalette.ColorRole.Base) == QColor(colors.base)
        assert palette.color(QPalette.ColorRole.Highlight) == QColor(colors.selection)
        assert palette.color(QPalette.ColorRole.HighlightedText) == QColor(colors.selection_text)
        assert contrast(colors.selection_text, colors.selection) >= 4.5
        combo.hidePopup()
        assert combo.currentData() == action.value and changes.count() == 0


def configure(window, source):
    mask = window.anonymization_widget
    mask.load_csv(str(source))
    mask._action_fields[0].setCurrentIndex(mask._action_fields[0].findData("mask"))
    mask._prefix_fields[0].setText("NAME")
    mask._action_fields[2].setCurrentIndex(mask._action_fields[2].findData("exclude"))
    components = tuple(CompositeSource(SourceColumnRef(h)) for h in ("Name", "Document"))
    mask.composite_section.set_configurations((
        CompositeColumnConfig("Joined original", components=components),
        CompositeColumnConfig("Joined token", "PAIR", components, action=ColumnAction.MASK),
    ))
    return mask


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_palette_updates_do_not_revalidate_rebuild_or_change_processing_state(app, tmp_path, monkeypatch, locale):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_theme("light").error is None
    assert service.save_language(locale).error is None
    window = build_localized_window(tmp_path, Translator(locale), service)
    source = tmp_path / "synthetic.csv"
    source.write_text("Name,Document,Note\nSynthetic Person,123,Test only\n", encoding="utf-8")
    mask = configure(window, source)
    mask._prefix_fields[0].setText("INVALID-prefix")
    mask._output_name_fields[0].setText("Document")
    composite_prefix = mask.composite_section.table.cellWidget(1, 3)
    composite_prefix.setText("INVALID-prefix")
    mask.transfer_controls.checkbox.setChecked(True)
    mask.transfer_controls.password.setText("Synthetic password")
    mask.transfer_controls.confirmation.setText("Synthetic password")
    window.show()
    app.processEvents()
    mask._prefix_fields[0].setFocus()
    mask._prefix_fields[0].selectAll()
    controls = [*mask._action_fields, *mask._output_name_fields, *mask._prefix_fields,
                *(mask.composite_section.table.cellWidget(r, c) for r in (0, 1) for c in (0, 3)),
                mask.transfer_controls.password, mask.transfer_controls.confirmation]
    fields = [(c, c.text(), c.echoMode(), c.selectedText()) for c in controls if isinstance(c, QLineEdit)]
    properties = [(c, c.property("columnAction"), c.property("validationError"), c.isEnabled(), c.toolTip()) for c in controls]
    assert mask._prefix_fields[0].property("validationError") is True
    assert mask._output_name_fields[0].property("validationError") is True
    assert composite_prefix.property("validationError") is True
    models = deepcopy(mask._column_configs), mask.composite_section.configurations
    pages, widgets = window.page_widgets, set(window.findChildren(QWidget))
    focus, global_state = app.focusWidget(), (app.palette(), app.styleSheet(), app.style())
    before = service.path.read_bytes()
    changes = QSignalSpy(mask.composite_section.changed)
    stopped = QThread()
    def forbidden(*args, **kwargs):
        pytest.fail("Palette updates must not access files/services or refresh validation")
    with monkeypatch.context() as guard:
        guard.setattr(mask, "_worker", stopped)
        workers = worker_references(window)
        for name in ("load", "save_theme", "save_language", "resolve_language"):
            guard.setattr(PreferencesService, name, forbidden)
        for name in ("open", "read_bytes", "read_text", "write_bytes", "write_text", "stat", "resolve", "mkdir"):
            guard.setattr(Path, name, forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        guard.setattr(QThread, "start", forbidden)
        guard.setattr(mask_module, "validate_configuration", forbidden)
        guard.setattr(mask, "_build_current_plan", forbidden)
        guard.setattr(mask.composite_section, "_refresh_prefix", forbidden)
        guard.setattr(window, "retranslate", forbidden)
        for colors in (LIGHT_PALETTE, DARK_PALETTE, LIGHT_PALETTE, DARK_PALETTE):
            apply_widget_palette(window, colors)
            app.processEvents()
            assert window.page_widgets == pages and set(window.findChildren(QWidget)) == widgets
            assert all((c.text(), c.echoMode(), c.selectedText()) == (text, echo, selected) for c, text, echo, selected in fields)
            assert all((c.property("columnAction"), c.property("validationError"), c.isEnabled(), c.toolTip()) == (action, error, enabled, tooltip)
                       for c, action, error, enabled, tooltip in properties)
            assert (mask._column_configs, mask.composite_section.configurations) == models
            assert not mask.generate_button.isEnabled() and changes.count() == 0
            assert worker_references(window) == workers and app.focusWidget() is focus
            assert (app.palette(), app.styleSheet(), app.style()) == global_state
    assert service.path.read_bytes() == before
    mask._prefix_fields[0].setText("NAME")
    mask._output_name_fields[0].setText("Person")
    composite_prefix.setText("PAIR")
    assert mask._prefix_fields[0].property("validationError") is False
    assert mask._output_name_fields[0].property("validationError") is False
    assert composite_prefix.property("validationError") is False


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_validation_border_is_semantic_and_preserves_field_state(app, colors):
    field = QLineEdit("Literal value")
    apply_widget_palette(field, colors)
    field.show()
    field.setFocus()
    set_validation_indicator(field, True)
    assert field.grab().toImage().pixelColor(0, field.height() // 2) == QColor(colors.validation_border)
    assert field.text() == "Literal value" and field.property("validationError") is True
    set_validation_indicator(field, False)
    assert field.property("validationError") is False and field.styleSheet() == application_stylesheet(colors)
    assert field.text() == "Literal value"
    assert DARK_PALETTE.validation_border == "#b42318"
    assert contrast(LIGHT_PALETTE.validation_border, LIGHT_PALETTE.base) >= 3


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_three_restoration_summaries_follow_palette_without_changing_choices(app, tmp_path, locale):
    window = build_localized_window(tmp_path, Translator(locale))
    controls = tuple(page for page in (window.restoration_widget, window.html_restoration_widget, window.batch_restoration_widget))
    texts = [page.options_summary.text() for page in controls]
    choices = [(p.missing_policy_combo.currentData(), p.representation_combo.currentData(), p.options_toggle.isChecked()) for p in controls]
    for colors in (LIGHT_PALETTE, DARK_PALETTE, LIGHT_PALETTE):
        apply_widget_palette(window, colors)
        for page, text, choice in zip(controls, texts, choices, strict=True):
            label = page.options_summary
            assert not label.styleSheet() and label.property("textRole") == "description"
            assert label.palette().color(QPalette.ColorRole.WindowText) == QColor(colors.muted_text)
            assert label.font().pixelSize() == METRICS["description_font_size"]
            assert label.text() == text
            assert (page.missing_policy_combo.currentData(), page.representation_combo.currentData(), page.options_toggle.isChecked()) == choice


def test_identical_scalar_composite_masking_and_restoration_bytes_after_styling(app, tmp_path):
    window = build_localized_window(tmp_path)
    source = tmp_path / "source.csv"
    source.write_text("Name,Document,Note\nSynthetic Person,123,Test only\n", encoding="utf-8")
    mask = configure(window, source)
    original_plan = mask._build_current_plan()
    outputs, restored, counts = [], [], []
    for number, colors in enumerate((DARK_PALETTE, LIGHT_PALETTE)):
        apply_widget_palette(window, colors)
        plan = mask._build_current_plan()
        assert plan == original_plan and plan.requires_masking
        repository = VaultRepository(tmp_path / f"vault-{number}.db", VaultCipher(b"V" * 32))
        destination = tmp_path / f"masked-{number}.csv"
        result = anonymize_csv(source, destination, encoding="utf-8", delimiter=",", secret_key=b"H" * 32,
                               processing_plan=plan, vault_repository=repository)
        outputs.append(destination.read_bytes())
        config = RestorationConfiguration(destination, "utf-8-sig", ",", plan.final_headers,
            tuple(SelectedColumn(i, header) for i, header in enumerate(plan.final_headers)))
        restored_path = tmp_path / f"restored-{number}.csv"
        restoration = restore_csv(config, restored_path, repository.as_read_only())
        restored.append(restored_path.read_bytes())
        counts.append((result.records_processed, restoration.rows_processed, restoration.restored_codes))
    assert outputs[0] == outputs[1] and restored[0] == restored[1]
    assert counts[0] == counts[1] == (1, 1, 2)

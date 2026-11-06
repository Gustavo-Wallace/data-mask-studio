"""Explicit widget scopes, not saved theme activation or runtime policy."""

from pathlib import Path
import sqlite3

import pytest
from PySide6.QtCore import QItemSelectionModel, QLocale, QRect, Qt, QThread, QTranslator
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QLineEdit, QMessageBox, QPushButton,
    QStyle, QStyleOptionButton, QVBoxLayout, QWidget,
)

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.batch import BatchFileStatus
from data_mask_studio.gui import styles
from data_mask_studio.gui.components import empty_state_table, empty_state_text
from data_mask_studio.gui.components.presentation import set_button_role, set_feedback_state
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService
from test_bootstrap_localization import app
from test_localization_integration import labels, worker_references
from test_navigation_localization import build_localized_window
from test_theme_palette import contrast


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_repeated_scope_rendering_preserves_real_window_state_without_io(app, tmp_path, monkeypatch, locale):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_theme("system").error is None
    assert service.save_language(locale).error is None
    window = build_localized_window(tmp_path, Translator(locale), service)
    source = tmp_path / "synthetic.csv"
    source.write_text("Name,Note\nSynthetic,Only test data\n", encoding="utf-8")
    mask = window.anonymization_widget
    mask.load_csv(str(source))
    mask._action_fields[0].setCurrentIndex(mask._action_fields[0].findData("mask"))
    mask._prefix_fields[0].setText("NAME")
    mask.transfer_controls.checkbox.setChecked(True)
    mask.transfer_controls.password.setText("Synthetic password")
    mask.transfer_controls.confirmation.setText("Synthetic password")
    batch = window.batch_widget
    batch.add_paths([source])
    published = tmp_path / "published" / "synthetic_masked.csv"
    published.parent.mkdir()
    published.write_bytes(b"NAME\nNAME-SYNTHETIC\n")
    item = batch.files[0]
    item.status, item.output_path, item.result_message = BatchFileStatus.COMPLETED, published, "Literal output"
    batch._output_directory = published.parent
    batch.open_output_button.setEnabled(True)
    batch._refresh_table()
    table = batch.file_table
    selection = table.selectionModel()
    selection.select(table.model().index(0, 0),
                     QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows)
    assert [index.row() for index in selection.selectedRows()] == [0]
    window.restoration_widget.progress_bar.setValue(57)
    window.restoration_widget._set_status("Literal service detail", is_error=True)
    window.consultant_widget.codes_input.setPlainText("\n".join(["SYNTHETIC"] * 30))
    window.consultant_widget.results_output.setPlainText("Literal result\n" * 50)
    window.show()
    window.set_current_page(4)
    app.processEvents()
    editor = window.consultant_widget.codes_input
    editor.verticalScrollBar().setValue(editor.verticalScrollBar().maximum())
    editor.setFocus()
    fields = window.findChildren(QLineEdit)
    mask.transfer_controls.password.setSelection(1, 4)
    values = [(field.text(), field.echoMode(), field.selectedText(), field.cursorPosition(), field.isEnabled())
              for field in fields]
    texts = [(control, control.text()) for control, _ in labels(window)]
    pages, shells = window.page_widgets, tuple(window.page_shells)
    widgets = set(window.findChildren(QWidget))
    all_buttons = window.findChildren(QPushButton)
    enabled = [(button.isEnabled(), button.isChecked()) for button in all_buttons]
    scrolls = [(shell.scroll_area.horizontalScrollBar(), shell.scroll_area.verticalScrollBar()) for shell in shells]
    bars = [bar for pair in scrolls for bar in pair] + [editor.verticalScrollBar(),
        window.consultant_widget.results_output.verticalScrollBar()]
    positions = [bar.value() for bar in bars]
    focus, translator = app.focusWidget(), window._translator
    qt_translators, locale_name = app.findChildren(QTranslator), QLocale().name()
    global_state = app.palette(), app.styleSheet(), app.style()
    preferences, output = service.path.read_bytes(), published.read_bytes()
    navigation = QSignalSpy(window.navigation.current_changed)
    saved = QSignalSpy(window.settings_widget.language_saved)
    stopped_worker = QThread()

    def forbidden(*args, **kwargs):
        pytest.fail("Palette application must be presentation-only")

    with monkeypatch.context() as guard:
        guard.setattr(mask, "_worker", stopped_worker)
        workers = worker_references(window)
        for name in ("load", "save_theme", "save_language", "resolve_language"):
            guard.setattr(PreferencesService, name, forbidden)
        for name in ("open", "read_bytes", "read_text", "stat", "resolve", "mkdir", "write_bytes", "write_text"):
            guard.setattr(Path, name, forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        guard.setattr(QThread, "start", forbidden)
        guard.setattr(window, "retranslate", forbidden)
        guard.setattr(window, "_vault_repository_factory", forbidden)
        guard.setattr(mask, "validate_current_configuration", forbidden)
        guard.setattr(batch, "refresh_profiles", forbidden)
        guard.setattr(window._key_provider, "get_key", forbidden)
        for colors in (LIGHT_PALETTE, DARK_PALETTE, LIGHT_PALETTE, LIGHT_PALETTE, DARK_PALETTE):
            styles.apply_widget_palette(window, colors)
            app.processEvents()
            assert styles.widget_palette(window.navigation) is colors
            assert window.page_widgets == pages and tuple(window.page_shells) == shells
            assert set(window.findChildren(QWidget)) == widgets
            assert [(f.text(), f.echoMode(), f.selectedText(), f.cursorPosition(), f.isEnabled()) for f in fields] == values
            assert [(b.isEnabled(), b.isChecked()) for b in all_buttons] == enabled
            assert all(control.text() == text for control, text in texts)
            assert [bar.value() for bar in bars] == positions
            assert app.focusWidget() is focus and window.current_page_index() == 4
            assert table.selectionModel() is selection
            assert [index.row() for index in selection.selectedRows()] == [0]
            assert batch.files[0] is item and item.status is BatchFileStatus.COMPLETED
            assert item.output_path == published and item.result_message == "Literal output"
            assert batch._output_directory == published.parent and batch.open_output_button.isEnabled()
            assert worker_references(window) == workers
            assert window.restoration_widget.progress_bar.value() == 57
            assert window.restoration_widget.status_label.text() == "Literal service detail"
            assert window.restoration_widget.status_label.property("feedbackState") == "error"
            assert window._translator is translator and app.findChildren(QTranslator) == qt_translators
            assert QLocale().name() == locale_name and navigation.count() == saved.count() == 0
            assert global_state == (app.palette(), app.styleSheet(), app.style())
    assert preferences == service.path.read_bytes() and output == published.read_bytes()
    window.navigation.buttons[0].click()
    assert window.current_page_index() == 0 and navigation.count() == 1


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_shell_paints_owned_surfaces_and_preserves_navigation_semantics(app, tmp_path, colors):
    window = build_localized_window(tmp_path)
    window.show()
    app.processEvents()
    styles.apply_widget_palette(window, colors)
    app.processEvents()
    nav = window.navigation
    assert nav.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    image = nav.grab().toImage()
    assert image.pixelColor(2, 450) == QColor(colors.navigation_surface)
    assert image.pixelColor(nav.width() - 1, 450) == QColor(colors.navigation_border)
    assert window.page_shells[0].grab().toImage().pixelColor(2, 450) == QColor(colors.window)
    changes = QSignalSpy(nav.current_changed)
    nav.set_page_enabled(1, False)
    nav.buttons[0].setFocus()
    QTest.keyClick(nav.buttons[0], Qt.Key.Key_Down)
    assert nav.current_index() == window.current_page_index() == 2
    assert nav.buttons[2].isChecked() and nav.buttons[2].hasFocus() and changes.count() == 1
    nav.buttons[1].click()
    assert changes.count() == 1
    nav.set_page_enabled(1, True)
    nav.buttons[1].click()
    assert window.current_page_index() == 1 and changes.count() == 2
    # Exercise Qt's real hover/press states without changing selection semantics.
    QTest.mouseMove(nav.buttons[3], nav.buttons[3].rect().center())
    QTest.mousePress(nav.buttons[3], Qt.MouseButton.LeftButton)
    assert nav.buttons[3].isDown()
    QTest.mouseRelease(nav.buttons[3], Qt.MouseButton.LeftButton)
    assert window.current_page_index() == 3 and changes.count() == 3


@pytest.mark.parametrize("kind", ["table", "plain", "rich"])
@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_empty_state_painter_uses_nearest_palette_without_model_changes(app, monkeypatch, kind, colors):
    module = empty_state_table if kind == "table" else empty_state_text
    pens = []
    class RecordingPainter(module.QPainter):
        def setPen(self, color):
            pens.append(color)
            super().setPen(color)
    monkeypatch.setattr(module, "QPainter", RecordingPainter)
    root = QWidget()
    layout = QVBoxLayout(root)
    if kind == "table":
        control = module.EmptyStateTable(0, 2, "Synthetic empty state")
    else:
        control = (module.EmptyStatePlainTextEdit if kind == "plain" else module.EmptyStateTextEdit)("Synthetic empty state")
    layout.addWidget(control)
    styles.apply_widget_palette(root, colors)
    assert not root.grab().isNull()
    assert pens and all(pen == QColor(colors.muted_text) for pen in pens)
    assert control.accessibleDescription() == control.empty_text == "Synthetic empty state"
    if kind == "table":
        assert control.rowCount() == 0 and control.columnCount() == 2
    else:
        assert control.toPlainText() == ""
    # Independent roots and nested explicit scopes do not overwrite each other.
    nested = QWidget(root)
    styles.apply_widget_palette(nested, DARK_PALETTE)
    assert styles.widget_palette(nested) is DARK_PALETTE
    assert styles.widget_palette(control) is colors
    assert styles.widget_palette(None) is DARK_PALETTE


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
@pytest.mark.parametrize("state,background", [
    (QStyle.StateFlag.State_Enabled, "base"),
    (QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_MouseOver, "indicator_hover_surface"),
    (QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_On, "primary"),
    (QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_NoChange, "primary"),
    (QStyle.StateFlag.State_On, "disabled_surface"),
])
def test_existing_checkbox_style_paints_from_explicit_scope_without_replacing_style(app, colors, state, background):
    root = QWidget()
    checkbox = QCheckBox(root)
    styles.apply_widget_palette(root, colors)
    style = styles.DataMaskStudioStyle()  # Same Dark-default class used by startup.
    style.setParent(root)
    option = QStyleOptionButton()
    option.rect = QRect(0, 0, 18, 18)
    option.state = state
    image = QImage(18, 18, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    style.drawPrimitive(QStyle.PrimitiveElement.PE_IndicatorCheckBox, option, painter, checkbox)
    painter.end()
    assert QColor(getattr(colors, background)) in [image.pixelColor(x, y) for y in range(18) for x in range(18)]


@pytest.mark.parametrize("answer", ["escape", "no", "yes"])
def test_shared_confirmation_stays_same_safe_modal_with_semantic_results(app, answer):
    parent = QWidget()
    message = QMessageBox(QMessageBox.Icon.Warning, "Synthetic warning", "Safe synthetic instructions",
                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
    message.setDefaultButton(QMessageBox.StandardButton.No)
    message.setEscapeButton(QMessageBox.StandardButton.No)
    message.setWindowModality(Qt.WindowModality.ApplicationModal)
    no, yes = message.button(QMessageBox.StandardButton.No), message.button(QMessageBox.StandardButton.Yes)
    set_button_role(yes, "destructive")
    message.show()
    app.processEvents()
    no.setFocus()
    before = message.text(), message.icon(), message.windowModality(), app.focusWidget()
    for colors in (LIGHT_PALETTE, DARK_PALETTE, LIGHT_PALETTE):
        styles.apply_widget_palette(parent, colors)
        app.processEvents()
        assert (message.text(), message.icon(), message.windowModality(), app.focusWidget()) == before
        assert message.defaultButton() is no and message.escapeButton() is no
        assert message.buttonRole(no) == QMessageBox.ButtonRole.NoRole
        assert message.buttonRole(yes) == QMessageBox.ButtonRole.YesRole
        assert yes.property("role") == "destructive"
        assert message.isVisible()
    if answer == "escape":
        QTest.keyClick(message, Qt.Key.Key_Escape)
    else:
        (yes if answer == "yes" else no).click()
    expected = QMessageBox.StandardButton.Yes if answer == "yes" else QMessageBox.StandardButton.No
    assert message.result() == expected


@pytest.mark.parametrize("state,foreground", [("neutral", "muted_text"), ("success", "success_text"),
    ("warning", "warning_text"), ("error", "danger_text")])
def test_shared_feedback_semantics_and_light_contrast(app, state, foreground):
    from PySide6.QtWidgets import QLabel
    root = QWidget()
    label = QLabel("Literal feedback", root)
    set_feedback_state(label, state)
    styles.apply_widget_palette(root, LIGHT_PALETTE)
    label.ensurePolished()
    assert label.property("feedbackState") == state and label.text() == "Literal feedback"
    assert label.palette().color(QPalette.ColorRole.WindowText) == QColor(getattr(LIGHT_PALETTE, foreground))
    assert contrast(getattr(LIGHT_PALETTE, foreground), LIGHT_PALETTE.window) >= 4.5


@pytest.mark.parametrize("foreground,background", [
    ("muted_text", "navigation_surface"), ("muted_text", "surface"),
    ("navigation_active_text", "navigation_active"), ("navigation_active_text", "navigation_pressed"),
    ("disabled_text", "navigation_surface"), ("disabled_text", "navigation_active"),
    ("heading_text", "navigation_surface"),
])
def test_light_navigation_text_contrast(foreground, background):
    assert contrast(getattr(LIGHT_PALETTE, foreground), getattr(LIGHT_PALETTE, background)) >= 4.5


@pytest.mark.parametrize("background", ["navigation_surface", "navigation_active", "navigation_pressed"])
def test_light_navigation_focus_indicator_contrast(background):
    assert contrast(LIGHT_PALETTE.focus, getattr(LIGHT_PALETTE, background)) >= 3


def test_late_children_and_nested_scopes_keep_palette_ownership(app):
    root = QWidget()
    layout = QVBoxLayout(root)
    styles.apply_widget_palette(root, LIGHT_PALETTE)
    child = empty_state_text.EmptyStatePlainTextEdit("Synthetic empty", root)
    layout.addWidget(child)
    child.ensurePolished()
    assert styles.widget_palette(child) is LIGHT_PALETTE
    assert child.palette().color(QPalette.ColorRole.Base) == QColor(LIGHT_PALETTE.base)
    nested = QWidget(root)
    layout.addWidget(nested)
    nested_layout = QVBoxLayout(nested)
    field = QLineEdit("Preserved", nested)
    nested_layout.addWidget(field)
    styles.apply_widget_palette(nested, DARK_PALETTE)
    styles.apply_widget_palette(root, LIGHT_PALETTE)
    assert styles.widget_palette(child) is LIGHT_PALETTE
    assert styles.widget_palette(field) is DARK_PALETTE
    assert field.palette().color(QPalette.ColorRole.Base) == QColor(DARK_PALETTE.base)
    assert field.text() == "Preserved"

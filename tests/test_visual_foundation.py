"""Focused checks for shared tokens and native Qt stylesheet inheritance."""

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QGroupBox, QLabel, QLineEdit,
    QMessageBox, QTableWidgetItem, QVBoxLayout,
)

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnAction
from data_mask_studio.app import create_application
from data_mask_studio.gui.action_styles import ACTION_INDICATOR_STYLES
from data_mask_studio.gui.components import empty_state_table, empty_state_text
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.styles import (
    BASE_COLOR, DISABLED_TEXT_COLOR, HIGHLIGHT_COLOR, TEXT_COLOR, WINDOW_COLOR,
    application_palette, application_stylesheet, section_title_stylesheet,
)
from data_mask_studio.gui.visual_tokens import COLORS, METRICS
from test_main_navigation import build_window


def rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", application_stylesheet())
    assert match is not None, f"Missing stylesheet rule: {selector}"
    return match.group(1)


def test_shared_values_are_valid_immutable_and_preserve_existing_imports():
    assert all(QColor(color).isValid() for color in COLORS.values())
    assert all(isinstance(value, int) and value > 0 for value in METRICS.values())
    with pytest.raises(TypeError):
        COLORS["text"] = "#000000"
    with pytest.raises(TypeError):
        METRICS["control_radius"] = 0
    assert (WINDOW_COLOR, TEXT_COLOR, BASE_COLOR, DISABLED_TEXT_COLOR, HIGHLIGHT_COLOR) == (
        COLORS["window"], COLORS["text"], COLORS["base"], COLORS["disabled_text"], COLORS["selection"],
    )


@pytest.mark.parametrize("group", [QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive,
                                   QPalette.ColorGroup.Disabled])
def test_palette_uses_shared_surfaces_text_selection_and_disabled_values(group):
    palette = application_palette()
    expected = {
        QPalette.ColorRole.Window: "window", QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "base", QPalette.ColorRole.AlternateBase: "surface",
        QPalette.ColorRole.Text: "input_text", QPalette.ColorRole.ToolTipBase: "raised_surface",
        QPalette.ColorRole.ToolTipText: "input_text", QPalette.ColorRole.Button: "button",
        QPalette.ColorRole.ButtonText: "button_text", QPalette.ColorRole.Highlight: "selection",
        QPalette.ColorRole.HighlightedText: "white", QPalette.ColorRole.PlaceholderText: "disabled_text",
    }
    if group == QPalette.ColorGroup.Disabled:
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
            expected[role] = "disabled_text"
        assert palette.color(group, QPalette.ColorRole.Highlight) == QColor("#344252")
        del expected[QPalette.ColorRole.Highlight]
    for role, token in expected.items():
        assert palette.color(group, role) == QColor(COLORS[token])


def test_qss_resolves_tokens_without_widening_state_or_container_selectors():
    stylesheet = application_stylesheet()
    assert "$" not in stylesheet and "__BADGE_" not in stylesheet
    assert f"border-color: {COLORS['focus']}" in rule("QPushButton:focus")
    assert f"background: {COLORS['button_hover']}" in rule("QPushButton:hover")
    assert f"color: {COLORS['disabled_text']}" in rule("QCheckBox:disabled")
    assert f"color: {COLORS['disabled_text']}" in rule(
        "QLineEdit:disabled, QComboBox:disabled, QPushButton:disabled")
    assert f"border-color: {COLORS['input_focus']}" in rule(
        "QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus, QTableWidget:focus")
    assert f"border-right: 1px solid {COLORS['border']}" in rule("QHeaderView::section")
    assert f"background: {COLORS['base']}" in rule("QScrollBar:vertical")
    assert f"border-radius: {METRICS['control_radius']}px" in rule(
        "QLineEdit, QComboBox, QPlainTextEdit, QTextEdit, QTableWidget")
    assert f"border-radius: {METRICS['panel_radius']}px" in rule("QGroupBox")
    assert f"color: {COLORS['muted_text']}" in rule("QFrame#pageHeader > QLabel#pageDescription")
    # Do not introduce a broad label font rule or change the existing transparent label rule.
    assert rule("QLabel").strip() == "background: transparent;"


def test_sidebar_states_use_scoped_selectors_and_shared_theme_values():
    assert f"background: {COLORS['base']}" in rule("QWidget#sidebarNavigation")
    assert f"border-right: 1px solid {COLORS['panel_border']}" in rule("QWidget#sidebarNavigation")
    assert f"background: {COLORS['panel_border']}" in rule("QFrame#navigationDivider")
    assert f"color: {COLORS['disabled_text']}" in rule("QLabel#navigationGroup")
    for selector in ("QPushButton#navigationItem", "QPushButton#navigationUtility"):
        assert f"border-left: {METRICS['navigation_marker_width']}px solid transparent" in rule(selector)
        assert f"padding: 0 {METRICS['button_padding']}px" in rule(selector)
        assert f"background: {COLORS['surface']}" in rule(selector + ":hover")
        assert f"background: {COLORS['navigation_pressed']}" in rule(selector + ":pressed")
        assert f"border-color: {COLORS['focus']}" in rule(selector + ":focus")
        assert f"color: {COLORS['disabled_text']}" in rule(selector + ":disabled")
    assert f"background: {COLORS['navigation_active']}" in rule("QPushButton#navigationItem:checked")
    assert f"border-left-color: {COLORS['accent']}" in rule("QPushButton#navigationItem:checked")
    assert f"border-left-color: {COLORS['accent']}" in rule("QPushButton#navigationItem:checked:focus")
    assert f"border-left-color: {COLORS['disabled_border']}" in rule("QPushButton#navigationItem:checked:disabled")


@pytest.mark.parametrize("action,surface,border", [
    (ColumnAction.PRESERVE, "action_preserve", "neutral_indicator"),
    (ColumnAction.MASK, "action_mask", "accent"),
    (ColumnAction.EXCLUDE, "danger_surface", "danger_border"),
])
def test_action_indicators_reuse_shared_values_without_new_local_overrides(action, surface, border):
    assert ACTION_INDICATOR_STYLES[action] == (
        f"QComboBox {{ background-color: {COLORS[surface]}; border-color: {COLORS[border]}; }}"
    )


def test_native_dialog_controls_keep_state_focus_popup_and_button_connections():
    app = create_application([])
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    group = QGroupBox("Synthetic controls")
    controls = QVBoxLayout(group)
    field = QLineEdit("unchanged")
    field.setReadOnly(True)
    disabled = QLineEdit("disabled")
    disabled.setEnabled(False)
    combo = ScrollSafeComboBox()
    combo.addItems(["First", "Second"])
    combo.setCurrentIndex(1)
    checkbox = QCheckBox("Checked")
    checkbox.setChecked(True)
    for widget in (field, disabled, combo, checkbox):
        controls.addWidget(widget)
    layout.addWidget(group)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    rejected = []
    buttons.rejected.connect(lambda: rejected.append(True))
    layout.addWidget(buttons)
    dialog.show()
    app.processEvents()

    assert dialog.palette().color(QPalette.ColorRole.Window) == QColor(COLORS["window"])
    assert field.isReadOnly() and field.text() == "unchanged"
    assert not disabled.isEnabled() and disabled.text() == "disabled"
    assert disabled.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text) == QColor(COLORS["disabled_text"])
    assert combo.focusPolicy() == Qt.FocusPolicy.StrongFocus and combo.currentIndex() == 1
    combo.setFocus()
    app.processEvents()
    assert combo.hasFocus()
    assert checkbox.isChecked() and checkbox.isEnabled()
    combo.showPopup()
    app.processEvents()
    assert combo.view().isVisible()
    assert combo.view().palette().color(QPalette.ColorRole.Highlight) == QColor(COLORS["selection"])
    combo.hidePopup()
    assert combo.currentIndex() == 1
    buttons.button(QDialogButtonBox.StandardButton.Close).click()
    assert rejected == [True]

    message = QMessageBox(QMessageBox.Icon.Warning, "Synthetic warning", "Safe message",
                         QMessageBox.StandardButton.Ok)
    message.ensurePolished()
    assert message.palette().color(QPalette.ColorRole.Window) == QColor(COLORS["window"])
    assert message.button(QMessageBox.StandardButton.Ok).isEnabled()


def test_section_heading_style_is_shared_without_changing_widget_structure(tmp_path):
    create_application([])
    window = build_window(tmp_path)
    headings = [label for label in window.anonymization_widget.findChildren(QLabel)
                if label.text() in ("Configuração das colunas", "Perfil de configuração")]
    assert len(headings) == 2
    for label in headings:
        label.ensurePolished()
        assert label.styleSheet() == section_title_stylesheet()
        assert label.font().pixelSize() == METRICS["section_font_size"]


@pytest.mark.parametrize("kind", ["table", "plain", "rich"])
def test_empty_state_painters_share_muted_text_without_changing_contents(monkeypatch, kind):
    create_application([])
    module = empty_state_table if kind == "table" else empty_state_text
    pens = []
    original_painter = module.QPainter

    class RecordingPainter(original_painter):
        def setPen(self, color):
            pens.append(color)
            super().setPen(color)

    monkeypatch.setattr(module, "QPainter", RecordingPainter)
    if kind == "table":
        widget = module.EmptyStateTable(0, 1, "Synthetic empty state")
    elif kind == "plain":
        widget = module.EmptyStatePlainTextEdit("Synthetic empty state")
    else:
        widget = module.EmptyStateTextEdit("Synthetic empty state")
    assert not widget.grab().isNull()
    assert pens and all(color == QColor(COLORS["muted_text"]) for color in pens)
    assert widget.accessibleDescription() == "Synthetic empty state"
    if kind == "table":
        widget.setRowCount(1)
        widget.setItem(0, 0, QTableWidgetItem("unchanged"))
        assert widget.item(0, 0).text() == "unchanged"
    else:
        widget.setPlainText("unchanged")
        assert widget.toPlainText() == "unchanged"
    pens.clear()
    assert not widget.grab().isNull()
    assert not pens

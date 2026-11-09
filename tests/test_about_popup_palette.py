"""Scoped About and Qt-owned popup appearance, never theme activation."""
from pathlib import Path
import sqlite3

import pytest
from PySide6.QtCore import QPoint, QThread, QTranslator, Qt
from PySide6.QtGui import QColor, QPalette, QTextDocument
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QDialogButtonBox, QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.gui import about_dialog as about_module
from data_mask_studio.gui.about_dialog import AboutDialog, PUBLIC_URLS, PRIVACY_URL, REPOSITORY_URL
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.styles import apply_widget_palette
from data_mask_studio.gui.theme_palette import DARK_PALETTE, LIGHT_PALETTE
from data_mask_studio.i18n import Translator
from data_mask_studio.preferences import PreferencesService
from test_bootstrap_localization import app
from test_localization_integration import worker_references
from test_navigation_localization import build_localized_window
from test_theme_palette import contrast


def anchor_formats(label):
    document = QTextDocument()
    document.setHtml(label.text())
    result = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            formatting = iterator.fragment().charFormat()
            if formatting.isAnchor():
                result.append((formatting.anchorHref(), formatting.foreground().color(), formatting.fontUnderline()))
            iterator += 1
        block = block.next()
    return result


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_known_link_colors_follow_explicit_palette_with_same_targets(app, locale, colors):
    dialog = AboutDialog(translator=Translator(locale))
    apply_widget_palette(dialog, colors)
    assert {url for url, _, _ in anchor_formats(dialog.links_label)} == PUBLIC_URLS
    assert all(color == QColor(colors.link_text) and underline for _, color, underline in anchor_formats(dialog.links_label))
    assert contrast(colors.link_text, colors.window) >= 4.5


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_about_palette_cycles_keep_selected_third_link_focus_and_metadata_cache(app, tmp_path, monkeypatch, locale):
    service = PreferencesService(tmp_path / "preferences.json")
    assert service.save_language(locale).error is None and service.save_theme("system").error is None
    saved = service.path.read_bytes()
    dialog = AboutDialog(translator=Translator(locale))
    dialog.show()
    app.processEvents()
    links = dialog.links_label
    labels = tuple(dialog.findChildren(QWidget))
    close = dialog.buttons.button(QDialogButtonBox.StandardButton.Close)
    links.setFocus(Qt.FocusReason.TabFocusReason)
    for _ in range(3):
        QTest.keyClick(links, Qt.Key.Key_Tab)
    selected, start = links.selectedText(), links.selectionStart()
    assert selected and start > 0 and links.hasFocus()
    opened = []
    monkeypatch.setattr(about_module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    activated = QSignalSpy(links.linkActivated)
    global_state = app.palette(), app.styleSheet(), app.style(), app.findChildren(QTranslator)
    def forbidden(*args, **kwargs):
        pytest.fail("Styling must not reread metadata, switch language or invoke services")
    with monkeypatch.context() as guard:
        guard.setattr(about_module, "application_version", forbidden)
        guard.setattr(dialog, "retranslate", forbidden)
        guard.setattr(sqlite3, "connect", forbidden)
        guard.setattr(QThread, "start", forbidden)
        for name in ("load", "save_theme", "save_language", "resolve_language"):
            guard.setattr(PreferencesService, name, forbidden)
        for name in ("open", "read_bytes", "read_text", "stat", "exists", "is_file", "resolve", "iterdir"):
            guard.setattr(Path, name, forbidden)
        for count, colors in enumerate((LIGHT_PALETTE, DARK_PALETTE, LIGHT_PALETTE, DARK_PALETTE), 1):
            apply_widget_palette(dialog, colors)
            app.processEvents()
            assert dialog.links_label is links and tuple(dialog.findChildren(QWidget)) == labels
            assert links.hasFocus() and links.selectedText() == selected and links.selectionStart() == start
            assert all(color == QColor(colors.link_text) for _, color, _ in anchor_formats(links))
            assert dialog.buttons.button(QDialogButtonBox.StandardButton.Close) is close and close.isDefault()
            assert dialog.isVisible() and dialog.isModal() and dialog._translator.locale == locale
            assert global_state == (app.palette(), app.styleSheet(), app.style(), app.findChildren(QTranslator))
            assert activated.count() == count - 1
            QTest.keyClick(links, Qt.Key.Key_Return)
            assert activated.count() == count and opened == [PRIVACY_URL] * count
    assert service.path.read_bytes() == saved
    links.linkActivated.emit("file:///synthetic-unapproved")
    assert opened == [PRIVACY_URL] * 4
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()


def test_late_about_inherits_parent_scope_without_new_reads_on_updates(app, tmp_path, monkeypatch):
    acquired = []
    monkeypatch.setattr(about_module, "application_version", lambda: acquired.append(True) or "1.3.0")
    window = build_localized_window(tmp_path)
    window.show()
    app.processEvents()
    pages = window.page_widgets
    apply_widget_palette(window, LIGHT_PALETTE)
    assert window.about_dialog is None and not acquired
    window.show_about_dialog()
    app.processEvents()
    dialog = window.about_dialog
    assert acquired == [True]
    assert all(color == QColor(LIGHT_PALETTE.link_text) for _, color, _ in anchor_formats(dialog.links_label))
    workers = worker_references(window)
    for colors in (DARK_PALETTE, LIGHT_PALETTE):
        apply_widget_palette(window, colors)
        app.processEvents()
        assert window.page_widgets == pages and window.about_dialog is dialog
        assert worker_references(window) == workers and acquired == [True]
        assert all(color == QColor(colors.link_text) for _, color, _ in anchor_formats(dialog.links_label))


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_about_hover_retains_link_affordance_and_activation(app, monkeypatch, colors):
    dialog = AboutDialog()
    apply_widget_palette(dialog, colors)
    dialog.show()
    app.processEvents()
    links = dialog.links_label
    hovered = QSignalSpy(links.linkHovered)
    opened = []
    monkeypatch.setattr(about_module.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    blank = QPoint(links.width() - 1, links.height() - 1)
    QTest.mouseMove(links, blank)
    blank_cursor = links.cursor().shape()
    point = QPoint(2, links.fontMetrics().height() // 2)
    QTest.mouseMove(links, point)
    app.processEvents()
    assert any(hovered.at(i)[0] == REPOSITORY_URL for i in range(hovered.count()))
    assert links.cursor().shape() == Qt.CursorShape.PointingHandCursor
    other = LIGHT_PALETTE if colors is DARK_PALETTE else DARK_PALETTE
    apply_widget_palette(dialog, other)
    app.processEvents()
    assert links.cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseMove(links, blank)
    assert links.cursor().shape() == blank_cursor
    QTest.mouseMove(links, point)
    QTest.mouseClick(links, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    assert opened == [REPOSITORY_URL] and dialog.isVisible()


@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
@pytest.mark.parametrize("column_action", [False, True], ids=["normal", "action"])
def test_late_popup_selection_scroll_and_dismissal_keep_semantics(app, colors, column_action):
    root = QWidget()
    layout = QVBoxLayout(root)
    combo = ScrollSafeComboBox()
    for index in range(30):
        combo.addItem(f"Synthetic choice {index}", index)
    combo.setCurrentIndex(1)
    combo.model().item(3).setEnabled(False)
    if column_action:
        combo.setProperty("columnAction", "mask")
    layout.addWidget(combo)
    root.show()
    apply_widget_palette(root, colors)
    combo.setFocus()
    app.processEvents()
    changes = QSignalSpy(combo.currentIndexChanged)
    combo.showPopup()
    app.processEvents()
    view = combo.view()
    assert view.isVisible()
    surface = colors.base if column_action else colors.window
    assert view.palette().color(QPalette.ColorRole.Base) == QColor(surface)
    assert view.palette().color(QPalette.ColorRole.Highlight) == QColor(colors.selection)
    assert view.palette().color(QPalette.ColorRole.HighlightedText) == QColor(colors.selection_text)
    row = view.visualRect(view.model().index(1, 0))
    image = view.viewport().grab().toImage()
    painted = {image.pixelColor(x, y).name() for y in range(row.top(), row.bottom() + 1)
               for x in range(row.left(), row.right() + 1)}
    assert colors.selection_text in painted
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert not view.isVisible() and combo.currentData() == 1 and changes.count() == 0
    combo.showPopup()
    app.processEvents()
    QTest.keyClick(view, Qt.Key.Key_Down)
    QTest.keyClick(view, Qt.Key.Key_Down)  # Skip disabled item 3.
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert combo.currentData() == 4 and changes.count() == 1
    assert not view.isVisible() and not combo.model().item(3).isEnabled()
    combo.showPopup()
    app.processEvents()
    QTest.keyClick(view, Qt.Key.Key_End)
    app.processEvents()
    assert view.currentIndex().row() == 29
    assert view.visualRect(view.model().index(29, 0)).intersects(view.viewport().rect())
    QTest.keyClick(view, Qt.Key.Key_Escape)
    assert combo.currentData() == 4 and changes.count() == 1


@pytest.mark.parametrize("kind", ["line", "plain"])
@pytest.mark.parametrize("answer", ["escape", "enter"])
@pytest.mark.parametrize("colors", [DARK_PALETTE, LIGHT_PALETTE], ids=["dark", "light"])
def test_late_context_menu_palette_actions_and_native_dismissal(app, kind, answer, colors):
    # The offscreen QPA owns an in-process clipboard, not the Windows clipboard.
    assert app.platformName() == "offscreen"
    root = QWidget()
    layout = QVBoxLayout(root)
    field = QLineEdit("Synthetic only") if kind == "line" else QPlainTextEdit("Synthetic only")
    layout.addWidget(field)
    apply_widget_palette(root, colors)
    root.show()
    app.processEvents()
    menu = field.createStandardContextMenu()
    actions = tuple(menu.actions())
    states = [(action.text(), action.isEnabled(), action.isSeparator()) for action in actions]
    assert any(not action.isEnabled() and not action.isSeparator() for action in actions)
    select_all = actions[-1]
    assert select_all.isEnabled()
    menu.popup(field.mapToGlobal(QPoint(0, field.height())))
    app.processEvents()
    assert menu.palette().color(QPalette.ColorRole.Window) == QColor(colors.window)
    assert menu.palette().color(QPalette.ColorRole.Highlight) == QColor(colors.selection)
    menu.setActiveAction(select_all)
    size = menu.size()
    triggered = QSignalSpy(select_all.triggered)
    other = LIGHT_PALETTE if colors is DARK_PALETTE else DARK_PALETTE
    for current in (colors, other, colors):
        apply_widget_palette(root, current)
        app.processEvents()
        assert menu.isVisible() and menu.activeAction() is select_all and menu.size() == size
        assert menu.palette().color(QPalette.ColorRole.Window) == QColor(current.window)
        row = menu.actionGeometry(select_all)
        image = menu.grab().toImage()
        painted = {image.pixelColor(x, y).name() for y in range(row.top(), row.bottom() + 1)
                   for x in range(row.left(), row.right() + 1)}
        assert current.selection_text in painted
        disabled = next(a for a in actions if not a.isEnabled() and not a.isSeparator())
        row = menu.actionGeometry(disabled)
        disabled_pixels = {image.pixelColor(x, y).name() for y in range(row.top(), row.bottom() + 1)
                           for x in range(row.left(), row.right() + 1)}
        assert current.disabled_text in disabled_pixels
        assert triggered.count() == 0
    assert tuple(menu.actions()) == actions
    assert [(a.text(), a.isEnabled(), a.isSeparator()) for a in actions] == states
    QTest.keyClick(menu, Qt.Key.Key_Escape if answer == "escape" else Qt.Key.Key_Return)
    assert not menu.isVisible()
    text = field.text() if kind == "line" else field.toPlainText()
    selected = field.selectedText() if kind == "line" else field.textCursor().selectedText()
    assert text == "Synthetic only"
    assert selected == ("" if answer == "escape" else "Synthetic only")
    assert triggered.count() == (0 if answer == "escape" else 1)
    menu.deleteLater()

"""Deliver actual Qt key events through page/window shortcut contexts."""

from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QThread, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QDialog, QFileDialog, QLineEdit, QVBoxLayout

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.i18n import Translator
from data_mask_studio.csv_tools.source_binding import SourceColumnRef
from data_mask_studio.processing.models import CompositeColumnConfig, CompositeSource
from test_column_search import page


@pytest.fixture
def gui(page, monkeypatch):
    app, window, mask = page
    window.activateWindow()
    app.processEvents()
    assert window.isActiveWindow()
    started = []
    monkeypatch.setattr(QThread, "start", lambda worker: started.append(worker))
    yield app, window, mask
    assert not started, "Selecting/focusing through a shortcut must never start a worker"


def key(app, letter):
    target = app.focusWidget()
    assert target is not None
    QTest.keyClick(target, letter, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()


def watch_picker(monkeypatch, result=""):
    calls = []
    def choose(*args):
        calls.append(args)
        return str(result), ""
    monkeypatch.setattr(QFileDialog, "getOpenFileName", choose)
    return calls


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_ctrl_o_uses_existing_mask_button_and_real_csv_inspection(gui, tmp_path, monkeypatch, locale):
    app, window, mask = gui
    window.retranslate(Translator(locale))
    source = tmp_path / "selected.csv"
    source.write_text("Different,Name\nSynthetic,Only test data\n", encoding="utf-8")
    calls = watch_picker(monkeypatch, source)
    clicks = QSignalSpy(mask.select_button.clicked)
    # Sidebar focus still belongs to the active page's window context.
    window.navigation.buttons[0].setFocus()
    key(app, Qt.Key.Key_O)
    assert len(calls) == clicks.count() == 1
    assert calls[0][1] == Translator(locale).text("masking.file.select_title")
    assert calls[0][3] == Translator(locale).text("masking.file.filter")
    assert mask.path_field.text() == str(source) and mask._inspection_result.headers == ["Different", "Name"]
    assert not mask.generate_button.isEnabled() and mask._worker is mask._detection_worker is None
    assert window.file_restoration_widget.stack.currentIndex() == 0


@pytest.mark.parametrize("state", ["idle", "csv", "html"])
def test_ctrl_o_uses_visible_restore_button_and_unified_selector(gui, tmp_path, monkeypatch, state):
    app, window, mask = gui
    restore = window.file_restoration_widget
    window.set_current_page(2)
    csv = Path(mask.path_field.text())
    html = tmp_path / "synthetic.html"
    html.write_text("<!doctype html><html><body><p>SYNTHETIC</p></body></html>", encoding="utf-8")
    if state != "idle":
        assert restore.load_file(csv if state == "csv" else html)
    app.processEvents()
    button = (restore.select_button if state == "idle" else restore.csv_widget.select_button
              if state == "csv" else restore.html_widget.select_button)
    calls = watch_picker(monkeypatch, html if state != "html" else csv)
    clicks = QSignalSpy(button.clicked)
    window.navigation.buttons[2].setFocus()
    key(app, Qt.Key.Key_O)
    assert len(calls) == clicks.count() == 1 and "*.csv *.html *.htm" in calls[0][3]
    assert restore.stack.currentWidget() is (restore.csv_widget if state == "html" else restore.html_widget)
    assert restore.csv_widget._worker is restore.html_widget._worker is None


def test_ctrl_f_only_focuses_search_preserving_query_cursor_selection_and_validated_plan(gui, monkeypatch):
    app, window, mask = gui
    mask.validate_current_configuration()
    before = mask._build_current_plan()
    mask.column_search_field.setText("CPF")
    mask.column_search_field.setSelection(1, 1)
    selection = mask.column_search_field.selectedText(), mask.column_search_field.cursorPosition()
    values = deepcopy(mask._column_configs)
    flags = mask._configuration_validated, mask._configuration_dirty, mask.generate_button.isEnabled()
    changed = QSignalSpy(mask.column_search_field.textChanged)
    operations = []
    def unexpected(*args, **kwargs):
        operations.append(True)
        raise AssertionError("Focusing search must be presentation only")
    mask._output_name_fields[1].setFocus()
    with monkeypatch.context() as guard:
        guard.setattr(Path, "open", unexpected)
        guard.setattr(Path, "stat", unexpected)
        guard.setattr(mask, "validate_current_configuration", unexpected)
        for _ in range(2):
            key(app, Qt.Key.Key_F)
            assert app.focusWidget() is mask.column_search_field
    assert not operations and changed.count() == 0
    assert mask.column_search_field.text() == "CPF"
    assert (mask.column_search_field.selectedText(), mask.column_search_field.cursorPosition()) == selection
    assert mask._column_configs == values and mask._build_current_plan() == before
    assert (mask._configuration_validated, mask._configuration_dirty, mask.generate_button.isEnabled()) == flags
    assert window.current_page_index() == 0


def test_shortcuts_on_hidden_pages_do_not_conflict_with_native_shortcuts_on_another_page(gui, monkeypatch):
    app, window, mask = gui
    calls = watch_picker(monkeypatch)
    native_open = QShortcut(QKeySequence("Ctrl+O"), window.batch_widget)
    native_find = QShortcut(QKeySequence("Ctrl+F"), window.batch_widget)
    opened, found = QSignalSpy(native_open.activated), QSignalSpy(native_find.activated)
    window.set_current_page(1)
    window.navigation.buttons[1].setFocus()
    app.processEvents()
    key(app, Qt.Key.Key_O)
    key(app, Qt.Key.Key_F)
    assert opened.count() == found.count() == 1 and not calls
    assert not mask.column_search_field.hasFocus() and window.current_page_index() == 1
    # Restore has Ctrl+O, not Ctrl+F: its own native Find remains available too.
    native_restore_find = QShortcut(QKeySequence("Ctrl+F"), window.file_restoration_widget)
    restore_find = QSignalSpy(native_restore_find.activated)
    window.set_current_page(2)
    window.navigation.buttons[2].setFocus()
    app.processEvents()
    key(app, Qt.Key.Key_F)
    assert restore_find.count() == 1 and not mask.column_search_field.hasFocus()


def test_ctrl_f_reveals_search_when_the_page_has_been_scrolled_down(gui):
    app, window, mask = gui
    mask.composite_section.set_configurations(tuple(CompositeColumnConfig(f"PAIR_{index}", components=(
        CompositeSource(SourceColumnRef("Nome")), CompositeSource(SourceColumnRef("CPF"))))
        for index in range(3)), notify=True)
    mask._action_fields[0].setCurrentIndex(mask._action_fields[0].findData("mask"))
    mask.transfer_controls.checkbox.setChecked(True)
    mask.validate_current_configuration()
    window.resize(960, 640)
    for _ in range(3):
        app.processEvents()
    mask.generate_button.setFocus()
    area = window.page_shells[0].scroll_area
    area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
    app.processEvents()
    assert mask.column_search_field.mapTo(area.viewport(), QPoint()).y() < 0
    key(app, Qt.Key.Key_F)
    top = mask.column_search_field.mapTo(area.viewport(), QPoint()).y()
    assert mask.column_search_field.hasFocus()
    assert 0 <= top and top + mask.column_search_field.height() <= area.viewport().height()


@pytest.mark.parametrize("modal", [False, True], ids=["modeless", "modal"])
def test_dialog_native_shortcuts_are_not_shadowed_by_page_shortcuts(gui, monkeypatch, modal):
    app, window, mask = gui
    calls = watch_picker(monkeypatch)
    dialog = QDialog(window)
    dialog.setModal(modal)
    field = QLineEdit("Synthetic dialog input", dialog)
    QVBoxLayout(dialog).addWidget(field)
    native_open = QShortcut(QKeySequence("Ctrl+O"), dialog)
    native_find = QShortcut(QKeySequence("Ctrl+F"), dialog)
    opened, found = QSignalSpy(native_open.activated), QSignalSpy(native_find.activated)
    dialog.show()
    dialog.activateWindow()
    field.setFocus()
    app.processEvents()
    assert dialog.isActiveWindow()
    try:
        key(app, Qt.Key.Key_O)
        key(app, Qt.Key.Key_F)
        assert opened.count() == found.count() == 1 and not calls
        assert field.hasFocus() and field.text() == "Synthetic dialog input"
    finally:
        dialog.reject()


@pytest.mark.parametrize("attribute,value", [("_worker", "worker"), ("_detection_worker", "worker"),
                                             ("_analyzing", True)])
def test_mask_owned_operation_blocks_both_shortcuts_even_before_reference_retirement(gui, monkeypatch, attribute, value):
    app, _, mask = gui
    calls = watch_picker(monkeypatch)
    mask.select_button.setFocus()
    value = QThread() if value == "worker" else value
    with monkeypatch.context() as guard:
        guard.setattr(mask, attribute, value)
        key(app, Qt.Key.Key_O)
        key(app, Qt.Key.Key_F)
        assert not calls and app.focusWidget() is mask.select_button
        assert getattr(mask, attribute) is value


@pytest.mark.parametrize("owner", ["csv_widget", "html_widget"])
def test_restore_owned_worker_blocks_file_picker_even_when_the_owner_is_hidden(gui, monkeypatch, owner):
    app, window, _ = gui
    restore = window.file_restoration_widget
    window.set_current_page(2)
    restore.select_button.setFocus()
    app.processEvents()
    calls = watch_picker(monkeypatch)
    worker = QThread()
    assert not worker.isRunning()
    with monkeypatch.context() as guard:
        guard.setattr(getattr(restore, owner), "_worker", worker)
        key(app, Qt.Key.Key_O)
        assert not calls and getattr(restore, owner)._worker is worker


def test_disabled_controls_and_empty_search_are_not_bypassed(gui, monkeypatch):
    app, window, mask = gui
    calls = watch_picker(monkeypatch)
    mask.select_button.setEnabled(False)
    mask._output_name_fields[0].setFocus()
    key(app, Qt.Key.Key_O)
    assert not calls
    mask.column_search_field.setEnabled(False)
    key(app, Qt.Key.Key_F)
    assert mask._output_name_fields[0].hasFocus()
    mask.select_button.setEnabled(True)
    mask.clear_selection()
    mask.select_button.setFocus()
    key(app, Qt.Key.Key_F)
    assert mask.select_button.hasFocus() and not mask.column_search_field.isEnabled()
    window.set_current_page(2)
    restore = window.file_restoration_widget
    restore.select_button.setEnabled(False)
    window.navigation.buttons[2].setFocus()
    app.processEvents()
    key(app, Qt.Key.Key_O)
    assert not calls


def test_restore_shortcut_respects_a_disabled_loaded_selection_button(gui, monkeypatch):
    app, window, mask = gui
    restore = window.file_restoration_widget
    assert restore.load_file(Path(mask.path_field.text()))
    window.set_current_page(2)
    restore.csv_widget.select_button.setEnabled(False)
    window.navigation.buttons[2].setFocus()
    app.processEvents()
    calls = watch_picker(monkeypatch)
    key(app, Qt.Key.Key_O)
    assert not calls and restore.stack.currentWidget() is restore.csv_widget


def test_cancelled_picker_preserves_package_mode_password_and_column_selection(gui, monkeypatch):
    app, window, mask = gui
    restore = window.file_restoration_widget
    assert restore.load_file(Path(mask.path_field.text()))
    window.set_current_page(2)
    csv = restore.csv_widget
    csv.source_combo.setCurrentIndex(1)
    csv._checkboxes[0].setChecked(True)
    csv.package_controls.path.setText("synthetic.dmspackage")
    csv.package_controls.password.setText("transient synthetic password")
    csv.package_controls.password.setFocus()
    app.processEvents()
    before = csv._inspection
    calls = watch_picker(monkeypatch)
    key(app, Qt.Key.Key_O)
    assert len(calls) == 1 and csv._inspection is before
    assert csv._checkboxes[0].isChecked() and csv.source_combo.currentData() == "package"
    assert csv.package_controls.password.text() == "transient synthetic password"
    assert csv.package_controls.path.text() == "synthetic.dmspackage"


def test_combo_popup_and_native_editing_keys_keep_their_existing_behavior(gui, monkeypatch):
    app, _, mask = gui
    calls = watch_picker(monkeypatch)
    combo = mask.column_action_filter
    combo.showPopup()
    app.processEvents()
    before = combo.currentData()
    try:
        key(app, Qt.Key.Key_O)
        key(app, Qt.Key.Key_F)
        assert not calls and combo.currentData() == before
        assert not mask.column_search_field.hasFocus()
    finally:
        combo.hidePopup()
    search = mask.column_search_field
    search.setText("CPF")
    search.setFocus()
    key(app, Qt.Key.Key_A)
    assert search.selectedText() == "CPF"
    QTest.keyClick(search, Qt.Key.Key_Backspace)
    key(app, Qt.Key.Key_Z)
    assert search.text() == "CPF"


def test_retranslation_does_not_duplicate_shortcuts_or_change_their_context(gui, monkeypatch):
    app, window, mask = gui
    mask.column_search_field.setText("CPF")
    before = deepcopy(mask._column_configs)
    shortcuts = window.findChildren(QShortcut)
    assert len(shortcuts) == 5
    assert sorted(shortcut.key().toString(QKeySequence.SequenceFormat.PortableText) for shortcut in shortcuts) == [
        "Ctrl+F", "Ctrl+O", "Ctrl+O", "Ctrl+Y", "Ctrl+Z",
    ]
    ambiguous = [QSignalSpy(shortcut.activatedAmbiguously) for shortcut in shortcuts]
    calls = watch_picker(monkeypatch)
    for locale in ("en", "pt-BR", "en"):
        window.retranslate(Translator(locale))
        mask.select_button.setFocus()
        key(app, Qt.Key.Key_O)
        assert window.findChildren(QShortcut) == shortcuts
        assert mask.column_search_field.text() == "CPF" and mask._column_configs == before
    assert len(calls) == 3 and all(spy.count() == 0 for spy in ambiguous)
    assert all(shortcut.context() == Qt.ShortcutContext.WindowShortcut and not shortcut.autoRepeat()
               for shortcut in shortcuts)

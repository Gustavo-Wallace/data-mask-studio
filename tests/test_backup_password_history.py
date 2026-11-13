"""Real Qt editor history for backup and maintenance passwords only."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application


SECRET = "Synthetic-Backup-Secret-001B"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    return create_application([])


@pytest.fixture(params=["create_password_field", "confirm_password_field", "restore_password_field", "maintenance"])
def password_input(app, tmp_path, request):
    if request.param == "maintenance":
        from test_maintenance_interface import prepare, widget_for
        paths, _ = prepare(tmp_path)
        widget = widget_for(paths)
        widget.sections.setCurrentIndex(1)
        field, toggle, clear = widget.backup_password_field, widget.show_backup_password, widget._finished
    else:
        from test_backup_interface import prepare_widget
        widget, _ = prepare_widget(tmp_path)
        field = getattr(widget, request.param)
        if request.param == "restore_password_field":
            toggle, clear = widget.show_restore_password, widget._clear_all_passwords
        else:
            toggle, clear = widget.show_create_password, widget._clear_create_passwords
    widget.resize(1000, 1000)
    widget.show()
    app.processEvents()
    return widget, field, toggle, clear, request.param == "maintenance"


def seed_password(field, toggle, visible, entry):
    toggle.setChecked(True if entry == "redo-pending" else visible)
    field.setFocus()
    QTest.keyClicks(field, SECRET)
    if entry == "edited":
        field.setSelection(10, 6)
        QTest.keyClicks(field, "Edited")
        QTest.keyClick(field, Qt.Key.Key_Backspace)
        assert field.text() and field.text() != SECRET
    elif entry == "deleted":
        field.selectAll()
        QTest.keyClick(field, Qt.Key.Key_Backspace)
        assert not field.text()
    elif entry == "redo-pending":
        field.undo()
        assert not field.text() and field.isRedoAvailable()
    else:
        assert field.text() == SECRET
    toggle.setChecked(visible)


def assert_context_history_erased(field):
    menu = field.createStandardContextMenu()
    try:
        actions = {action.objectName(): action for action in menu.actions()}
        for name in ("edit-undo", "edit-redo"):
            action = actions[name]  # Native Qt identity, independent of locale.
            enabled = action.isEnabled()
            action.trigger()
            assert not field.text(), "Context-menu action recovered the synthetic secret"
            assert not enabled
    finally:
        menu.deleteLater()
        QCoreApplication.sendPostedEvents(menu, QEvent.Type.DeferredDelete)


def assert_password_history_erased(field):
    """Also inspect masked/disabled fields without changing their UI lifecycle."""
    original_mode = field.echoMode()
    try:
        field.setEchoMode(QLineEdit.EchoMode.Normal)
        assert not field.text()
        undo_available, redo_available = field.isUndoAvailable(), field.isRedoAvailable()
        for key in (Qt.Key.Key_Z, Qt.Key.Key_Y):
            QTest.keyClick(field, key, Qt.KeyboardModifier.ControlModifier)
            assert not field.text(), "Keyboard shortcut recovered the synthetic secret"
        assert_context_history_erased(field)
        field.undo()
        assert not field.text()
        field.redo()
        assert not field.text()
        assert not undo_available and not redo_available
        assert not field.isUndoAvailable() and not field.isRedoAvailable()
    finally:
        field.setEchoMode(original_mode)


@pytest.mark.parametrize("visible", [False, True], ids=["hidden", "visible"])
@pytest.mark.parametrize("entry", ["typed", "edited", "deleted", "redo-pending"])
def test_backup_cleanup_discards_native_undo_and_redo(app, password_input, visible, entry):
    _, field, toggle, clear, hides_password = password_input
    seed_password(field, toggle, visible, entry)
    assert app.focusWidget() is field
    expected_visibility = False if hides_password else visible
    for _ in range(2):
        clear()
        assert not field.text()
        assert toggle.isChecked() is expected_visibility
        assert field.echoMode() == (QLineEdit.EchoMode.Normal if expected_visibility else QLineEdit.EchoMode.Password)
        assert app.focusWidget() is field
        toggle.setChecked(True)
        assert_password_history_erased(field)
        toggle.setChecked(expected_visibility)


@pytest.mark.parametrize("visible", [False, True], ids=["hidden", "visible"])
def test_backup_context_menu_cannot_recover_a_cleared_password(password_input, visible):
    _, field, toggle, clear, _ = password_input
    seed_password(field, toggle, visible, "edited")
    clear()
    toggle.setChecked(True)
    assert not field.text()
    assert_context_history_erased(field)

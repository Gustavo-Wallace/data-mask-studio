"""Package secret cleanup must discard Qt's native edit history, not only text."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.gui.package_restoration_controls import PackageRestorationControls
from data_mask_studio.gui.transfer_package_controls import TransferPackageControls
from test_masking_controls_localization import app


@pytest.fixture(params=[
    (TransferPackageControls, "password"),
    (TransferPackageControls, "confirmation"),
    (PackageRestorationControls, "password"),
], ids=["creation-password", "creation-confirmation", "restoration-password"])
def package_input(app, request):
    factory, field_name = request.param
    widget = factory()
    if isinstance(widget, TransferPackageControls):
        widget.set_mask_available(True)
        widget.checkbox.setChecked(True)
        toggle, clear = widget.show_passwords, widget.clear_passwords
    else:
        toggle, clear = widget.show_password, widget.clear_password
    widget.show()
    app.processEvents()
    return widget, getattr(widget, field_name), toggle, clear


@pytest.mark.parametrize("visible", [False, True], ids=["hidden", "visible"])
@pytest.mark.parametrize("entry", ["typed", "edited", "deleted", "redo-pending"])
def test_package_clear_discards_native_password_undo_and_redo(app, package_input, visible, entry):
    widget, field, toggle, clear = package_input
    # Prepare redo with real Qt editing in Normal mode, then return to the
    # requested visibility before cleanup. No clipboard or custom shortcuts.
    toggle.setChecked(True if entry == "redo-pending" else visible)
    field.setFocus()
    QTest.keyClicks(field, "Synthetic-Package-Secret-001A")
    if entry == "edited":
        field.setSelection(10, 7)
        QTest.keyClicks(field, "Edited")
        QTest.keyClick(field, Qt.Key.Key_Backspace)
        assert field.text() and field.text() != "Synthetic-Package-Secret-001A"
    elif entry == "deleted":
        field.selectAll()
        QTest.keyClick(field, Qt.Key.Key_Backspace)
        assert not field.text()
    elif entry == "redo-pending":
        field.undo()
        assert not field.text() and field.isRedoAvailable()
    else:
        assert field.text() == "Synthetic-Package-Secret-001A"
    toggle.setChecked(visible)
    assert app.focusWidget() is field

    for _ in range(2):  # Repeated cleanup of an already-empty field is safe.
        clear()
        assert not field.text() and not toggle.isChecked()
        assert field.echoMode() == QLineEdit.EchoMode.Password
        assert app.focusWidget() is field
        # Password mode hides native history availability. Reveal it as a user
        # can, and attempt both actual keyboard shortcuts and Qt API actions.
        toggle.setChecked(True)
        undo_available, redo_available = field.isUndoAvailable(), field.isRedoAvailable()
        for key in (Qt.Key.Key_Z, Qt.Key.Key_Y):
            QTest.keyClick(field, key, Qt.KeyboardModifier.ControlModifier)
            assert not field.text(), "Cleared synthetic password recovered from native edit history"
        field.undo()
        assert not field.text()
        field.redo()
        assert not field.text()
        assert not undo_available and not redo_available
        assert not field.isUndoAvailable() and not field.isRedoAvailable()
        toggle.setChecked(False)
    assert widget.isEnabled()

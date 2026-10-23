"""Local labels must never replace Qt's semantic standard-button contracts."""

import pytest
from PySide6.QtCore import QCoreApplication, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox, QVBoxLayout

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.gui.dialog_localization import (
    _DialogButtonTranslator, install_dialog_button_translations, localize_standard_buttons,
)
from data_mask_studio.i18n import Translator


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-local"))
    return create_application([])


EXPECTED = {
    "en": {"Yes": "&Yes", "No": "&No", "Cancel": "Cancel", "Close": "Close",
           "Ok": "OK", "Retry": "&Retry"},
    "pt-BR": {"Yes": "&Sim", "No": "&Não", "Cancel": "Cancelar", "Close": "Fechar",
              "Ok": "OK", "Retry": "&Tentar novamente"},
}


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("widget_type", [QMessageBox, QDialogButtonBox])
def test_local_standard_labels_preserve_objects_roles_and_unrelated_actions(app, locale, widget_type):
    widget = widget_type()
    standards = widget_type.StandardButton
    names = (*EXPECTED[locale], "Save")
    flags = standards.NoButton
    for name in names:
        flags |= getattr(standards, name)
    widget.setStandardButtons(flags)
    original = {name: widget.button(getattr(standards, name)) for name in names}
    roles = {name: widget.buttonRole(button) for name, button in original.items()}
    save_label = original["Save"].text()
    clicks = []
    original["Retry"].clicked.connect(lambda: clicks.append("retry"))

    localize_standard_buttons(widget, Translator(locale))

    assert widget.standardButtons() == flags
    for name, button in original.items():
        assert widget.button(getattr(standards, name)) is button
        assert widget.standardButton(button) == getattr(standards, name)
        assert widget.buttonRole(button) == roles[name]
        assert button.text() == EXPECTED[locale].get(name, save_label)
    original["Retry"].click()
    assert clicks == ["retry"]
    # Explicit English labels never change the shared Portuguese Qt translator.
    assert QCoreApplication.translate("QPlatformTheme", "Cancel") == "Cancelar"
    assert QCoreApplication.translate("QPlatformTheme", "Retry") == "&Tentar novamente"
    assert QCoreApplication.translate("QPlatformTheme", "Save") == "Save"


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("interaction,expected", [
    ("enter", QMessageBox.StandardButton.No),
    ("escape", QMessageBox.StandardButton.No),
    ("no", QMessageBox.StandardButton.No),
    ("yes", QMessageBox.StandardButton.Yes),
])
def test_message_box_keeps_safe_default_escape_and_standard_results(app, locale, interaction, expected):
    box = QMessageBox(QMessageBox.Icon.Warning, "Synthetic confirmation", "Safe text")
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    box.setEscapeButton(QMessageBox.StandardButton.No)
    original_default, original_escape = box.defaultButton(), box.escapeButton()
    localize_standard_buttons(box, Translator(locale))
    assert box.defaultButton() is original_default
    assert box.escapeButton() is original_escape
    clicked = []
    box.buttonClicked.connect(lambda button: clicked.append(box.standardButton(button)))
    expired = []
    timeout = QTimer(box)
    timeout.setSingleShot(True)
    timeout.timeout.connect(lambda: (expired.append(True), box.reject()))
    timeout.start(5000)

    def reply():
        if interaction in ("enter", "escape"):
            QTest.keyClick(box, Qt.Key.Key_Return if interaction == "enter" else Qt.Key.Key_Escape)
        else:
            box.button(QMessageBox.StandardButton.Yes if interaction == "yes" else QMessageBox.StandardButton.No).click()

    QTimer.singleShot(0, reply)
    result = box.exec()
    timeout.stop()
    assert not expired
    assert result == expected
    assert clicked == [expected]


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
@pytest.mark.parametrize("key,expected", [
    (Qt.Key.Key_Return, QDialog.DialogCode.Accepted),
    (Qt.Key.Key_Escape, QDialog.DialogCode.Rejected),
])
def test_button_box_keeps_keyboard_accept_and_cancel_connections(app, locale, key, expected):
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    layout.addWidget(buttons)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    original_ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
    original_ok.setDefault(True)
    localize_standard_buttons(buttons, Translator(locale))
    dialog.show()
    app.processEvents()
    assert original_ok.isDefault()
    QTest.keyClick(dialog, key)
    assert dialog.result() == expected
    assert not dialog.isVisible()


def test_retranslation_keeps_existing_focus_button_identity_and_connections(app):
    box = QMessageBox()
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    box.setEscapeButton(QMessageBox.StandardButton.No)
    box.show()
    app.processEvents()
    no_button = box.button(QMessageBox.StandardButton.No)
    no_button.setFocus()
    focus = box.focusWidget()
    for locale in ("en", "pt-BR", "en"):
        localize_standard_buttons(box, Translator(locale))
        assert box.focusWidget() is focus
        assert box.defaultButton() is no_button
        assert box.escapeButton() is no_button
        assert box.button(QMessageBox.StandardButton.No) is no_button
        assert no_button.text() == EXPECTED[locale]["No"]


def test_installed_translator_is_reused_and_only_translates_owned_standard_actions(app):
    before = app.findChildren(_DialogButtonTranslator)
    assert len(before) == 1
    install_dialog_button_translations(app)
    assert app.findChildren(_DialogButtonTranslator) == before
    assert before[0].parent() is app
    for source, expected in (("&Yes", "&Sim"), ("&No", "&Não"), ("Retry", "&Tentar novamente"),
                             ("&Retry", "&Tentar novamente"), ("OK", "OK"), ("Cancel", "Cancelar"),
                             ("Close", "Fechar")):
        assert before[0].translate("QPlatformTheme", source) == expected
    assert before[0].translate("QPlatformTheme", "Save") is None
    assert before[0].translate("Other", "Cancel") is None
    assert QCoreApplication.translate("QLineEdit", "Cancel") == "Cancel"


def test_explicit_translator_is_not_installed_globally(app):
    before = app.findChildren(_DialogButtonTranslator)
    translator = _DialogButtonTranslator(translator=Translator("en"))
    assert translator.translate("QPlatformTheme", "&Yes") == "&Yes"
    assert translator.translate("QPlatformTheme", "Retry") == "&Retry"
    assert app.findChildren(_DialogButtonTranslator) == before
    assert QCoreApplication.translate("QPlatformTheme", "&Yes") == "&Sim"

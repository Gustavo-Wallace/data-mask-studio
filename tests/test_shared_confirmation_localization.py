"""Exercise the existing shared confirmation, not a parallel dialog framework."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget
from shiboken6 import isValid

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.app import create_application
from data_mask_studio.gui.anonymization_widget import AnonymizationWidget
from data_mask_studio.gui.components.presentation import confirm_destructive_action
from data_mask_studio.gui.components import presentation
from data_mask_studio.gui.dialog_localization import localize_standard_buttons
from data_mask_studio.i18n import Translator
from data_mask_studio.profiles import ProfileRepository, ProfileService
from test_dialog_consistency import modal_reply


@pytest.fixture
def gui(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-appdata"))
    app = create_application([])
    parent = QWidget()
    parent.show()
    app.processEvents()
    return app, parent


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("interaction, expected", [
    ("enter", QMessageBox.StandardButton.No),
    ("escape", QMessageBox.StandardButton.No),
    ("close", QMessageBox.StandardButton.No),
    ("yes", QMessageBox.StandardButton.Yes),
    ("mnemonic_yes", QMessageBox.StandardButton.Yes),
    ("mnemonic_no", QMessageBox.StandardButton.No),
])
def test_shared_confirmation_explicit_locale_keeps_safe_results_and_literal_user_data(
    gui, locale, interaction, expected,
):
    app, parent = gui
    translator = Translator(locale)
    title = "Synthetic confirmation" if locale == "en" else "Confirmação sintética"
    text = '<img src="synthetic-only"> & <b>synthetic</b>\nC:/synthetic/file.csv'
    boxes = []

    def reply():
        box = app.activeModalWidget()
        boxes.append(box)
        assert box.parentWidget() is parent
        assert box.windowModality() == Qt.WindowModality.ApplicationModal
        assert box.windowTitle() == title and box.text() == text
        assert box.textFormat() == Qt.TextFormat.PlainText
        label = box.findChild(QLabel, "qt_msgbox_label")
        assert label.text() == text and label.textFormat() == Qt.TextFormat.PlainText
        assert box.icon() == QMessageBox.Icon.Warning
        assert box.standardButtons() == QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        assert box.standardButton(box.defaultButton()) == QMessageBox.StandardButton.No
        assert box.standardButton(box.escapeButton()) == QMessageBox.StandardButton.No
        yes = box.button(QMessageBox.StandardButton.Yes)
        no = box.button(QMessageBox.StandardButton.No)
        assert yes.property("role") == "destructive" and no.property("role") is None
        assert yes.text() == translator.text("dialog.button.yes")
        assert no.text() == translator.text("dialog.button.no")
        assert box.buttonRole(yes) == QMessageBox.ButtonRole.YesRole
        assert box.buttonRole(no) == QMessageBox.ButtonRole.NoRole
        if interaction == "yes":
            yes.click()
        elif interaction.startswith("mnemonic_"):
            box.activateWindow()
            app.processEvents()
            key = Qt.Key.Key_N if interaction == "mnemonic_no" else (
                Qt.Key.Key_Y if locale == "en" else Qt.Key.Key_S
            )
            QTest.keyClick(box, key, Qt.KeyboardModifier.AltModifier)
        elif interaction == "close":
            box.close()
        else:
            QTest.keyClick(box, Qt.Key.Key_Return if interaction == "enter" else Qt.Key.Key_Escape)

    with modal_reply(parent, reply):
        result = confirm_destructive_action(parent, title, text, translator=translator)
    assert type(result) is int and result == expected
    assert QCoreApplication.translate("QPlatformTheme", "&Yes") == "&Sim"
    QCoreApplication.sendPostedEvents(boxes[0], QEvent.Type.DeferredDelete)
    assert not isValid(boxes[0])


def test_existing_profile_confirmation_treats_user_name_as_plain_text_and_cancel_preserves_it(gui, tmp_path):
    app, _ = gui
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile = service.create("<b>Synthetic & profile</b>", [ColumnConfig("Nome")])
    widget = AnonymizationWidget(profile_service=service)
    widget.refresh_profiles(profile.identifier)
    before = (tmp_path / "profiles.json").read_bytes()
    observed = []

    def cancel():
        box = app.activeModalWidget()
        observed.append(box.text())
        assert box.textFormat() == Qt.TextFormat.PlainText
        assert box.findChild(QLabel, "qt_msgbox_label").textFormat() == Qt.TextFormat.PlainText
        QTest.keyClick(box, Qt.Key.Key_Escape)

    with modal_reply(widget, cancel):
        widget.delete_selected_profile()
    assert observed == [f"Excluir somente o perfil “{profile.name}”?"]
    assert service.list_profiles() == [profile]
    assert (tmp_path / "profiles.json").read_bytes() == before
    assert widget.profile_combo.currentData() == profile.identifier


def test_shared_confirmation_sets_plain_text_before_passing_untrusted_content_to_qt(gui, monkeypatch):
    app, parent = gui
    text = '<img src="synthetic-untrusted-reference"> & literal'
    observed = []

    class RecordingMessageBox(QMessageBox):
        def __init__(self, icon, title, initial_text, *args):
            assert initial_text == ""  # Qt must not parse caller data as AutoText first.
            super().__init__(icon, title, initial_text, *args)

        def setText(self, value):
            assert self.textFormat() == Qt.TextFormat.PlainText
            observed.append(value)
            super().setText(value)

    monkeypatch.setattr(presentation, "QMessageBox", RecordingMessageBox)

    def cancel():
        box = app.activeModalWidget()
        assert box.text() == text
        QTest.keyClick(box, Qt.Key.Key_Escape)

    with modal_reply(parent, cancel):
        result = confirm_destructive_action(parent, "Synthetic", text, translator=Translator("en"))
    assert observed == [text]
    assert result == QMessageBox.StandardButton.No


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
@pytest.mark.parametrize("icon", [QMessageBox.Icon.Critical, QMessageBox.Icon.Information])
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Escape])
def test_common_error_and_information_buttons_keep_severity_and_cancel_results(gui, locale, icon, key):
    app, parent = gui
    translator = Translator(locale)
    box = QMessageBox(icon, "Synthetic message", "Unchanged caller-owned message", parent=parent)
    box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(QMessageBox.StandardButton.Cancel)
    box.setEscapeButton(QMessageBox.StandardButton.Cancel)
    localize_standard_buttons(box, translator)
    assert box.icon() == icon and box.text() == "Unchanged caller-owned message"
    assert box.button(QMessageBox.StandardButton.Cancel).text() == translator.text("dialog.button.cancel")
    assert box.button(QMessageBox.StandardButton.Ok).text() == translator.text("dialog.button.ok")

    def cancel():
        assert app.activeModalWidget() is box
        QTest.keyClick(box, key)

    with modal_reply(parent, cancel):
        result = box.exec()
    assert result == QMessageBox.StandardButton.Cancel
    assert QCoreApplication.translate("QPlatformTheme", "Cancel") == "Cancelar"

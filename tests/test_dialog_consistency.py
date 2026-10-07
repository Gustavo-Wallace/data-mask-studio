"""Native Qt dialog contracts; no screenshots or pixel-perfect assertions."""

import os
from contextlib import contextmanager

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QTimer, QTranslator, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QInputDialog, QLabel, QMessageBox, QWidget,
)
from shiboken6 import isValid

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.anonymization import ColumnConfig
from data_mask_studio.csv_tools import inspect_csv
from data_mask_studio.detection import ColumnSuggestion, ConfidenceLevel, SuggestedType
from data_mask_studio.gui.about_dialog import AboutDialog
from data_mask_studio.gui.anonymization_widget import AnonymizationWidget
from data_mask_studio.gui.components.presentation import confirm_destructive_action
from data_mask_studio.gui.composite_dialog import CompositeDialog
from data_mask_studio.gui.detection_dialog import DetectionDialog
from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.normalization import NormalizationRule
from data_mask_studio.profiles import ProfileRepository, ProfileService


@pytest.fixture
def gui(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-local"))
    app = create_application([])
    parent = QWidget()
    parent.show()
    app.processEvents()
    return app, parent


@contextmanager
def modal_reply(parent, callback):
    # Guard the nested modal loop so a broken keyboard/default contract fails
    # rather than leaving an unattended CI dialog open indefinitely.
    timeout = QTimer(parent)
    timeout.setSingleShot(True)
    expired = []

    def abort():
        expired.append(True)
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QDialog):
            dialog.reject()

    timeout.timeout.connect(abort)
    timeout.start(5000)
    QTimer.singleShot(0, callback)
    try:
        yield
    finally:
        timeout.stop()
        timeout.deleteLater()
    assert not expired, "The dialog did not honor its terminal interaction"


@pytest.mark.parametrize("method,icon,buttons,default", [
    ("information", QMessageBox.Icon.Information, QMessageBox.StandardButton.Ok,
     QMessageBox.StandardButton.Ok),
    ("warning", QMessageBox.Icon.Warning,
     QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
     QMessageBox.StandardButton.Cancel),
    ("critical", QMessageBox.Icon.Critical, QMessageBox.StandardButton.Ok,
     QMessageBox.StandardButton.Ok),
    ("question", QMessageBox.Icon.Question,
     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
     QMessageBox.StandardButton.No),
])
@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Escape])
def test_standard_message_roles_keep_modality_buttons_and_safe_keyboard_results(
    gui, method, icon, buttons, default, key,
):
    app, parent = gui
    observed = {}
    expected_labels = {
        QMessageBox.StandardButton.Yes: "Sim", QMessageBox.StandardButton.No: "Não",
        QMessageBox.StandardButton.Cancel: "Cancelar", QMessageBox.StandardButton.Ok: "OK",
    }

    def reply():
        box = app.activeModalWidget()
        try:
            observed.update(icon=box.icon(), buttons=box.standardButtons(),
                            default=box.standardButton(box.defaultButton()),
                            modality=box.windowModality(),
                            labels={box.standardButton(button): button.text().replace("&", "")
                                    for button in box.buttons()})
            QTest.keyClick(box, key)
        finally:
            if box.isVisible():
                box.reject()

    with modal_reply(parent, reply):
        result = getattr(QMessageBox, method)(parent, "Synthetic dialog", "Safe text", buttons, default)
    assert observed == dict(icon=icon, buttons=buttons, default=default,
                            modality=Qt.WindowModality.ApplicationModal,
                            labels={button: label for button, label in expected_labels.items() if buttons & button})
    assert result == default
    assert not app.activeModalWidget()


@pytest.mark.parametrize("interaction,expected", [
    ("enter", QMessageBox.StandardButton.No),
    ("escape", QMessageBox.StandardButton.No),
    ("close", QMessageBox.StandardButton.No),
    ("yes", QMessageBox.StandardButton.Yes),
    ("mnemonic_yes", QMessageBox.StandardButton.Yes),
    ("mnemonic_no", QMessageBox.StandardButton.No),
])
def test_destructive_confirmation_keeps_standard_results_and_safe_default(gui, interaction, expected):
    app, parent = gui
    observed = {}
    boxes = []
    text = "Substituir arquivo sintético?\nC:/synthetic/" + "long-directory/" * 30 + "output.csv"

    def reply():
        box = app.activeModalWidget()
        boxes.append(box)
        try:
            label = box.findChild(QLabel, "qt_msgbox_label")
            observed.update(
                icon=box.icon(), text=box.text(), wrapping=label.wordWrap(),
                buttons=box.standardButtons(), default=box.standardButton(box.defaultButton()),
                escape=box.standardButton(box.escapeButton()),
                modality=box.windowModality(), yes_role=box.button(QMessageBox.StandardButton.Yes).property("role"),
                no_role=box.button(QMessageBox.StandardButton.No).property("role"),
                labels=[box.button(button).text().replace("&", "")
                        for button in (QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No)],
                buttons_fit=all(box.rect().contains(QRect(button.mapTo(box, QPoint()), button.size()))
                                for button in box.buttons()),
                within_screen=box.width() <= box.screen().availableGeometry().width(),
            )
            if interaction == "yes":
                box.button(QMessageBox.StandardButton.Yes).click()
            elif interaction.startswith("mnemonic_"):
                box.activateWindow()
                app.processEvents()
                QTest.keyClick(box, Qt.Key.Key_S if interaction == "mnemonic_yes" else Qt.Key.Key_N,
                               Qt.KeyboardModifier.AltModifier)
            elif interaction == "close":
                box.close()
            else:
                QTest.keyClick(box, Qt.Key.Key_Return if interaction == "enter" else Qt.Key.Key_Escape)
        finally:
            # Native mnemonic activation uses QAbstractButton.animateClick;
            # let the modal loop deliver it rather than rejecting too early.
            if box.isVisible() and not interaction.startswith("mnemonic_"):
                box.reject()

    with modal_reply(parent, reply):
        result = confirm_destructive_action(parent, "Confirmar substituição", text)
    assert type(result) is int  # Same raw return type as the Qt static methods.
    assert result == expected
    assert observed == dict(
        icon=QMessageBox.Icon.Warning, text=text, wrapping=True,
        buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        default=QMessageBox.StandardButton.No, escape=QMessageBox.StandardButton.No,
        modality=Qt.WindowModality.ApplicationModal, yes_role="destructive", no_role=None,
        labels=["Sim", "Não"],
        buttons_fit=True, within_screen=True,
    )
    QCoreApplication.sendPostedEvents(boxes[0], QEvent.Type.DeferredDelete)
    assert not isValid(boxes[0])  # Closed confirmations must not accumulate.


@pytest.mark.parametrize("kind", ["about", "composite", "detection"])
def test_custom_dialogs_share_compact_spacing_without_changing_modality(gui, tmp_path, kind):
    app, parent = gui
    source = tmp_path / "synthetic.csv"
    source.write_text("Nome,Cidade\nSynthetic,Town\n", encoding="utf-8")
    if kind == "about":
        dialog = AboutDialog(parent)
    elif kind == "composite":
        dialog = CompositeDialog(inspect_csv(source), lambda config: None, parent=parent)
    else:
        dialog = DetectionDialog((), 0, parent)
    dialog.show()
    app.processEvents()
    margins = dialog.layout().contentsMargins()
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (METRICS["dialog_margin"],) * 4
    assert dialog.layout().spacing() == METRICS["panel_spacing"]
    assert dialog.windowModality() == (
        Qt.WindowModality.ApplicationModal if kind == "about" else Qt.WindowModality.NonModal
    )
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible() and dialog.result() == QDialog.DialogCode.Rejected


def test_composite_error_and_primary_button_keep_real_planner_validation(gui, tmp_path):
    app, parent = gui
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    widget = AnonymizationWidget(profile_service=service)
    source = tmp_path / "synthetic.csv"
    source.write_text("Nome,Cidade\nSynthetic,Town\n", encoding="utf-8")
    widget.load_csv(str(source))
    dialog = CompositeDialog(widget._inspection_result, lambda c: widget._build_current_plan((c,)), parent=parent)
    dialog.show()
    app.processEvents()
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert save.property("role") == "primary" and save.text() == "Criar"
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).text() == "Cancelar"
    assert dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).property("role") is None
    dialog.accept()
    assert dialog.isVisible() and dialog.result_config is None
    assert dialog.error_label.text() and dialog.error_label.property("feedbackState") == "error"
    assert dialog.error_label.wordWrap() and dialog.error_label.textFormat() == Qt.TextFormat.PlainText
    dialog.name_field.setText("PESSOA")
    save.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.result_config.output_name == "PESSOA"


def test_detection_buttons_fit_and_keep_default_signals_and_focus_order(gui):
    app, parent = gui
    suggestion = ColumnSuggestion("Email", SuggestedType.EMAIL, True, "EMAIL",
                                  NormalizationRule.EXACT, ConfidenceLevel.HIGH, "Synthetic explanation", 1, 1)
    dialog = DetectionDialog((suggestion,), 1, parent)
    requests = []
    dialog.bulk_requested.connect(requests.append)
    dialog.show()
    app.processEvents()
    assert dialog.width() <= dialog.screen().availableGeometry().width()
    # As before, Qt initially makes the first per-row action auto-default.
    assert dialog.table.cellWidget(0, 8).isDefault()
    assert dialog.apply_high_button.property("role") == "primary"
    for button in (dialog.apply_high_button, dialog.apply_accepted_button, dialog.clear_button, dialog.close_button):
        assert dialog.rect().contains(button.geometry())
        assert button.width() >= button.sizeHint().width()
    dialog.apply_high_button.setFocus()
    assert dialog.apply_high_button.isDefault()
    QTest.keyClick(dialog.apply_high_button, Qt.Key.Key_Return)
    assert requests == [(0,)]
    QTest.keyClick(dialog.apply_high_button, Qt.Key.Key_Tab)
    assert dialog.apply_accepted_button.hasFocus()
    QTest.keyClick(dialog.apply_accepted_button, Qt.Key.Key_Tab)
    assert dialog.clear_button.hasFocus()
    QTest.keyClick(dialog.clear_button, Qt.Key.Key_Tab)
    assert dialog.close_button.hasFocus()
    assert dialog.isVisible()  # Applying suggestions does not change modeless behavior.


def test_about_close_button_keeps_enter_result_and_security_notice(gui):
    app, parent = gui
    dialog = AboutDialog(parent)
    dialog.show()
    app.processEvents()
    close = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Close)
    assert close.isDefault()
    assert close.text() == "Fechar"
    assert dialog.findChild(QDialogButtonBox).buttonRole(close) == QDialogButtonBox.ButtonRole.RejectRole
    notice = dialog.findChild(QLabel, "aboutSignatureNotice")
    assert notice.wordWrap() and "não possuem assinatura digital" in notice.text()
    QTest.keyClick(dialog, Qt.Key.Key_Return)
    assert dialog.result() == QDialog.DialogCode.Rejected and not dialog.isVisible()


def test_profile_deletion_escape_preserves_profile(gui, tmp_path):
    app, parent = gui
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    profile = service.create("Synthetic profile", [ColumnConfig("Nome")])
    widget = AnonymizationWidget(profile_service=service)
    widget.refresh_profiles(profile.identifier)
    observed = []

    def cancel():
        box = app.activeModalWidget()
        observed.append(box.windowTitle())
        QTest.keyClick(box, Qt.Key.Key_Escape)

    with modal_reply(parent, cancel):
        widget.delete_selected_profile()
    assert observed == ["Excluir perfil"]
    assert service.list_profiles() == [profile]
    assert widget.profile_combo.currentData() == profile.identifier


def test_masking_overwrite_refusal_preserves_existing_output(gui, tmp_path, monkeypatch):
    app, parent = gui
    service = ProfileService(ProfileRepository(tmp_path / "profiles.json"))
    widget = AnonymizationWidget(profile_service=service)
    source = tmp_path / "synthetic.csv"
    source.write_text("Nome,Cidade\nSynthetic,Town\n", encoding="utf-8")
    destination = tmp_path / "existing.csv"
    destination.write_bytes(b"existing synthetic output")
    widget.load_csv(str(source))
    widget.select_all_columns()
    widget.validate_current_configuration()
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(destination), ""))
    observed = []

    def cancel():
        box = app.activeModalWidget()
        observed.append(box.windowTitle())
        QTest.keyClick(box, Qt.Key.Key_Return)

    with modal_reply(parent, cancel):
        widget._choose_output_file()
    assert observed == ["Confirmar substituição"]
    assert widget._worker is None
    assert destination.read_bytes() == b"existing synthetic output"
    assert "não foi alterado" in widget.status_label.text()


def test_localization_is_reused_and_does_not_translate_unrelated_qt_text(gui):
    app, _ = gui
    translators = app.findChildren(QTranslator)
    assert len(translators) == 1
    assert translators[0].parent() is app
    assert create_application([]) is app
    assert app.findChildren(QTranslator) == translators
    assert QCoreApplication.translate("QLineEdit", "Cancel") == "Cancel"
    assert QCoreApplication.translate("QLineEdit", "&Copy") == "&Copy"
    assert QCoreApplication.translate("QPlatformTheme", "Save") == "Save"
    assert QCoreApplication.translate("Other", "Close") == "Close"


def test_input_dialog_uses_localized_actions_without_changing_results(gui):
    app, parent = gui
    observed = []

    def cancel():
        dialog = app.activeModalWidget()
        observed.append((dialog.okButtonText(), dialog.cancelButtonText()))
        QTest.keyClick(dialog, Qt.Key.Key_Escape)

    with modal_reply(parent, cancel):
        value, accepted = QInputDialog.getText(parent, "Nome do perfil", "Nome:", text="Synthetic profile")
    assert observed == [("OK", "Cancelar")]
    assert not accepted and value == ""


def test_localized_button_box_preserves_roles_and_standard_button_identity(gui):
    _, parent = gui
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
        | QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Close, parent,
    )
    expected = [
        (QDialogButtonBox.StandardButton.Yes, "Sim", QDialogButtonBox.ButtonRole.YesRole),
        (QDialogButtonBox.StandardButton.No, "Não", QDialogButtonBox.ButtonRole.NoRole),
        (QDialogButtonBox.StandardButton.Cancel, "Cancelar", QDialogButtonBox.ButtonRole.RejectRole),
        (QDialogButtonBox.StandardButton.Close, "Fechar", QDialogButtonBox.ButtonRole.RejectRole),
    ]
    for standard, label, role in expected:
        button = buttons.button(standard)
        assert button.text().replace("&", "") == label
        assert buttons.standardButton(button) == standard
        assert buttons.buttonRole(button) == role

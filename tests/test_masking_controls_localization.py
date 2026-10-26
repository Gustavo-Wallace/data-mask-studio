"""Individual masking entry points translate without resetting execution inputs."""

from pathlib import Path

import pytest
from PySide6.QtWidgets import QFileDialog, QLineEdit

from qt_lifecycle import qt_widget_lifecycle
from data_mask_studio.app import create_application
from data_mask_studio.csv_tools.csv_anonymizer import CSVAnonymizationError
from data_mask_studio.gui.profile_controls import ProfileControls
from data_mask_studio.gui.transfer_package_controls import TransferPackageControls
from data_mask_studio.i18n import Translator
from data_mask_studio.transfer_package.models import PackageError


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "isolated-localappdata"))
    return create_application([])


def profile_controls(translator=None):
    calls = []
    widget = ProfileControls(
        apply_profile=lambda: calls.append("apply"),
        save_profile=lambda: calls.append("save"),
        update_profile=lambda: calls.append("update"),
        rename_profile=lambda: calls.append("rename"),
        delete_profile=lambda: calls.append("delete"),
        selection_changed=lambda *args: calls.append("selection"),
        translator=translator,
    )
    return widget, calls


def enabled_package_controls(tmp_path, translator=None):
    widget = TransferPackageControls(translator=translator)
    widget.set_mask_available(True)
    widget.set_output_path(tmp_path / "synthetic_masked.csv")
    widget.checkbox.setChecked(True)
    widget.password.setText("synthetic password")
    widget.confirmation.setText("synthetic password")
    return widget


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_profile_entry_points_translate_without_changing_selection_or_callbacks(app, locale):
    widget, calls = profile_controls()
    widget.profile_combo.addItem("User profile name", "stable-profile-id")
    calls.clear()
    combo = widget.profile_combo
    widget.retranslate(Translator(locale))

    assert widget.profile_combo is combo
    assert combo.currentText() == "User profile name"
    assert combo.currentData() == "stable-profile-id"
    assert calls == []
    expected = (
        ("Perfil de configuração", "Aplicar perfil", "Salvar como perfil", "Atualizar perfil", "Renomear", "Excluir")
        if locale == "pt-BR" else
        ("Configuration profile", "Apply profile", "Save as profile", "Update profile", "Rename", "Delete")
    )
    assert widget.heading_label.text() == expected[0]
    assert combo.accessibleName() == expected[0]
    buttons = (widget.apply_button, widget.save_button, widget.update_button,
               widget.rename_button, widget.delete_button)
    assert tuple(button.text() for button in buttons) == expected[1:]
    for button in buttons:
        button.click()
    assert calls == ["apply", "save", "update", "rename", "delete"]


def test_package_controls_default_to_portuguese(app):
    widget = TransferPackageControls()
    assert widget.checkbox.text() == "Gerar pacote de transferência"
    assert widget.show_passwords.text() == "Mostrar senhas"
    assert widget.password.accessibleName() == "Senha do pacote"
    assert not widget.checkbox.isChecked()
    assert widget.password.echoMode() == QLineEdit.EchoMode.Password


@pytest.mark.parametrize("manual", [False, True])
def test_package_retranslation_preserves_transient_inputs_and_destination_policy(app, tmp_path, manual):
    widget = enabled_package_controls(tmp_path)
    if manual:
        widget.destination.setText(str(tmp_path / "chosen.dmspackage"))
    widget.show_passwords.setChecked(True)
    original_fields = (widget.password, widget.confirmation, widget.destination)
    before = (
        tuple(field.text() for field in original_fields), widget._manual_destination,
        widget.checkbox.isChecked(), widget.fields.isEnabled(),
    )
    emitted = []
    widget.checkbox.toggled.connect(emitted.append)
    widget.destination.textChanged.connect(emitted.append)
    widget.password.textChanged.connect(emitted.append)
    widget.confirmation.textChanged.connect(emitted.append)
    for locale in ("en", "pt-BR", "en"):
        widget.retranslate(Translator(locale))
        assert (widget.password, widget.confirmation, widget.destination) == original_fields
        assert (
            tuple(field.text() for field in original_fields), widget._manual_destination,
            widget.checkbox.isChecked(), widget.fields.isEnabled(),
        ) == before
        assert widget.show_passwords.isChecked()
        assert widget.password.echoMode() == widget.confirmation.echoMode() == QLineEdit.EchoMode.Normal
    assert emitted == []
    assert widget.checkbox.text() == "Generate transfer package"
    assert widget.browse_button.text() == "Choose destination"
    assert widget.destination_label.text() == "Package (.dmspackage):"
    assert widget.password_label.text() == "Password:"
    assert widget.confirmation_label.text() == "Confirm password:"
    assert widget.show_passwords.text() == "Show passwords"
    assert widget.destination.accessibleName() == "Transfer package destination"
    assert widget.password.accessibleName() == "Package password"
    assert widget.confirmation.accessibleName() == "Confirm package password"
    new_output = tmp_path / "next_masked.csv"
    widget.set_output_path(new_output)
    expected = tmp_path / "chosen.dmspackage" if manual else new_output.with_suffix(".dmspackage")
    assert Path(widget.destination.text()) == expected
    widget.reset()
    assert not widget.show_passwords.isChecked()
    assert not widget.password.text() and not widget.confirmation.text()
    assert widget.password.echoMode() == widget.confirmation.echoMode() == QLineEdit.EchoMode.Password


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_package_chooser_localizes_description_only_and_cancel_keeps_inputs(app, tmp_path, monkeypatch, locale):
    widget = enabled_package_controls(tmp_path, Translator(locale))
    calls = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (calls.append(args) or ("", "")))
    before = (widget.destination.text(), widget.password.text(), widget._manual_destination)
    widget._browse()
    expected_title = "Salvar pacote de transferência" if locale == "pt-BR" else "Save transfer package"
    expected_filter = "Pacotes de transferência (*.dmspackage)" if locale == "pt-BR" else "Transfer packages (*.dmspackage)"
    assert calls == [(widget, expected_title, before[0], expected_filter)]
    assert (widget.destination.text(), widget.password.text(), widget._manual_destination) == before


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_package_widget_owned_errors_are_localized_but_backend_errors_unchanged(app, tmp_path, locale):
    widget = enabled_package_controls(tmp_path, Translator(locale))
    source, output = tmp_path / "source.csv", tmp_path / "synthetic_masked.csv"
    with pytest.raises(CSVAnonymizationError) as missing_mask:
        widget.request(source, output, False)
    assert str(missing_mask.value) == (
        "O pacote exige ao menos uma coluna com ação Mascarar." if locale == "pt-BR" else
        "The package requires at least one column with the Mask action."
    )
    widget.confirmation.setText("different synthetic password")
    with pytest.raises(PackageError) as mismatch:
        widget.request(source, output, True)
    assert str(mismatch.value) == (
        "A confirmação da senha não corresponde à senha do pacote." if locale == "pt-BR" else
        "Password confirmation does not match the package password."
    )
    widget.password.setText("short")
    with pytest.raises(PackageError) as backend_error:
        widget.request(source, output, True)
    # Free-form service errors have no typed localization contract yet.
    assert str(backend_error.value) == "A senha do pacote deve possuir pelo menos 8 caracteres."


@pytest.mark.parametrize("locale", ["pt-BR", "en"])
def test_localized_package_request_retains_concrete_backend_inputs(app, tmp_path, locale):
    widget = enabled_package_controls(tmp_path, Translator(locale))
    source, output = tmp_path / "source.csv", tmp_path / "synthetic_masked.csv"
    result = widget.request(source, output, True)
    assert result["transfer_package_request"].password == "synthetic password"
    assert result["transfer_package_destination"] == output.with_suffix(".dmspackage").resolve()
    assert not output.exists()

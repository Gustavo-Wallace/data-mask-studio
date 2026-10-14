import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from qt_lifecycle import qt_widget_lifecycle

from PySide6.QtWidgets import QLineEdit, QMessageBox

from data_mask_studio.app import create_application
from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.gui.backup_widget import BackupWidget
from data_mask_studio.security import LocalKeyProvider
from data_mask_studio.vault import VaultCipher, VaultRepository

PASSWORD = "frase-senha longa e unica"


class FakeProtector:
    def __init__(self) -> None:
        self.counter = 0

    def protect(self, data: bytes) -> bytes:
        self.counter += 1
        return b"FAKE" + bytes([self.counter]) + data

    def unprotect(self, data: bytes) -> bytes:
        if not data.startswith(b"FAKE"):
            raise RuntimeError
        return data[5:]


def prepare_widget(tmp_path: Path) -> tuple[BackupWidget, EnvironmentPaths]:
    directory = tmp_path / "environment"
    directory.mkdir()
    paths = EnvironmentPaths(
        directory,
        directory / "secret.key",
        directory / "vault_key.dpapi",
        directory / "vault.db",
        directory / "profiles.json",
    )
    protector = FakeProtector()
    hmac_provider = LocalKeyProvider(
        directory, protector, key_file_name=paths.hmac_key_path.name
    )
    vault_provider = LocalKeyProvider(
        directory, protector, key_file_name=paths.vault_key_path.name
    )
    paths.hmac_key_path.write_bytes(protector.protect(b"H" * 32))
    paths.vault_key_path.write_bytes(protector.protect(b"V" * 32))
    VaultRepository(paths.vault_database_path, VaultCipher(b"V" * 32))
    return BackupWidget(paths, hmac_provider, vault_provider, protector), paths


def test_backup_widget_creates_validates_and_requires_restore_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    application = create_application([])
    widget, _ = prepare_widget(tmp_path)
    destination = tmp_path / "portable.dmsbackup"
    widget.destination_field.setText(str(destination))
    widget.create_password_field.setText(PASSWORD)
    widget.confirm_password_field.setText(PASSWORD)

    widget.create_button.click()
    creation_worker = widget._worker
    assert creation_worker is not None and creation_worker.wait(10000)
    application.processEvents()

    assert destination.exists()
    assert widget.create_password_field.text() == ""
    assert widget.confirm_password_field.text() == ""
    assert widget.open_folder_button.isEnabled()
    assert widget.create_status.property("feedbackState") == "success"

    widget.restore_file_field.setText(str(destination))
    widget.restore_password_field.setText(PASSWORD)
    assert not widget.restore_button.isEnabled()
    widget.validate_backup_button.click()
    validation_worker = widget._worker
    assert validation_worker is not None and validation_worker.wait(10000)
    application.processEvents()

    assert widget.restore_button.isEnabled()
    assert "Versão do formato: 1" in widget.restore_summary.toPlainText()
    assert widget.restore_status.property("feedbackState") == "success"
    widget.show_restore_password.setChecked(True)
    assert widget.restore_password_field.echoMode() is QLineEdit.EchoMode.Normal

    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    widget.restore_button.click()
    assert widget._worker is None

    restored: list[bool] = []
    widget.environment_restored.connect(lambda: restored.append(True))
    monkeypatch.setattr(
        QMessageBox,
        "exec",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    widget.restore_button.click()
    restore_worker = widget._worker
    assert restore_worker is not None and restore_worker.wait(10000)
    application.processEvents()

    assert restored == [True]
    assert widget.restore_password_field.text() == ""
    assert not widget.restore_button.isEnabled()

    widget.stop_worker()
    assert widget.restore_status.property("feedbackState") == "success"
    assert widget.restore_password_field.text() == ""
    widget.close()
    application.quit()


def test_backup_widget_rejects_short_password_and_disables_restore(tmp_path: Path) -> None:
    application = create_application([])
    widget, _ = prepare_widget(tmp_path)
    widget.destination_field.setText(str(tmp_path / "backup.dmsbackup"))
    widget.create_password_field.setText("curta")
    widget.confirm_password_field.setText("curta")

    widget.create_button.click()

    assert widget._worker is None
    assert "12 caracteres" in widget.create_status.text()
    assert not widget.restore_button.isEnabled()
    widget.close()
    application.quit()


@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
def test_existing_backup_keeps_explicit_overwrite_confirmation(tmp_path: Path, monkeypatch, answer) -> None:
    application = create_application([])
    widget, _ = prepare_widget(tmp_path)
    destination = tmp_path / "existing.dmsbackup"
    destination.write_bytes(b"existing synthetic backup")
    widget.destination_field.setText(str(destination))
    widget.create_password_field.setText(PASSWORD)
    widget.confirm_password_field.setText(PASSWORD)
    observed = []

    def confirm(box):
        observed.append(box.windowTitle())
        assert box.icon() == QMessageBox.Icon.Warning
        assert box.standardButton(box.defaultButton()) == QMessageBox.StandardButton.No
        assert box.standardButton(box.escapeButton()) == QMessageBox.StandardButton.No
        assert box.button(QMessageBox.StandardButton.Yes).property("role") == "destructive"
        return answer

    monkeypatch.setattr(QMessageBox, "exec", confirm)
    widget.start_creation()
    assert observed == ["Substituir backup"]
    if answer == QMessageBox.StandardButton.Yes:
        worker = widget._worker
        assert worker is not None and worker.wait(10000)
        application.processEvents()
        assert destination.read_bytes() != b"existing synthetic backup"
        assert widget.create_status.property("feedbackState") == "success"
        assert widget.create_password_field.text() == widget.confirm_password_field.text() == ""
    else:
        assert widget._worker is None
        assert destination.read_bytes() == b"existing synthetic backup"

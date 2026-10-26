"""Execution-only inputs; never part of profiles or settings."""

from pathlib import Path
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QWidget, QCheckBox, QLineEdit, QLabel, QPushButton, QVBoxLayout, QFormLayout, QHBoxLayout, QFileDialog

from data_mask_studio.csv_tools.csv_anonymizer import validate_transfer_package_destination, CSVAnonymizationError
from data_mask_studio.transfer_package.service import validate_password
from data_mask_studio.transfer_package.models import PackageError
from data_mask_studio.transfer_package.staging import PackageStagingRequest
from data_mask_studio.i18n import Translator


class TransferPackageControls(QWidget):
    def __init__(self, parent=None, *, translator: Translator | None = None):
        super().__init__(parent)
        self._translator = translator or Translator("pt-BR")
        self.output_path: Path | None = None
        self._manual_destination = False
        self.checkbox = QCheckBox(self)
        self.checkbox.setEnabled(False)
        self.fields = QWidget(self)
        self.destination = QLineEdit(self)
        self.browse_button = QPushButton(self)
        self.password = QLineEdit(self)
        self.confirmation = QLineEdit(self)
        for field in (self.password, self.confirmation):
            field.setEchoMode(QLineEdit.EchoMode.Password)
        self.destination_label = QLabel(self)
        self.password_label = QLabel(self)
        self.password_label.setBuddy(self.password)
        self.confirmation_label = QLabel(self)
        self.confirmation_label.setBuddy(self.confirmation)
        form = QFormLayout(self.fields)
        path_row = QHBoxLayout()
        path_row.addWidget(self.destination)
        path_row.addWidget(self.browse_button)
        form.addRow(self.destination_label, path_row)
        form.addRow(self.password_label, self.password)
        form.addRow(self.confirmation_label, self.confirmation)
        self.show_passwords = QCheckBox(self)
        self.show_passwords.toggled.connect(self._set_password_visibility)
        form.addRow("", self.show_passwords)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.fields)
        self.checkbox.toggled.connect(self._toggled)
        self.browse_button.clicked.connect(self._browse)
        self.destination.textChanged.connect(self._destination_changed)
        self.retranslate(self._translator)
        self._toggled(False)

    def retranslate(self, translator: Translator) -> None:
        """Translate presentation only, retaining transient execution inputs."""
        self._translator = translator
        for widget, message_id in (
            (self.checkbox, "masking.package.enabled"),
            (self.browse_button, "masking.package.browse"),
            (self.destination_label, "masking.package.destination_label"),
            (self.password_label, "masking.package.password_label"),
            (self.confirmation_label, "masking.package.confirmation_label"),
            (self.show_passwords, "masking.package.show_passwords"),
        ):
            widget.setText(translator.text(message_id))
        for field, message_id in (
            (self.destination, "masking.package.destination_accessible"),
            (self.password, "masking.package.password_accessible"),
            (self.confirmation, "masking.package.confirmation_accessible"),
        ):
            field.setAccessibleName(translator.text(message_id))

    def _toggled(self, checked):
        self.fields.setEnabled(checked)
        self.fields.setVisible(checked)
        if not checked:
            self.clear_passwords()
        else:
            self._update_destination()

    def _destination_changed(self, text: str):
        self._manual_destination = bool(text.strip())

    def set_output_path(self, output: Path):
        self.output_path = Path(output)
        self._update_destination()

    def _update_destination(self):
        if (self.checkbox.isChecked() and self.output_path is not None
                and (not self._manual_destination or not self.destination.text().strip())):
            blocker = QSignalBlocker(self.destination)
            self.destination.setText(str(self.output_path.with_suffix(".dmspackage")))
            del blocker
            self._manual_destination = False

    def _set_password_visibility(self, visible: bool):
        mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        self.password.setEchoMode(mode)
        self.confirmation.setEchoMode(mode)

    def clear_passwords(self):
        self.show_passwords.setChecked(False)
        self.password.clear()
        self.confirmation.clear()

    def reset(self):
        self.checkbox.setChecked(False)
        self.clear_passwords()
        self.destination.clear()
        self._manual_destination = False
        self.output_path = None
        self.checkbox.setEnabled(False)

    def set_mask_available(self, available: bool):
        if not available:
            self.reset()
        self.checkbox.setEnabled(available)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self, self._translator.text("masking.package.save_title"),
            self.destination.text(), self._translator.text("masking.package.file_filter"))
        if path:
            self.destination.setText(str(Path(path).with_suffix(".dmspackage")))
            self._manual_destination = True

    def request(self, source: Path, output: Path, requires_masking: bool):
        if not self.checkbox.isChecked():
            return {}
        if not requires_masking:
            raise CSVAnonymizationError(self._translator.text("masking.package.error.requires_masking"))
        self.set_output_path(output)
        destination = validate_transfer_package_destination(source, output, self.destination.text())
        validate_password(self.password.text())
        if self.password.text() != self.confirmation.text():
            raise PackageError(self._translator.text("masking.package.error.confirmation"))
        return dict(transfer_package_request=PackageStagingRequest(self.password.text()),
                    transfer_package_destination=destination)

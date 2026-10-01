"""Execution-only inputs; never part of profiles or settings."""

from pathlib import Path
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QWidget, QCheckBox, QLineEdit, QPushButton, QVBoxLayout, QFormLayout, QHBoxLayout, QFileDialog

from data_mask_studio.csv_tools.csv_anonymizer import validate_transfer_package_destination, CSVAnonymizationError
from data_mask_studio.transfer_package.service import validate_password
from data_mask_studio.transfer_package.models import PackageError
from data_mask_studio.transfer_package.staging import PackageStagingRequest


class TransferPackageControls(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.output_path: Path | None = None
        self._manual_destination = False
        self.checkbox = QCheckBox("Gerar pacote de transferência", self)
        self.checkbox.setEnabled(False)
        self.fields = QWidget(self)
        self.destination = QLineEdit(self)
        self.destination.setAccessibleName("Destino do pacote de transferência")
        self.browse_button = QPushButton("Escolher destino", self)
        self.password = QLineEdit(self)
        self.confirmation = QLineEdit(self)
        for field, label in ((self.password, "Senha do pacote"), (self.confirmation, "Confirmar senha do pacote")):
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setAccessibleName(label)
        form = QFormLayout(self.fields)
        path_row = QHBoxLayout()
        path_row.addWidget(self.destination)
        path_row.addWidget(self.browse_button)
        form.addRow("Pacote (.dmspackage):", path_row)
        form.addRow("Senha:", self.password)
        form.addRow("Confirmar senha:", self.confirmation)
        self.show_passwords = QCheckBox("Mostrar senhas", self)
        self.show_passwords.toggled.connect(self._set_password_visibility)
        form.addRow("", self.show_passwords)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.checkbox)
        layout.addWidget(self.fields)
        self.checkbox.toggled.connect(self._toggled)
        self.browse_button.clicked.connect(self._browse)
        self.destination.textChanged.connect(self._destination_changed)
        self._toggled(False)

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
        path, _ = QFileDialog.getSaveFileName(self, "Salvar pacote de transferência",
            self.destination.text(), "Pacotes de transferência (*.dmspackage)")
        if path:
            self.destination.setText(str(Path(path).with_suffix(".dmspackage")))
            self._manual_destination = True

    def request(self, source: Path, output: Path, requires_masking: bool):
        if not self.checkbox.isChecked():
            return {}
        if not requires_masking:
            raise CSVAnonymizationError("O pacote exige ao menos uma coluna com ação Mascarar.")
        self.set_output_path(output)
        destination = validate_transfer_package_destination(source, output, self.destination.text())
        validate_password(self.password.text())
        if self.password.text() != self.confirmation.text():
            raise PackageError("A confirmação da senha não corresponde à senha do pacote.")
        return dict(transfer_package_request=PackageStagingRequest(self.password.text()),
                    transfer_package_destination=destination)

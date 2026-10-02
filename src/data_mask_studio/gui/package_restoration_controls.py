from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QWidget,
)

from data_mask_studio.gui.restoration_worker import PackageRestorationRequest
from data_mask_studio.restoration import RestorationError


class PackageRestorationControls(QWidget):
    """Transient inputs only; authentication and binding belong to the backend."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.path = QLineEdit()
        self.path.setAccessibleName("Pacote de transferência")
        self.browse = QPushButton("Escolher arquivo")
        self.browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path)
        row.addWidget(self.browse)
        self.password = QLineEdit()
        self.password.setAccessibleName("Senha do pacote")
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password = QCheckBox("Mostrar senha")
        self.show_password.toggled.connect(self._show_password)
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addRow("Pacote (.dmspackage):", row)
        layout.addRow("Senha:", self.password)
        layout.addRow("", self.show_password)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Selecionar pacote de transferência", "", "Pacotes (*.dmspackage)"
        )
        if path:
            self.clear_password()
            self.path.setText(path)

    def _show_password(self, visible: bool) -> None:
        self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )

    def clear_password(self) -> None:
        self.show_password.setChecked(False)
        self.password.clear()

    def request(self) -> PackageRestorationRequest:
        path = Path(self.path.text()).expanduser()
        try:
            valid = bool(self.path.text()) and path.suffix.lower() == ".dmspackage" and path.is_file()
        except (OSError, ValueError):
            valid = False
        if not valid:
            raise RestorationError("Selecione um arquivo .dmspackage existente.")
        # Opening existing packages must not enforce the creation-time policy.
        if not self.password.text():
            raise RestorationError("Informe a senha do pacote.")
        return PackageRestorationRequest(path, self.password.text())

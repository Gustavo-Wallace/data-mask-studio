from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QWidget,
)

from data_mask_studio.gui.restoration_worker import PackageRestorationRequest
from data_mask_studio.i18n import Translator
from data_mask_studio.restoration import RestorationError


class RestorationInputError(RestorationError):
    """GUI-owned validation only; keep backend errors and contracts unchanged."""

    def __init__(self, message_id: str, translator: Translator) -> None:
        self.message_id = message_id
        super().__init__(translator.text(message_id))


class PackageRestorationControls(QWidget):
    """Transient inputs only; authentication and binding belong to the backend."""

    def __init__(self, parent: QWidget | None = None, *, translator: Translator | None = None) -> None:
        super().__init__(parent)
        self._translator = translator or Translator("pt-BR")
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
        self._path_label = layout.labelForField(row)
        self._password_label = layout.labelForField(self.password)
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        """Relabel inputs without touching their values, visibility or echo mode."""
        self._translator = translator
        for widget, message_id in (
            (self._path_label, "restoration.package.path_label"),
            (self._password_label, "restoration.package.password_label"),
            (self.browse, "restoration.package.browse"),
            (self.show_password, "restoration.package.show_password"),
        ):
            widget.setText(translator.text(message_id))
        self.path.setAccessibleName(translator.text("restoration.package.path_accessible"))
        self.password.setAccessibleName(translator.text("restoration.package.password_accessible"))

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self._translator.text("restoration.package.select_title"), "",
            self._translator.text("restoration.package.filter"),
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
            raise RestorationInputError("restoration.package.error.path", self._translator)
        # Opening existing packages must not enforce the creation-time policy.
        if not self.password.text():
            raise RestorationInputError("restoration.package.error.password", self._translator)
        return PackageRestorationRequest(path, self.password.text())

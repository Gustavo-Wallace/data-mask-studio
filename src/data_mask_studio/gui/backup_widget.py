from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.backup import (
    BackupCreationResult,
    BackupError,
    BackupValidationResult,
    EnvironmentPaths,
    RestoreResult,
    suggested_backup_name,
    validate_password,
)
from data_mask_studio.gui.backup_worker import (
    BackupCreationWorker,
    BackupRestoreWorker,
    BackupValidationWorker,
)
from data_mask_studio.security import DataProtector, KeyProvider


class BackupWidget(QWidget):
    busy_changed = Signal(bool)
    environment_restored = Signal()

    def __init__(
        self,
        paths: EnvironmentPaths,
        hmac_key_provider: KeyProvider,
        vault_key_provider: KeyProvider,
        protector: DataProtector,
        prepare_restore: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        self._paths = paths
        self._hmac_key_provider = hmac_key_provider
        self._vault_key_provider = vault_key_provider
        self._protector = protector
        self._prepare_restore = prepare_restore or (lambda: True)
        self._worker: (
            BackupCreationWorker | BackupValidationWorker | BackupRestoreWorker | None
        ) = None
        self._validated_result: BackupValidationResult | None = None
        self._last_backup_path: Path | None = None

        create_group = QGroupBox("Criar backup")
        self.destination_field = QLineEdit()
        self.destination_field.setReadOnly(True)
        self.destination_field.setText(str(Path.home() / suggested_backup_name()))
        self.choose_destination_button = QPushButton("Escolher destino")
        self.choose_destination_button.clicked.connect(self.choose_destination)
        destination_row = QHBoxLayout()
        destination_row.addWidget(self.destination_field, stretch=1)
        destination_row.addWidget(self.choose_destination_button)
        self.create_password_field = _password_field()
        self.confirm_password_field = _password_field()
        self.show_create_password = QCheckBox("Mostrar senha")
        self.show_create_password.toggled.connect(
            lambda visible: _set_password_visible(
                (self.create_password_field, self.confirm_password_field), visible
            )
        )
        recommendation = QLabel("Use uma frase-senha longa, única e bem guardada.")
        recommendation.setStyleSheet("color: #555;")
        self.create_button = QPushButton("Criar backup")
        self.create_button.clicked.connect(self.start_creation)
        self.create_cancel_button = QPushButton("Cancelar")
        self.create_cancel_button.clicked.connect(self.cancel)
        self.create_cancel_button.setVisible(False)
        self.create_progress = QProgressBar()
        self.create_progress.setRange(0, 1)
        self.create_progress.setValue(0)
        self.create_status = QLabel("Escolha o destino e informe uma senha.")
        self.create_status.setWordWrap(True)
        self.open_folder_button = QPushButton("Abrir pasta do backup")
        self.open_folder_button.clicked.connect(self.open_backup_folder)
        self.open_folder_button.setEnabled(False)
        create_form = QFormLayout(create_group)
        create_form.addRow("Destino:", destination_row)
        create_form.addRow("Senha:", self.create_password_field)
        create_form.addRow("Confirmar senha:", self.confirm_password_field)
        create_form.addRow("", self.show_create_password)
        create_form.addRow("", recommendation)
        create_actions = QHBoxLayout()
        create_actions.addWidget(self.create_button)
        create_actions.addWidget(self.create_cancel_button)
        create_actions.addWidget(self.open_folder_button)
        create_actions.addStretch()
        create_form.addRow("", create_actions)
        create_form.addRow("Progresso:", self.create_progress)
        create_form.addRow("Status:", self.create_status)

        restore_group = QGroupBox("Restaurar backup")
        self.restore_file_field = QLineEdit()
        self.restore_file_field.setReadOnly(True)
        self.restore_file_field.textChanged.connect(self._invalidate_validation)
        self.choose_restore_button = QPushButton("Selecionar backup")
        self.choose_restore_button.clicked.connect(self.choose_restore_file)
        restore_file_row = QHBoxLayout()
        restore_file_row.addWidget(self.restore_file_field, stretch=1)
        restore_file_row.addWidget(self.choose_restore_button)
        self.restore_password_field = _password_field()
        self.restore_password_field.textChanged.connect(self._invalidate_validation)
        self.show_restore_password = QCheckBox("Mostrar senha")
        self.show_restore_password.toggled.connect(
            lambda visible: _set_password_visible(
                (self.restore_password_field,), visible
            )
        )
        self.validate_backup_button = QPushButton("Validar backup")
        self.validate_backup_button.clicked.connect(self.start_validation)
        self.validate_backup_button.setEnabled(False)
        self.restore_button = QPushButton("Restaurar")
        self.restore_button.clicked.connect(self.start_restore)
        self.restore_button.setEnabled(False)
        self.restore_cancel_button = QPushButton("Cancelar")
        self.restore_cancel_button.clicked.connect(self.cancel)
        self.restore_cancel_button.setVisible(False)
        self.restore_progress = QProgressBar()
        self.restore_progress.setRange(0, 1)
        self.restore_progress.setValue(0)
        self.restore_summary = QTextEdit()
        self.restore_summary.setReadOnly(True)
        self.restore_summary.setMaximumHeight(115)
        self.restore_status = QLabel("Selecione um arquivo .dmsbackup.")
        self.restore_status.setWordWrap(True)
        restore_form = QFormLayout(restore_group)
        restore_form.addRow("Arquivo:", restore_file_row)
        restore_form.addRow("Senha:", self.restore_password_field)
        restore_form.addRow("", self.show_restore_password)
        restore_actions = QHBoxLayout()
        restore_actions.addWidget(self.validate_backup_button)
        restore_actions.addWidget(self.restore_button)
        restore_actions.addWidget(self.restore_cancel_button)
        restore_actions.addStretch()
        restore_form.addRow("", restore_actions)
        restore_form.addRow("Resumo técnico:", self.restore_summary)
        restore_form.addRow("Progresso:", self.restore_progress)
        restore_form.addRow("Status:", self.restore_status)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.addWidget(create_group)
        layout.addWidget(restore_group)
        layout.addStretch()

    def choose_destination(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar backup criptografado",
            self.destination_field.text(),
            "Backup do Data Mask Studio (*.dmsbackup)",
        )
        if selected:
            path = Path(selected)
            if path.suffix.lower() != ".dmsbackup":
                path = path.with_suffix(".dmsbackup")
            self.destination_field.setText(str(path))

    def choose_restore_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar backup",
            "",
            "Backup do Data Mask Studio (*.dmsbackup)",
        )
        if selected:
            self.restore_file_field.setText(selected)

    def start_creation(self) -> None:
        if self._worker is not None:
            return
        destination = Path(self.destination_field.text())
        try:
            validate_password(
                self.create_password_field.text(), self.confirm_password_field.text()
            )
            if not self.destination_field.text():
                raise BackupError("Escolha o destino do backup.")
        except BackupError as error:
            self._status(self.create_status, str(error), True)
            return
        overwrite = destination.exists()
        if overwrite:
            answer = QMessageBox.question(
                self,
                "Substituir backup",
                "O arquivo de backup já existe. Deseja substituí-lo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        worker = BackupCreationWorker(
            str(destination),
            self.create_password_field.text(),
            self.confirm_password_field.text(),
            self._paths,
            self._hmac_key_provider,
            self._vault_key_provider,
            overwrite=overwrite,
        )
        self._begin(worker, creating=True)
        worker.completed.connect(self._creation_completed)
        worker.cancelled.connect(self._cancelled)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.start()

    def start_validation(self) -> None:
        if self._worker is not None or not self.restore_file_field.text():
            self._status(self.restore_status, "Selecione um arquivo de backup.", True)
            return
        worker = BackupValidationWorker(
            self.restore_file_field.text(), self.restore_password_field.text()
        )
        self._begin(worker, creating=False)
        worker.completed.connect(self._validation_completed)
        worker.cancelled.connect(self._cancelled)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.start()

    def start_restore(self) -> None:
        if self._worker is not None or self._validated_result is None:
            return
        if not self._prepare_restore():
            self._status(
                self.restore_status,
                "Finalize as operações em andamento antes de restaurar.",
                True,
            )
            return
        answer = QMessageBox.question(
            self,
            "Confirmar restauração",
            "A restauração substituirá o cofre, as chaves e os perfis locais atuais.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        worker = BackupRestoreWorker(
            self.restore_file_field.text(),
            self.restore_password_field.text(),
            self._paths,
            self._protector,
        )
        self._begin(worker, creating=False)
        worker.completed.connect(self._restore_completed)
        worker.cancelled.connect(self._cancelled)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        worker.start()

    def _begin(self, worker: QThread, *, creating: bool) -> None:
        self._worker = worker
        self.busy_changed.emit(True)
        self._set_controls_enabled(False)
        progress = self.create_progress if creating else self.restore_progress
        progress.setRange(0, 0)
        if creating:
            self.create_cancel_button.setEnabled(True)
            self.create_cancel_button.setVisible(True)
            self._status(self.create_status, "Criando backup criptografado...", False)
        else:
            self.restore_cancel_button.setEnabled(True)
            self.restore_cancel_button.setVisible(True)
            self._status(self.restore_status, "Processando backup...", False)

    def _creation_completed(self, result: BackupCreationResult) -> None:
        self._last_backup_path = result.path
        self.create_progress.setRange(0, 1)
        self.create_progress.setValue(1)
        self.open_folder_button.setEnabled(True)
        self._status(self.create_status, "Backup criado com sucesso.", False)
        self._clear_create_passwords()

    def _validation_completed(self, result: BackupValidationResult) -> None:
        self._validated_result = result if result.is_compatible else None
        self.restore_button.setEnabled(result.is_compatible)
        self.restore_progress.setRange(0, 1)
        self.restore_progress.setValue(1)
        self.restore_summary.setPlainText(_render_validation(result))
        message = "Backup validado com sucesso." if result.is_compatible else "Backup incompatível."
        self._status(self.restore_status, message, not result.is_compatible)

    def _restore_completed(self, result: RestoreResult) -> None:
        self.restore_progress.setRange(0, 1)
        self.restore_progress.setValue(1)
        self._status(
            self.restore_status,
            f"Ambiente restaurado com sucesso: {result.mapping_count} mapeamento(s).",
            False,
        )
        self.restore_password_field.clear()
        self._validated_result = None
        self.restore_button.setEnabled(False)
        self.environment_restored.emit()

    def _cancelled(self) -> None:
        self._clear_all_passwords()
        self._status(self.create_status, "Operação cancelada.", False)
        self._status(self.restore_status, "Operação cancelada.", False)

    def _failed(self, error: Exception) -> None:
        message = str(error) if isinstance(error, BackupError) else "A operação de backup falhou."
        target = self.create_status if isinstance(self._worker, BackupCreationWorker) else self.restore_status
        self._status(target, message, True)
        if isinstance(self._worker, BackupCreationWorker):
            self._clear_create_passwords()
        self._validated_result = None
        self.restore_button.setEnabled(False)

    def _finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.create_cancel_button.setVisible(False)
        self.restore_cancel_button.setVisible(False)
        self.create_progress.setRange(0, 1)
        self.restore_progress.setRange(0, 1)
        self._set_controls_enabled(True)
        self.busy_changed.emit(False)
        self.restore_button.setEnabled(self._validated_result is not None)
        self.validate_backup_button.setEnabled(
            bool(self.restore_file_field.text() and self.restore_password_field.text())
        )

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self.create_cancel_button.setEnabled(False)
            self.restore_cancel_button.setEnabled(False)

    def _invalidate_validation(self) -> None:
        self._validated_result = None
        self.restore_button.setEnabled(False)
        self.restore_summary.clear()
        self.validate_backup_button.setEnabled(
            bool(self.restore_file_field.text() and self.restore_password_field.text())
            and self._worker is None
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        for control in (
            self.choose_destination_button,
            self.create_password_field,
            self.confirm_password_field,
            self.show_create_password,
            self.create_button,
            self.choose_restore_button,
            self.restore_password_field,
            self.show_restore_password,
            self.validate_backup_button,
        ):
            control.setEnabled(enabled)

    def open_backup_folder(self) -> None:
        if self._last_backup_path is not None:
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(self._last_backup_path.parent))
            )

    def stop_worker(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            if not self._worker.wait(5000):
                return False
        self._clear_all_passwords()
        return True

    def has_running_worker(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def _clear_create_passwords(self) -> None:
        self.create_password_field.clear()
        self.confirm_password_field.clear()

    def _clear_all_passwords(self) -> None:
        self._clear_create_passwords()
        self.restore_password_field.clear()

    @staticmethod
    def _status(label: QLabel, message: str, error: bool) -> None:
        label.setText(message)
        label.setStyleSheet(f"color: {'#b42318' if error else '#276749'};")


def _password_field() -> QLineEdit:
    field = QLineEdit()
    field.setEchoMode(QLineEdit.EchoMode.Password)
    return field


def _set_password_visible(fields: tuple[QLineEdit, ...], visible: bool) -> None:
    mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
    for field in fields:
        field.setEchoMode(mode)


def _render_validation(result: BackupValidationResult) -> str:
    compatibility = "Compatível" if result.is_compatible else "Incompatível"
    return "\n".join(
        (
            f"Data do backup: {result.created_at.isoformat()}",
            f"Versão da aplicação: {result.application_version}",
            f"Versão do formato: {result.format_version}",
            f"Mapeamentos: {result.mapping_count}",
            f"Perfis: {result.profile_count}",
            f"Cofre presente: {'Sim' if result.vault_present else 'Não'}",
            f"Compatibilidade: {compatibility}",
        )
    )

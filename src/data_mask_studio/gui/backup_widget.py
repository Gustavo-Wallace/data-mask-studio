from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
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
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.backup import (
    BackupCreationResult,
    BackupError,
    BackupValidationResult,
    BackupValidationError,
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
from data_mask_studio.gui.components import EmptyStateTextEdit
from data_mask_studio.gui.components.presentation import (
    FeedbackState, configure_result_area, confirm_destructive_action,
    set_button_role, set_feedback_state,
)
from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.gui.dialog_localization import localize_standard_buttons
from data_mask_studio.i18n import Translator
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
        *, translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self._paths = paths
        self._hmac_key_provider = hmac_key_provider
        self._vault_key_provider = vault_key_provider
        self._protector = protector
        self._prepare_restore = prepare_restore or (lambda: True)
        self._translator = translator or Translator("pt-BR")
        self._status_messages: dict[QLabel, tuple[str, dict[str, int]]] = {}
        self._displayed_validation: BackupValidationResult | None = None
        self._confirmation_ids: tuple[str, str] | None = None
        self._worker: (
            BackupCreationWorker | BackupValidationWorker | BackupRestoreWorker | None
        ) = None
        self._validated_result: BackupValidationResult | None = None
        self._last_backup_path: Path | None = None

        self.create_group = create_group = QGroupBox()
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
        self.recommendation_label = recommendation = QLabel()
        recommendation.setWordWrap(True)
        recommendation.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        set_feedback_state(recommendation, "neutral")
        self.create_button = QPushButton("Criar backup")
        set_button_role(self.create_button, "primary")
        self.create_button.clicked.connect(self.start_creation)
        self.create_cancel_button = QPushButton("Cancelar")
        self.create_cancel_button.clicked.connect(self.cancel)
        self.create_cancel_button.setVisible(False)
        self.create_progress = QProgressBar()
        self.create_progress.setRange(0, 1)
        self.create_progress.setValue(0)
        self.create_status = QLabel("Escolha o destino e informe uma senha.")
        self.create_status.setWordWrap(True)
        set_feedback_state(self.create_status, "neutral")
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
        self._create_labels = (
            (create_form.labelForField(destination_row), "backup.destination"),
            (create_form.labelForField(self.create_password_field), "masking.package.password_label"),
            (create_form.labelForField(self.confirm_password_field), "masking.package.confirmation_label"),
            (create_form.labelForField(self.create_progress), "backup.progress"),
            (create_form.labelForField(self.create_status), "backup.status_label"),
        )

        self.restore_group = restore_group = QGroupBox()
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
        set_button_role(self.validate_backup_button, "primary")
        self.validate_backup_button.clicked.connect(self.start_validation)
        self.validate_backup_button.setEnabled(False)
        self.restore_button = QPushButton("Restaurar backup…")
        set_button_role(self.restore_button, "attention")
        self.restore_button.clicked.connect(self.start_restore)
        self.restore_button.setEnabled(False)
        self.restore_cancel_button = QPushButton("Cancelar")
        self.restore_cancel_button.clicked.connect(self.cancel)
        self.restore_cancel_button.setVisible(False)
        self.restore_progress = QProgressBar()
        self.restore_progress.setRange(0, 1)
        self.restore_progress.setValue(0)
        self.restore_summary = EmptyStateTextEdit(
            "Valide um backup para exibir o resumo técnico."
        )
        self.restore_summary.setReadOnly(True)
        # Let the scrollable page use the editor's Qt minimum, not its large
        # preferred height, while the result row receives the available space.
        self.restore_summary.setMinimumHeight(self.restore_summary.minimumSizeHint().height())
        self.restore_summary.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        configure_result_area(self.restore_summary, 80)
        self.restore_status = QLabel("Selecione um arquivo .dmsbackup.")
        self.restore_status.setWordWrap(True)
        set_feedback_state(self.restore_status, "neutral")
        restore_form = QFormLayout()
        restore_form.addRow("Arquivo:", restore_file_row)
        restore_form.addRow("Senha:", self.restore_password_field)
        restore_form.addRow("", self.show_restore_password)
        restore_actions = QHBoxLayout()
        restore_actions.addWidget(self.validate_backup_button)
        restore_actions.addWidget(self.restore_button)
        restore_actions.addWidget(self.restore_cancel_button)
        restore_actions.addStretch()
        restore_form.addRow("", restore_actions)
        self.summary_label = summary_label = QLabel()
        summary_label.setBuddy(self.restore_summary)
        summary_row = QHBoxLayout()
        summary_row.addWidget(summary_label, alignment=Qt.AlignmentFlag.AlignTop)
        summary_row.addWidget(self.restore_summary, stretch=1)
        restore_feedback = QFormLayout()
        restore_feedback.addRow("Progresso:", self.restore_progress)
        restore_feedback.addRow("Status:", self.restore_status)
        # Align the existing captions using their font-dependent size hints.
        self._restore_labels = (
            (restore_form.labelForField(restore_file_row), "masking.metadata.file"),
            (restore_form.labelForField(self.restore_password_field), "masking.package.password_label"),
            (summary_label, "backup.summary.label"),
            (restore_feedback.labelForField(self.restore_progress), "backup.progress"),
            (restore_feedback.labelForField(self.restore_status), "backup.status_label"),
        )
        restore_layout = QVBoxLayout(restore_group)
        restore_layout.addLayout(restore_form)
        restore_layout.addLayout(summary_row, stretch=1)
        restore_layout.addLayout(restore_feedback)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(METRICS["panel_spacing"])
        layout.addWidget(create_group)
        layout.addWidget(restore_group)
        layout.addStretch()
        self.restore_summary.textChanged.connect(self._update_summary_layout)
        self._update_summary_layout()
        for label in (self.create_status, self.restore_status, recommendation):
            label.setTextFormat(Qt.TextFormat.PlainText)
        self._status_id(self.create_status, "backup.create.initial")
        self._status_id(self.restore_status, "backup.restore.initial")
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        """Relabel existing controls and metadata without touching credentials or workers."""
        self._translator = translator
        self.create_group.setTitle(translator.text("backup.create"))
        self.restore_group.setTitle(translator.text("backup.restore"))
        for widget, identifier in (
            (self.choose_destination_button, "backup.choose_destination"),
            (self.create_button, "backup.create"),
            (self.create_cancel_button, "dialog.button.cancel"),
            (self.restore_cancel_button, "dialog.button.cancel"),
            (self.open_folder_button, "backup.open_folder"),
            (self.choose_restore_button, "backup.select"),
            (self.validate_backup_button, "backup.validate"),
            (self.restore_button, "backup.restore_action"),
            (self.show_create_password, "restoration.package.show_password"),
            (self.show_restore_password, "restoration.package.show_password"),
            (self.recommendation_label, "backup.recommendation"),
            *self._create_labels, *self._restore_labels,
        ):
            widget.setText(translator.text(identifier))
        for field, identifier in (
            (self.destination_field, "backup.destination_accessible"),
            (self.restore_file_field, "backup.file_accessible"),
            (self.create_password_field, "backup.password_accessible"),
            (self.confirm_password_field, "backup.confirmation_accessible"),
            (self.restore_password_field, "backup.restore_password_accessible"),
            (self.restore_summary, "backup.summary_accessible"),
        ):
            field.setAccessibleName(translator.text(identifier))
        self.restore_summary.empty_text = translator.text("backup.summary.empty")
        self.restore_summary.setAccessibleDescription(self.restore_summary.empty_text)
        self.restore_summary.viewport().update()
        for label, _ in self._restore_labels:
            label.setMinimumWidth(0)
        width = max(label.sizeHint().width() for label, _ in self._restore_labels)
        for label, _ in self._restore_labels:
            label.setMinimumWidth(width)
        for label, (identifier, parameters) in self._status_messages.items():
            label.setText(self._status_text(identifier, parameters))
        if self._displayed_validation is not None:
            self._render_summary(preserve_position=True)
        dialog = QApplication.activeModalWidget()
        if (self._confirmation_ids is not None and isinstance(dialog, QMessageBox)
                and dialog.parentWidget() is self):
            title_id, text_id = self._confirmation_ids
            dialog.setWindowTitle(translator.text(title_id))
            dialog.setText(translator.text(text_id))
            localize_standard_buttons(dialog, translator)

    def _render_summary(self, *, preserve_position: bool = False) -> None:
        output = self.restore_summary
        text = _render_validation(self._displayed_validation, self._translator)
        if not preserve_position:
            output.setPlainText(text)
            return
        cursor = output.textCursor()
        positions = []
        for position in (cursor.anchor(), cursor.position()):
            cursor.setPosition(position)
            block = cursor.block()
            positions.append((block.blockNumber(), block.length() - 1 - cursor.positionInBlock()))
        vertical, horizontal = output.verticalScrollBar().value(), output.horizontalScrollBar().value()
        with QSignalBlocker(output):
            output.setPlainText(text)
            cursor = output.textCursor()
            for index, (number, offset) in enumerate(positions):
                block = output.document().findBlockByNumber(number)
                cursor.setPosition(block.position() + max(0, block.length() - 1 - offset),
                                   QTextCursor.MoveMode.MoveAnchor if index == 0 else QTextCursor.MoveMode.KeepAnchor)
            output.setTextCursor(cursor)
            output.verticalScrollBar().setValue(vertical)
            output.horizontalScrollBar().setValue(horizontal)

    def _confirm(self, title_id: str, text_id: str) -> int:
        self._confirmation_ids = (title_id, text_id)
        try:
            return confirm_destructive_action(
                self, self._translator.text(title_id), self._translator.text(text_id),
                translator=self._translator,
            )
        finally:
            self._confirmation_ids = None

    def _update_summary_layout(self) -> None:
        # Creation keeps its natural size; only populated restore results expand.
        has_summary = bool(self.restore_summary.toPlainText().strip())
        layout = self.layout()
        layout.setStretch(1, int(has_summary))
        layout.setStretch(layout.count() - 1, int(not has_summary))

    def choose_destination(self) -> None:
        selected, _ = QFileDialog.getSaveFileName(
            self,
            self._translator.text("backup.dialog.save"),
            self.destination_field.text(),
            self._translator.text("backup.file_filter"),
        )
        if selected:
            path = Path(selected)
            if path.suffix.lower() != ".dmsbackup":
                path = path.with_suffix(".dmsbackup")
            self.destination_field.setText(str(path))

    def choose_restore_file(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self,
            self._translator.text("backup.select"),
            "",
            self._translator.text("backup.file_filter"),
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
                self._status_id(self.create_status, "backup.error.destination", error=True)
                return
        except BackupError as error:
            self._status(self.create_status, str(error), True)
            return
        overwrite = destination.exists()
        if overwrite:
            answer = self._confirm("backup.confirm.overwrite.title", "backup.confirm.overwrite.text")
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
            self._status_id(self.restore_status, "backup.error.select", error=True)
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
            self._status_id(self.restore_status, "backup.error.busy", error=True)
            return
        answer = self._confirm("backup.confirm.restore.title", "backup.confirm.restore.text")
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
        if isinstance(worker, BackupValidationWorker):
            self._validated_result = None
            self.restore_button.setEnabled(False)
            self._clear_summary()
        self.busy_changed.emit(True)
        self._set_controls_enabled(False)
        progress = self.create_progress if creating else self.restore_progress
        progress.setRange(0, 0)
        progress.setValue(0)
        if creating:
            self.create_cancel_button.setEnabled(True)
            self.create_cancel_button.setVisible(True)
            self._status_id(self.create_status, "backup.status.creating")
        else:
            self.restore_cancel_button.setEnabled(True)
            self.restore_cancel_button.setVisible(True)
            self._status_id(self.restore_status, "backup.status.processing")

    def _creation_completed(self, result: BackupCreationResult) -> None:
        if not self._accept_worker_signal():
            return
        self._last_backup_path = result.path
        self.create_progress.setRange(0, 1)
        self.create_progress.setValue(1)
        self.open_folder_button.setEnabled(True)
        self._status_id(self.create_status, "backup.status.created", state="success")
        self._clear_create_passwords()

    def _validation_completed(self, result: BackupValidationResult) -> None:
        if not self._accept_worker_signal():
            return
        self._validated_result = result if result.is_compatible else None
        self.restore_button.setEnabled(result.is_compatible)
        self.restore_progress.setRange(0, 1)
        self.restore_progress.setValue(1)
        self._displayed_validation = result
        self._render_summary()
        self._status_id(self.restore_status, "backup.status.validated" if result.is_compatible
                        else "backup.status.incompatible", error=not result.is_compatible, state="success")

    def _restore_completed(self, result: RestoreResult) -> None:
        if not self._accept_worker_signal():
            return
        self.restore_progress.setRange(0, 1)
        self.restore_progress.setValue(1)
        self._status_id(self.restore_status, "backup.status.restored", count=result.mapping_count, state="success")
        self.restore_password_field.clear()
        self._validated_result = None
        self.restore_button.setEnabled(False)
        self.environment_restored.emit()

    def _cancelled(self) -> None:
        if not self._accept_worker_signal():
            return
        self._clear_all_passwords()
        for progress in (self.create_progress, self.restore_progress):
            progress.setRange(0, 1)
            progress.setValue(0)
        self._status_id(self.create_status, "backup.status.cancelled", state="warning")
        self._status_id(self.restore_status, "backup.status.cancelled", state="warning")

    def _failed(self, error: Exception) -> None:
        if not self._accept_worker_signal():
            return
        target = self.create_status if isinstance(self._worker, BackupCreationWorker) else self.restore_status
        if isinstance(self._worker, BackupValidationWorker) and isinstance(error, BackupValidationError):
            self._status_id(target, "backup.error.validation", error=True)
        elif isinstance(error, BackupError):
            self._status(target, str(error), True)
        else:
            self._status_id(target, "backup.error.unexpected", error=True)
        progress = self.create_progress if target is self.create_status else self.restore_progress
        progress.setRange(0, 1)
        progress.setValue(0)
        if isinstance(self._worker, BackupCreationWorker):
            self._clear_create_passwords()
        self._validated_result = None
        self._clear_summary()
        self.restore_button.setEnabled(False)

    def _finished(self) -> None:
        if not self._accept_worker_signal():
            return
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.create_cancel_button.setVisible(False)
        self.restore_cancel_button.setVisible(False)
        self.create_progress.setRange(0, 1)
        self.restore_progress.setRange(0, 1)
        for progress in (self.create_progress, self.restore_progress):
            if progress.value() < 0:
                progress.setValue(0)
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
        self._clear_summary()
        if self._worker is None:
            self.restore_progress.setRange(0, 1)
            self.restore_progress.setValue(0)
            self._status_id(self.restore_status, "backup.status.validation_pending" if self.restore_file_field.text()
                            else "backup.restore.initial")
        self.validate_backup_button.setEnabled(
            bool(self.restore_file_field.text() and self.restore_password_field.text())
            and self._worker is None
        )

    def _set_controls_enabled(self, enabled: bool) -> None:
        if not enabled:
            self.restore_button.setEnabled(False)
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

    def _status(
        self, label: QLabel, message: str, error: bool, *, state: FeedbackState = "neutral"
    ) -> None:
        self._status_messages.pop(label, None)
        label.setText(message)
        set_feedback_state(label, "error" if error else state)

    def _status_id(self, label: QLabel, identifier: str, *, error: bool = False,
                   state: FeedbackState = "neutral", **parameters: int) -> None:
        self._status_messages[label] = (identifier, parameters)
        label.setText(self._status_text(identifier, parameters))
        set_feedback_state(label, "error" if error else state)

    def _status_text(self, identifier: str, parameters: dict[str, int]) -> str:
        if identifier == "backup.status.restored":
            return self._translator.plural(identifier, parameters["count"])
        return self._translator.text(identifier, **parameters)

    def _clear_summary(self) -> None:
        self._displayed_validation = None
        self.restore_summary.clear()

    def _accept_worker_signal(self) -> bool:
        sender = self.sender()
        # Direct internal calls remain supported; Qt callbacks belong only to
        # the active worker, including terminal signals queued before finished.
        return sender is None or sender is self._worker


def _password_field() -> QLineEdit:
    field = QLineEdit()
    field.setEchoMode(QLineEdit.EchoMode.Password)
    return field


def _set_password_visible(fields: tuple[QLineEdit, ...], visible: bool) -> None:
    mode = QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
    for field in fields:
        field.setEchoMode(mode)


def _render_validation(result: BackupValidationResult, translator: Translator | None = None) -> str:
    translator = translator or Translator("pt-BR")
    text = translator.text
    return "\n".join(
        (
            text("backup.summary.date", timestamp=result.created_at.isoformat()),
            text("backup.summary.application", version=result.application_version),
            text("backup.summary.format", version=result.format_version),
            text("backup.summary.mappings", count=result.mapping_count),
            text("backup.summary.profiles", count=result.profile_count),
            text("backup.summary.vault_present", present=text("backup.value.yes" if result.vault_present else "backup.value.no")),
            text("backup.summary.compatibility", compatibility=text("batch.file_status.compatible" if result.is_compatible
                                                                   else "batch.file_status.incompatible")),
        )
    )

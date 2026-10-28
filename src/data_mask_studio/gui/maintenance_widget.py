from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, QThread, QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.backup import (
    BackupError,
    BackupValidationError,
    BackupValidationResult,
    EnvironmentPaths,
)
from data_mask_studio.gui.backup_worker import BackupValidationWorker
from data_mask_studio.gui.maintenance_worker import (
    CompactionWorker,
    DiagnosticWorker,
    TemporaryCleanupWorker,
    TemporaryScanWorker,
)
from data_mask_studio.gui.components import EmptyStateTable, EmptyStateTextEdit
from data_mask_studio.gui.components.presentation import (
    FeedbackState, configure_result_area, confirm_destructive_action,
    set_button_role, set_feedback_state,
)
from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.integrity import AuditReport
from data_mask_studio.maintenance import (
    CleanupResult,
    CompactionResult,
    DiagnosticResult,
    MaintenanceDiagnostics,
    MaintenanceError,
    MaintenanceStatus,
    TemporaryItem,
    VaultCompactor,
    safe_diagnostic_report,
)
from data_mask_studio.i18n import Translator
from data_mask_studio.security import KeyProvider


@dataclass(frozen=True)
class _SectionFeedback:
    message: str
    state: FeedbackState
    minimum: int
    maximum: int
    value: int


class MaintenanceWidget(QWidget):
    busy_changed = Signal(bool)
    environment_changed = Signal()

    def __init__(
        self,
        paths: EnvironmentPaths,
        hmac_key_provider: KeyProvider,
        vault_key_provider: KeyProvider,
        prepare_operation: Callable[[], bool] | None = None,
        session_directories: Callable[[], Iterable[Path]] | None = None,
        *, translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self._translator = translator or Translator("pt-BR")
        self._overview_message_id: str | None = "maintenance.overview.status.initial"
        self._last_audit_message_id = "maintenance.overview.audit.not_run"
        self._backup_message_id: str | None = None
        self._backup_result_message_id: str | None = None
        self._last_backup_validation: BackupValidationResult | None = None
        self._backup_accept_result = False
        self._temporary_message: tuple[str, dict[str, int]] | None = None
        self._temporary_accept_result = False
        self._temporary_succeeded = False
        self._paths = paths
        self._diagnostics = MaintenanceDiagnostics(
            paths, hmac_key_provider, vault_key_provider
        )
        self._compactor = VaultCompactor(
            paths, hmac_key_provider, vault_key_provider
        )
        self._prepare_operation = prepare_operation or (lambda: True)
        self._session_directories = session_directories or (lambda: ())
        self._worker: QThread | None = None
        self._last_diagnostic: DiagnosticResult | None = None
        self._last_audit: AuditReport | None = None
        self._diagnostic_accept_result = False
        self.temporary_items: list[TemporaryItem] = []

        self.sections = QTabWidget()
        self.sections.addTab(self._build_overview(), "Visão geral")
        self.sections.addTab(self._build_backup_validation(), "Validar backup")
        self.sections.addTab(self._build_cleanup(), "Temporários")
        self.sections.addTab(self._build_compaction(), "Compactação")

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setVisible(False)
        self.status_label = QLabel("Pronto para diagnosticar o ambiente local.")
        self.status_label.setWordWrap(True)
        set_feedback_state(self.status_label, "neutral")
        self._feedback_section = 0
        self._section_feedback: dict[int, _SectionFeedback] = {}
        # Keep cancellation outside the disabled tab content while a worker runs.
        self._feedback_area = QWidget()
        feedback_layout = QVBoxLayout(self._feedback_area)
        feedback_layout.setContentsMargins(0, 0, 0, 0)
        feedback_layout.setSpacing(METRICS["panel_spacing"])
        operation_row = QHBoxLayout()
        operation_row.addWidget(self.progress, stretch=1)
        operation_row.addWidget(self.cancel_button)
        feedback_layout.addLayout(operation_row)
        feedback_layout.addWidget(self.status_label)
        self._remember_feedback()
        self.sections.currentChanged.connect(self._section_changed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(METRICS["panel_spacing"])
        layout.addWidget(self.sections, stretch=1)
        layout.addWidget(self._feedback_area)
        layout.addStretch()
        for output in (self.overview_output, self.backup_result, self.compaction_result):
            configure_result_area(output, 160)
            output.textChanged.connect(self._update_result_layout)
        self.temporary_table.model().rowsInserted.connect(self._update_result_layout)
        self.temporary_table.model().rowsRemoved.connect(self._update_result_layout)
        self._update_result_layout()
        self.last_audit_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        """Relabel migrated presentation without running operations or mutating validity."""
        self._translator = translator
        with QSignalBlocker(self.sections):
            for index, identifier in enumerate(("maintenance.tab.overview", "backup.validate",
                                                "maintenance.tab.temporaries", "maintenance.tab.compaction")):
                self.sections.setTabText(index, translator.text(identifier))
        for widget, identifier in (
            (self.refresh_button, "maintenance.overview.refresh"),
            (self.copy_report_button, "maintenance.overview.copy"),
            (self.cancel_button, "dialog.button.cancel"),
            (self.choose_backup_button, "maintenance.backup.select"),
            (self.validate_backup_button, "backup.validate"),
            (self.backup_file_label, "maintenance.backup.file_label"),
            (self.backup_password_label, "masking.package.password_label"),
            (self.show_backup_password, "restoration.package.show_password"),
            (self.locate_button, "maintenance.temporary.locate"),
            (self.cleanup_button, "maintenance.temporary.delete"),
            (self.temporary_info, "maintenance.temporary.instructions"),
        ):
            widget.setText(translator.text(identifier))
            widget.setAccessibleName(translator.text(identifier))
        for widget, identifier in (
            (self.sections, "maintenance.tabs_accessible"),
            (self.progress, "maintenance.progress_accessible"),
            (self.status_label, "maintenance.status_accessible"),
            (self.overview_output, "maintenance.overview.results_accessible"),
            (self.backup_path_field, "maintenance.backup.path_accessible"),
            (self.backup_password_field, "maintenance.backup.password_accessible"),
            (self.backup_result, "maintenance.backup.result_accessible"),
            (self.temporary_table, "maintenance.temporary.table_accessible"),
        ):
            widget.setAccessibleName(translator.text(identifier))
        copy_description = translator.text("maintenance.overview.copy_description")
        self.copy_report_button.setToolTip(copy_description)
        self.copy_report_button.setAccessibleDescription(copy_description)
        self.overview_output.empty_text = translator.text("maintenance.overview.empty")
        self.overview_output.setAccessibleDescription(self.overview_output.empty_text)
        self.overview_output.viewport().update()
        self.backup_result.empty_text = translator.text("maintenance.backup.empty")
        self.backup_result.setAccessibleDescription(self.backup_result.empty_text)
        self.backup_result.viewport().update()
        self.temporary_table.empty_text = translator.text("maintenance.temporary.empty")
        self.temporary_table.setAccessibleDescription(translator.text("maintenance.temporary.instructions"))
        self.temporary_table.viewport().update()
        with QSignalBlocker(self.temporary_table):
            vertical = self.temporary_table.verticalScrollBar().value()
            horizontal = self.temporary_table.horizontalScrollBar().value()
            for column, identifier in enumerate(("masking.composite.delete", "batch.table.path",
                    "maintenance.temporary.size", "maintenance.temporary.age", "batch.table.status")):
                self.temporary_table.horizontalHeaderItem(column).setText(translator.text(identifier))
            # Relabel in place: never rebuild rows, checkboxes, selection or results.
            for row, item in enumerate(self.temporary_items):
                checkbox = self.temporary_table.cellWidget(row, 0)
                checkbox.setAccessibleName(translator.text("maintenance.temporary.select_accessible", row=row + 1))
                self.temporary_table.item(row, 4).setText(self._temporary_status(item))
            self.temporary_table.verticalScrollBar().setValue(vertical)
            self.temporary_table.horizontalScrollBar().setValue(horizontal)
        self._render_last_audit()
        if self._overview_message_id is not None:
            message = self._overview_status_text(self._overview_message_id)
            saved = self._section_feedback.get(0)
            if saved is not None:
                self._section_feedback[0] = replace(saved, message=message)
            if self._feedback_section == 0:
                self.status_label.setText(message)
        if self._backup_message_id is not None:
            message = translator.text(self._backup_message_id)
            saved = self._section_feedback.get(1)
            if saved is not None:
                self._section_feedback[1] = replace(saved, message=message)
            if self._feedback_section == 1:
                self.status_label.setText(message)
        if self._temporary_message is not None:
            identifier, parameters = self._temporary_message
            message = translator.text(identifier, **parameters)
            saved = self._section_feedback.get(2)
            if saved is not None:
                self._section_feedback[2] = replace(saved, message=message)
            if self._feedback_section == 2:
                self.status_label.setText(message)
        rendered_outputs = []
        if self._last_diagnostic is not None:
            rendered_outputs.append((self.overview_output, _render_diagnostic(self._last_diagnostic, translator)))
        if self._last_backup_validation is not None:
            rendered_outputs.append((self.backup_result, _render_backup(self._last_backup_validation, translator)))
        elif self._backup_result_message_id is not None:
            rendered_outputs.append((self.backup_result, translator.text(self._backup_result_message_id)))
        for output, text in rendered_outputs:
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

    def _render_last_audit(self) -> None:
        if self._last_audit is None:
            text = self._translator.text(self._last_audit_message_id)
        else:
            timestamp = self._last_audit.finished_at.astimezone().strftime("%d/%m/%Y %H:%M:%S")
            text = self._translator.text("maintenance.overview.audit.last", timestamp=timestamp,
                status=self._translator.text(f"maintenance.integrity.{self._last_audit.status.value}"))
        self.last_audit_label.setText(text)

    def _overview_status_text(self, identifier: str) -> str:
        if identifier == "maintenance.overview.status.completed":
            return self._translator.text(identifier,
                status=self._translator.text(f"maintenance.status.{self._last_diagnostic.status.value}"))
        return self._translator.text(identifier)

    def _set_overview_status_id(self, identifier: str, error: bool = False,
                                *, state: FeedbackState = "neutral") -> None:
        self._set_status(self._overview_status_text(identifier), error, state=state)
        self._overview_message_id = identifier

    def _set_backup_status_id(self, identifier: str, error: bool = False,
                              *, state: FeedbackState = "neutral") -> None:
        self._set_status(self._translator.text(identifier), error, state=state)
        self._backup_message_id = identifier

    def _clear_backup_result(self) -> None:
        self._last_backup_validation = None
        self._backup_result_message_id = None
        self.backup_result.clear()

    def _set_temporary_status_id(self, identifier: str, error: bool = False,
                                 *, state: FeedbackState = "neutral", **parameters: int) -> None:
        self._set_status(self._translator.text(identifier, **parameters), error, state=state)
        self._temporary_message = (identifier, parameters)

    def _remember_feedback(self) -> None:
        self._section_feedback[self._feedback_section] = _SectionFeedback(
            self.status_label.text(), self.status_label.property("feedbackState"),
            self.progress.minimum(), self.progress.maximum(), self.progress.value(),
        )

    def _select_feedback_section(self, section: int) -> None:
        if section != self._feedback_section:
            if self._feedback_section in self._section_feedback:
                self._remember_feedback()
            self._feedback_section = section
            saved = self._section_feedback.get(section)
            self.progress.setRange(saved.minimum if saved else 0, saved.maximum if saved else 1)
            self.progress.setValue(saved.value if saved else 0)
            self.status_label.setText(saved.message if saved else "")
            set_feedback_state(self.status_label, saved.state if saved else "neutral")
        self._update_feedback_visibility()

    def _section_changed(self, section: int) -> None:
        if self._worker is None:
            self._select_feedback_section(section)
        else:
            self._update_feedback_visibility()

    def _update_feedback_visibility(self) -> None:
        self._feedback_area.setVisible(
            self.sections.currentIndex() == self._feedback_section
            and self._feedback_section in self._section_feedback
        )

    def _update_result_layout(self) -> None:
        # Só a prioridade de expansão muda; abas e controles permanecem visíveis.
        has_results = self.temporary_table.rowCount() > 0 or any(
            output.toPlainText().strip()
            for output in (self.overview_output, self.backup_result, self.compaction_result)
        )
        layout = self.layout()
        layout.setStretch(0, int(has_results))
        layout.setStretch(layout.count() - 1, int(not has_results))

    def _build_overview(self) -> QWidget:
        widget = QWidget()
        self.refresh_button = QPushButton("Atualizar diagnóstico")
        self.refresh_button.clicked.connect(self.start_diagnostic)
        self.copy_report_button = QPushButton("Copiar relatório técnico seguro")
        self.copy_report_button.clicked.connect(self.copy_report)
        self.copy_report_button.setEnabled(False)
        actions = QHBoxLayout()
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.copy_report_button)
        actions.addStretch()
        self.last_audit_label = QLabel(
            "Última auditoria nesta sessão: ainda não executada"
        )
        self.last_audit_label.setWordWrap(True)
        self.overview_output = EmptyStateTextEdit(
            "Estatísticas e detalhes seguros das verificações serão exibidos aqui."
        )
        self.overview_output.setReadOnly(True)
        layout = QVBoxLayout(widget)
        layout.setSpacing(METRICS["panel_spacing"])
        layout.addLayout(actions)
        layout.addWidget(self.last_audit_label)
        layout.addWidget(self.overview_output, stretch=1)
        layout.addStretch()
        return widget

    def _build_backup_validation(self) -> QWidget:
        widget = QWidget()
        self.backup_path_field = QLineEdit()
        self.backup_path_field.setReadOnly(True)
        self.choose_backup_button = QPushButton("Selecionar .dmsbackup")
        self.choose_backup_button.clicked.connect(self.choose_backup)
        file_row = QHBoxLayout()
        file_row.addWidget(self.backup_path_field, stretch=1)
        file_row.addWidget(self.choose_backup_button)
        self.backup_password_field = QLineEdit()
        self.backup_password_field.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_backup_password = QCheckBox("Mostrar senha")
        self.show_backup_password.toggled.connect(
            lambda visible: self.backup_password_field.setEchoMode(
                QLineEdit.EchoMode.Normal
                if visible
                else QLineEdit.EchoMode.Password
            )
        )
        self.validate_backup_button = QPushButton("Validar backup")
        self.validate_backup_button.clicked.connect(self.start_backup_validation)
        set_button_role(self.validate_backup_button, "primary")
        actions = QHBoxLayout()
        actions.addWidget(self.validate_backup_button)
        actions.addStretch()
        self.backup_result = EmptyStateTextEdit(
            "A validação não restaura nem modifica arquivos locais."
        )
        self.backup_result.setReadOnly(True)
        layout = QVBoxLayout(widget)
        layout.setSpacing(METRICS["panel_spacing"])
        self.backup_file_label = QLabel()
        self.backup_file_label.setBuddy(self.backup_path_field)
        self.backup_password_label = QLabel()
        self.backup_password_label.setBuddy(self.backup_password_field)
        layout.addWidget(self.backup_file_label)
        layout.addLayout(file_row)
        layout.addWidget(self.backup_password_label)
        layout.addWidget(self.backup_password_field)
        layout.addWidget(self.show_backup_password)
        layout.addLayout(actions)
        layout.addWidget(self.backup_result, stretch=1)
        layout.addStretch()
        return widget

    def _build_cleanup(self) -> QWidget:
        widget = QWidget()
        self.locate_button = QPushButton("Localizar temporários")
        self.locate_button.clicked.connect(self.start_temporary_scan)
        set_button_role(self.locate_button, "primary")
        self.cleanup_button = QPushButton("Excluir selecionados")
        self.cleanup_button.clicked.connect(self.start_cleanup)
        self.cleanup_button.setEnabled(False)
        actions = QHBoxLayout()
        actions.addWidget(self.locate_button)
        actions.addWidget(self.cleanup_button)
        actions.addStretch()
        self.temporary_table = EmptyStateTable(
            0, 5, "Nenhum arquivo temporário localizado."
        )
        self.temporary_table.setHorizontalHeaderLabels(
            ["Excluir", "Caminho", "Tamanho", "Idade", "Situação"]
        )
        self.temporary_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.temporary_table.verticalHeader().setVisible(False)
        header = self.temporary_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in range(2, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout = QVBoxLayout(widget)
        layout.setSpacing(METRICS["panel_spacing"])
        self.temporary_info = QLabel()
        self.temporary_info.setWordWrap(True)
        layout.addWidget(self.temporary_info)
        layout.addLayout(actions)
        layout.addWidget(self.temporary_table)
        return widget

    def _build_compaction(self) -> QWidget:
        widget = QWidget()
        self.compaction_info = QLabel(
            "A compactação pode recuperar pouco espaço. Crie antes um backup "
            ".dmsbackup, embora ele não seja obrigatório."
        )
        self.compaction_info.setWordWrap(True)
        set_feedback_state(self.compaction_info, "warning")
        self.compact_button = QPushButton("Compactar cofre")
        self.compact_button.clicked.connect(self.start_compaction)
        set_button_role(self.compact_button, "attention")
        actions = QHBoxLayout()
        actions.addWidget(self.compact_button)
        actions.addStretch()
        self.compaction_result = EmptyStateTextEdit(
            "O resultado seguro da compactação aparecerá aqui."
        )
        self.compaction_result.setReadOnly(True)
        layout = QVBoxLayout(widget)
        layout.setSpacing(METRICS["panel_spacing"])
        layout.addWidget(self.compaction_info)
        layout.addLayout(actions)
        layout.addWidget(self.compaction_result, stretch=1)
        layout.addStretch()
        return widget

    def start_diagnostic(self) -> None:
        if not self._begin_allowed(0):
            return
        self.invalidate_diagnostic()
        self._diagnostic_accept_result = True
        worker = DiagnosticWorker(self._diagnostics)
        worker.progress.connect(self._diagnostic_progress)
        worker.completed.connect(self._diagnostic_completed)
        self._start_worker(worker, "", message_id="maintenance.overview.status.running")

    def _diagnostic_progress(self, completed: int, total: int) -> None:
        if not self._accept_diagnostic_signal():
            return
        self._select_feedback_section(0)
        self.progress.setRange(0, total)
        self.progress.setValue(completed)

    def _diagnostic_completed(self, result: DiagnosticResult) -> None:
        if not self._accept_diagnostic_signal():
            if self.sender() is self._worker and self._diagnostic_accept_result:
                self._cancelled()
            return
        self._select_feedback_section(0)
        self._last_diagnostic = result
        self.set_last_audit(result.audit)
        self.overview_output.setPlainText(_render_diagnostic(result, self._translator))
        self.copy_report_button.setEnabled(True)
        self._set_overview_status_id(
            "maintenance.overview.status.completed",
            result.status.value == "failure",
            state="warning" if result.status.value == "attention" else "success",
        )

    def _accept_diagnostic_signal(self) -> bool:
        return (
            self.sender() is self._worker
            and isinstance(self._worker, DiagnosticWorker)
            and self._diagnostic_accept_result
            and not self._worker.cancellation_requested
        )

    def invalidate_diagnostic(self) -> None:
        """Discard feedback tied to an older environment or diagnostic run."""
        self._diagnostic_accept_result = False
        if isinstance(self._worker, DiagnosticWorker):
            self._worker.request_cancel()
        self._clear_diagnostic_result()
        self._overview_message_id = "maintenance.overview.status.invalidated"
        message = self._overview_status_text(self._overview_message_id)
        self._section_feedback[0] = _SectionFeedback(message, "warning", 0, 1, 0)
        if self._feedback_section == 0:
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self._set_overview_status_id(self._overview_message_id, state="warning")

    def _clear_diagnostic_result(self) -> None:
        self._last_diagnostic = None
        self._last_audit = None
        self.copy_report_button.setEnabled(False)
        self.overview_output.clear()
        self._last_audit_message_id = "maintenance.overview.audit.invalidated"
        self._render_last_audit()

    def copy_report(self) -> None:
        if self._last_diagnostic is not None:
            QApplication.clipboard().setText(
                safe_diagnostic_report(self._last_diagnostic)
            )

    def set_last_audit(self, report: AuditReport) -> None:
        self._last_audit = report
        self._render_last_audit()

    def choose_backup(self) -> None:
        if self._worker is not None:
            return
        selected, _ = QFileDialog.getOpenFileName(
            self, self._translator.text("backup.select"), "", "Backup (*.dmsbackup)"
        )
        if selected:
            self.backup_path_field.setText(selected)
            self._clear_backup_result()
            self._select_feedback_section(1)
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self._set_backup_status_id("backup.status.validation_pending")

    def start_backup_validation(self) -> None:
        if not self.backup_path_field.text():
            if self._worker is None:
                self._select_feedback_section(1)
            self._set_backup_status_id("maintenance.backup.error.input", True)
            return
        if not self._begin_allowed(1):
            self._set_backup_status_id("maintenance.backup.error.input", True)
            return
        self._clear_backup_result()
        self._backup_accept_result = True
        worker = BackupValidationWorker(
            self.backup_path_field.text(), self.backup_password_field.text()
        )
        worker.completed.connect(self._backup_completed)
        self._start_worker(worker, "", message_id="maintenance.backup.status.validating")

    def _backup_completed(self, result: BackupValidationResult) -> None:
        if not (self.sender() is self._worker and isinstance(self._worker, BackupValidationWorker)
                and self._backup_accept_result and not self._worker.cancellation_requested):
            if self.sender() is self._worker and self._backup_accept_result:
                self._cancelled()
            return
        self._select_feedback_section(1)
        self._last_backup_validation = result
        self._backup_result_message_id = None
        self.backup_result.setPlainText(_render_backup(result, self._translator))
        self._set_backup_status_id("backup.status.validated" if result.is_compatible else "backup.status.incompatible",
                                   state="success" if result.is_compatible else "warning")

    def start_temporary_scan(self) -> None:
        if not self._begin_allowed(2):
            return
        worker = TemporaryScanWorker(
            self._paths.directory, tuple(self._session_directories())
        )
        worker.completed.connect(self._temporaries_completed)
        self._temporary_accept_result = True
        self._temporary_succeeded = False
        self._start_worker(worker, "", message_id="maintenance.temporary.status.scanning")

    def _temporaries_completed(self, items: list[TemporaryItem]) -> None:
        if not (self.sender() is self._worker and isinstance(self._worker, TemporaryScanWorker)
                and self._temporary_accept_result):
            return
        self._temporary_accept_result = False
        self._temporary_succeeded = True
        self._select_feedback_section(2)
        self.temporary_items = items
        self._refresh_temporary_table()
        self._set_temporary_status_id("maintenance.temporary.status.found", state="success", count=len(items))

    def _temporary_toggled(self, row: int, checked: bool) -> None:
        if row < len(self.temporary_items):
            self.temporary_items[row].selected = checked
        self.cleanup_button.setEnabled(
            any(item.selected and item.removable for item in self.temporary_items)
        )

    def start_cleanup(self) -> None:
        selected = [
            item for item in self.temporary_items if item.selected and item.removable
        ]
        if not selected or not self._begin_allowed(2):
            return
        answer = confirm_destructive_action(
            self,
            self._translator.text("maintenance.temporary.confirm.title"),
            self._translator.text("maintenance.temporary.confirm.text", count=len(selected)),
            translator=self._translator,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        worker = TemporaryCleanupWorker(
            self.temporary_items,
            self._paths.directory,
            tuple(self._session_directories()),
        )
        worker.completed.connect(self._cleanup_completed)
        self._temporary_accept_result = True
        self._temporary_succeeded = False
        self._start_worker(worker, "", message_id="maintenance.temporary.status.cleaning")

    def _cleanup_completed(self, result: CleanupResult) -> None:
        if not (self.sender() is self._worker and isinstance(self._worker, TemporaryCleanupWorker)
                and self._temporary_accept_result):
            return
        self._temporary_accept_result = False
        self._temporary_succeeded = result.failed == 0
        self._select_feedback_section(2)
        if result.removed:
            self.invalidate_diagnostic()
        self._refresh_temporary_table()
        self._set_temporary_status_id(
            "maintenance.temporary.status.cleaned",
            result.failed > 0,
            state="warning" if result.preserved else "success",
            removed=result.removed, preserved=result.preserved, failed=result.failed,
        )

    def start_compaction(self) -> None:
        if self._worker is None:
            self._select_feedback_section(3)
        if not self._paths.vault_database_path.is_file():
            self._set_status("O cofre local ainda não existe.", True)
            return
        if not self._begin_allowed(3):
            return
        size = self._paths.vault_database_path.stat().st_size
        answer = QMessageBox.warning(
            self,
            "Confirmar compactação",
            f"Tamanho atual: {_format_bytes(size)}. A redução pode ser pequena. "
            "Recomenda-se criar antes um backup .dmsbackup. Deseja continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.invalidate_diagnostic()
        worker = CompactionWorker(self._compactor)
        worker.phase_changed.connect(self._compaction_phase)
        worker.completed.connect(self._compaction_completed)
        self._start_worker(worker, "Preparando compactação segura...")

    def _compaction_phase(self, stage: str, cancellation_allowed: bool) -> None:
        self._select_feedback_section(3)
        self.progress.setRange(0, 0)
        self.cancel_button.setEnabled(cancellation_allowed)
        self._set_status(stage, False)

    def _compaction_completed(self, result: CompactionResult) -> None:
        self._select_feedback_section(3)
        self.set_last_audit(result.audit)
        self.compaction_result.setPlainText(
            "\n".join(
                (
                    f"Tamanho anterior: {_format_bytes(result.size_before)}",
                    f"Tamanho final: {_format_bytes(result.size_after)}",
                    f"Espaço recuperado: {_format_bytes(result.recovered_bytes)}",
                    "Auditoria posterior concluída sem falhas.",
                )
            )
        )
        self.environment_changed.emit()
        self._set_status("Compactação concluída com segurança.", False, state="success")

    def _begin_allowed(self, section: int) -> bool:
        if self._worker is not None:
            return False
        self._select_feedback_section(section)
        if not self._prepare_operation():
            if section == 0:
                self._set_overview_status_id("maintenance.overview.error.busy", True)
            elif section == 1:
                self._set_backup_status_id("maintenance.overview.error.busy", True)
            elif section == 2:
                self._set_temporary_status_id("maintenance.overview.error.busy", True)
            else:
                self._set_status("Finalize as outras operações antes da manutenção.", True)
            return False
        return True

    def _start_worker(self, worker: QThread, message: str, *, message_id: str | None = None) -> None:
        self._worker = worker
        if hasattr(worker, "cancelled"):
            worker.cancelled.connect(self._cancelled)
        if hasattr(worker, "failed"):
            worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self._set_busy(True)
        self.progress.setRange(0, 0)
        if message_id is not None:
            if isinstance(worker, BackupValidationWorker):
                self._set_backup_status_id(message_id)
            elif isinstance(worker, (TemporaryScanWorker, TemporaryCleanupWorker)):
                self._set_temporary_status_id(message_id)
            else:
                self._set_overview_status_id(message_id)
        else:
            self._set_status(message, False)
        worker.start()

    def cancel(self) -> None:
        worker = self._worker
        if worker is not None and hasattr(worker, "request_cancel"):
            worker.request_cancel()
            if isinstance(worker, DiagnosticWorker):
                self._clear_diagnostic_result()
            elif isinstance(worker, BackupValidationWorker):
                self._clear_backup_result()
            self.cancel_button.setEnabled(False)
            if isinstance(worker, DiagnosticWorker):
                self._set_overview_status_id("masking.status.cancelling")
            elif isinstance(worker, BackupValidationWorker):
                self._set_backup_status_id("masking.status.cancelling")
            elif isinstance(worker, (TemporaryScanWorker, TemporaryCleanupWorker)):
                self._set_temporary_status_id("masking.status.cancelling")
            else:
                self._set_status("Cancelamento solicitado...", False)

    def _cancelled(self) -> None:
        if self.sender() is not self._worker:
            return
        if isinstance(self._worker, (TemporaryScanWorker, TemporaryCleanupWorker)):
            if not self._temporary_accept_result:
                return
            self._temporary_accept_result = False
            self._temporary_succeeded = False
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self._set_temporary_status_id("restoration.csv.status.cancelled", state="warning")
            return
        if isinstance(self._worker, BackupValidationWorker):
            if not self._backup_accept_result:
                return
            self._backup_accept_result = False
            self._clear_backup_result()
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self._set_backup_status_id("restoration.csv.status.cancelled", state="warning")
            return
        if isinstance(self._worker, DiagnosticWorker):
            if not self._diagnostic_accept_result:
                return
            self._diagnostic_accept_result = False
            self._clear_diagnostic_result()
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        if isinstance(self._worker, DiagnosticWorker):
            self._set_overview_status_id("restoration.csv.status.cancelled", state="warning")
        else:
            self._set_status("Operação cancelada com segurança.", False, state="warning")

    def _failed(self, error: Exception) -> None:
        if self.sender() is not self._worker:
            return
        if isinstance(self._worker, (TemporaryScanWorker, TemporaryCleanupWorker)):
            if not self._temporary_accept_result:
                return
            self._temporary_accept_result = False
            self._temporary_succeeded = False
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            if isinstance(error, (MaintenanceError, BackupError)):
                self._set_status(str(error), True)
            else:
                self._set_temporary_status_id("maintenance.overview.error.unexpected", True)
            return
        if isinstance(self._worker, BackupValidationWorker):
            if not self._backup_accept_result:
                return
            self._backup_accept_result = False
            self._clear_backup_result()
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self._backup_result_message_id = "maintenance.backup.result.invalid"
            self.backup_result.setPlainText(self._translator.text(self._backup_result_message_id))
            if isinstance(error, BackupValidationError):
                self._set_backup_status_id("backup.error.validation", True)
            elif isinstance(error, (MaintenanceError, BackupError)):
                self._set_status(str(error), True)
            else:
                self._set_backup_status_id("maintenance.overview.error.unexpected", True)
            return
        if isinstance(self._worker, DiagnosticWorker):
            if not self._diagnostic_accept_result:
                return
            self._diagnostic_accept_result = False
            self._clear_diagnostic_result()
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
        message = (
            str(error)
            if isinstance(error, (MaintenanceError, BackupError))
            else "A operação de manutenção falhou com segurança."
        )
        if isinstance(self._worker, DiagnosticWorker) and not isinstance(error, (MaintenanceError, BackupError)):
            self._set_overview_status_id("maintenance.overview.error.unexpected", True)
        else:
            self._set_status(message, True)

    def _finished(self) -> None:
        if self.sender() is not self._worker:
            return
        worker = self._worker
        if (
            isinstance(worker, DiagnosticWorker)
            and worker.cancellation_requested
            and self._diagnostic_accept_result
        ):
            self._cancelled()
        if (isinstance(worker, BackupValidationWorker) and worker.cancellation_requested
                and self._backup_accept_result):
            self._cancelled()
        diagnostic_succeeded = (
            self._last_diagnostic is not None
            and self._last_diagnostic.status is not MaintenanceStatus.FAILURE
        )
        self._diagnostic_accept_result = False
        self._backup_accept_result = False
        self._temporary_accept_result = False
        self._worker = None
        self.backup_password_field.clear()
        self.show_backup_password.setChecked(False)
        if worker is not None:
            worker.deleteLater()
        self._set_busy(
            False, completed=(self._last_backup_validation is not None if isinstance(worker, BackupValidationWorker)
                              else self._temporary_succeeded if isinstance(worker, (TemporaryScanWorker, TemporaryCleanupWorker))
                              else not isinstance(worker, DiagnosticWorker) or diagnostic_succeeded)
        )
        self._remember_feedback()

    def _set_busy(self, busy: bool, *, completed: bool = True) -> None:
        self.sections.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        if not busy:
            self.progress.setRange(0, 1)
            self.progress.setValue(int(completed))
        self.busy_changed.emit(busy)

    def has_running_worker(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def stop_worker(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            if hasattr(self._worker, "request_cancel"):
                self._worker.request_cancel()
            return self._worker.wait(10000)
        return True

    def _refresh_temporary_table(self) -> None:
        self.temporary_table.setRowCount(len(self.temporary_items))
        for row, item in enumerate(self.temporary_items):
            checkbox = QCheckBox()
            checkbox.setChecked(item.selected)
            checkbox.setEnabled(item.removable and not item.result.startswith("Removido"))
            checkbox.toggled.connect(
                lambda checked, current=row: self._temporary_toggled(current, checked)
            )
            self.temporary_table.setCellWidget(row, 0, checkbox)
            checkbox.setAccessibleName(self._translator.text("maintenance.temporary.select_accessible", row=row + 1))
            status = self._temporary_status(item)
            values = (
                str(item.path),
                _format_bytes(item.size),
                _format_age(item.age_seconds),
                status,
            )
            for column, value in enumerate(values, start=1):
                cell = QTableWidgetItem(value)
                if column == 1:
                    cell.setToolTip(value)
                self.temporary_table.setItem(row, column, cell)
        self.cleanup_button.setEnabled(
            any(item.selected and item.removable for item in self.temporary_items)
        )

    def _temporary_status(self, item: TemporaryItem) -> str:
        # Service outcomes are free-form, not a typed translation contract.
        if item.result:
            return item.result
        identifier = ("maintenance.temporary.recent" if item.recent
                      else "maintenance.temporary.in_use" if item.in_use
                      else "maintenance.temporary.available")
        return self._translator.text(identifier)

    def _set_status(
        self, message: str, error: bool, *, state: FeedbackState = "neutral"
    ) -> None:
        if self._feedback_section == 0:
            self._overview_message_id = None
        elif self._feedback_section == 1:
            self._backup_message_id = None
        elif self._feedback_section == 2:
            self._temporary_message = None
        self.status_label.setText(message)
        set_feedback_state(self.status_label, "error" if error else state)
        self._remember_feedback()
        self._update_feedback_visibility()


def _render_diagnostic(result: DiagnosticResult, translator: Translator | None = None) -> str:
    translator = translator or Translator("pt-BR")
    text = translator.text
    stats = result.statistics
    distribution = ", ".join(
        f"{rule}: {count}" for rule, count in stats.normalization_distribution
    ) or text("maintenance.value.none")
    unavailable = text("maintenance.value.unavailable")
    return "\n".join(
        (
            text("maintenance.overview.state", status=text(f"maintenance.status.{result.status.value}")),
            text("maintenance.overview.hmac", status=text(f"maintenance.integrity.{_check_status(result, 'Disponibilidade da chave HMAC')}")),
            text("maintenance.overview.aes", status=text(f"maintenance.integrity.{_check_status(result, 'Disponibilidade da chave AES')}")),
            text("maintenance.overview.schema", version=stats.schema_version if stats.schema_version is not None else unavailable),
            text("maintenance.overview.vault_size", size=_format_bytes(stats.vault_size)),
            text("backup.summary.mappings", count=stats.mapping_count),
            text("maintenance.overview.variations", count=stats.variation_count),
            text("maintenance.overview.occurrences", count=stats.total_occurrences),
            text("backup.summary.profiles", count=stats.profile_count),
            text("maintenance.overview.prefixes", count=stats.prefix_count),
            text("maintenance.overview.normalization", distribution=distribution),
            text("maintenance.overview.first_entry", timestamp=stats.first_entry or unavailable),
            text("maintenance.overview.last_entry", timestamp=stats.last_entry or unavailable),
            text("maintenance.overview.auxiliaries", wal=text("backup.value.yes" if stats.wal_present else "backup.value.no"),
                 shm=text("backup.value.yes" if stats.shm_present else "backup.value.no"),
                 journal=text("backup.value.yes" if stats.journal_present else "backup.value.no")),
            text("maintenance.overview.environment_size", size=_format_bytes(stats.environment_size)),
            text("maintenance.overview.free_space", size=_format_bytes(stats.free_space)),
            *stats.errors,
            "",
            _render_integrity_details(result.audit, translator),
        )
    )


def _render_integrity_details(report: AuditReport, translator: Translator) -> str:
    """Same safe check fields as the canonical report; never render paths or values."""
    text = translator.text
    lines = [
        text("maintenance.integrity.overall", status=text(f"maintenance.integrity.{report.status.value}")),
        text("maintenance.integrity.started", timestamp=report.started_at.astimezone().isoformat(timespec="seconds")),
        text("maintenance.integrity.finished", timestamp=report.finished_at.astimezone().isoformat(timespec="seconds")),
        text("maintenance.integrity.checks"),
    ]
    for check in report.checks:
        line = text("maintenance.integrity.check", status=text(f"maintenance.integrity.{check.status.value}"),
                    check_type=check.check_type, examined=check.examined, failures=check.failures, message=check.message)
        if check.identifiers:
            line += text("maintenance.integrity.references", identifiers=", ".join(check.identifiers))
        lines.append(line)
    return "\n".join(lines)


def _check_status(result: DiagnosticResult, name: str) -> str:
    check = next(item for item in result.audit.checks if item.check_type == name)
    return check.status.value


def _render_backup(result: BackupValidationResult, translator: Translator | None = None) -> str:
    text = (translator or Translator("pt-BR")).text
    return "\n".join(
        (
            text("maintenance.backup.summary.valid_file"),
            text("maintenance.backup.summary.created", timestamp=result.created_at.astimezone().isoformat(timespec="seconds")),
            text("backup.summary.application", version=result.application_version),
            text("backup.summary.format", version=result.format_version),
            text("maintenance.overview.schema", version=result.vault_schema_version),
            text("backup.summary.mappings", count=result.mapping_count),
            text("backup.summary.profiles", count=result.profile_count),
            text("maintenance.backup.summary.compatible_yes" if result.is_compatible else "maintenance.backup.summary.compatible_no"),
        )
    )


def _format_bytes(value: int) -> str:
    if value < 1024:
        return f"{value} B"
    if value < 1024**2:
        return f"{value / 1024:.1f} KiB"
    return f"{value / 1024**2:.1f} MiB"


def _format_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    return f"{seconds / 3600:.1f} h"

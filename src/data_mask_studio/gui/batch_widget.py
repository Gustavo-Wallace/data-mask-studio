from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox

from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.batch import (
    BatchError,
    BatchFile,
    BatchFileStatus,
    BatchExplanation,
    BatchReason,
    BatchProgress,
    BatchService,
    BatchSummary,
    add_files,
    discover_csv_files,
    invalidate_files,
)
from data_mask_studio.gui.batch_worker import (
    BatchProcessingWorker,
    BatchValidationWorker,
)
from data_mask_studio.gui.components import EmptyStateTable, EmptyStateTextEdit
from data_mask_studio.gui.components.presentation import (
    FeedbackState, configure_result_area, set_button_role, set_feedback_state,
)
from data_mask_studio.gui.visual_tokens import METRICS
from data_mask_studio.gui.dialog_localization import localize_standard_buttons
from data_mask_studio.i18n import Translator
from data_mask_studio.profiles import (
    ConfigurationProfile,
    ProfileError,
    ProfileService,
)
from data_mask_studio.security import KeyProvider
from data_mask_studio.vault import VaultRepository

VaultRepositoryFactory = Callable[[], VaultRepository]

_RESULT_REASONS = {
    BatchReason.COMPATIBLE: ("batch.result.compatible", BatchFileStatus.COMPATIBLE),
    BatchReason.HEADER_MISMATCH: (None, BatchFileStatus.INCOMPATIBLE),
    BatchReason.PROCESSING: ("batch.result.processing", BatchFileStatus.PROCESSING),
    BatchReason.COMPLETED: ("batch.result.completed", BatchFileStatus.COMPLETED),
    BatchReason.CANCELLED: ("batch.result.cancelled", BatchFileStatus.CANCELLED),
    BatchReason.NOT_PROCESSED_CANCELLED: ("batch.result.not_processed_cancelled", BatchFileStatus.CANCELLED),
    BatchReason.SKIPPED_STRUCTURAL: ("batch.result.skipped_structural", BatchFileStatus.SKIPPED),
    BatchReason.STRUCTURAL_FAILURE: ("batch.result.structural_failure", BatchFileStatus.ERROR),
}


class BatchWidget(QWidget):
    environment_may_change = Signal()

    def __init__(
        self,
        profile_service: ProfileService | None,
        key_provider: KeyProvider,
        vault_repository_factory: VaultRepositoryFactory,
        *, translator: Translator | None = None,
    ) -> None:
        super().__init__()
        self._translator = translator or Translator("pt-BR")
        self._status_message = None
        self._current_progress: BatchProgress | None = None
        self._current_idle = True
        self._displayed_summary: BatchSummary | None = None
        self._output_confirmation: QMessageBox | None = None
        self._profile_service = profile_service
        self._service = BatchService(profile_service) if profile_service else None
        self._key_provider = key_provider
        self._vault_repository_factory = vault_repository_factory
        self._profiles: list[ConfigurationProfile] = []
        self.files: list[BatchFile] = []
        self._validation_worker: BatchValidationWorker | None = None
        self._processing_worker: BatchProcessingWorker | None = None
        self._profiles_refresh_pending = False
        self._requires_revalidation = False
        self._output_directory: Path | None = None

        self.add_files_button = QPushButton("Adicionar arquivos")
        set_button_role(self.add_files_button, "primary")
        self.add_files_button.clicked.connect(self._choose_files)
        self.add_folder_button = QPushButton("Adicionar pasta")
        set_button_role(self.add_folder_button, "primary")
        self.add_folder_button.clicked.connect(self._choose_folder)
        self.remove_button = QPushButton("Remover selecionados")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton("Limpar lista")
        self.clear_button.clicked.connect(self.clear_files)
        file_actions = QVBoxLayout()
        add_actions = QHBoxLayout()
        add_actions.addWidget(self.add_files_button)
        add_actions.addWidget(self.add_folder_button)
        add_actions.addStretch()
        list_actions = QHBoxLayout()
        list_actions.addWidget(self.remove_button)
        list_actions.addWidget(self.clear_button)
        list_actions.addStretch()
        file_actions.addLayout(add_actions)
        file_actions.addLayout(list_actions)

        self.file_table = EmptyStateTable(0, 5, "Nenhum arquivo adicionado.")
        self.file_table.setHorizontalHeaderLabels(
            ["Arquivo", "Caminho", "Status", "Colunas", "Resultado"]
        )
        self.file_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.verticalHeader().setVisible(False)
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.file_table.setWordWrap(False)
        self.file_table.setMinimumHeight(
            header.sizeHint().height()
            + 2 * self.file_table.verticalHeader().defaultSectionSize()
            + 2 * self.file_table.frameWidth()
        )

        self.profile_combo = ScrollSafeComboBox()
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)

        self.output_field = QLineEdit()
        self.output_field.setPlaceholderText("Escolha ou informe a pasta de saída")
        self.output_field.textChanged.connect(self._update_actions)
        self.choose_output_button = QPushButton("Escolher pasta")
        self.choose_output_button.clicked.connect(self._choose_output_directory)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_field, stretch=1)
        output_row.addWidget(self.choose_output_button)
        configuration = QFormLayout()
        configuration.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.profile_label = QLabel()
        self.profile_label.setBuddy(self.profile_combo)
        self.output_label = QLabel()
        self.output_label.setBuddy(self.output_field)
        configuration.addRow(self.profile_label, self.profile_combo)
        configuration.addRow(self.output_label, output_row)

        self.validate_button = QPushButton("Validar arquivos")
        self.validate_button.clicked.connect(self.validate_files)
        self.start_button = QPushButton("Iniciar mascaramento")
        set_button_role(self.start_button, "primary")
        self.start_button.clicked.connect(self.start_processing)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setVisible(False)
        self.open_output_button = QPushButton("Abrir pasta de saída")
        self.open_output_button.clicked.connect(self.open_output_directory)
        self.open_output_button.setEnabled(False)
        processing_actions = QHBoxLayout()
        processing_actions.addWidget(self.validate_button)
        processing_actions.addWidget(self.start_button)
        processing_actions.addWidget(self.cancel_button)
        processing_actions.addStretch()
        processing_actions.addWidget(self.open_output_button)

        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 1)
        self.overall_progress.setValue(0)
        self.current_progress_label = QLabel("Nenhum processamento em andamento.")
        self.current_progress_label.setWordWrap(True)
        self.current_progress_label.setTextFormat(Qt.TextFormat.PlainText)
        self.summary_output = EmptyStateTextEdit(
            "O resumo do lote aparecerá aqui."
        )
        self.summary_output.setReadOnly(True)
        self.summary_output.setMinimumHeight(self.summary_output.minimumSizeHint().height())
        configure_result_area(self.summary_output, 80)
        self.status_label = QLabel("Adicione arquivos CSV para começar.")
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        set_feedback_state(self.status_label, "neutral")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(METRICS["panel_spacing"])
        layout.addLayout(file_actions)
        layout.addWidget(self.file_table, stretch=1)
        layout.addLayout(configuration)
        layout.addLayout(processing_actions)
        layout.addWidget(self.overall_progress)
        layout.addWidget(self.current_progress_label)
        layout.addWidget(self.summary_output)
        layout.addWidget(self.status_label)
        layout.addStretch()
        self.file_table.model().rowsInserted.connect(self._update_information_layout)
        self.file_table.model().rowsRemoved.connect(self._update_information_layout)
        self.summary_output.textChanged.connect(self._update_information_layout)
        self._update_information_layout()

        self._set_status_id("batch.status.initial")
        self.refresh_profiles()
        self._update_actions()
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        """Presentation only: no profile reload, model reset, gating or worker calls."""
        self._translator = translator
        for widget, message_id in (
            (self.add_files_button, "batch.add_files"),
            (self.add_folder_button, "batch.add_folder"),
            (self.remove_button, "batch.remove"),
            (self.clear_button, "batch.clear"),
            (self.profile_label, "batch.profile"),
            (self.output_label, "batch.output_folder"),
            (self.choose_output_button, "batch.choose_folder"),
            (self.validate_button, "batch.validate"),
            (self.start_button, "batch.start"),
            (self.cancel_button, "dialog.button.cancel"),
            (self.open_output_button, "batch.open_output"),
        ):
            widget.setText(translator.text(message_id))
        self.profile_combo.setAccessibleName(translator.text("batch.profile_accessible"))
        self.output_field.setAccessibleName(translator.text("batch.output_accessible"))
        self.output_field.setPlaceholderText(translator.text("batch.output_placeholder"))
        self.file_table.empty_text = translator.text("batch.files_empty")
        self.file_table.setAccessibleDescription(self.file_table.empty_text)
        self.summary_output.empty_text = translator.text("batch.summary_empty")
        self.summary_output.setAccessibleDescription(self.summary_output.empty_text)
        with QSignalBlocker(self.file_table):
            self.file_table.setHorizontalHeaderLabels([
                translator.text(f"batch.table.{column}")
                for column in ("file", "path", "status", "columns", "result")
            ])
            for row, item in enumerate(self.files):
                self.file_table.item(row, 2).setText(translator.text(f"batch.file_status.{item.status.value}"))
                result = self._result_text(item)
                self.file_table.item(row, 4).setText(result)
                self.file_table.item(row, 4).setToolTip(result)
        self.file_table.viewport().update()
        self.summary_output.viewport().update()
        if self._status_message is not None:
            message_id, parameters, plural = self._status_message
            self.status_label.setText(self._status_text(message_id, parameters, plural))
        self._render_current_progress()
        if self._displayed_summary is not None:
            # Keep read-only report selection and scroll position as well as focus.
            cursor = self.summary_output.textCursor()
            anchor, position = cursor.anchor(), cursor.position()
            horizontal = self.summary_output.horizontalScrollBar().value()
            vertical = self.summary_output.verticalScrollBar().value()
            with QSignalBlocker(self.summary_output):
                self.summary_output.setPlainText(_render_summary(self._displayed_summary, translator))
                maximum = self.summary_output.document().characterCount() - 1
                cursor.setPosition(min(anchor, maximum))
                cursor.setPosition(min(position, maximum), QTextCursor.MoveMode.KeepAnchor)
                self.summary_output.setTextCursor(cursor)
            self.summary_output.horizontalScrollBar().setValue(horizontal)
            self.summary_output.verticalScrollBar().setValue(vertical)
        if self._output_confirmation is not None:
            self._retranslate_output_confirmation()

    def _set_status_id(self, message_id: str, *, is_error: bool = False,
                       state: FeedbackState = "neutral", plural: bool = False,
                       **parameters: str | int | float) -> None:
        text = self._status_text(message_id, parameters, plural)
        self._set_status(text, is_error=is_error, state=state)
        self._status_message = (message_id, parameters, plural)

    def _status_text(self, message_id: str, parameters: dict, plural: bool) -> str:
        if plural:
            return self._translator.plural(message_id, parameters["count"], **{
                name: value for name, value in parameters.items() if name != "count"
            })
        return self._translator.text(message_id, **parameters)

    def _clear_summary(self) -> None:
        self._displayed_summary = None
        self.summary_output.clear()

    def _clear_current_progress(self) -> None:
        self._current_progress = None
        self._current_idle = False
        self.current_progress_label.clear()

    def _render_current_progress(self) -> None:
        progress = self._current_progress
        if progress is not None:
            self.current_progress_label.setText(self._translator.text(
                "batch.current.progress", current=progress.current_file,
                total=progress.compatible_files, filename=progress.file_name,
                records=self._translator.plural("batch.records", progress.records_processed),
                completed=progress.completed_files, errors=progress.error_files,
            ))
        else:
            self.current_progress_label.setText(
                self._translator.text("batch.current.idle") if self._current_idle else ""
            )

    def _result_text(self, item: BatchFile) -> str:
        explanation = item.result_explanation
        if explanation is not None:
            presentation = (_RESULT_REASONS.get(explanation.reason)
                            if isinstance(explanation, BatchExplanation)
                            and isinstance(explanation.reason, BatchReason) else None)
            if presentation is None or item.status is not presentation[1]:
                return item.result_message
            if explanation.reason is BatchReason.HEADER_MISMATCH:
                parts = []
                if explanation.missing_headers:
                    parts.append(self._translator.text("masking.error.missing_headers",
                        headers=", ".join(explanation.missing_headers)))
                if explanation.extra_headers:
                    parts.append(self._translator.text("masking.error.unknown_columns",
                        columns=", ".join(explanation.extra_headers)))
                if not parts:
                    return item.result_message
                text = " ".join(parts)
            elif explanation.reason is not BatchReason.COMPLETED:
                text = self._translator.text(presentation[0])
            else:
                if item.processing_result is None:
                    return item.result_message
                text = self._completed_result_text(item)
            return text + (" " + explanation.detail if explanation.detail else "")
        # Legacy successful results already provide typed normalization data.
        return self._completed_result_text(item)

    def _completed_result_text(self, item: BatchFile) -> str:
        if item.status is not BatchFileStatus.COMPLETED or item.processing_result is None:
            return item.result_message
        text = self._translator.text("batch.result.completed")
        if item.processing_result.normalization_fallbacks:
            details = "; ".join(
                self._translator.text("batch.result.fallback_detail", header=value.header, count=value.count)
                for value in item.processing_result.normalization_fallbacks
            )
            text += self._translator.text("batch.result.fallbacks", details=details)
        return text

    def _confirm_create_output(self) -> int:
        dialog = QMessageBox(QMessageBox.Icon.Question, "", "",
                             QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        dialog.setEscapeButton(QMessageBox.StandardButton.No)
        self._output_confirmation = dialog
        self._retranslate_output_confirmation()
        try:
            return dialog.exec()
        finally:
            self._output_confirmation = None
            dialog.deleteLater()

    def _retranslate_output_confirmation(self) -> None:
        self._output_confirmation.setWindowTitle(self._translator.text("batch.output.create_title"))
        self._output_confirmation.setText(self._translator.text("batch.output.create_text"))
        localize_standard_buttons(self._output_confirmation, self._translator)

    def _update_information_layout(self) -> None:
        has_files = self.file_table.rowCount() > 0
        has_summary = bool(self.summary_output.toPlainText().strip())
        self.file_table.setMaximumHeight(16_777_215 if has_files else 160)
        # Populated views share spare space without forcing their preferred
        # heights on the outer page. Empty views remain compact and visible.
        for view, populated in ((self.file_table, has_files), (self.summary_output, has_summary)):
            policy = view.sizePolicy()
            policy.setVerticalPolicy(
                QSizePolicy.Policy.Ignored if populated else QSizePolicy.Policy.Preferred
            )
            view.setSizePolicy(policy)
        layout = self.layout()
        layout.setStretch(layout.indexOf(self.file_table), int(has_files))
        layout.setStretch(layout.indexOf(self.summary_output), int(has_summary))
        layout.setStretch(layout.count() - 1, int(not has_files and not has_summary))

    def refresh_profiles(self) -> None:
        # Keep worker-owned files and the selected profile untouched until all
        # queued result signals have been handled, including after run() returns.
        if self._validation_worker is not None or self._processing_worker is not None:
            self._profiles_refresh_pending = True
            return
        self._reload_profiles(preserve_results=False)

    def _reload_profiles(self, *, preserve_results: bool) -> None:
        selected = self.profile_combo.currentData()
        previous_profile = self._selected_profile()
        try:
            self._profiles = (
                self._profile_service.list_profiles()
                if self._profile_service is not None
                else []
            )
        except ProfileError as error:
            self._profiles = []
            self._set_status(str(error), is_error=True)
        self.profile_combo.blockSignals(True)
        self.profile_combo.clear()
        for profile in self._profiles:
            self.profile_combo.addItem(profile.name, profile.identifier)
        index = self.profile_combo.findData(selected)
        if index >= 0:
            self.profile_combo.setCurrentIndex(index)
        self.profile_combo.blockSignals(False)
        current_profile = self._selected_profile()
        if self.files and previous_profile is not None and (
            current_profile is None
            or current_profile.identifier != previous_profile.identifier
            or current_profile.columns != previous_profile.columns
            or current_profile.composites != previous_profile.composites
            or current_profile.unknown_column_policy != previous_profile.unknown_column_policy
        ):
            self._requires_revalidation = True
            if not preserve_results:
                invalidate_files(self.files)
                self._refresh_table()
        self._update_actions()

    def _refresh_pending_profiles(self) -> None:
        if self._profiles_refresh_pending:
            self._profiles_refresh_pending = False
            # Reload the selector, not the just-completed operation's results.
            self._reload_profiles(preserve_results=True)

    def add_paths(self, paths: Iterable[str | Path]) -> int:
        added = add_files(self.files, paths)
        if added:
            self._refresh_table()
            self._set_status_id("batch.files_added", count=added, plural=True)
        self._update_actions()
        return added

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, self._translator.text("batch.files_title"), "", self._translator.text("masking.file.filter")
        )
        if paths:
            self.add_paths(paths)

    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, self._translator.text("batch.add_folder"))
        if not directory:
            return
        try:
            paths = discover_csv_files(directory)
        except BatchError as error:
            self._set_status(str(error), is_error=True)
            return
        if not paths:
            self._set_status_id("batch.folder_empty")
            return
        self.add_paths(paths)

    def remove_selected(self) -> None:
        rows = sorted(
            {index.row() for index in self.file_table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            del self.files[row]
        if rows:
            self._refresh_table()
        self._update_actions()

    def clear_files(self) -> None:
        self.files.clear()
        self._refresh_table()
        self._clear_summary()
        self._output_directory = None
        self.open_output_button.setEnabled(False)
        self.overall_progress.setRange(0, 1)
        self.overall_progress.setValue(0)
        self._current_progress = None
        self._current_idle = True
        self._render_current_progress()
        self._set_status_id("batch.status.initial")
        self._update_actions()

    def _profile_changed(self) -> None:
        self._requires_revalidation = True
        if self._validation_worker is not None or self._processing_worker is not None:
            self._profiles_refresh_pending = True
            return
        invalidate_files(self.files)
        self._refresh_table()
        self._set_status_id("batch.status.profile_changed")
        self._update_actions()

    def _selected_profile(self) -> ConfigurationProfile | None:
        identifier = self.profile_combo.currentData()
        return next(
            (profile for profile in self._profiles if profile.identifier == identifier),
            None,
        )

    def validate_files(self) -> None:
        if self._validation_worker is not None or self._processing_worker is not None:
            return
        profile = self._selected_profile()
        if not self.files:
            self._set_status_id("batch.error.no_files", is_error=True)
            return
        if profile is None or self._service is None:
            self._set_status_id("batch.error.no_profile", is_error=True)
            return
        # Start a fresh validation generation before the worker visits any file.
        # Unvisited files must not retain results from an earlier batch.
        self._requires_revalidation = True
        invalidate_files(self.files)
        self._refresh_table()
        self._clear_summary()
        self._set_busy(True, validating=True)
        worker = BatchValidationWorker(self._service, self.files, profile)
        self._validation_worker = worker
        worker.file_validated.connect(self._file_changed)
        worker.completed.connect(self._validation_completed)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(self._validation_finished)
        worker.start()

    def _validation_completed(self) -> None:
        compatible = sum(
            item.status is BatchFileStatus.COMPATIBLE for item in self.files
        )
        incompatible = sum(
            item.status is BatchFileStatus.INCOMPATIBLE for item in self.files
        )
        incomplete = any(
            item.status not in (BatchFileStatus.COMPATIBLE, BatchFileStatus.INCOMPATIBLE)
            for item in self.files
        )
        # A busy selector change cannot reuse validation of the worker's profile.
        self._requires_revalidation = incomplete or (
            self._validation_worker is not None
            and self._validation_worker._profile != self._selected_profile()
        )
        if incomplete:
            self._set_status_id("batch.status.validation_interrupted", compatible=compatible,
                                incompatible=incompatible, state="warning")
            return
        self._set_status_id(
            "batch.status.validated", compatible=compatible, incompatible=incompatible,
            is_error=compatible == 0,
            state="warning" if incompatible else "success",
        )

    def _validation_finished(self) -> None:
        worker = self._validation_worker
        self._validation_worker = None
        if worker is not None:
            worker.deleteLater()
        self._refresh_pending_profiles()
        self._set_busy(False)

    def _choose_output_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, self._translator.text("batch.output.choose_title"))
        if directory:
            self.output_field.setText(directory)

    def start_processing(self) -> None:
        if self._validation_worker is not None or self._processing_worker is not None:
            return
        profile = self._selected_profile()
        if profile is None or self._service is None:
            self._set_status_id("batch.error.no_profile", is_error=True)
            return
        if self._requires_revalidation or not self.files or any(
            item.status is BatchFileStatus.PENDING for item in self.files
        ):
            self._set_status_id("batch.error.validate_first", is_error=True)
            return
        if not any(item.status is BatchFileStatus.COMPATIBLE for item in self.files):
            self._set_status_id("batch.error.no_compatible", is_error=True)
            return
        output_text = self.output_field.text().strip()
        if not output_text:
            self._set_status_id("batch.error.no_output", is_error=True)
            return
        output = Path(output_text).expanduser()
        if not output.exists():
            answer = self._confirm_create_output()
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                output.mkdir(parents=True, exist_ok=False)
            except OSError:
                self._set_status_id("batch.error.create_output", is_error=True)
                return
        if not output.is_dir():
            self._set_status_id("batch.error.invalid_output", is_error=True)
            return

        # A configured destination is not evidence that this run published files.
        self._output_directory = None
        self.open_output_button.setEnabled(False)
        self._clear_summary()
        compatible_count = sum(
            item.status is BatchFileStatus.COMPATIBLE for item in self.files
        )
        self.overall_progress.setRange(0, compatible_count)
        self.overall_progress.setValue(0)
        self._set_busy(True, validating=False)
        worker = BatchProcessingWorker(
            self._service,
            self.files,
            profile,
            str(output.absolute()),
            self._key_provider,
            self._vault_repository_factory,
        )
        self._processing_worker = worker
        worker.file_changed.connect(self._file_changed)
        worker.progress.connect(self._progress_changed)
        worker.completed.connect(self._processing_completed)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(self._processing_finished)
        self.environment_may_change.emit()
        worker.start()

    def cancel(self) -> None:
        worker = self._processing_worker or self._validation_worker
        if worker is not None:
            worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self._set_status_id("batch.status.cancelling")

    def _file_changed(self, _item: BatchFile) -> None:
        if (self._processing_worker is not None
                and _item.status is BatchFileStatus.COMPLETED
                and _item.output_path is not None):
            self._output_directory = _item.output_path.parent
            self.open_output_button.setEnabled(True)
        self._refresh_table()

    def _progress_changed(self, progress: BatchProgress) -> None:
        self.overall_progress.setValue(progress.completed_files + progress.error_files)
        self._current_progress = progress
        self._render_current_progress()

    def _processing_completed(self, summary: BatchSummary) -> None:
        self.overall_progress.setValue(
            summary.completed_files
            + summary.error_files
            + summary.cancelled_or_skipped_files
        )
        self._displayed_summary = summary
        self.summary_output.setPlainText(_render_summary(summary, self._translator))
        if summary.completed_files > 0:
            self._output_directory = summary.output_directory
        self.open_output_button.setEnabled(self._output_directory is not None)
        self._set_status_id(
            "batch.status.completed", is_error=False,
            state="error" if summary.error_files else "warning"
            if summary.incompatible_files or summary.cancelled_or_skipped_files
            or summary.normalization_fallbacks else "success",
        )

    def _processing_finished(self) -> None:
        worker = self._processing_worker
        self._processing_worker = None
        if worker is not None:
            worker.deleteLater()
        self._clear_current_progress()
        self._refresh_pending_profiles()
        self._set_busy(False)

    def _worker_failed(self, error: Exception) -> None:
        if isinstance(error, BatchError):
            self._set_status(str(error), is_error=True)
        else:
            self._set_status_id("batch.error.unexpected", is_error=True)
        self._refresh_table()

    def _set_busy(self, busy: bool, *, validating: bool = False) -> None:
        for control in (
            self.add_files_button,
            self.add_folder_button,
            self.remove_button,
            self.clear_button,
            self.profile_combo,
            self.output_field,
            self.choose_output_button,
            self.validate_button,
            self.start_button,
        ):
            control.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        if busy:
            self._clear_current_progress()
            if validating:
                self.overall_progress.setRange(0, 1)
                self.overall_progress.setValue(0)
            self._set_status_id("batch.status.validating" if validating else "batch.status.processing")
        else:
            self._update_actions()

    def _update_actions(self, *_args: object) -> None:
        busy = self._validation_worker is not None or self._processing_worker is not None
        if busy:
            return
        has_files = bool(self.files)
        has_profile = self._selected_profile() is not None
        self.remove_button.setEnabled(has_files)
        self.clear_button.setEnabled(has_files)
        self.validate_button.setEnabled(has_files and has_profile)
        self.start_button.setEnabled(
            has_files
            and has_profile
            and not self._requires_revalidation
            and bool(self.output_field.text().strip())
            and not any(item.status is BatchFileStatus.PENDING for item in self.files)
            and any(item.status is BatchFileStatus.COMPATIBLE for item in self.files)
        )

    def _refresh_table(self) -> None:
        self.file_table.setRowCount(len(self.files))
        for row, item in enumerate(self.files):
            values = (
                item.path.name,
                str(item.path),
                self._translator.text(f"batch.file_status.{item.status.value}"),
                "—" if item.column_count is None else str(item.column_count),
                self._result_text(item),
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column in (0, 1, 4):
                    cell.setToolTip(str(item.path) if column in (0, 1) else value)
                self.file_table.setItem(row, column, cell)

    def open_output_directory(self) -> None:
        if self._output_directory is None:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_directory))):
            self._set_status_id("batch.error.open_output", is_error=True)

    def stop_workers(self) -> bool:
        workers = [self._validation_worker, self._processing_worker]
        for worker in workers:
            if worker is not None and worker.isRunning():
                worker.request_cancel()
        stopped = True
        for worker in workers:
            if worker is not None and not worker.wait(5000):
                stopped = False
        return stopped

    def has_running_workers(self) -> bool:
        return any(
            worker is not None and worker.isRunning()
            for worker in (self._validation_worker, self._processing_worker)
        )

    def _set_status(
        self, message: str, *, is_error: bool, state: FeedbackState = "neutral"
    ) -> None:
        self._status_message = None
        self.status_label.setText(message)
        set_feedback_state(self.status_label, "error" if is_error else state)


def _render_summary(summary: BatchSummary, translator: Translator | None = None) -> str:
    translator = translator or Translator("pt-BR")
    lines = [
        translator.text(f"batch.summary.{name}", count=value)
        for name, value in (
            ("selected", summary.selected_files), ("compatible", summary.compatible_files),
            ("completed", summary.completed_files), ("incompatible", summary.incompatible_files),
            ("errors", summary.error_files), ("cancelled", summary.cancelled_or_skipped_files),
            ("records", summary.records_processed), ("new_mappings", summary.new_mappings),
            ("updated_mappings", summary.updated_mappings),
        )
    ]
    lines.extend((
        translator.text("batch.summary.duration", seconds=f"{summary.duration_seconds:.2f}"),
        translator.text("batch.summary.output", path=str(summary.output_directory)),
    ))
    if summary.normalization_fallbacks:
        lines.append(
            translator.text("batch.summary.fallbacks", details="; ".join(
                translator.text("masking.status.fallback_detail", header=item.header, count=item.count)
                for item in summary.normalization_fallbacks
            ))
        )
    return "\n".join(lines)

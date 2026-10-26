from collections.abc import Callable
from functools import partial
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox

from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
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

from data_mask_studio.csv_tools.csv_anonymizer import paths_refer_to_same_file
from data_mask_studio.gui.html_restoration_worker import (
    HTMLAnalysisWorker,
    HTMLRestorationWorker,
)
from data_mask_studio.gui.components import EmptyStatePlainTextEdit
from data_mask_studio.gui.components.presentation import (
    FeedbackState, configure_result_area, confirm_destructive_action, set_button_role, set_feedback_state,
)
from data_mask_studio.gui.visual_tokens import COLORS, METRICS
from data_mask_studio.gui.dialog_localization import localize_standard_buttons
from data_mask_studio.i18n import Translator
from data_mask_studio.html_restoration import (
    HTMLAnalysisResult,
    HTMLInspectionResult,
    HTMLMissingCodePolicy,
    HTMLMissingCodeError,
    HTMLRestorationError,
    HTMLRestorationProgress,
    HTMLRestorationResult,
    HTMLRestorationSecurityError,
    HTMLRestorationService,
    HTMLUnsupportedContextError,
    inspect_html,
    suggested_html_output_path,
)
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.vault import VaultRepository


class HTMLRestorationWidget(QWidget):
    file_loaded = Signal()
    busy_changed = Signal(bool)

    def __init__(
        self,
        repository_factory: Callable[[], VaultRepository],
        parent: QWidget | None = None,
        *,
        file_selector: Callable[[], None] | None = None,
        translator: Translator | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator or Translator("pt-BR")
        self._unified_selector = file_selector is not None
        self._status_message = None
        self._progress_message = None
        self._displayed_result: HTMLAnalysisResult | HTMLRestorationResult | None = None
        self._confirmation_dialog: QMessageBox | None = None
        self._service = HTMLRestorationService(repository_factory)
        self._inspection: HTMLInspectionResult | None = None
        self._worker: HTMLAnalysisWorker | HTMLRestorationWorker | None = None
        self._last_output_path: Path | None = None
        self._last_error: Exception | None = None

        self.select_button = QPushButton("Selecionar arquivo mascarado" if file_selector else "Selecionar HTML mascarado")
        self.select_button.clicked.connect(file_selector or self._select_html)
        self.path_field = QLineEdit()
        self.path_field.setReadOnly(True)
        self.path_field.setPlaceholderText("Nenhum arquivo selecionado")
        self.file_name_label = QLabel("—")
        self.encoding_label = QLabel("—")

        select_layout = QHBoxLayout()
        select_layout.addWidget(self.select_button)
        select_layout.addStretch()
        details = QFormLayout()
        details.addRow("Arquivo:", self.file_name_label)
        details.addRow("Caminho:", self.path_field)
        details.addRow("Codificação:", self.encoding_label)
        self._detail_labels = [
            (details.labelForField(field), message_id) for field, message_id in (
                (self.file_name_label, "restoration.csv.metadata.file"),
                (self.path_field, "restoration.csv.metadata.path"),
                (self.encoding_label, "restoration.csv.metadata.encoding"),
            )
        ]

        self.missing_policy_combo = ScrollSafeComboBox()
        self.missing_policy_combo.setAccessibleName("Códigos não encontrados")
        self.missing_policy_combo.addItem(
            "Manter código original", HTMLMissingCodePolicy.KEEP.value
        )
        self.missing_policy_combo.addItem(
            "Interromper restauração", HTMLMissingCodePolicy.ABORT.value
        )
        self.representation_combo = ScrollSafeComboBox()
        self.representation_combo.setAccessibleName("Valor restaurado")
        self.representation_combo.addItem(
            "Primeira representação original",
            RepresentationPolicy.FIRST_ORIGINAL.value,
        )
        self.representation_combo.addItem(
            "Valor canônico normalizado", RepresentationPolicy.CANONICAL.value
        )
        self.options_controls = QWidget()
        policies = QFormLayout(self.options_controls)
        policies.setContentsMargins(0, 0, 0, 0)
        policies.addRow("Códigos não encontrados:", self.missing_policy_combo)
        policies.addRow("Valor restaurado:", self.representation_combo)
        self._policy_labels = [
            (policies.labelForField(self.missing_policy_combo), "restoration.csv.policy.label"),
            (policies.labelForField(self.representation_combo), "restoration.csv.representation.label"),
        ]
        self.options_toggle = QPushButton("Opções de restauração")
        self.options_toggle.setCheckable(True)
        self.options_toggle.setAccessibleName("Mostrar ou ocultar opções de restauração")
        self.options_toggle.toggled.connect(self._update_options_disclosure)
        self.options_summary = QLabel()
        self.options_summary.setWordWrap(True)
        self.options_summary.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.options_summary.setStyleSheet(
            f"color: {COLORS['muted_text']}; font-size: {METRICS['description_font_size']}px;"
        )
        self.missing_policy_combo.currentIndexChanged.connect(self._update_options_summary)
        self.representation_combo.currentIndexChanged.connect(self._update_options_summary)
        set_button_role(self.options_toggle, "secondary")

        self.analyze_button = QPushButton("Analisar códigos")
        self.analyze_button.clicked.connect(self.start_analysis)
        self.generate_button = QPushButton("Gerar HTML restaurado")
        self.generate_button.clicked.connect(self._choose_output)
        set_button_role(self.analyze_button, "secondary")
        set_button_role(self.generate_button, "primary")
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.clicked.connect(self.cancel_processing)
        self.cancel_button.setVisible(False)
        actions = QHBoxLayout()
        actions.addWidget(self.analyze_button)
        actions.addWidget(self.generate_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel()
        self.progress_label.setWordWrap(True)
        self.progress_label.setVisible(False)
        progress = QHBoxLayout()
        progress.addWidget(self.progress_bar, stretch=1)
        progress.addWidget(self.progress_label)

        self.summary = EmptyStatePlainTextEdit(
            "O resumo da análise aparecerá aqui."
        )
        self.summary.setReadOnly(True)
        self.summary.setMinimumHeight(self.summary.minimumSizeHint().height())
        configure_result_area(self.summary, self.summary.minimumHeight())
        self.open_folder_button = QPushButton("Abrir pasta do resultado")
        self.open_folder_button.clicked.connect(self.open_output_folder)
        self.open_folder_button.setVisible(False)
        self.status_label = QLabel("Selecione um arquivo HTML para começar.")
        self.status_label.setWordWrap(True)
        for label in (self.file_name_label, self.progress_label, self.status_label, self.options_summary):
            label.setTextFormat(Qt.TextFormat.PlainText)
        set_feedback_state(self.status_label, "neutral")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(METRICS["panel_spacing"])
        layout.addLayout(select_layout)
        layout.addLayout(details)
        layout.addWidget(self.options_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.options_summary)
        layout.addWidget(self.options_controls)
        layout.addLayout(actions)
        layout.addLayout(progress)
        layout.addWidget(self.summary)
        layout.addWidget(self.open_folder_button)
        layout.addWidget(self.status_label)
        layout.addStretch()
        self.summary.textChanged.connect(self._update_information_layout)
        self._update_information_layout()
        option_tab_order = (
            self.path_field, self.options_toggle, self.missing_policy_combo,
            self.representation_combo, self.analyze_button, self.generate_button,
        )
        for previous, following in zip(option_tab_order, option_tab_order[1:]):
            QWidget.setTabOrder(previous, following)
        self._update_options_summary()
        self._update_options_disclosure()
        self._set_file_controls_enabled(False)
        self._set_status_id("restoration.html.status.initial")
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        """Render presentation only; inspection, policies and workers stay owned."""
        self._translator = translator
        for widget, message_id in (
            (self.select_button, "restoration.file.select" if self._unified_selector else "restoration.html.select"),
            (self.analyze_button, "restoration.csv.analyze"),
            (self.generate_button, "restoration.html.generate"),
            (self.cancel_button, "dialog.button.cancel"),
            (self.open_folder_button, "restoration.csv.open_folder"),
            *self._detail_labels, *self._policy_labels,
        ):
            widget.setText(translator.text(message_id))
        for combo, identifiers in (
            (self.missing_policy_combo, {"keep": "restoration.csv.policy.keep", "abort": "restoration.csv.policy.abort"}),
            (self.representation_combo, {"first_original": "restoration.csv.representation.first",
                                         "canonical": "restoration.csv.representation.canonical"}),
        ):
            with QSignalBlocker(combo):
                for index in range(combo.count()):
                    combo.setItemText(index, translator.text(identifiers[combo.itemData(index)]))
        for widget, message_id in (
            (self.path_field, "restoration.html.path_accessible"),
            (self.missing_policy_combo, "restoration.csv.policy.accessible"),
            (self.representation_combo, "restoration.csv.representation.accessible"),
            (self.options_toggle, "restoration.csv.options.accessible"),
        ):
            widget.setAccessibleName(translator.text(message_id))
        self.path_field.setPlaceholderText(translator.text("restoration.csv.no_file"))
        self.summary.empty_text = translator.text("restoration.csv.summary.empty")
        self.summary.setAccessibleDescription(self.summary.empty_text)
        self.summary.viewport().update()
        self._render_options_title()
        self._update_options_summary()
        if self._status_message is not None:
            message_id, parameters = self._status_message
            self.status_label.setText(translator.text(message_id, **parameters))
        self._render_progress()
        if self._displayed_result is not None:
            cursor = self.summary.textCursor()
            anchor, position = cursor.anchor(), cursor.position()
            scroll = self.summary.verticalScrollBar().value(), self.summary.horizontalScrollBar().value()
            self._render_result()
            end = self.summary.document().characterCount() - 1
            cursor.setPosition(min(anchor, end))
            cursor.setPosition(min(position, end), QTextCursor.MoveMode.KeepAnchor)
            self.summary.setTextCursor(cursor)
            self.summary.verticalScrollBar().setValue(scroll[0])
            self.summary.horizontalScrollBar().setValue(scroll[1])
        if self._confirmation_dialog is not None:
            self._render_sensitive_confirmation()

    def _update_information_layout(self) -> None:
        has_summary = bool(self.summary.toPlainText().strip())
        policy = self.summary.sizePolicy()
        policy.setVerticalPolicy(
            QSizePolicy.Policy.Ignored if has_summary else QSizePolicy.Policy.Preferred
        )
        self.summary.setSizePolicy(policy)
        layout = self.layout()
        layout.setStretch(layout.indexOf(self.summary), int(has_summary))
        layout.setStretch(layout.count() - 1, int(not has_summary))

    def _update_options_disclosure(self, *_args: object) -> None:
        focused = QApplication.focusWidget()
        options_have_focus = focused is not None and self.options_controls.isAncestorOf(focused)
        expanded = self.options_toggle.isChecked()
        self.options_controls.setVisible(expanded)
        self.options_summary.setVisible(not expanded)
        self._render_options_title()
        if options_have_focus and not expanded and self.isVisible():
            self.options_toggle.setFocus(Qt.FocusReason.OtherFocusReason)

    def _render_options_title(self) -> None:
        self.options_toggle.setText(self._translator.text(
            "restoration.csv.options.hide" if self.options_toggle.isChecked() else "restoration.csv.options.show"))

    def _update_options_summary(self, *_args: object) -> None:
        self.options_summary.setText(self._translator.text("restoration.csv.options.summary",
            missing=self.missing_policy_combo.currentText(), representation=self.representation_combo.currentText()))

    def _select_html(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._translator.text("restoration.html.select"),
            "",
            self._translator.text("restoration.html.filter"),
        )
        if path:
            self.load_html(path)

    def load_html(self, path: str) -> None:
        if self._worker is not None:
            return
        try:
            inspection = inspect_html(path)
        except HTMLRestorationError as error:
            self._set_status(str(error), is_error=True)
            return
        self._inspection = inspection
        self._reset_operation_feedback()
        self.file_name_label.setText(inspection.path.name)
        self.path_field.setText(str(inspection.path))
        self.encoding_label.setText(inspection.encoding)
        self._set_file_controls_enabled(True)
        self._set_status_id("restoration.html.status.inspected", state="success")
        self.file_loaded.emit()

    def clear_selection(self) -> None:
        if self._worker is not None:
            return
        self._inspection = None
        self._reset_operation_feedback()
        self.path_field.clear()
        self.file_name_label.setText("—")
        self.encoding_label.setText("—")
        self._set_file_controls_enabled(False)
        self._set_status_id("restoration.html.status.initial")

    def _reset_operation_feedback(self) -> None:
        self._reset_progress()
        self._last_output_path = None
        self._last_error = None
        self._displayed_result = None
        self.summary.clear()
        self.open_folder_button.setVisible(False)

    def start_analysis(self) -> None:
        if self._inspection is None or self._worker is not None:
            return
        worker = HTMLAnalysisWorker(self._service, self._inspection)
        self._start_worker(worker, "restoration.csv.status.analyzing", self._analysis_completed)

    def _analysis_completed(self, result: HTMLAnalysisResult) -> None:
        self._displayed_result = result
        self._render_result()
        self._set_status_id(
            "restoration.html.status.analyzed",
            state="warning" if result.missing_codes or result.invalid_similar_codes else "success",
        )

    def _choose_output(self) -> None:
        if self._inspection is None:
            return
        answer = self._confirm_sensitive_data()
        if answer != QMessageBox.StandardButton.Ok:
            return
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            self._translator.text("restoration.html.save_title"),
            str(suggested_html_output_path(self._inspection.path)),
            self._translator.text("restoration.html.save_filter"),
        )
        if not selected_path:
            return
        destination = Path(selected_path)
        if destination.suffix.lower() not in {".html", ".htm"}:
            destination = destination.with_suffix(".html")
        if paths_refer_to_same_file(self._inspection.path, destination):
            self._set_status_id("restoration.html.error.source_alias", is_error=True)
            return
        overwrite = destination.exists()
        if overwrite:
            answer = confirm_destructive_action(
                self,
                self._translator.text("masking.file.overwrite_title"),
                self._translator.text("masking.file.overwrite_text"),
                translator=self._translator,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.start_restoration(destination, overwrite=overwrite)

    def _confirm_sensitive_data(self) -> int:
        dialog = QMessageBox(QMessageBox.Icon.Warning, "", "",
                             QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel, self)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        dialog.setEscapeButton(QMessageBox.StandardButton.Cancel)
        self._confirmation_dialog = dialog
        self._render_sensitive_confirmation()
        try:
            return dialog.exec()
        finally:
            self._confirmation_dialog = None
            dialog.deleteLater()

    def _render_sensitive_confirmation(self) -> None:
        dialog = self._confirmation_dialog
        dialog.setWindowTitle(self._translator.text("restoration.csv.sensitive_title"))
        dialog.setText(self._translator.text("restoration.html.sensitive_text"))
        localize_standard_buttons(dialog, self._translator)

    def start_restoration(self, destination: Path, *, overwrite: bool) -> None:
        if self._inspection is None or self._worker is not None:
            return
        worker = HTMLRestorationWorker(
            self._service,
            self._inspection,
            str(destination),
            HTMLMissingCodePolicy(self.missing_policy_combo.currentData()),
            RepresentationPolicy(self.representation_combo.currentData()),
            overwrite=overwrite,
        )
        self._start_worker(worker, "restoration.html.status.restoring", self._restoration_completed)

    def _start_worker(
        self,
        worker: HTMLAnalysisWorker | HTMLRestorationWorker,
        message_id: str,
        completed: Callable[..., None],
    ) -> None:
        self._reset_operation_feedback()
        self._worker = worker
        self._set_processing_state(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self._progress_message = ("restoration.html.progress.initial", {"count": 0})
        self._render_progress()
        self.progress_label.setVisible(True)
        self._set_status_id(message_id)
        def deliver(callback: Callable[..., None], *args: object) -> None:
            if self._worker is worker:
                callback(*args)

        for signal, callback in (
            (worker.progress, self._progress_changed),
            (worker.completed, completed),
            (worker.cancelled, self._cancelled),
            (worker.failed, self._failed),
            (worker.finished, self._worker_finished),
        ):
            signal.connect(partial(deliver, callback))
        worker.start()

    def _progress_changed(self, progress: HTMLRestorationProgress) -> None:
        percentage = (
            min(100, int(progress.processed_bytes * 100 / progress.total_bytes))
            if progress.total_bytes
            else 0
        )
        self.progress_bar.setValue(percentage)
        self._progress_message = ("restoration.html.progress.metrics", dict(
            processed=progress.occurrences_processed, restored=progress.restored_occurrences,
            missing=progress.missing_occurrences))
        self._render_progress()

    def _render_progress(self) -> None:
        if self._progress_message is None:
            return
        message_id, parameters = self._progress_message
        self.progress_label.setText(
            self._translator.plural(message_id, parameters["count"])
            if message_id == "restoration.html.progress.initial" else
            self._translator.text(message_id, **parameters))

    def _restoration_completed(self, result: HTMLRestorationResult) -> None:
        self._last_output_path = result.output_path
        self._displayed_result = result
        self._render_result()
        self.open_folder_button.setVisible(True)
        self._set_status_id(
            "restoration.html.status.completed",
            state="warning" if result.missing_occurrences else "success",
        )

    def _render_result(self) -> None:
        result = self._displayed_result
        if isinstance(result, HTMLAnalysisResult):
            self.summary.setPlainText(self._translator.text("restoration.html.analysis.summary",
                unique=result.unique_codes, total=result.total_occurrences, found=result.found_codes,
                missing=result.missing_codes, invalid=result.invalid_similar_codes,
                prefixes=", ".join(result.prefixes) or self._translator.text("restoration.csv.analysis.no_prefixes")))
        elif isinstance(result, HTMLRestorationResult):
            self.summary.setPlainText(self._translator.text("restoration.html.result.summary",
                path=str(result.output_path), encoding=result.encoding, processed=result.total_occurrences,
                restored=result.restored_occurrences, missing=result.missing_occurrences,
                seconds=f"{result.duration_seconds:.2f}",
                missing_policy=self._translator.text("restoration.csv.policy." + result.missing_code_policy.value),
                representation=self._translator.text("restoration.csv.representation.first"
                    if result.representation_policy is RepresentationPolicy.FIRST_ORIGINAL else
                    "restoration.csv.representation.canonical_summary")))

    def _cancelled(self) -> None:
        self._reset_progress()
        self._set_status_id("restoration.csv.status.cancelled", state="warning")

    def _failed(self, error: Exception) -> None:
        self._reset_progress()
        self._last_error = error
        if isinstance(error, HTMLRestorationSecurityError):
            self._set_status_id("restoration.csv.error.security", is_error=True)
        elif isinstance(error, HTMLMissingCodeError):
            self._set_status_id("restoration.html.error.missing", is_error=True)
        elif isinstance(error, HTMLUnsupportedContextError):
            self._set_status_id("restoration.html.error.context", is_error=True)
        elif isinstance(error, HTMLRestorationError):
            self._set_status(str(error), True)
        else:
            self._set_status_id("restoration.csv.error.unexpected", is_error=True)

    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        self._set_processing_state(False)
        if worker is not None:
            worker.deleteLater()

    def cancel_processing(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self._set_status_id("restoration.csv.status.cancelling")

    def has_running_worker(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def stop_worker(self) -> bool:
        if not self.has_running_worker():
            return True
        assert self._worker is not None
        self._worker.request_cancel()
        return self._worker.wait(5000)

    def _set_processing_state(self, processing: bool) -> None:
        self.options_toggle.setEnabled(not processing)
        self.select_button.setEnabled(not processing)
        has_file = self._inspection is not None
        for widget in (
            self.analyze_button,
            self.generate_button,
            self.missing_policy_combo,
            self.representation_combo,
        ):
            widget.setEnabled(not processing and has_file)
        self.cancel_button.setVisible(processing)
        self.cancel_button.setEnabled(processing)
        self.busy_changed.emit(processing)

    def _set_file_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.analyze_button,
            self.generate_button,
            self.missing_policy_combo,
            self.representation_combo,
        ):
            widget.setEnabled(enabled)

    def _reset_progress(self) -> None:
        self._progress_message = None
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.progress_label.clear()
        self.progress_label.setVisible(False)

    def open_output_folder(self) -> None:
        if self._last_output_path is None:
            return
        opened = QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self._last_output_path.parent))
        )
        if not opened:
            self._set_status_id("masking.error.open_folder", is_error=True)

    def _set_status_id(self, message_id: str, *, is_error: bool = False,
                       state: FeedbackState = "neutral", **parameters: str | int | float) -> None:
        self._set_status(self._translator.text(message_id, **parameters), is_error, state=state)
        self._status_message = (message_id, parameters)

    def _set_status(
        self, message: str, is_error: bool, *, state: FeedbackState = "neutral"
    ) -> None:
        self._status_message = None
        self.status_label.setText(message)
        set_feedback_state(self.status_label, "error" if is_error else state)

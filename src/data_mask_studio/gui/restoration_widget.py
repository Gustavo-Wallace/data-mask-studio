from collections.abc import Callable
from functools import partial
from pathlib import Path
import time

from PySide6.QtCore import QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
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
from data_mask_studio.performance import calculate_metrics
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox
from data_mask_studio.gui.components.presentation import (
    FeedbackState, TruncatedTextToolTipDelegate, configure_result_area, confirm_destructive_action,
    set_button_role, set_feedback_state,
)
from data_mask_studio.gui.visual_tokens import COLORS, METRICS

from data_mask_studio.csv_tools import (
    CSVInspectionError,
    CSVInspectionResult,
    format_header_replacement_warning,
    inspect_csv,
)
from data_mask_studio.csv_tools.csv_anonymizer import paths_refer_to_same_file
from data_mask_studio.gui.restoration_worker import (
    CSVRestorationWorker,
    RestorationAnalysisWorker,
)
from data_mask_studio.gui.components import EmptyStatePlainTextEdit, EmptyStateTable
from data_mask_studio.gui.package_restoration_controls import PackageRestorationControls, RestorationInputError
from data_mask_studio.gui.dialog_localization import localize_standard_buttons
from data_mask_studio.i18n import Translator
from data_mask_studio.restoration import (
    AnalysisResult,
    MissingCodePolicy,
    RepresentationPolicy,
    RestorationConfiguration,
    RestorationError,
    RestorationProgress,
    RestorationResult,
    RestorationSecurityError,
    RestorationService,
    SelectedColumn,
    suggested_output_path,
)
from data_mask_studio.vault import VaultRepository

_MISSING_POLICY_IDS = {
    MissingCodePolicy.KEEP: "restoration.csv.policy.keep",
    MissingCodePolicy.EMPTY: "restoration.csv.policy.empty",
    MissingCodePolicy.ABORT: "restoration.csv.policy.abort",
}
_REPRESENTATION_IDS = {
    RepresentationPolicy.FIRST_ORIGINAL: "restoration.csv.representation.first",
    RepresentationPolicy.CANONICAL: "restoration.csv.representation.canonical",
}


class RestorationWidget(QWidget):
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
        self._displayed_result: AnalysisResult | RestorationResult | None = None
        self._confirmation_dialog: QMessageBox | None = None
        self._service = RestorationService(repository_factory)
        self._inspection: CSVInspectionResult | None = None
        self._checkboxes: list[QCheckBox] = []
        self._worker: RestorationAnalysisWorker | CSVRestorationWorker | None = None
        self._last_output_path: Path | None = None
        self._last_error: Exception | None = None

        self.source_combo = ScrollSafeComboBox()
        self.source_combo.setAccessibleName("Origem da restauração")
        self.source_combo.addItem("Cofre local", "vault")
        self.source_combo.addItem("Pacote de transferência", "package")
        self.package_controls = PackageRestorationControls(self, translator=self._translator)
        self.package_controls.setVisible(False)
        self.package_controls.setEnabled(False)
        source_layout = QFormLayout()
        source_layout.addRow("Origem da restauração:", self.source_combo)
        self._source_label = source_layout.labelForField(self.source_combo)

        self.select_button = QPushButton("Selecionar arquivo mascarado" if file_selector else "Selecionar CSV mascarado")
        self.select_button.clicked.connect(file_selector or self._select_csv)
        self.path_field = QLineEdit()
        self.path_field.setReadOnly(True)
        self.path_field.setPlaceholderText("Nenhum arquivo selecionado")
        self.file_name_label = QLabel("—")
        self.encoding_label = QLabel("—")
        self.delimiter_label = QLabel("—")

        file_buttons = QHBoxLayout()
        file_buttons.addWidget(self.select_button)
        file_buttons.addStretch()
        details = QFormLayout()
        details.addRow("Arquivo:", self.file_name_label)
        details.addRow("Caminho:", self.path_field)
        details.addRow("Codificação:", self.encoding_label)
        details.addRow("Separador:", self.delimiter_label)
        self._detail_labels = [
            (details.labelForField(field), message_id) for field, message_id in (
                (self.file_name_label, "restoration.csv.metadata.file"),
                (self.path_field, "restoration.csv.metadata.path"),
                (self.encoding_label, "restoration.csv.metadata.encoding"),
                (self.delimiter_label, "restoration.csv.metadata.separator"),
            )
        ]

        self.table = EmptyStateTable(
            0, 2, "Selecione um CSV mascarado para revisar as colunas."
        )
        self.table.setHorizontalHeaderLabels(["Restaurar", "Cabeçalho"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setItemDelegateForColumn(1, TruncatedTextToolTipDelegate(self.table))
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )

        self.select_all_button = QPushButton("Selecionar todas")
        self.select_all_button.clicked.connect(self.select_all_columns)
        self.unselect_all_button = QPushButton("Desmarcar todas")
        self.unselect_all_button.clicked.connect(self.unselect_all_columns)
        self.selected_count_label = QLabel("0 colunas selecionadas")
        selection = QHBoxLayout()
        selection.addWidget(self.select_all_button)
        selection.addWidget(self.unselect_all_button)
        selection.addStretch()
        selection.addWidget(self.selected_count_label)

        self.missing_policy_combo = ScrollSafeComboBox()
        self.missing_policy_combo.setAccessibleName("Códigos não encontrados")
        self.missing_policy_combo.addItem(
            "Manter código original", MissingCodePolicy.KEEP.value
        )
        self.missing_policy_combo.addItem(
            "Deixar célula vazia", MissingCodePolicy.EMPTY.value
        )
        self.missing_policy_combo.addItem(
            "Interromper restauração", MissingCodePolicy.ABORT.value
        )
        self.representation_combo = ScrollSafeComboBox()
        self.representation_combo.setAccessibleName("Valor restaurado")
        self.representation_combo.addItem(
            "Primeira representação original",
            RepresentationPolicy.FIRST_ORIGINAL.value,
        )
        self.representation_combo.addItem(
            "Preferir valor canônico", RepresentationPolicy.CANONICAL.value
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
        self.generate_button = QPushButton("Gerar CSV restaurado")
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
            "O resumo da análise ou restauração aparecerá aqui."
        )
        self.summary.setReadOnly(True)
        self.summary.setMinimumHeight(self.summary.minimumSizeHint().height())
        configure_result_area(self.summary, self.summary.minimumHeight())
        self.open_folder_button = QPushButton("Abrir pasta do arquivo")
        self.open_folder_button.clicked.connect(self.open_output_folder)
        self.open_folder_button.setVisible(False)
        self.status_label = QLabel("Selecione um CSV mascarado para começar.")
        self.status_label.setWordWrap(True)
        for label in (self.file_name_label, self.progress_label, self.status_label, self.options_summary):
            label.setTextFormat(Qt.TextFormat.PlainText)
        set_feedback_state(self.status_label, "neutral")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(8)
        layout.addLayout(source_layout)
        layout.addWidget(self.package_controls)
        layout.addLayout(file_buttons)
        layout.addLayout(details)
        layout.addLayout(selection)
        layout.addWidget(self.table, stretch=1)
        layout.addWidget(self.options_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.options_summary)
        layout.addWidget(self.options_controls)
        layout.addLayout(actions)
        layout.addLayout(progress)
        layout.addWidget(self.summary)
        layout.addWidget(self.open_folder_button)
        layout.addWidget(self.status_label)
        layout.addStretch()
        self.table.model().rowsInserted.connect(self._update_information_layout)
        self.table.model().rowsRemoved.connect(self._update_information_layout)
        self.summary.textChanged.connect(self._update_information_layout)
        self._update_information_layout()
        option_tab_order = (
            self.table, self.options_toggle, self.missing_policy_combo,
            self.representation_combo, self.analyze_button, self.generate_button,
        )
        for previous, following in zip(option_tab_order, option_tab_order[1:]):
            QWidget.setTabOrder(previous, following)
        self._update_options_summary()
        self._update_options_disclosure()
        self._update_enabled_state(False)
        self.source_combo.currentIndexChanged.connect(self._source_changed)
        self._set_status_id("restoration.csv.status.initial")
        self.retranslate(self._translator)

    def retranslate(self, translator: Translator) -> None:
        """Relabel existing controls and render stored results; never restart or reset."""
        self._translator = translator
        for widget, message_id in (
            (self.select_button, "restoration.file.select" if self._unified_selector else "restoration.csv.select"),
            (self._source_label, "restoration.csv.source.label"),
            (self.select_all_button, "restoration.csv.select_all"),
            (self.unselect_all_button, "restoration.csv.deselect_all"),
            (self.analyze_button, "restoration.csv.analyze"),
            (self.generate_button, "restoration.csv.generate"),
            (self.cancel_button, "dialog.button.cancel"),
            (self.open_folder_button, "restoration.csv.open_folder"),
            *self._detail_labels, *self._policy_labels,
        ):
            widget.setText(translator.text(message_id))
        for combo, identifiers in (
            (self.source_combo, {"vault": "restoration.csv.source.vault", "package": "restoration.csv.source.package"}),
            (self.missing_policy_combo, _MISSING_POLICY_IDS),
            (self.representation_combo, _REPRESENTATION_IDS),
        ):
            with QSignalBlocker(combo):
                for index in range(combo.count()):
                    combo.setItemText(index, translator.text(identifiers[combo.itemData(index)]))
        for widget, message_id in (
            (self.source_combo, "restoration.csv.source.accessible"),
            (self.path_field, "restoration.csv.path_accessible"),
            (self.missing_policy_combo, "restoration.csv.policy.accessible"),
            (self.representation_combo, "restoration.csv.representation.accessible"),
            (self.options_toggle, "restoration.csv.options.accessible"),
        ):
            widget.setAccessibleName(translator.text(message_id))
        self.path_field.setPlaceholderText(translator.text("restoration.csv.no_file"))
        for view, message_id in ((self.table, "restoration.csv.table.empty"),
                                 (self.summary, "restoration.csv.summary.empty")):
            view.empty_text = translator.text(message_id)
            view.setAccessibleDescription(view.empty_text)
            view.viewport().update()
        # Preserve cell identity, checkboxes, selections and scroll positions.
        for index, message_id in enumerate(("restoration.csv.table.restore", "restoration.csv.table.header")):
            self.table.horizontalHeaderItem(index).setText(translator.text(message_id))
        if self._inspection is not None:
            self.delimiter_label.setText(self._separator_label(self._inspection.delimiter))
            for index, checkbox in enumerate(self._checkboxes):
                checkbox.setAccessibleName(translator.text("restoration.csv.column_accessible",
                    header=self._inspection.headers[index], number=index + 1))
        self.package_controls.retranslate(translator)
        self._update_selected_count()
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

    def _separator_label(self, delimiter: str) -> str:
        return self._translator.text({",": "masking.separator.comma", ";": "masking.separator.semicolon",
                                     "\t": "masking.separator.tab", "|": "masking.separator.pipe"}[delimiter])

    def _update_information_layout(self) -> None:
        has_columns = self.table.rowCount() > 0
        has_summary = bool(self.summary.toPlainText().strip())
        minimum = (
            self.table.horizontalHeader().sizeHint().height()
            + 2 * self.table.verticalHeader().defaultSectionSize()
            + 2 * self.table.frameWidth()
        )
        self.table.setMinimumHeight(minimum)
        self.table.setMaximumHeight(16_777_215 if has_columns else minimum)
        # Explicit minima keep each view readable without imposing its preferred
        # height on the enclosing scroll area. Populated views share spare space.
        for view, populated in ((self.table, has_columns), (self.summary, has_summary)):
            policy = view.sizePolicy()
            policy.setVerticalPolicy(
                QSizePolicy.Policy.Ignored if populated else QSizePolicy.Policy.Preferred
            )
            view.setSizePolicy(policy)
        layout = self.layout()
        layout.setStretch(layout.indexOf(self.table), int(has_columns))
        layout.setStretch(layout.indexOf(self.summary), int(has_summary))
        layout.setStretch(layout.count() - 1, int(not (has_columns or has_summary)))

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
        self.options_summary.setText(self._translator.text(
            "restoration.csv.options.package_summary" if self._package_mode() else "restoration.csv.options.summary",
            missing=self.missing_policy_combo.currentText(), representation=self.representation_combo.currentText()))

    def _package_mode(self) -> bool:
        return self.source_combo.currentData() == "package"

    def _source_changed(self) -> None:
        if self._worker is not None:
            with QSignalBlocker(self.source_combo):
                self.source_combo.setCurrentIndex(self._worker_source_index)
            return
        self.package_controls.clear_password()
        if self._package_mode():
            self._local_missing_policy_index = self.missing_policy_combo.currentIndex()
            self.missing_policy_combo.setCurrentIndex(
                self.missing_policy_combo.findData(MissingCodePolicy.ABORT.value)
            )
        else:
            self.missing_policy_combo.setCurrentIndex(getattr(self, "_local_missing_policy_index", 0))
        self.package_controls.setVisible(self._package_mode())
        self.package_controls.setEnabled(self._package_mode())
        self._reset_operation_feedback()
        self._update_enabled_state(self._inspection is not None)
        self._update_options_summary()
        self._set_status_id("restoration.csv.status.package" if self._package_mode() else "restoration.csv.status.vault")

    def _select_csv(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, self._translator.text("restoration.csv.select"), "", self._translator.text("masking.file.filter")
        )
        if file_path:
            self.load_csv(file_path)

    def load_csv(self, file_path: str) -> None:
        if self._worker is not None:
            return
        self.package_controls.clear_password()
        try:
            inspection = inspect_csv(file_path)
        except CSVInspectionError as error:
            self._set_status(str(error), is_error=True)
            return
        self._inspection = inspection
        self._reset_operation_feedback()
        self.file_name_label.setText(inspection.path.name)
        self.path_field.setText(str(inspection.path))
        self.encoding_label.setText(inspection.encoding)
        self.delimiter_label.setText(self._separator_label(inspection.delimiter))
        self.table.setRowCount(0)
        self._checkboxes.clear()
        for row, header in enumerate(inspection.headers):
            self.table.insertRow(row)
            checkbox = QCheckBox()
            checkbox.setAccessibleName(self._translator.text("restoration.csv.column_accessible", header=header, number=row + 1))
            checkbox.stateChanged.connect(self._selection_changed)
            container = QWidget()
            checkbox_layout = QHBoxLayout(container)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            checkbox_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            checkbox_layout.addWidget(checkbox)
            self.table.setCellWidget(row, 0, container)
            item = QTableWidgetItem(header)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, item)
            self._checkboxes.append(checkbox)
        self._update_selected_count()
        self._update_enabled_state(True)
        warning = format_header_replacement_warning(inspection.header_replacements)
        if warning:
            # This service warning has no typed localization contract yet.
            self._set_status_id("restoration.csv.status.inspected_warning", warning=warning, state="warning")
        else:
            self._set_status_id("restoration.csv.status.inspected", state="success")
        self.file_loaded.emit()

    def clear_selection(self) -> None:
        if self._worker is not None:
            return
        self.package_controls.clear_password()
        self._inspection = None
        self._reset_operation_feedback()
        self.path_field.clear()
        for label in (self.file_name_label, self.encoding_label, self.delimiter_label):
            label.setText("—")
        self.table.setRowCount(0)
        self._checkboxes.clear()
        self._update_selected_count()
        self._update_enabled_state(False)
        self._set_status_id("restoration.csv.status.initial")

    def _reset_operation_feedback(self) -> None:
        """Invalidate feedback, not the selected file, columns or source inputs."""
        self._operation_succeeded = False
        self._last_output_path = None
        self._last_error = None
        self._displayed_result = None
        self._progress_message = None
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.progress_label.clear()
        self.progress_label.setVisible(False)
        self.summary.clear()
        self.open_folder_button.setVisible(False)
        self._set_status("", is_error=False)

    def select_all_columns(self) -> None:
        for checkbox in self._checkboxes:
            checkbox.setChecked(True)

    def unselect_all_columns(self) -> None:
        for checkbox in self._checkboxes:
            checkbox.setChecked(False)

    def _selection_changed(self, _state: int) -> None:
        self._update_selected_count()

    def _update_selected_count(self) -> None:
        count = sum(checkbox.isChecked() for checkbox in self._checkboxes)
        self.selected_count_label.setText(self._translator.plural(
            "restoration.csv.selected", count, total=len(self._checkboxes)))

    def _configuration(self) -> RestorationConfiguration:
        if self._inspection is None:
            raise RestorationInputError("restoration.csv.error.no_csv", self._translator)
        columns = tuple(
            SelectedColumn(index, self._inspection.headers[index])
            for index, checkbox in enumerate(self._checkboxes)
            if checkbox.isChecked()
        )
        if not columns:
            raise RestorationInputError("restoration.csv.error.no_columns", self._translator)
        return RestorationConfiguration(
            source_path=self._inspection.path,
            encoding=self._inspection.encoding,
            delimiter=self._inspection.delimiter,
            headers=tuple(self._inspection.headers),
            selected_columns=columns,
            missing_code_policy=MissingCodePolicy.ABORT if self._package_mode() else MissingCodePolicy(
                self.missing_policy_combo.currentData()
            ),
            representation_policy=RepresentationPolicy(
                self.representation_combo.currentData()
            ),
        )

    def start_analysis(self) -> None:
        if self._worker is not None or self._package_mode():
            return
        try:
            configuration = self._configuration()
        except RestorationError as error:
            self._show_input_error(error)
            return
        worker = RestorationAnalysisWorker(self._service, configuration)
        self._start_worker(worker, "restoration.csv.status.analyzing", self._analysis_completed)

    def _analysis_completed(self, result: AnalysisResult) -> None:
        self._operation_succeeded = True
        self._displayed_result = result
        self._render_result()
        self._set_status_id(
            "restoration.csv.status.analyzed",
            state="warning" if result.missing_codes or result.invalid_formats
            or result.possible_incompatibilities else "success",
        )

    def _choose_output(self) -> None:
        try:
            configuration = self._configuration()
        except RestorationError as error:
            self._show_input_error(error)
            return
        confirmation = self._confirm_sensitive_data()
        if confirmation != QMessageBox.StandardButton.Ok:
            return
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            self._translator.text("restoration.csv.save_title"),
            str(suggested_output_path(configuration.source_path)),
            self._translator.text("masking.file.filter"),
        )
        if not selected_path:
            return
        destination = Path(selected_path)
        if destination.suffix.lower() != ".csv":
            destination = destination.with_suffix(".csv")
        if paths_refer_to_same_file(configuration.source_path, destination):
            self._set_status_id("restoration.csv.error.source_alias", is_error=True)
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
        dialog.setText(self._translator.text("restoration.csv.sensitive_text"))
        localize_standard_buttons(dialog, self._translator)

    def start_restoration(self, destination: Path, *, overwrite: bool) -> None:
        if self._worker is not None:
            return
        try:
            configuration = self._configuration()
            options = {}
            if self._package_mode():
                options["package_request"] = self.package_controls.request()
            destination = Path(destination).expanduser().absolute()
            if (destination.suffix.lower() != ".csv" or not destination.parent.is_dir()
                    or destination.is_dir()
                    or paths_refer_to_same_file(configuration.source_path, destination)
                    or (destination.exists() and not overwrite)):
                raise RestorationInputError("restoration.csv.error.destination", self._translator)
        except RestorationError as error:
            self._show_input_error(error)
            return
        worker = CSVRestorationWorker(
            self._service,
            configuration,
            str(destination),
            overwrite=overwrite,
            **options,
        )
        self.package_controls.clear_password()
        self._start_worker(worker, "restoration.csv.status.restoring", self._restoration_completed)

    def _start_worker(
        self,
        worker: RestorationAnalysisWorker | CSVRestorationWorker,
        status_id: str,
        completed: Callable[..., None],
    ) -> None:
        self._reset_operation_feedback()
        self._worker = worker
        self._worker_source_index = self.source_combo.currentIndex()
        self._set_processing_state(True)
        self.progress_bar.reset()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self._progress_message = ("restoration.csv.progress.initial", {"count": 0})
        self._render_progress()
        self.progress_label.setVisible(True)
        self._set_status_id(status_id)
        self._processing_started_at = time.perf_counter()

        def deliver(callback: Callable[..., None], *args: object) -> None:
            # A retired worker's queued callbacks must not affect a new context
            # or finish a replacement worker. Keep normal Qt signal delivery.
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

    def _progress_changed(self, progress: RestorationProgress) -> None:
        elapsed = time.perf_counter() - getattr(
            self, "_processing_started_at", time.perf_counter()
        )
        metrics = calculate_metrics(progress.rows_processed, elapsed)
        self._progress_message = ("restoration.csv.progress.metrics", dict(
            rows=progress.rows_processed, restored=progress.restored_codes, missing=progress.missing_codes,
            preserved=progress.preserved_values, errors=progress.errors,
            rate=f"{metrics.rows_per_second:,.0f}", seconds=f"{metrics.elapsed_seconds:.1f}"))
        self._render_progress()

    def _render_progress(self) -> None:
        if self._progress_message is None:
            return
        message_id, parameters = self._progress_message
        self.progress_label.setText(
            self._translator.plural(message_id, parameters["count"])
            if message_id == "restoration.csv.progress.initial" else
            self._translator.text(message_id, **parameters))

    def _restoration_completed(self, result: RestorationResult) -> None:
        self._operation_succeeded = True
        self.package_controls.clear_password()
        self._last_output_path = result.output_path
        self._displayed_result = result
        self._render_result()
        self.open_folder_button.setVisible(True)
        self._set_status_id(
            "restoration.csv.status.completed",
            state="warning" if result.missing_codes else "success",
        )

    def _render_result(self) -> None:
        result = self._displayed_result
        if isinstance(result, AnalysisResult):
            self.summary.setPlainText(self._translator.text("restoration.csv.analysis.summary",
                cells=result.cells_analyzed, valid=result.valid_codes, found=result.found_codes,
                missing=result.missing_codes, invalid=result.invalid_formats, empty=result.empty_cells,
                common=result.common_values,
                prefixes=", ".join(result.prefixes) or self._translator.text("restoration.csv.analysis.no_prefixes"),
                incompatibilities="\n".join(result.possible_incompatibilities)
                    or self._translator.text("restoration.csv.analysis.no_incompatibilities")))
        elif isinstance(result, RestorationResult):
            representation_id = (
                "restoration.csv.representation.canonical_summary"
                if result.representation_policy is RepresentationPolicy.CANONICAL else
                _REPRESENTATION_IDS[result.representation_policy])
            self.summary.setPlainText(self._translator.text("restoration.csv.result.summary",
                path=str(result.output_path), rows=result.rows_processed, restored=result.restored_codes,
                missing=result.missing_codes, preserved=result.preserved_common_values, empty=result.empty_cells,
                seconds=f"{result.duration_seconds:.2f}",
                missing_policy=self._translator.text(_MISSING_POLICY_IDS[result.missing_code_policy]),
                representation=self._translator.text(representation_id)))

    def _cancelled(self) -> None:
        self._operation_succeeded = False
        self.package_controls.clear_password()
        self._set_status_id("restoration.csv.status.cancelled", state="warning")

    def _failed(self, error: Exception) -> None:
        self._operation_succeeded = False
        self.package_controls.clear_password()
        self._last_error = error
        if isinstance(error, RestorationSecurityError):
            self._set_status_id("restoration.csv.error.security", is_error=True)
        elif self._package_mode() and isinstance(error, RestorationError):
            # The worker intentionally redacts all package failures to one safe
            # outcome. Present that outcome in this GUI's locale, not by parsing
            # or translating backend text. Keep the original error for diagnostics.
            self._set_status_id("restoration.csv.error.package", is_error=True)
        elif isinstance(error, RestorationError):
            self._set_status(str(error), is_error=True)
        else:
            self._set_status_id("restoration.csv.error.unexpected", is_error=True)

    def _worker_finished(self) -> None:
        self.package_controls.clear_password()
        worker = self._worker
        self._worker = None
        self._set_processing_state(False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1 if getattr(self, "_operation_succeeded", False) else 0)
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
        self.source_combo.setEnabled(not processing)
        self.package_controls.setEnabled(not processing and self._package_mode())
        self.select_button.setEnabled(not processing)
        self.table.setEnabled(not processing)
        has_file = self._inspection is not None
        for widget in (
            self.select_all_button,
            self.unselect_all_button,
            self.analyze_button,
            self.generate_button,
            self.missing_policy_combo,
            self.representation_combo,
        ):
            widget.setEnabled(not processing and has_file)
        self.cancel_button.setVisible(processing)
        self.cancel_button.setEnabled(processing)
        if self._package_mode():
            self.analyze_button.setEnabled(False)
            self.missing_policy_combo.setEnabled(False)
        self.busy_changed.emit(processing)

    def _update_enabled_state(self, has_file: bool) -> None:
        for widget in (
            self.select_all_button,
            self.unselect_all_button,
            self.analyze_button,
            self.generate_button,
            self.missing_policy_combo,
            self.representation_combo,
        ):
            widget.setEnabled(has_file)
        if self._package_mode():
            self.analyze_button.setEnabled(False)
            self.missing_policy_combo.setEnabled(False)

    def open_output_folder(self) -> None:
        if self._last_output_path is None:
            return
        if not QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self._last_output_path.parent))
        ):
            self._set_status_id("restoration.csv.error.open_folder", is_error=True)

    def _show_input_error(self, error: RestorationError) -> None:
        if isinstance(error, RestorationInputError):
            self._set_status_id(error.message_id, is_error=True)
        else:
            self._set_status(str(error), is_error=True)

    def _set_status_id(self, message_id: str, *, is_error: bool = False,
                       state: FeedbackState = "neutral", **parameters: str | int | float) -> None:
        self._set_status(self._translator.text(message_id, **parameters), is_error=is_error, state=state)
        self._status_message = (message_id, parameters)

    def _set_status(
        self, message: str, *, is_error: bool, state: FeedbackState = "neutral"
    ) -> None:
        self._status_message = None
        self.status_label.setText(message)
        set_feedback_state(self.status_label, "error" if is_error else state)

from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from data_mask_studio.gui.components.scroll_safe_combo_box import ScrollSafeComboBox

from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
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
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.batch_restoration import (
    STATUS_LABELS,
    BatchMissingCodePolicy,
    BatchRestorationError,
    BatchRestorationFile,
    BatchRestorationFileType,
    BatchRestorationOptions,
    BatchRestorationProgress,
    BatchRestorationService,
    BatchRestorationStatus,
    BatchRestorationSummary,
    add_files,
    discover_files,
    invalidate_files,
)
from data_mask_studio.gui.batch_restoration_worker import (
    BatchRestorationAnalysisWorker,
    BatchRestorationProcessingWorker,
)
from data_mask_studio.gui.components import EmptyStateTable, EmptyStateTextEdit
from data_mask_studio.gui.components.presentation import (
    FeedbackState, TruncatedTextToolTipDelegate, configure_result_area,
    set_button_role, set_feedback_state,
)
from data_mask_studio.gui.visual_tokens import COLORS, METRICS
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.vault import VaultRepository

RepositoryFactory = Callable[[], VaultRepository]


class BatchRestorationWidget(QWidget):
    busy_changed = Signal(bool)

    def __init__(
        self,
        repository_factory: RepositoryFactory,
        prepare_operation: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        self._service = BatchRestorationService(repository_factory)
        self._prepare_operation = prepare_operation or (lambda: True)
        self.files: list[BatchRestorationFile] = []
        self._analysis_worker: BatchRestorationAnalysisWorker | None = None
        self._processing_worker: BatchRestorationProcessingWorker | None = None
        self._output_directory: Path | None = None
        self._table_population: tuple[bool, bool] | None = None
        self._restoration_ignored_files = 0

        self.add_files_button = QPushButton("Adicionar arquivos")
        self.add_files_button.clicked.connect(self._choose_files)
        self.add_folder_button = QPushButton("Adicionar pasta")
        self.add_folder_button.clicked.connect(self._choose_folder)
        self.remove_button = QPushButton("Remover selecionados")
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button = QPushButton("Limpar lista")
        self.clear_button.clicked.connect(self.clear_files)
        file_actions = QHBoxLayout()
        for button in (
            self.add_files_button,
            self.add_folder_button,
            self.remove_button,
            self.clear_button,
        ):
            file_actions.addWidget(button)
        file_actions.addStretch()

        self.file_table = EmptyStateTable(0, 8, "Nenhum arquivo adicionado.")
        self.file_table.setHorizontalHeaderLabels(
            [
                "Arquivo",
                "Tipo",
                "Codificação",
                "Status",
                "Códigos",
                "No cofre",
                "Ausentes",
                "Resultado",
            ]
        )
        self.file_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.setWordWrap(False)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.currentCellChanged.connect(self._selected_file_changed)
        file_header = self.file_table.horizontalHeader()
        file_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        file_header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        for index in range(1, 7):
            file_header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)

        self.column_table = EmptyStateTable(
            0, 4, "Selecione um CSV analisado para revisar as colunas."
        )
        self.column_table.setHorizontalHeaderLabels(
            ["Restaurar", "Cabeçalho", "Códigos válidos", "No cofre / ausentes"]
        )
        self.column_table.verticalHeader().setVisible(False)
        self.column_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.column_table.setItemDelegateForColumn(1, TruncatedTextToolTipDelegate(self.column_table))
        column_header = self.column_table.horizontalHeader()
        column_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        column_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        column_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        column_header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.select_candidates_button = QPushButton("Selecionar colunas reconhecidas")
        self.select_candidates_button.clicked.connect(self.select_candidate_columns)
        self.unselect_columns_button = QPushButton("Desmarcar todas")
        self.unselect_columns_button.clicked.connect(self.unselect_all_columns)
        column_actions = QHBoxLayout()
        column_actions.addWidget(self.select_candidates_button)
        column_actions.addWidget(self.unselect_columns_button)
        column_actions.addStretch()
        self._column_panel = QWidget()
        column_layout = QVBoxLayout(self._column_panel)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.setSpacing(METRICS["panel_spacing"])
        column_layout.addWidget(QLabel("Revisão das colunas do CSV selecionado:"))
        column_layout.addLayout(column_actions)
        column_layout.addWidget(self.column_table)

        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(6)
        self.splitter.addWidget(self.file_table)
        self.splitter.addWidget(self._column_panel)
        self.splitter_panel = QWidget()
        splitter_layout = QVBoxLayout(self.splitter_panel)
        splitter_layout.setContentsMargins(0, 0, 0, 0)
        splitter_layout.addWidget(self.splitter)

        self.output_field = QLineEdit()
        self.output_field.setPlaceholderText("Escolha a pasta de saída")
        self.output_field.textChanged.connect(self._update_actions)
        self.choose_output_button = QPushButton("Escolher pasta")
        self.choose_output_button.clicked.connect(self._choose_output_directory)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Pasta de saída:"))
        output_row.addWidget(self.output_field, stretch=1)
        output_row.addWidget(self.choose_output_button)

        self.representation_combo = ScrollSafeComboBox()
        self.representation_combo.setAccessibleName("Representação restaurada")
        self.representation_combo.addItem(
            "Primeira representação original", RepresentationPolicy.FIRST_ORIGINAL.value
        )
        self.representation_combo.addItem(
            "Valor canônico", RepresentationPolicy.CANONICAL.value
        )
        self.missing_policy_combo = ScrollSafeComboBox()
        self.missing_policy_combo.setAccessibleName("Códigos ausentes")
        self.missing_policy_combo.addItem(
            "Manter código original", BatchMissingCodePolicy.KEEP.value
        )
        self.missing_policy_combo.addItem(
            "Interromper somente o arquivo", BatchMissingCodePolicy.ABORT_FILE.value
        )
        self.missing_policy_combo.addItem(
            "Interromper todo o lote", BatchMissingCodePolicy.ABORT_BATCH.value
        )
        self.options_controls = QWidget()
        options = QFormLayout(self.options_controls)
        options.setContentsMargins(0, 0, 0, 0)
        options.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        options.addRow("Representação restaurada:", self.representation_combo)
        options.addRow("Códigos ausentes:", self.missing_policy_combo)
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

        self.analyze_button = QPushButton("Analisar arquivos")
        self.analyze_button.clicked.connect(self.analyze_files)
        self.start_button = QPushButton("Iniciar restauração")
        self.start_button.clicked.connect(self.start_restoration)
        set_button_role(self.analyze_button, "secondary")
        set_button_role(self.start_button, "primary")
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setVisible(False)
        self.open_output_button = QPushButton("Abrir pasta de saída")
        self.open_output_button.clicked.connect(self.open_output_directory)
        self.open_output_button.setEnabled(False)
        operation_row = QHBoxLayout()
        operation_row.addWidget(self.analyze_button)
        operation_row.addWidget(self.start_button)
        operation_row.addWidget(self.cancel_button)
        operation_row.addStretch()
        operation_row.addWidget(self.open_output_button)

        self.current_progress = QProgressBar()
        self.current_progress.setRange(0, 1)
        self.current_progress.setValue(0)
        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 1)
        self.overall_progress.setValue(0)
        progress_layout = QFormLayout()
        progress_layout.setVerticalSpacing(METRICS["panel_spacing"])
        progress_layout.addRow("Progresso do arquivo atual:", self.current_progress)
        progress_layout.addRow("Progresso geral:", self.overall_progress)
        self.current_progress_label = QLabel("Nenhum processamento em andamento.")
        self.summary_output = EmptyStateTextEdit(
            "O resumo final aparecerá aqui."
        )
        self.summary_output.setReadOnly(True)
        self.summary_output.setMinimumHeight(self.summary_output.minimumSizeHint().height())
        configure_result_area(self.summary_output, self.summary_output.minimumHeight())
        self.status_label = QLabel("Adicione arquivos CSV ou HTML para começar.")
        self.status_label.setWordWrap(True)
        set_feedback_state(self.status_label, "neutral")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.setSpacing(METRICS["panel_spacing"])
        layout.addLayout(file_actions)
        layout.addWidget(self.splitter_panel)
        layout.addLayout(output_row)
        layout.addWidget(self.options_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.options_summary)
        layout.addWidget(self.options_controls)
        layout.addLayout(operation_row)
        layout.addLayout(progress_layout)
        layout.addWidget(self.current_progress_label)
        layout.addWidget(self.summary_output)
        layout.addWidget(self.status_label)
        layout.addStretch()
        for table in (self.file_table, self.column_table):
            table.model().rowsInserted.connect(self._update_information_layout)
            table.model().rowsRemoved.connect(self._update_information_layout)
        self.summary_output.textChanged.connect(self._update_information_layout)
        self._update_information_layout()
        option_tab_order = (
            self.output_field, self.choose_output_button, self.options_toggle,
            self.representation_combo, self.missing_policy_combo,
            self.analyze_button, self.start_button,
        )
        for previous, following in zip(option_tab_order, option_tab_order[1:]):
            QWidget.setTabOrder(previous, following)
        self._update_options_summary()
        self._update_options_disclosure()
        self._update_actions()

    def _update_information_layout(self) -> None:
        has_files = self.file_table.rowCount() > 0
        has_columns = self.column_table.rowCount() > 0
        has_summary = bool(self.summary_output.toPlainText().strip())
        for table, populated in ((self.file_table, has_files), (self.column_table, has_columns)):
            # Keep the empty message and at least two rows readable at any DPI.
            minimum = (
                table.horizontalHeader().sizeHint().height()
                + 2 * table.verticalHeader().defaultSectionSize()
                + 2 * table.frameWidth()
            )
            table.setMinimumHeight(minimum)
            table.setMaximumHeight(16_777_215 if populated else minimum)
        column_minimum = self._column_panel.layout().minimumSize().height()
        self._column_panel.setMinimumHeight(column_minimum)
        self._column_panel.setMaximumHeight(16_777_215 if has_columns else column_minimum)
        self.splitter.setStretchFactor(0, int(has_files))
        self.splitter.setStretchFactor(1, int(has_columns))
        splitter_minimum = self.splitter.minimumSizeHint().height()
        self.splitter_panel.setMinimumHeight(splitter_minimum)
        self.splitter_panel.setMaximumHeight(
            16_777_215 if has_files or has_columns else splitter_minimum
        )
        population = (has_files, has_columns)
        if population != self._table_population:
            # A previously capped pane must participate in the splitter again.
            # Do not reset the user's divider when refreshing existing rows.
            self.splitter.setSizes([
                self.file_table.sizeHint().height(), self._column_panel.sizeHint().height(),
            ])
            self._table_population = population
        # Let populated views share available height rather than imposing their
        # preferred size on the enclosing page's scroll area. Explicit minima
        # keep both tables usable; the splitter remains manually adjustable.
        for view, populated in (
            (self.splitter_panel, has_files or has_columns),
            (self.summary_output, has_summary),
        ):
            policy = view.sizePolicy()
            policy.setVerticalPolicy(
                QSizePolicy.Policy.Ignored if populated else QSizePolicy.Policy.Preferred
            )
            view.setSizePolicy(policy)
        layout = self.layout()
        layout.setStretch(layout.indexOf(self.splitter_panel), int(has_files) + int(has_columns))
        layout.setStretch(layout.indexOf(self.summary_output), int(has_summary))
        layout.setStretch(layout.count() - 1, int(not (has_files or has_columns or has_summary)))

    def _update_options_disclosure(self, *_args: object) -> None:
        focused = QApplication.focusWidget()
        options_have_focus = focused is not None and self.options_controls.isAncestorOf(focused)
        expanded = self.options_toggle.isChecked()
        self.options_controls.setVisible(expanded)
        self.options_summary.setVisible(not expanded)
        self.options_toggle.setText(
            "Ocultar opções de restauração" if expanded else "Opções de restauração"
        )
        if options_have_focus and not expanded and self.isVisible():
            self.options_toggle.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_options_summary(self, *_args: object) -> None:
        self.options_summary.setText(
            f"Não encontrados: {self.missing_policy_combo.currentText()}. "
            f"Valor: {self.representation_combo.currentText()}."
        )

    def add_paths(self, paths: Iterable[str | Path]) -> int:
        added = add_files(self.files, paths)
        if added:
            self._refresh_file_table()
            self._set_status(f"{added} arquivo(s) adicionado(s).", False)
        self._update_actions()
        return added

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Adicionar arquivos anonimizados",
            "",
            "Arquivos CSV e HTML (*.csv *.html *.htm)",
        )
        if paths:
            self.add_paths(paths)

    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Adicionar pasta")
        if not directory:
            return
        try:
            found = discover_files(directory)
        except BatchRestorationError as error:
            self._set_status(str(error), True)
            return
        if not found:
            self._set_status("Nenhum CSV ou HTML foi encontrado na pasta.", False)
            return
        self.add_paths(found)

    def remove_selected(self) -> None:
        rows = sorted(
            {index.row() for index in self.file_table.selectedIndexes()}, reverse=True
        )
        for row in rows:
            del self.files[row]
        self._refresh_file_table()
        self._refresh_column_table(None)
        self._update_actions()

    def clear_files(self) -> None:
        if self._analysis_worker is not None or self._processing_worker is not None:
            return
        self.files.clear()
        self._refresh_file_table()
        self._refresh_column_table(None)
        self.summary_output.clear()
        self._output_directory = None
        self.open_output_button.setEnabled(False)
        self.overall_progress.setRange(0, 1)
        self.overall_progress.setValue(0)
        self._reset_current_progress()
        self._set_status("Adicione arquivos CSV ou HTML para começar.", False)
        self._update_actions()

    def invalidate_analysis(self) -> None:
        invalidate_files(self.files)
        self._refresh_file_table()
        self._refresh_column_table(self._selected_file())
        self._update_actions()

    def analyze_files(self) -> None:
        if not self.files:
            self._set_status("Adicione ao menos um arquivo.", True)
            return
        if not self._prepare_operation():
            self._set_status("Finalize as outras operações antes da análise.", True)
            return
        invalidate_files(self.files)
        self._refresh_file_table()
        worker = BatchRestorationAnalysisWorker(self._service, self.files)
        self._analysis_worker = worker
        worker.file_changed.connect(self._file_changed)
        worker.progress.connect(self._analysis_progress)
        worker.completed.connect(self._analysis_completed)
        worker.cancelled.connect(self._analysis_cancelled)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(self._analysis_finished)
        self._set_busy(True, "Analisando arquivos...")
        self.overall_progress.setRange(0, len(self.files))
        self.overall_progress.setValue(0)
        worker.start()

    def _analysis_progress(self, completed: int, total: int) -> None:
        self.overall_progress.setRange(0, total)
        self.overall_progress.setValue(completed)

    def _analysis_cancelled(self) -> None:
        self._reset_current_progress()
        self._set_status("Análise cancelada com segurança.", False, state="warning")

    def _analysis_completed(self) -> None:
        self._reset_current_progress()
        compatible = sum(
            item.status is BatchRestorationStatus.COMPATIBLE for item in self.files
        )
        review = sum(
            item.status is BatchRestorationStatus.REVIEW_REQUIRED for item in self.files
        )
        self._set_status(
            f"Análise concluída: {compatible} compatível(is), {review} para revisão.",
            compatible + review == 0,
            state="warning" if review or compatible < len(self.files) else "success",
        )

    def _analysis_finished(self) -> None:
        worker = self._analysis_worker
        self._analysis_worker = None
        if worker:
            worker.deleteLater()
        self._set_busy(False)

    def select_candidate_columns(self) -> None:
        item = self._selected_file()
        if item is None or item.file_type is not BatchRestorationFileType.CSV:
            return
        for column in item.columns:
            column.selected = column.is_candidate
        self._refresh_column_table(item)
        self._update_actions()

    def unselect_all_columns(self) -> None:
        item = self._selected_file()
        if item is None:
            return
        for column in item.columns:
            column.selected = False
        self._refresh_column_table(item)
        self._update_actions()

    def _column_toggled(self, row: int, checked: bool) -> None:
        item = self._selected_file()
        if item is not None and row < len(item.columns):
            item.columns[row].selected = checked
        self._update_actions()

    def _choose_output_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Escolher pasta de saída")
        if directory:
            self.output_field.setText(directory)

    def start_restoration(self) -> None:
        if not self._can_start():
            self._set_status(
                "Analise os arquivos, confirme as colunas CSV e escolha a pasta de saída.",
                True,
            )
            return
        if not self._prepare_operation():
            self._set_status("Finalize as outras operações antes da restauração.", True)
            return
        output = Path(self.output_field.text().strip()).expanduser()
        if not output.is_dir():
            self._set_status("A pasta de saída não existe ou é inválida.", True)
            return
        if QMessageBox.warning(
            self,
            "Confirmar restauração em lote",
            "Os arquivos gerados poderão conter dados pessoais ou sensíveis. "
            "Deseja iniciar a restauração?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        # A configured destination is not evidence that this run published files.
        self._output_directory = None
        self.open_output_button.setEnabled(False)
        options = BatchRestorationOptions(
            representation_policy=RepresentationPolicy(
                self.representation_combo.currentData()
            ),
            missing_code_policy=BatchMissingCodePolicy(
                self.missing_policy_combo.currentData()
            ),
        )
        eligible = sum(
            item.status
            in {
                BatchRestorationStatus.COMPATIBLE,
                BatchRestorationStatus.REVIEW_REQUIRED,
            }
            for item in self.files
        )
        self._restoration_ignored_files = sum(
            item.status is BatchRestorationStatus.INCOMPATIBLE for item in self.files
        )
        # The final summary includes these files; worker counters do not.
        self.overall_progress.setRange(0, eligible + self._restoration_ignored_files)
        self.overall_progress.setValue(self._restoration_ignored_files)
        self.summary_output.clear()
        worker = BatchRestorationProcessingWorker(
            self._service, self.files, str(output.absolute()), options
        )
        self._processing_worker = worker
        worker.file_changed.connect(self._file_changed)
        worker.progress.connect(self._progress_changed)
        worker.completed.connect(self._processing_completed)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(self._processing_finished)
        self._set_busy(True, "Restaurando arquivos sequencialmente...")
        worker.start()

    def cancel(self) -> None:
        worker = self._processing_worker or self._analysis_worker
        if worker:
            worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self._set_status("Cancelamento solicitado...", False)

    def _progress_changed(self, progress: BatchRestorationProgress) -> None:
        self.overall_progress.setValue(
            progress.completed_files + progress.error_files + self._restoration_ignored_files
        )
        if progress.current_total:
            self.current_progress.setRange(0, progress.current_total)
            self.current_progress.setValue(progress.current_value)
        else:
            self.current_progress.setRange(0, 0)
        self.current_progress_label.setText(
            f"Arquivo {progress.current_file} de {progress.total_files}: "
            f"{progress.file_name}"
        )

    def _processing_completed(self, summary: BatchRestorationSummary) -> None:
        # Cancellation skips unfinished files too; do not count them as processed.
        ignored = (
            self._restoration_ignored_files
            if summary.cancelled or summary.cancelled_files else summary.skipped_files
        )
        self.overall_progress.setValue(
            summary.completed_files + summary.error_files + ignored
        )
        self._reset_current_progress()
        self.summary_output.setPlainText(_render_summary(summary))
        if summary.completed_files > 0:
            self._output_directory = summary.output_directory
        self.open_output_button.setEnabled(self._output_directory is not None)
        message = "Lote cancelado com segurança." if summary.cancelled else "Lote concluído."
        self._set_status(
            message, False,
            state="error" if summary.error_files else "warning"
            if summary.cancelled or summary.cancelled_files or summary.skipped_files
            or summary.missing_occurrences else "success",
        )

    def _processing_finished(self) -> None:
        worker = self._processing_worker
        self._processing_worker = None
        if worker:
            worker.deleteLater()
        self._set_busy(False)

    def _worker_failed(self, error: Exception) -> None:
        self._reset_current_progress()
        message = (
            str(error)
            if isinstance(error, BatchRestorationError)
            else "A operação em lote falhou com segurança."
        )
        self._set_status(message, True)
        self._refresh_file_table()

    def _file_changed(self, _item: BatchRestorationFile) -> None:
        if (self._processing_worker is not None
                and _item.status is BatchRestorationStatus.COMPLETED
                and _item.output_path is not None):
            self._output_directory = _item.output_path.parent
            self.open_output_button.setEnabled(True)
        selected_row = self.file_table.currentRow()
        self._refresh_file_table()
        if 0 <= selected_row < len(self.files):
            self.file_table.selectRow(selected_row)
            self._refresh_column_table(self.files[selected_row])

    def _selected_file_changed(
        self, current_row: int, _current_column: int, _previous_row: int, _previous_column: int
    ) -> None:
        item = self.files[current_row] if 0 <= current_row < len(self.files) else None
        self._refresh_column_table(item)

    def _selected_file(self) -> BatchRestorationFile | None:
        row = self.file_table.currentRow()
        return self.files[row] if 0 <= row < len(self.files) else None

    def _refresh_file_table(self) -> None:
        self.file_table.setRowCount(len(self.files))
        for row, item in enumerate(self.files):
            values = (
                item.path.name,
                item.file_type.value.upper(),
                item.encoding or "—",
                STATUS_LABELS[item.status],
                str(item.codes_found),
                str(item.codes_in_vault),
                str(item.missing_codes),
                item.result_message,
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column in (0, 7):
                    cell.setToolTip(str(item.path) if column == 0 else value)
                self.file_table.setItem(row, column, cell)

    def _refresh_column_table(self, item: BatchRestorationFile | None) -> None:
        columns = (
            item.columns
            if item is not None and item.file_type is BatchRestorationFileType.CSV
            else []
        )
        self.column_table.setRowCount(len(columns))
        for row, column in enumerate(columns):
            checkbox = QCheckBox()
            checkbox.setChecked(column.selected)
            checkbox.setEnabled(column.valid_codes > 0 and not self.has_running_workers())
            checkbox.toggled.connect(
                lambda checked, current_row=row: self._column_toggled(current_row, checked)
            )
            self.column_table.setCellWidget(row, 0, checkbox)
            self.column_table.setItem(row, 1, QTableWidgetItem(column.header))
            self.column_table.setItem(row, 2, QTableWidgetItem(str(column.valid_codes)))
            self.column_table.setItem(
                row,
                3,
                QTableWidgetItem(f"{column.found_codes} / {column.missing_codes}"),
            )
        enabled = bool(columns) and not self.has_running_workers()
        self.select_candidates_button.setEnabled(enabled)
        self.unselect_columns_button.setEnabled(enabled)

    def _reset_current_progress(self) -> None:
        """Sem arquivo ativo: encerra a animação sem representar sucesso."""
        self.current_progress.setRange(0, 1)
        self.current_progress.setValue(0)
        self.current_progress_label.setText("Nenhum processamento em andamento.")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._reset_current_progress()
        if busy:
            self.current_progress.setRange(0, 0)
            self.current_progress_label.clear()
        for control in (
            self.add_files_button,
            self.add_folder_button,
            self.remove_button,
            self.clear_button,
            self.file_table,
            self.output_field,
            self.choose_output_button,
            self.options_toggle,
            self.representation_combo,
            self.missing_policy_combo,
            self.analyze_button,
            self.start_button,
            self.select_candidates_button,
            self.unselect_columns_button,
        ):
            control.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        self.busy_changed.emit(busy)
        if message:
            self._set_status(message, False)
        if not busy:
            self._refresh_column_table(self._selected_file())
            self._update_actions()

    def _can_start(self) -> bool:
        eligible = [
            item
            for item in self.files
            if item.status
            in {
                BatchRestorationStatus.COMPATIBLE,
                BatchRestorationStatus.REVIEW_REQUIRED,
            }
        ]
        return (
            bool(eligible)
            and bool(self.output_field.text().strip())
            and all(
                item.file_type is BatchRestorationFileType.HTML
                or any(column.selected for column in item.columns)
                for item in eligible
            )
        )

    def _update_actions(self, *_args: object) -> None:
        if self.has_running_workers():
            return
        self.remove_button.setEnabled(bool(self.files))
        self.clear_button.setEnabled(bool(self.files))
        self.analyze_button.setEnabled(bool(self.files))
        self.start_button.setEnabled(self._can_start())

    def open_output_directory(self) -> None:
        if self._output_directory is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_directory)))

    def has_running_workers(self) -> bool:
        return any(
            worker is not None and worker.isRunning()
            for worker in (self._analysis_worker, self._processing_worker)
        )

    def stop_workers(self) -> bool:
        workers = (self._analysis_worker, self._processing_worker)
        for worker in workers:
            if worker is not None and worker.isRunning():
                worker.request_cancel()
        return all(worker is None or worker.wait(5000) for worker in workers)

    def _set_status(
        self, message: str, is_error: bool, *, state: FeedbackState = "neutral"
    ) -> None:
        self.status_label.setText(message)
        set_feedback_state(self.status_label, "error" if is_error else state)


def _render_summary(summary: BatchRestorationSummary) -> str:
    return "\n".join(
        (
            f"Arquivos selecionados: {summary.selected_files}",
            f"Concluídos: {summary.completed_files}",
            f"Com erro: {summary.error_files}",
            f"Ignorados: {summary.skipped_files}",
            f"Cancelados: {summary.cancelled_files}",
            f"Ocorrências restauradas: {summary.restored_occurrences}",
            f"Códigos ausentes: {summary.missing_occurrences}",
            f"Tempo aproximado: {summary.duration_seconds:.2f} s",
            f"Pasta de saída: {summary.output_directory}",
        )
    )

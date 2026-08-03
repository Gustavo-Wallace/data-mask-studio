from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
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

        title = QLabel("Restauração em lote")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
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

        self.file_table = QTableWidget(0, 8)
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
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.currentCellChanged.connect(self._selected_file_changed)
        file_header = self.file_table.horizontalHeader()
        file_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        file_header.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        for index in range(1, 7):
            file_header.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)

        self.column_table = QTableWidget(0, 4)
        self.column_table.setHorizontalHeaderLabels(
            ["Restaurar", "Cabeçalho", "Códigos válidos", "No cofre / ausentes"]
        )
        self.column_table.verticalHeader().setVisible(False)
        self.column_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
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
        column_panel = QWidget()
        column_layout = QVBoxLayout(column_panel)
        column_layout.setContentsMargins(0, 0, 0, 0)
        column_layout.addWidget(QLabel("Revisão das colunas do CSV selecionado:"))
        column_layout.addLayout(column_actions)
        column_layout.addWidget(self.column_table)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.file_table)
        splitter.addWidget(column_panel)
        splitter.setSizes([260, 160])

        self.output_field = QLineEdit()
        self.output_field.setPlaceholderText("Escolha a pasta de saída")
        self.output_field.textChanged.connect(self._update_actions)
        self.choose_output_button = QPushButton("Escolher pasta")
        self.choose_output_button.clicked.connect(self._choose_output_directory)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Pasta de saída:"))
        output_row.addWidget(self.output_field, stretch=1)
        output_row.addWidget(self.choose_output_button)

        self.representation_combo = QComboBox()
        self.representation_combo.addItem(
            "Primeira representação original", RepresentationPolicy.FIRST_ORIGINAL.value
        )
        self.representation_combo.addItem(
            "Valor canônico", RepresentationPolicy.CANONICAL.value
        )
        self.missing_policy_combo = QComboBox()
        self.missing_policy_combo.addItem(
            "Manter código original", BatchMissingCodePolicy.KEEP.value
        )
        self.missing_policy_combo.addItem(
            "Interromper somente o arquivo", BatchMissingCodePolicy.ABORT_FILE.value
        )
        self.missing_policy_combo.addItem(
            "Interromper todo o lote", BatchMissingCodePolicy.ABORT_BATCH.value
        )
        option_row = QHBoxLayout()
        option_row.addWidget(QLabel("Representação restaurada:"))
        option_row.addWidget(self.representation_combo)
        option_row.addWidget(QLabel("Códigos ausentes:"))
        option_row.addWidget(self.missing_policy_combo)
        option_row.addStretch()

        self.analyze_button = QPushButton("Analisar arquivos")
        self.analyze_button.clicked.connect(self.analyze_files)
        self.start_button = QPushButton("Iniciar restauração")
        self.start_button.clicked.connect(self.start_restoration)
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
        self.current_progress_label = QLabel("Nenhum processamento em andamento.")
        self.summary_output = QTextEdit()
        self.summary_output.setReadOnly(True)
        self.summary_output.setMaximumHeight(110)
        self.summary_output.setPlaceholderText("O resumo final aparecerá aqui.")
        self.status_label = QLabel("Adicione arquivos CSV ou HTML para começar.")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.addWidget(title)
        layout.addLayout(file_actions)
        layout.addWidget(splitter, stretch=1)
        layout.addLayout(output_row)
        layout.addLayout(option_row)
        layout.addLayout(operation_row)
        layout.addWidget(QLabel("Progresso do arquivo atual:"))
        layout.addWidget(self.current_progress)
        layout.addWidget(QLabel("Progresso geral:"))
        layout.addWidget(self.overall_progress)
        layout.addWidget(self.current_progress_label)
        layout.addWidget(self.summary_output)
        layout.addWidget(self.status_label)
        self._update_actions()

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
        self.files.clear()
        self._refresh_file_table()
        self._refresh_column_table(None)
        self.summary_output.clear()
        self.open_output_button.setEnabled(False)
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
        self.current_progress.setRange(0, 0)
        worker.start()

    def _analysis_progress(self, completed: int, total: int) -> None:
        self.overall_progress.setRange(0, total)
        self.overall_progress.setValue(completed)

    def _analysis_cancelled(self) -> None:
        self._set_status("Análise cancelada com segurança.", False)

    def _analysis_completed(self) -> None:
        compatible = sum(
            item.status is BatchRestorationStatus.COMPATIBLE for item in self.files
        )
        review = sum(
            item.status is BatchRestorationStatus.REVIEW_REQUIRED for item in self.files
        )
        self._set_status(
            f"Análise concluída: {compatible} compatível(is), {review} para revisão.",
            compatible + review == 0,
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
        self._output_directory = output.absolute()
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
        self.overall_progress.setRange(0, eligible)
        self.overall_progress.setValue(0)
        self.summary_output.clear()
        worker = BatchRestorationProcessingWorker(
            self._service, self.files, str(self._output_directory), options
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
        self.overall_progress.setValue(progress.completed_files + progress.error_files)
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
        self.overall_progress.setValue(
            summary.completed_files + summary.error_files + summary.skipped_files
        )
        self.current_progress.setRange(0, 1)
        self.current_progress.setValue(1)
        self.summary_output.setPlainText(_render_summary(summary))
        self.open_output_button.setEnabled(summary.completed_files > 0)
        message = "Lote cancelado com segurança." if summary.cancelled else "Lote concluído."
        self._set_status(message, False)

    def _processing_finished(self) -> None:
        worker = self._processing_worker
        self._processing_worker = None
        if worker:
            worker.deleteLater()
        self._set_busy(False)

    def _worker_failed(self, error: Exception) -> None:
        message = (
            str(error)
            if isinstance(error, BatchRestorationError)
            else "A operação em lote falhou com segurança."
        )
        self._set_status(message, True)
        self._refresh_file_table()

    def _file_changed(self, _item: BatchRestorationFile) -> None:
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
                self.file_table.setItem(row, column, QTableWidgetItem(value))

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

    def _set_busy(self, busy: bool, message: str = "") -> None:
        for control in (
            self.add_files_button,
            self.add_folder_button,
            self.remove_button,
            self.clear_button,
            self.file_table,
            self.output_field,
            self.choose_output_button,
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

    def _set_status(self, message: str, is_error: bool) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color: {'#b42318' if is_error else '#276749'};"
        )


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

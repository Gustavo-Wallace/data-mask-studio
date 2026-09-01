from collections.abc import Callable
from pathlib import Path
import time

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from data_mask_studio.performance import calculate_metrics

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

SEPARATOR_NAMES = {
    ",": "Vírgula (,)",
    ";": "Ponto e vírgula (;)",
    "\t": "Tabulação",
    "|": "Barra vertical (|)",
}


class RestorationWidget(QWidget):
    def __init__(
        self,
        repository_factory: Callable[[], VaultRepository],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = RestorationService(repository_factory)
        self._inspection: CSVInspectionResult | None = None
        self._checkboxes: list[QCheckBox] = []
        self._worker: RestorationAnalysisWorker | CSVRestorationWorker | None = None
        self._last_output_path: Path | None = None
        self._last_error: Exception | None = None

        self.select_button = QPushButton("Selecionar CSV anonimizado")
        self.select_button.clicked.connect(self._select_csv)
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

        self.table = EmptyStateTable(
            0, 2, "Selecione um CSV anonimizado para revisar as colunas."
        )
        self.table.setHorizontalHeaderLabels(["Restaurar", "Cabeçalho"])
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
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

        self.missing_policy_combo = QComboBox()
        self.missing_policy_combo.addItem(
            "Manter código original", MissingCodePolicy.KEEP.value
        )
        self.missing_policy_combo.addItem(
            "Deixar célula vazia", MissingCodePolicy.EMPTY.value
        )
        self.missing_policy_combo.addItem(
            "Interromper restauração", MissingCodePolicy.ABORT.value
        )
        self.representation_combo = QComboBox()
        self.representation_combo.addItem(
            "Primeira representação original",
            RepresentationPolicy.FIRST_ORIGINAL.value,
        )
        self.representation_combo.addItem(
            "Preferir valor canônico", RepresentationPolicy.CANONICAL.value
        )
        policies = QFormLayout()
        policies.addRow("Códigos não encontrados:", self.missing_policy_combo)
        policies.addRow("Valor restaurado:", self.representation_combo)

        self.analyze_button = QPushButton("Analisar códigos")
        self.analyze_button.clicked.connect(self.start_analysis)
        self.generate_button = QPushButton("Gerar CSV restaurado")
        self.generate_button.clicked.connect(self._choose_output)
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
        self.progress_label.setVisible(False)
        progress = QHBoxLayout()
        progress.addWidget(self.progress_bar, stretch=1)
        progress.addWidget(self.progress_label)

        self.summary = EmptyStatePlainTextEdit(
            "O resumo da análise ou restauração aparecerá aqui."
        )
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(125)
        self.open_folder_button = QPushButton("Abrir pasta do arquivo")
        self.open_folder_button.clicked.connect(self.open_output_folder)
        self.open_folder_button.setVisible(False)
        self.status_label = QLabel("Selecione um CSV anonimizado para comecar.")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(8)
        layout.addLayout(file_buttons)
        layout.addLayout(details)
        layout.addLayout(selection)
        layout.addWidget(self.table, stretch=1)
        layout.addLayout(policies)
        layout.addLayout(actions)
        layout.addLayout(progress)
        layout.addWidget(self.summary)
        layout.addWidget(self.open_folder_button)
        layout.addWidget(self.status_label)
        layout.addStretch()
        self._update_enabled_state(False)

    def _select_csv(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Selecionar CSV anonimizado", "", "Arquivos CSV (*.csv)"
        )
        if file_path:
            self.load_csv(file_path)

    def load_csv(self, file_path: str) -> None:
        try:
            inspection = inspect_csv(file_path)
        except CSVInspectionError as error:
            self._set_status(str(error), is_error=True)
            return
        self._inspection = inspection
        self._last_output_path = None
        self.file_name_label.setText(inspection.path.name)
        self.path_field.setText(str(inspection.path))
        self.encoding_label.setText(inspection.encoding)
        self.delimiter_label.setText(SEPARATOR_NAMES[inspection.delimiter])
        self.table.setRowCount(0)
        self._checkboxes.clear()
        for row, header in enumerate(inspection.headers):
            self.table.insertRow(row)
            checkbox = QCheckBox()
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
        self.summary.clear()
        self.open_folder_button.setVisible(False)
        self._update_selected_count()
        self._update_enabled_state(True)
        warning = format_header_replacement_warning(inspection.header_replacements)
        status = "Cabecalhos lidos com sucesso."
        if warning:
            status = f"{status} {warning}"
        self._set_status(status, is_error=False)

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
        self.selected_count_label.setText(
            f"{count} de {len(self._checkboxes)} colunas selecionadas"
        )

    def _configuration(self) -> RestorationConfiguration:
        if self._inspection is None:
            raise RestorationError("Selecione um arquivo CSV anonimizado.")
        columns = tuple(
            SelectedColumn(index, self._inspection.headers[index])
            for index, checkbox in enumerate(self._checkboxes)
            if checkbox.isChecked()
        )
        if not columns:
            raise RestorationError("Selecione ao menos uma coluna para restaurar.")
        return RestorationConfiguration(
            source_path=self._inspection.path,
            encoding=self._inspection.encoding,
            delimiter=self._inspection.delimiter,
            headers=tuple(self._inspection.headers),
            selected_columns=columns,
            missing_code_policy=MissingCodePolicy(
                self.missing_policy_combo.currentData()
            ),
            representation_policy=RepresentationPolicy(
                self.representation_combo.currentData()
            ),
        )

    def start_analysis(self) -> None:
        if self._worker is not None:
            return
        try:
            configuration = self._configuration()
        except RestorationError as error:
            self._set_status(str(error), is_error=True)
            return
        worker = RestorationAnalysisWorker(self._service, configuration)
        worker.completed.connect(self._analysis_completed)
        self._start_worker(worker, "Analisando códigos...")

    def _analysis_completed(self, result: AnalysisResult) -> None:
        prefixes = ", ".join(result.prefixes) or "nenhum"
        incompatibilities = (
            "\n".join(result.possible_incompatibilities) or "nenhuma"
        )
        self.summary.setPlainText(
            f"Células analisadas: {result.cells_analyzed}\n"
            f"Códigos válidos: {result.valid_codes}\n"
            f"Encontrados no cofre: {result.found_codes}\n"
            f"Não encontrados: {result.missing_codes}\n"
            f"Formatos inválidos: {result.invalid_formats}\n"
            f"Células vazias: {result.empty_cells}\n"
            f"Valores comuns: {result.common_values}\n"
            f"Prefixos: {prefixes}\n"
            f"Possíveis incompatibilidades: {incompatibilities}"
        )
        self._set_status("Análise concluída sem alterar o cofre.", is_error=False)

    def _choose_output(self) -> None:
        try:
            configuration = self._configuration()
        except RestorationError as error:
            self._set_status(str(error), is_error=True)
            return
        confirmation = QMessageBox.warning(
            self,
            "Confirmar restauração de dados sensíveis",
            "O arquivo restaurado poderá conter dados pessoais ou sensíveis. "
            "Mantenha-o em ambiente autorizado.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if confirmation != QMessageBox.StandardButton.Ok:
            return
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar CSV restaurado",
            str(suggested_output_path(configuration.source_path)),
            "Arquivos CSV (*.csv)",
        )
        if not selected_path:
            return
        destination = Path(selected_path)
        if destination.suffix.lower() != ".csv":
            destination = destination.with_suffix(".csv")
        if paths_refer_to_same_file(configuration.source_path, destination):
            self._set_status(
                "O arquivo restaurado não pode substituir o CSV de entrada.",
                is_error=True,
            )
            return
        overwrite = destination.exists()
        if overwrite:
            answer = QMessageBox.question(
                self,
                "Confirmar substituição",
                "O arquivo de saída já existe. Deseja substituí-lo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.start_restoration(destination, overwrite=overwrite)

    def start_restoration(self, destination: Path, *, overwrite: bool) -> None:
        try:
            configuration = self._configuration()
        except RestorationError as error:
            self._set_status(str(error), is_error=True)
            return
        worker = CSVRestorationWorker(
            self._service,
            configuration,
            str(destination),
            overwrite=overwrite,
        )
        worker.completed.connect(self._restoration_completed)
        self._start_worker(worker, "Gerando CSV restaurado...")

    def _start_worker(
        self,
        worker: RestorationAnalysisWorker | CSVRestorationWorker,
        status: str,
    ) -> None:
        self._worker = worker
        self._set_processing_state(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.progress_label.setText("0 linhas processadas")
        self.progress_label.setVisible(True)
        self._set_status(status, is_error=False)
        self._processing_started_at = time.perf_counter()
        worker.progress.connect(self._progress_changed)
        worker.cancelled.connect(self._cancelled)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _progress_changed(self, progress: RestorationProgress) -> None:
        elapsed = time.perf_counter() - getattr(
            self, "_processing_started_at", time.perf_counter()
        )
        metrics = calculate_metrics(progress.rows_processed, elapsed)
        self.progress_label.setText(
            f"{progress.rows_processed} linhas; "
            f"{progress.restored_codes} restaurados; "
            f"{progress.missing_codes} não encontrados; "
            f"{progress.preserved_values} preservados; {progress.errors} erros; "
            f"{metrics.rows_per_second:,.0f} linhas/s; {metrics.elapsed_seconds:.1f} s; "
            "estimativa indisponível"
        )

    def _restoration_completed(self, result: RestorationResult) -> None:
        self._last_output_path = result.output_path
        missing_policy = {
            MissingCodePolicy.KEEP: "Manter código original",
            MissingCodePolicy.EMPTY: "Deixar célula vazia",
            MissingCodePolicy.ABORT: "Interromper restauração",
        }[result.missing_code_policy]
        representation = {
            RepresentationPolicy.FIRST_ORIGINAL: "Primeira representação original",
            RepresentationPolicy.CANONICAL: "Valor canônico",
        }[result.representation_policy]
        self.summary.setPlainText(
            f"Arquivo: {result.output_path}\n"
            f"Linhas processadas: {result.rows_processed}\n"
            f"Códigos restaurados: {result.restored_codes}\n"
            f"Códigos não encontrados: {result.missing_codes}\n"
            f"Valores comuns preservados: {result.preserved_common_values}\n"
            f"Células vazias: {result.empty_cells}\n"
            f"Tempo aproximado: {result.duration_seconds:.2f} s\n"
            f"Política de ausentes: {missing_policy}\n"
            f"Representação: {representation}"
        )
        self.open_folder_button.setVisible(True)
        self._set_status("CSV restaurado gerado com sucesso.", is_error=False)

    def _cancelled(self) -> None:
        self._set_status("A operacao foi cancelada com seguranca.", is_error=False)

    def _failed(self, error: Exception) -> None:
        self._last_error = error
        if isinstance(error, RestorationSecurityError):
            message = "Não foi possível recuperar um ou mais mapeamentos com segurança."
        elif isinstance(error, RestorationError):
            message = str(error)
        else:
            message = "Nao foi possivel concluir a restauracao com seguranca."
        self._set_status(message, is_error=True)

    def _worker_finished(self) -> None:
        worker = self._worker
        self._worker = None
        self._set_processing_state(False)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        if worker is not None:
            worker.deleteLater()

    def cancel_processing(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self._set_status("Cancelamento solicitado...", is_error=False)

    def has_running_worker(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def stop_worker(self) -> bool:
        if not self.has_running_worker():
            return True
        assert self._worker is not None
        self._worker.request_cancel()
        return self._worker.wait(5000)

    def _set_processing_state(self, processing: bool) -> None:
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

    def open_output_folder(self) -> None:
        if self._last_output_path is None:
            return
        if not QDesktopServices.openUrl(
            QUrl.fromLocalFile(str(self._last_output_path.parent))
        ):
            self._set_status("Nao foi possivel abrir a pasta do arquivo.", is_error=True)

    def _set_status(self, message: str, *, is_error: bool) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color: {'#b42318' if is_error else '#276749'};"
        )

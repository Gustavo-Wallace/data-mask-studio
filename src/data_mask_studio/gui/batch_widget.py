from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.batch import (
    STATUS_LABELS,
    BatchError,
    BatchFile,
    BatchFileStatus,
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
from data_mask_studio.profiles import (
    ConfigurationProfile,
    ProfileError,
    ProfileService,
)
from data_mask_studio.security import KeyProvider
from data_mask_studio.vault import VaultRepository

VaultRepositoryFactory = Callable[[], VaultRepository]


class BatchWidget(QWidget):
    def __init__(
        self,
        profile_service: ProfileService | None,
        key_provider: KeyProvider,
        vault_repository_factory: VaultRepositoryFactory,
    ) -> None:
        super().__init__()
        self._profile_service = profile_service
        self._service = BatchService(profile_service) if profile_service else None
        self._key_provider = key_provider
        self._vault_repository_factory = vault_repository_factory
        self._profiles: list[ConfigurationProfile] = []
        self.files: list[BatchFile] = []
        self._validation_worker: BatchValidationWorker | None = None
        self._processing_worker: BatchProcessingWorker | None = None
        self._output_directory: Path | None = None

        title = QLabel("Anonimização em lote")
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

        self.file_table = QTableWidget(0, 5)
        self.file_table.setHorizontalHeaderLabels(
            ["Arquivo", "Caminho", "Status", "Colunas", "Resultado"]
        )
        self.file_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.file_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.file_table.verticalHeader().setVisible(False)
        header = self.file_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

        self.profile_combo = QComboBox()
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("Perfil:"))
        profile_row.addWidget(self.profile_combo, stretch=1)

        self.output_field = QLineEdit()
        self.output_field.setPlaceholderText("Escolha ou informe a pasta de saída")
        self.output_field.textChanged.connect(self._update_actions)
        self.choose_output_button = QPushButton("Escolher pasta")
        self.choose_output_button.clicked.connect(self._choose_output_directory)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("Pasta de saída:"))
        output_row.addWidget(self.output_field, stretch=1)
        output_row.addWidget(self.choose_output_button)

        self.validate_button = QPushButton("Validar arquivos")
        self.validate_button.clicked.connect(self.validate_files)
        self.start_button = QPushButton("Iniciar anonimização")
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
        self.summary_output = QTextEdit()
        self.summary_output.setReadOnly(True)
        self.summary_output.setPlaceholderText("O resumo do lote aparecerá aqui.")
        self.summary_output.setMaximumHeight(125)
        self.status_label = QLabel("Adicione arquivos CSV para começar.")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.addWidget(title)
        layout.addLayout(file_actions)
        layout.addWidget(self.file_table, stretch=1)
        layout.addLayout(profile_row)
        layout.addLayout(output_row)
        layout.addLayout(processing_actions)
        layout.addWidget(self.overall_progress)
        layout.addWidget(self.current_progress_label)
        layout.addWidget(self.summary_output)
        layout.addWidget(self.status_label)

        self.refresh_profiles()
        self._update_actions()

    def refresh_profiles(self) -> None:
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
        ):
            invalidate_files(self.files)
            self._refresh_table()
        self._update_actions()

    def add_paths(self, paths: Iterable[str | Path]) -> int:
        added = add_files(self.files, paths)
        if added:
            self._refresh_table()
            self._set_status(f"{added} arquivo(s) adicionado(s).", is_error=False)
        self._update_actions()
        return added

    def _choose_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Adicionar arquivos CSV", "", "Arquivos CSV (*.csv)"
        )
        if paths:
            self.add_paths(paths)

    def _choose_folder(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Adicionar pasta")
        if not directory:
            return
        try:
            paths = discover_csv_files(directory)
        except BatchError as error:
            self._set_status(str(error), is_error=True)
            return
        if not paths:
            self._set_status("Nenhum arquivo CSV foi encontrado na pasta.", is_error=False)
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
        self.summary_output.clear()
        self.open_output_button.setEnabled(False)
        self._update_actions()

    def _profile_changed(self) -> None:
        invalidate_files(self.files)
        self._refresh_table()
        self._set_status(
            "Perfil alterado. Valide novamente todos os arquivos.", is_error=False
        )
        self._update_actions()

    def _selected_profile(self) -> ConfigurationProfile | None:
        identifier = self.profile_combo.currentData()
        return next(
            (profile for profile in self._profiles if profile.identifier == identifier),
            None,
        )

    def validate_files(self) -> None:
        profile = self._selected_profile()
        if not self.files:
            self._set_status("Adicione ao menos um arquivo ao lote.", is_error=True)
            return
        if profile is None or self._service is None:
            self._set_status("Selecione um perfil de configuração.", is_error=True)
            return
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
        self._set_status(
            f"Validação concluída: {compatible} compatível(is), "
            f"{incompatible} incompatível(is).",
            is_error=compatible == 0,
        )

    def _validation_finished(self) -> None:
        worker = self._validation_worker
        self._validation_worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)

    def _choose_output_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Escolher pasta de saída")
        if directory:
            self.output_field.setText(directory)

    def start_processing(self) -> None:
        profile = self._selected_profile()
        if profile is None or self._service is None:
            self._set_status("Selecione um perfil de configuração.", is_error=True)
            return
        if not self.files or any(
            item.status is BatchFileStatus.PENDING for item in self.files
        ):
            self._set_status("Valide todos os arquivos antes de iniciar.", is_error=True)
            return
        if not any(item.status is BatchFileStatus.COMPATIBLE for item in self.files):
            self._set_status("Não existem arquivos compatíveis para processar.", is_error=True)
            return
        output_text = self.output_field.text().strip()
        if not output_text:
            self._set_status("Escolha uma pasta de saída.", is_error=True)
            return
        output = Path(output_text).expanduser()
        if not output.exists():
            answer = QMessageBox.question(
                self,
                "Criar pasta de saída",
                "A pasta de saída não existe. Deseja criá-la?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                output.mkdir(parents=True, exist_ok=False)
            except OSError:
                self._set_status("Não foi possível criar a pasta de saída.", is_error=True)
                return
        if not output.is_dir():
            self._set_status("A pasta de saída é inválida.", is_error=True)
            return

        self._output_directory = output.absolute()
        self.summary_output.clear()
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
            str(self._output_directory),
            self._key_provider,
            self._vault_repository_factory,
        )
        self._processing_worker = worker
        worker.file_changed.connect(self._file_changed)
        worker.progress.connect(self._progress_changed)
        worker.completed.connect(self._processing_completed)
        worker.failed.connect(self._worker_failed)
        worker.finished.connect(self._processing_finished)
        worker.start()

    def cancel(self) -> None:
        worker = self._processing_worker or self._validation_worker
        if worker is not None:
            worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self._set_status("Cancelamento solicitado...", is_error=False)

    def _file_changed(self, _item: BatchFile) -> None:
        self._refresh_table()

    def _progress_changed(self, progress: BatchProgress) -> None:
        self.overall_progress.setValue(progress.completed_files + progress.error_files)
        self.current_progress_label.setText(
            f"Arquivo {progress.current_file} de {progress.compatible_files}: "
            f"{progress.file_name} — {progress.records_processed} registros; "
            f"{progress.completed_files} concluído(s), {progress.error_files} erro(s)."
        )

    def _processing_completed(self, summary: BatchSummary) -> None:
        self.overall_progress.setValue(
            summary.completed_files
            + summary.error_files
            + summary.cancelled_or_skipped_files
        )
        self.summary_output.setPlainText(_render_summary(summary))
        self.open_output_button.setEnabled(summary.completed_files > 0)
        self._set_status("Processamento em lote encerrado.", is_error=False)

    def _processing_finished(self) -> None:
        worker = self._processing_worker
        self._processing_worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)

    def _worker_failed(self, error: Exception) -> None:
        message = str(error) if isinstance(error, BatchError) else "Falha inesperada no lote."
        self._set_status(message, is_error=True)
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
            self._set_status(
                "Validando arquivos..." if validating else "Processando lote...",
                is_error=False,
            )
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
                STATUS_LABELS[item.status],
                "—" if item.column_count is None else str(item.column_count),
                item.result_message,
            )
            for column, value in enumerate(values):
                self.file_table.setItem(row, column, QTableWidgetItem(value))

    def open_output_directory(self) -> None:
        if self._output_directory is None:
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._output_directory))):
            self._set_status("Não foi possível abrir a pasta de saída.", is_error=True)

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

    def _set_status(self, message: str, *, is_error: bool) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color: {'#b42318' if is_error else '#276749'};"
        )


def _render_summary(summary: BatchSummary) -> str:
    lines = [
            f"Arquivos selecionados: {summary.selected_files}",
            f"Arquivos compatíveis: {summary.compatible_files}",
            f"Arquivos concluídos: {summary.completed_files}",
            f"Arquivos incompatíveis: {summary.incompatible_files}",
            f"Arquivos com erro: {summary.error_files}",
            "Arquivos cancelados ou ignorados: "
            f"{summary.cancelled_or_skipped_files}",
            f"Registros processados: {summary.records_processed}",
            f"Novos mapeamentos: {summary.new_mappings}",
            f"Mapeamentos atualizados: {summary.updated_mappings}",
            f"Tempo aproximado: {summary.duration_seconds:.2f} s",
            f"Pasta de saída: {summary.output_directory}",
    ]
    if summary.normalization_fallbacks:
        lines.append(
            "Valores incompatíveis anonimizados por valor exato: "
            + "; ".join(
                f"{item.header}: {item.count} fallback(s)"
                for item in summary.normalization_fallbacks
            )
        )
    return "\n".join(lines)

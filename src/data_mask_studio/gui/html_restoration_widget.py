from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.csv_tools.csv_anonymizer import paths_refer_to_same_file
from data_mask_studio.gui.html_restoration_worker import (
    HTMLAnalysisWorker,
    HTMLRestorationWorker,
)
from data_mask_studio.gui.components import EmptyStatePlainTextEdit
from data_mask_studio.html_restoration import (
    HTMLAnalysisResult,
    HTMLInspectionResult,
    HTMLMissingCodePolicy,
    HTMLRestorationError,
    HTMLRestorationProgress,
    HTMLRestorationResult,
    HTMLRestorationSecurityError,
    HTMLRestorationService,
    inspect_html,
    suggested_html_output_path,
)
from data_mask_studio.restoration import RepresentationPolicy
from data_mask_studio.vault import VaultRepository


class HTMLRestorationWidget(QWidget):
    def __init__(
        self,
        repository_factory: Callable[[], VaultRepository],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = HTMLRestorationService(repository_factory)
        self._inspection: HTMLInspectionResult | None = None
        self._worker: HTMLAnalysisWorker | HTMLRestorationWorker | None = None
        self._last_output_path: Path | None = None
        self._last_error: Exception | None = None

        self.select_button = QPushButton("Selecionar HTML anonimizado")
        self.select_button.clicked.connect(self._select_html)
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

        self.missing_policy_combo = QComboBox()
        self.missing_policy_combo.addItem(
            "Manter código original", HTMLMissingCodePolicy.KEEP.value
        )
        self.missing_policy_combo.addItem(
            "Interromper restauração", HTMLMissingCodePolicy.ABORT.value
        )
        self.representation_combo = QComboBox()
        self.representation_combo.addItem(
            "Primeira representação original",
            RepresentationPolicy.FIRST_ORIGINAL.value,
        )
        self.representation_combo.addItem(
            "Valor canônico normalizado", RepresentationPolicy.CANONICAL.value
        )
        policies = QFormLayout()
        policies.addRow("Códigos não encontrados:", self.missing_policy_combo)
        policies.addRow("Valor restaurado:", self.representation_combo)

        self.analyze_button = QPushButton("Analisar códigos")
        self.analyze_button.clicked.connect(self.start_analysis)
        self.generate_button = QPushButton("Gerar HTML restaurado")
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
            "O resumo da análise aparecerá aqui."
        )
        self.summary.setReadOnly(True)
        self.open_folder_button = QPushButton("Abrir pasta do resultado")
        self.open_folder_button.clicked.connect(self.open_output_folder)
        self.open_folder_button.setVisible(False)
        self.status_label = QLabel("Selecione um arquivo HTML para começar.")
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 24, 36, 24)
        layout.setSpacing(10)
        layout.addLayout(select_layout)
        layout.addLayout(details)
        layout.addLayout(policies)
        layout.addLayout(actions)
        layout.addLayout(progress)
        layout.addWidget(self.summary)
        layout.addWidget(self.open_folder_button)
        layout.addWidget(self.status_label)
        layout.addStretch()
        self._set_file_controls_enabled(False)

    def _select_html(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Selecionar HTML anonimizado",
            "",
            "Arquivos HTML (*.html *.htm)",
        )
        if path:
            self.load_html(path)

    def load_html(self, path: str) -> None:
        self._reset_progress()
        try:
            inspection = inspect_html(path)
        except HTMLRestorationError as error:
            self._set_status(str(error), is_error=True)
            return
        self._inspection = inspection
        self._last_output_path = None
        self.file_name_label.setText(inspection.path.name)
        self.path_field.setText(str(inspection.path))
        self.encoding_label.setText(inspection.encoding)
        self.summary.clear()
        self.open_folder_button.setVisible(False)
        self._set_file_controls_enabled(True)
        self._set_status("Arquivo HTML inspecionado com sucesso.", is_error=False)

    def start_analysis(self) -> None:
        if self._inspection is None or self._worker is not None:
            return
        worker = HTMLAnalysisWorker(self._service, self._inspection)
        worker.completed.connect(self._analysis_completed)
        self._start_worker(worker, "Analisando códigos no HTML...")

    def _analysis_completed(self, result: HTMLAnalysisResult) -> None:
        prefixes = ", ".join(result.prefixes) or "nenhum"
        self.summary.setPlainText(
            f"Códigos únicos encontrados: {result.unique_codes}\n"
            f"Total de ocorrências: {result.total_occurrences}\n"
            f"Códigos existentes no cofre: {result.found_codes}\n"
            f"Códigos não encontrados: {result.missing_codes}\n"
            f"Formatos semelhantes inválidos: {result.invalid_similar_codes}\n"
            f"Prefixos encontrados: {prefixes}"
        )
        self._set_status("Análise concluída sem modificar o arquivo ou o cofre.", False)

    def _choose_output(self) -> None:
        if self._inspection is None:
            return
        answer = QMessageBox.warning(
            self,
            "Confirmar restauração de dados sensíveis",
            "O HTML restaurado poderá conter dados pessoais ou sensíveis. "
            "Mantenha-o em ambiente autorizado.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Ok:
            return
        selected_path, _ = QFileDialog.getSaveFileName(
            self,
            "Salvar HTML restaurado",
            str(suggested_html_output_path(self._inspection.path)),
            "Arquivos HTML (*.html)",
        )
        if not selected_path:
            return
        destination = Path(selected_path)
        if destination.suffix.lower() not in {".html", ".htm"}:
            destination = destination.with_suffix(".html")
        if paths_refer_to_same_file(self._inspection.path, destination):
            self._set_status(
                "O arquivo restaurado não pode substituir o HTML de entrada.", True
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
        worker.completed.connect(self._restoration_completed)
        self._start_worker(worker, "Gerando HTML restaurado...")

    def _start_worker(
        self,
        worker: HTMLAnalysisWorker | HTMLRestorationWorker,
        message: str,
    ) -> None:
        self._reset_progress()
        self._worker = worker
        self._set_processing_state(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.progress_label.setText("0 ocorrências processadas")
        self.progress_label.setVisible(True)
        self._set_status(message, False)
        worker.progress.connect(self._progress_changed)
        worker.cancelled.connect(self._cancelled)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._worker_finished)
        worker.start()

    def _progress_changed(self, progress: HTMLRestorationProgress) -> None:
        percentage = (
            min(100, int(progress.processed_bytes * 100 / progress.total_bytes))
            if progress.total_bytes
            else 0
        )
        self.progress_bar.setValue(percentage)
        self.progress_label.setText(
            f"{progress.occurrences_processed} ocorrências; "
            f"{progress.restored_occurrences} restauradas; "
            f"{progress.missing_occurrences} ausentes"
        )

    def _restoration_completed(self, result: HTMLRestorationResult) -> None:
        self._last_output_path = result.output_path
        missing_policy = (
            "Manter código original"
            if result.missing_code_policy is HTMLMissingCodePolicy.KEEP
            else "Interromper restauração"
        )
        representation = (
            "Primeira representação original"
            if result.representation_policy is RepresentationPolicy.FIRST_ORIGINAL
            else "Valor canônico normalizado"
        )
        self.summary.setPlainText(
            f"Arquivo gerado: {result.output_path}\n"
            f"Codificação preservada: {result.encoding}\n"
            f"Ocorrências processadas: {result.total_occurrences}\n"
            f"Ocorrências restauradas: {result.restored_occurrences}\n"
            f"Códigos ausentes preservados: {result.missing_occurrences}\n"
            f"Tempo aproximado: {result.duration_seconds:.2f} s\n"
            f"Política de ausentes: {missing_policy}\n"
            f"Representação: {representation}"
        )
        self.open_folder_button.setVisible(True)
        self._set_status("HTML restaurado gerado com sucesso.", False)

    def _cancelled(self) -> None:
        self._reset_progress()
        self._set_status("A operação foi cancelada com segurança.", False)

    def _failed(self, error: Exception) -> None:
        self._reset_progress()
        self._last_error = error
        if isinstance(error, HTMLRestorationSecurityError):
            message = "Não foi possível recuperar os mapeamentos com segurança."
        elif isinstance(error, HTMLRestorationError):
            message = str(error)
        else:
            message = "Não foi possível concluir a restauração com segurança."
        self._set_status(message, True)

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
            self._set_status("Cancelamento solicitado...", False)

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

    def _set_file_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.analyze_button,
            self.generate_button,
            self.missing_policy_combo,
            self.representation_combo,
        ):
            widget.setEnabled(enabled)

    def _reset_progress(self) -> None:
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
            self._set_status("Não foi possível abrir a pasta do resultado.", True)

    def _set_status(self, message: str, is_error: bool) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color: {'#b42318' if is_error else '#276749'};"
        )

from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.backup import (
    BackupError,
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
from data_mask_studio.gui.components import EmptyStateTable
from data_mask_studio.integrity import AuditReport
from data_mask_studio.maintenance import (
    STATUS_LABELS,
    CleanupResult,
    CompactionResult,
    DiagnosticResult,
    MaintenanceDiagnostics,
    MaintenanceError,
    TemporaryItem,
    VaultCompactor,
    safe_diagnostic_report,
)
from data_mask_studio.security import KeyProvider


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
    ) -> None:
        super().__init__()
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
        operation_row = QHBoxLayout()
        operation_row.addWidget(self.progress, stretch=1)
        operation_row.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 20, 28, 20)
        layout.addWidget(self.sections, stretch=1)
        layout.addLayout(operation_row)
        layout.addWidget(self.status_label)

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
        self.overview_output = QTextEdit()
        self.overview_output.setReadOnly(True)
        self.overview_output.setPlaceholderText(
            "Somente estatísticas agregadas serão exibidas aqui."
        )
        layout = QVBoxLayout(widget)
        layout.addLayout(actions)
        layout.addWidget(self.last_audit_label)
        layout.addWidget(self.overview_output)
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
        self.backup_result = QTextEdit()
        self.backup_result.setReadOnly(True)
        self.backup_result.setPlaceholderText(
            "A validação não restaura nem modifica arquivos locais."
        )
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Arquivo de backup:"))
        layout.addLayout(file_row)
        layout.addWidget(QLabel("Senha:"))
        layout.addWidget(self.backup_password_field)
        layout.addWidget(self.show_backup_password)
        layout.addWidget(self.validate_backup_button)
        layout.addWidget(self.backup_result)
        layout.addStretch()
        return widget

    def _build_cleanup(self) -> QWidget:
        widget = QWidget()
        self.locate_button = QPushButton("Localizar temporários")
        self.locate_button.clicked.connect(self.start_temporary_scan)
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
        layout.addWidget(
            QLabel(
                "A busca é limitada às pastas controladas da aplicação e da sessão."
            )
        )
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
        self.compact_button = QPushButton("Compactar cofre")
        self.compact_button.clicked.connect(self.start_compaction)
        self.compaction_result = QTextEdit()
        self.compaction_result.setReadOnly(True)
        layout = QVBoxLayout(widget)
        layout.addWidget(self.compaction_info)
        layout.addWidget(self.compact_button)
        layout.addWidget(self.compaction_result)
        layout.addStretch()
        return widget

    def start_diagnostic(self) -> None:
        if not self._begin_allowed():
            return
        worker = DiagnosticWorker(self._diagnostics)
        worker.progress.connect(self._diagnostic_progress)
        worker.completed.connect(self._diagnostic_completed)
        self._start_worker(worker, "Atualizando diagnóstico...")

    def _diagnostic_progress(self, completed: int, total: int) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(completed)

    def _diagnostic_completed(self, result: DiagnosticResult) -> None:
        self._last_diagnostic = result
        self.set_last_audit(result.audit)
        self.overview_output.setPlainText(_render_diagnostic(result))
        self.copy_report_button.setEnabled(True)
        self._set_status(
            f"Diagnóstico concluído: {STATUS_LABELS[result.status]}.",
            result.status.value == "failure",
        )

    def copy_report(self) -> None:
        if self._last_diagnostic is not None:
            QApplication.clipboard().setText(
                safe_diagnostic_report(self._last_diagnostic)
            )

    def set_last_audit(self, report: AuditReport) -> None:
        self._last_audit = report
        timestamp = report.finished_at.astimezone().strftime("%d/%m/%Y %H:%M:%S")
        self.last_audit_label.setText(
            f"Última auditoria nesta sessão: {timestamp} — {report.status.value}"
        )

    def choose_backup(self) -> None:
        selected, _ = QFileDialog.getOpenFileName(
            self, "Selecionar backup", "", "Backup (*.dmsbackup)"
        )
        if selected:
            self.backup_path_field.setText(selected)
            self.backup_result.clear()

    def start_backup_validation(self) -> None:
        if not self.backup_path_field.text() or not self._begin_allowed():
            self._set_status("Selecione um backup e informe sua senha.", True)
            return
        worker = BackupValidationWorker(
            self.backup_path_field.text(), self.backup_password_field.text()
        )
        worker.completed.connect(self._backup_completed)
        self._start_worker(worker, "Validando backup sem restaurá-lo...")

    def _backup_completed(self, result: BackupValidationResult) -> None:
        self.backup_result.setPlainText(_render_backup(result))
        self._set_status("Backup válido.", False)

    def start_temporary_scan(self) -> None:
        if not self._begin_allowed():
            return
        worker = TemporaryScanWorker(
            self._paths.directory, tuple(self._session_directories())
        )
        worker.completed.connect(self._temporaries_completed)
        self._start_worker(worker, "Localizando temporários conhecidos...")

    def _temporaries_completed(self, items: list[TemporaryItem]) -> None:
        self.temporary_items = items
        self._refresh_temporary_table()
        self._set_status(f"{len(items)} temporário(s) conhecido(s) localizado(s).", False)

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
        if not selected or not self._begin_allowed():
            return
        answer = QMessageBox.warning(
            self,
            "Confirmar limpeza",
            f"Excluir {len(selected)} temporário(s) antigo(s) selecionado(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        worker = TemporaryCleanupWorker(
            self.temporary_items,
            self._paths.directory,
            tuple(self._session_directories()),
        )
        worker.completed.connect(self._cleanup_completed)
        self._start_worker(worker, "Removendo temporários selecionados...")

    def _cleanup_completed(self, result: CleanupResult) -> None:
        self._refresh_temporary_table()
        self._set_status(
            f"Limpeza concluída: {result.removed} removido(s), "
            f"{result.preserved} preservado(s), {result.failed} falha(s).",
            result.failed > 0,
        )

    def start_compaction(self) -> None:
        if not self._paths.vault_database_path.is_file():
            self._set_status("O cofre local ainda não existe.", True)
            return
        if not self._begin_allowed():
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
        worker = CompactionWorker(self._compactor)
        worker.phase_changed.connect(self._compaction_phase)
        worker.completed.connect(self._compaction_completed)
        self._start_worker(worker, "Preparando compactação segura...")

    def _compaction_phase(self, stage: str, cancellation_allowed: bool) -> None:
        self.progress.setRange(0, 0)
        self.cancel_button.setEnabled(cancellation_allowed)
        self._set_status(stage, False)

    def _compaction_completed(self, result: CompactionResult) -> None:
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
        self._set_status("Compactação concluída com segurança.", False)

    def _begin_allowed(self) -> bool:
        if self._worker is not None:
            return False
        if not self._prepare_operation():
            self._set_status("Finalize as outras operações antes da manutenção.", True)
            return False
        return True

    def _start_worker(self, worker: QThread, message: str) -> None:
        self._worker = worker
        if hasattr(worker, "cancelled"):
            worker.cancelled.connect(self._cancelled)
        if hasattr(worker, "failed"):
            worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self._set_busy(True)
        self.progress.setRange(0, 0)
        self._set_status(message, False)
        worker.start()

    def cancel(self) -> None:
        worker = self._worker
        if worker is not None and hasattr(worker, "request_cancel"):
            worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self._set_status("Cancelamento solicitado...", False)

    def _cancelled(self) -> None:
        self._set_status("Operação cancelada com segurança.", False)

    def _failed(self, error: Exception) -> None:
        message = (
            str(error)
            if isinstance(error, (MaintenanceError, BackupError))
            else "A operação de manutenção falhou com segurança."
        )
        if isinstance(self._worker, BackupValidationWorker):
            self.backup_result.setPlainText("Arquivo inválido ou senha incorreta.")
        self._set_status(message, True)

    def _finished(self) -> None:
        worker = self._worker
        self._worker = None
        self.backup_password_field.clear()
        self.show_backup_password.setChecked(False)
        if worker is not None:
            worker.deleteLater()
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self.sections.setEnabled(not busy)
        self.cancel_button.setVisible(busy)
        self.cancel_button.setEnabled(busy)
        if not busy:
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
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
            status = item.result or (
                "Recente — preservado"
                if item.recent
                else "Possivelmente em uso"
                if item.in_use
                else "Disponível para seleção"
            )
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

    def _set_status(self, message: str, error: bool) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color: {'#b42318' if error else '#276749'};"
        )


def _render_diagnostic(result: DiagnosticResult) -> str:
    stats = result.statistics
    distribution = ", ".join(
        f"{rule}: {count}" for rule, count in stats.normalization_distribution
    ) or "nenhuma"
    return "\n".join(
        (
            f"Estado geral: {STATUS_LABELS[result.status]}",
            f"Chave HMAC: {_check_status(result, 'Disponibilidade da chave HMAC')}",
            f"Chave AES: {_check_status(result, 'Disponibilidade da chave AES')}",
            f"Versão do esquema: {stats.schema_version if stats.schema_version is not None else 'indisponível'}",
            f"Tamanho do vault.db: {_format_bytes(stats.vault_size)}",
            f"Mapeamentos: {stats.mapping_count}",
            f"Variações: {stats.variation_count}",
            f"Total de ocorrências: {stats.total_occurrences}",
            f"Perfis: {stats.profile_count}",
            f"Prefixos: {stats.prefix_count}",
            f"Distribuição por normalização: {distribution}",
            f"Primeira entrada: {stats.first_entry or 'indisponível'}",
            f"Última entrada: {stats.last_entry or 'indisponível'}",
            f"WAL: {'sim' if stats.wal_present else 'não'}; "
            f"SHM: {'sim' if stats.shm_present else 'não'}; "
            f"journal: {'sim' if stats.journal_present else 'não'}",
            f"Espaço do ambiente local: {_format_bytes(stats.environment_size)}",
            f"Espaço livre na unidade: {_format_bytes(stats.free_space)}",
        )
    )


def _check_status(result: DiagnosticResult, name: str) -> str:
    check = next(item for item in result.audit.checks if item.check_type == name)
    return check.status.value


def _render_backup(result: BackupValidationResult) -> str:
    return "\n".join(
        (
            "Arquivo válido: sim",
            f"Data de criação: {result.created_at.astimezone().isoformat(timespec='seconds')}",
            f"Versão da aplicação: {result.application_version}",
            f"Versão do formato: {result.format_version}",
            f"Versão do esquema: {result.vault_schema_version}",
            f"Mapeamentos: {result.mapping_count}",
            f"Perfis: {result.profile_count}",
            f"Compatível: {'sim' if result.is_compatible else 'não'}",
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

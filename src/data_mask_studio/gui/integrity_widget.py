from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from data_mask_studio.backup import EnvironmentPaths
from data_mask_studio.gui.integrity_worker import IntegrityWorker
from data_mask_studio.integrity import AuditReport, IntegrityAuditor, IntegrityStatus
from data_mask_studio.security import KeyProvider

_STATUS_LABELS = {
    IntegrityStatus.INTACT: "ÍNTEGRO",
    IntegrityStatus.ATTENTION: "ATENÇÃO",
    IntegrityStatus.FAILURE: "FALHA",
}


class IntegrityWidget(QWidget):
    busy_changed = Signal(bool)
    audit_completed = Signal(object)

    def __init__(
        self,
        paths: EnvironmentPaths,
        hmac_key_provider: KeyProvider,
        vault_key_provider: KeyProvider,
        prepare_audit: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        self._auditor = IntegrityAuditor(paths, hmac_key_provider, vault_key_provider)
        self._prepare_audit = prepare_audit or (lambda: True)
        self._worker: IntegrityWorker | None = None
        self._last_report: AuditReport | None = None

        title = QLabel("Auditoria de integridade")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        description = QLabel(
            "Verifica as chaves locais, o cofre criptografado e os perfis sem "
            "alterar ou reparar os dados."
        )
        description.setWordWrap(True)

        self.run_button = QPushButton("Executar verificação")
        self.run_button.clicked.connect(self.start_audit)
        self.cancel_button = QPushButton("Cancelar")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setVisible(False)
        self.copy_button = QPushButton("Copiar relatório seguro")
        self.copy_button.clicked.connect(self.copy_report)
        self.copy_button.setEnabled(False)
        actions = QHBoxLayout()
        actions.addWidget(self.run_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.copy_button)
        actions.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 12)
        self.progress_bar.setValue(0)
        self.last_check_label = QLabel(
            "Última verificação nesta sessão: ainda não executada"
        )
        self.status_label = QLabel("Pronto para executar a auditoria.")
        self.status_label.setWordWrap(True)
        self.report_view = QPlainTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlaceholderText(
            "O resumo seguro da auditoria será exibido aqui."
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addLayout(actions)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.last_check_label)
        layout.addWidget(self.status_label)
        layout.addWidget(self.report_view, stretch=1)

    def start_audit(self) -> None:
        if self._worker is not None:
            return
        if not self._prepare_audit():
            self._set_status(
                "Finalize as operações em andamento antes de auditar.", True
            )
            return
        self.clear_report()
        worker = IntegrityWorker(self._auditor)
        self._worker = worker
        worker.progress.connect(self._update_progress)
        worker.completed.connect(self._completed)
        worker.cancelled.connect(self._cancelled)
        worker.failed.connect(self._failed)
        worker.finished.connect(self._finished)
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.cancel_button.setVisible(True)
        self.busy_changed.emit(True)
        self._set_status("Auditoria em andamento...", False)
        worker.start()

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.request_cancel()
            self.cancel_button.setEnabled(False)
            self._set_status("Cancelamento solicitado...", False)

    def _update_progress(self, completed: int, total: int) -> None:
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(completed)

    def _completed(self, report: AuditReport) -> None:
        self._last_report = report
        self.report_view.setPlainText(report.to_safe_text())
        self.copy_button.setEnabled(True)
        timestamp = report.finished_at.astimezone().strftime("%d/%m/%Y %H:%M:%S")
        self.last_check_label.setText(
            f"Última verificação nesta sessão: {timestamp}"
        )
        self._set_status(
            f"Auditoria concluída: {_STATUS_LABELS[report.status]}.",
            report.status is IntegrityStatus.FAILURE,
        )
        self.audit_completed.emit(report)

    def _cancelled(self) -> None:
        self._set_status("Auditoria cancelada. Nenhum dado foi alterado.", False)

    def _failed(self, _error: Exception) -> None:
        self._set_status(
            "Não foi possível concluir a auditoria com segurança.", True
        )

    def _finished(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self.run_button.setEnabled(True)
        self.cancel_button.setVisible(False)
        self.busy_changed.emit(False)

    def copy_report(self) -> None:
        if self._last_report is not None:
            QApplication.clipboard().setText(self._last_report.to_safe_text())

    def clear_report(self) -> None:
        self._last_report = None
        self.report_view.clear()
        self.copy_button.setEnabled(False)
        self.progress_bar.setRange(0, 12)
        self.progress_bar.setValue(0)

    def has_running_worker(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def stop_worker(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            if not self._worker.wait(5000):
                return False
        return True

    def _set_status(self, message: str, is_error: bool) -> None:
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color: {'#b42318' if is_error else '#276749'};"
        )

from threading import Event

from PySide6.QtCore import QThread, Signal

from data_mask_studio.integrity import IntegrityAuditor, IntegrityCancelled


class IntegrityWorker(QThread):
    progress = Signal(int, int)
    completed = Signal(object)
    cancelled = Signal()
    failed = Signal(object)

    def __init__(self, auditor: IntegrityAuditor) -> None:
        super().__init__()
        self._auditor = auditor
        self._cancellation = Event()

    def request_cancel(self) -> None:
        self._cancellation.set()

    def run(self) -> None:
        try:
            report = self._auditor.run(
                should_cancel=self._cancellation.is_set,
                progress_callback=self.progress.emit,
            )
        except IntegrityCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(report)

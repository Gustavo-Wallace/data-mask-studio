from threading import Event

from PySide6.QtCore import QThread, Signal

from data_mask_studio.html_restoration import (
    HTMLInspectionResult,
    HTMLMissingCodePolicy,
    HTMLRestorationCancelled,
    HTMLRestorationService,
)
from data_mask_studio.restoration import RepresentationPolicy


class _HTMLWorker(QThread):
    progress = Signal(object)
    cancelled = Signal()
    failed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._cancel_requested = Event()

    def request_cancel(self) -> None:
        self._cancel_requested.set()


class HTMLAnalysisWorker(_HTMLWorker):
    completed = Signal(object)

    def __init__(
        self,
        service: HTMLRestorationService,
        inspection: HTMLInspectionResult,
    ) -> None:
        super().__init__()
        self._service = service
        self._inspection = inspection

    def run(self) -> None:
        try:
            result = self._service.analyze(
                self._inspection,
                progress_callback=self.progress.emit,
                should_cancel=self._cancel_requested.is_set,
            )
        except HTMLRestorationCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)


class HTMLRestorationWorker(_HTMLWorker):
    completed = Signal(object)

    def __init__(
        self,
        service: HTMLRestorationService,
        inspection: HTMLInspectionResult,
        destination: str,
        missing_code_policy: HTMLMissingCodePolicy,
        representation_policy: RepresentationPolicy,
        *,
        overwrite: bool,
    ) -> None:
        super().__init__()
        self._service = service
        self._inspection = inspection
        self._destination = destination
        self._missing_code_policy = missing_code_policy
        self._representation_policy = representation_policy
        self._overwrite = overwrite

    def run(self) -> None:
        try:
            result = self._service.restore(
                self._inspection,
                self._destination,
                missing_code_policy=self._missing_code_policy,
                representation_policy=self._representation_policy,
                overwrite=self._overwrite,
                progress_callback=self.progress.emit,
                should_cancel=self._cancel_requested.is_set,
            )
        except HTMLRestorationCancelled:
            self.cancelled.emit()
        except Exception as error:
            self.failed.emit(error)
        else:
            self.completed.emit(result)
